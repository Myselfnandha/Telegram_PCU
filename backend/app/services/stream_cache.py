import os
import shutil
import asyncio
import logging
import time
import uuid
import threading
from pathlib import Path
from typing import Optional, Dict, Tuple, List, BinaryIO, Union

logger = logging.getLogger("stream_cache")

DEFAULT_CACHE_DIR = Path("/var/tmp/tg_cache")
try:
    DEFAULT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR = Path(os.getenv("STREAM_CACHE_DIR", str(DEFAULT_CACHE_DIR)))
except Exception:
    CACHE_DIR = Path(os.getenv("STREAM_CACHE_DIR", str(Path(__file__).resolve().parent.parent.parent / "data" / "stream_cache")))

try:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

MAX_CACHE_SIZE_BYTES = int(os.getenv("STREAM_CACHE_MAX_GB", "10")) * 1024 * 1024 * 1024
MIN_FREE_DISK_BYTES = int(os.getenv("STREAM_CACHE_MIN_FREE_GB", "2")) * 1024 * 1024 * 1024

BLOCK_SIZE = 10 * 1024 * 1024
BLOCKS_DIR = CACHE_DIR / "blocks"
try:
    BLOCKS_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass


class StreamCacheManager:
    """
    High-Performance Local Stream Cache Manager.
    Caches Telegram MTProto media in discrete 10MB segment parts on local disk for instant <1ms seek/replay
    and powers the Continuous Background Prefetcher for non-watched chunks.
    """
    _instance: Optional["StreamCacheManager"] = None
    _lock = asyncio.Lock()
    _file_info: Dict[str, dict] = {}
    _merge_locks: Dict[str, threading.Lock] = {}
    _locks_lock = threading.Lock()
    _last_disk_guard_check: float = 0.0
    _disk_guard_lock = threading.Lock()

    def __init__(self):
        self._last_disk_guard_check: float = 0.0
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            BLOCKS_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self._scan_existing_cache()

    @classmethod
    def get_instance(cls) -> "StreamCacheManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def _canonical_cid(chat_id: Union[str, int]) -> str:
        s = str(chat_id)
        if s.startswith("-100") and len(s) > 4 and s[4:].isdigit() and not s[4:].startswith("0"):
            return s[4:]
        if s.startswith("-") and len(s) > 1 and s[1:].isdigit() and not s[1:].startswith("0"):
            return s[1:]
        return s

    @classmethod
    def _cid_candidates(cls, chat_id: Union[str, int]) -> List[str]:
        s = str(chat_id)
        canon = cls._canonical_cid(chat_id)
        cands = [canon]
        if s not in cands:
            cands.append(s)
        if s.isdigit() and not s.startswith("0"):
            pref = f"-100{s}"
            if pref not in cands:
                cands.append(pref)
        elif s.startswith("-100") and len(s) > 4 and s[4:].isdigit() and not s[4:].startswith("0"):
            raw = s[4:]
            if raw not in cands:
                cands.append(raw)
        elif s.startswith("-") and len(s) > 1 and s[1:].isdigit() and not s[1:].startswith("0"):
            raw = s[1:]
            if raw not in cands:
                cands.append(raw)
        return cands

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
        try:
            return p.exists() and p.is_file() and p.stat().st_size == expected_size
        except OSError:
            return False

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
        """Saves a discrete 10MB chunk part to disk atomically."""
        p = self.get_block_path(chat_id, message_id, block_idx)
        tmp = p.with_suffix(f".tmp.{uuid.uuid4().hex[:8]}")
        try:
            with open(tmp, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)
            self.evict_if_needed()
        except Exception as e:
            logger.debug(f"Could not save stream block {block_idx}: {e}")
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    def get_slice(self, chat_id: Union[str, int], message_id: int, offset: int, length: int) -> Optional[bytes]:
        if length <= 0:
            return b""
        candidate_cids = self._cid_candidates(chat_id)

        for cid in candidate_cids:
            full_path = self.get_cached_file(cid, message_id)
            if full_path and full_path.exists() and full_path.is_file():
                try:
                    file_size = full_path.stat().st_size
                    if offset + length <= file_size:
                        with open(full_path, "rb") as f:
                            f.seek(offset)
                            data = f.read(length)
                            if len(data) == length:
                                return data
                except Exception:
                    pass

            exact_slice = BLOCKS_DIR / f"{cid}_{message_id}_slice_{offset}_{length}.bin"
            if exact_slice.exists() and exact_slice.is_file():
                try:
                    with open(exact_slice, "rb") as f:
                        data = f.read()
                        if len(data) == length:
                            return data
                except Exception:
                    pass

            if BLOCKS_DIR.exists():
                try:
                    for p in BLOCKS_DIR.glob(f"{cid}_{message_id}_slice_*.bin"):
                        if p.is_file():
                            parts = p.stem.split("_slice_")
                            if len(parts) == 2:
                                s_off_str, s_len_str = parts[1].split("_")
                                s_off = int(s_off_str)
                                s_len = int(s_len_str)
                                if s_off <= offset and (offset + length) <= (s_off + s_len):
                                    with open(p, "rb") as f:
                                        f.seek(offset - s_off)
                                        data = f.read(length)
                                        if len(data) == length:
                                            return data
                except Exception:
                    pass

            start_block = offset // BLOCK_SIZE
            end_block = (offset + length - 1) // BLOCK_SIZE
            if start_block == end_block:
                off_in_block = offset % BLOCK_SIZE
                data = self.read_block_slice(cid, message_id, start_block, off_in_block, length)
                if data and len(data) == length:
                    return data
            else:
                assembled = bytearray()
                cur_offset = offset
                needed = length
                can_assemble = True
                for b_idx in range(start_block, end_block + 1):
                    b_start = b_idx * BLOCK_SIZE
                    off_in_b = cur_offset - b_start
                    to_read = min(needed, BLOCK_SIZE - off_in_b)
                    chunk = self.read_block_slice(cid, message_id, b_idx, off_in_b, to_read)
                    if not chunk or len(chunk) != to_read:
                        can_assemble = False
                        break
                    assembled.extend(chunk)
                    cur_offset += to_read
                    needed -= to_read
                if can_assemble and len(assembled) == length:
                    return bytes(assembled)

        return None

    def put_slice(self, chat_id: str, message_id: int, offset: int, data: bytes) -> bool:
        if not data:
            return False
        tmp_path = None
        try:
            BLOCKS_DIR.mkdir(parents=True, exist_ok=True)
            slice_path = BLOCKS_DIR / f"{chat_id}_{message_id}_slice_{offset}_{len(data)}.bin"
            tmp_path = slice_path.with_suffix(f".tmp.{uuid.uuid4().hex[:8]}")
            with open(tmp_path, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, slice_path)
            if len(data) == BLOCK_SIZE and offset % BLOCK_SIZE == 0:
                block_idx = offset // BLOCK_SIZE
                block_path = self.get_block_path(chat_id, message_id, block_idx)
                if not block_path.exists():
                    try:
                        os.link(slice_path, block_path)
                    except Exception:
                        try:
                            shutil.copyfile(slice_path, block_path)
                        except Exception:
                            pass
            now = time.time()
            if (now - self._last_disk_guard_check) >= 5.0:
                self.check_disk_guard()
            return True
        except Exception as e:
            logger.debug(f"Could not put slice {offset}: {e}")
            if tmp_path and tmp_path.exists():
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass
            return False

    def merge_blocks_if_complete(self, chat_id: str, message_id: int, total_file_size: int, filename: Optional[str] = None) -> Optional[Path]:
        """If all discrete blocks are present on disk, merges them into the final full movie file."""
        key = self._get_cache_key(chat_id, message_id)
        with self._locks_lock:
            if key not in self._merge_locks:
                self._merge_locks[key] = threading.Lock()
            lock = self._merge_locks[key]

        with lock:
            # Check if already completed by another thread/request
            cached = self.get_cached_file(chat_id, message_id, expected_size=total_file_size)
            if cached:
                return cached

            total_blocks = (total_file_size + BLOCK_SIZE - 1) // BLOCK_SIZE
            for i in range(total_blocks):
                expected = min(BLOCK_SIZE, total_file_size - (i * BLOCK_SIZE))
                if not self.has_block(chat_id, message_id, i, expected):
                    return None

            # All blocks present -> Assemble into full cached file
            target_path = self.get_cache_path(chat_id, message_id, filename)
            tmp_target = target_path.with_suffix(f".merging.{uuid.uuid4().hex[:8]}")
            try:
                with open(tmp_target, "wb") as out_f:
                    for i in range(total_blocks):
                        block_file = self.get_block_path(chat_id, message_id, i)
                        with open(block_file, "rb") as bf:
                            shutil.copyfileobj(bf, out_f, length=1024 * 1024)
                        out_f.flush()
                    os.fsync(out_f.fileno())

                if tmp_target.stat().st_size != total_file_size:
                    raise IOError(f"Merged size {tmp_target.stat().st_size} != expected {total_file_size}")

                os.replace(tmp_target, target_path)
                logger.info(f"Assembled complete movie from {total_blocks} blocks: {target_path.name}")

                # Clean block fragments only after successful assembly and verification
                for i in range(total_blocks):
                    self.get_block_path(chat_id, message_id, i).unlink(missing_ok=True)

                self._file_info[key] = {
                    "path": target_path,
                    "size": total_file_size,
                    "completed": True
                }

                # Automatically trigger offline stream saver if enabled
                try:
                    from app.services.stream_saver import stream_saver_service
                    if stream_saver_service.is_enabled and filename:
                        stream_saver_service.save_completed_stream(str(chat_id), message_id, total_file_size, filename)
                except Exception as se:
                    logger.debug(f"Offline stream saver notice: {se}")

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
                if p.is_file() and p.suffix not in (".part", ".tmp", ".merging") and ".tmp." not in p.name and not p.name.endswith(".merging"):
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

    def is_cached(self, chat_id: Union[str, int], message_id: int, expected_size: Optional[int] = None) -> bool:
        """Returns True if the complete media file is cached locally."""
        for cid in self._cid_candidates(chat_id):
            key = self._get_cache_key(cid, message_id)
            info = self._file_info.get(key)
            if info and info.get("path") and info["path"].exists() and info["path"].is_file():
                if info["path"].suffix not in (".part", ".tmp", ".merging") and ".tmp." not in info["path"].name and not info["path"].name.endswith(".merging"):
                    if expected_size is None or expected_size <= 0 or info["path"].stat().st_size == expected_size:
                        return True
            for p in CACHE_DIR.glob(f"{cid}_{message_id}.*"):
                if p.exists() and p.is_file() and p.suffix not in (".part", ".tmp", ".merging") and ".tmp." not in p.name and not p.name.endswith(".merging"):
                    if expected_size is not None and expected_size > 0:
                        if p.stat().st_size != expected_size:
                            continue
                    if p.stat().st_size > 0:
                        self._file_info[key] = {"path": p, "size": p.stat().st_size, "completed": True}
                        return True
        return False

    def get_cached_file(self, chat_id: Union[str, int], message_id: int, expected_size: Optional[int] = None) -> Optional[Path]:
        """Returns the local path only if fully cached."""
        for cid in self._cid_candidates(chat_id):
            key = self._get_cache_key(cid, message_id)
            info = self._file_info.get(key)
            if info and info.get("path") and info["path"].exists() and info["path"].is_file():
                if info["path"].suffix not in (".part", ".tmp", ".merging") and ".tmp." not in info["path"].name and not info["path"].name.endswith(".merging"):
                    if expected_size is None or expected_size <= 0 or info["path"].stat().st_size == expected_size:
                        return info["path"]
            for p in CACHE_DIR.glob(f"{cid}_{message_id}.*"):
                if p.exists() and p.is_file() and p.suffix not in (".part", ".tmp", ".merging") and ".tmp." not in p.name and not p.name.endswith(".merging"):
                    if expected_size is not None and expected_size > 0:
                        if p.stat().st_size != expected_size:
                            continue
                    if p.stat().st_size > 0:
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

    def startup_purge(self, wipe_all: bool = True) -> int:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            BLOCKS_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        purged_count = 0
        try:
            candidate_files = []
            seen = set()
            for d in [CACHE_DIR, BLOCKS_DIR]:
                if d.exists():
                    for p in d.iterdir():
                        if p.is_file() and p not in seen:
                            seen.add(p)
                            candidate_files.append(p)
            for p in candidate_files:
                if wipe_all or (
                    ".tmp" in p.name
                    or ".merging" in p.name
                    or p.name.endswith(".part")
                    or p.suffix == ".part"
                ):
                    p.unlink(missing_ok=True)
                    purged_count += 1
            if wipe_all:
                self._file_info.clear()
        except Exception as e:
            logger.warning(f"Error during startup_purge: {e}")

        if not wipe_all:
            self._scan_existing_cache()
        self.check_disk_guard()
        return purged_count

    def startup_clean(self, wipe_all: bool = False) -> int:
        return self.startup_purge(wipe_all=wipe_all)

    def check_disk_guard(self) -> bool:
        with self._disk_guard_lock:
            self._last_disk_guard_check = time.time()
            check_path = CACHE_DIR if CACHE_DIR.exists() else CACHE_DIR.parent
            try:
                free_bytes = shutil.disk_usage(str(check_path)).free
            except Exception:
                free_bytes = MIN_FREE_DISK_BYTES + 1

            files = []
            seen = set()
            for d in [CACHE_DIR, BLOCKS_DIR]:
                if d.exists():
                    try:
                        for p in d.iterdir():
                            if p in seen:
                                continue
                            seen.add(p)
                            name = p.name
                            if ".tmp" in name or ".merging" in name or name.endswith(".part") or p.suffix == ".part":
                                continue
                            try:
                                if not p.is_file():
                                    continue
                                st = p.stat()
                                files.append((p, st.st_size, st.st_mtime))
                            except OSError:
                                continue
                    except OSError:
                        continue

            total_cache = sum(item[1] for item in files)

            if free_bytes < MIN_FREE_DISK_BYTES or total_cache > MAX_CACHE_SIZE_BYTES:
                files.sort(key=lambda item: item[2])
                for p, size, _ in files:
                    if free_bytes >= MIN_FREE_DISK_BYTES and total_cache <= int(MAX_CACHE_SIZE_BYTES * 0.7):
                        break
                    try:
                        p.unlink(missing_ok=True)
                        total_cache -= size
                        free_bytes += size
                        stale_keys = [k for k, v in list(self._file_info.items()) if v.get("path") == p]
                        for k in stale_keys:
                            self._file_info.pop(k, None)
                        logger.debug(f"Evicted stream cache file: {p.name}")
                    except Exception:
                        pass
                return True

            return False

    def evict_if_needed(self):
        now = time.time()
        try:
            if BLOCKS_DIR.exists():
                for p in BLOCKS_DIR.glob("*.bin"):
                    try:
                        if p.is_file() and (now - p.stat().st_mtime) > 86400:
                            p.unlink(missing_ok=True)
                            logger.debug(f"Purged expired stream chunk block: {p.name}")
                    except OSError:
                        pass

            for d in [CACHE_DIR, BLOCKS_DIR]:
                if d.exists():
                    for p in list(d.glob("*.tmp*")) + list(d.glob("*.merging*")):
                        try:
                            if p.is_file() and (now - p.stat().st_mtime) > 3600:
                                p.unlink(missing_ok=True)
                        except OSError:
                            pass
        except Exception as e:
            logger.debug(f"Error purging expired blocks: {e}")

        self.check_disk_guard()

stream_cache_service = StreamCacheManager.get_instance()
