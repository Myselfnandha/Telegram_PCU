import asyncio
import time
import uuid
import logging
from typing import Optional, Union, AsyncGenerator, Any, Dict, Tuple, Set, List
from telethon import utils, errors
from telethon.tl.functions.upload import GetFileRequest

from app.telegram_client import sender_pool
from app.services.rate_governor import rate_governor
from app.services.stream_buffer import stream_buffer, StreamSlidingBuffer
from app.services.stream_cache import stream_cache_service

logger = logging.getLogger("stream_engine")

FILE_REF_ERRORS = (
    getattr(errors, "FileReferenceExpiredError", Exception),
    getattr(errors, "FileReferenceInvalidError", Exception),
    getattr(errors, "MediaEmptyError", Exception)
)


class SeekEpisode:
    def __init__(self, episode_id: str, chat_id: str, message_id: int):
        self.episode_id = episode_id
        self.chat_id = chat_id
        self.message_id = message_id
        self.candidate_windows: Set[int] = set()
        self.latest_target: int = 0
        self.created_at: float = time.time()
        self.last_activity: float = time.time()
        self.request_count: int = 0
        self.active_subscribers: Set[str] = set()

    def is_expired(self, ttl: float = 30.0) -> bool:
        return (time.time() - self.last_activity) > ttl

    def add_target(self, offset: int, window_size: int = 1024 * 1024):
        self.last_activity = time.time()
        self.request_count += 1
        self.latest_target = offset
        w_start = (offset // window_size) * window_size
        windows = [
            max(0, w_start - window_size),
            w_start,
            w_start + window_size,
            w_start + 2 * window_size,
            w_start + 3 * window_size
        ]
        for w in windows:
            self.candidate_windows.add(w)
        if len(self.candidate_windows) > 16:
            sorted_by_dist = sorted(
                list(self.candidate_windows),
                key=lambda w: abs(w - self.latest_target)
            )
            self.candidate_windows = set(sorted_by_dist[:16])
        stream_buffer.retain_chunks(self.chat_id, self.message_id, list(self.candidate_windows), ttl=30.0)


class SeekEpisodeManager:
    def __init__(self):
        self._episodes: Dict[Tuple[str, int], SeekEpisode] = {}

    def observe(self, chat_id: Union[str, int], message_id: int, offset: int, window_size: int = 1024 * 1024) -> SeekEpisode:
        norm_cid = StreamSlidingBuffer._canonical_cid(chat_id)
        key = (norm_cid, int(message_id))
        ep = self._episodes.get(key)
        if ep is None or ep.is_expired(ttl=30.0):
            ep_id = f"ep_{uuid.uuid4().hex[:8]}"
            ep = SeekEpisode(ep_id, norm_cid, int(message_id))
            self._episodes[key] = ep
        ep.add_target(offset, window_size)
        return ep

    def get_episode(self, chat_id: Union[str, int], message_id: int) -> Optional[SeekEpisode]:
        norm_cid = StreamSlidingBuffer._canonical_cid(chat_id)
        return self._episodes.get((norm_cid, int(message_id)))

    def is_candidate_window(self, chat_id: Union[str, int], message_id: int, offset: int, window_size: int = 1024 * 1024) -> bool:
        norm_cid = StreamSlidingBuffer._canonical_cid(chat_id)
        ep = self._episodes.get((norm_cid, int(message_id)))
        if not ep or ep.is_expired(ttl=30.0):
            return False
        w_start = (offset // window_size) * window_size
        return w_start in ep.candidate_windows


class ChunkTask:
    def __init__(self, key: Tuple[str, int, int]):
        self.key = key
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()
        self.subscribers: Set[str] = set()
        self.priority: int = 4
        self.created_at: float = time.time()
        self.wire_task: Optional[asyncio.Task] = None
        self.completed: bool = False


class StreamEngine:
    def __init__(self):
        self._in_flight_chunks: Dict[Tuple[str, int, int], asyncio.Future] = {}
        self._chunk_task_registry: Dict[Tuple[str, int, int], ChunkTask] = {}
        self.seek_episode_manager = SeekEpisodeManager()
        self._chunk_lock: Optional[asyncio.Lock] = None
        self._mtproto_sem: Optional[asyncio.Semaphore] = None
        self._active_stream_wire_tasks: Dict[Tuple[str, int], Set[asyncio.Task]] = {}
        self._active_stop_events: Dict[Tuple[str, int], Set[asyncio.Event]] = {}
        self._seek_generations: Dict[Tuple[str, int], int] = {}
        self._active_read_positions: Dict[Tuple[str, int], int] = {}
        self._retained_cleanup_tasks: Set[asyncio.Task] = set()

    def _get_chunk_lock(self) -> asyncio.Lock:
        if self._chunk_lock is None:
            self._chunk_lock = asyncio.Lock()
        return self._chunk_lock

    def _get_mtproto_sem(self) -> asyncio.Semaphore:
        if self._mtproto_sem is None:
            self._mtproto_sem = asyncio.Semaphore(16)
        return self._mtproto_sem

    def get_seek_generation(self, chat_id: Union[str, int], message_id: int) -> int:
        norm_cid = StreamSlidingBuffer._canonical_cid(chat_id)
        return self._seek_generations.get((norm_cid, int(message_id)), 0)

    def advance_seek_generation(self, chat_id: Union[str, int], message_id: int, new_offset: int, chunk_size: int = 512 * 1024) -> int:
        norm_cid = StreamSlidingBuffer._canonical_cid(chat_id)
        key = (norm_cid, int(message_id))
        gen = self._seek_generations.get(key, 0) + 1
        self._seek_generations[key] = gen
        self.seek_episode_manager.observe(norm_cid, int(message_id), new_offset, chunk_size)

        prev_offset = self._active_read_positions.get(key)
        if prev_offset is not None and prev_offset != new_offset:
            retained_offsets = [
                max(0, prev_offset - chunk_size * 2),
                max(0, prev_offset - chunk_size),
                prev_offset,
            ]
            stream_buffer.retain_chunks(norm_cid, int(message_id), retained_offsets, ttl=30.0)

        self._active_read_positions[key] = new_offset
        return gen

    def abort_message_stream(self, chat_id: Union[str, int], message_id: int, cancel_retained: bool = True):
        s_cid = str(chat_id).strip()
        norm_cid = s_cid[4:] if s_cid.startswith("-100") else (s_cid[3:] if s_cid.startswith("100") else s_cid.lstrip("-"))
        candidate_cids = {s_cid, norm_cid, f"-100{norm_cid}", f"-{norm_cid}"}

        for cid in candidate_cids:
            mk = (cid, int(message_id))
            stop_events = self._active_stop_events.pop(mk, set())
            for ev in stop_events:
                ev.set()
            wire_tasks = self._active_stream_wire_tasks.pop(mk, set())
            for task in wire_tasks:
                if not task.done():
                    task_off = getattr(task, "_chunk_offset", None)
                    if cancel_retained or task_off is None or not stream_buffer.is_chunk_retained(cid, int(message_id), task_off):
                        task.cancel()

        matching_keys = [k for k in list(self._in_flight_chunks.keys()) if str(k[0]) in candidate_cids and k[1] == int(message_id)]
        for k in matching_keys:
            c_offset = k[2] if len(k) > 2 else 0
            if cancel_retained or not stream_buffer.is_chunk_retained(str(k[0]), int(message_id), c_offset):
                fut = self._in_flight_chunks.pop(k, None)
                if fut and not fut.done():
                    fut.cancel()
                ct = self._chunk_task_registry.pop(k, None)
                if ct and not ct.future.done():
                    ct.future.cancel()

    async def stream_media(
        self,
        client: Any,
        chat_id: Union[int, str],
        message_id: int,
        start: int,
        length: int,
        file_size: int = 0,
        is_media_player: bool = False,
        clean_name: str = "media",
        cached_prefix: Optional[bytes] = None,
        message: Optional[Any] = None,
        get_fresh_message_fn: Optional[Any] = None,
        external_stop_event: Optional[asyncio.Event] = None,
        request_id: Optional[str] = None
    ) -> AsyncGenerator[bytes, None]:
        t0 = time.time()
        bytes_sent = 0
        first_chunk_sent = False
        effective_start = start
        orig_cid = str(chat_id).lstrip("-").replace("100", "", 1)
        orig_mid = int(message_id)
        norm_cid = orig_cid
        media_key = (norm_cid, orig_mid)
        stream_wire_tasks = set()

        if not request_id:
            request_id = f"req_{uuid.uuid4().hex[:8]}"

        if media_key not in self._active_stream_wire_tasks:
            self._active_stream_wire_tasks[media_key] = set()
        if media_key not in self._active_stop_events:
            self._active_stop_events[media_key] = set()
        if external_stop_event:
            self._active_stop_events[media_key].add(external_stop_event)

        if external_stop_event and external_stop_event.is_set():
            logger.debug(f"STREAM [{clean_name}] Aborted stream before start (superseded by seek)")
            return

        if cached_prefix and len(cached_prefix) >= length:
            logger.info(f"STREAM [{clean_name}] Complete (RAM prefix only): {length:,}B delivered in {time.time() - t0:.3f}s (0ms TTFB)")
            yield cached_prefix[:length]
            return

        cached_prefix_len = len(cached_prefix) if cached_prefix else 0
        effective_start = start + cached_prefix_len
        remaining_length = length - cached_prefix_len

        if length <= 256 * 1024 or (file_size > 0 and effective_start >= (file_size - 4 * 1024 * 1024)):
            chunk_size = 128 * 1024
        else:
            chunk_size = 1024 * 1024

        episode = self.seek_episode_manager.observe(norm_cid, orig_mid, effective_start, chunk_size)
        episode.active_subscribers.add(request_id)

        aligned_offset = (effective_start // chunk_size) * chunk_size
        discard_front = effective_start - aligned_offset
        total_chunks = (remaining_length + discard_front + chunk_size - 1) // chunk_size

        from app.telegram_client import TelegramClientManager
        relay_id = TelegramClientManager.get_relay_channel_id()
        if relay_id and str(chat_id) != str(relay_id) and get_fresh_message_fn:
            try:
                relayed_mid = await TelegramClientManager.forward_to_relay_channel(chat_id, message_id)
                if relayed_mid:
                    relay_msg = await get_fresh_message_fn(client, relay_id, relayed_mid)
                    if relay_msg and hasattr(relay_msg, "media") and relay_msg.media:
                        message = relay_msg
                        chat_id = relay_id
                        message_id = relayed_mid
                        norm_cid = str(chat_id).lstrip("-").replace("100", "", 1)
                        media_key = (norm_cid, int(message_id))
            except Exception as rfe:
                logger.debug(f"Streaming relay switch notice: {rfe}")

        # Resolve media location and DC ID
        try:
            file_info = utils._get_file_info(message.media) if (message and hasattr(message, "media")) else None
        except Exception:
            file_info = None
        file_loc = getattr(file_info, "location", None)
        dc_id = getattr(file_info, "dc_id", None) or getattr(getattr(client, "session", None), "dc_id", 5)

        session_dc = getattr(getattr(client, "session", None), "dc_id", None)
        is_foreign = bool(dc_id and session_dc and dc_id != session_dc)

        try:
            active_clients = await TelegramClientManager.get_active_streaming_clients()
        except Exception:
            active_clients = []
        if not active_clients or (client and client not in active_clients):
            active_clients = [client] if client else (active_clients or [client])

        worker_file_locs = {id(client): file_loc}
        is_saved_messages = str(chat_id).lower() == "me" or (
            hasattr(client, "session") and str(chat_id) == str(getattr(client.session, "user_id", ""))
        )

        if not is_saved_messages and len(active_clients) > 1 and get_fresh_message_fn:
            valid_active_clients = [client]
            for sec_client in active_clients:
                if sec_client is client:
                    continue
                try:
                    sec_msg = await get_fresh_message_fn(sec_client, chat_id, message_id)
                    if sec_msg and hasattr(sec_msg, "media") and sec_msg.media:
                        sec_info = utils._get_file_info(sec_msg.media)
                        sec_loc = getattr(sec_info, "location", None)
                        if sec_loc:
                            worker_file_locs[id(sec_client)] = sec_loc
                            valid_active_clients.append(sec_client)
                            logger.info(f"STREAM [{clean_name}] Secondary account synchronized for pooled streaming on DC {dc_id}.")
                except Exception as sec_e:
                    logger.info(
                        f"STREAM [{clean_name}] Secondary account cannot access channel {chat_id} ({type(sec_e).__name__}); "
                        f"streaming via primary account only."
                    )
            active_clients = valid_active_clients
        elif is_saved_messages:
            active_clients = [client]

        logger.info(
            f"STREAM [{clean_name}] Starting range: offset={effective_start:,}, "
            f"length={remaining_length:,}B ({remaining_length / (1024*1024):.2f}MB), "
            f"chunk_size={chunk_size // 1024}KB, total_chunks={total_chunks}, DC={dc_id}, "
            f"active_accounts={len(active_clients)}"
        )

        queue: asyncio.Queue = asyncio.Queue(maxsize=32 if is_media_player else 64)
        stop_event = external_stop_event or asyncio.Event()
        if media_key in self._active_stop_events:
            self._active_stop_events[media_key].add(stop_event)

        async def _download_chunk_from_mtproto(target_offset: int, target_size: int, worker_client: Any, is_emergency: bool, is_head_chunk: bool = False) -> bytes:
            nonlocal file_loc, message, active_clients
            if stop_event.is_set() and not is_head_chunk:
                is_cand = self.seek_episode_manager.is_candidate_window(norm_cid, message_id, target_offset, target_size)
                is_retained = stream_buffer.is_chunk_retained(norm_cid, message_id, target_offset)
                if not is_cand and not is_retained:
                    raise asyncio.CancelledError("Stream aborted by seek")
            cached_data = stream_buffer.get_slice(norm_cid, message_id, target_offset, target_size)
            if not cached_data and (orig_cid, orig_mid) != (norm_cid, message_id):
                cached_data = stream_buffer.get_slice(orig_cid, orig_mid, target_offset, target_size)
            if cached_data and len(cached_data) == target_size:
                return cached_data
            disk_data = stream_cache_service.get_slice(norm_cid, message_id, target_offset, target_size)
            if not disk_data and (orig_cid, orig_mid) != (norm_cid, message_id):
                disk_data = stream_cache_service.get_slice(orig_cid, orig_mid, target_offset, target_size)
            if disk_data and len(disk_data) == target_size:
                stream_buffer.push(norm_cid, message_id, target_offset, disk_data)
                if (orig_cid, orig_mid) != (norm_cid, message_id):
                    stream_buffer.push(orig_cid, orig_mid, target_offset, disk_data)
                if file_size > 0 and target_offset >= (file_size - 4 * 1024 * 1024):
                    stream_buffer.push_tail(norm_cid, message_id, target_offset, disk_data)
                return disk_data

            target_loc = worker_file_locs.get(id(worker_client), file_loc)

            doc_id = getattr(target_loc, "id", None)
            access_hash = getattr(target_loc, "access_hash", None)
            file_ref = getattr(target_loc, "file_reference", None)

            await rate_governor.acquire_chunk_slot(target_size, is_emergency=is_emergency, is_unlimited=not is_media_player)

            if doc_id and access_hash and file_ref:
                for rust_attempt in range(2):
                    try:
                        from app.services.rust_bridge import rust_bridge
                        if await rust_bridge.is_healthy():
                            target_account = active_clients.index(worker_client) if (worker_client in active_clients and id(worker_client) in worker_file_locs) else 0
                            rust_timeout = 15.0 if is_media_player else 28.0
                            rust_data = await asyncio.wait_for(
                                rust_bridge.stream_chunk(
                                    dc_id=dc_id,
                                    doc_id=doc_id,
                                    access_hash=access_hash,
                                    file_reference=file_ref,
                                    offset=target_offset,
                                    limit=target_size,
                                    priority="high" if (is_media_player or is_emergency) else "bulk",
                                    account_idx=target_account
                                ),
                                timeout=rust_timeout
                            )
                            if rust_data and len(rust_data) > 0:
                                stream_buffer.push(norm_cid, message_id, target_offset, rust_data)
                                if file_size > 0 and target_offset >= (file_size - 4 * 1024 * 1024):
                                    stream_buffer.push_tail(norm_cid, message_id, target_offset, rust_data)
                                stream_cache_service.put_slice(norm_cid, message_id, target_offset, rust_data)
                                return rust_data

                            if len(active_clients) > 1 and not is_saved_messages:
                                alt_idx = 1 if target_account == 0 else 0
                                alt_client = active_clients[alt_idx]
                                alt_loc = worker_file_locs.get(id(alt_client))
                                if alt_loc:
                                    alt_doc_id = getattr(alt_loc, "id", None)
                                    alt_access_hash = getattr(alt_loc, "access_hash", None)
                                    alt_file_ref = getattr(alt_loc, "file_reference", None)
                                    if alt_doc_id and alt_access_hash and alt_file_ref:
                                        rust_data = await asyncio.wait_for(
                                            rust_bridge.stream_chunk(
                                                dc_id=dc_id,
                                                doc_id=alt_doc_id,
                                                access_hash=alt_access_hash,
                                                file_reference=alt_file_ref,
                                                offset=target_offset,
                                                limit=target_size,
                                                priority="high" if (is_media_player or is_emergency) else "bulk",
                                                account_idx=alt_idx
                                            ),
                                            timeout=rust_timeout
                                        )
                                        if rust_data and len(rust_data) > 0:
                                            stream_buffer.push(norm_cid, message_id, target_offset, rust_data)
                                            stream_cache_service.put_slice(norm_cid, message_id, target_offset, rust_data)
                                            if (orig_cid, orig_mid) != (norm_cid, message_id):
                                                stream_buffer.push(orig_cid, orig_mid, target_offset, rust_data)
                                                stream_cache_service.put_slice(orig_cid, orig_mid, target_offset, rust_data)
                                            if file_size > 0 and target_offset >= (file_size - 4 * 1024 * 1024):
                                                stream_buffer.push_tail(norm_cid, message_id, target_offset, rust_data)
                                            return rust_data
                        break
                    except Exception as r_err:
                        err_s = str(r_err)
                        if "FILE_REFERENCE_EXPIRED" in err_s and rust_attempt == 0:
                            logger.info(f"STREAM [{clean_name}] Rust reported FILE_REFERENCE_EXPIRED for chunk at {target_offset:,}. Refreshing...")
                            if rate_governor.is_client_cooling_down(worker_client) and len(active_clients) > 1:
                                alt_idx = 1 if target_account == 0 else 0
                                alt_client = active_clients[alt_idx]
                                if not rate_governor.is_client_cooling_down(alt_client):
                                    worker_client = alt_client
                                    target_loc = worker_file_locs.get(id(worker_client), file_loc)
                                    doc_id = getattr(target_loc, "id", None)
                                    access_hash = getattr(target_loc, "access_hash", None)
                                    file_ref = getattr(target_loc, "file_reference", None)
                                    continue
                            if get_fresh_message_fn:
                                refreshed = await get_fresh_message_fn(worker_client, chat_id, message_id, force_refresh=True)
                                if refreshed and hasattr(refreshed, "media"):
                                    ref_info = utils._get_file_info(refreshed.media)
                                    if ref_info and hasattr(ref_info, "location"):
                                        if worker_client is client:
                                            message = refreshed
                                            file_loc = ref_info.location
                                        worker_file_locs[id(worker_client)] = ref_info.location
                                        target_loc = ref_info.location
                                        doc_id = getattr(target_loc, "id", None)
                                        access_hash = getattr(target_loc, "access_hash", None)
                                        file_ref = getattr(target_loc, "file_reference", None)
                                        req = GetFileRequest(target_loc, offset=target_offset, limit=target_size)
                                        continue
                        if "FloodWait" in err_s or "cooldown" in err_s:
                            alternate_clients = [c for c in active_clients if c is not worker_client and not rate_governor.is_client_cooling_down(c)]
                            if alternate_clients:
                                worker_client = alternate_clients[0]
                        logger.debug(f"Rust stream chunk fallback notice: {r_err}")
                        break

            from app.services.rust_bridge import rust_bridge
            rust_bridge.python_fallback_chunks += 1
            req = GetFileRequest(target_loc, offset=target_offset, limit=target_size)

            file_ref_attempts = 0
            for attempt in range(6):
                if stop_event.is_set() and not is_head_chunk and not stream_buffer.is_chunk_retained(norm_cid, message_id, target_offset):
                    raise asyncio.CancelledError("Stream aborted by seek")
                try:
                    c_t0 = time.time()
                    if stop_event.is_set() and not is_head_chunk and not stream_buffer.is_chunk_retained(norm_cid, message_id, target_offset):
                        raise asyncio.CancelledError("Stream aborted by seek")
                    chunk_timeout = 16.0 if is_media_player else 25.0
                    async with self._get_mtproto_sem():
                        async with sender_pool.borrow(worker_client, dc_id, priority=is_media_player) as s:
                            res = await asyncio.wait_for(
                                worker_client._call(s, req, flood_sleep_threshold=0),
                                timeout=chunk_timeout
                            )
                    c_dt = max(0.001, time.time() - c_t0)
                    if hasattr(res, "bytes"):
                        data = res.bytes
                    elif isinstance(res, (bytes, bytearray)):
                        data = bytes(res)
                    else:
                        data = b""

                    stream_buffer.push(norm_cid, message_id, target_offset, data)
                    stream_cache_service.put_slice(norm_cid, message_id, target_offset, data)
                    if (orig_cid, orig_mid) != (norm_cid, message_id):
                        stream_buffer.push(orig_cid, orig_mid, target_offset, data)
                        stream_cache_service.put_slice(orig_cid, orig_mid, target_offset, data)
                    if file_size > 0 and target_offset >= (file_size - 4 * 1024 * 1024):
                        stream_buffer.push_tail(norm_cid, message_id, target_offset, data)

                    c_speed = (len(data) / (1024 * 1024)) / c_dt
                    logger.info(
                        f"STREAM [{clean_name}] Chunk at {target_offset:,}: "
                        f"{len(data):,}B in {c_dt:.2f}s ({c_speed:.1f} MB/s) from DC {dc_id}"
                    )
                    return data

                except (getattr(errors, "ChannelPrivateError", Exception),
                        getattr(errors, "ChatAdminRequiredError", Exception),
                        getattr(errors, "ChatForbiddenError", Exception),
                        getattr(errors, "UserNotParticipantError", Exception)) as priv_err:
                    logger.info(
                        f"STREAM [{clean_name}] Worker client cannot access private channel ({type(priv_err).__name__}). "
                        f"Falling back chunk to primary client immediately."
                    )
                    worker_client = client
                    if len(active_clients) > 1:
                        active_clients = [client]
                    continue

                except (asyncio.TimeoutError, ConnectionError, BrokenPipeError, OSError, RuntimeError) as conn_err:
                    err_str = f"{type(conn_err).__name__}: {conn_err}" if str(conn_err) else type(conn_err).__name__
                    logger.warning(f"STREAM [{clean_name}] Network/socket glitch on DC {dc_id} (attempt {attempt+1}/6): {err_str}")

                    if "Unable to establish MTProto connection" in err_str:
                        rate_governor.report_flood_wait(300, f"Worker failure DC {dc_id}", client=worker_client, all_clients_in_pool=active_clients)
                        alternate_clients = [c for c in active_clients if c is not worker_client and not rate_governor.is_client_cooling_down(c)]
                        if alternate_clients:
                            worker_client = alternate_clients[0]
                            target_loc = worker_file_locs.get(id(worker_client), file_loc)
                            req = GetFileRequest(target_loc, offset=target_offset, limit=target_size)
                            continue

                    if hasattr(worker_client, "is_connected") and not worker_client.is_connected():
                        try:
                            await asyncio.wait_for(worker_client.connect(), timeout=5.0)
                        except Exception as rec_err:
                            logger.debug(f"Reconnection attempt: {rec_err}")

                    backoff = min(2.0, 0.2 * (1.5 ** attempt))
                    await asyncio.sleep(backoff)
                    continue

                except errors.FloodWaitError as fwe:
                    wait_time = max(1, int(fwe.seconds))
                    rate_governor.report_flood_wait(
                        wait_time,
                        f"FloodWait DC {dc_id}",
                        client=worker_client,
                        all_clients_in_pool=active_clients
                    )
                    logger.warning(f"STREAM [{clean_name}] FloodWait {wait_time}s on DC {dc_id}...")
                    alternate_clients = [c for c in active_clients if c is not worker_client and not rate_governor.is_client_cooling_down(c)]
                    if alternate_clients:
                        worker_client = alternate_clients[0]
                        logger.info(f"STREAM [{clean_name}] Switched chunk to healthy alternate client.")
                        continue
                    await asyncio.sleep(wait_time + 0.5)
                    continue

                except FILE_REF_ERRORS:
                    file_ref_attempts += 1
                    logger.info(f"STREAM [{clean_name}] File ref expired on chunk at {target_offset:,} (refresh {file_ref_attempts}/2)...")
                    if file_ref_attempts > 2:
                        if worker_client is not client:
                            logger.warning(
                                f"STREAM [{clean_name}] Worker client encountered file ref mismatch. "
                                f"Falling back chunk to primary client immediately."
                            )
                            worker_client = client
                            target_loc = file_loc
                            req = GetFileRequest(target_loc, offset=target_offset, limit=target_size)
                            if len(active_clients) > 1:
                                active_clients = [client]
                            continue
                        raise RuntimeError(f"File ref refresh limit exceeded for chunk at {target_offset:,}")
                    if get_fresh_message_fn:
                        refreshed = await get_fresh_message_fn(worker_client, chat_id, message_id, force_refresh=True)
                        if refreshed and hasattr(refreshed, "media"):
                            ref_info = utils._get_file_info(refreshed.media)
                            if ref_info and hasattr(ref_info, "location"):
                                if worker_client is client:
                                    message = refreshed
                                    file_loc = ref_info.location
                                worker_file_locs[id(worker_client)] = ref_info.location
                                target_loc = ref_info.location
                                req = GetFileRequest(target_loc, offset=target_offset, limit=target_size)
                    await asyncio.sleep(0.3)
                    continue

                except Exception as fe:
                    fe_s = str(fe)
                    if "FloodWait" in fe_s or "cooldown" in fe_s or "Unable to establish MTProto connection" in fe_s:
                        rate_governor.report_flood_wait(300, f"Worker failure DC {dc_id}", client=worker_client, all_clients_in_pool=active_clients)
                        alternate_clients = [c for c in active_clients if c is not worker_client and not rate_governor.is_client_cooling_down(c)]
                        if alternate_clients:
                            worker_client = alternate_clients[0]
                            target_loc = worker_file_locs.get(id(worker_client), file_loc)
                            req = GetFileRequest(target_loc, offset=target_offset, limit=target_size)
                            continue
                    if worker_client is not client:
                        logger.warning(
                            f"STREAM [{clean_name}] Secondary worker client failed on chunk: {fe}. "
                            f"Falling back chunk to primary client immediately."
                        )
                        worker_client = client
                        target_loc = file_loc
                        req = GetFileRequest(target_loc, offset=target_offset, limit=target_size)
                        if len(active_clients) > 1:
                            active_clients = [client]
                        continue
                    err_str = f"{type(fe).__name__}: {fe}" if str(fe) else type(fe).__name__
                    if attempt >= 5:
                        logger.warning(f"STREAM [{clean_name}] Chunk at {target_offset:,} failed (attempt {attempt+1}): {err_str}")
                        raise
                    else:
                        logger.debug(f"STREAM [{clean_name}] Chunk at {target_offset:,} retry {attempt+1}: {err_str}")
                    await asyncio.sleep(0.4)

            raise RuntimeError(f"STREAM [{clean_name}] Chunk at {target_offset:,} failed after 6 attempts")

        async def fetch_one_chunk(c_idx: int, worker_client: Any = None, is_emergency: bool = False):
            nonlocal file_loc, message, active_clients
            if worker_client is None:
                worker_client = client
            if stop_event.is_set():
                is_cand = self.seek_episode_manager.is_candidate_window(norm_cid, message_id, aligned_offset + (c_idx * chunk_size), chunk_size)
                is_retained = stream_buffer.is_chunk_retained(norm_cid, message_id, aligned_offset + (c_idx * chunk_size))
                if not is_cand and not is_retained:
                    raise asyncio.CancelledError()
            c_offset = aligned_offset + (c_idx * chunk_size)

            cached_data = stream_buffer.get_slice(norm_cid, message_id, c_offset, chunk_size)
            if not cached_data and (orig_cid, orig_mid) != (norm_cid, message_id):
                cached_data = stream_buffer.get_slice(orig_cid, orig_mid, c_offset, chunk_size)
            if cached_data and len(cached_data) == chunk_size:
                logger.info(f"STREAM [{clean_name}] Chunk {c_idx+1}/{total_chunks} at {c_offset:,}: RAM buffer hit (0ms)")
                return c_idx, cached_data
            disk_data = stream_cache_service.get_slice(norm_cid, message_id, c_offset, chunk_size)
            if not disk_data and (orig_cid, orig_mid) != (norm_cid, message_id):
                disk_data = stream_cache_service.get_slice(orig_cid, orig_mid, c_offset, chunk_size)
            if disk_data and len(disk_data) == chunk_size:
                stream_buffer.push(norm_cid, message_id, c_offset, disk_data)
                if (orig_cid, orig_mid) != (norm_cid, message_id):
                    stream_buffer.push(orig_cid, orig_mid, c_offset, disk_data)
                logger.info(f"STREAM [{clean_name}] Chunk {c_idx+1}/{total_chunks} at {c_offset:,}: Disk buffer hit (0ms)")
                return c_idx, disk_data

            chunk_key = (norm_cid, int(message_id), c_offset)
            lock = self._get_chunk_lock()
            async with lock:
                task_obj = self._chunk_task_registry.get(chunk_key)
                if task_obj is None or task_obj.future.done():
                    task_obj = ChunkTask(chunk_key)
                    task_obj.subscribers.add(request_id)
                    task_obj.priority = 0 if c_idx == 0 else (1 if is_emergency else 3)
                    self._chunk_task_registry[chunk_key] = task_obj
                    self._in_flight_chunks[chunk_key] = task_obj.future
                    is_lead = True
                else:
                    task_obj.subscribers.add(request_id)
                    if c_idx == 0:
                        task_obj.priority = 0
                    elif is_emergency and task_obj.priority > 1:
                        task_obj.priority = 1
                    is_lead = False
                wire_fut = task_obj.future

            if is_lead:
                async def _background_wire_runner():
                    try:
                        if stop_event.is_set() and c_idx > 0:
                            is_cand = self.seek_episode_manager.is_candidate_window(norm_cid, message_id, c_offset, chunk_size)
                            is_retained = stream_buffer.is_chunk_retained(norm_cid, message_id, c_offset)
                            if not is_cand and not is_retained:
                                if not wire_fut.done():
                                    wire_fut.cancel()
                                return
                        data = await _download_chunk_from_mtproto(
                            c_offset, chunk_size, worker_client, is_emergency, is_head_chunk=(c_idx == 0)
                        )
                        task_obj.completed = True
                        if not wire_fut.done():
                            wire_fut.set_result(data)
                    except (asyncio.CancelledError, BaseException) as e:
                        if not wire_fut.done():
                            if isinstance(e, asyncio.CancelledError):
                                wire_fut.cancel()
                            else:
                                wire_fut.set_exception(e)
                    finally:
                        async with lock:
                            if self._chunk_task_registry.get(chunk_key) is task_obj:
                                self._chunk_task_registry.pop(chunk_key, None)
                            if self._in_flight_chunks.get(chunk_key) is wire_fut:
                                self._in_flight_chunks.pop(chunk_key, None)

                wt = asyncio.create_task(_background_wire_runner())
                wt._chunk_idx = c_idx
                wt._chunk_offset = c_offset
                task_obj.wire_task = wt
                stream_wire_tasks.add(wt)
                if media_key in self._active_stream_wire_tasks:
                    self._active_stream_wire_tasks[media_key].add(wt)
                wt.add_done_callback(stream_wire_tasks.discard)
                wt.add_done_callback(lambda t: self._active_stream_wire_tasks.get(media_key, set()).discard(t))
            else:
                logger.info(
                    f"STREAM [{clean_name}] Chunk {c_idx+1}/{total_chunks} at {c_offset:,}: "
                    f"Joined in-flight wire download (request={request_id}, episode={episode.episode_id}, subscribers={len(task_obj.subscribers)})"
                )

            try:
                if c_idx == 0:
                    data = await asyncio.shield(wire_fut)
                else:
                    data = await asyncio.wait_for(asyncio.shield(wire_fut) if is_lead else wire_fut, timeout=25.0)
                return c_idx, data
            except asyncio.CancelledError:
                task_obj.subscribers.discard(request_id)
                if not task_obj.subscribers:
                    is_cand = self.seek_episode_manager.is_candidate_window(norm_cid, message_id, c_offset, chunk_size)
                    is_retained = stream_buffer.is_chunk_retained(norm_cid, message_id, c_offset)
                    if not is_cand and not is_retained and (time.time() - task_obj.created_at) > 10.0:
                        if task_obj.wire_task and not task_obj.wire_task.done():
                            task_obj.wire_task.cancel()
                        if not wire_fut.done():
                            wire_fut.cancel()
                    else:
                        logger.debug(
                            f"STREAM [{clean_name}] Soft-cancelling reader {request_id} on chunk at {c_offset:,}: "
                            f"Wire task preserved for episode {episode.episode_id}"
                        )
                raise
            except Exception as fut_err:
                if stop_event.is_set():
                    is_cand = self.seek_episode_manager.is_candidate_window(norm_cid, message_id, c_offset, chunk_size)
                    is_retained = stream_buffer.is_chunk_retained(norm_cid, message_id, c_offset)
                    if not is_cand and not is_retained:
                        raise
                cached_data = stream_buffer.get_slice(norm_cid, message_id, c_offset, chunk_size)
                if cached_data and len(cached_data) == chunk_size:
                    return c_idx, cached_data
                disk_data = stream_cache_service.get_slice(norm_cid, message_id, c_offset, chunk_size)
                if disk_data and len(disk_data) == chunk_size:
                    stream_buffer.push(norm_cid, message_id, c_offset, disk_data)
                    return c_idx, disk_data
                data = await _download_chunk_from_mtproto(
                    c_offset, chunk_size, worker_client, is_emergency, is_head_chunk=(c_idx == 0)
                )
                return c_idx, data

        last_progress_log = time.time()
        last_interval_time = last_progress_log
        last_interval_bytes = 0
        delivered_chunks = 0

        if file_size > 6 * 1024 * 1024 and remaining_length > 0 and start == 0:
            async def _prefetch_dual_anchors():
                try:
                    await asyncio.sleep(0.5)
                    if stop_event.is_set():
                        return
                    head_anchor = stream_buffer.get_slice(norm_cid, message_id, 0, 1024 * 1024) or stream_cache_service.get_slice(norm_cid, message_id, 0, 1024 * 1024)
                    tail_offset = max(0, (file_size // 1024 // 1024 - 1) * 1024 * 1024)
                    tail_anchor = stream_buffer.get_slice(norm_cid, message_id, tail_offset, 1024 * 1024) or stream_cache_service.get_slice(norm_cid, message_id, tail_offset, 1024 * 1024)
                    anchor_client = active_clients[-1] if len(active_clients) > 1 else active_clients[0]
                    if not head_anchor and not stop_event.is_set():
                        h_data = await _download_chunk_from_mtproto(0, 1024 * 1024, anchor_client, is_emergency=False, is_head_chunk=True)
                        if h_data:
                            stream_buffer.push(norm_cid, message_id, 0, h_data)
                            stream_cache_service.put_slice(norm_cid, message_id, 0, h_data)
                    if not tail_anchor and not stop_event.is_set():
                        sz = min(1024 * 1024, file_size - tail_offset)
                        if sz > 0:
                            t_data = await _download_chunk_from_mtproto(tail_offset, sz, anchor_client, is_emergency=False, is_head_chunk=True)
                            if t_data:
                                stream_buffer.push(norm_cid, message_id, tail_offset, t_data)
                                stream_buffer.push_tail(norm_cid, message_id, tail_offset, t_data)
                                stream_cache_service.put_slice(norm_cid, message_id, tail_offset, t_data)
                except Exception as e:
                    logger.debug(f"Dual-anchor prefetch notice: {e}")

            dual_anchor_task = asyncio.create_task(_prefetch_dual_anchors())
            stream_wire_tasks.add(dual_anchor_task)
            dual_anchor_task.add_done_callback(stream_wire_tasks.discard)

        producer_task = None
        if remaining_length > 0 and total_chunks > 0:
            async def pipelined_chunk_producer():
                from collections import defaultdict
                chunk_retries = defaultdict(int)
                client_active_chunks = defaultdict(int)
                client_latencies = {}
                in_flight_clients = {}

                is_multi_account = len(active_clients) >= 2
                is_premium_rate = rate_governor.current_rate_limit_mbs > 15.0
                if is_media_player:
                    max_in_flight = 8 if (is_premium_rate or is_multi_account) else 6
                    max_lookahead = 32 if (is_premium_rate or is_multi_account) else 24
                else:
                    max_in_flight = 12 if is_multi_account else 8
                    max_lookahead = 48 if is_multi_account else 32

                in_flight = {}
                next_to_fetch = 0
                next_to_deliver = 0
                buffered = {}
                wire_speed_mb_s = 5.0
                wire_bytes_window = 0
                last_wire_calc_time = time.time()

                async def _put_chunk(item):
                    """Puts chunk into queue without blocking forever if consumer exits or stop_event is set."""
                    while not stop_event.is_set():
                        try:
                            queue.put_nowait(item)
                            return True
                        except asyncio.QueueFull:
                            try:
                                await asyncio.wait_for(stop_event.wait(), timeout=0.04)
                                return False
                            except asyncio.TimeoutError:
                                continue
                    return False

                try:
                    while next_to_deliver < total_chunks and not stop_event.is_set():
                        while next_to_deliver in buffered and not stop_event.is_set():
                            data = buffered.pop(next_to_deliver)
                            if not await _put_chunk(data):
                                break
                            next_to_deliver += 1

                        if next_to_deliver >= total_chunks:
                            break

                        runway = queue.qsize() + len(buffered)
                        if is_media_player:
                            if next_to_deliver == 0:
                                allowed_in_flight = 2
                            elif runway < 12 or wire_speed_mb_s <= 1.5:
                                allowed_in_flight = 8 if (is_premium_rate or is_multi_account) else 6
                            elif runway >= 20:
                                allowed_in_flight = 3
                                if runway >= 24:
                                    await asyncio.sleep(0.02)
                            else:
                                allowed_in_flight = 4
                        else:
                            if next_to_deliver == 0:
                                allowed_in_flight = min(4, max_in_flight)
                            elif runway >= max_lookahead - 4:
                                allowed_in_flight = 3
                            else:
                                allowed_in_flight = max_in_flight

                        while (
                            len(in_flight) < allowed_in_flight
                            and (queue.qsize() + len(in_flight) + len(buffered)) < max_lookahead
                            and next_to_fetch < total_chunks
                            and not stop_event.is_set()
                        ):
                            healthy_clients = [c for c in active_clients if not rate_governor.is_client_cooling_down(c)]
                            if not healthy_clients:
                                healthy_clients = active_clients

                            if next_to_fetch == next_to_deliver:
                                if client in healthy_clients and client_active_chunks[id(client)] == 0:
                                    assigned_client = client
                                else:
                                    assigned_client = min(
                                        healthy_clients,
                                        key=lambda c: (client_active_chunks[id(c)], client_latencies.get(id(c), 1.0))
                                    )
                            else:
                                if len(healthy_clients) > 1:
                                    target_candidate = healthy_clients[next_to_fetch % len(healthy_clients)]
                                    if client_active_chunks[id(target_candidate)] == 0:
                                        assigned_client = target_candidate
                                    else:
                                        assigned_client = min(healthy_clients, key=lambda c: client_active_chunks[id(c)])
                                else:
                                    assigned_client = healthy_clients[0]

                            client_active_chunks[id(assigned_client)] += 1
                            is_emergency = (is_media_player and runway < 4) or (not is_media_player and next_to_deliver == 0)
                            t = asyncio.create_task(fetch_one_chunk(next_to_fetch, assigned_client, is_emergency=is_emergency))
                            in_flight[t] = next_to_fetch
                            in_flight_clients[t] = (id(assigned_client), time.time())
                            next_to_fetch += 1

                        if in_flight:
                            done, _ = await asyncio.wait(list(in_flight.keys()), return_when=asyncio.FIRST_COMPLETED)
                            for d in done:
                                c_idx = in_flight.pop(d, None)
                                client_info = in_flight_clients.pop(d, None)
                                if client_info:
                                    cid, t_start = client_info
                                    client_active_chunks[cid] = max(0, client_active_chunks[cid] - 1)
                                    duration = max(0.001, time.time() - t_start)
                                    old_lat = client_latencies.get(cid, duration)
                                    client_latencies[cid] = 0.7 * old_lat + 0.3 * duration

                                try:
                                    res = d.result()
                                    if res is None:
                                        raise RuntimeError(f"fetch_one_chunk({c_idx}) returned None")
                                    _, data = res
                                    buffered[c_idx] = data
                                    wire_bytes_window += len(data)
                                    now_w = time.time()
                                    w_elapsed = now_w - last_wire_calc_time
                                    if w_elapsed >= 2.0:
                                        measured_speed = (wire_bytes_window / (1024 * 1024)) / w_elapsed
                                        wire_speed_mb_s = 0.6 * wire_speed_mb_s + 0.4 * measured_speed
                                        wire_bytes_window = 0
                                        last_wire_calc_time = now_w
                                except asyncio.CancelledError:
                                    if stop_event.is_set():
                                        break
                                    cached_data = stream_buffer.get_slice(norm_cid, message_id, aligned_offset + (c_idx * chunk_size), chunk_size)
                                    if not cached_data or len(cached_data) != chunk_size:
                                        cached_data = stream_cache_service.get_slice(norm_cid, message_id, aligned_offset + (c_idx * chunk_size), chunk_size)
                                    if cached_data and len(cached_data) == chunk_size:
                                        buffered[c_idx] = cached_data
                                        continue
                                    chunk_retries[c_idx] += 1
                                    if chunk_retries[c_idx] >= 6:
                                        stop_event.set()
                                        break
                                    await asyncio.sleep(0.1)
                                    healthy_clients = [c for c in active_clients if not rate_governor.is_client_cooling_down(c)]
                                    retry_client = min(healthy_clients, key=lambda c: client_active_chunks[id(c)]) if healthy_clients else client
                                    client_active_chunks[id(retry_client)] += 1
                                    t = asyncio.create_task(fetch_one_chunk(c_idx, retry_client, is_emergency=True))
                                    in_flight[t] = c_idx
                                    in_flight_clients[t] = (id(retry_client), time.time())
                                except Exception as e:
                                    chunk_retries[c_idx] += 1
                                    if chunk_retries[c_idx] >= 6:
                                        logger.error(
                                            f"STREAM [{clean_name}] Chunk {c_idx} permanently failed after {chunk_retries[c_idx]} retries: {e}. Terminating stream."
                                        )
                                        stop_event.set()
                                        await queue.put(e)
                                        break
                                    logger.warning(f"STREAM [{clean_name}] Chunk {c_idx} failed (attempt {chunk_retries[c_idx]}/6): {type(e).__name__} ({e or 'timeout'}). Retrying...")
                                    if not stop_event.is_set():
                                        await asyncio.sleep(0.1)
                                        healthy_clients = [c for c in active_clients if not rate_governor.is_client_cooling_down(c)]
                                        retry_client = min(healthy_clients, key=lambda c: client_active_chunks[id(c)]) if healthy_clients else client
                                        client_active_chunks[id(retry_client)] += 1
                                        t = asyncio.create_task(fetch_one_chunk(c_idx, retry_client, is_emergency=True))
                                        in_flight[t] = c_idx
                                        in_flight_clients[t] = (id(retry_client), time.time())
                        else:
                            if next_to_fetch >= total_chunks:
                                if next_to_deliver not in buffered:
                                    break
                            else:
                                if next_to_deliver in buffered:
                                    continue
                                await asyncio.sleep(0.05)

                    if not stop_event.is_set():
                        await _put_chunk(None)
                except asyncio.CancelledError:
                    logger.debug(f"STREAM [{clean_name}] Pipelined producer cancelled")
                except Exception as e:
                    logger.debug(f"STREAM [{clean_name}] Producer notice: {e}")
                finally:
                    if in_flight:
                        for t in list(in_flight.keys()):
                            if not t.done():
                                t.cancel()
                    in_flight_clients.clear()
                    client_active_chunks.clear()
                    try:
                        queue.put_nowait(None)
                    except Exception:
                        pass

            producer_task = asyncio.create_task(pipelined_chunk_producer())

        # 3. Deliver pre-cached RAM prefix immediately while producer warms wire lookahead in parallel
        if cached_prefix:
            first_chunk_sent = True
            logger.info(f"STREAM [{clean_name}] RAM prefix hit: {len(cached_prefix):,}B delivered in {time.time() - t0:.3f}s (0ms TTFB)")
            yield cached_prefix
            bytes_sent += len(cached_prefix)

        try:
            feed_pos = aligned_offset
            while bytes_sent < length:
                if stop_event.is_set() and queue.empty():
                    raise ConnectionResetError(f"Stream aborted before full length: sent {bytes_sent}/{length} bytes")
                try:
                    chunk = await asyncio.wait_for(queue.get(), timeout=2.0)
                except asyncio.TimeoutError:
                    if stop_event.is_set() or producer_task.done():
                        if bytes_sent < length:
                            raise ConnectionResetError(f"Stream aborted before full length: sent {bytes_sent}/{length} bytes")
                        break
                    continue
                if isinstance(chunk, Exception):
                    raise chunk
                if chunk is None:
                    if bytes_sent < length:
                        logger.debug(f"STREAM [{clean_name}] Pipeline stopped early: sent {bytes_sent:,}/{length:,} bytes")
                        raise ConnectionResetError(f"Stream aborted before full length: sent {bytes_sent}/{length} bytes")
                    break

                if not first_chunk_sent:
                    first_chunk_sent = True
                    ttfb = time.time() - t0
                    logger.info(f"STREAM [{clean_name}] Seek target chunk 1/{total_chunks} ({len(chunk):,}B) delivered in {ttfb:.3f}s (TTFB)")

                feed_pos += len(chunk)

                if discard_front > 0:
                    if len(chunk) <= discard_front:
                        discard_front -= len(chunk)
                        continue
                    chunk = chunk[discard_front:]
                    discard_front = 0

                remaining = length - bytes_sent
                if len(chunk) > remaining:
                    chunk = chunk[:remaining]

                yield chunk
                bytes_sent += len(chunk)
                delivered_chunks += 1

                # Periodic throughput progress logging every 5 seconds
                now = time.time()
                if (now - last_progress_log) >= 5.0 and bytes_sent > 0:
                    last_progress_log = now
                    elapsed = max(0.01, now - t0)
                    playback_speed = (bytes_sent / (1024 * 1024)) / elapsed
                    interval_elapsed = max(0.01, now - last_interval_time)
                    interval_bytes = bytes_sent - last_interval_bytes
                    window_player_speed = (interval_bytes / (1024 * 1024)) / interval_elapsed
                    last_interval_time = now
                    last_interval_bytes = bytes_sent
                    pct = (bytes_sent * 100.0 / length) if length > 0 else 0
                    q_len = queue.qsize()
                    buffered_mb = (q_len * chunk_size) / (1024 * 1024)
                    from app.services.rust_bridge import rust_bridge
                    wire_telemetry = rust_bridge.get_telemetry()
                    logger.info(
                        f"STREAM [{clean_name}] Progress: {bytes_sent:,}/{length:,}B ({pct:.1f}%) "
                        f"| Player: {window_player_speed:.2f} MB/s (cum {playback_speed:.2f} MB/s) "
                        f"| Buffer: ~{buffered_mb:.1f}MB ({q_len} chunks) | Del: {delivered_chunks} "
                        f"| Wire: Rust={wire_telemetry['chunks_served']} ({wire_telemetry['bytes_delivered_mb']}MB, avg {wire_telemetry['avg_latency_ms']}ms) "
                        f"/ PyFallback={wire_telemetry['python_fallback_chunks']}"
                    )
                    if is_media_player and bytes_sent >= 2 * 1024 * 1024:
                        try:
                            from app.services.playback_tracker import playback_tracker
                            asyncio.create_task(playback_tracker.aupdate_byte_progress(
                                str(chat_id), message_id, clean_name, start + bytes_sent, file_size, chunk_size=chunk_size
                            ))
                        except Exception:
                            pass

            elapsed = max(0.01, time.time() - t0)
            avg_speed = (bytes_sent / (1024 * 1024)) / elapsed
            logger.info(
                f"STREAM [{clean_name}] Complete: sent {bytes_sent:,}/{length:,}B "
                f"in {elapsed:.2f}s ({avg_speed:.2f} MB/s)"
            )
            if is_media_player and bytes_sent >= 2 * 1024 * 1024:
                try:
                    from app.services.playback_tracker import playback_tracker
                    asyncio.create_task(playback_tracker.aupdate_byte_progress(
                        str(chat_id), message_id, clean_name, start + bytes_sent, file_size, chunk_size=chunk_size
                    ))
                except Exception:
                    pass

        except (ConnectionResetError, BrokenPipeError) as ce:
            elapsed = max(0.01, time.time() - t0)
            logger.info(
                f"STREAM [{clean_name}] Client seek/disconnect at byte {start + bytes_sent:,} "
                f"(delivered {bytes_sent:,}/{length:,}B in {elapsed:.2f}s)"
            )
            if is_media_player and bytes_sent >= 2 * 1024 * 1024:
                try:
                    from app.services.playback_tracker import playback_tracker
                    asyncio.create_task(playback_tracker.aupdate_byte_progress(
                        str(chat_id), message_id, clean_name, start + bytes_sent, file_size, chunk_size=chunk_size
                    ))
                except Exception:
                    pass
            raise
        except asyncio.CancelledError:
            elapsed = max(0.01, time.time() - t0)
            logger.info(
                f"STREAM [{clean_name}] Stream cancelled at byte {start + bytes_sent:,} "
                f"(delivered {bytes_sent:,}/{length:,}B in {elapsed:.2f}s)"
            )
            if is_media_player and bytes_sent >= 2 * 1024 * 1024:
                try:
                    from app.services.playback_tracker import playback_tracker
                    asyncio.create_task(playback_tracker.aupdate_byte_progress(
                        str(chat_id), message_id, clean_name, start + bytes_sent, file_size, chunk_size=chunk_size
                    ))
                except Exception:
                    pass
            raise
        finally:
            episode.active_subscribers.discard(request_id)
            stop_event.set()
            if media_key in self._active_stop_events:
                self._active_stop_events[media_key].discard(stop_event)
                if not self._active_stop_events[media_key]:
                    self._active_stop_events.pop(media_key, None)
            if producer_task and not producer_task.done():
                producer_task.cancel()
                try:
                    await asyncio.wait_for(asyncio.shield(producer_task), timeout=2.0)
                except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                    pass
                if not producer_task.done():
                    # Retain supervisor reference so GC never collects it pending (RC-002)
                    self._retained_cleanup_tasks.add(producer_task)
                    def _cleanup_done(t):
                        self._retained_cleanup_tasks.discard(t)
                        if not t.cancelled():
                            exc = t.exception()
                            if exc:
                                logger.debug(f"STREAM [{clean_name}] Supervised producer ended with: {exc}")
                    producer_task.add_done_callback(_cleanup_done)
                elif not producer_task.cancelled():
                    p_exc = producer_task.exception()
                    if p_exc:
                        logger.debug(f"STREAM [{clean_name}] Producer task error: {p_exc}")
            for wt in list(stream_wire_tasks):
                if getattr(wt, "_chunk_idx", 1) > 0 and not wt.done():
                    task_off = getattr(wt, "_chunk_offset", None)
                    is_cand = self.seek_episode_manager.is_candidate_window(norm_cid, message_id, task_off, chunk_size) if task_off is not None else False
                    is_retained = stream_buffer.is_chunk_retained(norm_cid, message_id, task_off) if task_off is not None else False
                    if not is_cand and not is_retained:
                        wt.cancel()
            if media_key in self._active_stream_wire_tasks:
                for wt in list(stream_wire_tasks):
                    self._active_stream_wire_tasks[media_key].discard(wt)
                if not self._active_stream_wire_tasks[media_key]:
                    self._active_stream_wire_tasks.pop(media_key, None)

    async def shutdown(self):
        """Cleanly aborts and cancels all in-flight streaming tasks and clears runtime state."""
        for media_key in list(self._active_stream_wire_tasks.keys()):
            norm_cid, orig_mid = media_key
            self.abort_message_stream(norm_cid, orig_mid, cancel_retained=True)
        for t in list(self._retained_cleanup_tasks):
            if not t.done():
                t.cancel()
        self._retained_cleanup_tasks.clear()
        self._active_stream_wire_tasks.clear()
        self._active_stop_events.clear()
        self._seek_generations.clear()
        self._active_read_positions.clear()
        self._in_flight_chunks.clear()
        self._chunk_task_registry.clear()


stream_engine = StreamEngine()
