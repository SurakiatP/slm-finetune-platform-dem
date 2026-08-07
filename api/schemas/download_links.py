"""Response schemas for the presigned-download-URL endpoints (Wave 1b).

These are additive alongside the existing streaming `/download` endpoints —
see `api/services/download_links.py` for the policy layer that builds them.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from api.schemas.enums import ArtifactFormat


class DatasetDownloadUrlResponse(BaseModel):
    """Body of `GET /api/v1/datasets/{id}/download-url`."""

    model_config = ConfigDict(extra="forbid")

    url: str
    filename: str
    content_type: str
    expires_at: datetime
    expires_in: int = Field(description="Seconds from mint time until `url` stops working.")


class ModelDownloadFile(BaseModel):
    """One object inside a `ModelDownloadUrlResponse.files` listing."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(description="Full MinIO object key (bucket-relative).")
    name: str = Field(description="Basename, suitable for a client-side Content-Disposition.")
    size_bytes: int
    url: str


class ModelDownloadUrlResponse(BaseModel):
    """Body of `GET /api/v1/models/{id}/download-url`.

    `gguf` always returns exactly one file (the first `.gguf` object under
    the export prefix, matching `_first_gguf_object`'s existing selection
    rule). `safetensors`/`lora` are multi-file directories, so `files` can
    contain many entries — capped at
    `api.services.download_links.MAX_LISTING_OBJECTS`; `truncated=True`
    means the prefix held more objects than the cap and the response only
    covers the first `MAX_LISTING_OBJECTS` of them.
    """

    model_config = ConfigDict(extra="forbid")

    format: ArtifactFormat
    files: list[ModelDownloadFile]
    expires_at: datetime
    expires_in: int
    truncated: bool = False


__all__ = [
    "DatasetDownloadUrlResponse",
    "ModelDownloadFile",
    "ModelDownloadUrlResponse",
]
