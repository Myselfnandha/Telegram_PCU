# -*- coding: utf-8 -*-
"""
Cinema Catalog Service (Deep Module)
Encapsulates Telegram video querying, entity resolution, SQLite persistence,
series metadata extraction, and multi-tier caching behind a clean, focused interface.
"""

import os
import time
import asyncio
import logging
from typing import Dict, List, Any, Optional, Union, Tuple
from fastapi import HTTPException
from app.services.sniffer_service import sniffer_service, auto_rename
from app.routes.history import get_cinema_cached_videos_db, save_cinema_cached_videos_db

logger = logging.getLogger("cinema_catalog")

_THUMB_CACHE_DIR = os.path.expanduser("~/.cache/tg_power_suite/thumbs")
os.makedirs(_THUMB_CACHE_DIR, exist_ok=True)

VIDEO_EXTENSIONS = (
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts",
    ".flv", ".wmv", ".m4v", ".3gp", ".vob", ".mpg", ".mpeg"
)


class CinemaCatalog:
    """
    Deep Module for Cinema Media Discovery and Cataloging.
    Hides all Telethon dialog scanning, entity ID resolution, multi-filter
    merging, SQLite synchronization, and background thumbnail prefetching.
    """

    def __init__(self):
        self._message_cache: Dict[Tuple[Union[int, str], int], Tuple[Any, float]] = {}
        self._videos_cache: Dict[str, dict] = {}
        self.file_ref_max_age = 7200.0  # 2 hours file_reference freshness window
        self.cache_ttl = 300.0  # 5 minutes in-memory cache TTL

    async def get_streamable_message(
        self,
        client: Any,
        clean_chat_id: Union[int, str],
        message_id: int,
        force_refresh: bool = False
    ) -> Optional[Any]:
        """
        Fetch a Telegram message with guaranteed-valid file_reference.
        Automatically heals missing peer entities by scanning dialogs if needed.
        """
        key = (clean_chat_id, message_id)
        str_key = (str(clean_chat_id), message_id)
        now = time.time()

        if not force_refresh:
            entry = self._message_cache.get(key) or self._message_cache.get(str_key)
            if entry:
                msg, ts = entry
                if (now - ts) < self.file_ref_max_age and hasattr(msg, "media") and msg.media:
                    return msg

        msg = None
        try:
            msg = await client.get_messages(clean_chat_id, ids=message_id)
        except Exception as e:
            logger.debug(f"Direct fetch for message {message_id} from {clean_chat_id} notice: {e}")
            try:
                entity = await client.get_entity(clean_chat_id)
                msg = await client.get_messages(entity, ids=message_id)
            except Exception:
                try:
                    # Prime dialogs cache if entity is unknown after restart
                    await client.get_dialogs(limit=100)
                    entity = await client.get_entity(clean_chat_id)
                    msg = await client.get_messages(entity, ids=message_id)
                except Exception:
                    try:
                        target_str = str(clean_chat_id).lstrip("-").replace("100", "", 1)
                        async for d in client.iter_dialogs(limit=400):
                            did_str = str(d.id).lstrip("-").replace("100", "", 1)
                            if d.id == clean_chat_id or did_str == target_str or str(d.id).endswith(str(clean_chat_id)):
                                msg = await client.get_messages(d.entity, ids=message_id)
                                break
                    except Exception as e2:
                        logger.debug(f"Dialog-primed fallback fetch failed for {clean_chat_id}: {e2}")

        if msg and hasattr(msg, "media") and msg.media:
            self._message_cache[key] = (msg, now)
            self._message_cache[str_key] = (msg, now)
            sniffer_service._message_cache[key] = msg
            sniffer_service._message_cache[str_key] = msg
            return msg

        cached = sniffer_service._message_cache.get(key) or sniffer_service._message_cache.get(str_key)
        if cached:
            return cached

        return None

    async def get_videos(
        self,
        client: Any,
        chat_id: str,
        limit: int = 250,
        offset_id: int = 0,
        force_refresh: bool = False
    ) -> Dict[str, Any]:
        """
        Retrieves video archives from a channel or Saved Messages.
        Applies Stale-While-Revalidate pattern using in-memory and SQLite storage.
        """
        cache_key = f"{chat_id}_{offset_id}"
        now = time.time()

        # 1. Fast Memory Cache (<0.1ms)
        if not force_refresh and cache_key in self._videos_cache:
            cached_entry = self._videos_cache[cache_key]
            if (now - cached_entry["timestamp"]) > 60:
                asyncio.create_task(self._fetch_and_cache(client, chat_id, limit, offset_id))
            return cached_entry["data"]

        # 2. SQLite Cache (<1ms)
        if not force_refresh:
            try:
                db_cached = await get_cinema_cached_videos_db(chat_id)
                if db_cached and db_cached.get("count", 0) > 0:
                    for v in db_cached.get("videos", []):
                        if "size" not in v and "file_size" in v:
                            v["size"] = v["file_size"]
                        if "file_size" not in v and "size" in v:
                            v["file_size"] = v["size"]
                        if "resolution" not in v and v.get("height"):
                            v["resolution"] = f"{v['height']}p"

                    self._videos_cache[cache_key] = {
                        "data": db_cached,
                        "timestamp": db_cached.get("updated_at", now)
                    }
                    if (now - db_cached.get("updated_at", 0)) > 60:
                        asyncio.create_task(self._fetch_and_cache(client, chat_id, limit, offset_id))
                    return db_cached
            except Exception as e:
                logger.debug(f"SQLite cache lookup error: {e}")

        # 3. Fresh fetch
        return await self._fetch_and_cache(client, chat_id, limit, offset_id)

    async def _fetch_and_cache(
        self,
        client: Any,
        chat_id: str,
        limit: int = 250,
        offset_id: int = 0
    ) -> Dict[str, Any]:
        """Executes full MTProto query and updates caches."""
        if not client or not client.is_connected() or not await client.is_user_authorized():
            raise HTTPException(status_code=401, detail="Telegram client not authorized.")

        clean_chat_id: Union[int, str] = chat_id
        if chat_id != "me":
            try:
                clean_chat_id = int(chat_id)
            except ValueError:
                # Must be a valid Telegram username or handle
                candidate = chat_id.lstrip("@")
                if not candidate.replace("_", "").isalnum() or len(candidate) > 32:
                    raise HTTPException(status_code=400, detail="Invalid chat identifier format")
                clean_chat_id = chat_id

        videos: List[Dict[str, Any]] = []
        try:
            from telethon.tl.types import (
                InputMessagesFilterVideo,
                InputMessagesFilterDocument,
                DocumentAttributeVideo,
                DocumentAttributeFilename
            )

            fetch_limit = min(max(limit, 100), 500)
            target_peer = clean_chat_id
            try:
                target_peer = await client.get_input_entity(clean_chat_id)
            except Exception:
                target_str = str(clean_chat_id).lstrip("-").replace("100", "", 1)
                async for d in client.iter_dialogs(limit=400):
                    did_str = str(d.id).lstrip("-").replace("100", "", 1)
                    if d.id == clean_chat_id or did_str == target_str or str(d.id).endswith(str(clean_chat_id)):
                        target_peer = d.entity
                        break

            recent_task = client.get_messages(target_peer, limit=fetch_limit, offset_id=offset_id)
            v_task = client.get_messages(target_peer, limit=fetch_limit, offset_id=offset_id, filter=InputMessagesFilterVideo)
            d_task = client.get_messages(target_peer, limit=fetch_limit, offset_id=offset_id, filter=InputMessagesFilterDocument)

            recent_msgs, v_msgs, d_msgs = await asyncio.gather(recent_task, v_task, d_task, return_exceptions=True)
            if isinstance(recent_msgs, BaseException):
                recent_msgs = []
            if isinstance(v_msgs, BaseException):
                v_msgs = []
            if isinstance(d_msgs, BaseException):
                d_msgs = []

            seen_ids = set()
            combined_msgs = []
            for msg in list(v_msgs) + list(d_msgs) + list(recent_msgs):
                if not msg or msg.id in seen_ids or not hasattr(msg, "file") or not msg.file:
                    continue
                seen_ids.add(msg.id)
                combined_msgs.append(msg)

            combined_msgs.sort(key=lambda m: m.id, reverse=True)

            for msg in combined_msgs:
                filename = msg.file.name if msg.file.name else f"video_{msg.id}.mp4"
                duration = 0
                width = 0
                height = 0

                if hasattr(msg.media, "document") and msg.media.document:
                    for attr in msg.media.document.attributes:
                        if isinstance(attr, DocumentAttributeVideo):
                            duration = int(getattr(attr, "duration", 0))
                            width = int(getattr(attr, "w", 0))
                            height = int(getattr(attr, "h", 0))
                        elif isinstance(attr, DocumentAttributeFilename):
                            if getattr(attr, "file_name", None):
                                filename = attr.file_name

                mime = msg.file.mime_type or ""
                is_video_media = (
                    getattr(msg, "video", None) is not None or
                    mime.startswith("video/") or
                    any(filename.lower().endswith(ext) for ext in VIDEO_EXTENSIONS)
                )

                if not is_video_media:
                    continue

                sniffer_service._message_cache[(clean_chat_id, msg.id)] = msg

                raw_name = "".join([c for c in filename if (c.isalnum() or c in " .-_()")]).strip()
                clean_name = auto_rename(raw_name)

                has_thumb = False
                if hasattr(msg.media, "document") and msg.media.document and getattr(msg.media.document, "thumbs", None):
                    has_thumb = True
                elif hasattr(msg.media, "photo"):
                    has_thumb = True

                ext = os.path.splitext(clean_name)[1].lower()
                is_mkv = ext in (".mkv", ".avi", ".ts", ".flv", ".wmv", ".vob")

                import urllib.parse
                encoded_name = urllib.parse.quote(clean_name)
                base_clean = os.path.splitext(clean_name)[0]
                encoded_mp4 = urllib.parse.quote(f"{base_clean}.mp4")

                videos.append({
                    "message_id": msg.id,
                    "chat_id": str(chat_id),
                    "filename": clean_name,
                    "file_size": int(msg.file.size),
                    "size": int(msg.file.size),
                    "duration": duration,
                    "width": width,
                    "height": height,
                    "resolution": f"{height}p" if height else None,
                    "date": int(msg.date.timestamp()) if msg.date else 0,
                    "mime_type": msg.file.mime_type or "video/mp4",
                    "has_thumb": has_thumb,
                    "is_mkv": is_mkv,
                    "stream_url": f"/dl/{chat_id}/{msg.id}/{encoded_name}",
                    "stream_transmux_url": f"/stream/{chat_id}/{msg.id}/{encoded_mp4}",
                    "thumb_url": f"/dl/{chat_id}/{msg.id}/thumb" if has_thumb else None
                })

            # Background thumbnail prefetch
            async def _prefetch(cid, msgs):
                try:
                    for m in msgs[:30]:
                        cf = os.path.join(_THUMB_CACHE_DIR, f"{cid}_{m.id}.jpg")
                        if not os.path.exists(cf):
                            try:
                                tb = await client.download_media(m.media, thumb=0, file=bytes)
                                if tb:
                                    with open(cf, "wb") as f:
                                        f.write(tb)
                            except Exception:
                                pass
                except Exception:
                    pass

            asyncio.create_task(_prefetch(clean_chat_id, combined_msgs))

            # Merge with existing SQLite cache so older episodes are preserved
            existing_videos_map = {}
            try:
                db_cached = await get_cinema_cached_videos_db(str(chat_id))
                if db_cached and "videos" in db_cached:
                    for v in db_cached["videos"]:
                        if "message_id" in v:
                            existing_videos_map[v["message_id"]] = v
            except Exception as e:
                logger.debug(f"Could not load existing db videos to merge: {e}")

            new_videos_map = {v["message_id"]: v for v in videos}
            merged_videos_dict = {**existing_videos_map, **new_videos_map}
            videos = sorted(merged_videos_dict.values(), key=lambda x: x.get("message_id", 0), reverse=True)

        except Exception as e:
            logger.error(f"Error querying videos for {chat_id}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to query videos: {str(e)}")

        res = {
            "chat_id": str(chat_id),
            "count": len(videos),
            "videos": videos,
            "updated_at": time.time()
        }

        cache_key = f"{chat_id}_{offset_id}"
        self._videos_cache[cache_key] = {
            "data": res,
            "timestamp": time.time()
        }

        # Persist to SQLite
        asyncio.create_task(save_cinema_cached_videos_db(str(chat_id), videos))

        # Socket.IO sync notification
        try:
            from app.socket_handlers import sio
            asyncio.create_task(sio.emit("cinema:videos_updated", res))
        except Exception:
            pass

        return res


cinema_catalog = CinemaCatalog()
get_streamable_message = cinema_catalog.get_streamable_message
