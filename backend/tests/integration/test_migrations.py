"""Integration tests for the Alembic migration boot path (app/db/migrations.py).

Covers the two real-world cases: a brand-new database (upgrade from nothing to head, same
end state `create_all()` used to produce) and an existing pre-Alembic database that already
has the app's tables from the old `create_all()` boot step but no `alembic_version` row
(stamped at the baseline revision, then upgraded normally from there).
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, text

from app.db.base import dispose_engine, get_engine, init_engine
from app.db.migrations import run_migrations


@pytest.fixture(autouse=True)
async def _cleanup_engine():
    yield
    await dispose_engine()


async def test_run_migrations_creates_schema_on_fresh_database(tmp_path):
    db_path = tmp_path / "fresh.db"
    database_url = f"sqlite+aiosqlite:///{db_path}"
    init_engine(database_url)

    await run_migrations(get_engine(), database_url)

    def _tables(sync_conn) -> set[str]:
        return set(inspect(sync_conn).get_table_names())

    async with get_engine().connect() as conn:
        tables = await conn.run_sync(_tables)

    assert {
        "users",
        "requests",
        "fulfillment_attempts",
        "integration_settings",
    } <= tables
    assert "alembic_version" in tables


async def test_run_migrations_stamps_pre_alembic_database(tmp_path):
    db_path = tmp_path / "legacy.db"
    database_url = f"sqlite+aiosqlite:///{db_path}"

    # Simulate a database created by the old create_all() boot step. Crucially, a REAL
    # pre-Alembic database has the schema as it was when create_all() was the boot path
    # (exactly the baseline revision), not whatever today's models look like — using
    # Base.metadata.create_all here would bake in every later column and then collide with
    # the incremental migrations this test exists to prove get applied. So: migrate a fresh
    # DB to the baseline revision only, then drop alembic_version to erase the bookkeeping.
    import asyncio

    from alembic import command
    from app.db.migrations import _BASELINE_REVISION, _alembic_config

    init_engine(database_url)
    config = _alembic_config(database_url)
    await asyncio.to_thread(command.upgrade, config, _BASELINE_REVISION)
    async with get_engine().begin() as conn:
        await conn.execute(text("DROP TABLE alembic_version"))

    async def _has_alembic_version() -> bool:
        def _check(sync_conn) -> bool:
            return "alembic_version" in inspect(sync_conn).get_table_names()

        async with get_engine().connect() as conn:
            return await conn.run_sync(_check)

    assert await _has_alembic_version() is False

    await run_migrations(get_engine(), database_url)

    assert await _has_alembic_version() is True

    # The legacy database must have been carried past the baseline to head: the column added
    # by the second migration has to exist now.
    def _request_columns(sync_conn) -> set[str]:
        return {col["name"] for col in inspect(sync_conn).get_columns("requests")}

    async with get_engine().connect() as conn:
        columns = await conn.run_sync(_request_columns)
    assert "desired_quality" in columns

    # The stamp must not have touched or recreated any pre-existing table's data.
    async with get_engine().connect() as conn:
        result = await conn.execute(text("SELECT COUNT(*) FROM users"))
        assert result.scalar_one() == 0


async def test_run_migrations_is_idempotent_on_already_migrated_database(tmp_path):
    db_path = tmp_path / "twice.db"
    database_url = f"sqlite+aiosqlite:///{db_path}"
    init_engine(database_url)

    await run_migrations(get_engine(), database_url)
    await run_migrations(get_engine(), database_url)

    def _tables(sync_conn) -> set[str]:
        return set(inspect(sync_conn).get_table_names())

    async with get_engine().connect() as conn:
        tables = await conn.run_sync(_tables)

    assert "users" in tables
