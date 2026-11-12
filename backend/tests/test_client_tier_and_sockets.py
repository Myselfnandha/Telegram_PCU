import sys
import os
import time
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.rate_governor import RateGovernor, rate_governor
from app.telegram_client import MTProtoSenderPool, TelegramClientManager, sender_pool
from telethon import functions


def test_premium_tier_32mbs_ceiling():
    gov = RateGovernor()
    gov.configure_for_tier(is_premium=True, secondary_premium=None)
    assert gov.current_rate_limit_mbs == 32.0
    assert gov._rate == 32.0
    assert gov._min_rate == 16.0
    assert gov._max_rate == 36.0
    assert gov._capacity == 16.0


def test_standard_tier_4_5mbs_and_dual_9mbs():
    gov = RateGovernor()
    gov.configure_for_tier(is_premium=False, secondary_premium=None)
    assert gov.current_rate_limit_mbs == 4.5
    assert gov._min_rate == 1.8
    assert gov._max_rate == 6.5
    assert gov._capacity == 1.5

    gov.configure_for_tier(is_premium=False, secondary_premium=False)
    assert gov.current_rate_limit_mbs == 9.0
    assert gov._min_rate == 3.6
    assert gov._max_rate == 13.0
    assert gov._capacity == 3.0


@pytest.mark.asyncio
async def test_sync_tier_limits_lifecycle():
    orig_client = TelegramClientManager._client
    orig_ready = TelegramClientManager._is_ready
    orig_me = TelegramClientManager._cached_me
    orig_sec = TelegramClientManager._secondary_client
    orig_sec_ready = TelegramClientManager._is_secondary_ready
    orig_sec_me = TelegramClientManager._cached_secondary_me

    try:
        TelegramClientManager._client = MagicMock()
        TelegramClientManager._is_ready = True
        TelegramClientManager._cached_me = {"id": 101, "is_premium": True}
        TelegramClientManager._secondary_client = None
        TelegramClientManager._is_secondary_ready = False
        TelegramClientManager._cached_secondary_me = None

        TelegramClientManager.sync_tier_limits()
        assert rate_governor.current_rate_limit_mbs == 32.0

        TelegramClientManager._secondary_client = MagicMock()
        TelegramClientManager._is_secondary_ready = True
        TelegramClientManager._cached_secondary_me = {"id": 202, "is_premium": False}

        TelegramClientManager.sync_tier_limits()
        assert rate_governor.current_rate_limit_mbs == 28.5

        TelegramClientManager._cached_secondary_me = {"id": 202, "is_premium": True}
        TelegramClientManager.sync_tier_limits()
        assert rate_governor.current_rate_limit_mbs == 48.0

        TelegramClientManager._cached_me = {"id": 101, "is_premium": False}
        TelegramClientManager._cached_secondary_me = {"id": 202, "is_premium": False}
        TelegramClientManager.sync_tier_limits()
        assert rate_governor.current_rate_limit_mbs == 9.0

        TelegramClientManager._client = None
        TelegramClientManager._is_ready = False
        TelegramClientManager._cached_me = None
        TelegramClientManager.sync_tier_limits()
        assert rate_governor.current_rate_limit_mbs == 4.5

        TelegramClientManager._cached_secondary_me = {"id": 202, "is_premium": True}
        TelegramClientManager.sync_tier_limits()
        assert rate_governor.current_rate_limit_mbs == 32.0

        TelegramClientManager._secondary_client = None
        TelegramClientManager._is_secondary_ready = False
        TelegramClientManager._cached_secondary_me = None
        TelegramClientManager.sync_tier_limits()
        assert rate_governor.current_rate_limit_mbs == 4.5
    finally:
        TelegramClientManager._client = orig_client
        TelegramClientManager._is_ready = orig_ready
        TelegramClientManager._cached_me = orig_me
        TelegramClientManager._secondary_client = orig_sec
        TelegramClientManager._is_secondary_ready = orig_sec_ready
        TelegramClientManager._cached_secondary_me = orig_sec_me
        TelegramClientManager.sync_tier_limits()


@pytest.mark.asyncio
async def test_prewarm_dc_allocates_two_senders():
    pool = MTProtoSenderPool(max_per_dc=32)
    mock_client = MagicMock()
    mock_client.is_connected = MagicMock(return_value=True)

    created = []
    async def fake_create_sender(client, dc_id):
        s = MagicMock()
        s.is_connected.return_value = True
        created.append(s)
        return s

    pool._create_sender = fake_create_sender

    await pool.prewarm_dc(mock_client, dc_id=2, target_hot_senders=2)

    assert len(pool._idle_senders[2]) == 2
    assert len(created) == 2
    assert pool._idle_senders[2][0] == created[0]
    assert pool._idle_senders[2][1] == created[1]
    assert created[0] in pool._last_active
    assert created[1] in pool._last_active


@pytest.mark.asyncio
async def test_prewarm_dc_idempotent():
    pool = MTProtoSenderPool(max_per_dc=32)
    mock_client = MagicMock()
    mock_client.is_connected = MagicMock(return_value=True)

    created = []
    async def fake_create_sender(client, dc_id):
        s = MagicMock()
        s.is_connected.return_value = True
        created.append(s)
        return s

    pool._create_sender = fake_create_sender

    await pool.prewarm_dc(mock_client, dc_id=4, target_hot_senders=2)
    assert len(pool._idle_senders[4]) == 2
    assert len(created) == 2

    await pool.prewarm_dc(mock_client, dc_id=4, target_hot_senders=2)
    assert len(pool._idle_senders[4]) == 2
    assert len(created) == 2


@pytest.mark.asyncio
async def test_keepalive_20s_no_semaphore_over_release():
    pool = MTProtoSenderPool(max_per_dc=4)
    mock_client = MagicMock()
    mock_client.is_connected = MagicMock(return_value=True)

    dead_sender = MagicMock()
    dead_sender.is_connected.return_value = False
    dead_sender.disconnect = AsyncMock()

    failing_sender = MagicMock()
    failing_sender.is_connected.return_value = True
    failing_sender.disconnect = AsyncMock()

    pool._idle_senders[2] = [dead_sender, failing_sender]
    pool._last_active[dead_sender] = time.time() - 30.0
    pool._last_active[failing_sender] = time.time() - 25.0

    async def mock_call(s, req):
        if s is failing_sender:
            raise ConnectionResetError("Socket broken")
        return MagicMock()

    mock_client._call = AsyncMock(side_effect=mock_call)

    sem = pool.get_semaphore(2)
    initial_value = sem._value

    with patch("asyncio.sleep", AsyncMock(side_effect=[None, asyncio.CancelledError()])):
        try:
            await pool._keepalive_loop(mock_client)
        except asyncio.CancelledError:
            pass

    assert sem._value == initial_value
    assert dead_sender not in pool._idle_senders[2]
    assert failing_sender not in pool._idle_senders[2]
    dead_sender.disconnect.assert_awaited_once()
    failing_sender.disconnect.assert_awaited_once()
    assert dead_sender not in pool._last_active
    assert failing_sender not in pool._last_active


@pytest.mark.asyncio
async def test_autonomous_secondary_streaming_clients():
    orig_client = TelegramClientManager._client
    orig_ready = TelegramClientManager._is_ready
    orig_sec = TelegramClientManager._secondary_client
    orig_sec_ready = TelegramClientManager._is_secondary_ready
    orig_env_sec = os.environ.get("TG_SESSION_STRING_2")

    try:
        primary = MagicMock()
        primary.is_connected = MagicMock(return_value=True)
        primary.is_user_authorized = AsyncMock(return_value=True)

        secondary = MagicMock()
        secondary.is_connected = MagicMock(return_value=True)
        secondary.is_user_authorized = AsyncMock(return_value=True)

        TelegramClientManager._client = primary
        TelegramClientManager._is_ready = True
        TelegramClientManager._secondary_client = secondary
        TelegramClientManager._is_secondary_ready = True

        os.environ["TG_SESSION_STRING_2"] = ""
        clients_empty_env = await TelegramClientManager.get_active_streaming_clients()
        assert primary in clients_empty_env
        assert secondary in clients_empty_env

        os.environ["TG_SESSION_STRING_2"] = "test_mock_secondary_session_string"
        clients_populated_env = await TelegramClientManager.get_active_streaming_clients()
        assert primary in clients_populated_env
        assert secondary in clients_populated_env
    finally:
        if orig_env_sec is not None:
            os.environ["TG_SESSION_STRING_2"] = orig_env_sec
        else:
            os.environ.pop("TG_SESSION_STRING_2", None)
        TelegramClientManager._client = orig_client
        TelegramClientManager._is_ready = orig_ready
        TelegramClientManager._secondary_client = orig_sec
        TelegramClientManager._is_secondary_ready = orig_sec_ready


@pytest.mark.asyncio
async def test_get_me_info_populates_premium():
    orig_client = TelegramClientManager._client
    orig_ready = TelegramClientManager._is_ready
    orig_me = TelegramClientManager._cached_me

    try:
        client = MagicMock()
        client.is_connected = MagicMock(return_value=True)
        client.is_user_authorized = AsyncMock(return_value=True)
        user_mock = MagicMock()
        user_mock.id = 555
        user_mock.first_name = "Speed"
        user_mock.last_name = "Racer"
        user_mock.username = "speedy"
        user_mock.phone = 1234567890
        user_mock.bot = False
        user_mock.premium = True
        client.get_me = AsyncMock(return_value=user_mock)

        TelegramClientManager._client = client
        TelegramClientManager._is_ready = True
        TelegramClientManager._cached_me = None

        info = await TelegramClientManager.get_me_info()
        assert info is not None
        assert info["is_premium"] is True
        assert rate_governor.current_rate_limit_mbs == 32.0
    finally:
        TelegramClientManager._client = orig_client
        TelegramClientManager._is_ready = orig_ready
        TelegramClientManager._cached_me = orig_me
        TelegramClientManager.sync_tier_limits()
