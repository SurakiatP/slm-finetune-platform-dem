# Template marketplace implementation ledger

Approved design: `2026-09-20-template-marketplace-design.md`. User authorized
implementation and later requested an independent **gpt-5.6-sol / high**
security-audit and current CVE review after delegate-build completes.

## Constraints and evidence

Work in the existing local `feat/frontend-contract-sync` checkout. Do not edit
the separate frontend, read `.env`, deploy, rent GPU capacity, or call paid SDG.
Preserve existing dataset preparation changes. No agent commits. Parent owns
integration and any commits/hub updates. Reuse existing helpers and add no
runtime dependency for marketplace. Test changed behavior before implementation.

Baseline: 2,078 passed, 4 failed, 4 skipped. Pre-existing failures are nginx's
standalone unresolved `api` upstream and three `test_snapshot_node_3b4.py` SDG
snapshots. These must not be represented as a green baseline.

## Ordered waves and ownership

1. **Foundation** — agent `marketplace_foundation`: models `template.py`,
   project/training additions, model exports, migration `0014_template_marketplace`
   after `0013_dataset_reuse`, shared ownership/job ownership/quota and their tests.
   Acceptance: durable ownership survives project deletion; null unknown owners
   remain closed; unique usage/rating and rating-range DB constraints exist.
   Parent concurrently creates isolated loopback-only Postgres/MinIO harness.
2. **Three disjoint parallel tasks**, only after foundation completes:
   - Catalog/materialization: template catalog/schema/service/router; project
     schema/service/router; main router wiring; import/cleanup scripts and tests.
     Acceptance: all eight definitions, two registered-ready only; real statistics,
     eligible rating, durable concurrent idempotency and independent split copies.
   - Training: training schema/service, manual/HPO workers, formatters/trainer,
     Optuna objective and tests. Acceptance: immutable effective context; heldout
     role guards; external validation; custom prompt; no token truncation;
     legacy defaults remain compatible. Training owner/name scope fixed here.
   - Serving/evaluation: evaluation service/worker, inference service/schema,
     export worker, automatic pipeline dispatch, Ollama helper as needed,
     exact NER metric and tests. Acceptance: saved prompt on UUID/tag chat/text,
     explicit overrides, fixed test evaluation, span/type F1, retained namespace.
3. **Parent integration**: dedicated real PG/MinIO tests, package JSON inclusion,
   OpenAPI export, frontend examples, import/cleanup and GPU runbook. Check
   concurrency, conflicting payload, failed copies/cleanup, replay after deletion,
   source preservation, ratings and retained owner access. Run focused and full
   unit suites and report separately from known baseline failures.
4. **Independent delegate-build reviewer**: run tests and compare aggregate
   diff to approved design; explicit PASS/FAIL. On FAIL, report gaps and ask only
   about failing topics before a new build round (no silent retry).
5. **Security audit** using gpt-5.6-sol / high and the user's security-audit
   skill: review code bugs/trust boundaries and dependency CVEs. Audit is read-only
   against code; no live probing. Honor its sandbox requirement for audit execution;
   if unavailable report needs-validation, never claim a dynamic check ran.
   Check current advisory sources and distinguish declared ranges, actual local
   installed versions and unverified deployment images. Findings do not authorize
   unrelated fixes or dependency upgrades.

## Frozen interfaces

`TemplateDatasetVersion`: composite PK `(template_id, version)`, definition_sha256,
manifest_json, splits_json. Split entries: storage_uri/sha256/num_samples/size_bytes.
`TemplateUse`: UUID id, user_id, template_id, template_version, idempotency_key,
request_sha256, plain UUID project_id (no FK), response_json; unique user/key.
`TemplateRating`: composite PK template/user, integer rating CHECK 1..5.
Nullable `Project.template_snapshot`; nullable `TrainingJob.owner_id` and
`context_snapshot`. Backfill owner from surviving projects only.

Snapshot keys: template_id, template_version, definition (project only), base_model,
system_prompt, chat_template, manual_config, train_sample_count, sampling_seed,
train_dataset_id, validation_dataset_id, test_dataset_id and the three `<role>_sha256`.
Do not place context in strict `TrainingJob.config_json`. Artifact uses training
context instead of a redundant column. Dataset generation_metadata has template_id,
template_version, role, sha256, sampling_seed; source is existing UPLOADED enum;
do not link split datasets via the cascading parent_dataset_id.

Catalog field casing follows the actual handoff, not assumptions from frontend
internal types. Optional template_id/template_version/template_overrides on project
creation; `Idempotency-Key` mandatory for template use only. Rating uses PUT and
strict integer 1..5. Catalog/template mutations require a real CurrentUser even
when global AUTH_REQUIRED is false. Ordinary project create remains unchanged.

## Progress

- Planner completed and interfaces frozen; foundation complete (253 focused tests).
- Real Postgres migration/backfill/retention/downgrade/reupgrade test: PASS.
- Wave 2 catalog, training and serving complete; no code commits yet.
- Aggregate unit suite after hash repair: 2,173 passed, the same 4 baseline
  failures, 4 skipped; 152 snapshots passed. No new full-suite failure.
- Real PG/MinIO and offline full-chat integration suite: 11 passed. Covers
  migration/backfill, real imports, concurrent durable create/replay, rating,
  private split copies, copy-failure cleanup, retained ownership and training
  submission snapshots. The 12,740 ready rows fit their actual catalog prompts
  within 2,048 cached-tokenizer tokens; actual GPU runtime remains unverified.
- Both ready sources registered only in the disposable local test stack, not
  production.
- Independent delegate-build review initially found missing `test_sha256`
  verification before evaluation inference. User authorized the scoped repair:
  `get_jsonl` now verifies optional raw-byte SHA256, and template evaluation
  requires/passes its frozen test SHA256 before prediction. Regression: 3 passed;
  focused suite: 95 passed. Fresh independent read-only review: PASS, no P0–P3
  findings. Existing non-template `get_jsonl` callers retain their three-argument
  behavior.
- Requested gpt-5.6-sol/high security audit: completed scoped source pass,
  0 confirmed vulnerabilities, 5 needs_validation, 4 rejected candidates;
  16 coverage units, distinct final critic clean, both JSON validators passed.
  Report: `/Users/parksurakiat/security-audit-skill/slm-finetune-platform-dem/run-1/REPORT.md`.
  OS-enforced execution sandbox unavailable: no security exploit reproduction.
  OSV inventory queried 201 public local distributions; 9 packages matched
  advisories. Local versions/Dockerfile declarations are not deployed SBOMs or
  exploitability proof. No dependency upgrades, runtime repairs or deployment.
- GPU validation pending new vast.ai SSH; production deployment remains gated.
- Frontend wiring belongs to frontend team and is not backend completion evidence.
