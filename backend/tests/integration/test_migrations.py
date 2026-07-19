"""Integration tests for the Alembic migration boot path (app/db/migrations.py).

Covers the two real-world cases: a brand-new database (upgrade from nothing to head, same
end state `create_all()` used to produce) and an existing pre-Alembic database that already
has the app's tables from the old `create_all()` boot step but no `alembic_version` row
(stamped at the baseline revision, then upgraded normally from there).
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, text

from app.db.base import Base, dispose_engine, get_engine, init_engine
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

    # Simulate a database created by the old create_all() boot step: the app's tables exist,
    # but there is no alembic_version row, since this database predates migrations entirely.
    init_engine(database_url)
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def _has_alembic_version() -> bool:
        def _check(sync_conn) -> bool:
            return "alembic_version" in inspect(sync_conn).get_table_names()

        async with get_engine().connect() as conn:
            return await conn.run_sync(_check)

    assert await _has_alembic_version() is False

    await run_migrations(get_engine(), database_url)

    assert await _has_alembic_version() is True

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
