# SLM Platform — Frontend API Integration Guide

> เอกสารนี้เป็น **single source of truth** สำหรับ frontend dev ที่จะเชื่อมต่อกับ backend ของ SLM Fine-Tuning Platform.
> สำหรับ contract ที่เป็นทางการ ใช้ **OpenAPI spec** ที่ `http://<api-host>:8000/openapi.json` (Swagger UI ที่ `/docs`).

**สารบัญ**
1. [Overview](#1-overview)
2. [Common Patterns](#2-common-patterns)
3. [Health & Metadata](#3-health--metadata)
4. [Projects](#4-projects)
5. [Datasets](#5-datasets)
6. [Trainings](#6-trainings)
7. [Models / Artifacts](#7-models--artifacts)
8. [Evaluations](#8-evaluations)
9. [Inference (proxy to Ollama)](#9-inference-proxy-to-ollama)
10. [WebSocket Events](#10-websocket-events)
11. [Frontend Integration Patterns](#11-frontend-integration-patterns)
12. [HTTP Status Reference](#12-http-status-reference)

---

## 1. Overview

| Item | Value |
|------|-------|
| Base URL (dev local) | `http://localhost:8000` (ผ่าน SSH port-forward `-L 8000:localhost:8000`) |
| API prefix | `/api/v1` |
| WebSocket prefix | `/ws` (ไม่มี `/api/v1`) |
| Authentication | **ไม่มี** — ทุก endpoint open (PoC) |
| Content-Type | `application/json` (ยกเว้น upload — `multipart/form-data`) |
| OpenAPI spec | `GET /openapi.json` |
| Swagger UI | `GET /docs` |
| ReDoc | `GET /redoc` |
| Datetime format | ISO 8601 UTC (`"2026-05-10T07:35:27.522045Z"`) |
| ID format | UUID v4 string (`"b2b97392-0314-43ea-a2d7-2e56b299d469"`) |

### Source-of-truth

ถ้าไฟล์นี้ขัดกับ OpenAPI spec → **OpenAPI ชนะ** (ไฟล์นี้อาจ stale). ใช้ Swagger UI ตรวจ payload จริงทุกครั้งที่ implement endpoint ใหม่

---

## 2. Common Patterns

### 2.1 Error envelope (ทุก non-2xx response)

```json
{
  "detail": "human-readable message",
  "code": "not_found",          // string code ใช้แยก case ใน FE
  "extra": null                 // null หรือ object เช่น {errors: [...]} สำหรับ validation
}
```

**Example: validation error (422)**
```json
{
  "detail": "body.with_seed.seed_dataset_id: Field required",
  "code": "validation_error",
  "extra": {
    "errors": [
      {
        "type": "missing",
        "loc": ["body", "with_seed", "seed_dataset_id"],
        "msg": "Field required",
        "input": { /* request body */ }
      }
    ]
  }
}
```

**Common `code` values:** `not_found`, `bad_request`, `conflict`, `validation_error`

### 2.2 Pagination envelope (`Page<T>`)

List endpoints (เช่น `GET /projects`, `/datasets`, `/trainings`, `/models`) คืน:

```json
{
  "items": [ /* T[] */ ],
  "total": 9,        // total rows ที่ match filter (สำหรับ pagination calculation)
  "limit": 10,       // limit ที่ส่งไป (default 50, max 200)
  "offset": 0
}
```

**Query params:** `limit` (1-200) + `offset` (≥ 0). บาง endpoint มี filters เพิ่ม เช่น `project_id`, `status`, `training_job_id`

> ⚠️ **Metadata endpoints ไม่ใช้ envelope** — `GET /tasks` กับ `GET /base-models` คืน **plain JSON list** ตรงๆ (ดู section 3)

### 2.3 Async job pattern

ทุก endpoint ที่ trigger long-running task (SDG / training / export / evaluation) จะคืน `202 Accepted` ทันทีพร้อม shape:

```json
{
  "<id_field>": "<resource_uuid>",   // dataset_id / training_id / artifact_id / evaluation_id
  "job_id": "<celery_task_uuid>",    // ใช้ subscribe WebSocket
  "status": "pending",
  "websocket_url": "/ws/jobs/<celery_task_uuid>"
}
```

**FE pattern:**
1. `POST /<endpoint>` → ได้ `job_id` + `websocket_url`
2. **Subscribe WebSocket** ที่ `ws://<host>${websocket_url}` (prepend protocol+host)
3. รับ progress events ผ่าน WS — ดู [section 10](#10-websocket-events)
4. เมื่อได้ `{"type":"completed"}` หรือ `{"type":"failed"}` → close WS เอง + `GET /<resource>/{id}` เพื่อดึง final state

**Alternative (ไม่ใช้ WS):** poll `GET /<resource>/{id}` ทุก 5-10 วินาทีจน `status` = `completed` / `failed` / `cancelled`

---

## 3. Health & Metadata

### `GET /health`

Liveness probe.

**Response 200:**
```json
{ "status": "ok" }
```

### `GET /api/v1/tasks`

ดู task types ที่ระบบรองรับ + Pydantic schema สำหรับสร้าง dynamic form ใน FE.

**Response 200:** _(plain list, ไม่ใช่ envelope)_
```json
[
  {
    "task_type": "classification",
    "display_name": "Text Classification",
    "description": "Assign one label from a closed set...",
    "sample_schema": { /* JSON schema ของ 1 row */ },
    "example": { "text": "I can't log into my account", "label": "technical" },
    "sdg_modes_supported": ["with_seed", "description_only"]
  },
  { "task_type": "tool_calling", ... },
  { "task_type": "qa", ... }
]
```

> มี **3 task types** เท่านั้น: `classification`, `tool_calling`, `qa` (per ADR-005)

### `GET /api/v1/tasks/{task_type}/example`

ดึง example payload เดี่ยวสำหรับ task ที่เลือก (ใช้สำหรับ "Insert sample" button).

**Response 200:** _(returns the example object directly — ไม่มี wrapper)_

```json
// /tasks/qa/example
{ "question": "What is the return policy?", "answer": "You can return items within 30 days of purchase." }

// /tasks/classification/example
{ "text": "I can't log into my account", "label": "technical" }

// /tasks/tool_calling/example
{ "question": "Set the oven to 250 degrees Celsius", "answer": "{\"name\":\"set_oven\",\"parameters\":{\"celsius\":250}}" }
```

**Response 422** ถ้า `task_type` ไม่ใช่หนึ่งใน 3 ตัว — `code: validation_error`, `loc: ["path", "task_type"]`.

### `GET /api/v1/base-models`

List 4-bit Unsloth models ที่ training endpoint รับ.

**Response 200:** _(plain list)_
```json
[
  {
    "id": "unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
    "display_name": "Llama 3.2 1B Instruct (4-bit)",
    "family": "llama",
    "params_billions": 1.24,
    "context_length": 131072,
    "recommended_max_seq_length": 2048,
    "quantization": "bnb-4bit",
    "license": "llama-3.2",
    "notes": "Fastest, lowest VRAM. Good first choice."
  },
  /* ...5 more (Qwen 0.5B/1.5B/3B, Llama 3B, Gemma 2B) */
]
```

> 6 models ทั้งหมด ทุกตัวเป็น `unsloth/...-bnb-4bit`. `params_billions` ≤ ~3.3 (รวม embeddings)

---

## 4. Projects

### `POST /api/v1/projects`

สร้าง project ใหม่.

**Request:**
```json
{
  "name": "qa-customer-support",
  "description": "Train a QA model on internal docs",
  "task_type": "qa"      // classification | tool_calling | qa
}
```

**Response 201:**
```json
{
  "id": "b2b97392-0314-43ea-a2d7-2e56b299d469",
  "name": "qa-customer-support",
  "description": "Train a QA model on internal docs",
  "task_type": "qa",
  "created_at": "2026-05-10T07:21:07.703083Z",
  "updated_at": "2026-05-10T07:21:07.703083Z"
}
```

### `GET /api/v1/projects?limit=&offset=`

List paginated. รับ `Page<Project>`.

### `GET /api/v1/projects/{id}`

Project detail. **404** ถ้าไม่มี.

### `DELETE /api/v1/projects/{id}`

**Cascade delete** — ลบ project + datasets + trainings + artifacts + ไฟล์ใน MinIO ทั้งหมด.

**Response 204** (no body). **404** ถ้าไม่มี.

> ⚠️ **Irreversible** — frontend ควรเตือน user + confirm dialog ก่อนเรียก

---

## 5. Datasets

### `POST /api/v1/datasets/upload-seed`

อัพโหลด seed file (JSONL/JSON/PDF). ระบบจะ run **Format Detection** (Gemini-based) ถ้า keys ไม่ canonical — auto-rename ให้ตรง schema.

**Request (multipart/form-data):**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | file | ✓ | `.jsonl`, `.json` (top-level array), หรือ `.pdf` (qa เท่านั้น) |
| `project_id` | string (UUID) | ✓ | |
| `task_type` | string | ✓ | `classification` / `tool_calling` / `qa` |
| `name` | string | ✓ | dataset display name |

**Response 201:**
```json
{
  "dataset_id": "be8da2d3-5e78-486d-99af-beb85cc1ac78",
  "task_type": "classification",
  "num_samples": 40,
  "invalid_rows": [],                     // index ของ rows ที่ผิด schema
  "format_detection": {
    "ran": true,                          // false ถ้า keys ตรง canonical อยู่แล้ว
    "model_used": "google/gemini-2.5-flash-lite",
    "field_mapping": { "message": "text", "category": "label" },
    "rows_total": 40,
    "rows_canonicalised": 40,
    "rows_dropped": 0,
    "notes": null
  },
  "pdf_uri": null                         // populate เฉพาะตอน upload PDF
}
```

**Common error cases:**
- `400 PDF uploads are supported only for task_type=qa` — PDF กับ task อื่น
- `413 PDF size <bytes> exceeds cap 26214400 (25 MiB)` — PDF ใหญ่เกิน
- `413 PDF has too many pages` — PDF > 100 หน้า
- `400 failed to parse PDF` — ไฟล์ PDF corrupt

### `POST /api/v1/datasets/generate` (SDG)

Trigger Synthetic Data Generation. **Async** — return 202.

**Discriminated by `sdg_mode`:**

#### Mode 1: `with_seed` (อ้าง dataset ที่ upload มา)
```json
{
  "sdg_mode": "with_seed",
  "project_id": "<uuid>",
  "task_type": "qa",
  "task_description": "string ≥ 10 chars อธิบาย task",
  "num_samples": 20,
  "temperature": 0.8,
  "dataset_name": "sdg-output-1",
  "seed_dataset_id": "<uuid ของ uploaded seed>"
}
```

#### Mode 2: `description_only` (ไม่มี seed — ใช้ config แยก task type)

**Classification:**
```json
{
  "sdg_mode": "description_only",
  "project_id": "<uuid>",
  "task_type": "classification",
  "task_description": "...",
  "num_samples": 15,
  "classification_config": {
    "labels": ["urgent", "not_urgent"]
  }
}
```

**Tool calling:**
```json
{
  "sdg_mode": "description_only",
  "project_id": "<uuid>",
  "task_type": "tool_calling",
  "task_description": "...",
  "num_samples": 15,
  "tool_calling_config": {
    "tool_definitions": [
      { "name": "set_oven", "description": "...",
        "parameters": { "celsius": { "type": "integer", "required": true } } }
    ]
  }
}
```

**QA:** ส่งแค่ task_description + num_samples (ไม่มี config เพิ่ม).

**Response 202:** ดู [Async pattern](#23-async-job-pattern) — `dataset_id` + `job_id` + `websocket_url`.

**Common 422:**
- `body.with_seed.seed_dataset_id: Field required` — ลืมส่ง seed id ใน with_seed mode
- `body.with_seed.task_description: String should have at least 10 characters`
- Extra fields เก่า (`seed_data`, `teacher_model`) — Phase 9 ตัด field เหล่านี้ออก

**Common 400:**
- `seed dataset task_type is qa but request asks for classification` — cross-task seed
- `Dataset ... has source=sdg; with_seed requires a dataset uploaded via /datasets/upload-seed`

### `GET /api/v1/datasets?project_id=&limit=&offset=`

List datasets, filter by project. คืน `Page<Dataset>`.

### `GET /api/v1/datasets/{id}`

Dataset detail. มี `generation_metadata` ถ้า `source=sdg`:

```json
{
  "id": "<uuid>",
  "project_id": "<uuid>",
  "name": "sdg-cls-test-1",
  "task_type": "classification",
  "source": "sdg",                            // "seed" | "sdg"
  "num_samples": 20,
  "storage_uri": "s3://datasets/sdg/<id>.jsonl",
  "size_bytes": 5367,
  "generation_metadata": {                    // null เมื่อ source=seed
    "sdg_mode": "with_seed",
    "api_calls": 42,
    "rejected_count": 0,
    "duplicate_count": 0,
    "judge_rejected_count": 4,
    "judge_parse_failures": 0,
    "seed_dataset_id": "<uuid>",
    "task_description": "...",
    "requested_samples": 20,
    "submitted_at": "...",
    "completed_at": "..."
  },
  "created_at": "...",
  "updated_at": "..."
}
```

### `GET /api/v1/datasets/{id}/preview?limit=&offset=`

ดู rows จริง (paginated). ⚠️ **response key คือ `samples` ไม่ใช่ `items`**:

```json
{
  "dataset_id": "<uuid>",
  "task_type": "qa",
  "samples": [
    { "question": "What is 2+2?", "answer": "4" },
    { "question": "Capital of France?", "answer": "Paris" }
  ],
  "total": 5
}
```

### `GET /api/v1/datasets/{id}/download`

Stream JSONL ทั้งไฟล์ — content เหมือน source file ที่ upload ไป (ไม่ rename keys ตาม Format Detection — server เก็บ canonicalised version แล้ว).

**Response 200:**
- `Content-Type: application/x-ndjson` (newline-delimited JSON, **ไม่ใช่ octet-stream**)
- Body = JSONL bytes ตรงๆ
- Server อาจตัด trailing newline ของบรรทัดสุดท้าย

**Response 404** ถ้าไม่มี dataset:
```json
{ "detail": "Dataset <id> not found", "code": "not_found", "extra": null }
```

### `DELETE /api/v1/datasets/{id}`

**Response 204** ถ้าลบสำเร็จ.
**Response 409** ถ้ามี training/evaluation อ้างอิง:
```json
{
  "detail": "Dataset <id> is referenced by 1 training_job(s) and 0 evaluation_run(s); delete those first or DELETE the parent project to cascade.",
  "code": "conflict"
}
```

---

## 6. Trainings

### `POST /api/v1/trainings`

Trigger fine-tuning. **Async** — 202. Discriminated by `mode`:

#### Mode 1: `manual`
```json
{
  "mode": "manual",
  "project_id": "<uuid>",
  "dataset_id": "<uuid>",
  "base_model": "unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
  "training_name": "qa-smoke",
  "manual_config": {
    "num_train_epochs": 1,
    "per_device_train_batch_size": 1,
    "gradient_accumulation_steps": 1,
    "learning_rate": 2e-4,
    "max_seq_length": 512,
    "lora": { "r": 8, "alpha": 16, "dropout": 0.05 }
  }
}
```

#### Mode 2: `hpo` (Optuna search)
```json
{
  "mode": "hpo",
  "project_id": "<uuid>",
  "dataset_id": "<uuid>",
  "base_model": "unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
  "training_name": "hpo-search-1",
  "hpo_config": {
    "n_trials": 5,                          // ≥ 2
    "objective_metric": "eval_loss",
    "direction": "minimize",
    "sampler": "tpe",                       // tpe | random
    "pruner": "median",                     // median | none
    "timeout_seconds": 1800,
    "search_space": {
      "learning_rate": { "type": "float", "low": 1e-5, "high": 5e-4, "log": true },
      "lora_r": { "type": "categorical", "choices": [8, 16, 32] }
    },
    "fixed_config": {
      "num_train_epochs": 2,
      "per_device_train_batch_size": 1,
      "max_seq_length": 512
    }
  }
}
```

**Response 202:**
```json
{
  "training_id": "<uuid>",
  "job_id": "<celery_uuid>",
  "mlflow_run_id": null,
  "mlflow_url": null,
  "status": "pending",
  "websocket_url": "/ws/jobs/<celery_uuid>"
}
```

### `GET /api/v1/trainings?project_id=&status=&limit=&offset=`

List + filter. คืน `Page<Training>`.

### `GET /api/v1/trainings/{id}`

Training detail. **เป็น primary endpoint สำหรับ training detail page:**

```json
{
  "id": "<uuid>",
  "project_id": "<uuid>",
  "dataset_id": "<uuid>",
  "mode": "manual",                                // manual | hpo
  "status": "completed",                           // pending | running | completed | failed | cancelled
  "celery_task_id": "<uuid>",
  "base_model": "unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
  "training_name": "qa-smoke",
  "mlflow_experiment_id": "1",
  "mlflow_run_id": "85f391f310d0452880fac683cb2f4b2a",
  "config_json": {
    "lora": { "r": 8, "alpha": 16, "dropout": 0.05, "target_modules": [...] },
    "learning_rate": 0.0002,
    "num_train_epochs": 1,
    /* ...full Pydantic-validated config... */
  },
  "best_metric_value": null,                       // populate เฉพาะ HPO mode
  "best_params_json": null,                        // populate เฉพาะ HPO mode
  "error_message": null,
  "started_at": "2026-05-10T07:35:27.522045Z",
  "ended_at": "2026-05-10T07:36:10.442540Z",
  "created_at": "...",
  "updated_at": "..."
}
```

### `GET /api/v1/trainings/{id}/mlflow-url`

ดึง URL ของ MLflow run (สำหรับ "Open in MLflow" button).

```json
{
  "training_id": "<uuid>",
  "mlflow_run_id": "<run_id>",
  "mlflow_url": "http://mlflow:5000/#/experiments/1/runs/<run_id>"
}
```

> ⚠️ **`mlflow_url` ใช้ internal hostname** `mlflow:5000` — frontend ต้อง substitute เป็น `localhost:5000` (หรือ public hostname) ก่อน redirect ไป browser

### `GET /api/v1/trainings/{id}/loss-history` ⭐ (FE chart endpoint)

**Lightweight — train_loss + eval_loss series only.** ใช้ render chart โดยไม่ต้องคุย MLflow REST.

**Response 200:**
```json
{
  "training_id": "<uuid>",
  "mlflow_run_id": "<run_id>",
  "train_loss": [
    { "step": 0, "value": 4.78, "timestamp_ms": 1778398570184 },
    { "step": 4, "value": 1.05, "timestamp_ms": 1778398570500 }
  ],
  "eval_loss": [
    { "step": 0, "value": 3.96, "timestamp_ms": 1778398570198 },
    { "step": 4, "value": 1.42, "timestamp_ms": 1778398570600 }
  ]
}
```

**Behavior:**
- จะ sorted by `step` ascending (ไม่ต้องเรียงเอง)
- ถ้า training ยังไม่เริ่ม / ไม่มี mlflow run → `train_loss=[]`, `eval_loss=[]`, `mlflow_run_id=null` (200 ปกติ)
- ถ้า MLflow ดาวน์ → **502** + `"MLflow tracking server not reachable"`

### `GET /api/v1/trainings/{id}/metrics` ⭐ (Full series + HPO summary)

**Full metric history + HPO child summary.**

**Response 200 (manual mode):**
```json
{
  "training_id": "<uuid>",
  "mlflow_run_id": "<run_id>",
  "metrics": {
    "train_loss":    [ { "step": 0, "value": 4.78, "timestamp_ms": ... }, ... ],
    "eval_loss":     [ { "step": 0, "value": 3.96, "timestamp_ms": ... }, ... ],
    "learning_rate": [ { "step": 1, "value": 0.0002, "timestamp_ms": ... }, ... ],
    "epoch":         [ ... ],
    "grad_norm":     [ ... ],
    "loss":          [ ... ]
    /* ~13 keys total — depends on what HuggingFace Trainer logs */
  },
  "hpo_children": null                           // null ใน manual mode
}
```

**Response 200 (hpo mode):** เพิ่ม `hpo_children`:
```json
{
  /* ...same as manual... */
  "hpo_children": [
    {
      "run_id": "37f0c88926...",
      "name": "trial-000",
      "final_eval_loss": 3.87,
      "params": { "learning_rate": "0.000115", "lora.r": "8" }
    },
    {
      "run_id": "397a144fe6...",
      "name": "trial-001",
      "final_eval_loss": 5.03,
      "params": { "learning_rate": "1.02e-05", "lora.r": "8" }
    },
    {
      "run_id": "8a5329ed43...",
      "name": "best",
      "final_eval_loss": 3.87,
      "params": {
        "best_params.learning_rate": "0.000115",
        "best_params.lora_r": "8",
        "best_metric_value": "3.87",
        /* ...flattened config... */
      }
    }
  ]
}
```

> ⚠️ **`params` value type = string เสมอ** (MLflow stores params as strings). Frontend ต้อง `parseFloat` / `parseInt` ก่อนใช้ใน chart

### `DELETE /api/v1/trainings/{id}` (cancel)

Cancel running training (`SIGTERM` → Celery worker). **Idempotent** — เรียกซ้ำบน cancelled training คืน 202 ปกติ.

**Response 202:**
```json
{ "training_id": "<uuid>", "status": "cancelled" }
```

---

## 7. Models / Artifacts

### `GET /api/v1/models?training_job_id=&limit=&offset=`

List artifacts. **Filter `training_job_id` ใช้หา artifact ของ training เฉพาะ — สำคัญสุด** (Bug regression — ต้องคืนแค่ 1 row, ไม่ใช่ทั้ง DB).

**Response 200:** `Page<ModelArtifact>`

### `GET /api/v1/models/{id}`

Artifact detail.

```json
{
  "id": "<uuid>",
  "training_job_id": "<uuid>",
  "name": "qa-smoke",
  "base_model": "unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
  "mlflow_run_id": "<run_id>",
  "lora_adapter_uri": "s3://models/adapters/<training_id>",
  "gguf_uri": "s3://models/exports/<artifact_id>/gguf",          // null ก่อน export
  "safetensors_uri": null,                                       // populate หลัง POST export safetensors
  "size_mb": 19.74,
  "ollama_model_tag": "slm/cdeddfd5",                            // populate หลัง GGUF export + Ollama register
  "export_error_message": null,                                  // string ถ้า export fail
  "created_at": "...",
  "updated_at": "..."
}
```

### `GET /api/v1/models/{id}/download`

Binary stream — ดาวน์โหลด GGUF หรือ SafeTensors ของ artifact (เลือก format ตามที่ export ล่าสุด).

**Response 200:**
- `Content-Type: application/octet-stream`
- File size: ~770MB (GGUF q4_k_m) หรือ ~800MB-1.5GB (SafeTensors merged)

**Response 409** ถ้า artifact ยังไม่ export.

> ⚠️ **HEAD method ไม่รองรับ** (405). ใช้ GET เท่านั้น

### `POST /api/v1/models/{id}/export`

Trigger export. **Async** — 202.

**Request (GGUF):**
```json
{ "format": "gguf", "quantization": "q4_k_m" }
```

> `quantization` เป็น free-form string ที่ส่งต่อให้ `llama-quantize`. Default = `q4_k_m`. Common values: `q4_k_m` (recommended), `q5_k_m`, `q8_0`, `f16`. Worker validate string ตอนรัน llama.cpp — string อื่นที่ llama.cpp รองรับก็ใช้ได้.

**Request (SafeTensors):**
```json
{ "format": "safetensors" }                      // ไม่มี quantization
```

**Response 202:**
```json
{
  "artifact_id": "<uuid>",
  "format": "gguf",
  "job_id": "<celery_uuid>",
  "status": "pending",
  "websocket_url": "/ws/jobs/<celery_uuid>"
}
```

> ⚠️ **GGUF export จะ register ลง Ollama อัตโนมัติ** (best-effort) — ถ้า `ollama_model_tag` populate = พร้อม inference, ถ้า null = เฉพาะ MinIO. SafeTensors ไม่ register

---

## 8. Evaluations

### `POST /api/v1/evaluations`

Run evaluation (rule-based metrics + optional LLM judge). **Async** — 202.

**Request:**
```json
{
  "model_artifact_id": "<uuid>",
  "dataset_id": "<uuid>",
  "use_llm_judge": true,                              // false = แค่ rule-based (ฟรี)
  "judge_model": "google/gemini-3.1-flash-lite-preview"   // optional, default จาก backend config
}
```

**Response 202:**
```json
{
  "evaluation_id": "<uuid>",
  "job_id": "<celery_uuid>",
  "status": "pending",
  "websocket_url": "/ws/jobs/<celery_uuid>"
}
```

**Common error cases:**
- `404 Model <id> not found` — artifact ไม่มี
- `409 Model has not been registered with Ollama` — ยังไม่ POST export gguf
- `409 Dataset has no rows persisted yet` — dataset ยังว่าง (SDG ไม่จบ / upload fail)
- `400 Dataset task_type=classification does not match artifact task_type=qa` — task_type ไม่ตรง

### `GET /api/v1/evaluations/{id}`

Eval detail.

```json
{
  "id": "<uuid>",
  "model_artifact_id": "<uuid>",
  "dataset_id": "<uuid>",
  "celery_task_id": "<uuid>",
  "status": "completed",
  "metrics_json": {
    "exact_match": 0.0,
    "rouge1": 0.286,
    "rouge2": 0.0,
    "rougeL": 0.286,
    "bleu": 0.020,
    "n": 5,
    "llm_judge_skipped_rows": 0                       // populate เมื่อ use_llm_judge=true
  },
  "llm_judge_score": 5.0,                             // null ถ้า rule-based; 1.0-5.0 ถ้า judge
  "llm_judge_model": "google/gemini-3.1-flash-lite-preview",   // null ถ้า rule-based
  "error_message": null,
  "started_at": "...",
  "ended_at": "..."
}
```

> 💡 **Metrics ตาม task_type** (verified จาก `ai_engine/evaluation/metrics_*.py`):
> - `qa` → `bleu`, `rouge1`, `rouge2`, `rougeL`, `exact_match`, `n` _(verified live)_
> - `classification` → `accuracy`, `f1_macro`, `f1_per_label` (dict), `confusion_matrix`, `n`
> - `tool_calling` → `json_validity`, `name_accuracy`, `arg_accuracy`, `exact_match`, `n`

### `POST /api/v1/evaluations/compare`

N-way compare — pivot metrics ข้าม eval runs.

**Request:**
```json
{ "evaluation_ids": ["<uuid_a>", "<uuid_b>", "<uuid_c>"] }   // ≥ 2 ids
```

**Response 200:**
```json
{
  "evaluation_ids": ["<uuid_a>", "<uuid_b>"],
  "metrics": {
    "exact_match": { "<uuid_a>": 0.0, "<uuid_b>": 0.0 },
    "rouge1":      { "<uuid_a>": 0.286, "<uuid_b>": 0.286 },
    "rougeL":      { "<uuid_a>": 0.286, "<uuid_b>": 0.286 },
    "bleu":        { "<uuid_a>": 0.020, "<uuid_b>": 0.020 },
    "n":           { "<uuid_a>": 5, "<uuid_b>": 5 }
  },
  "judge_scores": {
    "<uuid_a>": null,                  // null = rule-based
    "<uuid_b>": 5.0
  }
}
```

> 💡 **FE pattern:** render เป็นตาราง — แถว = metric name, คอลัมน์ = eval run, cell = value. metric ที่หายไปบาง run = null cell

**Response 422** ถ้าส่ง 1 id หรือน้อยกว่า.

---

## 9. Inference (proxy to Ollama)

### `GET /api/v1/inference/models`

List Ollama models — **OpenAI-compatible shape**.

**Response 200:**
```json
{
  "object": "list",
  "data": [
    { "id": "slm/cdeddfd5:latest", "object": "model", "created": 0, "owned_by": "ollama" }
  ]
}
```

> ถ้า Ollama ว่าง → `data: []` (200, **NOT 500**)

### `POST /api/v1/inference/chat/completions`

OpenAI Chat Completions API — proxy ไป Ollama. ใช้ OpenAI SDK ตรงๆ ก็ได้ ถ้า base_url ตั้งเป็น `http://<api>/api/v1/inference`.

**Request:**
```json
{
  "model": "<artifact_id หรือ slm/<id8>>",         // รับทั้ง UUID และ Ollama tag
  "messages": [
    { "role": "system", "content": "You are a helpful assistant." },
    { "role": "user", "content": "What is the capital of France?" }
  ],
  "max_tokens": 50,
  "temperature": 0.0,
  "stream": false                                  // streaming ยังไม่รองรับ
}
```

**Response 200:**
```json
{
  "id": "chatcmpl-253",
  "object": "chat.completion",
  "created": 1778398708,
  "model": "slm/cdeddfd5",                         // resolved Ollama tag
  "choices": [
    {
      "index": 0,
      "message": { "role": "assistant", "content": "Paris.", "name": null, "tool_call_id": null },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 22,
    "completion_tokens": 3,
    "total_tokens": 25
  }
}
```

### `POST /api/v1/inference/completions` (legacy text)

OpenAI legacy completions (สำหรับ tools เก่าที่ยังใช้ `prompt` แทน `messages`).

**Request:**
```json
{
  "model": "<artifact_id>",
  "prompt": "The capital of France is",
  "max_tokens": 20,
  "temperature": 0.0
}
```

**Response 200:**
```json
{
  "id": "cmpl-940",
  "object": "text_completion",
  "created": 1778399106,
  "model": "slm/cdeddfd5",
  "choices": [
    { "index": 0, "text": " Paris.", "finish_reason": "stop" }
  ],
  "usage": { "prompt_tokens": 15, "completion_tokens": 3, "total_tokens": 18 }
}
```

> ⚠️ Difference: `choices[].text` (legacy) **vs** `choices[].message.content` (chat)

---

## 10. WebSocket Events

### `WS /ws/jobs/{job_id}`

Subscribe เพื่อรับ live progress events ของ Celery job.

### Common shape (ทุก message)

```json
{
  "type": "<discriminator>",
  "job_id": "<celery_task_id>",
  "timestamp": "2026-05-10T07:35:27.522045+00:00",
  /* ...type-specific fields... */
}
```

มี **5 event types** discriminated by `type`:

### 10.1 `sdg_progress`

```json
{
  "type": "sdg_progress",
  "job_id": "...",
  "timestamp": "...",
  "phase": "judging",                  // 1 ใน 8 phases ด้านล่าง
  "samples_generated": 12,
  "samples_target": 20,
  "samples_valid": 10,
  "samples_rejected": 2,
  "duplicates_removed": 0,
  "current_loop": 0,                   // 0-indexed SDG loop iteration
  "judge_rejected": 4,                 // rows ถูก LLM judge ตัด
  "judge_parse_failures": 0,
  "dedup_rejected": 0                  // MinHash near-dup ตัด
}
```

**Phase enum:** `generating`, `validating`, `deduplicating`, `persisting`, `format_detection`, `meta_prompting`, `judging`, `dedup`

### 10.2 `training_progress`

```json
{
  "type": "training_progress",
  "job_id": "...",
  "timestamp": "...",
  "epoch": 1.5,                        // float — fractional
  "epochs_total": 3,
  "step": 25,
  "steps_total": 50,
  "train_loss": 1.43,
  "eval_loss": 1.87,                   // null ระหว่าง non-eval steps
  "learning_rate": 0.00018,
  "samples_per_second": 12.3,
  "gpu_memory_mb": 4520.5
}
```

> 💡 **Observed live (2026-05-10):** worker ส่ง 1 training_progress ต่อ logging-step + 1 final event ที่ `train_loss/eval_loss/lr = null` ตอน end-of-epoch (เป็น marker, ไม่ใช่ data point — frontend ควร skip ถ้า train_loss=null ก่อน plot).

### 10.3 `hpo_progress` (HPO mode เท่านั้น)

```json
{
  "type": "hpo_progress",
  "job_id": "...",
  "timestamp": "...",
  "trial_number": 1,                    // 0-indexed
  "trials_total": 2,
  "current_params": { "learning_rate": 0.000115, "lora_r": 8 },
  "best_value": 3.87,
  "best_params": { "learning_rate": 0.000115, "lora_r": 8 },
  "last_trial_value": 5.03,
  "last_trial_pruned": false,
  "inner_progress": {                   // optional — nested TrainingProgress ของ trial ปัจจุบัน
    "type": "training_progress",
    "epoch": 0.5,
    "step": 5,
    "train_loss": 4.2,
    /* ...full TrainingProgress fields... */
  }
}
```

> 💡 **`inner_progress` may be `null`** — populated เฉพาะตอน worker emit per-step ภายใน trial. ใน trial ที่สั้นมาก (small dataset) อาจไม่มี inner emit เลย → `null` ตลอด. **Live observed (2026-05-10):** HPO บน 3-row dataset เห็น 2 hpo_progress events ที่ inner_progress=null ทั้งคู่.

### 10.4 `completed` (event สุดท้ายถ้าสำเร็จ)

```json
{
  "type": "completed",
  "job_id": "...",
  "timestamp": "...",
  "result": { /* shape ต่างกันตาม task type — ดูตารางด้านล่าง */ },
  "mlflow_run_id": "<run_id>",          // optional
  "dataset_id": "<uuid>",               // optional (SDG)
  "model_artifact_id": "<uuid>"         // optional (training/export)
}
```

| Job type | `result` ที่ได้ |
|----------|----------------|
| **SDG** | `{samples_generated, rejected_count, duplicate_count, judge_rejected_count, judge_parse_failures, api_calls, storage_uri, size_bytes}` |
| **Training (manual)** | `{training_id, model_artifact_id, lora_adapter_uri, final_train_loss, final_eval_loss, steps_completed, train_runtime_seconds, metrics}` |
| **Training (HPO)** | (manual fields) + `best_metric_value`, `best_params_json` |
| **Evaluation** | `{evaluation_id, metrics, llm_judge_score, llm_judge_model}` |
| **Export** | `{artifact_id, format, gguf_uri, safetensors_uri, ollama_model_tag}` |

### 10.5 `failed` (event สุดท้ายถ้าผิด)

```json
{
  "type": "failed",
  "job_id": "...",
  "timestamp": "...",
  "error": "CUDA out of memory ... try smaller batch size",
  "error_type": "OutOfMemoryError",      // exception class name
  "traceback": null                       // ปกติ null; populate เป็น string ตอน LOG_LEVEL=DEBUG
}
```

> 💡 **Live verified (2026-05-10):** Default deploy (LOG_LEVEL=INFO) → `traceback: null`. Field มีเสมอใน JSON — frontend อ่าน `error` กับ `error_type` พอ.
>
> **ตัวอย่าง failure types ที่เคยเจอ:**
> - `error_type: "ValueError", error: "No trials are completed yet."` — HPO เทรนทุก trial fail (เช่น dataset เล็กเกิน eval split=0)
> - `error_type: "OutOfMemoryError"` — GPU OOM (ลด batch_size หรือ max_seq_length)
> - `error_type: "ModuleNotFoundError"` — worker image ขาด dep (เช่น sacrebleu) — rebuild worker

### Event sequences ที่จะเห็นในแต่ละ job

| Job | Sequence |
|-----|----------|
| SDG | `sdg_progress` × N → `completed` หรือ `failed` |
| Training (manual) | `training_progress` × N (1 ต่อ step) → `completed` หรือ `failed` |
| Training (HPO) | `hpo_progress` × N (1 ต่อ trial finish, มี `inner_progress` per-step) → `completed` หรือ `failed` |
| Export | (no progress) → `completed` หรือ `failed` |
| Evaluation | (no progress) → `completed` หรือ `failed` |

> ⚠️ **Export กับ Evaluation ยังไม่มี per-step progress event** — frontend ใช้ spinner รอจน `completed`

### Connection lifecycle

| Action | Behavior |
|--------|----------|
| Client connect | Server accept ทันที + subscribe Redis channel `job:{job_id}` |
| Server เห็น message | Forward เป็น text JSON ตรงๆ (no buffering) |
| Job จบ | **WS ไม่ auto-close** — client ต้อง close เอง |
| Client disconnect | Server cleanup pubsub + close Redis client |
| Redis error | Server close ด้วย code `1011` + reason |

> ⚠️ **No replay** — events ก่อน connect จะหายไป. ถ้า user เปิดหน้าหลัง job เริ่ม → ใช้ REST GET เพื่อ backfill state

### Quick test (browser console)

```javascript
const ws = new WebSocket("ws://localhost:8000/ws/jobs/<job_id>");
ws.onmessage = (e) => {
  const msg = JSON.parse(e.data);
  console.log(msg.type, msg);
  if (msg.type === "completed" || msg.type === "failed") {
    ws.close();
  }
};
ws.onclose = () => console.log("WS closed");
```

---

## 11. Frontend Integration Patterns

### หน้า Dashboard

```
GET  /api/v1/projects?limit=20&offset=0          → sidebar projects
GET  /api/v1/trainings?limit=10                  → recent trainings table
```

### หน้า Project Detail

```
GET  /api/v1/projects/{project_id}               → project header
GET  /api/v1/datasets?project_id={id}            → datasets tab
GET  /api/v1/trainings?project_id={id}           → trainings tab
DELETE /api/v1/projects/{id}                     → cleanup button (cascade — confirm dialog!)
```

### หน้า Upload Data

```
POST /api/v1/datasets/upload-seed (multipart)    → seed file
                                                 → response มี format_detection.field_mapping
                                                   ถ้า ran=true แสดง preview ของ rename
```

### หน้า SDG Wizard

```
1. POST /api/v1/datasets/generate {sdg_mode, ...}        → 202 + websocket_url
2. WebSocket subscribe /ws/jobs/{job_id}                  → progress bar (sdg_progress events)
3. on "completed" event → close WS
4. GET  /api/v1/datasets/{dataset_id}                     → final state + generation_metadata
5. GET  /api/v1/datasets/{dataset_id}/preview?limit=20    → ดู rows ที่ generate ออกมา
```

### หน้า Start Training

```
1. POST /api/v1/trainings {mode: manual|hpo, ...}        → 202 + websocket_url
2. WebSocket subscribe                                    → training_progress (manual) หรือ
                                                            hpo_progress (HPO) — render live loss curve
3. on "completed" → close WS, refresh data
4. GET  /api/v1/trainings/{id}/loss-history              → render final loss chart
5. GET  /api/v1/models?training_job_id={id}              → show artifact card
```

### หน้า Training Detail

```
GET  /api/v1/trainings/{id}                              → header (status, params, best_metric)
GET  /api/v1/trainings/{id}/loss-history                 → main chart (train_loss + eval_loss)
GET  /api/v1/trainings/{id}/metrics                      → expanded view (all metrics + HPO trials)
GET  /api/v1/trainings/{id}/mlflow-url                   → "Open in MLflow" button
GET  /api/v1/models?training_job_id={id}                 → artifact card
DELETE /api/v1/trainings/{id}                            → cancel button (ถ้า status=running/pending)
```

### หน้า Export

```
1. POST /api/v1/models/{id}/export {format, quantization}    → 202 + websocket_url
2. (no progress events — แสดง spinner)
3. on "completed" → GET  /api/v1/models/{id}                 → ดู gguf_uri / safetensors_uri / ollama_model_tag
4. (optional) GET  /api/v1/models/{id}/download              → download binary
```

### หน้า Evaluation

```
1. POST /api/v1/evaluations {use_llm_judge, judge_model?}    → 202
2. (no progress events — spinner)
3. GET  /api/v1/evaluations/{id}                              → metrics + judge_score
4. (A/B) POST /api/v1/evaluations/compare {evaluation_ids}    → pivot table
```

### หน้า Playground

```
GET  /api/v1/inference/models                                → model dropdown
POST /api/v1/inference/chat/completions {model, messages}    → submit
```

### TypeScript codegen tip

ใช้ **openapi-typescript** หรือ **openapi-fetch** generate types ตรงจาก OpenAPI spec:

```bash
npx openapi-typescript http://localhost:8000/openapi.json --output ./src/api/types.ts
```

แล้ว FE จะมี types ครบทุก request/response — ไม่ต้องเขียนเอง:

```typescript
import type { paths } from "./api/types";

type Project = paths["/api/v1/projects/{project_id}"]["get"]["responses"]["200"]["content"]["application/json"];
type LossHistory = paths["/api/v1/trainings/{training_id}/loss-history"]["get"]["responses"]["200"]["content"]["application/json"];
```

WebSocket message types ก็ generate ได้ — Pydantic models export schema ลง OpenAPI ครบ.

---

## 12. HTTP Status Reference

### Success

| Code | When |
|------|------|
| 200 OK | GET / sync POST (compare, inference) |
| 201 Created | POST ที่สร้าง resource sync (project, upload-seed) |
| 202 Accepted | POST ที่ enqueue async job (generate, training, export, eval) + DELETE training (cancel) |
| 204 No Content | DELETE project / dataset (sync removal) |

### Client errors

| Code | When |
|------|------|
| 400 Bad Request | Business rule violation (cross-task seed, task_type mismatch, bad PDF, etc.) |
| 404 Not Found | Resource doesn't exist (use envelope `{detail, code: "not_found"}`) |
| 409 Conflict | State guard (delete dataset with refs, eval with no Ollama tag, no LoRA artifact) |
| 413 Payload Too Large | PDF > 25 MiB หรือ > 100 หน้า |
| 422 Unprocessable Entity | Pydantic validation fail (envelope's `extra.errors[]` มี `loc` + `msg`) |

### Server errors

| Code | When |
|------|------|
| 500 Internal Server Error | unexpected — มี `correlation_id` ใน log |
| 502 Bad Gateway | downstream service ดาวน์ (เช่น MLflow ไม่ตอบใน `/trainings/{id}/metrics`) |

### WebSocket close codes

| Code | Reason |
|------|--------|
| 1000 | Normal close |
| 1011 | Server error (Redis pubsub fail) |

---

## Appendix: Quick reference — endpoint list ทั้งหมด

```
# Health & Metadata
GET    /health
GET    /api/v1/tasks
GET    /api/v1/tasks/{task_type}/example
GET    /api/v1/base-models

# Projects
POST   /api/v1/projects
GET    /api/v1/projects
GET    /api/v1/projects/{id}
DELETE /api/v1/projects/{id}

# Datasets
POST   /api/v1/datasets/upload-seed       (multipart)
POST   /api/v1/datasets/generate          (async 202)
GET    /api/v1/datasets
GET    /api/v1/datasets/{id}
GET    /api/v1/datasets/{id}/preview
GET    /api/v1/datasets/{id}/download
DELETE /api/v1/datasets/{id}

# Trainings
POST   /api/v1/trainings                  (async 202)
GET    /api/v1/trainings
GET    /api/v1/trainings/{id}
GET    /api/v1/trainings/{id}/mlflow-url
GET    /api/v1/trainings/{id}/loss-history    ⭐ FE chart
GET    /api/v1/trainings/{id}/metrics         ⭐ Full + HPO summary
DELETE /api/v1/trainings/{id}             (cancel async 202)

# Models / Artifacts
GET    /api/v1/models
GET    /api/v1/models/{id}
GET    /api/v1/models/{id}/download       (binary stream)
POST   /api/v1/models/{id}/export         (async 202)

# Evaluations
POST   /api/v1/evaluations                (async 202)
GET    /api/v1/evaluations/{id}
POST   /api/v1/evaluations/compare        (sync 200 — read-only)

# Inference (proxy → Ollama)
GET    /api/v1/inference/models
POST   /api/v1/inference/chat/completions
POST   /api/v1/inference/completions

# WebSocket
WS     /ws/jobs/{job_id}                  (5 event types — sdg_progress / training_progress / hpo_progress / completed / failed)
```
