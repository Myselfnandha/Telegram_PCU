import os
import re
import asyncio
import logging
from typing import Optional, Union, Dict, Any, List

from fastapi import APIRouter, Response
from telethon import utils
from telethon.tl.types import Photo, PhotoSize, PhotoSizeProgressive, PhotoStrippedSize, PhotoCachedSize

from app.telegram_client import TelegramClientManager
from app.services.cinema_catalog import get_streamable_message
from app.services.stream_cache import stream_cache_service
from app.routes.history import get_cinema_cached_videos_db

logger = logging.getLogger("proxy_thumbnails")

router = APIRouter(tags=["proxy_thumbnails"])

_THUMB_CACHE_DIR = os.path.expanduser("~/.cache/tg_power_suite/thumbs")
_PREVIEW_CACHE_DIR = os.path.expanduser("~/.cache/tg_power_suite/previews")
os.makedirs(_THUMB_CACHE_DIR, exist_ok=True)
os.makedirs(_PREVIEW_CACHE_DIR, exist_ok=True)
_THUMB_SEMAPHORE = asyncio.Semaphore(16)

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
        clean = re.sub(r"^(?:vip\s*linkzz|linkzz|tamizhan|org|com|net|tamilmv|tamilblasters|onlyhd4u|mspxmovies|sklink\s*files?|cszmovies|team\s*hdt|trending\s*tamizhaas?|sklink|prakytv|movieemanhd)\s+", "", clean, flags=re.I)
        clean = re.sub(r"\b(1080p|720p|480p|4k|2160p|bluray|web-?dl|webrip|remux|x264|x265|hevc|ddp|atmos|5\.1|true|remastered|v1\.neil|telumkv|tpu|clean|predvd|combined|org\s*audio|tamil\s*audio|hindi\s*audio|telugu\s*audio|yts\s*bz|yts|proper|hdrip|dvdrip|ta|tam|hin|tel|eng|sub|esub)\b", " ", clean, flags=re.I)
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

    for fname in unique_candidates:
        c_path = os.path.join(_THUMB_CACHE_DIR, fname)
        if os.path.exists(c_path) and os.path.getsize(c_path) > 0:
            sz = os.path.getsize(c_path)
            if 500 <= sz <= 1400:
                try:
                    os.remove(c_path)
                except Exception:
                    pass
            else:
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
        message = await get_streamable_message(client, clean_chat_id, message_id)
        if not message or not getattr(message, "media", None):
            fn = ""
            try:
                from app.services.playback_tracker import playback_tracker
                rec = playback_tracker.get_record(clean_chat_id, message_id) or playback_tracker.get_record(chat_id, message_id)
                if rec:
                    fn = rec.get("filename") or rec.get("display_title") or ""
            except Exception:
                pass
            if not fn:
                try:
                    for cid_candidate in [clean_chat_id, chat_id, str_cid]:
                        cached_data = await get_cinema_cached_videos_db(cid_candidate)
                        if cached_data and "videos" in cached_data:
                            for v in cached_data["videos"]:
                                if v.get("message_id") == message_id:
                                    fn = v.get("filename") or ""
                                    break
                        if fn:
                            break
                except Exception:
                    pass
            if fn:
                web_tb = await search_web_poster(fn)
                if web_tb and len(web_tb) >= 1500:
                    for fname in unique_candidates:
                        try:
                            with open(os.path.join(_THUMB_CACHE_DIR, fname), "wb") as f:
                                f.write(web_tb)
                        except Exception:
                            pass
                    return Response(
                        content=web_tb,
                        media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400, immutable"}
                    )
            return Response(
                content=DEFAULT_VIDEO_PLACEHOLDER_SVG,
                media_type="image/svg+xml",
                headers={"Cache-Control": "public, max-age=3600"}
            )

        thumb_bytes = None
        micro_thumb = None
        doc = getattr(message.media, "document", None) or (message.media if hasattr(message.media, "thumbs") else None)
        thumbs = getattr(doc, "thumbs", None) or getattr(message.media, "thumbs", None) or getattr(message.media, "sizes", None) or []

        best_thumb = None
        if thumbs:
            photo_sizes = [t for t in thumbs if isinstance(t, (PhotoSize, PhotoSizeProgressive))]
            if photo_sizes:
                best_thumb = max(photo_sizes, key=lambda t: getattr(t, "size", 0) or max(getattr(t, "sizes", [0])))

        if best_thumb is not None or thumbs or isinstance(message.media, Photo):
            async with _THUMB_SEMAPHORE:
                target = best_thumb if best_thumb is not None else -1
                try:
                    tb = await asyncio.wait_for(client.download_media(message, thumb=target, file=bytes), timeout=3.5)
                    if tb and len(tb) >= 1500:
                        thumb_bytes = tb
                except Exception:
                    try:
                        tb = await asyncio.wait_for(client.download_media(message, thumb=-1, file=bytes), timeout=3.0)
                        if tb and len(tb) >= 1500:
                            thumb_bytes = tb
                    except Exception:
                        pass

        if not thumb_bytes:
            try:
                prev_msgs = await asyncio.wait_for(client.get_messages(clean_chat_id, ids=[message_id - 1, message_id + 1]), timeout=2.0)
                for pm in (prev_msgs or []):
                    if pm and getattr(pm, "photo", None):
                        async with _THUMB_SEMAPHORE:
                            tb = await asyncio.wait_for(client.download_media(pm, thumb=-1, file=bytes), timeout=3.0)
                            if tb and len(tb) >= 1500:
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
            if not fn:
                try:
                    from app.services.playback_tracker import playback_tracker
                    rec = playback_tracker.get_record(clean_chat_id, message_id) or playback_tracker.get_record(chat_id, message_id)
                    if rec:
                        fn = rec.get("filename") or rec.get("display_title") or ""
                except Exception:
                    pass
            if not fn:
                try:
                    for cid_candidate in [clean_chat_id, chat_id, str_cid]:
                        cached_data = await get_cinema_cached_videos_db(cid_candidate)
                        if cached_data and "videos" in cached_data:
                            for v in cached_data["videos"]:
                                if v.get("message_id") == message_id:
                                    fn = v.get("filename") or ""
                                    break
                        if fn:
                            break
                except Exception:
                    pass
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
                    if os.path.exists(tmp_frame) and os.path.getsize(tmp_frame) >= 1500:
                        with open(tmp_frame, "rb") as f:
                            thumb_bytes = f.read()
                        try:
                            os.remove(tmp_frame)
                        except Exception:
                            pass
            except Exception:
                pass

        if not thumb_bytes and thumbs:
            for t in thumbs:
                if isinstance(t, PhotoCachedSize) and getattr(t, "bytes", None):
                    micro_thumb = t.bytes
                    break
                elif isinstance(t, PhotoStrippedSize) and getattr(t, "bytes", None):
                    try:
                        micro_thumb = utils.stripped_photo_to_jpg(t.bytes)
                        if micro_thumb:
                            break
                    except Exception:
                        pass

        if thumb_bytes and len(thumb_bytes) >= 1500:
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

        if micro_thumb:
            return Response(
                content=micro_thumb,
                media_type="image/jpeg",
                headers={"Cache-Control": "public, max-age=5"}
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
