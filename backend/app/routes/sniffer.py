import logging
from typing import Optional, List, Union
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.services.sniffer_service import sniffer_service

logger = logging.getLogger("sniffer_route")

router = APIRouter(prefix="/api/sniffer", tags=["sniffer"])

class ChannelRequest(BaseModel):
    channel: str

@router.get("/status")
async def get_sniffer_status():
    """Returns current status of sniffer service, watched channels, and detected managers."""
    return sniffer_service.get_status()

@router.post("/channels/add")
async def add_watched_channel(req: ChannelRequest):
    """Add a channel/chat to watched list."""
    if not req.channel.strip():
        raise HTTPException(status_code=400, detail="Channel ID or username cannot be empty.")
    
    added = sniffer_service.add_channel(req.channel.strip())
    return {
        "success": added,
        "channel": req.channel.strip(),
        "active_channels": [str(c) for c in sniffer_service.active_channels]
    }

@router.post("/channels/remove")
async def remove_watched_channel(req: ChannelRequest):
    """Remove a channel/chat from watched list."""
    removed = sniffer_service.remove_channel(req.channel.strip())
    return {
        "success": removed,
        "channel": req.channel.strip(),
        "active_channels": [str(c) for c in sniffer_service.active_channels]
    }

@router.get("/feed")
async def get_sniffer_feed():
    """Returns recent captured & auto-dispatched files feed."""
    return sniffer_service.recent_feed
