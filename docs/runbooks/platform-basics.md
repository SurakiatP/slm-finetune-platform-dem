# Platform Basics Runbook — Health, Metadata, Project + Dataset CRUD

> **Goal:** ทดสอบ endpoint พื้นฐานที่ runbook อื่น ๆ assume ว่าผ่าน — health check, static metadata, project lifecycle, dataset preview/list/delete
> **Estimated time:** 10 นาที
> **Cost:** $0 (ไม่เรียก LLM)
> **Prerequisites:** Stack รันอยู่ (ไม่จำเป็นต้องมี `OPENROUTER_API_KEY`)

---

## 0. Pre-flight checklist

### 0.1 Stack ขึ้น

```bash
ssh -p <vast-port> root@<vast-ip> "docker compose ps && curl -sf http://localhost:8000/health"
```

ต้องเห็น 7 containers up + `{"status":"ok"}`:
- `slm-postgres` (healthy), `slm-redis` (healthy), `slm-minio` (healthy)
- `slm-mlflow` (healthy), `slm-api`, `slm-worker`, `slm-ollama`

### 0.2 SSH port forwards

```powershell
ssh -p <vast-port> root@<vast-ip> `
  -L 8000:localhost:8000 `
  -L 9001:localhost:9001
```

### 0.3 Tabs ที่ต้องเปิด

| URL | จุดประสงค์ |
|-----|-----------|
| http://localhost:8000/docs | Swagger UI (หลัก) |
| http://localhost:9001 | MinIO console (`minioadmin`/`minioadmin`) — verify object paths |

---

## Task 1 — `GET /health`

**วัตถุประสงค์:** confirm process alive + DB/Redis pingable

### Steps

1. ใน Swagger เปิด `GET /health`
2. **Try it out** → **Execute**

### ✅ Expected response (200)

```json
{ "status": "ok" }
```

### 🧪 Verification

| Check | Expected |
|-------|----------|
| HTTP status | 200 |
| Response body | exactly `{"status":"ok"}` (ไม่มี extra fields) |

---

## Task 2 — `GET /api/v1/tasks` — Task type metadata

**วัตถุประสงค์:** ดู task types ที่ระบบรองรับ + Pydantic JSON schema ของแต่ละ task

### Steps

1. เปิด `GET /api/v1/tasks` → **Execute**

### ✅ Expected response (200)

```json
{
  "items": [
    {
      "task_type": "classification",
      "description": "...",
      "sample_schema": { "type": "object", "properties": {...} },
      "example": { "text": "...", "label": "..." }
    },
    {
      "task_type": "tool_calling",
      "...": "..."
    },
    {
      "task_type": "qa",
      "...": "..."
    }
  ],
  "total": 3
}
```

### 🧪 Verification

| Check | Expected |
|-------|----------|
| `total` | exactly 3 |
| Task types | `classification`, `tool_calling`, `qa` (ไม่มีอื่น) |
| Each item has | `sample_schema` + `example` |

---

## Task 3 — `GET /api/v1/base-models` — Allowed base models

**วัตถุประสงค์:** ดู 4-bit Unsloth models ที่ training endpoint จะรับ

### Steps

1. เปิด `GET /api/v1/base-models` → **Execute**

### ✅ Expected response (200)

```json
{
  "items": [
    {
      "model_id": "unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
      "family": "llama-3.2",
      "parameters_billion": 1.0,
      "...": "..."
    },
    "...": "5 รายการเพิ่มเติม"
  ],
  "total": 6
}
```

### 🧪 Verification

| Check | Expected |
|-------|----------|
| `total` | 6 (Llama 3.2 1B/3B, Qwen 2.5 0.5B/1.5B/3B, Gemma 2 2B) |
| ทุก `model_id` | ขึ้นต้น `unsloth/` + ลงท้าย `-bnb-4bit` |
| `parameters_billion` | ≤ 3.0 ทุก row (hard constraint per CLAUDE.md) |

🔖 **เก็บ `model_id` ตัวที่จะใช้** เช่น `unsloth/Llama-3.2-1B-Instruct-bnb-4bit` (เร็วสุด, smoke-friendly)

---

## Task 4 — `POST /api/v1/projects` — Create project

**วัตถุประสงค์:** สร้าง project สำหรับ runbook นี้

### Steps

1. เปิด `POST /api/v1/projects` → **Try it out** → กรอก:

```json
{
  "name": "platform-basics-test",
  "description": "Runbook: platform basics smoke",
  "task_type": "qa"
}
```

2. **Execute**

### ✅ Expected response (201)

```json
{
  "id": "<UUID>",
  "name": "platform-basics-test",
  "description": "Runbook: platform basics smoke",
  "task_type": "qa",
  "created_at": "2026-..."
}
```

🔖 **เก็บ `id` → `<project_id>` ใช้ตลอด runbook นี้**

### 🧪 Verification

| Check | Expected |
|-------|----------|
| HTTP status | 201 |
| Response มี `id` (UUID) | ✓ |

---

## Task 5 — `GET /api/v1/projects` — List

**วัตถุประสงค์:** ตรวจ project เพิ่งสร้างปรากฏใน list + pagination

### Steps

```
GET /api/v1/projects?limit=10&offset=0
```

### ✅ Expected response (200)

```json
{
  "items": [
    { "id": "<project_id>", "name": "platform-basics-test", "task_type": "qa", "..." },
    "..."
  ],
  "total": ≥ 1,
  "limit": 10,
  "offset": 0
}
```

### 🧪 Verification

| Check | Expected |
|-------|----------|
| Found `<project_id>` | ✓ |
| `total ≥ 1` | ✓ |

---

## Task 6 — `GET /api/v1/projects/{id}` — Get one

**วัตถุประสงค์:** ดึง project ตาม id

### Steps

```
GET /api/v1/projects/<project_id>
```

### ✅ Expected response (200)

```json
{ "id": "<project_id>", "name": "platform-basics-test", "task_type": "qa", "..." }
```

### 🧪 Verification

| Check | Expected |
|-------|----------|
| `id` ตรงกับ path | ✓ |

---

## Task 7 — Negative: GET nonexistent project → 404

**วัตถุประสงค์:** ระบบคืน 404 พร้อม error envelope ที่ถูกต้อง

### Steps

```
GET /api/v1/projects/00000000-0000-0000-0000-000000000000
```

### ✅ Expected response (404)

```json
{
  "detail": "Project ... not found",
  "code": "not_found",
  "extra": null
}
```

### 🧪 Verification

| Check | Expected |
|-------|----------|
| HTTP status | 404 |
| `code` | `"not_found"` |
| Response shape | มี `detail` + `code` + `extra` (envelope จาก `api/core/exceptions.py`) |

---

## Task 8 — Upload tiny dataset (สำหรับ Task 9-11)

**วัตถุประสงค์:** สร้าง dataset เล็ก ๆ สำหรับทดสอบ preview/list/delete

### Steps

สร้างไฟล์ `tiny.jsonl` (3 บรรทัด):

```jsonl
{"question": "What is 2+2?", "answer": "4"}
{"question": "What is the capital of Thailand?", "answer": "Bangkok"}
{"question": "Sky color?", "answer": "Blue"}
```

`POST /api/v1/datasets/upload-seed` (multipart):
- `project_id` = `<project_id>`
- `task_type` = `qa`
- `name` = `tiny-qa-seed`
- `file` = `tiny.jsonl`

### ✅ Expected response (201)

```json
{
  "dataset_id": "<UUID>",
  "task_type": "qa",
  "num_samples": 3,
  "invalid_rows": [],
  "format_detection": { "ran": <true|false>, "..." : "..." },
  "pdf_uri": null
}
```

🔖 **เก็บ `dataset_id` → `<dataset_id>`**

### 🧪 Verification

| Check | Expected |
|-------|----------|
| `num_samples` | 3 |
| `invalid_rows` | `[]` |

---

## Task 9 — `GET /api/v1/datasets/{id}/preview` — Preview rows

**วัตถุประสงค์:** ดู rows ที่ upload ไปแบบ paginated preview

### Steps

```
GET /api/v1/datasets/<dataset_id>/preview?limit=2
```

### ✅ Expected response (200)

```json
{
  "dataset_id": "<dataset_id>",
  "task_type": "qa",
  "samples": [
    { "question": "What is 2+2?", "answer": "4" },
    { "question": "What is the capital of Thailand?", "answer": "Bangkok" }
  ],
  "total": 3
}
```

### 🧪 Verification

| Check | Expected |
|-------|----------|
| `len(samples)` | 2 (= limit) |
| `total` | 3 (= num_samples) |
| ทุก sample มี | `question` + `answer` |

> 💡 Note: response key คือ `samples` ไม่ใช่ `items` — ต่างจาก list endpoints อื่น

---

## Task 10 — `GET /api/v1/datasets?project_id=...` — List

**วัตถุประสงค์:** filter datasets ตาม project

### Steps

```
GET /api/v1/datasets?project_id=<project_id>&limit=10
```

### ✅ Expected response (200)

```json
{
  "items": [
    { "id": "<dataset_id>", "name": "tiny-qa-seed", "source": "seed", "num_samples": 3, "..." }
  ],
  "total": 1,
  "limit": 10,
  "offset": 0
}
```

### 🧪 Verification

| Check | Expected |
|-------|----------|
| Found `<dataset_id>` | ✓ |
| `source` | `"seed"` (uploaded ด้วยมือ ไม่ใช่ SDG generate) |

---

## Task 11 — `DELETE /api/v1/datasets/{id}` — Delete

**วัตถุประสงค์:** ลบ dataset (ใช้ได้ตอนยังไม่มี training/eval ที่ refer)

### Steps

```
DELETE /api/v1/datasets/<dataset_id>
```

### ✅ Expected response (204)

(ไม่มี body)

### 🧪 Verification

| Check | Where | Expected |
|-------|-------|----------|
| HTTP status | response | 204 |
| File MinIO | http://localhost:9001 → bucket `datasets` → folder `seeds/` | `<dataset_id>.jsonl` หายไป |
| List datasets | `GET /api/v1/datasets?project_id=<project_id>` | `total: 0` |

---

## Task 12 — Negative: DELETE dataset ที่ถูกอ้างถึงโดย training → 409

**วัตถุประสงค์:** ระบบป้องกันลบ dataset ที่มี training/evaluation อ้าง (per Bug B1 fix Session 13)

### Steps

1. Upload seed ใหม่ (เพราะเพิ่งลบไป) → `<seed2_id>`
2. POST training อ้าง `<seed2_id>` (ดูตอน manual lifecycle runbook ละเอียด หรือใช้ payload สั้น ๆ ตรงนี้):

```json
POST /api/v1/trainings
{
  "mode": "manual",
  "project_id": "<project_id>",
  "dataset_id": "<seed2_id>",
  "base_model": "unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
  "manual_config": { "num_train_epochs": 1, "per_device_train_batch_size": 1 }
}
→ 202
```

3. **ทันที** ลอง `DELETE /api/v1/datasets/<seed2_id>`

### ✅ Expected response (409)

```json
{
  "detail": "Dataset ... has 1 referencing training/evaluation row(s); cannot delete",
  "code": "conflict"
}
```

### 🧪 Verification

| Check | Expected |
|-------|----------|
| HTTP status | 409 (NOT 500!) |
| `code` | `"conflict"` |
| Dataset ยังอยู่ | `GET /api/v1/datasets/<seed2_id>` → 200 |

🧹 **Cleanup:** หลังจบ DELETE training (`DELETE /api/v1/trainings/<training_id>`) แล้วค่อยลบ dataset ตอนหลัง

---

## Task 13 — `DELETE /api/v1/projects/{id}` — Cleanup

**วัตถุประสงค์:** ลบ project + cascade ทุกของในนั้น

### Steps

(หลัง cleanup Task 12) `DELETE /api/v1/projects/<project_id>`

### ✅ Expected response (204)

### 🧪 Verification

| Check | Expected |
|-------|----------|
| HTTP status | 204 |
| `GET /api/v1/projects/<project_id>` | 404 |

---

## ✅ Test Completion Checklist

- [ ] Task 1 — `/health` → `{"status":"ok"}`
- [ ] Task 2 — `/api/v1/tasks` → 3 task types
- [ ] Task 3 — `/api/v1/base-models` → 6 models, ทุกตัว ≤ 3B + bnb-4bit
- [ ] Task 4 — POST project → 201 + UUID
- [ ] Task 5 — list projects → เห็นที่เพิ่งสร้าง
- [ ] Task 6 — GET project by id → ตรงกัน
- [ ] Task 7 — GET nonexistent → 404 + envelope shape ถูก
- [ ] Task 8 — upload tiny seed → 201 + 3 samples
- [ ] Task 9 — preview → response key = `samples`, ไม่ใช่ `items`
- [ ] Task 10 — list datasets ตาม project → filter ทำงาน
- [ ] Task 11 — DELETE dataset → 204 + MinIO file หาย
- [ ] Task 12 — DELETE dataset ที่มี training อ้าง → 409 (NOT 500)
- [ ] Task 13 — DELETE project cascade → 204

ผ่านครบ 13 ข้อ = endpoint พื้นฐานพร้อมไปต่อ runbook อื่น ✅

---

## Troubleshooting

| อาการ | สาเหตุที่เป็นไปได้ | แก้ |
|-------|------------------|-----|
| `/health` 502 | uvicorn ตาย | `docker compose restart api` + `docker compose logs api` |
| Task 7 คืน 500 ไม่ใช่ 404 | exception handler ตาย / `correlation_id` มี | `docker compose logs api \| grep correlation_id` |
| Task 9 response key ไม่ใช่ `samples` | ระบบเปลี่ยน schema → ทดสอบ regression | check `api/schemas/datasets.py` ล่าสุด |
| Task 11 DELETE 500 (Bug B1 ที่เคยเจอ) | pre-check ไม่ได้รัน → DB FK constraint โผล่ | regression — แจ้ง dev (commit `c117f2a` ควรครอบคลุม) |
| Task 12 ลบสำเร็จไม่ใช่ 409 | guard ของ Phase 9 หาย | regression check `tests/integration/test_dataset_delete.py` |

---

## Cost Estimate

| Task | LLM ที่ใช้ | API calls | ค่าประมาณ |
|------|-----------|-----------|----------|
| Task 1-13 | (none) | 0 | $0 |

ทั้ง runbook ฟรี — ใช้ทดสอบ smoke ของ infra หลัง redeploy ได้ทุกครั้ง
