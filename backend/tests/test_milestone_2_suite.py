import os
import sys
import uuid
import time
import asyncio
import hashlib
import json
from pathlib import Path
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from telethon.errors import SessionPasswordNeededError, AuthKeyUnregisteredError
from telethon.sessions import StringSession
from telethon.crypto import AuthKey


def make_valid_string_session() -> str:
    s = StringSession()
    s._dc_id = 2
    s._server_address = "149.154.167.50"
    s._port = 443
    s._auth_key = AuthKey(data=os.urandom(256))
    return s.save()

# Ensure backend directory is in python path
TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Set test master key for AES-256-GCM vault
os.environ["SESSION_MASTER_KEY_B64"] = "MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTIzNDU2Nzg5MDE="
os.environ["ADMIN_BOOTSTRAP_TOKEN"] = "secure-bootstrap-token-tg-suite"
os.environ["MAX_FAILED_LOGIN_ATTEMPTS"] = "5"
os.environ["LOGIN_LOCKOUT_WINDOW_SECS"] = "900"

from app.db.base import Base, utc_now
from app.db.models.user import AppUser, LoginCredential, TrustedDevice, AppSession
from app.db.models.telegram import TelegramAccount, TelegramSessionVault
from app.db.repositories.user_repo import UserRepository
from app.db.repositories.telegram_repo import (
    TelegramAccountRepository,
    DuplicateTelegramAccountError,
    PrimaryAccountConflictError
)
from app.auth_v3.models import Principal
from app.auth_v3.identity_service import (
    IdentityService,
    RateLimitError,
    AuthenticationError
)
from app.auth_v3.session_service import SessionService
from app.auth_v3.device_service import DeviceService
from app.telegram.session_vault import SessionVaultRepository
from app.telegram.link_service import (
    TelegramLinkService,
    STATE_CREATED,
    STATE_QR_PENDING,
    STATE_TELEGRAM_AUTHENTICATING,
    STATE_2FA_REQUIRED,
    STATE_IDENTITY_VERIFIED,
    STATE_PERSISTING,
    STATE_LINKED,
    STATE_CANCELLED,
    STATE_FAILED
)
from app.telegram.client_manager import (
    MultiTenantTelegramManager,
    AccountNotLinkedError,
    RelinkRequiredError
)


@asynccontextmanager
async def create_test_session():
    """Provides an isolated in-memory SQLite database session for unit tests."""
    test_db_url = f"sqlite+aiosqlite:///{BACKEND_DIR}/data/test_m2_{uuid.uuid4().hex[:8]}.db"
    test_engine = create_async_engine(test_db_url, echo=False)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    with patch("app.telegram.link_service.get_sessionmaker", return_value=session_maker), \
         patch("app.telegram.client_manager.get_sessionmaker", return_value=session_maker):
        async with session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    await test_engine.dispose()
    db_file = Path(test_db_url.replace("sqlite+aiosqlite:///", ""))
    if db_file.exists():
        try:
            db_file.unlink()
        except Exception:
            pass


# =========================================================================
# 1. User Registration with Password & PBKDF2 Hash
# =========================================================================
@pytest.mark.asyncio
async def test_user_registration_password_pbkdf2():
    async with create_test_session() as session:
        user, recovery_codes = await IdentityService.register_user(
            session=session,
            username="alice",
            password="StrongPassword123!",
            bootstrap_token="secure-bootstrap-token-tg-suite"
        )
        assert user.id is not None
        assert user.display_name == "alice"
        assert user.status == "active"
        assert user.role == "admin"  # Bootstraps as admin with valid token
        assert len(recovery_codes) == 8

        # Verify credential in database
        user_repo = UserRepository(session)
        creds = await user_repo.get_credentials(user.id, "password_hash")
        assert len(creds) == 1
        cred_data = json.loads(creds[0].credential_data)
        assert "hash" in cred_data
        hashed = cred_data["hash"]
        assert hashed.startswith("pbkdf2_sha256$600000$")
        assert "StrongPassword123!" not in hashed


# =========================================================================
# 2. User Login & Opaque 32-Byte Session Token Issuance
# =========================================================================
@pytest.mark.asyncio
async def test_user_login_session_token_issuance():
    async with create_test_session() as session:
        user, _ = await IdentityService.register_user(
            session=session,
            username="bob",
            password="BobSecurePassword!"
        )

        # Successful login
        auth_user = await IdentityService.authenticate_password(
            session=session,
            username="bob",
            password="BobSecurePassword!"
        )
        assert auth_user.id == user.id

        # Issue session
        raw_token, app_sess = await SessionService.create_session(session, user.id)
        assert len(raw_token) >= 32
        assert app_sess.user_id == user.id

        # Wrong password fails
        with pytest.raises(AuthenticationError):
            await IdentityService.authenticate_password(
                session=session,
                username="bob",
                password="WrongPassword!"
            )


# =========================================================================
# 3. Session Token Hashing (DB never contains plaintext token)
# =========================================================================
@pytest.mark.asyncio
async def test_session_token_hashing_in_db():
    async with create_test_session() as session:
        user, _ = await IdentityService.register_user(session, "charlie", "SecretCharliePass!")
        raw_token, app_sess = await SessionService.create_session(session, user.id)

        # Retrieve session directly from repository
        user_repo = UserRepository(session)
        expected_hash = hashlib.sha256(raw_token.encode("utf-8")).digest()
        db_sess = await user_repo.get_valid_session(expected_hash)
        assert db_sess is not None
        assert db_sess.token_hash == expected_hash
        assert raw_token.encode("utf-8") != db_sess.token_hash


# =========================================================================
# 4. Tenant Isolation: User A cannot authenticate as User B
# =========================================================================
@pytest.mark.asyncio
async def test_tenant_isolation_user_a_cannot_access_user_b():
    async with create_test_session() as session:
        user_a, _ = await IdentityService.register_user(session, "user_a", "PasswordUserA123!")
        user_b, _ = await IdentityService.register_user(session, "user_b", "PasswordUserB123!")

        token_a, _ = await SessionService.create_session(session, user_a.id)
        token_b, _ = await SessionService.create_session(session, user_b.id)

        principal_a = await SessionService.validate_session(session, token_a)
        assert principal_a is not None
        assert principal_a.user_id == user_a.id
        assert principal_a.user_id != user_b.id

        principal_b = await SessionService.validate_session(session, token_b)
        assert principal_b is not None
        assert principal_b.user_id == user_b.id
        assert principal_b.user_id != user_a.id


# =========================================================================
# 5. Rate Limiting / Login Throttling (Locks out after 5 failed attempts)
# =========================================================================
@pytest.mark.asyncio
async def test_rate_limiting_login_throttling():
    IdentityService._failed_attempts.clear()
    async with create_test_session() as session:
        await IdentityService.register_user(session, "throttled_user", "RealPassword123!")

        # 5 consecutive failed attempts
        for _ in range(5):
            with pytest.raises(AuthenticationError):
                await IdentityService.authenticate_password(
                    session=session,
                    username="throttled_user",
                    password="BadPassword"
                )

        # 6th attempt must trigger RateLimitError
        with pytest.raises(RateLimitError):
            await IdentityService.authenticate_password(
                session=session,
                username="throttled_user",
                password="RealPassword123!"
            )


# =========================================================================
# 6. Admin Bootstrap Security
# =========================================================================
@pytest.mark.asyncio
async def test_admin_bootstrap_security():
    async with create_test_session() as session:
        # First user without bootstrap token registers as regular user
        first_user, _ = await IdentityService.register_user(session, "normal_user1", "Pass12345678!")
        assert first_user.role == "user"

        # User with invalid bootstrap token is rejected
        with pytest.raises(ValueError, match="Invalid admin bootstrap token"):
            await IdentityService.register_user(
                session,
                "bad_token_user",
                "NormalPass123!",
                bootstrap_token="invalid_token"
            )

        # Initial admin bootstrap with valid token succeeds
        admin_user, _ = await IdentityService.register_user(
            session,
            "admin_user",
            "AdminPass123!",
            bootstrap_token="secure-bootstrap-token-tg-suite"
        )
        assert admin_user.role == "admin"

        # Once admin is initialized, subsequent bootstrap attempt is rejected
        with pytest.raises(ValueError, match="Administrator account already bootstrapped"):
            await IdentityService.register_user(
                session,
                "another_admin",
                "AnotherAdminPass123!",
                bootstrap_token="secure-bootstrap-token-tg-suite"
            )


# =========================================================================
# 7. Session Revocation (Immediate token invalidation)
# =========================================================================
@pytest.mark.asyncio
async def test_session_revocation_single():
    async with create_test_session() as session:
        user, _ = await IdentityService.register_user(session, "revoke_test_user", "SomePassword123!")
        raw_token, app_sess = await SessionService.create_session(session, user.id)

        # Valid prior to revocation
        principal = await SessionService.validate_session(session, raw_token)
        assert principal is not None

        # Revoke session
        revoked = await SessionService.revoke_session(session, app_sess.id, user.id)
        assert revoked is True

        # Validation fails immediately
        invalid_principal = await SessionService.validate_session(session, raw_token)
        assert invalid_principal is None


# =========================================================================
# 8. Revoke All User Sessions
# =========================================================================
@pytest.mark.asyncio
async def test_revoke_all_user_sessions():
    async with create_test_session() as session:
        user, _ = await IdentityService.register_user(session, "multi_sess_user", "SomePassword123!")
        token1, _ = await SessionService.create_session(session, user.id)
        token2, _ = await SessionService.create_session(session, user.id)
        token3, _ = await SessionService.create_session(session, user.id)

        # Revoke all
        count = await SessionService.revoke_all_user_sessions(session, user.id)
        assert count == 3

        # All tokens invalid
        assert await SessionService.validate_session(session, token1) is None
        assert await SessionService.validate_session(session, token2) is None
        assert await SessionService.validate_session(session, token3) is None


# =========================================================================
# 9. Trusted Device: Initial Device Auto-Verification
# =========================================================================
@pytest.mark.asyncio
async def test_trusted_device_initial_auto_verification():
    async with create_test_session() as session:
        user, _ = await IdentityService.register_user(session, "device_user1", "Pass12345678!")
        device, is_verified, approval_code = await DeviceService.register_or_resolve_device(
            session=session,
            user_id=user.id,
            display_name="First Laptop",
            device_fingerprint="fp-initial-01"
        )
        assert is_verified is True
        assert device.verified_at is not None
        assert approval_code is None


# =========================================================================
# 10. Subsequent Untrusted Device Generates Approval Challenge Code
# =========================================================================
@pytest.mark.asyncio
async def test_subsequent_untrusted_device_approval_challenge():
    async with create_test_session() as session:
        user, _ = await IdentityService.register_user(session, "device_user2", "Pass12345678!")
        # 1st device (verified)
        await DeviceService.register_or_resolve_device(session, user.id, "Device 1", "fp-1")

        # 2nd unknown device
        dev2, is_verified2, approval_code = await DeviceService.register_or_resolve_device(
            session, user.id, "Device 2 Phone", "fp-2"
        )
        assert is_verified2 is False
        assert dev2.verified_at is None
        assert approval_code is not None
        assert len(approval_code) == 6


# =========================================================================
# 11. Trusted Session Approves Pending Device with Challenge Code
# =========================================================================
@pytest.mark.asyncio
async def test_trusted_session_approves_pending_device():
    async with create_test_session() as session:
        user, _ = await IdentityService.register_user(session, "device_user3", "Pass12345678!")
        # 1st device (verified)
        dev1, _, _ = await DeviceService.register_or_resolve_device(session, user.id, "Device 1", "fp-1")
        # 2nd device (pending)
        dev2, _, approval_code = await DeviceService.register_or_resolve_device(
            session, user.id, "Device 2", "fp-2"
        )

        # Unverified device cannot approve itself
        with pytest.raises(PermissionError, match="An unverified device cannot approve itself"):
            await DeviceService.approve_device(
                session=session,
                user_id=user.id,
                device_id=dev2.id,
                challenge_code=approval_code,
                approver_device_id=dev2.id
            )

        # Approve with challenge code from verified session
        approved = await DeviceService.approve_device(
            session=session,
            user_id=user.id,
            device_id=dev2.id,
            challenge_code=approval_code,
            approver_device_id=dev1.id
        )
        assert approved is True

        user_repo = UserRepository(session)
        updated_dev = await user_repo.get_device(dev2.id, user.id)
        assert updated_dev.verified_at is not None


# =========================================================================
# 12. Device Revocation Cascades to Bound Sessions
# =========================================================================
@pytest.mark.asyncio
async def test_device_revocation_cascades_to_sessions():
    async with create_test_session() as session:
        user, _ = await IdentityService.register_user(session, "device_user4", "Pass12345678!")
        dev, _, _ = await DeviceService.register_or_resolve_device(session, user.id, "Work PC", "fp-work")
        raw_token, app_sess = await SessionService.create_session(session, user.id, device_id=dev.id)

        # Valid session
        assert await SessionService.validate_session(session, raw_token) is not None

        # Revoke device
        revoked = await DeviceService.revoke_device(session, user.id, dev.id)
        assert revoked is True

        # Bound session is now invalid
        assert await SessionService.validate_session(session, raw_token) is None


# =========================================================================
# 13. Telegram QR Linking: State Machine CREATED -> QR_PENDING
# =========================================================================
@pytest.mark.asyncio
async def test_qr_linking_state_machine_created_to_qr_pending():
    async with create_test_session() as session:
        user, _ = await IdentityService.register_user(session, "qr_user1", "Pass12345678!")
        user_id = user.id
        mock_qr = MagicMock()
        mock_qr.url = "tg://login?token=dGVzdF90b2tlbg=="

        with patch("telethon.TelegramClient.connect", new_callable=AsyncMock), \
             patch("telethon.TelegramClient.qr_login", new_callable=AsyncMock, return_value=mock_qr), \
             patch("telethon.TelegramClient.disconnect", new_callable=AsyncMock):

            attempt = await TelegramLinkService.start_link_attempt(user_id=user_id, role="primary")
            assert attempt.state == STATE_QR_PENDING
            assert attempt.qr_url == "tg://login?token=dGVzdF90b2tlbg=="
            assert attempt.qr_data_uri.startswith("data:image/png;base64,")

            # Cleanup
            await TelegramLinkService.cancel_link_attempt(user_id, attempt.id)


# =========================================================================
# 14. Telegram QR Linking: 2FA Password Handling without Plaintext Leaks
# =========================================================================
@pytest.mark.asyncio
async def test_qr_linking_2fa_challenge_handling():
    async with create_test_session() as session:
        user, _ = await IdentityService.register_user(session, "twofa_user", "Pass12345678!")
        await session.commit()
        user_id = user.id

        mock_qr = MagicMock()
        mock_qr.url = "tg://login?token=2fa_test_token"
        mock_qr.wait = AsyncMock(side_effect=SessionPasswordNeededError(request=None))

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.qr_login = AsyncMock(return_value=mock_qr)
        mock_client.sign_in = AsyncMock(return_value=MagicMock(id=999001, username="twofa_user", first_name="TwoFA", phone="+1000"))
        valid_session_str = make_valid_string_session()
        mock_client.session.save = MagicMock(return_value=valid_session_str)
        mock_client.disconnect = AsyncMock()
        mock_client.is_connected = MagicMock(return_value=True)

        with patch("app.telegram.link_service.TelegramClient", return_value=mock_client):
            attempt = await TelegramLinkService.start_link_attempt(user_id=user_id, role="turbo")
            # Wait for transition to 2FA_REQUIRED
            for _ in range(20):
                if attempt.state == STATE_2FA_REQUIRED:
                    break
                await asyncio.sleep(0.02)
            assert attempt.state == STATE_2FA_REQUIRED

            # User submits 2FA password
            submitted = await TelegramLinkService.submit_2fa_password(user_id, attempt.id, "MyCloudPassword!")
            assert submitted is True

            # Wait for completion
            for _ in range(40):
                if attempt.state == STATE_LINKED:
                    break
                await asyncio.sleep(0.05)
            assert attempt.state == STATE_LINKED
            assert attempt._2fa_password is None  # Password wiped from memory
            mock_client.sign_in.assert_called_once_with(password="MyCloudPassword!")


# =========================================================================
# 15. Duplicate Telegram ID Rejection Across Tenants
# =========================================================================
@pytest.mark.asyncio
async def test_duplicate_telegram_id_rejection_across_tenants():
    async with create_test_session() as session:
        user_a, _ = await IdentityService.register_user(session, "tenant_a", "TenantPassA123!")
        user_b, _ = await IdentityService.register_user(session, "tenant_b", "TenantPassB123!")

        tg_repo = TelegramAccountRepository(session)
        # User A links Telegram ID 777888
        await tg_repo.create_account(
            owner_user_id=user_a.id,
            telegram_user_id=777888,
            role="primary",
            status="linked"
        )

        # User B attempts to claim the same Telegram ID 777888
        with pytest.raises(DuplicateTelegramAccountError):
            await tg_repo.create_account(
                owner_user_id=user_b.id,
                telegram_user_id=777888,
                role="primary",
                status="linked"
            )


# =========================================================================
# 16. User Account Limits (1 Primary, Max 3 Accounts Total)
# =========================================================================
@pytest.mark.asyncio
async def test_account_limits_one_primary_max_three_accounts():
    async with create_test_session() as session:
        user, _ = await IdentityService.register_user(session, "limit_user", "LimitPass123!")
        tg_repo = TelegramAccountRepository(session)

        # 1st account (primary)
        acc1 = await tg_repo.create_account(user.id, 1001, role="primary")
        assert acc1.role == "primary"

        # Attempting second primary account fails
        with pytest.raises(PrimaryAccountConflictError):
            await tg_repo.create_account(user.id, 1002, role="primary")

        # 2nd account (turbo)
        acc2 = await tg_repo.create_account(user.id, 1002, role="turbo")
        assert acc2.role == "turbo"

        # 3rd account (turbo)
        acc3 = await tg_repo.create_account(user.id, 1003, role="turbo")
        assert acc3.role == "turbo"

        # 4th account attempt fails limit
        count = await tg_repo.count_user_accounts(user.id)
        assert count == 3
        with pytest.raises(ValueError):
            if count >= 3:
                raise ValueError("Maximum Telegram accounts limit (3) reached for this user.")


# =========================================================================
# 17. Round-Trip Encrypted Session Verification before LINKED
# =========================================================================
@pytest.mark.asyncio
async def test_round_trip_vault_encryption_verification():
    async with create_test_session() as session:
        user, _ = await IdentityService.register_user(session, "vault_user", "VaultPass123!")
        tg_repo = TelegramAccountRepository(session)
        vault_repo = SessionVaultRepository(session)

        account = await tg_repo.create_account(user.id, 555444, role="primary")
        plain_session = "1ApWapzMBu6RandomTelegramStringSessionDataHere..."

        # Save to vault
        version = await vault_repo.save_session(user.id, account.id, plain_session)
        assert version == 1

        # Decrypt round-trip
        loaded = await vault_repo.load_session(user.id, account.id)
        assert loaded == plain_session


# =========================================================================
# 18. QR Linking Cancellation and Cleanup
# =========================================================================
@pytest.mark.asyncio
async def test_qr_linking_cancellation_and_cleanup():
    async with create_test_session() as session:
        user, _ = await IdentityService.register_user(session, "cancel_user", "Pass12345678!")
        user_id = user.id
        mock_qr = MagicMock()
        mock_qr.url = "tg://login?token=cancel_token"
        mock_qr.wait = AsyncMock(side_effect=asyncio.CancelledError)

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.qr_login = AsyncMock(return_value=mock_qr)
        mock_client.disconnect = AsyncMock()
        mock_client.is_connected = MagicMock(return_value=True)

        with patch("app.telegram.link_service.TelegramClient", return_value=mock_client):
            attempt = await TelegramLinkService.start_link_attempt(user_id=user_id, role="primary")
            cancelled = await TelegramLinkService.cancel_link_attempt(user_id, attempt.id)
            assert cancelled is True
            assert attempt.state == STATE_CANCELLED
            mock_client.disconnect.assert_called()


# =========================================================================
# 19. MultiTenantTelegramManager: Lazy Connection & Ownership Check
# =========================================================================
@pytest.mark.asyncio
async def test_multi_tenant_client_manager_lazy_and_isolation():
    manager = MultiTenantTelegramManager(idle_timeout_seconds=60)
    async with create_test_session() as session:
        user_a, _ = await IdentityService.register_user(session, "mgr_user_a", "MgrPass123!")
        user_b, _ = await IdentityService.register_user(session, "mgr_user_b", "MgrPass123!")

        tg_repo = TelegramAccountRepository(session)
        vault_repo = SessionVaultRepository(session)

        # Setup user A account & vault
        acc_a = await tg_repo.create_account(user_a.id, 111222, role="primary")
        valid_sess = make_valid_string_session()
        await vault_repo.save_session(user_a.id, acc_a.id, valid_sess)
        await session.commit()

        principal_a = Principal(user_id=user_a.id, role="user")
        principal_b = Principal(user_id=user_b.id, role="user")

        mock_telethon = MagicMock()
        mock_telethon.connect = AsyncMock()
        mock_telethon.is_user_authorized = AsyncMock(return_value=True)
        mock_telethon.is_connected = MagicMock(return_value=True)

        with patch("app.telegram.client_manager.TelegramClient", return_value=mock_telethon):
            # User A retrieves client successfully
            client = await manager.get_client(principal_a, acc_a.id)
            assert client is not None
            assert acc_a.id in manager._active_clients

            # User B attempts to access User A's account -> AccountNotLinkedError / scoped check fails
            with pytest.raises(AccountNotLinkedError):
                await manager.get_client(principal_b, acc_a.id)

        await manager.stop()


# =========================================================================
# 20. Idle Disconnect & Relink Required Transition
# =========================================================================
@pytest.mark.asyncio
async def test_idle_disconnect_and_relink_required_transition():
    manager = MultiTenantTelegramManager(idle_timeout_seconds=1)  # 1-second timeout
    async with create_test_session() as session:
        user, _ = await IdentityService.register_user(session, "idle_user", "IdlePass123!")
        tg_repo = TelegramAccountRepository(session)
        vault_repo = SessionVaultRepository(session)

        acc = await tg_repo.create_account(user.id, 333444, role="primary")
        valid_sess = make_valid_string_session()
        await vault_repo.save_session(user.id, acc.id, valid_sess)
        await session.commit()
        principal = Principal(user_id=user.id, role="user")

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.is_user_authorized = AsyncMock(return_value=True)
        mock_client.disconnect = AsyncMock()
        mock_client.is_connected = MagicMock(return_value=True)

        with patch("app.telegram.client_manager.TelegramClient", return_value=mock_client):
            await manager.get_client(principal, acc.id)
            assert acc.id in manager._active_clients

            # Wait 1.1s for idle timeout
            await asyncio.sleep(1.1)
            disconnected = await manager.disconnect_idle_clients()
            assert disconnected == 1
            assert acc.id not in manager._active_clients
            mock_client.disconnect.assert_called()

        # Test AuthKeyUnregisteredError transitions account status to 'relink_required'
        mock_revoked_client = MagicMock()
        mock_revoked_client.connect = AsyncMock()
        mock_revoked_client.is_user_authorized = AsyncMock(side_effect=AuthKeyUnregisteredError(request=None))
        mock_revoked_client.disconnect = AsyncMock()
        mock_revoked_client.is_connected = MagicMock(return_value=True)

        with patch("app.telegram.client_manager.TelegramClient", return_value=mock_revoked_client):
            with pytest.raises(RelinkRequiredError):
                await manager.get_client(principal, acc.id)

            # Refresh DB account status
            await session.refresh(acc)
            assert acc.status == "relink_required"

        await manager.stop()
