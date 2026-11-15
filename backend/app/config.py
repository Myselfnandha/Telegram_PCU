import os
import sys
from pathlib import Path
from typing import Dict, Any, List
from dotenv import load_dotenv

# Base paths
APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR.parent
ROOT_DIR = BACKEND_DIR.parent

# Load environment variables
ENV_FILE = ROOT_DIR / ".env"
if not ENV_FILE.exists():
    ENV_FILE = BACKEND_DIR / ".env"

load_dotenv(BACKEND_DIR / ".env")
load_dotenv(ROOT_DIR / ".env")

# Directories
TEMP_UPLOAD_DIR = BACKEND_DIR / "temp_uploads"
SESSION_DIR = BACKEND_DIR / "sessions"
DATA_DIR = BACKEND_DIR / "data"
DOWNLOADS_DIR = BACKEND_DIR / "downloads"
REUI_DIST = ROOT_DIR / "frontend-reui" / "dist"
FRONTEND_DIR = REUI_DIST if (REUI_DIST / "index.html").exists() else (ROOT_DIR / "frontend")
ASSETS_DIR = ROOT_DIR / "assets"
if not ASSETS_DIR.exists():
    ASSETS_DIR = BACKEND_DIR / "assets"

# Ensure runtime directories exist
TEMP_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
SESSION_DIR.mkdir(parents=True, exist_ok=True)
try:
    os.chmod(SESSION_DIR, 0o700)
    for sfile in SESSION_DIR.glob("*"):
        if sfile.is_file() and sfile.name != ".gitkeep":
            try:
                os.chmod(sfile, 0o600)
            except Exception:
                pass
except Exception:
    pass
DATA_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Point system temporary file directory to local workspace NVMe storage (prevents /tmp 7.5GB RAM exhaustion)
import tempfile
tempfile.tempdir = str(TEMP_UPLOAD_DIR)

# Database
DB_PATH = DATA_DIR / "history.db"
SUITE_DB_PATH = DATA_DIR / "tg_power_suite.db"
DISPATCH_PACING_SECS = float(os.getenv("DISPATCH_PACING_SECS", "1.5"))

# Telegram API Config
API_ID_RAW = os.getenv("TG_API_ID", os.getenv("API_ID", "0"))
try:
    API_ID = int(API_ID_RAW) if API_ID_RAW.strip() else 0
except ValueError:
    API_ID = 0

API_HASH = os.getenv("TG_API_HASH", os.getenv("API_HASH", "")).strip()
PHONE_NUMBER = os.getenv("TG_PHONE", os.getenv("PHONE_NUMBER", "")).strip()
BOT_TOKEN = os.getenv("TG_BOT_TOKEN", os.getenv("BOT_TOKEN", "")).strip()
SESSION_NAME = (os.getenv("TG_SESSION_NAME", "").strip()) or "tg_suite_user_session"
SESSION_FILE_PATH = SESSION_DIR / SESSION_NAME
SESSION_STRING = os.getenv("TG_SESSION_STRING", "").strip()

SESSION_NAME_2 = (os.getenv("TG_SESSION_NAME_2", "").strip()) or "tg_suite_secondary_session"
SESSION_FILE_PATH_2 = SESSION_DIR / SESSION_NAME_2
SESSION_STRING_2 = os.getenv("TG_SESSION_STRING_2", "").strip()
ENABLE_MULTI_ACCOUNT_TURBO = os.getenv("ENABLE_MULTI_ACCOUNT_TURBO", "true").lower() in ("1", "true", "yes")

STREAMING_RELAY_CHANNEL_ID_RAW = os.getenv("STREAMING_RELAY_CHANNEL_ID", "").strip()
try:
    STREAMING_RELAY_CHANNEL_ID = int(STREAMING_RELAY_CHANNEL_ID_RAW) if STREAMING_RELAY_CHANNEL_ID_RAW else 0
except ValueError:
    STREAMING_RELAY_CHANNEL_ID = 0

def get_env_dict() -> Dict[str, str]:
    """Reads .env file into key-value dictionary."""
    config: Dict[str, str] = {}
    target = ROOT_DIR / ".env"
    if not target.exists():
        target = BACKEND_DIR / ".env"
    if not target.exists():
        return config
    
    with open(target, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" in stripped:
                k, v = stripped.split("=", 1)
                config[k.strip()] = v.split(" #")[0].strip()
    return config


def save_env_dict(updates: Dict[str, Any]) -> None:
    """Updates key-value pairs in the .env file. Never writes plaintext session credentials."""
    # Security: Never persist plaintext Telegram session credentials into .env
    filtered_updates = {
        k: v for k, v in updates.items()
        if k not in ("TG_SESSION_STRING", "TG_SESSION_STRING_2") and not k.endswith("_SESSION_STRING")
    }
    if not filtered_updates:
        return

    target = ROOT_DIR / ".env"
    if not target.exists():
        target = BACKEND_DIR / ".env"
    
    existing_lines: List[str] = []
    if target.exists():
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            existing_lines = f.readlines()

    keys_written = set()
    new_lines = []

    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in filtered_updates:
                new_lines.append(f"{key}={filtered_updates[key]}\n")
                keys_written.add(key)
                continue
        new_lines.append(line)

    for k, v in filtered_updates.items():
        if k not in keys_written:
            new_lines.append(f"{k}={v}\n")

    with open(target, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    
    load_dotenv(target, override=True)


def set_multi_account_turbo(enabled: bool) -> None:
    """Updates ENABLE_MULTI_ACCOUNT_TURBO in runtime environment and .env file."""
    global ENABLE_MULTI_ACCOUNT_TURBO
    ENABLE_MULTI_ACCOUNT_TURBO = enabled
    os.environ["ENABLE_MULTI_ACCOUNT_TURBO"] = "true" if enabled else "false"
    save_env_dict({"ENABLE_MULTI_ACCOUNT_TURBO": "true" if enabled else "false"})


AUTO_SAVE_STREAM = os.getenv("TG_AUTO_SAVE_STREAM", "false").lower() in ("1", "true", "yes")
DEFAULT_SUBTITLE_LANG = os.getenv("DEFAULT_SUBTITLE_LANG", "eng,tam,hin").strip()


def update_session_string_in_env(session_string: str) -> None:
    """Updates in-memory session string for compatibility; NEVER persists plaintext credentials to .env."""
    global SESSION_STRING
    SESSION_STRING = session_string
    os.environ["TG_SESSION_STRING"] = session_string


def update_secondary_session_string_in_env(session_string: str) -> None:
    """Updates in-memory session string for compatibility; NEVER persists plaintext credentials to .env."""
    global SESSION_STRING_2
    SESSION_STRING_2 = session_string
    os.environ["TG_SESSION_STRING_2"] = session_string


def migrate_and_purge_env_sessions() -> None:
    """
    Safely migrates legacy StringSession strings from .env into SQLite .session files
    (with 0600 file permissions and 0700 directory permissions), then purges plaintext
    session credentials from .env.
    """
    from telethon.sessions import StringSession, SQLiteSession

    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(SESSION_DIR, 0o700)
    except Exception:
        pass

    primary_target = SESSION_FILE_PATH.with_suffix(".session") if not str(SESSION_FILE_PATH).endswith(".session") else SESSION_FILE_PATH
    if SESSION_STRING:
        if not primary_target.exists():
            try:
                src = StringSession(SESSION_STRING)
                if src.auth_key:
                    dst = SQLiteSession(str(SESSION_FILE_PATH))
                    dst.set_dc(src.dc_id, src.server_address, src.port)
                    dst.auth_key = src.auth_key
                    dst.save()
                    dst.close()
                    try:
                        os.chmod(primary_target, 0o600)
                    except Exception:
                        pass
            except Exception:
                pass

    sec_target = SESSION_FILE_PATH_2.with_suffix(".session") if not str(SESSION_FILE_PATH_2).endswith(".session") else SESSION_FILE_PATH_2
    if SESSION_STRING_2:
        if not sec_target.exists():
            try:
                src2 = StringSession(SESSION_STRING_2)
                if src2.auth_key:
                    dst2 = SQLiteSession(str(SESSION_FILE_PATH_2))
                    dst2.set_dc(src2.dc_id, src2.server_address, src2.port)
                    dst2.auth_key = src2.auth_key
                    dst2.save()
                    dst2.close()
                    try:
                        os.chmod(sec_target, 0o600)
                    except Exception:
                        pass
            except Exception:
                pass

    # Ensure all session files have 0600 permissions
    for sf in SESSION_DIR.glob("*.session"):
        try:
            os.chmod(sf, 0o600)
        except Exception:
            pass

    # Once SQLite session is confirmed valid on disk, purge plaintext string from .env files
    if primary_target.exists() and primary_target.stat().st_size > 0:
        for env_path in (BACKEND_DIR / ".env", ROOT_DIR / ".env"):
            if env_path.exists():
                try:
                    with open(env_path, "r", encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()
                    purged_lines = []
                    changed = False
                    for line in lines:
                        sline = line.strip()
                        if sline.startswith("TG_SESSION_STRING=") and not sline.startswith("TG_SESSION_STRING_2="):
                            purged_lines.append("TG_SESSION_STRING=\n")
                            changed = True
                        elif sline.startswith("TG_SESSION_STRING_2="):
                            purged_lines.append("TG_SESSION_STRING_2=\n")
                            changed = True
                        else:
                            purged_lines.append(line)
                    if changed:
                        with open(env_path, "w", encoding="utf-8") as f:
                            f.writelines(purged_lines)
                except Exception:
                    pass

# Auto-execute migration and purge of legacy plaintext session strings
try:
    migrate_and_purge_env_sessions()
except Exception:
    pass


def update_relay_channel_id_in_env(channel_id: int) -> None:
    global STREAMING_RELAY_CHANNEL_ID
    STREAMING_RELAY_CHANNEL_ID = channel_id
    os.environ["STREAMING_RELAY_CHANNEL_ID"] = str(channel_id)
    save_env_dict({"STREAMING_RELAY_CHANNEL_ID": str(channel_id)})




# Upload Thresholds & Chunking
# 2 GB hard limit for Telegram MTProto
MAX_TELEGRAM_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2,147,483,648 bytes
# 1.9 GB split threshold for safety margin
SPLIT_THRESHOLD = int(os.getenv("SPLIT_THRESHOLD_BYTES", str(int(1.9 * 1024 * 1024 * 1024))))
STREAM_THRESHOLD = int(os.getenv("STREAM_THRESHOLD_BYTES", str(500 * 1024 * 1024)))
CHUNK_READ_SIZE = 4 * 1024 * 1024  # 4 MB

# Proxy & Downloader Settings
PROXY_HOST = os.getenv("PROXY_HOST", "127.0.0.1")
PROXY_PORT = int(os.getenv("PROXY_PORT", "8088"))
MIN_FILE_SIZE_MB = int(os.getenv("MIN_FILE_SIZE_MB", "50"))
QUALITY_WAIT_SECS = int(os.getenv("QUALITY_WAIT_SECS", "30"))
ALLOWED_EXT = [x.strip().lower() for x in os.getenv("ALLOWED_EXT", ".mkv,.mp4,.avi,.mov,.flv,.wmv,.zip,.rar,.tar,.gz,.7z,.iso").split(",") if x.strip()]
KEYWORD_BLOCK = [x.strip().lower() for x in os.getenv("KEYWORD_BLOCK", "sample,trailer,cam,ts,telesync,PRE-DVD").split(",") if x.strip()]
KEYWORD_ALLOW = [x.strip().lower() for x in os.getenv("KEYWORD_ALLOW", "").split(",") if x.strip()]
TARGET_CHANNELS = [x.strip() for x in os.getenv("TARGET_CHANNELS", "").split(",") if x.strip()]
PREFERRED_MANAGER = os.getenv("PREFERRED_MANAGER", "auto").strip().lower()
ENABLE_NOTIFICATIONS = os.getenv("ENABLE_NOTIFICATIONS", "true").strip().lower() in ("true", "1", "yes", "on")
NOTIFICATION_MODE = os.getenv("NOTIFICATION_MODE", "downloads_only").strip().lower()

# Server Config
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8088"))
SECRET_KEY = os.getenv("SECRET_KEY", "tg-power-suite-secret-key")
CORS_ORIGINS = [
    f"http://localhost:{PORT}",
    f"http://127.0.0.1:{PORT}",
    "http://localhost:8088",
    "http://127.0.0.1:8088",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173"
]

