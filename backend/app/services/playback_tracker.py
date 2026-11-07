"""
Playback Progress & Continue Watching Tracker.
Maintains persistent watch history in SQLite for seamless VLC resume.
"""

import os
import time
import sqlite3
import aiosqlite
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
        """Initializes the playback_history SQLite table with chunk and byte offsets."""
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
                    thumbnail_url TEXT,
                    last_chunk_index INTEGER DEFAULT 0,
                    last_byte_offset INTEGER DEFAULT 0
                )
            """)
            # Auto-migrate if table existed without chunk columns
            for col, col_type in [("last_chunk_index", "INTEGER DEFAULT 0"), ("last_byte_offset", "INTEGER DEFAULT 0")]:
                try:
                    cursor.execute(f"ALTER TABLE playback_history ADD COLUMN {col} {col_type}")
                except sqlite3.OperationalError:
                    pass
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
        thumbnail_url: Optional[str] = None,
        last_chunk_index: int = 0,
        last_byte_offset: int = 0,
        is_reset: bool = False
    ):
        """Updates playback position, chunk index, and calculates progress percentage."""
        if not filename or filename == "thumb":
            return

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

        if duration_seconds <= 15.0 and file_size > 10 * 1024 * 1024:
            existing_rec = self.get_playback_progress(chat_id, message_id)
            if existing_rec and existing_rec.get("duration_seconds", 0) > 15.0:
                duration_seconds = float(existing_rec["duration_seconds"])
            else:
                duration_seconds = max(60.0, file_size / (350 * 1024))
        elif duration_seconds <= 0 and file_size > 0:
            duration_seconds = max(60.0, file_size / (350 * 1024))

        if is_reset:
            last_position_seconds = 0.0
            progress_percent = 0.0
        elif duration_seconds > 0 and last_position_seconds > 0:
            progress_percent = min(100.0, (last_position_seconds / duration_seconds) * 100.0)
        else:
            progress_percent = 0.0

        record_id = f"{chat_id}_{message_id}"
        now = time.time()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            if progress_percent >= 98.0 and not is_reset:
                self.remove_progress(chat_id, message_id)
                return
            cursor.execute("""
                INSERT INTO playback_history (
                    id, chat_id, message_id, filename, display_title,
                    file_size, duration_seconds, last_position_seconds,
                    progress_percent, last_watched_at, is_series,
                    series_title, season_num, episode_num, thumbnail_url,
                    last_chunk_index, last_byte_offset
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    filename = CASE WHEN excluded.filename NOT LIKE 'tg_media_%' AND excluded.filename != 'thumb' THEN excluded.filename ELSE playback_history.filename END,
                    display_title = CASE WHEN excluded.filename NOT LIKE 'tg_media_%' AND excluded.filename != 'thumb' THEN excluded.display_title ELSE playback_history.display_title END,
                    last_position_seconds = CASE 
                        WHEN ? THEN 0.0
                        WHEN excluded.last_position_seconds > 0 AND NOT (excluded.last_position_seconds <= 10.0 AND playback_history.last_position_seconds >= 30.0) THEN excluded.last_position_seconds 
                        ELSE playback_history.last_position_seconds 
                    END,
                    duration_seconds = CASE 
                        WHEN excluded.duration_seconds > 15.0 THEN excluded.duration_seconds 
                        WHEN playback_history.duration_seconds > 15.0 THEN playback_history.duration_seconds 
                        ELSE excluded.duration_seconds 
                    END,
                    progress_percent = CASE 
                        WHEN ? THEN 0.0
                        WHEN excluded.progress_percent > 0 AND NOT (excluded.last_position_seconds <= 10.0 AND playback_history.last_position_seconds >= 30.0) THEN excluded.progress_percent 
                        ELSE playback_history.progress_percent 
                    END,
                    last_watched_at = excluded.last_watched_at,
                    thumbnail_url = COALESCE(excluded.thumbnail_url, playback_history.thumbnail_url),
                    last_chunk_index = CASE WHEN excluded.last_chunk_index > 0 THEN excluded.last_chunk_index ELSE playback_history.last_chunk_index END,
                    last_byte_offset = CASE WHEN excluded.last_byte_offset > 0 THEN excluded.last_byte_offset ELSE playback_history.last_byte_offset END
            """, (
                record_id, str(chat_id), message_id, filename, display_title,
                file_size, duration_seconds, last_position_seconds,
                progress_percent, now, is_series,
                series_title, season_num, episode_num, thumbnail_url,
                last_chunk_index, last_byte_offset,
                1 if is_reset else 0,
                1 if is_reset else 0
            ))
            conn.commit()

    def update_byte_progress(
        self,
        chat_id: str,
        message_id: int,
        filename: str,
        streamed_byte_offset: int,
        total_file_size: int,
        chunk_size: int = 512 * 1024,
        media_duration: float = 0.0
    ):
        if not filename or filename == "thumb":
            return
        if total_file_size <= 0:
            return

        existing = self.get_playback_progress(chat_id, message_id)
        ratio = min(1.0, max(0.0, streamed_byte_offset / total_file_size)) if total_file_size > 0 else 0.0

        if streamed_byte_offset >= (total_file_size - 64 * 1024 * 1024) or ratio >= 0.88:
            existing_pct = float(existing.get("progress_percent", 0.0)) if existing else 0.0
            if existing_pct < 80.0:
                return

        if streamed_byte_offset <= 4 * 1024 * 1024:
            existing_pos = float(existing.get("last_position_seconds", 0.0)) if existing else 0.0
            if existing_pos > 20.0:
                return

        chunk_index = streamed_byte_offset // chunk_size
        record_id = f"{chat_id}_{message_id}"
        now = time.time()

        if media_duration > 15.0:
            duration_seconds = media_duration
        else:
            duration_seconds = (existing.get("duration_seconds") or 0.0) if existing else 0.0
            if duration_seconds <= 15.0 and total_file_size > 0:
                duration_seconds = max(60.0, total_file_size / (350 * 1024))

        inferred_position_seconds = round(ratio * duration_seconds, 1) if duration_seconds > 0 else 0.0
        inferred_percent = round(min(100.0, ratio * 100.0), 1)

        if inferred_percent >= 98.0:
            self.remove_progress(chat_id, message_id)
            return

        parsed = subtitle_service.parse_media_title(filename)
        display_title = parsed.get("title", filename)
        is_series = 1 if parsed.get("is_series") else 0
        series_title = parsed.get("title") if is_series else None
        season_num = parsed.get("season")
        episode_num = parsed.get("episode")
        if is_series and season_num and episode_num:
            display_title = f"{series_title} S{season_num:02d}E{episode_num:02d}"
        elif parsed.get("year"):
            display_title = f"{display_title} ({parsed.get('year')})"

        thumb_url = f"/dl/{chat_id}/{message_id}/thumb"

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO playback_history (
                    id, chat_id, message_id, filename, display_title,
                    file_size, duration_seconds, last_position_seconds,
                    progress_percent, last_watched_at, is_series,
                    series_title, season_num, episode_num, thumbnail_url,
                    last_chunk_index, last_byte_offset
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    last_chunk_index = excluded.last_chunk_index,
                    last_byte_offset = excluded.last_byte_offset,
                    last_watched_at = excluded.last_watched_at,
                    duration_seconds = CASE 
                        WHEN excluded.duration_seconds > 15.0 THEN excluded.duration_seconds 
                        WHEN playback_history.duration_seconds > 15.0 THEN playback_history.duration_seconds 
                        ELSE excluded.duration_seconds 
                    END,
                    last_position_seconds = excluded.last_position_seconds,
                    progress_percent = excluded.progress_percent,
                    thumbnail_url = COALESCE(playback_history.thumbnail_url, excluded.thumbnail_url)
            """, (
                record_id, str(chat_id), message_id, filename, display_title,
                total_file_size, duration_seconds, inferred_position_seconds,
                inferred_percent, now, is_series,
                series_title, season_num, episode_num, thumb_url,
                chunk_index, streamed_byte_offset
            ))
            conn.commit()

    def get_playback_progress(self, chat_id: str, message_id: int) -> Optional[Dict[str, Any]]:
        """Returns watch progress and chunk offsets for a specific video."""
        record_id = f"{chat_id}_{message_id}"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM playback_history WHERE id = ?", (record_id,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["playback_seconds"] = d.get("last_position_seconds", 0.0)
                d["total_seconds"] = d.get("duration_seconds", 0.0)
                d["percent"] = round(d.get("progress_percent", 0.0), 1)
                d["thumb_url"] = d.get("thumbnail_url") or f"/dl/{d.get('chat_id')}/{d.get('message_id')}/thumb"
                d["last_watched"] = d.get("last_watched_at")
                return d
        return None

    def get_continue_watching(self, limit: int = 12) -> List[Dict[str, Any]]:
        """
        Returns actively watched videos (progress >= 1% or position >= 10s)
        ordered by most recently watched, with normalized keys for frontend and backend compatibility.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM playback_history
                WHERE (progress_percent >= 1.0 OR last_position_seconds >= 10.0)
                  AND progress_percent < 96.0
                  AND filename != 'thumb'
                ORDER BY last_watched_at DESC
                LIMIT ?
            """, (limit,))
            rows = cursor.fetchall()
            results = []
            for r in rows:
                d = dict(r)
                d["playback_seconds"] = d.get("last_position_seconds", 0.0)
                d["total_seconds"] = d.get("duration_seconds", 0.0)
                d["percent"] = round(d.get("progress_percent", 0.0), 1)
                d["thumb_url"] = d.get("thumbnail_url") or f"/dl/{d.get('chat_id')}/{d.get('message_id')}/thumb"
                d["last_watched"] = d.get("last_watched_at")
                results.append(d)
            return results

    def remove_progress(self, chat_id: str, message_id: int):
        """Removes an item from continue watching."""
        record_id = f"{chat_id}_{message_id}"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM playback_history WHERE id = ?", (record_id,))
            conn.commit()

    async def ainit_db(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(str(DB_PATH), timeout=10.0) as db:
            await db.execute("""
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
                    thumbnail_url TEXT,
                    last_chunk_index INTEGER DEFAULT 0,
                    last_byte_offset INTEGER DEFAULT 0
                )
            """)
            for col, col_type in [("last_chunk_index", "INTEGER DEFAULT 0"), ("last_byte_offset", "INTEGER DEFAULT 0")]:
                try:
                    await db.execute(f"ALTER TABLE playback_history ADD COLUMN {col} {col_type}")
                except Exception:
                    pass
            await db.execute("CREATE INDEX IF NOT EXISTS idx_playback_last_watched ON playback_history(last_watched_at DESC)")
            await db.commit()

    async def aupdate_progress(
        self,
        chat_id: str,
        message_id: int,
        filename: str,
        last_position_seconds: float = 0.0,
        duration_seconds: float = 0.0,
        file_size: int = 0,
        thumbnail_url: Optional[str] = None,
        last_chunk_index: int = 0,
        last_byte_offset: int = 0,
        is_reset: bool = False
    ):
        if not filename or filename == "thumb":
            return

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

        if duration_seconds <= 15.0 and file_size > 10 * 1024 * 1024:
            existing_rec = await self.aget_playback_progress(chat_id, message_id)
            if existing_rec and existing_rec.get("duration_seconds", 0) > 15.0:
                duration_seconds = float(existing_rec["duration_seconds"])
            else:
                duration_seconds = max(60.0, file_size / (350 * 1024))
        elif duration_seconds <= 0 and file_size > 0:
            duration_seconds = max(60.0, file_size / (350 * 1024))

        if is_reset:
            last_position_seconds = 0.0
            progress_percent = 0.0
        elif duration_seconds > 0 and last_position_seconds > 0:
            progress_percent = min(100.0, (last_position_seconds / duration_seconds) * 100.0)
        else:
            progress_percent = 0.0

        if progress_percent >= 98.0 and not is_reset:
            await self.aremove_progress(chat_id, message_id)
            return

        record_id = f"{chat_id}_{message_id}"
        now = time.time()

        async with aiosqlite.connect(str(DB_PATH), timeout=10.0) as db:
            await db.execute("""
                INSERT INTO playback_history (
                    id, chat_id, message_id, filename, display_title,
                    file_size, duration_seconds, last_position_seconds,
                    progress_percent, last_watched_at, is_series,
                    series_title, season_num, episode_num, thumbnail_url,
                    last_chunk_index, last_byte_offset
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    filename = CASE WHEN excluded.filename NOT LIKE 'tg_media_%' AND excluded.filename != 'thumb' THEN excluded.filename ELSE playback_history.filename END,
                    display_title = CASE WHEN excluded.filename NOT LIKE 'tg_media_%' AND excluded.filename != 'thumb' THEN excluded.display_title ELSE playback_history.display_title END,
                    last_position_seconds = CASE 
                        WHEN ? THEN 0.0
                        WHEN excluded.last_position_seconds > 0 AND NOT (excluded.last_position_seconds <= 10.0 AND playback_history.last_position_seconds >= 30.0) THEN excluded.last_position_seconds 
                        ELSE playback_history.last_position_seconds 
                    END,
                    duration_seconds = CASE 
                        WHEN excluded.duration_seconds > 15.0 THEN excluded.duration_seconds 
                        WHEN playback_history.duration_seconds > 15.0 THEN playback_history.duration_seconds 
                        ELSE excluded.duration_seconds 
                    END,
                    progress_percent = CASE 
                        WHEN ? THEN 0.0
                        WHEN excluded.progress_percent > 0 AND NOT (excluded.last_position_seconds <= 10.0 AND playback_history.last_position_seconds >= 30.0) THEN excluded.progress_percent 
                        ELSE playback_history.progress_percent 
                    END,
                    last_watched_at = excluded.last_watched_at,
                    thumbnail_url = COALESCE(excluded.thumbnail_url, playback_history.thumbnail_url),
                    last_chunk_index = CASE WHEN excluded.last_chunk_index > 0 THEN excluded.last_chunk_index ELSE playback_history.last_chunk_index END,
                    last_byte_offset = CASE WHEN excluded.last_byte_offset > 0 THEN excluded.last_byte_offset ELSE playback_history.last_byte_offset END
            """, (
                record_id, str(chat_id), message_id, filename, display_title,
                file_size, duration_seconds, last_position_seconds,
                progress_percent, now, is_series,
                series_title, season_num, episode_num, thumbnail_url,
                last_chunk_index, last_byte_offset,
                1 if is_reset else 0,
                1 if is_reset else 0
            ))
            await db.commit()

    async def aupdate_byte_progress(
        self,
        chat_id: str,
        message_id: int,
        filename: str,
        streamed_byte_offset: int,
        total_file_size: int,
        chunk_size: int = 512 * 1024,
        media_duration: float = 0.0
    ):
        if not filename or filename == "thumb":
            return
        if total_file_size <= 0:
            return

        existing = await self.aget_playback_progress(chat_id, message_id)
        ratio = min(1.0, max(0.0, streamed_byte_offset / total_file_size)) if total_file_size > 0 else 0.0

        if streamed_byte_offset >= (total_file_size - 64 * 1024 * 1024) or ratio >= 0.88:
            existing_pct = float(existing.get("progress_percent", 0.0)) if existing else 0.0
            if existing_pct < 80.0:
                return

        if streamed_byte_offset <= 4 * 1024 * 1024:
            existing_pos = float(existing.get("last_position_seconds", 0.0)) if existing else 0.0
            if existing_pos > 20.0:
                return

        chunk_index = streamed_byte_offset // chunk_size
        record_id = f"{chat_id}_{message_id}"
        now = time.time()

        if media_duration > 15.0:
            duration_seconds = media_duration
        else:
            duration_seconds = (existing.get("duration_seconds") or 0.0) if existing else 0.0
            if duration_seconds <= 15.0 and total_file_size > 0:
                duration_seconds = max(60.0, total_file_size / (350 * 1024))

        inferred_position_seconds = round(ratio * duration_seconds, 1) if duration_seconds > 0 else 0.0
        inferred_percent = round(min(100.0, ratio * 100.0), 1)

        if inferred_percent >= 98.0:
            await self.aremove_progress(chat_id, message_id)
            return

        parsed = subtitle_service.parse_media_title(filename)
        display_title = parsed.get("title", filename)
        is_series = 1 if parsed.get("is_series") else 0
        series_title = parsed.get("title") if is_series else None
        season_num = parsed.get("season")
        episode_num = parsed.get("episode")
        if is_series and season_num and episode_num:
            display_title = f"{series_title} S{season_num:02d}E{episode_num:02d}"
        elif parsed.get("year"):
            display_title = f"{display_title} ({parsed.get('year')})"

        thumb_url = f"/dl/{chat_id}/{message_id}/thumb"

        async with aiosqlite.connect(str(DB_PATH), timeout=10.0) as db:
            await db.execute("""
                INSERT INTO playback_history (
                    id, chat_id, message_id, filename, display_title,
                    file_size, duration_seconds, last_position_seconds,
                    progress_percent, last_watched_at, is_series,
                    series_title, season_num, episode_num, thumbnail_url,
                    last_chunk_index, last_byte_offset
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    last_chunk_index = excluded.last_chunk_index,
                    last_byte_offset = excluded.last_byte_offset,
                    last_watched_at = excluded.last_watched_at,
                    duration_seconds = CASE 
                        WHEN excluded.duration_seconds > 15.0 THEN excluded.duration_seconds 
                        WHEN playback_history.duration_seconds > 15.0 THEN playback_history.duration_seconds 
                        ELSE excluded.duration_seconds 
                    END,
                    last_position_seconds = excluded.last_position_seconds,
                    progress_percent = excluded.progress_percent,
                    thumbnail_url = COALESCE(playback_history.thumbnail_url, excluded.thumbnail_url)
            """, (
                record_id, str(chat_id), message_id, filename, display_title,
                total_file_size, duration_seconds, inferred_position_seconds,
                inferred_percent, now, is_series,
                series_title, season_num, episode_num, thumb_url,
                chunk_index, streamed_byte_offset
            ))
            await db.commit()

    async def aget_playback_progress(self, chat_id: str, message_id: int) -> Optional[Dict[str, Any]]:
        from app.services.telegram_sync import canonicalize_chat_id
        from app.telegram_client import TelegramClientManager
        my_id = None
        try:
            me = TelegramClientManager.get_cached_me() or {}
            my_id = me.get("id")
        except Exception:
            pass

        canonical_id, aliases = canonicalize_chat_id(chat_id, my_id)
        candidate_ids = list(set([str(chat_id), canonical_id] + aliases))
        placeholders = ",".join("?" for _ in candidate_ids)

        async with aiosqlite.connect(str(DB_PATH), timeout=10.0) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(f"""
                SELECT * FROM playback_history 
                WHERE (chat_id IN ({placeholders}) AND message_id = ?)
                   OR id IN ({placeholders})
                ORDER BY last_watched_at DESC
                LIMIT 1
            """, (*candidate_ids, message_id, *[f"{cid}_{message_id}" for cid in candidate_ids])) as cursor:
                row = await cursor.fetchone()
                if row:
                    d = dict(row)
                    d["playback_seconds"] = d.get("last_position_seconds", 0.0)
                    d["total_seconds"] = d.get("duration_seconds", 0.0)
                    d["percent"] = round(d.get("progress_percent", 0.0), 1)
                    d["thumb_url"] = d.get("thumbnail_url") or f"/dl/{d.get('chat_id')}/{d.get('message_id')}/thumb"
                    d["last_watched"] = d.get("last_watched_at")
                    return d
        return None

    async def aget_continue_watching(self, limit: int = 12) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(str(DB_PATH), timeout=10.0) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT * FROM playback_history
                WHERE (progress_percent >= 1.0 OR last_position_seconds >= 10.0)
                  AND progress_percent < 96.0
                  AND filename != 'thumb'
                ORDER BY last_watched_at DESC
                LIMIT ?
            """, (limit,)) as cursor:
                rows = await cursor.fetchall()
                results = []
                for r in rows:
                    d = dict(r)
                    d["playback_seconds"] = d.get("last_position_seconds", 0.0)
                    d["total_seconds"] = d.get("duration_seconds", 0.0)
                    d["percent"] = round(d.get("progress_percent", 0.0), 1)
                    d["thumb_url"] = d.get("thumbnail_url") or f"/dl/{d.get('chat_id')}/{d.get('message_id')}/thumb"
                    d["last_watched"] = d.get("last_watched_at")
                    results.append(d)
                return results

    async def aremove_progress(self, chat_id: str, message_id: int):
        record_id = f"{chat_id}_{message_id}"
        async with aiosqlite.connect(str(DB_PATH), timeout=10.0) as db:
            await db.execute("DELETE FROM playback_history WHERE id = ?", (record_id,))
            await db.commit()


playback_tracker = PlaybackTracker()


