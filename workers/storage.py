"""MinIO (S3) helpers for worker-side artifact persistence.

Buckets are created by the `minio-init` compose service at stack start
(see docker-compose.yml). Workers read from / write into them.
"""

from __future__ import annotations

import json
import os
from datetime import timedelta
from io import BytesIO
from typing import Any, Iterable
from urllib.parse import urlsplit

from minio import Minio

from api.core.config import get_settings


def get_minio_client() -> Minio:
    settings = get_settings()
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_use_ssl,
    )


def get_presign_client() -> Minio:
    """A second `Minio` client, dedicated to minting presigned GET URLs.

    Distinct from `get_minio_client()` for two reasons, both load-bearing:

    1. **Host mismatch.** `get_minio_client()` points at the in-network
       endpoint (`minio:9000` by default) — reachable from inside the
       compose network, unreachable from a browser. Presigned URLs must be
       signed against the *public* host (`settings.minio_public_url`, e.g.
       `https://storage.example.com`) so the link a browser follows matches
       the Host the signature was computed for. SigV4 signs the Host header
       verbatim: `presign_v4` builds `canonical_headers = "host:" +
       url.netloc` (`minio/signer.py:275`). A URL signed for `minio:9000`
       and then fetched with `Host: storage.example.com` (or vice versa)
       fails signature verification — there is no way to reuse one client
       for both endpoints.

    2. **`region=` is mandatory, not cosmetic.** `Minio.get_presigned_url`
       calls `self._get_region(bucket_name)` (`minio/api.py:2476`), and
       `_get_region` (`minio/api.py:481-514`) issues a live
       `GET /{bucket}?location=` request whenever the client has no region
       configured — *not* a cached/local lookup. Because this client's
       endpoint is the public storage host, that call would leave the
       container, cross the public tunnel/CDN, and come back down again on
       every single presign — slow at best, and a hard failure if the public
       edge doesn't proxy arbitrary S3 API verbs (it only needs to proxy
       GET-with-a-valid-signature). Passing `region="us-east-1"` explicitly
       short-circuits `_get_region` at its very first line
       (`if self._base_url.region: return self._base_url.region`) so that
       network call never happens. This is the single highest-risk line in
       the whole presigned-URL feature — dropping it doesn't fail loudly,
       it just gets slow or flaky the first time it runs against real
       infrastructure.

    Raises `RuntimeError` if `MINIO_PUBLIC_URL` is unset — callers (the
    `api/services/download_links.py` policy layer) turn that into a 503,
    since it means presigned downloads simply aren't configured on this
    deployment rather than being a bug.
    """
    settings = get_settings()
    if not settings.minio_public_url:
        raise RuntimeError(
            "MINIO_PUBLIC_URL is not set — cannot mint presigned download "
            "URLs on this deployment."
        )
    parts = urlsplit(settings.minio_public_url)
    return Minio(
        parts.netloc,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=parts.scheme == "https",
        region="us-east-1",
    )


def presigned_get_url(
    client: Minio,
    bucket: str,
    key: str,
    *,
    expires: int,
    filename: str | None = None,
    content_type: str | None = None,
    disposition: str = "attachment",
) -> str:
    """Mint a time-boxed SigV4 GET URL for `bucket/key`.

    `expires` is whole seconds (matches `settings.presigned_url_ttl_seconds`);
    converted to the `timedelta` `Minio.presigned_get_object` actually wants.
    `minio-py` hard-rejects `expires` outside 1 second-7 days
    (`minio/api.py:2473`), so an out-of-range value here raises `ValueError`
    from inside `minio-py` itself rather than silently clamping.

    `response_headers` carries `response-content-disposition` (and
    `response-content-type`, when given) so the signed URL preserves the
    download-as-a-file UX the current `StreamingResponse` endpoints give —
    without this, a browser would try to render a JSONL/GGUF blob inline
    instead of offering it as a download. `disposition="inline"` flips that
    on purpose (view-in-browser, e.g. a seed PDF); the disposition is part
    of the signed query, so it cannot be changed client-side after minting.
    """
    response_headers: dict[str, str] = {}
    if filename is not None:
        response_headers["response-content-disposition"] = f'{disposition}; filename="{filename}"'
    if content_type is not None:
        response_headers["response-content-type"] = content_type
    return client.presigned_get_object(
        bucket_name=bucket,
        object_name=key,
        expires=timedelta(seconds=expires),
        response_headers=response_headers or None,
    )


def s3_uri(bucket: str, key: str) -> str:
    return f"s3://{bucket}/{key}"


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """Split `s3://bucket/key/path` into `(bucket, key)`.

    Raises `ValueError` for any other scheme — we never accept http/file URIs
    because all dataset/model storage lives in MinIO.
    """
    if not uri.startswith("s3://"):
        raise ValueError(f"expected s3:// URI, got: {uri!r}")
    rest = uri[len("s3://") :]
    if "/" not in rest:
        raise ValueError(f"s3 URI missing object key: {uri!r}")
    bucket, key = rest.split("/", 1)
    if not bucket or not key:
        raise ValueError(f"s3 URI has empty bucket or key: {uri!r}")
    return bucket, key


def put_jsonl(
    client: Minio,
    bucket: str,
    key: str,
    rows: Iterable[dict[str, Any]],
) -> int:
    """Upload `rows` as a JSONL object. Returns the byte size written."""
    body = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows).encode("utf-8")
    client.put_object(
        bucket_name=bucket,
        object_name=key,
        data=BytesIO(body),
        length=len(body),
        content_type="application/x-ndjson",
    )
    return len(body)


def get_jsonl(client: Minio, bucket: str, key: str) -> list[dict[str, Any]]:
    """Download a JSONL object and parse it into a list of dicts.

    Skips blank lines. Raises `json.JSONDecodeError` on malformed lines —
    callers should surface that as a clear training failure.
    """
    response = client.get_object(bucket_name=bucket, object_name=key)
    try:
        body = response.read().decode("utf-8")
    finally:
        response.close()
        response.release_conn()
    rows: list[dict[str, Any]] = []
    for line in body.splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def put_directory(
    client: Minio,
    bucket: str,
    key_prefix: str,
    local_dir: str,
) -> tuple[int, int]:
    """Recursively upload `local_dir` under `key_prefix` inside `bucket`.

    Returns `(file_count, total_bytes)`. Used to push a saved LoRA adapter
    directory to MinIO after training.
    """
    file_count = 0
    total_bytes = 0
    base_len = len(local_dir.rstrip(os.sep)) + 1
    for root, _dirs, files in os.walk(local_dir):
        for fname in files:
            full = os.path.join(root, fname)
            rel = full[base_len:].replace(os.sep, "/")
            object_key = f"{key_prefix.rstrip('/')}/{rel}"
            size = os.path.getsize(full)
            with open(full, "rb") as fh:
                client.put_object(
                    bucket_name=bucket,
                    object_name=object_key,
                    data=fh,
                    length=size,
                )
            file_count += 1
            total_bytes += size
    return file_count, total_bytes


def remove_object(client: Minio, bucket: str, key: str) -> None:
    """Delete a single object.

    Used by task-level `except BaseException` handlers to clean up an
    artifact that was uploaded to MinIO before the DB row referencing it
    ever committed — a cancel (SIGTERM -> SystemExit) or an ordinary
    failure landing in that window otherwise leaves the object orphaned
    forever, since nothing in the DB points at it. Callers are expected to
    wrap this in their own try/except: cleanup here is always best-effort
    and must never be allowed to mask the original task failure.
    """
    client.remove_object(bucket_name=bucket, object_name=key)


def remove_prefix(client: Minio, bucket: str, key_prefix: str) -> int:
    """Delete every object whose key starts with `key_prefix`. Returns the count removed.

    MinIO/S3 has no real directories — `put_directory` "uploads a folder" by
    writing one object per file under a shared key prefix, so removing that
    "directory" means enumerating every key under the prefix and deleting
    them one by one; there is no single directory-delete call. Same
    orphan-cleanup use case as `remove_object`: called from a task's
    `except BaseException` handler when the DB row that was meant to
    reference this prefix (a LoRA adapter directory, a GGUF export
    directory, ...) never got committed. Best-effort by convention here too
    — wrap at the call site.

    Lists the full set of keys into memory FIRST, then deletes — rather
    than deleting while `list_objects` is still being iterated. Deleting
    mid-listing is a known footgun against paginated S3-style listings
    (entries can be skipped as pages shift), and it also isn't safe against
    any client whose `list_objects` is backed by something that can't
    tolerate mutation during iteration.
    """
    prefix = key_prefix.rstrip("/") + "/"
    keys = [
        obj.object_name
        for obj in client.list_objects(bucket_name=bucket, prefix=prefix, recursive=True)
    ]
    for key in keys:
        client.remove_object(bucket_name=bucket, object_name=key)
    return len(keys)


__all__ = [
    "get_minio_client",
    "get_presign_client",
    "presigned_get_url",
    "s3_uri",
    "parse_s3_uri",
    "put_jsonl",
    "get_jsonl",
    "put_directory",
    "remove_object",
    "remove_prefix",
]
