"""Unit tests for W1-T3 — Ollama tag naming.

Covers `workers/tasks/model_export.py`'s `_compute_ollama_tag` and its DB
resolver `_ollama_tag_context`, across the three tag rules:

  1. `training_name` set + owning Project has `owner_id` set
     -> "{owner_id}/{training_name}"
  2. `training_name` set but Project.owner_id is NULL (AUTH_REQUIRED=false)
     -> "local/{training_name}"
  3. `training_name` is NULL -> unchanged fallback "slm/<first-8-of-uuid>"

Layers covered:
  1. Pure-function level — `_compute_ollama_tag` called directly with each
     combination of (training_name, owner_id), no DB involved.
  2. DB-resolver level — `_ollama_tag_context` against an in-memory sync
     sqlite engine, proving the ModelArtifact -> TrainingJob -> Project
     join picks up the right `training_name` / `owner_id` pair for each of
     the three scenarios, exercised through `session_scope` exactly as
     `export_model`'s registration step would call it.

KNOWN GOTCHA (see tests/unit/test_dataset_status.py): `Project`/`Dataset`-
adjacent models use `sqlalchemy.dialects.postgresql.JSONB`
(`TrainingJob.config_json`), which sqlite doesn't understand natively —
needs the `@compiles` shim below to create the table at all.

Everything here runs against in-memory sqlite only — no Postgres, no
Docker, no GPU, no real Ollama daemon.
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


# ---- KNOWN GOTCHA shim: JSONB is Postgres-only, sqlite needs a stand-in ----


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


# =============================================================================
# 1. Pure-function level — _compute_ollama_tag
# =============================================================================


class TestComputeOllamaTag:
    def test_training_name_and_owner_id_set(self) -> None:
        tag = model_export._compute_ollama_tag(
            "a1b2c3d4e5f6",
            training_name="qa-policy-v1",
            owner_id="9f8e7d6c-1234-5678-9abc-def012345678",
        )
        assert tag == "9f8e7d6c-1234-5678-9abc-def012345678/qa-policy-v1"

    def test_training_name_set_owner_id_none_falls_back_to_local(self) -> None:
        tag = model_export._compute_ollama_tag(
            "a1b2c3d4e5f6",
            training_name="qa-policy-v1",
            owner_id=None,
        )
        assert tag == "local/qa-policy-v1"

    def test_training_name_none_keeps_legacy_fallback(self) -> None:
        # Backward-compat: identical to calling with no kwargs at all (the
        # pre-existing test_snapshot_node_7.py call shape).
        tag = model_export._compute_ollama_tag("a1b2c3d4e5f6", training_name=None, owner_id=None)
        assert tag == "slm/a1b2c3d4"

    def test_training_name_none_owner_id_set_still_uses_legacy_fallback(self) -> None:
        # owner_id alone (without training_name) must not change behaviour —
        # the fallback path only looks at training_name.
        tag = model_export._compute_ollama_tag(
            "a1b2c3d4e5f6", training_name=None, owner_id="some-owner"
        )
        assert tag == "slm/a1b2c3d4"

    def test_no_kwargs_matches_original_default_signature(self) -> None:
        # Proves the signature change is additive: old call sites (and the
        # existing snapshot test) that pass only artifact_id keep working.
        assert model_export._compute_ollama_tag("a1b2c3d4e5f6") == "slm/a1b2c3d4"

    def test_training_name_not_sanitized(self) -> None:
        # No lowercasing/normalization here — enforced elsewhere at
        # training-create time, per the task instructions.
        tag = model_export._compute_ollama_tag(
            "a1b2c3d4e5f6", training_name="QA-Policy_V1", owner_id="Owner-ABC"
        )
        assert tag == "Owner-ABC/QA-Policy_V1"


# =============================================================================
# 2. DB-resolver level — _ollama_tag_context (in-memory sync sqlite)
# =============================================================================


@pytest.fixture
def sync_sessionmaker():
    """In-memory sync sqlite engine + sessionmaker with the full ORM schema."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    yield maker
    engine.dispose()


def _patch_session_scope(monkeypatch: pytest.MonkeyPatch, sync_sessionmaker) -> None:
    """Point `model_export.session_scope` at the sqlite sessionmaker.

    `model_export.py` does `from workers.sync_db import session_scope`,
    binding a module-local alias — patching `workers.sync_db.session_scope`
    afterwards would not reach that alias, so the patch target is the
    use-site (`model_export.session_scope`), same gotcha called out in
    tests/unit/test_dataset_status.py's `_install_worker_patches`.
    """

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


def _seed_chain(
    sync_sessionmaker,
    *,
    owner_id: str | None,
    training_name: str | None,
) -> tuple:
    """Create Project -> TrainingJob -> ModelArtifact and return their ids
    as (project_id, training_job_id, artifact_id).
    """
    project_id = uuid4()
    training_job_id = uuid4()
    artifact_id = uuid4()
    dataset_id = uuid4()  # FK target not enforced by sqlite; no Dataset row needed

    session = sync_sessionmaker()
    try:
        project = Project(
            id=project_id,
            name="proj",
            task_type=TaskType.QA,
            owner_id=owner_id,
        )
        session.add(project)

        training_job = TrainingJob(
            id=training_job_id,
            project_id=project_id,
            dataset_id=dataset_id,
            mode=TrainingMode.MANUAL,
            status=JobStatus.COMPLETED,
            base_model="unsloth/Llama-3.2-1B",
            training_name=training_name,
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

    return project_id, training_job_id, artifact_id


class TestOllamaTagContextAndComputeIntegration:
    def test_owner_id_set_yields_owner_scoped_tag(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker
    ) -> None:
        _patch_session_scope(monkeypatch, sync_sessionmaker)
        owner_id = "9f8e7d6c-1234-5678-9abc-def012345678"
        _project_id, _training_job_id, artifact_id = _seed_chain(
            sync_sessionmaker, owner_id=owner_id, training_name="qa-policy-v1"
        )

        training_name, resolved_owner_id = model_export._ollama_tag_context(artifact_id)
        assert training_name == "qa-policy-v1"
        assert resolved_owner_id == owner_id

        tag = model_export._compute_ollama_tag(
            str(artifact_id), training_name=training_name, owner_id=resolved_owner_id
        )
        assert tag == f"{owner_id}/qa-policy-v1"

    def test_owner_id_null_yields_local_scoped_tag(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker
    ) -> None:
        _patch_session_scope(monkeypatch, sync_sessionmaker)
        _project_id, _training_job_id, artifact_id = _seed_chain(
            sync_sessionmaker, owner_id=None, training_name="qa-policy-v1"
        )

        training_name, resolved_owner_id = model_export._ollama_tag_context(artifact_id)
        assert training_name == "qa-policy-v1"
        assert resolved_owner_id is None

        tag = model_export._compute_ollama_tag(
            str(artifact_id), training_name=training_name, owner_id=resolved_owner_id
        )
        assert tag == "local/qa-policy-v1"

    def test_no_training_name_yields_legacy_fallback_tag(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker
    ) -> None:
        _patch_session_scope(monkeypatch, sync_sessionmaker)
        owner_id = "9f8e7d6c-1234-5678-9abc-def012345678"
        _project_id, _training_job_id, artifact_id = _seed_chain(
            sync_sessionmaker, owner_id=owner_id, training_name=None
        )

        training_name, resolved_owner_id = model_export._ollama_tag_context(artifact_id)
        assert training_name is None
        # owner_id is still resolved even though training_name is None —
        # _compute_ollama_tag is what decides the fallback applies.
        assert resolved_owner_id == owner_id

        tag = model_export._compute_ollama_tag(
            str(artifact_id), training_name=training_name, owner_id=resolved_owner_id
        )
        assert tag == f"slm/{str(artifact_id)[:8]}"

    def test_missing_artifact_returns_none_none(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker
    ) -> None:
        _patch_session_scope(monkeypatch, sync_sessionmaker)
        training_name, owner_id = model_export._ollama_tag_context(uuid4())
        assert training_name is None
        assert owner_id is None
