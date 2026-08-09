"""Guards for the backup/restore/verify script trio (gap-analysis item 9).

Why this exists: `scripts/backup.sh` / `scripts/restore.sh` /
`scripts/verify_restore.sh` encode several load-bearing invariants that are
easy to silently break with an innocuous-looking edit:

  * backup.sh MUST dump Postgres before it mirrors MinIO — that ordering,
    combined with the upload-before-commit contract in workers/tasks/*.py
    ("item 13"), is what guarantees the MinIO mirror is always a superset
    of everything the DB dump can reference, even with writers active the
    entire time the backup runs (see backup.sh's header comment).
  * restore.sh must restore globals -> per-DB dumps -> bucket mirrors, in
    that order, and must refuse to target what looks like the live stack
    unless a human explicitly opts in via RESTORE_ALLOW_LIVE.
  * verify_restore.sh's five check groups are what actually prove a given
    backup set restores completely and consistently — a group silently
    becoming warn-only, or losing its FAIL_COUNT contribution, would make
    the whole rehearsal a no-op that always reports success.
  * Credentials (MINIO_ROOT_PASSWORD, RESTORE_MINIO_PASSWORD) must never
    reach a say/ok/warn/fail/echo call site — only the MC_HOST_* env
    assignment channel.

These assertions are enumerated over parsed structure (line indexes, case
arms, block ranges), never matched against the whole file as one blob — see
test_mlflow_provisioning.py's module docstring for why a whole-file
substring guard goes green the moment its author edits any one call site.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_BACKUP = _REPO / "scripts" / "backup.sh"
_RESTORE = _REPO / "scripts" / "restore.sh"
_VERIFY = _REPO / "scripts" / "verify_restore.sh"

_REQUIRED_RESTORE_VARS = (
    "RESTORE_PG_CONTAINER",
    "RESTORE_MINIO_URL",
    "RESTORE_MINIO_USER",
    "RESTORE_MINIO_PASSWORD",
    "RESTORE_MC_NETWORK",
)
_BUCKET_VAR_NAMES = ("MINIO_DATASETS_BUCKET", "MINIO_MODELS_BUCKET", "MLFLOW_S3_BUCKET")


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _find_line(lines: list[str], needle: str, label: str) -> int:
    """Return the single line index containing `needle`. Asserts exactly one
    occurrence — both a non-vacuity backstop (the guard is testing
    something real) and an anti-drift guard (a second, ambiguous
    occurrence means these line-index-based assertions need updating
    deliberately, not silently)."""
    hits = [i for i, line in enumerate(lines) if needle in line]
    assert hits, f"`{needle}` not found in {label} — non-vacuity backstop"
    assert len(hits) == 1, (
        f"`{needle}` appears {len(hits)} times in {label}; these guards "
        "assume one occurrence — update them deliberately, not by deletion"
    )
    return hits[0]


def _find_lines(lines: list[str], pattern: str, label: str) -> list[int]:
    """Return every line index matching regex `pattern`. Callers must assert
    non-emptiness themselves before relying on min()/max() over the result —
    an empty enumeration makes any ordering assertion pass vacuously."""
    rx = re.compile(pattern)
    return [i for i, line in enumerate(lines) if rx.search(line)]


def _case_arms(lines: list[str], case_line: int, label: str) -> list[str]:
    """Parse the arm labels of a `case ... in ... esac` block opening at
    `case_line`, returning e.g. ['slm', 'mlflow', 'all']."""
    arms: list[str] = []
    for i in range(case_line + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped == "esac":
            return arms
        m = re.match(r"^([\w|]+)\)", stripped)
        if m:
            arms.append(m.group(1))
    raise AssertionError(
        f"case block opening at line {case_line + 1} of {label} never closes "
        "with `esac` — either the script is broken or this parser is; do "
        "not delete the assertion, an enumeration over nothing passes "
        "vacuously"
    )


# =============================================================================
# (a) ORDERING: backup.sh dumps Postgres before it mirrors MinIO.
# =============================================================================
def test_backup_dumps_postgres_before_mirroring_minio() -> None:
    lines = _lines(_BACKUP)
    label = _BACKUP.name

    db_hits = _find_lines(lines, r"\bpg_dump(all)?\b", label)
    db_hits = [i for i in db_hits if not lines[i].strip().startswith("#")]
    assert db_hits, (
        f"no pg_dump/pg_dumpall invocation found in {label} — the ordering "
        "guard below would pass vacuously without this"
    )

    mirror_hits = _find_lines(lines, r"\bmirror --overwrite\b", label)
    assert mirror_hits, (
        f"no `mc ... mirror` invocation found in {label} — the ordering "
        "guard below would pass vacuously without this"
    )

    assert max(db_hits) < min(mirror_hits), (
        "a pg_dump/pg_dumpall invocation now runs at or after the first "
        "MinIO mirror in backup.sh — this breaks the dumps-before-mirrors "
        "guarantee documented in the script's header (item 13): the mirror "
        "is only guaranteed to be a superset of what the DB dump can "
        "reference because the dump always runs first"
    )


# =============================================================================
# (b) BOTH DBS + GLOBALS via variable expansion, --globals-only exactly once.
# =============================================================================
def test_backup_dumps_both_databases_via_expansion_and_globals_once() -> None:
    lines = _lines(_BACKUP)
    label = _BACKUP.name

    db_hits = [
        i
        for i in _find_lines(lines, r"\bpg_dump(all)?\b", label)
        if not lines[i].strip().startswith("#")
    ]
    assert db_hits, f"no pg_dump/pg_dumpall invocation found in {label}"
    texts = [lines[i] for i in db_hits]

    assert any(re.search(r"\$\{?POSTGRES_DB\}?", t) for t in texts), (
        "no pg_dump invocation in backup.sh references $POSTGRES_DB / "
        "${POSTGRES_DB} — the app-DB dump may have regressed to a literal "
        "database name, which breaks any deployment where POSTGRES_DB "
        "isn't the default"
    )
    assert any(re.search(r"\$\{?MLFLOW_DB\}?", t) for t in texts), (
        "no pg_dump invocation in backup.sh references $MLFLOW_DB / "
        "${MLFLOW_DB} — the mlflow-DB dump may have regressed to a literal "
        "database name"
    )

    globals_hits = _find_lines(lines, r"--globals-only", label)
    assert globals_hits, f"--globals-only not found in {label} — role/password backup is gone"
    assert len(globals_hits) == 1, (
        f"--globals-only appears {len(globals_hits)} times in {label}, expected "
        "exactly 1 — either a duplicate dump was added or the guard needs updating"
    )


# =============================================================================
# (c) SECRETS CHANNEL: no say/ok/warn/fail/echo line ever carries a secret;
#     credentials travel only via MC_HOST_ env assignments.
# =============================================================================
def test_no_say_ok_warn_fail_echo_line_ever_carries_a_secret() -> None:
    call_pattern = re.compile(r'^\s*(say|ok|warn|fail)\s+"|^\s*echo\b')
    scripts = {_BACKUP: _lines(_BACKUP), _RESTORE: _lines(_RESTORE), _VERIFY: _lines(_VERIFY)}

    all_call_lines: list[tuple[str, int, str]] = []
    for path, lines in scripts.items():
        hits = [i for i, line in enumerate(lines) if call_pattern.search(line)]
        assert hits, (
            f"no say/ok/warn/fail/echo call sites found in {path.name} — "
            "the non-vacuity backstop for the secrets-channel guard below "
            "would be checking nothing"
        )
        for i in hits:
            all_call_lines.append((path.name, i, lines[i]))

    for label, idx, text in all_call_lines:
        assert "MINIO_ROOT_PASSWORD" not in text, (
            f"{label}:{idx + 1} passes MINIO_ROOT_PASSWORD to a say/ok/warn/fail/echo "
            "call — secrets must travel only via MC_HOST_* env assignments, never "
            "through anything that gets tee'd to a log file"
        )
        assert "RESTORE_MINIO_PASSWORD" not in text, (
            f"{label}:{idx + 1} passes RESTORE_MINIO_PASSWORD to a say/ok/warn/fail/echo "
            "call — secrets must travel only via MC_HOST_* env assignments, never "
            "through anything that gets tee'd to a log file"
        )


def test_credentials_travel_only_via_mc_host_assignments() -> None:
    mc_host_pattern = r"-e\s+MC_HOST_\w+="
    expected_counts = {_BACKUP: 1, _RESTORE: 2, _VERIFY: 1}

    total = 0
    for path, expected in expected_counts.items():
        lines = _lines(path)
        hits = _find_lines(lines, mc_host_pattern, path.name)
        assert hits, f"no MC_HOST_* env assignment found in {path.name}"
        assert len(hits) == expected, (
            f"{path.name} has {len(hits)} MC_HOST_* env assignment(s), expected "
            f"exactly {expected} — the credential-channel shape changed, update "
            "this guard deliberately if that's intentional"
        )
        total += len(hits)

    assert total == 4, (
        f"total MC_HOST_* assignments across all three scripts is {total}, expected 4"
    )


# =============================================================================
# (d) BUCKET NAMES: every mc mirror/mb operand is a variable expansion.
# =============================================================================
def test_mirror_and_mb_bucket_operands_are_always_variable_expansions() -> None:
    literal_names = {"datasets", "models", "mlflow"}
    operand_re = re.compile(r'"(?:live|target)/([^"]*)"')

    hits: list[tuple[str, int, str]] = []
    for path in (_BACKUP, _RESTORE):
        lines = _lines(path)
        for i in _find_lines(lines, r"\b(mirror --overwrite|mb --ignore-existing)\b", path.name):
            if lines[i].strip().startswith("#"):
                continue
            hits.append((path.name, i, lines[i]))

    assert hits, "no `mc mirror`/`mc mb` invocation found across backup.sh/restore.sh"

    for label, idx, text in hits:
        m = operand_re.search(text)
        assert m, (
            f"{label}:{idx + 1} is an mc mirror/mb call whose bucket operand "
            "doesn't match the expected live/<x> or target/<x> shape — could "
            "not verify it's a variable expansion"
        )
        operand = m.group(1)
        assert "$" in operand, (
            f"{label}:{idx + 1} mc mirror/mb bucket operand `{operand}` has no "
            "variable expansion — bucket names must never be hardcoded so a "
            "custom MINIO_*_BUCKET/MLFLOW_S3_BUCKET override is always honored"
        )
        bare = operand.strip("${}")
        assert bare not in literal_names, (
            f"{label}:{idx + 1} mc mirror/mb bucket operand `{operand}` looks "
            f"like it resolves to a hardcoded literal ({bare!r}) rather than "
            "an env-driven bucket name"
        )


# =============================================================================
# (e) RETENTION: BACKUP_RETAIN default 7; prune loop gated on BACKUP_COMPLETE.
# =============================================================================
def test_backup_retain_defaults_to_seven() -> None:
    lines = _lines(_BACKUP)
    label = _BACKUP.name
    idx = _find_line(lines, "BACKUP_RETAIN=", label)
    m = re.search(r"env_val BACKUP_RETAIN (\d+)\)", lines[idx])
    assert m, f"could not parse BACKUP_RETAIN default out of {label}:{idx + 1}: {lines[idx]!r}"
    assert m.group(1) == "7", (
        f"BACKUP_RETAIN default in {label} is {m.group(1)}, expected 7 — this "
        "must match the value documented in docs/runbooks/backup_restore.md's "
        "Retention section (see test_backup_restore_runbook.py's drift guard)"
    )


def test_retention_prune_only_ever_touches_backup_complete_sets() -> None:
    lines = _lines(_BACKUP)
    label = _BACKUP.name

    complete_check = _find_line(lines, 'if [[ -f "$d/BACKUP_COMPLETE" ]]; then', label)
    append_line = _find_line(lines, 'COMPLETE_SETS+=("$d")', label)
    assert append_line == complete_check + 1, (
        "COMPLETE_SETS is no longer populated as the immediate then-branch of "
        "the BACKUP_COMPLETE existence check — an incomplete set could end up "
        "eligible for pruning"
    )

    rm_line = _find_line(lines, 'rm -rf "${COMPLETE_SETS[$i]}"', label)
    assert rm_line > append_line, (
        "the retention rm -rf no longer runs against the COMPLETE_SETS array "
        "built from the BACKUP_COMPLETE-gated loop above it"
    )


# =============================================================================
# (f) RESTORE ORDERING + SAFETY.
# =============================================================================
def test_restore_order_is_globals_then_pg_restore_then_mirror() -> None:
    lines = _lines(_RESTORE)
    label = _RESTORE.name

    globals_line = _find_line(
        lines, 'psql -v ON_ERROR_STOP=0 -U "$POSTGRES_USER" -d postgres < "$GLOBALS_FILE"', label
    )

    pg_restore_hits = [
        i for i in _find_lines(lines, r"\bpg_restore\b", label) if not lines[i].strip().startswith("#")
    ]
    assert pg_restore_hits, f"no pg_restore invocation found in {label}"
    first_pg_restore = min(pg_restore_hits)

    mirror_hits = _find_lines(lines, r"\bmirror --overwrite\b", label)
    assert mirror_hits, f"no `mc mirror` invocation found in {label}"
    first_mirror = min(mirror_hits)

    assert globals_line < first_pg_restore < first_mirror, (
        "restore.sh's ordering regressed from globals -> pg_restore -> mirror. "
        "Globals must land before any per-DB pg_restore (the mlflow role must "
        "exist before its DB is restored/owned), and DB restore is documented "
        "to precede bucket restore"
    )


def test_live_target_guard_names_both_targets_and_is_overridable_only_by_flag() -> None:
    lines = _lines(_RESTORE)
    label = _RESTORE.name

    pg_guard = _find_line(lines, 'RESTORE_PG_CONTAINER" == "slm-postgres"', label)

    # The MinIO side matches the PARSED host:port as whole-string case arms.
    # Substring matching would be wrong in both directions: it would miss
    # nothing, but it would misclassify the rehearsal target restore-minio:9000
    # (which contains "minio:9000") as the live stack, teaching operators to
    # set RESTORE_ALLOW_LIVE=1 by reflex on every rehearsal.
    minio_case = _find_line(lines, 'case "$MINIO_HOSTPORT" in', label)
    # Parsed here rather than via _case_arms(): these arms are glob patterns
    # containing ':', '*' and '.', which that helper's [\w|]+ arm regex (shaped
    # for the plain --db/--bucket word arms) deliberately does not accept.
    minio_arms: list[str] = []
    for i in range(minio_case + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped == "esac":
            break
        m = re.match(r"^([^\s()#]+)\)", stripped)
        if m:
            minio_arms.append(m.group(1))
    else:
        raise AssertionError(f"case block at {label}:{minio_case + 1} never closes with esac")
    assert len(minio_arms) == 1, (
        f"expected a single case arm enumerating the live MinIO identities in "
        f"{label}, found {minio_arms}"
    )
    patterns = set(minio_arms[0].split("|"))
    for required in ("minio", "minio:*", "slm-minio", "slm-minio:*", "localhost:9000", "127.0.0.1:9000"):
        assert required in patterns, (
            f"{label} live-MinIO case arm {sorted(patterns)} no longer includes "
            f"`{required}` — the compose service name, its container_name, and "
            "the 127.0.0.1:${MINIO_PORT:-9000} address docker-compose.yml "
            "publishes must all be treated as live"
        )
    assert minio_case > pg_guard, (
        "the MinIO live-target check no longer follows the postgres one"
    )
    # Non-vacuity: the guard must actually discriminate. Whole-string patterns
    # mean the rehearsal host is NOT live; a regression to substrings would
    # make one of these classify wrongly.
    assert not any(p in ("restore-minio", "restore-minio:9000") for p in patterns), (
        f"{label} treats the documented rehearsal MinIO target as the live "
        "stack — every rehearsal would demand RESTORE_ALLOW_LIVE=1"
    )
    minio_guard = minio_case

    override_line = _find_line(lines, '"${RESTORE_ALLOW_LIVE:-0}" != "1"', label)
    assert override_line > max(pg_guard, minio_guard), (
        "RESTORE_ALLOW_LIVE override check no longer comes after both "
        "live-target detection lines"
    )
    # No other env var name appears as an override condition on the same line.
    override_condition = lines[override_line]
    other_override_vars = re.findall(r"\$\{(\w+):-", override_condition)
    assert other_override_vars == ["RESTORE_ALLOW_LIVE"], (
        f"live-target override line now reads {other_override_vars} instead of "
        "exactly ['RESTORE_ALLOW_LIVE'] — another env var can bypass the guard"
    )


def test_restore_parses_db_and_bucket_flags_with_expected_case_arms() -> None:
    lines = _lines(_RESTORE)
    label = _RESTORE.name

    db_case_line = _find_line(lines, 'case "$DB_SELECT" in', label)
    db_arms = _case_arms(lines, db_case_line, label)
    assert set(db_arms) == {"slm", "mlflow", "all"}, (
        f"restore.sh's --db case arms are {db_arms}, expected exactly "
        "{'slm', 'mlflow', 'all'}"
    )

    bucket_case_line = _find_line(lines, 'case "$BUCKET_SELECT" in', label)
    bucket_arms = _case_arms(lines, bucket_case_line, label)
    assert set(bucket_arms) == {"datasets", "models", "mlflow", "all"}, (
        f"restore.sh's --bucket case arms are {bucket_arms}, expected exactly "
        "{'datasets', 'models', 'mlflow', 'all'}"
    )


def test_restore_target_vars_are_required_with_no_default() -> None:
    lines = _lines(_RESTORE)
    label = _RESTORE.name
    for var in _REQUIRED_RESTORE_VARS:
        idx = _find_line(lines, f"${{{var}:?", label)
        assert f"${{{var}:-" not in lines[idx], (
            f"{label}:{idx + 1} declares {var} with a `:?` required-form AND "
            "a `:-` default-form on the same line — pick one; a default on a "
            "restore target var is exactly the silent-mistarget risk this "
            "script exists to prevent"
        )
        # And confirm no *other* line anywhere gives this var a soft default,
        # which would make the :? above unreachable.
        default_hits = _find_lines(lines, re.escape(f"{var}:-"), label)
        assert not default_hits, (
            f"{var} has a `:-` (soft-default) usage elsewhere in {label} at "
            f"line(s) {[i + 1 for i in default_hits]} — this var must have no "
            "default anywhere, only the required :? form"
        )


# =============================================================================
# (f2) MC CONTAINER NETWORKING: never --network host (rootless netns), always
#      the operator-supplied RESTORE_MC_NETWORK.
# =============================================================================
def test_mc_container_never_uses_network_host() -> None:
    """Docker on the target box is rootless (uid 1001). A container started
    with `--network host` joins RootlessKit's CHILD network namespace, while
    ports published as 127.0.0.1:<port> listen in the PARENT namespace — so a
    loopback MinIO target is unreachable from inside the mc container and every
    bucket operation fails to connect. mc must join a real docker network and
    resolve MinIO by container-name DNS instead."""
    for path in (_RESTORE, _VERIFY):
        lines = _lines(path)
        # Comments explaining *why not* --network host are expected and must
        # not trip this guard; only executable lines count.
        offenders = [
            i
            for i in _find_lines(lines, r"--network\s+host\b", path.name)
            if not lines[i].strip().startswith("#")
        ]
        assert not offenders, (
            f"{path.name} line(s) {[i + 1 for i in offenders]} run a container "
            "with `--network host`. Under this box's rootless docker that joins "
            "RootlessKit's child netns, where 127.0.0.1-published ports are not "
            "reachable — the mc container cannot talk to MinIO at all. Use "
            '`--network "$RESTORE_MC_NETWORK"` and a container-DNS '
            "RESTORE_MINIO_URL"
        )


def test_mc_container_network_comes_from_restore_mc_network() -> None:
    expected_counts = {_RESTORE: 2, _VERIFY: 1}
    for path, expected in expected_counts.items():
        lines = _lines(path)
        hits = [
            i
            for i in _find_lines(lines, r'--network\s+"\$RESTORE_MC_NETWORK"', path.name)
            if not lines[i].strip().startswith("#")
        ]
        assert len(hits) == expected, (
            f"{path.name} has {len(hits)} `--network \"$RESTORE_MC_NETWORK\"` "
            f"site(s), expected exactly {expected} — every docker run that "
            "talks to MinIO must be attached to the operator-supplied network"
        )


# =============================================================================
# (g) VERIFY COMPLETENESS.
# =============================================================================
def test_verify_restore_has_exactly_five_check_groups() -> None:
    lines = _lines(_VERIFY)
    label = _VERIFY.name
    group_hits = _find_lines(lines, r"^#\s*GROUP\s+\d+:", label)
    assert group_hits, f"no `# GROUP N:` markers found in {label}"
    assert len(group_hits) == 5, (
        f"{label} has {len(group_hits)} GROUP markers, expected exactly 5 — "
        "a check group was added or removed without updating this guard"
    )


def test_verify_restore_fails_loudly_and_no_group_is_warn_only() -> None:
    lines = _lines(_VERIFY)
    label = _VERIFY.name

    fail_count_check = _find_line(lines, '"$FAIL_COUNT" -eq 0', label)
    exit1_hits = [i for i in _find_lines(lines, r"^\s*exit 1\s*$", label) if i > fail_count_check]
    assert exit1_hits, (
        f"no `exit 1` found after the final FAIL_COUNT check in {label} — a "
        "failed verification could return success"
    )

    for n in range(1, 6):
        _find_line(lines, f"FAIL_COUNT=$((FAIL_COUNT + GROUP{n}_FAIL))", label)
        # _find_line already asserts exactly one occurrence; if group N's
        # failure count is never folded into FAIL_COUNT, this raises — that
        # is precisely "group N became warn-only".


def test_verify_restore_references_every_chain_column() -> None:
    lines = _lines(_VERIFY)
    label = _VERIFY.name
    columns = (
        "external_project_id",
        "storage_uri",
        "gguf_uri",
        "lora_adapter_uri",
        "safetensors_uri",
        "usage_events",
        "audit_events",
    )
    # A mere mention is not a check. Group 5's `say "==== Group 5: ...
    # (storage_uri, gguf_uri, lora_adapter_uri, safetensors_uri) ===="` banner
    # names all four URI columns, so a presence-anywhere assertion stays green
    # after the actual `check_uri_column` call is deleted — verified by
    # mutation. Only lines that could actually execute a check count here:
    # comments and say/ok/warn/fail output lines are excluded.
    prose_re = re.compile(r'^\s*#|^\s*(say|ok|warn|fail)\s+"')
    for col in columns:
        hits = [
            i
            for i in _find_lines(lines, re.escape(col), label)
            if not prose_re.search(lines[i])
        ]
        assert hits, (
            f"{col} is only ever mentioned in a comment or a say/ok/warn/fail "
            f"banner in {label}, never in an executable check — the "
            "smart-model-tune chain / dangling-URI sweep for it is gone even "
            "though the word still appears in the file"
        )


# =============================================================================
# (g2) BACKUP DURABILITY INVARIANTS: marker written last, set dir private,
#      dumps in a format restore.sh can actually consume.
# =============================================================================
def test_backup_complete_marker_is_written_after_everything_else() -> None:
    """`BACKUP_COMPLETE` is the sole signal that a set is restorable (restore.sh
    refuses without it) and countable for retention. If it is ever written
    before the dumps/mirrors/manifest finish, a set interrupted midway looks
    complete and restore.sh will happily restore a truncated dump."""
    lines = _lines(_BACKUP)
    label = _BACKUP.name

    marker = _find_line(lines, '> "$SET_DIR/BACKUP_COMPLETE"', label)

    preceding = {
        "pg_dump/pg_dumpall": [
            i
            for i in _find_lines(lines, r"\bpg_dump(all)?\b", label)
            if not lines[i].strip().startswith("#")
        ],
        "mc mirror": _find_lines(lines, r"\bmirror --overwrite\b", label),
        "manifest write": _find_lines(lines, r'>> "\$MANIFEST"', label),
    }
    for what, hits in preceding.items():
        assert hits, (
            f"no {what} line found in {label} — this ordering guard would pass "
            "vacuously without one"
        )
        assert max(hits) < marker, (
            f"{label} writes BACKUP_COMPLETE at line {marker + 1}, at or before "
            f"its last {what} line ({max(hits) + 1}). The marker must be written "
            "LAST or an interrupted backup set becomes indistinguishable from a "
            "complete one"
        )


def test_backup_set_dir_is_chmod_700() -> None:
    """db/globals.sql carries Postgres role password hashes, and the box is a
    shared host (a second uid runs its own rootless docker on it)."""
    lines = _lines(_BACKUP)
    label = _BACKUP.name
    idx = _find_line(lines, 'chmod 700 "$SET_DIR"', label)
    assert idx < _find_line(lines, '> "$SET_DIR/BACKUP_COMPLETE"', label), (
        "the set dir is chmod 700'd after the backup already completed — it "
        "must be locked down at creation, before any dump lands in it"
    )


def test_both_dumps_use_custom_format_restore_can_consume() -> None:
    """restore.sh feeds these files to `pg_restore`, which only accepts the
    custom/directory/tar formats — a plain-SQL dump would restore as a no-op
    error at exactly the wrong moment."""
    lines = _lines(_BACKUP)
    label = _BACKUP.name
    dump_lines = [
        lines[i]
        for i in _find_lines(lines, r"\bpg_dump\b", label)
        if not lines[i].strip().startswith("#") and "pg_dumpall" not in lines[i]
    ]
    assert len(dump_lines) == 2, (
        f"expected exactly 2 non-globals pg_dump invocations in {label} (app DB "
        f"+ mlflow DB), found {len(dump_lines)}: {dump_lines!r}"
    )
    for text in dump_lines:
        assert "-Fc" in text, (
            f"pg_dump invocation `{text.strip()}` lost its -Fc custom-format "
            "flag — restore.sh runs pg_restore against these files and "
            "pg_restore cannot read a plain-SQL dump"
        )


def test_only_the_globals_step_tolerates_sql_errors() -> None:
    """Restoring roles is allowed to hit 'role already exists' on a reused
    target; every other step must abort loudly rather than leave a
    half-restored database that verify_restore.sh then has to catch."""
    lines = _lines(_RESTORE)
    label = _RESTORE.name

    tolerant = _find_lines(lines, r"ON_ERROR_STOP=0", label)
    assert len(tolerant) == 1, (
        f"{label} has {len(tolerant)} ON_ERROR_STOP=0 site(s), expected exactly 1 "
        "(the globals step). Error tolerance must not spread to any other step"
    )
    assert "globals" in lines[tolerant[0]].lower() or "GLOBALS_FILE" in lines[tolerant[0]], (
        f"{label}:{tolerant[0] + 1} is the only error-tolerant psql call but it "
        "is no longer the globals restore"
    )

    # Only real invocations: skip comments and the `ok "pg_restore complete"`
    # progress line, which mentions the command without running it.
    prose_re = re.compile(r'^\s*#|^\s*(say|ok|warn|fail)\s+"')
    pg_restore_lines = [
        lines[i]
        for i in _find_lines(lines, r"\bpg_restore\b", label)
        if not prose_re.search(lines[i])
    ]
    assert pg_restore_lines, f"no pg_restore invocation found in {label}"
    for text in pg_restore_lines:
        assert "--exit-on-error" in text, (
            f"pg_restore invocation `{text.strip()}` lost --exit-on-error — "
            "pg_restore's default is to log errors and exit 0, so a partially "
            "restored database would be reported as a successful restore"
        )


# =============================================================================
# (h) CROSS-FILE CONSISTENCY.
# =============================================================================
def test_required_restore_vars_match_between_restore_and_verify() -> None:
    def required_vars(path: Path) -> set[str]:
        text = "\n".join(_lines(path))
        return set(re.findall(r"\$\{(RESTORE_[A-Z_]+):\?", text))

    restore_vars = required_vars(_RESTORE)
    verify_vars = required_vars(_VERIFY)
    assert restore_vars, f"no required RESTORE_* vars found in {_RESTORE.name}"
    assert verify_vars, f"no required RESTORE_* vars found in {_VERIFY.name}"
    assert restore_vars == verify_vars, (
        f"required RESTORE_* env vars differ between scripts: "
        f"restore.sh={sorted(restore_vars)} verify_restore.sh={sorted(verify_vars)} "
        "— a rehearsal that passes verify_restore.sh's contract could still "
        "fail restore.sh's, or vice versa"
    )
    assert restore_vars == set(_REQUIRED_RESTORE_VARS), (
        f"required RESTORE_* vars {sorted(restore_vars)} no longer match the "
        f"expected set {sorted(_REQUIRED_RESTORE_VARS)} documented in this test"
    )


def test_bucket_env_var_names_match_across_all_three_scripts() -> None:
    def bucket_vars(path: Path) -> set[str]:
        text = "\n".join(_lines(path))
        return set(re.findall(r"\b(?:" + "|".join(_BUCKET_VAR_NAMES) + r")\b", text))

    per_file = {p: bucket_vars(p) for p in (_BACKUP, _RESTORE, _VERIFY)}
    for path, names in per_file.items():
        assert names, f"no bucket env var names found in {path.name}"

    values = list(per_file.values())
    assert values[0] == values[1] == values[2], (
        "bucket env var names diverge across scripts: "
        + ", ".join(f"{p.name}={sorted(v)}" for p, v in per_file.items())
    )
    assert values[0] == set(_BUCKET_VAR_NAMES), (
        f"bucket env var names {sorted(values[0])} no longer match the "
        f"expected set {sorted(_BUCKET_VAR_NAMES)}"
    )
