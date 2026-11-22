import pytest
import httpx
from app.services.rust_bridge import rust_bridge, _RUST_CORE_AUTH_TOKEN

@pytest.mark.asyncio
async def test_rust_core_unauthorized_rejection():
    # Calling /api/* without Authorization header must return 401 Unauthorized
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8099") as client:
        resp = await client.post("/api/safety/report_flood", json={"account_idx": 0, "seconds": 0})
        assert resp.status_code == 401, f"Expected 401 without auth, got {resp.status_code}"

@pytest.mark.asyncio
async def test_rust_core_health_and_auth():
    # /health is unauthenticated and reports status
    status = await rust_bridge.get_status()
    if status.get("active_accounts", 0) < 1:
        import struct, socket, base64
        raw_bytes = struct.pack(">B4sH", 5, socket.inet_aton("149.154.167.51"), 443) + b"\x07" * 256
        s = "1" + base64.urlsafe_b64encode(raw_bytes).decode().rstrip("=")
        await rust_bridge.register_session(s, 30662321, account_idx=0)
        status = await rust_bridge.get_status()

    assert status.get("status") == "ok"
    assert status.get("active_accounts", 0) >= 1

    # Authorized call using rust_bridge._get_client() succeeds
    client = rust_bridge._get_client()
    resp = await client.post("/api/safety/report_flood", json={"account_idx": 0, "seconds": 0})
    assert resp.status_code == 200, f"Expected 200 with auth, got {resp.status_code}"
