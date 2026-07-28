# 04 — Frontend Integration: `smart-model-tune`

## Purpose & Audience

This document is a handoff for the **`smart-model-tune` integration
developer** (a separate, Lovable-managed React/Vite SPA — see the
workspace `CLAUDE.md`). It maps every backend capability to the
`smart-model-tune` screen/function that should call it, marks what is
**already correct** (leave it alone), and what is **mismatched or not
wired** (with the concrete fix). It is documentation only — nothing in
`smart-model-tune/` was edited to produce this file; all fixes described
below must be made by the frontend team in their own repo/editor.

Two important scoping notes:

- `smart-model-tune` talks to **two backends**: Supabase (auth + its own
  `projects`/`trained_models`/`datasets` tables) and this repo's FastAPI
  "Engine" API (`VITE_ENGINE_HOST`, proxied at `/api/v1`). This document
  covers the **Engine side only** — the `src/lib/engineApi.ts` client and
  everything downstream of it. Supabase-only CRUD (auth, dashboards,
  billing) is out of scope.
- This repo also ships its own embedded `frontend/` (on `feat/web-ui`),
  which is a **separate, already-correctly-wired** integration of the
  same API. It is not discussed here except as a reference point — see
  `frontend/src/api/` if you want to see a working implementation of any
  call described below.

## How to Consume the Spec

- Full machine-readable contract: `docs/openapi.json` (this directory) —
  regenerate from a running instance via `GET /openapi.json`, or browse
  interactively at `/docs` (Swagger UI) / `/redoc`.
- Endpoint-by-endpoint prose reference: `docs/02-api-reference.md`.
- WebSocket message shapes and reconnection contract:
  `docs/03-realtime-websocket.md`.
- Architecture / hexagonal layout: `docs/01-architecture.md`.
- All backend Pydantic schema source lives under `api/schemas/*.py` in
  this repo if you need to read validators directly instead of the
  generated JSON Schema.

## The 3 Supported Task Types Only

The backend's `TaskType` enum (`api/schemas/enums.py`) has exactly three
values — this is ADR-005, a hard constraint, not a current gap:

| Value | Meaning |
|---|---|
| `classification` | Categorize text into predefined labels |
| `tool_calling` | Map natural language to a function/tool call |
| `qa` | Answer questions from context |

`smart-model-tune`'s `TaskType` (`src/types/index.ts:2`) has **6** values:
`classification`, `ner`, `qa`, `function-calling`, `extraction`,
`ranking`. Its own mapping table,
`TASK_TYPE_TO_ENGINE` (`src/lib/engineMappings.ts:5-12`), already encodes
this correctly — `ner`/`extraction`/`ranking` map to `null` and
`NewProject.tsx:161-169` blocks launch with a toast when the mapping is
`null`. **This guard is correct and should not be changed.** The
remaining gap is cosmetic/UX only: `TaskSelectionStep.tsx:6-45` still
*presents* all 6 task types as selectable cards (with fabricated
examples for the 3 unsupported ones) before the launch-time guard
rejects them. Recommended fix: either hide the 3 unsupported cards, or
badge them "coming soon" — driven from `GET /api/v1/base-models` /
`GET /api/v1/tasks` (see the "not wired" rows below) rather than
duplicating the enum by hand.

## Endpoint → Screen Mapping

Legend: ✅ already correct (do not change) · ⚠️ mismatched (fix
described) · ❌ not wired (endpoint exists, nothing in `smart-model-tune`
calls it).

| Backend endpoint | `smart-model-tune` screen/function | Status | What to change |
|---|---|---|---|
| `POST /api/v1/projects` | `engineCreateProject` (`src/lib/engineApi.ts:114-123`), called from `NewProject.tsx:193-197` | ⚠️ | Request shape (`name`, `description`, `task_type`) is correct, but `external_project_id` is never sent even though `ProjectCreate` (`api/schemas/projects.py:13`) accepts it and `ProjectResponse` returns it. Pass the Supabase project's own `id` (the `created.id` already computed at `NewProject.tsx:180-188`) as `external_project_id` so the mapping is durable server-side instead of living only in `localStorage`. |
| `GET /api/v1/projects/{id}` | `engineGetProject` (`engineApi.ts:125-127`) | ❌ | Defined, never called anywhere in `src/`. Nothing currently detects drift between the Supabase `projects` row and the Engine project (e.g. if the Engine project was deleted server-side). Not urgent, but worth a periodic reconciliation check on `ProjectDetail.tsx` if `external_project_id` round-tripping (row above) is adopted. |
| `PATCH /api/v1/projects/{id}` | — | ❌ | Not called. `smart-model-tune` only ever mutates the Supabase `projects` row (`updateProject` in `src/lib/projectsApi.ts`); the Engine-side project name/description can drift silently. Low priority. |
| `DELETE /api/v1/projects/{id}` | — | ❌ | Not called. Deleting a project in Supabase does not delete the corresponding Engine project/datasets/trainings (cascade only happens on the Engine side if this endpoint is hit). Worth wiring into whatever "delete project" UI action exists so Engine storage doesn't orphan. |
| `POST /api/v1/datasets/upload-seed` | `engineUploadSeed` (`engineApi.ts:131-154`), called `NewProject.tsx:98` | ✅ request / ⚠️ response | The multipart request (`file`, `project_id`, `task_type`, `name`) is correct. The response is under-consumed: `EngineSeedUploadResponse` (`engineApi.ts:19-24`) only types `dataset_id`/`task_type`/`num_samples`/`invalid_rows`, and `NewProject.tsx:98-99` reads only `.dataset_id`. Backend's `SeedUploadResponse` (`api/schemas/*` seed response, Phase 9) also returns `format_detection` (schema-remap audit trail) and `pdf_uri` (QA+PDF uploads) — neither is typed nor surfaced. `invalid_rows` is typed but never read either. Add the two missing fields to the TS type and surface a toast/banner for dropped rows / remapped columns / PDF confirmation. |
| `POST /api/v1/datasets/generate` (`sdg_mode="with_seed"`) | `engineGenerateDataset` (`engineApi.ts:158-176`), called `NewProject.tsx:102-107` | ❌ **broken** | See Priority Fix #1 below — sends inline `seed_data` instead of `seed_dataset_id`. Every real launch 422s. |
| `POST /api/v1/datasets/generate` (`sdg_mode="description_only"`) | — | ❌ | Not wired at all — no UI path generates from a task description alone (no seed upload), even though `SDGRequestDescriptionOnly` (`api/schemas/sdg.py`) supports it with `classification_config`/`tool_calling_config` schemas (label lists / tool definitions). This is `backend-required-changes.md` item 3, still open on the frontend side. Would let users skip the seed-file step entirely for classification/tool_calling. |
| `GET /api/v1/datasets/{id}` | `engineGetDataset` (`engineApi.ts:180-182`), polled by `pollDatasetReady` (`NewProject.tsx:71-78`) | ⚠️ | Polling logic only checks `storage_uri && num_samples > 0` to decide "ready," and the return type (`engineApi.ts:180`) doesn't even declare `status`/`error_message`. A **failed** SDG job (bad seed data, OpenRouter error, etc.) has `storage_uri = null` forever, so the poll silently spins for the full `maxAttempts=60 × 5s = 5 minutes` before throwing a generic "Dataset generation timed out" instead of surfacing the real `error_message` immediately. Add `status`/`error_message` to the return type and short-circuit the poll loop on `status === "failed"`. |
| `DELETE /api/v1/datasets/{id}` | — | ❌ | Not called; no dataset-delete UI action found. |
| `GET /api/v1/datasets/{id}/download`, `GET /api/v1/datasets/{id}/preview` | — | ❌ | Not called; no dataset preview/download UI in `smart-model-tune`. |
| `GET /api/v1/datasets` | — | ❌ | Not called; dataset listing (if any) is Supabase-only (`src/lib/datasetsApi.ts`), disconnected from real Engine dataset rows. |
| `POST /api/v1/trainings` (`mode="manual"`) | `engineStartTraining` (`engineApi.ts:200-218`), called `runEngineFlow` (`NewProject.tsx:114-122`) | ✅ shape / ⚠️ base_model | Request shape (`project_id`, `dataset_id`, `base_model`, `training_name`, `manual_config`) matches `ManualTrainingRequest` (`api/schemas/training.py:222`). But see Priority Fix #2 — one of the 6 `BASE_MODEL_TO_ENGINE` entries maps to a model ID the backend rejects. |
| `POST /api/v1/trainings` (`mode="hpo"`) | Auto-Tuning tab, `ProjectDetail.tsx:298-320` (`TuningReport`/`TuningHistory`/`getLatestTuningRun`) | ❌ | Zero backend calls — confirmed no reference to `hpo`/`HPOConfig`/`hpo_config` anywhere in `src/`. The entire tuning report and tuning history are generated client-side (`src/lib/tuningGenerator.ts`). `HPOTrainingRequest` (`api/schemas/training.py:263`, needs `hpo_config`: `n_trials`, `search_space`, `sampler`, `pruner`, `objective_metric`) is a real, working endpoint. This demos as if HPO ran; it didn't. |
| `GET /api/v1/trainings/{id}` | `engineGetTraining` (`engineApi.ts:220-222`) | ❌ | Defined, never called. There's no fallback to the canonical training resource when the WebSocket gives up (`useTrainingWebSocket.ts:67`, `MAX_ATTEMPTS = 8`) — if reconnection is exhausted, the UI has no way to learn the training's final status/`error_message`. Poll this endpoint once the WS hook's `connected` flips permanently false without a `completed`/`failed` event. |
| `DELETE /api/v1/trainings/{id}` | `engineCancelTraining` (`engineApi.ts:224-226`) | ❌ | Defined, never called. No "cancel training" UI action exists anywhere in `smart-model-tune`. |
| `GET /api/v1/trainings/{id}/loss-history` | — | ❌ | **Not called at all.** `TrainingMonitor.tsx:14,35,146` renders `mockLossCurve` with the in-code comment *"The API does not yet expose historical loss points; only live WebSocket values are real"* (`TrainingMonitor.tsx:34`) — this comment is stale/incorrect; the endpoint has existed since before this integration was built (`backend-required-changes.md` item 2: "✅ Already existed"). `TrainingLossHistoryResponse` (`api/schemas/trainings.py:86`) returns `train_loss`/`eval_loss` point arrays plus `mlflow_run_id`. Fetch this on mount (and on WS `completed`) and use it as the initial series, appending live WS points as they arrive — this also fixes the "loss curve resets to nothing on page reload" gap. |
| `GET /api/v1/trainings/{id}/metrics` | — | ❌ | Not called. Full metric history + HPO child-trial summary; only relevant once the HPO row above is wired. |
| `GET /api/v1/trainings/{id}/mlflow-url` | — | ❌ | Not called directly, but `mlflow_run_id`/`mlflow_url` already arrive inline on `EngineTrainingAccepted` (`engineApi.ts:33-40`) from the `POST /trainings` response and are stored nowhere, rendered nowhere. Add an "Open in MLflow" link using the value already in hand — no extra call needed for the common case. |
| `WS /ws/jobs/{job_id}` (training progress) | `useTrainingWebSocket` (`src/hooks/useTrainingWebSocket.ts`), consumed by `useTrainingSimulator.ts` and `TrainingMonitor.tsx:30-32` | ✅ | **Correctly wired** — URL construction (`buildWsUrl`, `useTrainingWebSocket.ts:46-52`) matches `SDGJobAcceptedResponse`/`TrainingJobAcceptedResponse.websocket_url`'s `/ws/jobs/{job_id}` path, the `training_progress`/`completed`/`failed` event shapes match the backend WS contract, and the exponential-backoff reconnect (`MAX_ATTEMPTS=8`, capped at 30s) is a reasonable client. Do not change. See `docs/03-realtime-websocket.md` for the full message contract this depends on. |
| `WS /ws/jobs/{job_id}` (SDG progress) | — | ❌ | `SDGJobAcceptedResponse.websocket_url` (`engineApi.ts:26-31`, `EngineSdgResponse.websocket_url`) is returned and typed but never connected to — `NewProject.tsx`'s `runEngineFlow` only polls `GET /datasets/{id}` (see row above) instead of subscribing to the SDG job's own WS channel for live progress. Same hook (`useTrainingWebSocket`, generically named enough to reuse) could drive an SDG progress indicator during the "generating dataset" step instead of a blind 5s poll loop. |
| `GET /api/v1/models` | `engineGetModelArtifacts` (`engineApi.ts:230-232`) | ❌ **not wired — high impact** | Defined, **never called anywhere**. `Models.tsx:9,13` and `ModelDetail.tsx:10,78` read exclusively from the Supabase `trained_models` table via `src/lib/modelsApi.ts`, which nothing in the app ever populates from a real completed training — `useTrainingSimulator.ts:29-33` only writes `modelArtifactId` into `localStorage` (`engineStore.ts`) on WS `completed`, it never inserts a Supabase `trained_models` row or calls this endpoint. Net effect: the Models list / Model Detail / Playground model picker show **nothing for real Engine-trained models** unless someone manually seeds the Supabase table. See Priority Fix below. |
| `GET /api/v1/models/{id}` | — | ❌ | Not called; same root cause as above. |
| `POST /api/v1/models/{id}/export` | `ModelDetail.tsx` "Export" tab, `exportFormats` array (`ModelDetail.tsx:13-17`) | ❌ **and factually wrong** | Not called — the export tab is a static list with hardcoded fake sizes and non-functional `Download` buttons (no `onClick`, `ModelDetail.tsx:220-223`). Worse: it lists **`ONNX`** as an export option (`ModelDetail.tsx:16`), which the backend does not support — `ArtifactFormat` (`api/schemas/enums.py:53`) is `lora \| gguf \| safetensors` only. Remove the ONNX row entirely (don't just leave it unwired — it's actively misleading), and wire the GGUF/SafeTensors rows to `POST /models/{id}/export` (`ModelExportRequest`: `format`, optional `quantization`), which returns a `job_id`/`websocket_url` for the same `/ws/jobs/{id}` progress channel used for training. |
| `GET /api/v1/models/{id}/download` | — | ❌ | Not called; see above — the Download buttons have no handler at all. |
| `GET /api/v1/inference/models` | `engineListInferenceModels` (`engineApi.ts:236-240`) | ❌ | Defined, never called. This is the correct source for "which Ollama-served models exist right now" (OpenAI-compatible `/v1/models` listing) — `Playground.tsx:11,15` instead sources its model dropdown from `useModels()` (Supabase `trained_models`), which has the same disconnect as the `GET /api/v1/models` row above. |
| `POST /api/v1/inference/chat/completions` | `engineChatCompletion` (`engineApi.ts:242-253`), called `ChatPanel.tsx:50-55` | ⚠️ **mismatched — high impact** | See Priority Fix below — `model` is sent as the Supabase display name, not a real Ollama tag, and failures are invisibly masked by a mock fallback. |
| `POST /api/v1/inference/completions` | — | ❌ | Legacy text-completion endpoint; not used, no gap (chat completions is the correct one for this UI). |
| `POST /api/v1/evaluations` | `ProjectDetail.tsx` "evaluation" tab (deterministic seeded fake metrics, `ProjectDetail.tsx:76-86`) and `TrainingMonitor.tsx` "evaluation" tab (`mockComparisonResults`, `TrainingMonitor.tsx:14,166`) | ❌ | Zero backend calls — confirmed no reference to `/evaluations` anywhere in `src/`. Both eval surfaces are entirely client-fabricated (`Math.random`/seeded-hash metrics), not backend results. `EvaluationCreate` (`api/schemas/evaluations.py:14`, needs `model_artifact_id` + `dataset_id`, optional `use_llm_judge`/`judge_model`) is a real, working endpoint but depends on `GET /api/v1/models` being wired first (needs a real `model_artifact_id`). |
| `GET /api/v1/evaluations`, `GET /api/v1/evaluations/{id}` | — | ❌ | Not called. |
| `POST /api/v1/evaluations/compare` | — | ❌ | Not called. `EvaluationCompareRequest` (`api/schemas/evaluations.py:58`, 2-10 `evaluation_ids`) would be the correct backend call for the A/B compare feature already present in `Playground.tsx` (`abMode`) — currently that toggle only runs two independent chat panels side by side, it does not compare structured eval metrics. |
| `GET /api/v1/tasks`, `GET /api/v1/tasks/{task_type}/example` | — | ❌ | Not called. `TaskSelectionStep.tsx:6-45` hardcodes all 6 task types (3 unsupported) with fabricated example strings instead of asking the backend which 3 are real. Low priority but removes a staleness source. |
| `GET /api/v1/base-models` | — | ❌ **and factually wrong** | Not called. `ModelSelectionStep.tsx` hardcodes 6 base models; see Priority Fix below — one of the 6 (`phi-3-mini`) doesn't exist in the backend's supported list at all. Switching to `GET /api/v1/base-models` (`BaseModelInfo[]`, backed by `SUPPORTED_BASE_MODELS` in `api/routers/tasks_meta.py:101-208`) removes this whole class of drift permanently — the backend added this exact live-config pattern for `GET /api/v1/sdg-pipeline` after the same staleness bug recurred 3 times on the embedded `frontend/` (see the backend repo's own `WORKING_LOG.md`, 2026-07-29 entry). |
| `GET /api/v1/sdg-pipeline` | — | ❌ | Not called; `smart-model-tune` doesn't display which LLM the SDG pipeline uses (generator/judge/diversity-rules). Cosmetic only — nice-to-have, not required. |
| `GET /health` | `engineHealthCheck` (`engineApi.ts:257-264`) | ❌ | Defined, never called anywhere in `src/`. No "Engine unreachable" banner exists — the only failure signal a user gets today is `ChatPanel`'s silent mock fallback (see Priority Fix below) or a generic launch-toast error in `NewProject.tsx`. |

34 REST endpoints + 1 WebSocket channel from `docs/openapi.json` are covered above.

## Priority Fix List

Ordered most-impactful first.

### 1. SDG generate sends inline `seed_data` instead of `seed_dataset_id` — every real launch 422s

`engineGenerateDataset` (`src/lib/engineApi.ts:158-176`):

```ts
// BEFORE — current code, engineApi.ts:158-176
export async function engineGenerateDataset(
  projectId: string,
  taskType: EngineTaskType,
  taskDescription: string,
  seedData: Record<string, unknown>[],
  numSamples = 200,
): Promise<EngineSdgResponse> {
  return apiFetch<EngineSdgResponse>("/datasets/generate", {
    method: "POST",
    body: JSON.stringify({
      sdg_mode: "with_seed",
      project_id: projectId,
      task_type: taskType,
      task_description: taskDescription,
      seed_data: seedData,       // ← rejected; this field doesn't exist on SDGRequestWithSeed
      num_samples: numSamples,
    }),
  });
}
```

The backend's `SDGRequestWithSeed` (`api/schemas/sdg.py:104`) requires
`seed_dataset_id: UUID` (the id returned by the seed-upload call that
already runs immediately before this) and has no `seed_data` field at
all. Fix:

```ts
// AFTER
export async function engineGenerateDataset(
  projectId: string,
  taskType: EngineTaskType,
  taskDescription: string,
  seedDatasetId: string,        // ← the dataset_id from engineUploadSeed's response
  numSamples = 200,
  holdoutSize = 100,
): Promise<EngineSdgResponse> {
  return apiFetch<EngineSdgResponse>("/datasets/generate", {
    method: "POST",
    body: JSON.stringify({
      sdg_mode: "with_seed",
      project_id: projectId,
      task_type: taskType,
      task_description: taskDescription,
      seed_dataset_id: seedDatasetId,
      num_samples: numSamples,
      holdout_size: holdoutSize,
    }),
  });
}
```

Caller fix at `NewProject.tsx:102-107` — pass `seedResult.dataset_id`
(already captured at line 98, currently only used for `patchEngineMeta`)
instead of the raw `seedRows` array:

```ts
// AFTER — NewProject.tsx runEngineFlow
const sdgResult = await engineGenerateDataset(
  engineProjectId,
  engineTaskType,
  taskDescription,
  seedResult.dataset_id,   // was: seedRows
);
```

This also means `readSeedRows` (`NewProject.tsx:49-69`) is no longer
needed to build the SDG request body — it can stay only if the raw rows
are still needed for local pre-flight validation (row-count check), or
be removed if that validation moves server-side.

### 2. `base_model="phi-3-mini"` is not in the backend's supported list — training 422s for that one model

`BASE_MODEL_TO_ENGINE` (`src/lib/engineMappings.ts:15-22`):

```ts
"phi-3-mini": "unsloth/Phi-3-mini-4k-instruct-bnb-4bit",
```

Cross-checked against `SUPPORTED_BASE_MODELS`
(`api/routers/tasks_meta.py:101-208`, backing `GET /api/v1/base-models`)
— that exact string is **not** in the 10-entry catalog (which has, among
others, `unsloth/Qwen2.5-0.5B-Instruct-bnb-4bit`,
`unsloth/Qwen3-0.6B-unsloth-bnb-4bit`,
`unsloth/Qwen3-1.7B-unsloth-bnb-4bit`,
`unsloth/tinyllama-chat-bnb-4bit`, and the 5 others that do match the
frontend's list). The training service enforces this as an allowlist:

```python
# api/services/training_service.py:119-128
base_model = request.base_model or settings.default_base_model
if base_model not in _SUPPORTED_MODEL_IDS:
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=(
            f"base_model '{base_model}' is not in the supported list. "
            f"Use GET /api/v1/base-models to see allowed values."
        ),
    )
```

So any user who picks **"Phi-3 Mini"** in `ModelSelectionStep.tsx` gets a
422 at `POST /api/v1/trainings` after already having paid the seed-upload
+ SDG-generation cost. Two fixes, do both:
- Immediate: remove the `phi-3-mini` card from `ModelSelectionStep.tsx`
  (or repoint it at a real supported ID — there is no Phi-3 model in the
  current catalog at all, so removal is the honest fix).
- Structural: replace the hardcoded 6-entry list with
  `GET /api/v1/base-models`, exactly as recommended in the mapping table
  above, so this class of drift can't recur silently.

### 3. Playground chat is disconnected from real trained models and fails silently

Three compounding issues, same root cause (`GET /api/v1/models` never
called — see mapping table):

1. `Playground.tsx:11,15` sources its model list from Supabase
   `trained_models` (`useModels()`), which is never populated by a real
   completed training (`useTrainingSimulator.ts:29-33` only patches
   `localStorage`).
2. Even if it were populated, `ChatPanel.tsx:50-55` sends
   `model: modelName` where `modelName` is
   `models.find(...).name` — an arbitrary Supabase display string — but
   the backend's `ChatCompletionRequest.model` field
   (`api/schemas/inference.py:36`) must be a real **Ollama model tag**
   (e.g. `"slm-platform/cls-1234:latest"`, per the field's own
   description), which only exists on `ModelArtifactResponse
   .ollama_model_tag` / `.base_ollama_tag` (`api/schemas/artifacts.py:16`).
3. Every failure from (1)+(2) is invisible: `ChatPanel.tsx:66-80` catches
   *any* error from `engineChatCompletion` and substitutes one of 3
   hardcoded `mockResponses` (`ChatPanel.tsx:15-19`), so the Playground
   looks fully functional in a demo while never once calling a real
   fine-tuned model.

Fix, in order:
- Wire `GET /api/v1/models` (optionally `GET /api/v1/inference/models`
  to cross-check what Ollama actually has loaded) into `useModels()` or
  a new `useEngineModels()` hook, keyed off `project_id`/
  `training_job_id` from `engineStore.ts`.
- Pass `ollama_model_tag` (fall back to `base_ollama_tag` for a base-model
  A/B comparison) as `model`, not the Supabase display name.
- Remove or clearly label the mock fallback in `ChatPanel.tsx:66-80` —
  at minimum, show a distinct "Engine unreachable, showing a sample
  response" banner instead of a silent swap, so real failures during
  testing/demos are visible (this was already flagged in
  `TASK_TRACKER.md`'s contract-mismatch table).

### 4. `external_project_id` not sent on project create

Backend has supported this since `backend-required-changes.md` item 4
landed (confirmed live in `ProjectCreate`/`ProjectResponse` schemas).
One-line fix at both ends:

```ts
// BEFORE — engineApi.ts:114-123
export async function engineCreateProject(
  name: string,
  description: string,
  task_type: EngineTaskType,
): Promise<EngineProject> {
  return apiFetch<EngineProject>("/projects", {
    method: "POST",
    body: JSON.stringify({ name, description, task_type }),
  });
}

// AFTER
export async function engineCreateProject(
  name: string,
  description: string,
  task_type: EngineTaskType,
  externalProjectId: string,
): Promise<EngineProject> {
  return apiFetch<EngineProject>("/projects", {
    method: "POST",
    body: JSON.stringify({ name, description, task_type, external_project_id: externalProjectId }),
  });
}
```

Call site `NewProject.tsx:193-197` already has `created.id` (the
Supabase project id) in scope at that point — pass it through. This
makes the id mapping durable server-side instead of `localStorage`-only
(`engineStore.ts`), which is lost on browser/profile change today.

### 5. `TrainingMonitor.tsx` renders mock pipeline/log/loss-curve/eval data next to a real WS connection

`TrainingMonitor.tsx` genuinely connects to the real training WebSocket
(`useTrainingWebSocket`, line 30-32) and reads real
`latestProgress.train_loss`/`eval_loss` for the two headline numbers
(lines 36-39) — but then renders `mockPipelineSteps` (line 129),
`mockLossCurve` for the actual chart (lines 14, 35, 146),
`mockTrainingLog` (line 160), and `mockComparisonResults` for the
evaluation tab (line 166), all imported from
`src/data/trainingMockData.ts`. This is the most visually convincing
fake surface in the app because the headline stat cards *are* real,
which makes the mocked chart/log/pipeline next to them look real too.
Fix order: wire `GET /trainings/{id}/loss-history` first (row above,
removes the biggest visual mock), then the pipeline-step list can likely
be derived from WS event history instead of a separate mock array.

### 6. HPO and Evaluation tabs are 100% client-simulated

No code changes shown here (this is a build task, not a one-liner) —
noted as priority because it's the largest scope gap. `ProjectDetail.tsx`
tuning tab and both evaluation surfaces need `POST /trainings`
(`mode=hpo`) and `POST /evaluations` wired end-to-end respectively; both
depend on `GET /api/v1/models` (Priority Fix #3) being wired first since
they need a real `model_artifact_id`.

## Correct End-to-End Sequence

The canonical call order a correct integration follows, backend endpoint
at each step:

1. `POST /api/v1/projects` — `{name, description, task_type, external_project_id}` → `ProjectResponse.id` (Engine project id).
2. `POST /api/v1/datasets/upload-seed` (multipart: `file`, `project_id`, `task_type`, `name`) → `SeedUploadResponse.dataset_id` (seed dataset id).
3. `POST /api/v1/datasets/generate` (`sdg_mode="with_seed"`, `seed_dataset_id` from step 2) → `SDGJobAcceptedResponse.{job_id, dataset_id, websocket_url}` (dataset id here is the placeholder that fills in as SDG runs).
4. Either poll `GET /api/v1/datasets/{dataset_id}` until `status="completed"` (checking `status`, not just `storage_uri`), or subscribe to `websocket_url` from step 3 for live SDG progress.
5. `POST /api/v1/trainings` (`mode="manual"` or `"hpo"`, `project_id`, `dataset_id` from step 3/4, `base_model` — validated against `GET /api/v1/base-models`) → `TrainingJobAcceptedResponse.{training_id, job_id, mlflow_run_id, mlflow_url, websocket_url}`.
6. Subscribe to `websocket_url` (`/ws/jobs/{job_id}`) for `training_progress` → `completed`/`failed` events; on `completed`, the event carries `model_artifact_id`.
7. `GET /api/v1/trainings/{training_id}/loss-history` for the persisted chart series (call once on load, not only via WS, so a page refresh still shows the curve).
8. `GET /api/v1/models/{model_artifact_id}` (or `GET /api/v1/models?training_job_id=...`) → `ModelArtifactResponse` — this is the row containing `ollama_model_tag`, needed for both inference and export.
9. Optional: `POST /api/v1/models/{model_artifact_id}/export` (`format="gguf"|"safetensors"`) → another `job_id`/`websocket_url` on the same `/ws/jobs/{id}` channel.
10. `GET /api/v1/inference/models` to confirm the Ollama tag is actually served, then `POST /api/v1/inference/chat/completions` (`model=ollama_model_tag`) for the Playground.
11. `POST /api/v1/evaluations` (`model_artifact_id`, `dataset_id` — use a holdout child dataset via `DatasetResponse.parent_dataset_id` for a leak-free score) → evaluation run; `POST /api/v1/evaluations/compare` across 2-10 runs for the A/B / tuning-comparison views.

## What's Already Correct — Don't Touch

- **`POST /api/v1/projects` request shape** (minus the missing
  `external_project_id` — see Priority Fix #4; the `name`/`description`/
  `task_type` fields are right).
- **`POST /api/v1/datasets/upload-seed`** — multipart construction,
  field names, and the "no Content-Type header" comment
  (`engineApi.ts:147`) are all correct; browser-set boundary is required
  here.
- **`TASK_TYPE_TO_ENGINE` mapping and its launch-time guard**
  (`engineMappings.ts:5-12`, `NewProject.tsx:161-169`) — correctly maps
  3 of 6 UI task types and blocks launch on the other 3 rather than
  sending an invalid value.
- **5 of the 6 `BASE_MODEL_TO_ENGINE` entries** — everything except
  `phi-3-mini` (Priority Fix #2) resolves to a real, currently-supported
  Ollama/Unsloth base model id.
- **`buildManualConfig`** (`engineMappings.ts:24-38`) — produces a valid
  `ManualTrainingConfig` (LoRA `r`/`alpha`/`dropout`/`target_modules`,
  batch size, grad accumulation) that matches
  `api/schemas/training.py:44`'s field names and defaults exactly.
- **The training WebSocket integration** — URL construction
  (`useTrainingWebSocket.ts:46-52`), event-type discrimination
  (`training_progress`/`completed`/`failed`), and the exponential
  backoff reconnect loop are all correctly built against the real `/ws/
  jobs/{job_id}` contract. `useTrainingSimulator.ts` correctly prefers
  this real WS path over any client-side simulation when a `jobId` is
  present in `engineStore`.
- **`POST /api/v1/inference/chat/completions` request envelope** (minus
  the `model` value itself — see Priority Fix #3) — `messages`,
  `temperature`, `max_tokens`, and forcing `stream: false`
  (`engineApi.ts:246`, matching the backend's non-streaming-only
  contract) are all correct.
- **`apiFetch` error handling** (`engineApi.ts:100-110`) — correctly
  reads response body text on non-2xx before throwing, which is what
  makes the 422s from Priority Fixes #1/#2 debuggable at all once you
  stop catching-and-hiding them client-side.

## See Also

- `docs/01-architecture.md` — hexagonal layout, where GPU work happens.
- `docs/02-api-reference.md` — full endpoint reference in prose.
- `docs/03-realtime-websocket.md` — `/ws/jobs/{id}` message contract in detail.
- `docs/README.md` — doc set index.
- Workspace-level `TASK_TRACKER.md` / `WORKING_LOG.md` (one level up from
  this repo) — cross-repo integration status; this document is a
  detailed drill-down of the "Contract mismatches found in
  `smart-model-tune`" section there.
