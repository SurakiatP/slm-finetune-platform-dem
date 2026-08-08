"""The acceptance criterion, tested directly (ADR-009).

`BACKEND_GAP_ANALYSIS.md` states it as: *user A must be refused on every
resource and every job stream belonging to user B.* Everything here is that
sentence, turned into assertions at every join depth ownership has to travel:

    Dataset / TrainingJob      -> Project                                (1 hop)
    ModelArtifact              -> TrainingJob -> Project                 (2 hops)
    EvaluationRun              -> ModelArtifact -> TrainingJob -> Project (3 hops)

Two behaviours look like bugs and are not, so they are pinned explicitly:

* **`404`, never `403`**, for someone else's existing row — a `403` would
  confirm the row exists, making every endpoint an id oracle.
* **`owner_id IS NULL` belongs to nobody**, not to everybody. Rows predating
  the migration must not become world-readable the moment auth turns on.

In-memory aiosqlite; no Postgres, no network. The `@compiles(JSONB, "sqlite")`
shim is the same one `test_dataset_status.py` documents as a known gotcha.
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
from api.models.evaluation_run import EvaluationRun
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.enums import DatasetSource, JobStatus, TaskType, TrainingMode
from api.services import ownership
from api.services.job_ownership import resolve_job_owner


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


ALICE = CurrentUser(id="alice-sub", email="alice@example.com")
BOB = CurrentUser(id="bob-sub", email="bob@example.com")


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


async def _world(db: AsyncSession, owner: str | None, tag: str) -> dict:
    """One full ownership chain — project → dataset → training → artifact → eval."""
    project = Project(id=uuid4(), name=f"p-{tag}", task_type=TaskType.QA, owner_id=owner)
    dataset = Dataset(
        id=uuid4(), project_id=project.id, name=f"d-{tag}", source=DatasetSource.SDG,
        task_type=TaskType.QA, status=JobStatus.COMPLETED, celery_task_id=f"sdg-{tag}",
    )
    training = TrainingJob(
        id=uuid4(), project_id=project.id, dataset_id=dataset.id, mode=TrainingMode.MANUAL,
        status=JobStatus.COMPLETED, celery_task_id=f"train-{tag}",
        base_model="unsloth/x", config_json={},
    )
    artifact = ModelArtifact(
        id=uuid4(), training_job_id=training.id, name=f"m-{tag}", base_model="unsloth/x",
        export_celery_task_id=f"export-{tag}",
    )
    evaluation = EvaluationRun(
        id=uuid4(), model_artifact_id=artifact.id, dataset_id=dataset.id,
        status=JobStatus.COMPLETED, celery_task_id=f"eval-{tag}",
    )
    db.add_all([project, dataset, training, artifact, evaluation])
    await db.commit()
    return {
        "project": project, "dataset": dataset, "training": training,
        "artifact": artifact, "evaluation": evaluation,
    }


@pytest.fixture
async def alice_world(db):
    return await _world(db, ALICE.id, "alice")


@pytest.fixture
async def bob_world(db):
    return await _world(db, BOB.id, "bob")


@pytest.fixture
async def orphan_world(db):
    """A chain created before auth existed — `owner_id IS NULL`."""
    return await _world(db, None, "orphan")


_ASSERTS = [
    ("project", ownership.assert_project_access),
    ("dataset", ownership.assert_dataset_access),
    ("training", ownership.assert_training_access),
    ("artifact", ownership.assert_model_access),
    ("evaluation", ownership.assert_evaluation_access),
]


# =============================================================================
# 1. The criterion: A cannot reach B's resources, at every join depth
# =============================================================================


class TestCrossUserAccess:
    @pytest.mark.parametrize("kind,assert_fn", _ASSERTS, ids=[k for k, _ in _ASSERTS])
    async def test_owner_can_reach_their_own(self, db, alice_world, kind, assert_fn) -> None:
        assert await assert_fn(db, alice_world[kind].id, ALICE) is not None

    @pytest.mark.parametrize("kind,assert_fn", _ASSERTS, ids=[k for k, _ in _ASSERTS])
    async def test_other_user_is_refused(self, db, alice_world, kind, assert_fn) -> None:
        """THE acceptance criterion, at all three join depths."""
        with pytest.raises(HTTPException) as exc:
            await assert_fn(db, alice_world[kind].id, BOB)
        assert exc.value.status_code == 404

    @pytest.mark.parametrize("kind,assert_fn", _ASSERTS, ids=[k for k, _ in _ASSERTS])
    async def test_refusal_is_indistinguishable_from_absence(
        self, db, alice_world, kind, assert_fn
    ) -> None:
        """A `403` (or a different message) would confirm the row exists,
        turning every endpoint into a probe for other users' ids."""
        forbidden_id = alice_world[kind].id
        missing_id = uuid4()

        with pytest.raises(HTTPException) as exc_forbidden:
            await assert_fn(db, forbidden_id, BOB)
        with pytest.raises(HTTPException) as exc_missing:
            await assert_fn(db, missing_id, BOB)

        assert exc_forbidden.value.status_code == exc_missing.value.status_code

        # The id inside the message necessarily differs; blank it out and the
        # two messages must then be byte-identical. Anything else — a different
        # word, different punctuation — is enough to tell the cases apart.
        norm_forbidden = str(exc_forbidden.value.detail).replace(str(forbidden_id), "<id>")
        norm_missing = str(exc_missing.value.detail).replace(str(missing_id), "<id>")
        assert norm_forbidden == norm_missing, (
            f"{kind}: refusal and absence are distinguishable — "
            f"{norm_forbidden!r} vs {norm_missing!r}"
        )

    @pytest.mark.parametrize("kind,assert_fn", _ASSERTS, ids=[k for k, _ in _ASSERTS])
    async def test_null_owner_rows_belong_to_nobody(
        self, db, orphan_world, kind, assert_fn
    ) -> None:
        """Fails closed. A pre-auth row must not become world-readable."""
        with pytest.raises(HTTPException) as exc:
            await assert_fn(db, orphan_world[kind].id, ALICE)
        assert exc.value.status_code == 404

    @pytest.mark.parametrize("kind,assert_fn", _ASSERTS, ids=[k for k, _ in _ASSERTS])
    async def test_phase_one_anonymous_is_a_no_op(
        self, db, alice_world, kind, assert_fn
    ) -> None:
        """`user is None` must skip ownership entirely, or the compatibility
        window would break every existing caller."""
        assert await assert_fn(db, alice_world[kind].id, None) is not None

    async def test_anonymous_still_404s_on_a_genuinely_missing_row(self, db) -> None:
        """Phase 1 relaxes *ownership*, not existence."""
        with pytest.raises(HTTPException) as exc:
            await ownership.assert_project_access(db, uuid4(), None)
        assert exc.value.status_code == 404


# =============================================================================
# 2. List scoping — the leak that a per-row check alone wouldn't catch
# =============================================================================


class TestListScoping:
    async def test_projects_are_scoped_to_the_caller(
        self, db, alice_world, bob_world
    ) -> None:
        stmt = ownership.scope_projects_to_owner(select(Project.id), ALICE)
        ids = {r[0] for r in (await db.execute(stmt)).all()}
        assert ids == {alice_world["project"].id}

    @pytest.mark.parametrize(
        "model,scope_fn,key",
        [
            (Dataset, ownership.scope_datasets_to_owner, "dataset"),
            (TrainingJob, ownership.scope_trainings_to_owner, "training"),
            (ModelArtifact, ownership.scope_models_to_owner, "artifact"),
            (EvaluationRun, ownership.scope_evaluations_to_owner, "evaluation"),
        ],
        ids=["datasets", "trainings", "models", "evaluations"],
    )
    async def test_children_are_scoped_through_their_project(
        self, db, alice_world, bob_world, model, scope_fn, key
    ) -> None:
        stmt = scope_fn(select(model.id), ALICE)
        ids = {r[0] for r in (await db.execute(stmt)).all()}
        assert ids == {alice_world[key].id}, f"{key} list leaked across users"

    async def test_null_owner_rows_are_excluded_from_lists(
        self, db, alice_world, orphan_world
    ) -> None:
        stmt = ownership.scope_projects_to_owner(select(Project.id), ALICE)
        ids = {r[0] for r in (await db.execute(stmt)).all()}
        assert orphan_world["project"].id not in ids

    async def test_phase_one_lists_everything(
        self, db, alice_world, bob_world
    ) -> None:
        stmt = ownership.scope_projects_to_owner(select(Project.id), None)
        ids = {r[0] for r in (await db.execute(stmt)).all()}
        assert {alice_world["project"].id, bob_world["project"].id} <= ids


# =============================================================================
# 3. Job streams — the other half of the criterion
# =============================================================================


class TestJobOwnership:
    @pytest.mark.parametrize(
        "job_attr", ["sdg-alice", "train-alice", "eval-alice", "export-alice"]
    )
    async def test_every_job_kind_resolves_to_its_owner(
        self, db, alice_world, job_attr: str
    ) -> None:
        """All four `*celery_task_id` columns must be reachable — SDG,
        training, evaluation and export each live in a different table."""
        result = await resolve_job_owner(db, job_attr)
        assert result.found is True
        assert result.owner_id == ALICE.id

    async def test_unknown_job_is_not_found(self, db, alice_world) -> None:
        result = await resolve_job_owner(db, "no-such-job")
        assert result.found is False
        assert result.owner_id is None

    async def test_null_owner_job_reports_found_with_no_owner(
        self, db, orphan_world
    ) -> None:
        """Callers must treat this as a refusal, not as 'public'."""
        result = await resolve_job_owner(db, "sdg-orphan")
        assert result.found is True
        assert result.owner_id is None

    async def test_legacy_jsonb_only_job_id_still_resolves(self, db) -> None:
        """Datasets predating migration 0006 carry the task id only inside
        `generation_metadata`; the WS gate must still find their owner."""
        project = Project(id=uuid4(), name="legacy", task_type=TaskType.QA, owner_id=ALICE.id)
        dataset = Dataset(
            id=uuid4(), project_id=project.id, name="legacy-ds", source=DatasetSource.SDG,
            task_type=TaskType.QA, status=JobStatus.RUNNING,
            celery_task_id=None, generation_metadata={"celery_task_id": "legacy-job"},
        )
        db.add_all([project, dataset])
        await db.commit()

        result = await resolve_job_owner(db, "legacy-job")
        assert result.found is True
        assert result.owner_id == ALICE.id


# =============================================================================
# 4. Ownership stamping
# =============================================================================


class TestOwnerStamping:
    def test_owner_id_for_authenticated_caller(self) -> None:
        assert ownership.owner_id_for(ALICE) == ALICE.id

    def test_owner_id_for_anonymous_is_none(self) -> None:
        """Phase-1 creates leave `owner_id` null rather than inventing one."""
        assert ownership.owner_id_for(None) is None
