"""W2-T7 cross-cutting hardening: Ollama-tag-computation edges beyond what
`tests/unit/test_ollama_tag_naming.py` already covers.

That file exercises: (training_name, owner_id) present/absent combinations
at the pure-function level, and the DB resolver `_ollama_tag_context` when
the ARTIFACT itself is missing. Not covered there, and covered here instead:

  1. The chain is broken one hop further in — the `ModelArtifact` row
     exists (as it would mid-export, created before the export task even
     starts) but its `TrainingJob` has since disappeared. Distinct code
     path from "artifact missing" (same early return, different branch).
  2. The chain resolves down to `TrainingJob` but the `Project` row is
     gone — `_ollama_tag_context` explicitly guards this
     (`project.owner_id if project is not None else None`), a branch the
     existing tests never hit because their `_seed_chain` helper always
     creates the Project.
  3. `training_name=""` (empty string) vs `training_name=None` at the
     `_compute_ollama_tag` pure-function level. The schema-level
     `TRAINING_NAME_PATTERN` (api/schemas/training.py) can never produce
     `""` through the validated API today, but the DB column itself
     (`String(200), nullable=True`) has no CHECK constraint, so a legacy/
     out-of-band row could carry `""`. `if training_name:` treats it the
     same as falsy/None per Python truthiness — asserted explicitly here
     as a documented, deliberate consequence rather than an oversight.

Same in-memory sync sqlite + JSONB `@compiles` shim + `session_scope`
monkeypatch harness as `test_ollama_tag_naming.py`.
"""

from __future__ import annotations

from contextlib import contextmanager
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from api.models.base import Base
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.enums import JobStatus, TaskType, TrainingMode
from workers.tasks import model_export


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


@pytest.fixture
def sync_sessionmaker():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    yield maker
    engine.dispose()


def _patch_session_scope(monkeypatch: pytest.MonkeyPatch, sync_sessionmaker) -> None:
    @contextmanager
    def _fake_session_scope():
        session = sync_sessionmaker()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(model_export, "session_scope", _fake_session_scope)


class TestComputeOllamaTagEmptyStringTrainingName:
    def test_empty_string_training_name_falls_back_to_legacy_tag_like_none(self) -> None:
        tag_empty = model_export._compute_ollama_tag(
            "a1b2c3d4e5f6", training_name="", owner_id="owner-abc"
        )
        tag_none = model_export._compute_ollama_tag(
            "a1b2c3d4e5f6", training_name=None, owner_id="owner-abc"
        )
        assert tag_empty == tag_none == "slm/a1b2c3d4"
        # Explicitly NOT "owner-abc/" (empty tag suffix) — falsy-string
        # short-circuits before the owner_id branch is even considered.
        assert tag_empty != "owner-abc/"


class TestOllamaTagContextArtifactPresentTrainingJobMissing:
    def test_artifact_exists_but_its_training_job_is_gone(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker
    ) -> None:
        """Distinct from `test_missing_artifact_returns_none_none` (artifact
        itself absent) — here the artifact row is present (as it would be
        mid-export, since `ModelArtifact` is created before the export task
        runs) but its `training_job_id` points at a `TrainingJob` that no
        longer exists (e.g. deleted out from under an in-flight export)."""
        _patch_session_scope(monkeypatch, sync_sessionmaker)

        dangling_training_job_id = uuid4()
        artifact_id = uuid4()
        session = sync_sessionmaker()
        try:
            artifact = ModelArtifact(
                id=artifact_id,
                training_job_id=dangling_training_job_id,
                name="artifact",
                base_model="unsloth/Llama-3.2-1B",
                lora_adapter_uri="s3://bucket/adapter",
            )
            session.add(artifact)
            session.commit()
        finally:
            session.close()

        training_name, owner_id = model_export._ollama_tag_context(artifact_id)
        assert training_name is None
        assert owner_id is None

        tag = model_export._compute_ollama_tag(
            str(artifact_id), training_name=training_name, owner_id=owner_id
        )
        assert tag == f"slm/{str(artifact_id)[:8]}"


class TestOllamaTagContextTrainingJobPresentProjectMissing:
    def test_training_job_exists_but_its_project_is_gone(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker
    ) -> None:
        """One hop further than the missing-TrainingJob case: TrainingJob
        resolves fine (so `training_name` comes back populated) but its
        `project_id` points at a `Project` row that's gone — a state that
        can't arise through the ORM-level `delete_project` path today
        (Project.training_jobs cascades in lockstep), but could exist from
        a raw-SQL admin delete of just the `projects` row, or a
        partially-applied cleanup. `_ollama_tag_context` explicitly guards
        this with `project.owner_id if project is not None else None`."""
        _patch_session_scope(monkeypatch, sync_sessionmaker)

        training_job_id = uuid4()
        artifact_id = uuid4()
        dataset_id = uuid4()
        dangling_project_id = uuid4()  # never inserted

        session = sync_sessionmaker()
        try:
            training_job = TrainingJob(
                id=training_job_id,
                project_id=dangling_project_id,
                dataset_id=dataset_id,
                mode=TrainingMode.MANUAL,
                status=JobStatus.COMPLETED,
                base_model="unsloth/Llama-3.2-1B",
                training_name="qa-policy-v1",
                config_json={},
            )
            session.add(training_job)
            artifact = ModelArtifact(
                id=artifact_id,
                training_job_id=training_job_id,
                name="artifact",
                base_model="unsloth/Llama-3.2-1B",
                lora_adapter_uri="s3://bucket/adapter",
            )
            session.add(artifact)
            session.commit()
        finally:
            session.close()

        training_name, owner_id = model_export._ollama_tag_context(artifact_id)
        # training_name resolves fine — it lives on TrainingJob, not Project.
        assert training_name == "qa-policy-v1"
        # owner_id falls back to None since Project can't be resolved.
        assert owner_id is None

        tag = model_export._compute_ollama_tag(
            str(artifact_id), training_name=training_name, owner_id=owner_id
        )
        # Falls to the "local/" rule (case 2), NOT the owner-scoped rule —
        # a missing Project is treated identically to owner_id=None.
        assert tag == "local/qa-policy-v1"
