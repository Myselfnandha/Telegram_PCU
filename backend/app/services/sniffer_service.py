import os
import re
import sys
import time
import logging
import asyncio
from typing import Dict, List, Set, Optional, Tuple, Any, Union
from telethon import events, utils
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
    save_env_dict
)
from app.services.manager_detector import auto_send, detect_managers, MANAGER_LABELS

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
        self._processed_messages: Set[Tuple[int, int]] = set()
        self._quality_buffer: Dict[Tuple[int, str], List[dict]] = {}
        self._quality_timers: Dict[Tuple[int, str], asyncio.TimerHandle] = {}
        self.recent_feed: List[dict] = []
        self.batch_active = False
        self.batch_links: List[dict] = []
        self.socket_emitter = None

        # Load channels from TARGET_CHANNELS
        for c in TARGET_CHANNELS:
            c = c.strip()
            if c.isdigit() or (c.startswith("-") and c[1:].isdigit()):
                self.active_channels.add(int(c))
            elif c:
                self.active_channels.add(c)

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

    def _is_duplicate(self, chat_id: int, message_id: int) -> bool:
        key = (chat_id, message_id)
        if key in self._processed_messages:
            return True
        self._processed_messages.add(key)
        if len(self._processed_messages) > 5000:
            # Prune oldest items safely down to 4000
            while len(self._processed_messages) > 4000:
                try:
                    self._processed_messages.pop()
                except KeyError:
                    break
        return False

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

    def get_status(self) -> dict:
        return {
            "is_running": self.is_running,
            "active_channels": [str(c) for c in self.active_channels],
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
        return f"name_{base}" if base else "name_unknown"

    async def _flush_quality_group(self, buf_key: tuple) -> None:
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
        link = f"http://{PROXY_HOST}:{PROXY_PORT}/dl/{chat_id}/{message_id}"

        self._message_cache[(chat_id, message_id)] = event.message

        if self._is_duplicate(chat_id, message_id):
            return

        mgr, pushed = await auto_send(link)
        label = MANAGER_LABELS.get(mgr, mgr)

        feed_item = {
            "id": f"sniff_{chat_id}_{message_id}",
            "chat_id": chat_id,
            "message_id": message_id,
            "filename": fname,
            "size_bytes": best["size"],
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

        if KEYWORD_BLOCK and any(kw in fname_lower for kw in KEYWORD_BLOCK):
            return

        if KEYWORD_ALLOW and not any(kw in fname_lower for kw in KEYWORD_ALLOW):
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

        loop = asyncio.get_running_loop()
        self._quality_timers[buf_key] = loop.call_later(
            QUALITY_WAIT_SECS,
            lambda k=buf_key: asyncio.create_task(self._flush_quality_group(k)),
        )

    def attach_to_client(self, client) -> None:
        """Attaches sniffer event listener to the Telethon client."""
        if not client or self.is_running:
            return

        self._client = client

        @client.on(events.NewMessage)
        async def sniffer_listener(event):
            try:
                # If active channels configured, filter by chat
                if self.active_channels:
                    chat = event.chat
                    chat_id = utils.get_peer_id(chat) if chat else None
                    username = getattr(chat, "username", "")
                    
                    matched = False
                    if chat_id is not None:
                        if chat_id in self.active_channels:
                            matched = True
                        elif abs(chat_id) in self.active_channels:
                            matched = True
                        elif str(chat_id).replace("-100", "") in [str(x).replace("-100", "") for x in self.active_channels]:
                            matched = True

                    if not matched and username:
                        clean_un = username.lower().lstrip("@")
                        for ac in self.active_channels:
                            if str(ac).lower().lstrip("@") == clean_un:
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
