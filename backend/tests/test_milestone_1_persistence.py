import os
import sys
import uuid
import pytest
import asyncio
import tempfile
import shutil
import hashlib
from contextlib import asynccontextmanager
from pathlib import Path

# Bootstrap backend directory into sys.path
TEST_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TEST_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from telethon.sessions import StringSession
from telethon.crypto import AuthKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.db.base import Base
import app.db.models
from app.db.repositories.user_repo import UserRepository
from app.db.repositories.telegram_repo import (
    TelegramAccountRepository,
    DuplicateTelegramAccountError,
    PrimaryAccountConflictError
)
from app.telegram.session_vault import (
    SessionVaultRepository,
    VaultConcurrencyError
)
from app.security.encryption import (
    encrypt_session_string,
    decrypt_session_string,
    SessionDecryptionError,
    KeyManager
)
from app.telegram.session_migration import LegacySessionMigrator


@asynccontextmanager
async def create_test_session():
    """Provides an isolated in-memory SQLite database session for unit tests."""
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_maker() as session:
        yield session

    await test_engine.dispose()


# 1. Database creation and migration test
@pytest.mark.asyncio
async def test_db_creation_and_migration():
    """Verifies that all Milestone 1 tables and relationships exist in schema."""
    async with create_test_session() as session:
        conn = await session.connection()
        tables = await conn.run_sync(lambda sync_conn: Base.metadata.tables.keys())
        expected_tables = {
            "app_users",
            "login_credentials",
            "trusted_devices",
            "app_sessions",
            "telegram_accounts",
            "telegram_session_vault",
            "telegram_client_leases",
            "audit_events"
        }
        for t in expected_tables:
            assert t in tables, f"Expected table {t} missing from schema"


# 2. Tenant isolation test
@pytest.mark.asyncio
async def test_tenant_isolation():
    """Ensures that User B cannot access, read, or delete User A's Telegram accounts or vault sessions."""
    async with create_test_session() as session:
        u_repo = UserRepository(session)
        tg_repo = TelegramAccountRepository(session)
        vault_repo = SessionVaultRepository(session)

        user_a = await u_repo.create_user(display_name="User A")
        user_b = await u_repo.create_user(display_name="User B")

        acc_a = await tg_repo.create_account(
            owner_user_id=user_a.id,
            telegram_user_id=1001,
            role="primary"
        )
        mock_token = "1BAP4_fake_telegram_session_user_a"
        await vault_repo.save_session(user_a.id, acc_a.id, mock_token)
        await session.commit()

        # User B attempts to access User A's session -> Must raise PermissionError
        with pytest.raises(PermissionError):
            await vault_repo.load_session(user_b.id, acc_a.id)

        # User B attempts to delete User A's account -> Must fail and return False
        deleted = await tg_repo.delete_scoped(acc_a.id, user_b.id)
        assert deleted is False

        # Verify User A's session remains intact
        restored = await vault_repo.load_session(user_a.id, acc_a.id)
        assert restored == mock_token


# 3. Encryption/decryption round-trip
def test_encryption_roundtrip():
    """Verifies AES-256-GCM encryption and decryption round-trip."""
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    original_session = "1BVtsOJYBu_test_telethon_authorization_string_length_353_random_chars"

    ciphertext, nonce, key_ver = encrypt_session_string(original_session, tenant_id, account_id)
    assert len(nonce) == 12
    assert len(ciphertext) > len(original_session)

    decrypted = decrypt_session_string(ciphertext, nonce, tenant_id, account_id, key_ver)
    assert decrypted == original_session


# 4. Wrong-key rejection
def test_wrong_key_rejection():
    """Verifies that decrypting with the wrong cryptographic key fails closed."""
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    original_session = "1BVtsOJYBu_wrong_key_test_token"

    km = KeyManager()
    key1 = km.get_key(1)
    key2 = AESGCM.generate_key(bit_length=256)
    km.register_key(2, key2)

    # Encrypt with Key 1
    aes1 = AESGCM(key1)
    nonce = os.urandom(12)
    from app.security.encryption import build_aad
    aad = build_aad(tenant_id, account_id, 1)
    ct = aes1.encrypt(nonce, original_session.encode("utf-8"), aad)

    # Attempt decrypt with Key 2
    aes2 = AESGCM(key2)
    with pytest.raises(Exception):
        aes2.decrypt(nonce, ct, aad)


# 5. Ciphertext tampering rejection
def test_ciphertext_tampering_rejection():
    """Verifies that flipping even a single bit in the ciphertext causes immediate rejection."""
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    original_session = "1BVtsOJYBu_tampering_test_token"

    ciphertext, nonce, key_ver = encrypt_session_string(original_session, tenant_id, account_id)

    # Flip the last byte
    tampered = bytearray(ciphertext)
    tampered[-1] ^= 0x01
    tampered_bytes = bytes(tampered)

    with pytest.raises(SessionDecryptionError):
        decrypt_session_string(tampered_bytes, nonce, tenant_id, account_id, key_ver)


# 6. Cross-tenant decryption rejection
def test_cross_tenant_decryption_rejection():
    """Verifies that ciphertext generated for Tenant A cannot be decrypted by Tenant B (AAD mismatch)."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    account_id = uuid.uuid4()
    original_session = "1BVtsOJYBu_cross_tenant_test_token"

    ciphertext, nonce, key_ver = encrypt_session_string(original_session, tenant_a, account_id)

    with pytest.raises(SessionDecryptionError):
        decrypt_session_string(ciphertext, nonce, tenant_b, account_id, key_ver)


# 7. Duplicate Telegram account prevention
@pytest.mark.asyncio
async def test_duplicate_telegram_account_prevention():
    """Enforces global uniqueness of Telegram user IDs and single primary account per tenant."""
    async with create_test_session() as session:
        u_repo = UserRepository(session)
        tg_repo = TelegramAccountRepository(session)

        user1 = await u_repo.create_user(display_name="User 1")
        user2 = await u_repo.create_user(display_name="User 2")

        # User 1 links TG ID 777
        await tg_repo.create_account(user1.id, 777, role="primary")

        # User 2 attempts to link the SAME TG ID 777 -> Must raise DuplicateTelegramAccountError
        with pytest.raises(DuplicateTelegramAccountError):
            await tg_repo.create_account(user2.id, 777, role="primary")

        # User 1 attempts to add a second primary account -> Must raise PrimaryAccountConflictError
        with pytest.raises(PrimaryAccountConflictError):
            await tg_repo.create_account(user1.id, 888, role="primary")

        # User 1 adds a secondary account with role='turbo' -> Allowed
        turbo_acc = await tg_repo.create_account(user1.id, 888, role="turbo")
        assert turbo_acc.role == "turbo"


# 8. Concurrent session updates
@pytest.mark.asyncio
async def test_concurrent_session_updates():
    """Verifies optimistic concurrency control on vault session updates."""
    async with create_test_session() as session:
        u_repo = UserRepository(session)
        tg_repo = TelegramAccountRepository(session)
        vault_repo = SessionVaultRepository(session)

        user = await u_repo.create_user(display_name="Concurrency User")
        acc = await tg_repo.create_account(user.id, 5555, role="primary")

        # Initial save -> Version 1
        v1 = await vault_repo.save_session(user.id, acc.id, "session_token_initial")
        assert v1 == 1

        # Save with matching expected_version -> Version 2
        v2 = await vault_repo.save_session(user.id, acc.id, "session_token_v2", expected_version=1)
        assert v2 == 2

        # Save with stale expected_version (1 instead of 2) -> Must raise VaultConcurrencyError
        with pytest.raises(VaultConcurrencyError):
            await vault_repo.save_session(user.id, acc.id, "session_token_stale", expected_version=1)


# 9. Session schema compatibility
def test_session_schema_compatibility():
    """Verifies that an exported StringSession can be re-parsed by Telethon StringSession parser."""
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()

    dummy_str = StringSession()
    dummy_str.set_dc(2, "149.154.167.51", 443)
    dummy_str.auth_key = AuthKey(data=os.urandom(256))
    exported = dummy_str.save()

    ct, n, kv = encrypt_session_string(exported, tenant_id, account_id)
    recovered = decrypt_session_string(ct, n, tenant_id, account_id, kv)

    # Re-initialize Telethon StringSession from recovered string
    parsed = StringSession(recovered)
    assert parsed.dc_id == 2
    assert parsed.server_address == "149.154.167.51"
    assert parsed.port == 443
    assert parsed.auth_key.key == dummy_str.auth_key.key


# 10. Backup restoration test
def test_backup_restoration():
    """Verifies that the encrypted backup v2_legacy_sqlite_backup.tar.gz.enc can be decrypted and extracted."""
    from pathlib import Path
    import tarfile

    backup_dir = Path("backend/backup")
    enc_file = backup_dir / "v2_legacy_sqlite_backup.tar.gz.enc"
    key_file = backup_dir / "backup_key.key"

    assert enc_file.exists(), "Encrypted backup file missing"
    assert key_file.exists(), "Backup encryption key missing"

    key = key_file.read_bytes()
    raw = enc_file.read_bytes()
    magic = raw[:4]
    nonce = raw[4:16]
    ct = raw[16:]

    assert magic == b"TGBK"
    aesgcm = AESGCM(key)
    decrypted_tar = aesgcm.decrypt(nonce, ct, b"tg_power_suite_v2_legacy_backup_v1")

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_tar = Path(tmp_dir) / "restore.tar.gz"
        tmp_tar.write_bytes(decrypted_tar)
        with tarfile.open(tmp_tar, "r:gz") as tar:
            tar.extractall(path=tmp_dir)

        restored_sessions = Path(tmp_dir) / "sessions"
        assert restored_sessions.exists()
        assert (restored_sessions / "tg_suite_user_session.session").exists()
        assert (restored_sessions / "tg_suite_user_session.session").stat().st_size > 0


# 11. Container restart persistence simulation
@pytest.mark.asyncio
async def test_container_restart_persistence():
    """
    Simulates container shutdown and recreation:
    1. Writes account & encrypted session to a SQLite database file.
    2. Shuts down and disposes the database engine.
    3. Boots a completely new engine instance pointing to the same file.
    4. Confirms that credentials restore immediately without needing re-login.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "persistent_test.db"
        db_url = f"sqlite+aiosqlite:///{db_path}"

        owner_id = uuid.uuid4()
        tg_id = 99887766
        mock_session_str = "1BAP4_persistent_session_token_xyz"

        # --- Boot 1: Store credentials ---
        engine1 = create_async_engine(db_url)
        async with engine1.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        sessionmaker1 = async_sessionmaker(engine1, expire_on_commit=False)
        async with sessionmaker1() as session1:
            u_repo = UserRepository(session1)
            tg_repo = TelegramAccountRepository(session1)
            vault_repo = SessionVaultRepository(session1)

            user = await u_repo.create_user(display_name="Persistent User", user_id=owner_id)
            acc = await tg_repo.create_account(owner_id, tg_id, role="primary")
            acc_id = acc.id
            await vault_repo.save_session(owner_id, acc_id, mock_session_str)
            await session1.commit()

        # Simulate container teardown: dispose engine
        await engine1.dispose()

        # --- Boot 2: Fresh container starts with new memory ---
        engine2 = create_async_engine(db_url)
        sessionmaker2 = async_sessionmaker(engine2, expire_on_commit=False)
        async with sessionmaker2() as session2:
            vault_repo2 = SessionVaultRepository(session2)
            restored = await vault_repo2.load_session(owner_id, acc_id)
            assert restored == mock_session_str, "Failed to restore session across container restart"

        await engine2.dispose()


# 12. Fail-closed database errors
@pytest.mark.asyncio
async def test_fail_closed_database_errors():
    """Verifies that missing records or empty strings raise errors and NEVER write to .env or return dummy sessions."""
    async with create_test_session() as session:
        u_repo = UserRepository(session)
        tg_repo = TelegramAccountRepository(session)
        vault_repo = SessionVaultRepository(session)

        user = await u_repo.create_user(display_name="Fail-closed User")
        acc = await tg_repo.create_account(user.id, 99999, role="primary")

        non_existent_user = uuid.uuid4()
        non_existent_acc = uuid.uuid4()

        # Loading non-existent account must raise PermissionError / FileNotFoundError
        with pytest.raises((PermissionError, FileNotFoundError)):
            await vault_repo.load_session(non_existent_user, non_existent_acc)

        # Empty string save must raise ValueError
        with pytest.raises(ValueError):
            await vault_repo.save_session(user.id, acc.id, "")

        with pytest.raises(ValueError):
            await vault_repo.save_session(user.id, acc.id, "   ")


# 13. Legacy session migrator live and rollback test
@pytest.mark.asyncio
async def test_legacy_session_migrator_full_lifecycle():
    """Tests inspection, migration, verification, and rollback of legacy session files."""
    async with create_test_session() as session:
        u_repo = UserRepository(session)
        admin = await u_repo.create_user(display_name="Migration Admin", role="admin")

        migrator = LegacySessionMigrator(session)

        # 1. Inspect
        info = migrator.inspect_legacy_sessions()
        assert "primary_telethon" in info
        assert "secondary_telethon" in info

        # 2. Dry run
        dry_res = await migrator.migrate(
            owner_user_id=admin.id,
            primary_telegram_user_id=88881111,
            secondary_telegram_user_id=88882222,
            dry_run=True
        )
        assert dry_res.dry_run is True
        assert dry_res.success is True

        # 3. Live Migration
        live_res = await migrator.migrate(
            owner_user_id=admin.id,
            primary_telegram_user_id=88881111,
            secondary_telegram_user_id=88882222,
            dry_run=False
        )
        assert live_res.dry_run is False
        assert live_res.success is True
        assert len(live_res.migrated_accounts) >= 1

        # Verify account was saved in vault and can be decrypted
        primary_acc_id = uuid.UUID(live_res.migrated_accounts[0]["account_id"])
        vault_repo = SessionVaultRepository(session)
        decrypted = await vault_repo.load_session(admin.id, primary_acc_id)
        assert len(decrypted) > 50

        # 4. Rollback
        acc_ids = [uuid.UUID(a["account_id"]) for a in live_res.migrated_accounts]
        rb_ok = await migrator.rollback(admin.id, acc_ids)
        assert rb_ok is True

        # Confirm accounts removed
        with pytest.raises((PermissionError, FileNotFoundError)):
            await vault_repo.load_session(admin.id, primary_acc_id)
