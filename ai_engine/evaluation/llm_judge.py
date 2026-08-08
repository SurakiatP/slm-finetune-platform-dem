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
from ai_engine.data_gen.usage import STAGE_EVAL_JUDGE, UsageAccumulator

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
    mean_score: float | None  # None when every row was skipped (no signal vs. score=0)
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
    usage: UsageAccumulator | None = None,
) -> JudgeBatchResult:
    """Score `predicted` answers against `expected`, one OpenRouter call per row.

    Args:
        client: an `OpenRouterClient` (the worker constructs one).
        judge_model: e.g. `anthropic/claude-3.5-sonnet`.
        questions: parallel to predicted/expected.
        expected: gold answers.
        predicted: model outputs.
        temperature: 0.0 for deterministic grading (recommended).
        usage: optional accumulator. Passed *in* rather than returned on
            `JudgeBatchResult` for the same reason SDG threads one into
            `generate()`: a run that raises never returns a result, and the
            tokens it burned before raising still have to be billed. With
            `None` the judge simply runs unmetered (the pre-2026-08-08
            behaviour, kept for callers that have no accumulator).

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
        row = _judge_one_row(
            client=client,
            judge_model=judge_model,
            question=q,
            expected=exp,
            predicted=pred,
            temperature=temperature,
            usage=usage,
        )
        if row is None:
            skipped += 1
        else:
            rows.append(row)
        # Budget is checked per row, after that row's tokens are recorded —
        # the same "cap spend as it happens, not once at submit" reasoning
        # `UsageAccumulator.check_budget` documents. A 500-row judge pass is
        # exactly the shape of run a submit-only check cannot stop.
        if usage is not None:
            usage.check_budget()

    return JudgeBatchResult(
        rows=rows,
        mean_score=_aggregate_mean(rows),
        judge_model=judge_model,
        skipped=skipped,
    )


# ---- per-row + aggregation helpers ----------------------------------------


def _build_judge_user_prompt(question: str, expected: str, predicted: str) -> str:
    """Render the user-side prompt the judge model sees for one row."""
    return _JUDGE_USER_TEMPLATE.format(
        question=question, expected=expected, predicted=predicted
    )


def _judge_one_row(
    *,
    client: OpenRouterClient,
    judge_model: str,
    question: str,
    expected: str,
    predicted: str,
    temperature: float,
    usage: UsageAccumulator | None = None,
) -> JudgeRowResult | None:
    """Judge a single row; ``None`` on any OpenRouter / parse failure."""
    prompt = _build_judge_user_prompt(question, expected, predicted)
    try:
        chat = client.chat(
            system=_JUDGE_SYSTEM,
            user=prompt,
            temperature=temperature,
            model=judge_model,
            response_format={"type": "json_object"},
        )
        # Record BEFORE parsing, deliberately. A row whose JSON fails to
        # parse is `skipped` for scoring purposes but was still a paid
        # OpenRouter call — billing it only on the parse-success path would
        # under-report exactly the runs that go wrong. `chat.model` (not
        # `judge_model`) because OpenRouter may serve a different concrete
        # model than the one requested.
        if usage is not None:
            usage.add(
                chat.model,
                STAGE_EVAL_JUDGE,
                chat.prompt_tokens,
                chat.completion_tokens,
            )
        score, reason = _parse_judge_response(chat.content)
        return JudgeRowResult(score=score, reason=reason)
    except Exception as exc:  # noqa: BLE001
        log.warning("judge: row failed (%s); skipping", exc)
        return None


def _aggregate_mean(rows: list[JudgeRowResult]) -> float | None:
    """Mean of successful row scores; ``None`` when every row was skipped.

    Returning ``None`` (not 0.0) is intentional: a zero would suggest "scored
    1 across the board" which is qualitatively different from "no judge
    signal at all".
    """
    if not rows:
        return None
    return sum(r.score for r in rows) / len(rows)


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
