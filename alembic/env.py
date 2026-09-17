"""Alembic environment.

The URL comes from the app's own settings, never from alembic.ini, so a
migration can never run against a different database than the app is using.

Async engines are the norm here (asyncpg in production, aiosqlite in tests), so
migrations run through ``connection.run_sync``.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from chmabapay.config import get_settings
from chmabapay.models import Base

target_metadata = Base.metadata


def _database_url() -> str:
    return get_settings().database_url


def _configure(**kwargs) -> None:  # type: ignore[no-untyped-def]
    context.configure(target_metadata=target_metadata, compare_type=True, **kwargs)


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it (`alembic upgrade --sql`)."""
    _configure(
        url=_database_url(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:  # type: ignore[no-untyped-def]
    # SQLite cannot ALTER in place; batch mode rewrites the table instead so the
    # same migrations work on both backends.
    render_as_batch = connection.dialect.name == "sqlite"
    _configure(connection=connection, render_as_batch=render_as_batch)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    engine = create_async_engine(_database_url(), poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_do_run_migrations)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
