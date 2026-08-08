# ADR-013 — Prometheus metrics: scrape-time derivation, a fixed label set, and no Grafana yet

- **Status**: Accepted
- **Date**: 2026-08-08

## Context

Round 4 item 7 ("metrics") is the first of three observability items in
sequence — 7 metrics → 8 alerts → 9 backup/restore (see `WORKING_LOG.md`).
Item 8 needs a rules engine to evaluate alert conditions against; the only
credible candidate that doesn't invent a second time-series store is
Prometheus itself, so this ADR's scope is really "stand up the thing item 8
depends on," not metrics for their own sake.

Two things independently pointed at the same shape once the design started:

**The worker process recycles on every task.** `workers/celery_app.py` sets
`worker_max_tasks_per_child=1` — a Celery worker process is torn down and
respawned after each single task completes. `api/services/circuit_breaker.py`
already had to solve this exact problem for OpenRouter breaker state (moved
to Redis, see ADR-010) for exactly this reason: any counter accumulated
in-process would be wiped clean before the next task even starts. Any
in-worker `prometheus_client.Counter().inc()` would have the identical
failure mode — it would never report anything meaningful, no matter how many
tasks ran.

**A live bug in `/ready` was found on the box before this design started.**
Round 3's deploy session (`WORKING_LOG.md`, "round 3 deployed to the pasaflow
VM") recorded: *"`/ready` says `worker: ok` while the GPU worker is dead.
`worker-cpu` answers `inspect()`, and the probe does not distinguish
queues — so a total GPU outage is invisible to readiness."* That was filed as
a known gap, not fixed in round 3. It is fixed as part of this round, and the
fix (per-queue worker keys) is a joint decision between this ADR and
`api/services/readiness.py` — see Decision below.

## Decision

### 1. Prometheus exposition on the API; a Prometheus server container; Grafana deferred

`prometheus_client` is wired into the API process only (`api/core/metrics.py`,
gated behind the `metrics` extra in `pyproject.toml` — see Decision 2 for why
it is API-only). A new `prometheus` Compose service (`prom/prometheus`)
scrapes it:

- Loopback-only (`127.0.0.1:${PROMETHEUS_PORT:-9090}`), same as every other
  internal service — see `tests/unit/test_compose_port_exposure.py`.
- `scrape_interval: 30s` (`docker/prometheus/prometheus.yml`).
- `--storage.tsdb.retention.time=30d`.

**Grafana is deliberately not part of this round.** There is no external
ingress for it to sit behind yet — `edge`'s only public hostnames are the app
and storage subdomains (ADR-011), and Grafana would need its own auth story
this platform has no answer for (`ADR-009` covers API bearer tokens, not a
dashboard login). What item 8 actually needs is Prometheus's own rule
evaluation engine, not a dashboard, so Prometheus alone is sufficient scope
for round 4. Grafana is a follow-up once there is somewhere to expose it.

### 2. Every worker/job metric is derived at scrape time, never accumulated in-worker

`api/services/metrics_sources.py` re-derives every worker-fleet number fresh
on each call, straight from the systems that already durably hold that
state:

- **Queue depth** — `LLEN` against the Celery broker's Redis lists (`gpu`,
  `cpu` — see ADR-010's queue split).
- **Job counts / durations** — `SELECT`/`GROUP BY` over `Dataset.status`,
  `TrainingJob.status`, `EvaluationRun.status`, `ModelArtifact.export_status`.
- **OpenRouter cost/token totals** — `SUM()` aggregation over `usage_events`
  (the same table ADR-010's budget gate reads).
- **Circuit breaker state** — read from the same Redis key
  `circuit_breaker.py` already owns (see ADR-010), reusing its
  `_interpret()` function rather than re-deriving the policy a second time.

This is the same rationale that moved circuit-breaker state to Redis in
ADR-010, applied one level further: **not even Redis-backed in-worker
counters** are used, because there is nothing in-worker to increment — the
worker process is gone before a scrape could ever observe it. Every number
above already lives durably somewhere else (Postgres rows, Redis lists/keys)
by the time a scrape asks for it, so the metrics layer only ever reads,
never writes, worker-side state.

**Consequence for the worker image**: the worker deliberately does not ship
`prometheus_client` at all. `docker/worker.Dockerfile` never installs the
`metrics` extra, and `tests/unit/test_worker_import_surface.py` asserts (by
import-hook subprocess, the same mechanism it already used to guard PyJWT)
that no worker-boot module can import it. `api/services/metrics_sources.py`
itself is guarded the opposite way — an AST import scan
(`test_metrics_sources.py`) asserts it never imports `prometheus_client`,
keeping the derivation layer usable and testable independently of the
`prometheus_client`-wired exporter in `api/core/metrics.py`.

### 3. No per-tenant/per-project labels — a fixed, closed label-name set

No metric in `api/core/metrics.py` ever carries a `project`, `actor`,
`user`, `tenant`, or `job_id` label. The complete set of label names any
metric is allowed to use is:

```
route, method, status_class, queue, type, stage, model, outcome
```

`tests/unit/test_metrics_registry.py`
(`test_registered_metric_labels_are_a_subset_of_the_allowed_non_tenant_set`)
enumerates every collector on the private registry and asserts its label
names are a subset of exactly this set — not a substring or spot check, a
full enumeration, so a new metric with a stray `project_id` label fails the
guard the moment it is registered, whether or not anything has scraped it
yet.

Rationale, two-fold:

- **Cardinality.** A label that grows with the number of projects/jobs ever
  created turns every scrape bigger forever — this is the standard
  Prometheus cardinality-bomb failure mode, and nothing about this
  platform's monitoring needs justifies paying it.
- **Tenancy leak into the monitoring layer.** Prometheus has no
  per-caller authorization model of its own (see Decision 4) — anything
  with a `project`/`actor` label on it would mean every project's job
  volume, spend, and duration is readable by anyone who can reach `:9090`
  or a future Grafana instance, with none of the ownership checks
  ADR-009/ADR-012 enforce on the API itself.

Per-project numbers are not missing from the platform, they are already
served correctly, just from a different, authenticated surface:
`GET /usage` and `GET /projects/{id}/usage` (both behind the ownership
checks ADR-009/ADR-012 established). Metrics answer "how is the platform
doing," usage answers "how is my project doing" — different audiences,
different auth models, deliberately not merged into one label space.

### 4. `/metrics` is unauthenticated, and not proxied through the edge

`GET /metrics` requires no token, the same as `/health` — a scraper has no
bearer token to send, and per Decision 3 above there is nothing tenant-scoped
in the response for a token to gate anyway. It is **not** added to
`docker/edge.nginx.conf`. This mirrors the round-3 edge design's existing
pattern of deliberately not proxying process-introspection surfaces
(`/docs`, `/redoc`, `/openapi.json` are handled the same way — see
`WORKING_LOG.md`'s "round 3 deployed to the pasaflow VM" entry on the `/docs`
200-that-wasn't). Anything not explicitly given a `location` block in
`edge.nginx.conf` falls through to the SPA's `try_files ... /index.html`, so
an external caller hitting `https://slmpc.pasaflow.com/metrics` gets the SPA
shell, not metrics — `/metrics` is reachable only from inside the compose
network (Prometheus's own scrape) or from `127.0.0.1` on the box itself
(`docker compose exec api curl localhost:8000/metrics`), same access pattern
as `/ready`.

### 5. The GPU exporter ships now, expected down until the host `no-cgroups` fix

`docker-compose.yml`'s `gpu-exporter` service (`utkuozdemir/nvidia_gpu_exporter`,
scraped as the `gpu_exporter` job in `docker/prometheus/prometheus.yml`) is
added in this round even though it is known not to run on the pasaflow box
yet. The blocker is the same host-level issue already tracked against the
GPU worker itself: `/etc/nvidia-container-runtime/config.toml` needs
`no-cgroups = true` for rootless Docker, requires root, and `slmuser` has no
sudo (`WORKING_LOG.md`, "round 3 deployed to the pasaflow VM" and the wave-5
resume point). This is the same wave-5 blocker that keeps `worker`/`ollama`
GPU-dead on that box today, not a new one.

Because the down state is expected, **nothing depends on `gpu-exporter`**:
no `depends_on` anywhere in `docker-compose.yml` names it, and the deploy
script's healthy-container-count wait excludes it explicitly
(`tests/unit/test_prometheus_stack.py`'s `_UNCOUNTED_SERVICES`). A
crash-looping `gpu-exporter` (`restart: unless-stopped`) must never block
`docker compose up` from bringing up the rest of the stack — it just shows
as `up == 0` for that one Prometheus target once the host fix lands.

### 6. `/ready`'s worker probe is now per-queue: `worker_gpu`, `worker_cpu`

`api/services/readiness.py`'s `probe_worker_queues()` replaces the old single
aggregate `worker: ok/unavailable` check with one verdict per queue
(`WORKER_QUEUES = ("gpu", "cpu")`), via one `inspect(...).active_queues()`
broadcast naming which queues each responding node actually consumes. This
directly fixes the bug recorded in Context: a plain `ping()`-style check only
proves *some* Celery node answered, so on this deployment `worker-cpu`
answering was enough to make the whole probe say `worker: ok` straight
through a total GPU outage. The fix is degrade-only, matching the existing
`/ready` philosophy (ADR-established in `readiness.py`'s own module
docstring): `worker_gpu`/`worker_cpu` join `minio` as **reported, not
fatal** — a dead GPU worker still shows up explicitly in the response body,
but does not flip `/ready`'s status code to `503`, since submits still
enqueue and `job_reconcile.py` ends orphaned jobs rather than leaving them
spinning. Only `postgres`/`redis` being down is fatal, unchanged from
before this round.

`slm_worker_up{queue=...}` (Decision 2/3) is the scrape-time, Prometheus-native
sibling of the same fact `worker_gpu`/`worker_cpu` report synchronously on
`/ready` — both are ultimately backed by the same `probe_worker_queues()`
call. See `docs/runbooks/metrics.md` for exactly how the two relate.

## Consequences

**Accepted:**

- A scrape can be slightly stale relative to a synchronous `/ready` call
  (up to one `scrape_interval`, 30s) — accepted because nothing here is used
  for a hot-path decision; alerting on a 30-second-old queue depth is the
  norm for Prometheus, not a gap specific to this design.
- The per-scrape cost (one `LLEN` per queue, several grouped `COUNT`/`SUM`
  queries, one Redis `GET`, one Celery broadcast every `/ready` call) is
  accepted the same way ADR-010 accepted its own per-submission quota/budget
  query cost: job and scrape volumes on this platform are small.
- Two things must both be updated if a new job-bearing entity or queue is
  ever added: `metrics_sources.py`'s reader functions and
  `api/core/metrics.py`'s label declarations. There is no schema-driven
  generation here — this is a deliberately small, hand-maintained catalog,
  consistent with Decision 3's closed label-name set.
- `gpu-exporter` will show `up == 0` in Prometheus indefinitely until a human
  with root fixes the host config — this is expected, not a bug to chase,
  per Decision 5.

**Rejected alternatives:**

- *In-worker Prometheus counters, pushed via the Pushgateway pattern instead
  of scraped* — rejected for the same root reason as Decision 2: even a
  push has to originate from a worker process that is recycled after one
  task, so nothing durable would accumulate to push from. Pushgateway also
  adds a second exposition surface and a "when does a pushed metric expire"
  problem this design has no need to take on when every number is already
  cheaply re-derivable from Postgres/Redis at scrape time.
- *Grafana in this round* — rejected; see Decision 1. Revisit once an
  internal-only ingress path (or a Cloudflare Access-gated hostname) exists
  for it to sit behind.
- *Per-project labels behind a separate, access-controlled metrics
  endpoint* — rejected as unnecessary duplication: `GET /usage` and
  `GET /projects/{id}/usage` already serve this, already authenticated,
  already ownership-checked. Standing up a second, parallel per-project
  numeric surface inside Prometheus would mean maintaining the same access
  control twice, in two different technologies, for the same underlying
  numbers.
- *Proxying `/metrics` through `edge`* — rejected; see Decision 4. The
  round-3 precedent (`/docs`/`/redoc`/`/openapi.json` deliberately absent
  from `edge.nginx.conf`) already established that process-introspection
  endpoints stay off the public edge, and `/metrics` is the same shape of
  endpoint.
- *Skipping the GPU exporter until the host fix lands* — rejected. Shipping
  it now, wired but expected-down, means the day the host `no-cgroups` fix
  happens the exporter just starts reporting — no follow-up deploy, no
  compose change, nothing to remember. The alternative (add it later) is
  strictly more work for the same eventual end state.

## Related

- [ADR-010](./ADR-010-cpu-gpu-queue-split-and-quotas.md) — the CPU/GPU queue
  split this ADR's `queue` label and `worker_gpu`/`worker_cpu` probe both
  read; the circuit breaker and `usage_events` table this ADR's scrape-time
  readers reuse rather than re-deriving; the original "in-process state dies
  with `worker_max_tasks_per_child=1`" reasoning this ADR applies a second
  time.
- [ADR-011](./ADR-011-nginx-edge-cloudflare-tunnel-presigned-downloads.md) —
  the edge/ingress design `/metrics` deliberately stays outside of.
- [ADR-009](./ADR-009-supabase-jwt-auth.md) /
  [ADR-012](./ADR-012-owner-mismatch-403-not-404.md) — the ownership/auth
  model `GET /usage` and `GET /projects/{id}/usage` enforce, which is why
  per-project numbers live there and not in this label set.
- `docs/runbooks/metrics.md` — how to reach Prometheus on the box, what each
  metric family means, and the known-down GPU exporter.
- `docs/02-api-reference.md` — the `GET /metrics` and `GET /ready` contract
  this ADR's decisions produce.
