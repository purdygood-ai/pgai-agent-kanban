"""
test_lock_live_holder_never_reaped_by_age.py
=============================================
Behavioral fixtures for the rule that a lock whose recorded holder pid is
alive is never deleted on age, that age is measured against the holder's
real budget, and that a running task keeps its held lock looking fresh.

The mechanism under test, across three files:

  team/scripts/lib/lock_provenance.sh -- pgai_lock_acquire's contention
    classifier reaps only a provenance-less lock (a dead holder's lock
    is free in the kernel and simply taken over). A live
    holder whose lock file has aged past the stale-lock threshold is
    narrated as overdue (lock.provenance.holder_overdue) and deferred to,
    never reaped. The threshold's floor is max_runtime_seconds +
    max_task_seconds + kill_grace_seconds + margin: run_project_chain
    checks its caps only between tasks, so the last task dispatched under
    max_runtime may run its whole per-task budget past it.
    pgai_lock_refresh_held_mtime is the flock-free, ownership-checked
    touch the wake chain uses to keep a held lock fresh.

  team/scripts/lib/wake_driver_invoke.sh -- the per-task watchdog poll
    loop calls _wd_refresh_project_lock_mtime every
    _PGAI_LOCK_HELD_REFRESH_INTERVAL_SECONDS while the provider runs.

  team/scripts/release-locks.sh -- `--release NAME --force` against a
    live holder sends it SIGTERM and waits for its own release path,
    instead of swapping its file for a fresh inode (which seats a second
    chain beside a still-running one).

Every scenario uses synthetic lock paths under tmp_path or a synthetic
kanban root; nothing here touches the live install's locks/ directory.
The watchdog scenario runs the REAL watchdog block (extracted between its
WATCHDOG-INVOKE-BEGIN/END markers) and the REAL refresh wrapper against a
stub provider, on a real clock with a short refresh interval. Every
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
import time

import pytest

from tests.unit.shell_harness import run_bash

# ---------------------------------------------------------------------------
# Paths (relative to this file: team/tests/unit/ -> team/ -> repo root)
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]  # team/
_LIB_DIR = _TEAM_DIR / "scripts" / "lib"
_LOCK_PROVENANCE_SH = _LIB_DIR / "lock_provenance.sh"
_DRIVER_INVOKE = _LIB_DIR / "wake_driver_invoke.sh"
_TEMP_SH = _LIB_DIR / "temp.sh"
# Defines _wd_epoch_now, the wall clock the deadline watchdog reads; the
# watchdog scenario runs on the real clock, so the shipped definition is used.
_TASK_BUDGET_SH = _LIB_DIR / "wake_task_budget.sh"
_RELEASE_LOCKS_SH = _TEAM_DIR / "scripts" / "release-locks.sh"

_WATCHDOG_BEGIN = "# WATCHDOG-INVOKE-BEGIN"
_WATCHDOG_END = "# WATCHDOG-INVOKE-END"

# The filed scenario's configuration: under the old floor
# (max_runtime + kill_grace + 120s margin = 270s) a lock aged 400s was
# reaped even though its holder answered kill -0; the chain could
# legitimately hold it for max_runtime + max_task = 720s. The new floor
# is 120 + 600 + 30 + 120 = 870s.
_BUG_ENV = {
    "KANBAN_ROOT": "",
    "MAX_RUNTIME_SECONDS": "120",
    "MAX_TASK_SECONDS": "600",
    "KILL_GRACE_SECONDS": "30",
}
_OLD_LIMIT_SECONDS = 120 + 30 + 120
_NEW_FLOOR_SECONDS = 120 + 600 + 30 + 120


def _wait_for(path: pathlib.Path, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while not path.exists() and time.time() < deadline:
        time.sleep(0.05)
    assert path.exists(), f"expected {path} to appear within {timeout}s"


def _end(proc: subprocess.Popen, done_file: pathlib.Path | None = None) -> None:
    """Own the holder: ask it to finish, then make sure it did."""
    if done_file is not None:
        done_file.touch()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _live_holder(
    tmp_path: pathlib.Path, lock_file: pathlib.Path, backdate_seconds: int, env: dict[str, str]
) -> tuple[subprocess.Popen, pathlib.Path]:
    """A real pgai_lock_acquire holder (its own live pid in the record)
    whose lock file is then backdated by *backdate_seconds*."""
    ready_file = tmp_path / "holder-ready"
    done_file = tmp_path / "holder-done"
    holder_script = textwrap.dedent(f"""\
        source {_LOCK_PROVENANCE_SH}
        fd=""
        pgai_lock_acquire "{lock_file}" writer "$$" real-op TASK-LIVE fd
        touch -d "-{backdate_seconds} seconds" "{lock_file}"
        touch "{ready_file}"
        while [[ ! -f "{done_file}" ]]; do sleep 0.05; done
        pgai_lock_release "{lock_file}" "$fd"
    """)
    holder = subprocess.Popen(
        ["bash", "-c", holder_script], cwd=str(_TEAM_DIR), env={**os.environ, **env}
    )
    _wait_for(ready_file)
    return holder, done_file


def _contend(tmp_path: pathlib.Path, lock_file: pathlib.Path, env: dict[str, str]):
    script = textwrap.dedent(f"""\
        set +e
        source {_LOCK_PROVENANCE_SH}
        fd2=""
        pgai_lock_acquire "{lock_file}" reader "$$" other-op TASK-2 fd2
        echo "RC=$?"
        if [[ "$fd2" != "" ]]; then pgai_lock_release "{lock_file}" "$fd2"; fi
    """)
    return run_bash(tmp_path, script, extra_env=env)


# ---------------------------------------------------------------------------
# (1) The filed scenario: a live, in-budget holder older than the old limit
#     is deferred to with contention naming its four fields -- no reap.
# ---------------------------------------------------------------------------


def test_live_holder_past_old_limit_but_within_real_budget_is_deferred_not_reaped(
    tmp_path: pathlib.Path,
) -> None:
    lock_file = tmp_path / "locks" / "repo-wake-demo.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    age = _OLD_LIMIT_SECONDS + 130  # 400s: past the old limit, under the new floor
    assert _OLD_LIMIT_SECONDS < age < _NEW_FLOOR_SECONDS
    holder, done_file = _live_holder(tmp_path, lock_file, age, _BUG_ENV)
    try:
        inode_before = lock_file.stat().st_ino
        record_before = lock_file.read_text()
        result = _contend(tmp_path, lock_file, _BUG_ENV)
        assert "RC=1" in result.stdout, (
            f"a live holder within its real budget must be deferred.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "stale_reaped" not in result.stderr, (
            f"no reap of any kind against a live holder.\nstderr: {result.stderr}"
        )
        assert "holder_overdue" not in result.stderr, (
            f"a holder inside its budget is not overdue.\nstderr: {result.stderr}"
        )
        assert "lock.provenance.contended" in result.stderr
        assert f"holder=writer pid={holder.pid} op=real-op task=TASK-LIVE" in result.stderr, (
            f"the contention must name the holder's four provenance fields.\n"
            f"stderr: {result.stderr}"
        )
        assert lock_file.stat().st_ino == inode_before, "the holder's file was replaced"
        assert lock_file.read_text() == record_before, "the holder's record was rewritten"
    finally:
        _end(holder, done_file)


def test_live_holder_past_real_budget_is_narrated_overdue_and_still_deferred(
    tmp_path: pathlib.Path,
) -> None:
    lock_file = tmp_path / "locks" / "repo-wake-demo.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    holder, done_file = _live_holder(tmp_path, lock_file, _NEW_FLOOR_SECONDS + 600, _BUG_ENV)
    try:
        inode_before = lock_file.stat().st_ino
        result = _contend(tmp_path, lock_file, _BUG_ENV)
        assert "RC=1" in result.stdout, (
            f"a live holder past its budget is still never reaped.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "stale_reaped" not in result.stderr, result.stderr
        assert "lock.provenance.holder_overdue" in result.stderr, result.stderr
        assert f"holder=writer pid={holder.pid} op=real-op task=TASK-LIVE" in result.stderr
        assert "kill -0" in result.stderr and "derivation:" in result.stderr, result.stderr
        assert "max_task_seconds 600s" in result.stderr, (
            f"the derivation must show max_task_seconds as an addend.\nstderr: {result.stderr}"
        )
        assert f"--release repo-wake-demo --force" in result.stderr, (
            f"the overdue remedy must name release-locks --force.\nstderr: {result.stderr}"
        )
        assert "lock.provenance.contended" in result.stderr
        assert lock_file.stat().st_ino == inode_before, "the overdue holder's file was replaced"
        assert f"pid={holder.pid}" in lock_file.read_text()
    finally:
        _end(holder, done_file)


def test_overdue_narration_fires_once_per_process_while_contention_keeps_counting(
    tmp_path: pathlib.Path,
) -> None:
    lock_file = tmp_path / "locks" / "repo-wake-demo.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    holder, done_file = _live_holder(tmp_path, lock_file, _NEW_FLOOR_SECONDS + 600, _BUG_ENV)
    try:
        script = textwrap.dedent(f"""\
            set +e
            source {_LOCK_PROVENANCE_SH}
            for i in 1 2 3; do
              fd2=""
              pgai_lock_acquire "{lock_file}" reader "$$" other-op TASK-2 fd2
              echo "RC=$?"
            done
            echo "PROBES=$(pgai_lock_contention_probe_count "{lock_file}")"
        """)
        result = run_bash(tmp_path, script, extra_env=_BUG_ENV)
        assert result.stdout.count("RC=1") == 3, result.stdout
        assert "PROBES=3" in result.stdout, result.stdout
        assert result.stderr.count("error:   lock.provenance.holder_overdue") == 1, result.stderr
    finally:
        _end(holder, done_file)


# ---------------------------------------------------------------------------
# (2) Regression guard: a dead-pid lock is still taken over and reacquired.
# ---------------------------------------------------------------------------


def test_dead_pid_lock_is_still_taken_over_and_reacquired_under_the_same_budget(
    tmp_path: pathlib.Path,
) -> None:
    lock_file = tmp_path / "locks" / "repo-wake-demo.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    # A holder that died and left its record: the kernel dropped its flock
    # when it died, so nothing holds the file -- only the record remains.
    lock_file.write_text("holder=ghost\npid=99999999\nop=old-op\ntask=OLD-TASK\n", encoding="utf-8")
    result = _contend(tmp_path, lock_file, _BUG_ENV)
    assert "RC=0" in result.stdout, (
        f"a dead-pid holder's lock must still be taken over and reacquired.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "99999999" in result.stderr, "the takeover narration names the dead pid"
    assert "holder_overdue" not in result.stderr
    assert "orphan_holder" not in result.stderr


# ---------------------------------------------------------------------------
# (3) The refresh helper and the watchdog loop that calls it.
# ---------------------------------------------------------------------------


def test_refresh_touches_own_lock_without_releasing_the_flock(tmp_path: pathlib.Path) -> None:
    lock_file = tmp_path / "locks" / "own.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}
        fd=""
        pgai_lock_acquire "{lock_file}" coder "$$" wake-chain:demo none fd
        touch -d "-1000 seconds" "{lock_file}"
        echo "BEFORE=$(stat -c %Y "{lock_file}")"
        NOW=$(date +%s)
        pgai_lock_refresh_held_mtime "{lock_file}" "$fd"
        echo "RC=$?"
        echo "NOW=$NOW"
        echo "AFTER=$(stat -c %Y "{lock_file}")"
        # Still held: a second, independent fd on the same file must lose flock -n.
        exec 9>>"{lock_file}"
        if flock -n 9; then echo "FLOCK=free"; else echo "FLOCK=held"; fi
        cat "{lock_file}" | sed 's/^/RECORD:/'
        pgai_lock_release "{lock_file}" "$fd"
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    values = dict(re.findall(r"^(\w+)=(.*)$", result.stdout, re.M))
    assert values["RC"] == "0"
    assert int(values["AFTER"]) >= int(values["NOW"]) > int(values["BEFORE"]), values
    assert values["FLOCK"] == "held", f"the refresh must not release the flock: {result.stdout}"
    assert "RECORD:holder=coder" in result.stdout, "the refresh must not rewrite provenance"


def test_refresh_leaves_a_path_taken_over_by_another_holder_untouched(
    tmp_path: pathlib.Path,
) -> None:
    lock_file = tmp_path / "locks" / "taken.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}
        fd=""
        pgai_lock_acquire "{lock_file}" chain-a "$$" wake-chain:demo none fd
        # Simulate a reap-and-reacquire by someone else: a NEW file at the path.
        rm -f "{lock_file}"
        printf 'holder=chain-b\\npid=424242\\nop=wake-chain:demo\\ntask=none\\n' > "{lock_file}"
        touch -d "-1000 seconds" "{lock_file}"
        echo "BEFORE=$(stat -c %Y "{lock_file}")"
        pgai_lock_refresh_held_mtime "{lock_file}" "$fd"
        echo "RC=$?"
        echo "AFTER=$(stat -c %Y "{lock_file}")"
        pgai_lock_abandon "{lock_file}" "$fd"
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    values = dict(re.findall(r"^(\w+)=(.*)$", result.stdout, re.M))
    assert values["RC"] == "0"
    assert values["AFTER"] == values["BEFORE"], (
        f"a chain that lost the lock must never freshen the new holder's file: {result.stdout}"
    )


def test_refresh_is_a_silent_noop_without_a_held_lock(tmp_path: pathlib.Path) -> None:
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_LOCK_PROVENANCE_SH}
        pgai_lock_refresh_held_mtime "" ""
        echo "RC1=$?"
        pgai_lock_refresh_held_mtime "{tmp_path}/never-acquired.lock" 7
        echo "RC2=$?"
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "RC1=0" in result.stdout and "RC2=0" in result.stdout
    assert result.stderr == "", f"the no-op must be silent: {result.stderr}"
    assert not (tmp_path / "never-acquired.lock").exists()


def _extract_watchdog_block() -> str:
    text = _DRIVER_INVOKE.read_text(encoding="utf-8")
    begin = text.index(_WATCHDOG_BEGIN)
    end = text.index(_WATCHDOG_END) + len(_WATCHDOG_END)
    return text[begin:end]


def _extract_function(name: str, source: pathlib.Path) -> str:
    text = source.read_text(encoding="utf-8")
    match = re.search(rf"^{re.escape(name)}\(\)\s*\{{\n.*?^\}}", text, re.S | re.M)
    assert match, f"function {name}() not found in {source}"
    return match.group(0)


def test_watchdog_loop_refreshes_the_held_lock_mtime_while_the_provider_runs(
    tmp_path: pathlib.Path,
) -> None:
    """A holder that acquired the project lock, then let a stub provider
    run under the real watchdog block with a 2s refresh interval, ends the
    task with a lock file whose mtime is newer than at acquisition (it was
    backdated by 1000s first), still holds the flock, and still carries
    its own provenance record."""
    lock_file = tmp_path / "locks" / "repo-wake-demo.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    log_file = tmp_path / "wake.log"
    log_file.write_text("", encoding="utf-8")
    temp_root = tmp_path / "kanban_tmp"
    temp_root.mkdir()
    pgid_file = tmp_path / "pgid"

    harness = f"""
set -uo pipefail
export PGAI_AGENT_KANBAN_TEMP_DIR={str(temp_root)!r}
source {str(_TEMP_SH)!r}
source {str(_TASK_BUDGET_SH)!r}
source {str(_LOCK_PROVENANCE_SH)!r}
log() {{ echo "[$(date -Iseconds)] wake(${{AGENT}}): $*" | tee -a "$LOG_FILE"; }}

LOG_FILE={str(log_file)!r}
AGENT="coder"
task_id="TASK-LOCK-REFRESH"
MAX_TASK_SECONDS=30
KILL_GRACE_SECONDS=2
_PGAI_LOCK_HELD_REFRESH_INTERVAL_SECONDS=2

# What run_project_chain does: acquire the project lock on one fd held
# for the whole chain. Both names are its locals, reached by the refresh
# wrapper through dynamic scope.
_project_lock_file={str(lock_file)!r}
_project_lock_fd=""
pgai_lock_acquire "$_project_lock_file" coder "$$" wake-chain:demo none _project_lock_fd || exit 97
touch -d "-1000 seconds" "$_project_lock_file"
echo "MTIME_BEFORE=$(stat -c %Y "$_project_lock_file")"
echo "INODE_BEFORE=$(stat -c %i "$_project_lock_file")"
START_EPOCH=$(date +%s)

# Stub provider: records the wrapper's pgid (so the test can kill the
# group on any failure) and runs long enough for three refresh ticks.
_wd_run_provider_with_telemetry_safeguard_retry() {{
  echo "$BASHPID" > {str(pgid_file)!r}
  command sleep 7 </dev/null >/dev/null 2>&1
  PROVIDER_AGENT_EXIT_CODE=0
}}

{_extract_function("_wd_refresh_project_lock_mtime", _DRIVER_INVOKE)}

_run_watchdog() {{
{_extract_watchdog_block()}
}}
_run_watchdog

echo "START_EPOCH=$START_EPOCH"
echo "MTIME_AFTER=$(stat -c %Y "$_project_lock_file")"
echo "INODE_AFTER=$(stat -c %i "$_project_lock_file")"
echo "FINAL_EXIT=${{PROVIDER_AGENT_EXIT_CODE}}"
echo "TASK_KILLED=${{_task_killed}}"
# A sibling process contending now must still be refused: the refresh
# never let go of the flock.
bash -c 'set +e; source {str(_LOCK_PROVENANCE_SH)!r}; fd2=""; pgai_lock_acquire "$1" sibling "$$" wake-chain:demo none fd2 2>/dev/null; echo "CONTENDER_RC=$?"' _ "$_project_lock_file"
sed 's/^/RECORD:/' "$_project_lock_file"
pgai_lock_release "$_project_lock_file" "$_project_lock_fd"
"""
    proc = subprocess.Popen(
        ["bash", "-c", harness],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, cwd=str(tmp_path), env=dict(os.environ), start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=MAX_HARNESS_SECONDS)
    finally:
        _kill_recorded_group(pgid_file)
        if proc.poll() is None:
            os.killpg(proc.pid, 9)
            proc.communicate()
    assert proc.returncode == 0, f"harness failed rc={proc.returncode}\nstdout:\n{stdout}\nstderr:\n{stderr}"
    values = dict(re.findall(r"^(\w+)=(.*)$", stdout, re.M))
    assert values["TASK_KILLED"] == "false" and values["FINAL_EXIT"] == "0", stdout
    assert int(values["MTIME_AFTER"]) > int(values["MTIME_BEFORE"]), (
        f"the lock mtime must be refreshed while the provider runs.\n{stdout}\n{stderr}"
    )
    assert int(values["MTIME_AFTER"]) >= int(values["START_EPOCH"]), (
        f"the refreshed mtime must be at or after the task started.\n{stdout}"
    )
    assert values["INODE_AFTER"] == values["INODE_BEFORE"], "the refresh replaced the file"
    assert values["CONTENDER_RC"] == "1", f"the flock must still be held after refreshes.\n{stdout}"
    assert "RECORD:holder=coder" in stdout, "provenance must survive the refresh"
    log_text = log_file.read_text(encoding="utf-8")
    assert "held lock mtime refreshed every 2s" in log_text, log_text


MAX_HARNESS_SECONDS = 60


def _kill_recorded_group(pgid_file: pathlib.Path) -> None:
    if not pgid_file.is_file():
        return
    text = pgid_file.read_text(encoding="utf-8").strip()
    if text.isdigit():
        try:
            os.killpg(int(text), 9)
        except ProcessLookupError:
            pass


# ---------------------------------------------------------------------------
# (4) release-locks.sh --force signals the live holder, never unlinks.
# ---------------------------------------------------------------------------


def _sandbox_root(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "kanban"
    (root / "locks").mkdir(parents=True)
    return root


def _release_locks_env(root: pathlib.Path, grace: int) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PGAI_PROJECT_NAME", None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
    env["KANBAN_ROOT"] = str(root)
    env["KILL_GRACE_SECONDS"] = str(grace)
    return env


def _start_holder(tmp_path: pathlib.Path, lock_file: pathlib.Path, *, honours_sigterm: bool):
    """A real pgai_lock_acquire holder. The cooperative one exits on
    SIGTERM (its EXIT trap releases the lock); the hostile one only records
    the signal and keeps holding."""
    ready_file = tmp_path / "holder-ready"
    term_marker = tmp_path / "holder-got-term"
    on_term = "exit 0" if honours_sigterm else ":"
    holder_script = textwrap.dedent(f"""\
        source {_LOCK_PROVENANCE_SH}
        trap 'touch "{term_marker}"; {on_term}' TERM
        fd=""
        pgai_lock_acquire "{lock_file}" coder "$$" wake-chain:demo none fd || exit 97
        touch "{ready_file}"
        while true; do sleep 0.1; done
    """)
    holder = subprocess.Popen(["bash", "-c", holder_script], cwd=str(_TEAM_DIR))
    _wait_for(ready_file)
    return holder, term_marker


def _run_release_locks(root: pathlib.Path, *args: str, grace: int = 3) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(_RELEASE_LOCKS_SH), *args], capture_output=True, text=True, timeout=60,
        cwd=str(_TEAM_DIR), env=_release_locks_env(root, grace), stdin=subprocess.DEVNULL,
    )


def test_force_signals_a_cooperative_live_holder_which_releases_its_own_lock(
    tmp_path: pathlib.Path,
) -> None:
    root = _sandbox_root(tmp_path)
    lock_file = root / "locks" / "repo-wake-demo.lock"
    holder, term_marker = _start_holder(tmp_path, lock_file, honours_sigterm=True)
    try:
        result = _run_release_locks(root, "--release", "repo-wake-demo", "--force", grace=5)
        assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        assert f"sent SIGTERM to live holder pid {holder.pid}" in result.stdout, result.stdout
        assert "let go of it" in result.stdout or "exited" in result.stdout, result.stdout
        assert term_marker.exists(), "the holder never received SIGTERM"
        holder.wait(timeout=5)
        assert not lock_file.exists(), (
            "the holder's own release path removes the file; the script must not have to"
        )
        assert "holder_not_released" not in result.stderr
    finally:
        _end(holder)


def test_force_never_unlinks_a_live_holder_that_ignores_sigterm(tmp_path: pathlib.Path) -> None:
    root = _sandbox_root(tmp_path)
    lock_file = root / "locks" / "repo-wake-demo.lock"
    holder, term_marker = _start_holder(tmp_path, lock_file, honours_sigterm=False)
    try:
        inode_before = lock_file.stat().st_ino
        record_before = lock_file.read_text()
        result = _run_release_locks(root, "--release", "repo-wake-demo", "--force", grace=2)
        assert result.returncode == 5, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        assert "error:   release_locks.holder_not_released" in result.stderr, result.stderr
        assert f"kill -KILL {holder.pid}" in result.stderr, (
            f"the remedy must name the escalation with the real pid.\nstderr: {result.stderr}"
        )
        assert term_marker.exists(), "the holder never received SIGTERM"
        assert holder.poll() is None, "the script must not kill the holder itself"
        assert lock_file.exists(), "the live holder's file was unlinked"
        assert lock_file.stat().st_ino == inode_before, "the live holder's file was swapped"
        assert lock_file.read_text() == record_before, "the live holder's record was rewritten"
        assert f"pid={holder.pid}" in record_before
    finally:
        _end(holder)


def test_release_without_force_still_refuses_a_live_holder(tmp_path: pathlib.Path) -> None:
    root = _sandbox_root(tmp_path)
    lock_file = root / "locks" / "repo-wake-demo.lock"
    holder, term_marker = _start_holder(tmp_path, lock_file, honours_sigterm=False)
    try:
        result = _run_release_locks(root, "--release", "repo-wake-demo")
        assert result.returncode == 4, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        assert "release_locks.holder_alive" in result.stderr
        assert not term_marker.exists(), "without --force nothing may be signalled"
        assert lock_file.exists() and f"pid={holder.pid}" in lock_file.read_text()
    finally:
        _end(holder)


# ---------------------------------------------------------------------------
# (5) Structural guarantees on the shipped files.
# ---------------------------------------------------------------------------


def test_watchdog_block_calls_the_refresh_wrapper_inside_its_poll_loop() -> None:
    block = _extract_watchdog_block()
    loop_start = block.index('while kill -0 "$_provider_pgid" 2>/dev/null; do')
    refresh_at = block.index("_wd_refresh_project_lock_mtime", loop_start)
    loop_end = block.index("done", refresh_at)
    assert loop_start < refresh_at < loop_end, (
        "the refresh must run inside the per-task poll loop, not after it"
    )
    assert "pgai_lock_refresh_held_mtime" in _extract_function(
        "_wd_refresh_project_lock_mtime", _DRIVER_INVOKE
    )


def test_no_age_alone_reap_key_survives_in_the_lock_library() -> None:
    text = _LOCK_PROVENANCE_SH.read_text(encoding="utf-8")
    assert "stale_reaped.aged_out" not in text, (
        "a live holder is never reaped on age; the aged-out reap must not come back"
    )
    assert "flock" not in _extract_function("pgai_lock_refresh_held_mtime", _LOCK_PROVENANCE_SH), (
        "the refresh must never take or release the flock"
    )


@pytest.mark.parametrize("script", [_LOCK_PROVENANCE_SH, _DRIVER_INVOKE, _RELEASE_LOCKS_SH])
def test_shipped_shell_parses(script: pathlib.Path) -> None:
    result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"bash -n {script} failed:\n{result.stderr}"
