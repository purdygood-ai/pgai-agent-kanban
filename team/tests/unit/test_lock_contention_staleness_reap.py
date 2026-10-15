"""
test_lock_contention_staleness_reap.py
=======================================
Behavioral fixtures for pgai_lock_acquire's contention-path staleness
reaper (team/scripts/lib/lock_provenance.sh): a waiter that loses the
flock race no longer defers unconditionally -- it tests two
independently-verifiable conditions (dead pid, no provenance) against the
existing holder's provenance and reaps (removes the stale lock, narrates
dual-channel, retries once) when either holds. Age never reaps a live
holder: past the derived threshold the waiter narrates the holder as
overdue and defers.

Acceptance criteria exercised:
  - a lock whose provenance names a dead pid while some live process still
    holds its flock is NOT reaped: the waiter narrates
    lock.provenance.orphan_holder naming the dead recorded pid and the pid
    that really holds the flock, leaves the file byte-for-byte in place,
    and defers (RC=1).
  - a lock whose holder pid is alive is deferred exactly as before this
    reaper existed -- no reap, no behavior change (RC=1).
  - a genuinely contended lock with no provenance at all, older than the
    freshness window, is reaped, and the narration names the atomicity
    violation it evidences.
  - a live-pid lock older than the derived age threshold is DEFERRED, not
    reaped, and narrated as overdue naming the derivation; the same
    live-pid lock under the threshold is deferred with no overdue
    narration -- proving age is evidence against a live holder, never
    grounds to unlink its file.
  - `kill -9` of a lock-holding process leaves the very next contending
    acquisition attempt succeeding (the fleet self-recovers within one
    firing, no operator action).
  - the age threshold is DERIVED, never just the configured wake-periods
    product: it is max(configured periods x period length,
    max_runtime_seconds + max_task_seconds + kill_grace_seconds + margin).
    A live-pid lock under that derived floor is deferred with no overdue
    narration, and a lock at or past the floor is deferred with the
    derivation named in its overdue narration. A configured wake-periods
    product that resolves below the floor is clamped to the floor with
    its own dual-channel narration naming the floor.
  - regression: a live holder at 6m0s of age, under a configuration whose
    wake-periods product alone is also 6m (3 periods x 120s/period, the
    shipped default), must be DEFERRED, not reaped -- reproducing and
    closing the filed failure where the age-alone reaper fired on a chain
    that was merely mid-batch, not wedged.

Also covers the two small shared helpers the reaper introduces:
  pgai_lock_pid_liveness_verdict (held|stale|unparseable classifier, now
  shared with team/scripts/release-locks.sh) and
  pgai_lock_format_age_seconds (human-readable age string, now shared
  with team/scripts/lib/why_details.sh).
"""

from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import textwrap
import time

from tests.unit.shell_harness import run_bash


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


# Resolved from this file's own location, the same way the holder
# subprocesses below already self-locate via cwd=str(_repo_root()) -- a
# relative "scripts/lib/lock_provenance.sh" would depend on whatever
# directory the acquirer subprocess (run through shell_harness.run_bash())
# happens to start in, which is the cwd-coupling docs/test-authoring-
# guidelines.md's "State your working directory; never inherit it" forbids.
_LOCK_PROVENANCE_SH = _repo_root() / "scripts" / "lib" / "lock_provenance.sh"


def _wait_for(path: pathlib.Path, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while not path.exists() and time.time() < deadline:
        time.sleep(0.05)
    assert path.exists(), f"expected {path} to appear within {timeout}s"


# ---------------------------------------------------------------------------
# Fixture: a lock whose provenance names a dead pid while a live process
# still holds its flock is deferred to, never reaped, and the narration
# names who really holds it.
# ---------------------------------------------------------------------------


def test_dead_pid_record_with_flock_still_held_is_deferred_not_reaped(tmp_path: pathlib.Path) -> None:
    lock_file = tmp_path / "locks" / "dead-pid.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    ready_file = tmp_path / "holder-ready"
    done_file = tmp_path / "holder-done"

    # Genuinely flock the file (so the acquirer below is really contended),
    # but record a pid this holder never was -- the shape an orphan leaves:
    # a child that inherited a killed holder's lock descriptor keeps the
    # flock while the record still names the dead holder.
    holder_script = textwrap.dedent(f"""\
        exec 9>"{lock_file}"
        flock -x 9
        printf 'holder=ghost\\npid=99999999\\nop=old-op\\ntask=OLD-TASK\\n' > "{lock_file}"
        touch "{ready_file}"
        while [[ ! -f "{done_file}" ]]; do sleep 0.05; done
    """)
    holder = subprocess.Popen(["bash", "-c", holder_script], cwd=str(_repo_root()))
    try:
        _wait_for(ready_file)
        before = lock_file.read_bytes()
        inode_before = lock_file.stat().st_ino

        script = textwrap.dedent(f"""\
            set +e
            source {_LOCK_PROVENANCE_SH}
            fd=""
            pgai_lock_acquire "{lock_file}" fresh "$$" new-op NEW-TASK fd
            echo "RC=$?"
        """)
        result = run_bash(tmp_path, script)
        assert "RC=1" in result.stdout, (
            f"a flock held under a dead recorded pid must be deferred to, not reaped.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert lock_file.exists() and lock_file.stat().st_ino == inode_before, "the lock file must not be removed"
        assert lock_file.read_bytes() == before, "the holder's record must be left byte-for-byte in place"
        assert "stale_reaped" not in result.stderr, result.stderr
        assert "lock.provenance.orphan_holder" in result.stderr, result.stderr
        assert "99999999" in result.stderr, f"expected the narration to name the dead pid.\nstderr: {result.stderr}"
        assert str(holder.pid) in result.stderr, (
            f"expected the narration to name the pid that really holds the flock ({holder.pid}).\n"
            f"stderr: {result.stderr}"
        )
        assert "lock.provenance.contended" in result.stderr, result.stderr
    finally:
        done_file.touch()
        holder.wait(timeout=5)


# ---------------------------------------------------------------------------
# Fixture: a lock whose holder pid is alive is deferred exactly as today --
# no reap, no behavior change.
# ---------------------------------------------------------------------------


def test_live_pid_holder_lock_is_deferred_with_no_reap(tmp_path: pathlib.Path) -> None:
    lock_file = tmp_path / "locks" / "live-pid.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    ready_file = tmp_path / "holder-ready"
    done_file = tmp_path / "holder-done"

    holder_script = textwrap.dedent(f"""\
        source {_LOCK_PROVENANCE_SH}
        fd=""
        pgai_lock_acquire "{lock_file}" writer "$$" real-op TASK-LIVE fd
        touch "{ready_file}"
        while [[ ! -f "{done_file}" ]]; do sleep 0.05; done
        pgai_lock_release "{lock_file}" "$fd"
    """)
    holder = subprocess.Popen(["bash", "-c", holder_script], cwd=str(_repo_root()))
    try:
        _wait_for(ready_file)

        script = textwrap.dedent(f"""\
            set +e
            source {_LOCK_PROVENANCE_SH}
            fd2=""
            pgai_lock_acquire "{lock_file}" reader "$$" other-op TASK-2 fd2
            echo "RC=$?"
        """)
        result = run_bash(tmp_path, script)
        assert "RC=1" in result.stdout, (
            f"a live-pid holder must still be deferred, not reaped.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "lock.provenance.contended" in result.stderr
        assert "stale_reaped" not in result.stderr, (
            f"no reap narration is expected on the unchanged live-holder path.\n"
            f"stderr: {result.stderr}"
        )
    finally:
        done_file.touch()
        holder.wait(timeout=5)


# ---------------------------------------------------------------------------
# Fixture: a genuinely contended, provenance-less lock is reaped, and the
# narration names the atomicity violation it evidences.
# ---------------------------------------------------------------------------


def test_provenance_less_contended_lock_is_reaped_with_atomicity_narration(
    tmp_path: pathlib.Path,
) -> None:
    lock_file = tmp_path / "locks" / "no-provenance.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    ready_file = tmp_path / "holder-ready"
    done_file = tmp_path / "holder-done"

    # A caller that flocks the file directly without ever writing a
    # provenance record -- the on-disk shape of a non-atomic release or a
    # holder that predates/bypasses this helper. Backdated past the
    # freshness window: a provenance-less file is reapable only once it is
    # also old enough that no writer can still be mid-flight on it (a
    # fresh one is deferred to as contention -- see
    # test_lock_provenance_appears_atomically.py for that case).
    holder_script = textwrap.dedent(f"""\
        exec 9>"{lock_file}"
        flock -x 9
        touch -d "-120 seconds" "{lock_file}"
        touch "{ready_file}"
        while [[ ! -f "{done_file}" ]]; do sleep 0.05; done
    """)
    holder = subprocess.Popen(["bash", "-c", holder_script], cwd=str(_repo_root()))
    try:
        _wait_for(ready_file)

        script = textwrap.dedent(f"""\
            set -euo pipefail
            source {_LOCK_PROVENANCE_SH}
            fd=""
            pgai_lock_acquire "{lock_file}" fresh "$$" new-op NEW-TASK fd
            echo "RC=$?"
            pgai_lock_release "{lock_file}" "$fd"
        """)
        result = run_bash(tmp_path, script)
        assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        assert "RC=0" in result.stdout, (
            f"a provenance-less contended lock must be reaped, not deferred forever.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "lock.provenance.stale_reaped.no_provenance" in result.stderr
        assert "atomicity" in result.stderr.lower() or "non-atomic" in result.stderr.lower(), (
            f"expected the narration to name the atomicity violation a "
            f"provenance-less lock evidences.\nstderr: {result.stderr}"
        )
    finally:
        done_file.touch()
        holder.wait(timeout=5)


# ---------------------------------------------------------------------------
# Fixture: age is an independent third condition. A live-pid lock older
# than the configured threshold is reaped on age alone; the same live-pid
# lock under the threshold is left deferred.
# ---------------------------------------------------------------------------


# A small, fully-controlled chain-budget floor for the two fixtures below:
# MAX_RUNTIME_SECONDS=300 + MAX_TASK_SECONDS=0 + KILL_GRACE_SECONDS=10 +
# the fixed 120s margin (_PGAI_LOCK_CHAIN_BUDGET_MARGIN_SECONDS in
# lock_provenance.sh) = a 430s floor -- deliberately ABOVE the
# shipped-default wake-periods product (3 periods x 120s/period = 360s) so
# the floor, not the periods count, is what these fixtures are actually
# exercising. Reachable by backdating a lock file's mtime instead of
# actually sleeping. All three env vars are pinned explicitly rather than
# left to whatever the ambient process environment happens to carry (this
# test suite's own invocation may itself be running inside a wake chain
# that already exports these names) -- deterministic input, deterministic
# floor, regardless of the calling process's own environment.
_SMALL_FLOOR_ENV = {
    "KANBAN_ROOT": "",
    "MAX_RUNTIME_SECONDS": "300",
    "MAX_TASK_SECONDS": "0",
    "KILL_GRACE_SECONDS": "10",
}
_SMALL_FLOOR_SECONDS = 430  # 300 + 0 + 10 + 120s margin, above the 360s default periods product


def test_live_pid_lock_older_than_derived_floor_is_deferred_and_narrated_overdue(
    tmp_path: pathlib.Path,
) -> None:
    lock_file = tmp_path / "locks" / "aged.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    ready_file = tmp_path / "holder-ready"
    done_file = tmp_path / "holder-done"

    # Backdate past the pinned 430s floor (_SMALL_FLOOR_ENV above) so age
    # is the only thing that could distinguish this holder from a fresh
    # one -- the recorded pid is this holder's own real, live pid.
    holder_script = textwrap.dedent(f"""\
        source {_LOCK_PROVENANCE_SH}
        fd=""
        pgai_lock_acquire "{lock_file}" writer "$$" real-op TASK-AGED fd
        touch -d "-{_SMALL_FLOOR_SECONDS + 5} seconds" "{lock_file}"
        touch "{ready_file}"
        while [[ ! -f "{done_file}" ]]; do sleep 0.05; done
        pgai_lock_release "{lock_file}" "$fd"
    """)
    holder = subprocess.Popen(["bash", "-c", holder_script], cwd=str(_repo_root()))
    try:
        _wait_for(ready_file)

        script = textwrap.dedent(f"""\
            set +e
            source {_LOCK_PROVENANCE_SH}
            fd2=""
            pgai_lock_acquire "{lock_file}" reader "$$" other-op TASK-2 fd2
            echo "RC=$?"
        """)
        result = run_bash(tmp_path, script, extra_env=_SMALL_FLOOR_ENV)
        assert "RC=1" in result.stdout, (
            f"a lock older than the derived floor whose recorded pid is alive "
            f"must be deferred, never reaped.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "stale_reaped" not in result.stderr, (
            f"no reap of any kind is allowed against a live holder.\nstderr: {result.stderr}"
        )
        assert lock_file.exists() and "holder=writer" in lock_file.read_text(), (
            "the live holder's lock file must be left exactly as it was"
        )
        assert "lock.provenance.holder_overdue" in result.stderr
        assert "kill -0" in result.stderr, (
            f"expected the overdue narration to note the pid is still alive.\n"
            f"stderr: {result.stderr}"
        )
        assert "derivation:" in result.stderr, (
            f"expected the overdue narration to state the derivation "
            f"(which inputs produced the threshold), not just the seconds "
            f"value.\nstderr: {result.stderr}"
        )
        assert "--force" in result.stderr, (
            f"expected the overdue remedy to name release-locks --force.\nstderr: {result.stderr}"
        )
        assert "lock.provenance.contended" in result.stderr
    finally:
        done_file.touch()
        holder.wait(timeout=5)


def test_live_pid_lock_just_under_derived_floor_is_deferred_not_reaped(
    tmp_path: pathlib.Path,
) -> None:
    lock_file = tmp_path / "locks" / "fresh.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    ready_file = tmp_path / "holder-ready"
    done_file = tmp_path / "holder-done"

    # Just under the pinned 430s floor (_SMALL_FLOOR_ENV/_SMALL_FLOOR_
    # SECONDS above) -- the boundary the staleness reap must never cross
    # regardless of how small a wake-periods override would otherwise make
    # the threshold. 20s of margin (rather than exactly 1s) absorbs this
    # test's own real subprocess-launch latency between backdating the
    # lock file and the acquirer's own age computation -- a real, not
    # simulated, elapsed-time gap that a single-second margin would make
    # this fixture flaky under host load, without weakening what the
    # fixture proves (the threshold floor, not a timing coincidence, is what
    # keeps this holder deferred).
    holder_script = textwrap.dedent(f"""\
        source {_LOCK_PROVENANCE_SH}
        fd=""
        pgai_lock_acquire "{lock_file}" writer "$$" real-op TASK-FRESH fd
        touch -d "-{_SMALL_FLOOR_SECONDS - 20} seconds" "{lock_file}"
        touch "{ready_file}"
        while [[ ! -f "{done_file}" ]]; do sleep 0.05; done
        pgai_lock_release "{lock_file}" "$fd"
    """)
    holder = subprocess.Popen(["bash", "-c", holder_script], cwd=str(_repo_root()))
    try:
        _wait_for(ready_file)

        script = textwrap.dedent(f"""\
            set +e
            source {_LOCK_PROVENANCE_SH}
            fd2=""
            pgai_lock_acquire "{lock_file}" reader "$$" other-op TASK-2 fd2
            echo "RC=$?"
        """)
        result = run_bash(tmp_path, script, extra_env=_SMALL_FLOOR_ENV)
        assert "RC=1" in result.stdout, (
            f"a live-pid lock under the derived floor must be deferred, "
            f"never reaped.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "stale_reaped" not in result.stderr, (
            f"no reap narration is expected below the derived floor.\n"
            f"stderr: {result.stderr}"
        )
        assert "holder_overdue" not in result.stderr, (
            f"a live holder under the floor is not overdue.\nstderr: {result.stderr}"
        )
        assert "lock.provenance.contended" in result.stderr, (
            f"expected the deferral itself to be narrated dual-channel.\n"
            f"stderr: {result.stderr}"
        )
    finally:
        done_file.touch()
        holder.wait(timeout=5)


def test_live_holder_at_six_minutes_under_default_wake_periods_window_is_deferred(
    tmp_path: pathlib.Path,
) -> None:
    """Regression: the filed failure was a live holder reaped at 6m0s of
    age against a 6m window -- exactly 3 wake periods (the shipped
    default) x 120s/period. An age-alone reaper that compares age only
    against that periods-based product trips a reap at 6m0s >= 6m0s even
    though the recorded pid is alive and a wake chain can legitimately
    hold this lock for its whole batch. The reaper's effective threshold
    is max(periods product, chain-budget
    floor); with the shipped defaults (max_runtime_seconds=14400,
    max_task_seconds=5400, kill_grace_seconds=30) the floor is ~19950s,
    so a 360s-old live holder must be deferred, not reaped -- and a live
    holder is never reaped on age at all.
    """
    lock_file = tmp_path / "locks" / "six-minutes.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    ready_file = tmp_path / "holder-ready"
    done_file = tmp_path / "holder-done"

    # No project.cfg/kanban.cfg on the sourcing path, and MAX_RUNTIME_
    # SECONDS/MAX_TASK_SECONDS/KILL_GRACE_SECONDS explicitly blanked so this
    # reproduces the shipped-default chain budget regardless of the calling
    # process's own environment -- pp_stale_lock_reap_after_wake_periods
    # falls back to 3 periods, and _pgai_lock_wake_cfg_seconds falls back
    # to 14400/5400/30.
    default_env = {
        "KANBAN_ROOT": "",
        "MAX_RUNTIME_SECONDS": "",
        "MAX_TASK_SECONDS": "",
        "KILL_GRACE_SECONDS": "",
    }

    holder_script = textwrap.dedent(f"""\
        source {_LOCK_PROVENANCE_SH}
        fd=""
        pgai_lock_acquire "{lock_file}" writer "$$" real-op TASK-WAKE-CHAIN fd
        touch -d "-6 minutes" "{lock_file}"
        touch "{ready_file}"
        while [[ ! -f "{done_file}" ]]; do sleep 0.05; done
        pgai_lock_release "{lock_file}" "$fd"
    """)
    holder = subprocess.Popen(
        ["bash", "-c", holder_script],
        cwd=str(_repo_root()),
        env={**os.environ, **default_env},
    )
    try:
        _wait_for(ready_file)

        script = textwrap.dedent(f"""\
            set +e
            source {_LOCK_PROVENANCE_SH}
            fd2=""
            pgai_lock_acquire "{lock_file}" reader "$$" other-op TASK-2 fd2
            echo "RC=$?"
        """)
        result = run_bash(tmp_path, script, extra_env=default_env)
        assert "RC=1" in result.stdout, (
            f"a live chain holder at 6m0s must be deferred, not reaped, "
            f"when the derived floor (~19950s under shipped defaults) is "
            f"correctly enforced.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "stale_reaped" not in result.stderr, (
            f"the filed failure was exactly this: an age-alone reap firing "
            f"on a live, on-budget chain holder.\nstderr: {result.stderr}"
        )
        assert "lock.provenance.contended" in result.stderr
    finally:
        done_file.touch()
        holder.wait(timeout=5)


# ---------------------------------------------------------------------------
# Fixture: `kill -9` of a lock-holding process leaves the very next
# contending acquisition succeeding -- the fleet self-recovers within one
# firing, with no operator action.
# ---------------------------------------------------------------------------


def test_kill9_holder_self_recovers_on_next_acquisition(tmp_path: pathlib.Path) -> None:
    lock_file = tmp_path / "locks" / "kill9.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    ready_file = tmp_path / "holder-ready"

    holder_script = textwrap.dedent(f"""\
        source {_LOCK_PROVENANCE_SH}
        fd=""
        pgai_lock_acquire "{lock_file}" wake-holder "$$" wake-chain:demo TASK-KILL9 fd
        touch "{ready_file}"
        # exec, not a loop of short sleeps: a child started while the lock
        # is held inherits its descriptor and would keep the flock after
        # the holder is killed (an orphan holder, which is deferred to).
        exec sleep 600
    """)
    holder = subprocess.Popen(["bash", "-c", holder_script], cwd=str(_repo_root()))
    try:
        _wait_for(ready_file)
        assert lock_file.exists()

        holder.send_signal(signal.SIGKILL)
        # A real kill -9: the process runs no more code of its own, so this
        # is deliberately NOT a graceful wait for a self-release -- the
        # kernel closes every fd (and so the flock) on process death
        # regardless. wait() here only confirms the process has fully
        # exited before the next acquisition attempt, so the test is not
        # racing the kernel's own cleanup.
        holder.wait(timeout=5.0)
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=5.0)

    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}
        fd2=""
        pgai_lock_acquire "{lock_file}" next-holder "$$" wake-chain:demo TASK-NEXT fd2
        echo "RC=$?"
        pgai_lock_release "{lock_file}" "$fd2"
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "RC=0" in result.stdout, (
        "the very next acquisition attempt after a killed holder must "
        f"succeed -- self-recovery within one firing.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# Shared helper: pgai_lock_pid_liveness_verdict (held|stale|unparseable),
# now consumed by both this reaper and team/scripts/release-locks.sh.
# ---------------------------------------------------------------------------


def test_pid_liveness_verdict_classifies_held_stale_and_unparseable(
    tmp_path: pathlib.Path,
) -> None:
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}
        echo "HELD=$(pgai_lock_pid_liveness_verdict "holder=x
pid=$$
op=y
task=z")"
        echo "STALE=$(pgai_lock_pid_liveness_verdict "holder=x
pid=99999999
op=y
task=z")"
        echo "UNPARSEABLE_EMPTY=$(pgai_lock_pid_liveness_verdict "")"
        echo "UNPARSEABLE_NO_PID=$(pgai_lock_pid_liveness_verdict "holder=x
op=y
task=z")"
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "HELD=held" in result.stdout
    assert "STALE=stale" in result.stdout
    assert "UNPARSEABLE_EMPTY=unparseable" in result.stdout
    assert "UNPARSEABLE_NO_PID=unparseable" in result.stdout


# ---------------------------------------------------------------------------
# Shared helper: pgai_lock_format_age_seconds, now consumed by both this
# reaper's narration and team/scripts/lib/why_details.sh's lock pane.
# ---------------------------------------------------------------------------


def test_format_age_seconds_renders_short_human_units(tmp_path: pathlib.Path) -> None:
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}
        pgai_lock_format_age_seconds 45
        echo
        pgai_lock_format_age_seconds 125
        echo
        pgai_lock_format_age_seconds 7384
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    lines = result.stdout.splitlines()
    assert lines[0] == "45s"
    assert lines[1] == "2m 5s"
    assert lines[2] == "2h 3m"


# ---------------------------------------------------------------------------
# Threshold derivation: pgai_lock_chain_budget_floor_seconds,
# pgai_lock_stale_age_threshold_seconds, and
# pgai_lock_stale_age_threshold_derivation. These exercise the math in
# isolation, without a real contended lock, complementing the fixtures
# above that exercise it through an actual pgai_lock_acquire call.
# ---------------------------------------------------------------------------


def test_chain_budget_floor_uses_max_runtime_plus_max_task_plus_kill_grace_plus_margin(
    tmp_path: pathlib.Path,
) -> None:
    """run_project_chain checks its caps only between tasks, so the last
    task dispatched just under max_runtime_seconds may run its whole
    max_task_seconds past it: the floor carries all three [wake] budgets."""
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}
        pgai_lock_chain_budget_floor_seconds
    """)
    result = run_bash(
        tmp_path,
        script,
        extra_env={
            "KANBAN_ROOT": "",
            "MAX_RUNTIME_SECONDS": "100",
            "MAX_TASK_SECONDS": "50",
            "KILL_GRACE_SECONDS": "10",
        },
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    # 100 (max_runtime) + 50 (max_task) + 10 (kill_grace) + 120 (fixed margin) = 280
    assert result.stdout.strip() == "280"


def test_derived_threshold_exceeds_max_runtime_plus_max_task_plus_kill_grace_for_shipped_defaults(
    tmp_path: pathlib.Path,
) -> None:
    """Acceptance: for the shipped kanban.cfg_example values
    (max_runtime_seconds=14400, max_task_seconds=5400,
    kill_grace_seconds=30), the derived threshold must be strictly greater
    than their sum (19830s) -- the fixed margin alone guarantees this, but
    the assertion pins the invariant against the actual shipped numbers
    rather than the margin constant in isolation.
    """
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}
        pgai_lock_stale_age_threshold_seconds 2>/dev/null
    """)
    result = run_bash(
        tmp_path,
        script,
        extra_env={
            "KANBAN_ROOT": "",
            "MAX_RUNTIME_SECONDS": "14400",
            "MAX_TASK_SECONDS": "5400",
            "KILL_GRACE_SECONDS": "30",
        },
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    derived = int(result.stdout.strip())
    assert derived > 14400 + 5400 + 30, (
        f"derived threshold {derived}s must exceed max_runtime_seconds + "
        f"max_task_seconds + kill_grace_seconds (19830s) for the shipped defaults"
    )


def test_configured_periods_product_below_floor_is_clamped_silently_via_public_wrapper(
    tmp_path: pathlib.Path,
) -> None:
    """Acceptance: a configured stale_lock_reap_after_wake_periods whose
    product resolves below the chain-budget floor is clamped to the floor.
    The $(...)-safe public wrapper (used by read-only display paths --
    the lane verdict's lock pane and STUCK-LOCK correlation) never
    narrates the clamp itself: narrating on every read-only query would
    write a refusal-ledger entry on every call, violating why_details.sh's
    own documented read-only contract. See the companion test below for
    the narrated path pgai_lock_acquire's reap decision actually uses.
    """
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}
        pgai_lock_stale_age_threshold_seconds
    """)
    # Shipped-default periods product (3 * 120s/period = 360s) is below the
    # 430s floor this env pins (_SMALL_FLOOR_ENV: 300 + 10 + 120 margin) --
    # the clamp must fire.
    result = run_bash(tmp_path, script, extra_env=_SMALL_FLOOR_ENV)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert result.stdout.strip() == str(_SMALL_FLOOR_SECONDS), (
        f"the effective threshold must be the floor ({_SMALL_FLOOR_SECONDS}s), "
        f"not the unclamped shipped-default periods product.\nstdout: {result.stdout}"
    )
    assert result.stderr == "", (
        f"a read-only threshold query must never narrate or write anywhere.\n"
        f"stderr: {result.stderr}"
    )


def test_configured_periods_product_below_floor_is_narrated_when_acted_on(
    tmp_path: pathlib.Path,
) -> None:
    """Acceptance: a configured stale_lock_reap_after_wake_periods whose
    product resolves below the chain-budget floor is narrated dual-channel
    naming the floor value when the caller passes "narrate" -- the
    internal resolver's contract for a caller that is actually ACTING on
    the clamped value, as pgai_lock_acquire's own reap decision does.
    """
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}
        _pgai_lock_resolve_stale_threshold "" narrate
        echo "$_PGAI_LOCK_LAST_THRESHOLD_SECONDS"
    """)
    result = run_bash(tmp_path, script, extra_env=_SMALL_FLOOR_ENV)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert result.stdout.strip() == str(_SMALL_FLOOR_SECONDS), (
        f"the effective threshold must be the floor ({_SMALL_FLOOR_SECONDS}s), "
        f"not the unclamped shipped-default periods product.\nstdout: {result.stdout}"
    )
    assert "error:   lock.stale_threshold.floor_enforced" in result.stderr
    assert f"{_SMALL_FLOOR_SECONDS}s" in result.stderr, (
        f"expected the clamp narration to name the floor value.\n"
        f"stderr: {result.stderr}"
    )


def test_floor_enforced_narration_fires_once_per_process_when_unchanged(
    tmp_path: pathlib.Path,
) -> None:
    """Narration-rate memoization: five narrated resolver calls in the SAME process,
    all with the same below-floor configuration (so the clamp derivation
    never changes), narrate the lock.stale_threshold.floor_enforced
    dual-channel block exactly once -- "narrate ONCE per process
    (memoized)" -- while every call still resolves to the same clamped
    floor value, unchanged."""
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}
        for i in $(seq 1 5); do
          _pgai_lock_resolve_stale_threshold "" narrate
          echo "THRESHOLD=$_PGAI_LOCK_LAST_THRESHOLD_SECONDS"
        done
    """)
    result = run_bash(tmp_path, script, extra_env=_SMALL_FLOOR_ENV)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    threshold_lines = [
        ln for ln in result.stdout.splitlines() if ln.startswith("THRESHOLD=")
    ]
    assert threshold_lines == [f"THRESHOLD={_SMALL_FLOOR_SECONDS}"] * 5, (
        f"every call must still resolve to the same clamped floor.\n"
        f"stdout: {result.stdout}"
    )
    narration_count = result.stderr.count("error:   lock.stale_threshold.floor_enforced")
    assert narration_count == 1, (
        f"expected exactly one clamp narration across 5 identically-clamped "
        f"calls in the same process, got {narration_count}.\nstderr: {result.stderr}"
    )


def test_floor_enforced_narration_absent_when_config_at_or_above_floor(
    tmp_path: pathlib.Path,
) -> None:
    """Acceptance: raising stale_lock_reap_after_wake_periods past the
    derived floor makes the clamp narration absent entirely -- not
    merely rate-limited -- even on the narrated call path
    pgai_lock_acquire's own reap decision uses."""
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}
        _pgai_lock_resolve_stale_threshold "" narrate
        echo "THRESHOLD=$_PGAI_LOCK_LAST_THRESHOLD_SECONDS"
    """)
    # A tiny floor (0 + 0 + 0 + 120s margin = 120s) that the shipped-default
    # periods product (3 * 120s/period = 360s) already exceeds -- no
    # clamp, so no narration, on the narrated call path.
    result = run_bash(
        tmp_path,
        script,
        extra_env={
            "KANBAN_ROOT": "",
            "MAX_RUNTIME_SECONDS": "0",
            "MAX_TASK_SECONDS": "0",
            "KILL_GRACE_SECONDS": "0",
        },
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "THRESHOLD=360" in result.stdout, (
        f"expected the unclamped configured product (360s).\nstdout: {result.stdout}"
    )
    assert result.stderr == "", (
        f"expected no narration at all when the config already clears the "
        f"floor.\nstderr: {result.stderr}"
    )


def test_threshold_derivation_names_which_input_won(tmp_path: pathlib.Path) -> None:
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}
        echo "ABOVE=$(pgai_lock_stale_age_threshold_derivation 2>/dev/null)"
    """)
    # A tiny floor (0 + 0 + 0 + 120 margin = 120s) that the shipped-default
    # periods product (3 * 120s = 360s) already exceeds -- the derivation
    # must say the configured product won, not the floor.
    result = run_bash(
        tmp_path,
        script,
        extra_env={
            "KANBAN_ROOT": "",
            "MAX_RUNTIME_SECONDS": "0",
            "MAX_TASK_SECONDS": "0",
            "KILL_GRACE_SECONDS": "0",
        },
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "360s" in result.stdout and "120s" in result.stdout, (
        f"expected the derivation to name both the configured product and "
        f"the floor it exceeded.\nstdout: {result.stdout}"
    )
    assert "at or above the max-chain-budget floor" in result.stdout
