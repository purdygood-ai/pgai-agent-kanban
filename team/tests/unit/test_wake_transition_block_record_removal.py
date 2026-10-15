"""
test_wake_transition_block_record_removal.py
============================================
``transition_task_state`` (team/scripts/lib/wake_task_transition.sh) removes
a task's ``## Block Record`` when it writes a terminal state (DONE or
WONT-DO), leaving every other section byte-identical, and leaves the record
alone on every other state.

The real wake_task_transition.sh is sourced whole (it self-sources its own
dependencies, block_record.sh included); the two wake_task_state.sh
functions it calls, ``set_state`` and ``mark_backlog``, are extracted from
the real file. Every fixture is a throwaway task folder and queue file under
pytest's tmp_path; nothing touches the live install.

Naming: function names describe behaviour under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import pathlib
import subprocess
import textwrap

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

# This file lives at team/tests/unit/; team/ is three parents up.
_LIB = pathlib.Path(__file__).resolve().parent.parent.parent / "scripts" / "lib"
_TASK_ID = "CODER-20260101-001-example-task"

_RECORD = (
    "## Block Record\n"
    "reason_code: gate_dirty_worktree\n"
    "owner_role: CODER\n"
    "attempts: 0\n"
    "first_blocked_at: 2026-01-01T00:00:00+00:00\n"
    "last_blocked_at: 2026-01-01T00:00:00+00:00\n"
    "cooldown_until: none\n"
    "evidence: worktree=gone; branch=ai_feature/example; ahead=1; merged=no\n"
    "resume: fresh\n"
    "\n"
)

_STATUS = (
    "# Status\n\n## Task\n" + _TASK_ID + "\n\n## Role\nCODER\n\n## State\nBLOCKED\n\n"
    "## Summary\nBlocked on a dirty worktree.\n\n"
    + _RECORD
    + "## Needs Human\nyes\n\n## Instruction Conflicts\nnone\n"
)


def _transition(tmp_path: pathlib.Path, status: pathlib.Path, state: str) -> subprocess.CompletedProcess:
    queue = tmp_path / "coder_backlog.md"
    queue.write_text(f"# CODER backlog\n\n- [B] {_TASK_ID} — example\n", encoding="utf-8")
    (tmp_path / "tmp").mkdir(exist_ok=True)
    (tmp_path / "home").mkdir(exist_ok=True)
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source "{_LIB}/wake_task_transition.sh"
        eval "$(awk '/^(set_state|mark_backlog)\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{f=0}}' '{_LIB}/wake_task_state.sh')"
        BACKLOG="{queue}"
        transition_task_state "$1" "$2" "$3"
    """)
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={"PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "tmp")},
    )
    return subprocess.run(
        ["bash", "-c", script, "transition", _TASK_ID, str(status), state],
        capture_output=True, text=True, env=env, timeout=120,
    )


@pytest.mark.parametrize("state", ["DONE", "WONT-DO"])
def test_terminal_transition_removes_block_record_and_nothing_else(tmp_path: pathlib.Path, state: str) -> None:
    status = tmp_path / "status.md"
    status.write_text(_STATUS, encoding="utf-8")
    result = _transition(tmp_path, status, state)
    assert result.returncode == 0, result.stderr
    expected = _STATUS.replace(_RECORD, "").replace("## State\nBLOCKED\n", f"## State\n{state}\n")
    assert status.read_text(encoding="utf-8") == expected
    assert f"- [x] {_TASK_ID}" in (tmp_path / "coder_backlog.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("state", ["BACKLOG", "WAITING", "WORKING", "BLOCKED"])
def test_non_terminal_transition_keeps_block_record(tmp_path: pathlib.Path, state: str) -> None:
    status = tmp_path / "status.md"
    status.write_text(_STATUS, encoding="utf-8")
    result = _transition(tmp_path, status, state)
    assert result.returncode == 0, result.stderr
    assert status.read_text(encoding="utf-8") == _STATUS.replace("## State\nBLOCKED\n", f"## State\n{state}\n")


def test_terminal_transition_without_record_succeeds_unchanged(tmp_path: pathlib.Path) -> None:
    status = tmp_path / "status.md"
    status.write_text(_STATUS.replace(_RECORD, ""), encoding="utf-8")
    result = _transition(tmp_path, status, "DONE")
    assert result.returncode == 0, result.stderr
    assert status.read_text(encoding="utf-8") == _STATUS.replace(_RECORD, "").replace(
        "## State\nBLOCKED\n", "## State\nDONE\n"
    )


def test_failed_record_removal_never_fails_the_transition(tmp_path: pathlib.Path) -> None:
    status = tmp_path / "status.md"
    status.write_text(_STATUS, encoding="utf-8")
    queue = tmp_path / "coder_backlog.md"
    queue.write_text(f"- [B] {_TASK_ID}\n", encoding="utf-8")
    (tmp_path / "tmp").mkdir()
    (tmp_path / "home").mkdir()
    # The record remover itself fails; State and marker still land.
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source "{_LIB}/wake_task_transition.sh"
        eval "$(awk '/^(set_state|mark_backlog)\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{f=0}}' '{_LIB}/wake_task_state.sh')"
        block_record_remove() {{ return 1; }}
        BACKLOG="{queue}"
        transition_task_state "$1" "$2" DONE
    """)
    env = build_scrubbed_env(home_dir=tmp_path / "home", extra_env={"PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "tmp")})
    result = subprocess.run(["bash", "-c", script, "transition", _TASK_ID, str(status)],
                            capture_output=True, text=True, env=env, timeout=120)
    assert result.returncode == 0, result.stderr
    assert "Block Record not removed" in result.stderr
    assert "## State\nDONE\n" in status.read_text(encoding="utf-8")
    assert f"- [x] {_TASK_ID}" in queue.read_text(encoding="utf-8")
