#!/usr/bin/env python3
"""Link a Keycloak/OIDC identity to an existing Engine actor (owner id).

Use during the Supabase -> Keycloak migration so a migrated user's new
Keycloak login sees the rows owned by their old Supabase `sub`. Run it only
after confirming **out of band** that one person holds both accounts —
never link on a matching email alone.

What `--apply` writes: one `identity_links` row `(issuer, subject) ->
actor_id`. If that Keycloak identity already logged in before being linked,
it was auto-issued a fresh empty actor; that row is re-pointed, and the
script refuses (unless `--force`) when that fresh actor already owns data,
since re-pointing would orphan it.

Talks to Postgres with plain sync psycopg2 and resolves the DSN exactly like
`scripts/backfill_project_owner.py` (ALEMBIC_DATABASE_URL, then
DATABASE_URL).

Usage:
    # Preview only (default).
    .venv/bin/python scripts/link_identity.py \\
        --issuer https://<host>/auth/realms/tunelab \\
        --subject <keycloak-sub> --actor-id <old-supabase-sub>

    # Commit. Nothing is written without this flag.
    ... --apply
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg2

from scripts.backfill_project_owner import redact_dsn, resolve_db_url

# Every column that stores an actor id. Used to show what the target actor
# owns, and to refuse orphaning data held by a to-be-replaced actor.
OWNER_COLUMNS = (
    ("projects", "owner_id"),
    ("datasets", "owner_id"),
    ("training_jobs", "owner_id"),
    ("deployments", "owner_id"),
    ("api_keys", "owner_id"),
    ("template_uses", "user_id"),
    ("template_ratings", "user_id"),
)

_MAX_ACTOR_LEN = 64  # owner_id / user_id are String(64)


def count_owned(cur: Any, actor_id: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table, column in OWNER_COLUMNS:
        # Identifiers come from the constant above, never from input.
        cur.execute(f"SELECT count(*) FROM {table} WHERE {column} = %s", (actor_id,))
        counts[table] = cur.fetchone()[0]
    return counts


def _fmt(counts: dict[str, int]) -> str:
    owned = {t: n for t, n in counts.items() if n}
    return ", ".join(f"{t}={n}" for t, n in owned.items()) or "nothing"


def run(
    conn: Any,
    *,
    issuer: str,
    subject: str,
    actor_id: str,
    apply: bool,
    force: bool = False,
    out: Any = sys.stdout,
) -> int:
    """Preview, then maybe write. Returns a process exit code."""
    with conn, conn.cursor() as cur:
        target = count_owned(cur, actor_id)
        print(f"target actor {actor_id} owns: {_fmt(target)}", file=out)

        cur.execute(
            "SELECT actor_id FROM identity_links WHERE issuer = %s AND subject = %s",
            (issuer, subject),
        )
        row = cur.fetchone()
        current = row[0] if row else None

        if current == actor_id:
            print("already linked: nothing to do", file=out)
            return 0
        if current is not None:
            displaced = count_owned(cur, current)
            print(f"identity currently -> {current}, which owns: {_fmt(displaced)}", file=out)
            if any(displaced.values()) and not force:
                print(
                    "error: re-linking would orphan that data. Move or delete it "
                    "first, or pass --force.",
                    file=out,
                )
                return 1

        if not apply:
            print("dry-run: nothing written. Re-run with --apply to commit.", file=out)
            return 0

        cur.execute(
            "INSERT INTO identity_links (issuer, subject, actor_id) VALUES (%s, %s, %s) "
            "ON CONFLICT (issuer, subject) DO UPDATE "
            "SET actor_id = EXCLUDED.actor_id, updated_at = now()",
            (issuer, subject, actor_id),
        )
        print(f"applied: ({issuer}, {subject}) -> {actor_id}", file=out)
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--issuer", required=True, help="exact OIDC issuer (== OIDC_ISSUER)")
    parser.add_argument("--subject", required=True, help="the Keycloak user's `sub`")
    parser.add_argument("--actor-id", required=True, help="existing owner id to link to")
    parser.add_argument("--apply", action="store_true", help="write the link")
    parser.add_argument("--force", action="store_true", help="allow orphaning a displaced actor's data")
    return parser.parse_args(argv)


def validate(args: argparse.Namespace) -> None:
    for name in ("issuer", "subject", "actor_id"):
        if not getattr(args, name).strip():
            raise ValueError(f"--{name.replace('_', '-')} must not be empty")
    if len(args.actor_id) > _MAX_ACTOR_LEN:
        raise ValueError(f"--actor-id longer than {_MAX_ACTOR_LEN} characters")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        validate(args)
        dsn = resolve_db_url()
    except (ValueError, RuntimeError) as exc:
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
            issuer=args.issuer,
            subject=args.subject,
            actor_id=args.actor_id,
            apply=args.apply,
            force=args.force,
        )
    except psycopg2.Error as exc:
        print(f"error: database operation failed: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
