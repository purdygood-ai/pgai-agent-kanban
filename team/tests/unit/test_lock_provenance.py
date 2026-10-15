"""
test_lock_provenance.py
========================
Behavioral unit tests for the shared lock provenance helper pair
(team/scripts/lib/lock_provenance.sh: pgai_lock_acquire / pgai_lock_release)
and its adoption at the three per-project/per-firing acquisition sites.

Acceptance criteria exercised:
  (1) A successful acquisition leaves the lock file holding all four
      provenance fields; dropping the lock clears them.
  (2) A second process attempting the same lock is declined and its message
      names the holder, pid, op, and task recorded by the first.
  (3) An acquisition attempt with any of the four fields absent or blank is
      refused dual-channel and takes no lock -- exercised once per field.
  (4) Provenance left behind by a pid that no longer exists does not make
      the lock permanently unacquirable, and produces a documented,
      observable outcome (a diagnostic naming the stale pid).
  (4b) A lock whose recorded holder was killed while a child that inherited
      its lock descriptor still holds the flock is deferred to: a second
      shell's acquisition returns the contended exit, leaves the file
      byte-for-byte in place, and names the child as the real holder.
  (5) Narration-rate memoization: the lock.provenance.contended dual-channel
      narration fires at most once per lock file per process regardless
      of how many times contention is probed, while
      pgai_lock_contention_probe_count keeps counting every probe so a
      caller that gives up can still report the total in its own closing
      summary; lock behavior itself (contention still defers) is
      unchanged.
  (6) Grep-zero: the only `flock` invocation left in the wake and overwatch
      libraries is the shared helper's own.

All tests run against synthetic lock directories under tmp_path and never
touch the live kanban root or its locks/ directory.
"""

from __future__ import annotations

import os
import pathlib
import re
import signal
import subprocess
import textwrap
import time

import pytest

from tests.unit.shell_harness import run_bash

_LOCK_PROVENANCE_SH = "scripts/lib/lock_provenance.sh"
_WAKE_DRIVER_SH = "scripts/lib/wake_driver.sh"
_OVERWATCH_LIB_SH = "scripts/lib/overwatch_lib.sh"
_OVERWATCH_PROTOCOL_SH = "scripts/lib/overwatch_protocol.sh"


# ---------------------------------------------------------------------------
# (1) Successful acquisition writes all four fields; release clears them.
# ---------------------------------------------------------------------------


def test_acquire_writes_all_four_fields_and_release_clears_them(
    tmp_path: pathlib.Path,
) -> None:
    lock_file = tmp_path / "locks" / "test-repo.lock"

    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}
        the_fd=""
        pgai_lock_acquire "{lock_file}" coder 12345 "wake-chain:demo" TASK-1 the_fd
        echo "ACQUIRE_RC=$?"
        cat "{lock_file}"
        pgai_lock_release "{lock_file}" "$the_fd"
        echo "AFTER_RELEASE:[$(cat "{lock_file}")]"
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "ACQUIRE_RC=0" in result.stdout

    assert "holder=coder" in result.stdout
    assert "pid=12345" in result.stdout
    assert "op=wake-chain:demo" in result.stdout
    assert "task=TASK-1" in result.stdout

    assert "AFTER_RELEASE:[]" in result.stdout, (
        f"Expected the lock file to be empty after release.\nstdout: {result.stdout}"
    )


# ---------------------------------------------------------------------------
# (2) A contended acquisition names the holder, pid, op, and task.
# ---------------------------------------------------------------------------


def test_contended_acquisition_message_names_holder_pid_op_task(
    tmp_path: pathlib.Path,
) -> None:
    lock_file = tmp_path / "locks" / "contended.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    # Hold the lock from a long-lived subprocess so the test's own attempt
    # is genuinely contended. The holder records its OWN real pid ($$, not
    # a synthetic literal) -- pgai_lock_acquire's contention path treats a
    # held flock under a dead recorded pid as an orphan holder and narrates
    # it differently (see test (4b) below), so a live-holder fixture must
    # record a pid that is actually still alive for the whole hold.
    import subprocess
    import time

    ready_file = tmp_path / "holder-ready"
    done_file = tmp_path / "holder-done"
    holder_pid_file = tmp_path / "holder-pid"
    holder_script = textwrap.dedent(f"""\
        source {_LOCK_PROVENANCE_SH}
        fd=""
        pgai_lock_acquire "{lock_file}" writer "$$" "some-op" TASK-9 fd
        echo "$$" > "{holder_pid_file}"
        touch "{ready_file}"
        while [[ ! -f "{done_file}" ]]; do sleep 0.05; done
        pgai_lock_release "{lock_file}" "$fd"
    """)
    holder = subprocess.Popen(
        ["bash", "-c", holder_script],
        cwd=str(pathlib.Path(__file__).resolve().parents[2]),
    )
    try:
        deadline = time.time() + 5.0
        while not ready_file.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert ready_file.exists(), "holder subprocess did not signal readiness"
        holder_pid = holder_pid_file.read_text(encoding="utf-8").strip()
        assert holder_pid, "holder subprocess did not record its own pid"

        script = textwrap.dedent(f"""\
            set +e
            source {_LOCK_PROVENANCE_SH}
            fd2=""
            pgai_lock_acquire "{lock_file}" reader 1234 "other-op" TASK-2 fd2
            echo "RC=$?"
        """)
        result = run_bash(tmp_path, script)
        assert "RC=1" in result.stdout, (
            f"Expected contention (rc=1) against a live holder -- a live pid must "
            f"never be reaped.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "holder=writer" in result.stderr
        assert f"pid={holder_pid}" in result.stderr
        assert "op=some-op" in result.stderr
        assert "task=TASK-9" in result.stderr
    finally:
        done_file.touch()
        holder.wait(timeout=5)


# ---------------------------------------------------------------------------
# (3) Incomplete provenance is refused dual-channel, once per missing field.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "holder,pid,op,task,missing_name",
    [
        ("", "1", "op", "task", "holder"),
        ("holder", "", "op", "task", "pid"),
        ("holder", "1", "", "task", "op"),
        ("holder", "1", "op", "", "task"),
    ],
)
def test_acquire_refuses_when_a_field_is_blank(
    tmp_path: pathlib.Path, holder: str, pid: str, op: str, task: str, missing_name: str
) -> None:
    lock_file = tmp_path / "locks" / f"incomplete-{missing_name}.lock"

    script = textwrap.dedent(f"""\
        set +e
        source {_LOCK_PROVENANCE_SH}
        fd=""
        pgai_lock_acquire "{lock_file}" "{holder}" "{pid}" "{op}" "{task}" fd
        echo "RC=$?"
        echo "FD_EMPTY=$([[ -z \"$fd\" ]] && echo yes || echo no)"
        echo "FILE_EXISTS=$([[ -e "{lock_file}" ]] && echo yes || echo no)"
    """)
    result = run_bash(tmp_path, script)
    assert "RC=2" in result.stdout, (
        f"Expected refusal (rc=2) with '{missing_name}' blank.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "FD_EMPTY=yes" in result.stdout, "No fd should be left open on refusal."
    assert missing_name in result.stderr, (
        f"Expected the refusal message to name the missing field '{missing_name}'.\n"
        f"stderr: {result.stderr}"
    )
    assert "error:" in result.stderr and "message:" in result.stderr and "remedy:" in result.stderr, (
        f"Expected the dual-channel error contract shape.\nstderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# (4) Stale provenance from a dead pid does not block reacquisition.
# ---------------------------------------------------------------------------


def test_stale_provenance_from_dead_pid_does_not_block_acquisition(
    tmp_path: pathlib.Path,
) -> None:
    lock_file = tmp_path / "locks" / "stale.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    # A pid this high is virtually guaranteed not to exist, and the lock was
    # never actually flocked by anyone -- this simulates a crash that left
    # provenance behind without ever holding the OS-level lock (the OS
    # releases flocks on process exit regardless of file content).
    lock_file.write_text("holder=ghost\npid=99999999\nop=old-op\ntask=OLD-TASK\n", encoding="utf-8")

    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}
        fd=""
        pgai_lock_acquire "{lock_file}" fresh "$$" new-op NEW-TASK fd
        echo "RC=$?"
        cat "{lock_file}"
        pgai_lock_release "{lock_file}" "$fd"
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "RC=0" in result.stdout, "Stale provenance must not block acquisition."
    assert "holder=fresh" in result.stdout
    assert "op=new-op" in result.stdout
    assert "task=NEW-TASK" in result.stdout
    assert "99999999" in result.stderr, (
        f"Expected a diagnostic naming the stale/dead pid.\nstderr: {result.stderr}"
    )
    assert "no longer running" in result.stderr


# ---------------------------------------------------------------------------
# (4b) A dead recorded pid whose flock an inherited descriptor still holds
#      is deferred to, never removed.
# ---------------------------------------------------------------------------


def _wait_for_file(path: pathlib.Path, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert path.exists(), f"expected {path} to appear within {timeout}s"


def _wait_until_gone(pid: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    pytest.fail(f"pid {pid} did not exit within {timeout}s")


def test_flock_held_by_an_orphan_under_a_dead_recorded_pid_is_deferred_to(
    tmp_path: pathlib.Path,
) -> None:
    lock_file = tmp_path / "locks" / "repo-wake-demo.lock"
    holder_pid_file = tmp_path / "holder.pid"
    child_pid_file = tmp_path / "child.pid"
    lib = _repo_root() / _LOCK_PROVENANCE_SH
    # The holder takes the lock through the helper (recording its own pid),
    # starts a child that inherits the lock descriptor, and is then killed
    # with SIGKILL: the record names a dead pid while the child keeps the
    # flock -- what a provider left running after `kill -KILL <wake pid>`
    # looks like. The wrapper shell reaps the holder so it does not linger
    # as a zombie that still answers kill -0.
    holder_script = tmp_path / "holder.sh"
    holder_script.write_text(
        textwrap.dedent(f"""\
            source {lib}
            fd=""
            pgai_lock_acquire "{lock_file}" wake-coder "$$" wake-chain:demo TASK-1 fd || exit 3
            sleep 600 &
            echo "$!" > "{child_pid_file}"
            echo "$$" > "{holder_pid_file}"
            while :; do wait; done
        """),
        encoding="utf-8",
    )
    wrapper = subprocess.Popen(
        ["bash", "-c", 'bash "$0"; sleep 600', str(holder_script)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        _wait_for_file(holder_pid_file)
        _wait_for_file(child_pid_file)
        holder_pid = int(holder_pid_file.read_text(encoding="utf-8").strip())
        child_pid = child_pid_file.read_text(encoding="utf-8").strip()
        os.kill(holder_pid, signal.SIGKILL)
        _wait_until_gone(holder_pid)

        before = lock_file.read_bytes()
        inode_before = lock_file.stat().st_ino
        assert f"pid={holder_pid}".encode() in before, "fixture precondition: the record names the killed holder"

        script = textwrap.dedent(f"""\
            set +e
            source {_LOCK_PROVENANCE_SH}
            fd=""
            pgai_lock_acquire "{lock_file}" fresh "$$" new-op NEW-TASK fd
            echo "RC=$?"
        """)
        result = run_bash(tmp_path, script)
        assert "RC=1" in result.stdout, (
            f"an orphan-held lock must return the contended exit.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert lock_file.exists() and lock_file.stat().st_ino == inode_before, "the lock file must not be removed"
        assert lock_file.read_bytes() == before, "the record must be left byte-for-byte in place"
        assert "lock.provenance.orphan_holder" in result.stderr, result.stderr
        assert child_pid in result.stderr, (
            f"the narration must name the child that really holds the flock ({child_pid}).\nstderr: {result.stderr}"
        )
    finally:
        try:
            os.killpg(wrapper.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        wrapper.wait(timeout=10)


# ---------------------------------------------------------------------------
# (5) Narration-rate memoization. A long chain used to
#     re-narrate the SAME dual-channel block on every contention probe
#     (once per retry tick, from every waiting agent) -- these fixtures
#     pin the memoization: at most one lock.provenance.contended narration per
#     lock file per process, no matter how many times contention is
#     probed, with the probe count still tracked for a caller's own
#     closing summary.
# ---------------------------------------------------------------------------


def test_contended_dual_channel_narration_fires_once_per_lock_per_process(
    tmp_path: pathlib.Path,
) -> None:
    """Ten contention probes against the SAME lock file, in the SAME
    process, narrate the lock.provenance.contended dual-channel block
    exactly once -- not ten times -- while lock behavior itself is
    unchanged: every probe still returns contended (rc=1), and
    pgai_lock_contention_probe_count still reports the full probe count."""
    lock_file = tmp_path / "locks" / "hot.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    import subprocess
    import time

    ready_file = tmp_path / "holder-ready"
    done_file = tmp_path / "holder-done"
    holder_script = textwrap.dedent(f"""\
        source {_LOCK_PROVENANCE_SH}
        fd=""
        pgai_lock_acquire "{lock_file}" writer "$$" "some-op" TASK-9 fd
        touch "{ready_file}"
        while [[ ! -f "{done_file}" ]]; do sleep 0.05; done
        pgai_lock_release "{lock_file}" "$fd"
    """)
    holder = subprocess.Popen(
        ["bash", "-c", holder_script],
        cwd=str(pathlib.Path(__file__).resolve().parents[2]),
    )
    try:
        deadline = time.time() + 5.0
        while not ready_file.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert ready_file.exists(), "holder subprocess did not signal readiness"

        probe_count = 10
        script = textwrap.dedent(f"""\
            set +e
            source {_LOCK_PROVENANCE_SH}
            for i in $(seq 1 {probe_count}); do
              fd=""
              pgai_lock_acquire "{lock_file}" reader 1234 "other-op" TASK-2 fd
              echo "RC=$?"
            done
            echo "PROBE_COUNT=$(pgai_lock_contention_probe_count "{lock_file}")"
        """)
        result = run_bash(tmp_path, script)
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

        rc_lines = [ln for ln in result.stdout.splitlines() if ln.startswith("RC=")]
        assert rc_lines == ["RC=1"] * probe_count, (
            f"every probe must still be contended (rc=1) -- lock behavior "
            f"itself must not change.\nstdout: {result.stdout}"
        )
        assert f"PROBE_COUNT={probe_count}" in result.stdout, (
            f"expected the probe counter to track all {probe_count} probes "
            f"even though only the first narrated.\nstdout: {result.stdout}"
        )

        narration_count = result.stderr.count("error:   lock.provenance.contended")
        assert narration_count == 1, (
            f"expected exactly one dual-channel narration for {probe_count} "
            f"probes against the same lock in the same process, got "
            f"{narration_count}.\nstderr: {result.stderr}"
        )
    finally:
        done_file.touch()
        holder.wait(timeout=5)


# ---------------------------------------------------------------------------
# (6) Grep-zero: the only flock call left in the wake/overwatch libraries is
#     the shared helper's own.
# ---------------------------------------------------------------------------


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "relative_path",
    [_WAKE_DRIVER_SH, _OVERWATCH_LIB_SH, _OVERWATCH_PROTOCOL_SH],
)
def test_no_direct_flock_calls_outside_the_shared_helper(relative_path: str) -> None:
    """None of the three acquisition sites calls `flock` directly anymore.

    Each must instead call pgai_lock_acquire (lock_provenance.sh), the only
    file in this fleet that still invokes `flock` itself.
    """
    target = _repo_root() / relative_path
    assert target.is_file(), f"Expected file at {target}"

    text = target.read_text(encoding="utf-8")
    direct_flock_calls = [
        line
        for line in text.splitlines()
        if re.search(r"(?<![\w-])flock\s+-n\b", line) and not line.strip().startswith("#")
    ]
    assert not direct_flock_calls, (
        f"{relative_path} still calls flock directly outside comments -- "
        f"acquisition must go through pgai_lock_acquire "
        f"(team/scripts/lib/lock_provenance.sh) instead:\n"
        + "\n".join(direct_flock_calls)
    )
    assert "pgai_lock_acquire" in text, (
        f"{relative_path} does not call pgai_lock_acquire -- expected it to "
        f"acquire its lock through the shared helper."
    )


def test_lock_provenance_sh_is_the_only_remaining_flock_caller() -> None:
    """Positive check: lock_provenance.sh itself still calls flock (the
    acquisition attempt) -- confirming the grep-zero above isn't passing
    because nobody calls flock anywhere anymore. The count is a floor, not
    an exact shape: acquisition flocks the staged record's inode and then
    contends for the inode at the lock path, so more than one executable
    call site is the design, and only zero would be the failure."""
    target = _repo_root() / _LOCK_PROVENANCE_SH
    text = target.read_text(encoding="utf-8")
    direct_flock_calls = [
        line
        for line in text.splitlines()
        if re.search(r"(?<![\w-])flock\s+-n\b", line) and not line.strip().startswith("#")
    ]
    assert len(direct_flock_calls) >= 1, (
        f"Expected at least one executable flock call in {_LOCK_PROVENANCE_SH}, "
        f"found {len(direct_flock_calls)}: {direct_flock_calls!r}"
    )
