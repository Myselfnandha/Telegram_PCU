import asyncio
import os
import sys
from pathlib import Path
from logging.config import fileConfig

from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Ensure backend directory is in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.db.base import Base
import app.db.models  # Register all models
from app.db.session import DATABASE_URL

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Dynamically set sqlalchemy.url from our application config
config.set_main_option("sqlalchemy.url", DATABASE_URL)

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    is_postgres = connection.dialect.name == "postgresql"
    lock_id = 894723894723  # Dedicated 64-bit advisory lock key to prevent concurrent migration races across overlapping container deploys

    if is_postgres:
        connection.execute(text(f"SELECT pg_advisory_lock({lock_id})"))

    try:
        from sqlalchemy import inspect
        inspector = inspect(connection)
        table_names = inspector.get_table_names()

        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            # If tables were pre-created by create_all but alembic_version is not set, stamp initial revision
            if "app_users" in table_names and "alembic_version" not in table_names:
                context.stamp(context.script, "ae9b4dcc8a72")
            else:
                context.run_migrations()
    except Exception:
        if is_postgres:
            try:
                connection.rollback()
            except Exception:
                pass
        raise
    finally:
        if is_postgres:
            try:
                connection.execute(text(f"SELECT pg_advisory_unlock({lock_id})"))
            except Exception:
                pass


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
