import os
import shutil
import asyncio
import logging
from pathlib import Path
from typing import Optional, Dict, Tuple, List, BinaryIO

logger = logging.getLogger("stream_cache")

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "stream_cache"
MAX_CACHE_SIZE_BYTES = 5 * 1024 * 1024 * 1024  # 5 GB max local media cache

BLOCK_SIZE = 10 * 1024 * 1024  # 10 MB discrete chunk block size
BLOCKS_DIR = CACHE_DIR / "blocks"
BLOCKS_DIR.mkdir(parents=True, exist_ok=True)


class StreamCacheManager:
    """
    High-Performance Local Stream Cache Manager.
    Caches Telegram MTProto media in discrete 10MB segment parts on local disk for instant <1ms seek/replay
    and powers the Continuous Background Prefetcher for non-watched chunks.
    """
    _instance: Optional["StreamCacheManager"] = None
    _lock = asyncio.Lock()
    _file_info: Dict[str, dict] = {}

    def __init__(self):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        BLOCKS_DIR.mkdir(parents=True, exist_ok=True)
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

    def get_block_path(self, chat_id: str, message_id: int, block_idx: int) -> Path:
        """Returns the file path for a discrete 10MB chunk block."""
        return BLOCKS_DIR / f"{chat_id}_{message_id}_part{block_idx:05d}.bin"

    def has_block(self, chat_id: str, message_id: int, block_idx: int, expected_size: int) -> bool:
        """Returns True if the discrete 10MB block exists on disk and matches expected size."""
        p = self.get_block_path(chat_id, message_id, block_idx)
        return p.exists() and p.is_file() and p.stat().st_size == expected_size

    def read_block_slice(self, chat_id: str, message_id: int, block_idx: int, offset_in_block: int, length: int) -> Optional[bytes]:
        """Reads a byte slice from a discrete cached block file."""
        p = self.get_block_path(chat_id, message_id, block_idx)
        if not p.exists() or not p.is_file():
            return None
        try:
            with open(p, "rb") as f:
                f.seek(offset_in_block)
                return f.read(length)
        except Exception:
            return None

    def save_block(self, chat_id: str, message_id: int, block_idx: int, data: bytes):
        """Saves a discrete 10MB chunk part to disk."""
        p = self.get_block_path(chat_id, message_id, block_idx)
        tmp = p.with_suffix(".tmp")
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            tmp.rename(p)
            self.evict_if_needed()
        except Exception as e:
            logger.debug(f"Could not save stream block {block_idx}: {e}")

    def merge_blocks_if_complete(self, chat_id: str, message_id: int, total_file_size: int, filename: Optional[str] = None) -> Optional[Path]:
        """If all discrete blocks are present on disk, merges them into the final full movie file."""
        total_blocks = (total_file_size + BLOCK_SIZE - 1) // BLOCK_SIZE
        for i in range(total_blocks):
            expected = min(BLOCK_SIZE, total_file_size - (i * BLOCK_SIZE))
            if not self.has_block(chat_id, message_id, i, expected):
                return None

        # All blocks present -> Assemble into full cached file
        target_path = self.get_cache_path(chat_id, message_id, filename)
        tmp_target = target_path.with_suffix(".merging")
        try:
            with open(tmp_target, "wb") as out_f:
                for i in range(total_blocks):
                    block_file = self.get_block_path(chat_id, message_id, i)
                    with open(block_file, "rb") as bf:
                        shutil.copyfileobj(bf, out_f, length=1024 * 1024)
            tmp_target.rename(target_path)
            logger.info(f"Assembled complete movie from {total_blocks} blocks: {target_path.name}")

            # Clean block fragments
            for i in range(total_blocks):
                self.get_block_path(chat_id, message_id, i).unlink(missing_ok=True)

            self._file_info[self._get_cache_key(chat_id, message_id)] = {
                "path": target_path,
                "size": total_file_size,
                "completed": True
            }
            return target_path
        except Exception as me:
            logger.warning(f"Error assembling stream blocks: {me}")
            if tmp_target.exists():
                tmp_target.unlink(missing_ok=True)
            return None

    def _scan_existing_cache(self):
        """Scans fully cached completed files on startup."""
        try:
            for p in CACHE_DIR.glob("*_*.*"):
                if p.is_file() and p.suffix not in (".part", ".tmp", ".merging"):
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
        if info and info.get("path") and info["path"].exists() and info["path"].suffix not in (".part", ".tmp"):
            return True
        for p in CACHE_DIR.glob(f"{chat_id}_{message_id}.*"):
            if p.exists() and p.is_file() and p.suffix not in (".part", ".tmp") and p.stat().st_size > 0:
                self._file_info[key] = {"path": p, "size": p.stat().st_size, "completed": True}
                return True
        return False

    def get_cached_file(self, chat_id: str, message_id: int) -> Optional[Path]:
        """Returns the local path only if fully cached."""
        key = self._get_cache_key(chat_id, message_id)
        info = self._file_info.get(key)
        if info and info.get("path") and info["path"].exists() and info["path"].suffix not in (".part", ".tmp"):
            return info["path"]
        for p in CACHE_DIR.glob(f"{chat_id}_{message_id}.*"):
            if p.exists() and p.is_file() and p.suffix not in (".part", ".tmp") and p.stat().st_size > 0:
                self._file_info[key] = {"path": p, "size": p.stat().st_size, "completed": True}
                return p
        return None

    def get_cache_stats(self) -> dict:
        """Returns total cached files and disk space used."""
        total_size = 0
        file_count = 0
        try:
            for p in list(CACHE_DIR.glob("*")) + list(BLOCKS_DIR.glob("*")):
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
            for p in list(CACHE_DIR.glob("*")) + list(BLOCKS_DIR.glob("*")):
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
            files = [p for p in list(CACHE_DIR.glob("*")) + list(BLOCKS_DIR.glob("*")) if p.is_file()]
            total_size = sum(p.stat().st_size for p in files)
            if total_size > MAX_CACHE_SIZE_BYTES:
                files.sort(key=lambda p: p.stat().st_mtime)
                for p in files:
                    if total_size <= MAX_CACHE_SIZE_BYTES * 0.7:
                        break
                    size = p.stat().st_size
                    p.unlink(missing_ok=True)
                    total_size -= size
                    logger.debug(f"Evicted stream cache file: {p.name}")
        except Exception as e:
            logger.warning(f"Error during cache eviction: {e}")

stream_cache_service = StreamCacheManager.get_instance()
