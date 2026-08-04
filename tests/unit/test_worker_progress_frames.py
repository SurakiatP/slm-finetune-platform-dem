"""Unit tests for the intermediate progress frames added to the two silent tasks.

Before this branch, `workers/tasks/evaluation.py` and
`workers/tasks/model_export.py` each published exactly two messages, both
terminal — a client watching a multi-minute export over `/ws/jobs/{id}` saw
silence and then one frame (`docs/03-realtime-websocket.md` §5).

Covered here:
  1. Frame schemas    — the stage/phase vocabularies are closed sets.
  2. Evaluation       — `_predict_rows` drives its callback per row, and
     `run_evaluation`'s throttle keeps the WS from being flooded.
  3. Export           — the six stages are wired, and the `except BaseException`
     that makes cancellation observable is still in place.

No GPU, no Ollama, no MinIO, no broker — the export *pipeline* itself needs a
GPU and is verified separately on real hardware; what is unit-testable here is
the wiring around it.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from pydantic import ValidationError

from api.schemas.enums import TaskType, WSMessageType
from api.schemas.progress import EvaluationProgress, ExportProgress
from workers.tasks import evaluation as eval_task
from workers.tasks import model_export as export_task

_EXPORT_STAGES = (
    "downloading",
    "merging",
    "converting",
    "quantizing",
    "uploading",
    "registering",
)

_EXPORT_SOURCE = Path(export_task.__file__).read_text(encoding="utf-8")


# =============================================================================
# 1. Frame schemas
# =============================================================================


class TestFrameSchemas:
    def test_new_message_types_are_registered(self) -> None:
        assert WSMessageType.EXPORT_PROGRESS.value == "export_progress"
        assert WSMessageType.EVALUATION_PROGRESS.value == "evaluation_progress"

    @pytest.mark.parametrize("stage", _EXPORT_STAGES)
    def test_every_pipeline_stage_is_a_valid_frame(self, stage: str) -> None:
        frame = ExportProgress(job_id="j", stage=stage)
        assert frame.type is WSMessageType.EXPORT_PROGRESS
        assert frame.detail is None

    def test_unknown_stage_is_rejected(self) -> None:
        """The stage vocabulary is a contract, not free text."""
        with pytest.raises(ValidationError):
            ExportProgress(job_id="j", stage="teleporting")

    def test_detail_carries_the_quant_level(self) -> None:
        frame = ExportProgress(job_id="j", stage="quantizing", detail="q4_k_m")
        assert frame.detail == "q4_k_m"

    @pytest.mark.parametrize("phase", ["predicting", "scoring", "judging"])
    def test_evaluation_phases(self, phase: str) -> None:
        assert EvaluationProgress(job_id="j", phase=phase, rows_done=0, rows_total=1)

    def test_evaluation_rejects_negative_counters(self) -> None:
        with pytest.raises(ValidationError):
            EvaluationProgress(job_id="j", phase="predicting", rows_done=-1, rows_total=5)


# =============================================================================
# 2. Evaluation
# =============================================================================


class _FakeResponse:
    @staticmethod
    def raise_for_status() -> None:
        return None

    @staticmethod
    def json() -> dict:
        return {"choices": [{"message": {"content": "answer"}}]}


class _FakeClient:
    def __init__(self, *a, **kw) -> None:
        pass

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *a) -> None:
        return None

    def post(self, *a, **kw) -> _FakeResponse:
        return _FakeResponse()


@pytest.fixture
def stub_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(eval_task.httpx, "Client", _FakeClient)


class TestEvaluationProgressWiring:
    def test_progress_cb_is_optional(self) -> None:
        """Existing callers (and the snapshot tests) must keep working."""
        param = inspect.signature(eval_task._predict_rows).parameters["progress_cb"]
        assert param.default is None

    def test_callback_fires_once_per_row(self, stub_ollama) -> None:
        seen: list[tuple[int, int]] = []
        rows = [{"question": f"q{i}", "answer": "a"} for i in range(5)]

        eval_task._predict_rows(
            rows=rows,
            task_type=TaskType.QA,
            tool_definitions=None,
            ollama_base_url="http://ollama",
            ollama_tag="m:latest",
            progress_cb=lambda done, total: seen.append((done, total)),
        )

        assert seen == [(1, 5), (2, 5), (3, 5), (4, 5), (5, 5)]

    def test_counters_end_at_completion(self, stub_ollama) -> None:
        """The UI must land on rows_done == rows_total, never 4/5."""
        seen: list[tuple[int, int]] = []
        rows = [{"question": "q", "answer": "a"} for _ in range(3)]

        eval_task._predict_rows(
            rows=rows,
            task_type=TaskType.QA,
            tool_definitions=None,
            ollama_base_url="http://ollama",
            ollama_tag="m:latest",
            progress_cb=lambda done, total: seen.append((done, total)),
        )

        assert seen[-1] == (3, 3)

    def test_works_without_a_callback(self, stub_ollama) -> None:
        predicted, expected, questions = eval_task._predict_rows(
            rows=[{"question": "q", "answer": "a"}],
            task_type=TaskType.QA,
            tool_definitions=None,
            ollama_base_url="http://ollama",
            ollama_tag="m:latest",
        )
        assert len(predicted) == len(expected) == len(questions) == 1

    def test_throttle_is_time_based_and_documented(self) -> None:
        """One frame per row would flood the WS on a 500-row eval."""
        assert eval_task._PREDICT_PROGRESS_THROTTLE_SECONDS == 2.0


# =============================================================================
# 3. Export
# =============================================================================


class TestExportProgressWiring:
    def test_quantize_helper_takes_an_optional_callback(self) -> None:
        param = inspect.signature(export_task._quantize_merged_to_gguf).parameters[
            "progress_cb"
        ]
        assert param.default is None, "existing callers must keep working"

    @pytest.mark.parametrize("stage", _EXPORT_STAGES)
    def test_every_stage_is_emitted_somewhere(self, stage: str) -> None:
        """Each stage must appear as an actual emit call — `publish_stage(...)`
        in the task body, or `progress_cb(...)` inside `_quantize_merged_to_gguf`,
        which only receives the publisher as a callback."""
        emitted = (
            f'publish_stage("{stage}"' in _EXPORT_SOURCE
            or f'progress_cb("{stage}"' in _EXPORT_SOURCE
        )
        assert emitted, (
            f"stage {stage!r} is in the ExportProgress vocabulary but is never "
            f"published by workers/tasks/model_export.py"
        )

    def test_export_status_lifecycle_is_wired(self) -> None:
        for marker in ("JobStatus.RUNNING", "JobStatus.COMPLETED", "JobStatus.FAILED"):
            assert marker in _EXPORT_SOURCE

    def test_catches_baseexception_so_cancellation_is_observable(self) -> None:
        """REGRESSION GUARD. `revoke(terminate=True, SIGTERM)` reaches the worker
        child as a `SystemExit` raised inside the task body — a `BaseException`,
        which `except Exception` does not catch. Narrowing this back would leave
        `export_status` stuck at RUNNING forever after every cancel.
        """
        assert "except BaseException as exc:" in _EXPORT_SOURCE

    def test_cancelled_status_is_not_clobbered_to_failed(self) -> None:
        """The API sets CANCELLED *before* revoking; the worker's cleanup must
        not overwrite that with FAILED."""
        assert "!= JobStatus.CANCELLED" in _EXPORT_SOURCE

    def test_failure_path_still_reraises(self) -> None:
        """Swallowing the exception would make Celery record the task as OK."""
        tail = _EXPORT_SOURCE.split("except BaseException as exc:")[1]
        assert "\n            raise\n" in tail

    def test_gpu_memory_is_still_released(self) -> None:
        assert "_release_gpu_memory()" in _EXPORT_SOURCE
