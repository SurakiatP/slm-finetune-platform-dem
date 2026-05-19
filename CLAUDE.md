# Claude Code Rules — SLM Fine-Tuning Platform

> **Read this FIRST every session.** Then load the **3 most recent entries**
> from `WORKING_LOG.md` (top of file — newest on top) and the **3 most recently
> updated sections** of `TASK_TRACKER.md` (typically the last phase tables).
> This file is the project's source of truth for conventions, constraints, and process.

---

## Project Mission

Backend-only PoC for an **Automated Small Language Model (SLM) Fine-Tuning Platform**.

**Two-machine workflow** (split by GPU need):
- **Dev laptop** — Acer Nitro / i5-8300H / 16 GB / GTX 1050 Ti 4 GB (sm_61).
  Runs all infra (postgres, redis, minio, mlflow, api) + non-GPU code: schemas,
  CRUD, SDG client, validators, tests. **Cannot** run Unsloth/QLoRA locally —
  sm_61 is below bitsandbytes' 4-bit threshold and 4 GB VRAM is too small anyway.
- **Training server** — RTX 3060 12 GB. Runs `worker` + `ollama` for actual
  fine-tuning, evaluation, and serving. Same compose file, full stack up.

Generates synthetic data via OpenRouter, fine-tunes models ≤3B with Unsloth+QLoRA,
tracks with MLflow, serves via Ollama.

The frontend is built by a teammate — we expose **OpenAPI contracts only**.

Source of truth for requirements: [`require.md`](./require.md)

---

## Hard Constraints (NEVER violate)

- Models **≤3B parameters** and MUST fit in **QLoRA 4-bit**
- **NO authentication** system — all endpoints open
- **NO frontend code** — backend only
- Use **MLflow** for experiment tracking (not W&B, not TensorBoard) — see ADR-001
- Use **OpenRouter** for SDG (not OpenAI/Anthropic direct) — see ADR-003
- Async work goes through **Celery**, never FastAPI `BackgroundTasks` — see ADR-004
- Only **3 supported task types**: classification, tool_calling, qa — see ADR-005

## Forbidden Files (NEVER read/write/commit)

- `.env`, `.env.*` — secrets live here
- `secrets/`, any credentials, API keys
- Any file containing real user PII or production data
- Model weights / checkpoints — use MinIO, never the repo

If you see one of these in a tool result, flag it to the developer and stop.

---

## Required Reading (in order)

1. [`require.md`](./require.md) — original spec (source of truth)
2. [`docs/architecture/OVERVIEW.md`](./docs/architecture/OVERVIEW.md) — system architecture
3. [`docs/architecture/TECH_STACK.md`](./docs/architecture/TECH_STACK.md) — locked stack
4. [`docs/adr/ADR-INDEX.md`](./docs/adr/ADR-INDEX.md) — all accepted decisions
5. [`docs/standards/CODING_STYLE.md`](./docs/standards/CODING_STYLE.md)
6. [`docs/standards/API_CONVENTIONS.md`](./docs/standards/API_CONVENTIONS.md)
7. [`docs/standards/TESTING_GUIDE.md`](./docs/standards/TESTING_GUIDE.md)
8. [`docs/prompts/`](./docs/prompts/) — reusable prompt templates

---

## Session Protocol (Discovery → Execution → Handover)

### 1. Discovery (start of session)
1. Read `CLAUDE.md` (this file) in full
2. Read the **3 most recent sessions** from `WORKING_LOG.md` (newest on top —
   stop after the 3rd `## Session N` header). This is enough context to know
   what's just been done and what's open without flooding the window.
3. Read the **3 most recently updated sections** of `TASK_TRACKER.md`
   (typically the last phase tables — scroll to bottom and read upward until
   3 phase/section headers have been covered)
4. Pick up the latest **Next Action** from the top WORKING_LOG entry
5. Validate scope against `require.md` if anything is unclear
6. Propose an action plan → wait for developer approval

### 2. Execution (during work)
- **Hexagonal discipline**: keep domain logic (`ai_engine/`) isolated from infra (FastAPI, Celery, DB)
- **Test-aware**: design tests alongside logic — don't bolt them on
- **Issue reporting**: never hide bugs or skip failing tests; surface them immediately
- **Pattern reuse**: reference existing code first, never invent new conventions

### 3. Handover (end of session)
Run the **DoD checklist** before claiming "Done":

- [ ] All tests pass (or failures explicitly listed)
- [ ] No debug `print` / commented code / leftover scaffolding
- [ ] Update `WORKING_LOG.md` with: Who / Status / Why & What / Test Summary / Next Action
- [ ] Update `TASK_TRACKER.md` (status + next step)
- [ ] If a major decision was made → write/update an ADR
- [ ] Tell the developer the session is safe to `/clear`

---

## Locked Tech Stack (ADR-001)

| Layer | Tech |
|-------|------|
| API | FastAPI + Pydantic v2 |
| ORM | SQLAlchemy 2.0 (async where possible) |
| Async jobs | Celery + Redis |
| DB | PostgreSQL 16 |
| Object storage | MinIO (S3-compatible) |
| Experiment tracking | **MLflow** |
| SDG provider | **OpenRouter** via `openai` SDK |
| Fine-tuning | Unsloth + QLoRA + TRL |
| HPO | Optuna |
| Inference | Ollama (OpenAI-compatible) |
| Real-time | WebSocket + Redis Pub/Sub |
| Containers | Docker Compose (NVIDIA runtime for GPU) |

---

## Snapshot Harness (refactor safety net)

See [`docs/runbooks/snapshot_harness.md`](./docs/runbooks/snapshot_harness.md).

- 43 syrupy snapshots ใน `tests/unit/__snapshots__/*.ambr` ถูก checked-in — เป็น
  baseline ของ pure functions (prompts, generator helpers, eval metrics)
- **ก่อน refactor ใดๆ:** รัน `pytest -m "not integration" -q` — ต้อง 0 failed ก่อนเริ่ม
- **หลัง refactor:** snapshot diff = 0 = ปลอดภัย; diff ≠ 0 = ตัดสินใจ
  (a) intentional → `pytest --snapshot-update` + อธิบาย diff ใน commit message
  (b) unintentional → revert code, retry
- PR ที่มี `--snapshot-update` ต้องอธิบาย wording/schema change ใน body — ห้าม
  reset เป็นนิสัยโดยไม่อ่าน diff
- ขยาย harness ครอบ node ใหม่: ดู rollout roadmap §7 ใน runbook

---

## Code Style (see `docs/standards/CODING_STYLE.md`)

- Python 3.11+
- `from __future__ import annotations` at the top of every module
- Type hints everywhere
- Pydantic v2 syntax: `model_validator`, `field_validator`, `model_dump`
- SQLAlchemy 2.0 declarative + async session
- `async def` for I/O-bound FastAPI handlers
- `logging` module — never `print` in production code
- Google-style docstrings on public functions
- Config via `pydantic-settings` — **no hardcoded values**

---

## Cost Awareness

- Keep sessions focused on one task; `/clear` afterwards
- `/compact` when context grows past ~50% capacity
- Read only files relevant to the current task
- Use memory files instead of long chat threads — they're cheaper to re-read

---

## Working with the Developer

**Confirmation needed for** (always ask first):
- Destructive ops: dropping tables, force-push, deleting branches, `rm -rf`
- Skipping hooks (`--no-verify`)
- Adding new dependencies outside the locked stack
- Architecture changes that contradict an ADR

**Free to do** (no need to ask):
- Edit/create code in `api/`, `workers/`, `ai_engine/`, `tests/`, `examples/`
- Run tests, lint, type check
- Read any non-forbidden file
- Generate scaffolding for already-approved phases

**Default preferences** (from `require.md`):
- Simplicity > cleverness
- Explicit > implicit
- Well-known patterns > custom solutions
- Strong typing > flexibility

---

## Phase Order (do not skip ahead)

1. **Foundation** — structure, docker-compose, deps, README
2. **Schemas & Models** — Pydantic + SQLAlchemy + Alembic
3. **API Skeleton** — routers + WebSocket + metadata
4. **SDG Pipeline** — OpenRouter + prompts + validators + dedup
5. **Manual Training** — Unsloth + formatters + MLflow callback
6. **HPO Pipeline** — Optuna + nested MLflow runs
7. **Inference & Evaluation** — Ollama, GGUF, metrics, LLM judge
8. **Polish** — error handling, examples, integration test

Each phase ends with developer approval before the next begins.
