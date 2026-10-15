"""
test_lock_release_atomicity.py
================================
Behavioral fixtures for atomic lock release plus trap/finally coverage of
every wake exit path.

Exercises team/scripts/lib/lock_provenance.sh's:
  - the two-step release (clear provenance, then remove the lock file
    itself) that leaves no on-disk trace after a clean pgai_lock_release,
    and the recovery sweep that completes an interrupted release the next
    time pgai_lock_acquire is called against the same path;
  - the held-lock registry (_PGAI_LOCK_HELD_FDS) and the trap/finally sweep
    (pgai_lock_release_all_held), installed once per process either by
    composing into script_common.sh's CLEANUP_ON_EXIT_PRE_HOOK (the wake
    chain's own convention, since wake_bootstrap.sh already installs
    cleanup_on_exit before any lib sources this file) or, absent any
    existing EXIT trap, via this file's own bare EXIT/SIGTERM/SIGINT trap.

Acceptance criteria exercised:
  - a seeded crash between sidecar removal and lock removal is completed by
    the recovery sweep on the next acquisition attempt; no orphan lock
    remains.
  - a HALT-style early, clean exit that never reaches its own release call
    site still ends with the lock and provenance fully gone, via the
    trap/finally sweep.
  - SIGTERM to a lock-holding process releases the lock and provenance
    together before exit.
  - across two consecutive "wake generations" against the same lock name,
    no lock file survives its creating process by more than one firing.
"""

from __future__ import annotations

import pathlib
import signal
import subprocess
import textwrap
import time

from tests.unit.shell_harness import run_bash

_LOCK_PROVENANCE_SH = "scripts/lib/lock_provenance.sh"
_SCRIPT_COMMON_SH = "scripts/lib/script_common.sh"


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Fixture: a seeded crash between sidecar removal and lock removal is
# completed by the recovery sweep on the next acquisition attempt; no
# orphan lock remains.
# ---------------------------------------------------------------------------


def test_recovery_sweep_completes_interrupted_release(tmp_path: pathlib.Path) -> None:
    lock_file = tmp_path / "locks" / "crash-sim.lock"

    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}

        # Seed the crash: acquire, then perform ONLY step 1 of release
        # (clear provenance) inside a subshell, and exit the subshell
        # WITHOUT reaching step 2 (removing the file) or an explicit
        # pgai_lock_release call. The subshell's own exit closes its fd
        # (and so the flock) exactly as the kernel would for a real crash
        # -- bash does not re-fire an inherited EXIT trap for a subshell's
        # own exit, so this faithfully simulates a process killed between
        # the two release steps without needing a crash hook in
        # production code.
        (
            fd=""
            pgai_lock_acquire "{lock_file}" holder1 "$$" crash-sim TASK-X fd
            : > "{lock_file}"
            exit 0
        )

        echo "PRE_EXISTING=$([[ -e "{lock_file}" ]] && echo yes || echo no)"

        fd2=""
        pgai_lock_acquire "{lock_file}" holder2 "$$" recovered-op TASK-Y fd2
        echo "RC2=$?"
        pgai_lock_release "{lock_file}" "$fd2"
        echo "FILE_EXISTS_AFTER=$([[ -e "{lock_file}" ]] && echo yes || echo no)"
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "PRE_EXISTING=yes" in result.stdout, (
        "the seeded crash must leave a provenance-less lock file behind "
        f"before the recovery attempt.\nstdout: {result.stdout}"
    )
    assert "RC2=0" in result.stdout, (
        "the recovery acquisition must succeed, not be blocked by the "
        f"interrupted release.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "recovery sweep" in result.stderr, (
        f"expected a narrated recovery-sweep diagnostic.\nstderr: {result.stderr}"
    )
    assert "FILE_EXISTS_AFTER=no" in result.stdout, (
        f"no orphan lock file may remain after the recovering acquisition "
        f"releases cleanly.\nstdout: {result.stdout}"
    )


# ---------------------------------------------------------------------------
# Fixture: a HALT-style early, clean exit that never reaches its own
# release call site still ends with the lock and provenance gone, via the
# trap/finally sweep lock_provenance.sh installs.
# ---------------------------------------------------------------------------


def test_early_exit_without_explicit_release_still_releases_via_trap(
    tmp_path: pathlib.Path,
) -> None:
    lock_file = tmp_path / "locks" / "halt-exit.lock"

    script = textwrap.dedent(f"""\
        set -uo pipefail
        source {_SCRIPT_COMMON_SH}
        trap cleanup_on_exit EXIT
        trap cleanup_on_exit SIGTERM SIGINT
        source {_LOCK_PROVENANCE_SH}

        fd=""
        pgai_lock_acquire "{lock_file}" wake-holder "$$" wake-chain:demo TASK-HALT fd
        echo "ACQUIRE_RC=$?"

        # Simulate a HALT flag detected mid-chain: the wake process exits
        # cleanly right here WITHOUT calling pgai_lock_release itself --
        # exactly the missed-call-site failure mode a signal, a provider
        # refusal, or an idle exit could equally trigger. The trap/finally
        # sweep must still release it.
        exit 0
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "ACQUIRE_RC=0" in result.stdout
    assert not lock_file.exists(), (
        "expected the trap/finally sweep to remove the lock file on an "
        "early exit that never called pgai_lock_release.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "pgai_lock_release_all_held" in result.stderr, (
        "expected the sweep to narrate that it found and released a "
        f"still-held lock.\nstderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# Fixture: SIGTERM to a lock-holding process releases the lock and
# provenance together before exit.
# ---------------------------------------------------------------------------


def test_sigterm_releases_lock_and_provenance_before_exit(
    tmp_path: pathlib.Path,
) -> None:
    lock_file = tmp_path / "locks" / "sigterm.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    ready_file = tmp_path / "holder-ready"

    holder_script = textwrap.dedent(f"""\
        set -uo pipefail
        source {_SCRIPT_COMMON_SH}
        trap cleanup_on_exit EXIT
        trap cleanup_on_exit SIGTERM SIGINT
        source {_LOCK_PROVENANCE_SH}

        fd=""
        pgai_lock_acquire "{lock_file}" wake-holder "$$" wake-chain:demo TASK-SIGTERM fd
        touch "{ready_file}"
        while true; do sleep 0.05; done
    """)
    holder = subprocess.Popen(
        ["bash", "-c", holder_script],
        cwd=str(_repo_root()),
    )
    try:
        deadline = time.time() + 5.0
        while not ready_file.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert ready_file.exists(), "holder process did not signal readiness"
        assert lock_file.exists(), (
            "holder should have written provenance before signaling ready"
        )

        holder.send_signal(signal.SIGTERM)
        holder.wait(timeout=5.0)
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=5.0)

    assert not lock_file.exists(), (
        "SIGTERM to a lock-holding process must release both the lock and "
        "its provenance -- the file must be gone, not merely emptied."
    )


# ---------------------------------------------------------------------------
# Fixture: across two consecutive "wake generations" against the same lock
# name, no lock file survives its creating process by more than one
# firing.
# ---------------------------------------------------------------------------


def test_two_consecutive_generations_leave_no_orphan_lock(
    tmp_path: pathlib.Path,
) -> None:
    lock_file = tmp_path / "locks" / "two-generations.lock"

    # Generation 1: acquires, then exits early without an explicit release
    # (mirrors the HALT-style fixture above) -- the trap/finally sweep is
    # this generation's only cleanup.
    gen1_script = textwrap.dedent(f"""\
        set -uo pipefail
        source {_SCRIPT_COMMON_SH}
        trap cleanup_on_exit EXIT
        trap cleanup_on_exit SIGTERM SIGINT
        source {_LOCK_PROVENANCE_SH}
        fd=""
        pgai_lock_acquire "{lock_file}" gen1 "$$" wake-chain:demo TASK-GEN1 fd
        exit 0
    """)
    gen1 = run_bash(tmp_path, gen1_script)
    assert gen1.returncode == 0, f"stdout:\n{gen1.stdout}\nstderr:\n{gen1.stderr}"
    assert not lock_file.exists(), (
        "generation 1 must leave no lock file behind after its own exit.\n"
        f"stderr: {gen1.stderr}"
    )

    # Generation 2: a fresh process, a fresh firing, same lock name -- must
    # acquire immediately (no contention left over from generation 1) and,
    # after its own clean release, leave nothing behind either.
    gen2_script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}
        fd=""
        pgai_lock_acquire "{lock_file}" gen2 "$$" wake-chain:demo TASK-GEN2 fd
        echo "RC=$?"
        pgai_lock_release "{lock_file}" "$fd"
    """)
    gen2 = run_bash(tmp_path, gen2_script)
    assert gen2.returncode == 0, f"stdout:\n{gen2.stdout}\nstderr:\n{gen2.stderr}"
    assert "RC=0" in gen2.stdout, (
        "generation 2 must acquire without contention.\n"
        f"stdout: {gen2.stdout}\nstderr: {gen2.stderr}"
    )
    assert not lock_file.exists(), (
        "no lock file may survive its creating process by more than one firing."
    )
