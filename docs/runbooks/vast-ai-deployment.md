# vast.ai Deployment Runbook

End-to-end recipe for deploying the SLM Fine-Tuning Platform on a freshly-rented vast.ai Linux VM. Distilled from session 2026-05-08 experience — including the gotchas that ate hours.

---

## 0. When to use this

- You need to actually fine-tune a model. The laptop GPU (GTX 1050 Ti, sm_61) cannot run Unsloth/QLoRA, so any real training must happen remotely.
- You want to run the **full** docker-compose stack (incl. `worker` + `ollama`) on real GPU hardware.
- This runbook assumes you already developed + validated the no-GPU half of the stack on your laptop (Step A — see `WORKING_LOG.md` Session 9). If not, do that first; deploying broken code to paid GPU time wastes money.

**Skip this runbook if** you only need to run infra services (postgres / redis / minio / mlflow / api) — those work locally on Docker Desktop without a GPU.

---

## 1. Why a Linux **VM**, not a regular Docker instance

vast.ai's standard "Docker instance" templates **cannot run Docker** — those instances are *themselves* Docker containers, with no `cap_sys_admin`, no `/var/run/docker.sock`, no nested daemon support. You will SSH in, type `docker --version`, and get `command not found`.

The fix is to rent a **Linux VM** instead. vast.ai launched VM rental on 2024-12-11 specifically for users who need full systemd + nested Docker. The template name to search for is **`Ubuntu 22.04 VM`**.

**Tradeoffs:**
- Boot time: 1–3 min (vs ~10 s for Docker instances) — sometimes 30+ min if RAM is high
- Smaller pool of compatible hosts (filter automatically narrows in the UI)
- SSH-only launch mode — no Jupyter / no entrypoint override
- **SSH key cannot be changed once the VM is running** — so set it up *before* renting

---

## 2. Pre-flight on your laptop (5 min, before opening vast.ai)

Do this before browsing the marketplace — nothing here is reversible-after-rental, so you don't want to discover a missing piece halfway through.

```powershell
# Confirm a public SSH key exists
Get-Content ~/.ssh/id_ed25519.pub  # or id_rsa.pub
```

Upload that key to **vast.ai → Account → SSH Keys** if it's not already there. If you skip this and create the VM, you have to destroy and re-rent.

Also have ready:
- Your **OpenRouter API key** (`sk-or-v1-...`) — get it from <https://openrouter.ai/keys>
- A **Docker Hub account** (free is fine) if you'll be pulling lots of images. Anonymous pulls are throttled at 100 / 6 hr per IP, and that's enough to stall the worker image build (see §6).
- The **branch name** you want to deploy. Default to `dev`; only use `main` for verified releases.

---

## 3. Pick a host that won't strangle the build (THIS IS THE BIG ONE)

The single most painful failure mode of this stack is the worker image's pytorch base layer (`pytorch/pytorch:2.5.1-cuda12.1-cudnn9-runtime` ≈ 3.1 GB). On a slow-network host, this single download takes **2+ hours** at ~340 KB/s. On a fast-network host, it finishes in 5 min.

In `cloud.vast.ai/create/`, the columns to pay attention to:

| Column | What it means | Target |
|--------|---------------|--------|
| **Inet Down** | Internet **download** Mbps — how fast it pulls Docker images | **≥ 500 Mbps** required, ≥ 1000 Mbps ideal |
| **Inet Up** | Internet **upload** Mbps — only matters if pushing artifacts | ≥ 100 Mbps fine |
| **DLP / DLPerf** | GPU compute score (Tensor TFLOPS / mem bandwidth) — **NOT network!** | ignore for selection |
| **Reliability** | % uptime | ≥ 99% |
| **Disk Bandwidth** | Sequential disk MB/s | ≥ 500 MB/s helps the build |

Sort by `Inet Down` descending and pick the cheapest host with both adequate speed and the GPU you need. **Do not** filter only on `$/hr` — a slightly cheaper host with bad network costs more in compute hours waiting for the build than a marginally pricier one that finishes fast.

**Recommended GPU for this project:** anything ≥ 12 GB VRAM and Ampere or newer.
- ✅ RTX 3060 (12 GB, Ampere sm_86) — original spec
- ✅ RTX 3090 (24 GB, Ampere sm_86)
- ✅ RTX 4090 (24 GB, Ada sm_89)
- ✅ RTX 5070 Ti (16 GB, Blackwell sm_120) — works but our `worker.Dockerfile` uses CUDA 12.1 + PyTorch 2.5.1, which falls back to PTX JIT on Blackwell. To get native kernels, bump to PyTorch 2.6+ + CUDA 12.8+.

---

## 4. Rent + wait

1. Click rent. Status goes `Scheduling → Loading → Running`.
2. **For VMs with high RAM (e.g., ≥ 32 GB), boot can take 30+ min** — vast.ai pre-allocates the full memory at boot, unlike Docker instances. Don't terminate. Don't refresh obsessively. The status banner says "VM instances with high CPU RAM can take longer to load" when this happens.
3. Once `Running`, click `>_CONNECT` to get the SSH command. It looks like:
   ```
   ssh -p <PORT> root@<IP> -L 8080:localhost:8080
   ```
   The `-L 8080:localhost:8080` is a port forward. **Edit it** to forward the API port: `-L 8000:localhost:8000`. The default `:8080` is what vast.ai uses for its own Jupyter/services, not us.

---

## 5. SSH in (use tmux from minute one)

```bash
# On your laptop:
ssh -p <PORT> root@<IP> -L 8000:localhost:8000

# First thing inside the VM — start a tmux so the build survives SSH disconnects
apt-get install -y tmux
tmux new -s slm
```

Detach later with `Ctrl+B D`, re-attach from a fresh SSH with `tmux attach -t slm`.

---

## 6. Fix the NVIDIA Container Toolkit (5 min — vast.ai's `Ubuntu 22.04 VM` ships it half-configured)

vast.ai's VM template comes with `/etc/docker/daemon.json` already pointing to `nvidia-container-runtime` — **but the binary isn't actually installed**. Both `--gpus all` and `--runtime=nvidia` will error out until you install the full `nvidia-container-toolkit` package.

There's a second gotcha layered on top: a fresh Ubuntu boots with `unattended-upgrades` running, which holds the dpkg lock. `apt-get install` will fail with:
```
E: Could not get lock /var/lib/dpkg/lock-frontend. It is held by process N (unattended-upgr)
```
**`systemctl stop unattended-upgrades` does NOT kill the in-flight process** — only the service unit. You need `systemctl mask` (to prevent restart) **plus** `kill -9 <PID>`.

Run this whole block (copy-paste safe, idempotent):

```bash
# A. Add NVIDIA repo (only if not already present)
# Note: --batch --no-tty --yes prevent `gpg --dearmor` from opening /dev/tty
# under non-interactive SSH (otherwise: "cannot open '/dev/tty'").
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
    gpg --batch --no-tty --yes --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
    tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
apt-get update

# B. Hard-kill unattended-upgrades (mask + force kill)
systemctl mask unattended-upgrades
kill -9 $(pgrep -f unattended-upgr) 2>/dev/null || true

# C. Install + configure
DEBIAN_FRONTEND=noninteractive apt-get install -y nvidia-container-toolkit
nvidia-ctk runtime configure --runtime=docker
systemctl restart docker

# D. Verify — must show your GPU
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

If step D shows your GPU's name + memory, you're done with the toolkit setup.

If step D errors with `nvidia-container-runtime: executable file not found` → step C did not actually install (re-check dpkg lock with `fuser /var/lib/dpkg/lock-frontend`).

---

## 7. Clone + branch + `.env`

```bash
cd ~
# Replace <BRANCH> with the branch you want to deploy:
#   - dev      → default integration branch (recommended)
#   - main     → only after a release PR has merged
#   - feature/<name> → smoke-testing a feature branch before PR
git clone -b <BRANCH> https://github.com/SurakiatP/slm-finetune-platform-dem.git slm-platform
cd slm-platform

cp .env.example .env
nano .env
# Fill in:
#   OPENROUTER_API_KEY=sk-or-v1-...        ← required (SDG + LLM judge)
#   POSTGRES_PASSWORD=<strong>             ← change from default
#   MINIO_ROOT_PASSWORD=<strong>           ← change from default
```

`.env` is gitignored, so it won't survive a `git pull --hard` or a fresh clone — set it once per VM.

> **⚠️ Stale-env pitfall:** if you edit `.env` *after* `docker compose up -d` has already started the `api` / `worker` containers, the running containers keep the old values (env is baked in at start). Re-roll them with `docker compose up -d api worker` after any `.env` change — `restart` alone is NOT enough on Compose v2.

---

## 8. (Optional) `docker login` to lift Docker Hub's anonymous rate limit

If your host's `Inet Down` is borderline, the unauth limit (100 pulls / 6 hr) can compound the slowness. Authenticated free accounts get 200 / 6 hr, paid get 5000 / day:

```bash
docker login   # enter your Docker Hub username + access token
```

Cheap insurance if you anticipate multiple builds.

---

## 9. Build + boot (10–25 min on a fast host, 2+ hrs on a slow one)

```bash
# Inside tmux from §5
docker compose build     # or `docker compose pull && docker compose build` to split network from CPU work

# Watch progress in another tmux pane (Ctrl+B %, then):
# tail -f /var/log/syslog | grep docker

docker compose up -d

# Wait for healthy (this is fast — usually <60 s after build is done)
watch -n 2 'docker compose ps'
# Expect: postgres / redis / minio / mlflow = healthy
#         api / worker / ollama = running
```

If `docker compose build` is downloading the worker base image at < 500 KB/s for more than 5 min — **abort and switch host**. Let me say that more loudly: at 340 KB/s, the 3.1 GB base image alone takes 2.5 hours, before any `pip install` happens. Sunk cost is real; do not bargain with a slow host.

---

## 10. Migrations + model pull (5–15 min depending on connection)

```bash
# 1. App schema (currently 3 migrations — count grows over time)
docker compose exec api alembic upgrade head
# Must show all 3 revisions applied in order:
#   Running upgrade  -> 0001_initial, initial schema
#   Running upgrade 0001_initial -> 0002_artifact_export_error_message, add export_error_message
#   Running upgrade 0002_artifact_export_error_message -> 0003_dataset_parent_id, add parent_dataset_id for hold-out splits
# (Cross-check current head with: docker compose exec api alembic heads)

# 2. Verify the schema looks right (6 application tables, all in `slm` DB)
docker compose exec postgres psql -U slm -d slm -c '\dt'

# 3. Pull a base model into Ollama (for inference + LLM judge)
docker compose exec ollama ollama pull llama3.2:3b
```

The `mlflow` database is separate from `slm` — created automatically by `docker/postgres-init.sql` on the first volume init. If you ever see MLflow tables appear in the `slm` database, that init script didn't run; see ADR / Session 9 entry for why.

---

## 11. Validate (3 min — proves the deployment isn't lying)

```bash
# A. Smoke
curl -s http://localhost:8000/health
# {"status":"ok"}

curl -s "http://localhost:8000/api/v1/projects?limit=5"
# {"items":[],"total":0,"limit":5,"offset":0}

# B. Unit tests (regression guards — includes snapshot harness)
docker compose exec api pip install -q -e '.[dev]'
docker compose exec api python -m pytest tests/unit/ -q
# Expect: 176 passed, 3 skipped (the 3 SDG full-pipeline snapshots skip
# until recorded OpenRouter fixtures land in tests/fixtures/recorded/openrouter/ —
# see docs/runbooks/snapshot_harness.md §4)
#
# If fewer than ~120 tests run, [dev] extras likely failed to install — check
# that syrupy/respx/moto/fakeredis are present: `pip list | grep -iE 'syrupy|respx|moto|fakeredis'`

# C. Integration test (with GPU)
docker compose exec -e INTEGRATION_HAS_GPU=1 api \
    python -m pytest -m integration tests/integration/test_full_flow.py -v
# Expect: 3 tests, possibly skips for SDG-dependent ones if OPENROUTER_API_KEY is absent
```

Test via Swagger UI: open `http://localhost:8000/docs` in your laptop browser (works because of `-L 8000:localhost:8000` from §5).

---

## 12. Cleanup before destroying the instance

If you produced anything you want to keep (model checkpoints in MinIO, MLflow runs, dataset uploads), get them off the VM **before** you destroy:

```bash
# Example: pull MLflow runs to your laptop via SSH
mkdir -p ./mlflow-export
ssh -p <PORT> root@<IP> 'docker compose exec -T postgres pg_dump -U slm mlflow' > mlflow-export/mlflow.sql
ssh -p <PORT> root@<IP> 'docker compose exec -T minio mc ls --recursive local/models/' > mlflow-export/models-listing.txt
# (Selectively rsync the artifacts you actually want)
```

Then destroy from the vast.ai web console — billing stops at the moment of destroy.

---

## 13. Common errors → fixes

| Symptom | Cause | Fix |
|---------|-------|-----|
| `docker: command not found` | You rented a Docker instance, not a VM | Destroy. Re-rent with `Ubuntu 22.04 VM` template. |
| `docker run --gpus all` → `could not select device driver` | NVIDIA Container Toolkit not installed (only the runtime path is in daemon.json) | Run §6 in full. |
| `apt-get install` → `Could not get lock /var/lib/dpkg/lock-frontend` | unattended-upgrades is running | `systemctl mask unattended-upgrades && kill -9 $(pgrep -f unattended-upgr)` then retry. |
| `docker compose build` stuck at < 500 KB/s for the pytorch base layer | Slow `Inet Down` host or Docker Hub anon throttling | (a) `docker login` first, (b) destroy + rent a faster host (§3). |
| API endpoints return 500 with `relation "model_artifacts" does not exist` | Forgot `alembic upgrade head` | Run §10 step 1. |
| API endpoints return 500 with weird MLflow-shaped errors | MLflow + app sharing the `slm` DB (init script didn't run) | Run `docker compose exec postgres psql -U slm -d postgres -c "CREATE DATABASE mlflow"` then re-create the mlflow service. |
| `worker` container exits with bitsandbytes / PyTorch errors on Blackwell GPU (RTX 50-series) | `worker.Dockerfile` pinned to PyTorch 2.5.1 + CUDA 12.1, predates Blackwell native support | Either accept PTX JIT (slower warmup, works) or upgrade base image to `pytorch/pytorch:2.6.0-cuda12.8-cudnn9-runtime` and bump `bitsandbytes` to a version with sm_120 kernels. |
| `ssh ... command` fails with `Connection reset` mid-run | VM is heavily loaded (build IO contention) — SSHd kills the channel | Smaller commands, or wait until build is in a quiet phase, or run the orchestrator script in `tmux` and just `tail -f` the log over a fresh SSH. |
| `docker compose up -d` fails with `nvidia-container-cli: initialization error: nvml error: driver/library version mismatch: unknown` | Kernel module loaded at boot doesn't match the userspace lib that `nvidia-container-toolkit` just installed (first `--gpus all` after §6 on a running host) | Reload the nvidia stack in-place without rebooting: `systemctl stop docker docker.socket && rmmod nvidia_uvm nvidia_drm nvidia_modeset nvidia && modprobe nvidia_uvm nvidia_drm && systemctl start docker`. Verify with `docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi` before retrying compose up. If `rmmod` complains "module is in use", stop all GPU containers first. |
| API container picks up empty / stale env values even though `.env` looks correct on disk | `api` (or `worker`) was started before `.env` had the value — env is baked in at container start | `docker compose up -d api worker` after editing `.env`. Plain `restart` is NOT enough on Compose v2. |
| `pytest tests/unit/` collects only ~5 tests | `[dev]` extras not installed — snapshot / mock libraries (`syrupy`, `respx`, `moto`, `fakeredis`) missing → 13 of 17 test modules fail to import | `docker compose exec api pip install -q -e '.[dev]'` then re-run. |

---

## 14. Cost estimate (one fine-tuning iteration)

Assumes RTX 3060 12 GB at $0.30/hr (vast.ai marketplace, May 2026):

| Phase | Time | Cost |
|-------|------|------|
| Pre-flight + boot | 5–35 min | $0.03–$0.18 |
| `docker compose build` (first time, fast host) | 15 min | $0.08 |
| `docker compose build` (slow host — abort first!) | 2+ hrs wasted | $0.60+ |
| Migrations + ollama pull | 10 min | $0.05 |
| Run one HPO sweep (10 trials × 3B model × 1k samples) | 1–3 hr | $0.30–$0.90 |
| **Total — successful run** | ~ 1.5–4 hr | **$0.50–$1.30** |

Re-deploys (after destroy) re-pay the build/pull cost. So **don't destroy until the iteration is genuinely done** — you can `docker compose stop` (preserves volumes) and `docker compose start` later if it's the same instance.

---

## 15. Quick-paste cheat sheet (the 30-second version)

```bash
# After ssh -p <PORT> root@<IP> -L 8000:localhost:8000

# Step 6 — NVIDIA toolkit
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --batch --no-tty --yes --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
apt-get update
systemctl mask unattended-upgrades
kill -9 $(pgrep -f unattended-upgr) 2>/dev/null || true
DEBIAN_FRONTEND=noninteractive apt-get install -y nvidia-container-toolkit
nvidia-ctk runtime configure --runtime=docker
systemctl restart docker
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi  # verify — if "nvml driver/library mismatch", see §13

# Step 7-9 — repo + boot   (set BRANCH to dev / main / feature/<name>)
BRANCH=dev
apt-get install -y tmux
tmux new -s slm
cd ~ && git clone -b "$BRANCH" https://github.com/SurakiatP/slm-finetune-platform-dem.git slm-platform && cd slm-platform
cp .env.example .env && nano .env       # set OPENROUTER_API_KEY etc — BEFORE compose up
docker login                              # optional: avoid Docker Hub rate limit
docker compose build && docker compose up -d
watch -n 2 'docker compose ps'            # Ctrl+C when all healthy → Ctrl+B D to detach tmux

# Step 10 — migrations + base model
docker compose exec api alembic upgrade head   # expect 3 revisions: 0001 → 0002 → 0003
docker compose exec ollama ollama pull llama3.2:3b

# Step 11 — validate
curl -s http://localhost:8000/health
docker compose exec api pip install -q -e '.[dev]'
docker compose exec api python -m pytest tests/unit/ -q   # expect 176 passed, 3 skipped
```
