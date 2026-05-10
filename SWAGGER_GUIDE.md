# Swagger Test Guide — SLM Fine-Tuning Platform API

คู่มือ end-to-end สำหรับทดสอบ API 25 endpoints ผ่าน Swagger UI ที่ `/docs` — เรียงตาม **lifecycle จริง** (project → dataset → training → export → inference → eval) พร้อม request/response ตัวอย่างที่ใช้งานได้จริง

> ทุก request body ที่อยู่ในเอกสารนี้ผ่านการ verify กับ OpenAPI spec ของ deploy ปัจจุบัน — copy-paste ลง Swagger ได้เลย

---

## ⚠️ Phase 9 — SDG Hardening (สิ่งที่เปลี่ยนจาก Phase 4)

ถ้าเคยอ่าน guide เวอร์ชันเก่าหรือใช้ payload เก่า โปรดสังเกตการเปลี่ยนแปลงของ SDG flow:

| เดิม (Phase 4) | ใหม่ (Phase 9) |
|----------------|----------------|
| `POST /datasets/generate` รับ `seed_data: [...]` inline | **`seed_data` ถูกตัดออก** → ส่ง `seed_dataset_id: UUID` (ของ seed ที่ upload ไว้แล้ว) |
| `teacher_model: "..."` ใน request body override โมเดลได้ | **`teacher_model` ถูกตัดออก** — โมเดล LLM ทั้ง 5 ตัว hardcoded server-side |
| `upload-seed` รับ `.json/.jsonl` เท่านั้น | **`.pdf` รับได้สำหรับ task=qa** (multimodal Q&A extraction รอบแรก) |
| Response ของ upload-seed มี 4 ฟิลด์ | **เพิ่ม `format_detection` + `pdf_uri`** — audit ของ schema-mapping pass |
| SDG loop = validate + exact dedup | **เพิ่ม Judge gate (0.4·F + 0.3·N + 0.3·U ≥ 0.7) + MinHash LSH dedup (Jaccard 0.90) + 90/10 sentinel quota + adaptive over-gen** |
| `SDGProgress` มีแค่ 5 ตัวเลข | **เพิ่ม `current_loop`, `judge_rejected`, `judge_parse_failures`, `dedup_rejected`** + phases `format_detection` / `meta_prompting` / `judging` / `dedup` |

ส่ง `seed_data` หรือ `teacher_model` แบบเดิม → **422 Unprocessable Entity** (`extra="forbid"`)

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
[Setup]
GET  /health
GET  /api/v1/tasks                          → 3 task types
GET  /api/v1/tasks/{task_type}/example      → seed shape
GET  /api/v1/base-models                    → 1B / 3B options

[Project]
POST /api/v1/projects                       → save project_id
GET  /api/v1/projects
GET  /api/v1/projects/{id}

[Seed]                  ← Phase 9: upload เสมอ ก่อน SDG with_seed
POST /api/v1/datasets/upload-seed           → save seed_dataset_id
                                              (รับ .json/.jsonl, ของ qa รับ .pdf ได้)
                                              Response มี format_detection report

[Dataset]
POST /api/v1/datasets/generate              → save dataset_id
                                              with_seed → ส่ง seed_dataset_id
                                              description_only → ใส่ labels/tools
                                              **needs OPENROUTER_API_KEY**
GET  /api/v1/datasets/{id}                  (poll until storage_uri set)
GET  /api/v1/datasets/{id}/preview

[Training]
POST /api/v1/trainings                      (manual or HPO)
GET  /api/v1/trainings/{id}                 (poll until completed)
GET  /api/v1/trainings/{id}/mlflow-url

[Model]
GET  /api/v1/models?training_job_id={id}    → save artifact_id
POST /api/v1/models/{id}/export             → GGUF / SafeTensors

[Use]
POST /api/v1/inference/chat/completions
POST /api/v1/evaluations
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

## 6. สร้าง Dataset — Phase 9 flow (upload-seed → generate)

> Phase 9 บังคับให้ upload seed ก่อน → จึง generate (เปลี่ยนจาก Phase 4 ที่ inline `seed_data` ได้) ดู §1 ในตารางความต่างด้านบน

ขั้นตอนคือ:
1. **§6.1 — Upload seed dataset** (JSON / JSONL / PDF) → ได้ `seed_dataset_id`
2. **§6.2 — Submit SDG generation** ส่ง `seed_dataset_id` (with_seed) หรือ labels/tools (description_only)

---

### 6.1 Upload seed file — `POST /api/v1/datasets/upload-seed`

multipart form. รับ 3 รูปแบบ:

| ไฟล์ | task_type | คำอธิบาย |
|------|-----------|---------|
| `.jsonl` (1 row/บรรทัด) หรือ `.json` (top-level array) | qa / classification / tool_calling | format ปกติ — ถ้า key ไม่ตรง canonical ระบบจะเรียก **Format Detection LLM** auto-rename ให้ |
| `.pdf` | **qa เท่านั้น** | multimodal flow — เก็บ PDF ดิบไว้, รอบแรกของ SDG จะส่ง PDF เข้า Gemini multimodal สกัด Q&A pairs |

#### 6.1.a — JSONL canonical (key ตรงเลย, ไม่ต้องเรียก LLM)

ไฟล์ `seed.jsonl`:
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
  "invalid_rows": [],
  "format_detection": {
    "ran": false,
    "model_used": null,
    "field_mapping": {},
    "rows_total": 5,
    "rows_canonicalised": 5,
    "rows_dropped": 0,
    "notes": "already canonical — Format Detection skipped"
  },
  "pdf_uri": null
}
```

> 🔖 เก็บ `dataset_id` ใช้เป็น `seed_dataset_id` ใน §6.2

#### 6.1.b — JSONL key ไม่ตรง (Format Detection ทำงาน)

ไฟล์ `seed_messy.jsonl` (key เป็น `q`/`a` ไม่ตรง canonical `question`/`answer`):
```jsonl
{"q": "Return window?", "a": "30 days."}
{"q": "Need receipt?", "a": "Yes."}
{"q": "Sale items?", "a": "Final."}
{"q": "Refund?", "a": "5-7 days."}
{"q": "Where ship?", "a": "Returns Lane 123."}
```

**Response 201:**
```json
{
  "dataset_id": "f1c0a4ba-...",
  "task_type": "qa",
  "num_samples": 5,
  "invalid_rows": [],
  "format_detection": {
    "ran": true,
    "model_used": "google/gemini-2.5-flash-lite",
    "field_mapping": { "q": "question", "a": "answer" },
    "rows_total": 5,
    "rows_canonicalised": 5,
    "rows_dropped": 0,
    "notes": null
  },
  "pdf_uri": null
}
```

> 🧪 ตรวจสอบใน MinIO Console: `datasets/seeds/{dataset_id}.jsonl` ควรเป็น canonical แล้ว (key เป็น `question`/`answer`)
> 🔧 ถ้า `OPENROUTER_API_KEY` ว่าง — Format Detection จะ fallback "passthrough mode" + drop rows ที่ขาด required keys (notes จะบอก)

#### 6.1.c — PDF (qa only)

```
project_id:  <project_id>
task_type:   qa
file:        policy.pdf
```

**Response 201:**
```json
{
  "dataset_id": "f1c0a4ba-...",
  "task_type": "qa",
  "num_samples": 0,
  "invalid_rows": [],
  "format_detection": {
    "ran": false,
    "model_used": null,
    "field_mapping": {},
    "rows_total": 0,
    "rows_canonicalised": 0,
    "rows_dropped": 0,
    "notes": "PDF upload — Format Detection not applicable"
  },
  "pdf_uri": "s3://datasets/seed-pdfs/f1c0a4ba-.../seed.pdf"
}
```

**ข้อจำกัด PDF:** ≤25 MiB, ≤100 หน้า — เกินคืน 413

> `num_samples: 0` ถูกแล้ว — Q&A pairs จะมาจาก SDG generator (รอบแรกใช้ multimodal LLM อ่าน PDF)

#### 6.1.d — Errors ที่พบบ่อย

| Status | Reason |
|--------|--------|
| 400 | `task_type` ใน form ≠ project's task_type |
| 400 | upload `.pdf` แต่ task_type ≠ qa |
| 400 | parse JSON/JSONL ไม่ผ่าน |
| 413 | JSONL > 10 MiB หรือ PDF > 25 MiB / >100 หน้า |
| 422 | row schema ผิด (ทุก row reject → error) |

---

### 6.2 Submit SDG generation — `POST /api/v1/datasets/generate`

ต้องมี `OPENROUTER_API_KEY` ตั้งใน `.env` แล้วเสมอ (Generator + Judge LLM)

#### 6.2.a Mode `with_seed` (แนะนำ — quality สูงกว่า)

ใช้ `seed_dataset_id` จาก §6.1:

```json
{
  "sdg_mode": "with_seed",
  "project_id": "58e2064c-e2e2-4096-817e-38bf1abf7c87",
  "task_type": "qa",
  "task_description": "Answer questions about our return policy",
  "num_samples": 50,
  "temperature": 0.8,
  "dataset_name": "policy-sdg-v1",
  "seed_dataset_id": "f1c0a4ba-2d12-4001-b3c0-9e3b7e1a4f12"
}
```

**กฎ validation ของ `seed_dataset_id`:**
- ต้องมีอยู่ใน DB (404 ถ้าไม่มี)
- ต้องเป็น `source = seed` (400 ถ้าผิด)
- ต้อง `task_type` เดียวกับ request (400)
- ต้องอยู่ใน project เดียวกัน (400)
- ถ้าเป็น PDF seed (`pdf_uri` set) → request `task_type` ต้องเป็น qa (400)

#### 6.2.b Mode `description_only` (ไม่มี seed, ต้องใส่ task-specific config)

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

> Phase 9 จะ auto-inject `"unknown"` sentinel เพิ่ม 10% ของ target — sentinel นี้ใช้สำหรับ off-topic / out-of-scope inputs ทำให้ classifier ปรับ confidence ได้ดีขึ้น

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

> Phase 9 จะ auto-inject `"no_tool_needed"` sentinel tool — ใช้กรณีที่ input ไม่ตรงกับ tool ใด ๆ

**Response 202 (Accepted — job runs async):**
```json
{
  "job_id": "5e2bdc60-1fa1-4e1d-9b91-e7d2b34c7ed3",
  "dataset_id": "9b0e7c1f-b834-4df0-9c81-c4b6ec5c6e10",
  "status": "pending",
  "websocket_url": "ws://localhost:8000/ws/jobs/5e2bdc60-1fa1-4e1d-9b91-e7d2b34c7ed3"
}
```

> 🔖 เก็บ `dataset_id` (เป็นคนละตัวกับ `seed_dataset_id`)
> 🔖 เก็บ `websocket_url` ถ้าจะดู progress live (ดู §10)

#### 6.2.c Errors ที่พบบ่อยใน `/datasets/generate`

| Status | สาเหตุ | สิ่งที่ต้องแก้ |
|--------|--------|----------------|
| **422** | ใส่ `seed_data: [...]` (Phase 4 contract) | ตัด `seed_data` ออก → ใช้ `seed_dataset_id` แทน (upload seed ก่อน §6.1) |
| **422** | ใส่ `teacher_model: "..."` | ตัดทิ้ง — Phase 9 hardcode โมเดล server-side |
| **422** | with_seed mode แต่ไม่มี `seed_dataset_id` | ใส่ field |
| **404** | `seed_dataset_id` ไม่มีใน DB | upload seed ก่อน |
| **400** | seed task_type / project ไม่ตรง | upload ใหม่ในโปรเจกต์ที่ถูก |

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
  "source": "sdg",
  "num_samples": 0,
  "storage_uri": null,
  "size_bytes": null,
  "generation_metadata": {
    "sdg_mode": "with_seed",
    "task_description": "Answer questions about our return policy",
    "temperature": 0.8,
    "requested_samples": 50,
    "submitted_at": "2026-05-09T13:00:00Z",
    "seed_dataset_id": "f1c0a4ba-...",
    "celery_task_id": "5e2bdc60-..."
  },
  "created_at": "...",
  "updated_at": "..."
}
```

**Response 200 (เสร็จแล้ว — Phase 9 audit fields):**
```json
{
  "id": "9b0e7c1f-...",
  "num_samples": 50,
  "storage_uri": "s3://datasets/sdg/9b0e7c1f-....jsonl",
  "size_bytes": 12384,
  "generation_metadata": {
    "sdg_mode": "with_seed",
    "task_description": "...",
    "temperature": 0.8,
    "requested_samples": 50,
    "submitted_at": "2026-05-09T13:00:00Z",
    "seed_dataset_id": "f1c0a4ba-...",
    "celery_task_id": "5e2bdc60-...",
    "completed_at": "2026-05-09T13:04:32Z",
    "rejected_count": 8,
    "duplicate_count": 3,
    "judge_rejected_count": 12,
    "judge_parse_failures": 0,
    "api_calls": 87
  }
}
```

> Phase 9 audit fields:
> - `rejected_count` — schema/validation rejects (Pydantic + business rule failures)
> - `duplicate_count` — MinHash LSH near-duplicate rejects
> - `judge_rejected_count` — Judge weighted score < 0.7
> - `judge_parse_failures` — Judge response ไม่ parse เป็น JudgeScore
> - `api_calls` — รวม Generator + Judge + (Meta-prompter / PDF / Format Detection ถ้ามี)

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

**Sample training messages:**
```json
{ "type": "training_progress", "step": 10, "total_steps": 100, "loss": 1.23 }
{ "type": "training_progress", "step": 50, "total_steps": 100, "loss": 0.56 }
{ "type": "completed", "metric": 0.124 }
```

**Sample SDG messages (Phase 9 — มี phase markers + per-loop counters):**
```json
{ "type": "sdg_progress", "phase": "meta_prompting",
  "samples_generated": 0, "samples_target": 50,
  "current_loop": null }

{ "type": "sdg_progress", "phase": "judging",
  "samples_generated": 0, "samples_target": 50,
  "samples_rejected": 5, "duplicates_removed": 0,
  "current_loop": 0 }

{ "type": "sdg_progress", "phase": "generating",
  "samples_generated": 17, "samples_target": 50,
  "samples_rejected": 8, "duplicates_removed": 3,
  "current_loop": 0,
  "judge_rejected": 4, "judge_parse_failures": 0,
  "dedup_rejected": 3 }

{ "type": "sdg_progress", "phase": "persisting",
  "samples_generated": 50, "samples_target": 50,
  "samples_valid": 50 }

{ "type": "completed",
  "result": { "samples_generated": 50, "judge_rejected_count": 12,
              "duplicate_count": 3, "api_calls": 87, "storage_uri": "s3://datasets/sdg/..." } }
```

**Phase markers** (`phase` field):
- `format_detection` — emitted ถ้าระบบเรียก Format Detection (รอบนี้ไม่ค่อยเห็น เพราะรันที่ upload-seed time)
- `meta_prompting` — เรียก LLM ทำ diversity rules (1 ครั้ง/job, ตอน setup)
- `generating` — Generator + Judge + dedup loop body
- `judging` — emitted ก่อน Judge batch
- `dedup` — emitted ก่อน MinHash filter
- `persisting` — เขียน JSONL ขึ้น MinIO (final phase)

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

**Polling for completion** — `GET /api/v1/models/{model_id}` until one of:

- ✅ Success: `gguf_uri = "s3://models/exports/<id>/gguf"` (≈770 MB for a 1B model at q4_k_m) + `ollama_model_tag = "slm/<id8>:latest"` + `export_error_message = null`. Typical wall time ~70-90 s for a 1B model on a 12+ GB GPU (load base + merge + convert + quantize + blob upload).
- ❌ Failure: `export_error_message` populated with the actual error string (e.g. `"Unsloth: GGUF conversion failed: …"`). The artifact row is the canonical signal — `JobFailed` is also published to `job:{export_job_id}` on Redis pub/sub but no client subscribes after the WebSocket closes, so don't rely on the WS for failure detection.
- ⚠️ Partial: `gguf_uri` set but `ollama_model_tag` still null = upload to MinIO succeeded but the daemon registration failed (best-effort wrapper). The GGUF is usable for any tool that reads from MinIO, but the OpenAI-compatible inference router (§12) won't be able to resolve it until you re-export.

`ollama_model_tag` is the tag you pass to inference endpoints (§12). It's also accepted as the artifact UUID directly — either works.

### `GET /api/v1/models/{model_id}/download`

Stream binary file ของ GGUF / SafeTensors

---

## 12. Inference (chat กับโมเดลที่เทรน)

### `POST /api/v1/inference/chat/completions` (OpenAI-compatible)

**Request:**
```json
{
  "model": "65a05a2b-cdb0-4831-bea5-e86c093c3046",
  "messages": [
    { "role": "system", "content": "You are a helpful customer service agent." },
    { "role": "user", "content": "Can I return sale items?" }
  ],
  "temperature": 0.7,
  "max_tokens": 256
}
```

> `model` accepts either form:
> - **Artifact UUID** (recommended) — `model_artifact.id` from §11. The router looks up `ollama_model_tag` for you and 409s if the model wasn't exported yet, with a `POST /api/v1/models/{id}/export with format=gguf first` hint.
> - **Ollama tag** — `slm/<id8>:latest` or a base model the daemon already has (`llama3.2:3b`). Pass-through verbatim.
>
> Streaming (`"stream": true`) is intentionally rejected with 400 in this PoC — set it false.

**Response 200:**
```json
{
  "id": "chatcmpl-abc",
  "object": "chat.completion",
  "created": 1715192400,
  "model": "slm/65a05a2b",
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

> Ollama may include extra fields in the response (`system_fingerprint`, future OpenAI additions). The schema accepts and drops them; you'll only see the typed fields above.

### `GET /api/v1/inference/models`

List Ollama models ที่มีบนเครื่อง — รวม base models และ fine-tuned models. Each fine-tuned export shows up as `slm/<artifact_id8>:latest`.

**Response 200:**
```json
{
  "object": "list",
  "data": [
    { "id": "llama3.2:3b", "object": "model", "created": 0, "owned_by": "ollama" },
    { "id": "slm/65a05a2b:latest", "object": "model", "created": 0, "owned_by": "ollama" }
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
  "judge_model": "google/gemini-3.1-flash-lite-preview"
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
  "llm_judge_model": "google/gemini-3.1-flash-lite-preview",
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
| **422** | `validation_error` | **Phase 9:** ส่ง `seed_data: [...]` ไป `/datasets/generate` | ตัดออก → upload seed (§6.1) แล้วใช้ `seed_dataset_id` |
| **422** | `validation_error` | **Phase 9:** ส่ง `teacher_model: "..."` | ตัดทิ้ง — Phase 9 hardcode โมเดลแล้ว |
| **400** | `bad_request` | upload PDF แต่ task_type ≠ qa | ใช้ JSONL, หรือ project ใหม่ที่ task_type=qa |
| **400** | `bad_request` | seed_dataset_id อยู่ project อื่น / task_type ไม่ตรง | upload seed ใหม่ในโปรเจกต์ + task ที่ตรง |
| **404** | `not_found` | ID ไม่มีใน DB / route ไม่มี / **Phase 9:** seed_dataset_id ไม่มีอยู่ | ตรวจ UUID ให้ถูก หรือ upload seed ก่อน |
| **405** | `http_405` | HTTP method ผิด (เช่น GET endpoint ที่รับแต่ POST) | เปลี่ยน method |
| **409** | `conflict` | resource state ขัด (เช่น train job already terminal) / seed dataset ไม่มี JSONL หรือ PDF | refresh `GET /datasets/{id}` ดู state |
| **413** | `payload_too_large` | JSONL > 10 MiB หรือ PDF > 25 MiB / >100 หน้า | ลดขนาด หรือใช้ chunk |
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

## 16. Quick Smoke Test — Full Lifecycle (~5 นาที on a 12+ GB GPU, no OpenRouter)

ทดสอบ end-to-end เร็ว ๆ ด้วย upload-seed + manual training 1 epoch. Verified live on a fresh RTX 5000 Ada VM in Session 14 (2026-05-09): training 55 s, export 73 s, inference responded with the seed answer.

```
1. GET  /health                                            → 200 {"status":"ok"}
2. POST /api/v1/projects                                   → save project_id
   { "name":"smoke", "task_type":"qa" }
3. POST /api/v1/datasets/upload-seed (multipart)           → save dataset_id
   project_id, task_type=qa, file=seed.jsonl (5 rows)
   Response มี format_detection.ran=false (canonical seed)
4. GET  /api/v1/datasets/{dataset_id}/preview?limit=3      → ดูว่าข้อมูลถูก
5. POST /api/v1/trainings                                  → save training_id
   { "mode":"manual", "project_id":..., "dataset_id":...,
     "base_model":"unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
     "manual_config": { "num_train_epochs":1, "per_device_train_batch_size":1,
                         "gradient_accumulation_steps":1 } }
6. GET  /api/v1/trainings/{training_id}    (poll ~1 min)   → status: completed
7. GET  /api/v1/models?training_job_id={training_id}       → save artifact_id (items[0].id)
8. POST /api/v1/models/{artifact_id}/export                → export job (GGUF)
   { "format":"gguf", "quantization":"q4_k_m" }
   then GET /api/v1/models/{artifact_id} (poll ~1.5 min)   → gguf_uri set + ollama_model_tag set
                                                              + export_error_message null
9. POST /api/v1/inference/chat/completions                 → "Paris."  (or seed-derived answer)
   { "model":"<artifact_id>",                                ← UUID works directly, no tag needed
     "messages":[{"role":"user","content":"What is the capital of France? Answer in one word."}],
     "max_tokens":50, "temperature":0.0 }
```

> **seed.jsonl shape (one JSON object per line):**
> ```json
> {"question": "What is the capital of France?", "answer": "Paris"}
> {"question": "What is the capital of Germany?", "answer": "Berlin"}
> {"question": "What is the capital of Japan?", "answer": "Tokyo"}
> {"question": "What is the capital of Italy?", "answer": "Rome"}
> {"question": "What is the capital of Spain?", "answer": "Madrid"}
> ```

ผ่านครบ 9 ข้อ = pipeline สมบูรณ์ พร้อมใช้งานจริง 🎉

---

## 17. Phase 9 SDG Smoke Test — Quality Gates Verification (no GPU needed)

ทดสอบเฉพาะ SDG pipeline (ไม่ต้องเทรน) เพื่อยืนยัน Format Detection + Judge + MinHash + Sentinel quota ทำงาน ใช้ OPENROUTER_API_KEY แต่ใช้แค่ ~$0.10-0.20 ต่อ run

```
1. GET  /health                                            → 200

2. POST /api/v1/projects                                   → save project_id
   { "name":"sdg-phase9-smoke", "task_type":"qa" }

3a. POST /api/v1/datasets/upload-seed (canonical)          → save seed_id_a
    project_id, task_type=qa, file=seed.jsonl (canonical key)
    Response: format_detection.ran=false (skip LLM cost)

3b. POST /api/v1/datasets/upload-seed (mismatched keys)    → save seed_id_b
    project_id, task_type=qa, file=seed_messy.jsonl (key=q/a)
    Response: format_detection.ran=true,
              field_mapping={"q":"question","a":"answer"}

4. POST /api/v1/datasets/generate                          → save sdg_dataset_id
   { "sdg_mode":"with_seed", "project_id":..., "task_type":"qa",
     "task_description":"Answer policy questions",
     "num_samples":15, "seed_dataset_id":"<seed_id_a>" }
   Response: 202 + websocket_url

5. (ดู progress via WS หรือ poll)
   GET /api/v1/datasets/{sdg_dataset_id} (poll ~1-2 min)
   → storage_uri populated
   → generation_metadata.judge_rejected_count > 0 (Judge ทำงาน)
   → generation_metadata.duplicate_count >= 0 (MinHash ทำงาน)
   → generation_metadata.api_calls บอกจำนวน LLM ครั้ง

6. GET /api/v1/datasets/{sdg_dataset_id}/preview?limit=5
   → เห็น Q&A pairs ที่ Generator สร้าง

7. (Negative tests — ต้อง 422)
   POST /api/v1/datasets/generate with seed_data=[...]      → 422
   POST /api/v1/datasets/generate with teacher_model="..."  → 422

8. (Classification 90/10 sentinel quota)
   POST /api/v1/datasets/generate
   { "sdg_mode":"description_only", "project_id":<cls_project>,
     "task_type":"classification", "task_description":"...",
     "num_samples":20,
     "classification_config":{"labels":["billing","tech","general"]} }
   หลัง completion: GET /api/v1/datasets/{id}/preview?limit=20
   → ควรเห็นบางแถว label="unknown" (~10% = 2 แถว)

9. (Tool calling sentinel)
   POST /api/v1/datasets/generate (description_only + tools)
   หลัง completion: เห็นบางแถว answer มี name="no_tool_needed"
```

> **seed.jsonl (canonical):**
> ```json
> {"question": "Return window?", "answer": "30 days."}
> {"question": "Need receipt?", "answer": "Yes."}
> {"question": "Sale items?", "answer": "Final."}
> {"question": "Refund timing?", "answer": "5-7 days."}
> {"question": "Where to ship?", "answer": "Returns Lane 123."}
> ```

> **seed_messy.jsonl (Format Detection trigger):**
> ```json
> {"q": "Return window?", "a": "30 days."}
> {"q": "Need receipt?", "a": "Yes."}
> {"q": "Sale items?", "a": "Final."}
> {"q": "Refund timing?", "a": "5-7 days."}
> {"q": "Where to ship?", "a": "Returns Lane 123."}
> ```

ผ่านครบ 9 ข้อ = Phase 9 SDG quality stack ทำงานครบ 🎉

**ถ้า step 8 พัง:** `GET /api/v1/models/{artifact_id}` แล้วดู `export_error_message` — ทุก error path เขียนลง field นี้ ไม่ต้องไป tail worker logs (B7).
