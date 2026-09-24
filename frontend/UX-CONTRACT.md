# UX Contract

## Product context

- Audience: technical users preparing, training, evaluating, and exporting compact language models.
- Primary jobs: create a project, prepare a dataset, monitor a job, inspect results, and download an artifact.
- Active locales: English and Thai via `src/i18n/LanguageContext.tsx`.
- Accessibility target: WCAG 2.2 AA.

## Business-context sources

| Domain / scope | Authoritative source | Source type | Reviewed date |
|---|---|---|---|
| API and lifecycle | `../slm-finetune-platform-dem/openapi.json` | OpenAPI contract | 2026-09-24 |
| Auth and ownership | `../slm-finetune-platform-dem/api/core/auth.py`, `api/routers/websocket.py` | API policy | 2026-09-24 |
| Project/delete job behavior | `../slm-finetune-platform-dem/api/routers/projects.py`, `docs/04-frontend-integration-smart-model-tune.md` | API / integration guide | 2026-09-24 |
| Template availability and ratings | `../slm-finetune-platform-dem/api/schemas/templates.py`, `api/routers/templates.py` | API contract | 2026-09-24 |
| Spend and quotas | `../slm-finetune-platform-dem/api/services/quota.py`, `docs/patches/smart-model-tune-unused-endpoints.md` | API policy | 2026-09-24 |

## Visual contract

- Project design context: `DESIGN.md`.
- Token ownership: existing runtime CSS variables are canonical.
- Runtime adapters: `src/index.css` → `tailwind.config.ts` → shared `src/components/ui/*` primitives.
- Themes: light and dark through the existing `ThemeProvider`.

## Canonical UI Map

| Capability | Canonical owner | Source of truth | Allowed variants | Verification |
|---|---|---|---|---|
| Select/Listbox | `src/components/ui/select.tsx` (Radix) | shared primitive | authored | keyboard + popup |
| Form | React Hook Form and shared Input/Label where forms are used | existing workflow | create / wizard | component tests |
| Scrollbar | `src/index.css` | global CSS | geometry only | computed style |
| Toast | shared `useToast` / Toaster | existing primitive | success / error | component tests |
| CRUD | endpoint modules + TanStack query hooks | OpenAPI | return / stay | workflow tests |

## Flow ledger

| Operation | Trigger | Pending | Success | Failure recovery | Source ref |
|---|---|---|---|---|---|
| Create project | Wizard launch | Stable busy button | project detail + toast | inline error, preserve form | OpenAPI `/trainings` |
| Start long job | Generate/train/evaluate/export | server-accepted job + progress | persistent status and return path | inline error/retry | OpenAPI + WS contract |
| Cancel job | ConfirmDialog | dialog busy | invalidate job data | keep dialog/error path | OpenAPI cancel endpoints |
| Hard-delete project/dataset | ConfirmDialog | dialog busy | owning list + toast | dialog/error path | OpenAPI delete endpoints |
| Template search | Marketplace search | retain prior cards with “Updating” | same route | inline retry | OpenAPI `/templates` |
| Use template | Available marketplace template | navigation action | project wizard with backend values | unavailable templates cannot start | TemplateResponse `available` |

## Async and resilience

- Mutations are pessimistic; create endpoints accept idempotency keys where supported.
- Job progress first reads the REST snapshot, then opens same-origin WebSocket with a fresh Supabase JWT as `['bearer', token]`.
- Browsers expose rejected pre-accept WebSocket handshakes as a generic failure, so the client retries boundedly rather than claiming it can distinguish 4401 from 4403.
- Server-filtered marketplace search is 300ms debounced, IME-safe, retains prior results during refresh, and has a clear action.
- A 401 is handled through the existing protected/auth flow. 429 retains `Retry-After` for an actionable cooldown; 402 and 503 remain server errors until product copy is localized.

## Verification

- Static: endpoint wrapper tests, API client tests, job-progress hook tests, lint, typecheck/build, i18n check, premium project audit.
- Browser: inspect loading, unavailable template, no results, error/retry, keyboard search/clear, authored select popup, narrow viewport, dark theme, and a real Engine-authenticated job connection.
