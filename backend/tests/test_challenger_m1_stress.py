import os
import sys
import shutil
import hashlib
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from app.services.stream_cache import (
    StreamCacheManager,
    BLOCK_SIZE,
)
from app.services.stream_buffer import StreamSlidingBuffer

Usage = namedtuple("Usage", ["total", "used", "free"])


def test_stress_boot_cleanup_matrix(tmp_path, monkeypatch):
    cache_dir = tmp_path / "stream_cache"
    blocks_dir = cache_dir / "blocks"
    cache_dir.mkdir(parents=True, exist_ok=True)
    blocks_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.services.stream_cache.CACHE_DIR", cache_dir)
    monkeypatch.setattr("app.services.stream_cache.BLOCKS_DIR", blocks_dir)

    stale_files = []
    valid_files = []

    for i in range(5):
        p = cache_dir / f"session_{i}.tmp"
        p.write_bytes(f"tmp_{i}".encode())
        stale_files.append(p)

    for i in range(5):
        p = blocks_dir / f"block_{i}.tmp.abcdef{i}"
        p.write_bytes(f"block_tmp_{i}".encode())
        stale_files.append(p)

    for i in range(5):
        p = cache_dir / f"movie_{i}.merging.12345{i}"
        p.write_bytes(f"merging_{i}".encode())
        stale_files.append(p)

    for i in range(5):
        p = cache_dir / f"upload_{i}.part"
        p.write_bytes(f"part_{i}".encode())
        stale_files.append(p)

    for i in range(5):
        p = blocks_dir / f"stream_{i}.part"
        p.write_bytes(f"block_part_{i}".encode())
        stale_files.append(p)

    for i in range(5):
        p = blocks_dir / f"chat1_{i}_part00000.bin"
        p.write_bytes(f"valid_block_{i}".encode())
        valid_files.append(p)

    for i in range(5):
        p = blocks_dir / f"chat1_{i}_slice_0_1024.bin"
        p.write_bytes(f"valid_slice_{i}".encode())
        valid_files.append(p)

    for i in range(5):
        p = cache_dir / f"chat1_{i}.mp4"
        p.write_bytes(f"valid_movie_{i}".encode())
        valid_files.append(p)

    for i in range(5):
        p = cache_dir / f"template_not_tmp_{i}.bin"
        p.write_bytes(f"not_tmp_{i}".encode())
        valid_files.append(p)

    assert len(stale_files) == 25
    assert len(valid_files) == 20

    service = StreamCacheManager()
    purged = service.startup_clean()

    assert purged == 25

    for sf in stale_files:
        assert not sf.exists(), f"Stale file {sf.name} was not deleted"

    for vf in valid_files:
        assert vf.exists(), f"Valid file {vf.name} was erroneously deleted"
        expected_content = (
            f"valid_block_{vf.stem.split('_')[1]}".encode()
            if "_part" in vf.name
            else f"valid_slice_{vf.stem.split('_')[1]}".encode()
            if "_slice" in vf.name
            else f"valid_movie_{vf.stem.split('_')[1]}".encode()
            if vf.suffix == ".mp4"
            else f"not_tmp_{vf.stem.split('_')[-1]}".encode()
        )
        assert vf.read_bytes() == expected_content


def test_stress_disk_guard_low_disk_eviction_to_70_percent(tmp_path, monkeypatch):
    cache_dir = tmp_path / "stream_cache"
    blocks_dir = cache_dir / "blocks"
    cache_dir.mkdir(parents=True, exist_ok=True)
    blocks_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.services.stream_cache.CACHE_DIR", cache_dir)
    monkeypatch.setattr("app.services.stream_cache.BLOCKS_DIR", blocks_dir)

    max_cache = 10000
    min_free = 3000
    target_70_pct = int(max_cache * 0.7)
    monkeypatch.setattr("app.services.stream_cache.MAX_CACHE_SIZE_BYTES", max_cache)
    monkeypatch.setattr("app.services.stream_cache.MIN_FREE_DISK_BYTES", min_free)

    files = []
    for i in range(10):
        f = blocks_dir / f"block_{i:02d}.bin"
        f.write_bytes(b"X" * 1000)
        os.utime(f, (1000 + i * 100, 1000 + i * 100))
        files.append(f)

    def mock_low_disk(path):
        return Usage(total=100000, used=98500, free=1500)

    monkeypatch.setattr(shutil, "disk_usage", mock_low_disk)

    service = StreamCacheManager()
    triggered = service.check_disk_guard()

    assert triggered is True

    assert not files[0].exists()
    assert not files[1].exists()
    assert not files[2].exists()

    assert files[3].exists()
    assert files[4].exists()
    assert files[5].exists()
    assert files[6].exists()
    assert files[7].exists()
    assert files[8].exists()
    assert files[9].exists()

    stats = service.get_cache_stats()
    assert stats["total_bytes"] <= target_70_pct
    assert stats["total_bytes"] == 7000


def test_stress_disk_guard_extreme_low_disk_full_eviction(tmp_path, monkeypatch):
    cache_dir = tmp_path / "stream_cache"
    blocks_dir = cache_dir / "blocks"
    cache_dir.mkdir(parents=True, exist_ok=True)
    blocks_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.services.stream_cache.CACHE_DIR", cache_dir)
    monkeypatch.setattr("app.services.stream_cache.BLOCKS_DIR", blocks_dir)
    monkeypatch.setattr("app.services.stream_cache.MIN_FREE_DISK_BYTES", 3000)

    files = []
    for i in range(4):
        f = blocks_dir / f"piece_{i}.bin"
        f.write_bytes(b"Z" * 500)
        os.utime(f, (1000 + i, 1000 + i))
        files.append(f)

    def mock_zero_disk(path):
        return Usage(total=100000, used=100000, free=500)

    monkeypatch.setattr(shutil, "disk_usage", mock_zero_disk)

    service = StreamCacheManager()
    triggered = service.check_disk_guard()

    assert triggered is True
    for f in files:
        assert not f.exists()

    stats = service.get_cache_stats()
    assert stats["total_bytes"] == 0


def test_stress_concurrent_slice_writes_and_reads(tmp_path, monkeypatch):
    cache_dir = tmp_path / "stream_cache"
    blocks_dir = cache_dir / "blocks"
    cache_dir.mkdir(parents=True, exist_ok=True)
    blocks_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.services.stream_cache.CACHE_DIR", cache_dir)
    monkeypatch.setattr("app.services.stream_cache.BLOCKS_DIR", blocks_dir)

    service = StreamCacheManager()
    num_writers = 15
    num_readers = 15
    records_per_thread = 10

    test_payloads = {}
    for w in range(num_writers):
        for r in range(records_per_thread):
            key = (f"chat_stress_{w}", 1000 + r, r * 1024)
            seed = f"worker_{w}_rec_{r}".encode() * 64
            test_payloads[key] = (seed, hashlib.sha256(seed).hexdigest())

    errors = []

    def writer_worker(w_id):
        try:
            for r in range(records_per_thread):
                key = (f"chat_stress_{w_id}", 1000 + r, r * 1024)
                data, _ = test_payloads[key]
                ok = service.put_slice(key[0], key[1], key[2], data)
                if not ok:
                    errors.append(f"put_slice failed for {key}")
        except Exception as e:
            errors.append(f"Writer exception: {e}")

    def reader_worker(r_id):
        try:
            for _ in range(records_per_thread * 2):
                w_target = r_id % num_writers
                r_target = (r_id * 3) % records_per_thread
                key = (f"chat_stress_{w_target}", 1000 + r_target, r_target * 1024)
                data, expected_hash = test_payloads[key]
                result = service.get_slice(key[0], key[1], key[2], len(data))
                if result is not None:
                    actual_hash = hashlib.sha256(result).hexdigest()
                    if actual_hash != expected_hash:
                        errors.append(
                            f"Corrupted read for {key}: got {actual_hash} expected {expected_hash}"
                        )
        except Exception as e:
            errors.append(f"Reader exception: {e}")

    with ThreadPoolExecutor(max_workers=num_writers + num_readers) as executor:
        futures = []
        for w in range(num_writers):
            futures.append(executor.submit(writer_worker, w))
        for r in range(num_readers):
            futures.append(executor.submit(reader_worker, r))
        for f in as_completed(futures):
            f.result()

    assert not errors, f"Encountered concurrency errors: {errors[:5]}"

    for key, (data, expected_hash) in test_payloads.items():
        retrieved = service.get_slice(key[0], key[1], key[2], len(data))
        assert retrieved is not None, f"Missing slice after write: {key}"
        assert hashlib.sha256(retrieved).hexdigest() == expected_hash

    leftover_tmps = list(blocks_dir.glob("*.tmp*")) + list(cache_dir.glob("*.tmp*"))
    assert len(leftover_tmps) == 0, f"Leaked tmp files: {leftover_tmps}"


def test_stress_concurrent_same_slice_race(tmp_path, monkeypatch):
    cache_dir = tmp_path / "stream_cache"
    blocks_dir = cache_dir / "blocks"
    cache_dir.mkdir(parents=True, exist_ok=True)
    blocks_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.services.stream_cache.CACHE_DIR", cache_dir)
    monkeypatch.setattr("app.services.stream_cache.BLOCKS_DIR", blocks_dir)

    service = StreamCacheManager()
    chat_id = "chat_race"
    msg_id = 999
    offset = 0
    slice_len = 4096

    patterns = [bytes([i % 256]) * slice_len for i in range(20)]
    valid_hashes = {hashlib.sha256(p).hexdigest() for p in patterns}

    errors = []

    def racer_writer(p_idx):
        try:
            payload = patterns[p_idx]
            ok = service.put_slice(chat_id, msg_id, offset, payload)
            if not ok:
                errors.append(f"Writer {p_idx} failed put_slice")
        except Exception as e:
            errors.append(f"Racer writer exception: {e}")

    def racer_reader():
        try:
            for _ in range(30):
                res = service.get_slice(chat_id, msg_id, offset, slice_len)
                if res is not None:
                    h = hashlib.sha256(res).hexdigest()
                    if h not in valid_hashes:
                        errors.append(f"Race corrupted read: unknown hash {h}")
        except Exception as e:
            errors.append(f"Racer reader exception: {e}")

    with ThreadPoolExecutor(max_workers=30) as executor:
        futures = []
        for i in range(20):
            futures.append(executor.submit(racer_writer, i))
        for _ in range(10):
            futures.append(executor.submit(racer_reader))
        for f in as_completed(futures):
            f.result()

    assert not errors, f"Errors in same slice race: {errors}"

    final_read = service.get_slice(chat_id, msg_id, offset, slice_len)
    assert final_read is not None
    assert hashlib.sha256(final_read).hexdigest() in valid_hashes


def test_stress_buffer_concurrent_push_and_eviction_pressure():
    buffer = StreamSlidingBuffer()
    num_threads = 10
    chunks_per_thread = 8
    chunk_bytes = 4 * 1024 * 1024

    errors = []

    def pusher_thread(t_id):
        try:
            for c in range(chunks_per_thread):
                chat = f"stream_thr_{t_id}"
                msg = 500 + c
                data = bytes([(t_id * 17 + c) % 256]) * chunk_bytes
                buffer.push(chat, msg, c * 1024 * 1024, data)
        except Exception as e:
            errors.append(f"Pusher thread exception: {e}")

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(pusher_thread, t) for t in range(num_threads)]
        for f in as_completed(futures):
            f.result()

    assert not errors, f"Buffer concurrency errors: {errors}"
    total_ram = buffer._get_total_ram_bytes()
    assert total_ram <= buffer.TOTAL_GLOBAL_RAM_BUDGET
    assert buffer.TOTAL_GLOBAL_RAM_BUDGET == 256 * 1024 * 1024


def test_edge_cases_slice_api(tmp_path, monkeypatch):
    cache_dir = tmp_path / "stream_cache"
    blocks_dir = cache_dir / "blocks"
    cache_dir.mkdir(parents=True, exist_ok=True)
    blocks_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.services.stream_cache.CACHE_DIR", cache_dir)
    monkeypatch.setattr("app.services.stream_cache.BLOCKS_DIR", blocks_dir)

    service = StreamCacheManager()

    assert service.put_slice("edge", 1, 0, b"") is False
    assert service.get_slice("edge", 1, 0, 0) == b""
    assert service.get_slice("edge", 1, 0, -10) == b""

    block0 = blocks_dir / "chat_boundary_1_part00000.bin"
    block0.write_bytes(b"A" * BLOCK_SIZE)

    miss_cross_block = service.get_slice(
        "chat_boundary", 1, BLOCK_SIZE - 1000, 2000
    )
    assert miss_cross_block is None

    block1 = blocks_dir / "chat_boundary_1_part00001.bin"
    block1.write_bytes(b"B" * BLOCK_SIZE)

    cross_block = service.get_slice(
        "chat_boundary", 1, BLOCK_SIZE - 1000, 2000
    )
    assert cross_block is not None
    assert len(cross_block) == 2000
    assert cross_block[:1000] == b"A" * 1000
    assert cross_block[1000:] == b"B" * 1000


def test_stress_concurrent_slice_writes_during_active_disk_sweeps(tmp_path, monkeypatch):
    import time
    cache_dir = tmp_path / "stream_cache"
    blocks_dir = cache_dir / "blocks"
    cache_dir.mkdir(parents=True, exist_ok=True)
    blocks_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.services.stream_cache.CACHE_DIR", cache_dir)
    monkeypatch.setattr("app.services.stream_cache.BLOCKS_DIR", blocks_dir)
    monkeypatch.setattr("app.services.stream_cache.MIN_FREE_DISK_BYTES", 3 * 1024 * 1024 * 1024)

    service = StreamCacheManager()
    num_writers = 12
    slices_per_writer = 25
    slice_size = 8192

    def mock_low_disk(path):
        return Usage(total=100 * 1024 * 1024 * 1024, used=98 * 1024 * 1024 * 1024, free=1 * 1024 * 1024 * 1024)

    monkeypatch.setattr(shutil, "disk_usage", mock_low_disk)

    stop_event = threading.Event()
    errors = []
    writes_succeeded = [0]
    writes_lock = threading.Lock()

    def writer_task(wid):
        try:
            for s in range(slices_per_writer):
                chat = f"sweep_chat_{wid}"
                msg = 2000 + s
                offset = s * slice_size
                payload = bytes([(wid * 11 + s) % 256]) * slice_size
                ok = service.put_slice(chat, msg, offset, payload)
                if not ok:
                    errors.append(f"Writer {wid} failed put_slice at offset {offset}")
                else:
                    with writes_lock:
                        writes_succeeded[0] += 1
                time.sleep(0.001)
        except Exception as e:
            errors.append(f"Writer {wid} exception: {e}")

    def sweeper_task(sid):
        try:
            while not stop_event.is_set():
                service.check_disk_guard()
                time.sleep(0.002)
        except Exception as e:
            errors.append(f"Sweeper {sid} exception: {e}")

    def reader_task(rid):
        try:
            while not stop_event.is_set():
                target_w = rid % num_writers
                target_s = (rid * 7) % slices_per_writer
                chat = f"sweep_chat_{target_w}"
                msg = 2000 + target_s
                offset = target_s * slice_size
                res = service.get_slice(chat, msg, offset, slice_size)
                if res is not None:
                    expected = bytes([(target_w * 11 + target_s) % 256]) * slice_size
                    if res != expected:
                        errors.append(f"Reader corrupted slice at {chat}:{msg}")
                time.sleep(0.002)
        except Exception as e:
            errors.append(f"Reader {rid} exception: {e}")

    with ThreadPoolExecutor(max_workers=24) as pool:
        sweeper_futures = [pool.submit(sweeper_task, s) for s in range(4)]
        reader_futures = [pool.submit(reader_task, r) for r in range(4)]
        writer_futures = [pool.submit(writer_task, w) for w in range(num_writers)]

        for wf in writer_futures:
            wf.result()

        stop_event.set()
        for sf in sweeper_futures:
            sf.result()
        for rf in reader_futures:
            rf.result()

    assert not errors, f"Errors during concurrent writes and sweeps: {errors[:5]}"
    assert writes_succeeded[0] == num_writers * slices_per_writer

    leftovers = list(blocks_dir.glob("*.tmp*")) + list(cache_dir.glob("*.tmp*"))
    assert len(leftovers) == 0, f"Leaked tmp files: {leftovers}"


def test_stress_concurrent_multi_stream_pushes_and_reads_hammer():
    import time
    buffer = StreamSlidingBuffer()
    num_pushers = 12
    num_tail_pushers = 4
    num_readers = 6
    num_stats = 2
    chunks_per_pusher = 20
    chunk_size = 512 * 1024

    stop_event = threading.Event()
    errors = []

    def chunk_pusher(pid):
        try:
            for c in range(chunks_per_pusher):
                chat = f"-1009999000{pid % 8}"
                msg = 100 + (c % 10)
                offset = (c // 10) * chunk_size
                data = bytes([(pid * 13 + c) % 256]) * chunk_size
                buffer.push(chat, msg, offset, data)
                time.sleep(0.001)
        except Exception as e:
            errors.append(f"Chunk pusher {pid} error: {e}")

    def tail_pusher(tid):
        try:
            for t in range(10):
                chat = f"9999000{tid % 8}"
                msg = 500 + t
                offset = 50 * 1024 * 1024
                tail_data = bytes([(tid * 17 + t) % 256]) * (1024 * 1024)
                buffer.push_tail(chat, msg, offset, tail_data)
                time.sleep(0.002)
        except Exception as e:
            errors.append(f"Tail pusher {tid} error: {e}")

    def reader(rid):
        try:
            while not stop_event.is_set():
                target_p = rid % 8
                target_msg = 100 + (rid % 10)
                chat = f"9999000{target_p}"
                res = buffer.get_slice(chat, target_msg, 0, 1024)
                if res is not None:
                    assert len(res) == 1024
                time.sleep(0.001)
        except Exception as e:
            errors.append(f"Reader {rid} error: {e}")

    def stats_worker(sid):
        try:
            while not stop_event.is_set():
                st = buffer.get_stats()
                assert "active_streams_count" in st
                assert "total_buffered_bytes" in st
                assert st["total_buffered_bytes"] <= buffer.TOTAL_GLOBAL_RAM_BUDGET
                time.sleep(0.002)
        except Exception as e:
            errors.append(f"Stats worker {sid} error: {e}")

    with ThreadPoolExecutor(max_workers=24) as pool:
        reader_futures = [pool.submit(reader, r) for r in range(num_readers)]
        stats_futures = [pool.submit(stats_worker, s) for s in range(num_stats)]
        tail_futures = [pool.submit(tail_pusher, t) for t in range(num_tail_pushers)]
        pusher_futures = [pool.submit(chunk_pusher, p) for p in range(num_pushers)]

        for pf in pusher_futures:
            pf.result()
        for tf in tail_futures:
            tf.result()

        stop_event.set()
        for rf in reader_futures:
            rf.result()
        for sf in stats_futures:
            sf.result()

    assert not errors, f"Buffer hammer concurrency errors: {errors[:5]}"
    assert buffer._get_total_ram_bytes() <= buffer.TOTAL_GLOBAL_RAM_BUDGET

