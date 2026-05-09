# SDG Manual Test Runbook — Classification (Thai)

> **Goal:** ทดสอบ Phase 9 SDG pipeline ครบทุก path สำหรับ task type `classification` (เนื้อหาภาษาไทย)
> **Estimated time:** 20-25 นาที + ~$0.30 OpenRouter cost
> **Prerequisites:** Stack รันอยู่ + `OPENROUTER_API_KEY` ตั้งใน `.env`
> **Seed files:** `seed_data/classification/classification_*.{json,jsonl}`

---

## 0. Pre-flight checklist

### 0.1 Stack ขึ้น

```bash
ssh -p 51030 root@202.215.2.218 "docker compose ps && curl -sf http://localhost:8000/health"
```

ต้องเห็น:
- `slm-postgres`, `slm-redis`, `slm-minio` Up (healthy)
- `{"status":"ok"}`

### 0.2 SSH port forwards (terminal Windows ของคุณ)

```powershell
ssh -p 51030 root@202.215.2.218 `
  -L 8000:localhost:8000 `
  -L 9001:localhost:9001 `
  -L 5432:localhost:5432
```

### 0.3 Tabs ที่ต้องเปิด

| URL | จุดประสงค์ |
|-----|-----------|
| http://localhost:8000/docs | Swagger UI (ทดสอบหลัก) |
| http://localhost:9001 | MinIO console (`minioadmin`/`minioadmin`) — verify uploads |
| Terminal #2: `ssh -p 51030 root@202.215.2.218 "tail -f /tmp/celery.log"` | ดู SDG progress real-time |
| Terminal #3 (DBeaver / psql): `localhost:5432`, user=slm, pwd=slm, db=slm | ตรวจ datasets table |

### 0.4 Create project

ใน Swagger:

```
POST /api/v1/projects
{
  "name": "sdg-test-cls",
  "description": "Phase 9 classification SDG test",
  "task_type": "classification"
}
→ 201
```

🔖 **เก็บ `id` → ตัวแปร `<cls_project_id>` ใช้ตลอด runbook นี้**

---

## Task 1 — Upload canonical seed (.jsonl) → ไม่เรียก LLM

**วัตถุประสงค์:** ยืนยันว่าระบบ skip Format Detection LLM call เมื่อ key ตรง canonical อยู่แล้ว (cost saver per Q1.2)

### Steps

1. ใน Swagger เปิด `POST /api/v1/datasets/upload-seed`
2. กด **Try it out**
3. กรอก:
   - `project_id` = `<cls_project_id>`
   - `task_type` = `classification`
   - `name` = `seed-cls-canonical-jsonl`
   - `file` = upload `seed_data/classification/classification_canonical.jsonl`
4. **Execute**

### ✅ Expected response (201)

```json
{
  "dataset_id": "...",
  "task_type": "classification",
  "num_samples": 9,
  "invalid_rows": [],
  "format_detection": {
    "ran": false,                                              // ← key
    "model_used": null,
    "field_mapping": {},
    "rows_total": 9,
    "rows_canonicalised": 9,
    "rows_dropped": 0,
    "notes": "already canonical — Format Detection skipped"
  },
  "pdf_uri": null
}
```

🔖 **เก็บ `dataset_id` → `<seed_cls_canonical_jsonl_id>`**

### 🧪 Verification

| Check | Where | Expected |
|-------|-------|----------|
| File บน MinIO | http://localhost:9001 → bucket `datasets` → folder `seeds/` | `<seed_cls_canonical_jsonl_id>.jsonl` ขนาด ~2KB |
| Content canonical | คลิกไฟล์ → preview | ทุกแถวมี `text` + `label` (ไม่ใช่ `message`/`category`) |
| Dataset row | `GET /api/v1/datasets/{seed_cls_canonical_jsonl_id}` | `source: "seed"`, `num_samples: 9` |
| Preview | `GET /api/v1/datasets/{id}/preview?limit=3` | 3 rows ภาษาไทย, มี `text`/`label` |
| celery log | terminal #2 | (ไม่มี log ใหม่ — Format Detection ทำใน API process) |

---

## Task 2 — Upload canonical seed (.json) → identical behavior

**วัตถุประสงค์:** ยืนยันว่า JSON array (top-level) ทำงานเหมือน JSONL

### Steps

ทำซ้ำ Task 1 แต่:
- `name` = `seed-cls-canonical-json`
- `file` = `seed_data/classification/classification_canonical.json`

### ✅ Expected: เหมือน Task 1 (`format_detection.ran: false`, `num_samples: 9`)

🔖 **เก็บ → `<seed_cls_canonical_json_id>`**

> **Note:** API parse `[` ที่จุดเริ่มต้น → ตีความเป็น JSON array; line-by-line สำหรับ JSONL บนพฤติกรรมการ persist ไปที่ MinIO ไม่ต่างกัน — เก็บเป็น `.jsonl` เหมือนกัน

---

## Task 3 — Upload mismatched keys (.jsonl) → Format Detection ทำงาน

**วัตถุประสงค์:** ยืนยัน Format Detection LLM (gemini-2.5-flash-lite) auto-rename keys

### Steps

1. `POST /api/v1/datasets/upload-seed`
2. - `project_id` = `<cls_project_id>`
   - `task_type` = `classification`
   - `name` = `seed-cls-mismatched-jsonl`
   - `file` = `seed_data/classification/classification_mismatched.jsonl`
3. **Execute**

### ✅ Expected response (201) — สำคัญที่ `format_detection`

```json
{
  "dataset_id": "...",
  "task_type": "classification",
  "num_samples": 9,
  "invalid_rows": [],
  "format_detection": {
    "ran": true,                                                 // ← LLM ทำงาน
    "model_used": "google/gemini-2.5-flash-lite",                // ← model ที่ใช้
    "field_mapping": {                                           // ← rename map
      "message": "text",
      "category": "label"
    },
    "rows_total": 9,
    "rows_canonicalised": 9,
    "rows_dropped": 0,
    "notes": null
  },
  "pdf_uri": null
}
```

🔖 **เก็บ → `<seed_cls_mismatched_jsonl_id>`**

### 🧪 Verification

| Check | Where | Expected |
|-------|-------|----------|
| File บน MinIO ถูก rename | bucket `datasets/seeds/<id>.jsonl` → preview | **`text`/`label`** (canonical) — ไม่ใช่ `message`/`category` แล้ว |
| Preview API | `GET /api/v1/datasets/{id}/preview?limit=9` | ทุก row มี `text`/`label` |
| MetadataDB | `SELECT generation_metadata FROM datasets WHERE id = '<id>';` | `format_detection.field_mapping = {"message": "text", "category": "label"}` |

### ⚠️ ถ้า `ran: false` (ปัญหา)

- เช็ค: `tail /tmp/uvicorn.log | grep -i 'OPENROUTER\|format detect'`
- สาเหตุ: `OPENROUTER_API_KEY` ว่าง → fallback "passthrough mode"
- แก้: เติม key ใน `.env` แล้ว restart uvicorn

---

## Task 4 — Upload mismatched (.json) → identical to Task 3

ทำซ้ำ Task 3 แต่ใช้ `classification_mismatched.json` (top-level array)

🔖 **เก็บ → `<seed_cls_mismatched_json_id>`**

✅ Expected: identical → `format_detection.ran: true` + `field_mapping: {"message": "text", "category": "label"}`

---

## Task 5 — SDG generate `with_seed` → quality gates

**วัตถุประสงค์:** ทดสอบ Generator + Judge + MinHash dedup + 90/10 sentinel quota ทำงานครบ

### Steps

```
POST /api/v1/datasets/generate
{
  "sdg_mode": "with_seed",
  "project_id": "<cls_project_id>",
  "task_type": "classification",
  "task_description": "จำแนกประเภทคำร้องของลูกค้าเป็น 3 หมวดหมู่: ปัญหาการเงิน (เกี่ยวกับการชำระเงิน บัตรเครดิต ใบเสร็จ), ปัญหาเทคนิค (เกี่ยวกับการใช้แอป รหัสผ่าน ระบบขัดข้อง), คำถามทั่วไป (สอบถามข้อมูลทั่วไป สาขา การติดต่อ)",
  "num_samples": 20,
  "temperature": 0.8,
  "dataset_name": "sdg-cls-test-1",
  "seed_dataset_id": "<seed_cls_canonical_jsonl_id>"
}
```

### ✅ Immediate response (202)

```json
{
  "job_id": "...",
  "dataset_id": "...",
  "status": "pending",
  "websocket_url": "/ws/jobs/..."
}
```

🔖 **เก็บ → `<sdg_cls_dataset_id>`** + `<sdg_cls_job_id>`

### ⏳ Watch progress (terminal #2)

```bash
tail -f /tmp/celery.log
```

ควรเห็น sequence:

```
[INFO] SDG starting (Phase 9): job=... task=classification mode=with_seed target=20
[INFO] meta-prompter LLM call (gemini-3.1-flash-lite-preview) ...
[INFO] Generator chat_batch (qwen-235b, concurrency=100) ...
[INFO] Judge chat_batch (gpt-4o-mini, concurrency=100) ...
[INFO] MinHash filter ...
[INFO] SDG done: job=... samples=20 rejected=N1 dup=N2 judge_low=N3 calls=N4
```

### 🔁 Poll until complete (~2-3 นาที)

```
GET /api/v1/datasets/{sdg_cls_dataset_id}
```

ทำซ้ำทุก ~10 วิ จน `storage_uri` ติด

### ✅ Final state

```json
{
  "id": "<sdg_cls_dataset_id>",
  "num_samples": 20,
  "storage_uri": "s3://datasets/sdg/<id>.jsonl",
  "size_bytes": <some_size>,
  "generation_metadata": {
    "sdg_mode": "with_seed",
    "task_description": "...",
    "seed_dataset_id": "<seed_cls_canonical_jsonl_id>",
    "completed_at": "...",
    "rejected_count": >= 0,                  // schema reject
    "duplicate_count": >= 0,                 // MinHash near-dup reject
    "judge_rejected_count": > 0,             // ✅ Judge ทำงานแล้ว
    "judge_parse_failures": >= 0,
    "api_calls": > 30                        // ✅ มากกว่า 20 = Generator + Judge ทำงาน
  }
}
```

### 🧪 Quality gate verifications

#### A) Sentinel quota (10% target = ~2 rows ของ "unknown")

```
GET /api/v1/datasets/<sdg_cls_dataset_id>/preview?limit=20
```

นับ labels:
- `"ปัญหาการเงิน"` ~6 rows
- `"ปัญหาเทคนิค"` ~6 rows
- `"คำถามทั่วไป"` ~6 rows
- **`"unknown"` ~2 rows** ← Phase 9 sentinel injection

Row "unknown" ต้องเป็นข้อความ off-topic หรือ ambiguous (เช่น "วันนี้อากาศดีจัง", "เห็นด้วยครับ")

#### B) Judge gate ทำงาน

```sql
SELECT
  generation_metadata->>'judge_rejected_count' AS judge_rej,
  generation_metadata->>'duplicate_count' AS dup,
  generation_metadata->>'api_calls' AS api_calls
FROM datasets WHERE id = '<sdg_cls_dataset_id>';
```

Expected: `judge_rej > 0` (Judge reject อย่างน้อย 1 row) และ `api_calls` ~ 30-100

#### C) MinHash dedup

ดู `duplicate_count` ใน metadata — ค่ามากกว่า 0 = MinHash detect duplicates

ถ้า `0` ทุกครั้ง อาจเพราะ batch size เล็ก รอบแรก seeds ไม่เยอะพอจะชน

#### D) Closed label set (ไม่มี label นอก set)

```sql
-- ใช้ DBeaver / psql ก็ได้ — query JSONL บน MinIO ผ่าน duckdb-style ก็ได้
-- หรือ script Python อ่าน JSONL ดู labels
```

ทุก row ต้องมี label ใน `["ปัญหาการเงิน", "ปัญหาเทคนิค", "คำถามทั่วไป", "unknown"]` — ห้ามมี "อื่นๆ" หรือ label นอก set

---

## Task 6 — SDG generate `description_only` → ไม่มี seed, ใช้ classification_config

**วัตถุประสงค์:** ทดสอบ flow ที่ไม่ใช่ seed-based — ใช้ `labels` array แทน

### Steps

```
POST /api/v1/datasets/generate
{
  "sdg_mode": "description_only",
  "project_id": "<cls_project_id>",
  "task_type": "classification",
  "task_description": "จำแนกประเภทคำร้องของลูกค้าเป็น 3 หมวดหมู่: ปัญหาการเงิน, ปัญหาเทคนิค, คำถามทั่วไป",
  "num_samples": 15,
  "temperature": 0.9,
  "classification_config": {
    "labels": ["ปัญหาการเงิน", "ปัญหาเทคนิค", "คำถามทั่วไป"]
  }
}
```

### ✅ Expected: 202 → poll → completed

🔖 **เก็บ → `<sdg_cls_descr_id>`**

### 🧪 Verification

- เหมือน Task 5 — ทุก row ต้องมี label ใน `["ปัญหาการเงิน", "ปัญหาเทคนิค", "คำถามทั่วไป", "unknown"]`
- Generator ไม่มี seed → ใช้ task_description + meta-prompter rules อย่างเดียว
- Quality ของ rows อาจต่ำกว่า Task 5 เล็กน้อย (ไม่มี few-shot guidance)

---

## Task 7 — Negative: legacy `seed_data` → 422

**วัตถุประสงค์:** ยืนยัน Phase 9 ตัด field เก่าจริง

### Steps

```
POST /api/v1/datasets/generate
{
  "sdg_mode": "with_seed",
  "project_id": "<cls_project_id>",
  "task_type": "classification",
  "task_description": "test legacy contract — must reject",
  "num_samples": 5,
  "seed_data": [
    {"text": "test", "label": "ปัญหาการเงิน"}
  ]
}
```

### ✅ Expected: 422

```json
{
  "detail": [
    {
      "type": "extra_forbidden",
      "loc": ["body", "with_seed", "seed_data"],   // (or similar)
      "msg": "Extra inputs are not permitted",
      "input": [...]
    }
  ]
}
```

> Field `seed_data` อาจปรากฏใน `loc` หลายแบบ ตาม discriminator handling ของ Pydantic — สำคัญคือ status 422 + พูดถึง `seed_data`

---

## Task 8 — Negative: legacy `teacher_model` → 422

```
POST /api/v1/datasets/generate
{
  "sdg_mode": "description_only",
  "project_id": "<cls_project_id>",
  "task_type": "classification",
  "task_description": "test",
  "num_samples": 5,
  "classification_config": {"labels": ["a", "b"]},
  "teacher_model": "openai/gpt-4o-mini"
}
```

### ✅ Expected: 422 + `loc` มี `teacher_model`

---

## Task 9 — Negative: missing `seed_dataset_id` → 422

```
POST /api/v1/datasets/generate
{
  "sdg_mode": "with_seed",
  "project_id": "<cls_project_id>",
  "task_type": "classification",
  "task_description": "missing seed id",
  "num_samples": 5
}
```

### ✅ Expected: 422 + `loc` มี `seed_dataset_id` + `msg: "Field required"`

---

## Task 10 — Negative: nonexistent `seed_dataset_id` → 404

```
POST /api/v1/datasets/generate
{
  "sdg_mode": "with_seed",
  "project_id": "<cls_project_id>",
  "task_type": "classification",
  "task_description": "nonexistent seed",
  "num_samples": 5,
  "seed_dataset_id": "00000000-0000-0000-0000-000000000000"
}
```

### ✅ Expected: 404

```json
{
  "detail": "seed_dataset_id 00000000-... not found",
  "code": "not_found"
}
```

---

## Task 11 — Negative: cross-project seed → 400

**วัตถุประสงค์:** ใช้ seed ของ project อื่นมา generate ใน project นี้ → reject

### Steps

1. สร้าง project อื่น (e.g. `task_type: qa`)
2. Upload seed ใน project นั้น → `<other_seed_id>`
3. POST `/datasets/generate` ใน `<cls_project_id>` ที่ใช้ `seed_dataset_id: <other_seed_id>`

### ✅ Expected: 400 (task_type หรือ project_id ไม่ตรง)

```json
{
  "detail": "seed dataset task_type is qa but request asks for classification",
  "code": "bad_request"
}
```

---

## Task 12 — Negative: ใช้ SDG dataset เป็น seed_dataset_id → 400

**วัตถุประสงค์:** seed_dataset_id ต้องเป็น `source=seed` เท่านั้น (ไม่ใช่ `source=sdg`)

### Steps

ใช้ `<sdg_cls_dataset_id>` (จาก Task 5 — `source=sdg`) เป็น seed:

```
POST /api/v1/datasets/generate
{
  "sdg_mode": "with_seed",
  "project_id": "<cls_project_id>",
  "task_type": "classification",
  "task_description": "test seed source check",
  "num_samples": 5,
  "seed_dataset_id": "<sdg_cls_dataset_id>"
}
```

### ✅ Expected: 400

```json
{
  "detail": "Dataset ... has source=sdg; with_seed requires a dataset uploaded via /datasets/upload-seed",
  "code": "bad_request"
}
```

---

## Task 13 — Edge: invalid rows in seed file (drop & report)

**วัตถุประสงค์:** ทดสอบ behavior เมื่อ row บางส่วนผิด schema

### Steps

สร้างไฟล์ JSONL ที่มี row ผิดบางส่วน (e.g. ขาด `label`):

```jsonl
{"text": "test 1", "label": "ปัญหาการเงิน"}
{"text": "test 2"}
{"text": "test 3", "label": "ปัญหาเทคนิค"}
{"label": "no text"}
{"text": "test 5", "label": "คำถามทั่วไป"}
```

Upload → Expected: 201 + `invalid_rows: [1, 3]` + `num_samples: 3`

> ถ้า invalid > valid (ส่วนใหญ่ผิด) อาจคืน 400 — แต่ใน edge นี้ valid > invalid → 201 ปกติ

---

## ✅ Test Completion Checklist

หลังจากทำทุก Task ติ๊กให้ครบ:

- [ ] Task 1 — canonical .jsonl → `ran: false`
- [ ] Task 2 — canonical .json → `ran: false`
- [ ] Task 3 — mismatched .jsonl → `ran: true` + `field_mapping` ถูก
- [ ] Task 4 — mismatched .json → `ran: true` + `field_mapping` ถูก
- [ ] Task 5 — SDG with_seed → metadata มี `judge_rejected_count > 0`
- [ ] Task 5 — sentinel rows ~10% ของ target (label = "unknown")
- [ ] Task 5 — ทุก label อยู่ใน closed set (no nonsense)
- [ ] Task 6 — SDG description_only → completed
- [ ] Task 7 — legacy `seed_data` → 422
- [ ] Task 8 — legacy `teacher_model` → 422
- [ ] Task 9 — missing seed_dataset_id → 422
- [ ] Task 10 — nonexistent seed_dataset_id → 404
- [ ] Task 11 — cross-project seed → 400
- [ ] Task 12 — sdg dataset as seed → 400

ผ่านครบ 14 ข้อ = Classification SDG pipeline สมบูรณ์ Phase 9 ✅

---

## Troubleshooting

| อาการ | สาเหตุที่เป็นไปได้ | แก้ |
|-------|------------------|-----|
| Task 3-4 `ran: false` | `OPENROUTER_API_KEY` ว่าง / ผิด | เช็ค `.env` + `tail /tmp/uvicorn.log` หา warning |
| Task 5 `judge_rejected_count: 0` | LLM Judge ตอบ malformed JSON ทุก call (rare) หรือ batch fail | เช็ค `tail /tmp/celery.log` หา Judge error |
| Task 5 รัน > 5 นาที | num_samples ใหญ่ไป / OpenRouter rate-limit | ลด `num_samples` เป็น 10 / ดู celery log หา 429 |
| label นอก set ปรากฏ | Generator hallucinate (ผิดพลาด) | ปกติ Judge ควรกรองออก — ถ้าหลุด เพิ่ม seed examples ให้แต่ละ class |
| MinIO console เข้าไม่ได้ | port forward 9001 ไม่ติด | re-SSH ด้วย `-L 9001:localhost:9001` |
| Sentinel quota = 0 | LLM ไม่ generate `unknown` ตามคำสั่ง | เช็ค prompt ว่า `is_sentinel=True` ส่งไปจริง — ดู celery log |

---

## Cost Estimate

| Task | LLM ที่ใช้ | API calls | ค่าประมาณ |
|------|-----------|-----------|----------|
| Task 1-2 | (none) | 0 | $0 |
| Task 3-4 | gemini-2.5-flash-lite | 2 | <$0.001 |
| Task 5 | qwen-235b + gpt-4o-mini + gemini-flash-lite | ~50-100 | $0.05-0.10 |
| Task 6 | same | ~40-80 | $0.04-0.08 |
| Task 7-13 | (none — early reject) | 0 | $0 |

**Total estimated:** ~$0.10-0.20 ต่อ runbook
