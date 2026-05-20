"""Phase 9 SDG orchestrator — async, multi-stage, quality-gated.

Pure domain code. Celery / DB / MinIO / Redis live elsewhere; the
worker passes us already-resolved seed rows + (optional) PDF bytes.

Pipeline (per task type):

  Setup (once)
    1. Resolve seed → label_examples / tool_examples (cls + tool only)
    2. (cls + tool only) Compute 90/10 quota with sentinel class
    3. Meta-prompter LLM call → diversity_rules [+ unknown_diversity_rules]
    4. Pre-load seed MinHash signatures into the global LSH
    5. (QA + PDF only) PDF → Q&A pairs via multimodal Generator (one-shot,
       seeds first iteration's example pool)

  Loop (up to MAX_LOOPS)
    1. Build batch — coverage_pool over rules + difficulties; nonce per call
    2. Generator chat_batch (concurrency=GENERATOR_BATCH_SIZE)
    3. parse_generator_response per call → exploded candidates
    4. Schema + business validation (label widening for sentinels)
    5. Judge chat_batch (concurrency=JUDGE_BATCH_SIZE) → keep score >= threshold
    6. MinHash LSH dedup
    7. Quota-respecting collection
    8. Adaptive over-gen multiplier update (EMA smoothed)
    9. Emit SDGProgress to caller

  Termination
    • collected >= target_count → success
    • loop_count > MAX_LOOPS → partial success (return what we have)
    • MAX_CONSECUTIVE_FAILURES zero-yield loops → SDGAbortedError
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from json import JSONDecodeError
from typing import Any, Awaitable, Callable, Literal
from uuid import uuid4

from api.schemas.data_formats import ToolDefinition
from api.schemas.enums import SDGMode, TaskType
from api.schemas.sdg import SDGRequestDescriptionOnly, SDGRequestWithSeed

from . import models
from .constants import (
    CANDIDATES_PER_GEN_CALL,
    CLASSIFICATION_SENTINEL_LABEL,
    DIFFICULTY_LEVELS,
    GENERATOR_BATCH_SIZE,
    INITIAL_OVER_GEN_MULT,
    JUDGE_BATCH_SIZE,
    JUDGE_THRESHOLD,
    MAX_CONSECUTIVE_FAILURES,
    MAX_LOOPS,
    MAX_OVER_GEN_MULT,
    MIN_OVER_GEN_MULT,
    SENTINEL_RATIO,
    TOOL_CALLING_SENTINEL_DESCRIPTION,
    TOOL_CALLING_SENTINEL_NAME,
)
from .coverage_pool import make_coverage_pool
from .judge import parse_judge_response
from .meta_prompter import SDGRules, fallback_rules, parse_meta_response
from .minhash_dedup import MinHashDeduplicator
from .openrouter_client import AsyncOpenRouterClient, ChatResult, OpenRouterClient, Prompt
from .prompts import (
    build_generator_prompt,
    build_judge_prompt,
    build_meta_prompt,
    build_pdf_qa_messages,
    parse_generator_response,
)
from .validators import validate_generated_rows

log = logging.getLogger(__name__)

GenerationPhase = Literal[
    "format_detection",
    "meta_prompting",
    "generating",
    "validating",
    "judging",
    "dedup",
    "persisting",
]


@dataclass(frozen=True)
class GenerationProgress:
    """Progress event emitted to the caller after every loop iteration.

    The Celery worker translates this into `SDGProgress` (Pydantic) +
    publishes to Redis on `job:{job_id}`.
    """

    phase: GenerationPhase
    samples_generated: int
    samples_target: int
    samples_valid: int
    samples_rejected: int
    duplicates_removed: int
    current_loop: int | None = None
    judge_rejected: int | None = None
    judge_parse_failures: int | None = None
    dedup_rejected: int | None = None


ProgressCallback = Callable[[GenerationProgress], None]
AsyncProgressCallback = Callable[[GenerationProgress], Awaitable[None] | None]


@dataclass
class SDGRunResult:
    """Final result returned to the worker."""

    valid_rows: list[dict]
    rejected_count: int
    duplicate_count: int
    judge_rejected_count: int
    judge_parse_failures: int
    api_calls: int
    failed_attempts: list[str] = field(default_factory=list)


class SDGAbortedError(RuntimeError):
    """Raised when too many consecutive zero-yield loops occur."""


# ---------------------------------------------------------------------------
# The orchestrator
# ---------------------------------------------------------------------------


class SyntheticDataGenerator:
    """Async SDG orchestrator. One instance per Celery task is fine."""

    def __init__(
        self,
        async_client: AsyncOpenRouterClient,
        sync_client: OpenRouterClient,
        *,
        rng: random.Random | None = None,
    ) -> None:
        self._async = async_client
        self._sync = sync_client
        self._rng = rng if rng is not None else random.Random()

    # -- Public entry point -------------------------------------------------

    async def generate(
        self,
        request: SDGRequestWithSeed | SDGRequestDescriptionOnly,
        *,
        seed_rows: list[dict[str, Any]] | None = None,
        pdf_bytes: bytes | None = None,
        progress_cb: ProgressCallback | None = None,
    ) -> SDGRunResult:
        """Run one full SDG job.

        Args:
            request: validated SDG request body.
            seed_rows: canonical seed rows from the worker (with_seed) or
                None / [] (description_only / PDF-only QA).
            pdf_bytes: raw PDF bytes (QA + PDF only).
            progress_cb: optional sync callback for SDGProgress emission.
        """
        target = request.num_samples
        seed_rows = seed_rows or []

        # ---- Setup --------------------------------------------------------
        emit = _emit_factory(
            progress_cb,
            target=target,
            samples_generated=0,
            samples_valid=0,
            samples_rejected=0,
            duplicates_removed=0,
        )
        emit("meta_prompting")

        cls_labels: list[str] | None = None
        tool_defs: list[ToolDefinition] | None = None
        sentinel_active = False
        if isinstance(request, SDGRequestDescriptionOnly):
            if request.classification_config is not None:
                cls_labels = list(request.classification_config.labels)
            if request.tool_calling_config is not None:
                tool_defs = list(request.tool_calling_config.tool_definitions)

        # In with_seed mode, the catalog comes from the seed rows. For
        # classification we collect the unique `label` values; for
        # tool_calling we collect the unique tool names from each row's
        # JSON-encoded `answer` and synthesise a minimal ToolDefinition
        # for each one (the per-tool seed rows are then used as
        # in-context examples, so parameter schemas are conveyed via the
        # examples themselves).
        if request.task_type is TaskType.CLASSIFICATION and cls_labels is None:
            if seed_rows:
                cls_labels = sorted(
                    {str(r.get("label", "")).strip() for r in seed_rows if r.get("label")}
                )
                if not cls_labels:
                    raise ValueError(
                        "Could not derive classification labels from seed rows"
                    )
        if request.task_type is TaskType.TOOL_CALLING and tool_defs is None:
            if seed_rows:
                import json as _json

                names: list[str] = []
                seen: set[str] = set()
                for r in seed_rows:
                    try:
                        inner = _json.loads(r.get("answer", "{}"))
                        name = str(inner.get("name", "")).strip()
                    except (ValueError, TypeError):
                        continue
                    if name and name not in seen:
                        seen.add(name)
                        names.append(name)
                if not names:
                    raise ValueError(
                        "Could not derive tool names from seed rows"
                    )
                tool_defs = [
                    ToolDefinition(
                        name=n,
                        description="Tool derived from seed dataset; parameter schema inferred from in-context examples.",
                        parameters={},
                    )
                    for n in names
                ]

        # Sentinel injection: append/inject before computing quota so they
        # participate in the rotation.
        sentinel_active = request.task_type in (
            TaskType.CLASSIFICATION,
            TaskType.TOOL_CALLING,
        )
        if request.task_type is TaskType.CLASSIFICATION and cls_labels is not None:
            if CLASSIFICATION_SENTINEL_LABEL not in cls_labels:
                cls_labels = list(cls_labels) + [CLASSIFICATION_SENTINEL_LABEL]
        if request.task_type is TaskType.TOOL_CALLING and tool_defs is not None:
            if not any(t.name == TOOL_CALLING_SENTINEL_NAME for t in tool_defs):
                tool_defs = list(tool_defs) + [_make_sentinel_tool_def()]

        # ---- Meta-prompting (1 LLM call) ---------------------------------
        rules = await self._meta_prompt(
            request=request,
            classification_labels=cls_labels,
            tool_definitions=tool_defs,
            include_unknown=sentinel_active,
        )

        # ---- Quota (cls + tool only) -------------------------------------
        quota: dict[str, int] = {}
        if request.task_type is TaskType.CLASSIFICATION and cls_labels is not None:
            quota = _compute_classification_quota(cls_labels, target)
        elif request.task_type is TaskType.TOOL_CALLING and tool_defs is not None:
            tool_names = [t.name for t in tool_defs]
            quota = _compute_tool_quota(tool_names, target)

        # ---- Build per-key example pools ---------------------------------
        label_examples: dict[str, list[dict[str, Any]]] = {}
        tool_examples: dict[str, list[dict[str, Any]]] = {}
        if request.task_type is TaskType.CLASSIFICATION:
            label_examples = _group_by(seed_rows, key="label")
        elif request.task_type is TaskType.TOOL_CALLING:
            tool_examples = _group_tool_examples(seed_rows)

        # ---- Pre-load LSH with seeds (and PDF Q&A pairs if any) ----------
        dedup = MinHashDeduplicator()
        seed_text_field = _seed_text_field(request.task_type)
        for row in seed_rows:
            text = str(row.get(seed_text_field, ""))
            if text:
                dedup.add(text)

        # ---- PDF → Q&A first pass (QA + PDF only) ------------------------
        accepted: list[dict[str, Any]] = []
        api_calls = 0
        if request.task_type is TaskType.QA and pdf_bytes:
            try:
                pdf_rows, pdf_calls = await self._pdf_first_pass(
                    request=request,
                    pdf_bytes=pdf_bytes,
                    target=target,
                )
                api_calls += pdf_calls
                # Dedup against any seeds we may also have.
                pdf_outcome = dedup.filter(pdf_rows, key="question")
                # Trim to target so we don't overshoot before the loop.
                kept = pdf_outcome.unique[: target]
                accepted.extend(kept)
                # Use the PDF-derived Q&As as in-context examples for the loop.
                for r in kept:
                    label_examples.setdefault("__qa__", []).append(r)
            except Exception as exc:  # noqa: BLE001 — PDF call is best-effort
                log.warning(
                    "PDF first-pass failed; continuing with text-only Generator: %s",
                    exc,
                )

        # ---- Loop --------------------------------------------------------
        rejected_total = 0
        duplicates_total = 0
        judge_rejected_total = 0
        judge_parse_total = 0
        failed_attempts: list[str] = []
        consecutive_failures = 0
        over_gen_mult = INITIAL_OVER_GEN_MULT
        collected_per_key: dict[str, int] = {k: 0 for k in quota}
        # Account for any PDF-derived rows under the QA bucket.
        if request.task_type is TaskType.QA and accepted:
            collected_per_key.setdefault("__qa__", 0)
            collected_per_key["__qa__"] = len(accepted)

        for loop_idx in range(MAX_LOOPS):
            if len(accepted) >= target:
                break

            batch_inputs = self._build_batch_inputs(
                request=request,
                quota=quota,
                collected_per_key=collected_per_key,
                target=target,
                label_examples=label_examples,
                tool_examples=tool_examples,
                rules=rules,
                over_gen_mult=over_gen_mult,
                cls_labels=cls_labels,
                tool_defs=tool_defs,
            )
            if not batch_inputs:
                # Nothing left to ask for — loop is done.
                break

            # 1. Generator batch
            gen_prompts = [
                build_generator_prompt(
                    task_type=request.task_type,
                    task_description=request.task_description,
                    label_or_tool=b["label_or_tool"],
                    examples=b["examples"],
                    diversity_rule=b["diversity_rule"],
                    difficulty=b["difficulty"],
                    classification_labels=cls_labels,
                    tool_definitions=tool_defs,
                    is_sentinel=b["is_sentinel"],
                )
                for b in batch_inputs
            ]
            try:
                gen_results = await self._async.chat_batch(
                    prompts=gen_prompts,
                    model=models.GENERATOR,
                    temperature=request.temperature,
                    response_format={"type": "json_object"},
                    concurrency=GENERATOR_BATCH_SIZE,
                )
            except Exception as exc:  # noqa: BLE001 — surface as loop failure
                consecutive_failures += 1
                failed_attempts.append(f"generator batch error: {exc}")
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    raise SDGAbortedError(
                        f"SDG aborted after {consecutive_failures} consecutive "
                        f"generator failures; last error: {exc}"
                    ) from exc
                emit("generating", current_loop=loop_idx)
                continue
            api_calls += len(gen_prompts)

            candidates: list[dict[str, Any]] = []
            for raw, b in zip(gen_results, batch_inputs):
                if isinstance(raw, Exception):
                    failed_attempts.append(f"generator call error: {raw}")
                    continue
                try:
                    rows = parse_generator_response(raw.content)
                except (ValueError, JSONDecodeError) as exc:
                    failed_attempts.append(f"generator parse error: {exc}")
                    continue
                for row in rows:
                    # Classification: ALWAYS stamp the requested label so quota
                    # routing matches what we asked for. Otherwise the Generator
                    # may emit a different label (e.g. when asked for the
                    # sentinel "unknown" it often picks a real label), which
                    # buckets the row under the wrong key and gets rejected by
                    # an already-full quota — a "validate-passes-but-collect-
                    # rejects-all" pathology that abort the whole job.
                    if request.task_type is TaskType.CLASSIFICATION:
                        row["label"] = b["label_or_tool"]
                    # Tool-calling sentinel: override the inner tool name to the
                    # sentinel value for the same reason. Non-sentinel
                    # tool-calling we trust the model since it may legitimately
                    # pick a different (still-valid) tool from the catalog.
                    elif (
                        request.task_type is TaskType.TOOL_CALLING
                        and b["is_sentinel"]
                    ):
                        try:
                            import json as _json

                            inner = _json.loads(row.get("answer", "{}"))
                            inner["name"] = b["label_or_tool"]
                            inner["parameters"] = {}
                            row["answer"] = _json.dumps(inner, ensure_ascii=False)
                        except (ValueError, TypeError):
                            # Let the validator drop malformed answers.
                            pass
                    candidates.append(row)

            if not candidates:
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    raise SDGAbortedError(
                        f"SDG aborted after {consecutive_failures} consecutive "
                        f"empty-candidate loops"
                    )
                emit(
                    "generating",
                    current_loop=loop_idx,
                    samples_rejected=rejected_total,
                    duplicates_removed=duplicates_total,
                )
                continue

            # 2. Schema + business validation
            valid_rows, failures = validate_generated_rows(
                request.task_type,
                candidates,
                # Widen the closed label set so sentinel labels validate.
                classification_labels=cls_labels,
                tool_definitions=tool_defs,
            )
            rejected_total += len(failures)

            # 3. Judge batch
            emit(
                "judging",
                current_loop=loop_idx,
                samples_rejected=rejected_total,
                duplicates_removed=duplicates_total,
            )
            judge_keep, j_low, j_parse, judge_calls = await self._judge_filter(
                request=request,
                rows=valid_rows,
                classification_labels=cls_labels,
                tool_definitions=tool_defs,
            )
            api_calls += judge_calls
            judge_rejected_total += j_low
            judge_parse_total += j_parse

            # 4. MinHash LSH dedup
            text_field = _seed_text_field(request.task_type)
            outcome = dedup.filter(judge_keep, key=text_field)
            duplicates_total += outcome.duplicates_dropped

            # 5. Quota-respecting collection
            added = _apply_collection_quota(
                outcome.unique,
                accepted=accepted,
                collected_per_key=collected_per_key,
                quota=quota,
                target=target,
                key_for_row=lambda r: self._row_quota_key(request.task_type, r),
            )

            # 6. Adaptive multiplier (EMA-smoothed)
            over_gen_mult = _update_over_gen_mult(
                over_gen_mult, added=added, batch_size=len(batch_inputs)
            )

            # 7. Failure counter
            if added == 0:
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    raise SDGAbortedError(
                        f"SDG aborted after {consecutive_failures} consecutive "
                        f"zero-yield loops"
                    )
            else:
                consecutive_failures = 0

            emit(
                "generating",
                current_loop=loop_idx,
                samples_generated=len(accepted),
                samples_valid=len(accepted),
                samples_rejected=rejected_total,
                duplicates_removed=duplicates_total,
                judge_rejected=judge_rejected_total,
                judge_parse_failures=judge_parse_total,
                dedup_rejected=duplicates_total,
            )

        return SDGRunResult(
            valid_rows=accepted[:target],
            rejected_count=rejected_total,
            duplicate_count=duplicates_total,
            judge_rejected_count=judge_rejected_total,
            judge_parse_failures=judge_parse_total,
            api_calls=api_calls,
            failed_attempts=failed_attempts,
        )

    # -- Internal stages ----------------------------------------------------

    async def _meta_prompt(
        self,
        *,
        request: SDGRequestWithSeed | SDGRequestDescriptionOnly,
        classification_labels: list[str] | None,
        tool_definitions: list[ToolDefinition] | None,
        include_unknown: bool,
    ) -> SDGRules:
        """One meta-prompter LLM call. Falls back to generic rules on any error."""
        prompt = build_meta_prompt(
            request.task_type,
            task_description=request.task_description,
            classification_labels=classification_labels,
            tool_definitions=tool_definitions,
            include_unknown=include_unknown,
        )
        try:
            chat = await self._async.chat(
                system=prompt.system,
                user=prompt.user,
                model=models.DIVERSITY_RULES,
                temperature=0.5,
                response_format={"type": "json_object"},
            )
            return parse_meta_response(chat.content, include_unknown=include_unknown)
        except Exception as exc:  # noqa: BLE001 — fall back, log
            log.warning("meta-prompter LLM call failed (%s); using fallback rules", exc)
            return fallback_rules(include_unknown=include_unknown)

    async def _judge_filter(
        self,
        *,
        request: SDGRequestWithSeed | SDGRequestDescriptionOnly,
        rows: list[dict[str, Any]],
        classification_labels: list[str] | None,
        tool_definitions: list[ToolDefinition] | None,
    ) -> tuple[list[dict[str, Any]], int, int, int]:
        """Score every row; keep those with weighted >= JUDGE_THRESHOLD.

        Returns (kept_rows, low_score_count, parse_failure_count, api_calls).
        """
        if not rows:
            return [], 0, 0, 0
        prompts = [
            build_judge_prompt(
                request.task_type,
                task_description=request.task_description,
                row=row,
                classification_labels=classification_labels,
                tool_definitions=tool_definitions,
            )
            for row in rows
        ]
        try:
            results = await self._async.chat_batch(
                prompts=prompts,
                model=models.JUDGE,
                temperature=0.0,
                response_format={"type": "json_object"},
                concurrency=JUDGE_BATCH_SIZE,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("judge batch failed wholesale (%s); skipping judge gate", exc)
            return rows, 0, 0, 0
        kept: list[dict[str, Any]] = []
        low = 0
        parse_fail = 0
        for row, raw in zip(rows, results):
            if isinstance(raw, Exception):
                parse_fail += 1
                continue
            score = parse_judge_response(raw.content)
            if score is None:
                parse_fail += 1
                continue
            if score.weighted < JUDGE_THRESHOLD:
                low += 1
                continue
            kept.append(row)
        return kept, low, parse_fail, len(prompts)

    async def _pdf_first_pass(
        self,
        *,
        request: SDGRequestWithSeed | SDGRequestDescriptionOnly,
        pdf_bytes: bytes,
        target: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """One multimodal call to extract Q&A pairs from a PDF.

        Returns (qa_rows, api_calls). The result is dedup'd + judge'd by
        the regular loop afterwards (we don't run a separate Judge pass on
        the PDF's first batch — keeps the PDF flow at one call).
        """
        from .pdf_loader import to_base64_data_url  # local: avoid eager pypdf import

        # Cap to a sensible upper bound — the multimodal call is the most
        # expensive single LLM call in the pipeline.
        ask = min(target, 50)
        data_url = to_base64_data_url(pdf_bytes)
        messages = build_pdf_qa_messages(
            task_description=request.task_description,
            num_samples=ask,
            pdf_data_url=data_url,
        )
        chat: ChatResult = await self._sync_pdf_call(messages=messages)
        rows = parse_generator_response(chat.content)
        # Validate against the QA schema; drop anything that doesn't fit.
        accepted, _failures = validate_generated_rows(TaskType.QA, rows)
        return accepted, 1

    async def _sync_pdf_call(self, *, messages: list[dict[str, Any]]) -> ChatResult:
        """Run the multimodal call in a thread (the sync client has the
        chat_raw entry point we need). Keeps the event loop free."""
        import asyncio

        def _go() -> ChatResult:
            return self._sync.chat_raw(
                messages=messages,
                model=models.PDF_QA,
                temperature=0.4,
                response_format={"type": "json_object"},
                max_tokens=8192,
            )

        return await asyncio.to_thread(_go)

    # -- Batch input assembly ----------------------------------------------

    def _build_batch_inputs(
        self,
        *,
        request: SDGRequestWithSeed | SDGRequestDescriptionOnly,
        quota: dict[str, int],
        collected_per_key: dict[str, int],
        target: int,
        label_examples: dict[str, list[dict[str, Any]]],
        tool_examples: dict[str, list[dict[str, Any]]],
        rules: SDGRules,
        over_gen_mult: float,
        cls_labels: list[str] | None,
        tool_defs: list[ToolDefinition] | None,
    ) -> list[dict[str, Any]]:
        """Build the per-call input dicts for one loop iteration."""
        if request.task_type is TaskType.QA:
            return self._build_qa_inputs(
                target=target,
                accepted_so_far=sum(collected_per_key.values()),
                examples=label_examples.get("__qa__", []),
                rules=rules,
                over_gen_mult=over_gen_mult,
            )
        # cls + tool: drive by quota, with sentinel branch
        return self._build_keyed_inputs(
            task_type=request.task_type,
            quota=quota,
            collected_per_key=collected_per_key,
            label_examples=label_examples,
            tool_examples=tool_examples,
            rules=rules,
            over_gen_mult=over_gen_mult,
            cls_labels=cls_labels,
            tool_defs=tool_defs,
        )

    def _build_qa_inputs(
        self,
        *,
        target: int,
        accepted_so_far: int,
        examples: list[dict[str, Any]],
        rules: SDGRules,
        over_gen_mult: float,
    ) -> list[dict[str, Any]]:
        remaining = target - accepted_so_far
        if remaining <= 0:
            return []
        # Generator emits CANDIDATES_PER_GEN_CALL per call.
        n_calls = max(1, math.ceil(remaining / CANDIDATES_PER_GEN_CALL * over_gen_mult))
        # Cap to keep one loop iteration bounded — full-tree of candidates
        # equals n_calls × CANDIDATES_PER_GEN_CALL. Generator concurrency
        # also caps at GENERATOR_BATCH_SIZE.
        n_calls = min(n_calls, GENERATOR_BATCH_SIZE)
        rules_pool = make_coverage_pool(rules.diversity_rules, n_calls, rng=self._rng)
        diff_pool = make_coverage_pool(list(DIFFICULTY_LEVELS), n_calls, rng=self._rng)
        out: list[dict[str, Any]] = []
        for i in range(n_calls):
            sample_count = min(3, len(examples))
            picked = (
                self._rng.sample(examples, sample_count) if sample_count else []
            )
            nonce = uuid4().hex[:8]
            out.append(
                {
                    "label_or_tool": None,
                    "examples": picked,
                    "diversity_rule": f"{rules_pool[i]} [variation_id={nonce}]",
                    "difficulty": diff_pool[i],
                    "is_sentinel": False,
                }
            )
        return out

    def _build_keyed_inputs(
        self,
        *,
        task_type: TaskType,
        quota: dict[str, int],
        collected_per_key: dict[str, int],
        label_examples: dict[str, list[dict[str, Any]]],
        tool_examples: dict[str, list[dict[str, Any]]],
        rules: SDGRules,
        over_gen_mult: float,
        cls_labels: list[str] | None,
        tool_defs: list[ToolDefinition] | None,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for key, target_for_key in quota.items():
            need = max(0, target_for_key - collected_per_key.get(key, 0))
            if need == 0:
                continue
            n_calls = max(
                1, math.ceil((need / CANDIDATES_PER_GEN_CALL) * over_gen_mult)
            )
            n_calls = min(n_calls, max(1, need))
            is_sentinel = (
                key == CLASSIFICATION_SENTINEL_LABEL
                if task_type is TaskType.CLASSIFICATION
                else key == TOOL_CALLING_SENTINEL_NAME
            )
            pool_source = (
                rules.unknown_diversity_rules
                if is_sentinel and rules.unknown_diversity_rules
                else rules.diversity_rules
            )
            rules_pool = make_coverage_pool(pool_source, n_calls, rng=self._rng)
            diff_pool = make_coverage_pool(
                list(DIFFICULTY_LEVELS), n_calls, rng=self._rng
            )

            for i in range(n_calls):
                examples_for_call: list[dict[str, Any]] = []
                if not is_sentinel:
                    if task_type is TaskType.CLASSIFICATION:
                        pool = label_examples.get(key, [])
                        examples_for_call = self._rng.sample(
                            pool, min(3, len(pool))
                        )
                    else:  # tool_calling
                        pool = tool_examples.get(key, [])
                        examples_for_call = self._rng.sample(
                            pool, min(3, len(pool))
                        )
                nonce = uuid4().hex[:8]
                out.append(
                    {
                        "label_or_tool": key,
                        "examples": examples_for_call,
                        "diversity_rule": f"{rules_pool[i]} [variation_id={nonce}]",
                        "difficulty": diff_pool[i],
                        "is_sentinel": is_sentinel,
                    }
                )
        # Shuffle so calls don't all fire for one key first.
        self._rng.shuffle(out)
        # Cap total calls at GENERATOR_BATCH_SIZE so one loop iteration
        # doesn't fire 1000 prompts.
        return out[:GENERATOR_BATCH_SIZE]

    # -- Per-row quota key --------------------------------------------------

    def _row_quota_key(self, task_type: TaskType, row: dict[str, Any]) -> str:
        if task_type is TaskType.CLASSIFICATION:
            return str(row.get("label", "")).strip()
        if task_type is TaskType.TOOL_CALLING:
            try:
                import json

                inner = json.loads(row.get("answer", "{}"))
                return str(inner.get("name", "")).strip()
            except Exception:  # noqa: BLE001
                return ""
        return "__qa__"


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _emit_factory(
    cb: ProgressCallback | None,
    *,
    target: int,
    samples_generated: int,
    samples_valid: int,
    samples_rejected: int,
    duplicates_removed: int,
) -> Callable[..., None]:
    """Build a closure that emits a GenerationProgress to the callback."""

    state = {
        "samples_generated": samples_generated,
        "samples_valid": samples_valid,
        "samples_rejected": samples_rejected,
        "duplicates_removed": duplicates_removed,
    }

    def emit(phase: GenerationPhase, **overrides: Any) -> None:
        if cb is None:
            return
        state.update({k: v for k, v in overrides.items() if k in state})
        cb(
            GenerationProgress(
                phase=phase,
                samples_generated=state["samples_generated"],
                samples_target=target,
                samples_valid=state["samples_valid"],
                samples_rejected=state["samples_rejected"],
                duplicates_removed=state["duplicates_removed"],
                current_loop=overrides.get("current_loop"),
                judge_rejected=overrides.get("judge_rejected"),
                judge_parse_failures=overrides.get("judge_parse_failures"),
                dedup_rejected=overrides.get("dedup_rejected"),
            )
        )

    return emit


def _compute_classification_quota(labels: list[str], target: int) -> dict[str, int]:
    """90/10 split: SENTINEL_RATIO of target goes to the sentinel label,
    the rest is split evenly across the real classes.
    """
    if CLASSIFICATION_SENTINEL_LABEL not in labels:
        # Sentinel must be in labels by the time we get here.
        return {label: target // max(1, len(labels)) for label in labels}
    real_labels = [lbl for lbl in labels if lbl != CLASSIFICATION_SENTINEL_LABEL]
    target_unknown = math.ceil(target * SENTINEL_RATIO)
    target_others = target - target_unknown
    if not real_labels:
        return {CLASSIFICATION_SENTINEL_LABEL: target}
    per_real = math.ceil(target_others / len(real_labels))
    quota = {lbl: per_real for lbl in real_labels}
    quota[CLASSIFICATION_SENTINEL_LABEL] = target_unknown
    return quota


def _compute_tool_quota(tool_names: list[str], target: int) -> dict[str, int]:
    if TOOL_CALLING_SENTINEL_NAME not in tool_names:
        return {name: target // max(1, len(tool_names)) for name in tool_names}
    real_names = [name for name in tool_names if name != TOOL_CALLING_SENTINEL_NAME]
    target_unknown = math.ceil(target * SENTINEL_RATIO)
    target_others = target - target_unknown
    if not real_names:
        return {TOOL_CALLING_SENTINEL_NAME: target}
    per_real = math.ceil(target_others / len(real_names))
    quota = {name: per_real for name in real_names}
    quota[TOOL_CALLING_SENTINEL_NAME] = target_unknown
    return quota


def _make_sentinel_tool_def() -> ToolDefinition:
    return ToolDefinition(
        name=TOOL_CALLING_SENTINEL_NAME,
        description=TOOL_CALLING_SENTINEL_DESCRIPTION,
        parameters={},
    )


def _group_by(rows: list[dict[str, Any]], *, key: str) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        k = str(r.get(key, "")).strip()
        if not k:
            continue
        out.setdefault(k, []).append(r)
    return out


def _group_tool_examples(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group tool_calling seed rows by the tool name encoded in `answer`."""
    import json as _json

    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        try:
            inner = _json.loads(r.get("answer", "{}"))
            name = str(inner.get("name", "")).strip()
        except Exception:  # noqa: BLE001
            continue
        if not name:
            continue
        out.setdefault(name, []).append(r)
    return out


def _seed_text_field(task_type: TaskType) -> str:
    """The row field used as MinHash input for dedup."""
    return "text" if task_type is TaskType.CLASSIFICATION else "question"


def _apply_collection_quota(
    rows: list[dict[str, Any]],
    *,
    accepted: list[dict[str, Any]],
    collected_per_key: dict[str, int],
    quota: dict[str, int],
    target: int,
    key_for_row: Callable[[dict[str, Any]], str],
) -> int:
    """Accept rows up to per-key quota; mutate ``accepted`` + ``collected_per_key``.

    Mirrors the inline collection step of the SDG loop verbatim:

      - Stop once ``len(accepted) >= target``.
      - When ``quota`` is non-empty, drop rows whose bucket is already full
        and increment the per-key counter on acceptance.
      - When ``quota`` is empty (QA flow), accept until target hits.

    Returns the number of rows actually added in this call. The orchestrator
    uses this count to drive the adaptive over-gen multiplier and the
    consecutive-failure counter.
    """
    added = 0
    for row in rows:
        if len(accepted) >= target:
            break
        key = key_for_row(row)
        if quota:
            if collected_per_key.get(key, 0) >= quota.get(key, 0):
                continue
            collected_per_key[key] = collected_per_key.get(key, 0) + 1
        accepted.append(row)
        added += 1
    return added


def _update_over_gen_mult(
    over_gen_mult: float, *, added: int, batch_size: int
) -> float:
    """EMA-smoothed adaptive over-generation multiplier.

    When yield per request is "OK" (>5%) we ease the multiplier toward
    ``1/yield`` (clamped to [MIN, MAX]). When yield collapses, we crank the
    multiplier 1.5x toward the ceiling so the next loop iteration over-asks
    more aggressively. Pure math — no side effects.
    """
    yield_per_req = added / max(1, batch_size)
    if yield_per_req > 0.05:
        new_mult = max(
            MIN_OVER_GEN_MULT, min(MAX_OVER_GEN_MULT, 1.0 / yield_per_req)
        )
        return 0.5 * over_gen_mult + 0.5 * new_mult
    return min(MAX_OVER_GEN_MULT, over_gen_mult * 1.5)


__all__ = [
    "GenerationProgress",
    "GenerationPhase",
    "ProgressCallback",
    "SDGRunResult",
    "SDGAbortedError",
    "SyntheticDataGenerator",
]
