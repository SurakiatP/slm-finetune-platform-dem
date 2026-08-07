"""Schema-mismatch detection + key renaming for uploaded seed datasets.

Phase 9 §10.6. When a user uploads a seed file whose keys don't match
the canonical schema (e.g. `text1`/`answer` instead of `text`/`label`),
we ask `gemini-2.5-flash-lite` for a key-rename mapping, then apply it
locally. Best-effort: rows with unmappable required keys are dropped.

Pure domain code — runs sync. Callers that need to invoke this from an
async context should wrap in `asyncio.to_thread(...)`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_engine.data_gen.openrouter_client import OpenRouterClient

from . import models

log = logging.getLogger(__name__)


# ---- Inputs / outputs -----------------------------------------------------


@dataclass(frozen=True)
class FormatDetectionResult:
    """Result of one Format Detection pass."""

    ran: bool
    """False when the seed already matched the canonical schema (skipped)."""
    canonical_rows: list[dict[str, Any]]
    """Rows after the rename (or the originals if `ran is False`)."""
    field_mapping: dict[str, str]
    """The key-rename map the LLM produced. Empty when `ran is False`."""
    rows_dropped: int
    """Rows that couldn't be canonicalised (required keys still missing)."""
    notes: str | None = None
    # Token accounting for the billed OpenRouter call made inside
    # `detect_and_rename`. Populated on the LLM success path only; every
    # early-return path (empty input, already-canonical, LLM error,
    # no-mapping-produced) leaves all three `None`. An absent value means
    # no call was billed — that is NOT the same as a call that billed and
    # returned zero tokens (which would show up as `0`, not `None`).
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class _LLMFieldMapping(BaseModel):
    """Schema the LLM is asked to emit."""

    model_config = ConfigDict(extra="ignore")

    field_mapping: dict[str, str] = Field(default_factory=dict)


# ---- Public entry points --------------------------------------------------


def already_canonical(rows: list[dict[str, Any]], canonical_keys: set[str]) -> bool:
    """True when every row's keys are a subset of (or equal to) the canonical set
    AND every required key is present.

    `canonical_keys` is the FULL set (required + optional) — required-key
    enforcement happens elsewhere (Pydantic validators on the rows). We
    only short-circuit when there's no extraneous / misnamed key to fix.
    """
    if not rows:
        return True
    for row in rows:
        if not isinstance(row, dict):
            return False
        if not set(row.keys()).issubset(canonical_keys):
            return False
    return True


def detect_and_rename(
    *,
    rows: list[dict[str, Any]],
    canonical_keys: set[str],
    required_keys: set[str],
    task_type_label: str,
    client: OpenRouterClient,
    samples_for_llm: int = 3,
) -> FormatDetectionResult:
    """Run Format Detection on a list of rows.

    Args:
        rows: raw rows from the user's upload.
        canonical_keys: FULL set of canonical keys for this task type.
        required_keys: keys that MUST be present after renaming for a row
            to survive (others are optional).
        task_type_label: human-readable task name used inside the prompt
            (e.g. "classification (text + label)").
        client: sync OpenRouter client. Caller is responsible for the
            wrapping `asyncio.to_thread(...)` when called from async code.
        samples_for_llm: how many rows to show the LLM. Default 3.

    Returns:
        FormatDetectionResult with the canonicalised rows and a report.
    """
    if not rows:
        return FormatDetectionResult(
            ran=False,
            canonical_rows=[],
            field_mapping={},
            rows_dropped=0,
            notes="empty input",
        )

    if already_canonical(rows, canonical_keys):
        return FormatDetectionResult(
            ran=False,
            canonical_rows=list(rows),
            field_mapping={},
            rows_dropped=0,
            notes="already canonical — Format Detection skipped",
        )

    samples = rows[: max(1, samples_for_llm)]
    prompt_user = _build_user_prompt(
        canonical_keys=canonical_keys,
        required_keys=required_keys,
        task_type_label=task_type_label,
        samples=samples,
    )

    try:
        chat = client.chat(
            system=_SYSTEM_PROMPT,
            user=prompt_user,
            model=models.FORMAT_DETECTION,
            temperature=0.0,
            response_format={"type": "json_object"},
            max_tokens=512,
        )
    except Exception as exc:  # noqa: BLE001 — LLM down → best-effort
        log.warning(
            "Format Detection LLM call failed (%s); proceeding with raw rows",
            type(exc).__name__,
        )
        return _apply_no_mapping(rows, required_keys, notes=f"llm error: {exc}")

    mapping = _parse_mapping(chat.content)
    if not mapping:
        # LLM returned empty / unparseable — try the rows as-is and let the
        # required-key check below decide what to drop. No mapping was
        # produced, so — even though the call was billed — we don't record
        # usage here; this is a no-mapping-produced skip path, not the
        # success path.
        return _apply_no_mapping(rows, required_keys, notes="llm returned no mapping")

    # Success path: the LLM call was billed and produced a usable mapping.
    return _apply_mapping(
        rows,
        mapping,
        required_keys,
        model=chat.model,
        prompt_tokens=chat.prompt_tokens,
        completion_tokens=chat.completion_tokens,
    )


# ---- Internals ------------------------------------------------------------


_SYSTEM_PROMPT = (
    "You are a schema-mapping assistant. Given sample rows from a "
    "user-uploaded seed dataset and the canonical schema for a task, "
    "produce a JSON object that maps each non-canonical key in the "
    "samples to its canonical equivalent. Output ONLY a JSON object — "
    "no prose, no markdown, no explanation."
)


def _build_user_prompt(
    *,
    canonical_keys: set[str],
    required_keys: set[str],
    task_type_label: str,
    samples: list[dict[str, Any]],
) -> str:
    canonical_with_required = sorted(canonical_keys)
    required_sorted = sorted(required_keys)
    return (
        f"[Task type] {task_type_label}\n\n"
        f"[Canonical schema]\n"
        f"All keys: {canonical_with_required}\n"
        f"Required: {required_sorted}\n\n"
        f"[Sample rows from upload (first {len(samples)})]\n"
        f"{json.dumps(samples, ensure_ascii=False, indent=2)}\n\n"
        "[Output Instructions]\n"
        "1. ONLY rename keys; do NOT alter values.\n"
        "2. If a key already matches a canonical key, OMIT it from the mapping.\n"
        "3. If a key cannot be confidently mapped, OMIT it (rows with unmapped "
        "required keys will be dropped).\n"
        '4. Output ONLY a JSON object: {"field_mapping": {"old_key": "new_key", ...}}\n'
    )


def _parse_mapping(raw: str) -> dict[str, str]:
    """Extract the field_mapping dict from one LLM response. Returns {} on any error."""
    if not raw or not raw.strip():
        return {}
    try:
        parsed = _LLMFieldMapping.model_validate_json(raw.strip())
    except (ValidationError, json.JSONDecodeError, ValueError) as exc:
        log.warning("Format Detection response did not parse: %s", exc)
        return {}
    # Sanity: no key maps to itself; no empty values.
    return {k: v for k, v in parsed.field_mapping.items() if v and k != v}


def _rename_keys(row: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    """Return a new dict with each old key replaced by its canonical name."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        new_key = mapping.get(k, k)
        out[new_key] = v
    return out


def _apply_mapping(
    rows: list[dict[str, Any]],
    mapping: dict[str, str],
    required_keys: set[str],
    *,
    model: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> FormatDetectionResult:
    """Apply ``mapping`` to every row; drop rows still missing required keys.

    Counterpart to :func:`passthrough_with_required_check` for the case when
    the LLM produced a usable rename map. Kept as a private helper so the
    public :func:`detect_and_rename` orchestrator stays short and readable.

    ``model``/``prompt_tokens``/``completion_tokens`` are only ever passed by
    the LLM-success branch of :func:`detect_and_rename` — every other caller
    of this helper (there are none today) would leave them ``None``.
    """
    canonical_rows: list[dict[str, Any]] = []
    dropped = 0
    for row in rows:
        renamed = _rename_keys(row, mapping)
        if not required_keys.issubset(renamed.keys()):
            dropped += 1
            continue
        canonical_rows.append(renamed)
    return FormatDetectionResult(
        ran=True,
        canonical_rows=canonical_rows,
        field_mapping=mapping,
        rows_dropped=dropped,
        notes=None,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def passthrough_with_required_check(
    rows: list[dict[str, Any]],
    required_keys: set[str],
    *,
    notes: str,
) -> FormatDetectionResult:
    """Best-effort: pass rows through unchanged but drop any that lack required keys.

    Used when Format Detection is unavailable (no API key, LLM error,
    malformed mapping) — we still want to canonicalise as much as we can.
    """
    canonical: list[dict[str, Any]] = []
    dropped = 0
    for row in rows:
        if not required_keys.issubset(row.keys()):
            dropped += 1
            continue
        canonical.append(row)
    return FormatDetectionResult(
        ran=True,
        canonical_rows=canonical,
        field_mapping={},
        rows_dropped=dropped,
        notes=notes,
    )


# Backwards-compatible alias for the private helper used internally above.
_apply_no_mapping = passthrough_with_required_check


__all__ = [
    "FormatDetectionResult",
    "already_canonical",
    "detect_and_rename",
    "passthrough_with_required_check",
]
