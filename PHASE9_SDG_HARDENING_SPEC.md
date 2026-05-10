# Phase 9 — SDG Hardening: Quality Controls + Multi-Modal Seeds

> **Audience:** Claude Code, working in `slm-finetune-platform-dem` on branch `dev`
> **Status:** Approved by parks (developer); ready for implementation
> **Estimated effort:** 4–6 sessions across 3 sub-phases (see roadmap at end)
> **Read first:** `CLAUDE.md`, `WORKING_LOG.md`, `TASK_TRACKER.md`, all 6 ADRs
> **Source of truth for "old behaviour":** the two reference scripts
> `sdg_classification.py` and `sdg_tool_calling.py` provided to parks; treat
> them as PORTING TARGETS, not as code to copy verbatim
> (architecture differs — see §3 Architecture Constraints).

---

## 0. Index

1. Executive Summary
2. Confirmed Decisions (recap)
3. Architecture Constraints
4. Pipeline Changes — Before / After
5. New & Modified Components
6. Schema Changes (Pydantic + ORM + Alembic)
7. Per-Task Pipeline — Classification
8. Per-Task Pipeline — Tool Calling
9. Per-Task Pipeline — QA (with PDF support)
10. LLM Call Patterns (async batching, prompts, models)
11. Stop Conditions & Adaptive Over-Generation
12. API Endpoint Changes
13. Migration & Backwards Compatibility
14. Test Plan
15. Implementation Roadmap (Phase 9.1 → 9.3)
16. CLAUDE.md Compliance Checklist
17. Open Questions / Known Risks

---

## 1. Executive Summary

The current SDG pipeline (Phase 4) is a thin **schema-validate + exact-dup**
loop over a sync OpenRouter client. It works but produces low-quality data
for two reasons:

- **No quality gate** — anything that parses Pydantic ships, even if the
  teacher hallucinated.
- **No semantic dedup** — `Deduplicator` only catches `lower().strip()`
  exact matches; paraphrases slip through.

Phase 9 ports the **proven quality controls** from parks's research-grade
scripts (`sdg_classification.py` v3, `sdg_tool_calling.py`) into the new
hexagonal backend, **adds three new capabilities the old scripts didn't
have**, and **explicitly rejects** the one feature that caused the old
"loop never terminates" pain (FAISS semantic dedup).

**What's added (from old):**
- LLM-as-Judge (fidelity / naturalness / utility, weighted 0.4/0.3/0.3, threshold 0.7)
- MinHash LSH dedup (5-gram char, threshold 0.90)
- Meta-prompting for diversity rules
- Per-class quota (90% real / 10% sentinel) — classification + tool_calling
- `no_tool_needed` sentinel tool — tool_calling only
- Adaptive over-generation multiplier (1.5× → 8×, yield-driven)
- Coverage pool for diversity rules + difficulty rotation

**What's new (not in old):**
- Format Detection LLM — auto-rename misnamed seed keys
- Two scenarios per task: `with_seed` and `no_seed` (no_seed needs
  user-supplied labels / tools / task_description)
- PDF input for QA — pass raw PDF (base64) to multimodal LLM

**What's explicitly rejected:**
- FAISS semantic dedup — caused infinite loops in old version per parks.
  MinHash LSH alone is the dedup story.

---

## 2. Confirmed Decisions (Recap)

| ID | Decision |
|----|----------|
| Q1.1 | Format Detection LLM = `google/gemini-2.5-flash-lite` |
| Q1.2 | Skip Format Detection if seed already matches canonical schema (cost saver) |
| Q1.3 | Format Detection scope = **rename keys only** — no value transforms |
| Q1.4 | Format Detection failure mode = **best-effort** (drop unfixable rows, keep good ones) |
| Q2.1 | QA + no_seed needs only `task_description` |
| Q3.1 | Parallelism = **Option (b)** custom `asyncio.gather` + `AsyncOpenAI`, concurrency 100 |
| Q4.1 | Stop conditions = same defaults as old (`max_loops=20`, `max_consecutive_failures=5`, `judge_threshold=0.7`, `minhash_threshold=0.90`) |
| Q4.2 | Adaptive `over_gen_mult` (1.5× → 8×) — keep |
| Q5.1 | PDF strategy = **base64 → OpenRouter** with `google/gemini-2.5-flash-lite` (multimodal) |
| Q5.2 | PDF chunking = **none** — send whole file in one call |
| Q5.3 | PDF Q&A count = honor `num_samples` from request |
| Q5.4 | PDF persistence = MinIO bucket `datasets`, key prefix `seed-pdfs/{dataset_id}.pdf` |
| Q6.1 | Model strings = **hardcoded** in code (no per-request override) |
| Q7.1 | Upload flow = **separate** — `upload-seed` (multipart) → `seed_dataset_id` → `/datasets/generate` references it |
| Q8.1 | Format Detection runs **once at upload time**; canonicalised JSONL persisted to MinIO; `/generate` reads canonical |

**LLM model assignment (hardcoded constants):**

| Role | Model |
|------|-------|
| Format Detection | `google/gemini-2.5-flash-lite` |
| PDF → Q&A (QA only, multimodal) | `google/gemini-2.5-flash-lite` |
| Diversity Rules (meta-prompting) | `google/gemini-3.1-flash-lite-preview` |
| Synthetic Data Generator | `qwen/qwen3-235b-a22b-2507` |
| Judge | `openai/gpt-4o-mini` |

These live in **one** module (`ai_engine/data_gen/models.py` — new file)
as module-level constants. No env override.

---

## 3. Architecture Constraints

These constraints are **non-negotiable** — they keep the new code aligned
with the rest of the platform:

1. **Hexagonal discipline preserved.** Pure logic stays in `ai_engine/`;
   Celery / DB / MinIO / Redis stays in `workers/` and `api/`.
   Format Detection LLM call lives in `ai_engine/data_gen/format_detector.py`,
   not in the upload service.

2. **No new heavy dependencies.** No `distilabel`, no `faiss-cpu`,
   no `sentence-transformers`. The only new runtime deps are:
   - `datasketch` (≥ 1.6.0) — MinHash LSH (used in old code already proven)
   - `pypdf` (≥ 5.0) — PDF size + page-count probe only (not text extraction —
     we send PDF straight to the multimodal LLM)
   Both go in base deps in `pyproject.toml`. Add an ADR-007 covering this.

3. **All LLM calls go through one async client.** Add `AsyncOpenRouterClient`
   alongside the existing `OpenRouterClient`. Both sit in
   `ai_engine/data_gen/openrouter_client.py`. The sync version stays for
   single-shot calls (Format Detection runs sync — see §10).

4. **Celery worker stays sync at the task body level.** Use
   `asyncio.run(...)` only at the LLM-batch boundary inside the task,
   never above it. Same pattern as `inference_service.py` already follows.

5. **Backwards-incompatible API changes are allowed.** Phase 4's
   `SDGRequestWithSeed.seed_data: list[dict]` (inline) is replaced by
   `seed_dataset_id: UUID` (reference to an uploaded seed). The old
   inline form is removed — there is no production traffic to preserve.

6. **All Phase 9 progress messages reuse the existing `SDGProgress` schema.**
   Add new optional fields if needed (`current_loop`, `current_phase`,
   `judge_rejected`, `dedup_rejected`) but keep the discriminator
   (`type=sdg_progress`) and the channel (`job:{job_id}`).

---

## 4. Pipeline Changes — Before / After

### 4.1 Classification & Tool Calling

**Before (Phase 4):**
```
SDGRequest → SyntheticDataGenerator.generate():
    while collected < target:
        prompt = build_prompt(...)
        chat = openrouter.chat(...)            # SYNC, one call at a time
        rows = parse(chat.content)
        valid, _ = validate_generated_rows(rows)
        unique = dedup.filter(valid)            # exact-string only
        collected += unique
```

**After (Phase 9):**
```
[Upload] /datasets/upload-seed (.json/.jsonl)
    → Format Detection LLM (sync, single call) → canonicalised JSONL → MinIO

[Generate] /datasets/generate (with_seed → seed_dataset_id; no_seed → labels|tools)
    → Celery task workers/tasks/data_generation.generate_synthetic_data
    → SyntheticDataGenerator.generate():
        # Setup (once)
        diversity_rules, sentinel_rules = meta_prompt_rules(...)
        seed_minhashes = pre-load seeds into LSH
        per_class_quota = 90/10 split (sentinel = "unknown" / "no_tool_needed")
        over_gen_mult = 1.5

        while collected < target and loop < max_loops:
            # 1. Build batch (coverage pool over rules + difficulties)
            batch_inputs = [
                {label, examples, rule, difficulty, nonce}
                for each class needing more rows
            ]

            # 2. Generate — ASYNC, batch_size=100 concurrent
            results = await async_openrouter.chat_batch(
                prompts=[generator_prompt(x) for x in batch_inputs],
                concurrency=100,
            )
            candidates = [parse(r) for r in results]   # 5 per call → exploded

            # 3. Schema + business validation
            valid, rejected_schema = validate_generated_rows(candidates)

            # 4. Judge — ASYNC, batch_size=100 concurrent
            judge_scores = await async_openrouter.judge_batch(valid, ...)
            kept = [v for v, s in zip(valid, judge_scores) if s.weighted >= 0.7]
            rejected_judge = len(valid) - len(kept)

            # 5. MinHash LSH dedup
            unique = lsh_dedup(kept, threshold=0.90, n_perm=128)
            rejected_dup = len(kept) - len(unique)

            # 6. Quota-respecting collection
            for row in unique:
                if collected_per_class[row.label] < quota[row.label]:
                    collected.append(row)

            # 7. Adaptive multiplier update
            yield_rate = added_in_loop / total_processed
            over_gen_mult = adapt(over_gen_mult, yield_rate)

            emit SDGProgress(...)
```

### 4.2 QA

**Same as 4.1 but:**
- No per-class quota (no labels)
- No sentinel
- Schema check only (no closed-label check)
- **PDF branch**: if seed_dataset.metadata.has_pdf → first iteration uses
  multimodal Generator (`gemini-2.5-flash-lite` with PDF base64), subsequent
  iterations use text-only Generator (`qwen3-235b`) seeded with first batch's
  output

---

## 5. New & Modified Components

### 5.1 New files

```
ai_engine/data_gen/
├── models.py                      # NEW — hardcoded LLM model constants
├── format_detector.py             # NEW — schema-mismatch detector + key renamer
├── async_openrouter_client.py     # NEW — AsyncOpenAI wrapper, batch primitives
├── meta_prompter.py               # NEW — diversity rules generator (LLM)
├── judge.py                       # NEW — LLM-as-Judge (fidelity/naturalness/utility)
├── minhash_dedup.py               # NEW — MinHashLSH dedup (replaces simple Dedup for SDG;
│                                  # the existing exact-dup Dedup stays for seed upload)
├── coverage_pool.py               # NEW — make_coverage_pool helper (port from old)
└── pdf_loader.py                  # NEW — read PDF bytes, base64 encode, validate

api/schemas/
└── upload.py                      # NEW — schemas for upload-seed multipart payloads
                                  # (format detection report, etc.)
```

### 5.2 Modified files

| File | Change |
|------|--------|
| `ai_engine/data_gen/openrouter_client.py` | Add `AsyncOpenRouterClient`; keep sync `OpenRouterClient` for single-shot calls |
| `ai_engine/data_gen/generator.py` | Major rewrite — orchestrate all 5 new stages; async loop body |
| `ai_engine/data_gen/prompts.py` | Replace templates with old-style RTC-FO structured prompts; add JUDGE_TEMPLATE; add META_PROMPT_TEMPLATE; add FORMAT_DETECT_TEMPLATE; add PDF_QA_TEMPLATE |
| `ai_engine/data_gen/deduplicator.py` | Keep existing exact-dup `Deduplicator` (used at seed-upload time only). Add docstring noting MinHash is the SDG-loop dedup. |
| `ai_engine/data_gen/validators.py` | Unchanged behaviour, but accept new sentinel labels (`unknown`, `no_tool_needed`) without rejecting them as "out of label set" |
| `api/schemas/sdg.py` | Replace `SDGRequestWithSeed.seed_data` with `seed_dataset_id: UUID`; keep description-only branch |
| `api/schemas/data_formats.py` | Add helper `canonical_field_names(task_type) → set[str]` for Format Detection |
| `api/schemas/datasets.py` | `DatasetResponse.generation_metadata` already JSONB — extend to carry `format_detection_report`, `pdf_uri`, `sentinel_quota`, etc. (no schema change) |
| `api/services/datasets_service.py` | Extend `upload_seed_dataset` to (a) accept `.pdf` for QA, (b) run Format Detection on JSON/JSONL, (c) persist canonicalised JSONL + report |
| `api/services/sdg_service.py` | Validate `seed_dataset_id` exists + matches task_type; pass canonical seed URI to worker |
| `workers/tasks/data_generation.py` | Hand-off canonical seed URI + (optional) PDF URI; use new `SyntheticDataGenerator` flow |

### 5.3 Deleted code

- The current Phase 4 inline `seed_data` field in `SDGRequestWithSeed`
- The current Phase 4 SDG loop body in `generator.py` (rewritten, not patched)

---

## 6. Schema Changes (Pydantic + ORM + Alembic)

### 6.1 Pydantic — request schemas

**`api/schemas/sdg.py`** — modify `SDGRequestWithSeed`:

```python
class SDGRequestWithSeed(_SDGRequestBase):
    sdg_mode: Literal[SDGMode.WITH_SEED] = SDGMode.WITH_SEED
    seed_dataset_id: UUID = Field(
        ...,
        description=(
            "ID of a previously-uploaded seed dataset (POST /datasets/upload-seed). "
            "The dataset's source must be DatasetSource.SEED and task_type must match."
        ),
    )
    # REMOVED: seed_data (was inline list[dict])
    # The seed rows now live in MinIO (canonicalised by Format Detection).
```

**`SDGRequestDescriptionOnly`** — add QA-no-seed acceptance:

```python
# Existing: classification needs classification_config; tool_calling needs tool_calling_config.
# QA already needs no extra config — already supported. No code change.
```

### 6.2 Pydantic — new schemas (`api/schemas/upload.py`)

```python
class FormatDetectionReport(BaseModel):
    """Stored in Dataset.generation_metadata['format_detection']."""
    model_config = ConfigDict(extra="forbid")
    ran: bool                                          # False if seed was already canonical
    model_used: str | None                             # e.g. "google/gemini-2.5-flash-lite"
    field_mapping: dict[str, str]                      # {"text1": "text", "answer": "label"}
    rows_total: int
    rows_canonicalised: int                            # successfully renamed
    rows_dropped: int                                  # unfixable, dropped (best-effort)
    notes: str | None = None
```

### 6.3 Pydantic — Judge output (`ai_engine/data_gen/judge.py`)

```python
class JudgeScore(BaseModel):
    """One judge output for one row."""
    model_config = ConfigDict(extra="forbid")
    fidelity: float = Field(..., ge=0.0, le=1.0)
    naturalness: float = Field(..., ge=0.0, le=1.0)
    utility: float = Field(..., ge=0.0, le=1.0)
    reasoning: str

    @property
    def weighted(self) -> float:
        return 0.4 * self.fidelity + 0.3 * self.naturalness + 0.3 * self.utility
```

### 6.4 Pydantic — Meta-prompting output (`ai_engine/data_gen/meta_prompter.py`)

```python
class SDGRules(BaseModel):
    diversity_rules: list[str] = Field(..., min_length=8)
    unknown_diversity_rules: list[str] = Field(..., min_length=5)
    # For tool_calling: rename to out_of_scope_rules at the call-site
    # (old code did this; keep the same naming convention).
```

### 6.5 ORM / Alembic

**No new tables; no new columns.** All Phase 9 metadata fits inside the
existing `Dataset.generation_metadata` JSONB column. Add ONLY a new
documentation comment in `api/models/dataset.py` listing the keys we now
expect:

```python
generation_metadata: Mapped[dict[str, Any] | None] = mapped_column(
    JSONB,
    nullable=True,
    doc=(
        "Free-form JSON. Keys used by SDG: 'sdg_mode', 'task_description', "
        "'teacher_model', 'temperature', 'requested_samples', 'submitted_at', "
        "'celery_task_id', 'completed_at', 'rejected_count', 'duplicate_count', "
        "'api_calls', 'format_detection' (FormatDetectionReport), 'pdf_uri', "
        "'sentinel_quota', 'judge_rejected', 'dedup_rejected', 'tool_definitions'."
    ),
)
```

No Alembic migration needed.

---

## 7. Per-Task Pipeline — Classification

### 7.1 Setup phase (once per job)

1. Resolve seed: load `Dataset` by `seed_dataset_id`, fetch canonical JSONL
   from MinIO, group by label → `label_examples: dict[str, list[str]]`.
2. Detect labels:
   - `with_seed` mode → labels = keys of `label_examples`
   - `no_seed` mode → labels = `request.classification_config.labels`
3. Compute quota:
   - `target_unknown = ceil(target_count * 0.10)`
   - `target_others  = target_count - target_unknown`
   - `target_per_class = ceil(target_others / num_real_classes)`
   - Append `"unknown"` to working labels list.
4. Generate diversity rules via Meta-Prompting LLM
   (`google/gemini-3.1-flash-lite-preview`) — call `meta_prompter.generate(
   task_description, labels)` → `SDGRules` with ≥8 normal + ≥5 unknown rules.
5. Pre-load seed `MinHash`es into the global LSH at threshold=0.90, n_perm=128.

### 7.2 Loop phase

Each iteration (max 20):

```
needed_per_label = {l: max(0, quota[l] - collected[l]) for l in labels}

batch = []
for label, n in needed_per_label.items():
    generate_quota = max(1, ceil((n / 4.0) * over_gen_mult))
    generate_quota = min(generate_quota, max(2, n))   # cap

    rules_pool = make_coverage_pool(
        diversity_rules if label != "unknown" else unknown_diversity_rules,
        generate_quota,
    )
    diff_pool = make_coverage_pool(["easy", "medium", "hard", "complex-structure"], generate_quota)

    for i in range(generate_quota):
        examples = (
            [] if label == "unknown"
            else random.sample(label_examples[label], min(3, len(label_examples[label])))
        )
        nonce = uuid4().hex[:8]
        batch.append({
            "task_description": task_description,
            "label": label,
            "examples": examples,
            "difficulty": diff_pool[i],
            "diversity_rule": f"{rules_pool[i]} [variation_id={nonce}]",
        })

random.shuffle(batch)

# 1. Generate — async, concurrency=100
generator_prompts = [build_generator_prompt(b) for b in batch]
gen_results = await async_or.chat_batch(
    prompts=generator_prompts,
    model=models.GENERATOR,
    response_format={"type": "json_object"},
    concurrency=100,
)
candidates = []
for raw, b in zip(gen_results, batch):
    for text in extract_multiple_outputs(raw):
        candidates.append({"text": text, "label": b["label"]})

# 2. Schema + business validation
valid, _failures = validate_generated_rows(
    TaskType.CLASSIFICATION, candidates,
    classification_labels=list(labels),  # includes "unknown" as accepted label
)

# 3. Judge — async, concurrency=100
judge_prompts = [build_judge_prompt(task_description, v["text"], v["label"]) for v in valid]
judge_raw = await async_or.chat_batch(
    prompts=judge_prompts, model=models.JUDGE,
    response_format={"type": "json_object"}, concurrency=100,
    temperature=0.0,
)
keep = []
rej_judge_score = rej_judge_parse = 0
for v, raw in zip(valid, judge_raw):
    try:
        score = JudgeScore.model_validate_json(raw)
    except (ValidationError, JSONDecodeError):
        rej_judge_parse += 1
        continue
    if score.weighted < 0.7:
        rej_judge_score += 1
        continue
    keep.append(v)

# 4. MinHash dedup
unique = []
rej_dup = 0
for row in keep:
    m = compute_minhash(row["text"], num_perm=128)
    if global_lsh.query(m):
        rej_dup += 1
        continue
    global_lsh.insert(f"gen_{lsh_counter}", m)
    lsh_counter += 1
    unique.append(row)

# 5. Quota-respecting collect
added = 0
for row in unique:
    if collected_counts[row["label"]] >= quota[row["label"]]:
        continue
    collected.append(row)
    collected_counts[row["label"]] += 1
    added += 1
    if sum(collected_counts.values()) >= target_count:
        break

# 6. Adaptive multiplier
processed = len(batch) * 5  # ≈ candidate count expected
yield_per_req = added / max(1, len(batch))
if yield_per_req > 0.05:
    new_mult = max(1.5, min(8.0, 1.0 / yield_per_req))
    over_gen_mult = 0.5 * over_gen_mult + 0.5 * new_mult
else:
    over_gen_mult = min(8.0, over_gen_mult * 1.5)

# 7. Emit progress
publish(SDGProgress(
    job_id=job_id,
    phase="generating",
    samples_generated=sum(collected_counts.values()),
    samples_target=target_count,
    samples_valid=len(valid),
    samples_rejected=rej_judge_score + rej_judge_parse + rej_dup,
    duplicates_removed=rej_dup,
))
```

### 7.3 Termination

- `sum(collected_counts.values()) >= target_count` → success
- `loop_count > max_loops (20)` → partial success; persist what you have +
  log per-label completion
- `consecutive_failures >= max_consecutive_failures (5)` (a "failed loop"
  = 0 added) → raise `SDGAbortedError`, mark task FAILED

---

## 8. Per-Task Pipeline — Tool Calling

Same as §7 with these substitutions:

| Classification | Tool Calling |
|----------------|--------------|
| label | tool name |
| `"unknown"` sentinel | `"no_tool_needed"` sentinel |
| `unknown_diversity_rules` | `out_of_scope_rules` |
| `label_examples: dict[str, list[str]]` | `tool_examples: dict[str, list[ToolCallingSample]]` |
| Output: `{"text": ..., "label": ...}` | Output: `{"question": ..., "tool_call": {"name": ..., "parameters": {...}}}` |

**Sentinel injection** (in-memory only, do NOT write back to tools_spec):

```python
SENTINEL_TOOL_NAME = "no_tool_needed"
SENTINEL_TOOL_SPEC = {
    "type": "function",
    "function": {
        "name": SENTINEL_TOOL_NAME,
        "description": (
            "Use this when the user's request does not match any available tool "
            "(off-topic small talk, ambiguous queries, or requests outside the catalog). "
            "Returns no parameters; the orchestrator handles the response."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

# At job start:
tools_by_name = {t.name: t for t in tool_definitions}
tools_by_name[SENTINEL_TOOL_NAME] = SENTINEL_TOOL_SPEC
```

**Output format** is the canonical `ToolCallingSample` form — `answer` is a
JSON-encoded string of `{"name": ..., "parameters": {...}}`. The output JSONL
shipped to MinIO follows the existing `data_formats.py` shape; no distil
labs–specific wrapper.

**Schema validation extra step** (Python-side, complements Judge):
- `tool_call.name` must be in `tools_by_name`
- All required parameters present
- Parameter types match (lenient JSON-Schema-style; bool ≠ int)

This already exists in current `validators.py` `_check_tool_call`; just
make it recognise the sentinel.

---

## 9. Per-Task Pipeline — QA (with PDF support)

### 9.1 Three input modes

| Mode | User provides | Behaviour |
|------|---------------|-----------|
| `with_seed` (JSON/JSONL) | seed_dataset_id (canonicalised) | Same loop as §7 minus quota |
| `with_seed` (PDF) | seed_dataset_id (with `pdf_uri` in metadata) | First batch: PDF → multimodal Generator. Subsequent batches: text-only Generator using first batch as in-context examples |
| `no_seed` | task_description only | Same loop as §7 minus quota; no examples in generator prompt |

### 9.2 Setup differences vs §7

- **No quota.** All `target_count` rows count toward one bucket.
- **No sentinel.** No "unknown" / "no_tool_needed" class.
- **No labels.** `validators.py` schema check only.
- **PDF branch** (if `pdf_uri` is set):
  - Download PDF from MinIO once.
  - First iteration only: use multimodal Generator
    (`google/gemini-2.5-flash-lite`) with PDF base64 + prompt asking for
    `min(num_samples, 50)` Q&A pairs from the document.
  - Drop the PDF from subsequent prompts (it's expensive); use the PDF-derived
    Q&As as seed examples for the text-only Generator from iteration 2 onward.

### 9.3 Diversity rules

Meta-prompter still runs; it generates `diversity_rules` only (no
`out_of_scope_rules` for QA). Use only the `diversity_rules` field.

### 9.4 Judge prompt

Same JUDGE_TEMPLATE structure but `fidelity` for QA means "answer is
correct given the question" — phrase the judge prompt accordingly.

---

## 10. LLM Call Patterns

### 10.1 Sync vs async — the rule

| Caller | Calls | Pattern |
|--------|-------|---------|
| Format Detection (upload-seed endpoint) | 1 LLM call per upload | **Sync** — runs inline in the FastAPI request handler (acceptable: gemini-flash-lite is <1s) |
| Meta-prompting (job setup) | 1 LLM call per job | **Sync** — runs inside Celery task body before the async batch loop |
| Generator (per loop) | up to ~100 calls per loop iteration | **Async** — `asyncio.gather` with semaphore, concurrency=100 |
| Judge (per loop) | up to ~500 calls per loop iteration (5× generator) | **Async** — same pattern |
| PDF Generator (QA only, first iteration) | 1 call | **Sync** — multimodal call is a single shot |

### 10.2 `AsyncOpenRouterClient` — the new wrapper

Add to `ai_engine/data_gen/openrouter_client.py`:

```python
class AsyncOpenRouterClient:
    def __init__(self, api_key, http_referer, app_title, timeout=120.0): ...

    async def chat_batch(
        self,
        *,
        prompts: list[Prompt],          # list of (system, user) pairs
        model: str,
        temperature: float = 0.7,
        response_format: dict | None = None,
        concurrency: int = 100,
        per_call_timeout: float = 120.0,
    ) -> list[ChatResult | Exception]:
        """Fire all prompts concurrently under a semaphore.
        Returns results in input order. Failed calls return their exception
        (caller filters/logs). Uses tenacity inside the per-call wrapper for
        retries on connect/timeout/429.
        """
```

**Why semaphore + gather, not `asyncio.Queue` + workers?** Same throughput,
half the code, error path is `gather(return_exceptions=True)` which is
exactly what we need. OpenRouter's per-org rate limit is generous enough at
100 concurrent.

### 10.3 Calling pattern inside Celery task

```python
# workers/tasks/data_generation.py — inside generate_synthetic_data
import asyncio
...
async def _run_loop():
    return await generator.generate(request, progress_cb=emit_progress)

result = asyncio.run(_run_loop())
```

`SyntheticDataGenerator.generate()` becomes `async def`. The Celery task
body itself stays sync; `asyncio.run` is the boundary.

### 10.4 PDF call (multimodal)

OpenRouter's `gemini-2.5-flash-lite` accepts PDF via the OpenAI-compat
multimodal `messages.content` array:

```python
messages = [
    {"role": "system", "content": "You are generating QA pairs from the attached document."},
    {"role": "user", "content": [
        {"type": "text", "text": pdf_qa_prompt(num_samples, task_description)},
        {"type": "file", "file": {"filename": "seed.pdf",
                                  "file_data": f"data:application/pdf;base64,{b64}"}},
    ]},
]
resp = sync_or_client.chat_completions_create(model="google/gemini-2.5-flash-lite", messages=messages, ...)
```

Sanity-check OpenRouter's docs at implementation time — schema can drift.

### 10.5 Prompts — port targets

Treat these old-script sections as **reference implementations**:

| New prompt | Source (old script) |
|------------|---------------------|
| `GENERATOR_SYSTEM_PROMPT` (classification) | `sdg_classification.py` lines 63–89 |
| `UNIFIED_GENERATOR_TEMPLATE` (classification) | lines 92–112 |
| `JUDGE_TEMPLATE` (classification) | lines 114–134 |
| Meta-prompt for rules | lines 226–245 |
| `GENERATOR_SYSTEM_PROMPT` (tool_calling) | `sdg_tool_calling.py` lines 175–212 |
| `UNIFIED_GENERATOR_TEMPLATE` (tool_calling) | lines 216–253 |
| `JUDGE_TEMPLATE` (tool_calling) | lines 256–289 |
| Meta-prompt for tool_calling | lines 502–539 |
| Format Detection prompt | **NEW — see §10.6** |
| QA + PDF prompt | **NEW — see §10.7** |
| QA Judge prompt | adapt classification JUDGE_TEMPLATE; redefine `fidelity` as "answer is correct" |

Port the **structure and intent**, rewrite to match new naming. Keep the
RTC-FO format ([Role][Task][Context][Few-shot Guideline][Output Instructions]).

### 10.6 Format Detection prompt (NEW)

```
[Role] You are a schema-mapping assistant.

[Task] Given a few sample rows from a user-uploaded seed dataset and the
canonical schema for {task_type}, produce a JSON object that maps each
non-canonical key in the samples to its canonical equivalent.

[Canonical schema for {task_type}]
{canonical_keys_with_descriptions}

[Sample rows from upload (first 3)]
{sample_rows_json}

[Output Instructions]
1. ONLY rename keys; do NOT alter values.
2. If a key already matches canonical, omit it from the mapping.
3. If a key cannot be confidently mapped, omit it (rows with unmapped
   required keys will be dropped).
4. Output ONLY a JSON object: {"field_mapping": {"old_key": "new_key", ...}}
   No prose, no markdown.
```

### 10.7 QA + PDF prompt (NEW)

```
[Role] You are extracting question-answer training pairs from a document.

[Task] Read the attached PDF and produce {num_samples} diverse, factually-
grounded Q&A pairs that test understanding of its content.

[Context — task description from user]
{task_description}

[Output Instructions]
1. Each Q&A pair must be answerable from the document alone.
2. Vary question style: factual recall, comparison, "why", procedural.
3. Answers should be concise (1–4 sentences) and grounded in document text.
4. Output ONLY a JSON object:
   {"results": [{"question": "...", "answer": "..."}, ...]}
```

---

## 11. Stop Conditions & Adaptive Over-Generation

### 11.1 Defaults (port verbatim)

```python
MAX_LOOPS = 20
MAX_CONSECUTIVE_FAILURES = 5
JUDGE_THRESHOLD = 0.7
MINHASH_THRESHOLD = 0.90
MINHASH_NUM_PERM = 128
MINHASH_NGRAM_SIZE = 5
INITIAL_OVER_GEN_MULT = 1.5
MIN_OVER_GEN_MULT = 1.5
MAX_OVER_GEN_MULT = 8.0
SENTINEL_RATIO = 0.10
GENERATOR_BATCH_SIZE = 100      # concurrency on AsyncOpenRouterClient
JUDGE_BATCH_SIZE = 100
CANDIDATES_PER_GEN_CALL = 5     # already locked into prompt
```

All in one constants module (`ai_engine/data_gen/constants.py` — new file,
or co-locate with `models.py` if it stays small).

### 11.2 Adaptive multiplier formula (port verbatim)

```python
yield_per_req = added_in_loop / max(1, num_generator_calls_this_loop)
if yield_per_req > 0.05:
    new_mult = max(MIN_OVER_GEN_MULT, min(MAX_OVER_GEN_MULT, 1.0 / yield_per_req))
    over_gen_mult = 0.5 * over_gen_mult + 0.5 * new_mult   # EMA smoothing
else:
    over_gen_mult = min(MAX_OVER_GEN_MULT, over_gen_mult * 1.5)
```

### 11.3 Failure semantics

- "Failed loop" = `added_in_loop == 0`.
- Track a `consecutive_failures` counter; reset to 0 on any non-zero loop.
- If `consecutive_failures >= MAX_CONSECUTIVE_FAILURES` → raise
  `SDGAbortedError("aborted after N consecutive zero-yield loops")`.

This is the explicit defence against "loop never terminates". Do NOT remove.

---

## 12. API Endpoint Changes

### 12.1 `POST /api/v1/datasets/upload-seed` — extended

**Existing (Phase 4):** accepts `.json` / `.jsonl` via multipart, validates
each row against canonical Pydantic.

**New (Phase 9):**
- Add `.pdf` to allowed extensions **for `task_type=qa` only**.
- For `.json`/`.jsonl`: run Format Detection if any row's keys ≠ canonical.
- Persist canonical JSONL to MinIO at `seeds/{dataset_id}.jsonl` (unchanged).
- For `.pdf`: persist raw PDF to `seed-pdfs/{dataset_id}.pdf`. Set
  `Dataset.generation_metadata['pdf_uri']`. Do NOT extract text here —
  the worker reads the PDF in §9.4.
- Always write `Dataset.generation_metadata['format_detection']` =
  `FormatDetectionReport.model_dump()` (with `ran=False` if no detection
  was needed, or for PDFs).

**Response (extended):**

```python
class SeedUploadResponse(BaseModel):
    dataset_id: UUID
    task_type: TaskType
    num_samples: int                  # 0 for PDF (samples come from generator later)
    invalid_rows: list[int]
    format_detection: FormatDetectionReport   # NEW
    pdf_uri: str | None = None        # NEW (only set for PDF)
```

**Hard limits:**
- JSON/JSONL: 10 MiB (existing)
- PDF: 25 MiB (new, set in `_MAX_SEED_PDF_BYTES`)
- PDF max pages: 100 (use `pypdf.PdfReader(..).pages` to check; 413 if over)

### 12.2 `POST /api/v1/datasets/generate` — request schema changes

**Removed:** `SDGRequestWithSeed.seed_data: list[dict]`
**Added:** `SDGRequestWithSeed.seed_dataset_id: UUID`

Service-level validation in `sdg_service.submit_sdg_job`:
- Load `Dataset` by `seed_dataset_id`
- Must exist, `source == DatasetSource.SEED`
- `task_type` must match request's `task_type`
- `project_id` must match request's `project_id`
- For PDF flow: assert `task_type == QA` AND `generation_metadata['pdf_uri']` set

### 12.3 OpenAPI examples — update `json_schema_extra` on the changed schemas

---

## 13. Migration & Backwards Compatibility

- **No existing production data.** Per parks (Session 9), the platform
  has only been smoke-tested locally; no real datasets exist. Phase 9 can
  ship breaking schema changes without migration.
- **No Alembic migration** (no DB column changes).
- **`SDGRequestWithSeed.seed_data` removal IS a breaking API change** —
  document it in the next `WORKING_LOG.md` session entry. Update
  `examples/python_client.py` and `examples/quickstart_curl.sh`
  to use the upload-seed → seed_dataset_id flow.

---

## 14. Test Plan

### 14.1 Unit tests (no external services)

| Module | Cases |
|--------|-------|
| `format_detector.py` | (1) canonical keys → ran=False; (2) renamable keys → mapping returned; (3) unmapped required key → row dropped; (4) malformed LLM response → fall-back report |
| `meta_prompter.py` | (1) happy parse; (2) malformed JSON → fall-back rules used (port from old line 405–409) |
| `judge.py` | (1) parse + score weighted = 0.4F + 0.3N + 0.3U; (2) reject score > 1.0 or < 0; (3) reasoning truncated |
| `minhash_dedup.py` | (1) exact match → dup; (2) 95%-similar → dup at threshold 0.90; (3) different texts → not dup; (4) Thai + English mixed |
| `coverage_pool.py` | (1) cycles through items ≥ floor(n/k) times; (2) shuffled |
| `pdf_loader.py` | (1) valid PDF → bytes + page count; (2) corrupt → ValueError; (3) >25 MiB → reject |
| `async_openrouter_client.py` | mock-server tests: (1) 100 prompts complete; (2) one fails, others succeed; (3) all 429 → all retry-then-succeed |
| `generator.py` (orchestrator) | scripted FakeAsyncClient: (1) classification 90/10 quota math; (2) tool_calling sentinel injection; (3) MinHash rejects seed copies; (4) abort after 5 zero-yield loops; (5) adaptive multiplier moves up/down |

### 14.2 Integration test additions

Extend `tests/integration/test_full_flow.py`:
- `test_classification_with_format_detection` — upload seed with mismatched
  keys (e.g. `"text1"`/`"answer"`); assert canonical JSONL on MinIO has
  `"text"`/`"label"`; assert `generation_metadata.format_detection.ran is True`.
- `test_qa_pdf_upload` — upload a small synthetic PDF; assert `pdf_uri`
  populated; submit SDG; assert generation completes (skip if no
  OPENROUTER_API_KEY).
- `test_tool_calling_sentinel_quota` — assert ~10% of generated rows have
  `tool_call.name == "no_tool_needed"`.

### 14.3 Mark organisation

```python
@pytest.mark.unit          # default, fast
@pytest.mark.integration   # needs compose stack
@pytest.mark.external      # needs OPENROUTER_API_KEY (existing convention)
```

Add `@pytest.mark.external` to any test that calls real OpenRouter.

---

## 15. Implementation Roadmap (Phase 9.1 → 9.3)

> **Critical:** Follow the existing CLAUDE.md session protocol. After each
> sub-phase, **stop and wait for parks's approval** before continuing. Update
> `WORKING_LOG.md` and `TASK_TRACKER.md` at every handover.

### Phase 9.1 — Foundations & Async Plumbing
**Goal:** New deps, async client, hardcoded models, no behaviour change yet.

1. Add to `pyproject.toml` base deps: `datasketch>=1.6.0`, `pypdf>=5.0.0`.
2. Write **ADR-007: Async LLM batching with custom asyncio.gather**.
3. Create `ai_engine/data_gen/models.py` — model constants.
4. Create `ai_engine/data_gen/constants.py` — thresholds, batch sizes, etc.
5. Add `AsyncOpenRouterClient` to `ai_engine/data_gen/openrouter_client.py`.
6. Unit tests for the async client (mock-server style, no real OpenRouter).

**Definition of Done:** new code imports cleanly, async client passes its
mock tests, no other modules touched yet.

### Phase 9.2 — Quality Modules (port from old)
**Goal:** Five new pure-domain modules, fully tested, not yet wired.

1. `format_detector.py` + tests
2. `meta_prompter.py` + tests
3. `judge.py` + tests
4. `minhash_dedup.py` + tests
5. `coverage_pool.py` + tests
6. `pdf_loader.py` + tests
7. Update `prompts.py` with the new RTC-FO templates from §10.5
8. Add `FormatDetectionReport` to `api/schemas/upload.py`

**Definition of Done:** all 7 modules import + unit tests green; **none
referenced by `generator.py` yet**.

### Phase 9.3 — Wire Everything Together
**Goal:** New SDG loop end-to-end, breaking-API change shipped.

1. Rewrite `ai_engine/data_gen/generator.py` to be `async`, orchestrate all
   stages, honour quota + sentinel + adaptive multiplier.
2. Modify `workers/tasks/data_generation.py` to `asyncio.run(...)` the new
   generator and pass canonical seed URI + (optional) PDF URI.
3. Modify `api/services/datasets_service.py`:
   - Accept `.pdf` for QA
   - Run Format Detection inline at upload time
   - Persist canonical JSONL + PDF + report
4. Modify `api/services/sdg_service.py`:
   - Validate `seed_dataset_id` (ownership + task_type + PDF for QA)
   - Hand off canonical URI + PDF URI to worker
5. Modify `api/schemas/sdg.py`:
   - Replace `seed_data` with `seed_dataset_id`
6. Update `examples/python_client.py` + `examples/quickstart_curl.sh` to
   use upload-seed → seed_dataset_id flow.
7. Add integration tests from §14.2.
8. Update README's API usage section.

**Definition of Done:** integration test `test_qa_full_flow` (existing) still
passes after the schema change; new integration tests pass; manual smoke
test against the local stack (parks runs this) generates a 30-row dataset
end-to-end with Judge rejections + dedup + sentinel quota visible in logs.

---

## 16. CLAUDE.md Compliance Checklist

For each Phase 9.x session:

- [ ] **Read first:** `CLAUDE.md`, `WORKING_LOG.md`, `TASK_TRACKER.md`,
      this document, all 6 ADRs.
- [ ] **Discovery → Execution → Handover** protocol followed.
- [ ] **No forbidden files touched:** `.env`, `secrets/`, model weights.
- [ ] **Hexagonal discipline:** `ai_engine/` has zero imports of
      `api.*`, `workers.*`, `fastapi`, `celery`, `redis`, `minio`,
      `sqlalchemy`. (Pydantic + openai SDK + datasketch + pypdf are OK.)
- [ ] **No new heavy deps beyond §3.2.** If something needs adding, write
      a session note + propose to parks before installing.
- [ ] **Tests added in same session as code** — not deferred.
- [ ] **`WORKING_LOG.md` updated** with: Who / Status / Why & What /
      Test Summary / Decisions Made / Files Touched / Next Action /
      Blockers.
- [ ] **`TASK_TRACKER.md` Phase 9 section** added/updated with status per
      sub-task.
- [ ] **ADR-007 written** in Phase 9.1.
- [ ] **No production-PII in test fixtures or logs.**

---

## 17. Open Questions / Known Risks

### 17.1 Risks Claude Code should flag back

- **OpenRouter rate limits** — concurrency=100 may hit per-org limits on
  cheap models. The async client's tenacity retry covers transient 429s,
  but persistent rate-limiting would need lowering `GENERATOR_BATCH_SIZE`
  to 50 or 25 and re-running. If this happens during dev smoke tests,
  flag it and propose a per-model concurrency cap.
- **Multimodal PDF call cost** — `gemini-2.5-flash-lite` is cheap, but a
  100-page PDF + a long prompt is non-trivial. The 25 MiB upload cap +
  100-page cap (§12.1) is a defensive bound. If parks's real PDFs are
  larger, raise it after first measurement.
- **MinHash for very short texts** — 5-gram MinHash on 10-character
  classification labels degenerates. Old code handled this:
  `if len(clean_text) < 5: m.update(clean_text.encode())`. Port that
  exact behaviour.
- **Sentinel `unknown` for classification with closed-label mode** —
  `validators.py` currently rejects any label not in the closed set.
  When the new pipeline injects `"unknown"`, `validate_generated_rows`
  must accept it. The cleanest fix: pass `unknown` into the
  `classification_labels` argument when calling the validator
  (effectively widening the accepted set).

### 17.2 Things parks may want to revisit later

- Whether to surface Judge rejection breakdown in `SDGProgress` (per-label
  rejection counts, judge_low vs judge_parse_err vs minhash_dup vs
  quota_full — old code logged these). Useful for debugging stuck jobs.
  Add as optional fields on `SDGProgress` and emit when populated.
- A `dry_run` flag on `/datasets/generate` that runs Meta-Prompting only
  and returns the rules + estimated cost, no generation. Easy add later.
- Per-model concurrency overrides if Judge model rate-limits differently
  from Generator.

### 17.3 Things deliberately out of scope for Phase 9

- Multi-turn tool calling
- Translation between languages (Thai ↔ English) within SDG
- Active learning / human-in-the-loop rejection feedback
- Caching of Generator outputs across jobs
