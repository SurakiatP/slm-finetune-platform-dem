# Swagger Test Guide — SLM Fine-Tuning Platform API

คู่มือ end-to-end สำหรับทดสอบ API 25 endpoints ผ่าน Swagger UI ที่ `/docs` — เรียงตาม **lifecycle จริง** (project → dataset → training → export → inference → eval) พร้อม request/response ตัวอย่างที่ใช้งานได้จริง

> ทุก request body ที่อยู่ในเอกสารนี้ผ่านการ verify กับ OpenAPI spec ของ deploy ปัจจุบัน (vast.ai 4060 Ti host, branch `dev`) — copy-paste ลง Swagger ได้เลย

---

## 0. เปิด Swagger UI

### บน vast.ai (ผ่าน SSH tunnel)

⚠️ **คำสั่ง SSH default ของ vast.ai (`-L 8080:localhost:8080`) ผิด** — API listen ที่ port `8000`

```bash
# ออก session เก่า (ถ้ายังเปิด) แล้ว SSH ใหม่ด้วย port forward ที่ถูก
ssh -p 35711 root@211.21.106.81 -L 8000:localhost:8000
```

→ เปิด browser ของ laptop ที่ **http://localhost:8000/docs**

### บน laptop (local development)

```powershell
# ถ้า docker compose ยังรันอยู่
Start-Process http://localhost:8000/docs
```

### ตรวจว่าเปิดได้

หน้า Swagger ต้องโชว์:
- Title: **SLM Fine-Tuning Platform 0.1.0**
- 8 tag groups ทางซ้าย: `projects`, `datasets`, `trainings`, `models`, `inference`, `evaluations`, `metadata`, `system`
- ปุ่ม **Authorize** มุมขวาบน → **ไม่ต้องกด** (PoC นี้ no-auth ตาม `require.md`)

---

## 1. Lifecycle — Endpoints เรียงตามลำดับใช้จริง

```
                                                                   ┌─ POST /api/v1/datasets/upload-seed   (no OpenRouter)
[Setup]                                                            │
GET  /health                                                       ├─ POST /api/v1/datasets/generate      (needs OpenRouter)
GET  /api/v1/tasks                          → 3 task types         │
GET  /api/v1/tasks/{task_type}/example      → seed shape           ▼
GET  /api/v1/base-models                    → 1B / 3B options    [Dataset]
                                                                   │
[Project]                                                          ├─ GET /api/v1/datasets/{id}           (poll until storage_uri set)
POST /api/v1/projects                                              ├─ GET /api/v1/datasets/{id}/preview
GET  /api/v1/projects                                              ▼
GET  /api/v1/projects/{id}                                       [Training]
                                                                   │
                                                                   ├─ POST /api/v1/trainings              (manual or HPO)
                                                                   ├─ GET  /api/v1/trainings/{id}         (poll until completed)
                                                                   ├─ GET  /api/v1/trainings/{id}/mlflow-url
                                                                   ▼
                                                                 [Model]
                                                                   │
                                                                   ├─ GET  /api/v1/models?training_job_id={id}
                                                                   ├─ POST /api/v1/models/{id}/export    → GGUF / SafeTensors
                                                                   ▼
                                                                 [Use]
                                                                   ├─ POST /api/v1/inference/chat/completions
                                                                   └─ POST /api/v1/evaluations
```

---

## 2. ก่อนเริ่ม — เตรียม OPENROUTER_API_KEY (ทางเลือก)

API บางเส้นเรียก OpenRouter:
- `POST /api/v1/datasets/generate` (SDG) — **ต้องมี** key
- `POST /api/v1/evaluations` ถ้า `use_llm_judge: true` — **ต้องมี** key

ตั้งคีย์บน VM:
```bash
ssh -p 35711 root@211.21.106.81
nano /root/slm-platform/.env
# แก้บรรทัด: OPENROUTER_API_KEY=sk-or-v1-...
docker compose restart api worker
```

ถ้า**ไม่มี** key — ใช้ `POST /api/v1/datasets/upload-seed` (อัปโหลด JSONL ที่เตรียมไว้) แทน path SDG ได้ทุกขั้น

---

## 3. Smoke check (start ที่นี่ทุกครั้ง)

### `GET /health`
- Tag: **system**
- คลิก **Try it out** → **Execute**

**Response 200:**
```json
{ "status": "ok" }
```

ถ้าไม่ได้ 200 → ตรวจ `docker compose ps` ก่อนทำต่อ

---

## 4. Metadata (read-only — ใช้เพื่อรู้ shape ก่อน POST)

### `GET /api/v1/tasks`
ดูทุก task type ที่ system รองรับ

**Response 200 (snippet):**
```json
[
  {
    "task_type": "classification",
    "display_name": "Text Classification",
    "description": "Assign one label from a closed set...",
    "sample_schema": { "...": "JSON schema for one row" },
    "example": { "text": "I can't log into my account", "label": "technical" },
    "sdg_modes_supported": ["with_seed", "description_only"]
  },
  { "task_type": "tool_calling", "...": "..." },
  { "task_type": "qa", "...": "..." }
]
```

### `GET /api/v1/tasks/{task_type}/example`
- path param `task_type` = `qa` | `classification` | `tool_calling`
- ใช้ดู shape ของ 1 row ก่อนเตรียม seed file

**Response 200 (qa):**
```json
{
  "question": "What is the return policy?",
  "answer": "You can return items within 30 days of purchase."
}
```

**Response 200 (classification):**
```json
{ "text": "I can't log into my account", "label": "technical" }
```

**Response 200 (tool_calling):**
```json
{
  "question": "Set the oven to 250 degrees Celsius",
  "answer": "{\"name\":\"set_oven\",\"parameters\":{\"celsius\":250}}"
}
```
> ⚠️ Tool calling: ฟิลด์ `answer` เป็น **JSON-encoded string** ไม่ใช่ object

### `GET /api/v1/base-models`
ดู base models ที่ใช้ fine-tune ได้

**Response 200 (snippet):**
```json
[
  {
    "id": "unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
    "display_name": "Llama 3.2 1B Instruct (4-bit)",
    "params_billions": 1.24,
    "context_length": 131072,
    "recommended_max_seq_length": 2048,
    "quantization": "bnb-4bit",
    "notes": "Fastest, lowest VRAM. Good first choice for QA and classification PoCs."
  },
  { "id": "unsloth/Llama-3.2-3B-Instruct-bnb-4bit", "...": "default" }
]
```

จำ `id` ที่จะใช้ → ใส่ในฟิลด์ `base_model` ตอน POST trainings

---

## 5. สร้าง Project

### `POST /api/v1/projects`

**Request body** (เลือก task_type ที่จะทำ):
```json
{
  "name": "policy-bot",
  "description": "Answer questions about our return policy",
  "task_type": "qa"
}
```

**Response 201:**
```json
{
  "id": "58e2064c-e2e2-4096-817e-38bf1abf7c87",
  "name": "policy-bot",
  "description": "Answer questions about our return policy",
  "task_type": "qa",
  "created_at": "2026-05-08T13:30:35.795440Z",
  "updated_at": "2026-05-08T13:30:35.795440Z"
}
```

> 🔖 **เก็บ `id`** — ทุก request หลังจากนี้ใช้ `project_id` นี้

**Validation error (422):**
```json
{
  "detail": "body.name: Field required",
  "code": "validation_error",
  "extra": { "errors": [ /* per-field details */ ] }
}
```

### `GET /api/v1/projects?limit=10`

**Response 200:**
```json
{
  "items": [ { /* ProjectResponse */ } ],
  "total": 1,
  "limit": 10,
  "offset": 0
}
```

### `GET /api/v1/projects/{project_id}`

ดู project เดียว

### `PATCH /api/v1/projects/{project_id}`

แก้ `name` หรือ `description` (`task_type` เปลี่ยนไม่ได้ — immutable):
```json
{ "description": "Updated description" }
```

### `DELETE /api/v1/projects/{project_id}`

ลบ project + cascade ทุก dataset / training / model

---

## 6. สร้าง Dataset — เลือก 1 ใน 2 ทาง

### Path A — Upload seed file (ไม่ต้องใช้ OpenRouter)

**`POST /api/v1/datasets/upload-seed`** — multipart form

ก่อนกด Execute เตรียมไฟล์ `seed.jsonl` (1 row ต่อบรรทัด):

```jsonl
{"question": "What is the return window?", "answer": "Items can be returned within 30 days of purchase."}
{"question": "Do I need a receipt?", "answer": "Yes, please keep your receipt for any return."}
{"question": "Can I return sale items?", "answer": "Sale items are final sale and cannot be returned."}
{"question": "How long does a refund take?", "answer": "5-7 business days after we receive the item."}
{"question": "Where do I ship returns?", "answer": "Returns Lane 123, Bangkok 10110."}
```

ใน Swagger:
- `project_id`: `58e2064c-...` (จาก §5)
- `task_type`: `qa`
- `file`: เลือก `seed.jsonl`
- `name` (optional): `policy-seed-v1`

**Response 201:**
```json
{
  "dataset_id": "f1c0a4ba-2d12-4001-b3c0-9e3b7e1a4f12",
  "task_type": "qa",
  "num_samples": 5,
  "invalid_rows": []
}
```

> ถ้า `invalid_rows: [3, 7]` แสดงว่าแถว index 3 กับ 7 ไม่ผ่าน schema validation — แก้ไฟล์แล้วอัปใหม่

### Path B — Generate via SDG (ต้องตั้ง OPENROUTER_API_KEY)

**`POST /api/v1/datasets/generate`** — JSON

#### B1. Mode `with_seed` (แนะนำ — quality สูงกว่า)

```json
{
  "sdg_mode": "with_seed",
  "project_id": "58e2064c-e2e2-4096-817e-38bf1abf7c87",
  "task_type": "qa",
  "task_description": "Answer questions about our return policy",
  "num_samples": 50,
  "temperature": 0.8,
  "dataset_name": "policy-sdg-v1",
  "seed_data": [
    { "question": "What is the return window?", "answer": "30 days." },
    { "question": "Do I need a receipt?", "answer": "Yes, please keep it." },
    { "question": "Sale items returnable?", "answer": "Sale items are final." },
    { "question": "Refund timing?", "answer": "5-7 business days." },
    { "question": "Where to ship?", "answer": "Returns Lane 123." }
  ]
}
```

#### B2. Mode `description_only` (ไม่มี seed, ต้องใส่ task-specific config)

**Classification:**
```json
{
  "sdg_mode": "description_only",
  "project_id": "<your-project-id>",
  "task_type": "classification",
  "task_description": "Classify customer support tickets into billing / technical / general",
  "num_samples": 200,
  "temperature": 0.9,
  "classification_config": {
    "labels": ["billing", "technical", "general"]
  }
}
```

**Tool calling:**
```json
{
  "sdg_mode": "description_only",
  "project_id": "<your-project-id>",
  "task_type": "tool_calling",
  "task_description": "Translate kitchen instructions into JSON tool calls",
  "num_samples": 150,
  "temperature": 0.7,
  "tool_calling_config": {
    "tool_definitions": [
      {
        "name": "set_oven",
        "description": "Set oven temperature",
        "parameters": {
          "celsius": { "type": "integer", "description": "Target temp", "required": true }
        }
      },
      {
        "name": "start_timer",
        "description": "Start a kitchen timer",
        "parameters": {
          "minutes": { "type": "integer", "required": true }
        }
      }
    ]
  }
}
```

**Response 202 (Accepted — job runs async):**
```json
{
  "job_id": "5e2bdc60-1fa1-4e1d-9b91-e7d2b34c7ed3",
  "dataset_id": "9b0e7c1f-b834-4df0-9c81-c4b6ec5c6e10",
  "status": "pending",
  "websocket_url": "ws://localhost:8000/ws/jobs/5e2bdc60-1fa1-4e1d-9b91-e7d2b34c7ed3"
}
```

> 🔖 เก็บ `dataset_id` (ตัวเดียวกันกับ Path A)
> 🔖 เก็บ `websocket_url` ถ้าจะดู progress live (ดู §10)

---

## 7. รอ Dataset เสร็จ + Preview

### `GET /api/v1/datasets/{dataset_id}` (poll)

ทำซ้ำทุก ~5 วิ จนเห็น `storage_uri` มีค่า

**Response 200 (กำลังทำ):**
```json
{
  "id": "9b0e7c1f-...",
  "project_id": "58e2064c-...",
  "name": "policy-sdg-v1",
  "task_type": "qa",
  "source": "synthetic",
  "num_samples": 0,
  "storage_uri": null,
  "size_bytes": null,
  "generation_metadata": {
    "sdg_mode": "with_seed",
    "teacher_model": "anthropic/claude-3.5-sonnet"
  },
  "created_at": "...",
  "updated_at": "..."
}
```

**Response 200 (เสร็จแล้ว):**
```json
{
  "id": "9b0e7c1f-...",
  "num_samples": 50,
  "storage_uri": "s3://datasets/9b0e7c1f-.../data.jsonl",
  "size_bytes": 12384,
  "...": "..."
}
```

### `GET /api/v1/datasets/{dataset_id}/preview?limit=5`

**Response 200:**
```json
{
  "dataset_id": "9b0e7c1f-...",
  "task_type": "qa",
  "samples": [
    { "question": "...", "answer": "..." },
    { "question": "...", "answer": "..." }
  ],
  "total": 50
}
```

### `GET /api/v1/datasets/{dataset_id}/download`
Stream ทั้งไฟล์ JSONL — จะใหญ่ ใช้เฉพาะตอนต้องดูข้อมูลทั้งหมด

### `DELETE /api/v1/datasets/{dataset_id}`
ลบ — best-effort cleanup ใน MinIO

---

## 8. Train — เลือก 1 ใน 2 mode

### Mode A — Manual (config ที่เรากำหนด)

**`POST /api/v1/trainings`**:
```json
{
  "mode": "manual",
  "project_id": "58e2064c-e2e2-4096-817e-38bf1abf7c87",
  "dataset_id": "9b0e7c1f-b834-4df0-9c81-c4b6ec5c6e10",
  "base_model": "unsloth/Llama-3.2-3B-Instruct-bnb-4bit",
  "training_name": "policy-bot-v1",
  "manual_config": {
    "learning_rate": 2e-4,
    "num_train_epochs": 3,
    "per_device_train_batch_size": 2,
    "gradient_accumulation_steps": 4,
    "warmup_ratio": 0.05,
    "weight_decay": 0.01,
    "max_seq_length": 2048,
    "lora": {
      "r": 16,
      "alpha": 32,
      "dropout": 0.05
    }
  }
}
```

> 💡 **Smoke test config** (เร็ว ~5 นาที, ใช้ verify pipeline ทำงาน):
> ```json
> "manual_config": { "num_train_epochs": 1, "per_device_train_batch_size": 1 }
> ```
> โดย dataset เล็ก ๆ (≤ 50 rows) จะเทรนจบใน 2-5 นาที

### Mode B — HPO (Optuna sweep หา hyperparam ดีสุด)

**`POST /api/v1/trainings`**:
```json
{
  "mode": "hpo",
  "project_id": "58e2064c-...",
  "dataset_id": "9b0e7c1f-...",
  "base_model": "unsloth/Llama-3.2-3B-Instruct-bnb-4bit",
  "training_name": "policy-bot-hpo",
  "hpo_config": {
    "n_trials": 8,
    "objective_metric": "eval_loss",
    "direction": "minimize",
    "sampler": "tpe",
    "pruner": "median",
    "search_space": {
      "learning_rate": { "type": "float", "low": 1e-5, "high": 1e-3, "log": true },
      "num_train_epochs": { "type": "int", "low": 2, "high": 5 },
      "lora_r": { "type": "categorical", "choices": [8, 16, 32] }
    }
  }
}
```

**Response 202 (ทั้ง 2 mode):**
```json
{
  "job_id": "celery-task-id-here",
  "training_id": "tr-uuid-here",
  "mlflow_run_id": null,
  "mlflow_url": null,
  "status": "pending",
  "websocket_url": "ws://localhost:8000/ws/jobs/celery-task-id-here"
}
```

> 🔖 เก็บ `training_id`

---

## 9. ดู Training Progress

### `GET /api/v1/trainings/{training_id}` (poll)

**Response 200 (running):**
```json
{
  "id": "tr-uuid-here",
  "status": "running",
  "mode": "manual",
  "base_model": "unsloth/Llama-3.2-3B-Instruct-bnb-4bit",
  "mlflow_run_id": "abc123",
  "mlflow_experiment_id": "1",
  "config_json": { /* what you sent */ },
  "started_at": "2026-05-08T15:00:00Z",
  "ended_at": null,
  "error_message": null
}
```

**Response 200 (completed):**
```json
{
  "status": "completed",
  "best_metric_value": 0.124,
  "ended_at": "2026-05-08T15:08:42Z",
  "...": "..."
}
```

**Response 200 (failed):**
```json
{
  "status": "failed",
  "error_message": "CUDA out of memory ... try smaller batch size",
  "ended_at": "..."
}
```

### `GET /api/v1/trainings/{training_id}/mlflow-url`

**Response 200:**
```json
{
  "training_id": "tr-uuid-here",
  "mlflow_run_id": "abc123",
  "mlflow_url": "http://localhost:5000/#/experiments/1/runs/abc123"
}
```

> เปิด link ใน browser → ดู metrics curves, params, artifacts (เปิดได้บน VM ผ่าน `-L 5000:localhost:5000`)

### `GET /api/v1/trainings?project_id=<id>&status=running`

Filter list

### `DELETE /api/v1/trainings/{training_id}`

Cancel job (`SIGTERM` ไป Celery worker — idempotent ถ้าจบแล้วคืน status ปัจจุบัน)

---

## 10. WebSocket — Live Progress (optional แต่สนุก)

แทนที่จะ poll `GET /trainings/{id}` ทุก 5 วิ → subscribe WebSocket ดู progress real-time

URL จาก response: `ws://localhost:8000/ws/jobs/{job_id}`

ทดสอบใน browser DevTools → Console:
```javascript
const ws = new WebSocket("ws://localhost:8000/ws/jobs/celery-task-id-here");
ws.onmessage = (e) => console.log(JSON.parse(e.data));
```

หรือใช้ [websocat](https://github.com/vi/websocat):
```bash
websocat ws://localhost:8000/ws/jobs/celery-task-id-here
```

**Sample messages:**
```json
{ "type": "progress", "step": 10, "total_steps": 100, "loss": 1.23 }
{ "type": "progress", "step": 50, "total_steps": 100, "loss": 0.56 }
{ "type": "completed", "metric": 0.124 }
```

---

## 11. ค้นหาและ Export Model

### `GET /api/v1/models?training_job_id={training_id}`

**Response 200:**
```json
{
  "items": [
    {
      "id": "model-uuid",
      "training_job_id": "tr-uuid-here",
      "name": "policy-bot-v1",
      "base_model": "unsloth/Llama-3.2-3B-Instruct-bnb-4bit",
      "lora_adapter_uri": "s3://models/model-uuid/adapter.safetensors",
      "gguf_uri": null,
      "ollama_model_tag": null,
      "size_mb": 134.2
    }
  ],
  "total": 1, "limit": 50, "offset": 0
}
```

> 🔖 เก็บ `id` (model_id)

### `POST /api/v1/models/{model_id}/export`

**Request (GGUF — สำหรับ Ollama inference):**
```json
{ "format": "gguf", "quantization": "q4_k_m" }
```

**Quantization options:** `q4_k_m` (default, balance), `q5_k_m` (better quality), `q8_0` (best quality, larger)

**Request (SafeTensors — สำหรับ HuggingFace inference):**
```json
{ "format": "safetensors" }
```

**Response 202:**
```json
{
  "artifact_id": "model-uuid",
  "format": "gguf",
  "job_id": "export-celery-id",
  "status": "pending",
  "websocket_url": "ws://localhost:8000/ws/jobs/export-celery-id"
}
```

หลัง export เสร็จ:
- `GET /api/v1/models/{model_id}` → ฟิลด์ `gguf_uri` + `ollama_model_tag` จะถูกตั้ง
- `ollama_model_tag` คือ tag ที่ใช้ใน inference endpoint (§12)

### `GET /api/v1/models/{model_id}/download`

Stream binary file ของ GGUF / SafeTensors

---

## 12. Inference (chat กับโมเดลที่เทรน)

### `POST /api/v1/inference/chat/completions` (OpenAI-compatible)

**Request:**
```json
{
  "model": "policy-bot-v1:latest",
  "messages": [
    { "role": "system", "content": "You are a helpful customer service agent." },
    { "role": "user", "content": "Can I return sale items?" }
  ],
  "temperature": 0.7,
  "max_tokens": 256
}
```

> `model` = `ollama_model_tag` จาก §11

**Response 200:**
```json
{
  "id": "chatcmpl-abc",
  "object": "chat.completion",
  "created": 1715192400,
  "model": "policy-bot-v1:latest",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Sale items are final sale and cannot be returned."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": { "prompt_tokens": 28, "completion_tokens": 14, "total_tokens": 42 }
}
```

### `GET /api/v1/inference/models`

List Ollama models ที่มีบนเครื่อง — รวม base models และ fine-tuned models

**Response 200:**
```json
{
  "object": "list",
  "data": [
    { "id": "llama3.2:3b", "object": "model", "created": 0, "owned_by": "ollama" },
    { "id": "policy-bot-v1:latest", "object": "model", "created": 0, "owned_by": "ollama" }
  ]
}
```

### `POST /api/v1/inference/completions`
Legacy text-completion (ไม่ใช้ chat format) — มีไว้สำหรับ tools เก่าๆ

---

## 13. Evaluation (วัดผล + LLM Judge)

### `POST /api/v1/evaluations`

**Request:**
```json
{
  "model_artifact_id": "model-uuid",
  "dataset_id": "9b0e7c1f-...",
  "use_llm_judge": true,
  "judge_model": "anthropic/claude-3.5-sonnet"
}
```

> `use_llm_judge: true` → ส่ง output ของโมเดล vs ground truth ไปให้ Claude / GPT ตัดสิน  ต้องมี `OPENROUTER_API_KEY`
> `false` → ใช้ rule-based metrics เท่านั้น (BLEU / ROUGE / accuracy / F1 ตาม task_type)

**Response 202:**
```json
{
  "evaluation_id": "eval-uuid",
  "job_id": "eval-celery-id",
  "status": "pending",
  "websocket_url": "ws://localhost:8000/ws/jobs/eval-celery-id"
}
```

### `GET /api/v1/evaluations/{evaluation_id}` (poll)

**Response 200 (completed):**
```json
{
  "id": "eval-uuid",
  "status": "completed",
  "metrics_json": {
    "bleu": 0.62,
    "rouge_l": 0.71,
    "exact_match": 0.18
  },
  "llm_judge_score": 4.2,
  "llm_judge_model": "anthropic/claude-3.5-sonnet",
  "started_at": "...",
  "ended_at": "..."
}
```

> ค่า metric ขึ้นกับ task_type:
> - `qa` → BLEU, ROUGE-L, exact_match
> - `classification` → accuracy, F1 (per-class + macro)
> - `tool_calling` → name_match, params_match, exact_match (string)

### `POST /api/v1/evaluations/compare`

เทียบ N evaluation runs (เอาไว้ดู effect ของการเปลี่ยน hyperparameter)

**Request:**
```json
{ "evaluation_ids": ["eval-uuid-1", "eval-uuid-2", "eval-uuid-3"] }
```

**Response 200:**
```json
{
  "evaluation_ids": ["eval-uuid-1", "eval-uuid-2", "eval-uuid-3"],
  "metrics": {
    "bleu":     [0.62, 0.68, 0.71],
    "rouge_l":  [0.71, 0.74, 0.78]
  },
  "judge_scores": {
    "eval-uuid-1": 4.2, "eval-uuid-2": 4.5, "eval-uuid-3": 4.6
  }
}
```

---

## 14. Common Errors → Fixes

| Status | `code` | สาเหตุ | Fix |
|--------|--------|--------|-----|
| **422** | `validation_error` | request body ผิด schema | ดู `extra.errors[]` ระบุ field ที่ผิด — แก้แล้วลองใหม่ |
| **404** | `not_found` | ID ไม่มีใน DB / route ไม่มี | ตรวจ UUID ให้ถูก หรือเช็ค path |
| **405** | `http_405` | HTTP method ผิด (เช่น GET endpoint ที่รับแต่ POST) | เปลี่ยน method |
| **409** | `conflict` | resource state ขัด (เช่น train job already terminal) | refresh `GET /trainings/{id}` ดู status |
| **500** | `internal_error` | bug — มี `correlation_id` | ส่ง `correlation_id` ให้ดูใน `docker compose logs api` |
| **502** | `bad_gateway` | OpenRouter / Ollama down หรือ key ผิด | ตรวจ `.env`, restart api/worker |

ทุก error response มี shape เดียวกัน:
```json
{
  "detail": "human-readable description",
  "code": "stable_string_for_frontend",
  "extra": null
}
```

---

## 15. Tips การใช้ Swagger UI

1. **Try it out → Execute** — ปุ่มทุก endpoint, fill body จาก example แล้วยิงได้เลย
2. **Copy `id` ทันทีหลัง POST** — Swagger ไม่ remember IDs ระหว่าง endpoints — ใช้ scratch text file ข้างๆ
3. **Authorize ปุ่มมุมขวาบน** — **ไม่ต้องกด** (PoC ไม่มี auth)
4. **Schemas section ด้านล่างหน้า** — collapse/expand เพื่อดู JSON shape เต็มของ Pydantic model
5. **ทดสอบทีละ scenario แยก** — สร้าง 1 project ต่อ task type ไม่ปนกัน (เช่น `qa-test`, `cls-test`, `tools-test`)
6. **เลข sample เริ่มน้อย** — `num_samples: 20`, `num_train_epochs: 1` ตอน smoke; อัปขึ้นเมื่อ confirm ว่า pipeline ทำงาน
7. **Network tab ของ DevTools** — ดู request/response raw เห็น body จริงที่ Swagger ส่ง

---

## 16. Quick Smoke Test (5 นาที, no OpenRouter)

ทดสอบ end-to-end เร็ว ๆ ด้วย Path A (upload seed) + manual training 1 epoch:

```
1. GET  /health                                            → 200
2. POST /api/v1/projects                                   → save project_id
   { "name":"smoke", "task_type":"qa" }
3. POST /api/v1/datasets/upload-seed (multipart)           → save dataset_id
   project_id, task_type=qa, file=seed.jsonl (5 rows)
4. GET  /api/v1/datasets/{dataset_id}/preview?limit=3      → ดูว่าข้อมูลถูก
5. POST /api/v1/trainings                                  → save training_id
   { "mode":"manual", "project_id":..., "dataset_id":...,
     "manual_config": { "num_train_epochs":1, "per_device_train_batch_size":1 } }
6. GET  /api/v1/trainings/{training_id}    (poll ~5 min)   → status: completed
7. GET  /api/v1/models?training_job_id={training_id}       → save model_id
8. POST /api/v1/models/{model_id}/export                   → export job (GGUF)
   { "format":"gguf", "quantization":"q4_k_m" }
9. POST /api/v1/inference/chat/completions                 → generated answer
   { "model":"smoke:latest", "messages":[{"role":"user","content":"What is the return window?"}] }
```

ผ่านครบ 9 ข้อ = pipeline สมบูรณ์ พร้อมใช้งานจริง 🎉
