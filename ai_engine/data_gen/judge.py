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
"""

from __future__ import annotations

from json import JSONDecodeError

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


__all__ = ["JudgeScore", "parse_judge_response"]
