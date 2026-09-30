import os
import sys
import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.fast_stream_engine import FastStreamEngine, MediaStreamSession, SLICE_SIZE, MAX_MEM_CACHE_PER_MEDIA


def test_session_slice_range_and_availability():
    """Validates 1MB slice calculation and presence in memory."""
    session = MediaStreamSession("chat123", 100, 100 * 1024 * 1024, "video.mp4")
    assert session.total_slices == 100

    start, end = session.get_slice_range(0)
    assert start == 0
    assert end == 1024 * 1024 - 1

    start_5, end_5 = session.get_slice_range(5)
    assert start_5 == 5 * 1024 * 1024
    assert end_5 == 6 * 1024 * 1024 - 1

    # Store slice 5
    fake_data = b"X" * (1024 * 1024)
    session.store_slice(5, fake_data)
    assert session.is_slice_available(5)
    assert not session.is_slice_available(6)


def test_bidirectional_ring_buffer_eviction():
    """Verifies that slices behind and ahead of last_accessed_slice are preserved within 32MB window."""
    session = MediaStreamSession("chat123", 100, 200 * 1024 * 1024, "video.mp4")
    dummy = b"A" * 1024

    # Simulate playing at slice 50
    session.last_accessed_slice = 50

    # Fill slices around 50 (from 35 to 65)
    for idx in range(35, 66):
        session.store_slice(idx, dummy)

    # Store far slice (e.g. 95)
    session.store_slice(95, dummy)

    # Slices around 50 (e.g. slice 40 which is a backward seek) MUST be retained
    assert 40 in session.memory_slices
    assert 55 in session.memory_slices


@pytest.mark.asyncio
async def test_stream_byte_range_reads_cached_slices():
    """Verifies that seeking backwards directly reads from memory slices with zero network calls."""
    engine = FastStreamEngine()
    session = engine.get_or_create_session("chat123", 200, 50 * 1024 * 1024, "sample.mkv")

    # Pre-populate slice 10 (representing 10MB to 11MB)
    slice_data = b"KEYFRAME_DATA_" + (b"0" * (1024 * 1024 - 14))
    session.store_slice(10, slice_data)

    client = MagicMock()
    message = MagicMock()

    # Seek to byte 10MB + 500
    start_byte = 10 * 1024 * 1024 + 500
    end_byte = start_byte + 1000

    chunks = []
    async for c in engine.stream_byte_range(
        client, message, "chat123", 200, 50 * 1024 * 1024, "sample.mkv", start_byte, end_byte
    ):
        chunks.append(c)

    full_read = b"".join(chunks)
    assert len(full_read) == 1001
    assert full_read == slice_data[500:1501]
    # Ensure client.iter_download was NEVER called since it was cached in RAM
    client.iter_download.assert_not_called()


def test_sliding_buffer_header_and_backward_seek():
    """Verifies that StreamSlidingBuffer caches initial 2MB header and yields backward seeks from RAM."""
    from app.routes.proxy import StreamSlidingBuffer
    buf = StreamSlidingBuffer()

    header_data = b"HEADER_DATA_" * (100 * 1024)  # ~1.2MB
    buf.push("chat999", 555, 0, header_data)

    # 1. Header probe from 0
    slice1 = buf.get_slice("chat999", 555, 0, 1000)
    assert slice1 == header_data[:1000]

    # 2. Re-probe middle of header
    slice2 = buf.get_slice("chat999", 555, 5000, 2000)
    assert slice2 == header_data[5000:7000]

    # 3. Push continuous stream at offset 5MB
    stream_chunk1 = b"CHUNK_A_" * (64 * 1024)
    buf.push("chat999", 555, 5 * 1024 * 1024, stream_chunk1)

    stream_chunk2 = b"CHUNK_B_" * (64 * 1024)
    buf.push("chat999", 555, 5 * 1024 * 1024 + len(stream_chunk1), stream_chunk2)

    # Backward seek into stream_chunk1
    seek_read = buf.get_slice("chat999", 555, 5 * 1024 * 1024 + 100, 500)
    assert seek_read == stream_chunk1[100:600]

