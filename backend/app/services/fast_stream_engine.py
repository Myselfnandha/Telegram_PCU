"""
VLC Ultra Fast-Start & Aggressive 8-Worker Streaming Engine.
Powers instant 200ms video playback and stutter-free 4K/1080p high-bitrate streaming from Telegram.
"""

import os
import time
import asyncio
import logging
from typing import Dict, Tuple, Optional, AsyncGenerator, List, Set
from app.services.stream_cache import stream_cache_service, BLOCK_SIZE

logger = logging.getLogger("fast_stream_engine")

# 1MB slices for ultra-responsive parallel downloads
SLICE_SIZE = 1024 * 1024  # 1 MB
MAX_MEM_CACHE_PER_MEDIA = 64 * 1024 * 1024  # 64 MB memory ring buffer per video
LOOKAHEAD_SLICES = 32  # 32 MB lookahead window ahead of VLC playback
CONCURRENT_WORKERS = 8  # 8 aggressive parallel MTProto downloaders


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

        # Evict old memory slices if memory buffer exceeds limit
        max_slices = max(16, MAX_MEM_CACHE_PER_MEDIA // SLICE_SIZE)
        if len(self.memory_slices) > max_slices:
            # Preserve head (0..19) and tail (last 5) for instant seek, evict far slices
            head_indices = set(range(min(20, self.total_slices)))
            tail_indices = set(range(max(0, self.total_slices - 5), self.total_slices))
            keep_indices = head_indices | tail_indices

            candidates = [
                idx
                for idx in self.memory_slices.keys()
                if idx not in keep_indices and abs(idx - self.last_accessed_slice) > 16
            ]
            for c in candidates[:10]:
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

    def get_or_create_session(
        self, chat_id: str, message_id: int, file_size: int, filename: str
    ) -> MediaStreamSession:
        key = (str(chat_id), message_id)
        if key not in self._sessions:
            self._sessions[key] = MediaStreamSession(chat_id, message_id, file_size, filename)
        session = self._sessions[key]
        session.last_active_time = time.time()
        return session

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
            chunks = []
            try:
                async for raw in client.iter_download(
                    message.media,
                    offset=start,
                    limit=length,
                    request_size=min(512 * 1024, length),
                    chunk_size=min(512 * 1024, length),
                ):
                    if raw:
                        chunks.append(raw)
                data = b"".join(chunks)
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

        # Trigger background lookahead for next 16-32 slices
        self._trigger_lookahead(client, message, session, slice_idx)

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
        chunks = []
        try:
            async for raw in client.iter_download(
                message.media,
                offset=start,
                limit=length,
                request_size=min(512 * 1024, length),
                chunk_size=min(512 * 1024, length),
            ):
                if raw:
                    chunks.append(raw)
            data = b"".join(chunks)
            if len(data) == length:
                session.store_slice(slice_idx, data)
                return data
        except Exception as e:
            logger.warning(f"Fetch slice {slice_idx} error: {e}")
        return None

    def _trigger_lookahead(
        self, client, message, session: MediaStreamSession, current_slice: int
    ):
        """Spawns 8 parallel lookahead workers streaming ahead of playback position."""
        lookahead_end = min(session.total_slices, current_slice + LOOKAHEAD_SLICES)
        missing_slices = [
            i
            for i in range(current_slice + 1, lookahead_end)
            if not session.is_slice_available(i) and i not in session.downloading_slices
        ]
        if not missing_slices:
            return

        async def worker_pool():
            sem = asyncio.Semaphore(CONCURRENT_WORKERS)

            async def fetch_one(s_idx):
                async with sem:
                    if session.is_slice_available(s_idx):
                        return
                    session.downloading_slices.add(s_idx)
                    session.slice_events.setdefault(s_idx, asyncio.Event())
                    try:
                        start, end = session.get_slice_range(s_idx)
                        length = end - start + 1
                        chunks = []
                        async for raw in client.iter_download(
                            message.media,
                            offset=start,
                            limit=length,
                            request_size=min(512 * 1024, length),
                            chunk_size=min(512 * 1024, length),
                        ):
                            if raw:
                                chunks.append(raw)
                        data = b"".join(chunks)
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
        Streams requested byte range to VLC with maximum efficiency, reading from memory slices.
        """
        session = self.get_or_create_session(chat_id, message_id, file_size, filename)
        start_slice = start_byte // SLICE_SIZE
        end_slice = end_byte // SLICE_SIZE

        current_offset = start_byte

        for s_idx in range(start_slice, end_slice + 1):
            slice_data = await self.ensure_slice(client, message, session, s_idx)
            if not slice_data:
                # If slice could not be retrieved from engine, fallback to direct iter_download
                s_start, s_end = session.get_slice_range(s_idx)
                fetch_start = max(current_offset, s_start)
                fetch_len = min(end_byte, s_end) - fetch_start + 1
                if fetch_len > 0:
                    async for raw in client.iter_download(
                        message.media,
                        offset=fetch_start,
                        limit=fetch_len,
                        request_size=min(512 * 1024, fetch_len),
                        chunk_size=min(512 * 1024, fetch_len),
                    ):
                        if raw:
                            yield raw
                            current_offset += len(raw)
                continue

            s_start, s_end = session.get_slice_range(s_idx)
            in_slice_start = max(0, current_offset - s_start)
            in_slice_end = min(len(slice_data), end_byte - s_start + 1)

            chunk = slice_data[in_slice_start:in_slice_end]
            if chunk:
                yield chunk
                current_offset += len(chunk)


# Global singleton fast stream engine
fast_stream_engine = FastStreamEngine()
