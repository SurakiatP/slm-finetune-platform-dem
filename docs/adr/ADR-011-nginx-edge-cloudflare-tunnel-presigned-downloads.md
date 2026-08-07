# ADR-011 — Same-origin nginx edge, Cloudflare Tunnel ingress, and presigned object downloads

- **Status**: Accepted
- **Date**: 2026-08-07
- **Relates to**: [ADR-009](./ADR-009-supabase-jwt-auth.md), [ADR-010](./ADR-010-cpu-gpu-queue-split-and-quotas.md)

## Context

Round 3 picks up the three gap-analysis items that round 1's ADR-009 explicitly
left open: "closing the internal service ports still published by
`docker-compose.yml`, a startup guard against default credentials, ...
TLS/rate-limiting at the ingress." The production host was chosen (the pasaflow
VM, `slmpc.pasaflow.com`) and the threat model settled (public internet,
unknown users) before any of this was built — see the round-3 plan's Decisions
table.

Two problems forced the shape below:

- `minio_endpoint` is `minio:9000` — an in-network hostname a browser cannot
  resolve, bound to `127.0.0.1` besides. A presigned URL minted against it
  points nowhere any client outside the compose network can reach. Presigned
  downloads need a public host to sign against.
- The round-1 network boundary put every internal service on a loopback bind
  except `api`, which was still directly published. That model — "the API is
  the one public service" — does not survive a public-internet threat model:
  it has no TLS, no rate limiting, and no host in front of it that could add
  either without application code changing.

`smart-model-tune`, the other frontend this platform talks to, is out of this
workspace's edit scope by the workspace `CLAUDE.md`'s golden rule (Lovable-managed,
edited only from its own editor). Which frontend ultimately serves production
traffic — `smart-model-tune`, this repo's own `frontend/` on `feat/web-ui`, or
something else — is a separate, still-open decision. This ADR does not make
that decision, and does not need to: the edge described below serves a static
root from a mountable volume (`SPA_DIST_DIR` in `docker-compose.yml`,
defaulting to a committed placeholder), so it is frontend-agnostic. Whichever
SPA wins later just points its build output at that mount.

## Decision

### The edge lives in this repo

`docker/edge.nginx.conf` is a new `edge` nginx service (`docker-compose.yml`),
not a change to any file `smart-model-tune` owns. It serves the SPA's static
root, reverse-proxies `/api/` and `/ws/` to `api`, and — on a second
`server_name` — reverse-proxies to `minio` for presigned downloads. This is
what decouples round 3 from the still-unmade "which frontend is the frontend"
decision: the edge does not care which SPA is mounted at `SPA_DIST_DIR`.

### Zero externally published ports

`api` and `edge` both bind `127.0.0.1` (`docker-compose.yml` — `api`'s
`ports:` comment records this explicitly as a change from round 1's "the API
is the one public service"). `cloudflared` has no `ports:` block at all —
it only makes outbound connections to Cloudflare's edge. `cloudflared` is
consequently the sole ingress; `worker`, `worker-cpu`, and `minio-init` also
publish nothing, since none of them serve anything.

**This is a coupling, not just a hardening step, and it needs to be stated as
one.** `docker/edge.nginx.conf` opens with `set_real_ip_from 0.0.0.0/0;
real_ip_header CF-Connecting-IP;` — trusting the `CF-Connecting-IP` header
from *any* address in the compose network, not just from `cloudflared`. That
is sound only because nothing else in the network can originate a request
carrying a forged `CF-Connecting-IP` and reach `edge` directly — which is true
only as long as `edge` itself is the sole thing anything public can reach, and
only as long as nothing else acquires a published port later. The moment any
service gets a `0.0.0.0`-bound port again, a caller could hit `edge` (or
`api`) directly with a spoofed header and both the rate limits and the
XFF-overwrite trust boundary described below become bypassable. There is
nothing in nginx itself that can tell the difference between the real
`cloudflared` container and an impostor on the same bridge network — the only
thing standing in for that check is `tests/unit/test_compose_port_exposure.py`
(`PUBLIC_SERVICES = set()`, an equality assertion, not a subset check, plus
`MUST_HAVE_NO_PORTS` for `cloudflared`/`worker`/`worker-cpu`/`minio-init`).
That test is the guard that keeps this precondition true; it is not
incidental coverage, it is the thing this whole trust model rests on.

`edge` itself further overwrites (not appends to) `X-Forwarded-For` with
`$remote_addr` on every proxied location, rather than using nginx's default
`$proxy_add_x_forwarded_for`. Combined with `edge` being the only hop between
a client and `api`, this guarantees exactly one trusted hop — `api`'s
`--forwarded-allow-ips=*` (uvicorn) reads a header it knows cannot have been
appended-to by anything upstream of `edge`, and `idempotency.client_host()`
gets a real, un-spoofable client address to key on.

### The tunnel is opt-in, and the tunnel id is passed as an argument

`cloudflared` carries `profiles: ["tunnel"]`, so a plain `docker compose up`
does not start it and `scripts/deploy_pasaflow_vm.sh` passes
`--profile tunnel` on **every** compose invocation, including the ones its
own post-deploy banner tells the operator to run later. A developer machine
has no tunnel credentials, and `restart: unless-stopped` would have
crash-looped the container forever; a deployment, conversely, must never come
up without it, because `api` and `edge` both bind `127.0.0.1` and the tunnel
is the only ingress. A stack brought up without the profile is healthy, exits
0, and is unreachable from the internet — the most expensive kind of failure,
which is why `tests/unit/test_compose_port_exposure.py` enumerates every
`docker compose` call in that script rather than checking the flag appears
somewhere in it.

The tunnel id reaches cloudflared as the argument to `tunnel run` in
`docker-compose.yml`, **not** as a `tunnel:` key in
`docker/cloudflared/config.yml`. Compose interpolates the compose file and
never the contents of a bind-mounted one, so a `${...}` placeholder in that
YAML would have arrived verbatim and the tunnel would have failed to start.
The interpolation deliberately uses `:-` and not `:?`: compose evaluates the
whole file before selecting services or profiles and treats `:?` as an error
on an *empty* value as well as an unset one, and `.env.example` ships the
variable blank — so `:?` broke `docker compose up`, `build`, `config` and
`ps` for anyone following the README quickstart. Requiring the value belongs
where a deployment happens, which is the deploy script's Phase 4.6
pre-flight.

### Rate limiting at the edge, not the application

`docker/edge.nginx.conf` defines five `limit_req_zone`s (`api_read` 20r/s,
`api_write` 2r/s via a `$request_method` map, `uploads` 12r/m, `inference`
5r/s, `storage` 30r/s) plus a `limit_conn_zone` for WebSocket connections.
This is deliberately nginx-level, not a new Redis-per-IP counter inside the
API process. ADR-010 already rejected that exact shape once, for concurrency
quotas: "*A Redis-per-IP side channel to give anonymous callers their own
per-actor-equivalent quota* — rejected. Ownership lives on
`Project.owner_id`, a Supabase `sub` claim, and there is no IP address
anywhere in the data model to bucket an anonymous request by." The same
reasoning applies to request-rate limiting — `api/services/quota.py:47-58`
makes the identical point in code: "there is no owner to join on for an
anonymous caller — the DB has no IP address to bucket unauthenticated
requests by, and this module deliberately does not add one... Do NOT paper
over this with a Redis-per-IP side channel." Rate limiting by IP belongs at
the layer that already terminates the connection and already sees the
address — nginx — not inside a service whose whole data model is
deliberately IP-blind. Cloudflare's own edge-level rate limiting sits in
front of this as a second, coarser layer; nginx's `limit_req` is the one this
repo owns and tests.

### Presigned GETs on a dedicated storage subdomain

Presigned downloads (`workers/storage.py::get_presign_client` /
`presigned_get_url`, `api/services/download_links.py`) are minted against
`MINIO_PUBLIC_URL` (e.g. `https://storage.slmpc.pasaflow.com`) and served
through a second `server_name` block in `docker/edge.nginx.conf` that reverse
proxies straight to `minio:9000` with `proxy_pass http://minio:9000;` — no
trailing slash, no `rewrite`.

The reason it has to be a whole subdomain and not a path prefix on the main
host is SigV4 itself: `presign_v4` builds `canonical_headers = "host:" +
url.netloc` (`minio/signer.py:275`) and signs the canonical URI directly —
there is no room in that signature for a proxy to rewrite the path on the way
through. `api/core/config.py`'s `_validate_minio_public_url` field validator
enforces this at boot: it rejects any `MINIO_PUBLIC_URL` with a path
component, and separately rejects a URL that spells out the scheme's default
port (`:443` on https, `:80` on http) — `presign_v4` signs the Host header
including the port verbatim, but a browser omits a default port when it
actually sends the request, so a signed-in default port would never match
what the browser sends.

`get_presign_client()` in `workers/storage.py` is a second, dedicated `Minio`
client (not the existing `get_minio_client()`, which points at the in-network
`minio:9000` and would sign the wrong Host entirely) constructed with an
explicit `region="us-east-1"`. That region argument is not cosmetic:
`Minio.get_presigned_url` calls `self._get_region(bucket_name)`
(`minio/api.py:2476`), and when a client has no region configured that method
issues a **live** `GET /{bucket}?location=` request (`minio/api.py:481-514`)
— not a cached lookup. Because this client's endpoint is the public storage
host, an unset region would send that lookup out through the tunnel and back
down again on every single presign call: slow at best, a hard failure at
worst if the storage vhost (which only proxies GET-with-signature traffic)
doesn't handle that verb. Passing `region="us-east-1"` explicitly
short-circuits `_get_region` at its first line and the network call never
happens. `workers/storage.py`'s own docstring calls this "the single
highest-risk line in the whole presigned-URL feature" for exactly this
reason: dropping it doesn't fail loudly, it just gets slow or flaky the first
time it runs against real infrastructure — which is why it also has a
dedicated no-network URL-shape mutation test (see the round-3 plan's Wave 1b
notes).

The proxy itself carries three settings load-bearing for the same signature:
`proxy_set_header Host $host;` (not nginx's default `$proxy_host`, which
would resolve to `minio:9000` and silently invalidate every signature),
`proxy_buffering off;` with `proxy_max_temp_file_size 0;` (so a multi-GB GGUF
export doesn't spool to the edge container's disk before streaming to the
client), and no `rewrite` anywhere in that `location` block.

### Reconciling with ADR-009's rejection of `?token=`

**This needs to be stated as a reconciliation, not left for a reviewer to
notice as a contradiction.** ADR-009 rejected a `?token=` query parameter for
the WebSocket credential on exactly one ground: "it would put credentials in
nginx access logs and browser history." A presigned MinIO URL puts its SigV4
signature (`X-Amz-Signature`, `X-Amz-Credential`, ...) in the query string —
the same shape of secret, in the same place.

The resolution is that the storage vhost never writes that query string to
disk in the first place. `docker/edge.nginx.conf` defines a dedicated
`log_format storage_safe '$remote_addr - [$time_local] "$uri" $status
$body_bytes_sent';` — `$uri` only, no `$args`, no `$request`, no
`$request_uri` — and the storage `server` block's `access_log` line uses that
format exclusively. The in-file comment on that log format spells out the
same point: "reconciling with ADR-009's rejection of `?token=` on the same
grounds." A live, still-valid signature genuinely does travel over the wire
in the query string (that part of ADR-009's threat is unavoidable for any
presigned-URL scheme — the credential has to be somewhere in the request),
but it never lands in a place an operator with disk access, a log
aggregator, or a backup could read it back out of later. ADR-009's objection
was specifically to *logging* the credential, not to it existing in a query
string at all; narrowing the fix to the log format closes the actual gap
without re-litigating the WebSocket subprotocol decision.

### `AUTH_REQUIRED` fatal in production, with Cloudflare Access as the interim gate

`api/core/config.py::_reject_unsafe_production_config` now refuses to boot
when `ENVIRONMENT=production` and `AUTH_REQUIRED=false` — previously a
`startup_warnings()` entry, now one of the offenders collected into the
single fatal `ValueError` (round 1's config guard; see that validator's
`offenders` list). This makes the frontend token patch documented in
[`docs/04-frontend-integration-smart-model-tune.md`](../04-frontend-integration-smart-model-tune.md)
(`apiFetch`'s `Authorization` header, `useTrainingWebSocket`'s
`Sec-WebSocket-Protocol` subprotocol) a hard release gate: production cannot
run without it, not merely shouldn't.

That gate cannot be flipped instantly — the frontend changes are tracked
separately and are not yet shipped, but the box still needs to go in front of
the public internet before they land. **Cloudflare Access**, configured on
both hostnames (`slmpc.pasaflow.com` and `storage.slmpc.pasaflow.com`) ahead
of anything being made reachable, is the interim gate for that window: it
authenticates at Cloudflare's edge, in front of `cloudflared`, independent of
whether `AUTH_REQUIRED` can be turned on yet application-side. It is
explicitly a bridge, not a replacement — the verification checklist for this
round requires setting Access up *before* any hostname goes live, and later
confirming that `AUTH_REQUIRED=false` genuinely refuses to boot once the
frontend changes land and the flag can flip for real.

### The interactive API docs are not exposed at the edge

`/docs`, `/redoc`, `/openapi.json` and `/ready` are served by `api/main.py`
without authentication, and none of them get a `location` block in
`docker/edge.nginx.conf`. They fall through to the SPA route and are simply
not reachable from the internet.

The reasoning is that under a public-internet threat model the spec is a
complete map of every endpoint, including the ones this round added, and
`/ready` fans out to Postgres, Redis, MinIO and a Celery `inspect` on every
call — a free amplification primitive for anyone who finds it. Neither is
worth publishing to reach an audience of about three people. The cost is
real and accepted: Swagger is no longer available on the deployed box, so
API exploration means `docker compose exec` plus a local port-forward, or
reading the committed `openapi.json`, which
`tests/unit/test_openapi_spec_is_current.py` keeps in lockstep with the app
precisely so that reading it is a genuine substitute.

`/health` is the deliberate exception: it stays proxied and unrate-limited,
because uptime monitors poll it and it touches no dependency.

This is guarded by `test_the_api_docs_and_ready_are_not_proxied`. It was
previously only a comment in the nginx config, which does not fail CI when
someone adds `location /docs { proxy_pass http://api:8000; }` — a one-line
change that looks helpful.

### An unrecognised Host is rejected, not served the SPA

The edge declares a `default_server` catch-all that returns **421** for any
Host it does not recognise.

Without it nginx promotes the first `server` block, which is the app — and
the app answers `try_files $uri /index.html`. The storage hostname is
written in three places nothing links: `server_name` in
`docker/edge.nginx.conf`, `hostname:` in `docker/cloudflared/config.yml`,
and `MINIO_PUBLIC_URL` in the environment. Only the first two can be
compared statically (and now are, by
`test_the_edge_and_the_tunnel_agree_on_every_hostname`); the third is
runtime configuration that no test can reach. So the design makes a
mismatch loud instead of assuming all three get typed identically: a
misrouted presigned fetch would otherwise return **HTTP 200 with an HTML
page**, and the browser would save `index.html` under the name
`model.gguf` — a self-consistent-looking success, which is far more
expensive to diagnose than a 4xx.

## Deferred

- **A real secret manager.** `docker-compose.yml`'s mlflow `command:` still
  interpolates `$MLFLOW_BACKEND_STORE_URI` into `sh -c`, which `docker
  inspect` exposes via `Config.Env` to anyone with docker-group access; only
  the plain-`ps`/`docker top` argv leak is closed this round (see that
  service's own comment). Round 3 closes the credential-default gap (fatal
  boot guard, `scripts/deploy_pasaflow_vm.sh` generating real secrets) but
  does not introduce Vault/SOPS/age or any equivalent. Reason: the box has
  exactly one operator and no multi-tenant secret-sharing requirement yet: a
  secret manager is real infrastructure to stand up and operate for a
  problem this deployment doesn't have yet.
- **MinIO per-user service accounts / STS.** Every presigned URL is signed
  with the platform's one root MinIO credential, scoped down only by which
  bucket/key the application chose to sign, not by any MinIO-side per-user
  policy. Ownership enforcement (404-not-403) happens entirely in
  `api/services/download_links.py` before a URL is ever minted; MinIO itself
  has no idea one user's key differs from another's. Real per-user STS
  credentials would need a token-exchange step this platform's auth model
  (Supabase JWT, no local user store — ADR-009) doesn't have a natural home
  for yet. Deferred until there's a concrete cross-tenant leak this doesn't
  already close.
- **A WebSocket heartbeat.** Cloudflare closes idle tunnel connections after
  a period of inactivity, which could in principle sever a long-idle
  `/ws/jobs/{job_id}` connection with no application-level frame to explain
  why. Not addressed this round because the frontend already reconnects with
  exponential backoff (`docs/04-frontend-integration-smart-model-tune.md`'s
  `useTrainingWebSocket` notes, `MAX_ATTEMPTS = 8`, capped at 30s) and
  ADR-008's snapshot-on-connect (`job:{job_id}:last` in Redis) means a
  reconnect after a Cloudflare-forced close repaints the current state
  immediately rather than requiring the next live frame to arrive. The
  failure mode is a brief, self-healing stall, not data loss — not worth a
  keepalive frame and a matching client-side timer until it demonstrably is.
- **Server-side zipping of multi-file exports.** `safetensors`/`lora`
  exports remain multiple objects with one presigned URL per file
  (`api/services/download_links.py::mint_model_download_url`, capped at
  `MAX_LISTING_OBJECTS = 200`) rather than a single zip. Zipping
  server-side would mean streaming potentially multi-gigabyte weights
  through the API process's memory/disk to produce the archive — exactly
  the cost presigned URLs exist to avoid. Deferred, not rejected: a
  client-side "download all" (sequential presigned fetches) is a smaller,
  frontend-only fix if this becomes a real UX complaint.

## Rejected

- **Caddy or Traefik with automatic ACME/Let's Encrypt**, instead of
  Cloudflare Tunnel. Rejected because both need an inbound port (80 and/or
  443) reachable from the internet for the ACME HTTP-01/TLS-ALPN-01
  challenge, and the pasaflow VM's inbound 80/443 are filtered at the host
  level; opening them would need root/firewall changes the deploy account
  (`slmuser`) has no sudo to make. Cloudflare Tunnel needs no inbound port
  at all — `cloudflared` only dials out — which is the only ingress shape
  compatible with this host's actual access.
- **Token-mode `cloudflared`** (ingress rules configured in Cloudflare's
  dashboard) as the primary form, instead of the locally-managed
  `docker/cloudflared/config.yml` used here. Rejected because dashboard
  ingress rules live outside this repository and outside any test this repo
  can run — nothing here would go red if someone later repointed the tunnel
  straight at `api:8000`, silently bypassing every rate limit and body-size
  cap `docker/edge.nginx.conf` enforces. Keeping the ingress rules in-repo
  is what lets `tests/unit/test_compose_port_exposure.py`'s
  `test_the_edge_is_the_only_ingress` parse `config.yml` and assert every
  `service:` entry points at `edge:80`.
- **Removing the raw `s3://...` URIs from API response bodies**
  (`Dataset.storage_uri`, `ModelArtifact.gguf_uri`/`safetensors_uri`), in
  favor of exposing only the new presigned-URL endpoints. Rejected because
  the frontend already reads those fields purely as booleans — "has this
  finished?" — not as fetchable addresses: `NewProject.tsx:79` and
  `useTrainingSimulator.ts:200` in `smart-model-tune` check for presence,
  never dereference the value. Removing them would be a breaking schema
  change for zero security benefit (the string is an internal bucket/key
  path, not a credential — nothing in it grants access on its own); the
  actual leak this round closes is `model_service.py` echoing that URI
  inside an HTTP error's `detail` string for `safetensors`/`lora` downloads,
  which is now a plain pointer at
  `GET /api/v1/models/{id}/download-url?format=...` instead (see
  `docs/02-api-reference.md`).

## Related

- [ADR-009](./ADR-009-supabase-jwt-auth.md) — the `?token=` rejection this
  ADR reconciles with, and the `AUTH_REQUIRED` phase split this ADR's fatal
  production guard now forecloses on skipping.
- [ADR-010](./ADR-010-cpu-gpu-queue-split-and-quotas.md) — the
  Redis-per-IP rejection this ADR's `limit_req`-at-the-edge choice mirrors,
  and `api/services/quota.py`'s identical reasoning in code.
- [`01-architecture.md` §4](../01-architecture.md#4-infrastructure-services) —
  the `edge`/`cloudflared` service entries and the zero-public-port network
  boundary in full.
- [`02-api-reference.md`](../02-api-reference.md) — the `/download-url`
  endpoint contracts these presigned URLs are served through.
