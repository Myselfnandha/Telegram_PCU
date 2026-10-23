import os
import re
import sys
import time
import sqlite3
import logging
import asyncio
from typing import Dict, List, Set, Optional, Tuple, Any, Union
from telethon import events, utils, errors
from app.config import (
    PROXY_HOST,
    PROXY_PORT,
    MIN_FILE_SIZE_MB,
    QUALITY_WAIT_SECS,
    ALLOWED_EXT,
    KEYWORD_BLOCK,
    KEYWORD_ALLOW,
    TARGET_CHANNELS,
    ENABLE_NOTIFICATIONS,
    NOTIFICATION_MODE,
    PREFERRED_MANAGER,
    SUITE_DB_PATH,
    DISPATCH_PACING_SECS,
    save_env_dict
)
from app.services.manager_detector import auto_send, detect_managers, MANAGER_LABELS
from app.services.rate_governor import rate_governor

logger = logging.getLogger("sniffer_service")

# ────────────────────────────────────────────────────────
#  Notification Helper
# ────────────────────────────────────────────────────────
_last_notif_time: Dict[str, float] = {}
NOTIFICATION_DEBOUNCE_SECS = 8.0

def send_desktop_notification(
    title: str,
    message: str,
    urgency: str = "normal",
    category: str = "general",
    dedup_key: Optional[str] = None
) -> None:
    """Send native desktop notification on Linux/Windows with rate limiting."""
    if not ENABLE_NOTIFICATIONS:
        return
    if NOTIFICATION_MODE == "none":
        return
    if NOTIFICATION_MODE == "downloads_only" and category not in ("download", "batch"):
        return

    now = time.monotonic()
    key = dedup_key or f"{title}:{message}"
    if key in _last_notif_time and (now - _last_notif_time[key]) < NOTIFICATION_DEBOUNCE_SECS:
        return
    _last_notif_time[key] = now

    if sys.platform != "win32":
        try:
            import subprocess
            subprocess.Popen(
                ["notify-send", "-a", "TG Power Suite", "-u", urgency, title, message],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except Exception:
            pass


async def safe_reply(event, text: str, buttons=None):
    """Safely replies to event; if posting is forbidden in this channel, redirects message to Saved Messages (me)."""
    try:
        return await event.reply(text, buttons=buttons)
    except (errors.ChatAdminRequiredError, errors.UserBannedInChannelError, errors.ChatWriteForbiddenError):
        try:
            client = getattr(event, "_client", None)
            if client:
                return await client.send_message("me", f"📢 **[Watched Channel Auto-Download]**\n{text}", buttons=buttons)
        except Exception:
            return None
    except Exception as e:
        logger.debug(f"Could not send reply: {e}")
        return None


# ────────────────────────────────────────────────────────
#  Filename Cleaning & Formatting (Scrapes Watermarks, Channels & URLs)
# ────────────────────────────────────────────────────────
_WATERMARK_WORDS = [
    'tamilmovoo', 'tamildbox', 'tamilblasters', 'tamilmv', '1tamilmv',
    'tamilyogi', 'moviesda', 'bollyflix', 'katmovie', 'vegamovies',
    'rarbg', 'yify', 'psa', 'pahe', 'tn69', 'cinemavilla', 'isaimini',
    'movies4u', 'cinemahub', 'tamilrockers', 'movierulz', 'cinevood',
    'worldfree4u', 'khatrimaza', 'filmyzilla', '9xmovies', 'extramovies',
    'starflixtamil', 'starflix', 'moviesnation', 'mkvking', 'skymovies'
]
_NOISE_RE = re.compile(
    r'\b(2160p?|4k|uhd|1080p?|720p?|480p?|360p?|1p|hdrip|bdrip|bluray|blu-ray|webrip|web-dl|web|hdtv|dvdrip|hq|'
    r'x264|x265|hevc|avc|xvid|divx|10bit|8bit|'
    r'aac|ac3|eac3|ddp?\d?|dts|atmos|mp3|'
    r'esub|esubs|subrip|subs?|sub|'
    r'multi|dual|hindi|tamil|telugu|kannada|malayalam|english|dubbed)\b',
    re.IGNORECASE,
)

def auto_rename(raw: str) -> str:
    """Cleans up messy Telegram filenames by scraping URLs, @handles, channel tags and noise."""
    if not raw:
        return ""
    name, ext = os.path.splitext(raw)

    # 1. Strip URLs & site domains
    name = re.sub(r'https?://\S+', ' ', name, flags=re.IGNORECASE)
    name = re.sub(r'\b(?:t|telegram)\.me/[\w\+\-_/]+', ' ', name, flags=re.IGNORECASE)
    name = re.sub(r'\b(?:www\.[a-z0-9\.\-_]+|[a-z0-9\.\-_]+\.(?:com|org|net|in|yt|vip|me|to|is|cx|ms|li|co|cc|ws|site|xyz|online|live|tv))\b', ' ', name, flags=re.IGNORECASE)

    # 2. Convert underscores and dots to spaces
    name = re.sub(r'[\._]+', ' ', name)

    # 3. Strip @ handles (e.g. @channel or @user)
    name = re.sub(r'@\S+', ' ', name)

    # 4. Strip bracketed prefixes
    name = re.sub(r'^[\[\{][^\]\}]+[\]\}]\s*', ' ', name)

    # 5. Remove known site/channel watermark keywords
    name = re.sub(r'\b(?:' + '|'.join(_WATERMARK_WORDS) + r')\b', ' ', name, flags=re.IGNORECASE)

    # 6. Remove media codecs, qualities, audio types
    name = _NOISE_RE.sub(' ', name)

    # 7. Normalize punctuation and brackets
    name = re.sub(r'[\/\\:*?\"<>|\[\]\(\)\{\}\-]', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip()

    # 8. Re-format Year (e.g. 1999, 2024) cleanly into (YYYY)
    year_match = re.search(r'\b(19\d\d|20\d\d)\b', name)
    if year_match:
        year = year_match.group(1)
        idx = name.find(year)
        title_part = name[:idx].strip()
        rest_part = name[idx + len(year):].strip()
        if title_part and rest_part:
            name = f'{title_part} ({year}) {rest_part}'
        elif title_part:
            name = f'{title_part} ({year})'
        elif rest_part:
            name = f'{rest_part} ({year})'

    name = re.sub(r'\s+', ' ', name).strip()
    return f"{name}{ext}" if name else raw


class SnifferService:
    def __init__(self):
        self.active_channels: Set[Union[int, str]] = set()
        self.is_running = False
        self._client = None
        self._sniffer_handler_ref = None
        self._message_cache: Dict[Tuple[int, int], Any] = {}
        self._processed_messages: Set[Tuple[str, int]] = set()
        self._quality_buffer: Dict[Tuple[int, str], List[dict]] = {}
        self._quality_timers: Dict[Tuple[int, str], asyncio.TimerHandle] = {}
        self.recent_feed: List[dict] = []
        self.batch_active = False
        self.batch_links: List[dict] = []
        self.socket_emitter = None
        self._resolved_peer_ids: Set[int] = set()
        self._my_id: Optional[int] = None

        # tg-aligned persistent deduplication & dynamic pacing
        self._init_db()
        self._dispatch_queue: asyncio.Queue = asyncio.Queue()
        self._dispatch_worker_task: Optional[asyncio.Task] = None
        self._last_dispatch_time: float = 0.0
        self.pacing_delay: float = DISPATCH_PACING_SECS
        self.startup_time: float = time.time() - 10.0

        # Load channels from TARGET_CHANNELS
        for c in TARGET_CHANNELS:
            c = c.strip()
            if c.isdigit() or (c.startswith("-") and c[1:].isdigit()):
                self.active_channels.add(int(c))
            elif c:
                self.active_channels.add(c)

    def _init_db(self):
        """Initializes SQLite WAL table for persistent cross-restart deduplication."""
        try:
            SUITE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(str(SUITE_DB_PATH), timeout=10.0) as conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS dispatched_downloads (
                        chat_id TEXT NOT NULL,
                        message_id INTEGER NOT NULL,
                        file_name TEXT,
                        size_bytes INTEGER,
                        url TEXT,
                        dispatched_at REAL,
                        PRIMARY KEY (chat_id, message_id)
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_dispatched_at ON dispatched_downloads(dispatched_at DESC);")
                conn.commit()
        except Exception as e:
            logger.warning(f"Could not initialize dispatched_downloads SQLite table: {e}")

    def is_dispatched(self, chat_id: Union[int, str], message_id: int) -> bool:
        """Returns True if the message has already been dispatched (in-memory + SQLite)."""
        key = (str(chat_id), int(message_id))
        if key in self._processed_messages:
            return True
        try:
            with sqlite3.connect(str(SUITE_DB_PATH), timeout=10.0) as conn:
                cur = conn.execute(
                    "SELECT 1 FROM dispatched_downloads WHERE chat_id = ? AND message_id = ? LIMIT 1;",
                    (str(chat_id), int(message_id))
                )
                if cur.fetchone() is not None:
                    self._processed_messages.add(key)
                    return True
        except Exception as e:
            logger.debug(f"SQLite is_dispatched error: {e}")
        return False

    def record_dispatch(self, chat_id: Union[int, str], message_id: int, file_name: str, size_bytes: int, url: str) -> None:
        """Persistently records a dispatched download to prevent repeated triggering."""
        key = (str(chat_id), int(message_id))
        self._processed_messages.add(key)
        try:
            with sqlite3.connect(str(SUITE_DB_PATH), timeout=10.0) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO dispatched_downloads
                    (chat_id, message_id, file_name, size_bytes, url, dispatched_at)
                    VALUES (?, ?, ?, ?, ?, ?);
                """, (str(chat_id), int(message_id), file_name, int(size_bytes), str(url), time.time()))
                conn.commit()
        except Exception as e:
            logger.warning(f"Could not record dispatch to SQLite: {e}")

    def get_recent_dispatches(self, limit: int = 25) -> List[Dict]:
        """Retrieves recent dispatches from persistent SQLite storage."""
        try:
            with sqlite3.connect(str(SUITE_DB_PATH), timeout=10.0) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.execute("""
                    SELECT chat_id, message_id, file_name, size_bytes, url, dispatched_at
                    FROM dispatched_downloads
                    ORDER BY dispatched_at DESC
                    LIMIT ?;
                """, (limit,))
                rows = []
                for r in cur.fetchall():
                    size_mb = (r["size_bytes"] or 0) / (1024 * 1024)
                    dispatched_time = time.strftime("%H:%M:%S", time.localtime(r["dispatched_at"]))
                    rows.append({
                        "chat_id": r["chat_id"],
                        "message_id": r["message_id"],
                        "name": r["file_name"] or "Unknown",
                        "size": f"{size_mb:.2f} MB",
                        "time": dispatched_time,
                        "url": r["url"]
                    })
                return rows
        except Exception as e:
            logger.debug(f"Error querying recent dispatches: {e}")
            return []

    def _is_duplicate(self, chat_id: int, message_id: int) -> bool:
        return self.is_dispatched(chat_id, message_id)

    async def _resolve_watched_peers(self) -> None:
        """Pre-resolves usernames, Saved Messages, and channel IDs into numeric peer IDs."""
        if not self._client:
            return

        try:
            me = await self._client.get_me()
            if me:
                self._my_id = me.id
                logger.info(f"Sniffer resolved own user ID (Saved Messages): {self._my_id}")
        except Exception as me_err:
            logger.debug(f"Could not resolve own user ID: {me_err}")

        for ch in list(self.active_channels):
            try:
                # Handle Saved Messages aliases
                if str(ch).lower() in ("me", "saved", "self", "saved messages", "@me"):
                    if self._my_id:
                        self._resolved_peer_ids.add(self._my_id)
                        logger.info(f"Resolved watched channel '{ch}' -> Saved Messages ID {self._my_id}")
                    continue

                ent = await self._client.get_entity(ch)
                if ent and hasattr(ent, "id"):
                    self._resolved_peer_ids.add(ent.id)
                    from telethon import utils
                    peer_id = utils.get_peer_id(ent)
                    self._resolved_peer_ids.add(peer_id)
                    logger.debug(f"Resolved watched channel {ch} -> ID {ent.id} (peer {peer_id})")
            except Exception as e:
                logger.debug(f"Could not resolve watched entity {ch}: {e}")

    def set_socket_emitter(self, emitter):
        self.socket_emitter = emitter

    def _emit_feed_update(self, item: dict):
        self.recent_feed.insert(0, item)
        if len(self.recent_feed) > 100:
            self.recent_feed = self.recent_feed[:100]
        if self.socket_emitter:
            try:
                self.socket_emitter("sniffer_feed", item)
            except Exception as e:
                logger.debug(f"Error emitting sniffer feed: {e}")

    def _ensure_dispatch_worker(self):
        if self._dispatch_worker_task is None or self._dispatch_worker_task.done():
            try:
                loop = asyncio.get_running_loop()
                self._dispatch_worker_task = loop.create_task(self._dispatch_worker_loop())
            except RuntimeError:
                pass

    async def _dispatch_worker_loop(self):
        """Paced queue worker implementing dynamic pacing (0s single, 1.5s bursts) and FloodWait gates."""
        logger.info(f"⚡ Sniffer dispatch worker active (pacing delay: {self.pacing_delay}s).")
        while True:
            try:
                item = await self._dispatch_queue.get()
            except asyncio.CancelledError:
                break
            try:
                now = time.time()
                time_since_last = now - self._last_dispatch_time
                # Dynamic pacing: If multiple items in queue or recent dispatch within pacing delay, pace!
                if self._dispatch_queue.qsize() > 0 or time_since_last < self.pacing_delay:
                    wait_needed = max(0.0, self.pacing_delay - time_since_last)
                    if wait_needed > 0:
                        await asyncio.sleep(wait_needed)

                # Honor FloodWait if active
                await rate_governor.wait_if_cooling_down()

                link = item["link"]
                chat_id = item["chat_id"]
                message_id = item["message_id"]
                fname = item["fname"]
                size_bytes = item["size"]
                event = item.get("event")
                res_label = item.get("res_label", "")

                # Record in persistent store
                self.record_dispatch(chat_id, message_id, fname, size_bytes, link)
                self._last_dispatch_time = time.time()

                mgr, pushed = await auto_send(link)
                label = MANAGER_LABELS.get(mgr, mgr)
                size_mb = size_bytes / (1024 * 1024)

                feed_item = {
                    "id": f"sniff_{chat_id}_{message_id}",
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "filename": fname,
                    "size_bytes": size_bytes,
                    "size_formatted": f"{size_mb:.1f} MB",
                    "quality": res_label,
                    "download_url": link,
                    "manager": label,
                    "status": "dispatched" if pushed else "ready",
                    "timestamp": time.time()
                }
                self._emit_feed_update(feed_item)

                if pushed:
                    send_desktop_notification(
                        "Auto-Dispatched Media",
                        f"Pushed '{fname}' to {label}",
                        category="download",
                        dedup_key=f"dl_{chat_id}_{message_id}"
                    )

                if event:
                    status_desc = f"🚀 Dispatched to {label}" if pushed else "⚡ Direct HTTP Link Ready"
                    reply_text = (
                        f"📥 **Fast Download Link Ready:**\n"
                        f"`{link}`\n\n"
                        f"📁 **File:** `{fname}`\n"
                        f"📦 **Size:** `{size_mb:.1f} MB`\n"
                        f"🎯 **Status:** {status_desc}"
                    )
                    await safe_reply(event, reply_text)
            except Exception as e:
                logger.error(f"Error processing queued dispatch: {e}", exc_info=True)
            finally:
                self._dispatch_queue.task_done()

    def add_channel(self, channel: Union[int, str]) -> bool:
        """Add channel to sniffer monitoring and save to .env."""
        ch = int(channel) if str(channel).lstrip("-").isdigit() else str(channel).strip()
        if ch in self.active_channels:
            return False
        self.active_channels.add(ch)
        self._save_channels_to_env()
        logger.info(f"Added channel to monitor: {ch}")
        return True

    def remove_channel(self, channel: Union[int, str]) -> bool:
        """Remove channel from sniffer monitoring and save to .env."""
        ch = int(channel) if str(channel).lstrip("-").isdigit() else str(channel).strip()
        if ch in self.active_channels:
            self.active_channels.remove(ch)
            self._save_channels_to_env()
            logger.info(f"Removed channel from monitor: {ch}")
            return True
        return False

    def _save_channels_to_env(self):
        channels_str = ",".join(str(c) for c in self.active_channels)
        save_env_dict({"TARGET_CHANNELS": channels_str})

    def reload_config(self) -> None:
        """Dynamically reload filter settings from .env without restarting daemon."""
        from app.config import get_env_dict
        env_dict = get_env_dict()
        global MIN_FILE_SIZE_MB, ALLOWED_EXT, KEYWORD_BLOCK, KEYWORD_ALLOW, PREFERRED_MANAGER
        try:
            MIN_FILE_SIZE_MB = int(env_dict.get("MIN_FILE_SIZE_MB", "50"))
        except ValueError:
            pass
        ALLOWED_EXT = [x.strip().lower() for x in env_dict.get("ALLOWED_EXT", ".mkv,.mp4,.avi,.mov,.flv,.wmv,.zip,.rar,.tar,.gz,.7z,.iso").split(",") if x.strip()]
        KEYWORD_BLOCK = [x.strip().lower() for x in env_dict.get("KEYWORD_BLOCK", "sample,trailer,cam,ts,telesync,PRE-DVD").split(",") if x.strip()]
        KEYWORD_ALLOW = [x.strip().lower() for x in env_dict.get("KEYWORD_ALLOW", "").split(",") if x.strip()]
        PREFERRED_MANAGER = env_dict.get("PREFERRED_MANAGER", "auto").strip().lower()
        logger.info(f"Sniffer config reloaded: min_size={MIN_FILE_SIZE_MB}MB, allowed_exts={len(ALLOWED_EXT)}, block_kws={len(KEYWORD_BLOCK)}")

    def _is_keyword_blocked(self, fname: str) -> bool:
        """Checks if filename contains any blocked keywords using word boundary matching."""
        if not KEYWORD_BLOCK:
            return False
        fname_lower = fname.lower()
        for kw in KEYWORD_BLOCK:
            if not kw:
                continue
            # Use boundary matching so short words like 'ts' don't match 'yts', 'artists', 'cats'
            pattern = rf'(?:^|[\s\._\-\(\)\[\]]){re.escape(kw)}(?:$|[\s\._\-\(\)\[\]])'
            if re.search(pattern, fname_lower):
                return True
        return False

    def _is_keyword_allowed(self, fname: str) -> bool:
        """Checks if filename matches allowlist (if configured)."""
        if not KEYWORD_ALLOW:
            return True
        fname_lower = fname.lower()
        for kw in KEYWORD_ALLOW:
            if not kw:
                continue
            pattern = rf'(?:^|[\s\._\-\(\)\[\]]){re.escape(kw)}(?:$|[\s\._\-\(\)\[\]])'
            if re.search(pattern, fname_lower):
                return True
        return False

    def get_status(self) -> dict:
        from app.routes.chats import _chats_cache
        watched_enriched = []
        for ch in self.active_channels:
            raw = str(ch).strip()
            clean_handle = raw.lower().replace("@", "")
            clean_id = raw.replace("-100", "")
            found = next((c for c in _chats_cache if (c.username and c.username.lower() == clean_handle) or str(c.id).replace("-100", "") == clean_id or str(c.id) == raw), None)
            if found:
                watched_enriched.append({
                    "id": found.id,
                    "name": found.name or raw,
                    "type": found.type or "channel",
                    "username": found.username
                })
            else:
                c_type = "bot" if raw.lower().endswith("bot") else ("channel" if raw.startswith("@") else "group")
                watched_enriched.append({
                    "id": ch,
                    "name": raw,
                    "type": c_type,
                    "username": raw.lstrip("@") if raw.startswith("@") else None
                })

        return {
            "is_running": self.is_running,
            "active_channels": [str(c) for c in self.active_channels],
            "watched_channels": watched_enriched,
            "detected_managers": detect_managers(),
            "preferred_manager": PREFERRED_MANAGER,
            "min_file_size_mb": MIN_FILE_SIZE_MB,
            "allowed_ext": ALLOWED_EXT,
            "recent_count": len(self.recent_feed),
            "batch_active": self.batch_active,
            "batch_count": len(self.batch_links)
        }

    def _quality_score(self, fname: str, size: int) -> Tuple[int, int]:
        name = fname.lower()
        for rank, keyword in [
            (2160, "2160p"), (2160, "4k"), (1080, "1080p"),
            (720, "720p"), (480, "480p"), (360, "360p"),
        ]:
            if keyword in name:
                return rank, size
        return 0, size

    def _group_key(self, fname: str, media_group_id) -> str:
        if media_group_id:
            return f"album_{media_group_id}"
        base = fname.lower()
        
        # Preserve Season / Episode / Part numbers so series episodes are not merged into one group
        ep_match = re.search(r'\b(s\d{1,2}\s*e\d{1,3}|ep?\s*\d{1,3}|episode\s*\d{1,3}|part\s*\d{1,3})\b', base, re.IGNORECASE)
        ep_tag = f"_{ep_match.group(1).replace(' ', '')}" if ep_match else ""

        base = re.sub(
            r"[\._\-\s]*("
            r"2160p?|4k|uhd|1080p?|720p?|480p?|360p?|240p?"
            r"|x264|x265|hevc|avc|hdrip|bluray|bdrip|webrip|web-dl|web|hq"
            r"|esub|aac|dd\d|dts|atmos|ac3|eac3"
            r"|multi|dual|hindi|tamil|telugu|english|dubbed"
            r"|\d{2,4}mb"
            r")",
            "", base, flags=re.IGNORECASE,
        )
        base = re.sub(r"[^a-z0-9]", "", base)[:35]
        return f"name_{base}{ep_tag}" if base else f"name_unknown{ep_tag}"

    async def _flush_quality_group(self, buf_key: tuple, force: bool = False) -> None:
        candidates = self._quality_buffer.pop(buf_key, [])
        self._quality_timers.pop(buf_key, None)
        if not candidates:
            return

        best = max(candidates, key=lambda c: self._quality_score(c["fname"], c["size"]))
        res_rank, _ = self._quality_score(best["fname"], best["size"])
        res_label = f"{res_rank}p" if res_rank else "best size"

        chat_id = best["chat_id"]
        message_id = best["message_id"]
        fname = best["fname"]
        size_mb = best["size"] / (1024 * 1024)
        event = best["event"]
        import urllib.parse
        encoded_fname = urllib.parse.quote(fname)
        link = f"http://{PROXY_HOST}:{PROXY_PORT}/dl/{chat_id}/{message_id}/{encoded_fname}"

        self._message_cache[(chat_id, message_id)] = event.message

        if not force and self.is_dispatched(chat_id, message_id):
            return

        self._ensure_dispatch_worker()
        await self._dispatch_queue.put({
            "link": link,
            "chat_id": chat_id,
            "message_id": message_id,
            "fname": fname,
            "size": best["size"],
            "event": event,
            "res_label": res_label
        })

    async def _process_incoming_media(self, event) -> None:
        message = event.message
        if not message or not message.media or not hasattr(message, "file") or not message.file:
            return

        chat_id = utils.get_peer_id(event.chat_id) if hasattr(event, "chat_id") and event.chat_id else 0
        message_id = message.id
        self._message_cache[(chat_id, message_id)] = message

        file_size = int(message.file.size or 0)
        min_bytes = MIN_FILE_SIZE_MB * 1024 * 1024
        if file_size < min_bytes:
            return

        raw_name = message.file.name or f"media_{message_id}.bin"
        fname = auto_rename(raw_name)
        fname_lower = fname.lower()
        _, ext = os.path.splitext(fname_lower)

        if ALLOWED_EXT and ext not in ALLOWED_EXT:
            return

        if self._is_keyword_blocked(fname_lower):
            logger.info(f"Ignored media matching blocked keyword: {fname}")
            return

        if not self._is_keyword_allowed(fname_lower):
            return

        # Quality grouping
        group_key = self._group_key(fname, getattr(message, "grouped_id", None))
        buf_key = (chat_id, group_key)

        entry = {
            "chat_id": chat_id,
            "message_id": message_id,
            "fname": fname,
            "size": file_size,
            "event": event,
        }

        if buf_key not in self._quality_buffer:
            self._quality_buffer[buf_key] = []
        self._quality_buffer[buf_key].append(entry)

        if buf_key in self._quality_timers:
            self._quality_timers[buf_key].cancel()

        # For private chats / Saved Messages / non-album single forwards: wait only 1.0s instead of 30s
        is_direct = (
            getattr(event, "is_private", False)
            or (self._my_id and chat_id == self._my_id)
            or not getattr(message, "grouped_id", None)
        )
        wait_delay = 1.0 if is_direct else QUALITY_WAIT_SECS

        loop = asyncio.get_running_loop()
        self._quality_timers[buf_key] = loop.call_later(
            wait_delay,
            lambda k=buf_key: asyncio.create_task(self._flush_quality_group(k)),
        )

    def attach_to_client(self, client) -> None:
        """Attaches sniffer event listener to the Telethon client."""
        if not client or self.is_running:
            return

        self._client = client

        # Pre-resolve active channels to numeric peer IDs
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._resolve_watched_peers())
        except RuntimeError:
            pass

        @client.on(events.NewMessage)
        async def sniffer_listener(event):
            try:
                # 1. Startup backlog cutoff: allow up to 10 minutes prior to account for update delays
                if hasattr(event.message, "date") and event.message.date:
                    msg_ts = event.message.date.timestamp()
                    if msg_ts < (self.startup_time - 600.0):
                        return

                # If active channels configured, filter by chat
                if self.active_channels:
                    chat_id = event.chat_id
                    sender_id = getattr(event, "sender_id", None)

                    # Check for forwarded origin (if forwarded from a watched channel or bot)
                    fwd_id = None
                    if hasattr(event.message, "fwd_from") and event.message.fwd_from:
                        fwd = event.message.fwd_from
                        if hasattr(fwd, "from_id") and fwd.from_id:
                            fwd_id = utils.get_peer_id(fwd.from_id)
                        elif hasattr(fwd, "channel_id") and fwd.channel_id:
                            from telethon.tl.types import PeerChannel
                            fwd_id = utils.get_peer_id(PeerChannel(fwd.channel_id))

                    # Check if chat entity is available in memory or fetch it
                    chat = event.chat
                    if chat is None:
                        try:
                            chat = await event.get_chat()
                        except Exception:
                            chat = None

                    username = getattr(chat, "username", "") or ""

                    matched = False

                    # Check Saved Messages
                    is_saved_msg = bool(self._my_id and (chat_id == self._my_id or (getattr(event, "is_private", False) and sender_id == self._my_id)))
                    has_saved_watched = any(str(ac).lower() in ("me", "saved", "self", "saved messages", "@me") for ac in self.active_channels) or (self._my_id and self._my_id in self.active_channels)
                    if is_saved_msg and has_saved_watched:
                        matched = True

                    # 1. Match against resolved peer IDs or raw IDs
                    check_ids = [chat_id]
                    if sender_id is not None:
                        check_ids.append(sender_id)
                    if fwd_id is not None:
                        check_ids.append(fwd_id)

                    if not matched:
                        for cid in check_ids:
                            if cid is None:
                                continue
                            if cid in self.active_channels or abs(cid) in self.active_channels:
                                matched = True
                                break
                            cid_str = str(cid).replace("-100", "")
                            for ac in self.active_channels:
                                if str(ac).replace("-100", "") == cid_str:
                                    matched = True
                                    break
                            if matched:
                                break

                    # 2. Match against usernames
                    if not matched and username:
                        clean_un = username.lower().lstrip("@")
                        for ac in self.active_channels:
                            if str(ac).lower().lstrip("@") == clean_un:
                                matched = True
                                break

                    # 3. Match against cached resolved IDs
                    if not matched:
                        for cid in check_ids:
                            if cid in self._resolved_peer_ids:
                                matched = True
                                break

                    if not matched:
                        return

                await self._process_incoming_media(event)
            except Exception as e:
                logger.error(f"Error in sniffer message handler: {e}")

        self._sniffer_handler_ref = sniffer_listener
        self.is_running = True
        logger.info(f"Sniffer service attached and active on {len(self.active_channels)} channel(s).")


sniffer_service = SnifferService()

