"""Data access helpers.

Query code lives here so routes stay thin and the ORM never leaks into the HTTP layer.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PredictionLog


async def create_prediction_log(
    session: AsyncSession,
    *,
    model_name: str,
    model_version: str | None,
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    latency_ms: float,
    entity_id: str | None = None,
    requested_by: str | None = None,
) -> PredictionLog:
    entry = PredictionLog(
        model_name=model_name,
        model_version=model_version,
        entity_id=entity_id,
        inputs=inputs,
        outputs=outputs,
        latency_ms=latency_ms,
        requested_by=requested_by,
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return entry


async def list_prediction_logs(
    session: AsyncSession,
    *,
    model_name: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[PredictionLog]:
    statement = select(PredictionLog).order_by(PredictionLog.created_at.desc()).limit(limit).offset(offset)
    if model_name:
        statement = statement.where(PredictionLog.model_name == model_name)
    result = await session.execute(statement)
    return list(result.scalars().all())


async def get_prediction_log(session: AsyncSession, log_id: uuid.UUID) -> PredictionLog | None:
    return await session.get(PredictionLog, log_id)
