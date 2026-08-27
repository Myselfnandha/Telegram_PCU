import asyncio
import logging
import shutil
from pathlib import Path
from typing import List, Callable, Optional
import aiofiles
from app.config import SPLIT_THRESHOLD, CHUNK_READ_SIZE

logger = logging.getLogger("splitter")

class InsufficientDiskSpaceError(Exception):
    pass

class SplitError(Exception):
    pass

SPLIT_BUFFER_SIZE = 16 * 1024 * 1024  # 16 MB fast I/O block buffer

async def split_large_file(
    file_path: Path,
    part_size: int = SPLIT_THRESHOLD,
    progress_callback: Optional[Callable[[int, int], None]] = None
) -> List[Path]:
    """
    High-performance file splitter with 16MB binary block buffers.
    Runs asynchronously in thread pool for maximum disk I/O throughput.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"File to split does not exist: {file_path}")

    total_size = file_path.stat().st_size
    if total_size <= part_size:
        return [file_path]

    # Pre-check disk space
    stat = shutil.disk_usage(file_path.parent)
    required_space = total_size + (200 * 1024 * 1024)
    if stat.free < required_space:
        raise InsufficientDiskSpaceError(
            f"Not enough free disk space. Required: {required_space // (1024*1024)}MB, Available: {stat.free // (1024*1024)}MB"
        )

    def _sync_split() -> List[Path]:
        parts: List[Path] = []
        part_num = 1
        total_bytes_processed = 0

        try:
            with open(file_path, "rb") as source_f:
                while total_bytes_processed < total_size:
                    part_path = file_path.parent / f"{file_path.name}.part{part_num:03d}"
                    bytes_written_to_part = 0

                    with open(part_path, "wb") as part_f:
                        while bytes_written_to_part < part_size:
                            bytes_to_read = min(SPLIT_BUFFER_SIZE, part_size - bytes_written_to_part)
                            chunk = source_f.read(bytes_to_read)
                            if not chunk:
                                break

                            part_f.write(chunk)
                            chunk_len = len(chunk)
                            bytes_written_to_part += chunk_len
                            total_bytes_processed += chunk_len

                            if progress_callback:
                                try:
                                    progress_callback(total_bytes_processed, total_size)
                                except Exception:
                                    pass

                    if bytes_written_to_part == 0:
                        if part_path.exists():
                            part_path.unlink(missing_ok=True)
                        break

                    parts.append(part_path)
                    logger.info(f"Created fast split part: {part_path.name} ({bytes_written_to_part} bytes)")
                    part_num += 1

            return parts
        except Exception as e:
            logger.error(f"Error during fast file split of {file_path.name}: {e}")
            for p in parts:
                if p.exists():
                    try:
                        p.unlink(missing_ok=True)
                    except Exception:
                        pass
            raise SplitError(f"Fast file splitting failed: {e}") from e

    return await asyncio.to_thread(_sync_split)


def cleanup_files(paths: List[Path]):
    """Safely cleans up temporary files."""
    for p in paths:
        if p and p.exists():
            try:
                p.unlink(missing_ok=True)
                logger.debug(f"Cleaned up temp file: {p.name}")
            except Exception as e:
                logger.warning(f"Failed to delete temp file {p}: {e}")
