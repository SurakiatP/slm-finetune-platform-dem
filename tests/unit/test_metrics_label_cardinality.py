"""Cardinality guard for M9 "cardinality-guard-and-suite-reconciliation".

`test_metrics_registry.py` (M1 "metrics-core") already carries one in-module
check that every registered collector's labels are a subset of the allowed,
non-tenant-scoped set. This file is a second, independent guard that goes
further in three ways ADR-013 Decision 3 promises but the original test does
not yet enforce:

  1. It imports `api.services.metrics_export` as well as `api.core.metrics`
     before enumerating, so a collector that only gets pulled in through the
     export-adapter import path is still on the registry at enumeration time.
  2. It asserts an explicit forbidden-name blocklist (project, project_id,
     tenant, actor, actor_id, user, user_id, job_id, dataset_id) rather than
     only the allowed-set subset check — a belt-and-braces second phrasing of
     the same rule, so a typo'd allowed-set edit can't quietly let a
     known-bad name back in.
  3. It cross-checks docs/adr/ADR-013-metrics-prometheus.md's own
     allowed-label prose block against the set this test enforces, so the
     ADR and the guard cannot silently drift apart — and it checks a live
     `render()` exposition body, not just collector objects, since the
     collector-object label declarations and what a scraper actually
     receives are, in principle, two different things to get wrong.

House convention: enumerated over parsed/introspected structure, not
substring-matched against a blob of text, with an explicit non-vacuity
assertion ahead of every enumeration this file relies on (see
tests/unit/test_mlflow_provisioning.py's module docstring for why — an
enumeration over nothing passes vacuously and hides the exact regression
this file exists to catch).
"""

from __future__ import annotations

import re
from pathlib import Path

from prometheus_client.parser import text_string_to_metric_families

from api.core import metrics as metrics_core

# Imported for its side effect only (registering any collector it wires that
# isn't already declared directly in api/core/metrics.py) — never referenced
# by name below. The `noqa` marks that as deliberate, not dead code.
from api.services import metrics_export  # noqa: F401

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ADR_PATH = _REPO_ROOT / "docs" / "adr" / "ADR-013-metrics-prometheus.md"

# Mirrors ADR-013 Decision 3's fenced block verbatim (cross-checked against
# the live file itself in test_adr_allowed_label_block_matches_the_enforced_set
# below, so this literal and the ADR's prose cannot drift apart unnoticed).
_ALLOWED_LABELS = {
    "route",
    "method",
    "status_class",
    "queue",
    "type",
    "stage",
    "model",
    "outcome",
}

# Per-tenant/per-entity names that must never appear on any metric, per
# ADR-013 Decision 3's cardinality rationale — a label that grows with the
# number of projects/jobs/users ever created makes every scrape bigger
# forever, and (for project/tenant/actor/user) would put tenant-scoped data
# on an unauthenticated surface (Decision 3 and Decision 4).
_FORBIDDEN_LABELS = {
    "project",
    "project_id",
    "tenant",
    "actor",
    "actor_id",
    "user",
    "user_id",
    "job_id",
    "dataset_id",
}


def _collectors() -> list:
    """Enumerate every collector on the private registry.

    `_REGISTRY._collector_to_names` is the same attribute
    test_metrics_registry.py's own subset check already uses — there is no
    public prometheus_client API that lists *declared* collectors without
    also requiring at least one observed sample per collector (see
    `_touch_every_collector` below for why that distinction matters for the
    rendered-output check), so this stays consistent with that existing
    house precedent rather than inventing a second enumeration mechanism.
    """
    collectors = list(metrics_core._REGISTRY._collector_to_names.keys())
    # Non-vacuity backstop: an empty registry would make every assertion
    # below pass trivially and hide the exact failure mode (metrics.py or
    # metrics_export.py failing to register anything) this file exists to
    # catch.
    assert collectors, (
        "api.core.metrics._REGISTRY has no registered collectors after "
        "importing both api.core.metrics and api.services.metrics_export — "
        "the cardinality guard below would pass vacuously"
    )
    return collectors


# ---- 1. every collector's declared labels are a subset of the allowed set --


def test_every_collector_label_set_is_a_subset_of_the_allowed_set() -> None:
    collectors = _collectors()
    for collector in collectors:
        label_names = set(collector._labelnames)
        assert label_names <= _ALLOWED_LABELS, (
            f"{collector} declares label(s) {sorted(label_names - _ALLOWED_LABELS)} "
            f"outside the allowed, non-tenant-scoped set {sorted(_ALLOWED_LABELS)} "
            "(ADR-013 Decision 3)"
        )


# ---- 2. forbidden per-tenant/per-entity names appear on NO collector -------


def test_no_collector_uses_a_forbidden_label_name() -> None:
    collectors = _collectors()
    for collector in collectors:
        label_names = set(collector._labelnames)
        offending = label_names & _FORBIDDEN_LABELS
        assert not offending, (
            f"{collector} declares forbidden label(s) {sorted(offending)} — no "
            "metric may carry project/project_id/tenant/actor/actor_id/user/"
            "user_id/job_id/dataset_id (ADR-013 Decision 3's cardinality and "
            "tenancy-leak rationale)"
        )


# ---- 3. the ADR's own allowed-label block matches the set this test enforces


def _adr_allowed_label_block() -> str:
    text = _ADR_PATH.read_text(encoding="utf-8")
    # ADR-013 Decision 3 spells the allowed set as a single fenced block:
    #     ```
    #     route, method, status_class, queue, type, stage, model, outcome
    #     ```
    # Matched by content (contains "route" and "outcome"), not by position,
    # so this survives the ADR gaining other fenced blocks elsewhere.
    blocks = re.findall(r"```\n(.*?)\n```", text, re.S)
    candidates = [b for b in blocks if "route" in b and "outcome" in b]
    # Non-vacuity backstop: if the ADR's block ever gets reformatted out of
    # a fenced code span (e.g. into a bare paragraph), this must fail loudly
    # rather than silently comparing against nothing and passing.
    assert candidates, (
        f"no fenced code block containing the allowed-label list was found in "
        f"{_ADR_PATH.name} — the ADR cross-check would otherwise compare "
        "against an absent block and pass regardless of what the ADR says"
    )
    assert len(candidates) == 1, (
        f"expected exactly one allowed-label fenced block in {_ADR_PATH.name}, "
        f"found {len(candidates)}: {candidates!r}"
    )
    return candidates[0]


def test_adr_allowed_label_block_matches_the_enforced_set() -> None:
    adr_labels = {label.strip() for label in _adr_allowed_label_block().split(",")}
    assert adr_labels == _ALLOWED_LABELS, (
        f"docs/adr/ADR-013-metrics-prometheus.md's allowed-label block "
        f"{sorted(adr_labels)} no longer matches the set this test enforces "
        f"{sorted(_ALLOWED_LABELS)} — the ADR and the code guard have drifted; "
        "a change to the allowed label set must update both together, never "
        "just one"
    )


# ---- 4. a live render() body never carries a forbidden label key ----------


def _touch_every_collector() -> None:
    """Force at least one sample per labelled collector.

    `generate_latest()` only serializes a sample line for a labelled family
    once `.labels(...)` has been called at least once on it — an untouched
    labelled Counter/Gauge/Histogram renders nothing at all. Without this,
    a metric whose only observation call site is deep in metrics_export.py
    (never exercised by this file) could carry a forbidden label and this
    test would never see a sample line to catch it on.
    """
    for collector in _collectors():
        label_names = list(collector._labelnames)
        if not label_names:
            continue
        collector.labels(**{name: "cardinality-guard-probe" for name in label_names})


def test_forbidden_label_names_never_appear_in_rendered_output() -> None:
    _touch_every_collector()
    body, _content_type = metrics_core.render()
    text = body.decode("utf-8")

    families = list(text_string_to_metric_families(text))
    # Non-vacuity backstop: an empty render() would make the loop below
    # iterate zero times and pass without checking anything.
    assert families, "metrics_core.render() produced no metric families"

    total_samples = sum(len(family.samples) for family in families)
    assert total_samples > 0, (
        "metrics_core.render() produced metric families but zero samples — "
        "_touch_every_collector() should have forced at least one sample per "
        "labelled collector plus the pre-existing unlabelled gauges"
    )

    for family in families:
        for sample in family.samples:
            offending = set(sample.labels.keys()) & _FORBIDDEN_LABELS
            assert not offending, (
                f"rendered sample {sample!r} in metric family {family.name!r} "
                f"carries forbidden label key(s) {sorted(offending)}"
            )
