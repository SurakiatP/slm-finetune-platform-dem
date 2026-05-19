# Node 9 (Evaluation) — Tier 3 Baseline Reference

## What's here

| File | Source | Use |
|------|--------|-----|
| `session-26-2026-05-18_eval-start.json` | `POST /api/v1/evaluations` initial response | Verify 202 + status=pending shape |
| `session-26-2026-05-18_eval-final.json` | `GET /api/v1/evaluations/{id}` after `status=completed` | The contract — keys, types, structure that post-refactor must produce |

## Origin

Captured during Session 26 live E2E on vast.ai (RTX 3070 8 GB, 2026-05-18).
See `WORKING_LOG.md` Session 26 for context. Smoke run used:
- Project: classification (Thai customer service taxonomy, 4 labels)
- Base model: `unsloth/Llama-3.2-1B-Instruct-bnb-4bit`
- Training: 6 steps × 90 examples (smoke scale — not for accuracy)
- Eval: rule-based (no LLM judge), holdout 20 rows
- Result: `accuracy=0.0` because model didn't learn classification format at 6 steps

## How Tier 3 verification uses this (after refactor)

The numeric values **will not match** in a future vast.ai run because:
- LoRA init is random → different weights → different inferences → different metrics
- The artifact + dataset from Session 26 are gone (VM destroyed)

What MUST match (schema contract):
- All top-level keys: `id`, `model_artifact_id`, `dataset_id`, `celery_task_id`, `status`, `metrics_json`, `llm_judge_score`, `llm_judge_model`, `error_message`, `started_at`, `ended_at`, `created_at`, `updated_at`
- `status == "completed"` after polling
- `metrics_json` for classification has: `n`, `labels`, `accuracy`, `f1_macro`, `f1_per_label`, `confusion_matrix`, `out_of_set_predictions`
- `metrics_json.n == 20` (holdout size identical)
- `llm_judge_score == null` (rule-based run)
- `labels` is a sorted list of unique strings
- `confusion_matrix` is N×N square (N = len(labels))
- All timestamps ISO 8601 with `Z` suffix

Tier 3 verification script (post-refactor):
1. Deploy refactor branch to fresh vast.ai
2. Run minimal flow (project → seed-cls → train Llama-1B → export → eval rule-based)
3. Dump final eval response to `tmp-post-refactor-eval.json`
4. `python tests/fixtures/baseline/node-9/verify_schema.py tmp-post-refactor-eval.json`
   (script to be added in Step 5 of refactor workflow)
