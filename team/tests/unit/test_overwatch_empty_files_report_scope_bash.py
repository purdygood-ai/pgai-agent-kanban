"""
test_overwatch_empty_files_report_scope_bash.py
================================================
Behavioral unit tests for check-empty-files.sh: the scan must quarantine
only report files (bug and priority reports), never protocol machinery such
as the claim-lock sentinels and claim ledger the bug-id claim protocol
creates, and its bounded residue prune must touch only claim-lock-shaped
".empty.orphan" leftovers.

Fixtures exercised:
  (A) An empty ".claim-NNNN.lock" claim-lock sentinel in bugs/: the scan
      leaves it present and unrenamed, and files no report.
  (B) An empty "BUG-NNNN-<slug>.md" report in bugs/: the scan still renames
      it to <name>.empty.orphan, logs the action, and files one report --
      the behavior the check exists for is unchanged.
  (C) An empty dotfile that is not the claim-lock shape (the claim ledger
      ".bug-claims.log"): the scan leaves it present and unrenamed.
  (D) Pre-existing ".claim-NNNN.lock.empty.orphan" residue from a prior,
      unscoped firing: the scan prunes it (deletes it, logs the prune), while
      a pre-existing, non-claim-lock ".empty.orphan" file is left in place as
      evidence.

All tests run against synthetic fixtures and never touch the live kanban root.
"""

from __future__ import annotations

import pathlib
import textwrap

from tests.unit.shell_harness import run_bash


# ---------------------------------------------------------------------------
# Path to the check module (relative to the team/ cwd used by run-unit-tests.sh)
# ---------------------------------------------------------------------------
_CHECK_EMPTY_FILES = "scripts/lib/overwatch-checks/check-empty-files.sh"


# ---------------------------------------------------------------------------
# Kanban fixture builder
# ---------------------------------------------------------------------------

def _build_kanban(kanban_root: pathlib.Path, project_name: str) -> pathlib.Path:
    """Build a minimal synthetic kanban root with one registered project,
    a bugs/ directory, and a priority/ directory.

    Returns the project directory path.
    """
    proj_dir = kanban_root / "projects" / project_name
    (proj_dir / "overwatch" / "backups").mkdir(parents=True)
    (proj_dir / "overwatch" / "actions.log").write_text("", encoding="utf-8")
    (proj_dir / "bugs").mkdir(parents=True)
    (proj_dir / "priority").mkdir(parents=True)
    return proj_dir


def _action_log(proj_dir: pathlib.Path) -> str:
    """Read the overwatch action log for the project."""
    log_path = proj_dir / "overwatch" / "actions.log"
    if log_path.exists():
        return log_path.read_text(encoding="utf-8")
    return ""


def _stub_header(proj_dir: pathlib.Path) -> str:
    """Return bash stubs for overwatch_backup_file, overwatch_log_action, and
    overwatch_halt_first_fix.

    Sources the REAL overwatch_protocol.sh first (so any helper the check
    relies on beyond these three is genuine production code), then overrides
    the three protocol functions the test wants to intercept with lightweight
    stubs. overwatch_halt_first_fix calls its argument function directly
    (skipping the HALT/lock machinery, which is overwatch_protocol.sh's own
    concern, tested elsewhere) so a "live run" here actually exercises
    check-empty-files.sh's own rename/prune logic. Order matters: sourcing
    after the overrides would clobber the stubs with the real implementations.
    """
    return textwrap.dedent(f"""\
        source scripts/lib/overwatch_protocol.sh
        overwatch_log_action() {{
            local name="$1" target="$2" action="$3" backup="$4" reason="$5"
            printf '%s\\t%s\\t%s\\t%s\\n' "${{name}}" "${{action}}" "${{target}}" "${{reason}}" \
                >> "{proj_dir}/overwatch/actions.log"
        }}
        overwatch_backup_file() {{
            local src="$1"
            local ts
            ts="$(date -u +%Y%m%dT%H%M%SZ)"
            local dest="{proj_dir}/overwatch/backups/${{ts}}_$(basename "${{src}}")"
            cp -r "${{src}}" "${{dest}}" 2>/dev/null || true
            echo "${{dest}}"
        }}
        overwatch_halt_first_fix() {{
            local fn="$1"
            "${{fn}}"
        }}
    """)


# ---------------------------------------------------------------------------
# (A) Empty ".claim-NNNN.lock" sentinel survives the scan, no report filed.
# ---------------------------------------------------------------------------


def test_claim_lock_sentinel_survives_scan(tmp_path: pathlib.Path) -> None:
    """
    Fixture (A): an empty ".claim-0007.lock" claim-lock sentinel sits in
    bugs/ alongside an in-flight claim.

    Expected: the file is left present and unrenamed, and no bug report is
    filed for it.
    """
    kanban_root = tmp_path / "kanban"
    project_name = "test-project-a"
    proj_dir = _build_kanban(kanban_root, project_name)

    lock_file = proj_dir / "bugs" / ".claim-0007.lock"
    lock_file.write_text("", encoding="utf-8")

    script = _stub_header(proj_dir) + textwrap.dedent(f"""\
        set -euo pipefail
        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"
        source {_CHECK_EMPTY_FILES}
        overwatch_check_empty_files
    """)

    result = run_bash(tmp_path, script)
    assert result.returncode == 0, (
        f"check-empty-files should exit 0\nstderr={result.stderr}"
    )

    assert lock_file.exists(), (
        f"claim-lock sentinel must survive the scan unrenamed\nstderr={result.stderr}"
    )
    assert not (proj_dir / "bugs" / ".claim-0007.lock.empty.orphan").exists(), (
        "claim-lock sentinel must not be renamed to .empty.orphan"
    )

    filed_reports = list((proj_dir / "bugs").glob("BUG-*.md"))
    assert filed_reports == [], (
        f"no bug report should be filed for a claim-lock sentinel, got: {filed_reports}"
    )


# ---------------------------------------------------------------------------
# (B) Empty "BUG-NNNN-<slug>.md" report is still quarantined (unchanged
#     behavior: the check exists to catch exactly this case).
# ---------------------------------------------------------------------------


def test_empty_bug_report_still_quarantined(tmp_path: pathlib.Path) -> None:
    """
    Fixture (B): an empty "BUG-NNNN-something.md"-shaped report sits in
    bugs/.

    Expected: the file is renamed to <name>.empty.orphan, the rename is
    logged, and one consolidated bug report is filed.
    """
    kanban_root = tmp_path / "kanban"
    project_name = "test-project-b"
    proj_dir = _build_kanban(kanban_root, project_name)

    report = proj_dir / "bugs" / "BUG-0001-something.md"
    report.write_text("", encoding="utf-8")

    script = _stub_header(proj_dir) + textwrap.dedent(f"""\
        set -euo pipefail
        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"
        source {_CHECK_EMPTY_FILES}
        overwatch_check_empty_files
    """)

    result = run_bash(tmp_path, script)
    assert result.returncode == 0, (
        f"check-empty-files should exit 0\nstderr={result.stderr}"
    )

    assert not report.exists(), "the original empty report should be renamed away"
    orphan = proj_dir / "bugs" / "BUG-0001-something.md.empty.orphan"
    assert orphan.exists(), (
        f"empty report should be renamed to {orphan}\nstderr={result.stderr}"
    )

    log = _action_log(proj_dir)
    assert "renamed-to-empty-orphan" in log, (
        f"Expected 'renamed-to-empty-orphan' in action log, got:\n{log}\nstderr={result.stderr}"
    )

    filed_reports = list((proj_dir / "bugs").glob("BUG-*-auto-empty-files-*.md"))
    assert len(filed_reports) == 1, (
        f"Expected exactly one consolidated bug report filed, got: {filed_reports}"
    )


# ---------------------------------------------------------------------------
# (C) Empty dotfile that is NOT the claim-lock shape (the claim ledger)
#     survives the scan too.
# ---------------------------------------------------------------------------


def test_claim_ledger_dotfile_not_quarantined(tmp_path: pathlib.Path) -> None:
    """
    Fixture (C): a fresh, empty ".bug-claims.log" claim ledger sits in bugs/
    (the durable provenance ledger claim_next_bug_id appends to; empty on a
    fresh project before any claim has been recorded).

    Expected: the file is left present and unrenamed.
    """
    kanban_root = tmp_path / "kanban"
    project_name = "test-project-c"
    proj_dir = _build_kanban(kanban_root, project_name)

    ledger = proj_dir / "bugs" / ".bug-claims.log"
    ledger.write_text("", encoding="utf-8")

    script = _stub_header(proj_dir) + textwrap.dedent(f"""\
        set -euo pipefail
        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"
        source {_CHECK_EMPTY_FILES}
        overwatch_check_empty_files
    """)

    result = run_bash(tmp_path, script)
    assert result.returncode == 0, (
        f"check-empty-files should exit 0\nstderr={result.stderr}"
    )

    assert ledger.exists(), (
        f"claim ledger dotfile must survive the scan unrenamed\nstderr={result.stderr}"
    )
    assert not (proj_dir / "bugs" / ".bug-claims.log.empty.orphan").exists(), (
        "claim ledger dotfile must not be renamed to .empty.orphan"
    )


# ---------------------------------------------------------------------------
# (D) Bounded prune: pre-existing claim-lock residue is pruned; a
#     non-claim-lock .empty.orphan file is left in place as evidence.
# ---------------------------------------------------------------------------


def test_claim_lock_residue_pruned_scope_bounded(tmp_path: pathlib.Path) -> None:
    """
    Fixture (D): a ".claim-0009.lock.empty.orphan" residue file (left by a
    prior, unscoped firing of this check) sits in bugs/ alongside an
    unrelated "BUG-NNNN-something.md.empty.orphan"-shaped evidence file.

    Expected: the claim-lock residue is pruned (deleted) and the prune is
    logged; the unrelated .empty.orphan evidence file is left untouched.
    """
    kanban_root = tmp_path / "kanban"
    project_name = "test-project-d"
    proj_dir = _build_kanban(kanban_root, project_name)

    residue = proj_dir / "bugs" / ".claim-0009.lock.empty.orphan"
    residue.write_text("", encoding="utf-8")

    evidence = proj_dir / "bugs" / "BUG-0005-something.md.empty.orphan"
    evidence.write_text("", encoding="utf-8")

    script = _stub_header(proj_dir) + textwrap.dedent(f"""\
        set -euo pipefail
        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"
        source {_CHECK_EMPTY_FILES}
        overwatch_check_empty_files
    """)

    result = run_bash(tmp_path, script)
    assert result.returncode == 0, (
        f"check-empty-files should exit 0\nstderr={result.stderr}"
    )

    assert not residue.exists(), (
        f"claim-lock residue should have been pruned\nstderr={result.stderr}"
    )
    assert evidence.exists(), (
        "a non-claim-lock .empty.orphan file must be left in place as evidence"
    )

    log = _action_log(proj_dir)
    assert "pruned-claim-lock-residue" in log, (
        f"Expected 'pruned-claim-lock-residue' in action log, got:\n{log}\nstderr={result.stderr}"
    )
