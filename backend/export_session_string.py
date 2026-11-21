#!/usr/bin/env python3
"""
Exports the current SQLite .session file as a portable Telethon StringSession.
Use this string in your Render.com environment variables as TG_SESSION_STRING
to keep your Telegram session alive permanently on ephemeral cloud containers.
"""
import sys
from pathlib import Path

# Resolve sessions directory
SCRIPT_DIR = Path(__file__).resolve().parent
SESSION_DIR = SCRIPT_DIR / "sessions"
SESSION_NAME = "tg_suite_user_session"
SESSION_FILE = SESSION_DIR / SESSION_NAME

def export_string_session():
    full_path = Path(f"{SESSION_FILE}.session")
    if not full_path.exists():
        print(f"❌ Session file not found at: {full_path}")
        print("💡 You can log in first via QR code or phone number.")
        sys.exit(1)

    try:
        from telethon.sessions import SQLiteSession, StringSession
        sqlite_sess = SQLiteSession(str(SESSION_FILE))
        string_sess = StringSession.save(sqlite_sess)
        
        if not string_sess:
            print("⚠️ Session exists but does not contain authorization keys.")
            sys.exit(1)

        print("\n" + "=" * 60)
        print("🔑 TELEGRAM STRING SESSION EXPORT")
        print("=" * 60)
        print("Copy the string below and paste it into Render.com")
        print("Environment Variable: TG_SESSION_STRING")
        print("-" * 60)
        print(string_sess)
        print("-" * 60 + "\n")
    except Exception as e:
        print(f"❌ Failed to extract session string: {e}")
        sys.exit(1)

if __name__ == "__main__":
    export_string_session()
