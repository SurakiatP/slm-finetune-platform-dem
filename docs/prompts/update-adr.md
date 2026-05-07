# Prompt Template: Write or Supersede an ADR

> Use this when a major decision is being made or an existing ADR no longer fits.

---

## Case A — New ADR (no prior decision)

```
Write ADR-<NNN> for: <one-line decision summary>.

Context:
- Read `docs/adr/ADR-INDEX.md` to pick the next number.
- Read `CLAUDE.md` and `docs/architecture/OVERVIEW.md` for project context.
- Follow the template at the top of `ADR-INDEX.md`.

Drivers:
- <constraint 1>
- <constraint 2>
- <stakeholder need>

Options I'm considering:
- A: <option> — pros: <...> cons: <...>
- B: <option> — pros: <...> cons: <...>
- C: <option> — pros: <...> cons: <...>

My current lean: <A | B | C>, because <reason>.

Required fields in the ADR:
- Status: Proposed (until I approve)
- Confidence: <High | Medium | Low>
- AI Guidance Level: <STRICT | FLEXIBLE | EXPLORATORY> — and a clear list of dos/don'ts for the agent
- Alternatives Considered: spell out why each rejected option was rejected
- Consequences: positive and negative

Then:
- Append the new row to `docs/adr/ADR-INDEX.md`
- Don't change Status to Accepted — wait for my approval
```

---

## Case B — Supersede an existing ADR

```
ADR-<OLD_NNN> no longer fits because <reason>. Write ADR-<NEW_NNN> that supersedes it.

What changed:
- <change 1>
- <change 2>

The new ADR must:
- Reference ADR-<OLD_NNN> in the "Supersedes" field
- Reuse the original Drivers section as a starting point, then add new drivers
- Update AI Guidance — the agent should now <follow new rule> instead of <old rule>

Then:
- Set ADR-<OLD_NNN> Status to "Superseded by ADR-<NEW_NNN>" — this is the ONLY allowed edit to an accepted ADR
- Append the new row to `docs/adr/ADR-INDEX.md` with a note linking to the old one
- Update `CLAUDE.md` if the locked-stack section or hard constraints changed
```

---

## Reminders (from the AI-Native orientation)

- ADRs are **append-only**. Never edit content of an Accepted ADR — supersede instead.
- **Confidence Level** tells the team when to revisit. `High` = stable. `Low` = expect to revisit within a sprint.
- **AI Guidance Level**:
  - **STRICT** — agent MUST follow; breach requires explicit user override
  - **FLEXIBLE** — strong default; agent may deviate with reasoning
  - **EXPLORATORY** — direction only; agent has discretion
- ADRs are **the** guardrail for the agent. Vague ADRs produce sloppy code. Be specific in the AI Instructions section.
