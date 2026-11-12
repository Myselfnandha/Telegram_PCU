import sys
import time
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from app.services.stream_buffer import StreamSlidingBuffer
from app.services.stream_cache import StreamCacheManager


def test_heavy_tails_30_streams_ram_ceiling():
    buffer = StreamSlidingBuffer()
    tail_chunk = b"T" * (16 * 1024 * 1024)
    for i in range(30):
        buffer.push_tail(f"heavy_tail_chat_{i}", i, 100 * 1024 * 1024, tail_chunk)
        ram = buffer._get_total_ram_bytes()
        assert ram <= buffer.TOTAL_GLOBAL_RAM_BUDGET, f"Iter {i} exceeded budget: {ram}"

    assert buffer._get_total_ram_bytes() <= buffer.TOTAL_GLOBAL_RAM_BUDGET
    stats = buffer.get_stats()
    assert stats["total_buffered_bytes"] <= buffer.TOTAL_GLOBAL_RAM_BUDGET


def test_heavy_headers_30_streams_ram_ceiling():
    buffer = StreamSlidingBuffer()
    head_chunk = b"H" * (20 * 1024 * 1024)
    for i in range(30):
        buffer.push(f"heavy_head_chat_{i}", i, 0, head_chunk)
        ram = buffer._get_total_ram_bytes()
        assert ram <= buffer.TOTAL_GLOBAL_RAM_BUDGET, f"Iter {i} exceeded budget: {ram}"

    assert buffer._get_total_ram_bytes() <= buffer.TOTAL_GLOBAL_RAM_BUDGET
    stats = buffer.get_stats()
    assert stats["total_buffered_bytes"] <= buffer.TOTAL_GLOBAL_RAM_BUDGET


def test_heavy_tails_and_headers_combined_50_streams():
    buffer = StreamSlidingBuffer()
    tail_chunk = b"T" * (16 * 1024 * 1024)
    head_chunk = b"H" * (16 * 1024 * 1024)

    for i in range(50):
        buffer.push_tail(f"stream_{i}", 100 + i, 200 * 1024 * 1024, tail_chunk)
        ram1 = buffer._get_total_ram_bytes()
        assert ram1 <= buffer.TOTAL_GLOBAL_RAM_BUDGET, f"Tail iter {i} exceeded budget: {ram1}"

        buffer.push(f"stream_{i}", 100 + i, 0, head_chunk)
        ram2 = buffer._get_total_ram_bytes()
        assert ram2 <= buffer.TOTAL_GLOBAL_RAM_BUDGET, f"Head iter {i} exceeded budget: {ram2}"

    assert buffer._get_total_ram_bytes() <= buffer.TOTAL_GLOBAL_RAM_BUDGET


def test_single_stream_exceeding_total_budget():
    buffer = StreamSlidingBuffer()
    chat_id = "monster_stream"
    msg_id = 9999
    chunk = b"M" * (10 * 1024 * 1024)

    for i in range(35):
        buffer.push(chat_id, msg_id, i * 10 * 1024 * 1024, chunk)
        ram = buffer._get_total_ram_bytes()
        assert ram <= buffer.TOTAL_GLOBAL_RAM_BUDGET, f"Step {i} exceeded budget: {ram}"

    assert buffer._get_total_ram_bytes() <= buffer.TOTAL_GLOBAL_RAM_BUDGET


def test_bidirectional_chat_id_resolution_standard_channel():
    buffer = StreamSlidingBuffer()
    raw_cid = "9876543210"
    prefixed_cid = "-1009876543210"
    int_raw = 9876543210
    int_prefixed = -1009876543210
    msg_id = 42

    header_data = b"HEAD_DATA_12345"
    buffer.push(prefixed_cid, msg_id, 0, header_data)

    assert buffer.get_slice(raw_cid, msg_id, 0, len(header_data)) == header_data
    assert buffer.get_slice(prefixed_cid, msg_id, 0, len(header_data)) == header_data
    assert buffer.get_slice(int_raw, msg_id, 0, len(header_data)) == header_data
    assert buffer.get_slice(int_prefixed, msg_id, 0, len(header_data)) == header_data

    tail_data = b"TAIL_DATA_67890"
    tail_offset = 50 * 1024 * 1024
    buffer.push_tail(raw_cid, msg_id, tail_offset, tail_data)

    assert buffer.get_slice(raw_cid, msg_id, tail_offset, len(tail_data)) == tail_data
    assert buffer.get_slice(prefixed_cid, msg_id, tail_offset, len(tail_data)) == tail_data
    assert buffer.get_slice(int_raw, msg_id, tail_offset, len(tail_data)) == tail_data
    assert buffer.get_slice(int_prefixed, msg_id, tail_offset, len(tail_data)) == tail_data


def test_concurrent_readers_writers_heavy_churn():
    buffer = StreamSlidingBuffer()
    num_writers = 8
    num_readers = 8
    iterations_per_worker = 20
    chunk_size = 2 * 1024 * 1024

    errors = []
    stop_event = threading.Event()

    def writer_task(worker_id):
        try:
            for it in range(iterations_per_worker):
                if stop_event.is_set():
                    break
                chat = f"churn_chan_{worker_id}_{it % 5}"
                msg = 1000 + (it % 10)
                offset = (it % 4) * 1024 * 1024
                data = bytes([(worker_id * 19 + it) % 256]) * chunk_size

                if it % 2 == 0:
                    buffer.push(chat, msg, offset, data)
                else:
                    buffer.push_tail(chat, msg, 50 * 1024 * 1024 + offset, data)

                ram = buffer._get_total_ram_bytes()
                if ram > buffer.TOTAL_GLOBAL_RAM_BUDGET:
                    errors.append(f"Writer {worker_id} observed RAM leak: {ram}")
                    stop_event.set()
                    break
        except Exception as e:
            errors.append(f"Writer {worker_id} exception: {e}")
            stop_event.set()

    def reader_task(worker_id):
        try:
            for it in range(iterations_per_worker * 2):
                if stop_event.is_set():
                    break
                target_writer = it % num_writers
                chat = f"churn_chan_{target_writer}_{it % 5}"
                msg = 1000 + (it % 10)
                offset = (it % 4) * 1024 * 1024

                res = buffer.get_slice(chat, msg, offset, 4096)
                stats = buffer.get_stats()
                if stats["total_buffered_bytes"] > buffer.TOTAL_GLOBAL_RAM_BUDGET:
                    errors.append(f"Reader {worker_id} observed stats RAM leak: {stats}")
                    stop_event.set()
                    break
        except Exception as e:
            errors.append(f"Reader {worker_id} exception: {e}")
            stop_event.set()

    with ThreadPoolExecutor(max_workers=num_writers + num_readers) as pool:
        futures = []
        for w in range(num_writers):
            futures.append(pool.submit(writer_task, w))
        for r in range(num_readers):
            futures.append(pool.submit(reader_task, r))

        for f in as_completed(futures):
            f.result()

    assert not errors, f"Concurrency test errors: {errors}"
    final_ram = buffer._get_total_ram_bytes()
    assert final_ram <= buffer.TOTAL_GLOBAL_RAM_BUDGET


def test_concurrent_clear_and_evict_stale_under_heavy_writes():
    buffer = StreamSlidingBuffer()
    num_writers = 4
    stop_event = threading.Event()
    errors = []

    def writer(wid):
        try:
            for i in range(20):
                if stop_event.is_set():
                    break
                data = bytes([(wid * 7 + i) % 256]) * (1024 * 1024)
                buffer.push(f"clear_test_{wid}", i, 0, data)
                buffer.push_tail(f"clear_test_{wid}", i, 20 * 1024 * 1024, data)
        except Exception as e:
            errors.append(f"Writer exception: {e}")

    def modifier():
        try:
            for _ in range(10):
                if stop_event.is_set():
                    break
                buffer._evict_stale()
                buffer.get_stats()
                buffer.clear()
        except Exception as e:
            errors.append(f"Modifier exception: {e}")

    with ThreadPoolExecutor(max_workers=num_writers + 1) as pool:
        futures = [pool.submit(writer, w) for w in range(num_writers)]
        futures.append(pool.submit(modifier))
        for f in as_completed(futures):
            f.result()

    assert not errors, f"Errors during concurrent clear/evict: {errors}"
    assert buffer._get_total_ram_bytes() <= buffer.TOTAL_GLOBAL_RAM_BUDGET


def test_defect_cross_chat_data_leak_and_spoofing():
    buffer = StreamSlidingBuffer()
    user_a_cid = "2100456"
    user_b_cid = "2456"
    msg_id = 1
    private_payload = b"PRIVATE_PAYLOAD_USER_A"

    buffer.push(user_a_cid, msg_id, 0, private_payload)

    leaked_data = buffer.get_slice(user_b_cid, msg_id, 0, len(private_payload))
    assert leaked_data is None


def test_defect_cross_channel_data_leak_raw_100():
    buffer = StreamSlidingBuffer()
    chan_x_raw = "1005556667"
    chan_y_prefixed = "-1005556667"
    msg_id = 9
    chan_x_payload = b"CHANNEL_X_EXCLUSIVE_DATA"

    buffer.push(chan_x_raw, msg_id, 0, chan_x_payload)

    leaked_to_y = buffer.get_slice(chan_y_prefixed, msg_id, 0, len(chan_x_payload))
    assert leaked_to_y is None


def test_defect_disk_cache_cross_chat_leak(tmp_path, monkeypatch):
    cache_dir = tmp_path / "stream_cache"
    blocks_dir = cache_dir / "blocks"
    cache_dir.mkdir(parents=True, exist_ok=True)
    blocks_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.services.stream_cache.CACHE_DIR", cache_dir)
    monkeypatch.setattr("app.services.stream_cache.BLOCKS_DIR", blocks_dir)

    service = StreamCacheManager()
    user_b_cid = "2456"
    user_a_cid = "2100456"
    msg_id = 88
    payload = b"USER_B_DISK_DATA"

    service.put_slice(user_b_cid, msg_id, 0, payload)

    leaked_to_a = service.get_slice(user_a_cid, msg_id, 0, len(payload))
    assert leaked_to_a is None


def test_defect_4x_ram_budget_inflation_on_prefixed_channels():
    buffer = StreamSlidingBuffer()
    channel_cid = "-1001234567890"
    msg_id = 55
    chunk_10mb = b"K" * (10 * 1024 * 1024)

    buffer.push(channel_cid, msg_id, 0, chunk_10mb)

    total_ram = buffer._get_total_ram_bytes()
    assert total_ram == 20 * 1024 * 1024
