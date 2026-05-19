"""Tier-2 characterization snapshot for `SyntheticDataGenerator.generate()`.

Wraps the full SDG pipeline with a mocked OpenRouter responder so we can
capture the deterministic ``SDGRunResult`` shape produced from a fixed
sequence of meta-prompt, generator, and judge responses.

**Status:** scaffold only. Each test skips with a clear instruction unless
the corresponding recorded payload exists at
``tests/fixtures/recorded/openrouter/<name>.json``. The capture playbook is
in ``docs/runbooks/snapshot_harness.md`` — run an SDG smoke against
vast.ai, intercept the OpenRouter HTTP traffic, drop the JSON bodies here,
and rerun ``pytest --snapshot-update``.

Once the fixtures land, the assertions below upgrade from skipped to
passing snapshots that will catch any future refactor of the SDG
orchestrator.
"""

from __future__ import annotations

import pytest

# The actual generator imports stay lazy so the file is importable even
# before the fixtures exist — the skip path inside each test keeps the
# imports under their conditional.

pytestmark = pytest.mark.characterization


@pytest.mark.asyncio
async def test_sdg_classification_full(
    recorded_payload,
    openrouter_responder,
    seed_dataset_factory,
    snapshot,
):
    """Classification SDG: with_seed + sentinel quota → SDGRunResult.

    Required fixtures:
      - ``sdg_classification_meta.json``      (one meta-prompt response)
      - ``sdg_classification_batch.json``     (one generator response per loop)
      - ``sdg_classification_judge.json``     (one judge call per row)
    """
    meta = recorded_payload("sdg_classification_meta")
    batch = recorded_payload("sdg_classification_batch")
    judge = recorded_payload("sdg_classification_judge")

    # When recorded files land, wire them into a stateful responder that
    # dispatches by call_index (meta=0, then generator+judge interleaved)
    # and snapshot the resulting SDGRunResult.
    pytest.skip(
        "Scaffold only — recorded fixtures present but assertion not yet "
        "wired. Implement responder dispatch + snapshot SDGRunResult in "
        "the next harness session."
    )
    # Reference to silence unused-var warnings until implemented.
    _ = (meta, batch, judge, openrouter_responder, seed_dataset_factory, snapshot)


@pytest.mark.asyncio
async def test_sdg_qa_full(
    recorded_payload,
    openrouter_responder,
    seed_dataset_factory,
    snapshot,
):
    """QA SDG: with_seed (no sentinel) → SDGRunResult.

    Required fixtures: ``sdg_qa_meta.json``, ``sdg_qa_batch.json``,
    ``sdg_qa_judge.json``.
    """
    meta = recorded_payload("sdg_qa_meta")
    batch = recorded_payload("sdg_qa_batch")
    judge = recorded_payload("sdg_qa_judge")
    pytest.skip(
        "Scaffold only — recorded fixtures present but assertion not yet wired."
    )
    _ = (meta, batch, judge, openrouter_responder, seed_dataset_factory, snapshot)


@pytest.mark.asyncio
async def test_sdg_tool_calling_full(
    recorded_payload,
    openrouter_responder,
    seed_dataset_factory,
    snapshot,
):
    """Tool-calling SDG: with_seed + sentinel quota → SDGRunResult.

    Required fixtures: ``sdg_tool_calling_meta.json``,
    ``sdg_tool_calling_batch.json``, ``sdg_tool_calling_judge.json``.
    """
    meta = recorded_payload("sdg_tool_calling_meta")
    batch = recorded_payload("sdg_tool_calling_batch")
    judge = recorded_payload("sdg_tool_calling_judge")
    pytest.skip(
        "Scaffold only — recorded fixtures present but assertion not yet wired."
    )
    _ = (meta, batch, judge, openrouter_responder, seed_dataset_factory, snapshot)


def test_fake_minio_round_trip(fake_minio):
    """Smoke test for the `fake_minio` conftest fixture.

    Verifies the in-memory MinIO stub round-trips bytes correctly so future
    Tier-2 tests can trust it. Does NOT depend on any recorded fixture.
    """
    fake_minio.make_bucket("datasets")
    fake_minio.put_object("datasets", "test.jsonl", b'{"a":1}\n', length=8)

    assert fake_minio.bucket_exists("datasets")
    assert fake_minio.stat_object("datasets", "test.jsonl").size == 8
    body = fake_minio.get_object("datasets", "test.jsonl").read()
    assert body == b'{"a":1}\n'

    keys = [obj.object_name for obj in fake_minio.list_objects("datasets")]
    assert keys == ["test.jsonl"]


def test_fake_redis_pubsub_captures(fake_redis_pubsub):
    """Smoke test for the `fake_redis_pubsub` fixture.

    Confirms publish() returns a delivered-count and the spy records the
    payload so Tier-2 tests can assert on emitted SDGProgress messages.
    """
    delivered = fake_redis_pubsub.client.publish("job:abc", b'{"phase":"setup"}')
    assert isinstance(delivered, int)
    assert fake_redis_pubsub.published == [("job:abc", b'{"phase":"setup"}')]
