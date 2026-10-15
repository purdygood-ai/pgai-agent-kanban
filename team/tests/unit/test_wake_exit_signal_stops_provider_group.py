"""
test_wake_exit_signal_stops_provider_group.py
==============================================
A wake stopped by SIGTERM or SIGHUP stops its provider's process group
before it releases any lock.

The wake runs the provider CLI in a process group of its own, under a wrapper
subshell that traps TERM to a no-op (team/scripts/lib/wake_driver_invoke.sh's
watchdog block), and exposes that group's id as
CLEANUP_ON_EXIT_PROCESS_GROUP. The exit trap wake_bootstrap.sh installs
(script_common.sh's cleanup_on_exit, on EXIT/SIGTERM/SIGINT/SIGHUP) stops
that group -- SIGTERM, KILL_GRACE_SECONDS, SIGKILL -- and only then runs the
pre-hook through which lock_provenance.sh releases every lock the wake holds.

Each scenario runs a stub wake: the REAL trap lines extracted from
wake_bootstrap.sh, the REAL script_common.sh and lock_provenance.sh, a lock
taken with the real pgai_lock_acquire, and the REAL watchdog block extracted
verbatim from wake_driver_invoke.sh, around a stub provider that only sleeps
-- one foreground sleeper and one double-forked grandchild, both inheriting
the lock descriptor, as a provider CLI does. Once both sleepers are up the
test signals the stub wake and asserts:
  * the wake exits;
  * no process of the provider group survives;
  * at the moment the lock-release hook ran, the provider group had no
    member left and the lock file was still in place -- the release came
    after the group was empty, never before;
  * the lock file is gone afterwards.

Scenarios: SIGTERM and SIGHUP with a provider that dies on SIGTERM, and
SIGTERM with a provider that ignores it (the SIGKILL-after-grace path).

Every process a scenario starts is owned by the test: the stub wake runs in
its own session and the provider group's id is written to a file the moment
it is known; teardown SIGKILLs both groups whether or not the assertions
passed (docs/test-authoring-guidelines.md "Own every process your
subprocess starts"). All files live under pytest's tmp_path.
"""

from __future__ import annotations

import os
import pathlib
import re
import signal
import subprocess
import time

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent  # team/
_LIB_DIR = _TEAM_DIR / "scripts" / "lib"
_BOOTSTRAP = _LIB_DIR / "wake_bootstrap.sh"
_DRIVER_INVOKE = _LIB_DIR / "wake_driver_invoke.sh"

_WATCHDOG_BEGIN = "# WATCHDOG-INVOKE-BEGIN"
_WATCHDOG_END = "# WATCHDOG-INVOKE-END"

# A per-task limit far beyond the scenario: the stop must come from the
# signal, never from the watchdog's own deadline.
_MAX_TASK_SECONDS = 600
_KILL_GRACE_SECONDS = 3
_STARTUP_TIMEOUT = 30
_EXIT_TIMEOUT = 4 * _KILL_GRACE_SECONDS + 30


def _bootstrap_trap_lines() -> str:
    """The exit-trap installation lines exactly as wake_bootstrap.sh writes them."""
    lines = re.findall(r"^trap cleanup_on_exit .*$", _BOOTSTRAP.read_text(encoding="utf-8"), re.M)
    assert lines, f"no 'trap cleanup_on_exit' line found in {_BOOTSTRAP}"
    return "\n".join(lines)


def _watchdog_block() -> str:
    text = _DRIVER_INVOKE.read_text(encoding="utf-8")
    begin = text.index(_WATCHDOG_BEGIN)
    end = text.index(_WATCHDOG_END) + len(_WATCHDOG_END)
    return text[begin:end]


def _stub_provider(marker: str, *, ignore_sigterm: bool) -> str:
    """A provider stand-in that only sleeps; never a real provider call."""
    trap_line = "trap '' TERM   # an ignored disposition survives exec: both sleepers ignore it" if ignore_sigterm else ""
    return f"""#!/usr/bin/env bash
{trap_line}
( ( exec -a {marker}-grandchild sleep 600 ) & )
exec -a {marker}-foreground sleep 600
"""


def _harness(work: pathlib.Path, marker: str, *, ignore_sigterm: bool) -> str:
    stub = work / "stub-provider.sh"
    stub.write_text(_stub_provider(marker, ignore_sigterm=ignore_sigterm), encoding="utf-8")
    temp_root = work / "kanban_tmp"
    temp_root.mkdir()
    return f"""
set -uo pipefail
export PGAI_AGENT_KANBAN_TEMP_DIR={str(temp_root)!r}
source {str(_LIB_DIR / 'temp.sh')!r}
source {str(_LIB_DIR / 'wake_task_budget.sh')!r}
source {str(_LIB_DIR / 'script_common.sh')!r}
{_bootstrap_trap_lines()}

LOG_FILE={str(work / 'wake.log')!r}
AGENT="coder"
task_id="TASK-EXIT-SIGNAL-{marker}"
MAX_TASK_SECONDS={_MAX_TASK_SECONDS}
KILL_GRACE_SECONDS={_KILL_GRACE_SECONDS}
log() {{ echo "[$(date -Iseconds)] wake(${{AGENT}}): $*" >> "$LOG_FILE"; }}

# Registered BEFORE lock_provenance.sh is sourced, so that file chains it
# ahead of its own release (its "prior hook"): it records, at the moment the
# lock release begins, how many members the provider group still has and
# whether the lock file is still in place.
_record_release_moment() {{
  local pg members lock_present=no
  pg="$(cat {str(work / 'pgid')!r} 2>/dev/null)"
  members="$(ps -eo pgid=,stat= | awk -v g="$pg" '$1 == g && $2 !~ /^Z/' | wc -l)"
  [[ -e {str(work / 'repo.lock')!r} ]] && lock_present=yes
  echo "members=${{members}} lock_present=${{lock_present}}" >> {str(work / 'release_moment')!r}
}}
CLEANUP_ON_EXIT_PRE_HOOK=_record_release_moment
source {str(_LIB_DIR / 'lock_provenance.sh')!r}
pgai_lock_acquire {str(work / 'repo.lock')!r} wake-coder "$$" wake none REPO_LOCK_FD || exit 7

_wd_refresh_project_lock_mtime() {{ :; }}
_wd_run_provider_with_telemetry_safeguard_retry() {{
  echo "$BASHPID" > {str(work / 'pgid')!r}
  bash {str(stub)!r} </dev/null >/dev/null 2>&1
  PROVIDER_AGENT_EXIT_CODE=$?
}}

_run_watchdog() {{
{_watchdog_block()}
}}
_run_watchdog
echo "watchdog returned without a signal" >> "$LOG_FILE"
"""


def _group_members(pgid: int) -> list[str]:
    out = subprocess.run(["ps", "-eo", "pid=,pgid=,stat=,args="], capture_output=True, text=True, check=True).stdout
    return [
        line.strip()
        for line in out.splitlines()
        if len(line.split()) >= 3 and line.split()[1] == str(pgid) and not line.split()[2].startswith("Z")
    ]


def _marker_processes(marker: str) -> list[str]:
    out = subprocess.run(["pgrep", "-af", marker], capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if "pgrep" not in line]


def _killpg(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


@pytest.mark.parametrize(
    ("sig", "ignore_sigterm"),
    [(signal.SIGTERM, False), (signal.SIGHUP, False), (signal.SIGTERM, True)],
    ids=["sigterm-cooperative-provider", "sighup-cooperative-provider", "sigterm-provider-ignores-term"],
)
def test_exit_signal_stops_the_provider_group_before_the_lock_is_released(
    tmp_path: pathlib.Path, sig: signal.Signals, ignore_sigterm: bool
) -> None:
    marker = f"wake-exit-stub-{sig.name.lower()}-{'deaf' if ignore_sigterm else 'coop'}-{os.getpid()}"
    script = tmp_path / "stub-wake.sh"
    script.write_text(_harness(tmp_path, marker, ignore_sigterm=ignore_sigterm), encoding="utf-8")
    pgid_file = tmp_path / "pgid"

    wake = subprocess.Popen(
        ["bash", str(script)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    provider_pgid = 0
    try:
        deadline = time.monotonic() + _STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            if pgid_file.is_file() and pgid_file.read_text(encoding="utf-8").strip().isdigit():
                provider_pgid = int(pgid_file.read_text(encoding="utf-8").strip())
            if provider_pgid and len(_marker_processes(marker)) >= 2:
                break
            if wake.poll() is not None:
                break
            time.sleep(0.2)
        assert provider_pgid and len(_marker_processes(marker)) >= 2, (
            f"the stub provider never came up; wake rc={wake.poll()}\n{wake.stderr.read() if wake.poll() is not None else ''}"
        )
        assert (tmp_path / "repo.lock").is_file(), "fixture precondition: the stub wake holds its lock"

        os.kill(wake.pid, sig)
        stdout, stderr = wake.communicate(timeout=_EXIT_TIMEOUT)

        survivors = _group_members(provider_pgid) + _marker_processes(marker)
        assert not survivors, f"processes of the provider group survived {sig.name} on the wake: {survivors}\n{stderr}"

        moments = (tmp_path / "release_moment").read_text(encoding="utf-8").splitlines()
        assert moments, f"the lock-release hook never ran\n{stderr}"
        assert moments[0] == "members=0 lock_present=yes", (
            f"when the lock release began the provider group must already be empty and the lock still "
            f"held; recorded {moments[0]!r}\n{stderr}"
        )
        assert not (tmp_path / "repo.lock").exists(), f"the lock must be released on the way out\n{stderr}"
        assert f"stopping process group {provider_pgid}" in stderr, stderr
        if ignore_sigterm:
            assert f"sending SIGKILL to the group" in stderr, stderr
        log_text = (tmp_path / "wake.log").read_text(encoding="utf-8")
        assert "watchdog returned without a signal" not in log_text, log_text
    finally:
        _killpg(wake.pid)
        if provider_pgid:
            _killpg(provider_pgid)
        if wake.poll() is None:
            wake.communicate(timeout=10)
