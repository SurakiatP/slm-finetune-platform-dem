# Runbook — GPU verification for `feat/be-fe-gap001`

Everything in this branch is unit-tested on CPU (427 passed locally, no GPU, no
Docker). What follows is the subset that **cannot** be proven without real
hardware, plus the two checks that need a real Postgres.

Why these and not others: the GGUF export pipeline loads a model into VRAM
(`workers/tasks/model_export.py` calls `_release_gpu_memory()` for a reason), and
evaluation runs inference against Ollama, which is GPU-served. Everything else —
the snapshot store, the REST endpoint, the cancel semantics, the schemas — is
pure CPU logic and is already covered by `tests/unit/`.

## Not GPU, but not runnable on a laptop without Docker

| # | Check | Why it's here |
|---|---|---|
| D1 | `alembic upgrade head` → `0006_job_control_columns`, then `downgrade -1`, then `upgrade head` again | Proves `create_type=False` doesn't try to re-create the shared `job_status` enum. Offline-rendered SQL already looks correct both ways, but only a real Postgres proves it. |
| D2 | SDG run + `POST /api/v1/datasets/{id}/cancel` mid-run | Needs Redis + Postgres + an OpenRouter key. **No GPU** — SDG calls OpenRouter, not the local GPU. This is the single most important check in the branch. |

## Needs a GPU (vast.ai / the pasaflow box)

| # | Check | What must be true |
|---|---|---|
| G1 | Export a model, watch `/ws/jobs/{export_job_id}` | All six `export_progress` frames arrive in order: `downloading → merging → converting → quantizing → uploading → registering`. `quantizing` carries the quant level in `detail`. |
| G2 | Same run, poll `GET /api/v1/models/{id}` | `export_status` goes `pending → running → completed`, and `gguf_uri` is still populated (the legacy completion signal must not have regressed). |
| G3 | Start an export, then `POST /api/v1/models/{id}/export/cancel` **during quantization** | `export_status` ends as `cancelled` — **not** stuck at `running`, and **not** flipped to `failed`. This is the regression that the `except BaseException` widening fixes; it is the highest-risk item here because it only manifests against a real Celery worker child receiving a real SIGTERM. Also confirm GPU memory is released (`nvidia-smi`) and no orphaned `export-*` temp dir is left in the container. |
| G4 | Run an evaluation against an exported model | `evaluation_progress` frames arrive with `phase="predicting"` at roughly one per 2s (not one per row), the last frame has `rows_done == rows_total`, and exactly one `phase="judging"` frame appears for QA / tool_calling — and none for classification. |
| G5 | Full E2E through `smart-model-tune` | Start SDG, then **hard-refresh the browser mid-run**. The sample counter (e.g. `80/200`) must appear **immediately**, without waiting for the next publish. This is the branch's actual acceptance criterion, and it must pass with **zero changes to the frontend**. |

## Order

D1 → D2 → G1 → G2 → G3 → G4 → G5. D2 gates the GPU work: if the snapshot doesn't
survive a real Redis + Celery round trip there, nothing downstream is worth
running.

## Commands

```bash
# on the box, in the deploy dir
git fetch origin && git checkout feat/be-fe-gap001 && git pull
docker compose up -d --no-deps api worker      # --no-deps: see the deploy-slm-vm skill re: ollama/11434
docker compose exec api alembic upgrade head
docker compose exec api alembic current        # expect 0006_job_control_columns (head)

# D2 / G5 — watch the snapshot work
JOB=<job_id from the 202 response>
curl -s localhost:8000/api/v1/jobs/$JOB/progress | jq   # must return the last frame, not 404
websocat ws://localhost:8000/ws/jobs/$JOB              # first line should arrive instantly

# regression baseline on the box
docker compose exec api pytest tests/unit -q
# expect 427 passed / 3 skipped / 3 failed
# the 3 failures are the known pre-existing test_snapshot_node_3b4.py SDGAbortedError
# snapshot flakiness — identical to the vast.ai and pasaflow baselines, not a regression
```

## If something fails

- **`alembic upgrade` errors on `job_status` already existing** → the migration
  lost `create_type=False`. Check `alembic/versions/20260804_0006_job_control_columns.py`.
- **`export_status` stuck at `running` after a cancel** → the `except BaseException`
  in `workers/tasks/model_export.py` was narrowed back to `except Exception`.
  `tests/unit/test_worker_progress_frames.py` guards this; run it first.
- **WS connects but no first frame** → check Redis actually has the key:
  `docker compose exec redis redis-cli get "job:$JOB:last"`. Empty means the
  worker's `publish_ws_message` isn't writing it (or the 24h TTL lapsed).
- **Frames arrive twice on connect** → expected and harmless; see ADR-007's
  accepted race. Do not "fix" it.
