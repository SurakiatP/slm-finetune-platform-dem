"""Guards for docker/prometheus/alerts.yml (round 4 / item 8, "alerting rules").

Why this exists: this deployment ships alert *rules* only — no Alertmanager,
no `alerting:` block (see alerts.yml's own header for why: no notification
channel exists to route to). That makes the rules file itself the only
thing standing between "the six required alerts exist and are correct" and
a silent gap, so it gets the same enumerated-over-parsed-structure treatment
tests/unit/test_prometheus_stack.py uses for prometheus.yml/docker-compose.yml
— explicit non-vacuity asserts first, then set equality (not "at least
these"), never a whole-file substring match.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALERTS_PATH = _REPO_ROOT / "docker" / "prometheus" / "alerts.yml"
_PROM_CONFIG_PATH = _REPO_ROOT / "docker" / "prometheus" / "prometheus.yml"
_COMPOSE_PATH = _REPO_ROOT / "docker-compose.yml"

_CONTAINER_ALERTS_PATH = "/etc/prometheus/alerts.yml"

# Expected shape of each of the six required alerts. `for_` is the expected
# `for:` duration, or None if this alert must NOT carry a `for:` at all
# (RepeatedTaskFailures/GpuOom/CostAnomaly are instant thresholds on an
# increase() window, not a sustained-condition alert — the task spec gives
# no `for:` for these three).
_EXPECTED_ALERTS: dict[str, dict[str, object]] = {
    "WorkerDown": {
        "for": "2m",
        "expr": 'slm_worker_up == 0',
    },
    "QueueBackedUp": {
        "for": "15m",
        "expr": "slm_queue_depth > 10",
    },
    "DependencyDown": {
        "for": "2m",
        "expr": "slm_dependency_up == 0",
    },
    "RepeatedTaskFailures": {
        "for": None,
        "expr": 'increase(slm_jobs{outcome="failed"}[30m]) >= 3',
    },
    "GpuOom": {
        "for": None,
        "expr": 'increase(slm_job_failures{error_type="oom"}[1h]) >= 1',
    },
    "CostAnomaly": {
        "for": None,
        "expr": "sum(increase(slm_openrouter_cost_usd_total[1h])) > 5",
    },
}


def _alerts_config() -> dict:
    assert _ALERTS_PATH.exists(), "docker/prometheus/alerts.yml does not exist"
    config = yaml.safe_load(_ALERTS_PATH.read_text(encoding="utf-8"))
    assert isinstance(config, dict), "alerts.yml did not parse to a mapping"
    return config


def _rules_by_name() -> dict[str, dict]:
    config = _alerts_config()
    groups = config.get("groups") or []
    assert groups, "alerts.yml has no groups at all"
    assert len(groups) == 1, f"expected exactly one rule group, found {len(groups)}"

    rules = groups[0].get("rules") or []
    assert rules, "the alerts.yml rule group has no rules at all"

    by_name: dict[str, dict] = {}
    for rule in rules:
        name = rule.get("alert")
        assert name, f"a rule has no 'alert' name: {rule!r}"
        assert name not in by_name, f"duplicate alert name: {name!r}"
        by_name[name] = rule
    return by_name


# ---------------------------------------------------------------------------
# groups/rules structure + enumerated alert-name equality
# ---------------------------------------------------------------------------


def test_alerts_file_has_exactly_one_group_named_slm_platform_alerts() -> None:
    config = _alerts_config()
    groups = config["groups"]
    assert groups[0].get("name") == "slm-platform-alerts", (
        f"expected the single rule group to be named 'slm-platform-alerts', "
        f"got {groups[0].get('name')!r}"
    )


def test_alert_names_are_enumerated_exactly() -> None:
    by_name = _rules_by_name()
    # Enumerated equality, not "at least these" — an extra copy-pasted rule
    # or a typo'd duplicate would otherwise sail through a subset check.
    assert set(by_name) == set(_EXPECTED_ALERTS), (
        f"alerts.yml must define exactly these six alerts "
        f"{sorted(_EXPECTED_ALERTS)}, found {sorted(by_name)}"
    )


# ---------------------------------------------------------------------------
# per-alert: for: duration + expr threshold literal
# ---------------------------------------------------------------------------


def test_each_alert_has_the_expected_for_duration_and_expr() -> None:
    by_name = _rules_by_name()
    for name, expected in _EXPECTED_ALERTS.items():
        rule = by_name[name]

        expected_for = expected["for"]
        if expected_for is None:
            assert "for" not in rule, (
                f"{name} carries a 'for:' duration ({rule.get('for')!r}) but "
                "the task spec gives it none (instant threshold on an "
                "increase() window, not a sustained condition)"
            )
        else:
            assert rule.get("for") == expected_for, (
                f"{name}'s for: is {rule.get('for')!r}, expected {expected_for!r}"
            )

        actual_expr = (rule.get("expr") or "").strip()
        assert actual_expr == expected["expr"], (
            f"{name}'s expr is {actual_expr!r}, expected {expected['expr']!r}"
        )


def test_cost_anomaly_expr_is_wrapped_in_sum() -> None:
    """The sum() is required — without it CostAnomaly evaluates per
    (model, stage, outcome) series ("one model/stage burned $5/h"), not as a
    platform-wide anomaly. Dropping it is a silent semantic change, not a
    syntax error, so this gets its own guard rather than relying on the
    exact-expr-string check above to catch a future "simplification"."""
    by_name = _rules_by_name()
    expr = by_name["CostAnomaly"]["expr"].strip()
    assert expr.startswith("sum("), (
        f"CostAnomaly's expr must be wrapped in sum(...), got {expr!r}"
    )


# ---------------------------------------------------------------------------
# every rule: severity label + summary/description annotations
# ---------------------------------------------------------------------------


def test_every_rule_has_a_severity_label_and_both_annotations() -> None:
    by_name = _rules_by_name()
    assert by_name, "no rules to check — _rules_by_name returned nothing"
    for name, rule in by_name.items():
        labels = rule.get("labels") or {}
        assert labels.get("severity"), f"{name} has no 'severity' label"

        annotations = rule.get("annotations") or {}
        assert annotations.get("summary"), f"{name} has no 'summary' annotation"
        assert annotations.get("description"), (
            f"{name} has no 'description' annotation"
        )
        assert "docs/runbooks/metrics.md" in annotations["description"], (
            f"{name}'s description does not point at docs/runbooks/metrics.md"
        )


# ---------------------------------------------------------------------------
# wiring: prometheus.yml rule_files + docker-compose.yml mount
# ---------------------------------------------------------------------------


def test_rule_files_names_exactly_the_compose_mount_container_path() -> None:
    assert _PROM_CONFIG_PATH.exists(), "docker/prometheus/prometheus.yml does not exist"
    prom_config = yaml.safe_load(_PROM_CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(prom_config, dict), "prometheus.yml did not parse to a mapping"

    rule_files = prom_config.get("rule_files") or []
    assert rule_files, "prometheus.yml has no rule_files at all"
    assert rule_files == [_CONTAINER_ALERTS_PATH], (
        f"prometheus.yml's rule_files is {rule_files!r}, expected exactly "
        f"[{_CONTAINER_ALERTS_PATH!r}]"
    )

    assert _COMPOSE_PATH.exists(), "docker-compose.yml does not exist"
    compose = yaml.safe_load(_COMPOSE_PATH.read_text(encoding="utf-8"))
    prometheus = compose["services"]["prometheus"]
    mounts = [str(v) for v in (prometheus.get("volumes") or [])]

    alert_mounts = [
        m for m in mounts if m.split(":")[0] == "./docker/prometheus/alerts.yml"
    ]
    assert alert_mounts, (
        "the prometheus service does not mount ./docker/prometheus/alerts.yml at all"
    )
    expected_mount_suffix = f"{_CONTAINER_ALERTS_PATH}:ro"
    assert any(m.endswith(expected_mount_suffix) for m in alert_mounts), (
        f"docker/prometheus/alerts.yml is not mounted read-only at "
        f"{_CONTAINER_ALERTS_PATH} — got {alert_mounts!r}"
    )

    # No Alertmanager service and no alerting: block — the settled decision
    # this task is built on (rules only; no notification channel to route
    # to). A future PR silently reintroducing either would defeat the point.
    assert "alertmanager" not in compose["services"], (
        "an 'alertmanager' service was added to docker-compose.yml — this "
        "deployment deliberately ships rules only, no Alertmanager"
    )
    assert "alerting" not in prom_config, (
        "prometheus.yml has an 'alerting:' block — this deployment "
        "deliberately ships rules only, no Alertmanager to point one at"
    )

    assert _ALERTS_PATH.exists(), (
        "the mount's host-side file docker/prometheus/alerts.yml does not exist on disk"
    )
