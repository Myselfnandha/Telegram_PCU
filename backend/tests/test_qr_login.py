import sys
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app
from app.telegram_client import TelegramClientManager, generate_qr_data_url


def test_generate_qr_data_url():
    test_url = "tg://login?token=AQAB_mock_token_12345"
    data_url = generate_qr_data_url(test_url)
    assert data_url.startswith("data:image/png;base64,")
    assert len(data_url) > 100


@pytest.mark.asyncio
async def test_start_qr_login_already_authorized():
    mock_client = AsyncMock()
    mock_client.is_connected = MagicMock(return_value=True)
    mock_client.is_user_authorized = AsyncMock(return_value=True)

    with patch.object(TelegramClientManager, "get_client", AsyncMock(return_value=mock_client)), \
         patch.object(TelegramClientManager, "get_me_info", AsyncMock(return_value={"id": 123456, "name": "Alice"})):
        res = await TelegramClientManager.start_qr_login()
        assert res["status"] in ("authorized", "success")
        assert res["user"]["id"] == 123456


@pytest.mark.asyncio
async def test_start_qr_login_waiting_and_poll():
    mock_client = AsyncMock()
    mock_client.is_connected = MagicMock(return_value=True)
    mock_client.is_user_authorized = AsyncMock(return_value=False)

    mock_qr = AsyncMock()
    mock_qr.url = "tg://login?token=token_abc_xyz"
    mock_qr.expires = 1790000000
    mock_qr.wait = AsyncMock()
    mock_client.qr_login = AsyncMock(return_value=mock_qr)

    with patch.object(TelegramClientManager, "get_client", AsyncMock(return_value=mock_client)):
        res = await TelegramClientManager.start_qr_login()
        assert res["status"] == "waiting"
        assert res["url"] == "tg://login?token=token_abc_xyz"
        assert "data:image/png;base64," in res["qr_image"]

        status = TelegramClientManager.get_qr_status()
        assert status["status"] == "waiting"

        if TelegramClientManager._qr_wait_task:
            TelegramClientManager._qr_wait_task.cancel()


@pytest.mark.asyncio
async def test_qr_wait_2fa_required():
    from telethon.errors import SessionPasswordNeededError

    mock_qr = AsyncMock()
    mock_qr.wait = AsyncMock(side_effect=SessionPasswordNeededError(request=None))

    await TelegramClientManager._wait_qr_login(mock_qr)
    status = TelegramClientManager.get_qr_status()
    assert status["status"] == "2fa_required"
    assert "2FA" in status["message"]


@pytest.mark.asyncio
async def test_qr_wait_expired():
    mock_qr = AsyncMock()
    mock_qr.wait = AsyncMock(side_effect=asyncio.TimeoutError())

    await TelegramClientManager._wait_qr_login(mock_qr)
    status = TelegramClientManager.get_qr_status()
    assert status["status"] == "expired"


@pytest.mark.asyncio
async def test_secondary_qr_login_and_status():
    mock_sec_client = AsyncMock()
    mock_sec_client.is_connected = MagicMock(return_value=True)
    mock_qr = AsyncMock()
    mock_qr.url = "tg://login?token=secondary_token_789"
    mock_qr.expires = 1790000000
    mock_qr.wait = AsyncMock()
    mock_sec_client.qr_login = AsyncMock(return_value=mock_qr)

    with patch("app.telegram_client.TelegramClient", return_value=mock_sec_client):
        res = await TelegramClientManager.start_secondary_qr_login()
        assert res["status"] == "waiting"
        assert res["url"] == "tg://login?token=secondary_token_789"
        assert "data:image/png;base64," in res["qr_image"]

        status = TelegramClientManager.get_secondary_qr_status()
        assert status["status"] == "waiting"

        if TelegramClientManager._secondary_qr_wait_task:
            TelegramClientManager._secondary_qr_wait_task.cancel()


@pytest.mark.asyncio
async def test_api_qr_routes():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        with patch("app.telegram_client.TelegramClientManager.start_qr_login") as mock_start, \
             patch("app.telegram_client.TelegramClientManager.get_qr_status") as mock_status, \
             patch("app.telegram_client.TelegramClientManager.start_secondary_qr_login") as mock_sec_start, \
             patch("app.telegram_client.TelegramClientManager.get_secondary_qr_status") as mock_sec_status:

            mock_start.return_value = {
                "status": "waiting",
                "url": "tg://login?token=test",
                "qr_image": "data:image/png;base64,mock",
                "expires": "1790000000",
            }
            mock_status.return_value = {"status": "waiting"}

            mock_sec_start.return_value = {
                "status": "waiting",
                "url": "tg://login?token=test_sec",
                "qr_image": "data:image/png;base64,mock_sec",
                "expires": "1790000000",
            }
            mock_sec_status.return_value = {"status": "waiting"}

            # Primary QR start & status
            res = await client.post("/api/auth/qr/start")
            assert res.status_code == 200
            assert res.json()["status"] == "waiting"
            assert "data:image/png;base64," in res.json()["qr_image"]

            res = await client.get("/api/auth/qr/status")
            assert res.status_code == 200
            assert res.json()["status"] == "waiting"

            # Secondary QR start & status
            res_sec = await client.post("/api/auth/secondary/qr/start")
            assert res_sec.status_code == 200
            assert res_sec.json()["status"] == "waiting"

            res_sec_stat = await client.get("/api/auth/secondary/qr/status")
            assert res_sec_stat.status_code == 200
            assert res_sec_stat.json()["status"] == "waiting"
