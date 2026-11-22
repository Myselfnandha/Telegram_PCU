import os
import time
import uuid
import asyncio
import logging
from enum import Enum
from typing import Optional, Dict, List, Any, Tuple, Set, Union, AsyncGenerator
from dataclasses import dataclass, field

from telethon import TelegramClient, utils, errors
from telethon.tl.functions.upload import GetFileRequest
from telethon.tl.types import InputFileLocation, TypeInputFileLocation

from app.db.session import get_sessionmaker
from app.db.repositories.telegram_repo import TelegramAccountRepository
from app.telegram.client_manager import multi_tenant_manager
from app.auth_v3.session_service import Principal

logger = logging.getLogger("turbo_pool")

MAX_ACCOUNTS_PER_USER = 3  # Hard ceiling: 1 primary + max 2 secondary
DEFAULT_CHUNK_SIZE = 512 * 1024  # 512 KB per MTProto chunk
EWMA_ALPHA = 0.3  # Smoothing factor for EWMA latency tracking


class TurboMode(str, Enum):
    OFF = "off"
    BALANCED = "balanced"
    ADAPTIVE = "adaptive"
    MAXIMUM = "maximum"


@dataclass
class AccountWorker:
    account_id: uuid.UUID
    telegram_user_id: int
    role: str  # "primary" or "secondary"
    client: TelegramClient
    is_eligible: bool = False
    file_location: Optional[TypeInputFileLocation] = None
    media_dc_id: Optional[int] = None
    floodwait_until: float = 0.0
    ewma_latency_ms: float = 100.0
    ewma_throughput_mbs: float = 5.0
    active_in_flight: int = 0
    total_bytes_fetched: int = 0
    consecutive_errors: int = 0
    last_error_reason: Optional[str] = None

    @property
    def is_cooling_down(self) -> bool:
        return time.time() < self.floodwait_until

    def record_chunk_metric(self, bytes_count: int, duration_s: float):
        latency_ms = duration_s * 1000.0
        # Update EWMA latency
        self.ewma_latency_ms = (EWMA_ALPHA * latency_ms) + ((1.0 - EWMA_ALPHA) * self.ewma_latency_ms)
        if duration_s > 0:
            mb_s = (bytes_count / (1024.0 * 1024.0)) / duration_s
            self.ewma_throughput_mbs = (EWMA_ALPHA * mb_s) + ((1.0 - EWMA_ALPHA) * self.ewma_throughput_mbs)
        self.total_bytes_fetched += bytes_count
        self.consecutive_errors = 0

    def record_error(self, err: Exception):
        self.consecutive_errors += 1
        self.last_error_reason = str(err)
        # Penalize EWMA latency on error
        self.ewma_latency_ms = max(self.ewma_latency_ms * 1.5, 500.0)
        if isinstance(err, errors.FloodWaitError):
            wait_secs = getattr(err, "seconds", 30)
            self.floodwait_until = time.time() + wait_secs
            logger.warning(
                f"Worker {self.account_id} hit FloodWait for {wait_secs}s. Placing in cooldown."
            )


class UserTurboPool:
    """
    Per-user isolated Multi-Turbo worker pool.
    Contains at most 3 accounts (1 primary + up to 2 secondaries).
    Ensures zero cross-user pooling.
    """
    def __init__(self, user_id: uuid.UUID):
        self.user_id = user_id
        self.mode: TurboMode = TurboMode.ADAPTIVE
        self.workers: Dict[uuid.UUID, AccountWorker] = {}
        self._lock = asyncio.Lock()
        self.last_accessed_at: float = time.time()

    def set_mode(self, mode: Union[TurboMode, str]):
        if isinstance(mode, str):
            mode = TurboMode(mode.lower())
        self.mode = mode
        logger.info(f"User {self.user_id} Turbo pool mode set to {self.mode.value}")

    async def sync_accounts(self) -> int:
        """
        Synchronizes workers with active accounts in database (up to 3 max).
        """
        session_maker = get_sessionmaker()
        async with session_maker() as db:
            tg_repo = TelegramAccountRepository(db)
            accounts = await tg_repo.list_user_accounts(self.user_id)

        # Enforce max 3 accounts
        active_accs = [a for a in accounts if a.status == "linked"][:MAX_ACCOUNTS_PER_USER]
        async with self._lock:
            # Remove workers no longer present or unlinked
            active_ids = {a.id for a in active_accs}
            for aid in list(self.workers.keys()):
                if aid not in active_ids:
                    self.workers.pop(aid, None)

            # Add or update workers
            for acc in active_accs:
                if acc.id not in self.workers:
                    principal = Principal(
                        user_id=self.user_id,
                        session_id=uuid.uuid4(),
                        device_id=None,
                        role="user",
                        display_name=""
                    )
                    try:
                        client = await multi_tenant_manager.get_client(principal, account_id=acc.id)
                        self.workers[acc.id] = AccountWorker(
                            account_id=acc.id,
                            telegram_user_id=acc.telegram_user_id,
                            role=acc.role,
                            client=client
                        )
                    except Exception as e:
                        logger.warning(f"Failed to initialize worker client for account {acc.id}: {e}")

            self.last_accessed_at = time.time()
            return len(self.workers)

    async def check_media_eligibility(
        self,
        chat_id: Union[str, int],
        message_id: int
    ) -> List[AccountWorker]:
        """
        Verifies which accounts have access to the specified message.
        CRITICAL: Each account independently resolves its own InputPeer and InputFileLocation
        with its own account-bound file_reference.
        """
        await self.sync_accounts()
        eligible_workers: List[AccountWorker] = []

        async with self._lock:
            all_workers = list(self.workers.values())

        for worker in all_workers:
            if worker.is_cooling_down:
                logger.debug(f"Worker {worker.account_id} is cooling down from FloodWait; skipping")
                worker.is_eligible = False
                continue

            try:
                # Resolve message on worker's own Telegram connection
                clean_cid: Union[str, int] = chat_id
                try:
                    clean_cid = int(chat_id)
                except ValueError:
                    pass

                msg = await worker.client.get_messages(clean_cid, ids=int(message_id))
                if not msg or not msg.media:
                    # Try alternative channel format (-100 prefix)
                    if isinstance(clean_cid, int) and clean_cid > 0:
                        alt_cid = int(f"-100{clean_cid}")
                        msg = await worker.client.get_messages(alt_cid, ids=int(message_id))

                if msg and getattr(msg, "media", None):
                    try:
                        file_info = utils._get_file_info(msg.media)
                    except Exception:
                        file_info = None
                    file_loc = utils.get_input_location(msg.media)
                    if file_loc:
                        worker.file_location = file_loc
                        worker.media_dc_id = getattr(file_info, "dc_id", None)
                        worker.is_eligible = True
                        eligible_workers.append(worker)
                        logger.debug(
                            f"Worker {worker.account_id} (role={worker.role}) verified eligible for msg {message_id}"
                        )
                        continue

                worker.is_eligible = False
                logger.info(
                    f"Worker {worker.account_id} could not find media for chat {chat_id} msg {message_id}"
                )
            except (errors.ChatAdminRequiredError, errors.ChannelPrivateError, errors.UserNotParticipantError) as e:
                logger.info(f"Worker {worker.account_id} lacks permission for chat {chat_id}: {e}")
                worker.is_eligible = False
            except errors.FloodWaitError as e:
                worker.record_error(e)
                worker.is_eligible = False
            except Exception as e:
                logger.warning(f"Error checking eligibility for worker {worker.account_id}: {e}")
                worker.record_error(e)
                worker.is_eligible = False

        # In OFF mode, retain only the primary worker
        if self.mode == TurboMode.OFF:
            eligible_workers = [w for w in eligible_workers if w.role == "primary"]
            if not eligible_workers and all_workers:
                eligible_workers = [all_workers[0]]

        return eligible_workers

    def select_best_worker(self, candidates: List[AccountWorker]) -> AccountWorker:
        """
        Selects the best worker based on active mode, EWMA latency, and in-flight concurrency.
        """
        available = [w for w in candidates if not w.is_cooling_down]
        if not available:
            # Fallback to candidates with lowest cooldown
            return min(candidates, key=lambda w: w.floodwait_until)

        if self.mode == TurboMode.OFF:
            primary = next((w for w in available if w.role == "primary"), None)
            return primary if primary else available[0]

        if self.mode == TurboMode.BALANCED:
            # Prefer worker with fewest active in-flight requests (load balancing)
            return min(available, key=lambda w: w.active_in_flight)

        if self.mode == TurboMode.ADAPTIVE:
            # Weighted selection: composite score = latency * (1 + active_in_flight)
            # Lower score is better
            return min(available, key=lambda w: w.ewma_latency_ms * (1 + (w.active_in_flight * 0.5)))

        # MAXIMUM mode: pick worker with lowest in-flight
        return min(available, key=lambda w: w.active_in_flight)

    async def fetch_chunk(
        self,
        worker: AccountWorker,
        offset: int,
        limit: int,
        retries: int = 2
    ) -> bytes:
        """
        Fetches a single chunk from Telegram using the worker's own connection and file_location.
        """
        if not worker.file_location:
            raise ValueError(f"Worker {worker.account_id} has no resolved file_location")

        worker.active_in_flight += 1
        start_time = time.time()
        try:
            for attempt in range(retries + 1):
                try:
                    req = GetFileRequest(
                        location=worker.file_location,
                        offset=offset,
                        limit=limit
                    )
                    res = await worker.client(req)
                    chunk_bytes = res.bytes if hasattr(res, "bytes") else b""
                    duration = time.time() - start_time
                    worker.record_chunk_metric(len(chunk_bytes), duration)
                    return chunk_bytes
                except errors.FloodWaitError as e:
                    worker.record_error(e)
                    raise
                except errors.FileReferenceExpiredError:
                    # Mark ineligible so caller can re-evaluate
                    worker.is_eligible = False
                    raise
                except Exception as e:
                    worker.record_error(e)
                    if attempt >= retries:
                        raise
                    await asyncio.sleep(0.1 * (attempt + 1))
            return b""
        finally:
            worker.active_in_flight = max(0, worker.active_in_flight - 1)

    async def stream_byte_range(
        self,
        chat_id: Union[str, int],
        message_id: int,
        start_byte: int,
        end_byte: int,
        chunk_size: int = DEFAULT_CHUNK_SIZE
    ) -> AsyncGenerator[bytes, None]:
        """
        Asynchronously streams a byte range with Adaptive Multi-Turbo concurrency
        and deterministic chunk reassembly.
        """
        eligible = await self.check_media_eligibility(chat_id, message_id)
        if not eligible:
            raise RuntimeError(f"No eligible Telegram accounts available to stream chat {chat_id} msg {message_id}")

        total_bytes = end_byte - start_byte + 1
        num_chunks = (total_bytes + chunk_size - 1) // chunk_size

        # Determine pipeline window based on mode
        if self.mode == TurboMode.OFF:
            window_size = 1
        elif self.mode == TurboMode.BALANCED:
            window_size = min(len(eligible) * 2, 4)
        elif self.mode == TurboMode.ADAPTIVE:
            window_size = min(len(eligible) * 3, 6)
        else:  # MAXIMUM
            window_size = min(len(eligible) * 4, 8)

        # In-flight task queue
        in_flight: Dict[int, asyncio.Task] = {}
        next_chunk_to_schedule = 0
        next_chunk_to_yield = 0

        async def _download_task(chunk_idx: int) -> Tuple[int, bytes]:
            chunk_offset = start_byte + (chunk_idx * chunk_size)
            chunk_limit = min(chunk_size, end_byte - chunk_offset + 1)
            selected_worker = self.select_best_worker(eligible)

            try:
                data = await self.fetch_chunk(selected_worker, chunk_offset, chunk_limit)
                return chunk_idx, data
            except Exception as e:
                logger.warning(
                    f"Chunk {chunk_idx} failed on worker {selected_worker.account_id}: {e}. Retrying with fallback worker."
                )
                # Fallback to primary worker
                primary_worker = next((w for w in eligible if w.role == "primary"), None)
                if primary_worker and primary_worker != selected_worker:
                    data = await self.fetch_chunk(primary_worker, chunk_offset, chunk_limit)
                    return chunk_idx, data
                raise

        try:
            while next_chunk_to_yield < num_chunks:
                # Fill in-flight window
                while len(in_flight) < window_size and next_chunk_to_schedule < num_chunks:
                    idx = next_chunk_to_schedule
                    task = asyncio.create_task(_download_task(idx))
                    in_flight[idx] = task
                    next_chunk_to_schedule += 1

                # Wait for next chunk in strict sequence
                if next_chunk_to_yield in in_flight:
                    target_task = in_flight[next_chunk_to_yield]
                    c_idx, c_data = await target_task
                    del in_flight[next_chunk_to_yield]
                    next_chunk_to_yield += 1
                    if c_data:
                        yield c_data
                else:
                    await asyncio.sleep(0.01)

        finally:
            # Cancel any remaining in-flight tasks on early client disconnect / seek
            for t in in_flight.values():
                if not t.done():
                    t.cancel()


class TurboPoolManager:
    """
    Singleton managing tenant-isolated UserTurboPool instances.
    """
    _instance: Optional["TurboPoolManager"] = None

    def __init__(self):
        self._pools: Dict[uuid.UUID, UserTurboPool] = {}
        self._lock = asyncio.Lock()

    @classmethod
    def get_instance(cls) -> "TurboPoolManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def get_pool(self, user_id: uuid.UUID) -> UserTurboPool:
        async with self._lock:
            if user_id not in self._pools:
                self._pools[user_id] = UserTurboPool(user_id)
            pool = self._pools[user_id]
            pool.last_accessed_at = time.time()
            return pool

    async def get_pool_status(self, user_id: uuid.UUID) -> Dict[str, Any]:
        pool = await self.get_pool(user_id)
        await pool.sync_accounts()
        workers_info = []
        for w in pool.workers.values():
            workers_info.append({
                "account_id": str(w.account_id),
                "telegram_user_id": w.telegram_user_id,
                "role": w.role,
                "is_eligible": w.is_eligible,
                "is_cooling_down": w.is_cooling_down,
                "cooldown_remaining_s": max(0.0, round(w.floodwait_until - time.time(), 1)),
                "ewma_latency_ms": round(w.ewma_latency_ms, 1),
                "ewma_throughput_mbs": round(w.ewma_throughput_mbs, 2),
                "active_in_flight": w.active_in_flight,
                "total_bytes_fetched": w.total_bytes_fetched
            })

        return {
            "user_id": str(user_id),
            "mode": pool.mode.value,
            "total_workers": len(pool.workers),
            "workers": workers_info
        }

    async def set_user_mode(self, user_id: uuid.UUID, mode: Union[TurboMode, str]) -> str:
        pool = await self.get_pool(user_id)
        pool.set_mode(mode)
        return pool.mode.value


turbo_pool_manager = TurboPoolManager.get_instance()
