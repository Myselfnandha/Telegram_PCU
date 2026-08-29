import aiosqlite
import logging
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from app.config import DB_PATH
from app.models import UploadHistoryItem
from app.services.queue_manager import UploadItem

logger = logging.getLogger("history")

router = APIRouter(prefix="/api/history", tags=["history"])

async def init_db():
    """Initializes SQLite database table with WAL mode, index, and performance PRAGMAs."""
    async with aiosqlite.connect(str(DB_PATH), timeout=10.0) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        await db.execute("PRAGMA busy_timeout=10000;")
        await db.execute("PRAGMA temp_store=MEMORY;")
        await db.execute("PRAGMA cache_size=-64000;")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS upload_history (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                mime_type TEXT,
                chat_id TEXT NOT NULL,
                chat_name TEXT,
                message_id INTEGER,
                telegram_file_id TEXT,
                caption TEXT,
                send_as TEXT,
                status TEXT NOT NULL,
                error_message TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                parts_count INTEGER DEFAULT 1
            );
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_history_created 
            ON upload_history (created_at DESC);
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS cinema_videos_cache (
                chat_id TEXT PRIMARY KEY,
                videos_json TEXT NOT NULL,
                video_count INTEGER NOT NULL,
                updated_at REAL NOT NULL
            );
        """)
        await db.commit()
    logger.info(f"Initialized high-performance upload history DB at {DB_PATH}")


async def record_task_history(item: UploadItem):
    """Records or updates an upload task status in the SQLite history database."""
    try:
        created_str = datetime.fromtimestamp(item.created_at).strftime("%Y-%m-%d %H:%M:%S")
        completed_str = datetime.fromtimestamp(item.completed_at).strftime("%Y-%m-%d %H:%M:%S") if item.completed_at else None
        first_msg_id = item.message_ids[0] if item.message_ids else None

        async with aiosqlite.connect(str(DB_PATH), timeout=10.0) as db:
            await db.execute("""
                INSERT INTO upload_history (
                    id, filename, file_size, mime_type, chat_id, chat_name,
                    message_id, caption, send_as, status, error_message,
                    created_at, completed_at, parts_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status,
                    message_id=excluded.message_id,
                    error_message=excluded.error_message,
                    completed_at=excluded.completed_at,
                    parts_count=excluded.parts_count;
            """, (
                item.id,
                item.display_filename,
                item.file_size,
                item.mime_type,
                str(item.chat_id),
                item.chat_name,
                first_msg_id,
                item.caption,
                item.send_as,
                item.status.value,
                item.error_message,
                created_str,
                completed_str,
                item.total_parts
            ))
            await db.commit()
    except Exception as e:
        logger.error(f"Failed to record history for task {item.id}: {e}")


@router.get("", response_model=List[UploadHistoryItem])
async def get_history(limit: int = 50, offset: int = 0):
    """Fetch upload history ordered by newest first."""
    try:
        async with aiosqlite.connect(str(DB_PATH), timeout=10.0) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT * FROM upload_history 
                ORDER BY created_at DESC 
                LIMIT ? OFFSET ?
            """, (limit, offset))
            rows = await cursor.fetchall()
            return [UploadHistoryItem(**dict(row)) for row in rows]
    except Exception as e:
        logger.error(f"Error fetching upload history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/clear")
async def clear_history():
    """Clears all upload history records."""
    try:
        async with aiosqlite.connect(str(DB_PATH), timeout=10.0) as db:
            await db.execute("DELETE FROM upload_history")
            await db.commit()
        return {"status": "success", "message": "Upload history cleared"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def get_cinema_cached_videos_db(chat_id: str) -> Optional[dict]:
    """Retrieves cached video metadata list for a chat from SQLite in <1ms."""
    try:
        async with aiosqlite.connect(str(DB_PATH), timeout=5.0) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT videos_json, video_count, updated_at FROM cinema_videos_cache WHERE chat_id = ?", (str(chat_id),))
            row = await cursor.fetchone()
            if row:
                import json
                return {
                    "chat_id": str(chat_id),
                    "count": row["video_count"],
                    "videos": json.loads(row["videos_json"]),
                    "updated_at": row["updated_at"]
                }
    except Exception as e:
        logger.debug(f"SQLite cinema cache read notice: {e}")
    return None


async def save_cinema_cached_videos_db(chat_id: str, videos: list):
    """Persists fetched video metadata list for a chat into SQLite."""
    try:
        import json
        videos_json = json.dumps(videos)
        count = len(videos)
        now = datetime.utcnow().timestamp()
        async with aiosqlite.connect(str(DB_PATH), timeout=5.0) as db:
            await db.execute("""
                INSERT INTO cinema_videos_cache (chat_id, videos_json, video_count, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    videos_json = excluded.videos_json,
                    video_count = excluded.video_count,
                    updated_at = excluded.updated_at
            """, (str(chat_id), videos_json, count, now))
            await db.commit()
    except Exception as e:
        logger.debug(f"SQLite cinema cache write notice: {e}")
