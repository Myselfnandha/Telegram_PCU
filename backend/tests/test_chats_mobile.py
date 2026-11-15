import sys
import asyncio
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


@pytest.mark.asyncio
async def test_list_chats_telegram_ordering_and_contact_separation():
    from telethon.tl.types import User as TgUser, Channel as TgChannel
    from app.routes.chats import clear_chats_cache

    clear_chats_cache()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        with patch("app.telegram_client.TelegramClientManager.get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.is_user_authorized = AsyncMock(return_value=True)

            me_mock = MagicMock(spec=TgUser)
            me_mock.id = 6161427514
            me_mock.username = "myself_nandha"
            mock_client.get_me = AsyncMock(return_value=me_mock)

            dialog_pinned = MagicMock()
            dialog_pinned.entity = MagicMock(spec=TgChannel, id=2001, username="pinned_chan", photo=None)
            dialog_pinned.id = 2001
            dialog_pinned.name = "Pinned Channel"
            dialog_pinned.pinned = True
            dialog_pinned.unread_count = 0
            dialog_pinned.is_channel = True
            dialog_pinned.is_group = False
            dialog_pinned.date = datetime(2026, 11, 10, 10, 0, 0, tzinfo=timezone.utc)

            dialog_recent = MagicMock()
            dialog_recent.entity = MagicMock(spec=TgUser, id=3001, username="recent_friend", bot=False, photo=None)
            dialog_recent.id = 3001
            dialog_recent.name = "Recent Friend"
            dialog_recent.pinned = False
            dialog_recent.unread_count = 2
            dialog_recent.date = datetime(2026, 11, 12, 18, 0, 0, tzinfo=timezone.utc)

            dialog_older = MagicMock()
            dialog_older.entity = MagicMock(spec=TgChannel, id=2002, username="older_chan", photo=None)
            dialog_older.id = 2002
            dialog_older.name = "Older Channel"
            dialog_older.pinned = False
            dialog_older.unread_count = 0
            dialog_older.is_channel = True
            dialog_older.is_group = False
            dialog_older.date = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)

            dialog_me_dup = MagicMock()
            dialog_me_dup.entity = MagicMock(spec=TgUser, id=6161427514, username="myself_nandha", bot=False, photo=None)
            dialog_me_dup.id = 6161427514
            dialog_me_dup.name = "Nandha"
            dialog_me_dup.pinned = False
            dialog_me_dup.unread_count = 0
            dialog_me_dup.date = datetime(2026, 11, 12, 20, 0, 0, tzinfo=timezone.utc)

            mock_client.get_dialogs = AsyncMock(side_effect=[
                [dialog_older, dialog_pinned, dialog_me_dup, dialog_recent],
                []
            ])

            contact_user = MagicMock(spec=TgUser)
            contact_user.id = 4001
            contact_user.first_name = "Phone"
            contact_user.last_name = "Contact"
            contact_user.username = None
            contact_user.bot = False
            contact_user.photo = None

            contacts_res_mock = MagicMock()
            contacts_res_mock.users = [contact_user]
            mock_client.return_value = contacts_res_mock
            mock_get_client.return_value = mock_client

            res = await client.get("/api/chats?force_refresh=true")
            assert res.status_code == 200
            data = res.json()

            assert data[0]["type"] == "saved_messages"
            assert data[0]["id"] == 6161427514
            assert data[0]["pinned"] is True

            me_occurrences = [c for c in data if c["id"] == 6161427514]
            assert len(me_occurrences) == 1

            assert data[1]["id"] == 2001
            assert data[1]["name"] == "Pinned Channel"
            assert data[1]["pinned"] is True

            assert data[2]["id"] == 3001
            assert data[2]["name"] == "Recent Friend"
            assert data[2]["type"] == "user"
            assert data[2]["is_contact"] is False

            assert data[3]["id"] == 2002
            assert data[3]["name"] == "Older Channel"
            assert data[3]["type"] == "channel"
            assert data[3]["is_contact"] is False

            contact_items = [c for c in data if c["id"] == 4001]
            assert len(contact_items) == 1
            assert contact_items[0]["type"] == "contact"
            assert contact_items[0]["is_contact"] is True
