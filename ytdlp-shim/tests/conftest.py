from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import _jobs, app


@pytest.fixture(autouse=True)
def _clear_jobs():
    """Job state is a module-level dict; tests must not leak jobs into each other."""
    _jobs.clear()
    yield
    _jobs.clear()


@pytest.fixture
def client() -> TestClient:
    """Synchronous client for endpoints with no background work: /search, input validation.

    Do NOT use this for anything that spawns a background task (POST /downloads and its
    polling). Starlette's TestClient runs the app on its own portal thread that does not keep
    advancing between calls, confirmed directly: a fire-and-forget asyncio task scheduled
    through it can sit at 0% progress across 2+ real seconds of repeated polling. Real uvicorn
    runs one continuously-executing loop, so this is a test-harness artifact, not a production
    behavior, but it means TestClient cannot validate anything background-task-shaped here.
    """
    return TestClient(app)


@pytest.fixture
async def async_client():
    """Async client sharing the test's own event loop, for anything spawning a background task.

    Runs the ASGI app in-process on the SAME loop pytest-asyncio drives the test with, so a
    task started via asyncio.ensure_future() inside a route handler is a real task on a real,
    continuously-running loop and actually gets to progress between awaits, the way it does
    under uvicorn in production.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
