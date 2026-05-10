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

## Task 3 — เปิด MLflow UI ดู nested runs

**วัตถุประสงค์:** ดูว่า HPO สร้าง parent + nested runs structure ใน MLflow ถูกต้อง

### Steps

1. `GET /api/v1/trainings/<hpo_training_id>/mlflow-url` → คัดลอก URL
2. Substitute `mlflow:5000` → `localhost:5000`
3. เปิด browser

### ✅ Expected ใน MLflow UI

ใน experiment 1 ควรเห็น run hierarchy:

```
hpo-capitals-smoke (parent run)         ← <hpo_run_id>
├── trial-0                              ← n_trials=2 ⇒ 2 nested
├── trial-1
└── best                                 ← final retrain ใช้ best_params
```

### 🧪 Verification

| Check | Where | Expected |
|-------|-------|----------|
| Parent run exists | experiment 1 | row `hpo-capitals-smoke` |
| Click parent → child runs section | bottom of run page | 3 child runs (`trial-0`, `trial-1`, `best`) |
| `trial-0` params | params tab | `learning_rate`, `lora_r` (ตามที่ Optuna sample) |
| `trial-0` metrics | metrics tab | มี `eval_loss` (เพื่อ pruner ตัดสินใจ) |
| `best` run | params tab | match `best_params_json` จาก Task 2 response |
| `best` run metrics | metrics tab | มี `eval_loss` final + `train_loss` series |

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
2. ดู `eval_loss` ของ `trial-0` กับ `trial-1`
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
- [ ] Task 3 — MLflow UI เห็น parent + 3 nested runs (`trial-0`, `trial-1`, `best`)
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
| Task 3 MLflow แค่ parent run, ไม่มี nested | nested run wiring break (regression) | ดู `workers/tasks/hpo_training.py` `mlflow.start_run(nested=True)` |
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
