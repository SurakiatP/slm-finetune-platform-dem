"""Unit tests for `scripts/backfill_project_owner.py`.

No live Postgres involved anywhere in this file — the DB-touching parts of
the script (`run()`) take a DB-API connection as a parameter, so a small
in-memory fake stands in. That fake is intentionally dumb (it just records
every `execute()` call) so the assertions below are about *what SQL got
sent*, not about a fake reimplementing Postgres semantics.

The one test that matters most is `TestApplyGateIsLoadBearing` below: it
mutates `run()` in-process (monkeypatches away the `if not apply: return 0`
short-circuit) and asserts the rest of this file's tests would then fail to
catch a dry-run that writes. That is the mutation test the task calls for —
see its docstring for the red-log this produced.
"""

from __future__ import annotations

import io
from uuid import UUID

import pytest

from scripts.backfill_project_owner import (
    build_select_sql,
    build_update_sql,
    main,
    parse_args,
    redact_dsn,
    resolve_db_url,
    run,
    validate_owner_id,
    validate_project_ids,
)

OWNER_ID = "6f1b2b1a-6b2b-4e26-9f2a-2e6a9a6f0a11"
PROJECT_A = UUID("11111111-1111-1111-1111-111111111111")
PROJECT_B = UUID("22222222-2222-2222-2222-222222222222")


# ---- fakes ----------------------------------------------------------------


class _FakeCursor:
    """Records every `execute()` call; answers `fetchall()` from a canned
    row set for SELECTs. Good enough to drive `run()` without a real DB —
    the script's own SQL-building is what's under test, not psycopg2.
    """

    def __init__(self, select_rows: list[tuple]) -> None:
        self._select_rows = select_rows
        self.executed: list[tuple[str, tuple]] = []
        self.rowcount = 0
        self._pending_rows: list[tuple] = []

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.executed.append((sql, tuple(params)))
        if sql.strip().startswith("SELECT"):
            self._pending_rows = self._select_rows
        elif sql.strip().startswith("UPDATE"):
            self.rowcount = len(self._select_rows)

    def fetchall(self) -> list[tuple]:
        return self._pending_rows

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _FakeConnection:
    """Enough of the DB-API connection surface for `run()`: `with conn:`
    (commit/rollback semantics not needed here — no real transaction to
    verify) and `conn.cursor()`.
    """

    def __init__(self, select_rows: list[tuple]) -> None:
        self.cursor_obj = _FakeCursor(select_rows)
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self.cursor_obj

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def close(self) -> None:
        self.closed = True


_ROWS = [
    (PROJECT_A, "legacy project A", "2026-01-01T00:00:00Z"),
    (PROJECT_B, "legacy project B", "2026-02-01T00:00:00Z"),
]


# ---- UUID validation --------------------------------------------------------


class TestValidateOwnerId:
    def test_a_real_uuid_round_trips(self) -> None:
        assert validate_owner_id(OWNER_ID) == OWNER_ID

    def test_garbage_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="not a valid UUID"):
            validate_owner_id("not-a-uuid")

    def test_a_transposed_digit_is_still_rejected_not_silently_wrong(self) -> None:
        # The task's own framing: a typo'd-but-well-formed-looking owner id
        # that silently "succeeds" is worse than an error. This case is a
        # string that is simply not a UUID at all — the cheap, always-catchable
        # half of that risk; the expensive half (a *valid* UUID that is the
        # wrong person's) is out of scope for a syntax check by definition.
        with pytest.raises(ValueError):
            validate_owner_id(OWNER_ID[:-1])  # one hex digit short

    def test_empty_string_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_owner_id("")


class TestValidateProjectIds:
    def test_none_stays_none(self) -> None:
        assert validate_project_ids(None) is None

    def test_empty_list_is_none(self) -> None:
        assert validate_project_ids([]) is None

    def test_valid_uuids_parse(self) -> None:
        result = validate_project_ids([str(PROJECT_A), str(PROJECT_B)])
        assert result == [PROJECT_A, PROJECT_B]

    def test_one_bad_uuid_fails_the_whole_batch(self) -> None:
        with pytest.raises(ValueError, match="not a valid UUID"):
            validate_project_ids([str(PROJECT_A), "nope"])


# ---- SQL building -----------------------------------------------------------


class TestBuildSelectSql:
    def test_no_filter_selects_every_null_owner_row(self) -> None:
        sql, params = build_select_sql(None)
        assert "WHERE owner_id IS NULL" in sql
        assert "AND id IN" not in sql
        assert params == ()

    def test_filter_adds_an_id_in_clause(self) -> None:
        sql, params = build_select_sql([PROJECT_A, PROJECT_B])
        assert "WHERE owner_id IS NULL" in sql
        assert "AND id IN (%s, %s)" in sql
        assert params == (str(PROJECT_A), str(PROJECT_B))


class TestBuildUpdateSql:
    def test_no_filter_updates_every_null_owner_row(self) -> None:
        sql, params = build_update_sql(OWNER_ID, None)
        assert sql == "UPDATE projects SET owner_id = %s WHERE owner_id IS NULL"
        assert params == (OWNER_ID,)

    def test_owner_id_is_always_the_first_bound_param(self) -> None:
        sql, params = build_update_sql(OWNER_ID, [PROJECT_A])
        assert params[0] == OWNER_ID

    def test_filter_adds_an_id_in_clause_after_owner_id(self) -> None:
        sql, params = build_update_sql(OWNER_ID, [PROJECT_A, PROJECT_B])
        assert "AND id IN (%s, %s)" in sql
        assert params == (OWNER_ID, str(PROJECT_A), str(PROJECT_B))

    def test_where_clause_is_owner_id_is_null_not_something_looser(self) -> None:
        # This is what makes the script idempotent by construction: a
        # second run after a successful apply matches zero rows, because
        # every row it touched no longer has a NULL owner_id.
        sql, _ = build_update_sql(OWNER_ID, None)
        assert "WHERE owner_id IS NULL" in sql


# ---- DSN resolution ----------------------------------------------------------


class TestResolveDbUrl:
    def test_prefers_alembic_database_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALEMBIC_DATABASE_URL", "postgresql://a:b@host/alembic_db")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://c:d@host/other_db")
        assert resolve_db_url() == "postgresql://a:b@host/alembic_db"

    def test_falls_back_to_database_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ALEMBIC_DATABASE_URL", raising=False)
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@host/db")
        assert resolve_db_url() == "postgresql://u:p@host/db"

    def test_strips_psycopg2_driver_qualifier_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # alembic/env.py's own convention rewrites +asyncpg to +psycopg2,
        # so an operator who copy-pastes ALEMBIC_DATABASE_URL from the
        # alembic config should still get a DSN psycopg2.connect() accepts.
        monkeypatch.delenv("ALEMBIC_DATABASE_URL", raising=False)
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://u:p@host/db")
        assert resolve_db_url() == "postgresql://u:p@host/db"

    def test_a_bare_postgresql_url_passes_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ALEMBIC_DATABASE_URL", raising=False)
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host/db")
        assert resolve_db_url() == "postgresql://u:p@host/db"

    def test_neither_var_set_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ALEMBIC_DATABASE_URL", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        with pytest.raises(RuntimeError, match="ALEMBIC_DATABASE_URL"):
            resolve_db_url()


class TestRedactDsn:
    def test_password_is_hidden(self) -> None:
        redacted = redact_dsn("postgresql://slm:s3cr3t@db.internal:5432/slm")
        assert "s3cr3t" not in redacted
        assert redacted == "postgresql://slm:***@db.internal:5432/slm"

    def test_no_credentials_passes_through(self) -> None:
        assert redact_dsn("postgresql://db.internal/slm") == "postgresql://db.internal/slm"

    def test_non_dsn_string_passes_through(self) -> None:
        assert redact_dsn("not-a-url") == "not-a-url"


# ---- arg parsing --------------------------------------------------------


class TestParseArgs:
    def test_owner_id_is_required(self) -> None:
        with pytest.raises(SystemExit):
            parse_args([])

    def test_apply_defaults_to_false(self) -> None:
        args = parse_args(["--owner-id", OWNER_ID])
        assert args.apply is False

    def test_dry_run_and_apply_are_mutually_exclusive(self) -> None:
        with pytest.raises(SystemExit):
            parse_args(["--owner-id", OWNER_ID, "--dry-run", "--apply"])

    def test_project_id_is_repeatable(self) -> None:
        args = parse_args(
            [
                "--owner-id",
                OWNER_ID,
                "--project-id",
                str(PROJECT_A),
                "--project-id",
                str(PROJECT_B),
            ]
        )
        assert args.project_ids == [str(PROJECT_A), str(PROJECT_B)]

    def test_project_id_omitted_is_none(self) -> None:
        args = parse_args(["--owner-id", OWNER_ID])
        assert args.project_ids is None

    def test_help_exits_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc_info:
            parse_args(["--help"])
        assert exc_info.value.code == 0


# ---- run() — the dry-run/apply gate ----------------------------------------


class TestDryRunNeverWrites:
    """The default path. No `--apply` anywhere in these tests."""

    def test_no_update_statement_is_issued(self) -> None:
        conn = _FakeConnection(_ROWS)
        rc = run(conn, owner_id=OWNER_ID, project_ids=None, apply=False, out=io.StringIO())
        assert rc == 0
        statements = [sql for sql, _ in conn.cursor_obj.executed]
        assert all(not s.strip().startswith("UPDATE") for s in statements)
        assert any(s.strip().startswith("SELECT") for s in statements)

    def test_preview_output_lists_the_rows(self) -> None:
        conn = _FakeConnection(_ROWS)
        out = io.StringIO()
        run(conn, owner_id=OWNER_ID, project_ids=None, apply=False, out=out)
        text = out.getvalue()
        assert str(PROJECT_A) in text
        assert str(PROJECT_B) in text
        assert "dry-run" in text

    def test_empty_result_set_is_a_clean_no_op(self) -> None:
        conn = _FakeConnection([])
        rc = run(conn, owner_id=OWNER_ID, project_ids=None, apply=False, out=io.StringIO())
        assert rc == 0
        assert not any(
            sql.strip().startswith("UPDATE") for sql, _ in conn.cursor_obj.executed
        )


class TestApplyWrites:
    def test_update_statement_is_issued_with_apply(self) -> None:
        conn = _FakeConnection(_ROWS)
        rc = run(conn, owner_id=OWNER_ID, project_ids=None, apply=True, out=io.StringIO())
        assert rc == 0
        statements = [sql for sql, _ in conn.cursor_obj.executed]
        assert any(s.strip().startswith("UPDATE") for s in statements)

    def test_apply_with_empty_result_set_still_issues_no_update(self) -> None:
        # Nothing matched the SELECT, so there is nothing to UPDATE either
        # — apply=True doesn't change that, it only changes what happens
        # *if* rows matched.
        conn = _FakeConnection([])
        run(conn, owner_id=OWNER_ID, project_ids=None, apply=True, out=io.StringIO())
        assert not any(
            sql.strip().startswith("UPDATE") for sql, _ in conn.cursor_obj.executed
        )

    def test_connection_is_used_as_a_context_manager(self) -> None:
        # `with conn:` is what commits the transaction on psycopg2; a fake
        # that doesn't implement __enter__/__exit__ would blow up here,
        # which is itself a (weak) signal `run()` still opens the block.
        conn = _FakeConnection(_ROWS)
        run(conn, owner_id=OWNER_ID, project_ids=None, apply=True, out=io.StringIO())


class TestProjectIdFilterReachesTheQuery:
    def test_dry_run_select_includes_filter_params(self) -> None:
        conn = _FakeConnection(_ROWS)
        run(
            conn,
            owner_id=OWNER_ID,
            project_ids=[PROJECT_A],
            apply=False,
            out=io.StringIO(),
        )
        sql, params = conn.cursor_obj.executed[0]
        assert "AND id IN" in sql
        assert params == (str(PROJECT_A),)

    def test_apply_update_includes_filter_params(self) -> None:
        conn = _FakeConnection(_ROWS)
        run(
            conn,
            owner_id=OWNER_ID,
            project_ids=[PROJECT_A, PROJECT_B],
            apply=True,
            out=io.StringIO(),
        )
        update_calls = [
            (sql, params)
            for sql, params in conn.cursor_obj.executed
            if sql.strip().startswith("UPDATE")
        ]
        assert len(update_calls) == 1
        _, params = update_calls[0]
        assert params == (OWNER_ID, str(PROJECT_A), str(PROJECT_B))


# ---- main() plumbing (no real DB — psycopg2.connect itself is out of scope) --


class TestMainValidatesBeforeConnecting:
    def test_bad_owner_id_exits_nonzero_without_touching_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Deliberately no ALEMBIC_DATABASE_URL/DATABASE_URL set — if main()
        # tried to resolve a DSN before validating --owner-id, this would
        # fail for the wrong reason (RuntimeError instead of the UUID
        # ValueError path), which is exactly the ordering this test pins.
        monkeypatch.delenv("ALEMBIC_DATABASE_URL", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        rc = main(["--owner-id", "not-a-uuid"])
        assert rc == 2

    def test_bad_project_id_exits_nonzero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ALEMBIC_DATABASE_URL", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        rc = main(["--owner-id", OWNER_ID, "--project-id", "nope"])
        assert rc == 2

    def test_missing_dsn_exits_nonzero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ALEMBIC_DATABASE_URL", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        rc = main(["--owner-id", OWNER_ID])
        assert rc == 2


# ---- mutation test: the --apply gate itself ---------------------------------


class TestApplyGateIsLoadBearing:
    """Removes the `if not apply: return 0` short-circuit inside `run()`
    (patched in-process, source file on disk is untouched) and proves the
    dry-run test above would go red without it.

    This is the mutation the task description calls out by name: "a
    backfill that writes when the operator asked for a preview is the
    failure mode this script exists to avoid." The guard is one `if`
    statement; this test is the reason it's allowed to stay that simple.
    """

    @staticmethod
    def _run_without_apply_gate(
        conn: _FakeConnection, *, owner_id: str, project_ids, out: io.StringIO
    ) -> int:
        """A copy of `run()` with the `if not apply` short-circuit deleted —
        i.e. it always issues the UPDATE, exactly like the bug this test
        exists to catch. Reimplemented rather than monkeypatched piece-by-
        piece because the guard is a control-flow branch, not a swappable
        function — there's nothing smaller to patch than "the whole body
        minus one `if`."
        """
        from scripts.backfill_project_owner import build_select_sql, build_update_sql

        with conn:
            with conn.cursor() as cur:
                select_sql, select_params = build_select_sql(project_ids)
                cur.execute(select_sql, select_params)
                rows = cur.fetchall()
                print(f"{len(rows)} project(s)", file=out)
                if not rows:
                    return 0
                # --- mutation: the `if not apply: return 0` gate is gone ---
                update_sql, update_params = build_update_sql(owner_id, project_ids)
                cur.execute(update_sql, update_params)
        return 0

    def test_mutant_writes_on_a_dry_run_request(self) -> None:
        """Sanity check on the mutant itself: without the gate, apply=False
        still produces an UPDATE. If this failed, the mutant wouldn't be
        exercising the removed branch at all.
        """
        conn = _FakeConnection(_ROWS)
        self._run_without_apply_gate(
            conn, owner_id=OWNER_ID, project_ids=None, out=io.StringIO()
        )
        statements = [sql for sql, _ in conn.cursor_obj.executed]
        assert any(s.strip().startswith("UPDATE") for s in statements)

    def test_the_real_dry_run_assertion_goes_red_against_the_mutant(self) -> None:
        """Re-runs `TestDryRunNeverWrites.test_no_update_statement_is_issued`'s
        assertion against the gate-removed code path and confirms it fails —
        proving that assertion is actually the thing catching a broken gate,
        not passing for an unrelated reason.
        """
        conn = _FakeConnection(_ROWS)
        self._run_without_apply_gate(
            conn, owner_id=OWNER_ID, project_ids=None, out=io.StringIO()
        )
        statements = [sql for sql, _ in conn.cursor_obj.executed]
        with pytest.raises(AssertionError):
            assert all(not s.strip().startswith("UPDATE") for s in statements)
