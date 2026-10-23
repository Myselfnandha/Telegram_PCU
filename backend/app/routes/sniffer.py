import logging
from typing import Optional, List, Union
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.services.sniffer_service import sniffer_service

logger = logging.getLogger("sniffer_route")

router = APIRouter(prefix="/api/sniffer", tags=["sniffer"])

class ChannelRequest(BaseModel):
    channel: str
    limit: Optional[int] = 15

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

@router.post("/process_chat")
async def process_chat_media(req: ChannelRequest):
    """Scan and process latest media messages in a given chat (useful for testing or manual triggers)."""
    from app.telegram_client import TelegramClientManager
    client = await TelegramClientManager.get_client()
    try:
        clean_ch = req.channel.strip()
        entity_id = int(clean_ch) if clean_ch.lstrip("-").isdigit() else clean_ch
        entity = await client.get_entity(entity_id)
        scan_limit = max(1, min(req.limit or 15, 50))
        messages = await client.get_messages(entity, limit=scan_limit)
        processed = 0
        # Process from oldest to newest so episodes download in order
        for m in reversed(messages):
            if m.media and hasattr(m, "file") and m.file:
                class EventWrapper:
                    def __init__(self, message, chat_entity):
                        self.message = message
                        self.chat_id = chat_entity.id
                        self.chat = chat_entity
                    async def reply(self, text):
                        try:
                            return await client.send_message(self.chat_id, text, reply_to=self.message.id)
                        except Exception:
                            return None

                wrapper = EventWrapper(m, entity)
                await sniffer_service._process_incoming_media(wrapper)
                # Flush group immediately
                raw_name = m.file.name or f"media_{m.id}.bin"
                from app.services.sniffer_service import auto_rename
                fname = auto_rename(raw_name)
                group_key = sniffer_service._group_key(fname, getattr(m, "grouped_id", None))
                buf_key = (entity.id, group_key)
                if buf_key in sniffer_service._quality_timers:
                    sniffer_service._quality_timers[buf_key].cancel()
                await sniffer_service._flush_quality_group(buf_key, force=True)
                processed += 1
        return {"success": True, "processed": processed}
    except Exception as e:
        logger.error(f"Error processing chat media: {e}")
        raise HTTPException(status_code=500, detail=str(e))

