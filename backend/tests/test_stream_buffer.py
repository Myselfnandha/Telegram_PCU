import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from app.services.stream_buffer import StreamSlidingBuffer


def test_ram_budget_256mb_ceiling():
    buffer = StreamSlidingBuffer()
    chunk_size = 10 * 1024 * 1024
    for i in range(35):
        buffer.push(chat_id="chan", message_id=i, offset=0, data=b"X" * chunk_size)
    total_allocated = buffer._get_total_ram_bytes()
    assert total_allocated <= buffer.TOTAL_GLOBAL_RAM_BUDGET
    assert buffer.TOTAL_GLOBAL_RAM_BUDGET == 256 * 1024 * 1024


def test_multi_segment_forward_and_backward_seek_history_retention():
    buffer = StreamSlidingBuffer()
    chat_id = "test_stream"
    message_id = 101

    seg1 = b"1" * 1000000
    seg2 = b"2" * 1000000
    seg3 = b"3" * 1000000

    buffer.push(chat_id, message_id, 0, seg1)
    buffer.push(chat_id, message_id, 50 * 1024 * 1024, seg2)
    buffer.push(chat_id, message_id, 25 * 1024 * 1024, seg3)

    r1 = buffer.get_slice(chat_id, message_id, 0, 1000000)
    r2 = buffer.get_slice(chat_id, message_id, 50 * 1024 * 1024, 1000000)
    r3 = buffer.get_slice(chat_id, message_id, 25 * 1024 * 1024, 1000000)

    assert r1 == seg1
    assert r2 == seg2
    assert r3 == seg3


def test_contiguous_stitched_assembly_across_boundaries():
    buffer = StreamSlidingBuffer()
    chat_id = "test_stitch"
    message_id = 202

    part1 = b"ABCDE" * 200
    part2 = b"FGHIJ" * 200
    buffer.push(chat_id, message_id, 1000, part1)
    buffer.push(chat_id, message_id, 2000, part2)

    stitched = buffer.get_slice(chat_id, message_id, 1500, 1000)
    expected = (part1 + part2)[500:1500]
    assert stitched == expected


def test_header_and_tail_prewarm_limits():
    buffer = StreamSlidingBuffer()
    chat_id = "prewarm_test"
    message_id = 303

    assert buffer.HEADER_SIZE == 16 * 1024 * 1024
    assert buffer.TAIL_SIZE == 16 * 1024 * 1024

    header_data = b"H" * (20 * 1024 * 1024)
    buffer.push(chat_id, message_id, 0, header_data)

    head_key = (chat_id, message_id)
    assert len(buffer._headers[head_key]) == buffer.HEADER_SIZE

    read_head = buffer.get_slice(chat_id, message_id, 0, 1024 * 1024)
    assert read_head == b"H" * (1024 * 1024)

    tail_offset = 200 * 1024 * 1024
    tail_data = b"T" * (2 * 1024 * 1024)
    buffer.push_tail(chat_id, message_id, tail_offset, tail_data)

    read_tail = buffer.get_slice(chat_id, message_id, tail_offset, len(tail_data))
    assert read_tail == tail_data

    big_tail = b"Z" * (18 * 1024 * 1024)
    buffer.push_tail(chat_id, message_id, tail_offset + (1 * 1024 * 1024), big_tail)
    tail_key = (chat_id, message_id)
    assert len(buffer._tails[tail_key]["data"]) <= buffer.TAIL_SIZE


def test_sub_slice_and_miss_behavior():
    buffer = StreamSlidingBuffer()
    chat_id = "miss_test"
    message_id = 404

    data = b"0123456789" * 100
    buffer.push(chat_id, message_id, 5000, data)

    sub = buffer.get_slice(chat_id, message_id, 5010, 20)
    assert sub == data[10:30]

    miss = buffer.get_slice(chat_id, message_id, 10000, 50)
    assert miss is None


def test_concurrent_multi_stream_pushes_without_runtime_error():
    buffer = StreamSlidingBuffer()
    num_threads = 12
    chunks_per_thread = 10
    chunk_bytes = 2 * 1024 * 1024

    errors = []

    def pusher(thread_id):
        try:
            for c in range(chunks_per_thread):
                chat = f"multi_stream_{thread_id}"
                msg = 600 + c
                data = bytes([(thread_id * 13 + c) % 256]) * chunk_bytes
                buffer.push(chat, msg, c * 512 * 1024, data)
                if c % 3 == 0:
                    buffer.get_slice(chat, msg, 0, 1024)
        except Exception as e:
            errors.append(f"Thread {thread_id} error: {e}")

    with ThreadPoolExecutor(max_workers=num_threads) as pool:
        futures = [pool.submit(pusher, t) for t in range(num_threads)]
        for f in futures:
            f.result()

    assert not errors, f"Buffer concurrency errors: {errors}"
    total = buffer._get_total_ram_bytes()
    assert total <= buffer.TOTAL_GLOBAL_RAM_BUDGET


def test_pushing_heavy_tails_and_headers_strictly_respects_256mb_ram_ceiling():
    buffer = StreamSlidingBuffer()
    tail_chunk = b"T" * (16 * 1024 * 1024)
    for i in range(30):
        buffer.push_tail(f"tail_chat_{i}", i, 100 * 1024 * 1024, tail_chunk)
        assert buffer._get_total_ram_bytes() <= buffer.TOTAL_GLOBAL_RAM_BUDGET

    assert buffer._get_total_ram_bytes() <= buffer.TOTAL_GLOBAL_RAM_BUDGET

    head_chunk = b"H" * (20 * 1024 * 1024)
    for i in range(30):
        buffer.push(f"head_chat_{i}", i, 0, head_chunk)
        assert buffer._get_total_ram_bytes() <= buffer.TOTAL_GLOBAL_RAM_BUDGET

    assert buffer._get_total_ram_bytes() <= buffer.TOTAL_GLOBAL_RAM_BUDGET


def test_bidirectional_chat_id_normalization_in_buffer():
    buffer = StreamSlidingBuffer()
    raw_cid = "1234567890"
    prefixed_cid = "-1001234567890"
    msg_id = 555
    data = b"NORMALIZED_STREAM_CONTENT"

    buffer.push(prefixed_cid, msg_id, 0, data)
    read_via_raw = buffer.get_slice(raw_cid, msg_id, 0, len(data))
    assert read_via_raw == data

    buffer.clear()

    buffer.push(raw_cid, msg_id, 0, data)
    read_via_prefixed = buffer.get_slice(prefixed_cid, msg_id, 0, len(data))
    assert read_via_prefixed == data


def test_edge_case_chat_id_formats():
    buffer = StreamSlidingBuffer()
    test_cases = [
        "-100",
        "-1000",
        "0",
        "chat_alpha_numeric",
        "-100111",
        "111",
    ]
    for cid in test_cases:
        data = f"DATA_FOR_{cid}".encode()
        buffer.push(cid, 1, 0, data)
        read = buffer.get_slice(cid, 1, 0, len(data))
        assert read == data
