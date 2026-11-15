import os
import asyncio
import logging
from typing import Union, Optional

from fastapi import APIRouter, HTTPException, Request, Response, Depends

from app.auth import verify_auth
from app.telegram_client import TelegramClientManager
from app.services.cinema_catalog import get_streamable_message
from app.services.subtitle_service import subtitle_service
from app.services.sniffer_service import sniffer_service

logger = logging.getLogger("proxy_subtitles")

router = APIRouter(tags=["proxy_subtitles"])

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

    raw_name = message.file.name if message.file else f"media_{message_id}.bin"
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
