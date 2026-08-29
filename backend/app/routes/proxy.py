import os
import re
import time
import logging
import asyncio
import email.utils
from typing import Optional, Dict, Any, List, Union, cast
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from app.telegram_client import TelegramClientManager
from app.services.sniffer_service import auto_rename, sniffer_service
from app.services.manager_detector import trigger_manager, detect_managers

logger = logging.getLogger("proxy_route")

router = APIRouter(tags=["proxy"])

_MESSAGE_CACHE = {}

from app.services.stream_cache import stream_cache_service

@router.get("/dl/{chat_id}/{message_id}")
@router.get("/dl/{chat_id}/{message_id}/{filename}")
async def handle_proxy_download(chat_id: str, message_id: int, request: Request, filename: Optional[str] = None):
    """
    High-Speed HTTP Streaming Proxy with Zero-Lag Local Cache & Range Resumption.
    If media is cached on local disk, serves instantly in <1ms (HTTP 206 Partial Content).
    Otherwise streams from Telegram MTProto while caching in background.
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
    import urllib.parse
    clean_name = urllib.parse.unquote(filename) if filename else auto_rename(raw_name)

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

    # 1. Check if complete media file is in local cache -> Instant <1ms serve
    cached_file = stream_cache_service.get_cached_file(str(chat_id), message_id)
    if cached_file and cached_file.exists() and cached_file.stat().st_size == file_size:
        async def cached_streamer():
            try:
                with open(cached_file, "rb") as f:
                    f.seek(start)
                    rem = length
                    while rem > 0:
                        read_sz = min(rem, 128 * 1024)
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

    # 2. Otherwise stream from MTProto and buffer in background
    cache_path = stream_cache_service.get_cache_path(str(chat_id), message_id, clean_name)
    part_path = cache_path.with_suffix(".part")

    async def stream_generator():
        bytes_written = 0
        first_chunk = True
        cache_f = None
        if start == 0 and not cache_path.exists():
            try:
                cache_f = open(part_path, "wb")
            except Exception:
                pass

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
                    
                    if cache_f:
                        try:
                            cache_f.write(chunk)
                        except Exception:
                            pass

                    yield chunk
                    bytes_written += len(chunk)
                    await asyncio.sleep(0)
                    if bytes_written >= length:
                        break

            if cache_f:
                cache_f.close()
                cache_f = None
                if bytes_written == file_size:
                    part_path.rename(cache_path)
                    stream_cache_service.evict_if_needed()
        except (ConnectionResetError, ConnectionAbortedError, asyncio.CancelledError):
            logger.debug(f"Client disconnected during streaming of {clean_name}")
        except Exception as err:
            logger.warning(f"Error during streaming download: {err}")
        finally:
            if cache_f:
                try:
                    cache_f.close()
                except Exception:
                    pass

    return StreamingResponse(
        stream_generator(),
        status_code=status_code,
        headers=headers
    )


@router.get("/stream/{chat_id}/{message_id}")
@router.get("/stream/{chat_id}/{message_id}/{filename}")
async def handle_transmux_stream(
    chat_id: str,
    message_id: int,
    request: Request,
    filename: Optional[str] = None,
    audio: int = 0,
    ss: float = 0.0
):
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

    message = sniffer_service._message_cache.get((clean_chat_id, message_id))
    if not message:
        try:
            message = await client.get_messages(clean_chat_id, ids=message_id)
        except Exception as e:
            logger.warning(f"Could not fetch message {message_id} from {clean_chat_id}: {e}")

    if not message or not message.media or not hasattr(message, "file") or not message.file:
        raise HTTPException(status_code=404, detail="Message not found or contains no media.")

    raw_name = message.file.name if message.file.name else f"stream_{message_id}.mp4"
    import urllib.parse
    clean_name = urllib.parse.unquote(filename) if filename else auto_rename(raw_name)
    base_name = os.path.splitext(clean_name)[0] + ".mp4"

    # Check local cache
    cached_file = stream_cache_service.get_cached_file(str(chat_id), message_id)
    input_target = str(cached_file) if (cached_file and cached_file.exists()) else "pipe:0"
    chunk_unit = 128 * 1024  # 128KB ultra-fast low-latency chunks

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
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
            stderr=asyncio.subprocess.PIPE
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
            async for chunk in client.iter_download(
                message.media,
                offset=0,
                request_size=chunk_unit,
                chunk_size=chunk_unit
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
async def get_stream_subtitles(chat_id: str, message_id: int, sub_index: int):
    """Extracts embedded subtitle track and converts to WebVTT."""
    clean_chat_id: Union[int, str] = chat_id
    if chat_id != "me":
        try:
            clean_chat_id = int(chat_id)
        except ValueError:
            clean_chat_id = chat_id

    cache_key = (clean_chat_id, message_id, sub_index)
    if cache_key in _SUBTITLE_CACHE:
        return Response(content=_SUBTITLE_CACHE[cache_key], media_type="text/vtt")

    client = await TelegramClientManager.get_client()
    if not client or not client.is_connected():
        raise HTTPException(status_code=503, detail="Telegram client is not connected.")

    message = sniffer_service._message_cache.get((clean_chat_id, message_id))
    if not message:
        message = await client.get_messages(clean_chat_id, ids=message_id)

    if not message or not message.media:
        raise HTTPException(status_code=404, detail="Media not found.")

    sub_buffer = bytearray()
    try:
        async for chunk in client.iter_download(message.media, request_size=256*1024, chunk_size=256*1024):
            sub_buffer.extend(chunk)
            if len(sub_buffer) >= 1500 * 1024:
                break
    except Exception:
        pass

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-i", "pipe:0",
        "-map", f"0:s:{sub_index}",
        "-f", "webvtt",
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
        if not stdout_data or not stdout_data.startswith(b"WEBVTT"):
            stdout_data = b"WEBVTT\n\n1\n00:00:01.000 --> 00:00:05.000\n[Subtitles Active]\n"
        _SUBTITLE_CACHE[cache_key] = stdout_data
        return Response(content=stdout_data, media_type="text/vtt")
    except Exception as e:
        logger.warning(f"Subtitle extraction notice: {e}")
        return Response(content=b"WEBVTT\n\n", media_type="text/vtt")


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
    try:
        client = await TelegramClientManager.get_client()
        if not client or not client.is_connected() or not await client.is_user_authorized():
            raise HTTPException(status_code=401, detail="Telegram client not authorized.")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Could not connect to Telegram client: {e}")
        return {"chat_id": str(chat_id), "count": 0, "videos": []}

    clean_chat_id: Union[int, str] = chat_id
    if chat_id != "me":
        try:
            clean_chat_id = int(chat_id)
        except ValueError:
            clean_chat_id = chat_id

    videos = []
    try:
        from telethon.tl.types import InputMessagesFilterVideo, InputMessagesFilterDocument, DocumentAttributeVideo, DocumentAttributeFilename

        # Fetch both native video messages AND videos sent as documents (MKV, MP4, etc.)
        fetch_limit = max(limit, 80)
        v_task = client.get_messages(
            clean_chat_id,
            limit=fetch_limit,
            offset_id=offset_id,
            filter=InputMessagesFilterVideo
        )
        d_task = client.get_messages(
            clean_chat_id,
            limit=fetch_limit,
            offset_id=offset_id,
            filter=InputMessagesFilterDocument
        )

        v_msgs, d_msgs = await asyncio.gather(v_task, d_task, return_exceptions=True)
        if isinstance(v_msgs, Exception):
            v_msgs = []
        if isinstance(d_msgs, Exception):
            d_msgs = []

        seen_ids = set()
        combined_msgs = []
        for msg in list(v_msgs) + list(d_msgs):
            if not msg or msg.id in seen_ids or not hasattr(msg, "file") or not msg.file:
                continue
            seen_ids.add(msg.id)
            combined_msgs.append(msg)

        # Sort messages by ID descending (newest first)
        combined_msgs.sort(key=lambda m: m.id, reverse=True)

        video_extensions = (
            ".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts",
            ".flv", ".wmv", ".m4v", ".3gp", ".vob", ".mpg", ".mpeg"
        )

        for msg in combined_msgs:
            filename = msg.file.name if msg.file.name else f"video_{msg.id}.mp4"
            duration = 0
            width = 0
            height = 0

            if hasattr(msg.media, "document") and msg.media.document:
                for attr in msg.media.document.attributes:
                    if isinstance(attr, DocumentAttributeVideo):
                        duration = int(getattr(attr, "duration", 0))
                        width = int(getattr(attr, "w", 0))
                        height = int(getattr(attr, "h", 0))
                    elif isinstance(attr, DocumentAttributeFilename):
                        if getattr(attr, "file_name", None):
                            filename = attr.file_name

            # Check if this document/media is a video
            mime = msg.file.mime_type or ""
            is_video_media = (
                getattr(msg, "video", None) is not None or
                mime.startswith("video/") or
                any(filename.lower().endswith(ext) for ext in video_extensions)
            )

            if not is_video_media:
                continue

            # Cache in sniffer message cache for instant stream resolution
            sniffer_service._message_cache[(clean_chat_id, msg.id)] = msg

            raw_name = "".join([c for c in filename if (c.isalnum() or c in " .-_()")]).strip()
            clean_name = auto_rename(raw_name)

            has_thumb = False
            if hasattr(msg.media, "document") and msg.media.document and getattr(msg.media.document, "thumbs", None):
                has_thumb = True
            elif hasattr(msg.media, "photo"):
                has_thumb = True

            ext = os.path.splitext(clean_name)[1].lower()
            is_mkv = ext in (".mkv", ".avi", ".ts", ".flv", ".wmv", ".vob")

            import urllib.parse
            encoded_name = urllib.parse.quote(clean_name)
            base_clean = os.path.splitext(clean_name)[0]
            encoded_mp4 = urllib.parse.quote(f"{base_clean}.mp4")

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
                "is_mkv": is_mkv,
                "stream_url": f"/dl/{chat_id}/{msg.id}/{encoded_name}",
                "stream_transmux_url": f"/stream/{chat_id}/{msg.id}/{encoded_mp4}",
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


@router.get("/api/media/cache/status")
async def get_stream_cache_status():
    """Returns local stream cache usage statistics."""
    return stream_cache_service.get_cache_stats()


@router.post("/api/media/cache/clear")
async def clear_stream_cache():
    """Clears all local cached media chunks."""
    cleared = stream_cache_service.clear_cache()
    return {"success": True, "cleared_files": cleared}


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

    if not raw_url and chat_id and message_id:
        import urllib.parse
        encoded_name = urllib.parse.quote(filename)
        raw_url = f"http://127.0.0.1:{PROXY_PORT}/dl/{chat_id}/{message_id}/{encoded_name}"
    elif raw_url and raw_url.startswith("/"):
        raw_url = f"http://127.0.0.1:{PROXY_PORT}{raw_url}"

    vlc_bin = shutil.which("vlc") or "/usr/bin/vlc"
    if not os.path.exists(vlc_bin):
        mpv_bin = shutil.which("mpv") or "/usr/bin/mpv"
        if os.path.exists(mpv_bin):
            vlc_bin = mpv_bin

    launched = False
    if os.path.exists(vlc_bin):
        try:
            subprocess.Popen(
                [vlc_bin, raw_url, "--network-caching=3000"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
            launched = True
            logger.info(f"Launched VLC player for stream: {raw_url}")
        except Exception as e:
            logger.warning(f"Could not launch VLC subprocess: {e}")

    import urllib.parse
    encoded_file = urllib.parse.quote(f"{filename}.m3u")
    return {
        "success": True,
        "launched": launched,
        "stream_url": raw_url,
        "playlist_url": f"/api/media/vlc/playlist/{chat_id}/{message_id}/{encoded_file}"
    }


@router.get("/api/media/vlc/playlist/{chat_id}/{message_id}/{filename}")
async def generate_vlc_playlist(chat_id: str, message_id: int, filename: str, request: Request):
    """Generates an .m3u playlist file for 1-click opening in VLC on any device."""
    from app.config import PROXY_PORT
    host = request.headers.get("host", f"localhost:{PROXY_PORT}")
    clean_filename = filename.replace(".m3u", "")
    import urllib.parse
    encoded_name = urllib.parse.quote(clean_filename)
    stream_url = f"http://{host}/dl/{chat_id}/{message_id}/{encoded_name}"

    m3u_content = f"#EXTM3U\n#EXTINF:-1,{clean_filename}\n{stream_url}\n"
    return Response(
        content=m3u_content,
        media_type="audio/x-mpegurl",
        headers={
            "Content-Disposition": f'attachment; filename="{clean_filename}.m3u"',
            "Content-Type": "audio/x-mpegurl; charset=utf-8"
        }
    )
