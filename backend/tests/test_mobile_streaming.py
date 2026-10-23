import sys
from pathlib import Path
import pytest
from httpx import AsyncClient, ASGITransport

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.network_service import NetworkService, network_service
from app.main import app


def test_network_service_lan_detection():
    svc = NetworkService()
    lan_ip = svc.get_lan_ip()
    assert isinstance(lan_ip, str)
    assert len(lan_ip) > 0
    # Should not be empty, usually 127.0.0.1 or 192.168.x.x or 10.x.x.x
    parts = lan_ip.split(".")
    assert len(parts) == 4

    info = svc.get_network_info(port=8088)
    assert "lan_ip" in info
    assert "interfaces" in info
    assert info["port"] == 8088
    assert info["default_url"].startswith("http://")
    assert ":8088" in info["default_url"]
    assert "tunnel" in info


def test_network_service_qr_generation():
    svc = NetworkService()
    test_url = "http://10.74.171.142:8088/dl/me/123/video.mp4"
    qr_bytes = svc.generate_qr(test_url)
    assert isinstance(qr_bytes, bytes)
    # PNG signature check: \x89PNG\r\n\x1a\n
    assert qr_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(qr_bytes) > 100


def test_network_service_tunnel_initial_state():
    svc = NetworkService()
    status = svc.get_tunnel_status()
    assert "status" in status
    assert status["status"] in ("stopped", "running", "starting")


@pytest.mark.asyncio
async def test_api_network_endpoints():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. GET /api/system/network
        resp = await ac.get("/api/system/network")
        assert resp.status_code == 200
        data = resp.json()
        assert "lan_ip" in data
        assert "default_url" in data

        # 2. GET /api/system/qrcode
        qr_resp = await ac.get("/api/system/qrcode?text=http%3A%2F%2F127.0.0.1%3A8088%2Ftest")
        assert qr_resp.status_code == 200
        assert qr_resp.headers["content-type"] == "image/png"
        assert qr_resp.content.startswith(b"\x89PNG\r\n\x1a\n")

        # 3. GET /api/system/tunnel/status
        tunnel_resp = await ac.get("/api/system/tunnel/status")
        assert tunnel_resp.status_code == 200
        assert "status" in tunnel_resp.json()


@pytest.mark.asyncio
async def test_api_mobile_info_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://10.74.171.142:8088") as ac:
        resp = await ac.get("/api/media/mobile_info/12345/678?filename=Movie.2024.1080p.mkv")
        assert resp.status_code == 200
        data = resp.json()

        assert data["chat_id"] == "12345"
        assert data["message_id"] == 678
        assert data["filename"] == "Movie.2024.1080p.mkv"
        assert data["stream_url"].startswith("http://10.74.171.142:8088/dl/12345/678/")
        assert data["vlc_url"].startswith("vlc://http://10.74.171.142:8088/dl/12345/678/")
        assert data["android_intent"].startswith("intent://10.74.171.142:8088/dl/12345/678/")
        assert "package=org.videolan.vlc" in data["android_intent"]
        assert data["playlist_url"] == "/api/media/vlc/playlist/12345/678/Movie.2024.1080p.mkv.m3u"
        assert data["transmux_url"] == "/stream/12345/678/Movie.2024.1080p.mp4"
        assert "has_subtitles" in data
        assert data["host"] == "10.74.171.142:8088"
