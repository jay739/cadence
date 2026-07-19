"""Unit tests for the SQLite connection tuning in app.db.base.

Verifies the actual PRAGMA values a real connection reports, not just that the code runs
without error: a typo in a PRAGMA name is silently ignored by SQLite rather than raising, so
asserting on the reported value is the only way to catch that class of mistake.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.db.base import create_all, dispose_engine, get_engine, init_engine

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _cleanup_engine():
    yield
    await dispose_engine()


async def test_sqlite_connections_get_wal_and_tuning_pragmas(tmp_path):
    db_path = tmp_path / "pragma.db"
    init_engine(f"sqlite+aiosqlite:///{db_path}")

    async with get_engine().connect() as conn:
        assert (await conn.execute(text("PRAGMA journal_mode"))).scalar() == "wal"
        assert (await conn.execute(text("PRAGMA synchronous"))).scalar() == 1
        assert (await conn.execute(text("PRAGMA busy_timeout"))).scalar() == 5000
        assert (await conn.execute(text("PRAGMA foreign_keys"))).scalar() == 1


async def test_pragmas_apply_to_every_pooled_connection_not_just_the_first(tmp_path):
    """The pool can open more than one physical connection; the event listener must fire on
    each one, not only whichever connection happened to be first.
    """
    db_path = tmp_path / "pragma-pool.db"
    init_engine(f"sqlite+aiosqlite:///{db_path}")

    async with get_engine().connect() as first, get_engine().connect() as second:
        assert (await first.execute(text("PRAGMA foreign_keys"))).scalar() == 1
        assert (await second.execute(text("PRAGMA foreign_keys"))).scalar() == 1


async def test_foreign_key_enforcement_rejects_an_orphaned_request(tmp_path):
    """The whole point of turning foreign_keys ON: a request row referencing a nonexistent
    user must now be rejected by SQLite itself, not silently accepted as it was before this
    pragma existed (SQLite ships FK enforcement OFF per connection by default).
    """
    from sqlalchemy.exc import IntegrityError

    from app.models.request import Request, RequestStatus

    db_path = tmp_path / "fk.db"
    init_engine(f"sqlite+aiosqlite:///{db_path}")
    await create_all()

    from app.db.base import get_sessionmaker

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        session.add(
            Request(
                user_id="does-not-exist",
                title="t",
                artist="a",
                source="apple",
                source_id="1",
                dedupe_key="k",
                status=RequestStatus.PENDING,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
