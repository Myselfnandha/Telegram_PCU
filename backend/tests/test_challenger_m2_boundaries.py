import sys
import time
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.rate_governor import RateGovernor, rate_governor
from app.telegram_client import MTProtoSenderPool, TelegramClientManager, sender_pool
from telethon import functions, errors


@pytest.fixture(autouse=True)
def preserve_client_manager_state():
    orig_client = TelegramClientManager._client
    orig_ready = TelegramClientManager._is_ready
    orig_me = TelegramClientManager._cached_me
    orig_sec_client = TelegramClientManager._secondary_client
    orig_sec_ready = TelegramClientManager._is_secondary_ready
    orig_sec_me = TelegramClientManager._cached_secondary_me

    yield

    TelegramClientManager._client = orig_client
    TelegramClientManager._is_ready = orig_ready
    TelegramClientManager._cached_me = orig_me
    TelegramClientManager._secondary_client = orig_sec_client
    TelegramClientManager._is_secondary_ready = orig_sec_ready
    TelegramClientManager._cached_secondary_me = orig_sec_me
    TelegramClientManager.sync_tier_limits()


@pytest.mark.parametrize(
    "p_active,p_prem,s_active,s_prem,expected_rate,expected_min,expected_max,expected_cap",
    [
        (False, False, False, False, 4.5, 1.8, 6.5, 1.5),
        (True, False, False, False, 4.5, 1.8, 6.5, 1.5),
        (True, True, False, False, 32.0, 16.0, 36.0, 16.0),
        (True, False, True, False, 9.0, 3.6, 13.0, 3.0),
        (True, True, True, False, 28.5, 13.8, 38.5, 7.5),
        (True, True, True, True, 48.0, 24.0, 64.0, 12.0),
        (True, False, True, True, 28.5, 13.8, 38.5, 7.5),
        (False, False, True, False, 4.5, 1.8, 6.5, 1.5),
        (False, False, True, True, 32.0, 16.0, 36.0, 16.0),
    ],
)
def test_sync_tier_limits_all_combinations(
    p_active, p_prem, s_active, s_prem, expected_rate, expected_min, expected_max, expected_cap
):
    TelegramClientManager._client = MagicMock() if p_active else None
    TelegramClientManager._is_ready = p_active
    TelegramClientManager._cached_me = {"id": 1, "is_premium": p_prem} if p_active else None

    TelegramClientManager._secondary_client = MagicMock() if s_active else None
    TelegramClientManager._is_secondary_ready = s_active
    TelegramClientManager._cached_secondary_me = {"id": 2, "is_premium": s_prem} if s_active else None

    TelegramClientManager.sync_tier_limits()

    assert rate_governor.current_rate_limit_mbs == expected_rate
    assert rate_governor._min_rate == expected_min
    assert rate_governor._max_rate == expected_max
    assert rate_governor._capacity == expected_cap
    assert sender_pool.max_per_dc == 32


@pytest.mark.asyncio
async def test_get_me_info_populates_premium_reliable_true():
    mock_client = MagicMock()
    mock_client.is_connected = MagicMock(return_value=True)
    mock_client.is_user_authorized = AsyncMock(return_value=True)

    user = MagicMock()
    user.id = 1001
    user.first_name = "Alice"
    user.last_name = "Smith"
    user.username = "alicesmith"
    user.phone = 9876543210
    user.bot = False
    user.premium = True
    mock_client.get_me = AsyncMock(return_value=user)

    TelegramClientManager._client = mock_client
    TelegramClientManager._is_ready = True
    TelegramClientManager._cached_me = None

    info = await TelegramClientManager.get_me_info()
    assert info is not None
    assert info["is_premium"] is True
    assert info["id"] == 1001
    assert rate_governor.current_rate_limit_mbs == 32.0


@pytest.mark.asyncio
async def test_get_me_info_populates_premium_reliable_false():
    mock_client = MagicMock()
    mock_client.is_connected = MagicMock(return_value=True)
    mock_client.is_user_authorized = AsyncMock(return_value=True)

    user = MagicMock()
    user.id = 1002
    user.first_name = "Bob"
    user.last_name = None
    user.username = "bobbystandard"
    user.phone = 9876543211
    user.bot = False
    user.premium = False
    mock_client.get_me = AsyncMock(return_value=user)

    TelegramClientManager._client = mock_client
    TelegramClientManager._is_ready = True
    TelegramClientManager._cached_me = None

    info = await TelegramClientManager.get_me_info()
    assert info is not None
    assert info["is_premium"] is False
    assert rate_governor.current_rate_limit_mbs == 4.5


@pytest.mark.asyncio
async def test_get_me_info_missing_premium_attribute_graceful():
    mock_client = MagicMock()
    mock_client.is_connected = MagicMock(return_value=True)
    mock_client.is_user_authorized = AsyncMock(return_value=True)

    user = MagicMock(spec=["id", "first_name", "last_name", "username", "phone", "bot"])
    user.id = 1003
    user.first_name = "Charlie"
    user.last_name = None
    user.username = "charlie"
    user.phone = 9876543212
    user.bot = False
    mock_client.get_me = AsyncMock(return_value=user)

    TelegramClientManager._client = mock_client
    TelegramClientManager._is_ready = True
    TelegramClientManager._cached_me = None

    info = await TelegramClientManager.get_me_info()
    assert info is not None
    assert info["is_premium"] is False
    assert rate_governor.current_rate_limit_mbs == 4.5


@pytest.mark.asyncio
async def test_get_me_info_unauthorized_returns_none():
    mock_client = MagicMock()
    mock_client.is_connected = MagicMock(return_value=True)
    mock_client.is_user_authorized = AsyncMock(return_value=False)

    TelegramClientManager._client = mock_client
    TelegramClientManager._is_ready = False
    TelegramClientManager._cached_me = None

    with patch.object(TelegramClientManager, "get_client", AsyncMock(return_value=mock_client)):
        info = await TelegramClientManager.get_me_info()
        assert info is None


@pytest.mark.asyncio
async def test_get_me_info_refreshes_when_is_premium_key_missing_from_cache():
    mock_client = MagicMock()
    mock_client.is_connected = MagicMock(return_value=True)
    mock_client.is_user_authorized = AsyncMock(return_value=True)

    user = MagicMock()
    user.id = 1004
    user.first_name = "David"
    user.last_name = None
    user.username = "david"
    user.phone = 9876543213
    user.bot = False
    user.premium = True
    mock_client.get_me = AsyncMock(return_value=user)

    TelegramClientManager._client = mock_client
    TelegramClientManager._is_ready = True
    TelegramClientManager._cached_me = {"id": 1004, "phone": "+123******4567"}

    with patch.object(TelegramClientManager, "get_client", AsyncMock(return_value=mock_client)):
        info = await TelegramClientManager.get_me_info()
        assert "is_premium" in info
        assert info["is_premium"] is True


@pytest.mark.asyncio
async def test_get_secondary_me_info_populates_premium():
    mock_sec_client = MagicMock()
    mock_sec_client.is_connected = MagicMock(return_value=True)
    mock_sec_client.is_user_authorized = AsyncMock(return_value=True)

    sec_user = MagicMock()
    sec_user.id = 2001
    sec_user.first_name = "SecUser"
    sec_user.last_name = None
    sec_user.username = "secuser"
    sec_user.phone = 5551234567
    sec_user.premium = True
    mock_sec_client.get_me = AsyncMock(return_value=sec_user)

    TelegramClientManager._secondary_client = mock_sec_client
    TelegramClientManager._is_secondary_ready = True
    TelegramClientManager._cached_secondary_me = None

    with patch.object(TelegramClientManager, "get_secondary_client", AsyncMock(return_value=mock_sec_client)):
        sec_info = await TelegramClientManager.get_secondary_me_info()
        assert sec_info is not None
        assert sec_info["is_premium"] is True


@pytest.mark.asyncio
async def test_get_active_streaming_clients_secondary_ready_vs_not_ready():
    primary_client = MagicMock()
    primary_client.is_connected = MagicMock(return_value=True)
    primary_client.is_user_authorized = AsyncMock(return_value=True)

    secondary_client = MagicMock()
    secondary_client.is_connected = MagicMock(return_value=True)
    secondary_client.is_user_authorized = AsyncMock(return_value=True)

    with patch.object(TelegramClientManager, "get_client", AsyncMock(return_value=primary_client)), \
         patch.object(TelegramClientManager, "is_authorized", AsyncMock(return_value=True)):

        with patch.object(TelegramClientManager, "get_secondary_client", AsyncMock(return_value=secondary_client)):
            TelegramClientManager._is_secondary_ready = True
            clients = await TelegramClientManager.get_active_streaming_clients()
            assert clients == [primary_client, secondary_client]

            TelegramClientManager._is_secondary_ready = False
            clients_sec_unready = await TelegramClientManager.get_active_streaming_clients()
            assert clients_sec_unready == [primary_client]

        with patch.object(TelegramClientManager, "get_secondary_client", AsyncMock(return_value=None)):
            TelegramClientManager._is_secondary_ready = False
            clients_no_sec = await TelegramClientManager.get_active_streaming_clients()
            assert clients_no_sec == [primary_client]

        with patch.object(TelegramClientManager, "get_secondary_client", AsyncMock(side_effect=RuntimeError("Secondary network failure"))):
            clients_sec_err = await TelegramClientManager.get_active_streaming_clients()
            assert clients_sec_err == [primary_client]

    with patch.object(TelegramClientManager, "get_client", AsyncMock(return_value=primary_client)), \
         patch.object(TelegramClientManager, "is_authorized", AsyncMock(return_value=False)), \
         patch.object(TelegramClientManager, "get_secondary_client", AsyncMock(return_value=secondary_client)):
        TelegramClientManager._is_secondary_ready = True
        clients_sec_only = await TelegramClientManager.get_active_streaming_clients()
        assert clients_sec_only == [secondary_client]


@pytest.mark.asyncio
async def test_dynamic_lifecycle_transitions():
    TelegramClientManager._client = MagicMock()
    TelegramClientManager._is_ready = True
    TelegramClientManager._cached_me = {"id": 1, "is_premium": False}
    TelegramClientManager._secondary_client = None
    TelegramClientManager._is_secondary_ready = False
    TelegramClientManager._cached_secondary_me = None
    TelegramClientManager.sync_tier_limits()
    assert rate_governor.current_rate_limit_mbs == 4.5

    TelegramClientManager._secondary_client = MagicMock()
    TelegramClientManager._is_secondary_ready = True
    TelegramClientManager._cached_secondary_me = {"id": 2, "is_premium": True}
    TelegramClientManager.sync_tier_limits()
    assert rate_governor.current_rate_limit_mbs == 28.5

    TelegramClientManager._cached_me = {"id": 1, "is_premium": True}
    TelegramClientManager.sync_tier_limits()
    assert rate_governor.current_rate_limit_mbs == 48.0

    TelegramClientManager._secondary_client = None
    TelegramClientManager._is_secondary_ready = False
    TelegramClientManager._cached_secondary_me = None
    TelegramClientManager.sync_tier_limits()
    assert rate_governor.current_rate_limit_mbs == 32.0

    TelegramClientManager._client = None
    TelegramClientManager._is_ready = False
    TelegramClientManager._cached_me = None
    TelegramClientManager.sync_tier_limits()
    assert rate_governor.current_rate_limit_mbs == 4.5


@pytest.mark.asyncio
async def test_concurrent_prewarm_dc_no_duplicate_senders():
    pool = MTProtoSenderPool(max_per_dc=32)
    mock_client = MagicMock()
    mock_client.is_connected = MagicMock(return_value=True)

    create_count = 0
    async def delayed_create_sender(client, dc_id):
        nonlocal create_count
        create_count += 1
        await asyncio.sleep(0.02)
        s = MagicMock()
        s.is_connected.return_value = True
        return s

    pool._create_sender = delayed_create_sender

    await asyncio.gather(
        pool.prewarm_dc(mock_client, dc_id=2, target_hot_senders=2),
        pool.prewarm_dc(mock_client, dc_id=2, target_hot_senders=2),
        pool.prewarm_dc(mock_client, dc_id=2, target_hot_senders=2),
    )

    assert len(pool._idle_senders[2]) == 2
    assert create_count == 2


@pytest.mark.asyncio
async def test_keepalive_loop_routes_correct_clients():
    pool = MTProtoSenderPool(max_per_dc=8)
    primary_client = MagicMock()
    secondary_client = MagicMock()

    TelegramClientManager._client = primary_client
    TelegramClientManager._secondary_client = secondary_client

    primary_sender = MagicMock()
    primary_sender.is_connected.return_value = True
    secondary_sender = MagicMock()
    secondary_sender.is_connected.return_value = True

    pool._idle_senders[2] = [primary_sender]
    pool._idle_senders["sec_2"] = [secondary_sender]
    pool._last_active[primary_sender] = time.time() - 25.0
    pool._last_active[secondary_sender] = time.time() - 25.0

    primary_client._call = AsyncMock(return_value=MagicMock())
    secondary_client._call = AsyncMock(return_value=MagicMock())

    with patch("asyncio.sleep", AsyncMock(side_effect=[None, asyncio.CancelledError()])):
        try:
            await pool._keepalive_loop(primary_client)
        except asyncio.CancelledError:
            pass

    primary_client._call.assert_awaited_once()
    secondary_client._call.assert_awaited_once()
    assert primary_client._call.call_args[0][0] == primary_sender
    assert secondary_client._call.call_args[0][0] == secondary_sender


@pytest.mark.asyncio
async def test_logout_and_logout_secondary_tier_sync():
    p_client = MagicMock()
    p_client.is_connected = MagicMock(return_value=True)
    p_client.log_out = AsyncMock()
    p_client.disconnect = AsyncMock()

    s_client = MagicMock()
    s_client.is_connected = MagicMock(return_value=True)
    s_client.log_out = AsyncMock()
    s_client.disconnect = AsyncMock()

    TelegramClientManager._client = p_client
    TelegramClientManager._is_ready = True
    TelegramClientManager._cached_me = {"id": 10, "is_premium": True}

    TelegramClientManager._secondary_client = s_client
    TelegramClientManager._is_secondary_ready = True
    TelegramClientManager._cached_secondary_me = {"id": 20, "is_premium": True}

    TelegramClientManager.sync_tier_limits()
    assert rate_governor.current_rate_limit_mbs == 48.0

    with patch("app.telegram_client.update_session_string_in_env"), \
         patch("app.telegram_client.update_session_string_2_in_env"):
        await TelegramClientManager.logout_secondary()
        assert TelegramClientManager._secondary_client is None
        assert TelegramClientManager._is_secondary_ready is False
        assert rate_governor.current_rate_limit_mbs == 32.0

        await TelegramClientManager.logout()
        assert TelegramClientManager._client is None
        assert TelegramClientManager._is_ready is False
        assert rate_governor.current_rate_limit_mbs == 4.5
