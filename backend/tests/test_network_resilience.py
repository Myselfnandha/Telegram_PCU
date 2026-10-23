import os
import sys
from pathlib import Path
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.telegram_client import TelegramClientManager, MTProtoSenderPool
from app.services.stream_engine import StreamEngine


@pytest.mark.asyncio
async def test_client_manager_preserves_auth_on_network_failure():
    """Verify TelegramClientManager does not de-authorize user if connect() fails temporarily."""
    TelegramClientManager._is_ready = True
    TelegramClientManager._cached_me = {"id": 12345, "name": "Test User"}

    mock_client = MagicMock()
    mock_client.is_connected.return_value = False
    mock_client.connect = AsyncMock(side_effect=OSError("Network is unreachable"))
    TelegramClientManager._client = mock_client

    # Calling get_client should NOT wipe _is_ready
    client = await TelegramClientManager.get_client()
    assert TelegramClientManager._is_ready is True
    assert await TelegramClientManager.is_authorized() is True


@pytest.mark.asyncio
async def test_sender_pool_purge_dc():
    """Verify purge_dc cleanly disconnects and drops all idle and active senders for a DC."""
    pool = MTProtoSenderPool(max_per_dc=4)

    s1 = MagicMock()
    s1.disconnect = AsyncMock()
    s2 = MagicMock()
    s2.disconnect = AsyncMock()

    pool._idle_senders[4] = [s1]
    pool._active_senders[4] = {s2}

    await pool.purge_dc(4)

    s1.disconnect.assert_awaited_once()
    s2.disconnect.assert_awaited_once()
    assert 4 not in pool._idle_senders
    assert 4 not in pool._active_senders


@pytest.mark.asyncio
async def test_sender_pool_independent_foreign_leases():
    """Verify that multiple concurrent leases do not collide on the same borrowed sender instance."""
    pool = MTProtoSenderPool(max_per_dc=4)
    mock_client = MagicMock()
    mock_client.session.dc_id = 5

    b_sender = MagicMock()
    b_sender.is_connected.return_value = True
    mock_client._borrowed_senders = {4: [1, b_sender]}

    # First lease gets b_sender
    lease1 = await pool.acquire(mock_client, dc_id=4)
    assert lease1 == b_sender
    assert b_sender in pool._active_senders[4]

    # Second concurrent lease must NOT reuse b_sender while it is active; should create a new sender
    mock_created = MagicMock()
    mock_created.is_connected.return_value = True
    mock_created.dc_id = 4
    mock_created._is_independent = True
    pool._create_sender = AsyncMock(return_value=mock_created)

    lease2 = await pool.acquire(mock_client, dc_id=4)
    assert lease2 != lease1
    assert lease2 == mock_created
