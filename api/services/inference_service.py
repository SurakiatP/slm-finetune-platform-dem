"""Application service for `/api/v1/inference/*`.

Ollama exposes its own OpenAI-compatible router under `/v1/...`. Our endpoints
forward the validated body, swap the `model` field if the caller passed a
ModelArtifact UUID instead of an Ollama tag, and return Ollama's response.

`stream=true` proxies Ollama's own SSE framing back to the caller (OpenAI
shape: `data: {chunk}\n\n` ... `data: [DONE]\n\n`) via `StreamingResponse`,
gated by `api/services/stream_slots.py`'s per-actor/global concurrency caps
and an idle/total wall-clock timeout. See `_start_stream`/`_relay` below.

**Ownership decision.** Everything here is scoped by whether an Ollama tag is
*namespaced* — has a "/" before any ":" — which is exactly the set of shapes
`workers/tasks/model_export.py::_compute_ollama_tag` can emit for an exported
artifact: `{owner_id}/{name}` (auth on), `local/{name}` (auth off, a
caller-supplied training name), and `slm/{first-8-of-uuid}` (auth off,
legacy unnamed export). Any of these always reverse-maps to a `ModelArtifact`
row via `ollama_model_tag` and therefore always has an owner to check.
Anything else on the daemon — a base model someone pulled directly,
`llama3.2:3b`, `qwen2.5:0.5b`, `nomic-embed-text` and friends — has no "/"
before its colon, maps to no row of ours, and has no owner to check against.

So:

* `chat_completions` / `text_completions` ownership-check `body.model` when
  it is a ModelArtifact UUID **and** when it is a literal namespaced tag
  (`_resolve_model_tag` -> `ownership.assert_model_access`). Running
  inference against someone else's private fine-tune is the same class of
  attack as reading or exporting it.
* `list_models` filters namespaced (platform-owned) tags to the caller's own artifacts.
* Base-model tags (no "/" before the colon) stay listed and callable for
  everyone. Hiding them would make the picker lie about what the daemon can
  actually serve, and they leak nothing — they are not derived from anyone's
  data.

**This was previously scoped to the single `slm/` prefix only**, which
missed the other two shapes `_compute_ollama_tag` can emit —
`{owner_id}/{name}` and `local/{name}` tags bypassed ownership filtering
entirely. Before that, listing was left unfiltered on the grounds that a
bare tag is "a capability from Ollama's own namespace, not one of ours", and
that dropping unresolvable tags would make the listing inconsistent. Both
are worth recording because they were wrong in an instructive way: the two
halves interacted — an unfiltered listing handed every caller the exact
literal strings that `_resolve_model_tag` then accepted without any check.
Either half alone looks defensible; together they were a working
cross-tenant read. Filtering the listing without also closing the literal
path would have been theatre, since the tags are short enough to guess (or,
for `{owner_id}/{name}` and `local/{name}`, guessable/known outright) and
were being published anyway.

`user is None` is a complete no-op throughout, matching the phase-1 rule in
`api/services/ownership.py` — anonymous callers see and can call exactly
what they could before auth existed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from uuid import UUID

import anyio
import httpx
from fastapi import HTTPException, status
from fastapi.responses import StreamingResponse
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core import request_context
from api.core.auth import CurrentUser
from api.core.config import get_settings
from api.core.redis_client import get_redis_client
from api.models.deployment import Deployment
from api.models.model_artifact import ModelArtifact
from api.models.training_job import TrainingJob
from api.schemas.enums import JobStatus
from api.schemas.inference import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    CompletionRequest,
    CompletionResponse,
    ModelDescriptor,
    ModelDescriptorList,
)
from api.services import audit_service, ownership, stream_slots

log = logging.getLogger(__name__)

# Ollama's OpenAI-compat router lives under /v1; native API under /api.
_OLLAMA_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=10.0)
# The streaming path can't use a finite read timeout (a slow token is not a
# dead connection) — idle/total wall-clock limits are enforced in `_relay`
# instead, via `asyncio.wait_for` on each line read.
_OLLAMA_STREAM_TIMEOUT = httpx.Timeout(connect=5.0, read=None, write=10.0, pool=10.0)

# Historical single-prefix constant. `_compute_ollama_tag` now emits three
# namespaced shapes (`{owner_id}/{name}`, `local/{name}`, `slm/{hash8}`), so
# this alone no longer decides ownership anywhere in this module — see
# `is_platform_owned_tag` below. Kept only so an external importer of the old
# name doesn't break; nothing in this file reads it anymore.
OUR_TAG_PREFIX = "slm/"


def is_platform_owned_tag(tag: str) -> bool:
    """True iff `tag` is one of the namespaced shapes this platform emits.

    "Namespaced" means a "/" appears before any ":" in the tag —
    `workers/tasks/model_export.py::_compute_ollama_tag` emits exactly three
    such shapes for an exported artifact: `{owner_id}/{name}` (auth on),
    `local/{name}` (auth off, caller-supplied training name), and
    `slm/{first-8-of-uuid}` (auth off, legacy unnamed export). Any of these
    always reverse-maps to a `ModelArtifact` row via `ollama_model_tag` and
    therefore always has an owner to check.

    Library tags Ollama serves out of its own namespace — `qwen2.5:0.5b`,
    `llama3.2:3b`, `nomic-embed-text` — have no "/" before the colon (or no
    colon at all) and are excluded: they map to no row of ours.
    """
    head = tag.split(":", 1)[0]
    return "/" in head


def canonical_our_tag(tag: str) -> str:
    """Strip Ollama's version suffix from a tag in a platform namespace.

    `workers/tasks/model_export.py` creates models under one of three
    namespaced shapes (`{owner_id}/{name}`, `local/{name}`,
    `slm/<first-8-of-uuid>`) and stores exactly that in
    `ModelArtifact.ollama_model_tag`. The daemon, however, reports the same
    model back with an implicit `:latest` appended to every untagged create.
    Comparing the two forms directly is a silent mismatch that only shows up
    against a real daemon: the owner's own fine-tune gets filtered out of
    `GET /inference/models` as "not yours", and if the id were listed
    verbatim, posting it back would 404 on the reverse-map. Both were
    observed on the vast.ai box.

    Library tags (`llama3.2:1b`, `qwen2.5:0.5b`) are returned untouched —
    there the part after the colon is a real parameter-size/variant, not a
    version, and dropping it would conflate `llama3.2:1b` with
    `llama3.2:3b`.
    """
    if not is_platform_owned_tag(tag):
        return tag
    return tag.split(":", 1)[0]


async def chat_completions(
    db: AsyncSession,
    body: ChatCompletionRequest,
    user: CurrentUser | None = None,
    *,
    api_key_id: str | None = None,
    actor: str | None = None,
) -> ChatCompletionResponse | StreamingResponse:
    tag, deployment = await _resolve_caller_model(db, body.model, user, api_key_id)
    payload = body.model_dump(exclude_none=True)
    payload["model"] = tag
    system_prompt = await _saved_system_prompt(db, tag)
    if system_prompt is not None and not any(m.role == "system" for m in body.messages):
        payload["messages"].insert(0, {"role": "system", "content": system_prompt})

    # Ollama's daemon-global pin: /v1/chat/completions honours a `keep_alive`
    # field, unlike /v1/completions (see `_repin`/text_completions below).
    # Not scoped to the caller — any owner's RUNNING deployment pins the tag.
    running = deployment is not None or await _has_running_deployment(db, tag)
    if running:
        payload["keep_alive"] = -1

    if body.stream:
        return await _start_stream(
            db,
            action="inference.chat_completions",
            url="/v1/chat/completions",
            payload=payload,
            tag=tag,
            deployment=deployment,
            api_key_id=api_key_id,
            actor=_resolve_actor(actor, user),
            translate=None,
            repin=False,
        )

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
            deployment=deployment,
            api_key_id=api_key_id,
        )
        raise
    await _audit_call(
        db,
        action="inference.chat_completions",
        tag=tag,
        outcome="success",
        deployment=deployment,
        api_key_id=api_key_id,
    )
    return ChatCompletionResponse.model_validate(raw)


async def text_completions(
    db: AsyncSession,
    body: CompletionRequest,
    user: CurrentUser | None = None,
    *,
    api_key_id: str | None = None,
    actor: str | None = None,
) -> CompletionResponse | StreamingResponse:
    if body.stream and isinstance(body.prompt, list) and len(body.prompt) != 1:
        # Checked before any DB/rate-limit/slot work per resolved ambiguity —
        # a caller sending a batch of prompts into an SSE response has no
        # sane framing (which prompt does which chunk belong to?), so this is
        # rejected up front rather than silently only answering the first one.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="stream=true supports a single prompt",
        )

    tag, deployment = await _resolve_caller_model(db, body.model, user, api_key_id)
    payload = body.model_dump(exclude_none=True, exclude={"system_prompt"})
    payload["model"] = tag
    system_prompt = body.system_prompt
    if system_prompt is None:
        system_prompt = await _saved_system_prompt(db, tag)
    running = deployment is not None or await _has_running_deployment(db, tag)

    if body.stream:
        if system_prompt is not None:
            # Same chat-translation as the non-stream branch below — the
            # legacy completions API has no system role.
            prompts = body.prompt if isinstance(body.prompt, list) else [body.prompt]
            chat_payload = {k: v for k, v in payload.items() if k != "prompt"}
            chat_payload["messages"] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompts[0]},
            ]
            if running:
                chat_payload["keep_alive"] = -1
            return await _start_stream(
                db,
                action="inference.completions",
                url="/v1/chat/completions",
                payload=chat_payload,
                tag=tag,
                deployment=deployment,
                api_key_id=api_key_id,
                actor=_resolve_actor(actor, user),
                translate=_chat_chunk_to_completion,
                repin=False,
            )
        if isinstance(payload["prompt"], list):
            payload["prompt"] = payload["prompt"][0]
        # /v1/completions ignores `keep_alive` entirely, so no field is sent
        # here — `repin=True` re-pins through the native API once the stream
        # ends instead (see `_repin`).
        return await _start_stream(
            db,
            action="inference.completions",
            url="/v1/completions",
            payload=payload,
            tag=tag,
            deployment=deployment,
            api_key_id=api_key_id,
            actor=_resolve_actor(actor, user),
            translate=None,
            repin=running,
        )

    try:
        if system_prompt is None:
            raw = await _post_json("/v1/completions", payload)
            if running:
                # Best-effort; /v1/completions ignores a `keep_alive` field so
                # the pin is refreshed through Ollama's native API instead.
                await _repin(tag)
        else:
            # The legacy completions API has no system role. Use chat's native
            # template, then translate the response back to the requested shape.
            prompts = body.prompt if isinstance(body.prompt, list) else [body.prompt]
            raw = {
                "created": 0, "model": tag, "choices": [],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
            for index, prompt in enumerate(prompts):
                chat_payload = {k: v for k, v in payload.items() if k != "prompt"}
                chat_payload["messages"] = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ]
                if running:
                    chat_payload["keep_alive"] = -1
                response = await _post_json("/v1/chat/completions", chat_payload)
                chat = ChatCompletionResponse.model_validate(response)
                raw["created"] = chat.created
                choice = chat.choices[0]
                raw["choices"].append({
                    "index": index, "text": choice.message.content,
                    "finish_reason": (
                        "stop" if choice.finish_reason == "tool_calls" else choice.finish_reason
                    ),
                })
                for key, count in chat.usage.model_dump().items():
                    raw["usage"][key] += count
    except Exception as exc:
        await _audit_call(
            db,
            action="inference.completions",
            tag=tag,
            outcome="failure",
            detail={"error_type": type(exc).__name__},
            deployment=deployment,
            api_key_id=api_key_id,
        )
        raise
    await _audit_call(
        db,
        action="inference.completions",
        tag=tag,
        outcome="success",
        deployment=deployment,
        api_key_id=api_key_id,
    )
    return CompletionResponse.model_validate(raw)


async def list_models(
    db: AsyncSession, user: CurrentUser | None = None, *, api_key_id: str | None = None
) -> ModelDescriptorList:
    """List models known to the local Ollama daemon (OpenAI shape).

    Namespaced (platform-owned) tags — `{owner_id}/{name}`, `local/{name}`,
    `slm/…` — are filtered to the caller's own artifacts; everything else the
    daemon knows about — base models pulled straight in — stays visible to
    everyone, because those carry no ownership information and hiding them
    would only make the picker lie about what the daemon can actually serve.

    `user is None` returns the unfiltered list, identical to pre-auth
    behaviour (phase-1 rule, `api/services/ownership.py`).

    `api_key_id is not None` (key-authenticated caller) takes a completely
    different path: only the caller's own **RUNNING** deployments are
    listed, built straight from the DB with no call to the daemon at all —
    a key is scoped to deployments, not to the wider catalogue of exported-
    but-undeployed artifacts and base models a JWT caller can see.
    """
    if api_key_id is not None:
        assert user is not None  # key path always resolves to an owner
        return await _list_deployed_models(db, user.id)

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
        # The daemon reports our models with an implicit `:latest`; the DB
        # holds the bare tag. Compare — and publish — the canonical form, so
        # the id in this listing is the same string the call path accepts.
        tag = canonical_our_tag(entry["id"])
        if visible is not None and is_platform_owned_tag(tag) and tag not in visible:
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


async def _list_deployed_models(db: AsyncSession, owner_id: str) -> ModelDescriptorList:
    """A key-authenticated caller's own RUNNING deployments, DB-only."""
    stmt = (
        select(Deployment, ModelArtifact.ollama_model_tag)
        .join(ModelArtifact, ModelArtifact.id == Deployment.model_artifact_id)
        .where(
            Deployment.owner_id == owner_id,
            Deployment.status == JobStatus.RUNNING,
            ModelArtifact.ollama_model_tag.is_not(None),
        )
        .order_by(Deployment.created_at.desc())
    )
    rows = (await db.execute(stmt)).all()
    items = [
        ModelDescriptor(
            id=tag,
            created=int(deployment.created_at.timestamp()),
            owned_by="slm-platform",
            metadata=None,
        )
        for deployment, tag in rows
    ]
    return ModelDescriptorList(data=items)


async def _resolve_for_key(
    db: AsyncSession, identifier: str, owner_id: str
) -> tuple[str, Deployment]:
    """Resolve `identifier` (a Deployment id or a model tag) to
    `(ollama_tag, Deployment)` for a key-authenticated caller.

    Scoped to the caller's own **RUNNING** deployments only — a base-model
    tag, another owner's deployment, or a PENDING/COMPLETED/FAILED one of
    the caller's own all collapse into the same single 404, same anti-oracle
    rule `_resolve_model_tag` already applies to the JWT path.
    """
    not_found = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Model {identifier} not found or not deployed",
    )
    try:
        deployment_id: UUID | None = UUID(identifier)
    except (ValueError, TypeError):
        deployment_id = None

    if deployment_id is not None:
        stmt = select(Deployment).where(
            Deployment.id == deployment_id,
            Deployment.owner_id == owner_id,
            Deployment.status == JobStatus.RUNNING,
        )
    else:
        stmt = (
            select(Deployment)
            .join(ModelArtifact, ModelArtifact.id == Deployment.model_artifact_id)
            .where(
                Deployment.owner_id == owner_id,
                Deployment.status == JobStatus.RUNNING,
                ModelArtifact.ollama_model_tag == canonical_our_tag(identifier),
            )
        )
    deployment = (await db.execute(stmt)).scalar_one_or_none()
    if deployment is None or deployment.model_artifact_id is None:
        raise not_found

    artifact = await db.get(ModelArtifact, deployment.model_artifact_id)
    if artifact is None or not artifact.ollama_model_tag:
        raise not_found
    return artifact.ollama_model_tag, deployment


async def _resolve_caller_model(
    db: AsyncSession,
    identifier: str,
    user: CurrentUser | None,
    api_key_id: str | None,
) -> tuple[str, Deployment | None]:
    """Dispatch to the key-scoped or JWT tag resolver, whichever applies.

    The key path additionally consumes the deployment's per-minute rate
    limit before returning — a request that fails the quota never reaches
    Ollama.
    """
    if api_key_id is not None:
        assert user is not None  # key path always resolves to an owner
        tag, deployment = await _resolve_for_key(db, identifier, user.id)
        await _consume_rate_limit(deployment)
        return tag, deployment
    return await _resolve_model_tag(db, identifier, user), None


async def _consume_rate_limit(deployment: Deployment) -> None:
    """Redis fixed-window counter, one window per calendar minute.

    A `RedisError` degrades to fail-open (logged, request proceeds) — same
    rationale as `circuit_breaker.py`: a broken Redis must not silently
    refuse every future key-authenticated call.
    """
    now = time.time()
    key = f"ratelimit:deployment:{deployment.id}:{int(now // 60)}"
    redis = get_redis_client()
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 60)
    except RedisError:
        log.warning(
            "inference rate limit check failed for deployment %s, failing open",
            deployment.id,
            exc_info=True,
        )
        return
    finally:
        await redis.aclose()

    if count > deployment.rate_limit_per_min:
        retry_after = 60 - int(now % 60)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Deployment rate limit reached "
                f"({deployment.rate_limit_per_min} req/min). Try again shortly."
            ),
            headers={"Retry-After": str(retry_after)},
        )


def _resolve_actor(actor: str | None, user: CurrentUser | None) -> str:
    """Fall back to the caller's own id when the router didn't supply one —
    e.g. these two service functions called directly, as the unit tests do.
    Mirrors `api/services/idempotency.py::actor_for`'s anonymous bucket."""
    if actor is not None:
        return actor
    return user.id if user is not None else "anon:unknown"


async def _has_running_deployment(db: AsyncSession, tag: str) -> bool:
    """Does *any* owner have a RUNNING Deployment pinning `tag`?

    Ollama's `keep_alive` pin is daemon-global, so this is deliberately not
    scoped to the calling user — someone else's running deployment of the
    same tag still keeps it resident. The key-authenticated path already
    knows its own deployment is RUNNING (`_resolve_for_key` filters on it),
    so callers short-circuit on `deployment is not None` before ever calling
    this. Base-model tags map to no `ModelArtifact` row and are never worth
    the round-trip (decision 6 in the streaming plan).
    """
    if not is_platform_owned_tag(tag):
        return False
    stmt = (
        select(Deployment.id)
        .join(ModelArtifact, ModelArtifact.id == Deployment.model_artifact_id)
        .where(
            Deployment.status == JobStatus.RUNNING,
            ModelArtifact.ollama_model_tag == canonical_our_tag(tag),
        )
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none() is not None


async def _audit_call(
    db: AsyncSession,
    *,
    action: str,
    tag: str,
    outcome: str,
    detail: dict | None = None,
    deployment: Deployment | None = None,
    api_key_id: str | None = None,
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
    if is_platform_owned_tag(tag):
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
    metadata = {"tag": tag, **(detail or {})}
    if deployment is not None or api_key_id is not None:
        metadata["auth"] = "api_key"
        if deployment is not None:
            metadata["deployment_id"] = str(deployment.id)
        if api_key_id is not None:
            metadata["api_key_id"] = api_key_id
    audit_service.record(
        db,
        action=action,
        resource_type="model",
        resource_id=artifact_id or tag,
        project_id=project_id,
        outcome=outcome,
        actor_id=request_context.current_user_id(),
        request_id=request_context.current_request_id(),
        metadata=metadata,
    )
    await db.commit()


# ---- helpers ---------------------------------------------------------------


async def _saved_system_prompt(db: AsyncSession, tag: str) -> str | None:
    """Read immutable training context after the caller's model access check."""
    if not is_platform_owned_tag(tag):
        return None
    artifact = (await db.execute(select(ModelArtifact).where(
        ModelArtifact.ollama_model_tag == canonical_our_tag(tag)
    ))).scalar_one_or_none()
    if artifact is None:
        return None
    training = await db.get(TrainingJob, artifact.training_job_id)
    context = training.context_snapshot if training is not None else None
    return context.get("system_prompt") if isinstance(context, dict) else None


async def _resolve_model_tag(
    db: AsyncSession, identifier: str, user: CurrentUser | None = None
) -> str:
    """Accept either a UUID (ModelArtifact id) or a literal Ollama tag.

    Three cases:

    * **UUID** → look up `ollama_model_tag` on the artifact (must be exported
      first), ownership-checked exactly like `GET /models/{id}`.
    * **Literal tag in a platform namespace** (`{owner_id}/…`, `local/…`,
      `slm/…`) → reverse-map it to the `ModelArtifact` it names and run the
      same ownership check. Skipping this was a real hole: `GET
      /inference/models` used to hand every caller the full tag list, so
      anyone could read a namespaced tag off it and pass it here as a
      literal to run inference on someone else's private fine-tune.
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
        if user is None or not is_platform_owned_tag(identifier):
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


def _ollama_client(timeout: httpx.Timeout = _OLLAMA_TIMEOUT) -> httpx.AsyncClient:
    """Single seam for constructing the client that talks to Ollama.

    `_post_json`, `_get_json`, `_repin` and the streaming path (`_start_stream`)
    all go through this one function, so a test that patches it (to install
    an `httpx.MockTransport`) covers every call shape at once.
    """
    return httpx.AsyncClient(timeout=timeout)


def _raise_for_ollama_status(resp: httpx.Response) -> None:
    """Map a fully-read Ollama response's status code to our HTTPException
    shape. Shared by `_post_json` and the streaming path — the latter reads
    the (small, error-shaped) body before checking status, since a 4xx/5xx
    stream still has to be drained to report anything useful back."""
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


async def _post_json(path: str, payload: dict) -> dict:
    base = str(get_settings().ollama_base_url).rstrip("/")
    async with _ollama_client() as client:
        try:
            resp = await client.post(f"{base}{path}", json=payload)
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"ollama unreachable: {exc}",
            ) from exc
    _raise_for_ollama_status(resp)
    return resp.json()


async def _get_json(path: str) -> dict:
    base = str(get_settings().ollama_base_url).rstrip("/")
    async with _ollama_client() as client:
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


async def _repin(tag: str) -> None:
    """Best-effort keep_alive refresh via Ollama's *native* API.

    `/v1/completions` (OpenAI-compat) silently ignores a `keep_alive` field —
    only `/v1/chat/completions` honours it — so a bare completions call on a
    tag with a RUNNING deployment has to re-pin through `/api/generate`
    instead, after the fact. Never raises: a failed re-pin only means the
    daemon may evict the model on its normal idle schedule, which is not
    worth turning an otherwise-successful inference call into a failure.
    """
    base = str(get_settings().ollama_base_url).rstrip("/")
    try:
        async with _ollama_client() as client:
            await client.post(
                f"{base}/api/generate",
                json={"model": tag, "keep_alive": -1, "stream": False},
            )
    except Exception:
        log.warning("inference: keep_alive re-pin failed for %s", tag, exc_info=True)


def _sse(data: dict | str) -> bytes:
    """One SSE event. `str` is used verbatim (the literal `[DONE]` sentinel);
    anything else is JSON-encoded — this is the OpenAI/Ollama chunk framing."""
    body = data if isinstance(data, str) else json.dumps(data, separators=(",", ":"))
    return f"data: {body}\n\n".encode()


def _sse_error(message: str, type_: str) -> bytes:
    """A mid-stream error event. No `[DONE]` follows one of these — the
    stream just ends (see `_relay`)."""
    return _sse({"error": {"message": str(message)[:300], "type": type_}})


def _chat_chunk_to_completion(chunk: dict) -> dict:
    """Translate one `/v1/chat/completions` stream chunk into the
    `text_completion` chunk shape a `/completions` caller expects — used when
    a saved system prompt forces that call through the chat endpoint (see
    `text_completions`). The usage-only final chunk has empty `choices`, so
    the loop below is a no-op for it and the rest of the chunk passes through.
    """
    choices = [
        {
            "index": choice.get("index", 0),
            "text": (choice.get("delta") or {}).get("content") or "",
            "finish_reason": (
                "stop"
                if choice.get("finish_reason") == "tool_calls"
                else choice.get("finish_reason")
            ),
        }
        for choice in chunk.get("choices", [])
    ]
    return {**chunk, "object": "text_completion", "choices": choices}


async def _start_stream(
    db: AsyncSession,
    *,
    action: str,
    url: str,
    payload: dict,
    tag: str,
    deployment: Deployment | None,
    api_key_id: str | None,
    actor: str,
    translate: Callable[[dict], dict] | None,
    repin: bool,
) -> StreamingResponse:
    """Acquire a concurrency slot, open the upstream stream, map a pre-stream
    failure to the same JSON error shape `_post_json` would raise, then hand
    off to `_relay` for the SSE body.

    A slot from `stream_slots.acquire` is either a real id (release it later)
    or `None` (Redis-outage fail-open — `stream_slots.release` is a no-op for
    `None` too, so the cleanup call below is unconditional either way).

    Everything from here to the `return` is a single failure domain: once
    the slot is held, *any* failure — Ollama refusing the connection, a
    non-httpx exception or cancellation while it's opening, or the
    pre-stream `db.commit()` itself failing — must still close the upstream
    connection (if one was opened), close the client, and release the slot,
    or a broken caller leaks all three forever. `except BaseException` (not
    `Exception`) is deliberate: this can run under cancellation (uvicorn
    tearing down the request task), and a `CancelledError` needs the same
    cleanup as any other failure before it re-propagates.
    """
    slot_id = await stream_slots.acquire(actor)
    payload = {**payload, "stream": True, "stream_options": {"include_usage": True}}
    base = str(get_settings().ollama_base_url).rstrip("/")
    client = _ollama_client(_OLLAMA_STREAM_TIMEOUT)
    upstream: httpx.Response | None = None
    try:
        upstream = await client.send(
            client.build_request("POST", f"{base}{url}", json=payload), stream=True
        )
        if upstream.status_code >= 400:
            await upstream.aread()
            _raise_for_ollama_status(upstream)
        # Release the pooled DB connection before settling in for a
        # potentially-long-lived stream (get_db's session stays open until
        # after the response finishes) — same pattern as
        # model_service.download_artifact. Inside the try: a commit failure
        # here must get the same upstream/client/slot cleanup as any other
        # setup failure, not leak them.
        await db.commit()
    except BaseException as exc:
        # Shielded: every step below must run to completion even though the
        # surrounding task may already be cancelled. Each close is in its
        # own try/except so a failing `upstream.aclose()` can't skip
        # `client.aclose()` or the slot release that follow it.
        with anyio.CancelScope(shield=True):
            if upstream is not None:
                try:
                    await upstream.aclose()
                except Exception:
                    log.warning(
                        "inference stream: failed to close upstream during setup failure",
                        exc_info=True,
                    )
            try:
                await client.aclose()
            except Exception:
                log.warning(
                    "inference stream: failed to close client during setup failure",
                    exc_info=True,
                )
            await stream_slots.release(actor, slot_id)
            try:
                await _audit_call(
                    db, action=action, tag=tag, outcome="error",
                    detail={"stream": True, "error_type": type(exc).__name__},
                    deployment=deployment, api_key_id=api_key_id,
                )
            except Exception:
                log.warning("inference stream: audit write failed", exc_info=True)
        if isinstance(exc, httpx.HTTPError):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"ollama unreachable: {exc}",
            ) from exc
        raise

    return StreamingResponse(
        _relay(
            db,
            client=client,
            upstream=upstream,
            action=action,
            tag=tag,
            deployment=deployment,
            api_key_id=api_key_id,
            actor=actor,
            slot_id=slot_id,
            repin=repin,
            translate=translate,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _relay(
    db: AsyncSession,
    *,
    client: httpx.AsyncClient,
    upstream: httpx.Response,
    action: str,
    tag: str,
    deployment: Deployment | None,
    api_key_id: str | None,
    actor: str,
    slot_id: str | None,
    repin: bool,
    translate: Callable[[dict], dict] | None,
) -> AsyncIterator[bytes]:
    """Relay Ollama's SSE body to the caller, enforcing the idle/total
    timeouts, translating chunks when this is `/completions` proxied through
    the chat endpoint, and recording exactly one audit row no matter how the
    stream ends.

    `outcome` starts as "client_disconnected": that's what stays true if the
    generator is torn down (cancelled/GC'd) without any of the explicit exits
    below running — i.e. the client went away. Cleanup happens inside a
    shielded scope because uvicorn cancels this task on disconnect, and slot
    release / upstream close / the audit write must still complete.
    """
    settings = get_settings()
    idle = settings.inference_stream_idle_timeout_seconds
    deadline = time.monotonic() + settings.inference_stream_max_seconds
    outcome = "client_disconnected"
    usage: dict | None = None
    error_type: str | None = None
    lines = upstream.aiter_lines()

    try:
        while True:
            remaining = deadline - time.monotonic()
            try:
                line = await asyncio.wait_for(anext(lines), max(0.0, min(idle, remaining)))
            except TimeoutError:
                outcome = error_type = "timeout"
                yield _sse_error("stream timed out", "timeout")
                return
            except StopAsyncIteration:
                outcome, error_type = "error", "upstream_error"
                yield _sse_error("upstream closed the stream unexpectedly", "upstream_error")
                return
            except httpx.HTTPError as exc:
                outcome, error_type = "error", "upstream_error"
                yield _sse_error(str(exc), "upstream_error")
                return

            line = line.strip()
            if not line:
                continue
            if line == "data: [DONE]":
                outcome = "completed"
                yield _sse("[DONE]")
                return

            # Ollama's own mid-stream errors arrive as a bare JSON line with
            # no `data:` prefix and no trailing [DONE] — handled the same as
            # a normal `data:` line below once the prefix is stripped.
            raw_json = line[len("data:"):].strip() if line.startswith("data:") else line
            try:
                chunk = json.loads(raw_json)
            except ValueError:
                outcome, error_type = "error", "upstream_error"
                yield _sse_error("could not parse upstream chunk", "upstream_error")
                return

            if not isinstance(chunk, dict):
                # A well-formed but non-object chunk (e.g. a bare `data: []`)
                # has no `.get`/`choices` shape anything downstream can use —
                # treat it the same as an unparseable one rather than let it
                # escape the generator as an uncaught exception.
                outcome, error_type = "error", "upstream_error"
                yield _sse_error("upstream sent a non-object chunk", "upstream_error")
                return

            if "error" in chunk:
                outcome, error_type = "error", "upstream_error"
                message = chunk["error"]
                if isinstance(message, dict):
                    message = message.get("message", str(message))
                yield _sse_error(str(message), "upstream_error")
                return

            chunk_usage = chunk.get("usage")
            if chunk_usage:
                usage = chunk_usage
            try:
                translated = translate(chunk) if translate is not None else chunk
            except (AttributeError, TypeError):
                # `translate` (`_chat_chunk_to_completion`) assumes the chat
                # chunk shape it's documented to receive — a malformed
                # upstream chunk that fails that assumption is an upstream
                # problem, not a 500 in our generator.
                outcome, error_type = "error", "upstream_error"
                yield _sse_error("could not translate upstream chunk", "upstream_error")
                return
            yield _sse(translated)
    finally:
        with anyio.CancelScope(shield=True):
            try:
                await upstream.aclose()
            except Exception:
                log.warning("inference stream: failed to close upstream", exc_info=True)
            try:
                await client.aclose()
            except Exception:
                log.warning("inference stream: failed to close client", exc_info=True)
            await stream_slots.release(actor, slot_id)
            if repin:
                await _repin(tag)
            try:
                detail: dict = {"stream": True}
                if isinstance(usage, dict):
                    # Only the three known-int token counts, never the raw
                    # dict verbatim — a malformed upstream chunk could put
                    # anything under "usage", and `_audit_call` writes this
                    # straight into `AuditEvent.event_metadata`.
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                        value = usage.get(key)
                        if isinstance(value, int):
                            detail[key] = value
                if error_type is not None:
                    detail["error_type"] = error_type
                await _audit_call(
                    db, action=action, tag=tag, outcome=outcome, detail=detail,
                    deployment=deployment, api_key_id=api_key_id,
                )
            except Exception:
                log.warning("inference stream: audit write failed", exc_info=True)


__all__ = [
    "chat_completions",
    "text_completions",
    "list_models",
    "is_platform_owned_tag",
    "canonical_our_tag",
]
