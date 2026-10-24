import os
import re
import time
import logging
import asyncio
import email.utils
from collections import defaultdict
from typing import Optional, Dict, Any, List, Union, cast, Tuple, Set, DefaultDict
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from starlette.requests import ClientDisconnect
from app.telegram_client import TelegramClientManager, sender_pool
from app.services.sniffer_service import auto_rename, sniffer_service
from app.services.manager_detector import trigger_manager, detect_managers

logger = logging.getLogger("proxy_route")

router = APIRouter(tags=["proxy"])

_MESSAGE_CACHE = {}
from telethon import errors, utils
from telethon.tl.functions.upload import GetFileRequest

FILE_REF_ERRORS = (
    errors.FileReferenceExpiredError,
    errors.FileReferenceInvalidError,
    errors.FileReferenceEmptyError,
    errors.FilerefUpgradeNeededError,
)
from app.services.cinema_catalog import cinema_catalog, get_streamable_message
_STREAM_MESSAGE_CACHE = cinema_catalog._message_cache
from app.services.rate_governor import rate_governor
from app.services.stream_cache import stream_cache_service, BLOCK_SIZE
from app.services.fast_stream_engine import fast_stream_engine
from app.services.subtitle_service import subtitle_service
from app.services.playback_tracker import playback_tracker
from app.services.stream_saver import stream_saver_service
from app.routes.history import get_cinema_cached_videos_db, save_cinema_cached_videos_db


from app.services.stream_buffer import stream_buffer, _SLIDING_STREAM_BUFFER, StreamSlidingBuffer
from app.services.stream_engine import stream_engine
from app.services.player_launcher import player_launcher, get_desktop_env
_ACTIVE_STREAM_CLIENTS = 0
_ACTIVE_STREAM_PRODUCERS: Dict[Tuple[str, int], asyncio.Task] = {}
_ACTIVE_STREAM_TASKS: Dict[Tuple[str, int], Tuple[asyncio.Task, int]] = {}
_ACTIVE_MEDIA_CONNS: DefaultDict[Tuple[str, int], Set[asyncio.Task]] = defaultdict(set)
_ACTIVE_STREAM_ITERS: Dict[Tuple[str, int], Any] = {}
_ACTIVE_PREFETCH_TASKS: Dict[Tuple[str, int], asyncio.Task] = {}
_ACTIVE_PLAYER_PROC: Optional[Any] = None


class StreamIteratorHandle:
    """Atomic cancellation handle for pipelined MTProto streamer & fallback iterators."""
    def __init__(self, stop_event: asyncio.Event, task: Optional[asyncio.Task] = None, it: Optional[Any] = None):
        self.stop_event = stop_event
        self.task = task
        self.it = it

    async def close(self):
        self.stop_event.set()
        if self.task and not self.task.done():
            self.task.cancel()
        if self.it:
            try:
                if hasattr(self.it, "aclose"):
                    await self.it.aclose()
                elif hasattr(self.it, "close"):
                    res = self.it.close()
                    if asyncio.iscoroutine(res):
                        await res
            except Exception:
                pass


async def _prefetch_non_watched_blocks(client, message, chat_id: str, message_id: int, file_size: int, filename: str, start_block: int = 0):
    """Disabled during playback: pure streaming without background disk downloading."""
    return


async def prewarm_media_stream(client, chat_id: Union[str, int], message_id: int, message: Any):
    """
    Asynchronously pre-warms the media file header (0..512KB) and tail (cues/moov) into RAM
    concurrently so that VLC's initial probing requests hit memory with <0.001s latency.
    """
    try:
        if not message or not getattr(message, "file", None):
            return
        f_size = getattr(message.file, "size", 0)
        key = (str(chat_id), int(message_id))

        # 1. Prewarm tail if not already cached and file > 2MB
        if f_size > 2 * 1024 * 1024 and key not in _SLIDING_STREAM_BUFFER._tails:
            asyncio.create_task(_SLIDING_STREAM_BUFFER.prewarm_tail(client, chat_id, message_id, message, f_size))

        # 2. Prewarm header chunk 0 (512KB) into RAM buffer if not already in headers
        if key not in _SLIDING_STREAM_BUFFER._headers:
            chunk_size = 512 * 1024
            file_info = utils._get_file_info(message.media) if hasattr(message, "media") else None
            file_loc = getattr(file_info, "location", None)
            dc_id = getattr(file_info, "dc_id", None) or getattr(getattr(client, "session", None), "dc_id", 5)
            if file_loc:
                req = GetFileRequest(file_loc, offset=0, limit=chunk_size)
                res = None
                try:
                    async with sender_pool.borrow(client, dc_id) as s:
                        res = await asyncio.wait_for(client._call(s, req, flood_sleep_threshold=0), timeout=6.0)
                except Exception:
                    res = await asyncio.wait_for(client(req, flood_sleep_threshold=0), timeout=6.0)
                data = getattr(res, "bytes", None) or (bytes(res) if isinstance(res, (bytes, bytearray)) else b"")
                if data:
                    _SLIDING_STREAM_BUFFER.push(str(chat_id), int(message_id), 0, data)
                    fn = getattr(message.file, "name", "media")
                    logger.info(f"STREAM [{fn}] Prewarmed {len(data):,}B header into RAM for instant VLC playback.")
    except Exception as e:
        logger.debug(f"Media prewarm notice: {e}")


def compute_dynamic_align_unit(file_size: int, start: int, length: int, user_agent: str = "") -> int:
    """
    Adaptive MTProto chunk alignment strategy:
    - 128 KB for header probing / tiny seeks (<50ms response)
    - 256 KB for fast-forward scrubbing & mid-stream seeks
    - 512 KB for standard continuous video streaming
    - 1024 KB for large multi-part download manager requests
    """
    ua = user_agent.lower()
    is_download_manager = any(dm in ua for dm in ("fdm", "aria2", "idm", "wget", "curl", "freedownloadmanager", "libcurl", "persepolis", "kget", "neatdm"))

    if length <= 2 * 1024 * 1024:
        return 128 * 1024  # 128 KB for ultra-fast <50ms header/keyframe response

    if start > 0 and not is_download_manager:
        if length <= 8 * 1024 * 1024:
            return 256 * 1024  # 256 KB for instant keyframe scrubbing
        return 512 * 1024

    if is_download_manager and length > 16 * 1024 * 1024:
        return 1024 * 1024  # 1 MB for maximum bulk download throughput

    return 512 * 1024


_THUMB_CACHE_DIR = os.path.expanduser("~/.cache/tg_power_suite/thumbs")
_PREVIEW_CACHE_DIR = os.path.expanduser("~/.cache/tg_power_suite/previews")
os.makedirs(_THUMB_CACHE_DIR, exist_ok=True)
os.makedirs(_PREVIEW_CACHE_DIR, exist_ok=True)
_THUMB_SEMAPHORE = asyncio.Semaphore(4)
DEFAULT_VIDEO_PLACEHOLDER_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 180" width="320" height="180">'
    b'<rect width="320" height="180" fill="#0f1117"/>'
    b'<circle cx="160" cy="90" r="26" fill="#1e2230" stroke="#3b82f6" stroke-width="2"/>'
    b'<polygon points="153,78 173,90 153,102" fill="#3b82f6"/>'
    b'</svg>'
)


@router.get("/dl/{chat_id}/{message_id}/thumb")
@router.get("/stream/{chat_id}/{message_id}/thumb")
async def get_media_thumbnail(chat_id: str, message_id: int):
    """Fetches and caches the preview thumbnail for a Telegram video/document with multi-alias lookups."""
    clean_chat_id: Union[int, str] = chat_id
    if chat_id != "me":
        try:
            clean_chat_id = int(chat_id)
        except ValueError:
            clean_chat_id = chat_id

    # 1. Resolve candidate cache filenames across all Telegram ID formats
    my_info = TelegramClientManager.get_cached_me() or {}
    my_id = my_info.get("id")

    candidates = [
        f"{chat_id}_{message_id}.jpg",
        f"{clean_chat_id}_{message_id}.jpg"
    ]
    if chat_id == "me" or (my_id and str(clean_chat_id).lstrip("-") == str(my_id)):
        candidates.append(f"me_{message_id}.jpg")
        if my_id:
            candidates.append(f"{my_id}_{message_id}.jpg")

    str_cid = str(clean_chat_id)
    if str_cid.startswith("-100"):
        candidates.append(f"{str_cid[4:]}_{message_id}.jpg")
    elif str_cid.lstrip("-").isdigit():
        candidates.append(f"-100{str_cid}_{message_id}.jpg")

    unique_candidates = list(dict.fromkeys(candidates))

    # 2. Check disk cache first (instant <1ms response)
    for fname in unique_candidates:
        c_path = os.path.join(_THUMB_CACHE_DIR, fname)
        if os.path.exists(c_path) and os.path.getsize(c_path) > 0:
            with open(c_path, "rb") as f:
                return Response(
                    content=f.read(),
                    media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=86400, immutable"}
                )

    client = await TelegramClientManager.get_client()
    if not client or not client.is_connected():
        return Response(
            content=DEFAULT_VIDEO_PLACEHOLDER_SVG,
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=60"}
        )

    try:
        # Use get_streamable_message for robust entity resolution & channel -100 auto-healing
        message = await get_streamable_message(client, clean_chat_id, message_id)
        if not message or not getattr(message, "media", None):
            return Response(
                content=DEFAULT_VIDEO_PLACEHOLDER_SVG,
                media_type="image/svg+xml",
                headers={"Cache-Control": "public, max-age=3600"}
            )

        thumb_bytes = None
        doc = getattr(message.media, "document", None) or (message.media if hasattr(message.media, "thumbs") else None)
        thumbs = getattr(doc, "thumbs", None) or getattr(message.media, "thumbs", None) or []

        # Fast Path 1: Check inlined PhotoStrippedSize / PhotoCachedSize (0ms instant JPEG decode)
        from telethon.tl.types import PhotoStrippedSize, PhotoCachedSize
        for t in thumbs:
            if isinstance(t, PhotoStrippedSize) and getattr(t, "bytes", None):
                try:
                    thumb_bytes = utils.stripped_photo_to_jpg(t.bytes)
                    if thumb_bytes:
                        break
                except Exception:
                    pass
            elif isinstance(t, PhotoCachedSize) and getattr(t, "bytes", None):
                thumb_bytes = t.bytes
                if thumb_bytes:
                    break

        # Fast Path 2: Download medium PhotoSize through client.download_media throttled by semaphore
        if not thumb_bytes:
            async with _THUMB_SEMAPHORE:
                try:
                    thumb_bytes = await client.download_media(message, thumb=0, file=bytes)
                except Exception:
                    try:
                        thumb_bytes = await client.download_media(message, thumb=-1, file=bytes)
                    except Exception:
                        pass

        if thumb_bytes:
            # Save to disk cache under all alias keys so subsequent lookups hit in 0ms
            for fname in unique_candidates:
                try:
                    with open(os.path.join(_THUMB_CACHE_DIR, fname), "wb") as f:
                        f.write(thumb_bytes)
                except Exception:
                    pass

            return Response(
                content=thumb_bytes,
                media_type="image/jpeg",
                headers={"Cache-Control": "public, max-age=86400, immutable"}
            )

        # Fallback to sleek SVG video placeholder
        return Response(
            content=DEFAULT_VIDEO_PLACEHOLDER_SVG,
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=3600"}
        )
    except Exception as e:
        logger.debug(f"Thumbnail notice for {chat_id}/{message_id}: {e}")
        return Response(
            content=DEFAULT_VIDEO_PLACEHOLDER_SVG,
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=300"}
        )


@router.api_route("/dl/{chat_id}/{message_id}", methods=["GET", "HEAD"])
@router.api_route("/dl/{chat_id}/{message_id}/{filename}", methods=["GET", "HEAD"])
async def handle_proxy_download(
    chat_id: str,
    message_id: int,
    request: Request,
    filename: Optional[str] = None
):
    if filename == "thumb":
        return await get_media_thumbnail(chat_id, message_id)

    """
    Streaming proxy endpoint that fetches data dynamically from Telegram MTProto
    and pipes it directly to the client (VLC, browser, or download manager).
    Supports HTTP Range requests (seeking/resuming) and real-time caching.
    """
    try:
        # Give client up to 10s to reconnect if network interface is switching (e.g. WiFi to Hotspot/Cellular)
        connected = await TelegramClientManager.ensure_connected(timeout=10.0)
        client = await TelegramClientManager.get_client()
        is_auth = await TelegramClientManager.is_authorized()
        if not is_auth:
            raise HTTPException(status_code=401, detail="Telegram client not authorized.")
        if not connected or not client or not client.is_connected():
            raise HTTPException(status_code=503, detail="Telegram network connection temporarily unavailable. Please retry.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Could not get Telegram client: {e}")
        raise HTTPException(status_code=500, detail=f"Telegram client error: {str(e)}")

    clean_chat_id: Union[int, str] = chat_id
    if chat_id != "me":
        try:
            clean_chat_id = int(chat_id)
        except ValueError:
            clean_chat_id = chat_id

    message = await get_streamable_message(client, clean_chat_id, message_id)
    if not message or not message.media or not hasattr(message, "file") or not message.file:
        raise HTTPException(status_code=404, detail="Message not found or contains no media.")

    file_size = int(message.file.size)
    file_info = utils._get_file_info(message.media) if hasattr(message, "media") else None
    media_dc_id = getattr(file_info, "dc_id", None)
    session_dc = getattr(getattr(client, "session", None), "dc_id", None)
    if media_dc_id and session_dc and media_dc_id != session_dc and hasattr(client, "_borrow_exported_sender"):
        borrowed = getattr(client, "_borrowed_senders", {}).get(media_dc_id)
        if not borrowed or not borrowed[1] or not borrowed[1].is_connected():
            asyncio.create_task(client._borrow_exported_sender(media_dc_id))

    raw_name = message.file.name if message.file.name else f"tg_media_{message_id}.bin"
    raw_name = "".join([c for c in raw_name if (c.isalnum() or c in " .-_()")]).strip()
    import urllib.parse
    real_name = auto_rename(raw_name)
    if filename and filename != "thumb" and not filename.startswith("tg_media_"):
        clean_name = urllib.parse.unquote(filename)
    else:
        clean_name = real_name

    range_header = request.headers.get("Range", "")
    start = 0
    end = file_size - 1
    status_code = 200

    if range_header:
        match = re.search(r"bytes=(\d+)-(\d*)", range_header)
        if match:
            start = int(match.group(1))
            if match.group(2):
                end = int(match.group(2))
        status_code = 206

    length = int(end - start + 1)
    media_key = (str(chat_id), message_id)
    user_agent = request.headers.get("User-Agent", "")
    ua_lower = user_agent.lower()

    # Robust detection: dedicated media players (VLC, MPV, etc.) vs download managers and browser downloads
    player_names = ("vlc", "mpv", "lavf", "libvlc", "quicktime", "exoplayer", "iina", "clapper")
    is_dedicated_player = any(p in ua_lower for p in player_names)

    sec_fetch_dest = request.headers.get("sec-fetch-dest", "").lower()
    accept_hdr = request.headers.get("accept", "").lower()
    is_browser_media = sec_fetch_dest in ("video", "audio") or "video/" in accept_hdr

    is_media_player = is_dedicated_player or is_browser_media
    is_known_dm_ua = any(dm in ua_lower for dm in ("fdm", "aria2", "idm", "wget", "curl", "freedownloadmanager", "libcurl", "persepolis", "kget", "neatdm"))
    pragma_no_cache = "no-cache" in request.headers.get("pragma", "").lower()
    cc_no_cache = "no-cache" in request.headers.get("cache-control", "").lower()
    has_keepalive = request.headers.get("connection", "").lower() == "keep-alive"
    no_sec_fetch = "sec-fetch-dest" not in request.headers
    is_spoofed_dm = not is_media_player and (pragma_no_cache or cc_no_cache) and has_keepalive and no_sec_fetch
    is_multi_conn = not is_media_player and len(_ACTIVE_MEDIA_CONNS.get(media_key, ())) >= 1

    is_dm = not is_media_player and (is_known_dm_ua or is_spoofed_dm or is_multi_conn)

    if start > 0 and not is_dm:
        # Precise 4KB alignment: eliminates discarding up to 512KB of unneeded data on forward seek
        aligned_start = (start // 4096) * 4096
        discard_bytes = start - aligned_start
        align_unit = 256 * 1024 if length <= 8 * 1024 * 1024 else 512 * 1024
        aligned_limit = length + discard_bytes
    else:
        align_unit = compute_dynamic_align_unit(file_size, start, length, user_agent)
        aligned_start = start - (start % align_unit)
        discard_bytes = start - aligned_start
        aligned_limit = length + discard_bytes

    # Only record progress on actual playback reads (not tiny probe requests or tail index probes)
    if length > 2 * 1024 * 1024 and (file_size <= 0 or start < (file_size - 10 * 1024 * 1024)):
        try:
            playback_tracker.update_byte_progress(str(chat_id), message_id, clean_name, start, file_size)
        except Exception as track_err:
            logger.debug(f"Playback tracking notice: {track_err}")

    ext = os.path.splitext(clean_name)[1].lower()
    content_type = "application/octet-stream"
    if ext in (".mp4", ".m4v", ".mov"):
        content_type = "video/mp4"
    elif ext == ".webm":
        content_type = "video/webm"
    elif ext in (".mkv", ".matroska"):
        content_type = "video/x-matroska"
    elif ext in (".mp3", ".m4a", ".aac", ".flac", ".ogg", ".wav"):
        content_type = f"audio/{ext.lstrip('.')}"
    elif hasattr(message.file, "mime_type") and message.file.mime_type:
        content_type = message.file.mime_type

    headers = {
        "Content-Type": content_type,
        "Content-Disposition": f'inline; filename="{clean_name}"',
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "ETag": f'"{chat_id}_{message_id}_{file_size}"',
        "X-Accel-Buffering": "no",
        "Cache-Control": "public, max-age=3600",
        "Connection": "keep-alive",
        "Keep-Alive": "timeout=60, max=1000",
    }

    if status_code == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    if hasattr(message, "date") and message.date:
        headers["Last-Modified"] = email.utils.formatdate(timeval=message.date.timestamp(), usegmt=True)

    if request.method == "HEAD":
        return Response(status_code=status_code, headers=headers)

    # 1. Check if complete media file is in local cache -> Instant <1ms serve
    cached_file = stream_cache_service.get_cached_file(str(chat_id), message_id)
    if cached_file and cached_file.exists() and cached_file.stat().st_size == file_size:
        logger.info(f"STREAM [{clean_name}] Local disk cache hit: {cached_file.name} (serving {length:,}B from offset {start:,})")
        async def cached_streamer():
            try:
                with open(cached_file, "rb") as f:
                    f.seek(start)
                    rem = length
                    while rem > 0:
                        read_sz = min(rem, 1024 * 1024)
                        chunk = f.read(read_sz)
                        if not chunk:
                            break
                        yield chunk
                        rem -= len(chunk)
                        await asyncio.sleep(0)
            except Exception as ce:
                logger.debug(f"Cached stream read notice: {ce}")

        return StreamingResponse(
            cached_streamer(),
            status_code=status_code,
            headers=headers
        )

    # Pure Direct Zero-Disk Stream Pipeline (Zero background downloads, zero disk writes)
    client_name = user_agent.split()[0] if user_agent else "Client"
    logger.info(
        f"STREAM [{clean_name}] Incoming stream request from {client_name}: "
        f"Range={range_header or 'Full'} (start={start:,}, length={length:,}B, total={file_size:,}B)"
    )

    # Check RAM Sliding Ring Buffer & Header/Tail Cache first (<0.01ms instant response)
    cached_slice = _SLIDING_STREAM_BUFFER.get_slice(str(chat_id), message_id, start, length)
    if cached_slice and len(cached_slice) == length:
        logger.info(f"STREAM [{clean_name}] Instant RAM hit: 100% served from RAM buffer ({len(cached_slice):,}B, offset {start:,}) in 0.000s")
        async def ram_streamer():
            yield cached_slice
        return StreamingResponse(ram_streamer(), status_code=status_code, headers=headers)

    # Differentiate between small probe requests (tail cues, short index queries) and continuous playback seeks
    is_probe_request = length < 1024 * 1024 or (file_size > 0 and start >= (file_size - 4 * 1024 * 1024))

    # Fast one-shot probe delivery for metadata queries (MKV cues, MP4 moov atoms, container probes)
    if is_probe_request and length <= 512 * 1024:
        chunk_size = 512 * 1024
        aligned_offset = (start // chunk_size) * chunk_size
        dc_id = media_dc_id or getattr(getattr(client, "session", None), "dc_id", 5)
        file_loc = getattr(file_info, "location", None) if file_info else None
        if file_loc:
            try:
                req = GetFileRequest(file_loc, offset=aligned_offset, limit=chunk_size)
                res = None
                try:
                    async with sender_pool.borrow(client, dc_id) as s:
                        res = await asyncio.wait_for(client._call(s, req, flood_sleep_threshold=0), timeout=8.0)
                except Exception:
                    res = await asyncio.wait_for(client(req, flood_sleep_threshold=0), timeout=8.0)

                data = getattr(res, "bytes", None) or (bytes(res) if isinstance(res, (bytes, bytearray)) else b"")
                if data:
                    _SLIDING_STREAM_BUFFER.push(str(chat_id), message_id, aligned_offset, data)
                    if file_size > 0 and aligned_offset >= (file_size - 4 * 1024 * 1024):
                        _SLIDING_STREAM_BUFFER.push_tail(str(chat_id), message_id, aligned_offset, data)

                    slice_offset = start - aligned_offset
                    probe_slice = data[slice_offset:slice_offset + length]
                    if len(probe_slice) == length:
                        logger.info(f"STREAM [{clean_name}] Direct probe delivered: {len(probe_slice):,}B at offset {start:,} (instant single shot)")
                        return Response(content=probe_slice, status_code=status_code, headers=headers)
            except Exception as probe_err:
                logger.debug(f"Direct probe fallback: {probe_err}")

    # Check for RAM prefix slice (e.g. VLC byte 107 seek where bytes 0..1MB are in RAM)
    cached_prefix = None
    if cached_slice and len(cached_slice) > 0 and len(cached_slice) < length:
        cached_prefix = cached_slice

    # Prewarm tail (cues / moov atom) in background during initial file load
    if start == 0 and file_size > 2 * 1024 * 1024 and media_key not in _SLIDING_STREAM_BUFFER._tails:
        asyncio.create_task(_SLIDING_STREAM_BUFFER.prewarm_tail(client, chat_id, message_id, message, file_size))

    curr_task = asyncio.current_task()
    if curr_task and not is_probe_request:
        # Atomic cancellation of preceding stream: halts obsolete MTProto in-flight requests in 0ms!
        old_handle = _ACTIVE_STREAM_ITERS.pop(media_key, None)
        if old_handle:
            logger.info(
                f"STREAM [{clean_name}] Fast seek detected ({start:,}): "
                f"Instantly cancelling previous stream iterator to free MTProto senders."
            )
            old_handle.stop_event.set()
            asyncio.create_task(old_handle.close())

        old_task_entry = _ACTIVE_STREAM_TASKS.get(media_key)
        if old_task_entry:
            old_task, old_start = old_task_entry
            if old_task is not curr_task and not old_task.done():
                old_task.cancel()
        _ACTIVE_STREAM_TASKS[media_key] = (curr_task, start)
        _ACTIVE_MEDIA_CONNS[media_key].add(curr_task)

    stop_event = asyncio.Event()

    async def stream_wrapper():
        gen = stream_engine.stream_media(
            client=client,
            chat_id=chat_id,
            message_id=message_id,
            start=start,
            length=length,
            file_size=file_size,
            is_media_player=is_media_player,
            clean_name=clean_name,
            cached_prefix=cached_prefix,
            message=message,
            get_fresh_message_fn=get_streamable_message,
            external_stop_event=stop_event
        )
        handle = StreamIteratorHandle(stop_event=stop_event, task=asyncio.current_task(), it=gen)
        if not is_probe_request:
            _ACTIVE_STREAM_ITERS[media_key] = handle

        try:
            async for chunk in gen:
                if stop_event.is_set():
                    raise ClientDisconnect()
                yield chunk
        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError, ClientDisconnect):
            raise ClientDisconnect()
        except Exception as e:
            logger.debug(f"Stream wrapper notice for {clean_name}: {e}")
            raise ClientDisconnect()
        finally:
            try:
                await gen.aclose()
            except Exception:
                pass
            if not is_probe_request:
                entry = _ACTIVE_STREAM_TASKS.get(media_key)
                if entry and entry[0] is curr_task:
                    _ACTIVE_STREAM_TASKS.pop(media_key, None)
                if _ACTIVE_STREAM_ITERS.get(media_key) is handle:
                    _ACTIVE_STREAM_ITERS.pop(media_key, None)
                if curr_task:
                    _ACTIVE_MEDIA_CONNS[media_key].discard(curr_task)
                if not _ACTIVE_MEDIA_CONNS[media_key]:
                    _ACTIVE_MEDIA_CONNS.pop(media_key, None)

    return StreamingResponse(
        stream_wrapper(),
        status_code=status_code,
        headers=headers
    )


@router.api_route("/stream/{chat_id}/{message_id}", methods=["GET", "HEAD"])
@router.api_route("/stream/{chat_id}/{message_id}/{filename}", methods=["GET", "HEAD"])
async def handle_transmux_stream(
    chat_id: str,
    message_id: int,
    request: Request,
    filename: Optional[str] = None,
    audio: int = 0,
    ss: float = 0.0
):
    if filename == "thumb":
        return await get_media_thumbnail(chat_id, message_id)

    """
    Real-Time Zero-Disk FFmpeg Chunk-Based Transmuxing Stream with Local Cache Acceleration.
    Remuxes MKV, AVI, TS, and AC3/DTS containers into fragmented MP4 chunks.
    If cached on local disk, runs FFmpeg directly against local file (<10ms start & instant seeks).
    """
    client = await TelegramClientManager.get_client()
    if not client or not client.is_connected():
        raise HTTPException(status_code=503, detail="Telegram client is not connected.")

    clean_chat_id: Union[int, str] = chat_id
    if chat_id != "me":
        try:
            clean_chat_id = int(chat_id)
        except ValueError:
            clean_chat_id = chat_id

    message = await get_streamable_message(client, clean_chat_id, message_id)
    if not message or not message.media or not hasattr(message, "file") or not message.file:
        raise HTTPException(status_code=404, detail="Message not found or contains no media.")

    raw_name = message.file.name if message.file.name else f"stream_{message_id}.mp4"
    file_size = int(message.file.size) if hasattr(message.file, "size") else 0
    import urllib.parse
    clean_name = urllib.parse.unquote(filename) if filename else auto_rename(raw_name)
    base_name = os.path.splitext(clean_name)[0] + ".mp4"

    if request.method == "HEAD":
        return Response(status_code=200, headers={
            "Content-Type": "video/mp4",
            "Content-Disposition": f'inline; filename="{base_name}"',
            "Accept-Ranges": "none",
        })

    # Check local cache
    cached_file = stream_cache_service.get_cached_file(str(chat_id), message_id)
    input_target = str(cached_file) if (cached_file and cached_file.exists()) else "pipe:0"
    chunk_unit = 128 * 1024  # 128KB ultra-fast low-latency chunks

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-threads", "2",
    ]
    if input_target != "pipe:0":
        if ss > 0:
            cmd.extend(["-ss", str(ss)])
        cmd.extend(["-i", input_target])
    else:
        cmd.extend(["-i", "pipe:0"])
        if ss > 0:
            cmd.extend(["-ss", str(ss)])

    cmd.extend([
        "-map", "0:v:0?",
        "-map", f"0:a:{audio}?",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ac", "2",
        "-avoid_negative_ts", "make_zero",
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-frag_duration", "500000",
        "-f", "mp4",
        "pipe:1"
    ])

    logger.info(f"Transmux starting: {' '.join(cmd)}")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE if input_target == "pipe:0" else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=lambda: os.nice(10) if hasattr(os, "nice") else None
        )
    except Exception as e:
        logger.error(f"Failed to start FFmpeg process: {e}")
        raise HTTPException(status_code=500, detail="FFmpeg is not available on the server.")

    async def log_stderr():
        if not proc.stderr:
            return
        try:
            err_data = await proc.stderr.read()
            if err_data:
                logger.warning(f"FFmpeg stderr: {err_data.decode(errors='ignore')}")
        except Exception:
            pass

    asyncio.create_task(log_stderr())

    # Feeder task: downloads from Telegram in 128KB chunks and pipes to proc.stdin
    async def feed_stdin():
        if input_target != "pipe:0" or not proc.stdin:
            return
        try:
            total_fed = 0
            from app.services.fast_stream_engine import fast_stream_engine
            async for chunk in fast_stream_engine.stream_byte_range(
                client=client,
                message=message,
                chat_id=str(chat_id),
                message_id=message_id,
                file_size=file_size if file_size else 0,
                filename=clean_name,
                start_byte=0,
                end_byte=(file_size - 1) if file_size else 0
            ):
                if not chunk or proc.returncode is not None:
                    break
                if proc.stdin and not proc.stdin.is_closing():
                    proc.stdin.write(chunk)
                    await proc.stdin.drain()
                    total_fed += len(chunk)
                    await asyncio.sleep(0)
                else:
                    break
            logger.info(f"Feed stdin finished, total bytes fed to FFmpeg: {total_fed}")
        except (ConnectionResetError, ConnectionAbortedError, asyncio.CancelledError, BrokenPipeError) as be:
            logger.debug(f"Feed stdin pipe notice: {be}")
        except Exception as e:
            logger.warning(f"Feed stdin error: {e}")
        finally:
            try:
                if proc.stdin and not proc.stdin.is_closing():
                    proc.stdin.close()
            except Exception:
                pass

    feeder_task = asyncio.create_task(feed_stdin())

    async def stream_output():
        if not proc.stdout:
            return
        total_out = 0
        try:
            while True:
                data = await proc.stdout.read(64 * 1024)
                if not data:
                    logger.info(f"Stream output EOF reached. Total out: {total_out}, returncode: {proc.returncode}")
                    break
                total_out += len(data)
                yield data
        except (ConnectionResetError, ConnectionAbortedError, asyncio.CancelledError, GeneratorExit):
            logger.debug("Transmux stream client disconnected or cancelled")
        finally:
            feeder_task.cancel()
            try:
                await feeder_task
            except (asyncio.CancelledError, Exception):
                pass
            try:
                if proc.returncode is None:
                    proc.kill()
                    await asyncio.shield(proc.wait())
            except Exception:
                pass

    headers = {
        "Content-Type": "video/mp4",
        "Content-Disposition": f'inline; filename="{base_name}"',
        "Accept-Ranges": "none",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0"
    }

    return StreamingResponse(
        stream_output(),
        media_type="video/mp4",
        headers=headers
    )


_STREAM_PROBE_CACHE = {}

@router.get("/api/media/streams/{chat_id}/{message_id}")
async def probe_media_streams(chat_id: str, message_id: int):
    """
    Probes audio languages, video codecs, and embedded subtitle tracks for the player toolbar.
    Cached in memory for instant responses.
    """
    clean_chat_id: Union[int, str] = chat_id
    if chat_id != "me":
        try:
            clean_chat_id = int(chat_id)
        except ValueError:
            clean_chat_id = chat_id

    cache_key = (clean_chat_id, message_id)
    if cache_key in _STREAM_PROBE_CACHE:
        return _STREAM_PROBE_CACHE[cache_key]

    client = await TelegramClientManager.get_client()
    if not client or not client.is_connected():
        raise HTTPException(status_code=503, detail="Telegram client is not connected.")

    message = sniffer_service._message_cache.get((clean_chat_id, message_id))
    if not message:
        try:
            message = await client.get_messages(clean_chat_id, ids=message_id)
        except Exception as e:
            logger.warning(f"Could not fetch message {message_id} from {clean_chat_id}: {e}")

    if not message or not message.media:
        raise HTTPException(status_code=404, detail="Media not found.")

    # Download initial 2.5MB buffer to probe container streams
    probe_buffer = bytearray()
    try:
        async for chunk in client.iter_download(message.media, request_size=512*1024, chunk_size=512*1024):
            probe_buffer.extend(chunk)
            if len(probe_buffer) >= 2500 * 1024:
                break
    except Exception as e:
        logger.debug(f"Probe buffer notice: {e}")

    if not probe_buffer:
        raise HTTPException(status_code=404, detail="Could not read stream headers.")

    try:
        ffprobe_cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            "-show_format",
            "pipe:0"
        ]
        proc = await asyncio.create_subprocess_exec(
            *ffprobe_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout_data, _ = await proc.communicate(input=bytes(probe_buffer))
        import json
        probe_json = json.loads(stdout_data.decode("utf-8", errors="ignore"))

        ISO_LANG_NAMES = {
            "tam": "Tamil", "tel": "Telugu", "hin": "Hindi", "eng": "English",
            "mal": "Malayalam", "kan": "Kannada", "spa": "Spanish", "fre": "French",
            "fra": "French", "ger": "German", "deu": "German", "ita": "Italian",
            "rus": "Russian", "jpn": "Japanese", "kor": "Korean", "chi": "Chinese",
            "zho": "Chinese", "ara": "Arabic", "por": "Portuguese", "ben": "Bengali"
        }

        audio_tracks = []
        subtitle_tracks = []
        audio_idx = 0
        sub_idx = 0

        for st in probe_json.get("streams", []):
            ctype = st.get("codec_type")
            tags = st.get("tags", {}) or {}
            lang = (tags.get("language") or tags.get("lang") or "").lower()
            lang_name = ISO_LANG_NAMES.get(lang, lang.upper() if lang else "")
            title = tags.get("title") or ""
            codec = (st.get("codec_name") or "").upper()
            channels = st.get("channels", 2)
            ch_str = "5.1" if channels == 6 else ("7.1" if channels == 8 else f"{channels}.0")

            if ctype == "audio":
                if lang_name:
                    track_label = f"{lang_name} ({codec} {ch_str})"
                elif title and not title.lower().startswith("telegram"):
                    track_label = f"{title} ({codec})"
                else:
                    track_label = f"Audio {audio_idx + 1} ({codec} {ch_str})"

                audio_tracks.append({
                    "index": audio_idx,
                    "stream_index": st.get("index", audio_idx),
                    "language": lang,
                    "title": track_label,
                    "codec": codec,
                    "channels": channels
                })
                audio_idx += 1
            elif ctype == "subtitle":
                if lang_name:
                    sub_label = f"{lang_name} Subtitles"
                elif title and not title.lower().startswith("telegram"):
                    sub_label = title
                else:
                    sub_label = f"Subtitle {sub_idx + 1} ({codec})"

                subtitle_tracks.append({
                    "index": sub_idx,
                    "stream_index": st.get("index", sub_idx),
                    "language": lang,
                    "title": sub_label,
                    "codec": codec,
                    "vtt_url": f"/api/media/subtitles/{chat_id}/{message_id}/{sub_idx}.vtt"
                })
                sub_idx += 1

        res = {
            "chat_id": str(chat_id),
            "message_id": message_id,
            "audio_tracks": audio_tracks,
            "subtitle_tracks": subtitle_tracks
        }
        _STREAM_PROBE_CACHE[cache_key] = res
        return res
    except Exception as err:
        logger.warning(f"ffprobe stream probe error: {err}")
        return {
            "chat_id": str(chat_id),
            "message_id": message_id,
            "audio_tracks": [{"index": 0, "title": "Default Audio", "language": "def"}],
            "subtitle_tracks": []
        }


_SUBTITLE_CACHE = {}

@router.get("/api/media/subtitles/{chat_id}/{message_id}/{sub_index}.vtt")
@router.get("/api/media/subtitles/{chat_id}/{message_id}/{sub_index}.srt")
async def get_stream_subtitles(chat_id: str, message_id: int, sub_index: int, request: Request):
    """Extracts embedded SSA/ASS/SubRip subtitle track and converts to standard WebVTT or SRT."""
    is_srt = request.url.path.endswith(".srt")
    fmt = "srt" if is_srt else "webvtt"
    media_type = "text/plain; charset=utf-8" if is_srt else "text/vtt; charset=utf-8"

    clean_chat_id: Union[int, str] = chat_id
    if chat_id != "me":
        try:
            clean_chat_id = int(chat_id)
        except ValueError:
            clean_chat_id = chat_id

    cache_key = (clean_chat_id, message_id, sub_index, fmt)
    if cache_key in _SUBTITLE_CACHE:
        return Response(content=_SUBTITLE_CACHE[cache_key], media_type=media_type)

    client = await TelegramClientManager.get_client()
    if not client or not client.is_connected():
        raise HTTPException(status_code=503, detail="Telegram client is not connected.")

    message = sniffer_service._message_cache.get((clean_chat_id, message_id))
    if not message:
        message = await client.get_messages(clean_chat_id, ids=message_id)

    if not message or not message.media:
        raise HTTPException(status_code=404, detail="Media not found.")

    raw_name = message.file.name or f"media_{message_id}.bin"
    cached_sub = subtitle_service.get_cached_subtitle(raw_name)
    if cached_sub and cached_sub.exists():
        try:
            with open(cached_sub, "r", encoding="utf-8", errors="ignore") as f:
                full_srt = f.read()
            if is_srt:
                return Response(content=full_srt, media_type=media_type)
            else:
                vtt_content = subtitle_service.convert_srt_to_vtt(full_srt)
                return Response(content=vtt_content, media_type=media_type)
        except Exception as read_err:
            logger.debug(f"Cached subtitle read error: {read_err}")

    sub_buffer = bytearray()
    try:
        async for chunk in client.iter_download(message.media, request_size=256*1024, chunk_size=256*1024):
            sub_buffer.extend(chunk)
            if len(sub_buffer) >= 2000 * 1024:
                break
    except Exception:
        pass

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-i", "pipe:0",
        "-map", f"0:s:{sub_index}?",
        "-c:s", "subrip" if is_srt else "webvtt",
        "-f", fmt,
        "pipe:1"
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout_data, _ = await proc.communicate(input=bytes(sub_buffer))
        if not stdout_data:
            stdout_data = b"1\n00:00:01,000 --> 00:00:05,000\n[Subtitles Active]\n" if is_srt else b"WEBVTT\n\n1\n00:00:01.000 --> 00:00:05.000\n[Subtitles Active]\n"
        _SUBTITLE_CACHE[cache_key] = stdout_data
        return Response(content=stdout_data, media_type=media_type)
    except Exception as e:
        logger.warning(f"Subtitle conversion notice: {e}")
        fallback = b"" if is_srt else b"WEBVTT\n\n"
        return Response(content=fallback, media_type=media_type)


@router.get("/api/media/subtitles/status/{chat_id}/{message_id}")
async def get_subtitle_status(chat_id: str, message_id: int):
    """Returns subtitle availability status for Cinema cards and player toolbar."""
    clean_chat_id: Union[int, str] = chat_id if chat_id == "me" else int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
    message = sniffer_service._message_cache.get((clean_chat_id, message_id))
    if not message:
        try:
            client = await TelegramClientManager.get_client()
            if client and client.is_connected():
                message = await client.get_messages(clean_chat_id, ids=message_id)
        except Exception:
            pass

    filename = message.file.name if message and message.file else f"media_{message_id}.bin"
    cached = subtitle_service.get_cached_subtitle(filename)
    return {
        "chat_id": chat_id,
        "message_id": message_id,
        "has_subtitles": bool(cached and cached.exists()),
        "path": str(cached) if cached else None,
        "filename": filename
    }


@router.post("/api/media/subtitles/prefetch/{chat_id}/{message_id}")
async def prefetch_media_subtitles(chat_id: str, message_id: int):
    """Triggers non-blocking background discovery and extraction of subtitles."""
    clean_chat_id: Union[int, str] = chat_id if chat_id == "me" else int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
    message = sniffer_service._message_cache.get((clean_chat_id, message_id))
    if not message:
        try:
            client = await TelegramClientManager.get_client()
            if client and client.is_connected():
                message = await client.get_messages(clean_chat_id, ids=message_id)
        except Exception:
            pass

    if message and message.file:
        raw_name = message.file.name or f"media_{message_id}.bin"
        stream_url = f"http://127.0.0.1:8088/dl/{chat_id}/{message_id}"
        subtitle_service.trigger_prefetch(raw_name, stream_url=stream_url)
        return {"status": "started", "filename": raw_name}
    return {"status": "skipped", "reason": "No media found"}


@router.get("/api/media/subtitles/download/{chat_id}/{message_id}")
async def download_media_subtitles(chat_id: str, message_id: int):
    """
    Direct subtitle file provider for VLC (--sub-file / --input-slave) and mobile M3U players.
    Serves cached or auto-fetched SRT subtitles, or falls back to a clean empty subtitle stream.
    """
    clean_chat_id: Union[int, str] = chat_id if chat_id == "me" else int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
    cached_msg = sniffer_service._message_cache.get((clean_chat_id, message_id)) or sniffer_service._message_cache.get((str(clean_chat_id), message_id))

    filename = None
    if cached_msg and getattr(cached_msg, "file", None) and getattr(cached_msg.file, "name", None):
        filename = cached_msg.file.name
    else:
        try:
            client = await TelegramClientManager.get_client()
            if client and client.is_connected():
                msg = await get_streamable_message(client, clean_chat_id, message_id)
                if msg and getattr(msg, "file", None) and getattr(msg.file, "name", None):
                    filename = msg.file.name
        except Exception:
            pass

    if not filename:
        filename = f"media_{message_id}.bin"

    # Check local cache first
    cached_sub = subtitle_service.get_cached_subtitle(filename)
    if not cached_sub:
        try:
            from app.config import PROXY_PORT
            stream_url = f"http://127.0.0.1:{PROXY_PORT}/dl/{chat_id}/{message_id}"
            cached_sub = await asyncio.wait_for(
                subtitle_service.get_or_download_subtitle(filename, stream_url=stream_url),
                timeout=4.0
            )
        except Exception:
            cached_sub = None

    if cached_sub and cached_sub.exists():
        try:
            with open(cached_sub, "rb") as f:
                content = f.read()
            return Response(
                content=content,
                media_type="text/plain; charset=utf-8",
                headers={
                    "Content-Disposition": f'inline; filename="{os.path.basename(cached_sub)}"',
                    "Cache-Control": "public, max-age=86400"
                }
            )
        except Exception as read_err:
            logger.debug(f"Subtitle download read error: {read_err}")

    # Return valid blank SRT with 200 OK so media player does not display network error popup
    blank_srt = b"1\n00:00:00,000 --> 00:00:01,000\n\n"
    return Response(
        content=blank_srt,
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "public, max-age=300"}
    )


# Thumbnail cache directories & routes defined earlier above handle_proxy_download


@router.get("/api/media/preview/{chat_id}/{message_id}/{frame_idx}")
async def get_media_preview_frame(chat_id: str, message_id: int, frame_idx: int = 0):
    """
    Generates or serves a keyframe preview snapshot (0 to 4) for YouTube/Netflix-style hover scrubbing.
    """
    frame_idx = max(0, min(4, frame_idx))
    cache_file = os.path.join(_PREVIEW_CACHE_DIR, f"{chat_id}_{message_id}_f{frame_idx}.jpg")
    
    if os.path.exists(cache_file) and os.path.getsize(cache_file) > 0:
        with open(cache_file, "rb") as f:
            return Response(
                content=f.read(),
                media_type="image/jpeg",
                headers={"Cache-Control": "public, max-age=86400, immutable"}
            )

    # If stream file is cached locally in stream_cache, extract directly
    local_cached = stream_cache_service.get_cached_file(chat_id, message_id)
    if not (local_cached and local_cached.exists()):
        # Avoid running FFmpeg against live MTProto proxy stream to protect VLC streaming bandwidth
        return await get_media_thumbnail(chat_id, message_id)

    input_source = str(local_cached)
    
    # Calculate seek percentage: 0 -> 10%, 1 -> 30%, 2 -> 50%, 3 -> 70%, 4 -> 90%
    ratios = [0.10, 0.30, 0.50, 0.70, 0.90]
    ratio = ratios[frame_idx]

    try:
        # Seek estimated duration or fallback to 15s * frame_idx
        seek_sec = max(5, int(ratio * 300))
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(seek_sec),
            "-i", input_source,
            "-vframes", "1",
            "-q:v", "4",
            "-vf", "scale=320:-1",
            cache_file
        ]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.wait(), timeout=3.5)

        if os.path.exists(cache_file) and os.path.getsize(cache_file) > 0:
            with open(cache_file, "rb") as f:
                return Response(
                    content=f.read(),
                    media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=86400, immutable"}
                )
    except Exception as e:
        logger.debug(f"Frame extraction skipped for {chat_id}/{message_id}: {e}")

    # Fallback to standard thumbnail
    return await get_media_thumbnail(chat_id, message_id)


@router.get("/api/media/videos/{chat_id}")
async def get_chat_videos(chat_id: str, limit: int = 250, offset_id: int = 0, force_refresh: bool = False):
    """
    Fetches video archives from a Telegram channel, group, or Saved Messages for the Cinema tab.
    Delegates to CinemaCatalog deep module with Stale-While-Revalidate caching.
    """
    client = await TelegramClientManager.get_client()
    return await cinema_catalog.get_videos(client, chat_id, limit, offset_id, force_refresh)


@router.post("/api/proxy/trigger")
async def trigger_proxy_download(chat_id: Union[int, str], message_id: int, manager: Optional[str] = "auto"):
    """1-Click manual trigger to push a Telegram file to external download manager."""
    from app.config import PROXY_HOST, PROXY_PORT
    url = f"http://{PROXY_HOST}:{PROXY_PORT}/dl/{chat_id}/{message_id}"
    installed = detect_managers()
    
    target_manager = manager
    if target_manager == "auto" or not target_manager:
        target_manager = "fdm" if "fdm" in installed else ("aria2" if "aria2" in installed else "neat")

    ok = await trigger_manager(target_manager, url, installed)
    return {
        "success": ok,
        "manager": target_manager,
        "url": url
    }


@router.get("/api/media/cache/status")
@router.get("/api/media/cache_status")
async def get_stream_cache_status():
    """Returns local stream cache usage statistics."""
    return stream_cache_service.get_cache_stats()


@router.post("/api/media/cache/clear")
@router.post("/api/media/cache_clear")
async def clear_stream_cache():
    """Clears all local cached media chunks."""
    cleared = stream_cache_service.clear_cache()
    return {"success": True, "cleared_files": cleared}


@router.get("/api/media/buffer/stats")
async def get_stream_buffer_stats():
    """Returns real-time RAM sliding buffer telemetry."""
    return _SLIDING_STREAM_BUFFER.get_stats()




@router.post("/api/media/vlc/play")
async def launch_vlc_stream(payload: Dict[str, Any]):
    """Launch VLC Player directly on host machine with high-speed MTProto stream."""
    import shutil
    import subprocess
    from app.config import PROXY_PORT

    chat_id = payload.get("chat_id")
    message_id = payload.get("message_id")
    raw_url = payload.get("stream_url")
    filename = payload.get("filename", "video.mp4")

    # If filename is generic or missing, try resolving from sniffer cache
    if chat_id and message_id:
        try:
            clean_cid = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
            cached_msg = sniffer_service._message_cache.get((clean_cid, message_id)) or sniffer_service._message_cache.get((str(clean_cid), message_id))
            if cached_msg and cached_msg.file and cached_msg.file.name:
                if not filename or filename in ("thumb", "video.mp4") or filename.startswith("tg_media_"):
                    filename = auto_rename(cached_msg.file.name)
        except Exception:
            pass

    import urllib.parse
    encoded_name = urllib.parse.quote(filename)

    if not raw_url and chat_id and message_id:
        raw_url = f"http://127.0.0.1:{PROXY_PORT}/dl/{chat_id}/{message_id}/{encoded_name}"
    elif raw_url and raw_url.startswith("/"):
        raw_url = f"http://127.0.0.1:{PROXY_PORT}{raw_url}"

    # If stream_url ended with /thumb, correct to proper media stream URL
    if raw_url and raw_url.endswith("/thumb") and filename != "thumb":
        raw_url = raw_url[:-6] + f"/{encoded_name}"

    # Pre-resolve and warm message cache & RAM buffer before launching player so /dl/... never stalls
    if chat_id and message_id:
        try:
            client = await TelegramClientManager.get_client()
            if client and client.is_connected():
                clean_cid = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
                msg = await get_streamable_message(client, clean_cid, int(message_id))
                if msg:
                    asyncio.create_task(prewarm_media_stream(client, clean_cid, int(message_id), msg))
        except Exception as pre_err:
            logger.debug(f"Pre-warm streamable message notice: {pre_err}")

    target_player = payload.get("player", "vlc")
    network_caching = payload.get("network_caching")
    audio_lang = payload.get("audio_lang")
    sub_lang = payload.get("sub_lang")
    resume_sec = 0
    if "start_time" in payload and payload["start_time"] is not None:
        resume_sec = max(0, int(payload["start_time"]))
    elif chat_id and message_id:
        try:
            prog = playback_tracker.get_playback_progress(str(chat_id), message_id)
            if prog and prog.get("progress_percent", 0) < 96.0:
                pos = prog.get("last_position_seconds", 0)
                if pos and pos > 10:
                    resume_sec = max(0, int(pos))
        except Exception as pe:
            logger.debug(f"Progress check notice: {pe}")

    _ACTIVE_STREAM_TASKS.clear()
    for k in list(_ACTIVE_STREAM_ITERS.keys()):
        old_i = _ACTIVE_STREAM_ITERS.pop(k, None)
        if old_i:
            try:
                await old_i.close()
            except Exception:
                pass

    logger.info(f"PLAYER LAUNCH: player={target_player}, file='{filename}', resume_sec={resume_sec}s, stream_url={raw_url}, caching={network_caching}ms")
    res = player_launcher.launch(
        stream_url=raw_url,
        filename=filename,
        player=target_player,
        resume_sec=resume_sec,
        chat_id=chat_id,
        message_id=message_id,
        network_caching=network_caching,
        audio_lang=audio_lang,
        sub_lang=sub_lang
    )

    import urllib.parse
    encoded_file = urllib.parse.quote(f"{filename}.m3u")
    q_params = []
    if network_caching:
        q_params.append(f"caching={network_caching}")
    if audio_lang:
        q_params.append(f"audio={urllib.parse.quote(audio_lang)}")
    if sub_lang:
        q_params.append(f"sub={urllib.parse.quote(sub_lang)}")
    qs = f"?{'&'.join(q_params)}" if q_params else ""
    res["playlist_url"] = f"/api/media/vlc/playlist/{chat_id}/{message_id}/{encoded_file}{qs}"
    return res


@router.get("/api/media/vlc/playlist/{chat_id}/{message_id}/{filename}")
async def generate_vlc_playlist(
    chat_id: str,
    message_id: int,
    filename: str,
    request: Request,
    caching: Optional[int] = None,
    audio: Optional[str] = None,
    sub: Optional[str] = None
):
    """Generates an .m3u playlist file for 1-click opening in VLC on any device."""
    from app.config import PROXY_PORT
    host = request.headers.get("host", f"localhost:{PROXY_PORT}")
    clean_filename = filename.replace(".m3u", "")
    encoded_name = urllib.parse.quote(clean_filename)
    stream_url = f"http://{host}/dl/{chat_id}/{message_id}/{encoded_name}"
    sub_url = f"http://{host}/api/media/subtitles/download/{chat_id}/{message_id}"
    m3u_content = player_launcher.build_single_playlist(
        clean_filename,
        stream_url,
        sub_url=sub_url,
        network_caching=caching,
        audio_lang=audio,
        sub_lang=sub
    )
    return Response(
        content=m3u_content,
        media_type="audio/x-mpegurl",
        headers={
            "Content-Disposition": f'attachment; filename="{clean_filename}.m3u"',
            "Content-Type": "audio/x-mpegurl; charset=utf-8"
        }
    )


@router.get("/api/media/mobile_info/{chat_id}/{message_id}")
async def get_mobile_playback_info(chat_id: str, message_id: int, request: Request, filename: Optional[str] = None):
    """
    Returns complete multi-platform playback metadata formatted for mobile VLC.
    Includes vlc:// protocol URL, Android Intent URL, M3U playlist URL, and direct stream links.
    """
    from app.config import PROXY_PORT
    host = request.headers.get("host", f"127.0.0.1:{PROXY_PORT}")
    scheme = request.url.scheme or "http"
    clean_fn = filename or f"video_{message_id}.mp4"
    if clean_fn in ("thumb", "video.mp4") or clean_fn.startswith("tg_media_"):
        clean_cid = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
        cached_msg = sniffer_service._message_cache.get((clean_cid, message_id)) or sniffer_service._message_cache.get((str(clean_cid), message_id))
        if cached_msg and cached_msg.file and cached_msg.file.name:
            clean_fn = auto_rename(cached_msg.file.name)

    import urllib.parse
    enc_name = urllib.parse.quote(clean_fn)
    stream_url = f"{scheme}://{host}/dl/{chat_id}/{message_id}/{enc_name}"

    vlc_url = f"vlc://{stream_url}"
    path_only = f"{host}/dl/{chat_id}/{message_id}/{enc_name}"
    android_intent = f"intent://{path_only}#Intent;scheme={scheme};type=video/*;package=org.videolan.vlc;end"

    playlist_url = f"/api/media/vlc/playlist/{chat_id}/{message_id}/{enc_name}.m3u"
    base_no_ext = os.path.splitext(clean_fn)[0]
    transmux_url = f"/stream/{chat_id}/{message_id}/{urllib.parse.quote(base_no_ext)}.mp4"
    sub_file = subtitle_service.get_cached_subtitle(clean_fn)

    from app.services.network_service import network_service
    lan_ip = network_service.get_lan_ip()
    lan_host = f"{lan_ip}:{PROXY_PORT}"
    lan_stream_url = f"{scheme}://{lan_host}/dl/{chat_id}/{message_id}/{enc_name}"
    lan_vlc_url = f"vlc://{lan_stream_url}"
    lan_android_intent = f"intent://{lan_host}/dl/{chat_id}/{message_id}/{enc_name}#Intent;scheme={scheme};type=video/*;package=org.videolan.vlc;end"
    lan_playlist_url = f"http://{lan_host}/api/media/vlc/playlist/{chat_id}/{message_id}/{enc_name}.m3u"
    lan_qrcode_url = f"/api/system/qrcode?text={urllib.parse.quote(lan_vlc_url)}"

    return {
        "chat_id": str(chat_id),
        "message_id": message_id,
        "filename": clean_fn,
        "stream_url": stream_url,
        "vlc_url": vlc_url,
        "android_intent": android_intent,
        "playlist_url": playlist_url,
        "transmux_url": transmux_url,
        "has_subtitles": bool(sub_file and sub_file.exists()),
        "subtitles_url": f"/api/media/subtitles/download/{chat_id}/{message_id}" if (sub_file and sub_file.exists()) else None,
        "host": host,
        "lan_ip": lan_ip,
        "lan_stream_url": lan_stream_url,
        "lan_vlc_url": lan_vlc_url,
        "lan_android_intent": lan_android_intent,
        "lan_playlist_url": lan_playlist_url,
        "lan_qrcode_url": lan_qrcode_url
    }


@router.get("/api/media/vlc/live_status")
async def get_vlc_live_status():
    """Returns live playback telemetry, volume, and playback state from active VLC instance."""
    return player_launcher.get_vlc_live_status()


@router.post("/api/media/vlc/control")
async def control_vlc(payload: Dict[str, Any]):
    """Dispatches remote control commands (play/pause, seek, volume, fullscreen, stop) to VLC."""
    command = payload.get("command", "")
    val = payload.get("val")
    if not command:
        raise HTTPException(status_code=400, detail="Command is required")
    return player_launcher.send_vlc_control(command, val)


@router.post("/api/media/vlc/play_batch")
async def launch_vlc_batch(payload: Dict[str, Any], request: Request):
    """Launch VLC Player with an entire TV Series / Season playlist queued for seamless binge watching."""
    from app.config import PROXY_PORT
    host = request.headers.get("host", f"127.0.0.1:{PROXY_PORT}")
    items = payload.get("items", [])
    title = payload.get("title", "Season Playlist")
    player = payload.get("player", "vlc")
    
    # Auto-resolve from chat_id and message_ids if items not provided directly
    if not items:
        chat_id = payload.get("chat_id")
        message_ids = payload.get("message_ids", [])
        if chat_id and message_ids:
            items = []
            for mid in message_ids:
                fn = "video.mp4"
                cached = sniffer_service._message_cache.get((chat_id, mid)) or sniffer_service._message_cache.get((str(chat_id), mid))
                if cached and getattr(cached, "file", None) and getattr(cached.file, "name", None):
                    fn = auto_rename(cached.file.name)
                items.append({
                    "chat_id": str(chat_id),
                    "message_id": int(mid),
                    "filename": fn
                })

    if not items:
        raise HTTPException(status_code=400, detail="No video items provided for batch playback.")

    network_caching = payload.get("network_caching")
    audio_lang = payload.get("audio_lang")
    sub_lang = payload.get("sub_lang")

    return player_launcher.launch_batch(
        items=items,
        title=title,
        player=player,
        host=host,
        network_caching=network_caching,
        audio_lang=audio_lang,
        sub_lang=sub_lang
    )


@router.post("/api/media/vlc/batch_playlist")
async def generate_batch_playlist(payload: Dict[str, Any], request: Request):
    """Generates a complete multi-episode .m3u playlist for a TV Series season."""
    from app.config import PROXY_PORT
    host = request.headers.get("host", f"localhost:{PROXY_PORT}")
    items = payload.get("items", [])
    title = payload.get("title", "Season_Playlist").replace(" ", "_")

    if not items:
        chat_id = payload.get("chat_id")
        message_ids = payload.get("message_ids", [])
        if chat_id and message_ids:
            items = []
            for mid in message_ids:
                fn = "video.mp4"
                cached = sniffer_service._message_cache.get((chat_id, mid)) or sniffer_service._message_cache.get((str(chat_id), mid))
                if cached and getattr(cached, "file", None) and getattr(cached.file, "name", None):
                    fn = auto_rename(cached.file.name)
                items.append({
                    "chat_id": str(chat_id),
                    "message_id": int(mid),
                    "filename": fn
                })

    if not items:
        raise HTTPException(status_code=400, detail="No video items provided for batch playlist.")

    network_caching = payload.get("network_caching")
    audio_lang = payload.get("audio_lang")
    sub_lang = payload.get("sub_lang")

    m3u_content = player_launcher.build_playlist(
        items=items,
        host=host,
        title=title,
        network_caching=network_caching,
        audio_lang=audio_lang,
        sub_lang=sub_lang
    )
    return Response(
        content=m3u_content,
        media_type="audio/x-mpegurl",
        headers={
            "Content-Disposition": f'attachment; filename="{title}.m3u"',
            "Content-Type": "audio/x-mpegurl; charset=utf-8"
        }
    )


@router.get("/api/media/continue_watching")
async def get_continue_watching():
    """Returns list of recently watched media ready for 1-click resume."""
    return playback_tracker.get_continue_watching(limit=12)


@router.delete("/api/media/continue_watching/{chat_id}/{message_id}")
async def dismiss_continue_watching(chat_id: str, message_id: int):
    """Dismisses an item from continue watching."""
    playback_tracker.remove_progress(chat_id, message_id)
    return {"success": True, "dismissed": f"{chat_id}_{message_id}"}


class PlaybackProgressPayload(BaseModel):
    chat_id: str
    message_id: int
    filename: str
    position_seconds: float
    duration_seconds: float
    thumbnail_url: Optional[str] = None
    last_chunk_index: Optional[int] = 0
    last_byte_offset: Optional[int] = 0


@router.post("/api/media/playback_progress")
async def update_playback_progress_api(payload: PlaybackProgressPayload):
    """Updates watch progress for a video with chunk coordinates."""
    playback_tracker.update_progress(
        chat_id=payload.chat_id,
        message_id=payload.message_id,
        filename=payload.filename,
        last_position_seconds=payload.position_seconds,
        duration_seconds=payload.duration_seconds,
        thumbnail_url=payload.thumbnail_url,
        last_chunk_index=payload.last_chunk_index or 0,
        last_byte_offset=payload.last_byte_offset or 0
    )
    return {"status": "ok"}


class OfflineSaverPayload(BaseModel):
    enabled: bool


@router.get("/api/settings/offline_saver")
async def get_offline_saver_settings():
    return {
        "enabled": stream_saver_service.is_enabled,
        "library_dir": str(stream_saver_service._library_dir)
    }


@router.post("/api/settings/offline_saver")
async def set_offline_saver_settings(payload: OfflineSaverPayload):
    stream_saver_service.set_enabled(payload.enabled)
    return {
        "status": "ok",
        "enabled": stream_saver_service.is_enabled,
        "library_dir": str(stream_saver_service._library_dir)
    }

