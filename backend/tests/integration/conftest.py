from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.core.limiter import limiter
from app.db.base import get_sessionmaker
from app.main import create_app
from app.models.user import UserRole


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Each test gets a clean rate-limit bucket; otherwise tests sharing the 127.0.0.1 test
    client IP would trip each other's /api/auth or /api/search limits (the dedicated
    TestSearchRateLimit / auth-rate-limit tests intentionally exhaust their own budget).
    """
    limiter.reset()
    yield
    limiter.reset()


def _set_test_env(monkeypatch, tmp_path, *, demo_mode: bool = False) -> None:
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DEMO_MODE", "true" if demo_mode else "false")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv(
        "SESSION_SECRET", "test-session-secret-value-not-real-32bytes-long"
    )
    monkeypatch.setenv("MUSIC_LIBRARY_PATH", str(tmp_path / "library"))
    monkeypatch.delenv("ADMIN_BOOTSTRAP_EMAIL", raising=False)
    monkeypatch.delenv("ADMIN_BOOTSTRAP_PASSWORD", raising=False)
    monkeypatch.delenv("PROWLARR_URL", raising=False)
    monkeypatch.delenv("PROWLARR_API_KEY", raising=False)
    monkeypatch.delenv("QBITTORRENT_URL", raising=False)
    monkeypatch.delenv("SLSKD_URL", raising=False)
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)
    get_settings.cache_clear()


async def _run_with_lifespan(app) -> AsyncIterator[AsyncClient]:
    """Manually drive the app's lifespan context, since httpx's ASGITransport does not.

    Equivalent to what the `asgi-lifespan` package provides, implemented directly against
    Starlette's router to avoid an extra dependency for tests only.
    """
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            yield client


@pytest_asyncio.fixture
async def app_client(tmp_path, monkeypatch) -> AsyncIterator[AsyncClient]:
    """A live app wired to a fresh temp-file SQLite DB, wrapped in an httpx AsyncClient."""
    _set_test_env(monkeypatch, tmp_path, demo_mode=False)
    app = create_app()
    async for client in _run_with_lifespan(app):
        yield client
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def demo_app_client(tmp_path, monkeypatch) -> AsyncIterator[AsyncClient]:
    _set_test_env(monkeypatch, tmp_path, demo_mode=True)
    app = create_app()
    async for client in _run_with_lifespan(app):
        yield client
    get_settings.cache_clear()


async def register_and_login(client: AsyncClient, email: str, password: str) -> dict:
    resp = await client.post(
        "/api/auth/register", json={"email": email, "password": password}
    )
    assert resp.status_code == 201, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def make_admin_headers(client: AsyncClient) -> dict:
    """Register a user then promote to admin directly via the DB session factory."""
    await register_and_login(client, "admin-test@example.com", "a-strong-password-1")
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        from sqlalchemy import select

        from app.models.user import User

        result = await session.execute(
            select(User).where(User.email == "admin-test@example.com")
        )
        user = result.scalar_one()
        user.role = UserRole.ADMIN
        await session.commit()
    # token issued before promotion still has role=user claim; log in again for a fresh token
    resp = await client.post(
        "/api/auth/login",
        json={"email": "admin-test@example.com", "password": "a-strong-password-1"},
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
