"""
Alembic environment configuration — async-aware.

This file tells Alembic how to connect to the database and which
models to track. We override the default sync approach with async
engine support so it works with our aiosqlite driver.
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Import our config to get DATABASE_URL
from backend.config import get_settings

# Import all models so Base.metadata knows about every table.
# This is the critical import — without it, autogenerate finds nothing.
from backend.models import Base  # noqa: F401

# Alembic Config object (reads alembic.ini)
config = context.config

# Set up Python logging from the ini file
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Point Alembic at our models' metadata
target_metadata = Base.metadata

# Override the sqlalchemy.url from our .env-based config.
# We use the sync driver URL here (sqlite:///) because Alembic's
# migration runner works synchronously even with async_engine_from_config.
settings = get_settings()
db_url = settings.database_url
# Alembic needs the sync URL for its internal operations, but
# async_engine_from_config handles the translation.
config.set_main_option("sqlalchemy.url", db_url)


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode — generates SQL without connecting.
    Useful for producing migration scripts for a DBA.
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


def do_run_migrations(connection):
    """Helper: run migrations inside a connection context."""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """
    Run migrations in 'online' mode using an async engine.
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
    """Entry point for online migrations — delegates to async."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
