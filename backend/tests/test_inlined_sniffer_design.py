import os
import sys
import time
import asyncio
import pytest
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telethon import errors

from app.config import SUITE_DB_PATH
from app.services.sniffer_service import SnifferService, safe_reply
from app.services.rate_governor import rate_governor


@pytest.fixture
def clean_test_sniffer(tmp_path):
    test_db = tmp_path / "test_suite.db"
    with patch("app.services.sniffer_service.SUITE_DB_PATH", test_db):
        service = SnifferService()
        yield service


def test_sqlite_deduplication_persistence(tmp_path):
    """Verifies that dispatched message deduplication persists across in-memory cache clears."""
    test_db = tmp_path / "test_suite.db"
    with patch("app.services.sniffer_service.SUITE_DB_PATH", test_db):
        service = SnifferService()

        # Initially not dispatched
        assert not service.is_dispatched(-100123456, 999)

        # Record dispatch
        service.record_dispatch(-100123456, 999, "Avatar.2.2022.1080p.mkv", 2147483648, "http://localhost:8088/dl/-100123456/999")
        assert service.is_dispatched(-100123456, 999)

        # Clear in-memory cache to simulate service restart
        service._processed_messages.clear()
        assert len(service._processed_messages) == 0

        # Must still be recognized as dispatched via SQLite WAL
        assert service.is_dispatched(-100123456, 999)
        assert ("-100123456", 999) in service._processed_messages

        # Verify recent dispatches
        recent = service.get_recent_dispatches(5)
        assert len(recent) == 1
        assert recent[0]["name"] == "Avatar.2.2022.1080p.mkv"
        assert recent[0]["chat_id"] == "-100123456"
        assert recent[0]["message_id"] == 999
        assert "2048.00 MB" in recent[0]["size"]


@pytest.mark.asyncio
async def test_dynamic_pacing_queue(tmp_path):
    """Verifies that the dispatch queue dynamically dispatches single items quickly and paces bursts."""
    test_db = tmp_path / "test_suite.db"
    with patch("app.services.sniffer_service.SUITE_DB_PATH", test_db):
        service = SnifferService()
        service.pacing_delay = 0.1  # Fast 100ms pacing for testing

        sent_links = []
        async def fake_auto_send(url):
            sent_links.append(url)
            return "fdm", True

        with patch("app.services.sniffer_service.auto_send", side_effect=fake_auto_send):
            service._ensure_dispatch_worker()

            # Enqueue item 1
            await service._dispatch_queue.put({
                "link": "http://localhost:8088/dl/1/1",
                "chat_id": 1,
                "message_id": 1,
                "fname": "ep1.mkv",
                "size": 1000000,
                "event": None,
                "res_label": "1080p"
            })

            # Enqueue item 2 immediately (burst)
            await service._dispatch_queue.put({
                "link": "http://localhost:8088/dl/1/2",
                "chat_id": 1,
                "message_id": 2,
                "fname": "ep2.mkv",
                "size": 1000000,
                "event": None,
                "res_label": "1080p"
            })

            # Wait for queue to process
            await asyncio.wait_for(service._dispatch_queue.join(), timeout=2.0)

            assert len(sent_links) == 2
            assert "http://localhost:8088/dl/1/1" in sent_links
            assert "http://localhost:8088/dl/1/2" in sent_links

            # Cleanup worker
            if service._dispatch_worker_task:
                service._dispatch_worker_task.cancel()


@pytest.mark.asyncio
async def test_safe_reply_channel_permission_fallback():
    """Verifies that safe_reply falls back to Saved Messages ('me') if channel write is forbidden."""
    mock_event = MagicMock()
    mock_event.reply = AsyncMock(side_effect=errors.ChatWriteForbiddenError(request=None))
    mock_client = MagicMock()
    mock_client.send_message = AsyncMock(return_value="sent_to_me")
    mock_event._client = mock_client

    res = await safe_reply(mock_event, "Test Message")
    assert res == "sent_to_me"
    mock_client.send_message.assert_awaited_once()
    args, kwargs = mock_client.send_message.call_args
    assert args[0] == "me"
    assert "Test Message" in args[1]


@pytest.mark.asyncio
async def test_rate_governor_reporting_and_cooldown():
    """Verifies that RateGovernor reports FloodWait and activates cooldown properly."""
    try:
        rate_governor.report_flood_wait(2, reason="Test FloodWait")
        assert rate_governor.is_cooling_down
        assert rate_governor.cooldown_seconds_remaining > 0
        assert "Test FloodWait" in rate_governor.reason
    finally:
        rate_governor._cooldown_until = 0.0
        rate_governor._cooldown_event.set()


@pytest.mark.asyncio
async def test_saved_messages_and_forward_peer_resolution(tmp_path):
    """Verifies that 'me' (Saved Messages) is resolved to user ID and forwarded messages are matched."""
    test_db = tmp_path / "test_suite.db"
    with patch("app.services.sniffer_service.SUITE_DB_PATH", test_db):
        service = SnifferService()
        service.active_channels = {"me", "@watched_channel"}

        mock_client = MagicMock()
        mock_me = MagicMock()
        mock_me.id = 777888
        mock_client.get_me = AsyncMock(return_value=mock_me)

        mock_channel = MagicMock()
        mock_channel.id = 999111
        mock_client.get_entity = AsyncMock(return_value=mock_channel)

        service._client = mock_client
        await service._resolve_watched_peers()

        assert service._my_id == 777888
        assert 777888 in service._resolved_peer_ids
        assert 999111 in service._resolved_peer_ids

