"""Only the API may be reachable from outside the host.

BACKEND_GAP_ANALYSIS.md P0: "เปิด public port เฉพาะ reverse proxy/API ที่จำเป็น
และให้ PostgreSQL, Redis, MinIO, MLflow และ Ollama อยู่ใน network ภายใน", with
the acceptance criterion "การสแกน port จากภายนอกเข้าถึงได้เฉพาะบริการที่ตั้งใจเปิด".

A published Docker port defaults to `0.0.0.0`, so `"5432:5432"` puts Postgres
on every interface the host has. Prefixing `127.0.0.1:` keeps it reachable
locally — `docker compose exec`, and an SSH tunnel, which is what
`scripts/deploy_pasaflow_vm.sh` hands the operator for the MinIO console,
MLflow and Ollama — while an external scan sees nothing.

This is a text guard on `docker-compose.yml` rather than an integration test:
the file IS the security boundary, there is no runtime behaviour to observe,
and a regression here is a one-character deletion that nothing else would
catch. It is deliberately strict — a new service that publishes a port has to
come here and declare itself.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_COMPOSE_PATH = Path(__file__).resolve().parents[2] / "docker-compose.yml"
_COMPOSE_TEXT = _COMPOSE_PATH.read_text(encoding="utf-8")
_COMPOSE = yaml.safe_load(_COMPOSE_TEXT)

# The one service that is supposed to face the network. Everything else is an
# internal dependency the frontend never addresses directly — verified by
# reading smart-model-tune, which only ever calls `${ENGINE_HOST}/api/v1/...`
# and `${ENGINE_HOST}/health`.
PUBLIC_SERVICES = {"api"}

# Services the gap analysis names explicitly.
MUST_BE_INTERNAL = {"postgres", "redis", "minio", "mlflow", "ollama"}

_LOOPBACK = re.compile(r"^127\.0\.0\.1:")


def _published(service: str) -> list[str]:
    return [str(p) for p in (_COMPOSE["services"][service].get("ports") or [])]


def _services_with_ports() -> list[str]:
    return [
        name
        for name, body in _COMPOSE["services"].items()
        if isinstance(body, dict) and body.get("ports")
    ]


@pytest.mark.parametrize("service", sorted(MUST_BE_INTERNAL))
def test_infrastructure_binds_loopback_only(service: str) -> None:
    mappings = _published(service)
    assert mappings, f"{service} has no ports block — update this guard if that was intentional"
    for mapping in mappings:
        assert _LOOPBACK.match(mapping), (
            f"{service} publishes {mapping!r} on all interfaces. Prefix it with "
            f"'127.0.0.1:' — without that, an external port scan reaches it."
        )


def test_the_api_is_the_only_externally_published_service() -> None:
    """The real assertion: not 'these five are internal' but 'nothing else
    became public'. A new service added with a bare `ports:` entry fails here
    rather than quietly widening the attack surface."""
    external = {
        name
        for name in _services_with_ports()
        if any(not _LOOPBACK.match(m) for m in _published(name))
    }
    assert external == PUBLIC_SERVICES, (
        f"services reachable from outside the host: {sorted(external)}; "
        f"expected only {sorted(PUBLIC_SERVICES)}"
    )


def test_the_api_stays_published() -> None:
    """The mirror image: binding the API to loopback too would make the whole
    deployment unreachable, and the failure would look like a network problem
    rather than a config one."""
    mappings = _published("api")
    assert mappings and all(not _LOOPBACK.match(m) for m in mappings)


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
    'make consistent' with the others. The comment explaining why it differs
    has to survive next to it."""
    api_block = _COMPOSE_TEXT.split("  api:")[1].split("  worker:")[0]
    assert "127.0.0.1" in api_block, "the note explaining the exception is gone"
