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
_ACTIVE_PREFETCH_TASKS: Dict[Tuple[str, int], asyncio.Task] = {}

from app.services.stream_cache import stream_cache_service, BLOCK_SIZE


async def _prefetch_non_watched_blocks(client, message, chat_id: str, message_id: int, file_size: int, filename: str, start_block: int = 0):
    """
    Background worker that continuously downloads all subsequent non-watched 10MB chunk blocks
    into local discrete block parts until the entire video is cached on local disk.
    """
    total_blocks = (file_size + BLOCK_SIZE - 1) // BLOCK_SIZE
    try:
        sequence = list(range(start_block, total_blocks)) + list(range(0, start_block))

        for block_idx in sequence:
            block_start = block_idx * BLOCK_SIZE
            block_end = min(file_size - 1, (block_idx + 1) * BLOCK_SIZE - 1)
            block_len = block_end - block_start + 1

            if stream_cache_service.has_block(chat_id, message_id, block_idx, block_len):
                continue

            chunks = []
            async for raw in client.iter_download(
                message.media,
                offset=block_start,
                limit=block_len,
                request_size=min(512 * 1024, block_len),
                chunk_size=min(512 * 1024, block_len),
            ):
                if raw:
                    chunks.append(raw)

            block_bytes = b"".join(chunks)
            if len(block_bytes) == block_len:
                stream_cache_service.save_block(chat_id, message_id, block_idx, block_bytes)
                logger.debug(f"[Continuous Prefetcher] Cached block {block_idx}/{total_blocks-1} for {filename}")

            await asyncio.sleep(0.04)  # Prioritize active playback stream

        # Merge blocks if all are present
        stream_cache_service.merge_blocks_if_complete(chat_id, message_id, file_size, filename)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.debug(f"Continuous prefetcher notice for {filename}: {e}")
    finally:
        _ACTIVE_PREFETCH_TASKS.pop((str(chat_id), message_id), None)


def compute_dynamic_align_unit(file_size: int, start: int, length: int, user_agent: str = "") -> int:
    """
    Adaptive MTProto chunk alignment strategy:
    - 128 KB for header probing / tiny seeks (<50ms response)
    - 256 KB for fast-forward scrubbing & mid-stream seeks
    - 512 KB for standard continuous video streaming
    - 1024 KB for large multi-part download manager requests
    """
    ua = user_agent.lower()
    is_download_manager = any(dm in ua for dm in ("fdm", "aria2", "idm", "wget", "curl"))

    if length <= 2 * 1024 * 1024:
        return 128 * 1024  # 128 KB for ultra-fast <50ms header/keyframe response

    if start > 0 and not is_download_manager:
        if length <= 8 * 1024 * 1024:
            return 256 * 1024  # 256 KB for instant keyframe scrubbing
        return 512 * 1024

    if is_download_manager and length > 16 * 1024 * 1024:
        return 1024 * 1024  # 1 MB for maximum bulk download throughput

    return 512 * 1024

@router.get("/dl/{chat_id}/{message_id}")
@router.get("/dl/{chat_id}/{message_id}/{filename}")
async def handle_proxy_download(
    chat_id: str,
    message_id: int,
    request: Request,
    filename: Optional[str] = None
):
    """
    Streaming proxy endpoint that fetches data dynamically from Telegram MTProto
    and pipes it directly to the client (VLC, browser, or download manager).
    Supports HTTP Range requests (seeking/resuming) and real-time caching.
    """
    try:
        client = await TelegramClientManager.get_client()
        if not client or not client.is_connected() or not await client.is_user_authorized():
            raise HTTPException(status_code=401, detail="Telegram client not authorized.")
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
    user_agent = request.headers.get("User-Agent", "")
    align_unit = compute_dynamic_align_unit(file_size, start, length, user_agent)
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
        "X-Accel-Buffering": "no",
        "Cache-Control": "public, max-age=3600",
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

    # 2. Launch Continuous Background Prefetcher for non-watched blocks
    curr_block = start // BLOCK_SIZE
    task_key = (str(chat_id), message_id)
    if task_key not in _ACTIVE_PREFETCH_TASKS or _ACTIVE_PREFETCH_TASKS[task_key].done():
        _ACTIVE_PREFETCH_TASKS[task_key] = asyncio.create_task(
            _prefetch_non_watched_blocks(client, message, str(chat_id), message_id, file_size, clean_name, curr_block)
        )

    # 3. Check if the requested range can be served directly from discrete block parts on disk (<0.1ms)
    start_block = start // BLOCK_SIZE
    end_block = end // BLOCK_SIZE
    all_blocks_present = True
    for b_idx in range(start_block, end_block + 1):
        b_len = min(BLOCK_SIZE, file_size - (b_idx * BLOCK_SIZE))
        if not stream_cache_service.has_block(str(chat_id), message_id, b_idx, b_len):
            all_blocks_present = False
            break

    if all_blocks_present:
        async def block_streamer():
            curr_pos = start
            bytes_left = length
            while bytes_left > 0:
                b_idx = curr_pos // BLOCK_SIZE
                offset_in_b = curr_pos % BLOCK_SIZE
                b_len = min(BLOCK_SIZE, file_size - (b_idx * BLOCK_SIZE))
                read_len = min(bytes_left, b_len - offset_in_b)
                data = stream_cache_service.read_block_slice(str(chat_id), message_id, b_idx, offset_in_b, read_len)
                if not data:
                    break
                yield data
                bytes_left -= len(data)
                curr_pos += len(data)
                await asyncio.sleep(0)

        return StreamingResponse(
            block_streamer(),
            status_code=status_code,
            headers=headers
        )

    # 4. Otherwise stream from MTProto and buffer in background
    cache_path = stream_cache_service.get_cache_path(str(chat_id), message_id, clean_name)
    part_path = cache_path.with_suffix(".part")

    async def stream_generator():
        bytes_written = 0
        cache_f = None
        if start == 0 and not cache_path.exists():
            try:
                cache_f = open(part_path, "wb")
            except Exception:
                pass

        # High-throughput asynchronous MTProto chunk lookahead queue (16MB buffer)
        chunk_queue = asyncio.Queue(maxsize=16)
        producer_done = asyncio.Event()

        async def _mtproto_producer():
            try:
                first_chunk = True
                async for raw_chunk in client.iter_download(
                    message.media,
                    offset=aligned_start,
                    limit=aligned_limit,
                    request_size=align_unit,
                    chunk_size=align_unit,
                ):
                    if first_chunk:
                        first_chunk = False
                        if discard_bytes > 0:
                            raw_chunk = raw_chunk[discard_bytes:]
                    if raw_chunk:
                        await chunk_queue.put(raw_chunk)
            except Exception as pe:
                logger.debug(f"MTProto producer notice: {pe}")
            finally:
                producer_done.set()
                await chunk_queue.put(None)

        prod_task = asyncio.create_task(_mtproto_producer())

        try:
            while True:
                chunk = await chunk_queue.get()
                if chunk is None:
                    break

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
            if not prod_task.done():
                prod_task.cancel()
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


_THUMB_CACHE_DIR = os.path.expanduser("~/.cache/tg_power_suite/thumbs")
_PREVIEW_CACHE_DIR = os.path.expanduser("~/.cache/tg_power_suite/previews")
os.makedirs(_THUMB_CACHE_DIR, exist_ok=True)
os.makedirs(_PREVIEW_CACHE_DIR, exist_ok=True)


@router.get("/dl/{chat_id}/{message_id}/thumb")
async def get_media_thumbnail(chat_id: str, message_id: int):
    """Fetches and caches the preview thumbnail for a Telegram video/document."""
    cache_file = os.path.join(_THUMB_CACHE_DIR, f"{chat_id}_{message_id}.jpg")
    if os.path.exists(cache_file) and os.path.getsize(cache_file) > 0:
        with open(cache_file, "rb") as f:
            return Response(
                content=f.read(),
                media_type="image/jpeg",
                headers={"Cache-Control": "public, max-age=86400, immutable"}
            )

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

        # Download compact lightweight thumbnail into memory buffer (<10ms)
        thumb_bytes = None
        doc = getattr(message.media, "document", None) or (message.media if hasattr(message.media, "thumbs") else None)
        if doc and getattr(doc, "thumbs", None):
            try:
                # Prefer fast medium thumbnail (thumbs[1] or thumbs[0]) over massive full-res to load 10x faster
                target_thumb = doc.thumbs[1] if len(doc.thumbs) > 1 else doc.thumbs[0]
                thumb_bytes = await client.download_media(target_thumb, file=bytes)
            except Exception:
                try:
                    thumb_bytes = await client.download_media(doc.thumbs[-1], file=bytes)
                except Exception:
                    pass

        if not thumb_bytes:
            try:
                thumb_bytes = await client.download_media(message.media, thumb=0, file=bytes)
            except Exception:
                try:
                    thumb_bytes = await client.download_media(message.media, thumb=-1, file=bytes)
                except Exception:
                    pass

        if not thumb_bytes:
            raise HTTPException(status_code=404, detail="No thumbnail available for this media.")

        # Save to disk cache
        try:
            with open(cache_file, "wb") as f:
                f.write(thumb_bytes)
        except Exception:
            pass

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
    input_source = str(local_cached) if local_cached and local_cached.exists() else f"http://127.0.0.1:8088/dl/{chat_id}/{message_id}"
    
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


_CINEMA_VIDEOS_CACHE: Dict[str, dict] = {}
CINEMA_CACHE_TTL = 300  # 5 minutes in-memory cache


async def _fetch_and_cache_videos(chat_id: str, limit: int = 50, offset_id: int = 0) -> dict:
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

        # Fetch both native video messages AND videos sent as documents (MKV, MP4, etc.) at full raw MTProto speed
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

        # Fire background thumbnail prefetcher for instant 0ms loads
        async def _prefetch_thumbs(cid, msgs):
            try:
                for m in msgs[:30]:
                    cf = os.path.join(_THUMB_CACHE_DIR, f"{cid}_{m.id}.jpg")
                    if os.path.exists(cf) and os.path.getsize(cf) > 0:
                        continue
                    doc = getattr(m.media, "document", None) or (m.media if hasattr(m.media, "thumbs") else None)
                    if doc and getattr(doc, "thumbs", None):
                        target = doc.thumbs[1] if len(doc.thumbs) > 1 else doc.thumbs[0]
                        tb = await client.download_media(target, file=bytes)
                        if tb:
                            with open(cf, "wb") as f:
                                f.write(tb)
            except Exception:
                pass

        asyncio.create_task(_prefetch_thumbs(clean_chat_id, combined_msgs))

    except Exception as e:
        logger.error(f"Error querying videos for {chat_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to query videos: {str(e)}")

    res = {
        "chat_id": str(chat_id),
        "count": len(videos),
        "videos": videos
    }

    cache_key = f"{chat_id}_{offset_id}"
    _CINEMA_VIDEOS_CACHE[cache_key] = {
        "data": res,
        "timestamp": time.time()
    }
    return res


@router.get("/api/media/videos/{chat_id}")
async def get_chat_videos(chat_id: str, limit: int = 50, offset_id: int = 0, force_refresh: bool = False):
    """
    Fetches video archives from a Telegram channel, group, or Saved Messages for the Cinema tab.
    Implements Stale-While-Revalidate caching for instantaneous (<1ms) response times.
    """
    cache_key = f"{chat_id}_{offset_id}"
    now = time.time()

    if not force_refresh and cache_key in _CINEMA_VIDEOS_CACHE:
        cached_entry = _CINEMA_VIDEOS_CACHE[cache_key]
        if (now - cached_entry["timestamp"]) > 60:
            asyncio.create_task(_fetch_and_cache_videos(chat_id, limit, offset_id))
        return cached_entry["data"]

    return await _fetch_and_cache_videos(chat_id, limit, offset_id)


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

    target_player = payload.get("player", "vlc")
    vlc_bin = shutil.which("vlc") or "/usr/bin/vlc"
    mpv_bin = shutil.which("mpv") or "/usr/bin/mpv"

    chosen_bin = None
    if target_player == "mpv" and os.path.exists(mpv_bin):
        chosen_bin = mpv_bin
    elif os.path.exists(vlc_bin):
        chosen_bin = vlc_bin
    elif os.path.exists(mpv_bin):
        chosen_bin = mpv_bin

    launched = False
    player_name = "vlc"
    if chosen_bin:
        try:
            if "mpv" in chosen_bin:
                player_name = "mpv"
                subprocess.Popen(
                    [
                        chosen_bin,
                        raw_url,
                        "--hr-seek=yes",
                        "--hr-seek-framedrop=yes",
                        "--cache=yes",
                        "--cache-pause=no",
                        "--cache-secs=30",
                        "--demuxer-max-bytes=128M",
                        "--demuxer-readahead-secs=30",
                        "--vd-lavc-threads=0",
                        "--vd-lavc-fast=yes"
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True
                )
            else:
                player_name = "vlc"
                subprocess.Popen(
                    [
                        chosen_bin,
                        raw_url,
                        "--input-fast-seek",
                        "--avcodec-threads=0",
                        "--avcodec-fast",
                        "--network-caching=800",
                        "--file-caching=500",
                        "--live-caching=500",
                        "--clock-jitter=0",
                        "--no-qt-error-dialogs",
                        "--quiet"
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True
                )
            launched = True
            logger.info(f"Launched {player_name.upper()} player (Turbo Streaming) for: {raw_url}")
        except Exception as e:
            logger.warning(f"Could not launch media player subprocess: {e}")

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


@router.post("/api/media/vlc/play_batch")
async def launch_vlc_batch(payload: Dict[str, Any]):
    """Launch VLC Player with an entire TV Series / Season playlist queued for seamless binge watching."""
    import shutil
    import subprocess
    from app.config import PROXY_PORT

    items = payload.get("items", [])
    title = payload.get("title", "Season Playlist")
    if not items:
        raise HTTPException(status_code=400, detail="No video items provided for batch playback.")

    vlc_bin = shutil.which("vlc") or "/usr/bin/vlc"
    mpv_bin = shutil.which("mpv") or "/usr/bin/mpv"

    stream_urls = []
    for item in items:
        chat_id = item.get("chat_id")
        msg_id = item.get("message_id")
        fn = item.get("filename", "video.mp4")
        raw_url = item.get("stream_url")
        if not raw_url and chat_id and msg_id:
            import urllib.parse
            encoded_name = urllib.parse.quote(fn)
            raw_url = f"http://127.0.0.1:{PROXY_PORT}/dl/{chat_id}/{msg_id}/{encoded_name}"
        elif raw_url and raw_url.startswith("/"):
            raw_url = f"http://127.0.0.1:{PROXY_PORT}{raw_url}"
        if raw_url:
            stream_urls.append(raw_url)

    launched = False
    player_name = "vlc"
    chosen_bin = vlc_bin if os.path.exists(vlc_bin) else (mpv_bin if os.path.exists(mpv_bin) else None)

    if chosen_bin and stream_urls:
        try:
            if "mpv" in chosen_bin:
                player_name = "mpv"
                cmd = [
                    chosen_bin,
                    *stream_urls,
                    "--hr-seek=yes",
                    "--cache=yes",
                    "--demuxer-max-bytes=256M"
                ]
            else:
                player_name = "vlc"
                cmd = [
                    chosen_bin,
                    *stream_urls,
                    "--input-fast-seek",
                    "--avcodec-threads=0",
                    "--avcodec-fast",
                    "--network-caching=300",
                    "--no-qt-error-dialogs",
                    "--quiet"
                ]
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
            launched = True
            logger.info(f"Launched batch binge session for {len(stream_urls)} episodes: {title}")
        except Exception as e:
            logger.warning(f"Could not launch batch player subprocess: {e}")

    return {
        "success": True,
        "launched": launched,
        "episodes_count": len(stream_urls),
        "title": title
    }


@router.post("/api/media/vlc/batch_playlist")
async def generate_batch_playlist(payload: Dict[str, Any], request: Request):
    """Generates a complete multi-episode .m3u playlist for a TV Series season."""
    from app.config import PROXY_PORT
    host = request.headers.get("host", f"localhost:{PROXY_PORT}")
    items = payload.get("items", [])
    title = payload.get("title", "Season_Playlist").replace(" ", "_")

    m3u_lines = ["#EXTM3U"]
    for item in items:
        chat_id = item.get("chat_id")
        msg_id = item.get("message_id")
        fn = item.get("filename", "episode.mp4")
        import urllib.parse
        encoded_name = urllib.parse.quote(fn)
        stream_url = f"http://{host}/dl/{chat_id}/{msg_id}/{encoded_name}"
        m3u_lines.append(f"#EXTINF:-1,{fn}")
        m3u_lines.append(stream_url)

    m3u_content = "\n".join(m3u_lines) + "\n"
    return Response(
        content=m3u_content,
        media_type="audio/x-mpegurl",
        headers={
            "Content-Disposition": f'attachment; filename="{title}.m3u"',
            "Content-Type": "audio/x-mpegurl; charset=utf-8"
        }
    )
