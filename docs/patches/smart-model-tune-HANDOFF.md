# Engine backend — handoff to the `smart-model-tune` team

**Date:** 2026-08-10 · **Backend:** `dev @ ef38d44` · **Contract:**
`openapi.json` (40 paths / 47 operations), regenerated and verified current
on this date.

**Short version:** the Engine is feature-complete for everything your app
does today, and the whole pipeline has been run end-to-end on the GPU box —
not simulated, not unit-tested only. You can start integrating. Sections 1–3
tell you what to point at, section 4 tells you what to wire, section 5 tells
you honestly what is *not* there so you don't go looking for it.

---

## 1. What was verified, on real hardware

One continuous lineage on the deployment box (project `f88d2561`), every
stage a real run:

| Stage | Result |
|---|---|
| **SDG** | 10 synthetic support tickets generated (Thai + English), plus an automatic 2-row holdout split. Cost metered: $0.0062 |
| **Fine-tune** | Qwen2.5-0.5B, QLoRA, on an RTX 3090 Ti. MLflow run logged, adapter stored |
| **Export** | GGUF q4_k_m produced, uploaded, and registered with Ollama as `slm/598d87d6` |
| **Serve** | That model answers `POST /inference/chat/completions` with 200 + token usage |
| **Evaluate** | Rule-based metrics computed on the holdout (accuracy, f1_macro, per-label, confusion matrix) |

Also verified live: cancel across all job types, WebSocket progress frames
including intermediate export stages, the audit trail (9 events in order),
Prometheus alerting (a real alert fired and self-resolved), and a full
backup → restore → verify cycle.

**One result worth understanding, because you will see it too:** that
evaluation returned `accuracy: 0.0` with `out_of_set_predictions: 2`. That
is not a bug — a 0.5B model trained on 9 examples for 3 steps replies in
prose instead of emitting one of the three labels, and the evaluator
correctly *reported* that rather than scoring nonsense. Real training runs
use far more data. Treat `out_of_set_predictions` as a signal to show the
user, not an error.

---

## 2. Connecting

```
Base URL:  https://<the API hostname you were given>
Contract:  GET {base}/openapi.json   ← always authoritative; generate your
                                        types from this, not from docs
Health:    GET {base}/health         ← liveness, no auth
```

**No authentication is required today.** `AUTH_REQUIRED=false`. Send requests
as you do now. When we flip it on, the patch you need is
`docs/patches/smart-model-tune-auth.md` — it is written, verified, and
nothing here changes before you have applied it. Do not build auth yet.

**CORS:** your deployed origin must be in the Engine's allowlist. If every
call works in `curl` but fails in the browser with an opaque "Failed to
fetch", that is CORS and not your code — tell us your exact origin (scheme +
host, no trailing slash) and we will add it. Dev origins
(`localhost:3000`, `localhost:5173`) are already allowed.

---

## 3. What you already call — no action needed

Paths below are **absolute**. Your `apiFetch` prepends `/api/v1`, so in your
code these appear as `/projects`, `/datasets/{id}` and so on.

`POST /api/v1/projects` · `GET /api/v1/projects/{project_id}` ·
`GET /api/v1/projects?external_project_id=` ·
`POST /api/v1/datasets/upload-seed` · `POST /api/v1/datasets/generate` ·
`GET /api/v1/datasets/{dataset_id}` · `POST /api/v1/trainings` ·
`GET /api/v1/trainings/{training_id}` ·
`GET /api/v1/trainings/{training_id}/loss-history` ·
`DELETE /api/v1/trainings/{training_id}` · `GET /api/v1/models` ·
`GET /api/v1/models/{model_id}` · `POST /api/v1/models/{model_id}/export` ·
`GET /api/v1/inference/models` ·
`POST /api/v1/inference/chat/completions` · `GET /health`
· `WS /ws/jobs/{job_id}`

All correct against the current contract. The `external_project_id` mapping
in particular is wired exactly as designed.

---

## 4. What to wire next

**→ `docs/patches/smart-model-tune-unused-endpoints.md`** is the working
document. It lists the **27 paths you do not call yet**, grouped by which of
your screens each one unlocks, with exact request/response shapes read out of
`openapi.json`.

The short form, in the order we suggest:

1. **Three status codes you currently collapse into one error** — `429`
   (quota; read the `Retry-After` header), `402` (budget cap), `503`
   (provider circuit breaker). Smallest change, biggest drop in confusing
   failures.
2. **Download buttons** — they cannot work as built. Object storage is on an
   internal network; only `GET /api/v1/datasets/{dataset_id}/download-url`
   and `GET /api/v1/models/{model_id}/download-url` return a
   browser-reachable presigned URL. Fetch it at click time (it expires).
   Pass the **Engine `ModelArtifact.id`**, not the Supabase row id.
3. **SDG reconnect after reload** — `DatasetResponse.celery_task_id` exists
   (your `EngineDataset` interface doesn't declare it); that is the WS
   `job_id`, exactly like `training.celery_task_id` which you already use.
   Pair it with `GET /api/v1/jobs/{job_id}/progress` for an instant repaint.
4. **Evaluation** — your Evaluation screen computes scores client-side. The
   real pipeline exists: `POST /api/v1/evaluations` → progress over the same
   WebSocket → `GET /api/v1/evaluations/{evaluation_id}`. Proven working (§1).
5. **Cost / Analytics data** — `GET /api/v1/usage` and
   `GET /api/v1/projects/{project_id}/usage` carry real token and cost
   figures. `cost_usd` is a **string or null**; null means unpriced — render
   "tokens only", never `$0`.
6. **HPO** — `POST /api/v1/trainings` is a union on `mode`; you send
   `"manual"`, `"hpo"` is the other arm.

---

## 5. What is *not* there — please read before scoping

**Deployment, API Keys, and Analytics-as-a-product have no Engine backend at
all.** Those three screens read Supabase tables that nothing writes.
Concretely, the Engine has:

- **one shared** inference endpoint — no per-model deployment, no per-model
  URL, no endpoint lifecycle (activate/deactivate);
- **no API-key issuance and no per-key authentication or rate limiting**
  (your `apiKeysApi.ts` mints keys with `Math.random()`);
- **no per-endpoint call/latency analytics.** `GET /api/v1/usage` is
  *upstream provider spend* during SDG and evaluation — a different thing
  from "how many calls did my deployed model serve, and how fast".
  `GET /api/v1/projects/{project_id}/activity` can give you a rough call
  count from the audit log, but carries no latency and no time bucketing.

This is an **open product decision, not backend work we forgot**. Nothing in
the requirements documents ever asked for a model-serving layer. If those
screens matter for launch, that needs a scoping conversation — please don't
spend time hunting for endpoints that were never specified.

Two smaller notes in the same spirit: your `TaskType` has 6 values but the
Engine supports exactly 3 (`classification`, `tool_calling`, `qa`) — pull the
live list from `GET /api/v1/tasks` and the model list from
`GET /api/v1/base-models` rather
than hardcoding, and the dead options disappear on their own.

---

## 6. Known limitations, stated plainly

- **`holdout_size` defaults to 100.** On a small SDG request that exceeds
  what you asked to generate — always set it explicitly.
- **Cost is an estimate**, derived from published provider rates, not an
  invoice. Token counts are ground truth.
- A cancel issued while no worker is running used to be able to complete
  anyway; **fixed and verified on 2026-08-10** — a cancelled job now stays
  cancelled and emits a terminal WebSocket frame so your watchers resolve.
- WebSocket close codes `4401`/`4403` are **not distinguishable by a
  browser** (you will see `1006` either way). Do not branch on them; treat
  any close as "reconnect or show disconnected".

---

## 7. Who to ask

Anything in this document that does not match what you observe: tell us.
Every field and path here was generated from `openapi.json` on the date at
the top, and a mismatch means either your build is pointing at an older
Engine or we made an error worth correcting. There is a guard test in the
backend (`tests/unit/test_frontend_handoff_docs.py`) that fails CI if these
documents drift from the live contract, so they should stay true.
