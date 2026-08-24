"""W2-T7 cross-cutting hardening: does a Dataset actually survive its
Project being deleted, as migration `20260818_0010_dataset_decouple.py`
(W1-T1) intends?

`tests/unit/test_dataset_decouple_auto_pipeline.py` proves the SCHEMA
supports it: `Dataset.project_id` is nullable, and the FK's `ondelete` is
"SET NULL" (asserted directly off `Dataset.__table__.columns["project_id"]
.foreign_keys`). It also proves a Dataset row can be constructed with
`project_id=None`, and that flipping an *existing* row's `project_id` to
`None` in Python and committing persists that. Neither of those exercises
an actual project deletion — this file does, calling the real
`projects_service.delete_project` (the only production code path that
deletes a Project), and finds the migration's guarantee does NOT hold.

*** BUG FOUND BY THIS FILE, SINCE FIXED ***

As originally written, this file proved that `Project.datasets` still
declared `cascade="all, delete-orphan"` (ORM-level), which made the ORM
unit-of-work hard-DELETE every child Dataset before the DB's
`ON DELETE SET NULL` could fire — i.e. migration 0010's guarantee was dead
code on the only production delete path. The fix (applied in
`api/models/project.py` as a direct consequence of this finding) drops the
delete cascade to `cascade="save-update, merge"` with
`passive_deletes="all"`, so the ORM leaves FK handling entirely to the DB
and datasets survive project deletion as orphans (`project_id -> NULL`).

*** UPDATE 2026-08-24 (D10, migration `0012_training_decouple`) ***

The "contrast" this file used to draw against `TrainingJob` no longer
holds: user decision D10 ("deleting a project must KEEP its trained
models") gave `TrainingJob.project_id` the exact same treatment as
`Dataset.project_id` above — nullable, `ondelete="SET NULL"`. `api/models/
training_job.py` was updated accordingly (owned by the D10 task).

`api/models/project.py`'s `training_jobs` relationship briefly lagged
behind that (still `cascade="all, delete-orphan"`, no `passive_deletes`)
— exactly the bug this file's own docstring already tells the story of
for `datasets` above, reintroduced for `training_jobs`: the ORM would
hard-delete every `TrainingJob` (and, via `TrainingJob.model_artifact`'s
own delete-orphan cascade, every `ModelArtifact`) the instant
`projects_service.delete_project`'s `db.delete(project)` flushed,
entirely bypassing the new `ondelete="SET NULL"` FK. See
`tests/unit/test_training_decouple.py` for the full writeup. Now fixed:
`Project.training_jobs` got the same `cascade="save-update, merge"` +
`passive_deletes="all"` treatment already applied to `Project.datasets`,
and `test_contrast_...` below asserts the fixed (post-D10) contract as a
normal, non-xfail test.

sqlite note: FK actions (including SET NULL) only run under
`PRAGMA foreign_keys=ON`, which sqlite defaults OFF — the fixture here
turns it on per-connection so the DB-level behavior is actually exercised.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from api.core import request_context
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.project import Project
from api.schemas.enums import DatasetSource, JobStatus, TaskType


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)

    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_fks(dbapi_conn, _record):  # noqa: ANN001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

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


class TestDatasetOrphaningContractViaRealDeleteProjectService:
    async def test_dataset_project_id_column_declares_set_null_ondelete(self, db: AsyncSession) -> None:
        """Sanity check the premise before showing it doesn't hold in
        practice: the FK metadata really does say SET NULL (matches
        `test_dataset_decouple_auto_pipeline.py`'s equivalent assertion —
        repeated here so this file's failure below is self-contained and
        not dependent on reading the other file to understand the
        contradiction)."""
        col = Dataset.__table__.columns["project_id"]
        fks = list(col.foreign_keys)
        assert len(fks) == 1
        assert fks[0].ondelete == "SET NULL"

    async def test_dataset_survives_project_delete_as_orphan(
        self, db: AsyncSession
    ) -> None:
        """The real `projects_service.delete_project` path must leave the
        dataset alive with `project_id` nulled by the DB's ON DELETE SET
        NULL — this is the acceptance test for the ORM-cascade fix in
        `api/models/project.py` (see module docstring for the history)."""
        from api.services import projects_service

        project = Project(id=uuid4(), name="proj", task_type=TaskType.QA)
        db.add(project)
        await db.flush()

        dataset = Dataset(
            id=uuid4(),
            project_id=project.id,
            name="ds",
            task_type=TaskType.QA,
            source=DatasetSource.SEED,
            status=JobStatus.COMPLETED,
            num_samples=1,
            storage_uri="s3://datasets/ds.jsonl",
        )
        db.add(dataset)
        await db.flush()
        dataset_id = dataset.id

        await projects_service.delete_project(db, project.id, user=None)

        # Post-fix expectation: the dataset SURVIVES as an orphan. The ORM
        # no longer delete-cascades (cascade="save-update, merge" +
        # passive_deletes="all" on Project.datasets), so the DB-level
        # ON DELETE SET NULL nulls project_id instead.
        db.expire_all()
        got = await db.get(Dataset, dataset_id)
        assert got is not None, "dataset must survive project deletion as an orphan"
        assert got.project_id is None

    async def test_contrast_training_job_cascade_is_internally_consistent(
        self, db: AsyncSession
    ) -> None:
        """No longer a contrast — D10 (migration 0012_training_decouple)
        gave `TrainingJob.project_id` the same nullable/SET NULL treatment
        as `Dataset.project_id` above, so a training run now survives its
        project being deleted, exactly like a dataset. `Project.
        training_jobs`'s ORM cascade was updated to match (`cascade=
        "save-update, merge"` + `passive_deletes="all"`) — see the module
        docstring's 2026-08-24 update.
        """
        from api.models.training_job import TrainingJob
        from api.schemas.enums import TrainingMode

        col = TrainingJob.__table__.columns["project_id"]
        fks = list(col.foreign_keys)
        assert fks[0].ondelete == "SET NULL"

        from api.services import projects_service

        project = Project(id=uuid4(), name="proj", task_type=TaskType.QA)
        db.add(project)
        await db.flush()
        dataset = Dataset(
            id=uuid4(),
            project_id=project.id,
            name="ds",
            task_type=TaskType.QA,
            source=DatasetSource.SEED,
            status=JobStatus.COMPLETED,
            num_samples=1,
            storage_uri="s3://datasets/ds.jsonl",
        )
        db.add(dataset)
        await db.flush()
        job = TrainingJob(
            id=uuid4(),
            project_id=project.id,
            dataset_id=dataset.id,
            mode=TrainingMode.MANUAL,
            status=JobStatus.PENDING,
            base_model="m",
            config_json={},
        )
        db.add(job)
        await db.flush()
        job_id = job.id

        await projects_service.delete_project(db, project.id, user=None)

        # Post-D10 expectation (matches test_dataset_survives_project_delete_
        # as_orphan above): the training run SURVIVES as an orphan.
        db.expire_all()
        got = await db.get(TrainingJob, job_id)
        assert got is not None, "training job must survive project deletion as an orphan"
        assert got.project_id is None
