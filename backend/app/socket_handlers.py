import logging
import socketio
from typing import Dict, Any
from app.config import CORS_ORIGINS
from app.models import UploadTaskState, UploadStatus
from app.services.queue_manager import queue_manager, UploadItem

logger = logging.getLogger("socket_handlers")

# Create Async Socket.IO Server
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
    ping_timeout=60,
    ping_interval=25
)

# Connect & Disconnect handlers
@sio.event
async def connect(sid, environ):
    logger.debug(f"Socket client connected: {sid}")
    # Send current queue snapshot to client
    tasks = queue_manager.get_all_tasks()
    task_snapshots = [_item_to_state_dict(t) for t in tasks]
    await sio.emit("queue:snapshot", task_snapshots, room=sid)

    # Send current download snapshot
    from app.services.download_manager import download_manager
    await sio.emit("download:snapshot", download_manager.get_all_tasks(), room=sid)

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
        "created_at": item.created_at
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

