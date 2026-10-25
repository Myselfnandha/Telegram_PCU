import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi.testclient import TestClient
from app.main import app
import app.auth as auth_mod
from app.services.fast_stream_engine import FastStreamEngine


from starlette.requests import Request


def make_req(headers=None, query_string="", client_host="127.0.0.1"):
    scope = {
        "type": "http",
        "client": (client_host, 50000),
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
    """Verify Cloudflare tunnel start strictly requires API_AUTH_TOKEN and has no unauthenticated bypass."""
    # 1. Without API_AUTH_TOKEN set -> 403 Forbidden unconditionally
    monkeypatch.setattr(auth_mod, "API_AUTH_TOKEN", "")
    monkeypatch.setattr(auth_mod, "ENABLE_API_AUTH", False)

    client = TestClient(app)
    r_forbidden = client.post("/api/system/tunnel/start", json={})
    assert r_forbidden.status_code == 403
    assert "Remote Cloudflare tunnel cannot be exposed without authentication" in r_forbidden.json()["detail"]

    # Even passing allow_unauthenticated=true is rejected
    r_no_bypass = client.post("/api/system/tunnel/start", json={"allow_unauthenticated": True})
    assert r_no_bypass.status_code == 403

    # 2. With API_AUTH_TOKEN set -> requires token
    monkeypatch.setattr(auth_mod, "API_AUTH_TOKEN", "secure-tunnel-key-888")
    monkeypatch.setattr(auth_mod, "ENABLE_API_AUTH", True)

    # Missing token -> 401
    r_unauth = client.post("/api/system/tunnel/start")
    assert r_unauth.status_code == 401

    # Valid token -> succeeds (status 200 or 400 if binary missing in environment)
    r_valid = client.post(
        "/api/system/tunnel/start",
        headers={"Authorization": "Bearer secure-tunnel-key-888"}
    )
    assert r_valid.status_code in (200, 400)


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


def test_non_loopback_auth_requirement(monkeypatch):
    """Verify non-loopback or forwarded proxy requests strictly require authentication."""
    # When API_AUTH_TOKEN is unset, loopback works, but non-loopback / forwarded requests are forbidden (403)
    monkeypatch.setattr(auth_mod, "API_AUTH_TOKEN", "")
    monkeypatch.setattr(auth_mod, "ENABLE_API_AUTH", False)

    client = TestClient(app)

    # 1. Forwarded header (e.g. from tunnel or reverse proxy) without token -> 403 Forbidden
    r_fwd = client.post(
        "/api/media/cache/clear",
        headers={"X-Forwarded-For": "203.0.113.195"}
    )
    assert r_fwd.status_code == 403
    assert "Non-loopback or remote requests require API_AUTH_TOKEN" in r_fwd.json()["detail"]

    # 2. CF-Connecting-IP header without token -> 403 Forbidden
    r_cf = client.post(
        "/api/media/cache/clear",
        headers={"CF-Connecting-IP": "198.51.100.1"}
    )
    assert r_cf.status_code == 403

    # 3. When token is configured, forwarded request with matching token succeeds
    monkeypatch.setattr(auth_mod, "API_AUTH_TOKEN", "remote-secret-key-777")
    monkeypatch.setattr(auth_mod, "ENABLE_API_AUTH", True)

    r_fwd_auth = client.post(
        "/api/media/cache/clear",
        headers={
            "X-Forwarded-For": "203.0.113.195",
            "Authorization": "Bearer remote-secret-key-777"
        }
    )
    assert r_fwd_auth.status_code == 200


def test_socket_io_auth_verification(monkeypatch):
    """Verify WebSocket authentication logic for loopback, remote, and token auth."""
    from app.socket_handlers import verify_socket_auth

    # 1. Local loopback without configured token -> allowed
    monkeypatch.setattr(auth_mod, "API_AUTH_TOKEN", "")
    monkeypatch.setattr(auth_mod, "ENABLE_API_AUTH", False)
    assert verify_socket_auth({"REMOTE_ADDR": "127.0.0.1"}) is True

    # 2. Remote/Forwarded connection without configured token -> rejected
    assert verify_socket_auth({"REMOTE_ADDR": "192.168.1.50"}) is False
    assert verify_socket_auth({"REMOTE_ADDR": "127.0.0.1", "HTTP_X_FORWARDED_FOR": "1.2.3.4"}) is False

    # 3. Token configured: rejects missing / invalid credentials
    monkeypatch.setattr(auth_mod, "API_AUTH_TOKEN", "ws-secret-key-123")
    monkeypatch.setattr(auth_mod, "ENABLE_API_AUTH", True)

    assert verify_socket_auth({"REMOTE_ADDR": "127.0.0.1"}, auth={}) is False
    assert verify_socket_auth({"REMOTE_ADDR": "127.0.0.1"}, auth={"token": "wrong"}) is False

    # 4. Token configured: accepts valid auth dictionary
    assert verify_socket_auth({"REMOTE_ADDR": "127.0.0.1"}, auth={"token": "ws-secret-key-123"}) is True

    # 5. Token configured: accepts valid query parameter
    assert verify_socket_auth({"REMOTE_ADDR": "192.168.1.50", "QUERY_STRING": "token=ws-secret-key-123"}) is True

    # 6. Token configured: accepts valid Bearer header
    assert verify_socket_auth({
        "REMOTE_ADDR": "192.168.1.50",
        "HTTP_AUTHORIZATION": "Bearer ws-secret-key-123"
    }) is True
