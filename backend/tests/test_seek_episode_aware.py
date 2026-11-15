import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio
import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from telethon.tl.types import Document

from app.services.stream_engine import StreamEngine, SeekEpisode, SeekEpisodeManager, ChunkTask
from app.services.stream_buffer import stream_buffer
from app.services.stream_cache import stream_cache_service


def test_seek_episode_manager_observe_groups_probes_and_retains():
    mgr = SeekEpisodeManager()
    chat_id = "test_episode_chat"
    msg_id = 5501
    w_size = 1024 * 1024

    stream_buffer.clear()

    ep1 = mgr.observe(chat_id, msg_id, 91 * w_size)
    assert ep1.latest_target == 91 * w_size
    assert 91 * w_size in ep1.candidate_windows
    assert 90 * w_size in ep1.candidate_windows
    assert 92 * w_size in ep1.candidate_windows
    assert stream_buffer.is_chunk_retained(chat_id, msg_id, 91 * w_size)

    ep2 = mgr.observe(chat_id, msg_id, 88 * w_size)
    assert ep2.episode_id == ep1.episode_id
    assert 88 * w_size in ep2.candidate_windows
    assert 91 * w_size in ep2.candidate_windows
    assert stream_buffer.is_chunk_retained(chat_id, msg_id, 88 * w_size)
    assert stream_buffer.is_chunk_retained(chat_id, msg_id, 91 * w_size)

    ep3 = mgr.observe(chat_id, msg_id, 85 * w_size)
    assert ep3.episode_id == ep1.episode_id
    assert 85 * w_size in ep3.candidate_windows
    assert 88 * w_size in ep3.candidate_windows
    assert 91 * w_size in ep3.candidate_windows

    assert mgr.is_candidate_window(chat_id, msg_id, 85 * w_size)
    assert mgr.is_candidate_window(chat_id, msg_id, 88 * w_size)
    assert mgr.is_candidate_window(chat_id, msg_id, 91 * w_size)


def test_seek_episode_budget_bounded_to_16_windows():
    mgr = SeekEpisodeManager()
    chat_id = "test_budget_chat"
    msg_id = 5502
    w_size = 1024 * 1024

    ep = None
    for i in range(25):
        ep = mgr.observe(chat_id, msg_id, i * 4 * w_size)

    assert ep is not None
    assert len(ep.candidate_windows) <= 16


@pytest.mark.asyncio
async def test_chunk_task_deduplication_and_join():
    engine = StreamEngine()
    norm_cid = "test_join_chat"
    msg_id = 5503
    offset = 40 * 1024 * 1024
    chunk_size = 1024 * 1024
    chunk_key = (norm_cid, msg_id, offset)

    stream_buffer.clear()
    stream_cache_service.clear_cache()

    ct = ChunkTask(chunk_key)
    ct.subscribers.add("req_1")
    engine._chunk_task_registry[chunk_key] = ct
    engine._in_flight_chunks[chunk_key] = ct.future

    assert len(ct.subscribers) == 1
    assert not ct.future.done()

    ct.subscribers.add("req_2")
    assert len(ct.subscribers) == 2

    fake_data = b"M" * chunk_size
    ct.future.set_result(fake_data)

    res1 = await ct.future
    assert res1 == fake_data


@pytest.mark.asyncio
async def test_soft_cancellation_preserves_episode_chunks():
    engine = StreamEngine()
    chat_id = "test_soft_cancel_chat"
    msg_id = 5504
    file_size = 100 * 1024 * 1024
    w_size = 1024 * 1024

    stream_buffer.clear()
    stream_cache_service.clear_cache()

    ep = engine.seek_episode_manager.observe(chat_id, msg_id, 10 * w_size)
    assert 10 * w_size in ep.candidate_windows

    stop_event = asyncio.Event()

    mock_client = MagicMock()
    mock_client.session = MagicMock(dc_id=4)
    fake_message = MagicMock()
    fake_message.media = Document(
        id=msg_id,
        access_hash=12345,
        file_reference=b"ref_soft",
        date=None,
        mime_type="video/mp4",
        size=file_size,
        dc_id=4,
        thumbs=None,
        video_thumbs=None,
        attributes=[],
    )
    fake_message.file = MagicMock(size=file_size, name="soft_test.mp4")

    mock_chunk_data = b"K" * w_size
    mock_res = MagicMock()
    mock_res.bytes = mock_chunk_data

    async def mock_call(s, req, flood_sleep_threshold=0):
        await asyncio.sleep(0.05)
        return mock_res

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
            start=10 * w_size,
            length=w_size,
            file_size=file_size,
            is_media_player=True,
            clean_name="soft_test.mp4",
            message=fake_message,
            external_stop_event=stop_event,
            request_id="req_test_1"
        )

        stream_task = asyncio.create_task(gen.__anext__())
        await asyncio.sleep(0.01)
        stop_event.set()

        try:
            c = await stream_task
            assert c == mock_chunk_data
        except (asyncio.CancelledError, StopAsyncIteration, ConnectionResetError):
            pass

        await asyncio.sleep(0.08)
        assert stream_buffer.get_slice(chat_id, msg_id, 10 * w_size, w_size) == mock_chunk_data
        await gen.aclose()
