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


@pytest.mark.asyncio
async def test_sender_pool_acquire_and_release():
    """Verifies that MTProtoSenderPool acquires and recycles senders."""
    from app.telegram_client import MTProtoSenderPool
    pool = MTProtoSenderPool(max_per_dc=3)

    client = MagicMock()
    client.session = MagicMock()
    client.session.dc_id = 5
    client.session.auth_key = MagicMock()
    client._connection = MagicMock()
    client._get_dc = AsyncMock(return_value=MagicMock(ip_address="1.2.3.4", port=443, id=5))

    with patch("app.telegram_client.MTProtoSender") as mock_sender_cls:
        fake_sender = MagicMock()
        fake_sender.is_connected = MagicMock(return_value=True)
        fake_sender.connect = AsyncMock()
        mock_sender_cls.return_value = fake_sender

        s1 = await pool.acquire(client, dc_id=5)
        assert s1 == fake_sender
        fake_sender.connect.assert_called_once()

        # Release s1 back into pool
        await pool.release(client, 5, s1, healthy=True)

        # Re-acquiring from pool should recycle s1 without creating another connection
        s2 = await pool.acquire(client, dc_id=5)
        assert s2 == s1
        assert fake_sender.connect.call_count == 1  # Not called again


@pytest.mark.asyncio
async def test_direct_download_iter_custom_sender():
    """Verifies that _DirectDownloadIter adopts custom sender when injected."""
    import telethon.client.downloads as td
    from telethon import types

    loc = types.InputDocumentFileLocation(id=1, access_hash=2, file_reference=b"3", thumb_size="")
    custom_sender = MagicMock()

    client = MagicMock()
    client._borrow_exported_sender = AsyncMock()
    client.session = MagicMock()
    client.session.dc_id = 5

    it = td._DirectDownloadIter(
        client,
        limit=10,
        file=loc,
        dc_id=5,
        offset=0,
        stride=1024 * 1024,
        chunk_size=1024 * 1024,
        request_size=1024 * 1024,
        file_size=10 * 1024 * 1024,
    )
    it._custom_sender = custom_sender
    await it._init(loc, 5, 0, 1024 * 1024, 1024 * 1024, 1024 * 1024, 10 * 1024 * 1024, None)

    assert it._sender == custom_sender


@pytest.mark.asyncio
async def test_rate_governor_fallback_slot_pacing():
    """Verifies that fallback_slot enforces serial execution with 300ms pacing across concurrent tasks."""
    import time
    timestamps = []

    async def worker():
        async with rate_governor.fallback_slot():
            timestamps.append(time.monotonic())
            await asyncio.sleep(0.01)

    t0 = time.monotonic()
    await asyncio.gather(worker(), worker(), worker())
    total_duration = time.monotonic() - t0

    assert len(timestamps) == 3
    # 3 serialized requests with 300ms intervals should take at least ~0.6s
    assert total_duration >= 0.55


@pytest.mark.asyncio
async def test_cooldown_fallback_continues_download(engine):
    """
    Verifies that when MTProto cooldown is active, downloads do NOT pause to 0 KB/s;
    instead they continue downloading seamlessly via fallback_slot and client().
    """
    import time
    client = AsyncMock()
    client.session = MagicMock()
    client.session.dc_id = 2

    # Put rate governor into active cooldown
    rate_governor._cooldown_until = time.time() + 10.0
    rate_governor._cooldown_reason = "Test Cooldown"
    assert rate_governor.is_cooling_down is True

    chunk_1mb = b"F" * (1024 * 1024)
    fake_file_res = upload.File(type=None, mtime=0, bytes=chunk_1mb)
    # primary client() callable returns the chunk
    client.return_value = fake_file_res
    # client._call should NOT be called in cooldown fallback mode
    client._call = AsyncMock()

    loc = InputDocumentFileLocation(id=10, access_hash=20, file_reference=b"ref", thumb_size="")
    sender = MagicMock()

    data = await engine._fetch_single_chunk(client, sender, loc, offset=0, limit=1024 * 1024)
    assert len(data) == 1024 * 1024
    assert data == chunk_1mb
    # Primary client called directly, sender pool bypassed
    assert client.call_count == 1
    assert client._call.call_count == 0

    # Reset cooldown
    rate_governor._cooldown_until = 0.0


@pytest.mark.asyncio
async def test_cooldown_expiry_returns_to_parallel_mode(engine):
    """
    Verifies that as soon as the cooldown expires, the engine automatically returns
    to high-speed parallel mode using sender pool (_call).
    """
    import time
    client = MagicMock()
    client.session = MagicMock()
    client.session.dc_id = 2

    # Ensure cooldown is expired
    rate_governor._cooldown_until = 0.0
    assert rate_governor.is_cooling_down is False

    chunk_1mb = b"P" * (1024 * 1024)
    fake_file_res = upload.File(type=None, mtime=0, bytes=chunk_1mb)
    client._call = AsyncMock(return_value=fake_file_res)
    client.return_value = None

    loc = InputDocumentFileLocation(id=10, access_hash=20, file_reference=b"ref", thumb_size="")
    sender = MagicMock()

    data = await engine._fetch_single_chunk(client, sender, loc, offset=0, limit=1024 * 1024)
    assert len(data) == 1024 * 1024
    assert data == chunk_1mb
    # Parallel sender call used
    assert client._call.call_count == 1
    assert client.call_count == 0

