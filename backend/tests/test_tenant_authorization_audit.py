import os
import uuid
import asyncio
import pytest
from pathlib import Path
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, AsyncMock, patch
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.main import app
from app.db.base import Base
from app.auth_v3.identity_service import IdentityService
from app.auth_v3.session_service import SessionService, Principal
from app.auth_v3.device_service import DeviceService
from app.db.repositories.telegram_repo import TelegramAccountRepository
from app.telegram.session_vault import SessionVaultRepository
from app.telegram.client_manager import MultiTenantTelegramManager, AccountNotLinkedError, RelinkRequiredError

BACKEND_DIR = Path(__file__).resolve().parent.parent

def make_valid_string_session(dc_id: int = 2) -> str:
    from telethon.sessions import StringSession
    from telethon.crypto import AuthKey
    s = StringSession()
    s._dc_id = dc_id
    s._server_address = "149.154.167.50"
    s._port = 443
    s._auth_key = AuthKey(data=b"\xcc" * 256)
    return s.save()


@asynccontextmanager
async def create_test_session():
    test_db_url = f"sqlite+aiosqlite:///{BACKEND_DIR}/data/test_auth_audit_{uuid.uuid4().hex[:8]}.db"
    test_engine = create_async_engine(test_db_url, echo=False)
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        async with session_factory() as session:
            yield session, session_factory
    finally:
        await test_engine.dispose()
        db_path = Path(test_db_url.replace("sqlite+aiosqlite:///", ""))
        if db_path.exists():
            db_path.unlink()


@pytest.mark.asyncio
async def test_forged_loopback_headers_rejected_in_multitenant_mode(monkeypatch):
    """
    Penetration Test: An attacker behind an external IP sends forged loopback headers
    (X-Forwarded-For: 127.0.0.1, X-Real-IP: 127.0.0.1, CF-Connecting-IP: 127.0.0.1).
    When MULTI_TENANT_STREAMING_ENABLED=True, loopback bypass must NEVER be granted.
    """
    monkeypatch.setenv("MULTI_TENANT_STREAMING_ENABLED", "true")
    monkeypatch.setenv("ENABLE_LEGACY_LOOPBACK", "false")
    monkeypatch.setenv("API_AUTH_TOKEN", "prod_secret_token_xyz")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        # Attack 1: X-Forwarded-For: 127.0.0.1
        res1 = await client.get("/dl/chat_hack/123", headers={"X-Forwarded-For": "127.0.0.1"})
        assert res1.status_code in (401, 403), f"Forged X-Forwarded-For was not rejected: {res1.status_code}"

        # Attack 2: X-Real-IP: 127.0.0.1
        res2 = await client.get("/dl/chat_hack/123", headers={"X-Real-IP": "127.0.0.1"})
        assert res2.status_code in (401, 403), f"Forged X-Real-IP was not rejected: {res2.status_code}"

        # Attack 3: CF-Connecting-IP: 127.0.0.1
        res3 = await client.get("/dl/chat_hack/123", headers={"CF-Connecting-IP": "127.0.0.1"})
        assert res3.status_code in (401, 403), f"Forged CF-Connecting-IP was not rejected: {res3.status_code}"

        # Attack 4: Spoofed local host header + X-Forwarded-For
        res4 = await client.get("/dl/chat_hack/123", headers={
            "Host": "localhost:8088",
            "X-Forwarded-For": "127.0.0.1, 203.0.113.195"
        })
        assert res4.status_code in (401, 403)


@pytest.mark.asyncio
async def test_session_token_validation_and_expiry(monkeypatch):
    """
    Penetration Test:
    - Tampered bearer token must return 401.
    - Revoked session token must return 401.
    """
    monkeypatch.setenv("MULTI_TENANT_STREAMING_ENABLED", "true")
    monkeypatch.setenv("API_AUTH_TOKEN", "prod_secret_token_xyz")

    async with create_test_session() as (session, session_factory):
        monkeypatch.setattr("app.db.session.get_sessionmaker", lambda: session_factory)

        # Create user & device
        user, _ = await IdentityService.register_user(session, "tenant_audit_user", "password123")
        device, _, _ = await DeviceService.register_or_resolve_device(
            session=session,
            user_id=user.id,
            display_name="Audit Device",
            device_fingerprint="fp-audit-01"
        )
        await session.commit()

        # Create valid session
        raw_token, app_sess = await SessionService.create_session(
            session=session,
            user_id=user.id,
            device_id=device.id,
            ttl_hours=1
        )
        await session.commit()

        # 1. Tampered Bearer token
        tampered_token = raw_token[:-6] + "xxxxxx"
        principal_tampered = await SessionService.validate_session(session, tampered_token)
        assert principal_tampered is None, "Tampered token was accepted as valid principal"

        # 2. Revoked session
        await SessionService.revoke_session(session, app_sess.id, user.id)
        await session.commit()

        principal_revoked = await SessionService.validate_session(session, raw_token)
        assert principal_revoked is None, "Revoked session was accepted as valid principal"


@pytest.mark.asyncio
async def test_device_revocation_invalidates_active_sessions(monkeypatch):
    """
    Penetration Test: Revoking a trusted device must immediately invalidate
    all sessions associated with that device.
    """
    async with create_test_session() as (session, session_factory):
        monkeypatch.setattr("app.db.session.get_sessionmaker", lambda: session_factory)

        user, _ = await IdentityService.register_user(session, "device_revocation_user", "password123")
        device, _, _ = await DeviceService.register_or_resolve_device(
            session=session,
            user_id=user.id,
            display_name="Laptop to Revoke",
            device_fingerprint="fp-audit-02"
        )
        await session.commit()

        raw_tok, app_sess = await SessionService.create_session(
            session=session,
            user_id=user.id,
            device_id=device.id,
            ttl_hours=2
        )
        await session.commit()

        # Verify active session resolves
        p = await SessionService.validate_session(session, raw_tok)
        assert p is not None
        assert p.user_id == user.id

        # Now revoke the device
        await DeviceService.revoke_device(session, user.id, device.id)
        await session.commit()

        # Session token for revoked device must now fail validation!
        p_after = await SessionService.validate_session(session, raw_tok)
        assert p_after is None, "Active session survived device revocation!"


@pytest.mark.asyncio
async def test_telegram_account_relink_required_enforcement(monkeypatch):
    """
    Penetration Test: If a tenant's Telegram account is flagged with 'relink_required',
    any attempt to acquire or stream using that account must raise RelinkRequiredError
    and deny media delivery.
    """
    async with create_test_session() as (session, session_factory):
        monkeypatch.setattr("app.db.session.get_sessionmaker", lambda: session_factory)
        monkeypatch.setattr("app.telegram.client_manager.get_sessionmaker", lambda: session_factory)
        user, _ = await IdentityService.register_user(session, "tg_relink_user", "password123")
        device, _, _ = await DeviceService.register_or_resolve_device(
            session=session,
            user_id=user.id,
            display_name="Audit Device",
            device_fingerprint="fp-relink-01"
        )
        tg_repo = TelegramAccountRepository(session)
        vault_repo = SessionVaultRepository(session)
        acc = await tg_repo.create_account(user.id, 999111, role="primary")
        await vault_repo.save_session(user.id, acc.id, make_valid_string_session(2))
        await session.commit()

        principal = Principal(user_id=user.id, role="member", device_id=device.id)
        manager = MultiTenantTelegramManager(idle_timeout_seconds=60)

        # Flag account as relink_required
        await tg_repo.update_status(acc.id, user.id, "relink_required")
        await session.commit()

        with pytest.raises(RelinkRequiredError):
            await manager.get_client(principal, acc.id)
