import logging
from typing import Dict, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.config import get_env_dict, save_env_dict
from app.services.manager_detector import detect_managers, MANAGER_LABELS

logger = logging.getLogger("settings_route")

router = APIRouter(prefix="/api/settings", tags=["settings"])

class SettingsUpdateRequest(BaseModel):
    settings: Dict[str, Any]

@router.get("")
async def get_settings():
    """Retrieve current settings from environment / .env file."""
    env_dict = get_env_dict()
    # Mask API Hash / Bot token if needed, or return raw for settings UI
    return {
        "TG_API_ID": env_dict.get("TG_API_ID", env_dict.get("API_ID", "")),
        "TG_API_HASH": env_dict.get("TG_API_HASH", env_dict.get("API_HASH", "")),
        "TG_PHONE": env_dict.get("TG_PHONE", env_dict.get("PHONE_NUMBER", "")),
        "TG_BOT_TOKEN": env_dict.get("TG_BOT_TOKEN", env_dict.get("BOT_TOKEN", "")),
        "PREFERRED_MANAGER": env_dict.get("PREFERRED_MANAGER", "auto"),
        "MIN_FILE_SIZE_MB": env_dict.get("MIN_FILE_SIZE_MB", "50"),
        "ALLOWED_EXT": env_dict.get("ALLOWED_EXT", ".mkv,.mp4,.avi,.mov,.flv,.wmv,.zip,.rar,.tar,.gz,.7z,.iso"),
        "KEYWORD_BLOCK": env_dict.get("KEYWORD_BLOCK", "sample,trailer,cam,ts,telesync,PRE-DVD"),
        "KEYWORD_ALLOW": env_dict.get("KEYWORD_ALLOW", ""),
        "ENABLE_NOTIFICATIONS": env_dict.get("ENABLE_NOTIFICATIONS", "true"),
        "NOTIFICATION_MODE": env_dict.get("NOTIFICATION_MODE", "downloads_only"),
        "TARGET_CHANNELS": env_dict.get("TARGET_CHANNELS", ""),
        "PORT": env_dict.get("PORT", "8088")
    }

@router.post("")
async def update_settings(req: SettingsUpdateRequest):
    """Update settings in .env file."""
    try:
        save_env_dict(req.settings)
        return {
            "success": True,
            "message": "Settings successfully saved to .env",
            "updated": list(req.settings.keys())
        }
    except Exception as e:
        logger.error(f"Failed to save settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/managers")
async def get_installed_managers():
    """Returns detected download managers on this system."""
    detected = detect_managers()
    result = []
    for mgr_id, path in detected.items():
        result.append({
            "id": mgr_id,
            "label": MANAGER_LABELS.get(mgr_id, mgr_id),
            "path": path
        })
    return {
        "detected": result,
        "count": len(result)
    }
