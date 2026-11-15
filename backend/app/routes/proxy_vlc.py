import os
import shutil
import subprocess
import urllib.parse
import asyncio
import logging
from typing import Optional, Dict, Any, Union

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Request, Response, Depends

from app.auth import verify_auth
from app.config import PROXY_HOST, PROXY_PORT
from app.telegram_client import TelegramClientManager
from app.services.cinema_catalog import cinema_catalog, get_streamable_message
from app.services.stream_buffer import stream_buffer
from app.services.playback_tracker import playback_tracker
from app.services.vlc_playback import playback_grants
from app.services.sniffer_service import auto_rename, sniffer_service
from app.services.manager_detector import get_desktop_env

logger = logging.getLogger("proxy_vlc")

router = APIRouter(tags=["proxy_vlc"])


class VlcPlayPayload(BaseModel):
    chat_id: Union[int, str]
    message_id: int
    filename: Optional[str] = None


class PlaybackProgressPayload(BaseModel):
    chat_id: str
    message_id: int
    filename: str
    position_seconds: float
    duration_seconds: float
    thumbnail_url: Optional[str] = None
    last_chunk_index: Optional[int] = 0
    last_byte_offset: Optional[int] = 0


@router.post("/api/media/vlc/play", dependencies=[Depends(verify_auth)])
async def play_desktop_vlc(payload: VlcPlayPayload, request: Request):
    return await launch_desktop_vlc(str(payload.chat_id), payload.message_id, request, payload.filename)


@router.post("/api/media/vlc/launch/{chat_id}/{message_id}", dependencies=[Depends(verify_auth)])
async def launch_desktop_vlc(chat_id: str, message_id: int, request: Request, filename: Optional[str] = None):
    """Launch VLC on the TG Power Suite host; deliberately restricted to direct loopback clients."""
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
    import sys
    proxy_mod = sys.modules.get("app.routes.proxy")
    get_msg_fn = getattr(proxy_mod, "get_streamable_message", get_streamable_message) if proxy_mod else get_streamable_message

    message = await get_msg_fn(client, clean_chat_id, message_id)
    if (not message or not getattr(message, "media", None) or not hasattr(message, "file") or not message.file) and clean_chat_id != "me":
        message = await get_msg_fn(client, clean_chat_id, message_id, force_refresh=True)
    if (not message or not getattr(message, "media", None) or not hasattr(message, "file") or not message.file) and clean_chat_id != "me":
        me_msg = await get_msg_fn(client, "me", message_id)
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

    safe_name = urllib.parse.quote(filename or f"video_{message_id}.mp4")
    grant = playback_grants.issue(chat_id, message_id)
    url = f"http://127.0.0.1:{PROXY_PORT}/dl/{urllib.parse.quote(str(chat_id), safe='')}/{message_id}/{safe_name}?playback_token={urllib.parse.quote(grant)}"
    prog = await playback_tracker.aget_playback_progress(str(clean_chat_id), message_id)
    start_sec = int(prog.get("last_position_seconds", 0)) if prog else 0
    duration_sec = int(prog.get("duration_seconds", 0)) if prog else 0
    prog_pct = float(prog.get("percent", 0.0)) if prog else 0.0
    if start_sec <= 0 and prog and prog.get("last_byte_offset", 0) > 0 and duration_sec > 0 and file_size > 0:
        start_sec = int((prog["last_byte_offset"] / file_size) * duration_sec)
    is_finished = bool(prog.get("finished", False)) if prog else False
    if is_finished or prog_pct >= 98.0 or (duration_sec > 0 and start_sec >= max(0, duration_sec - 90)):
        start_sec = 0

    resume_sec = max(0, start_sec - 5) if start_sec > 10 else start_sec

    vlc_args = [
        vlc_bin,
        "--no-video-title-show",
        "--network-caching=1000",
        "--file-caching=1000",
        "--http-reconnect",
        "--no-spdif",
        "--avcodec-hw=any",
    ]
    if resume_sec > 2:
        vlc_args.append(f"--start-time={resume_sec}")
        logger.info(f"VLC [{safe_name}] Resuming at {resume_sec}s")

    vlc_args.append(url)

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
        "--network-caching=1000",
        "--file-caching=1000",
        "--http-reconnect",
        "--no-spdif",
        "--avcodec-hw=any",
    ]

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
        ep_url = f"http://127.0.0.1:{PROXY_PORT}/dl/{safe_cid}/{mid}/{safe_name}?playback_token={urllib.parse.quote(grant)}"
        vlc_args.append(ep_url)

    if len(vlc_args) <= 7:
        raise HTTPException(status_code=400, detail="No valid media streams could be constructed.")

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
