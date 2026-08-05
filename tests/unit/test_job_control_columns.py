"""Unit tests for the job-control columns added in migration `0006`.

Three columns exist so an in-flight job can be recovered (after a browser
reload) or cancelled by job id:

  • ``Dataset.celery_task_id``              — promoted out of the
    ``generation_metadata`` JSONB blob, where it was previously the only copy.
  • ``ModelArtifact.export_status``         — reuses the shared ``job_status``
    Postgres enum type.
  • ``ModelArtifact.export_celery_task_id`` — the export job's id, which
    ``submit_export_job`` previously returned in the 202 body and then dropped.

Layers covered:
  1. Model-level    — columns exist with the right type/nullability/indexing.
  2. Schema-level   — the API responses actually expose them, additively.
  3. Service-level  — ``sdg_service.submit_sdg_job`` populates the column *and*
     keeps the legacy JSONB key in sync, against in-memory aiosqlite.
  4. Migration-level — chain tip, shared-enum handling, backfill.

In-memory fakes only — no Postgres, no Docker, no GPU, no Celery broker.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from api.models.base import Base
from api.models.dataset import Dataset
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.artifacts import ModelArtifactResponse
from api.schemas.datasets import DatasetResponse
from api.schemas.enums import ArtifactFormat, JobStatus, TaskType
from api.schemas.sdg import SDGRequestDescriptionOnly

# ---- KNOWN GOTCHA shim: JSONB is Postgres-only, sqlite needs a stand-in ----


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260804_0006_job_control_columns.py"
)


# =============================================================================
# 1. Model-level
# =============================================================================


class TestColumnDefinitions:
    def test_dataset_has_celery_task_id(self) -> None:
        col = Dataset.__table__.columns["celery_task_id"]
        assert col.nullable, "pre-existing seed datasets have no SDG job"
        assert col.index, "recovery looks datasets up by job id"

    def test_dataset_celery_task_id_is_not_unique(self) -> None:
        """Deliberately weaker than TrainingJob's: the 0006 backfill copies
        values out of a JSONB blob whose uniqueness nobody ever enforced."""
        col = Dataset.__table__.columns["celery_task_id"]
        assert not col.unique, (
            "must stay non-unique — the migration backfills from "
            "generation_metadata['celery_task_id'], which carries no uniqueness guarantee"
        )
        assert TrainingJob.__table__.columns["celery_task_id"].unique, (
            "TrainingJob's stays unique; this test documents the intentional asymmetry"
        )

    def test_artifact_export_columns_exist(self) -> None:
        cols = ModelArtifact.__table__.columns
        assert cols["export_status"].nullable, "null == no export ever requested"
        assert cols["export_celery_task_id"].nullable
        assert cols["export_celery_task_id"].index

    def test_export_status_reuses_the_shared_job_status_enum(self) -> None:
        """CLAUDE.md convention: reuse the `job_status` Postgres type rather
        than creating a second enum type for the same lifecycle."""
        enum_type = ModelArtifact.__table__.columns["export_status"].type
        assert enum_type.name == "job_status"
        assert set(enum_type.enums) == {s.value for s in JobStatus}

    def test_export_status_defaults_to_none(self) -> None:
        artifact = ModelArtifact(
            training_job_id=uuid4(), name="m", base_model="unsloth/x"
        )
        assert artifact.export_status is None
        assert artifact.export_celery_task_id is None

    def test_legacy_completion_signal_is_untouched(self) -> None:
        """`export_status` is additive — `gguf_uri` / `export_error_message`
        remain the contract `smart-model-tune` reads today."""
        cols = ModelArtifact.__table__.columns
        assert "gguf_uri" in cols
        assert "export_error_message" in cols


# =============================================================================
# 2. Schema-level
# =============================================================================


class TestResponseSchemas:
    def test_dataset_response_exposes_celery_task_id(self) -> None:
        assert "celery_task_id" in DatasetResponse.model_fields

    def test_dataset_response_celery_task_id_is_optional(self) -> None:
        """Seed uploads have no job id; the field must not become required."""
        fields = DatasetResponse.model_fields
        assert not fields["celery_task_id"].is_required()

    def test_dataset_response_keeps_generation_metadata(self) -> None:
        """The JSONB path stays — docs/03 §2 documents it and clients may read it."""
        assert "generation_metadata" in DatasetResponse.model_fields

    def test_artifact_response_exposes_export_state(self) -> None:
        fields = ModelArtifactResponse.model_fields
        assert "export_status" in fields
        assert "export_celery_task_id" in fields
        assert not fields["export_status"].is_required()
        assert not fields["export_celery_task_id"].is_required()

    def test_artifact_response_still_carries_the_legacy_fields(self) -> None:
        fields = ModelArtifactResponse.model_fields
        for legacy in ("gguf_uri", "export_error_message", "ollama_model_tag"):
            assert legacy in fields, f"removing {legacy} would break smart-model-tune"


# =============================================================================
# 3. Service-level — submit_sdg_job populates both copies
# =============================================================================


@pytest.fixture
async def async_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


class TestSubmitSdgJobPersistsTaskId:
    async def test_column_and_jsonb_key_are_both_written(
        self, async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The column is the new home; the JSONB key is kept in sync, not moved."""
        from api.services import sdg_service

        project = Project(id=uuid4(), name="proj", task_type=TaskType.QA)
        async_session.add(project)
        await async_session.flush()

        import workers.tasks.data_generation as dg_module

        fake_result = type("FakeAsyncResult", (), {"id": "celery-task-xyz"})()
        monkeypatch.setattr(
            dg_module.generate_synthetic_data, "apply_async", lambda **kw: fake_result
        )

        request = SDGRequestDescriptionOnly(
            project_id=project.id,
            task_type=TaskType.QA,
            task_description="Answer questions about our return policy",
            num_samples=10,
            holdout_size=0,
        )

        response = await sdg_service.submit_sdg_job(async_session, request)

        dataset = await async_session.get(Dataset, response.dataset_id)
        assert dataset is not None
        assert dataset.celery_task_id == "celery-task-xyz", (
            "the column is what a reloaded client reads to reconnect to /ws/jobs/{id}"
        )
        assert (dataset.generation_metadata or {})["celery_task_id"] == "celery-task-xyz", (
            "the legacy JSONB key must stay in sync — docs/03 §2 documents it "
            "as the recovery path and existing clients may still read it"
        )

    async def test_column_matches_the_returned_job_id(
        self, async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """What we persist must be the same id the 202 body advertises."""
        from api.services import sdg_service

        project = Project(id=uuid4(), name="proj", task_type=TaskType.QA)
        async_session.add(project)
        await async_session.flush()

        import workers.tasks.data_generation as dg_module

        fake_result = type("FakeAsyncResult", (), {"id": "job-consistency"})()
        monkeypatch.setattr(
            dg_module.generate_synthetic_data, "apply_async", lambda **kw: fake_result
        )

        response = await sdg_service.submit_sdg_job(
            async_session,
            SDGRequestDescriptionOnly(
                project_id=project.id,
                task_type=TaskType.QA,
                task_description="Answer questions about our return policy",
                num_samples=10,
                holdout_size=0,
            ),
        )

        dataset = await async_session.get(Dataset, response.dataset_id)
        assert response.job_id == dataset.celery_task_id
        assert response.websocket_url.endswith(dataset.celery_task_id)


# =============================================================================
# 4. Migration-level
# =============================================================================


@pytest.fixture(scope="module")
def source() -> str:
    return _MIGRATION.read_text(encoding="utf-8")


class TestMigration0006:
    def test_exists_and_chains_to_the_previous_tip(self, source: str) -> None:
        assert _MIGRATION.exists()
        assert 'revision: str = "0006_job_control_columns"' in source
        assert 'down_revision: str | None = "0005_project_external_id"' in source

    def test_does_not_recreate_the_shared_enum_type(self, source: str) -> None:
        """`job_status` is shared by four tables; re-creating it fails the upgrade."""
        assert "create_type=False" in source

    def test_adds_all_three_columns(self, source: str) -> None:
        for column in ("celery_task_id", "export_status", "export_celery_task_id"):
            assert column in source

    def test_backfills_the_dataset_column_from_jsonb(self, source: str) -> None:
        assert "UPDATE datasets" in source
        assert "generation_metadata" in source

    def test_backfill_avoids_the_jsonb_question_mark_operator(self, source: str) -> None:
        """`?` is SQLAlchemy's bind-param placeholder; the `->>` form needs no escaping."""
        assert "->>" in source

    def test_downgrade_does_not_drop_the_shared_enum(self, source: str) -> None:
        downgrade = source.split("def downgrade")[1]
        assert "DROP TYPE" not in downgrade.upper()
        assert "job_status" in downgrade, "expected a comment explaining why it is kept"


def test_artifact_format_enum_untouched() -> None:
    """Guard against collateral edits to a neighbouring public enum."""
    assert {f.value for f in ArtifactFormat} == {"lora", "gguf", "safetensors"}
