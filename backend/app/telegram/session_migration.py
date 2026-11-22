import os
import sys
import uuid
import logging
from typing import Optional, Dict, Any, List, Tuple
from pathlib import Path
from dataclasses import dataclass, asdict

# Ensure backend directory is in sys.path
APP_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = APP_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from telethon.sessions import SQLiteSession, StringSession
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import SESSION_DIR, DATA_DIR, SESSION_FILE_PATH, SESSION_FILE_PATH_2
from app.db.models.user import AppUser
from app.db.models.telegram import TelegramAccount, TelegramSessionVault
from app.db.repositories.telegram_repo import TelegramAccountRepository, DuplicateTelegramAccountError, PrimaryAccountConflictError
from app.telegram.session_vault import SessionVaultRepository
from app.security.encryption import decrypt_session_string

logger = logging.getLogger("legacy_session_migration")


@dataclass
class SessionFileMetadata:
    path: str
    exists: bool
    size_bytes: int
    dc_id: Optional[int] = None
    server_address: Optional[str] = None
    port: Optional[int] = None
    has_auth_key: bool = False


@dataclass
class MigrationResult:
    success: bool
    dry_run: bool
    owner_user_id: str
    migrated_accounts: List[Dict[str, Any]]
    skipped_accounts: List[Dict[str, Any]]
    errors: List[str]
    notes: List[str]


class LegacySessionMigrator:
    def __init__(self, db_session: AsyncSession):
        self.session = db_session
        self.account_repo = TelegramAccountRepository(db_session)
        self.vault_repo = SessionVaultRepository(db_session)

    @staticmethod
    def inspect_legacy_sessions() -> Dict[str, Any]:
        """Inspects disk for legacy Telethon and Rust SQLite session files."""
        primary_path = SESSION_FILE_PATH.with_suffix(".session") if not str(SESSION_FILE_PATH).endswith(".session") else SESSION_FILE_PATH
        sec_path = SESSION_FILE_PATH_2.with_suffix(".session") if not str(SESSION_FILE_PATH_2).endswith(".session") else SESSION_FILE_PATH_2

        def _inspect_telethon(path: Path) -> SessionFileMetadata:
            if not path.exists():
                return SessionFileMetadata(path=str(path), exists=False, size_bytes=0)
            try:
                base = str(path.with_suffix(""))
                s = SQLiteSession(base)
                dc = s.dc_id
                addr = s.server_address
                port = s.port
                has_key = bool(s.auth_key)
                s.close()
                return SessionFileMetadata(
                    path=str(path),
                    exists=True,
                    size_bytes=path.stat().st_size,
                    dc_id=dc,
                    server_address=addr,
                    port=port,
                    has_auth_key=has_key
                )
            except Exception as e:
                return SessionFileMetadata(path=str(path), exists=True, size_bytes=path.stat().st_size)

        rust_sessions = []
        for rf in sorted(DATA_DIR.glob("rust_session_*.db")):
            rust_sessions.append({
                "path": str(rf),
                "size_bytes": rf.stat().st_size
            })

        return {
            "primary_telethon": asdict(_inspect_telethon(primary_path)),
            "secondary_telethon": asdict(_inspect_telethon(sec_path)),
            "rust_sessions": rust_sessions
        }

    @staticmethod
    def extract_telethon_string_session(path: Path) -> Optional[str]:
        """Extracts Telethon StringSession from a SQLite .session file without network connection."""
        if not path.exists():
            return None
        base = str(path.with_suffix("")) if str(path).endswith(".session") else str(path)
        s = SQLiteSession(base)
        try:
            if s.auth_key:
                str_s = StringSession()
                str_s.set_dc(s.dc_id, s.server_address, s.port)
                str_s.auth_key = s.auth_key
                return str_s.save()
        finally:
            s.close()
        return None

    async def migrate(
        self,
        owner_user_id: uuid.UUID,
        primary_telegram_user_id: int,
        secondary_telegram_user_id: Optional[int] = None,
        dry_run: bool = False
    ) -> MigrationResult:
        """
        Executes legacy session migration for an explicitly designated user owner.
        Preserves original session files unchanged.
        """
        errors = []
        notes = []
        migrated = []
        skipped = []

        # 1. Verify owner exists
        user_stmt = select(AppUser).where(AppUser.id == owner_user_id)
        u_res = await self.session.execute(user_stmt)
        user = u_res.scalar_one_or_none()
        if not user:
            return MigrationResult(
                success=False,
                dry_run=dry_run,
                owner_user_id=str(owner_user_id),
                migrated_accounts=[],
                skipped_accounts=[],
                errors=[f"Designated owner user {owner_user_id} does not exist in app_users."],
                notes=[]
            )

        primary_path = SESSION_FILE_PATH.with_suffix(".session") if not str(SESSION_FILE_PATH).endswith(".session") else SESSION_FILE_PATH
        sec_path = SESSION_FILE_PATH_2.with_suffix(".session") if not str(SESSION_FILE_PATH_2).endswith(".session") else SESSION_FILE_PATH_2

        # 2. Extract Primary Session
        primary_str = self.extract_telethon_string_session(primary_path)
        if not primary_str:
            errors.append(f"Primary session file {primary_path} is missing or has no active auth key.")
        else:
            notes.append(f"Extracted primary StringSession (length={len(primary_str)}).")

        # 3. Extract Secondary Session
        sec_str = self.extract_telethon_string_session(sec_path)
        if sec_str and secondary_telegram_user_id:
            notes.append(f"Extracted secondary StringSession (length={len(sec_str)}).")

        if dry_run or errors:
            return MigrationResult(
                success=len(errors) == 0,
                dry_run=dry_run,
                owner_user_id=str(owner_user_id),
                migrated_accounts=[{"role": "primary", "telegram_user_id": primary_telegram_user_id}] if primary_str else [],
                skipped_accounts=[],
                errors=errors,
                notes=notes + (["Dry run completed: no database modifications were made."] if dry_run else [])
            )

        # 4. Live Migration: Primary Account
        try:
            # Check if primary account already exists in DB
            account = await self.account_repo.get_by_telegram_user_id(primary_telegram_user_id)
            if account and account.owner_user_id != owner_user_id:
                raise DuplicateTelegramAccountError(
                    f"Telegram user ID {primary_telegram_user_id} is already linked to another application owner."
                )

            if not account:
                account = await self.account_repo.create_account(
                    owner_user_id=owner_user_id,
                    telegram_user_id=primary_telegram_user_id,
                    role="primary",
                    username="Migrated Primary",
                    status="active"
                )

            # Encrypt and save to vault
            assert primary_str is not None
            ver = await self.vault_repo.save_session(
                owner_user_id=owner_user_id,
                account_id=account.id,
                plaintext_session=primary_str
            )

            # Cryptographic round-trip verification
            verified_str = await self.vault_repo.load_session(owner_user_id, account.id)
            if verified_str != primary_str:
                raise RuntimeError("Cryptographic round-trip verification failed on migrated primary session.")

            migrated.append({
                "account_id": str(account.id),
                "telegram_user_id": primary_telegram_user_id,
                "role": "primary",
                "vault_version": ver,
                "status": "verified"
            })
            notes.append("Primary session successfully migrated and verified in vault.")

        except Exception as pe:
            errors.append(f"Failed migrating primary session: {pe}")

        # 5. Live Migration: Secondary Turbo Account
        if sec_str and secondary_telegram_user_id and not errors:
            try:
                sec_account = await self.account_repo.get_by_telegram_user_id(secondary_telegram_user_id)
                if sec_account and sec_account.owner_user_id != owner_user_id:
                    raise DuplicateTelegramAccountError(
                        f"Secondary Telegram user ID {secondary_telegram_user_id} is already linked to another application owner."
                    )

                if not sec_account:
                    sec_account = await self.account_repo.create_account(
                        owner_user_id=owner_user_id,
                        telegram_user_id=secondary_telegram_user_id,
                        role="turbo",
                        username="Migrated Turbo",
                        status="active"
                    )

                sec_ver = await self.vault_repo.save_session(
                    owner_user_id=owner_user_id,
                    account_id=sec_account.id,
                    plaintext_session=sec_str
                )

                sec_verified_str = await self.vault_repo.load_session(owner_user_id, sec_account.id)
                if sec_verified_str != sec_str:
                    raise RuntimeError("Cryptographic round-trip verification failed on migrated secondary session.")

                migrated.append({
                    "account_id": str(sec_account.id),
                    "telegram_user_id": secondary_telegram_user_id,
                    "role": "turbo",
                    "vault_version": sec_ver,
                    "status": "verified"
                })
                notes.append("Secondary Turbo session successfully migrated and verified in vault.")

            except Exception as se:
                errors.append(f"Failed migrating secondary session: {se}")

        success = len(errors) == 0
        if success:
            await self.session.commit()
            notes.append("Migration transaction committed successfully. Original SQLite session files preserved.")
        else:
            await self.session.rollback()
            notes.append("Migration transaction rolled back due to errors.")

        return MigrationResult(
            success=success,
            dry_run=False,
            owner_user_id=str(owner_user_id),
            migrated_accounts=migrated,
            skipped_accounts=skipped,
            errors=errors,
            notes=notes
        )

    async def rollback(self, owner_user_id: uuid.UUID, account_ids: List[uuid.UUID]) -> bool:
        """Controlled rollback deleting migrated vault and account records for safety."""
        try:
            for acc_id in account_ids:
                await self.vault_repo.delete_session(owner_user_id, acc_id)
                await self.account_repo.delete_scoped(acc_id, owner_user_id)
            await self.session.commit()
            logger.info(f"Rollback completed for accounts {account_ids} owned by {owner_user_id}")
            return True
        except Exception as e:
            await self.session.rollback()
            logger.error(f"Rollback failed: {e}")
            return False
