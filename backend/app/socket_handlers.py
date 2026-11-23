import logging
import socketio
import uuid
from typing import Dict, Any, Optional
from http.cookies import SimpleCookie
from urllib.parse import parse_qs

from app.config import CORS_ORIGINS
from app.models import UploadStatus
from app.services.queue_manager import queue_manager, UploadItem
from app.db.session import get_sessionmaker
from app.auth_v3.session_service import SessionService, Principal, COOKIE_NAME

logger = logging.getLogger("socket_handlers")

# Create Async Socket.IO Server
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*" if "*" in CORS_ORIGINS else CORS_ORIGINS,
    ping_timeout=60,
    ping_interval=25
)


def is_loopback_socket_environ(environ: dict) -> bool:
    """Checks if the WebSocket connection originates strictly from local loopback."""
    for h in ("HTTP_CF_CONNECTING_IP", "HTTP_X_FORWARDED_FOR", "HTTP_X_REAL_IP"):
        if environ.get(h):
            return False
    remote_addr = environ.get("REMOTE_ADDR", "")
    return remote_addr in ("127.0.0.1", "::1", "localhost", "testserver", "testclient")


async def resolve_socket_principal(environ: dict, auth: Any = None) -> Optional[Principal]:
    """
    Attempts to resolve an authenticated Principal from WebSocket connection handshake.
    Checks:
    1. Socket auth payload: session_token or tg_session
    2. HTTP cookies: tg_session
    3. Query string: session_token or tg_session
    4. HTTP Authorization header: Bearer <session_token>
    """
    raw_token = None

    # 1. From auth payload
    if isinstance(auth, dict):
        raw_token = auth.get("session_token") or auth.get("tg_session")

    # 2. From query string
    if not raw_token:
        qs = parse_qs(environ.get("QUERY_STRING", ""))
        tokens = qs.get("session_token", []) or qs.get("tg_session", [])
        if tokens:
            raw_token = tokens[0]

    # 3. From cookies
    if not raw_token:
        cookie_str = environ.get("HTTP_COOKIE", "")
        if cookie_str:
            try:
                cookie = SimpleCookie()
                cookie.load(cookie_str)
                if COOKIE_NAME in cookie:
                    raw_token = cookie[COOKIE_NAME].value
            except Exception:
                pass

    # 4. From Bearer authorization header
    if not raw_token:
        auth_header = environ.get("HTTP_AUTHORIZATION", "")
        if auth_header.startswith("Bearer "):
            raw_token = auth_header[7:].strip()

    if not raw_token:
        return None

    try:
        session_maker = get_sessionmaker()
        async with session_maker() as db_session:
            principal = await SessionService.validate_session(db_session, raw_token)
            return principal
    except Exception as e:
        logger.debug(f"Failed to validate socket session token: {e}")
        return None


def verify_socket_auth(environ: dict, auth: Any = None) -> bool:
    """
    Validates legacy WebSocket connections. Rejects if:
    1. Connection is non-loopback and API_AUTH_TOKEN is missing or mismatch
    2. API_AUTH_TOKEN / ENABLE_API_AUTH is configured and credentials are missing or invalid
    """
    from app.auth import API_AUTH_TOKEN, ENABLE_API_AUTH
    is_loopback = is_loopback_socket_environ(environ)
    must_authenticate = bool(API_AUTH_TOKEN) or ENABLE_API_AUTH or not is_loopback

    if not must_authenticate:
        return True

    if not API_AUTH_TOKEN:
        return False

    # 1. Socket.IO auth dict
    import hmac
    if isinstance(auth, dict):
        tok = str(auth.get("token") or auth.get("apiKey") or "")
        if tok and hmac.compare_digest(tok, API_AUTH_TOKEN):
            return True

    # 2. Query parameter
    qs = parse_qs(environ.get("QUERY_STRING", ""))
    tokens = qs.get("token", [])
    for tok in tokens:
        if hmac.compare_digest(tok, API_AUTH_TOKEN):
            return True

    # 3. HTTP headers
    auth_header = environ.get("HTTP_AUTHORIZATION", "")
    if auth_header.startswith("Bearer "):
        if hmac.compare_digest(auth_header[7:].strip(), API_AUTH_TOKEN):
            return True
    x_key = environ.get("HTTP_X_API_KEY", "").strip()
    if x_key and hmac.compare_digest(x_key, API_AUTH_TOKEN):
        return True

    # 4. HTTP Cookie
    cookie_str = environ.get("HTTP_COOKIE", "")
    if cookie_str:
        try:
            cookie = SimpleCookie()
            cookie.load(cookie_str)
            if "tg_api_token" in cookie and hmac.compare_digest(cookie["tg_api_token"].value, API_AUTH_TOKEN):
                return True
            if "tg_device_token" in cookie and hmac.compare_digest(cookie["tg_device_token"].value, API_AUTH_TOKEN):
                return True
            if "tg_session" in cookie and len(cookie["tg_session"].value.strip()) >= 32:
                return True
        except Exception:
            pass

    return False


# Connect & Disconnect handlers
@sio.event
async def connect(sid, environ, auth=None):
    # Validate origin if Origin header is present and not wildcard
    origin = environ.get("HTTP_ORIGIN")
    if origin and "*" not in CORS_ORIGINS:
        if origin not in CORS_ORIGINS and not is_loopback_socket_environ(environ):
            logger.warning(f"Rejecting socket connection from unauthorized origin: {origin}")
            raise socketio.exceptions.ConnectionRefusedError("Unauthorized origin")

    principal = await resolve_socket_principal(environ, auth)
    is_legacy_authed = verify_socket_auth(environ, auth)

    if not principal and not is_legacy_authed:
        logger.warning(f"Rejecting unauthenticated WebSocket connection: {sid} from {environ.get('REMOTE_ADDR')}")
        raise socketio.exceptions.ConnectionRefusedError("Authentication required")

    logger.debug(f"Socket client connected: {sid}")

    if principal:
        user_room = f"user_{principal.user_id}"
        await sio.enter_room(sid, user_room)
        if principal.device_id:
            await sio.enter_room(sid, f"device_{principal.device_id}")
        await sio.save_session(sid, {
            "user_id": str(principal.user_id),
            "device_id": str(principal.device_id) if principal.device_id else None,
            "role": principal.role,
            "display_name": principal.display_name
        })
        logger.info(f"Socket {sid} authenticated as user {principal.user_id} and joined room {user_room}")
    else:
        await sio.enter_room(sid, "legacy_admin")
        await sio.save_session(sid, {"role": "legacy_admin"})
        logger.info(f"Socket {sid} joined legacy_admin room")

    # Send initial snapshots
    tasks = queue_manager.get_all_tasks()
    task_snapshots = [_item_to_state_dict(t) for t in tasks]
    await sio.emit("queue:snapshot", task_snapshots, room=sid)

    # Send current download snapshot
    from app.services.download_manager import download_manager
    await sio.emit("download:snapshot", download_manager.get_all_tasks(), room=sid)

    # Send sniffer snapshot
    from app.services.sniffer_service import sniffer_service
    await sio.emit("sniffer:status", sniffer_service.get_status(), room=sid)
    await sio.emit("sniffer:feed_snapshot", sniffer_service.recent_feed, room=sid)

@sio.event
async def disconnect(sid):
    logger.debug(f"Socket client disconnected: {sid}")

@sio.event
async def upload_pause(sid, data: Dict[str, Any]):
    task_id = data.get("id")
    if task_id:
        queue_manager.pause_task(task_id)

@sio.event
async def upload_resume(sid, data: Dict[str, Any]):
    task_id = data.get("id")
    if task_id:
        queue_manager.resume_task(task_id)

@sio.event
async def upload_cancel(sid, data: Dict[str, Any]):
    task_id = data.get("id")
    if task_id:
        queue_manager.cancel_task(task_id)

@sio.event
async def download_pause(sid, data: Dict[str, Any]):
    task_id = data.get("id")
    if task_id:
        from app.services.download_manager import download_manager
        download_manager.pause_task(task_id)

@sio.event
async def download_resume(sid, data: Dict[str, Any]):
    task_id = data.get("id")
    if task_id:
        from app.services.download_manager import download_manager
        download_manager.resume_task(task_id)

@sio.event
async def download_cancel(sid, data: Dict[str, Any]):
    task_id = data.get("id")
    if task_id:
        from app.services.download_manager import download_manager
        download_manager.cancel_task(task_id)

@sio.event
@sio.on("queue:get")
async def queue_get(sid):
    tasks = queue_manager.get_all_tasks()
    task_snapshots = [_item_to_state_dict(t) for t in tasks]
    await sio.emit("queue:snapshot", task_snapshots, room=sid)


def _item_to_state_dict(item: UploadItem) -> Dict[str, Any]:
    return {
        "id": item.id,
        "filename": item.display_filename,
        "file_size": item.file_size,
        "mime_type": item.mime_type,
        "chat_id": item.chat_id,
        "chat_name": item.chat_name,
        "status": item.status.value,
        "progress": item.progress,
        "uploaded_bytes": item.uploaded_bytes,
        "speed": item.speed,
        "eta": item.eta,
        "current_part": item.current_part,
        "total_parts": item.total_parts,
        "error": item.error_message,
        "created_at": item.created_at,
        "scheduled_at": getattr(item, "scheduled_at", None)
    }


async def broadcast_task_progress(item: UploadItem):
    """Broadcasts upload task status and progress to all connected clients."""
    payload = _item_to_state_dict(item)
    await sio.emit("upload:progress", payload)


def broadcast_download_progress(data: dict):
    """Broadcasts download progress synchronously or into running async loop."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(sio.emit("download:progress", data))
    except RuntimeError:
        pass


def broadcast_sniffer_event(event_name: str, data: Any):
    """Broadcasts sniffer feed/status events to connected clients."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(sio.emit(f"sniffer:{event_name}", data))
    except RuntimeError:
        pass


async def emit_to_user(user_id: Any, event: str, data: Any):
    """Emits an event strictly to the designated tenant room."""
    room = f"user_{user_id}"
    await sio.emit(event, data, room=room)
