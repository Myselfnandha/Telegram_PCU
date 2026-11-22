import os
import time
import uuid
import logging
import asyncio
from typing import Optional, Dict, List, Any
from dataclasses import dataclass
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import AuthKeyUnregisteredError, SessionRevokedError, UserDeactivatedError

from app.config import API_ID, API_HASH
from app.auth_v3.models import Principal
from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.db.models.telegram import TelegramAccount
from app.db.repositories.telegram_repo import TelegramAccountRepository
from app.db.repositories.audit_repo import AuditRepository
from app.telegram.session_vault import SessionVaultRepository

logger = logging.getLogger("multi_tenant_client_manager")

IDLE_DISCONNECT_SECONDS = int(os.getenv("TELEGRAM_IDLE_DISCONNECT_SECS", "180"))


class AccountNotLinkedError(Exception):
    """Raised when no active Telegram account is found for the user."""
    pass


class RelinkRequiredError(Exception):
    """Raised when Telegram credentials have expired or were revoked remotely."""
    pass


@dataclass
class ManagedClient:
    account_id: uuid.UUID
    owner_user_id: uuid.UUID
    client: TelegramClient
    role: str
    connected_at: float
    last_accessed_at: float


class MultiTenantTelegramManager:
    """
    Manages Telegram MTProto connections scoped to individual tenant identities.
    Replaces global singleton client models with lazy-loaded, isolated connection pools.
    """

    def __init__(self, idle_timeout_seconds: int = IDLE_DISCONNECT_SECONDS):
        self.idle_timeout_seconds = idle_timeout_seconds
        # In-memory connection cache: {account_id: ManagedClient}
        self._active_clients: Dict[uuid.UUID, ManagedClient] = {}
        self._lock = asyncio.Lock()
        self._idle_sweeper_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Starts background idle connection cleaner."""
        if self._idle_sweeper_task is None or self._idle_sweeper_task.done():
            self._idle_sweeper_task = asyncio.create_task(self._sweeper_loop())

    async def stop(self) -> None:
        """Disconnects all managed clients cleanly on server shutdown."""
        if self._idle_sweeper_task and not self._idle_sweeper_task.done():
            self._idle_sweeper_task.cancel()

        async with self._lock:
            for account_id, managed in list(self._active_clients.items()):
                try:
                    if managed.client.is_connected():
                        await managed.client.disconnect()
                except Exception as e:
                    logger.debug(f"Error disconnecting client {account_id}: {e}")
            self._active_clients.clear()
        logger.info("MultiTenantTelegramManager stopped and all clients disconnected.")

    async def _sweeper_loop(self) -> None:
        """Periodic loop disconnecting idle connections."""
        while True:
            try:
                await asyncio.sleep(30)
                await self.disconnect_idle_clients()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in idle connection sweeper: {e}")

    async def disconnect_idle_clients(self) -> int:
        """Disconnects clients that have exceeded the idle timeout."""
        now = time.time()
        disconnected_count = 0

        async with self._lock:
            for account_id, managed in list(self._active_clients.items()):
                if now - managed.last_accessed_at > self.idle_timeout_seconds:
                    logger.info(
                        f"Disconnecting idle Telegram client {account_id} "
                        f"(idle for {int(now - managed.last_accessed_at)}s)"
                    )
                    try:
                        if managed.client.is_connected():
                            await managed.client.disconnect()
                    except Exception as e:
                        logger.warning(f"Error while disconnecting idle client {account_id}: {e}")
                    self._active_clients.pop(account_id, None)
                    disconnected_count += 1

        return disconnected_count

    async def get_client(
        self,
        principal: Principal,
        account_id: Optional[uuid.UUID] = None
    ) -> TelegramClient:
        """
        Retrieves or lazily initializes an authorized Telethon client for the given tenant.
        Strictly enforces tenant ownership boundaries.
        """
        session_maker = get_sessionmaker()
        async with session_maker() as db_session:
            tg_repo = TelegramAccountRepository(db_session)
            vault_repo = SessionVaultRepository(db_session)
            audit_repo = AuditRepository(db_session)

            # Resolve account
            account: Optional[TelegramAccount] = None
            if account_id:
                account = await tg_repo.get_scoped(account_id, principal.user_id)
            else:
                account = await tg_repo.get_primary_account(principal.user_id)

            if not account:
                raise AccountNotLinkedError(
                    f"No linked Telegram account found for user {principal.user_id}."
                )

            if account.status == "relink_required":
                raise RelinkRequiredError(
                    f"Telegram account {account.id} session is revoked or invalid. Relinking is required."
                )

            if account.status == "unlinked":
                raise AccountNotLinkedError(f"Telegram account {account.id} is unlinked.")

            # Check cached active client
            async with self._lock:
                if account.id in self._active_clients:
                    managed = self._active_clients[account.id]
                    if managed.client.is_connected():
                        managed.last_accessed_at = time.time()
                        return managed.client
                    else:
                        # Stale connection in cache, discard and recreate
                        self._active_clients.pop(account.id, None)

            # Lazy initialization: load session string from encrypted vault
            session_str = await vault_repo.load_session(principal.user_id, account.id)

            client = TelegramClient(
                StringSession(session_str),
                API_ID,
                API_HASH,
                device_model="TG Power Suite v3",
                system_version="Linux Multi-Tenant",
                app_version="3.0.0"
            )

            try:
                await client.connect()
                is_auth = await client.is_user_authorized()
                if not is_auth:
                    raise RelinkRequiredError("Telegram user authorization check failed.")
            except (AuthKeyUnregisteredError, SessionRevokedError, UserDeactivatedError, RelinkRequiredError) as e:
                # Mark account as relink_required in database
                logger.warning(f"Telegram authorization revoked for account {account.id}: {e}")
                await tg_repo.update_status(account.id, principal.user_id, "relink_required")
                await audit_repo.log_event(
                    event_type="telegram.auth_revoked",
                    user_id=principal.user_id,
                    telegram_account_id=account.id,
                    metadata={"reason": str(e)}
                )
                await db_session.commit()
                if client.is_connected():
                    await client.disconnect()
                raise RelinkRequiredError(
                    "Telegram session has expired or was revoked. Please relink your Telegram account."
                )

            # Cache connected client
            now = time.time()
            managed = ManagedClient(
                account_id=account.id,
                owner_user_id=principal.user_id,
                client=client,
                role=account.role,
                connected_at=now,
                last_accessed_at=now
            )
            async with self._lock:
                self._active_clients[account.id] = managed

            logger.info(f"Connected and cached Telegram client for account {account.id} (user {principal.user_id})")
            return client

    async def get_eligible_turbo_accounts(self, principal: Principal) -> List[TelegramAccount]:
        """
        Returns all verified secondary ('turbo') accounts belonging strictly to the requesting user.
        Guarantees tenant isolation for Multi-Turbo operations.
        """
        session_maker = get_sessionmaker()
        async with session_maker() as db_session:
            tg_repo = TelegramAccountRepository(db_session)
            accounts = await tg_repo.list_user_accounts(principal.user_id)
            return [
                a for a in accounts
                if a.role == "turbo" and a.status in ("linked", "active")
            ]

    async def revoke_telegram_account(
        self,
        principal: Principal,
        account_id: uuid.UUID
    ) -> bool:
        """
        Safely disconnects client, purges vault encryption entry, and removes account record.
        """
        # Disconnect active client if present
        async with self._lock:
            if account_id in self._active_clients:
                managed = self._active_clients.pop(account_id)
                try:
                    if managed.client.is_connected():
                        await managed.client.disconnect()
                except Exception as e:
                    logger.debug(f"Error disconnecting client {account_id}: {e}")

        session_maker = get_sessionmaker()
        async with session_maker() as db_session:
            tg_repo = TelegramAccountRepository(db_session)
            vault_repo = SessionVaultRepository(db_session)
            audit_repo = AuditRepository(db_session)

            account = await tg_repo.get_scoped(account_id, principal.user_id)
            if not account:
                return False

            # Delete vault entry
            try:
                await vault_repo.delete_session(principal.user_id, account_id)
            except Exception as e:
                logger.warning(f"Vault session deletion notice: {e}")

            # Delete account record
            deleted = await tg_repo.delete_account(account_id, principal.user_id)
            if deleted:
                await audit_repo.log_event(
                    event_type="telegram.unlinked",
                    user_id=principal.user_id,
                    telegram_account_id=account_id,
                    metadata={"telegram_user_id": account.telegram_user_id}
                )
                await db_session.commit()
                return True

        return False

    async def set_primary_account(
        self,
        principal: Principal,
        account_id: uuid.UUID
    ) -> bool:
        session_maker = get_sessionmaker()
        async with session_maker() as db_session:
            tg_repo = TelegramAccountRepository(db_session)
            audit_repo = AuditRepository(db_session)
            success = await tg_repo.set_primary(account_id, principal.user_id)
            if success:
                await audit_repo.log_event(
                    event_type="telegram.primary_swapped",
                    user_id=principal.user_id,
                    telegram_account_id=account_id
                )
                await db_session.commit()
            return success


# Global multi-tenant manager instance
multi_tenant_manager = MultiTenantTelegramManager()
