import pytest
import httpx
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.main import app

BASE_URL = "http://127.0.0.1:8088"

def make_client(timeout: float = 10.0):
    import os
    if os.environ.get("TEST_LIVE_SERVER") == "1":
        try:
            import socket
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.2)
                if s.connect_ex(("127.0.0.1", 8088)) == 0:
                    return httpx.AsyncClient(base_url=BASE_URL, timeout=timeout)
        except Exception:
            pass
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8088", timeout=timeout)

@pytest.mark.asyncio
async def test_auth_status_and_privacy_masking():
    """Verify authentication endpoint and ensure phone number is masked for privacy."""
    async with make_client(timeout=10.0) as client:
        res = await client.get("/api/auth/status")
        assert res.status_code == 200, f"Expected 200 OK, got {res.status_code}: {res.text}"
        data = res.json()
        assert data.get("authenticated") is True, "Expected user to be authenticated"
        assert data.get("username") is not None
        phone = data.get("phone")
        if phone:
            assert "*" in phone, f"Phone number must be masked, got: {phone}"
            assert not phone.isdigit(), "Raw unmasked digits detected in phone field"

@pytest.mark.asyncio
async def test_media_cache_status_and_limits():
    """Verify media stream cache status adheres to configured 2GB max limit."""
    async with make_client(timeout=10.0) as client:
        res = await client.get("/api/media/cache/status")
        assert res.status_code == 200
        data = res.json()
        assert "max_mb" in data
        assert "total_mb" in data
        # Check cache is bounded to 2GB (2048 MB)
        assert data["max_mb"] <= 2048.0, f"Cache limit exceeded 2048 MB: {data['max_mb']}"
        assert data["total_mb"] >= 0

@pytest.mark.asyncio
async def test_cinema_videos_functional_schema():
    """Verify cinema videos endpoint returns correctly formatted video entities."""
    async with make_client(timeout=10.0) as client:
        res = await client.get("/api/media/videos/me?limit=5")
        assert res.status_code == 200
        data = res.json()
        assert "chat_id" in data
        assert "count" in data
        assert "videos" in data
        assert isinstance(data["videos"], list)
        if len(data["videos"]) > 0:
            vid = data["videos"][0]
            assert "message_id" in vid
            assert "filename" in vid
            assert "file_size" in vid

@pytest.mark.asyncio
async def test_path_traversal_probes():
    """Verify endpoint resists path traversal and malformed resource identifiers."""
    async with make_client(timeout=10.0) as client:
        traversal_payloads = [
            "../../etc/passwd",
            "..%2F..%2Fetc%2Fpasswd",
            "../../../etc/shadow",
            "me/../../etc/passwd"
        ]
        for payload in traversal_payloads:
            res = await client.get(f"/api/media/videos/{payload}")
            # Endpoint should reject, return 400/404, or safely treat as chat query without 500 error leak
            assert res.status_code in (400, 404, 200), f"Unexpected status {res.status_code} on traversal: {payload}"
            assert "root:x:0:0" not in res.text

@pytest.mark.asyncio
async def test_injection_and_xss_probes():
    """Verify SQL injection and XSS payloads in query parameters do not cause internal crash."""
    async with make_client(timeout=10.0) as client:
        payloads = [
            "' OR '1'='1",
            "<script>alert(1)</script>",
            "'; DROP TABLE cinema_videos_cache; --"
        ]
        for p in payloads:
            res = await client.get(f"/api/media/videos/{p}?limit=1")
            assert res.status_code in (200, 400, 404, 422), f"Endpoint crashed on payload: {p}"
            assert "OperationalError" not in res.text
            assert "syntax error" not in res.text.lower()

@pytest.mark.asyncio
async def test_stream_range_bounds():
    """Verify stream handler handles out-of-bounds byte ranges gracefully."""
    async with make_client(timeout=10.0) as client:
        headers = {"Range": "bytes=999999999999-"}
        res = await client.get("/api/media/stream/me/13889", headers=headers)
        # Should return 416 (Range Not Satisfiable), 404, or 400, never uncaught 500
        assert res.status_code in (416, 404, 400, 206), f"Expected handled range response, got {res.status_code}"

@pytest.mark.asyncio
async def test_concurrent_session_access():
    """Verify concurrent requests do not trigger SQLite database locking or session contention."""
    async with make_client(timeout=15.0) as client:
        tasks = [client.get("/api/auth/status") for _ in range(25)]
        responses = await asyncio.gather(*tasks)
        for r in responses:
            assert r.status_code == 200, f"Concurrent request failed with status {r.status_code}"
            assert r.json().get("authenticated") is True
            assert "database is locked" not in r.text
