"""Run Alembic migrations programmatically at app boot.

Replaces the old `Base.metadata.create_all()` boot step: a brand-new database upgrades from
no revision to head (creating every table, the same end state `create_all()` used to produce),
and an already-migrated database just applies whatever revisions it's missing. Existing
deployments whose database was created by the old `create_all()` path (i.e. has the app's
tables but no `alembic_version` row) are stamped at the baseline revision first, since that
schema is byte-for-byte what the baseline migration also produces; anything newer than the
baseline behaves exactly like a normal incremental upgrade for them from that point on.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic import command

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_ALEMBIC_INI = _BACKEND_ROOT / "alembic.ini"

# The baseline migration's revision id: the schema it creates is identical to what
# `create_all()` used to produce, so a pre-existing database (no alembic_version table yet)
# gets stamped here rather than replayed against tables that already exist.
_BASELINE_REVISION = "184e48b9d542"


def _alembic_config(database_url: str) -> Config:
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


async def _has_pre_migration_schema(engine: AsyncEngine) -> bool:
    def _check(sync_conn) -> bool:
        inspector = inspect(sync_conn)
        tables = set(inspector.get_table_names())
        return "users" in tables and "alembic_version" not in tables

    async with engine.connect() as conn:
        return await conn.run_sync(_check)


async def run_migrations(engine: AsyncEngine, database_url: str) -> None:
    """Bring the database up to the latest schema, stamping pre-Alembic databases first."""
    config = _alembic_config(database_url)
    needs_stamp = await _has_pre_migration_schema(engine)

    def _run_sync() -> None:
        # command.stamp/upgrade call Alembic's env.py, which wraps its async engine work in
        # asyncio.run(); that fails with "cannot be called from a running event loop" if
        # invoked directly from FastAPI's own lifespan loop, so this whole step runs in a
        # worker thread instead, off the loop that's calling run_migrations().
        if needs_stamp:
            command.stamp(config, _BASELINE_REVISION)
        command.upgrade(config, "head")

    await asyncio.to_thread(_run_sync)
