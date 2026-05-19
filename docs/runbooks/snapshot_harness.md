# Snapshot Harness Runbook — Characterization Tests for Refactor Safety

> **Goal:** จับ behavior ของ pure functions + orchestrators ผ่าน syrupy snapshot — รัน "ก่อน refactor" และ "หลัง refactor" เพื่อพิสูจน์ว่า output เหมือนเดิมแบบ byte-equivalent
> **Estimated time:** 0 (run อยู่ทุก `pytest tests/unit`)
> **Cost:** $0 — ไม่เรียก LLM, ไม่ใช้ infra
> **Prerequisites:** dev deps ติดตั้งครบ (`pip install -e ".[dev,eval]"`)

---

## 0. ภาพรวม 3 Tier

| Tier | สิ่งที่ครอบ | external deps | speed | สถานะ |
|------|------------|--------------|-------|------|
| **1** — pure-function snapshots | `prompts.py` builders, `metrics_*.compute_metrics()`, `generator.py` pure helpers (`_compute_*_quota`, `_group_*`) | ไม่มี | <1s ต่อ test | ✅ live |
| **2** — mocked-external snapshots | `SyntheticDataGenerator.generate()` full pipeline, eval task ทั้งก้อน | OpenRouter / Ollama / MinIO / Redis ผ่าน mock | <3s ต่อ test | 🔧 scaffold + smoke ผ่าน, รอ recorded fixtures |
| **3** — live characterization | full E2E ผ่าน compose stack — SDG → train → export → eval, 1 epoch, 5 rows | ต้องมี vast.ai + OPENROUTER_API_KEY | ~10 นาที | ⏳ ยังไม่ได้สร้าง |

ปัจจุบัน 43 snapshots ใน Tier 1 ผ่านเสถียร 100% reproducibility — ดู
`tests/unit/__snapshots__/*.ambr` ที่ checked-in

---

## 1. ติดตั้ง / setup

```bash
# All test infra (Tier 1 ทำงานทันที; Tier 2 มี mock primitives พร้อม)
pip install -e ".[dev,eval]"

# ตรวจ syrupy plugin ติดมาแล้ว
pytest --help | grep snapshot
```

ไม่ต้องตั้ง env vars สำหรับ Tier 1+2 — โค้ดใช้ `monkeypatch` ภายในเอง

---

## 2. Workflow — ก่อน/หลัง refactor

> 🎯 **กฎหลัก:** ห้ามเริ่ม refactor ถ้า snapshot ยังไม่ green กับ baseline ปัจจุบัน

```bash
# 1. เริ่ม branch — ตรวจ baseline ผ่านก่อน
git checkout -b feature/<refactor-target>
pytest -m "not integration" -q

# คาดหวัง: 176+ passed, 3 skipped (Tier 2 scaffolds), 0 failed

# 2. (optional) regen snapshots — เฉพาะถ้าเพิ่มเคสใหม่ที่ตั้งใจ
pytest tests/unit/test_snapshot_<new>.py --snapshot-update
git add tests/unit/__snapshots__/
git commit -m "test(harness): add snapshots for <new>"

# 3. <refactor code>
# ระหว่าง refactor: pytest -q ตามจังหวะ
# snapshot diff = 0 = ปลอดภัย; diff ≠ 0 = ต้องอธิบายเหตุ

# 4. ถ้า snapshot fail หลัง refactor:
#    (a) การเปลี่ยนตั้งใจ (เช่น เปลี่ยน prompt wording)
#        → pytest --snapshot-update
#        → diff snapshot ใน git, อธิบายใน commit message
#    (b) การเปลี่ยนไม่ตั้งใจ
#        → revert code, retry

# 5. (optional) Tier 3 live smoke ก่อน merge — ถ้าแก้ในโซน hot
pytest -m characterization   # หลังมี recorded fixtures
```

---

## 3. ไฟล์โครงสร้าง

```
tests/
├── conftest.py                              # shared fixtures (openrouter, fake_minio, fake_redis, seed factory)
├── unit/
│   ├── __snapshots__/                       # syrupy baselines (committed!)
│   │   ├── test_snapshot_prompts.ambr
│   │   ├── test_snapshot_generator_builders.ambr
│   │   └── test_snapshot_metrics.ambr
│   ├── test_snapshot_prompts.py             # Tier 1
│   ├── test_snapshot_generator_builders.py  # Tier 1
│   ├── test_snapshot_metrics.py             # Tier 1
│   └── test_snapshot_generator_full.py      # Tier 2 scaffold (skips until fixtures land)
└── fixtures/
    └── recorded/
        └── openrouter/                      # captured payloads (committed)
            ├── sdg_classification_meta.json    ⏳ ยังไม่มี
            ├── sdg_classification_batch.json   ⏳
            ├── sdg_classification_judge.json   ⏳
            ├── sdg_qa_meta.json                ⏳
            ├── sdg_qa_batch.json               ⏳
            ├── sdg_qa_judge.json               ⏳
            ├── sdg_tool_calling_meta.json      ⏳
            ├── sdg_tool_calling_batch.json     ⏳
            └── sdg_tool_calling_judge.json     ⏳
```

---

## 4. Live capture — เพิ่ม recorded payload สำหรับ Tier 2

จุดประสงค์: ดักจับ OpenRouter response จริง 1 ครั้ง แล้วเล่นซ้ำใน CI ตลอดอายุ harness

### 4.1 ขั้นตอน (จะทำใน vast.ai session ถัดไป)

```bash
# 1. เปิด stack บน vast.ai + ตั้ง OPENROUTER_API_KEY
ssh -p <vast-port> root@<vast-ip> "docker compose up -d"

# 2. รัน SDG smoke 3 ก้อน (cls / qa / tool) แบบเล็ก: num_samples=10, holdout=0
#    ใช้ scripts/swagger_smoke_section_*.py หรือ Swagger UI

# 3. ดักจับ response — มี 3 ทางเลือก:
#    (a) `respx --record` mode (ต้องแก้ openrouter_client ให้ใช้ httpx โดยตรงชั่วคราว)
#    (b) ดึงจาก mlflow/logs container ที่บันทึก raw response ไว้
#    (c) แก้ AsyncOpenRouterClient ชั่วคราวให้ dump response เป็น JSON ลง /tmp ก่อน return

# 4. คัดลอกมาเป็นไฟล์ใน tests/fixtures/recorded/openrouter/
#    ตามชื่อใน test_snapshot_generator_full.py
```

### 4.2 Schema ของไฟล์ recorded

ใช้ schema เดียวกับ OpenAI SDK ChatCompletion response — เก็บเป็น JSON dict ที่มี:

```json
{
  "id": "gen-...",
  "model": "google/gemini-2.5-flash",
  "choices": [
    { "message": { "role": "assistant", "content": "<json content>" },
      "finish_reason": "stop" }
  ],
  "usage": { "prompt_tokens": ..., "completion_tokens": ..., "total_tokens": ... }
}
```

> 💡 หลังเอาเข้า เปิด PR แล้วใช้ subject `test(harness): record OpenRouter fixtures for SDG snapshot` พร้อม timestamp + judge_model id ใน body — ช่วยให้ regen ภายหลังตัดสินใจได้ว่ายัง valid

---

## 5. เมื่อไหร่ update snapshot vs revert code

| เหตุการณ์ | การตัดสินใจ |
|----------|-------------|
| Refactor `generator.py` แล้ว `_compute_classification_quota` snapshot เปลี่ยน | **revert** — ไม่ควรเปลี่ยน math; bug |
| เปลี่ยน prompt wording ที่ตั้งใจ (เช่น เพิ่ม [Difficulty] clause) | **update** + อธิบาย wording change ใน commit message |
| เปลี่ยน Pydantic schema ของ `ToolDefinition` | **update** — schema change เป็นเรื่องตั้งใจ; ต้องระบุ migration plan ใน PR |
| sklearn version bump → confusion_matrix.tolist() เปลี่ยน | **update** — แต่ pin sklearn version ใน pyproject.toml ป้องกัน drift |
| Snapshot ผ่าน 1 ครั้ง แต่อีกรอบไม่ผ่าน (flake) | **investigate** — ไม่ใช่ update; harness ออกแบบมาให้ deterministic |

---

## 6. ขีดจำกัดที่รู้ไว้ก่อน

- Tier 2 mocked-Ollama ยังไม่มี (`workers/ollama_client.py` mock pending) → eval task snapshot ยังต้องรอ
- `aiosqlite` / `pytest-postgresql` ยังไม่ติด — DB-touching tests ยังต้องใช้ Tier 3 (compose stack)
- GPU/Unsloth ไม่ mock — training pipeline snapshot จะถูก gate ด้วย `@pytest.mark.gpu`
- Recorded payload **decay**: ถ้า OpenRouter retire model หรือเปลี่ยน response schema (เคยเกิด — MT.B4), recorded fixture จะ stale; ตรงนี้ runbook ระบุให้ regen ทุก ~3 เดือนหรือเมื่อ OpenRouter ประกาศ deprecation

---

## 7. Rollout roadmap (ดู `~/.claude/plans/peppy-sauteeing-rain.md`)

หลัง pilot นี้ผ่าน, node ถัดไปที่จะ wrap:

| Iter | Node | Module | Tier |
|---|---|---|---|
| 2 | Training | `unsloth_trainer.py` | 1 + 2 (tiny config + mock Unsloth) |
| 3 | Export | `workers/tasks/model_export.py` | 2 (mock Ollama + MinIO + subprocess) |
| 4 | Eval | `workers/tasks/evaluation.py` | 1 + 2 (pure metrics + mock Ollama+OpenRouter) |
| 5 | SDG worker | `workers/tasks/data_generation.py` | 2 (DB+Redis+MinIO+OpenRouter) |
| 6 | HPO | `optuna_objective.py` + worker | 1 + 2 |
| 7 | API CRUD | `api/services/{projects,datasets}_service.py` | 2 |
| 8 | Tier 3 E2E | 11 nodes mini-flow | 3 (live stack) |
