"""T7 — `GET /api/v1/datasets/{id}/insights`.

Exercises `api.services.dataset_insights.get_dataset_insights` directly
against an in-memory aiosqlite DB + a `fake_minio`-backed JSONL object,
mirroring `tests/unit/test_dataset_upload_endpoint.py` /
`tests/unit/test_dataset_owner_access_matrix.py`'s harness shape.

Covers:
  1. SDG dataset with a stored `generation_metadata["insights"]` blob ->
     judge fields populated, score blended with the judge weighted mean.
  2. Uploaded dataset with `generation_metadata=None` -> 200, judge/counts
     null, row stats still present.
  3. `storage_uri=None` (still generating) -> 409.
  4. Malformed stored insights blob -> 200 with judge/judge_by_key/counts
     all null (never a 500 on a bad upstream shape).
  5. More than `INSIGHTS_SCAN_LIMIT` rows -> `scanned_rows == 5000`,
     `scan_truncated is True`.
  6. A near-duplicate (paraphrase) pair is caught by the MinHash pass even
     though the two rows are not byte-identical.
  7. Ownership denial -- non-owner gets 403 (matches
     `test_dataset_owner_access_matrix.py`'s pattern for the sibling
     `preview` endpoint).
"""

from __future__ import annotations

import json
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from api.core.auth import CurrentUser
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.project import Project
from api.schemas.enums import DatasetSource, JobStatus, TaskType
from api.services import dataset_insights as di_module
from workers.storage import s3_uri


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


USER_A = CurrentUser(id="user-a-sub", email="a@example.com")
USER_B = CurrentUser(id="user-b-sub", email="b@example.com")

_BUCKET = "datasets"


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def project(db: AsyncSession) -> Project:
    proj = Project(
        id=uuid4(),
        name="insights-proj",
        task_type=TaskType.CLASSIFICATION,
        owner_id=USER_A.id,
    )
    db.add(proj)
    await db.flush()
    return proj


@pytest.fixture
def patch_minio(monkeypatch: pytest.MonkeyPatch, fake_minio):
    # Import-by-name trap (see test_dataset_owner_access_matrix.py's docstring):
    # dataset_insights.py did `from workers.storage import get_minio_client`,
    # so the patch has to target its own copy of the name.
    monkeypatch.setattr(di_module, "get_minio_client", lambda: fake_minio)
    return fake_minio


def _put_rows(fake_minio, rows: list[dict], key: str) -> str:
    body = "\n".join(json.dumps(r) for r in rows).encode("utf-8") + b"\n"
    fake_minio.put_object(_BUCKET, key, BytesIO(body), length=len(body))
    return s3_uri(_BUCKET, key)


async def _make_dataset(
    db: AsyncSession,
    project: Project,
    *,
    rows: list[dict] | None,
    fake_minio,
    key: str,
    task_type: TaskType = TaskType.CLASSIFICATION,
    source: DatasetSource = DatasetSource.SEED,
    owner_id: str | None = USER_A.id,
    generation_metadata: dict | None = None,
    num_samples: int | None = None,
) -> Dataset:
    storage_uri = _put_rows(fake_minio, rows, key) if rows is not None else None
    dataset = Dataset(
        id=uuid4(),
        project_id=project.id,
        owner_id=owner_id,
        name=f"ds-{key}",
        task_type=task_type,
        source=source,
        status=JobStatus.COMPLETED,
        num_samples=num_samples if num_samples is not None else (len(rows) if rows else 0),
        storage_uri=storage_uri,
        generation_metadata=generation_metadata,
    )
    db.add(dataset)
    await db.flush()
    await db.commit()
    return dataset


def _classification_rows(n: int, *, label: str = "billing") -> list[dict]:
    return [
        {"text": f"row number {i} about {label} issues needing attention", "label": label}
        for i in range(n)
    ]


def _judge_axis(mean: float) -> dict:
    return {"count": 10, "mean": mean, "histogram": [0] * 9 + [10]}


def _judge_blob(weighted_mean: float = 0.9) -> dict:
    axes = {
        "fidelity": _judge_axis(0.95),
        "naturalness": _judge_axis(0.85),
        "utility": _judge_axis(0.9),
        "weighted": _judge_axis(weighted_mean),
    }
    return {
        "count": 10,
        "mean": {axis: v["mean"] for axis, v in axes.items()},
        "histogram": {axis: v["histogram"] for axis, v in axes.items()},
        "by_key": {
            "billing": {
                "count": 10,
                "mean": {axis: v["mean"] for axis, v in axes.items()},
                "histogram": {axis: v["histogram"] for axis, v in axes.items()},
            }
        },
    }


class TestStoredJudgeAggregates:
    async def test_sdg_dataset_with_stored_insights_populates_judge_and_blends_score(
        self, db: AsyncSession, project: Project, patch_minio
    ) -> None:
        rows = _classification_rows(20)
        meta = {
            "insights": {
                "schema_version": 1,
                "judge": _judge_blob(weighted_mean=0.9),
                "counts": {
                    "generated": 25,
                    "target": 20,
                    "schema_rejected": 2,
                    "duplicates_removed": 3,
                    "judge_rejected": 0,
                    "judge_parse_failures": 0,
                },
            }
        }
        ds = await _make_dataset(
            db,
            project,
            rows=rows,
            fake_minio=patch_minio,
            key="sdg-with-judge.jsonl",
            source=DatasetSource.SDG,
            generation_metadata=meta,
        )

        resp = await di_module.get_dataset_insights(db, ds.id, USER_A)

        assert resp.judge is not None
        assert resp.judge.count == 10
        assert resp.judge.weighted.mean == pytest.approx(0.9)
        assert resp.judge.fidelity.histogram == [0] * 9 + [10]
        assert resp.judge_by_key is not None
        assert "billing" in resp.judge_by_key
        assert resp.counts is not None
        assert resp.counts.generated == 25
        assert resp.counts.target == 20

        # Base score (all rows share one label, no dupes/missing/outliers) is
        # 100; blended with a 0.9 judge weighted mean:
        # round(0.75*100 + 0.25*0.9*100) == 98.
        assert resp.overall_quality_score == 98
        assert resp.readiness == "ready"


class TestUploadedDatasetNoMetadata:
    async def test_uploaded_dataset_with_no_metadata_returns_200_with_null_judge(
        self, db: AsyncSession, project: Project, patch_minio
    ) -> None:
        rows = _classification_rows(10)
        ds = await _make_dataset(
            db,
            project,
            rows=rows,
            fake_minio=patch_minio,
            key="uploaded-no-meta.jsonl",
            source=DatasetSource.UPLOADED,
            generation_metadata=None,
        )

        resp = await di_module.get_dataset_insights(db, ds.id, USER_A)

        assert resp.judge is None
        assert resp.judge_by_key is None
        assert resp.counts is None
        assert resp.row_count == 10
        assert resp.scanned_rows == 10
        assert resp.scan_truncated is False
        assert resp.label_distribution[0].label == "billing"
        assert resp.label_distribution[0].count == 10


class TestNoRowsYet:
    async def test_storage_uri_none_returns_409(
        self, db: AsyncSession, project: Project, patch_minio
    ) -> None:
        ds = await _make_dataset(
            db,
            project,
            rows=None,
            fake_minio=patch_minio,
            key="unused.jsonl",
            source=DatasetSource.SDG,
        )

        with pytest.raises(HTTPException) as exc:
            await di_module.get_dataset_insights(db, ds.id, USER_A)
        assert exc.value.status_code == 409


class TestMalformedStoredInsights:
    async def test_malformed_insights_blob_returns_200_with_nulls(
        self, db: AsyncSession, project: Project, patch_minio
    ) -> None:
        rows = _classification_rows(5)
        # `judge.mean` missing the "utility" key entirely -> KeyError inside
        # `_judge_stats_from_dict`, caught by `_map_stored_insights`.
        meta = {
            "insights": {
                "schema_version": 1,
                "judge": {
                    "count": 5,
                    "mean": {"fidelity": 0.9, "naturalness": 0.8, "weighted": 0.85},
                    "histogram": {"fidelity": [0] * 10},
                },
                "counts": {"generated": 5},
            }
        }
        ds = await _make_dataset(
            db,
            project,
            rows=rows,
            fake_minio=patch_minio,
            key="malformed.jsonl",
            source=DatasetSource.SDG,
            generation_metadata=meta,
        )

        resp = await di_module.get_dataset_insights(db, ds.id, USER_A)

        assert resp.judge is None
        assert resp.judge_by_key is None
        assert resp.counts is None
        assert resp.row_count == 5


class TestScanCap:
    async def test_more_than_cap_rows_are_truncated(
        self, db: AsyncSession, project: Project, patch_minio
    ) -> None:
        n = di_module.INSIGHTS_SCAN_LIMIT + 37
        rows = _classification_rows(n)
        ds = await _make_dataset(
            db,
            project,
            rows=rows,
            fake_minio=patch_minio,
            key="huge.jsonl",
            source=DatasetSource.SEED,
            num_samples=n,
        )

        resp = await di_module.get_dataset_insights(db, ds.id, USER_A)

        assert resp.scanned_rows == di_module.INSIGHTS_SCAN_LIMIT
        assert resp.scan_truncated is True
        assert resp.row_count == n  # num_samples wins over the scanned count


class TestNearDuplicateDetection:
    async def test_paraphrase_pair_is_flagged_as_near_duplicate(
        self, db: AsyncSession, project: Project, patch_minio
    ) -> None:
        rows = [
            {
                "text": "Please process my refund request for order 48213 as soon as possible",
                "label": "billing",
            },
            {
                "text": "Please process my refund request for order 48213 as soon as possible.",
                "label": "billing",
            },
            {"text": "What is your return policy for damaged goods", "label": "shipping"},
        ]
        ds = await _make_dataset(
            db,
            project,
            rows=rows,
            fake_minio=patch_minio,
            key="near-dup.jsonl",
        )

        resp = await di_module.get_dataset_insights(db, ds.id, USER_A)

        assert resp.near_duplicate_count >= 1


class TestOwnershipDenial:
    async def test_non_owner_gets_403(
        self, db: AsyncSession, project: Project, patch_minio
    ) -> None:
        rows = _classification_rows(5)
        ds = await _make_dataset(
            db,
            project,
            rows=rows,
            fake_minio=patch_minio,
            key="owned-by-a.jsonl",
        )

        with pytest.raises(HTTPException) as exc:
            await di_module.get_dataset_insights(db, ds.id, USER_B)
        assert exc.value.status_code == 403

    async def test_owner_can_still_read_their_own(
        self, db: AsyncSession, project: Project, patch_minio
    ) -> None:
        rows = _classification_rows(5)
        ds = await _make_dataset(
            db,
            project,
            rows=rows,
            fake_minio=patch_minio,
            key="owned-by-a-2.jsonl",
        )

        resp = await di_module.get_dataset_insights(db, ds.id, USER_A)
        assert resp.dataset_id == ds.id
