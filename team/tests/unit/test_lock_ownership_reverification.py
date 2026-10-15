"""
test_lock_ownership_reverification.py
=======================================
Behavioral fixtures for chain-level repo lock ownership reverification: a
chain re-verifies it still holds its project repo lock at every mutating
phase boundary
(team/scripts/lib/lock_provenance.sh: pgai_lock_still_held,
pgai_lock_reverify_or_narrate, pgai_lock_abandon).

The guarded defect: a chain reaped mid-run (an aggressive stale-lock
threshold, an operator `release-locks.sh --force`) that has no way to
notice -- it keeps dispatching agents, verifying merges, and writing queue
markers as though it still owned the project's dev tree and queue files, racing
whatever new chain the reap let in.

Acceptance criteria exercised:
  - pgai_lock_still_held is true for the pid that actually holds the lock,
    and false once a different process's provenance is on disk (a reap and
    takeover) or the lock file is gone entirely.
  - pgai_lock_reverify_or_narrate is silent (no stdout, no stderr) on the
    healthy path -- the fix's own "no extra output" constraint.
  - pgai_lock_reverify_or_narrate narrates dual-channel on loss, naming a
    stable error key and the CURRENT holder's four provenance fields
    (holder, pid, op, task), and returns non-zero.
  - pgai_lock_abandon closes this process's own fd and drops its
    bookkeeping entry WITHOUT touching the on-disk lock file -- the new
    holder's provenance record survives byte-for-byte. This is the
    correctness property a naive "just call pgai_lock_release" fix would
    have violated: release truncates and removes whatever currently sits
    at the path, which after a takeover is the NEW holder's own record.
  - pgai_lock_abandon is a safe no-op with no fd (mirrors pgai_lock_release).

Also covers the cheapness constraint (no flock acquisition, no sleep) by
asserting a `flock` verb never appears in the two new public functions'
own definitions -- a caller that used one would make the check too
expensive to run unconditionally at every mutating phase boundary of
every chain, per this file's own header ("Chain-level ownership
reverification").
"""

from __future__ import annotations

import pathlib
import textwrap

from tests.unit.shell_harness import run_bash

_LOCK_PROVENANCE_SH = "scripts/lib/lock_provenance.sh"


def _repo_root() -> pathlib.Path:
    """team/ -- mirrors test_lock_provenance.py's own self-location, so a
    relative lib path resolves the same way regardless of the pytest
    process's own invocation cwd (docs/test-authoring-guidelines.md
    "State your working directory; never inherit it")."""
    return pathlib.Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# pgai_lock_still_held: true for the real holder, false after a takeover or
# when the lock file is gone.
# ---------------------------------------------------------------------------


def test_still_held_true_for_actual_holder(tmp_path: pathlib.Path) -> None:
    lock_file = tmp_path / "locks" / "chain.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}
        fd=""
        pgai_lock_acquire "{lock_file}" wake-holder "$$" "wake-chain:demo" TASK-1 fd
        if pgai_lock_still_held "{lock_file}" "$$"; then echo "HELD=yes"; else echo "HELD=no"; fi
        pgai_lock_release "{lock_file}" "$fd"
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "HELD=yes" in result.stdout


def test_still_held_false_after_a_takeover_by_a_different_pid(
    tmp_path: pathlib.Path,
) -> None:
    lock_file = tmp_path / "locks" / "chain.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}
        fd=""
        pgai_lock_acquire "{lock_file}" wake-holder "$$" "wake-chain:demo" TASK-1 fd
        # Simulate a reap-and-takeover: a new process's provenance record now
        # sits at the same path this chain still holds an fd/flock on.
        printf 'holder=wake-holder\\npid=999999\\nop=wake-chain:demo\\ntask=TASK-2\\n' > "{lock_file}"
        if pgai_lock_still_held "{lock_file}" "$$"; then echo "HELD=yes"; else echo "HELD=no"; fi
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "HELD=no" in result.stdout, (
        f"a provenance record naming a different pid must read as lost.\n"
        f"stdout: {result.stdout}"
    )


def test_still_held_false_when_lock_file_is_gone(tmp_path: pathlib.Path) -> None:
    lock_file = tmp_path / "locks" / "vanished.lock"

    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}
        if pgai_lock_still_held "{lock_file}" "$$"; then echo "HELD=yes"; else echo "HELD=no"; fi
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "HELD=no" in result.stdout


# ---------------------------------------------------------------------------
# pgai_lock_reverify_or_narrate: silent on the healthy path; narrated
# dual-channel, naming the current holder's four fields, on loss.
# ---------------------------------------------------------------------------


def test_reverify_healthy_path_is_completely_silent(tmp_path: pathlib.Path) -> None:
    lock_file = tmp_path / "locks" / "chain.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}
        fd=""
        pgai_lock_acquire "{lock_file}" wake-holder "$$" "wake-chain:demo" TASK-1 fd
        pgai_lock_reverify_or_narrate "{lock_file}" "$$" "agent dispatch" "wake-chain:demo" TASK-1
        echo "RC=$?"
        pgai_lock_release "{lock_file}" "$fd"
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "RC=0" in result.stdout
    assert result.stderr == "", (
        f"the healthy re-verification path must produce zero extra output.\n"
        f"stderr: {result.stderr}"
    )


def test_reverify_on_loss_narrates_dual_channel_naming_current_holder(
    tmp_path: pathlib.Path,
) -> None:
    lock_file = tmp_path / "locks" / "chain.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    script = textwrap.dedent(f"""\
        set +e
        source {_LOCK_PROVENANCE_SH}
        fd=""
        pgai_lock_acquire "{lock_file}" wake-holder "$$" "wake-chain:demo" TASK-1 fd
        # A different chain reaped this lock and reacquired it under a new pid.
        printf 'holder=new-holder\\npid=424242\\nop=wake-chain:other\\ntask=TASK-9\\n' > "{lock_file}"
        pgai_lock_reverify_or_narrate "{lock_file}" "$$" "merge" "wake-chain:demo" TASK-1
        echo "RC=$?"
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "RC=1" in result.stdout, (
        f"a lost lock must return non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "error:   lock.provenance.reverify_lost" in result.stderr
    assert "holder=new-holder" in result.stderr
    assert "pid=424242" in result.stderr
    assert "op=wake-chain:other" in result.stderr
    assert "task=TASK-9" in result.stderr
    assert "'merge'" in result.stderr, (
        f"expected the narration to name which phase boundary stopped.\n"
        f"stderr: {result.stderr}"
    )
    assert "remedy:" in result.stderr and "release-locks.sh" in result.stderr


def test_reverify_on_missing_lock_file_narrates_no_provenance(
    tmp_path: pathlib.Path,
) -> None:
    """A lock file that has vanished entirely (rather than been taken over)
    is still a loss -- narrated with the "(no provenance recorded)" shape
    pgai_lock_describe_provenance already uses for an untrusted/absent
    record, not a crash or a silent pass."""
    lock_file = tmp_path / "locks" / "gone.lock"

    script = textwrap.dedent(f"""\
        set +e
        source {_LOCK_PROVENANCE_SH}
        pgai_lock_reverify_or_narrate "{lock_file}" 12345 "queue write" "wake-chain:demo" TASK-1
        echo "RC=$?"
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "RC=1" in result.stdout
    assert "error:   lock.provenance.reverify_lost" in result.stderr
    assert "(no provenance recorded)" in result.stderr
    assert "'queue write'" in result.stderr


# ---------------------------------------------------------------------------
# pgai_lock_abandon: closes the fd and drops bookkeeping without touching
# the on-disk file -- the new holder's record survives byte-for-byte.
# ---------------------------------------------------------------------------


def test_abandon_never_touches_the_new_holders_on_disk_record(
    tmp_path: pathlib.Path,
) -> None:
    lock_file = tmp_path / "locks" / "chain.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    new_holder_record = "holder=new-holder\npid=424242\nop=wake-chain:other\ntask=TASK-9\n"

    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}
        fd=""
        pgai_lock_acquire "{lock_file}" wake-holder "$$" "wake-chain:demo" TASK-1 fd
        printf '{new_holder_record}' > "{lock_file}"
        pgai_lock_abandon "{lock_file}" "$fd"
        echo "AFTER_ABANDON:[$(cat "{lock_file}")]"
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    expected = f"AFTER_ABANDON:[{new_holder_record.rstrip(chr(10))}]"
    assert expected in result.stdout, (
        f"pgai_lock_abandon must never mutate the on-disk lock file -- a "
        f"call after loss must leave the new holder's record byte-for-byte "
        f"intact.\nexpected: {expected!r}\nstdout: {result.stdout!r}"
    )
    assert lock_file.read_text(encoding="utf-8") == new_holder_record, (
        "the lock file on disk must be untouched by pgai_lock_abandon"
    )


def test_abandon_drops_bookkeeping_so_trap_sweep_never_touches_the_file(
    tmp_path: pathlib.Path,
) -> None:
    """If pgai_lock_abandon merely closed the fd and left the
    _PGAI_LOCK_HELD_FDS entry behind, this process's own EXIT trap
    (pgai_lock_release_all_held) would still call pgai_lock_release on the
    real lock_file path when the script exits -- corrupting the new
    holder's record on the way out even though the abandon call itself
    looked clean. This fixture lets the interpreter exit normally (firing
    the real EXIT trap) after abandoning, and asserts the new holder's
    record is still intact afterward."""
    lock_file = tmp_path / "locks" / "chain.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    new_holder_record = "holder=new-holder\npid=424242\nop=wake-chain:other\ntask=TASK-9\n"

    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}
        fd=""
        pgai_lock_acquire "{lock_file}" wake-holder "$$" "wake-chain:demo" TASK-1 fd
        printf '{new_holder_record}' > "{lock_file}"
        pgai_lock_abandon "{lock_file}" "$fd"
        # No explicit exit -- let the script's own EXIT trap (installed by
        # lock_provenance.sh's sourcing block) fire naturally.
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert lock_file.read_text(encoding="utf-8") == new_holder_record, (
        "the trap/finally sweep must not release a lock this process "
        "already abandoned -- the new holder's record must survive "
        "process exit untouched.\n"
        f"stderr: {result.stderr}"
    )


def test_abandon_with_empty_fd_is_a_safe_no_op(tmp_path: pathlib.Path) -> None:
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}
        pgai_lock_abandon "/nonexistent/path.lock" ""
        echo "RC=$?"
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "RC=0" in result.stdout


# ---------------------------------------------------------------------------
# Cheapness constraint: neither new public function may take a flock or
# sleep -- a per-boundary check that did either would be too expensive to
# run unconditionally at every mutating phase boundary of every chain.
# ---------------------------------------------------------------------------


def test_still_held_and_reverify_never_call_flock_or_sleep(tmp_path: pathlib.Path) -> None:
    lib_path = _repo_root() / _LOCK_PROVENANCE_SH
    text = lib_path.read_text(encoding="utf-8")

    def _function_body(name: str) -> str:
        start = text.index(f"\n{name}() {{")
        # Find the matching closing brace at column 0 (this file's own
        # convention: every top-level function closes with a bare "}" line).
        end = text.index("\n}\n", start)
        return text[start:end]

    for fn in ("pgai_lock_still_held", "pgai_lock_reverify_or_narrate"):
        body = _function_body(fn)
        assert "flock" not in body, (
            f"{fn} must never take a flock -- it is a cheap read-only check "
            f"run at every mutating phase boundary of every chain."
        )
        assert "sleep" not in body, (
            f"{fn} must never sleep -- see this file's own cheapness "
            f"constraint."
        )
