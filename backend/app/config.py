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
FRONTEND_DIR = ROOT_DIR / "frontend"
ASSETS_DIR = ROOT_DIR / "assets"
if not ASSETS_DIR.exists():
    ASSETS_DIR = BACKEND_DIR / "assets"

# Ensure runtime directories exist
TEMP_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
SESSION_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Database
DB_PATH = DATA_DIR / "history.db"

# Telegram API Config
API_ID_RAW = os.getenv("TG_API_ID", os.getenv("API_ID", "0"))
try:
    API_ID = int(API_ID_RAW) if API_ID_RAW.strip() else 0
except ValueError:
    API_ID = 0

API_HASH = os.getenv("TG_API_HASH", os.getenv("API_HASH", "")).strip()
PHONE_NUMBER = os.getenv("TG_PHONE", os.getenv("PHONE_NUMBER", "")).strip()
BOT_TOKEN = os.getenv("TG_BOT_TOKEN", os.getenv("BOT_TOKEN", "")).strip()
SESSION_NAME = os.getenv("TG_SESSION_NAME", "tg_suite_user_session").strip()
SESSION_FILE_PATH = SESSION_DIR / SESSION_NAME

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
HOST = os.getenv("HOST", "0.0.0.0")
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
    "*"
]

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
    """Updates key-value pairs in the .env file."""
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
            if key in updates:
                new_lines.append(f"{key}={updates[key]}\n")
                keys_written.add(key)
                continue
        new_lines.append(line)

    for k, v in updates.items():
        if k not in keys_written:
            new_lines.append(f"{k}={v}\n")

    with open(target, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    
    # Reload environment
    load_dotenv(target, override=True)
