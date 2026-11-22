import uuid
from typing import Optional, List
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, and_
from app.db.models.user import AppUser, LoginCredential, TrustedDevice, AppSession
from app.db.repositories.base_repo import BaseRepository
from app.db.base import utc_now


class UserRepository(BaseRepository[AppUser]):
    def __init__(self, session: AsyncSession):
        super().__init__(AppUser, session)

    async def create_user(
        self,
        display_name: Optional[str] = None,
        status: str = "active",
        role: str = "user",
        user_id: Optional[uuid.UUID] = None
    ) -> AppUser:
        user = AppUser(
            id=user_id or uuid.uuid4(),
            display_name=display_name,
            status=status,
            role=role
        )
        self.session.add(user)
        await self.session.flush()
        return user

    async def create_device(
        self,
        user_id: uuid.UUID,
        display_name: str,
        device_fingerprint: Optional[str] = None,
        public_key: Optional[bytes] = None,
        verified: bool = True
    ) -> TrustedDevice:
        device = TrustedDevice(
            id=uuid.uuid4(),
            user_id=user_id,
            display_name=display_name,
            device_fingerprint=device_fingerprint,
            public_key=public_key,
            verified_at=utc_now() if verified else None,
            last_seen_at=utc_now()
        )
        self.session.add(device)
        await self.session.flush()
        return device

    async def list_devices(self, user_id: uuid.UUID) -> List[TrustedDevice]:
        stmt = select(TrustedDevice).where(
            TrustedDevice.user_id == user_id,
            TrustedDevice.revoked_at.is_(None)
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def revoke_device(self, device_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        stmt = (
            update(TrustedDevice)
            .where(
                TrustedDevice.id == device_id,
                TrustedDevice.user_id == user_id,
                TrustedDevice.revoked_at.is_(None)
            )
            .values(revoked_at=utc_now())
        )
        res = await self.session.execute(stmt)
        # Also revoke all active sessions associated with this device
        if res.rowcount > 0:
            sess_stmt = (
                update(AppSession)
                .where(
                    AppSession.device_id == device_id,
                    AppSession.revoked_at.is_(None)
                )
                .values(revoked_at=utc_now())
            )
            await self.session.execute(sess_stmt)
            await self.session.flush()
            return True
        return False

    async def create_session(
        self,
        user_id: uuid.UUID,
        token_hash: bytes,
        expires_at: datetime,
        device_id: Optional[uuid.UUID] = None
    ) -> AppSession:
        app_sess = AppSession(
            id=uuid.uuid4(),
            user_id=user_id,
            device_id=device_id,
            token_hash=token_hash,
            expires_at=expires_at
        )
        self.session.add(app_sess)
        await self.session.flush()
        return app_sess

    async def get_valid_session(self, token_hash: bytes) -> Optional[AppSession]:
        now = utc_now()
        stmt = select(AppSession).where(
            AppSession.token_hash == token_hash,
            AppSession.revoked_at.is_(None),
            AppSession.expires_at > now
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def revoke_session(self, session_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        stmt = (
            update(AppSession)
            .where(
                AppSession.id == session_id,
                AppSession.user_id == user_id,
                AppSession.revoked_at.is_(None)
            )
            .values(revoked_at=utc_now())
        )
        res = await self.session.execute(stmt)
        await self.session.flush()
        return res.rowcount > 0
