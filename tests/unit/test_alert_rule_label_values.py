"""Label-VALUE guard for docker/prometheus/alerts.yml (round 4 / item 8,
"alert-label-value-guard" — the second half of the silent-never-fires
failure mode).

`test_alert_rules.py` already pins the six required alerts' shape (name,
`for:`, `expr:` string). A sibling guard (`test_alert_rule_metric_names.py`)
checks that every metric NAME an alert's `expr` references is one this
platform actually exports. Neither of those catches the other way an alert
rule can be silently dead on arrival: `slm_jobs{outcome="FAILED"}` names a
real metric with a real label, but if the platform only ever emits the
value `"failed"` (lowercase — see `api.schemas.enums.JobStatus`), that
matcher selects zero series, the alert can never fire, and nothing errors
anywhere — Prometheus happily evaluates an expression that matches nothing
forever. `docker/prometheus/alerts.yml` filters on `outcome="failed"` and
`error_type="oom"` today; this file is what makes sure those two literal
values (and any future ones on `queue`/`dependency`) are values the
platform can actually produce, not just plausible-looking strings.

Deliberately a SEPARATE, independent parser from
`test_alert_rule_metric_names.py`, not a shared imported helper. Two
independent parsers means a bug in one can't silently take both guards
down together — see that file's own module docstring for the same
reasoning stated from its side.

House convention (see `test_alert_rules.py` and
`test_metrics_label_cardinality.py`): enumerated over parsed structure,
never a whole-file substring match, with an explicit non-vacuity assertion
ahead of every enumeration/comparison this file relies on — an empty
collection on either side of a subset/membership check makes that check
pass vacuously and hides the exact regression this file exists to catch.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

# Side-effect import: registers every collector wired through the export
# adapter (not just the ones declared directly in api/core/metrics.py) onto
# the private registry before it is enumerated below — same reasoning, and
# same import order, as tests/unit/test_metrics_label_cardinality.py.
from api.core import metrics as metrics_core
from api.schemas.enums import JobStatus
from api.services import metrics_export  # noqa: F401
from api.services import readiness
from api.services.metrics_sources import ERROR_TYPES

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALERTS_PATH = _REPO_ROOT / "docker" / "prometheus" / "alerts.yml"

# The 10-name allowed, non-tenant-scoped label set (mirrors ADR-013 Decision 3
# / test_metrics_label_cardinality.py's `_ALLOWED_LABELS`). A label matcher
# using a name outside this set is wrong regardless of what value it carries.
_ALLOWED_LABEL_NAMES = {
    "route",
    "method",
    "status_class",
    "queue",
    "type",
    "stage",
    "model",
    "outcome",
    "dependency",
    "error_type",
}

# metric_name{...} — the metric name immediately followed by a brace block.
# Applied only to each rule's `expr` string (never the whole YAML file, which
# also contains `{{ $labels.queue }}`-style Go-template refs inside
# `annotations.description` — those are not PromQL matchers at all and must
# never be mistaken for one).
_METRIC_BLOCK_RE = re.compile(r"([a-zA-Z_:][a-zA-Z0-9_:]*)\s*\{([^{}]*)\}")

# name="value" — literal string-equality matchers only. Deliberately does
# NOT match !=, =~, !~ (each has a non-word character directly before the
# "=" that this pattern requires to immediately follow the label name, so
# e.g. "outcome!=\"x\"" cannot match here) — those are negations/regexes,
# not a concrete emitted value, and are out of scope for a "can the platform
# actually emit this literal" check.
_MATCHER_PAIR_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="([^"]*)"')


def _alerts_config() -> dict:
    assert _ALERTS_PATH.exists(), "docker/prometheus/alerts.yml does not exist"
    config = yaml.safe_load(_ALERTS_PATH.read_text(encoding="utf-8"))
    assert isinstance(config, dict), "alerts.yml did not parse to a mapping"
    return config


def _rule_exprs() -> list[tuple[str, str]]:
    """[(alert_name, expr_string), ...] for every rule in every group."""
    config = _alerts_config()
    groups = config.get("groups") or []
    assert groups, "alerts.yml has no groups at all"

    exprs: list[tuple[str, str]] = []
    for group in groups:
        for rule in group.get("rules") or []:
            name = rule.get("alert", "<unnamed>")
            expr = rule.get("expr")
            assert isinstance(expr, str) and expr.strip(), (
                f"rule {name!r} has no non-empty 'expr' string"
            )
            exprs.append((name, expr))

    assert exprs, "alerts.yml has no rules with an 'expr' at all"
    return exprs


def _label_matchers() -> list[tuple[str, str, str, str]]:
    """[(alert_name, metric_name, label_name, label_value), ...] for every
    literal label matcher found in every rule's `expr` (own parser — see
    module docstring for why this is not shared with
    test_alert_rule_metric_names.py)."""
    matchers: list[tuple[str, str, str, str]] = []
    for alert_name, expr in _rule_exprs():
        for metric_name, block in _METRIC_BLOCK_RE.findall(expr):
            for label_name, value in _MATCHER_PAIR_RE.findall(block):
                matchers.append((alert_name, metric_name, label_name, value))

    # Non-vacuity backstop: if the parser regressed (e.g. a brace-syntax
    # change in alerts.yml it no longer understands) and silently found zero
    # matchers, every check below would iterate zero times and pass without
    # actually checking anything — exactly the silent-never-fires shape this
    # whole file exists to prevent, just relocated into the test itself.
    assert matchers, (
        "found zero label matchers in docker/prometheus/alerts.yml's rule "
        "expressions — either the file lost its outcome=/error_type= "
        "matchers, or this parser regressed; either way the checks below "
        "would pass vacuously"
    )
    return matchers


def _collector_label_names_by_metric_name() -> dict[str, set[str]]:
    """metric name (including every exposition-time suffix, e.g. Counters'
    `_total`/`_created`) -> the set of label names its collector declares.

    Reads `_collector_to_names` off `api.core.metrics._REGISTRY` the same
    way `test_metrics_label_cardinality.py` does, so a collector's *declared*
    labels (not merely whatever label keys happen to appear in an already-
    rendered sample) are what's compared against.
    """
    mapping: dict[str, set[str]] = {}
    collectors = list(metrics_core._REGISTRY._collector_to_names.items())
    assert collectors, (
        "api.core.metrics._REGISTRY has no registered collectors after "
        "importing both api.core.metrics and api.services.metrics_export — "
        "the label-declaration check below would have nothing to compare "
        "against and pass vacuously"
    )
    for collector, names in collectors:
        label_names = set(collector._labelnames)
        for name in names:
            mapping[name] = label_names
    return mapping


# ---------------------------------------------------------------------------
# non-vacuity of every source collection this file compares matchers against
# ---------------------------------------------------------------------------


def test_source_collections_are_non_empty() -> None:
    """An empty source set would make every subset/membership check below
    pass vacuously (every value is trivially "in" an empty-set-derived
    always-false check, or worse, an empty allowed-set bug could flip a
    check to always pass) — assert each one actually has members before any
    test relies on it."""
    assert _ALLOWED_LABEL_NAMES, "the allowed label-name set must not be empty"
    assert ERROR_TYPES, "api.services.metrics_sources.ERROR_TYPES must not be empty"
    assert {s.value for s in JobStatus}, "api.schemas.enums.JobStatus must not be empty"
    assert readiness.WORKER_QUEUES, "api.services.readiness.WORKER_QUEUES must not be empty"
    assert set(readiness._PROBES), "api.services.readiness._PROBES must not be empty"


def test_matcher_extraction_is_non_vacuous() -> None:
    matchers = _label_matchers()
    assert len(matchers) > 0


# ---------------------------------------------------------------------------
# (a) every label NAME is in the allowed set AND declared on that metric's
#     collector
# ---------------------------------------------------------------------------


def test_every_label_name_is_allowed_and_declared_on_its_metric() -> None:
    matchers = _label_matchers()
    collector_labels = _collector_label_names_by_metric_name()

    for alert_name, metric_name, label_name, value in matchers:
        assert label_name in _ALLOWED_LABEL_NAMES, (
            f"alert {alert_name!r}'s matcher on {metric_name!r} uses label "
            f"name {label_name!r}, which is outside the allowed, "
            f"non-tenant-scoped set {sorted(_ALLOWED_LABEL_NAMES)} (ADR-013 "
            "Decision 3)"
        )
        assert metric_name in collector_labels, (
            f"alert {alert_name!r}'s expr references metric {metric_name!r}, "
            "which is not exposed by any collector on api.core.metrics."
            "_REGISTRY — this alert can never match any series"
        )
        declared = collector_labels[metric_name]
        assert label_name in declared, (
            f"alert {alert_name!r}'s matcher {label_name}={value!r} on "
            f"{metric_name!r} uses a label the collector does not declare "
            f"(declared labels: {sorted(declared)}) — this matcher can "
            "never match any series exported for this metric, so the alert "
            "silently never fires"
        )


# ---------------------------------------------------------------------------
# (b) every literal label VALUE is one the platform can actually emit
# ---------------------------------------------------------------------------


def test_every_label_value_is_one_the_platform_can_actually_emit() -> None:
    matchers = _label_matchers()

    # `job_counts()` (api/services/metrics_sources.py) zero-fills and stores
    # keys as `status.value` (e.g. "failed", not "FAILED") — `.value` strings
    # are therefore the source of truth for what `outcome` can ever equal on
    # `slm_jobs`, confirmed by reading that function directly rather than
    # assumed.
    value_sources: dict[str, set[str]] = {
        "error_type": set(ERROR_TYPES),
        "outcome": {status.value for status in JobStatus},
        "queue": set(readiness.WORKER_QUEUES),
        "dependency": set(readiness._PROBES),
    }
    for label_name, source in value_sources.items():
        assert source, f"value source for label {label_name!r} must not be empty"

    checked_any = False
    for alert_name, metric_name, label_name, value in matchers:
        source = value_sources.get(label_name)
        if source is None:
            # route/method/status_class/type/stage/model have no fixed,
            # closed enum this file can validate a literal against — only
            # the four labels above (error_type, outcome, queue, dependency)
            # are backed by one. Not an error; simply out of scope for the
            # value check (the name/declaration check above still applies).
            continue
        checked_any = True
        assert value in source, (
            f"alert {alert_name!r}'s matcher {label_name}={value!r} on "
            f"{metric_name!r} is not a value the platform can ever emit for "
            f"{label_name!r} (allowed: {sorted(source)}) — this matcher "
            "selects zero series, so the alert can never fire and nothing "
            "errors to say so"
        )

    # Non-vacuity: if every matcher happened to use a label outside
    # value_sources (e.g. alerts.yml only ever grew route/method/type
    # matchers), the loop above would never execute its assert and this
    # test would pass having checked nothing.
    assert checked_any, (
        "no matcher on error_type/outcome/queue/dependency was found in "
        "alerts.yml — the label-value guard would pass vacuously"
    )
