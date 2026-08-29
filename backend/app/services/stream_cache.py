import os
import asyncio
import logging
from pathlib import Path
from typing import Optional, Dict, Tuple, List, BinaryIO

logger = logging.getLogger("stream_cache")

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "stream_cache"
MAX_CACHE_SIZE_BYTES = 5 * 1024 * 1024 * 1024  # 5 GB max local media cache

class StreamCacheManager:
    """
    High-Performance Local Stream Cache Manager.
    Caches Telegram MTProto media chunks on local disk for instant <1ms seek/replay (HTTP 206 Partial Content)
    and enables zero-latency FFmpeg local file transmuxing.
    """
    _instance: Optional["StreamCacheManager"] = None
    _lock = asyncio.Lock()
    _file_info: Dict[str, dict] = {}

    def __init__(self):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._scan_existing_cache()

    @classmethod
    def get_instance(cls) -> "StreamCacheManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _get_cache_key(self, chat_id: str, message_id: int) -> str:
        return f"{chat_id}_{message_id}"

    def get_cache_path(self, chat_id: str, message_id: int, filename: Optional[str] = None) -> Path:
        ext = os.path.splitext(filename)[1] if filename else ".bin"
        if not ext or len(ext) > 6:
            ext = ".bin"
        return CACHE_DIR / f"{chat_id}_{message_id}{ext}"

    def _scan_existing_cache(self):
        """Scans cached files on startup."""
        try:
            for p in CACHE_DIR.glob("*_*.*"):
                if p.is_file():
                    name_parts = p.stem.split("_", 1)
                    if len(name_parts) == 2:
                        key = p.stem
                        self._file_info[key] = {
                            "path": p,
                            "size": p.stat().st_size,
                            "completed": True
                        }
        except Exception as e:
            logger.warning(f"Error scanning stream cache: {e}")

    def is_cached(self, chat_id: str, message_id: int) -> bool:
        """Returns True if the complete media file is cached locally."""
        key = self._get_cache_key(chat_id, message_id)
        info = self._file_info.get(key)
        if info and info.get("path") and info["path"].exists():
            return True
        # Check disk directly
        for p in CACHE_DIR.glob(f"{chat_id}_{message_id}.*"):
            if p.exists() and p.stat().st_size > 0:
                self._file_info[key] = {"path": p, "size": p.stat().st_size, "completed": True}
                return True
        return False

    def get_cached_file(self, chat_id: str, message_id: int) -> Optional[Path]:
        """Returns the local path if cached."""
        key = self._get_cache_key(chat_id, message_id)
        info = self._file_info.get(key)
        if info and info.get("path") and info["path"].exists():
            return info["path"]
        for p in CACHE_DIR.glob(f"{chat_id}_{message_id}.*"):
            if p.exists() and p.stat().st_size > 0:
                self._file_info[key] = {"path": p, "size": p.stat().st_size, "completed": True}
                return p
        return None

    def get_cache_stats(self) -> dict:
        """Returns total cached files and disk space used."""
        total_size = 0
        file_count = 0
        try:
            for p in CACHE_DIR.glob("*"):
                if p.is_file():
                    total_size += p.stat().st_size
                    file_count += 1
        except Exception:
            pass
        return {
            "file_count": file_count,
            "total_bytes": total_size,
            "total_mb": round(total_size / (1024 * 1024), 2),
            "max_mb": round(MAX_CACHE_SIZE_BYTES / (1024 * 1024), 2)
        }

    def clear_cache(self) -> int:
        """Clears all cached video chunks from disk."""
        cleared_count = 0
        try:
            for p in CACHE_DIR.glob("*"):
                if p.is_file():
                    p.unlink(missing_ok=True)
                    cleared_count += 1
            self._file_info.clear()
        except Exception as e:
            logger.error(f"Error clearing stream cache: {e}")
        return cleared_count

    def evict_if_needed(self):
        """LRU cache cleanup when storage exceeds limit."""
        try:
            files = list(CACHE_DIR.glob("*"))
            total_size = sum(p.stat().st_size for p in files if p.is_file())
            if total_size > MAX_CACHE_SIZE_BYTES:
                # Sort by last access / modification time (oldest first)
                files.sort(key=lambda p: p.stat().st_mtime)
                for p in files:
                    if total_size <= MAX_CACHE_SIZE_BYTES * 0.7:  # Free down to 70%
                        break
                    size = p.stat().st_size
                    p.unlink(missing_ok=True)
                    total_size -= size
                    logger.info(f"Evicted cache file: {p.name}")
        except Exception as e:
            logger.warning(f"Error during cache eviction: {e}")

stream_cache_service = StreamCacheManager.get_instance()
