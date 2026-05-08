# Architecture Decision Records

> **Append-only.** Never edit an Accepted ADR — write a new one that supersedes it.
> Every ADR includes `Confidence Level` and `AI Guidance Level` fields.

## ADR Template

Each ADR follows this shape:

```markdown
# ADR-NNN: <Title>

**Status:** Proposed | Accepted | Superseded by ADR-XXX
**Confidence:** High | Medium | Low
**Date:** YYYY-MM-DD
**Supersedes:** ADR-XXX or —

## Context + Decision Drivers
Why does this decision need to be made? What forces are pushing on it?

## Decision
What did we decide? Be concrete.

## Alternatives Considered
- Option A — pros / cons → why rejected
- Option B — pros / cons → why rejected
- Option C — chosen ← what we picked

## AI Instructions
**Guidance Level: STRICT | FLEXIBLE | EXPLORATORY**

- Concrete dos and don'ts for the agent
- Reference files / patterns to follow
- When to alert the developer

## Consequences
✅ Positive
⚠️ Negative / risks
```

## Guidance Level Semantics

| Level | Meaning |
|-------|---------|
| **STRICT** | Agent MUST follow. Breach requires explicit user override. |
| **FLEXIBLE** | Strong default; agent may deviate with a reasoned explanation. |
| **EXPLORATORY** | Direction only; agent has discretion within the spirit of the decision. |

---

## Index

| # | Title | Status | Confidence | Guidance |
|---|-------|--------|------------|----------|
| [001](./ADR-001-locked-tech-stack.md) | Locked Tech Stack | Accepted | High | STRICT |
| [002](./ADR-002-model-size-constraint.md) | Model Size ≤3B + QLoRA 4-bit | Accepted | High | STRICT |
| [003](./ADR-003-openrouter-for-sdg.md) | OpenRouter for Synthetic Data Generation | Accepted | High | STRICT |
| [004](./ADR-004-celery-not-bgtasks.md) | Celery for Async, Not FastAPI BackgroundTasks | Accepted | High | STRICT |
| [005](./ADR-005-three-task-types.md) | Only 3 Supported Task Types | Accepted | High | STRICT |
| [006](./ADR-006-asyncpg-for-async-sqlalchemy.md) | asyncpg for Async SQLAlchemy Sessions | Accepted | High | STRICT |
