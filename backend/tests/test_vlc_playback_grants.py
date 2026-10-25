"""Security tests for short-lived VLC/mobile playback capabilities."""
import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).parent.parent))

import app.auth as auth
from app.services.vlc_playback import PlaybackGrantStore, playback_grants


def _request(path: str, token: str = "", method: str = "GET", peer: str = "192.168.1.50") -> Request:
    query = f"playback_token={token}".encode() if token else b""
    return Request({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": query, "headers": [], "client": (peer, 43210),
        "server": ("192.168.1.10", 8088),
    })


def test_grant_is_bound_to_one_media_item():
    store = PlaybackGrantStore(ttl_seconds=60)
    token = store.issue("-100123", 42)
    assert store.allows(token, "-100123", 42)
    assert not store.allows(token, "-100123", 43)
    assert not store.allows(token, "-100999", 42)
    assert not store.allows("invalid", "-100123", 42)


def test_grant_expires():
    store = PlaybackGrantStore(ttl_seconds=0)
    token = store.issue("-100123", 42)
    assert not store.allows(token, "-100123", 42)


def test_remote_vlc_media_link_works_without_permanent_api_token(monkeypatch):
    monkeypatch.setattr(auth, "API_AUTH_TOKEN", "")
    monkeypatch.setattr(auth, "ENABLE_API_AUTH", True)
    token = playback_grants.issue("-100123", 42)
    asyncio.run(auth.verify_auth(_request(f"/dl/-100123/42/movie.mkv", token)))


def test_media_grant_cannot_authorize_other_routes_or_media(monkeypatch):
    monkeypatch.setattr(auth, "API_AUTH_TOKEN", "")
    monkeypatch.setattr(auth, "ENABLE_API_AUTH", True)
    token = playback_grants.issue("-100123", 42)
    with pytest.raises(HTTPException) as other_media:
        asyncio.run(auth.verify_auth(_request("/dl/-100123/43/movie.mkv", token)))
    assert other_media.value.status_code == 403
    with pytest.raises(HTTPException) as api_route:
        asyncio.run(auth.verify_auth(_request("/api/system/stats", token)))
    assert api_route.value.status_code == 403


def test_grant_can_authorize_its_matching_subtitle_only(monkeypatch):
    monkeypatch.setattr(auth, "API_AUTH_TOKEN", "")
    monkeypatch.setattr(auth, "ENABLE_API_AUTH", True)
    token = playback_grants.issue("-100123", 42)
    asyncio.run(auth.verify_auth(_request("/api/media/subtitles/download/-100123/42", token)))
    with pytest.raises(HTTPException):
        asyncio.run(auth.verify_auth(_request("/api/media/subtitles/download/-100123/43", token)))


def test_grant_supports_only_get_and_head(monkeypatch):
    monkeypatch.setattr(auth, "API_AUTH_TOKEN", "")
    monkeypatch.setattr(auth, "ENABLE_API_AUTH", True)
    token = playback_grants.issue("-100123", 42)
    with pytest.raises(HTTPException):
        asyncio.run(auth.verify_auth(_request("/dl/-100123/42/movie.mkv", token, method="POST")))
