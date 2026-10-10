"""Request/response models for the prediction endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PredictionRequest(BaseModel):
    features: dict[str, Any] = Field(
        description="Feature payload handed to the model. Keys must match what the model expects."
    )
    entity_id: str | None = Field(
        default=None, description="Optional identifier of the scored entity (e.g. a SKU or store id)."
    )
    options: dict[str, Any] = Field(
        default_factory=dict, description="Model-specific options such as a horizon or threshold."
    )
    log: bool = Field(default=True, description="Persist the request/response to `prediction_logs`.")


class PredictionResponse(BaseModel):
    model_name: str
    model_version: str | None = None
    prediction: Any = Field(description="Model output. Shape is defined by the predictor.")
    probabilities: dict[str, float] | None = None
    latency_ms: float
    log_id: UUID | None = None


class ModelInfo(BaseModel):
    name: str
    version: str | None = None
    description: str | None = None
    loaded: bool = False


class PredictionLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    model_name: str
    model_version: str | None
    entity_id: str | None
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    latency_ms: float | None
    requested_by: str | None
    created_at: datetime
