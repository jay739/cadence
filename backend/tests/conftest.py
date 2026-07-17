from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import create_all, dispose_engine, get_sessionmaker, init_engine

# Note: the session-scoped event loop (needed because aiosqlite's background worker thread
# schedules callbacks back onto the loop that opened each connection, and a function-scoped
# loop would be torn down mid-flight between tests) is configured via
# asyncio_default_fixture_loop_scope = "session" in pyproject.toml, not a custom fixture here.


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """A fresh in-memory SQLite database per test function."""
    init_engine("sqlite+aiosqlite:///:memory:")
    await create_all()
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        yield session
    await dispose_engine()
