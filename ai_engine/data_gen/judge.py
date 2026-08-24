"""LLM-as-Judge for synthetic-row quality.

Each generated row is scored on three axes:

  • fidelity      — does the row faithfully reflect the task description /
                    label / tool spec? (For QA: "is the answer correct given
                    the question".)
  • naturalness   — would a human user actually phrase / receive this?
  • utility       — is this row useful for fine-tuning a small model on
                    the task?

Final score is the weighted mean: 0.4·fidelity + 0.3·naturalness + 0.3·utility.
Threshold (default 0.7, see constants.JUDGE_THRESHOLD) gates the row.

The judge is invoked via AsyncOpenRouterClient.chat_batch — one prompt
per row, up to 100 concurrent. Failed parses are returned as None and
the orchestrator counts them separately from low-score rejections.

Batch mode: parse_judge_batch_response parses a single LLM response that
scores constants.JUDGE_ROWS_PER_CALL rows at once (see
`{"scores": [{"index": ..., ...}, ...]}`), returning a positionally
aligned list where any row that failed to parse/validate is None.
"""

from __future__ import annotations

from json import JSONDecodeError, loads

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class JudgeScore(BaseModel):
    """One judge output for one row."""

    model_config = ConfigDict(extra="ignore")

    fidelity: float = Field(..., ge=0.0, le=1.0)
    naturalness: float = Field(..., ge=0.0, le=1.0)
    utility: float = Field(..., ge=0.0, le=1.0)
    reasoning: str = Field(default="", max_length=2000)

    @property
    def weighted(self) -> float:
        return 0.4 * self.fidelity + 0.3 * self.naturalness + 0.3 * self.utility


def parse_judge_response(raw: str) -> JudgeScore | None:
    """Parse one judge LLM response. Returns None on any error.

    Returning None instead of raising lets the orchestrator track parse
    failures separately from low-score rejections (both reasons matter
    for the SDGProgress breakdown).
    """
    if not raw or not raw.strip():
        return None
    try:
        return JudgeScore.model_validate_json(raw.strip())
    except (ValidationError, JSONDecodeError, ValueError):
        return None


def parse_judge_batch_response(raw: str, *, expected: int) -> list[JudgeScore | None]:
    """Parse one batched judge LLM response covering `expected` rows.

    Accepts either the primary `{"scores": [{"index": ..., ...}, ...]}`
    shape or a bare top-level JSON array of entries (defensive fallback).
    Each entry must carry an integer "index" in [0, expected) to be placed
    into the returned list, which is always positionally aligned to the
    input rows and exactly `expected` long. A slot is None when: the index
    is missing/non-int/out of range, the entry fails JudgeScore validation,
    or no entry claims that index. Duplicate indices: first one wins,
    whether or not it validates. `reasoning` is truncated to 120 chars
    before validation so an overlong (but otherwise valid) explanation
    never turns into a parse failure. Blank/unparseable `raw` returns
    `[None] * expected`; this function never raises.
    """
    result: list[JudgeScore | None] = [None] * expected
    if not raw or not raw.strip():
        return result

    try:
        payload = loads(raw.strip())
    except (JSONDecodeError, ValueError):
        return result

    if isinstance(payload, dict):
        entries = payload.get("scores")
    elif isinstance(payload, list):
        entries = payload
    else:
        entries = None

    if not isinstance(entries, list):
        return result

    seen: set[int] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        index = entry.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or not (0 <= index < expected):
            continue
        if index in seen:
            continue
        seen.add(index)

        reasoning = entry.get("reasoning", "")
        if isinstance(reasoning, str) and len(reasoning) > 120:
            entry = {**entry, "reasoning": reasoning[:120]}

        try:
            result[index] = JudgeScore.model_validate(entry)
        except ValidationError:
            result[index] = None

    return result


__all__ = ["JudgeScore", "parse_judge_response", "parse_judge_batch_response"]
