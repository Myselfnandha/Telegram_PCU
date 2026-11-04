import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from app.services.cinema_catalog import CinemaCatalog


@pytest.mark.asyncio
async def test_cinema_catalog_in_flight_deduplication():
    catalog = CinemaCatalog()
    mock_client = MagicMock()
    mock_client.session.user_id = 111111
    mock_client._is_secondary_client = False

    fake_media = MagicMock()
    fake_media.document = MagicMock()
    fake_msg = MagicMock()
    fake_msg.id = 3513
    fake_msg.media = fake_media

    call_count = 0

    async def mock_get_messages(cid, ids=None):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return fake_msg

    mock_client.get_messages = AsyncMock(side_effect=mock_get_messages)

    tasks = [
        catalog.get_streamable_message(mock_client, 2372906767, 3513, force_refresh=True)
        for _ in range(5)
    ]
    results = await asyncio.gather(*tasks)

    assert len(results) == 5
    for r in results:
        assert r is fake_msg
    assert call_count == 1


@pytest.mark.asyncio
async def test_cinema_catalog_candidate_cids_channel_prefix():
    catalog = CinemaCatalog()
    mock_client = MagicMock()
    mock_client.session.user_id = 222222
    mock_client._is_secondary_client = True

    fake_media = MagicMock()
    fake_msg = MagicMock()
    fake_msg.id = 3513
    fake_msg.media = fake_media

    attempted_cids = []

    async def mock_get_messages(cid, ids=None):
        attempted_cids.append(cid)
        if cid == -1002372906767:
            return fake_msg
        raise ValueError(f"Cannot find entity {cid}")

    mock_client.get_messages = AsyncMock(side_effect=mock_get_messages)
    mock_client.get_entity = AsyncMock(side_effect=ValueError("entity not found"))

    res = await catalog.get_streamable_message(mock_client, 2372906767, 3513, force_refresh=True)

    assert res is fake_msg
    assert -1002372906767 in attempted_cids


def test_fdm_detection_logic():
    player_names = ("vlc", "mpv", "lavf", "libvlc", "quicktime", "exoplayer", "iina", "clapper")

    def classify(ua: str, headers: dict, query_params: dict, active_conns_count: int = 0):
        ua_lower = ua.lower()
        has_dm_query = query_params.get("dm") in ("1", "true") or query_params.get("download") in ("1", "true")
        is_known_dm_ua = any(dm in ua_lower for dm in ("fdm", "aria2", "idm", "wget", "curl", "freedownloadmanager", "libcurl", "persepolis", "kget", "neatdm"))
        pragma_no_cache = "no-cache" in headers.get("pragma", "").lower()
        cc_no_cache = "no-cache" in headers.get("cache-control", "").lower()
        has_keepalive = headers.get("connection", "").lower() == "keep-alive"
        no_sec_fetch = "sec-fetch-dest" not in headers

        is_dedicated_player = any(p in ua_lower for p in player_names)
        sec_fetch_dest = headers.get("sec-fetch-dest", "").lower()
        is_browser_media = sec_fetch_dest in ("video", "audio")

        if has_dm_query or is_known_dm_ua:
            is_media_player = False
            is_dm = True
        elif is_dedicated_player or is_browser_media:
            is_media_player = True
            is_dm = False
        else:
            is_spoofed_dm = (pragma_no_cache or cc_no_cache) and has_keepalive and no_sec_fetch
            is_multi_conn = active_conns_count >= 1
            is_dm = is_spoofed_dm or is_multi_conn or no_sec_fetch
            is_media_player = not is_dm
        return is_media_player, is_dm

    is_player, is_dm = classify("FreeDownloadManager/6.21.0", {}, {})
    assert not is_player and is_dm

    is_player, is_dm = classify("Mozilla/5.0 (X11; Linux x86_64) Chrome/120.0.0.0", {}, {"dm": "1"})
    assert not is_player and is_dm

    is_player, is_dm = classify(
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        {"connection": "keep-alive", "cache-control": "no-cache"},
        {}
    )
    assert not is_player and is_dm

    is_player, is_dm = classify("VLC/3.0.18 LibVLC/3.0.18", {}, {})
    assert is_player and not is_dm

    is_player, is_dm = classify(
        "Mozilla/5.0 (X11; Linux x86_64) Chrome/120.0.0.0",
        {"sec-fetch-dest": "video"},
        {}
    )
    assert is_player and not is_dm
