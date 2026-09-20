# Template marketplace handoff

Status: after removing unsupported NER scope, focused tests pass 110/110,
real Postgres/MinIO integration passes 10/10, and the full unit suite reports
2,160 passed with the same four pre-existing failures and four skips. Template
evaluation requires and verifies frozen `test_sha256` against raw MinIO bytes
before parsing or inference.
The requested gpt-5.6-sol/high security audit completed its scoped source pass:
0 confirmed vulnerabilities, 5 needs_validation, 4 rejected candidates. This is
not deployed-runtime clearance; actual image/SBOM and bounded validation remain.
See the implementation ledger and
`/Users/parksurakiat/security-audit-skill/slm-finetune-platform-dem/run-1/REPORT.md`.
Vast.ai validation at commit `56f9fd5` completed the Thai Sentiment flow:
train, GGUF export, Ollama registration, evaluation and inference. Macro-F1
improved from 0.4152 to 0.6458 and accuracy from 0.5022 to 0.6589 on the frozen
900-row test split. Production deployment is not implied by this document.

## Frontend contract

Send the existing Supabase bearer token. All template operations require a real
user even in environments where legacy Engine endpoints allow anonymous calls.
The backend does not accept a client-supplied owner ID.

`GET /api/v1/templates?include_unavailable=true&limit=50&offset=0` returns
`{items,total,limit,offset}`. Without `include_unavailable`, only registered ready
templates appear. The seven curated definitions are not seven trained models.
Only `tpl-006` (Thai sentiment) is ready for dataset import; the other six remain
unavailable with an explanation. The product has no NER template or NER task type.

The minimum handoff fields use **snake_case**: `long_description`, `task_type`,
`base_model`, `learning_rate`, `dataset_size`. Map these to the frontend's internal
camelCase model explicitly. Extra fields include version, available/unavailable_reason,
split counts, true dataset attribution, rating_count and my_rating.

No ratings means `rating: null`, `rating_count: 0`; display “ยังไม่มีคะแนน”.
Forks starts at zero and counts complete successful project creation, not button
clicks. Remove “มีผู้ใช้แล้ว 1,248 โปรเจกต์”. Refetch catalog/statistics after
mutations; this release does not add a cross-user statistics WebSocket.

Create a sentiment project:

```http
POST /api/v1/projects
Authorization: Bearer <current-user-token>
Idempotency-Key: <one-new-UUID-for-this-logical-create>
Content-Type: application/json

{
  "name": "Thai sentiment experiment",
  "task_type": "classification",
  "template_id": "tpl-006",
  "template_version": "1",
  "template_overrides": {
    "base_model": "unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit",
    "epochs": 2,
    "learning_rate": 0.0002,
    "train_sample_count": 6000,
    "sampling_seed": 42
  }
}
```

Keep the same key and identical payload for a network retry. Reusing that key for
a different payload returns a conflict. A deliberate second project gets a new key.
The saved response is replayable even after the original project is deleted.
Ordinary projects do not require template fields or this durable key.

Use returned `template_snapshot` and split dataset IDs as server-side truth.
Train only the train split; validation is reserved for checkpoint selection and
test for final evaluation. Creating a project does not start SDG, training or HPO.
Reducing training size keeps validation/test unchanged. Existing project snapshots
do not follow future catalog edits.

Rate only after successful creation:

```http
PUT /api/v1/templates/tpl-006/rating
Authorization: Bearer <current-user-token>
Content-Type: application/json

{"rating": 5}
```

One integer score from 1 to 5 per user; another PUT replaces it. No anonymous,
fractional, string or boolean scores. Deleting the project retains eligibility,
rating and the historical successful-use count.

## Verification and release gates

Local isolated services are defined in `docker/compose.template-tests.yml`:
Postgres is loopback port 15432, MinIO loopback port 19000. Credentials in that
file are public test fixtures, not deployment credentials. Never point integration
tests at another project's database or import test ratings/forks into production.

From the backend checkout, start only the dedicated stack:

```sh
docker compose --env-file /dev/null -p slm-template-tests -f docker/compose.template-tests.yml up -d
ALEMBIC_DATABASE_URL=postgresql+psycopg2://template_test:template_test_only@127.0.0.1:15432/template_tests .venv/bin/python -m alembic upgrade head
TEMPLATE_TEST_DATABASE_URL=postgresql+asyncpg://template_test:template_test_only@127.0.0.1:15432/template_tests .venv/bin/python -m pytest tests/integration/test_template_migration.py tests/integration/test_template_marketplace.py -q
```

The marketplace fixture imports real prepared splits through the importer, using
explicit isolated clients rather than the developer's `.env`. Its resources remain
in the dedicated stack for inspection. Stop services without deleting their volumes:

```sh
docker compose --env-file /dev/null -p slm-template-tests -f docker/compose.template-tests.yml stop
```

Operator import entrypoint (set real DB/storage settings securely in the target
environment; do not paste secrets into commands, documentation or chat):

```sh
.venv/bin/python -m scripts.import_template_catalog --prepared-root data/template-catalog/prepared
```

Repeat import is safe only for matching immutable content/version. Changed source
or definition requires a new curated version, not overwriting an existing registration.

After independent local review and security/CVE review, run one manual two-epoch
Thai Sentiment job on Vast.ai, with no paid SDG/HPO or automatic quality-tuning retries. Record exact
code/data/model revisions, prompt, seed, token limit and actual runtime versions.
Evaluate baseline and final served artifact on the same fixed test inputs and
decoding settings and sentiment Macro-F1. Baseline and exported quantization may
differ: record that limitation instead of attributing every score change to training.

The workflow and its primary score must pass before wetty deployment to
slmpc. A regression stops the release and requires a user decision. Check slmpc's
actual GPU/runtime separately; a vast.ai result does not prove that hardware works.
Frontend team owns UI changes/deployment; backend tests do not prove UI wiring.

## Prompt and exported models

The training's saved effective prompt is the default for evaluation and inference
through this platform, not the latest editable catalog prompt. An explicit caller
prompt can override inference. External runtimes must load the exported prompt/chat
metadata themselves; weights alone do not enforce application-level instructions.

Read `context_snapshot` from `GET /api/v1/trainings/{training_id}` for the exact
effective prompt and chat-template identifier. A model response already contains
its `training_job_id`, so no new metadata endpoint is necessary. Owner access to
this metadata survives project deletion. The worker also stores
`serving_context.json` beside exported artifacts; a direct GGUF download alone
does not bundle that sidecar. Hugging Face chat-template identifiers are metadata,
not Ollama Go-template text—do not pass one as the other.
