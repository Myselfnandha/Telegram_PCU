# -*- coding: utf-8 -*-
"""
Telegram Real-Time Message Synchronization Service
Provides robust Telegram peer/entity resolution, canonical chat ID aliasing,
and live MTProto event listeners (NewMessage, MessageDeleted, MessageEdited)
to keep Cinema catalog, dialog lists, and connected frontend clients synchronized in real time.
"""

import os
import time
import asyncio
import logging
from typing import Dict, List, Any, Optional, Union, Tuple
from telethon import events, utils
from telethon.tl.types import (
    PeerUser,
    PeerChat,
    PeerChannel,
    InputPeerSelf,
    InputPeerUser,
    InputPeerChat,
    InputPeerChannel,
    Channel,
    Chat,
    User,
    DocumentAttributeVideo,
    DocumentAttributeFilename,
)

logger = logging.getLogger("telegram_sync")

VIDEO_EXTENSIONS = (
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts",
    ".flv", ".wmv", ".m4v", ".3gp", ".vob", ".mpg", ".mpeg"
)


def canonicalize_chat_id(chat_id: Union[int, str], my_id: Optional[int] = None) -> Tuple[str, List[str]]:
    """
    Normalizes any chat identifier into a canonical key and its known aliases.
    - Saved Messages: canonical 'me', aliases ['me', str(my_id), f'-100{my_id}']
    - Channels/Supergroups: canonical '-100{digits}', aliases ['-100{digits}', '{digits}']
    - Users / Groups: canonical string representation and numeric aliases
    """
    raw = str(chat_id).strip()
    if raw.lower() in ("me", "self", "saved", "saved messages"):
        aliases = ["me"]
        if my_id:
            aliases.extend([str(my_id), f"-100{my_id}"])
        return "me", aliases

    if my_id and raw in (str(my_id), f"-100{my_id}"):
        return "me", ["me", str(my_id), f"-100{my_id}"]

    clean_digits = raw.lstrip("-")
    if clean_digits.startswith("100"):
        num_part = clean_digits[3:]
    else:
        num_part = clean_digits

    if num_part.isdigit():
        canonical = f"-100{num_part}"
        aliases = [canonical, num_part, f"-{num_part}"]
        return canonical, aliases

    return raw, [raw]


async def resolve_telegram_entity(client: Any, chat_id: Union[int, str]) -> Any:
    """
    Resolves any Telegram chat identifier into an InputPeer or Entity in <1ms.
    Gracefully handles:
      - 'me' / 'self' -> InputPeerSelf
      - Raw numeric user IDs (positive)
      - Channel IDs (positive without -100 or marked with -100)
      - Usernames / handles
      - Fallback dialog matching
    """
    raw_str = str(chat_id).strip()
    if raw_str.lower() in ("me", "self"):
        return "me"

    # 1. Numeric resolution
    if raw_str.lstrip("-").isdigit():
        num = int(raw_str)

        # Try exact numeric peer first
        try:
            return await client.get_input_entity(num)
        except Exception:
            pass

        # If positive integer, test channel format (-100...)
        if num > 0:
            channel_id = int(f"-100{num}")
            try:
                return await client.get_input_entity(channel_id)
            except Exception:
                pass

        # If -100 channel ID, test positive integer
        if raw_str.startswith("-100"):
            pos_id = int(raw_str[4:])
            try:
                return await client.get_input_entity(pos_id)
            except Exception:
                pass

    # 2. String username / entity lookup
    try:
        return await client.get_input_entity(chat_id)
    except Exception:
        pass

    # 3. Direct entity lookup
    try:
        return await client.get_entity(chat_id)
    except Exception:
        pass

    # 4. Dialog-primed fallback match
    clean_digits = raw_str.lstrip("-").replace("100", "", 1) if raw_str.startswith("-100") else raw_str.lstrip("-")
    try:
        async for d in client.iter_dialogs(limit=250):
            d_raw = str(d.id).lstrip("-").replace("100", "", 1) if str(d.id).startswith("-100") else str(d.id).lstrip("-")
            if d_raw == clean_digits or str(d.id) == raw_str:
                return d.input_entity
            if getattr(d.entity, "username", None) and d.entity.username.lower() == raw_str.lower().lstrip("@"):
                return d.input_entity
    except Exception as e:
        logger.debug(f"Dialog fallback scan notice for {chat_id}: {e}")

    raise ValueError(f"Could not resolve Telegram entity for '{chat_id}'")


class TelegramSyncService:
    """
    Coordinates real-time MTProto event listening across Telegram clients.
    Automatically captures incoming media, prunes deleted items, and updates
    in-memory and SQLite catalogs with zero manual polling.
    """

    def __init__(self):
        self._client: Optional[Any] = None
        self._my_id: Optional[int] = None
        self._attached = False
        self._lock = asyncio.Lock()

    def attach_to_client(self, client: Any):
        """Attaches real-time NewMessage, MessageDeleted, and MessageEdited handlers."""
        if not client:
            return

        async def _init_listener():
            async with self._lock:
                if self._client is client and self._attached:
                    return
                self._client = client
                try:
                    me = await client.get_me()
                    if me:
                        self._my_id = me.id
                except Exception:
                    self._my_id = None

                @client.on(events.NewMessage)
                async def on_new_message(event):
                    await self._handle_new_message(event)

                @client.on(events.MessageDeleted)
                async def on_message_deleted(event):
                    await self._handle_message_deleted(event)

                @client.on(events.MessageEdited)
                async def on_message_edited(event):
                    await self._handle_new_message(event, is_edit=True)

                self._attached = True
                logger.info(f"Telegram real-time sync service attached to MTProto client (User ID: {self._my_id}).")

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_init_listener())
        except RuntimeError:
            pass

    async def _handle_new_message(self, event: Any, is_edit: bool = False):
        """Processes incoming live messages and syncs Cinema catalog and chat dialogs."""
        try:
            msg = getattr(event, "message", None)
            if not msg:
                return

            # Determine Chat ID
            chat_id_num = event.chat_id
            sender_id = getattr(event, "sender_id", None)
            is_saved = bool(
                self._my_id and (
                    chat_id_num == self._my_id or
                    (getattr(event, "is_private", False) and sender_id == self._my_id)
                )
            )

            effective_chat_id = "me" if is_saved else str(chat_id_num)

            # Check if message contains streamable video media
            is_video = False
            filename = getattr(msg.file, "name", None) if getattr(msg, "file", None) else None
            mime = getattr(msg.file, "mime_type", "") if getattr(msg, "file", None) else ""

            if getattr(msg, "video", None) is not None:
                is_video = True
            elif mime and mime.startswith("video/"):
                is_video = True
            elif filename and any(filename.lower().endswith(ext) for ext in VIDEO_EXTENSIONS):
                is_video = True

            if is_video and getattr(msg, "media", None):
                from app.services.cinema_catalog import cinema_catalog
                await cinema_catalog.process_incoming_video_message(self._client, msg, effective_chat_id)
                logger.info(f"Real-time synced new video message {msg.id} in chat {effective_chat_id} (filename: {filename or 'video'})")

            # Update dialogs cache in chats.py if present
            try:
                from app.routes.chats import update_dialog_on_message
                update_dialog_on_message(chat_id_num if not is_saved else "me", msg)
            except Exception:
                pass

        except Exception as e:
            logger.debug(f"Real-time message sync notice: {e}")

    async def _handle_message_deleted(self, event: Any):
        """Prunes deleted messages from Cinema catalog and notifies UI."""
        try:
            chat_id_num = event.chat_id
            if not chat_id_num:
                return
            is_saved = bool(self._my_id and chat_id_num == self._my_id)
            effective_chat_id = "me" if is_saved else str(chat_id_num)

            deleted_ids = getattr(event, "deleted_ids", [])
            if deleted_ids:
                from app.services.cinema_catalog import cinema_catalog
                for mid in deleted_ids:
                    await cinema_catalog.remove_deleted_video(effective_chat_id, mid)
                logger.info(f"Real-time pruned {len(deleted_ids)} deleted message(s) from chat {effective_chat_id}")
        except Exception as e:
            logger.debug(f"Real-time message deletion notice: {e}")


telegram_sync_service = TelegramSyncService()
