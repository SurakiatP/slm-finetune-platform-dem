"""Curated catalog, real ratings and atomic per-project materialization."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_engine.data_gen.holdout_split import split_rows
from ai_engine.training.unsloth_trainer import _chat_template_for
from api.core import request_context
from api.core.auth import CurrentUser
from api.core.config import get_settings
from api.models.dataset import Dataset
from api.models.project import Project
from api.models.template import TemplateDatasetVersion, TemplateRating, TemplateUse
from api.schemas.enums import DatasetSource, JobStatus, TaskType
from api.schemas.projects import ProjectCreate, ProjectResponse
from api.schemas.responses import Page
from api.schemas.templates import TemplateRatingResponse, TemplateResponse
from api.schemas.training import ManualTrainingConfig
from api.services import audit_service
from workers.storage import get_minio_client, parse_s3_uri, s3_uri

log = logging.getLogger(__name__)
ROLES = ("train", "validation", "test")
COPY_PREFIX = "template-copies/"
SOURCE_PREFIX = "template-sources/"
# Shared by materializers; exclusive only during the operator orphan sweep.
CLEANUP_LOCK = 810625925


def load_catalog() -> list[dict[str, Any]]:
    return json.loads((Path(__file__).parents[1] / "template_catalog.json").read_text())


def get_definition(template_id: str) -> dict[str, Any]:
    for definition in load_catalog():
        if definition["id"] == template_id:
            return definition
    raise HTTPException(404, "Template not found")


def definition_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def authenticated(user: CurrentUser | None) -> CurrentUser:
    if user is None:
        raise HTTPException(401, "authentication required")
    return user


def registration_matches(definition: dict, registration: TemplateDatasetVersion | None) -> bool:
    return bool(
        definition["data_ready"]
        and registration is not None
        and registration.version == definition["version"]
        and registration.definition_sha256 == definition_hash(definition)
        and all(
            registration.splits_json.get(role, {}).get("sha256")
            == definition["source_sha256"][role]
            and registration.splits_json[role].get("num_samples")
            == definition["split_counts"][role]
            for role in ROLES
        )
    )


async def list_templates(
    db: AsyncSession,
    *,
    user: CurrentUser | None,
    limit: int = 50,
    offset: int = 0,
    category: str | None = None,
    featured: bool | None = None,
    search: str | None = None,
    sort: str = "popular",
    include_unavailable: bool = False,
) -> Page[TemplateResponse]:
    user = authenticated(user)
    registrations = {
        (r.template_id, r.version): r
        for r in (await db.scalars(select(TemplateDatasetVersion))).all()
    }
    forks = dict(
        (
            await db.execute(
                select(TemplateUse.template_id, func.count()).group_by(TemplateUse.template_id)
            )
        ).all()
    )
    ratings = {
        row[0]: (float(row[1]), row[2])
        for row in (
            await db.execute(
                select(
                    TemplateRating.template_id, func.avg(TemplateRating.rating), func.count()
                ).group_by(TemplateRating.template_id)
            )
        ).all()
    }
    mine = dict(
        (
            await db.execute(
                select(TemplateRating.template_id, TemplateRating.rating).where(
                    TemplateRating.user_id == user.id
                )
            )
        ).all()
    )
    items = []
    for definition in load_catalog():
        registration = registrations.get((definition["id"], definition["version"]))
        available = registration_matches(definition, registration)
        if not available and not include_unavailable:
            continue
        if category is not None and definition["category"] != category:
            continue
        if featured is not None and definition["featured"] != featured:
            continue
        if (
            search
            and search.casefold()
            not in " ".join(
                [
                    definition["name"],
                    definition["description"],
                    definition["long_description"],
                    *definition["tags"],
                ]
            ).casefold()
        ):
            continue
        rating, count = ratings.get(definition["id"], (None, 0))
        counts = definition.get("split_counts", dict.fromkeys(ROLES, 0))
        items.append(
            TemplateResponse(
                **{
                    key: definition[key]
                    for key in (
                        "id",
                        "version",
                        "name",
                        "description",
                        "long_description",
                        "category",
                        "task_type",
                        "base_model",
                        "prompt",
                        "epochs",
                        "learning_rate",
                        "author",
                        "tags",
                        "featured",
                    )
                },
                dataset_size=counts["train"],
                available=available,
                unavailable_reason=None
                if available
                else definition.get(
                    "unavailable_reason",
                    "Prepared data has not been registered for this definition/version.",
                ),
                split_counts=counts,
                source_attribution=registration.manifest_json.get("source")
                if registration
                else None,
                forks=forks.get(definition["id"], 0),
                rating=rating,
                rating_count=count,
                my_rating=mine.get(definition["id"]),
            )
        )
    if sort == "rating":
        items.sort(key=lambda item: (-(item.rating or 0), -item.rating_count, item.id))
    else:
        items.sort(key=lambda item: (-item.forks, -(item.rating or 0), item.id))
    return Page(items=items[offset : offset + limit], total=len(items), limit=limit, offset=offset)


async def rate_template(
    db: AsyncSession, template_id: str, rating: int, user: CurrentUser | None
) -> TemplateRatingResponse:
    user = authenticated(user)
    get_definition(template_id)
    if type(rating) is not int or not 1 <= rating <= 5:
        raise HTTPException(422, "rating must be an integer from 1 to 5")
    if not await db.scalar(
        select(TemplateUse.id)
        .where(TemplateUse.template_id == template_id, TemplateUse.user_id == user.id)
        .limit(1)
    ):
        raise HTTPException(403, "Create a project from this template before rating it")
    # Native upsert serializes concurrent edits without duplicate votes.
    if db.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    stmt = insert(TemplateRating).values(template_id=template_id, user_id=user.id, rating=rating)
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=["template_id", "user_id"],
            set_={"rating": rating, "updated_at": func.now()},
        )
    )
    await db.commit()
    average, count = (
        await db.execute(
            select(func.avg(TemplateRating.rating), func.count()).where(
                TemplateRating.template_id == template_id
            )
        )
    ).one()
    current = await db.scalar(
        select(TemplateRating.rating).where(
            TemplateRating.template_id == template_id, TemplateRating.user_id == user.id
        )
    )
    return TemplateRatingResponse(rating=float(average), rating_count=count, my_rating=current)


def sample_train(rows: list[dict], task: TaskType, count: int, seed: int) -> list[dict]:
    if not 1 <= count <= len(rows):
        raise HTTPException(422, f"train_sample_count must be between 1 and {len(rows)}")
    selected = rows if count == len(rows) else split_rows(rows, task, count, random.Random(seed))[1]
    if len(selected) != count:
        raise HTTPException(422, "Unable to select the exact requested training count")
    return selected


def read_object(client, bucket: str, key: str) -> bytes:
    response = client.get_object(bucket, key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


async def storage_call(function, *args, **kwargs):
    """Finish in-flight writes before cancellation can trigger object cleanup."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


async def _previous_use(
    db: AsyncSession, user_id: str, key: str, request_hash: str
) -> ProjectResponse | None:
    previous = await db.scalar(
        select(TemplateUse).where(
            TemplateUse.user_id == user_id, TemplateUse.idempotency_key == key
        )
    )
    if previous is None:
        return None
    if previous.request_sha256 != request_hash:
        raise HTTPException(409, "Idempotency-Key was already used with a different request")
    return ProjectResponse.model_validate(previous.response_json)


async def create_template_project(
    db: AsyncSession, body: ProjectCreate, user: CurrentUser | None, idempotency_key: str | None
) -> ProjectResponse:
    user = authenticated(user)
    if not idempotency_key or not idempotency_key.strip() or len(idempotency_key) > 200:
        raise HTTPException(
            422, "Template creation requires an Idempotency-Key of 1-200 characters"
        )
    request_hash = definition_hash(body.model_dump(mode="json"))
    previous = await _previous_use(db, user.id, idempotency_key, request_hash)
    if previous is not None:
        return previous
    project_id = uuid4()
    usage = TemplateUse(
        user_id=user.id,
        template_id=body.template_id,
        template_version=body.template_version or "1",
        idempotency_key=idempotency_key,
        request_sha256=request_hash,
        project_id=project_id,
        response_json={},
    )
    db.add(usage)
    try:
        # The unique user/key insert blocks concurrent retries until commit/rollback.
        await db.flush()
    except IntegrityError:
        await db.rollback()
        previous = await _previous_use(db, user.id, idempotency_key, request_hash)
        if previous is not None:
            return previous
        raise HTTPException(409, "Template creation conflicts with an existing request") from None

    client = None
    owned_objects: list[tuple[str, str]] = []
    commit_started = False
    try:
        if db.bind.dialect.name == "postgresql":
            await db.execute(select(func.pg_advisory_xact_lock_shared(CLEANUP_LOCK)))
        definition = get_definition(body.template_id)
        version = body.template_version or definition["version"]
        if version != definition["version"]:
            raise HTTPException(409, "Template version is no longer available for new projects")
        registration = await db.get(TemplateDatasetVersion, (body.template_id, version))
        if not registration_matches(definition, registration):
            raise HTTPException(
                409,
                definition.get(
                    "unavailable_reason", "Template data is not registered for this version"
                ),
            )
        if body.task_type.value != definition["task_type"]:
            raise HTTPException(422, "Project task_type must match its template")
        overrides = (
            body.template_overrides.model_dump(exclude_none=True) if body.template_overrides else {}
        )
        config = ManualTrainingConfig(
            num_train_epochs=overrides.get("epochs", definition["epochs"]),
            learning_rate=overrides.get("learning_rate", definition["learning_rate"]),
        )
        base_model = overrides.get("base_model", definition["base_model"])
        count = overrides.get("train_sample_count", definition["split_counts"]["train"])
        if count > definition["split_counts"]["train"]:
            raise HTTPException(422, "train_sample_count exceeds available training rows")
        seed = overrides.get("sampling_seed", 42)
        snapshot = dict(
            template_id=body.template_id,
            template_version=version,
            definition=definition,
            base_model=base_model,
            system_prompt=overrides.get("system_prompt", definition["prompt"]),
            chat_template=_chat_template_for(base_model),
            manual_config=config.model_dump(mode="json"),
            train_sample_count=count,
            sampling_seed=seed,
        )
        project = Project(
            id=project_id,
            name=body.name,
            description=body.description,
            task_type=body.task_type,
            external_project_id=body.external_project_id,
            owner_id=user.id,
        )
        db.add(project)
        await db.flush()
        client = get_minio_client()
        bucket = get_settings().minio_datasets_bucket
        for role in ROLES:
            source = registration.splits_json[role]
            source_bucket, source_key = parse_s3_uri(source["storage_uri"])
            payload = await storage_call(read_object, client, source_bucket, source_key)
            if (
                hashlib.sha256(payload).hexdigest() != source["sha256"]
                or len(payload) != source["size_bytes"]
            ):
                raise HTTPException(503, "Template source integrity check failed")
            if len(payload.splitlines()) != source["num_samples"]:
                raise HTTPException(503, "Template source row count changed")
            num_samples = source["num_samples"]
            if role == "train" and count != num_samples:
                rows = [json.loads(line) for line in payload.splitlines()]
                rows = sample_train(rows, body.task_type, count, seed)
                payload = b"".join(
                    (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
                    for row in rows
                )
                num_samples = count
            dataset_id = uuid4()
            key = f"{COPY_PREFIX}{usage.id}/{role}/{dataset_id}.jsonl"
            owned_objects.append((bucket, key))
            await storage_call(
                client.put_object,
                bucket,
                key,
                BytesIO(payload),
                len(payload),
                content_type="application/x-ndjson",
            )
            digest = hashlib.sha256(payload).hexdigest()
            db.add(
                Dataset(
                    id=dataset_id,
                    project_id=project.id,
                    owner_id=user.id,
                    name=f"{body.name[:180]} {role}",
                    task_type=body.task_type,
                    source=DatasetSource.UPLOADED,
                    status=JobStatus.COMPLETED,
                    num_samples=num_samples,
                    storage_uri=s3_uri(bucket, key),
                    size_bytes=len(payload),
                    generation_metadata=dict(
                        template_id=body.template_id,
                        template_version=version,
                        role=role,
                        sha256=digest,
                        sampling_seed=seed,
                    ),
                )
            )
            snapshot[f"{role}_dataset_id"] = str(dataset_id)
            snapshot[f"{role}_sha256"] = digest
        project.template_snapshot = snapshot
        usage.template_version = version
        audit_service.record(
            db,
            action="project.create",
            resource_type="project",
            resource_id=str(project.id),
            project_id=project.id,
            actor_id=user.id,
            request_id=request_context.current_request_id(),
            metadata={
                "name": project.name,
                "task_type": project.task_type.value,
                "template_id": body.template_id,
                "template_version": version,
            },
        )
        await db.flush()
        await db.refresh(project)
        response = ProjectResponse.model_validate(project)
        usage.response_json = response.model_dump(mode="json")
        # If commit loses its connection, success is uncertain: keep the objects.
        # The reference-aware orphan sweep can safely decide later.
        commit_started = True
        await db.commit()
        return response
    except BaseException as exc:
        await db.rollback()
        if client is not None and not commit_started:
            for bucket, key in owned_objects:
                try:
                    await storage_call(client.remove_object, bucket, key)
                except Exception:
                    log.exception("Template copy cleanup failed for %s", key)
        if isinstance(exc, IntegrityError):
            raise HTTPException(
                409, "Project conflicts with an existing external_project_id"
            ) from None
        if isinstance(exc, (HTTPException, asyncio.CancelledError)):
            raise
        log.exception("Template materialization failed")
        raise HTTPException(
            503, "Template materialization failed; retry with the same Idempotency-Key"
        ) from None
