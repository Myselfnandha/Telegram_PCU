import sys
import time
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.rate_governor import RateGovernor, rate_governor
from app.telegram_client import MTProtoSenderPool, TelegramClientManager, sender_pool
from telethon import functions


@pytest.mark.asyncio
async def test_prewarm_dc_concurrency_race():
    pool = MTProtoSenderPool(max_per_dc=32)
    mock_client = MagicMock()
    mock_client.is_connected = MagicMock(return_value=True)

    created_senders = []

    async def delayed_create_sender(client, dc_id):
        await asyncio.sleep(0.02)
        s = MagicMock()
        s.is_connected.return_value = True
        created_senders.append(s)
        return s

    pool._create_sender = delayed_create_sender

    tasks = [
        asyncio.create_task(pool.prewarm_dc(mock_client, dc_id=2, target_hot_senders=2))
        for _ in range(20)
    ]
    await asyncio.gather(*tasks)

    assert len(pool._idle_senders[2]) == 2
    assert len(created_senders) == 2
    assert len(pool._prewarming_keys) == 0


@pytest.mark.asyncio
async def test_prewarm_dc_respects_max_per_dc():
    pool = MTProtoSenderPool(max_per_dc=3)
    mock_client = MagicMock()
    mock_client.is_connected = MagicMock(return_value=True)
    mock_client.session = MagicMock()
    mock_client.session.dc_id = 2

    s_active1 = MagicMock()
    s_active1.is_connected.return_value = True
    s_active2 = MagicMock()
    s_active2.is_connected.return_value = True

    pool._active_senders[2] = {s_active1, s_active2}
    pool.get_semaphore(2)._value = 1

    created_senders = []

    async def fake_create_sender(client, dc_id):
        s = MagicMock()
        s.is_connected.return_value = True
        created_senders.append(s)
        return s

    pool._create_sender = fake_create_sender

    tasks = [
        asyncio.create_task(pool.prewarm_dc(mock_client, dc_id=2, target_hot_senders=5))
        for _ in range(10)
    ]
    await asyncio.gather(*tasks)

    assert len(created_senders) == 1
    assert len(pool._idle_senders[2]) == 1
    total_senders = len(pool._active_senders[2]) + len(pool._idle_senders[2])
    assert total_senders <= pool.max_per_dc


@pytest.mark.asyncio
async def test_prewarm_dc_independent_keys():
    pool = MTProtoSenderPool(max_per_dc=32)

    primary_client = MagicMock()
    primary_client.is_connected = MagicMock(return_value=True)
    primary_client._is_secondary_client = False

    secondary_client = MagicMock()
    secondary_client.is_connected = MagicMock(return_value=True)
    secondary_client._is_secondary_client = True

    async def fake_create_sender(client, dc_id):
        s = MagicMock()
        s.is_connected.return_value = True
        return s

    pool._create_sender = fake_create_sender

    await asyncio.gather(
        pool.prewarm_dc(primary_client, dc_id=2, target_hot_senders=2),
        pool.prewarm_dc(primary_client, dc_id=4, target_hot_senders=2),
        pool.prewarm_dc(secondary_client, dc_id=2, target_hot_senders=2),
    )

    assert len(pool._idle_senders[2]) == 2
    assert len(pool._idle_senders[4]) == 2
    assert len(pool._idle_senders["sec_2"]) == 2


@pytest.mark.asyncio
async def test_prewarm_dc_error_recovery():
    pool = MTProtoSenderPool(max_per_dc=32)
    mock_client = MagicMock()
    mock_client.is_connected = MagicMock(return_value=True)

    call_count = 0

    async def failing_create_sender(client, dc_id):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionResetError("Connection dropped during handshake")
        s = MagicMock()
        s.is_connected.return_value = True
        return s

    pool._create_sender = failing_create_sender

    await pool.prewarm_dc(mock_client, dc_id=2, target_hot_senders=2)
    assert len(pool._prewarming_keys) == 0
    assert len(pool._idle_senders.get(2, [])) == 0

    await pool.prewarm_dc(mock_client, dc_id=2, target_hot_senders=2)
    assert len(pool._prewarming_keys) == 0
    assert len(pool._idle_senders[2]) == 2


@pytest.mark.asyncio
async def test_prewarm_dc_client_not_connected_failsafe():
    pool = MTProtoSenderPool(max_per_dc=32)
    mock_client = MagicMock()
    mock_client.is_connected.return_value = False
    mock_client.connect = AsyncMock(side_effect=TimeoutError("Network down"))

    await pool.prewarm_dc(mock_client, dc_id=2, target_hot_senders=2)
    assert 2 not in pool._idle_senders
    assert len(pool._prewarming_keys) == 0


@pytest.mark.asyncio
async def test_keepalive_ping_failure_disconnect_and_semaphore_integrity():
    pool = MTProtoSenderPool(max_per_dc=5)
    mock_client = MagicMock()
    mock_client.is_connected.return_value = True

    healthy_s = MagicMock()
    healthy_s.is_connected.return_value = True
    healthy_s.disconnect = AsyncMock()

    dead_s = MagicMock()
    dead_s.is_connected.return_value = False
    dead_s.disconnect = AsyncMock()

    failing_s = MagicMock()
    failing_s.is_connected.return_value = True
    failing_s.disconnect = AsyncMock()

    now = time.time()
    pool._idle_senders[2] = [healthy_s, dead_s, failing_s]
    pool._last_active[healthy_s] = now - 25.0
    pool._last_active[dead_s] = now - 25.0
    pool._last_active[failing_s] = now - 25.0

    async def mock_call(s, req):
        if s is failing_s:
            raise ConnectionResetError("Remote server closed socket")
        return MagicMock()

    mock_client._call = AsyncMock(side_effect=mock_call)

    sem = pool.get_semaphore(2)
    assert sem._value == 5

    with patch("asyncio.sleep", AsyncMock(side_effect=[None, asyncio.CancelledError()])):
        try:
            await pool._keepalive_loop(mock_client)
        except asyncio.CancelledError:
            pass

    assert sem._value == 5
    assert pool._idle_senders[2] == [healthy_s]
    assert dead_s not in pool._last_active
    assert failing_s not in pool._last_active
    assert healthy_s in pool._last_active

    dead_s.disconnect.assert_awaited_once()
    failing_s.disconnect.assert_awaited_once()
    healthy_s.disconnect.assert_not_called()

    acquired = []
    pool._create_sender = AsyncMock(return_value=MagicMock(is_connected=MagicMock(return_value=True)))
    for _ in range(5):
        s = await pool.acquire(mock_client, 2, priority=True)
        acquired.append(s)

    assert sem._value == 0

    acquire_blocked = False
    try:
        await asyncio.wait_for(pool.acquire(mock_client, 2, priority=True), timeout=0.05)
    except asyncio.TimeoutError:
        acquire_blocked = True
    assert acquire_blocked is True

    for s in acquired:
        await pool.release(mock_client, 2, s, healthy=True, priority=True)

    assert sem._value == 5


@pytest.mark.asyncio
async def test_keepalive_secondary_client_routing():
    pool = MTProtoSenderPool(max_per_dc=4)
    sec_client = MagicMock()
    sec_client.is_connected.return_value = True
    sec_client._call = AsyncMock(return_value=MagicMock())

    orig_sec = TelegramClientManager._secondary_client
    try:
        TelegramClientManager._secondary_client = sec_client

        sec_sender = MagicMock()
        sec_sender.is_connected.return_value = True
        sec_sender.disconnect = AsyncMock()

        pool._idle_senders["sec_4"] = [sec_sender]
        pool._last_active[sec_sender] = time.time() - 25.0

        with patch("asyncio.sleep", AsyncMock(side_effect=[None, asyncio.CancelledError()])):
            try:
                await pool._keepalive_loop(MagicMock())
            except asyncio.CancelledError:
                pass

        sec_client._call.assert_awaited_once()
        assert pool._idle_senders["sec_4"] == [sec_sender]
    finally:
        TelegramClientManager._secondary_client = orig_sec


@pytest.mark.asyncio
async def test_token_bucket_burst_premium_32mbs():
    gov = RateGovernor()
    gov.configure_for_tier(is_premium=True, secondary_premium=None)

    assert gov.current_rate_limit_mbs == 32.0
    assert gov._rate == 32.0
    assert gov._capacity == 16.0

    await asyncio.sleep(0.55)
    await gov.acquire_chunk_slot(chunk_size_bytes=0)
    assert gov._tokens == pytest.approx(16.0, abs=0.2)

    t_start = asyncio.get_event_loop().time()
    for _ in range(16):
        await gov.acquire_chunk_slot(chunk_size_bytes=1024 * 1024)
    t_burst = asyncio.get_event_loop().time() - t_start

    assert t_burst < 0.05
    assert gov._tokens == pytest.approx(0.0, abs=0.2)

    t_pacing_start = asyncio.get_event_loop().time()
    await gov.acquire_chunk_slot(chunk_size_bytes=1024 * 1024)
    t_pacing_elapsed = asyncio.get_event_loop().time() - t_pacing_start

    assert 0.025 <= t_pacing_elapsed <= 0.065


@pytest.mark.asyncio
async def test_token_bucket_standard_tier_ceiling():
    gov = RateGovernor()
    gov.configure_for_tier(is_premium=False, secondary_premium=None)

    assert gov.current_rate_limit_mbs == 4.5
    assert gov._rate == 4.5
    assert gov._min_rate == 1.8
    assert gov._max_rate == 6.5
    assert gov._capacity == 1.5

    await asyncio.sleep(0.40)
    await gov.acquire_chunk_slot(chunk_size_bytes=0)
    assert gov._tokens == pytest.approx(1.5, abs=0.2)

    t_start = asyncio.get_event_loop().time()
    await gov.acquire_chunk_slot(chunk_size_bytes=1024 * 1024)
    t_first = asyncio.get_event_loop().time() - t_start
    assert t_first < 0.05

    t_second_start = asyncio.get_event_loop().time()
    await gov.acquire_chunk_slot(chunk_size_bytes=1024 * 1024)
    t_second = asyncio.get_event_loop().time() - t_second_start
    assert 0.20 <= t_second <= 0.26


def test_sync_tier_limits_matrix():
    orig_client = TelegramClientManager._client
    orig_ready = TelegramClientManager._is_ready
    orig_me = TelegramClientManager._cached_me
    orig_sec = TelegramClientManager._secondary_client
    orig_sec_ready = TelegramClientManager._is_secondary_ready
    orig_sec_me = TelegramClientManager._cached_secondary_me

    cases = [
        (True, True, True, True, 48.0),
        (True, False, True, False, 9.0),
        (True, True, True, False, 28.5),
        (True, False, True, True, 28.5),
        (True, True, False, False, 32.0),
        (True, False, False, False, 4.5),
        (False, False, True, True, 32.0),
        (False, False, True, False, 4.5),
        (False, False, False, False, 4.5),
    ]

    try:
        for p_active, p_prem, s_active, s_prem, expected_rate in cases:
            TelegramClientManager._client = MagicMock() if p_active else None
            TelegramClientManager._is_ready = p_active
            TelegramClientManager._cached_me = {"is_premium": p_prem} if p_active else None

            TelegramClientManager._secondary_client = MagicMock() if s_active else None
            TelegramClientManager._is_secondary_ready = s_active
            TelegramClientManager._cached_secondary_me = {"is_premium": s_prem} if s_active else None

            TelegramClientManager.sync_tier_limits()
            assert rate_governor.current_rate_limit_mbs == expected_rate
            assert sender_pool.max_per_dc == 32
    finally:
        TelegramClientManager._client = orig_client
        TelegramClientManager._is_ready = orig_ready
        TelegramClientManager._cached_me = orig_me
        TelegramClientManager._secondary_client = orig_sec
        TelegramClientManager._is_secondary_ready = orig_sec_ready
        TelegramClientManager._cached_secondary_me = orig_sec_me
        TelegramClientManager.sync_tier_limits()


@pytest.mark.asyncio
async def test_semaphore_invariants_across_pool_lifecycle():
    pool = MTProtoSenderPool(max_per_dc=4)
    client = MagicMock()
    client.is_connected.return_value = True
    client.session = MagicMock()
    client.session.dc_id = 2

    created = []
    async def fake_create_sender(c, dc):
        s = MagicMock()
        s.is_connected.return_value = True
        s.disconnect = AsyncMock()
        created.append(s)
        return s

    pool._create_sender = fake_create_sender

    sem = pool.get_semaphore(2)
    assert sem._value == 4
    assert len(pool._active_senders.get(2, set())) == 0

    await pool.prewarm_dc(client, dc_id=2, target_hot_senders=2)
    assert len(pool._idle_senders[2]) == 2
    assert sem._value == 4

    s1 = await pool.acquire(client, 2, priority=True)
    assert sem._value == 3
    assert len(pool._active_senders[2]) == 1
    assert len(pool._idle_senders[2]) == 1

    s2 = await pool.acquire(client, 2, priority=True)
    assert sem._value == 2
    assert len(pool._active_senders[2]) == 2
    assert len(pool._idle_senders[2]) == 0

    s3 = await pool.acquire(client, 2, priority=True)
    assert sem._value == 1
    assert len(pool._active_senders[2]) == 3

    await pool.release(client, 2, s1, healthy=True, priority=True)
    assert sem._value == 2
    assert len(pool._active_senders[2]) == 2
    assert len(pool._idle_senders[2]) == 1

    await pool.release(client, 2, s2, healthy=False, priority=True)
    assert sem._value == 3
    assert len(pool._active_senders[2]) == 1
    assert len(pool._idle_senders[2]) == 1

    await pool.release(client, 2, s3, healthy=True, priority=True)
    assert sem._value == 4
    assert len(pool._active_senders[2]) == 0
    assert len(pool._idle_senders[2]) == 2

    assert sem._value + len(pool._active_senders[2]) == pool.max_per_dc


@pytest.mark.asyncio
async def test_multi_account_flood_isolation_and_aimd():
    gov = RateGovernor()
    gov.configure_for_tier(is_premium=True, secondary_premium=False)
    assert gov.current_rate_limit_mbs == 28.5
    assert gov._rate == 28.5
    assert gov._capacity == 7.5

    client1 = MagicMock()
    client2 = MagicMock()
    pool = [client1, client2]

    gov.report_flood_wait(seconds=5, reason="DC flood", client=client1, all_clients_in_pool=pool)
    assert gov.is_client_cooling_down(client1) is True
    assert gov.is_client_cooling_down(client2) is False
    assert gov.is_cooling_down is False


@pytest.mark.asyncio
async def test_prewarm_dc_massive_parallel_multi_dc_stress():
    pool = MTProtoSenderPool(max_per_dc=8)
    primary = MagicMock()
    primary.is_connected.return_value = True
    primary._is_secondary_client = False

    secondary = MagicMock()
    secondary.is_connected.return_value = True
    secondary._is_secondary_client = True

    created_per_dc = {}
    lock = asyncio.Lock()

    async def fake_create_sender(c, dc):
        key = pool._get_key(c, dc)
        await asyncio.sleep(0.005)
        s = MagicMock()
        s.is_connected.return_value = True
        s.disconnect = AsyncMock()
        async with lock:
            created_per_dc.setdefault(key, []).append(s)
        return s

    pool._create_sender = fake_create_sender

    tasks = []
    for _ in range(5):
        for dc in [1, 2, 4]:
            tasks.append(pool.prewarm_dc(primary, dc, target_hot_senders=2))
            tasks.append(pool.prewarm_dc(secondary, dc, target_hot_senders=2))

    await asyncio.gather(*tasks)

    for dc in [1, 2, 4]:
        assert len(pool._idle_senders[dc]) == 2
        assert len(created_per_dc[dc]) == 2
        assert len(pool._idle_senders[f"sec_{dc}"]) == 2
        assert len(created_per_dc[f"sec_{dc}"]) == 2

    assert len(pool._prewarming_keys) == 0


@pytest.mark.asyncio
async def test_keepalive_timeout_handling():
    pool = MTProtoSenderPool(max_per_dc=4)
    client = MagicMock()
    client.is_connected.return_value = True

    timeout_sender = MagicMock()
    timeout_sender.is_connected.return_value = True
    timeout_sender.disconnect = AsyncMock()

    pool._idle_senders[2] = [timeout_sender]
    pool._last_active[timeout_sender] = time.time() - 30.0

    async def timeout_call(s, req):
        raise asyncio.TimeoutError()

    client._call = AsyncMock(side_effect=timeout_call)

    with patch("asyncio.sleep", AsyncMock(side_effect=[None, asyncio.CancelledError()])):
        try:
            await pool._keepalive_loop(client)
        except asyncio.CancelledError:
            pass

    assert timeout_sender not in pool._idle_senders[2]
    assert timeout_sender not in pool._last_active
    timeout_sender.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_keepalive_no_senders_clean_pass():
    pool = MTProtoSenderPool(max_per_dc=4)
    client = MagicMock()
    client.is_connected.return_value = True

    with patch("asyncio.sleep", AsyncMock(side_effect=[None, asyncio.CancelledError()])):
        try:
            await pool._keepalive_loop(client)
        except asyncio.CancelledError:
            pass

    assert len(pool._idle_senders) == 0

