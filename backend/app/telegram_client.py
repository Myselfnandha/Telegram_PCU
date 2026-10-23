import sys
import time
import random
import asyncio
import logging
from typing import Optional, Dict, Any
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneNumberInvalidError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError
)
from app.config import (
    API_ID,
    API_HASH,
    BOT_TOKEN,
    SESSION_DIR,
    SESSION_FILE_PATH,
    SESSION_STRING,
    update_session_string_in_env
)

import telethon.client.downloads as td
from telethon import functions, errors
from telethon.network import MTProtoSender
from telethon.tl.alltlobjects import LAYER

logger = logging.getLogger("telegram_client")
logging.getLogger("telethon.network").setLevel(logging.ERROR)

# 1. Optimize Telethon MTProto chunk sizing to 1MB (Telegram API supported limit)
td.MAX_CHUNK_SIZE = 1024 * 1024

# 2. Patch _DirectDownloadIter to support custom MTProtoSender injection
_orig_direct_init = td._DirectDownloadIter._init

async def _custom_direct_init(self, *args, **kwargs):
    ret = await _orig_direct_init(self, *args, **kwargs)
    if hasattr(self, "_custom_sender") and self._custom_sender:
        self._sender = self._custom_sender
    return ret

td._DirectDownloadIter._init = _custom_direct_init

# 3. Patch _iter_download to avoid Telethon bug checking 'offset % limit == 0' against chunk counts
_orig_iter_download = td.DownloadMethods._iter_download

# 4. Patch Connection._recv_loop to safely handle GeneratorExit under Python 3.12-3.14
from telethon.network.connection.connection import Connection
from telethon.errors import InvalidChecksumError, InvalidBufferError

_orig_conn_recv_loop = Connection._recv_loop

async def _safe_conn_recv_loop(self):
    """
    Patched _recv_loop that cleanly catches GeneratorExit under Python 3.12-3.14
    preventing 'RuntimeError: coroutine ignored GeneratorExit' and unclosed tasks on cancellation/GC.
    """
    try:
        while self._connected:
            try:
                data = await self._recv()
            except asyncio.CancelledError:
                break
            except (IOError, asyncio.IncompleteReadError) as e:
                self._log.warning('Server closed the connection: %s', e)
                await self._recv_queue.put((None, e))
                await self.disconnect()
            except InvalidChecksumError as e:
                self._log.warning('Server response had invalid checksum: %s', e)
                await self._recv_queue.put((None, e))
            except InvalidBufferError as e:
                self._log.warning('Server response had invalid buffer: %s', e)
                await self._recv_queue.put((None, e))
            except Exception as e:
                self._log.exception('Unexpected exception in the receive loop')
                await self._recv_queue.put((None, e))
                await self.disconnect()
            else:
                await self._recv_queue.put((data, None))
    except GeneratorExit:
        # Awaiting inside GeneratorExit violates Python generator protocol and triggers RuntimeError.
        # Synchronously flag disconnected and close the writer.
        self._connected = False
        if self._writer:
            try:
                self._writer.close()
            except Exception:
                pass
        return
    finally:
        if self._connected:
            try:
                await self.disconnect()
            except (GeneratorExit, asyncio.CancelledError, Exception):
                pass

Connection._recv_loop = _safe_conn_recv_loop

from telethon import helpers

_orig_conn_disconnect = Connection.disconnect

async def _safe_conn_disconnect(self):
    """
    Safely disconnects without allowing _recv_task or _send_task to cancel/await itself,
    eliminating Python 3.12-3.14 'cannot reuse already awaited coroutine' errors on shutdown.
    """
    if not self._connected:
        return
    self._connected = False

    cur = asyncio.current_task()
    tasks_to_cancel = {}
    if self._send_task and self._send_task is not cur and not self._send_task.done():
        tasks_to_cancel["send_task"] = self._send_task
    if self._recv_task and self._recv_task is not cur and not self._recv_task.done():
        tasks_to_cancel["recv_task"] = self._recv_task

    if tasks_to_cancel:
        try:
            await helpers._cancel(self._log, **tasks_to_cancel)
        except Exception:
            pass

    if self._writer:
        try:
            self._writer.close()
        except Exception:
            pass
        if sys.version_info >= (3, 7):
            try:
                await asyncio.wait_for(self._writer.wait_closed(), timeout=5)
            except Exception:
                pass

Connection.disconnect = _safe_conn_disconnect

# 5. Retain strong references to Telethon background tasks until completion
# to eliminate Python 3.12-3.14 'Task was destroyed but it is pending!' on sender GC.
_ACTIVE_TELETHON_TASKS: set = set()

_orig_conn_connect = Connection.connect

async def _safe_conn_connect(self, *args, **kwargs):
    await _orig_conn_connect(self, *args, **kwargs)
    for attr in ("_send_task", "_recv_task"):
        task = getattr(self, attr, None)
        if task and isinstance(task, asyncio.Task):
            _ACTIVE_TELETHON_TASKS.add(task)
            task.add_done_callback(_ACTIVE_TELETHON_TASKS.discard)

Connection.connect = _safe_conn_connect

_orig_sender_connect = MTProtoSender.connect

async def _safe_sender_connect(self, *args, **kwargs):
    ret = await _orig_sender_connect(self, *args, **kwargs)
    for attr in ("_send_loop_handle", "_recv_loop_handle"):
        task = getattr(self, attr, None)
        if task and isinstance(task, asyncio.Task):
            _ACTIVE_TELETHON_TASKS.add(task)
            task.add_done_callback(_ACTIVE_TELETHON_TASKS.discard)
    return ret

MTProtoSender.connect = _safe_sender_connect



def _patched_iter_download(
    self, file, *, offset=0, stride=None, limit=None, chunk_size=None,
    request_size=td.MAX_CHUNK_SIZE, file_size=None, dc_id=None,
    msg_data=None, cdn_redirect=None
):
    info = td.utils._get_file_info(file)
    if info.dc_id is not None:
        dc_id = info.dc_id
    if file_size is None:
        file_size = info.size
    file_loc = info.location
    if chunk_size is None:
        chunk_size = request_size
    if limit is None and file_size is not None:
        limit = (file_size + chunk_size - 1) // chunk_size
    if stride is None:
        stride = chunk_size
    request_size -= request_size % td.MIN_CHUNK_SIZE
    if request_size < td.MIN_CHUNK_SIZE:
        request_size = td.MIN_CHUNK_SIZE
    elif request_size > td.MAX_CHUNK_SIZE:
        request_size = td.MAX_CHUNK_SIZE

    if chunk_size == request_size and offset % td.MIN_CHUNK_SIZE == 0 and stride % td.MIN_CHUNK_SIZE == 0:
        cls = td._DirectDownloadIter
    else:
        cls = td._GenericDownloadIter

    return cls(
        self, limit, file=file_loc, dc_id=dc_id, offset=offset,
        stride=stride, chunk_size=chunk_size, request_size=request_size,
        file_size=file_size, msg_data=msg_data, cdn_redirect=cdn_redirect
    )

td.DownloadMethods._iter_download = _patched_iter_download

class TelegramClientManager:
    _instance: Optional["TelegramClientManager"] = None
    _client: Optional[TelegramClient] = None
    _bot_client: Optional[TelegramClient] = None
    _lock = asyncio.Lock()
    _is_ready = False
    _cached_me: Optional[dict] = None

    @classmethod
    async def get_client(cls) -> TelegramClient:
        """
        Retrieves the singleton user Telethon client instance.
        If no user session is authorized but BOT_TOKEN is present, falls back to bot client.
        """
        if cls._client is None or not cls._client.is_connected():
            async with cls._lock:
                if cls._client is None:
                    if not API_ID or not API_HASH:
                        logger.warning("TG_API_ID or TG_API_HASH is not set. Telethon client cannot start.")
                        raise ValueError("TG_API_ID and TG_API_HASH must be configured in environment variables.")

                    logger.info("Initializing Telethon user client with StringSession...")
                    session_target = StringSession(SESSION_STRING) if SESSION_STRING else str(SESSION_FILE_PATH)
                    cls._client = TelegramClient(
                        session_target,
                        API_ID,
                        API_HASH,
                        flood_sleep_threshold=0,  # Never sleep silently inside Telethon; raise FloodWaitError so RateGovernor coordinates all streams cleanly without stalls
                        connection_retries=None,  # Infinite retries so network drops never permanently kill client
                        retry_delay=3,
                        auto_reconnect=True,
                        timeout=15  # 15s: must be below VLC's HTTP timeout (~10-15s) to fail fast
                    )

                was_ready = cls._is_ready
                if not cls._client.is_connected():
                    logger.info("Connecting Telethon user client to Telegram MTProto...")
                    try:
                        await cls._client.connect()
                    except Exception as conn_err:
                        logger.warning(f"MTProto connect error (network may be switching): {conn_err}")

                if cls._client.is_connected():
                    cls._is_ready = await cls._client.is_user_authorized()
                    if cls._is_ready:
                        # Sync and persist active string session if not already stored
                        if isinstance(cls._client.session, StringSession):
                            current_str = cls._client.session.save()
                            if current_str and current_str != SESSION_STRING:
                                update_session_string_in_env(current_str)

                        if not cls._cached_me:
                            try:
                                me = await cls._client.get_me()
                                full_name = f"{getattr(me, 'first_name', '') or ''} {getattr(me, 'last_name', '') or ''}".strip()
                                is_prem = getattr(me, "premium", False)
                                cls._cached_me = {
                                    "id": getattr(me, "id", None),
                                    "name": full_name or getattr(me, "username", "Telegram User"),
                                    "username": getattr(me, "username", None),
                                    "is_premium": is_prem
                                }
                                sender_pool.set_max_senders(12 if is_prem else 8)
                                from app.services.rate_governor import rate_governor
                                rate_governor.configure_for_tier(is_premium=is_prem)
                                logger.info(f"Telegram user client authorized as: {getattr(me, 'first_name', '')} (@{getattr(me, 'username', 'N/A')}, Premium: {is_prem})")
                                asyncio.create_task(cls._client.get_dialogs(limit=300))
                            except Exception as me_err:
                                logger.debug(f"get_me notice: {me_err}")
                    elif BOT_TOKEN:
                        logger.info("User session not authorized; attempting Bot login with BOT_TOKEN...")
                        try:
                            await cls._client.start(bot_token=BOT_TOKEN)
                            cls._is_ready = True
                            me = await cls._client.get_me()
                            cls._cached_me = {
                                "id": getattr(me, "id", None),
                                "name": getattr(me, "first_name", "Telegram Bot"),
                                "username": getattr(me, "username", None)
                            }
                            logger.info(f"Telegram client started as Bot: @{getattr(me, 'username', 'N/A')}")
                        except Exception as bot_err:
                            logger.warning(f"Failed bot login on user client: {bot_err}")
                    else:
                        cls._cached_me = None
                        logger.warning("Telethon client connected but NOT authorized yet. Run setup_auth.py to log in.")
                else:
                    # Client could not connect right now (e.g. WiFi switched, no route yet)
                    # CRITICAL: Preserve existing authorization so network changes don't log out user!
                    if was_ready:
                        cls._is_ready = True

        return cls._client

    @classmethod
    async def ensure_connected(cls, timeout: float = 15.0) -> bool:
        """
        Ensures the client is connected to MTProto.
        If a network switch or temporary disconnect occurs, aggressively reconnects with exponential backoff.
        Does NOT de-authorize the user while reconnecting.
        """
        if cls._client is not None and cls._client.is_connected():
            return True

        deadline = time.time() + timeout
        delay = 0.4
        while time.time() < deadline:
            try:
                client = await cls.get_client()
                if client and client.is_connected():
                    return True
            except Exception as e:
                logger.debug(f"ensure_connected retry notice: {e}")
            await asyncio.sleep(min(delay, max(0.1, deadline - time.time())))
            delay = min(2.0, delay * 1.5)

        return bool(cls._client and cls._client.is_connected())

    @classmethod
    async def get_bot_client(cls) -> Optional[TelegramClient]:
        """Returns dedicated bot client if BOT_TOKEN is set."""
        if not BOT_TOKEN or not API_ID or not API_HASH:
            return None

        if cls._bot_client is None or not cls._bot_client.is_connected():
            async with cls._lock:
                if cls._bot_client is None:
                    bot_session = SESSION_DIR / "bot_session"
                    cls._bot_client = TelegramClient(
                        str(bot_session),
                        API_ID,
                        API_HASH,
                        connection_retries=10,
                        retry_delay=2,
                        auto_reconnect=True
                    )
                if not cls._bot_client.is_connected():
                    await cls._bot_client.start(bot_token=BOT_TOKEN)
                    me = await cls._bot_client.get_me()
                    logger.info(f"Dedicated Bot client connected as @{getattr(me, 'username', '')}")

        return cls._bot_client

    @classmethod
    async def is_authorized(cls) -> bool:
        """Check if client is authorized (instant memory check first)."""
        try:
            if cls._is_ready:
                return True
            if cls._client is not None and cls._client.is_connected() and cls._cached_me:
                return True
            session_file = SESSION_FILE_PATH.with_suffix(".session") if not str(SESSION_FILE_PATH).endswith(".session") else SESSION_FILE_PATH
            if not SESSION_STRING and not session_file.exists() and not SESSION_FILE_PATH.exists():
                return False
            client = await cls.get_client()
            if client and client.is_connected():
                cls._is_ready = await client.is_user_authorized()
            return cls._is_ready
        except Exception as e:
            logger.debug(f"Error checking authorization status: {e}")
            return False

    @classmethod
    async def get_me_info(cls) -> Optional[dict]:
        """Fetch current authenticated Telegram user info (served from memory cache with phone privacy masking)."""
        if cls._cached_me and "phone" in cls._cached_me:
            return cls._cached_me
        try:
            client = await cls.get_client()
            if not await client.is_user_authorized():
                return None
            me = await client.get_me()
            full_name = f"{getattr(me, 'first_name', '') or ''} {getattr(me, 'last_name', '') or ''}".strip()
            raw_phone = getattr(me, "phone", None)
            masked_phone = None
            if raw_phone:
                raw_str = str(raw_phone)
                masked_phone = f"+{raw_str[:3]}******{raw_str[-4:]}" if len(raw_str) >= 7 else "***"

            cls._cached_me = {
                "id": me.id,
                "first_name": getattr(me, "first_name", ""),
                "last_name": getattr(me, "last_name", ""),
                "name": full_name or getattr(me, "username", "Telegram User"),
                "username": getattr(me, "username", None),
                "phone": masked_phone,
                "is_bot": getattr(me, "bot", False)
            }
            return cls._cached_me
        except Exception as e:
            logger.error(f"Error getting Telegram profile: {e}")
            return None

    @classmethod
    def get_cached_me(cls) -> Optional[dict]:
        """Returns in-memory cached profile of currently authorized Telegram user."""
        return cls._cached_me

    @classmethod
    async def send_login_code(cls, phone: str) -> dict:
        """Sends Telegram verification login code to the specified phone number."""
        client = await cls.get_client()
        sent_code = await client.send_code_request(phone)
        return {
            "phone": phone,
            "phone_code_hash": sent_code.phone_code_hash,
            "timeout": getattr(sent_code, "timeout", 60)
        }

    @classmethod
    async def sign_in_with_code(cls, phone: str, code: str, phone_code_hash: str) -> dict:
        """Completes sign-in with phone verification code. Handles 2FA if enabled."""
        client = await cls.get_client()
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
            if isinstance(client.session, StringSession):
                new_str = client.session.save()
                if new_str:
                    update_session_string_in_env(new_str)
            cls._cached_me = None
            me_info = await cls.get_me_info()
            cls._is_ready = True
            return {"status": "authorized", "user": me_info}
        except SessionPasswordNeededError:
            return {"status": "2fa_required", "message": "Two-Step Verification (2FA) is enabled on this Telegram account."}

    @classmethod
    async def sign_in_with_2fa(cls, password: str) -> dict:
        """Completes sign-in with 2FA password."""
        client = await cls.get_client()
        await client.sign_in(password=password)
        if isinstance(client.session, StringSession):
            new_str = client.session.save()
            if new_str:
                update_session_string_in_env(new_str)
        cls._cached_me = None
        me_info = await cls.get_me_info()
        cls._is_ready = True
        return {"status": "authorized", "user": me_info}

    @classmethod
    async def logout(cls) -> dict:
        """Logs out from Telegram and clears local session."""
        async with cls._lock:
            if cls._client and cls._client.is_connected():
                try:
                    await cls._client.log_out()
                except Exception as e:
                    logger.debug(f"Logout notice: {e}")
                await cls._client.disconnect()
            cls._client = None
            cls._cached_me = None
            cls._is_ready = False
            update_session_string_in_env("")
            for ext in (".session", ".session.bak", ".session-wal", ".session-shm"):
                sf = SESSION_FILE_PATH.with_suffix(ext)
                if sf.exists():
                    sf.unlink(missing_ok=True)
            return {"status": "logged_out"}

    @classmethod
    async def disconnect(cls):
        """Cleanly disconnect all clients on shutdown."""
        async with cls._lock:
            if cls._client is not None:
                logger.info("Disconnecting Telethon user client...")
                try:
                    if cls._client.is_connected():
                        await cls._client.disconnect()
                except Exception as e:
                    logger.error(f"Error during user client disconnect: {e}")
                finally:
                    cls._client = None
                    cls._is_ready = False

            if cls._bot_client is not None:
                logger.info("Disconnecting Telethon bot client...")
                try:
                    if cls._bot_client.is_connected():
                        await cls._bot_client.disconnect()
                except Exception as e:
                    logger.error(f"Error during bot client disconnect: {e}")
                finally:
                    cls._bot_client = None

        await sender_pool.disconnect_all()


from contextlib import asynccontextmanager


class MTProtoSenderPool:
    """
    High-Throughput Multi-Connection MTProto Sender Pool.
    Maintains multiple persistent TCP sockets to Telegram MTProto DCs.
    Allows parallel HTTP Range streams (e.g. Free Download Manager with 8-16 segments)
    to achieve 18-30+ MB/s aggregate throughput by eliminating single-socket serialization
    and preventing socket exhaustion / FloodWait.
    """
    def __init__(self, max_per_dc: int = 8):
        self.max_per_dc = max_per_dc
        self._semaphores: Dict[int, asyncio.Semaphore] = {}
        self._idle_senders: Dict[int, list] = {}
        self._active_senders: Dict[int, set] = {}
        self._auth_keys: Dict[int, Any] = {}
        self._last_active: Dict[Any, float] = {}
        self._keepalive_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._create_lock = asyncio.Lock()

    def get_semaphore(self, dc_id: int) -> asyncio.Semaphore:
        if dc_id not in self._semaphores:
            self._semaphores[dc_id] = asyncio.Semaphore(self.max_per_dc)
        return self._semaphores[dc_id]

    def set_max_senders(self, max_per_dc: int):
        self.max_per_dc = max_per_dc
        for dc_id in list(self._semaphores.keys()):
            self._semaphores[dc_id] = asyncio.Semaphore(max_per_dc)

    def _ensure_keepalive(self, client):
        """Starts background keepalive ping loop if not already running."""
        if self._keepalive_task is None or self._keepalive_task.done():
            try:
                self._keepalive_task = asyncio.create_task(self._keepalive_loop(client))
            except RuntimeError:
                pass

    async def _keepalive_loop(self, client):
        """
        Background keepalive loop: sends a lightweight 20-byte PingRequest
        every 25s to idle pooled senders. Prevents Telegram server from closing
        idle MTProto connections during continuous video playback.
        Ensures instantaneous (<300ms) seeks even after hours of movie playback.
        """
        while True:
            try:
                await asyncio.sleep(25.0)
                now = time.time()
                senders_to_ping = []
                async with self._lock:
                    for dc_id, idles in self._idle_senders.items():
                        for s in list(idles):
                            if (now - self._last_active.get(s, 0)) >= 20.0:
                                senders_to_ping.append((dc_id, s))

                for dc_id, s in senders_to_ping:
                    if not s.is_connected():
                        await self.release(client, dc_id, s, healthy=False)
                        continue
                    try:
                        await asyncio.wait_for(
                            client._call(s, functions.PingRequest(ping_id=random.randint(0, 0x7fffffff))),
                            timeout=2.0
                        )
                        self._last_active[s] = time.time()
                    except Exception:
                        logger.debug(f"MTProtoPool: Evicted stale idle sender on DC {dc_id}")
                        await self.release(client, dc_id, s, healthy=False)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"MTProtoPool keepalive notice: {e}")

    async def _create_sender(self, client, dc_id: int) -> MTProtoSender:
        current_dc = getattr(getattr(client, "session", None), "dc_id", None)
        if current_dc and current_dc == dc_id:
            dc = await client._get_dc(dc_id)
            s = MTProtoSender(client.session.auth_key, loggers=client._log)
            conn = client._connection(
                dc.ip_address, dc.port, dc.id,
                loggers=client._log, proxy=client._proxy, local_addr=client._local_addr
            )
            try:
                await asyncio.wait_for(s.connect(conn), timeout=15.0)
                self._last_active[s] = time.time()
                return s
            except Exception:
                try:
                    await s.disconnect()
                except Exception:
                    pass
                raise
        else:
            # Foreign DC: create an independent authenticated MTProtoSender via ExportAuthorization
            # Serialized via _create_lock to prevent race conditions on client._init_request.query and simultaneous ExportAuth requests
            async with self._create_lock:
                sender = None
                try:
                    dc = await client._get_dc(dc_id)
                    sender = MTProtoSender(None, loggers=client._log)
                    conn = client._connection(
                        dc.ip_address, dc.port, dc.id,
                        loggers=client._log, proxy=client._proxy, local_addr=client._local_addr
                    )
                    await asyncio.wait_for(sender.connect(conn), timeout=15.0)
                    auth = await asyncio.wait_for(
                        client(functions.auth.ExportAuthorizationRequest(dc_id)),
                        timeout=10.0
                    )
                    client._init_request.query = functions.auth.ImportAuthorizationRequest(id=auth.id, bytes=auth.bytes)
                    req = functions.InvokeWithLayerRequest(LAYER, client._init_request)
                    await asyncio.wait_for(sender.send(req), timeout=10.0)
                    sender.dc_id = dc_id
                    sender._is_independent = True
                    self._last_active[sender] = time.time()
                    return sender
                except Exception as e:
                    if sender:
                        try:
                            await sender.disconnect()
                        except Exception:
                            pass
                    logger.debug(f"Export authorization for DC {dc_id} delegated to Telethon borrower: {e}")
                    borrowed_sender = await asyncio.wait_for(client._borrow_exported_sender(dc_id), timeout=15.0)
                    self._last_active[borrowed_sender] = time.time()
                    return borrowed_sender

    async def acquire(self, client, dc_id: int) -> MTProtoSender:
        """Acquires a pooled or newly initialized MTProtoSender guarded by semaphore."""
        sem = self.get_semaphore(dc_id)
        await sem.acquire()
        sender = None
        self._ensure_keepalive(client)
        try:
            async with self._lock:
                if dc_id not in self._idle_senders:
                    self._idle_senders[dc_id] = []
                    self._active_senders[dc_id] = set()

                idle_list = self._idle_senders[dc_id]
                while idle_list:
                    candidate = idle_list.pop()
                    last_used = self._last_active.get(candidate, 0)
                    # If sender was idle for > 45s without activity, verify with fast ping before handing to seek
                    if (time.time() - last_used) > 45.0:
                        try:
                            await asyncio.wait_for(
                                client._call(candidate, functions.PingRequest(ping_id=random.randint(0, 0x7fffffff))),
                                timeout=1.0
                            )
                            self._last_active[candidate] = time.time()
                            sender = candidate
                            break
                        except Exception:
                            try:
                                await candidate.disconnect()
                            except Exception:
                                pass
                            self._last_active.pop(candidate, None)
                            continue
                    elif candidate.is_connected():
                        self._last_active[candidate] = time.time()
                        sender = candidate
                        break
                    try:
                        await candidate.disconnect()
                    except Exception:
                        pass
                    self._last_active.pop(candidate, None)

            # If Telethon has a disconnected borrowed sender for this DC, purge it to avoid zombie socket
            if hasattr(client, "_borrowed_senders") and isinstance(client._borrowed_senders, dict):
                borrowed_entry = client._borrowed_senders.get(dc_id)
                if borrowed_entry and len(borrowed_entry) > 1:
                    b_sender = borrowed_entry[1]
                    if b_sender and not b_sender.is_connected():
                        client._borrowed_senders.pop(dc_id, None)

            if sender is None:
                current_dc = getattr(getattr(client, "session", None), "dc_id", None)
                # Foreign DC fast-path: Check Telethon's borrowed exported sender first ONLY if not already actively leased
                if current_dc and current_dc != dc_id:
                    borrowed_entry = getattr(client, "_borrowed_senders", {}).get(dc_id)
                    if borrowed_entry and len(borrowed_entry) > 1:
                        b_sender = borrowed_entry[1]
                        async with self._lock:
                            is_in_use = b_sender in self._active_senders.get(dc_id, set())
                        if b_sender and b_sender.is_connected() and not is_in_use:
                            sender = b_sender
                            self._last_active[sender] = time.time()
                            logger.debug(f"MTProtoPool: Fast-reused Telethon exported sender for DC {dc_id} (0ms TTFB)")
                    if sender is None and len(self._active_senders.get(dc_id, set())) == 0:
                        try:
                            sender = await asyncio.wait_for(client._borrow_exported_sender(dc_id), timeout=10.0)
                            self._last_active[sender] = time.time()
                            logger.info(f"MTProtoPool: Borrowed exported MTProto sender for DC {dc_id}")
                        except Exception as fe:
                            logger.debug(f"Borrow fallback: {fe}")

            if sender is None:
                try:
                    sender = await self._create_sender(client, dc_id)
                    logger.info(f"MTProtoPool: Connected new parallel MTProto sender for DC {dc_id}")
                except Exception as ce:
                    err_msg = f"{type(ce).__name__}: {ce}" if str(ce) else type(ce).__name__
                    logger.debug(f"Parallel MTProto sender init for DC {dc_id} redirected to borrower: {err_msg}")
                    current_dc = getattr(getattr(client, "session", None), "dc_id", None)
                    if current_dc and current_dc == dc_id:
                        sender = getattr(client, "_sender", None)
                    else:
                        try:
                            sender = await asyncio.wait_for(client._borrow_exported_sender(dc_id), timeout=15.0)
                            self._last_active[sender] = time.time()
                            logger.info(f"MTProtoPool: Borrowed exported MTProto sender for DC {dc_id}")
                        except Exception as fe:
                            err_f = f"{type(fe).__name__}: {fe}" if str(fe) else type(fe).__name__
                            logger.warning(f"Fallback borrow sender for DC {dc_id} failed: {err_f}")
                            sender = None

            if sender is None:
                raise RuntimeError(f"Unable to establish MTProto connection for DC {dc_id}")

            if sender and sender != getattr(client, "_sender", None):
                async with self._lock:
                    self._active_senders[dc_id].add(sender)
                self._last_active[sender] = time.time()
                logger.debug(f"MTProtoPool: Leased MTProto sender for DC {dc_id} (active: {len(self._active_senders[dc_id])})")
            return sender
        except BaseException:
            try:
                sem.release()
            except ValueError:
                pass
            raise

    async def release(self, client, dc_id: int, sender: MTProtoSender, healthy: bool = True):
        """Returns sender to idle cache or disposes of broken socket, always releasing semaphore slot."""
        current_dc = getattr(getattr(client, "session", None), "dc_id", None)
        try:
            if sender and sender != getattr(client, "_sender", None):
                is_foreign = current_dc is not None and current_dc != dc_id
                borrowed = None
                if hasattr(client, "_borrowed_senders") and isinstance(client._borrowed_senders, dict):
                    borrowed_entry = client._borrowed_senders.get(dc_id)
                    if borrowed_entry and len(borrowed_entry) > 1:
                        borrowed = borrowed_entry[1]

                async with self._lock:
                    if dc_id in self._active_senders:
                        self._active_senders[dc_id].discard(sender)

                if not healthy:
                    # Broken / cancelled dirty sender: cleanly disconnect and purge immediately
                    try:
                        await sender.disconnect()
                    except Exception:
                        pass
                    self._last_active.pop(sender, None)
                    # ONLY clear Telethon's borrowed sender entry if THIS specific sender was Telethon's borrowed sender
                    if sender == borrowed and hasattr(client, "_borrowed_senders") and isinstance(client._borrowed_senders, dict):
                        if dc_id in client._borrowed_senders:
                            client._borrowed_senders.pop(dc_id, None)
                    async with self._lock:
                        if dc_id in self._idle_senders:
                            self._idle_senders[dc_id] = [s for s in self._idle_senders[dc_id] if s != sender]
                elif sender == borrowed or (is_foreign and getattr(sender, "_is_independent", None) is not True and hasattr(client, "_return_exported_sender")):
                    # Telethon's borrowed sender returned via Telethon's mechanism
                    self._last_active[sender] = time.time()
                    if hasattr(client, "_return_exported_sender"):
                        try:
                            await client._return_exported_sender(sender)
                        except Exception:
                            pass
                else:
                    # Independent pooled sender: return to idle pool for immediate reuse
                    async with self._lock:
                        if sender.is_connected():
                            self._last_active[sender] = time.time()
                            if dc_id not in self._idle_senders:
                                self._idle_senders[dc_id] = []
                            self._idle_senders[dc_id].append(sender)
                        else:
                            self._last_active.pop(sender, None)
                            try:
                                await sender.disconnect()
                            except Exception:
                                pass
        finally:
            sem = self.get_semaphore(dc_id)
            try:
                sem.release()
            except ValueError:
                pass

    @asynccontextmanager
    async def borrow(self, client, dc_id: int):
        """Context manager for acquiring and safely returning a pooled sender."""
        sender = await self.acquire(client, dc_id)
        healthy = True
        try:
            yield sender
        except (ConnectionResetError, ConnectionError, BrokenPipeError, OSError, asyncio.TimeoutError):
            healthy = False
            raise
        except errors.FloodWaitError:
            # FloodWait is server-enforced rate-limit, not a socket error; keep socket healthy
            healthy = True
            raise
        except asyncio.CancelledError:
            # Task cancelled while in borrow: socket may contain unread RPC bytes from server.
            # Quarantine and close this dirty sender so leftover bytes never corrupt subsequent seek requests!
            healthy = False
            raise
        except BaseException:
            if sender and not sender.is_connected():
                healthy = False
            raise
        finally:
            await asyncio.shield(self.release(client, dc_id, sender, healthy=healthy))

    async def purge_dc(self, dc_id: int):
        """Immediately disconnects and purges all pooled senders for a specific DC upon socket/network failure."""
        async with self._lock:
            idle = self._idle_senders.pop(dc_id, [])
            for s in idle:
                self._last_active.pop(s, None)
                try:
                    await s.disconnect()
                except Exception:
                    pass
            active = self._active_senders.get(dc_id, set()).copy()
            for s in active:
                self._last_active.pop(s, None)
                try:
                    await s.disconnect()
                except Exception:
                    pass
            self._active_senders.pop(dc_id, None)

    async def disconnect_all(self):
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
        async with self._lock:
            for dc_id, senders in self._idle_senders.items():
                for s in senders:
                    try:
                        await s.disconnect()
                    except Exception:
                        pass
            for dc_id, senders in self._active_senders.items():
                for s in senders:
                    try:
                        await s.disconnect()
                    except Exception:
                        pass
            self._idle_senders.clear()
            self._active_senders.clear()
            self._semaphores.clear()
            self._last_active.clear()


sender_pool = MTProtoSenderPool(max_per_dc=8)
