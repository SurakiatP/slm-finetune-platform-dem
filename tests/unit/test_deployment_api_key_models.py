"""Unit tests for the Deployment / ApiKey ORM models + their migration (T1).

Layers covered:
  1. Model-level  -- column shape/nullability/defaults, no relationship from
                     ModelArtifact, no api.core.auth import.
  2. Round-trip   -- in-memory sqlite: defaults persist; duplicate
                     `api_keys.key_hash` raises IntegrityError.
  3. Migration    -- source-text guards on 0015: revision/down_revision
                     literals, revision id length, job_status enum reuse
                     (create_type=False, no DROP TYPE on downgrade).

Runs entirely against in-memory sqlite -- no Postgres, no Docker. Neither
Deployment nor ApiKey has a JSONB column itself, but `Base.metadata` is
shared across every model in the app (Project.template_snapshot etc. do
have JSONB columns), so `create_all` still needs the same
`@compiles(JSONB, "sqlite")` shim as test_dataset_status.py /
test_dataset_owner_id_migration.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from api.models import ApiKey, Base, Deployment, ModelArtifact
from api.schemas.enums import JobStatus


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):
    return "JSON"


# =============================================================================
# 1. Model-level (no DB needed)
# =============================================================================


class TestDeploymentModelColumns:
    def test_owner_id_not_nullable_string_64_indexed(self) -> None:
        col = Deployment.__table__.columns["owner_id"]
        assert col.nullable is False
        assert col.type.length == 64
        assert col.index is True

    def test_model_artifact_id_nullable_fk_set_null_indexed(self) -> None:
        col = Deployment.__table__.columns["model_artifact_id"]
        assert col.nullable is True
        assert col.index is True
        (fk,) = col.foreign_keys
        assert fk.target_fullname == "model_artifacts.id"
        assert fk.ondelete == "SET NULL"

    def test_status_default_pending_indexed(self) -> None:
        col = Deployment.__table__.columns["status"]
        assert col.nullable is False
        assert col.index is True
        assert col.default is not None
        assert col.default.arg == JobStatus.PENDING

    def test_rate_limit_per_min_not_nullable(self) -> None:
        col = Deployment.__table__.columns["rate_limit_per_min"]
        assert col.nullable is False

    def test_celery_task_id_nullable_indexed(self) -> None:
        col = Deployment.__table__.columns["celery_task_id"]
        assert col.nullable is True
        assert col.index is True
        assert col.type.length == 64

    def test_error_message_nullable_4000(self) -> None:
        col = Deployment.__table__.columns["error_message"]
        assert col.nullable is True
        assert col.type.length == 4000

    def test_no_relationship_declared_on_model_artifact(self) -> None:
        # T1 owns "no relationship on ModelArtifact" -- deployments are
        # looked up by an explicit query, not an ORM relationship, so
        # deleting a ModelArtifact never cascades through the mapper.
        assert "deployments" not in ModelArtifact.__mapper__.relationships
        assert not Deployment.__mapper__.relationships.keys()

    def test_model_module_does_not_import_auth(self) -> None:
        import api.models.deployment as mod

        assert "api.core.auth" not in mod.__dict__
        source = Path(mod.__file__).read_text()
        assert "api.core.auth" not in source


class TestApiKeyModelColumns:
    def test_owner_id_not_nullable_indexed(self) -> None:
        col = ApiKey.__table__.columns["owner_id"]
        assert col.nullable is False
        assert col.type.length == 64
        assert col.index is True

    def test_key_hash_unique_not_nullable(self) -> None:
        col = ApiKey.__table__.columns["key_hash"]
        assert col.nullable is False
        assert col.unique is True
        assert col.type.length == 64

    def test_prefix_and_last4_shapes(self) -> None:
        assert ApiKey.__table__.columns["prefix"].type.length == 16
        assert ApiKey.__table__.columns["last4"].type.length == 4

    def test_revoked_at_and_last_used_at_nullable(self) -> None:
        assert ApiKey.__table__.columns["revoked_at"].nullable is True
        assert ApiKey.__table__.columns["last_used_at"].nullable is True

    def test_model_module_does_not_import_auth(self) -> None:
        import api.models.api_key as mod

        source = Path(mod.__file__).read_text()
        assert "api.core.auth" not in source


# =============================================================================
# 2. Round-trip against in-memory sqlite
# =============================================================================


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


class TestDeploymentRoundTrip:
    def test_defaults_round_trip(self, engine) -> None:
        with Session(engine) as db:
            deployment = Deployment(
                owner_id="user-a",
                name="my-deploy",
                rate_limit_per_min=60,
            )
            db.add(deployment)
            db.commit()

            got = db.get(Deployment, deployment.id)
            assert got is not None
            assert got.status == JobStatus.PENDING
            assert got.model_artifact_id is None
            assert got.celery_task_id is None
            assert got.error_message is None
            assert got.created_at is not None
            assert got.updated_at is not None


class TestApiKeyRoundTrip:
    def test_defaults_round_trip(self, engine) -> None:
        with Session(engine) as db:
            key = ApiKey(
                owner_id="user-a",
                name="my-key",
                key_hash="a" * 64,
                prefix="sk-slm-abcd",
                last4="wxyz",
            )
            db.add(key)
            db.commit()

            got = db.get(ApiKey, key.id)
            assert got is not None
            assert got.revoked_at is None
            assert got.last_used_at is None

    def test_duplicate_key_hash_raises_integrity_error(self, engine) -> None:
        with Session(engine) as db:
            db.add(ApiKey(owner_id="user-a", name="k1", key_hash="a" * 64, prefix="sk-slm-aaaa", last4="1111"))
            db.commit()

            db.add(ApiKey(owner_id="user-b", name="k2", key_hash="a" * 64, prefix="sk-slm-bbbb", last4="2222"))
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()


# =============================================================================
# 3. Migration source guards (no DB needed)
# =============================================================================


class TestMigrationSourceGuards:
    _PATH = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260927_0015_deployments_api_keys.py"
    )

    def test_migration_file_exists(self) -> None:
        assert self._PATH.is_file()

    def test_revision_and_down_revision_literals(self) -> None:
        source = self._PATH.read_text()
        assert 'revision: str = "0015_deployments_api_keys"' in source
        assert 'down_revision: str | None = "0014_template_marketplace"' in source

    def test_revision_id_fits_alembic_version_column(self) -> None:
        assert len("0015_deployments_api_keys") <= 32

    def test_job_status_enum_reused_not_recreated(self) -> None:
        source = self._PATH.read_text()
        assert 'name="job_status"' in source
        assert "create_type=False" in source

    def test_downgrade_does_not_drop_job_status_enum_type(self) -> None:
        source = self._PATH.read_text()
        downgrade_body = source.split("def downgrade()")[1]
        assert "DROP TYPE" not in downgrade_body.upper()
        assert "job_status" in downgrade_body.lower()  # explanatory comment present

    def test_downgrade_drops_both_tables(self) -> None:
        source = self._PATH.read_text()
        downgrade_body = source.split("def downgrade()")[1]
        assert 'op.drop_table("deployments")' in downgrade_body
        assert 'op.drop_table("api_keys")' in downgrade_body
