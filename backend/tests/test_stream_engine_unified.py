import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.stream_engine import StreamEngine
from app.services.stream_buffer import stream_buffer
from app.services.stream_cache import stream_cache_service
from app.services.rate_governor import rate_governor
from app.telegram_client import MTProtoSenderPool, TelegramClientManager

@pytest.mark.asyncio
async def test_unified_stream_2tier_hybrid_cache_hit():
    engine = StreamEngine()
    test_cid = "test_unified_channel_99"
    msg_id = 99901
    offset = 0
    chunk_size = 1024 * 1024
    payload = b"U" * chunk_size

    stream_buffer.clear()
    stream_cache_service.put_slice(test_cid, msg_id, offset, payload)

    client = MagicMock()
    client.session = MagicMock(dc_id=5, user_id="111")
    client.is_connected = MagicMock(return_value=True)

    chunks = []
    async for chunk in engine.stream_media(
        client=client,
        chat_id=test_cid,
        message_id=msg_id,
        start=0,
        length=chunk_size,
        file_size=100 * 1024 * 1024,
        clean_name="test_unified_hit"
    ):
        chunks.append(chunk)

    assembled = b"".join(chunks)
    assert len(assembled) == chunk_size
    assert assembled == payload
    assert stream_buffer.get_slice(test_cid, msg_id, offset, chunk_size) == payload

@pytest.mark.asyncio
async def test_dual_anchor_prefetch_execution():
    engine = StreamEngine()
    test_cid = "test_anchor_channel"
    msg_id = 88801
    file_size = 20 * 1024 * 1024
    chunk_size = 1024 * 1024
    chunk_data = b"D" * chunk_size

    stream_buffer.clear()
    stream_cache_service.put_slice(test_cid, msg_id, 0, chunk_data)

    client = MagicMock()
    client.session = MagicMock(dc_id=5, user_id="111")
    client.is_connected = MagicMock(return_value=True)

    chunks = []
    async for c in engine.stream_media(
        client=client,
        chat_id=test_cid,
        message_id=msg_id,
        start=0,
        length=chunk_size,
        file_size=file_size,
        clean_name="test_anchor"
    ):
        chunks.append(c)

    assert len(chunks) >= 1
    assert b"".join(chunks) == chunk_data

@pytest.mark.asyncio
async def test_adaptive_sliding_window_burst_and_float():
    rate_governor.configure_for_tier(is_premium=False, secondary_premium=False)
    assert rate_governor.current_rate_limit_mbs == 9.0

    rate_governor.configure_for_tier(is_premium=True)
    assert rate_governor.current_rate_limit_mbs == 32.0

    rate_governor.configure_for_tier(is_premium=True, secondary_premium=False)
    assert rate_governor.current_rate_limit_mbs == 28.5

@pytest.mark.asyncio
async def test_work_stealing_dispatcher_load_balancing():
    client1 = MagicMock()
    client1.session = MagicMock(dc_id=5, user_id="111")
    client1._is_secondary_client = False

    client2 = MagicMock()
    client2.session = MagicMock(dc_id=5, user_id="222")
    client2._is_secondary_client = True

    rate_governor.configure_for_tier(is_premium=False, secondary_premium=False)
    assert not rate_governor.is_client_cooling_down(client1)
    assert not rate_governor.is_client_cooling_down(client2)

    rate_governor.report_flood_wait(30, "Rate test", client=client1, all_clients_in_pool=[client1, client2])
    assert rate_governor.is_client_cooling_down(client1)
    assert not rate_governor.is_client_cooling_down(client2)

    healthy = [c for c in [client1, client2] if not rate_governor.is_client_cooling_down(c)]
    assert healthy == [client2]

@pytest.mark.asyncio
async def test_prewarmed_socket_pool_and_keepalive():
    pool = MTProtoSenderPool(max_per_dc=8)
    client = MagicMock()
    client.session = MagicMock(dc_id=2)
    client._is_secondary_client = False

    mock_sender_1 = MagicMock()
    mock_sender_1.is_connected = MagicMock(return_value=True)
    mock_sender_2 = MagicMock()
    mock_sender_2.is_connected = MagicMock(return_value=True)

    with patch.object(pool, "_create_sender", side_effect=[mock_sender_1, mock_sender_2]):
        await pool.prewarm_dc(client, 2, target_hot_senders=2)
        assert len(pool._idle_senders.get(2, [])) == 2

        await pool.prewarm_dc(client, 2, target_hot_senders=2)
        assert len(pool._idle_senders.get(2, [])) == 2

    async with pool.borrow(client, 2) as s:
        assert s is mock_sender_2 or s is mock_sender_1

    assert len(pool._idle_senders.get(2, [])) == 2
