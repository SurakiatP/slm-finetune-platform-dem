"""W2-T4: the ownership behaviour matrix for `Dataset.owner_id`, direct.

`ownership.py` used to resolve a dataset's owner by a 1-hop join through
`Project.owner_id`. That join is gone: `assert_dataset_access` and
`scope_datasets_to_owner` now read `Dataset.owner_id` off the row itself
(see `Dataset.owner_id`'s own `doc=` in `api/models/dataset.py` — it is
copied from the owning `Project` at creation time and, critically, is NOT
cleared when the project is deleted).

The one behaviour this file exists to pin down: an **orphaned** dataset
(`project_id IS NULL`, e.g. because its project was deleted) must stay
fully reachable by its owner, across every access path a dataset has —
scoped list, by-id `assert_dataset_access`, preview, download-url, and
delete — and stay a clean 403 for everyone else, exactly like a
non-orphaned dataset would. A `Project`-join implementation would 500 or
silently drop these rows the moment `project_id` goes NULL; this file
would catch that regression immediately.

Covered, per access path:
  * scoped list (`ownership.scope_datasets_to_owner`)
  * by-id load (`ownership.assert_dataset_access`)
  * preview (`datasets_service.preview_dataset`)
  * download-url (`download_links.mint_dataset_download_url`)
  * delete (`datasets_service.delete_dataset`)

against three dataset states: owned-and-orphaned, owned-by-someone-else,
and owner_id IS NULL (fails closed — belongs to nobody, not everybody) —
plus the `user is None` (auth disabled) no-op, asserted as an identity
check on `scope_datasets_to_owner`, not just an equality one.

Same in-memory aiosqlite + `@compiles(JSONB, "sqlite")` shim pattern as
`tests/unit/test_dataset_status.py`. The MinIO-touching paths (preview,
download-url, delete) use the `fake_minio` fixture from `tests/conftest.py`
and patch it in at each service module's own import site — both
`datasets_service.py` and `download_links.py` did
`from workers.storage import get_minio_client, ...`, binding a local name
at import time, so patching `workers.storage.get_minio_client` itself
(what `fake_minio` does) does not reach either module's copy. Same
import-by-name trap `test_download_links.py` and
`test_dataset_decouple_api.py` both document.
"""

from __future__ import annotations

from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from api.core.auth import CurrentUser
from api.core.config import get_settings
from api.models.base import Base
from api.models.dataset import Dataset
from api.schemas.enums import DatasetSource, JobStatus, TaskType
from api.services import datasets_service, download_links, ownership
from workers.storage import s3_uri


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


USER_A = CurrentUser(id="user-a-sub", email="a@example.com")
USER_B = CurrentUser(id="user-b-sub", email="b@example.com")

_BUCKET = "datasets"


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


async def _orphan_dataset(
    db: AsyncSession,
    *,
    owner: str | None,
    tag: str,
    storage_uri: str | None = None,
) -> Dataset:
    """A dataset with `project_id IS NULL` — as if its project was deleted
    out from under it (W1-T1's `ondelete="SET NULL"`). No `Project` row is
    ever created in this file: the whole point is that datasets must not
    need one.
    """
    dataset = Dataset(
        id=uuid4(),
        project_id=None,
        owner_id=owner,
        name=f"orphan-{tag}",
        task_type=TaskType.QA,
        source=DatasetSource.SEED,
        status=JobStatus.COMPLETED,
        num_samples=1,
        storage_uri=storage_uri,
    )
    db.add(dataset)
    await db.commit()
    return dataset


# =============================================================================
# 1. Scoped list — ownership.scope_datasets_to_owner
# =============================================================================


class TestScopedList:
    async def test_owner_sees_their_orphan_in_the_scoped_list(self, db: AsyncSession) -> None:
        mine = await _orphan_dataset(db, owner=USER_A.id, tag="a")
        theirs = await _orphan_dataset(db, owner=USER_B.id, tag="b")

        stmt = ownership.scope_datasets_to_owner(select(Dataset.id), USER_A)
        ids = {r[0] for r in (await db.execute(stmt)).all()}

        assert ids == {mine.id}
        assert theirs.id not in ids

    async def test_null_owner_dataset_is_absent_from_every_authenticated_list(
        self, db: AsyncSession
    ) -> None:
        nobody_s = await _orphan_dataset(db, owner=None, tag="null")

        for user in (USER_A, USER_B):
            stmt = ownership.scope_datasets_to_owner(select(Dataset.id), user)
            ids = {r[0] for r in (await db.execute(stmt)).all()}
            assert nobody_s.id not in ids, f"{user.id} saw a null-owner dataset"

    async def test_user_none_sees_everything_unchanged(self, db: AsyncSession) -> None:
        mine = await _orphan_dataset(db, owner=USER_A.id, tag="a")
        theirs = await _orphan_dataset(db, owner=USER_B.id, tag="b")
        nobody_s = await _orphan_dataset(db, owner=None, tag="null")

        stmt = ownership.scope_datasets_to_owner(select(Dataset.id), None)
        ids = {r[0] for r in (await db.execute(stmt)).all()}

        assert {mine.id, theirs.id, nobody_s.id} <= ids

    def test_user_none_is_the_identity_no_op(self) -> None:
        """Not just equal SQL — the *same statement object*, unchanged.

        `ownership.py`'s module docstring commits to this explicitly: the
        `user is None` branch returns the statement untouched rather than
        adding a harmless-looking filter, so phase-1 callers get
        byte-for-byte identical SQL. `is`, not `==`, is the only assertion
        that can catch a rewrite that happens to produce an
        equivalent-looking `Select`.
        """
        stmt = select(Dataset.id)
        assert ownership.scope_datasets_to_owner(stmt, None) is stmt


# =============================================================================
# 2. By-id load — ownership.assert_dataset_access
# =============================================================================


class TestAssertDatasetAccess:
    async def test_owner_can_load_their_orphan(self, db: AsyncSession) -> None:
        mine = await _orphan_dataset(db, owner=USER_A.id, tag="a")
        got = await ownership.assert_dataset_access(db, mine.id, USER_A)
        assert got.id == mine.id

    async def test_non_owner_gets_403_on_an_orphan(self, db: AsyncSession) -> None:
        mine = await _orphan_dataset(db, owner=USER_A.id, tag="a")
        with pytest.raises(HTTPException) as exc:
            await ownership.assert_dataset_access(db, mine.id, USER_B)
        assert exc.value.status_code == 403

    async def test_null_owner_orphan_is_403_for_both_users(self, db: AsyncSession) -> None:
        nobody_s = await _orphan_dataset(db, owner=None, tag="null")
        for user in (USER_A, USER_B):
            with pytest.raises(HTTPException) as exc:
                await ownership.assert_dataset_access(db, nobody_s.id, user)
            assert exc.value.status_code == 403

    async def test_user_none_is_a_no_op(self, db: AsyncSession) -> None:
        mine = await _orphan_dataset(db, owner=USER_A.id, tag="a")
        got = await ownership.assert_dataset_access(db, mine.id, None)
        assert got.id == mine.id


# =============================================================================
# 3. Preview — datasets_service.preview_dataset
# =============================================================================


class TestPreviewPath:
    @pytest.fixture
    def patch_datasets_service_minio(self, monkeypatch: pytest.MonkeyPatch, fake_minio):
        # Import-by-name trap: datasets_service.py did
        # `from workers.storage import get_minio_client`, so the patch has
        # to target datasets_service's own copy of the name.
        monkeypatch.setattr(datasets_service, "get_minio_client", lambda: fake_minio)
        return fake_minio

    async def test_owner_can_preview_their_orphan(
        self, db: AsyncSession, patch_datasets_service_minio
    ) -> None:
        uri = s3_uri(_BUCKET, "orphan/rows.jsonl")
        patch_datasets_service_minio.put_object(
            _BUCKET, "orphan/rows.jsonl", BytesIO(b'{"q": "hi", "a": "bye"}\n'), length=25
        )
        mine = await _orphan_dataset(db, owner=USER_A.id, tag="a", storage_uri=uri)

        resp = await datasets_service.preview_dataset(db, mine.id, 10, USER_A)

        assert resp.dataset_id == mine.id
        assert resp.samples == [{"q": "hi", "a": "bye"}]

    async def test_non_owner_gets_403_on_preview(
        self, db: AsyncSession, patch_datasets_service_minio
    ) -> None:
        uri = s3_uri(_BUCKET, "orphan/rows.jsonl")
        patch_datasets_service_minio.put_object(
            _BUCKET, "orphan/rows.jsonl", BytesIO(b'{"q": "hi", "a": "bye"}\n'), length=25
        )
        mine = await _orphan_dataset(db, owner=USER_A.id, tag="a", storage_uri=uri)

        with pytest.raises(HTTPException) as exc:
            await datasets_service.preview_dataset(db, mine.id, 10, USER_B)
        assert exc.value.status_code == 403


# =============================================================================
# 4. Download-url — download_links.mint_dataset_download_url
# =============================================================================


class TestDownloadUrlPath:
    @pytest.fixture
    def presigned_url_configured(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MINIO_PUBLIC_URL", "https://storage.example.com")
        monkeypatch.setenv("PRESIGNED_URL_TTL_SECONDS", "300")
        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    @pytest.fixture
    def patch_download_links_minio(
        self, monkeypatch: pytest.MonkeyPatch, fake_minio, presigned_url_configured
    ):
        # Same import-by-name trap as datasets_service, documented at the
        # top of test_download_links.py.
        monkeypatch.setattr(download_links, "get_presign_client", lambda: fake_minio)
        monkeypatch.setattr(download_links, "get_minio_client", lambda: fake_minio)
        return fake_minio

    async def test_owner_gets_a_download_url_for_their_orphan(
        self, db: AsyncSession, patch_download_links_minio
    ) -> None:
        uri = s3_uri(_BUCKET, "orphan/rows.jsonl")
        mine = await _orphan_dataset(db, owner=USER_A.id, tag="a", storage_uri=uri)

        resp = await download_links.mint_dataset_download_url(db, mine.id, USER_A)

        assert "fake-presigned.example.com" in resp.url

    async def test_non_owner_gets_403_on_download_url(
        self, db: AsyncSession, patch_download_links_minio
    ) -> None:
        uri = s3_uri(_BUCKET, "orphan/rows.jsonl")
        mine = await _orphan_dataset(db, owner=USER_A.id, tag="a", storage_uri=uri)

        with pytest.raises(HTTPException) as exc:
            await download_links.mint_dataset_download_url(db, mine.id, USER_B)
        assert exc.value.status_code == 403


# =============================================================================
# 5. Delete — datasets_service.delete_dataset
# =============================================================================


class TestDeletePath:
    @pytest.fixture
    def patch_datasets_service_minio(self, monkeypatch: pytest.MonkeyPatch, fake_minio):
        monkeypatch.setattr(datasets_service, "get_minio_client", lambda: fake_minio)
        return fake_minio

    async def test_owner_can_delete_their_orphan(
        self, db: AsyncSession, patch_datasets_service_minio
    ) -> None:
        mine = await _orphan_dataset(db, owner=USER_A.id, tag="a")

        await datasets_service.delete_dataset(db, mine.id, USER_A)

        gone = await db.get(Dataset, mine.id)
        assert gone is None

    async def test_non_owner_gets_403_on_delete_and_row_survives(
        self, db: AsyncSession, patch_datasets_service_minio
    ) -> None:
        mine = await _orphan_dataset(db, owner=USER_A.id, tag="a")

        with pytest.raises(HTTPException) as exc:
            await datasets_service.delete_dataset(db, mine.id, USER_B)
        assert exc.value.status_code == 403

        still_there = await db.get(Dataset, mine.id)
        assert still_there is not None
