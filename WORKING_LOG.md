# Working Log

> Append-only session log. **Newest entries on top.**
> Each entry must include: Who / Status / Why & What / Test Summary / Next Action.
> Update this file at the end of every Claude Code session before `/clear`.

---

## Session 11 — vast.ai Deploy SUCCEEDED + Swagger Test Guide (2026-05-08)

**Who:** Claude (Opus 4.7) + parks (developer)
**Status:** Finished — full stack deployed in 9 min 13 sec on a new 4060 Ti host; runbook validated end-to-end; Swagger test guide shipped

**Why & What:**
- Continuation of Session 10. After destroying the slow-network 5070 Ti host, parks rented a new vast.ai Linux VM with explicit attention to the `Inet Down` filter — picked a Taiwan host with `Inet Down` 1016 Mbps + `Inet Up` 2050 Mbps + KINGSTON NVMe 4723 MB/s + RTX **4060 Ti / 16 GB**. Architecture is Ada Lovelace (sm_89), which **matches** our `worker.Dockerfile`'s CUDA 12.1 + PyTorch 2.5.1 base perfectly — no PTX JIT fallback like the Blackwell 5070 Ti would have triggered.
- Re-applied the §6 NVIDIA toolkit fix from the runbook. Hit a new wrinkle: **stale dpkg lock** from the previous failed install attempt (PID 4599 was dead but the lock file `/var/lib/dpkg/lock-frontend` still showed it as held by `fuser`). Fix was `rm -f /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/cache/apt/archives/lock /var/lib/apt/lists/lock` then `dpkg --configure -a` — needs to be added to the runbook's troubleshooting section.
- Also hit GPG TTY error: `gpg --dearmor` opened `/dev/tty` for a status display, which fails under non-interactive SSH. Fix is `gpg --batch --yes --no-tty --dearmor`. Existing runbook §6 doesn't have these flags — also worth adding.
- After cleanup, the deploy script ran clean. Timeline:
  - `BUILD` start → `UP -d` start: **7 min 5 s** (vs 41 min and counting on the slow host)
  - `UP -d` → `ALL HEALTHY`: 10 s
  - `MIGRATIONS`: ~2 s
  - `OLLAMA PULL llama3.2:3b` (≈ 2 GB): **22 s** at near-line-rate (1 Gbps theoretical = 16 s; 22 s actual is ~73% of theoretical, excellent)
  - Unit tests: 5/5 passed in 0.14 s
  - Smoke endpoints: `/health` = `{"status":"ok"}`, `/api/v1/projects` = empty paged list
  - **Total: 9 min 13 s** from `git pull` to all-tests-green
- Wrote `SWAGGER_GUIDE.md` (top-level, 16 sections, ~14 KB) for parks to drive the deployed API via Swagger. Schemas were extracted live from the running deploy's `/openapi.json` — every request body example is verified against the actual Pydantic models. Three task-type examples (qa, classification, tool_calling) are covered, plus both SDG paths (with-seed via OpenRouter, upload-seed via multipart for the no-key flow).

**Test Summary:**
- **Deploy script** (`/tmp/deploy.sh` on VM, output at `/tmp/deploy.log`):
  - All stage markers progressed in order: `START → BUILD → UP -d → WAIT HEALTHY → ALL HEALTHY (10s) → MIGRATIONS → OLLAMA PULL → INSTALL PYTEST → UNIT TESTS → SMOKE → ALL DONE`.
  - All 7 containers ended in `Up` state: 4 healthy (postgres, redis, minio, mlflow), 3 running (api, worker, ollama).
- **Endpoint smoke** (live, vast.ai):
  - `GET /health` → 200 `{"status":"ok"}`
  - `GET /api/v1/projects` → 200 `{"items":[],"total":0,"limit":50,"offset":0}`
  - `GET /openapi.json` → 200, **57 schemas + 25 paths** matching expected surface.
- **Unit tests** (inside api container, host pytest install): `tests/unit/test_config.py::*` → 5/5 passed in 0.14 s.
- **Schemas verified** before writing the Swagger guide:
  - 25 endpoints listed by tag — matches `/openapi.json`.
  - Response shapes for `ProjectResponse`, `DatasetResponse`, `DatasetPreviewResponse`, `SeedUploadResponse`, `SDGJobAcceptedResponse`, `TrainingResponse`, `TrainingJobAcceptedResponse`, `ModelArtifactResponse`, `ModelExportResponse`, `EvaluationResponse`, `EvaluationCompareResponse`, `MlflowUrlResponse`, `Page_*` — all extracted from live spec.
  - Task example endpoint (`GET /api/v1/tasks/{type}/example`) returns the canonical row shape for each of qa / classification / tool_calling — verified all three.
- **Did NOT run** integration tests (`tests/integration/test_full_flow.py`) or any SDG/training/inference flow on the deploy. parks intends to drive those manually via Swagger.

**Decisions Made:**
- **`Inet Down` is the load-bearing filter for picking vast.ai hosts**, confirmed empirically. The 5070 Ti host (~340 KB/s effective) projected a 2.5-hour build; the 4060 Ti host (1 Gbps) finished in 7 min. A factor of 20+ in build time, on the same `worker.Dockerfile`. Codified in the runbook §3.
- **Ada-arch GPU (4060 Ti / 4090) is the cleanest match for our current `worker.Dockerfile`.** Blackwell (5070 Ti) works via PTX JIT but with a 30–60 s warmup penalty per fresh container start, and Unsloth 2024.12.4 doesn't have sm_120 native paths. For first deployments, prefer Ada or Ampere hosts unless we deliberately bump the base image to PyTorch 2.6+ / CUDA 12.8+.
- **`SWAGGER_GUIDE.md` lives at the repo root, not under `docs/`.** It's a *user-facing* operational document (parks will open it alongside the Swagger UI), not architectural reference. Treating it like `README.md` and `API_GUIDE.md` (also at root) keeps it discoverable.
- **Schemas in the Swagger guide are extracted from the live `/openapi.json`, not hand-written.** Hand-written examples drift the moment a Pydantic model gains a field. Anchoring on the live spec means a `git pull` followed by a fresh deploy regenerates the truth without touching the doc.
- **Did NOT bump `worker.Dockerfile` base image yet.** parks's 4060 Ti is Ada-arch, so PyTorch 2.5.1 + CUDA 12.1 are correct. The Blackwell follow-up only matters if a future rental lands on a 5070/5080/5090 — captured as a future item, not done in this session.

**Files Touched:**
- `WORKING_LOG.md` — this entry.
- `SWAGGER_GUIDE.md` — **new**, top-level user guide for Swagger-driven testing (16 sections).

**Next Action:**
1. **parks tests via Swagger** at `http://localhost:8000/docs` (with the SSH tunnel `-L 8000:localhost:8000`). Recommended first run: §16 Quick Smoke Test (5-min, 9-step lifecycle without OpenRouter). If green, layer SDG / LLM-judge on top.
2. **(Optional) Fold the dpkg-lock-cleanup + gpg `--no-tty` flags into runbook §6.** They're real failure modes that bit us this session; the current runbook would steer a fresh deployer into the same wall. Open a small follow-up PR.
3. **(Optional) Once parks finishes Swagger validation**, consider opening the `dev → main` release PR. The branch will then have: Sessions 9 (local infra fixes), 10 (vast.ai investigation + runbook), 11 (validated deploy + Swagger guide) — a coherent release surface.

**Blockers:** None. The pipeline is live and exercisable; only thing left is parks driving the lifecycle via Swagger to confirm the user-experience surface.

---

## Session 10 — vast.ai Linux VM Deployment Attempt (2026-05-08)

**Who:** Claude (Opus 4.7) + parks (developer)
**Status:** Partial — environment validated, deploy aborted on slow Docker Hub pull; runbook + memory updated for next attempt

**Why & What:**
- Goal: deploy the full stack (incl. `worker` + `ollama`) on a vast.ai GPU instance so we can actually fine-tune. Laptop GPU (GTX 1050 Ti, sm_61) cannot run Unsloth/QLoRA, so this is the first remote-deploy attempt.
- Initial misstep: rented a regular vast.ai **Docker instance** (RTX 3060), then discovered via `ssh` probe that `docker: command not found` and `/var/run/docker.sock` is missing — vast.ai's Docker instances *are* containers, so they cannot host nested Docker. Confirmed via [docs](https://docs.vast.ai/instances/launch-modes): "you cannot run docker in vast.ai instances because they are docker containers themselves." Switched to the **`Ubuntu 22.04 VM`** template — a real KVM VM that supports nested Docker.
- Second VM (RTX **5070 Ti** / 16 GB / driver 580.95.05 — vast.ai gave us a Blackwell GPU instead of Ampere; better VRAM, but worker.Dockerfile's PyTorch 2.5.1 + CUDA 12.1 will fall back to PTX JIT until we bump the base image). Probed the environment:
  - ✅ Real VM (no `/.dockerenv`, full `cap_sys_admin`, systemd 249).
  - ✅ Docker 28.1.1 + Compose v2.35.1 pre-installed.
  - ⚠️ NVIDIA Container Toolkit **half-configured** — `/etc/docker/daemon.json` points to `nvidia-container-runtime` but the binary isn't installed. `--gpus all` and `--runtime=nvidia` both error with "executable file not found" / "could not select device driver."
  - ⚠️ `unattended-upgrades` on fresh boot holds the dpkg lock, blocking `apt install`. **`systemctl stop` does NOT kill the in-flight Python process** — only the unit. Required `systemctl mask unattended-upgrades` + `kill -9 $(pgrep -f unattended-upgr)` to free the lock.
  - Once both fixes were in, `apt install nvidia-container-toolkit` + `nvidia-ctk runtime configure --runtime=docker` + `systemctl restart docker` made `docker run --gpus all` work cleanly. Verified with `nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi` → showed `RTX 5070 Ti, 16303 MiB`.
- Cloned the repo, switched to `dev`, copied `.env.example` → `.env` (user filled in `OPENROUTER_API_KEY`), kicked off `docker compose build` via a `nohup` deploy script writing to `/tmp/deploy.log`.
- **Build aborted at 41 min** — pytorch base image (`pytorch/pytorch:2.5.1-cuda12.1-cudnn9-runtime`, 3.1 GB) was downloading at ~340 KB/s from Docker Hub on this host. Even with one resumed-stall recovery, the projected total deploy time was 2.5+ hours just to finish the worker image. User decided to destroy + re-rent on a faster host. The build was killed but the VM was kept running per user instruction (they handle destroy via web console).
- Parallel work during the slow build: researched vast.ai's marketplace filter columns and confirmed `Inet Down` (not `DLP`/`DLPerf`) is the correct knob. `DLP` = GPU compute score; `Inet Down` = actual download bandwidth. Cheap-but-slow hosts ≪ slightly-pricier-but-fast hosts when builds are network-bound — the wasted compute hours dwarf any `$/hr` saving.

**Test Summary:**
- **Pre-deploy probes** — all read-only SSH checks passed:
  - Identity: Ubuntu 22.04.5 LTS, kernel 6.8, hostname `ubuntu` (not a container ID).
  - Resources: 24 GB RAM, 118 GB free disk, RTX 5070 Ti / 16 GB.
  - Capabilities: `cap_sys_admin` + 30+ others — full systemd VM.
  - Docker daemon: nvidia runtime registered (but binary missing — fixed in §6).
- **NVIDIA toolkit install** — `docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi` → `RTX 5070 Ti, 16303 MiB` ✅ (after the mask + kill -9 + apt install sequence).
- **Stack boot/healthcheck** — never reached. Build aborted before `docker compose up -d`.
- **API endpoints / migrations / ollama / unit tests / integration tests** — none executed; deploy didn't get there.

**Decisions Made:**
- **Pre-empt slow builds, don't wait through them.** At < 500 KB/s on the pytorch base layer, the worker image alone takes 2+ hours and the math never works out — better to destroy and re-rent in 5 min than wait 2 hours for sunk-cost reasons. Codified as a top-level §3 in the new runbook ("if `Inet Down` is < 500 Mbps, abort").
- **Runbook lives at `docs/runbooks/vast-ai-deployment.md`**, not in `docs/architecture/` or scattered across ADRs. New runbooks go under `docs/runbooks/` so future ones (server-side fine-tune, MLflow restore, etc.) have an obvious home.
- **Memory updated, not duplicated.** `~/.claude/projects/.../memory/vast_ai_deployment.md` was rewritten to point at the runbook for procedural detail, and to surface only the cross-session lessons (Inet Down filter, two install gotchas, SSH-during-build instability). Memory should change *recommendations*, not duplicate steps.
- **Did NOT bump `worker.Dockerfile` to PyTorch 2.6 / CUDA 12.8 yet** even though the VM has a Blackwell GPU. PTX JIT will work for first runs and we can validate the deploy path before committing to a base-image change. If the worker container exits on `import unsloth` due to sm_120 issues, the runbook §13 has the fallback recipe.

**Files Touched:**
- `docs/runbooks/vast-ai-deployment.md` — **new**, full deployment recipe (~340 lines).
- `WORKING_LOG.md` — this entry.
- `~/.claude/projects/.../memory/vast_ai_deployment.md` — rewritten with the 4 lessons learned.
- `~/.claude/projects/.../memory/MEMORY.md` — index hook updated.

**Next Action:**
1. **User destroys the slow VM** via [cloud.vast.ai/instances/](https://cloud.vast.ai/instances/) → trash icon. Billing stops at destroy.
2. **Pick a new host** with `Inet Down ≥ 500 Mbps` (ideally ≥ 1000) on `RTX 3060` / `3090` / `4090`. Sort the marketplace by `Inet Down` descending, filter the GPU you want, ignore `DLPerf` for selection.
3. (Optional) `docker login` after SSH-in to lift Docker Hub's anon-IP rate limit.
4. Run the §15 quick-paste cheat sheet in the runbook. ETA on a fast host: ~30 min from `git clone` to all-tests-green.
5. After successful deploy: open `http://localhost:8000/docs` in browser (works via `-L 8000:localhost:8000` SSH tunnel) and exercise the API endpoints via Swagger.

**Blockers:** None procedurally — runbook is complete. The retry just needs a faster host and a willing user.

---

## Session 9 — Local Infra Validation (Step A) (2026-05-08)

**Who:** Claude (Opus 4.7) + parks (developer)
**Status:** Finished — all 5 infra services healthy, 8/8 endpoints green, 3 latent bugs found & fixed

**Why & What:**
- First-time `docker compose up -d postgres redis minio minio-init mlflow api` on developer's local box (Windows 11 + Docker Desktop + WSL2 Ubuntu, Intel i5-8300H / 16 GB / GTX 1050 Ti 4 GB). Goal: confirm the no-GPU half of the stack boots cleanly so non-training development can happen on the laptop and only fine-tuning is shipped to the remote 3060 box.
- After boot, all five services reached `healthy` (postgres, redis, minio, mlflow, api), but the smoke-test of every GET endpoint surfaced **three latent bugs** that had never run end-to-end before:

  - **B1 — `api_cors_origins` parse failure** (`api/core/config.py:26`):
    - API container crashed on startup with `pydantic_settings.exceptions.SettingsError` → `JSONDecodeError: Expecting value`.
    - Root cause: pydantic-settings v2 (we ship 2.14) attempts `json.loads()` on **any** `list[X]`-typed env value **before** field validators run. The CSV value from `.env.example` (`http://localhost:3000,http://localhost:5173`) is not valid JSON, and the existing `field_validator(mode="before")` was never reached.
    - Fix: `api_cors_origins: Annotated[list[str], NoDecode] = Field(...)` + `from pydantic_settings import NoDecode`. `NoDecode` disables the JSON pre-pass so the existing CSV-splitting validator runs.

  - **B2 — MLflow shares the application database** (`docker-compose.yml:133`, `docker/postgres-init.sql` (new)):
    - All four CRUD list endpoints (`/projects`, `/datasets`, `/trainings`, `/models`) returned 500 with `relation "model_artifacts" does not exist`. App alembic had clearly never run, but the `slm` DB already had 19 tables (`experiments`, `runs`, `metrics`, ...) and `alembic_version=0584bdc529eb` — none of which match the app schema.
    - Root cause: MLflow's `--backend-store-uri` pointed at `postgres:5432/${POSTGRES_DB}` = the same `slm` database as the app. MLflow auto-runs its own alembic on first startup, which races to populate `alembic_version` first; the app's alembic would then refuse to apply migrations on an unknown revision.
    - Fix:
      1. New `docker/postgres-init.sql` creates a separate `mlflow` database on first volume init, mounted into postgres at `/docker-entrypoint-initdb.d/`.
      2. Compose `mlflow` service `--backend-store-uri` switched to `postgres:5432/${MLFLOW_DB:-mlflow}`.
      3. For the running cluster (init script doesn't re-run on existing volumes): manually `CREATE DATABASE mlflow`, then `DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ...` on the polluted `slm` DB to clear it.

  - **B3 — `alembic.ini` not present in the api container** (`docker/api.Dockerfile:23`, `docker-compose.yml`):
    - `docker compose exec api alembic upgrade head` → `FAILED: No 'script_location' key found in configuration`. The container had `/app/alembic/` (migrations dir) but not `/app/alembic.ini` (config).
    - Root cause: `api.Dockerfile` copies `alembic/` directory but not `alembic.ini`; compose mounts also didn't include the file.
    - Fix: `COPY alembic.ini ./alembic.ini` in `api.Dockerfile` (forward-compat for non-compose runs) **and** `./alembic.ini:/app/alembic.ini:ro` bind mount in compose (avoids a rebuild for the running stack).

- After all three fixes + `alembic upgrade head` (revision `0001_initial`), the `slm` DB now has the expected 6 application tables (`projects`, `datasets`, `training_jobs`, `model_artifacts`, `evaluation_runs`, `alembic_version`). The `mlflow` DB has its own 19 MLflow tables — clean separation.
- Added `./tests:/app/tests:ro` mount to the api service so unit tests can be executed inside the container without rebuilding the image. (Long-term, devs run pytest from the host with `[dev]` extras; this mount is for ad-hoc verification.)
- Wrote `tests/unit/test_config.py` (5 tests) as a regression guard for B1: default fallback, single-CSV, multi-CSV, whitespace stripping, and explicit-list-kwarg paths. All pass.

**Test Summary:**
- **Endpoint smoke (8/8 GET, 1 POST→GET cycle, all green)**:
  | Endpoint | Status | Notes |
  |----------|--------|-------|
  | `GET /health` | 200 `{"status":"ok"}` | |
  | `GET /api/v1/tasks` | 200 | metadata, no DB |
  | `GET /api/v1/base-models` | 200 | metadata, no DB |
  | `GET /api/v1/projects` | 200 `{items:[],total:0,...}` | empty paged list |
  | `GET /api/v1/datasets` | 200 | empty paged list |
  | `GET /api/v1/trainings` | 200 | empty paged list |
  | `GET /api/v1/models` | 200 | empty paged list |
  | `GET /api/v1/no-such-route` | 404 `{detail:"Not Found",code:"not_found",extra:null}` | global handler shape preserved |
  | `POST /api/v1/projects {name:"smoke-test",task_type:"qa"}` | 201 + UUID | CRUD path live |
  | `GET /api/v1/projects` (after POST) | 200 `total:1` | round-trip confirmed |

- **Service health** (`docker compose ps`): postgres, redis, minio, mlflow all `(healthy)`; api `running` (no healthcheck wired in compose; `/health` returns 200).
- **Regression tests**: `pytest tests/unit/test_config.py -v` → 5/5 passed in 0.52s, on Python 3.11.15 inside the api container.
- **DB schema**: `slm` DB shows app's 6 tables; `mlflow` DB shows MLflow's 19 tables. No overlap.

**Decisions Made:**
- **MLflow runs on its own database, not a schema.** Schema isolation in the same DB still leaves a single `alembic_version` table contended between two alembic instances; only a separate database fully decouples MLflow's migration history from the app's. The cost is a one-line postgres init script — cheap.
- **`MLFLOW_DB` env var with default `mlflow`**, not hardcoded. Keeps the docker-compose pattern consistent with `${POSTGRES_DB:-slm}` and lets ops override (e.g., `mlflow_prod`, `mlflow_staging`) without editing compose.
- **`alembic.ini` is duplicated between Dockerfile COPY and compose bind mount**, intentionally. The COPY makes the image self-contained for `docker run` outside compose; the bind mount avoids a rebuild during dev when only `alembic.ini` changes. Same dual-track applied to `alembic/` already.
- **`NoDecode` over a custom `EnvSettingsSource`**. The custom source is the more powerful pattern (would let us override JSON parsing for *all* fields), but it's overkill for one CSV field. `Annotated[..., NoDecode]` is a 12-character change with the same effect and stays in the type signature where future readers will see it.
- **Did NOT bake pytest / dev extras into the api image.** API runtime images stay minimal; tests are run on the host (recommended) or via `pip install pytest pytest-asyncio` inside a temp `docker exec` (what we did to verify in this session). If we end up wanting CI to run unit tests against the same image, a separate `api-dev.Dockerfile` is the right move — recorded as a follow-up, not done here.
- **Did NOT wipe Docker volumes** to clean the polluted `slm` DB. `DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ...` is reversible and surgical — losing only the data we want to lose. `docker compose down -v` would have also wiped redis (empty) and minio buckets (recreatable), but it's a heavier hammer and easy to misuse on a non-fresh system.

**Files Touched:**
- `api/core/config.py` — `Annotated[list[str], NoDecode]` + import.
- `docker-compose.yml` — postgres init mount, mlflow URI to `mlflow` DB, api `alembic.ini` + `tests` mounts.
- `docker/api.Dockerfile` — `COPY alembic.ini ./alembic.ini`.
- `docker/postgres-init.sql` — **new**, single `CREATE DATABASE mlflow;`.
- `tests/unit/test_config.py` — **new**, 5 regression tests.
- `.env` — created from `.env.example` (gitignored, no secrets set).

**Next Action:**
1. Update `CLAUDE.md` to reflect actual local hardware (GTX 1050 Ti 4 GB, not RTX 3060 12 GB) — laptop stays the *dev* target, server with 3060 is the *training* target. The "≤3B in QLoRA 4-bit" constraint stays accurate for the server but cannot be smoke-tested locally.
2. Consider a `docker compose --profile dev up` profile that mounts tests + installs dev extras, vs the current "manual `pip install` after up" pattern. Out of scope for this session.
3. Step B (GPU runtime probe with `docker run --rm --gpus all nvidia/cuda:... nvidia-smi`) — confirm Docker Desktop sees the 1050 Ti before attempting `worker`/`ollama` services. The 1050 Ti is sm_61 and *won't* run bitsandbytes 4-bit in any case, but the runtime path itself can still be validated before sending a job to the 3060.
4. Set `OPENROUTER_API_KEY` in `.env` before any SDG call (Phase 4 surface) — currently empty.

**Blockers:** None for non-GPU work. GPU-dependent tests/training stay blocked by the 1050 Ti's compute capability and 4 GB VRAM until the 3060 box is online.

---

## Session 8 — Phase 8: Polish (2026-05-08)

**Who:** Claude (Opus 4.7) + parks (developer)
**Status:** Finished — full PoC complete; awaiting Phase 8 approval

**Why & What:**
- Phase 8 wraps the PoC: every endpoint listed in `require.md` is now wired (no remaining 501s), error responses share one shape, OpenAPI docs include copy-paste-able examples for each task type, an integration-test scaffold + two example clients ship in the repo, and the README has a full curl + Python walkthrough.
  - **Wire-501 (CRUD endpoints prior phases left as stubs)**:
    - `api/services/projects_service.py`: full CRUD (create/list/get/update/delete) + `task_type` immutable on update (matches schema).
    - `api/services/datasets_service.py`: list (paginated, optional `project_id` filter), get, **preview** (streams JSONL line-by-line out of MinIO and stops at `limit` so big datasets never load fully into memory), download (`StreamingResponse` over MinIO), delete (best-effort MinIO object cleanup; row removal succeeds even on storage errors), **upload-seed** (multipart, JSON-or-JSONL detection, per-row Pydantic validation, returns `invalid_rows: [int]` indexes).
    - `api/services/trainings_service.py`: list (project + status filters), get, **cancel** (revokes the Celery task with `terminate=True, signal='SIGTERM'`, then flips DB status to `CANCELLED`; idempotent — terminal-status jobs return their existing status; 404 for missing), `mlflow-url` (synthesises the deep-link from `mlflow_run_id` + `mlflow_experiment_id`).
    - All three routers replaced their remaining `_NOT_IMPLEMENTED` raises with thin pass-throughs to the new services. **Final 501-stub count: 0 across the entire API surface.**
  - **P1 — global error handlers** (`api/core/exceptions.py` + `install_handlers(app)` in `main.py`):
    - `HTTPException` → `ErrorResponse(detail, code)` with code derived from status (`not_found`, `conflict`, `validation_error`, `bad_gateway`, ...).
    - `RequestValidationError` → 422 + `{detail: "<loc>: <msg>", code: "validation_error", extra: {errors: [...]}}` — keeps Pydantic's per-field details under `extra` for form rendering, surfaces the first error's location in `detail` for log/log-aggregation.
    - Unhandled `Exception` → 500 + correlation-id (12-char uuid) in `extra`; full traceback logged server-side, never leaked to clients. The user can quote the correlation-id when reporting an issue.
    - **Important fix**: handler registers against `starlette.exceptions.HTTPException`, not `fastapi.HTTPException`. Starlette raises 404/405 from the routing layer before any handler runs; FastAPI's class is a subclass, so registering on Starlette's catches both.
  - **P2 — OpenAPI examples**: added `json_schema_extra={"examples": [...]}` to:
    - `ProjectCreate` (3 examples — one per task type).
    - `SDGRequestWithSeed` (1 — QA happy path with 5-seed example), `SDGRequestDescriptionOnly` (2 — classification + tool_calling).
    - `ManualTrainingRequest` (1 — full-config example), `HPOTrainingRequest` (1 — n_trials=8 with mixed search_space).
    - `EvaluationCreate` (1 — with `use_llm_judge` + judge override).
    - `ModelExportRequest` (2 — gguf q4_k_m + safetensors).
    Verified all 7 schemas have at least one example in `/openapi.json` `components.schemas`.
  - **P3 — integration test scaffold** (`tests/integration/test_full_flow.py`, `pytestmark = pytest.mark.integration`):
    - Probes the API at `INTEGRATION_API_URL` (default `http://localhost:8000`); skips the whole module if `/health` is unreachable, so unit-test runs (`pytest -m "not integration"`) never accidentally execute it.
    - 3 tests: `test_qa_full_flow` (project → seed upload → preview → SDG → wait → train, with the train step gated behind `INTEGRATION_HAS_GPU=1`), `test_classification_create_only` (no-GPU path: project + description_only SDG submission), `test_404_for_missing_project` (asserts the global handler's `ErrorResponse` shape on a real 404).
    - `_wait_for_status(path, target, timeout, poll_interval)` helper polls a row's status until terminal — stand-in for the WS subscription in test land.
  - **P4 — example clients** (`examples/`):
    - `python_client.py`: argparse-driven CLI (`--task-type {qa,classification,tool_calling}`, `--num-samples`, `--train`); walks project → SDG (with WebSocket progress streaming via `websockets`) → optional train+export → `/v1/chat/completions`. Falls back from WS to dataset polling if `websockets.connect` fails. Uses `httpx.Client` for HTTP, `asyncio.run(_stream_progress)` for WS.
    - `quickstart_curl.sh`: pure-bash curl walkthrough; reads project_id / dataset_id / job_id via `jq` and chains them; covers project → SDG → poll → preview → train submission. Tells the user what to do next (websocat, export curl, chat curl).
  - **P5 — README** "API usage" section: full curl snippets for the 6 lifecycle steps + Python pointer to the bundled walkthrough + `ErrorResponse` shape doc. Added a TOC entry. The previous section list goes from 8 to 9 entries.

**Test Summary:**
- **AST parse** — all 15 changed/new files OK (3 services, 3 routers, exceptions module, main.py, 5 schema files w/ examples, integration test, python client).
- **Endpoint surface (TestClient)**:
  1. `/openapi.json` → 200, **25 paths** still present (matches Phase 3).
  2. `POST /api/v1/projects {}` → **422** with `code='validation_error'`, `detail='body.name: Field required'`, `extra.errors[0].loc=['body','name']`.
  3. `POST /api/v1/projects {name:'x', task_type:'bogus'}` → **422** `code='validation_error'`.
  4. `GET /api/v1/does-not-exist` → **404** `{detail:'Not Found', code:'not_found', extra:None}` (confirms Starlette's routing-layer HTTPException IS caught by the handler — the bug fix).
- **OpenAPI examples present** in the schema components: `ProjectCreate=3`, `SDGRequestWithSeed=1`, `SDGRequestDescriptionOnly=2`, `ManualTrainingRequest=1`, `HPOTrainingRequest=1`, `EvaluationCreate=1`, `ModelExportRequest=2`.
- **Service imports** without DB / mlflow: clean (mlflow stubbed in sys.modules; the API host has it via base deps anyway).
- Did NOT run the live integration test against compose (compose stack isn't up on this host); it will skip-collect cleanly under `pytest -m integration`.

**Decisions Made:**
- **`ErrorResponse.code` is a stable string enum** (`not_found`, `conflict`, `validation_error`, ...), separate from the HTTP status. Frontend can switch on `code` and avoid hard-coding "404 means missing project" — useful when the same status code can mean two different things (e.g., 409 for "dataset still generating" vs. 409 for "model already exported").
- **Handler registers on `starlette.exceptions.HTTPException`**, not FastAPI's. FastAPI's HTTPException subclasses Starlette's; registering on the parent catches BOTH (a) HTTPExceptions raised by our service code, AND (b) Starlette-internal 404/405 from the routing layer. If we'd only registered on FastAPI's, unknown-route 404s would slip through with the bare `{"detail":"Not Found"}` body and break the frontend's error-rendering contract.
- **Validation errors keep details under `extra`**, not at the top level. Pydantic emits a list-of-dicts; the top-level `detail` becomes a single human-readable line so `code: "validation_error"` + `detail: "body.name: Field required"` is enough for a log-aggregation tool. Frontend can still deep-render the per-field list from `extra.errors`.
- **Unhandled-exception correlation id is 12 hex chars** (uuid hex's first 12). Long enough for global uniqueness within a sane log retention window, short enough that a user can quote it in a bug report. The full uuid is overkill.
- **Cancel is hard-revoke** (`terminate=True, signal=SIGTERM`). The training tasks have a `finally` block that runs `torch.cuda.empty_cache()` regardless of how they exit, so SIGTERM is safe — Celery's default grace period gives the worker a few seconds to clean up before SIGKILL.
- **Preview reads JSONL line-by-line and stops at `limit`** rather than loading the whole MinIO object. For datasets with 10k rows × 1KB each (10MB), buffering all of it just to slice the first 20 rows wastes memory on the API container. The line-walker bails after `limit` rows.
- **Seed upload tolerates JSON-array OR JSONL** (auto-detected from leading `[`). Less friction for users who exported their seed data from another tool. Per-row validation collects `invalid_rows: [int]` indexes so users get a clear "rows 3, 7, 12 failed" report rather than a 422-and-give-up.
- **Integration test skips module-level** if compose isn't up; doesn't fail. This is intentional — `pytest -m "not integration"` (the unit-only run) shouldn't crash because of a missing API server. The CI runs full integration in a separate compose-up job.

**PoC complete.** All 8 phases finished, 0 stubbed endpoints, 25 paths documented, integration scaffold + 2 example clients shipped.

**Next Action:**
→ The build is feature-complete per `require.md`. Recommended next steps (post-handover):
  1. Run `docker compose up -d` and execute `pytest -m integration tests/integration/test_full_flow.py -v` against a real GPU host.
  2. Write ADR-006 (asyncpg) — flagged in Session 1 as needing one.
  3. (Optional) Phase-8.5 polish: streaming inference (`stream: true` SSE), GGUF download as zip/tar, near-dup detection in SDG (Phase-4 D4 carry-over), HPO retrain on a held-out eval set.

**Blockers:** None.

---

## Session 7 — Phase 7: Inference & Evaluation (2026-05-08)

**Who:** Claude (Opus 4.7) + parks (developer)
**Status:** Finished — awaiting Phase 7 approval

**Why & What:**
- Phase 7 is the largest phase by file count (7 sub-tasks). Pattern reuse from Phases 4–6: pure-domain code in `ai_engine/`, infra adapters in `workers/`, application services in `api/services/`, routers stay thin. All endpoints from `require.md`'s API list are now wired (no more 501s except for routes pre-Phase-3 that this phase didn't own — projects, dataset CRUD, training-list/get/cancel/mlflow-url; those were never Phase-7 scope).
  - **I1 / I7 — Ollama integration + GGUF/SafeTensors export**:
    - `workers/ollama_client.py`: sync httpx wrapper for `/api/tags`, `/api/create`, `/api/show`, `/api/delete`. `health()` returns False (not raise) on unreachable daemon. `build_modelfile(base_gguf_path, template?, system?, parameter_lines?)` helper produces the Modelfile string for `POST /api/create`.
    - `workers/tasks/model_export.py` (Celery task `model.export`): downloads the LoRA adapter dir from MinIO into a tempdir, re-loads base in 4-bit + attaches adapter via `peft.PeftModel.from_pretrained`, calls `model.save_pretrained_gguf(out_dir, tokenizer, quantization_method=…)` (default `q4_k_m`) **or** `model.save_pretrained_merged(out_dir, tokenizer, save_method='merged_16bit')`. Uploads result to `models/exports/{artifact_id}/{format}/`. For GGUF: probes Ollama via `health()`; if reachable, builds a Modelfile and calls `create_from_modelfile(tag='slm/{first8}', modelfile)`. Persists URIs + `ollama_model_tag` on the artifact row. GPU cleanup in `finally`.
    - `api/services/model_service.py` + `api/routers/models.py` wired: list (paginated, filter by `project_id` via JOIN through `training_jobs`), get, export (returns 202 + `job_id`), download (streams the first GGUF blob; SafeTensors/LoRA are multi-file and surface a 400 with the URI for the caller to fetch via MinIO directly).
    - Local module-import dance: `_download_prefix(minio, bucket, prefix, local_dir)` walks `list_objects(recursive=True)` and writes files preserving relative paths.
  - **I2 — Inference router (OpenAI-compat proxy)**:
    - `api/services/inference_service.py`: async httpx client; forwards `chat/completions`, `completions`, `models` to Ollama's `/v1/...`. Long read-timeout (300s), generous connect (5s).
    - `_resolve_model_tag(db, identifier)`: accepts EITHER a UUID (resolves to `ModelArtifact.ollama_model_tag` — 404 if missing artifact, 409 if not exported) OR a literal Ollama tag (passthrough; lets callers hit base models directly like `llama3.2:3b`).
    - Streaming explicitly rejected with 400 — adding SSE proxying isn't in scope for the PoC.
    - Errors mapped: connect/timeout to 502 BAD_GATEWAY; 404 from daemon → 404 to client.
    - `api/routers/inference.py` simplified to thin pass-through.
  - **I3-I5 — Per-task metrics** (pure domain, deferred imports):
    - `metrics_classification.py`: sklearn `accuracy_score`, `f1_score(macro)`, per-label F1 dict, `confusion_matrix`. Tracks `out_of_set_predictions` (count of predictions outside the closed label set) so the frontend can flag a model that's hallucinating labels.
    - `metrics_tool_calling.py`: stdlib-only. `_safe_parse(raw)` tolerates ```json``` fences, then validates `name: str` + `parameters: dict`. Computes:
      - `json_validity` = parsed_count / n
      - `name_accuracy` = name_match_count / n (denominator is total, NOT parsed)
      - `arg_accuracy` = arg_match_count / **name-match** count (zero-safe)
      - `exact_match` = both name + args correct, divided by n
      `_params_equal()` is a lenient deep-equal: bool stays distinct from int (so `True != 1`), int↔float coerce on equal numeric value.
    - `metrics_qa.py`: stdlib-only EM (case+whitespace normalised); ROUGE-1/2/L via `rouge_score.RougeScorer`; corpus BLEU via `sacrebleu.metrics.BLEU` (normalised to 0–1 from sacrebleu's 0–100).
  - **I6 — LLM-as-judge** (`ai_engine/evaluation/llm_judge.py`):
    - Uses the existing `OpenRouterClient`. System prompt defines a 1–5 rubric (1=unrelated, 5=fully correct). Asks for `{"score": int, "reason": str}` with `response_format={"type": "json_object"}`.
    - Per-row call (no batching — judge models tolerate parallelism but we keep it simple). Mean is computed across **successful** rows; failed parses go to `JudgeBatchResult.skipped` so a few parse failures don't drag the score.
    - `_parse_judge_response` enforces score is an int in [1, 5]; tolerates fenced markdown.
  - **Evaluation worker + service + router**:
    - `workers/tasks/evaluation.py` (Celery task `evaluation.run`):
      1. Load `EvaluationRun` + `ModelArtifact` (must have `ollama_model_tag`) + `Dataset` (must have `storage_uri`).
      2. Build per-row inference prompt (`_prompt_and_gold`): classification → text, tool_calling → "Available tools:..." + question, qa → question. Gold pulled from row's label/answer field.
      3. Loop POST `/v1/chat/completions` to Ollama at `temperature=0.0, stream=False`.
      4. `_postprocess`: classification keeps only the first line.
      5. Dispatch to per-task metric module.
      6. If `use_llm_judge=True` and task is QA or tool_calling, additionally run `llm_judge.judge_rows()`. Adds `llm_judge_skipped_rows` to the metrics dict.
      7. Persist `metrics_json`, `llm_judge_score`, `llm_judge_model`, flip COMPLETED.
    - `api/services/evaluation_service.py`: validates artifact has Ollama tag, dataset is ready; `compare_evaluations` pivots metrics into `metric → {eval_id → value}` with explicit `None` for missing metrics across runs (so the frontend can render a complete grid without dropping rows).
    - `api/routers/evaluations.py` simplified.

**Test Summary:**
- **AST parse** — all 14 changed/new files OK.
- **Celery task registration** (with mlflow stubbed): `evaluation.run`, `model.export`, `sdg.generate`, `train.hpo`, `train.manual` — exact 5-task list, no extras, no missing.
- **Tool-calling metrics** with hand-built 4-row test cases:
  - Row 1: exact match → counts toward all four ratios.
  - Row 2: same name, wrong arg value → counts toward `json_validity` + `name_accuracy` but not `arg_accuracy` or `exact_match`.
  - Row 3: not-JSON output → counts only against the denominator on `json_validity`.
  - Row 4: parsed but wrong tool name → counts toward `json_validity` only.
  - Result: `json=0.75, name=0.5, arg=0.5, exact=0.25` ✓ all match expected.
- **QA helpers**: `_normalize('  Hello  WORLD ') == 'hello world'`; `_exact_match` matches case-insensitively.
- **Classification metrics**: imports OK; sklearn correctly raises `ImportError` on the host without `[eval]` extras (smoke confirms deferred import).
- **Judge parser**: parses canonical JSON; tolerates ```json fences; rejects out-of-range score (`9` → `ValueError`).
- **Modelfile builder**: emits well-formed `FROM "..."`, `TEMPLATE """..."""`, `SYSTEM """..."""`, `PARAMETER ...` lines.
- **OllamaClient.health()** on unreachable host returns `False` without raising.
- **TestClient endpoint validation** (still 25 paths in OpenAPI):
  1. `POST /api/v1/inference/chat/completions` empty body → 422.
  2. Same with `stream: true` → **400** (our `inference_service` gate).
  3. `POST /api/v1/models/{uuid}/export` with `format: "invalid"` → 422 (Pydantic gates `ArtifactFormat` enum).
  4. `POST /api/v1/evaluations` empty body → 422.
  5. `POST /api/v1/evaluations/compare` with 1 eval id → 422 (schema `min_length=2`).
- Did NOT run a live Ollama / sklearn / rouge_score path — those are integration smoke tests for first compose-up with the RTX 3060.

**Decisions Made:**
- **Ollama integration is best-effort** during export. If the daemon is unreachable when `model.export` runs, we still upload the GGUF to MinIO, but skip `create_from_modelfile` and leave `ollama_model_tag = NULL`. Re-running export later (or registering manually) populates the tag. Avoids hard-failing an export run because Ollama crashed mid-stack-up.
- **Inference identifier is overloaded.** `body.model` accepts UUID (looked up against ModelArtifact) OR Ollama tag (passthrough). The UUID path is friendlier for the frontend ("here's the model you trained") and the passthrough lets ops people poke base models directly. Documented in `_resolve_model_tag` docstring.
- **Streaming rejected with 400, not 501.** `stream: true` is well-defined in OpenAI's spec but adding SSE proxying is meaningfully more work (asyncio + chunked transfer + reconnect handling) than the PoC needs. The 400 + clear message tells frontend authors to set `stream: false` rather than waiting for it to be implemented.
- **GGUF download is the only single-file stream**; SafeTensors and LoRA returns are explicitly NOT zipped server-side. Big merged-weights tarballs would need either streaming-tar or temp-file buffering, both of which are footguns. The artifact response already exposes the s3:// URI; sophisticated callers can iterate the prefix via the MinIO client.
- **Tool-calling `arg_accuracy` denominator is name-matches, not total**. This makes the metric meaningful as "given the model picked the right tool, did it call it correctly" — which is the question users actually ask. `exact_match` covers the all-up "got both right" view.
- **LLM judge runs sequentially** per row. Could parallelize with httpx async, but: (a) judge models on OpenRouter are rate-limited per-org, (b) the judge call is 1 row × ~200 tokens, tiny relative to fine-tune wall-clock, (c) deterministic order makes debugging trivial. Revisit in Phase 8 if eval throughput becomes a bottleneck.
- **Compare emits `None` for missing metrics**, not skipped keys. Frontend rendering as a grid means missing values must be addressable as `None` in the per-evaluation column rather than "this metric doesn't exist for this run".
- **Ollama tag scheme: `slm/{artifact_id[:8]}`**. Eight chars of the UUID is plenty unique within one platform install; the full UUID would be ugly in `ollama list` output. Frontend hides this detail and shows `ModelArtifact.name`.

**Next Action:**
→ Get developer sign-off on Phase 7, then begin **Phase 8: Polish** (`P1`–`P5`):
  1. `P1` — comprehensive error handling: HTTP codes + uniform `ErrorResponse` body across all routers
  2. `P2` — OpenAPI request/response examples (per task type) using `openapi_extra` or schema `examples=[...]`
  3. `P3` — end-to-end integration test (one full flow per task type — requires compose stack)
  4. `P4` — example client scripts in `examples/` (Python + curl)
  5. `P5` — README API usage examples with curl + Python snippets

**Blockers:** None.

---

## Session 6 — Phase 6: HPO Pipeline (2026-05-07)

**Who:** Claude (Opus 4.7) + parks (developer)
**Status:** Finished — awaiting Phase 6 approval

**Why & What:**
- Completed Phase 6 (H1–H4). Pattern matches Phase 5: domain code in `ai_engine/hpo/` is pure (deferred imports for `optuna`/`mlflow`/training stack); `workers/tasks/hpo_training.py` is the Celery+infra adapter; the API service replaces its 501 stub.
  - **H1** — `ai_engine/hpo/search_spaces.py`: `sample_config(trial, search_space, fixed_config) -> SampledTrial(config, params)` walks the explicit allow-list of 10 tunables and dispatches to `trial.suggest_float/int/categorical`. LoRA-side knobs (`lora_r`, `lora_alpha`, `lora_dropout`) merge into a fresh `LoRAConfig`. The returned `params` dict uses dotted keys (`lora.r`, `lora.alpha`, `lora.dropout`) so MLflow lays them out cleanly. Plus `best_params_to_config(study.best_params, fixed_config)` for the final retrain step.
  - **H2** — `ai_engine/hpo/optuna_objective.py`: `HPOObjective` dataclass — callable passed to `study.optimize`. Each `__call__(trial)`:
    1. Sample config via H1.
    2. Open a **nested** MLflow run named `trial-{N:03d}` with tags `{trial_number, celery_job_id, task_type, base_model, mode=hpo-trial}`.
    3. Build & run `UnslothTrainer` for that trial (per-trial tempdir, removed on exit — only the final-best run keeps weights).
    4. Pull the configured objective metric from `result.metrics` (fallback through `eval_*`/`train_*` aliases via `_extract_metric`).
    5. `trial.report(value, step)` + `trial.should_prune()` → raise `TrialPruned`.
    6. Update best-so-far (direction-aware), fire the `on_trial_done(TrialOutcome)` callback (the worker turns it into `HPOProgress`).
  - **H3** — `workers/tasks/hpo_training.py` (Celery task `train.hpo`, registered in `celery_app.include`):
    - Lifecycle parallels `train.manual`: load row → flip `RUNNING` → fetch JSONL → parent MLflow run → log `n_trials` / `direction` / `objective_metric` / flat `search_space` + `fixed_config` → `optuna.create_study(direction, sampler, pruner)` with `_build_sampler`/`_build_pruner` switching on the schema's enum values → `study.optimize(objective, n_trials, timeout, catch=(Exception,))`.
    - **Final retrain on `study.best_params`** in a `nested=True` child run named `best`. The adapter from this run is what we persist to MinIO at `models/adapters/{training_id}/`.
    - Persists `ModelArtifact` + flips `TrainingJob.status=COMPLETED` + writes `best_metric_value` and `best_params_json` (these columns exist on the schema from Phase 2 — finally being filled).
    - On exception: `FAILED` + `error_message`, publish `JobFailed`. Always: `torch.cuda.empty_cache()` + `gc.collect()` in `finally`.
    - **Inner per-step training progress is suppressed during HPO trials** — passing a chatty WS firehose across N trials would drown the frontend. Only the final-`best` retrain emits per-step `TrainingProgress`. Trial-level activity goes via `HPOProgress` (one event per trial completion).
  - **H4** — `api/services/training_service.py`: `submit_hpo_training_job` replaces the 501 stub. Same validation chain as `submit_manual_training_job` (project + dataset + task-type-match + storage_uri + base-model allowlist), with one extra gate: `n_trials <= settings.default_hpo_max_trials` (defense-in-depth on top of the schema's hard cap of 100). Persists `request.hpo_config.model_dump(mode='json')` into `config_json` and dispatches to `train.hpo`.

**Test Summary:**
- **AST parse**: all 5 changed/new files OK.
- **Import smoke (host venv, no torch/unsloth/optuna/mlflow)**: with `mlflow` stubbed in `sys.modules`, `ai_engine.hpo.search_spaces`, `ai_engine.hpo.optuna_objective`, `workers.tasks.hpo_training`, `api.services.training_service` all import cleanly. Module-level imports stay light; `import optuna` lives inside the task body.
- **`sample_config` exercise** with a fake Trial that returns midpoints / lows / first-choice:
  - All 6 sampled fields produce expected types: `learning_rate=5.05e-4`, `epochs=2`, `batch=2`, `scheduler='linear'`, `lora.r=8`, `lora.dropout=0.100`.
  - `best_params_to_config({'learning_rate': 5e-4, 'num_train_epochs': 4, 'lora_r': 32, 'lora_dropout': 0.1}, fixed)` round-trips into a valid `ManualTrainingConfig`.
- **Celery task registration** (with mlflow stubbed): `sdg.generate`, `train.manual`, `train.hpo` all register on `celery_app.tasks`.
- **TestClient** (3 cases on top of Phase 5):
  1. `GET /openapi.json` still 200, 25 paths.
  2. HPO `TrainingRequest` discriminator parses (mode=hpo, n_trials=5, sampler=tpe, pruner=median, mixed search_space).
  3. Empty `search_space: {}` rejected at schema; `n_trials=200` rejected at schema (>100 cap).
- **`HPOObjective` behavior test** (with stubbed UnslothTrainer, stubbed mlflow, fake Optuna trial):
  1. Trial 0 returns `eval_loss=0.30` (the fake metric); `on_trial_done` fires with `best_value=0.30`.
  2. Trial 1 same metric; `best_value` stays at 0.30 (tie ≠ improvement).
  3. Pruning path: `trial.should_prune() == True` → `TrialPruned` raised; final `on_trial_done` callback marks `pruned=True`.
- Did NOT run a live worker → GPU → real Optuna study — that's an integration test for Phase 8 once the compose stack is up with the RTX 3060 attached.

**Decisions Made:**
- **Final-best retrain inside the parent MLflow run** as a `nested=True` child named `best`, NOT as an unrelated top-level run. This way the parent run is a self-contained "HPO study" view — UI shows the search params at the top, child trials, and one `best` retrain whose adapter is the deliverable.
- **One adapter per HPO job** (the final-best retrain). Trial adapters are throwaway tempdirs cleaned up in their own `finally`. Persisting all N trial adapters would 5–10× our MinIO footprint per study; if a user wants to re-evaluate a specific trial they have its `best_params_json` and can rerun.
- **`catch=(Exception,)` on `study.optimize`.** Optuna by default re-raises any non-`TrialPruned` exception out of the study, killing all remaining trials. We swallow into the study's per-trial fail count and let it carry on — one bad trial (e.g., transient OOM) shouldn't waste the study.
- **`n_trials` cap at the service layer** (`settings.default_hpo_max_trials`, default 10) on top of the schema's 2–100 hard range. Prevents an over-eager request from spending hours of GPU before someone notices.
- **Inner per-step `TrainingProgress` suppressed during trials.** Passing `inner_progress_publish=None` to `HPOObjective` deliberately silences the per-step Trainer callback during HPO trials; only `HPOProgress` events (one per trial) hit the WS. Final-best retrain re-enables `TrainingProgress` so the frontend sees normal training stream for the deliverable.
- **`HPOProgress.last_trial_pruned`** flag is set even when the trial completed normally; pruning is signaled via the `TrialPruned`-emitted callback before re-raise, so the frontend can render "trial 5 pruned at step N" correctly.
- **`_coerce_params`** stringifies any non-primitive value before publishing on `HPOProgress.current_params` / `best_params` (the WS schema only allows `str|int|float|bool`). Currently a no-op for our search space, but defends against future categorical lists or nested dicts.

**Next Action:**
→ Get developer sign-off on Phase 6, then begin **Phase 7: Inference & Evaluation** (`I1`–`I7`):
  1. `I1` — Ollama integration: convert LoRA → GGUF (`unsloth.save.save_to_gguf`) + `ollama create model:tag -f Modelfile`
  2. `I2` — OpenAI-compatible inference router (`/v1/chat/completions`, `/v1/completions`, `/v1/models`) backed by Ollama
  3. `I3`–`I5` — per-task metrics: classification (accuracy, F1, confusion matrix), tool_calling (JSON validity, name accuracy, arg accuracy), QA (ROUGE, BLEU, exact match)
  4. `I6` — `ai_engine/evaluation/llm_judge.py` — OpenRouter-as-judge for QA + tool_calling free-form responses
  5. `I7` — `POST /api/v1/models/{id}/export` (GGUF, SafeTensors)

**Blockers:** None.

---

## Session 5 — Phase 5: Manual Training Pipeline (2026-05-07)

**Who:** Claude (Opus 4.7) + parks (developer)
**Status:** Finished — awaiting Phase 5 approval

**Why & What:**
- Completed Phase 5 (T1–T6). The hexagonal split mirrors Phase 4: `ai_engine/training/*` is pure domain code (deferred imports for `unsloth`/`torch`/`transformers`/`trl`/`datasets`); `workers/tasks/training.py` is the Celery+infra adapter; `api/services/training_service.py` is the application service.
  - **Pre-existing from before this session:** `ai_engine/training/data_formatters.py` (T2), `ai_engine/training/callbacks.py` (T3), `ai_engine/training/mlflow_logger.py` (T4) were already on disk but undocumented in WORKING_LOG. Reviewed them for this session — all three follow the established conventions and are picked up by T1/T5.
  - **T1** — `ai_engine/training/unsloth_trainer.py`: `UnslothTrainer` class wraps `FastLanguageModel.from_pretrained(load_in_4bit=True, …)` + `FastLanguageModel.get_peft_model(...)` + TRL `SFTTrainer`. Heavy deps imported lazily inside `train()`; module is importable on the API host without `[training]` extras. Auto-selects bf16 on Ampere+ (`torch.cuda.get_device_capability()[0] >= 8`), fp16 elsewhere. Saves the LoRA adapter to a tempdir; uses Hugging Face's `dataset_text_field="text"` after passing rows through `get_formatter(task_type, tool_definitions)`. Eval split is 10% by default (skipped for tiny datasets). Returns `TrainingResult(adapter_dir, final_train_loss, final_eval_loss, train_runtime_seconds, train_samples_per_second, steps_completed, metrics)`.
  - **T5** — `workers/tasks/training.py`: Celery task `train.manual` registered in `workers/celery_app.py` (added to `include`). Flow: load `TrainingJob` + `Dataset` (sync session) → flip status `RUNNING` + `started_at` → fetch JSONL from MinIO via the URI on `Dataset.storage_uri` → open MLflow run + persist `mlflow_run_id`+`mlflow_experiment_id` back on the row → log flat params (`base_model`, `task_type`, `num_samples`, `config.*`) → instantiate `UnslothTrainer` → train under a `make_progress_callback(job_id, publish)` callback → upload adapter dir to `models/adapters/{training_id}/` via new `put_directory()` → insert `ModelArtifact(lora_adapter_uri, size_mb, mlflow_run_id, …)` → flip `TrainingJob.status=COMPLETED` + `ended_at` → publish `JobCompleted` to `job:{id}`. On exception: persist `FAILED` + `error_message`, publish `JobFailed`, re-raise. **Always** runs `torch.cuda.empty_cache()` + `torch.cuda.ipc_collect()` + `gc.collect()` in a `finally` block (ADR-002 STRICT). Tool definitions are recovered from `Dataset.generation_metadata['tool_definitions']` so the formatter can rebuild the ChatML system prompt.
  - **T6** — `api/services/training_service.py` + `api/routers/trainings.py` wiring: `submit_manual_training_job(db, request)` validates project exists, dataset exists + belongs to project + matches task_type + has `storage_uri` + `num_samples > 0`, validates `base_model` against `SUPPORTED_BASE_MODELS` allowlist (ADR-002), inserts `TrainingJob` (status=PENDING), enqueues `train.manual.apply_async(kwargs={"training_id": …})`, stashes `celery_task_id`, commits, returns `TrainingJobAcceptedResponse(job_id, training_id, status=PENDING, websocket_url='/ws/jobs/{job_id}')`. HPO mode dispatches to `submit_hpo_training_job` which raises 501 (Phase 6). Router replaces the 501 stub with a `body.mode is TrainingMode.MANUAL` branch.
  - **Storage helpers added** (`workers/storage.py`): `parse_s3_uri(uri) -> (bucket, key)` strict s3:// parser, `get_jsonl(client, bucket, key) -> list[dict]` reader (skips blank lines, raises `JSONDecodeError` on malformed lines), `put_directory(client, bucket, key_prefix, local_dir) -> (file_count, total_bytes)` recursive uploader for the saved adapter directory.

**Test Summary:**
- **AST parse** — all 6 changed/new files parse OK.
- **Import smoke (host venv, no torch/unsloth/mlflow)**: `ai_engine.training.unsloth_trainer`, `workers.storage`, `api.services.training_service` import cleanly. Allowlist is 6 model ids and includes `unsloth/Llama-3.2-3B-Instruct-bnb-4bit` (the default).
- **`parse_s3_uri` behavior**: `s3://datasets/sdg/abc.jsonl` → `('datasets', 'sdg/abc.jsonl')`; `http://...` → `ValueError`; `s3://nokey` → `ValueError`.
- **Celery task registration** (with mlflow stubbed in `sys.modules`): both `sdg.generate` and `train.manual` register on `celery_app.tasks`.
- **TestClient (5 cases)**:
  1. `GET /openapi.json` still 200, all 25 paths still present.
  2. `POST /api/v1/trainings` empty body → **422**.
  3. `ManualTrainingRequest` happy-path payload parses (mode=manual, default LoRA, custom hyperparams).
  4. `HPOTrainingRequest` discriminator picks the HPO branch correctly with mixed search_space.
  5. Negative `learning_rate` → `ValidationError` (gates before service).
- Did NOT run a live worker → GPU → MinIO → MLflow path — that's a docker-compose smoke test (Phase 8 polish or first real deploy with the RTX 3060 attached).

**Decisions Made:**
- **API process imports the worker task callable** (`from workers.tasks.training import train_manual`) — same convention as Phase 4. Safe because: (a) `mlflow` is in base deps (pyproject `[project.dependencies]`, line 42), (b) all training-only deps are deferred imports inside `UnslothTrainer.train()`, (c) the Celery task body itself doesn't import torch/unsloth/transformers/trl at module top-level.
- **`save_strategy="no"`** in `TrainingArguments`. We don't want HuggingFace writing intermediate checkpoints into the worker's local FS — the only artifact we care about is the final LoRA adapter, which we upload to MinIO ourselves. Saves disk + avoids a race between HF and our uploader.
- **`report_to=[]`** disables HF's built-in MLflow integration. Our `make_progress_callback` already mirrors loss/lr/grad-norm into MLflow via `mlflow.log_metric` on every `on_log`, and the trainer's HF integration would double-log + create duplicate runs.
- **`disable_tqdm=True`** because progress streams via the WS callback. Logs in container stdout would just be noise.
- **bf16 on Ampere+, fp16 otherwise.** RTX 3060 (consumer Ampere, capability 8.6) → bf16. Older Turing (T4) → fp16. The auto-detect lives in `_supports_bf16()` in `unsloth_trainer.py`.
- **Adapter persistence to MinIO via `put_directory()`** rather than tarball/zip. Keeps the adapter browsable in the MinIO UI; Phase 7 (Ollama / GGUF conversion) can read individual files (adapter_config.json, adapter_model.safetensors, tokenizer files) without unpacking.
- **`tool_definitions` survive across SDG → training** by stashing them in `Dataset.generation_metadata['tool_definitions']`. The training task pulls them out when building the formatter so the ChatML system prompt mirrors what was used during SDG. (Note: the SDG service writes this for `description_only` runs but does NOT yet for `with_seed` runs — generic fallback prompt is used there. Acceptable for PoC; tracked as a Phase-8 polish item.)
- **Eval split = 10%, skipped on `len(rows) < 4`.** Avoids `train_test_split` with degenerate sizes that would give 0-row eval sets.

**Next Action:**
→ Get developer sign-off on Phase 5, then begin **Phase 6: HPO Pipeline** (`H1`–`H4`):
  1. `H1` — `ai_engine/hpo/search_spaces.py` (translate `HPOSearchSpace` → `optuna.Trial.suggest_*` calls)
  2. `H2` — `ai_engine/hpo/optuna_objective.py` (one trial = one nested MLflow run; reuses `UnslothTrainer`)
  3. `H3` — `workers/tasks/training.py` add `train.hpo` task (parent run + nested trials; publishes `HPOProgress`)
  4. `H4` — wire `submit_hpo_training_job` (replace its 501)

**Blockers:** None.

---

## Session 4 — Phase 4: SDG Pipeline (2026-05-07)

**Who:** Claude (Opus 4.7) + parks (developer)
**Status:** Finished — awaiting Phase 4 approval

**Why & What:**
- Built the full Synthetic Data Generation pipeline (D1–D6). The hexagonal split is honored: `ai_engine/data_gen/*` is pure domain code with no FastAPI/Celery imports; `workers/*` is the Celery + infra adapter; `api/services/sdg_service.py` is the application service that ties the API request to the worker.
  - **D1** — `ai_engine/data_gen/openrouter_client.py`: sync `OpenRouterClient` wrapping `OpenAI(base_url='https://openrouter.ai/api/v1')`; tenacity retries 4× on `APIConnectionError`/`APITimeoutError`/`RateLimitError` with exponential backoff. `APIStatusError` (other 4xx) is logged + re-raised without retry. Returns `ChatResult(content, model, finish_reason, prompt_tokens, completion_tokens)`. Sets `HTTP-Referer` + `X-Title` headers per OpenRouter conventions.
  - **D2** — `ai_engine/data_gen/prompts.py`: `Prompt(system, user)` dataclass + dispatcher `build_prompt(task_type, sdg_mode, ...)`. Six combinations covered. Always asks teacher to return `{"samples": [...]}` so we can use `response_format={"type":"json_object"}`. Tool definitions are JSON-pretty-printed in the prompt; seeds shown verbatim.
  - **D3** — `ai_engine/data_gen/validators.py`: `validate_generated_rows()` does Pydantic schema validation first, then per-task business rules:
    - Classification: label must be in the closed `classification_labels` set.
    - Tool calling: name in `tool_definitions`; required params present; param types match (lenient JSON-schema-style — booleans aren't ints).
    - QA: schema only.
    Returns `(accepted_dicts, [ValidationFailure(index, reason), ...])` — never raises on a single bad row.
  - **D4** — `ai_engine/data_gen/deduplicator.py`: stateful `Deduplicator` with SHA-1 of normalized (whitespace-collapsed, lowercased, stripped) text. Seed-aware via `seed()`. Returns `DedupResult(unique, duplicate_count)`. Note: PoC scope is exact-dup only; near-dup (MinHash, embeddings) is Phase 8.
  - **D5** — orchestrator + worker glue:
    - `ai_engine/data_gen/generator.py` — `SyntheticDataGenerator.generate(request, progress_cb)` runs the loop: prompt → call → parse → validate → dedup → repeat until target. Tolerates markdown fences in teacher output. Bails with `SDGAbortedError` after `max_consecutive_failures` (default 5) consecutive failed batches. Emits `GenerationProgress` events (pure domain dataclass — no `job_id`).
    - `workers/celery_app.py` — Celery factory; `task_track_started=True`, `worker_prefetch_multiplier=1`, `worker_max_tasks_per_child=1` (so CUDA memory is released between training jobs in Phase 5+). Includes `workers.tasks.data_generation`.
    - `workers/sync_db.py` — sync SQLAlchemy engine (auto-rewrites `+asyncpg` → `+psycopg2`); `session_scope()` context manager that commits on clean exit, rolls back on exception.
    - `workers/storage.py` — MinIO factory + `put_jsonl(bucket, key, rows)` + `s3_uri()` helper.
    - `workers/progress.py` — sync Redis publisher; `sync_redis_scope()` context + `publish_ws_message(client, job_id, BaseModel)` (auto-serializes Pydantic to JSON).
    - `workers/tasks/data_generation.py` — the Celery task `sdg.generate`. Translates `GenerationProgress` → `SDGProgress` (with `job_id` from `self.request.id`) and publishes to `job:{id}`. On success: writes `sdg/{dataset_id}.jsonl` to the `datasets` bucket, updates `Dataset.num_samples` + `storage_uri` + `size_bytes` + `generation_metadata`, publishes `JobCompleted`. On failure: publishes `JobFailed` (best-effort, never masks the original) and re-raises.
  - **D6** — `api/services/sdg_service.py` + endpoint wired: looks up the project, checks `task_type` matches, inserts the placeholder `Dataset` (source=sdg, num_samples=0), enqueues the Celery task, stashes `celery_task_id` into `generation_metadata`, returns `SDGJobAcceptedResponse(job_id, dataset_id, status=PENDING, websocket_url='/ws/jobs/{job_id}')`. Local import of the Celery task keeps API process from eagerly loading worker-only deps (minio, etc.).

**Test Summary:**
- **AST + import**: all new modules parse OK and import cleanly. `celery_app.tasks` shows `'sdg.generate'` registered.
- **Generator unit tests** (5/5 with a scripted `FakeClient`):
  1. Classification with closed labels — 5 valid + 2 rejected (1 bad-label, 1 schema), 0 dups across 2 batches.
  2. Dedup vs seed — 3 dups detected (incl. case + whitespace normalization), 2 fresh kept.
  3. Tool-calling business rules — unknown tool name + missing required param + wrong param type all rejected (3 of 5).
  4. Parse-error retry — succeeds on attempt 3 after 2 unparseable responses.
  5. Abort path — `SDGAbortedError` raised after 3 consecutive failed batches.
- **Endpoint validation gate** (3/3 via TestClient):
  1. `task_description` < 10 chars → **422** (Pydantic gates before service).
  2. `description_only` + classification without `classification_config` → **422** (with custom message).
  3. `/openapi.json` still renders; `POST /api/v1/datasets/generate` documented with `responses.202`.
- Did NOT run the live worker → Redis → MinIO → DB path — that's a docker-compose smoke test (Phase 8 polish or first real deploy).

**Decisions Made:**
- **Sync OpenRouter client** (not async). The consumer is a Celery worker, which is sync; making the client sync removes event-loop juggling. If the API process needs async (e.g. inline LLM judge in Phase 7), build a parallel `AsyncOpenRouterClient`.
- **Domain progress vs WS progress** are separate types. `ai_engine.data_gen.GenerationProgress` is a frozen dataclass with no `job_id` (pure domain). The Celery task wraps it into `api.schemas.progress.SDGProgress` with the celery task id and timestamp. Keeps the orchestrator independent of the Celery/WS contract.
- **Exact-dup only for PoC.** Near-dup (MinHash / embedding cosine) deferred to Phase 8. Documented in `deduplicator.py` docstring.
- **`worker_max_tasks_per_child=1`** in Celery config. This makes the worker process recycle after each task — critical for releasing CUDA memory between training jobs in Phase 5+. Costs a process-fork per task, which is fine for our throughput.
- **`max_consecutive_failures=5`** sentinel. If 5 batches in a row can't produce parseable JSON, abort the run rather than spinning forever burning OpenRouter credits. The error message includes the last parse failure for debugging.
- **`SDGRequest` discriminated union** — used `TypeAdapter(SDGRequest)` in the worker because `SDGRequest` is `Annotated[Union[...], Field(discriminator=...)]`, not a class. Same approach worked at the API boundary.
- **`websocket_url` is path-only** (`/ws/jobs/{job_id}`). Frontend prepends scheme + host. The API can't reliably know its public URL.

**Next Action:**
→ Get developer sign-off on Phase 4, then begin **Phase 5: Manual Training Pipeline** (`T1`–`T6`):
  1. `T1` — `ai_engine/training/unsloth_trainer.py` (4-bit load + LoRA setup)
  2. `T2` — `ai_engine/training/data_formatters.py` (cls → text/label, tool_calling → ChatML, qa → Alpaca)
  3. `T3` — `ai_engine/training/callbacks.py` (HuggingFace Trainer callback that publishes `TrainingProgress` to `job:{id}`)
  4. `T4` — `ai_engine/training/mlflow_logger.py` (params + metrics + model artifact logging)
  5. `T5` — Celery task `train_manual` w/ `torch.cuda.empty_cache()` on exit
  6. `T6` — wire `POST /trainings` mode=manual

**Blockers:** None.

---

## Session 3 — Phase 3: API Skeleton (2026-05-07)

**Who:** Claude (Opus 4.7) + parks (developer)
**Status:** Finished — awaiting Phase 3 approval

**Why & What:**
- Executed Phase 3 (A1–A5) per `TASK_TRACKER.md`. Full FastAPI surface is now live; everything except `/health`, `/api/v1/tasks`, and `/api/v1/base-models` returns 501 until later phases wire real logic.
  - **Core infrastructure**:
    - `api/core/config.py` — `Settings` (pydantic-settings) reading `.env` + env vars; `api_cors_origins` accepts CSV strings; AnyUrl for MLflow/Ollama URLs; cached `get_settings()` singleton.
    - `api/core/database.py` — async SQLAlchemy engine (asyncpg) + `async_sessionmaker` + FastAPI dependency `get_db()`.
    - `api/core/redis_client.py` — async Redis factory + `job_channel(job_id)` helper.
  - **A1** — `api/main.py`: lifespan (logs startup, disposes engine on shutdown), CORS (no creds; origins from settings), 8-tag OpenAPI metadata, `/`, `/health`, all routers mounted at `/api/v1/*`. URL credentials redacted in log lines.
  - **A2** — 7 routers under `api/routers/`:
    - `projects.py` (5 endpoints), `datasets.py` (7 incl. multipart upload-seed + JSONL download), `trainings.py` (5), `models.py` (4), `inference.py` (3 OpenAI-compat), `evaluations.py` (3). All with `Annotated[..., Depends(get_db)]` style and full pydantic validation that runs **before** the 501 stub fires (so frontend gets 422s for bad payloads, 501s for valid ones).
    - 7 new schema files: `responses.py` (`Page[T]`, `ErrorResponse`), `projects.py`, `datasets.py`, `trainings.py` (read views), `artifacts.py` (the `models` REST resource — named `artifacts` to avoid collision with `api.models` ORM package), `evaluations.py`, `inference.py` (OpenAI-compatible chat / completion / model list types), `tasks_meta.py`.
  - **A3** — `api/routers/websocket.py`: WebSocket at `/ws/jobs/{job_id}` opens a fresh Redis pubsub, subscribes to `job:{job_id}`, and races two tasks via `asyncio.wait(FIRST_COMPLETED)`:
    1. `_relay_redis_to_ws` — forwards each Redis `message` to WS as text.
    2. `_watch_client_close` — blocks on `ws.receive_text()`; raises `WebSocketDisconnect` cleanly.
    Whichever finishes first cancels the other; cleanup unsubscribes + closes pubsub + Redis. Redis errors during subscribe close the WS with code 1011.
  - **A4** — `tasks_router` exposes `GET /api/v1/tasks` (3 `TaskTypeInfo` rows with Pydantic-generated JSON schemas + canonical examples) and `GET /api/v1/tasks/{task_type}/example` (just the example).
  - **A5** — `base_models_router` exposes `GET /api/v1/base-models` returning 6 Unsloth 4-bit models from llama/qwen/gemma families. Llama 3.2 3B is marked as the default; Llama 3.2 3B is technically 3.21B params (just over the 3.0B nominal cap in ADR-002, but spec-author lists it as default in TECH_STACK.md).

**Test Summary:** 10 TestClient smoke tests, all green:
- `/health` → 200
- `/openapi.json` enumerates 25 HTTP paths across 8 tags (all from `require.md` § API Endpoints)
- `/api/v1/tasks` → 3 task types with examples
- `/api/v1/tasks/tool_calling/example` → returns the canonical tool-call example
- `/api/v1/base-models` → 6 base models
- Valid `POST /projects` body → **501** (logic deferred), invalid → **422**
- Valid SDG `with_seed` body → **501**, invalid (missing per-task config) → **422 with the custom error message** (`"description_only + classification requires classification_config.labels"`)
- Valid HPO training body → **501**
- WebSocket route present at `/ws/jobs/{job_id}` (`APIWebSocketRoute`).

**Decisions Made:**
- **Two-router file `tasks_meta.py`** (`tasks_router` + `base_models_router`). Originally tried mounting one router under both `/tasks` and `/base-models` prefixes, but the two endpoints both used path `""` and would have collided. Splitting the router objects keeps the file cohesive while letting `main.py` mount them under different prefixes.
- **Schema file naming**: the REST resource for trained models is `/api/v1/models`, but `api/models/` is the ORM package. To avoid the import shadow, the response schemas live in `api/schemas/artifacts.py` and the router in `api/routers/models.py`. The two namespaces don't collide because they're distinct packages.
- **`response_class=StreamingResponse`** on the JSONL/GGUF download endpoints. FastAPI's OpenAPI generator asserts `response_class is not None`, so the original `response_class=None` broke `/openapi.json`. Fixed by importing `StreamingResponse`.
- **Validation runs before 501.** The 501 stub is a `raise HTTPException(...)` inside the function body, so FastAPI only enters it after Pydantic validates the request. That's exactly what we want for a frontend-friendly contract: the schema works today, the logic comes later.
- **No replay on WebSocket reconnect.** Per Phase-3 PoC scope, clients only get messages published after they connect. Frontend must hit the read endpoint (e.g., `GET /trainings/{id}`) for backfill on reconnect. Documented inside `websocket.py`.

**Next Action:**
→ Get developer sign-off on Phase 3, then begin **Phase 4: SDG Pipeline** (`D1`–`D6`):
  1. `D1` — `ai_engine/data_gen/openrouter_client.py` (openai SDK pointed at OpenRouter)
  2. `D2` — `ai_engine/data_gen/prompts.py` (3 task types × 2 SDG modes = 6 templates)
  3. `D3` — `ai_engine/data_gen/validators.py` (per-task post-generation validation, building on the `data_formats` schemas)
  4. `D4` — `ai_engine/data_gen/deduplicator.py` (near-dup hashing for generated rows)
  5. `D5` — Celery task `generate_synthetic_data` that publishes `SDGProgress` to `job:{job_id}`
  6. `D6` — wire `POST /datasets/generate` to actually enqueue (replace the 501)

**Blockers:** None.

---

## Session 2 — Phase 2: Schemas & Models (2026-05-07)

**Who:** Claude (Opus 4.7) + parks (developer)
**Status:** Finished — awaiting Phase 2 approval

**Why & What:**
- Executed Phase 2 (S1–S7) per `TASK_TRACKER.md`. Built the full API contract surface and DB layer.
  - **S1** — `api/schemas/enums.py`: `TaskType`, `TrainingMode`, `SDGMode`, `JobStatus`, `DatasetSource`, `ArtifactFormat`, `WSMessageType`. All str-Enums so JSON serialization is just the `.value`.
  - **S2** — `api/schemas/data_formats.py`: `ClassificationSample`, `ToolCallingSample` (with `field_validator` enforcing answer = valid JSON containing `name` + `parameters`), `QASample`, `ToolDefinition` + `ToolParameterSpec`. Added `parse_samples(task_type, rows)` helper that uses `TypeAdapter` to validate a list of raw dicts as the right sample type.
  - **S3** — `api/schemas/sdg.py`: discriminated union `SDGRequest` (`sdg_mode` discriminator) over `SDGRequestWithSeed` (5–50 seed rows, validated to match `task_type`) and `SDGRequestDescriptionOnly` (per-task `classification_config`/`tool_calling_config` required). Plus `SDGJobAcceptedResponse`, `SeedUploadResponse`.
  - **S4** — `api/schemas/training.py`: `LoRAConfig`, `ManualTrainingConfig` (full LoRA + Trainer hyperparams). HPO support via discriminated `HPOParam` (`HPOFloatRange` / `HPOIntRange` / `HPOCategorical`) → `HPOSearchSpace` (≥1 param required) → `HPOConfig` (n_trials, objective_metric, direction, sampler, pruner, fixed_config). Top-level `TrainingRequest` discriminated by `mode`.
  - **S5** — `api/schemas/progress.py`: discriminated `WSMessage` over `SDGProgress`, `TrainingProgress`, `HPOProgress` (with optional `inner_progress`), `JobCompleted`, `JobFailed`. UTC timestamps default-factoryd in.
  - **S6** — `api/models/`: `base.py` (DeclarativeBase + naming-convention metadata + `TimestampMixin` + `uuid_pk()` + `pg_enum()` helper), then one file per entity (`project`, `dataset`, `training_job`, `model_artifact`, `evaluation_run`). All FKs use `ondelete=CASCADE` for owning relations and `RESTRICT` for read-only references (datasets ← training_jobs / evaluation_runs). `JSONB` for structured payloads.
  - **S7** — `alembic.ini` (no secrets in file; URL resolved at runtime), `alembic/env.py` (auto-rewrites `+asyncpg` → `+psycopg2` for sync alembic), `alembic/script.py.mako`, `alembic/versions/20260507_0001_initial_schema.py` — handwritten initial migration that explicitly creates the 4 enum types up front (so `job_status` shared by `training_jobs` + `evaluation_runs` doesn't collide).

**Test Summary:**
- **AST parse**: all 14 new files parse OK.
- **Import smoke test**: `api.schemas.*` and `api.models.*` import cleanly under installed `pydantic 2.x` + `sqlalchemy 2.x`. `Base.metadata.tables` lists all 5 tables.
- **Pydantic validators (6 cases)** all behave as expected:
  1. `with_seed` + classification + 8 valid seed rows → parses to `SDGRequestWithSeed`.
  2. `description_only` + tool_calling without `tool_calling_config` → `ValidationError` ✓
  3. `ToolCallingSample(answer="not json")` → `ValidationError` ✓
  4. HPO with empty `search_space: {}` → `ValidationError` ✓
  5. HPO happy path with mixed float/categorical params → parses to `HPOTrainingRequest`.
  6. `WSMessage` round-trips a `training_progress` dict → `TrainingProgress`.
- **Alembic offline render**: `alembic upgrade head --sql` (with a fake URL) emits clean Postgres DDL — 4 `CREATE TYPE`s, 5 `CREATE TABLE`s in dependency order, indexes / FKs / UNIQUEs all correctly named, server defaults `'pending'::job_status` and `now()` applied.
- Did not run a live `alembic upgrade head` against Postgres — defer to first compose-up.

**Decisions Made:**
- **Single Postgres ENUM type per logical enum.** `task_type` is shared between `projects` + `datasets`; `job_status` between `training_jobs` + `evaluation_runs`. The migration creates each enum once with `create_type=False` columns referencing it. Avoids the SAEnum auto-create-twice bug.
- **`celery_task_id` is the public `job_id`.** Stored on `TrainingJob` and `EvaluationRun` with a unique index — this is what frontend passes to `WS /ws/jobs/{job_id}`. Per require.md note 6.
- **Naming convention metadata.** All FKs/indexes/uniques follow `{kind}_{table}_{cols}_{ref}`. Future autogenerate diffs will be stable across environments.
- **`HPOSearchSpace` is an explicit allow-list** (10 specific tunable params), not an open dict. Means adding a new tunable hyperparameter requires a schema change + matching code in `ai_engine/hpo/search_spaces.py` (Phase 6) — by design.
- **Pinned a small dep set into the local venv** (`pydantic`, `sqlalchemy`, `alembic`, `psycopg2-binary`) to enable host-side smoke tests. The full `[training]` extras still belong only in the worker container (huge CUDA/torch deps).

**Next Action:**
→ Get developer sign-off on Phase 2, then begin **Phase 3: API Skeleton** (`A1`–`A5`):
  1. `A1` — FastAPI app w/ CORS + OpenAPI metadata + lifespan
  2. `A2` — Routers mounted (return 501 for not-yet-implemented logic)
  3. `A3` — `WS /ws/jobs/{job_id}` + Redis pub/sub bridge
  4. `A4` — `GET /api/v1/tasks` (static metadata: returns task schemas as JSON Schema)
  5. `A5` — `GET /api/v1/base-models` (static metadata)

  Phase 3 will also need `api/core/config.py` (pydantic-settings) and `api/core/database.py` (async engine + session). I'll lift those alongside A1.

**Blockers:** None.

---

## Session 1 — Phase 1: Foundation (2026-05-07)

**Who:** Claude (Opus 4.7) + parks (developer)
**Status:** Finished — awaiting Phase 1 approval

**Why & What:**
- Executed Phase 1 (F1–F5) per `TASK_TRACKER.md`:
  - **F1** — Scaffolded full directory tree: `api/{core,routers,schemas,models,services}`, `workers/tasks`, `ai_engine/{data_gen,training,hpo,evaluation}`, `tests/{unit,integration}`, `examples/`, `docker/`, `alembic/versions`, `scripts/`. Each Python package has an `__init__.py` with a one-line module docstring. Added `.gitignore` (Python + .env + ML artifacts + Docker volumes).
  - **F2** — Wrote [`docker-compose.yml`](./docker-compose.yml) with 7 services (postgres, redis, minio, mlflow, api, worker, ollama) + a one-shot `minio-init` job that creates the 3 buckets. GPU services use `runtime: nvidia` + `deploy.resources.reservations.devices` per ADR-001. Healthchecks gate `depends_on` for postgres/redis/minio/mlflow. Three Dockerfiles in `docker/`:
    - `mlflow.Dockerfile` — `python:3.11-slim` + mlflow 2.19 + psycopg2 + boto3
    - `api.Dockerfile` — `python:3.11-slim`, installs `pip install -e .`
    - `worker.Dockerfile` — `pytorch/pytorch:2.5.1-cuda12.1-cudnn9-runtime`, installs `[training,eval]` extras
  - **F3** — Wrote [`.env.example`](./.env.example) covering Postgres, Redis, MinIO + bucket names, MLflow port, OpenRouter (key + teacher model + referer), default base model, HPO trial cap, Ollama port, LLM judge model.
  - **F4** — Wrote [`pyproject.toml`](./pyproject.toml) with all locked deps from ADR-001 + `[training]`, `[eval]`, `[dev]` extras. Added Ruff, Pytest, Mypy config. Setuptools `packages.find` includes `api*`, `workers*`, `ai_engine*`.
  - **F5** — Wrote [`README.md`](./README.md): hard constraints table, ASCII architecture diagram, prerequisites (NVIDIA Container Toolkit), quickstart, service URL table, dev workflow (container-first + host tooling), troubleshooting playbook.

**Test Summary:** No code yet — nothing to test. Verified directory structure with `find`. Did not run `docker compose build` (would require ~10–15 min of CUDA + torch + Unsloth downloads — defer to first real Phase 5 dry-run).

**Decisions Made:**
- **Added `asyncpg>=0.30.0` to base deps** (NOT in `require.md`'s locked list). Rationale: `require.md` calls for "SQLAlchemy 2.0 (async where possible)" — async sessions against Postgres require either `asyncpg` or `psycopg[binary]` (v3). `psycopg2-binary` is sync-only, kept for Alembic + MLflow. This is a strict-additive change, not a replacement. **Flagging for developer review** — if rejected, fall back to sync sessions.
- **Used Compose YAML anchors** (`x-app-env`, `x-gpu-deploy`) to share env blocks between `api`/`worker` and GPU `deploy` between `worker`/`ollama`. Reduces drift.
- **Did not write a Phase-1 ADR.** No constraint was relaxed; only the dep list was extended additively. If you want this captured formally, I can write `ADR-006: asyncpg for async SQLAlchemy`.

**Next Action:**
→ Get developer sign-off on Phase 1 (especially the `asyncpg` addition), then begin **Phase 2: Schemas & Models** (`S1`–`S7`):
  1. `S1` — Enums (`TaskType`, `TrainingMode`, `SDGMode`, `JobStatus`)
  2. `S2` — Per-task data format schemas (Classification / ToolCalling / QA)
  3. `S3` — `SDGRequest`/`SDGResponse` (discriminated by `sdg_mode` + `task_type`)
  4. `S4` — `TrainingRequest`/`TrainingResponse` (manual vs hpo discriminator)
  5. `S5` — WebSocket message schemas
  6. `S6` — SQLAlchemy ORM models (Project, Dataset, TrainingJob, ModelArtifact, EvaluationRun)
  7. `S7` — Alembic init + first migration

**Blockers:** None.

---

## Session 0 — Project Kickoff & AI-Native Scaffolding (2026-05-07)

**Who:** Claude (Opus 4.7) + parks (developer)
**Status:** Finished

**Why & What:**
- Read full project spec at [`require.md`](./require.md)
- Read AI-Native Development orientation deck (PDF)
- Established AI-Native infrastructure **before** writing any project code:
  - [`CLAUDE.md`](./CLAUDE.md) — project rules, hard constraints, session protocol, forbidden files
  - [`WORKING_LOG.md`](./WORKING_LOG.md) — this file
  - [`TASK_TRACKER.md`](./TASK_TRACKER.md) — all 8 phases broken into trackable tasks
  - [`docs/architecture/OVERVIEW.md`](./docs/architecture/OVERVIEW.md) — high-level system view
  - [`docs/architecture/TECH_STACK.md`](./docs/architecture/TECH_STACK.md) — locked stack details
  - [`docs/adr/ADR-INDEX.md`](./docs/adr/ADR-INDEX.md) — index of decisions
  - 5 initial ADRs locking the most important constraints
  - 3 standards docs: coding style, API conventions, testing guide
  - 4 reusable prompt templates in `docs/prompts/`

**Test Summary:** N/A — no project code yet. Documentation only.

**Decisions Made:**
- Adopt the AI-Native session protocol (Discovery → Execution → Handover)
- Use ADRs with `Confidence Level` and `AI Guidance Level` (Strict/Flexible/Exploratory) fields
- Append-only ADRs — supersede instead of editing accepted ones

**Next Action:**
→ Begin **Phase 1: Foundation** from `require.md` and `TASK_TRACKER.md` (tasks `F1`–`F5`):
  1. Generate the project skeleton (`api/`, `workers/`, `ai_engine/`, `tests/`, `examples/`)
  2. Write `docker-compose.yml` for the 7 required services
  3. Write `.env.example`, `pyproject.toml`, `README.md`

**Blockers:** None

---

<!-- Add new sessions ABOVE this line. Template:

## Session N — <Title> (YYYY-MM-DD)

**Who:**
**Status:** <In Progress | Finished | Blocked>

**Why & What:**
- ...

**Test Summary:**
- ...

**Decisions Made:**
- ...

**Next Action:**
→ ...

**Blockers:**
- ...

---
-->
