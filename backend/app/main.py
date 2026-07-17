"""Cadence FastAPI application factory and ASGI entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes_auth import router as auth_router
from app.api.routes_health import router as health_router
from app.api.routes_requests import router as requests_router
from app.api.routes_search import router as search_router
from app.core.config import Settings, get_settings
from app.core.limiter import limiter
from app.core.middleware import SecurityHeadersMiddleware
from app.db.base import create_all, get_sessionmaker, init_engine
from app.demo.seed import seed_demo_data
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
    await create_all()

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        await bootstrap_admin(
            session,
            email=settings.admin_bootstrap_email,
            password=settings.admin_bootstrap_password,
        )
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
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
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
        logger.info("validation_error", path=request.url.path, errors=exc.errors())
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": "Invalid request", "errors": exc.errors()},
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

    static_dir = Path(__file__).resolve().parent.parent / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="web")

    return app


app = create_app()
