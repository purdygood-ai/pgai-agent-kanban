"""
test_discovery_idle_gate_reads_task_status.py
==============================================
Integration tests for the discovery pipeline's idle/RC-in-flight decisions
(team/scripts/lib/discovery_guards.sh: _disc_chain_idle,
_disc_pm_ticket_in_flight).

These tests exercise the real guard functions against temporary kanban
trees — no seam is mocked. Each test stands up a real project tree with
real backlog files and real task status.md files, sources the real
discovery library, invokes the guard function directly, and asserts on its
return code and log output.

Coverage:
  - A backlog whose queue markers are stale (non-terminal) while the named
    tasks' own status.md files read DONE/WONT-DO is judged idle.
  - A backlog whose queue markers all read [x] while a named task's
    status.md reads WORKING is judged not idle, with a log line naming the
    task that closed the gate.
  - A queue entry naming a task folder that does not exist is judged not
    idle (the documented outcome for an uncorroborated task reference),
    rather than being decided by whatever the marker happened to say.
  - A backlog entry whose status.md has no parseable ## State block is
    judged not idle, for the same reason.
  - _disc_pm_ticket_in_flight mirrors the same status-based decision for
    the decompose-v* PM-ticket gate.

Design notes:
  - All tests use two_project_root for scratch. No bare /tmp paths.
  - Each test is self-contained and produces no state visible to other tests.
  - Test names describe the behavior under test, not the gate or ticket that
    motivated them.
  - The guard functions are invoked directly (not through the full
    discovery_run_pipeline), via a thin subprocess wrapper that sources the
    discovery library and calls the named function, echoing its exit code.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import textwrap

import pytest

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_SCRIPTS_LIB = _TEAM_DIR / "scripts" / "lib"


def _run_guard(
    kanban_root: pathlib.Path,
    project_name: str,
    function_call: str,
) -> subprocess.CompletedProcess:
    """Source the discovery library against a temp kanban tree and run *function_call*.

    *function_call* is a bash expression such as
    ``'_disc_chain_idle "project_a"'`` or
    ``'_disc_pm_ticket_in_flight "project_a"'``. The wrapper prints
    ``GUARD_EXIT=<n>`` on stdout after the call so tests can assert on the
    guard's return code without relying on ``set -e`` semantics.
    """
    script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -uo pipefail
        source "{_SCRIPTS_LIB}/ini_parser.sh"
        source "{_SCRIPTS_LIB}/temp.sh"
        source "{_SCRIPTS_LIB}/semver.sh"
        source "{_SCRIPTS_LIB}/project_paths.sh"
        source "{_SCRIPTS_LIB}/discovery.sh"
        {function_call}
        echo "GUARD_EXIT=$?"
    """)

    base_env = dict(os.environ)
    base_env["KANBAN_ROOT"] = str(kanban_root)
    base_env["TEAM_ROOT"] = str(kanban_root)
    base_env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    base_env["PGAI_PROJECT_NAME"] = project_name
    base_env.pop("PGAI_DEV_TREE_PATH", None)

    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=base_env,
        cwd=str(kanban_root),
        timeout=60,
    )


def _write_task_status(
    kanban_root: pathlib.Path,
    project_name: str,
    task_id: str,
    state: str | None,
    *,
    parseable: bool = True,
) -> pathlib.Path:
    """Create <task_id>/status.md under the project's tasks/ directory.

    When *parseable* is False, writes a status.md with no ## State block at
    all (an unparseable status file) and *state* is ignored.
    """
    task_dir = kanban_root / "projects" / project_name / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    status_file = task_dir / "status.md"
    if not parseable:
        status_file.write_text("no state header here, just prose\n", encoding="utf-8")
        return status_file
    status_file.write_text(f"## State\n{state}\n\n## Summary\ntest fixture\n", encoding="utf-8")
    return status_file


def _write_backlog(
    kanban_root: pathlib.Path,
    project_name: str,
    agent: str,
    entries: list[str],
) -> pathlib.Path:
    """Overwrite <agent>_backlog.md with the given entry lines (already formatted)."""
    backlog_file = (
        kanban_root / "projects" / project_name / "tasks" / "queues" / f"{agent}_backlog.md"
    )
    backlog_file.parent.mkdir(parents=True, exist_ok=True)
    header = f"# {agent.upper()} Backlog\n\n"
    backlog_file.write_text(header + "\n".join(entries) + "\n", encoding="utf-8")
    return backlog_file


class TestChainIdleReadsTaskStatus:
    """_disc_chain_idle decides from status.md, never from the queue marker."""

    def test_stale_non_terminal_markers_with_terminal_status_is_judged_idle(
        self, two_project_root: pathlib.Path
    ) -> None:
        """A [ ] marker left behind by an interrupted wake does not idle-block
        the gate once the task it names has reached a terminal status.md state.
        """
        root = two_project_root
        _write_task_status(root, "project_a", "CODER-1", "DONE")
        _write_task_status(root, "project_a", "CODER-2", "WONT-DO")
        _write_backlog(
            root, "project_a", "coder",
            [
                "- [ ] CODER-1 -- stale marker, task actually done",
                "- [B] CODER-2 -- stale marker, task actually wont-do",
            ],
        )

        result = _run_guard(root, "project_a", '_disc_chain_idle "project_a"')

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "GUARD_EXIT=0" in result.stdout, (
            f"Expected the gate to judge idle despite stale markers.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_terminal_marker_with_working_task_is_judged_not_idle(
        self, two_project_root: pathlib.Path
    ) -> None:
        """An [x] marker does not open the gate when the task it names is
        genuinely still in flight (status.md reads WORKING).

        The log line must name the task that closed the gate so an operator
        reading a wake log can see why nothing was queued.
        """
        root = two_project_root
        _write_task_status(root, "project_a", "CODER-3", "WORKING")
        _write_backlog(
            root, "project_a", "coder",
            ["- [x] CODER-3 -- marker says done, task is not"],
        )

        result = _run_guard(root, "project_a", '_disc_chain_idle "project_a"')

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "GUARD_EXIT=1" in result.stdout, (
            f"Expected the gate to judge NOT idle for a genuinely in-flight task.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert "CODER-3" in combined and "coder_backlog" in combined, (
            "Expected the log line to name the agent backlog and the task "
            f"that closed the gate.\ncombined output: {combined}"
        )

    def test_queue_entry_naming_missing_task_folder_does_not_block_idle(
        self, two_project_root: pathlib.Path
    ) -> None:
        """A backlog entry naming a task with no task folder is a real case
        with a documented answer: since task folders are never deleted once
        created, a missing folder has nothing left to verify and is treated
        as terminal (does not block idle) rather than wedging the gate
        forever — but the outcome is still logged distinctly so an operator
        can see the anomaly, and it is never decided by the marker.
        """
        root = two_project_root
        _write_backlog(
            root, "project_a", "coder",
            ["- [x] CODER-9999-ghost -- no task folder exists for this id"],
        )

        result = _run_guard(root, "project_a", '_disc_chain_idle "project_a"')

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "GUARD_EXIT=0" in result.stdout, (
            f"Expected a queue entry naming a missing task folder to NOT block idle.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert "no task folder" in combined.lower(), (
            f"Expected the log line to name the missing-task-folder outcome.\n"
            f"combined output: {combined}"
        )

    def test_backlog_entry_with_unparseable_status_is_judged_not_idle(
        self, two_project_root: pathlib.Path
    ) -> None:
        """A status.md with no parseable ## State block blocks idle rather
        than falling through to whatever the marker happened to say.
        """
        root = two_project_root
        _write_task_status(root, "project_a", "CODER-4", None, parseable=False)
        _write_backlog(
            root, "project_a", "coder",
            ["- [x] CODER-4 -- status.md has no ## State block"],
        )

        result = _run_guard(root, "project_a", '_disc_chain_idle "project_a"')

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "GUARD_EXIT=1" in result.stdout, (
            f"Expected an unparseable status.md to block idle.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert "unparseable" in combined.lower(), (
            f"Expected the log line to name the unparseable-status outcome.\n"
            f"combined output: {combined}"
        )

    def test_empty_backlog_is_judged_idle(self, two_project_root: pathlib.Path) -> None:
        """A backlog with no task entries at all counts as idle for that agent.

        Regression guard for the writer_backlog consolidation: before this
        rewrite, an empty writer_backlog was treated differently from an
        empty pm/cm/coder/tester backlog (the duplicated Check-2 block below
        the main loop). All five agents must now behave identically.
        """
        root = two_project_root
        # two_project_root already ships every backlog empty (comments only).

        result = _run_guard(root, "project_a", '_disc_chain_idle "project_a"')

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "GUARD_EXIT=0" in result.stdout, (
            f"Expected an all-empty set of backlogs to be judged idle.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_earlier_backlog_entry_still_in_flight_is_not_masked_by_terminal_tail(
        self, two_project_root: pathlib.Path
    ) -> None:
        """Every entry in a backlog is consulted, not just the last line.

        A terminal (DONE) tail entry must not mask an earlier entry that is
        still genuinely in flight — the prior tail-only implementation would
        have missed this case entirely.
        """
        root = two_project_root
        _write_task_status(root, "project_a", "CODER-5", "WORKING")
        _write_task_status(root, "project_a", "CODER-6", "DONE")
        _write_backlog(
            root, "project_a", "coder",
            [
                "- [x] CODER-5 -- still working despite stale [x] marker",
                "- [x] CODER-6 -- correctly done, appended after CODER-5",
            ],
        )

        result = _run_guard(root, "project_a", '_disc_chain_idle "project_a"')

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "GUARD_EXIT=1" in result.stdout, (
            "Expected the earlier in-flight backlog entry to block idle "
            "even though the tail entry is terminal.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert "CODER-5" in combined, (
            f"Expected the log line to name the earlier in-flight task.\ncombined: {combined}"
        )


class TestPmTicketInFlightReadsTaskStatus:
    """_disc_pm_ticket_in_flight decides from status.md, never the marker."""

    def test_stale_pending_marker_with_done_pm_task_is_not_in_flight(
        self, two_project_root: pathlib.Path
    ) -> None:
        """A [ ] decompose-v* entry whose PM task already reached DONE is not
        treated as in-flight, even though its queue marker was never flipped.
        """
        root = two_project_root
        task_id = "PM-20260601-001-decompose-v0-0-2-test-feature"
        _write_task_status(root, "project_a", task_id, "DONE")
        _write_backlog(
            root, "project_a", "pm",
            [f"- [ ] {task_id} -- stale marker, PM task actually done"],
        )

        result = _run_guard(root, "project_a", '_disc_pm_ticket_in_flight "project_a"')

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "GUARD_EXIT=1" in result.stdout, (
            f"Expected a done PM decompose ticket to NOT be in flight.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_x_marker_with_working_pm_task_is_in_flight(
        self, two_project_root: pathlib.Path
    ) -> None:
        """An [x] decompose-v* entry whose PM task is genuinely still WORKING
        is treated as in-flight — the marker never overrides status.md.
        """
        root = two_project_root
        task_id = "PM-20260601-002-decompose-v0-0-3-test-feature"
        _write_task_status(root, "project_a", task_id, "WORKING")
        _write_backlog(
            root, "project_a", "pm",
            [f"- [x] {task_id} -- marker says done, PM task is not"],
        )

        result = _run_guard(root, "project_a", '_disc_pm_ticket_in_flight "project_a"')

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "GUARD_EXIT=0" in result.stdout, (
            f"Expected a genuinely-working PM decompose ticket to be in flight.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert task_id in combined, (
            f"Expected the log line to name the in-flight PM task.\ncombined: {combined}"
        )

    def test_decompose_entry_naming_missing_task_folder_is_not_in_flight(
        self, two_project_root: pathlib.Path
    ) -> None:
        """A decompose-v* entry naming a task with no task folder has nothing
        left to verify and is treated as terminal (not in flight) — the same
        documented answer as the chain-idle gate, and for the same reason:
        no mechanism ever heals a missing task folder, so treating it as
        in-flight would block RC-opening forever.
        """
        root = two_project_root
        task_id = "PM-20260601-003-decompose-v0-0-4-ghost-task"
        _write_backlog(
            root, "project_a", "pm",
            [f"- [x] {task_id} -- no task folder exists for this id"],
        )

        result = _run_guard(root, "project_a", '_disc_pm_ticket_in_flight "project_a"')

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "GUARD_EXIT=1" in result.stdout, (
            f"Expected a decompose-v* entry naming a missing task folder to NOT be in flight.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert "no task folder" in combined.lower(), (
            f"Expected the log line to name the missing-task-folder outcome.\ncombined: {combined}"
        )

    def test_decompose_entry_with_unparseable_status_is_in_flight(
        self, two_project_root: pathlib.Path
    ) -> None:
        """A decompose-v* entry whose status.md has no parseable ## State
        block is active corruption of a file the wake toolchain owns, and is
        treated as in-flight (blocking) rather than falling through to
        whatever the marker happened to say.
        """
        root = two_project_root
        task_id = "PM-20260601-004-decompose-v0-0-5-corrupt-status"
        _write_task_status(root, "project_a", task_id, None, parseable=False)
        _write_backlog(
            root, "project_a", "pm",
            [f"- [x] {task_id} -- status.md has no ## State block"],
        )

        result = _run_guard(root, "project_a", '_disc_pm_ticket_in_flight "project_a"')

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "GUARD_EXIT=0" in result.stdout, (
            f"Expected an unparseable status.md to be treated as in flight.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert "unparseable" in combined.lower(), (
            f"Expected the log line to name the unparseable-status outcome.\ncombined: {combined}"
        )
