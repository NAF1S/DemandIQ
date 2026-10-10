"""Liveness and readiness probes (mounted outside the versioned prefix)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Response
from sqlalchemy import text

from app.api.deps import SettingsDep
from app.db.session import get_engine
from app.schemas.common import HealthStatus
from app.services.supabase_client import check_supabase_reachable

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthStatus, summary="Liveness probe")
async def health(settings: SettingsDep) -> HealthStatus:
    """Reports that the process is up. Does not touch any dependency."""
    return HealthStatus(
        status="ok",
        service=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
    )


@router.get("/health/ready", response_model=HealthStatus, summary="Readiness probe")
async def readiness(settings: SettingsDep, response: Response) -> HealthStatus:
    """Verifies that Postgres and Supabase are reachable. Returns 503 when degraded."""
    checks: dict[str, str] = {}

    if not settings.database_configured:
        checks["database"] = "not_configured"
    else:
        try:
            engine = get_engine()
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:
            logger.warning("Database readiness check failed: %s", exc)
            checks["database"] = f"error: {type(exc).__name__}"

    if not settings.supabase_configured:
        checks["supabase"] = "not_configured"
    else:
        checks["supabase"] = "ok" if await check_supabase_reachable() else "unreachable"

    healthy = all(value == "ok" for value in checks.values())
    if not healthy:
        response.status_code = 503

    return HealthStatus(
        status="ok" if healthy else "degraded",
        service=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
        checks=checks,
    )
