"""Tier 1 + Tier 2 characterization snapshots for Node 3b + 4 (SDG + Holdout).

Targets two boundaries:

  • Tier 1 — pure helpers in ``ai_engine/data_gen/generator.py`` that aren't
    yet covered by ``test_snapshot_generator_builders.py``. These capture
    behaviour of internal sentinel/quota/derivation logic that the refactor
    must preserve byte-for-byte.

  • Tier 2 — full ``SyntheticDataGenerator.generate()`` orchestration with
    inline-mocked OpenRouter via the ``openrouter_responder`` fixture.
    Snapshots cover ``SDGRunResult`` shape plus the train/holdout split
    produced by ``holdout_split.split_rows`` for the three task types.
    The Tier 2 tests deliberately use a tiny seeded RNG + small targets so
    the mocked LLM responses produce a deterministic, reproducible result.

The recorded-fixtures Tier 2 scaffold lives separately in
``test_snapshot_generator_full.py`` (currently skipped); this file uses
inline mocks so we can build the safety net for the SDG refactor without
needing live OpenRouter capture (CH.8) first.

NOTE: We deliberately mock at the ``AsyncOpenAI.chat.completions.create``
boundary using the ``openrouter_responder`` factory. The sync
``OpenRouterClient`` is also stubbed because ``_pdf_first_pass`` would
otherwise need it; we skip the PDF branch entirely by passing
``pdf_bytes=None``.
"""

from __future__ import annotations

import json
import os
import random
from types import SimpleNamespace
from typing import Any

import pytest

# Worker import requires a non-empty DATABASE_URL / OPENROUTER_API_KEY at
# import time (Celery app evaluates settings on import). Provide dummies
# BEFORE importing anything from workers.tasks. These dummies are never
# touched by the tests — the Tier 2 tests mock the OpenRouter client and
# never hit a real DB.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test_user:test_pw@localhost/test_db"
)
os.environ.setdefault("OPENROUTER_API_KEY", "test-dummy")

from ai_engine.data_gen.constants import (  # noqa: E402
    CLASSIFICATION_SENTINEL_LABEL,
    TOOL_CALLING_SENTINEL_NAME,
)
from ai_engine.data_gen.generator import (  # noqa: E402
    GenerationProgress,
    SyntheticDataGenerator,
    _emit_factory,
)
from ai_engine.data_gen.holdout_split import split_rows  # noqa: E402
from ai_engine.data_gen.openrouter_client import (  # noqa: E402
    AsyncOpenRouterClient,
    OpenRouterClient,
)
from api.schemas.enums import SDGMode, TaskType  # noqa: E402
from api.schemas.sdg import (  # noqa: E402
    ClassificationGenConfig,
    SDGRequestDescriptionOnly,
    SDGRequestWithSeed,
)
from workers.tasks.data_generation import _split_seed  # noqa: E402


# ---------------------------------------------------------------------------
# Tier 1 — pure helpers not yet in test_snapshot_generator_builders.py
# ---------------------------------------------------------------------------


def test_emit_factory_records_progress_events(snapshot):
    """``_emit_factory`` returns a closure that emits progress events via cb.

    Snapshot the sequence of GenerationProgress payloads produced when a
    typical short emit sequence runs, captured into a list. Confirms the
    state machine (carried in the closure's ``state`` dict) updates only on
    explicitly listed keys.
    """
    captured: list[dict[str, Any]] = []

    def cb(p: GenerationProgress) -> None:
        captured.append(
            {
                "phase": p.phase,
                "samples_generated": p.samples_generated,
                "samples_target": p.samples_target,
                "samples_valid": p.samples_valid,
                "samples_rejected": p.samples_rejected,
                "duplicates_removed": p.duplicates_removed,
                "current_loop": p.current_loop,
                "judge_rejected": p.judge_rejected,
                "judge_parse_failures": p.judge_parse_failures,
                "dedup_rejected": p.dedup_rejected,
            }
        )

    emit = _emit_factory(
        cb,
        target=10,
        samples_generated=0,
        samples_valid=0,
        samples_rejected=0,
        duplicates_removed=0,
    )

    emit("meta_prompting")
    emit("generating", current_loop=0, samples_rejected=1)
    emit(
        "judging",
        current_loop=0,
        samples_rejected=2,
        duplicates_removed=1,
        judge_rejected=3,
        judge_parse_failures=1,
    )
    emit(
        "generating",
        current_loop=1,
        samples_generated=4,
        samples_valid=4,
        dedup_rejected=2,
    )

    assert captured == snapshot


def test_emit_factory_noop_when_cb_is_none():
    """Passing ``cb=None`` returns a callable that silently no-ops."""
    emit = _emit_factory(
        None,
        target=5,
        samples_generated=0,
        samples_valid=0,
        samples_rejected=0,
        duplicates_removed=0,
    )
    # Just call it; no assertion needed — must not raise / must not blow up.
    emit("meta_prompting")
    emit("generating", current_loop=0)


def test_row_quota_key_classification():
    """``_row_quota_key`` extracts the row's ``label`` for classification."""
    gen = _make_generator()
    assert (
        gen._row_quota_key(
            TaskType.CLASSIFICATION,
            {"text": "x", "label": "ปัญหาเทคนิค"},
        )
        == "ปัญหาเทคนิค"
    )
    assert (
        gen._row_quota_key(
            TaskType.CLASSIFICATION,
            {"text": "x", "label": " padded "},
        )
        == "padded"
    )


def test_row_quota_key_tool_calling():
    """``_row_quota_key`` parses tool name from the JSON-encoded ``answer``."""
    gen = _make_generator()
    answer = json.dumps({"name": "set_volume", "parameters": {"level": 50}})
    assert (
        gen._row_quota_key(
            TaskType.TOOL_CALLING,
            {"question": "q", "answer": answer},
        )
        == "set_volume"
    )
    # malformed JSON falls back to empty string (not raising)
    assert (
        gen._row_quota_key(
            TaskType.TOOL_CALLING,
            {"question": "q", "answer": "not-json"},
        )
        == ""
    )


def test_row_quota_key_qa():
    """``_row_quota_key`` returns the QA bucket for QA rows."""
    gen = _make_generator()
    assert (
        gen._row_quota_key(
            TaskType.QA,
            {"question": "q1", "answer": "a1"},
        )
        == "__qa__"
    )


@pytest.mark.parametrize(
    "task_type,holdout,dataset_id",
    [
        (TaskType.CLASSIFICATION, 100, "00000000-0000-0000-0000-000000000001"),
        (TaskType.QA, 50, "11111111-1111-1111-1111-111111111111"),
        (TaskType.TOOL_CALLING, 0, "22222222-2222-2222-2222-222222222222"),
    ],
    ids=["cls_100_ds1", "qa_50_ds2", "tool_0_ds3"],
)
def test_split_seed_deterministic(
    task_type: TaskType, holdout: int, dataset_id: str
) -> None:
    """``_split_seed`` is deterministic across calls (same input → same seed)."""
    request = SDGRequestDescriptionOnly(
        project_id="00000000-0000-0000-0000-0000000000ff",
        task_type=task_type,
        task_description="x" * 12,
        num_samples=10,
        holdout_size=holdout,
        classification_config=(
            ClassificationGenConfig(labels=["a", "b"])
            if task_type is TaskType.CLASSIFICATION
            else None
        ),
        tool_calling_config=None
        if task_type is not TaskType.TOOL_CALLING
        else _ToolCfgFactory().build(),
    )
    s1 = _split_seed(request, dataset_id)
    s2 = _split_seed(request, dataset_id)
    assert s1 == s2
    assert 0 <= s1 < 2**31 - 1


# ---------------------------------------------------------------------------
# Tier 2 — SyntheticDataGenerator.generate() with inline OpenRouter mock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sdg_classification_with_seed_full(
    openrouter_responder,
    seed_dataset_factory,
    snapshot,
):
    """Classification + with_seed: small seed (3 rows), target 4, holdout 2.

    The mocked LLM produces 5 candidate rows per generator call (cycling
    through the seed labels + sentinel) and gives every row a passing judge
    score. This drives the orchestrator through one full happy-path loop
    iteration and returns deterministic ``SDGRunResult``.
    """
    seed_rows = seed_dataset_factory("classification", n=3)
    # 4 train + 2 holdout = 6 effective rows
    request = SDGRequestWithSeed(
        project_id="00000000-0000-0000-0000-000000000010",
        task_type=TaskType.CLASSIFICATION,
        task_description="คัดประเภทคำถามลูกค้าธนาคาร",
        num_samples=6,  # already effective target
        holdout_size=0,  # we'll split after via split_rows directly
        seed_dataset_id="00000000-0000-0000-0000-000000000099",
    )
    result = await _run_with_mock(
        request,
        seed_rows=seed_rows,
        openrouter_responder=openrouter_responder,
        rng_seed=42,
    )

    # Capture the deterministic SDGRunResult fields. valid_rows is the
    # generator's accepted set (already shape-validated).
    summary = _summarize_result(result)
    assert summary == snapshot

    # Now run the holdout split as the worker would.
    train, holdout = split_rows(
        result.valid_rows,
        TaskType.CLASSIFICATION,
        holdout_size=2,
        rng=random.Random(7),
    )
    assert {
        "train_count": len(train),
        "holdout_count": len(holdout),
        "train_labels_sorted": sorted(r["label"] for r in train),
        "holdout_labels_sorted": sorted(r["label"] for r in holdout),
    } == snapshot


@pytest.mark.asyncio
async def test_sdg_qa_description_only_full(
    openrouter_responder,
    snapshot,
):
    """QA + description_only: no seed rows, target 6, holdout 2.

    Mocked LLM returns 5 QA pairs per generator call; all pass judge gate.
    """
    request = SDGRequestDescriptionOnly(
        project_id="00000000-0000-0000-0000-000000000020",
        task_type=TaskType.QA,
        task_description="ตอบคำถามนโยบายการคืนสินค้า 30 วัน",
        num_samples=6,
        holdout_size=0,
    )
    result = await _run_with_mock(
        request,
        seed_rows=[],
        openrouter_responder=openrouter_responder,
        rng_seed=7,
    )
    summary = _summarize_result(result)
    assert summary == snapshot

    train, holdout = split_rows(
        result.valid_rows,
        TaskType.QA,
        holdout_size=2,
        rng=random.Random(3),
    )
    assert {
        "train_count": len(train),
        "holdout_count": len(holdout),
        "train_questions_sorted": sorted(r["question"] for r in train),
        "holdout_questions_sorted": sorted(r["question"] for r in holdout),
    } == snapshot


@pytest.mark.asyncio
async def test_sdg_tool_calling_with_seed_full(
    openrouter_responder,
    seed_dataset_factory,
    snapshot,
):
    """Tool-calling + with_seed: 3 seed rows (3 tools), target 6, holdout 2.

    Tool definitions are derived from the seed answers (the
    SyntheticDataGenerator code path under test). The mocked LLM returns
    rows cycling through those tools + sentinel.
    """
    seed_rows = seed_dataset_factory("tool_calling", n=3)
    request = SDGRequestWithSeed(
        project_id="00000000-0000-0000-0000-000000000030",
        task_type=TaskType.TOOL_CALLING,
        task_description="แปลคำสั่งสมาร์ทโฮมเป็น JSON tool call",
        num_samples=6,
        holdout_size=0,
        seed_dataset_id="00000000-0000-0000-0000-000000000099",
    )
    result = await _run_with_mock(
        request,
        seed_rows=seed_rows,
        openrouter_responder=openrouter_responder,
        rng_seed=11,
    )
    summary = _summarize_result(result)
    assert summary == snapshot

    train, holdout = split_rows(
        result.valid_rows,
        TaskType.TOOL_CALLING,
        holdout_size=2,
        rng=random.Random(5),
    )
    # Stratify on parsed tool name
    def _tool_name(r: dict[str, Any]) -> str:
        try:
            return str(json.loads(r["answer"])["name"])
        except Exception:  # noqa: BLE001
            return "__bad__"

    assert {
        "train_count": len(train),
        "holdout_count": len(holdout),
        "train_tools_sorted": sorted(_tool_name(r) for r in train),
        "holdout_tools_sorted": sorted(_tool_name(r) for r in holdout),
    } == snapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_generator(
    *, async_client: AsyncOpenRouterClient | None = None
) -> SyntheticDataGenerator:
    """Build a SyntheticDataGenerator with dummy clients for pure-helper tests."""
    a = async_client or AsyncOpenRouterClient(api_key="dummy")
    s = OpenRouterClient(
        api_key="dummy",
        teacher_model="placeholder/unused",
    )
    return SyntheticDataGenerator(a, s, rng=random.Random(0))


class _ToolCfgFactory:
    """Helper: build a minimal valid ToolCallingGenConfig for _split_seed tests."""

    def build(self):
        from api.schemas.data_formats import ToolDefinition
        from api.schemas.sdg import ToolCallingGenConfig

        return ToolCallingGenConfig(
            tool_definitions=[
                ToolDefinition(name="a", description="aa aa aa", parameters={}),
                ToolDefinition(name="b", description="bb bb bb", parameters={}),
            ]
        )


def _build_chat_response(content: str, model: str = "stub") -> Any:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason="stop",
            )
        ],
        model=model,
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


def _meta_response_json(include_unknown: bool) -> str:
    rules = {
        "diversity_rules": [
            f"rule {i}: vary phrasing for axis {i}" for i in range(1, 11)
        ],
    }
    if include_unknown:
        rules["unknown_diversity_rules"] = [
            f"unk rule {i}: off-topic / ambiguous" for i in range(1, 7)
        ]
    return json.dumps(rules, ensure_ascii=False)


def _judge_response_json() -> str:
    # Weighted = 0.4*0.9 + 0.3*0.9 + 0.3*0.9 = 0.9 >= JUDGE_THRESHOLD (0.7)
    return json.dumps(
        {"fidelity": 0.9, "naturalness": 0.9, "utility": 0.9, "reasoning": "ok"}
    )


def _classification_sample_pool(seed_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable canonical pool of cls samples; the orchestrator stamps label so
    we just need the ``text`` to differ per row."""
    base_texts = [
        f"ตัวอย่างคำถามผู้ใช้ที่ {i:02d}" for i in range(50)
    ]
    return [{"text": t, "label": "__will_be_stamped__"} for t in base_texts]


def _qa_sample_pool() -> list[dict[str, Any]]:
    return [
        {"question": f"คำถามนโยบาย #{i:02d}?", "answer": f"คำตอบที่ {i:02d}."}
        for i in range(50)
    ]


def _tool_sample_pool(tool_name: str) -> list[dict[str, Any]]:
    """Build 50 distinct tool-calling samples for the given tool name."""
    out: list[dict[str, Any]] = []
    for i in range(50):
        out.append(
            {
                "question": f"คำสั่ง {tool_name} ลำดับ #{i:02d}",
                "answer": json.dumps(
                    {
                        "name": tool_name,
                        "parameters": {"level": 10 + i},
                    },
                    ensure_ascii=False,
                ),
            }
        )
    return out


def _extract_target_tool(user_prompt: str) -> str | None:
    """Pull the requested tool name out of the generator user prompt.

    The prompt embeds ``[Target tool]\\n'<tool_name>'`` (single-quoted via
    ``repr``). Returns the tool name on a match, else None.
    """
    marker = "[Target tool]\n"
    idx = user_prompt.find(marker)
    if idx == -1:
        return None
    line = user_prompt[idx + len(marker) :].split("\n", 1)[0].strip()
    # Strip the single quotes from repr().
    if line.startswith("'") and "'" in line[1:]:
        return line[1 : 1 + line[1:].index("'")]
    return line.strip("'\"") or None


class _SDGResponder:
    """Stateful responder for the inline-mock Tier 2 tests.

    Dispatches by ``model`` kwarg:
      • DIVERSITY_RULES model → meta-prompt response
      • JUDGE model → judge response
      • GENERATOR model → generator response (per-task pool)
    """

    def __init__(self, *, task_type: TaskType, include_unknown: bool) -> None:
        self._task_type = task_type
        self._include_unknown = include_unknown
        self._gen_idx = 0

    async def __call__(self, kwargs: dict[str, Any], call_index: int) -> Any:
        from ai_engine.data_gen import models as _models

        model = kwargs.get("model", "")
        if model == _models.DIVERSITY_RULES:
            return _build_chat_response(
                _meta_response_json(include_unknown=self._include_unknown),
                model=model,
            )
        if model == _models.JUDGE:
            return _build_chat_response(_judge_response_json(), model=model)
        # Generator path — return 5 samples per call. We use a sliding window
        # over a 50-row pool so each call returns distinct content (prevents
        # MinHash dedup from collapsing everything to one row).
        if self._task_type is TaskType.CLASSIFICATION:
            pool = _classification_sample_pool([])
        elif self._task_type is TaskType.QA:
            pool = _qa_sample_pool()
        else:
            # tool_calling: pull the target tool from the prompt so the row
            # actually fills the right quota bucket. Falls back to a real
            # catalog name if parsing fails (defensive — shouldn't happen).
            msgs = kwargs.get("messages") or []
            user_msg = ""
            for m in msgs:
                if m.get("role") == "user":
                    user_msg = m.get("content", "")
                    break
            target_tool = _extract_target_tool(user_msg) or "set_volume"
            pool = _tool_sample_pool(target_tool)
        start = (self._gen_idx * 5) % (len(pool) - 5)
        slice_ = pool[start : start + 5]
        self._gen_idx += 1
        return _build_chat_response(
            json.dumps({"samples": slice_}, ensure_ascii=False), model=model
        )


async def _run_with_mock(
    request,
    *,
    seed_rows: list[dict[str, Any]],
    openrouter_responder,
    rng_seed: int,
):
    """Run ``SyntheticDataGenerator.generate()`` with mocked OpenRouter clients."""
    async_client = AsyncOpenRouterClient(api_key="dummy")
    sync_client = OpenRouterClient(api_key="dummy", teacher_model="placeholder/unused")
    include_unknown = request.task_type in (
        TaskType.CLASSIFICATION,
        TaskType.TOOL_CALLING,
    )
    responder = _SDGResponder(
        task_type=request.task_type, include_unknown=include_unknown
    )
    openrouter_responder(async_client, responder)
    # sync client never gets called in our test (no PDF), but we still hand
    # the generator a working stub.
    sync_client._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: None))
    )

    gen = SyntheticDataGenerator(
        async_client, sync_client, rng=random.Random(rng_seed)
    )
    try:
        return await gen.generate(request, seed_rows=seed_rows, pdf_bytes=None)
    finally:
        await async_client.aclose()


def _summarize_result(result) -> dict[str, Any]:
    """Distil SDGRunResult into a deterministic, snapshot-friendly shape.

    We sort accepted rows by their canonical text field so the snapshot is
    insensitive to in-loop iteration order (the orchestrator's RNG decides
    the per-key call order). All counts and the row-content set itself stay
    asserted.
    """
    rows = list(result.valid_rows)
    # Canonical sort key per task: cls=text, qa+tool=question.
    sort_key = "question" if rows and "question" in rows[0] else "text"
    rows_sorted = sorted(rows, key=lambda r: r.get(sort_key, ""))
    return {
        "valid_row_count": len(rows),
        "rejected_count": result.rejected_count,
        "duplicate_count": result.duplicate_count,
        "judge_rejected_count": result.judge_rejected_count,
        "judge_parse_failures": result.judge_parse_failures,
        # api_calls / failed_attempts depend on loop scheduling decisions
        # which are RNG-stable but easier to diff if we list them too.
        "api_calls": result.api_calls,
        "failed_attempts": list(result.failed_attempts),
        "rows_sorted": rows_sorted,
    }
