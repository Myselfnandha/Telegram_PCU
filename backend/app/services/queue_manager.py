import asyncio
import logging
import math
import time
import uuid
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Dict, Optional, Callable, List, Any, Union
from app.models import UploadStatus, SendMode
from app.config import SPLIT_THRESHOLD
from app.services.splitter import split_large_file, cleanup_files
from app.services.uploader import send_file_to_telegram, UploadCancelledError, UploadFailedError
from app.services.file_detector import detect_mime
from app.telegram_client import TelegramClientManager

logger = logging.getLogger("queue_manager")


class NightModeManager:
    """Manages off-peak night schedule window (e.g. 01:00 AM to 06:00 AM)."""
    def __init__(self):
        self.enabled: bool = False
        self.start_time: str = "01:00"  # HH:MM 24hr format
        self.end_time: str = "06:00"    # HH:MM 24hr format

    def is_in_night_window(self) -> bool:
        if not self.enabled:
            return True  # If disabled, anytime is active

        now = datetime.now().time()
        try:
            start_h, start_m = map(int, self.start_time.split(":"))
            end_h, end_m = map(int, self.end_time.split(":"))
            t_start = dt_time(start_h, start_m)
            t_end = dt_time(end_h, end_m)

            if t_start <= t_end:
                # Same day window (e.g. 02:00 to 06:00)
                return t_start <= now <= t_end
            else:
                # Overnight window across midnight (e.g. 23:00 to 06:00)
                return now >= t_start or now <= t_end
        except Exception:
            return True

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "in_window": self.is_in_night_window()
        }


class UploadItem:
    def __init__(
        self,
        task_id: str,
        file_path: Path,
        original_filename: str,
        chat_id: Union[int, str],
        chat_name: str = "",
        caption: str = "",
        custom_filename: Optional[str] = None,
        send_as: str = "auto",
        client_sid: Optional[str] = None,
        is_temp_file: bool = True,
        scheduled_at: Optional[float] = None
    ):
        self.id = task_id
        self.file_path = file_path
        self.original_filename = original_filename
        self.display_filename = custom_filename or original_filename
        self.file_size = file_path.stat().st_size if file_path.exists() else 0
        self.mime_type = detect_mime(file_path, filename=self.display_filename)
        self.chat_id = chat_id
        self.chat_name = chat_name
        self.caption = caption
        self.send_as = send_as
        self.client_sid = client_sid
        self.is_temp_file = is_temp_file
        self.scheduled_at = scheduled_at

        self.status = UploadStatus.SCHEDULED if (scheduled_at and scheduled_at > time.time()) else UploadStatus.QUEUED
        self.progress = 0.0
        self.uploaded_bytes = 0
        self.speed = 0.0
        self.eta = 0.0
        self.current_part = 1
        self.total_parts = 1
        self.error_message: Optional[str] = None
        self.message_ids: List[int] = []
        
        self.pause_event = asyncio.Event()
        self.pause_event.set()  # Initialized as not paused
        self.cancel_event = asyncio.Event()

        self.created_at = time.time()
        self.completed_at: Optional[float] = None
        self.split_parts: List[Path] = []


class QueueManager:
    def __init__(self, max_concurrent: int = 1):
        self.tasks: Dict[str, UploadItem] = {}
        self.queue: asyncio.Queue[UploadItem] = asyncio.Queue()
        self.max_concurrent = max_concurrent
        self._workers: List[asyncio.Task] = []
        self._scheduler_task: Optional[asyncio.Task] = None
        self._is_running = False
        self.night_mode = NightModeManager()
        self.progress_emitter: Optional[Callable[[UploadItem], Any]] = None
        self.db_logger: Optional[Callable[[UploadItem], Any]] = None

    def set_callbacks(self, progress_emitter=None, db_logger=None):
        self.progress_emitter = progress_emitter
        self.db_logger = db_logger

    def start_workers(self):
        """Starts background worker tasks and scheduler monitor."""
        if not self._is_running:
            self._is_running = True
            self._scheduler_task = asyncio.create_task(self._scheduler_loop())
            for i in range(self.max_concurrent):
                worker = asyncio.create_task(self._worker_loop(i))
                self._workers.append(worker)
            logger.info(f"Started {self.max_concurrent} upload queue worker(s) & Scheduler loop.")

    async def stop_workers(self):
        """Stops all background queue workers and scheduler."""
        self._is_running = False
        if self._scheduler_task:
            self._scheduler_task.cancel()
            self._scheduler_task = None
        for worker in self._workers:
            worker.cancel()
        self._workers.clear()
        logger.info("Upload queue workers stopped.")

    async def _scheduler_loop(self):
        """Monitors scheduled items and activates them when their trigger time arrives."""
        while self._is_running:
            try:
                now = time.time()
                for task_id, item in list(self.tasks.items()):
                    if item.status == UploadStatus.SCHEDULED and item.scheduled_at:
                        if now >= item.scheduled_at:
                            logger.info(f"Scheduled timer reached for task {task_id} ({item.display_filename}). Activating upload.")
                            item.status = UploadStatus.QUEUED
                            item.scheduled_at = None
                            item.pause_event.set()
                            await self.queue.put(item)
                            self._notify_update(item)
            except Exception as e:
                logger.debug(f"Scheduler tick error: {e}")
            await asyncio.sleep(1.0)

    async def add_task(self, item: UploadItem, scheduled_at: Optional[float] = None) -> str:
        """Adds a new upload item to the processing queue (or schedule)."""
        self.tasks[item.id] = item
        if scheduled_at and scheduled_at > time.time():
            item.scheduled_at = scheduled_at
            item.status = UploadStatus.SCHEDULED
            logger.info(f"Scheduled task: {item.id} ({item.display_filename}) for timestamp {scheduled_at} (in {int(scheduled_at - time.time())}s)")
            self._notify_update(item)
            return item.id

        await self.queue.put(item)
        logger.info(f"Enqueued upload task: {item.id} ({item.display_filename})")
        self._notify_update(item)
        return item.id

    def get_task(self, task_id: str) -> Optional[UploadItem]:
        return self.tasks.get(task_id)

    def get_all_tasks(self) -> List[UploadItem]:
        return list(self.tasks.values())

    def schedule_task(self, task_id: str, scheduled_at: float) -> bool:
        item = self.tasks.get(task_id)
        if item and item.status in (UploadStatus.QUEUED, UploadStatus.PAUSED, UploadStatus.SCHEDULED):
            item.scheduled_at = scheduled_at
            item.status = UploadStatus.SCHEDULED
            logger.info(f"Task {task_id} scheduled for timestamp {scheduled_at}")
            self._notify_update(item)
            return True
        return False

    async def start_task_now(self, task_id: str) -> bool:
        item = self.tasks.get(task_id)
        if item and item.status in (UploadStatus.SCHEDULED, UploadStatus.PAUSED):
            item.scheduled_at = None
            item.status = UploadStatus.QUEUED
            item.pause_event.set()
            await self.queue.put(item)
            logger.info(f"Task {task_id} manually started via 'Start Now'")
            self._notify_update(item)
            return True
        return False

    def set_night_mode(self, enabled: bool, start_time: str = "01:00", end_time: str = "06:00") -> dict:
        self.night_mode.enabled = enabled
        self.night_mode.start_time = start_time
        self.night_mode.end_time = end_time
        logger.info(f"Night Mode config updated: Enabled={enabled}, Window={start_time}-{end_time}")
        return self.night_mode.to_dict()

    def get_night_mode(self) -> dict:
        return self.night_mode.to_dict()

    def pause_task(self, task_id: str) -> bool:
        item = self.tasks.get(task_id)
        if item and item.status in (UploadStatus.UPLOADING, UploadStatus.SPLITTING, UploadStatus.QUEUED, UploadStatus.SCHEDULED):
            item.pause_event.clear()
            item.status = UploadStatus.PAUSED
            logger.info(f"Paused task: {task_id}")
            self._notify_update(item)
            return True
        return False

    def resume_task(self, task_id: str) -> bool:
        item = self.tasks.get(task_id)
        if item and item.status == UploadStatus.PAUSED:
            item.pause_event.set()
            item.status = UploadStatus.UPLOADING
            logger.info(f"Resumed task: {task_id}")
            self._notify_update(item)
            return True
        return False

    def cancel_task(self, task_id: str) -> bool:
        item = self.tasks.get(task_id)
        if item and item.status not in (UploadStatus.COMPLETED, UploadStatus.CANCELLED, UploadStatus.FAILED):
            item.cancel_event.set()
            item.pause_event.set()  # Unblock in case it was paused
            item.status = UploadStatus.CANCELLED
            logger.info(f"Cancelled task: {task_id}")
            self._notify_update(item)
            return True
        return False

    def pause_all_tasks(self) -> int:
        """Pauses all active/queued upload tasks."""
        count = 0
        for task_id in list(self.tasks.keys()):
            if self.pause_task(task_id):
                count += 1
        logger.info(f"Paused {count} task(s) via batch control.")
        return count

    def resume_all_tasks(self) -> int:
        """Resumes all paused upload tasks."""
        count = 0
        for task_id in list(self.tasks.keys()):
            if self.resume_task(task_id):
                count += 1
        logger.info(f"Resumed {count} task(s) via batch control.")
        return count

    def cancel_all_tasks(self) -> int:
        """Cancels all running/queued upload tasks."""
        count = 0
        for task_id in list(self.tasks.keys()):
            if self.cancel_task(task_id):
                count += 1
        logger.info(f"Cancelled {count} task(s) via batch control.")
        return count

    def clear_completed_tasks(self) -> int:
        """Removes completed, cancelled, or failed tasks from memory."""
        to_remove = [
            tid for tid, item in self.tasks.items()
            if item.status in (UploadStatus.COMPLETED, UploadStatus.CANCELLED, UploadStatus.FAILED)
        ]
        for tid in to_remove:
            self.tasks.pop(tid, None)
        logger.info(f"Cleared {len(to_remove)} finished task(s) from memory.")
        return len(to_remove)

    def _notify_update(self, item: UploadItem):
        if self.progress_emitter:
            try:
                res = self.progress_emitter(item)
                if asyncio.iscoroutine(res):
                    asyncio.create_task(res)
            except Exception as e:
                logger.debug(f"Error notifying progress: {e}")

    async def _worker_loop(self, worker_id: int):
        from app.services.rate_governor import rate_governor
        logger.debug(f"Worker {worker_id} ready.")
        while self._is_running:
            try:
                item: UploadItem = await self.queue.get()
            except asyncio.CancelledError:
                break

            if item.cancel_event.is_set():
                item.status = UploadStatus.CANCELLED
                self.queue.task_done()
                self._notify_update(item)
                continue

            # Check if Telegram FloodWait cooldown is active
            await rate_governor.wait_if_cooling_down()

            # If Night Mode is enabled and we are outside the night window, wait gracefully
            while self._is_running and self.night_mode.enabled and not self.night_mode.is_in_night_window():
                if item.cancel_event.is_set():
                    break
                logger.info(f"Night Mode active: Task {item.id} waiting for night window ({self.night_mode.start_time} - {self.night_mode.end_time})...")
                await asyncio.sleep(15)

            try:
                await self._process_upload(item)
            except UploadCancelledError:
                logger.info(f"Task {item.id} cancelled by user.")
                item.status = UploadStatus.CANCELLED
                item.error_message = "Cancelled by user"
                self._notify_update(item)
            except Exception as e:
                logger.error(f"Error processing task {item.id}: {e}", exc_info=True)
                item.status = UploadStatus.FAILED
                item.error_message = str(e)
                self._notify_update(item)
            finally:
                self.queue.task_done()
                if self.db_logger:
                    try:
                        res = self.db_logger(item)
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception as e:
                        logger.error(f"Failed to log task to DB: {e}")

    async def _process_upload(self, item: UploadItem):
        try:
            client = await TelegramClientManager.get_client()
            if not await client.is_user_authorized():
                raise UploadFailedError("Telegram client is not authorized. Run setup_auth.py first.")

            item.status = UploadStatus.PREPARING
            self._notify_update(item)

            file_size = item.file_path.stat().st_size
            item.file_size = file_size

            # Dynamic split threshold based on Telegram account tier (4GB for Premium, 2GB for standard)
            cached_user = TelegramClientManager._cached_me or {}
            is_premium = cached_user.get("is_premium", False)
            active_split_threshold = int(3.95 * 1024 * 1024 * 1024) if is_premium else SPLIT_THRESHOLD

            # Check if file needs multi-part sequence slicing (> active_split_threshold)
            if file_size > active_split_threshold:
                item.status = UploadStatus.SPLITTING
                item.total_parts = math.ceil(file_size / active_split_threshold)
                item.progress = 100.0
                self._notify_update(item)
                logger.info(f"Zero-copy direct slice prepared for {item.display_filename}: {item.total_parts} sequence slices (threshold: {active_split_threshold // (1024*1024)}MB, Premium={is_premium})")
            else:
                item.total_parts = 1

            item.status = UploadStatus.UPLOADING
            self._notify_update(item)

            total_file_bytes = item.file_size
            bytes_before_current_part = 0
            last_time = time.time()
            last_bytes = 0

            # Calculate slice byte boundaries
            slices = []
            for part_index, offset in enumerate(range(0, file_size, active_split_threshold), start=1):
                slice_len = min(active_split_threshold, file_size - offset)
                slices.append((part_index, offset, slice_len))

            # Upload each part via zero-copy direct range slicing
            for part_index, slice_offset, slice_len in slices:
                if item.cancel_event.is_set():
                    raise UploadCancelledError("Upload cancelled by user.")

                item.current_part = part_index
                part_filename = item.display_filename

                # Format caption for multi-part uploads
                part_caption = item.caption or ""
                if item.total_parts > 1:
                    part_filename = f"{item.display_filename}.part{part_index:03d}"
                    part_suffix = f"\n📦 Part {part_index} of {item.total_parts} ({item.display_filename})"
                    part_caption = (item.caption + part_suffix).strip()

                last_emit_time = 0

                def _part_progress(curr, tot):
                    nonlocal last_time, last_bytes, last_emit_time
                    now = time.time()
                    
                    # Throttle progress updates to ~3 per second
                    if (now - last_emit_time) < 0.3 and curr < tot:
                        return
                    last_emit_time = now

                    overall_current = bytes_before_current_part + curr
                    item.uploaded_bytes = overall_current
                    item.progress = min(100.0, round((overall_current / max(1, total_file_bytes)) * 100, 1))

                    elapsed = now - last_time
                    if elapsed >= 0.5:
                        delta_bytes = overall_current - last_bytes
                        item.speed = max(0.0, delta_bytes / elapsed)
                        remaining_bytes = max(0, total_file_bytes - overall_current)
                        item.eta = (remaining_bytes / item.speed) if item.speed > 0 else 0
                        last_time = now
                        last_bytes = overall_current

                    self._notify_update(item)

                msg = await send_file_to_telegram(
                    client=client,
                    file_path=item.file_path,
                    chat_id=item.chat_id,
                    caption=part_caption,
                    custom_filename=part_filename,
                    send_as=item.send_as,
                    progress_callback=_part_progress,
                    pause_event=item.pause_event,
                    cancel_event=item.cancel_event,
                    byte_offset=slice_offset,
                    byte_length=slice_len
                )

                if msg:
                    item.message_ids.append(msg.id)

                bytes_before_current_part += slice_len

            # Mark completed
            item.status = UploadStatus.COMPLETED
            item.progress = 100.0
            item.uploaded_bytes = total_file_bytes
            item.completed_at = time.time()
            self._notify_update(item)

            # Notify folder watcher if this was a watched sync file
            try:
                from app.services.folder_watcher import folder_watcher
                first_msg_id = item.message_ids[0] if item.message_ids else 0
                folder_watcher.on_file_uploaded(str(item.file_path), str(item.chat_id), first_msg_id)
            except Exception as we:
                logger.debug(f"Folder watcher notify notice: {we}")

        finally:
            # Guarantee safe cleanup of uploaded source temp file
            if item.is_temp_file and item.file_path and item.file_path.exists():
                try:
                    item.file_path.unlink(missing_ok=True)
                except Exception:
                    pass


# Global singleton queue manager supporting concurrent multi-file uploads
queue_manager = QueueManager(max_concurrent=2)
