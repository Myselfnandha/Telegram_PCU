import uuid
import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, update

from app.db.models.telegram import TelegramAccount, TelegramSessionVault
from app.db.base import utc_now
from app.security.encryption import (
    encrypt_session_string,
    decrypt_session_string,
    SessionDecryptionError
)

logger = logging.getLogger("session_vault")


class VaultConcurrencyError(Exception):
    """Raised when an optimistic concurrency check fails on session_version update."""
    pass


class SessionVaultRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _verify_account_ownership(self, owner_user_id: uuid.UUID, account_id: uuid.UUID) -> TelegramAccount:
        stmt = select(TelegramAccount).where(
            TelegramAccount.id == account_id,
            TelegramAccount.owner_user_id == owner_user_id
        )
        res = await self.session.execute(stmt)
        account = res.scalar_one_or_none()
        if not account:
            raise PermissionError(f"Telegram account {account_id} does not exist or is not owned by user {owner_user_id}.")
        return account

    async def save_session(
        self,
        owner_user_id: uuid.UUID,
        account_id: uuid.UUID,
        plaintext_session: str,
        expected_version: Optional[int] = None,
        key_version: int = 1
    ) -> int:
        """
        Encrypts and stores a Telegram session string with atomic concurrency checks.
        Returns the new session_version.
        """
        await self._verify_account_ownership(owner_user_id, account_id)

        if not plaintext_session or not plaintext_session.strip():
            raise ValueError("Refusing to save empty session string to vault.")

        ciphertext, nonce, kv = encrypt_session_string(
            plaintext_session=plaintext_session,
            tenant_id=owner_user_id,
            telegram_account_id=account_id,
            key_version=key_version
        )

        stmt = select(TelegramSessionVault).where(
            TelegramSessionVault.telegram_account_id == account_id
        )
        res = await self.session.execute(stmt)
        vault_entry = res.scalar_one_or_none()

        if vault_entry is None:
            new_version = 1
            vault_entry = TelegramSessionVault(
                telegram_account_id=account_id,
                ciphertext=ciphertext,
                nonce=nonce,
                key_version=kv,
                session_version=new_version
            )
            self.session.add(vault_entry)
        else:
            if expected_version is not None and vault_entry.session_version != expected_version:
                raise VaultConcurrencyError(
                    f"Session version conflict: expected {expected_version}, but found {vault_entry.session_version}."
                )
            new_version = vault_entry.session_version + 1
            vault_entry.ciphertext = ciphertext
            vault_entry.nonce = nonce
            vault_entry.key_version = kv
            vault_entry.session_version = new_version
            vault_entry.updated_at = utc_now()

        await self.session.flush()
        logger.info(f"Encrypted session saved to vault for account {account_id} (version={new_version}, key_ver={kv})")
        return new_version

    async def load_session(
        self,
        owner_user_id: uuid.UUID,
        account_id: uuid.UUID
    ) -> str:
        """
        Loads and decrypts a Telegram StringSession from the vault.
        Strictly verifies that the account belongs to the tenant.
        """
        await self._verify_account_ownership(owner_user_id, account_id)

        stmt = select(TelegramSessionVault).where(
            TelegramSessionVault.telegram_account_id == account_id
        )
        res = await self.session.execute(stmt)
        vault_entry = res.scalar_one_or_none()
        if not vault_entry:
            raise FileNotFoundError(f"No stored session vault entry found for account {account_id}.")

        decrypted_session = decrypt_session_string(
            ciphertext=vault_entry.ciphertext,
            nonce=vault_entry.nonce,
            tenant_id=owner_user_id,
            telegram_account_id=account_id,
            key_version=vault_entry.key_version
        )
        return decrypted_session

    async def rotate_key(
        self,
        owner_user_id: uuid.UUID,
        account_id: uuid.UUID,
        new_key_version: int
    ) -> int:
        """Re-encrypts the stored session with a new key version."""
        current_session = await self.load_session(owner_user_id, account_id)
        new_version = await self.save_session(
            owner_user_id=owner_user_id,
            account_id=account_id,
            plaintext_session=current_session,
            key_version=new_key_version
        )
        logger.info(f"Successfully rotated encryption key for account {account_id} to key_version={new_key_version}")
        return new_version

    async def delete_session(
        self,
        owner_user_id: uuid.UUID,
        account_id: uuid.UUID
    ) -> bool:
        """Deletes the stored session vault entry for an account."""
        await self._verify_account_ownership(owner_user_id, account_id)

        stmt = (
            delete(TelegramSessionVault)
            .where(TelegramSessionVault.telegram_account_id == account_id)
        )
        res = await self.session.execute(stmt)
        await self.session.flush()
        return res.rowcount > 0
