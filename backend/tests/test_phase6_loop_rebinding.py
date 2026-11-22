import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from app.telegram_client import TelegramClientManager

@pytest.mark.asyncio
async def test_event_loop_rebinding_primary():
    # Simulate a client initialized on loop 1
    loop1 = asyncio.get_running_loop()
    mock_client1 = MagicMock()
    mock_client1.loop = loop1
    mock_client1.is_connected = MagicMock(return_value=True)

    TelegramClientManager._client = mock_client1
    TelegramClientManager._is_ready = True

    # Same loop: returns existing client
    assert TelegramClientManager._client.loop is loop1

    # Now create a different mock loop
    loop2 = MagicMock()
    mock_client2 = MagicMock()
    mock_client2.loop = loop2
    mock_client2.is_connected = MagicMock(return_value=True)

    TelegramClientManager._client = mock_client2

    # In our current loop (loop1), loop2 does not match loop1
    # Calling get_client logic must detect loop mismatch and clear stale client
    with patch("app.telegram_client.TelegramClient") as MockTelethon:
        mock_instance = MagicMock()
        mock_instance.loop = loop1
        mock_instance.is_connected = MagicMock(return_value=True)
        mock_instance.connect = AsyncMock()
        mock_instance.is_user_authorized = AsyncMock(return_value=True)
        mock_instance.get_me = AsyncMock(return_value=MagicMock(first_name="Test", last_name="User", username="testuser", premium=False))
        MockTelethon.return_value = mock_instance

        client = await TelegramClientManager.get_client()
        assert client is mock_instance
        assert client.loop is loop1
