# SDG Manual Test Runbook — QA (Thai + PDF)

> **Goal:** ทดสอบ Phase 9 SDG pipeline ครบทุก path สำหรับ task type `qa` — รวม PDF multimodal flow
> **Estimated time:** 25-30 นาที + ~$0.35 OpenRouter cost
> **Prerequisites:** Stack รันอยู่ + `OPENROUTER_API_KEY` ตั้งใน `.env`
> **Seed files:**
> - `seed_data/qa/qa_canonical.jsonl` / `.json`
> - `seed_data/qa/qa_mismatched.jsonl` / `.json`
> - `seed_data/qa/2503.14023v2.pdf` (สำหรับ Task 5+)

---

## 0. Pre-flight

ดูส่วน `0. Pre-flight checklist` ใน [`sdg-test-classification.md`](./sdg-test-classification.md) — ทำเหมือนกัน

### Create project

```
POST /api/v1/projects
{
  "name": "sdg-test-qa",
  "description": "Phase 9 QA SDG test (Thai + PDF)",
  "task_type": "qa"
}
→ 201
```

🔖 **เก็บ → `<qa_project_id>`**

---

## Task 1 — Upload canonical seed (.jsonl)

```
POST /api/v1/datasets/upload-seed
project_id:  <qa_project_id>
task_type:   qa
name:        seed-qa-canonical-jsonl
file:        seed_data/qa/qa_canonical.jsonl
```

### ✅ Expected (201)

> **Note:** `num_samples` reflects the **actual rows in the seed file**. Shipped `qa_canonical.jsonl` has **40 rows** (verified 2026-05-10).

```json
{
  "dataset_id": "...",
  "task_type": "qa",
  "num_samples": 40,
  "invalid_rows": [],
  "format_detection": {
    "ran": false,
    "field_mapping": {},
    "rows_total": 40,
    "rows_canonicalised": 40,
    "rows_dropped": 0,
    "notes": "already canonical — Format Detection skipped"
  },
  "pdf_uri": null
}
```

🔖 **เก็บ → `<seed_qa_canonical_jsonl_id>`**

---

## Task 2 — Upload canonical seed (.json)

ทำซ้ำ Task 1 แต่ใช้ `qa_canonical.json`

🔖 **เก็บ → `<seed_qa_canonical_json_id>`**

---

## Task 3 — Upload mismatched (.jsonl) → Format Detection

```
POST /api/v1/datasets/upload-seed
project_id:  <qa_project_id>
task_type:   qa
name:        seed-qa-mismatched-jsonl
file:        seed_data/qa/qa_mismatched.jsonl
```

### ✅ Expected (201)

```json
{
  "dataset_id": "...",
  "task_type": "qa",
  "num_samples": 40,
  "format_detection": {
    "ran": true,
    "model_used": "google/gemini-2.5-flash-lite",
    "field_mapping": {
      "prompt": "question",
      "response": "answer"
    },
    "rows_total": 40,
    "rows_canonicalised": 40,
    "rows_dropped": 0
  }
}
```

🔖 **เก็บ → `<seed_qa_mismatched_jsonl_id>`**

### 🧪 Verification

ใน MinIO console:
- bucket `datasets` → `seeds/<id>.jsonl` → preview
- ✅ ทุก row มี `question`/`answer` (rename จาก `prompt`/`response`) และ **เนื้อหาภาษาไทยยังครบถ้วน** (UTF-8 encoding ถูกต้อง)

---

## Task 4 — Upload mismatched (.json)

ทำซ้ำ Task 3 แต่ใช้ `qa_mismatched.json`

🔖 **เก็บ → `<seed_qa_mismatched_json_id>`**

---

## Task 5 — Upload PDF (qa-only flow)

**วัตถุประสงค์:** ทดสอบ PDF upload + persist + multimodal flow setup

### Steps

```
POST /api/v1/datasets/upload-seed
project_id:  <qa_project_id>
task_type:   qa
name:        seed-qa-pdf
file:        seed_data/qa/2503.14023v2.pdf
```

### ✅ Expected (201)

```json
{
  "dataset_id": "...",
  "task_type": "qa",
  "num_samples": 0,                                       // ✅ ไม่มี Q&A pairs ตอนนี้
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
  "pdf_uri": "s3://datasets/seed-pdfs/<dataset_id>.pdf"   // ✅ key สำคัญ
}
```

🔖 **เก็บ → `<seed_qa_pdf_id>`**

### 🧪 Verification

| Check | Where | Expected |
|-------|-------|----------|
| PDF object MinIO | http://localhost:9001 → bucket `datasets` → folder **`seed-pdfs/`** | มีไฟล์ `<seed_qa_pdf_id>.pdf` |
| File size | คลิกไฟล์ดู metadata | ใกล้เคียงกับขนาด PDF original |
| Dataset row | `GET /datasets/<seed_qa_pdf_id>` | `source: "seed"`, `num_samples: 0`, `storage_uri: null` |
| Metadata | check `generation_metadata` | มี `pdf_uri` + `pdf_pages` |
| DB query | `SELECT generation_metadata FROM datasets WHERE id = '<seed_qa_pdf_id>'` | `pdf_pages: <integer 1-100>` |

### Edge: PDF upload สำหรับ task อื่น (ห้าม)

```
POST /api/v1/datasets/upload-seed
project_id:  <some classification or tool_calling project>
task_type:   classification
file:        2503.14023v2.pdf
```

✅ Expected: **400 Bad Request**
```json
{
  "detail": "PDF uploads are supported only for task_type=qa (got classification)"
}
```

### Edge: PDF เกิน cap (ถ้าจะทดสอบ)

- File > 25 MiB → 413
- File > 100 หน้า → 413

(ของจริง 2503.14023v2.pdf น่าจะ < 25 MiB และ < 100 หน้า → ไม่ trigger limits)

---

## Task 6 — SDG generate `with_seed` (JSONL flow) → quality gates

**วัตถุประสงค์:** ทดสอบ Generator + Judge + MinHash dedup โดยใช้ JSONL seed (ไม่ใช่ PDF)

> **Note:** QA ไม่มี sentinel quota (ตามสเปก §9.2 — "No sentinel for QA")

### Steps

```
POST /api/v1/datasets/generate
{
  "sdg_mode": "with_seed",
  "project_id": "<qa_project_id>",
  "task_type": "qa",
  "task_description": "ตอบคำถามเกี่ยวกับนโยบายการคืนสินค้าของบริษัท X รวมถึงระยะเวลา เงื่อนไข ที่อยู่ส่งคืน และวิธีการขอคืนเงิน — ตอบเป็นภาษาไทยที่สุภาพและชัดเจน",
  "num_samples": 15,
  "temperature": 0.8,
  "dataset_name": "sdg-qa-test-1",
  "seed_dataset_id": "<seed_qa_canonical_jsonl_id>"
}
```

### ✅ Immediate response (202)

🔖 **เก็บ → `<sdg_qa_dataset_id>`**

### ⏳ Watch celery log

```
[INFO] SDG starting (Phase 9): task=qa mode=with_seed target=15
[INFO] meta-prompter LLM call ...
[INFO] (no PDF — using text-only Generator from start)
[INFO] Generator chat_batch (qwen-235b) ...
[INFO] Judge chat_batch (gpt-4o-mini) ...
[INFO] SDG done: samples=15 rejected=N1 dup=N2 judge_low=N3 calls=N4
```

### 🔁 Poll until complete (~2-3 minutes)

### ✅ Final state

```json
{
  "num_samples": 15,
  "storage_uri": "s3://datasets/sdg/<id>.jsonl",
  "generation_metadata": {
    "judge_rejected_count": > 0,
    "duplicate_count": >= 0,
    "api_calls": > 30
  }
}
```

### 🧪 Quality gate verifications

#### A) Content fidelity — ทุก row ตอบเรื่อง return policy

```
GET /datasets/<sdg_qa_dataset_id>/preview?limit=15
```

ทุก row ต้อง:
- เป็นภาษาไทย (ตามคำสั่งใน task_description)
- เกี่ยวกับ return policy (ไม่ off-topic)
- `answer` มีรายละเอียดสมเหตุสมผล (ไม่ใช่ "ไม่ทราบ" / "ไม่ตอบ")

ตัวอย่าง row ที่ดี:
```json
{
  "question": "หากซื้อสินค้าออนไลน์แล้วต้องการเปลี่ยนสี ทำได้ไหม?",
  "answer": "ลูกค้าสามารถเปลี่ยนสีของสินค้าได้ภายในระยะเวลา 30 วันนับจากวันที่ซื้อ โดยสินค้าต้องอยู่ในสภาพสมบูรณ์พร้อมบรรจุภัณฑ์เดิม"
}
```

#### B) No sentinel for QA

```
GET /datasets/<sdg_qa_dataset_id>/preview?limit=15
```

✅ ตรวจ: ไม่มี row ที่เป็น "off-topic" หรือ label `"unknown"` (QA ไม่มี sentinel)

#### C) Judge gate ทำงาน

```sql
SELECT
  generation_metadata->>'judge_rejected_count' AS judge_rej,
  generation_metadata->>'duplicate_count' AS dup,
  generation_metadata->>'api_calls' AS api_calls
FROM datasets WHERE id = '<sdg_qa_dataset_id>';
```

`judge_rej > 0` คาดว่าจะเห็น Judge reject อย่างน้อย 1-3 rows (ที่ Generator generate มาคุณภาพต่ำ)

#### D) MinHash dedup

ดู `duplicate_count` — ควรมีค่า ≥ 0 (อาจ 0 ถ้า Generator ไม่ผลิต near-dups)

#### E) Schema purity

ทุก row ต้องมีแค่ `question` + `answer` — ห้ามมี extra keys

---

## Task 7 — SDG generate ด้วย PDF seed → multimodal first pass

**วัตถุประสงค์:** ทดสอบ PDF→Q&A multimodal flow (รอบแรกใช้ Gemini multimodal, รอบหลังใช้ text-only)

### Steps

```
POST /api/v1/datasets/generate
{
  "sdg_mode": "with_seed",
  "project_id": "<qa_project_id>",
  "task_type": "qa",
  "task_description": "Answer questions about the content of the attached document — research paper on language model training",
  "num_samples": 10,
  "temperature": 0.7,
  "dataset_name": "sdg-qa-pdf-test",
  "seed_dataset_id": "<seed_qa_pdf_id>"
}
```

> **Note:** task_description เป็นภาษาอังกฤษเพราะ PDF (2503.14023v2.pdf) เป็น research paper — ปรับตามภาษาเนื้อหา PDF ของจริง

### ✅ Immediate response (202)

🔖 **เก็บ → `<sdg_qa_pdf_dataset_id>`**

### ⏳ Watch celery log carefully

```
[INFO] SDG starting (Phase 9): task=qa mode=with_seed target=10
[INFO] PDF first-pass: extracting Q&A from PDF via gemini-2.5-flash-lite
[INFO] PDF first-pass returned <N> pairs; using as in-context examples for loop
[INFO] meta-prompter LLM call ...
[INFO] Generator chat_batch (qwen-235b, text-only from now on) ...
[INFO] Judge chat_batch ...
[INFO] SDG done: samples=10 ...
```

### 🔁 Poll until complete (~3-5 นาที — PDF call ช้ากว่า)

### ✅ Final state

ใน metadata ดู:
- `api_calls` >= 1
  - **Common:** ถ้า PDF first-pass call เดียวคืน Q&A pairs ครบ `num_samples` แล้ว worker จะ short-circuit ไม่รัน Generator/Judge loop ต่อ → `api_calls = 1` (verified 2026-05-10 with 10-page paper, num_samples=10 → 10 pairs from one Gemini multimodal call)
  - **Otherwise:** `api_calls > 10` (1 PDF + N Generator + N Judge + 1 Meta) เมื่อ first-pass ไม่ครบ target
- `judge_rejected_count` >= 0 (= 0 เมื่อ short-circuit)

### 🧪 Quality verification

#### A) Q&A pairs grounded in document

```
GET /datasets/<sdg_qa_pdf_dataset_id>/preview?limit=10
```

ทุก row ต้อง:
- ถามเกี่ยวกับเนื้อหาใน PDF (ไม่ off-topic, ไม่ make up เนื้อหานอก document)
- คำตอบสามารถ verify จาก PDF original
- คำถามหลากหลายแบบ (factual, comparison, "why", procedural)

#### B) PDF object ยังอยู่ใน MinIO

```
http://localhost:9001 → bucket `datasets` → folder `seed-pdfs/`
```

✅ ไฟล์ `<seed_qa_pdf_id>.pdf` ยังอยู่ (ไม่ถูกลบหลัง SDG ใช้)

---

## Task 8 — Negative: legacy `seed_data` → 422

```
POST /api/v1/datasets/generate
{
  "sdg_mode": "with_seed",
  "project_id": "<qa_project_id>",
  "task_type": "qa",
  "task_description": "test legacy",
  "num_samples": 5,
  "seed_data": [{"question": "test", "answer": "test"}]
}
```

### ✅ Expected: 422

---

## Task 9 — Negative: legacy `teacher_model` → 422

```
POST /api/v1/datasets/generate
{
  "sdg_mode": "with_seed",
  "project_id": "<qa_project_id>",
  "task_type": "qa",
  "task_description": "test",
  "num_samples": 5,
  "seed_dataset_id": "<seed_qa_canonical_jsonl_id>",
  "teacher_model": "openai/gpt-4o-mini"
}
```

### ✅ Expected: 422

---

## Task 10 — Negative: PDF seed_dataset_id ใช้กับ task ผิด → 400

**วัตถุประสงค์:** PDF seed ต้องใช้ได้แค่ qa task

### Steps

1. สร้าง project ใหม่ `task_type: classification` → `<other_cls_pid>`
2. POST `/datasets/generate` ที่อยาก reuse PDF seed:

```
POST /api/v1/datasets/generate
{
  "sdg_mode": "with_seed",
  "project_id": "<other_cls_pid>",
  "task_type": "classification",
  "task_description": "trying to use PDF for classification",
  "num_samples": 5,
  "seed_dataset_id": "<seed_qa_pdf_id>"
}
```

### ✅ Expected: 400

```json
{
  "detail": "seed dataset belongs to project <qa_project_id> but request is for project <other_cls_pid>",
  "code": "bad_request"
}
```

> ❗ Note: error อาจ trigger ที่ project_id check ก่อน — ถ้า PDF seed อยู่ใน QA project และคุณส่ง task_type=classification ใน QA project ตัว task_type validation จะ trigger ก่อน เป็น "task_type mismatch"

---

## Task 11 — Negative: missing `seed_dataset_id` → 422

ดูเหมือน Task 9 ใน [`sdg-test-classification.md`](./sdg-test-classification.md)

---

## Task 12 — Edge: PDF too large / too many pages

**วัตถุประสงค์:** ทดสอบขีดจำกัด `MAX_SEED_PDF_BYTES = 25 MiB` + `MAX_SEED_PDF_PAGES = 100`

### Steps

ใช้ PDF ใหญ่ (> 25 MB) — ถ้ามี

```
POST /api/v1/datasets/upload-seed
project_id:  <qa_project_id>
task_type:   qa
file:        big.pdf  (> 25 MB)
```

### ✅ Expected: 413

```json
{
  "detail": "PDF size <bytes> exceeds cap 26214400 (25 MiB)"
}
```

> ถ้าไม่มี PDF ใหญ่ — ข้าม Task นี้

---

## Task 13 — Edge: PDF corrupt / non-PDF bytes

ลองส่งไฟล์ที่ไม่ใช่ PDF จริง — เช่น rename `seed.txt` เป็น `fake.pdf`:

```
POST /api/v1/datasets/upload-seed
file:  fake.pdf  (จริงๆ คือ text)
```

### ✅ Expected: 400

```json
{
  "detail": "failed to parse PDF: ..."
}
```

---

## ✅ Test Completion Checklist

- [ ] Task 1 — canonical .jsonl → `ran: false`, `num_samples: 8`
- [ ] Task 2 — canonical .json → identical
- [ ] Task 3 — mismatched .jsonl → `ran: true`, mapping `prompt→question, response→answer`
- [ ] Task 4 — mismatched .json → identical
- [ ] Task 5 — PDF upload → `pdf_uri` set, `num_samples: 0`, file ใน MinIO `seed-pdfs/`
- [ ] Task 6 — SDG with_seed (JSONL) → `judge_rejected_count > 0`, ทุก row ภาษาไทย, on-topic
- [ ] Task 7 — SDG with_seed (PDF) → multimodal first-pass log + Q&A grounded in document
- [ ] Task 8 — legacy `seed_data` → 422
- [ ] Task 9 — legacy `teacher_model` → 422
- [ ] Task 10 — cross-task PDF seed → 400
- [ ] Task 11 — missing seed_dataset_id → 422

ผ่านครบ 11 ข้อ = QA SDG pipeline (รวม PDF) สมบูรณ์ Phase 9 ✅

---

## Troubleshooting

| อาการ | สาเหตุ | แก้ |
|-------|--------|-----|
| Task 5 PDF upload 400 "PDF has zero pages" | PDF corrupt / encrypted | ลองเปิดใน reader ก่อน — ถ้าเปิดไม่ได้ก็ corrupt |
| Task 7 PDF first-pass timeout / fail | OpenRouter Gemini multimodal มี limit / API ดาวน์ | log fallback ไปที่ text-only Generator (PDF first-pass best-effort) |
| Task 7 Q&A pair off-topic จาก PDF | LLM ตีความ PDF ผิด หรือ task_description ไม่ชัด | refine task_description ให้ระบุ topic ของ PDF |
| Task 6 row ภาษาอื่น (ไม่ใช่ไทย) | task_description ไม่บอกชัดให้ใช้ไทย | เพิ่ม "ตอบเป็นภาษาไทยเท่านั้น" ใน task_description |
| `pdf_pages` ไม่ถูก set ใน metadata | ปกติพิมพ์โดย service หลัง probe — ถ้าไม่ติด มีบั๊ก | ดู `tail /tmp/uvicorn.log` |
| Delete dataset ไม่ลบ PDF ทิ้ง | dataset_service.delete_dataset() | best-effort cleanup — ถ้าไม่ลบ ใช้ MinIO console ลบเอง |

---

## Cost Estimate

| Task | LLM | Calls | ราคา |
|------|-----|-------|------|
| Task 1-2 | none | 0 | $0 |
| Task 3-4 | gemini-2.5-flash-lite | 2 | <$0.001 |
| Task 5 | none (PDF probe via pypdf, no LLM) | 0 | $0 |
| Task 6 | qwen-235b + gpt-4o-mini + gemini-flash-lite | ~40-80 | $0.04-0.08 |
| Task 7 | gemini-2.5-flash-lite (multimodal) + qwen-235b + gpt-4o-mini + gemini-flash-lite | ~30-60 + 1 PDF call | $0.10-0.15 |
| Task 8-13 | none | 0 | $0 |

**Total:** ~$0.15-0.25

> PDF multimodal call ราคาแพงกว่า text-only ~3-5x (per token) เพราะต้องประมวลผลทั้งเอกสาร — ใช้แค่ครั้งเดียว/job ก็พอ

---

## 🎯 Recommended order

1. **Task 1, 3** (5 นาที, ~$0.001) — verify Format Detection JSONL flow
2. **Task 5** (1 นาที, $0) — PDF upload + verify metadata
3. **Task 8-11** (3 นาที, $0) — negative tests batch
4. **Task 6** (3-5 นาที, $0.05) — SDG with JSONL seed
5. **Task 7** (5 นาที, $0.10-0.15) — SDG with PDF seed (last because expensive)
6. (optional) Task 2, 4 — JSON vs JSONL parity
7. (optional) Task 12, 13 — limit/error edge cases
