
# แยกข้อมูลทุกหน้าให้เป็นของผู้ใช้แต่ละคน (per-user data isolation)

## สถานะปัจจุบัน

- `projects` + `profiles` แยกตาม user แล้ว (มี RLS) ✅
- หน้าอื่นๆ ทั้งหมดยังอ่านจาก `mockData.ts` / mock files ที่แชร์กันทุกคน ❌
  - **Dashboard** — `StatsCards`, `RecentProjects`, `ActivityChart` ใช้ mock
  - **Models** — `mockModels` (แชร์)
  - **Datasets** — `mockDatasets`, `mockSchemas`, `mockSampleRows` (แชร์)
  - **Deployment** — `mockDeployedEndpoints` (แชร์)
  - **Analytics** — `generateApiCallData/Latency/Endpoints` (mock generator แชร์)
  - **Leaderboard** — อ่าน `mockModels` + `mockProjects`
  - **Settings → API Keys** — `mockApiKeys` (แชร์)

## เป้าหมาย

ทุกหน้าแสดงข้อมูลของผู้ใช้คนนั้นเท่านั้น โดยอิงจาก projects ที่ user เป็นเจ้าของ + ตารางใหม่ที่ผูก `user_id` พร้อม RLS

---

## 1) Database — สร้างตารางใหม่พร้อม RLS

ทุกตารางมี `user_id uuid NOT NULL` + RLS policy `auth.uid() = user_id` (SELECT/INSERT/UPDATE/DELETE)

| ตาราง | ใช้แทน | คอลัมน์หลัก |
|---|---|---|
| `trained_models` | `mockModels` | `project_id`, `name`, `base_model`, `task_type`, `accuracy`, `f1_score`, `file_size`, `format`, `status` |
| `datasets` | `mockDatasets` | `project_id` (nullable), `name`, `rows`, `columns`, `file_size`, `format`, `quality_score` |
| `deployed_endpoints` | `mockDeployedEndpoints` | `model_id`, `project_name`, `endpoint_url`, `status`, `requests_per_min`, `avg_latency_ms`, `error_rate`, `uptime`, `rate_limit_per_min`, `burst_limit` |
| `api_keys` | `mockApiKeys` | `name`, `key_prefix`, `key_hash`, `status`, `last_used_at` |
| `api_call_events` | analytics | `endpoint`, `latency_ms`, `status_code`, `created_at` (จะ aggregate ฝั่ง client) |

**Trigger**: `update_updated_at_column` ผูกกับทุกตาราง

## 2) ข้อมูลเริ่มต้น (seed) สำหรับผู้ใช้ใหม่

เพื่อไม่ให้ผู้ใช้ใหม่เห็นหน้าว่างเปล่า — ขยาย `handle_new_user` trigger หรือสร้างฟังก์ชัน `seed_user_demo_data(user_id)` ที่:
- สร้าง 2-3 demo projects
- สร้าง 1-2 trained_models ที่ผูก projects นั้น
- สร้าง 1 dataset, 1 deployed endpoint, 1 api_key (เป็น demo)
- ผู้ใช้ลบทิ้งได้ตามต้องการ

เรียก trigger นี้ตอน signup อัตโนมัติ

## 3) Frontend — API layer + hooks

สร้าง API modules แยกแต่ละ resource:
- `src/lib/modelsApi.ts` — listModels/getModel/createModel
- `src/lib/datasetsApi.ts`
- `src/lib/deploymentsApi.ts`
- `src/lib/apiKeysApi.ts`
- `src/lib/analyticsApi.ts` (รวม aggregate จาก `api_call_events` + นับจาก projects/models)

สร้าง hooks: `useModels`, `useDatasets`, `useDeployments`, `useApiKeys`, `useAnalytics`, `useUserStats`

## 4) Refactor หน้าเหล่านี้

- **Dashboard**:
  - `StatsCards` รับ props จาก `useUserStats()` (นับจาก projects/models ของผู้ใช้)
  - `RecentProjects` ใช้ `useProjects()` แทน `mockProjects`
  - `ActivityChart` คำนวณจาก `projects.created_at` ของผู้ใช้ (group by day)
- **Models** → `useModels()`
- **DatasetExplorer / DatasetInsights** → `useDatasets()`
- **Deployment** → `useDeployments()`
- **Analytics** → `useAnalytics()` aggregate จาก `api_call_events`
- **Leaderboard** → ใช้ models + projects ของ user เท่านั้น
- **Settings → ApiKeysTab** → `useApiKeys()` + functions create/revoke จริง
- **CommandPalette** — เปลี่ยน search ให้อ่านจาก hooks แทน mock

## 5) สิ่งที่ยังเก็บเป็น mock (ตั้งใจ)

- `qualityReportMockData`, `tuningReportMockData`, `templatesMockData` — ใช้เป็น generator ที่สร้างตามผลของ user (ทำไปแล้วใน `tuningGenerator.ts`, `qualityCalculator.ts`)
- `baseModelLabels`, `taskTypeLabels` — เป็น constants ไม่ใช่ user data ✓

## รายละเอียดทางเทคนิค

```text
auth.users ─┬─ profiles (1:1)
            └─ projects ─┬─ trained_models
                         ├─ datasets
                         └─ deployed_endpoints (via model_id)
api_keys ── user_id
api_call_events ── user_id (+ optional endpoint_id)
```

- ทุก SELECT policy: `USING (auth.uid() = user_id)`
- Insert policy: `WITH CHECK (auth.uid() = user_id)`
- Update/Delete policy: `USING (auth.uid() = user_id)`
- ฟังก์ชัน seed เป็น `SECURITY DEFINER` + `SET search_path = public`

## ลำดับการทำ

1. Migration: สร้าง 5 ตารางใหม่ + RLS + trigger seed
2. Seed function + ทดสอบกับ user ปัจจุบัน
3. สร้าง API layer + hooks
4. Refactor Dashboard (impact สูงสุด)
5. Refactor Models, Datasets, Deployment
6. Refactor Analytics, Leaderboard, Settings/ApiKeys
7. ลบ imports จาก `mockData.ts` ที่ไม่ใช้แล้ว (เก็บ labels/constants ไว้)
8. ทดสอบ: login ด้วย 2 บัญชีเห็นข้อมูลคนละชุด

## หมายเหตุ

- Demo seed data เพื่อให้ user ใหม่ไม่เจอหน้าว่าง — สามารถลบได้
- Analytics: เก็บ raw events เพื่อให้กราฟจริง (ถ้ายังไม่มี events ให้แสดง empty state พร้อมปุ่ม "Generate sample data")
- ระบบ A/B testing, Templates, Onboarding, CostCalculator ที่เป็น static reference ไม่ต้องแยกตาม user
