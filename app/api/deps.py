"""Reusable FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import NotConfiguredError, UnauthorizedError
from app.db.session import get_db, get_optional_db
from app.services.model_service import ModelRegistry, registry
from app.services.supabase_client import get_user_from_token

SettingsDep = Annotated[Settings, Depends(get_settings)]
DbSession = Annotated[AsyncSession, Depends(get_db)]
OptionalDbSession = Annotated[AsyncSession | None, Depends(get_optional_db)]


def get_model_registry() -> ModelRegistry:
    return registry


ModelRegistryDep = Annotated[ModelRegistry, Depends(get_model_registry)]


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def get_optional_user(
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any] | None:
    """Resolve the Supabase user when a token is supplied, otherwise ``None``.

    Used for attribution (e.g. ``requested_by`` on prediction logs) rather than access
    control, so an invalid token is treated as anonymous instead of a hard failure.
    """
    token = _extract_bearer_token(authorization)
    if token is None:
        return None
    try:
        return await get_user_from_token(token)
    except (UnauthorizedError, NotConfiguredError):
        return None


async def require_user(
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Dependency for routes that must be called with a valid Supabase access token."""
    token = _extract_bearer_token(authorization)
    if token is None:
        raise UnauthorizedError("Provide a Supabase access token as 'Authorization: Bearer <token>'.")
    return await get_user_from_token(token)


CurrentUser = Annotated[dict[str, Any], Depends(require_user)]
OptionalUser = Annotated[dict[str, Any] | None, Depends(get_optional_user)]
