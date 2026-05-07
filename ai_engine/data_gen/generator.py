"""SDG orchestrator — pure domain code, no FastAPI/Celery imports.

The Celery task in `workers/tasks/data_generation.py` instantiates this
generator, hands it a progress callback, and persists the result.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Callable, Literal

from api.schemas.enums import TaskType
from api.schemas.sdg import SDGRequestDescriptionOnly, SDGRequestWithSeed

from .deduplicator import Deduplicator
from .openrouter_client import OpenRouterClient
from .prompts import JSON_OBJECT_RESPONSE_FORMAT, build_prompt
from .validators import validate_generated_rows

log = logging.getLogger(__name__)

GenerationPhase = Literal["generating", "validating", "deduplicating", "persisting"]


@dataclass(frozen=True)
class GenerationProgress:
    """Progress event emitted to a callback after every batch.

    Translated by the Celery worker into `api.schemas.progress.SDGProgress`
    with `job_id` + `timestamp` attached, then published to Redis.
    """

    phase: GenerationPhase
    samples_generated: int
    samples_target: int
    samples_valid: int
    samples_rejected: int
    duplicates_removed: int


ProgressCallback = Callable[[GenerationProgress], None]


@dataclass
class SDGRunResult:
    valid_rows: list[dict]
    rejected_count: int
    duplicate_count: int
    api_calls: int
    failed_attempts: list[str] = field(default_factory=list)


class SDGAbortedError(RuntimeError):
    """Raised when too many consecutive batches fail to produce parseable rows."""


class SyntheticDataGenerator:
    """Drives the SDG loop: prompt → call → parse → validate → dedup → repeat."""

    def __init__(
        self,
        client: OpenRouterClient,
        *,
        batch_size: int = 10,
        max_attempts_per_batch: int = 3,
        max_consecutive_failures: int = 5,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        self._client = client
        self._batch_size = batch_size
        self._max_attempts = max_attempts_per_batch
        self._max_consec_fail = max_consecutive_failures

    def generate(
        self,
        request: SDGRequestWithSeed | SDGRequestDescriptionOnly,
        progress_cb: ProgressCallback | None = None,
    ) -> SDGRunResult:
        target = request.num_samples
        accepted: list[dict] = []
        rejected_total = 0
        duplicates_total = 0
        api_calls = 0
        failed_attempts: list[str] = []
        consecutive_failures = 0

        # Per-task config plumbing
        cls_labels: list[str] | None = None
        tool_defs = None
        seed_data: list[dict] | None = None
        if isinstance(request, SDGRequestWithSeed):
            seed_data = request.seed_data
        if isinstance(request, SDGRequestDescriptionOnly):
            if request.classification_config is not None:
                cls_labels = request.classification_config.labels
            if request.tool_calling_config is not None:
                tool_defs = request.tool_calling_config.tool_definitions

        dedup = Deduplicator()
        if seed_data:
            dedup.seed(request.task_type, seed_data)

        def _emit() -> None:
            if progress_cb is None:
                return
            progress_cb(
                GenerationProgress(
                    phase="generating",
                    samples_generated=len(accepted),
                    samples_target=target,
                    samples_valid=len(accepted),
                    samples_rejected=rejected_total,
                    duplicates_removed=duplicates_total,
                )
            )

        _emit()

        while len(accepted) < target:
            if consecutive_failures >= self._max_consec_fail:
                last = failed_attempts[-1] if failed_attempts else "unknown"
                raise SDGAbortedError(
                    f"SDG aborted: {consecutive_failures} consecutive failed batches; "
                    f"last error: {last}"
                )

            remaining = target - len(accepted)
            this_batch = min(self._batch_size, remaining)
            prompt = build_prompt(
                request.task_type,
                request.sdg_mode,
                task_description=request.task_description,
                batch_size=this_batch,
                seed_data=seed_data,
                classification_labels=cls_labels,
                tool_definitions=tool_defs,
            )

            rows: list | None = None
            last_err: str | None = None
            for attempt in range(1, self._max_attempts + 1):
                try:
                    chat = self._client.chat(
                        system=prompt.system,
                        user=prompt.user,
                        temperature=request.temperature,
                        model=request.teacher_model,
                        response_format=JSON_OBJECT_RESPONSE_FORMAT,
                    )
                    api_calls += 1
                    rows = self._parse_samples(chat.content)
                    last_err = None
                    break
                except (json.JSONDecodeError, ValueError) as exc:
                    last_err = f"parse error (attempt {attempt}): {exc}"
                    log.warning(last_err)
                except Exception as exc:  # noqa: BLE001 — log + retry on any provider error
                    last_err = f"api error (attempt {attempt}): {type(exc).__name__}: {exc}"
                    log.warning(last_err)
                    api_calls += 1

            if rows is None:
                consecutive_failures += 1
                failed_attempts.append(last_err or "unknown")
                continue
            consecutive_failures = 0

            valid_rows, failures = validate_generated_rows(
                request.task_type,
                rows,
                classification_labels=cls_labels,
                tool_definitions=tool_defs,
            )
            rejected_total += len(failures)

            dedup_res = dedup.filter(request.task_type, valid_rows)
            duplicates_total += dedup_res.duplicate_count

            for row in dedup_res.unique:
                if len(accepted) >= target:
                    break
                accepted.append(row)

            _emit()

        return SDGRunResult(
            valid_rows=accepted[:target],
            rejected_count=rejected_total,
            duplicate_count=duplicates_total,
            api_calls=api_calls,
            failed_attempts=failed_attempts,
        )

    @staticmethod
    def _parse_samples(raw: str) -> list[dict]:
        """Parse the teacher's JSON response into a list of row dicts.

        Tolerates accidental markdown code fences around the JSON.
        """
        text = raw.strip()
        if text.startswith("```"):
            text = text.lstrip("`")
            if text.lower().startswith("json"):
                text = text[4:]
            # Drop everything after the first closing fence (if any).
            text = text.split("```", 1)[0].strip()
        obj = json.loads(text)
        if not isinstance(obj, dict) or "samples" not in obj:
            raise ValueError("teacher response missing 'samples' key")
        samples = obj["samples"]
        if not isinstance(samples, list):
            raise ValueError("'samples' must be a JSON array")
        return samples


__all__ = [
    "GenerationProgress",
    "ProgressCallback",
    "SDGRunResult",
    "SDGAbortedError",
    "SyntheticDataGenerator",
]
