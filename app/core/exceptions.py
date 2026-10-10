"""Application errors and the JSON handlers that render them."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for errors that map to a specific HTTP status code."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.__class__.__doc__ or self.code
        super().__init__(self.detail)


class NotConfiguredError(AppError):
    """A required integration (Supabase or Postgres) is not configured."""

    status_code = 503
    code = "not_configured"


class ModelNotFoundError(AppError):
    """No predictor is registered under the requested name."""

    status_code = 404
    code = "model_not_found"


class ModelNotReadyError(AppError):
    """The predictor exists but its artifacts are not available yet."""

    status_code = 503
    code = "model_not_ready"


class InvalidModelInputError(AppError):
    """The feature payload cannot be scored by the model."""

    status_code = 422
    code = "invalid_model_input"


class SupabaseError(AppError):
    """A Supabase Storage or Auth call failed."""

    status_code = 502
    code = "supabase_error"


class UnauthorizedError(AppError):
    """Missing or invalid Supabase access token."""

    status_code = 401
    code = "unauthorized"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.exception("Request to %s failed: %s", request.url.path, exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "detail": exc.detail}},
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "detail": "Internal server error"}},
        )
