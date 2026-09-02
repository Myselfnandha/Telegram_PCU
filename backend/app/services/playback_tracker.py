"""
Playback Progress & Continue Watching Tracker.
Maintains persistent watch history in SQLite for seamless VLC resume.
"""

import os
import time
import sqlite3
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List

from app.config import DATA_DIR
from app.services.subtitle_service import subtitle_service

logger = logging.getLogger("playback_tracker")

DB_PATH = DATA_DIR / "tg_power_suite.db"


class PlaybackTracker:
    def __init__(self):
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Initializes the playback_history SQLite table."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS playback_history (
                    id TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    filename TEXT NOT NULL,
                    display_title TEXT,
                    file_size INTEGER DEFAULT 0,
                    duration_seconds REAL DEFAULT 0,
                    last_position_seconds REAL DEFAULT 0,
                    progress_percent REAL DEFAULT 0,
                    last_watched_at REAL NOT NULL,
                    is_series INTEGER DEFAULT 0,
                    series_title TEXT,
                    season_num INTEGER,
                    episode_num INTEGER,
                    thumbnail_url TEXT
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_playback_last_watched ON playback_history(last_watched_at DESC)")
            conn.commit()

    def update_progress(
        self,
        chat_id: str,
        message_id: int,
        filename: str,
        last_position_seconds: float = 0.0,
        duration_seconds: float = 0.0,
        file_size: int = 0,
        thumbnail_url: Optional[str] = None
    ):
        """Updates playback position and calculates progress percentage."""
        parsed = subtitle_service.parse_media_title(filename)
        is_series = 1 if parsed.get("is_series") else 0
        series_title = parsed.get("title") if is_series else None
        season_num = parsed.get("season")
        episode_num = parsed.get("episode")
        display_title = parsed.get("title", filename)

        if is_series and season_num and episode_num:
            display_title = f"{series_title} S{season_num:02d}E{episode_num:02d}"
        elif parsed.get("year"):
            display_title = f"{display_title} ({parsed.get('year')})"

        # If duration is missing, estimate based on size / typical bitrate (~3 Mbps)
        if duration_seconds <= 0 and file_size > 0:
            duration_seconds = max(60.0, file_size / (350 * 1024))  # ~2.8 Mbps average

        progress_percent = 0.0
        if duration_seconds > 0 and last_position_seconds > 0:
            progress_percent = min(100.0, (last_position_seconds / duration_seconds) * 100.0)

        record_id = f"{chat_id}_{message_id}"
        now = time.time()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO playback_history (
                    id, chat_id, message_id, filename, display_title,
                    file_size, duration_seconds, last_position_seconds,
                    progress_percent, last_watched_at, is_series,
                    series_title, season_num, episode_num, thumbnail_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    last_position_seconds = excluded.last_position_seconds,
                    duration_seconds = CASE WHEN excluded.duration_seconds > 0 THEN excluded.duration_seconds ELSE playback_history.duration_seconds END,
                    progress_percent = excluded.progress_percent,
                    last_watched_at = excluded.last_watched_at,
                    thumbnail_url = COALESCE(excluded.thumbnail_url, playback_history.thumbnail_url)
            """, (
                record_id, str(chat_id), message_id, filename, display_title,
                file_size, duration_seconds, last_position_seconds,
                progress_percent, now, is_series,
                series_title, season_num, episode_num, thumbnail_url
            ))
            conn.commit()

    def update_byte_progress(self, chat_id: str, message_id: int, filename: str, streamed_byte_offset: int, total_file_size: int):
        """Updates progress derived from VLC byte-range streaming requests."""
        if total_file_size <= 0:
            return

        ratio = min(1.0, streamed_byte_offset / total_file_size)
        # Estimate duration if unknown (e.g. 5400s / 90 mins average)
        estimated_duration = max(300.0, total_file_size / (350 * 1024))
        position_seconds = ratio * estimated_duration

        self.update_progress(
            chat_id=str(chat_id),
            message_id=message_id,
            filename=filename,
            last_position_seconds=position_seconds,
            duration_seconds=estimated_duration,
            file_size=total_file_size
        )

    def get_playback_progress(self, chat_id: str, message_id: int) -> Optional[Dict[str, Any]]:
        """Returns watch progress for a specific video."""
        record_id = f"{chat_id}_{message_id}"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM playback_history WHERE id = ?", (record_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)
        return None

    def get_continue_watching(self, limit: int = 12) -> List[Dict[str, Any]]:
        """
        Returns actively watched videos (between 2% and 95% complete)
        ordered by most recently watched.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM playback_history
                WHERE progress_percent >= 1.0 AND progress_percent < 96.0
                ORDER BY last_watched_at DESC
                LIMIT ?
            """, (limit,))
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def remove_progress(self, chat_id: str, message_id: int):
        """Removes an item from continue watching."""
        record_id = f"{chat_id}_{message_id}"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM playback_history WHERE id = ?", (record_id,))
            conn.commit()


# Global singleton playback tracker
playback_tracker = PlaybackTracker()
