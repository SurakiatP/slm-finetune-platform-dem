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
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from celery.utils.log import get_task_logger

from api.core.config import get_settings
from api.models.model_artifact import ModelArtifact
from api.models.training_job import TrainingJob
from api.schemas.enums import ArtifactFormat
from api.schemas.progress import JobCompleted, JobFailed
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

        try:
            # ---- 1. Load artifact + base_model -------------------------------
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
                base_model = artifact.base_model or training.base_model
                adapter_uri = artifact.lora_adapter_uri
                artifact_name = artifact.name

            # ---- 2. Pull adapter dir from MinIO ------------------------------
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

                    # Workaround transformers 4.57.2 bug at
                    # tokenization_utils_base.py:2419 — when the saved
                    # config.json has transformers_version <= 4.57.2,
                    # _from_pretrained does `_config.model_type` on a
                    # dict (json.load returned a dict, not a
                    # PretrainedConfig). AttributeError. The check is
                    # gated on version, so bumping the field in the
                    # saved config skips the buggy branch entirely.
                    cfg_path = os.path.join(stage_dir, "config.json")
                    with open(cfg_path, "r", encoding="utf-8") as fh:
                        cfg = json.load(fh)
                    if cfg.get("transformers_version", "0") <= "4.57.2":
                        cfg["transformers_version"] = "4.58.0"
                        with open(cfg_path, "w", encoding="utf-8") as fh:
                            json.dump(cfg, fh, indent=2)

                    out_dir = os.path.join(workdir, "gguf")
                    os.makedirs(out_dir, exist_ok=True)
                    f16_path = os.path.join(out_dir, "model.f16.gguf")
                    log.info("export: job=%s converting HF→GGUF (f16)", job_id)
                    subprocess.run(
                        [
                            "python",
                            "/app/llama.cpp/convert_hf_to_gguf.py",
                            "--outfile", f16_path,
                            "--outtype", "f16",
                            stage_dir,
                        ],
                        check=True,
                    )

                    gguf_path = os.path.join(out_dir, f"model.{quant}.gguf")
                    log.info("export: job=%s quantizing GGUF → %s", job_id, quant)
                    subprocess.run(
                        [
                            "/app/llama.cpp/llama-quantize",
                            f16_path, gguf_path, quant,
                        ],
                        check=True,
                    )
                    os.remove(f16_path)
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
                    try:
                        candidate_tag = f"slm/{artifact_id[:8]}"
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

        except Exception as exc:
            log.exception("export task failed (job=%s, artifact=%s)", job_id, artifact_id)
            try:
                with session_scope() as fail_session:
                    row = fail_session.get(ModelArtifact, artifact_uuid)
                    if row is not None:
                        row.export_error_message = (str(exc) or repr(exc))[:4000]
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


__all__ = ["export_model"]
