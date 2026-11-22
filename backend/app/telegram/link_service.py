import os
import io
import time
import uuid
import base64
import logging
import asyncio
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass
import qrcode
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

from app.config import API_ID, API_HASH
from app.db.session import get_sessionmaker
from app.db.repositories.telegram_repo import (
    TelegramAccountRepository,
    DuplicateTelegramAccountError,
    PrimaryAccountConflictError
)
from app.db.repositories.audit_repo import AuditRepository
from app.telegram.session_vault import SessionVaultRepository

logger = logging.getLogger("telegram_link_service")

# State Machine States
STATE_CREATED = "CREATED"
STATE_QR_PENDING = "QR_PENDING"
STATE_TELEGRAM_AUTHENTICATING = "TELEGRAM_AUTHENTICATING"
STATE_2FA_REQUIRED = "2FA_REQUIRED"
STATE_IDENTITY_VERIFIED = "IDENTITY_VERIFIED"
STATE_PERSISTING = "PERSISTING"
STATE_LINKED = "LINKED"
STATE_EXPIRED = "EXPIRED"
STATE_CANCELLED = "CANCELLED"
STATE_FAILED = "FAILED"
STATE_RELINK_REQUIRED = "RELINK_REQUIRED"

MAX_ACCOUNTS_PER_USER = 3
DEFAULT_LINK_TIMEOUT = 180  # 3 minutes


@dataclass
class LinkAttempt:
    id: uuid.UUID
    user_id: uuid.UUID
    role: str  # "primary" or "turbo"
    state: str
    created_at: float
    expires_at: float
    qr_url: Optional[str] = None
    qr_data_uri: Optional[str] = None
    error_message: Optional[str] = None
    telegram_user_id: Optional[int] = None
    username: Optional[str] = None
    first_name: Optional[str] = None
    account_id: Optional[uuid.UUID] = None
    # Internal lifecycle controls
    _client: Optional[TelegramClient] = None
    _task: Optional[asyncio.Task] = None
    _2fa_event: Optional[asyncio.Event] = None
    _2fa_password: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "role": self.role,
            "state": self.state,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "expires_in": max(0, int(self.expires_at - time.time())),
            "qr_url": self.qr_url,
            "qr_data_uri": self.qr_data_uri,
            "error_message": self.error_message,
            "telegram_user_id": self.telegram_user_id,
            "username": self.username,
            "first_name": self.first_name,
            "account_id": str(self.account_id) if self.account_id else None
        }


class TelegramLinkService:
    # Memory store for active link attempts: {attempt_id: LinkAttempt}
    _attempts: Dict[uuid.UUID, LinkAttempt] = {}

    @staticmethod
    def generate_qr_data_uri(url: str) -> str:
        qr = qrcode.QRCode(box_size=6, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    @classmethod
    async def start_link_attempt(
        cls,
        user_id: uuid.UUID,
        role: str = "primary",
        timeout_seconds: int = DEFAULT_LINK_TIMEOUT
    ) -> LinkAttempt:
        if role not in ("primary", "turbo"):
            raise ValueError(f"Invalid account role: {role}. Must be 'primary' or 'turbo'.")

        session_maker = get_sessionmaker()
        async with session_maker() as db_session:
            tg_repo = TelegramAccountRepository(db_session)

            # Check max account limit per user (3 accounts max)
            count = await tg_repo.count_user_accounts(user_id)
            if count >= MAX_ACCOUNTS_PER_USER:
                raise ValueError(f"Maximum Telegram accounts limit ({MAX_ACCOUNTS_PER_USER}) reached for this user.")

            # If requesting primary, check if user already has an active primary account
            if role == "primary":
                existing_primary = await tg_repo.get_primary_account(user_id)
                if existing_primary and existing_primary.status in ("linked", "active"):
                    raise PrimaryAccountConflictError("User already has an active primary Telegram account.")

        attempt_id = uuid.uuid4()
        now = time.time()
        attempt = LinkAttempt(
            id=attempt_id,
            user_id=user_id,
            role=role,
            state=STATE_CREATED,
            created_at=now,
            expires_at=now + timeout_seconds,
            _2fa_event=asyncio.Event()
        )
        cls._attempts[attempt_id] = attempt

        # Start temporary isolated Telethon client using StringSession (no SQLite files on disk)
        temp_client = TelegramClient(
            StringSession(),
            API_ID,
            API_HASH,
            device_model="TG Power Suite v3",
            system_version="Linux Multi-Tenant",
            app_version="3.0.0"
        )
        attempt._client = temp_client

        try:
            await temp_client.connect()
            qr_login = await temp_client.qr_login()
            attempt.qr_url = qr_login.url
            attempt.qr_data_uri = cls.generate_qr_data_uri(qr_login.url)
            attempt.state = STATE_QR_PENDING
        except Exception as e:
            logger.error(f"Failed to initialize QR login: {e}")
            attempt.state = STATE_FAILED
            attempt.error_message = f"Failed to initialize Telegram QR login: {str(e)}"
            await cls._cleanup_client(attempt)
            return attempt

        # Launch polling loop in background
        task = asyncio.create_task(cls._run_qr_lifecycle(attempt, qr_login, timeout_seconds))
        attempt._task = task

        return attempt

    @classmethod
    async def _run_qr_lifecycle(cls, attempt: LinkAttempt, qr_login: Any, timeout_seconds: int) -> None:
        try:
            # Wait for QR code scan and authorization
            logged_in_user = None
            try:
                logged_in_user = await qr_login.wait(timeout=timeout_seconds)
                attempt.state = STATE_TELEGRAM_AUTHENTICATING
            except SessionPasswordNeededError:
                # 2FA password required
                attempt.state = STATE_2FA_REQUIRED
                logger.info(f"LinkAttempt {attempt.id}: 2FA password required. Waiting for user input.")

                # Wait for user to submit 2FA password via API
                remaining = max(1, int(attempt.expires_at - time.time()))
                try:
                    await asyncio.wait_for(attempt._2fa_event.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    attempt.state = STATE_EXPIRED
                    attempt.error_message = "Timed out waiting for 2FA password."
                    return

                # Submit password to Telethon without logging or storing plaintext
                password = attempt._2fa_password
                attempt._2fa_password = None  # Zero immediately

                if not password:
                    attempt.state = STATE_FAILED
                    attempt.error_message = "No 2FA password provided."
                    return

                try:
                    logged_in_user = await attempt._client.sign_in(password=password)
                except Exception as e:
                    attempt.state = STATE_FAILED
                    attempt.error_message = f"2FA verification failed: {str(e)}"
                    return

            if not logged_in_user:
                # Fetch user details
                logged_in_user = await attempt._client.get_me()

            if not logged_in_user:
                attempt.state = STATE_FAILED
                attempt.error_message = "Could not retrieve Telegram user profile."
                return

            tg_user_id = logged_in_user.id
            username = getattr(logged_in_user, "username", None)
            first_name = getattr(logged_in_user, "first_name", None)
            phone = getattr(logged_in_user, "phone", None)

            attempt.telegram_user_id = tg_user_id
            attempt.username = username
            attempt.first_name = first_name
            attempt.state = STATE_IDENTITY_VERIFIED

            # Check uniqueness and persist
            session_maker = get_sessionmaker()
            async with session_maker() as db_session:
                tg_repo = TelegramAccountRepository(db_session)
                audit_repo = AuditRepository(db_session)
                vault_repo = SessionVaultRepository(db_session)

                # Check global uniqueness of Telegram user ID
                existing = await tg_repo.get_by_telegram_user_id(tg_user_id)
                if existing and existing.owner_user_id != attempt.user_id:
                    attempt.state = STATE_FAILED
                    attempt.error_message = (
                        f"Telegram account ({tg_user_id}) is already linked to another user."
                    )
                    await audit_repo.log_event(
                        event_type="telegram.link_failed",
                        user_id=attempt.user_id,
                        metadata={"telegram_user_id": tg_user_id, "reason": "claimed_by_other_tenant"}
                    )
                    return

                attempt.state = STATE_PERSISTING

                # Save or update TelegramAccount record
                if existing and existing.owner_user_id == attempt.user_id:
                    account = existing
                    account.status = "linked"
                    account.role = attempt.role
                    account.username = username
                    account.first_name = first_name
                    account.phone_number = phone
                    await db_session.flush()
                else:
                    account = await tg_repo.create_account(
                        owner_user_id=attempt.user_id,
                        telegram_user_id=tg_user_id,
                        role=attempt.role,
                        username=username,
                        first_name=first_name,
                        phone_number=phone,
                        status="linked"
                    )

                attempt.account_id = account.id

                # Export StringSession
                plaintext_session = attempt._client.session.save()
                if not plaintext_session:
                    raise ValueError("Telethon returned empty session string.")

                # Encrypt and save in vault
                await vault_repo.save_session(
                    owner_user_id=attempt.user_id,
                    account_id=account.id,
                    plaintext_session=plaintext_session
                )

                # Strict round-trip verification: decrypt and verify match before marking LINKED
                decrypted_check = await vault_repo.load_session(
                    owner_user_id=attempt.user_id,
                    account_id=account.id
                )
                if decrypted_check != plaintext_session:
                    raise ValueError("Round-trip vault encryption check failed: decrypted session does not match original.")

                await db_session.commit()

                await audit_repo.log_event(
                    event_type="telegram.linked",
                    user_id=attempt.user_id,
                    telegram_account_id=account.id,
                    metadata={"telegram_user_id": tg_user_id, "role": attempt.role}
                )

            attempt.state = STATE_LINKED
            logger.info(f"LinkAttempt {attempt.id}: Successfully linked Telegram account {tg_user_id} to user {attempt.user_id}.")

        except asyncio.CancelledError:
            attempt.state = STATE_CANCELLED
            attempt.error_message = "Linking attempt was cancelled by user."
            logger.info(f"LinkAttempt {attempt.id} cancelled.")
        except asyncio.TimeoutError:
            attempt.state = STATE_EXPIRED
            attempt.error_message = "Telegram QR login timed out. Please scan before expiry."
            logger.info(f"LinkAttempt {attempt.id} expired.")
        except Exception as e:
            logger.error(f"LinkAttempt {attempt.id} failed with error: {e}", exc_info=True)
            attempt.state = STATE_FAILED
            attempt.error_message = str(e)
        finally:
            await cls._cleanup_client(attempt)

    @classmethod
    async def submit_2fa_password(cls, user_id: uuid.UUID, attempt_id: uuid.UUID, password: str) -> bool:
        attempt = cls._attempts.get(attempt_id)
        if not attempt or attempt.user_id != user_id:
            return False
        if attempt.state != STATE_2FA_REQUIRED or not attempt._2fa_event:
            return False

        attempt._2fa_password = password
        attempt._2fa_event.set()
        return True

    @classmethod
    async def cancel_link_attempt(cls, user_id: uuid.UUID, attempt_id: uuid.UUID) -> bool:
        attempt = cls._attempts.get(attempt_id)
        if not attempt or attempt.user_id != user_id:
            return False

        if attempt._task and not attempt._task.done():
            attempt._task.cancel()

        attempt.state = STATE_CANCELLED
        await cls._cleanup_client(attempt)
        return True

    @classmethod
    def get_link_attempt(cls, user_id: uuid.UUID, attempt_id: uuid.UUID) -> Optional[LinkAttempt]:
        attempt = cls._attempts.get(attempt_id)
        if not attempt or attempt.user_id != user_id:
            return None
        return attempt

    @classmethod
    async def _cleanup_client(cls, attempt: LinkAttempt) -> None:
        if attempt._client:
            try:
                if attempt._client.is_connected():
                    await attempt._client.disconnect()
            except Exception as e:
                logger.debug(f"Error disconnecting temporary client: {e}")
            attempt._client = None

    @classmethod
    def cleanup_old_attempts(cls, max_age_seconds: int = 3600) -> None:
        now = time.time()
        for aid in list(cls._attempts.keys()):
            att = cls._attempts[aid]
            if now - att.created_at > max_age_seconds:
                cls._attempts.pop(aid, None)
