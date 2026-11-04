# -*- coding: utf-8 -*-
"""
AAA Unit Tests for Web Series Draft Engine and Batch VLC / Playlist endpoints.
"""

import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app
from app.services.subtitle_service import SubtitleService
from app.services.vlc_playback import playback_grants


# ---------------------------------------------------------------------------
# Test Suite: Series Title & Season/Episode Extraction
# ---------------------------------------------------------------------------

def test_extract_series_standard_s_e():
    """Arrange: raw filename with S01E05 notation. Act: parse. Assert: valid series, season, episode."""
    svc = SubtitleService()
    res = svc.parse_media_title("Dark.S01E05.1080p.NF.WEB-DL.DDP5.1.x264.mkv")
    assert res["is_series"] is True
    assert res["title"] == "Dark"
    assert res["season"] == 1
    assert res["episode"] == 5


def test_extract_series_season_word():
    """Arrange: filename with 'Season 2 Episode 03'. Act: parse. Assert: S2E3."""
    svc = SubtitleService()
    res = svc.parse_media_title("Stranger.Things.Season.2.Episode.03.720p.mkv")
    assert res["is_series"] is True
    assert res["title"] == "Stranger Things"
    assert res["season"] == 2
    assert res["episode"] == 3


def test_extract_series_x_notation():
    """Arrange: filename with 02x04 notation. Act: parse. Assert: S2E4."""
    svc = SubtitleService()
    res = svc.parse_media_title("Breaking.Bad.02x04.HDTV.x264.mkv")
    assert res["is_series"] is True
    assert res["title"] == "Breaking Bad"
    assert res["season"] == 2
    assert res["episode"] == 4


def test_extract_movie_not_flagged_as_series():
    """Arrange: movie with release year. Act: parse. Assert: is_series is False."""
    svc = SubtitleService()
    res = svc.parse_media_title("Oppenheimer.2023.IMAX.2160p.UHD.HDR.mkv")
    assert res["is_series"] is False
    assert res["year"] == 2023
    assert res["season"] is None
    assert res["episode"] is None


# ---------------------------------------------------------------------------
# Test Suite: Batch Playlist Generation & Loopback Guards
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    return TestClient(app)


def test_batch_playlist_endpoint(client):
    """
    Arrange: payload with multiple episodes in a season.
    Act: POST to /api/media/vlc/batch_playlist with loopback testclient.
    Assert: HTTP 200 with #EXTM3U content and audio/x-mpegurl media type.
    """
    payload = {
        "title": "Dark_Season_1",
        "items": [
            {"chat_id": "-100123456", "message_id": 101, "filename": "Dark.S01E01.mkv"},
            {"chat_id": "-100123456", "message_id": 102, "filename": "Dark.S01E02.mkv"}
        ]
    }
    response = client.post("/api/media/vlc/batch_playlist", json=payload)
    assert response.status_code == 200
    assert "audio/x-mpegurl" in response.headers["content-type"]
    text = response.text
    assert "#EXTM3U" in text
    assert "Dark.S01E01.mkv" in text
    assert "Dark.S01E02.mkv" in text
    assert "playback_token=" in text


def test_batch_playlist_empty_items_rejected(client):
    """
    Arrange: empty items list.
    Act: POST /api/media/vlc/batch_playlist.
    Assert: HTTP 400 Bad Request.
    """
    response = client.post("/api/media/vlc/batch_playlist", json={"items": []})
    assert response.status_code == 400


def test_batch_vlc_launch_empty_items_rejected(client):
    """
    Arrange: empty items list.
    Act: POST /api/media/vlc/launch_batch.
    Assert: HTTP 400 Bad Request.
    """
    response = client.post("/api/media/vlc/launch_batch", json={"items": []})
    assert response.status_code == 400
