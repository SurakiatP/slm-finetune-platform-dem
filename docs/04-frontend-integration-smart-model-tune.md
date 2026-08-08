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

- Full machine-readable contract: [`../openapi.json`](../openapi.json) at the
  repo root — or fetch it live from a running instance via `GET /openapi.json`,
  or browse interactively at `/docs` (Swagger UI) / `/redoc`.
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
`NewProject.tsx:170-179` blocks launch with a toast when the mapping is
`null`. **This guard is correct and should not be changed.** The
remaining gap is cosmetic/UX only: `TaskSelectionStep.tsx:6-49` still
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
| `POST /api/v1/projects` | `engineCreateProject` (`src/lib/engineApi.ts:163-173`), called from `NewProject.tsx:203-208` | ✅ **Resolved** | `external_project_id` is now sent — see Priority Fix #4 below. Request shape (`name`, `description`, `task_type`, `external_project_id`) matches `ProjectCreate` (`api/schemas/projects.py:13`) exactly. |
| `GET /api/v1/projects/{id}` | `engineGetProject` (`engineApi.ts:175-177`) | ❌ | Defined, never called anywhere in `src/`. Nothing currently detects drift between the Supabase `projects` row and the Engine project (e.g. if the Engine project was deleted server-side). Not urgent, but worth a periodic reconciliation check on `ProjectDetail.tsx` now that `external_project_id` round-tripping (row above) is adopted. |
| `PATCH /api/v1/projects/{id}` | — | ❌ | Not called. `smart-model-tune` only ever mutates the Supabase `projects` row (`updateProject` in `src/lib/projectsApi.ts`); the Engine-side project name/description can drift silently. Low priority. |
| `DELETE /api/v1/projects/{id}` | — | ❌ | Not called. Deleting a project in Supabase does not delete the corresponding Engine project/datasets/trainings (cascade only happens on the Engine side if this endpoint is hit). Worth wiring into whatever "delete project" UI action exists so Engine storage doesn't orphan. |
| `POST /api/v1/datasets/upload-seed` | `engineUploadSeed` (`engineApi.ts:188-211`), called `NewProject.tsx:107` | ✅ request / ⚠️ response | The multipart request (`file`, `project_id`, `task_type`, `name`) is correct. The response is under-consumed: `EngineSeedUploadResponse` (`engineApi.ts:20-25`) only types `dataset_id`/`task_type`/`num_samples`/`invalid_rows`, and `NewProject.tsx:108` reads only `.dataset_id`. Backend's `SeedUploadResponse` (`api/schemas/*` seed response, Phase 9) also returns `format_detection` (schema-remap audit trail) and `pdf_uri` (QA+PDF uploads) — neither is typed nor surfaced. `invalid_rows` is typed but never read either. Add the two missing fields to the TS type and surface a toast/banner for dropped rows / remapped columns / PDF confirmation. |
| `POST /api/v1/datasets/generate` (`sdg_mode="with_seed"`) | `engineGenerateDataset` (`engineApi.ts:215-233`), called from `runEngineFlow` (`NewProject.tsx:111-116`) | ✅ **Resolved** | See Priority Fix #1 below — posts `sdg_mode: "with_seed"` with a real `seed_dataset_id`, matching what the backend enforces. |
| `POST /api/v1/datasets/generate` (`sdg_mode="description_only"`) | — | ❌ | Not wired at all — no UI path generates from a task description alone (no seed upload), even though `SDGRequestDescriptionOnly` (`api/schemas/sdg.py`) supports it with `classification_config`/`tool_calling_config` schemas (label lists / tool definitions). This is `backend-required-changes.md` item 3, still open on the frontend side. Would let users skip the seed-file step entirely for classification/tool_calling. |
| `GET /api/v1/datasets/{id}` | `engineGetDataset` (`engineApi.ts:237-239`), polled by `pollDatasetReady` (`NewProject.tsx:76-86`) | ⚠️ | Polling logic checks `storage_uri && num_samples > 0` to decide "ready" (`NewProject.tsx:79`), and does short-circuit on `status === "failed" \| "cancelled"` (`:80-82`) — this part is *not* broken, contrary to what an earlier pass of this doc claimed. The remaining gap: `EngineDataset` (`engineApi.ts:121-128`) already declares `status`/`error_message`, but the poll's failure path throws `ds.error_message \|\| \`Dataset generation ${ds.status}.\`` rather than distinguishing "failed" (show the real reason now) from "cancelled" (a normal user action, not an error) — both currently surface through the same generic-error toast path in `NewProject.tsx:237-244`. |
| `DELETE /api/v1/datasets/{id}` | — | ❌ | Not called; no dataset-delete UI action found. |
| `GET /api/v1/datasets/{id}/download`, `GET /api/v1/datasets/{id}/preview` | — | ❌ | Not called; no dataset preview/download UI in `smart-model-tune`. |
| `GET /api/v1/datasets` | — | ❌ | Not called; dataset listing (if any) is Supabase-only (`src/lib/datasetsApi.ts`), disconnected from real Engine dataset rows. |
| `POST /api/v1/trainings` (`mode="manual"`) | `engineStartTraining` (`engineApi.ts:257-275`), called from `runEngineFlow` (`NewProject.tsx:125-131`) | ✅ shape / ⚠️ base_model | Request shape (`project_id`, `dataset_id`, `base_model`, `training_name`, `manual_config`) matches `ManualTrainingRequest` (`api/schemas/training.py:222`). But see Priority Fix #2 — one of the 6 `BASE_MODEL_TO_ENGINE` entries maps to a model ID the backend rejects. |
| `POST /api/v1/trainings` (`mode="hpo"`) | Auto-Tuning tab, `ProjectDetail.tsx:298-321` (`TuningReport`/`TuningHistory`/`getLatestTuningRun`) | ❌ | Zero backend calls — confirmed no reference to `hpo`/`HPOConfig`/`hpo_config` anywhere in `src/`. The entire tuning report and tuning history are generated client-side (`src/lib/tuningGenerator.ts`). `HPOTrainingRequest` (`api/schemas/training.py:263`, needs `hpo_config`: `n_trials`, `search_space`, `sampler`, `pruner`, `objective_metric`) is a real, working endpoint. This demos as if HPO ran; it didn't. |
| `GET /api/v1/trainings/{id}` | `engineGetTraining` (`engineApi.ts:277-279`) | ❌ | Defined, never called. There's no fallback to the canonical training resource when the WebSocket gives up (`useTrainingWebSocket.ts:86`, `MAX_ATTEMPTS = 8`) — if reconnection is exhausted, the UI has no way to learn the training's final status/`error_message`. Poll this endpoint once the WS hook's `connected` flips permanently false without a `completed`/`failed` event. (`useTrainingSimulator.ts:173-233` already polls `engineGetTraining` as a WS fallback for the *status* transition — this row is about the terminal-state edge case specifically, not a from-scratch gap.) |
| `DELETE /api/v1/trainings/{id}` | `engineCancelTraining` (`engineApi.ts:285-287`) | ❌ | Defined, never called. No "cancel training" UI action exists anywhere in `smart-model-tune`. |
| `GET /api/v1/trainings/{id}/loss-history` | `engineGetTrainingLossHistory` (`engineApi.ts:281-283`), called from `TrainingMonitor.tsx:63` | ✅ **Resolved** | See Priority Fix #5 below — this is now the real loss chart, fetched on mount and on a 10s poll while training runs. |
| `GET /api/v1/trainings/{id}/metrics` | — | ❌ | Not called. Full metric history + HPO child-trial summary; only relevant once the HPO row above is wired. |
| `GET /api/v1/trainings/{id}/mlflow-url` | — | ❌ | Not called directly, but `mlflow_run_id`/`mlflow_url` already arrive inline on `EngineTrainingAccepted` (`engineApi.ts:34-41`) from the `POST /trainings` response and are stored nowhere, rendered nowhere. Add an "Open in MLflow" link using the value already in hand — no extra call needed for the common case. |
| `WS /ws/jobs/{job_id}` (training progress) | `useTrainingWebSocket` (`src/hooks/useTrainingWebSocket.ts`), consumed by `useTrainingSimulator.ts` and `TrainingMonitor.tsx:47` | ✅ URL/events / ⚠️ auth | URL construction (`buildWsUrl`, `useTrainingWebSocket.ts:58-64`) matches `SDGJobAcceptedResponse`/`TrainingJobAcceptedResponse.websocket_url`'s `/ws/jobs/{job_id}` path, the `training_progress`/`completed`/`failed` event shapes match the backend WS contract, and the exponential-backoff reconnect (`MAX_ATTEMPTS=8`, capped at 30s) is a reasonable client. **Do not change any of that.** What does need to change is the missing `Authorization` subprotocol — see "Required Frontend Change" §2 below; that section (not this row) is now the authoritative version. See `docs/03-realtime-websocket.md` for the full message contract this depends on. |
| `WS /ws/jobs/{job_id}` (SDG progress) | — | ❌ | `SDGJobAcceptedResponse.websocket_url` (`engineApi.ts:27-32`, `EngineSdgResponse.websocket_url`) is returned and typed but never connected to — `NewProject.tsx`'s `runEngineFlow` only polls `GET /datasets/{id}` (see row above) instead of subscribing to the SDG job's own WS channel for live progress. Same hook (`useTrainingWebSocket`, generically named enough to reuse) could drive an SDG progress indicator during the "generating dataset" step instead of a blind 5s poll loop. |
| `GET /api/v1/models` | `engineGetModelArtifacts` / `engineListModelArtifacts` / `engineGetModelArtifact` (`engineApi.ts:297-310`) | ❌ **not wired — high impact, still true** | Defined, **never called anywhere**. `Models.tsx:9,13` and `ModelDetail.tsx:10,78` still read exclusively from the Supabase `trained_models` table via `src/lib/modelsApi.ts`, and nothing in the app populates that table from a real completed training. `useTrainingSimulator.ts` (the `syncTrainingStatus` effect, `:194-210`, and the WS-`completed` effect, `:272-284`) writes `modelArtifactId` into `localStorage` (`engineStore.ts`) — real progress, confirmed working — but never into Supabase. Net effect, unchanged from the previous pass of this doc: the Models list and Model Detail pages show **nothing for real Engine-trained models** unless someone manually seeds the Supabase table. (The Playground's own model picker is a separate story now — see Priority Fix #7 below; it stopped depending on this endpoint entirely.) |
| `GET /api/v1/models/{id}` | — | ❌ | Not called; same root cause as above. |
| `POST /api/v1/models/{id}/export` | `engineExportModel` (`engineApi.ts:312-317`) is called automatically by `useTrainingSimulator.ts` right after training completes (`:194-203`, `:236-251`) — but the UI surface for it, `ModelDetail.tsx` "Export" tab, `exportFormats` array (`ModelDetail.tsx:13-17`), is separate and still fake | ⚠️ **half-wired, and the visible half is factually wrong** | The export call itself is real and already fires automatically (see left column) — this row was previously marked "not called" for the whole endpoint, which was wrong; it undersold what `useTrainingSimulator.ts` does. What's still true: `ModelDetail.tsx`'s manual "Export" tab is a static list with hardcoded fake sizes and non-functional `Download` buttons (no `onClick`, `ModelDetail.tsx:220-222`), and it lists **`ONNX`** as an export option (`ModelDetail.tsx:16`), which the backend does not support — `ArtifactFormat` (`api/schemas/enums.py:53`) is `lora \| gguf \| safetensors` only. Remove the ONNX row (actively misleading, not just unwired), and wire the GGUF/SafeTensors rows to the same `engineExportModel` the auto-export already uses, for a manual re-export path (e.g. a different quantization). |
| `GET /api/v1/models/{id}/download` | — | ❌ | Not called; see above — the Download buttons have no handler at all. |
| `GET /api/v1/inference/models` | `engineListInferenceModels` (`engineApi.ts:321-325`), called from `Playground.tsx:24` | ✅ **Resolved** | See Priority Fix #7 below — this is now the real source of the model picker. |
| `POST /api/v1/inference/chat/completions` | `engineChatCompletion` (`engineApi.ts:327-338`), called from `ChatPanel.tsx:52-57` | ✅ model / ⚠️ dev-mode mock | See Priority Fix #7 — `model` is now a real Ollama tag from the listing above, not a Supabase display name. Priority Fix #3 covers the remaining item: production failures surface a real error, but the `mockResponses` fallback (`ChatPanel.tsx:70-84`) still runs when `import.meta.env.DEV`, by design — worth knowing when testing locally. |
| `POST /api/v1/inference/completions` | — | ❌ | Legacy text-completion endpoint; not used, no gap (chat completions is the correct one for this UI). |
| `POST /api/v1/evaluations` | `ProjectDetail.tsx` "evaluation" tab (deterministic seeded fake metrics, `ProjectDetail.tsx:76-86`) and `TrainingMonitor.tsx` "evaluation" tab (`TrainingMonitor.tsx:259-265`) | ❌ | Zero backend calls — confirmed no reference to `/evaluations` anywhere in `src/`. Both eval surfaces are entirely client-fabricated (`Math.random`/seeded-hash metrics on the `ProjectDetail` side; a static "not started" placeholder on the `TrainingMonitor` side, no longer `mockComparisonResults` — that mock was removed along with the rest of the Priority Fix #5 cleanup), not backend results. `EvaluationCreate` (`api/schemas/evaluations.py:14`, needs `model_artifact_id` + `dataset_id`, optional `use_llm_judge`/`judge_model`) is a real, working endpoint but depends on `GET /api/v1/models` being wired first (needs a real `model_artifact_id`). |
| `GET /api/v1/evaluations`, `GET /api/v1/evaluations/{id}` | — | ❌ | Not called. |
| `POST /api/v1/evaluations/compare` | — | ❌ | Not called. `EvaluationCompareRequest` (`api/schemas/evaluations.py:58`, 2-10 `evaluation_ids`) would be the correct backend call for the A/B compare feature already present in `Playground.tsx` (`abMode`) — currently that toggle only runs two independent chat panels side by side, it does not compare structured eval metrics. |
| `GET /api/v1/tasks`, `GET /api/v1/tasks/{task_type}/example` | — | ❌ | Not called. `TaskSelectionStep.tsx:6-49` hardcodes all 6 task types (3 unsupported) with fabricated example strings instead of asking the backend which 3 are real. Low priority but removes a staleness source. |
| `GET /api/v1/base-models` | — | ❌ **and factually wrong** | Not called. `ModelSelectionStep.tsx` hardcodes 6 base models; see Priority Fix #2 below — one of the 6 (`phi-3-mini`) doesn't exist in the backend's supported list at all. Switching to `GET /api/v1/base-models` (`BaseModelInfo[]`, backed by `SUPPORTED_BASE_MODELS` in `api/routers/tasks_meta.py:101-208`) removes this whole class of drift permanently — the backend added this exact live-config pattern for `GET /api/v1/sdg-pipeline` after the same staleness bug recurred 3 times on the embedded `frontend/` (see the backend repo's own `WORKING_LOG.md`, 2026-07-29 entry). |
| `GET /api/v1/sdg-pipeline` | — | ❌ | Not called; `smart-model-tune` doesn't display which LLM the SDG pipeline uses (generator/judge/diversity-rules). Cosmetic only — nice-to-have, not required. |
| `GET /health` | `engineHealthCheck` (`engineApi.ts:342-349`) | ❌ | Defined, never called anywhere in `src/`. No "Engine unreachable" banner exists — the only failure signal a user gets today is `ChatPanel`'s real error message in production (Priority Fix #3) or a generic launch-toast error in `NewProject.tsx`. |

35 REST endpoints + 1 WebSocket channel from [`../openapi.json`](../openapi.json) are covered above.

## ⚠️ Required Frontend Change — send the Supabase token (branch `feat/be-auth001`)

**This is the first backend branch that is not zero-frontend-change.** Everything
before it was designed so `smart-model-tune` needed no edits. Authentication
cannot be, because the credential has to come from the client.

It ships behind `AUTH_REQUIRED`, defaulting to **`false`**, precisely so the two
sides can land independently: deploying the backend changes nothing until the
flag is flipped, and the flag should only be flipped once the two changes below
are live. Full rationale in [ADR-009](./adr/ADR-009-supabase-jwt-auth.md).

### 1. `apiFetch` — attach the header

`src/lib/engineApi.ts:149-158` currently sends only `Content-Type` and
`ngrok-skip-browser-warning`. The token is already in the app — `AuthContext.tsx:52`
holds the session from `supabase.auth.getSession()`.

**`engineApi.ts` does not import `supabase` today** — check with
`grep -n supabase src/lib/engineApi.ts` before starting; it currently matches
nothing. The patch below is two changes, not one: add the import, then attach
the header.

```ts
// engineApi.ts — new import at the top of the file
import { supabase } from "@/integrations/supabase/client";

// engineApi.ts — inside apiFetch, before the fetch
const { data: { session } } = await supabase.auth.getSession();
const authHeader = session?.access_token
  ? { Authorization: `Bearer ${session.access_token}` }
  : {};

const res = await fetch(`${ENGINE_HOST}/api/v1${path}`, {
  ...init,
  headers: { "Content-Type": "application/json", ...NGROK_HEADER, ...authHeader, ...init?.headers },
});
```

Do the same for the three calls that bypass `apiFetch` and build their own
`fetch` — the seed upload (`engineApi.ts:200`) and the two inference calls
(`engineApi.ts:322`, `:328`).

**Send no header rather than a stale one.** An expired token is a `401`; an absent
one is served anonymously while the flag is off. Supabase refreshes tokens
automatically (`autoRefreshToken: true` in `integrations/supabase/client.ts`), so
reading the session per request — not once at module load — is what keeps it fresh.

### 2. `useTrainingWebSocket` — pass the token as a subprotocol

Browsers do not allow headers on `new WebSocket()`. `useTrainingWebSocket.ts:92`
(not `:63` — an earlier pass of this doc cited the pre-refactor line; the file has
since grown by ~29 lines) opens the socket with a URL only:

```ts
const ws = new WebSocket(buildWsUrl(jobId));
```

**The obvious patch — `new WebSocket(buildWsUrl(jobId), ["bearer", accessToken])`
— does not compile.** `useTrainingWebSocket(jobId)` takes one argument
(`useTrainingWebSocket.ts:66`); there is no `accessToken` in scope inside
`connect()`. Two ways to get one there, and the second is the one to use:

- *Thread it through as a second hook parameter.* Works, but every call site —
  `TrainingMonitor.tsx:47`, and the three separate `useTrainingWebSocket` calls
  inside `useTrainingSimulator.ts:125-131` — has to be updated to source and
  pass a token, and keep passing a *fresh* one across re-renders.
- **Read `supabase.auth.getSession()` inside `connect()` itself, the same way
  patch #1 reads it inside `apiFetch`.** No signature change, and — this is
  the part that matters — a fresh read on every reconnect attempt. The backoff
  loop below can run for minutes before giving up (`1000 * 2^attempts`, capped
  at 30s, across up to `MAX_ATTEMPTS = 8` tries); a token captured once when the
  hook first mounted could easily be stale by the last attempt. `connect()`
  already runs inside a `useEffect`, so making it `async` is a small change:

```ts
// useTrainingWebSocket.ts — connect() becomes async; everything else in the
// effect (destroyed/attempts/reconnectTimer, the effect's own cleanup) is unchanged
async function connect() {
  if (destroyed || attempts >= MAX_ATTEMPTS) return;
  attempts++;

  const { data: { session } } = await supabase.auth.getSession();
  if (destroyed) return; // unmounted while the await was in flight

  const token = session?.access_token;
  const ws = token
    ? new WebSocket(buildWsUrl(jobId), ["bearer", token])
    : new WebSocket(buildWsUrl(jobId));
  wsRef.current = ws;
  // ...rest of connect() (onopen/onmessage/onerror/onclose) unchanged
}
```

The server selects and echoes `bearer` — nothing else in the hook needs to change
for that.

**On close-code handling: don't build the 4401-vs-4403 branch.** An earlier pass
of this doc recommended treating them distinctly (bad credential → refresh and
retry; wrong job → stop reconnecting). That branch cannot fire. `ws.close()`
called on a socket that was never `accept()`-ed — which is what both 4401 and
4403 are — is not a WebSocket close frame at all; it is an HTTP 403 handshake
failure, and every browser reports a rejected handshake to `onclose` as code
`1006` with no reason string, indistinguishable from a network failure. This is
a backend-side gap (`api/routers/websocket.py`'s own docstring and
[ADR-009](./adr/ADR-009-supabase-jwt-auth.md) currently describe the same
unreachable branch — tracked separately in the workspace `TASK_TRACKER.md`, not
something to work around from this side). Until that lands, the existing
backoff loop's behavior on any handshake rejection — retry up to `MAX_ATTEMPTS`,
then give up silently — is the correct client behavior, because there is no
signal available yet to do anything smarter with.

### 3. `VITE_ENGINE_HOST` — leave it empty

The agreed topology is a single nginx serving the frontend and proxying
`/api/v1` and `/ws` to the Engine. `useTrainingWebSocket.ts:60` already documents
that an empty `VITE_ENGINE_HOST` falls back to same-origin, which is the intended
production setting. Same-origin also removes CORS from the picture entirely.

### What breaks if only one side ships

| | Backend flag `false` (today) | Backend flag `true` |
|---|---|---|
| Frontend without the token | works, anonymous, **no per-user isolation** | every call `401` — total outage |
| Frontend with the token | works, scoped per user | works, scoped per user |

So: ship the frontend changes first, confirm tokens are arriving, then flip.
Reversing that order takes the product down.

### Also note

- `owner_id` appears on project responses. Never send it — it is set from your
  token and rejected as input.
- Another user's resource returns **`404`, not `403`**, with a message identical
  to a genuine not-found. Don't build UI that distinguishes them; it can't.
- Anything created before the cutover has `owner_id = null` and becomes
  **invisible to everyone** once a token is sent. Existing demo projects need an
  owner assigned or they vanish.

## Priority Fix List

**Re-verified against `smart-model-tune`'s current source on 2026-08-06.**
Six of the seven items below have since been fixed frontend-side — two more
than the previous pass found, because `Playground.tsx`/`ChatPanel.tsx` had
moved on to a real fix (item 7) that this doc hadn't caught up to; the table
above still marked the endpoint "not wired" while the code already called it.
Kept below, struck through with the evidence, rather than deleted — a handoff
doc that silently drops items invites them being re-reported.

| # | Item | Status |
|---|------|--------|
| 1 | SDG sends inline `seed_data` instead of `seed_dataset_id` | ✅ **Resolved** — `engineApi.ts:229` sends `seed_dataset_id` |
| 2 | `base_model="phi-3-mini"` rejected by the backend | 🟡 **Partly resolved** — blocked before launch, but still reachable via a template |
| 3 | Playground chat fails silently into canned replies | 🟡 **Resolved for production** — mock is now dev-only |
| 4 | `external_project_id` not sent on project create | ✅ **Resolved** — `engineApi.ts:171`, lookup at `:180-183` |
| 5 | `TrainingMonitor.tsx` renders mock pipeline/log/loss-curve | ✅ **Resolved** — real WS + MLflow loss history |
| 6 | HPO and Evaluation tabs are 100% client-simulated | 🔴 **Still open** — no backend call exists anywhere in `src/` |
| 7 | Playground sources its model list from Supabase `trained_models`, not real Ollama tags | ✅ **Resolved** — `Playground.tsx` now calls `engineListInferenceModels` directly |

### ~~1. SDG generate sends inline `seed_data` instead of `seed_dataset_id`~~ — RESOLVED

Fixed frontend-side in the 2026-07-30 pass. `engineGenerateDataset`
(`src/lib/engineApi.ts:215-233`) now posts `sdg_mode: "with_seed"` with a
`seed_dataset_id` obtained from `POST /datasets/upload-seed`
(`engineApi.ts:200`), which is exactly the contract the backend enforces.

Nothing to do. Do not re-report.

### 2. `base_model="phi-3-mini"` — blocked before launch, but a template still selects it

Partly fixed. `BASE_MODEL_TO_ENGINE` (`src/lib/engineMappings.ts:19-20`) maps
`"phi-3-mini" → null` with the comment *"Phi-3 is retained in historic/demo
data, but the engine does not allow it"*, and the model picker no longer
offers it — so it can no longer reach the backend and 422.

**What remains**: `phi-3-mini` is still a member of the `BaseModel` union
(`src/types/index.ts:3`) and is still the `baseModel` of a template in
`src/components/new-project/TemplateLibrary.tsx:40`. A user who picks that
template gets a project whose model maps to `null` and is blocked at launch —
no longer a confusing 422, but still a dead end reached through a
supported-looking path.

Fix: point that template at a model the engine accepts. `GET /api/v1/base-models`
is the authoritative list (ADR-002: ≤3B params, must fit QLoRA 4-bit).

### 3. Playground chat — real errors in production, mock retained for dev

Fixed for the case that mattered. `ChatPanel.tsx:68-70` now sets a real error
(*"The inference request failed. Check that the model is exported and the
engine is online."*) and returns early unless `import.meta.env.DEV`, so a
production user sees the failure instead of a canned reply.

**What remains** is a deliberate dev affordance, not a bug: the
`mockResponses` fallback below that guard still runs in dev builds. Worth
knowing when testing locally — a green-looking chat in `npm run dev` proves
nothing about the engine.

### ~~4. `external_project_id` not sent on project create~~ — RESOLVED

Fixed frontend-side. `engineCreateProject` sends `external_project_id`
(`src/lib/engineApi.ts:171`) and `engineFindProjectByExternalId` reads it back
(`:181`), which is what `useTrainingSimulator.ts:60-68` now uses to recover a
project's Engine ids after a reload instead of trusting `localStorage` alone.

### ~~5. `TrainingMonitor.tsx` renders mock pipeline/log/loss-curve/eval data~~ — RESOLVED

Fixed frontend-side. `TrainingMonitor.tsx` now takes progress from
`useTrainingWebSocket` (`:47`) and the loss chart from
`engineGetTrainingLossHistory` (`:63`), i.e. real MLflow data via
`GET /api/v1/trainings/{id}/loss-history`. `grep -rn "mockPipelineSteps|mockTrainingLog|mockLossCurve" src`
now matches **only** the definitions in `src/data/trainingMockData.ts` — no
consumers remain. Those exports are dead code and can be deleted.

### 6. HPO and Evaluation tabs are 100% client-simulated — STILL OPEN

Unchanged, and now the largest remaining gap. `src/lib/tuningGenerator.ts` and
`src/lib/qualityCalculator.ts` contain **no `fetch` and no engine import** —
their output is computed in the browser. Nothing anywhere in `src/` calls
`/api/v1/evaluations` or submits a training with `mode="hpo"`.

The backend implements both today. Wiring needed:
- Tuning tab → `POST /api/v1/trainings` with `mode="hpo"`, then the
  `hpo_progress` WS frames and `GET /api/v1/trainings/{id}/metrics`.
- Evaluation surfaces → `POST /api/v1/evaluations`, then
  `GET /api/v1/evaluations/{id}`. As of this branch these also emit
  `evaluation_progress` WS frames (see below), so a real progress bar is
  available rather than a spinner.

Both need a real `model_artifact_id` from `GET /api/v1/models`.

### ~~7. Playground sourced its model list from Supabase, not real Ollama tags~~ — RESOLVED

Fixed frontend-side since the previous pass of this doc, which had not caught
up to it — the endpoint table above still said `GET /api/v1/inference/models`
was "defined, never called" until this revision. `Playground.tsx:12,24`
now imports and calls `engineListInferenceModels` directly on mount, and the
model picker (`Playground.tsx:69-99`) renders `EngineInferenceModel.id` —
`grep -n "useModels\|trained_models\|modelsApi" src/pages/Playground.tsx
src/components/playground/ChatPanel.tsx` matches nothing. `getModelName`
(`Playground.tsx:42`) now passes that same `id` straight through to
`ChatPanel`, which sends it as `model` on `POST /inference/chat/completions`
(`ChatPanel.tsx:52-57`) — a real Ollama/`slm/<8hex>` tag, not a Supabase
display name.

Nothing to do. Do not re-report. (Item 6 above is now the last unwired
high-value surface — HPO and Evaluation are both still 100% client-simulated.)

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
10. `GET /api/v1/inference/models` to confirm the Ollama tag is actually served, then `POST /api/v1/inference/chat/completions` (`model=ollama_model_tag`) for the Playground. The listing is owner-scoped — `slm/…` entries are only the caller's own fine-tunes, base models stay visible to everyone — and its `id` is the canonical `slm/<8hex>` form, i.e. exactly what `model` accepts, so a dropdown can pass the listed value straight through. (Anonymous phase-1 callers get the unfiltered list.)
11. `POST /api/v1/evaluations` (`model_artifact_id`, `dataset_id` — use a holdout child dataset via `DatasetResponse.parent_dataset_id` for a leak-free score) → evaluation run; `POST /api/v1/evaluations/compare` across 2-10 runs for the A/B / tuning-comparison views.

## What's Already Correct — Don't Touch

- **`POST /api/v1/projects` request shape** (minus the missing
  `external_project_id` — see Priority Fix #4; the `name`/`description`/
  `task_type` fields are right).
- **`POST /api/v1/datasets/upload-seed`** — multipart construction,
  field names, and the "no Content-Type header" comment
  (`engineApi.ts:204`) are all correct; browser-set boundary is required
  here.
- **`TASK_TYPE_TO_ENGINE` mapping and its launch-time guard**
  (`engineMappings.ts:5-12`, `NewProject.tsx:170-179`) — correctly maps
  3 of 6 UI task types and blocks launch on the other 3 rather than
  sending an invalid value.
- **5 of the 6 `BASE_MODEL_TO_ENGINE` entries** — everything except
  `phi-3-mini` (Priority Fix #2) resolves to a real, currently-supported
  Ollama/Unsloth base model id.
- **`buildManualConfig`** (`engineMappings.ts:25-39`) — produces a valid
  `ManualTrainingConfig` (LoRA `r`/`alpha`/`dropout`/`target_modules`,
  batch size, grad accumulation) that matches
  `api/schemas/training.py:44`'s field names and defaults exactly.
- **The training WebSocket integration** — URL construction
  (`useTrainingWebSocket.ts:58-64`), event-type discrimination
  (`training_progress`/`completed`/`failed`), and the exponential
  backoff reconnect loop are all correctly built against the real `/ws/
  jobs/{job_id}` contract. `useTrainingSimulator.ts` correctly prefers
  this real WS path over any client-side simulation when a `jobId` is
  present in `engineStore`. (The one open item is auth — see "Required
  Frontend Change" §2 above, not a defect in this integration itself.)
- **`POST /api/v1/inference/chat/completions` request envelope** — since
  Priority Fix #7, this now includes the `model` value itself too;
  `messages`, `temperature`, `max_tokens`, and forcing `stream: false`
  (`engineApi.ts:331`, matching the backend's non-streaming-only
  contract) are all correct.
- **`apiFetch` error handling** (`engineApi.ts:149-159`) — correctly
  reads response body text on non-2xx before throwing, which is what
  makes 422s debuggable at all once you stop catching-and-hiding them
  client-side.

## New Backend Capabilities (branch `feat/be-fe-gap001`)

### What you get with no frontend changes at all

`GET /ws/jobs/{job_id}` now sends the **last published progress frame
immediately on connect**, before any live frame (Redis `job:{id}:last`, 24h
TTL — see [ADR-008](./adr/ADR-008-ws-progress-snapshot.md)).

`useTrainingWebSocket.ts` stores only `latestProgress` / `latestSdgProgress`
(singular, not arrays), so it renders that first frame the moment it arrives.
The practical effect: hard-refreshing mid-SDG now paints `80/200` instantly
instead of showing nothing until the next publish — which, mid-loop, can be
tens of seconds away. **No client change is required for this.**

The 10-second REST poll added in the 2026-07-30 pass is still worth keeping as
a fallback: the snapshot is a Redis-backed UX accelerator, not durable state,
and it 404s once the 24h TTL lapses.

### What needs a small frontend change

- **`EngineDataset` (`src/lib/engineApi.ts:121-128`) doesn't declare
  `celery_task_id`.** `DatasetResponse` now exposes it as a first-class field
  (it previously existed only inside `generation_metadata`). Without it, an
  in-flight **SDG** job can't be reconnected to after a reload from a fresh
  browser — training already recovers this way via `training.celery_task_id`
  (`useTrainingSimulator.ts:91`). Adding the field to the interface and
  reusing the same recovery path closes the gap.
- **`GET /api/v1/jobs/{job_id}/progress`** is a REST snapshot of the same
  frame, for surfaces that don't hold a socket open. `404` means "no frame
  yet" — fall back to the resource's own `status`, don't treat it as an error.
- **New `WSMessageType` values**: `export_progress`
  (`stage: downloading|merging|converting|quantizing|uploading|registering`,
  plus `detail`) and `evaluation_progress`
  (`phase: predicting|scoring|judging`, `rows_done`, `rows_total`). Export and
  evaluation used to be silent for their whole run. Existing `switch (type)`
  handling ignores unknown values safely, so nothing breaks by not adopting
  them — but a real export progress bar is now possible.
- **Cancel endpoints**: `POST` on `/datasets/{id}/cancel`,
  `/models/{id}/export/cancel`, `/evaluations/{id}/cancel`, and
  `/trainings/{id}/cancel`. All are idempotent (cancelling an
  already-finished job returns `200` with its existing status) and `404` on a
  missing row. Export-cancel returns `409` when no export was ever requested.
  **`DELETE /api/v1/trainings/{id}` is unchanged** — the call at
  `engineApi.ts:286` keeps working; the `POST` alias exists only for
  consistency. Cancelling SDG is the one worth wiring first: it is the job
  that actually spends OpenRouter credit.
- **`ModelArtifactResponse`** gains `export_status` and
  `export_celery_task_id`. `null` `export_status` means no export was ever
  requested. `gguf_uri` / `export_error_message` are unchanged and remain the
  completion signal you read today.

## Known Gaps — Frontend Screens With No Backend Counterpart

Recorded because they are invisible in the API surface: these screens look
finished and are wired to nothing on the Engine side. Building them is **out
of scope** for the current backend branch and needs a product decision.

- **Deployment.** `src/lib/deploymentsApi.ts` reads `deployed_endpoints`
  straight from Supabase. `requestsPerMin`, `avgLatencyMs`, `errorRate`,
  `uptime`, `rateLimitPerMin` and `burstLimit` are columns that **nothing
  writes** — no backend produces those numbers. The Engine has exactly one
  shared inference route, `POST /api/v1/inference/chat/completions`, proxied
  to Ollama: no per-model endpoint, no rate limiting, no usage metering.
- **API Keys.** `src/lib/apiKeysApi.ts:43-44` mints a key prefix/suffix with
  `Math.random()` and stores it in Supabase. The backend never sees these
  keys and does not authenticate anything — the platform has **no
  authentication at all**, deliberately, see
  [ADR-006](./adr/ADR-006-defer-authentication.md). Per-key auth and per-key
  quotas both block on that decision.
- **Analytics.** `api_call_events` has no producer for the same reason —
  nothing counts Engine calls.

If this product needs real deployment/metering, it is a backend workstream
that has to be scheduled after ADR-006, not a wiring task.

## See Also

- `docs/01-architecture.md` — hexagonal layout, where GPU work happens.
- `docs/02-api-reference.md` — full endpoint reference in prose.
- `docs/03-realtime-websocket.md` — `/ws/jobs/{id}` message contract in detail.
- `docs/README.md` — doc set index.
- Workspace-level `TASK_TRACKER.md` / `WORKING_LOG.md` (one level up from
  this repo) — cross-repo integration status; this document is a
  detailed drill-down of the "Contract mismatches found in
  `smart-model-tune`" section there.
