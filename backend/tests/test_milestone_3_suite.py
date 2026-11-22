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
from telethon.sessions import StringSession
from telethon.crypto import AuthKey
from telethon import errors
from telethon.tl.types import InputFileLocation

# Ensure backend directory is in python path
TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Set test environment
os.environ["SESSION_MASTER_KEY_B64"] = "MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTIzNDU2Nzg5MDE="
os.environ["ADMIN_BOOTSTRAP_TOKEN"] = "secure-bootstrap-token-tg-suite"
os.environ["MULTI_TENANT_STREAMING_ENABLED"] = "true"
os.environ["ADAPTIVE_MULTI_TURBO_ENABLED"] = "true"
os.environ["ENABLE_LEGACY_LOOPBACK"] = "true"

from app.db.base import Base, utc_now
from app.db.models.user import AppUser, LoginCredential, TrustedDevice, AppSession
from app.db.models.telegram import TelegramAccount, TelegramSessionVault
from app.db.repositories.user_repo import UserRepository
from app.db.repositories.telegram_repo import TelegramAccountRepository
from app.auth_v3.models import Principal
from app.auth_v3.identity_service import IdentityService
from app.auth_v3.session_service import SessionService
from app.auth_v3.device_service import DeviceService
from app.telegram.session_vault import SessionVaultRepository
from app.telegram.client_manager import MultiTenantTelegramManager
from app.services.playback_grants import (
    PlaybackGrantService,
    playback_grant_service,
    normalize_chat_id,
    TenantPlaybackGrant
)
from app.services.stream_cache import stream_cache_service, StreamCacheManager
from app.telegram.turbo_pool import (
    UserTurboPool,
    TurboPoolManager,
    turbo_pool_manager,
    AccountWorker,
    TurboMode,
    MAX_ACCOUNTS_PER_USER
)


def make_valid_string_session() -> str:
    s = StringSession()
    s._dc_id = 2
    s._server_address = "149.154.167.50"
    s._port = 443
    s._auth_key = AuthKey(data=os.urandom(256))
    return s.save()


@asynccontextmanager
async def create_test_session():
    """Provides an isolated SQLite database session for Milestone 3 tests."""
    test_db_url = f"sqlite+aiosqlite:///{BACKEND_DIR}/data/test_m3_{uuid.uuid4().hex[:8]}.db"
    test_engine = create_async_engine(test_db_url, echo=False)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    with patch("app.db.session.get_sessionmaker", return_value=session_maker), \
         patch("app.telegram.link_service.get_sessionmaker", return_value=session_maker), \
         patch("app.telegram.client_manager.get_sessionmaker", return_value=session_maker), \
         patch("app.telegram.turbo_pool.get_sessionmaker", return_value=session_maker), \
         patch("app.socket_handlers.get_sessionmaker", return_value=session_maker):
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
# 1. Phase 3A: Admin Bootstrap Security & Remote Impersonation Prevention
# =========================================================================
@pytest.mark.asyncio
async def test_admin_bootstrap_security_hardened():
    async with create_test_session() as session:
        # 1. Unauthenticated registration without token creates standard user, NOT admin
        user1, _ = await IdentityService.register_user(session, "standard_user", "Pass12345678!")
        assert user1.role == "user"

        # 2. Registration with wrong token fails
        with pytest.raises(ValueError, match="Invalid admin bootstrap token"):
            await IdentityService.register_user(
                session, "attacker", "Pass12345678!", bootstrap_token="wrong_token"
            )

        # 3. Registration with matching token succeeds as admin
        admin_user, _ = await IdentityService.register_user(
            session, "true_admin", "AdminPass123!", bootstrap_token="secure-bootstrap-token-tg-suite"
        )
        assert admin_user.role == "admin"

        # 4. Once admin exists, subsequent bootstrap attempts are completely disabled
        with pytest.raises(ValueError, match="already bootstrapped"):
            await IdentityService.register_user(
                session, "second_admin", "AdminPass123!", bootstrap_token="secure-bootstrap-token-tg-suite"
            )


# =========================================================================
# 2. Phase 3A: Device Approval Security (Untrusted Cannot Approve Itself)
# =========================================================================
@pytest.mark.asyncio
async def test_untrusted_device_cannot_approve_itself():
    async with create_test_session() as session:
        user, _ = await IdentityService.register_user(session, "device_owner", "Pass12345678!")
        # 1st verified device
        dev1, is_v1, _ = await DeviceService.register_or_resolve_device(session, user.id, "Verified PC", "fp-trusted")
        assert is_v1 is True

        # 2nd unknown pending device
        dev2, is_v2, code = await DeviceService.register_or_resolve_device(session, user.id, "Pending Phone", "fp-untrusted")
        assert is_v2 is False
        assert dev2.verified_at is None
        assert code is not None

        # 3rd pending device (to test unverified approver)
        dev3, _, _ = await DeviceService.register_or_resolve_device(session, user.id, "Pending Laptop", "fp-pending-2")

        # Untrusted device attempting to approve itself is rejected
        with pytest.raises(PermissionError, match="An unverified device cannot approve itself"):
            await DeviceService.approve_device(
                session=session,
                user_id=user.id,
                device_id=dev2.id,
                challenge_code=code,
                approver_device_id=dev2.id
            )

        # Non-verified approver device is rejected
        with pytest.raises(PermissionError, match="Device approval requires an already-verified trusted device session"):
            await DeviceService.approve_device(
                session=session,
                user_id=user.id,
                device_id=dev2.id,
                challenge_code=code,
                approver_device_id=dev3.id
            )

        # Verified approver session approves successfully
        approved = await DeviceService.approve_device(
            session=session,
            user_id=user.id,
            device_id=dev2.id,
            challenge_code=code,
            approver_device_id=dev1.id
        )
        assert approved is True

        user_repo = UserRepository(session)
        updated = await user_repo.get_device(dev2.id, user.id)
        assert updated.verified_at is not None


# =========================================================================
# 3. Phase 3B: Cryptographic Playback Grant Issuance & Signature Verification
# =========================================================================
def test_playback_grant_issuance_and_signing():
    service = PlaybackGrantService(default_ttl_seconds=3600)
    user_id = uuid.uuid4()
    chat_id = "-1001234567890"
    message_id = 42

    token = service.issue_grant(
        user_id=user_id,
        chat_id=chat_id,
        message_id=message_id,
        operation="stream"
    )
    assert token is not None
    assert "." in token

    # Verification with exact media parameters succeeds
    grant = service.verify_grant(token, chat_id, message_id, operation="stream")
    assert grant is not None
    assert grant.user_id == user_id
    assert grant.chat_id == "1234567890"  # Normalized
    assert grant.message_id == 42
    assert grant.operation == "stream"
    assert grant.expires_at > time.time()


# =========================================================================
# 4. Phase 3B: Playback Grant Tampering & Media Mismatch Rejection
# =========================================================================
def test_playback_grant_tampering_and_mismatch_rejection():
    service = PlaybackGrantService(default_ttl_seconds=3600)
    user_id = uuid.uuid4()
    token = service.issue_grant(user_id=user_id, chat_id="-1009876543210", message_id=50)

    # 1. Tampered token signature fails
    tampered = token[:-4] + "ffff"
    assert service.verify_grant(tampered, "-1009876543210", 50) is None

    # 2. Reusing grant token on a DIFFERENT message fails
    assert service.verify_grant(token, "-1009876543210", 51) is None

    # 3. Reusing grant token on a DIFFERENT chat fails
    assert service.verify_grant(token, "-1001111111111", 50) is None

    # 4. Reusing stream grant for download when operation is restricted fails
    assert service.verify_grant(token, "-1009876543210", 50, operation="download") is None


# =========================================================================
# 5. Phase 3B: Playback Grant Revocation Lifecycle
# =========================================================================
def test_playback_grant_revocation_lifecycle():
    service = PlaybackGrantService(default_ttl_seconds=3600)
    user_id = uuid.uuid4()
    token1 = service.issue_grant(user_id=user_id, chat_id="111", message_id=1)
    token2 = service.issue_grant(user_id=user_id, chat_id="222", message_id=2)

    g1 = service.verify_grant(token1, "111", 1)
    assert g1 is not None

    # Explicit revocation of grant 1
    revoked = service.revoke_grant(user_id, g1.grant_id)
    assert revoked is True

    # Immediate rejection of revoked grant
    assert service.verify_grant(token1, "111", 1) is None

    # Grant 2 remains active
    assert service.verify_grant(token2, "222", 2) is not None

    # Bulk revocation of all user grants
    count = service.revoke_all_user_grants(user_id)
    assert count >= 1
    assert service.verify_grant(token2, "222", 2) is None


# =========================================================================
# 6. Phase 3F: Stream Cache Tenant Isolation
# =========================================================================
def test_stream_cache_tenant_isolation(tmp_path, monkeypatch):
    cache_dir = tmp_path / "tg_cache"
    blocks_dir = cache_dir / "blocks"
    cache_dir.mkdir(parents=True, exist_ok=True)
    blocks_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("app.services.stream_cache.CACHE_DIR", cache_dir)
    monkeypatch.setattr("app.services.stream_cache.BLOCKS_DIR", blocks_dir)

    manager = StreamCacheManager()
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    chat_id = "channel_99"
    message_id = 777

    # User A caches block 0
    sample_data = b"TENANT_A_EXCLUSIVE_STREAMING_DATA"
    manager.save_block(chat_id, message_id, 0, sample_data, user_id=user_a)

    # User A can read their block
    read_a = manager.read_block_slice(chat_id, message_id, 0, 0, len(sample_data), user_id=user_a)
    assert read_a == sample_data

    # User B querying for the SAME (chat_id, message_id) CANNOT access User A's cache
    read_b = manager.read_block_slice(chat_id, message_id, 0, 0, len(sample_data), user_id=user_b)
    assert read_b is None

    # Unauthenticated/legacy query also cannot access User A's isolated cache
    read_unauth = manager.read_block_slice(chat_id, message_id, 0, 0, len(sample_data), user_id=None)
    assert read_unauth is None


# =========================================================================
# 7. Phase 3C: Per-User Turbo Pool Ceiling (Max 3 Accounts Strict Enforcement)
# =========================================================================
@pytest.mark.asyncio
async def test_turbo_pool_max_3_accounts_enforcement():
    async with create_test_session() as session:
        user, _ = await IdentityService.register_user(session, "turbo_user", "Pass12345678!")
        tg_repo = TelegramAccountRepository(session)
        vault_repo = SessionVaultRepository(session)

        # Add 1 primary account
        acc1 = await tg_repo.create_account(user.id, 1001, "primary", "user1")
        await vault_repo.save_session(user.id, acc1.id, make_valid_string_session())

        # Add 2 secondary turbo accounts
        acc2 = await tg_repo.create_account(user.id, 1002, "turbo", "user2")
        await vault_repo.save_session(user.id, acc2.id, make_valid_string_session())

        acc3 = await tg_repo.create_account(user.id, 1003, "turbo", "user3")
        await vault_repo.save_session(user.id, acc3.id, make_valid_string_session())
        await session.commit()

        # Sync pool
        pool = await turbo_pool_manager.get_pool(user.id)
        with patch.object(MultiTenantTelegramManager, "get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.is_connected = MagicMock(return_value=True)
            mock_client.is_user_authorized = AsyncMock(return_value=True)
            mock_get_client.return_value = mock_client

            count = await pool.sync_accounts()
            assert count == 3
            assert len(pool.workers) <= MAX_ACCOUNTS_PER_USER


# =========================================================================
# 8. Phase 3D: Media Eligibility & File Reference Binding
# =========================================================================
@pytest.mark.asyncio
async def test_turbo_pool_media_eligibility_and_file_reference_binding():
    user_id = uuid.uuid4()
    pool = UserTurboPool(user_id)

    # Setup worker 1 (eligible with its own valid media message)
    client1 = MagicMock()
    msg1 = MagicMock()
    msg1.media = MagicMock()
    file_loc1 = InputFileLocation(volume_id=1, local_id=1, secret=111, file_reference=b"ref_worker_1")
    client1.get_messages = AsyncMock(return_value=msg1)

    # Setup worker 2 (ineligible - channel private / lacks permission)
    client2 = MagicMock()
    client2.get_messages = AsyncMock(side_effect=errors.ChannelPrivateError(request=None))

    worker1 = AccountWorker(
        account_id=uuid.uuid4(),
        telegram_user_id=101,
        role="primary",
        client=client1
    )
    worker2 = AccountWorker(
        account_id=uuid.uuid4(),
        telegram_user_id=102,
        role="secondary",
        client=client2
    )

    pool.workers[worker1.account_id] = worker1
    pool.workers[worker2.account_id] = worker2

    with patch("app.telegram.turbo_pool.utils.get_input_location", return_value=file_loc1), \
         patch("app.telegram.turbo_pool.utils._get_file_info", return_value=MagicMock(dc_id=2)), \
         patch.object(pool, "sync_accounts", return_value=2):
        eligible = await pool.check_media_eligibility("secret_channel", 999)

        assert len(eligible) == 1
        assert eligible[0].account_id == worker1.account_id
        assert eligible[0].file_location == file_loc1
        assert worker2.is_eligible is False


# =========================================================================
# 9. Phase 3D: FloodWait Isolation & Worker Cooldown
# =========================================================================
@pytest.mark.asyncio
async def test_turbo_pool_floodwait_isolation():
    worker = AccountWorker(
        account_id=uuid.uuid4(),
        telegram_user_id=201,
        role="secondary",
        client=MagicMock()
    )
    assert worker.is_cooling_down is False

    # Simulate FloodWait error of 15 seconds
    fw_err = errors.FloodWaitError(request=None)
    fw_err.seconds = 15
    worker.record_error(fw_err)

    assert worker.is_cooling_down is True
    assert worker.floodwait_until > time.time()
    assert worker.consecutive_errors == 1


# =========================================================================
# 10. Phase 3E: Adaptive Multi-Turbo Scheduler Modes & Deterministic Assembly
# =========================================================================
@pytest.mark.asyncio
async def test_turbo_pool_modes_and_deterministic_chunk_assembly():
    user_id = uuid.uuid4()
    pool = UserTurboPool(user_id)

    client1 = MagicMock()
    client2 = MagicMock()

    file_loc = InputFileLocation(volume_id=10, local_id=20, secret=30, file_reference=b"ref")

    worker1 = AccountWorker(account_id=uuid.uuid4(), telegram_user_id=301, role="primary", client=client1, file_location=file_loc)
    worker2 = AccountWorker(account_id=uuid.uuid4(), telegram_user_id=302, role="secondary", client=client2, file_location=file_loc)

    pool.workers[worker1.account_id] = worker1
    pool.workers[worker2.account_id] = worker2

    # Mode 1: OFF -> Selects primary worker only
    pool.set_mode(TurboMode.OFF)
    selected_off = pool.select_best_worker([worker1, worker2])
    assert selected_off.role == "primary"

    # Mode 2: BALANCED -> Selects worker with lowest active in-flight requests
    pool.set_mode(TurboMode.BALANCED)
    worker1.active_in_flight = 2
    worker2.active_in_flight = 0
    selected_balanced = pool.select_best_worker([worker1, worker2])
    assert selected_balanced.account_id == worker2.account_id

    # Mode 3: ADAPTIVE -> Weights EWMA latency and in-flight count
    pool.set_mode(TurboMode.ADAPTIVE)
    worker1.active_in_flight = 0
    worker1.ewma_latency_ms = 40.0  # Fast worker
    worker2.active_in_flight = 0
    worker2.ewma_latency_ms = 350.0  # Slow worker
    selected_adaptive = pool.select_best_worker([worker1, worker2])
    assert selected_adaptive.account_id == worker1.account_id

    # Deterministic chunk stream assembly
    chunks_delivered = []
    chunk_map = {
        0: b"CHUNK_PART_00000_",
        1: b"CHUNK_PART_00001_",
        2: b"CHUNK_PART_00002_",
        3: b"CHUNK_PART_00003_"
    }

    async def mock_fetch_chunk(w, offset, limit, retries=2):
        idx = offset // (17)  # length of each chunk
        await asyncio.sleep(0.01)
        return chunk_map.get(idx, b"")

    with patch.object(pool, "check_media_eligibility", return_value=[worker1, worker2]), \
         patch.object(pool, "fetch_chunk", side_effect=mock_fetch_chunk):

        async for chunk in pool.stream_byte_range(
            chat_id="chan",
            message_id=1,
            start_byte=0,
            end_byte=67,  # 4 * 17 - 1
            chunk_size=17
        ):
            chunks_delivered.append(chunk)

        assembled = b"".join(chunks_delivered)
        assert assembled == b"CHUNK_PART_00000_CHUNK_PART_00001_CHUNK_PART_00002_CHUNK_PART_00003_"


# =========================================================================
# 11. Phase 3B: Streaming Gateway Authorization Gate
# =========================================================================
@pytest.mark.asyncio
async def test_streaming_gateway_authorization_gate():
    from app.auth import verify_auth
    from fastapi import Request

    # 1. Remote request without auth raises 401
    mock_remote_req = MagicMock(spec=Request)
    mock_remote_req.headers = {"host": "streaming.example.com"}
    mock_remote_req.client = MagicMock(host="198.51.100.42")
    mock_remote_req.cookies = {}
    mock_remote_req.query_params = {}
    mock_remote_req.scope = {"method": "GET", "path": "/stream/12345/100"}
    mock_remote_req.state = MagicMock()

    with patch("app.auth.is_loopback_request", return_value=False):
        with pytest.raises(Exception) as exc_info:
            await verify_auth(mock_remote_req)
        assert "401" in str(exc_info.value) or "403" in str(exc_info.value)

    # 2. Remote request with valid Playback Grant succeeds
    user_id = uuid.uuid4()
    grant_token = playback_grant_service.issue_grant(user_id, "12345", 100)
    mock_granted_req = MagicMock(spec=Request)
    mock_granted_req.headers = {"host": "streaming.example.com"}
    mock_granted_req.client = MagicMock(host="198.51.100.42")
    mock_granted_req.cookies = {}
    mock_granted_req.query_params = {"grant": grant_token}
    mock_granted_req.scope = {"method": "GET", "path": "/stream/12345/100"}
    mock_granted_req.state = MagicMock()

    with patch("app.auth.is_loopback_request", return_value=False):
        # Should not raise exception
        await verify_auth(mock_granted_req)
        assert getattr(mock_granted_req.state, "grant", None) is not None
        assert mock_granted_req.state.grant.chat_id == "12345"


# =========================================================================
# 12. Phase 3A: WebSocket Handshake & Multi-Tenant Room Isolation
# =========================================================================
@pytest.mark.asyncio
async def test_websocket_tenant_room_isolation():
    from app.socket_handlers import connect, sio, emit_to_user

    user_id = uuid.uuid4()
    sid = "test_socket_session_123"
    environ = {
        "REMOTE_ADDR": "198.51.100.55",
        "HTTP_COOKIE": f"tg_session=fake_v3_session_token_xyz"
    }

    mock_principal = Principal(
        user_id=user_id,
        session_id=uuid.uuid4(),
        device_id=uuid.uuid4(),
        role="user",
        display_name="SocketUser"
    )

    with patch("app.socket_handlers.resolve_socket_principal", return_value=mock_principal), \
         patch.object(sio, "enter_room", new_callable=AsyncMock) as mock_enter_room, \
         patch.object(sio, "save_session", new_callable=AsyncMock) as mock_save_session, \
         patch.object(sio, "emit", new_callable=AsyncMock) as mock_emit:

        await connect(sid, environ, auth=None)

        # Verified joining user's tenant-isolated room
        mock_enter_room.assert_any_call(sid, f"user_{user_id}")
        mock_enter_room.assert_any_call(sid, f"device_{mock_principal.device_id}")

        # Emitting to tenant delivers strictly to user_{user_id}
        await emit_to_user(user_id, "turbo:metric", {"speed": "25MB/s"})
        mock_emit.assert_any_call("turbo:metric", {"speed": "25MB/s"}, room=f"user_{user_id}")
