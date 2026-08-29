import os
import re
import time
import logging
import asyncio
import email.utils
from typing import Optional
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from app.telegram_client import TelegramClientManager
from app.services.sniffer_service import auto_rename, sniffer_service
from app.services.manager_detector import trigger_manager, detect_managers

logger = logging.getLogger("proxy_route")

router = APIRouter(tags=["proxy"])

_MESSAGE_CACHE = {}

from typing import Optional, Union

@router.get("/dl/{chat_id}/{message_id}")
@router.get("/dl/{chat_id}/{message_id}/{filename}")
async def handle_proxy_download(chat_id: str, message_id: int, request: Request, filename: Optional[str] = None):
    """
    High-Speed HTTP Streaming Proxy for Telegram Media.
    Supports HTTP Range requests (resumption & multi-part download managers like FDM, aria2).
    """
    client = await TelegramClientManager.get_client()
    if not client or not client.is_connected():
        raise HTTPException(status_code=503, detail="Telegram client is not connected.")

    # Parse numeric or entity chat_id
    clean_chat_id: Union[int, str] = chat_id
    if chat_id != "me":
        try:
            clean_chat_id = int(chat_id)
        except ValueError:
            clean_chat_id = chat_id

    message = sniffer_service._message_cache.get((clean_chat_id, message_id))
    if not message:
        try:
            message = await client.get_messages(clean_chat_id, ids=message_id)
        except Exception as e:
            logger.warning(f"Could not fetch message {message_id} from {clean_chat_id}: {e}")

    if not message or not message.media or not hasattr(message, "file") or not message.file:
        raise HTTPException(status_code=404, detail="Message not found or contains no media.")

    file_size = int(message.file.size)
    raw_name = message.file.name if message.file.name else f"tg_media_{message_id}.bin"
    raw_name = "".join([c for c in raw_name if (c.isalnum() or c in " .-_()")]).strip()
    clean_name = filename or auto_rename(raw_name)

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
    align_unit = 512 * 1024
    aligned_start = start - (start % align_unit)
    discard_bytes = start - aligned_start
    aligned_limit = length + discard_bytes

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
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Content-Length": str(length),
        "ETag": f'"{chat_id}_{message_id}_{file_size}"',
    }

    if hasattr(message, "date") and message.date:
        headers["Last-Modified"] = email.utils.formatdate(timeval=message.date.timestamp(), usegmt=True)

    async def stream_generator():
        bytes_written = 0
        first_chunk = True
        try:
            async for chunk in client.iter_download(
                message.media,
                offset=aligned_start,
                limit=aligned_limit,
                request_size=align_unit,
                chunk_size=align_unit,
            ):
                if first_chunk:
                    first_chunk = False
                    if discard_bytes > 0:
                        chunk = chunk[discard_bytes:]

                if chunk:
                    remaining = length - bytes_written
                    if len(chunk) > remaining:
                        chunk = chunk[:remaining]
                    yield chunk
                    bytes_written += len(chunk)
                    if bytes_written >= length:
                        break
        except (ConnectionResetError, ConnectionAbortedError, asyncio.CancelledError):
            logger.debug(f"Client disconnected during streaming of {clean_name}")
        except Exception as err:
            logger.warning(f"Error during streaming download: {err}")

    return StreamingResponse(
        stream_generator(),
        status_code=status_code,
        headers=headers
    )


@router.get("/dl/{chat_id}/{message_id}/thumb")
async def get_media_thumbnail(chat_id: str, message_id: int):
    """Fetches and caches the preview thumbnail for a Telegram video/document."""
    client = await TelegramClientManager.get_client()
    if not client or not client.is_connected():
        raise HTTPException(status_code=503, detail="Telegram client is not connected.")

    clean_chat_id: Union[int, str] = chat_id
    if chat_id != "me":
        try:
            clean_chat_id = int(chat_id)
        except ValueError:
            clean_chat_id = chat_id

    try:
        message = sniffer_service._message_cache.get((clean_chat_id, message_id))
        if not message:
            message = await client.get_messages(clean_chat_id, ids=message_id)

        if not message or not message.media:
            raise HTTPException(status_code=404, detail="Media not found.")

        # Download thumbnail into memory buffer
        thumb_bytes = await client.download_media(message.media, thumb=-1, file=bytes)
        if not thumb_bytes:
            raise HTTPException(status_code=404, detail="No thumbnail available for this media.")

        return Response(
            content=thumb_bytes,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=86400, immutable"}
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Failed to fetch thumbnail for {chat_id}/{message_id}: {e}")
        raise HTTPException(status_code=404, detail="Failed to load thumbnail.")


@router.get("/api/media/videos/{chat_id}")
async def get_chat_videos(chat_id: str, limit: int = 50, offset_id: int = 0):
    """
    Fetches video archives from a Telegram channel, group, or Saved Messages for the Cinema tab.
    Extracts duration, resolution dimensions, file size, and direct stream URLs.
    """
    client = await TelegramClientManager.get_client()
    if not client or not client.is_connected() or not await client.is_user_authorized():
        raise HTTPException(status_code=401, detail="Telegram client not authorized.")

    clean_chat_id: Union[int, str] = chat_id
    if chat_id != "me":
        try:
            clean_chat_id = int(chat_id)
        except ValueError:
            clean_chat_id = chat_id

    videos = []
    try:
        from telethon.tl.types import InputMessagesFilterVideo, DocumentAttributeVideo, DocumentAttributeFilename

        # Fetch messages with video filter
        messages = await client.get_messages(
            clean_chat_id,
            limit=limit,
            offset_id=offset_id,
            filter=InputMessagesFilterVideo
        )

        for msg in messages:
            if not msg or not msg.media or not hasattr(msg, "file") or not msg.file:
                continue

            # Cache in sniffer message cache for instant stream resolution
            sniffer_service._message_cache[(clean_chat_id, msg.id)] = msg

            # Extract video metadata
            duration = 0
            width = 0
            height = 0
            filename = msg.file.name if msg.file.name else f"video_{msg.id}.mp4"

            if hasattr(msg.media, "document") and msg.media.document:
                for attr in msg.media.document.attributes:
                    if isinstance(attr, DocumentAttributeVideo):
                        duration = int(getattr(attr, "duration", 0))
                        width = int(getattr(attr, "w", 0))
                        height = int(getattr(attr, "h", 0))
                    elif isinstance(attr, DocumentAttributeFilename):
                        if getattr(attr, "file_name", None):
                            filename = attr.file_name

            raw_name = "".join([c for c in filename if (c.isalnum() or c in " .-_()")]).strip()
            clean_name = auto_rename(raw_name)

            has_thumb = False
            if hasattr(msg.media, "document") and msg.media.document and getattr(msg.media.document, "thumbs", None):
                has_thumb = True
            elif hasattr(msg.media, "photo"):
                has_thumb = True

            videos.append({
                "message_id": msg.id,
                "chat_id": str(chat_id),
                "filename": clean_name,
                "file_size": int(msg.file.size),
                "duration": duration,
                "width": width,
                "height": height,
                "date": int(msg.date.timestamp()) if msg.date else 0,
                "mime_type": msg.file.mime_type or "video/mp4",
                "has_thumb": has_thumb,
                "stream_url": f"/dl/{chat_id}/{msg.id}/{clean_name}",
                "thumb_url": f"/dl/{chat_id}/{msg.id}/thumb" if has_thumb else None
            })

    except Exception as e:
        logger.error(f"Error querying videos for {chat_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to query videos: {str(e)}")

    return {
        "chat_id": str(chat_id),
        "count": len(videos),
        "videos": videos
    }


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
