import sys
import time
import asyncio
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.main import app
from app.services.vlc_playback import playback_grants
from app.services.playback_tracker import playback_tracker

@pytest.fixture
def test_client():
    return TestClient(app)

def test_vlc_playback_grant_issuance_and_expiry():
    token = playback_grants.issue("-100111222", 301)
    assert token is not None
    assert len(token) >= 16
    assert playback_grants.allows(token, "-100111222", 301) is True
    assert playback_grants.allows(token, "-100111222", 302) is False
    assert playback_grants.allows(token, "-100999999", 301) is False
    assert playback_grants.allows("", "-100111222", 301) is False

def test_vlc_batch_playlist_generation(test_client):
    payload = {
        "title": "Season_Playlist_Test",
        "items": [
            {"chat_id": "-100444", "message_id": 10, "filename": "Ep1.mkv"},
            {"chat_id": "-100444", "message_id": 11, "filename": "Ep2.mkv"},
            {"chat_id": "-100444", "message_id": 12, "filename": "Ep3.mkv"}
        ]
    }
    response = test_client.post("/api/media/vlc/batch_playlist", json=payload)
    assert response.status_code == 200
    assert "audio/x-mpegurl" in response.headers["content-type"]
    text = response.text
    assert "#EXTM3U" in text
    assert "Ep1.mkv" in text
    assert "Ep2.mkv" in text
    assert "Ep3.mkv" in text

def test_vlc_batch_playlist_rejects_empty_items(test_client):
    response = test_client.post("/api/media/vlc/batch_playlist", json={"items": []})
    assert response.status_code == 400

@pytest.mark.asyncio
async def test_vlc_resume_exact_98_percent_auto_purge_boundary():
    await playback_tracker.ainit_db()
    chat_id = "-10077701"
    msg_id = 901
    fname = "BoundaryMovie.2024.1080p.mkv"
    total_sec = 200.0

    await playback_tracker.aupdate_progress(chat_id, msg_id, fname, last_position_seconds=50.0, duration_seconds=total_sec)
    p = await playback_tracker.aget_playback_progress(chat_id, msg_id)
    assert p is not None
    assert p["percent"] == 25.0

    await playback_tracker.aupdate_progress(chat_id, msg_id, fname, last_position_seconds=195.8, duration_seconds=total_sec)
    p_retained = await playback_tracker.aget_playback_progress(chat_id, msg_id)
    assert p_retained is not None
    assert p_retained["percent"] == 97.9

    await playback_tracker.aupdate_progress(chat_id, msg_id, fname, last_position_seconds=196.0, duration_seconds=total_sec)
    p_purged = await playback_tracker.aget_playback_progress(chat_id, msg_id)
    assert p_purged is None

@pytest.mark.asyncio
async def test_vlc_resume_above_98_percent_purged():
    await playback_tracker.ainit_db()
    chat_id = "-10077702"
    msg_id = 902
    fname = "HighProgressMovie.mkv"

    await playback_tracker.aupdate_progress(chat_id, msg_id, fname, last_position_seconds=99.0, duration_seconds=100.0)
    res = await playback_tracker.aget_playback_progress(chat_id, msg_id)
    assert res is None

@pytest.mark.asyncio
async def test_vlc_resume_reset_clears_position_and_percent():
    await playback_tracker.ainit_db()
    chat_id = "-10077703"
    msg_id = 903
    fname = "ResetTestMovie.mkv"

    await playback_tracker.aupdate_progress(chat_id, msg_id, fname, last_position_seconds=60.0, duration_seconds=100.0)
    p = await playback_tracker.aget_playback_progress(chat_id, msg_id)
    assert p["percent"] == 60.0

    await playback_tracker.aupdate_progress(chat_id, msg_id, fname, is_reset=True)
    p_reset = await playback_tracker.aget_playback_progress(chat_id, msg_id)
    assert p_reset["percent"] == 0.0
    assert p_reset["playback_seconds"] == 0.0
    await playback_tracker.aremove_progress(chat_id, msg_id)

@pytest.mark.asyncio
async def test_vlc_byte_stream_progress_derivation():
    await playback_tracker.ainit_db()
    chat_id = "-10077704"
    msg_id = 904
    fname = "StreamInference.mkv"
    file_size = 200 * 1024 * 1024
    byte_offset = 140 * 1024 * 1024

    await playback_tracker.aupdate_byte_progress(chat_id, msg_id, fname, byte_offset, file_size)
    prog = await playback_tracker.aget_playback_progress(chat_id, msg_id)
    assert prog is not None
    assert prog["percent"] == 70.0
    await playback_tracker.aremove_progress(chat_id, msg_id)

def test_vlc_progress_bar_color_classification():
    def classify(pct: float) -> str:
        if pct >= 70.0:
            return "bg-emerald-500"
        if pct >= 30.0:
            return "bg-amber-500"
        return "bg-sky-500"

    assert classify(0.0) == "bg-sky-500"
    assert classify(15.5) == "bg-sky-500"
    assert classify(29.9) == "bg-sky-500"
    assert classify(30.0) == "bg-amber-500"
    assert classify(50.0) == "bg-amber-500"
    assert classify(69.9) == "bg-amber-500"
    assert classify(70.0) == "bg-emerald-500"
    assert classify(97.9) == "bg-emerald-500"
