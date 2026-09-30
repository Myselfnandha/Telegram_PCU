"""
VLC Ultra Fast-Start & Aggressive 8-Worker Streaming Engine.
Powers instant 200ms video playback and stutter-free 4K/1080p high-bitrate streaming from Telegram.
"""

import os
import time
import math
import asyncio
import logging
from typing import Dict, Tuple, Optional, AsyncGenerator, List, Set, Any
from telethon import utils, errors
from telethon.tl.functions.upload import GetFileRequest
from app.services.rate_governor import rate_governor
from app.services.stream_cache import stream_cache_service, BLOCK_SIZE

logger = logging.getLogger("fast_stream_engine")

# 1MB slices for ultra-responsive parallel downloads
SLICE_SIZE = 1024 * 1024  # 1 MB
MAX_MEM_CACHE_PER_MEDIA = 64 * 1024 * 1024  # 64 MB memory ring buffer per video (instant backward/forward seeks)
LOOKAHEAD_SLICES = 8  # 8 MB lookahead window ahead of playback
CONCURRENT_WORKERS = 3  # 3 balanced parallel MTProto downloaders (avoids server-side resets)


class MediaStreamSession:
    def __init__(self, chat_id: str, message_id: int, file_size: int, filename: str):
        self.chat_id = str(chat_id)
        self.message_id = message_id
        self.file_size = file_size
        self.filename = filename
        self.total_slices = max(1, (file_size + SLICE_SIZE - 1) // SLICE_SIZE)

        # In-memory slice cache: slice_idx -> bytes
        self.memory_slices: Dict[int, bytes] = {}
        self.slice_events: Dict[int, asyncio.Event] = {}
        self.downloading_slices: Set[int] = set()

        self.last_accessed_slice = 0
        self.last_active_time = time.time()
        self._lock = asyncio.Lock()

    def get_slice_range(self, slice_idx: int) -> Tuple[int, int]:
        start = slice_idx * SLICE_SIZE
        end = min(self.file_size - 1, (slice_idx + 1) * SLICE_SIZE - 1)
        return start, end

    def is_slice_available(self, slice_idx: int) -> bool:
        if slice_idx in self.memory_slices:
            return True
        # Check local discrete block cache on disk
        start, end = self.get_slice_range(slice_idx)
        block_idx = start // BLOCK_SIZE
        offset_in_block = start % BLOCK_SIZE
        slice_len = end - start + 1
        data = stream_cache_service.read_block_slice(
            self.chat_id, self.message_id, block_idx, offset_in_block, slice_len
        )
        if data and len(data) == slice_len:
            self.memory_slices[slice_idx] = data
            return True
        return False

    def store_slice(self, slice_idx: int, data: bytes):
        self.memory_slices[slice_idx] = data
        self.last_active_time = time.time()

        # Evict old memory slices if memory buffer exceeds 64MB limit
        max_slices = max(32, MAX_MEM_CACHE_PER_MEDIA // SLICE_SIZE)
        if len(self.memory_slices) > max_slices:
            # Preserve head (0..19) and tail (last 5) for instant seek, evict far slices beyond 32MB radius
            head_indices = set(range(min(20, self.total_slices)))
            tail_indices = set(range(max(0, self.total_slices - 5), self.total_slices))
            keep_indices = head_indices | tail_indices

            candidates = [
                idx
                for idx in self.memory_slices.keys()
                if idx not in keep_indices and abs(idx - self.last_accessed_slice) > 32
            ]
            for c in candidates[:16]:
                self.memory_slices.pop(c, None)

        # Notify any waiters
        event = self.slice_events.get(slice_idx)
        if event:
            event.set()


class FastStreamEngine:
    """
    Singleton High-Performance Video Stream Engine.
    Coordinates Head+Tail pre-buffering, 8-worker parallel lookahead downloads,
    and instantaneous RAM-based byte-range slicing.
    """

    def __init__(self):
        self._sessions: Dict[Tuple[str, int], MediaStreamSession] = {}
        self._lock = asyncio.Lock()
        
        # Start aggressive memory cleanup task
        async def cleanup_loop():
            import gc
            while True:
                await asyncio.sleep(30)
                now = time.time()
                stale_keys = []
                for key, session in list(self._sessions.items()):
                    if now - session.last_active_time > 60:  # 1 minute idle
                        stale_keys.append(key)
                if stale_keys:
                    for key in stale_keys:
                        self._sessions.pop(key, None)
                    gc.collect()
        
        # Fire and forget the cleanup loop
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(cleanup_loop())
        except RuntimeError:
            pass

    def get_or_create_session(
        self, chat_id: str, message_id: int, file_size: int, filename: str
    ) -> MediaStreamSession:
        key = (str(chat_id), message_id)
        if key not in self._sessions:
            self._sessions[key] = MediaStreamSession(chat_id, message_id, file_size, filename)
        session = self._sessions[key]
        session.last_active_time = time.time()
        return session

    async def _acquire_sender(self, client, dc_id: Optional[int]):
        """Borrows exported MTProto sender for specific DC, or returns primary client sender."""
        is_exported = False
        sender = getattr(client, "_sender", None)
        if hasattr(client, "_borrow_exported_sender") and dc_id:
            current_dc = getattr(getattr(client, "session", None), "dc_id", None)
            if current_dc and current_dc != dc_id:
                try:
                    sender = await client._borrow_exported_sender(dc_id)
                    is_exported = True
                except errors.DcIdInvalidError:
                    sender = getattr(client, "_sender", None)
                    is_exported = False
                except Exception as e:
                    logger.debug(f"DC sender borrow notice for DC {dc_id}: {e}")
                    sender = getattr(client, "_sender", None)
                    is_exported = False
        return sender, is_exported

    async def _release_sender(self, client, sender, is_exported: bool):
        """Safely returns borrowed exported sender to Telethon pool."""
        if is_exported and sender and hasattr(client, "_return_exported_sender"):
            try:
                await client._return_exported_sender(sender)
            except Exception:
                pass

    async def _fetch_single_chunk(
        self, client, sender, file_loc, offset: int, limit: int = 1024 * 1024
    ) -> bytes:
        """Executes a single GetFileRequest with RateGovernor pacing, sender re-acquisition on reset, and FloodWait backoff."""
        await rate_governor.wait_if_cooling_down()
        req = GetFileRequest(file_loc, offset=offset, limit=limit)
        active_sender = sender
        for attempt in range(5):
            await rate_governor.wait_if_cooling_down()
            try:
                if active_sender and hasattr(client, "_call"):
                    try:
                        res = await asyncio.wait_for(
                            client._call(active_sender, req),
                            timeout=12.0
                        )
                    except (ConnectionResetError, ConnectionError, BrokenPipeError, OSError, errors.SecurityError, asyncio.TimeoutError):
                        # Sender died — use primary client call as immediate fallback
                        active_sender = None
                        res = await asyncio.wait_for(client(req), timeout=12.0)
                else:
                    res = await asyncio.wait_for(client(req), timeout=12.0)
                if hasattr(res, "bytes"):
                    return res.bytes
                elif isinstance(res, (bytes, bytearray)):
                    return bytes(res)
                return b""
            except errors.FloodWaitError as fwe:
                wait_s = max(fwe.seconds, 1)
                rate_governor.report_flood_wait(wait_s, reason=f"MTProto 1MB Fetcher (attempt {attempt+1})")
                await rate_governor.wait_if_cooling_down()
            except asyncio.TimeoutError:
                logger.warning(f"Chunk offset {offset} timed out (attempt {attempt+1}/5)")
                active_sender = None  # Force re-acquire on next attempt
                await asyncio.sleep(0.3)
            except (ConnectionResetError, ConnectionError, BrokenPipeError, OSError) as ce:
                logger.warning(f"Connection reset on chunk offset {offset} (attempt {attempt+1}): {ce}")
                active_sender = None  # Force primary client on retry
                await asyncio.sleep(0.5)
            except errors.SecurityError as se:
                logger.warning(f"Session security desync on chunk offset {offset}: {se}")
                active_sender = None
                await asyncio.sleep(1.0)
            except Exception as e:
                logger.debug(f"Fetch chunk offset {offset} error (attempt {attempt+1}): {e}")
                if attempt == 4:
                    raise
                await asyncio.sleep(0.5)
        return b""

    async def stream_parallel_mtproto(
        self,
        client,
        message,
        start: int,
        length: int,
        session: Optional[MediaStreamSession] = None,
        max_workers: int = 2,
        window_size: int = 8,
    ) -> AsyncGenerator[bytes, None]:
        """
        High-throughput parallel MTProto streaming engine with bounded lookahead window.
        Uses 1MB aligned GetFileRequests, borrowed DC senders, and in-order concurrent worker prefetching.
        Populates session.memory_slices for zero-latency backward seeking.
        """
        if length <= 0:
            return

        file_info = None
        try:
            file_info = utils._get_file_info(message.media)
        except Exception:
            file_info = None

        if not file_info or not getattr(file_info, "location", None):
            # Fallback to standard iter_download if media structure is unconventional or mocked
            chunk_unit = 512 * 1024
            bytes_written = 0
            async for chunk in client.iter_download(
                message.media, offset=start, request_size=chunk_unit, chunk_size=chunk_unit
            ):
                if chunk:
                    remaining = length - bytes_written
                    if len(chunk) > remaining:
                        chunk = chunk[:remaining]
                    yield chunk
                    bytes_written += len(chunk)
                    if bytes_written >= length:
                        break
            return

        sender, is_exported = await self._acquire_sender(client, file_info.dc_id)
        file_loc = file_info.location

        align_unit = 1024 * 1024  # 1MB MTProto max request limit
        aligned_start = start - (start % align_unit)
        discard_bytes = start - aligned_start
        total_chunks = math.ceil((length + discard_bytes) / align_unit)

        if total_chunks == 0:
            await self._release_sender(client, sender, is_exported)
            return

        loop = asyncio.get_running_loop()
        results: Dict[int, asyncio.Future] = {i: loop.create_future() for i in range(total_chunks)}
        next_fetch_idx = 0
        current_consumed_idx = 0
        window_event = asyncio.Event()
        window_event.set()
        idx_lock = asyncio.Lock()

        # Bounded worker pool: prefetch up to `window_size` chunks ahead of current consumer
        async def prefetch_worker():
            nonlocal next_fetch_idx
            while True:
                # 1. Wait if window is full WITHOUT holding lock (with safety timeout to prevent deadlock)
                wait_loops = 0
                while next_fetch_idx - current_consumed_idx >= window_size:
                    window_event.clear()
                    try:
                        await asyncio.wait_for(window_event.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        wait_loops += 1
                        if wait_loops > 3:
                            return  # Prevent infinite deadlock
                        window_event.set()  # Self-heal: force re-check

                # 2. Acquire chunk index atomically
                async with idx_lock:
                    if next_fetch_idx >= total_chunks:
                        return
                    if next_fetch_idx - current_consumed_idx >= window_size:
                        continue
                    my_idx = next_fetch_idx
                    next_fetch_idx += 1

                chunk_offset = aligned_start + my_idx * align_unit
                data = b""
                try:
                    data = await self._fetch_single_chunk(client, sender, file_loc, chunk_offset, align_unit)
                except Exception as e:
                    logger.debug(f"Chunk {my_idx} fetch error: {e}")

                # 3. If primary MTProto fetch returned empty, retrieve full 1MB slice via iter_download fallback
                if not data:
                    try:
                        fallback_chunks = []
                        f_len = 0
                        async for c in client.iter_download(
                            message.media, offset=chunk_offset, request_size=512 * 1024, chunk_size=512 * 1024
                        ):
                            if c:
                                fallback_chunks.append(c)
                                f_len += len(c)
                                if f_len >= align_unit:
                                    break
                        if fallback_chunks:
                            data = b"".join(fallback_chunks)[:align_unit]
                    except Exception as fe:
                        logger.debug(f"Chunk {my_idx} fallback download error: {fe}")

                # 4. Cache in session memory for zero-latency backward seek
                if data and session:
                    s_idx = chunk_offset // SLICE_SIZE
                    session.store_slice(s_idx, data)

                if my_idx in results and not results[my_idx].done():
                    results[my_idx].set_result(data)

        worker_count = min(max_workers, total_chunks)
        workers = [asyncio.create_task(prefetch_worker()) for _ in range(worker_count)]

        bytes_written = 0
        try:
            for idx in range(total_chunks):
                current_consumed_idx = idx
                window_event.set()  # Wake prefetch workers to advance window

                # Consumer-side timeout: don't hang forever if worker crashed
                try:
                    chunk = await asyncio.wait_for(results[idx], timeout=15.0)
                except asyncio.TimeoutError:
                    logger.warning(f"Consumer timeout on chunk {idx}/{total_chunks}, attempting direct fetch")
                    # Emergency direct fetch for this chunk
                    chunk_offset = aligned_start + idx * align_unit
                    try:
                        chunk = await self._fetch_single_chunk(client, sender, file_loc, chunk_offset, align_unit)
                    except Exception:
                        chunk = b""
                    # If still empty, try iter_download as last resort
                    if not chunk:
                        try:
                            fb = []
                            async for c in client.iter_download(
                                message.media, offset=chunk_offset, request_size=512*1024, chunk_size=512*1024
                            ):
                                if c:
                                    fb.append(c)
                                    if sum(len(x) for x in fb) >= align_unit:
                                        break
                            if fb:
                                chunk = b"".join(fb)[:align_unit]
                        except Exception:
                            chunk = b""

                results.pop(idx, None)  # Free consumed future from memory

                if idx == 0 and discard_bytes > 0:
                    chunk = chunk[discard_bytes:]
                remaining = length - bytes_written
                if len(chunk) > remaining:
                    chunk = chunk[:remaining]
                if chunk:
                    yield chunk
                    bytes_written += len(chunk)
                if bytes_written >= length:
                    break
        finally:
            for w in workers:
                w.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            await self._release_sender(client, sender, is_exported)

    async def _download_range_bytes(
        self, client, message, offset: int, length: int
    ) -> bytes:
        """
        Safely downloads a byte range from Telegram MTProto.
        Uses single direct 1MB GetFileRequest when possible, falls back to iter_download.
        """
        if length <= 0:
            return b""
        await rate_governor.wait_if_cooling_down()

        # Attempt direct high-speed 1MB fetch via MTProto
        try:
            file_info = utils._get_file_info(message.media)
            if file_info and getattr(file_info, "location", None):
                align_unit = 1024 * 1024
                aligned_start = offset - (offset % align_unit)
                discard_front = offset - aligned_start
                total_needed = length + discard_front

                sender, is_exported = await self._acquire_sender(client, file_info.dc_id)
                try:
                    if total_needed <= align_unit:
                        raw = await self._fetch_single_chunk(client, sender, file_info.location, aligned_start, align_unit)
                        if discard_front > 0:
                            raw = raw[discard_front:]
                        return raw[:length]
                    else:
                        # Use sequential iter_download instead of stream_parallel_mtproto
                        # to avoid nested sender acquisition doubling connection load (RC-4)
                        chunks = []
                        fetched = 0
                        async for c in client.iter_download(
                            message.media, offset=offset, request_size=512*1024, chunk_size=512*1024
                        ):
                            if c:
                                chunks.append(c)
                                fetched += len(c)
                                if fetched >= length:
                                    break
                        return b"".join(chunks)[:length]
                finally:
                    await self._release_sender(client, sender, is_exported)
        except Exception as e:
            logger.debug(f"Direct MTProto fetch notice: {e}, falling back to iter_download")

        # Fallback to standard iter_download
        chunk_unit = 256 * 1024 if length <= 512 * 1024 else 512 * 1024
        aligned_offset = (offset // 4096) * 4096
        discard_front = offset - aligned_offset
        total_needed = length + discard_front
        chunk_count = (total_needed + chunk_unit - 1) // chunk_unit

        chunks = []
        c_downloaded = 0
        try:
            async for raw in client.iter_download(
                message.media,
                offset=aligned_offset,
                request_size=chunk_unit,
                chunk_size=chunk_unit,
            ):
                if raw:
                    chunks.append(raw)
                    c_downloaded += 1
                    if c_downloaded >= chunk_count:
                        break
        except errors.FloodWaitError as fwe:
            rate_governor.report_flood_wait(fwe.seconds, reason="FastStreamEngine FloodWait")
            return b""
        except Exception as e:
            logger.debug(f"Download range bytes fallback notice: {e}")
            return b""

        full_data = b"".join(chunks)
        if discard_front > 0:
            full_data = full_data[discard_front:]
        return full_data[:length]

    async def prebuffer_media(
        self, client, message, chat_id: str, message_id: int, file_size: int, filename: str
    ):
        """
        Instantly and concurrently pulls the first 20MB (head) + last 5MB (tail MOOV/EBML atom) into RAM.
        """
        session = self.get_or_create_session(chat_id, message_id, file_size, filename)

        # Target slices: Head (0..19) and Tail (last 5 slices)
        head_count = min(20, session.total_slices)
        head_slices = list(range(head_count))
        tail_slices = list(range(max(head_count, session.total_slices - 5), session.total_slices))
        target_slices = head_slices + tail_slices

        async def fetch_slice(s_idx: int):
            if session.is_slice_available(s_idx):
                return
            async with session._lock:
                if s_idx in session.downloading_slices:
                    return
                session.downloading_slices.add(s_idx)
                if s_idx not in session.slice_events:
                    session.slice_events[s_idx] = asyncio.Event()

            start, end = session.get_slice_range(s_idx)
            length = end - start + 1
            try:
                data = await self._download_range_bytes(client, message, start, length)
                if len(data) == length:
                    session.store_slice(s_idx, data)
            except Exception as e:
                logger.debug(f"Prebuffer slice {s_idx} failed: {e}")
            finally:
                session.downloading_slices.discard(s_idx)
                ev = session.slice_events.get(s_idx)
                if ev:
                    ev.set()

        sem = asyncio.Semaphore(CONCURRENT_WORKERS)

        async def sem_fetch(idx):
            async with sem:
                await fetch_slice(idx)

        tasks = [asyncio.create_task(sem_fetch(idx)) for idx in target_slices]
        # Prioritize first 4 head slices + last 2 tail slices for instant MOOV/index lookup
        priority_tasks = [t for i, t in enumerate(tasks) if i < 4 or i >= max(0, len(tasks) - 2)]
        if priority_tasks:
            await asyncio.gather(*priority_tasks, return_exceptions=True)
        logger.info(f"⚡ Prebuffer complete for {filename}: Head & Tail primed in RAM.")

    async def ensure_slice(
        self, client, message, session: MediaStreamSession, slice_idx: int
    ) -> Optional[bytes]:
        """Returns slice data from memory, disk cache, or active download."""
        if slice_idx >= session.total_slices:
            return None
        session.last_accessed_slice = slice_idx

        # No background lookahead — stream_parallel_mtproto already fetches progressively.
        # Spawning extra lookahead workers doubles connection load and causes Telegram resets.

        if session.is_slice_available(slice_idx):
            return session.memory_slices.get(slice_idx)

        # If already downloading, wait for it
        if slice_idx in session.downloading_slices:
            event = session.slice_events.setdefault(slice_idx, asyncio.Event())
            try:
                await asyncio.wait_for(event.wait(), timeout=8.0)
                return session.memory_slices.get(slice_idx)
            except asyncio.TimeoutError:
                pass

        # Otherwise fetch directly now
        start, end = session.get_slice_range(slice_idx)
        length = end - start + 1
        try:
            data = await self._download_range_bytes(client, message, start, length)
            if len(data) == length:
                session.store_slice(slice_idx, data)
                return data
        except Exception as e:
            logger.warning(f"Fetch slice {slice_idx} error: {e}")
        return None

    def _trigger_lookahead(
        self, client, message, session: MediaStreamSession, current_slice: int
    ):
        """Spawns debounced parallel lookahead workers streaming ahead of playback position."""
        if getattr(session, "_lookahead_active", False):
            return

        lookahead_end = min(session.total_slices, current_slice + LOOKAHEAD_SLICES)
        missing_slices = [
            i
            for i in range(current_slice + 1, lookahead_end)
            if not session.is_slice_available(i) and i not in session.downloading_slices
        ]
        if not missing_slices:
            return

        session._lookahead_active = True

        async def worker_pool():
            try:
                sem = asyncio.Semaphore(min(CONCURRENT_WORKERS, 4))

                async def fetch_one(s_idx):
                    async with sem:
                        if session.is_slice_available(s_idx):
                            return
                        session.downloading_slices.add(s_idx)
                        session.slice_events.setdefault(s_idx, asyncio.Event())
                        try:
                            start, end = session.get_slice_range(s_idx)
                            length = end - start + 1
                            data = await self._download_range_bytes(client, message, start, length)
                            if len(data) == length:
                                session.store_slice(s_idx, data)
                        except Exception as e:
                            logger.debug(f"Lookahead slice {s_idx} error: {e}")
                        finally:
                            session.downloading_slices.discard(s_idx)
                            ev = session.slice_events.get(s_idx)
                            if ev:
                                ev.set()

                await asyncio.gather(
                    *(fetch_one(idx) for idx in missing_slices[:LOOKAHEAD_SLICES]),
                    return_exceptions=True,
                )
            finally:
                session._lookahead_active = False

        asyncio.create_task(worker_pool())

    async def stream_byte_range(
        self,
        client,
        message,
        chat_id: str,
        message_id: int,
        file_size: int,
        filename: str,
        start_byte: int,
        end_byte: int,
    ) -> AsyncGenerator[bytes, None]:
        """
        Streams requested byte range to VLC with maximum efficiency:
        1. Instantly yields any already-buffered slices from memory.
        2. Streams remaining unbuffered bytes via the high-throughput parallel MTProto pipeline.
        """
        session = self.get_or_create_session(chat_id, message_id, file_size, filename)
        start_slice = start_byte // SLICE_SIZE
        end_slice = end_byte // SLICE_SIZE

        curr_offset = start_byte
        curr_slice = start_slice

        # 1. Serve consecutive in-memory slices first (instant 0ms response)
        while curr_slice <= end_slice and session.is_slice_available(curr_slice):
            slice_data = session.memory_slices.get(curr_slice)
            if not slice_data:
                break
            s_start, s_end = session.get_slice_range(curr_slice)
            in_slice_start = max(0, curr_offset - s_start)
            in_slice_end = min(len(slice_data), end_byte - s_start + 1)

            chunk = slice_data[in_slice_start:in_slice_end]
            if chunk:
                yield chunk
                curr_offset += len(chunk)
            if curr_offset > end_byte:
                return
            curr_slice += 1

        # 2. For unbuffered remainder, stream directly through bounded parallel MTProto engine
        rem_len = end_byte - curr_offset + 1
        if rem_len > 0:
            async for chunk in self.stream_parallel_mtproto(
                client=client,
                message=message,
                start=curr_offset,
                length=rem_len,
                session=session,
                max_workers=2,
            ):
                if chunk:
                    yield chunk
                    curr_offset += len(chunk)


# Global singleton fast stream engine
fast_stream_engine = FastStreamEngine()
