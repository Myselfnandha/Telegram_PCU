import time
import logging
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query
from telethon.tl.types import User, Channel, Chat
from telethon.tl.functions.contacts import GetContactsRequest
from app.models import ChatItem, AuthStatus
from app.telegram_client import TelegramClientManager

logger = logging.getLogger("chats")

router = APIRouter(prefix="/api", tags=["chats", "auth"])

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
    
    # 1. Fetch main dialogs and archived dialogs
    main_dialogs = await client.get_dialogs(limit=300)
    try:
        archived_dialogs = await client.get_dialogs(folder=1, limit=100)
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
        result.append(ChatItem(
            id=d.entity.id,
            name=name,
            username=username,
            type=entity_type,
            unread_count=d.unread_count,
            pinned=bool(d.pinned)
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
                result.append(ChatItem(
                    id=u.id,
                    name=full_name,
                    username=u.username,
                    type="bot" if is_bot else "user",
                    unread_count=0,
                    pinned=False
                ))
    except Exception as contact_err:
        logger.debug(f"Could not fetch contacts list: {contact_err}")

    _chats_cache = result
    _cache_time = time.time()
    return result

@router.get("/auth/status", response_model=AuthStatus)
async def check_auth():
    """Returns the current Telegram MTProto authentication status."""
    try:
        is_auth = await TelegramClientManager.is_authorized()
        if not is_auth:
            return AuthStatus(authenticated=False)

        info = await TelegramClientManager.get_me_info()
        if info:
            return AuthStatus(
                authenticated=True,
                phone=info.get("phone"),
                username=info.get("username"),
                user_id=info.get("id"),
                first_name=info.get("first_name"),
                last_name=info.get("last_name")
            )
        return AuthStatus(authenticated=False)
    except Exception as e:
        logger.error(f"Auth check failed: {e}")
        return AuthStatus(authenticated=False, error=str(e))


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
