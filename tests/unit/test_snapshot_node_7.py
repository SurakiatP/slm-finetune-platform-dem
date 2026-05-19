"""Tier 1+2 characterization snapshots for Node 7 (Export GGUF).

Wraps the pure helpers + side-effectful seams of ``workers/tasks/model_export.py``
BEFORE refactoring that file. Snapshot diff = 0 post-refactor ⇒ orchestration
behaviour preserved.

Pre-refactor coverage:

* **Tier 1 (pure helpers, no IO)** — argv builders for the two subprocess
  invocations (``convert_hf_to_gguf.py`` + ``llama-quantize``), the Ollama tag
  format, the transformers-4.57.2 config-bump guard, and the ``_first_gguf``
  directory scan.

* **Tier 2 (respx + fake_minio + monkeypatched subprocess)** — the MinIO
  adapter download (``_download_prefix``), the Ollama blob-upload +
  ``create``-from-blob registration flow (``_register_with_ollama``), and the
  end-to-end GGUF conversion subprocess sequence stitched through fake
  ``subprocess.run`` so the argv shape is byte-asserted at the orchestrator
  boundary.

Why this exists: ``export_model`` is too tangled with Unsloth / torch / DB to
characterize as a single unit. Splitting it into helpers (the planned Option-B
refactor) lets us pin behaviour piece-by-piece and prove the refactor doesn't
drift via ``pytest --snapshot-update`` diff = 0.

See ``docs/runbooks/snapshot_harness.md`` §2 for the workflow rules.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx

from workers.ollama_client import OllamaClient, OllamaError
from workers.tasks.model_export import (
    _bump_transformers_version_if_buggy,
    _compute_ollama_tag,
    _convert_hf_to_gguf_argv,
    _download_prefix,
    _first_gguf,
    _quantize_gguf_argv,
    _register_with_ollama,
)

_OLLAMA_BASE = "http://ollama-test:11434"


# ---- Tier 1: pure helper snapshots -----------------------------------------


def test_compute_ollama_tag_canonical(snapshot):
    """Single artifact id → short slm/<8> tag."""
    out = _compute_ollama_tag("a1b2c3d4e5f6789012345678abcdef00")
    assert out == snapshot


def test_compute_ollama_tag_various(snapshot):
    """Tag generation across UUID shapes — must stay byte-stable."""
    samples = {
        "uuid_dashed_lower": _compute_ollama_tag(
            "1234abcd-5678-90ef-1234-567890abcdef"
        ),
        "uuid_no_dashes": _compute_ollama_tag("1234abcd567890ef1234567890abcdef"),
        "short_id_under_8": _compute_ollama_tag("short"),
        "exact_8_chars": _compute_ollama_tag("01234567"),
        "uppercase_hex": _compute_ollama_tag("AABBCCDDEEFF0011223344556677"),
    }
    assert samples == snapshot


def test_convert_hf_to_gguf_argv_default(snapshot):
    """Argv for the HF → f16 GGUF conversion step (llama.cpp script)."""
    argv = _convert_hf_to_gguf_argv(
        stage_dir="/tmp/export-1/stage",
        f16_path="/tmp/export-1/gguf/model.f16.gguf",
    )
    assert argv == snapshot


def test_convert_hf_to_gguf_argv_windows_paths(snapshot):
    """Argv must preserve caller-supplied path style — no rewriting."""
    argv = _convert_hf_to_gguf_argv(
        stage_dir=r"C:\work\export-2\stage",
        f16_path=r"C:\work\export-2\gguf\model.f16.gguf",
    )
    assert argv == snapshot


def test_quantize_gguf_argv_q4_k_m(snapshot):
    """Argv for the f16 → quantized GGUF step (llama-quantize binary)."""
    argv = _quantize_gguf_argv(
        f16_path="/tmp/export-1/gguf/model.f16.gguf",
        gguf_path="/tmp/export-1/gguf/model.q4_k_m.gguf",
        quant="q4_k_m",
    )
    assert argv == snapshot


def test_quantize_gguf_argv_various_quants(snapshot):
    """Argv across quant methods — last position is the quant token verbatim."""
    samples = {}
    for q in ("q4_k_m", "q5_k_m", "q8_0", "f16"):
        samples[q] = _quantize_gguf_argv(
            f16_path="/tmp/f16.gguf",
            gguf_path=f"/tmp/out.{q}.gguf",
            quant=q,
        )
    assert samples == snapshot


def test_bump_transformers_version_below_threshold(snapshot):
    """transformers_version <= 4.57.2 → bumped to 4.58.0, returns True."""
    cfg = {"model_type": "llama", "transformers_version": "4.57.2"}
    mutated = _bump_transformers_version_if_buggy(cfg)
    assert {"mutated": mutated, "cfg_after": cfg} == snapshot


def test_bump_transformers_version_above_threshold(snapshot):
    """transformers_version > 4.57.2 → no change, returns False."""
    cfg = {"model_type": "llama", "transformers_version": "4.58.0"}
    mutated = _bump_transformers_version_if_buggy(cfg)
    assert {"mutated": mutated, "cfg_after": cfg} == snapshot


def test_bump_transformers_version_missing_field(snapshot):
    """No ``transformers_version`` key → treated as "0", bumped."""
    cfg = {"model_type": "llama"}
    mutated = _bump_transformers_version_if_buggy(cfg)
    assert {"mutated": mutated, "cfg_after": cfg} == snapshot


def test_bump_transformers_version_older_release(snapshot):
    """Older 4.x version (e.g. 4.51.0) still triggers bump (lexicographic <=)."""
    cfg = {"model_type": "llama", "transformers_version": "4.51.0"}
    mutated = _bump_transformers_version_if_buggy(cfg)
    assert {"mutated": mutated, "cfg_after": cfg} == snapshot


def test_first_gguf_returns_lowest_sorted(snapshot, tmp_path: Path):
    """``_first_gguf`` picks the lexicographically first .gguf in a dir."""
    (tmp_path / "model.q5_k_m.gguf").write_bytes(b"x")
    (tmp_path / "model.q4_k_m.gguf").write_bytes(b"y")
    (tmp_path / "readme.txt").write_text("not gguf")
    out = _first_gguf(str(tmp_path))
    # Snapshot just the file basename so the test is portable across OSes.
    assert os.path.basename(out) == snapshot


def test_first_gguf_raises_on_empty_dir(tmp_path: Path):
    """Empty directory → RuntimeError with directory path in message."""
    with pytest.raises(RuntimeError, match="no .gguf produced"):
        _first_gguf(str(tmp_path))


def test_first_gguf_ignores_non_gguf_files(snapshot, tmp_path: Path):
    """Non-.gguf siblings don't shadow the .gguf pick."""
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "tokenizer.json").write_text("{}")
    (tmp_path / "weights.q4_k_m.gguf").write_bytes(b"z")
    out = _first_gguf(str(tmp_path))
    assert os.path.basename(out) == snapshot


# ---- Tier 2: side-effectful helpers w/ mocked externals --------------------


class _FakeStreamResponse:
    """Minimal minio get_object response — supports stream(chunksize)."""

    def __init__(self, body: bytes) -> None:
        self._body = body
        self.closed = False
        self.released = False

    def stream(self, chunk_size: int):
        # Mirror minio's behaviour: yields one or more byte chunks.
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i : i + chunk_size]

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        self.released = True


class _RecordingMinio:
    """In-memory MinIO with deterministic recording for snapshots.

    Implements only the subset ``_download_prefix`` exercises:
    ``list_objects`` + ``get_object``. Records the call sequence so the test
    can snapshot the order of operations as well as the downloaded bytes.
    """

    def __init__(self, objects: dict[str, bytes]) -> None:
        self._objects = objects
        self.calls: list[tuple[str, str]] = []  # (op, object_name)

    def list_objects(self, *, bucket_name: str, prefix: str, recursive: bool):
        self.calls.append(("list_objects", f"{bucket_name}|{prefix}|{recursive}"))
        for name in sorted(self._objects):
            if name.startswith(prefix):
                yield SimpleNamespace(object_name=name, size=len(self._objects[name]))

    def get_object(self, *, bucket_name: str, object_name: str):
        self.calls.append(("get_object", object_name))
        return _FakeStreamResponse(self._objects[object_name])


def _read_dir_recursive(root: Path) -> dict[str, str]:
    """Walk ``root`` and return {relative_posix_path: utf8_text_or_marker}."""
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            data = p.read_bytes()
            try:
                out[rel] = data.decode("utf-8")
            except UnicodeDecodeError:
                out[rel] = f"<bytes:{len(data)}:{hashlib.sha256(data).hexdigest()[:12]}>"
    return out


def test_download_prefix_writes_all_objects(snapshot, tmp_path: Path):
    """Every object under the prefix lands at the correct relative path."""
    minio = _RecordingMinio(
        {
            "adapters/abc/adapter_config.json": b'{"task": "CAUSAL_LM"}',
            "adapters/abc/adapter_model.safetensors": b"<bin1>",
            "adapters/abc/tokenizer.json": b'{"version": "1.0"}',
            "adapters/abc/subdir/extra.bin": b"<bin2>",
            "adapters/other/skip.txt": b"should not be downloaded",
        }
    )
    local = tmp_path / "out"
    local.mkdir()
    _download_prefix(minio, "models", "adapters/abc", str(local))
    assert {
        "calls": minio.calls,
        "files": _read_dir_recursive(local),
    } == snapshot


def test_download_prefix_handles_trailing_slash(snapshot, tmp_path: Path):
    """Caller may pass the prefix with or without trailing slash — same result."""
    objects = {
        "p/f1.txt": b"one",
        "p/f2.txt": b"two",
    }
    a = tmp_path / "no_slash"
    a.mkdir()
    minio_a = _RecordingMinio(objects)
    _download_prefix(minio_a, "b", "p", str(a))

    b = tmp_path / "with_slash"
    b.mkdir()
    minio_b = _RecordingMinio(objects)
    _download_prefix(minio_b, "b", "p/", str(b))

    assert {
        "no_slash_calls": minio_a.calls,
        "no_slash_files": _read_dir_recursive(a),
        "with_slash_calls": minio_b.calls,
        "with_slash_files": _read_dir_recursive(b),
    } == snapshot


def test_download_prefix_skips_prefix_marker(snapshot, tmp_path: Path):
    """An object equal to the prefix itself is skipped (S3 quirk)."""
    minio = _RecordingMinio(
        {
            "exports/x/": b"",  # the prefix marker itself
            "exports/x/model.gguf": b"<gguf>",
        }
    )
    local = tmp_path / "out"
    local.mkdir()
    _download_prefix(minio, "b", "exports/x", str(local))
    assert {
        "files": _read_dir_recursive(local),
    } == snapshot


@pytest.fixture
def mock_ollama_router():
    """Mount respx on the Ollama daemon base URL."""
    with respx.mock(base_url=_OLLAMA_BASE, assert_all_called=False) as router:
        yield router


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_register_with_ollama_happy_path(snapshot, tmp_path: Path, mock_ollama_router):
    """Full happy path: health → upload_blob → create_from_blob."""
    gguf = tmp_path / "model.q4_k_m.gguf"
    body = b"GGUF FAKE BODY for snapshot test"
    gguf.write_bytes(body)
    digest = f"sha256:{_sha256(body)}"

    health_route = mock_ollama_router.get("/api/tags").mock(
        return_value=httpx.Response(200, json={"models": []}),
    )
    blob_route = mock_ollama_router.post(f"/api/blobs/{digest}").mock(
        return_value=httpx.Response(201),
    )
    create_calls: list[dict] = []

    def _create_handler(request: httpx.Request) -> httpx.Response:
        create_calls.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "success"})

    mock_ollama_router.post("/api/create").mock(side_effect=_create_handler)

    _register_with_ollama(
        ollama=OllamaClient(_OLLAMA_BASE),
        tag="slm/abc12345",
        gguf_path=str(gguf),
    )

    assert {
        "health_called": health_route.called,
        "blob_uploaded": blob_route.called,
        "create_payloads": create_calls,
    } == snapshot


def test_register_with_ollama_daemon_down(snapshot, tmp_path: Path, mock_ollama_router):
    """If /api/tags fails → log + early return; no blob/create called."""
    gguf = tmp_path / "model.gguf"
    gguf.write_bytes(b"x")
    mock_ollama_router.get("/api/tags").mock(return_value=httpx.Response(500))
    blob_route = mock_ollama_router.post(path__regex=r"^/api/blobs/.*$")
    create_route = mock_ollama_router.post("/api/create")

    _register_with_ollama(
        ollama=OllamaClient(_OLLAMA_BASE),
        tag="slm/down",
        gguf_path=str(gguf),
    )
    assert {
        "blob_called": blob_route.called,
        "create_called": create_route.called,
    } == snapshot


def test_register_with_ollama_blob_failure_propagates(tmp_path: Path, mock_ollama_router):
    """Blob upload non-2xx → OllamaError surfaces; create is NOT called."""
    gguf = tmp_path / "model.gguf"
    gguf.write_bytes(b"x")
    mock_ollama_router.get("/api/tags").mock(return_value=httpx.Response(200, json={"models": []}))
    mock_ollama_router.post(path__regex=r"^/api/blobs/.*$").mock(
        return_value=httpx.Response(500, text="disk full"),
    )
    create_route = mock_ollama_router.post("/api/create")

    with pytest.raises(OllamaError, match="upload_blob failed"):
        _register_with_ollama(
            ollama=OllamaClient(_OLLAMA_BASE),
            tag="slm/boom",
            gguf_path=str(gguf),
        )
    assert create_route.called is False


# ---- Tier 2: subprocess-mocked GGUF conversion sequence --------------------


@pytest.fixture
def fake_subprocess(monkeypatch: pytest.MonkeyPatch):
    """Replace subprocess.run with a recorder. Returns the call log."""
    calls: list[dict[str, Any]] = []

    def _fake_run(argv, *args, **kwargs):
        calls.append({"argv": list(argv), "check": kwargs.get("check", False)})
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    return calls


def test_gguf_subprocess_sequence_default_quant(snapshot, fake_subprocess):
    """The two-step convert+quantize sequence as we drive it inline today.

    Mirrors lines 186-205 of ``model_export.py``. Once those lines move into
    a ``_convert_to_gguf_via_subprocess`` helper, this test will catch any
    argv drift between the helper's output and the current inline form.
    """
    stage_dir = "/work/stage"
    out_dir = "/work/gguf"
    f16_path = "/work/gguf/model.f16.gguf"
    quant = "q4_k_m"
    gguf_path = f"/work/gguf/model.{quant}.gguf"

    subprocess.run(
        _convert_hf_to_gguf_argv(stage_dir=stage_dir, f16_path=f16_path),
        check=True,
    )
    subprocess.run(
        _quantize_gguf_argv(f16_path=f16_path, gguf_path=gguf_path, quant=quant),
        check=True,
    )

    assert fake_subprocess == snapshot


def test_gguf_subprocess_sequence_q5_k_m(snapshot, fake_subprocess):
    """Same sequence with a different quant — argv shape must match."""
    f16_path = "/work/gguf/model.f16.gguf"
    quant = "q5_k_m"
    gguf_path = f"/work/gguf/model.{quant}.gguf"

    subprocess.run(
        _convert_hf_to_gguf_argv(stage_dir="/work/stage", f16_path=f16_path),
        check=True,
    )
    subprocess.run(
        _quantize_gguf_argv(f16_path=f16_path, gguf_path=gguf_path, quant=quant),
        check=True,
    )

    assert fake_subprocess == snapshot
