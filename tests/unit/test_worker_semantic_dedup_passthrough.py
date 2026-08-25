"""Unit tests proving `generate_synthetic_data` forwards the semantic-dedup
config (`settings.sdg_embedding_model` / `settings.sdg_embedding_dedup_threshold`)
to `SyntheticDataGenerator`'s constructor, and that the price-resolution set
only includes the embedding model when it's actually configured (W2-T7).

Runs the Celery task synchronously via `.apply(...)` against an in-memory
sync sqlite engine, mirroring the established pattern in
`tests/unit/test_worker_sdg_insights.py` / `tests/unit/test_worker_usage_events.py`
(same fixtures, same `_install_worker_patches`-style monkeypatching of
`session_scope` / `sync_redis_scope` / `get_minio_client` at the worker
module's own use-sites, same JSONB->JSON sqlite compile shim). The
`_install_worker_patches` / `_seed_project_and_dataset` /
`_build_description_only_payload` helpers are copied rather than imported
across test modules, matching repo convention.

`SyntheticDataGenerator` itself is monkeypatched to a recording stand-in
here (rather than `_run_generator`, which is what the sibling suites patch)
because the thing under test IS the call site that constructs
`SyntheticDataGenerator` — patching `_run_generator` away would hide it.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from ai_engine.data_gen.generator import SDGRunResult
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.project import Project
from api.schemas.enums import DatasetSource, JobStatus, TaskType
from api.schemas.sdg import SDGRequestDescriptionOnly

# ---- KNOWN GOTCHA shim: JSONB is Postgres-only, sqlite needs a stand-in ----
# (same shim as tests/unit/test_dataset_status.py / test_worker_sdg_insights.py
# — Dataset/Project use sqlalchemy.dialects.postgresql.JSONB, which sqlite's
# DDL compiler doesn't understand without this.)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


# =============================================================================
# Fixtures / shared helpers (copied from test_worker_sdg_insights.py)
# =============================================================================


@pytest.fixture
def sync_sessionmaker():
    """In-memory sync sqlite engine + sessionmaker with the full ORM schema."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    yield maker
    engine.dispose()


def _install_worker_patches(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub):
    """Same use-site patching as test_worker_sdg_insights.py's
    `_install_worker_patches`: patch `session_scope` / `sync_redis_scope` /
    `get_minio_client` on `workers.tasks.data_generation` itself, not on the
    modules they were imported from — the module did `from X import Y`,
    which binds a local alias that patching `X.Y` afterwards doesn't reach.
    """
    import workers.tasks.data_generation as dg_module

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

    @contextmanager
    def _fake_redis_scope():
        yield fake_redis_pubsub.client

    monkeypatch.setattr(dg_module, "session_scope", _fake_session_scope)
    monkeypatch.setattr(dg_module, "sync_redis_scope", _fake_redis_scope)
    monkeypatch.setattr(dg_module, "get_minio_client", lambda: fake_minio)
    return dg_module


def _seed_project_and_dataset(sync_sessionmaker, *, project_id, dataset_id, status=JobStatus.PENDING):
    session = sync_sessionmaker()
    try:
        project = Project(id=project_id, name="proj", task_type=TaskType.QA)
        session.add(project)
        dataset = Dataset(
            id=dataset_id,
            project_id=project_id,
            name="sdg-ds",
            task_type=TaskType.QA,
            source=DatasetSource.SDG,
            status=status,
            num_samples=0,
        )
        session.add(dataset)
        session.commit()
    finally:
        session.close()


def _build_description_only_payload(project_id, *, num_samples=1, holdout_size=0) -> dict:
    request = SDGRequestDescriptionOnly(
        project_id=project_id,
        task_type=TaskType.QA,
        task_description="Answer questions about our 30-day return policy",
        num_samples=num_samples,
        holdout_size=holdout_size,
    )
    return request.model_dump(mode="json")


# =============================================================================
# Recording stand-in for SyntheticDataGenerator
# =============================================================================


class _CapturingGenerator:
    """Records the kwargs `_run_generator` constructs it with, and the kwargs
    passed to `.generate(...)`, then returns a minimal `SDGRunResult` without
    ever touching the network (no real OpenRouter calls happen here).

    Class-level storage (not instance-level) because the test only has
    access to the Celery task's *return value*, not the `SyntheticDataGenerator`
    instance it builds internally.
    """

    last_init_kwargs: dict[str, Any] | None = None
    last_generate_kwargs: dict[str, Any] | None = None

    @classmethod
    def reset(cls) -> None:
        cls.last_init_kwargs = None
        cls.last_generate_kwargs = None

    def __init__(self, async_client, sync_client, **kwargs: Any) -> None:
        type(self).last_init_kwargs = kwargs
        self._async_client = async_client
        self._sync_client = sync_client

    async def generate(self, request, **kwargs: Any) -> SDGRunResult:
        type(self).last_generate_kwargs = kwargs
        return SDGRunResult(
            valid_rows=[{"question": "q", "answer": "a"}],
            rejected_count=0,
            duplicate_count=0,
            semantic_duplicate_count=0,
            judge_rejected_count=0,
            judge_parse_failures=0,
            api_calls=0,
        )


def _run_task(dg_module, *, project_id, dataset_id, sync_sessionmaker) -> None:
    _seed_project_and_dataset(sync_sessionmaker, project_id=project_id, dataset_id=dataset_id)
    payload = _build_description_only_payload(project_id, num_samples=1, holdout_size=0)
    result = dg_module.generate_synthetic_data.apply(
        kwargs={"request_payload": payload, "dataset_id": str(dataset_id)}
    )
    assert result.successful(), f"task raised: {result.result!r}"


# =============================================================================
# 1. Configured embedding model/threshold are forwarded, and priced
# =============================================================================


class TestEmbeddingConfigForwardedWhenEnabled:
    def test_forwards_configured_embedding_model_and_threshold(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )
        _CapturingGenerator.reset()
        monkeypatch.setattr(dg_module, "SyntheticDataGenerator", _CapturingGenerator)

        real_settings = dg_module.get_settings()
        configured_settings = real_settings.model_copy(
            update={
                # Non-empty so `AsyncOpenRouterClient`/`OpenRouterClient`
                # construction (which happens for real in `_run_generator`,
                # only `SyntheticDataGenerator` itself is faked) doesn't
                # raise `ValueError("OPENROUTER_API_KEY is empty...")`.
                "openrouter_api_key": "test-key-not-real",
                "sdg_embedding_model": "openai/text-embedding-3-small",
                "sdg_embedding_dedup_threshold": 0.87,
            }
        )
        monkeypatch.setattr(dg_module, "get_settings", lambda: configured_settings)
        # Deterministic non-None price for every model id, so the assertion
        # on `usage._prices` below isn't hostage to whatever the real
        # pricing map happens to contain for this particular model string.
        monkeypatch.setattr(
            dg_module.model_pricing, "price_for", lambda model_id: (0.001, 0.002)
        )

        project_id = uuid4()
        dataset_id = uuid4()
        _run_task(dg_module, project_id=project_id, dataset_id=dataset_id, sync_sessionmaker=sync_sessionmaker)

        assert _CapturingGenerator.last_init_kwargs is not None
        assert (
            _CapturingGenerator.last_init_kwargs["embedding_model"]
            == "openai/text-embedding-3-small"
        )
        assert _CapturingGenerator.last_init_kwargs["embedding_dedup_threshold"] == 0.87

        assert _CapturingGenerator.last_generate_kwargs is not None
        usage = _CapturingGenerator.last_generate_kwargs["usage"]
        assert usage is not None
        assert usage._prices["openai/text-embedding-3-small"] == (0.001, 0.002)


# =============================================================================
# 2. Disabled ("") is still forwarded as-is, and excluded from the price map
# =============================================================================


class TestEmbeddingDisabledIsForwardedButUnpriced:
    def test_empty_embedding_model_forwarded_and_excluded_from_prices(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )
        _CapturingGenerator.reset()
        monkeypatch.setattr(dg_module, "SyntheticDataGenerator", _CapturingGenerator)

        real_settings = dg_module.get_settings()
        configured_settings = real_settings.model_copy(
            update={
                "openrouter_api_key": "test-key-not-real",
                "sdg_embedding_model": "",
                "sdg_embedding_dedup_threshold": 0.9,
            }
        )
        monkeypatch.setattr(dg_module, "get_settings", lambda: configured_settings)
        # Would price ANY model id, including "" — if the worker's `if
        # settings.sdg_embedding_model:` guard were missing/broken, this
        # would make "" show up in `usage._prices` and fail the assertion
        # below. A price map that simply has no entry for "" would pass
        # for the wrong reason; forcing a hit proves the guard is what's
        # keeping it out.
        monkeypatch.setattr(
            dg_module.model_pricing, "price_for", lambda model_id: (0.001, 0.002)
        )

        project_id = uuid4()
        dataset_id = uuid4()
        _run_task(dg_module, project_id=project_id, dataset_id=dataset_id, sync_sessionmaker=sync_sessionmaker)

        assert _CapturingGenerator.last_init_kwargs is not None
        # "" (disabled) is forwarded as-is, not omitted or swapped for None —
        # `SyntheticDataGenerator` is the layer that interprets "disabled".
        assert _CapturingGenerator.last_init_kwargs["embedding_model"] == ""
        assert _CapturingGenerator.last_init_kwargs["embedding_dedup_threshold"] == 0.9

        assert _CapturingGenerator.last_generate_kwargs is not None
        usage = _CapturingGenerator.last_generate_kwargs["usage"]
        assert usage is not None
        assert "" not in usage._prices


__all__: list[str] = []
