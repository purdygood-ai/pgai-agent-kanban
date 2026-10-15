"""
test_ship_policy_halt_row_on_always_lane.py
===========================================
OVERWATCH's resolution matrix has one row, for trigger class
"ship-policy-halt": a project HALT file whose header says CM wrote it
(pgai_agent_kanban.overwatch.resolution_matrix._fact_trigger_class). On a
lane whose ship posture is "always", the release ships through triggers 2,
3 and 8 instead of halting, so none of those three stops ever leaves a HALT
file for that row to classify. The posture adds no matrix row and changes
no existing one.

Each fixture runs the real ship-policy stage (cm_release_apply_ship_policy,
Steps 4c-4f) against a sandbox lane (tests/fixtures/ship_policy_sandbox.py):

  - under the matrix posture it halts with the trigger's error key and
    writes a project HALT file that OVERWATCH classifies as
    "ship-policy-halt" -- proof that the fixture really reaches the stop
    the row is about;
  - under always it ships, writes no HALT file in the project or at the
    kanban root, and the trigger class OVERWATCH would read for the lane is
    "unknown", which no matrix row matches.

The row only matches CM's HALT header, so an always lane can still raise
trigger 1 and the mechanical HALTs (4-11) for it to classify; those are out
of this invariant and covered as unchanged by the ship-posture tests.

The shipped resolution-matrix.yaml is checked to declare exactly its one
existing row.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from pgai_agent_kanban.overwatch.resolution_matrix import (
    CANCEL_AND_REBUNDLE,
    BlockEvidence,
    MatrixRow,
    extract_facts,
    load_matrix,
)
from tests.fixtures.binary_guard import require_binary
from tests.fixtures.ship_policy_sandbox import KEY_T2, KEY_T3, KEY_T8, build_lane, run_ship_policy

_SHIPPED_MATRIX = (
    pathlib.Path(__file__).resolve().parents[2] / "pgai_agent_kanban" / "overwatch" / "resolution-matrix.yaml"
)

_TRIGGER_FIXTURES = {"trigger-2": KEY_T2, "trigger-3": KEY_T3, "trigger-8": KEY_T8}


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH."""
    require_binary("git", label="ship-policy-halt-row-on-always-lane")


def _trigger_class(halt_file: pathlib.Path) -> str:
    """The trigger-class fact OVERWATCH extracts for a block at *halt_file*.
    The retry ceiling is given explicitly; it plays no part in this fact."""
    evidence = BlockEvidence(halt_file_path=str(halt_file), max_rc_retries=3)
    return extract_facts(evidence).trigger_class


def _diag(result) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


@pytest.mark.parametrize("fixture", sorted(_TRIGGER_FIXTURES))
def test_matrix_lane_leaves_a_ship_policy_halt_for_the_row(tmp_path, fixture):
    lane = build_lane(tmp_path, fixture, "matrix")

    result = run_ship_policy(tmp_path, lane)

    assert result.returncode != 0, _diag(result)
    assert lane.halt_file.is_file(), _diag(result)
    halt = lane.halt_file.read_text(encoding="utf-8")
    assert re.search(rf"error:\s+{re.escape(_TRIGGER_FIXTURES[fixture])}\b", halt), halt
    assert _trigger_class(lane.halt_file) == "ship-policy-halt"


@pytest.mark.parametrize("fixture", sorted(_TRIGGER_FIXTURES))
def test_always_lane_leaves_no_trigger_2_3_or_8_halt_for_the_row(tmp_path, fixture):
    lane = build_lane(tmp_path, fixture, "always")

    result = run_ship_policy(tmp_path, lane, 'echo "decision=$_SHIP_DECISION"')

    assert result.returncode == 0, _diag(result)
    assert not lane.halt_file.exists(), f"an always lane must not halt on {fixture}:\n{_diag(result)}"
    assert not (lane.root / "HALT").exists(), "a release never writes the fleet-scoped HALT"
    assert re.search(r"^decision=SHIP-", result.stdout, re.MULTILINE), _diag(result)
    assert _trigger_class(lane.halt_file) == "unknown"
    rows = load_matrix(matrix_path=str(_SHIPPED_MATRIX))
    assert all(row.trigger_class != "unknown" for row in rows), "no row may match a lane with no HALT"


def test_shipped_resolution_matrix_declares_exactly_its_existing_row():
    assert load_matrix(matrix_path=str(_SHIPPED_MATRIX)) == [
        MatrixRow(
            trigger_class="ship-policy-halt",
            findings_all_filed=True,
            shipped_or_tagged=False,
            approval_gate_present=False,
            operator_commits_in_lineage=False,
            attempts_below_ceiling=True,
            resolution=CANCEL_AND_REBUNDLE,
        )
    ]
