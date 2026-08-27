import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import socketio

import time
from app.config import CORS_ORIGINS, FRONTEND_DIR, TEMP_UPLOAD_DIR
from app.socket_handlers import sio, broadcast_task_progress, broadcast_download_progress
from app.services.queue_manager import queue_manager
from app.services.download_manager import download_manager
from app.routes.history import init_db, record_task_history, router as history_router
from app.routes.chats import router as chats_router, preload_chats
from app.routes.upload import router as upload_router
from app.routes.download import router as download_router
from app.telegram_client import TelegramClientManager

# Configure root logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
)
logger = logging.getLogger("main")

def cleanup_stale_temp_files():
    """Removes leftover temporary files older than 2 hours on startup to prevent disk exhaustion."""
    try:
        now = time.time()
        for f in TEMP_UPLOAD_DIR.glob("*"):
            if f.is_file() and (now - f.stat().st_mtime) > 7200:
                try:
                    f.unlink()
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"Error during temp file cleanup: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI Lifespan Context Manager.
    Initializes SQLite DB, wires real-time socket events, starts queue workers,
    and initializes Telethon client.
    """
    logger.info("Initializing Telegram Web Uploader Backend...")

    # 1. Cleanup old orphaned temp files
    cleanup_stale_temp_files()

    # 2. Initialize SQLite Database
    await init_db()

    # 2. Wire Queue Callbacks to Socket.IO and SQLite DB
    queue_manager.set_callbacks(
        progress_emitter=broadcast_task_progress,
        db_logger=record_task_history
    )
    download_manager.set_emitter(broadcast_download_progress)

    # 3. Start Upload Queue Workers and Download Manager
    queue_manager.start_workers()
    download_manager.start()

    # 4. Attempt to initialize Telethon MTProto Client if session/credentials exist
    try:
        if await TelegramClientManager.is_authorized():
            me_info = await TelegramClientManager.get_me_info()
            logger.info(f"Telegram client ready: {me_info.get('first_name')} (@{me_info.get('username')})")
            import asyncio
            asyncio.create_task(preload_chats())
        else:
            logger.warning("Telegram client is NOT authorized yet. Please run 'python setup_auth.py'.")
    except Exception as e:
        logger.warning(f"Could not auto-start Telegram client: {e}")

    yield

    # Clean shutdown
    logger.info("Shutting down backend...")
    await queue_manager.stop_workers()
    await TelegramClientManager.disconnect()
    logger.info("Shutdown complete.")


# Create FastAPI application
app = FastAPI(
    title="Telegram Web Uploader API",
    description="FastAPI Backend for uploading large files to Telegram via MTProto with real-time progress",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS Middleware (Applied before socket wrapper)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API Routers
app.include_router(upload_router)
app.include_router(download_router)
app.include_router(chats_router)
app.include_router(history_router)

# Mount Frontend Static Assets
if FRONTEND_DIR.exists():
    app.mount("/css", StaticFiles(directory=str(FRONTEND_DIR / "css")), name="css")
    app.mount("/js", StaticFiles(directory=str(FRONTEND_DIR / "js")), name="js")
    if (FRONTEND_DIR / "assets").exists():
        app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR / "assets")), name="assets")

    @app.api_route("/", methods=["GET", "HEAD"])
    async def serve_index():
        return FileResponse(FRONTEND_DIR / "index.html")

# Create ASGI application combining Socket.IO and FastAPI
socket_app = socketio.ASGIApp(
    socketio_server=sio,
    other_asgi_app=app,
    socketio_path="socket.io"
)
