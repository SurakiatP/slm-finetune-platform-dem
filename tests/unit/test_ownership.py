"""The acceptance criterion, tested directly (ADR-012, superseding ADR-009).

`BACKEND_GAP_ANALYSIS.md:33-34` states it as: *user A must get `403` on
every resource and every job stream belonging to user B.* Everything here is
that sentence, turned into assertions at every join depth ownership has to
travel:

    Dataset / TrainingJob      -> Project                                (1 hop)
    ModelArtifact              -> TrainingJob -> Project                 (2 hops)
    EvaluationRun              -> ModelArtifact -> TrainingJob -> Project (3 hops)

Three behaviours look like bugs and are not, so they are pinned explicitly:

* **`403` for someone else's *existing* row, `404` for a row that doesn't
  exist at all.** These must never collapse into one code — a mutation that
  makes everything `403` (or everything `404`) has to be caught by this
  file, which is why every "wrong owner" test below has a "doesn't exist"
  twin sitting next to it. See `test_missing_row_is_404` and
  `test_anonymous_still_404s_on_a_genuinely_missing_row`.
* **This is a known, accepted trade-off, not a fixed hole.** `403` lets an
  authenticated caller distinguish "exists, not yours" from "doesn't exist"
  — the exact id-oracle risk ADR-009 avoided by using `404` for both. ADR-012
  accepts that risk because the customer's own acceptance criterion requires
  `403`, not because the risk went away. See `ownership.py`'s module
  docstring for the full argument.
* **`owner_id IS NULL` belongs to nobody**, not to everybody, and — since
  the row still exists — that is a `403` like any other mismatch, not a
  `404`. Rows predating the migration must not become world-readable the
  moment auth turns on, but they also aren't invisible: fail closed, not
  fail silent.

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
        id=uuid4(), project_id=project.id, owner_id=owner, name=f"d-{tag}",
        source=DatasetSource.SDG, task_type=TaskType.QA, status=JobStatus.COMPLETED,
        celery_task_id=f"sdg-{tag}",
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

    # ---- the pair: existing-but-not-yours (403) vs genuinely-missing (404) --
    #
    # Kept adjacent deliberately (Round 3.5 lesson): a mutation that makes
    # `_check_owner` fall back to `_not_found` only shows up in the first of
    # these two; a mutation that makes `_not_found` itself return 403 (i.e.
    # "everything is 403") only shows up in the second. Neither is visible
    # from the other, so both must exist side by side or a defect in either
    # direction ships silently green.

    @pytest.mark.parametrize("kind,assert_fn", _ASSERTS, ids=[k for k, _ in _ASSERTS])
    async def test_other_user_is_refused_with_403(
        self, db, alice_world, kind, assert_fn
    ) -> None:
        """THE acceptance criterion (`BACKEND_GAP_ANALYSIS.md:33-34`), at all
        three join depths: an existing row that isn't BOB's is a 403."""
        with pytest.raises(HTTPException) as exc:
            await assert_fn(db, alice_world[kind].id, BOB)
        assert exc.value.status_code == 403

    @pytest.mark.parametrize("kind,assert_fn", _ASSERTS, ids=[k for k, _ in _ASSERTS])
    async def test_missing_row_is_404(self, db, alice_world, kind, assert_fn) -> None:
        """The other half of the pair above: a uuid that names no row at all
        must stay 404 even though owner-mismatch on the same resource type is
        now 403. If a bug ever made `_check_owner`'s 403 apply universally
        (i.e. `_not_found` itself became 403), this is the test that would
        catch it — `test_other_user_is_refused_with_403` above cannot, since
        it never exercises a missing id."""
        with pytest.raises(HTTPException) as exc:
            await assert_fn(db, uuid4(), BOB)
        assert exc.value.status_code == 404

    @pytest.mark.parametrize("kind,assert_fn", _ASSERTS, ids=[k for k, _ in _ASSERTS])
    async def test_forbidden_and_missing_are_now_distinguishable(
        self, db, alice_world, kind, assert_fn
    ) -> None:
        """The mirror image of what this file pinned before ADR-012: 403 and
        404 must now be genuinely different responses, not the same response
        under two names. This is the accepted trade-off (see `ownership.py`'s
        module docstring) made visible as an assertion — if this test starts
        failing because the two codes were quietly made identical again, that
        is the P0 acceptance criterion regressing, not a false alarm."""
        forbidden_id = alice_world[kind].id
        missing_id = uuid4()

        with pytest.raises(HTTPException) as exc_forbidden:
            await assert_fn(db, forbidden_id, BOB)
        with pytest.raises(HTTPException) as exc_missing:
            await assert_fn(db, missing_id, BOB)

        assert exc_forbidden.value.status_code == 403
        assert exc_missing.value.status_code == 404
        assert exc_forbidden.value.status_code != exc_missing.value.status_code

    @pytest.mark.parametrize("kind,assert_fn", _ASSERTS, ids=[k for k, _ in _ASSERTS])
    async def test_null_owner_rows_belong_to_nobody(
        self, db, orphan_world, kind, assert_fn
    ) -> None:
        """Fails closed. A pre-auth row must not become world-readable — but
        it still *exists*, so the refusal is 403 like any other mismatch,
        not 404 (ADR-012)."""
        with pytest.raises(HTTPException) as exc:
            await assert_fn(db, orphan_world[kind].id, ALICE)
        assert exc.value.status_code == 403

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
            id=uuid4(), project_id=project.id, owner_id=ALICE.id, name="legacy-ds",
            source=DatasetSource.SDG, task_type=TaskType.QA, status=JobStatus.RUNNING,
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


# =============================================================================
# 5. `POST /evaluations/compare` — the list-shaped endpoint
#
# It names concrete ids, so ADR-012's rule applies: exists-but-not-yours is
# 403, exists-nowhere is 404. It was missed by the original 403 split because
# it does not call `assert_evaluation_access` — it scopes a list query, and a
# foreign run therefore fell into the same "not found" branch as a made-up
# UUID. Correct under ADR-009, wrong under ADR-012, and not on ADR-012's
# exclusion list.
#
# The pair below is the point. Asserting only the 403 case would pass against
# an implementation that returned 403 for *everything*, which would delete the
# 404 branch without a single test noticing.
# =============================================================================


class TestCompareEvaluationsOwnership:
    async def test_another_users_evaluation_is_403(
        self, db: AsyncSession, alice_world, bob_world
    ) -> None:
        from api.schemas.evaluations import EvaluationCompareRequest
        from api.services import evaluation_service

        with pytest.raises(HTTPException) as exc:
            await evaluation_service.compare_evaluations(
                db,
                EvaluationCompareRequest(
                    evaluation_ids=[alice_world["evaluation"].id, bob_world["evaluation"].id]
                ),
                ALICE,
            )
        assert exc.value.status_code == 403
        assert str(bob_world["evaluation"].id) in str(exc.value.detail)
        assert str(alice_world["evaluation"].id) not in str(exc.value.detail)

    async def test_a_nonexistent_evaluation_is_still_404(
        self, db: AsyncSession, alice_world
    ) -> None:
        """The neighbouring case. Without it, an implementation that answers
        403 unconditionally passes the test above and silently removes 404
        from this endpoint entirely."""
        from api.schemas.evaluations import EvaluationCompareRequest
        from api.services import evaluation_service

        ghost = uuid4()
        with pytest.raises(HTTPException) as exc:
            await evaluation_service.compare_evaluations(
                db,
                EvaluationCompareRequest(
                    evaluation_ids=[alice_world["evaluation"].id, ghost]
                ),
                ALICE,
            )
        assert exc.value.status_code == 404
        assert str(ghost) in str(exc.value.detail)

    async def test_403_wins_when_both_kinds_are_present(
        self, db: AsyncSession, alice_world, bob_world
    ) -> None:
        """A request mixing a foreign id and a made-up one must not let the
        404 branch mask the 403 — otherwise padding the list with one junk
        UUID downgrades the response and hides that the other id is real."""
        from api.schemas.evaluations import EvaluationCompareRequest
        from api.services import evaluation_service

        with pytest.raises(HTTPException) as exc:
            await evaluation_service.compare_evaluations(
                db,
                EvaluationCompareRequest(
                    evaluation_ids=[bob_world["evaluation"].id, uuid4()]
                ),
                ALICE,
            )
        assert exc.value.status_code == 403

    async def test_the_owner_still_gets_their_comparison(
        self, db: AsyncSession, alice_world
    ) -> None:
        """The happy path must survive the extra existence probe.

        `EvaluationCompareRequest` requires at least two ids, so this needs a
        second run of Alice's — which is also the more honest test: the
        endpoint exists to compare, and a one-id call could never exercise
        the `unreachable` branch it now guards.
        """
        from api.schemas.evaluations import EvaluationCompareRequest
        from api.services import evaluation_service

        second = EvaluationRun(
            id=uuid4(),
            model_artifact_id=alice_world["artifact"].id,
            dataset_id=alice_world["dataset"].id,
            status=JobStatus.COMPLETED,
            celery_task_id="eval-alice-2",
        )
        db.add(second)
        await db.commit()

        resp = await evaluation_service.compare_evaluations(
            db,
            EvaluationCompareRequest(
                evaluation_ids=[alice_world["evaluation"].id, second.id]
            ),
            ALICE,
        )
        assert resp is not None

    async def test_anonymous_caller_is_unaffected(
        self, db: AsyncSession, alice_world, bob_world
    ) -> None:
        """`user is None` is a documented no-op everywhere in ownership.py;
        phase-1 behaviour must not change."""
        from api.schemas.evaluations import EvaluationCompareRequest
        from api.services import evaluation_service

        resp = await evaluation_service.compare_evaluations(
            db,
            EvaluationCompareRequest(
                evaluation_ids=[alice_world["evaluation"].id, bob_world["evaluation"].id]
            ),
            None,
        )
        assert resp is not None
