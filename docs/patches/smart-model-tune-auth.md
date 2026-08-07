# Patch bundle: `smart-model-tune` auth + error-handling + download-url

**Status: handoff, not applied.** Nothing in this file has been run against
`smart-model-tune/` — that repo is Lovable-managed and outside this repo's
edit scope (workspace `CLAUDE.md` golden rule). Every snippet below is a
paste-ready diff the frontend team applies themselves in the Lovable editor,
plus the context needed to understand *why* each change is required.

**Why this exists at all.** The backend refuses to boot with
`ENVIRONMENT=production` + `AUTH_REQUIRED=false` (see
[`../adr/ADR-009-supabase-jwt-auth.md`](../adr/ADR-009-supabase-jwt-auth.md)).
`smart-model-tune` was chosen as the canonical frontend
(workspace `TASK_TRACKER.md`), and it currently sends **zero**
`Authorization` headers anywhere — so the production flip is blocked on
this patch landing. This document exists to unblock that without anyone on
this side touching `smart-model-tune/`'s files directly.

This document turns [`../04-frontend-integration-smart-model-tune.md`](../04-frontend-integration-smart-model-tune.md)'s
prose (the "⚠️ Required Frontend Change" section) into applicable code, plus
covers four things that doc does not: the four hand-rolled fetches
individually, structured error handling (429/402/503), the WS close-code
trap, and the `/download-url` wiring. Where this document and `04-…md`
disagree, see **"Where this disagrees with `docs/04-…md`"** at the bottom —
short version: they don't, on substance; `04-…md` is coarser-grained on a
couple of points and this fills in the gaps.

All line numbers below were read directly from `smart-model-tune/src/...`
on 2026-08-08. Re-run the `grep`/`sed` commands shown before applying if any
time has passed — Lovable auto-commits on every editor change, so the file
may have moved.

---

## 0. Prerequisite: `engineApi.ts` has no imports yet

Verify before starting:

```
$ grep -c "^import" src/lib/engineApi.ts
0
```

`src/lib/engineApi.ts` is 350 lines of types, helpers, and fetch calls with
**not one `import` statement** — it doesn't even import its own types from
elsewhere, everything is declared inline in the file. This matters because
every step below that touches `engineApi.ts` assumes the Supabase client
import exists. It doesn't yet. Add it once, at the top of the file, before
any other change in this document:

```ts
// engineApi.ts — new line 1
import { supabase } from "@/integrations/supabase/client";
```

`@/integrations/supabase/client` already exists and is correct — see
`src/integrations/supabase/client.ts:11-17` (`createClient` with
`persistSession: true`, `autoRefreshToken: true`). Nothing there needs to
change; it's cited here only so the import path above is verifiably right.

---

## 1. `apiFetch` — attach the `Authorization` header

**File:** `src/lib/engineApi.ts:149-159`

**Before:**

```ts
async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${ENGINE_HOST}/api/v1${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...NGROK_HEADER, ...init?.headers },
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`Engine API ${res.status}: ${body}`);
  }
  return res.json() as Promise<T>;
}
```

**After:**

```ts
async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const { data: { session } } = await supabase.auth.getSession();
  const authHeader = session?.access_token
    ? { Authorization: `Bearer ${session.access_token}` }
    : {};

  const res = await fetch(`${ENGINE_HOST}/api/v1${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...NGROK_HEADER, ...authHeader, ...init?.headers },
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`Engine API ${res.status}: ${body}`);
  }
  return res.json() as Promise<T>;
}
```

Notes:
- `apiFetch` is **already `async`** — no signature change, just a new
  `await` at the top of the body.
- Header **spread order matters and is preserved**: `Content-Type` →
  `NGROK_HEADER` → `authHeader` → `init?.headers`. The caller-supplied
  `init.headers` stays last so a call site can still override any of the
  three defaults (including `Authorization`, if a future call ever needs
  a different credential) — this patch only inserts `authHeader` between
  the ngrok header and the caller override, it doesn't reorder anything
  that was already there.
- **Send no header rather than a stale one.** An expired token is a `401`
  the caller can react to; a stale one sent anyway is a `401` that looks
  identical but wastes a round trip. Reading `getSession()` fresh on every
  call (not once at module load, not cached in a variable) is what keeps
  it correct — Supabase's `autoRefreshToken: true` handles rotation, this
  just has to ask each time instead of caching.
- `getSession()` is a local, synchronous-fast read against Supabase's
  in-memory/localStorage session state, not a network call — this does not
  add a real round trip.

---

## 2. The four hand-rolled fetches that bypass `apiFetch`

`apiFetch` is the ~90% path — most calls in the file already go through
it and get the fix in §1 for free. Four calls build their own `fetch` and
need the same header added individually.

### 2a. Upload-seed — `src/lib/engineApi.ts:200`

**Before (`:194-210`):**

```ts
  const res = await fetch(`${ENGINE_HOST}/api/v1/datasets/upload-seed`, {
    method: "POST",
    headers: { ...NGROK_HEADER },
    body: form,
    // No Content-Type header — browser sets multipart boundary automatically
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`Engine API ${res.status}: ${body}`);
  }
  return res.json() as Promise<EngineSeedUploadResponse>;
```

**After:**

```ts
  const { data: { session } } = await supabase.auth.getSession();
  const authHeader = session?.access_token
    ? { Authorization: `Bearer ${session.access_token}` }
    : {};

  const res = await fetch(`${ENGINE_HOST}/api/v1/datasets/upload-seed`, {
    method: "POST",
    headers: { ...NGROK_HEADER, ...authHeader },
    body: form,
    // Still no Content-Type header — browser sets multipart boundary automatically.
    // Do not add Content-Type here even for the auth patch; it will break the
    // multipart boundary the browser generates.
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`Engine API ${res.status}: ${body}`);
  }
  return res.json() as Promise<EngineSeedUploadResponse>;
```

### 2b. `/inference/models` — `src/lib/engineApi.ts:322`

**Before (`:321-325`):**

```ts
export async function engineListInferenceModels(): Promise<{ object: "list"; data: EngineInferenceModel[] }> {
  const res = await fetch(`${ENGINE_HOST}/api/v1/inference/models`, { headers: { ...NGROK_HEADER } });
  if (!res.ok) throw new Error(`Engine API ${res.status}`);
  return res.json();
}
```

**After:**

```ts
export async function engineListInferenceModels(): Promise<{ object: "list"; data: EngineInferenceModel[] }> {
  const { data: { session } } = await supabase.auth.getSession();
  const authHeader = session?.access_token
    ? { Authorization: `Bearer ${session.access_token}` }
    : {};
  const res = await fetch(`${ENGINE_HOST}/api/v1/inference/models`, {
    headers: { ...NGROK_HEADER, ...authHeader },
  });
  if (!res.ok) throw new Error(`Engine API ${res.status}`);
  return res.json();
}
```

Note per `docs/04-…md`: this listing is owner-scoped once auth is on —
`slm/…` entries become the caller's own fine-tunes only, base models stay
visible to everyone. Anonymous (no token) callers still get the unfiltered
list under `AUTH_REQUIRED=false`; this is why testing this endpoint
specifically is a good pre-flip smoke check (see the verification
checklist at the bottom).

### 2c. `/inference/chat/completions` — `src/lib/engineApi.ts:330`

**Before (`:327-338`):**

```ts
export async function engineChatCompletion(req: EngineChatRequest): Promise<EngineChatResponse> {
  const res = await fetch(`${ENGINE_HOST}/api/v1/inference/chat/completions`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...NGROK_HEADER },
    body: JSON.stringify({ ...req, stream: false }),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`Engine API ${res.status}: ${body}`);
  }
  return res.json() as Promise<EngineChatResponse>;
}
```

**After:**

```ts
export async function engineChatCompletion(req: EngineChatRequest): Promise<EngineChatResponse> {
  const { data: { session } } = await supabase.auth.getSession();
  const authHeader = session?.access_token
    ? { Authorization: `Bearer ${session.access_token}` }
    : {};
  const res = await fetch(`${ENGINE_HOST}/api/v1/inference/chat/completions`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...NGROK_HEADER, ...authHeader },
    body: JSON.stringify({ ...req, stream: false }),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`Engine API ${res.status}: ${body}`);
  }
  return res.json() as Promise<EngineChatResponse>;
}
```

### 2d. `/health` — `src/lib/engineApi.ts:344` — **does NOT need the header**

**Current (`:342-349`), leave as-is:**

```ts
export async function engineHealthCheck(): Promise<boolean> {
  try {
    const res = await fetch(`${ENGINE_HOST}/health`, { headers: { ...NGROK_HEADER } });
    return res.ok;
  } catch {
    return false;
  }
}
```

`GET /health` (`api/main.py:282-290` in this repo) is unauthenticated **by
design** — it has no `Depends(require_user)` or any auth dependency at all,
deliberately, so a supervisor/uptime monitor can hit it without a credential
and it never depends on Postgres/Redis/MinIO. Do not add an `Authorization`
header here; it would be accepted-and-ignored at best and is pointless
churn at worst.

**This matters more than it looks.** Once `AUTH_REQUIRED=true` is flipped
and every *other* endpoint starts 401ing for missing/expired tokens,
`/health` will keep returning `200` regardless — same as before the flip.
If the only signal a build/monitoring script checks is "does `/health`
return 200", it will report the app as healthy while every real API call
is failing with 401. Don't use `engineHealthCheck()`'s result as evidence
that the auth patch is working — use the verification checklist at the
bottom instead, which checks an authenticated vs. anonymous call to a real
endpoint.

---

## 3. WebSocket — subprotocol, not query param

**File:** `src/hooks/useTrainingWebSocket.ts:92`

**Before (`connect()`, `:88-136`, relevant slice):**

```ts
    function connect() {
      if (destroyed || attempts >= MAX_ATTEMPTS) return;
      attempts++;

      const ws = new WebSocket(buildWsUrl(jobId));
      wsRef.current = ws;

      ws.onopen = () => {
        if (!destroyed) setConnected(true);
      };
      // ...onmessage/onerror unchanged...
```

**After:**

```ts
    async function connect() {
      if (destroyed || attempts >= MAX_ATTEMPTS) return;
      attempts++;

      const { data: { session } } = await supabase.auth.getSession();
      if (destroyed) return; // unmounted while the await was in flight

      const token = session?.access_token;
      const ws = token
        ? new WebSocket(buildWsUrl(jobId), ["bearer", token])
        : new WebSocket(buildWsUrl(jobId));
      wsRef.current = ws;

      ws.onopen = () => {
        if (!destroyed) setConnected(true);
      };
      // ...onmessage/onerror/onclose unchanged, see §4 below for onclose...
```

`connect()` already runs inside a `useEffect` (`:75-145`) and is already
called as a plain (non-awaited) function from both the initial call
(`:138`) and the `setTimeout(connect, delay)` reconnect (`:133`) — making
it `async` needs no change at either call site; a `setTimeout` callback
and an effect body both tolerate an async function returning a Promise
they don't await.

Also add the same top-of-file import as `engineApi.ts` needed (§0):

```ts
// useTrainingWebSocket.ts — new import
import { supabase } from "@/integrations/supabase/client";
```

**Why a subprotocol and not `?token=<jwt>` in the URL:** two independent
reasons, both hard constraints, not style preferences.

1. **Browsers cannot set arbitrary headers on a WebSocket handshake.**
   `new WebSocket(url, headers)` is not an API that exists — the only
   extensibility point the browser exposes on the handshake is the
   `Sec-WebSocket-Protocol` list, which is exactly the second constructor
   argument used above.
2. **A query-string token lands in logs.** `?token=...` is part of the
   request URL, and nginx (and most reverse proxies/CDNs) logs full
   request URLs to access logs by default — a credential in the URL ends
   up written to disk in plaintext on every hop. ADR-009 in this repo
   explicitly rejected the `?token=` approach for this reason. The
   subprotocol value is part of the WS handshake headers, not the URL, so
   it does not appear in a standard nginx access log line.

The backend (`api/routers/websocket.py:28-42` in this repo) expects
**exactly** the two-value form `["bearer", "<jwt>"]` — one value, more than
two values, or a first value that isn't (case-insensitively) `"bearer"` are
all treated as a malformed credential and rejected, not silently accepted
with degraded behavior. It selects and echoes the subprotocol back on
`ws.accept(subprotocol="bearer")` if it accepts the connection — nothing
else in this hook needs to read or check that echo for the connection to
work; browsers require the server to have echoed *a* subprotocol from the
offered list or they refuse to complete the handshake themselves, but that
happens transparently.

---

## 4. Close-code handling — don't build a 4401-vs-4403 branch, but do stop wasting all 8 retries

**File:** `src/hooks/useTrainingWebSocket.ts:86` (constant), `:127-135` (`onclose`)

**Current (`:83-90`, `:127-135`):**

```ts
    let destroyed = false;
    let terminal = false;
    let attempts = 0;
    const MAX_ATTEMPTS = 8;
    // ...
      ws.onclose = () => {
        if (destroyed) return;
        setConnected(false);
        // Retry with exponential back-off if not yet finished
        if (!terminal && attempts < MAX_ATTEMPTS) {
          const delay = Math.min(1000 * 2 ** attempts, 30000);
          reconnectTimer.current = setTimeout(connect, delay);
        }
      };
```

`onclose` never inspects `event.code` at all today. If the backend rejects
the connection for an auth reason, this reconnect loop burns all 8 attempts
(delays `2s, 4s, 8s, 16s, 30s, 30s, 30s, 30s` ≈ 2.5 minutes total) before
giving up silently — indistinguishable, from the user's side, from a
network problem, because it *looks* like a network problem the whole time.

**What the backend actually sends** (`api/routers/websocket.py:46-51,
93-94` in this repo):

- `4401` — no credential offered while `AUTH_REQUIRED=true`.
- `4403` — credential verified, but the `job_id` is unknown or belongs to
  a different owner.

**Why you cannot switch on either code client-side.** A close code sent
before `ws.accept()` is called is not a WebSocket close frame — RFC 6455
close frames only exist on an already-established connection. Rejecting
the handshake itself is delivered to the browser as a failed HTTP
upgrade, and every major browser reports that to `onclose` as
**`event.code === 1006`** with `event.reason === ""`, regardless of what
numeric code the server tried to send. `4401` and `4403` never reach
JavaScript as those numbers — they're only visible server-side (in
backend logs), not client-side. Do not write an `if (event.code === 4401)`
branch; it is dead code that will never execute.

**The practical fix instead**: stop retrying after N rapid closes with no
successful frame ever received, not by inspecting a code that never
arrives. A close that happens within, say, 500ms of the *previous* close
(i.e., the connection never got past the handshake) is the observable
signature of a rejection — a real network flap tends to at least complete
a handshake sometimes across 8 attempts over 2.5 minutes. Track "hard-fail
streak" and cut the loop short once it's clearly not intermittent:

```ts
    let destroyed = false;
    let terminal = false;
    let attempts = 0;
    let lastCloseAt = 0;
    let neverConnectedStreak = 0;
    const MAX_ATTEMPTS = 8;
    const HARD_FAIL_STREAK_LIMIT = 3; // give up early if the handshake itself
                                       // is failing every time, not just a flap
    // ...
      ws.onopen = () => {
        if (!destroyed) {
          setConnected(true);
          neverConnectedStreak = 0; // a real handshake succeeded — reset the streak
        }
      };
      // ...
      ws.onclose = () => {
        if (destroyed) return;
        setConnected(false);
        const now = Date.now();
        const wasFastFail = now - lastCloseAt < 500 || lastCloseAt === 0;
        lastCloseAt = now;
        // A handshake that never opened counts toward the streak regardless
        // of elapsed time; `onopen` above resets it on any real success.
        neverConnectedStreak++;
        if (!terminal && attempts < MAX_ATTEMPTS && neverConnectedStreak < HARD_FAIL_STREAK_LIMIT) {
          const delay = Math.min(1000 * 2 ** attempts, 30000);
          reconnectTimer.current = setTimeout(connect, delay);
        }
        // else: give up — surface something to the caller here if you want a
        // "connection rejected" banner distinct from "still trying"; this hook
        // doesn't currently expose a dedicated state for that, `connected: false`
        // combined with no further attempts is the only signal today.
      };
```

This is a **defensive client-side heuristic**, not a fix for the underlying
gap — the underlying gap (browser sees `1006` for both an auth rejection
and a real network blip, full stop) is a backend-visibility limitation
tracked separately in the workspace `TASK_TRACKER.md`, not something
fixable from the frontend. Treat the snippet above as optional
hardening, not a blocking requirement for the auth flip — the existing
unconditional 8-attempt retry is *safe* (it just wastes time and looks
confusing), it is not broken.

---

## 5. Structured errors for 429 / 402 / 503 — read `Retry-After`

**Problem locations** — every non-2xx response collapses into one
`throw new Error(...)`, discarding the status code and any headers:

- `src/lib/engineApi.ts:154-157` (inside `apiFetch`)
- `src/lib/engineApi.ts:206-209` (upload-seed)
- `src/lib/engineApi.ts:333-336` (chat completions)
- (also `:323` for `/inference/models`, which throws with no body text at
  all today — `throw new Error(\`Engine API ${res.status}\`)`)

The backend already sends a real `Retry-After` header on the two cases that
matter for a "should I show a retry countdown" UI:

- **429** (quota exceeded) — `api/services/quota.py:211,223` in this repo
  sets `Retry-After` from `settings.quota_retry_after_seconds` (default 30s).
- **503** (OpenRouter circuit breaker open) — `api/services/circuit_breaker.py:296-303`
  sets `Retry-After` from the breaker's computed backoff.
- **402** (monthly budget exceeded) — `api/routers/usage.py` — no
  `Retry-After` (retrying sooner doesn't help; the budget resets monthly,
  not on a short timer), so `retryAfter` will be `null` for a 402 and
  callers should not render a countdown for it.

Add a small typed error and use it everywhere `Error` was thrown for a
non-2xx:

```ts
// engineApi.ts — add near the top, after the NGROK_HEADER constant
export class EngineError extends Error {
  readonly status: number;
  readonly retryAfter: number | null;

  constructor(status: number, body: string, retryAfter: number | null) {
    super(`Engine API ${status}: ${body}`);
    this.name = "EngineError";
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

function parseRetryAfter(res: Response): number | null {
  const raw = res.headers.get("Retry-After");
  if (!raw) return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}
```

Then in `apiFetch` (replacing the `if (!res.ok)` block from §1's "after"
snippet):

```ts
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new EngineError(res.status, body, parseRetryAfter(res));
  }
```

Apply the same replacement at `:206-209` (upload-seed) and `:333-336`
(chat completions); for `:323` (`/inference/models`, which currently
doesn't even read the body), also add the `await res.text().catch(() => "")`
read so the error message isn't empty:

```ts
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new EngineError(res.status, body, parseRetryAfter(res));
  }
```

Call sites that want to branch on status can now do:

```ts
try {
  await engineChatCompletion(req);
} catch (e) {
  if (e instanceof EngineError && e.status === 429 && e.retryAfter) {
    // show "try again in {e.retryAfter}s"
  } else if (e instanceof EngineError && e.status === 402) {
    // show "monthly budget exceeded" — no retry countdown, retryAfter is null
  } else if (e instanceof EngineError && e.status === 503) {
    // show "generation service temporarily unavailable, retry in {e.retryAfter}s"
  }
  // else: fall through to the existing generic error toast
}
```

This is additive — `EngineError extends Error`, so any existing
`catch (e) { toast(e.message) }` call site keeps working unchanged with no
migration required; only call sites that want the finer-grained behavior
need to add an `instanceof EngineError` check.

---

## 6. `/download-url` — wire the Export tab's Download buttons to a real endpoint

**Problem, `src/pages/ModelDetail.tsx`:**

- `exportFormats` (`:13-17`) is a hardcoded array with **fabricated sizes**:

  ```ts
  const exportFormats = [
    { format: "SafeTensors", size: "1.2 GB", description: "Default format, best compatibility with HuggingFace" },
    { format: "GGUF", size: "0.8 GB", description: "Optimized for llama.cpp and local inference" },
    { format: "ONNX", size: "1.0 GB", description: "Cross-platform deployment with ONNX Runtime" },
  ];
  ```

  Note **`ONNX` is not just fake-sized, it does not exist as a backend
  format at all** — `ArtifactFormat` (`api/schemas/enums.py:53-58` in this
  repo) is `lora | gguf | safetensors` only, three values, no fourth. This
  row should be deleted, not just re-pointed at a real size.

- The Download button (`:220-222`) has **no `onClick`**:

  ```tsx
                    <Button variant="outline" size="sm" className="gap-2">
                      <Download className="h-3.5 w-3.5" /> {t("modelDetail.download")}
                    </Button>
  ```

**The real endpoints** (added this backend cycle, `api/schemas/download_links.py`
and `api/routers/{datasets,models}.py` in this repo):

- `GET /api/v1/datasets/{id}/download-url` → `DatasetDownloadUrlResponse`:
  ```ts
  interface DatasetDownloadUrlResponse {
    url: string;
    filename: string;
    content_type: string;
    expires_at: string; // ISO datetime
    expires_in: number; // seconds from mint time
  }
  ```
- `GET /api/v1/models/{id}/download-url?format=gguf|safetensors|lora` →
  `ModelDownloadUrlResponse`:
  ```ts
  interface ModelDownloadFile {
    key: string;       // full MinIO object key
    name: string;      // basename, safe for Content-Disposition
    size_bytes: number; // REAL size, in bytes — this is what replaces the fake "1.2 GB" strings
    url: string;        // presigned, ready to open directly
  }
  interface ModelDownloadUrlResponse {
    format: "gguf" | "safetensors" | "lora";
    files: ModelDownloadFile[]; // gguf: always exactly 1 entry. safetensors/lora: can be many
    expires_at: string;
    expires_in: number;
    truncated: boolean; // true if the directory had more files than the response covers
  }
  ```

Both endpoints require an authenticated, owning caller
(`Depends(require_user)`) once `AUTH_REQUIRED=true` — same header as
everything else in this document.

**Important: these URLs are presigned and go directly to MinIO/`storage.…`,
not through `apiFetch`.** Per `docs/04-…md`'s ADR-011 section: the
`storage.slmpc.pasaflow.com` subdomain these URLs point to is *not* Engine
API — don't run the returned `url` through `apiFetch` or attach an
`Authorization` header to it; the credential is already embedded in the
presigned query string. Fetch the *minting* endpoint through `apiFetch`
(so it carries the Supabase bearer token, proving you're the owner), then
navigate the browser to the `url` it returns as-is:

```ts
// engineApi.ts — new function
export interface EngineDatasetDownloadUrl {
  url: string;
  filename: string;
  content_type: string;
  expires_at: string;
  expires_in: number;
}

export async function engineGetDatasetDownloadUrl(datasetId: string): Promise<EngineDatasetDownloadUrl> {
  return apiFetch<EngineDatasetDownloadUrl>(`/datasets/${datasetId}/download-url`);
}

export interface EngineModelDownloadFile {
  key: string;
  name: string;
  size_bytes: number;
  url: string;
}
export interface EngineModelDownloadUrl {
  format: "gguf" | "safetensors" | "lora";
  files: EngineModelDownloadFile[];
  expires_at: string;
  expires_in: number;
  truncated: boolean;
}

export async function engineGetModelDownloadUrl(
  modelId: string,
  format: "gguf" | "safetensors" | "lora" = "gguf",
): Promise<EngineModelDownloadUrl> {
  return apiFetch<EngineModelDownloadUrl>(`/models/${modelId}/download-url?format=${format}`);
}
```

`ModelDetail.tsx` changes:

```ts
// exportFormats (:13-17) — drop ONNX, drop fake sizes, key by the real format value
const exportFormats = [
  { format: "safetensors" as const, label: "SafeTensors", description: "Default format, best compatibility with HuggingFace" },
  { format: "gguf" as const, label: "GGUF", description: "Optimized for llama.cpp and local inference" },
];
```

```tsx
{/* :220-222 — add onClick, drop the fake `fmt.size` display */}
<Button
  variant="outline"
  size="sm"
  className="gap-2"
  onClick={async () => {
    const result = await engineGetModelDownloadUrl(model.id, fmt.format);
    // gguf is always exactly one file; safetensors/lora can be several —
    // open each in a new tab/trigger a browser download per file.
    result.files.forEach((f) => window.open(f.url, "_blank"));
  }}
>
  <Download className="h-3.5 w-3.5" /> {t("modelDetail.download")}
</Button>
```

If a real file-size display is wanted, it now has to come from
`result.files[0].size_bytes` fetched on demand (or on tab-open) — there is
no size known ahead of a mint call, since the object may not exist yet if
export hasn't run (`gguf_uri`/`export_error_message` on the model artifact
is still the export-completion signal per this repo's conventions; a
`download-url` call before export completes will 404 or 409 depending on
state — treat that as "not exported yet", not a bug).

---

## 7. Build note: `.env` is committed with a dead ngrok host

`smart-model-tune/.env` is **checked into that repo** (verified —
`git status --short` shows it clean/tracked, not gitignored) and pins:

```
VITE_ENGINE_HOST="https://prevalent-emblaze-vacation.ngrok-free.dev"
```

That tunnel host is dead. Building with this file as-is produces a `dist/`
that calls a nonexistent cross-origin host for every API/WS request. Per
`docs/04-…md`'s ADR-011 section, the production topology is same-origin —
a single `edge` nginx serves the SPA static root **and** proxies
`/api/v1`/`/ws` on the same origin — so the correct production build is:

```
VITE_ENGINE_HOST="" npm run build
```

(or override it in whatever CI/deploy environment builds this repo — the
point is the build-time env var must be blanked, not left at the
committed default, for a same-origin deploy target). An empty
`VITE_ENGINE_HOST` is already the fallback `buildWsUrl` uses for the
WebSocket (`useTrainingWebSocket.ts:60`, `||` not `??` specifically so an
empty string falls through to `window.location.origin`) — this build note
is what makes that fallback the one actually exercised in production.

**Also**: `package.json`'s `prebuild` script runs
`node scripts/check-i18n.mjs --strict` (`package.json:17`), which fails the
build (non-zero exit) on any translation key that's referenced in `src/`
but missing from the locale data, or present in one language block but not
the other. Both locales live in **one file**,
`src/i18n/translations.ts` (not two separate files) — it exports an object
with an `en:` block and a `th:` block (`scripts/check-i18n.mjs` locates
them by searching for the literal substrings `"en:"` / `"th:"`); any new
user-facing string added by this patch (there aren't any required by the
snippets above, but if the team adds copy while wiring — e.g. a "connection
rejected" banner per §4, or new labels for §6's Download button per
format) needs a key added to **both** blocks in that one file, or
`npm run build` fails on this gate.

---

## Verification checklist

Run these against a deployment where the backend has been switched to
`AUTH_REQUIRED=true` (staging, not prod, until this patch is confirmed
live) — none of this is meaningful with `AUTH_REQUIRED=false`, since
everything succeeds anonymously either way under the current default.

1. **Anonymous vs. authenticated REST call, same endpoint:**

   ```
   # anonymous — expect 401
   curl -s -o /dev/null -w "%{http_code}\n" https://<host>/api/v1/projects

   # authenticated — expect 200 (replace <token> with a real Supabase access_token,
   # e.g. from `supabase.auth.getSession()` in a browser console while logged in)
   curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer <token>" https://<host>/api/v1/projects
   ```

   Confirms §1/§2 landed: the anonymous call must 401 once the flag is on,
   and the authenticated call must succeed. If both 401, the header isn't
   being attached. If both 200, `AUTH_REQUIRED` isn't actually `true` yet
   on that deployment — check that before assuming the frontend patch is
   broken.

2. **`/health` stays 200 regardless (§2d control check):**

   ```
   curl -s -o /dev/null -w "%{http_code}\n" https://<host>/health
   ```

   Should be `200` in both the anonymous and authenticated cases above —
   this is expected, not a bug, and specifically the trap called out in
   §2d: don't let a green `/health` check stand in for "auth is working."

3. **WebSocket handshake carries the subprotocol back:** open the Network
   tab, filter to `WS`, open a training/SDG job page so
   `useTrainingWebSocket` connects, click the request, and check the
   **Response Headers** of the `101 Switching Protocols` upgrade:

   ```
   Sec-WebSocket-Protocol: bearer
   ```

   Its presence confirms the server accepted the offered `["bearer",
   "<jwt>"]` subprotocol pair (§3) and echoed `bearer` back per
   `api/routers/websocket.py`'s `ws.accept(subprotocol="bearer")`. If the
   header is absent but the socket still opens, the server accepted with
   no subprotocol negotiated — check whether a token was actually offered
   (§3's `token ? new WebSocket(url, [...]) : new WebSocket(url)` branch —
   an empty/missing session silently falls back to the no-subprotocol
   constructor call, which is intentional under `AUTH_REQUIRED=false` but
   would produce `4401`-then-`1006` under `AUTH_REQUIRED=true`).

4. **429/402/503 surface `EngineError` with the right `status`:** easiest
   forced case is 429 — call `engineChatCompletion` or trigger SDG rapidly
   enough to hit the per-actor quota (`quota.py`'s default cap), and
   confirm the caught error is `instanceof EngineError`, `e.status === 429`,
   and `e.retryAfter` is a small positive integer (not `null`) — §5.

5. **Download button produces a real, working link:** open a completed
   model's Export tab, click Download for `gguf`, confirm a new tab opens
   a URL under `storage.<host>` (not `<host>/api/v1/...`) that starts an
   actual file download, and that the file size roughly matches
   `files[0].size_bytes` from the minting response (visible in the
   Network tab on the `/models/{id}/download-url` request) — §6.

---

## Where this disagrees with `docs/04-…md`

Nowhere on substance. Differences are granularity, not correctness:

- `docs/04-…md`'s "Required Frontend Change" section (§112-277 there)
  covers §1 and §3 of this document at the same level of detail — this
  document doesn't override that section, it's the same guidance, just
  copy-pasteable rather than prose, plus it adds the four hand-rolled
  fetches (§2 here) individually, which `04-…md` only mentions in passing
  ("Do the same for the three calls that bypass `apiFetch`" — it says
  *three*, not four, because it doesn't count `/health`; this document
  explicitly covers `/health` and explains why it's the one call that
  should **not** get the header).
- **One line in `04-…md` needs a heads-up, not a fix, and it isn't this
  document's place to change it:** its "Also note" list
  (`04-…md:273-274`) says *"Another user's resource returns `404`, not
  `403`, with a message identical to a genuine not-found."* That was
  accurate when `04-…md` was written. A **concurrent, separate change in
  this same backend cycle** (tracked as the 404→403 workstream, touching
  `api/services/ownership.py`/`job_ownership.py` — outside this document's
  file scope) flips non-owner responses from `404` to `403` while a
  genuinely-missing resource stays `404`. If that lands, `04-…md:273-274`
  becomes stale and should be updated by whoever owns that workstream
  (not this document — this document's scope is `docs/patches/` only).
  Flagging it here so it isn't lost: **do not build frontend logic that
  assumes 404-means-either-case** once that change ships; `403` will then
  mean "exists, not yours" and `404` will mean "doesn't exist", and those
  are distinguishable again.
- Everything else in `04-…md` (the endpoint-mapping table, the Priority
  Fix list, the "Correct End-to-End Sequence") is out of this document's
  scope (auth + error handling + download-url only) and this document
  makes no claim about it either way.
