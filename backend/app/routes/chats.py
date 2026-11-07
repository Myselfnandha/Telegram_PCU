import os
import time
import asyncio
import logging
from typing import List, Optional, Union, Any, Dict
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Query
from telethon.tl.types import User, Channel, Chat
from telethon.tl.functions.contacts import GetContactsRequest
from app.models import ChatItem, AuthStatus
from app.telegram_client import TelegramClientManager

logger = logging.getLogger("chats")

router = APIRouter(prefix="/api", tags=["chats"])

# Cache chats in memory for 10 minutes to ensure instant response times
_chats_cache: List[ChatItem] = []
_cache_time: float = 0
CACHE_TTL = 600  # 10 minutes

async def preload_chats():
    """Background task to pre-fetch and warm up the dialogs cache on server startup."""
    try:
        if await TelegramClientManager.is_authorized():
            logger.info("Pre-warming Telegram dialogs cache in background...")
            await fetch_dialogs_from_telegram()
            logger.info(f"Pre-warmed {len(_chats_cache)} dialogs in cache.")
    except Exception as e:
        logger.debug(f"Pre-warming dialogs deferred: {e}")

async def fetch_dialogs_from_telegram() -> List[ChatItem]:
    global _chats_cache, _cache_time
    client = await TelegramClientManager.get_client()
    if not await client.is_user_authorized():
        raise HTTPException(
            status_code=401,
            detail="Telegram account is not authorized. Please run 'python setup_auth.py' in the terminal."
        )

    me = await client.get_me()
    
    # 1. Fetch main dialogs and archived dialogs (compact slice to save RAM)
    main_dialogs = await client.get_dialogs(limit=100)
    try:
        archived_dialogs = await client.get_dialogs(folder=1, limit=30)
    except Exception:
        archived_dialogs = []

    seen_ids = {me.id}
    result: List[ChatItem] = []

    # Always add "Saved Messages" as the very first option
    result.append(ChatItem(
        id=me.id,
        name="Saved Messages (Personal Cloud)",
        username=me.username,
        type="saved_messages",
        unread_count=0,
        pinned=True
    ))

    for d in list(main_dialogs) + list(archived_dialogs):
        if not d.entity or d.entity.id in seen_ids:
            continue
        seen_ids.add(d.entity.id)

        username = getattr(d.entity, "username", None)
        entity_type = "user"

        if isinstance(d.entity, Channel):
            entity_type = "channel" if d.is_channel and not d.is_group else "supergroup"
        elif isinstance(d.entity, Chat):
            entity_type = "group"
        elif isinstance(d.entity, User):
            is_bot = bool(getattr(d.entity, "bot", False)) or (username and username.lower().endswith("bot"))
            entity_type = "bot" if is_bot else "user"

        name = d.name or "Unknown Chat"
        has_photo = bool(getattr(d.entity, "photo", None))
        result.append(ChatItem(
            id=d.entity.id,
            name=name,
            username=username,
            type=entity_type,
            unread_count=d.unread_count,
            pinned=bool(d.pinned),
            photo_url=f"/api/chats/{d.entity.id}/avatar" if has_photo else None,
            has_avatar=has_photo
        ))

    # 2. Fetch all Telegram Contacts from user address book
    try:
        contacts_res = await client(GetContactsRequest(hash=0))
        if hasattr(contacts_res, "users"):
            for u in contacts_res.users:
                if not u or u.id in seen_ids:
                    continue
                seen_ids.add(u.id)
                full_name = f"{u.first_name or ''} {u.last_name or ''}".strip()
                if not full_name:
                    full_name = u.username or f"Contact {u.id}"
                is_bot = bool(getattr(u, "bot", False)) or (u.username and u.username.lower().endswith("bot"))
                has_u_photo = bool(getattr(u, "photo", None))
                result.append(ChatItem(
                    id=u.id,
                    name=full_name,
                    username=u.username,
                    type="bot" if is_bot else "user",
                    unread_count=0,
                    pinned=False,
                    photo_url=f"/api/chats/{u.id}/avatar" if has_u_photo else None,
                    has_avatar=has_u_photo
                ))
    except Exception as contact_err:
        logger.debug(f"Could not fetch contacts list: {contact_err}")

    _chats_cache = result
    _cache_time = time.time()
    return result


def update_dialog_on_message(chat_id: Union[int, str], msg: Any) -> None:
    """
    Updates the in-memory dialogs cache when a new live message arrives.
    Bumps the target chat towards the top of the dialogs list and updates unread counts.
    Emits a 'chats:dialog_updated' event via Socket.IO so docked channel lists update in real-time.
    """
    global _chats_cache, _cache_time
    if not _chats_cache:
        return

    clean_str = str(chat_id).strip()
    clean_digits = clean_str.lstrip("-").replace("100", "", 1) if clean_str.startswith("-100") else clean_str.lstrip("-")
    is_saved = clean_str.lower() in ("me", "self")

    found_idx = -1
    for i, c in enumerate(_chats_cache):
        c_str = str(c.id).strip()
        c_digits = c_str.lstrip("-").replace("100", "", 1) if c_str.startswith("-100") else c_str.lstrip("-")
        if is_saved and c.type == "saved_messages":
            found_idx = i
            break
        elif not is_saved and (c_str == clean_str or c_digits == clean_digits):
            found_idx = i
            break

    if found_idx != -1:
        target_chat = _chats_cache.pop(found_idx)
        # Increment unread count if message is inbound
        is_out = getattr(msg, "out", False)
        if not is_out and not is_saved:
            target_chat.unread_count = (target_chat.unread_count or 0) + 1

        # Position after pinned chats
        insert_idx = 0
        if not target_chat.pinned:
            for idx, c in enumerate(_chats_cache):
                if not c.pinned:
                    insert_idx = idx
                    break
            else:
                insert_idx = len(_chats_cache)
        _chats_cache.insert(insert_idx, target_chat)
        _cache_time = time.time()

        try:
            from app.socket_handlers import sio
            payload = {
                "chat_id": str(target_chat.id),
                "dialogs": [c.model_dump() for c in _chats_cache[:100]]
            }
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(sio.emit("chats:dialog_updated", payload))
            except RuntimeError:
                pass
        except Exception as e:
            logger.debug(f"Socket emit for dialogs update notice: {e}")


import os
from fastapi import Response
_AVATAR_CACHE_DIR = os.path.expanduser("~/.cache/tg_power_suite/avatars")
os.makedirs(_AVATAR_CACHE_DIR, exist_ok=True)
_EMPTY_PIXEL = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"


_AVATAR_SEMAPHORE = asyncio.Semaphore(2)


@router.get("/chats/{chat_id}/avatar")
async def get_chat_avatar(chat_id: str):
    """
    Downloads and caches Telegram channel/group/user profile avatar photo.
    Returns 1x1 transparent PNG if no photo exists, preventing 404 console noise.
    Uses concurrency semaphore and 3.5s timeout to protect Telethon from multi-DC flood.
    """
    cache_file = os.path.join(_AVATAR_CACHE_DIR, f"{chat_id}.jpg")
    cache_none = os.path.join(_AVATAR_CACHE_DIR, f"{chat_id}.none")
    if os.path.exists(cache_file) and os.path.getsize(cache_file) > 0:
        with open(cache_file, "rb") as f:
            return Response(
                content=f.read(),
                media_type="image/jpeg",
                headers={"Cache-Control": "public, max-age=604800, immutable"}
            )
    if os.path.exists(cache_none):
        return Response(
            content=_EMPTY_PIXEL,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=86400"}
        )

    client = await TelegramClientManager.get_client()
    if not client or not client.is_connected():
        return Response(content=_EMPTY_PIXEL, media_type="image/png")

    async with _AVATAR_SEMAPHORE:
        # Re-check cache inside semaphore lock
        if os.path.exists(cache_file) and os.path.getsize(cache_file) > 0:
            with open(cache_file, "rb") as f:
                return Response(
                    content=f.read(),
                    media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=604800, immutable"}
                )

        try:
            clean_id: int | str = chat_id
            if chat_id != "me":
                try:
                    clean_id = int(chat_id)
                except ValueError:
                    clean_id = chat_id

            entity = await asyncio.wait_for(client.get_entity(clean_id), timeout=3.0)
            if not getattr(entity, "photo", None):
                try:
                    with open(cache_none, "w") as f:
                        f.write("none")
                except Exception:
                    pass
                return Response(content=_EMPTY_PIXEL, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})

            photo_bytes = await asyncio.wait_for(client.download_profile_photo(entity, file=bytes), timeout=3.5)
            if not photo_bytes:
                try:
                    with open(cache_none, "w") as f:
                        f.write("none")
                except Exception:
                    pass
                return Response(content=_EMPTY_PIXEL, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})

            try:
                with open(cache_file, "wb") as f:
                    f.write(photo_bytes)
            except Exception:
                pass

            return Response(
                content=photo_bytes,
                media_type="image/jpeg",
                headers={"Cache-Control": "public, max-age=604800, immutable"}
            )
        except Exception as e:
            logger.debug(f"Avatar fetch notice for {chat_id}: {e}")
            try:
                with open(cache_none, "w") as f:
                    f.write("none")
            except Exception:
                pass
            return Response(content=_EMPTY_PIXEL, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


def clear_chats_cache():
    global _chats_cache, _cache_time
    _chats_cache = []
    _cache_time = 0


from app.routes.auth import (
    SendCodeRequest,
    SignInRequest,
    Password2FARequest,
    SessionStringRequest,
    ToggleTurboRequest,
    check_auth,
    start_primary_qr_login,
    get_primary_qr_status,
    send_auth_code,
    sign_in_code,
    sign_in_2fa,
    logout_telegram,
    check_secondary_auth,
    start_secondary_qr,
    get_secondary_qr,
    send_secondary_auth_code,
    sign_in_secondary_code,
    sign_in_secondary_2fa,
    import_secondary_session,
    logout_secondary,
    toggle_secondary_turbo
)


@router.get("/chats", response_model=List[ChatItem])
async def list_chats(
    force_refresh: bool = Query(False, description="Bypass cache and re-fetch from Telegram"),
    search: Optional[str] = Query(None, description="Search query to filter chats")
):
    """
    Fetches the user's Telegram dialogs (Saved Messages, Channels, Groups, Direct Chats).
    Cached for instant response times.
    """
    global _chats_cache, _cache_time

    now = time.time()
    if not force_refresh and _chats_cache and (now - _cache_time) < CACHE_TTL:
        chats = _chats_cache
    else:
        try:
            chats = await fetch_dialogs_from_telegram()
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error fetching chats: {e}", exc_info=True)
            if _chats_cache:
                chats = _chats_cache
            else:
                raise HTTPException(status_code=500, detail=f"Failed to fetch Telegram dialogs: {str(e)}")

    # Apply search filter if provided
    if search and search.strip():
        q = search.strip().lower()
        return [c for c in chats if q in c.name.lower() or (c.username and q in c.username.lower())]

    return chats


class SendTextMessageRequest(BaseModel):
    text: str


@router.get("/chats/{chat_id}/messages")
async def get_chat_messages(
    chat_id: str,
    limit: int = Query(35, ge=1, le=100),
    offset_id: int = Query(0, ge=0)
):
    """
    Fetches recent messages from a Telegram chat/channel for the native mobile dock.
    Extracts text, media attributes (video/photo/document, size, duration, stream_url),
    and sender information.
    """
    client = await TelegramClientManager.get_client()
    if not await client.is_user_authorized():
        raise HTTPException(status_code=401, detail="Telegram account not authorized.")

    try:
        clean_chat_id: int | str = chat_id
        if chat_id != "me":
            try:
                clean_chat_id = int(chat_id)
            except ValueError:
                clean_chat_id = chat_id

        entity = await client.get_entity(clean_chat_id)
        raw_msgs = await client.get_messages(entity, limit=limit, offset_id=offset_id)

        messages = []
        for m in raw_msgs:
            if not m:
                continue

            media_info = None
            if m.media:
                has_doc = hasattr(m, "document") and m.document
                file_name = getattr(m.file, "name", None) if m.file else None
                file_size = getattr(m.file, "size", 0) if m.file else 0
                mime_type = getattr(m.file, "mime_type", "") if m.file else ""
                duration = getattr(m.file, "duration", None) if m.file else None

                is_video = bool(
                    getattr(m, "video", False)
                    or (has_doc and mime_type and mime_type.startswith("video/"))
                    or (file_name and any(file_name.lower().endswith(ext) for ext in (".mkv", ".mp4", ".avi", ".mov", ".ts", ".webm")))
                )

                if is_video:
                    media_info = {
                        "type": "video",
                        "file_name": file_name or f"video_{m.id}.mp4",
                        "file_size": file_size,
                        "duration": duration,
                        "mime_type": mime_type or "video/mp4",
                        "stream_url": f"/stream/{chat_id}/{m.id}/{file_name or 'video.mp4'}",
                        "thumb_url": f"/stream/{chat_id}/{m.id}/thumb"
                    }
                elif getattr(m, "photo", False):
                    media_info = {
                        "type": "photo",
                        "file_name": f"photo_{m.id}.jpg",
                        "file_size": file_size,
                        "thumb_url": f"/stream/{chat_id}/{m.id}/thumb"
                    }
                elif has_doc:
                    media_info = {
                        "type": "document",
                        "file_name": file_name or f"file_{m.id}",
                        "file_size": file_size,
                        "mime_type": mime_type,
                        "download_url": f"/dl/{chat_id}/{m.id}/{file_name or 'file'}"
                    }

            sender_name = "User"
            if m.out:
                sender_name = "You"
            elif m.sender:
                s_first = getattr(m.sender, "first_name", "") or ""
                s_last = getattr(m.sender, "last_name", "") or ""
                s_title = getattr(m.sender, "title", "") or ""
                sender_name = s_title or f"{s_first} {s_last}".strip() or getattr(m.sender, "username", "User")

            messages.append({
                "id": m.id,
                "date": m.date.isoformat() if m.date else None,
                "text": m.message or "",
                "out": bool(m.out),
                "sender_name": sender_name,
                "sender_id": getattr(m, "sender_id", None),
                "media": media_info,
                "views": getattr(m, "views", None),
                "forwards": getattr(m, "forwards", None)
            })

        return {
            "chat_id": str(chat_id),
            "count": len(messages),
            "messages": messages
        }
    except Exception as e:
        logger.error(f"Failed to fetch messages for chat {chat_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chats/{chat_id}/send")
async def send_chat_message(chat_id: str, payload: SendTextMessageRequest):
    """Sends a text message to the specified Telegram chat/channel."""
    client = await TelegramClientManager.get_client()
    if not await client.is_user_authorized():
        raise HTTPException(status_code=401, detail="Telegram account not authorized.")

    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message text cannot be empty.")

    try:
        clean_chat_id: int | str = chat_id
        if chat_id != "me":
            try:
                clean_chat_id = int(chat_id)
            except ValueError:
                clean_chat_id = chat_id

        sent = await client.send_message(clean_chat_id, text)
        return {"success": True, "message_id": sent.id}
    except Exception as e:
        logger.error(f"Failed to send message to {chat_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

