import os
import sys
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from app.services.stream_cache import (
    StreamCacheManager,
    DEFAULT_CACHE_DIR,
    MAX_CACHE_SIZE_BYTES,
    MIN_FREE_DISK_BYTES,
    BLOCK_SIZE,
)


def test_default_cache_configuration():
    assert DEFAULT_CACHE_DIR == Path("/var/tmp/tg_cache")
    assert MAX_CACHE_SIZE_BYTES == 10 * 1024 * 1024 * 1024
    assert MIN_FREE_DISK_BYTES in (2 * 1024 * 1024 * 1024, 3 * 1024 * 1024 * 1024)
    assert BLOCK_SIZE == 10 * 1024 * 1024


def test_startup_clean_removes_lingering_tmp_files(tmp_path, monkeypatch):
    cache_dir = tmp_path / "stream_cache"
    blocks_dir = cache_dir / "blocks"
    cache_dir.mkdir(parents=True, exist_ok=True)
    blocks_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.services.stream_cache.CACHE_DIR", cache_dir)
    monkeypatch.setattr("app.services.stream_cache.BLOCKS_DIR", blocks_dir)

    stale_tmp1 = cache_dir / "interrupted.tmp"
    stale_tmp1.write_bytes(b"temp1")
    stale_tmp2 = blocks_dir / "part.tmp.12345"
    stale_tmp2.write_bytes(b"temp2")
    stale_merging = cache_dir / "movie.merging.abcdef"
    stale_merging.write_bytes(b"merging")
    stale_part = cache_dir / "stream.part"
    stale_part.write_bytes(b"orphan_part")

    valid_block = blocks_dir / "chat1_100_part00000.bin"
    valid_block.write_bytes(b"valid_block_data")
    valid_slice = blocks_dir / "chat1_100_slice_0_16.bin"
    valid_slice.write_bytes(b"valid_slice_data")

    service = StreamCacheManager()
    purged_count = service.startup_clean()

    assert purged_count == 4
    assert not stale_tmp1.exists()
    assert not stale_tmp2.exists()
    assert not stale_merging.exists()
    assert not stale_part.exists()

    assert valid_block.exists()
    assert valid_block.read_bytes() == b"valid_block_data"
    assert valid_slice.exists()
    assert valid_slice.read_bytes() == b"valid_slice_data"


def test_put_slice_and_get_slice(tmp_path, monkeypatch):
    cache_dir = tmp_path / "stream_cache"
    blocks_dir = cache_dir / "blocks"
    cache_dir.mkdir(parents=True, exist_ok=True)
    blocks_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.services.stream_cache.CACHE_DIR", cache_dir)
    monkeypatch.setattr("app.services.stream_cache.BLOCKS_DIR", blocks_dir)

    service = StreamCacheManager()
    chat_id = "test_chat"
    message_id = 42

    miss = service.get_slice(chat_id, message_id, 0, 100)
    assert miss is None

    payload = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    saved = service.put_slice(chat_id, message_id, 0, payload)
    assert saved is True

    exact = service.get_slice(chat_id, message_id, 0, len(payload))
    assert exact == payload

    sub = service.get_slice(chat_id, message_id, 10, 5)
    assert sub == b"KLMNO"

    miss_beyond = service.get_slice(chat_id, message_id, 30, 20)
    assert miss_beyond is None

    empty = service.get_slice(chat_id, message_id, 0, 0)
    assert empty == b""


def test_get_slice_from_block_and_full_file(tmp_path, monkeypatch):
    cache_dir = tmp_path / "stream_cache"
    blocks_dir = cache_dir / "blocks"
    cache_dir.mkdir(parents=True, exist_ok=True)
    blocks_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.services.stream_cache.CACHE_DIR", cache_dir)
    monkeypatch.setattr("app.services.stream_cache.BLOCKS_DIR", blocks_dir)

    service = StreamCacheManager()
    chat_id = "chat_block"
    message_id = 99

    block_file = service.get_block_path(chat_id, message_id, 0)
    block_file.parent.mkdir(parents=True, exist_ok=True)
    block_data = b"B" * 5000
    block_file.write_bytes(block_data)

    read_block = service.get_slice(chat_id, message_id, 100, 200)
    assert read_block == b"B" * 200

    chat_full = "chat_full"
    full_msg = 88
    full_file = service.get_cache_path(chat_full, full_msg, "video.mp4")
    full_data = b"F" * 8000
    full_file.write_bytes(full_data)
    service._scan_existing_cache()

    read_full = service.get_slice(chat_full, full_msg, 2000, 500)
    assert read_full == b"F" * 500


def test_check_disk_guard_triggers_emergency_lru_eviction(tmp_path, monkeypatch):
    cache_dir = tmp_path / "stream_cache"
    blocks_dir = cache_dir / "blocks"
    cache_dir.mkdir(parents=True, exist_ok=True)
    blocks_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.services.stream_cache.CACHE_DIR", cache_dir)
    monkeypatch.setattr("app.services.stream_cache.BLOCKS_DIR", blocks_dir)

    service = StreamCacheManager()

    f1 = blocks_dir / "file1.bin"
    f1.write_bytes(b"1" * 1024)
    os.utime(f1, (1000, 1000))

    f2 = blocks_dir / "file2.bin"
    f2.write_bytes(b"2" * 1024)
    os.utime(f2, (2000, 2000))

    f3 = blocks_dir / "file3.bin"
    f3.write_bytes(b"3" * 1024)
    os.utime(f3, (3000, 3000))

    Usage = namedtuple("Usage", ["total", "used", "free"])

    def mock_disk_low(path):
        return Usage(total=100 * 1024 * 1024 * 1024, used=99 * 1024 * 1024 * 1024, free=1 * 1024 * 1024 * 1024)

    monkeypatch.setattr(shutil, "disk_usage", mock_disk_low)

    triggered = service.check_disk_guard()
    assert triggered is True
    assert not f1.exists()


def test_total_cache_size_cap_lru_eviction(tmp_path, monkeypatch):
    cache_dir = tmp_path / "stream_cache"
    blocks_dir = cache_dir / "blocks"
    cache_dir.mkdir(parents=True, exist_ok=True)
    blocks_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.services.stream_cache.CACHE_DIR", cache_dir)
    monkeypatch.setattr("app.services.stream_cache.BLOCKS_DIR", blocks_dir)
    monkeypatch.setattr("app.services.stream_cache.MAX_CACHE_SIZE_BYTES", 3000)

    service = StreamCacheManager()

    Usage = namedtuple("Usage", ["total", "used", "free"])

    def mock_disk_high(path):
        return Usage(total=100 * 1024 * 1024 * 1024, used=10 * 1024 * 1024 * 1024, free=90 * 1024 * 1024 * 1024)

    monkeypatch.setattr(shutil, "disk_usage", mock_disk_high)

    f1 = blocks_dir / "part_old.bin"
    f1.write_bytes(b"A" * 1500)
    os.utime(f1, (1000, 1000))

    f2 = blocks_dir / "part_mid.bin"
    f2.write_bytes(b"B" * 1500)
    os.utime(f2, (2000, 2000))

    f3 = blocks_dir / "part_new.bin"
    f3.write_bytes(b"C" * 1500)
    os.utime(f3, (3000, 3000))

    triggered = service.check_disk_guard()
    assert triggered is True
    assert not f1.exists()
    stats = service.get_cache_stats()
    assert stats["total_bytes"] <= int(3000 * 0.7)


def test_concurrent_slice_writes_without_file_not_found(tmp_path, monkeypatch):
    cache_dir = tmp_path / "stream_cache"
    blocks_dir = cache_dir / "blocks"
    cache_dir.mkdir(parents=True, exist_ok=True)
    blocks_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.services.stream_cache.CACHE_DIR", cache_dir)
    monkeypatch.setattr("app.services.stream_cache.BLOCKS_DIR", blocks_dir)

    service = StreamCacheManager()
    errors = []

    def writer_task(worker_id):
        for i in range(20):
            offset = i * 2048
            data = bytes([(worker_id * 7 + i) % 256]) * 2048
            ok = service.put_slice(f"chat_concurrent_{worker_id}", 777, offset, data)
            if not ok:
                errors.append(f"Worker {worker_id} failed slice {offset}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(writer_task, w) for w in range(8)]
        for f in futures:
            f.result()

    assert not errors, f"Concurrent slice errors: {errors}"
    tmps = list(blocks_dir.glob("*.tmp*")) + list(cache_dir.glob("*.tmp*"))
    assert len(tmps) == 0


def test_check_disk_guard_ignores_transients_and_handles_missing_file(tmp_path, monkeypatch):
    cache_dir = tmp_path / "stream_cache"
    blocks_dir = cache_dir / "blocks"
    cache_dir.mkdir(parents=True, exist_ok=True)
    blocks_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.services.stream_cache.CACHE_DIR", cache_dir)
    monkeypatch.setattr("app.services.stream_cache.BLOCKS_DIR", blocks_dir)

    service = StreamCacheManager()

    transient1 = blocks_dir / "slice.tmp.12345"
    transient1.write_bytes(b"T" * 4096)
    transient2 = cache_dir / "file.merging.67890"
    transient2.write_bytes(b"M" * 4096)

    valid_file = blocks_dir / "valid_slice.bin"
    valid_file.write_bytes(b"V" * 1024)

    Usage = namedtuple("Usage", ["total", "used", "free"])
    monkeypatch.setattr(shutil, "disk_usage", lambda p: Usage(100 * 1024 * 1024 * 1024, 10 * 1024 * 1024 * 1024, 90 * 1024 * 1024 * 1024))

    triggered = service.check_disk_guard()
    assert triggered is False
    assert transient1.exists()
    assert transient2.exists()
    assert valid_file.exists()


def test_put_slice_debounces_check_disk_guard(tmp_path, monkeypatch):
    cache_dir = tmp_path / "stream_cache"
    blocks_dir = cache_dir / "blocks"
    cache_dir.mkdir(parents=True, exist_ok=True)
    blocks_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.services.stream_cache.CACHE_DIR", cache_dir)
    monkeypatch.setattr("app.services.stream_cache.BLOCKS_DIR", blocks_dir)

    service = StreamCacheManager()
    call_count = [0]
    orig_guard = service.check_disk_guard

    def counting_guard():
        call_count[0] += 1
        return orig_guard()

    monkeypatch.setattr(service, "check_disk_guard", counting_guard)

    for i in range(15):
        ok = service.put_slice("debounce_chat", 1, i * 1024, b"D" * 1024)
        assert ok is True

    assert call_count[0] <= 1
