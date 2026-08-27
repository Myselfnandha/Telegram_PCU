import asyncio
import logging
import os
import time
import uuid
import httpx
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any
from app.config import DOWNLOADS_DIR

logger = logging.getLogger("download_manager")


class DownloadStatus:
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DownloadTask:
    def __init__(
        self,
        task_id: str,
        source_url: str,
        filename: str,
        file_path: Path,
        file_size: int = 0
    ):
        self.id = task_id
        self.url = source_url
        self.filename = filename
        self.file_path = file_path
        self.file_size = file_size
        self.downloaded_bytes = 0
        self.progress = 0.0
        self.speed = 0.0
        self.eta = 0.0
        self.status = DownloadStatus.QUEUED
        self.error: Optional[str] = None
        self.created_at = time.time()
        self.completed_at: Optional[float] = None
        self.pause_event = asyncio.Event()
        self.pause_event.set()
        self.cancel_event = asyncio.Event()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "filename": self.filename,
            "file_size": self.file_size,
            "downloaded_bytes": self.downloaded_bytes,
            "progress": self.progress,
            "speed": self.speed,
            "eta": self.eta,
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "file_path": str(self.file_path.resolve()) if self.file_path else None
        }


class DownloadManager:
    def __init__(self):
        self.tasks: Dict[str, DownloadTask] = {}
        self.progress_emitter: Optional[Callable[[dict], None]] = None
        self._worker_task: Optional[asyncio.Task] = None
        self._queue: asyncio.Queue = asyncio.Queue()

    def set_emitter(self, emitter: Callable[[dict], None]):
        self.progress_emitter = emitter

    def _notify(self, task: DownloadTask):
        if self.progress_emitter:
            try:
                self.progress_emitter(task.to_dict())
            except Exception as e:
                logger.debug(f"Progress emitter error: {e}")

    def start(self):
        if not self._worker_task or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop())
            logger.info("Background Download Manager worker started.")

    async def add_url_download(self, url: str, custom_filename: Optional[str] = None) -> DownloadTask:
        task_id = f"dl_{uuid.uuid4().hex[:10]}"
        
        # Derive initial filename from URL
        inferred_name = url.split("?")[0].split("/")[-1].strip() or f"download_{task_id}.bin"
        filename = custom_filename.strip() if custom_filename and custom_filename.strip() else inferred_name
        file_path = DOWNLOADS_DIR / f"{task_id}_{filename}"

        task = DownloadTask(
            task_id=task_id,
            source_url=url,
            filename=filename,
            file_path=file_path
        )
        self.tasks[task_id] = task
        self._notify(task)
        await self._queue.put(task)
        return task

    def pause_task(self, task_id: str):
        task = self.tasks.get(task_id)
        if task and task.status == DownloadStatus.DOWNLOADING:
            task.pause_event.clear()
            task.status = DownloadStatus.PAUSED
            self._notify(task)

    def resume_task(self, task_id: str):
        task = self.tasks.get(task_id)
        if task and task.status == DownloadStatus.PAUSED:
            task.pause_event.set()
            task.status = DownloadStatus.DOWNLOADING
            self._notify(task)

    def cancel_task(self, task_id: str):
        task = self.tasks.get(task_id)
        if task:
            task.cancel_event.set()
            task.pause_event.set()
            task.status = DownloadStatus.CANCELLED
            if task.file_path.exists():
                try:
                    task.file_path.unlink(missing_ok=True)
                except Exception:
                    pass
            self._notify(task)

    def get_all_tasks(self) -> List[dict]:
        return [t.to_dict() for t in self.tasks.values()]

    async def _worker_loop(self):
        while True:
            try:
                task: DownloadTask = await self._queue.get()
                if task.status == DownloadStatus.CANCELLED:
                    self._queue.task_done()
                    continue

                await self._process_download(task)
                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Download worker loop error: {e}", exc_info=True)

    async def _process_download(self, task: DownloadTask):
        task.status = DownloadStatus.DOWNLOADING
        self._notify(task)

        # Check existing downloaded bytes for HTTP Range resumption
        resume_offset = 0
        if task.file_path.exists():
            resume_offset = task.file_path.stat().st_size

        headers = {}
        if resume_offset > 0:
            headers["Range"] = f"bytes={resume_offset}-"

        last_time = time.time()
        last_bytes = resume_offset
        last_emit = 0

        try:
            timeout = httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=None)
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                async with client.stream("GET", task.url, headers=headers) as resp:
                    if resp.status_code not in (200, 206):
                        raise Exception(f"HTTP Server returned error status {resp.status_code}")

                    content_length = resp.headers.get("Content-Length")
                    if content_length and content_length.isdigit():
                        task.file_size = resume_offset + int(content_length)

                    mode = "ab" if (resp.status_code == 206 and resume_offset > 0) else "wb"
                    if mode == "wb":
                        resume_offset = 0

                    task.downloaded_bytes = resume_offset

                    with open(task.file_path, mode) as f:
                        async for chunk in resp.aiter_bytes(chunk_size=256 * 1024):
                            if task.cancel_event.is_set():
                                raise asyncio.CancelledError("Download cancelled by user.")

                            if not task.pause_event.is_set():
                                task.status = DownloadStatus.PAUSED
                                self._notify(task)
                                await task.pause_event.wait()
                                task.status = DownloadStatus.DOWNLOADING
                                self._notify(task)

                            f.write(chunk)
                            task.downloaded_bytes += len(chunk)

                            now = time.time()
                            if (now - last_emit) >= 0.3:
                                if task.file_size > 0:
                                    task.progress = min(99.9, round((task.downloaded_bytes / task.file_size) * 100, 1))

                                elapsed = now - last_time
                                if elapsed >= 0.5:
                                    delta = task.downloaded_bytes - last_bytes
                                    task.speed = max(0.0, delta / elapsed)
                                    if task.file_size > task.downloaded_bytes and task.speed > 0:
                                        task.eta = (task.file_size - task.downloaded_bytes) / task.speed
                                    else:
                                        task.eta = 0
                                    last_time = now
                                    last_bytes = task.downloaded_bytes

                                last_emit = now
                                self._notify(task)

            # Successfully completed
            task.status = DownloadStatus.COMPLETED
            task.progress = 100.0
            task.completed_at = time.time()
            if task.file_path.exists():
                task.file_size = task.file_path.stat().st_size
                task.downloaded_bytes = task.file_size
            self._notify(task)
            logger.info(f"Successfully downloaded {task.filename} ({task.file_size} bytes)")

        except asyncio.CancelledError:
            task.status = DownloadStatus.CANCELLED
            self._notify(task)
        except Exception as e:
            logger.error(f"Download failed for {task.filename}: {e}")
            task.status = DownloadStatus.FAILED
            task.error = str(e)
            self._notify(task)


download_manager = DownloadManager()
