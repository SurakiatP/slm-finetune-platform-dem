# Model Export Extras Runbook — SafeTensors + Binary Download + Legacy /completions + Cancel

> **Goal:** ทดสอบ endpoints ที่ `training-manual-lifecycle.md` ไม่ครอบคลุม — SafeTensors export format, `/download` binary stream, legacy `/completions` (text not chat), `GET /inference/models`, `DELETE training` cancel mid-flight
> **Estimated time:** 5-10 นาที
> **Cost:** $0 (ไม่ใช้ LLM)
> **Prerequisites:** Stack รันอยู่ + มี trained artifact (จาก `training-manual-lifecycle.md` Task 1-9 ขั้นต่ำ)

---

## 0. Pre-flight checklist

### 0.1 Stack ขึ้น

```bash
ssh -p <vast-port> root@<vast-ip> "docker compose ps && curl -sf http://localhost:8000/health"
```

### 0.2 SSH port forwards

```powershell
ssh -p <vast-port> root@<vast-ip> -L 8000:localhost:8000 -L 9001:localhost:9001
```

### 0.3 Tabs ที่ต้องเปิด

| URL | จุดประสงค์ |
|-----|-----------|
| http://localhost:8000/docs | Swagger UI |
| http://localhost:9001 | MinIO console — verify SafeTensors path |
| Terminal #2: `docker compose logs -f worker` | ดู export task progress |

### 0.4 หา artifact ที่จะใช้

ถ้าจบ `training-manual-lifecycle.md` แล้ว → ใช้ `<artifact_id>` เดิมต่อได้

🔖 **เก็บ:** `<artifact_id>` (ต้องเป็น artifact ที่มี `lora_adapter_uri` populated; ไม่จำเป็นต้อง export GGUF มาก่อน)

---

## Task 1 — Export SafeTensors

**วัตถุประสงค์:** export merged HF model เป็น SafeTensors (สำหรับ HuggingFace inference / external tools)

### Steps

```
POST /api/v1/models/<artifact_id>/export
{
  "format": "safetensors"
}
```

### ✅ Expected response (202)

```json
{
  "artifact_id": "<artifact_id>",
  "format": "safetensors",
  "job_id": "<celery-task-uuid>",
  "status": "pending",
  "websocket_url": "/ws/jobs/<job_id>"
}
```

🔖 **เก็บ `job_id`** (สำหรับดู WebSocket progress ถ้าต้องการ)

### 🧪 Verification

| Check | Expected |
|-------|----------|
| HTTP status | 202 |
| `format` | `"safetensors"` |

---

## Task 2 — Poll SafeTensors export until `safetensors_uri` set (~30-90 วินาที)

**วัตถุประสงค์:** รอ merge + upload เสร็จ + ตรวจ MinIO

### Steps

(poll ทุก 10 วินาที)

```
GET /api/v1/models/<artifact_id>
```

### ✅ Expected สุดท้าย

```json
{
  "id": "<artifact_id>",
  "lora_adapter_uri": "s3://models/adapters/<training_id>",
  "gguf_uri": "<value or null — ขึ้นกับว่าเคย export gguf หรือยัง>",
  "safetensors_uri": "s3://models/exports/<artifact_id>/safetensors",
  "ollama_model_tag": "<value or null>",
  "export_error_message": null,
  "size_mb": ≈ 20
}
```

### 🧪 Verification

| Check | Where | Expected |
|-------|-------|----------|
| `safetensors_uri` populate | response | `s3://models/exports/<artifact_id>/safetensors` |
| `export_error_message` | response | `null` |
| Folder MinIO | http://localhost:9001 → bucket `models` → `exports/<artifact_id>/safetensors/` | มี `model.safetensors` (≈ 800MB f16 merged) + `config.json` + `tokenizer*` files |

> 💡 SafeTensors export เก็บ **merged** model (LoRA + base พับเข้าด้วยกัน), ไม่ใช่ adapter เดี่ยว ๆ. ขนาด ~ 800MB-1.5GB ขึ้นกับ base model

---

## Task 3 — Download binary stream

**วัตถุประสงค์:** ดึงไฟล์ที่ export ออกมา ผ่าน `/download` (streaming)

### Steps

**ใน Swagger UI:**

```
GET /api/v1/models/<artifact_id>/download
```

→ กด **Try it out** → **Execute**
→ Swagger จะแสดง response body (binary) — โหลด อาจช้า เพราะไฟล์ใหญ่

**ใน Terminal (แนะนำสำหรับไฟล์ใหญ่):**

```bash
curl -fL -o /tmp/model.bin http://localhost:8000/api/v1/models/<artifact_id>/download
ls -lh /tmp/model.bin
```

### ✅ Expected

```
-rw-r--r-- 1 root root 770M ... /tmp/model.bin   # ถ้า GGUF
หรือ
-rw-r--r-- 1 root root 1.5G ... /tmp/model.bin   # ถ้า SafeTensors merged
```

### 🧪 Verification

| Check | Expected |
|-------|----------|
| HTTP status | 200 |
| `Content-Type` | `application/octet-stream` |
| File size | > 100 MB (ปกติ 770MB GGUF / 1.5GB SafeTensors) |
| Stream behavior | byte ๆ ทยอยมา ไม่ใช่ buffer all-at-once (ใช้ `httpx.stream` หรือ `curl` ก็เห็น progress) |

> 💡 ระบบจะ pick ไฟล์ที่เพิ่ง export ล่าสุด (gguf หรือ safetensors). หาก artifact มีทั้งสอง — ลองทั้งคู่ ผ่าน param หรือ pick ตาม priority ของ implementation

---

## Task 4 — `POST /inference/completions` — Legacy text completion

**วัตถุประสงค์:** ทดสอบ legacy endpoint (ไม่ใช่ chat format) — สำหรับ tools เก่าที่ยังใช้ pre-chat OpenAI API

### Steps

```
POST /api/v1/inference/completions
{
  "model": "<artifact_id>",
  "prompt": "The capital of France is",
  "max_tokens": 20,
  "temperature": 0.0
}
```

### ✅ Expected response (200)

```json
{
  "id": "cmpl-<...>",
  "object": "text_completion",
  "created": 1746...,
  "model": "slm/<id8>",
  "choices": [
    {
      "index": 0,
      "text": " Paris.",
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": ≈ 15,
    "completion_tokens": ≈ 3,
    "total_tokens": ≈ 18
  }
}
```

### 🧪 Verification

| Check | Expected |
|-------|----------|
| HTTP status | 200 |
| `object` | `"text_completion"` (ต่างจาก chat ที่เป็น `"chat.completion"`) |
| `choices[0].text` | มีคำว่า `"Paris"` (continuation จาก prompt) |
| ไม่มี `choices[0].message` field | ✓ (legacy ไม่ใช่ chat) |

> 💡 Difference จาก `/chat/completions`:
> - `prompt: string` (legacy) vs `messages: [...]` (chat)
> - `choices[].text` (legacy) vs `choices[].message.content` (chat)

---

## Task 5 — Cancel training mid-flight

**วัตถุประสงค์:** ทดสอบ `DELETE /trainings/{id}` ตอน training กำลังรัน

### Steps

1. **Submit training ใหม่** (long-running เพื่อมีเวลา cancel):

```
POST /api/v1/trainings
{
  "mode": "manual",
  "project_id": "<project_id>",
  "dataset_id": "<dataset_id>",
  "base_model": "unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
  "training_name": "cancel-smoke",
  "manual_config": {
    "num_train_epochs": 5,
    "per_device_train_batch_size": 1,
    "gradient_accumulation_steps": 1,
    "max_seq_length": 512
  }
}
→ 202, เก็บ <cancel_training_id>
```

2. **Poll until status=running** (โดยใช้ Swagger ส่งซ้ำ):

```
GET /api/v1/trainings/<cancel_training_id>
```

ทำซ้ำทุก 2-3 วินาที จนเห็น `status: "running"` (ปกติ ~5-10 วินาที)

3. **DELETE ทันที:**

```
DELETE /api/v1/trainings/<cancel_training_id>
```

### ✅ Expected response (202)

(202 Accepted — cancel ส่ง SIGTERM ไป Celery worker; การ flip status จะเสร็จใน 1-2 วินาที)

### 🧪 Verification (ตรวจหลัง DELETE)

```
GET /api/v1/trainings/<cancel_training_id>
```

| Check | Expected |
|-------|----------|
| `status` หลัง DELETE | `"cancelled"` (ภายใน 5 วินาที) |
| `ended_at` | populate |
| `error_message` | `null` หรือ "cancelled by user" |

---

## Task 6 — Idempotent re-DELETE

**วัตถุประสงค์:** DELETE ซ้ำบน training ที่ cancel แล้ว — ห้าม 500

### Steps

(หลัง Task 5)

```
DELETE /api/v1/trainings/<cancel_training_id>
```

### ✅ Expected response (200, 202, 204, 404, หรือ 409)

ระบบควรคืนหนึ่งใน:
- 200/202/204 — idempotent successful
- 409 Conflict — "already terminal"
- 404 Not Found — ถ้าระบบ treat cancelled = "no longer cancellable"

**ห้ามเป็น 500 / 502 / 503**

### 🧪 Verification

| Check | Expected |
|-------|----------|
| HTTP status | NOT 5xx |
| ถ้ามี body | error envelope (`detail` + `code`) |

---

## Task 7 — `GET /api/v1/inference/models` — List Ollama models

**วัตถุประสงค์:** ดู models ใน Ollama (รวม base + slm/* fine-tuned)

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
| HTTP status | 200 |
| `data` ≥ 1 row (ถ้าเคย export GGUF + register) | ✓ |
| ไม่มี slm tag → `data: []` | ✓ (Bug MT.B1 regression check — ห้ามเป็น 500) |

> 🚨 **Bug MT.B1 regression:** ถ้า Ollama ว่าง → response = `{"data":[]}` ไม่ใช่ 500. ทดสอบกับ instance ที่ Ollama เพิ่ง deploy + ยังไม่มี model — ต้องคืน 200 + empty array

---

## Task 8 — Negative: download artifact ที่ยังไม่ export

**วัตถุประสงค์:** ลองดาวน์โหลด artifact ที่มี LoRA แต่ไม่มี GGUF/SafeTensors — ระบบควร 409 หรือ 404

### Steps

1. หา artifact ที่ `gguf_uri = null AND safetensors_uri = null` (สร้าง training ใหม่ไม่ export)
2. ลอง `GET /api/v1/models/<unexported_artifact_id>/download`

### ✅ Expected response (404 หรือ 409)

```json
{
  "detail": "Artifact ... has no exported file (call POST /export first)",
  "code": "conflict"
}
```

หรือ 404 ถ้า implementation treat ว่า "no file = not found"

### 🧪 Verification

| Check | Expected |
|-------|----------|
| HTTP status | NOT 200 (no broken stream); NOT 500 |
| Error envelope shape | ปกติ (`detail` + `code`) |

---

## Task 9 — Negative: export ที่ artifact ไม่มี LoRA → 409

**วัตถุประสงค์:** ระบบ guard ก่อน enqueue export task ถ้า artifact ไม่มี adapter

### Steps

(หาทาง — ปกติทุก artifact ที่ training completed จะมี LoRA. Skip ถ้าไม่มี artifact ที่ตรงเงื่อนไข; เป็น optional check)

```
POST /api/v1/models/<no-lora-artifact-id>/export
{ "format": "gguf", "quantization": "q4_k_m" }
```

### ✅ Expected response (409)

```json
{
  "detail": "Model ... has no LoRA adapter on file — training likely never completed",
  "code": "conflict"
}
```

---

## ✅ Test Completion Checklist

- [ ] Task 1 — POST safetensors export → 202
- [ ] Task 2 — poll → `safetensors_uri` populate, MinIO เห็น `model.safetensors` ≈ 800MB-1.5GB
- [ ] Task 3 — `/download` stream → file ขนาด > 100MB save สำเร็จ
- [ ] Task 4 — legacy `/completions` → 200 + `text` field (ไม่ใช่ `message.content`)
- [ ] Task 5 — submit + DELETE training → status `cancelled`
- [ ] Task 6 — idempotent re-DELETE → NOT 500
- [ ] Task 7 — list inference models → 200 + `data` array (เห็น slm/* tags)
- [ ] Task 8 — download unexported → 404/409 (ไม่ broken stream)
- [ ] (Optional) Task 9 — export no-LoRA → 409

ผ่านครบ 8 ข้อหลัก = Export + extras endpoints สมบูรณ์ ✅

---

## Troubleshooting

| อาการ | สาเหตุที่เป็นไปได้ | แก้ |
|-------|------------------|-----|
| Task 2 → `export_error_message` ไม่ใช่ null | merge step fail (ตัวเดียวกับ B6 GGUF) | check celery log; ปกติ regression ของ `save_pretrained_merged` หรือ Unsloth FastLanguageModel adapter loader |
| Task 2 → `safetensors_uri` ไม่ populate แต่ no error | upload to MinIO ติดขัด | check MinIO healthy + worker network ไป MinIO ได้ |
| Task 3 download ขาด / timeout | response not streamed | ใช้ `curl` แทน Swagger; ดู response header `transfer-encoding: chunked` |
| Task 3 file ขนาดเล็ก (< 100KB) | error page ถูก stream | check first 1KB ด้วย `head -c 1024 /tmp/model.bin \| od -c \| head` |
| Task 4 → `text` หายไป / `choices[0].message` มี | server sent chat shape (regression) | check `api/services/inference_service.py` `complete()` ใช้ `/v1/completions` ไม่ใช่ `/v1/chat/completions` ของ Ollama |
| Task 5 status ไม่เปลี่ยน cancelled (timeout) | Celery revoke ไม่ทำงาน / SIGTERM block | check `docker compose logs worker` หา signal received; force kill = `docker compose restart worker` |
| Task 5 stuck `running` หลัง DELETE | training อยู่ใน mid-step ของ Unsloth ที่ block signal | wait 30s; ถ้ายัง stuck → restart worker container |
| Task 6 → 500 | regression — DELETE ไม่ idempotent | check `api/services/training_service.py` `cancel_training()` handle terminal states |
| Task 7 → 500 (Bug MT.B1 regression) | Ollama คืน `data: null` | check `api/services/inference_service.py` `raw.get("data") or []` |

---

## Cost Estimate

| Task | LLM ที่ใช้ | API calls | ค่าประมาณ |
|------|-----------|-----------|----------|
| Task 1-9 | (none — Ollama in-cluster, no OpenRouter) | 0 | $0 |

**ทั้ง runbook ฟรี** — ใช้ smoke ทุกครั้งหลัง redeploy ก็ไม่กิน OpenRouter quota

> 💡 GPU compute สำหรับ Task 1 (SafeTensors merge) ใช้ ~30s บน 1B model. Task 5 cancel mid-flight = อาจกิน 5-10s ของ training compute ก่อน cancel จริง

---

## ⏭️ Cross-runbook tips

- **เทียบ GGUF vs SafeTensors size:** Task 2 ของ runbook นี้ + Task 11 ของ `training-manual-lifecycle.md` → ดูว่า quant ลด size เท่าไหร่ (ปกติ q4_k_m ~ 1/4 ของ f16 merged)
- **Cancel test กับ HPO:** ลอง DELETE HPO training (จาก `training-hpo.md` Task 1) ตอน trial ที่ 1 รัน → ควร cancel cleanly ไม่ทิ้ง partial nested run ค้างใน MLflow
