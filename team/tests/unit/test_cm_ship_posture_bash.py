"""
test_cm_ship_posture_bash.py
============================
Behavioral unit tests for team/scripts/lib/cm/release_ship_posture.sh -- how
the project's resolved ship posture changes CM's answer to a ship-policy
trigger that would otherwise HALT the release.

Each test sources the shipped libraries in a real bash subprocess and drives
the functions cm_release_apply_ship_policy calls from Steps 4c and 4d. The
posture is resolved through pp_ship_posture from a synthetic kanban root
under tmp_path, exactly as the release script resolves it; no live kanban
root, repository or release is involved.

Coverage:
  - Every ship-policy matrix row, with the ship_posture key absent and set
    to matrix (and set to always on an unconfirmed push_to_remote lane,
    which the remote-lane guard resolves to matrix), produces the decision,
    release-notes status and HALT error key the matrix produces without a
    posture, prints nothing, and records no Auto-Ship Event.
  - Under always, the rows that halt on trigger 2 or trigger 3 ship as
    SHIP-NON-FUNCTIONAL (notes status NON-FUNCTIONAL) and record exactly one
    event naming the trigger and its error key; every other row -- trigger
    1, the unrecognized-TESTER-state row and every ship row -- is unchanged.
  - The no-row-matched catch-all still halts under always.
  - The covered-trigger set is exactly 2, 3 and 8, and only under always.
  - The Step 4c pattern stop: under matrix the caller halts and no bug is
    filed; under always exactly one bug naming the pattern is filed, the
    event records it, and a second detection cites the open bug instead of
    filing another.
  - The Known Issues block for an auto-ship names the causing bugs by
    substance (never their internal IDs) and carries the non-functional
    warning; trigger 3 resolves the large-fix findings, not the others.
  - The Step 12 append writes one Auto-Ship Event block per recorded stop
    and a writer failure is a warning, never an exit.
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass

import pytest

from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_PROJECT = "posture-proj"
_VERSION = "v1.4.0"

_SOURCES = " && ".join(
    f"source {lib}"
    for lib in (
        "scripts/lib/project_paths.sh",
        "scripts/lib/pp_run_ops.sh",
        "scripts/lib/cm/release_known_issue.sh",
        "scripts/lib/cm/release_ship_policy.sh",
        "scripts/lib/cm/release_ship_posture.sh",
    )
)

_KEY_T1 = "cm-release.trigger-1.tester-blocked"
_KEY_T2 = "cm-release.trigger-2.systemic-risk-high"
_KEY_T3 = "cm-release.trigger-3.serious-concerns-large-fix"
_KEY_T8 = "cm-release.trigger-8.last-3-rcs-non-functional"
_KEY_UNRECOGNIZED = "cm-release.ship-policy.tester-state-unrecognized"
_KEY_UNMATCHED = "cm-release.ship-policy.halt"


@dataclass(frozen=True)
class MatrixRow:
    """One ship-policy matrix input and what the matrix decides for it.

    ``args`` are cm_ship_policy's seven positional arguments: tester_state,
    systemic_risk, recommendation, has_large_fix_effort, task_found,
    is_regression, integrity_damage.
    """

    id: str
    args: tuple
    decision: str
    notes_status: str
    halt_key: str = ""


# Every row the existing ship-policy fixtures exercise, with the matrix's
# decision, release-notes status and HALT error key.
_ROWS = [
    MatrixRow("blocked_high_pass", ("blocked", "high", "pass", "no", "yes", "no", "none"), "HALT", "", _KEY_T1),
    MatrixRow("blocked_low_concerns", ("blocked", "low", "ship-with-concerns", "no", "yes", "no", "none"), "HALT", "", _KEY_T1),
    MatrixRow("done_low_pass", ("done", "low", "pass", "no", "yes", "no", "none"), "SHIP-FUNCTIONAL", "FUNCTIONAL"),
    MatrixRow("done_medium_pass", ("done", "medium", "pass", "no", "yes", "no", "none"), "SHIP-FUNCTIONAL", "FUNCTIONAL"),
    MatrixRow("done_low_concerns", ("done", "low", "ship-with-concerns", "no", "yes", "no", "none"), "SHIP-KNOWN-BUGS", "KNOWN-BUGS"),
    MatrixRow(
        "done_serious_small_fix", ("done", "medium", "ship-with-serious-concerns", "no", "yes", "no", "none"),
        "SHIP-NON-FUNCTIONAL", "NON-FUNCTIONAL",
    ),
    MatrixRow(
        "done_serious_large_fix", ("done", "medium", "ship-with-serious-concerns", "yes", "yes", "no", "none"),
        "HALT", "", _KEY_T3,
    ),
    MatrixRow("no_tester_task", ("none", "none", "none", "no", "no", "no", "none"), "SHIP-FUNCTIONAL", "FUNCTIONAL"),
    MatrixRow("found_working", ("working", "none", "none", "no", "yes", "no", "none"), "HALT", "", _KEY_UNRECOGNIZED),
    MatrixRow("found_wont_do", ("wont-do", "none", "none", "no", "yes", "no", "none"), "HALT", "", _KEY_UNRECOGNIZED),
    MatrixRow("found_empty_state", ("", "none", "none", "no", "yes", "no", "none"), "HALT", "", _KEY_UNRECOGNIZED),
    MatrixRow(
        "high_regression", ("done", "high", "ship-with-serious-concerns", "no", "yes", "yes", "none"),
        "HALT", "", _KEY_T2,
    ),
    MatrixRow(
        "high_preexisting_small", ("done", "high", "ship-with-serious-concerns", "no", "yes", "no", "none"),
        "SHIP-KNOWN-ISSUE", "KNOWN-ISSUE",
    ),
    MatrixRow("high_integrity_breakage", ("done", "high", "pass", "no", "yes", "no", "breakage"), "HALT", "", _KEY_T2),
    MatrixRow("high_integrity_destructive", ("done", "high", "pass", "no", "yes", "no", "destructive"), "HALT", "", _KEY_T2),
    MatrixRow("high_large_fix", ("done", "high", "pass", "yes", "yes", "no", "none"), "HALT", "", _KEY_T2),
    MatrixRow("high_dimensions_unknown", ("done", "high", "pass", "no", "yes", "unknown", "unknown"), "HALT", "", _KEY_T2),
    MatrixRow(
        "high_regression_breakage_serious_large",
        ("done", "high", "ship-with-serious-concerns", "yes", "yes", "yes", "breakage"),
        "HALT", "", _KEY_T2,
    ),
    MatrixRow("unparseable_recommendation", ("done", "low", "unparseable", "no", "yes", "no", "none"), "SHIP-KNOWN-BUGS", "KNOWN-BUGS"),
    MatrixRow("unparseable_risk_pass", ("done", "unparseable", "pass", "no", "yes", "no", "none"), "SHIP-KNOWN-BUGS", "KNOWN-BUGS"),
    MatrixRow("absent_recommendation", ("done", "none", "none", "no", "yes", "no", "none"), "SHIP-KNOWN-BUGS", "KNOWN-BUGS"),
]

_LOCAL = "[project]\npush_to_remote = false\n"
_REMOTE = "[project]\npush_to_remote = true\n"

# Configurations that resolve to the matrix posture.
_MATRIX_CONFIGS = {
    "key_absent": (_LOCAL, "[paths]\n"),
    "matrix_in_project_cfg": (_LOCAL + "[release]\nship_posture = matrix\n", "[paths]\n"),
    "matrix_in_kanban_cfg": (_LOCAL, "[release]\nship_posture = matrix\n"),
    "always_on_unconfirmed_remote_lane": (_REMOTE + "[release]\nship_posture = always\n", "[paths]\n"),
}
_ALWAYS_CONFIG = (_LOCAL + "[release]\nship_posture = always\n", "[paths]\n")


def _build_root(tmp_path: pathlib.Path, project_cfg: str, kanban_cfg: str) -> pathlib.Path:
    """Write a synthetic kanban root with one project; return its path."""
    root = tmp_path / "kanban"
    project_root = root / "projects" / _PROJECT
    (project_root / "bugs").mkdir(parents=True)
    (project_root / "project.cfg").write_text(project_cfg, encoding="utf-8")
    (root / "kanban.cfg").write_text(kanban_cfg, encoding="utf-8")
    return root


def _q(value: str) -> str:
    """Single-quote *value* for a bash command line."""
    return "'" + value.replace("'", "'\\''") + "'"


def _decide(tmp_path: pathlib.Path, root: pathlib.Path, row: MatrixRow):
    """Resolve the posture, apply the matrix and the posture to *row*.

    Returns (values, narration, result): values is the dict of key=value
    lines the probe printed, narration the lines the posture functions
    printed on stdout.
    """
    state, risk, rec, large, found, _regression, _integrity = row.args
    narration = tmp_path / "narration.txt"
    script = (
        f"{_SOURCES} && PROJECT_NAME={_PROJECT} && "
        f"_TESTER_STATUS={_q(state)} _TESTER_SYSTEMIC_RISK={_q(risk)} "
        f"_TESTER_RECOMMENDATION={_q(rec)} _HAS_LARGE_FIX_EFFORT={_q(large)} "
        f"_TESTER_TASK_FOUND={_q(found)} && "
        f"_SHIP_DECISION=\"$(cm_ship_policy {' '.join(_q(a) for a in row.args)})\" && "
        f"{{ cm_ship_posture_read; cm_ship_posture_apply_decision; }} > {_q(str(narration))} && "
        "echo \"posture=$_SHIP_POSTURE\" && "
        "echo \"decision=$_SHIP_DECISION\" && "
        "echo \"notes_status=$(cm_ship_policy_notes_status \"$_SHIP_DECISION\")\" && "
        "echo \"halt_key=$_SHIP_HALT_KEY\" && "
        "echo \"auto_ship_trigger=$_AUTO_SHIP_TRIGGER\" && "
        "echo \"events=${#_CM_AUTO_SHIP_EVENTS[@]}\" && "
        "for e in \"${_CM_AUTO_SHIP_EVENTS[@]}\"; do printf 'event=%s\\n' \"$e\"; done"
    )
    result = run_bash(tmp_path, script, extra_env={"KANBAN_ROOT": str(root), "PGAI_PROJECT_NAME": ""})
    values: dict = {}
    events: list = []
    for line in result.stdout.splitlines():
        key, _, value = line.partition("=")
        if key == "event":
            events.append(value)
        else:
            values[key] = value
    values["event_list"] = events
    narration_lines = narration.read_text(encoding="utf-8").splitlines() if narration.exists() else []
    return values, narration_lines, result


def _diag(result) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


@pytest.mark.parametrize("config_id", sorted(_MATRIX_CONFIGS))
@pytest.mark.parametrize("row", _ROWS, ids=[r.id for r in _ROWS])
def test_matrix_posture_keeps_every_row_decision_notes_status_and_halt_key(tmp_path, row, config_id):
    project_cfg, kanban_cfg = _MATRIX_CONFIGS[config_id]
    root = _build_root(tmp_path, project_cfg, kanban_cfg)
    values, narration, result = _decide(tmp_path, root, row)

    assert result.returncode == 0, _diag(result)
    assert values["posture"] == "matrix", _diag(result)
    assert values["decision"] == row.decision, _diag(result)
    assert values["notes_status"] == row.notes_status, _diag(result)
    assert values["halt_key"] == row.halt_key, _diag(result)
    assert values["events"] == "0", _diag(result)
    assert narration == [], f"the matrix posture must print nothing, got {narration!r}"


@pytest.mark.parametrize("row", _ROWS, ids=[r.id for r in _ROWS])
def test_always_posture_ships_triggers_two_and_three_and_leaves_every_other_row(tmp_path, row):
    root = _build_root(tmp_path, *_ALWAYS_CONFIG)
    values, narration, result = _decide(tmp_path, root, row)

    assert result.returncode == 0, _diag(result)
    assert values["posture"] == "always", _diag(result)
    # The HALT row the matrix named is reported either way.
    assert values["halt_key"] == row.halt_key, _diag(result)
    if row.halt_key in (_KEY_T2, _KEY_T3):
        trigger = "2" if row.halt_key == _KEY_T2 else "3"
        assert values["decision"] == "SHIP-NON-FUNCTIONAL", _diag(result)
        assert values["notes_status"] == "NON-FUNCTIONAL", _diag(result)
        assert values["auto_ship_trigger"] == trigger, _diag(result)
        assert values["events"] == "1", _diag(result)
        _timestamp, ev_trigger, ev_key, ev_bug = (values["event_list"][0].split("\t") + [""])[:4]
        assert (ev_trigger, ev_key, ev_bug) == (trigger, row.halt_key, ""), values["event_list"]
        assert any(f"trigger {trigger} ({row.halt_key}) ships NON-FUNCTIONAL" in line for line in narration), narration
    else:
        assert values["decision"] == row.decision, _diag(result)
        assert values["notes_status"] == row.notes_status, _diag(result)
        assert values["auto_ship_trigger"] == "none", _diag(result)
        assert values["events"] == "0", _diag(result)


def test_unmatched_halt_row_still_halts_under_always(tmp_path):
    # A HALT whose inputs fit no named row reaches the catch-all; it is a
    # data-integrity stop, not a covered trigger, so it halts under always.
    root = _build_root(tmp_path, *_ALWAYS_CONFIG)
    script = (
        f"{_SOURCES} && PROJECT_NAME={_PROJECT} && cm_ship_posture_read >/dev/null && "
        "_SHIP_DECISION=HALT _TESTER_STATUS=done _TESTER_TASK_FOUND=yes "
        "_TESTER_SYSTEMIC_RISK=HIGH _TESTER_RECOMMENDATION=pass _HAS_LARGE_FIX_EFFORT=no && "
        "cm_ship_posture_apply_decision && "
        "echo \"$_SHIP_DECISION $_SHIP_HALT_KEY ${#_CM_AUTO_SHIP_EVENTS[@]}\""
    )
    result = run_bash(tmp_path, script, extra_env={"KANBAN_ROOT": str(root)})
    assert result.returncode == 0, _diag(result)
    assert result.stdout.split() == ["HALT", _KEY_UNMATCHED, "0"], _diag(result)


@pytest.mark.parametrize(
    "posture, trigger, passes",
    [
        ("always", "2", True),
        ("always", "3", True),
        ("always", "8", True),
        ("always", "1", False),
        ("always", "4", False),
        ("always", "5", False),
        ("always", "7", False),
        ("always", "9", False),
        ("always", "10", False),
        ("always", "11", False),
        ("always", "none", False),
        ("matrix", "2", False),
        ("matrix", "3", False),
        ("matrix", "8", False),
        ("ALWAYS", "2", False),
        ("", "2", False),
    ],
)
def test_only_always_ships_through_triggers_two_three_and_eight(tmp_path, posture, trigger, passes):
    result = run_bash(tmp_path, f"{_SOURCES} && cm_ship_posture_passes {_q(posture)} {_q(trigger)}")
    assert (result.returncode == 0) is passes, _diag(result)


@pytest.mark.parametrize(
    "key, number",
    [
        (_KEY_T1, "1"),
        (_KEY_T2, "2"),
        (_KEY_T3, "3"),
        (_KEY_T8, "8"),
        ("cm-release.trigger-10.pollution-guard-strict", "10"),
        (_KEY_UNRECOGNIZED, "none"),
        (_KEY_UNMATCHED, "none"),
        ("", "none"),
    ],
)
def test_trigger_number_is_read_from_the_error_key(tmp_path, key, number):
    result = run_bash(tmp_path, f"{_SOURCES} && cm_ship_policy_trigger_number {_q(key)}")
    assert result.returncode == 0, _diag(result)
    assert result.stdout.strip() == number


# ---------------------------------------------------------------------------
# Step 4c -- the last-three-releases-NON-FUNCTIONAL pattern
# ---------------------------------------------------------------------------

_PATTERN_OUTPUT = (
    "NON-FUNCTIONAL pattern check: last 3 releases: v1.1.0.md=NON-FUNCTIONAL, "
    "v1.2.0.md=NON-FUNCTIONAL, v1.3.0.md=NON-FUNCTIONAL\n"
    "NON-FUNCTIONAL pattern DETECTED: all 3 consecutive releases marked NON-FUNCTIONAL."
)


def _pattern_stop(tmp_path: pathlib.Path, root: pathlib.Path, calls: int = 1):
    notes_dir = tmp_path / "release-notes"
    notes_dir.mkdir(exist_ok=True)
    call = (
        f"if cm_ship_posture_pass_pattern_stop {_q(str(notes_dir))} {_q(_PATTERN_OUTPUT)}; "
        "then echo passed=yes; else echo passed=no; fi"
    )
    script = (
        f"{_SOURCES} && PROJECT_NAME={_PROJECT} ACTIVE_RC={_VERSION} "
        f"_CM_PROJECT_ROOT={_q(str(root / 'projects' / _PROJECT))} && "
        "cm_ship_posture_read && "
        + " && ".join([call] * calls)
        + " && for e in \"${_CM_AUTO_SHIP_EVENTS[@]}\"; do printf 'event=%s\\n' \"$e\"; done"
    )
    return run_bash(tmp_path, script, extra_env={"KANBAN_ROOT": str(root)}), notes_dir


def test_pattern_stop_under_matrix_halts_and_files_nothing(tmp_path):
    root = _build_root(tmp_path, *_MATRIX_CONFIGS["key_absent"])
    result, _notes_dir = _pattern_stop(tmp_path, root)

    assert result.returncode == 0, _diag(result)
    assert result.stdout.strip() == "passed=no", _diag(result)
    assert list((root / "projects" / _PROJECT / "bugs").iterdir()) == []


def test_pattern_stop_under_always_files_one_bug_and_records_it_in_the_event(tmp_path):
    root = _build_root(tmp_path, *_ALWAYS_CONFIG)
    result, notes_dir = _pattern_stop(tmp_path, root)

    assert result.returncode == 0, _diag(result)
    assert "passed=yes" in result.stdout.splitlines(), _diag(result)
    bugs = sorted((root / "projects" / _PROJECT / "bugs").glob("BUG-*.md"))
    assert len(bugs) == 1, _diag(result)
    bug_text = bugs[0].read_text(encoding="utf-8")
    assert "NON-FUNCTIONAL" in bug_text
    assert "v1.1.0.md=NON-FUNCTIONAL, v1.2.0.md=NON-FUNCTIONAL, v1.3.0.md=NON-FUNCTIONAL" in bug_text
    assert str(notes_dir) in bug_text
    bug_id = re.match(r"(BUG-\d+)", bugs[0].name).group(1)

    events = [line.split("=", 1)[1] for line in result.stdout.splitlines() if line.startswith("event=")]
    assert len(events) == 1, _diag(result)
    _timestamp, trigger, key, filed_bug = events[0].split("\t")
    assert (trigger, key, filed_bug) == ("8", _KEY_T8, bug_id)


def test_pattern_stop_cites_the_open_pattern_bug_instead_of_filing_another(tmp_path):
    root = _build_root(tmp_path, *_ALWAYS_CONFIG)
    result, _notes_dir = _pattern_stop(tmp_path, root, calls=2)

    assert result.returncode == 0, _diag(result)
    bugs = sorted((root / "projects" / _PROJECT / "bugs").glob("BUG-*.md"))
    assert len(bugs) == 1, _diag(result)
    bug_id = re.match(r"(BUG-\d+)", bugs[0].name).group(1)
    events = [line.split("=", 1)[1] for line in result.stdout.splitlines() if line.startswith("event=")]
    assert [e.split("\t")[3] for e in events] == [bug_id, bug_id], _diag(result)
    assert "Bug already open:" in result.stdout, _diag(result)


# ---------------------------------------------------------------------------
# Step 4d -- the Known Issues block after a trigger-2/3 auto-ship
# ---------------------------------------------------------------------------

_REPORT = """# Verification Report: v1.4.0

## Recommendation
SHIP-WITH-SERIOUS-CONCERNS

## Systemic Risk
medium

## Findings

### Finding 1: Parallel unit lane aborts at collection

**Category:** bug
**Fix Effort:** small
**Systemic Risk:** high (the unit signal is dead)
**Regression:** yes
**Integrity:** breakage
**Filed As:** BUG-0042 (Path C-A)

### Finding 2: Inline Python reduction short of target

**Category:** gap
**Fix Effort:** large (architectural)
**Systemic Risk:** low
**Filed As:** BUG-0043 (Path C-A)

## Lint Findings (non-blocking)

None.
"""

_BUG = """# {bug_id}: {slug}

**Bug ID:** {bug_id}-{slug}
**Severity:** high

## Status
open

## Symptom
{symptom}
"""


def _write_bug(bugs_dir: pathlib.Path, bug_id: str, slug: str, symptom: str) -> pathlib.Path:
    path = bugs_dir / f"{bug_id}-{slug}.md"
    path.write_text(_BUG.format(bug_id=bug_id, slug=slug, symptom=symptom), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "trigger, named, not_named",
    [
        ("2", "The parallel unit lane collects different tests in each worker", "Inline Python remains above the reduction target"),
        ("3", "Inline Python remains above the reduction target", "The parallel unit lane collects different tests in each worker"),
    ],
)
def test_known_issues_block_names_the_bugs_behind_the_stop(tmp_path, trigger, named, not_named):
    root = _build_root(tmp_path, *_ALWAYS_CONFIG)
    bugs_dir = root / "projects" / _PROJECT / "bugs"
    _write_bug(bugs_dir, "BUG-0042", "unit-lane-collection", "The parallel unit lane collects different tests in each worker.")
    _write_bug(bugs_dir, "BUG-0043", "inline-python-target", "Inline Python remains above the reduction target.")
    report = tmp_path / "report.md"
    report.write_text(_REPORT, encoding="utf-8")

    script = (
        f"{_SOURCES} && _AUTO_SHIP_TRIGGER={trigger} _TESTER_REPORT_PATH={_q(str(report))} "
        f"_CM_PROJECT_ROOT={_q(str(root / 'projects' / _PROJECT))} && "
        "_KNOWN_ISSUE_BUG_FILES=() && cm_ship_posture_resolve_known_issues && "
        "printf 'file=%s\\n' \"${_KNOWN_ISSUE_BUG_FILES[@]}\" && "
        "printf '%s\\n' '--- block ---' \"$_KNOWN_ISSUE_NOTES_BLOCK\""
    )
    result = run_bash(tmp_path, script, extra_env={"KANBAN_ROOT": str(root)})

    assert result.returncode == 0, _diag(result)
    files = [line.split("=", 1)[1] for line in result.stdout.splitlines() if line.startswith("file=")]
    assert len(files) == 1, _diag(result)
    block = result.stdout.split("--- block ---\n", 1)[1]
    assert block.startswith("This release is shipped non-functional."), block
    assert named in block, block
    assert not_named not in block, block
    assert "BUG-0042" not in block and "BUG-0043" not in block, block


def test_known_issues_block_without_a_resolvable_bug_points_at_the_ledger(tmp_path):
    root = _build_root(tmp_path, *_ALWAYS_CONFIG)
    script = (
        f"{_SOURCES} && _AUTO_SHIP_TRIGGER=2 _TESTER_REPORT_PATH={_q(str(tmp_path / 'missing.md'))} "
        f"_CM_PROJECT_ROOT={_q(str(root / 'projects' / _PROJECT))} && "
        "cm_ship_posture_resolve_known_issues >/dev/null && printf '%s\\n' \"$_KNOWN_ISSUE_NOTES_BLOCK\""
    )
    result = run_bash(tmp_path, script, extra_env={"KANBAN_ROOT": str(root)})

    assert result.returncode == 0, _diag(result)
    assert result.stdout.splitlines() == [
        "This release is shipped non-functional. Do not use in production. Fix expected in the next patch.",
        "The findings behind this status are tracked on the project's internal bug ledger.",
    ], _diag(result)


def test_known_issues_resolution_does_nothing_after_an_ordinary_decision(tmp_path):
    result = run_bash(
        tmp_path,
        f"{_SOURCES} && _AUTO_SHIP_TRIGGER=none _KNOWN_ISSUE_NOTES_BLOCK=unchanged && "
        "cm_ship_posture_resolve_known_issues && echo \"$_KNOWN_ISSUE_NOTES_BLOCK\"",
    )
    assert result.returncode == 0, _diag(result)
    assert result.stdout == "unchanged\n", _diag(result)


# ---------------------------------------------------------------------------
# Step 12 post-write -- appending the recorded events
# ---------------------------------------------------------------------------

_RELEASE_STATE = (
    "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n"
    "## RC Opened By Task\nnone\n\n## Last Released\nv1.4.0\n"
)


def test_recorded_events_are_appended_to_release_state_in_order(tmp_path):
    state = tmp_path / "release-state.md"
    state.write_text(_RELEASE_STATE, encoding="utf-8")
    script = (
        f"{_SOURCES} && ACTIVE_RC={_VERSION} RELEASE_STATE={_q(str(state))} "
        "_SHIP_POSTURE=always _TESTER_TASK_ID=TESTER-20260601-001-verify && "
        f"_cm_record_auto_ship_event 8 {_KEY_T8} BUG-0044 && "
        f"_cm_record_auto_ship_event 2 {_KEY_T2} && "
        "cm_release_append_auto_ship_events"
    )
    result = run_bash(tmp_path, script, extra_env={"KANBAN_ROOT": str(_TEAM_DIR)})

    assert result.returncode == 0, _diag(result)
    text = state.read_text(encoding="utf-8")
    assert text.startswith(_RELEASE_STATE)
    blocks = text.split("## Auto-Ship Event\n")[1:]
    assert len(blocks) == 2, text
    assert "Trigger: 8\n" in blocks[0] and f"Error Key: {_KEY_T8}\n" in blocks[0]
    assert "Filed Bug: BUG-0044\n" in blocks[0]
    assert "Trigger: 2\n" in blocks[1] and f"Error Key: {_KEY_T2}\n" in blocks[1]
    assert "Filed Bug:" not in blocks[1]
    for block in blocks:
        assert f"Version: {_VERSION}\n" in block
        assert "Ship Posture: always\n" in block
        assert "TESTER Task: TESTER-20260601-001-verify\n" in block


def test_event_writer_failure_is_a_warning_not_an_exit(tmp_path):
    script = (
        f"{_SOURCES} && set -euo pipefail && ACTIVE_RC={_VERSION} "
        f"RELEASE_STATE={_q(str(tmp_path / 'absent' / 'release-state.md'))} "
        "_SHIP_POSTURE=always _TESTER_TASK_ID=none && "
        f"_cm_record_auto_ship_event 2 {_KEY_T2} >/dev/null && "
        "cm_release_append_auto_ship_events && echo continued"
    )
    result = run_bash(tmp_path, script, extra_env={"KANBAN_ROOT": str(_TEAM_DIR)})

    assert result.returncode == 0, _diag(result)
    assert result.stdout.strip().endswith("continued"), _diag(result)
    assert "WARNING: the Auto-Ship Event for trigger 2 could not be appended" in result.stderr


def test_no_recorded_event_appends_nothing(tmp_path):
    state = tmp_path / "release-state.md"
    state.write_text(_RELEASE_STATE, encoding="utf-8")
    result = run_bash(
        tmp_path,
        f"{_SOURCES} && RELEASE_STATE={_q(str(state))} && cm_release_append_auto_ship_events",
        extra_env={"KANBAN_ROOT": str(_TEAM_DIR)},
    )
    assert result.returncode == 0, _diag(result)
    assert result.stdout == ""
    assert state.read_text(encoding="utf-8") == _RELEASE_STATE
