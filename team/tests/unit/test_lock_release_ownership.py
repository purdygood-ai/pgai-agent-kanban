"""
test_lock_release_ownership.py
==============================
Behavioral fixtures for pgai_lock_release's ownership check
(team/scripts/lib/lock_provenance.sh): a release -- an explicit call, or
the trap/finally sweep on exit -- clears and unlinks the lock path only
when the file there is the very inode this process holds the flock on.
When another process has reaped the lock and reacquired it (a new file at
the path), or the path is gone, release degrades to an abandon: it closes
its own fd, drops its bookkeeping, narrates dual-channel, and leaves the
new holder's file untouched. This is the release-side half of the hold
discipline the file's header describes: one chain's exit can never free a
lock a different, live chain is holding through its own subagent run.

Acceptance criteria exercised:
  - a release against a path another process now owns leaves that
    holder's file, provenance and inode intact, narrates
    lock.provenance.release_not_owner, returns 0, and still closes the
    caller's own fd and drops its bookkeeping;
  - the same holds for the exit-time trap/finally sweep of a process that
    never reached an explicit release, proven against a REAL second holder
    whose flock must remain contended afterwards;
  - an ordinary release of a lock this process still holds is unchanged:
    no on-disk trace remains and nothing is narrated;
  - a release whose lock file has been removed does not recreate it;
  - a second release of an already-released lock touches nothing at the
    path, so a stale fd number can never clear a successor's record;
  - the abandon drops the pid bookkeeping alongside the fd bookkeeping.
"""

from __future__ import annotations

import pathlib
import subprocess
import textwrap
import time

from tests.unit.shell_harness import run_bash


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


_LOCK_PROVENANCE_SH = _repo_root() / "scripts" / "lib" / "lock_provenance.sh"

_OTHER_RECORD = "holder=chain-b\\npid=424242\\nop=wake-chain:demo\\ntask=none\\n"


def _wait_for(path: pathlib.Path, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while not path.exists() and time.time() < deadline:
        time.sleep(0.05)
    assert path.exists(), f"expected {path} to appear within {timeout}s"


# ---------------------------------------------------------------------------
# Fixture: an explicit release against a path another process now owns
# abandons -- the new holder's file survives untouched.
# ---------------------------------------------------------------------------


def test_release_against_a_taken_over_path_abandons_and_leaves_the_new_file(
    tmp_path: pathlib.Path,
) -> None:
    lock_file = tmp_path / "locks" / "taken-over.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}
        fd=""
        pgai_lock_acquire "{lock_file}" chain-a "$$" wake-chain:demo TASK-A fd

        # Another process reaps this lock and reacquires it: the old file is
        # unlinked and a NEW file carrying the new holder's record sits at
        # the path.
        rm -f "{lock_file}"
        printf '{_OTHER_RECORD}' > "{lock_file}"
        new_ino="$(stat -c %i "{lock_file}")"

        pgai_lock_release "{lock_file}" "$fd"
        echo "RC=$?"
        echo "EXISTS=$([[ -e "{lock_file}" ]] && echo yes || echo no)"
        echo "INO_SAME=$([[ "$(stat -c %i "{lock_file}" 2>/dev/null)" == "$new_ino" ]] && echo yes || echo no)"
        echo "RECORD=$(tr '\\n' ' ' < "{lock_file}")"
        echo "BOOK_FDS=${{#_PGAI_LOCK_HELD_FDS[@]}} BOOK_PIDS=${{#_PGAI_LOCK_HELD_PIDS[@]}}"
        echo "FD_OPEN=$( {{ : >&"$fd"; }} 2>/dev/null && echo yes || echo no)"
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "RC=0" in result.stdout
    assert "EXISTS=yes" in result.stdout, (
        f"the new holder's lock file must survive the release.\nstdout: {result.stdout}"
    )
    assert "INO_SAME=yes" in result.stdout, (
        f"the new holder's file must be the same inode, not recreated.\nstdout: {result.stdout}"
    )
    assert "pid=424242" in result.stdout and "holder=chain-b" in result.stdout, (
        f"the new holder's provenance must be intact.\nstdout: {result.stdout}"
    )
    assert "BOOK_FDS=0 BOOK_PIDS=0" in result.stdout, (
        f"the releasing process must drop its own bookkeeping.\nstdout: {result.stdout}"
    )
    assert "FD_OPEN=no" in result.stdout, (
        f"the releasing process must close its own fd.\nstdout: {result.stdout}"
    )
    assert "lock.provenance.release_not_owner" in result.stderr, (
        f"expected the dual-channel narration.\nstderr: {result.stderr}"
    )
    assert "424242" in result.stderr and "chain-b" in result.stderr, (
        f"the narration must name the current holder's record.\nstderr: {result.stderr}"
    )
    assert "release-locks.sh" in result.stderr


# ---------------------------------------------------------------------------
# Fixture: the exit-time trap/finally sweep of a process that lost its lock
# leaves a REAL second holder's file and flock undisturbed -- a third
# acquisition is still contended afterwards.
# ---------------------------------------------------------------------------


def test_exit_sweep_of_a_process_that_lost_its_lock_leaves_the_live_holder_alone(
    tmp_path: pathlib.Path,
) -> None:
    lock_file = tmp_path / "locks" / "cascade.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    loser_ready = tmp_path / "loser-ready"
    holder_ready = tmp_path / "holder-ready"
    done_file = tmp_path / "holder-done"

    # The loser: acquires, then parks until told to exit WITHOUT releasing
    # -- the trap/finally sweep is its only release path.
    loser_script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}
        fd=""
        pgai_lock_acquire "{lock_file}" chain-a "$$" wake-chain:demo TASK-A fd
        touch "{loser_ready}"
        while [[ ! -f "{holder_ready}" ]]; do sleep 0.05; done
        exit 0
    """)
    loser = subprocess.Popen(
        ["bash", "-c", loser_script], cwd=str(_repo_root()),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    holder = None
    try:
        _wait_for(loser_ready)

        # An external reap unlinks the loser's file; a second holder then
        # takes a real flock on a NEW file at the same path and writes its
        # own record -- exactly what a sibling wake chain does.
        lock_file.unlink()
        holder_script = textwrap.dedent(f"""\
            exec 9>"{lock_file}"
            flock -x 9
            printf 'holder=chain-b\\npid=%s\\nop=wake-chain:demo\\ntask=none\\n' "$$" > "{lock_file}"
            touch "{holder_ready}"
            while [[ ! -f "{done_file}" ]]; do sleep 0.05; done
        """)
        holder = subprocess.Popen(["bash", "-c", holder_script], cwd=str(_repo_root()))
        _wait_for(holder_ready)
        holder_ino = lock_file.stat().st_ino
        holder_record = lock_file.read_text(encoding="utf-8")

        # The loser now exits; its sweep runs against a path it no longer owns.
        loser_out, loser_err = loser.communicate(timeout=10)
        assert loser.returncode == 0, f"stdout:\n{loser_out}\nstderr:\n{loser_err}"
        assert "pgai_lock_release_all_held" in loser_err, (
            f"the sweep must have run for the parked lock.\nstderr: {loser_err}"
        )
        assert "lock.provenance.release_not_owner" in loser_err, (
            f"the sweep's release must have narrated the abandon.\nstderr: {loser_err}"
        )

        assert lock_file.exists(), "the live holder's lock file must survive the loser's exit"
        assert lock_file.stat().st_ino == holder_ino, "the live holder's inode must be untouched"
        assert lock_file.read_text(encoding="utf-8") == holder_record, (
            "the live holder's provenance must be byte-identical after the loser's exit"
        )

        # The property that matters to the fleet: the lock is STILL held.
        # A fresh waiter must be contended (RC=1), not handed the lock.
        probe = textwrap.dedent(f"""\
            set +e
            source {_LOCK_PROVENANCE_SH}
            fd3=""
            pgai_lock_acquire "{lock_file}" chain-c "$$" wake-chain:demo TASK-C fd3
            echo "RC=$?"
        """)
        result = run_bash(tmp_path, probe)
        assert "RC=1" in result.stdout, (
            f"a waiter must still find the lock held by the live holder after "
            f"the loser's exit.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "lock.provenance.contended" in result.stderr
        assert "stale_reaped" not in result.stderr
    finally:
        done_file.touch()
        if holder is not None:
            holder.wait(timeout=5)
        if loser.poll() is None:
            loser.kill()


# ---------------------------------------------------------------------------
# Fixture: an ordinary release of a lock this process still holds is
# unchanged -- no on-disk trace, no narration.
# ---------------------------------------------------------------------------


def test_own_release_still_leaves_no_trace_and_is_silent(tmp_path: pathlib.Path) -> None:
    lock_file = tmp_path / "locks" / "own.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}
        fd=""
        pgai_lock_acquire "{lock_file}" chain-a "$$" wake-chain:demo TASK-A fd
        pgai_lock_release "{lock_file}" "$fd"
        echo "RC=$?"
        echo "EXISTS=$([[ -e "{lock_file}" ]] && echo yes || echo no)"
        echo "BOOK_FDS=${{#_PGAI_LOCK_HELD_FDS[@]}} BOOK_PIDS=${{#_PGAI_LOCK_HELD_PIDS[@]}}"
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "RC=0" in result.stdout
    assert "EXISTS=no" in result.stdout, (
        f"a clean release of an owned lock must leave no file.\nstdout: {result.stdout}"
    )
    assert "BOOK_FDS=0 BOOK_PIDS=0" in result.stdout
    assert "release_not_owner" not in result.stderr, (
        f"the owned path must not narrate.\nstderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# Fixture: a release whose lock file has been removed does not recreate it.
# ---------------------------------------------------------------------------


def test_release_after_the_lock_file_vanished_does_not_recreate_it(
    tmp_path: pathlib.Path,
) -> None:
    lock_file = tmp_path / "locks" / "vanished.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}
        fd=""
        pgai_lock_acquire "{lock_file}" chain-a "$$" wake-chain:demo TASK-A fd
        rm -f "{lock_file}"
        pgai_lock_release "{lock_file}" "$fd"
        echo "RC=$?"
        echo "EXISTS=$([[ -e "{lock_file}" ]] && echo yes || echo no)"
        echo "BOOK_FDS=${{#_PGAI_LOCK_HELD_FDS[@]}}"
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "RC=0" in result.stdout
    assert "EXISTS=no" in result.stdout, (
        f"release must not recreate a lock file that was removed out from under it.\n"
        f"stdout: {result.stdout}"
    )
    assert "BOOK_FDS=0" in result.stdout
    assert "lock.provenance.release_not_owner" in result.stderr
    assert "no lock file at the path" in result.stderr, (
        f"the narration must say what it found at the path.\nstderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# Fixture: a second release of an already-released lock touches nothing at
# the path -- a stale fd number can never clear a successor's record.
# ---------------------------------------------------------------------------


def test_second_release_touches_nothing_at_the_path(tmp_path: pathlib.Path) -> None:
    lock_file = tmp_path / "locks" / "double.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}
        fd=""
        pgai_lock_acquire "{lock_file}" chain-a "$$" wake-chain:demo TASK-A fd
        pgai_lock_release "{lock_file}" "$fd"
        # A successor's record now sits at the path.
        printf '{_OTHER_RECORD}' > "{lock_file}"
        pgai_lock_release "{lock_file}" "$fd"
        echo "RC=$?"
        echo "RECORD=$(tr '\\n' ' ' < "{lock_file}")"
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "RC=0" in result.stdout
    assert "pid=424242" in result.stdout and "holder=chain-b" in result.stdout, (
        f"the successor's record must survive a repeated release.\nstdout: {result.stdout}"
    )
    assert "release_not_owner" not in result.stderr, (
        f"a repeat release of an already-released lock is a silent no-op.\n"
        f"stderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# Fixture: pgai_lock_abandon drops the pid bookkeeping alongside the fd
# bookkeeping, so a later sweep has nothing to act on.
# ---------------------------------------------------------------------------


def test_abandon_drops_pid_bookkeeping_with_fd_bookkeeping(tmp_path: pathlib.Path) -> None:
    lock_file = tmp_path / "locks" / "abandon.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}
        fd=""
        pgai_lock_acquire "{lock_file}" chain-a "$$" wake-chain:demo TASK-A fd
        echo "BEFORE_FDS=${{#_PGAI_LOCK_HELD_FDS[@]}} BEFORE_PIDS=${{#_PGAI_LOCK_HELD_PIDS[@]}}"
        pgai_lock_abandon "{lock_file}" "$fd"
        echo "AFTER_FDS=${{#_PGAI_LOCK_HELD_FDS[@]}} AFTER_PIDS=${{#_PGAI_LOCK_HELD_PIDS[@]}}"
        echo "EXISTS=$([[ -e "{lock_file}" ]] && echo yes || echo no)"
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "BEFORE_FDS=1 BEFORE_PIDS=1" in result.stdout
    assert "AFTER_FDS=0 AFTER_PIDS=0" in result.stdout
    assert "EXISTS=yes" in result.stdout, "abandon never touches the on-disk file"
    assert "pgai_lock_release_all_held" not in result.stderr, (
        f"nothing may be left for the exit sweep after an abandon.\nstderr: {result.stderr}"
    )
