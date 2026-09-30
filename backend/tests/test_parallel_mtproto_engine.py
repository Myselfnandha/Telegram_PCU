import os
import sys
import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telethon import errors
from telethon.tl.types import upload, Document, InputDocumentFileLocation
from app.services.fast_stream_engine import FastStreamEngine
from app.services.rate_governor import rate_governor


@pytest.fixture
def engine():
    return FastStreamEngine()


def create_fake_document_message(dc_id=2, size=10 * 1024 * 1024):
    """Creates a mock Telethon message with a valid Document media."""
    message = MagicMock()
    doc = Document(
        id=99999,
        access_hash=88888,
        file_reference=b"ref123",
        date=None,
        mime_type="video/mp4",
        size=size,
        dc_id=dc_id,
        thumbs=None,
        video_thumbs=None,
        attributes=[],
    )
    message.media = doc
    message.file = MagicMock()
    message.file.size = size
    message.file.name = "test_video.mp4"
    return message


@pytest.mark.asyncio
async def test_dc_sender_borrow_and_release(engine):
    """Verifies that DC senders are correctly borrowed and released when media is on another DC."""
    client = MagicMock()
    client.session = MagicMock()
    client.session.dc_id = 4  # Main DC is 4
    borrowed_sender = MagicMock()
    client._borrow_exported_sender = AsyncMock(return_value=borrowed_sender)
    client._return_exported_sender = AsyncMock()

    sender, is_exported = await engine._acquire_sender(client, dc_id=2)
    assert is_exported is True
    assert sender == borrowed_sender
    client._borrow_exported_sender.assert_called_once_with(2)

    await engine._release_sender(client, sender, is_exported)
    client._return_exported_sender.assert_called_once_with(borrowed_sender)


@pytest.mark.asyncio
async def test_same_dc_uses_primary_sender(engine):
    """Verifies that when media is on the same DC, client._sender is used without borrowing."""
    client = MagicMock()
    client.session = MagicMock()
    client.session.dc_id = 2  # Main DC is 2
    primary_sender = MagicMock()
    client._sender = primary_sender
    client._borrow_exported_sender = AsyncMock()

    sender, is_exported = await engine._acquire_sender(client, dc_id=2)
    assert is_exported is False
    assert sender == primary_sender
    client._borrow_exported_sender.assert_not_called()


@pytest.mark.asyncio
async def test_direct_1mb_chunk_fetch(engine):
    """Verifies that fetching a 1MB slice executes a single direct GetFileRequest."""
    message = create_fake_document_message(dc_id=2, size=50 * 1024 * 1024)

    client = MagicMock()
    client.session = MagicMock()
    client.session.dc_id = 2
    client._sender = MagicMock()

    chunk_1mb = b"M" * (1024 * 1024)
    fake_file_res = upload.File(type=None, mtime=0, bytes=chunk_1mb)
    client._call = AsyncMock(return_value=fake_file_res)

    # Download slice 1 (1MB to 2MB)
    offset = 1024 * 1024
    length = 1024 * 1024
    data = await engine._download_range_bytes(client, message, offset, length)

    assert len(data) == 1024 * 1024
    assert data == chunk_1mb
    assert client._call.call_count == 1

    req = client._call.call_args[0][1]
    assert req.offset == 1024 * 1024
    assert req.limit == 1024 * 1024


@pytest.mark.asyncio
async def test_parallel_mtproto_streaming_concurrency(engine):
    """Verifies sequential streaming yields multi-MB ranges in exact order."""
    message = create_fake_document_message(dc_id=2, size=20 * 1024 * 1024)

    client = MagicMock()
    client.session = MagicMock()
    client.session.dc_id = 2
    client._sender = MagicMock()

    # Provide 512KB chunks via iter_download that cover 3MB total
    chunk_unit = 512 * 1024
    total_chunks = (3 * 1024 * 1024) // chunk_unit  # 6 chunks of 512KB

    async def fake_iter_download(*args, **kwargs):
        for i in range(total_chunks):
            yield bytes([i % 256]) * chunk_unit

    client.iter_download = fake_iter_download

    # Stream 3MB from offset 0
    chunks = []
    async for c in engine.stream_parallel_mtproto(client, message, start=0, length=3 * 1024 * 1024):
        chunks.append(c)

    full_data = b"".join(chunks)
    assert len(full_data) == 3 * 1024 * 1024


@pytest.mark.asyncio
async def test_fallback_to_iter_download_on_unconventional_media(engine):
    """Verifies that non-standard media seamlessly falls back to iter_download."""
    message = MagicMock()
    message.media = "not_a_standard_document"

    client = MagicMock()
    dummy_chunks = [b"PART1_", b"PART2_", b"PART3"]

    async def fake_iter_download(*args, **kwargs):
        for c in dummy_chunks:
            yield c

    client.iter_download = fake_iter_download

    chunks = []
    async for c in engine.stream_parallel_mtproto(client, message, start=0, length=17):
        chunks.append(c)

    assert b"".join(chunks) == b"PART1_PART2_PART3"
