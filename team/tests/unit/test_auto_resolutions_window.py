"""
test_auto_resolutions_window.py
================================
Behavioral unit tests for the human-review window's Auto-Resolutions pane
(v2.0.0 Goal 6: "The surface"):
  team/pgai_agent_kanban/dashboard/scan_auto_resolutions.py

Covers the task's acceptance criteria:

  1. Auto-Resolutions section lists RC, attempt N/M, decision, outcome,
     and evidence links.
  2. Empty state renders 'no auto-resolutions'.
  3. Section is read-only (no action affordances such as close.sh/
     wontdo.sh/scripts referenced anywhere in the output).
  4. Multi-project aggregation and per-project retry-ceiling resolution
     (including graceful "?" degradation when the ceiling config is
     absent — this is a read-only display, not the resolver itself, so
     it must not raise).

All filesystem operations use tmp_path (pytest-managed). No live kanban
root, no subprocesses. Function names describe behaviour under test, never
bug/task IDs (SOP.md "Test Authoring Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import pathlib
import sys

import pytest

# ---------------------------------------------------------------------------
# Ensure the package root is importable regardless of cwd.
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/tests/unit/ -> team/
_PKG_ROOT = _TEAM_DIR  # pgai_agent_kanban/ lives directly under team/
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from pgai_agent_kanban.dashboard.scan_auto_resolutions import (
    _collect_auto_resolutions,
    collect_auto_resolutions,
    scan_auto_resolutions,
)
from pgai_agent_kanban.overwatch.ledger import LedgerEntry, append_entry, ledger_path_for_project


# ---------------------------------------------------------------------------
# Fixture builder helpers
# ---------------------------------------------------------------------------


def _write_ceiling_cfg(kanban_root: pathlib.Path, max_rc_retries: int = 3) -> None:
    """Write a kanban.cfg with [release] max_rc_retries so the ceiling resolves."""
    (kanban_root / "kanban.cfg").write_text(
        f"[release]\nmax_rc_retries = {max_rc_retries}\n",
        encoding="utf-8",
    )


def _append_resolution(
    kanban_root: pathlib.Path,
    project: str,
    rc: str = "v2.0.1",
    attempt_from: int = 1,
    attempt_to: int = 2,
    trigger: str = "ship-policy-halt",
    matrix_row: str = "CANCEL-AND-REBUNDLE",
    findings: list | None = None,
    evidence_paths: list | None = None,
    outcome: str | None = None,
    timestamp: str = "2026-07-27T18:03:00Z",
) -> str:
    """Append one ledger entry for *project* and return its entry_id."""
    (kanban_root / "projects" / project).mkdir(parents=True, exist_ok=True)
    ledger_path = ledger_path_for_project(str(kanban_root), project)
    entry = LedgerEntry(
        timestamp=timestamp,
        rc=rc,
        attempt_from=attempt_from,
        attempt_to=attempt_to,
        trigger=trigger,
        matrix_row=matrix_row,
        findings=findings or [],
        evidence_paths=evidence_paths or [],
        outcome=outcome,
    )
    return append_entry(ledger_path, entry)


# ---------------------------------------------------------------------------
# scan_auto_resolutions — empty state
# ---------------------------------------------------------------------------


def test_empty_state_prints_no_auto_resolutions(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """No ledger anywhere: renders the empty-state message."""
    (tmp_path / "projects").mkdir()
    scan_auto_resolutions(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "no auto-resolutions" in captured.out


def test_empty_state_when_projects_dir_absent(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """No projects/ directory at all: still renders the empty-state message,
    never a traceback."""
    scan_auto_resolutions(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "no auto-resolutions" in captured.out


def test_project_with_no_ledger_entries_yet_contributes_no_rows(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """A registered project directory with no auto-resolutions.md yet:
    empty-state message, no crash."""
    (tmp_path / "projects" / "proj-alpha").mkdir(parents=True)
    scan_auto_resolutions(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "no auto-resolutions" in captured.out


# ---------------------------------------------------------------------------
# scan_auto_resolutions — populated: RC, attempt N/M, decision, outcome, links
# ---------------------------------------------------------------------------


def test_single_entry_renders_rc_attempt_and_decision(tmp_path: pathlib.Path, capsys) -> None:
    """A single ledger entry (default fixture: rc=v2.0.1, attempt 1/3,
    decision CANCEL-AND-REBUNDLE) renders its RC, its "attempt N/M" label
    (N = attempt_from, M = the project's configured retry ceiling), and its
    decision from one render.  These three checks previously lived in three
    separate tests that each rebuilt the same default fixture; folded into
    one test since they assert on the same render's output.
    """
    _write_ceiling_cfg(tmp_path, max_rc_retries=3)
    _append_resolution(
        tmp_path, "proj-alpha",
        rc="v2.0.1", attempt_from=1, attempt_to=2,
        matrix_row="CANCEL-AND-REBUNDLE",
    )
    scan_auto_resolutions(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "v2.0.1" in captured.out
    assert "1/3" in captured.out
    assert "CANCEL-AND-REBUNDLE" in captured.out


def test_pending_outcome_renders_as_pending(tmp_path: pathlib.Path, capsys) -> None:
    """An entry whose Outcome has not been backfilled yet renders 'pending'."""
    _write_ceiling_cfg(tmp_path)
    _append_resolution(tmp_path, "proj-alpha", outcome=None)
    scan_auto_resolutions(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "pending" in captured.out


def test_backfilled_outcome_renders_verbatim(tmp_path: pathlib.Path, capsys) -> None:
    """A backfilled terminal Outcome (e.g. 'SHIPPED attempt 2') renders verbatim."""
    _write_ceiling_cfg(tmp_path)
    ledger_path = ledger_path_for_project(str(tmp_path), "proj-alpha")
    (tmp_path / "projects" / "proj-alpha").mkdir(parents=True)
    entry = LedgerEntry(
        timestamp="2026-07-27T18:03:00Z",
        rc="v2.0.1",
        attempt_from=1,
        attempt_to=2,
        trigger="ship-policy-halt",
        matrix_row="CANCEL-AND-REBUNDLE",
        outcome="SHIPPED attempt 2",
    )
    append_entry(ledger_path, entry)
    scan_auto_resolutions(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "SHIPPED attempt 2" in captured.out


def test_single_entry_renders_evidence_paths(tmp_path: pathlib.Path, capsys) -> None:
    _write_ceiling_cfg(tmp_path)
    _append_resolution(
        tmp_path,
        "proj-alpha",
        evidence_paths=["/kanban/projects/proj-alpha/tasks/CM-x/artifacts/report.md"],
    )
    scan_auto_resolutions(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "/kanban/projects/proj-alpha/tasks/CM-x/artifacts/report.md" in captured.out


def test_single_entry_renders_ledger_path_as_a_link(tmp_path: pathlib.Path, capsys) -> None:
    """The ledger file path (evidence link to the resolution decision itself)
    appears in the output alongside the entry id."""
    _write_ceiling_cfg(tmp_path)
    entry_id = _append_resolution(tmp_path, "proj-alpha")
    scan_auto_resolutions(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    ledger_path = ledger_path_for_project(str(tmp_path), "proj-alpha")
    assert ledger_path in captured.out
    assert entry_id in captured.out


def test_no_evidence_paths_renders_none(tmp_path: pathlib.Path, capsys) -> None:
    _write_ceiling_cfg(tmp_path)
    _append_resolution(tmp_path, "proj-alpha", evidence_paths=[])
    scan_auto_resolutions(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "Evidence: none" in captured.out


# ---------------------------------------------------------------------------
# scan_auto_resolutions — read-only: no action affordances
# ---------------------------------------------------------------------------


def test_output_contains_no_action_commands(tmp_path: pathlib.Path, capsys) -> None:
    """The surface never prints an approve/reject/close/wontdo affordance —
    it informs, it never mutates state (v2.0.0 Goal 6 constraint)."""
    _write_ceiling_cfg(tmp_path)
    _append_resolution(tmp_path, "proj-alpha")
    scan_auto_resolutions(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "close.sh" not in captured.out
    assert "wontdo.sh" not in captured.out
    assert "Approve" not in captured.out
    assert "Reject" not in captured.out


# ---------------------------------------------------------------------------
# scan_auto_resolutions — multi-project aggregation
# ---------------------------------------------------------------------------


def test_two_projects_both_listed(tmp_path: pathlib.Path, capsys) -> None:
    _write_ceiling_cfg(tmp_path)
    _append_resolution(tmp_path, "proj-alpha", rc="v1.0.1")
    _append_resolution(tmp_path, "proj-beta", rc="v2.0.1")
    scan_auto_resolutions(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "v1.0.1" in captured.out
    assert "v2.0.1" in captured.out
    assert "proj-alpha" in captured.out
    assert "proj-beta" in captured.out


def test_per_project_ceiling_override_used_in_attempt_display(
    tmp_path: pathlib.Path, capsys,
) -> None:
    """A project.cfg [release] max_rc_retries override wins over the
    kanban.cfg default for that project's attempt display."""
    _write_ceiling_cfg(tmp_path, max_rc_retries=3)
    (tmp_path / "projects" / "proj-alpha").mkdir(parents=True)
    (tmp_path / "projects" / "proj-alpha" / "project.cfg").write_text(
        "[project]\nproject_name = proj-alpha\n\n[release]\nmax_rc_retries = 1\n",
        encoding="utf-8",
    )
    _append_resolution(tmp_path, "proj-alpha", attempt_from=1, attempt_to=2)
    scan_auto_resolutions(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "1/1" in captured.out


# ---------------------------------------------------------------------------
# scan_auto_resolutions — graceful ceiling degradation
# ---------------------------------------------------------------------------


def test_missing_ceiling_config_renders_question_mark_instead_of_crashing(
    tmp_path: pathlib.Path, capsys,
) -> None:
    """No kanban.cfg and no project.cfg [release] max_rc_retries anywhere:
    the display degrades to 'N/?' rather than raising CeilingConfigError.
    This is a read-only display, not the resolver — it must never crash the
    pane just because config is incomplete."""
    _append_resolution(tmp_path, "proj-alpha", attempt_from=2, attempt_to=3)
    scan_auto_resolutions(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "2/?" in captured.out


# ---------------------------------------------------------------------------
# _collect_auto_resolutions / collect_auto_resolutions — structural tests
# ---------------------------------------------------------------------------


def test_collect_returns_correct_fields(tmp_path: pathlib.Path) -> None:
    _write_ceiling_cfg(tmp_path, max_rc_retries=3)
    _append_resolution(
        tmp_path,
        "proj-alpha",
        rc="v2.0.1",
        attempt_from=1,
        attempt_to=2,
        matrix_row="CANCEL-AND-REBUNDLE",
        findings=["BUG-0041-foo.md"],
        evidence_paths=["/kanban/x/report.md"],
    )
    results = _collect_auto_resolutions(tmp_path)
    assert len(results) == 1
    r = results[0]
    assert r["project"] == "proj-alpha"
    assert r["rc"] == "v2.0.1"
    assert r["attempt_from"] == 1
    assert r["attempt_to"] == 2
    assert r["attempt_display"] == "1/3"
    assert r["decision"] == "CANCEL-AND-REBUNDLE"
    assert r["outcome"] == "pending"
    assert r["findings"] == ["BUG-0041-foo.md"]
    assert r["evidence_paths"] == ["/kanban/x/report.md"]


def test_collect_orders_newest_first_within_a_project(tmp_path: pathlib.Path) -> None:
    """Multiple entries for the same project render newest-first (matches
    the "recent tags, newest first" dashboard convention elsewhere)."""
    _write_ceiling_cfg(tmp_path)
    _append_resolution(
        tmp_path, "proj-alpha", rc="v1.0.1",
        attempt_from=1, attempt_to=2, timestamp="2026-07-27T10:00:00Z",
    )
    _append_resolution(
        tmp_path, "proj-alpha", rc="v1.0.2",
        attempt_from=1, attempt_to=2, timestamp="2026-07-27T11:00:00Z",
    )
    results = _collect_auto_resolutions(tmp_path)
    assert [r["rc"] for r in results] == ["v1.0.2", "v1.0.1"]


def test_collect_auto_resolutions_filters_by_project(tmp_path: pathlib.Path) -> None:
    _write_ceiling_cfg(tmp_path)
    _append_resolution(tmp_path, "proj-alpha", rc="v1.0.1")
    _append_resolution(tmp_path, "proj-beta", rc="v2.0.1")
    results = collect_auto_resolutions(str(tmp_path), project="proj-beta")
    assert len(results) == 1
    assert results[0]["project"] == "proj-beta"
    assert results[0]["rc"] == "v2.0.1"


def test_collect_auto_resolutions_empty_list_when_nothing_recorded(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "projects").mkdir()
    results = collect_auto_resolutions(str(tmp_path))
    assert results == []
