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
| `slm_dependency_up` | `dependency` (`postgres`, `redis`, `minio`) | Whether that dependency's probe succeeded (1) or not (0) | `api/services/readiness.py`'s `probe_dependencies()` — the exact same three probes (`_PROBES`: `_probe_postgres`, `_probe_redis`, `_probe_minio`) that back `/ready`'s `postgres`/`redis`/`minio` checks, re-run fresh at scrape time so the gauge and `/ready` can never disagree. Worker-queue health is **not** in this metric — that's `slm_worker_up` above, kept separate |
| `slm_jobs` | `type` (`dataset`/`training`/`evaluation`/`export`), `outcome` (a `JobStatus` value) | Snapshot count of jobs in each status, zero-filled so a status with 0 current jobs still reports `0`, not "missing" | `SELECT ... GROUP BY status` over `Dataset`/`TrainingJob`/`EvaluationRun`/`ModelArtifact.export_status` |
| `slm_job_failures` | `type` (`dataset`/`training`/`evaluation`/`export`), `error_type` (closed whitelist, see below) | Snapshot count of jobs whose error-message column is non-null, bucketed into `error_type`; zero-filled across all 4×6=24 (type, error_type) pairs | `api/services/metrics_sources.py`'s `job_failure_counts()` — for each of the four entities, selects rows where `error_message`/`export_error_message` `IS NOT NULL` (capped at 5000 rows per type) and classifies each `(status, message)` pair with `classify_error()`. **Not** filtered on `status == FAILED`: `ModelArtifact.export_error_message` is the one error column ever cleared on retry, so the other three entities can carry a stale non-null error message next to a since-COMPLETED status — that row still counts here, because a failure of that `error_type` genuinely happened |
| `slm_job_duration_seconds_avg` / `slm_job_duration_seconds_max` | `type` | Avg/max duration of jobs that completed in the trailing 24h | Computed in Python from `(started_at or created_at, updated_at)` pairs on rows where `status == COMPLETED` and `updated_at` falls in the window — capped at 5000 rows per type so a pathological backlog can't turn a scrape into a full table scan |
| `slm_openrouter_breaker_state` | *(none)* | `0`=closed, `1`=half_open, `2`=open | Reads the same Redis key + `_interpret()` function `api/services/circuit_breaker.py` (ADR-010) owns — not re-derived independently, so the two can never drift apart |
| `slm_openrouter_cost_usd_total` | `model`, `stage`, `outcome` | Cumulative OpenRouter spend | `SUM(cost_usd)` over `usage_events`, grouped |
| `slm_openrouter_prompt_tokens_total` / `slm_openrouter_completion_tokens_total` | `model`, `stage`, `outcome` | Cumulative token counts | Same `usage_events` grouped query |

### The `error_type` whitelist

`slm_job_failures`'s `error_type` label is a **closed** whitelist, not
"whatever the exception class name happens to be" — a label populated from
raw exception class names is unbounded cardinality (every new exception type
ever raised, including third-party ones, would mint a new time series
forever). `api/services/metrics_sources.py`'s `classify_error(status,
message)` is pure and total: every path through it returns one of exactly
these six values, so `error_type` can never be anything else:

| `error_type` | What lands here |
|---|---|
| `oom` | GPU out-of-memory — matches on `"out of memory"` / `"outofmemoryerror"` in the stored error text (this always comes straight from PyTorch/CUDA, e.g. `torch.cuda.OutOfMemoryError: CUDA out of memory. ...` — no code in this repo raises it itself) |
| `provider` | OpenRouter/Ollama call failures — matches `"openrouter"`, `"ollama"`, `"rate limit"`/`"rate-limit"` in the text (circuit breaker trips, the SDG budget cap, Ollama daemon failures, provider-side rate limiting) |
| `storage` | MinIO/S3 failures — matches `"minio"`, `"s3"`, `"bucket"` in the text (`minio-py`'s own `S3Error` text, connection failures to `minio:9000`, this repo's `parse_s3_uri` `ValueError`s) |
| `cancelled` | The row's **status** is `CANCELLED` — checked first, before any text matching, and short-circuits regardless of what the message says. A cancelled task's `SystemExit` is stored verbatim as `error_message` and is the literal string `"-241"`, which carries no readable signature at all, so this has to be a status check, not a text match |
| `orphaned` | Matches `"no worker is executing this job"` in the text — the free-text sentence `job_reconcile.reconcile_once` writes into the error column for a swept orphan |
| `other` | Anything that falls through every check above |

Order matters: `status == CANCELLED` is checked first (independent of message
text), then the four text signatures above in the order `orphaned` → `oom` →
`provider` → `storage`, then `other` as the catch-all. See
`classify_error()`'s own docstring in `api/services/metrics_sources.py` for
the full reasoning and the exact matched strings, including why this matches
message *text* rather than exception class names (every task handler stores
`(str(exc) or repr(exc))[:4000]`, never `type(exc).__name__`).

**No metric here ever carries a `project`/`actor`/`user`/`job_id` label —
that is a deliberate, tested boundary** (ADR-013 Decision 3;
`tests/unit/test_metrics_registry.py` enumerates every registered collector
and fails the build if one declares a label outside the fixed set below).
If you're looking for per-project numbers, they live behind auth on
`GET /usage` and `GET /projects/{id}/usage` instead — not here.

The complete allowed label-name set, verbatim from the ADR (8 -> 10 as of
this round, adding `dependency` for `slm_dependency_up` and `error_type` for
`slm_job_failures`):

```
route, method, status_class, queue, type, stage, model, outcome, dependency, error_type
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

## Alerting

`docker/prometheus/alerts.yml` ships six rules, loaded via `prometheus.yml`'s
`rule_files:` and bind-mounted read-only into the `prometheus` service at
`/etc/prometheus/alerts.yml`. Read that file's own header comment for the
threshold-by-threshold reasoning; this section is the "how do I look at
this / what do I do about it" half.

### There is no Alertmanager, and nothing here will ever page you

This deployment has no Alertmanager, no `alerting:` block in
`prometheus.yml`, and no notification channel of any kind — no ingress, no
SMTP, no Slack webhook. That is a deliberate decision, not a gap waiting to
be filled: a router with nowhere to send anything is only extra failure
surface, not extra safety.

**An alert firing here is something you go and look at, never something
that finds you.** Nobody gets paged, texted, or emailed when one of the six
rules below goes into a `firing` state — it just sits there, visible only
to whoever next runs the `curl`/`docker compose exec` commands below or
opens the Prometheus UI over wetty. Do not assume "no alerts fired" means
"healthy" without checking, and do not assume a real incident will announce
itself — on this box, it won't, until a human looks.

### How to see rules and firing alerts, over loopback only

Same access pattern as the rest of this runbook ("Reaching Prometheus"
above): Prometheus binds `127.0.0.1:9090` on the host, there is no SSH
tunnel to this box (port 22 is filtered), so every command below runs from
a wetty shell on the box itself.

```bash
# Rule definitions + current state (inactive/pending/firing) for all six:
curl -s http://localhost:9090/api/v1/rules | python3 -m json.tool

# Only what is currently pending or firing:
curl -s http://localhost:9090/api/v1/alerts | python3 -m json.tool
```

Or from inside the container network via `docker compose exec` (works even
if `PROMETHEUS_PORT` was never published, same as the `/api/v1/targets`
example above):

```bash
docker compose exec prometheus wget -qO- http://localhost:9090/api/v1/rules
docker compose exec prometheus wget -qO- http://localhost:9090/api/v1/alerts
```

A rule shows up in `/api/v1/rules` regardless of state (it's a rule
definition plus whatever state it's currently in); `/api/v1/alerts` only
ever lists ones currently `pending` or `firing`. An empty `/api/v1/alerts`
response is the everyday case, not a sign something is broken.

### Before you trust "no alerts firing": check the API scrape target first

**All six rules are built entirely on metrics `GET /metrics` exports, so
every one of them goes silent — not firing, just absent from evaluation —
if the `api` scrape target itself is down.** A dead API means no fresh
`slm_worker_up`, `slm_queue_depth`, `slm_dependency_up`, `slm_jobs`,
`slm_job_failures`, or `slm_openrouter_cost_usd_total` samples land in
Prometheus at all, so there is nothing for any rule to evaluate against —
not "the alert evaluated false," but "the alert had no data to look at."
This is the single biggest way "no alerts" can silently mean "totally
blind" instead of "healthy." Check this first, every time, before reading
anything else in this section as good news:

```bash
curl -s 'http://localhost:9090/api/v1/query?query=up{job="api"}' | python3 -m json.tool
```

If that isn't `1`, nothing below this line can be trusted yet — go fix the
API scrape target before drawing any conclusion from `/api/v1/alerts`.

### The six alerts

| Alert | What it means | Threshold, and why | First thing to do |
|---|---|---|---|
| **WorkerDown** | No Celery worker node is currently consuming a given queue (`slm_worker_up{queue=...} == 0`) — jobs routed to that queue cannot run | `for: 2m` on `slm_worker_up == 0`. 2 minutes is long enough to ride out a worker restart/reconnect blip without firing on nothing, short enough to still catch a real outage quickly. First-guess, retune once real restart-blip durations are known | Check `slm_worker_up{queue=...}` and `/ready`'s `worker_gpu`/`worker_cpu` keys to confirm which queue and since when; if `queue="gpu"` on the pasaflow box, see the note below — this one is expected right now |
| **QueueBackedUp** | `slm_queue_depth{queue=...}` has stayed above 10 pending tasks for 15 minutes — tasks are queuing faster than they drain | `> 10` for `15m`. 10 is a first guess at "more than a single worker plausibly drains in one training/eval cycle" for this platform's scale (RTX 3060 12GB, one box); 15m is long enough for a normal burst (several datasets queued back-to-back) to drain before firing | Check `slm_worker_up` for the same queue — if the worker is also down, this is just WorkerDown's downstream symptom; if the worker is up, check whether it's stuck on one long-running task (a GPU job in progress) versus genuinely falling behind |
| **DependencyDown** | `slm_dependency_up{dependency=...} == 0` for 2 minutes — Postgres, Redis, or MinIO is failing its probe | `for: 2m`, same reasoning as WorkerDown — ride out a brief reconnect blip, still catch a real outage quickly | Run `/ready` directly (or re-check `slm_dependency_up` per-dependency) to confirm which of postgres/redis/minio is down; postgres or redis failing is the one case in this whole table that's also user-visible right now (`/ready` returns 503) — treat it as the most urgent of the six |
| **RepeatedTaskFailures** | `increase(slm_jobs{outcome="failed"}[30m]) >= 3` — at least ~3 jobs flipped to `failed` in the trailing 30 minutes | `>= 3`, no `for:` (instant threshold on a windowed `increase()`, not a sustained condition). First guess at "more than isolated/incidental" failures for this platform's job volume | Read the counter-reset caveat below before reacting — then, if it looks real, check `slm_job_failures{type=...}` broken out by `error_type` to see what kind of failure is repeating, and look at the actual failing rows' `error_message` |
| **GpuOom** | `increase(slm_job_failures{error_type="oom"}[1h]) >= 1` — at least one GPU out-of-memory failure in the trailing hour | `>= 1`, no `for:`. Any OOM is worth surfacing on this single RTX 3060 12GB box — there's no "acceptable" OOM rate here. First guess, in case OOM noise turns out to need debouncing later | Read the counter-reset caveat below first — then, if real, this is almost always a training/HPO config asking for more VRAM than the box has (batch size, sequence length, LoRA rank) rather than an infra problem; check the failing job's config before assuming hardware failure |
| **CostAnomaly** | `sum(increase(slm_openrouter_cost_usd_total[1h])) > 5` — platform-wide OpenRouter spend over the trailing hour exceeds $5 | `> 5` ($/h). First guess at a spend rate clearly abnormal for OpenRouter SDG usage at this project's scale. The `sum()` is load-bearing — without it the expression evaluates per (model, stage, outcome) series instead of platform-wide; a test guards its presence | Break the same query down without the `sum()` (or query `slm_openrouter_cost_usd_total` directly, grouped by `model`/`stage`) to find which model/stage is driving spend; check for a runaway SDG job or an unexpectedly expensive model selection before assuming abuse |

### The counter-reset caveat (RepeatedTaskFailures, GpuOom)

`slm_jobs{outcome="failed"}` and `slm_job_failures` are both **snapshot
row-counts**, re-derived fresh at every scrape (`SELECT ... GROUP BY
status`-style queries) — not true monotonic counters. Anything that makes
the underlying row count *drop* between scrapes — cascade-hard-deleting a
project or dataset, or retrying an export (which clears
`export_error_message`) — makes the exported value go down. PromQL's
`increase()` treats any decrease as a counter reset and adds the whole
post-reset value back in, so **RepeatedTaskFailures and GpuOom can
transiently over-fire right after a delete or a retry, even though no new
failure actually happened.**

This is a **false-positive-only** failure mode — it can never mask a real
failure, only manufacture the appearance of one. Given there's no
Alertmanager and nobody is paged by it (see above), the practical impact is
just: if one of these two fires shortly after a project/dataset deletion or
an export retry, check whether that's the actual cause before spending time
hunting for a training/GPU problem that isn't there. (Separately,
`increase()` extrapolates to the window edges rather than just between
observed samples, so its output is an approximation of the raw event count
— a firing alert's `{{ $value }}` should be read as "around N," not
"exactly N.")

### WorkerDown on `queue="gpu"` is expected right now — do not "fix" it

On the pasaflow box today, `slm_worker_up{queue="gpu"}` is genuinely `0`
because a host nvidia `no-cgroups` bug blocks the GPU containers from
starting (root-only fix, `slmuser` has no sudo — see the GPU-exporter
section above and `WORKING_LOG.md`). WorkerDown firing for `queue="gpu"` is
therefore **correct, expected behaviour** — it is exactly the "GPU jobs
cannot run right now" fact the metric exists to surface, not a bug in the
alert or a false positive. Do not silence, retune, or "fix" WorkerDown to
make this go away; fix the underlying nvidia runtime issue instead, at
which point the alert clears on its own.

### GpuOom ships verified by unit tests only

For the same host nvidia bug, no GPU work can run on the pasaflow box at
all right now, which means GpuOom cannot be exercised end-to-end there —
there is no way to actually trigger a `torch.cuda.OutOfMemoryError` on a
box where no training job can start in the first place. It is covered by
unit tests against `classify_error()`'s OOM text-matching (see
`tests/unit/test_metrics_sources.py`) and by `tests/unit/test_alert_rules.py`
asserting the rule's `expr`/threshold, but has no live-fire confirmation on
this box. Once the nvidia fix lands and GPU jobs run again, treat the first
real OOM as also the first real exercise of this alert.

## What this is not

There is no Grafana and no dashboard — read the raw HTTP API
(`/api/v1/query`, `/api/v1/targets`, and now `/api/v1/rules`/`/api/v1/alerts`
above) with `curl` as shown throughout this runbook until one exists, and
see ADR-013's Decision 1 for why standing up Grafana was deferred rather
than shipped alongside this. Alert rules exist now (this section), but
there is still no Alertmanager and no notification channel — see "There is
no Alertmanager" above.
