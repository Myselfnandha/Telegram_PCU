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


def test_grant_channel_id_normalization():
    store = PlaybackGrantStore(ttl_seconds=60)
    token1 = store.issue("-1002372906767", 886)
    assert store.allows(token1, "2372906767", 886)
    assert store.allows(token1, -1002372906767, 886)

    token2 = store.issue("-100100456", 10)
    assert store.allows(token2, "100456", 10)
    assert store.allows(token2, -100100456, 10)

    token3 = store.issue("-987654", 5)
    assert store.allows(token3, "987654", 5)
    assert store.allows(token3, -987654, 5)


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


def test_remove_deleted_video_prunes_cache(monkeypatch):
    from app.services.cinema_catalog import cinema_catalog
    
    # Setup dummy in-memory catalog
    cinema_catalog._videos_cache["12345_0"] = {
        "data": {
            "chat_id": "12345",
            "count": 2,
            "videos": [
                {"message_id": 101, "filename": "v1.mp4"},
                {"message_id": 102, "filename": "v2.mp4"}
            ]
        },
        "timestamp": 123456789.0
    }
    
    asyncio.run(cinema_catalog.remove_deleted_video("12345", 101))
    remaining = cinema_catalog._videos_cache["12345_0"]["data"]["videos"]
    assert len(remaining) == 1
    assert remaining[0]["message_id"] == 102


@pytest.mark.asyncio
async def test_launch_desktop_vlc_flags(monkeypatch):
    """Verifies that launch_desktop_vlc uses correct flags without --http-continuous and with --no-spdif."""
    from app.routes.proxy import launch_desktop_vlc
    from unittest.mock import MagicMock, AsyncMock, patch

    mock_client = MagicMock()
    mock_msg = MagicMock()
    mock_msg.media = MagicMock()
    mock_msg.file = MagicMock(size=1024*1024, name="movie.mkv")

    spawned_args = []
    spawned_kwargs = {}
    class MockPopen:
        def __init__(self, args, **kwargs):
            nonlocal spawned_args, spawned_kwargs
            spawned_args = args
            spawned_kwargs = kwargs
            self.pid = 99999

    req = _request("/api/media/vlc/launch/test_chat/1", method="POST", peer="127.0.0.1")

    with patch("app.telegram_client.TelegramClientManager.get_client", new_callable=AsyncMock) as mock_get_c, \
         patch("app.routes.proxy.get_streamable_message", new_callable=AsyncMock) as mock_get_m, \
         patch("shutil.which", return_value="/usr/bin/vlc"), \
         patch("subprocess.Popen", side_effect=MockPopen):
        mock_get_c.return_value = mock_client
        mock_get_m.return_value = mock_msg

        res = await launch_desktop_vlc("test_chat", 1, req, filename="movie.mkv")
        assert res["success"] is True
        assert "--http-continuous" not in spawned_args
        assert "--no-spdif" in spawned_args
        assert "--network-caching=2000" in spawned_args
        assert "env" in spawned_kwargs
        assert "DISPLAY" in spawned_kwargs["env"]


@pytest.mark.asyncio
async def test_get_streamable_message_cross_client_resolution():
    """Verifies that get_streamable_message resolves public channel usernames across primary and secondary clients."""
    from app.services.cinema_catalog import cinema_catalog
    from unittest.mock import MagicMock, AsyncMock, patch

    primary_client = MagicMock()
    primary_entity = MagicMock(username="my_cool_channel")
    primary_client.get_entity = AsyncMock(return_value=primary_entity)

    sec_client = MagicMock()
    sec_client._is_secondary_client = True
    sec_entity = MagicMock()
    sec_msg = MagicMock()
    sec_msg.media = MagicMock()
    async def mock_get_messages(entity, **kwargs):
        if entity is sec_entity:
            return sec_msg
        raise Exception("Unknown channel")
    sec_client.get_messages = AsyncMock(side_effect=mock_get_messages)
    sec_client.get_entity = AsyncMock(side_effect=[Exception("Unknown"), Exception("Unknown"), Exception("Unknown"), sec_entity])
    sec_client.get_dialogs = AsyncMock(return_value=[])

    async def mock_iter_dialogs(limit=400):
        if False:
            yield None
    sec_client.iter_dialogs = mock_iter_dialogs

    with patch("app.telegram_client.TelegramClientManager.get_client", new_callable=AsyncMock) as mock_get_primary:
        mock_get_primary.return_value = primary_client

        res = await cinema_catalog.get_streamable_message(sec_client, -100999888, 555, force_refresh=True)
        assert res is sec_msg
        assert any(c.args == ("my_cool_channel",) for c in sec_client.get_entity.await_args_list)


