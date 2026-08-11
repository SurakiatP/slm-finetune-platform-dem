# API Classification Report — Smart Model Tune (SLM Studio)

**วันที่จัดทำ:** 2026-05-06  
**Project Path:** `smart-model-tune-main/`  
**จุดประสงค์:** จำแนก API ทั้งหมดในโปรเจกต์ออกเป็น 3 กลุ่ม ได้แก่  
1. **Backend APIs** — เรียก Supabase จริง  
2. **External APIs** — ควรจะเรียก engine/inference ภายนอก (ยังไม่ implement จริง)  
3. **Client-side Mock** — คำนวณ/สร้างข้อมูลภายใน browser ไม่มี HTTP call

---

## กลุ่มที่ 1 — Backend APIs (Supabase)

> ทุก call ใช้ Supabase client จาก `src/integrations/supabase/client.ts`  
> Base URL: `https://<project>.supabase.co`

---

### 1.1 Authentication API
**ไฟล์:** `src/contexts/AuthContext.tsx`

| Function | Supabase Call | หน้าที่ |
|---|---|---|
| `getSession()` | `supabase.auth.getSession()` | ดึง session ที่ยังใช้งานอยู่เมื่อโหลดแอป |
| `onAuthStateChange()` | `supabase.auth.onAuthStateChange()` | Subscribe เพื่อ react ต่อ login / logout |
| `signOut()` | `supabase.auth.signOut()` | ออกจากระบบ |
| `loadProfile(userId)` | `supabase.from("profiles").select(...)` | ดึง display_name + avatar ของ user |

**ตาราง Supabase:** `profiles`  
**Columns ที่ดึง:** `id`, `user_id`, `display_name`, `avatar_url`

---

### 1.2 Projects API
**ไฟล์:** `src/lib/projectsApi.ts`  
**Hook:** `src/hooks/useProjects.ts`

| Function | Method | ตาราง | หน้าที่ |
|---|---|---|---|
| `listProjects()` | SELECT | `projects` | ดึง project ทั้งหมดของ user เรียงตาม updated_at |
| `getProject(id)` | SELECT | `projects` | ดึง project เดียวตาม ID |
| `createProject(input)` | INSERT | `projects` | สร้าง project ใหม่ (status="queued") |
| `updateProject(id, patch)` | UPDATE | `projects` | อัปเดต field ต่าง ๆ เช่น status, progress, tags |
| `deleteProject(id)` | DELETE | `projects` | ลบ project และ cascade |

**Fields ที่ write:** `name`, `description`, `task_type`, `base_model`, `status`, `progress`, `epochs`, `learning_rate`, `dataset_size`, `credits_cost`, `pinned`, `tags`

> **หมายเหตุ:** `useTrainingSimulator.ts` เรียก `updateProject()` ซ้ำ ๆ ทุก 2 วินาที เพื่อจำลองความคืบหน้าการ training ลงในตาราง `projects` จริง (ยังไม่เชื่อมกับ engine จริง)

---

### 1.3 Models API
**ไฟล์:** `src/lib/modelsApi.ts`  
**Hook:** `src/hooks/useUserData.ts → useModels()`

| Function | Method | ตาราง | หน้าที่ |
|---|---|---|---|
| `listModels()` | SELECT | `trained_models` | ดึง model ทั้งหมด เรียงตาม created_at |
| `getModel(id)` | SELECT | `trained_models` | ดึง model เดียวตาม ID |

**Fields ที่ดึง:** `id`, `name`, `base_model`, `task_type`, `accuracy`, `f1_score`, `precision`, `recall`, `latency_ms`, `file_size`, `format`, `status`

---

### 1.4 Datasets API
**ไฟล์:** `src/lib/datasetsApi.ts`  
**Hook:** `src/hooks/useUserData.ts → useDatasets()`

| Function | Method | ตาราง | หน้าที่ |
|---|---|---|---|
| `listDatasets()` | SELECT | `datasets` | ดึง dataset metadata ทั้งหมด |

**Fields ที่ดึง:** `id`, `project_id`, `name`, `description`, `rows`, `columns`, `file_size`, `format`, `quality_score`, `created_at`

> **หมายเหตุ:** ดึงได้เฉพาะ **metadata** — ไม่มีการดึงเนื้อหาไฟล์จริง (ไม่มี Supabase Storage call ในโค้ดปัจจุบัน)

---

### 1.5 Deployments API
**ไฟล์:** `src/lib/deploymentsApi.ts`  
**Hook:** `src/hooks/useUserData.ts → useEndpoints()`

| Function | Method | ตาราง | หน้าที่ |
|---|---|---|---|
| `listEndpoints()` | SELECT | `deployed_endpoints` | ดึง endpoint ทั้งหมด เรียงตาม created_at |
| `setEndpointStatus(id, status)` | UPDATE | `deployed_endpoints` | เปิด/ปิด endpoint (active/inactive) |

**Fields ที่ดึง:** `id`, `model_id`, `model_name`, `project_name`, `endpoint_url`, `status`, `requests_per_min`, `avg_latency_ms`, `error_rate`, `uptime`, `rate_limit_per_min`, `burst_limit`

---

### 1.6 API Keys API
**ไฟล์:** `src/lib/apiKeysApi.ts`  
**Hook:** `src/hooks/useUserData.ts → useApiKeys()`

| Function | Method | ตาราง | หน้าที่ |
|---|---|---|---|
| `listApiKeys()` | SELECT | `api_keys` | ดึง API key ทั้งหมดของ user |
| `createApiKey(name)` | INSERT | `api_keys` | สร้าง key ใหม่ รูปแบบ `sk-slm-{name}-{random4}` |
| `revokeApiKey(id)` | UPDATE | `api_keys` | เปลี่ยน status เป็น "revoked" |

**Fields ที่ดึง:** `id`, `name`, `key_prefix`, `key_suffix`, `status`, `last_used_at`, `created_at`

---

### 1.7 Analytics API
**ไฟล์:** `src/lib/analyticsApi.ts`  
**Hook:** `src/hooks/useUserData.ts → useCallEvents()`

| Function | ประเภท | หน้าที่ |
|---|---|---|
| `listCallEvents(range)` | **Supabase SELECT** | ดึง call events จากตาราง `api_call_events` ตามช่วงเวลา (24h / 7d / 30d / 90d) สูงสุด 5,000 records |
| `summarize(events)` | Client-side | คำนวณ totalCalls, avgLatency, errorRate, uptime |
| `bucketByTime(events, range)` | Client-side | แบ่ง events เป็น time bucket สำหรับ chart |
| `endpointStats(events)` | Client-side | จัดกลุ่มสถิติตาม endpoint |

**ตาราง Supabase:** `api_call_events`  
**Fields ที่ดึง:** `id`, `endpoint`, `status_code`, `latency_ms`, `created_at`

---

### สรุป Backend API — ตาราง Supabase ที่ใช้งานจริง

| ตาราง | อ่าน | เขียน | ลบ |
|---|:---:|:---:|:---:|
| `profiles` | ✓ | — | — |
| `projects` | ✓ | ✓ | ✓ |
| `trained_models` | ✓ | — | — |
| `datasets` | ✓ | — | — |
| `deployed_endpoints` | ✓ | ✓ | — |
| `api_keys` | ✓ | ✓ | — |
| `api_call_events` | ✓ | — | — |

---

## กลุ่มที่ 2 — External APIs (ยังไม่ Implement จริง)

> API เหล่านี้ถูกออกแบบไว้ในสถาปัตยกรรม และมีปรากฏในโค้ดเป็น **code snippet / documentation example เท่านั้น**  
> ยังไม่มี HTTP call จริงออกจาก UI ไปยัง endpoint เหล่านี้

---

### 2.1 Inference API
**Base URL:** `https://api.slmstudio.ai/v1`  
**ปรากฏใน:** `src/pages/Settings.tsx`, `src/pages/ModelDetail.tsx`

| Endpoint | Method | หน้าที่ | สถานะ |
|---|---|---|---|
| `/v1/chat/completions` | POST | Chat completions (OpenAI-compatible format) | ⚠ Code example เท่านั้น |
| `/v1/inference` | POST | Model inference ทั่วไป | ⚠ Code example เท่านั้น |

**Headers ที่ต้องใช้:**
```
Authorization: Bearer sk-slm-{prefix}-{random4}
Content-Type: application/json
```

**Request Body (chat/completions):**
```json
{
  "model": "intent-classifier-v1",
  "messages": [{ "role": "user", "content": "..." }],
  "temperature": 0.7,
  "max_tokens": 512
}
```

**ที่ต้องทำ:** เชื่อมต่อ ChatPanel ใน `src/components/playground/ChatPanel.tsx` ให้ส่ง request จริงแทน mock response

---

### 2.2 Training Engine API
**Base URL:** `https://engine.slmstudio.ai/v1`  
**ปรากฏใน:** ยังไม่มีในโค้ด — ถูก simulate โดย `useTrainingSimulator.ts`

| Endpoint | Method | หน้าที่ | สถานะ |
|---|---|---|---|
| `/v1/jobs` | POST | Submit fine-tuning job ใหม่ | ❌ ไม่มีการเรียกจริง |
| `/v1/jobs/{job_id}` | GET | ดึงสถานะ job | ❌ ไม่มีการเรียกจริง |
| `/v1/jobs/{job_id}/stream` | GET (SSE) | Stream progress แบบ real-time | ❌ ไม่มีการเรียกจริง |
| `/v1/jobs/{job_id}/cancel` | POST | ยกเลิก job | ❌ ไม่มีการเรียกจริง |
| `/v1/hpo/runs` | POST | เริ่ม Hyperparameter Optimization run | ❌ ไม่มีการเรียกจริง |
| `/v1/hpo/runs/{run_id}` | GET | ดึงผลลัพธ์ HPO | ❌ ไม่มีการเรียกจริง |

**ที่ต้องทำ:** แทนที่ `useTrainingSimulator.ts` (setTimeout loop) ด้วย SSE subscription จริงไปยัง `/v1/jobs/{job_id}/stream`

---

### สรุป External API — ช่องว่างที่ต้อง Implement

| UI Component | ไฟล์ | ควรเรียก External API | สถานะปัจจุบัน |
|---|---|---|---|
| Playground ChatPanel | `components/playground/ChatPanel.tsx` | `POST /v1/chat/completions` | Mock response (hardcoded) |
| Training Monitor | `pages/TrainingMonitor.tsx` | `GET /v1/jobs/{id}/stream` (SSE) | setTimeout simulation |
| New Project Wizard | `pages/NewProject.tsx` | `POST /v1/jobs` | updateProject() ไป Supabase เท่านั้น |
| Dataset Insights / Quality | `pages/DatasetInsights.tsx` | ควรดึง file content จริง | Mock rows จาก datasetExpander |
| HPO / Auto-Tune | `pages/ProjectDetail.tsx` | `POST /v1/hpo/runs` | tuningGenerator.ts (client-side mock) |

---

## กลุ่มที่ 3 — Client-side Mock (ไม่ใช่ API จริง)

> ไม่มี HTTP request เกิดขึ้น — ข้อมูลถูกสร้างขึ้นภายใน JavaScript ใน browser

---

### 3.1 Dataset Expander
**ไฟล์:** `src/lib/datasetExpander.ts`

| Function | หน้าที่ |
|---|---|
| `expandDataset(datasetId)` | สร้าง mock rows จาก template แบบ deterministic (seed = datasetId) สูงสุด 600 rows |
| `getLabelColumn(datasetId)` | คืนชื่อ column ที่เป็น label (เช่น "category", "rating") |
| `getTextColumn(datasetId)` | คืนชื่อ column ที่เป็น text input |

**Datasets ที่รองรับ:** `ds-1` (Thai complaints), `ds-2` (product reviews), `ds-3` (NER)  
**ควรแทนที่ด้วย:** Supabase Storage download → parse CSV/JSONL จริง

---

### 3.2 Quality Calculator
**ไฟล์:** `src/lib/qualityCalculator.ts`

| Function | หน้าที่ |
|---|---|
| `computeQualityReport(datasetId)` | คำนวณ quality score, issues, class distribution จาก mock rows |
| `getDatasetSampleSize(datasetId)` | นับจำนวน rows จาก mock data |
| `getRawRows(datasetId)` | คืน mock rows ทั้งหมด |

**ควรแทนที่ด้วย:** วิเคราะห์ไฟล์จริงหรือเรียก Engine API `POST /v1/datasets/analyze`

---

### 3.3 Tuning Generator (HPO Mock)
**ไฟล์:** `src/lib/tuningGenerator.ts`

| Function | หน้าที่ |
|---|---|
| `getTuningRunsForProject(project)` | สร้าง mock HPO runs แบบ deterministic (seed = project.id) 1-4 runs |
| `getLatestTuningRun(project)` | คืน HPO run ล่าสุด (mock) |
| `getTuningRun(project, runId)` | คืน HPO run ตาม ID (mock) |
| `setAppliedRun(projectId, runId)` | บันทึกว่าใช้ run ไหน → **localStorage** |
| `getAppliedRunId(projectId)` | อ่านจาก localStorage |

**ควรแทนที่ด้วย:** `POST /v1/hpo/runs` → polling `GET /v1/hpo/runs/{run_id}`

---

### 3.4 Training Simulator
**ไฟล์:** `src/hooks/useTrainingSimulator.ts`

| หน้าที่ | รายละเอียด |
|---|---|
| จำลอง training progress | loop ทุก 2 วินาที — เพิ่ม progress 4-7% ต่อ tick |
| เขียนผลลง Supabase จริง | เรียก `updateProject(id, { progress, status })` — ดังนั้น state **persist** ในฐานข้อมูล |
| ไม่เรียก Engine API | ไม่มี SSE subscription, ไม่มี job ID จริง |

**ควรแทนที่ด้วย:** EventSource SSE → `GET /v1/jobs/{job_id}/stream`

---

## ภาพรวม — จำแนกตาม Layer

```
┌─────────────────────────────────────────────────────────────┐
│                    FRONTEND (React/Vite)                     │
│                                                             │
│  ┌─────────────┐  ┌──────────────────┐  ┌───────────────┐  │
│  │  Auth       │  │  Data (CRUD)     │  │  Analytics    │  │
│  │  Context    │  │  useProjects     │  │  useCallEvents│  │
│  └──────┬──────┘  │  useModels       │  └───────┬───────┘  │
│         │         │  useDatasets     │          │          │
│         │         │  useEndpoints    │          │          │
│         │         │  useApiKeys      │          │          │
│         │         └────────┬─────────┘          │          │
└─────────┼──────────────────┼────────────────────┼──────────┘
          │  [REAL CALLS]    │  [REAL CALLS]       │ [REAL CALLS]
          ▼                  ▼                     ▼
┌─────────────────────────────────────────────────────────────┐
│              SUPABASE (Backend)                             │
│  supabase.auth.*   |  from("projects/models/...")          │
│  profiles          |  trained_models, datasets             │
│                    |  deployed_endpoints, api_keys         │
│                    |  api_call_events                      │
└─────────────────────────────────────────────────────────────┘

          [NOT YET CONNECTED — needs implementation]
          ▼                  ▼
┌──────────────────┐  ┌──────────────────────────────────────┐
│  Inference API   │  │  Training Engine API                 │
│  api.slmstudio   │  │  engine.slmstudio.ai/v1              │
│  .ai/v1          │  │  POST /v1/jobs                       │
│  POST /v1/chat   │  │  GET  /v1/jobs/{id}/stream (SSE)     │
│  /completions    │  │  POST /v1/hpo/runs                   │
└──────────────────┘  └──────────────────────────────────────┘

          [CLIENT-SIDE MOCK — no HTTP at all]
          ▼
┌─────────────────────────────────────────────────────────────┐
│  datasetExpander.ts  →  mock rows (hardcoded templates)     │
│  qualityCalculator.ts →  quality score (pure calculation)   │
│  tuningGenerator.ts  →  mock HPO trials (PRNG + localStorage)│
│  useTrainingSimulator→  setTimeout loop → writes to Supabase│
└─────────────────────────────────────────────────────────────┘
```

---

## ลำดับความสำคัญในการ Implement จริง

| ลำดับ | งาน | ผลกระทบ | ไฟล์ที่ต้องแก้ |
|:---:|---|---|---|
| 1 | เชื่อม Playground กับ Inference API | ฟีเจอร์หลักที่ user ต้องใช้งาน | `ChatPanel.tsx` |
| 2 | เชื่อม New Project กับ Engine `POST /v1/jobs` | เริ่ม fine-tuning จริง | `NewProject.tsx`, `projectsApi.ts` |
| 3 | แทน Training Simulator ด้วย SSE | progress เป็น real-time จริง | `useTrainingSimulator.ts` |
| 4 | เชื่อม Dataset upload กับ Supabase Storage | วิเคราะห์ไฟล์จริงได้ | `DatasetInsights.tsx`, `datasetsApi.ts` |
| 5 | เชื่อม HPO กับ Engine `POST /v1/hpo/runs` | Auto-tuning จริง | `tuningGenerator.ts`, `ProjectDetail.tsx` |

---

*จัดทำโดย Claude Code — สแกนจากซอร์สโค้ดทั้งหมดใน `src/`*
