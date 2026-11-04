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

from telethon import utils

_SHARED_BRIDGE_CACHE: Dict[int, Any] = {}
SHARED_BRIDGE_CHANNEL = -1002372906767

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
        self._in_flight_resolves: Dict[Any, asyncio.Future] = {}
        self._in_flight_lock = asyncio.Lock()
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
        client_id = getattr(getattr(client, "session", None), "user_id", None) or id(client)
        client_key = (client_id, clean_chat_id, message_id)
        client_str_key = (client_id, str(clean_chat_id), message_id)
        legacy_key = (clean_chat_id, message_id)
        legacy_str_key = (str(clean_chat_id), message_id)
        now = time.time()

        is_secondary = getattr(client, "_is_secondary_client", False) is True
        if not force_refresh:
            entry = self._message_cache.get(client_key) or self._message_cache.get(client_str_key)
            if not entry and not is_secondary:
                entry = self._message_cache.get(legacy_key) or self._message_cache.get(legacy_str_key)
            if entry:
                msg, ts = entry
                if (now - ts) < self.file_ref_max_age and hasattr(msg, "media") and msg.media:
                    return msg

        resolve_key = (client_id, str(clean_chat_id), message_id)
        async with self._in_flight_lock:
            fut = self._in_flight_resolves.get(resolve_key)
            if fut is None or fut.done():
                loop = asyncio.get_running_loop()
                fut = loop.create_future()
                self._in_flight_resolves[resolve_key] = fut
                is_leader = True
            else:
                is_leader = False

        if not is_leader:
            try:
                return await asyncio.shield(fut)
            except Exception:
                return None

        try:
            raw_str = str(clean_chat_id).strip()
            my_id = getattr(getattr(client, "session", None), "user_id", None)
            if not my_id:
                try:
                    from app.telegram_client import TelegramClientManager
                    me_info = TelegramClientManager.get_cached_me() or {}
                    my_id = me_info.get("id")
                except Exception:
                    my_id = None
            if not my_id:
                try:
                    my_id = getattr(sniffer_service, "_own_user_id", None)
                except Exception:
                    pass

            target_cid = clean_chat_id
            if raw_str.lower() in ("me", "self") or (my_id and raw_str in (str(my_id), f"-100{my_id}")):
                target_cid = "me"
            elif raw_str.lstrip("-").isdigit():
                target_cid = int(raw_str)

            candidate_cids = [target_cid]
            clean_digits = raw_str.lstrip("-").replace("100", "", 1) if raw_str.startswith("-100") else raw_str.lstrip("-")
            if clean_digits.isdigit():
                num = int(clean_digits)
                channel_num = int(f"-100{clean_digits}")
                if num not in candidate_cids:
                    candidate_cids.append(num)
                if channel_num not in candidate_cids:
                    candidate_cids.append(channel_num)

            msg = None
            try:
                msg = await client.get_messages(target_cid, ids=message_id)
            except Exception as e:
                logger.debug(f"Direct fetch for message {message_id} from {clean_chat_id} notice: {e}")
                try:
                    entity = await client.get_entity(target_cid)
                    msg = await client.get_messages(entity, ids=message_id)
                except Exception:
                    try:
                        await client.get_dialogs(limit=100)
                        entity = await client.get_entity(target_cid)
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

            if not msg or not getattr(msg, "media", None):
                alt_cids = [cid for cid in candidate_cids if cid != target_cid]
                for cid in alt_cids:
                    try:
                        msg = await client.get_messages(cid, ids=message_id)
                        if msg and getattr(msg, "media", None):
                            break
                    except Exception:
                        try:
                            entity = await client.get_entity(cid)
                            msg = await client.get_messages(entity, ids=message_id)
                            if msg and getattr(msg, "media", None):
                                break
                        except Exception:
                            pass

            if (not msg or not hasattr(msg, "media") or not msg.media) and is_secondary:
                try:
                    from app.telegram_client import TelegramClientManager
                    primary_client = await TelegramClientManager.get_client()
                    if primary_client and primary_client is not client:
                        for tid in candidate_cids:
                            try:
                                p_entity = await primary_client.get_entity(tid)
                                if p_entity and getattr(p_entity, "username", None):
                                    sec_entity = await client.get_entity(p_entity.username)
                                    msg = await client.get_messages(sec_entity, ids=message_id)
                                    if msg and hasattr(msg, "media") and msg.media:
                                        break
                            except Exception:
                                pass
                        if not msg or not hasattr(msg, "media") or not msg.media:
                            p_msg = await primary_client.get_messages(target_cid, ids=message_id)
                            if not p_msg or not getattr(p_msg, "media", None):
                                for cid in candidate_cids:
                                    try:
                                        p_msg = await primary_client.get_messages(cid, ids=message_id)
                                        if p_msg and getattr(p_msg, "media", None):
                                            target_cid = cid
                                            break
                                    except Exception:
                                        pass
                            if p_msg and getattr(p_msg, "media", None):
                                finfo = utils._get_file_info(p_msg.media)
                                floc = getattr(finfo, "location", None)
                                doc_id = getattr(floc, "id", None)
                                if doc_id and doc_id in _SHARED_BRIDGE_CACHE:
                                    msg = _SHARED_BRIDGE_CACHE[doc_id]
                                else:
                                    cid_raw = str(target_cid).lstrip("-").replace("100", "", 1)
                                    bridge_raw = str(SHARED_BRIDGE_CHANNEL).lstrip("-").replace("100", "", 1)
                                    if cid_raw != bridge_raw:
                                        fwd = await primary_client.forward_messages(SHARED_BRIDGE_CHANNEL, p_msg.id, target_cid)
                                        if fwd:
                                            sec_msg = await client.get_messages(SHARED_BRIDGE_CHANNEL, ids=fwd.id)
                                            if sec_msg and getattr(sec_msg, "media", None):
                                                if doc_id:
                                                    _SHARED_BRIDGE_CACHE[doc_id] = sec_msg
                                                msg = sec_msg
                except Exception as cross_err:
                    logger.debug(f"Cross-client resolution notice for {clean_chat_id}: {cross_err}")

            if msg and hasattr(msg, "media") and msg.media:
                self._message_cache[client_key] = (msg, now)
                self._message_cache[client_str_key] = (msg, now)
                if not is_secondary:
                    self._message_cache[legacy_key] = (msg, now)
                    self._message_cache[legacy_str_key] = (msg, now)
                    sniffer_service._message_cache[legacy_key] = msg
                    sniffer_service._message_cache[legacy_str_key] = msg
                if not fut.done():
                    fut.set_result(msg)
                return msg

            if not is_secondary:
                cached = sniffer_service._message_cache.get(legacy_key) or sniffer_service._message_cache.get(legacy_str_key)
                if cached:
                    if not fut.done():
                        fut.set_result(cached)
                    return cached

            if not fut.done():
                fut.set_result(None)
            return None
        except BaseException as exc:
            if not fut.done():
                fut.set_exception(exc)
            raise
        finally:
            async with self._in_flight_lock:
                if self._in_flight_resolves.get(resolve_key) is fut:
                    self._in_flight_resolves.pop(resolve_key, None)

    async def remove_deleted_video(self, clean_chat_id: Union[int, str], message_id: int):
        """
        Removes a deleted Telegram video from in-memory cache and SQLite persistence,
        and broadcasts real-time update to connected clients.
        """
        str_cid = str(clean_chat_id).lstrip("-").replace("100", "", 1)
        # 1. Clear from in-memory cache
        for k in list(self._videos_cache.keys()):
            kid = k.split("_")[0].lstrip("-").replace("100", "", 1)
            if kid == str_cid:
                cached_data = self._videos_cache[k].get("data", {})
                vids = cached_data.get("videos", [])
                new_vids = [v for v in vids if v.get("message_id") != message_id]
                if len(new_vids) != len(vids):
                    cached_data["videos"] = new_vids
                    cached_data["count"] = len(new_vids)
                    self._videos_cache[k]["data"] = cached_data

        # 2. Prune from SQLite database cache
        try:
            for cid_candidate in (str(clean_chat_id), str_cid):
                db_cached = await get_cinema_cached_videos_db(cid_candidate)
                if db_cached and "videos" in db_cached:
                    vids = db_cached["videos"]
                    new_vids = [v for v in vids if v.get("message_id") != message_id]
                    if len(new_vids) != len(vids):
                        await save_cinema_cached_videos_db(cid_candidate, new_vids)
                        logger.info(f"Purged deleted video message {message_id} from cinema cache for chat {cid_candidate}")
                        try:
                            from app.socket_handlers import sio
                            await sio.emit("cinema:videos_updated", {
                                "chat_id": cid_candidate,
                                "count": len(new_vids),
                                "videos": new_vids,
                                "updated_at": time.time()
                            })
                        except Exception:
                            pass
        except Exception as e:
            logger.debug(f"Failed to prune deleted video from database cache: {e}")

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

                res_str = f"{height}p" if height else None
                if not res_str:
                    import re
                    m = re.search(r'\b(2160p|4k|1080p|720p|480p|360p)\b', clean_name, re.I)
                    if m:
                        matched = m.group(1).lower()
                        res_str = "2160p" if matched == "4k" else matched
                        if not height:
                            height = 2160 if res_str == "2160p" else int(res_str.rstrip("p"))
                            width = int(height * 16 / 9)

                # Heuristic fallback if height/resolution is still unknown
                if not res_str:
                    sz = int(msg.file.size)
                    if duration > 0:
                        bps = (sz * 8) / duration
                        if bps >= 2_200_000:
                            res_str = "1080p"
                            height, width = 1080, 1920
                        elif bps >= 1_000_000:
                            res_str = "720p"
                            height, width = 720, 1280
                        elif bps >= 400_000:
                            res_str = "480p"
                            height, width = 480, 854
                        else:
                            res_str = "360p"
                            height, width = 360, 640
                    elif sz >= 2 * 1024 * 1024 * 1024:
                        res_str = "1080p"
                        height, width = 1080, 1920
                    elif sz >= 900 * 1024 * 1024:
                        res_str = "720p"
                        height, width = 720, 1280

                videos.append({
                    "message_id": msg.id,
                    "chat_id": str(chat_id),
                    "filename": clean_name,
                    "file_size": int(msg.file.size),
                    "size": int(msg.file.size),
                    "duration": duration,
                    "width": width,
                    "height": height,
                    "resolution": res_str,
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

            # Merge with existing SQLite cache while pruning messages deleted in Telegram
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
            if videos:
                min_scanned_id = min(v["message_id"] for v in videos)
                max_scanned_id = max(v["message_id"] for v in videos)
                pruned_existing = {}
                for mid, v in existing_videos_map.items():
                    # If an existing video was in the scanned ID range but Telegram no longer returned it,
                    # it was deleted on Telegram — prune it so ghost cards never linger in UI
                    if min_scanned_id <= mid <= max_scanned_id and mid not in new_videos_map:
                        continue
                    pruned_existing[mid] = v
                merged_videos_dict = {**pruned_existing, **new_videos_map}
            else:
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
