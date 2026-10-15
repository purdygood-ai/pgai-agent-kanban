"""
test_prerequisite_archive_fallback.py
=====================================
Prerequisite resolution falls back to the project's archive for a task that
is no longer live, and treats an archived task as DONE.

Covered, every fixture in a sandbox kanban root under pytest's tmp_path:

  * ``archive.layout.archive_layout_for_tasks_dir`` maps a live
    ``<root>/projects/<project>/tasks`` directory to
    ``<root>/archive/<project>/`` and answers ``None`` for any other shape.
  * ``archive.lookup.archived_task_ids`` returns only IDs that have no live
    folder and are archived; a live folder always wins, even one with no
    status.md; malformed IDs and missing archives contribute nothing.
  * The module CLI verb ``archived_tasks`` prints exactly those IDs.
  * The wake gate ``check_prerequisites`` (team/scripts/lib/
    wake_task_id_gate.sh, sourced for real) resolves an archived
    prerequisite as DONE, reads a live prerequisite from its live status.md
    first, still reports a prerequisite that is neither live nor archived
    as MISSING, and keeps MISSING when the archive lookup itself fails.
  * Sourcing the gate keeps a pp_run_ops the caller defined first (a
    wrapper or a test stub) as the active definition, and its archive
    lookup goes through that definition; with none defined first, the gate
    loads pp_run_ops.sh itself and an archived prerequisite still resolves.
  * The existing WAITING re-check (``recheck_waiting_tasks``,
    wake_task_state.sh) promotes a WAITING task whose prerequisite was
    archived to BACKLOG.
  * ``ops.rc_state`` check d does not report an archived prerequisite as a
    dangling reference, and still reports one that is neither live nor
    archived.

Nothing here reads or writes the live install: every subprocess gets
KANBAN_ROOT and PGAI_AGENT_KANBAN_ROOT_PATH pointing at the sandbox, and
the archive the code consults is derived from the sandbox TASKS_ROOT.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

from pgai_agent_kanban.archive import archive_layout_for_tasks_dir, archived_task_ids
from pgai_agent_kanban.ops.rc_state import _prerequisite_findings

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_LIB = _TEAM_DIR / "scripts" / "lib"
_TASK_ID_GATE = _LIB / "wake_task_id_gate.sh"
_WAKE_TASK_STATE = _LIB / "wake_task_state.sh"

_PROJECT = "demo-project"
_ARCHIVED_ID = "CM-20260101-001-open-rc"
_LIVE_ID = "CODER-20260101-002-live-work"
_GONE_ID = "CODER-20260101-003-never-existed"
_WAITER_ID = "CODER-20260101-005-waits-on-prereqs"


# ---------------------------------------------------------------------------
# Sandbox helpers
# ---------------------------------------------------------------------------


def _kanban(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "kanban"
    (root / "projects" / _PROJECT / "tasks" / "queues").mkdir(parents=True)
    (root / "tmp").mkdir()
    return root


def _live_tasks(root: pathlib.Path) -> pathlib.Path:
    return root / "projects" / _PROJECT / "tasks"


def _archive_tasks(root: pathlib.Path) -> pathlib.Path:
    return root / "archive" / _PROJECT / "tasks"


def _write_status(task_dir: pathlib.Path, state: str) -> pathlib.Path:
    task_dir.mkdir(parents=True, exist_ok=True)
    status = task_dir / "status.md"
    status.write_text(f"# Status\n\n## State\n{state}\n\n## Summary\nfixture.\n\n## Blockers\nnone\n",
                      encoding="utf-8")
    return status


def _write_readme(task_dir: pathlib.Path, prerequisites: list[str]) -> pathlib.Path:
    task_dir.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"- {p}" for p in prerequisites) if prerequisites else "none"
    readme = task_dir / "README.md"
    readme.write_text(f"# Task\n\n## Prerequisites\n{body}\n\n## Goal\nfixture\n", encoding="utf-8")
    return readme


def _archive_task(root: pathlib.Path, task_id: str, state: str = "DONE") -> pathlib.Path:
    return _write_status(_archive_tasks(root) / task_id, state).parent


def _bash_header(root: pathlib.Path, tasks_root: pathlib.Path, backlog: pathlib.Path | None = None,
                 before_source: str = "") -> str:
    """Point every inherited root at the sandbox, then source the real gate.

    The two pre-resolved exports are cleared rather than repointed, per the
    live-install-safety rule; PGAI_PROJECT_NAME is cleared so a refusal has
    no project ledger to record into. *before_source* is bash run after the
    sandbox is set up and before the gate (or wake_task_state.sh) is
    sourced — where a caller's own pp_run_ops would already be defined.
    """
    source_lib = _WAKE_TASK_STATE if backlog is not None else _TASK_ID_GATE
    return textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -uo pipefail
        unset PGAI_TASKS_DIR PGAI_PROJECT_ROOT PGAI_PROJECT_NAME
        export KANBAN_ROOT="{root}"
        export PGAI_AGENT_KANBAN_ROOT_PATH="{root}"
        export PGAI_AGENT_KANBAN_TEMP_DIR="{root / 'tmp'}"
        TASKS_ROOT="{tasks_root}"
        BACKLOG="{backlog or ''}"
        log() {{ printf 'LOG: %s\\n' "$*"; }}
        get_release_state_field() {{ echo none; }}
    """) + before_source + f'source "{source_lib}"\n'


def _run(tmp_path: pathlib.Path, script: str) -> subprocess.CompletedProcess:
    script_path = tmp_path / "run_script.sh"
    script_path.write_text(script, encoding="utf-8")
    return subprocess.run(["bash", str(script_path)], capture_output=True, text=True,
                          timeout=60, cwd=str(tmp_path), stdin=subprocess.DEVNULL)


def _check(tmp_path: pathlib.Path, root: pathlib.Path, readme: pathlib.Path, prelude: str = "",
           before_source: str = "") -> tuple[list[str], str]:
    """Run check_prerequisites on *readme*; return (stdout lines, stderr)."""
    script = _bash_header(root, _live_tasks(root), before_source=before_source) + prelude + textwrap.dedent(f"""\
        check_prerequisites "{readme}"
        echo "rc=$?"
    """)
    result = _run(tmp_path, script)
    return result.stdout.splitlines(), result.stderr


# ---------------------------------------------------------------------------
# layout / lookup
# ---------------------------------------------------------------------------


def test_live_tasks_directory_maps_to_its_own_kanban_roots_archive(tmp_path: pathlib.Path) -> None:
    layout = archive_layout_for_tasks_dir(tmp_path / "projects" / _PROJECT / "tasks")
    assert layout is not None
    assert layout.root == tmp_path / "archive" / _PROJECT
    assert layout.tasks == tmp_path / "archive" / _PROJECT / "tasks"


@pytest.mark.parametrize(
    "relative",
    ["tasks", "projects/tasks", f"elsewhere/{_PROJECT}/tasks", f"projects/{_PROJECT}/queues", "projects/Bad_Name/tasks"],
    ids=["single-project-root", "no-project", "not-under-projects", "not-tasks", "unsafe-project-name"],
)
def test_tasks_directory_outside_the_live_project_shape_has_no_archive(tmp_path: pathlib.Path, relative: str) -> None:
    assert archive_layout_for_tasks_dir(tmp_path / relative) is None


def test_archived_task_ids_returns_only_archived_tasks_with_no_live_folder(tmp_path: pathlib.Path) -> None:
    root = _kanban(tmp_path)
    _archive_task(root, _ARCHIVED_ID)
    _write_status(_live_tasks(root) / _LIVE_ID, "WORKING")
    _archive_task(root, _LIVE_ID)  # an archived copy never shadows a live folder
    ids = [_GONE_ID, _ARCHIVED_ID, _LIVE_ID, _ARCHIVED_ID, "../x", ".."]
    assert archived_task_ids(_live_tasks(root), ids) == [_ARCHIVED_ID]


def test_live_folder_without_status_is_never_answered_from_the_archive(tmp_path: pathlib.Path) -> None:
    root = _kanban(tmp_path)
    (_live_tasks(root) / _ARCHIVED_ID).mkdir()
    _archive_task(root, _ARCHIVED_ID)
    assert archived_task_ids(_live_tasks(root), [_ARCHIVED_ID]) == [], (
        "a live folder whose status.md is missing is unreadable live state; fail closed, not DONE"
    )


def test_archived_task_ids_with_no_archive_directory_is_empty(tmp_path: pathlib.Path) -> None:
    root = _kanban(tmp_path)
    assert archived_task_ids(_live_tasks(root), [_ARCHIVED_ID, _GONE_ID]) == []


def test_archived_tasks_cli_prints_archived_ids_one_per_line(tmp_path: pathlib.Path) -> None:
    root = _kanban(tmp_path)
    _archive_task(root, _ARCHIVED_ID)
    env = {k: v for k, v in os.environ.items() if k not in ("PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT")}
    env.update({"KANBAN_ROOT": str(root), "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
                "PYTHONPATH": str(_TEAM_DIR)})
    result = subprocess.run(
        [sys.executable, "-m", "pgai_agent_kanban.archive", "archived_tasks",
         "--tasks-root", str(_live_tasks(root)), _GONE_ID, _ARCHIVED_ID],
        capture_output=True, text=True, timeout=60, env=env, cwd=str(tmp_path), stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{_ARCHIVED_ID}\n"


# ---------------------------------------------------------------------------
# wake gate: check_prerequisites
# ---------------------------------------------------------------------------


def test_archived_prerequisite_resolves_as_done(tmp_path: pathlib.Path) -> None:
    root = _kanban(tmp_path)
    _archive_task(root, _ARCHIVED_ID)
    readme = _write_readme(_live_tasks(root) / _WAITER_ID, [_ARCHIVED_ID])
    lines, stderr = _check(tmp_path, root, readme)
    assert lines == ["rc=0"], f"an archived prerequisite must count as DONE\nstderr={stderr}"


def test_live_prerequisite_is_read_from_live_status_before_the_archive(tmp_path: pathlib.Path) -> None:
    root = _kanban(tmp_path)
    _write_status(_live_tasks(root) / _LIVE_ID, "WORKING")
    _archive_task(root, _LIVE_ID, "DONE")
    _archive_task(root, _ARCHIVED_ID)
    readme = _write_readme(_live_tasks(root) / _WAITER_ID, [_ARCHIVED_ID, _LIVE_ID])
    lines, stderr = _check(tmp_path, root, readme)
    assert lines == [f"{_LIVE_ID}:WORKING", "rc=1"], (
        f"the live WORKING state must win over an archived DONE copy\nstderr={stderr}"
    )


def test_prerequisite_neither_live_nor_archived_is_still_missing(tmp_path: pathlib.Path) -> None:
    root = _kanban(tmp_path)
    _archive_task(root, _ARCHIVED_ID)
    readme = _write_readme(_live_tasks(root) / _WAITER_ID, [_ARCHIVED_ID, _GONE_ID])
    lines, stderr = _check(tmp_path, root, readme)
    assert lines == [f"{_GONE_ID}:MISSING", "rc=1"], stderr


def test_missing_prerequisite_with_no_archive_reads_as_before(tmp_path: pathlib.Path) -> None:
    root = _kanban(tmp_path)
    readme = _write_readme(_live_tasks(root) / _WAITER_ID, [_GONE_ID])
    lines, stderr = _check(tmp_path, root, readme)
    assert lines == [f"{_GONE_ID}:MISSING", "rc=1"], stderr


def test_failed_archive_lookup_keeps_the_prerequisite_unsatisfied(tmp_path: pathlib.Path) -> None:
    root = _kanban(tmp_path)
    _archive_task(root, _ARCHIVED_ID)
    readme = _write_readme(_live_tasks(root) / _WAITER_ID, [_ARCHIVED_ID])
    lines, stderr = _check(tmp_path, root, readme, prelude="pp_run_ops() { return 4; }\n")
    assert lines == [f"{_ARCHIVED_ID}:MISSING", "rc=1"], f"a lookup fault must fail closed\nstderr={stderr}"
    assert "archive lookup for missing prerequisites" in stderr and "exited 4" in stderr, stderr


def test_malformed_prerequisite_is_not_satisfied_by_the_archive(tmp_path: pathlib.Path) -> None:
    root = _kanban(tmp_path)
    _archive_task(root, _ARCHIVED_ID)
    readme = _write_readme(_live_tasks(root) / _WAITER_ID, [_ARCHIVED_ID, "../x"])
    lines, _stderr = _check(tmp_path, root, readme)
    assert lines == ["../x:MALFORMED", "rc=1"]


# ---------------------------------------------------------------------------
# wake gate: loading the gate keeps a caller's pp_run_ops
# ---------------------------------------------------------------------------

_STUB_MARKER = "STUB-PP-RUN-OPS"


def test_loading_the_gate_keeps_an_already_defined_pp_run_ops(tmp_path: pathlib.Path) -> None:
    root = _kanban(tmp_path)
    stub = f'pp_run_ops() {{ echo "{_STUB_MARKER} $*"; }}\n'
    script = _bash_header(root, _live_tasks(root), before_source=stub) + textwrap.dedent("""\
        pp_run_ops pgai_agent_kanban.archive archived_tasks
        declare -f pp_run_ops
    """)
    result = _run(tmp_path, script)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    lines = result.stdout.splitlines()
    assert lines[0] == f"{_STUB_MARKER} pgai_agent_kanban.archive archived_tasks", (
        f"sourcing the gate must leave the caller's pp_run_ops active\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert _STUB_MARKER in result.stdout.split("pp_run_ops ()", 1)[1], (
        f"the active pp_run_ops definition must still be the stub\nstdout={result.stdout}"
    )


def test_archive_lookup_goes_through_a_pp_run_ops_defined_before_the_gate(tmp_path: pathlib.Path) -> None:
    # Nothing is archived on disk: only the caller's stub can answer that the
    # prerequisite is archived, so rc=0 proves the gate called the stub.
    root = _kanban(tmp_path)
    readme = _write_readme(_live_tasks(root) / _WAITER_ID, [_ARCHIVED_ID])
    stub = f'pp_run_ops() {{ echo "{_ARCHIVED_ID}"; }}\n'
    lines, stderr = _check(tmp_path, root, readme, before_source=stub)
    assert lines == ["rc=0"], f"the archive lookup must use the caller's pp_run_ops\nstderr={stderr}"


def test_archived_prerequisite_resolves_as_done_when_pp_run_ops_was_not_loaded_first(tmp_path: pathlib.Path) -> None:
    root = _kanban(tmp_path)
    _archive_task(root, _ARCHIVED_ID)
    readme = _write_readme(_live_tasks(root) / _WAITER_ID, [_ARCHIVED_ID])
    no_helper = textwrap.dedent("""\
        unset -f pp_run_ops pp_pythonpath 2>/dev/null
        if declare -F pp_run_ops >/dev/null; then echo "pp_run_ops defined before the gate"; exit 99; fi
    """)
    lines, stderr = _check(tmp_path, root, readme, before_source=no_helper)
    assert lines == ["rc=0"], (
        f"the gate must load pp_run_ops itself when no caller defined it\nstderr={stderr}"
    )


def test_waiting_task_with_archived_prerequisite_is_promoted_to_backlog(tmp_path: pathlib.Path) -> None:
    root = _kanban(tmp_path)
    tasks = _live_tasks(root)
    _archive_task(root, _ARCHIVED_ID)
    waiting_status = _write_status(tasks / _WAITER_ID, "WAITING")
    _write_readme(tasks / _WAITER_ID, [_ARCHIVED_ID])
    held_id = "CODER-20260101-006-waits-on-nothing-real"
    held_status = _write_status(tasks / held_id, "WAITING")
    _write_readme(tasks / held_id, [_GONE_ID])
    backlog = tasks / "queues" / "coder_backlog.md"
    backlog.write_text(f"# Coder Backlog\n\n- [W] {_WAITER_ID} — waits on an archived task\n"
                       f"- [W] {held_id} — waits on a task that never existed\n", encoding="utf-8")

    result = _run(tmp_path, _bash_header(root, tasks, backlog) + "recheck_waiting_tasks\n")

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "## State\nBACKLOG" in waiting_status.read_text(encoding="utf-8"), result.stdout + result.stderr
    assert "## State\nWAITING" in held_status.read_text(encoding="utf-8"), (
        "a prerequisite that is neither live nor archived must keep its task WAITING"
    )
    queue = backlog.read_text(encoding="utf-8")
    assert f"- [ ] {_WAITER_ID}" in queue and f"- [W] {held_id}" in queue, queue
    assert "promoted 1 task(s)" in result.stdout


# ---------------------------------------------------------------------------
# ops.rc_state check d
# ---------------------------------------------------------------------------


def test_rc_state_does_not_report_an_archived_prerequisite_as_dangling(tmp_path: pathlib.Path) -> None:
    root = _kanban(tmp_path)
    tasks = _live_tasks(root)
    archived = "CLAUDE-CM-20260101-001-open-rc"
    gone = "CLAUDE-CODER-20260101-009-never-existed"
    _archive_task(root, archived)
    _write_status(tasks / "CLAUDE-CODER-20260101-004-dependant", "WAITING")
    _write_readme(tasks / "CLAUDE-CODER-20260101-004-dependant", [archived, gone])

    findings = list(_prerequisite_findings(tasks))

    errors = [message for severity, message in findings if severity == "ERROR"]
    assert len(errors) == 1 and gone in errors[0], findings
    assert not any(archived in message for message in errors), "an archived prerequisite is not dangling"
    assert ("OK", "Prerequisites: 1 reference(s) resolved to archived (finished) tasks") in findings
