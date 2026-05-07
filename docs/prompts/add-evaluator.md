# Prompt Template: Add an Evaluation Metric

> Use this when adding a new metric to `ai_engine/evaluation/`. Each task type has its own metrics module.

---

```
Add metric `<metric_name>` to `ai_engine/evaluation/metrics_<task>.py`.

Context:
- Read `CLAUDE.md` and `docs/adr/ADR-005-three-task-types.md`.
- Task type: <classification | tool_calling | qa>
- This metric measures: <one-line description>

Signature:
```python
def <metric_name>(
    predictions: list[<PredType>],
    references: list[<RefType>],
) -> float:
    """<One-line summary>.

    Args:
        predictions: Model outputs, length N.
        references: Ground-truth labels/answers, length N (same order).

    Returns:
        A scalar in <range>.

    Raises:
        ValueError: If lengths differ or inputs are empty.
    """
```

Implementation:
- Pure function, no I/O
- Accept Python lists, not numpy arrays — keep the call site simple
- Validate `len(predictions) == len(references)` and non-empty
- For per-class breakdown, return a structured dict alongside (use a separate function — don't overload)

Tests:
- In `tests/ai_engine/evaluation/test_metrics_<task>.py`:
  - Perfect predictions → expected max score
  - Worst predictions → expected min score
  - Tied / ambiguous case → known reference value
  - Length mismatch → raises ValueError
  - Empty input → raises ValueError

Wiring:
- If this metric should be reported in the standard evaluation output, register it in
  `ai_engine/evaluation/__init__.py`'s task→metrics map
- Add the metric name to the `EvaluationResult` schema in `api/schemas/evaluations.py`

Definition of Done:
- Tests pass (including edge cases)
- Metric appears in the `POST /evaluations` response
- Update `WORKING_LOG.md` and `TASK_TRACKER.md`
```

---

## Per-task notes

- **classification** — accuracy, precision/recall/F1 (macro + per-class), confusion matrix shape
- **tool_calling** — JSON-validity rate, name accuracy, parameter-key accuracy, parameter-value accuracy (string match or LLM judge)
- **qa** — ROUGE-1/2/L, BLEU, exact-match, optional LLM-judge for semantic equivalence

For LLM-judge style metrics (subjective quality), use the OpenRouter client (ADR-003) — never a separate provider SDK.
