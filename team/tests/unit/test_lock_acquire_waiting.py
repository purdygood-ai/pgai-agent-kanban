"""
test_lock_acquire_waiting.py
============================
``pgai_lock_acquire_waiting`` (scripts/lib/lock_provenance.sh): the shared
lock acquisition, retried on contention with backoff for a caller-chosen
number of seconds.

Covered:
  1. A wait of 0 is one attempt: a held lock returns 1 at once.
  2. A holder that releases during the wait: the waiter acquires, its
     provenance is on the lock, and its out-fd variable is set.
  3. A holder that keeps the lock for the whole wait: the waiter returns 1
     after (about) the wait, and the contention narration appears once.
  4. A refusal (blank provenance field) returns 2 at once; waiting cannot
     fix it.

Every lock file lives under pytest's tmp_path.
"""

from __future__ import annotations

import pathlib
import subprocess
import textwrap
import time

from tests.unit.shell_harness import run_bash

_LOCK_PROVENANCE = "scripts/lib/lock_provenance.sh"
_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]


class _Holder:
    """A separate process holding *lock_file* until release()."""

    def __init__(self, tmp_path: pathlib.Path, lock_file: pathlib.Path) -> None:
        self.ready = tmp_path / "holder-ready"
        self.done = tmp_path / "holder-done"
        script = textwrap.dedent(f"""\
            source {_LOCK_PROVENANCE}
            fd=""
            pgai_lock_acquire "{lock_file}" holder-proc "$$" "hold-op" none fd
            touch "{self.ready}"
            while [[ ! -f "{self.done}" ]]; do sleep 0.05; done
            pgai_lock_release "{lock_file}" "$fd"
        """)
        self.proc = subprocess.Popen(["bash", "-c", script], cwd=str(_TEAM_DIR))
        deadline = time.time() + 10
        while not self.ready.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert self.ready.exists(), "the lock holder did not start"

    def release(self) -> None:
        self.done.touch()
        self.proc.wait(timeout=10)


def _waiter_script(lock_file: pathlib.Path, wait: str, holder: str = "waiter") -> str:
    return textwrap.dedent(f"""\
        source {_LOCK_PROVENANCE}
        my_fd=""
        start=$(date +%s)
        rc=0
        pgai_lock_acquire_waiting "{wait}" "{lock_file}" "{holder}" "$$" "wait-op" none my_fd || rc=$?
        echo "RC=$rc ELAPSED=$(( $(date +%s) - start )) FD=${{my_fd}}"
        if [[ $rc -eq 0 ]]; then
            cat "{lock_file}"
            pgai_lock_release "{lock_file}" "$my_fd"
        fi
    """)


def _field(stdout: str, key: str) -> str:
    for token in stdout.split():
        if token.startswith(f"{key}="):
            return token.split("=", 1)[1]
    return ""


def test_zero_wait_is_a_single_attempt(tmp_path):
    lock_file = tmp_path / "locks" / "probe.lock"
    holder = _Holder(tmp_path, lock_file)
    try:
        result = run_bash(tmp_path, _waiter_script(lock_file, "0"), timeout=30)
    finally:
        holder.release()
    assert _field(result.stdout, "RC") == "1", result.stdout + result.stderr
    assert int(_field(result.stdout, "ELAPSED")) <= 1


def test_lock_released_during_the_wait_is_acquired(tmp_path):
    lock_file = tmp_path / "locks" / "probe.lock"
    holder = _Holder(tmp_path, lock_file)
    waiter = subprocess.Popen(
        ["bash", "-c", _waiter_script(lock_file, "20")],
        cwd=str(_TEAM_DIR), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    time.sleep(2)
    holder.release()
    out, err = waiter.communicate(timeout=60)
    assert _field(out, "RC") == "0", out + err
    assert 1 <= int(_field(out, "ELAPSED")) < 20, out
    assert _field(out, "FD"), "the caller's out-fd variable was not set"
    assert "holder=waiter" in out and "op=wait-op" in out, out


def test_lock_held_for_the_whole_wait_gives_up_after_the_wait(tmp_path):
    lock_file = tmp_path / "locks" / "probe.lock"
    holder = _Holder(tmp_path, lock_file)
    try:
        result = run_bash(tmp_path, _waiter_script(lock_file, "4"), timeout=60)
    finally:
        holder.release()
    assert _field(result.stdout, "RC") == "1", result.stdout + result.stderr
    elapsed = int(_field(result.stdout, "ELAPSED"))
    assert 4 <= elapsed <= 8, f"gave up after {elapsed}s for a 4s wait"
    assert result.stderr.count("error:   lock.provenance.contended") == 1, result.stderr
    assert "holder=holder-proc" in result.stderr


def test_refusal_is_not_retried(tmp_path):
    lock_file = tmp_path / "locks" / "probe.lock"
    result = run_bash(tmp_path, _waiter_script(lock_file, "10", holder=""), timeout=30)
    assert _field(result.stdout, "RC") == "2", result.stdout + result.stderr
    assert int(_field(result.stdout, "ELAPSED")) <= 1
    assert "lock.provenance.incomplete" in result.stderr
