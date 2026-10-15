"""
test_ops_lock_acquire_and_queue_line_moves.py
=============================================
Unit tests for two shared ops primitives a queue-line move under the repo
lock is built from:

  (A) pgai_agent_kanban.ops.locks.acquire_lock / HeldLock / repo_lock_path:
      the record is the five provenance lines; a second acquirer is refused
      as contended with the holder's fields; a dead holder's record never
      blocks reacquisition; a blank field is refused dual-channel and takes
      nothing; release removes only this holder's file; a bash
      pgai_lock_acquire holder and a Python acquirer exclude each other in
      both directions; repo_lock_path names the file pp_repo_lock_path
      prints; pid_in_own_ancestry is true for this process and its
      ancestors and false for a child, a dead pid and anything that is not
      a pid above 1.
  (B) pgai_agent_kanban.ops.write.queue_entries / append_queue_lines: whole
      lines read by the set_marker entry match, appended with markers as
      given, never duplicated, into a new file when none exists.

Every file lives under pytest's tmp_path.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import textwrap
import time

import pytest

from pgai_agent_kanban.lib.error_contract import parse_error_contract
from pgai_agent_kanban.ops.errors import OpsError
from pgai_agent_kanban.ops.locks import (
    LockContended,
    LockProvenanceIncomplete,
    acquire_lock,
    pid_in_own_ancestry,
    read_provenance,
    repo_lock_path,
)
from pgai_agent_kanban.ops.write import append_queue_lines, queue_entries, remove_marker
from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_LOCK_PROVENANCE = "scripts/lib/lock_provenance.sh"
_DEAD_PID = "2147483646"


@pytest.fixture
def lock_file(tmp_path: pathlib.Path) -> pathlib.Path:
    return repo_lock_path(tmp_path / "kanban", "demo-project")


# ---------------------------------------------------------------------------
# (A) acquire_lock
# ---------------------------------------------------------------------------


def test_repo_lock_path_matches_pp_repo_lock_path(tmp_path) -> None:
    root = tmp_path / "kanban"
    result = run_bash(
        tmp_path,
        'source scripts/lib/project_paths.sh; pp_repo_lock_path "demo-project"',
        extra_env={"KANBAN_ROOT": str(root), "PGAI_AGENT_KANBAN_ROOT_PATH": str(root)},
        cwd=_TEAM_DIR,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(repo_lock_path(root, "demo-project"))


def test_acquire_writes_the_five_provenance_lines_and_release_removes_the_file(lock_file) -> None:
    held = acquire_lock(lock_file, holder="archive", pid=os.getpid(), op="archive-apply", task="none")

    lines = lock_file.read_text(encoding="utf-8").splitlines()
    assert lines[:4] == ["holder=archive", f"pid={os.getpid()}", "op=archive-apply", "task=none"]
    assert len(lines) == 5 and lines[4].startswith("acquired_at=") and lines[4].endswith("Z")
    assert held.owns_path()

    assert held.release() is True
    assert not lock_file.exists()
    assert sorted(os.listdir(lock_file.parent)) == [], "a staging file was left behind"


def test_a_second_acquirer_is_contended_and_sees_the_holder(lock_file) -> None:
    with acquire_lock(lock_file, holder="wake-coder", pid="111", op="wake", task="CODER-1"):
        with pytest.raises(LockContended) as caught:
            acquire_lock(lock_file, holder="archive", pid=os.getpid(), op="archive-apply", task="none")

    busy = caught.value
    assert (busy.holder, busy.pid, busy.op, busy.task) == ("wake-coder", "111", "wake", "CODER-1")
    assert busy.describe() == "holder=wake-coder pid=111 op=wake task=CODER-1"
    assert not lock_file.exists(), "the first holder's release should have removed the file"


def test_a_dead_holders_record_never_blocks_reacquisition(lock_file) -> None:
    lock_file.parent.mkdir(parents=True)
    lock_file.write_text(
        f"holder=wake-coder\npid={_DEAD_PID}\nop=wake\ntask=CODER-1\nacquired_at=2026-01-01T00:00:00Z\n",
        encoding="utf-8",
    )
    litter = lock_file.with_name(f"{lock_file.name}.tmp.{_DEAD_PID}")
    litter.write_text("half-staged", encoding="utf-8")

    with acquire_lock(lock_file, holder="archive", pid=os.getpid(), op="archive-apply", task="none"):
        assert read_provenance(lock_file).startswith("holder=archive\n")
        assert not litter.exists(), "a dead process's staging file was not cleared"


def test_a_blank_provenance_field_is_refused_and_takes_nothing(lock_file) -> None:
    with pytest.raises(LockProvenanceIncomplete) as caught:
        acquire_lock(lock_file, holder="archive", pid=os.getpid(), op=" ", task="")

    contract = parse_error_contract(str(caught.value))
    assert contract and contract["error"] == "lock.provenance.incomplete"
    assert "op, task" in contract["message"]
    assert caught.value.missing == ("op", "task")
    assert not lock_file.exists() and not lock_file.parent.exists()


def test_release_leaves_a_file_another_process_put_in_its_place(lock_file) -> None:
    held = acquire_lock(lock_file, holder="archive", pid=os.getpid(), op="archive-apply", task="none")
    lock_file.unlink()
    lock_file.write_text("holder=other\npid=1\nop=o\ntask=none\n", encoding="utf-8")

    assert held.release() is False
    assert lock_file.read_text(encoding="utf-8").startswith("holder=other\n")


class _BashHolder:
    """A bash process holding *lock_file* through pgai_lock_acquire until release()."""

    def __init__(self, tmp_path: pathlib.Path, lock_file: pathlib.Path) -> None:
        self.ready = tmp_path / "holder-ready"
        self.done = tmp_path / "holder-done"
        script = textwrap.dedent(f"""\
            source {_LOCK_PROVENANCE}
            fd=""
            pgai_lock_acquire "{lock_file}" wake-tester "$$" wake TESTER-7 fd || exit 9
            touch "{self.ready}"
            while [[ ! -f "{self.done}" ]]; do sleep 0.05; done
            pgai_lock_release "{lock_file}" "$fd"
        """)
        self.proc = subprocess.Popen(["bash", "-c", script], cwd=str(_TEAM_DIR))
        deadline = time.time() + 10
        while not self.ready.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert self.ready.exists(), "the bash lock holder did not start"

    def release(self) -> None:
        self.done.touch()
        self.proc.wait(timeout=10)


def test_a_bash_holder_excludes_a_python_acquirer(tmp_path, lock_file) -> None:
    lock_file.parent.mkdir(parents=True)
    holder = _BashHolder(tmp_path, lock_file)
    try:
        with pytest.raises(LockContended) as caught:
            acquire_lock(lock_file, holder="archive", pid=os.getpid(), op="archive-apply", task="none")
    finally:
        holder.release()

    assert (caught.value.holder, caught.value.op, caught.value.task) == ("wake-tester", "wake", "TESTER-7")
    with acquire_lock(lock_file, holder="archive", pid=os.getpid(), op="archive-apply", task="none"):
        pass


def test_a_python_holder_excludes_a_bash_acquirer(tmp_path, lock_file) -> None:
    with acquire_lock(lock_file, holder="archive", pid=os.getpid(), op="archive-apply", task="none"):
        result = run_bash(
            tmp_path,
            f'source {_LOCK_PROVENANCE}; fd=""; pgai_lock_acquire "{lock_file}" wake-pm "$$" wake none fd; echo "rc=$?"',
            cwd=_TEAM_DIR,
        )

    assert "rc=1" in result.stdout, result.stdout + result.stderr
    assert "holder=archive" in result.stderr and "op=archive-apply" in result.stderr


# ---------------------------------------------------------------------------
# (A) pid_in_own_ancestry
# ---------------------------------------------------------------------------


def test_pid_in_own_ancestry_is_true_for_this_process_and_its_ancestors() -> None:
    assert pid_in_own_ancestry(os.getpid())
    assert pid_in_own_ancestry(str(os.getppid()))
    assert pid_in_own_ancestry(f"  {os.getppid()}\n"), "surrounding whitespace is tolerated"


def test_pid_in_own_ancestry_is_true_for_the_bash_parent_of_a_child_process(tmp_path) -> None:
    # A bash parent runs a python child; the child sees the parent's pid as
    # its own chain -- the shape of a wake chain and the agent it starts.
    probe = "from pgai_agent_kanban.ops.locks import pid_in_own_ancestry as p; import sys; print(p(sys.argv[1]))"
    result = run_bash(
        tmp_path,
        f'python3 -c "{probe}" "$$"; (python3 -c "{probe}" "$$")',
        extra_env={"PYTHONPATH": str(_TEAM_DIR)},
        cwd=_TEAM_DIR,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["True", "True"], result.stdout + result.stderr


def test_pid_in_own_ancestry_is_false_for_a_child_a_dead_pid_and_non_pids() -> None:
    child = subprocess.Popen(["sleep", "60"])
    try:
        assert not pid_in_own_ancestry(child.pid), "a child is not an ancestor"
    finally:
        child.kill()
        child.wait(timeout=30)
    for value in (_DEAD_PID, "", None, "abc", "12x", "-5", "0", "1", 1):
        assert not pid_in_own_ancestry(value), repr(value)


# ---------------------------------------------------------------------------
# (B) queue_entries / append_queue_lines
# ---------------------------------------------------------------------------

_QUEUE = (
    "# Coder Backlog\n\n## Queue\n\n"
    "- [x] CODER-20260301-001-alpha — first\n"
    "- [A] CODER-20260301-002-beta\n"
    "- [x] CODER-20260301-001-alpha-two — a longer key\n"
    "-  [x]  CODER-20260301-001-alpha   — spaced duplicate\n"
)


def test_queue_entries_returns_whole_lines_for_the_key_only(tmp_path) -> None:
    queue = tmp_path / "coder_backlog.md"
    queue.write_text(_QUEUE, encoding="utf-8")

    assert queue_entries(queue, "CODER-20260301-001-alpha") == [
        "- [x] CODER-20260301-001-alpha — first",
        "-  [x]  CODER-20260301-001-alpha   — spaced duplicate",
    ]
    assert queue_entries(queue, "CODER-20260301-009-none") == []
    assert queue_entries(tmp_path / "absent.md", "CODER-20260301-001-alpha") == []


def test_queue_entries_are_exactly_the_lines_remove_marker_deletes(tmp_path) -> None:
    queue = tmp_path / "coder_backlog.md"
    queue.write_text(_QUEUE, encoding="utf-8")
    entries = queue_entries(queue, "CODER-20260301-001-alpha")

    remove_marker(queue, "CODER-20260301-001-alpha")

    remaining = queue.read_text(encoding="utf-8").splitlines()
    assert remaining == [line for line in _QUEUE.splitlines() if line not in entries]


def test_append_queue_lines_keeps_markers_and_skips_lines_already_present(tmp_path) -> None:
    queue = tmp_path / "coder_backlog.md"
    queue.write_text("# Coder Backlog\n\n## Queue\n\n- [ ] CODER-20260301-003-gamma", encoding="utf-8")
    lines = ["- [x] CODER-20260301-001-alpha — first", "- [A] CODER-20260301-002-beta"]

    assert append_queue_lines(queue, lines) == 2
    assert append_queue_lines(queue, lines) == 0

    assert queue.read_text(encoding="utf-8") == (
        "# Coder Backlog\n\n## Queue\n\n- [ ] CODER-20260301-003-gamma\n"
        "- [x] CODER-20260301-001-alpha — first\n- [A] CODER-20260301-002-beta\n"
    )


def test_append_queue_lines_creates_a_missing_file(tmp_path) -> None:
    queue = tmp_path / "bug_backlog.md"

    assert append_queue_lines(queue, ["- [x] BUG-0001-item-1"]) == 1

    assert queue.read_text(encoding="utf-8") == "- [x] BUG-0001-item-1\n"


def test_append_queue_lines_refuses_a_line_break_and_writes_nothing(tmp_path) -> None:
    queue = tmp_path / "bug_backlog.md"

    with pytest.raises(OpsError):
        append_queue_lines(queue, ["- [x] BUG-0001\n- [x] BUG-0002"])

    assert not queue.exists()
