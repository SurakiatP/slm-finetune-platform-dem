"""Cross-layer contract: SDG-time producer -> stored blob -> endpoint reader.

Two layers written by different owners have to agree on one JSONB shape:

  * `ai_engine.data_gen.insights.JudgeScoreAggregator.to_dict()` produces the
    judge rollup, which `workers/tasks/data_generation.py` drops verbatim into
    `Dataset.generation_metadata["insights"]["judge"]`.
  * `api.services.dataset_insights._map_stored_insights` reads that blob back
    for `GET /datasets/{id}/insights`.

The existing suites cover each side against a *hand-written* fixture of the
other side's shape (`tests/unit/test_worker_sdg_insights.py` pins the writer,
`tests/unit/test_dataset_insights_api.py` feeds the reader a literal blob), so
a drift in the aggregator's real output could pass both. These tests close
that loop: real aggregator output -> the worker's literal envelope -> real
reader, with no hand-written judge dict anywhere.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from ai_engine.data_gen.insights import JUDGE_HISTOGRAM_BINS, JudgeScoreAggregator
from ai_engine.data_gen.judge import JudgeScore
from api.schemas.datasets import GenerationCounts
from api.services.dataset_insights import _map_stored_insights

_REPO = Path(__file__).resolve().parents[2]
_WORKER = _REPO / "workers" / "tasks" / "data_generation.py"


def _real_aggregate() -> dict:
    """A `JudgeScoreAggregator.to_dict()` built the way the SDG loop builds it.

    Mixed keys and a mix of kept (>= JUDGE_THRESHOLD) and rejected scores, so
    the rollup exercises multiple `by_key` buckets and more than one histogram
    bin — not a single-value degenerate case.
    """
    agg = JudgeScoreAggregator()
    agg.add("billing", JudgeScore(fidelity=0.9, naturalness=0.9, utility=0.9, reasoning="ok"))
    agg.add("billing", JudgeScore(fidelity=0.2, naturalness=0.3, utility=0.1, reasoning="bad"))
    agg.add("refund", JudgeScore(fidelity=1.0, naturalness=0.8, utility=0.75, reasoning=""))
    agg.add("", JudgeScore(fidelity=0.5, naturalness=0.5, utility=0.5, reasoning=""))
    out = agg.to_dict()
    assert out is not None
    return out


def _worker_envelope(judge: dict | None) -> dict:
    """The exact `generation_metadata` envelope `data_generation.py` writes."""
    return {
        "completed_at": "2026-08-25T00:00:00+00:00",
        "role": "train",
        "insights": {
            "schema_version": 1,
            "judge": judge,
            "counts": {
                "generated": 25,
                "target": 20,
                "train_rows": 18,
                "holdout_rows": 2,
                "schema_rejected": 3,
                "duplicates_removed": 4,
                "semantic_duplicates_removed": 2,
                "judge_rejected": 1,
                "judge_parse_failures": 0,
            },
            "computed_at": "2026-08-25T00:00:00+00:00",
        },
    }


class TestAggregatorToReaderRoundTrip:
    def test_real_aggregate_is_readable_by_the_endpoint_reader(self) -> None:
        aggregate = _real_aggregate()
        judge, judge_by_key, counts = _map_stored_insights(_worker_envelope(aggregate))

        assert judge is not None, (
            "the endpoint reader degraded a REAL JudgeScoreAggregator.to_dict() "
            "to None — producer and reader shapes have drifted apart"
        )
        assert judge.count == aggregate["count"] == 4
        for axis in ("fidelity", "naturalness", "utility", "weighted"):
            dim = getattr(judge, axis)
            assert dim.mean == pytest.approx(aggregate["mean"][axis])
            assert dim.histogram == aggregate["histogram"][axis]
            assert len(dim.histogram) == JUDGE_HISTOGRAM_BINS
            assert sum(dim.histogram) == judge.count

        assert judge_by_key is not None
        assert set(judge_by_key) == set(aggregate["by_key"]) == {"billing", "refund", "__unknown__"}
        assert judge_by_key["billing"].count == 2
        assert judge_by_key["__unknown__"].weighted.mean == pytest.approx(0.5)
        assert sum(v.count for v in judge_by_key.values()) == judge.count

        assert counts is not None
        assert counts.generated == 25
        assert counts.target == 20
        assert counts.schema_rejected == 3
        assert counts.duplicates_removed == 4
        assert counts.semantic_duplicates_removed == 2
        assert counts.judge_rejected == 1
        assert counts.judge_parse_failures == 0

    def test_round_trip_survives_a_json_dumps_loads(self) -> None:
        """The blob lands in JSONB — it must survive serialisation unchanged."""
        envelope = _worker_envelope(_real_aggregate())
        rehydrated = json.loads(json.dumps(envelope))

        judge, judge_by_key, counts = _map_stored_insights(rehydrated)
        direct_judge, _, _ = _map_stored_insights(envelope)

        assert judge is not None and direct_judge is not None
        assert judge.model_dump() == direct_judge.model_dump()
        assert judge_by_key is not None and counts is not None

    def test_judge_none_from_an_empty_run_still_yields_counts(self) -> None:
        """`to_dict()` returns None when nothing was judged; counts still read."""
        assert JudgeScoreAggregator().to_dict() is None

        judge, judge_by_key, counts = _map_stored_insights(_worker_envelope(None))
        assert judge is None
        assert judge_by_key is None
        assert counts is not None and counts.generated == 25


class TestWorkerCountsCoverEveryResponseField:
    def test_worker_writes_every_generation_counts_field(self) -> None:
        """Every `GenerationCounts` field must be a key the worker actually writes.

        `_map_stored_insights` builds `GenerationCounts` with
        `counts_raw.get(field)`, so a field the worker never writes silently
        serialises as `null` forever instead of failing loudly. Pinning the
        writer's key set here makes that a test failure instead.
        """
        source = _WORKER.read_text(encoding="utf-8")
        block = re.search(r'"counts":\s*\{(.*?)\n\s*\},', source, re.DOTALL)
        assert block is not None, (
            'could not locate the insights "counts" dict in '
            f"{_WORKER.relative_to(_REPO)} — was the payload restructured?"
        )
        written = set(re.findall(r'"(\w+)":', block.group(1)))

        missing = set(GenerationCounts.model_fields) - written
        assert not missing, (
            f"GenerationCounts exposes {sorted(missing)} but the SDG worker never "
            "writes those keys — the endpoint would always return null for them"
        )
