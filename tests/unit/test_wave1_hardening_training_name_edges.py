"""W2-T7 cross-cutting hardening: `training_name` 409-uniqueness edges that
the wave-1 author's own tests (`tests/unit/test_training_create_contract.py`)
don't cover.

These deliberately do NOT re-test the happy paths already covered there
(same-owner dup, cross-project dup, null-owner scope, different-owner reuse).
Instead:

  1. Does a FAILED/CANCELLED training of the same name still block reuse?
     `_assert_training_name_available` (api/services/training_service.py)
     only filters on `TrainingJob.training_name` + owner scope — it does
     NOT filter on `status`. This suite asserts (documents) that a
     terminal-but-not-deleted training still 409s a same-name resubmit.
     This is CURRENT BEHAVIOR, asserted here as a contract pin, not
     flagged as a bug — whether a failed run "gives back" its name is a
     product decision nobody has made yet.

  2. Case sensitivity: `TRAINING_NAME_PATTERN` (api/schemas/training.py)
     forbids uppercase for anything submitted through the Pydantic
     schema, but the DB-level uniqueness check
     (`TrainingJob.training_name == training_name`) is a plain string
     equality — case-SENSITIVE on both sqlite and Postgres. A legacy /
     pre-validator row with mixed case does NOT block a new lowercase
     name that only differs by case. Asserted directly against the
     service, bypassing the schema validator via a raw ORM insert (the
     only way such a row could exist in practice: pre-existing data from
     before the pattern was added).

  3. The 63-char boundary, exercised at the SERVICE/DB layer (not just
     schema validation, which `test_training_create_contract.py`'s
     `test_max_length_63_enforced` already covers) — the collision check
     must still catch an exact 63-char duplicate.

  4. Project deletion frees the name: `TrainingJob.project_id` is
     `ondelete="CASCADE"` at the DB level AND `Project.training_jobs` is
     `cascade="all, delete-orphan"` at the ORM level (api/models/project.py)
     — both agree, so `projects_service.delete_project` really does
     delete the old TrainingJob row, and the name becomes available again
     in a fresh project under the same owner. (Contrast with
     `test_wave1_hardening_dataset_project_delete_cascade.py`, which
     finds the analogous Dataset relationship does NOT agree with its own
     migration's DB-level `ondelete=SET NULL` — that one IS a bug.)

Same in-memory aiosqlite + JSONB `@compiles` shim harness as
`test_training_create_contract.py`.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from api.core import request_context
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.enums import DatasetSource, JobStatus, TaskType, TrainingMode
from api.schemas.training import ManualTrainingRequest
from api.services import training_service


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def _clear_request_context():
    yield
    request_context.clear()


@pytest.fixture
def spy_apply(monkeypatch):
    calls: dict[str, list[dict]] = {"training": []}
    counter = {"n": 0}

    class _Result:
        def __init__(self, job_id: str) -> None:
            self.id = job_id

    def _apply_async(**kwargs):
        calls["training"].append(kwargs)
        counter["n"] += 1
        return _Result(f"job-training-{counter['n']}")

    import workers.tasks.training as training_task

    monkeypatch.setattr(training_task.train_manual, "apply_async", _apply_async)
    return calls


async def _make_project(db: AsyncSession, *, owner_id: str | None) -> Project:
    project = Project(id=uuid4(), name="proj", task_type=TaskType.QA, owner_id=owner_id)
    db.add(project)
    await db.flush()
    return project


async def _make_ready_dataset(db: AsyncSession, *, project: Project) -> Dataset:
    dataset = Dataset(
        id=uuid4(),
        project_id=project.id,
        name="ds",
        task_type=project.task_type,
        source=DatasetSource.SDG,
        status=JobStatus.COMPLETED,
        num_samples=5,
        storage_uri="s3://datasets/ds.jsonl",
    )
    db.add(dataset)
    await db.flush()
    return dataset


class TestTerminalStatusStillBlocksNameReuse:
    """Current behavior: a FAILED or CANCELLED training's name is NOT
    released. `_assert_training_name_available` has no status filter."""

    @pytest.mark.parametrize("terminal_status", [JobStatus.FAILED, JobStatus.CANCELLED])
    async def test_terminal_training_still_409s_same_name(
        self, db: AsyncSession, spy_apply, terminal_status: JobStatus
    ) -> None:
        project = await _make_project(db, owner_id="owner-a")
        dataset = await _make_ready_dataset(db, project=project)

        first = await training_service.submit_manual_training_job(
            db,
            ManualTrainingRequest(
                project_id=project.id, dataset_id=dataset.id, training_name="release-candidate"
            ),
        )
        # Simulate the worker flipping the row to a terminal, non-deleted
        # status (what a real failed/cancelled run looks like at rest).
        job_row = await db.get(TrainingJob, first.training_id)
        assert job_row is not None
        job_row.status = terminal_status
        await db.commit()

        with pytest.raises(HTTPException) as excinfo:
            await training_service.submit_manual_training_job(
                db,
                ManualTrainingRequest(
                    project_id=project.id,
                    dataset_id=dataset.id,
                    training_name="release-candidate",
                ),
            )
        assert excinfo.value.status_code == 409
        assert len(spy_apply["training"]) == 1, "the blocked resubmit must never enqueue"


class TestNameCollisionIsCaseSensitive:
    async def test_legacy_mixed_case_row_does_not_block_new_lowercase_name(
        self, db: AsyncSession, spy_apply
    ) -> None:
        """A pre-validator / legacy row saved with mixed case (impossible to
        create today via the Pydantic-validated API, but plausible as
        existing data predating `TRAINING_NAME_PATTERN`) must not collide
        with a new, pattern-valid lowercase submission that differs only in
        case — the uniqueness check is a plain string `==`, case-sensitive
        on both sqlite and Postgres."""
        project = await _make_project(db, owner_id="owner-a")
        dataset = await _make_ready_dataset(db, project=project)

        legacy_job = TrainingJob(
            id=uuid4(),
            project_id=project.id,
            dataset_id=dataset.id,
            mode=TrainingMode.MANUAL,
            status=JobStatus.COMPLETED,
            base_model="unsloth/Llama-3.2-3B-Instruct-bnb-4bit",
            training_name="Legacy-Name",  # bypasses the schema validator entirely
            config_json={},
        )
        db.add(legacy_job)
        await db.commit()

        resp = await training_service.submit_manual_training_job(
            db,
            ManualTrainingRequest(
                project_id=project.id, dataset_id=dataset.id, training_name="legacy-name"
            ),
        )
        assert resp.status is JobStatus.PENDING
        assert len(spy_apply["training"]) == 1


class TestSixtyThreeCharBoundaryCollisionAtServiceLayer:
    async def test_exact_63_char_duplicate_409s(self, db: AsyncSession, spy_apply) -> None:
        project = await _make_project(db, owner_id="owner-a")
        dataset = await _make_ready_dataset(db, project=project)
        name_63 = "a" * 63

        await training_service.submit_manual_training_job(
            db, ManualTrainingRequest(project_id=project.id, dataset_id=dataset.id, training_name=name_63)
        )

        with pytest.raises(HTTPException) as excinfo:
            await training_service.submit_manual_training_job(
                db,
                ManualTrainingRequest(
                    project_id=project.id, dataset_id=dataset.id, training_name=name_63
                ),
            )
        assert excinfo.value.status_code == 409


class TestProjectDeletionFreesTrainingName:
    async def test_deleting_the_owning_project_frees_the_name_for_reuse(
        self, db: AsyncSession, spy_apply
    ) -> None:
        """`Project.training_jobs` cascade ("all, delete-orphan") agrees
        with the DB-level `ondelete="CASCADE"` on `TrainingJob.project_id`
        — deleting the project via the real `projects_service.delete_project`
        really does remove the TrainingJob row, so the name becomes free
        again for a brand-new project under the same owner."""
        from api.services import projects_service

        owner = "owner-a"
        project1 = await _make_project(db, owner_id=owner)
        dataset1 = await _make_ready_dataset(db, project=project1)

        await training_service.submit_manual_training_job(
            db,
            ManualTrainingRequest(
                project_id=project1.id, dataset_id=dataset1.id, training_name="reusable-name"
            ),
        )

        await projects_service.delete_project(db, project1.id, user=None)

        # The TrainingJob row is really gone (cascade-deleted), not just
        # its parent project.
        from sqlalchemy import select

        remaining = (
            await db.execute(
                select(TrainingJob).where(TrainingJob.training_name == "reusable-name")
            )
        ).scalars().all()
        assert remaining == []

        project2 = await _make_project(db, owner_id=owner)
        dataset2 = await _make_ready_dataset(db, project=project2)
        resp = await training_service.submit_manual_training_job(
            db,
            ManualTrainingRequest(
                project_id=project2.id, dataset_id=dataset2.id, training_name="reusable-name"
            ),
        )
        assert resp.status is JobStatus.PENDING
        assert len(spy_apply["training"]) == 2
