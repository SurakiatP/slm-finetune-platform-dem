# API Deep Analysis Report — Smart Model Tune (SLM Studio)

**วันที่จัดทำ:** 2026-05-06  
**จุดประสงค์:** อธิบายการทำงานของ API แต่ละตัวอย่างละเอียด และวิเคราะห์ว่าตัวไหนพึ่งพา External API เพิ่มเติม vs. ทำจบในระบบได้เลย

---

## สัญลักษณ์ที่ใช้

| สัญลักษณ์ | ความหมาย |
|:---:|---|
| ✅ | ทำจบภายในระบบได้เลย (Supabase only) |
| ⚠️ | ทำได้บางส่วน แต่ต้องพึ่ง External API เพื่อให้สมบูรณ์ |
| ❌ | ยังไม่ implement / ต้องพึ่ง External API ทั้งหมด |

---

## ส่วนที่ 1 — Backend APIs (Supabase) วิเคราะห์เชิงลึก

---

### API-01 | Authentication API
**ไฟล์:** `src/contexts/AuthContext.tsx`  
**สถานะ:** ✅ ทำจบในระบบได้เลย

#### การทำงานแบบ Step-by-Step

```
[Browser เปิดแอป]
       │
       ▼
supabase.auth.getSession()
       │
       ├─► มี session → setUser(user) → loadProfile(userId) → setProfile(data)
       │
       └─► ไม่มี session → user = null → redirect ไป /login

[User กรอก Email/Password]
       │
       ▼
supabase.auth.signInWithPassword()  ← จัดการโดย Supabase Auth เอง
       │
       ▼
onAuthStateChange() fires → setSession → loadProfile

[loadProfile(userId)]
       │
       ▼
SELECT id, user_id, display_name, avatar_url
FROM profiles
WHERE user_id = ?
```

#### Data Flow
- **Input:** email + password (จาก Login form)
- **Output:** `Session` object (JWT) + `User` object + `Profile` (display_name, avatar_url)
- **JWT เก็บใน:** `localStorage` (ตั้งค่าใน `client.ts`: `storage: localStorage`)
- **Auto-refresh:** ใช้ `autoRefreshToken: true` — Supabase จะ refresh JWT อัตโนมัติก่อนหมดอายุ

#### ข้อจำกัดปัจจุบัน
- ยังไม่มี `signUp()` ที่เชื่อม profile creation — ต้องใช้ Supabase Function `seed_user_demo_data` แทน
- ไม่มี OAuth (Google/GitHub) ในโค้ดปัจจุบัน

#### ต้องพึ่ง External API ไหม?
> ไม่ — Supabase Auth ครอบคลุมทุกอย่างในตัว

---

### API-02 | Projects API
**ไฟล์:** `src/lib/projectsApi.ts` + `src/hooks/useProjects.ts`  
**สถานะ:** ⚠️ CRUD ทำจบในระบบได้ แต่ `createProject` ต้องพึ่ง Engine API เพื่อเริ่ม training จริง

#### การทำงานแบบ Step-by-Step

**`listProjects()` — ดึง Project ทั้งหมด**
```
SELECT *
FROM projects
WHERE user_id = (current user via RLS)
ORDER BY updated_at DESC
→ map snake_case → camelCase
→ return Project[]
```

**`createProject(input)` — สร้าง Project ใหม่**
```
[User กรอก Wizard ใน NewProject.tsx]
       │
       ▼
validatePreflight(input)  ← ตรวจ file format, size, prompt length
       │
       ├─► ถ้า error → แสดง error message, ไม่ส่ง
       │
       ▼
supabase.auth.getUser()  ← ดึง user_id ปัจจุบัน
       │
       ▼
INSERT INTO projects (
  user_id, name, description, task_type,
  base_model, epochs, learning_rate,
  dataset_size, tags,
  status = "queued",    ← เริ่มต้นเสมอ
  progress = 0
)
       │
       ▼
return Project (พร้อม id ที่ DB สร้าง)

       ⚠️ ขาดขั้นตอนนี้:
       │
       ▼
POST https://engine.slmstudio.ai/v1/jobs
{
  "project_id": project.id,
  "base_model": "...",
  "task_type": "...",
  "dataset_url": "...",
  "epochs": 3,
  "learning_rate": 2e-4
}
→ ได้ job_id กลับมา
→ UPDATE projects SET job_id = ? WHERE id = ?
```

**`updateProject(id, patch)` — อัปเดต Field ใดก็ได้**
```
รับ patch object (camelCase) → แปลงเป็น snake_case → UPDATE projects SET ... WHERE id = ?
ใช้โดย: useTrainingSimulator (อัปเดต progress/status)
         NewProject wizard (อัปเดต config)
         Dashboard (toggle pinned)
```

**`deleteProject(id)` — ลบ Project**
```
DELETE FROM projects WHERE id = ?
→ Cascade ไป datasets (FK: datasets.project_id)
→ Cascade ไป trained_models (FK: trained_models.project_id)
```

#### ต้องพึ่ง External API ไหม?
> **createProject** → ต้องพึ่ง `engine.slmstudio.ai/v1/jobs` เพื่อเริ่ม fine-tuning จริง  
> **list / get / update / delete** → ทำจบในระบบได้เลย

---

### API-03 | Models API
**ไฟล์:** `src/lib/modelsApi.ts`  
**สถานะ:** ⚠️ การ Read ทำจบในระบบ แต่ข้อมูลใน DB ต้องถูกสร้างโดย Engine

#### การทำงานแบบ Step-by-Step

**`listModels()` — ดึง Model ทั้งหมด**
```
SELECT *
FROM trained_models
WHERE user_id = (RLS)
ORDER BY created_at DESC
→ map row → TrainedModelExt
→ return TrainedModelExt[]
```

**`getModel(id)` — ดึง Model เดียว**
```
SELECT *
FROM trained_models
WHERE id = ? AND user_id = (RLS)
→ return TrainedModelExt | null
```

#### ปัญหาสำคัญ: ข้อมูลใน `trained_models` มาจากไหน?

```
ปัจจุบัน (ไม่สมบูรณ์):
  ตารางว่าง หรือมีแค่ seed_user_demo_data() ที่ inject mock data

ควรเป็น:
  เมื่อ Engine training เสร็จ → Engine call กลับมา (Webhook หรือ Polling) →
  INSERT INTO trained_models (
    user_id, project_id, name, base_model, task_type,
    accuracy, f1_score, precision, recall,
    latency_ms, file_size, format,
    status = "ready"
  )
```

**Fields ที่ Engine ต้องส่งกลับมา:**
- `accuracy`, `f1_score`, `precision`, `recall` — metrics จาก validation set
- `latency_ms` — ความเร็ว inference ที่ Engine วัด
- `file_size`, `format` — ขนาดและ format ของ model file (เช่น GGUF, ONNX)

#### ต้องพึ่ง External API ไหม?
> **การ read** → ทำจบในระบบ  
> **ข้อมูลต้นทาง** → ต้องมาจาก Engine (Webhook callback หรือ Polling หลัง training)

---

### API-04 | Datasets API
**ไฟล์:** `src/lib/datasetsApi.ts`  
**สถานะ:** ⚠️ Metadata CRUD ทำจบในระบบ แต่ File Content ต้องพึ่ง Supabase Storage

#### การทำงานแบบ Step-by-Step

**`listDatasets()` — ดึง Dataset Metadata**
```
SELECT *
FROM datasets
WHERE user_id = (RLS)
ORDER BY created_at DESC
→ return UserDataset[]
```

**UserDataset มีแค่ Metadata:**
```typescript
{
  id, projectId, name, description,
  rows,          // จำนวนแถว (ตัวเลขเท่านั้น)
  columns,       // จำนวนคอลัมน์
  fileSize,      // เช่น "2.4 MB" (string)
  format,        // "csv" | "jsonl" | "json"
  qualityScore,  // 0-100
  createdAt
}
```

#### ปัญหา: ไม่มี File Upload / Download API

```
ปัจจุบัน:
  ไม่มี INSERT/UPDATE datasets ใน datasetsApi.ts
  ไม่มี Supabase Storage call ใดเลย
  DatasetInsights ใช้ datasetExpander.ts (mock rows) แทน

ควรเป็น:
  [User อัปโหลดไฟล์ใน NewProject wizard]
          │
          ▼
  supabase.storage
    .from("datasets")
    .upload(`${userId}/${datasetId}.csv`, file)
          │
          ▼
  INSERT INTO datasets (
    user_id, project_id, name, format,
    file_size, rows, columns, quality_score
  )
          │
          ▼
  เมื่อจะ train → Engine download ไฟล์จาก Storage URL
```

#### ต้องพึ่ง External API ไหม?
> **Metadata read** → ทำจบในระบบ  
> **File upload/download** → ต้องพึ่ง **Supabase Storage** (ยังไม่ implement)  
> **Quality analysis** → ต้องพึ่ง Engine หรือทำ client-side (ปัจจุบันใช้ mock)

---

### API-05 | Deployments API
**ไฟล์:** `src/lib/deploymentsApi.ts`  
**สถานะ:** ⚠️ Toggle Status ทำจบในระบบ แต่ Deployment จริงต้องพึ่ง Engine

#### การทำงานแบบ Step-by-Step

**`listEndpoints()` — ดึง Endpoint ทั้งหมด**
```
SELECT *
FROM deployed_endpoints
WHERE user_id = (RLS)
ORDER BY created_at DESC
→ return DeployedEndpoint[]
```

**`setEndpointStatus(id, status)` — เปิด/ปิด Endpoint**
```
UPDATE deployed_endpoints
SET status = 'active' | 'inactive'
WHERE id = ?
→ แค่เปลี่ยน flag ใน DB เท่านั้น
```

**ปัญหา: `setEndpointStatus` ไม่ได้สั่ง Engine จริง**
```
ปัจจุบัน:
  เปลี่ยนแค่ status ใน DB → UI แสดงผลเปลี่ยน
  แต่ Infrastructure จริงยังไม่เปลี่ยนแปลง

ควรเป็น:
  PATCH https://engine.slmstudio.ai/v1/endpoints/{id}
  { "action": "start" | "stop" }
  → Engine จัดการ container/GPU scaling จริง
  → Callback มา UPDATE deployed_endpoints SET status = ?
```

**`deployed_endpoints` FK:**
```
deployed_endpoints.model_id → trained_models.id
```
ดังนั้นจะ deploy ได้ต้องมี trained_model record ก่อน

#### Metrics ที่ต้องอัปเดตจาก Engine
ปัจจุบัน `requests_per_min`, `avg_latency_ms`, `error_rate`, `uptime` เป็น static data ใน DB  
ควรถูก update โดย Engine ผ่าน Webhook ทุก N วินาที

#### ต้องพึ่ง External API ไหม?
> **listEndpoints** → ทำจบในระบบ  
> **setEndpointStatus** → ต้องพึ่ง Engine API (`PATCH /v1/endpoints/{id}`)  
> **Metrics ที่แม่นยำ** → ต้องพึ่ง Engine ส่ง Webhook มาอัปเดต

---

### API-06 | API Keys API
**ไฟล์:** `src/lib/apiKeysApi.ts`  
**สถานะ:** ⚠️ Key Management ทำจบในระบบ แต่ต้องซิงค์กับ Inference API เพื่อ validate จริง

#### การทำงานแบบ Step-by-Step

**`createApiKey(name)` — สร้าง API Key ใหม่**
```
supabase.auth.getUser() → ได้ user.id

สร้าง key format:
  prefix = "sk-slm-{name[0:4].toLowerCase()}"
         → เช่น name="Production" → "sk-slm-prod"
  suffix = Math.random().toString(36).slice(2, 6)
         → เช่น "a3bx"

INSERT INTO api_keys (
  user_id, name,
  key_prefix = "sk-slm-prod",
  key_suffix = "a3bx",
  status = "active"
)
→ return ApiKey (แสดง prefix + suffix เท่านั้น ไม่มี full key)
```

**ข้อสังเกต:** ระบบไม่เคยสร้าง full key string และไม่เก็บ hash  
Full key ที่ user เห็นคือ `sk-slm-prod-****` (แค่ mask แสดง suffix)

**`revokeApiKey(id)` — ยกเลิก Key**
```
UPDATE api_keys
SET status = "revoked"
WHERE id = ?
→ ไม่ได้ DELETE จริง (audit trail)
```

**ปัญหา: Key ใน DB ไม่ถูก Register กับ Inference API**
```
ปัจจุบัน:
  Key เก็บใน Supabase เท่านั้น
  Inference API (api.slmstudio.ai) ไม่รู้จัก key นี้เลย

ควรเป็น:
  createApiKey → INSERT DB → 
  POST https://api.slmstudio.ai/v1/keys/register
  { "key_id": id, "user_id": ..., "status": "active" }

  revokeApiKey → UPDATE DB →
  POST https://api.slmstudio.ai/v1/keys/revoke
  { "key_id": id }
```

#### ต้องพึ่ง External API ไหม?
> **Key Storage ใน DB** → ทำจบในระบบ  
> **Key Validation เวลา User เรียก Inference** → ต้องพึ่ง Inference API (`api.slmstudio.ai`) รู้จัก key  
> **Sync create/revoke** → ต้องพึ่ง Inference API

---

### API-07 | Analytics API
**ไฟล์:** `src/lib/analyticsApi.ts`  
**สถานะ:** ⚠️ Query + คำนวณทำจบในระบบ แต่ Source Data ต้องมาจาก Inference API

#### การทำงานแบบ Step-by-Step

**`listCallEvents(range)` — ดึง Call Log**
```
คำนวณ since = Date.now() - days * 86400_000

SELECT *
FROM api_call_events
WHERE created_at >= since
  AND user_id = (RLS)
ORDER BY created_at ASC
LIMIT 5000
→ return CallEvent[]
```

**Client-side Computation (ไม่มี DB call เพิ่ม):**
```
summarize(events):
  totalCalls = events.length
  avgLatency = Σ latencyMs / count
  errorRate  = (events where statusCode >= 400) / total * 100
  uptime     = 100 - errorRate * 0.1

bucketByTime(events, range):
  แบ่ง events เป็น time buckets (24 bucket สำหรับ 24h, N bucket สำหรับ Nd)
  คำนวณ calls, errors ต่อ bucket
  คำนวณ P50, P95, P99 latency ต่อ bucket (quantile sort)

endpointStats(events):
  groupBy endpoint → คำนวณ calls, avgLatency, errorRate ต่อ endpoint
  เรียงจากมากไปน้อย
```

**ปัญหา: ตาราง `api_call_events` ว่างเปล่า**
```
ปัจจุบัน:
  ไม่มีโค้ดที่ INSERT ลง api_call_events เลย
  ตาราง Row Type มี: endpoint, status_code, latency_ms, user_id

ควรเป็น:
  ทุกครั้งที่ user เรียก Inference API →
  api.slmstudio.ai บันทึกผลกลับมาเป็น:
  INSERT INTO api_call_events (
    user_id, endpoint, status_code, latency_ms
  )
  
  ทำได้สองวิธี:
  1. Inference API ส่ง Webhook → Supabase Edge Function → INSERT
  2. Frontend INSERT หลังได้ response (ไม่แนะนำ — client อาจปลอมค่าได้)
```

#### ต้องพึ่ง External API ไหม?
> **Query + คำนวณ** → ทำจบในระบบ  
> **ข้อมูลต้นทาง** → ต้องพึ่ง Inference API ส่ง Webhook มา populate ตาราง

---

### API-08 | Training Simulator (ปัจจุบัน) / Training Progress (ควรเป็น)
**ไฟล์:** `src/hooks/useTrainingSimulator.ts`  
**สถานะ:** ⚠️ เขียนลง Supabase จริง แต่ Logic ยังเป็น Simulation ที่ต้องแทนด้วย SSE

#### การทำงานปัจจุบัน (Simulation)
```
[useTrainingSimulator(project, onUpdate)]
       │
       ▼
ถ้า status === "queued":
  updateProject(id, { status: "training", progress: 5 })
  → รอ 2 วินาที → tick()

ถ้า status === "training":
  newProgress = current.progress + 4~7% (random)
  ถ้า newProgress >= 100:
    updateProject(id, {
      progress: 100,
      status: "completed",
      creditsCost: 50-130 (random)
    })
  ไม่งั้น:
    updateProject(id, { progress: newProgress, status: "training" })
  → รอ 2 วินาที → tick()
```

**สิ่งที่เกิดขึ้นจริง:** เขียน progress ลง Supabase ทุก 2 วินาที จนถึง 100%  
**ปัญหา:** ไม่มี job จริงที่ Engine ทำงานอยู่ — progress เป็นแค่ตัวเลขสุ่ม

#### ควรเปลี่ยนเป็น SSE
```
[Engine ได้รับ POST /v1/jobs และเริ่ม Training จริง]
       │
       ▼
Frontend EventSource:
  const es = new EventSource(
    `https://engine.slmstudio.ai/v1/jobs/${jobId}/stream`,
    { headers: { Authorization: `Bearer ${apiKey}` } }
  )

  es.onmessage = (event) => {
    const data = JSON.parse(event.data)
    // data: { status, progress, currentEpoch, trainLoss, valLoss }
    updateProject(projectId, {
      status: data.status,
      progress: data.progress
    })
    onUpdate(data)
  }

  es.addEventListener("done", () => {
    // training complete → INSERT trained_models
    es.close()
  })
```

#### ต้องพึ่ง External API ไหม?
> ใช่ — ต้องพึ่ง `engine.slmstudio.ai/v1/jobs/{id}/stream` (SSE) เพื่อรับ progress จริง

---

## ส่วนที่ 2 — External APIs วิเคราะห์เชิงลึก (ยังไม่ Implement)

---

### EXT-01 | Inference API
**Base URL:** `https://api.slmstudio.ai/v1`  
**ปรากฏใน:** Settings.tsx, ModelDetail.tsx (เป็นแค่ code example)

#### `POST /v1/chat/completions`
```
Request:
{
  "model": "intent-classifier-v1",
  "messages": [
    { "role": "system", "content": "You are a classifier..." },
    { "role": "user", "content": "ต้องการยกเลิกแพ็กเกจ" }
  ],
  "temperature": 0.7,
  "max_tokens": 512
}

Response (OpenAI-compatible):
{
  "id": "chatcmpl-...",
  "choices": [{
    "message": { "role": "assistant", "content": "billing" },
    "finish_reason": "stop"
  }],
  "usage": { "prompt_tokens": 45, "completion_tokens": 3 }
}
```

**ควรเชื่อมกับ:** `ChatPanel.tsx` ใน Playground — ปัจจุบัน mock response ด้วย setTimeout

---

### EXT-02 | Training Engine API
**Base URL:** `https://engine.slmstudio.ai/v1`

#### `POST /v1/jobs` — เริ่ม Fine-tuning Job
```
Request:
{
  "project_id": "uuid",
  "base_model": "typhoon-7b",
  "task_type": "text-classification",
  "dataset_url": "https://supabase.../storage/v1/...",
  "hyperparams": {
    "epochs": 5,
    "learning_rate": 2e-4,
    "batch_size": 16,
    "warmup_steps": 200
  }
}

Response:
{
  "job_id": "job_abc123",
  "status": "queued",
  "estimated_duration_min": 45,
  "gpu": "A100"
}
```

#### `GET /v1/jobs/{job_id}/stream` — SSE Progress Stream
```
Event stream:
data: {"status":"training","progress":12,"epoch":1,"train_loss":0.823,"val_loss":0.791}
data: {"status":"training","progress":28,"epoch":1,"train_loss":0.654,"val_loss":0.671}
...
data: {"status":"completed","progress":100,"accuracy":88.4,"f1":86.9}
event: done
```

#### `POST /v1/hpo/runs` — เริ่ม Hyperparameter Optimization
```
Request:
{
  "project_id": "uuid",
  "base_model": "typhoon-7b",
  "dataset_url": "...",
  "search_space": {
    "learning_rate": {"type": "log_uniform", "min": 5e-5, "max": 1e-3},
    "epochs": {"type": "int", "min": 3, "max": 10},
    "batch_size": {"type": "choice", "values": [8, 16, 32]}
  },
  "max_trials": 20,
  "strategy": "tpe"
}

Response:
{
  "run_id": "hpo_xyz789",
  "status": "running",
  "estimated_duration_min": 180
}
```

---

## ส่วนที่ 3 — สรุปการพึ่งพา External APIs

```
╔══════════════════════════════════════════════════════════════════════╗
║                    DEPENDENCY MATRIX                               ║
╠════════════════════╦══════════╦══════════════╦════════════════════╣
║ API / Function     ║ Supabase ║ Engine API   ║ Inference API      ║
╠════════════════════╬══════════╬══════════════╬════════════════════╣
║ Auth (login/out)   ║    ✅    ║      —       ║        —           ║
║ Auth (profile)     ║    ✅    ║      —       ║        —           ║
╠════════════════════╬══════════╬══════════════╬════════════════════╣
║ listProjects       ║    ✅    ║      —       ║        —           ║
║ getProject         ║    ✅    ║      —       ║        —           ║
║ createProject      ║    ✅    ║  ❌ POST /v1/jobs  ║   —           ║
║ updateProject      ║    ✅    ║      —       ║        —           ║
║ deleteProject      ║    ✅    ║      —       ║        —           ║
╠════════════════════╬══════════╬══════════════╬════════════════════╣
║ listModels         ║    ✅    ║      —       ║        —           ║
║ getModel           ║    ✅    ║      —       ║        —           ║
║ [model records]    ║   source ║ ❌ must write║        —           ║
╠════════════════════╬══════════╬══════════════╬════════════════════╣
║ listDatasets       ║    ✅    ║      —       ║        —           ║
║ [file upload]      ║ ❌ Storage ║    —       ║        —           ║
║ [quality scan]     ║    —     ║ ❌ /v1/datasets/analyze ║   —    ║
╠════════════════════╬══════════╬══════════════╬════════════════════╣
║ listEndpoints      ║    ✅    ║      —       ║        —           ║
║ setEndpointStatus  ║    ✅    ║ ❌ PATCH /v1/endpoints ║   —      ║
║ [metrics update]   ║   target ║ ❌ must webhook║       —          ║
╠════════════════════╬══════════╬══════════════╬════════════════════╣
║ listApiKeys        ║    ✅    ║      —       ║        —           ║
║ createApiKey       ║    ✅    ║      —       ║ ❌ must register   ║
║ revokeApiKey       ║    ✅    ║      —       ║ ❌ must sync       ║
╠════════════════════╬══════════╬══════════════╬════════════════════╣
║ listCallEvents     ║    ✅    ║      —       ║        —           ║
║ [event records]    ║   target ║      —       ║ ❌ must webhook    ║
╠════════════════════╬══════════╬══════════════╬════════════════════╣
║ Training Progress  ║    ✅    ║ ❌ SSE stream ║       —           ║
║ HPO Tuning         ║    —    ║ ❌ POST /v1/hpo ║      —          ║
║ Chat Inference     ║    —    ║      —        ║ ❌ POST /v1/chat   ║
╚════════════════════╩══════════╩══════════════╩════════════════════╝
```

---

## ส่วนที่ 4 — APIs ที่ทำจบในระบบได้เลย (Self-contained)

| API Function | เหตุผล |
|---|---|
| `getSession / signOut / onAuthStateChange` | Supabase Auth ครอบคลุมทั้งหมด |
| `loadProfile` | อ่าน/เขียน `profiles` table เท่านั้น |
| `listProjects / getProject` | SELECT จาก `projects` table |
| `updateProject` (สำหรับ config) | UPDATE `projects` table |
| `deleteProject` | DELETE + cascade ใน Supabase |
| `listModels / getModel` | SELECT จาก `trained_models` (read-only) |
| `listDatasets` | SELECT จาก `datasets` (read-only, metadata only) |
| `listEndpoints` | SELECT จาก `deployed_endpoints` |
| `setEndpointStatus` (DB flag เท่านั้น) | UPDATE `deployed_endpoints.status` |
| `listApiKeys` | SELECT จาก `api_keys` |
| `createApiKey / revokeApiKey` (DB เท่านั้น) | INSERT/UPDATE `api_keys` |
| `listCallEvents` | SELECT จาก `api_call_events` |
| `summarize / bucketByTime / endpointStats` | Pure JavaScript computation |
| `validatePreflight` | Client-side validation เท่านั้น |

---

## ส่วนที่ 5 — APIs ที่ต้องพึ่ง External เพิ่มเติม + สิ่งที่ต้อง Implement

### ต้องพึ่ง Engine API (`engine.slmstudio.ai/v1`)

| สิ่งที่ต้องทำ | เรียก | ผลที่ต้องเกิด |
|---|---|---|
| createProject → trigger training | `POST /v1/jobs` | ได้ `job_id` กลับมา เก็บใน `projects` table |
| training monitor | `GET /v1/jobs/{id}/stream` (SSE) | อัปเดต `progress`, `status` ใน Supabase real-time |
| HPO run | `POST /v1/hpo/runs` | ได้ `run_id` กลับมา |
| HPO result | `GET /v1/hpo/runs/{id}` | อ่าน trial results จริง |
| deploy endpoint | `POST /v1/endpoints` | สร้าง container + ได้ `endpoint_url` กลับมา |
| start/stop endpoint | `PATCH /v1/endpoints/{id}` | Engine จัดการ infrastructure จริง |
| Engine callback | Webhook → Supabase Edge Function | Engine write ผลลัพธ์กลับมาใน `trained_models` |

### ต้องพึ่ง Inference API (`api.slmstudio.ai/v1`)

| สิ่งที่ต้องทำ | เรียก | ผลที่ต้องเกิด |
|---|---|---|
| Playground chat | `POST /v1/chat/completions` | ได้ AI response จริงแทน mock |
| Key registration | `POST /v1/keys/register` | Inference API รู้จัก key ที่สร้างใน Supabase |
| Key revocation | `POST /v1/keys/revoke` | Inference API ไม่ accept key ที่ถูก revoke |
| Usage logging | Webhook จาก Inference API → Supabase | `api_call_events` มีข้อมูลจริงสำหรับ Analytics |

### ต้องพึ่ง Supabase Storage (Internal แต่ยังไม่ implement)

| สิ่งที่ต้องทำ | Storage Call | ผลที่ต้องเกิด |
|---|---|---|
| อัปโหลด dataset ใน NewProject | `storage.upload(bucket, path, file)` | ได้ URL สำหรับส่งให้ Engine |
| Engine download dataset | `storage.createSignedUrl(path, ttl)` | Engine อ่านไฟล์ได้ผ่าน signed URL |
| Dataset preview ใน Insights | `storage.download(path)` | parse CSV/JSONL จริงแทน mock rows |

---

## ส่วนที่ 6 — Supabase Function ที่ซ่อนอยู่

จาก `types.ts` พบว่ามี Supabase RPC Function:

```sql
seed_user_demo_data(_user_id: uuid) → void
```

**หน้าที่:** inject demo data (projects, models, datasets, endpoints, api_keys) ให้ user ใหม่  
**เรียกเมื่อไหร่:** หลัง sign-up ครั้งแรก (ไม่เห็นในโค้ด frontend — น่าจะเรียกจาก Supabase Auth Hook)  
**สถานะ:** ✅ ทำจบในระบบ — เป็น PostgreSQL function ที่ Supabase รัน server-side

---

*จัดทำโดย Claude Code — วิเคราะห์จาก source code ทั้งหมดใน `src/lib/`, `src/hooks/`, `src/contexts/`, `src/integrations/`*
