"""Application service for `/api/v1/inference/*`.

Ollama exposes its own OpenAI-compatible router under `/v1/...`. Our endpoints
forward the validated body, swap the `model` field if the caller passed a
ModelArtifact UUID instead of an Ollama tag, and return Ollama's response.

We deliberately do NOT support streaming (`stream=true`) in this PoC — adding
SSE proxying is straightforward but out of scope per Phase 7 requirements.

**Ownership decision.** Everything here is scoped by the `slm/` tag
namespace, which `workers/tasks/model_export.py::_compute_ollama_tag` owns:
every artifact this platform exports is registered as
`slm/<first-8-of-uuid>`, so a tag with that prefix always reverse-maps to a
`ModelArtifact` and therefore always has an owner. Anything else on the
daemon — a base model someone pulled directly, `llama3.2:3b` and friends —
maps to no row of ours and has no owner to check against.

So:

* `chat_completions` / `text_completions` ownership-check `body.model` when
  it is a ModelArtifact UUID **and** when it is a literal `slm/` tag
  (`_resolve_model_tag` -> `ownership.assert_model_access`). Running
  inference against someone else's private fine-tune is the same class of
  attack as reading or exporting it.
* `list_models` filters `slm/` tags to the caller's own artifacts.
* Base-model tags stay listed and callable for everyone. Hiding them would
  make the picker lie about what the daemon can actually serve, and they
  leak nothing — they are not derived from anyone's data.

**This was previously the opposite**, and the earlier reasoning is worth
recording because it was wrong in an instructive way: the listing was left
unfiltered on the grounds that a bare tag is "a capability from Ollama's own
namespace, not one of ours", and that dropping unresolvable tags would make
the listing inconsistent. But the two halves interacted — the unfiltered
listing handed every caller the exact `slm/<8hex>` strings that
`_resolve_model_tag` then accepted as literals without any check. Either
half alone looks defensible; together they were a working cross-tenant read.
Filtering the listing without also closing the literal path would have been
theatre, since the tags are short enough to guess and were being published
anyway.

`user is None` is a complete no-op throughout, matching the phase-1 rule in
`api/services/ownership.py` — anonymous callers see and can call exactly
what they could before auth existed.
"""

from __future__ import annotations

import logging
from uuid import UUID

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core import request_context
from api.core.auth import CurrentUser
from api.core.config import get_settings
from api.models.model_artifact import ModelArtifact
from api.models.training_job import TrainingJob
from api.schemas.inference import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    CompletionRequest,
    CompletionResponse,
    ModelDescriptor,
    ModelDescriptorList,
)
from api.services import audit_service, ownership

log = logging.getLogger(__name__)

# Ollama's OpenAI-compat router lives under /v1; native API under /api.
_OLLAMA_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=10.0)

# The tag namespace this platform owns. `workers/tasks/model_export.py`'s
# `_compute_ollama_tag` registers every exported artifact as
# `slm/<first-8-chars-of-uuid>`, so a tag with this prefix always maps back
# to a `ModelArtifact` row and therefore always has an owner. Anything else
# on the daemon (a base model someone pulled directly, e.g. `llama3.2:3b`)
# does not.
OUR_TAG_PREFIX = "slm/"


def canonical_our_tag(tag: str) -> str:
    """Strip Ollama's version suffix from a tag in our own namespace.

    `workers/tasks/model_export.py` creates models as `slm/<first-8-of-uuid>`
    and stores exactly that in `ModelArtifact.ollama_model_tag`. The daemon,
    however, reports the same model back as `slm/<8hex>:latest` — it appends
    the implicit version to every untagged create. Comparing the two forms
    directly is a silent mismatch that only shows up against a real daemon:
    the owner's own fine-tune gets filtered out of `GET /inference/models` as
    "not yours", and if the id were listed verbatim, posting it back would
    404 on the reverse-map. Both were observed on the vast.ai box.

    Base-model tags (`llama3.2:1b`) are returned untouched — there the part
    after the colon is a real parameter-size variant, not a version, and
    dropping it would conflate `llama3.2:1b` with `llama3.2:3b`.
    """
    if not tag.startswith(OUR_TAG_PREFIX):
        return tag
    return tag.split(":", 1)[0]


async def chat_completions(
    db: AsyncSession,
    body: ChatCompletionRequest,
    user: CurrentUser | None = None,
) -> ChatCompletionResponse:
    if body.stream:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="streaming is not supported on /api/v1/inference (set stream=false)",
        )
    tag = await _resolve_model_tag(db, body.model, user)
    payload = body.model_dump(exclude_none=True)
    payload["model"] = tag

    try:
        raw = await _post_json("/v1/chat/completions", payload)
    except Exception as exc:
        # Recorded, committed, then re-raised — a failed call is exactly the
        # kind of event the trail must not lose.
        await _audit_call(
            db,
            action="inference.chat_completions",
            tag=tag,
            outcome="failure",
            detail={"error_type": type(exc).__name__},
        )
        raise
    await _audit_call(db, action="inference.chat_completions", tag=tag, outcome="success")
    return ChatCompletionResponse.model_validate(raw)


async def text_completions(
    db: AsyncSession,
    body: CompletionRequest,
    user: CurrentUser | None = None,
) -> CompletionResponse:
    if body.stream:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="streaming is not supported on /api/v1/inference (set stream=false)",
        )
    tag = await _resolve_model_tag(db, body.model, user)
    payload = body.model_dump(exclude_none=True)
    payload["model"] = tag

    try:
        raw = await _post_json("/v1/completions", payload)
    except Exception as exc:
        await _audit_call(
            db,
            action="inference.completions",
            tag=tag,
            outcome="failure",
            detail={"error_type": type(exc).__name__},
        )
        raise
    await _audit_call(db, action="inference.completions", tag=tag, outcome="success")
    return CompletionResponse.model_validate(raw)


async def list_models(
    db: AsyncSession, user: CurrentUser | None = None
) -> ModelDescriptorList:
    """List models known to the local Ollama daemon (OpenAI shape).

    Tags in our own namespace (`slm/…`) are filtered to the caller's own
    artifacts; everything else the daemon knows about — base models pulled
    straight in — stays visible to everyone, because those carry no ownership
    information and hiding them would only make the picker lie about what the
    daemon can actually serve.

    `user is None` returns the unfiltered list, identical to pre-auth
    behaviour (phase-1 rule, `api/services/ownership.py`).
    """
    raw = await _get_json("/v1/models")
    entries = raw.get("data") or []

    visible: set[str] | None = None
    if user is not None:
        stmt = ownership.scope_models_to_owner(
            select(ModelArtifact.ollama_model_tag).where(
                ModelArtifact.ollama_model_tag.is_not(None)
            ),
            user,
        )
        visible = set((await db.execute(stmt)).scalars().all())

    items = []
    for entry in entries:
        # The daemon reports our models as `slm/<8hex>:latest`; the DB holds
        # `slm/<8hex>`. Compare — and publish — the canonical form, so the id
        # in this listing is the same string the call path accepts.
        tag = canonical_our_tag(entry["id"])
        if visible is not None and tag.startswith(OUR_TAG_PREFIX) and tag not in visible:
            continue
        items.append(
            ModelDescriptor(
                id=tag,
                created=int(entry.get("created", 0)),
                owned_by=entry.get("owned_by", "slm-platform"),
                metadata=None,
            )
        )
    return ModelDescriptorList(data=items)



async def _audit_call(
    db: AsyncSession, *, action: str, tag: str, outcome: str, detail: dict | None = None
) -> None:
    """Record one inference call.

    Inference is a read, so there is no mutation to ride along with and this
    commits on its own. It is audited anyway because it is the surface where
    cross-tenant access was actually reachable (see the module docstring):
    "who ran what against whose model" is the question that would be asked
    first if that ever happened again.
    """
    artifact_id = None
    project_id = None
    if tag.startswith(OUR_TAG_PREFIX):
        # Canonical form, because an anonymous caller's tag is passed through
        # un-normalised — without this the row would lose its artifact link
        # for exactly the callers phase 1 still allows.
        artifact = (
            await db.execute(
                select(ModelArtifact).where(
                    ModelArtifact.ollama_model_tag == canonical_our_tag(tag)
                )
            )
        ).scalar_one_or_none()
        if artifact is not None:
            artifact_id = str(artifact.id)
            training_job = await db.get(TrainingJob, artifact.training_job_id)
            project_id = training_job.project_id if training_job is not None else None
    audit_service.record(
        db,
        action=action,
        resource_type="model",
        resource_id=artifact_id or tag,
        project_id=project_id,
        outcome=outcome,
        actor_id=request_context.current_user_id(),
        request_id=request_context.current_request_id(),
        metadata={"tag": tag, **(detail or {})},
    )
    await db.commit()


# ---- helpers ---------------------------------------------------------------


async def _resolve_model_tag(
    db: AsyncSession, identifier: str, user: CurrentUser | None = None
) -> str:
    """Accept either a UUID (ModelArtifact id) or a literal Ollama tag.

    Three cases:

    * **UUID** → look up `ollama_model_tag` on the artifact (must be exported
      first), ownership-checked exactly like `GET /models/{id}`.
    * **Literal tag in our namespace** (`slm/…`) → reverse-map it to the
      `ModelArtifact` it names and run the same ownership check. Skipping this
      was a real hole: `GET /inference/models` used to hand every caller the
      full tag list, so anyone could read `slm/<8hex>` off it and pass it here
      as a literal to run inference on someone else's private fine-tune.
      Filtering the listing alone would have been theatre — this is the path
      that actually enforced nothing.
    * **Any other literal tag** (`llama3.2:3b`, anything pulled straight into
      the daemon) → passed through verbatim. It maps to no row of ours, so
      there is no owner to check it against.

    `user is None` short-circuits every check — phase-1 anonymous behaviour is
    byte-identical to before auth existed (see `api/services/ownership.py`).
    """
    try:
        artifact_id = UUID(identifier)
    except (ValueError, TypeError):
        if user is None or not identifier.startswith(OUR_TAG_PREFIX):
            return identifier
        # A caller that copied the id straight out of `GET /inference/models`
        # (or out of `ollama list`) may carry the daemon's `:latest` suffix.
        # Reverse-map on the canonical form, or the owner 404s on their own
        # model — the DB never stores the suffix.
        identifier = canonical_our_tag(identifier)
        stmt = select(ModelArtifact).where(ModelArtifact.ollama_model_tag == identifier)
        artifact = (await db.execute(stmt)).scalar_one_or_none()
        # One response for both "no such tag" and "not yours", phrased with the
        # tag the caller supplied. `assert_model_access`'s own 404 names the
        # artifact's UUID, which would both distinguish the two cases and hand
        # the caller an id they had no way to know — so its error is swallowed
        # and re-raised in this shape. Anti-oracle rule, and a **declared
        # exclusion from ADR-012** (see its exclusion list): everywhere else
        # an owner mismatch is now 403, but this branch deliberately keeps
        # both outcomes as one 404. Cited to ADR-012, not ADR-009 — ADR-009's
        # status-code decision is superseded, and a reader who followed that
        # citation would land on a retired rule and conclude this is a miss.
        not_found = HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model {identifier} not found",
        )
        if artifact is None:
            raise not_found
        try:
            await ownership.assert_model_access(db, artifact.id, user)
        except HTTPException:
            raise not_found from None
        return identifier

    artifact = await ownership.assert_model_access(db, artifact_id, user)
    if not artifact.ollama_model_tag:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Model {artifact_id} has not been exported to Ollama yet. "
                f"POST /api/v1/models/{artifact_id}/export with format=gguf first."
            ),
        )
    return artifact.ollama_model_tag


async def _post_json(path: str, payload: dict) -> dict:
    base = str(get_settings().ollama_base_url).rstrip("/")
    async with httpx.AsyncClient(timeout=_OLLAMA_TIMEOUT) as client:
        try:
            resp = await client.post(f"{base}{path}", json=payload)
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"ollama unreachable: {exc}",
            ) from exc
    if resp.status_code == 404:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ollama: model not found ({resp.text[:200]})",
        )
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"ollama returned {resp.status_code}: {resp.text[:300]}",
        )
    return resp.json()


async def _get_json(path: str) -> dict:
    base = str(get_settings().ollama_base_url).rstrip("/")
    async with httpx.AsyncClient(timeout=_OLLAMA_TIMEOUT) as client:
        try:
            resp = await client.get(f"{base}{path}")
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"ollama unreachable: {exc}",
            ) from exc
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"ollama returned {resp.status_code}: {resp.text[:300]}",
        )
    return resp.json()


__all__ = ["chat_completions", "text_completions", "list_models"]
