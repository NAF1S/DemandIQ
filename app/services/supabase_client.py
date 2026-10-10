"""Supabase REST integration: Storage buckets and Auth token verification.

Relational data goes through the direct Postgres connection in :mod:`app.db`; this module
covers the Supabase-specific surfaces. Storage is where model artifacts (``.joblib``,
``.onnx``, ``.pt``, feature encoders) live, so predictors can pull them at load time.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from supabase import AsyncClient, acreate_client

from app.core.config import get_settings
from app.core.exceptions import NotConfiguredError, SupabaseError, UnauthorizedError
from app.schemas.storage import StorageObject

logger = logging.getLogger(__name__)

_client: AsyncClient | None = None

_NOT_CONFIGURED_MESSAGE = (
    "Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY "
    "(or SUPABASE_ANON_KEY) in your .env file."
)


async def get_supabase_client() -> AsyncClient:
    """Return a cached AsyncClient bound to the service role key."""
    global _client
    if _client is None:
        settings = get_settings()
        if not settings.supabase_configured:
            raise NotConfiguredError(_NOT_CONFIGURED_MESSAGE)
        _client = await acreate_client(settings.supabase_url, settings.supabase_api_key)
    return _client


async def close_supabase_client() -> None:
    global _client
    if _client is not None:
        close = getattr(_client, "aclose", None) or getattr(_client, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result
    _client = None


def model_bucket() -> str:
    """Name of the Storage bucket that holds model artifacts."""
    return get_settings().supabase_model_bucket


async def check_supabase_reachable(timeout: float = 5.0) -> bool:
    """Ping Supabase's public auth health endpoint without needing a valid key."""
    settings = get_settings()
    if not settings.supabase_configured:
        return False
    url = settings.supabase_url.rstrip("/") + "/auth/v1/health"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, headers={"apikey": settings.supabase_api_key})
        return response.status_code < 500
    except httpx.HTTPError as exc:
        logger.warning("Supabase health check failed: %s", exc)
        return False


# --------------------------------------------------------------------------- Storage


async def upload_artifact(
    path: str,
    data: bytes,
    *,
    content_type: str = "application/octet-stream",
    upsert: bool = True,
) -> str:
    """Upload an artifact, returning its path inside the bucket."""
    settings = get_settings()
    bucket = settings.supabase_model_bucket
    client = await get_supabase_client()
    try:
        await client.storage.from_(bucket).upload(
            path,
            data,
            {"content-type": content_type, "upsert": "true" if upsert else "false"},
        )
    except Exception as exc:  # storage3 raises its own error types
        raise SupabaseError(f"Failed to upload '{path}' to bucket '{bucket}': {exc}") from exc
    logger.info("Uploaded artifact %s/%s", bucket, path)
    return path


async def download_artifact(path: str) -> bytes:
    settings = get_settings()
    bucket = settings.supabase_model_bucket
    client = await get_supabase_client()
    try:
        return await client.storage.from_(bucket).download(path)
    except Exception as exc:
        raise SupabaseError(f"Failed to download '{path}' from bucket '{bucket}': {exc}") from exc


async def list_artifacts(prefix: str | None = None, limit: int = 100) -> list[StorageObject]:
    settings = get_settings()
    bucket = settings.supabase_model_bucket
    client = await get_supabase_client()
    try:
        rows = await client.storage.from_(bucket).list(
            prefix or "",
            {"limit": limit, "sortBy": {"column": "name", "order": "asc"}},
        )
    except Exception as exc:
        raise SupabaseError(f"Failed to list bucket '{bucket}': {exc}") from exc

    objects: list[StorageObject] = []
    for row in rows or []:
        entry = row if isinstance(row, dict) else row.model_dump()
        name = entry.get("name", "")
        objects.append(
            StorageObject(
                name=name,
                bucket=bucket,
                path=f"{prefix}/{name}".strip("/") if prefix else name,
                size=entry.get("metadata", {}).get("size") if isinstance(entry.get("metadata"), dict) else None,
                content_type=(
                    entry.get("metadata", {}).get("mimetype") if isinstance(entry.get("metadata"), dict) else None
                ),
                updated_at=entry.get("updated_at"),
            )
        )
    return objects


async def create_signed_url(path: str, expires_in: int = 3600) -> str:
    """Return a temporary download URL for a private artifact."""
    settings = get_settings()
    bucket = settings.supabase_model_bucket
    client = await get_supabase_client()
    try:
        response = await client.storage.from_(bucket).create_signed_url(path, expires_in)
    except Exception as exc:
        raise SupabaseError(f"Failed to sign '{path}' in bucket '{bucket}': {exc}") from exc

    if isinstance(response, dict):
        url = response.get("signedURL") or response.get("signedUrl") or response.get("signed_url")
    else:
        url = getattr(response, "signed_url", None) or getattr(response, "signedURL", None)
    if not url:
        raise SupabaseError(f"Supabase did not return a signed URL for '{path}'.")
    return url


async def delete_artifacts(paths: list[str]) -> list[str]:
    settings = get_settings()
    bucket = settings.supabase_model_bucket
    client = await get_supabase_client()
    try:
        await client.storage.from_(bucket).remove(paths)
    except Exception as exc:
        raise SupabaseError(f"Failed to delete {paths} from bucket '{bucket}': {exc}") from exc
    return paths


# ------------------------------------------------------------------------------ Auth


async def get_user_from_token(access_token: str) -> dict[str, Any]:
    """Verify a Supabase access token and return the associated user."""
    client = await get_supabase_client()
    try:
        response = await client.auth.get_user(access_token)
    except Exception as exc:
        raise UnauthorizedError("The provided Supabase access token is invalid or expired.") from exc

    user = getattr(response, "user", None)
    if user is None:
        raise UnauthorizedError("The provided Supabase access token does not identify a user.")
    return user.model_dump() if hasattr(user, "model_dump") else dict(user)
