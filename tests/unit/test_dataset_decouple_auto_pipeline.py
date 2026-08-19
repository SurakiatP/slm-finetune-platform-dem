"""Unit tests for W1-T1 schema changes: dataset/project decoupling and the
training_jobs auto-pipeline columns (auto_export, auto_evaluate, auto_pipeline).

Layers covered:
  1. Model-level — column existence/nullability/FK-ondelete/server_default
     sanity checks (no DB needed), mirroring the pattern in
     test_dataset_status.py's TestDatasetModelColumns.
  2. DB-level — actual insert/flush against an in-memory sqlite engine
     proving: (a) a Dataset row can persist with project_id=None (the
     orphaned-dataset case that ondelete=SET NULL produces at the Postgres
     level); (b) a bare TrainingJob() insert resolves auto_export /
     auto_evaluate to False via server_default; (c) auto_pipeline JSONB
     round-trips a nested per-stage progress dict.

Everything here runs against an in-memory sqlite engine only — no Postgres,
no Docker, no GPU. Postgres JSONB needs the sqlite @compiles shim (same
gotcha documented in test_dataset_status.py) since sqlite has no native
JSONB type.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from api.models.base import Base
from api.models.dataset import Dataset
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.enums import DatasetSource, JobStatus, TaskType, TrainingMode


# ---- KNOWN GOTCHA shim: JSONB is Postgres-only, sqlite needs a stand-in ----


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


# =============================================================================
# 1. Model-level (no DB needed)
# =============================================================================


class TestDatasetProjectIdColumn:
    def test_project_id_column_is_nullable(self) -> None:
        col = Dataset.__table__.columns["project_id"]
        assert col.nullable is True

    def test_project_id_fk_ondelete_is_set_null(self) -> None:
        col = Dataset.__table__.columns["project_id"]
        fks = list(col.foreign_keys)
        assert len(fks) == 1
        assert fks[0].ondelete == "SET NULL"


class TestTrainingJobAutoPipelineColumns:
    def test_auto_export_not_nullable_with_server_default(self) -> None:
        col = TrainingJob.__table__.columns["auto_export"]
        assert col.nullable is False
        assert col.server_default is not None

    def test_auto_evaluate_not_nullable_with_server_default(self) -> None:
        col = TrainingJob.__table__.columns["auto_evaluate"]
        assert col.nullable is False
        assert col.server_default is not None

    def test_auto_pipeline_column_is_nullable(self) -> None:
        col = TrainingJob.__table__.columns["auto_pipeline"]
        assert col.nullable is True


# =============================================================================
# 2. DB-level (in-memory sqlite, sync)
# =============================================================================


@pytest.fixture
def sync_sessionmaker():
    """In-memory sync sqlite engine + sessionmaker with the full ORM schema."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    yield maker
    engine.dispose()


def _make_project(session) -> Project:
    project = Project(id=uuid4(), name="proj", task_type=TaskType.QA)
    session.add(project)
    session.flush()
    return project


def _make_dataset(session, *, project_id) -> Dataset:
    dataset = Dataset(
        id=uuid4(),
        project_id=project_id,
        name="ds",
        task_type=TaskType.QA,
        source=DatasetSource.SEED,
        status=JobStatus.COMPLETED,
        num_samples=1,
    )
    session.add(dataset)
    session.flush()
    return dataset


class TestDatasetOrphaning:
    def test_dataset_can_be_created_with_project_id_none(self, sync_sessionmaker) -> None:
        session = sync_sessionmaker()
        try:
            dataset = _make_dataset(session, project_id=None)
            session.commit()

            got = session.get(Dataset, dataset.id)
            assert got is not None
            assert got.project_id is None
        finally:
            session.close()

    def test_setting_project_id_to_none_on_existing_row_persists(self, sync_sessionmaker) -> None:
        """Mirrors what ondelete=SET NULL does at the Postgres FK level:
        an existing dataset's project_id transitions from a real project to
        None and that persists across a re-fetch."""
        session = sync_sessionmaker()
        try:
            project = _make_project(session)
            dataset = _make_dataset(session, project_id=project.id)
            session.commit()

            dataset.project_id = None
            session.commit()

            got = session.get(Dataset, dataset.id)
            assert got is not None
            assert got.project_id is None
        finally:
            session.close()


class TestTrainingJobAutoPipelineDefaults:
    def test_auto_export_and_auto_evaluate_default_false(self, sync_sessionmaker) -> None:
        session = sync_sessionmaker()
        try:
            project = _make_project(session)
            dataset = _make_dataset(session, project_id=project.id)

            job = TrainingJob(
                id=uuid4(),
                project_id=project.id,
                dataset_id=dataset.id,
                mode=TrainingMode.MANUAL,
                status=JobStatus.PENDING,
                base_model="Qwen/Qwen2.5-0.5B",
                config_json={"epochs": 1},
            )
            session.add(job)
            session.commit()

            got = session.get(TrainingJob, job.id)
            assert got is not None
            assert got.auto_export is False
            assert got.auto_evaluate is False
            assert got.auto_pipeline is None
        finally:
            session.close()

    def test_auto_pipeline_json_round_trips(self, sync_sessionmaker) -> None:
        session = sync_sessionmaker()
        try:
            project = _make_project(session)
            dataset = _make_dataset(session, project_id=project.id)

            pipeline_state = {
                "export": {
                    "status": "completed",
                    "artifact_id": str(uuid4()),
                    "error": None,
                },
                "evaluate": {
                    "status": "running",
                    "evaluation_id": None,
                    "skip_reason": None,
                    "error": None,
                },
            }

            job = TrainingJob(
                id=uuid4(),
                project_id=project.id,
                dataset_id=dataset.id,
                mode=TrainingMode.MANUAL,
                status=JobStatus.RUNNING,
                base_model="Qwen/Qwen2.5-0.5B",
                config_json={"epochs": 1},
                auto_export=True,
                auto_evaluate=True,
                auto_pipeline=pipeline_state,
            )
            session.add(job)
            session.commit()

            got = session.get(TrainingJob, job.id)
            assert got is not None
            assert got.auto_export is True
            assert got.auto_evaluate is True
            assert got.auto_pipeline == pipeline_state
            assert got.auto_pipeline["export"]["status"] == "completed"
            assert got.auto_pipeline["evaluate"]["status"] == "running"
        finally:
            session.close()
