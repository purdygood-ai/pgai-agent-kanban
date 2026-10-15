"""
test_overwatch_wake_honours_halt.py
===================================
The overwatch wake keeps observing read-only and changes nothing while a
HALT is present, driven through the REAL production entry point
(``bash scripts/wake/claude.sh --agent=overwatch``) against a self-contained
kanban root built by the shared wake-driven fixture builders.

Coverage:
  - The fleet-wide $KANBAN_ROOT/HALT: the wake exits 0 at its startup HALT
    check, narrating the deferral dual-channel, and runs no block triage
    (the BLOCKED task carrying an always-human Block Record is byte-for-byte
    unchanged, no triage log or action-log row is written), no resolution
    stage and no task processing for any project.
  - An operator's project HALT: the wake still enters the project's chain
    (the resolution stage classifies the HALT), but block triage is skipped
    with a "fix deferred: HALT present" narration and the task is unchanged.

The no-HALT case (triage escalates the same task) is
test_overwatch_block_triage_wake_driven.py. No real provider is invoked:
every agent backlog the builder seeds is empty, so the wake dispatches
nothing. All paths live under pytest's tmp_path.
"""

from __future__ import annotations

import pathlib

from tests.integration.test_overwatch_block_triage_wake_driven import _build
from tests.integration.test_overwatch_wake_driven_fixtures import _action_log, _run_wake

_OPERATOR_HALT = "# operator full-stop for a hand patch\n"


def _triage_log(kanban_root: pathlib.Path) -> pathlib.Path:
    return kanban_root / "logs" / "overwatch-block-triage.log"


def _stage_log(kanban_root: pathlib.Path) -> pathlib.Path:
    return kanban_root / "logs" / "overwatch-resolution-stage.log"


def test_fleet_halt_stops_the_overwatch_wake_before_any_project_is_touched(tmp_path: pathlib.Path) -> None:
    kanban_root, proj, status = _build(tmp_path, "wd_fleet_halt")
    halt = kanban_root / "HALT"
    halt.write_text(_OPERATOR_HALT, encoding="utf-8")
    before = status.read_bytes()

    result = _run_wake(kanban_root, "overwatch")

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert f"HALT flag detected at {halt}" in result.stderr, result.stderr
    assert "error:   wake.overwatch_fix_deferred_halt_present" in result.stderr, result.stderr
    assert "fix deferred: HALT present" in result.stderr
    assert "beginning chain" not in result.stderr, "no project's chain may start under the fleet HALT"
    assert status.read_bytes() == before
    assert not _triage_log(kanban_root).exists()
    assert not _stage_log(kanban_root).exists()
    assert "block-triage" not in _action_log(proj)
    assert halt.read_text(encoding="utf-8") == _OPERATOR_HALT


def test_project_halt_skips_block_triage_for_that_project(tmp_path: pathlib.Path) -> None:
    kanban_root, proj, status = _build(tmp_path, "wd_project_halt")
    halt = proj / "HALT"
    halt.write_text(_OPERATOR_HALT, encoding="utf-8")
    before = status.read_bytes()

    result = _run_wake(kanban_root, "overwatch")

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert f"block triage skipped -- HALT present at {halt}" in result.stderr, result.stderr
    assert "error:   wake.overwatch_block_triage_deferred_halt_present" in result.stderr, result.stderr
    assert status.read_bytes() == before
    assert not _triage_log(kanban_root).exists()
    assert "block-triage" not in _action_log(proj)
    assert "resolution stage evaluated, skipping task processing" in result.stderr, result.stderr
    assert halt.read_text(encoding="utf-8") == _OPERATOR_HALT
