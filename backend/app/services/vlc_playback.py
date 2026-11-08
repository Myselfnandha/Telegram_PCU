from __future__ import annotations

import hmac
import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional, Union
from app.config import API_HASH

SECRET = (API_HASH or "tg_power_suite_secret").encode()

@dataclass(frozen=True)
class PlaybackGrant:
    chat_id: str
    message_id: int
    expires_at: Optional[float]

class PlaybackGrantStore:
    def __init__(self, ttl_seconds: Optional[int] = None, max_grants: int = 8192):
        self.ttl_seconds = ttl_seconds
        self.max_grants = max_grants
        self._grants: Dict[str, PlaybackGrant] = {}
        self._lock = threading.Lock()

    def _norm_chat(self, chat_id: Union[str, int]) -> str:
        s = str(chat_id).strip()
        if s.startswith("-100"):
            return s[4:]
        if s.startswith("-"):
            return s[1:]
        return s

    def _get_sig(self, chat_id: Union[str, int], message_id: int) -> str:
        norm_chat = self._norm_chat(chat_id)
        data = f"{norm_chat}:{message_id}".encode()
        return hmac.new(SECRET, data, hashlib.sha256).hexdigest()

    def issue(self, chat_id: Union[str, int], message_id: int) -> str:
        now = time.time()
        token = self._get_sig(chat_id, message_id)
        exp = (now + self.ttl_seconds) if (self.ttl_seconds is not None) else None
        grant = PlaybackGrant(str(chat_id), int(message_id), exp)
        with self._lock:
            self._prune(now)
            if len(self._grants) >= self.max_grants:
                oldest = min(self._grants, key=lambda k: self._grants[k].expires_at or float("inf"))
                self._grants.pop(oldest, None)
            self._grants[token] = grant
        return token

    def allows(self, token: str, chat_id: Union[str, int], message_id: int) -> bool:
        if not token:
            return False
        if self.ttl_seconds == 0:
            return False
        now = time.time()
        with self._lock:
            grant = self._grants.get(token)
            if grant:
                if grant.expires_at is not None and grant.expires_at <= now:
                    self._grants.pop(token, None)
                    return False
                norm_grant = self._norm_chat(grant.chat_id)
                norm_target = self._norm_chat(chat_id)
                if norm_grant == norm_target and grant.message_id == int(message_id):
                    return True

        expected = self._get_sig(chat_id, message_id)
        return hmac.compare_digest(token, expected)

    def _prune(self, now: float) -> None:
        expired = [k for k, g in self._grants.items() if g.expires_at is not None and g.expires_at <= now]
        for k in expired:
            self._grants.pop(k, None)

playback_grants = PlaybackGrantStore(ttl_seconds=None)
