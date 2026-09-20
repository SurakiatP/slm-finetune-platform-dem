"""Dry-run by default: remove old unreferenced per-project template copies only."""

from __future__ import annotations

import argparse
import asyncio
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from api.models.dataset import Dataset
from api.models.template import TemplateDatasetVersion
from api.services import templates_service as service
from workers.storage import s3_uri

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_COPY_KEY = re.compile(rf"template-copies/{_UUID}/(?:train|validation|test)/{_UUID}\.jsonl\Z")


async def cleanup_objects(
    db, *, client=None, bucket=None, minimum_age_hours=24, apply=False
) -> list[str]:
    if minimum_age_hours < 1:
        raise ValueError("Minimum object age must be at least one hour")
    client = client or service.get_minio_client()
    bucket = bucket or service.get_settings().minio_datasets_bucket
    if db.bind.dialect.name != "postgresql":
        raise ValueError("Cleanup requires Postgres coordination with active materializers")
    # No request can be copying while this transaction examines DB references.
    await db.execute(select(func.pg_advisory_xact_lock(service.CLEANUP_LOCK)))
    cutoff = datetime.now(UTC) - timedelta(hours=minimum_age_hours)
    references = set(
        (
            await db.scalars(select(Dataset.storage_uri).where(Dataset.storage_uri.is_not(None)))
        ).all()
    )
    for registration in (await db.scalars(select(TemplateDatasetVersion))).all():
        references.update(split["storage_uri"] for split in registration.splits_json.values())
    objects = await service.storage_call(
        lambda: list(client.list_objects(bucket, prefix=service.COPY_PREFIX, recursive=True))
    )
    candidates = []
    for obj in objects:
        modified = obj.last_modified
        if not _COPY_KEY.fullmatch(obj.object_name) or modified is None or modified.tzinfo is None:
            continue
        if modified >= cutoff or s3_uri(bucket, obj.object_name) in references:
            continue
        candidates.append(obj.object_name)
        if apply:
            await service.storage_call(client.remove_object, bucket, obj.object_name)
    await db.commit()
    return candidates


async def _main(args):
    from api.core.database import AsyncSessionLocal, engine

    try:
        async with AsyncSessionLocal() as db:
            keys = await cleanup_objects(
                db, bucket=args.bucket, minimum_age_hours=args.minimum_age_hours, apply=args.apply
            )
            for key in keys:
                print(f"{'Deleted' if args.apply else 'Would delete'} {key}")
            print(f"{len(keys)} orphan copies; {'applied' if args.apply else 'dry run'}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket")
    parser.add_argument("--minimum-age-hours", type=int, default=24)
    parser.add_argument("--apply", action="store_true")
    asyncio.run(_main(parser.parse_args()))
