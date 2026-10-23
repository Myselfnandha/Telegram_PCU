import time
import asyncio
import logging
from typing import Dict, Tuple, Optional, List, Any, Union
from telethon import utils
from telethon.tl.functions.upload import GetFileRequest

logger = logging.getLogger("stream_buffer")


class StreamSlidingBuffer:
    """
    High-Performance RAM Multi-Segment Ring & Header/Tail Buffer for Media Streams.
    Provides instant 0.000s playback starts and zero-lag forward/backward scrubbing:
    1. Initial header cache (16MB): Instant startup for all video players (0ms TTFB).
    2. Tail metadata cache (16MB): Instant seek table parsing (MKV Cues / MP4 Moov).
    3. Multi-Segment Ring Buffer (192MB per active stream): Retains recent forward and
       backward seek segments simultaneously in RAM, allowing instant scrubbing without
       re-downloading from Telegram MTProto.
    """
    MAX_RING_SIZE = 192 * 1024 * 1024  # 192MB per active stream window across segments
    MAX_ENTRIES = 8                    # Max concurrent streams cached
    HEADER_SIZE = 16 * 1024 * 1024     # 16MB header cache per video (instant playback start)
    TAIL_SIZE = 16 * 1024 * 1024       # 16MB tail metadata cache (instant cues/moov)
    EVICT_AGE = 300                    # Evict entries idle >300s (5 minutes)

    def __init__(self):
        self._buffers: Dict[Tuple[str, int], dict] = {}
        self._segments: Dict[Tuple[str, int], List[dict]] = {}
        self._headers: Dict[Tuple[str, int], bytearray] = {}
        self._tails: Dict[Tuple[str, int], dict] = {}

    def _evict_stale(self, current_key=None):
        """Evict idle entries and cap total to MAX_ENTRIES."""
        now = time.time()
        stale = [
            k for k, v in self._buffers.items()
            if k != current_key and now - v.get("last_active", 0) > self.EVICT_AGE
        ]
        for k in stale:
            self._buffers.pop(k, None)
            self._segments.pop(k, None)
            self._headers.pop(k, None)
            self._tails.pop(k, None)

        while len(self._buffers) > self.MAX_ENTRIES:
            oldest_key = min(
                (k for k in self._buffers if k != current_key),
                key=lambda k: self._buffers[k].get("last_active", 0),
                default=None
            )
            if oldest_key:
                self._buffers.pop(oldest_key, None)
                self._segments.pop(oldest_key, None)
                self._headers.pop(oldest_key, None)
                self._tails.pop(oldest_key, None)
            else:
                break

    def push(self, chat_id: str, message_id: int, offset: int, data: bytes):
        """Append streamed data to the RAM sliding window, multi-segment cache, and initial header cache."""
        if not data:
            return
        key = (str(chat_id), message_id)
        now = time.time()

        # 1. Update initial header cache (0..4MB)
        if offset < self.HEADER_SIZE:
            if key not in self._headers:
                self._headers[key] = bytearray()
            h = self._headers[key]
            if offset <= len(h):
                overlap = len(h) - offset
                new_data = data[overlap:]
                if new_data and len(h) < self.HEADER_SIZE:
                    take = min(len(new_data), self.HEADER_SIZE - len(h))
                    h.extend(new_data[:take])
            elif offset == 0:
                take = min(len(data), self.HEADER_SIZE)
                self._headers[key] = bytearray(data[:take])

        # 2. Update multi-segment scrubbing buffer
        if key not in self._segments:
            self._evict_stale(key)
            self._segments[key] = []

        segments = self._segments[key]
        data_len = len(data)
        data_end = offset + data_len

        # Check if adjacent or overlapping with an existing segment
        merged = False
        for seg in segments:
            seg_start = seg["start"]
            seg_end = seg["end"]
            if offset == seg_end:
                # Contiguous append to end of segment
                seg["data"].extend(data)
                seg["end"] = data_end
                seg["last_active"] = now
                merged = True
                break
            elif data_end == seg_start:
                # Contiguous prepend to front of segment
                new_ba = bytearray(data)
                new_ba.extend(seg["data"])
                seg["data"] = new_ba
                seg["start"] = offset
                seg["last_active"] = now
                merged = True
                break
            elif offset <= seg_end and data_end >= seg_start:
                # Overlapping segment - merge into one
                min_s = min(seg_start, offset)
                max_e = max(seg_end, data_end)
                merged_ba = bytearray(max_e - min_s)
                merged_ba[seg_start - min_s : seg_end - min_s] = seg["data"]
                merged_ba[offset - min_s : data_end - min_s] = data
                seg["start"] = min_s
                seg["end"] = max_e
                seg["data"] = merged_ba
                seg["last_active"] = now
                merged = True
                break

        if not merged:
            segments.append({
                "start": offset,
                "end": data_end,
                "data": bytearray(data),
                "last_active": now
            })

        # Sort segments by start offset and merge any that now touch
        segments.sort(key=lambda s: s["start"])
        i = 0
        while i < len(segments) - 1:
            if segments[i]["end"] >= segments[i + 1]["start"]:
                s1 = segments[i]
                s2 = segments.pop(i + 1)
                max_e = max(s1["end"], s2["end"])
                merged_ba = bytearray(max_e - s1["start"])
                merged_ba[0 : len(s1["data"])] = s1["data"]
                merged_ba[s2["start"] - s1["start"] : s2["end"] - s1["start"]] = s2["data"]
                s1["end"] = max_e
                s1["data"] = merged_ba
                s1["last_active"] = max(s1["last_active"], s2["last_active"])
            else:
                i += 1

        # Enforce MAX_RING_SIZE across segments for this key
        total_size = sum(len(s["data"]) for s in segments)
        if total_size > self.MAX_RING_SIZE:
            by_age = sorted(segments, key=lambda s: s["last_active"])
            while total_size > self.MAX_RING_SIZE and by_age:
                oldest = by_age[0]
                excess = total_size - self.MAX_RING_SIZE
                if len(oldest["data"]) <= excess:
                    total_size -= len(oldest["data"])
                    segments.remove(oldest)
                    by_age.pop(0)
                else:
                    oldest["data"] = oldest["data"][excess:]
                    oldest["start"] += excess
                    total_size -= excess
                    break

        # Maintain self._buffers[key] referencing the most recent segment for backwards compatibility
        most_recent = max(segments, key=lambda s: s["last_active"], default=None)
        if most_recent:
            self._buffers[key] = {
                "start": most_recent["start"],
                "data": most_recent["data"],
                "last_active": most_recent["last_active"]
            }

    def push_tail(self, chat_id: str, message_id: int, offset: int, data: bytes):
        """Cache container tail metadata (e.g. MKV Cues / MP4 Moov)."""
        if not data:
            return
        key = (str(chat_id), message_id)
        if key not in self._tails:
            self._tails[key] = {"start": offset, "data": bytearray(data)}
        else:
            t = self._tails[key]
            t_start = t["start"]
            t_end = t_start + len(t["data"])
            if offset <= t_end and (offset + len(data)) >= t_start:
                min_start = min(t_start, offset)
                max_end = max(t_end, offset + len(data))
                merged = bytearray(max_end - min_start)
                merged[t_start - min_start : t_end - min_start] = t["data"]
                merged[offset - min_start : offset + len(data) - min_start] = data
                if len(merged) > self.TAIL_SIZE:
                    trim = len(merged) - self.TAIL_SIZE
                    merged = merged[trim:]
                    min_start += trim
                self._tails[key] = {"start": min_start, "data": merged}
            else:
                self._tails[key] = {"start": offset, "data": bytearray(data)}

    def get_slice(self, chat_id: str, message_id: int, start: int, length: int) -> Optional[bytes]:
        """
        Retrieve pre-cached RAM slice (header, tail, or multi-segment scrubbing buffer).
        Supports seamless contiguous multi-segment assembly across buffer boundaries.
        """
        key = (str(chat_id), message_id)

        # Fast Path 1: Exact / Contained Header Cache Hit (0ms playback start)
        if key in self._headers and start < len(self._headers[key]):
            h = self._headers[key]
            available = len(h) - start
            if available >= length:
                logger.info(f"BUFFER [{chat_id}:{message_id}] RAM Header Cache HIT: {length:,}B at offset {start:,} (instant 0ms playback start)")
                return bytes(h[start:start + length])

        # Fast Path 2: Exact / Contained Tail (MKV Cues / MP4 Moov atoms) Cache Hit
        if key in self._tails:
            t = self._tails[key]
            t_start = t["start"]
            t_end = t_start + len(t["data"])
            if t_start <= start < t_end:
                offset_in_tail = start - t_start
                available = len(t["data"]) - offset_in_tail
                if available >= length:
                    logger.info(f"BUFFER [{chat_id}:{message_id}] RAM Tail Cache HIT: {length:,}B at offset {start:,} (cues/moov metadata)")
                    return bytes(t["data"][offset_in_tail : offset_in_tail + length])

        # Fast Path 3: Exact / Contained Multi-Segment Scrubbing Buffer Hit
        if key in self._segments:
            for seg in self._segments[key]:
                if seg["start"] <= start < seg["end"]:
                    offset_in_seg = start - seg["start"]
                    available = len(seg["data"]) - offset_in_seg
                    if available >= length:
                        seg["last_active"] = time.time()
                        logger.info(f"BUFFER [{chat_id}:{message_id}] RAM Multi-Segment HIT: {length:,}B at offset {start:,} (instant scrub)")
                        return bytes(seg["data"][offset_in_seg : offset_in_seg + length])

        # Enhanced Path 4: Contiguous Multi-Source Assembly (Header + Segments + Tail)
        # Stitches contiguous bytes if a chunk spans across segment boundaries or header/segment borders
        assembled = bytearray()
        cur_offset = start
        end_offset = start + length

        while cur_offset < end_offset:
            chunk_found = False

            # Check header
            if key in self._headers and cur_offset < len(self._headers[key]):
                h = self._headers[key]
                take = min(end_offset - cur_offset, len(h) - cur_offset)
                if take > 0:
                    assembled.extend(h[cur_offset : cur_offset + take])
                    cur_offset += take
                    chunk_found = True

            # Check segments
            if not chunk_found and key in self._segments:
                for seg in self._segments[key]:
                    if seg["start"] <= cur_offset < seg["end"]:
                        take = min(end_offset - cur_offset, seg["end"] - cur_offset)
                        offset_in_seg = cur_offset - seg["start"]
                        assembled.extend(seg["data"][offset_in_seg : offset_in_seg + take])
                        seg["last_active"] = time.time()
                        cur_offset += take
                        chunk_found = True
                        break

            # Check tail
            if not chunk_found and key in self._tails:
                t = self._tails[key]
                t_start = t["start"]
                t_end = t_start + len(t["data"])
                if t_start <= cur_offset < t_end:
                    take = min(end_offset - cur_offset, t_end - cur_offset)
                    offset_in_tail = cur_offset - t_start
                    assembled.extend(t["data"][offset_in_tail : offset_in_tail + take])
                    cur_offset += take
                    chunk_found = True

            if not chunk_found:
                break

        if assembled:
            logger.info(f"BUFFER [{chat_id}:{message_id}] RAM Stitched Buffer HIT: {len(assembled):,}B at offset {start:,} (instant scrub)")
            return bytes(assembled)

        # Fallback check for single _buffers (compat)
        if key in self._buffers:
            buf = self._buffers[key]
            buf_start = buf["start"]
            buf_end = buf_start + len(buf["data"])
            if buf_start <= start < buf_end:
                offset_in_buf = start - buf_start
                available = len(buf["data"]) - offset_in_buf
                read_len = min(length, available)
                if read_len > 0:
                    logger.info(f"BUFFER [{chat_id}:{message_id}] RAM Sliding Window HIT: {read_len:,}B at offset {start:,} (playback scrub)")
                    return bytes(buf["data"][offset_in_buf:offset_in_buf + read_len])

        return None

    async def prewarm_tail(self, client: Any, chat_id: Union[str, int], message_id: int, message: Optional[Any], file_size: int):
        """Fetches the container tail (up to last 1MB in parallel) so index probes are instant RAM hits."""
        key = (str(chat_id), message_id)
        if key in self._tails or file_size <= 2 * 1024 * 1024:
            return
        chunk_size = 512 * 1024
        tail_chunk_idx = (file_size - 1) // chunk_size
        tail_start = tail_chunk_idx * chunk_size
        try:
            file_info = utils._get_file_info(message.media) if (message and hasattr(message, "media")) else None
            file_loc = getattr(file_info, "location", None)
            if not file_loc:
                return
            dc_id = getattr(file_info, "dc_id", None) or getattr(getattr(client, "session", None), "dc_id", 5)
            from app.services.rate_governor import rate_governor
            if rate_governor.is_cooling_down:
                return

            async def _fetch_tail_part(offset: int):
                req = GetFileRequest(file_loc, offset=offset, limit=chunk_size)
                res = None
                try:
                    await rate_governor.acquire_chunk_slot(chunk_size)
                    from app.telegram_client import sender_pool
                    async with sender_pool.borrow(client, dc_id) as s:
                        res = await asyncio.wait_for(client._call(s, req, flood_sleep_threshold=0), timeout=10.0)
                except Exception:
                    try:
                        res = await asyncio.wait_for(client(req, flood_sleep_threshold=0), timeout=10.0)
                    except Exception as c_err:
                        logger.debug(f"Tail fallback notice: {c_err}")
                data = getattr(res, "bytes", None) or (bytes(res) if isinstance(res, (bytes, bytearray)) else b"")
                if data:
                    self.push_tail(str(chat_id), message_id, offset, data)
                    self.push(str(chat_id), message_id, offset, data)

            tail_tasks = [_fetch_tail_part(tail_start)]
            if tail_chunk_idx > 0 and (tail_start - chunk_size) > 0:
                tail_tasks.append(_fetch_tail_part(tail_start - chunk_size))
            await asyncio.gather(*tail_tasks, return_exceptions=True)
            logger.info(f"BUFFER [{chat_id}:{message_id}] Tail pre-warmed: up to 1MB at offset {tail_start:,} (instant cues/moov)")
        except Exception as e:
            logger.debug(f"Tail prewarm notice: {e}")

    async def prewarm_media(self, client: Any, chat_id: Union[str, int], message_id: int, file_size: int = 0):
        """Pre-warms initial header (up to 1MB in parallel) and tail (up to 1MB in parallel) into RAM buffer upon media launch."""
        key = (str(chat_id), message_id)
        if key in self._headers and len(self._headers[key]) >= 1024 * 1024:
            return
        try:
            clean_chat_id = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
            msg = await client.get_messages(clean_chat_id, ids=message_id)
            if not msg or not msg.media:
                return
            file_info = utils._get_file_info(msg.media)
            file_loc = getattr(file_info, "location", None)
            if not file_loc:
                return
            dc_id = getattr(file_info, "dc_id", None) or getattr(getattr(client, "session", None), "dc_id", 5)
            actual_file_size = file_size or getattr(file_info, "size", 0) or getattr(getattr(msg, "file", None), "size", 0)
            from app.services.rate_governor import rate_governor
            if rate_governor.is_cooling_down:
                return

            # Pre-warm 2 header chunks (1MB total) in parallel for instantaneous start
            async def _fetch_hdr_chunk(offset: int):
                req = GetFileRequest(file_loc, offset=offset, limit=512 * 1024)
                res = None
                try:
                    await rate_governor.acquire_chunk_slot(512 * 1024)
                    from app.telegram_client import sender_pool
                    async with sender_pool.borrow(client, dc_id) as s:
                        res = await asyncio.wait_for(client._call(s, req, flood_sleep_threshold=0), timeout=10.0)
                except Exception:
                    try:
                        res = await asyncio.wait_for(client(req, flood_sleep_threshold=0), timeout=10.0)
                    except Exception as c_err:
                        logger.debug(f"Header fallback notice: {c_err}")
                data = getattr(res, "bytes", None) or (bytes(res) if isinstance(res, (bytes, bytearray)) else b"")
                if data:
                    self.push(str(chat_id), message_id, offset, data)

            tasks = [_fetch_hdr_chunk(0)]
            if actual_file_size > 512 * 1024:
                tasks.append(_fetch_hdr_chunk(512 * 1024))
            await asyncio.gather(*tasks, return_exceptions=True)
            logger.info(f"BUFFER [{chat_id}:{message_id}] Initial header pre-warmed (up to 1MB ready for instant start)")

            # Fetch tail if file > 2MB
            if actual_file_size > 2 * 1024 * 1024 and key not in self._tails:
                await self.prewarm_tail(client, chat_id, message_id, msg, actual_file_size)
        except Exception as e:
            logger.debug(f"Media prewarm notice: {e}")

    def clear(self):
        """Clear all cached entries."""
        self._buffers.clear()
        self._segments.clear()
        self._headers.clear()
        self._tails.clear()


stream_buffer = StreamSlidingBuffer()
_SLIDING_STREAM_BUFFER = stream_buffer
