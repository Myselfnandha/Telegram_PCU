import os
import uuid
import logging
from pathlib import Path
from typing import Optional, Union, Any
import aiofiles
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, BackgroundTasks
from app.config import TEMP_UPLOAD_DIR, CHUNK_READ_SIZE
from app.models import UploadStatus
from app.services.queue_manager import queue_manager, UploadItem
from app.telegram_client import TelegramClientManager

from pydantic import BaseModel

logger = logging.getLogger("upload_route")

router = APIRouter(prefix="/api", tags=["upload"])


class ChunkCompleteRequest(BaseModel):
    upload_id: str
    chat_id: str
    chat_name: Optional[str] = ""
    caption: Optional[str] = ""
    filename: Optional[str] = ""
    send_as: Optional[str] = "auto"
    total_size: Optional[int] = None


@router.post("/upload/chunk")
async def handle_upload_chunk(
    file: UploadFile = File(...),
    upload_id: str = Form(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    offset: int = Form(...),
    total_size: int = Form(...),
    filename: str = Form(...)
):
    """
    Handles sliced chunk uploads from the browser.
    Appends binary chunks directly to the target temporary file on NVMe disk,
    bypassing /tmp memory limits completely for multi-gigabyte (10GB-100GB+) uploads.
    """
    clean_original_filename = filename or f"upload_{upload_id}"
    clean_original_filename = "".join([c for c in clean_original_filename if (c.isalnum() or c in " .-_()")]).strip()
    temp_file_path = TEMP_UPLOAD_DIR / f"{upload_id}_{clean_original_filename}"

    try:
        # Pre-allocate full file space on first chunk to prevent fragmentation
        if chunk_index == 0 and not temp_file_path.exists():
            try:
                fd = os.open(str(temp_file_path), os.O_CREAT | os.O_WRONLY, 0o644)
                try:
                    if total_size > 10 * 1024 * 1024:
                        os.posix_fallocate(fd, 0, total_size)
                finally:
                    os.close(fd)
            except Exception as e:
                logger.debug(f"fallocate for chunked upload skipped: {e}")

        # Read chunk content and write to exact byte offset
        chunk_data = await file.read()
        chunk_len = len(chunk_data)

        mode = "r+b" if temp_file_path.exists() else "wb"
        with open(temp_file_path, mode) as f:
            f.seek(offset)
            f.write(chunk_data)

        return {
            "status": "ok",
            "upload_id": upload_id,
            "chunk_index": chunk_index,
            "received_bytes": offset + chunk_len,
            "total_size": total_size
        }
    except Exception as e:
        logger.error(f"Error writing chunk {chunk_index} for task {upload_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to write chunk {chunk_index}: {str(e)}")
    finally:
        await file.close()


@router.post("/upload/chunk/complete")
async def handle_chunk_upload_complete(payload: ChunkCompleteRequest):
    """
    Finalizes chunked upload and enqueues the task into Telegram MTProto QueueManager.
    """
    if not await TelegramClientManager.is_authorized():
        raise HTTPException(
            status_code=401,
            detail="Telegram MTProto is not authorized. Please complete authentication first."
        )

    task_id = payload.upload_id
    clean_original_filename = payload.filename or f"upload_{task_id}"
    clean_original_filename = "".join([c for c in clean_original_filename if (c.isalnum() or c in " .-_()")]).strip()
    target_filename = payload.filename if payload.filename and payload.filename.strip() else clean_original_filename
    temp_file_path = TEMP_UPLOAD_DIR / f"{task_id}_{clean_original_filename}"

    if not temp_file_path.exists():
        raise HTTPException(status_code=404, detail="Uploaded file segments not found on server.")

    total_bytes = temp_file_path.stat().st_size
    if total_bytes == 0:
        temp_file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Uploaded file is empty (0 bytes).")

    clean_chat_id: Union[int, str] = payload.chat_id.strip()
    if clean_chat_id != "me":
        try:
            clean_chat_id = int(clean_chat_id)
        except ValueError:
            pass

    upload_item = UploadItem(
        task_id=task_id,
        file_path=temp_file_path,
        original_filename=clean_original_filename,
        custom_filename=target_filename,
        chat_id=clean_chat_id,
        chat_name=payload.chat_name or f"Chat {clean_chat_id}",
        caption=payload.caption or "",
        send_as=payload.send_as or "auto",
        is_temp_file=True
    )

    await queue_manager.add_task(upload_item)

    return {
        "status": "queued",
        "task_id": task_id,
        "filename": target_filename,
        "file_size": total_bytes,
        "chat_id": payload.chat_id,
        "send_as": payload.send_as
    }

@router.post("/upload")
async def handle_upload(
    file: UploadFile = File(...),
    chat_id: str = Form(...),
    chat_name: Optional[str] = Form(""),
    caption: Optional[str] = Form(""),
    filename: Optional[str] = Form(None),
    send_as: Optional[str] = Form("auto"),  # auto, document, media
    upload_id: Optional[str] = Form(None)
):
    """
    Handles file upload from the browser.
    Streams incoming file in chunks directly to temp storage to avoid OOM,
    then adds the task to the Telegram MTProto upload queue.
    """
    # Check if client is authorized
    if not await TelegramClientManager.is_authorized():
        raise HTTPException(
            status_code=401,
            detail="Telegram MTProto is not authorized. Please complete authentication in setup_auth.py first."
        )

    # Parse chat_id (e.g. numeric ID, "me", or channel username)
    clean_chat_id: Union[int, str] = chat_id.strip()
    if clean_chat_id != "me":
        try:
            clean_chat_id = int(clean_chat_id)
        except ValueError:
            pass

    task_id = upload_id or str(uuid.uuid4())
    clean_original_filename = file.filename or f"upload_{task_id}"
    target_filename = filename if filename and filename.strip() else clean_original_filename

    # Create a unique temporary file path on disk
    temp_file_path = TEMP_UPLOAD_DIR / f"{task_id}_{clean_original_filename}"

    try:
        # Zero-fragmentation pre-allocation on Linux filesystem
        if hasattr(file, "size") and file.size and file.size > 10 * 1024 * 1024:
            try:
                fd = os.open(str(temp_file_path), os.O_CREAT | os.O_WRONLY, 0o644)
                try:
                    os.posix_fallocate(fd, 0, file.size)
                finally:
                    os.close(fd)
            except Exception as e:
                logger.debug(f"fallocate skipped: {e}")

        # Stream file chunks to disk to prevent RAM blowup (anti-bug for 2GB+ files)
        logger.info(f"Receiving file '{clean_original_filename}' (task {task_id}) to {temp_file_path}")
        async with aiofiles.open(temp_file_path, "wb") as out_file:
            while True:
                chunk = await file.read(CHUNK_READ_SIZE)
                if not chunk:
                    break
                await out_file.write(chunk)

        total_bytes = temp_file_path.stat().st_size
        logger.info(f"Received {total_bytes} bytes for task {task_id}")

        if total_bytes == 0:
            temp_file_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="Uploaded file is empty (0 bytes).")

        # Create UploadItem
        upload_item = UploadItem(
            task_id=task_id,
            file_path=temp_file_path,
            original_filename=clean_original_filename,
            custom_filename=target_filename,
            chat_id=clean_chat_id,
            chat_name=chat_name or f"Chat {clean_chat_id}",
            caption=caption or "",
            send_as=send_as or "auto",
            is_temp_file=True
        )

        # Enqueue task
        await queue_manager.add_task(upload_item)

        return {
            "status": "queued",
            "task_id": task_id,
            "filename": target_filename,
            "file_size": total_bytes,
            "chat_id": chat_id,
            "send_as": send_as
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error handling upload for task {task_id}: {e}", exc_info=True)
        if temp_file_path.exists():
            temp_file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Failed to process uploaded file: {str(e)}")
    finally:
        await file.close()


@router.get("/tasks")
async def get_all_tasks():
    """Returns the list of all currently active or recent tasks."""
    tasks = queue_manager.get_all_tasks()
    return [{
        "id": t.id,
        "filename": t.display_filename,
        "file_size": t.file_size,
        "mime_type": t.mime_type,
        "chat_id": t.chat_id,
        "chat_name": t.chat_name,
        "status": t.status.value,
        "progress": t.progress,
        "uploaded_bytes": t.uploaded_bytes,
        "speed": t.speed,
        "eta": t.eta,
        "current_part": t.current_part,
        "total_parts": t.total_parts,
        "error": t.error_message,
        "created_at": t.created_at
    } for t in tasks]


@router.post("/tasks/{task_id}/pause")
async def pause_task(task_id: str):
    success = queue_manager.pause_task(task_id)
    if not success:
        raise HTTPException(status_code=404, detail="Task not found or not in pausable state")
    return {"status": "success", "task_id": task_id, "action": "paused"}


@router.post("/tasks/{task_id}/resume")
async def resume_task(task_id: str):
    success = queue_manager.resume_task(task_id)
    if not success:
        raise HTTPException(status_code=404, detail="Task not found or not in paused state")
    return {"status": "success", "task_id": task_id, "action": "resumed"}


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    success = queue_manager.cancel_task(task_id)
    if not success:
        raise HTTPException(status_code=404, detail="Task not found or already finished")
    return {"status": "success", "task_id": task_id, "action": "cancelled"}


@router.post("/upload/batch/pause")
async def batch_pause_tasks():
    count = queue_manager.pause_all_tasks()
    return {"status": "success", "count": count, "action": "paused_all"}


@router.post("/upload/batch/resume")
async def batch_resume_tasks():
    count = queue_manager.resume_all_tasks()
    return {"status": "success", "count": count, "action": "resumed_all"}


@router.post("/upload/batch/cancel")
async def batch_cancel_tasks():
    count = queue_manager.cancel_all_tasks()
    return {"status": "success", "count": count, "action": "cancelled_all"}


@router.post("/upload/batch/clear")
async def batch_clear_completed():
    count = queue_manager.clear_completed_tasks()
    return {"status": "success", "count": count, "action": "cleared_completed"}
