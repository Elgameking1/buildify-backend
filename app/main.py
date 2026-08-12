"""Application entry point.

Run locally with:  uvicorn app.main:app --reload
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import text

from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.logging import RequestContextMiddleware, configure_logging
from app.core.middleware import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from app.core.rate_limit import limiter
from app.db import registry  # noqa: F401  - registers every model with Base
from app.db.session import SessionFactory, engine
from app.modules.auth.router import router as auth_router
from app.modules.catalog.router import router as catalog_router
from app.modules.jobs.router import router as jobs_router
from app.modules.media.router import router as media_router
from app.modules.notifications.router import router as notifications_router
from app.modules.orders.router import router as orders_router
from app.modules.reviews.router import router as reviews_router
from app.modules.users.router import router as users_router
from app.modules.workers.router import router as workers_router

logger = logging.getLogger(__name__)

DESCRIPTION = """
Backend for **Buildify** - construction materials and skilled workers in one marketplace.

Two halves over one identity system:

* **Materials marketplace** - categories, product listings, search, cart, orders.
* **Worker hiring** - worker profiles, skill/location search, job requests, ratings.

Roles are `CLIENT`, `VENDOR` and `WORKER`; each has its own dashboard endpoints.
Authenticate with `POST /api/v1/auth/login`, then send `Authorization: Bearer <access_token>`.

Online payment, delivery logistics and the mobile app are out of scope by design.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging("DEBUG" if settings.debug else "INFO")
    logger.info("Starting %s in %s mode", settings.app_name, settings.environment)
    yield
    await engine.dispose()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        description=DESCRIPTION,
        version="0.1.0",
        lifespan=lifespan,
        # In production the interactive docs are switched off: they are a
        # complete, free API map for anyone probing the service. Override with
        # EXPOSE_DOCS=true if a marker needs to browse the deployed instance.
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )

    # Middleware order matters: add_middleware puts the most recently added
    # OUTERMOST.  CORS is therefore added last, so even a 429 from the rate
    # limiter comes back with the headers a browser needs to read it.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    # Outermost of the body-handling stack: reject an oversized body before it
    # is buffered or a handler is reached.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)

    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)

    # An explicit origin list - never "*" together with credentials.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    register_exception_handlers(app)

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limited(request, exc):  # type: ignore[no-untyped-def]
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many requests. Try again shortly.", "code": "rate_limited"},
        )

    for router in (
        auth_router,
        users_router,
        catalog_router,
        orders_router,
        workers_router,
        jobs_router,
        reviews_router,
        media_router,
        notifications_router,
    ):
        app.include_router(router, prefix=settings.api_v1_prefix)

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        """Liveness probe for the cloud host - also proves the DB is reachable."""
        try:
            async with SessionFactory() as session:
                await session.execute(text("SELECT 1"))
            database = "up"
        except Exception:  # pragma: no cover - only on a broken deployment
            logger.exception("Health check failed to reach the database")
            database = "down"

        return {
            "status": "ok" if database == "up" else "degraded",
            "database": database,
            "environment": settings.environment,
        }

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"name": settings.app_name, "docs": "/docs", "health": "/health"}

    return app


app = create_app()
