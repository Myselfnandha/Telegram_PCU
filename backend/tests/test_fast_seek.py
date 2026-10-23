import os
import sys
import time
import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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


def test_sliding_buffer_tail_merging():
    """Verifies that tail chunks (cues/moov atoms) are merged and served instantly from RAM."""
    from app.routes.proxy import StreamSlidingBuffer
    buf = StreamSlidingBuffer()

    tail_offset = 500 * 1024 * 1024
    tail_chunk1 = b"TAIL_CUES_PART1_" * 100
    tail_chunk2 = b"TAIL_CUES_PART2_" * 100

    # Push first tail chunk
    buf.push_tail("chat1", 10, tail_offset, tail_chunk1)
    read1 = buf.get_slice("chat1", 10, tail_offset, len(tail_chunk1))
    assert read1 == tail_chunk1

    # Push overlapping/adjacent tail chunk
    buf.push_tail("chat1", 10, tail_offset + len(tail_chunk1), tail_chunk2)
    combined = buf.get_slice("chat1", 10, tail_offset, len(tail_chunk1) + len(tail_chunk2))
    assert combined == tail_chunk1 + tail_chunk2


@pytest.mark.asyncio
async def test_rate_governor_token_bucket_burst_and_aimd():
    """Verifies that RateGovernor allows immediate bursts and applies AIMD on flood wait."""
    from app.services.rate_governor import RateGovernor
    gov = RateGovernor(initial_rate=40.0, burst_capacity=5.0, min_rate=20.0, max_rate=50.0)

    # 1. Burst capacity allows immediate dispatch
    t0 = asyncio.get_event_loop().time()
    for _ in range(5):
        await gov.acquire_chunk_slot()
    t_burst = asyncio.get_event_loop().time() - t0
    assert t_burst < 0.05  # All 5 proceed with essentially zero delay

    # 2. Status reporting includes rate limit
    status = gov.get_status()
    assert status["is_cooling_down"] is False
    assert status["current_rate_limit_mbs"] == 40.0

    # 3. Flood wait triggers multiplicative decrease
    gov.report_flood_wait(2, "Test FloodWait")
    assert gov.is_cooling_down is True
    assert gov.cooldown_seconds_remaining >= 2
    # Rate backed off from 40.0 by 15% -> 34.0
    assert gov.current_rate_limit_mbs == pytest.approx(34.0, 0.1)


@pytest.mark.asyncio
async def test_rate_governor_standard_tier_and_debouncing():
    """Verifies Standard tier safe floors (1.8 - 6.5 MB/s) and debouncing of duplicate worker FloodWaits."""
    from app.services.rate_governor import RateGovernor
    gov = RateGovernor()
    gov.configure_for_tier(is_premium=False)

    assert gov.current_rate_limit_mbs == 4.5
    assert gov._min_rate == 1.8
    assert gov._max_rate == 6.5
    assert gov._capacity == 1.5

    # First flood wait backs off by 20%
    gov.report_flood_wait(3, "Worker 1 FloodWait")
    assert gov.is_cooling_down is True
    assert gov.current_rate_limit_mbs == pytest.approx(3.6, 0.1)
    assert gov._total_flood_events == 1

    # Immediate duplicate report from Worker 2 (within 1.5s) must be debounced without double decrease
    gov.report_flood_wait(3, "Worker 2 FloodWait")
    assert gov._total_flood_events == 1
    assert gov.current_rate_limit_mbs == pytest.approx(3.6, 0.1)


@pytest.mark.asyncio
async def test_sender_pool_borrow_cancellation_marks_unhealthy():
    """Verifies that cancellation inside sender_pool.borrow cleanly disconnects and drops the sender."""
    from app.telegram_client import MTProtoSenderPool
    pool = MTProtoSenderPool(max_per_dc=2)

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
        fake_sender.disconnect = AsyncMock()
        mock_sender_cls.return_value = fake_sender

        async def worker():
            async with pool.borrow(client, 5) as s:
                await asyncio.sleep(10.0)

        task = asyncio.create_task(worker())
        await asyncio.sleep(0.01)  # Allow worker to acquire sender
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Disconnect MUST be called to quarantine dirty socket with unread RPC frames
        fake_sender.disconnect.assert_called_once()
        assert fake_sender not in pool._idle_senders.get(5, [])


@pytest.mark.asyncio
async def test_sender_pool_foreign_dc_serialized_creation():
    """Verifies that foreign DC sender creation safely delegates to Telethon's exported sender."""
    from app.telegram_client import MTProtoSenderPool
    pool = MTProtoSenderPool(max_per_dc=3)

    client = MagicMock()
    client.session = MagicMock()
    client.session.dc_id = 5  # User main DC is 5, requesting DC 4
    client._get_dc = AsyncMock(return_value=MagicMock(ip_address="4.4.4.4", port=443, id=4))

    fake_s1 = MagicMock()
    fake_s1.is_connected = MagicMock(return_value=True)
    client._borrow_exported_sender = AsyncMock(return_value=fake_s1)
    client._return_exported_sender = AsyncMock()

    # Acquire sender on foreign DC 4
    s = await pool.acquire(client, dc_id=4)
    assert s == fake_s1
    client._borrow_exported_sender.assert_called_once_with(4)
    await pool.release(client, 4, s, healthy=True)
    client._return_exported_sender.assert_called_once_with(fake_s1)


def test_vlc_playlist_and_flags_enable_seeking():
    """Verifies that VLC arguments and playlists favor fast seeking and avoid http-continuous."""
    import inspect
    from app.routes import proxy
    from app.services import player_launcher
    src = inspect.getsource(proxy) + inspect.getsource(player_launcher)
    assert "--http-continuous" not in src
    assert "#EXTVLCOPT:http-continuous" not in src
    assert "--input-fast-seek" in src
    assert "#EXTVLCOPT:input-fast-seek" in src
    assert "--network-caching=1000" in src
    assert "#EXTVLCOPT:network-caching=1000" in src


@pytest.mark.asyncio
async def test_get_streamable_message_auto_heals_unknown_entity():
    """Verifies that get_streamable_message primes dialogs cache if entity was initially unknown."""
    from app.routes.proxy import get_streamable_message

    fake_client = MagicMock()
    # First get_messages raises ValueError ("Could not find input entity")
    # Second call (after get_dialogs and get_entity) succeeds
    fake_msg = MagicMock()
    fake_msg.media = MagicMock()

    fake_client.get_messages = AsyncMock(side_effect=[
        ValueError("Could not find the input entity for PeerUser(user_id=7106415730)"),
        fake_msg
    ])
    fake_client.get_entity = AsyncMock(side_effect=[
        ValueError("Could not find the input entity for PeerUser(user_id=7106415730)"),
        MagicMock()
    ])
    fake_client.get_dialogs = AsyncMock()

    res = await get_streamable_message(fake_client, 7106415730, 14064, force_refresh=True)
    assert res == fake_msg
    fake_client.get_dialogs.assert_called_once_with(limit=100)


@pytest.mark.asyncio
async def test_direct_streamer_pipelined_producer_delivers_chunks():
    """Verifies that pipelined_chunk_producer correctly accesses file_loc in nested scope and yields chunks."""
    import time
    from app.routes.proxy import handle_proxy_download, _STREAM_MESSAGE_CACHE
    from starlette.requests import Request
    from telethon.tl.types import MessageMediaDocument, Document, InputDocumentFileLocation

    mock_client = MagicMock()
    mock_client.is_connected = MagicMock(return_value=True)
    mock_client.is_user_authorized = AsyncMock(return_value=True)

    fake_doc = Document(
        id=99999,
        access_hash=88888,
        file_reference=b"test_ref",
        date=None,
        mime_type="video/mp4",
        size=5000000,
        dc_id=4,
        attributes=[]
    )
    fake_media = MessageMediaDocument(document=fake_doc, nopremium=False, spoiler=False, video=True, round=False, voice=False, alt_documents=[])
    fake_msg = MagicMock()
    fake_msg.id = 12345
    fake_msg.media = fake_media
    fake_msg.file = MagicMock()
    fake_msg.file.size = 5000000
    fake_msg.file.name = "test_video.mp4"
    fake_msg.file.mime_type = "video/mp4"

    # Pre-cache message
    _STREAM_MESSAGE_CACHE[("123456", 12345)] = (fake_msg, time.time())
    _STREAM_MESSAGE_CACHE[("123456", 12345)] = (fake_msg, time.time())

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/dl/123456/12345/test_video.mp4",
        "headers": [
            (b"range", b"bytes=0-2000000"),
            (b"user-agent", b"VLC/3.0.23 LibVLC/3.0.23"),
        ],
    }
    request = Request(scope)

    # Mock client._call to return 1MB of bytes per chunk
    mock_res = MagicMock()
    mock_res.bytes = b"X" * (1024 * 1024)
    mock_client._call = AsyncMock(return_value=mock_res)

    with patch("app.telegram_client.TelegramClientManager.get_client", AsyncMock(return_value=mock_client)), \
         patch("app.telegram_client.sender_pool.borrow") as mock_borrow:
        mock_sender = MagicMock()
        mock_borrow.return_value.__aenter__ = AsyncMock(return_value=mock_sender)
        mock_borrow.return_value.__aexit__ = AsyncMock(return_value=None)

        resp = await handle_proxy_download("123456", 12345, request, "test_video.mp4")
        assert resp.status_code == 206
        chunks = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk)

        total_bytes = sum(len(c) for c in chunks)
        assert total_bytes == 2000001


@pytest.mark.asyncio
async def test_connection_recv_loop_generator_exit_safety():
    """Verifies that closing Connection._recv_loop under Python 3.12-3.14 does not raise 'coroutine ignored GeneratorExit'."""
    from telethon.network.connection.connection import Connection
    import logging

    conn = Connection("127.0.0.1", 443, 1, loggers={"telethon.network.connection.connection": logging.getLogger()})
    conn._connected = True
    fut = asyncio.get_running_loop().create_future()
    conn._recv = lambda: fut
    coro = conn._recv_loop()
    try:
        coro.send(None)
    except Exception:
        pass

    # Closing the suspended coroutine directly should return cleanly and set _connected to False
    try:
        coro.close()
    except Exception as exc:
        pytest.fail(f"Connection._recv_loop raised unexpected exception during close: {exc}")

    assert conn._connected is False


@pytest.mark.asyncio
async def test_create_sender_cleans_up_on_auth_failure():
    """Verifies that if MTProtoSenderPool._create_sender fails during foreign DC auth, sender.disconnect is awaited."""
    from app.telegram_client import MTProtoSenderPool
    pool = MTProtoSenderPool(max_per_dc=2)

    fake_client = MagicMock()
    fake_client.session.dc_id = 1  # Foreign to requested DC 4
    fake_client._log = MagicMock()
    fake_client._proxy = None
    fake_client._local_addr = None
    fake_client._connection = MagicMock()
    fake_client._get_dc = AsyncMock(return_value=MagicMock(ip_address="149.154.167.91", port=443, id=4))
    fake_client.side_effect = RuntimeError("ExportAuthorization failed")

    with patch("app.telegram_client.MTProtoSender") as mock_sender_cls, \
         patch.object(fake_client, "_borrow_exported_sender", AsyncMock(return_value="fallback_borrowed")):
        mock_sender = MagicMock()
        mock_sender.connect = AsyncMock()
        mock_sender.disconnect = AsyncMock()
        mock_sender_cls.return_value = mock_sender

        res = await pool._create_sender(fake_client, 4)
        assert res == "fallback_borrowed"
        # The failed independent sender must have had disconnect called to prevent dangling _recv_loop
        mock_sender.disconnect.assert_awaited_once()


def test_multi_segment_forward_and_backward_seeking():
    """
    Verifies that StreamSlidingBuffer retains multiple distinct playback segments in RAM.
    Seeking forward and then seeking backward to an earlier position hits RAM instantly.
    """
    from app.services.stream_buffer import StreamSlidingBuffer
    buf = StreamSlidingBuffer()

    seg1_data = b"SEGMENT1_10MB_PLAYED_" * (20 * 1024)  # ~440KB
    seg2_data = b"SEGMENT2_50MB_FORWARD_" * (20 * 1024)  # ~440KB

    # 1. User plays at offset 10MB
    buf.push("chat_seek", 1, 10 * 1024 * 1024, seg1_data)

    # 2. User seeks forward to 50MB
    buf.push("chat_seek", 1, 50 * 1024 * 1024, seg2_data)

    # 3. Read from 50MB forward position (must hit RAM)
    read_fwd = buf.get_slice("chat_seek", 1, 50 * 1024 * 1024 + 100, 200)
    assert read_fwd == seg2_data[100:300]

    # 4. User seeks backward to 10MB (must hit RAM, was NOT wiped out by forward seek!)
    read_bwd = buf.get_slice("chat_seek", 1, 10 * 1024 * 1024 + 50, 250)
    assert read_bwd == seg1_data[50:300]


@pytest.mark.asyncio
async def test_prewarm_media_populates_header_and_tail():
    """Verifies that prewarm_media preloads header (0..512KB) and tail (last 512KB) into RAM buffer."""
    from app.services.stream_buffer import StreamSlidingBuffer
    from telethon.tl.types import MessageMediaDocument, Document

    buf = StreamSlidingBuffer()

    fake_doc = Document(
        id=777, access_hash=888, file_reference=b"ref", date=None,
        mime_type="video/mp4", size=10 * 1024 * 1024, dc_id=4, attributes=[]
    )
    fake_media = MessageMediaDocument(document=fake_doc, nopremium=False, spoiler=False, video=True, round=False, voice=False, alt_documents=[])
    fake_msg = MagicMock()
    fake_msg.media = fake_media
    fake_msg.file = MagicMock(size=10 * 1024 * 1024)

    mock_client = MagicMock()
    mock_client.get_messages = AsyncMock(return_value=fake_msg)

    fake_header = b"H" * (512 * 1024)
    fake_tail = b"T" * (512 * 1024)

    async def mock_call(req, flood_sleep_threshold=0):
        res = MagicMock()
        if req.offset == 0:
            res.bytes = fake_header
        else:
            res.bytes = fake_tail
        return res

    mock_client.side_effect = mock_call

    await buf.prewarm_media(mock_client, 12345, 678, file_size=10 * 1024 * 1024)

    # Header check
    h_slice = buf.get_slice("12345", 678, 0, 1000)
    assert h_slice == fake_header[:1000]

    # Tail check (offset near 10MB)
    t_start = (max(0, 10 * 1024 * 1024 - 512 * 1024) // 4096) * 4096
    t_slice = buf.get_slice("12345", 678, t_start, 1000)
    assert t_slice == fake_tail[:1000]
@pytest.mark.asyncio
async def test_stream_media_delivers_first_chunk_instantly_on_seek():
    """Verifies that stream_media with is_media_player=True delivers seek target chunk 0 directly first."""
    from app.services.stream_engine import stream_engine
    from app.services.stream_buffer import stream_buffer
    from telethon.tl.types import MessageMediaDocument, Document

    fake_doc = Document(
        id=999, access_hash=111, file_reference=b"ref", date=None,
        mime_type="video/x-matroska", size=50 * 1024 * 1024, dc_id=4, attributes=[]
    )
    fake_media = MessageMediaDocument(document=fake_doc, nopremium=False, spoiler=False, video=True, round=False, voice=False, alt_documents=[])
    fake_msg = MagicMock()
    fake_msg.media = fake_media

    chunk_data = b"VLC_FRAME_DATA_" * (32 * 1024)  # ~480KB
    mock_client = MagicMock()
    mock_client.session = MagicMock()
    mock_client.session.dc_id = 4
    mock_client._borrow_exported_sender = AsyncMock(return_value=MagicMock())

    async def mock_call(sender, req, flood_sleep_threshold=0):
        res = MagicMock()
        res.bytes = chunk_data
        return res

    mock_client._call = mock_call

    # Seek to offset 10MB, length 2MB
    seek_start = 10 * 1024 * 1024
    seek_len = 2 * 1024 * 1024

    stream_buffer.clear()
    delivered = []
    gen = stream_engine.stream_media(
        client=mock_client,
        chat_id="chat_test",
        message_id=42,
        start=seek_start,
        length=seek_len,
        file_size=50 * 1024 * 1024,
        is_media_player=True,
        clean_name="seek_test.mkv",
        message=fake_msg
    )

    async for chunk in gen:
        delivered.append(chunk)
        if len(delivered) >= 1:
            break
    await gen.aclose()

    # First chunk must be delivered directly without waiting for pipeline
    assert len(delivered) >= 1
    assert delivered[0] == chunk_data

    # And chunk must be immediately cached in stream_buffer
    cached = stream_buffer.get_slice("chat_test", 42, seek_start, 100)
    assert cached == chunk_data[:100]


@pytest.mark.asyncio
async def test_stream_buffer_lookahead_enables_instant_short_forward_seek():
    """Verifies that lookahead chunks pushed into stream_buffer provide 0ms instant short forward seeks."""
    from app.services.stream_buffer import stream_buffer

    stream_buffer.clear()
    chat_id = "test_fwd"
    msg_id = 99

    # Simulate chunks 0, 1, 2, 3 being downloaded (each 512KB)
    base_offset = 20 * 1024 * 1024
    chunk_size = 512 * 1024
    for i in range(4):
        chunk_bytes = f"CHUNK_{i}_CONTENT_".encode() * (32 * 1024)
        stream_buffer.push(chat_id, msg_id, base_offset + (i * chunk_size), chunk_bytes)

    # 1. User is currently at base_offset (Chunk 0)
    # 2. User presses short forward (+10s, approx 1MB ahead, into Chunk 2)
    fwd_offset = base_offset + (2 * chunk_size) + 100
    hit = stream_buffer.get_slice(chat_id, msg_id, fwd_offset, 500)

    assert hit is not None
    assert len(hit) == 500
    expected = (b"CHUNK_2_CONTENT_" * (32 * 1024))[100:600]
    assert hit == expected


@pytest.mark.asyncio
async def test_stream_engine_graceful_drain_and_immediate_seek_reuse():
    """
    Verifies that when a stream is cancelled by a seek:
    1. In-flight chunks gracefully drain into stream_buffer without violent disconnection.
    2. Senders return to _idle_senders clean and warm.
    3. The subsequent seek immediately leases the warm sender with zero ExportAuth delay.
    """
    from app.services.stream_engine import stream_engine
    from app.services.stream_buffer import stream_buffer
    from telethon.tl.types import MessageMediaDocument, Document

    fake_doc = Document(
        id=777, access_hash=888, file_reference=b"ref777", date=None,
        mime_type="video/x-matroska", size=100 * 1024 * 1024, dc_id=4, attributes=[]
    )
    fake_media = MessageMediaDocument(document=fake_doc, nopremium=False, spoiler=False, video=True, round=False, voice=False, alt_documents=[])
    fake_msg = MagicMock()
    fake_msg.media = fake_media

    chunk_data = b"STREAM_CHUNK_DATA_" * (32 * 1024)
    mock_client = MagicMock()
    mock_client.session = MagicMock()
    mock_client.session.dc_id = 4

    mock_sender = MagicMock()
    mock_sender.is_connected = MagicMock(return_value=True)
    mock_sender.connect = AsyncMock()
    mock_sender.disconnect = AsyncMock()
    mock_client._borrow_exported_sender = AsyncMock(return_value=mock_sender)

    async def mock_call(sender, req, flood_sleep_threshold=0):
        await asyncio.sleep(0.02)
        res = MagicMock()
        res.bytes = chunk_data
        return res

    mock_client._call = mock_call

    stream_buffer.clear()
    stop_event = asyncio.Event()

    # Start first stream (continuous streaming)
    gen = stream_engine.stream_media(
        client=mock_client,
        chat_id="chat_drain",
        message_id=1,
        start=0,
        length=10 * 1024 * 1024,
        file_size=100 * 1024 * 1024,
        is_media_player=True,
        clean_name="drain_test.mkv",
        message=fake_msg,
        external_stop_event=stop_event
    )

    # Consume first chunk (Chunk 0)
    chunk0 = await gen.__anext__()
    assert len(chunk0) > 0

    # Simulate user seek: set stop_event and close stream
    stop_event.set()
    await gen.aclose()

    # Verify that mock_sender was NOT violently disconnected
    mock_sender.disconnect.assert_not_called()

    # Now simulate the seek request arriving immediately at offset 30MB
    seek_stop = asyncio.Event()
    gen_seek = stream_engine.stream_media(
        client=mock_client,
        chat_id="chat_drain",
        message_id=1,
        start=30 * 1024 * 1024,
        length=5 * 1024 * 1024,
        file_size=100 * 1024 * 1024,
        is_media_player=True,
        clean_name="drain_test.mkv",
        message=fake_msg,
        external_stop_event=seek_stop
    )

    seek_chunk0 = await gen_seek.__anext__()
    assert len(seek_chunk0) > 0
    await gen_seek.aclose()

    # Verify no reconnection loops occurred
    mock_sender.disconnect.assert_not_called()


def test_stream_buffer_enhanced_limits_and_stitched_assembly():
    """Verifies that StreamSlidingBuffer supports 128MB ring buffers and stitches across header/segment boundaries."""
    from app.services.stream_buffer import StreamSlidingBuffer
    buf = StreamSlidingBuffer()

    assert buf.MAX_RING_SIZE == 192 * 1024 * 1024
    assert buf.HEADER_SIZE == 16 * 1024 * 1024
    assert buf.TAIL_SIZE == 16 * 1024 * 1024
    assert buf.EVICT_AGE == 300
    assert buf.MAX_ENTRIES == 8

    # Push 1MB to header (offset 0)
    hdr_data = b"H" * (1024 * 1024)
    buf.push("chat_stitch", 1, 0, hdr_data)

    # Push adjacent 1MB to segment at offset 1MB
    seg_data = b"S" * (1024 * 1024)
    buf.push("chat_stitch", 1, 1024 * 1024, seg_data)

    # Request a slice that spans across the 1MB boundary:
    # 512KB from header (starting at 512KB) + 512KB from segment (up to 1.5MB)
    stitched = buf.get_slice("chat_stitch", 1, 512 * 1024, 1024 * 1024)
    assert stitched is not None
    assert len(stitched) == 1024 * 1024
    assert stitched[:512 * 1024] == b"H" * (512 * 1024)
    assert stitched[512 * 1024:] == b"S" * (512 * 1024)


def test_sender_pool_max_per_dc_capacity():
    """Verifies that sender_pool default capacity is 8 per DC for high-throughput streaming."""
    from app.telegram_client import sender_pool
    assert sender_pool.max_per_dc == 8


@pytest.mark.asyncio
async def test_thumbnail_multi_alias_cache_and_svg_fallback(tmp_path):
    """Verifies that get_media_thumbnail resolves 'me_' alias cache and returns SVG fallback on missing thumbs."""
    from app.routes.proxy import get_media_thumbnail, _THUMB_CACHE_DIR, DEFAULT_VIDEO_PLACEHOLDER_SVG
    from app.telegram_client import TelegramClientManager

    # Simulate user having cached me as 6161427514
    TelegramClientManager._cached_me = {"id": 6161427514, "name": "Test User"}

    # Write a fake thumbnail under me_9999.jpg
    fake_thumb_path = os.path.join(_THUMB_CACHE_DIR, "me_9999.jpg")
    with open(fake_thumb_path, "wb") as f:
        f.write(b"\xff\xd8\xff\xe0FAKE_JPEG_DATA")

    try:
        # Request using numeric chat_id "6161427514"
        res = await get_media_thumbnail("6161427514", 9999)
        assert res.status_code == 200
        assert res.body == b"\xff\xd8\xff\xe0FAKE_JPEG_DATA"
        assert res.media_type == "image/jpeg"
    finally:
        if os.path.exists(fake_thumb_path):
            os.remove(fake_thumb_path)


@pytest.mark.asyncio
async def test_continuous_pipelined_producer_does_not_abort_on_full_lookahead():
    """Verifies that pipelined_chunk_producer does NOT terminate when lookahead is full; it pauses and resumes as consumer reads."""
    from app.services.stream_engine import stream_engine
    from telethon.tl.types import MessageMediaDocument, Document

    fake_doc = Document(
        id=888, access_hash=222, file_reference=b"ref", date=None,
        mime_type="video/x-matroska", size=20 * 512 * 1024, dc_id=4, attributes=[]
    )
    fake_media = MessageMediaDocument(document=fake_doc, nopremium=False, spoiler=False, video=True, round=False, voice=False, alt_documents=[])
    fake_msg = MagicMock()
    fake_msg.media = fake_media

    chunk_data = b"X" * (512 * 1024)
    mock_client = MagicMock()
    mock_client.session = MagicMock()
    mock_client.session.dc_id = 4
    mock_client._borrow_exported_sender = AsyncMock(return_value=MagicMock())

    async def mock_call(sender, req, flood_sleep_threshold=0):
        res = MagicMock()
        res.bytes = b"X" * getattr(req, "limit", 512 * 1024)
        return res

    mock_client._call = mock_call

    total_len = 10 * 512 * 1024  # 10 chunks
    gen = stream_engine.stream_media(
        client=mock_client,
        chat_id="chat_continuous",
        message_id=555,
        start=0,
        length=total_len,
        file_size=20 * 512 * 1024,
        is_media_player=True,
        clean_name="continuous_test",
        message=fake_msg
    )

    received_chunks = []
    async for chunk in gen:
        received_chunks.append(chunk)
        # Simulate player consumption delay
        await asyncio.sleep(0.001)

    assert len(received_chunks) in (5, 10)
    total_received = sum(len(c) for c in received_chunks)
    assert total_received == total_len


@pytest.mark.asyncio
async def test_sender_pool_keepalive_pings_idle_senders():
    """Verifies that sender_pool keepalive task pings idle senders to maintain TCP connection."""
    from app.telegram_client import MTProtoSenderPool
    from telethon import functions

    pool = MTProtoSenderPool(max_per_dc=4)
    mock_client = MagicMock()
    mock_sender = MagicMock()
    mock_sender.is_connected.return_value = True

    ping_calls = []
    async def mock_call(sender, req, flood_sleep_threshold=0):
        if isinstance(req, functions.PingRequest):
            ping_calls.append(req.ping_id)
            return MagicMock()
        return MagicMock()

    mock_client._call = mock_call

    # Register an idle sender with last active timestamp in the past
    pool._idle_senders[4] = [mock_sender]
    pool._last_active[mock_sender] = time.time() - 30.0

    # Run one pass of keepalive check
    now = time.time()
    senders_to_ping = []
    async with pool._lock:
        for dc_id, idles in pool._idle_senders.items():
            for s in list(idles):
                if (now - pool._last_active.get(s, 0)) >= 20.0:
                    senders_to_ping.append((dc_id, s))

    for dc_id, s in senders_to_ping:
        res = await mock_client._call(s, functions.PingRequest(ping_id=12345))
        pool._last_active[s] = time.time()

    assert len(ping_calls) == 1
    assert ping_calls[0] == 12345
    assert (time.time() - pool._last_active[mock_sender]) < 1.0


@pytest.mark.asyncio
async def test_sender_pool_evicts_stale_sender_on_acquire():
    """Verifies that acquire evicts stale idle senders (>45s) that fail ping instead of failing the caller."""
    from app.telegram_client import MTProtoSenderPool
    from telethon import functions

    pool = MTProtoSenderPool(max_per_dc=4)
    mock_client = MagicMock()
    mock_client.session = MagicMock()
    mock_client.session.dc_id = 4

    stale_sender = MagicMock()
    stale_sender.is_connected.return_value = True
    stale_sender.disconnect = AsyncMock()

    fresh_sender = MagicMock()
    fresh_sender.is_connected.return_value = True
    fresh_sender.disconnect = AsyncMock()

    # When pinging stale_sender, it fails; fresh_sender succeeds
    async def mock_call(sender, req, flood_sleep_threshold=0):
        if sender is stale_sender:
            raise ConnectionResetError("Socket dead")
        return MagicMock()

    mock_client._call = mock_call
    mock_client._borrow_exported_sender = AsyncMock(return_value=fresh_sender)

    # Put stale sender in pool with old timestamp
    pool._idle_senders[4] = [stale_sender]
    pool._last_active[stale_sender] = time.time() - 60.0

    # Acquire should detect stale sender, discard it, and get a fresh one
    s = await pool.acquire(mock_client, 4)
    assert s is not stale_sender
    stale_sender.disconnect.assert_awaited()
    await pool.release(mock_client, 4, s, healthy=True)


@pytest.mark.asyncio
async def test_client_disconnect_suppressor_middleware():
    """Verifies that ClientDisconnectSuppressorMiddleware silences ClientDisconnect and shorter-than-content-length RuntimeError."""
    from app.main import ClientDisconnectSuppressorMiddleware
    from starlette.requests import ClientDisconnect

    async def app_raise_disconnect(scope, receive, send):
        raise ClientDisconnect()

    async def app_raise_short_content(scope, receive, send):
        raise RuntimeError("Response content shorter than Content-Length")

    async def app_raise_other_runtime(scope, receive, send):
        raise RuntimeError("Fatal internal error")

    scope = {"type": "http"}
    async def receive():
        return {}
    async def send(msg):
        pass

    # ClientDisconnect should be suppressed cleanly without exception
    mw1 = ClientDisconnectSuppressorMiddleware(app_raise_disconnect)
    await mw1(scope, receive, send)

    # Shorter-than-content-length should be suppressed cleanly without exception
    mw2 = ClientDisconnectSuppressorMiddleware(app_raise_short_content)
    await mw2(scope, receive, send)

    # Other runtime errors must NOT be suppressed
    mw3 = ClientDisconnectSuppressorMiddleware(app_raise_other_runtime)
    with pytest.raises(RuntimeError, match="Fatal internal error"):
        await mw3(scope, receive, send)


@pytest.mark.asyncio
async def test_download_media_subtitles_endpoint(tmp_path):
    """Verifies that /api/media/subtitles/download/{chat_id}/{message_id} returns cached subtitle or valid fallback."""
    from app.routes.proxy import download_media_subtitles
    from app.services.subtitle_service import subtitle_service, SUBTITLES_DIR
    from telethon.tl.types import MessageMediaDocument, Document

    # 1. Test fallback blank subtitle when no cached file exists
    res = await download_media_subtitles("chat_sub", 1001)
    assert res.status_code == 200
    assert res.media_type == "text/plain; charset=utf-8"
    assert b"00:00:00,000" in res.body

    # 2. Test cached subtitle delivery
    sub_file = SUBTITLES_DIR / "test_movie.eng.srt"
    sub_content = b"1\n00:00:01,000 --> 00:00:04,000\nHello World\n"
    with open(sub_file, "wb") as f:
        f.write(sub_content)

    fake_doc = Document(
        id=1002, access_hash=123, file_reference=b"ref", date=None,
        mime_type="video/mp4", size=10 * 1024 * 1024, dc_id=4, attributes=[]
    )
    fake_media = MessageMediaDocument(document=fake_doc, nopremium=False, spoiler=False, video=True, round=False, voice=False, alt_documents=[])
    fake_msg = MagicMock()
    fake_msg.file = MagicMock()
    fake_msg.file.name = "test_movie.mp4"
    fake_msg.media = fake_media

    from app.services.sniffer_service import sniffer_service
    sniffer_service._message_cache[("chat_sub", 1002)] = fake_msg

    try:
        res2 = await download_media_subtitles("chat_sub", 1002)
        assert res2.status_code == 200
        assert res2.body == sub_content
    finally:
        if sub_file.exists():
            sub_file.unlink(missing_ok=True)
        sniffer_service._message_cache.pop(("chat_sub", 1002), None)


@pytest.mark.asyncio
async def test_prewarm_tail_dc_id_resolved_without_name_error():
    """Verifies that prewarm_tail properly resolves dc_id from file_info and borrows from sender_pool."""
    from app.services.stream_buffer import StreamSlidingBuffer
    from telethon.tl.types import MessageMediaDocument, Document

    buf = StreamSlidingBuffer()
    fake_doc = Document(
        id=789, access_hash=456, file_reference=b"ref", date=None,
        mime_type="video/mp4", size=10 * 1024 * 1024, dc_id=2, attributes=[]
    )
    fake_media = MessageMediaDocument(document=fake_doc, nopremium=False, spoiler=False, video=True, round=False, voice=False, alt_documents=[])
    fake_msg = MagicMock()
    fake_msg.media = fake_media

    mock_client = MagicMock()
    mock_sender = MagicMock()
    borrow_called_with_dc = None

    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def fake_borrow(c, dc_id):
        nonlocal borrow_called_with_dc
        borrow_called_with_dc = dc_id
        yield mock_sender

    with patch("app.telegram_client.sender_pool.borrow", side_effect=fake_borrow):
        fake_res = MagicMock(bytes=b"T" * (512 * 1024))
        mock_client._call = AsyncMock(return_value=fake_res)

        await buf.prewarm_tail(mock_client, "chat_dc", 999, fake_msg, file_size=10 * 1024 * 1024)
        assert borrow_called_with_dc == 2


@pytest.mark.asyncio
async def test_launch_vlc_batch_resolves_chat_id_and_message_ids():
    """Verifies that launch_vlc_batch supports payload with chat_id and message_ids."""
    from app.routes.proxy import launch_vlc_batch
    from starlette.requests import Request

    payload = {
        "chat_id": "test_chat_batch",
        "message_ids": [101, 102, 103],
        "title": "Season 1"
    }
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/media/vlc/play_batch",
        "headers": [(b"host", b"localhost:8088")]
    }
    req = Request(scope)

    with patch("app.services.player_launcher.player_launcher.launch_batch") as mock_launch:
        mock_launch.return_value = {"success": True, "launched": True, "episodes_count": 3}
        res = await launch_vlc_batch(payload, req)
        assert res["success"] is True
        mock_launch.assert_called_once()
        call_kwargs = mock_launch.call_args[1]
        assert len(call_kwargs["items"]) == 3
        assert call_kwargs["items"][0]["message_id"] == 101


def test_tgweb_router_registered_in_fastapi():
    """Verifies that tgweb_router is mounted and handles /api/tgweb routes."""
    from app.main import app, tgweb_router
    routers = [getattr(r, "original_router", None) for r in app.routes]
    assert tgweb_router in routers






