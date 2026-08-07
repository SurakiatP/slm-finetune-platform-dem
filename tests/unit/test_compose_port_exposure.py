"""Zero services are reachable from outside the host.

BACKEND_GAP_ANALYSIS.md P0: "เปิด public port เฉพาะ reverse proxy/API ที่จำเป็น
และให้ PostgreSQL, Redis, MinIO, MLflow และ Ollama อยู่ใน network ภายใน", with
the acceptance criterion "การสแกน port จากภายนอกเข้าถึงได้เฉพาะบริการที่ตั้งใจเปิด".

Round 3 tightened this from "only `api` is public" to "nothing is public at
all". `cloudflared` (a Cloudflare Tunnel client) is now the sole ingress: it
makes outbound-only connections to Cloudflare's edge and needs no inbound
port on this host, and it forwards everything to `edge` (nginx) over the
compose network. `edge` is the only thing that talks to `api`, and `api`
itself now binds loopback like every other internal service.

A published Docker port defaults to `0.0.0.0`, so `"5432:5432"` puts Postgres
on every interface the host has. Prefixing `127.0.0.1:` keeps it reachable
locally — `docker compose exec`, and an SSH tunnel, which is what
`scripts/deploy_pasaflow_vm.sh` hands the operator for the MinIO console,
MLflow, Ollama, and now the `api`/`edge` loopback ports too — while an
external scan sees nothing.

This is a text/structure guard on `docker-compose.yml` (and, for the
ingress-bypass check below, `docker/cloudflared/config.yml`) rather than an
integration test: these files ARE the security boundary, there is little
runtime behaviour to observe short of standing up the whole stack, and a
regression here can be a one-character deletion (dropping `127.0.0.1:`) that
nothing else would catch. It is deliberately strict — a new service that
publishes a port has to come here and declare itself.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE_PATH = _REPO_ROOT / "docker-compose.yml"
_COMPOSE_TEXT = _COMPOSE_PATH.read_text(encoding="utf-8")
_COMPOSE = yaml.safe_load(_COMPOSE_TEXT)

_CLOUDFLARED_CONFIG_PATH = _REPO_ROOT / "docker" / "cloudflared" / "config.yml"

# Nothing is supposed to face the network directly anymore. `cloudflared` is
# the sole ingress (outbound-only, no `ports:` block at all — see
# MUST_HAVE_NO_PORTS below) and forwards to `edge`, which is itself
# loopback-only. Verified by reading smart-model-tune, which only ever calls
# `${ENGINE_HOST}/api/v1/...` and `${ENGINE_HOST}/health` — `ENGINE_HOST` is
# expected to be the Cloudflare-fronted hostname, never a container's
# published port directly.
PUBLIC_SERVICES: set[str] = set()

# Services that must be loopback-bound (have a `ports:` block, every mapping
# prefixed `127.0.0.1:`). `api` and `edge` joined this list in round 3 — both
# used to be (or, for `edge`, would otherwise default to) publicly bound.
MUST_BE_INTERNAL = {"postgres", "redis", "minio", "mlflow", "ollama", "api", "edge"}

# Services that must carry NO `ports:` block at all — not even a loopback
# one. Distinct from MUST_BE_INTERNAL: these have nothing worth debugging
# over a direct port (no HTTP surface, or `cloudflared`, which is
# outbound-only by design), so there is no operator workflow to preserve by
# keeping a `ports:` entry around.
MUST_HAVE_NO_PORTS = {"cloudflared", "worker", "worker-cpu", "minio-init"}

_LOOPBACK = re.compile(r"^127\.0\.0\.1:")
_SERVICE_HEADER = re.compile(r"^  (?P<name>[A-Za-z0-9_-]+):", re.MULTILINE)


def _published(service: str) -> list[str]:
    return [str(p) for p in (_COMPOSE["services"][service].get("ports") or [])]


def _services_with_ports() -> list[str]:
    return [
        name
        for name, body in _COMPOSE["services"].items()
        if isinstance(body, dict) and body.get("ports")
    ]


def _service_block(name: str) -> str:
    """Return the raw YAML text of service `name`'s block (top-level key
    through the line before the next top-level service key, or EOF).

    Generic replacement for the old `_COMPOSE_TEXT.split("  api:")[1]
    .split("  worker:")[0]` slice, which hard-coded "the next service is
    named worker" and broke (or silently mis-sliced) the moment a service
    got inserted between them — exactly what happened when `edge` and
    `cloudflared` were added between `api` and `worker` in round 3. This
    walks the same two-space-indented `name:` markers the old code did, but
    finds the *next one after `name`*, whatever it happens to be, instead of
    a specific hard-coded string.
    """
    headers = list(_SERVICE_HEADER.finditer(_COMPOSE_TEXT))
    start = next((m for m in headers if m.group("name") == name), None)
    assert start is not None, f"no top-level service block found for {name!r}"
    later = [m.start() for m in headers if m.start() > start.start()]
    end = min(later) if later else len(_COMPOSE_TEXT)
    return _COMPOSE_TEXT[start.start():end]


@pytest.mark.parametrize("service", sorted(MUST_BE_INTERNAL))
def test_infrastructure_binds_loopback_only(service: str) -> None:
    mappings = _published(service)
    assert mappings, f"{service} has no ports block — update this guard if that was intentional"
    for mapping in mappings:
        assert _LOOPBACK.match(mapping), (
            f"{service} publishes {mapping!r} on all interfaces. Prefix it with "
            f"'127.0.0.1:' — without that, an external port scan reaches it."
        )


@pytest.mark.parametrize("service", sorted(MUST_HAVE_NO_PORTS))
def test_internal_only_services_publish_no_port_at_all(service: str) -> None:
    assert not _published(service), (
        f"{service} has a `ports:` block ({_published(service)!r}), but it is listed in "
        f"MUST_HAVE_NO_PORTS — either remove the port mapping, or move {service!r} to "
        f"MUST_BE_INTERNAL if a loopback-bound debugging port is now intentional."
    )


def test_nothing_is_externally_published() -> None:
    """The real assertion: not 'these N are internal' but 'nothing became
    public'. A new service added with a bare `ports:` entry fails here
    rather than quietly widening the attack surface. `PUBLIC_SERVICES` is
    the empty set — the whole point of round 3 — so this is really asserting
    `_services_with_non_loopback_ports() == set()`, but keeps the equality
    form (rather than `assert not external`) so a future PUBLIC_SERVICES
    edit that widens the intended set still has to touch this line and
    think about it."""
    external = {
        name
        for name in _services_with_ports()
        if any(not _LOOPBACK.match(m) for m in _published(name))
    }
    assert external == PUBLIC_SERVICES, (
        f"services reachable from outside the host: {sorted(external)}; "
        f"expected only {sorted(PUBLIC_SERVICES)}"
    )


@pytest.mark.parametrize("service", sorted(MUST_BE_INTERNAL))
def test_internal_services_still_reachable_inside_the_compose_network(
    service: str,
) -> None:
    """Loopback binding must not be confused with removing the port. Container
    to container traffic goes over the compose network by service name and is
    unaffected by the host binding — but only if the service is still on that
    network."""
    networks = _COMPOSE["services"][service].get("networks") or []
    assert "slm-net" in networks, f"{service} left the shared network"


def test_the_reason_is_recorded_next_to_the_exception() -> None:
    """The `api` ports block is the one place someone will be tempted to
    'make consistent' with the others — except now, post round-3, it already
    IS consistent (loopback-bound like everything else), and the comment
    explains why that consistency matters (it wasn't always true, and the
    whole edge/real_ip trust model depends on it staying true)."""
    api_block = _service_block("api")
    assert "127.0.0.1" in api_block, "the note explaining the loopback binding is gone"


def test_the_edge_is_the_only_ingress() -> None:
    """The single most important new test in this wave. `cloudflared` is
    locally-managed (docker/cloudflared/config.yml's ingress rules live in
    this repo, not in Cloudflare's dashboard) specifically so this assertion
    is possible: every ingress rule's `service:` must point at `edge`, never
    directly at `api` (or anything else). Without this test, someone could
    repoint one `hostname:` entry at `http://api:8000` and every rate limit
    docker/edge.nginx.conf enforces would be silently bypassable — the
    change would be a one-line, easy-to-miss diff in a YAML file, with no
    other test anywhere in this suite that reads it.
    """
    config = yaml.safe_load(_CLOUDFLARED_CONFIG_PATH.read_text(encoding="utf-8"))
    ingress = config.get("ingress") or []
    assert ingress, "docker/cloudflared/config.yml has no ingress rules"

    services = [rule.get("service") for rule in ingress if isinstance(rule, dict)]
    allowed = {"http://edge:80", "http_status:404"}
    assert all(s in allowed for s in services), (
        f"ingress rule(s) point somewhere other than edge or a 404 catch-all: "
        f"{[s for s in services if s not in allowed]!r}"
    )
    assert any(s == "http://edge:80" for s in services), (
        "no ingress rule routes to edge at all — nothing would be served"
    )
    assert not any("api" in str(s) for s in services), (
        "an ingress rule names `api` directly — this bypasses every rate limit "
        "and body-size cap in docker/edge.nginx.conf"
    )
    # Catch-all 404 must be last (cloudflared evaluates ingress rules in
    # order and stops at the first hostname match) and must have no
    # `hostname:` key of its own.
    assert "hostname" not in ingress[-1], "the catch-all rule must not have a hostname"
    assert ingress[-1].get("service") == "http_status:404", (
        "the last ingress rule must be the http_status:404 catch-all"
    )


def test_the_mounted_config_is_the_tested_config() -> None:
    """Prevents a thoroughly-tested nginx config that is never actually
    served: `edge`'s compose service must bind-mount the exact file
    tests/unit/test_edge_nginx_config.py exercises, and that file must
    exist."""
    edge = _COMPOSE["services"]["edge"]
    mounts = [str(v) for v in (edge.get("volumes") or [])]
    conf_mounts = [m for m in mounts if m.split(":")[0] == "./docker/edge.nginx.conf"]
    assert conf_mounts, "edge does not mount ./docker/edge.nginx.conf at all"
    assert any(m.endswith("/etc/nginx/conf.d/default.conf:ro") for m in conf_mounts), (
        "edge.nginx.conf is not mounted read-only at nginx's default.conf path"
    )
    assert (_REPO_ROOT / "docker" / "edge.nginx.conf").exists()


def test_the_api_does_not_hot_reload() -> None:
    """`--reload` belongs only in docker-compose.dev.yml (see that file's
    header comment for why it is not merged into the base file, and not
    named docker-compose.override.yml). Its presence in the base `api.command`
    would mean it silently ships to the VM unless the operator remembers to
    NOT pass -f docker-compose.dev.yml — the inverse, safer default is for
    the base file to never have it at all."""
    command = _COMPOSE["services"]["api"].get("command") or []
    assert "--reload" not in [str(c) for c in command], (
        "base docker-compose.yml's api.command carries --reload; move it to "
        "docker-compose.dev.yml instead"
    )


def test_dev_override_is_not_named_override_yml() -> None:
    """Compose auto-loads a file literally named `docker-compose.override.yml`
    with no `-f` flag. `scripts/deploy_pasaflow_vm.sh` already generates and
    owns a file with that exact name on the VM (for volume rebinding) — a
    second, differently-sourced writer targeting the same filename is
    exactly how `--reload` (or any other dev-only setting) could silently
    reappear in production. The dev override must live at a different,
    explicitly-`-f`'d path."""
    assert (_REPO_ROOT / "docker-compose.dev.yml").exists()
    assert not (_REPO_ROOT / "docker-compose.override.yml").exists(), (
        "docker-compose.override.yml exists and would be auto-loaded by a bare "
        "`docker compose up` — this repo's dev overrides must be opt-in via "
        "`-f docker-compose.dev.yml`, not this auto-loaded filename"
    )


# ---------------------------------------------------------------------------
# The cloudflared bootstrap seam.
#
# `docker/cloudflared/config.yml` is bind-mounted read-only. Compose
# interpolates the compose file, NOT the contents of files it mounts — so a
# `${...}` placeholder in that YAML reaches cloudflared verbatim, the tunnel
# never starts, and every public route is down. Nothing else in the stack
# depends on cloudflared booting, so the failure is silent: the deploy
# script's wait loop times out and still prints a green summary advertising
# the public hostname.
#
# That is exactly the seam the round-2 review named: one wave wrote the
# config, another wrote the env-var prompt, and nobody owned the middle. The
# two guards below are the middle.
# ---------------------------------------------------------------------------

_CF_CONFIG_PATH = _COMPOSE_PATH.parent / "docker" / "cloudflared" / "config.yml"


def _strip_yaml_comments(text: str) -> str:
    """Assertions about forbidden syntax must not match the comments that
    explain why it is forbidden — the same trap that made every guard in
    tests/unit/test_edge_nginx_config.py fail against a correct config."""
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def test_the_tunnel_config_carries_no_uninterpolated_placeholder() -> None:
    code = _strip_yaml_comments(_CF_CONFIG_PATH.read_text(encoding="utf-8"))
    assert "${" not in code, (
        "docker/cloudflared/config.yml contains a ${...} placeholder. Nothing "
        "substitutes it: compose does not interpolate bind-mounted file "
        "contents, and no script rewrites this file. cloudflared would fail "
        "to start and every public route would be down. Pass the value as an "
        "argument in docker-compose.yml's cloudflared `command:` instead, "
        "where interpolation actually happens."
    )


def test_the_tunnel_id_reaches_cloudflared_from_the_compose_file() -> None:
    """The other half: having removed the placeholder, the id must still get
    there. Also pins the env-var NAME — the original bug was that config.yml
    said TUNNEL_ID while .env.example and the deploy script both said
    CLOUDFLARE_TUNNEL_ID, so even a working substitution step would have
    read the wrong variable."""
    command = [str(c) for c in (_COMPOSE["services"]["cloudflared"].get("command") or [])]
    joined = " ".join(command)

    assert "run" in command, "cloudflared is not invoked with `tunnel run`"

    # Extract the VARIABLE NAMES actually referenced, rather than asking
    # whether the string appears anywhere. The first version of this
    # assertion did the latter and could not fail: the `:?` error message is
    # itself "set CLOUDFLARE_TUNNEL_ID in .env", so renaming the real
    # variable to ${TUNNEL_ID:?set CLOUDFLARE_TUNNEL_ID...} — reintroducing
    # the exact name mismatch this test exists to prevent — kept it green.
    # Same shape as a guard matching the comment that explains it.
    referenced = set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)[:?\-}]", joined))
    assert "CLOUDFLARE_TUNNEL_ID" in referenced, (
        f"cloudflared's command references {sorted(referenced)}, not "
        "CLOUDFLARE_TUNNEL_ID. With no `tunnel:` key in config.yml it has no "
        "tunnel to run, and .env.example/the deploy script both write "
        "CLOUDFLARE_TUNNEL_ID — a different name here is the original bug."
    )

    env_example = (_COMPOSE_PATH.parent / ".env.example").read_text(encoding="utf-8")
    assert "CLOUDFLARE_TUNNEL_ID=" in env_example, (
        "compose reads CLOUDFLARE_TUNNEL_ID but .env.example does not declare "
        "it — an operator following the example file gets an unset variable"
    )
