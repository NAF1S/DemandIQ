"""Schemas for Supabase Storage endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class StorageObject(BaseModel):
    name: str
    bucket: str
    path: str | None = None
    size: int | None = None
    content_type: str | None = None
    updated_at: datetime | None = None


class SignedUrlResponse(BaseModel):
    path: str
    url: str
    expires_in: int


class UploadResponse(BaseModel):
    bucket: str
    path: str
