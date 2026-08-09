"""Guards for docs/runbooks/backup_restore.md (gap-analysis item 9).

Why this exists: the runbook is the human-facing contract for
scripts/backup.sh / scripts/restore.sh / scripts/verify_restore.sh — its
structure (the seven `## ` sections), its crontab line, its stated
retention default, and its Exclusions/Known-gaps lists are all things an
operator will trust verbatim during an actual disaster-recovery run. A
silently dropped section, a `sudo` sneaking into the cron line (this user
has no sudo on the pasaflow box — see the runbook's own access-constraints
paragraph), or the documented BACKUP_RETAIN default drifting from the
script's actual default are the kind of errors that only surface at 2am
during a real incident.

These assertions are enumerated over parsed structure (headings, section
text, regex-matched lines) rather than whole-file substring checks — see
test_mlflow_provisioning.py's module docstring for why that distinction
matters: a whole-file substring guard goes green the moment its author
edits any one call site, including moving text into the wrong section.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_RUNBOOK = _REPO / "docs" / "runbooks" / "backup_restore.md"
_BACKUP_SCRIPT = _REPO / "scripts" / "backup.sh"

_REQUIRED_HEADINGS = {
    "Full-box DR procedure",
    "Partial restore",
    "Consistency gap",
    "Retention and scheduling",
    "Known gaps",
    "Exclusions",
    "Rehearsal log",
}


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _find_line(lines: list[str], needle: str, label: str) -> int:
    """Return the single line index containing `needle`. Asserts exactly one
    occurrence — non-vacuity backstop plus anti-drift guard, same pattern as
    test_mlflow_provisioning.py and test_backup_restore_scripts.py."""
    hits = [i for i, line in enumerate(lines) if needle in line]
    assert hits, f"`{needle}` not found in {label} — non-vacuity backstop"
    assert len(hits) == 1, (
        f"`{needle}` appears {len(hits)} times in {label}; these guards "
        "assume one occurrence — update them deliberately, not by deletion"
    )
    return hits[0]


def _headings(lines: list[str]) -> list[tuple[int, str]]:
    """Enumerate `## ` (h2) headings as (line_index, title) pairs."""
    return [(i, line[3:].strip()) for i, line in enumerate(lines) if line.startswith("## ")]


def _section_text(lines: list[str], headings: list[tuple[int, str]], title: str) -> str:
    """Return the body text of the section whose heading title == `title`,
    from just after that heading up to (not including) the next heading."""
    idx_by_title = {t: i for i, t in headings}
    assert title in idx_by_title, f"no `## {title}` heading found to extract a section from"
    start = idx_by_title[title]
    later_starts = [i for i, _ in headings if i > start]
    end = min(later_starts) if later_starts else len(lines)
    return "\n".join(lines[start + 1 : end])


def _exclusion_bullets(section_text: str) -> list[str]:
    """Group the Exclusions section's markdown into per-bullet blocks: each
    block starts at a `- **` line and absorbs every following line up to
    the next `- **` line (bullets wrap across multiple physical lines)."""
    lines = section_text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith("- **")]
    assert starts, "no `- **name**.` bullet lines found in the Exclusions section"
    blocks = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        blocks.append(" ".join(l.strip() for l in lines[start:end]))
    return blocks


# =============================================================================
# (i) HEADINGS: exactly the seven required sections, order-insensitive.
# =============================================================================
def test_runbook_has_exactly_the_seven_required_headings() -> None:
    lines = _lines(_RUNBOOK)
    headings = _headings(lines)
    assert headings, f"no `## ` headings found in {_RUNBOOK.name} — parser or file is broken"

    titles = {t for _, t in headings}
    assert titles == _REQUIRED_HEADINGS, (
        f"runbook headings are {sorted(titles)}, expected exactly "
        f"{sorted(_REQUIRED_HEADINGS)} — a section was added, removed, or renamed "
        "without updating this guard (and without updating anything that links "
        "to the old title)"
    )
    assert len(headings) == len(_REQUIRED_HEADINGS), (
        f"found {len(headings)} `## ` heading lines but only "
        f"{len(_REQUIRED_HEADINGS)} distinct titles — a heading is duplicated"
    )


# =============================================================================
# (j) CRONTAB: exactly one 5-field cron line invoking backup.sh, no sudo.
# =============================================================================
def test_crontab_line_is_present_exactly_once_and_never_uses_sudo() -> None:
    lines = _lines(_RUNBOOK)
    cron_re = re.compile(r"([-0-9*/,]+\s+){4}[-0-9*/,]+\s+.*backup\.sh")
    hits = [i for i, line in enumerate(lines) if cron_re.search(line)]
    assert hits, (
        f"no crontab line matching a 5-field schedule + backup.sh invocation "
        f"found in {_RUNBOOK.name} — the scheduling instructions are gone"
    )
    assert len(hits) == 1, (
        f"found {len(hits)} crontab lines invoking backup.sh, expected exactly 1: "
        f"{[lines[i] for i in hits]}"
    )
    line = lines[hits[0]]
    assert "sudo" not in line, (
        f"{_RUNBOOK.name}:{hits[0] + 1} crontab line uses sudo — the runbook's "
        "own access-constraints note says this user has no sudo on the box "
        "(rootless docker, uid 1001); a sudo'd cron line would just fail"
    )


# =============================================================================
# (k) RETENTION AGREEMENT: runbook and backup.sh agree on BACKUP_RETAIN=7.
# =============================================================================
def test_retention_section_names_backup_retain_and_matches_script_default() -> None:
    runbook_lines = _lines(_RUNBOOK)
    headings = _headings(runbook_lines)
    section = _section_text(runbook_lines, headings, "Retention and scheduling")

    assert "BACKUP_RETAIN" in section, (
        "the Retention and scheduling section no longer names BACKUP_RETAIN — "
        "an operator reading only the runbook wouldn't know which env var controls it"
    )
    m = re.search(r"BACKUP_RETAIN.{0,20}?(\d+)", section)
    assert m, (
        "BACKUP_RETAIN is mentioned in the Retention section but no default "
        "number appears near it — could not parse the documented default"
    )
    documented_default = m.group(1)
    assert documented_default == "7", (
        f"Retention section documents BACKUP_RETAIN default as {documented_default}, "
        "expected 7"
    )

    script_lines = _lines(_BACKUP_SCRIPT)
    script_idx = _find_line(script_lines, "BACKUP_RETAIN=", _BACKUP_SCRIPT.name)
    script_m = re.search(r"env_val BACKUP_RETAIN (\d+)\)", script_lines[script_idx])
    assert script_m, (
        f"could not parse BACKUP_RETAIN default out of {_BACKUP_SCRIPT.name}:"
        f"{script_idx + 1}: {script_lines[script_idx]!r}"
    )
    assert documented_default == script_m.group(1), (
        f"runbook documents BACKUP_RETAIN default as {documented_default} but "
        f"{_BACKUP_SCRIPT.name} actually defaults it to {script_m.group(1)} — "
        "script and doc have drifted apart"
    )


# =============================================================================
# (l) EXCLUSIONS: redis, ollama, hf/huggingface each named with a reason.
# =============================================================================
def test_exclusions_section_names_redis_ollama_and_hf_cache_each_with_a_reason() -> None:
    runbook_lines = _lines(_RUNBOOK)
    headings = _headings(runbook_lines)
    section = _section_text(runbook_lines, headings, "Exclusions")
    bullets = _exclusion_bullets(section)
    assert bullets, "Exclusions section has no bullet entries to check"

    keywords = {
        "redis": "redis",
        "ollama": "ollama",
        "hf/huggingface cache": "hugging face",
    }
    min_reason_length = 60  # well past "- **Redis.**" alone; forces an actual reason clause

    for label, keyword in keywords.items():
        matches = [b for b in bullets if keyword.lower() in b.lower()]
        assert matches, (
            f"no Exclusions bullet mentions {label} — this deliberate exclusion "
            "is undocumented, an operator could mistake it for an oversight"
        )
        for bullet in matches:
            assert len(bullet) > min_reason_length, (
                f"Exclusions bullet for {label} is only {len(bullet)} chars "
                f"({bullet!r}) — it names the excluded thing but doesn't appear "
                "to carry an actual reason for excluding it"
            )


# =============================================================================
# (l2) MC NETWORKING: both procedures export RESTORE_MC_NETWORK, neither tells
#      the operator to reach MinIO over loopback.
# =============================================================================
def test_both_procedures_export_restore_mc_network() -> None:
    """restore.sh/verify_restore.sh require RESTORE_MC_NETWORK with no default
    (`:?`), so a procedure that omits the export just aborts at step 4."""
    runbook_lines = _lines(_RUNBOOK)
    headings = _headings(runbook_lines)
    for title in ("Full-box DR procedure", "Partial restore"):
        section = _section_text(runbook_lines, headings, title)
        assert "export RESTORE_MC_NETWORK=" in section, (
            f"the `{title}` section never exports RESTORE_MC_NETWORK — both "
            "restore.sh and verify_restore.sh require it via the `:?` form "
            "with no default, so following this procedure verbatim aborts "
            "before doing anything"
        )


def test_no_procedure_points_minio_at_loopback() -> None:
    """Rootless docker: `mc` cannot reach a 127.0.0.1-published port, because
    --network host lands it in RootlessKit's child netns while the published
    port listens in the parent. Any loopback RESTORE_MINIO_URL in the runbook
    is an instruction that cannot work on this box."""
    runbook_lines = _lines(_RUNBOOK)
    bad = [
        (i, line)
        for i, line in enumerate(runbook_lines)
        if re.search(r"RESTORE_MINIO_URL=\S*(localhost|127\.0\.0\.1)", line)
    ]
    assert not bad, (
        "runbook line(s) "
        + str([i + 1 for i, _ in bad])
        + " set RESTORE_MINIO_URL to a loopback address: "
        + str([l.strip() for _, l in bad])
        + ". Under rootless docker the mc container cannot reach a "
        "127.0.0.1-published port — use container-name DNS on "
        "RESTORE_MC_NETWORK instead"
    )


def test_dr_section_explains_why_not_network_host() -> None:
    """The rootless-netns reasoning has to live next to the procedure, or the
    next person 'simplifies' it back to --network host + localhost."""
    runbook_lines = _lines(_RUNBOOK)
    headings = _headings(runbook_lines)
    section = _section_text(runbook_lines, headings, "Full-box DR procedure").lower()
    assert "--network host" in section, (
        "the DR section no longer mentions --network host at all — the "
        "explanation of why it must not be used is gone"
    )
    for term in ("rootless", "namespace"):
        assert term in section, (
            f"the DR section's --network host explanation no longer mentions "
            f"'{term}' — without the rootless/netns reasoning it reads as an "
            "arbitrary style preference and will be reverted"
        )


# =============================================================================
# (m) KNOWN GAPS: offsite and BackupStale deferrals are recorded.
# =============================================================================
def test_known_gaps_section_records_offsite_and_backupstale_deferrals() -> None:
    runbook_lines = _lines(_RUNBOOK)
    headings = _headings(runbook_lines)
    section = _section_text(runbook_lines, headings, "Known gaps")
    assert section.strip(), "Known gaps section is empty"

    assert "offsite" in section.lower(), (
        "Known gaps section no longer mentions offsite backups — the "
        "single-disk-of-record risk (backups live on the same HDD as the "
        "data they protect) is undocumented"
    )
    assert "BackupStale" in section, (
        "Known gaps section no longer mentions BackupStale — the deferred "
        "freshness-alerting follow-up (tracked in TASK_TRACKER.md) is undocumented"
    )
