"""Tier 1+2 characterization snapshots for Node 3a (Upload Seed Data).

Wraps the pure helpers + Gemini-mediated key-rename flow used by
`POST /api/v1/datasets/upload-seed` BEFORE refactoring the three target
modules:

    • ``ai_engine/data_gen/format_detector.py``  (Tier 1 pure helpers +
      Tier 2 orchestrator under Gemini mock)
    • ``ai_engine/data_gen/pdf_loader.py``        (Tier 1 probe + base64 wrap)
    • ``api/services/datasets_service.py``        (helper-level snapshots —
      we do NOT exercise the AsyncSession / FastAPI surface here; the
      frontend contract is asserted at the integration layer)

Snapshot diff = 0 after refactor ⇒ behaviour preserved. See
``docs/runbooks/snapshot_harness.md`` for the workflow.

Mocking notes:
  • The Gemini call in ``format_detector.detect_and_rename`` is replaced
    with a tiny ``_FakeOpenRouterClient`` that returns a canned
    ``ChatResult`` — same shape that the real ``OpenRouterClient.chat``
    returns. This mirrors the pattern in
    ``tests/unit/test_format_detector.py``.
  • PDF tests build minimal valid PDFs in-memory via pypdf (no binary
    fixtures); the "too-large" case uses an explicit ``max_bytes``
    override to keep snapshots deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any

import pytest
from pypdf import PdfWriter

from ai_engine.data_gen.format_detector import (
    FormatDetectionResult,
    _build_user_prompt,
    _parse_mapping,
    _rename_keys,
    already_canonical,
    detect_and_rename,
    passthrough_with_required_check,
)
from ai_engine.data_gen.openrouter_client import ChatResult
from ai_engine.data_gen.pdf_loader import (
    PdfCorruptError,
    PdfTooLargeError,
    PdfTooManyPagesError,
    probe as pdf_probe,
    to_base64_data_url,
)
from api.schemas.data_formats import canonical_field_names, required_field_names
from api.schemas.enums import TaskType


# ---- Helpers ---------------------------------------------------------------


@dataclass
class _FakeOpenRouterClient:
    """Tiny stand-in for ``OpenRouterClient`` that returns a canned response.

    Mirrors ``tests/unit/test_format_detector.py``'s ``_FakeClient`` so the
    Tier 2 orchestrator path can be characterized without touching the
    network. Captures ``last_kwargs`` so individual tests can also assert
    on the call shape if they need to (we snapshot the whole call below).
    """

    response_content: str
    raise_exc: Exception | None = None
    last_kwargs: dict[str, Any] | None = None
    call_count: int = 0

    def chat(self, **kwargs: Any) -> ChatResult:
        self.call_count += 1
        self.last_kwargs = kwargs
        if self.raise_exc is not None:
            raise self.raise_exc
        return ChatResult(
            content=self.response_content,
            model="fake-gemini",
            finish_reason="stop",
            prompt_tokens=42,
            completion_tokens=7,
        )


def _make_pdf_bytes(num_pages: int) -> bytes:
    """Build a minimal valid PDF with ``num_pages`` blank pages."""
    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=72, height=72)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _fd_to_dict(result: FormatDetectionResult) -> dict[str, Any]:
    """Stable dict form of a ``FormatDetectionResult`` for snapshot diffing.

    We avoid relying on ``dataclasses.asdict`` so reordering of dataclass
    fields in a refactor doesn't break snapshots — the explicit key order
    here is the snapshot contract.
    """
    return {
        "ran": result.ran,
        "canonical_rows": list(result.canonical_rows),
        "field_mapping": dict(result.field_mapping),
        "rows_dropped": result.rows_dropped,
        "notes": result.notes,
    }


# ===========================================================================
# Tier 1 — pure helper snapshots
# ===========================================================================


# ---- canonical-key checker -------------------------------------------------


@pytest.mark.parametrize(
    "rows,canonical_keys",
    [
        # subset is canonical (missing optional key — required-key check
        # happens elsewhere)
        ([{"text": "hi", "label": "greet"}], {"text", "label"}),
        # extra key → not canonical
        (
            [{"text": "hi", "label": "greet", "extra": "noise"}],
            {"text", "label"},
        ),
        # misnamed key → not canonical
        ([{"text1": "hi", "answer": "greet"}], {"text", "label"}),
        # mixed rows: one canonical + one extra-key → not canonical
        (
            [
                {"text": "a", "label": "x"},
                {"text": "b", "label": "y", "extra": True},
            ],
            {"text", "label"},
        ),
        # empty rows short-circuits True
        ([], {"text", "label"}),
        # non-dict row → False (defensive)
        (["not-a-dict"], {"text", "label"}),  # type: ignore[list-item]
    ],
    ids=[
        "canonical_subset",
        "extra_key",
        "misnamed_keys",
        "mixed_canonical_and_extra",
        "empty_rows",
        "non_dict_row",
    ],
)
def test_already_canonical(snapshot, rows: list, canonical_keys: set[str]):
    assert already_canonical(rows, canonical_keys) == snapshot


# ---- key-rename mapping logic ---------------------------------------------


@pytest.mark.parametrize(
    "row,mapping",
    [
        # standard rename
        (
            {"text1": "hello", "answer": "greet", "score": 0.9},
            {"text1": "text", "answer": "label"},
        ),
        # passthrough — no key in mapping
        (
            {"text": "hello", "label": "greet"},
            {"old1": "text", "old2": "label"},
        ),
        # empty mapping — all keys preserved verbatim
        ({"a": 1, "b": 2}, {}),
        # mapping renames to a key that already exists → collision retains
        # the LAST-written value (insertion order: original key removed via
        # rename, second key keeps its original value). This is the existing
        # behaviour we lock in.
        (
            {"text": "FIRST", "old": "RENAMED"},
            {"old": "text"},
        ),
    ],
    ids=[
        "standard_rename",
        "passthrough_no_match",
        "empty_mapping",
        "collision_to_existing_key",
    ],
)
def test_rename_keys(snapshot, row: dict, mapping: dict[str, str]):
    assert _rename_keys(row, mapping) == snapshot


# ---- LLM-response mapping parser ------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        '{"field_mapping": {"text1": "text", "answer": "label"}}',
        # extra fields ignored (ConfigDict extra="ignore")
        '{"field_mapping": {"a": "b"}, "explanation": "ignored"}',
        # identity entry filtered out (k == v)
        '{"field_mapping": {"text": "text", "answer": "label"}}',
        # empty values filtered out
        '{"field_mapping": {"text": "", "answer": "label"}}',
        # empty input → {}
        "",
        "   ",
        # invalid JSON → {}
        "not json at all",
        # JSON but wrong schema → {}
        '{"foo": "bar"}',
        # nested object where string expected — pydantic rejects → {}
        '{"field_mapping": {"text": {"nested": "x"}}}',
    ],
    ids=[
        "standard",
        "extra_field_ignored",
        "identity_filtered",
        "empty_value_filtered",
        "empty_string",
        "whitespace_only",
        "invalid_json",
        "wrong_schema",
        "nested_value_rejected",
    ],
)
def test_parse_mapping(snapshot, raw: str):
    assert _parse_mapping(raw) == snapshot


# ---- prompt builder --------------------------------------------------------


def test_build_user_prompt_classification(snapshot):
    out = _build_user_prompt(
        canonical_keys={"text", "label"},
        required_keys={"text", "label"},
        task_type_label="classification",
        samples=[
            {"text1": "I want a refund", "answer": "billing"},
            {"text1": "App crashes", "answer": "technical"},
        ],
    )
    assert out == snapshot


def test_build_user_prompt_tool_calling(snapshot):
    out = _build_user_prompt(
        canonical_keys={"question", "answer"},
        required_keys={"question", "answer"},
        task_type_label="tool_calling",
        samples=[
            {
                "user_msg": "เปิดเพลง",
                "response": '{"name": "play_music", "parameters": {}}',
            },
        ],
    )
    assert out == snapshot


# ---- passthrough with required-key check ----------------------------------


def test_passthrough_drops_rows_missing_required(snapshot):
    rows = [
        {"text": "hi", "label": "greet"},          # keep
        {"text": "hello"},                          # drop — no label
        {"label": "greet"},                         # drop — no text
        {"text": "hey", "label": "greet", "x": 1},  # keep (extra ok)
    ]
    result = passthrough_with_required_check(
        rows, {"text", "label"}, notes="OPENROUTER_API_KEY not set"
    )
    assert _fd_to_dict(result) == snapshot


def test_passthrough_all_canonical_no_drops(snapshot):
    rows = [
        {"text": "hi", "label": "a"},
        {"text": "yo", "label": "b"},
    ]
    result = passthrough_with_required_check(
        rows, {"text", "label"}, notes="llm error: ConnectionError"
    )
    assert _fd_to_dict(result) == snapshot


# ---- canonical-field-name helpers (datasets_service inputs) ----------------


def test_canonical_field_names_per_task(snapshot):
    samples = {
        "classification": sorted(canonical_field_names(TaskType.CLASSIFICATION)),
        "tool_calling": sorted(canonical_field_names(TaskType.TOOL_CALLING)),
        "qa": sorted(canonical_field_names(TaskType.QA)),
    }
    assert samples == snapshot


def test_required_field_names_per_task(snapshot):
    samples = {
        "classification": sorted(required_field_names(TaskType.CLASSIFICATION)),
        "tool_calling": sorted(required_field_names(TaskType.TOOL_CALLING)),
        "qa": sorted(required_field_names(TaskType.QA)),
    }
    assert samples == snapshot


# ---- pdf_loader pure helpers -----------------------------------------------


def test_pdf_probe_one_page(snapshot):
    pdf = _make_pdf_bytes(1)
    p = pdf_probe(pdf)
    # We snapshot only `num_pages` — `size_bytes` depends on pypdf's
    # internal serialiser and would drift with a version bump.
    assert {"num_pages": p.num_pages} == snapshot


def test_pdf_probe_three_pages(snapshot):
    pdf = _make_pdf_bytes(3)
    p = pdf_probe(pdf)
    assert {"num_pages": p.num_pages} == snapshot


def test_pdf_probe_too_large_error_message(snapshot):
    pdf = _make_pdf_bytes(1)
    # Force the byte-cap branch with a tiny cap; snapshot the error class +
    # the deterministic part of the message (cap value).
    with pytest.raises(PdfTooLargeError) as ei:
        pdf_probe(pdf, max_bytes=10)
    assert {
        "error_type": type(ei.value).__name__,
        "mentions_cap": "cap 10" in str(ei.value),
        "mentions_mib": "0 MiB" in str(ei.value),
    } == snapshot


def test_pdf_probe_too_many_pages_error_message(snapshot):
    pdf = _make_pdf_bytes(5)
    with pytest.raises(PdfTooManyPagesError) as ei:
        pdf_probe(pdf, max_pages=2)
    assert {
        "error_type": type(ei.value).__name__,
        "message": str(ei.value),
    } == snapshot


def test_pdf_probe_corrupt_error_message(snapshot):
    with pytest.raises(PdfCorruptError) as ei:
        pdf_probe(b"this is definitely not a PDF")
    assert {
        "error_type": type(ei.value).__name__,
        "starts_with_failed_to_parse": str(ei.value).startswith(
            "failed to parse PDF:"
        ),
    } == snapshot


def test_to_base64_data_url_prefix_and_payload(snapshot):
    pdf = _make_pdf_bytes(1)
    url = to_base64_data_url(pdf)
    # Snapshot only deterministic structural pieces — the encoded payload
    # itself depends on pypdf's serialiser.
    head, _, payload = url.partition(",")
    assert {
        "scheme_prefix": head,
        "payload_is_ascii_base64": all(
            c.isalnum() or c in "+/=" for c in payload
        ),
    } == snapshot


def test_to_base64_data_url_empty_raises():
    # Defensive: not a snapshot — just a behaviour we lock in.
    with pytest.raises(ValueError):
        to_base64_data_url(b"")


# ===========================================================================
# Tier 2 — Gemini-mocked orchestrator snapshots
# ===========================================================================


def test_detect_canonical_passthrough_no_llm_call(snapshot):
    """Canonical rows: LLM is never invoked, rows returned verbatim."""
    fake = _FakeOpenRouterClient(response_content='{"field_mapping": {}}')
    result = detect_and_rename(
        rows=[
            {"text": "hi", "label": "greet"},
            {"text": "bye", "label": "farewell"},
        ],
        canonical_keys={"text", "label"},
        required_keys={"text", "label"},
        task_type_label="classification",
        client=fake,  # type: ignore[arg-type]
    )
    assert {
        "fd_result": _fd_to_dict(result),
        "llm_call_count": fake.call_count,
    } == snapshot


def test_detect_renames_keys_via_gemini(snapshot):
    """Non-canonical seed: Gemini returns rename map, rows are canonicalised."""
    fake = _FakeOpenRouterClient(
        response_content='{"field_mapping": {"text1": "text", "answer": "label"}}'
    )
    result = detect_and_rename(
        rows=[
            {"text1": "I want a refund", "answer": "billing"},
            {"text1": "App crashes on launch", "answer": "technical"},
        ],
        canonical_keys={"text", "label"},
        required_keys={"text", "label"},
        task_type_label="classification",
        client=fake,  # type: ignore[arg-type]
    )
    assert {
        "fd_result": _fd_to_dict(result),
        "llm_call_count": fake.call_count,
        # Snapshot the call kwargs so the prompt-building seam is locked in.
        "llm_call_kwargs": fake.last_kwargs,
    } == snapshot


def test_detect_drops_unmappable_required_rows(snapshot):
    """Gemini renames text1→text but emits no mapping for label — row 0 drops."""
    fake = _FakeOpenRouterClient(
        response_content='{"field_mapping": {"text1": "text"}}'
    )
    result = detect_and_rename(
        rows=[
            {"text1": "missing label"},                # drop
            {"text1": "has label", "label": "ok"},      # keep
        ],
        canonical_keys={"text", "label"},
        required_keys={"text", "label"},
        task_type_label="classification",
        client=fake,  # type: ignore[arg-type]
    )
    assert {
        "fd_result": _fd_to_dict(result),
        "llm_call_count": fake.call_count,
    } == snapshot


def test_detect_gemini_returns_wrong_json_shape(snapshot):
    """Gemini returns valid JSON but wrong schema → fall back to passthrough."""
    fake = _FakeOpenRouterClient(
        response_content='{"explanation": "I cannot map these fields"}'
    )
    result = detect_and_rename(
        rows=[
            {"text1": "no mapping", "answer": "drops"},  # missing text, label
            {"text": "already canonical", "label": "stays"},
        ],
        canonical_keys={"text", "label"},
        required_keys={"text", "label"},
        task_type_label="classification",
        client=fake,  # type: ignore[arg-type]
    )
    assert {
        "fd_result": _fd_to_dict(result),
        "llm_call_count": fake.call_count,
    } == snapshot


def test_detect_gemini_returns_unparseable_garbage(snapshot):
    """Gemini emits non-JSON prose → parser yields {} → passthrough."""
    fake = _FakeOpenRouterClient(
        response_content="I'm sorry, I cannot infer a mapping from these rows."
    )
    result = detect_and_rename(
        rows=[
            {"text": "row1", "label": "a"},
            {"text1": "row2", "answer": "b"},  # drops — missing text, label
        ],
        canonical_keys={"text", "label"},
        required_keys={"text", "label"},
        task_type_label="classification",
        client=fake,  # type: ignore[arg-type]
    )
    assert {
        "fd_result": _fd_to_dict(result),
        "llm_call_count": fake.call_count,
    } == snapshot


def test_detect_gemini_raises_exception(snapshot):
    """LLM call raises → caught, falls back to passthrough with notes."""
    fake = _FakeOpenRouterClient(
        response_content="ignored",
        raise_exc=ConnectionError("openrouter unreachable"),
    )
    result = detect_and_rename(
        rows=[
            {"text": "row1", "label": "a"},
            {"text1": "row2"},  # drops
        ],
        canonical_keys={"text", "label"},
        required_keys={"text", "label"},
        task_type_label="classification",
        client=fake,  # type: ignore[arg-type]
    )
    assert {
        "fd_result": _fd_to_dict(result),
        "llm_call_count": fake.call_count,
        # We don't snapshot the full notes string (contains exc repr) —
        # just confirm the shape was set.
        "notes_starts_with_llm_error": (result.notes or "").startswith(
            "llm error:"
        ),
    } == snapshot


def test_detect_empty_rows_short_circuits(snapshot):
    fake = _FakeOpenRouterClient(response_content='{"field_mapping": {}}')
    result = detect_and_rename(
        rows=[],
        canonical_keys={"text", "label"},
        required_keys={"text", "label"},
        task_type_label="classification",
        client=fake,  # type: ignore[arg-type]
    )
    assert {
        "fd_result": _fd_to_dict(result),
        "llm_call_count": fake.call_count,
    } == snapshot


def test_detect_tool_calling_rename(snapshot):
    """Tool-calling task: rename `user_msg`/`response` → `question`/`answer`."""
    fake = _FakeOpenRouterClient(
        response_content=(
            '{"field_mapping": {"user_msg": "question", "response": "answer"}}'
        )
    )
    result = detect_and_rename(
        rows=[
            {
                "user_msg": "เปิดเพลง",
                "response": '{"name": "play_music", "parameters": {}}',
            },
        ],
        canonical_keys={"question", "answer"},
        required_keys={"question", "answer"},
        task_type_label="tool_calling",
        client=fake,  # type: ignore[arg-type]
    )
    assert {
        "fd_result": _fd_to_dict(result),
        "llm_call_count": fake.call_count,
    } == snapshot
