import os
import time
import uuid
import json
import base64
import hmac
import hashlib
import threading
import logging
from typing import Optional, Dict, Any, List, Union, Set
from dataclasses import dataclass, asdict

from app.config import API_HASH

logger = logging.getLogger("playback_grants")

# Use master key or API_HASH for cryptographic signing
_RAW_KEY = os.getenv("SESSION_MASTER_KEY_B64", "") or API_HASH or "tg_power_suite_playback_master_secret"
SIGNING_KEY = hashlib.sha256(_RAW_KEY.encode("utf-8")).digest()


@dataclass
class TenantPlaybackGrant:
    grant_id: str
    user_id: uuid.UUID
    chat_id: str
    message_id: int
    operation: str  # "stream", "download", or "all"
    account_id: Optional[uuid.UUID]
    created_at: float
    expires_at: float
    revoked: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "grant_id": self.grant_id,
            "user_id": str(self.user_id),
            "chat_id": self.chat_id,
            "message_id": self.message_id,
            "operation": self.operation,
            "account_id": str(self.account_id) if self.account_id else None,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "revoked": self.revoked
        }


def normalize_chat_id(chat_id: Union[str, int]) -> str:
    """Normalizes Telegram chat ID by stripping -100 or - prefixes for uniform comparison."""
    s = str(chat_id).strip()
    if s.startswith("-100"):
        return s[4:]
    if s.startswith("-"):
        return s[1:]
    return s


class PlaybackGrantService:
    """
    Manages cryptographic, revocable, tenant-scoped Playback Grants for VLC,
    mobile players, and external streaming consumers.
    """
    def __init__(self, default_ttl_seconds: int = 14400, max_grants: int = 10000):
        self.default_ttl = default_ttl_seconds
        self.max_grants = max_grants
        self._grants: Dict[str, TenantPlaybackGrant] = {}
        self._revoked_grants: Set[str] = set()
        self._lock = threading.Lock()

    def _sign(self, data: bytes) -> str:
        return hmac.new(SIGNING_KEY, data, hashlib.sha256).hexdigest()

    def issue_grant(
        self,
        user_id: uuid.UUID,
        chat_id: Union[str, int],
        message_id: int,
        operation: str = "stream",
        account_id: Optional[uuid.UUID] = None,
        ttl_seconds: Optional[int] = None
    ) -> str:
        """Issues a signed, revocable playback grant token."""
        now = time.time()
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl
        expires_at = now + ttl
        grant_id = uuid.uuid4().hex

        norm_chat = normalize_chat_id(chat_id)
        grant = TenantPlaybackGrant(
            grant_id=grant_id,
            user_id=user_id,
            chat_id=norm_chat,
            message_id=int(message_id),
            operation=operation,
            account_id=account_id,
            created_at=now,
            expires_at=expires_at,
            revoked=False
        )

        payload_dict = {
            "gid": grant_id,
            "uid": str(user_id),
            "cid": norm_chat,
            "mid": int(message_id),
            "op": operation,
            "aid": str(account_id) if account_id else None,
            "exp": int(expires_at)
        }
        payload_bytes = json.dumps(payload_dict, separators=(",", ":")).encode("utf-8")
        b64_payload = base64.urlsafe_b64encode(payload_bytes).decode("ascii").rstrip("=")
        sig = self._sign(b64_payload.encode("ascii"))
        token = f"{b64_payload}.{sig}"

        with self._lock:
            self._prune(now)
            if len(self._grants) >= self.max_grants:
                oldest = min(self._grants, key=lambda k: self._grants[k].expires_at)
                self._grants.pop(oldest, None)
            self._grants[grant_id] = grant

        return token

    def verify_grant(
        self,
        token: str,
        chat_id: Union[str, int],
        message_id: int,
        operation: str = "stream"
    ) -> Optional[TenantPlaybackGrant]:
        """
        Validates token signature, expiration, non-revocation, and media scope.
        Returns TenantPlaybackGrant if valid, None otherwise.
        """
        if not token or "." not in token:
            return None

        parts = token.split(".", 1)
        if len(parts) != 2:
            return None

        b64_payload, signature = parts
        expected_sig = self._sign(b64_payload.encode("ascii"))
        if not hmac.compare_digest(signature, expected_sig):
            logger.warning("Playback grant signature mismatch")
            return None

        try:
            # Re-pad base64
            padded = b64_payload + "=" * ((4 - len(b64_payload) % 4) % 4)
            payload_bytes = base64.urlsafe_b64decode(padded)
            payload = json.loads(payload_bytes.decode("utf-8"))
        except Exception as e:
            logger.warning(f"Failed to decode playback grant payload: {e}")
            return None

        grant_id = payload.get("gid")
        user_id_str = payload.get("uid")
        cid = str(payload.get("cid", ""))
        mid = int(payload.get("mid", 0))
        op = payload.get("op", "stream")
        aid_str = payload.get("aid")
        exp = float(payload.get("exp", 0))

        now = time.time()
        if exp < now:
            logger.info(f"Playback grant {grant_id} has expired")
            return None

        with self._lock:
            if grant_id in self._revoked_grants:
                logger.warning(f"Playback grant {grant_id} has been revoked")
                return None

            stored_grant = self._grants.get(grant_id)
            if stored_grant and stored_grant.revoked:
                return None

        # Verify media scope
        norm_req_chat = normalize_chat_id(chat_id)
        if cid != norm_req_chat or mid != int(message_id):
            logger.warning(
                f"Playback grant media mismatch: grant=({cid}, {mid}) vs req=({norm_req_chat}, {message_id})"
            )
            return None

        # Verify operation scope
        if op != "all" and op != operation:
            logger.warning(f"Playback grant operation mismatch: grant={op} vs req={operation}")
            return None

        try:
            user_uuid = uuid.UUID(user_id_str)
            acc_uuid = uuid.UUID(aid_str) if aid_str else None
        except Exception:
            return None

        return TenantPlaybackGrant(
            grant_id=grant_id,
            user_id=user_uuid,
            chat_id=cid,
            message_id=mid,
            operation=op,
            account_id=acc_uuid,
            created_at=float(exp - self.default_ttl),
            expires_at=exp,
            revoked=False
        )

    def revoke_grant(self, user_id: uuid.UUID, grant_id: str) -> bool:
        """Revokes an active grant by ID for the authorized owner."""
        with self._lock:
            self._revoked_grants.add(grant_id)
            if grant_id in self._grants:
                grant = self._grants[grant_id]
                if grant.user_id == user_id:
                    grant.revoked = True
                    return True
                else:
                    return False
            # Even if not in local memory, record in revoked set
            return True

    def revoke_all_user_grants(self, user_id: uuid.UUID) -> int:
        """Revokes all active grants belonging to a user."""
        count = 0
        with self._lock:
            for gid, grant in list(self._grants.items()):
                if grant.user_id == user_id:
                    grant.revoked = True
                    self._revoked_grants.add(gid)
                    count += 1
        return count

    def list_user_grants(self, user_id: uuid.UUID) -> List[Dict[str, Any]]:
        """Lists active (non-expired, non-revoked) grants for a user."""
        now = time.time()
        with self._lock:
            self._prune(now)
            active = [
                g.to_dict() for g in self._grants.values()
                if g.user_id == user_id and not g.revoked and g.expires_at > now
            ]
        return active

    def _prune(self, now: float) -> None:
        """Prunes expired grants to bound memory usage."""
        expired = [gid for gid, g in self._grants.items() if g.expires_at <= now]
        for gid in expired:
            self._grants.pop(gid, None)


playback_grant_service = PlaybackGrantService()
