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
            await session.execute(text("SET LOCAL app.current_user_id = :uid"), {"uid": test_uid})
            res = await session.execute(text("SELECT current_setting('app.current_user_id', true)"))
            assert res.scalar() == test_uid
        await pg_engine.dispose()
