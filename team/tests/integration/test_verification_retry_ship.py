"""
test_verification_retry_ship.py
===============================
The requeue-then-ship sequence for a release whose verification did not
run, driven through the real cm-open-rc.sh and cm-release.sh against a
throwaway kanban root and git repository (the fixture builders of
test_ship_policy_acceptance.py; every path is under pytest's tmp_path).

Coverage:
  - TESTER task BLOCKED, under both ship postures: the first release
    attempt writes no project HALT file, ships nothing (exit 4), and returns
    the TESTER task to BACKLOG once with its block cause in the README's
    ## Triage Note and the status.md Summary; run inside its CM wake, the
    release task is set to WAITING. The same task BLOCKED again: the second
    attempt ships with Notes Status NON-FUNCTIONAL, a "Verification did not
    run" line under ## Known Issues, one operator inbox entry naming the
    TESTER task and its block cause, and no second requeue.
  - Missing ordered report (a TESTER task that reads DONE with no
    artifacts/report.md): the same requeue once, then the NON-FUNCTIONAL
    ship with the notes line and the inbox entry.
  - --decide-only reports REQUEUE-VERIFICATION (exit 4) and writes nothing.
  - After the requeue, while the requeued TESTER task is still BACKLOG,
    WORKING or WAITING: a re-run exits 4 again (VERIFICATION-PENDING, also
    under --decide-only), requeues nothing a second time, and leaves
    release-state.md, the HALT paths, operator-inbox.md, the retry record
    and the TESTER task byte-identical; run inside its CM wake, the release
    task is set to WAITING again. Once that task is BLOCKED or WONT-DO, the
    next run ships NON-FUNCTIONAL as unverified.
"""

from __future__ import annotations

import pathlib

import pytest

from tests.integration.test_cm_release_synthetic import _git_tag_exists, _run_cm_script
from tests.integration.test_ship_policy_acceptance import (
    _RELEASE_SCRIPT,
    _add_tester_task,
    _open_rc_with_one_commit,
    _release_notes_text,
)

# provenance-allowlist: synthetic fixture task IDs (test DATA the release
# engine reads), not references to real tasks.
_TESTER_ID = "TESTER-20260601-001-verify-release"
_CM_ID = "CM-20260601-002-release"


def _status(path: pathlib.Path, state: str, blockers: str = "none") -> None:
    path.write_text(
        f"# Status\n\n## Task\n{path.parent.name}\n\n## State\n{state}\n\n## Summary\nfixture\n\n"
        f"## Blockers\n{blockers}\n\n## Needs Human\nno\n",
        encoding="utf-8",
    )


def _tester_task(kanban_root: pathlib.Path, project: str, version: str, *, state: str, blockers: str) -> pathlib.Path:
    """A TESTER task for *version* with no report, a queue line, and *state*."""
    report = _add_tester_task(kanban_root, project, version, task_id=_TESTER_ID, state=state)
    report.unlink()
    task_dir = report.parent.parent
    _status(task_dir / "status.md", state, blockers)
    marker = "B" if state == "BLOCKED" else "x"
    (kanban_root / "projects" / project / "tasks" / "queues" / "tester_backlog.md").write_text(
        f"# Tester Backlog\n\n- [{marker}] {_TESTER_ID} — verify {version}\n", encoding="utf-8"
    )
    return task_dir


def _cm_task(kanban_root: pathlib.Path, project: str) -> pathlib.Path:
    task_dir = kanban_root / "projects" / project / "tasks" / _CM_ID
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "README.md").write_text(f"# Task: {_CM_ID}\n\n## Role\nCM\n", encoding="utf-8")
    _status(task_dir / "status.md", "WORKING")
    return task_dir


def _section(text: str, heading: str) -> str:
    """The body of ``## <heading>`` in *text*, up to the next level-2 heading."""
    tail = text.split(f"## {heading}\n", 1)[1]
    return tail.split("\n## ", 1)[0]


def _inbox_open_entries(kanban_root: pathlib.Path, project: str) -> list:
    inbox = kanban_root / "projects" / project / "operator-inbox.md"
    if not inbox.exists():
        return []
    open_body = _section(inbox.read_text(encoding="utf-8"), "Open")
    return [chunk for chunk in open_body.split("### INBOX-")[1:]]


def _release(kanban_root, project, repo_path, *args, extra_env=None):
    return _run_cm_script(
        _RELEASE_SCRIPT, list(args), kanban_root, project, repo_root=repo_path, extra_env=extra_env,
    )


def _assert_requeued(result, kanban_root, project, version, task_dir, cause_fragment):
    out = f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert result.returncode == 4, f"the first attempt requeues verification and ships nothing\n{out}"
    assert "REQUEUE-VERIFICATION" in result.stdout, out
    assert not (kanban_root / "projects" / project / "HALT").exists(), f"no HALT file is written\n{out}"
    assert not (kanban_root / "HALT").exists()
    status = (task_dir / "status.md").read_text(encoding="utf-8")
    assert "## State\nBACKLOG" in status, status
    assert "## Needs Human\nno" in status, status
    assert cause_fragment in _section(status, "Summary"), status
    note = _section((task_dir / "README.md").read_text(encoding="utf-8"), "Triage Note")
    assert cause_fragment in note and version in note, note
    queue = (kanban_root / "projects" / project / "tasks" / "queues" / "tester_backlog.md").read_text(
        encoding="utf-8"
    )
    assert f"- [ ] {_TESTER_ID}" in queue, queue
    record = kanban_root / "projects" / project / "release-state" / f"verification-retry-{version}"
    assert record.is_file(), "the RC's one requeue is recorded"
    assert f"tester_task={_TESTER_ID}" in record.read_text(encoding="utf-8")


def _assert_shipped_unverified(result, kanban_root, project, version, repo_path, task_dir, cause_fragment):
    out = f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert result.returncode == 0, f"the second failure ships, never HALTs\n{out}"
    assert not (kanban_root / "projects" / project / "HALT").exists(), out
    assert _git_tag_exists(repo_path, version), out
    notes = _release_notes_text(repo_path, version)
    assert _section(notes, "Status").strip().startswith("NON-FUNCTIONAL"), notes
    assert "Verification did not run" in _section(notes, "Known Issues"), notes
    assert _TESTER_ID not in notes, "no task ID reaches the shipped notes"
    entries = _inbox_open_entries(kanban_root, project)
    assert len(entries) == 1, f"one operator inbox entry; got {entries}\n{out}"
    assert f"**Source:** {_TESTER_ID}" in entries[0] and _TESTER_ID in entries[0], entries[0]
    assert cause_fragment in entries[0], entries[0]
    assert "verification did not run" in entries[0], entries[0]
    record = kanban_root / "projects" / project / "release-state" / f"verification-retry-{version}"
    assert record.read_text(encoding="utf-8").count("requeued_at=") == 1


@pytest.mark.parametrize("posture", ["matrix", "always"])
def test_blocked_tester_is_requeued_once_then_the_release_ships_non_functional(
    tmp_path: pathlib.Path, posture: str
) -> None:
    version, project = "v0.3.0", "verify-blocked-project"
    kanban_root, repo_path = _open_rc_with_one_commit(tmp_path, project, version)
    if posture == "always":
        cfg = kanban_root / "projects" / project / "project.cfg"
        cfg.write_text(cfg.read_text(encoding="utf-8") + "\n[release]\nship_posture = always\n", encoding="utf-8")
    task_dir = _tester_task(
        kanban_root, project, version, state="BLOCKED", blockers="The suite runner could not start: no python3 venv."
    )
    cm_dir = _cm_task(kanban_root, project)

    first = _release(kanban_root, project, repo_path, extra_env={"PGAI_TASK_ID": _CM_ID})
    _assert_requeued(first, kanban_root, project, version, task_dir, "no python3 venv")
    assert not _git_tag_exists(repo_path, version), "nothing ships on the requeue"
    cm_status = (cm_dir / "status.md").read_text(encoding="utf-8")
    assert "## State\nWAITING" in cm_status, cm_status
    assert _inbox_open_entries(kanban_root, project) == [], "the requeue itself asks the operator nothing"

    # The requeued attempt blocks again.
    _status(task_dir / "status.md", "BLOCKED", "The suite runner still could not start: disk full.")
    second = _release(kanban_root, project, repo_path)
    _assert_shipped_unverified(second, kanban_root, project, version, repo_path, task_dir, "disk full")
    status = (task_dir / "status.md").read_text(encoding="utf-8")
    assert "## State\nBLOCKED" in status, "the second failure never requeues again"


def test_missing_ordered_report_is_requeued_once_then_the_release_ships_non_functional(
    tmp_path: pathlib.Path,
) -> None:
    version, project = "v0.3.1", "verify-missing-report-project"
    kanban_root, repo_path = _open_rc_with_one_commit(tmp_path, project, version)
    task_dir = _tester_task(kanban_root, project, version, state="DONE", blockers="none")

    first = _release(kanban_root, project, repo_path)
    _assert_requeued(first, kanban_root, project, version, task_dir, "report.md does not exist")
    assert not _git_tag_exists(repo_path, version)

    # The requeued attempt ends DONE without a report again.
    _status(task_dir / "status.md", "DONE")
    second = _release(kanban_root, project, repo_path)
    _assert_shipped_unverified(second, kanban_root, project, version, repo_path, task_dir, "report.md does not exist")
    bugs = list((kanban_root / "projects" / project / "bugs").glob("BUG-*.md"))
    assert bugs == [], f"a missing report is verification that did not run, not a report-value bug: {bugs}"


def test_decide_only_reports_the_requeue_and_writes_nothing(tmp_path: pathlib.Path) -> None:
    version, project = "v0.3.2", "verify-decide-only-project"
    kanban_root, repo_path = _open_rc_with_one_commit(tmp_path, project, version)
    task_dir = _tester_task(kanban_root, project, version, state="BLOCKED", blockers="runner crashed")
    before = (task_dir / "status.md").read_text(encoding="utf-8")

    result = _release(kanban_root, project, repo_path, "--decide-only")
    assert result.returncode == 4, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert result.stdout.startswith("ship-policy: decision=REQUEUE-VERIFICATION "), result.stdout
    assert (task_dir / "status.md").read_text(encoding="utf-8") == before
    assert not (kanban_root / "projects" / project / "release-state" / f"verification-retry-{version}").exists()
    assert not (kanban_root / "projects" / project / "HALT").exists()


def _watched_bytes(kanban_root: pathlib.Path, project: str, version: str, task_dir: pathlib.Path) -> dict:
    """The files a pending re-run must leave untouched (None when absent)."""
    project_root = kanban_root / "projects" / project
    paths = [
        project_root / "release-state.md",
        project_root / "HALT",
        kanban_root / "HALT",
        project_root / "operator-inbox.md",
        project_root / "release-state" / f"verification-retry-{version}",
        task_dir / "status.md",
        task_dir / "README.md",
    ]
    return {str(p): (p.read_bytes() if p.exists() else None) for p in paths}


def _requeue_then_pend(tmp_path, project, version, pending_state):
    """Open the RC, requeue its BLOCKED TESTER task once, then mark it *pending_state*."""
    kanban_root, repo_path = _open_rc_with_one_commit(tmp_path, project, version)
    task_dir = _tester_task(
        kanban_root, project, version, state="BLOCKED", blockers="The suite runner could not start: no python3 venv."
    )
    cm_dir = _cm_task(kanban_root, project)
    first = _release(kanban_root, project, repo_path, extra_env={"PGAI_TASK_ID": _CM_ID})
    _assert_requeued(first, kanban_root, project, version, task_dir, "no python3 venv")
    _status(task_dir / "status.md", pending_state)
    return kanban_root, repo_path, task_dir, cm_dir


@pytest.mark.parametrize("pending_state", ["BACKLOG", "WORKING", "WAITING"])
def test_a_rerun_while_the_requeued_task_is_pending_exits_4_and_writes_nothing(
    tmp_path: pathlib.Path, pending_state: str
) -> None:
    version, project = "v0.3.3", "verify-pending-project"
    kanban_root, repo_path, task_dir, cm_dir = _requeue_then_pend(tmp_path, project, version, pending_state)
    # The wake promoted the release task again (e.g. no ## Prerequisites line).
    _status(cm_dir / "status.md", "WORKING")
    before = _watched_bytes(kanban_root, project, version, task_dir)

    decided = _release(kanban_root, project, repo_path, "--decide-only")
    assert decided.returncode == 4, f"stdout: {decided.stdout}\nstderr: {decided.stderr}"
    assert decided.stdout.startswith("ship-policy: decision=VERIFICATION-PENDING "), decided.stdout

    rerun = _release(kanban_root, project, repo_path, extra_env={"PGAI_TASK_ID": _CM_ID})
    out = f"stdout: {rerun.stdout}\nstderr: {rerun.stderr}"
    assert rerun.returncode == 4, f"a pending verification holds the release at exit 4\n{out}"
    assert "VERIFICATION-PENDING" in rerun.stdout, out
    assert "verification is still pending" in rerun.stdout, out
    assert "cm-release.verification-pending" in rerun.stderr, out
    assert _watched_bytes(kanban_root, project, version, task_dir) == before, (
        f"release-state.md, HALT, inbox, retry record and TESTER task are untouched\n{out}"
    )
    assert not _git_tag_exists(repo_path, version), "nothing ships while verification is pending"
    assert "## State\nWAITING" in (cm_dir / "status.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("final_state", ["BLOCKED", "WONT-DO"])
def test_a_pending_verification_that_fails_again_ships_non_functional(
    tmp_path: pathlib.Path, final_state: str
) -> None:
    version, project = "v0.3.4", "verify-pending-then-fails-project"
    kanban_root, repo_path, task_dir, _cm_dir = _requeue_then_pend(tmp_path, project, version, "WORKING")
    pending = _release(kanban_root, project, repo_path)
    assert pending.returncode == 4, f"stdout: {pending.stdout}\nstderr: {pending.stderr}"

    _status(task_dir / "status.md", final_state, "The suite runner still could not start: disk full.")
    shipped = _release(kanban_root, project, repo_path)
    _assert_shipped_unverified(shipped, kanban_root, project, version, repo_path, task_dir, "disk full")
