"""
test_wake_blocked_ledger.py
============================
Unit tests for team/scripts/lib/wake_blocked_ledger.sh — the thin bash
adapter over pgai_agent_kanban.metrics.blocked_ledger that the wake path's
HALT-skip sites in wake_common.sh call.

These tests source the real library files (project_paths.sh, projects.sh,
discovery.sh, wake_blocked_ledger.sh) against a synthetic kanban root and
call pgai_stamp_halt_boundary / pgai_stamp_fleet_halt_boundary directly,
exactly as wake_common.sh's own HALT checks do -- see
docs/test-authoring-guidelines.md ("the test must invoke that script").
log() is a real capturing function (echoes a tagged line to stdout), not a
no-op stub -- the tests that claim "silent" assert the tag never appears
in stdout, and the genuine-failure test asserts it appears exactly once.

Coverage:
  - A block-start then a matching block-end for a project with an active
    RC round-trips through the real ledger file.
  - Calling "start" a second time before the matching "end" is a silent
    no-op (the ledger's own duplicate-open refusal, swallowed, producing
    no wake-log output) -- this is what makes "first cycle it appears"
    hold without a second state machine in the adapter.
  - Calling "end" with no open interval is likewise a silent no-op,
    producing no wake-log output -- what makes "first cycle it's gone"
    hold the same way.
  - A project whose Active RC is "none" gets no ledger file at all.
  - pgai_stamp_fleet_halt_boundary fans out to every project in
    projects_cfg_list, stamping only the ones with an active RC.
  - An unwritable metrics directory never raises out of the bash function
    -- the caller (a wake cycle) keeps its own exit code -- and, unlike
    the designed no-ops above, this genuine write failure still produces
    exactly one wake-log line naming the project and the source.

All tests use tmp_path via the shared run_bash harness; no live filesystem
paths, no bare /tmp. Function names describe behaviour under test, never
bug/task IDs (roles/TESTER.md "Test Authoring Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from tests.unit.shell_harness import run_bash

# log() writes a tagged line to stdout (rather than discarding, as a stub
# would) so a test can observe exactly what the wake log would have shown --
# the "silent no-op" tests below assert this tag never appears, and the
# unwritable-directory test asserts it appears exactly once. The tag keeps
# an assertion from accidentally matching unrelated stdout (there is none
# today, since the ledger CLI's own stdout is redirected to /dev/null by
# the adapter, but the tag makes that independent of that redirection).
_LOG_TAG = "WAKE-LOG:"

_LIB_SOURCE_PREAMBLE = """
set -euo pipefail
export KANBAN_ROOT="{kanban_root}"
export TEAM_ROOT="{kanban_root}"
source scripts/lib/project_paths.sh
source scripts/lib/projects.sh
source scripts/lib/discovery.sh
source scripts/lib/pp_run_ops.sh
log() {{ echo "%s $*"; }}
source scripts/lib/wake_blocked_ledger.sh
""" % _LOG_TAG


def _write_release_state(project_dir: Path, active_rc: str = "none") -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "release-state.md").write_text(
        "# Release State\n\n"
        "## Active RC\n"
        f"{active_rc}\n\n"
        "## RC Opened At\n"
        "2026-09-18T00:00:00+00:00\n\n"
        "## RC Opened By Task\n"
        "none\n",
        encoding="utf-8",
    )


def _write_projects_cfg(kanban_root: Path, project_names: list[str]) -> None:
    lines = []
    for i, name in enumerate(project_names, start=1):
        lines.append(f"[project:{name}]\npriority={i}\n\n")
    (kanban_root / "projects.cfg").write_text("".join(lines), encoding="utf-8")


def _ledger_lines(kanban_root: Path, project: str) -> list[dict]:
    ledger_path = kanban_root / "projects" / project / "metrics" / "blocked-intervals.jsonl"
    if not ledger_path.is_file():
        return []
    text = ledger_path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _make_root(tmp_path: Path, projects: dict[str, str]) -> Path:
    """Build a synthetic kanban root. *projects* maps name -> active_rc."""
    root = tmp_path / "kanban"
    for name, active_rc in projects.items():
        _write_release_state(root / "projects" / name, active_rc=active_rc)
    _write_projects_cfg(root, list(projects.keys()))
    return root


# ---------------------------------------------------------------------------
# pgai_stamp_halt_boundary -- single project
# ---------------------------------------------------------------------------


def test_start_then_end_round_trips_through_the_real_ledger(tmp_path: Path) -> None:
    root = _make_root(tmp_path, {"demo": "v1.2.3"})
    script = _LIB_SOURCE_PREAMBLE.format(kanban_root=root) + (
        'pgai_stamp_halt_boundary demo "halt:project" start\n'
        'pgai_stamp_halt_boundary demo "halt:project" end\n'
    )
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, result.stderr

    records = _ledger_lines(root, "demo")
    assert [r["kind"] for r in records] == ["block-start", "block-end"]
    assert all(r["version"] == "v1.2.3" for r in records)
    assert all(r["source"] == "halt:project" for r in records)


def test_repeated_start_before_matching_end_is_a_silent_no_op(tmp_path: Path) -> None:
    root = _make_root(tmp_path, {"demo": "v1.2.3"})
    script = _LIB_SOURCE_PREAMBLE.format(kanban_root=root) + (
        'pgai_stamp_halt_boundary demo "halt:project" start\n'
        'pgai_stamp_halt_boundary demo "halt:project" start\n'
        'pgai_stamp_halt_boundary demo "halt:project" start\n'
    )
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, result.stderr

    records = _ledger_lines(root, "demo")
    # Exactly one block-start recorded, no matter how many wake cycles
    # observe the HALT still present.
    assert [r["kind"] for r in records] == ["block-start"]
    # The second and third attempts found the boundary already in the
    # state they asked for (already blocked) -- the designed steady
    # state, which must produce no wake-log output at all.
    assert _LOG_TAG not in result.stdout, result.stdout


def test_end_with_no_open_interval_is_a_silent_no_op(tmp_path: Path) -> None:
    root = _make_root(tmp_path, {"demo": "v1.2.3"})
    script = _LIB_SOURCE_PREAMBLE.format(kanban_root=root) + (
        'pgai_stamp_halt_boundary demo "halt:project" end\n'
        'pgai_stamp_halt_boundary demo "halt:project" end\n'
    )
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, result.stderr
    assert _ledger_lines(root, "demo") == []
    # Both attempts found no open interval to close -- also the designed
    # steady state (an ordinary wake cycle with no HALT to close out), and
    # likewise must produce no wake-log output at all.
    assert _LOG_TAG not in result.stdout, result.stdout


def test_active_rc_none_writes_no_ledger_file_at_all(tmp_path: Path) -> None:
    root = _make_root(tmp_path, {"demo": "none"})
    script = _LIB_SOURCE_PREAMBLE.format(kanban_root=root) + (
        'pgai_stamp_halt_boundary demo "halt:project" start\n'
    )
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, result.stderr
    assert not (root / "projects" / "demo" / "metrics").exists()


# ---------------------------------------------------------------------------
# pgai_stamp_fleet_halt_boundary -- fleet fan-out
# ---------------------------------------------------------------------------


def test_fleet_boundary_stamps_only_projects_with_an_active_rc(tmp_path: Path) -> None:
    root = _make_root(tmp_path, {"has-rc": "v2.0.0", "no-rc": "none"})
    script = _LIB_SOURCE_PREAMBLE.format(kanban_root=root) + (
        'pgai_stamp_fleet_halt_boundary "halt:global" start\n'
    )
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, result.stderr

    has_rc_records = _ledger_lines(root, "has-rc")
    assert [r["kind"] for r in has_rc_records] == ["block-start"]
    assert has_rc_records[0]["source"] == "halt:global"
    assert not (root / "projects" / "no-rc" / "metrics").exists()


def test_fleet_boundary_pairs_start_and_end_across_two_calls(tmp_path: Path) -> None:
    root = _make_root(tmp_path, {"has-rc": "v2.0.0"})
    script = _LIB_SOURCE_PREAMBLE.format(kanban_root=root) + (
        'pgai_stamp_fleet_halt_boundary "halt:global" start\n'
        'pgai_stamp_fleet_halt_boundary "halt:global" end\n'
    )
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, result.stderr
    records = _ledger_lines(root, "has-rc")
    assert [r["kind"] for r in records] == ["block-start", "block-end"]


# ---------------------------------------------------------------------------
# Best-effort: an unwritable metrics directory never raises
# ---------------------------------------------------------------------------


def test_unwritable_metrics_directory_never_breaks_the_caller(tmp_path: Path) -> None:
    root = _make_root(tmp_path, {"demo": "v3.0.0"})
    metrics_dir = root / "projects" / "demo" / "metrics"
    metrics_dir.mkdir(parents=True)
    metrics_dir.chmod(0o000)
    try:
        script = _LIB_SOURCE_PREAMBLE.format(kanban_root=root) + (
            'pgai_stamp_halt_boundary demo "halt:project" start\n'
            'echo "CALLER_STILL_RUNNING"\n'
        )
        result = run_bash(tmp_path, script)
        # The adapter itself never aborts the caller: the script's own
        # exit code and its trailing echo both survive the failed stamp.
        assert result.returncode == 0, result.stderr
        assert "CALLER_STILL_RUNNING" in result.stdout
        # Unlike the designed no-op above, this is a genuine write failure
        # (an unwritable directory, not the boundary already being in the
        # requested state) -- it must still speak: exactly one wake-log
        # line, naming both the project and the source.
        log_lines = [line for line in result.stdout.splitlines() if _LOG_TAG in line]
        assert len(log_lines) == 1, result.stdout
        assert "demo" in log_lines[0]
        assert "halt:project" in log_lines[0]
    finally:
        metrics_dir.chmod(0o755)
