"""Guards for the idempotent MLflow role provisioning (deploy Phase 5.5).

Why this exists: `docker/postgres-init.sql` only runs against an EMPTY
Postgres volume, so round 3's least-privilege `mlflow` role never existed on
any box deployed before it — compose fell back to `mlflow:mlflow@postgres`
and `slm-mlflow` crash-looped (pasaflow box, 2026-08-08, restarts=7). The fix
has two halves that MUST stay consistent:

  * `scripts/deploy_pasaflow_vm.sh` Phase 5.5 provisions the role/database
    idempotently (create-or-alter) on EVERY deploy, and MLFLOW_DB_PASSWORD
    generation is no longer gated on volume state; and
  * `docker/postgres-init.sql` creates the same state on first init — with
    the role as database OWNER, because on PG15+ the `public` schema belongs
    to `pg_database_owner` and GRANT ALL ON DATABASE alone cannot CREATE
    TABLE, which MLflow's own migrations do on first boot.

These assertions are enumerated over parsed structure, not substring-matched
against the whole file — see test_compose_port_exposure.py's history for why
a whole-file substring guard goes green the moment its author edits any one
call site.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "deploy_pasaflow_vm.sh"
_INIT_SQL = _REPO / "docker" / "postgres-init.sql"


def _script_lines() -> list[str]:
    return _SCRIPT.read_text(encoding="utf-8").splitlines()


def _block_range(lines: list[str], start: int) -> tuple[int, int]:
    """Return (start, end) line indexes of the bash if-block opening at
    `start`, by tracking if/fi depth. `elif`/`else` do not change depth."""
    depth = 0
    for i in range(start, len(lines)):
        stripped = lines[i].strip()
        if stripped == "if" or stripped.startswith("if "):
            depth += 1
        elif stripped == "fi" or stripped.startswith("fi "):
            depth -= 1
            if depth == 0:
                return (start, i)
    raise AssertionError(
        f"unterminated if-block starting at line {start + 1} of {_SCRIPT.name} "
        "— either the script is broken or this parser is; do not delete the "
        "assertion, an enumeration over nothing passes vacuously"
    )


def _find_line(lines: list[str], needle: str) -> int:
    hits = [i for i, line in enumerate(lines) if needle in line]
    assert hits, f"`{needle}` not found in {_SCRIPT.name} — non-vacuity backstop"
    assert len(hits) == 1, (
        f"`{needle}` appears {len(hits)} times in {_SCRIPT.name}; these guards "
        "assume one occurrence — update them deliberately, not by deletion"
    )
    return hits[0]


def test_mlflow_password_generation_is_not_gated_on_volume_state() -> None:
    """The whole point of Phase 5.5: MLFLOW_DB_PASSWORD can be generated on a
    box whose Postgres volume already has data, because the role is ALTERed
    to match afterwards. If generation crawls back inside the ENV_IS_NEW /
    POSTGRES_DATA_EXISTS gates, the pasaflow failure mode returns silently."""
    lines = _script_lines()

    gen_line = _find_line(lines, 'if ! grep -q "^MLFLOW_DB_PASSWORD=." "$ENV_FILE"')

    env_new_line = _find_line(
        lines, "Generating real credentials into the new .env"
    )
    # The say() call sits just inside the ENV_IS_NEW block — find that block's
    # opening `if` by scanning backwards to the nearest `if` line.
    open_line = next(
        i
        for i in range(env_new_line, -1, -1)
        if lines[i].strip().startswith("if ")
    )
    start, end = _block_range(lines, open_line)
    assert not (start <= gen_line <= end), (
        "MLFLOW_DB_PASSWORD generation moved back inside the ENV_IS_NEW "
        "credential block — a pre-existing .env without the var (the pasaflow "
        "box) would again never get one, and mlflow crash-loops on mlflow:mlflow"
    )
    block_text = "\n".join(lines[start : end + 1])
    assert "MLFLOW" not in block_text, (
        "the ENV_IS_NEW/POSTGRES_DATA_EXISTS credential block mentions MLFLOW "
        "again — mlflow's password must not share Postgres's volume-state "
        "constraint (Phase 5.5 converges the role on every deploy)"
    )


def _heredoc(delim: str) -> str:
    lines = _script_lines()
    open_line = _find_line(lines, f"<<'{delim}'")
    close_hits = [i for i, line in enumerate(lines) if line.strip() == delim]
    assert close_hits, f"{delim} heredoc never closes"
    close_line = next(i for i in close_hits if i > open_line)
    return "\n".join(lines[open_line + 1 : close_line])


def _provisioning_heredoc() -> str:
    return _heredoc("PSQL")


def test_provisioning_is_create_or_alter_for_both_role_and_database() -> None:
    sql = _provisioning_heredoc()

    # Create-if-missing arms must be guarded (idempotent against an existing
    # first-init box) …
    assert re.search(
        r"CREATE ROLE %I LOGIN'.*?WHERE NOT EXISTS \(SELECT FROM pg_roles", sql, re.S
    ), "role creation lost its NOT-EXISTS guard — reruns would error, not converge"
    assert re.search(
        r"CREATE DATABASE %I OWNER %I'.*?WHERE NOT EXISTS \(SELECT FROM pg_database",
        sql,
        re.S,
    ), (
        "database creation lost its NOT-EXISTS guard or its OWNER — on PG15+ a "
        "non-owner role cannot CREATE TABLE in `public`, so MLflow still fails"
    )

    # … while the converge arms must be UNguarded, or an existing role/db
    # keeps its stale password/owner and the crash-loop this phase exists to
    # fix survives the fix.
    alter_role = re.search(r'^ALTER ROLE :"mlflow_db_user" WITH LOGIN PASSWORD', sql, re.M)
    assert alter_role, "unconditional ALTER ROLE … PASSWORD is gone — an existing role never converges on .env"
    alter_db = re.search(r'^ALTER DATABASE :"mlflow_db" OWNER TO', sql, re.M)
    assert alter_db, "unconditional ALTER DATABASE … OWNER is gone — a pre-existing mlflow database keeps its superuser owner"


def test_provisioning_passes_secrets_via_env_not_shell_interpolation() -> None:
    """The heredoc delimiter must stay quoted (<<'PSQL'): an unquoted heredoc
    would shell-expand `$` inside the SQL — and invite writing the password
    into the SQL text itself, which then lands in $LOG. Secrets travel via
    `exec -e` + \\getenv only (same channel postgres-init.sql uses)."""
    lines = _script_lines()
    _find_line(lines, "<<'PSQL'")  # asserts quoted form, exactly once
    _find_line(lines, '-e MLFLOW_DB_PASSWORD="$MLFLOW_PW"')  # env channel, exactly once


def test_provisioning_refuses_to_alter_the_application_role() -> None:
    """ALTER ROLE on POSTGRES_USER would rotate the application's own
    password to mlflow's and break every DATABASE_URL in the stack at once."""
    lines = _script_lines()
    guard = _find_line(lines, '"$MLFLOW_ROLE" == "$PG_USER"')
    start, end = _block_range(
        lines,
        next(i for i in range(guard, -1, -1) if lines[i].strip().startswith("if ")),
    )
    block = "\n".join(lines[start : end + 1])
    assert "psql" in block, (
        "the MLFLOW_DB_USER==POSTGRES_USER guard no longer encloses the psql "
        "provisioning call — the dangerous ALTER can now run for the app role"
    )


def test_provisioning_converges_existing_object_ownership() -> None:
    """ALTER DATABASE OWNER does not cascade: a box where MLflow previously
    ran as POSTGRES_USER has every table owned by that role, and the fresh
    `mlflow` role dies on `permission denied for table alembic_version` the
    moment auth works — proven live on the pasaflow box 2026-08-08 (19
    slm-owned tables). The PSQL_OWN heredoc must enumerate and re-own tables
    AND sequences; and it must never be spelled as REASSIGN OWNED BY, which
    also transfers shared objects (the application database included)."""
    sql = _heredoc("PSQL_OWN")
    assert re.search(
        r"ALTER TABLE public\.%I OWNER TO %I.*?FROM pg_tables", sql, re.S
    ), "table re-owning enumeration is gone — pre-round-3 boxes crash-loop again"
    assert re.search(
        r"ALTER SEQUENCE public\.%I OWNER TO %I.*?FROM pg_sequences", sql, re.S
    ), "sequence re-owning enumeration is gone"
    lines = _script_lines()
    whole = "\n".join(lines)
    assert "REASSIGN OWNED" not in whole.replace("NOT `REASSIGN OWNED BY`", ""), (
        "someone 'simplified' the ownership transfer to REASSIGN OWNED BY — "
        "that also transfers ownership of the application database itself"
    )
    # The ownership pass must run against the MLFLOW database, not the app DB.
    own_exec = _find_line(lines, 'psql -q -U "$PG_USER" -d "$MLFLOW_DB_NAME"')
    assert own_exec, "ownership pass no longer targets the mlflow database"


def test_init_sql_creates_role_first_and_database_with_owner() -> None:
    sql_lines = _INIT_SQL.read_text(encoding="utf-8").splitlines()
    code = [
        (i, line)
        for i, line in enumerate(sql_lines)
        if line.strip() and not line.strip().startswith("--")
    ]
    role_lines = [i for i, line in code if line.startswith("CREATE ROLE")]
    db_lines = [i for i, line in code if line.startswith("CREATE DATABASE")]
    assert role_lines and db_lines, "postgres-init.sql lost its CREATE statements"
    assert role_lines[0] < db_lines[0], (
        "postgres-init.sql creates the database before the role — CREATE "
        "DATABASE … OWNER then errors on first init, which is the only time "
        "this file ever runs"
    )
    db_stmt = sql_lines[db_lines[0]]
    assert 'OWNER :"mlflow_db_user"' in db_stmt, (
        "CREATE DATABASE mlflow lost its OWNER — on PG15+ the mlflow role can "
        "connect but not CREATE TABLE in `public`, so MLflow's first-boot "
        "migrations fail (quieter than the auth error, same outage)"
    )
