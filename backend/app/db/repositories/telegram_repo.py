import uuid
from typing import Optional, List
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, and_
from app.db.models.telegram import TelegramAccount, TelegramSessionVault, TelegramClientLease
from app.db.repositories.base_repo import BaseRepository
from app.db.base import utc_now


class DuplicateTelegramAccountError(Exception):
    """Raised when attempting to link a Telegram account that is already claimed."""
    pass


class PrimaryAccountConflictError(Exception):
    """Raised when a user attempts to add multiple primary Telegram accounts."""
    pass


class TelegramAccountRepository(BaseRepository[TelegramAccount]):
    def __init__(self, session: AsyncSession):
        super().__init__(TelegramAccount, session)

    async def create_account(
        self,
        owner_user_id: uuid.UUID,
        telegram_user_id: int,
        role: str = "primary",
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        phone_number: Optional[str] = None,
        status: str = "linked",
        account_id: Optional[uuid.UUID] = None
    ) -> TelegramAccount:
        if role not in ("primary", "turbo"):
            raise ValueError(f"Invalid account role: {role}")

        # Check global uniqueness of Telegram user ID
        existing_global = await self.get_by_telegram_user_id(telegram_user_id)
        if existing_global:
            if existing_global.owner_user_id != owner_user_id:
                raise DuplicateTelegramAccountError(
                    f"Telegram user ID {telegram_user_id} is already linked to another application account."
                )
            else:
                return existing_global

        # Enforce one primary account per owner
        if role == "primary":
            existing_primary = await self.get_primary_account(owner_user_id)
            if existing_primary:
                raise PrimaryAccountConflictError(
                    f"User {owner_user_id} already has an active primary Telegram account ({existing_primary.telegram_user_id})."
                )

        account = TelegramAccount(
            id=account_id or uuid.uuid4(),
            owner_user_id=owner_user_id,
            telegram_user_id=telegram_user_id,
            role=role,
            username=username,
            first_name=first_name,
            phone_number=phone_number,
            status=status
        )
        self.session.add(account)
        await self.session.flush()
        return account

    async def get_by_telegram_user_id(self, telegram_user_id: int) -> Optional[TelegramAccount]:
        stmt = select(TelegramAccount).where(TelegramAccount.telegram_user_id == telegram_user_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_primary_account(self, owner_user_id: uuid.UUID) -> Optional[TelegramAccount]:
        stmt = select(TelegramAccount).where(
            TelegramAccount.owner_user_id == owner_user_id,
            TelegramAccount.role == "primary"
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_user_accounts(self, owner_user_id: uuid.UUID) -> List[TelegramAccount]:
        return await self.list_by_owner(owner_user_id)

    async def update_status(self, account_id: uuid.UUID, owner_user_id: uuid.UUID, status: str) -> bool:
        stmt = (
            update(TelegramAccount)
            .where(
                TelegramAccount.id == account_id,
                TelegramAccount.owner_user_id == owner_user_id
            )
            .values(status=status, updated_at=utc_now())
        )
        res = await self.session.execute(stmt)
        await self.session.flush()
        return res.rowcount > 0

    async def acquire_lease(
        self,
        account_id: uuid.UUID,
        owner_instance_id: uuid.UUID,
        ttl_seconds: int = 60
    ) -> bool:
        """Acquires or renews an exclusive client lease using fencing epochs."""
        now = utc_now()
        expires = now + timedelta(seconds=ttl_seconds)

        stmt = select(TelegramClientLease).where(TelegramClientLease.telegram_account_id == account_id)
        res = await self.session.execute(stmt)
        lease = res.scalar_one_or_none()

        if lease is None:
            new_lease = TelegramClientLease(
                telegram_account_id=account_id,
                owner_instance_id=owner_instance_id,
                fencing_epoch=1,
                expires_at=expires
            )
            self.session.add(new_lease)
            await self.session.flush()
            return True

        # Check if lease expired or already held by this instance
        if lease.expires_at < now or lease.owner_instance_id == owner_instance_id:
            lease.owner_instance_id = owner_instance_id
            lease.fencing_epoch += 1
            lease.expires_at = expires
            lease.updated_at = now
            await self.session.flush()
            return True

        return False

    async def release_lease(self, account_id: uuid.UUID, owner_instance_id: uuid.UUID) -> bool:
        stmt = (
            update(TelegramClientLease)
            .where(
                TelegramClientLease.telegram_account_id == account_id,
                TelegramClientLease.owner_instance_id == owner_instance_id
            )
            .values(expires_at=utc_now())
        )
        res = await self.session.execute(stmt)
        await self.session.flush()
        return res.rowcount > 0
