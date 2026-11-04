import sys
import time
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

# Ensure backend root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.rate_governor import RateGovernor
from app.telegram_client import MTProtoSenderPool, TelegramClientManager


def test_multi_account_rate_governor_tier_combining():
    """Verify that RateGovernor properly combines capacity across pooled accounts."""
    gov = RateGovernor()

    # 1. Single standard account
    gov.configure_for_tier(is_premium=False, secondary_premium=None)
    assert gov.current_rate_limit_mbs == 4.5
    assert gov._min_rate == 1.8
    assert gov._max_rate == 6.5
    assert gov._capacity == 1.5

    # 2. Dual standard accounts (Standard + Standard) -> 2x speed!
    gov.configure_for_tier(is_premium=False, secondary_premium=False)
    assert gov.current_rate_limit_mbs == 9.0
    assert gov._min_rate == 3.6
    assert gov._max_rate == 13.0
    assert gov._capacity == 3.0

    # 3. Premium + Standard accounts -> 28.5 MB/s
    gov.configure_for_tier(is_premium=True, secondary_premium=False)
    assert gov.current_rate_limit_mbs == 28.5
    assert gov._min_rate == 13.8
    assert gov._max_rate == 38.5
    assert gov._capacity == 7.5

    # 4. Dual Premium accounts -> 48.0 MB/s
    gov.configure_for_tier(is_premium=True, secondary_premium=True)
    assert gov.current_rate_limit_mbs == 48.0
    assert gov._min_rate == 24.0
    assert gov._max_rate == 64.0
    assert gov._capacity == 12.0


def test_multi_account_flood_wait_isolation():
    """Verify that FloodWait on Account 2 isolates without stalling Account 1."""
    gov = RateGovernor()
    c1 = MagicMock()
    c2 = MagicMock()

    # Report 10s FloodWait on c2
    gov.report_flood_wait(10, reason="FloodWait on secondary", client=c2, all_clients_in_pool=[c1, c2])

    # c2 is cooling down
    assert gov.is_client_cooling_down(c2) is True
    # c1 is NOT cooling down
    assert gov.is_client_cooling_down(c1) is False
    # Global gate is NOT closed because c1 is healthy
    assert gov.is_cooling_down is False


@pytest.mark.asyncio
async def test_sender_pool_multi_account_namespacing():
    """Verify that senders for primary and secondary accounts are strictly namespaced by account key."""
    pool = MTProtoSenderPool(max_per_dc=4)

    primary_client = MagicMock()
    primary_client._is_secondary_client = False
    primary_client.session.dc_id = 5

    secondary_client = MagicMock()
    secondary_client._is_secondary_client = True
    secondary_client.session.dc_id = 5

    assert pool._get_key(primary_client, 4) == 4
    assert pool._get_key(secondary_client, 4) == "sec_4"

    # Primary sender
    s_primary = MagicMock()
    s_primary.is_connected.return_value = True
    pool._idle_senders[4] = [s_primary]

    # Secondary sender
    s_secondary = MagicMock()
    s_secondary.is_connected.return_value = True
    pool._idle_senders["sec_4"] = [s_secondary]

    # Acquiring for secondary should get s_secondary, never s_primary
    mock_call = AsyncMock(return_value=MagicMock())
    secondary_client._call = mock_call
    acquired_sec = await pool.acquire(secondary_client, 4)
    assert acquired_sec == s_secondary
    assert acquired_sec != s_primary

    # Clean purge on DC 4 purges both primary and secondary senders
    await pool.purge_dc(4)
    assert 4 not in pool._idle_senders
    assert "sec_4" not in pool._idle_senders


@pytest.mark.asyncio
async def test_stream_engine_private_channel_fallback():
    """Verify that when secondary client hits ChannelPrivateError, it falls back to primary client."""
    from telethon import errors
    from app.services.stream_engine import StreamEngine

    engine = StreamEngine()

    c1 = MagicMock()
    c1.session.dc_id = 5
    c1._is_secondary_client = False

    c2 = MagicMock()
    c2.session.dc_id = 5
    c2._is_secondary_client = True

    # c2 raises ChannelPrivateError
    async def mock_call_c2(sender, req, flood_sleep_threshold=0):
        raise errors.ChannelPrivateError(request=req)

    # c1 succeeds
    fake_res = MagicMock()
    fake_res.bytes = b"X" * (128 * 1024)
    async def mock_call_c1(sender, req, flood_sleep_threshold=0):
        return fake_res

    c1._call = mock_call_c1
    c2._call = mock_call_c2

    # Mock TelegramClientManager.get_active_streaming_clients to return [c1, c2]
    with patch("app.telegram_client.TelegramClientManager.get_active_streaming_clients", new_callable=AsyncMock) as mock_active:
        mock_active.return_value = [c1, c2]
        with patch("app.telegram_client.sender_pool.borrow") as mock_borrow:
            mock_borrow.return_value.__aenter__.return_value = MagicMock()
            mock_borrow.return_value.__aexit__.return_value = None

            # Stream a 128KB chunk
            stream = engine.stream_media(
                client=c1,
                chat_id="-100999999",
                message_id=1,
                start=0,
                length=128 * 1024,
                clean_name="test_private_fallback"
            )

            chunks = []
            async for chunk in stream:
                chunks.append(chunk)

            assert len(chunks) == 1
            assert len(chunks[0]) == 128 * 1024
