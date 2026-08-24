"""The platform-owned tag predicate generalizes beyond the `slm/` prefix.

`workers/tasks/model_export.py::_compute_ollama_tag` emits three tag shapes
depending on auth state and whether a training name was supplied:

* `{owner_id}/{name}`  — auth on
* `local/{name}`       — auth off, caller supplied a training name
* `slm/{hash8}`        — auth off, legacy unnamed export

`inference_service` used to treat only the `slm/` prefix as platform-owned,
so the other two shapes bypassed ownership filtering entirely (visible to
everyone in `list_models`, callable by anyone as a literal tag in
`_resolve_model_tag`). The fix generalizes the test to: platform-owned iff a
"/" appears before any ":" in the tag — which covers all three shapes and
still excludes Ollama's own library tags (`qwen2.5:0.5b`, `llama3.2:3b`,
`nomic-embed-text`), none of which are namespaced.

No live Ollama daemon anywhere in this file — pure functions and
monkeypatched `_get_json`/DB doubles only, same style as
`test_inference_tenancy.py`.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
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
from api.services import inference_service


# ---- KNOWN GOTCHA shim: JSONB is Postgres-only, sqlite needs a stand-in ----
@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


USER_A = CurrentUser(id="user-a-sub", email="a@example.com")
USER_B = CurrentUser(id="user-b-sub", email="b@example.com")


# =============================================================================
# 1. Pure predicate — all three platform shapes vs. library tags
# =============================================================================


@pytest.mark.parametrize(
    "tag",
    [
        "alice-sub/my-run",  # {owner_id}/{name}, auth on
        "local/my-run",  # local/{name}, auth off with a training name
        "slm/1a2b3c4d",  # slm/{hash8}, auth off legacy
    ],
)
class TestPlatformOwnedShapes:
    def test_is_platform_owned(self, tag: str) -> None:
        assert inference_service.is_platform_owned_tag(tag) is True

    def test_latest_suffix_is_canonicalized_away(self, tag: str) -> None:
        assert inference_service.canonical_our_tag(f"{tag}:latest") == tag

    def test_bare_tag_is_already_canonical(self, tag: str) -> None:
        assert inference_service.canonical_our_tag(tag) == tag


@pytest.mark.parametrize(
    "tag",
    [
        "qwen2.5:0.5b",
        "llama3.2:3b",
        "nomic-embed-text",
    ],
)
class TestLibraryTagsAreNotPlatformOwned:
    def test_is_not_platform_owned(self, tag: str) -> None:
        assert inference_service.is_platform_owned_tag(tag) is False

    def test_round_trips_unchanged(self, tag: str) -> None:
        """Canonicalization must be a no-op for tags outside every platform
        namespace — the colon here is a parameter-size/variant marker, not a
        version, and stripping it would conflate distinct models
        (`llama3.2:1b` vs `llama3.2:3b`)."""
        assert inference_service.canonical_our_tag(tag) == tag


# =============================================================================
# 2. DB-backed: the `local/{name}` shape gets the same enforcement as `slm/`
# =============================================================================


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


async def _chain(session: AsyncSession, owner_id: str | None, tag: str) -> ModelArtifact:
    """One project -> dataset -> training -> artifact chain, so the 2-hop
    ownership join `scope_models_to_owner` performs has something real to
    walk. (`TrainingJob.dataset_id` is NOT NULL, hence the dataset.)"""
    project = Project(
        id=uuid4(), name=f"p-{owner_id}-{tag}", task_type=TaskType.QA, owner_id=owner_id
    )
    session.add(project)
    await session.flush()
    dataset = Dataset(
        id=uuid4(),
        project_id=project.id,
        name=f"ds-{owner_id}-{tag}",
        task_type=TaskType.QA,
        source=DatasetSource.SEED,
        status=JobStatus.COMPLETED,
        num_samples=1,
    )
    session.add(dataset)
    await session.flush()
    training = TrainingJob(
        id=uuid4(),
        project_id=project.id,
        dataset_id=dataset.id,
        mode=TrainingMode.MANUAL,
        status=JobStatus.COMPLETED,
        base_model="unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
        config_json={},
    )
    session.add(training)
    await session.flush()
    artifact = ModelArtifact(
        id=uuid4(),
        training_job_id=training.id,
        name=f"m-{owner_id}-{tag}",
        base_model=training.base_model,
        lora_adapter_uri="s3://models/x",
        ollama_model_tag=tag,
    )
    session.add(artifact)
    await session.commit()
    return artifact


class TestLocalNamespaceGetsTheSameEnforcementAsSlm:
    """`local/{name}` is the auth-off, named-export shape. Before the fix it
    fell through the old `slm/`-only prefix test entirely, so it was neither
    filtered from the listing nor ownership-checked as a literal — the same
    hole `test_inference_tenancy.py` documents for `slm/`, just unpatched for
    this shape."""

    async def test_non_owner_gets_the_same_404_shaped_refusal(self, db) -> None:
        owned = await _chain(db, USER_A.id, "local/foo")

        # Same refusal shape as the slm/ path: caller B passes A's tag as a
        # literal and gets a 404 that names the tag, not the artifact's UUID.
        with pytest.raises(HTTPException) as exc:
            await inference_service._resolve_model_tag(db, "local/foo", USER_B)
        assert exc.value.status_code == 404
        assert str(owned.id) not in str(exc.value.detail)

        # The owner can still resolve their own tag.
        assert (
            await inference_service._resolve_model_tag(db, "local/foo", USER_A)
            == "local/foo"
        )

    async def test_unknown_local_tag_is_the_same_404_as_not_yours(self, db) -> None:
        """No-such-tag and not-yours must be indistinguishable for the
        `local/` shape too, or the 404 becomes an oracle."""
        await _chain(db, USER_A.id, "local/foo")

        with pytest.raises(HTTPException) as unknown:
            await inference_service._resolve_model_tag(db, "local/does-not-exist", USER_B)
        with pytest.raises(HTTPException) as not_yours:
            await inference_service._resolve_model_tag(db, "local/foo", USER_B)
        assert unknown.value.status_code == not_yours.value.status_code == 404
        assert unknown.value.detail.replace("local/does-not-exist", "X") == (
            not_yours.value.detail.replace("local/foo", "X")
        )

    async def test_owner_scoped_tag_also_gets_the_same_treatment(self, db) -> None:
        """`{owner_id}/{name}` (auth on) is the third shape — same refusal
        for a non-owner, same success for the owner."""
        await _chain(db, USER_A.id, "alice-sub/my-run")

        with pytest.raises(HTTPException) as exc:
            await inference_service._resolve_model_tag(db, "alice-sub/my-run", USER_B)
        assert exc.value.status_code == 404
        assert (
            await inference_service._resolve_model_tag(db, "alice-sub/my-run", USER_A)
            == "alice-sub/my-run"
        )


@pytest.fixture
def fake_ollama_mixed_shapes(monkeypatch):
    """The daemon reporting all three platform shapes plus a library tag —
    exactly the mix `_compute_ollama_tag` can actually produce across
    different auth states and export calls."""

    async def _get_json(path: str):
        return {
            "data": [
                {"id": "alice-sub/my-run", "created": 1, "owned_by": "slm-platform"},
                {"id": "local/foo", "created": 2, "owned_by": "slm-platform"},
                {"id": "slm/1a2b3c4d", "created": 3, "owned_by": "slm-platform"},
                {"id": "llama3.2:3b", "created": 4, "owned_by": "library"},
            ]
        }

    monkeypatch.setattr(inference_service, "_get_json", _get_json)


class TestListingFiltersAllThreeShapes:
    async def test_unowned_platform_tags_are_filtered_from_the_list(
        self, db, fake_ollama_mixed_shapes
    ) -> None:
        """USER_A owns none of the three platform-shaped tags the daemon
        reports here, so all three must be filtered out — only the library
        tag survives."""
        ids = {m.id for m in (await inference_service.list_models(db, USER_A)).data}
        assert ids == {"llama3.2:3b"}

    async def test_owner_still_sees_their_own_shape(
        self, db, fake_ollama_mixed_shapes
    ) -> None:
        await _chain(db, USER_A.id, "local/foo")
        ids = {m.id for m in (await inference_service.list_models(db, USER_A)).data}
        assert "local/foo" in ids
        assert "alice-sub/my-run" not in ids
        assert "slm/1a2b3c4d" not in ids
        assert "llama3.2:3b" in ids

    async def test_anonymous_listing_stays_unfiltered(
        self, db, fake_ollama_mixed_shapes
    ) -> None:
        """Phase-1 rule: `user is None` is a complete no-op, matching
        pre-auth behaviour."""
        ids = {m.id for m in (await inference_service.list_models(db, None)).data}
        assert ids == {"alice-sub/my-run", "local/foo", "slm/1a2b3c4d", "llama3.2:3b"}
