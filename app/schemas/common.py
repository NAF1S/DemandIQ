"""Shared response models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    code: str
    detail: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class HealthStatus(BaseModel):
    status: str = Field(description="'ok' when healthy, otherwise 'degraded'.")
    service: str
    version: str
    environment: str
    checks: dict[str, str] = Field(default_factory=dict)
