# Guidebook E2E — SLM Fine-Tuning Platform

> คู่มือทดสอบ **end-to-end** ผ่าน Swagger UI ของ SLM Fine-Tuning Platform
> ใช้ทีละ **node** เริ่มจาก Node 1 → ไหลตามรูป workflow ด้านล่าง
> ถ้าพังที่ node ไหน — เปิดไฟล์ของ node นั้นใน [`sub-node/`](./sub-node/) เพื่อดู request/response ที่คาดหวัง + error cases

---

## Workflow Diagram

![Project Workflow](../../project-workflow.png)

> ถ้ารูปไม่ขึ้น — เปิดไฟล์ตรงๆ ที่ [`../../project-workflow.png`](../../project-workflow.png)

---

## สารบัญ 11 Nodes

| # | Node | Type | Swagger Endpoint | Guide |
|---|------|------|------------------|-------|
| 1 | Create Project | Action | `POST /api/v1/projects` | [📄 1_create-project.html](./sub-node/1_create-project.html) |
| 2 | gen data condition | Decision | _(เลือกใน body ของ node ถัดไป)_ | [📄 2_gen-data-condition.html](./sub-node/2_gen-data-condition.html) |
| 3a | add seed data (json/jsonl/pdf) | Action | `POST /api/v1/datasets/upload-seed` | [📄 3a_add-seed-data.html](./sub-node/3a_add-seed-data.html) |
| 3b | description only | Action | `POST /api/v1/datasets/generate` (`sdg_mode=description_only`) | [📄 3b_description-only.html](./sub-node/3b_description-only.html) |
| 4 | SDG + Holdout Split (MINHASH) | Action + async | `POST /api/v1/datasets/generate` (+ `holdout_size`) | [📄 4_sdg-holdout-split.html](./sub-node/4_sdg-holdout-split.html) |
| 5 | synthetic data (verify artifact) | Inspect | `GET /api/v1/datasets/{id}` (parent + holdout child) | [📄 5_verify-dataset.html](./sub-node/5_verify-dataset.html) |
| 6 | Training (manual / HPO) | Action + async | `POST /api/v1/trainings` + `WS /ws/jobs/{job_id}` | [📄 6_training.html](./sub-node/6_training.html) |
| 7 | Export GGUF + Register Ollama | Action + async | `POST /api/v1/models/{id}/export` | [📄 7_export-gguf.html](./sub-node/7_export-gguf.html) |
| 8 | Serve / Inference | Action | `POST /api/v1/inference/chat/completions` | [📄 8_inference.html](./sub-node/8_inference.html) |
| 9 | Evaluation (rule-based) | Action + async | `POST /api/v1/evaluations` (`use_llm_judge=false`) | [📄 9_evaluation.html](./sub-node/9_evaluation.html) |
| 10 | LLM as Judge | Action + async | `POST /api/v1/evaluations` (`use_llm_judge=true`) | [📄 10_llm-judge.html](./sub-node/10_llm-judge.html) |
| 11 | MLflow tracking (ขนาน) | Inspect | `GET /api/v1/trainings/{id}/loss-history` + `/metrics` | [📄 11_mlflow-tracking.html](./sub-node/11_mlflow-tracking.html) |

---

## ลำดับการทดสอบที่แนะนำ

```
Node 1 → Node 2 → ┬─ Node 3a ─┐
                  └─ Node 3b ─┘ → Node 4 → Node 5 → Node 6 → Node 7 → Node 8 → Node 9 → Node 10
                                                       │
                                                       └─ (ขนาน) Node 11
```

**Notes:**
- **Node 2** เป็นจุดตัดสินใจ — เลือกไป 3a หรือ 3b
  - **3a (With Seed)** + 4 = ใช้ seed file เป็น few-shot
  - **3b (Description Only)** = ข้าม 3a, request เดียวกับ Node 4 แต่ใส่ `sdg_mode=description_only`
- **Node 11** รันคู่ขนานกับ Node 6 ได้ (เปิดดู loss curve ขณะเทรน)
- **Node 10** เป็น optional — ถ้า rule-based metrics จาก Node 9 พอแล้วก็ไม่ต้องรัน

---

## ค่าที่ต้องเก็บไว้ระหว่างทำ test

| จาก Node | เก็บค่า | ใช้ใน Node |
|----------|---------|-----------|
| 1 | `project_id` | 3a, 3b, 4, 6 |
| 3a | `seed_dataset_id` | 4 |
| 4 | `train_dataset_id` (parent) | 5, 6 |
| 4 | `holdout_dataset_id` (child) | 5, 9, 10 |
| 4, 6, 7, 9, 10 | `job_id` | WebSocket subscribe |
| 6 | `training_id` | 11 |
| 6 | `model_artifact_id` | 7, 9, 10 |
| 7 | `ollama_model_tag` | 8 |
| 7 | `base_ollama_tag` | 8 (A/B compare) |

---

## Seed data ที่เตรียมไว้ใน `seed_data/`

3 task types — ทุกไฟล์มาในรูปแบบ `_canonical` (keys ตรง schema) และ `_mismatched` (keys ผิด — สำหรับทดสอบ Format Detection)

| Task | Domain | Files | Labels / Tools |
|------|--------|-------|---------------|
| `classification` | Thai customer support ticket (40 rows) | `classification_canonical.{json,jsonl}` <br> `classification_mismatched.{json,jsonl}` | `ปัญหาการเงิน`, `ปัญหาเทคนิค`, `คำถามทั่วไป` |
| `qa` | Thai return policy (40 rows) + 1 PDF research paper | `qa_canonical.{json,jsonl}` <br> `qa_mismatched.{json,jsonl}` <br> `2503.14023v2.pdf` | — |
| `tool_calling` | English smart home commands (40 rows) | `tool_calling_canonical.{json,jsonl}` <br> `tool_calling_mismatched.{json,jsonl}` | `set_oven`, `start_timer`, `set_volume`, `play_music`, `light_on` |

**Mismatched keys ที่ใช้ทดสอบ Format Detection:**
- `classification`: `message` → `text`, `category` → `label`
- `qa`: `prompt` → `question`, `response` → `answer`
- `tool_calling`: `instruction` → `question`, `function_call` → `answer`

---

## รายงาน bug

ถ้าพังที่ node ไหน — แจ้งมาแบบนี้เพื่อให้แก้ได้เร็ว:

```
Node:        <เลข node>
Endpoint:    <method + path>
Request:     <body ที่ส่ง>
Status:      <code ที่ได้>
Response:    <body ที่ได้>
WS events:   <ถ้ามี — copy 3-5 events สุดท้าย>
Expected:    <สิ่งที่คาดว่าจะได้>
```

---

## Reference

- **API spec ฉบับสมบูรณ์:** [`../runbooks/api_docs.md`](../runbooks/api_docs.md)
- **Live OpenAPI spec:** `http://<host>:8000/openapi.json`
- **Swagger UI:** `http://<host>:8000/docs`
- **MLflow UI:** `http://<host>:5000`
- **Manual test runbooks (แบบเก่า):** [`../runbooks/`](../runbooks/) (sdg-test-*, training-*, evaluation.md, ฯลฯ)
