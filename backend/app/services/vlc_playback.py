"""Short-lived, media-scoped capabilities for VLC clients that cannot set auth headers."""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class PlaybackGrant:
    chat_id: str
    message_id: int
    expires_at: float


class PlaybackGrantStore:
    """In-memory playback grants; each grant authorizes one media item, not the API."""

    def __init__(self, ttl_seconds: int = 4 * 60 * 60, max_grants: int = 4096):
        self.ttl_seconds = ttl_seconds
        self.max_grants = max_grants
        self._grants: Dict[str, PlaybackGrant] = {}
        self._lock = threading.Lock()

    def issue(self, chat_id: str | int, message_id: int) -> str:
        now = time.time()
        token = secrets.token_urlsafe(32)
        grant = PlaybackGrant(str(chat_id), int(message_id), now + self.ttl_seconds)
        with self._lock:
            self._prune(now)
            if len(self._grants) >= self.max_grants:
                # Evict the grant closest to expiry rather than growing without bound.
                oldest = min(self._grants, key=lambda key: self._grants[key].expires_at)
                self._grants.pop(oldest, None)
            self._grants[token] = grant
        return token

    def allows(self, token: str, chat_id: str, message_id: int) -> bool:
        if not token:
            return False
        now = time.time()
        with self._lock:
            grant = self._grants.get(token)
            if not grant:
                self._prune(now)
                return False
            if grant.expires_at <= now:
                self._grants.pop(token, None)
                return False
            return grant.chat_id == str(chat_id) and grant.message_id == int(message_id)

    def _prune(self, now: float) -> None:
        expired = [key for key, grant in self._grants.items() if grant.expires_at <= now]
        for key in expired:
            self._grants.pop(key, None)


playback_grants = PlaybackGrantStore()
