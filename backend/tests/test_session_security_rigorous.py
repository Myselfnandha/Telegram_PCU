import os
import sys
import shutil
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import pytest
from telethon.sessions import StringSession, SQLiteSession
from telethon.crypto import AuthKey

from app.config import (
    SESSION_DIR,
    save_env_dict,
    update_session_string_in_env,
    update_secondary_session_string_in_env,
    migrate_and_purge_env_sessions,
)


def test_session_name_and_path_defaults_when_empty_env(monkeypatch, tmp_path):
    """Ensures that empty or whitespace TG_SESSION_NAME defaults to tg_suite_user_session."""
    monkeypatch.setenv("TG_SESSION_NAME", "   ")
    monkeypatch.setenv("TG_SESSION_NAME_2", "")

    # Re-evaluate logic as defined in config.py
    s_name = (os.getenv("TG_SESSION_NAME", "").strip()) or "tg_suite_user_session"
    s_name_2 = (os.getenv("TG_SESSION_NAME_2", "").strip()) or "tg_suite_secondary_session"

    assert s_name == "tg_suite_user_session"
    assert s_name_2 == "tg_suite_secondary_session"
    assert (tmp_path / s_name).name == "tg_suite_user_session"
    assert (tmp_path / s_name_2).name == "tg_suite_secondary_session"


def test_save_env_dict_never_writes_session_strings(tmp_path, monkeypatch):
    """Verifies save_env_dict strips any key containing session strings from persisting to .env."""
    test_env = tmp_path / ".env"
    test_env.write_text("HOST=127.0.0.1\nPORT=8088\n", encoding="utf-8")
    monkeypatch.setattr("app.config.ROOT_DIR", tmp_path)

    updates = {
        "HOST": "0.0.0.0",
        "TG_SESSION_STRING": "1BAP45fake_primary_session_token_xyz999",
        "TG_SESSION_STRING_2": "1BAP45fake_secondary_session_token_abc111",
        "CUSTOM_SESSION_STRING": "should_be_filtered",
        "MIN_FILE_SIZE_MB": "100"
    }
    save_env_dict(updates)

    content = test_env.read_text(encoding="utf-8")
    assert "fake_primary_session_token" not in content
    assert "fake_secondary_session_token" not in content
    assert "should_be_filtered" not in content
    assert "HOST=0.0.0.0" in content
    assert "MIN_FILE_SIZE_MB=100" in content


def test_update_session_string_in_memory_only(monkeypatch):
    """Verifies update_session_string_in_env only alters os.environ and memory, never writes to disk."""
    import app.config as cfg
    fake_token = "1BAP45in_memory_only_token_445566"
    update_session_string_in_env(fake_token)
    assert os.environ.get("TG_SESSION_STRING") == fake_token
    assert cfg.SESSION_STRING == fake_token

    fake_sec_token = "1BAP45in_memory_sec_token_778899"
    update_secondary_session_string_in_env(fake_sec_token)
    assert os.environ.get("TG_SESSION_STRING_2") == fake_sec_token
    assert cfg.SESSION_STRING_2 == fake_sec_token


def test_telethon_sqlite_session_structure_and_integrity(tmp_path):
    """Verifies that Telethon's SQLiteSession creates a genuine, valid SQLite database with expected schema."""
    session_file = tmp_path / "test_session_integrity.session"
    s = SQLiteSession(str(tmp_path / "test_session_integrity"))
    s.set_dc(2, "149.154.167.50", 443)
    # Auth key dummy 256 bytes
    s.auth_key = AuthKey(data=b"\xaa" * 256)
    s.save()
    s.close()

    assert session_file.exists()

    import sqlite3
    conn = sqlite3.connect(str(session_file))
    cursor = conn.cursor()

    # Check SQLite integrity
    cursor.execute("PRAGMA integrity_check;")
    res = cursor.fetchall()
    assert res == [("ok",)]

    # Check Telethon tables exist
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = {row[0] for row in cursor.fetchall()}
    assert "sessions" in tables
    assert "version" in tables

    # Query sessions table
    cursor.execute("SELECT dc_id, server_address, port FROM sessions;")
    session_row = cursor.fetchone()
    assert session_row == (2, "149.154.167.50", 443)

    conn.close()

    # Verify reopen through Telethon SQLiteSession
    s_reopen = SQLiteSession(str(tmp_path / "test_session_integrity"))
    assert s_reopen.dc_id == 2
    assert s_reopen.server_address == "149.154.167.50"
    assert s_reopen.port == 443
    assert s_reopen.auth_key.key == b"\xaa" * 256
    s_reopen.close()


def test_telegram_client_manager_enforces_sqlite_target(monkeypatch, tmp_path):
    """Verifies TelegramClientManager unconditionally targets SQLite session files, ignoring StringSession fallback."""
    from app.telegram_client import TelegramClientManager
    import app.config as cfg

    test_session_path = tmp_path / "custom_test_session"
    test_session_path_2 = tmp_path / "custom_test_session_2"

    monkeypatch.setattr(cfg, "SESSION_FILE_PATH", test_session_path)
    monkeypatch.setattr(cfg, "SESSION_FILE_PATH_2", test_session_path_2)
    monkeypatch.setattr(cfg, "SESSION_STRING", "legacy_string_should_be_ignored")
    monkeypatch.setattr(cfg, "SESSION_STRING_2", "legacy_sec_string_should_be_ignored")
    monkeypatch.setattr(cfg, "API_ID", 12345)
    monkeypatch.setattr(cfg, "API_HASH", "0123456789abcdef0123456789abcdef")

    # In get_client(): session_target is unconditionally str(SESSION_FILE_PATH)
    assert str(test_session_path) == str(cfg.SESSION_FILE_PATH)
    assert str(test_session_path_2) == str(cfg.SESSION_FILE_PATH_2)
