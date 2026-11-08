import os
import re
import time
import logging
import asyncio
import email.utils
from collections import defaultdict
from typing import Optional, Dict, Any, List, Union, cast, Tuple, Set, DefaultDict
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Request, Response, Depends
from fastapi.responses import StreamingResponse
from starlette.requests import ClientDisconnect
from app.auth import verify_auth
from app.config import ENABLE_MULTI_ACCOUNT_TURBO
from app.telegram_client import TelegramClientManager, sender_pool
from app.services.sniffer_service import auto_rename, sniffer_service
from app.services.manager_detector import trigger_manager, detect_managers

logger = logging.getLogger("proxy_route")

router = APIRouter(tags=["proxy"])

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
from app.services.vlc_playback import playback_grants
from app.services.stream_saver import stream_saver_service
from app.routes.history import get_cinema_cached_videos_db, save_cinema_cached_videos_db


from app.services.stream_buffer import stream_buffer, _SLIDING_STREAM_BUFFER, StreamSlidingBuffer
from app.services.stream_engine import stream_engine
_ACTIVE_STREAM_TASKS: Dict[Any, Tuple[asyncio.Task, int]] = {}
_ACTIVE_MEDIA_CONNS: DefaultDict[Tuple[str, int], Set[asyncio.Task]] = defaultdict(set)
_ACTIVE_STREAM_ITERS: Dict[Any, Any] = {}
_ACTIVE_PREFETCH_TASKS: Dict[Tuple[str, int], asyncio.Task] = {}
_PREBORROW_ACTIVE_DCS = set()
_PREBORROW_LOCK = asyncio.Lock()


class StreamIteratorHandle:
    """Atomic cancellation handle for pipelined MTProto streamer & fallback iterators."""
    def __init__(self, stop_event: asyncio.Event, task: Optional[asyncio.Task] = None, it: Optional[Any] = None):
        self.stop_event = stop_event
        self.task = task
        self.it = it

    async def close(self):
        self.stop_event.set()


def parse_http_range(range_header: str, file_size: int) -> Tuple[int, int, int, Optional[Response]]:
    """
    Parses single HTTP Range header per RFC 9110 / RFC 7233.
    Returns: (start, end, status_code, error_response)
    - If no Range header and file_size <= 0: (0, 0, 200, None)
    - If Range header on empty file (file_size <= 0): 416 Range Not Satisfiable (Content-Range: bytes */0)
    - If no Range header: (0, file_size - 1, 200, None)
    - If valid range: (start, end, 206, None)
    - If unsatisfiable / malformed: returns 416 Range Not Satisfiable response
    """
    if not range_header:
        if file_size <= 0:
            return 0, 0, 200, None
        return 0, file_size - 1, 200, None

    if file_size <= 0:
        # RFC 9110 Section 14.1.2: Any range on a 0-length representation is unsatisfiable
        return 0, 0, 416, Response(
            status_code=416,
            headers={
                "Content-Range": "bytes */0",
                "Accept-Ranges": "bytes"
            }
        )

    range_str = range_header.strip()
    if not range_str.startswith("bytes="):
        return 0, file_size - 1, 416, Response(
            status_code=416,
            headers={
                "Content-Range": f"bytes */{file_size}",
                "Accept-Ranges": "bytes"
            }
        )

    ranges_spec = range_str[6:].strip()
    if not ranges_spec:
        return 0, file_size - 1, 416, Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{file_size}", "Accept-Ranges": "bytes"}
        )

    # Multi-range is not supported for media streaming; use first range specification
    if "," in ranges_spec:
        ranges_spec = ranges_spec.split(",")[0].strip()

    # Case 1: Suffix range bytes=-500 (last 500 bytes of the file)
    suffix_match = re.fullmatch(r"-(\d+)", ranges_spec)
    if suffix_match:
        suffix_len = int(suffix_match.group(1))
        if suffix_len <= 0:
            return 0, file_size - 1, 416, Response(
                status_code=416,
                headers={"Content-Range": f"bytes */{file_size}", "Accept-Ranges": "bytes"}
            )
        start = max(0, file_size - suffix_len)
        end = file_size - 1
        return start, end, 206, None

    # Case 2: Standard range bytes=start-end or bytes=start-
    std_match = re.fullmatch(r"(\d+)-(\d*)", ranges_spec)
    if not std_match:
        return 0, file_size - 1, 416, Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{file_size}", "Accept-Ranges": "bytes"}
        )

    start = int(std_match.group(1))
    end_str = std_match.group(2)

    # Unsatisfiable if start is past EOF
    if start >= file_size:
        return 0, file_size - 1, 416, Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{file_size}", "Accept-Ranges": "bytes"}
        )

    if end_str:
        end = int(end_str)
        if end < start:
            return 0, file_size - 1, 416, Response(
                status_code=416,
                headers={"Content-Range": f"bytes */{file_size}", "Accept-Ranges": "bytes"}
            )
        end = min(end, file_size - 1)
    else:
        end = file_size - 1

    return start, end, 206, None



async def _prefetch_non_watched_blocks(client, message, chat_id: str, message_id: int, file_size: int, filename: str, start_block: int = 0):
    """Disabled during playback: pure streaming without background disk downloading."""
    return


async def prewarm_media_stream(client, chat_id: Union[str, int], message_id: int, message: Any):
    """
    Asynchronously pre-warms the media file header (up to 1MB in parallel) and tail (cues/moov) into RAM
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

        # 2. Prewarm header chunks 0 and 1 (up to 1MB total in parallel) into RAM buffer
        if key not in _SLIDING_STREAM_BUFFER._headers:
            chunk_size = 512 * 1024
            file_info = utils._get_file_info(message.media) if hasattr(message, "media") else None
            file_loc = getattr(file_info, "location", None)
            dc_id = getattr(file_info, "dc_id", None) or getattr(getattr(client, "session", None), "dc_id", 5)
            if file_loc:
                try:
                    from app.services.rust_bridge import rust_bridge
                    doc_id = getattr(file_loc, "id", None)
                    access_hash = getattr(file_loc, "access_hash", None)
                    file_ref = getattr(file_loc, "file_reference", None)
                    if doc_id and access_hash and file_ref:
                        asyncio.create_task(rust_bridge.prewarm(dc_id, doc_id, access_hash, file_ref, f_size))
                except Exception:
                    pass

                async def _fetch_chunk(offset: int):
                    req = GetFileRequest(file_loc, offset=offset, limit=chunk_size)
                    res = None
                    try:
                        async with sender_pool.borrow(client, dc_id) as s:
                            res = await asyncio.wait_for(client._call(s, req, flood_sleep_threshold=0), timeout=6.0)
                    except Exception:
                        res = await asyncio.wait_for(client(req, flood_sleep_threshold=0), timeout=6.0)
                    data = getattr(res, "bytes", None) or (bytes(res) if isinstance(res, (bytes, bytearray)) else b"")
                    if data:
                        _SLIDING_STREAM_BUFFER.push(str(chat_id), int(message_id), offset, data)
                        return len(data)
                    return 0

                tasks = [_fetch_chunk(0)]
                if f_size > chunk_size:
                    tasks.append(_fetch_chunk(chunk_size))
                results = await asyncio.gather(*tasks, return_exceptions=True)
                total_warmed = sum(r for r in results if isinstance(r, int))
                fn = getattr(message.file, "name", "media")
                logger.info(f"STREAM [{fn}] Prewarmed {total_warmed:,}B header (up to 1MB) into RAM for instant VLC playback.")
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


async def search_web_poster(raw_name: str) -> Optional[bytes]:
    try:
        from app.services.sniffer_service import auto_rename
        cleaned_fn = auto_rename(raw_name)
        queries = []
        clean = re.sub(r"\.[a-zA-Z0-9]+$", "", cleaned_fn)
        clean = re.sub(r"^@\S+\s*", "", clean)
        clean = re.sub(r"^(?:vip\s*linkzz|linkzz|tamizhan|org|com|net|tamilmv|tamilblasters|onlyhd4u|mspxmovies|sklink\s*files|cszmovies|team\s*hdt)\s+", "", clean, flags=re.I)
        clean = re.sub(r"\b(1080p|720p|480p|4k|2160p|bluray|web-?dl|webrip|remux|x264|x265|hevc|ddp|atmos|5\.1|true|remastered|v1\.neil|telumkv|tpu|clean|predvd|combined|org\s*audio|tamil\s*audio|hindi\s*audio|telugu\s*audio|yts\s*bz|yts|proper|hdrip|dvdrip)\b", " ", clean, flags=re.I)
        clean = re.sub(r"\b[sS]\d{1,2}(?:[\s._\-]?[eE]\d{1,3})?\b", " ", clean)
        clean = re.sub(r"\b(?:ep|episode)\s*\d+(?:/\d+)?\b", " ", clean, flags=re.I)
        clean = re.sub(r"[\(\)\[\]\{\}\-_.]", " ", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        if clean and len(clean) >= 2:
            queries.append(clean)
            no_year = re.sub(r"\b(19\d{2}|20\d{2})\b", "", clean).strip()
            if no_year and no_year != clean and len(no_year) >= 2:
                queries.append(no_year)

        is_series = bool(re.search(r"\b(season|episode|s\d{1,2}|ep\d{1,2})\b", raw_name, re.I))
        categories = ["series", "movie"] if is_series else ["movie", "series"]

        import urllib.parse
        import httpx
        async with httpx.AsyncClient(timeout=3.0, follow_redirects=True) as http_client:
            for q in queries:
                for cat in categories:
                    url = f"https://v3-cinemeta.strem.io/catalog/{cat}/top/search={urllib.parse.quote(q)}.json"
                    try:
                        resp = await http_client.get(url, headers={"User-Agent": "TGPowerSuite/1.0"})
                        if resp.status_code == 200:
                            data = resp.json()
                            metas = data.get("metas", [])
                            if metas and metas[0].get("poster"):
                                poster_url = metas[0]["poster"]
                                img_resp = await http_client.get(poster_url)
                                if img_resp.status_code == 200 and len(img_resp.content) > 100:
                                    return img_resp.content
                    except Exception:
                        continue
    except Exception:
        pass
    return None


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
        from telethon.tl.types import Photo, PhotoStrippedSize, PhotoCachedSize

        if thumbs or isinstance(message.media, Photo):
            async with _THUMB_SEMAPHORE:
                try:
                    thumb_bytes = await client.download_media(message, thumb=-1, file=bytes)
                except Exception:
                    try:
                        thumb_bytes = await client.download_media(message, thumb=0, file=bytes)
                    except Exception:
                        pass

        if not thumb_bytes and thumbs:
            for t in thumbs:
                if isinstance(t, PhotoCachedSize) and getattr(t, "bytes", None):
                    thumb_bytes = t.bytes
                    if thumb_bytes:
                        break
                elif isinstance(t, PhotoStrippedSize) and getattr(t, "bytes", None):
                    try:
                        thumb_bytes = utils.stripped_photo_to_jpg(t.bytes)
                        if thumb_bytes:
                            break
                    except Exception:
                        pass

        if not thumb_bytes:
            try:
                prev_msgs = await client.get_messages(clean_chat_id, ids=[message_id - 1, message_id + 1])
                for pm in (prev_msgs or []):
                    if pm and getattr(pm, "photo", None):
                        tb = await client.download_media(pm, thumb=0, file=bytes)
                        if tb:
                            thumb_bytes = tb
                            break
            except Exception:
                pass

        if not thumb_bytes:
            fn = ""
            if hasattr(message, "file") and getattr(message.file, "name", None):
                fn = message.file.name
            elif getattr(message, "message", None):
                fn = message.message
            if fn:
                thumb_bytes = await search_web_poster(fn)

        if not thumb_bytes:
            try:
                local_cached = stream_cache_service.get_cached_file(chat_id, message_id)
                if local_cached and local_cached.exists():
                    tmp_frame = os.path.join(_THUMB_CACHE_DIR, f"tmp_{message_id}.jpg")
                    cmd = [
                        "ffmpeg", "-y",
                        "-ss", "10",
                        "-i", str(local_cached),
                        "-vframes", "1",
                        "-q:v", "4",
                        "-vf", "scale=480:-1",
                        tmp_frame
                    ]
                    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                    await asyncio.wait_for(proc.wait(), timeout=3.0)
                    if os.path.exists(tmp_frame) and os.path.getsize(tmp_frame) > 0:
                        with open(tmp_frame, "rb") as f:
                            thumb_bytes = f.read()
                        try:
                            os.remove(tmp_frame)
                        except Exception:
                            pass
            except Exception:
                pass

        if thumb_bytes:
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

        return Response(
            content=DEFAULT_VIDEO_PLACEHOLDER_SVG,
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=30"}
        )
    except Exception as e:
        logger.debug(f"Thumbnail notice for {chat_id}/{message_id}: {e}")
        return Response(
            content=DEFAULT_VIDEO_PLACEHOLDER_SVG,
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=30"}
        )


@router.api_route("/dl/{chat_id}/{message_id}", methods=["GET", "HEAD"])
@router.api_route("/dl/{chat_id}/{message_id}/{filename}", methods=["GET", "HEAD"])
@router.api_route("/dl/{chat_id}/{message_id}/{filename:path}", methods=["GET", "HEAD"])
async def handle_proxy_download(
    chat_id: str,
    message_id: int,
    request: Request,
    filename: Optional[str] = None,
    _auth = Depends(verify_auth)
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
    my_info = TelegramClientManager.get_cached_me() or {}
    my_id = my_info.get("id")
    if chat_id == "me" or (my_id and str(chat_id) in (str(my_id), f"-100{my_id}")):
        clean_chat_id = "me"
    else:
        try:
            clean_chat_id = int(chat_id)
        except ValueError:
            clean_chat_id = chat_id

    message = await get_streamable_message(client, clean_chat_id, message_id)
    if not message or not message.media or not hasattr(message, "file") or not message.file:
        message = await get_streamable_message(client, clean_chat_id, message_id, force_refresh=True)
    if not message or not message.media or not hasattr(message, "file") or not message.file:
        clean_str = str(clean_chat_id).strip()
        alt_ids = []
        if not clean_str.startswith("-") and clean_str.isdigit():
            alt_ids.append(int(f"-100{clean_str}"))
        elif clean_str.startswith("-100"):
            alt_ids.append(int(clean_str[4:]))
        for alt_id in alt_ids:
            try:
                message = await get_streamable_message(client, alt_id, message_id, force_refresh=True)
                if message and getattr(message, "media", None) and getattr(message, "file", None):
                    clean_chat_id = alt_id
                    break
            except Exception:
                pass
    if not message or not message.media or not hasattr(message, "file") or not message.file:
        if not client.is_connected():
            raise HTTPException(status_code=503, detail="Telegram connection temporarily unavailable. Please retry.", headers={"Retry-After": "3"})
        raise HTTPException(status_code=404, detail="Message not found or contains no media.")

    file_size = int(message.file.size)
    file_info = utils._get_file_info(message.media) if hasattr(message, "media") else None
    media_dc_id = getattr(file_info, "dc_id", None)
    session_dc = getattr(getattr(client, "session", None), "dc_id", None)
    if media_dc_id and session_dc and media_dc_id != session_dc and hasattr(client, "_borrow_exported_sender"):
        borrowed = getattr(client, "_borrowed_senders", {}).get(media_dc_id)
        if not borrowed or not borrowed[1] or not borrowed[1].is_connected():
            async def _safe_preborrow(dc: int):
                async with _PREBORROW_LOCK:
                    if dc in _PREBORROW_ACTIVE_DCS or rate_governor.is_cooling_down or rate_governor.is_client_cooling_down(client):
                        return
                    _PREBORROW_ACTIVE_DCS.add(dc)
                try:
                    b = getattr(client, "_borrowed_senders", {}).get(dc)
                    if not b or not b[1] or not b[1].is_connected():
                        await client._borrow_exported_sender(dc)
                except errors.FloodWaitError as fwe:
                    rate_governor.report_flood_wait(fwe.seconds, reason=f"ExportAuth DC {dc}", client=client)
                except Exception:
                    pass
                finally:
                    async with _PREBORROW_LOCK:
                        _PREBORROW_ACTIVE_DCS.discard(dc)
            asyncio.create_task(_safe_preborrow(media_dc_id))

    raw_name = message.file.name if message.file.name else f"tg_media_{message_id}.bin"
    raw_name = "".join([c for c in raw_name if (c.isalnum() or c in " .-_()")]).strip()
    import urllib.parse
    real_name = auto_rename(raw_name)
    if filename and filename != "thumb" and not filename.startswith("tg_media_") and not filename.startswith("video_"):
        clean_name = urllib.parse.unquote(filename)
    else:
        clean_name = real_name

    range_header = request.headers.get("Range", "")
    start, end, status_code, err_resp = parse_http_range(range_header, file_size)
    if err_resp:
        return err_resp

    length = int(end - start + 1)
    norm_chat_id = str(clean_chat_id).lstrip("-").replace("100", "", 1)
    media_key = (norm_chat_id, message_id)
    user_agent = request.headers.get("User-Agent", "")
    ua_lower = user_agent.lower()

    try:
        query_params = request.query_params
    except Exception:
        query_params = {}
    has_dm_query = query_params.get("dm") in ("1", "true") or query_params.get("download") in ("1", "true")
    is_known_dm_ua = any(dm in ua_lower for dm in ("fdm", "aria2", "idm", "wget", "curl", "freedownloadmanager", "libcurl", "persepolis", "kget", "neatdm"))
    pragma_no_cache = "no-cache" in request.headers.get("pragma", "").lower()
    cc_no_cache = "no-cache" in request.headers.get("cache-control", "").lower()
    has_keepalive = request.headers.get("connection", "").lower() == "keep-alive"
    no_sec_fetch = "sec-fetch-dest" not in request.headers

    player_names = ("vlc", "mpv", "lavf", "libvlc", "quicktime", "exoplayer", "iina", "clapper")
    is_dedicated_player = any(p in ua_lower for p in player_names)
    sec_fetch_dest = request.headers.get("sec-fetch-dest", "").lower()
    is_browser_media = sec_fetch_dest in ("video", "audio")

    if has_dm_query or is_known_dm_ua:
        is_media_player = False
        is_dm = True
    elif is_dedicated_player or is_browser_media:
        is_media_player = True
        is_dm = False
    else:
        is_spoofed_dm = (pragma_no_cache or cc_no_cache) and has_keepalive and no_sec_fetch
        is_multi_conn = len(_ACTIVE_MEDIA_CONNS.get(media_key, ())) >= 1
        is_dm = is_spoofed_dm or is_multi_conn or no_sec_fetch
        is_media_player = not is_dm

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

    if length > 2 * 1024 * 1024 and (file_size <= 0 or start < (file_size - 10 * 1024 * 1024)):
        try:
            asyncio.create_task(playback_tracker.aupdate_byte_progress(
                str(chat_id), message_id, clean_name, start, file_size
            ))
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

    # RFC 5987 / 6266 standard content disposition: ASCII fallback + UTF-8 percent-encoded filename
    safe_ascii = re.sub(r'[^\x20-\x7E]', '_', clean_name).replace('"', '\\"')
    safe_utf8 = urllib.parse.quote(clean_name, encoding="utf-8")
    content_disp = f'inline; filename="{safe_ascii}"; filename*=UTF-8\'\'{safe_utf8}'

    headers = {
        "Content-Type": content_type,
        "Content-Disposition": content_disp,
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "ETag": f'"{norm_chat_id}_{message_id}_{file_size}"',
        "X-Accel-Buffering": "no",
        "X-Content-Type-Options": "nosniff",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Expose-Headers": "Content-Range, Content-Length, Accept-Ranges, Content-Disposition, ETag",
        "Cache-Control": "public, max-age=3600",
        "Connection": "keep-alive",
        "Keep-Alive": "timeout=60, max=1000",
    }

    if status_code == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    if hasattr(message, "date") and message.date:
        headers["Last-Modified"] = email.utils.formatdate(timeval=message.date.timestamp(), usegmt=True)

    if_none_match = request.headers.get("If-None-Match", "").strip()
    if if_none_match:
        etag_val = headers["ETag"].strip('"')
        req_tags = [t.strip().strip('"') for t in if_none_match.split(",")]
        if "*" in req_tags or etag_val in req_tags or headers["ETag"] in if_none_match:
            return Response(status_code=304, headers=headers)

    if_range = request.headers.get("If-Range", "").strip()
    if if_range:
        etag_val = headers["ETag"].strip('"')
        clean_if_range = if_range.strip('"')
        if clean_if_range != etag_val and clean_if_range != headers.get("ETag", ""):
            start = 0
            end = file_size - 1
            length = file_size
            status_code = 200
            headers["Content-Length"] = str(length)
            headers.pop("Content-Range", None)

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

    cached_slice = _SLIDING_STREAM_BUFFER.get_slice(norm_chat_id, message_id, start, length)
    if cached_slice and len(cached_slice) == length:
        logger.info(f"STREAM [{clean_name}] Instant RAM hit: 100% served from RAM buffer ({len(cached_slice):,}B, offset {start:,}) in 0.000s")
        async def ram_streamer():
            yield cached_slice
        return StreamingResponse(ram_streamer(), status_code=status_code, headers=headers)

    if file_size > 0 and start >= (file_size - 4 * 1024 * 1024):
        tail_entry = _SLIDING_STREAM_BUFFER._tails.get((norm_chat_id, message_id)) or _SLIDING_STREAM_BUFFER._tails.get((str(chat_id), message_id))
        if tail_entry:
            tail_offset = tail_entry.get("start", tail_entry.get("offset", 0))
            tail_data = tail_entry.get("data", b"")
            if tail_offset <= start < (tail_offset + len(tail_data)):
                slice_offset = start - tail_offset
                avail = len(tail_data) - slice_offset
                slice_len = min(length, avail)
                if slice_len > 0:
                    probe_slice = bytes(tail_data[slice_offset:slice_offset + slice_len])
                    logger.info(f"STREAM [{clean_name}] Direct tail RAM hit: {len(probe_slice):,}B at offset {start:,} (0ms TTFB)")
                    return Response(content=probe_slice, status_code=status_code, headers=headers)

    is_probe_request = (length <= 512 * 1024) and (start < 2 * 1024 * 1024 or (file_size > 0 and start >= (file_size - 4 * 1024 * 1024)))

    if is_probe_request and length <= 512 * 1024:
        chunk_size = 512 * 1024
        aligned_offset = (start // chunk_size) * chunk_size
        dc_id = media_dc_id or getattr(getattr(client, "session", None), "dc_id", 5)
        file_loc = getattr(file_info, "location", None) if file_info else None
        if file_loc:
            doc_id = getattr(file_loc, "id", None)
            access_hash = getattr(file_loc, "access_hash", None)
            file_ref = getattr(file_loc, "file_reference", None)
            if doc_id and access_hash and file_ref:
                try:
                    from app.services.rust_bridge import rust_bridge
                    if await rust_bridge.is_healthy():
                        probe_acc = 1 if (rate_governor.is_client_cooling_down(client) and ENABLE_MULTI_ACCOUNT_TURBO) else 0
                        rust_chunk = await rust_bridge.stream_chunk(
                            dc_id=dc_id,
                            doc_id=doc_id,
                            access_hash=access_hash,
                            file_reference=file_ref,
                            offset=aligned_offset,
                            limit=chunk_size,
                            priority="high",
                            account_idx=probe_acc
                        )
                        if rust_chunk and len(rust_chunk) > 0:
                            _SLIDING_STREAM_BUFFER.push(norm_chat_id, message_id, aligned_offset, rust_chunk)
                            if file_size > 0 and aligned_offset >= (file_size - 4 * 1024 * 1024):
                                _SLIDING_STREAM_BUFFER.push_tail(norm_chat_id, message_id, aligned_offset, rust_chunk)
                            slice_offset = start - aligned_offset
                            probe_slice = rust_chunk[slice_offset:slice_offset + length]
                            if len(probe_slice) == length:
                                return Response(content=probe_slice, status_code=status_code, headers=headers)
                except Exception as r_probe_err:
                    logger.debug(f"Rust direct probe fallback: {r_probe_err}")

            try:
                req = GetFileRequest(file_loc, offset=aligned_offset, limit=chunk_size)
                res = None
                try:
                    async with sender_pool.borrow(client, dc_id, priority=is_media_player) as s:
                        res = await asyncio.wait_for(client._call(s, req, flood_sleep_threshold=0), timeout=8.0)
                except Exception:
                    res = await asyncio.wait_for(client(req, flood_sleep_threshold=0), timeout=8.0)

                data = getattr(res, "bytes", None) or (bytes(res) if isinstance(res, (bytes, bytearray)) else b"")
                if data:
                    _SLIDING_STREAM_BUFFER.push(norm_chat_id, message_id, aligned_offset, data)
                    if file_size > 0 and aligned_offset >= (file_size - 4 * 1024 * 1024):
                        _SLIDING_STREAM_BUFFER.push_tail(norm_chat_id, message_id, aligned_offset, data)

                    slice_offset = start - aligned_offset
                    probe_slice = data[slice_offset:slice_offset + length]
                    if len(probe_slice) == length:
                        logger.info(f"STREAM [{clean_name}] Direct probe delivered: {len(probe_slice):,}B at offset {start:,} (instant single shot)")
                        return Response(content=probe_slice, status_code=status_code, headers=headers)
            except Exception as probe_err:
                logger.debug(f"Direct probe fallback: {probe_err}")

    cached_prefix = None
    if cached_slice and len(cached_slice) > 0 and len(cached_slice) < length:
        cached_prefix = cached_slice

    if start == 0 and file_size > 2 * 1024 * 1024 and media_key not in _SLIDING_STREAM_BUFFER._tails:
        pt = asyncio.create_task(_SLIDING_STREAM_BUFFER.prewarm_tail(client, clean_chat_id, message_id, message, file_size))
        _ACTIVE_PREFETCH_TASKS[media_key] = pt
        pt.add_done_callback(lambda _t: _ACTIVE_PREFETCH_TASKS.pop(media_key, None) if _ACTIVE_PREFETCH_TASKS.get(media_key) is _t else None)

    req_client_host = getattr(getattr(request, "client", None), "host", "127.0.0.1") or "127.0.0.1"
    norm_client_host = "127.0.0.1" if req_client_host in ("::1", "localhost", "testclient", "testserver") else req_client_host
    ua_lower = user_agent.lower() if user_agent else ""
    if "vlc" in ua_lower:
        client_tag = "vlc"
    elif "mpv" in ua_lower:
        client_tag = "mpv"
    elif any(b in ua_lower for b in ["mozilla", "chrome", "safari", "webkit", "edge"]):
        client_tag = "browser"
    else:
        client_tag = "generic"

    curr_task = asyncio.current_task()
    session_key = (norm_chat_id, message_id, client_tag, norm_client_host, id(curr_task)) if is_media_player else (norm_chat_id, message_id, f"dl_{id(curr_task)}_{start}", norm_client_host)

    stop_event = asyncio.Event()
    handle = StreamIteratorHandle(stop_event=stop_event)

    if curr_task and is_media_player:
        host_player_keys = [
            k for k in list(_ACTIVE_STREAM_ITERS.keys())
            if len(k) >= 4 and k[2] == client_tag and k[3] == norm_client_host and k[1] != message_id
        ]
        for sk in host_player_keys:
            old_h = _ACTIVE_STREAM_ITERS.pop(sk, None)
            if old_h:
                old_h.stop_event.set()
            _ACTIVE_STREAM_TASKS.pop(sk, None)
            old_cid, old_mid = sk[0], sk[1]
            stream_engine.abort_message_stream(old_cid, old_mid)
            _ACTIVE_MEDIA_CONNS.pop((old_cid, old_mid), None)

    if curr_task and not is_probe_request:
        if is_media_player:
            pref_t = _ACTIVE_PREFETCH_TASKS.pop(media_key, None)
            if pref_t and not pref_t.done():
                pref_t.cancel()

        _ACTIVE_STREAM_TASKS[session_key] = (curr_task, start)
        _ACTIVE_MEDIA_CONNS[media_key].add(curr_task)
        _ACTIVE_STREAM_ITERS[session_key] = handle

    async def stream_wrapper():
        handle.task = asyncio.current_task()
        if stop_event.is_set():
            return

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
        handle.it = gen
        if not is_probe_request:
            _ACTIVE_STREAM_ITERS[session_key] = handle

        try:
            async for chunk in gen:
                if stop_event.is_set():
                    return
                yield chunk
        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError, ClientDisconnect):
            return
        except Exception as e:
            logger.debug(f"Stream wrapper notice for {clean_name}: {e}")
            return
        finally:
            try:
                await gen.aclose()
            except Exception:
                pass
            if not is_probe_request:
                entry = _ACTIVE_STREAM_TASKS.get(session_key)
                if entry and entry[0] is curr_task:
                    _ACTIVE_STREAM_TASKS.pop(session_key, None)
                if _ACTIVE_STREAM_ITERS.get(session_key) is handle:
                    _ACTIVE_STREAM_ITERS.pop(session_key, None)
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
@router.api_route("/stream/{chat_id}/{message_id}/{filename:path}", methods=["GET", "HEAD"])
async def handle_transmux_stream(
    chat_id: str,
    message_id: int,
    request: Request,
    filename: Optional[str] = None,
    audio: int = 0,
    ss: float = 0.0,
    _auth = Depends(verify_auth)
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
        message = await get_streamable_message(client, clean_chat_id, message_id, force_refresh=True)
    if not message or not message.media or not hasattr(message, "file") or not message.file:
        clean_str = str(clean_chat_id).strip()
        alt_ids = []
        if not clean_str.startswith("-") and clean_str.isdigit():
            alt_ids.append(int(f"-100{clean_str}"))
        elif clean_str.startswith("-100"):
            alt_ids.append(int(clean_str[4:]))
        for alt_id in alt_ids:
            try:
                message = await get_streamable_message(client, alt_id, message_id, force_refresh=True)
                if message and getattr(message, "media", None) and getattr(message, "file", None):
                    clean_chat_id = alt_id
                    break
            except Exception:
                pass
    if not message or not message.media or not hasattr(message, "file") or not message.file:
        asyncio.create_task(cinema_catalog.remove_deleted_video(clean_chat_id, message_id))
        raise HTTPException(status_code=404, detail="Message not found or contains no media. It may have been deleted from Telegram.")

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

@router.get("/api/media/streams/{chat_id}/{message_id}", dependencies=[Depends(verify_auth)])
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
    if not message or not getattr(message, "media", None):
        message = await get_streamable_message(client, clean_chat_id, message_id)
    if not message or not getattr(message, "media", None):
        clean_str = str(clean_chat_id).strip()
        alt_ids = []
        if not clean_str.startswith("-") and clean_str.isdigit():
            alt_ids.append(int(f"-100{clean_str}"))
        elif clean_str.startswith("-100"):
            alt_ids.append(int(clean_str[4:]))
        for alt_id in alt_ids:
            try:
                message = await get_streamable_message(client, alt_id, message_id)
                if message and getattr(message, "media", None):
                    clean_chat_id = alt_id
                    break
            except Exception:
                pass

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
    if not message or not getattr(message, "media", None):
        message = await get_streamable_message(client, clean_chat_id, message_id)

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
    if not message or not getattr(message, "media", None):
        try:
            client = await TelegramClientManager.get_client()
            if client and client.is_connected():
                message = await get_streamable_message(client, clean_chat_id, message_id)
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
    if not message or not getattr(message, "media", None):
        try:
            client = await TelegramClientManager.get_client()
            if client and client.is_connected():
                message = await get_streamable_message(client, clean_chat_id, message_id)
        except Exception:
            pass

    if message and message.file:
        raw_name = message.file.name or f"media_{message_id}.bin"
        stream_url = f"http://127.0.0.1:8088/dl/{chat_id}/{message_id}"
        subtitle_service.trigger_prefetch(raw_name, stream_url=stream_url)
        return {"status": "started", "filename": raw_name}
    return {"status": "skipped", "reason": "No media found"}


@router.get("/api/media/subtitles/download/{chat_id}/{message_id}")
async def download_media_subtitles(chat_id: str, message_id: int, _auth = Depends(verify_auth)):
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


@router.get("/api/media/videos/{chat_id}", dependencies=[Depends(verify_auth)])
async def get_chat_videos(chat_id: str, limit: int = 250, offset_id: int = 0, force_refresh: bool = False):
    """
    Fetches video archives from a Telegram channel, group, or Saved Messages for the Cinema tab.
    Delegates to CinemaCatalog deep module with Stale-While-Revalidate caching.
    """
    client = await TelegramClientManager.get_client()
    return await cinema_catalog.get_videos(client, chat_id, limit, offset_id, force_refresh)


@router.post("/api/proxy/trigger")
async def trigger_proxy_download(
    request: Request,
    chat_id: Optional[Union[int, str]] = None,
    message_id: Optional[int] = None,
    manager: Optional[str] = "auto",
):
    from app.config import PROXY_HOST, PROXY_PORT
    body_data = {}
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body_data = await request.json()
        except Exception:
            body_data = {}

    target_chat_id = chat_id if chat_id is not None else body_data.get("chat_id")
    target_msg_id = message_id if message_id is not None else body_data.get("message_id")
    target_manager = manager if (manager and manager != "auto") else body_data.get("manager", "auto")
    provided_url = body_data.get("url")

    if provided_url:
        if str(provided_url).startswith("http://") or str(provided_url).startswith("https://"):
            url = str(provided_url)
        else:
            rel = str(provided_url) if str(provided_url).startswith("/") else "/" + str(provided_url)
            url = f"http://{PROXY_HOST}:{PROXY_PORT}{rel}"
    elif target_chat_id is not None and target_msg_id is not None:
        url = f"http://{PROXY_HOST}:{PROXY_PORT}/dl/{target_chat_id}/{target_msg_id}"
    else:
        raise HTTPException(status_code=400, detail="Missing chat_id/message_id or download URL")

    installed = detect_managers()
    if target_manager == "auto" or not target_manager:
        target_manager = "fdm" if "fdm" in installed else ("aria2" if "aria2" in installed else "neat")

    if "?" in url:
        if "dm=" not in url:
            url = f"{url}&dm=1"
    else:
        url = f"{url}?dm=1"

    ok = await trigger_manager(target_manager, url, installed)
    return {
        "success": ok,
        "manager": target_manager,
        "url": url,
    }


@router.get("/api/media/cache/status")
@router.get("/api/media/cache_status")
async def get_stream_cache_status():
    """Returns local stream cache usage statistics."""
    return stream_cache_service.get_cache_stats()


@router.post("/api/media/cache/clear", dependencies=[Depends(verify_auth)])
@router.post("/api/media/cache_clear", dependencies=[Depends(verify_auth)])
async def clear_stream_cache():
    """Clears all local cached media chunks."""
    cleared = stream_cache_service.clear_cache()
    return {"success": True, "cleared_files": cleared}


@router.get("/api/media/buffer/stats")
async def get_stream_buffer_stats():
    """Returns real-time RAM sliding buffer telemetry."""
    return _SLIDING_STREAM_BUFFER.get_stats()




@router.get("/api/media/mobile_info/{chat_id}/{message_id}", dependencies=[Depends(verify_auth)])
async def get_mobile_playback_info(chat_id: str, message_id: int, request: Request, filename: Optional[str] = None):
    """Returns scoped, expiring playback URLs and an embedded QR image for VLC/mobile clients."""
    from app.config import PROXY_PORT
    host = request.headers.get("host", f"127.0.0.1:{PROXY_PORT}")
    scheme = request.url.scheme or "http"
    clean_fn = filename or f"video_{message_id}.mp4"
    if clean_fn in ("thumb", "video.mp4") or clean_fn.startswith("tg_media_") or clean_fn.startswith("video_"):
        clean_cid = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
        cached_msg = sniffer_service._message_cache.get((clean_cid, message_id)) or sniffer_service._message_cache.get((str(clean_cid), message_id))
        if not cached_msg:
            try:
                client = await TelegramClientManager.get_client()
                if client and client.is_connected():
                    cached_msg = await get_streamable_message(client, clean_cid, message_id)
            except Exception:
                pass
        if cached_msg and getattr(cached_msg, "file", None) and getattr(cached_msg.file, "name", None):
            clean_fn = auto_rename(cached_msg.file.name)

    import urllib.parse
    import base64
    enc_name = urllib.parse.quote(clean_fn)
    # The capability is scoped to this chat/message and expires after four hours. VLC
    # can use it without receiving the server's permanent API token.
    playback_token = playback_grants.issue(chat_id, message_id)
    token_query = urllib.parse.urlencode({"playback_token": playback_token})
    stream_url = f"{scheme}://{host}/dl/{urllib.parse.quote(str(chat_id), safe='')}/{message_id}/{enc_name}?{token_query}"
    local_stream_url = f"http://127.0.0.1:{PROXY_PORT}/dl/{urllib.parse.quote(str(chat_id), safe='')}/{message_id}/{enc_name}?{token_query}"
    sub_file = subtitle_service.get_cached_subtitle(clean_fn)

    from app.services.network_service import network_service
    lan_ip = network_service.get_lan_ip()
    lan_stream_url = f"http://{lan_ip}:{PROXY_PORT}/dl/{urllib.parse.quote(str(chat_id), safe='')}/{message_id}/{enc_name}?{token_query}"
    # Embed the QR PNG in the authenticated response; an <img> request cannot attach
    # the application's API auth headers, and the QR endpoint must remain private.
    qr_bytes = network_service.generate_qr(lan_stream_url)
    qr_data_url = "data:image/png;base64," + base64.b64encode(qr_bytes).decode("ascii")
    lan_qrcode_url = f"/api/system/qrcode?text={urllib.parse.quote(lan_stream_url)}"

    local_subtitle_url = f"http://127.0.0.1:{PROXY_PORT}/api/media/subtitles/download/{urllib.parse.quote(str(chat_id), safe='')}/{message_id}?{token_query}"
    lan_subtitle_url = f"http://{lan_ip}:{PROXY_PORT}/api/media/subtitles/download/{urllib.parse.quote(str(chat_id), safe='')}/{message_id}?{token_query}"

    return {
        "chat_id": str(chat_id),
        "message_id": message_id,
        "filename": clean_fn,
        "stream_url": stream_url,
        "local_stream_url": local_stream_url,
        "has_subtitles": bool(sub_file and sub_file.exists()),
        "subtitles_url": local_subtitle_url if (sub_file and sub_file.exists()) else None,
        "lan_subtitles_url": lan_subtitle_url,
        "host": host,
        "lan_ip": lan_ip,
        "lan_stream_url": lan_stream_url,
        "lan_qrcode_url": lan_qrcode_url,
        "lan_qrcode_data_url": qr_data_url,
        "playback_token_expires_in_seconds": 365 * 24 * 60 * 60
    }


class VlcPlayPayload(BaseModel):
    chat_id: Union[int, str]
    message_id: int
    filename: Optional[str] = None


@router.get("/api/media/prewarm/{chat_id}/{message_id}", dependencies=[Depends(verify_auth)])
@router.post("/api/media/prewarm/{chat_id}/{message_id}", dependencies=[Depends(verify_auth)])
async def prewarm_media_endpoint(chat_id: str, message_id: int):
    client = await TelegramClientManager.get_client()
    clean_chat_id: Union[int, str] = chat_id
    if clean_chat_id != "me":
        try:
            clean_chat_id = int(chat_id)
        except ValueError:
            pass
    msg = await get_streamable_message(client, clean_chat_id, message_id)
    if msg:
        asyncio.create_task(prewarm_media_stream(client, clean_chat_id, message_id, msg))
        return {"status": "ok", "prewarming": True, "chat_id": str(clean_chat_id), "message_id": message_id}
    return {"status": "not_found", "prewarming": False, "chat_id": str(clean_chat_id), "message_id": message_id}


@router.post("/api/media/vlc/play", dependencies=[Depends(verify_auth)])
async def play_desktop_vlc(payload: VlcPlayPayload, request: Request):
    return await launch_desktop_vlc(str(payload.chat_id), payload.message_id, request, payload.filename)


@router.post("/api/media/vlc/launch/{chat_id}/{message_id}", dependencies=[Depends(verify_auth)])
async def launch_desktop_vlc(chat_id: str, message_id: int, request: Request, filename: Optional[str] = None):
    """Launch VLC on the TG Power Suite host; deliberately restricted to direct loopback clients."""
    import shutil
    import subprocess
    from app.config import PROXY_PORT

    peer = getattr(request.client, "host", "") if request.client else ""
    if peer not in ("127.0.0.1", "::1", "localhost", "testclient", "testserver"):
        raise HTTPException(status_code=403, detail="Desktop VLC launch is available only from the local machine.")
    if any(header in request.headers for header in ("cf-connecting-ip", "x-forwarded-for", "x-real-ip")):
        raise HTTPException(status_code=403, detail="Proxied VLC launch requests are not allowed.")

    vlc_bin = shutil.which("vlc")
    if not vlc_bin:
        raise HTTPException(status_code=503, detail="VLC is not installed or not available on PATH. On Arch Linux, install it with: sudo pacman -S vlc.")

    clean_chat_id: Union[int, str] = chat_id
    my_info = TelegramClientManager.get_cached_me() or {}
    my_id = my_info.get("id") or getattr(sniffer_service, "_own_user_id", None)
    if chat_id == "me" or (my_id and str(chat_id) in (str(my_id), f"-100{my_id}")):
        clean_chat_id = "me"
    else:
        try:
            clean_chat_id = int(chat_id)
        except ValueError:
            clean_chat_id = chat_id

    client = await TelegramClientManager.get_client()
    message = await get_streamable_message(client, clean_chat_id, message_id)
    if (not message or not getattr(message, "media", None) or not hasattr(message, "file") or not message.file) and clean_chat_id != "me":
        message = await get_streamable_message(client, clean_chat_id, message_id, force_refresh=True)
    if (not message or not getattr(message, "media", None) or not hasattr(message, "file") or not message.file) and clean_chat_id != "me":
        me_msg = await get_streamable_message(client, "me", message_id)
        if me_msg and getattr(me_msg, "media", None) and hasattr(me_msg, "file") and me_msg.file:
            message = me_msg
            clean_chat_id = "me"
    if not message or not getattr(message, "media", None) or not hasattr(message, "file") or not message.file:
        asyncio.create_task(cinema_catalog.remove_deleted_video(clean_chat_id, message_id))
        display_name = filename or f"Message {message_id}"
        raise HTTPException(
            status_code=404,
            detail=f"Cannot play '{display_name}': Message not found or temporarily unavailable. Please retry."
        )

    file_size = int(getattr(getattr(message, "file", None), "size", 0))
    asyncio.create_task(stream_buffer.prewarm_media(client, clean_chat_id, message_id, file_size))

    import urllib.parse
    safe_name = urllib.parse.quote(filename or f"video_{message_id}.mp4")
    grant = playback_grants.issue(chat_id, message_id)
    url = f"http://127.0.0.1:{PROXY_PORT}/dl/{urllib.parse.quote(str(chat_id), safe='')}/{message_id}/{safe_name}?playback_token={urllib.parse.quote(grant)}"
    prog = await playback_tracker.aget_playback_progress(str(clean_chat_id), message_id)
    start_sec = int(prog.get("last_position_seconds", 0)) if prog else 0
    duration_sec = int(prog.get("duration_seconds", 0)) if prog else 0
    is_finished = bool(prog.get("finished", False)) if prog else False
    if is_finished or (duration_sec > 0 and start_sec >= duration_sec - 15):
        start_sec = 0

    vlc_args = [
        vlc_bin,
        "--no-video-title-show",
        "--network-caching=2000",
        "--file-caching=2000",
        "--http-reconnect",
        "--no-spdif",
        "--avcodec-hw=any",
    ]
    if start_sec > 2:
        vlc_args.append(f"--start-time={start_sec}")
        logger.info(f"VLC [{safe_name}] Resuming at {start_sec}s")

    vlc_args.append(url)

    from app.services.manager_detector import get_desktop_env
    desktop_env = get_desktop_env()

    try:
        proc = subprocess.Popen(
            vlc_args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=desktop_env,
            start_new_session=True,
            close_fds=True,
        )
        if hasattr(proc, "wait"):
            asyncio.create_task(asyncio.to_thread(proc.wait))
    except OSError as exc:
        logger.exception("Could not launch VLC")
        raise HTTPException(status_code=500, detail=f"Failed to launch VLC: {exc}")
    
    msg = f"VLC launched (resumed at {start_sec}s)." if start_sec > 10 else "VLC launched."
    return {"success": True, "pid": proc.pid, "player": "vlc", "resumed_seconds": start_sec, "message": msg}


@router.post("/api/media/vlc/play_batch", dependencies=[Depends(verify_auth)])
@router.post("/api/media/vlc/launch_batch", dependencies=[Depends(verify_auth)])
async def launch_vlc_batch(payload: Dict[str, Any], request: Request):
    """
    Launch VLC with an ordered batch/season of media streams queued for continuous binge watching.
    Restricted to direct loopback clients.
    """
    import shutil
    import subprocess
    import urllib.parse
    from app.config import PROXY_PORT

    peer = getattr(request.client, "host", "") if request.client else ""
    if peer not in ("127.0.0.1", "::1", "localhost", "testclient", "testserver"):
        raise HTTPException(status_code=403, detail="Desktop VLC launch is available only from the local machine.")
    if any(header in request.headers for header in ("cf-connecting-ip", "x-forwarded-for", "x-real-ip")):
        raise HTTPException(status_code=403, detail="Proxied VLC launch requests are not allowed.")

    vlc_bin = shutil.which("vlc")
    if not vlc_bin:
        raise HTTPException(status_code=503, detail="VLC is not installed or not available on PATH.")

    items = payload.get("items", [])
    title = payload.get("title", "Season Binge")
    if not items:
        raise HTTPException(status_code=400, detail="No video items provided for batch playback.")

    vlc_args = [
        vlc_bin,
        "--no-video-title-show",
        "--network-caching=2000",
        "--file-caching=2000",
        "--http-reconnect",
        "--no-spdif",
        "--avcodec-hw=any",
    ]

    for item in items:
        cid = item.get("chat_id")
        mid = item.get("message_id")
        fname = item.get("filename") or f"video_{mid}.mp4"
        if not cid or not mid:
            continue
        grant = playback_grants.issue(str(cid), int(mid))
        safe_name = urllib.parse.quote(fname)
        safe_cid = urllib.parse.quote(str(cid), safe="")
        ep_url = f"http://127.0.0.1:{PROXY_PORT}/dl/{safe_cid}/{mid}/{safe_name}?playback_token={urllib.parse.quote(grant)}"
        vlc_args.append(ep_url)

    if len(vlc_args) <= 7:
        raise HTTPException(status_code=400, detail="No valid media streams could be constructed.")

    from app.services.manager_detector import get_desktop_env
    desktop_env = get_desktop_env()

    try:
        proc = subprocess.Popen(
            vlc_args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=desktop_env,
            start_new_session=True,
            close_fds=True,
        )
        if hasattr(proc, "wait"):
            asyncio.create_task(asyncio.to_thread(proc.wait))
    except OSError as exc:
        logger.exception("Could not launch batch VLC")
        raise HTTPException(status_code=500, detail=f"Failed to launch VLC: {exc}")

    return {
        "success": True,
        "pid": proc.pid,
        "player": "vlc",
        "queued_items": len(items),
        "message": f"VLC launched with {len(items)} episodes of {title}.",
    }


@router.post("/api/media/vlc/batch_playlist", dependencies=[Depends(verify_auth)])
async def generate_batch_playlist(payload: Dict[str, Any], request: Request):
    """
    Generates an .m3u playlist file for a batch of videos / web series season.
    """
    import urllib.parse
    from app.config import PROXY_HOST, PROXY_PORT

    items = payload.get("items", [])
    title = payload.get("title", "Season_Playlist")
    if not items:
        raise HTTPException(status_code=400, detail="No video items provided for batch playlist.")

    host = request.headers.get("host") or f"{PROXY_HOST}:{PROXY_PORT}"
    scheme = "https" if request.headers.get("x-forwarded-proto") == "https" else "http"

    lines = ["#EXTM3U"]
    for item in items:
        cid = item.get("chat_id")
        mid = item.get("message_id")
        fname = item.get("filename") or f"video_{mid}.mp4"
        if fname.startswith("video_") and not item.get("filename"):
            clean_cid = int(cid) if str(cid).lstrip("-").isdigit() else cid
            cached_msg = sniffer_service._message_cache.get((clean_cid, mid)) or sniffer_service._message_cache.get((str(clean_cid), mid))
            if cached_msg and getattr(cached_msg, "file", None) and getattr(cached_msg.file, "name", None):
                fname = auto_rename(cached_msg.file.name)
        if not cid or not mid:
            continue
        grant = playback_grants.issue(str(cid), int(mid))
        safe_name = urllib.parse.quote(fname)
        safe_cid = urllib.parse.quote(str(cid), safe="")
        ep_url = f"{scheme}://{host}/dl/{safe_cid}/{mid}/{safe_name}?playback_token={urllib.parse.quote(grant)}"
        lines.append(f"#EXTINF:-1,{fname}")
        lines.append("#EXTVLCOPT:network-caching=2000")
        lines.append("#EXTVLCOPT:file-caching=2000")
        lines.append(ep_url)

    clean_title = "".join(c for c in title if c.isalnum() or c in " .-_").strip() or "playlist"
    m3u_content = "\n".join(lines)
    return Response(
        content=m3u_content,
        media_type="audio/x-mpegurl",
        headers={
            "Content-Disposition": f'attachment; filename="{clean_title}.m3u"',
            "Content-Type": "audio/x-mpegurl; charset=utf-8",
        },
    )


@router.get("/api/media/vlc/playlist/{chat_id}/{message_id}", dependencies=[Depends(verify_auth)])
@router.get("/api/media/vlc/playlist/{chat_id}/{message_id}/{filename:path}", dependencies=[Depends(verify_auth)])
async def generate_single_vlc_playlist(chat_id: str, message_id: int, request: Request, filename: Optional[str] = None):
    import urllib.parse
    from app.config import PROXY_HOST, PROXY_PORT
    clean_name = filename or f"video_{message_id}.mp4"
    if clean_name.endswith(".m3u"):
        clean_name = clean_name[:-4]
    if clean_name.startswith("video_") and not filename:
        clean_cid = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
        cached_msg = sniffer_service._message_cache.get((clean_cid, message_id)) or sniffer_service._message_cache.get((str(clean_cid), message_id))
        if cached_msg and getattr(cached_msg, "file", None) and getattr(cached_msg.file, "name", None):
            clean_name = auto_rename(cached_msg.file.name)
    host = (request.headers.get("host") if request else None) or f"{PROXY_HOST}:{PROXY_PORT}"
    scheme = "https" if (request and request.headers.get("x-forwarded-proto") == "https") else "http"
    grant = playback_grants.issue(str(chat_id), int(message_id))
    safe_name = urllib.parse.quote(clean_name)
    safe_cid = urllib.parse.quote(str(chat_id), safe="")
    ep_url = f"{scheme}://{host}/dl/{safe_cid}/{message_id}/{safe_name}?playback_token={urllib.parse.quote(grant)}"
    lines = [
        "#EXTM3U",
        f"#EXTINF:-1,{clean_name}",
        "#EXTVLCOPT:network-caching=2000",
        "#EXTVLCOPT:file-caching=2000",
        ep_url
    ]
    m3u_content = "\n".join(lines)
    return Response(
        content=m3u_content,
        media_type="audio/x-mpegurl",
        headers={
            "Content-Disposition": f'attachment; filename="{clean_name}.m3u"',
            "Content-Type": "audio/x-mpegurl; charset=utf-8",
        },
    )




@router.get("/api/media/continue_watching", dependencies=[Depends(verify_auth)])
async def get_continue_watching():
    """Returns list of recently watched media ready for 1-click resume."""
    return await playback_tracker.aget_continue_watching(limit=12)


@router.delete("/api/media/continue_watching/{chat_id}/{message_id}", dependencies=[Depends(verify_auth)])
async def dismiss_continue_watching(chat_id: str, message_id: int):
    """Dismisses an item from continue watching."""
    await playback_tracker.aremove_progress(chat_id, message_id)
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


@router.post("/api/media/playback_progress", dependencies=[Depends(verify_auth)])
async def update_playback_progress_api(payload: PlaybackProgressPayload):
    """Updates watch progress for a video with chunk coordinates."""
    await playback_tracker.aupdate_progress(
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


from app.routes.settings import (
    OfflineSaverPayload,
    get_offline_saver_settings,
    set_offline_saver_settings
)


