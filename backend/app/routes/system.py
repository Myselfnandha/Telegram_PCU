import time
import os
import psutil
import logging
from typing import Dict, Any
from fastapi import APIRouter
from app.services.queue_manager import queue_manager
from app.services.sniffer_service import sniffer_service
from app.telegram_client import TelegramClientManager

logger = logging.getLogger("system_route")
router = APIRouter(prefix="/api/system", tags=["system"])

SERVER_START_TIME = time.time()

def get_system_telemetry() -> Dict[str, Any]:
    """Gathers real-time performance and application metrics."""
    # Memory
    mem = psutil.virtual_memory()
    ram_used_mb = round((mem.total - mem.available) / (1024 * 1024), 1)
    ram_total_mb = round(mem.total / (1024 * 1024), 1)
    ram_percent = mem.percent

    # CPU
    cpu_percent = psutil.cpu_percent(interval=None)

    # Active uploads
    all_tasks = queue_manager.get_all_tasks()
    active_uploads = [t for t in all_tasks if getattr(t, "status", None) and t.status.value == "uploading"]
    total_up_speed = sum(getattr(t, "speed", 0.0) for t in active_uploads)

    # Active proxy streams & feed count
    active_streams_count = getattr(sniffer_service, "active_streams_count", 0)
    total_stream_bytes = getattr(sniffer_service, "total_streamed_bytes", 0)

    # MTProto info
    me_info = TelegramClientManager._me_info
    auth_status = bool(me_info is not None)

    uptime_sec = round(time.time() - SERVER_START_TIME, 1)

    return {
        "cpu_percent": cpu_percent,
        "ram_used_mb": ram_used_mb,
        "ram_total_mb": ram_total_mb,
        "ram_percent": ram_percent,
        "active_uploads_count": len(active_uploads),
        "total_tasks_count": len(all_tasks),
        "upload_speed_bps": round(total_up_speed, 1),
        "active_proxy_streams": active_streams_count,
        "total_streamed_bytes": total_stream_bytes,
        "is_authenticated": auth_status,
        "user_name": me_info.get("name", "") if me_info else "",
        "user_id": me_info.get("id", None) if me_info else None,
        "uptime_seconds": uptime_sec,
        "server_time": time.time()
    }

@router.get("/stats")
async def get_stats():
    """Returns real-time system and throughput telemetry metrics."""
    return get_system_telemetry()
