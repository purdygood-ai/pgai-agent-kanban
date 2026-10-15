"""
test_release_locks_inherited_flock.py
======================================
A lock is let go only when no process still holds the flock on the lock
file's original inode.

``release-locks.sh --release NAME --force`` (pgai_agent_kanban.ops.locks)
sends the recorded holder SIGTERM and waits for it to let go. A wake's exit
path unlinks its lock file, but a process that inherited the wake's lock
descriptor -- a provider CLI the wake launched -- keeps the flock on the old
inode after the path is gone and after the wake itself has exited. Reporting
that lock as released lets the next acquirer take a fresh file beside a
process that is still working.

Fixtures:
  (A) ``_holder_let_go`` reads False for a lock whose path has been unlinked
      while a separate process still holds the flock on the old inode, read
      through a probe descriptor opened before the unlink; True once that
      process has exited.
  (B) ``release_lock(..., force=True)`` against a real holder process whose
      SIGTERM handler unlinks its lock file and exits while its background
      child still holds the inherited descriptor: the call refuses with
      ``release_locks.holder_not_released`` (exit 5) naming the child pid in
      a runnable ``kill -TERM`` remedy, instead of reporting the holder let go.

  (C) ``release_lock`` against a lock whose recorded holder was killed while
      a child that inherited its lock descriptor still holds the flock:
      without ``force`` it refuses ``release_locks.orphan_holder`` (exit 4)
      naming the child and leaves the file byte-for-byte in place; with
      ``force`` it signals the child -- not only the recorded pid -- and
      swaps the file for an empty one once the flock is free.

Every helper process is a plain ``sleep`` or a short Python/bash process
started in its own session; teardown SIGKILLs each session whether or not the
assertions passed (docs/test-authoring-guidelines.md, "Own every process your
subprocess starts"). All files live under pytest's tmp_path.
"""

from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import sys
import textwrap
import time

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.lib.error_contract import parse_error_contract  # noqa: E402
from pgai_agent_kanban.ops import locks as ops_locks  # noqa: E402
from pgai_agent_kanban.ops.context import OpsContext  # noqa: E402

# A pid above the kernel's pid_max ceiling: never alive, so a record naming it
# reads as a holder that has exited.
_DEAD_PID = "999999999"
_STARTUP_TIMEOUT = 20


class _Sessions:
    """Every helper process a test starts; teardown kills each whole session."""

    def __init__(self) -> None:
        self.procs: list[subprocess.Popen] = []

    def start(self, argv: list[str]) -> subprocess.Popen:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        self.procs.append(proc)
        return proc

    def kill_all(self) -> None:
        for proc in self.procs:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                pass


@pytest.fixture()
def sessions():
    owned = _Sessions()
    try:
        yield owned
    finally:
        owned.kill_all()


def _wait_for_line(proc: subprocess.Popen, expected: str) -> None:
    """Block until *proc* prints *expected* on stdout (its readiness signal)."""
    deadline = time.monotonic() + _STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if line.strip() == expected:
            return
        if line == "" and proc.poll() is not None:
            break
    pytest.fail(f"helper never printed {expected!r}; stderr={proc.stderr.read() if proc.stderr else ''}")


def _wait_until_gone(pid: int) -> None:
    deadline = time.monotonic() + _STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    pytest.fail(f"pid {pid} did not exit")


def _flock_holder_argv(path: pathlib.Path) -> list[str]:
    """A process that opens *path* itself, takes an exclusive flock and sleeps."""
    code = textwrap.dedent(
        f"""\
        import fcntl, os, time
        fd = os.open({str(path)!r}, os.O_RDONLY)
        fcntl.flock(fd, fcntl.LOCK_EX)
        print("locked", flush=True)
        time.sleep(600)
        """
    )
    return [sys.executable, "-c", code]


def test_unlinked_lock_with_a_live_flock_on_the_old_inode_is_not_let_go(tmp_path, sessions) -> None:
    lock = tmp_path / "locks" / "repo-wake-demo.lock"
    lock.parent.mkdir()
    lock.write_text(f"holder=wake-coder\npid={_DEAD_PID}\nop=wake\ntask=none\n", encoding="utf-8")
    child = sessions.start(_flock_holder_argv(lock))
    _wait_for_line(child, "locked")

    probe_fd = os.open(lock, os.O_RDONLY)
    try:
        inode = os.fstat(probe_fd).st_ino
        lock.unlink()

        assert ops_locks._holder_let_go(lock, _DEAD_PID, inode, probe_fd) is False, (
            "the path is gone and the recorded pid is dead, but a live process still holds the "
            "flock on the old inode: the holder has not let go"
        )
        # The path-and-pid evidence alone would have said otherwise -- the
        # probe descriptor is what sees the surviving flock.
        assert ops_locks._holder_let_go(lock, _DEAD_PID, inode) is True

        os.killpg(child.pid, signal.SIGKILL)
        child.communicate(timeout=10)
        assert ops_locks._holder_let_go(lock, _DEAD_PID, inode, probe_fd) is True, (
            "once the last process holding the old inode's flock exits, the lock is let go"
        )
    finally:
        os.close(probe_fd)


def test_force_release_refuses_while_an_inherited_descriptor_still_holds_the_lock(tmp_path, sessions) -> None:
    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()
    lock = locks_dir / "repo-wake-demo.lock"
    child_pid_file = tmp_path / "child.pid"
    # The holder: writes its provenance, takes the flock on fd 9, starts a
    # background child that inherits fd 9, and on SIGTERM unlinks its lock
    # file and exits -- the release path a wake's exit trap takes -- leaving
    # the child holding the flock on the unlinked inode.
    holder_script = tmp_path / "holder.sh"
    holder_script.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            lock={str(lock)!r}
            printf 'holder=wake-coder\\npid=%s\\nop=wake\\ntask=none\\n' "$$" > "$lock"
            exec 9<"$lock"
            flock -n 9 || exit 3
            sleep 600 &
            echo "$!" > {str(child_pid_file)!r}
            trap 'rm -f -- "$lock"; exit 0' TERM
            echo ready
            while :; do wait; done
            """
        ),
        encoding="utf-8",
    )
    # Started under a wrapper shell that reaps it, as the wake's own parent
    # does: a holder left a zombie of this test process would still answer
    # kill -0 after it exited, which no real release-locks.sh caller sees.
    wrapper = sessions.start(["bash", "-c", 'bash "$0" & echo "holder=$!"; wait', str(holder_script)])
    first = wrapper.stdout.readline().strip()
    assert first.startswith("holder="), first
    holder_pid = int(first.split("=", 1)[1])
    _wait_for_line(wrapper, "ready")
    child_pid = child_pid_file.read_text(encoding="utf-8").strip()
    assert child_pid.isdigit()

    lines: list[str] = []
    with pytest.raises(ops_locks.LockRefusal) as caught:
        ops_locks.release_lock(
            OpsContext(kanban_root=tmp_path), "repo-wake-demo", force=True, say=lines.append, grace_seconds=2
        )

    _wait_until_gone(holder_pid)
    refusal = caught.value
    contract = parse_error_contract(str(refusal))
    assert contract is not None, f"the refusal must speak the dual-channel contract:\n{refusal}"
    assert refusal.exit_code == 5
    assert refusal.error == contract["error"] == "release_locks.holder_not_released"
    assert not lock.exists(), "fixture precondition: the holder's exit path unlinked the lock file"
    assert child_pid in contract["message"], f"the refusal must name the process still holding the flock:\n{refusal}"
    assert f"kill -TERM {child_pid}" in contract["remedy"], f"the remedy must be runnable as printed:\n{refusal}"
    assert not any("let go" in line for line in lines), f"no let-go may be reported:\n{lines}"


def _orphan_held_lock(tmp_path: pathlib.Path, sessions) -> tuple[pathlib.Path, str]:
    """A lock whose recorded holder was SIGKILLed while its child keeps the flock.

    Returns the lock path and the child's pid. The holder records its own
    pid, takes the flock on fd 9 and starts a ``sleep`` that inherits fd 9;
    the wrapper shell reaps the killed holder so it is not left a zombie
    that still answers kill -0.
    """
    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()
    lock = locks_dir / "repo-wake-demo.lock"
    child_pid_file = tmp_path / "child.pid"
    holder_script = tmp_path / "holder.sh"
    holder_script.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            lock={str(lock)!r}
            printf 'holder=wake-coder\\npid=%s\\nop=wake\\ntask=CODER-1\\n' "$$" > "$lock"
            exec 9<"$lock"
            flock -n 9 || exit 3
            sleep 600 &
            echo "$!" > {str(child_pid_file)!r}
            echo ready
            while :; do wait; done
            """
        ),
        encoding="utf-8",
    )
    wrapper = sessions.start(["bash", "-c", 'bash "$0" & echo "holder=$!"; wait; sleep 600', str(holder_script)])
    first = wrapper.stdout.readline().strip()
    assert first.startswith("holder="), first
    holder_pid = int(first.split("=", 1)[1])
    _wait_for_line(wrapper, "ready")
    child_pid = child_pid_file.read_text(encoding="utf-8").strip()
    assert child_pid.isdigit()
    os.kill(holder_pid, signal.SIGKILL)
    _wait_until_gone(holder_pid)
    return lock, child_pid


def test_release_refuses_a_dead_record_whose_flock_an_orphan_still_holds(tmp_path, sessions) -> None:
    lock, child_pid = _orphan_held_lock(tmp_path, sessions)
    before = lock.read_bytes()
    inode_before = lock.stat().st_ino
    assert ops_locks.pid_liveness_verdict(before.decode()) == "stale", "fixture precondition: the recorded pid is dead"

    lines: list[str] = []
    with pytest.raises(ops_locks.LockRefusal) as caught:
        ops_locks.release_lock(OpsContext(kanban_root=tmp_path), "repo-wake-demo", say=lines.append)

    refusal = caught.value
    contract = parse_error_contract(str(refusal))
    assert contract is not None, f"the refusal must speak the dual-channel contract:\n{refusal}"
    assert refusal.exit_code == 4
    assert refusal.error == contract["error"] == "release_locks.orphan_holder"
    assert child_pid in contract["message"], f"the refusal must name the process holding the flock:\n{refusal}"
    assert "--release repo-wake-demo --force" in contract["remedy"]
    assert lock.stat().st_ino == inode_before and lock.read_bytes() == before, "the lock file must be left untouched"
    assert lines == []


def test_forced_release_signals_the_orphan_and_clears_the_record(tmp_path, sessions) -> None:
    lock, child_pid = _orphan_held_lock(tmp_path, sessions)

    lines: list[str] = []
    outcome = ops_locks.release_lock(
        OpsContext(kanban_root=tmp_path), "repo-wake-demo", force=True, say=lines.append, grace_seconds=10
    )

    _wait_until_gone(int(child_pid))
    assert outcome.outcome == "orphan_let_go", lines
    assert any(f"sent SIGTERM to pids {child_pid}" in line for line in lines), lines
    assert lock.read_bytes() == b"", "once the flock is free the dead holder's record is swapped for an empty file"
