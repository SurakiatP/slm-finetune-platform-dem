"""Celery task: export a trained LoRA adapter to GGUF or SafeTensors.

For GGUF we additionally register the model with Ollama (if reachable) so the
inference router can serve it via the OpenAI-compatible API on the same host.

Flow:
  1. Load the `ModelArtifact` row (with its parent `TrainingJob` for `base_model`).
  2. Download the LoRA adapter dir from MinIO into a local tempdir.
  3. Re-load the base model in 4-bit + attach the adapter via Unsloth.
  4. Save GGUF (or merged SafeTensors) into the tempdir.
  5. Upload the resulting file(s) to MinIO at `models/exports/{artifact_id}/{format}/`.
  6. (GGUF only) build a Modelfile + register `slm/{artifact_id}` with Ollama.
  7. Persist URIs + `ollama_model_tag` back on the artifact row.
  8. Publish `JobCompleted`. GPU cleanup in `finally`.
"""

from __future__ import annotations

import gc
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from celery.utils.log import get_task_logger

from api.core.config import get_settings
from api.models.model_artifact import ModelArtifact
from api.models.training_job import TrainingJob
from api.schemas.enums import ArtifactFormat, JobStatus
from api.schemas.progress import ExportProgress, JobCompleted, JobFailed
from api.services.base_model_catalog import (
    get_ollama_base_tag,
    pull_ollama_base_blocking,
)
from workers.celery_app import celery_app
from workers.ollama_client import OllamaClient
from workers.progress import publish_ws_message, sync_redis_scope
from workers.storage import (
    get_minio_client,
    parse_s3_uri,
    put_directory,
    s3_uri,
)
from workers.sync_db import session_scope

log = get_task_logger(__name__)


@celery_app.task(bind=True, name="model.export", max_retries=0)
def export_model(
    self,
    *,
    artifact_id: str,
    format: str,
    quantization: str | None = None,
) -> dict[str, Any]:
    """Export a `ModelArtifact` to GGUF or SafeTensors and register w/ Ollama.

    Args:
        artifact_id: UUID of the `ModelArtifact` row.
        format: `gguf` or `safetensors` (case-insensitive).
        quantization: GGUF quant method, e.g. `q4_k_m` (default), `q5_k_m`, `f16`.
            Ignored for SafeTensors.
    """
    job_id: str = self.request.id
    settings = get_settings()
    artifact_uuid = UUID(artifact_id)
    fmt = ArtifactFormat(format.lower())

    with sync_redis_scope() as redis:

        def publish(msg: Any) -> None:
            publish_ws_message(redis, job_id, msg)

        def publish_stage(stage: str, detail: str | None = None) -> None:
            """Emit one `ExportProgress` frame. Guarded like the terminal
            publishes below — a Redis hiccup here must never break the export.
            """
            try:
                publish(ExportProgress(job_id=job_id, stage=stage, detail=detail))
            except Exception:  # noqa: BLE001
                log.warning(
                    "export: job=%s failed to publish %s progress",
                    job_id,
                    stage,
                    exc_info=True,
                )

        try:
            # ---- 1. Load artifact + base_model -------------------------------
            base_model, adapter_uri, _artifact_name = _load_export_context(
                artifact_uuid=artifact_uuid, artifact_id=artifact_id
            )
            _mark_export_running(artifact_uuid=artifact_uuid, artifact_id=artifact_id)

            # ---- 2. Pull adapter dir from MinIO ------------------------------
            publish_stage("downloading")
            minio = get_minio_client()
            adapter_bucket, adapter_prefix = parse_s3_uri(adapter_uri)
            workdir = tempfile.mkdtemp(prefix=f"export-{artifact_id}-")
            adapter_dir = os.path.join(workdir, "adapter")
            os.makedirs(adapter_dir, exist_ok=True)

            log.info(
                "export: job=%s downloading adapter %s to %s",
                job_id,
                adapter_uri,
                adapter_dir,
            )
            _download_prefix(minio, adapter_bucket, adapter_prefix, adapter_dir)

            try:
                # ---- 3. Re-load base + LoRA + 4. save GGUF/SafeTensors ------
                # Deferred imports — torch/unsloth/transformers only here.
                from unsloth import FastLanguageModel

                # Load base + adapter via Unsloth in one shot. Reading from the
                # adapter dir lets FastLanguageModel parse adapter_config.json,
                # download the base from base_model_name_or_path, and tag the
                # resulting model as Unsloth-aware PeftModel — which is what
                # save_pretrained_merged checks for. Wrapping with raw
                # `PeftModel.from_pretrained(base, adapter_dir)` produced a
                # plain peft wrapper that Unsloth's saver rejected with:
                #   "Model is not a PeftModel (no Lora adapters detected).
                #    Skipping Merge"
                # — so the merge silently no-op'd, leaving stage/ empty and
                # convert_to_gguf failing on missing config.json.
                log.info(
                    "export: job=%s loading base %s + adapter from %s",
                    job_id,
                    base_model,
                    adapter_dir,
                )
                model, tokenizer = FastLanguageModel.from_pretrained(
                    model_name=adapter_dir,
                    max_seq_length=2048,
                    dtype=None,
                    load_in_4bit=True,
                )

                gguf_path: str | None = None
                merged_dir: str | None = None

                # Both branches below call `save_pretrained_merged` next — one
                # "merging" frame covers either format instead of duplicating
                # the publish call in each branch.
                publish_stage("merging")

                if fmt is ArtifactFormat.GGUF:
                    quant = quantization or "q4_k_m"
                    # We don't use Unsloth's save_pretrained_gguf — it ships a
                    # *patched* convert_hf_to_gguf.py that calls an old
                    # AutoTokenizer.from_pretrained signature and dies on
                    # transformers ≥4.51 with:
                    #     'dict' object has no attribute 'model_type'
                    # Instead: merge with Unsloth (works), then drive the
                    # *original* /app/llama.cpp/convert_hf_to_gguf.py and our
                    # statically-linked llama-quantize binary directly.
                    stage_dir = os.path.join(workdir, "stage")
                    os.makedirs(stage_dir, exist_ok=True)
                    log.info("export: job=%s merging HF model to %s", job_id, stage_dir)
                    model.save_pretrained_merged(
                        stage_dir,
                        tokenizer,
                        save_method="merged_16bit",
                    )
                    gguf_path = _quantize_merged_to_gguf(
                        stage_dir=stage_dir,
                        workdir=workdir,
                        quant=quant,
                        job_id=job_id,
                        progress_cb=publish_stage,
                    )
                elif fmt is ArtifactFormat.SAFETENSORS:
                    merged_dir = os.path.join(workdir, "merged")
                    os.makedirs(merged_dir, exist_ok=True)
                    log.info(
                        "export: job=%s saving merged SafeTensors to %s",
                        job_id,
                        merged_dir,
                    )
                    model.save_pretrained_merged(
                        merged_dir,
                        tokenizer,
                        save_method="merged_16bit",
                    )
                else:
                    raise ValueError(f"unsupported export format: {fmt!r}")

                # ---- 5. Upload to MinIO --------------------------------------
                publish_stage("uploading")
                bucket = settings.minio_models_bucket
                gguf_uri: str | None = None
                safetensors_uri: str | None = None
                ollama_tag: str | None = None

                if gguf_path is not None:
                    key_prefix = f"exports/{artifact_id}/gguf"
                    file_count, size_bytes = put_directory(
                        minio, bucket, key_prefix, os.path.dirname(gguf_path)
                    )
                    gguf_uri = s3_uri(bucket, key_prefix)
                    log.info(
                        "export: uploaded %d gguf files (%.1f MB) to %s",
                        file_count,
                        size_bytes / (1024 * 1024),
                        gguf_uri,
                    )

                    # ---- 6. Register with Ollama (best-effort) --------------
                    # Ollama registration is a convenience for serving via the
                    # OpenAI-compatible router; it's not what makes B6 succeed.
                    # The GGUF on MinIO is the primary artifact. Ollama's
                    # /api/create has rolled through several breaking schema
                    # changes (modelfile string → from/files), so isolating its
                    # failure keeps gguf_uri persistable on partial success.
                    publish_stage("registering")
                    try:
                        candidate_tag = _compute_ollama_tag(artifact_id)
                        _register_with_ollama(
                            ollama=OllamaClient(str(settings.ollama_base_url)),
                            tag=candidate_tag,
                            gguf_path=gguf_path,
                        )
                        ollama_tag = candidate_tag
                    except Exception as ollama_exc:  # noqa: BLE001
                        log.warning(
                            "ollama registration failed for %s (best-effort, continuing): %s",
                            artifact_id,
                            ollama_exc,
                        )

                    # ---- 6.5. Best-effort pull of the matching base ----------
                    # So the playground can A/B compare fine-tuned vs base
                    # without the user having to `ollama pull` by hand.
                    # Failure here is silent — base presence is checked by FE
                    # via /inference/models, and a missing base just hides the
                    # compare affordance in the UI.
                    if ollama_tag and get_ollama_base_tag(base_model):
                        pull_ollama_base_blocking(
                            base_model,
                            ollama_base_url=str(settings.ollama_base_url),
                        )

                if merged_dir is not None:
                    key_prefix = f"exports/{artifact_id}/safetensors"
                    file_count, size_bytes = put_directory(
                        minio, bucket, key_prefix, merged_dir
                    )
                    safetensors_uri = s3_uri(bucket, key_prefix)
                    log.info(
                        "export: uploaded %d safetensors files (%.1f MB) to %s",
                        file_count,
                        size_bytes / (1024 * 1024),
                        safetensors_uri,
                    )

                # ---- 7. Persist artifact URIs --------------------------------
                _persist_export_uris(
                    artifact_uuid=artifact_uuid,
                    artifact_id=artifact_id,
                    gguf_uri=gguf_uri,
                    safetensors_uri=safetensors_uri,
                    ollama_tag=ollama_tag,
                )

                # ---- 8. Publish completion -----------------------------------
                publish(
                    JobCompleted(
                        job_id=job_id,
                        result={
                            "artifact_id": artifact_id,
                            "format": fmt.value,
                            "gguf_uri": gguf_uri,
                            "safetensors_uri": safetensors_uri,
                            "ollama_model_tag": ollama_tag,
                        },
                        model_artifact_id=artifact_uuid,
                    )
                )
                return {
                    "status": "completed",
                    "artifact_id": artifact_id,
                    "format": fmt.value,
                    "gguf_uri": gguf_uri,
                    "safetensors_uri": safetensors_uri,
                    "ollama_model_tag": ollama_tag,
                }
            finally:
                shutil.rmtree(workdir, ignore_errors=True)

        except BaseException as exc:
            # BaseException, not Exception: `POST /models/{id}/export/cancel`
            # revokes this task via `celery_app.control.revoke(terminate=True,
            # signal="SIGTERM")`. Billiard's worker-child signal handler
            # (`billiard.common._shutdown_cleanup`) turns that SIGTERM into
            # `sys.exit(...)` — i.e. a `SystemExit` raised *inside this very
            # task body*, at whatever line happens to be executing. `celery.
            # exceptions.Terminated` (a plain `Exception` subclass) is a
            # separate thing the *master* process synthesizes for its own
            # bookkeeping — it is not what shows up here. `SystemExit` (and
            # `BaseException` generally) is NOT caught by `except Exception`,
            # so a cancelled export used to skip this whole block and leave
            # `export_status` stuck at RUNNING forever. Widening the catch
            # fixes that; the trailing bare `raise` still re-propagates
            # SystemExit/Terminated/whatever afterwards, so Celery's own
            # revoke/failure bookkeeping is unaffected — we only add cleanup
            # in front of it.
            log.exception("export task failed (job=%s, artifact=%s)", job_id, artifact_id)
            try:
                with session_scope() as fail_session:
                    row = fail_session.get(ModelArtifact, artifact_uuid)
                    if row is not None:
                        row.export_error_message = (str(exc) or repr(exc))[:4000]
                        # The cancel endpoint sets export_status=CANCELLED in
                        # the DB *before* revoking the task. If that already
                        # landed, don't clobber it back to FAILED — CANCELLED
                        # is the more accurate terminal state for this run.
                        if row.export_status != JobStatus.CANCELLED:
                            row.export_status = JobStatus.FAILED
            except Exception:  # noqa: BLE001 — never mask the original failure
                log.warning(
                    "could not persist export_error_message for %s",
                    artifact_id,
                    exc_info=True,
                )
            try:
                publish(
                    JobFailed(
                        job_id=job_id,
                        error=str(exc) or repr(exc),
                        error_type=type(exc).__name__,
                    )
                )
            except Exception:  # noqa: BLE001
                log.warning("failed to publish JobFailed", exc_info=True)
            raise
        finally:
            _release_gpu_memory()


# ---- helpers ---------------------------------------------------------------


def _download_prefix(minio: Any, bucket: str, prefix: str, local_dir: str) -> None:
    """Download every object under `prefix` into `local_dir`, preserving names."""
    prefix = prefix.rstrip("/") + "/"
    for obj in minio.list_objects(bucket_name=bucket, prefix=prefix, recursive=True):
        rel = obj.object_name[len(prefix) :]
        if not rel:  # the prefix itself
            continue
        target = os.path.join(local_dir, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        response = minio.get_object(bucket_name=bucket, object_name=obj.object_name)
        try:
            with open(target, "wb") as fh:
                for chunk in response.stream(64 * 1024):
                    fh.write(chunk)
        finally:
            response.close()
            response.release_conn()


def _first_gguf(directory: str) -> str:
    """Return the path of the first .gguf file in `directory` (depth-1 only)."""
    for name in sorted(os.listdir(directory)):
        if name.lower().endswith(".gguf"):
            return os.path.join(directory, name)
    raise RuntimeError(f"no .gguf produced in {directory}")


def _register_with_ollama(*, ollama: OllamaClient, tag: str, gguf_path: str) -> None:
    """Register a GGUF with the local Ollama daemon.

    Uses the blob-upload + create-with-files flow (Ollama 0.5+); the legacy
    Modelfile-string path was removed and now answers
    ``{"error":"neither 'from' or 'files' was specified"}`` if used. We
    upload the GGUF body to ``/api/blobs/sha256:<HASH>`` first and then
    reference it by digest in ``/api/create``, which means the ollama
    container does not need a shared volume with the worker — it can read
    its own blob store on its own filesystem.
    """
    if not ollama.health():
        log.warning("ollama daemon not reachable; skipping registration of %s", tag)
        return
    log.info("ollama: uploading blob from %s", gguf_path)
    digest = ollama.upload_blob(gguf_path)
    log.info("ollama: registering tag=%s with %s", tag, digest[:23])
    ollama.create_from_blob(
        tag=tag,
        digest=digest,
        parameters={"temperature": 0.0, "num_ctx": 2048},
    )


def _release_gpu_memory() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except Exception:  # noqa: BLE001
        log.debug("torch cleanup skipped", exc_info=True)
    gc.collect()


# ---- pure helpers (characterized by tests/unit/test_snapshot_node_7.py) ----
#
# These are split out so the refactor of ``export_model`` can call them in
# place of equivalent inline code without changing observable behaviour.
# Snapshot diff = 0 after refactor proves byte-stability.


def _compute_ollama_tag(artifact_id: str) -> str:
    """Build the Ollama model tag for a fine-tuned artifact.

    Format: ``slm/<first-8-chars-of-uuid>`` — short enough to type, long
    enough that real-world artifact collisions are vanishingly unlikely
    on the dev box. Used by ``export_model`` to register the fine-tuned
    GGUF with Ollama.
    """
    return f"slm/{artifact_id[:8]}"


def _convert_hf_to_gguf_argv(stage_dir: str, f16_path: str) -> list[str]:
    """Argv for the HF → GGUF f16 conversion step.

    We drive the *original* llama.cpp ``convert_hf_to_gguf.py`` (not
    Unsloth's patched copy, which calls a removed AutoTokenizer
    signature and dies on transformers ≥4.51).
    """
    return [
        "python",
        "/app/llama.cpp/convert_hf_to_gguf.py",
        "--outfile", f16_path,
        "--outtype", "f16",
        stage_dir,
    ]


def _quantize_gguf_argv(f16_path: str, gguf_path: str, quant: str) -> list[str]:
    """Argv for the f16 → quantized GGUF step.

    Uses our statically-linked ``llama-quantize`` binary baked into the
    worker image at ``/app/llama.cpp/llama-quantize``.
    """
    return [
        "/app/llama.cpp/llama-quantize",
        f16_path, gguf_path, quant,
    ]


def _load_export_context(
    *,
    artifact_uuid: UUID,
    artifact_id: str,
) -> tuple[str, str, str | None]:
    """Load ``ModelArtifact`` + parent ``TrainingJob`` and return the trio
    ``(base_model, adapter_uri, artifact_name)`` the rest of ``export_model``
    needs to drive the merge + GGUF pipeline.

    Validates the two preconditions that determine whether export can even
    start: the artifact row must exist, and it must have a
    ``lora_adapter_uri`` (which is only written after training completes).
    Either failure raises ``RuntimeError`` with a message suitable for
    surfacing via the WebSocket ``JobFailed`` envelope.

    Resolves ``base_model`` with a fallback ladder
    ``artifact.base_model`` → ``training.base_model`` because old artifact
    rows (pre Phase 11) didn't pin the base on the artifact itself. We
    still read ``artifact_name`` for parity with the inline form; current
    callers don't consume it, but extracting that side-effect-free read
    keeps the helper's return shape stable if a future caller wants it.
    """
    with session_scope() as session:
        artifact = session.get(ModelArtifact, artifact_uuid)
        if artifact is None:
            raise RuntimeError(f"ModelArtifact {artifact_id} not found")
        if not artifact.lora_adapter_uri:
            raise RuntimeError(
                f"Artifact {artifact_id} has no lora_adapter_uri — "
                "training likely never completed"
            )
        training = session.get(TrainingJob, artifact.training_job_id)
        if training is None:
            raise RuntimeError(
                f"TrainingJob {artifact.training_job_id} missing for artifact {artifact_id}"
            )
        return (
            artifact.base_model or training.base_model,
            artifact.lora_adapter_uri,
            artifact.name,
        )


def _mark_export_running(*, artifact_uuid: UUID, artifact_id: str) -> None:
    """Flip `export_status` to RUNNING once `_load_export_context` has
    confirmed the artifact exists and has a LoRA adapter to export.

    Deliberately a separate `session_scope()` call rather than a side
    effect added to `_load_export_context` itself — that helper lives in
    the "pure helpers" section characterized by the snapshot refactor
    tests (`tests/unit/test_snapshot_node_7.py`) as a read-only
    validator, and its return-shape contract shouldn't grow a write side
    effect. Missing row is a no-op (mirrors `_persist_export_uris`'s
    defensive checks elsewhere) — `_load_export_context` already raised
    if the row didn't exist, so this only no-ops on the (extremely rare)
    concurrent-delete race.
    """
    with session_scope() as session:
        artifact = session.get(ModelArtifact, artifact_uuid)
        if artifact is not None:
            artifact.export_status = JobStatus.RUNNING
        else:  # pragma: no cover — defensive; _load_export_context just confirmed it
            log.warning(
                "export: job artifact %s vanished before RUNNING could be persisted",
                artifact_id,
            )


def _persist_export_uris(
    *,
    artifact_uuid: UUID,
    artifact_id: str,
    gguf_uri: str | None,
    safetensors_uri: str | None,
    ollama_tag: str | None,
) -> None:
    """Write back the produced artifact URIs and clear any stale export error.

    Each URI is written only if set, so this is safe to call for either
    GGUF-only or SafeTensors-only exports. Clearing
    ``export_error_message`` here ensures a retry that succeeds doesn't
    leave the prior failure visible to the FE. Also flips
    ``export_status`` to ``COMPLETED`` — purely additive alongside the
    URI/error-message fields, which remain the completion contract
    callers already rely on.

    Raises ``RuntimeError`` if the artifact row vanished mid-export — this
    is the same defensive check the inline code performed and signals a
    concurrent delete (extremely rare; FE confirms artifact existence
    before queuing).
    """
    with session_scope() as session:
        art = session.get(ModelArtifact, artifact_uuid)
        if art is None:
            raise RuntimeError(f"artifact {artifact_id} disappeared mid-export")
        if gguf_uri:
            art.gguf_uri = gguf_uri
        if safetensors_uri:
            art.safetensors_uri = safetensors_uri
        if ollama_tag:
            art.ollama_model_tag = ollama_tag
        art.export_error_message = None  # clear stale failure on retry
        art.export_status = JobStatus.COMPLETED


def _quantize_merged_to_gguf(
    *,
    stage_dir: str,
    workdir: str,
    quant: str,
    job_id: str,
    progress_cb: Callable[[str, str | None], None] | None = None,
) -> str:
    """Drive ``convert_hf_to_gguf.py`` + ``llama-quantize`` over a merged HF dir.

    Takes a directory containing an Unsloth-merged HF model (``stage_dir``,
    already written by ``model.save_pretrained_merged``), produces the
    quantized GGUF at ``<workdir>/gguf/model.<quant>.gguf``, and returns
    its path. Removes the intermediate f16 GGUF on success — it's only
    needed as input to llama-quantize.

    Side effects: creates ``<workdir>/gguf/`` (mkdir -p), may mutate
    ``<stage_dir>/config.json`` in place to dodge the transformers 4.57.2
    AutoTokenizer bug (see ``_bump_transformers_version_if_buggy``).
    Raises ``CalledProcessError`` if either subprocess returns non-zero.

    Extracted from ``export_model`` so the byte-stable refactor can be
    snapshot-verified — the four pure helpers it calls
    (``_bump_transformers_version_if_buggy``,
    ``_convert_hf_to_gguf_argv``, ``_quantize_gguf_argv``) are
    individually pinned by ``tests/unit/test_snapshot_node_7.py``.

    ``progress_cb``, if given, is invoked as ``(stage, detail)`` right
    before each subprocess starts (``"converting"`` then
    ``"quantizing"`` with the quant level as ``detail``) — the same
    optional-callback shape ``workers/tasks/evaluation.py`` uses for
    ``_predict_rows``. Optional and keyword-only so the existing caller
    (and any test that constructs this helper directly) keeps working
    unchanged when it's omitted.
    """
    # Workaround transformers 4.57.2 bug at
    # tokenization_utils_base.py:2419 — when the saved config.json has
    # transformers_version <= 4.57.2, _from_pretrained does
    # `_config.model_type` on a dict (json.load returned a dict, not a
    # PretrainedConfig). AttributeError. The check is gated on version,
    # so bumping the field skips the buggy branch entirely.
    cfg_path = os.path.join(stage_dir, "config.json")
    with open(cfg_path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    if _bump_transformers_version_if_buggy(cfg):
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)

    out_dir = os.path.join(workdir, "gguf")
    os.makedirs(out_dir, exist_ok=True)
    f16_path = os.path.join(out_dir, "model.f16.gguf")
    if progress_cb is not None:
        progress_cb("converting", None)
    log.info("export: job=%s converting HF→GGUF (f16)", job_id)
    subprocess.run(
        _convert_hf_to_gguf_argv(stage_dir=stage_dir, f16_path=f16_path),
        check=True,
    )

    gguf_path = os.path.join(out_dir, f"model.{quant}.gguf")
    if progress_cb is not None:
        progress_cb("quantizing", quant)
    log.info("export: job=%s quantizing GGUF → %s", job_id, quant)
    subprocess.run(
        _quantize_gguf_argv(f16_path=f16_path, gguf_path=gguf_path, quant=quant),
        check=True,
    )
    os.remove(f16_path)
    return gguf_path


def _bump_transformers_version_if_buggy(cfg: dict[str, Any]) -> bool:
    """Workaround transformers 4.57.2 ``model_type``-on-dict AttributeError.

    The bug at ``tokenization_utils_base.py:2419`` only triggers when the
    saved ``config.json`` has ``transformers_version <= 4.57.2``. Bumping
    the field in-place skips the buggy branch entirely without affecting
    inference behaviour (the field is purely informational at load time).

    Mutates ``cfg`` in place. Returns True iff a bump happened — callers
    use that to decide whether to re-serialize.
    """
    if cfg.get("transformers_version", "0") <= "4.57.2":
        cfg["transformers_version"] = "4.58.0"
        return True
    return False


__all__ = ["export_model"]
