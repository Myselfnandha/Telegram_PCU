"""Unit tests verifying end-to-end resuming architecture."""
import sys
import time
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.playback_tracker import playback_tracker


def test_resume_dual_schema_normalization():
    """Verify Continue Watching returns dual backend/frontend schema keys."""
    chat_id = "test_resume_chat"
    msg_id = 998877

    playback_tracker.update_progress(
        chat_id=chat_id,
        message_id=msg_id,
        filename="Test.Movie.2026.1080p.mkv",
        last_position_seconds=1500.0,
        duration_seconds=3000.0,
        file_size=1000000000,
        thumbnail_url=f"/dl/{chat_id}/{msg_id}/thumb"
    )

    items = playback_tracker.get_continue_watching(limit=50)
    matched = [it for it in items if str(it["chat_id"]) == chat_id and it["message_id"] == msg_id]
    assert len(matched) == 1
    item = matched[0]

    # Verify backend schema keys
    assert item["last_position_seconds"] == 1500.0
    assert item["duration_seconds"] == 3000.0
    assert item["progress_percent"] == 50.0

    # Verify frontend normalized schema keys
    assert item["playback_seconds"] == 1500.0
    assert item["total_seconds"] == 3000.0
    assert item["percent"] == 50.0
    assert item["thumb_url"] == f"/dl/{chat_id}/{msg_id}/thumb"
    assert "last_watched" in item

    # Cleanup
    playback_tracker.remove_progress(chat_id, msg_id)


def test_resume_prevents_accidental_initial_overwrite():
    """Verify that playing 5s from the start does NOT wipe out a 1-hour watched position."""
    chat_id = "test_guard_chat"
    msg_id = 112233

    # User watched 45 minutes (2700s)
    playback_tracker.update_progress(
        chat_id=chat_id,
        message_id=msg_id,
        filename="Guarded.Episode.S01E01.mkv",
        last_position_seconds=2700.0,
        duration_seconds=3600.0,
        file_size=2000000000
    )

    # A delayed client starts from 00:00 and reports 5 seconds without reset
    playback_tracker.update_progress(
        chat_id=chat_id,
        message_id=msg_id,
        filename="Guarded.Episode.S01E01.mkv",
        last_position_seconds=5.0,
        duration_seconds=3600.0,
        file_size=2000000000,
        is_reset=False
    )

    prog = playback_tracker.get_playback_progress(chat_id, msg_id)
    assert prog is not None
    # Watch position must stay preserved at 2700s!
    assert prog["last_position_seconds"] == 2700.0
    assert prog["playback_seconds"] == 2700.0

    # If is_reset=True is explicitly passed, it should reset
    playback_tracker.update_progress(
        chat_id=chat_id,
        message_id=msg_id,
        filename="Guarded.Episode.S01E01.mkv",
        last_position_seconds=0.0,
        duration_seconds=3600.0,
        file_size=2000000000,
        is_reset=True
    )
    prog_reset = playback_tracker.get_playback_progress(chat_id, msg_id)
    assert prog_reset["last_position_seconds"] == 0.0

    # Cleanup
    playback_tracker.remove_progress(chat_id, msg_id)


def test_resume_byte_progress_infers_seconds_and_percent():
    """Verify that external players streaming byte ranges update inferred seconds and appear in Continue Watching."""
    chat_id = "test_byte_chat"
    msg_id = 445566
    file_size = 1000 * 1024 * 1024  # 1GB
    streamed_offset = 500 * 1024 * 1024  # 500MB (50%)

    playback_tracker.update_byte_progress(
        chat_id=chat_id,
        message_id=msg_id,
        filename="VLC.Streamed.Movie.2026.mkv",
        streamed_byte_offset=streamed_offset,
        total_file_size=file_size
    )

    prog = playback_tracker.get_playback_progress(chat_id, msg_id)
    assert prog is not None
    assert prog["last_byte_offset"] == streamed_offset
    assert prog["progress_percent"] == 50.0
    assert prog["percent"] == 50.0
    assert prog["last_position_seconds"] > 0
    assert prog["playback_seconds"] > 0

    # Cleanup
    playback_tracker.remove_progress(chat_id, msg_id)
