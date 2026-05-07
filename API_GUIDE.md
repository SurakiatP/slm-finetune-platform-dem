# API Guide — แพลตฟอร์ม SLM Fine-Tuning

เอกสารอ้างอิงสำหรับ HTTP และ WebSocket endpoint ทุกตัวที่แพลตฟอร์มเปิดใช้งาน
สำหรับการติดตั้ง / quickstart ดู [`README.md`](./README.md) สำหรับ OpenAPI schema
ฉบับสด (มี request/response shape ครบ) ให้เปิด <http://localhost:8000/docs>
หลังจากรัน `docker compose up -d`

---

## ข้อตกลงทั่วไป (Conventions)

- **Base URL**: `http://localhost:8000` (ปรับผ่าน env ได้)
- **Versioning**: ทุก resource อยู่ภายใต้ `/api/v1/`
- **Auth**: ไม่มี — ทุก endpoint เปิดทั้งหมด (ตาม scope ของโปรเจกต์ PoC)
- **Content type**: `application/json` ทุกที่ ยกเว้น `upload-seed` (multipart)
  และ `download` (binary stream)
- **งาน async** (SDG, training, export, evaluation) คืน **`202 Accepted`**
  พร้อม `job_id` — subscribe ที่ `ws://.../ws/jobs/{job_id}` เพื่อรับ progress
- **IDs**: id ของทุก resource เป็น UUID ส่วน `job_id` คือ Celery task id
  (รูปแบบเป็น UUID เช่นกัน)

### รูปแบบ error response

response ที่ไม่ใช่ 2xx ทุกตัวใช้ body แบบเดียวกัน:

```json
{
  "detail": "Project 00000000-... not found",
  "code": "not_found",
  "extra": null
}
```

| `code` | HTTP | เกิดขึ้นเมื่อ |
|--------|------|------|
| `validation_error` | 422 | Pydantic schema ปฏิเสธ body — รายละเอียดต่อ field อยู่ใน `extra.errors` |
| `not_found` | 404 | id ของ resource ไม่มีอยู่ |
| `conflict` | 409 | resource มีอยู่ แต่ยังไม่พร้อมใช้ (dataset กำลังสร้าง / ยังไม่ export model / ฯลฯ) |
| `bad_request` | 400 | error เชิง semantic ที่ schema ไม่ดัก (task_type ไม่ตรง, `stream=true`, ฯลฯ) |
| `payload_too_large` | 413 | upload seed เกิน 10 MiB |
| `bad_gateway` | 502 | Ollama daemon ติดต่อไม่ได้ / ตอบ 5xx |
| `internal_error` | 500 | server error ที่จัดการไม่ได้ — มี `extra.correlation_id` ไว้อ้างอิง log บนเซิร์ฟเวอร์ |

---

## ภาพรวม Lifecycle

```
┌─────────┐   POST /projects   ┌─────────┐
│ create  ├───────────────────►│ project │
└─────────┘                    └────┬────┘
                                    │
            POST /datasets/upload-seed   (ทางเลือก สำหรับ with_seed mode)
                                    │
            POST /datasets/generate ─┤   (SDG ผ่าน OpenRouter — 202)
                                    │
                       WS job_id ───┤───► sdg_progress* → completed
                                    │
              POST /trainings ──────┤   (manual หรือ HPO — 202)
                                    │
                       WS job_id ───┤───► training_progress* | hpo_progress*
                                    │     → completed (ได้ artifact)
                                    │
        POST /models/{id}/export ───┤   (LoRA → GGUF + ลงทะเบียนกับ Ollama — 202)
                                    │
                       WS job_id ───┤───► completed (มี ollama_model_tag)
                                    │
   POST /inference/chat/completions ┤   (forward ไปยัง Ollama, OpenAI-compat)
                                    │
        POST /evaluations ──────────┤   (predict + metrics + LLM judge ทางเลือก — 202)
                                    │
                       WS job_id ───┘───► completed (ได้ metrics_json)
```

---

## Resources

### Projects (`/api/v1/projects`)

Project คือกลุ่มระดับบนสุด — ผูกค่า `task_type` ไว้คงที่ และเป็นเจ้าของทั้ง
dataset และ training ทั้งหมดในกลุ่มนั้น

| Method | Path | คำอธิบาย |
|--------|------|--------------|
| `POST` | `/api/v1/projects` | **สร้าง project** Body: `{name, description?, task_type}` ค่า `task_type` เป็น `classification`, `tool_calling`, หรือ `qa` และ **เปลี่ยนไม่ได้** หลังสร้าง คืน `201` พร้อม project ใหม่ |
| `GET` | `/api/v1/projects` | **List projects** (มี pagination) Query: `limit` (1–200, default 50), `offset` (default 0) |
| `GET` | `/api/v1/projects/{id}` | **อ่าน project** ตาม UUID — `404` ถ้าไม่พบ |
| `PATCH` | `/api/v1/projects/{id}` | **แก้ name หรือ description** ทั้งสอง field optional ส่วน `task_type` แก้ไม่ได้ (ต้องสร้าง project ใหม่) |
| `DELETE` | `/api/v1/projects/{id}` | **ลบ project** — cascade ไปยัง dataset และ training ทุกตัว (FK เป็น `ON DELETE CASCADE`) คืน `204` |

### Datasets (`/api/v1/datasets`)

Dataset คือชุดของ row JSONL ที่เก็บใน MinIO มาจาก 3 ที่:
- `seed` — user upload เอง
- `sdg` — สร้างผ่าน OpenRouter
- `merged` — seed + sdg รวมกัน (ของอนาคต)

| Method | Path | คำอธิบาย |
|--------|------|--------------|
| `POST` | `/api/v1/datasets/upload-seed` | **Upload seed examples** (multipart) Form: `project_id`, `task_type`, `file` (JSON array หรือ JSONL), optional `name` แต่ละ row จะถูก validate ตาม Pydantic schema ของ task ส่วน row ที่ไม่ผ่านจะรายงานใน `invalid_rows` ขนาดสูงสุด 10 MiB ต่อ upload |
| `POST` | `/api/v1/datasets/generate` | **Generate ข้อมูลสังเคราะห์** ผ่าน OpenRouter Body เป็น discriminated union ตาม `sdg_mode`: `with_seed` (ต้องมี seed อย่างน้อย 5 row) หรือ `description_only` (ต้องมี `classification_config` / `tool_calling_config` ตาม task) คืน `202` + `{job_id, dataset_id, websocket_url}` row ของ dataset ถูกสร้างทันที โดย `num_samples=0` ก่อน แล้วค่อยอัปเดตเมื่อ worker ทำเสร็จ |
| `GET` | `/api/v1/datasets` | **List datasets** กรองด้วย `project_id` ได้ — มี pagination |
| `GET` | `/api/v1/datasets/{id}` | **อ่าน metadata ของ dataset** (ไม่รวม row) — `storage_uri` จะเป็น `null` จนกว่าการ generate เสร็จ |
| `GET` | `/api/v1/datasets/{id}/preview?limit=20` | **Preview row แรกๆ ของ dataset** stream อ่านทีละบรรทัดจาก MinIO และหยุดเมื่อถึง `limit` (1–200) — `409` ถ้ายังไม่มี row |
| `GET` | `/api/v1/datasets/{id}/download` | **ดาวน์โหลดไฟล์ JSONL ดิบ** เป็น streaming response (`application/x-ndjson`) |
| `DELETE` | `/api/v1/datasets/{id}` | **ลบ dataset** — พยายามลบ object บน MinIO ด้วย (best-effort) แต่ row จะถูกลบแม้ลบ MinIO ไม่ผ่าน คืน `204` |

### Trainings (`/api/v1/trainings`)

แต่ละ training run ที่สำเร็จจะได้ `ModelArtifact` (LoRA adapter) หนึ่งตัว

| Method | Path | คำอธิบาย |
|--------|------|--------------|
| `POST` | `/api/v1/trainings` | **เริ่ม training job** Discriminated ตาม `mode`: `manual` (user ระบุ `manual_config` — lr, epochs, LoRA…) หรือ `hpo` (Optuna ค้นหา hyperparam ใน `hpo_config.search_space` แล้ว retrain ตอนสุดท้ายด้วย best params) คืน `202` ส่วน base model ต้องอยู่ใน `/api/v1/base-models` (ADR-002) |
| `GET` | `/api/v1/trainings` | **List training jobs** ตัวกรอง: `project_id`, `status` (`pending`/`running`/`completed`/`failed`/`cancelled`) — มี pagination |
| `GET` | `/api/v1/trainings/{id}` | **อ่าน training job** พร้อม config เต็ม + status + `mlflow_run_id` (เมื่อพร้อม) |
| `DELETE` | `/api/v1/trainings/{id}` | **Cancel** job ที่ยัง pending หรือ running — revoke Celery task (`SIGTERM`) แล้ว flip row เป็น `cancelled` ทำซ้ำได้ (idempotent) บน job ที่อยู่สถานะ terminal |
| `GET` | `/api/v1/trainings/{id}/mlflow-url` | **คืน URL ของ MLflow run** สำหรับ training นี้ (deep link ไป MLflow UI) — `mlflow_url` เป็น `null` ถ้า run ยังไม่เริ่ม |

### Models (`/api/v1/models`)

`models` ที่นี่หมายถึง **artifact ของโมเดลที่ train เสร็จแล้ว** (หนึ่งตัวต่อ
training ที่สำเร็จ) ทั้ง LoRA adapter และไฟล์ที่ export (GGUF, SafeTensors)
อยู่บน MinIO

| Method | Path | คำอธิบาย |
|--------|------|--------------|
| `GET` | `/api/v1/models` | **List artifacts** กรองด้วย `project_id` ได้ (join ผ่าน TrainingJob) — มี pagination |
| `GET` | `/api/v1/models/{id}` | **อ่าน artifact** พร้อม URI ครบ (`lora_adapter_uri`, `gguf_uri`, `safetensors_uri`, `ollama_model_tag`) |
| `POST` | `/api/v1/models/{id}/export` | **Export เป็น GGUF หรือ SafeTensors** Body: `{format: "gguf" \| "safetensors", quantization?: "q4_k_m" \| …}` GGUF จะลงทะเบียนกับ Ollama daemon ในเครื่องด้วย (best-effort — ถ้า daemon ติดต่อไม่ได้ การ upload ยังเกิดขึ้น แต่ `ollama_model_tag` จะเป็น `null`) คืน `202` |
| `GET` | `/api/v1/models/{id}/download?format=gguf` | **Stream ไฟล์ที่ export ไว้แล้ว** ตอนนี้รองรับเฉพาะ `gguf` (single blob) ส่วน `safetensors` เป็น directory หลายไฟล์ จะคืน `400` พร้อม `s3://` URI ให้ไปดึงจาก MinIO ตรงๆ |

### Inference (`/api/v1/inference`) — OpenAI-compatible

เป็น proxy บางๆ หน้า Ollama daemon ในเครื่อง โดย type เฉพาะ field ที่
แพลตฟอร์มใช้จริงๆ — field อื่นๆ ถูก forbid ไว้

| Method | Path | คำอธิบาย |
|--------|------|--------------|
| `POST` | `/api/v1/inference/chat/completions` | **OpenAI-compat chat completions** — `model` รับได้ทั้ง UUID ของ `ModelArtifact` (แพลตฟอร์มจะ resolve เป็น `ollama_model_tag` ให้) หรือ Ollama tag ตรงๆ (`llama3.2:3b`) `stream: true` จะถูก reject ด้วย `400` — PoC ยังไม่ proxy SSE |
| `POST` | `/api/v1/inference/completions` | **Legacy text completions** (กฎเรื่อง identifier เหมือนข้างบน) |
| `GET` | `/api/v1/inference/models` | **List models** ที่ Ollama daemon ในเครื่องรู้จัก (รูปแบบ OpenAI) |

### Evaluations (`/api/v1/evaluations`)

รัน evaluation โดยส่ง model หนึ่งตัว + dataset หนึ่งตัว — worker จะ predict
ทีละ row ผ่าน Ollama, คำนวณ metric per task แล้วถ้าเปิดใช้ LLM judge จะให้
LLM ให้คะแนนคำตอบเชิงคุณภาพต่อด้วย

| Method | Path | คำอธิบาย |
|--------|------|--------------|
| `POST` | `/api/v1/evaluations` | **เริ่ม evaluation** Body: `{model_artifact_id, dataset_id, use_llm_judge?, judge_model?}` artifact ต้องมี `ollama_model_tag` แล้ว (ต้อง export เป็น GGUF ก่อน) คืน `202` |
| `GET` | `/api/v1/evaluations/{id}` | **อ่านผล evaluation** รวม `metrics_json` (ตัวเลข per task) และ `llm_judge_score` (mean ข้าม row, ถ้าเปิด judge ไว้) |
| `POST` | `/api/v1/evaluations/compare` | **เปรียบเทียบ evaluation 2–10 runs** Body: `{evaluation_ids: [UUID, ...]}` คืน pivot: `{metric_name: {evaluation_id: value \| null}, judge_scores: {evaluation_id: value \| null}}` |

#### Metric ต่อ task

| Task | Metric ที่ออก |
|------|----------------|
| Classification | `accuracy`, `f1_macro`, `f1_per_label` (dict), `confusion_matrix`, `out_of_set_predictions`, `n` |
| Tool calling | `json_validity`, `name_accuracy`, `arg_accuracy` (เงื่อนไข: เฉพาะ row ที่ name ตรง), `exact_match`, `n` |
| QA | `exact_match`, `rouge1`, `rouge2`, `rougeL`, `bleu`, `n` |

อัตราส่วนทุกตัวอยู่ในช่วง `[0, 1]`

### Metadata (`/api/v1/tasks`, `/api/v1/base-models`)

Catalog แบบ static — ใช้สร้าง form แบบ dynamic บน frontend ไม่ต้องผ่าน DB
หรือ Celery

| Method | Path | คำอธิบาย |
|--------|------|--------------|
| `GET` | `/api/v1/tasks` | List **task type ที่รองรับทั้ง 3 แบบ** พร้อม JSON Schema ของแต่ละ task และ row ตัวอย่าง (ใช้ขับ form upload seed) |
| `GET` | `/api/v1/tasks/{task_type}/example` | คืน **row ตัวอย่าง** ของ task เดียว |
| `GET` | `/api/v1/base-models` | List **base model ที่รองรับทั้ง 6 ตัว** (Llama 3.2 1B/3B, Qwen2.5 0.5B/1.5B/3B, Gemma 2 2B — ทั้งหมดเป็น Unsloth 4-bit) Llama 3.2 3B เป็น default ตาม ADR-002 |

### System

| Method | Path | คำอธิบาย |
|--------|------|--------------|
| `GET` | `/health` | **Liveness probe** คืน `200 {"status": "ok"}` ตลอดถ้า API process ยังอยู่ ไม่ probe Postgres / Redis |
| `GET` | `/` | JSON เล็กๆ ชี้ไป `/docs` |

---

## WebSocket — `/ws/jobs/{job_id}`

Subscribe ด้วย `job_id` จาก response 202 ใดๆ — server forward message จาก
Redis channel `job:{job_id}` มาตรงๆ ดังนั้น schema ของข้อมูล wire คือ
`api.schemas.progress.WSMessage` ทุกประการ

```
GET /ws/jobs/c4f8…  →  WS upgrade → forward message จนกว่าจะจบ
```

### ประเภท message (field `type` คือ discriminator)

| `type` | ส่งเมื่อ | Field สำคัญ |
|--------|--------------|------------|
| `sdg_progress` | หลัง batch ทุกครั้งระหว่าง SDG | `phase` (`generating`/`validating`/`deduplicating`/`persisting`), `samples_generated`, `samples_target`, `samples_valid`, `samples_rejected`, `duplicates_removed` |
| `training_progress` | ทุก `on_log` ของ Trainer (per-step) | `epoch`, `epochs_total`, `step`, `steps_total`, `train_loss`, `eval_loss`, `learning_rate`, `samples_per_second`, `gpu_memory_mb` |
| `hpo_progress` | ตอนจบ Optuna trial แต่ละครั้ง | `trial_number`, `trials_total`, `current_params`, `best_value`, `best_params`, `last_trial_value`, `last_trial_pruned`, `inner_progress` (`training_progress` ซ้อนใน — nullable) |
| `completed` | สถานะสุดท้าย — งานสำเร็จ | `result` (รูปแบบ free-form ตาม task), optional `mlflow_run_id`, `dataset_id`, `model_artifact_id` |
| `failed` | สถานะสุดท้าย — งาน raise | `error`, `error_type` |

Client ควรถือ `completed` / `failed` เป็นสัญญาณปิดและ disconnect

> **ไม่มี replay ตอน reconnect** ถ้า client พลาด message ให้ refresh state
> ผ่าน read endpoint (`GET /datasets/{id}` ฯลฯ) message เฉพาะที่ publish
> *หลังจาก* connect เท่านั้นที่จะถูก forward

---

## Flow ตัวอย่าง

### A — Classification (description-only SDG, manual training)

```
1. POST /projects                {task_type: "classification"}
2. POST /datasets/generate       {sdg_mode: "description_only",
                                  classification_config.labels: [...] }
   → 202 + ws://.../ws/jobs/<job_id>  → รอ completed
3. POST /trainings               {mode: "manual", manual_config: {...}}
   → 202 + ws://.../ws/jobs/<job_id>  → รอ completed
4. POST /models/{id}/export      {format: "gguf"}
   → 202 → รอ completed (ตอนนี้ ollama_model_tag มีค่าแล้ว)
5. POST /evaluations             {model_artifact_id, dataset_id}
   → 202 → รอ completed → GET /evaluations/{id} → metrics_json
6. POST /inference/chat/completions  {model: <model_artifact_id>, messages: [...]}
```

### B — QA (with-seed SDG, HPO training, LLM judge)

```
1. POST /projects                {task_type: "qa"}
2. POST /datasets/upload-seed    (multipart JSONL, ≥5 row)
3. POST /datasets/generate       {sdg_mode: "with_seed",
                                  seed_data: <inline> }   ← หรือใช้ dataset seed ตรงๆ ได้
4. POST /trainings               {mode: "hpo",
                                  hpo_config: {n_trials, search_space, ...}}
   → ws ส่ง hpo_progress per trial; completed result.best_metric_value
5. POST /models/{id}/export      {format: "gguf", quantization: "q4_k_m"}
6. POST /evaluations             {model_artifact_id, dataset_id,
                                  use_llm_judge: true}
   → llm_judge_score ออกมาคู่กับ metrics_json
```

### C — Tool calling (description-only พร้อม tools)

```
1. POST /projects                {task_type: "tool_calling"}
2. POST /datasets/generate       {sdg_mode: "description_only",
                                  tool_calling_config.tool_definitions: [
                                    {name, description, parameters}, ...
                                  ]}
3. POST /trainings               {mode: "manual"}
4. POST /models/{id}/export      {format: "gguf"}
5. POST /inference/chat/completions   ← list ของ tool ถูกฝังใน SYSTEM
                                       prompt ของ Ollama model ที่ลงทะเบียนแล้ว
6. POST /evaluations
   → metrics_json มี json_validity, name_accuracy, arg_accuracy
```

---

## Pagination

ทุก endpoint ที่ list คืน envelope แบบเดียวกัน:

```json
{
  "items": [ ... ],
  "total": 137,
  "limit": 50,
  "offset": 0
}
```

`limit` มีเพดาน 200; `offset` ไม่จำกัด แต่จะเสถียรเฉพาะตราบเท่าที่
ลำดับการเรียง (creation time, descending) ยังเหมือนเดิม

---

## Status Code — เจอเมื่อไหร่บ้าง

| Operation | Happy path | ที่พลาดบ่อย |
|-----------|------------|-----------------|
| สร้าง resource | `201` | `422` (validation), `404` (parent ไม่มี) |
| อ่าน resource | `200` | `404` |
| List | `200` | — |
| Update (PATCH) | `200` | `404`, `422` |
| Delete | `204` | `404` |
| Submit งาน async | `202` | `404` (parent ไม่มี), `409` (dependency ยังไม่พร้อม), `422` (validation), `502` (Ollama ล่ม — เฉพาะ inference) |
| Cancel งาน | `202` | `404` (และคืนสถานะ terminal เดิมแบบ idempotent) |
| Download | `200` (stream) | `404`, `409` (ยังไม่ export), `400` (รูปแบบเป็น multi-file) |

---

## OpenAPI

Schema แบบ machine-readable: `GET /openapi.json` — ส่ง URL นี้ให้ frontend
Swagger UI: `/docs` — ReDoc: `/redoc` — `openapi_tags` จะจัดกลุ่ม endpoint
ตามแบบเดียวกับในเอกสารฉบับนี้
