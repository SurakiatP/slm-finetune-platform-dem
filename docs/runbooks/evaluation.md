# Evaluation Runbook — Rule-based Metrics + LLM Judge + Compare

> **Goal:** ทดสอบ Swagger §13 ครบ — rule-based metrics (BLEU/ROUGE/EM), LLM-as-judge (1-5 rubric → 0-1 normalized), N-way compare endpoint, negative cases
> **Estimated time:** 5-10 นาที (eval เร็ว ~5s/run; LLM judge เพิ่ม ~10-20s)
> **Cost:** $0 rule-based; ~$0.05-0.10 LLM judge (`google/gemini-3.1-flash-lite-preview`)
> **Prerequisites:** Stack รันอยู่ + มี trained artifact (จาก `training-manual-lifecycle.md` หรือ `training-hpo.md`) + `OPENROUTER_API_KEY` ตั้งใน `.env` (สำหรับ Task 5+)

---

## 0. Pre-flight checklist

### 0.1 Stack ขึ้น + Ollama รู้จัก artifact

```bash
ssh -p <vast-port> root@<vast-ip> "docker compose ps && docker compose exec -T ollama ollama list"
```

ต้องเห็น:
- 7 containers up
- อย่างน้อย 1 row `slm/<id8>:latest` ใน Ollama (artifact ที่ export GGUF + register แล้ว)

### 0.2 SSH port forwards

```powershell
ssh -p <vast-port> root@<vast-ip> -L 8000:localhost:8000 -L 9001:localhost:9001
```

### 0.3 Tabs ที่ต้องเปิด

| URL | จุดประสงค์ |
|-----|-----------|
| http://localhost:8000/docs | Swagger UI |
| http://localhost:9001 | MinIO console — verify (ไม่จำเป็นสำหรับ eval) |
| Terminal #2: `docker compose logs -f worker` | ดู eval task progress + judge LLM call logs |

### 0.4 ตั้ง `OPENROUTER_API_KEY` (สำหรับ Task 5+)

ถ้ายังไม่ตั้ง:

```bash
ssh -p <vast-port> root@<vast-ip>
nano ~/slm-platform/.env
# เพิ่ม / แก้ OPENROUTER_API_KEY=sk-or-v1-...
docker compose restart api worker
```

> 💡 Task 1-4 (rule-based) ทำได้โดยไม่ต้อง key. Task 5-7 (LLM judge) ต้องใช้

### 0.5 หา artifact + dataset

ถ้ายังไม่มี ใส่ลำดับนี้:
- `training-manual-lifecycle.md` Task 1-11 → ได้ `<artifact_id>` + `<dataset_id>` (5 QA capitals)

🔖 **เก็บ:** `<artifact_id>` + `<dataset_id>` (ใช้ตลอด runbook)

---

## Task 1 — Submit eval rule-based (no LLM judge)

**วัตถุประสงค์:** เริ่ม eval task พื้นฐาน — Ollama infer ทุก row, รวม BLEU/ROUGE/EM

### Steps

```
POST /api/v1/evaluations
{
  "model_artifact_id": "<artifact_id>",
  "dataset_id": "<dataset_id>",
  "use_llm_judge": false
}
```

### ✅ Expected response (202)

```json
{
  "evaluation_id": "<UUID>",
  "job_id": "<celery-task-uuid>",
  "status": "pending",
  "websocket_url": "/ws/jobs/<job_id>"
}
```

🔖 **เก็บ `evaluation_id` → `<eval1_id>` (Task 4 จะใช้ compare)**

### 🧪 Verification

| Check | Expected |
|-------|----------|
| HTTP status | 202 |
| `status` | `"pending"` |
| `websocket_url` | relative path `/ws/...` |

---

## Task 2 — Poll eval until completed (~5 วินาที)

**วัตถุประสงค์:** รอ eval เสร็จ (เร็วเพราะ ไม่มี LLM judge)

### Steps

```
GET /api/v1/evaluations/<eval1_id>
```

(ส่งซ้ำ ทุก 3-5 วินาที)

### ✅ Expected สุดท้าย (status=completed)

```json
{
  "id": "<eval1_id>",
  "model_artifact_id": "<artifact_id>",
  "dataset_id": "<dataset_id>",
  "celery_task_id": "<job_id>",
  "status": "completed",
  "metrics_json": {
    "exact_match": <float 0-1>,
    "rouge1": <float 0-1>,
    "rouge2": <float 0-1>,
    "rougeL": <float 0-1>,
    "bleu": <float 0-1>,
    "n": 5
  },
  "llm_judge_score": null,
  "llm_judge_model": null,
  "error_message": null,
  "started_at": "2026-...",
  "ended_at": "2026-..."
}
```

### 🧪 Verification

| Check | Expected |
|-------|----------|
| `status` | `completed` (ภายใน ~5-10s) |
| `metrics_json.n` | 5 (= dataset rows) |
| `metrics_json` keys | `exact_match`, `rouge1`, `rouge2`, `rougeL`, `bleu` (ครบทั้ง 5 + n) |
| `llm_judge_score` | `null` (Bug MT.B4 fix — ไม่ใช่ 0.0!) |
| `error_message` | `null` |

> 💡 ค่า EM ปกติเป็น 0.0 หรือต่ำเพราะโมเดลตอบ "Paris." แต่ ground truth = "Paris" (มีจุด/whitespace ต่าง). ROUGE/BLEU จะสูงกว่า

---

## Task 3 — Submit eval ครั้งที่ 2 (สำหรับ compare)

**วัตถุประสงค์:** สร้าง eval ตัวที่สอง เพื่อให้ `/compare` มีอย่างน้อย 2 inputs

### Steps

(เหมือน Task 1)

```
POST /api/v1/evaluations
{
  "model_artifact_id": "<artifact_id>",
  "dataset_id": "<dataset_id>",
  "use_llm_judge": false
}
```

🔖 **เก็บ `evaluation_id` → `<eval2_id>`**

(poll ทันที จนได้ `status: "completed"`)

### 🧪 Verification

| Check | Expected |
|-------|----------|
| `<eval2_id>` ≠ `<eval1_id>` | (UUIDs ต่างกัน) |
| Metrics value | ใกล้เคียงกัน (deterministic เพราะ temperature=0 ใน eval) |

---

## Task 4 — `POST /evaluations/compare` — N-way diff

**วัตถุประสงค์:** ทดสอบ compare endpoint ที่ pivot metrics เป็น `metric → {eval_id → value}`

### Steps

```
POST /api/v1/evaluations/compare
{
  "evaluation_ids": ["<eval1_id>", "<eval2_id>"]
}
```

### ✅ Expected response (200)

```json
{
  "evaluation_ids": ["<eval1_id>", "<eval2_id>"],
  "metrics": {
    "exact_match": { "<eval1_id>": 0.0, "<eval2_id>": 0.0 },
    "rouge1": { "<eval1_id>": 0.286, "<eval2_id>": 0.286 },
    "rouge2": { "<eval1_id>": 0.0, "<eval2_id>": 0.0 },
    "rougeL": { "<eval1_id>": 0.286, "<eval2_id>": 0.286 },
    "bleu": { "<eval1_id>": 0.020, "<eval2_id>": 0.020 },
    "n": { "<eval1_id>": 5, "<eval2_id>": 5 }
  },
  "judge_scores": {
    "<eval1_id>": null,
    "<eval2_id>": null
  }
}
```

### 🧪 Verification

| Check | Expected |
|-------|----------|
| `evaluation_ids` | array 2 ตัวที่ส่งไป |
| `metrics` | keys ตรงกับ `metrics_json` ของแต่ละ eval |
| Each `metrics[key]` | dict ของ `eval_id → value` (cross-product shape) |
| `judge_scores` | dict; ทั้งสองค่า = `null` (rule-based) |

---

## Task 5 — Submit eval **with** LLM judge

**วัตถุประสงค์:** เพิ่ม LLM-as-judge — Claude Haiku ให้คะแนน 1-5 ต่อ row

### Steps

```
POST /api/v1/evaluations
{
  "model_artifact_id": "<artifact_id>",
  "dataset_id": "<dataset_id>",
  "use_llm_judge": true,
  "judge_model": "google/gemini-3.1-flash-lite-preview"
}
```

### ✅ Expected response (202)

```json
{
  "evaluation_id": "<UUID>",
  "job_id": "<celery-task-uuid>",
  "status": "pending",
  "websocket_url": "/ws/jobs/<job_id>"
}
```

🔖 **เก็บ `evaluation_id` → `<eval_judge_id>`**

### 🧪 Verification

| Check | Expected |
|-------|----------|
| HTTP status | 202 |
| ทันทีใน worker log | เห็น `evaluation.run` task picked up |

---

## Task 6 — Poll judge eval until completed (~15-25 วินาที)

**วัตถุประสงค์:** รอ judge call ครบทุก row + คำนวณ mean

### Steps

(poll ทุก 5 วินาที)

```
GET /api/v1/evaluations/<eval_judge_id>
```

### ✅ Expected สุดท้าย (status=completed)

```json
{
  "id": "<eval_judge_id>",
  "status": "completed",
  "metrics_json": {
    "exact_match": 0.0,
    "rouge1": 0.286,
    "rouge2": 0.0,
    "rougeL": 0.286,
    "bleu": 0.020,
    "n": 5,
    "llm_judge_skipped_rows": 0
  },
  "llm_judge_score": <float 1.0-5.0>,
  "llm_judge_model": "google/gemini-3.1-flash-lite-preview",
  "error_message": null
}
```

### 🧪 Verification (สำคัญที่สุด — Bug MT.B4 regression)

| Check | Expected |
|-------|----------|
| `llm_judge_score` | float **1.0-5.0** (NOT `0.0`!) — สำหรับ "What is the capital of X?" + ตอบถูก ปกติได้ 4-5 |
| `llm_judge_model` | `"google/gemini-3.1-flash-lite-preview"` (matches request) |
| `metrics_json.llm_judge_skipped_rows` | `0` (ทุก row judge สำเร็จ) |
| ใน worker log | เห็น 5 × `POST https://openrouter.ai/api/v1/chat/completions "HTTP/1.1 200 OK"` |

> 🚨 **ถ้า score = 0.0 และ skipped = 5** → judge model ทุก call fail (ปกติคือชื่อ model ผิด/retired). per Bug MT.B4: ตอนนี้ระบบคืน `null` ถูกต้องแทน `0.0`

> 💡 ลอง `judge_model` อื่นได้ — `anthropic/claude-sonnet-4.6` (แพงกว่าแต่คะแนนน่าจะ stable กว่า), `google/gemini-2.5-flash-lite` (ฟรี/ถูกมาก)

---

## Task 7 — Compare with judge included

**วัตถุประสงค์:** confirm `judge_scores` ใน compare response populate ตอน input มี LLM judge

### Steps

```
POST /api/v1/evaluations/compare
{
  "evaluation_ids": ["<eval1_id>", "<eval_judge_id>"]
}
```

### ✅ Expected response (200)

```json
{
  "evaluation_ids": ["<eval1_id>", "<eval_judge_id>"],
  "metrics": { "...rule metrics..." },
  "judge_scores": {
    "<eval1_id>": null,
    "<eval_judge_id>": <float 1.0-5.0>
  }
}
```

### 🧪 Verification

| Check | Expected |
|-------|----------|
| `judge_scores[<eval1_id>]` | `null` (rule-based — no judge) |
| `judge_scores[<eval_judge_id>]` | float (จาก Task 6) |

---

## Task 8 — Negative: nonexistent artifact → 404

**วัตถุประสงค์:** ระบบ validate artifact ก่อน enqueue

### Steps

```
POST /api/v1/evaluations
{
  "model_artifact_id": "00000000-0000-0000-0000-000000000000",
  "dataset_id": "<dataset_id>",
  "use_llm_judge": false
}
```

### ✅ Expected response (404)

```json
{
  "detail": "Model artifact ... not found",
  "code": "not_found"
}
```

---

## Task 9 — Negative: dataset task_type ไม่ match artifact → 400

**วัตถุประสงค์:** กัน eval ที่ task_type ของ dataset ไม่ตรงกับ training task_type. Guard ถูกเพิ่มใน `api/services/evaluation_service.py` (compare `dataset.task_type` vs `artifact.training_job.project.task_type`).

### Steps

1. สร้าง project + dataset อื่น `task_type: classification`
2. POST eval ที่ใช้ artifact (task_type=qa) + dataset ที่เพิ่งสร้าง (task_type=classification)

```json
{
  "model_artifact_id": "<artifact_id>",
  "dataset_id": "<other_dataset_id>",
  "use_llm_judge": false
}
```

### ✅ Expected (400)

```json
{
  "detail": "Dataset task_type=classification does not match artifact task_type=qa",
  "code": "bad_request",
  "extra": null
}
```

### 🧪 Verification

| Check | Expected |
|-------|----------|
| HTTP status | 400 (NOT 202!) |
| `code` | `"bad_request"` |
| `detail` | กล่าวถึงทั้ง `dataset task_type` + `artifact task_type` ที่ต่างกัน |

---

## Task 10 — Negative: compare ที่ส่ง 1 eval → 422

**วัตถุประสงค์:** schema enforce อย่างน้อย 2 ids

### Steps

```
POST /api/v1/evaluations/compare
{
  "evaluation_ids": ["<eval1_id>"]
}
```

### ✅ Expected response (422)

`extra.errors[0].msg` มี `min_length` หรือ `at least 2`

---

## ✅ Test Completion Checklist

### Rule-based path (ไม่ต้อง OPENROUTER_API_KEY)
- [ ] Task 1 — POST eval rule-based → 202
- [ ] Task 2 — poll → completed, metrics ครบ 5+1 keys, `llm_judge_score: null`
- [ ] Task 3 — eval ที่สอง → completed
- [ ] Task 4 — compare 2 evals → metrics shape ถูก, judge_scores ทั้งคู่ null

### LLM judge path (ต้องตั้ง OPENROUTER_API_KEY ก่อน)
- [ ] Task 5 — POST eval `use_llm_judge=true` + `gemini-3.1-flash-lite-preview` → 202
- [ ] Task 6 — poll → completed, **`llm_judge_score` ระหว่าง 1.0-5.0 (NOT 0.0)** + `skipped_rows: 0`
- [ ] Task 7 — compare มี judge_score ของ eval ที่ใช้ judge

### Negative
- [ ] Task 8 — nonexistent artifact → 404
- [ ] Task 9 — task_type mismatch → 400 + `code: bad_request`
- [ ] Task 10 — compare 1 id → 422

ผ่าน 10 ข้อ = Eval pipeline สมบูรณ์ ✅

---

## Troubleshooting

| อาการ | สาเหตุที่เป็นไปได้ | แก้ |
|-------|------------------|-----|
| Task 2 → `failed` + `error_message="No module named 'sacrebleu'"` | worker image ถูก build ก่อน commit 8900576 (sacrebleu added to `[eval]` extras) | `cd /root/slm-platform && docker compose build worker && docker compose up -d --force-recreate worker` (~3 นาที, layer ส่วนใหญ่ cached). Verify: `docker compose exec -T worker python -c "import sacrebleu; print(sacrebleu.__version__)"` |
| Task 2 → `failed` + Ollama error | artifact ไม่มี GGUF / Ollama ไม่รู้จัก tag | ดู `model-export-extras.md` ก่อน → export GGUF + register Ollama |
| Task 6 → `llm_judge_score: 0.0` + `skipped_rows: 5` (regression Bug MT.B4) | judge_model retired by OpenRouter | ดู worker log มี `404 No endpoints found for ...` → ลอง model อื่น (smoke ผ่าน `python /tmp/probe_judge.py`) |
| Task 6 → `llm_judge_score: null` แล้ว `skipped: 5` | ปกติของ Bug MT.B4 fix — บอกว่าทุก row fail | เปลี่ยน judge_model ให้ใช้งานได้ |
| Task 6 → `error_message` มี `OPENROUTER_API_KEY is empty` | parks ลืมตั้ง key | edit `.env` + `docker compose restart api worker` |
| Task 6 รันนาน > 1 นาที | OpenRouter ช้า / model ใหญ่ | เปลี่ยนเป็น `google/gemini-3.1-flash-lite-preview` (เร็วสุด) |
| Task 7 `judge_scores[<eval1>]` ไม่ใช่ null | response shape เปลี่ยน → regression | check `api/schemas/evaluations.py` `EvaluationCompareResponse` |
| Task 9 ผ่าน 202 ไม่ใช่ 400 | guard task_type match หาย (regression — guard อยู่ใน `api/services/evaluation_service.py` รอบ `dataset.task_type != project.task_type`) | check service file ว่า import `Project` + `TrainingJob` ครบ + เปรียบเทียบก่อน `db.add(ev)` |

---

## Cost Estimate

| Task | LLM ที่ใช้ | API calls (n=5 dataset) | ค่าประมาณ |
|------|-----------|-----------|----------|
| Task 1-4 (rule-based) | (Ollama in-cluster) | 0 OpenRouter | $0 |
| Task 5-6 (judge gemini-3.1-flash-lite-preview) | `google/gemini-3.1-flash-lite-preview` | 5 calls | ~$0.005 - $0.01 |
| Task 7 (compare, no LLM) | (none) | 0 | $0 |
| Task 8-10 (negative) | (early reject) | 0 | $0 |

**รวม: $0 รัน rule-based path; ~$0.01 ถ้าทำ judge path**

> 💡 ถ้าใช้ `claude-sonnet-4.6` แทน → ~$0.05-0.10 ต่อ run

---

## ⏭️ Cross-runbook tips

- **เทียบ HPO best vs manual baseline:** ทำ runbook นี้ 2 ครั้งโดยใช้ artifact ต่างกัน (`<manual_artifact_id>` vs `<hpo_artifact_id>`) → `/evaluations/compare` ทั้งสอง → ดูว่า HPO ดีกว่าจริงไหม
- **Multi-judge consensus:** ทำ Task 5 หลายครั้งด้วย judge_model ต่างกัน (haiku-4.5 + sonnet-4.6 + gemini-2.5-flash-lite) → compare 3 evals → ดู agreement
