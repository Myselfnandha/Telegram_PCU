import os
import sys
from pathlib import Path
import pytest
import sqlite3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.playback_tracker import PlaybackTracker
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_chunk_upload_status_endpoint():
    res = client.get("/api/upload/chunk/status?upload_id=test_non_existent&filename=video.mp4")
    assert res.status_code == 200
    data = res.json()
    assert data["exists"] is False
    assert data["next_chunk_index"] == 0
    assert data["received_bytes"] == 0

def test_playback_tracker_chunk_persistence(tmp_path, monkeypatch):
    test_db = tmp_path / "test_tracker.db"
    monkeypatch.setattr("app.services.playback_tracker.DB_PATH", test_db)
    tracker = PlaybackTracker()

    # 1. Simulate video playback at chunk 100 (50MB in, 300s into movie)
    tracker.update_progress(
        chat_id="123456",
        message_id=789,
        filename="Movie.2026.1080p.mkv",
        last_position_seconds=300.0,
        duration_seconds=5400.0,
        file_size=2 * 1024 * 1024 * 1024,
        last_chunk_index=100,
        last_byte_offset=50 * 1024 * 1024
    )

    prog = tracker.get_playback_progress("123456", 789)
    assert prog is not None
    assert prog["last_chunk_index"] == 100
    assert prog["last_byte_offset"] == 50 * 1024 * 1024
    assert prog["last_position_seconds"] == 300.0

    # 2. Verify that subsequent header probe reads (offset 0..16MB) DO NOT reset watch position to 0
    tracker.update_byte_progress(
        chat_id="123456",
        message_id=789,
        filename="Movie.2026.1080p.mkv",
        streamed_byte_offset=0,
        total_file_size=2 * 1024 * 1024 * 1024
    )

    prog2 = tracker.get_playback_progress("123456", 789)
    assert prog2["last_position_seconds"] == 300.0
    assert prog2["last_chunk_index"] == 100

    # 3. Verify that continue watching returns the item with chunk information
    cw = tracker.get_continue_watching()
    assert len(cw) >= 1
    item = next(i for i in cw if i["message_id"] == 789)
    assert item["last_chunk_index"] == 100
    assert item["last_position_seconds"] == 300.0
