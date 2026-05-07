"""MinIO (S3) helpers for worker-side artifact persistence.

Buckets are created by the `minio-init` compose service at stack start
(see docker-compose.yml). Workers read from / write into them.
"""

from __future__ import annotations

import json
import os
from io import BytesIO
from typing import Any, Iterable

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


__all__ = [
    "get_minio_client",
    "s3_uri",
    "parse_s3_uri",
    "put_jsonl",
    "get_jsonl",
    "put_directory",
]
