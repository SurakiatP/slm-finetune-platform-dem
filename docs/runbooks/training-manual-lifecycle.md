# Training Manual Lifecycle Runbook — Project → Train → Export → Inference

> **Goal:** ทดสอบ happy path ของ Swagger §16 ครบ — สร้าง project → upload seed → train manual → MLflow → WebSocket progress → list/export GGUF → chat completion
> **Estimated time:** 10-15 นาที (training 30 วินาที + export 70 วินาที + manual click-through)
> **Cost:** $0 (ไม่เรียก LLM) — ใช้ canonical seed → format detection skip
> **Prerequisites:** Stack รันอยู่ + GPU ≥ 6GB VRAM (1B QLoRA)

---

## 0. Pre-flight checklist

### 0.1 Stack ขึ้น + GPU พร้อม

```bash
ssh -p <vast-port> root@<vast-ip> "docker compose ps && docker compose exec -T worker python -c 'import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))'"
```

ต้องเห็น 7 containers up + `True NVIDIA RTX <model>`. ถ้า `False` → worker container เห็น GPU ไม่ได้ → ดู Troubleshooting MT.I1 ใน TASK_TRACKER

### 0.2 SSH port forwards (3 ports)

```powershell
ssh -p <vast-port> root@<vast-ip> `
  -L 8000:localhost:8000 `
  -L 9001:localhost:9001 `
  -L 5000:localhost:5000
```

> ⚠️ Port 5000 สำหรับ MLflow UI — ถ้าไม่ forward จะเปิด experiment runs ไม่ได้

### 0.3 Tabs ที่ต้องเปิด

| URL | จุดประสงค์ |
|-----|-----------|
| http://localhost:8000/docs | Swagger UI (หลัก) |
| http://localhost:9001 | MinIO console — verify GGUF + adapter upload |
| http://localhost:5000 | MLflow UI — ดู metric curves |
| Terminal #2: `ssh -p <vast-port> root@<vast-ip> "docker compose logs -f worker"` | ดู training progress real-time |
| Browser DevTools Console (สำหรับ Task 6) | WebSocket subscribe |

### 0.4 Create project

```
POST /api/v1/projects
{
  "name": "training-manual-test",
  "description": "Runbook: training manual lifecycle smoke",
  "task_type": "qa"
}
→ 201
```

🔖 **เก็บ `id` → `<project_id>`**

---

## Task 1 — Upload canonical QA seed (5 rows)

**วัตถุประสงค์:** เตรียม dataset ขั้นต่ำสำหรับ training; canonical = ไม่เรียก Format Detection LLM (ฟรี)

### Steps

สร้าง `seed.jsonl` (5 บรรทัด):

```jsonl
{"question": "What is the capital of France?", "answer": "Paris"}
{"question": "What is the capital of Germany?", "answer": "Berlin"}
{"question": "What is the capital of Japan?", "answer": "Tokyo"}
{"question": "What is the capital of Italy?", "answer": "Rome"}
{"question": "What is the capital of Spain?", "answer": "Madrid"}
```

`POST /api/v1/datasets/upload-seed` (multipart):
- `project_id` = `<project_id>`
- `task_type` = `qa`
- `name` = `capitals-seed`
- `file` = `seed.jsonl`

### ✅ Expected response (201)

```json
{
  "dataset_id": "<UUID>",
  "task_type": "qa",
  "num_samples": 5,
  "invalid_rows": [],
  "format_detection": {
    "ran": <true|false>,
    "model_used": ...,
    "field_mapping": {},
    "rows_total": 5,
    "rows_canonicalised": 5,
    "rows_dropped": 0,
    "notes": "..."
  },
  "pdf_uri": null
}
```

🔖 **เก็บ `dataset_id` → `<dataset_id>`**

> 💡 Note: `format_detection.ran` อาจ true หรือ false ขึ้นกับว่า canonical detection skip path ทำงานไหม + `OPENROUTER_API_KEY` ตั้งหรือยัง — ทั้งคู่ valid (per follow-up MT.F3)

### 🧪 Verification

| Check | Where | Expected |
|-------|-------|----------|
| HTTP status | response | 201 |
| `num_samples` | response | 5 |
| File บน MinIO | http://localhost:9001 → bucket `datasets` → `seeds/` | `<dataset_id>.jsonl` ≈ 400 bytes |

---

## Task 2 — Preview dataset

**วัตถุประสงค์:** confirm rows ถูก parse + เก็บอย่างถูกต้อง

### Steps

```
GET /api/v1/datasets/<dataset_id>/preview?limit=3
```

### ✅ Expected response (200)

```json
{
  "dataset_id": "<dataset_id>",
  "task_type": "qa",
  "samples": [
    { "question": "What is the capital of France?", "answer": "Paris" },
    { "question": "What is the capital of Germany?", "answer": "Berlin" },
    { "question": "What is the capital of Japan?", "answer": "Tokyo" }
  ],
  "total": 5
}
```

### 🧪 Verification

| Check | Expected |
|-------|----------|
| `len(samples)` | 3 |
| `total` | 5 |
| ทุก row มี | `question` + `answer` (no extra fields) |

---

## Task 3 — Submit manual training (1B Llama / 1 epoch / smoke)

**วัตถุประสงค์:** เริ่ม training พร้อม config เร็วที่สุดเพื่อ smoke pipeline

### Steps

```
POST /api/v1/trainings
{
  "mode": "manual",
  "project_id": "<project_id>",
  "dataset_id": "<dataset_id>",
  "base_model": "unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
  "training_name": "capitals-smoke",
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

### ✅ Expected response (202)

```json
{
  "job_id": "<celery-task-uuid>",
  "training_id": "<UUID>",
  "mlflow_run_id": null,
  "mlflow_url": null,
  "status": "pending",
  "websocket_url": "/ws/jobs/<job_id>"
}
```

🔖 **เก็บ:**
- `training_id` → `<training_id>`
- `job_id` → `<job_id>` (สำหรับ WebSocket Task 6)
- `websocket_url` → `<ws_path>` (เป็น relative path เริ่มด้วย `/ws/...` — ต้อง prepend `ws://localhost:8000` เอง)

### 🧪 Verification

| Check | Expected |
|-------|----------|
| HTTP status | 202 (Accepted, not 200) |
| `status` | `"pending"` |
| `websocket_url` ขึ้นต้น | `/ws/` (relative path ตามจริง) |

---

## Task 4 — Poll training status until completed

**วัตถุประสงค์:** ดู status transitions pending → running → completed

### Steps

ทุก ~5-10 วินาที (หรือใช้ Swagger Execute ซ้ำ):

```
GET /api/v1/trainings/<training_id>
```

### ✅ Expected สุดท้าย (status=completed) — ภายใน ~60 วินาที

```json
{
  "id": "<training_id>",
  "status": "completed",
  "mode": "manual",
  "base_model": "unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
  "mlflow_run_id": "<32-char-hex>",
  "mlflow_experiment_id": "1",
  "config_json": { "lora": {...}, "..." : "..." },
  "started_at": "2026-...",
  "ended_at": "2026-...",
  "error_message": null
}
```

🔖 **เก็บ `mlflow_run_id` → `<run_id>` (Task 5 จะใช้)**

### 🧪 Verification

| Check | Expected |
|-------|----------|
| `status` ผ่านลำดับ | `pending` → `running` → `completed` |
| `error_message` | `null` |
| Total wall-time | < 90 วินาที (1B model + 5 rows + 1 epoch) |
| `mlflow_run_id` populate | ✓ |

---

## Task 5 — `GET /trainings/{id}/mlflow-url` + เปิด UI

**วัตถุประสงค์:** ตรวจ mlflow run ลงจริง + ดู metric curves

### Steps

1. `GET /api/v1/trainings/<training_id>/mlflow-url`
2. คัดลอก `mlflow_url` จาก response

### ✅ Expected response (200)

```json
{
  "training_id": "<training_id>",
  "mlflow_run_id": "<run_id>",
  "mlflow_url": "http://mlflow:5000/#/experiments/1/runs/<run_id>"
}
```

### 🧪 Verification

⚠️ **`mlflow_url` คืน internal hostname `mlflow:5000`** — ต้อง substitute เป็น `localhost:5000` ก่อนเปิด browser (per follow-up MT.F1)

เปิด `http://localhost:5000/#/experiments/1/runs/<run_id>` ใน browser:

| Check | Where | Expected |
|-------|-------|----------|
| Run page โหลด | MLflow UI | ไม่ 404 |
| Metrics tab | tab `Metrics` | มี `train_loss` step series + `eval_loss` (เพราะ split=0.1) |
| Params tab | tab `Parameters` | เห็น `learning_rate=0.0002`, `lora.r=8`, `num_train_epochs=1`, etc. |
| Artifacts tab | tab `Artifacts` | (ปกติว่าง — adapter ขึ้น MinIO ไม่ใช่ MLflow) |

---

## Task 5b — Get metric series via API (FE chart endpoints)

**วัตถุประสงค์:** verify 2 endpoints ที่ frontend ใช้ render chart โดยไม่ต้องเรียก MLflow REST ตรงๆ

### Step A — `GET /trainings/{id}/loss-history` (lightweight, chart-ready)

```
GET /api/v1/trainings/<training_id>/loss-history
```

#### ✅ Expected response (200)

```json
{
  "training_id": "<training_id>",
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

#### 🧪 Verification

| Check | Expected |
|-------|----------|
| HTTP status | 200 |
| `train_loss` | array — sorted by `step` ascending; ทุก point มี `step`, `value`, `timestamp_ms` |
| `eval_loss` | array — point จำนวน ≤ train_loss (eval log ทุก eval_steps) |
| `mlflow_run_id` | ตรงกับ Task 4 |
| Empty data case (training fail ก่อน MLflow init) | `train_loss=[]`, `eval_loss=[]`, `mlflow_run_id=null` — 200 ปกติ ไม่ 404 |

### Step B — `GET /trainings/{id}/metrics` (full series + HPO summary)

```
GET /api/v1/trainings/<training_id>/metrics
```

#### ✅ Expected response (200) — manual mode

```json
{
  "training_id": "<training_id>",
  "mlflow_run_id": "<run_id>",
  "metrics": {
    "train_loss":    [ { "step": 0, "value": 4.78, "timestamp_ms": ... }, ... ],
    "eval_loss":     [ { "step": 0, "value": 3.96, "timestamp_ms": ... }, ... ],
    "learning_rate": [ { "step": 1, "value": 0.0002, "timestamp_ms": ... }, ... ],
    "epoch":         [ ... ],
    "grad_norm":     [ ... ],
    "loss":          [ ... ]
  },
  "hpo_children": null
}
```

#### 🧪 Verification

| Check | Expected |
|-------|----------|
| HTTP status | 200 |
| `metrics` keys | ครอบคลุม `train_loss`, `eval_loss`, `learning_rate`, `epoch`, `grad_norm`, `loss` (และ runtime metrics ของ HuggingFace Trainer — ปกติ ~13 keys) |
| ทุก series sorted by step | ascending |
| `hpo_children` | **`null`** (เพราะ mode=manual) — ถ้าเป็น list = bug, manual ไม่ควรมี trial |
| MLflow ดาวน์ | response = `502 + "MLflow tracking server not reachable"` (ไม่ใช่ 500) |

> 💡 **Frontend ใช้:** เรียก `/loss-history` ตอนเปิดหน้า training detail → render chart ด้วย points ตรงๆ. เรียก `/metrics` เมื่อต้องการ deep-dive (ดู grad_norm / learning_rate schedule).

---

## Task 6 — WebSocket live progress (เริ่มก่อน Task 3 ก็ได้)

**วัตถุประสงค์:** ทดสอบ `/ws/jobs/{job_id}` — รับ `training_progress` events real-time

> 💡 ทำคู่กับ Task 3 ได้: เปิด WS ก่อน → POST training → ดู event ไหลเข้า. หรือถ้า Task 3 จบแล้ว training อื่น (เช่น Task 12 ด้านล่าง) ก็ใช้ job_id ของ training นั้นได้

### Steps

1. **Method A — Browser DevTools Console:**
   ```javascript
   const ws = new WebSocket("ws://localhost:8000/ws/jobs/<job_id>");
   ws.onmessage = (e) => console.log(JSON.parse(e.data));
   ws.onclose = () => console.log("WS closed");
   ```

2. **Method B — `websocat` CLI** (ถ้ามี):
   ```bash
   websocat ws://localhost:8000/ws/jobs/<job_id>
   ```

### ✅ Expected events flow

```json
{ "type": "training_progress", "step": 1, "total_steps": 5, "loss": 2.34, "..." }
{ "type": "training_progress", "step": 2, "total_steps": 5, "loss": 1.87 }
{ "type": "training_progress", "step": 3, "total_steps": 5, "loss": 1.43 }
{ "type": "training_progress", "step": 4, "total_steps": 5, "loss": 1.05 }
{ "type": "training_progress", "step": 5, "total_steps": 5, "loss": 0.78 }
{ "type": "completed", "metric": <eval_loss> }
```

### 🧪 Verification

| Check | Expected |
|-------|----------|
| Events ≥ 5 | (1 ต่อ training step) |
| มี `type: "training_progress"` | ≥ 1 |
| มี `type: "completed"` ตอนจบ | ✓ |
| WS auto-close หลัง completed | ✓ (Redis pub/sub disconnect) |

---

## Task 7 — `GET /trainings?project_id=...` — Filter list

**วัตถุประสงค์:** filter trainings ตาม project + status

### Steps

```
GET /api/v1/trainings?project_id=<project_id>&status=completed&limit=10
```

### ✅ Expected response (200)

```json
{
  "items": [
    { "id": "<training_id>", "status": "completed", "..." }
  ],
  "total": 1,
  "limit": 10,
  "offset": 0
}
```

### 🧪 Verification

| Check | Expected |
|-------|----------|
| Found `<training_id>` | ✓ |
| ทุก item `status=completed` | ✓ |

---

## Task 8 — `GET /api/v1/models?training_job_id=...` — Find artifact

**วัตถุประสงค์:** หา model artifact ที่ training สร้าง (ใช้ filter `training_job_id` per Bug MT.B3 fix)

### Steps

```
GET /api/v1/models?training_job_id=<training_id>
```

### ✅ Expected response (200)

```json
{
  "items": [
    {
      "id": "<UUID>",
      "training_job_id": "<training_id>",
      "name": "capitals-smoke",
      "base_model": "unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
      "lora_adapter_uri": "s3://models/adapters/<training_id>",
      "gguf_uri": null,
      "safetensors_uri": null,
      "size_mb": <≈ 20>,
      "ollama_model_tag": null,
      "export_error_message": null,
      "...": "..."
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

🔖 **เก็บ `id` → `<artifact_id>`**

### 🧪 Verification

| Check | Where | Expected |
|-------|-------|----------|
| `total` | response | exactly **1** (NOT all artifacts in DB!) — Bug MT.B3 regression check |
| `training_job_id` ตรง | response | matches `<training_id>` |
| `lora_adapter_uri` populate | response | `s3://models/adapters/...` |
| Adapter file MinIO | http://localhost:9001 → bucket `models` → `adapters/<training_id>/` | มีไฟล์ ~20MB (`adapter_model.safetensors` + `adapter_config.json`) |

---

## Task 9 — `GET /api/v1/models/{id}` — Get artifact detail

**วัตถุประสงค์:** ดึง artifact ตาม id

### Steps

```
GET /api/v1/models/<artifact_id>
```

### ✅ Expected response (200)

(เหมือน items[0] จาก Task 8)

### 🧪 Verification

| Check | Expected |
|-------|----------|
| `id` ตรงกับ path | ✓ |
| `gguf_uri` | `null` (ยังไม่ได้ export) |
| `export_error_message` | `null` |

---

## Task 10 — Export GGUF (q4_k_m)

**วัตถุประสงค์:** convert LoRA → merged HF → f16 GGUF → q4_k_m → MinIO + Ollama

### Steps

```
POST /api/v1/models/<artifact_id>/export
{
  "format": "gguf",
  "quantization": "q4_k_m"
}
```

### ✅ Expected response (202)

```json
{
  "artifact_id": "<artifact_id>",
  "format": "gguf",
  "job_id": "<celery-task-uuid>",
  "status": "pending",
  "websocket_url": "/ws/jobs/<job_id>"
}
```

🔖 **เก็บ `job_id` (export ใหม่)** — ถ้าอยากดู WS progress

### 🧪 Verification

| Check | Expected |
|-------|----------|
| HTTP status | 202 |
| `format` | `"gguf"` |

---

## Task 11 — Poll export until `gguf_uri` set

**วัตถุประสงค์:** รอ export task จบ + ตรวจ MinIO + Ollama

### Steps

ทุก ~10 วินาที:

```
GET /api/v1/models/<artifact_id>
```

### ✅ Expected สุดท้าย — ภายใน ~90 วินาที

```json
{
  "id": "<artifact_id>",
  "lora_adapter_uri": "s3://models/adapters/<training_id>",
  "gguf_uri": "s3://models/exports/<artifact_id>/gguf",
  "ollama_model_tag": "slm/<id8>",
  "export_error_message": null,
  "size_mb": ≈ 20
}
```

> 💡 `<id8>` = 8 ตัวแรกของ `<artifact_id>` (e.g. ถ้า id=`0381367d-...` → tag=`slm/0381367d`)

### 🧪 Verification

| Check | Where | Expected |
|-------|-------|----------|
| `gguf_uri` populate | response | `s3://models/exports/<artifact_id>/gguf` |
| `ollama_model_tag` populate | response | `slm/<id8>` (NOT null — ถ้า null = best-effort fail, ดู error) |
| `export_error_message` | response | `null` |
| GGUF file ใน MinIO | http://localhost:9001 → bucket `models` → `exports/<artifact_id>/gguf/` | `model.q4_k_m.gguf` ≈ 770 MB (1B model q4_k_m) |
| Ollama รู้จัก model | `docker compose exec ollama ollama list` | เห็น row `slm/<id8>:latest` |

---

## Task 12 — Chat completion (OpenAI-compatible)

**วัตถุประสงค์:** inference end-to-end ผ่าน Ollama; โมเดลที่เทรนต้องตอบ "Paris" สำหรับ "capital of France"

### Steps

```
POST /api/v1/inference/chat/completions
{
  "model": "<artifact_id>",
  "messages": [
    { "role": "user", "content": "What is the capital of France? Answer in one word." }
  ],
  "max_tokens": 50,
  "temperature": 0.0
}
```

### ✅ Expected response (200)

```json
{
  "id": "chatcmpl-<...>",
  "object": "chat.completion",
  "created": 1746...,
  "model": "slm/<id8>",
  "choices": [
    {
      "index": 0,
      "message": { "role": "assistant", "content": "Paris." },
      "finish_reason": "stop"
    }
  ],
  "usage": { "prompt_tokens": ≈22, "completion_tokens": ≈3, "total_tokens": ≈25 }
}
```

### 🧪 Verification

| Check | Expected |
|-------|----------|
| HTTP status | 200 |
| `choices[0].message.content` | `"Paris."` หรือ `"Paris"` (โมเดลฝึกจาก seed = ตอบถูก) |
| `model` field ใน response | `slm/<id8>` (Ollama tag, ไม่ใช่ UUID) |
| `usage` populate | ทั้ง 3 ค่า |
| `system_fingerprint` field (ถ้ามี) ไม่ทำให้ 500 | per B8 fix Session 14 |

> 🔄 ลองเปลี่ยนคำถามเป็น `"What is the capital of Japan?"` → ควรตอบ `"Tokyo."`. ถ้าตอบผิด = LoRA adapter อาจ load ไม่ติด หรือ training ไม่ได้ converge

---

## Task 13 — `GET /api/v1/inference/models` — List

**วัตถุประสงค์:** ดู Ollama models ทั้งหมด (รวม base + slm/* ของเรา)

### Steps

```
GET /api/v1/inference/models
```

### ✅ Expected response (200)

```json
{
  "object": "list",
  "data": [
    { "id": "slm/<id8>:latest", "object": "model", "created": 0, "owned_by": "ollama" }
  ]
}
```

### 🧪 Verification

| Check | Expected |
|-------|----------|
| มี `slm/<id8>:latest` ใน data | ✓ |
| ถ้ามี base model อื่น (เช่น `llama3.2:3b`) | ขึ้นด้วย — ปกติ |

> 💡 Bug MT.B1 regression: ถ้า Ollama ไม่มี model เลย → ต้องคืน `{"data":[]}` (ไม่ใช่ 500). ตอนนี้ที่เราเทรนแล้ว = อย่างน้อย 1 row

---

## Task 14 — Cleanup (optional)

```
DELETE /api/v1/projects/<project_id>
→ 204 (cascade trainings + datasets + artifacts ในนั้น)
```

> ⚠️ Cascade behavior — ถ้าจะรัน runbook อื่นที่ refer artifact นี้ (เช่น `evaluation.md`) อย่าลบ — ใช้ artifact ต่อได้

---

## ✅ Test Completion Checklist

- [ ] Task 1 — upload canonical seed → 201, num_samples=5
- [ ] Task 2 — preview → samples (key ถูก) + total=5
- [ ] Task 3 — POST training manual → 202 + `training_id` + `websocket_url` (relative)
- [ ] Task 4 — poll → completed ภายใน ~90s, `error_message=null`, `mlflow_run_id` populated
- [ ] Task 5 — mlflow-url + เปิด UI ดู metrics curve
- [ ] Task 5b — `/loss-history` คืน train+eval loss arrays sorted; `/metrics` คืน full series + `hpo_children: null` (manual mode)
- [ ] Task 6 — WebSocket รับ ≥ 5 `training_progress` + 1 `completed`
- [ ] Task 7 — list trainings filter by project + status ทำงาน
- [ ] Task 8 — `?training_job_id=` filter คืนแค่ 1 item (Bug MT.B3 regression)
- [ ] Task 9 — GET model by id → `gguf_uri=null` ก่อน export
- [ ] Task 10 — POST export gguf q4_k_m → 202
- [ ] Task 11 — poll export → `gguf_uri` + `ollama_model_tag` set, file ~770MB ใน MinIO
- [ ] Task 12 — chat completion ตอบ "Paris."
- [ ] Task 13 — list inference models เห็น `slm/<id8>:latest`

ผ่านครบ 14 ข้อ = Manual training pipeline สมบูรณ์ ✅

---

## Troubleshooting

| อาการ | สาเหตุที่เป็นไปได้ | แก้ |
|-------|------------------|-----|
| Task 3 → 422 `lora.r out of range` | LoRA r > 256 | ใช้ r=8/16/32 |
| Task 4 stuck `pending` > 30s | Celery worker ไม่ได้รัน / queue เต็ม | `docker compose ps worker` + `docker compose logs worker` |
| Task 4 → `failed` + `error_message="Unsloth cannot find any torch accelerator"` | Worker GPU mount stale (per MT.I1 Session 18) | `docker compose up -d --force-recreate worker` |
| Task 4 → `failed` + `<EOS_TOKEN>` error | Unsloth import order / chat template (per B5 Session 13) | regression — เช็ค `ai_engine/training/unsloth_trainer.py` |
| Task 5 mlflow URL 404 ใน browser | host = `mlflow:5000` ใน URL | substitute เป็น `localhost:5000` |
| Task 5 mlflow ports forward ไม่ติด | ลืม `-L 5000:localhost:5000` ตอน SSH | re-SSH ด้วย flag |
| Task 6 WebSocket connect fail | path สร้างผิด — `websocket_url` เป็น relative | prepend `ws://localhost:8000` |
| Task 8 คืน > 1 item / item ไม่ใช่ของ training นี้ | filter `training_job_id` หาย (Bug MT.B3) | regression `commit 31ea5bd`; หา `api/services/model_service.py:50` |
| Task 11 → `export_error_message` = "EOF when reading a line" | llama.cpp ไม่ pre-built ใน worker | regression check `docker/worker.Dockerfile` `RUN cmake llama-quantize` (per B6) |
| Task 11 → `gguf_uri` set แต่ `ollama_model_tag=null` | Ollama API down / blob upload fail | `docker compose logs ollama` หา error; gguf ใช้ได้, แค่ inference router หา ไม่ได้ |
| Task 12 → 409 `no LoRA adapter on file` | training fail แต่ artifact row อยู่ | re-train + re-export |
| Task 12 → 500 `system_fingerprint Extra inputs not permitted` | response schema strict (regression of B8) | check `extra="ignore"` ใน chat completion response schemas |
| Task 12 ตอบไม่ใช่ "Paris" | LoRA load fail / training ไม่ converge | เช็ค model card Ollama: `docker compose exec ollama ollama show slm/<id8>` |

---

## Cost Estimate

| Task | LLM ที่ใช้ | API calls | ค่าประมาณ |
|------|-----------|-----------|----------|
| Task 1 (canonical seed) | (Format Detection อาจ skip) | 0-1 | $0 - <$0.001 |
| Task 2-13 | (ไม่ต้อง — Ollama in-cluster) | 0 | $0 |

**รวม: ฟรี (ถ้า canonical seed) — เป็น smoke ดี ๆ ที่ใช้ทดสอบ deployment ใหม่ทุกครั้งโดยไม่กิน OpenRouter**

---

## ⏭️ Next runbooks ที่ใช้ artifact จาก runbook นี้ต่อได้

- `evaluation.md` — ใช้ `<artifact_id>` + `<dataset_id>` ทำ rule-based + LLM judge eval
- `model-export-extras.md` — ใช้ `<artifact_id>` ทำ SafeTensors export + binary download + legacy completions
- `training-cancel.md` — ทำ training ใหม่ใน `<project_id>` แล้วลอง DELETE mid-flight
