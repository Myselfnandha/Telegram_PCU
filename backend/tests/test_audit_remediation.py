import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi.testclient import TestClient
from app.main import app
import app.auth as auth_mod
from app.services.fast_stream_engine import FastStreamEngine


from starlette.requests import Request


def make_req(headers=None, query_string=""):
    scope = {
        "type": "http",
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (headers or {}).items()],
        "query_string": query_string.encode("latin-1"),
    }
    return Request(scope)


def test_auth_verification_modes():
    """Verify is_authenticated behavior with various token schemes."""
    orig_token = auth_mod.API_AUTH_TOKEN
    orig_enable = auth_mod.ENABLE_API_AUTH
    try:
        auth_mod.API_AUTH_TOKEN = "test-secret-token-12345"
        auth_mod.ENABLE_API_AUTH = True

        # 1. Missing auth
        assert auth_mod.is_authenticated(make_req()) is False

        # 2. Correct Bearer token
        req = make_req(headers={"Authorization": "Bearer test-secret-token-12345"})
        assert auth_mod.is_authenticated(req) is True

        # 3. Incorrect Bearer token
        req = make_req(headers={"Authorization": "Bearer wrong-token"})
        assert auth_mod.is_authenticated(req) is False

        # 4. Correct X-API-Key
        req = make_req(headers={"X-API-Key": "test-secret-token-12345"})
        assert auth_mod.is_authenticated(req) is True

        # 5. Incorrect X-API-Key
        req = make_req(headers={"X-API-Key": "bad-key"})
        assert auth_mod.is_authenticated(req) is False

        # 6. Correct query param
        req = make_req(query_string="token=test-secret-token-12345")
        assert auth_mod.is_authenticated(req) is True

        # 7. Unconfigured mode allows all
        auth_mod.API_AUTH_TOKEN = ""
        auth_mod.ENABLE_API_AUTH = False
        assert auth_mod.is_authenticated(make_req()) is True
    finally:
        auth_mod.API_AUTH_TOKEN = orig_token
        auth_mod.ENABLE_API_AUTH = orig_enable


def test_protected_routes_require_auth(monkeypatch):
    """Verify protected endpoints return 401 when auth is configured but missing."""
    monkeypatch.setattr(auth_mod, "API_AUTH_TOKEN", "secure-api-key-999")
    monkeypatch.setattr(auth_mod, "ENABLE_API_AUTH", True)

    client = TestClient(app)

    # 1. Cache clear without auth -> 401
    r = client.post("/api/media/cache/clear")
    assert r.status_code == 401
    assert "Unauthorized" in r.json()["detail"]

    # 2. Cache clear with valid Bearer -> 200
    r_auth = client.post(
        "/api/media/cache/clear",
        headers={"Authorization": "Bearer secure-api-key-999"}
    )
    assert r_auth.status_code == 200
    assert r_auth.json().get("success") is True

    # 3. Cache clear with valid X-API-Key -> 200
    r_key = client.post(
        "/api/media/cache/clear",
        headers={"X-API-Key": "secure-api-key-999"}
    )
    assert r_key.status_code == 200

    # 4. Cache clear with valid query param -> 200
    r_query = client.post("/api/media/cache/clear?token=secure-api-key-999")
    assert r_query.status_code == 200


def test_tunnel_start_unauthenticated_guard(monkeypatch):
    """Verify Cloudflare tunnel start rejects if unauthenticated unless explicitly opted in."""
    monkeypatch.setattr(auth_mod, "API_AUTH_TOKEN", "")
    monkeypatch.setattr(auth_mod, "ENABLE_API_AUTH", False)

    client = TestClient(app)

    # Calling /tunnel/start without token or flag -> 403 Forbidden
    r_forbidden = client.post("/api/system/tunnel/start", json={})
    assert r_forbidden.status_code == 403
    assert "Remote Cloudflare tunnel cannot be started without authentication" in r_forbidden.json()["detail"]

    # Calling with allow_unauthenticated: True proceeds to launch (or returns status/error)
    r_opt_in = client.post("/api/system/tunnel/start", json={"allow_unauthenticated": True})
    # Should not be 403 (could be 200 with active status or error if binary missing)
    assert r_opt_in.status_code in (200, 400)


@pytest.mark.asyncio
async def test_fast_stream_engine_lifecycle():
    """Verify FastStreamEngine cleanup task initializes lazily and shuts down cleanly."""
    engine = FastStreamEngine()
    assert engine._cleanup_task is None

    session = engine.get_or_create_session("chat1", 100, 5000, "sample.mp4")
    assert session is not None
    assert engine._cleanup_task is not None
    assert not engine._cleanup_task.done()

    # Shutdown cancels and clears
    await engine.shutdown()
    assert engine._cleanup_task is None
    assert len(engine._sessions) == 0
