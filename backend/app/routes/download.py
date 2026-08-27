import logging
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Form
from pydantic import BaseModel
from app.services.download_manager import download_manager, DownloadStatus
from app.services.queue_manager import queue_manager, UploadItem

logger = logging.getLogger("download_route")

router = APIRouter(prefix="/api/download", tags=["downloads"])


class DownloadUrlRequest(BaseModel):
    url: str
    custom_filename: Optional[str] = None


@router.get("/list")
async def list_downloads():
    """Lists all background downloads and their real-time progress."""
    return download_manager.get_all_tasks()


@router.post("/url")
async def start_url_download(req: DownloadUrlRequest):
    """Enqueues a remote URL to be downloaded in the background."""
    if not req.url or not req.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Invalid URL. Must begin with http:// or https://")

    try:
        task = await download_manager.add_url_download(
            url=req.url,
            custom_filename=req.custom_filename
        )
        return {
            "status": "queued",
            "task_id": task.id,
            "filename": task.filename
        }
    except Exception as e:
        logger.error(f"Error enqueuing download: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{task_id}/pause")
async def pause_download(task_id: str):
    download_manager.pause_task(task_id)
    return {"status": "paused", "task_id": task_id}


@router.post("/{task_id}/resume")
async def resume_download(task_id: str):
    download_manager.resume_task(task_id)
    return {"status": "resumed", "task_id": task_id}


@router.post("/{task_id}/cancel")
async def cancel_download(task_id: str):
    download_manager.cancel_task(task_id)
    return {"status": "cancelled", "task_id": task_id}


@router.post("/{task_id}/upload-to-telegram")
async def send_download_to_telegram(
    task_id: str,
    chat_id: str = Form("me"),
    chat_name: str = Form("Saved Messages (Personal Cloud)"),
    caption: Optional[str] = Form(""),
    send_as: Optional[str] = Form("auto")
):
    """1-Click helper to immediately transfer a completed download into the Telegram upload queue."""
    task = download_manager.tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Download task not found")
    if task.status != DownloadStatus.COMPLETED or not task.file_path.exists():
        raise HTTPException(status_code=400, detail="File is not yet downloaded completely.")

    clean_chat_id = chat_id.strip() if chat_id else "me"
    if clean_chat_id.lstrip("-").isdigit():
        clean_chat_id = int(clean_chat_id)

    upload_item = UploadItem(
        task_id=f"up_{task.id}",
        file_path=task.file_path,
        original_filename=task.filename,
        custom_filename=task.filename,
        chat_id=clean_chat_id,
        chat_name=chat_name,
        caption=caption or f"Downloaded from: {task.url}",
        send_as=send_as or "auto",
        is_temp_file=False
    )
    await queue_manager.add_task(upload_item)
    return {
        "status": "queued_for_telegram",
        "task_id": upload_item.id,
        "filename": upload_item.display_filename,
        "chat_id": str(clean_chat_id)
    }
