# ADR-005: Only 3 Supported Task Types

**Status:** Accepted
**Confidence:** High
**Date:** 2026-05-07
**Supersedes:** —

## Context + Decision Drivers

A general "fine-tune anything" platform requires task-agnostic data formats, formatters, and evaluation metrics — that's a much bigger surface than a PoC needs. By fixing the task universe, we can:

- Provide concrete Pydantic schemas (no `Any` blobs)
- Pre-build prompt templates per task × SDG mode
- Pre-build evaluators per task
- Give the frontend dynamic-form metadata via `/api/v1/tasks`

## Decision

The platform supports exactly three `TaskType` values:

### 1. `classification`
```json
{"text": "I can't log into my account", "label": "technical"}
```
Single-label, multi-class. Labels come from a user-provided list (with-seed) or `classification_config.labels` (description-only).

### 2. `tool_calling`
```json
{
  "question": "Margherita pizza recipe says oven needs 250°C, currently at 100°C",
  "answer": "{\"name\":\"wait\",\"parameters\":{\"seconds\":150}}"
}
```
**Note:** `answer` is a JSON **string** (not a nested object) containing `name` and `parameters` keys.

### 3. `qa`
```json
{"question": "What is the return policy?", "answer": "You can return items within 30 days of purchase."}
```
Open-ended natural-language answer.

## Alternatives Considered

- **Generic "instruction tuning" task** — rejected; harder to evaluate, no clear metric
- **Add NER / summarization / translation** — rejected; out of scope for PoC; can be added later via new ADR
- **Allow custom user-defined task types** — rejected; explodes the validator/formatter/evaluator matrix

## AI Instructions

**Guidance Level: STRICT**

- The `TaskType` enum has exactly 3 values: `classification`, `tool_calling`, `qa`. Do not add more without superseding this ADR.
- Each new piece of pipeline code must handle all 3 task types — no `if task_type == "classification": ...` orphan branches.
- Per-task code lives in clearly named files:
  - `ai_engine/data_gen/prompts.py` — 6 templates (3 tasks × 2 SDG modes)
  - `ai_engine/data_gen/validators.py` — 3 validators
  - `ai_engine/training/data_formatters.py` — 3 formatters
  - `ai_engine/evaluation/metrics_classification.py`
  - `ai_engine/evaluation/metrics_tool_calling.py`
  - `ai_engine/evaluation/metrics_qa.py`
- For `tool_calling`, validate that `answer` is a **JSON-parseable string** with `name` and `parameters` fields — not an object
- The `/api/v1/tasks` endpoint is the source of truth that the frontend reads — keep it in sync with this ADR

## Data Format → Training Format Mapping

| Task | Training format |
|------|-----------------|
| classification | `### Text: {text}\n### Label: {label}` |
| tool_calling | ChatML with system prompt listing tool schemas |
| qa | Alpaca-style instruction format |

## Consequences

✅ Clear contracts for frontend, frontend can render task-specific UI
✅ Each pipeline stage has a small, finite switch (3 cases)
✅ Evaluators are precise per task
⚠️ Adding a 4th task type is a non-trivial change touching SDG / training / eval / API metadata — must go through ADR
