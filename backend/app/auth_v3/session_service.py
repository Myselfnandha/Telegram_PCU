import os
import secrets
import hashlib
import uuid
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from fastapi import Request, Response, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.db.session import get_db, set_tenant_context
from app.db.models.user import AppSession, AppUser
from app.db.repositories.user_repo import UserRepository
from app.db.repositories.audit_repo import AuditRepository
from app.auth_v3.models import Principal

logger = logging.getLogger("session_service")

SESSION_TTL_HOURS = int(os.getenv("APP_SESSION_TTL_HOURS", "168"))  # 7 days
COOKIE_NAME = "tg_session"
IS_PRODUCTION = os.getenv("ENVIRONMENT", "development").lower() in ("production", "prod")

security_bearer = HTTPBearer(auto_error=False)


class SessionService:
    @staticmethod
    def hash_token(raw_token: str) -> bytes:
        """Computes SHA-256 hash of the opaque session token for zero-knowledge storage."""
        return hashlib.sha256(raw_token.encode("utf-8")).digest()

    @classmethod
    async def create_session(
        cls,
        session: AsyncSession,
        user_id: uuid.UUID,
        device_id: Optional[uuid.UUID] = None,
        ttl_hours: int = SESSION_TTL_HOURS
    ) -> Tuple[str, AppSession]:
        user_repo = UserRepository(session)
        raw_token = secrets.token_urlsafe(32)
        token_hash = cls.hash_token(raw_token)
        expires_at = utc_now() + timedelta(hours=ttl_hours)

        app_session = await user_repo.create_session(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            device_id=device_id
        )
        return raw_token, app_session

    @classmethod
    async def validate_session(
        cls,
        session: AsyncSession,
        raw_token: str
    ) -> Optional[Principal]:
        if not raw_token or not raw_token.strip():
            return None

        user_repo = UserRepository(session)
        token_hash = cls.hash_token(raw_token)
        app_sess = await user_repo.get_valid_session(token_hash)
        if not app_sess:
            return None

        # Verify device validity if attached
        if app_sess.device_id:
            device = await user_repo.get_device(app_sess.device_id, app_sess.user_id)
            if not device or device.revoked_at is not None:
                # Device was revoked - session is invalid
                return None
            # Update device last seen
            await user_repo.update_device_seen(device.id)

        user = await user_repo.get_by_id(app_sess.user_id)
        if not user or user.status != "active":
            return None

        return Principal(
            user_id=user.id,
            role=user.role,
            display_name=user.display_name,
            device_id=app_sess.device_id,
            session_id=app_sess.id
        )

    @classmethod
    async def revoke_session(
        cls,
        session: AsyncSession,
        session_id: uuid.UUID,
        user_id: uuid.UUID
    ) -> bool:
        user_repo = UserRepository(session)
        audit_repo = AuditRepository(session)
        success = await user_repo.revoke_session(session_id, user_id)
        if success:
            await audit_repo.log_event(
                event_type="session.revoked",
                user_id=user_id,
                metadata={"session_id": str(session_id)}
            )
        return success

    @classmethod
    async def revoke_all_user_sessions(
        cls,
        session: AsyncSession,
        user_id: uuid.UUID
    ) -> int:
        user_repo = UserRepository(session)
        audit_repo = AuditRepository(session)
        count = await user_repo.revoke_all_user_sessions(user_id)
        await audit_repo.log_event(
            event_type="sessions.revoked_all",
            user_id=user_id,
            metadata={"revoked_count": count}
        )
        return count

    @staticmethod
    def set_session_cookie(response: Response, raw_token: str, ttl_hours: int = SESSION_TTL_HOURS) -> None:
        response.set_cookie(
            key=COOKIE_NAME,
            value=raw_token,
            max_age=ttl_hours * 3600,
            httponly=True,
            secure=IS_PRODUCTION,
            samesite="lax",
            path="/"
        )

    @staticmethod
    def clear_session_cookie(response: Response) -> None:
        response.delete_cookie(
            key=COOKIE_NAME,
            path="/",
            httponly=True,
            samesite="lax"
        )


async def get_optional_principal(
    request: Request,
    bearer: Optional[HTTPAuthorizationCredentials] = Depends(security_bearer),
    db: AsyncSession = Depends(get_db)
) -> Optional[Principal]:
    """Resolves authenticated Principal if a valid token is present; returns None otherwise."""
    raw_token: Optional[str] = None
    if bearer and bearer.credentials:
        raw_token = bearer.credentials
    elif COOKIE_NAME in request.cookies:
        raw_token = request.cookies[COOKIE_NAME]

    if not raw_token:
        return None

    principal = await SessionService.validate_session(db, raw_token)
    if principal:
        await set_tenant_context(db, str(principal.user_id))
    return principal


async def get_current_principal(
    principal: Optional[Principal] = Depends(get_optional_principal)
) -> Principal:
    """FastAPI dependency requiring a fully authenticated user."""
    if not principal:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"}
        )
    return principal


async def require_admin(
    principal: Principal = Depends(get_current_principal)
) -> Principal:
    """FastAPI dependency requiring administrator privileges."""
    if not principal.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrative privileges required."
        )
    return principal
