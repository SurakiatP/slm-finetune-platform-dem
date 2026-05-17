# Training HPO Runbook — Optuna Search + Best Params + Final Retrain

> **Goal:** ทดสอบ Swagger §8 mode=hpo ครบ — n_trials × Optuna sampling, MLflow nested runs, best params extraction, final-retrain artifact persistence
> **Estimated time:** 10-15 นาที (n_trials=2 + final retrain)
> **Cost:** $0 (Ollama in-cluster — ไม่ใช้ OpenRouter)
> **Prerequisites:** Stack รันอยู่ + GPU ≥ 6GB VRAM + จบ `training-manual-lifecycle.md` ดีกว่า (เพราะใช้ pattern เดียวกัน)

---

## 0. Pre-flight checklist

### 0.1 Stack ขึ้น + GPU พร้อม

```bash
ssh -p <vast-port> root@<vast-ip> "docker compose ps && nvidia-smi --query-gpu=memory.free --format=csv,noheader"
```

ต้องเห็น 7 containers up + free VRAM ≥ 5GB (แต่ละ trial โหลด 1B model)

### 0.2 SSH port forwards (3 ports เหมือน manual lifecycle)

```powershell
ssh -p <vast-port> root@<vast-ip> `
  -L 8000:localhost:8000 `
  -L 9001:localhost:9001 `
  -L 5000:localhost:5000
```

### 0.3 Tabs ที่ต้องเปิด

| URL | จุดประสงค์ |
|-----|-----------|
| http://localhost:8000/docs | Swagger UI (หลัก) |
| http://localhost:5000 | MLflow UI — **สำคัญสำหรับ HPO**: ดู nested runs structure |
| Terminal #2: `docker compose logs -f worker` | ดู Optuna trial logs + pruning decisions |

### 0.4 Create project + upload seed

```
POST /api/v1/projects
{ "name": "training-hpo-test", "task_type": "qa" }
→ 201, เก็บ <project_id>
```

`POST /api/v1/datasets/upload-seed` (multipart, 5 QA rows เหมือน manual runbook):
```jsonl
{"question": "What is the capital of France?", "answer": "Paris"}
{"question": "What is the capital of Germany?", "answer": "Berlin"}
{"question": "What is the capital of Japan?", "answer": "Tokyo"}
{"question": "What is the capital of Italy?", "answer": "Rome"}
{"question": "What is the capital of Spain?", "answer": "Madrid"}
```

🔖 **เก็บ:** `<project_id>` + `<dataset_id>`

---

## Task 1 — Submit HPO training (n_trials=2 smoke)

**วัตถุประสงค์:** เริ่ม HPO sweep พร้อม search space เล็ก ๆ + fixed_config สั้น ๆ เพื่อ smoke pipeline

### Steps

```
POST /api/v1/trainings
{
  "mode": "hpo",
  "project_id": "<project_id>",
  "dataset_id": "<dataset_id>",
  "base_model": "unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
  "training_name": "hpo-capitals-smoke",
  "hpo_config": {
    "n_trials": 2,
    "objective_metric": "eval_loss",
    "direction": "minimize",
    "sampler": "tpe",
    "pruner": "median",
    "timeout_seconds": 600,
    "search_space": {
      "learning_rate": {
        "type": "float", "low": 1e-5, "high": 5e-4, "log": true
      },
      "lora_r": {
        "type": "categorical", "choices": [8, 16]
      }
    },
    "fixed_config": {
      "num_train_epochs": 1,
      "per_device_train_batch_size": 1,
      "gradient_accumulation_steps": 1,
      "max_seq_length": 512
    }
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

🔖 **เก็บ `training_id` → `<hpo_training_id>`**

### 🧪 Verification

| Check | Expected |
|-------|----------|
| HTTP status | 202 |
| `mode` ใน config | `"hpo"` (validated by discriminated union) |

> 💡 **n_trials=2** เป็น minimum (per `HPOConfig.n_trials: ge=2`); production ใช้ 8-10 ขึ้นไป. smoke 2 พอ

---

## Task 2 — Poll HPO until completed (~3-5 นาที)

**วัตถุประสงค์:** ดู HPO ผ่านครบ trials + final retrain

### Steps

ทุก ~30 วินาที:

```
GET /api/v1/trainings/<hpo_training_id>
```

### ✅ Expected ระหว่างทาง (status=running)

```json
{
  "id": "<hpo_training_id>",
  "status": "running",
  "mode": "hpo",
  "config_json": {
    "n_trials": 2,
    "search_space": { ... },
    "...": "..."
  },
  "best_metric_value": null,
  "best_params_json": null,
  "ended_at": null
}
```

### ✅ Expected สุดท้าย (status=completed)

```json
{
  "id": "<hpo_training_id>",
  "status": "completed",
  "mode": "hpo",
  "best_metric_value": <float, ปกติ 1.5-3.5 สำหรับ smoke>,
  "best_params_json": {
    "learning_rate": <float between 1e-5 and 5e-4>,
    "lora_r": <8 or 16>
  },
  "mlflow_run_id": "<parent run id>",
  "mlflow_experiment_id": "1",
  "started_at": "2026-...",
  "ended_at": "2026-...",
  "error_message": null
}
```

🔖 **เก็บ `mlflow_run_id` → `<hpo_run_id>`** (parent run)

### 🧪 Verification

| Check | Expected |
|-------|----------|
| `status` final | `completed` |
| `error_message` | `null` |
| `best_metric_value` | not null + float |
| `best_params_json` | not null + dict ที่มี keys ตรงกับ search_space (`learning_rate`, `lora_r`) |
| Total wall time | ~3-5 นาที (2 trials + final retrain ใช้ best) |

---

## Task 3 — Verify nested runs ใน MLflow

**วัตถุประสงค์:** ยืนยันว่า HPO สร้าง parent + 3 nested runs (`trial-000`, `trial-001`, `best`) ที่มี `mlflow.parentRunId` tag ชี้ไปที่ parent ถูกต้อง

### 🎯 Primary check — เรียก API endpoint ที่ frontend ใช้

ใช้ `GET /trainings/{id}/metrics` (ตัวเดียว — backend ไป query MLflow ให้แล้ว):

```
GET /api/v1/trainings/<hpo_training_id>/metrics
```

#### ✅ Expected response (200)

```json
{
  "training_id": "<hpo_training_id>",
  "mlflow_run_id": "<parent_run_id>",
  "metrics": { /* parent run metrics — ปกติว่างหรือมี aggregate metrics */ },
  "hpo_children": [
    {
      "run_id": "8a5329ed43...",
      "name": "best",
      "final_eval_loss": 3.87,
      "params": {
        "best_params.learning_rate": "0.000115",
        "best_params.lora_r": "8",
        "best_metric_value": "3.87",
        "config.learning_rate": "0.000115",
        "config.lora.r": "8",
        "..." : "..."
      }
    },
    {
      "run_id": "397a144fe6...",
      "name": "trial-001",
      "final_eval_loss": 5.03,
      "params": { "learning_rate": "1.02e-05", "lora.r": "8" }
    },
    {
      "run_id": "37f0c88926...",
      "name": "trial-000",
      "final_eval_loss": 3.87,
      "params": { "learning_rate": "0.000115", "lora.r": "8" }
    }
  ]
}
```

#### 🧪 Verification

| Check | Expected |
|-------|----------|
| HTTP status | 200 |
| `hpo_children` | **list ขนาด 3** (n_trials=2 + 1 best run) |
| Children names | `{"best", "trial-000", "trial-001"}` (ลำดับไม่สำคัญ) |
| `final_eval_loss` ของ `best` | เท่ากับ `best_metric_value` ของ Task 2 response |
| `params` ของ trial | string-typed — มี keys ตรงกับ search_space (`learning_rate`, `lora.r` หรือ `lora_r`) |
| `params` value type | **string เสมอ** (MLflow params type — frontend ต้อง parse ถ้าจะ plot) |
| `hpo_children` is null | ❌ FAIL — null = แปลว่า mode != hpo หรือ children search ไม่เจอ |

> 💡 **นี่คือสิ่งที่ frontend จะใช้** — เรียกผ่าน API endpoint เดียว ไม่ต้องคุย MLflow REST ตรงๆ.

---

### 🔬 Alternative check — MLflow REST API ตรงๆ (deterministic deep-check)

ถ้าอยากดู raw data ของ MLflow โดยไม่ผ่าน backend — ใช้ตัวนี้ verify wiring ของ `mlflow.parentRunId` tag ตรงๆ:

```bash
# ssh เข้า host ก่อน หรือผ่าน port-forward 5000
HPO_TRID=<hpo_training_id>
HPO_RUN=$(curl -s http://localhost:8000/api/v1/trainings/$HPO_TRID | python3 -c "import json,sys; print(json.load(sys.stdin)['mlflow_run_id'])")
echo "parent=$HPO_RUN"

curl -s -X POST http://localhost:5000/api/2.0/mlflow/runs/search \
  -H "Content-Type: application/json" \
  -d "{\"experiment_ids\":[\"1\"],\"filter\":\"tags.mlflow.parentRunId = '$HPO_RUN'\",\"max_results\":10}" \
  | python3 -c "
import json, sys
d = json.load(sys.stdin)
for r in d.get('runs', []):
    tags = {t['key']: t['value'] for t in r['data'].get('tags', [])}
    metrics = {m['key']: m['value'] for m in r['data'].get('metrics', [])}
    print(f\"  - {tags.get('mlflow.runName','?'):12} eval_loss={metrics.get('eval_loss','-')}\")
"
```

### ✅ Expected output

```
parent=21b980ebd5534bc4bd0dc38573d8a11d
  - best         eval_loss=3.8726041316986084
  - trial-001    eval_loss=5.029859542846680
  - trial-000    eval_loss=3.8726041316986084
```

3 children ทุกตัวมี `mlflow.parentRunId` tag ชี้ไปที่ `<hpo_run_id>` = nested structure ถูกต้อง.

> 💡 `best` run ใช้ params เดียวกับ trial ที่ eval_loss ต่ำสุด → eval_loss เท่ากัน (deterministic re-train).

---

### 🖥️ ดูใน MLflow UI (3 ทาง)

> ⚠️ **MLflow UI default = flat list** — แสดงทุก run ใน table แบบเรียงตาม Created time (ทั้ง parent + children). **ไม่ใช่ bug** — เป็น behavior ปกติ. ดู nested view ได้ผ่าน 3 ทางนี้:

#### ทาง 1 (แนะนำ): Click parent run → ดู Child Runs ใน detail page

1. ใน experiment runs table หา row `hpo-capitals-smoke` แล้วคลิก
2. หน้า run detail จะมี section/tab **"Child Runs"** — list ของ trial-000, trial-001, best
3. คลิก child name แต่ละตัวเพื่อดู metrics + params ของ run นั้น

#### ทาง 2: Filter by parent tag ใน search bar

ใน search box ด้านบน runs table:
```
tags.`mlflow.parentRunId` = "<hpo_run_id>"
```
แทนที่ `<hpo_run_id>` ด้วย parent ที่ได้จาก Task 2 (`mlflow_run_id` ของ training response). ตารางจะ filter เห็นเฉพาะ 3 children.

#### ทาง 3: Toggle nested view (ถ้า MLflow version รองรับ)

มอง toolbar ด้านบนตาราง — บางเวอร์ชันมี:
- Icon ฟันเฟือง / "Columns" → settings → "Show nested runs"
- หรือ "Group by" → เลือก `mlflow.parentRunId`

(MLflow 3.x ตำแหน่ง toggle เปลี่ยนตาม minor version — ถ้าหาไม่เจอใช้ทาง 1 หรือ 2)

---

### 🧪 Per-run verification (หลังจาก click parent → Child Runs)

| Check | Where | Expected |
|-------|-------|----------|
| `trial-000` + `trial-001` exist as children | parent run → Child Runs section | 2 trials |
| `best` exists as child | parent run → Child Runs section | 1 best run |
| `trial-000` params | child run page → Parameters tab | `learning_rate`, `lora_r` (ตาม Optuna sample) |
| `trial-000` metrics | child run page → Metrics tab | มี `eval_loss` (พื้นฐานของ pruner) |
| `best` run params | child run page → Parameters tab | match `best_params_json` จาก Task 2 response |
| `best` run metrics | child run page → Metrics tab | `eval_loss` ≤ trial ที่ดีที่สุด + มี `train_loss` step series |

---

## Task 4 — Verify final retrain artifact persisted

**วัตถุประสงค์:** confirm best-params final retrain ลงเป็น `ModelArtifact` ใน DB

### Steps

```
GET /api/v1/models?training_job_id=<hpo_training_id>
```

### ✅ Expected response (200)

```json
{
  "items": [
    {
      "id": "<artifact_id>",
      "training_job_id": "<hpo_training_id>",
      "name": "hpo-capitals-smoke",
      "base_model": "unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
      "lora_adapter_uri": "s3://models/adapters/<hpo_training_id>",
      "size_mb": ≈ 23,
      "gguf_uri": null,
      "ollama_model_tag": null,
      "...": "..."
    }
  ],
  "total": 1
}
```

🔖 **เก็บ `id` → `<hpo_artifact_id>`**

### 🧪 Verification

| Check | Where | Expected |
|-------|-------|----------|
| `total` | response | exactly **1** (Bug MT.B3 regression) |
| `lora_adapter_uri` | response | populate `s3://models/adapters/...` |
| Adapter MinIO | http://localhost:9001 → bucket `models` → `adapters/<hpo_training_id>/` | มีไฟล์ ≈ 23MB |
| `size_mb` | response | ใกล้เคียง ขนาดไฟล์จริง |

---

## Task 5 — เทียบ best_params กับ trial ที่ดีที่สุด

**วัตถุประสงค์:** sanity check ว่า Optuna เลือก best ตรงกับ trial ที่มี `eval_loss` ต่ำสุด

### Steps

1. ใน MLflow UI ที่ Task 3 → experiment 1
2. ดู `eval_loss` ของ `trial-000` กับ `trial-001`
3. หา trial ที่ `eval_loss` ต่ำกว่า

### 🧪 Verification

| Check | Expected |
|-------|----------|
| `best_params_json` (จาก Task 2 response) | ตรงกับ params ของ trial ที่ `eval_loss` ต่ำสุด |
| Direction = `minimize` ⇒ Optuna เลือก min | ✓ |
| `best` run's `eval_loss` | ≈ best trial's `eval_loss` (อาจต่างเล็กน้อยเพราะ stochastic) |

---

## Task 6 — Negative: missing search_space → 422

**วัตถุประสงค์:** validator block HPO config ที่ไม่มี param ให้ search

### Steps

```
POST /api/v1/trainings
{
  "mode": "hpo",
  "project_id": "<project_id>",
  "dataset_id": "<dataset_id>",
  "hpo_config": {
    "n_trials": 2,
    "search_space": {}
  }
}
```

### ✅ Expected response (422)

```json
{
  "detail": "validation error",
  "code": "validation_error",
  "extra": {
    "errors": [
      {
        "loc": ["body", "hpo_config", "search_space"],
        "msg": "HPOSearchSpace must define at least one parameter",
        "..."
      }
    ]
  }
}
```

### 🧪 Verification

| Check | Expected |
|-------|----------|
| HTTP status | 422 |
| Error message มี `at least one parameter` | ✓ |

---

## Task 7 — Negative: n_trials=1 → 422

**วัตถุประสงค์:** schema enforce n_trials ≥ 2

### Steps

```
POST /api/v1/trainings
{
  "mode": "hpo",
  "project_id": "<project_id>",
  "dataset_id": "<dataset_id>",
  "hpo_config": {
    "n_trials": 1,
    "search_space": {
      "learning_rate": { "type": "float", "low": 1e-5, "high": 1e-3 }
    }
  }
}
```

### ✅ Expected response (422)

`extra.errors[0].msg` มี `greater than or equal to 2`

---

## Task 8 — (Optional) Submit HPO กับ pruner=none

**วัตถุประสงค์:** ทดสอบ pruner=none variant (median pruner ตัด trial เร็ว, none = ไม่ตัด)

### Steps

(เหมือน Task 1 แต่เปลี่ยน)

```json
"pruner": "none",
"n_trials": 3,
"timeout_seconds": 1200
```

### 🧪 Verification

| Check | Expected |
|-------|----------|
| ทุก trial รัน เต็มจำนวน step | (ปกติ trial เลย step ที่ pruner ตัด — ดูใน MLflow) |
| Total wall time นานกว่า | ~7-10 นาที (3 trials × full duration) |

> 💡 ส่วนใหญ่ใช้ `median` pruner ในการ production — ตัด trial ที่ promise น้อย

---

## ✅ Test Completion Checklist

- [ ] Task 1 — POST HPO mode=hpo + search_space → 202
- [ ] Task 2 — poll → completed ภายใน ~5 นาที, `best_metric_value` not null, `best_params_json` ตรง search_space schema
- [ ] Task 3 — `GET /trainings/{id}/metrics` คืน `hpo_children` array ขนาด 3 (`trial-000`, `trial-001`, `best`) แต่ละตัวมี `final_eval_loss` + `params`; (alt) MLflow REST: 3 children มี `mlflow.parentRunId` ชี้ parent
- [ ] Task 4 — `?training_job_id=<hpo>` → exactly 1 artifact (filter ทำงาน)
- [ ] Task 5 — `best_params` ตรง trial ที่ `eval_loss` ต่ำสุด
- [ ] Task 6 — empty search_space → 422
- [ ] Task 7 — n_trials=1 → 422
- [ ] (Optional) Task 8 — pruner=none variant ทำงาน

ผ่าน 7 ข้อหลัก = HPO pipeline สมบูรณ์ ✅

---

## Troubleshooting

| อาการ | สาเหตุที่เป็นไปได้ | แก้ |
|-------|------------------|-----|
| Task 2 → `failed` + `error_message="No trials are completed yet"` | ทุก trial ตายเพราะ Unsloth ไม่เห็น GPU | per MT.I1 — `docker compose up -d --force-recreate worker` |
| Task 2 → `failed` + `error_message` มี `<EOS_TOKEN>` หรือ chat template error | trainer regression (B5 Session 13) | check `ai_engine/training/unsloth_trainer.py` import order |
| Task 2 รันนาน > 15 นาที | `timeout_seconds` ตั้งสูง + n_trials ใหญ่ | ลด n_trials หรือ `per_device_train_batch_size` |
| Task 3 — UI flat ทุก run โผล่เป็น row เดี่ยวเรียงตาม time | ✅ **ไม่ใช่ bug — MLflow UI default behavior**. ดู nested ผ่านทาง 1-3 (click parent / filter / toggle) | (no fix needed — verify ผ่าน `/trainings/{id}/metrics` หรือ MLflow REST ก็พอ) |
| Task 3 — `/trainings/{id}/metrics` คืน `hpo_children: null` หรือ `[]` ใน hpo mode | nested run wiring break จริง / mode สูญหาย | (1) check `api/services/trainings_service.py` ว่ายัง check `job.mode is TrainingMode.HPO` (2) check `workers/tasks/hpo_training.py` ว่ายังมี `mlflow.start_run(nested=True)` ทั้งใน trial loop และ best-retrain block |
| Task 3 — endpoint คืน 502 `MLflow tracking server not reachable` | MLflow container ดาวน์ / network ไป mlflow:5000 ไม่ติด | `docker compose ps mlflow` + `docker compose logs mlflow \| tail` |
| Task 4 `?training_job_id=` คืน > 1 item | filter regression (Bug MT.B3 ย้อน) | check `api/services/model_service.py:50` มี `WHERE training_job_id` |
| Task 5 best มี `eval_loss` สูงกว่า trials | Optuna direction ผิด / metric ผิด | ตรวจ `objective_metric: "eval_loss"` + `direction: "minimize"` |
| Task 6/7 ผ่าน 200 ไม่ใช่ 422 | Pydantic validator ไม่ทำงาน | check `HPOSearchSpace._at_least_one_param` + `HPOConfig.n_trials: ge=2` |

---

## Cost Estimate

| Phase | LLM ที่ใช้ | API calls | ค่าประมาณ |
|-------|-----------|-----------|----------|
| Task 1-7 | (none — Ollama in-cluster, ไม่มี OpenRouter) | 0 | $0 |
| Task 8 (3 trials) | (none) | 0 | $0 |

**HPO smoke ฟรี** — แต่กิน GPU compute ~5-10 นาทีต่อ run

> 💡 Production HPO (n_trials=10, full epochs) จะกิน 30 นาที - 2 ชม. ขึ้นกับ dataset/model size

---

## ⏭️ Next runbook

- `evaluation.md` — ใช้ `<hpo_artifact_id>` (best-retrain) เทียบ baseline ผ่าน `/evaluations/compare`
- `model-export-extras.md` — export `<hpo_artifact_id>` เป็น GGUF/SafeTensors เปรียบเทียบกับ manual artifact

---

## 🎯 RTX 3060 12GB sizing table (authoritative reference)

ตารางนี้ใช้เป็น single source of truth ทั้ง FE form defaults และ HPO service guard (`api/services/training_service.py:_max_safe_batch_for_3060`). มี ~20% headroom จาก Unsloth/community benchmarks (QLoRA 4-bit, LoRA r=16, all-linear target_modules).

| base_model (params) | seq=1024 | seq=2048 | seq=4096 | seq=8192 | trial time<br>(3 epochs, 200 rows) |
|---|---|---|---|---|---|
| ≤1B (TinyLlama, Llama-3.2-1B, Qwen3-0.6B, Qwen2.5-0.5B) | batch=16 | **batch=8** | batch=4 | batch=2 | ~15-30 min |
| ≤1.5B (Qwen2.5-1.5B) | batch=8 | **batch=4** | batch=2 | batch=1 | ~30-45 min |
| ≤2B (SmolLM2-1.7B, Qwen3-1.7B, Gemma2-2B) | batch=4 | **batch=4** | batch=2 | batch=1 | ~45-60 min |
| ≤3B (Llama-3.2-3B, Qwen2.5-3B) | batch=4 | **batch=2** | batch=1 | batch=1 | ~1.5-3 hr |

**Bold คือค่า default ที่ FE ควร pre-fill เมื่อเลือก base model นั้น** (seq=2048 ครอบคลุม 95% ของ task)

### HPO budget rule of thumb (timeout_seconds=14400 / 4 ชม.)

| Model size | safe n_trials | recommended |
|---|---|---|
| ≤1.5B | 8-12 | **8** |
| 2B | 4-6 | **6** |
| 3B | 2-3 | **3** |

ถ้าเลย 4 ชม. Optuna `timeout_seconds` จะตัด trial ที่กำลังรันทิ้งและคืน best-so-far.

---

## 🧰 Default 3060 search space (preset)

มี factory function `ai_engine.hpo.search_spaces.default_3060_search_space()` คืน `HPOSearchSpace` ที่ตั้งค่า 5 fields ที่ research แล้วว่าคุ้มค่าจะ tune บน 3060:

```python
from ai_engine.hpo.search_spaces import default_3060_search_space

# In an HPO request body, send the dict representation:
search_space = default_3060_search_space().model_dump(mode="json")
```

ผลลัพธ์ JSON:

```json
{
  "learning_rate": {"type": "float", "low": 1e-5, "high": 5e-4, "log": true},
  "lora_r": {"type": "categorical", "choices": [8, 16, 32]},
  "lora_alpha": {"type": "categorical", "choices": [16, 32, 64]},
  "num_train_epochs": {"type": "int", "low": 2, "high": 4, "step": 1, "log": false},
  "gradient_accumulation_steps": {"type": "categorical", "choices": [4, 8, 16]}
}
```

FE สามารถ deep-link ตัวอย่างนี้เข้าฟอร์มเป็น "Recommended for RTX 3060" preset, ให้ user override ที่ละ field ได้.

---

## 🚨 HPO safety guard — VRAM-unsafe batch rejected at submit time

ถ้าใส่ `per_device_train_batch_size` เข้า `search_space` และมีค่าที่เกิน safe ceiling สำหรับ (base_model, max_seq_length) ที่เลือก → service จะ reject ก่อน enqueue Celery ด้วย **HTTP 422**:

```
hpo_config.search_space.per_device_train_batch_size choices [8] exceed the
safe ceiling (2) for base_model='unsloth/Llama-3.2-3B-Instruct-bnb-4bit'
(3.21B params) at max_seq_length=2048 on RTX 3060 12GB. Lower the choices
or shorten max_seq_length.
```

วิธีหลีกเลี่ยง:
1. (แนะนำ) **ห้าม tune `per_device_train_batch_size` ใน HPO** — fix ใน `fixed_config` แทน, ใช้ table ด้านบน
2. ลด `fixed_config.max_seq_length` ลง (1024 → batch ได้ใหญ่ขึ้น)
3. เปลี่ยน base model เป็นเล็กกว่า
