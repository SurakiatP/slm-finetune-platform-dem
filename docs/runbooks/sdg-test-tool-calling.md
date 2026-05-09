# SDG Manual Test Runbook — Tool Calling (English)

> **Goal:** ทดสอบ Phase 9 SDG pipeline ครบทุก path สำหรับ task type `tool_calling` (เนื้อหาภาษาอังกฤษ)
> **Estimated time:** 20-25 นาที + ~$0.30 OpenRouter cost
> **Prerequisites:** Stack รันอยู่ + `OPENROUTER_API_KEY` ตั้งใน `.env`
> **Seed files:** `seed_data/tool_calling/tool_calling_*.{json,jsonl}`

---

## 0. Pre-flight

ดูส่วน `0. Pre-flight checklist` ใน [`sdg-test-classification.md`](./sdg-test-classification.md) — ทำเหมือนกัน (stack health, SSH, tabs)

### Create project (specific to this runbook)

```
POST /api/v1/projects
{
  "name": "sdg-test-tool",
  "description": "Phase 9 tool calling SDG test",
  "task_type": "tool_calling"
}
→ 201
```

🔖 **เก็บ → `<tool_project_id>`**

---

## Task 1 — Upload canonical seed (.jsonl)

### Steps

```
POST /api/v1/datasets/upload-seed
project_id:  <tool_project_id>
task_type:   tool_calling
name:        seed-tool-canonical-jsonl
file:        seed_data/tool_calling/tool_calling_canonical.jsonl
```

### ✅ Expected (201)

```json
{
  "dataset_id": "...",
  "task_type": "tool_calling",
  "num_samples": 8,
  "invalid_rows": [],
  "format_detection": {
    "ran": false,
    "field_mapping": {},
    "rows_total": 8,
    "rows_canonicalised": 8,
    "rows_dropped": 0,
    "notes": "already canonical — Format Detection skipped"
  },
  "pdf_uri": null
}
```

🔖 **เก็บ → `<seed_tool_canonical_jsonl_id>`**

### 🧪 Verification

| Check | Expected |
|-------|---------|
| `num_samples: 8` | ✅ ทุก row pass schema (รวม validation `answer` เป็น JSON-encoded string) |
| MinIO file content | บรรทัดแรก: `{"question": "Set the oven to 200 degrees Celsius please", "answer": "{\"name\":\"set_oven\",\"parameters\":{\"celsius\":200}}"}` |
| Schema validation | ✅ ทุก row's `answer` decode JSON ได้ + มี keys `name` + `parameters` |

---

## Task 2 — Upload canonical seed (.json)

ทำซ้ำ Task 1 แต่ใช้ `tool_calling_canonical.json`

✅ Expected: identical (`format_detection.ran: false`, `num_samples: 8`)

🔖 **เก็บ → `<seed_tool_canonical_json_id>`**

---

## Task 3 — Upload mismatched (.jsonl) → Format Detection ทำงาน

**วัตถุประสงค์:** รันการ rename `instruction` → `question` และ `function_call` → `answer`

### Steps

```
POST /api/v1/datasets/upload-seed
project_id:  <tool_project_id>
task_type:   tool_calling
name:        seed-tool-mismatched-jsonl
file:        seed_data/tool_calling/tool_calling_mismatched.jsonl
```

### ✅ Expected (201)

```json
{
  "dataset_id": "...",
  "task_type": "tool_calling",
  "num_samples": 8,
  "format_detection": {
    "ran": true,
    "model_used": "google/gemini-2.5-flash-lite",
    "field_mapping": {
      "instruction": "question",
      "function_call": "answer"
    },
    "rows_total": 8,
    "rows_canonicalised": 8,
    "rows_dropped": 0
  }
}
```

🔖 **เก็บ → `<seed_tool_mismatched_jsonl_id>`**

### 🧪 Verification

ใน MinIO — file หลัง rename ต้องมี `question`/`answer` (canonical) และ `answer` ยังคงเป็น JSON-encoded string ที่ valid:

```jsonl
{"question": "Set the oven to 200 degrees Celsius please", "answer": "{\"name\":\"set_oven\",\"parameters\":{\"celsius\":200}}"}
```

> สำคัญ: Format Detection แค่ rename **keys** เท่านั้น ค่าของ `answer` (JSON-encoded string) ไม่ถูกแก้ไข (Q1.3)

---

## Task 4 — Upload mismatched (.json)

ทำซ้ำ Task 3 แต่ใช้ `tool_calling_mismatched.json`

🔖 **เก็บ → `<seed_tool_mismatched_json_id>`**

---

## Task 5 — SDG generate `with_seed` → quality gates + sentinel tool

**วัตถุประสงค์:** ทดสอบ Generator + Judge + MinHash + sentinel `no_tool_needed` injection

### Steps

```
POST /api/v1/datasets/generate
{
  "sdg_mode": "with_seed",
  "project_id": "<tool_project_id>",
  "task_type": "tool_calling",
  "task_description": "Translate kitchen / smart-home commands into JSON tool calls. Available tools: set_oven (celsius), start_timer (minutes), set_volume (level 0-100), play_music (genre, room), light_on (room).",
  "num_samples": 20,
  "temperature": 0.7,
  "dataset_name": "sdg-tool-test-1",
  "seed_dataset_id": "<seed_tool_canonical_jsonl_id>"
}
```

### ✅ Immediate response (202)

🔖 **เก็บ → `<sdg_tool_dataset_id>`**

### ⏳ Watch celery log

```bash
tail -f /tmp/celery.log
```

ควรเห็น:
```
[INFO] SDG starting (Phase 9): task=tool_calling target=20
[INFO] sentinel injection: tools = [...] + ['no_tool_needed']
[INFO] meta-prompter LLM call ...
[INFO] Generator chat_batch ...
[INFO] Judge chat_batch ...
[INFO] SDG done: samples=20 rejected=N1 dup=N2 judge_low=N3 calls=N4
```

### 🔁 Poll until completed (~2-3 minutes)

```
GET /api/v1/datasets/<sdg_tool_dataset_id>
```

### ✅ Final state

```json
{
  "num_samples": 20,
  "storage_uri": "s3://datasets/sdg/<id>.jsonl",
  "generation_metadata": {
    "judge_rejected_count": > 0,
    "duplicate_count": >= 0,
    "api_calls": > 30
  }
}
```

### 🧪 Quality gate verifications

#### A) Sentinel `no_tool_needed` injection (~10% = 2 rows)

```
GET /api/v1/datasets/<sdg_tool_dataset_id>/preview?limit=20
```

ดู `answer` field ของแต่ละ row — decode JSON ดู:
- ~18 rows: `name` ใน `["set_oven", "start_timer", "set_volume", "play_music", "light_on"]`
- **~2 rows: `name = "no_tool_needed"`** ← Phase 9 sentinel

Question ของ row sentinel ต้องเป็น off-topic / ambiguous (เช่น "What's the weather like?", "Tell me a joke")

#### B) Tool name validation

ทุก row ต้อง:
1. `answer` เป็น **JSON-encoded string** (มี `\"` escape, ไม่ใช่ object)
2. Decode แล้วได้ `{"name": "...", "parameters": {...}}`
3. `name` อยู่ใน working tool set (รวม sentinel)

ตัวอย่าง valid:
```json
{
  "question": "Make some coffee",
  "answer": "{\"name\":\"play_music\",\"parameters\":{\"genre\":\"morning\",\"room\":\"kitchen\"}}"
}
```

ตัวอย่าง invalid (Judge ควร reject):
```json
{
  "question": "Make some coffee",
  "answer": "{\"name\":\"make_coffee\",\"parameters\":{}}"   // ❌ tool ไม่อยู่ใน catalog
}
```

#### C) Parameter type matching

`set_oven` → `celsius: integer`, `start_timer` → `minutes: number`, `set_volume` → `level: integer`

ตรวจ row ที่เป็น `set_oven` — `parameters.celsius` ต้องเป็น integer (ไม่ใช่ string)

```bash
# Quick verify ผ่าน curl + jq
ssh -p 51030 root@202.215.2.218 \
  "curl -s http://localhost:8000/api/v1/datasets/<sdg_tool_dataset_id>/preview?limit=20" \
  | jq -r '.samples[] | .answer | fromjson | .name'
# → list ของ tool names
```

#### D) Judge gate

```sql
SELECT
  generation_metadata->>'judge_rejected_count' AS judge_rej,
  generation_metadata->>'rejected_count' AS schema_rej,
  generation_metadata->>'api_calls' AS api_calls
FROM datasets WHERE id = '<sdg_tool_dataset_id>';
```

Expected: `judge_rej > 0` (Judge filter ทำงาน) + `api_calls` ~ 30-100

---

## Task 6 — SDG generate `description_only`

```
POST /api/v1/datasets/generate
{
  "sdg_mode": "description_only",
  "project_id": "<tool_project_id>",
  "task_type": "tool_calling",
  "task_description": "Translate kitchen / smart-home commands into JSON tool calls",
  "num_samples": 15,
  "temperature": 0.7,
  "tool_calling_config": {
    "tool_definitions": [
      {
        "name": "set_oven",
        "description": "Set oven temperature in Celsius",
        "parameters": {
          "celsius": {"type": "integer", "description": "Target temp", "required": true}
        }
      },
      {
        "name": "start_timer",
        "description": "Start a kitchen countdown timer",
        "parameters": {
          "minutes": {"type": "number", "required": true}
        }
      },
      {
        "name": "set_volume",
        "description": "Set audio volume level",
        "parameters": {
          "level": {"type": "integer", "description": "0-100", "required": true}
        }
      },
      {
        "name": "play_music",
        "description": "Start playing music",
        "parameters": {
          "genre": {"type": "string", "required": true},
          "room": {"type": "string", "required": false}
        }
      },
      {
        "name": "light_on",
        "description": "Turn on the lights in a room",
        "parameters": {
          "room": {"type": "string", "required": true}
        }
      }
    ]
  }
}
```

### ✅ Expected: 202 → poll → completed

🔖 **เก็บ → `<sdg_tool_descr_id>`**

### Verification: เหมือน Task 5 แต่ไม่มี seed examples

---

## Task 7 — Negative: legacy `seed_data` → 422

```
POST /api/v1/datasets/generate
{
  "sdg_mode": "with_seed",
  "project_id": "<tool_project_id>",
  "task_type": "tool_calling",
  "task_description": "test legacy",
  "num_samples": 5,
  "seed_data": [
    {"question": "test", "answer": "{\"name\":\"set_oven\",\"parameters\":{\"celsius\":200}}"}
  ]
}
```

### ✅ Expected: 422

---

## Task 8 — Negative: legacy `teacher_model` → 422

```
POST /api/v1/datasets/generate
{
  "sdg_mode": "description_only",
  "project_id": "<tool_project_id>",
  "task_type": "tool_calling",
  "task_description": "test",
  "num_samples": 5,
  "tool_calling_config": {
    "tool_definitions": [
      {"name": "set_oven", "description": "test", "parameters": {"celsius": {"type": "integer", "required": true}}}
    ]
  },
  "teacher_model": "openai/gpt-4o-mini"
}
```

### ✅ Expected: 422

---

## Task 9 — Negative: missing `tool_calling_config` ใน description_only → 422

```
POST /api/v1/datasets/generate
{
  "sdg_mode": "description_only",
  "project_id": "<tool_project_id>",
  "task_type": "tool_calling",
  "task_description": "missing config",
  "num_samples": 5
}
```

### ✅ Expected: 422

```json
{
  "detail": "description_only + tool_calling requires tool_calling_config.tool_definitions"
}
```

---

## Task 10 — Negative: invalid tool name in seed (validator should reject)

**วัตถุประสงค์:** seed ที่มี `answer.name` ผิด schema (ไม่ใช่ valid JSON) ต้องถูก reject

### Steps

สร้างไฟล์ `seed_invalid.jsonl`:
```jsonl
{"question": "Set the oven to 200", "answer": "{\"name\":\"set_oven\",\"parameters\":{\"celsius\":200}}"}
{"question": "Bad row 1", "answer": "this is not JSON"}
{"question": "Bad row 2", "answer": "{\"name\":123,\"parameters\":{}}"}
{"question": "Good row", "answer": "{\"name\":\"start_timer\",\"parameters\":{\"minutes\":5}}"}
```

Upload → Expected: 201 + `invalid_rows: [1, 2]` + `num_samples: 2`

> Pydantic validator (`ToolCallingSample.answer`) จับ JSON ที่ malformed หรือ `name` ไม่ใช่ string

---

## Task 11 — Edge: parameter type mismatch in seed

**วัตถุประสงค์:** ทดสอบว่า `_check_tool_call` validator (validators.py) จับ parameter type ผิด

### Steps

ไฟล์ JSONL:
```jsonl
{"question": "Set timer", "answer": "{\"name\":\"start_timer\",\"parameters\":{\"minutes\":\"five\"}}"}
```

ใน description_only mode — declare:
```json
"start_timer": {"minutes": {"type": "number", "required": true}}
```

Generator generate row ที่ผิด type — validator จะ reject (ไม่ผ่าน `_type_matches`)

> ในเทสต์จริงต้องดู `rejected_count` ใน metadata หลัง SDG run — ถ้ามีค่า > 0 หมายถึง validator detect type mismatch ของ Generator

---

## ✅ Test Completion Checklist

- [ ] Task 1 — canonical .jsonl → `ran: false`, `num_samples: 8`
- [ ] Task 2 — canonical .json → `ran: false`, `num_samples: 8`
- [ ] Task 3 — mismatched .jsonl → `ran: true`, mapping `instruction→question, function_call→answer`
- [ ] Task 4 — mismatched .json → identical
- [ ] Task 5 — SDG with_seed → metadata มี `judge_rejected_count > 0`
- [ ] Task 5 — sentinel `no_tool_needed` rows ~10%
- [ ] Task 5 — ทุก row's `answer.name` อยู่ใน working tool set
- [ ] Task 5 — `answer` ทุก row decode JSON ได้
- [ ] Task 6 — SDG description_only → completed
- [ ] Task 7 — legacy `seed_data` → 422
- [ ] Task 8 — legacy `teacher_model` → 422
- [ ] Task 9 — missing tool_calling_config → 422

ผ่านครบ 12 ข้อ = Tool Calling SDG pipeline สมบูรณ์ Phase 9 ✅

---

## Troubleshooting

| อาการ | สาเหตุ | แก้ |
|-------|--------|-----|
| Upload-seed 422 บน canonical file | `answer` ไม่ใช่ JSON-encoded string ที่ valid | ตรวจ escape chars `\"` ใน JSON file |
| Generator output ที่ `answer` เป็น object (ไม่ใช่ string) | LLM hallucinate | Validator + Judge ควร reject — ดู `rejected_count` |
| Sentinel `no_tool_needed` ไม่ปรากฏ | sentinel injection logic | เช็ค `tail /tmp/celery.log` หา "sentinel injection" |
| Parameter type ผิด (`celsius: "two hundred"`) | LLM ไม่เคารพ schema | Validator (`_check_tool_call`) จะจับ ดู `rejected_count` |
| `field_mapping` มี `instruction → instruction` (no-op) | LLM ตอบ self-mapping | ปกติเราจัดการให้ — `_parse_mapping` filter `k != v` ออก |

---

## Cost Estimate

| Task | LLM | Calls | ราคา |
|------|-----|-------|------|
| Task 1-2 | none | 0 | $0 |
| Task 3-4 | gemini-2.5-flash-lite | 2 | <$0.001 |
| Task 5 | qwen-235b + gpt-4o-mini + gemini-flash-lite | ~50-100 | $0.05-0.10 |
| Task 6 | same | ~40-80 | $0.04-0.08 |
| Task 7-11 | none | 0 | $0 |

**Total:** ~$0.10-0.20
