import logging
import asyncio
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import ClientDisconnect
import socketio

from app.config import CORS_ORIGINS, FRONTEND_DIR, TEMP_UPLOAD_DIR, PORT
from app.socket_handlers import (
    sio,
    broadcast_task_progress,
    broadcast_download_progress,
    broadcast_sniffer_event
)
from app.services.queue_manager import queue_manager
from app.services.download_manager import download_manager
from app.services.sniffer_service import sniffer_service
from app.routes.history import init_db, record_task_history, router as history_router
from app.routes.chats import router as chats_router, preload_chats
from app.routes.upload import router as upload_router
from app.routes.download import router as download_router
from app.routes.proxy import router as proxy_router
from app.routes.sniffer import router as sniffer_router
from app.routes.settings import router as settings_router
from app.routes.system import router as system_router, get_system_telemetry
from app.routes.tgweb import router as tgweb_router
from app.telegram_client import TelegramClientManager

# Configure root logger with file handler
from logging.handlers import RotatingFileHandler
from app.config import DATA_DIR

log_file_path = DATA_DIR / "tg_power_suite.log"
file_handler = RotatingFileHandler(
    str(log_file_path),
    maxBytes=10 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8"
)
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s"))
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s"))

class EndpointFilter(logging.Filter):
    SILENT_PATHS = (
        "/api/governor/status",
        "/api/auth/status",
        "/api/history",
        "/api/system/network",
        "/socket.io/",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(path in msg for path in self.SILENT_PATHS)

logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, stream_handler],
    force=True
)
logging.getLogger("uvicorn.access").addFilter(EndpointFilter())
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

async def _telemetry_broadcast_loop():
    """Periodically broadcasts real-time system & throughput telemetry to all connected clients."""
    while True:
        try:
            await asyncio.sleep(2)
            stats = get_system_telemetry()
            await sio.emit("telemetry:stats", stats)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"Telemetry broadcast err: {e}")

async def _periodic_maintenance_loop():
    """Periodically sweeps interrupted temp uploads and stream cache LRU/TTL eviction every hour."""
    while True:
        try:
            await asyncio.sleep(3600)
            cleanup_stale_temp_files()
            from app.services.stream_cache import stream_cache_service
            stream_cache_service.evict_if_needed()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"Periodic storage maintenance notice: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI Lifespan Context Manager.
    Initializes SQLite DB, wires real-time socket events, starts queue workers,
    initializes Telethon client, and connects sniffer service.
    """
    logger.info("Initializing TG Power Suite Backend...")

    # 1. Cleanup old orphaned temp files & unmerged stream blocks
    cleanup_stale_temp_files()
    try:
        from app.services.stream_cache import stream_cache_service
        stream_cache_service.evict_if_needed()
    except Exception as e:
        logger.debug(f"Initial stream cache sweep notice: {e}")

    # 2. Initialize SQLite Database
    await init_db()

    # 3. Wire Callbacks to Socket.IO and SQLite DB
    queue_manager.set_callbacks(
        progress_emitter=broadcast_task_progress,
        db_logger=record_task_history
    )
    download_manager.set_emitter(broadcast_download_progress)
    sniffer_service.set_socket_emitter(broadcast_sniffer_event)

    # 4. Start Upload Queue Workers and Download Manager
    queue_manager.start_workers()
    download_manager.start()

    # 5. Start Telemetry Broadcast Loop & Storage Maintenance Loop
    telemetry_task = asyncio.create_task(_telemetry_broadcast_loop())
    maintenance_task = asyncio.create_task(_periodic_maintenance_loop())

    # 6. Attempt to initialize Telethon MTProto Client if session/credentials exist
    try:
        if await TelegramClientManager.is_authorized():
            client = await TelegramClientManager.get_client()
            me_info = await TelegramClientManager.get_me_info()
            if me_info:
                logger.info(f"Telegram client ready: {me_info.get('first_name')} (@{me_info.get('username')})")
            
            # Attach sniffer to client
            sniffer_service.attach_to_client(client)
        else:
            logger.info("Telegram client is not authorized yet. Log in via setup_auth.py or Web Settings.")
    except Exception as e:
        logger.warning(f"Could not auto-start Telegram client: {e}")

    yield

    # Clean shutdown
    logger.info("Shutting down TG Power Suite backend...")
    telemetry_task.cancel()
    maintenance_task.cancel()
    await queue_manager.stop_workers()
    await TelegramClientManager.disconnect()
    try:
        from app.routes.tgweb import _client as tgweb_client
        if tgweb_client and not tgweb_client.is_closed:
            await tgweb_client.aclose()
    except Exception:
        pass
    logger.info("Shutdown complete.")


# Create FastAPI application
app = FastAPI(
    title="TG Power Suite API",
    description="FastAPI Backend for TG Power Suite: Turbo Uploader, FDM Proxy, Channel Sniffer & Remote Downloader",
    version="2.0.0",
    lifespan=lifespan
)

from starlette.middleware.gzip import GZipMiddleware


class SmartStaticCacheASGIMiddleware:
    """
    High-Performance Static Asset Caching Middleware:
    1. Immutable Cache (1 year) for content-hashed assets (/assets/*, fonts, bundled JS/CSS).
    2. Zero-Cache (must-revalidate) for dynamic HTML entry points (/, index.html, sw.js)
       so code updates are picked up instantly without stale browser caches.
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            # Hashed assets in /assets/ or static font files can be aggressively cached forever
            if path.startswith("/assets/") or path.endswith((".woff2", ".woff", ".ttf")):
                async def send_wrapper(message):
                    if message["type"] == "http.response.start":
                        headers = list(message.get("headers", []))
                        headers.append((b"cache-control", b"public, max-age=31536000, immutable"))
                        message["headers"] = headers
                    await send(message)
                await self.app(scope, receive, send_wrapper)
                return

            # Dynamic entrypoints (index.html, sw.js, root) must never be cached
            if path == "/" or path.endswith((".html", "manifest.json", "sw.js")):
                async def send_wrapper(message):
                    if message["type"] == "http.response.start":
                        headers = list(message.get("headers", []))
                        headers.append((b"cache-control", b"no-cache, no-store, must-revalidate"))
                        headers.append((b"pragma", b"no-cache"))
                        headers.append((b"expires", b"0"))
                        message["headers"] = headers
                    await send(message)
                await self.app(scope, receive, send_wrapper)
                return

        await self.app(scope, receive, send)


class ClientDisconnectSuppressorMiddleware:
    """
    Prevents noisy ASGI tracebacks when media players (VLC, mpv, mobile)
    close or reset HTTP Range connections during seek or pause operations.
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        try:
            await self.app(scope, receive, send)
        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError, ClientDisconnect):
            if scope["type"] == "http":
                # Yield control to let asyncio loop process transport disconnection callbacks
                # so that Uvicorn marks the connection as disconnected before finalizing.
                await asyncio.sleep(0)
                return
            raise
        except RuntimeError as re:
            if scope["type"] == "http" and "Response content shorter than Content-Length" in str(re):
                return
            raise


app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(ClientDisconnectSuppressorMiddleware)
app.add_middleware(SmartStaticCacheASGIMiddleware)

# Add CORS Middleware
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
app.include_router(proxy_router)
app.include_router(sniffer_router)
app.include_router(settings_router)
app.include_router(chats_router)
app.include_router(history_router)
app.include_router(system_router)
app.include_router(tgweb_router)

# Mount Frontend Static Assets
if FRONTEND_DIR.exists():
    if (FRONTEND_DIR / "css").exists():
        app.mount("/css", StaticFiles(directory=str(FRONTEND_DIR / "css")), name="css")
    if (FRONTEND_DIR / "js").exists():
        app.mount("/js", StaticFiles(directory=str(FRONTEND_DIR / "js")), name="js")
    if (FRONTEND_DIR / "assets").exists():
        app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR / "assets")), name="assets")

    @app.api_route("/", methods=["GET", "HEAD"])
    async def serve_index():
        response = FileResponse(FRONTEND_DIR / "index.html")
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response

    @app.get("/manifest.json")
    async def serve_manifest():
        return FileResponse(FRONTEND_DIR / "manifest.json", media_type="application/manifest+json")

    @app.api_route("/favicon.svg", methods=["GET", "HEAD"])
    @app.api_route("/favicon.ico", methods=["GET", "HEAD"])
    async def serve_favicon():
        fav = FRONTEND_DIR / "favicon.svg"
        if not fav.exists():
            fav = FRONTEND_DIR / "assets" / "icon.svg"
        if fav.exists():
            return FileResponse(fav, media_type="image/svg+xml")
        raise HTTPException(status_code=404, detail="Favicon not found")

    @app.api_route("/icons.svg", methods=["GET", "HEAD"])
    async def serve_icons():
        ic = FRONTEND_DIR / "icons.svg"
        if ic.exists():
            return FileResponse(ic, media_type="image/svg+xml")
        raise HTTPException(status_code=404, detail="Icons not found")

    @app.get("/sw.js")
    async def serve_sw():
        res = FileResponse(FRONTEND_DIR / "sw.js", media_type="application/javascript")
        res.headers["Service-Worker-Allowed"] = "/"
        res.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return res

# Create ASGI application combining Socket.IO and FastAPI
socket_app = socketio.ASGIApp(
    socketio_server=sio,
    other_asgi_app=app,
    socketio_path="socket.io"
)
