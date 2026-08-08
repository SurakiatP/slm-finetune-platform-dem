# Runbook — Reading Prometheus metrics on the pasaflow VM

This platform ships its own Prometheus (`docker-compose.yml`'s `prometheus`
service, `docker/prometheus/prometheus.yml`), scraping the API's `GET
/metrics` every 30 seconds and retaining 30 days of data. There is no
Grafana yet — no external ingress exists for it to sit behind, and
Prometheus alone is what round 4's next item (alerting) needs. See
`docs/adr/ADR-013-metrics-prometheus.md` for the full set of decisions this
runbook assumes; this document is the "how do I actually look at it" half.

## Before you start

- You need wetty access to the box (see the `deploy-slm-vm` skill for how).
- **There is no SSH tunnel path to this box.** Port 22 is filtered — the
  advice you may remember from older runbooks ("`ssh -L 9001:localhost:9001
  ...`") is dead. Every loopback-bound service, Prometheus included, is
  reachable only from *inside* the VM. This is the same fact
  `scripts/deploy_pasaflow_vm.sh`'s own Phase 8 summary banner states for
  every other internal service:

  > Everything else binds 127.0.0.1 with no external route at all. There is
  > no SSH tunnel path from a laptop any more (port 22 is filtered) — reach
  > these from inside the VM (wetty) with docker compose exec.

  Prometheus follows the identical pattern as the MinIO console
  (`:9001`) and MLflow (`:5000`) examples in that same banner — it is not a
  special case.
- Confirm the container is actually up first: `docker compose ps prometheus`.

## Reaching Prometheus

Prometheus binds `127.0.0.1:${PROMETHEUS_PORT:-9090}` on the host — loopback
only, same as every other internal service
(`tests/unit/test_compose_port_exposure.py` guards this). From a wetty shell
on the box itself:

```bash
# The Prometheus UI/API answer directly on the box's own loopback:
curl -s http://localhost:9090/-/healthy
curl -s http://localhost:9090/api/v1/targets | python3 -m json.tool

# Or from inside the container network, via docker compose exec (works even
# if PROMETHEUS_PORT was never published, since this doesn't go through the
# host port at all):
docker compose exec prometheus wget -qO- http://localhost:9090/api/v1/targets
```

There is no browser-based Prometheus UI available from a laptop — reaching
`:9090`'s web UI means opening it inside a wetty session's own browser
context, or querying the HTTP API with `curl`/`wget` as above and reading
the JSON. Don't spend time looking for a tunnel or a forwarded port; per
"Before you start," none exists.

To hit the raw exposition the scraper itself sees (useful when Prometheus's
own scrape is the thing in question, not Prometheus's derived data):

```bash
docker compose exec api curl -fs http://localhost:8000/metrics
```

`GET /metrics` is unauthenticated (no bearer token needed, same as
`/health`) but is **not** proxied through `edge` — it will not answer at
`https://slmpc.pasaflow.com/metrics` from outside the box. See ADR-013
Decision 4 for why: it follows the same pattern as `/docs`/`/redoc`/
`/openapi.json`, which `docker/edge.nginx.conf` also never proxies.

## What each metric family means, and where its number actually comes from

Every metric below lives on a private Prometheus registry
(`api/core/metrics.py`), rendered fresh on each `GET /metrics` call. The
worker-fleet ones (`slm_queue_depth` onward) are **derived at scrape time**,
never accumulated in-process by a worker — see ADR-013 Decision 2 for the
full reasoning (short version: `worker_max_tasks_per_child=1` recycles the
Celery worker process after every task, so nothing accumulated in-worker
would survive to the next scrape). `api/services/metrics_sources.py` is the
module that does this re-derivation; it re-reads Postgres and Redis fresh
on every call rather than trusting any cached number.

| Metric | Labels | Meaning | Source at scrape time |
|---|---|---|---|
| `slm_http_requests_total` | `route`, `method`, `status_class` | Request count by route *template* (never a raw path — no UUIDs in labels) | In-process counter, incremented per request by `observe_http()` in the ASGI middleware |
| `slm_http_request_duration_seconds` | `route`, `method`, `status_class` | Request latency histogram | Same middleware, same call |
| `slm_queue_depth` | `queue` (`gpu`, `cpu`) | Pending (not-yet-picked-up) task count | `LLEN` against the Celery **broker** Redis db — a Celery queue is a Redis list under the hood |
| `slm_worker_up` | `queue` (`gpu`, `cpu`) | Whether some worker node is currently consuming that queue (1) or not (0) | `celery_app.control.inspect(...).active_queues()` — the exact same broadcast `/ready`'s `worker_gpu`/`worker_cpu` checks use (see below) |
| `slm_jobs` | `type` (`dataset`/`training`/`evaluation`/`export`), `outcome` (a `JobStatus` value) | Snapshot count of jobs in each status, zero-filled so a status with 0 current jobs still reports `0`, not "missing" | `SELECT ... GROUP BY status` over `Dataset`/`TrainingJob`/`EvaluationRun`/`ModelArtifact.export_status` |
| `slm_job_duration_seconds_avg` / `slm_job_duration_seconds_max` | `type` | Avg/max duration of jobs that completed in the trailing 24h | Computed in Python from `(started_at or created_at, updated_at)` pairs on rows where `status == COMPLETED` and `updated_at` falls in the window — capped at 5000 rows per type so a pathological backlog can't turn a scrape into a full table scan |
| `slm_openrouter_breaker_state` | *(none)* | `0`=closed, `1`=half_open, `2`=open | Reads the same Redis key + `_interpret()` function `api/services/circuit_breaker.py` (ADR-010) owns — not re-derived independently, so the two can never drift apart |
| `slm_openrouter_cost_usd_total` | `model`, `stage`, `outcome` | Cumulative OpenRouter spend | `SUM(cost_usd)` over `usage_events`, grouped |
| `slm_openrouter_prompt_tokens_total` / `slm_openrouter_completion_tokens_total` | `model`, `stage`, `outcome` | Cumulative token counts | Same `usage_events` grouped query |

**No metric here ever carries a `project`/`actor`/`user`/`job_id` label —
that is a deliberate, tested boundary** (ADR-013 Decision 3;
`tests/unit/test_metrics_registry.py` enumerates every registered collector
and fails the build if one declares a label outside the fixed set below).
If you're looking for per-project numbers, they live behind auth on
`GET /usage` and `GET /projects/{id}/usage` instead — not here.

The complete allowed label-name set, verbatim from the ADR:

```
route, method, status_class, queue, type, stage, model, outcome
```

## The GPU exporter — expected down, not a page-worthy incident

`docker-compose.yml` also runs `gpu-exporter`
(`utkuozdemir/nvidia_gpu_exporter`), scraped as Prometheus's `gpu_exporter`
job (`docker/prometheus/prometheus.yml`). **On the pasaflow box this
container is expected to be down** until the host's nvidia runtime
`no-cgroups` fix lands (root-only, `slmuser` has no sudo — the same wave-5
blocker that keeps the GPU Celery worker and Ollama down; see
`WORKING_LOG.md`). Seeing `up{job="gpu_exporter"} == 0` in Prometheus is the
current, expected state of the world, not something to restart or debug.
Nothing in the compose file `depends_on`s it and the deploy script's
container-count check excludes it, so a crash-looping `gpu-exporter` never
blocks the rest of the stack from coming up. Once the host fix lands, this
target starts reporting on its own — no redeploy needed.

## `/ready`'s `worker_gpu`/`worker_cpu` and `slm_worker_up` — same fact, two surfaces

`GET /ready` (see `docs/02-api-reference.md`) reports `worker_gpu` and
`worker_cpu` as separate keys, each `ok` or `unavailable`. `slm_worker_up`
reports the identical fact as a Prometheus gauge, one time series per
`queue` label value. Both are backed by the exact same underlying check —
`api/services/readiness.py`'s `probe_worker_queues()`, one
`inspect(...).active_queues()` broadcast that names which queues each
responding Celery node actually consumes.

They exist as two separate surfaces because they answer to two different
audiences on two different timescales:

- **`/ready`** is synchronous — call it right now, get the current answer,
  used by anything that needs a live yes/no (a load balancer, a human
  debugging a specific incident right now).
- **`slm_worker_up`** is scraped every 30s and retained for 30 days —
  useful for "when did the GPU worker actually go down" or an alert rule
  ("`slm_worker_up{queue="gpu"} == 0` for 5 minutes"), which `/ready` alone
  cannot answer since it has no memory of its own.

The per-queue split itself (rather than one aggregate `worker` check) is a
direct fix for a bug found live on this box: a plain Celery `ping()` only
proves *some* node answered, and `worker-cpu` answering was enough to make
the old aggregate check report `worker: ok` straight through a total GPU
outage (`WORKING_LOG.md`, "round 3 deployed to the pasaflow VM" —
*"`/ready` says `worker: ok` while the GPU worker is dead ... a total GPU
outage is invisible to readiness"*). If you ever see `worker_gpu:
unavailable` next to `worker_cpu: ok` on `/ready`, or
`slm_worker_up{queue="gpu"} == 0` next to `slm_worker_up{queue="cpu"} == 1`
in Prometheus, that is exactly the scenario this split exists to surface —
it means GPU jobs (training, export, evaluation, HPO) cannot run right now
even though the API and SDG pipeline look completely healthy. Neither key
flips `/ready`'s status code to `503` on its own (worker unavailability is
reported, not fatal — see ADR-013 Decision 6) — only `postgres`/`redis`
being down does that.

## What this is not

This is not an alerting system — Prometheus here only scrapes and stores;
there are no alert rules configured yet (that is round 4's next item, and
it is exactly what this Prometheus instance exists to serve, per
ADR-013). There is also no Grafana and no dashboard — read the raw HTTP API
(`/api/v1/query`, `/api/v1/targets`) with `curl` as shown above until one
exists, and see ADR-013's Decision 1 for why standing up Grafana was
deferred rather than shipped alongside this.
