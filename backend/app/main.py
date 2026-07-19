"""Cadence FastAPI application factory and ASGI entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes_auth import router as auth_router
from app.api.routes_health import router as health_router
from app.api.routes_requests import router as requests_router
from app.api.routes_search import router as search_router
from app.api.routes_settings import router as settings_router
from app.core.config import get_settings
from app.core.limiter import limiter
from app.core.middleware import SecurityHeadersMiddleware
from app.db.base import get_engine, get_sessionmaker, init_engine
from app.db.migrations import run_migrations
from app.demo.seed import seed_demo_data
from app.services.settings_store import seed_db_from_env_on_boot
from app.services.users import bootstrap_admin
from app.workers.fulfillment_worker import FulfillmentWorker, set_worker

logging.basicConfig(level=logging.INFO, format="%(message)s")
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
)

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_engine(settings.resolved_database_url)
    await run_migrations(get_engine(), settings.resolved_database_url)

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        await bootstrap_admin(
            session,
            email=settings.admin_bootstrap_email,
            password=settings.admin_bootstrap_password,
        )
        if not settings.demo_mode:
            await seed_db_from_env_on_boot(session, settings)
        if settings.demo_mode:
            await seed_demo_data(session)

    worker = FulfillmentWorker(settings)
    set_worker(worker)
    if not settings.demo_mode:
        worker.start()

    logger.info(
        "cadence_started",
        version=settings.version,
        mode="demo" if settings.demo_mode else "live",
    )

    yield

    if worker is not None:
        await worker.stop()
    set_worker(None)


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Cadence",
        version=settings.version,
        description="Self-hostable single-track music request service.",
        lifespan=lifespan,
    )

    app.state.limiter = limiter
    # slowapi's handler signature is narrower than Starlette's typed ExceptionHandler protocol
    # expects; this is slowapi's own documented usage pattern, not a real type mismatch.
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    if settings.cors_origins_list:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins_list,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ):
        # exc.errors() can contain non-JSON-serializable values (e.g. the raised ValueError
        # instance itself in ctx.error for custom @field_validator errors), so this must go
        # through jsonable_encoder rather than being handed to JSONResponse raw.
        safe_errors = jsonable_encoder(exc.errors())
        logger.info("validation_error", path=request.url.path, errors=safe_errors)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": "Invalid request", "errors": safe_errors},
        )

    @app.exception_handler(SQLAlchemyError)
    async def db_exception_handler(request: Request, exc: SQLAlchemyError):
        logger.error(
            "database_error", path=request.url.path, error=str(exc), exc_info=True
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "A database error occurred"},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.error(
            "unhandled_exception", path=request.url.path, error=str(exc), exc_info=True
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error"},
        )

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(search_router)
    app.include_router(requests_router)
    app.include_router(settings_router)

    static_dir = Path(__file__).resolve().parent.parent / "static"
    if static_dir.exists():
        index_path = static_dir / "index.html"
        app.mount(
            "/assets",
            StaticFiles(directory=str(static_dir / "assets")),
            name="web-assets",
        )

        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(full_path: str):
            """Serve the built web client, falling back to index.html for client-side routes.

            React Router owns paths like /login, /search, /requests/<id> entirely client-side;
            the server has no matching route for them, so without this fallback a browser
            refresh or direct link to any of those paths would 404 instead of loading the SPA,
            which then renders the correct view itself. Never intercepts /api/* (those are
            registered above and matched first, but an unknown /api/* path must still 404 as
            JSON rather than fall through to index.html, hence the explicit guard).
            """
            if full_path.startswith("api/"):
                return JSONResponse(status_code=404, content={"detail": "Not found"})

            candidate = (static_dir / full_path).resolve()
            static_dir_resolved = static_dir.resolve()
            is_within_static_dir = (
                candidate == static_dir_resolved
                or static_dir_resolved in candidate.parents
            )
            if full_path and is_within_static_dir and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index_path)

    return app


app = create_app()
