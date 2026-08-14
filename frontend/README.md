# SLM Fine-Tuning Platform — Web UI

Dark, data-dense dashboard for the FastAPI backend in this repo. Drives the full
workflow: project → dataset (seed upload / SDG) → training (manual / HPO) →
GGUF export → chat playground → evaluation & comparison.

## Stack

React 19 + Vite + TypeScript + Tailwind CSS 3 · TanStack Query v5 ·
react-router v7 · Recharts · lucide-react · Fira Sans / Fira Code.

Auth is **optional and compile-time**: leave `VITE_SUPABASE_URL` / `VITE_SUPABASE_PUBLISHABLE_KEY` unset (the default) for an auth-less build against a dev backend (`AUTH_REQUIRED=false`); set both to gate the app behind Supabase email/password login and send the session JWT on every /api call (`Authorization: Bearer`) and on /ws (`["bearer", <jwt>]` subprotocol) — required against a backend running `AUTH_REQUIRED=true`. Desktop-first, responsive, WCAG AA on dark.

## Run

```bash
# backend must be up first (docker compose / uvicorn on :8000)
cd frontend
npm install
npm run dev          # http://localhost:5173
```

The Vite dev server proxies `/api` → `http://localhost:8000` and `/ws` →
`ws://localhost:8000` (see `vite.config.ts`), so no CORS setup is needed.
For a non-proxy deployment set `VITE_API_BASE` (see `.env.example`).

```bash
npm run build        # type-check (tsc -b) + production bundle in dist/
npm run lint
```

## Structure

```
src/
├── api/            # fetch client, TS mirrors of api/schemas/*.py, WS helpers, endpoints/
├── hooks/          # TanStack Query hooks, useJobProgress (WS + polling), eval registry
├── components/     # ui/ primitives · data/ tables+badges · jobs/ progress panels · charts/
├── features/       # route pages: dashboard, projects, datasets, trainings, models,
│                   # playground, evaluations
└── lib/            # cn, formatters, JSONL parsing, metrics helpers
```

## Async jobs (202 + WebSocket)

SDG / training / export submits return `202 {job_id, …}`. `useJobProgress`
subscribes to `/ws/jobs/{job_id}`, accumulates loss curves / trial tables, and
falls back to REST polling (REST status is authoritative — the socket has no
replay). On terminal frames it invalidates the affected query caches.

## Known gaps

- The backend has no `GET /evaluations` list endpoint; evaluation history is
  remembered per-browser in localStorage (`useEvalRegistry`). Add a real list
  endpoint and replace the registry when possible.
- Inference is non-streaming by backend contract (`stream=true` → 400).
