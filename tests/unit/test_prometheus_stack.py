"""Guards for M4 observability-stack: `prometheus` + `gpu-exporter`.

Why this exists: the port-exposure guard (test_compose_port_exposure.py)
already enforces that these two services stay loopback-bound and on
`slm-net`, same as every other internal service. This file covers the parts
that guard does not: the *content* of docker/prometheus/prometheus.yml (the
file compose bind-mounts into the container, not compose itself), the
retention flag, the volume plumbing between docker-compose.yml and
scripts/deploy_pasaflow_vm.sh's generated override, and — the sharpest edge
in the M4 spec — that `gpu-exporter` (expected to stay down on the pasaflow
box until a host nvidia driver bug is fixed) never becomes something another
service's `depends_on` or the deploy script's healthy-wait count relies on.

Enumerated over parsed structure, not substring-matched against the whole
file — see test_compose_port_exposure.py's own history for why a whole-file
substring guard goes green the moment its author edits any one call site.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE_PATH = _REPO_ROOT / "docker-compose.yml"
_COMPOSE_TEXT = _COMPOSE_PATH.read_text(encoding="utf-8")
_COMPOSE = yaml.safe_load(_COMPOSE_TEXT)

_PROM_CONFIG_PATH = _REPO_ROOT / "docker" / "prometheus" / "prometheus.yml"
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "deploy_pasaflow_vm.sh"

# The one-shot job that never shows "running" (see docker-compose.yml's
# minio-init: `depends_on: service_completed_successfully`), and the service
# that is deliberately excluded from the deploy script's healthy-wait count
# because it is expected to stay down on the pasaflow box.
_ONE_SHOT_SERVICES = {"minio-init"}
_UNCOUNTED_SERVICES = {"gpu-exporter"}

# The profile a deploy actually enables — `scripts/deploy_pasaflow_vm.sh`
# passes `--profile tunnel` on every service-selecting invocation (enforced by
# tests/unit/test_compose_port_exposure.py).
_DEPLOY_PROFILE = "tunnel"


def _profile_gated_out(compose: dict) -> set[str]:
    """Services a `--profile tunnel` deploy never starts, derived from compose.

    A service with no `profiles:` always starts; one listing `tunnel` starts
    because the deploy enables that profile (this is `cloudflared`, which DOES
    count toward the healthy-wait). Anything gated behind a DIFFERENT profile
    is simply absent from the deploy's service set and must not be counted.

    Derived rather than hardcoded because `feat/web-ui` carries an extra
    `frontend-build` service (`profiles: ["build-spa"]`) that `dev` does not.
    A literal exclusion set would have to differ between the two branches —
    and backend test paths are required to stay byte-identical across them,
    so the difference has to live in logic that reads the compose file, not
    in a constant.
    """
    gated: set[str] = set()
    for name, spec in compose["services"].items():
        profiles = (spec or {}).get("profiles") or []
        if profiles and _DEPLOY_PROFILE not in profiles:
            gated.add(name)
    return gated


def _prometheus_config() -> dict:
    assert _PROM_CONFIG_PATH.exists(), "docker/prometheus/prometheus.yml does not exist"
    config = yaml.safe_load(_PROM_CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(config, dict), "prometheus.yml did not parse to a mapping"
    return config


def _script_lines() -> list[str]:
    return _SCRIPT_PATH.read_text(encoding="utf-8").splitlines()


# ---------------------------------------------------------------------------
# docker/prometheus/prometheus.yml content
# ---------------------------------------------------------------------------


def test_global_scrape_interval_is_30s() -> None:
    config = _prometheus_config()
    assert config.get("global", {}).get("scrape_interval") == "30s", (
        "global.scrape_interval must be exactly '30s' — the M4 spec value"
    )


def test_scrape_config_lists_exactly_the_two_expected_targets() -> None:
    config = _prometheus_config()
    jobs = config.get("scrape_configs") or []
    assert jobs, "prometheus.yml has no scrape_configs at all"

    targets_by_job: dict[str, list[str]] = {}
    for job in jobs:
        job_name = job.get("job_name")
        assert job_name, f"a scrape_config entry has no job_name: {job!r}"
        flat: list[str] = []
        for static_config in job.get("static_configs") or []:
            flat.extend(static_config.get("targets") or [])
        targets_by_job[job_name] = flat

    # Enumerated equality, not "at least these" — a stray third job (e.g. a
    # copy-pasted `prometheus` self-scrape) would otherwise sail through.
    assert len(jobs) == 2, (
        f"expected exactly 2 scrape jobs (api, gpu exporter), found "
        f"{len(jobs)}: {sorted(targets_by_job)}"
    )

    # Resolve each job by its target rather than assuming job_name spelling,
    # since the M4 spec only pins the targets, not the job_names.
    api_targets = None
    gpu_targets = None
    for job_name, flat in targets_by_job.items():
        if flat == ["api:8000"]:
            api_targets = flat
            api_job = next(j for j in jobs if j.get("job_name") == job_name)
        if flat == ["gpu-exporter:9835"]:
            gpu_targets = flat

    assert api_targets == ["api:8000"], (
        f"no scrape job targets exactly ['api:8000']; got {sorted(targets_by_job.items())}"
    )
    assert gpu_targets == ["gpu-exporter:9835"], (
        f"no scrape job targets exactly ['gpu-exporter:9835']; got "
        f"{sorted(targets_by_job.items())}"
    )
    assert api_job.get("metrics_path") == "/metrics", (
        "the api scrape job must set metrics_path: /metrics explicitly"
    )


# ---------------------------------------------------------------------------
# docker-compose.yml: prometheus service
# ---------------------------------------------------------------------------


def test_retention_flag_is_present_and_30d() -> None:
    command = [str(c) for c in (_COMPOSE["services"]["prometheus"].get("command") or [])]
    joined = " ".join(command)
    match = re.search(r"--storage\.tsdb\.retention\.time[= ]([^\s]+)", joined)
    assert match, (
        "prometheus's command has no --storage.tsdb.retention.time flag at all"
    )
    assert match.group(1) == "30d", (
        f"--storage.tsdb.retention.time is {match.group(1)!r}, expected '30d'"
    )
    assert "--config.file=/etc/prometheus/prometheus.yml" in command or any(
        c.startswith("--config.file=") for c in command
    ), "prometheus's command has no --config.file flag pointing at the mounted config"


def test_prometheus_mounts_the_tested_config_file() -> None:
    prometheus = _COMPOSE["services"]["prometheus"]
    mounts = [str(v) for v in (prometheus.get("volumes") or [])]
    config_mounts = [m for m in mounts if m.split(":")[0] == "./docker/prometheus/prometheus.yml"]
    assert config_mounts, "prometheus does not mount ./docker/prometheus/prometheus.yml at all"
    assert any(m.endswith("/etc/prometheus/prometheus.yml:ro") for m in config_mounts), (
        "prometheus.yml is not mounted read-only at /etc/prometheus/prometheus.yml"
    )
    assert _PROM_CONFIG_PATH.exists()


def test_prometheus_uses_the_named_data_volume() -> None:
    mounts = [str(v) for v in (_COMPOSE["services"]["prometheus"].get("volumes") or [])]
    assert any(m.split(":")[0] == "prometheus-data" for m in mounts), (
        "prometheus does not mount the prometheus-data named volume at /prometheus"
    )


# ---------------------------------------------------------------------------
# docker-compose.yml: gpu-exporter service — the "must never gate anything" guard
# ---------------------------------------------------------------------------


def test_gpu_exporter_uses_the_shared_gpu_deploy_anchor() -> None:
    """Same GPU-reservation shape as `worker`/`ollama` — verified by content
    equality against `ollama`'s `deploy:` block, since compose has already
    resolved the YAML anchor by the time `_COMPOSE` is parsed."""
    gpu_exporter = _COMPOSE["services"]["gpu-exporter"]
    ollama = _COMPOSE["services"]["ollama"]
    assert gpu_exporter.get("deploy") == ollama.get("deploy"), (
        "gpu-exporter's deploy block does not match ollama's *gpu-deploy anchor"
    )


def test_no_service_depends_on_the_gpu_exporter() -> None:
    offenders = []
    for name, body in _COMPOSE["services"].items():
        if not isinstance(body, dict):
            continue
        depends = body.get("depends_on")
        if not depends:
            continue
        names = depends if isinstance(depends, list) else list(depends.keys())
        if "gpu-exporter" in names:
            offenders.append(name)
    assert not offenders, (
        f"these services depends_on gpu-exporter: {offenders}; gpu-exporter is "
        "expected to stay down on the pasaflow prod box, so nothing may gate "
        "on it starting or being healthy"
    )


def test_gpu_exporter_itself_has_no_depends_on() -> None:
    assert not _COMPOSE["services"]["gpu-exporter"].get("depends_on"), (
        "gpu-exporter has a depends_on block — it should start independently "
        "of everything else, same as worker/ollama's GPU services do not "
        "block the rest of the stack"
    )


def test_the_gpu_exporter_down_reason_is_recorded_next_to_the_service() -> None:
    """Mirrors test_compose_port_exposure.py's
    test_the_reason_is_recorded_next_to_the_exception: the comment explaining
    why gpu-exporter is expected to be down must not get silently deleted."""
    # Comments live ABOVE the service key, so search backwards from it.
    idx = _COMPOSE_TEXT.index("  gpu-exporter:")
    preceding = _COMPOSE_TEXT[:idx]
    comment_block = preceding[preceding.rfind("\n\n") :]
    assert "nvidia" in comment_block.lower(), (
        "the comment explaining why gpu-exporter is expected to be down "
        "(host nvidia driver bug) is gone from directly above the service"
    )


# ---------------------------------------------------------------------------
# top-level volumes: docker-compose.yml AND the deploy script's override
# ---------------------------------------------------------------------------


def test_prometheus_data_volume_declared_at_top_level() -> None:
    assert "prometheus-data" in (_COMPOSE.get("volumes") or {}), (
        "prometheus-data is missing from docker-compose.yml's top-level volumes:"
    )


def _override_heredoc() -> str:
    lines = _script_lines()
    start_hits = [
        i for i, line in enumerate(lines) if 'docker-compose.override.yml" <<EOF' in line
    ]
    assert len(start_hits) == 1, (
        f"expected exactly one docker-compose.override.yml heredoc open, found {len(start_hits)}"
    )
    start = start_hits[0]
    end_hits = [i for i in range(start + 1, len(lines)) if lines[i].strip() == "EOF"]
    assert end_hits, "docker-compose.override.yml heredoc never closes with a bare EOF"
    end = end_hits[0]
    return "\n".join(lines[start + 1 : end])


def test_prometheus_data_bound_in_the_deploy_script_override_heredoc() -> None:
    heredoc = _override_heredoc()
    match = re.search(r"^  prometheus-data:\n((?:^ {4}.*\n?)*)", heredoc, re.M)
    assert match, (
        "prometheus-data has no volume block in scripts/deploy_pasaflow_vm.sh's "
        "docker-compose.override.yml heredoc"
    )
    block = match.group(1)
    assert "driver: local" in block, "prometheus-data override is missing driver: local"
    assert "type: none" in block, "prometheus-data override is missing driver_opts type: none"
    assert "o: bind" in block, "prometheus-data override is missing driver_opts o: bind"
    assert "device: $DATA_DIR/prometheus" in block, (
        "prometheus-data override does not bind device: $DATA_DIR/prometheus — "
        "same driver_opts shape as postgres-data/redis-data/etc above it"
    )


def test_prometheus_data_directory_created_in_phase_1() -> None:
    lines = _script_lines()
    hits = [i for i, line in enumerate(lines) if line.strip().startswith("for sub in ")]
    assert len(hits) == 1, "expected exactly one Phase 1 data-subdir loop"
    assert "prometheus" in lines[hits[0]], (
        "Phase 1's data-subdir loop does not create a prometheus/ subdirectory"
    )


# ---------------------------------------------------------------------------
# the healthy-wait count: must equal (compose services) - (one-shot) - (gpu-exporter)
# ---------------------------------------------------------------------------


def test_healthy_wait_count_matches_long_running_service_count() -> None:
    all_services = set(_COMPOSE["services"])
    gated_out = _profile_gated_out(_COMPOSE)
    expected = len(all_services - _ONE_SHOT_SERVICES - _UNCOUNTED_SERVICES - gated_out)

    lines = _script_lines()
    ge_hits = [i for i, line in enumerate(lines) if '"$UP" -ge' in line]
    assert len(ge_hits) == 1, (
        f"expected exactly one healthy-wait `-ge` comparison, found {len(ge_hits)}"
    )
    ge_match = re.search(r'-ge (\d+)', lines[ge_hits[0]])
    assert ge_match, "could not parse the numeric literal out of the -ge comparison"
    assert int(ge_match.group(1)) == expected, (
        f"deploy script waits for -ge {ge_match.group(1)} containers, but "
        f"{expected} compose services are long-running (total "
        f"{len(all_services)} minus one-shot {sorted(_ONE_SHOT_SERVICES)} minus "
        f"deliberately-uncounted {sorted(_UNCOUNTED_SERVICES)} minus "
        f"profile-gated-out {sorted(gated_out)}). Update the "
        "literal (and its paired ok()/N message) if a service was added or "
        "removed, or add it to _ONE_SHOT_SERVICES/_UNCOUNTED_SERVICES here if "
        "that's why it should not count."
    )

    # The printed "$UP/N" denominator must not drift from the -ge literal —
    # two numbers that are supposed to agree are two chances to edit only one.
    ok_hits = [i for i, line in enumerate(lines) if '"$UP/' in line]
    assert len(ok_hits) == 1, f"expected exactly one '$UP/N' summary line, found {len(ok_hits)}"
    ok_match = re.search(r'\$UP/(\d+)', lines[ok_hits[0]])
    assert ok_match, "could not parse the numeric literal out of the '$UP/N' summary"
    assert int(ok_match.group(1)) == expected, (
        f"the ok() summary prints $UP/{ok_match.group(1)} but the wait loop's "
        f"-ge literal is {ge_match.group(1)} — these must match"
    )
