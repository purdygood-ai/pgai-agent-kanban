"""
test_lock_provenance_appears_atomically.py
==========================================
Behavioral fixtures for the rule that a lock's provenance record and its
flock arrive at the lock path together, that release unlinks without a
prior truncate, and that a provenance-less lock file is reapable only
when it is also older than the freshness window.

The mechanism under test (team/scripts/lib/lock_provenance.sh):

  - pgai_lock_acquire stages its record in a private sibling file, takes
    its flock on that inode, and links or renames the inode onto the lock
    path in one directory operation -- so a concurrent reader of the path
    sees no file, a previous holder's complete record, or this holder's
    complete record, never an empty or torn one.
  - pgai_lock_release's only on-disk step is the unlink; there is no
    emptied-but-linked state a contender could observe and reap.
  - the contention classifier reaps a provenance-less file only when its
    mtime is at least _PGAI_LOCK_UNPROVENANCED_REAP_MIN_AGE_SECONDS old;
    a fresher one is deferred to as contention.
  - a flock won on an inode the path no longer names is closed and the
    attempt repeated, never granted.

Every scenario uses synthetic lock paths under tmp_path; nothing here
touches the live install's locks/ directory. The injected-delay race
widens the two windows the old sequence exposed -- after a flock win and
after an unlink -- by overriding `flock` and `rm` as shell functions in
the sourcing shell, so production code carries no test hook. Every
process a scenario starts is owned by the test and ended on every exit
path (docs/test-authoring-guidelines.md "Own every process your
subprocess starts").
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import textwrap
import threading
import time

import pytest

from tests.unit.shell_harness import run_bash

# ---------------------------------------------------------------------------
# Paths (relative to this file: team/tests/unit/ -> team/ -> repo root)
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]  # team/
_LOCK_PROVENANCE_SH = _TEAM_DIR / "scripts" / "lib" / "lock_provenance.sh"

_COMPLETE_RECORD = re.compile(r"\Aholder=[^\n]*\npid=[^\n]*\nop=[^\n]*\ntask=[^\n]*\nacquired_at=[^\n]*\n\Z")
_HARNESS_TIMEOUT_SECONDS = 90


def _wait_for(path: pathlib.Path, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while not path.exists() and time.time() < deadline:
        time.sleep(0.02)
    assert path.exists(), f"expected {path} to appear within {timeout}s"


def _end(proc: subprocess.Popen, done_file: pathlib.Path | None = None) -> None:
    """Own the process: ask it to finish, then make sure it did."""
    if done_file is not None:
        done_file.touch()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def _freshness_window_seconds(tmp_path: pathlib.Path) -> int:
    """Read the freshness window from the library itself so the fixtures
    below follow it rather than restating its value."""
    result = run_bash(
        tmp_path,
        f'source {_LOCK_PROVENANCE_SH}; echo "WINDOW=$_PGAI_LOCK_UNPROVENANCED_REAP_MIN_AGE_SECONDS"',
    )
    match = re.search(r"^WINDOW=(\d+)$", result.stdout, re.M)
    assert match, f"the library must define the freshness window.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    window = int(match.group(1))
    # A window of 0 or 1 second would make the "fresh" case below vacuous:
    # mtime has one-second granularity, so a file created this second
    # must count as fresh for at least one more.
    assert window >= 2, f"the freshness window must be at least 2s to be observable: {window}"
    return window


def _foreign_empty_holder(
    tmp_path: pathlib.Path, lock_file: pathlib.Path, backdate_seconds: int | None
) -> tuple[subprocess.Popen, pathlib.Path]:
    """A holder outside the library: a real exclusive flock on an EMPTY
    lock file, optionally backdated -- the one shape that still yields a
    held, provenance-less file now that the library's own record appears
    atomically."""
    ready_file = tmp_path / "holder-ready"
    done_file = tmp_path / "holder-done"
    backdate = f'touch -d "-{backdate_seconds} seconds" "{lock_file}"' if backdate_seconds else ":"
    holder_script = textwrap.dedent(f"""\
        exec 9>"{lock_file}"
        flock -x 9
        {backdate}
        touch "{ready_file}"
        while [[ ! -f "{done_file}" ]]; do sleep 0.05; done
    """)
    holder = subprocess.Popen(["bash", "-c", holder_script], cwd=str(_TEAM_DIR))
    _wait_for(ready_file)
    return holder, done_file


def _contend(tmp_path: pathlib.Path, lock_file: pathlib.Path):
    script = textwrap.dedent(f"""\
        set +e
        source {_LOCK_PROVENANCE_SH}
        fd2=""
        pgai_lock_acquire "{lock_file}" reader "$$" other-op TASK-2 fd2
        echo "RC=$?"
        if [[ "$fd2" != "" ]]; then pgai_lock_release "{lock_file}" "$fd2"; fi
    """)
    return run_bash(tmp_path, script)


# ---------------------------------------------------------------------------
# (1) A provenance-less lock file is reapable only when it is also old.
# ---------------------------------------------------------------------------


def test_empty_but_fresh_lock_file_is_contention_not_reaped(tmp_path: pathlib.Path) -> None:
    _freshness_window_seconds(tmp_path)
    lock_file = tmp_path / "locks" / "repo-wake-demo.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    holder, done_file = _foreign_empty_holder(tmp_path, lock_file, backdate_seconds=None)
    try:
        inode_before = lock_file.stat().st_ino
        result = _contend(tmp_path, lock_file)
        assert "RC=1" in result.stdout, (
            f"an empty lock file created this instant must be deferred to, not reaped.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "stale_reaped" not in result.stderr, (
            f"no reap of any kind against a fresh provenance-less file.\nstderr: {result.stderr}"
        )
        assert "lock.provenance.contended" in result.stderr, result.stderr
        assert "(no provenance recorded)" in result.stderr, result.stderr
        assert "freshness window" in result.stderr, (
            f"the contention narration must say why the file was not reaped.\nstderr: {result.stderr}"
        )
        assert lock_file.exists(), "the fresh file was unlinked"
        assert lock_file.stat().st_ino == inode_before, "the fresh file was replaced"
        assert lock_file.stat().st_size == 0, "the fresh file was written into"
    finally:
        _end(holder, done_file)


def test_empty_lock_file_older_than_the_freshness_window_is_still_reaped(
    tmp_path: pathlib.Path,
) -> None:
    window = _freshness_window_seconds(tmp_path)
    lock_file = tmp_path / "locks" / "repo-wake-demo.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    holder, done_file = _foreign_empty_holder(tmp_path, lock_file, backdate_seconds=window + 5)
    try:
        inode_before = lock_file.stat().st_ino
        result = _contend(tmp_path, lock_file)
        assert "RC=0" in result.stdout, (
            f"an empty lock file older than the window must be reaped and reacquired.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "lock.provenance.stale_reaped.no_provenance" in result.stderr, result.stderr
        assert f"{window}s freshness window" in result.stderr, (
            f"the reap narration must name the window it applied.\nstderr: {result.stderr}"
        )
        assert not lock_file.exists(), "the reaper's own acquisition must release cleanly"
        assert inode_before != 0
    finally:
        _end(holder, done_file)


# ---------------------------------------------------------------------------
# (2) Provenance appears with the file; release leaves nothing behind.
# ---------------------------------------------------------------------------


def _poll_lock_path(lock_file: pathlib.Path, stop: threading.Event, seen: list[str]) -> None:
    """Read the lock path as fast as possible and record every observation
    that is neither 'no file' nor a complete four-field record."""
    while not stop.is_set():
        try:
            data = lock_file.read_bytes()
        except FileNotFoundError:
            continue
        except OSError as exc:  # a transient EBUSY-style error is not a state
            seen.append(f"oserror:{exc}")
            continue
        if data == b"":
            seen.append("empty")
        elif not _COMPLETE_RECORD.match(data.decode("utf-8", errors="replace")):
            seen.append(f"torn:{data!r}")


def test_concurrent_reader_never_observes_an_empty_or_torn_lock_file(
    tmp_path: pathlib.Path,
) -> None:
    lock_file = tmp_path / "locks" / "repo-wake-demo.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    seen: list[str] = []
    stop = threading.Event()
    poller = threading.Thread(target=_poll_lock_path, args=(lock_file, stop, seen), daemon=True)
    poller.start()
    try:
        script = textwrap.dedent(f"""\
            set -euo pipefail
            source {_LOCK_PROVENANCE_SH}
            for i in $(seq 1 40); do
              fd=""
              pgai_lock_acquire "{lock_file}" looper "$$" wake-chain:demo none fd
              pgai_lock_release "{lock_file}" "$fd"
            done
            echo "PAIRS_DONE=40"
        """)
        result = run_bash(tmp_path, script, timeout=_HARNESS_TIMEOUT_SECONDS)
    finally:
        stop.set()
        poller.join(timeout=5)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "PAIRS_DONE=40" in result.stdout
    assert seen == [], (
        f"a concurrent reader observed a lock file that was empty or torn: {seen[:10]} "
        f"({len(seen)} observations)"
    )
    assert not lock_file.exists(), "release must leave no lock file on disk"
    assert sorted(p.name for p in lock_file.parent.iterdir()) == [], (
        f"release must leave no trace, staging files included: {sorted(p.name for p in lock_file.parent.iterdir())}"
    )


# ---------------------------------------------------------------------------
# (3) The injected-delay race: two callers loop acquire/release pairs on
#     one path with the post-flock and post-unlink windows widened to 0.2s;
#     no held lock is reaped and at most one holder exists at any time.
# ---------------------------------------------------------------------------


def _race_looper_script(
    lock_file: pathlib.Path, name: str, marker: pathlib.Path, violations: pathlib.Path, go: pathlib.Path
) -> str:
    return textwrap.dedent(f"""\
        set +e
        source {_LOCK_PROVENANCE_SH}
        # Widen the two windows the old sequence exposed: the moment after
        # a flock is won (before the record used to be written) and the
        # moment after the lock path is unlinked (before the fd is closed).
        flock() {{ command flock "$@"; local _rc=$?; if [[ $_rc -eq 0 ]]; then sleep 0.2; fi; return $_rc; }}
        rm() {{ command rm "$@"; local _rc=$?; sleep 0.2; return $_rc; }}
        while [[ ! -f "{go}" ]]; do sleep 0.01; done
        acquired=0
        for i in 1 2 3 4 5 6; do
          fd=""
          if pgai_lock_acquire "{lock_file}" {name} "$$" race-op TASK-RACE fd; then
            acquired=$(( acquired + 1 ))
            if [[ -e "{marker}" ]]; then
              echo "OVERLAP: {name} acquired while $(cat "{marker}") was still holding" >> "{violations}"
            fi
            echo "{name}" > "{marker}"
            held_ino="$(stat -L -c %i /proc/self/fd/$fd)"
            path_ino="$(stat -c %i "{lock_file}" 2>/dev/null || echo none)"
            if [[ "$held_ino" != "$path_ino" ]]; then
              echo "NOT-AT-PATH: {name} holds inode $held_ino but the path names $path_ino" >> "{violations}"
            fi
            # Hold longer than the widened windows so a second holder seated
            # inside one of them is still overlapping when it checks the marker.
            sleep 0.5
            command rm -f "{marker}"
            pgai_lock_release "{lock_file}" "$fd"
          else
            sleep 0.05
          fi
        done
        echo "ACQUIRED={name}:$acquired"
    """)


def test_injected_delay_race_never_reaps_a_held_lock_or_seats_two_holders(
    tmp_path: pathlib.Path,
) -> None:
    lock_file = tmp_path / "locks" / "repo-wake-demo.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    marker = tmp_path / "holding"
    violations = tmp_path / "violations"
    go = tmp_path / "go"
    procs: list[subprocess.Popen] = []
    outputs: dict[str, tuple[str, str]] = {}
    try:
        for name in ("alpha", "beta"):
            procs.append(
                subprocess.Popen(
                    ["bash", "-c", _race_looper_script(lock_file, name, marker, violations, go)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, cwd=str(_TEAM_DIR), env=dict(os.environ), start_new_session=True,
                )
            )
        go.touch()
        for name, proc in zip(("alpha", "beta"), procs):
            outputs[name] = proc.communicate(timeout=_HARNESS_TIMEOUT_SECONDS)
    finally:
        for proc in procs:
            if proc.poll() is None:
                os.killpg(proc.pid, 9)
                proc.communicate()
    combined_stderr = "\n".join(err for _, err in outputs.values())
    combined_stdout = "\n".join(out for out, _ in outputs.values())
    assert not violations.exists(), (
        f"two holders coexisted or a holder was not at the path:\n{violations.read_text()}\n"
        f"stdout:\n{combined_stdout}\nstderr:\n{combined_stderr}"
    )
    assert "stale_reaped" not in combined_stderr, (
        f"a held lock was reaped under the widened windows.\nstderr:\n{combined_stderr}"
    )
    assert "release_not_owner" not in combined_stderr, (
        f"a releaser found its lock taken over -- a holder was displaced.\nstderr:\n{combined_stderr}"
    )
    counts = dict(re.findall(r"^ACQUIRED=(\w+):(\d+)$", combined_stdout, re.M))
    assert set(counts) == {"alpha", "beta"}, combined_stdout
    assert sum(int(v) for v in counts.values()) >= 2, (
        f"both loopers must have run to completion: {counts}\nstderr:\n{combined_stderr}"
    )
    assert not lock_file.exists(), "no lock file may survive the loopers"
    assert sorted(p.name for p in lock_file.parent.iterdir()) == [], (
        f"no staging litter may survive: {sorted(p.name for p in lock_file.parent.iterdir())}"
    )


# ---------------------------------------------------------------------------
# (4) A flock won on an inode the path no longer names is retried, not
#     granted: the winner ends up holding the inode at the path.
# ---------------------------------------------------------------------------


def test_flock_won_on_an_orphaned_inode_is_retried_against_the_current_path(
    tmp_path: pathlib.Path,
) -> None:
    lock_file = tmp_path / "locks" / "repo-wake-demo.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    ready_file = tmp_path / "holder-ready"
    done_file = tmp_path / "holder-done"
    released_file = tmp_path / "holder-released"
    opened_file = tmp_path / "waiter-opened"

    holder_script = textwrap.dedent(f"""\
        source {_LOCK_PROVENANCE_SH}
        fd=""
        pgai_lock_acquire "{lock_file}" first "$$" op TASK-1 fd || exit 97
        touch "{ready_file}"
        while [[ ! -f "{done_file}" ]]; do sleep 0.02; done
        pgai_lock_release "{lock_file}" "$fd"
        touch "{released_file}"
    """)
    holder = subprocess.Popen(["bash", "-c", holder_script], cwd=str(_TEAM_DIR))
    try:
        _wait_for(ready_file)
        orphan_ino = lock_file.stat().st_ino

        # The waiter's second flock call is the one on the holder's inode
        # (the first is on its own staging file). Hold it there until the
        # holder has released, so the flock it then takes is won on an
        # inode the path no longer names.
        waiter_script = textwrap.dedent(f"""\
            set +e
            source {_LOCK_PROVENANCE_SH}
            _flock_calls=0
            flock() {{
              _flock_calls=$(( _flock_calls + 1 ))
              if (( _flock_calls == 2 )); then
                touch "{opened_file}"
                while [[ ! -f "{released_file}" ]]; do sleep 0.02; done
              fi
              command flock "$@"
            }}
            fd=""
            pgai_lock_acquire "{lock_file}" second "$$" op TASK-2 fd
            echo "RC=$?"
            echo "HELD_INO=$(stat -L -c %i /proc/self/fd/$fd)"
            echo "PATH_INO=$(stat -c %i "{lock_file}")"
            sed 's/^/RECORD:/' "{lock_file}"
            pgai_lock_release "{lock_file}" "$fd"
            echo "AFTER=$([[ -e "{lock_file}" ]] && echo yes || echo no)"
        """)
        waiter = subprocess.Popen(
            ["bash", "-c", waiter_script],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=str(_TEAM_DIR), env=dict(os.environ),
        )
        try:
            _wait_for(opened_file)
            _end(holder, done_file)
            _wait_for(released_file)
            stdout, stderr = waiter.communicate(timeout=_HARNESS_TIMEOUT_SECONDS)
        finally:
            if waiter.poll() is None:
                waiter.kill()
                waiter.communicate()
    finally:
        _end(holder, done_file)

    values = dict(re.findall(r"^(\w+)=(.*)$", stdout, re.M))
    assert values.get("RC") == "0", f"stdout:\n{stdout}\nstderr:\n{stderr}"
    assert values["HELD_INO"] == values["PATH_INO"], (
        f"the winner must hold the inode the path names.\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert int(values["HELD_INO"]) != orphan_ino, (
        f"the win on the released holder's orphaned inode must not have been granted.\nstdout:\n{stdout}"
    )
    assert "RECORD:holder=second" in stdout, stdout
    assert "stale_reaped" not in stderr, f"nothing was stale; nothing may be reaped.\nstderr:\n{stderr}"
    assert values.get("AFTER") == "no", stdout


# ---------------------------------------------------------------------------
# (5) Shipped file parses.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("script", [_LOCK_PROVENANCE_SH])
def test_shipped_shell_parses(script: pathlib.Path) -> None:
    result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"bash -n {script} failed:\n{result.stderr}"
