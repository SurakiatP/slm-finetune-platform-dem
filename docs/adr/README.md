# Architecture Decision Records

Each ADR records one decision that is expensive to reverse: the context that
forced it, the choice made, and the consequences we accepted. Supersede an ADR
with a new one rather than editing a decided record in place.

## Index

| ADR | Decision | Status |
|-----|----------|--------|
| 001–005 | See below — historical, no separate files | Accepted |
| [006](./ADR-006-defer-authentication.md) | Defer API authentication; keep the platform unauthenticated behind a private network for now | Accepted |
| [007](./ADR-007-ws-progress-snapshot.md) | Add a Redis last-frame snapshot to the WebSocket progress transport, and two new `WSMessageType` values | Accepted |

## About ADR-001 … ADR-005

These five predate this directory and were never written up as standalone
files. They are recorded as a constraint table in
[`../01-architecture.md`](../01-architecture.md) (and mirrored in the repo
`README.md`):

| ADR | Constraint |
|-----|------------|
| ADR-001 | MLflow for experiment tracking (not W&B / TensorBoard) |
| ADR-002 | Models ≤3B params, must fit in QLoRA 4-bit |
| ADR-003 | OpenRouter for SDG (not direct OpenAI / Anthropic) |
| ADR-004 | Celery for async jobs (never FastAPI `BackgroundTasks`) |
| ADR-005 | Only 3 task types: `classification`, `tool_calling`, `qa` |

They are left as-is deliberately — back-filling five records from memory would
invent context that was never written down. Treat the constraint table as
authoritative for those five; write new decisions here as numbered files.

## When an ADR is required

- Changing anything in the constraint table above.
- Adding or changing a value in `api/schemas/enums.py` — that file states its
  own rule: *"These values are part of the public API contract — frontend and
  DB rows depend on the exact string values. Treat additions as breaking
  changes (write an ADR and a migration)."* (A migration is only needed when
  the enum is actually persisted to Postgres; see ADR-007 for a case where it
  was not.)
- Changing the transport or delivery guarantees of `/ws/jobs/{job_id}`.
