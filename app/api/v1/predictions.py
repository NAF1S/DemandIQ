"""Prediction endpoints.

Scoring is delegated to the model registry, so these routes stay valid no matter which
framework or serving stack backs the model.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Query

from app.api.deps import ModelRegistryDep, OptionalDbSession, OptionalUser
from app.db.repositories import create_prediction_log, list_prediction_logs
from app.schemas.common import Page
from app.schemas.prediction import (
    ModelInfo,
    PredictionLogRead,
    PredictionRequest,
    PredictionResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/predictions", tags=["predictions"])


@router.get("/models", response_model=list[ModelInfo], summary="List registered models")
async def list_models(models: ModelRegistryDep) -> list[ModelInfo]:
    return [
        ModelInfo(name=item.name, version=item.version, description=item.description, loaded=item.loaded)
        for item in models.describe()
    ]


@router.get("/logs", response_model=Page[PredictionLogRead], summary="Recent prediction logs")
async def list_logs(
    db: OptionalDbSession,
    model_name: str | None = Query(default=None, description="Filter by model name."),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[PredictionLogRead]:
    if db is None:
        return Page[PredictionLogRead](items=[], total=0, limit=limit, offset=offset)

    entries = await list_prediction_logs(db, model_name=model_name, limit=limit, offset=offset)
    return Page[PredictionLogRead](
        items=[PredictionLogRead.model_validate(entry) for entry in entries],
        total=len(entries),
        limit=limit,
        offset=offset,
    )


@router.post("/{model_name}", response_model=PredictionResponse, summary="Score a feature payload")
async def predict(
    model_name: str,
    payload: PredictionRequest,
    models: ModelRegistryDep,
    db: OptionalDbSession,
    user: OptionalUser,
) -> PredictionResponse:
    started = time.perf_counter()
    result = models.predict(model_name, payload.features, payload.options or None)
    latency_ms = (time.perf_counter() - started) * 1000

    log_id = None
    if payload.log and db is not None:
        try:
            entry = await create_prediction_log(
                db,
                model_name=model_name,
                model_version=result.model_version,
                inputs=payload.features,
                outputs={"prediction": result.prediction, "probabilities": result.probabilities},
                latency_ms=latency_ms,
                entity_id=payload.entity_id,
                requested_by=user.get("id") if user else None,
            )
            log_id = entry.id
        except Exception:
            # Logging is best-effort: a persistence failure must not fail the prediction.
            await db.rollback()
            logger.exception("Could not persist prediction log for '%s'", model_name)

    return PredictionResponse(
        model_name=model_name,
        model_version=result.model_version,
        prediction=result.prediction,
        probabilities=result.probabilities,
        latency_ms=round(latency_ms, 3),
        log_id=log_id,
    )
