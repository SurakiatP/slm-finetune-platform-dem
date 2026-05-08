# Prompt Template: Add a New API Endpoint

> Copy-paste, fill the `<...>` placeholders, send to Claude.

---

```
Add a new endpoint <METHOD> <PATH> to router `api/routers/<ROUTER>.py`.

Context:
- Read `CLAUDE.md`, `docs/standards/API_CONVENTIONS.md`, `docs/standards/CODING_STYLE.md` first.
- This endpoint <CREATES | READS | TRIGGERS | UPDATES | DELETES> a <RESOURCE>.

Schema:
- Request body: define `<RequestModel>` in `api/schemas/<area>.py` with fields:
  - <field1: type — description>
  - <field2: type — description>
- Response body: `<ResponseModel>` with fields:
  - <field1: type — description>

Behavior:
- <step 1: e.g. "Validate that the dataset exists, 404 if not">
- <step 2: e.g. "Create a job row in DB with status=PENDING">
- <step 3: e.g. "Enqueue Celery task `workers.tasks.<x>.run`">
- <step 4: e.g. "Return 202 Accepted with job_id=task.id">

Status code: <201 | 202 | 200>

Tests:
- In `tests/api/routers/test_<router>.py`, cover:
  - Happy path (correct shape, correct status code)
  - 404 when <prerequisite> is missing
  - 422 for invalid input (one Pydantic validation case)
- Follow the pytest patterns in `docs/standards/TESTING_GUIDE.md`.

Definition of Done:
- Endpoint visible in Swagger UI with description, summary, examples
- Tests pass
- Update `WORKING_LOG.md` and `TASK_TRACKER.md`
```

---

## Tips for filling this in

- **METHOD/PATH** — follow `docs/standards/API_CONVENTIONS.md` (plural nouns, kebab-case actions).
- **Schema location** — group related schemas in one `api/schemas/<area>.py` file (e.g., `training.py` holds all training-related schemas).
- **Async vs sync** — if the endpoint triggers long work, use the **async-job pattern** (status 202, return `job_id`). If it's CRUD on metadata, return 200/201 directly.
- **Error codes** — see the table in `API_CONVENTIONS.md`. Don't invent new ones.
