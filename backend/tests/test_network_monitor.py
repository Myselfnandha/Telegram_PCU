import os
import sys
from pathlib import Path
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.network_monitor import NetworkMonitor

@pytest.mark.asyncio
async def test_network_monitor_lifecycle():
    monitor = NetworkMonitor()
    loop = asyncio.get_running_loop()
    monitor.start(loop=loop)
    assert monitor._running is True
    monitor.stop()
    assert monitor._running is False

@pytest.mark.asyncio
async def test_network_monitor_transition_to_offline():
    monitor = NetworkMonitor()
    monitor._loop = asyncio.get_running_loop()
    monitor._running = True

    with patch("app.services.queue_manager.queue_manager.pause_all_tasks") as mock_pause, \
         patch("app.telegram_client.sender_pool.disconnect_all", new_callable=AsyncMock) as mock_disconnect, \
         patch("app.socket_handlers.sio.emit", new_callable=AsyncMock) as mock_sio:

        await monitor._handle_state_transition(False)

        assert monitor.is_online is False
        mock_pause.assert_called_once()
        mock_disconnect.assert_awaited_once()
        mock_sio.assert_awaited_once_with(
            "network:status",
            {"online": False, "reason": "carrier_down", "ip": "127.0.0.1"}
        )

@pytest.mark.asyncio
async def test_network_monitor_transition_to_online():
    monitor = NetworkMonitor()
    monitor._loop = asyncio.get_running_loop()
    monitor._running = True
    monitor._is_online = False

    with patch("app.services.queue_manager.queue_manager.resume_all_tasks") as mock_resume, \
         patch("app.telegram_client.TelegramClientManager.ensure_connected", new_callable=AsyncMock) as mock_ensure, \
         patch("app.telegram_client.TelegramClientManager.is_secondary_authorized", new_callable=AsyncMock, return_value=False), \
         patch("app.services.network_service.NetworkService.get_local_ip", return_value="192.168.1.150"), \
         patch("app.socket_handlers.sio.emit", new_callable=AsyncMock) as mock_sio:

        await monitor._handle_state_transition(True)

        assert monitor.is_online is True
        mock_ensure.assert_awaited_once_with(timeout=10.0)
        mock_resume.assert_called_once()
        mock_sio.assert_awaited_once_with(
            "network:status",
            {"online": True, "reason": "reconnected", "ip": "192.168.1.150"}
        )
