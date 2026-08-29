import math
import os
import asyncio
import logging
from pathlib import Path
from typing import Optional, Callable
from telethon import TelegramClient
from telethon.tl import types, functions

logger = logging.getLogger("turbo_uploader")

# 512 KB: Maximum Telegram MTProto chunk size for SaveBigFilePartRequest
PART_SIZE = 512 * 1024
SMALL_PART_SIZE = 128 * 1024

# Active upload sessions cache for chunk-level pause/resume persistence
_active_sessions = {}


def get_optimal_chunk_config(size: int):
    """
    Returns optimal MTProto chunk size and worker pool size based on file size.
    Scales from 4 up to 12 concurrent workers for maximum fiber gigabit saturation.
    """
    if size <= 10 * 1024 * 1024:  # <= 10MB
        return SMALL_PART_SIZE, 4, False
    elif size <= 100 * 1024 * 1024:  # 10MB - 100MB
        return PART_SIZE, 8, True
    elif size <= 500 * 1024 * 1024:  # 100MB - 500MB
        return PART_SIZE, 10, True
    else:  # > 500MB (large files & 2GB/4GB parts)
        return PART_SIZE, 12, True


async def upload_file_turbo(
    client: TelegramClient,
    file_path: Path,
    filename: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    pause_event: Optional[asyncio.Event] = None,
    cancel_event: Optional[asyncio.Event] = None,
    byte_offset: int = 0,
    byte_length: Optional[int] = None,
    max_workers: Optional[int] = None
) -> types.TypeInputFile:
    """
    Turbo Multi-Connection Parallel Upload Engine for Telegram MTProto.
    Supports Zero-Copy byte range slicing directly from large files without intermediate disk splitting!
    Streams 512KB chunks concurrently over 6-8 parallel workers with chunk-level persistence.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    total_file_size = file_path.stat().st_size
    if byte_offset >= total_file_size:
        raise ValueError(f"Byte offset {byte_offset} is beyond file size {total_file_size}")

    if byte_length is not None:
        effective_size = min(byte_length, total_file_size - byte_offset)
    else:
        effective_size = total_file_size - byte_offset

    filename_to_use = filename or file_path.name
    part_size, default_workers, is_big = get_optimal_chunk_config(effective_size)
    num_workers = max_workers or default_workers

    total_parts = math.ceil(effective_size / part_size) if effective_size > 0 else 1

    session_key = f"{file_path.resolve()}:{byte_offset}:{effective_size}"
    if session_key in _active_sessions and _active_sessions[session_key]["total_parts"] == total_parts:
        session = _active_sessions[session_key]
        file_id = session["file_id"]
        completed_parts = session["completed_parts"]
        logger.info(
            f"Resuming zero-copy slice for {filename_to_use} [offset: {byte_offset}]: "
            f"{len(completed_parts)}/{total_parts} parts already uploaded"
        )
    else:
        file_id = int.from_bytes(os.urandom(8), "big", signed=True)
        completed_parts = set()
        session = {
            "file_id": file_id,
            "total_parts": total_parts,
            "completed_parts": completed_parts
        }
        _active_sessions[session_key] = session

    part_queue: asyncio.Queue = asyncio.Queue()
    for part_index in range(total_parts):
        if part_index not in completed_parts:
            await part_queue.put(part_index)

    uploaded_bytes_lock = asyncio.Lock()
    uploaded_bytes_total = len(completed_parts) * part_size

    # Open single file descriptor for atomic, lock-free POSIX os.pread()
    fd = os.open(str(file_path), os.O_RDONLY)

    try:
        async def _worker(worker_id: int):
            nonlocal uploaded_bytes_total
            while not part_queue.empty():
                if cancel_event and cancel_event.is_set():
                    break
                if pause_event:
                    await pause_event.wait()

                try:
                    part_index = part_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                # Atomic zero-seek read at exact slice offset using Linux page cache
                slice_offset = byte_offset + (part_index * part_size)
                bytes_remaining = effective_size - (part_index * part_size)
                read_amount = min(part_size, bytes_remaining)

                chunk = os.pread(fd, read_amount, slice_offset)

                if is_big:
                    req = functions.upload.SaveBigFilePartRequest(
                        file_id=file_id,
                        file_part=part_index,
                        file_total_parts=total_parts,
                        bytes=chunk
                    )
                else:
                    req = functions.upload.SaveFilePartRequest(
                        file_id=file_id,
                        file_part=part_index,
                        bytes=chunk
                    )

                # Retry chunk if temporary network blip occurs
                for attempt in range(6):
                    if cancel_event and cancel_event.is_set():
                        break
                    if pause_event:
                        await pause_event.wait()

                    try:
                        await client(req)
                        break
                    except Exception as err:
                        logger.warning(
                            f"[Turbo Worker {worker_id}] Part {part_index} network retry (attempt {attempt+1}/6): {err}"
                        )
                        if attempt >= 5:
                            raise
                        backoff = min(5.0, 0.4 * (1.8 ** attempt))
                        await asyncio.sleep(backoff)

                async with uploaded_bytes_lock:
                    completed_parts.add(part_index)
                    uploaded_bytes_total += len(chunk)
                    current_total = uploaded_bytes_total

                if progress_callback:
                    try:
                        progress_callback(current_total, effective_size)
                    except Exception:
                        pass

                part_queue.task_done()

        # Launch parallel workers
        worker_count = min(num_workers, max(1, total_parts))
        tasks = [asyncio.create_task(_worker(i)) for i in range(worker_count)]

        try:
            await asyncio.gather(*tasks)
        except Exception as e:
            for t in tasks:
                t.cancel()
            raise
    finally:
        os.close(fd)

    if cancel_event and cancel_event.is_set():
        _active_sessions.pop(session_key, None)
        raise asyncio.CancelledError("Upload cancelled by user.")

    # Completed successfully - clear session
    _active_sessions.pop(session_key, None)

    if is_big:
        return types.InputFileBig(
            id=file_id,
            parts=total_parts,
            name=filename_to_use
        )
    else:
        return types.InputFile(
            id=file_id,
            parts=total_parts,
            name=filename_to_use,
            md5_checksum=""
        )
