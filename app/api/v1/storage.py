"""Supabase Storage endpoints for model artifacts.

Artifacts are stored in the bucket configured by ``SUPABASE_MODEL_BUCKET``. All routes
require a valid Supabase access token; swap ``CurrentUser`` for ``OptionalUser`` in
:mod:`app.api.deps` if you want the bucket to be world-readable instead.
"""

from __future__ import annotations

from fastapi import APIRouter, File, Query, UploadFile, status
from fastapi.responses import Response

from app.api.deps import CurrentUser
from app.core.exceptions import InvalidModelInputError
from app.schemas.storage import SignedUrlResponse, StorageObject, UploadResponse
from app.services import supabase_client

router = APIRouter(prefix="/storage", tags=["storage"])


def _safe_artifact_path(path: str) -> str:
    """Reject paths that would escape the bucket's logical prefix."""
    cleaned = path.strip().lstrip("/")
    if not cleaned or ".." in cleaned.split("/"):
        raise InvalidModelInputError("Artifact path must be a relative path inside the bucket.")
    return cleaned


@router.get("/objects", response_model=list[StorageObject], summary="List artifacts")
async def list_objects(
    user: CurrentUser,
    prefix: str | None = Query(default=None, description="Folder prefix, e.g. 'demand-forecast'."),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[StorageObject]:
    return await supabase_client.list_artifacts(prefix, limit)


@router.post(
    "/objects/{path:path}",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload an artifact",
)
async def upload_object(
    path: str,
    user: CurrentUser,
    file: UploadFile = File(...),
) -> UploadResponse:
    artifact_path = _safe_artifact_path(path)
    data = await file.read()
    await supabase_client.upload_artifact(
        artifact_path,
        data,
        content_type=file.content_type or "application/octet-stream",
    )
    return UploadResponse(bucket=supabase_client.model_bucket(), path=artifact_path)


@router.get("/objects/{path:path}", summary="Download an artifact")
async def download_object(path: str, user: CurrentUser) -> Response:
    artifact_path = _safe_artifact_path(path)
    data = await supabase_client.download_artifact(artifact_path)
    return Response(content=data, media_type="application/octet-stream")


@router.get(
    "/objects/{path:path}/signed-url",
    response_model=SignedUrlResponse,
    summary="Create a temporary download URL",
)
async def sign_object(
    path: str,
    user: CurrentUser,
    expires_in: int = Query(default=3600, ge=60, le=604800),
) -> SignedUrlResponse:
    artifact_path = _safe_artifact_path(path)
    url = await supabase_client.create_signed_url(artifact_path, expires_in)
    return SignedUrlResponse(path=artifact_path, url=url, expires_in=expires_in)


@router.delete("/objects", response_model=list[str], summary="Delete artifacts")
async def delete_objects(
    user: CurrentUser,
    paths: list[str] = Query(min_length=1, description="Repeat for each artifact to delete."),
) -> list[str]:
    return await supabase_client.delete_artifacts([_safe_artifact_path(path) for path in paths])
