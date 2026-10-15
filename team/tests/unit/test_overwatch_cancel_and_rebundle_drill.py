"""
test_overwatch_cancel_and_rebundle_drill.py
=============================================
Behavioral tests for the CANCEL-AND-REBUNDLE drill in
pgai_agent_kanban.overwatch.resolution_stage: the drill is all-or-nothing
on its RC-cancel step.

  - Cancel fails: the RC task set, the bundle intake item and every cargo
    bug keep their statuses (bundle and bugs stay "running", so a later
    ship of the same RC still promotes them to done), the ship-policy HALT
    stays in place, and both the returned errors and the OVERWATCH action
    log say the cancel failed and the status reverts were not run.
  - Cancel succeeds: RC tasks go WONT-DO, the bundle closes wont-do with its
    close note, the cargo bugs reset to open and the ship-policy HALT is
    removed.

Design:
  - Every test builds a throwaway kanban root under pytest's tmp_path; no
    test reads or writes $PGAI_AGENT_KANBAN_ROOT_PATH.
  - The cancel-rc.sh subprocess is never run: _run_cm_cancel_rc is replaced
    by a recording stub, and every test asserts the stub was the one
    called. The success path through the real script is covered by the
    integration suite (tests/integration/test_overwatch_resolution_stage.py).
  - The matrix match is stubbed (resolve_block returns a CANCEL-AND-REBUNDLE
    result) so these tests need no git dev tree; the matrix's own fact
    extraction is covered by test_resolution_matrix.py. Everything after the
    match — bundle-family discovery, RC task discovery, the ledger write,
    the ops write layer and the action log — runs for real.
"""

from __future__ import annotations

import pathlib

import pytest

from pgai_agent_kanban.overwatch import resolution_stage
from pgai_agent_kanban.overwatch.resolution_matrix import (
    CANCEL_AND_REBUNDLE,
    BlockFacts,
    ResolutionResult,
)
from pgai_agent_kanban.overwatch.resolution_stage import EXECUTED, run_resolution_stage

_PROJECT = "drill_project"
_VERSION = "v9.9.1"
_BUNDLE_KEY = f"{_VERSION}-bugfix-bundle-20990101"
_BUG_IDS = ("BUG-0001-first-cargo-bug", "BUG-0002-second-cargo-bug")
_CODER_TASK = "CODER-20990101-001-fix-first"
_CM_TASK = "CM-20990101-002-release"
_LOCKED_OUTPUT = "LOCKED: project lock held by another cm operation\n"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _write_task(tasks_dir: pathlib.Path, task_id: str, role: str, state: str) -> None:
    task_dir = tasks_dir / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "README.md").write_text(
        f"# Task: {task_id}\n\n## Task ID\n{task_id}\n\n## Role\n{role}\n\n"
        f"## Source Branch\nai_rc/{_VERSION}\n\n## Release Version\n{_VERSION}\n",
        encoding="utf-8",
    )
    (task_dir / "status.md").write_text(
        f"# Status\n\n## Task\n{task_id}\n\n## State\n{state}\n\n## Blockers\nnone\n",
        encoding="utf-8",
    )


@pytest.fixture
def kanban_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """A throwaway kanban root holding one project halted on a ship-policy
    HALT with an active RC, a running bundle carrying two running bugs, and
    two still-active RC tasks."""
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
        f"## RC Opened By Task\n{_CM_TASK}\n",
        encoding="utf-8",
    )
    (proj / "HALT").write_text(
        "# HALT created by CM at 2099-01-01T00:00:00Z\n"
        "# Reason: TESTER systemic_risk=high\n",
        encoding="utf-8",
    )

    bundled_lines = []
    for bug_id in _BUG_IDS:
        (proj / "bugs" / f"{bug_id}.md").write_text(
            f"# Bug: {bug_id}\n\n## Status\nrunning\n\n## Symptom\nsample\n", encoding="utf-8"
        )
        bundled_lines.append(f"- {bug_id}.md (`{proj / 'bugs' / (bug_id + '.md')}`)\n")
    (proj / "tasks" / "queues" / "bug_backlog.md").write_text(
        "".join(f"- [x] {bug_id}\n" for bug_id in _BUG_IDS), encoding="utf-8"
    )
    (proj / "requirements" / f"{_BUNDLE_KEY}.md").write_text(
        f"# Requirements: {_VERSION} bug bundle\n\n"
        "## Status\nrunning\n\n"
        f"## Target Version\n{_VERSION}\n\n"
        "## Bundled Items\n\n" + "".join(bundled_lines),
        encoding="utf-8",
    )

    _write_task(proj / "tasks", _CODER_TASK, "CODER", "WORKING")
    _write_task(proj / "tasks", _CM_TASK, "CM", "BLOCKED")
    return root


def _project_dir(root: pathlib.Path) -> pathlib.Path:
    return root / "projects" / _PROJECT


def _section(path: pathlib.Path, heading: str) -> str:
    """Return the first non-blank line under ``## <heading>`` in *path*."""
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.strip() == f"## {heading}":
            for value in lines[index + 1:]:
                if value.strip():
                    return value.strip()
    return ""


class _CancelStub:
    """Stand-in for _run_cm_cancel_rc: records every call, returns a fixed result."""

    def __init__(self, ok: bool, output: str) -> None:
        self.ok = ok
        self.output = output
        self.calls: list = []

    def __call__(self, kanban_root: str, project_name: str, version: str) -> tuple:
        self.calls.append((kanban_root, project_name, version))
        return self.ok, self.output


def _install_stubs(monkeypatch: pytest.MonkeyPatch, cancel_ok: bool, output: str) -> _CancelStub:
    stub = _CancelStub(cancel_ok, output)
    monkeypatch.setattr(resolution_stage, "_run_cm_cancel_rc", stub)
    facts = BlockFacts(
        trigger_class="ship-policy-halt",
        findings_all_filed=True,
        shipped_or_tagged=False,
        approval_gate_present=False,
        operator_commits_in_lineage=False,
        attempts_below_ceiling=True,
        attempt=1,
        ceiling=3,
    )
    monkeypatch.setattr(
        resolution_stage,
        "resolve_block",
        lambda evidence, kanban_root=None: ResolutionResult(
            resolution=CANCEL_AND_REBUNDLE, facts=facts, matched_row=None
        ),
    )
    return stub


def _action_log_lines(root: pathlib.Path) -> list:
    log_file = _project_dir(root) / "overwatch" / "actions.log"
    return log_file.read_text(encoding="utf-8").splitlines()


# ---------------------------------------------------------------------------
# Cancel fails: nothing is reverted
# ---------------------------------------------------------------------------


class TestFailedCancelRevertsNothing:
    def test_bundle_and_cargo_bug_statuses_stay_running(
        self, kanban_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = _install_stubs(monkeypatch, cancel_ok=False, output=_LOCKED_OUTPUT)
        proj = _project_dir(kanban_root)
        bundle = proj / "requirements" / f"{_BUNDLE_KEY}.md"
        bundle_before = bundle.read_bytes()
        backlog = proj / "tasks" / "queues" / "bug_backlog.md"
        backlog_before = backlog.read_bytes()

        outcome = run_resolution_stage(_PROJECT, str(kanban_root))

        assert stub.calls == [(str(kanban_root), _PROJECT, _VERSION)]
        assert outcome.status == EXECUTED, outcome.reason
        assert _section(bundle, "Status") == "running"
        assert bundle.read_bytes() == bundle_before, "the bundle intake item must not be rewritten"
        for bug_id in _BUG_IDS:
            assert _section(proj / "bugs" / f"{bug_id}.md", "Status") == "running", bug_id
        assert backlog.read_bytes() == backlog_before, "bug backlog markers must not be flipped"

    def test_no_rc_task_is_moved_to_wont_do(
        self, kanban_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_stubs(monkeypatch, cancel_ok=False, output=_LOCKED_OUTPUT)
        proj = _project_dir(kanban_root)

        run_resolution_stage(_PROJECT, str(kanban_root))

        assert _section(proj / "tasks" / _CODER_TASK / "status.md", "State") == "WORKING"
        assert _section(proj / "tasks" / _CM_TASK / "status.md", "State") == "BLOCKED"

    def test_ship_policy_halt_stays_in_place(
        self, kanban_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_stubs(monkeypatch, cancel_ok=False, output=_LOCKED_OUTPUT)

        outcome = run_resolution_stage(_PROJECT, str(kanban_root))

        assert (_project_dir(kanban_root) / "HALT").is_file()
        assert not any(a.startswith("cleared ship-policy HALT") for a in outcome.actions_taken)

    def test_returned_errors_name_the_cancel_failure_and_the_skipped_reverts(
        self, kanban_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_stubs(monkeypatch, cancel_ok=False, output=_LOCKED_OUTPUT)

        outcome = run_resolution_stage(_PROJECT, str(kanban_root))

        assert outcome.actions_taken == [], "no drill step may report success"
        assert len(outcome.action_errors) == 2, outcome.action_errors
        cancel_error, skip_note = outcome.action_errors
        assert cancel_error.startswith("scripts/cm/cancel-rc.sh ") and "FAILED" in cancel_error
        assert "LOCKED" in cancel_error, "the script's own failure output must be carried"
        assert "status-revert steps not run" in skip_note
        assert "RC cancel failed" in skip_note
        assert _BUNDLE_KEY in skip_note
        assert "2 cargo bug(s)" in skip_note and "2 RC task(s)" in skip_note
        assert "HALT left in place" in skip_note

    def test_action_log_summary_line_says_the_cancel_failed_and_nothing_was_reverted(
        self, kanban_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_stubs(monkeypatch, cancel_ok=False, output=_LOCKED_OUTPUT)

        run_resolution_stage(_PROJECT, str(kanban_root))

        lines = _action_log_lines(kanban_root)
        summary = [line for line in lines if "\tauto-fix:cancel-and-rebundle\t" in line]
        assert len(summary) == 1, lines
        assert "RC cancel FAILED" in summary[0]
        assert "status-revert steps not run" in summary[0]
        error_lines = [line for line in lines if "\tauto-fix-error\t" in line]
        assert len(error_lines) == 2, lines
        assert "LOCKED" in error_lines[0]
        assert "status-revert steps not run" in error_lines[1]

    def test_ledger_entry_is_still_written_before_the_drill(
        self, kanban_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Decide-and-log holds on the failure path too: the ledger records the
        decision even when the cancel then fails."""
        _install_stubs(monkeypatch, cancel_ok=False, output=_LOCKED_OUTPUT)

        outcome = run_resolution_stage(_PROJECT, str(kanban_root))

        assert outcome.ledger_entry_id
        ledger_text = (_project_dir(kanban_root) / "auto-resolutions.md").read_text(encoding="utf-8")
        assert ledger_text.count("## Entry:") == 1


# ---------------------------------------------------------------------------
# Cancel succeeds: the existing full revert
# ---------------------------------------------------------------------------


class TestSuccessfulCancelRevertsEverything:
    def test_rc_tasks_wont_do_bundle_wont_do_bugs_open_halt_removed(
        self, kanban_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = _install_stubs(monkeypatch, cancel_ok=True, output="cancelled\n")
        proj = _project_dir(kanban_root)

        outcome = run_resolution_stage(_PROJECT, str(kanban_root))

        assert stub.calls == [(str(kanban_root), _PROJECT, _VERSION)]
        assert outcome.status == EXECUTED, outcome.reason
        assert outcome.action_errors == [], outcome.action_errors

        for task_id in (_CODER_TASK, _CM_TASK):
            assert _section(proj / "tasks" / task_id / "status.md", "State") == "WONT-DO", task_id

        bundle = proj / "requirements" / f"{_BUNDLE_KEY}.md"
        assert _section(bundle, "Status") == "wont-do"
        assert "OVERWATCH auto-resolution: CANCEL-AND-REBUNDLE" in bundle.read_text(encoding="utf-8")

        for bug_id in _BUG_IDS:
            assert _section(proj / "bugs" / f"{bug_id}.md", "Status") == "open", bug_id

        assert not (proj / "HALT").exists(), "the ship-policy HALT must be removed"

    def test_action_log_summary_line_does_not_report_a_cancel_failure(
        self, kanban_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_stubs(monkeypatch, cancel_ok=True, output="cancelled\n")

        run_resolution_stage(_PROJECT, str(kanban_root))

        lines = _action_log_lines(kanban_root)
        summary = [line for line in lines if "\tauto-fix:cancel-and-rebundle\t" in line]
        assert len(summary) == 1, lines
        assert "FAILED" not in summary[0]
        assert "status-revert steps not run" not in summary[0]
        assert not [line for line in lines if "\tauto-fix-error\t" in line]
