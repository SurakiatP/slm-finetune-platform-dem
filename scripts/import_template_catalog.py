"""Verify and register prepared immutable template sources (never ordinary datasets).

Run from the repository root: python -m scripts.import_template_catalog --help.
Existing registrations are verified, never overwritten; changed definitions need
a new curated version. Credentials come from the existing application settings.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from api.models.template import TemplateDatasetVersion
from api.schemas.data_formats import sample_model_for
from api.schemas.enums import TaskType
from api.services import templates_service as service
from workers.storage import parse_s3_uri, s3_uri


def _verified_file(root: Path, relative: str, expected_hash: str) -> bytes:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"Prepared file must remain within its template directory: {relative}")
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected_hash:
        raise ValueError(f"Checksum mismatch: {relative}")
    return payload


def verify_prepared(directory: Path, definition: dict) -> tuple[dict, dict[str, bytes]]:
    """Validate trusted prepared manifest, pinned hashes, counts and canonical rows."""
    if not definition["data_ready"]:
        raise ValueError("This curated template is not approved for registration")
    manifest = json.loads((directory / "manifest.json").read_text())
    if (manifest.get("status"), manifest.get("template_id"), manifest.get("task_type")) != (
        "prepared",
        definition["id"],
        definition["task_type"],
    ):
        raise ValueError("Manifest status, template or task does not match the definition")
    if set(manifest["splits"]) != set(service.ROLES):
        raise ValueError("Exactly train, validation and test splits are required")
    if not all(
        manifest.get("source", {}).get(key)
        for key in ("attribution", "license", "repo", "revision")
    ):
        raise ValueError("Source attribution, license and revision are required")
    license_file = manifest["license_file"]
    _verified_file(directory, license_file["path"], license_file["sha256"])
    model = sample_model_for(TaskType(definition["task_type"]))
    payloads = {}
    for role in service.ROLES:
        split = manifest["splits"][role]
        if (
            split["sha256"] != definition["source_sha256"][role]
            or split["rows"] != definition["split_counts"][role]
            or split.get("shortfall", 0) != 0
        ):
            raise ValueError(f"{role} differs from the approved definition")
        payload = _verified_file(directory, split["path"], split["sha256"])
        rows = payload.splitlines()
        if len(rows) != split["rows"]:
            raise ValueError(f"{role} row count differs from its manifest")
        for line in rows:
            model.model_validate_json(line)
        provenance = _verified_file(directory, split["provenance_path"], split["provenance_sha256"])
        if len(provenance.splitlines()) != len(rows):
            raise ValueError(f"{role} provenance count differs")
        for line in provenance.splitlines():
            if not isinstance(json.loads(line), dict):
                raise ValueError(f"{role} provenance must contain objects")
        if split["max_tokens"] > manifest["tokenizer"]["max_tokens"]:
            raise ValueError(f"{role} has an unresolved token overflow")
        payloads[role] = payload
    return manifest, payloads


async def _verify_registration(client, registration, definition, manifest):
    if (
        not service.registration_matches(definition, registration)
        or registration.manifest_json != manifest
    ):
        raise ValueError("Immutable registration differs; publish a new curated version")
    for role in service.ROLES:
        split = registration.splits_json[role]
        payload = await service.storage_call(
            service.read_object, client, *parse_s3_uri(split["storage_uri"])
        )
        if (
            hashlib.sha256(payload).hexdigest() != split["sha256"]
            or len(payload) != split["size_bytes"]
        ):
            raise ValueError("Registered source object failed integrity verification")


async def import_catalog(
    db, prepared_root: Path, *, template_ids=("tpl-006",), bucket=None, client=None
) -> list[TemplateDatasetVersion]:
    client = client or service.get_minio_client()
    bucket = bucket or service.get_settings().minio_datasets_bucket
    prepared = [
        (
            service.get_definition(tid),
            *verify_prepared(prepared_root / tid, service.get_definition(tid)),
        )
        for tid in template_ids
    ]
    if not await service.storage_call(client.bucket_exists, bucket):
        await service.storage_call(client.make_bucket, bucket)
    registrations = []
    for definition, manifest, payloads in prepared:
        identity = (definition["id"], definition["version"])
        existing = await db.get(TemplateDatasetVersion, identity)
        if existing is not None:
            await _verify_registration(client, existing, definition, manifest)
            registrations.append(existing)
            continue
        registration = TemplateDatasetVersion(
            template_id=identity[0],
            version=identity[1],
            definition_sha256=service.definition_hash(definition),
            manifest_json=manifest,
            splits_json={},
        )
        db.add(registration)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            existing = await db.get(TemplateDatasetVersion, identity)
            if existing is None:
                raise
            await _verify_registration(client, existing, definition, manifest)
            registrations.append(existing)
            continue
        created = []
        commit_started = False
        try:
            splits = {}
            import_id = uuid4()
            for role, payload in payloads.items():
                key = f"{service.SOURCE_PREFIX}{identity[0]}/{identity[1]}/{import_id}/{role}.jsonl"
                created.append(key)
                await service.storage_call(
                    client.put_object,
                    bucket,
                    key,
                    BytesIO(payload),
                    len(payload),
                    content_type="application/x-ndjson",
                )
                stored = await service.storage_call(service.read_object, client, bucket, key)
                if hashlib.sha256(stored).digest() != hashlib.sha256(payload).digest():
                    raise ValueError("Uploaded source object failed integrity verification")
                splits[role] = dict(
                    storage_uri=s3_uri(bucket, key),
                    sha256=hashlib.sha256(payload).hexdigest(),
                    num_samples=manifest["splits"][role]["rows"],
                    size_bytes=len(payload),
                )
            registration.splits_json = splits
            await db.flush()
            commit_started = True
            await db.commit()
            registrations.append(registration)
        except BaseException:
            await db.rollback()
            if not commit_started:
                for key in created:
                    try:
                        await service.storage_call(client.remove_object, bucket, key)
                    except Exception:
                        service.log.exception("Source cleanup failed for %s", key)
            raise
    return registrations


async def _main(args):
    from api.core.database import AsyncSessionLocal, engine

    try:
        async with AsyncSessionLocal() as db:
            rows = await import_catalog(
                db,
                args.prepared_root,
                template_ids=args.template_ids or ("tpl-006",),
                bucket=args.bucket,
            )
            for row in rows:
                print(f"Verified registration {row.template_id} version={row.version}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prepared-root", type=Path, default=Path("data/template-catalog/prepared")
    )
    parser.add_argument("--template-id", action="append", dest="template_ids")
    parser.add_argument("--bucket", help="Source bucket; defaults to MINIO_DATASETS_BUCKET")
    asyncio.run(_main(parser.parse_args()))
