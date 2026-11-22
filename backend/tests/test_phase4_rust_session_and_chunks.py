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
    assert status.get("status") == "ok"
    assert status.get("active_accounts", 0) >= 1

    # Authorized call using rust_bridge._get_client() succeeds
    client = rust_bridge._get_client()
    resp = await client.post("/api/safety/report_flood", json={"account_idx": 0, "seconds": 0})
    assert resp.status_code == 200, f"Expected 200 with auth, got {resp.status_code}"
