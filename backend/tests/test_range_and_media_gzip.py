import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from app.routes.proxy import parse_http_range
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
