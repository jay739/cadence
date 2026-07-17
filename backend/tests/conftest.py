from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import create_all, get_sessionmaker, init_engine


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """A fresh in-memory SQLite database per test function."""
    init_engine("sqlite+aiosqlite:///:memory:")
    await create_all()
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        yield session
