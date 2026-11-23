import os
import logging
import urllib.parse
from typing import AsyncGenerator, Optional
from pathlib import Path
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
    AsyncEngine
)
from sqlalchemy import event, text

from app.config import DATA_DIR, ROOT_DIR

logger = logging.getLogger("db_session")

# Default database URL: External PostgreSQL via DATABASE_URL or SQLite fallback
DEFAULT_SQLITE_PATH = DATA_DIR / "tg_power_suite_v3.db"
DEFAULT_DB_URL = f"sqlite+aiosqlite:///{DEFAULT_SQLITE_PATH}"

def normalize_database_url(url: str) -> str:
    """
    Normalizes database URL for asyncpg and Neon PostgreSQL compatibility.
    - Replaces postgres:// or postgresql:// with postgresql+asyncpg://
    - Translates ?sslmode= to ?ssl= (avoiding asyncpg TypeError: connect() got unexpected keyword 'sslmode')
    - Strips unsupported query parameters (e.g. channel_binding, endpoint) that trigger asyncpg TypeError
    """
    if not url:
        return url
    clean = url.strip()
    if clean.startswith("postgres://"):
        clean = clean.replace("postgres://", "postgresql+asyncpg://", 1)
    elif clean.startswith("postgresql://") and not clean.startswith("postgresql+"):
        clean = clean.replace("postgresql://", "postgresql+asyncpg://", 1)

    if clean.startswith("postgresql+asyncpg://"):
        parsed = urllib.parse.urlsplit(clean)
        query_params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        valid_asyncpg_keys = {
            "ssl",
            "timeout",
            "command_timeout",
            "statement_cache_size",
            "max_cached_statement_lifetime",
            "max_cacheable_statement_size",
            "direct_tls",
            "server_settings",
            "target_session_attrs",
            "krbsrvname",
            "gsslib",
            "prepared_statement_cache_size",
        }
        filtered = []
        has_ssl = any(k == "ssl" for k, _ in query_params)
        for k, v in query_params:
            if k == "sslmode" and not has_ssl:
                filtered.append(("ssl", v))
                has_ssl = True
            elif k in valid_asyncpg_keys:
                filtered.append((k, v))
        new_query = urllib.parse.urlencode(filtered)
        clean = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment)
        )
    return clean


DATABASE_URL = normalize_database_url(os.getenv("DATABASE_URL", DEFAULT_DB_URL))

_engine: Optional[AsyncEngine] = None
_sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        is_sqlite = DATABASE_URL.startswith("sqlite")
        engine_kwargs = {
            "echo": os.getenv("DB_ECHO", "false").lower() in ("1", "true", "yes"),
        }
        if is_sqlite:
            engine_kwargs["connect_args"] = {"check_same_thread": False}
        else:
            engine_kwargs["pool_size"] = int(os.getenv("DB_POOL_SIZE", "10"))
            engine_kwargs["max_overflow"] = int(os.getenv("DB_MAX_OVERFLOW", "20"))
            engine_kwargs["pool_pre_ping"] = True

        _engine = create_async_engine(DATABASE_URL, **engine_kwargs)

        if is_sqlite:
            # Enable SQLite foreign key constraints
            @event.listens_for(_engine.sync_engine, "connect")
            def set_sqlite_pragma(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON;")
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.close()

    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        engine = get_engine()
        _sessionmaker = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False
        )
    return _sessionmaker


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an async database session."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def set_tenant_context(session: AsyncSession, user_id: str) -> None:
    """Sets PostgreSQL transaction-local tenant context for Row-Level Security."""
    engine = get_engine()
    if not str(engine.url).startswith("sqlite"):
        try:
            await session.execute(
                text("SET LOCAL app.current_user_id = :uid"),
                {"uid": str(user_id)}
            )
        except Exception as e:
            logger.debug(f"Could not set tenant context in non-Postgres or unprivileged DB: {e}")


async def init_db() -> None:
    """Initializes tables using DeclarativeBase metadata."""
    from app.db.base import Base
    import app.db.models  # Ensure all models are registered
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schema initialized successfully.")


async def close_db() -> None:
    """Closes database engine connection pool."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None
        logger.info("Database engine disposed.")
