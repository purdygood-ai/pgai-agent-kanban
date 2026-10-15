"""
test_overwatch_lane_a_drill_stops_on_failed_rc_step.py
========================================================
Behavioral tests for the OVERWATCH Lane A recovery drill in
pgai_agent_kanban.overwatch.resolution_stage: execute_lane_a_drill() is
all-or-nothing on its two RC steps, unwind-rc then cancel-rc.

  - cancel-rc fails: the bundle document and the ship-policy HALT both stay
    in place, and the returned errors name the cancel failure and state
    that the later steps were not run.
  - unwind-rc fails (cancel-rc would succeed): the drill stops at the
    unwind — cancel-rc is never invoked, the bundle document and the HALT
    stay in place, and the returned errors name the unwind failure and
    every later step, cancel-rc included, as not run.
  - Both succeed: the existing outcome — the bundle document is deleted and
    the ship-policy HALT is removed.
  - The OVERWATCH action log's Lane A summary line (written by
    _run_lane_a()) names the failed RC step and says the later steps were
    not run; on success it reports no failure.

Design:
  - Every test builds a throwaway kanban root under pytest's tmp_path (which
    the suite conftest anchors under $PGAI_AGENT_KANBAN_TEMP_DIR), points
    PGAI_AGENT_KANBAN_ROOT_PATH and KANBAN_ROOT at it, and clears
    PGAI_TASKS_DIR and PGAI_PROJECT_ROOT so no path resolves to a live
    install.
  - The unwind-rc.sh and cancel-rc.sh subprocesses are never run:
    _run_unwind_rc and _run_cm_cancel_rc are replaced by recording stubs,
    and every test asserts which stubs were called. The success path
    through the real scripts is covered by the integration suite
    (tests/integration/test_overwatch_lane_a_drill.py).
  - Everything after the stubs — bundle-key resolution, the ops-layer
    delete_item(), the HALT re-classification and removal, the ledger and
    the action log — runs for real.
"""

from __future__ import annotations

import pathlib

import pytest

from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.overwatch import resolution_stage
from pgai_agent_kanban.overwatch.fix_rewrite import REWRITE_BLOCK_HEADING
from pgai_agent_kanban.overwatch.resolution_matrix import LANE_A, LaneClassification
from pgai_agent_kanban.overwatch.resolution_stage import EXECUTED, execute_lane_a_drill

_PROJECT = "demo"
_VERSION = "v9.9.1"
_BUNDLE_KEY = f"{_VERSION}-bugfix-bundle-20990101"
_BLAMED_BUG = "BUG-0001-blamed-fix"
_LOCKED_OUTPUT = "LOCKED: project lock held by another cm operation\n"

_UNWIND_STEP = f"unwind-rc.sh --project {_PROJECT} --key {_VERSION} --force"
_CANCEL_STEP = f"cancel-rc.sh --project {_PROJECT} --key {_VERSION} --yes"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


@pytest.fixture
def kanban_root(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """A throwaway kanban root holding one project halted on a CM
    ship-policy HALT with an active RC and a running bundle document."""
    root = tmp_path / "kanban_root"
    proj = root / "projects" / _PROJECT
    for sub in ("requirements", "bugs", "tasks/queues", "release-state"):
        (proj / sub).mkdir(parents=True)
    (root / "logs").mkdir(parents=True)

    (proj / "project.cfg").write_text(
        f"[project]\nproject_name = {_PROJECT}\nbranch_prefix = ai_\n", encoding="utf-8"
    )
    (proj / "release-state.md").write_text(
        "# Release State\n\n"
        f"## Active RC\n{_VERSION}\n\n"
        "## RC Opened At\n2099-01-01T00:00:00Z\n\n"
        "## RC Opened By Task\nCM-20990101-001-open-rc\n",
        encoding="utf-8",
    )
    (proj / "HALT").write_text(
        "# HALT created by CM at 2099-01-01T00:00:00Z\n"
        "# Reason: TESTER systemic_risk=high\n",
        encoding="utf-8",
    )
    (proj / "requirements" / f"{_BUNDLE_KEY}.md").write_text(
        f"# Requirements: {_VERSION} bug bundle\n\n"
        "## Status\nrunning\n\n"
        f"## Target Version\n{_VERSION}\n\n"
        f"## Bundled Items\n\n- {_BLAMED_BUG}.md\n",
        encoding="utf-8",
    )
    (proj / "bugs" / f"{_BLAMED_BUG}.md").write_text(
        f"# Bug: {_BLAMED_BUG}\n\n## Status\nrunning\n\n## Fix\nsample\n\n"
        f"---\n\n{REWRITE_BLOCK_HEADING}\nsample rewrite\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
    monkeypatch.setenv("KANBAN_ROOT", str(root))
    monkeypatch.delenv("PGAI_TASKS_DIR", raising=False)
    monkeypatch.delenv("PGAI_PROJECT_ROOT", raising=False)
    return root


def _project_dir(root: pathlib.Path) -> pathlib.Path:
    return root / "projects" / _PROJECT


def _bundle_doc(root: pathlib.Path) -> pathlib.Path:
    return _project_dir(root) / "requirements" / f"{_BUNDLE_KEY}.md"


def _halt(root: pathlib.Path) -> pathlib.Path:
    return _project_dir(root) / "HALT"


class _ScriptStub:
    """Stand-in for _run_unwind_rc / _run_cm_cancel_rc: records every call,
    returns a fixed (ok, output) result."""

    def __init__(self, ok: bool, output: str) -> None:
        self.ok = ok
        self.output = output
        self.calls: list = []

    def __call__(self, kanban_root: str, project_name: str, version: str) -> tuple:
        self.calls.append((kanban_root, project_name, version))
        return self.ok, self.output


def _install_stubs(
    monkeypatch: pytest.MonkeyPatch, unwind_ok: bool, cancel_ok: bool
) -> tuple:
    unwind = _ScriptStub(unwind_ok, "unwound\n" if unwind_ok else _LOCKED_OUTPUT)
    cancel = _ScriptStub(cancel_ok, "cancelled\n" if cancel_ok else _LOCKED_OUTPUT)
    monkeypatch.setattr(resolution_stage, "_run_unwind_rc", unwind)
    monkeypatch.setattr(resolution_stage, "_run_cm_cancel_rc", cancel)
    return unwind, cancel


def _run_drill(root: pathlib.Path) -> tuple:
    return execute_lane_a_drill(
        OpsContext(kanban_root=str(root)),
        str(root),
        _PROJECT,
        str(_project_dir(root)),
        _VERSION,
        _BUNDLE_KEY,
        str(_halt(root)),
    )


def _run_lane_a(root: pathlib.Path) -> resolution_stage.ResolutionStageOutcome:
    """Drive the drill through _run_lane_a(), the caller that writes the
    OVERWATCH action log, on a first attempt (no prior ledger entry)."""
    lane_result = LaneClassification(
        lane=LANE_A, reason="regression traced to the blamed fix", regression_bug_id=_BLAMED_BUG
    )
    return resolution_stage._run_lane_a(
        ctx=OpsContext(kanban_root=str(root)),
        kanban_root=str(root),
        project_name=_PROJECT,
        project_root=str(_project_dir(root)),
        version=_VERSION,
        lane_result=lane_result,
        family={"bundle_file": str(_bundle_doc(root)), "family_key": None},
        ship_halt_path=str(_halt(root)),
        evidence_refs=[],
        timestamp="2099-01-01T00:00:00Z",
    )


def _action_log_lines(root: pathlib.Path) -> list:
    log_file = _project_dir(root) / "overwatch" / "actions.log"
    return log_file.read_text(encoding="utf-8").splitlines()


# ---------------------------------------------------------------------------
# cancel-rc fails: the bundle document and the HALT stay
# ---------------------------------------------------------------------------


class TestFailedCancelLeavesBundleAndHalt:
    def test_bundle_doc_and_ship_policy_halt_still_exist(
        self, kanban_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        unwind, cancel = _install_stubs(monkeypatch, unwind_ok=True, cancel_ok=False)
        bundle_before = _bundle_doc(kanban_root).read_bytes()
        halt_before = _halt(kanban_root).read_bytes()

        _run_drill(kanban_root)

        expected_call = (str(kanban_root), _PROJECT, _VERSION)
        assert unwind.calls == [expected_call]
        assert cancel.calls == [expected_call]
        assert _bundle_doc(kanban_root).read_bytes() == bundle_before
        assert _halt(kanban_root).read_bytes() == halt_before

    def test_returned_errors_name_the_cancel_failure_and_the_steps_not_run(
        self, kanban_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_stubs(monkeypatch, unwind_ok=True, cancel_ok=False)

        completed, errors = _run_drill(kanban_root)

        assert completed == [_UNWIND_STEP], "only the unwind may report success"
        assert len(errors) == 2, errors
        cancel_error, skip_note = errors
        assert cancel_error.startswith(f"{_CANCEL_STEP} FAILED")
        assert "LOCKED" in cancel_error, "the script's own failure output must be carried"
        assert "later steps not run because the RC cancel failed" in skip_note
        assert "delete bundle doc" in skip_note and "clear ship-policy HALT" in skip_note
        assert f"bundle doc {_BUNDLE_KEY} left in place" in skip_note
        assert "ship-policy HALT left in place" in skip_note


# ---------------------------------------------------------------------------
# unwind-rc fails: the drill stops before cancel-rc
# ---------------------------------------------------------------------------


class TestFailedUnwindStopsBeforeCancel:
    def test_cancel_is_not_run_and_bundle_doc_and_halt_still_exist(
        self, kanban_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        unwind, cancel = _install_stubs(monkeypatch, unwind_ok=False, cancel_ok=True)
        bundle_before = _bundle_doc(kanban_root).read_bytes()
        halt_before = _halt(kanban_root).read_bytes()

        _run_drill(kanban_root)

        assert unwind.calls == [(str(kanban_root), _PROJECT, _VERSION)]
        assert cancel.calls == [], "cancel-rc must not run after a failed unwind"
        assert _bundle_doc(kanban_root).read_bytes() == bundle_before
        assert _halt(kanban_root).read_bytes() == halt_before

    def test_returned_errors_name_the_unwind_failure_and_every_step_not_run(
        self, kanban_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_stubs(monkeypatch, unwind_ok=False, cancel_ok=True)

        completed, errors = _run_drill(kanban_root)

        assert completed == [], "no drill step may report success"
        assert len(errors) == 2, errors
        unwind_error, skip_note = errors
        assert unwind_error.startswith(f"{_UNWIND_STEP} FAILED")
        assert "LOCKED" in unwind_error
        assert "later steps not run because the RC unwind failed" in skip_note
        assert _CANCEL_STEP in skip_note
        assert "delete bundle doc" in skip_note and "clear ship-policy HALT" in skip_note
        assert "ship-policy HALT left in place" in skip_note


# ---------------------------------------------------------------------------
# Both RC steps succeed: the existing outcome
# ---------------------------------------------------------------------------


class TestSuccessfulRcStepsDeleteBundleAndClearHalt:
    def test_bundle_doc_deleted_and_ship_policy_halt_removed(
        self, kanban_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        unwind, cancel = _install_stubs(monkeypatch, unwind_ok=True, cancel_ok=True)

        completed, errors = _run_drill(kanban_root)

        assert len(unwind.calls) == 1 and len(cancel.calls) == 1
        assert errors == [], errors
        assert completed == [
            _UNWIND_STEP,
            _CANCEL_STEP,
            f"delete bundle doc: {_BUNDLE_KEY}",
            f"cleared ship-policy HALT: {_halt(kanban_root)}",
        ]
        assert not _bundle_doc(kanban_root).exists(), "the bundle document must be deleted"
        assert not _halt(kanban_root).exists(), "the ship-policy HALT must be removed"


# ---------------------------------------------------------------------------
# The OVERWATCH action log's Lane A summary line
# ---------------------------------------------------------------------------


class TestActionLogNamesTheFailedStep:
    def test_failed_cancel_summary_line_says_later_steps_not_run(
        self, kanban_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_stubs(monkeypatch, unwind_ok=True, cancel_ok=False)

        outcome = _run_lane_a(kanban_root)

        assert outcome.status == EXECUTED, outcome.reason
        lines = _action_log_lines(kanban_root)
        summary = [line for line in lines if "\tauto-fix:lane-a-recovery\t" in line]
        assert len(summary) == 1, lines
        assert "RC cancel FAILED, later steps not run" in summary[0]
        assert "bundle doc and ship-policy HALT left in place" in summary[0]
        error_lines = [line for line in lines if "\tauto-fix-error\t" in line]
        assert len(error_lines) == 2, lines
        assert "LOCKED" in error_lines[0]
        assert "later steps not run because the RC cancel failed" in error_lines[1]
        assert _bundle_doc(kanban_root).is_file()
        assert _halt(kanban_root).is_file()

    def test_failed_unwind_summary_line_names_the_unwind(
        self, kanban_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_stubs(monkeypatch, unwind_ok=False, cancel_ok=True)

        _run_lane_a(kanban_root)

        summary = [
            line for line in _action_log_lines(kanban_root) if "\tauto-fix:lane-a-recovery\t" in line
        ]
        assert len(summary) == 1, summary
        assert "RC unwind FAILED, later steps not run" in summary[0]
        assert "RC cancel FAILED" not in summary[0]

    def test_successful_drill_summary_line_reports_no_failure(
        self, kanban_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_stubs(monkeypatch, unwind_ok=True, cancel_ok=True)

        _run_lane_a(kanban_root)

        lines = _action_log_lines(kanban_root)
        summary = [line for line in lines if "\tauto-fix:lane-a-recovery\t" in line]
        assert len(summary) == 1, lines
        assert "FAILED" not in summary[0]
        assert "later steps not run" not in summary[0]
        assert not [line for line in lines if "\tauto-fix-error\t" in line]
