# Architecture Decision Records

Each ADR records one decision that is expensive to reverse: the context that
forced it, the choice made, and the consequences we accepted. Supersede an ADR
with a new one rather than editing a decided record in place.

## Index

| ADR | Decision | Status |
|-----|----------|--------|
| 001–005, 007 | See below — historical, no separate files | Accepted |
| [006](./ADR-006-defer-authentication.md) | Defer API authentication; keep the platform unauthenticated behind a private network for now | **Superseded by 009** |
| [008](./ADR-008-ws-progress-snapshot.md) | Add a Redis last-frame snapshot to the WebSocket progress transport, and two new `WSMessageType` values | Accepted |
| [009](./ADR-009-supabase-jwt-auth.md) | Verify Supabase JWTs and enforce per-user ownership; two-phase rollout behind `AUTH_REQUIRED` | Accepted |

**Take the next free number from this table, and grep the codebase first** —
`ADR-008` above began life as `ADR-007` and had to be renumbered across 24
references because `ADR-007` was already in use by Phase 9 (see below) with no
file to make that visible. A number is claimed by any reference to it, not just
by a file in this directory.

## About the file-less ADRs (001–005, 007)

These predate this directory and were never written up as standalone files.
001–005 are recorded as the constraint table in
[`../01-architecture.md`](../01-architecture.md) (mirrored in the repo
`README.md`); 007 lives only as code comments.

| ADR | Constraint | Where it's recorded |
|-----|------------|---------------------|
| ADR-001 | MLflow for experiment tracking (not W&B / TensorBoard) | `01-architecture.md` |
| ADR-002 | Models ≤3B params, must fit in QLoRA 4-bit | `01-architecture.md` |
| ADR-003 | OpenRouter for SDG (not direct OpenAI / Anthropic) | `01-architecture.md` |
| ADR-004 | Celery for async jobs (never FastAPI `BackgroundTasks`) | `01-architecture.md` |
| ADR-005 | Only 3 task types: `classification`, `tool_calling`, `qa` | `01-architecture.md` |
| ADR-007 | Phase 9 SDG hardening; the sync and async OpenRouter clients stay co-located in one module | `pyproject.toml:45`, `ai_engine/data_gen/openrouter_client.py:3` |

They are left as-is deliberately — back-filling records from memory would
invent context that was never written down. Treat those references as
authoritative; write new decisions here as numbered files.

## When an ADR is required

- Changing anything in the constraint table above.
- Adding or changing a value in `api/schemas/enums.py` — that file states its
  own rule: *"These values are part of the public API contract — frontend and
  DB rows depend on the exact string values. Treat additions as breaking
  changes (write an ADR and a migration)."* (A migration is only needed when
  the enum is actually persisted to Postgres; see ADR-008 for a case where it
  was not.)
- Changing the transport or delivery guarantees of `/ws/jobs/{job_id}`.
