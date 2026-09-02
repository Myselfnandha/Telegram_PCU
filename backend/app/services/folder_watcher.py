"""
Auto-Directory Watcher & Background Backup Sync Daemon.
Monitors local folders, detects new or updated files, and automatically enqueues them for MTProto upload.
"""

import os
import time
import sqlite3
import logging
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any, List

from app.config import DATA_DIR

logger = logging.getLogger("folder_watcher")

DEFAULT_SYNC_DIR = Path.home() / "Downloads" / "Telegram_Sync"
DB_PATH = DATA_DIR / "tg_power_suite.db"


class FolderWatcherDaemon:
    def __init__(self):
        self._enabled: bool = False
        self._watch_dir: Path = DEFAULT_SYNC_DIR
        self._target_chat: str = "me"
        self._delete_after_upload: bool = False
        self._scan_interval: float = 6.0
        self._task: Optional[asyncio.Task] = None
        self._file_stability_cache: Dict[str, Dict[str, Any]] = {}
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            self._watch_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS synced_files (
                    filepath TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    file_size INTEGER DEFAULT 0,
                    last_mtime REAL NOT NULL,
                    status TEXT DEFAULT 'synced',
                    uploaded_at REAL,
                    telegram_chat_id TEXT,
                    telegram_message_id INTEGER
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_synced_mtime ON synced_files(last_mtime DESC)")
            conn.commit()

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def start(self):
        """Starts the background directory watcher loop."""
        if self._task is None or self._task.done():
            self._enabled = True
            self._task = asyncio.create_task(self._watch_loop())
            logger.info(f"📁 FolderWatcherDaemon started. Watching: {self._watch_dir}")

    def stop(self):
        """Stops the background directory watcher loop."""
        self._enabled = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        logger.info("📁 FolderWatcherDaemon stopped.")

    async def _watch_loop(self):
        while self._enabled:
            try:
                await self.scan_directory()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Folder watcher scan error: {e}")
            await asyncio.sleep(self._scan_interval)

    async def scan_directory(self) -> List[str]:
        """
        Scans the watch directory for new or modified files.
        Verifies stability (debounce) before enqueuing to QueueManager.
        """
        if not self._watch_dir.exists():
            return []

        from app.services.queue_manager import queue_manager, UploadItem

        enqueued_files = []
        now = time.time()

        try:
            for root, dirs, files in os.walk(self._watch_dir):
                for fn in files:
                    # Ignore temporary files, hidden files, or parts
                    if fn.startswith(".") or fn.endswith((".crdownload", ".part", ".tmp", ".download", ".saving")):
                        continue

                    full_path = Path(root) / fn
                    try:
                        stat = full_path.stat()
                        fsize = stat.st_size
                        fmtime = stat.st_mtime
                    except Exception:
                        continue

                    if fsize == 0:
                        continue

                    # 1. Debounce / Write Stability Check
                    path_str = str(full_path.resolve())
                    last_seen = self._file_stability_cache.get(path_str)

                    if not last_seen:
                        self._file_stability_cache[path_str] = {"size": fsize, "mtime": fmtime, "time": now}
                        if (now - fmtime) < 1.0:
                            continue
                    else:
                        if last_seen["size"] != fsize or last_seen["mtime"] != fmtime:
                            self._file_stability_cache[path_str] = {"size": fsize, "mtime": fmtime, "time": now}
                            continue
                        if (now - last_seen["time"]) < 1.0:
                            continue

                    # 2. Check if already synced in DB
                    with self._get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT * FROM synced_files WHERE filepath = ?", (path_str,))
                        row = cursor.fetchone()

                    if row:
                        if row["status"] in ("synced", "uploading") and row["last_mtime"] == fmtime and row["file_size"] == fsize:
                            continue  # File already synced and unchanged

                    # 3. Mark as uploading in DB and Enqueue to QueueManager
                    with self._get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("""
                            INSERT INTO synced_files (filepath, filename, file_size, last_mtime, status, uploaded_at, telegram_chat_id)
                            VALUES (?, ?, ?, ?, 'uploading', ?, ?)
                            ON CONFLICT(filepath) DO UPDATE SET
                                status = 'uploading',
                                file_size = excluded.file_size,
                                last_mtime = excluded.last_mtime,
                                telegram_chat_id = excluded.telegram_chat_id
                        """, (path_str, fn, fsize, fmtime, now, str(self._target_chat)))
                        conn.commit()

                    import uuid
                    task_id = str(uuid.uuid4())
                    item = UploadItem(
                        task_id=task_id,
                        file_path=full_path,
                        original_filename=fn,
                        chat_id=self._target_chat,
                        caption=f"📁 Auto-Synced: {fn}",
                        send_as="auto",
                        is_temp_file=False
                    )
                    await queue_manager.add_task(item)
                    enqueued_files.append(fn)
                    logger.info(f"📁 FolderWatcher auto-enqueued: '{fn}' ({fsize / (1024*1024):.1f}MB) -> Task {task_id}")

        except Exception as scan_err:
            logger.debug(f"Error during folder scan: {scan_err}")

        return enqueued_files

    def on_file_uploaded(self, filepath: str, chat_id: str, message_id: int):
        """Called by FastUploader/QueueManager upon verified upload success."""
        path_str = str(Path(filepath).resolve())
        now = time.time()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE synced_files
                SET status = 'synced', uploaded_at = ?, telegram_chat_id = ?, telegram_message_id = ?
                WHERE filepath = ?
            """, (now, str(chat_id), message_id, path_str))
            conn.commit()

        # Handle post-upload auto-delete if enabled
        if self._delete_after_upload:
            try:
                p = Path(filepath)
                if p.exists() and p.is_file():
                    p.unlink()
                    logger.info(f"🗑️ FolderWatcher: Deleted local file after verified upload: {p.name}")
            except Exception as de:
                logger.warning(f"Could not delete local file {filepath}: {de}")

    def get_status(self) -> Dict[str, Any]:
        """Returns the current watcher status and statistics."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as total FROM synced_files WHERE status = 'synced'")
            synced_count = cursor.fetchone()["total"]

        return {
            "enabled": self._enabled,
            "watch_dir": str(self._watch_dir),
            "target_chat": str(self._target_chat),
            "delete_after_upload": self._delete_after_upload,
            "synced_files_count": synced_count,
            "status_label": "Active (Watching)" if self._enabled else "Paused"
        }

    def update_config(
        self,
        enabled: Optional[bool] = None,
        watch_dir: Optional[str] = None,
        target_chat: Optional[str] = None,
        delete_after_upload: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Updates watcher configuration dynamically."""
        if watch_dir:
            p = Path(watch_dir).expanduser().resolve()
            p.mkdir(parents=True, exist_ok=True)
            self._watch_dir = p
        if target_chat is not None:
            self._target_chat = target_chat
        if delete_after_upload is not None:
            self._delete_after_upload = bool(delete_after_upload)
        if enabled is not None:
            if enabled:
                self.start()
            else:
                self.stop()

        return self.get_status()


# Global singleton folder watcher daemon
folder_watcher = FolderWatcherDaemon()
