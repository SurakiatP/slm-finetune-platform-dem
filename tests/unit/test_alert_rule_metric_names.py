"""Metric-name guard for docker/prometheus/alerts.yml (W3-T6 "alert-metric-
name-guard").

WHY THIS EXISTS: an alert whose `expr` names a metric that
`api/core/metrics.py` never actually exports does not error — Prometheus
just evaluates that series selector to an empty vector, forever. The rule
looks alive in `/api/v1/rules`, it never fires, and it never surfaces as
broken. This repo has already shipped that exact "healthy stack, nothing
actually works" shape twice (round 3's cloudflared that could never start;
round 2's dead `record_success()`), so this file's only job is to make that
specific failure mode loud instead of silent, for every rule in
docker/prometheus/alerts.yml.

APPROACH: parse alerts.yml with `yaml`, pull every rule's `expr`, extract
the identifier-shaped tokens that are plausibly metric names (i.e. not a
PromQL function/operator keyword, not a label name from inside a `{...}`
matcher, not a duration literal or bare number), and assert each survivor is
a name `api.core.metrics._REGISTRY` actually has registered.
`api.services.metrics_export` is imported first, purely for its
registration side effect, following the same approach
`tests/unit/test_metrics_label_cardinality.py` already uses for enumerating
the live registry (see that file's `_collectors()` for the precedent).

House convention: enumerated over parsed/introspected structure, explicit
non-vacuity asserts ahead of every enumeration this file relies on, and (the
sharper version of that same worry, specific to this file) a NEGATIVE
CONTROL that proves the extractor+checker pair can actually report a
missing metric — without it, an extractor that silently degraded to
matching nothing would make every assertion below pass vacuously forever,
which is the precise failure mode this guard exists to catch, so the guard
must not be able to have it itself.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from api.core import metrics as metrics_core

# Imported for its registration side effect only (any collector it wires that
# isn't already declared directly in api/core/metrics.py) — never referenced
# by name below. Mirrors tests/unit/test_metrics_label_cardinality.py.
from api.services import metrics_export  # noqa: F401

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALERTS_PATH = _REPO_ROOT / "docker" / "prometheus" / "alerts.yml"

_MIN_EXPECTED_ALERTS = 6

# PromQL functions/operators/keywords that can appear as bare identifiers in
# an `expr` but are never a metric name. Deliberately wider than exactly
# what the six shipped alerts use today (increase, sum), so this stays
# correct if a future alert's expr grows a new function this file hasn't
# seen yet.
_PROMQL_KEYWORDS = {
    # aggregation operators
    "sum", "avg", "max", "min", "count", "stddev", "stdvar",
    "topk", "bottomk", "quantile", "count_values", "group",
    # aggregation modifiers / binary-op modifiers / joins
    "by", "without", "on", "ignoring", "group_left", "group_right",
    "offset", "bool", "unless", "and", "or",
    # range/instant vector functions
    "increase", "rate", "irate", "delta", "idelta", "resets",
    "abs", "absent", "absent_over_time", "ceil", "floor", "round",
    "clamp", "clamp_max", "clamp_min", "exp", "ln", "log2", "log10", "sqrt",
    "sort", "sort_desc", "label_replace", "label_join",
    "vector", "scalar", "time", "timestamp", "histogram_quantile",
    "predict_linear", "deriv", "holt_winters", "changes",
    "day_of_month", "day_of_week", "days_in_month", "hour", "minute",
    "month", "year",
    "min_over_time", "max_over_time", "avg_over_time", "sum_over_time",
    "count_over_time", "quantile_over_time", "stddev_over_time",
    "stdvar_over_time", "last_over_time", "present_over_time",
}

# Non-slm_-prefixed metric names that are legitimately allowed to appear in
# an alert expr (e.g. Prometheus's own built-in `up`, or a node-exporter
# metric) — kept empty deliberately. None of the six shipped alerts
# reference anything outside the slm_ namespace; if one ever legitimately
# needs to (e.g. `up{job="..."}`), name it here explicitly rather than
# loosening the prefix check itself.
_ALLOWED_NON_SLM_PREFIXED_METRICS: frozenset[str] = frozenset()

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def extract_metric_names(expr: str) -> set[str]:
    """Extract the plausible metric-name tokens out of one PromQL `expr`.

    Strips `{...}` label matchers wholesale first (which removes both the
    label *names* and any quoted label *values* in one step — a quoted
    string value like `"failed"` would otherwise itself look like a bare
    identifier token once the surrounding quotes are gone), then strips
    duration/number literals (`30m`, `1h`, `10`, `3`) — these need an
    explicit pass rather than just relying on the token regex's
    leading-letter requirement, because a naive identifier scan over
    `30m` finds no match *starting* at `3`, but still matches `m` on its
    own starting one character in. Only after both strips does it tokenize
    what's left on identifier shape and drop known PromQL function/
    operator keywords.
    """

    without_matchers = re.sub(r"\{[^}]*\}", "", expr)
    # `\b\d` anchors on a token that *starts* with a digit at a word
    # boundary — true metric/function identifiers never start with a digit
    # (see `_TOKEN_RE`), so anything matching here is a duration literal
    # (`30m`, `1h`) or a bare number (`10`, `3`), never a real name. The
    # boundary requirement also protects a function name like `log2` from
    # having its trailing digit stripped out from under it, since the `2`
    # in `log2` isn't preceded by a word boundary.
    without_durations_or_numbers = re.sub(r"\b\d+(?:\.\d+)?[A-Za-z]*\b", "", without_matchers)
    tokens = _TOKEN_RE.findall(without_durations_or_numbers)
    return {token for token in tokens if token not in _PROMQL_KEYWORDS}


def _missing_metric_names(names: set[str], registered: set[str]) -> set[str]:
    """The subset of `names` that `registered` does not contain."""

    return {name for name in names if name not in registered}


def _alerts_config() -> dict:
    assert _ALERTS_PATH.exists(), "docker/prometheus/alerts.yml does not exist"
    config = yaml.safe_load(_ALERTS_PATH.read_text(encoding="utf-8"))
    assert isinstance(config, dict), "alerts.yml did not parse to a mapping"
    return config


def _alert_exprs() -> dict[str, str]:
    """Map alert name -> its `expr` string, for every rule in the one group."""

    config = _alerts_config()
    groups = config.get("groups") or []
    assert groups, "alerts.yml has no groups at all"

    exprs: dict[str, str] = {}
    for group in groups:
        for rule in group.get("rules") or []:
            name = rule.get("alert")
            expr = rule.get("expr")
            assert name, f"a rule has no 'alert' name: {rule!r}"
            assert expr, f"{name} has no 'expr'"
            assert name not in exprs, f"duplicate alert name: {name!r}"
            exprs[name] = expr

    # Non-vacuity #1: enough alerts actually got parsed for the checks below
    # to mean anything — an empty or near-empty file would otherwise let
    # every subsequent assertion pass by iterating over nothing.
    assert len(exprs) >= _MIN_EXPECTED_ALERTS, (
        f"expected at least {_MIN_EXPECTED_ALERTS} alert rules in "
        f"{_ALERTS_PATH}, found {len(exprs)}: {sorted(exprs)}"
    )
    return exprs


def _registered_metric_names() -> set[str]:
    names = set(metrics_core._REGISTRY._names_to_collectors.keys())
    # Non-vacuity backstop: an empty registry would make the "is this
    # extracted name registered" check below pass or fail meaninglessly —
    # see test_metrics_label_cardinality.py's identical backstop on
    # `_collectors()` for the established precedent.
    assert names, (
        "api.core.metrics._REGISTRY has no registered metric names after "
        "importing both api.core.metrics and api.services.metrics_export"
    )
    return names


def _extracted_by_alert() -> dict[str, set[str]]:
    exprs = _alert_exprs()
    extracted = {name: extract_metric_names(expr) for name, expr in exprs.items()}

    # Non-vacuity #2: every alert's expr must yield at least one candidate
    # metric name — an alert whose expr the extractor reduced to nothing
    # would silently skip the one check this file exists to do for it.
    for name, names in extracted.items():
        assert names, (
            f"{name}'s expr {exprs[name]!r} yielded zero extracted metric "
            "name candidates — the extractor is broken, or this alert's "
            "expr no longer references any metric at all"
        )

    return extracted


# ---------------------------------------------------------------------------
# the guard itself
# ---------------------------------------------------------------------------


def test_every_alert_expr_names_only_registered_metrics() -> None:
    extracted = _extracted_by_alert()
    registered = _registered_metric_names()

    # Non-vacuity #3: the overall extracted set (across every alert) must be
    # non-empty — a belt-and-braces restatement of non-vacuity #2 at the
    # aggregate level, so a bug that only zeroes out under aggregation
    # (rather than per-alert) can't slip through either.
    all_extracted = set().union(*extracted.values())
    assert all_extracted, "extracted zero metric names across all alerts combined"

    for alert_name, names in extracted.items():
        missing = _missing_metric_names(names, registered)
        assert not missing, (
            f"{alert_name}'s expr references metric name(s) {sorted(missing)} "
            "that api.core.metrics._REGISTRY has no matching collector for — "
            "this alert would silently evaluate to an empty vector forever "
            "and never fire, the exact 'healthy stack, nothing actually "
            "works' shape this guard exists to catch"
        )


def test_every_extracted_metric_name_has_the_slm_prefix() -> None:
    extracted = _extracted_by_alert()
    for alert_name, names in extracted.items():
        offending = {
            name
            for name in names
            if not name.startswith("slm_") and name not in _ALLOWED_NON_SLM_PREFIXED_METRICS
        }
        assert not offending, (
            f"{alert_name}'s expr references non-slm_-prefixed name(s) "
            f"{sorted(offending)} — likely a raw upstream/node-exporter "
            "metric rather than one this platform exports; if this is "
            "legitimate, add it by name to "
            "_ALLOWED_NON_SLM_PREFIXED_METRICS rather than loosening this "
            "check"
        )


# ---------------------------------------------------------------------------
# negative control — the guard must be able to fail
# ---------------------------------------------------------------------------


def test_extractor_and_checker_report_a_synthetic_missing_metric() -> None:
    """Run a synthetic expr naming a metric that will never be registered
    through the SAME extractor + checker the real assertion above uses, and
    confirm it IS reported as missing.

    Without this, an extractor that silently degraded to matching nothing
    (a bad regex edit, an over-eager keyword list swallowing real names,
    ...) would make test_every_alert_expr_names_only_registered_metrics
    pass vacuously forever — the exact silent-gap shape this whole file
    exists to prevent, reproduced inside its own guard. This test exists so
    that failure mode is caught here first.
    """

    synthetic_expr = 'increase(slm_does_not_exist{queue="gpu"}[5m]) > 0'
    extracted = extract_metric_names(synthetic_expr)
    assert extracted == {"slm_does_not_exist"}, (
        f"extractor produced {sorted(extracted)} for a controlled synthetic "
        "expr — expected exactly {'slm_does_not_exist'}; the extractor "
        "itself has drifted from what this test assumes about it"
    )

    registered = _registered_metric_names()
    missing = _missing_metric_names(extracted, registered)
    assert missing == {"slm_does_not_exist"}, (
        "the missing-metric checker failed to report a metric name that "
        "cannot possibly be registered — the guard this file provides "
        "would be unable to ever fail, which defeats its entire purpose"
    )
