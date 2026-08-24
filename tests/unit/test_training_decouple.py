"""Unit tests for D10 — deleting a project must KEEP its trained models.

Decouples `TrainingJob.project_id` from `Project` (migration
`0012_training_decouple`, mirroring `0010_dataset_decouple`'s treatment of
`Dataset.project_id`): NOT NULL + ondelete=CASCADE -> nullable +
ondelete=SET NULL. This file proves the decoupling end-to-end where
possible, and pins the orphan-handling contract of the code this task
actually owns (`api/services/ownership.py`, `training_service.py`,
`trainings_service.py`) directly:

  1. `TestProjectDeleteKeepsTraining` — deleting a project through the real
     service (`projects_service.delete_project`) leaves the `TrainingJob`
     row in place with `project_id` set to `NULL`, and its `ModelArtifact`
     intact. **Previously blocked, now fixed**: `api/models/project.py`'s
     `training_jobs` relationship used to carry `cascade="all,
     delete-orphan"` with no `passive_deletes`, which made the ORM eagerly
     hard-delete every `TrainingJob` (and, via `TrainingJob.model_artifact`'s
     own delete-orphan cascade, every `ModelArtifact`) the moment
     `db.delete(project)` flushed — entirely bypassing the `ondelete="SET
     NULL"` FK this task adds. Fixed by mirroring the `datasets`
     relationship's treatment from migration 0010 (`cascade="save-update,
     merge"` + `passive_deletes="all"`) onto `training_jobs` too.
  2. Everything else constructs the *orphaned* state directly (a
     `TrainingJob` with `project_id` set to `None`, matching what the DB's
     `ondelete=SET NULL` produces once the cascade bug above is fixed)
     rather than going through the broken deletion path, so it tests this
     task's own code in isolation from that unrelated defect:
       - `list_trainings` (no project filter, auth off) still returns the
         orphaned run.
       - `_assert_training_name_available` still 409s against a name held
         by an orphaned run (LEFT JOIN, not INNER — see
         `training_service.py`).
       - `assert_training_access` / `scope_trainings_to_owner` on an
         orphan: a no-op when `user is None`, fail-closed (403 / excluded)
         when `user` is set — same policy `test_ownership.py` already pins
         for a null-owner `Project`, extended to "no `Project` at all".

In-memory aiosqlite; no Postgres, no GPU, no Celery broker. The
`@compiles(JSONB, "sqlite")` shim is the same known gotcha
`test_dataset_status.py` and `test_ownership.py` already document.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from api.core.auth import CurrentUser
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.enums import DatasetSource, JobStatus, TaskType, TrainingMode
from api.services import ownership


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


ALICE = CurrentUser(id="alice-sub", email="alice@example.com")


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)

    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_fks(dbapi_conn, _record):  # noqa: ANN001
        # sqlite defaults FK enforcement OFF per-connection; without this,
        # `ON DELETE SET NULL` never fires and `TestProjectDeleteKeepsTraining`
        # below would pass for the wrong reason (or fail outright) regardless
        # of whether api/models/project.py's cascade is correct. Same pragma
        # `test_wave1_hardening_dataset_project_delete_cascade.py` uses for
        # the identical reason.
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


async def _make_project_with_training(
    db: AsyncSession, *, owner: str | None, tag: str
) -> dict:
    """One project -> dataset (needed for the training's dataset_id FK) ->
    training -> model artifact chain, all owned (or not) by `owner`.
    """
    project = Project(id=uuid4(), name=f"p-{tag}", task_type=TaskType.QA, owner_id=owner)
    dataset = Dataset(
        id=uuid4(), project_id=project.id, owner_id=owner, name=f"d-{tag}",
        source=DatasetSource.SEED, task_type=TaskType.QA, status=JobStatus.COMPLETED,
        num_samples=1, storage_uri="s3://bucket/key",
    )
    training = TrainingJob(
        id=uuid4(), project_id=project.id, dataset_id=dataset.id, mode=TrainingMode.MANUAL,
        status=JobStatus.COMPLETED, celery_task_id=f"train-{tag}",
        base_model="unsloth/x", training_name=f"run-{tag}", config_json={},
    )
    db.add_all([project, dataset, training])
    await db.flush()
    artifact = ModelArtifact(
        id=uuid4(), training_job_id=training.id, name=f"m-{tag}", base_model="unsloth/x",
    )
    db.add(artifact)
    await db.commit()
    return {"project": project, "dataset": dataset, "training": training, "artifact": artifact}


async def _orphan_training(db: AsyncSession, world: dict) -> None:
    """Simulate what `ondelete=SET NULL` produces on project delete,
    without going through the ORM-cascade-broken `delete_project` path
    (see the module docstring's xfail note): null the FK directly, delete
    the `Project` row via a bare Core statement (so no ORM relationship
    cascade ever fires), and commit.
    """
    from sqlalchemy import delete

    training = world["training"]
    training.project_id = None
    await db.flush()
    await db.execute(delete(Project).where(Project.id == world["project"].id))
    await db.commit()
    await db.refresh(training)


class TestProjectDeleteKeepsTraining:
    async def test_training_and_model_survive_project_delete(self, db: AsyncSession) -> None:
        from api.services import projects_service

        world = await _make_project_with_training(db, owner="alice-sub", tag="keep")
        training_id = world["training"].id
        artifact_id = world["artifact"].id
        project_id = world["project"].id

        await projects_service.delete_project(db, project_id, ALICE)

        # `project_id` FK nullification happens at the DB level (ON DELETE
        # SET NULL), not through the ORM (passive_deletes="all" — see
        # api/models/project.py) — with expire_on_commit=False, `training`
        # is still sitting in the session's identity map from the earlier
        # `_make_project_with_training` flush/commit, so it won't pick up
        # that DB-side change without an explicit expire. Same pattern
        # `test_wave1_hardening_dataset_project_delete_cascade.py` uses for
        # its analogous dataset check.
        db.expire_all()

        # Project itself is gone.
        assert await db.get(Project, project_id) is None

        # TrainingJob survives, orphaned (project_id NULL).
        training = await db.get(TrainingJob, training_id)
        assert training is not None
        assert training.project_id is None

        # ModelArtifact survives too.
        artifact = await db.get(ModelArtifact, artifact_id)
        assert artifact is not None
        assert artifact.training_job_id == training_id

    async def test_list_trainings_without_project_filter_returns_orphan(
        self, db: AsyncSession
    ) -> None:
        from api.services import trainings_service

        world = await _make_project_with_training(db, owner=None, tag="list")
        await _orphan_training(db, world)
        training_id = world["training"].id

        page = await trainings_service.list_trainings(
            db, project_id=None, status_filter=None, limit=50, offset=0, user=None,
        )
        assert training_id in {item.id for item in page.items}
        orphan = next(item for item in page.items if item.id == training_id)
        assert orphan.project_id is None


class TestTrainingNameCollisionAcrossOrphans:
    async def test_orphaned_run_still_blocks_the_name(self, db: AsyncSession) -> None:
        from api.services import training_service

        world = await _make_project_with_training(db, owner=None, tag="name")
        await _orphan_training(db, world)

        # A fresh project (also owner_id=None -- same "global/null-owner
        # scope" bucket) submitting the same training_name must still 409,
        # even though the run that first claimed it is now orphaned.
        new_project = Project(id=uuid4(), name="p-new", task_type=TaskType.QA, owner_id=None)
        db.add(new_project)
        await db.flush()

        with pytest.raises(HTTPException) as exc:
            await training_service._assert_training_name_available(
                db, project=new_project, training_name="run-name"
            )
        assert exc.value.status_code == 409

    async def test_unrelated_name_is_still_available(self, db: AsyncSession) -> None:
        from api.services import training_service

        world = await _make_project_with_training(db, owner=None, tag="free")
        await _orphan_training(db, world)

        new_project = Project(id=uuid4(), name="p-new2", task_type=TaskType.QA, owner_id=None)
        db.add(new_project)
        await db.flush()

        # Should not raise -- this name was never used.
        await training_service._assert_training_name_available(
            db, project=new_project, training_name="totally-different-name"
        )

    async def test_orphan_in_a_named_owner_scope_does_not_block_a_different_owner(
        self, db: AsyncSession
    ) -> None:
        """An orphan's scope is "global/null-owner", not the named owner its
        (now-deleted) project used to have -- a named-owner project must not
        collide with it.
        """
        from api.services import training_service

        world = await _make_project_with_training(db, owner="alice-sub", tag="named")
        await _orphan_training(db, world)

        bob_project = Project(id=uuid4(), name="p-bob", task_type=TaskType.QA, owner_id="bob-sub")
        db.add(bob_project)
        await db.flush()

        # Should not raise: bob's scope ("bob-sub") is disjoint from the
        # orphan's scope (null-owner), even though the orphan's project used
        # to belong to alice.
        await training_service._assert_training_name_available(
            db, project=bob_project, training_name="run-named"
        )


class TestAssertTrainingAccessOnOrphan:
    async def test_anonymous_caller_is_allowed(self, db: AsyncSession) -> None:
        world = await _make_project_with_training(db, owner="alice-sub", tag="anon")
        await _orphan_training(db, world)
        training_id = world["training"].id

        training = await ownership.assert_training_access(db, training_id, None)
        assert training.id == training_id
        assert training.project_id is None

    async def test_authenticated_caller_is_refused_with_403(self, db: AsyncSession) -> None:
        world = await _make_project_with_training(db, owner="alice-sub", tag="fail-closed")
        await _orphan_training(db, world)
        training_id = world["training"].id

        with pytest.raises(HTTPException) as exc:
            await ownership.assert_training_access(db, training_id, ALICE)
        assert exc.value.status_code == 403

    async def test_scoped_list_excludes_orphan_for_authenticated_caller(
        self, db: AsyncSession
    ) -> None:
        world = await _make_project_with_training(db, owner="alice-sub", tag="scope")
        await _orphan_training(db, world)
        training_id = world["training"].id

        stmt = ownership.scope_trainings_to_owner(select(TrainingJob.id), ALICE)
        ids = {r[0] for r in (await db.execute(stmt)).all()}
        assert training_id not in ids

    async def test_scoped_list_includes_orphan_when_anonymous(self, db: AsyncSession) -> None:
        world = await _make_project_with_training(db, owner="alice-sub", tag="scope-anon")
        await _orphan_training(db, world)
        training_id = world["training"].id

        stmt = ownership.scope_trainings_to_owner(select(TrainingJob.id), None)
        ids = {r[0] for r in (await db.execute(stmt)).all()}
        assert training_id in ids
