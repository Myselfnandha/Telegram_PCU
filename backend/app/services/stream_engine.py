import asyncio
import time
import logging
from typing import Optional, Union, AsyncGenerator, Any, Dict, Tuple, Set
from telethon import utils, errors
from telethon.tl.functions.upload import GetFileRequest

from app.telegram_client import sender_pool
from app.services.rate_governor import rate_governor
from app.services.stream_buffer import stream_buffer

logger = logging.getLogger("stream_engine")

FILE_REF_ERRORS = (
    getattr(errors, "FileReferenceExpiredError", Exception),
    getattr(errors, "FileReferenceInvalidError", Exception),
    getattr(errors, "MediaEmptyError", Exception)
)


class StreamEngine:
    """
    Deep Module for High-Speed MTProto Streaming.
    Hides all chunk-alignment arithmetic, concurrent lookahead pipelining,
    multi-socket MTProto sender borrowing, rate governor permit budgeting,
    RAM prefix handoffs, and error resilience behind a single async generator interface.
    """

    def __init__(self):
        self._in_flight_chunks: Dict[Tuple[str, int, int], asyncio.Future] = {}
        self._chunk_lock: Optional[asyncio.Lock] = None
        self._active_stream_wire_tasks: Dict[Tuple[str, int], Set[asyncio.Task]] = {}
        self._active_stop_events: Dict[Tuple[str, int], Set[asyncio.Event]] = {}

    def _get_chunk_lock(self) -> asyncio.Lock:
        if self._chunk_lock is None:
            self._chunk_lock = asyncio.Lock()
        return self._chunk_lock

    def abort_message_stream(self, chat_id: Union[str, int], message_id: int):
        """
        Hard aborts all active MTProto chunk downloads, pipelined lookahead tasks,
        and unfulfilled chunk futures for a specific message stream.
        Called immediately on seek or fast-forward to release sender sockets and Rust permits.
        """
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
                    task.cancel()

        matching_keys = [k for k in list(self._in_flight_chunks.keys()) if str(k[0]) in candidate_cids and k[1] == int(message_id)]
        for k in matching_keys:
            fut = self._in_flight_chunks.pop(k, None)
            if fut and not fut.done():
                fut.cancel()

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
        external_stop_event: Optional[asyncio.Event] = None
    ) -> AsyncGenerator[bytes, None]:
        """
        Stream arbitrary byte ranges directly from Telegram MTProto with zero-latency
        RAM prefix delivery and lookahead pipelining.
        """
        t0 = time.time()
        bytes_sent = 0
        first_chunk_sent = False
        effective_start = start
        norm_cid = str(chat_id).lstrip("-").replace("100", "", 1)
        media_key = (norm_cid, int(message_id))
        stream_wire_tasks = set()

        if media_key not in self._active_stream_wire_tasks:
            self._active_stream_wire_tasks[media_key] = set()
        if media_key not in self._active_stop_events:
            self._active_stop_events[media_key] = set()
        if external_stop_event:
            self._active_stop_events[media_key].add(external_stop_event)

        if external_stop_event and external_stop_event.is_set():
            logger.debug(f"STREAM [{clean_name}] Aborted stream before start (superseded by seek)")
            return

        # 1. Fast Path: Full RAM prefix coverage (e.g. tiny probe or complete cached slice)
        if cached_prefix and len(cached_prefix) >= length:
            logger.info(f"STREAM [{clean_name}] Complete (RAM prefix only): {length:,}B delivered in {time.time() - t0:.3f}s (0ms TTFB)")
            yield cached_prefix[:length]
            return

        cached_prefix_len = len(cached_prefix) if cached_prefix else 0
        effective_start = start + cached_prefix_len
        remaining_length = length - cached_prefix_len

        # 2. Compute optimal chunk size:
        # - 128KB for tiny probes / container metadata tails (<= 256KB or within last 4MB)
        # - 1024KB for continuous media streaming (halves wire request frequency and prevents MTProto RPS flood)
        if length <= 256 * 1024 or (file_size > 0 and effective_start >= (file_size - 4 * 1024 * 1024)):
            chunk_size = 128 * 1024
        else:
            chunk_size = 1024 * 1024

        aligned_offset = (effective_start // chunk_size) * chunk_size
        discard_front = effective_start - aligned_offset
        total_chunks = (remaining_length + discard_front + chunk_size - 1) // chunk_size

        # Resolve media location and DC ID
        file_info = utils._get_file_info(message.media) if (message and hasattr(message, "media")) else None
        file_loc = getattr(file_info, "location", None)
        dc_id = getattr(file_info, "dc_id", None) or getattr(getattr(client, "session", None), "dc_id", 5)

        session_dc = getattr(getattr(client, "session", None), "dc_id", None)
        is_foreign = bool(dc_id and session_dc and dc_id != session_dc)

        from app.telegram_client import TelegramClientManager
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

        if len(active_clients) > 1 and get_fresh_message_fn:
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

        logger.info(
            f"STREAM [{clean_name}] Starting range: offset={effective_start:,}, "
            f"length={remaining_length:,}B ({remaining_length / (1024*1024):.2f}MB), "
            f"chunk_size={chunk_size // 1024}KB, total_chunks={total_chunks}, DC={dc_id}, "
            f"active_accounts={len(active_clients)}"
        )

        queue: asyncio.Queue = asyncio.Queue(maxsize=32 if is_media_player else 128)
        stop_event = external_stop_event or asyncio.Event()
        if media_key in self._active_stop_events:
            self._active_stop_events[media_key].add(stop_event)

        async def _download_chunk_from_mtproto(target_offset: int, target_size: int, worker_client: Any, is_emergency: bool, is_head_chunk: bool = False) -> bytes:
            nonlocal file_loc, message, active_clients
            if stop_event.is_set() and not is_head_chunk:
                raise asyncio.CancelledError("Stream aborted by seek")
            cached_data = stream_buffer.get_slice(norm_cid, message_id, target_offset, target_size)
            if cached_data and len(cached_data) == target_size:
                return cached_data

            target_loc = worker_file_locs.get(id(worker_client), file_loc)

            doc_id = getattr(target_loc, "id", None)
            access_hash = getattr(target_loc, "access_hash", None)
            file_ref = getattr(target_loc, "file_reference", None)

            if doc_id and access_hash and file_ref:
                for rust_attempt in range(2):
                    try:
                        from app.services.rust_bridge import rust_bridge
                        if await rust_bridge.is_healthy():
                            target_account = active_clients.index(worker_client) if (worker_client in active_clients and id(worker_client) in worker_file_locs) else 0
                            rust_data = await rust_bridge.stream_chunk(
                                dc_id=dc_id,
                                doc_id=doc_id,
                                access_hash=access_hash,
                                file_reference=file_ref,
                                offset=target_offset,
                                limit=target_size,
                                priority="high" if (is_media_player or is_emergency) else "bulk",
                                account_idx=target_account
                            )
                            if rust_data and len(rust_data) > 0:
                                stream_buffer.push(norm_cid, message_id, target_offset, rust_data)
                                if file_size > 0 and target_offset >= (file_size - 4 * 1024 * 1024):
                                    stream_buffer.push_tail(norm_cid, message_id, target_offset, rust_data)
                                return rust_data

                            if len(active_clients) > 1:
                                alt_idx = 1 if target_account == 0 else 0
                                alt_client = active_clients[alt_idx]
                                alt_loc = worker_file_locs.get(id(alt_client))
                                if alt_loc:
                                    alt_doc_id = getattr(alt_loc, "id", None)
                                    alt_access_hash = getattr(alt_loc, "access_hash", None)
                                    alt_file_ref = getattr(alt_loc, "file_reference", None)
                                    if alt_doc_id and alt_access_hash and alt_file_ref:
                                        rust_data = await rust_bridge.stream_chunk(
                                            dc_id=dc_id,
                                            doc_id=alt_doc_id,
                                            access_hash=alt_access_hash,
                                            file_reference=alt_file_ref,
                                            offset=target_offset,
                                            limit=target_size,
                                            priority="high" if (is_media_player or is_emergency) else "bulk",
                                            account_idx=alt_idx
                                        )
                                        if rust_data and len(rust_data) > 0:
                                            stream_buffer.push(norm_cid, message_id, target_offset, rust_data)
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

            req = GetFileRequest(target_loc, offset=target_offset, limit=target_size)

            file_ref_attempts = 0
            for attempt in range(6):
                if stop_event.is_set() and not is_head_chunk:
                    raise asyncio.CancelledError("Stream aborted by seek")
                try:
                    c_t0 = time.time()
                    await rate_governor.acquire_chunk_slot(target_size, is_emergency=is_emergency, is_unlimited=not is_media_player)
                    if stop_event.is_set() and not is_head_chunk:
                        raise asyncio.CancelledError("Stream aborted by seek")
                    chunk_timeout = 12.0 if is_media_player else 20.0
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
                raise asyncio.CancelledError()
            c_offset = aligned_offset + (c_idx * chunk_size)

            cached_data = stream_buffer.get_slice(norm_cid, message_id, c_offset, chunk_size)
            if cached_data and len(cached_data) == chunk_size:
                logger.info(f"STREAM [{clean_name}] Chunk {c_idx+1}/{total_chunks} at {c_offset:,}: RAM buffer hit (0ms)")
                return c_idx, cached_data

            chunk_key = (norm_cid, int(message_id), c_offset)
            lock = self._get_chunk_lock()
            async with lock:
                wire_fut = self._in_flight_chunks.get(chunk_key)
                if wire_fut is None or wire_fut.done():
                    loop = asyncio.get_running_loop()
                    wire_fut = loop.create_future()
                    self._in_flight_chunks[chunk_key] = wire_fut
                    is_lead = True
                else:
                    is_lead = False

            wt = None
            if is_lead:
                async def _background_wire_runner():
                    try:
                        if stop_event.is_set() and c_idx > 0:
                            if not wire_fut.done():
                                wire_fut.cancel()
                            return
                        data = await _download_chunk_from_mtproto(
                            c_offset, chunk_size, worker_client, is_emergency, is_head_chunk=(c_idx == 0)
                        )
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
                            if self._in_flight_chunks.get(chunk_key) is wire_fut:
                                self._in_flight_chunks.pop(chunk_key, None)

                wt = asyncio.create_task(_background_wire_runner())
                wt._chunk_idx = c_idx
                stream_wire_tasks.add(wt)
                if media_key in self._active_stream_wire_tasks:
                    self._active_stream_wire_tasks[media_key].add(wt)
                wt.add_done_callback(stream_wire_tasks.discard)
                wt.add_done_callback(lambda t: self._active_stream_wire_tasks.get(media_key, set()).discard(t))
            else:
                logger.info(
                    f"STREAM [{clean_name}] Chunk {c_idx+1}/{total_chunks} at {c_offset:,}: "
                    f"Joined in-flight wire download (0 duplicate MTProto requests)"
                )

            try:
                if c_idx == 0:
                    data = await asyncio.shield(wire_fut)
                else:
                    data = await asyncio.wait_for(asyncio.shield(wire_fut) if is_lead else wire_fut, timeout=20.0)
                return c_idx, data
            except asyncio.CancelledError:
                if getattr(wt, "_chunk_idx", 1) > 0:
                    if wt and not wt.done():
                        wt.cancel()
                    if not wire_fut.done():
                        wire_fut.cancel()
                raise
            except Exception as fut_err:
                if stop_event.is_set():
                    raise
                cached_data = stream_buffer.get_slice(norm_cid, message_id, c_offset, chunk_size)
                if cached_data and len(cached_data) == chunk_size:
                    return c_idx, cached_data
                data = await _download_chunk_from_mtproto(
                    c_offset, chunk_size, worker_client, is_emergency, is_head_chunk=(c_idx == 0)
                )
                return c_idx, data

        last_progress_log = time.time()
        delivered_chunks = 0

        producer_task = None
        if remaining_length > 0 and total_chunks > 0:
            async def pipelined_chunk_producer():
                from collections import defaultdict
                chunk_retries = defaultdict(int)

                is_multi_account = len(active_clients) >= 2
                is_premium_rate = rate_governor.current_rate_limit_mbs > 15.0
                if is_media_player:
                    max_in_flight = 8 if (is_premium_rate or is_multi_account) else 6
                    max_lookahead = 32 if (is_premium_rate or is_multi_account) else 24
                else:
                    max_in_flight = 12 if is_multi_account else 6
                    max_lookahead = 48 if is_multi_account else 24

                in_flight = {}
                next_to_fetch = 0
                next_to_deliver = 0
                buffered = {}

                try:
                    while next_to_deliver < total_chunks and not stop_event.is_set():
                        # 1. Drain all sequential ready chunks from buffered into queue
                        while next_to_deliver in buffered and not stop_event.is_set():
                            data = buffered.pop(next_to_deliver)
                            await queue.put(data)
                            next_to_deliver += 1

                        if next_to_deliver >= total_chunks:
                            break

                        # 2. Adaptive Buffer-Aware Concurrency:
                        runway = queue.qsize() + len(buffered)
                        if is_media_player:
                            if next_to_deliver == 0:
                                # Dedicated focus on seek target chunk(s) for immediate sub-second TTFB
                                allowed_in_flight = 2 if is_multi_account else 1
                            elif runway < 8:
                                allowed_in_flight = max(6, max_in_flight)
                            elif runway < 16:
                                allowed_in_flight = max(4, max_in_flight - 1)
                            else:
                                allowed_in_flight = 3
                        else:
                            if next_to_deliver == 0:
                                allowed_in_flight = min(2, max_in_flight)
                            elif next_to_deliver == 1:
                                allowed_in_flight = min(4, max_in_flight)
                            elif runway >= 16:
                                allowed_in_flight = max(4, max_in_flight // 2)
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
                            assigned_client = healthy_clients[next_to_fetch % len(healthy_clients)]
                            is_emergency = (is_media_player and runway < 4) or (not is_media_player and next_to_deliver == 0)
                            t = asyncio.create_task(fetch_one_chunk(next_to_fetch, assigned_client, is_emergency=is_emergency))
                            in_flight[t] = next_to_fetch
                            next_to_fetch += 1

                        # 3. If tasks are downloading, wait for at least one to finish
                        if in_flight:
                            done, _ = await asyncio.wait(list(in_flight.keys()), return_when=asyncio.FIRST_COMPLETED)
                            for d in done:
                                c_idx = in_flight.pop(d, None)
                                try:
                                    res = d.result()
                                    if res is None:
                                        raise RuntimeError(f"fetch_one_chunk({c_idx}) returned None")
                                    _, data = res
                                    buffered[c_idx] = data
                                except asyncio.CancelledError:
                                    if stop_event.is_set():
                                        break
                                    cached_data = stream_buffer.get_slice(norm_cid, message_id, aligned_offset + (c_idx * chunk_size), chunk_size)
                                    if cached_data and len(cached_data) == chunk_size:
                                        buffered[c_idx] = cached_data
                                        continue
                                    chunk_retries[c_idx] += 1
                                    if chunk_retries[c_idx] >= 6:
                                        stop_event.set()
                                        break
                                    await asyncio.sleep(0.1)
                                    healthy_clients = [c for c in active_clients if not rate_governor.is_client_cooling_down(c)]
                                    retry_client = healthy_clients[0] if healthy_clients else client
                                    t = asyncio.create_task(fetch_one_chunk(c_idx, retry_client, is_emergency=True))
                                    in_flight[t] = c_idx
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
                                        retry_client = healthy_clients[0] if healthy_clients else client
                                        t = asyncio.create_task(fetch_one_chunk(c_idx, retry_client, is_emergency=True))
                                        in_flight[t] = c_idx
                        else:
                            # 4. If no tasks in flight:
                            if next_to_fetch >= total_chunks:
                                # All chunks fetched from wire; wait until remaining buffered chunks drain
                                if next_to_deliver not in buffered:
                                    break
                            else:
                                # Lookahead window is completely full and waiting for player to consume from queue
                                if next_to_deliver in buffered:
                                    continue
                                await asyncio.sleep(0.05)

                    await queue.put(None)
                except asyncio.CancelledError:
                    logger.debug(f"STREAM [{clean_name}] Pipelined producer cancelled")
                except Exception as e:
                    logger.debug(f"STREAM [{clean_name}] Producer notice: {e}")
                finally:
                    if in_flight:
                        for t in list(in_flight.keys()):
                            if not t.done():
                                t.cancel()
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
                chunk = await queue.get()
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
                    pct = (bytes_sent * 100.0 / length) if length > 0 else 0
                    q_len = queue.qsize()
                    buffered_mb = (q_len * chunk_size) / (1024 * 1024)
                    logger.info(
                        f"STREAM [{clean_name}] Progress: {bytes_sent:,}/{length:,}B "
                        f"({pct:.1f}%) | Player Rate: {playback_speed:.2f} MB/s | Buffer Runway: ~{buffered_mb:.1f}MB ({q_len} chunks in RAM) | {delivered_chunks} delivered"
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
            stop_event.set()
            if media_key in self._active_stop_events:
                self._active_stop_events[media_key].discard(stop_event)
                if not self._active_stop_events[media_key]:
                    self._active_stop_events.pop(media_key, None)
            if producer_task and not producer_task.done():
                producer_task.cancel()
                try:
                    await asyncio.wait_for(producer_task, timeout=0.8)
                except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                    pass
            for wt in list(stream_wire_tasks):
                if getattr(wt, "_chunk_idx", 1) > 0 and not wt.done():
                    wt.cancel()
            if media_key in self._active_stream_wire_tasks:
                for wt in list(stream_wire_tasks):
                    self._active_stream_wire_tasks[media_key].discard(wt)
                if not self._active_stream_wire_tasks[media_key]:
                    self._active_stream_wire_tasks.pop(media_key, None)


stream_engine = StreamEngine()
