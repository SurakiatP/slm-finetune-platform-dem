"""Sync Ollama HTTP client used by worker tasks.

We talk to the daemon over its native API (port 11434):
  • `POST /api/create` — register a new model from a Modelfile + GGUF blob
  • `POST /api/pull`    — pull a base model (used for first-run base availability)
  • `GET  /api/tags`    — list registered models
  • `POST /api/show`    — inspect one model
  • `DELETE /api/delete` — remove a model

The async equivalent for inference traffic lives in `api/services/inference_service.py`
and uses httpx.AsyncClient. We keep this one sync because Celery tasks are sync.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OllamaModelInfo:
    name: str
    digest: str
    size_bytes: int
    modified_at: str


class OllamaError(RuntimeError):
    """Raised when the Ollama daemon returns a non-2xx or invalid payload."""


class OllamaClient:
    """Thin sync wrapper around the Ollama HTTP API."""

    def __init__(self, base_url: str, *, timeout: float = 600.0) -> None:
        # Ollama imports can take a while (≥1 min for cold pulls), so default
        # timeout is generous.
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    # -- Lifecycle helpers -----------------------------------------------------

    def health(self) -> bool:
        """True iff the daemon is reachable. Used as a precondition guard."""
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(f"{self._base}/api/tags")
                return resp.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    # -- Models ----------------------------------------------------------------

    def list_models(self) -> list[OllamaModelInfo]:
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(f"{self._base}/api/tags")
        _raise_if_error(resp, "list_models")
        out: list[OllamaModelInfo] = []
        for entry in resp.json().get("models", []):
            out.append(
                OllamaModelInfo(
                    name=entry["name"],
                    digest=entry.get("digest", ""),
                    size_bytes=int(entry.get("size", 0)),
                    modified_at=entry.get("modified_at", ""),
                )
            )
        return out

    def has_model(self, tag: str) -> bool:
        return any(m.name == tag for m in self.list_models())

    def create_from_modelfile(
        self,
        *,
        tag: str,
        modelfile: str,
    ) -> None:
        """Register a new model under `tag` using the given Modelfile content.

        The Modelfile must reference paths the daemon can read locally (e.g.,
        a GGUF file we've placed on a shared volume between the worker and
        the ollama service).
        """
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(
                f"{self._base}/api/create",
                json={"name": tag, "modelfile": modelfile, "stream": False},
            )
        _raise_if_error(resp, "create")

    def delete_model(self, tag: str) -> None:
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.request(
                "DELETE",
                f"{self._base}/api/delete",
                json={"name": tag},
            )
        if resp.status_code == 404:
            return  # idempotent
        _raise_if_error(resp, "delete")


# ---- helpers ---------------------------------------------------------------


def _raise_if_error(resp: httpx.Response, op: str) -> None:
    if 200 <= resp.status_code < 300:
        return
    detail = resp.text[:500] if resp.text else f"status={resp.status_code}"
    raise OllamaError(f"ollama {op} failed: {detail}")


def build_modelfile(
    *,
    base_gguf_path: str,
    template: str | None = None,
    system: str | None = None,
    parameter_lines: list[str] | None = None,
) -> str:
    """Build a Modelfile string for `POST /api/create`.

    Args:
        base_gguf_path: absolute path the daemon can read (GGUF on a shared
            volume — keep the worker container and ollama container's volume
            mounts aligned).
        template: optional prompt template (e.g. ChatML for tool_calling).
        system: optional default system prompt.
        parameter_lines: extra `PARAMETER ...` directives (`temperature 0.0`,
            `num_ctx 2048`, …).
    """
    lines: list[str] = [f'FROM "{base_gguf_path}"']
    if template:
        lines.append('TEMPLATE """')
        lines.append(template)
        lines.append('"""')
    if system:
        lines.append('SYSTEM """')
        lines.append(system)
        lines.append('"""')
    if parameter_lines:
        for p in parameter_lines:
            lines.append(f"PARAMETER {p}")
    return "\n".join(lines) + "\n"


__all__ = [
    "OllamaClient",
    "OllamaError",
    "OllamaModelInfo",
    "build_modelfile",
]
