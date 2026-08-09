# Runbook: opening public ingress and verifying it

**When to use this:** the Cloudflare tunnel is about to go up (or just did),
and you are deciding whether to hand the public URL to the `smart-model-tune`
team.

**Why it needs its own runbook:** everything the platform has been verified
for so far was checked **over loopback from inside the box** — `curl
localhost:8081`. That path bypasses Cloudflare, the `edge` nginx, TLS, CORS,
and the WebSocket upgrade. A browser uses none of it. The first time the real
path gets exercised is the first time a frontend developer tries to use the
product, and the failures it produces (a CORS block, a 400 from the host
guard, a WebSocket that never upgrades) all look like "the backend is broken"
from their side.

`scripts/verify_public_ingress.sh` is that first exercise, run deliberately
instead of accidentally.

## Before you open the tunnel: fix CORS

**This is the one change that is guaranteed to be needed.** The box's `.env`
currently carries:

```
API_CORS_ORIGINS=http://localhost:3000,http://localhost:5173,https://gentle-fine-tuner.lovable.app
```

`gentle-fine-tuner.lovable.app` is a **stale Lovable preview origin**. Unless
the frontend still deploys there, no production origin is allowed, and every
call from the real app will fail in the browser while answering 200 to curl.

Two properties of this setting that cause repeat mistakes:

- **It REPLACES the code default, it does not merge.** Once `API_CORS_ORIGINS`
  is set at all, the `localhost:3000` / `localhost:5173` / lovable defaults in
  `api/core/config.py` stop applying. List every origin you need — including
  the dev ones, if developers still run locally against this box.
- It takes effect on API restart: `docker compose up -d --no-deps api`.

Ask the frontend team for their **exact deployed origin** (scheme + host, no
path, no trailing slash) and put it in the list. Guessing it wastes a round
trip; a wrong scheme (`http` vs `https`) fails exactly as hard as a wrong host.

`API_ALLOWED_HOSTS` needs **no change** — it is absent from the box's `.env`,
and both "absent" and "present but empty" mean allow-all (the round-3.5 S1
fix). Do not "helpfully" set it to a single hostname unless you intend to
maintain that list; a Host the guard does not know gets a 400 that reads like
an outage.

## Opening the tunnel (needs the Cloudflare account holding `pasaflow.com`)

These steps need a human with the account — not Claude, not the box.

1. `cloudflared tunnel login`
2. `cloudflared tunnel create <name>` — note the tunnel UUID
3. Put the UUID in the box's `.env` as `CLOUDFLARE_TUNNEL_ID=` (deploy Phase
   4.6 **hard-fails without it, by design** — that failure is the guard
   working, not a bug)
4. Create the DNS routes for the API hostname and, if you want browser-
   reachable downloads, the **storage hostname** (ADR-011: presigned URLs
   point at a dedicated storage subdomain, not through the API)
5. Add the Cloudflare Access policy over both hostnames (round-3 decision
   #11 — the interim gate while `AUTH_REQUIRED=false`)
6. Re-run `bash scripts/deploy_pasaflow_vm.sh` — Phase 4.6 now passes and
   `cloudflared` (profile `tunnel`) starts

## Verify

Run **from a laptop, not from the box.** From the box, loopback reaches
services the internet cannot, so the "these must NOT be public" group would
pass for the wrong reason. The script detects it is on the box and downgrades
that group to advisory, but a real answer needs an outside vantage point.

```bash
PUBLIC_API_URL=https://<api-hostname> \
FRONTEND_ORIGIN=https://<the-frontend-real-origin> \
STORAGE_URL=https://<storage-hostname> \
  bash scripts/verify_public_ingress.sh
```

Exit code 0 means every check passed. Non-zero means **do not hand over the
URL yet** — the script prints which group failed and what to do about it.

### What the four groups prove

| Group | Proves | If it fails |
|---|---|---|
| **A. Reachability** | tunnel → edge → api answers over HTTPS | `000` = tunnel not routing (check `docker compose logs cloudflared` and DNS). `400` = TrustedHost rejected the hostname — looks like an outage, is not |
| **B. CORS** | the frontend's real origin is allowed, on the preflight **and** the actual response | see the CORS section above; this is the failure that produces a working curl and a broken app |
| **C. Screen endpoints** | the 9 REST endpoints + WebSocket upgrade the 12 wired screens need | a failing single endpoint is usually the edge nginx location list; a failing WebSocket is the `Upgrade`/`Connection` headers or the tunnel ingress rule |
| **D. Negative checks** | Postgres/Redis/MinIO/MLflow/Prometheus/Ollama are NOT reachable publicly; `/docs` does not serve Swagger; `/metrics` does not serve Prometheus output | any reachable infra port is a **P0 violation** — stop and fix before handing over |

Group D's `/docs` check compares **bodies**, not status codes: the edge falls
through to the SPA, so `/docs` legitimately returns 200 with the SPA index.
A 200 there is not a decision-#12 violation; Swagger markup in the body is.

Group D's `/docs` and `/metrics` checks are **skipped with a warning** when
group A failed — "nothing leaked" would otherwise just mean "nothing
responded", which is a pass for the wrong reason.

## Known outages that are NOT ingress problems

Do not chase these while verifying ingress; they are tracked separately and
need root on the host:

- **`GET /inference/models` → 502.** Ollama is down (`Exited (128)`), blocked
  by the host's `no-cgroups = false` in
  `/etc/nvidia-container-runtime/config.toml`. Playground and ModelDetail's
  chat cannot work until the box owner fixes it. The script reports this as a
  warning, not a failure.
- **No GPU worker** (`slm-worker` stuck in `Created`, same host bug) ⇒ new
  training, export and evaluation runs cannot start. Existing rows still list
  and display fine, so the read-only screens verify normally.

## After a green run

1. Add a row to the Verification log below.
2. Tell the frontend team: the base URL, that CORS now allows their origin,
   and point them at
   `docs/patches/smart-model-tune-unused-endpoints.md` for what to wire.
3. Mention the two known outages above so a 502 on Playground does not get
   reported as a broken deployment.

## Verification log

Every row must come from an actual run of the script against the public URL —
not from reading this document. Add a row per run; never overwrite one.

| Date | Operator | Public URL | Frontend origin | Result | Notes |
|------|----------|------------|-----------------|--------|-------|
|      |          |            |                 |        |       |
