"""Layer-2 presign guards: a REAL `Minio` client, signing offline.

`tests/unit/test_download_links.py` covers the policy layer (ownership,
audit rows, 503s, listing caps) against `_FakeMinio`. That fake can never
exhibit the bugs that actually break this feature on real infrastructure,
because it does not sign anything: the signature is computed from the Host
and the canonical URI, and the fake has neither. So the two classes of bug
that only hardware would otherwise reveal —

  * signing against `minio:9000` instead of the public storage host, and
  * omitting `region=` so `minio-py` fires a live `GetBucketLocation`

— are invisible to every test in that file. This module closes that hole by
driving the genuine `minio.Minio` object with the network amputated.

Deliberately a separate file from `test_download_links.py`: these tests do
not touch the database, the app, or any fixture from `conftest.py`, and
mixing them in would tempt someone to reach for the `patch_presign` fixture,
which is precisely what must NOT be used here.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from minio import Minio

from api.core.config import Settings
from workers import storage

_PUBLIC_HOST = "storage.example.com"
_BUCKET = "datasets"
_KEY = "seeds/1c2f5a90-0000-4000-8000-000000000001.jsonl"


def _settings(**overrides) -> Settings:
    base = {
        "database_url": "postgresql+asyncpg://user:pw@postgres:5432/slm",
        "minio_access_key": "AKIAEXAMPLEEXAMPLE",
        "minio_secret_key": "s3cr3t-example-key-material-not-real",
        "minio_public_url": f"https://{_PUBLIC_HOST}",
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def presign_client(monkeypatch: pytest.MonkeyPatch) -> Minio:
    """The real client `get_presign_client()` builds, with settings injected.

    Patches the name as `workers.storage` bound it at import time
    (`from api.core.config import get_settings`), not `api.core.config`'s
    attribute — the same module-local rebinding
    `tests/unit/test_dataset_status.py:191,277` already relies on.
    """
    monkeypatch.setattr(storage, "get_settings", lambda: _settings())
    return storage.get_presign_client()


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Amputate `Minio`'s only HTTP entry point.

    Every request minio-py makes funnels through `_url_open`, so raising
    here turns "this code path talks to the network" from a slow, flaky,
    environment-dependent symptom into a deterministic, instant failure.
    """

    def _boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError(
            "minio-py attempted a network call while minting a presigned URL. "
            "Presigning is a pure local HMAC computation; the only thing that "
            "reaches out is _get_region()'s GetBucketLocation, which fires "
            "whenever the client was constructed without region=. See "
            "workers/storage.py::get_presign_client."
        )

    monkeypatch.setattr(Minio, "_url_open", _boom)


class TestPresignedUrlShape:
    def test_it_signs_against_the_public_host_with_no_port(
        self, presign_client: Minio, no_network: None
    ) -> None:
        """The Host in the URL is the Host the signature covers.

        `presign_v4` builds `canonical_headers = "host:" + url.netloc`
        (`minio/signer.py:275`). A browser sending `Host: storage.example.com`
        against a URL signed for `minio:9000` gets 403 SignatureDoesNotMatch —
        and so does the reverse. The no-port assertion matters for the same
        reason: `netloc` would carry `:443` verbatim into the signed string
        while a browser omits the default port, so the two would never agree.
        (`api/core/config.py`'s `minio_public_url` validator rejects an
        explicit `:443` for exactly this reason; this asserts the other half —
        that nothing downstream re-introduces one.)
        """
        url = storage.presigned_get_url(presign_client, _BUCKET, _KEY, expires=300)
        parts = urlsplit(url)

        assert parts.scheme == "https"
        assert parts.netloc == _PUBLIC_HOST, (
            f"signed against {parts.netloc!r}, not the public storage host"
        )
        assert ":" not in parts.netloc, "an explicit port would be signed but not sent"
        assert "minio:9000" not in url

    def test_it_uses_path_style_addressing(
        self, presign_client: Minio, no_network: None
    ) -> None:
        """`https://host/<bucket>/<key>`, not `https://<bucket>.host/<key>`.

        `BaseURL._virtual_style_flag` is False for non-AWS hosts
        (`minio/helpers.py:561-563`), which is what makes a plain
        `proxy_pass http://minio:9000;` with no rewrite forward the URI
        unchanged. Virtual-host style would need per-bucket DNS and a
        different nginx vhost entirely.
        """
        url = storage.presigned_get_url(presign_client, _BUCKET, _KEY, expires=300)
        assert urlsplit(url).path == f"/{_BUCKET}/{_KEY}"

    def test_the_query_carries_a_complete_sigv4_signature(
        self, presign_client: Minio, no_network: None
    ) -> None:
        url = storage.presigned_get_url(presign_client, _BUCKET, _KEY, expires=300)
        q = parse_qs(urlsplit(url).query)

        assert q["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
        assert q["X-Amz-SignedHeaders"] == ["host"]
        assert q["X-Amz-Expires"] == ["300"]
        assert q["X-Amz-Signature"][0]
        assert q["X-Amz-Credential"][0].endswith("us-east-1/s3/aws4_request"), (
            f"credential scope is {q['X-Amz-Credential'][0]!r} — the region in "
            "the scope must match the one get_presign_client() pins"
        )

    def test_the_expiry_is_the_ttl_it_was_given(
        self, presign_client: Minio, no_network: None
    ) -> None:
        url = storage.presigned_get_url(presign_client, _BUCKET, _KEY, expires=900)
        assert parse_qs(urlsplit(url).query)["X-Amz-Expires"] == ["900"]

    def test_a_filename_becomes_a_signed_content_disposition(
        self, presign_client: Minio, no_network: None
    ) -> None:
        """The response-override must be inside the signature, not appended
        afterwards — otherwise a caller could rewrite it and MinIO would
        reject the whole URL."""
        url = storage.presigned_get_url(
            presign_client, _BUCKET, _KEY, expires=300, filename="seed.jsonl"
        )
        q = parse_qs(urlsplit(url).query)
        assert q["response-content-disposition"] == ['attachment; filename="seed.jsonl"']

        # SigV4 covers the whole canonical query string, so an override that
        # is present in the URL is necessarily inside the signature — proven
        # here by signing the same object twice, once with the filename and
        # once without, and observing the signatures diverge. (Asserting the
        # override appears in X-Amz-SignedHeaders would be wrong: that header
        # lists signed *headers*, and for a presigned GET it is only `host`.)
        without = storage.presigned_get_url(presign_client, _BUCKET, _KEY, expires=300)
        assert (
            parse_qs(urlsplit(without).query)["X-Amz-Signature"][0] != q["X-Amz-Signature"][0]
        ), "the content-disposition override is not covered by the signature"

    def test_inline_disposition_is_signed_too(
        self, presign_client: Minio, no_network: None
    ) -> None:
        """`disposition="inline"` (view-in-browser, e.g. a seed PDF) swaps the
        disposition token but keeps it inside the signed query — same
        cannot-rewrite-client-side property as the attachment default."""
        url = storage.presigned_get_url(
            presign_client,
            _BUCKET,
            _KEY,
            expires=300,
            filename="seed.pdf",
            disposition="inline",
        )
        q = parse_qs(urlsplit(url).query)
        assert q["response-content-disposition"] == ['inline; filename="seed.pdf"']

        as_attachment = storage.presigned_get_url(
            presign_client, _BUCKET, _KEY, expires=300, filename="seed.pdf"
        )
        assert (
            parse_qs(urlsplit(as_attachment).query)["X-Amz-Signature"][0]
            != q["X-Amz-Signature"][0]
        ), "the disposition token is not covered by the signature"


class TestRegionIsPinned:
    """The mutation guard for the highest-risk line in the feature.

    Removing `region="us-east-1"` from `get_presign_client()` does not fail
    loudly — locally, against a fake, it looks identical. It only shows up
    against real infrastructure, as a first-presign-per-bucket round trip out
    through the public edge and back. These tests make that removal fail
    instantly and locally instead.
    """

    def test_presigning_makes_no_network_call(
        self, presign_client: Minio, no_network: None
    ) -> None:
        """Drop `region=` from get_presign_client() and this goes red with the
        AssertionError raised from the patched `_url_open`."""
        url = storage.presigned_get_url(presign_client, _BUCKET, _KEY, expires=300)
        assert url.startswith(f"https://{_PUBLIC_HOST}/")

    def test_the_client_carries_the_region_before_any_call(
        self, presign_client: Minio
    ) -> None:
        """The neighbouring assertion: the test above passes both when the
        region is pinned AND, hypothetically, if minio-py ever stopped
        calling `_get_region` for presigns. This one states the actual
        invariant — the client itself knows its region — so the guard does
        not silently become vacuous if upstream changes.
        """
        assert presign_client._base_url.region == "us-east-1"


class TestPublicUrlIsRequired:
    def test_unset_public_url_raises_rather_than_signing_a_useless_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Falling back to `minio_endpoint` would mint `http://minio:9000/...`
        links — syntactically fine, correctly signed, and unreachable from
        every browser on earth. The policy layer turns this into a 503."""
        monkeypatch.setattr(storage, "get_settings", lambda: _settings(minio_public_url=None))
        with pytest.raises(RuntimeError, match="MINIO_PUBLIC_URL"):
            storage.get_presign_client()

    def test_an_http_public_url_signs_without_tls(
        self, monkeypatch: pytest.MonkeyPatch, no_network: None
    ) -> None:
        """`secure` is derived from the configured scheme, not hard-coded —
        a local/staging deployment terminating TLS elsewhere still works."""
        monkeypatch.setattr(
            storage, "get_settings", lambda: _settings(minio_public_url="http://storage.local")
        )
        url = storage.presigned_get_url(storage.get_presign_client(), _BUCKET, _KEY, expires=300)
        assert url.startswith("http://storage.local/")


def test_the_files_that_point_here_use_this_module_s_real_name() -> None:
    """`tests/conftest.py` and `test_download_links.py` both cite this file
    as the justification for `_FakeMinio` not signing anything. Both cited a
    filename that does not exist (`test_presigned_url_shape.py`), so anyone
    following the pointer to check the fake's weakness found nothing and had
    to guess whether the coverage existed at all.

    A cross-reference that names a file is only worth writing if something
    checks the file is there.
    """
    from pathlib import Path

    me = Path(__file__)
    tests_dir = me.parent.parent
    for citing in (tests_dir / "conftest.py", me.parent / "test_download_links.py"):
        text = citing.read_text(encoding="utf-8")
        if me.name not in text and "presign" in text:
            raise AssertionError(
                f"{citing.name} references a presign-shape test file by a name "
                f"that is not {me.name!r}. Fix the pointer or drop it."
            )
