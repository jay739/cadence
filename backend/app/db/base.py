"""SQLAlchemy declarative base and async engine/session setup."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _tune_sqlite_connection(dbapi_connection, _connection_record) -> None:
    """Per-connection SQLite pragmas, applied via the pool's connect event.

    - WAL journal: readers (search, request lists) no longer block behind the fulfillment
      worker's writes, and a crash mid-write can't corrupt the main database file.
    - synchronous=NORMAL: the safe pairing with WAL (fsync at checkpoint, not every commit),
      substantially cheaper than FULL with no integrity loss for this workload.
    - busy_timeout: a briefly-locked database waits instead of instantly raising
      "database is locked" when the worker and a request commit at the same moment.
    - foreign_keys=ON: SQLite ships with FK enforcement OFF per connection; without this the
      requests.user_id / fulfillment_attempts.request_id constraints are decorative.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def init_engine(database_url: str, *, echo: bool = False):
    global _engine, _sessionmaker
    connect_args = {}
    is_sqlite = database_url.startswith("sqlite")
    if is_sqlite:
        connect_args["check_same_thread"] = False
    _engine = create_async_engine(database_url, echo=echo, connect_args=connect_args)
    if is_sqlite:
        event.listen(_engine.sync_engine, "connect", _tune_sqlite_connection)
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
