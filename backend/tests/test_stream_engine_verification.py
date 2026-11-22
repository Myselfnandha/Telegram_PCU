import os
import sys
import asyncio
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

TEST_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TEST_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.stream_engine import StreamEngine, stream_buffer
from app.services.stream_cache import stream_cache_service
from telethon.tl.types import Document


@pytest.mark.asyncio
async def test_stream_engine_full_delivery_without_data_loss():
    """
    Verifies that StreamEngine delivers 100% of requested bytes without early truncation
    or data corruption when streaming without cancellation.
    """
    engine = StreamEngine()
    chat_id = "test_full_delivery_chat"
    msg_id = 9911
    chunk_size = 1024 * 1024
    total_chunks = 2
    length = total_chunks * chunk_size
    file_size = 20 * 1024 * 1024  # 20MB file, streaming first 2MB

    stream_buffer.clear()
    stream_cache_service.clear_cache()

    mock_client = MagicMock(loop=asyncio.get_running_loop())
    mock_client.session = MagicMock(dc_id=5)

    fake_doc = Document(
        id=msg_id,
        access_hash=998877,
        file_reference=b"ref_verify",
        date=None,
        mime_type="video/mp4",
        size=file_size,
        dc_id=5,
        thumbs=None,
        video_thumbs=None,
        attributes=[],
    )
    fake_msg = MagicMock(media=fake_doc, file=MagicMock(size=file_size, name="test_full.mp4"))

    expected_payload = b""
    chunk_dict = {}
    for i in range(total_chunks):
        pattern = f"CHUNK_{i:02d}_".encode("ascii")
        repeats = (chunk_size // len(pattern)) + 1
        c_bytes = (pattern * repeats)[:chunk_size]
        assert len(c_bytes) == chunk_size
        chunk_dict[i * chunk_size] = c_bytes
        expected_payload += c_bytes

    async def mock_call(s, req, flood_sleep_threshold=0):
        await asyncio.sleep(0.005)
        res = MagicMock()
        offset = getattr(req, "offset", 0)
        limit = getattr(req, "limit", chunk_size)
        data = chunk_dict.get(offset, b"X" * limit)
        res.bytes = data[:limit]
        return res

    mock_client._call = mock_call

    with patch("app.services.stream_engine.sender_pool.borrow") as mock_borrow, \
         patch("app.services.rust_bridge.rust_bridge.is_healthy", AsyncMock(return_value=False)), \
         patch("app.telegram_client.TelegramClientManager.get_active_streaming_clients", AsyncMock(return_value=[mock_client])):
        
        mock_borrow.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_borrow.return_value.__aexit__ = AsyncMock(return_value=None)

        gen = engine.stream_media(
            client=mock_client,
            chat_id=chat_id,
            message_id=msg_id,
            start=0,
            length=length,
            file_size=file_size,
            is_media_player=False,
            clean_name="test_full.mp4",
            message=fake_msg
        )

        delivered_bytes = bytearray()
        async for chunk in gen:
            delivered_bytes.extend(chunk)

        assert len(delivered_bytes) == length, f"Expected {length} bytes, got {len(delivered_bytes)}"
        assert bytes(delivered_bytes) == expected_payload, "Delivered byte stream does not match expected payload"
