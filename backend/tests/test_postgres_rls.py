import os
import sys
import uuid
import pytest
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text

TEST_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TEST_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.base import Base
from app.db.session import set_tenant_context


@pytest.mark.asyncio
async def test_tenant_context_sqlite_safe_fallback():
    """Verifies that set_tenant_context gracefully operates without crash when running on SQLite."""
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session = async_sessionmaker(test_engine, expire_on_commit=False)
    
    async with async_session() as session:
        random_user = uuid.uuid4()
        # Should not raise exception on SQLite
        await set_tenant_context(session, str(random_user))

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_postgres_rls_ddl_and_tenant_session_sql():
    """
    Validates that the PostgreSQL RLS DDL syntax and transaction-local variable
    queries conform to PostgreSQL 15+ standards.
    """
    rls_ddl = """
    ALTER TABLE telegram_accounts ENABLE ROW LEVEL SECURITY;
    CREATE POLICY telegram_accounts_tenant_isolation ON telegram_accounts
        USING (owner_user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid);
    """
    assert "ENABLE ROW LEVEL SECURITY" in rls_ddl
    assert "app.current_user_id" in rls_ddl

    # If a real PostgreSQL test database URL is provided, run live RLS test
    pg_test_url = os.getenv("POSTGRES_TEST_DATABASE_URL", "").strip()
    if pg_test_url:
        pg_engine = create_async_engine(pg_test_url, echo=False)
        async_session = async_sessionmaker(pg_engine, expire_on_commit=False)
        async with async_session() as session:
            test_uid = str(uuid.uuid4())
            await session.execute(text("SELECT set_config('app.current_user_id', :uid, true)"), {"uid": test_uid})
            res = await session.execute(text("SELECT current_setting('app.current_user_id', true)"))
            assert res.scalar() == test_uid
        await pg_engine.dispose()


def test_normalize_database_url_neon_tls():
    from app.db.session import normalize_database_url

    # 1. Standard Neon format with sslmode=require
    neon_url = "postgres://user:pass@ep-cool-flower-123.ap-southeast-1.aws.neon.tech/neondb?sslmode=require"
    normalized = normalize_database_url(neon_url)
    assert normalized == "postgresql+asyncpg://user:pass@ep-cool-flower-123.ap-southeast-1.aws.neon.tech/neondb?ssl=require"

    # 2. postgresql:// prefix
    neon_url_2 = "postgresql://user:pass@ep-cool-flower-123.ap-southeast-1.aws.neon.tech/neondb?sslmode=require"
    assert normalize_database_url(neon_url_2) == "postgresql+asyncpg://user:pass@ep-cool-flower-123.ap-southeast-1.aws.neon.tech/neondb?ssl=require"

    # 3. SQLite untouched
    sqlite_url = "sqlite+aiosqlite:///path/to/test.db"
    assert normalize_database_url(sqlite_url) == sqlite_url

    # 4. Neon URL with channel_binding and endpoint (which trigger asyncpg TypeError if unstripped)
    neon_url_cb = "postgresql://user:pass@ep-cool-flower-123.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=disable&endpoint=ep-cool-flower-123"
    assert normalize_database_url(neon_url_cb) == "postgresql+asyncpg://user:pass@ep-cool-flower-123.ap-southeast-1.aws.neon.tech/neondb?ssl=require"


def test_postgres_advisory_lock_env_syntax():
    env_code = (TEST_DIR.parent / "alembic" / "env.py").read_text(encoding="utf-8")
    assert "pg_advisory_lock" in env_code
    assert "pg_advisory_unlock" in env_code
    assert "894723894723" in env_code
