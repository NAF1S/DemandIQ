"""FastAPI application entry point.

Run locally with:

    uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import health
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.db.session import dispose_engine
from app.services.model_service import register_demo_predictor
from app.services.supabase_client import close_supabase_client

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()

    if settings.enable_demo_predictor:
        register_demo_predictor()
        logger.warning(
            "The placeholder 'demo' predictor is registered. Wire in your own model and set "
            "ENABLE_DEMO_PREDICTOR=false before deploying."
        )
    if not settings.database_configured:
        logger.warning("DATABASE_URL is not set - data endpoints will report 'not_configured'.")
    if not settings.supabase_configured:
        logger.warning("Supabase credentials are not set - Storage and Auth endpoints are unavailable.")

    try:
        yield
    finally:
        await close_supabase_client()
        await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="FastAPI service that reads and writes Supabase data and serves ML predictions.",
        debug=settings.debug,
        lifespan=lifespan,
    )

    allow_all_origins = settings.cors_origins == ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=not allow_all_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, Any]:
        return {
            "service": settings.app_name,
            "version": settings.app_version,
            "environment": settings.environment,
            "docs": "/docs",
            "api": settings.api_v1_prefix,
        }

    return app


app = create_app()
