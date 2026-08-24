"""Unit tests for `JudgeScoreAggregator` (pure judge-score rollup)."""

from __future__ import annotations

import json

from ai_engine.data_gen.insights import JUDGE_HISTOGRAM_BINS, JudgeScoreAggregator
from ai_engine.data_gen.judge import JudgeScore


def _score(fidelity: float, naturalness: float, utility: float) -> JudgeScore:
    return JudgeScore(
        fidelity=fidelity, naturalness=naturalness, utility=utility, reasoning=""
    )


def test_empty_aggregator_returns_none():
    agg = JudgeScoreAggregator()
    assert agg.to_dict() is None
    assert len(agg) == 0


def test_single_score_shape_and_values():
    agg = JudgeScoreAggregator()
    s = _score(0.8, 0.7, 0.75)
    agg.add("billing", s)

    d = agg.to_dict()
    assert d is not None
    assert d["count"] == 1
    assert d["mean"] == {
        "fidelity": 0.8,
        "naturalness": 0.7,
        "utility": 0.75,
        "weighted": round(s.weighted, 4),
    }
    for axis in ("fidelity", "naturalness", "utility", "weighted"):
        assert len(d["histogram"][axis]) == JUDGE_HISTOGRAM_BINS
        assert sum(d["histogram"][axis]) == 1

    assert d["by_key"].keys() == {"billing"}
    billing = d["by_key"]["billing"]
    assert billing["count"] == 1
    assert billing["mean"] == d["mean"]
    assert "by_key" not in billing


def test_multi_key_grouping():
    agg = JudgeScoreAggregator()
    agg.add("billing", _score(0.9, 0.9, 0.9))
    agg.add("billing", _score(0.5, 0.5, 0.5))
    agg.add("shipping", _score(1.0, 1.0, 1.0))

    d = agg.to_dict()
    assert d["count"] == 3
    assert set(d["by_key"].keys()) == {"billing", "shipping"}
    assert d["by_key"]["billing"]["count"] == 2
    assert d["by_key"]["shipping"]["count"] == 1

    # Overall histogram bin counts must equal the sum of per-key bins.
    for axis in ("fidelity", "naturalness", "utility", "weighted"):
        summed = [
            b_hist + s_hist
            for b_hist, s_hist in zip(
                d["by_key"]["billing"]["histogram"][axis],
                d["by_key"]["shipping"]["histogram"][axis],
            )
        ]
        assert d["histogram"][axis] == summed


def test_boundary_values_land_in_expected_bins():
    agg = JudgeScoreAggregator()
    agg.add("k", _score(0.0, 0.0, 0.0))
    agg.add("k", _score(1.0, 1.0, 1.0))
    agg.add("k", _score(0.999, 0.999, 0.999))

    d = agg.to_dict()
    hist = d["histogram"]["fidelity"]
    assert hist[0] == 1  # 0.0 -> bin 0
    assert hist[9] == 2  # 1.0 and 0.999 -> bin 9
    assert sum(hist) == 3


def test_means_are_rounded_to_four_decimals():
    agg = JudgeScoreAggregator()
    agg.add("k", _score(1 / 3, 1 / 3, 1 / 3))
    agg.add("k", _score(2 / 3, 2 / 3, 2 / 3))

    d = agg.to_dict()
    for axis in ("fidelity", "naturalness", "utility", "weighted"):
        value = d["mean"][axis]
        assert value == round(value, 4)


def test_len_tracks_total_scores_added():
    agg = JudgeScoreAggregator()
    assert len(agg) == 0
    agg.add("a", _score(0.1, 0.1, 0.1))
    assert len(agg) == 1
    agg.add("b", _score(0.2, 0.2, 0.2))
    assert len(agg) == 2


def test_empty_string_key_buckets_under_unknown():
    agg = JudgeScoreAggregator()
    agg.add("", _score(0.5, 0.5, 0.5))

    d = agg.to_dict()
    assert set(d["by_key"].keys()) == {"__unknown__"}
    assert d["by_key"]["__unknown__"]["count"] == 1


def test_to_dict_survives_json_roundtrip():
    agg = JudgeScoreAggregator()
    agg.add("billing", _score(0.8, 0.7, 0.75))
    agg.add("", _score(0.2, 0.3, 0.4))
    agg.add("shipping", _score(1.0, 0.0, 0.5))

    d = agg.to_dict()
    dumped = json.dumps(d)
    reloaded = json.loads(dumped)
    assert reloaded == d
