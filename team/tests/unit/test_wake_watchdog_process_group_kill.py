"""
test_wake_watchdog_process_group_kill.py
=========================================
Behavioral fixtures for the per-task timeout watchdog's process-group kill
and the finalize-side drain that refuses to proceed while that group lives.

team/scripts/lib/wake_driver_invoke.sh launches the provider invocation as a
background subshell under bash job control (`set -m`), which gives the
subshell -- and with it the provider CLI and every tool it spawns -- a
process group of its own. At the deadline the watchdog sends SIGTERM to
that GROUP, polls the group (not the wrapper pid) through
KILL_GRACE_SECONDS, and sends SIGKILL to the group if anything survived.
team/scripts/lib/wake_driver_finalize.sh's wd_drain_provider_process_group
then holds finalization until the group is empty.

Tests run the REAL watchdog block (extracted verbatim between its
WATCHDOG-INVOKE-BEGIN/END markers, as test_wake_telemetry_safeguard_retry.py
does) followed by the REAL wd_drain_provider_process_group sourced from
wake_driver_finalize.sh, against a stub provider that is never a real
provider call: a bash script that double-forks a long sleep into the
background (a grandchild the wrapper's own wait never sees) and then blocks
in the foreground. Two stubs: one that ignores SIGTERM (the SIGKILL path)
and one that dies on it (the cooperative path).

The deadline is a real 60-second MAX_TASK_SECONDS on a real clock: the
measurement these tests report (seconds from deadline to an empty group) is
only comparable with KILL_GRACE_SECONDS if both are real time. So the two
scenarios run concurrently under one module-scoped fixture and the module
costs one deadline, not two.

Every process a scenario starts is owned by the test: the wrapper's pgid is
written to a file the moment it is known, and teardown SIGKILLs that group
and the harness's own session whether or not the assertions passed
(docs/test-authoring-guidelines.md "Own every process your subprocess
starts").
"""

from __future__ import annotations

import os
import pathlib
import re
import signal
import subprocess
import time
from dataclasses import dataclass

import pytest

# ---------------------------------------------------------------------------
# Paths (relative to this file: team/tests/unit/ -> team/ -> repo root)
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_LIB_DIR = _TEAM_DIR / "scripts" / "lib"
_DRIVER_INVOKE = _LIB_DIR / "wake_driver_invoke.sh"
_DRIVER_FINALIZE = _LIB_DIR / "wake_driver_finalize.sh"
_TEMP_SH = _LIB_DIR / "temp.sh"
# Defines _wd_epoch_now, the wall clock the deadline watchdog reads; these
# scenarios run on the real clock, so the shipped definition is sourced as-is.
_TASK_BUDGET_SH = _LIB_DIR / "wake_task_budget.sh"

_WATCHDOG_BEGIN = "# WATCHDOG-INVOKE-BEGIN"
_WATCHDOG_END = "# WATCHDOG-INVOKE-END"

# Real wall-clock budget: the deadline the acceptance names, and a grace
# window short enough to keep the module's cost near one deadline.
_MAX_TASK_SECONDS = 60
_KILL_GRACE_SECONDS = 5
# Ceiling on a scenario's own run: deadline + the watchdog's grace + the
# drain's grace + generous slack. A harness that outlives this has hung.
_SCENARIO_TIMEOUT = _MAX_TASK_SECONDS + 3 * _KILL_GRACE_SECONDS + 60


def _extract_watchdog_block() -> str:
    text = _DRIVER_INVOKE.read_text(encoding="utf-8")
    begin = text.index(_WATCHDOG_BEGIN)
    end = text.index(_WATCHDOG_END) + len(_WATCHDOG_END)
    return text[begin:end]


def _extract_function_body(name: str, source: pathlib.Path) -> str:
    text = source.read_text(encoding="utf-8")
    match = re.search(rf"^{re.escape(name)}\(\)\s*\{{\n(.*?)^\}}", text, re.S | re.M)
    assert match, f"function {name}() not found in {source}"
    return match.group(1)


def _stub_provider_script(marker: str, *, ignore_sigterm: bool) -> str:
    """A stand-in for a provider CLI that never reaches a real provider.

    Double-forks a long sleep (so the sleeper is a grandchild, reparented
    away from this script and invisible to any wait on it) and then blocks
    in the foreground until a signal ends it. Both processes carry *marker*
    in their argv so pgrep -f can find any survivor.
    """
    trap_line = "trap '' TERM   # ignored dispositions survive exec: sleep ignores it too" if ignore_sigterm else ""
    return f"""#!/usr/bin/env bash
# Test-only provider stand-in: forks a grandchild sleeper, then blocks.
{trap_line}
( ( exec -a {marker}-grandchild sleep 600 ) & )
exec -a {marker}-foreground sleep 600
"""


@dataclass
class ScenarioResult:
    name: str
    marker: str
    returncode: int
    stdout: str
    stderr: str
    log_text: str
    wrapper_finished: bool
    stray_argv: str  # pgrep -af <marker> output after the harness returned

    def value(self, key: str) -> str:
        match = re.search(rf"^{re.escape(key)}=(.*)$", self.stdout, re.M)
        assert match, f"{key}= missing from harness stdout:\n{self.stdout}\nstderr:\n{self.stderr}"
        return match.group(1).strip()

    def describe(self) -> str:
        return (
            f"scenario={self.name} rc={self.returncode}\n"
            f"--- stdout ---\n{self.stdout}\n--- stderr ---\n{self.stderr}\n"
            f"--- wake.log ---\n{self.log_text}"
        )


def _build_harness(scenario_dir: pathlib.Path, *, ignore_sigterm: bool, marker: str) -> tuple[str, pathlib.Path]:
    log_file = scenario_dir / "wake.log"
    log_file.write_text("", encoding="utf-8")
    temp_root = scenario_dir / "kanban_tmp"
    temp_root.mkdir()
    stub = scenario_dir / "stub-provider.sh"
    stub.write_text(_stub_provider_script(marker, ignore_sigterm=ignore_sigterm), encoding="utf-8")
    pgid_file = scenario_dir / "pgid"
    finished_file = scenario_dir / "wrapper_finished"

    harness = f"""
set -uo pipefail
export PGAI_AGENT_KANBAN_TEMP_DIR={str(temp_root)!r}
source {str(_TEMP_SH)!r}
source {str(_TASK_BUDGET_SH)!r}
source {str(_DRIVER_FINALIZE)!r}
log() {{ echo "[$(date -Iseconds)] wake(${{AGENT}}): $*" | tee -a "$LOG_FILE"; }}

LOG_FILE={str(log_file)!r}
AGENT="coder"
task_id="TASK-WATCHDOG-GROUP-{marker}"
MAX_TASK_SECONDS={_MAX_TASK_SECONDS}
KILL_GRACE_SECONDS={_KILL_GRACE_SECONDS}

# Stands in for the real invocation wrapper inside the watchdog's background
# subshell. $BASHPID here is that subshell's pid, and therefore its pgid:
# recorded first so the test can kill the group on any failure. The line
# after the stub returns proves the wrapper's own bookkeeping ran after the
# group-wide SIGTERM (the real wrapper's token capture sits at that point).
# The poll loop's held-lock mtime refresh: no project lock is held in this
# harness, so the real wrapper (wake_driver_invoke.sh) would be a no-op;
# stubbed to keep the extracted block self-contained.
_wd_refresh_project_lock_mtime() {{ :; }}

_wd_run_provider_with_telemetry_safeguard_retry() {{
  echo "$BASHPID" > {str(pgid_file)!r}
  bash {str(stub)!r} </dev/null >/dev/null 2>&1
  PROVIDER_AGENT_EXIT_CODE=$?
  echo "wrapper finished after exit ${{PROVIDER_AGENT_EXIT_CODE}}" > {str(finished_file)!r}
}}

_run_watchdog() {{
{_extract_watchdog_block()}
}}
_run_watchdog
echo "MEMBERS_BEFORE_DRAIN=$(ps -eo pid=,pgid= | awk -v g="${{_provider_pgid}}" '$2 == g' | wc -l)"
wd_drain_provider_process_group
echo "PGID=${{_provider_pgid}}"
echo "TASK_KILLED=${{_task_killed}}"
echo "FINAL_EXIT=${{PROVIDER_AGENT_EXIT_CODE}}"
echo "SIGKILLED=${{_provider_group_sigkilled}}"
echo "MEMBERS_AFTER_DRAIN=$(ps -eo pid=,pgid= | awk -v g="${{_provider_pgid}}" '$2 == g' | wc -l)"
"""
    return harness, log_file


def _killpg_if_alive(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _kill_recorded_group(scenario_dir: pathlib.Path) -> None:
    pgid_file = scenario_dir / "pgid"
    if not pgid_file.is_file():
        return
    text = pgid_file.read_text(encoding="utf-8").strip()
    if text.isdigit():
        _killpg_if_alive(int(text))


def _pgrep(marker: str) -> str:
    return subprocess.run(
        ["pgrep", "-af", marker], capture_output=True, text=True, timeout=10
    ).stdout.strip()


@pytest.fixture(scope="module")
def scenarios(tmp_path_factory: pytest.TempPathFactory) -> dict[str, ScenarioResult]:
    """Runs the hostile (SIGTERM-ignoring) and cooperative stubs concurrently,
    each through the real watchdog block and the real finalize drain, and
    kills every process either started before handing the results out."""
    base = tmp_path_factory.mktemp("watchdog_group")
    stamp = f"{os.getpid()}-{int(time.time())}"
    plan = {
        "hostile": True,
        "cooperative": False,
    }
    procs: dict[str, tuple[subprocess.Popen, pathlib.Path, pathlib.Path, str]] = {}
    results: dict[str, ScenarioResult] = {}
    try:
        for name, ignore in plan.items():
            scenario_dir = base / name
            scenario_dir.mkdir()
            marker = f"pgai-watchdog-stub-{name}-{stamp}"
            harness, log_file = _build_harness(scenario_dir, ignore_sigterm=ignore, marker=marker)
            env = dict(os.environ)
            proc = subprocess.Popen(
                ["bash", "-c", harness],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                cwd=str(base),
                start_new_session=True,
            )
            procs[name] = (proc, scenario_dir, log_file, marker)

        for name, (proc, scenario_dir, log_file, marker) in procs.items():
            stdout, stderr = proc.communicate(timeout=_SCENARIO_TIMEOUT)
            results[name] = ScenarioResult(
                name=name,
                marker=marker,
                returncode=proc.returncode,
                stdout=stdout,
                stderr=stderr,
                log_text=log_file.read_text(encoding="utf-8"),
                wrapper_finished=(scenario_dir / "wrapper_finished").is_file(),
                stray_argv=_pgrep(marker),
            )
    finally:
        # Own every process the scenarios started, on every exit path: the
        # provider group (its pgid was recorded by the stub function before
        # the stub ran) and the harness's own session.
        for name, (proc, scenario_dir, _log_file, _marker) in procs.items():
            _kill_recorded_group(scenario_dir)
            if proc.poll() is None:
                _killpg_if_alive(proc.pid)
                proc.communicate()
    return results


def _deadline_to_empty_seconds(result: ScenarioResult) -> int:
    match = re.search(
        rf"provider process group {result.value('PGID')} gone (\d+)s after the {_MAX_TASK_SECONDS}s deadline; finalizing",
        result.log_text,
    )
    assert match, (
        "Expected the finalize drain to narrate the measured deadline-to-empty "
        f"time; got:\n{result.describe()}"
    )
    return int(match.group(1))


# ---------------------------------------------------------------------------
# (1) Hostile provider: ignores SIGTERM, so the group needs SIGKILL.
# ---------------------------------------------------------------------------


def test_sigterm_ignoring_provider_tree_is_sigkilled_and_gone_before_finalize(
    scenarios: dict[str, ScenarioResult],
) -> None:
    """With a 60s max_task_seconds and a stub that ignores SIGTERM and has
    double-forked a sleeper, the deadline sends SIGTERM to the group, waits
    the grace, sends SIGKILL to the group, and after the finalize drain no
    process of that group -- and no process carrying the stub's marker --
    exists."""
    result = scenarios["hostile"]
    assert result.returncode == 0, f"Harness failed:\n{result.describe()}"
    pgid = result.value("PGID")
    assert result.value("TASK_KILLED") == "true", result.describe()
    assert result.value("FINAL_EXIT") == "124", result.describe()
    assert result.value("SIGKILLED") == "true", result.describe()

    log_text = result.log_text
    assert f"sending SIGTERM to provider process group {pgid}" in log_text, result.describe()
    assert (
        f"grace period ({_KILL_GRACE_SECONDS}s) expired; provider process group {pgid} "
        "still alive; sending SIGKILL to the group"
    ) in log_text, result.describe()
    assert result.value("MEMBERS_AFTER_DRAIN") == "0", (
        "Expected no process left in the provider's group after the drain.\n"
        + result.describe()
    )
    assert result.stray_argv == "", (
        f"Stub processes survived the group kill:\n{result.stray_argv}\n{result.describe()}"
    )
    print(
        f"measured deadline-to-empty-group: {_deadline_to_empty_seconds(result)}s "
        f"(KILL_GRACE_SECONDS={_KILL_GRACE_SECONDS}, hostile provider)"
    )


def test_sigterm_ignoring_provider_is_gone_within_one_grace_window(
    scenarios: dict[str, ScenarioResult],
) -> None:
    """The measured time from deadline to empty group is at least the grace
    window (SIGKILL is never sent early) and not much more than it (the
    drain does not add a second wait once SIGKILL has landed)."""
    result = scenarios["hostile"]
    assert result.returncode == 0, f"Harness failed:\n{result.describe()}"
    elapsed = _deadline_to_empty_seconds(result)
    assert _KILL_GRACE_SECONDS <= elapsed <= _KILL_GRACE_SECONDS + 5, (
        f"deadline-to-empty took {elapsed}s; expected within "
        f"[{_KILL_GRACE_SECONDS}, {_KILL_GRACE_SECONDS + 5}]s.\n{result.describe()}"
    )


# ---------------------------------------------------------------------------
# (2) Cooperative provider: exits on SIGTERM, so SIGKILL is never sent.
# ---------------------------------------------------------------------------


def test_sigterm_honouring_provider_tree_exits_without_sigkill(
    scenarios: dict[str, ScenarioResult],
) -> None:
    """A stub whose whole tree dies on SIGTERM produces the SIGTERM line and
    no SIGKILL line; the finalize drain finds the group already empty and
    proceeds within a fraction of the grace window."""
    result = scenarios["cooperative"]
    assert result.returncode == 0, f"Harness failed:\n{result.describe()}"
    pgid = result.value("PGID")
    assert result.value("TASK_KILLED") == "true", result.describe()
    assert result.value("FINAL_EXIT") == "124", result.describe()
    assert result.value("SIGKILLED") == "false", result.describe()

    log_text = result.log_text
    assert f"sending SIGTERM to provider process group {pgid}" in log_text, result.describe()
    assert "sending SIGKILL" not in log_text, (
        f"Cooperative provider must never draw a SIGKILL:\n{result.describe()}"
    )
    assert result.value("MEMBERS_AFTER_DRAIN") == "0", result.describe()
    assert result.stray_argv == "", (
        f"Stub processes survived SIGTERM:\n{result.stray_argv}\n{result.describe()}"
    )
    elapsed = _deadline_to_empty_seconds(result)
    assert elapsed < _KILL_GRACE_SECONDS, (
        f"deadline-to-empty took {elapsed}s for a cooperative provider; expected "
        f"under the {_KILL_GRACE_SECONDS}s grace.\n{result.describe()}"
    )
    print(
        f"measured deadline-to-empty-group: {elapsed}s "
        f"(KILL_GRACE_SECONDS={_KILL_GRACE_SECONDS}, cooperative provider)"
    )


def test_wrapper_bookkeeping_survives_the_group_sigterm(
    scenarios: dict[str, ScenarioResult],
) -> None:
    """The wrapper subshell's own code after the provider returns (where the
    real wrapper captures tokens and writes the exit-code file) still runs
    after the group-wide SIGTERM: its no-op handler keeps it alive while the
    exec'd provider, whose disposition reset to default, dies."""
    result = scenarios["cooperative"]
    assert result.returncode == 0, f"Harness failed:\n{result.describe()}"
    assert result.wrapper_finished, (
        "Expected the wrapper's post-provider bookkeeping to run after the "
        f"group SIGTERM; the marker file was never written.\n{result.describe()}"
    )


# ---------------------------------------------------------------------------
# (3) Structural guarantees on the shipped files.
# ---------------------------------------------------------------------------


def test_launch_site_uses_job_control_and_claims_no_setsid() -> None:
    """The launch runs under `set -m` (turned back off once the job exists),
    and nothing in wake_driver_invoke.sh claims a setsid the code does not
    perform."""
    block = _extract_watchdog_block()
    assert "setsid" not in _DRIVER_INVOKE.read_text(encoding="utf-8").lower(), (
        "wake_driver_invoke.sh mentions setsid; the launch uses bash job control."
    )
    on = block.index("set -m")
    launch = block.index(") &", on)
    off = block.index("set +m", launch)
    assert on < launch < off, (
        "Expected `set -m` before the background launch and `set +m` right after it."
    )
    assert "trap ':' TERM" in block, (
        "Expected the wrapper subshell to install a no-op SIGTERM handler."
    )
    assert 'kill -TERM -- "-${_provider_pgid}"' in block
    assert 'kill -KILL -- "-${_provider_pgid}"' in block
    assert 'kill -0 -- "-${_provider_pgid}"' in block, (
        "Expected the grace check to probe the process group, not the wrapper pid."
    )


def test_finalize_drains_the_group_before_any_state_write_or_teardown() -> None:
    """wd_finalize_task calls the drain before it writes task state, marks
    the backlog, or tears down the worktree."""
    body = _extract_function_body("wd_finalize_task", _DRIVER_FINALIZE)
    drain_at = body.index("wd_drain_provider_process_group")
    for later in ("block_task_with_record ", "mark_backlog ", "teardown_task_worktree "):
        assert later in body, f"{later!r} not found in wd_finalize_task"
        assert drain_at < body.index(later), (
            f"wd_drain_provider_process_group must run before {later!r} in wd_finalize_task."
        )


def test_drain_is_a_noop_without_a_provider_group(tmp_path: pathlib.Path) -> None:
    """With MAX_TASK_SECONDS=0 the provider runs in the foreground and no
    group is recorded; the drain returns 0 and narrates nothing."""
    log_file = tmp_path / "wake.log"
    log_file.write_text("", encoding="utf-8")
    harness = f"""
set -uo pipefail
source {str(_DRIVER_FINALIZE)!r}
log() {{ echo "$*" >> {str(log_file)!r}; }}
task_id="TASK-NO-GROUP"
_provider_pgid=""
wd_drain_provider_process_group
echo "RC=$?"
"""
    result = subprocess.run(
        ["bash", "-c", harness], capture_output=True, text=True, timeout=30,
        cwd=str(tmp_path), stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "RC=0" in result.stdout, result.stdout
    assert log_file.read_text(encoding="utf-8") == "", (
        f"Expected no narration without a group; got:\n{log_file.read_text(encoding='utf-8')}"
    )


def test_drain_reaps_a_stray_left_behind_by_a_provider_that_exited(tmp_path: pathlib.Path) -> None:
    """A group the watchdog never signalled (the provider returned on its
    own, leaving a background sleeper) is SIGTERMed by the drain, and the
    drain does not return until the group is empty."""
    log_file = tmp_path / "wake.log"
    log_file.write_text("", encoding="utf-8")
    marker = f"pgai-watchdog-stray-{os.getpid()}-{int(time.time())}"
    pgid_file = tmp_path / "pgid"
    harness = f"""
set -uo pipefail
source {str(_DRIVER_FINALIZE)!r}
log() {{ echo "$*" >> {str(log_file)!r}; }}
task_id="TASK-STRAY"
KILL_GRACE_SECONDS=3
_task_killed=false
_provider_group_sigkilled=false
set -m
( exec -a {marker} sleep 600 </dev/null >/dev/null 2>&1 ) &
_provider_pgid=$!
set +m
echo "$_provider_pgid" > {str(pgid_file)!r}
wd_drain_provider_process_group
echo "RC=$?"
echo "MEMBERS_AFTER_DRAIN=$(ps -eo pid=,pgid= | awk -v g="${{_provider_pgid}}" '$2 == g' | wc -l)"
"""
    try:
        result = subprocess.run(
            ["bash", "-c", harness], capture_output=True, text=True, timeout=60,
            cwd=str(tmp_path), stdin=subprocess.DEVNULL,
        )
    finally:
        _kill_recorded_group(tmp_path)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "RC=0" in result.stdout, result.stdout
    assert "MEMBERS_AFTER_DRAIN=0" in result.stdout, result.stdout
    log_text = log_file.read_text(encoding="utf-8")
    assert "still alive after the invocation returned" in log_text, log_text
    assert "sending SIGTERM to the group" in log_text, log_text
    assert "sending SIGKILL" not in log_text, log_text
    assert "gone; finalizing" in log_text, log_text
    assert _pgrep(marker) == "", f"stray survived the drain: {_pgrep(marker)}"


@pytest.mark.parametrize("script", [_DRIVER_INVOKE, _DRIVER_FINALIZE])
def test_shipped_lib_parses(script: pathlib.Path) -> None:
    result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"bash -n {script} failed:\n{result.stderr}"
