import os
from pathlib import Path
from dotenv import load_dotenv

# Base paths
APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR.parent
ROOT_DIR = BACKEND_DIR.parent

# Load environment variables
load_dotenv(BACKEND_DIR / ".env")
load_dotenv(ROOT_DIR / ".env")

# Directories
TEMP_UPLOAD_DIR = BACKEND_DIR / "temp_uploads"
SESSION_DIR = BACKEND_DIR / "sessions"
DATA_DIR = BACKEND_DIR / "data"
DOWNLOADS_DIR = BACKEND_DIR / "downloads"
FRONTEND_DIR = ROOT_DIR / "frontend"

# Ensure runtime directories exist
TEMP_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
SESSION_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Database
DB_PATH = DATA_DIR / "history.db"

# Telegram API Config
API_ID_RAW = os.getenv("TG_API_ID", "0")
try:
    API_ID = int(API_ID_RAW) if API_ID_RAW.strip() else 0
except ValueError:
    API_ID = 0

API_HASH = os.getenv("TG_API_HASH", "").strip()
PHONE_NUMBER = os.getenv("TG_PHONE", "").strip()
SESSION_NAME = os.getenv("TG_SESSION_NAME", "tg_uploader").strip()
SESSION_FILE_PATH = SESSION_DIR / SESSION_NAME

# Upload Thresholds & Chunking
# 2 GB hard limit for Telegram MTProto
MAX_TELEGRAM_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2,147,483,648 bytes
# 1.9 GB split threshold for safety margin
SPLIT_THRESHOLD = int(os.getenv("SPLIT_THRESHOLD_BYTES", str(int(1.9 * 1024 * 1024 * 1024))))
# 500 MB stream threshold (files smaller than this can be streamed directly in memory or direct buffer)
STREAM_THRESHOLD = int(os.getenv("STREAM_THRESHOLD_BYTES", str(500 * 1024 * 1024)))
# Read buffer chunks
CHUNK_READ_SIZE = 4 * 1024 * 1024  # 4 MB

# Server Config
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8088"))
SECRET_KEY = os.getenv("SECRET_KEY", "tg-web-uploader-secure-key-default")
CORS_ORIGINS = [
    "http://localhost:8088",
    "http://127.0.0.1:8088",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "*"
]
