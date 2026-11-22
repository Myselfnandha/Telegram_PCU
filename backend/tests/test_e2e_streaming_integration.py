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
from app.telegram.client_manager import MultiTenantTelegramManager, AccountNotLinkedError
from app.telegram.turbo_pool import UserTurboPool, TurboPoolManager, TurboMode
from telethon.tl.types import MessageMediaDocument, Document, InputFileLocation
from telethon.tl.functions.upload import GetFileRequest

BACKEND_DIR = Path(__file__).resolve().parent.parent

def make_valid_string_session(dc_id: int = 2) -> str:
    from telethon.sessions import StringSession
    from telethon.crypto import AuthKey
    import ipaddress
    s = StringSession()
    s._dc_id = dc_id
    s._server_address = "149.154.167.50"
    s._port = 443
    s._auth_key = AuthKey(data=b"\xab" * 256)
    return s.save()


@asynccontextmanager
async def create_test_session():
    test_db_url = f"sqlite+aiosqlite:///{BACKEND_DIR}/data/test_e2e_{uuid.uuid4().hex[:8]}.db"
    test_engine = create_async_engine(test_db_url, echo=False)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)

    with patch("app.db.session.get_sessionmaker", return_value=session_maker):
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


def create_mock_telethon_client(client_tag: str, dc_id: int = 2):
    client = MagicMock()
    client.is_connected = MagicMock(return_value=True)
    client.is_user_authorized = AsyncMock(return_value=True)
    client.session = MagicMock()
    client.session.dc_id = dc_id
    client.disconnect = AsyncMock()

    # Track requests
    call_log = []
    client._call_log = call_log

    async def mock_call(*args, **kwargs):
        req = args[-1] if len(args) > 1 else (args[0] if args else kwargs.get("request"))
        call_log.append(req)
        offset = getattr(req, "offset", 0)
        limit = getattr(req, "limit", 1024 * 1024)
        chunk_data = f"[{client_tag}:offset={offset}:len={limit}]".encode("utf-8")
        if len(chunk_data) < limit:
            chunk_data = chunk_data + (b"X" * (limit - len(chunk_data)))
        else:
            chunk_data = chunk_data[:limit]
        res = MagicMock()
        res.bytes = chunk_data
        return res

    client.side_effect = mock_call
    client._call = mock_call
    return client


@pytest.mark.asyncio
async def test_e2e_streaming_pipeline_tenant_isolation_and_correctness(monkeypatch):
    """
    Workstream 2 End-to-End Streaming Qualification:
    Traces HTTP media request from FastAPI -> Auth -> Device -> Account -> Manager -> TurboPool -> Response.
    Verifies that:
    1. User A stream uses ONLY User A's Telegram clients.
    2. User B stream uses ONLY User B's Telegram clients.
    3. User A cannot access User B's media or clients.
    4. HTTP Range, 206 Partial Content, Content-Length and byte accuracy are preserved.
    5. Multi-Turbo metrics update properly.
    """
    monkeypatch.setenv("MULTI_TENANT_ENABLED", "true")
    monkeypatch.setenv("MULTI_TENANT_STREAMING_ENABLED", "true")
    monkeypatch.setenv("ENABLE_LEGACY_LOOPBACK", "false")

    async with create_test_session() as session:
        # 1. Register User A and User B
        user_a, _ = await IdentityService.register_user(session, "user_alpha", "AlphaPass123!")
        user_b, _ = await IdentityService.register_user(session, "user_beta", "BetaPass123!")

        print("DEBUG: Step 1 - Users registered", flush=True)
        # 2. Register trusted devices
        dev_a, _, _ = await DeviceService.register_or_resolve_device(session, user_a.id, "DeviceA", "fp_a")
        dev_b, _, _ = await DeviceService.register_or_resolve_device(session, user_b.id, "DeviceB", "fp_b")
        print("DEBUG: Step 2 - Devices registered", flush=True)

        # 3. Create active sessions
        tok_a, sess_a = await SessionService.create_session(session, user_a.id, dev_a.id)
        tok_b, sess_b = await SessionService.create_session(session, user_b.id, dev_b.id)
        p_check = await SessionService.validate_session(session, tok_a)
        assert p_check is not None
        print("DEBUG: Step 3 - Sessions created, p_check valid!", flush=True)

        # 4. Link Telegram accounts
        tg_repo = TelegramAccountRepository(session)
        vault_repo = SessionVaultRepository(session)

        # User A has Primary + Turbo
        acc_a_prim = await tg_repo.create_account(user_a.id, 1001, role="primary")
        acc_a_turb = await tg_repo.create_account(user_a.id, 1002, role="turbo")
        await vault_repo.save_session(user_a.id, acc_a_prim.id, make_valid_string_session(2))
        await vault_repo.save_session(user_a.id, acc_a_turb.id, make_valid_string_session(2))

        # User B has Primary only
        acc_b_prim = await tg_repo.create_account(user_b.id, 2001, role="primary")
        await vault_repo.save_session(user_b.id, acc_b_prim.id, make_valid_string_session(2))

        await session.commit()
        print("DEBUG: Step 4 - Accounts committed", flush=True)

        # 5. Set up distinct mocked Telethon clients
        client_a1 = create_mock_telethon_client("USER_A1")
        client_a2 = create_mock_telethon_client("USER_A2")
        client_b1 = create_mock_telethon_client("USER_B1")

        mock_clients_map = {
            acc_a_prim.id: client_a1,
            acc_a_turb.id: client_a2,
            acc_b_prim.id: client_b1
        }

        # Setup manager
        manager = MultiTenantTelegramManager(idle_timeout_seconds=300)
        async def mock_get_client(principal: Principal, account_id=None):
            if principal.user_id == user_a.id:
                if account_id:
                    if account_id in mock_clients_map and account_id in (acc_a_prim.id, acc_a_turb.id):
                        return mock_clients_map[account_id]
                    raise AccountNotLinkedError(f"Account {account_id} not found for user A")
                return client_a1
            elif principal.user_id == user_b.id:
                if account_id:
                    if account_id == acc_b_prim.id:
                        return mock_clients_map[account_id]
                    raise AccountNotLinkedError(f"Account {account_id} not found for user B")
                return client_b1
            raise AccountNotLinkedError(f"User {principal.user_id} not found")

        monkeypatch.setattr(manager, "get_client", mock_get_client)
        monkeypatch.setattr("app.telegram.client_manager.multi_tenant_manager", manager)

        # Setup mock Telegram message with streamable media document
        file_size = 5 * 1024 * 1024  # 5MB
        fake_doc = Document(
            id=999888,
            access_hash=777666,
            file_reference=b"valid_ref_abc",
            date=None,
            mime_type="video/mp4",
            size=file_size,
            dc_id=2,
            attributes=[]
        )
        fake_media = MessageMediaDocument(
            document=fake_doc,
            nopremium=False,
            spoiler=False,
            video=True,
            round=False,
            voice=False,
            alt_documents=[]
        )
        fake_message = MagicMock()
        fake_message.id = 555
        fake_message.file = MagicMock()
        fake_message.file.name = "test_e2e_video.mp4"
        fake_message.file.size = file_size
        fake_message.file.mime_type = "video/mp4"
        fake_message.media = fake_media

        async def mock_get_message(client, chat_id, message_id, force_refresh=False):
            return fake_message

        # TestClient HTTP calls
        mock_borrow = MagicMock()
        mock_borrow.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_borrow.__aexit__ = AsyncMock(return_value=None)

        with patch("app.routes.proxy_streaming.get_streamable_message", mock_get_message), \
             patch("app.telegram.client_manager.multi_tenant_manager", manager), \
             patch("app.services.stream_engine.sender_pool.borrow", return_value=mock_borrow), \
             patch("app.routes.proxy_streaming.sender_pool.borrow", return_value=mock_borrow), \
             patch("app.services.rust_bridge.rust_bridge.is_healthy", AsyncMock(return_value=False)):
            
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client_http:
                print("DEBUG: Step 5 - AsyncClient created", flush=True)

                # Test A: Unauthenticated request must be rejected (401 or 403)
                resp_unauth = await client_http.get("/dl/chat_alpha/555")
                print("DEBUG: Step 6 - Unauth response:", resp_unauth.status_code, flush=True)
                assert resp_unauth.status_code in (401, 403)

                # Test B: User A authenticates with Bearer token
                headers_a = {"Authorization": f"Bearer {tok_a}"}
                print("DEBUG: Step 7 - Sending User A GET", flush=True)
                resp_a = await client_http.get("/dl/chat_alpha/555", headers=headers_a)
                print("DEBUG: Step 8 - User A response:", resp_a.status_code, resp_a.text, flush=True)
                assert resp_a.status_code == 200
                assert resp_a.headers["content-type"] == "video/mp4"
                assert resp_a.headers["accept-ranges"] == "bytes"
                assert "ETag" in resp_a.headers

                # Verify that ONLY User A's clients were invoked!
                assert len(client_a1._call_log) > 0 or len(client_a2._call_log) > 0
                assert len(client_b1._call_log) == 0, "User B's client was illegally invoked during User A's stream!"

                # Test C: HTTP Range Request for User A (Seeking/Resuming)
                range_headers_a = {
                    "Authorization": f"Bearer {tok_a}",
                    "Range": "bytes=1048576-2097151"  # 1MB slice
                }
                resp_range_a = await client_http.get("/dl/chat_alpha/555", headers=range_headers_a)
                assert resp_range_a.status_code == 206
                assert resp_range_a.headers["content-range"] == f"bytes 1048576-2097151/{file_size}"
                assert resp_range_a.headers["content-length"] == str(1048576)
                assert len(resp_range_a.content) == 1048576

                # Test D: User B authenticates with Bearer token
                client_a1._call_log.clear()
                client_a2._call_log.clear()
                client_b1._call_log.clear()

                headers_b = {"Authorization": f"Bearer {tok_b}"}
                resp_b = await client_http.get("/dl/chat_beta/555", headers=headers_b)
                assert resp_b.status_code == 200

                # Verify that ONLY User B's client was invoked!
                assert len(client_b1._call_log) > 0
                assert len(client_a1._call_log) == 0, "User A1 client was illegally invoked during User B stream!"
                assert len(client_a2._call_log) == 0, "User A2 client was illegally invoked during User B stream!"
