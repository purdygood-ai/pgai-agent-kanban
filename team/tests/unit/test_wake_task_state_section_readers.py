"""
test_wake_task_state_section_readers.py
=======================================
Behavioral fixtures for the status.md section readers in
team/scripts/lib/wake_task_state.sh: get_state, get_first_pending_task,
recheck_waiting_tasks, recheck_blocked_tasks, and recheck_blocked_tasks'
own ## Needs Human and ## Blocked Reason reads.

A reader must stop a section's body at the next ``##`` line exactly as the
writers in the same file do, so a section whose body is empty reads as
empty -- never as the heading and text of the section that follows it.
Populated values must read exactly as they did before.

Each empty-section fixture is built so that reading the FOLLOWING section's
text would change the observable outcome where the scan's own logic allows
it (a pending task skipped, a BLOCKED task promoted), so the assertion
discriminates a correct section boundary from one that runs past it.

The real wake_task_state.sh is sourced (it self-sources atomic_write.sh and
wake_task_id_gate.sh); log(), get_release_state_field and the transient
retry helpers are stubbed. Every fixture lives under pytest's tmp_path,
which the gated runner roots under $PGAI_AGENT_KANBAN_TEMP_DIR; no live task
folder or kanban install is read or written.

Naming: function names describe behavior under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import textwrap

import pytest

# This file lives at team/tests/unit/, so three parents up is team/.
_TEAM_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "scripts"
_WAKE_TASK_STATE_LIB = _TEAM_SCRIPTS_DIR / "lib" / "wake_task_state.sh"

_TASK_ID = "CODER-20260101-001-demo"
_OTHER_TASK_ID = "CODER-20260101-002-demo"

# The exact shape of an empty ## State whose next section reads DONE.
_EMPTY_STATE_FIXTURE = "## State\n\n## Summary\nDONE\n\n## Blockers\nnone\n"

_POPULATED_STATES = ["BACKLOG", "WAITING", "BLOCKED", "WORKING"]


def _run(tmp_path: pathlib.Path, calls: str) -> subprocess.CompletedProcess:
    """Source the real lib against a tmp_path queue and tasks root, then run
    *calls* (bash). The environment is built from an explicit base so no
    operator export reaches the lib."""
    temp_dir = tmp_path / "kanban_temp"
    temp_dir.mkdir(exist_ok=True)
    script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        BACKLOG="{tmp_path / 'coder_backlog.md'}"
        TASKS_ROOT="{tmp_path / 'tasks'}"
        log() {{ printf 'LOG: %s\\n' "$*"; }}
        get_release_state_field() {{ echo none; }}
        transient_retry_field() {{ printf '%s\\n' "${{3:-}}"; }}
        transient_retry_limit() {{ echo 3; }}
        source "{_WAKE_TASK_STATE_LIB}"
    """) + calls + "\n"
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "LANG": "C.UTF-8",
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_dir),
        "KANBAN_ROOT": str(temp_dir),
    }
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=60, env=env, cwd=str(tmp_path)
    )


def _explain(proc: subprocess.CompletedProcess) -> str:
    return f"rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"


def _task(tmp_path: pathlib.Path, task_id: str, status_text: str) -> pathlib.Path:
    """Create tasks/<task_id>/ with a prerequisite-free README and *status_text*."""
    task_dir = tmp_path / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "README.md").write_text("# Task\n\n## Prerequisites\nnone\n", encoding="utf-8")
    status_file = task_dir / "status.md"
    status_file.write_text(status_text, encoding="utf-8")
    return status_file


def _queue(tmp_path: pathlib.Path, *lines: str) -> None:
    (tmp_path / "coder_backlog.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _blocked_status(state: str, needs_human: str, blocked_reason: str) -> str:
    return (
        f"## State\n{state}\n\n## Summary\nold\n\n## Blockers\nnone\n\n"
        f"## Needs Human\n{needs_human}\n\n## Blocked Reason\n{blocked_reason}\n\n"
        "## Next Recommended Step\nwait for the Active RC to close\n"
    )


# --- get_state ---------------------------------------------------------------


def test_get_state_reads_empty_state_as_empty_not_next_section(tmp_path: pathlib.Path) -> None:
    status_file = tmp_path / "status.md"
    status_file.write_text(_EMPTY_STATE_FIXTURE, encoding="utf-8")
    proc = _run(tmp_path, f'printf "[%s]" "$(get_state "{status_file}")"')
    assert proc.returncode == 0, _explain(proc)
    assert proc.stdout == "[]", f"an empty ## State must read empty, never another section\n{_explain(proc)}"


@pytest.mark.parametrize(
    "text",
    ["## Summary\nx\n\n## State\n", "## Summary\nx\n\n## State", "## State\n## Summary\nx\n"],
    ids=["empty-last-section", "empty-last-section-no-newline", "no-blank-line-before-next-heading"],
)
def test_get_state_reads_empty_state_in_other_shapes(tmp_path: pathlib.Path, text: str) -> None:
    status_file = tmp_path / "status.md"
    status_file.write_text(text, encoding="utf-8")
    proc = _run(tmp_path, f'printf "[%s]" "$(get_state "{status_file}")"')
    assert proc.returncode == 0, _explain(proc)
    assert proc.stdout == "[]", _explain(proc)


@pytest.mark.parametrize("state", _POPULATED_STATES)
def test_get_state_reads_populated_state_unchanged(tmp_path: pathlib.Path, state: str) -> None:
    status_file = tmp_path / "status.md"
    status_file.write_text(f"## State\n{state.lower()}\n\n## Summary\nDONE\n", encoding="utf-8")
    proc = _run(tmp_path, f'printf "[%s]" "$(get_state "{status_file}")"')
    assert proc.returncode == 0, _explain(proc)
    assert proc.stdout == f"[{state}]", _explain(proc)


def test_get_state_refuses_missing_state_heading_naming_the_file(tmp_path: pathlib.Path) -> None:
    status_file = tmp_path / "status.md"
    status_file.write_text("## Summary\nDONE\n", encoding="utf-8")
    proc = _run(tmp_path, f'get_state "{status_file}"')
    assert proc.returncode != 0, _explain(proc)
    assert proc.stdout == "", _explain(proc)
    assert str(status_file) in proc.stderr, f"the refusal must name the status file\n{_explain(proc)}"


@pytest.mark.parametrize("state", _POPULATED_STATES)
def test_get_state_reads_back_what_set_state_wrote_into_empty_state(tmp_path: pathlib.Path, state: str) -> None:
    status_file = tmp_path / "status.md"
    status_file.write_text(_EMPTY_STATE_FIXTURE, encoding="utf-8")
    proc = _run(tmp_path, f'set_state "{status_file}" {state}\nprintf "[%s]" "$(get_state "{status_file}")"')
    assert proc.returncode == 0, _explain(proc)
    assert proc.stdout == f"[{state}]", f"reader and writer must agree on the ## State body\n{_explain(proc)}"


# --- get_first_pending_task ----------------------------------------------------


def test_pending_scan_serves_empty_state_task_instead_of_skipping_it(tmp_path: pathlib.Path) -> None:
    # Reading ## Summary as the state would skip this task and serve the next.
    _task(tmp_path, _TASK_ID, "## State\n\n## Summary\nBACKLOG\n")
    _task(tmp_path, _OTHER_TASK_ID, "## State\nBACKLOG\n")
    _queue(tmp_path, f"- [ ] {_TASK_ID}", f"- [ ] {_OTHER_TASK_ID}")
    proc = _run(tmp_path, "get_first_pending_task")
    assert proc.returncode == 0, _explain(proc)
    assert proc.stdout.strip().splitlines()[-1] == _TASK_ID, _explain(proc)


@pytest.mark.parametrize(("state", "served"), [("BACKLOG", True), ("WAITING", False), ("BLOCKED", False), ("WORKING", False)])
def test_pending_scan_reads_populated_state_unchanged(tmp_path: pathlib.Path, state: str, served: bool) -> None:
    _task(tmp_path, _TASK_ID, f"## State\n{state}\n\n## Summary\nBACKLOG\n")
    _queue(tmp_path, f"- [ ] {_TASK_ID}")
    proc = _run(tmp_path, "get_first_pending_task || echo NONE")
    assert proc.returncode == 0, _explain(proc)
    assert proc.stdout.strip().splitlines()[-1] == (_TASK_ID if served else "NONE"), _explain(proc)


# --- recheck_waiting_tasks -----------------------------------------------------


def test_waiting_scan_does_not_promote_empty_state_task(tmp_path: pathlib.Path) -> None:
    text = "## State\n\n## Summary\nWAITING\n\n## Blockers\nnone\n\n## Needs Human\nno\n"
    status_file = _task(tmp_path, _TASK_ID, text)
    _queue(tmp_path, f"- [ ] {_TASK_ID}")
    proc = _run(tmp_path, "recheck_waiting_tasks")
    assert proc.returncode == 0, _explain(proc)
    assert status_file.read_text(encoding="utf-8") == text, "an empty ## State is not WAITING"
    assert "no WAITING tasks to re-check" in proc.stdout, _explain(proc)


@pytest.mark.parametrize(("state", "promoted"), [("WAITING", True), ("BACKLOG", False), ("BLOCKED", False), ("WORKING", False)])
def test_waiting_scan_reads_populated_state_unchanged(tmp_path: pathlib.Path, state: str, promoted: bool) -> None:
    status_file = _task(tmp_path, _TASK_ID, f"## State\n{state}\n\n## Summary\nx\n\n## Blockers\nnone\n")
    _queue(tmp_path, f"- [ ] {_TASK_ID}")
    proc = _run(tmp_path, "recheck_waiting_tasks")
    assert proc.returncode == 0, _explain(proc)
    first_body = status_file.read_text(encoding="utf-8").split("\n")[1]
    assert first_body == ("BACKLOG" if promoted else state), _explain(proc)


def test_waiting_scan_recognises_extra_spacing_inside_state_heading(tmp_path: pathlib.Path) -> None:
    # Only the scan's reading is under test: the task is recognised as
    # WAITING and handed to the prerequisite check.
    _task(tmp_path, _TASK_ID, "##   State\nWAITING\n\n## Summary\nx\n\n## Blockers\nnone\n")
    _queue(tmp_path, f"- [ ] {_TASK_ID}")
    proc = _run(tmp_path, "recheck_waiting_tasks")
    assert proc.returncode == 0, _explain(proc)
    assert f"WAITING task {_TASK_ID}: prerequisites satisfied" in proc.stdout, _explain(proc)


# --- recheck_blocked_tasks -----------------------------------------------------


def test_blocked_scan_does_not_promote_empty_state_task(tmp_path: pathlib.Path) -> None:
    text = _blocked_status("", "no", "waiting on Active RC")
    status_file = _task(tmp_path, _TASK_ID, text)
    _queue(tmp_path, f"- [B] {_TASK_ID}")
    proc = _run(tmp_path, "recheck_blocked_tasks")
    assert proc.returncode == 0, _explain(proc)
    assert status_file.read_text(encoding="utf-8") == text, "an empty ## State is not BLOCKED"
    assert "no BLOCKED tasks to re-check" in proc.stdout, _explain(proc)


@pytest.mark.parametrize(("state", "promoted"), [("BLOCKED", True), ("BACKLOG", False), ("WAITING", False), ("WORKING", False)])
def test_blocked_scan_reads_populated_state_unchanged(tmp_path: pathlib.Path, state: str, promoted: bool) -> None:
    status_file = _task(tmp_path, _TASK_ID, _blocked_status(state, "no", "waiting on Active RC"))
    _queue(tmp_path, f"- [B] {_TASK_ID}")
    proc = _run(tmp_path, "recheck_blocked_tasks")
    assert proc.returncode == 0, _explain(proc)
    first_body = status_file.read_text(encoding="utf-8").split("\n")[1]
    assert first_body == ("BACKLOG" if promoted else state), _explain(proc)


@pytest.mark.parametrize(
    ("needs_human", "promoted"),
    [("no", True), ("No", True), ("yes", False), ("", False)],
    ids=["no", "no-mixed-case", "yes", "empty-reads-as-yes"],
)
def test_blocked_scan_reads_needs_human(tmp_path: pathlib.Path, needs_human: str, promoted: bool) -> None:
    # An empty ## Needs Human is followed by ## Blocked Reason; reading that
    # text as the flag would not equal "yes" and the task would be promoted.
    status_file = _task(tmp_path, _TASK_ID, _blocked_status("BLOCKED", needs_human, "waiting on Active RC"))
    _queue(tmp_path, f"- [B] {_TASK_ID}")
    proc = _run(tmp_path, "recheck_blocked_tasks")
    assert proc.returncode == 0, _explain(proc)
    first_body = status_file.read_text(encoding="utf-8").split("\n")[1]
    assert first_body == ("BACKLOG" if promoted else "BLOCKED"), _explain(proc)


@pytest.mark.parametrize(
    ("blocked_reason", "promoted"),
    [("waiting on Active RC", True), ("waiting on a credential", False), ("", False)],
    ids=["active-rc", "other-reason", "empty"],
)
def test_blocked_scan_reads_blocked_reason(tmp_path: pathlib.Path, blocked_reason: str, promoted: bool) -> None:
    # The section after an empty ## Blocked Reason mentions the Active RC;
    # reading it as the reason would clear the blocker and promote the task.
    status_file = _task(tmp_path, _TASK_ID, _blocked_status("BLOCKED", "no", blocked_reason))
    _queue(tmp_path, f"- [B] {_TASK_ID}")
    proc = _run(tmp_path, "recheck_blocked_tasks")
    assert proc.returncode == 0, _explain(proc)
    first_body = status_file.read_text(encoding="utf-8").split("\n")[1]
    assert first_body == ("BACKLOG" if promoted else "BLOCKED"), _explain(proc)
