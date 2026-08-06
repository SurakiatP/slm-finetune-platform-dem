"""Inference tenancy — the `slm/` namespace is owner-scoped on BOTH paths.

The hole this closes needed two halves to be exploitable, and the codebase
had both: `GET /inference/models` published every tag the shared Ollama
daemon knows about, and `_resolve_model_tag` accepted any literal tag without
a check (it only verified ownership when the caller passed a ModelArtifact
UUID). So user B read `slm/<8hex>` off the listing and ran inference on user
A's private fine-tune.

Closing only the listing would have been theatre — the tags are 8 hex chars
and were being handed out anyway. These tests therefore assert both halves,
and assert that base models (which map to no row of ours and so have no
owner) stay visible and callable for everyone.

In-memory aiosqlite only — no Ollama, no Postgres, no GPU.
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

# What the daemon reports: A's export, B's export, and a base model that was
# pulled straight in and belongs to nobody.
TAG_A = "slm/aaaaaaaa"
TAG_B = "slm/bbbbbbbb"
TAG_BASE = "llama3.2:3b"


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
        id=uuid4(), name=f"p-{owner_id}", task_type=TaskType.QA, owner_id=owner_id
    )
    session.add(project)
    await session.flush()
    dataset = Dataset(
        id=uuid4(),
        project_id=project.id,
        name=f"ds-{owner_id}",
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
        name=f"m-{owner_id}",
        base_model=training.base_model,
        lora_adapter_uri="s3://models/x",
        ollama_model_tag=tag,
    )
    session.add(artifact)
    await session.commit()
    return artifact


@pytest.fixture
async def seeded(db):
    a = await _chain(db, USER_A.id, TAG_A)
    b = await _chain(db, USER_B.id, TAG_B)
    return {"a": a, "b": b}


@pytest.fixture
def fake_ollama(monkeypatch):
    """Stub the daemon's /v1/models listing — every tag is present, which is
    exactly the pre-fix situation the filter has to correct."""

    async def _get_json(path: str):
        return {
            "data": [
                {"id": TAG_A, "created": 1, "owned_by": "slm-platform"},
                {"id": TAG_B, "created": 2, "owned_by": "slm-platform"},
                {"id": TAG_BASE, "created": 3, "owned_by": "library"},
            ]
        }

    monkeypatch.setattr(inference_service, "_get_json", _get_json)


# =============================================================================
# 1. The literal-tag path — the half that actually enforced nothing
# =============================================================================


class TestLiteralTagOwnership:
    async def test_b_cannot_resolve_as_literal_tag(self, db, seeded) -> None:
        """THE HOLE. B passes A's tag as a literal string, not a UUID."""
        with pytest.raises(HTTPException) as exc:
            await inference_service._resolve_model_tag(db, TAG_A, USER_B)
        assert exc.value.status_code == 404

    async def test_owner_can_resolve_own_literal_tag(self, db, seeded) -> None:
        assert await inference_service._resolve_model_tag(db, TAG_A, USER_A) == TAG_A

    async def test_unknown_slm_tag_is_the_same_404(self, db, seeded) -> None:
        """No-such-tag and not-yours must be indistinguishable, or the 404
        becomes an oracle for enumerating which artifacts exist."""
        with pytest.raises(HTTPException) as unknown:
            await inference_service._resolve_model_tag(db, "slm/deadbeef", USER_B)
        with pytest.raises(HTTPException) as not_yours:
            await inference_service._resolve_model_tag(db, TAG_A, USER_B)
        assert unknown.value.status_code == not_yours.value.status_code == 404
        assert unknown.value.detail.replace("slm/deadbeef", "X") == (
            not_yours.value.detail.replace(TAG_A, "X")
        )

    async def test_base_model_passes_through_for_everyone(self, db, seeded) -> None:
        """Not ours, no owner to check — and blocking it would break the
        daemon's own catalogue."""
        for user in (USER_A, USER_B):
            assert (
                await inference_service._resolve_model_tag(db, TAG_BASE, user)
                == TAG_BASE
            )

    async def test_uuid_branch_still_ownership_checked(self, db, seeded) -> None:
        with pytest.raises(HTTPException) as exc:
            await inference_service._resolve_model_tag(db, str(seeded["a"].id), USER_B)
        assert exc.value.status_code == 404
        assert (
            await inference_service._resolve_model_tag(db, str(seeded["a"].id), USER_A)
            == TAG_A
        )

    async def test_anonymous_is_a_complete_no_op(self, db, seeded) -> None:
        """Phase-1 rule: `user is None` behaves exactly as before auth existed.
        smart-model-tune is still anonymous today — breaking this breaks the
        only live client."""
        assert await inference_service._resolve_model_tag(db, TAG_A, None) == TAG_A
        assert await inference_service._resolve_model_tag(db, TAG_B, None) == TAG_B
        assert await inference_service._resolve_model_tag(db, TAG_BASE, None) == TAG_BASE


# =============================================================================
# 2. The listing — the half that published the tags in the first place
# =============================================================================


class TestListingFilter:
    async def test_each_user_sees_only_their_own_slm_tag(
        self, db, seeded, fake_ollama
    ) -> None:
        a_tags = {m.id for m in (await inference_service.list_models(db, USER_A)).data}
        b_tags = {m.id for m in (await inference_service.list_models(db, USER_B)).data}
        assert TAG_A in a_tags and TAG_B not in a_tags
        assert TAG_B in b_tags and TAG_A not in b_tags

    async def test_base_model_visible_to_both(self, db, seeded, fake_ollama) -> None:
        for user in (USER_A, USER_B):
            tags = {m.id for m in (await inference_service.list_models(db, user)).data}
            assert TAG_BASE in tags

    async def test_anonymous_listing_is_unfiltered(
        self, db, seeded, fake_ollama
    ) -> None:
        tags = {m.id for m in (await inference_service.list_models(db, None)).data}
        assert tags == {TAG_A, TAG_B, TAG_BASE}

    async def test_null_owner_artifact_is_hidden_from_everyone(
        self, db, fake_ollama
    ) -> None:
        """A pre-auth project owns nobody's rows — null owner_id fails closed
        rather than reading as public (Project.owner_id's documented rule)."""
        await _chain(db, None, TAG_A)
        tags = {m.id for m in (await inference_service.list_models(db, USER_A)).data}
        assert TAG_A not in tags
        assert TAG_BASE in tags


# =============================================================================
# 3. The two halves are only safe together
# =============================================================================


class TestBothHalvesClosed:
    async def test_tag_read_from_a_listing_is_still_refused(
        self, db, seeded, fake_ollama
    ) -> None:
        """Simulates the original exploit end to end: B reads the tag list,
        picks a tag it does not own, and passes it as a literal. Even if the
        listing regressed to unfiltered, the resolve path must still refuse."""
        published = {m.id for m in (await inference_service.list_models(db, None)).data}
        stolen = next(t for t in published if t.startswith("slm/") and t != TAG_B)
        with pytest.raises(HTTPException) as exc:
            await inference_service._resolve_model_tag(db, stolen, USER_B)
        assert exc.value.status_code == 404


# =============================================================================
# 4. Source guard — the docstring must not drift back
# =============================================================================


def test_module_docstring_documents_the_current_behaviour() -> None:
    """The previous version of this docstring argued FOR an unfiltered
    listing, i.e. the exact opposite of the code beneath it. A docstring that
    contradicts its own code is how the next person "fixes" the code back."""
    doc = inference_service.__doc__ or ""
    assert "left UNFILTERED" not in doc
    assert "filters `slm/` tags to the caller's own artifacts" in doc


# =============================================================================
# 5. The daemon's `:latest` suffix — what the fake above was hiding
# =============================================================================


@pytest.fixture
def real_ollama(monkeypatch):
    """What the daemon ACTUALLY reports.

    `ollama create slm/aaaaaaaa` is stored and listed back as
    `slm/aaaaaaaa:latest` — the implicit version is appended on create. The
    `fake_ollama` fixture above returns bare tags, which is why every test in
    this file passed while the owner's own model was being filtered out of
    their listing on the real box. Base models keep their tag: `llama3.2:3b`'s
    suffix is a parameter size, not a version.
    """

    async def _get_json(path: str):
        return {
            "data": [
                {"id": f"{TAG_A}:latest", "created": 1, "owned_by": "slm-platform"},
                {"id": f"{TAG_B}:latest", "created": 2, "owned_by": "slm-platform"},
                {"id": TAG_BASE, "created": 3, "owned_by": "library"},
            ]
        }

    monkeypatch.setattr(inference_service, "_get_json", _get_json)


class TestCanonicalTag:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("slm/aaaaaaaa:latest", "slm/aaaaaaaa"),
            ("slm/aaaaaaaa", "slm/aaaaaaaa"),
            ("llama3.2:3b", "llama3.2:3b"),
            ("llama3.2:1b", "llama3.2:1b"),
            ("qwen2.5:0.5b-instruct", "qwen2.5:0.5b-instruct"),
        ],
    )
    def test_only_our_namespace_is_normalised(self, given: str, expected: str) -> None:
        """Stripping the suffix outside `slm/` would conflate `llama3.2:1b`
        with `llama3.2:3b` — two different models."""
        assert inference_service.canonical_our_tag(given) == expected


class TestOwnerCanActuallyUseTheirOwnModel:
    """The tenancy filter is only correct if the owner still gets through it.

    Filtering B out while also filtering A out is not "secure", it is broken:
    `GET /inference/models` is the endpoint the frontend integration doc
    points at for populating its model picker.
    """

    async def test_owner_sees_their_own_fine_tune_in_the_listing(
        self, db, seeded, real_ollama
    ) -> None:
        ids = {m.id for m in (await inference_service.list_models(db, USER_A)).data}
        assert TAG_A in ids, f"the owner's own model vanished from their listing: {ids}"

    async def test_the_listing_publishes_the_form_the_call_path_accepts(
        self, db, seeded, real_ollama
    ) -> None:
        """A picker's value has to round-trip. Publishing `slm/x:latest` while
        the resolver only matches `slm/x` would 404 the owner on their own
        model, one click after the listing showed it to them."""
        listed = [m.id for m in (await inference_service.list_models(db, USER_A)).data]
        ours = next(t for t in listed if t.startswith("slm/"))
        assert await inference_service._resolve_model_tag(db, ours, USER_A) == TAG_A

    async def test_suffixed_tag_resolves_for_the_owner(self, db, seeded) -> None:
        """A caller that copied the id out of `ollama list` carries `:latest`."""
        resolved = await inference_service._resolve_model_tag(
            db, f"{TAG_A}:latest", USER_A
        )
        assert resolved == TAG_A

    async def test_suffix_is_not_a_way_around_the_ownership_check(
        self, db, seeded
    ) -> None:
        """Normalisation must not become a bypass: B appending `:latest` to
        A's tag is the same refusal as before."""
        with pytest.raises(HTTPException) as exc:
            await inference_service._resolve_model_tag(db, f"{TAG_A}:latest", USER_B)
        assert exc.value.status_code == 404
        assert str(seeded["a"].id) not in str(exc.value.detail)

    async def test_b_still_does_not_see_a_in_the_suffixed_listing(
        self, db, seeded, real_ollama
    ) -> None:
        ids = {m.id for m in (await inference_service.list_models(db, USER_B)).data}
        assert TAG_B in ids
        assert TAG_A not in ids
        assert f"{TAG_A}:latest" not in ids

    async def test_base_model_survives_normalisation(
        self, db, seeded, real_ollama
    ) -> None:
        ids = {m.id for m in (await inference_service.list_models(db, USER_A)).data}
        assert TAG_BASE in ids
