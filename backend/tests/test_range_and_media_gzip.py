import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from app.routes.proxy import parse_http_range, ALLOWED_VLC_COMMANDS
from fastapi.testclient import TestClient
from app.main import app
from app.services.playback_tracker import PlaybackTracker


def test_parse_http_range_no_header():
    start, end, status, err = parse_http_range("", 1000)
    assert status == 200
    assert start == 0
    assert end == 999
    assert err is None


def test_parse_http_range_standard():
    start, end, status, err = parse_http_range("bytes=100-200", 1000)
    assert status == 206
    assert start == 100
    assert end == 200
    assert err is None


def test_parse_http_range_open_ended():
    start, end, status, err = parse_http_range("bytes=250-", 1000)
    assert status == 206
    assert start == 250
    assert end == 999
    assert err is None


def test_parse_http_range_suffix():
    start, end, status, err = parse_http_range("bytes=-500", 1000)
    assert status == 206
    assert start == 500
    assert end == 999
    assert err is None


def test_parse_http_range_suffix_larger_than_file():
    start, end, status, err = parse_http_range("bytes=-2000", 1000)
    assert status == 206
    assert start == 0
    assert end == 999
    assert err is None


def test_parse_http_range_end_clamped():
    start, end, status, err = parse_http_range("bytes=500-99999", 1000)
    assert status == 206
    assert start == 500
    assert end == 999
    assert err is None


def test_parse_http_range_start_past_eof():
    start, end, status, err = parse_http_range("bytes=1000-", 1000)
    assert status == 416
    assert err is not None
    assert err.status_code == 416
    assert err.headers.get("Content-Range") == "bytes */1000"


def test_parse_http_range_end_before_start():
    start, end, status, err = parse_http_range("bytes=500-200", 1000)
    assert status == 416
    assert err is not None
    assert err.status_code == 416
    assert err.headers.get("Content-Range") == "bytes */1000"


def test_parse_http_range_malformed():
    for malformed in ["bytes=", "bytes=-", "bytes=abc-def", "not-a-range", "chars=0-10"]:
        start, end, status, err = parse_http_range(malformed, 1000)
        assert status == 416
        assert err is not None
        assert err.status_code == 416
        assert err.headers.get("Content-Range") == "bytes */1000"


def test_vlc_security_play_url_validation():
    client = TestClient(app)

    # 1. file:// scheme must be rejected with 400
    r_file = client.post("/api/media/vlc/play", json={"stream_url": "file:///etc/passwd"})
    assert r_file.status_code == 400
    assert "not allowed" in r_file.json()["detail"]

    # 2. Arbitrary external domain must be rejected with 400
    r_ext = client.post("/api/media/vlc/play", json={"stream_url": "http://evil.com/malicious.mp4"})
    assert r_ext.status_code == 400
    assert "not allowed" in r_ext.json()["detail"]

    # 3. Path outside /dl/ and /stream/ must be rejected with 400
    r_bad_path = client.post("/api/media/vlc/play", json={"stream_url": "http://127.0.0.1:8088/secret/data"})
    assert r_bad_path.status_code == 400
    assert "Path must start" in r_bad_path.json()["detail"]


def test_vlc_control_command_validation():
    client = TestClient(app)

    # Disallowed command
    r_bad = client.post("/api/media/vlc/control", json={"command": "exec_cmd"})
    assert r_bad.status_code == 400
    assert "not allowed" in r_bad.json()["detail"]

    # Allowed command
    assert "play" in ALLOWED_VLC_COMMANDS
    assert "pause" in ALLOWED_VLC_COMMANDS
    assert "seek" in ALLOWED_VLC_COMMANDS
    assert "volume" in ALLOWED_VLC_COMMANDS


def test_playback_tracker_explicit_reset(tmp_path, monkeypatch):
    test_db = tmp_path / "test_reset.db"
    monkeypatch.setattr("app.services.playback_tracker.DB_PATH", test_db)
    tracker = PlaybackTracker()

    # Seed position at 500s
    tracker.update_progress(
        chat_id="reset_chat",
        message_id=101,
        filename="reset_test.mkv",
        last_position_seconds=500.0,
        duration_seconds=1000.0
    )
    prog = tracker.get_playback_progress("reset_chat", 101)
    assert prog["last_position_seconds"] == 500.0

    # Explicit reset to 0.0 with is_reset=True
    tracker.update_progress(
        chat_id="reset_chat",
        message_id=101,
        filename="reset_test.mkv",
        last_position_seconds=0.0,
        duration_seconds=1000.0,
        is_reset=True
    )
    prog_reset = tracker.get_playback_progress("reset_chat", 101)
    assert prog_reset["last_position_seconds"] == 0.0
    assert prog_reset["progress_percent"] == 0.0
