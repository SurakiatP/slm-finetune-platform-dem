#!/usr/bin/env python3
"""Export the backend's OpenAPI 3.1 spec to ``openapi.json`` at the repo root.

FastAPI already generates the spec at runtime (served live at ``/openapi.json``,
with Swagger UI at ``/docs`` and ReDoc at ``/redoc``). This script writes that
same spec to a committed, published file so the frontend team can consume a
stable snapshot — codegen a typed client, import into Postman/Insomnia, or diff
it in review — without needing the API running. (It lives at the repo root, not
under ``docs/``, because ``docs/`` is gitignored as private/local-only.)

Run it from the repo root inside the backend environment (or the ``api``
container). ``app.openapi()`` only introspects routes and Pydantic models — it
opens no DB/Redis/network connections — so a throwaway ``DATABASE_URL`` is
enough to satisfy settings construction:

    DATABASE_URL=postgresql+asyncpg://x:x@localhost/x python scripts/export_openapi.py

Regenerate this whenever the API contract changes (new route, changed schema)
rather than hand-editing the JSON — a hand-edited spec drifts from the code,
which is exactly what this file exists to prevent.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# Settings requires a database_url; app.openapi() never connects, so any value
# works. Only set a placeholder if the caller hasn't provided a real one.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://export:export@localhost:5432/export")

from api.main import app  # noqa: E402  (import after the env default above)

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = REPO_ROOT / "openapi.json"


def main() -> None:
    spec = app.openapi()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    paths = spec.get("paths", {})
    operations = sum(len(methods) for methods in paths.values())
    print(
        f"wrote {OUTPUT.relative_to(REPO_ROOT)} — "
        f"OpenAPI {spec.get('openapi')}, "
        f"{spec['info']['title']} v{spec['info']['version']}, "
        f"{len(paths)} paths / {operations} operations"
    )


if __name__ == "__main__":
    main()
