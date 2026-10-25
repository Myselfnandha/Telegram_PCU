import sys
import asyncio
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.stream_buffer import StreamSlidingBuffer
from app.services.stream_engine import StreamEngine


def test_stream_buffer_prefix_and_tail_slicing():
    """Verifies that StreamSlidingBuffer caches headers and tails accurately for 0ms replay."""
    buf = StreamSlidingBuffer()
    
    # 1. Push header data at offset 0
    header_data = b"HEAD" * 1024  # 4096 bytes
    buf.push("123", 456, 0, header_data)
    
    slice1 = buf.get_slice("123", 456, 0, 100)
    assert slice1 == header_data[:100]
    
    slice2 = buf.get_slice("123", 456, 100, 200)
    assert slice2 == header_data[100:300]
    
    # 2. Push tail metadata at offset 1,000,000
    tail_data = b"TAIL_METADATA_CUES"
    buf.push_tail("123", 456, 1000000, tail_data)
    
    tail_slice = buf.get_slice("123", 456, 1000000, len(tail_data))
    assert tail_slice == tail_data
    
    # 3. Clear resets everything
    buf.clear()
    assert buf.get_slice("123", 456, 0, 100) is None
    assert buf.get_slice("123", 456, 1000000, 10) is None


@pytest.mark.asyncio
async def test_stream_engine_delivers_cached_prefix_immediately():
    """Verifies that StreamEngine delivers pre-cached RAM prefix in first yield without network call."""
    engine = StreamEngine()
    fake_client = MagicMock()
    fake_prefix = b"INSTANT_RAM_DATA" * 64
    
    chunks = []
    async for chunk in engine.stream_media(
        client=fake_client,
        chat_id="123",
        message_id=456,
        start=0,
        length=len(fake_prefix),
        file_size=len(fake_prefix),
        cached_prefix=fake_prefix
    ):
        chunks.append(chunk)
        
    assert b"".join(chunks) == fake_prefix


@pytest.mark.asyncio
async def test_cinema_catalog_caching_and_resolution():
    """Verifies that CinemaCatalog handles message resolution, entity priming, and caching."""
    from app.services.cinema_catalog import CinemaCatalog

    catalog = CinemaCatalog()
    fake_client = MagicMock()
    fake_msg = MagicMock()
    fake_msg.id = 777
    fake_msg.media = MagicMock()
    fake_client.get_messages = AsyncMock(return_value=fake_msg)

    # 1. First fetch hits client
    res = await catalog.get_streamable_message(fake_client, "12345", 777)
    assert res == fake_msg
    fake_client.get_messages.assert_awaited_once()

    # 2. Second fetch hits memory cache without client call
    fake_client.get_messages.reset_mock()
    res2 = await catalog.get_streamable_message(fake_client, "12345", 777)
    assert res2 == fake_msg
    fake_client.get_messages.assert_not_awaited()

