"""
test_wake_agent_run_reap.py
===========================
No process an agent run starts outlives the run.

wd_invoke_task (team/scripts/lib/wake_driver_invoke.sh) marks the run --
agent_run_begin exports a PGAI_AGENT_RUN_ID unique to it -- launches the
provider, and once the provider has returned calls agent_run_end
(team/scripts/lib/agent_run_reap.sh), which stops every process still
carrying that id and logs each one's pid and command in the batch log and
the task's logs/process-reap.log.

Each scenario runs the REAL span of wd_invoke_task from agent_run_begin to
agent_run_end, extracted verbatim (the provider launch, the dispatch ledger
and the timeout watchdog it encloses), against a stub provider that is
never a real provider call: a bash script that, from the task's scratch
directory, leaves a `sleep` running in the background and then returns or
blocks. The sleeper is started with setsid, so it sits in a session of its
own -- outside the provider's process group, re-parented to PID 1 once the
stub is gone -- which is exactly what no group signal reaches.

  1. Normal exit, timeout armed: one second after the agent step returns
     no sleeper survives, and the task log names its pid and command.
  2. Normal exit, timeout disabled (max_task_seconds 0, the provider runs
     in the wake's own shell): the same.
  3. Timeout: the stub blocks past a 2-second limit; the watchdog stops the
     group (exit 124), and the escaped sleeper is reaped and logged.
  4. A wake that exits mid-run: cleanup_on_exit (script_common.sh) reaps
     the run from its exit trap before the wake exits.
  5. A process that does not carry the run id -- standing in for the
     operator's own shell or another agent's process -- is never signalled.

Every sleeper carries a marker unique to the test in its argv; teardown
kills whatever still matches it, pass or fail, and each harness runs in a
session of its own that teardown kills as well
(docs/test-authoring-guidelines.md "Own every process your subprocess
starts").
"""

from __future__ import annotations

import os
import pathlib
import re
import signal
import subprocess
import sys
import uuid

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_LIB_DIR = _TEAM_DIR / "scripts" / "lib"
_INVOKE = _LIB_DIR / "wake_driver_invoke.sh"

_SPAN_BEGIN = "  # --- Mark the agent run"
_SPAN_END_CALL = "agent_run_end "
_HARNESS_TIMEOUT = 90


def _extract_run_span() -> str:
    """wd_invoke_task's lines from agent_run_begin through agent_run_end, verbatim."""
    text = _INVOKE.read_text(encoding="utf-8")
    begin = text.index(_SPAN_BEGIN)
    end_call = text.index(_SPAN_END_CALL, begin)
    end = text.index("\n", end_call)
    return text[begin:end]


def _stub_provider(marker: str, *, block: bool) -> str:
    tail = f"exec -a {marker}-foreground sleep 600" if block else "exit 0"
    return f"""#!/usr/bin/env bash
# Test-only provider stand-in: leaves a sleeper in a session of its own,
# started from the task's scratch directory, then {'blocks' if block else 'returns'}.
cd "$TASK_SCRATCH"
setsid bash -c 'exec -a {marker}-sleeper sleep 600' </dev/null >/dev/null 2>&1 &
{tail}
"""


def _pgrep(marker: str) -> list[str]:
    out = subprocess.run(["pgrep", "-af", f"{marker}-"], capture_output=True, text=True, timeout=10, check=False).stdout
    return [line for line in out.splitlines() if line.strip()]


def _kill_marker(marker: str) -> None:
    subprocess.run(["pkill", "-KILL", "-f", f"{marker}-"], capture_output=True, timeout=10, check=False)


class _Scenario:
    def __init__(self, tmp_path: pathlib.Path, name: str) -> None:
        self.dir = tmp_path / name
        self.dir.mkdir()
        self.marker = f"pgai-run-reap-{name}-{uuid.uuid4().hex[:10]}"
        self.task_log_dir = self.dir / "task" / "logs"
        self.task_log_dir.mkdir(parents=True)
        self.scratch = self.dir / "scratch"
        self.scratch.mkdir()
        self.temp_root = self.dir / "kanban_tmp"
        self.temp_root.mkdir()
        self.root = self.dir / "kanban"
        self.root.mkdir()
        self.log_file = self.dir / "wake.log"
        self.log_file.write_text("", encoding="utf-8")
        self.stub = self.dir / "stub-provider.sh"

    @property
    def reap_log(self) -> pathlib.Path:
        return self.task_log_dir / "process-reap.log"

    def env(self, extra: dict | None = None) -> dict:
        return build_scrubbed_env(
            home_dir=self.dir / "home",
            extra_path_dirs=[pathlib.Path(sys.executable).parent],
            extra_env={
                "KANBAN_ROOT": str(self.root),
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.temp_root),
                "TASK_SCRATCH": str(self.scratch),
                **(extra or {}),
            },
        )

    def run(self, script: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
        proc = subprocess.Popen(
            ["bash", "-c", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self.env(extra_env),
            cwd=str(self.dir),
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=_HARNESS_TIMEOUT)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)

    def describe(self, result: subprocess.CompletedProcess) -> str:
        reap = self.reap_log.read_text(encoding="utf-8") if self.reap_log.exists() else "(absent)"
        return (
            f"exit {result.returncode}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}\n"
            f"--- wake.log ---\n{self.log_file.read_text(encoding='utf-8')}\n--- process-reap.log ---\n{reap}\n"
            f"--- still running ---\n{_pgrep(self.marker)}"
        )


def _agent_step_harness(box: _Scenario, *, max_task_seconds: int, grace: int) -> str:
    """The real begin..end span inside a stand-in wd_invoke_task, then a one-second wait."""
    return f"""
set -uo pipefail
source {str(_LIB_DIR / 'temp.sh')!r}
source {str(_LIB_DIR / 'wake_task_budget.sh')!r}
source {str(_LIB_DIR / 'agent_run_reap.sh')!r}
LOG_FILE={str(box.log_file)!r}
AGENT=coder
log() {{ echo "[$(date -Iseconds)] wake(${{AGENT}}): $*" >> "$LOG_FILE"; }}
task_id="TASK-RUN-REAP-{box.marker}"
task_dir={str(box.task_log_dir.parent)!r}
task_log_dir={str(box.task_log_dir)!r}
MAX_TASK_SECONDS={max_task_seconds}
KILL_GRACE_SECONDS={grace}
dispatch_record_append() {{ return 0; }}
_wd_refresh_project_lock_mtime() {{ :; }}
_wd_run_provider_with_telemetry_safeguard_retry() {{
  bash {str(box.stub)!r} </dev/null >/dev/null 2>&1
  PROVIDER_AGENT_EXIT_CODE=$?
}}
_agent_step() {{
{_extract_run_span()}
}}
_agent_step
echo "PROVIDER_EXIT=${{PROVIDER_AGENT_EXIT_CODE}}"
echo "RUN_ID_LEFT=${{PGAI_AGENT_RUN_ID:-}}|${{CLEANUP_ON_EXIT_AGENT_RUN_ID:-}}"
sleep 1
"""


def _assert_reaped(box: _Scenario, result: subprocess.CompletedProcess) -> None:
    detail = box.describe(result)
    assert result.returncode == 0, detail
    assert _pgrep(box.marker) == [], detail
    assert "RUN_ID_LEFT=|" in result.stdout, detail
    reap_lines = box.reap_log.read_text(encoding="utf-8").splitlines() if box.reap_log.exists() else []
    sleeper = [line for line in reap_lines if f"command={box.marker}-sleeper 600" in line]
    assert len(sleeper) == 1, detail
    assert re.search(r"reaped pid=\d+ outcome=terminated ", sleeper[0]), detail
    assert f"cwd={box.scratch}" in sleeper[0], detail
    pid = re.search(r"pid=(\d+)", sleeper[0]).group(1)
    wake_log = box.log_file.read_text(encoding="utf-8")
    assert f"stopped a process the agent run left running: pid={pid} " in wake_log, detail


@pytest.fixture
def scenario(tmp_path: pathlib.Path, request: pytest.FixtureRequest):
    boxes: list[_Scenario] = []

    def make(name: str) -> _Scenario:
        box = _Scenario(tmp_path, name)
        boxes.append(box)
        return box

    yield make
    for box in boxes:
        _kill_marker(box.marker)


def test_a_background_process_left_by_a_finished_run_is_reaped_and_logged(scenario) -> None:
    box = scenario("normal-armed")
    box.stub.write_text(_stub_provider(box.marker, block=False), encoding="utf-8")

    result = box.run(_agent_step_harness(box, max_task_seconds=60, grace=5))

    assert "PROVIDER_EXIT=0" in result.stdout, box.describe(result)
    _assert_reaped(box, result)


def test_a_run_with_the_timeout_disabled_is_reaped_too(scenario) -> None:
    box = scenario("normal-unarmed")
    box.stub.write_text(_stub_provider(box.marker, block=False), encoding="utf-8")

    result = box.run(_agent_step_harness(box, max_task_seconds=0, grace=5))

    assert "PROVIDER_EXIT=0" in result.stdout, box.describe(result)
    _assert_reaped(box, result)


def test_a_timed_out_run_has_its_escaped_process_reaped(scenario) -> None:
    box = scenario("timeout")
    box.stub.write_text(_stub_provider(box.marker, block=True), encoding="utf-8")

    result = box.run(_agent_step_harness(box, max_task_seconds=2, grace=2))

    detail = box.describe(result)
    assert "PROVIDER_EXIT=124" in result.stdout, detail
    assert "timeout fired after" in box.log_file.read_text(encoding="utf-8"), detail
    _assert_reaped(box, result)


def test_a_wake_that_exits_mid_run_reaps_the_run_from_its_exit_trap(scenario) -> None:
    box = scenario("exit-trap")
    box.stub.write_text(_stub_provider(box.marker, block=False), encoding="utf-8")
    script = f"""
set -uo pipefail
source {str(_LIB_DIR / 'script_common.sh')!r}
source {str(_LIB_DIR / 'agent_run_reap.sh')!r}
LOG_FILE={str(box.log_file)!r}
log() {{ echo "[$(date -Iseconds)] wake(coder): $*" >> "$LOG_FILE"; }}
KILL_GRACE_SECONDS=2
trap cleanup_on_exit EXIT
agent_run_begin "TASK-RUN-REAP-EXIT" {str(box.reap_log)!r}
bash {str(box.stub)!r} </dev/null >/dev/null 2>&1
# The marker is read from the environment, so this script's own command line
# never matches the pattern it looks for.
for _ in $(seq 1 50); do pgrep -f "${{MARK}}-sleeper" >/dev/null && break; sleep 0.1; done
echo "SLEEPER_STARTED=$(pgrep -f "${{MARK}}-sleeper" >/dev/null && echo yes || echo no)"
exit 0
"""
    result = box.run(script, {"MARK": box.marker})

    detail = box.describe(result)
    assert "SLEEPER_STARTED=yes" in result.stdout, detail
    assert _pgrep(box.marker) == [], detail
    reap = box.reap_log.read_text(encoding="utf-8") if box.reap_log.exists() else ""
    assert f"command={box.marker}-sleeper 600" in reap, detail


def test_a_process_without_the_run_id_is_never_signalled(scenario) -> None:
    box = scenario("bystander")
    box.stub.write_text(_stub_provider(box.marker, block=False), encoding="utf-8")
    bystander = subprocess.Popen(
        ["bash", "-c", f"exec -a {box.marker}-bystander sleep 600"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(box.scratch),
        env=box.env(),
        start_new_session=True,
    )
    try:
        result = box.run(_agent_step_harness(box, max_task_seconds=60, grace=5))
        detail = box.describe(result)
        assert bystander.poll() is None, detail
        still = _pgrep(box.marker)
        assert len(still) == 1 and f"{box.marker}-bystander" in still[0], detail
        reap = box.reap_log.read_text(encoding="utf-8")
        assert f"{box.marker}-sleeper" in reap and f"{box.marker}-bystander" not in reap, detail
    finally:
        bystander.kill()
        bystander.wait(timeout=10)
