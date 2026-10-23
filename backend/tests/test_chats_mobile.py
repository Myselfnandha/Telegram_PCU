import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone
import httpx
from app.main import app

@pytest.mark.asyncio
async def test_chat_messages_unauthorized():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        with patch("app.telegram_client.TelegramClientManager.get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.is_user_authorized = AsyncMock(return_value=False)
            mock_get_client.return_value = mock_client

            res = await client.get("/api/chats/12345/messages")
            assert res.status_code == 401
            assert "not authorized" in res.json()["detail"]


@pytest.mark.asyncio
async def test_chat_messages_mocked_feed():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        with patch("app.telegram_client.TelegramClientManager.get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.is_user_authorized = AsyncMock(return_value=True)

            # Mock messages
            mock_msg_text = MagicMock()
            mock_msg_text.id = 101
            mock_msg_text.date = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
            mock_msg_text.message = "Hello from Mobile Telegram!"
            mock_msg_text.out = False
            mock_msg_text.media = None
            mock_msg_text.sender = MagicMock(first_name="Alice", last_name="Smith", title=None, username="alice")
            mock_msg_text.views = 42
            mock_msg_text.forwards = 2

            mock_msg_video = MagicMock()
            mock_msg_video.id = 102
            mock_msg_video.date = datetime(2026, 10, 22, 12, 5, 0, tzinfo=timezone.utc)
            mock_msg_video.message = "Check out this movie!"
            mock_msg_video.out = True
            mock_msg_video.media = MagicMock()
            mock_msg_video.video = True
            mock_file = MagicMock()
            mock_file.name = "Inception.2010.1080p.mkv"
            mock_file.size = 1500000000
            mock_file.duration = 8880
            mock_file.mime_type = "video/x-matroska"
            mock_msg_video.file = mock_file
            mock_msg_video.sender = None
            mock_msg_video.views = None
            mock_msg_video.forwards = None

            mock_client.get_entity = AsyncMock(return_value="mock_entity")
            mock_client.get_messages = AsyncMock(return_value=[mock_msg_video, mock_msg_text])
            mock_get_client.return_value = mock_client

            res = await client.get("/api/chats/123456789/messages?limit=20")
            assert res.status_code == 200
            data = res.json()
            assert data["chat_id"] == "123456789"
            assert data["count"] == 2
            msgs = data["messages"]
            assert msgs[0]["id"] == 102
            assert msgs[0]["out"] is True
            assert msgs[0]["sender_name"] == "You"
            assert msgs[0]["media"]["type"] == "video"
            assert msgs[0]["media"]["file_name"] == "Inception.2010.1080p.mkv"
            assert "/stream/123456789/102/Inception.2010.1080p.mkv" in msgs[0]["media"]["stream_url"]

            assert msgs[1]["id"] == 101
            assert msgs[1]["text"] == "Hello from Mobile Telegram!"
            assert msgs[1]["sender_name"] == "Alice Smith"
            assert msgs[1]["out"] is False


@pytest.mark.asyncio
async def test_send_chat_message_validation():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        with patch("app.telegram_client.TelegramClientManager.get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.is_user_authorized = AsyncMock(return_value=True)
            mock_get_client.return_value = mock_client

            # Empty text error
            res = await client.post("/api/chats/123456/send", json={"text": "   "})
            assert res.status_code == 400
            assert "cannot be empty" in res.json()["detail"]


@pytest.mark.asyncio
async def test_send_chat_message_success():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        with patch("app.telegram_client.TelegramClientManager.get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.is_user_authorized = AsyncMock(return_value=True)
            mock_sent_msg = MagicMock(id=999)
            mock_client.send_message = AsyncMock(return_value=mock_sent_msg)
            mock_get_client.return_value = mock_client

            res = await client.post("/api/chats/123456/send", json={"text": "Hello world!"})
            assert res.status_code == 200
            data = res.json()
            assert data["success"] is True
            assert data["message_id"] == 999
            mock_client.send_message.assert_awaited_once_with(123456, "Hello world!")
