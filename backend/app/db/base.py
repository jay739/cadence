"""SQLAlchemy declarative base and async engine/session setup."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init_engine(database_url: str, *, echo: bool = False):
    global _engine, _sessionmaker
    connect_args = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    _engine = create_async_engine(database_url, echo=echo, connect_args=connect_args)
    _sessionmaker = async_sessionmaker(
        _engine, expire_on_commit=False, class_=AsyncSession
    )
    return _engine


def get_engine():
    if _engine is None:
        raise RuntimeError("Database engine not initialized. Call init_engine() first.")
    return _engine


async def dispose_engine() -> None:
    """Dispose the current engine's connection pool, if one exists.

    Needed whenever init_engine() is called again to point at a new database (e.g. between
    test cases sharing one event loop): without disposing the prior engine first, aiosqlite's
    background worker thread for the old connection is abandoned mid-flight rather than closed
    cleanly, and later raises "Event loop is closed" from its own thread once the loop it was
    scheduled against eventually closes.
    """
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError("Session factory not initialized. Call init_engine() first.")
    return _sessionmaker


async def create_all() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        yield session
