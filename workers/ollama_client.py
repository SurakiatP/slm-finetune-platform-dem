"""Sync Ollama HTTP client used by worker tasks.

We talk to the daemon over its native API (port 11434):
  • `POST /api/blobs/sha256:<HASH>` — upload a GGUF body as a content-addressable blob
  • `POST /api/create` — register a new model from a previously-uploaded blob
  • `POST /api/pull`   — pull a base model (used for first-run base availability)
  • `GET  /api/tags`   — list registered models
  • `POST /api/show`   — inspect one model
  • `DELETE /api/delete` — remove a model

The async equivalent for inference traffic lives in `api/services/inference_service.py`
and uses httpx.AsyncClient. We keep this one sync because Celery tasks are sync.
"""

from __future__ import annotations

import hashlib
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
                )
            )
        return out

    def upload_blob(self, file_path: str) -> str:
        """Upload a file to Ollama as a content-addressable blob.

        Streams the file body to ``POST /api/blobs/sha256:<HEX>``. Returns
        the full digest string (e.g. ``sha256:abcd1234…``) suitable for use
        as a value in the ``files`` dict on ``/api/create``. Idempotent:
        if the daemon already has the blob it answers 200, otherwise 201;
        both are accepted.
        """
        digest_hex = _file_sha256(file_path)
        digest = f"sha256:{digest_hex}"
        with open(file_path, "rb") as fh:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(
                    f"{self._base}/api/blobs/{digest}",
                    content=fh,
                    headers={"Content-Type": "application/octet-stream"},
                )
        if 200 <= resp.status_code < 300:
            return digest
        detail = resp.text[:500] if resp.text else f"status={resp.status_code}"
        raise OllamaError(f"ollama upload_blob failed: {detail}")

    def create_from_blob(
        self,
        *,
        tag: str,
        digest: str,
        parameters: dict[str, Any] | None = None,
        system: str | None = None,
        template: str | None = None,
    ) -> None:
        """Register a new model under ``tag`` from a previously-uploaded blob.

        ``digest`` must be the ``sha256:<HEX>`` string returned by
        :meth:`upload_blob`. Replaces the legacy ``modelfile`` string flow,
        which Ollama removed around 0.5.x.
        """
        body: dict[str, Any] = {
            "model": tag,
            "files": {"model.gguf": digest},
            "stream": False,
        }
        if parameters:
            body["parameters"] = parameters
        if system:
            body["system"] = system
        if template:
            body["template"] = template
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(f"{self._base}/api/create", json=body)
        _raise_if_error(resp, "create")


# ---- helpers ---------------------------------------------------------------


def _raise_if_error(resp: httpx.Response, op: str) -> None:
    if 200 <= resp.status_code < 300:
        return
    detail = resp.text[:500] if resp.text else f"status={resp.status_code}"
    raise OllamaError(f"ollama {op} failed: {detail}")


def _file_sha256(file_path: str) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


__all__ = [
    "OllamaClient",
    "OllamaError",
    "OllamaModelInfo",
]
