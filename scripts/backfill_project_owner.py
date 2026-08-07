#!/usr/bin/env python3
"""Backfill `projects.owner_id` for rows that predate authentication.

Why this script exists: `api/services/ownership.py`'s module docstring
establishes that ownership lives *only* on `Project.owner_id` — every other
entity (`Dataset`, `TrainingJob`, `ModelArtifact`, `EvaluationRun`) reaches
it by FK join through `Project`. That means remediating a legacy demo
deployment is exactly one `UPDATE projects SET owner_id = ... WHERE
owner_id IS NULL` per row, not a migration and not a multi-table sweep.

It matters because `api/core/config.py`'s `_reject_unsafe_production_config`
(Wave 0 of this hardening pass) makes `ENVIRONMENT=production` +
`AUTH_REQUIRED=false` a fatal boot error, and the moment a real deployment
sets `AUTH_REQUIRED=true`, every row with `owner_id IS NULL` becomes
invisible to *everyone* — `_check_owner` in `api/services/ownership.py`
treats a null owner exactly like "owned by someone else", by design
(fail-closed, not fail-open). Any project created while auth was off has to
be assigned to a real owner (or deleted) before that flag flips, or it
silently vanishes for every caller.

Talks to Postgres with plain **sync psycopg2** — deliberately not the app's
async SQLAlchemy engine, and not even a SQLAlchemy engine at all, because
this is a one-shot operational script that should have as few moving parts
(and as little of the app's import graph) as possible. DSN resolution
mirrors `alembic/env.py`'s `_resolve_db_url()`: prefer
`ALEMBIC_DATABASE_URL`, fall back to `DATABASE_URL`, same as every other
sync entrypoint in this repo.

Usage:
    # Preview only (default) — always do this first.
    .venv/bin/python scripts/backfill_project_owner.py --owner-id <uuid>

    # Same, restricted to specific rows.
    .venv/bin/python scripts/backfill_project_owner.py --owner-id <uuid> \\
        --project-id <uuid> --project-id <uuid>

    # Commit the UPDATE. Nothing is written without this flag.
    .venv/bin/python scripts/backfill_project_owner.py --owner-id <uuid> --apply

Exits non-zero on any failure (bad UUID, unset DSN, connection failure) so
it is safe to use as a gate in a deploy script.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from typing import Any
from uuid import UUID

import psycopg2

# Number of preview rows printed on a dry run (and, for symmetry, on an
# --apply run before the UPDATE fires) so an operator eyeballing the output
# doesn't get a wall of text on a box with hundreds of legacy rows.
DEFAULT_PREVIEW_LIMIT = 20


# ---- DSN resolution ---------------------------------------------------------


def resolve_db_url() -> str:
    """Resolve the Postgres DSN this script connects with.

    Mirrors `alembic/env.py`'s `_resolve_db_url()`: prefer
    `ALEMBIC_DATABASE_URL`, fall back to `DATABASE_URL`, and strip any
    `+<driver>` qualifier (`+asyncpg`, `+psycopg2`) off the `postgresql`
    scheme.

    This deliberately does **not** match alembic's output byte-for-byte —
    alembic hands its URL to a SQLAlchemy `engine_from_config`, which
    understands `postgresql+psycopg2://` as "use the psycopg2 DBAPI"; this
    script calls `psycopg2.connect()` directly, with no SQLAlchemy engine
    in between, and psycopg2's own DSN parser does not recognize a `+driver`
    suffix on the scheme — only the bare `postgresql://` (or `postgres://`)
    form. So the conversion here goes one step further than alembic's and
    drops the driver qualifier entirely rather than rewriting it.
    """
    raw = os.getenv("ALEMBIC_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not raw:
        raise RuntimeError(
            "Set ALEMBIC_DATABASE_URL (or DATABASE_URL) before running this script."
        )
    if "+asyncpg" in raw:
        return raw.replace("+asyncpg", "", 1)
    if "+psycopg2" in raw:
        return raw.replace("+psycopg2", "", 1)
    return raw


def redact_dsn(url: str) -> str:
    """Hide credentials in a DSN before it is ever printed.

    Same pattern as `api/main.py`'s `_redact()` (see `:91-100` there) —
    duplicated rather than imported, because importing `api.main` pulls in
    the whole FastAPI app (routers, middleware, lifespan) for a one-line
    string transform, and this script otherwise has zero dependency on the
    `api` package by design (sync psycopg2 only, see module docstring).
    """
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" in rest:
        creds, host = rest.split("@", 1)
        creds = creds.split(":", 1)[0] + ":***"
        return f"{scheme}://{creds}@{host}"
    return url


# ---- validation --------------------------------------------------------


def validate_owner_id(raw: str) -> str:
    """Parse `raw` as a UUID and return it unchanged (as a string) if valid.

    Deliberately checked *before* any database connection is opened. A
    typo'd owner id that silently "succeeds" — e.g. writing a well-formed
    but wrong UUID because a digit was transposed — is worse than an
    outright error, because the rows would then look owned (no longer
    caught by a `WHERE owner_id IS NULL` re-run) while actually belonging
    to nobody's real account.
    """
    try:
        UUID(raw)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"--owner-id {raw!r} is not a valid UUID") from exc
    return raw


def validate_project_ids(raw_ids: Sequence[str] | None) -> list[UUID] | None:
    """Parse each `--project-id` value as a UUID. `None` means "no filter"."""
    if not raw_ids:
        return None
    parsed: list[UUID] = []
    for raw in raw_ids:
        try:
            parsed.append(UUID(raw))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError(f"--project-id {raw!r} is not a valid UUID") from exc
    return parsed


# ---- SQL building --------------------------------------------------------
#
# Kept as pure functions (no cursor, no connection) so the query shape —
# in particular, that `WHERE owner_id IS NULL` is always present, which is
# what makes a re-run a no-op instead of a second write — can be unit
# tested without a live database.


def build_select_sql(project_ids: Sequence[UUID] | None) -> tuple[str, tuple[Any, ...]]:
    """Build the preview `SELECT` — same `WHERE` as the `UPDATE` below, so
    the dry-run preview always shows exactly the rows the apply run would
    touch.
    """
    sql = "SELECT id, name, created_at FROM projects WHERE owner_id IS NULL"
    params: tuple[Any, ...] = ()
    if project_ids:
        placeholders = ", ".join(["%s"] * len(project_ids))
        sql += f" AND id IN ({placeholders})"
        params = tuple(str(pid) for pid in project_ids)
    sql += " ORDER BY created_at"
    return sql, params


def build_update_sql(
    owner_id: str, project_ids: Sequence[UUID] | None
) -> tuple[str, tuple[Any, ...]]:
    """Build the backfill `UPDATE`.

    `WHERE owner_id IS NULL` is not just a safety filter — it is what makes
    this script idempotent by construction. Re-running it after a
    successful apply touches zero rows (every row it already wrote no
    longer matches the `WHERE`), so operators can re-run it after adding
    new `--project-id`s or after a partial failure without double-checking
    prior state first.
    """
    sql = "UPDATE projects SET owner_id = %s WHERE owner_id IS NULL"
    params: tuple[Any, ...] = (owner_id,)
    if project_ids:
        placeholders = ", ".join(["%s"] * len(project_ids))
        sql += f" AND id IN ({placeholders})"
        params += tuple(str(pid) for pid in project_ids)
    return sql, params


# ---- CLI --------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="backfill_project_owner.py",
        description=(
            "Assign an owner to legacy projects.owner_id IS NULL rows before "
            "AUTH_REQUIRED is flipped on. Only projects.owner_id is written — "
            "datasets/trainings/models/evaluations reach ownership by FK join "
            "through Project (see api/services/ownership.py's module docstring)."
        ),
    )
    parser.add_argument(
        "--owner-id",
        required=True,
        help="Supabase auth 'sub' UUID to assign as the owner of matching rows.",
    )
    parser.add_argument(
        "--project-id",
        dest="project_ids",
        action="append",
        default=None,
        metavar="UUID",
        help=(
            "Restrict to this project id; repeatable. Omit to target every "
            "row with owner_id IS NULL."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview matching rows without writing anything. This is the default.",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Actually issue the UPDATE. Without this flag, nothing is written.",
    )
    parser.add_argument(
        "--preview-limit",
        type=int,
        default=DEFAULT_PREVIEW_LIMIT,
        help=f"Max rows to print in the preview (default {DEFAULT_PREVIEW_LIMIT}).",
    )
    return parser.parse_args(argv)


# ---- execution ----------------------------------------------------------


def run(
    conn: Any,
    *,
    owner_id: str,
    project_ids: Sequence[UUID] | None,
    apply: bool,
    preview_limit: int = DEFAULT_PREVIEW_LIMIT,
    out: Any = sys.stdout,
) -> int:
    """Run the preview-then-maybe-write flow against an open DB-API
    connection. Split out from `main()` so tests can pass a fake connection
    (no live Postgres needed) and assert on exactly what SQL was executed.

    Returns a process exit code: 0 on success (including a clean dry run
    or a no-op "nothing matched"), non-zero only via exceptions the caller
    (`main()`) translates.
    """
    with conn:
        with conn.cursor() as cur:
            select_sql, select_params = build_select_sql(project_ids)
            cur.execute(select_sql, select_params)
            rows = cur.fetchall()

            scope = " matching --project-id" if project_ids else ""
            print(f"{len(rows)} project(s) with owner_id IS NULL{scope}:", file=out)
            for row in rows[:preview_limit]:
                project_id, name, created_at = row[0], row[1], row[2]
                print(f"  {project_id}  {created_at}  {name!r}", file=out)
            if len(rows) > preview_limit:
                print(f"  ... and {len(rows) - preview_limit} more", file=out)

            if not rows:
                print("nothing to do", file=out)
                return 0

            # THE GATE. Nothing below this line runs unless the operator
            # explicitly passed --apply. This is the one line the mutation
            # test in tests/unit/test_backfill_project_owner.py removes to
            # confirm the test suite actually catches its absence — a
            # backfill that writes on a preview request is the exact
            # failure mode this script exists to prevent.
            if not apply:
                print("dry-run: no rows written. Re-run with --apply to commit.", file=out)
                return 0

            update_sql, update_params = build_update_sql(owner_id, project_ids)
            cur.execute(update_sql, update_params)
            print(f"applied: {cur.rowcount} row(s) set to owner_id={owner_id}", file=out)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        owner_id = validate_owner_id(args.owner_id)
        project_ids = validate_project_ids(args.project_ids)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        dsn = resolve_db_url()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"connecting to {redact_dsn(dsn)}")

    try:
        conn = psycopg2.connect(dsn)
    except psycopg2.Error as exc:
        print(f"error: could not connect to database: {exc}", file=sys.stderr)
        return 1

    try:
        return run(
            conn,
            owner_id=owner_id,
            project_ids=project_ids,
            apply=args.apply,
            preview_limit=args.preview_limit,
        )
    except psycopg2.Error as exc:
        print(f"error: database operation failed: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
