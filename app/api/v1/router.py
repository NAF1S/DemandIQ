"""Version 1 API surface."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import predictions, storage

api_router = APIRouter()
api_router.include_router(predictions.router)
api_router.include_router(storage.router)

__all__ = ["api_router"]
