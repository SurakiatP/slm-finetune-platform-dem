"""Application service for `POST /api/v1/trainings`.

Steps:
  1. Validate project exists.
  2. Validate dataset exists, is accessible to the caller (cross-project and
     orphaned datasets are allowed — see `ownership.assert_dataset_access`),
     has finished generation (storage_uri set), and matches the project's
     task_type.
  3. Validate base_model is in the supported allowlist (ADR-002).
  3b. RTX 3060 VRAM safety — reject a per_device_train_batch_size that
      would OOM at the requested max_seq_length (mirrors the HPO guard).
  4. training_name uniqueness within the owning project's owner scope (409
     on collision; owner_id IS NULL projects share one scope).
  5. GPU quota gate (Bucket.GPU, shared with HPO training/evaluation/export).
  6. Insert TrainingJob row (status=PENDING, status flips to RUNNING in the worker).
  7. Enqueue the train.manual Celery task.
  8. Stash celery_task_id back on the row.
  9. Return TrainingJobAcceptedResponse.

HPO mode goes through `submit_hpo_training_job` (Phase 6 — currently 501).
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core import request_context
from api.core.auth import CurrentUser
from api.services import audit_service, ownership, quota
from api.services.quota import Bucket
from api.core.config import get_settings
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.routers.tasks_meta import SUPPORTED_BASE_MODELS
from api.schemas.enums import JobStatus
from api.schemas.training import (
    HPOTrainingRequest,
    ManualTrainingRequest,
    TrainingJobAcceptedResponse,
)

_SUPPORTED_MODEL_IDS: frozenset[str] = frozenset(m.id for m in SUPPORTED_BASE_MODELS)
_PARAMS_BY_MODEL_ID: dict[str, float] = {m.id: m.params_billions for m in SUPPORTED_BASE_MODELS}


# RTX 3060 12GB safe `per_device_train_batch_size` ceiling per
# (params_billions_bucket, max_seq_length_bucket). Values include a ~20%
# headroom over what Unsloth/community benchmarks show fits comfortably with
# QLoRA 4-bit + LoRA r=16 on all-linear modules. Used by the HPO service
# guard to reject search-space `per_device_train_batch_size` choices that
# would OOM mid-trial and zombie the whole study.
_MAX_SAFE_BATCH_3060: tuple[tuple[float, tuple[tuple[int, int], ...]], ...] = (
    # (params_upper_billions, ((seq_upper, max_batch), ...))
    (1.2, ((1024, 16), (2048, 8), (4096, 4), (8192, 2))),  # ≤1B (TinyLlama, Llama-1B)
    (1.8, ((1024, 8), (2048, 4), (4096, 2), (8192, 1))),   # ≤1.5B (Qwen2.5-1.5B)
    (2.2, ((1024, 4), (2048, 4), (4096, 2), (8192, 1))),   # ≤2B (SmolLM2, Qwen3-1.7B, Gemma2-2B)
    (3.5, ((1024, 4), (2048, 2), (4096, 1), (8192, 1))),   # ≤3B (Llama-3B, Qwen2.5-3B)
)


def _max_safe_batch_for_3060(params_billions: float, max_seq_length: int) -> int:
    """Return the largest `per_device_train_batch_size` we expect to fit in
    12GB VRAM for the given model size and sequence length on RTX 3060 with
    QLoRA 4-bit + LoRA r=16 + all-linear target modules.

    Used by both manual and HPO submission to reject a
    `per_device_train_batch_size` that would OOM. Manual mode used to skip
    this check (power users get to overshoot at their own risk), but a
    doomed manual submit still burns the single GPU slot and fails opaquely
    mid-run — same cost as a poisoned HPO trial, so both paths apply it now.
    """
    for params_upper, seq_table in _MAX_SAFE_BATCH_3060:
        if params_billions <= params_upper:
            for seq_upper, max_batch in seq_table:
                if max_seq_length <= seq_upper:
                    return max_batch
            return seq_table[-1][1]  # seq > 8192 — clamp to longest row's ceiling
    # Shouldn't happen: SUPPORTED_BASE_MODELS caps at 3.5B per BaseModelInfo.
    # Be conservative and return the 3B row.
    return _MAX_SAFE_BATCH_3060[-1][1][-1][1]


async def _assert_training_name_available(
    db: AsyncSession, *, project: Project, training_name: str | None
) -> None:
    """Reject a duplicate `training_name` within the submitting project's
    owner scope (join TrainingJob -> Project, compared on `owner_id`).

    Scope is the *owner*, not just this one project: `training_name` doubles
    as the MLflow run name and (downstream) the Ollama export tag, both of
    which are namespaced per-owner rather than per-project, so two of the
    same owner's projects must not be able to collide on it either. Projects
    with `owner_id IS NULL` (phase-1 / auth-disabled rows) all share a single
    scope — matches `ownership.py`'s fail-closed treatment of null owners as
    their own bucket rather than either mutually invisible or a free-for-all
    open to every named owner too.

    LEFT OUTER JOIN, not INNER: `TrainingJob.project_id` is nullable
    (orphaned runs whose `Project` was deleted — migration
    `0012_training_decouple`). An INNER JOIN would silently drop those rows
    from this uniqueness check, letting a new submission reuse a
    `training_name` an orphaned run still holds — exactly the collision
    this check exists to prevent, since the MLflow run name / Ollama tag it
    protects don't care whether the row that minted them still has a
    project. With the outer join, an orphan produces one row with
    `Project.owner_id` NULL, which the `Project.owner_id.is_(None)` branch
    below already matches — so orphaned runs land in the same "global/
    null-owner scope" bucket as projects that predate auth, with no extra
    branching needed.

    A no-op when `training_name` is `None` — untitled runs never collide.
    """
    if training_name is None:
        return
    stmt = (
        select(TrainingJob.id)
        .outerjoin(Project, Project.id == TrainingJob.project_id)
        .where(TrainingJob.training_name == training_name)
    )
    stmt = (
        stmt.where(Project.owner_id.is_(None))
        if project.owner_id is None
        else stmt.where(Project.owner_id == project.owner_id)
    )
    existing = (await db.execute(stmt.limit(1))).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"training_name '{training_name}' already exists",
        )


async def submit_manual_training_job(
    db: AsyncSession,
    request: ManualTrainingRequest,
    user: CurrentUser | None = None,
) -> TrainingJobAcceptedResponse:
    """Validate the request, persist the TrainingJob row, and enqueue the worker."""
    settings = get_settings()

    # 1. Project exists?
    project = await db.get(Project, request.project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {request.project_id} not found",
        )

    # 2. Dataset exists + accessible to the caller + ready + matches project's
    # task_type. Cross-project and orphaned (project_id IS NULL) datasets are
    # allowed by user decision (G3) — ownership, not project membership, is
    # the gate: auth-off allows any dataset; auth-on requires the caller own
    # it (including an orphan whose owner_id still matches), 403 otherwise.
    dataset = await ownership.assert_dataset_access(db, request.dataset_id, user)
    if dataset.task_type != project.task_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Dataset task_type ({dataset.task_type.value}) does not match "
                f"project task_type ({project.task_type.value})"
            ),
        )
    if not dataset.storage_uri or dataset.num_samples == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Dataset {dataset.id} is not ready (no rows persisted yet). "
                "Wait for SDG to complete or upload seed data first."
            ),
        )

    # 3. Base model allowlist (ADR-002 — no arbitrary HF models)
    base_model = request.base_model or settings.default_base_model
    if base_model not in _SUPPORTED_MODEL_IDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"base_model '{base_model}' is not in the supported list. "
                f"Use GET /api/v1/base-models to see allowed values."
            ),
        )

    # 3b. 3060 VRAM safety — the requested per_device_train_batch_size must
    # fit on the GPU at the requested max_seq_length. A manual submit that
    # OOMs mid-run still burns the single GPU slot and fails opaquely, same
    # as an unsafe HPO trial (see `_max_safe_batch_for_3060` guard below).
    params_b = _PARAMS_BY_MODEL_ID.get(base_model)
    if params_b is not None:
        seq = request.manual_config.max_seq_length
        max_batch = _max_safe_batch_for_3060(params_b, seq)
        requested_batch = request.manual_config.per_device_train_batch_size
        if requested_batch > max_batch:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"manual_config.per_device_train_batch_size={requested_batch} "
                    f"exceeds the safe ceiling ({max_batch}) for base_model='{base_model}' "
                    f"({params_b:.2f}B params) at max_seq_length={seq} on RTX 3060 12GB. "
                    f"Lower per_device_train_batch_size or shorten max_seq_length."
                ),
            )

    # 4. training_name must be unique within the owning project's owner scope
    # (409 on collision). Runs before the GPU quota gate so a doomed-to-fail
    # duplicate submit never consumes a GPU slot.
    await _assert_training_name_available(
        db, project=project, training_name=request.training_name
    )

    # 5. GPU quota gate — one bucket shared with HPO training, evaluation, and
    # export (they all pin the same RTX 3060). Runs after every validation /
    # resource-state check above and right before the row insert, so a
    # rejected submit never leaves a half-created TrainingJob behind.
    await quota.assert_can_submit(db, bucket=Bucket.GPU, actor_id=request_context.current_user_id())

    # 6. Insert TrainingJob row.
    job_row = TrainingJob(
        project_id=project.id,
        dataset_id=dataset.id,
        mode=request.mode,
        status=JobStatus.PENDING,
        base_model=base_model,
        training_name=request.training_name,
        auto_export=request.auto_export,
        auto_evaluate=request.auto_evaluate,
        config_json=request.manual_config.model_dump(mode="json"),
    )
    db.add(job_row)
    await db.flush()  # populate job_row.id

    # 7. Enqueue Celery task. Local import keeps the API process from
    # eagerly loading worker-only deps (torch, unsloth, minio, ...).
    from workers.tasks.training import train_manual

    async_result = train_manual.apply_async(
        kwargs={"training_id": str(job_row.id)},
    )
    job_id: str = async_result.id

    # 8. Persist celery_task_id + the audit row, and commit them together.
    job_row.celery_task_id = job_id
    audit_service.record(
        db,
        action="training.submit",
        resource_type="training",
        resource_id=str(job_row.id),
        project_id=job_row.project_id,
        actor_id=request_context.current_user_id(),
        request_id=request_context.current_request_id(),
        metadata={"job_id": job_id, "mode": "manual", "base_model": job_row.base_model},
    )
    await db.commit()

    # 9. Return.
    return TrainingJobAcceptedResponse(
        job_id=job_id,
        training_id=job_row.id,
        mlflow_run_id=None,        # populated by the worker once the run starts
        mlflow_url=None,
        status=JobStatus.PENDING,
        websocket_url=f"/ws/jobs/{job_id}",
    )


async def submit_hpo_training_job(
    db: AsyncSession,
    request: HPOTrainingRequest,
    user: CurrentUser | None = None,
) -> TrainingJobAcceptedResponse:
    """Validate the HPO request, persist the TrainingJob row, and enqueue the worker.

    Mirrors `submit_manual_training_job` but persists `hpo_config` (n_trials,
    objective_metric, search_space, fixed_config, ...) into `config_json` and
    dispatches to the `train.hpo` Celery task. Also mirrors its `training_name`
    uniqueness check (409 within the owning project's owner scope) — see
    `_assert_training_name_available` — and its dataset ownership gate (G3) —
    see `ownership.assert_dataset_access`.
    """
    settings = get_settings()

    # 1. Project exists?
    project = await db.get(Project, request.project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {request.project_id} not found",
        )

    # 2. Dataset exists + accessible to the caller + ready + matches project's
    # task_type. Cross-project and orphaned (project_id IS NULL) datasets are
    # allowed by user decision (G3) — see submit_manual_training_job above.
    dataset = await ownership.assert_dataset_access(db, request.dataset_id, user)
    if dataset.task_type != project.task_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Dataset task_type ({dataset.task_type.value}) does not match "
                f"project task_type ({project.task_type.value})"
            ),
        )
    if not dataset.storage_uri or dataset.num_samples == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Dataset {dataset.id} is not ready (no rows persisted yet). "
                "Wait for SDG to complete or upload seed data first."
            ),
        )

    # 3. Base model allowlist (ADR-002)
    base_model = request.base_model or settings.default_base_model
    if base_model not in _SUPPORTED_MODEL_IDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"base_model '{base_model}' is not in the supported list. "
                f"Use GET /api/v1/base-models to see allowed values."
            ),
        )

    # 4. Per-config trial cap (defense-in-depth on top of HPOConfig's own cap).
    if request.hpo_config.n_trials > settings.default_hpo_max_trials:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"hpo_config.n_trials={request.hpo_config.n_trials} exceeds the platform cap "
                f"({settings.default_hpo_max_trials}). Lower it or raise DEFAULT_HPO_MAX_TRIALS."
            ),
        )

    # 4b. 3060 VRAM safety — if the search space includes
    # `per_device_train_batch_size`, every choice must fit on the GPU at the
    # `fixed_config.max_seq_length`. A trial that OOMs mid-epoch poisons
    # Optuna's pruner and wastes the wall-clock budget.
    bs_space = request.hpo_config.search_space.per_device_train_batch_size
    if bs_space is not None:
        seq = request.hpo_config.fixed_config.max_seq_length
        params_b = _PARAMS_BY_MODEL_ID.get(base_model)
        if params_b is not None:
            max_batch = _max_safe_batch_for_3060(params_b, seq)
            unsafe = [c for c in bs_space.choices if isinstance(c, int) and c > max_batch]
            if unsafe:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"hpo_config.search_space.per_device_train_batch_size choices {unsafe} "
                        f"exceed the safe ceiling ({max_batch}) for base_model='{base_model}' "
                        f"({params_b:.2f}B params) at max_seq_length={seq} on RTX 3060 12GB. "
                        f"Lower the choices or shorten max_seq_length."
                    ),
                )

    # 5. training_name must be unique within the owning project's owner scope
    # (409 on collision). Runs before the GPU quota gate so a doomed-to-fail
    # duplicate submit never consumes a GPU slot.
    await _assert_training_name_available(
        db, project=project, training_name=request.training_name
    )

    # 6. GPU quota gate — same shared bucket as manual training, evaluation,
    # and export (see quota.py). After all validation above, right before
    # the row insert.
    await quota.assert_can_submit(db, bucket=Bucket.GPU, actor_id=request_context.current_user_id())

    # 7. Insert TrainingJob row.
    job_row = TrainingJob(
        project_id=project.id,
        dataset_id=dataset.id,
        mode=request.mode,
        status=JobStatus.PENDING,
        base_model=base_model,
        training_name=request.training_name,
        auto_export=request.auto_export,
        auto_evaluate=request.auto_evaluate,
        config_json=request.hpo_config.model_dump(mode="json"),
    )
    db.add(job_row)
    await db.flush()

    # 8. Enqueue Celery task. Local import keeps the API process from
    # eagerly loading worker-only deps.
    from workers.tasks.hpo_training import train_hpo

    async_result = train_hpo.apply_async(
        kwargs={"training_id": str(job_row.id)},
    )
    job_id: str = async_result.id

    job_row.celery_task_id = job_id
    audit_service.record(
        db,
        action="training.submit",
        resource_type="training",
        resource_id=str(job_row.id),
        project_id=job_row.project_id,
        actor_id=request_context.current_user_id(),
        request_id=request_context.current_request_id(),
        metadata={"job_id": job_id, "mode": "hpo", "base_model": job_row.base_model},
    )
    await db.commit()

    return TrainingJobAcceptedResponse(
        job_id=job_id,
        training_id=job_row.id,
        mlflow_run_id=None,
        mlflow_url=None,
        status=JobStatus.PENDING,
        websocket_url=f"/ws/jobs/{job_id}",
    )


__all__ = ["submit_manual_training_job", "submit_hpo_training_job"]
