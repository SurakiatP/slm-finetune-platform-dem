"""Policy layer for presigned MinIO download URLs (Wave 1b).

Mints time-boxed SigV4 GET URLs (`workers.storage.presigned_get_url`) so
large dataset/model artifacts stop streaming through the API process. This
module owns everything that isn't pure S3-client mechanics:

  * TTL — sourced from `settings.presigned_url_ttl_seconds`, never
    hardcoded here.
  * Ownership — every mint goes through `api.services.ownership`'s
    `assert_*_access` first, same 404-not-403 contract as every other
    resource endpoint.
  * Audit — one row per mint, written and committed before the response
    goes out, mirroring the existing `dataset.download` /
    `model.download` audit points in `datasets_service.py` /
    `model_service.py`.
  * Multi-file listing (`safetensors`/`lora` exports) — capped at
    `MAX_LISTING_OBJECTS` so a pathological export prefix can't turn one
    response into an unbounded JSON body of signed URLs.
  * A clean 503 when `MINIO_PUBLIC_URL` isn't configured, instead of a
    stack trace the first time someone hits the endpoint.

This module is API-only: nothing on the Celery worker boot path imports it
(only `api/routers/datasets.py` and `api/routers/models.py` do), so it is
free to pull in `api.services.ownership` (and, transitively,
`api.core.auth`/PyJWT) without tripping the worker-import-surface guard in
`tests/unit/test_worker_import_surface.py`.

**Import-by-name trap for test authors**: the line below is
`from workers.storage import get_presign_client`, which binds a
module-local name `download_links.get_presign_client` at import time.
Patching `workers.storage.get_presign_client` *after* that import does NOT
reach this module's alias — you have to monkeypatch
`api.services.download_links.get_presign_client` (and `...get_minio_client`)
directly. This is the exact same footgun `tests/unit/test_dataset_status.py`
documents at lines 191 and 277 for `datasets_service`/`data_generation`;
losing an hour to it twice in one codebase is one time too many.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.core import request_context
from api.core.auth import CurrentUser
from api.core.config import get_settings
from api.schemas.download_links import (
    DatasetDownloadUrlResponse,
    ModelDownloadFile,
    ModelDownloadUrlResponse,
)
from api.schemas.enums import ArtifactFormat
from api.services import audit_service, ownership
from api.services.model_service import _first_gguf_object, _project_id_for_artifact
from workers.storage import get_minio_client, get_presign_client, parse_s3_uri, presigned_get_url

# Hard cap on how many objects a single safetensors/lora listing mints URLs
# for. Today's leak (model_service.py:306-311) tells the caller to go list
# the prefix themselves via the MinIO API with no bound at all; replacing
# that with an endpoint that mints an unbounded number of signed URLs into
# one JSON response would just move the same unboundedness problem one hop
# over. 200 is comfortably above any real LoRA adapter (a handful of files)
# or merged-weights safetensors export (shards + config, still low tens).
MAX_LISTING_OBJECTS = 200


def _get_presign_client_or_503():
    """`get_presign_client()`, translated into the HTTP shape callers want.

    `get_presign_client()` raises a plain `RuntimeError` when
    `MINIO_PUBLIC_URL` is unset (see its docstring in `workers/storage.py`)
    — that's the right exception type for a library function with no
    notion of HTTP, but every caller in this module needs it as a 503, so
    it's centralised here rather than repeated at each mint site.
    """
    try:
        return get_presign_client()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


def _expires(ttl_seconds: int) -> tuple[datetime, int]:
    return datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds), ttl_seconds


# ---- datasets ---------------------------------------------------------------


async def mint_dataset_download_url(
    db: AsyncSession, dataset_id: UUID, user: CurrentUser | None
) -> DatasetDownloadUrlResponse:
    """Mint a presigned GET URL for a dataset's stored object.

    Sources the object from `Dataset.storage_uri`, falling back to
    `generation_metadata["pdf_uri"]` (set at `datasets_service.py:812-825`)
    when `storage_uri` is null. That fallback closes a real gap for free:
    PDF-seeded datasets have `storage_uri = None` and 409 on the existing
    `/download` endpoint today, so an uploaded PDF has no download surface
    at all — this endpoint gives it one.

    Checked in this order: presign-client availability (a deployment-wide
    503, independent of any particular dataset — fail fast before touching
    the DB), then ownership (404, not 403 — see `ownership.py`'s module
    docstring for why), then whether there's anything to download yet
    (409). The audit row is written, and the transaction committed, before
    the URL is returned — mirroring `datasets_service.download_dataset`'s
    `dataset.download` audit point at `datasets_service.py:187`.
    """
    presign_client = _get_presign_client_or_503()

    ds = await ownership.assert_dataset_access(db, dataset_id, user)

    pdf_uri = (ds.generation_metadata or {}).get("pdf_uri")
    is_pdf_fallback = not ds.storage_uri and bool(pdf_uri)
    uri = ds.storage_uri or pdf_uri
    if not uri:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Dataset {dataset_id} has no rows yet (still generating?)",
        )

    bucket, key = parse_s3_uri(uri)
    filename = f"{ds.name}.pdf" if is_pdf_fallback else f"{ds.name}.jsonl"
    content_type = "application/pdf" if is_pdf_fallback else "application/x-ndjson"

    settings = get_settings()
    url = presigned_get_url(
        presign_client,
        bucket,
        key,
        expires=settings.presigned_url_ttl_seconds,
        filename=filename,
        content_type=content_type,
    )

    audit_service.record(
        db,
        action="dataset.download_url",
        resource_type="dataset",
        resource_id=str(ds.id),
        project_id=ds.project_id,
        actor_id=request_context.current_user_id(),
        request_id=request_context.current_request_id(),
        metadata={"filename": filename, "pdf_fallback": is_pdf_fallback},
    )
    await db.commit()

    expires_at, expires_in = _expires(settings.presigned_url_ttl_seconds)
    return DatasetDownloadUrlResponse(
        url=url,
        filename=filename,
        content_type=content_type,
        expires_at=expires_at,
        expires_in=expires_in,
    )


# ---- models -------------------------------------------------------------


async def mint_model_download_url(
    db: AsyncSession, model_id: UUID, fmt: ArtifactFormat, user: CurrentUser | None
) -> ModelDownloadUrlResponse:
    """Mint one or more presigned GET URLs for a model export.

    `gguf` reuses `model_service._first_gguf_object` and returns a single
    file. `safetensors`/`lora` are multi-file directories on MinIO — this
    is what closes today's hole (`model_service.py:306-311`): those two
    formats currently 400 with a message telling the caller to go fetch
    objects from MinIO directly. Here they're listed (via the *internal*
    `get_minio_client()` — listing never needs to leave the compose
    network, only the individual GET URLs do) and one presigned URL is
    minted per object, capped at `MAX_LISTING_OBJECTS`.

    One audit row for the whole mint (`model.download_url`, metadata
    `{format, file_count}`) — not one per file, since a multi-hundred-file
    listing would otherwise flood the audit table for what is, from the
    caller's perspective, a single action.
    """
    presign_client = _get_presign_client_or_503()

    artifact = await ownership.assert_model_access(db, model_id, user)

    if fmt is ArtifactFormat.GGUF:
        uri = artifact.gguf_uri
    elif fmt is ArtifactFormat.SAFETENSORS:
        uri = artifact.safetensors_uri
    else:
        uri = artifact.lora_adapter_uri

    if not uri:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Model {model_id} has not been exported as {fmt.value}. "
                f"POST /api/v1/models/{model_id}/export first."
            ),
        )

    bucket, prefix = parse_s3_uri(uri)
    settings = get_settings()
    expires_at, expires_in = _expires(settings.presigned_url_ttl_seconds)
    truncated = False

    if fmt is ArtifactFormat.GGUF:
        target = _first_gguf_object(bucket, prefix)
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"No GGUF file found at {uri}",
            )
        url = presigned_get_url(
            presign_client,
            bucket,
            target,
            expires=expires_in,
            filename=f"{artifact.name}.gguf",
            content_type="application/octet-stream",
        )
        files = [
            ModelDownloadFile(
                key=target,
                name=f"{artifact.name}.gguf",
                size_bytes=_object_size(bucket, target),
                url=url,
            )
        ]
    else:
        minio = get_minio_client()
        listing_prefix = prefix.rstrip("/") + "/"
        objects = list(
            minio.list_objects(bucket_name=bucket, prefix=listing_prefix, recursive=True)
        )
        if len(objects) > MAX_LISTING_OBJECTS:
            truncated = True
            objects = objects[:MAX_LISTING_OBJECTS]

        files = [
            ModelDownloadFile(
                key=obj.object_name,
                name=obj.object_name.rsplit("/", 1)[-1],
                size_bytes=obj.size or 0,
                url=presigned_get_url(
                    presign_client,
                    bucket,
                    obj.object_name,
                    expires=expires_in,
                    filename=obj.object_name.rsplit("/", 1)[-1],
                ),
            )
            for obj in objects
        ]

    audit_service.record(
        db,
        action="model.download_url",
        resource_type="model",
        resource_id=str(artifact.id),
        project_id=await _project_id_for_artifact(db, artifact),
        actor_id=request_context.current_user_id(),
        request_id=request_context.current_request_id(),
        metadata={"format": fmt.value, "file_count": len(files)},
    )
    await db.commit()

    return ModelDownloadUrlResponse(
        format=fmt,
        files=files,
        expires_at=expires_at,
        expires_in=expires_in,
        truncated=truncated,
    )


def _object_size(bucket: str, key: str) -> int:
    """`stat_object` for a single known key. Best-effort: a 0 fallback here
    is a cosmetic size-display miss, not a correctness problem — the URL
    itself is still valid regardless of whether the size lookup succeeds.
    """
    try:
        return get_minio_client().stat_object(bucket_name=bucket, object_name=key).size or 0
    except Exception:  # noqa: BLE001
        return 0


__all__ = ["MAX_LISTING_OBJECTS", "mint_dataset_download_url", "mint_model_download_url"]
