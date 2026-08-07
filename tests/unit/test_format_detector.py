"""Unit tests for format_detector.

We don't hit a real LLM — we inject a fake OpenRouterClient that returns
a canned response.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_engine.data_gen.format_detector import (
    already_canonical,
    detect_and_rename,
)
from ai_engine.data_gen.openrouter_client import ChatResult


@dataclass
class _FakeClient:
    """Minimal stand-in for OpenRouterClient that returns a fixed response."""

    response_content: str
    raise_exc: Exception | None = None
    last_kwargs: dict[str, Any] | None = None

    def chat(self, **kwargs: Any) -> ChatResult:
        self.last_kwargs = kwargs
        if self.raise_exc is not None:
            raise self.raise_exc
        return ChatResult(
            content=self.response_content,
            model="fake",
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=5,
        )


def test_already_canonical_short_circuits():
    rows = [{"text": "hi", "label": "greet"}]
    canonical = {"text", "label"}
    assert already_canonical(rows, canonical) is True


def test_already_canonical_detects_extra_key():
    rows = [{"text": "hi", "label": "greet", "weird": True}]
    canonical = {"text", "label"}
    assert already_canonical(rows, canonical) is False


def test_already_canonical_empty_rows():
    assert already_canonical([], {"text", "label"}) is True


def test_canonical_rows_skip_llm_call():
    """If rows are already canonical, the LLM is not invoked."""
    fake = _FakeClient(response_content='{"field_mapping": {}}')
    result = detect_and_rename(
        rows=[{"text": "hi", "label": "greet"}],
        canonical_keys={"text", "label"},
        required_keys={"text", "label"},
        task_type_label="classification",
        client=fake,  # type: ignore[arg-type]
    )
    assert result.ran is False
    assert fake.last_kwargs is None  # client.chat was never called
    assert result.canonical_rows == [{"text": "hi", "label": "greet"}]


def test_renames_keys_and_preserves_values():
    """Misnamed keys are renamed; values stay byte-identical."""
    fake = _FakeClient(
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
    assert result.ran is True
    assert result.field_mapping == {"text1": "text", "answer": "label"}
    assert result.canonical_rows == [
        {"text": "I want a refund", "label": "billing"},
        {"text": "App crashes on launch", "label": "technical"},
    ]
    assert result.rows_dropped == 0


def test_unmappable_required_key_drops_row():
    """When a row is missing a required key after renaming, drop it."""
    # LLM only renames `text1`; nothing maps to `label`. The first row has
    # no candidate for `label` → drop. Second row already has `label`.
    fake = _FakeClient(response_content='{"field_mapping": {"text1": "text"}}')
    result = detect_and_rename(
        rows=[
            {"text1": "no label here"},
            {"text1": "has label", "label": "ok"},
        ],
        canonical_keys={"text", "label"},
        required_keys={"text", "label"},
        task_type_label="classification",
        client=fake,  # type: ignore[arg-type]
    )
    assert result.rows_dropped == 1
    assert len(result.canonical_rows) == 1
    assert result.canonical_rows[0] == {"text": "has label", "label": "ok"}


def test_malformed_llm_response_falls_through():
    """If the LLM returns garbage, we still try the rows as-is."""
    fake = _FakeClient(response_content="not json at all")
    result = detect_and_rename(
        rows=[
            {"text": "ok", "label": "fine"},          # already canonical-ish
            {"text1": "wont map", "answer": "drop"},  # missing canonical keys
        ],
        canonical_keys={"text", "label"},
        required_keys={"text", "label"},
        task_type_label="classification",
        client=fake,  # type: ignore[arg-type]
    )
    assert result.ran is True
    assert result.field_mapping == {}
    # First row passes (has both required keys); second drops.
    assert result.rows_dropped == 1
    assert result.canonical_rows == [{"text": "ok", "label": "fine"}]


def test_llm_exception_is_best_effort():
    """If the LLM itself errors, we fall back to no-rename and drop unmappable rows."""
    fake = _FakeClient(response_content="", raise_exc=RuntimeError("network died"))
    result = detect_and_rename(
        rows=[{"text": "ok", "label": "fine"}, {"weird": "no required keys"}],
        canonical_keys={"text", "label"},
        required_keys={"text", "label"},
        task_type_label="classification",
        client=fake,  # type: ignore[arg-type]
    )
    assert result.ran is True
    assert "network died" in (result.notes or "")
    assert result.rows_dropped == 1


# ---- Token/usage fields ----------------------------------------------------
#
# `FormatDetectionResult.model` / `.prompt_tokens` / `.completion_tokens`
# must be populated ONLY on the LLM success path (a mapping was actually
# produced). Every early-return / skip / error path leaves all three
# `None` — that's the signal a later task uses to decide "no call was
# billed" vs. "a call was billed and returned 0 tokens".


def test_empty_input_leaves_usage_fields_none():
    fake = _FakeClient(response_content='{"field_mapping": {}}')
    result = detect_and_rename(
        rows=[],
        canonical_keys={"text", "label"},
        required_keys={"text", "label"},
        task_type_label="classification",
        client=fake,  # type: ignore[arg-type]
    )
    assert result.ran is False
    assert fake.last_kwargs is None  # never called
    assert result.model is None
    assert result.prompt_tokens is None
    assert result.completion_tokens is None


def test_already_canonical_leaves_usage_fields_none():
    fake = _FakeClient(response_content='{"field_mapping": {}}')
    result = detect_and_rename(
        rows=[{"text": "hi", "label": "greet"}],
        canonical_keys={"text", "label"},
        required_keys={"text", "label"},
        task_type_label="classification",
        client=fake,  # type: ignore[arg-type]
    )
    assert result.ran is False
    assert fake.last_kwargs is None  # never called
    assert result.model is None
    assert result.prompt_tokens is None
    assert result.completion_tokens is None


def test_llm_error_leaves_usage_fields_none():
    """A failed call is by definition not a billed success — no usage to record."""
    fake = _FakeClient(response_content="", raise_exc=RuntimeError("network died"))
    result = detect_and_rename(
        rows=[{"text": "ok", "label": "fine"}, {"weird": "no required keys"}],
        canonical_keys={"text", "label"},
        required_keys={"text", "label"},
        task_type_label="classification",
        client=fake,  # type: ignore[arg-type]
    )
    assert result.model is None
    assert result.prompt_tokens is None
    assert result.completion_tokens is None


def test_no_mapping_produced_still_reports_usage():
    """LLM responded but produced no usable mapping — the call was still billed.

    Billing tracks spend, not usefulness. This branch is where badly-shaped
    seed data lands most often, so dropping it would make under-reporting
    correlate with failure — the worst possible profile for a cost record.
    """
    fake = _FakeClient(response_content="not json at all")
    result = detect_and_rename(
        rows=[
            {"text": "ok", "label": "fine"},
            {"text1": "wont map", "answer": "drop"},
        ],
        canonical_keys={"text", "label"},
        required_keys={"text", "label"},
        task_type_label="classification",
        client=fake,  # type: ignore[arg-type]
    )
    assert result.ran is True
    assert result.field_mapping == {}
    assert result.model is not None
    assert result.prompt_tokens is not None
    assert result.completion_tokens is not None


def test_llm_success_populates_usage_fields():
    """A billed call that produces a usable mapping records model + token counts."""
    fake = _FakeClient(
        response_content='{"field_mapping": {"text1": "text", "answer": "label"}}'
    )
    result = detect_and_rename(
        rows=[{"text1": "I want a refund", "answer": "billing"}],
        canonical_keys={"text", "label"},
        required_keys={"text", "label"},
        task_type_label="classification",
        client=fake,  # type: ignore[arg-type]
    )
    assert result.ran is True
    assert result.model == "fake"
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 5


def test_usage_fields_are_trailing_defaults_positional_construction_still_valid():
    """Field ordering: new fields are appended at the end with defaults, so
    any pre-existing positional constructor call (ran, canonical_rows,
    field_mapping, rows_dropped) still works unchanged."""
    from ai_engine.data_gen.format_detector import FormatDetectionResult

    result = FormatDetectionResult(True, [{"text": "hi"}], {}, 0)
    assert result.notes is None
    assert result.model is None
    assert result.prompt_tokens is None
    assert result.completion_tokens is None
