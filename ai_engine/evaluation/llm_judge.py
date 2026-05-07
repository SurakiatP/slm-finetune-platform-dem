"""LLM-as-judge: have an OpenRouter model grade qualitative outputs 1–5.

Used for QA and tool-calling evaluations where exact-match metrics are too
strict (free-form answers can be correct with different wording).

The judge call asks the teacher model to return a JSON object with `score` and
`reason`; mean score across rows is what `EvaluationRun.llm_judge_score` stores.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from ai_engine.data_gen.openrouter_client import OpenRouterClient

log = logging.getLogger(__name__)


_JUDGE_SYSTEM = (
    "You are a strict but fair grader for QA / tool-calling fine-tuning outputs. "
    "Given the user question, the expected answer, and the model's predicted answer, "
    "rate the prediction on a 1–5 scale where:\n"
    "  1 = unrelated or wrong; 2 = partially relevant but mostly wrong; "
    "3 = roughly correct but missing key details; 4 = correct with minor issues; "
    "5 = fully correct.\n"
    "Respond ONLY with a JSON object: "
    '{"score": <int 1-5>, "reason": "<short explanation>"}.'
)

_JUDGE_USER_TEMPLATE = (
    "Question:\n{question}\n\n"
    "Expected answer:\n{expected}\n\n"
    "Predicted answer:\n{predicted}"
)


@dataclass(frozen=True)
class JudgeRowResult:
    score: int
    reason: str


@dataclass(frozen=True)
class JudgeBatchResult:
    rows: list[JudgeRowResult]
    mean_score: float
    judge_model: str
    skipped: int  # rows where the judge response failed to parse


def judge_rows(
    *,
    client: OpenRouterClient,
    judge_model: str,
    questions: list[str],
    expected: list[str],
    predicted: list[str],
    temperature: float = 0.0,
) -> JudgeBatchResult:
    """Score `predicted` answers against `expected`, one OpenRouter call per row.

    Args:
        client: an `OpenRouterClient` (the worker constructs one).
        judge_model: e.g. `anthropic/claude-3.5-sonnet`.
        questions: parallel to predicted/expected.
        expected: gold answers.
        predicted: model outputs.
        temperature: 0.0 for deterministic grading (recommended).

    Returns:
        `JudgeBatchResult` — per-row scores, mean across **successful** rows
        (skipped rows don't drag the mean down).
    """
    if not (len(questions) == len(expected) == len(predicted)):
        raise ValueError("judge_rows: input lists must be equal length")
    if not questions:
        raise ValueError("judge_rows: empty inputs")

    rows: list[JudgeRowResult] = []
    skipped = 0

    for q, exp, pred in zip(questions, expected, predicted):
        prompt = _JUDGE_USER_TEMPLATE.format(question=q, expected=exp, predicted=pred)
        try:
            chat = client.chat(
                system=_JUDGE_SYSTEM,
                user=prompt,
                temperature=temperature,
                model=judge_model,
                response_format={"type": "json_object"},
            )
            score, reason = _parse_judge_response(chat.content)
            rows.append(JudgeRowResult(score=score, reason=reason))
        except Exception as exc:  # noqa: BLE001
            log.warning("judge: row failed (%s); skipping", exc)
            skipped += 1

    successful = [r.score for r in rows]
    mean = (sum(successful) / len(successful)) if successful else 0.0
    return JudgeBatchResult(
        rows=rows,
        mean_score=float(mean),
        judge_model=judge_model,
        skipped=skipped,
    )


# ---- parsing ---------------------------------------------------------------


def _parse_judge_response(raw: str) -> tuple[int, str]:
    """Pull `score` (1–5) and `reason` (str) out of the judge's JSON response."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.lstrip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.split("```", 1)[0].strip()
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError("judge: not a JSON object")
    score = obj.get("score")
    if not isinstance(score, int) or not 1 <= score <= 5:
        raise ValueError(f"judge: bad/missing score={score!r}")
    reason = obj.get("reason", "")
    if not isinstance(reason, str):
        reason = str(reason)
    return score, reason[:500]


__all__ = [
    "JudgeRowResult",
    "JudgeBatchResult",
    "judge_rows",
]
