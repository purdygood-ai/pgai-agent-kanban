"""
test_container_down.py
======================
scripts/container-down.sh soft-stops the kanban container:

  * it stops the in-container scheduler (the tmux session "pseudocron", or a
    scheduler process started by hand) before any container stop, sending
    the scheduler SIGTERM before it closes the tmux session;
  * it waits while an agent session, a wake driver (wake-batch.sh,
    wake.sh, wake-now.sh) or an OVERWATCH sweep (overwatch-sweep.sh) is
    listed in the container, printing progress lines, and stops and removes
    the container once none remains; when
    either outlives [docker] container_stop_timeout_seconds it says so and
    still stops and removes;
  * --now stops and removes at once, naming the session it ends;
  * neither mode writes or removes a HALT file;
  * a stopped container is removed, an absent one is reported (exit 0);
  * inside an agent wake it prints SKIPPED and calls no engine;
  * a malformed timeout, an unknown argument or a failed engine query is
    refused dual-channel before anything is stopped.

No test reaches a real engine. Every subprocess runs with an environment
built from an explicit set (tests.fixtures.scrubbed_subprocess_env), a
throwaway kanban root and HOME, and a fake engine
(tests.fixtures.fake_container_engine) first on PATH. The authorized runs
carry PGAI_CONTAINER_TIER=1 and no PGAI_AGENT_WAKE_ACTIVE, which
build_scrubbed_env never inherits. The fake engine's agent processes leave
the process table after a fixed number of 'ps' polls, and the waits run
with --progress-interval 1 and a timeout of a few seconds, so no test
waits in real time for more than a few seconds.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import time

import pytest

from tests.fixtures.fake_container_engine import (
    FakeEngine,
    agent_process,
    container,
    scheduler_process,
    wake_driver_process,
)
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_DOWN_SH = _TEAM_DIR / "scripts" / "container-down.sh"

_NAME = "pgai-kanban"
_TASK = "CODER-20261011-901-example-task"
_AGENT_PID = 77
_SCHEDULER_PID = 40
_SKIP_KEY = "key=container_tier.operator_only"
_PROGRESS = "waiting for 1 agent session(s) to exit"
_DRIVER_PID = 60
_DRIVER_PROGRESS = "1 wake driver process(es) to finish: wake-batch.sh pid 60"
_IDLE = "no agent session is running and no wake driver or OVERWATCH sweep remains"
_SWEEP_PID = 90
_SWEEP_PROGRESS = "1 OVERWATCH sweep(s) to finish: overwatch-sweep.sh pid 90"


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"rc={result.returncode}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _contract(stderr: str) -> dict:
    """The error/message/remedy fields of the dual-channel block in *stderr*."""
    fields = {}
    for line in stderr.splitlines():
        for key in ("error", "message", "remedy"):
            prefix = f"{key}:"
            if line.startswith(prefix) and key not in fields:
                fields[key] = line[len(prefix):].strip()
    return fields


class _Sandbox:
    """A throwaway kanban root, HOME and temp dir, with a fake engine first
    on PATH and no container until a test describes one."""

    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.tmp = tmp_path
        self.root = (tmp_path / "kanban_root").resolve()
        self.root.mkdir()
        self.home = (tmp_path / "home").resolve()
        self.home.mkdir()
        self.temp = tmp_path / "pgai_tmp"
        self.temp.mkdir()
        self.engine = FakeEngine(tmp_path / "engine", name="docker", scenario={"images": ["pgai-kanban:latest"]})
        # The interpreter running this test answers python3 inside the script.
        self.py_bin = tmp_path / "py_bin"
        self.py_bin.mkdir()
        (self.py_bin / "python3").symlink_to(pathlib.Path(sys.executable))

    def running(self, *, agent_polls: int | None = None, with_agent: bool = True, name: str = _NAME,
                scheduler: dict | None = None, driver_polls: int | None = None,
                with_driver: bool = False, sweep_polls: int | None = None,
                with_sweep: bool = False) -> None:
        """A running container with a scheduler and (by default) one agent
        session listed for *agent_polls* 'ps' polls (forever when None);
        with *with_driver*, also a wake-batch.sh process listed for
        *driver_polls* polls, with no liveness record of its own."""
        processes = [scheduler if scheduler is not None else scheduler_process(_SCHEDULER_PID, self.root)]
        if with_agent:
            processes.append(agent_process(_AGENT_PID, _TASK, exits_after_polls=agent_polls))
            self.liveness_record(_TASK, _AGENT_PID)
        if with_driver:
            processes.append(wake_driver_process(_DRIVER_PID, self.root, exits_after_polls=driver_polls))
        if with_sweep:
            processes.append(wake_driver_process(
                _SWEEP_PID, self.root, script="overwatch-sweep.sh", args="--only block-triage --lock-wait 60",
                exits_after_polls=sweep_polls,
            ))
        self.scenario(containers={name: container(processes=processes)})

    def liveness_record(self, task_id: str, pid: int) -> None:
        records = self.root / "locks" / "task-liveness"
        records.mkdir(parents=True, exist_ok=True)
        (records / f"{task_id}.record").write_text(
            f"holder=claude\npid={pid}\nop=dispatch\ntask={task_id}\n", encoding="utf-8"
        )

    def scenario(self, **changes) -> None:
        scenario = self.engine.scenario()
        scenario.update(changes)
        self.engine.set_scenario(scenario)

    def write_cfg(self, text: str) -> None:
        (self.root / "kanban.cfg").write_text(text, encoding="utf-8")

    def env(self, *, wake: bool = False) -> dict:
        extra = {
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.temp),
        }
        if wake:
            extra["PGAI_AGENT_WAKE_ACTIVE"] = "1"
        else:
            extra["PGAI_CONTAINER_TIER"] = "1"
        env = build_scrubbed_env(
            home_dir=self.home,
            extra_path_dirs=(self.engine.bin_dir, self.py_bin),
            extra_env=extra,
        )
        if not wake:
            assert "PGAI_AGENT_WAKE_ACTIVE" not in env
        return env

    def down(self, *args: str, wake: bool = False) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(_DOWN_SH), *args],
            capture_output=True,
            text=True,
            env=self.env(wake=wake),
            cwd=str(self.tmp),
            timeout=60,
        )

    def commands(self) -> list[str]:
        return self.engine.commands()

    def calls_named(self, command: str) -> list[list[str]]:
        return [r["argv"] for r in self.engine.records() if r["command"] == command]

    def container_exists(self, name: str = _NAME) -> bool:
        return name in self.engine.scenario().get("containers", {})


@pytest.fixture
def sandbox(tmp_path: pathlib.Path) -> _Sandbox:
    return _Sandbox(tmp_path)


def _index_of_call(sb: _Sandbox, predicate) -> int:
    for i, argv in enumerate(sb.engine.calls()):
        if predicate(argv):
            return i
    raise AssertionError(f"no matching engine call in {sb.commands()}")


def _is_scheduler_stop(argv: list[str]) -> bool:
    return argv[:1] == ["exec"] and "tmux" in argv and "kill-session" in argv


def _assert_stopped_then_removed(sb: _Sandbox, name: str = _NAME) -> None:
    commands = sb.commands()
    assert sb.calls_named("stop") == [["stop", name]], commands
    assert sb.calls_named("rm") == [["rm", name]], commands
    assert commands.index("stop") < commands.index("rm"), commands
    assert not sb.container_exists(name), sb.engine.scenario()


# ---------------------------------------------------------------------------
# Soft stop: scheduler first, wait for the session, then stop and remove
# ---------------------------------------------------------------------------


def test_soft_stop_stops_scheduler_first_waits_for_the_session_then_stops_and_removes(sandbox):
    polls_listed = 3
    sandbox.running(agent_polls=polls_listed)
    result = sandbox.down("--progress-interval", "1")

    assert result.returncode == 0, _diagnostic(result)
    commands = sandbox.commands()
    scheduler_stop = _index_of_call(sandbox, _is_scheduler_stop)
    stop = commands.index("stop")
    assert scheduler_stop < stop, commands
    # The session is listed by the first `polls_listed` process-table reads;
    # the stop may only follow a read that no longer lists it.
    reads_before_stop = commands[:stop].count("exec ps")
    assert reads_before_stop >= polls_listed + 1, commands
    _assert_stopped_then_removed(sandbox)
    assert _PROGRESS in result.stdout and _TASK in result.stdout, _diagnostic(result)
    assert "no agent session is running" in result.stdout, _diagnostic(result)

    # One docker-use session records the whole run with its exit code.
    use_log = (sandbox.root / "logs" / "docker-use.log").read_text(encoding="utf-8")
    assert "START actor=operator" in use_log and "script=container-down.sh" in use_log, use_log
    assert "exit=0" in use_log, use_log


def test_wait_prints_a_progress_line_per_interval(sandbox):
    sandbox.running(agent_polls=6)
    started = time.monotonic()
    result = sandbox.down("--progress-interval", "1")
    elapsed = time.monotonic() - started

    assert result.returncode == 0, _diagnostic(result)
    progress = [line for line in result.stdout.splitlines() if _PROGRESS in line]
    assert len(progress) >= 2, _diagnostic(result)
    # Each line reports a later point in the wait than the one before.
    assert len(set(progress)) == len(progress), progress
    assert elapsed < 20, f"the wait took {elapsed:.1f}s"
    _assert_stopped_then_removed(sandbox)


def test_session_outliving_the_timeout_is_reported_and_the_container_still_stops(sandbox):
    sandbox.write_cfg("[docker]\ncontainer_stop_timeout_seconds = 2\n")
    sandbox.running(agent_polls=None)
    started = time.monotonic()
    result = sandbox.down("--progress-interval", "1")
    elapsed = time.monotonic() - started

    assert result.returncode == 0, _diagnostic(result)
    assert elapsed < 20, f"the wait took {elapsed:.1f}s"
    timeout_lines = [line for line in result.stdout.splitlines() if "timeout:" in line]
    assert len(timeout_lines) == 1, _diagnostic(result)
    assert "after 2s" in timeout_lines[0] and _TASK in timeout_lines[0], timeout_lines
    _assert_stopped_then_removed(sandbox)


def test_scheduler_started_outside_tmux_is_sent_sigterm_before_the_container_stops(sandbox):
    by_hand = {"pid": _SCHEDULER_PID, "args": f"python3 {sandbox.root / 'scripts' / 'pseudocron.py'}"}
    sandbox.running(with_agent=False, scheduler=by_hand)
    result = sandbox.down()

    assert result.returncode == 0, _diagnostic(result)
    assert sandbox.calls_named("exec tmux") == [["exec", _NAME, "tmux", "has-session", "-t", "pseudocron"]]
    kill = _index_of_call(sandbox, lambda a: a[:3] == ["exec", _NAME, "kill"])
    assert sandbox.engine.calls()[kill][3:] == [str(_SCHEDULER_PID)]
    assert kill < sandbox.commands().index("stop"), sandbox.commands()
    _assert_stopped_then_removed(sandbox)


def test_scheduler_is_sent_sigterm_before_its_tmux_session_is_closed(sandbox):
    sandbox.running(with_agent=False)
    result = sandbox.down()

    assert result.returncode == 0, _diagnostic(result)
    calls = sandbox.engine.calls()
    has_session = _index_of_call(sandbox, lambda a: a[:4] == ["exec", _NAME, "tmux", "has-session"])
    sigterm = _index_of_call(sandbox, lambda a: a[:3] == ["exec", _NAME, "kill"])
    close = _index_of_call(sandbox, _is_scheduler_stop)
    assert calls[sigterm][3:] == [str(_SCHEDULER_PID)], calls
    assert has_session < sigterm < close, calls
    # The session is closed only after a process-table read that no longer
    # lists the scheduler.
    reads_between = [c for c in calls[sigterm + 1:close] if c[:3] == ["exec", _NAME, "ps"]]
    assert reads_between, calls
    assert "sent SIGTERM to scheduler process(es) 40" in result.stdout, _diagnostic(result)
    assert "closed after the scheduler exited" in result.stdout, _diagnostic(result)
    _assert_stopped_then_removed(sandbox)


def test_scheduler_session_still_open_after_the_scheduler_exits_is_closed(sandbox):
    # A tmux session that outlives its scheduler (a second window, say) is
    # still closed, and kill-session is the call that closes it.
    sandbox.running(with_agent=False)
    scenario = sandbox.engine.scenario()
    scenario["containers"][_NAME]["processes"].append({"pid": 41, "args": "bash", "session": "pseudocron"})
    sandbox.engine.set_scenario(scenario)
    result = sandbox.down()

    assert result.returncode == 0, _diagnostic(result)
    assert _index_of_call(sandbox, lambda a: a[:3] == ["exec", _NAME, "kill"]) < _index_of_call(sandbox, _is_scheduler_stop)
    tmux_calls = sandbox.calls_named("exec tmux")
    assert [c[3] for c in tmux_calls] == ["has-session", "kill-session"], tmux_calls
    _assert_stopped_then_removed(sandbox)


# ---------------------------------------------------------------------------
# Wake drivers keep the container busy
# ---------------------------------------------------------------------------


def test_wake_driver_with_no_provider_pid_keeps_the_container_busy_until_it_exits(sandbox):
    # Listed by the scheduler stop's two process-table reads and the wait's
    # first three; the stop may only follow a read that no longer lists it.
    polls_listed = 5
    sandbox.running(with_agent=False, with_driver=True, driver_polls=polls_listed)
    result = sandbox.down("--progress-interval", "1")

    assert result.returncode == 0, _diagnostic(result)
    commands = sandbox.commands()
    reads_before_stop = commands[:commands.index("stop")].count("exec ps")
    assert reads_before_stop >= polls_listed + 1, commands
    progress = [line for line in result.stdout.splitlines() if "waiting for" in line]
    assert progress and all(_DRIVER_PROGRESS in line for line in progress), _diagnostic(result)
    assert "agent session(s) to exit" not in result.stdout, _diagnostic(result)
    assert _IDLE in result.stdout, _diagnostic(result)
    _assert_stopped_then_removed(sandbox)


def test_running_overwatch_sweep_keeps_the_container_busy_until_it_exits(sandbox):
    polls_listed = 5
    sandbox.running(with_agent=False, with_sweep=True, sweep_polls=polls_listed)
    result = sandbox.down("--progress-interval", "1")

    assert result.returncode == 0, _diagnostic(result)
    commands = sandbox.commands()
    reads_before_stop = commands[:commands.index("stop")].count("exec ps")
    assert reads_before_stop >= polls_listed + 1, commands
    progress = [line for line in result.stdout.splitlines() if "waiting for" in line]
    assert progress and all(_SWEEP_PROGRESS in line for line in progress), _diagnostic(result)
    assert _IDLE in result.stdout, _diagnostic(result)
    _assert_stopped_then_removed(sandbox)


def test_no_session_and_no_wake_driver_reads_idle_at_once(sandbox):
    sandbox.running(with_agent=False)
    result = sandbox.down("--progress-interval", "1")

    assert result.returncode == 0, _diagnostic(result)
    # "At once" is proven by behaviour, not by the elapsed seconds the idle
    # line reports: those come from two whole-second clock reads and may
    # read 1s when the first poll straddles a second boundary.
    assert "waiting for" not in result.stdout, _diagnostic(result)
    idle = [line for line in result.stdout.splitlines() if _IDLE in line]
    assert len(idle) == 1, _diagnostic(result)
    # The wait loop read the process table exactly once: the reads between
    # the scheduler stop and the container stop are all the wait's own.
    commands = sandbox.commands()
    scheduler_stop = _index_of_call(sandbox, _is_scheduler_stop)
    wait_reads = commands[scheduler_stop + 1:commands.index("stop")].count("exec ps")
    assert wait_reads == 1, f"the wait loop read the process table {wait_reads} times: {commands}"
    _assert_stopped_then_removed(sandbox)


def test_wake_driver_outliving_the_timeout_is_named_and_the_container_still_stops(sandbox):
    sandbox.write_cfg("[docker]\ncontainer_stop_timeout_seconds = 2\n")
    sandbox.running(with_agent=False, with_driver=True, driver_polls=None)
    started = time.monotonic()
    result = sandbox.down("--progress-interval", "1")
    elapsed = time.monotonic() - started

    assert result.returncode == 0, _diagnostic(result)
    assert elapsed < 20, f"the wait took {elapsed:.1f}s"
    timeout_lines = [line for line in result.stdout.splitlines() if "timeout:" in line]
    assert len(timeout_lines) == 1, _diagnostic(result)
    assert "1 wake driver process(es) (wake-batch.sh pid 60)" in timeout_lines[0], timeout_lines
    assert "after 2s" in timeout_lines[0], timeout_lines
    _assert_stopped_then_removed(sandbox)


def test_session_and_wake_driver_are_both_named_while_waiting(sandbox):
    sandbox.running(agent_polls=4, with_driver=True, driver_polls=4)
    result = sandbox.down("--progress-interval", "1")

    assert result.returncode == 0, _diagnostic(result)
    both = f"waiting for 1 agent session(s) to exit: {_TASK}; {_DRIVER_PROGRESS}"
    assert any(both in line for line in result.stdout.splitlines()), _diagnostic(result)
    assert _IDLE in result.stdout, _diagnostic(result)
    _assert_stopped_then_removed(sandbox)


# ---------------------------------------------------------------------------
# --now
# ---------------------------------------------------------------------------


def test_now_stops_and_removes_at_once_with_a_session_listed(sandbox):
    sandbox.running(agent_polls=None)
    started = time.monotonic()
    result = sandbox.down("--now")
    elapsed = time.monotonic() - started

    assert result.returncode == 0, _diagnostic(result)
    assert elapsed < 20, f"--now took {elapsed:.1f}s"
    commands = sandbox.commands()
    assert _index_of_call(sandbox, _is_scheduler_stop) < commands.index("stop"), commands
    _assert_stopped_then_removed(sandbox)
    assert _PROGRESS not in result.stdout, _diagnostic(result)
    assert "--now: stopping with 1 agent session(s) still running" in result.stdout, _diagnostic(result)
    assert _TASK in result.stdout


def test_now_names_a_wake_driver_it_ends(sandbox):
    sandbox.running(with_agent=False, with_driver=True, driver_polls=None)
    result = sandbox.down("--now")

    assert result.returncode == 0, _diagnostic(result)
    assert "--now: stopping with 1 wake driver process(es) still running: wake-batch.sh pid 60" in result.stdout, _diagnostic(result)
    assert "waiting for" not in result.stdout, _diagnostic(result)
    _assert_stopped_then_removed(sandbox)


# ---------------------------------------------------------------------------
# HALT is never touched
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("args", [("--progress-interval", "1"), ("--now",)], ids=["soft", "now"])
def test_existing_halt_file_is_left_in_place(sandbox, args):
    project_dir = sandbox.root / "projects" / "pgai-agent-kanban"
    project_dir.mkdir(parents=True)
    halts = {
        sandbox.root / "HALT": "operator pause before maintenance\n",
        project_dir / "HALT": "project pause\n",
    }
    for path, text in halts.items():
        path.write_text(text, encoding="utf-8")
    sandbox.running(agent_polls=2)
    result = sandbox.down(*args)

    assert result.returncode == 0, _diagnostic(result)
    for path, text in halts.items():
        assert path.read_text(encoding="utf-8") == text, path
    assert sorted(sandbox.root.rglob("HALT*")) == sorted(halts), sorted(sandbox.root.rglob("HALT*"))
    _assert_stopped_then_removed(sandbox)


@pytest.mark.parametrize("args", [("--progress-interval", "1"), ("--now",)], ids=["soft", "now"])
def test_no_halt_file_is_created(sandbox, args):
    sandbox.running(agent_polls=2)
    result = sandbox.down(*args)

    assert result.returncode == 0, _diagnostic(result)
    assert list(sandbox.root.rglob("HALT*")) == []
    _assert_stopped_then_removed(sandbox)


# ---------------------------------------------------------------------------
# Container state and name
# ---------------------------------------------------------------------------


def test_absent_container_is_reported_and_exits_zero(sandbox):
    result = sandbox.down()

    assert result.returncode == 0, _diagnostic(result)
    assert "does not exist" in result.stdout, _diagnostic(result)
    assert not {"stop", "rm", "exec tmux", "exec ps"} & set(sandbox.commands()), sandbox.commands()


def test_stopped_container_is_removed_without_a_stop(sandbox):
    sandbox.scenario(containers={_NAME: container(status="exited")})
    result = sandbox.down()

    assert result.returncode == 0, _diagnostic(result)
    assert sandbox.calls_named("stop") == []
    assert sandbox.calls_named("rm") == [["rm", _NAME]]
    assert not sandbox.container_exists()


def test_kanban_cfg_names_the_container_and_name_flag_overrides_it(sandbox):
    sandbox.write_cfg("[docker]\ncontainer_name = cfg-kanban\n")
    sandbox.running(with_agent=False, name="cfg-kanban")
    from_cfg = sandbox.down()
    assert from_cfg.returncode == 0, _diagnostic(from_cfg)
    _assert_stopped_then_removed(sandbox, name="cfg-kanban")

    sandbox.running(with_agent=False, name="flag-kanban")
    from_flag = sandbox.down("--name", "flag-kanban")
    assert from_flag.returncode == 0, _diagnostic(from_flag)
    assert ["stop", "flag-kanban"] in sandbox.engine.calls()
    assert not sandbox.container_exists("flag-kanban")


# ---------------------------------------------------------------------------
# Operator-only, usage and refusals
# ---------------------------------------------------------------------------


def test_agent_wake_prints_skipped_and_calls_no_engine(sandbox):
    sandbox.running(agent_polls=None)
    result = sandbox.down(wake=True)

    assert result.returncode == 0, _diagnostic(result)
    assert "SKIPPED" in result.stdout and _SKIP_KEY in result.stdout, _diagnostic(result)
    assert sandbox.engine.records() == []
    assert sandbox.container_exists()


def test_unknown_argument_is_a_usage_refusal(sandbox):
    result = sandbox.down("--bogus")

    assert result.returncode == 2, _diagnostic(result)
    assert _contract(result.stderr).get("error") == "container_down.usage"
    assert sandbox.engine.records() == []


def test_malformed_stop_timeout_is_refused_and_its_remedy_fixes_it(sandbox):
    sandbox.write_cfg("[docker]\ncontainer_name = pgai-kanban\ncontainer_stop_timeout_seconds = 30m\n")
    sandbox.running(agent_polls=None)
    refused = sandbox.down()

    assert refused.returncode == 2, _diagnostic(refused)
    fields = _contract(refused.stderr)
    assert fields.get("error") == "container_down.stop_timeout_invalid", _diagnostic(refused)
    assert "'30m'" in fields["message"]
    assert sandbox.engine.records() == []
    assert "<" not in fields["remedy"], fields["remedy"]

    applied = subprocess.run(
        ["bash", "-c", fields["remedy"]], capture_output=True, text=True,
        env=sandbox.env(), cwd=str(sandbox.tmp), timeout=30,
    )
    assert applied.returncode == 0, _diagnostic(applied)
    cfg = (sandbox.root / "kanban.cfg").read_text(encoding="utf-8")
    assert "container_stop_timeout_seconds = 1800" in cfg and "30m" not in cfg, cfg
    assert "container_name = pgai-kanban" in cfg, cfg


def test_failed_process_listing_refuses_without_stopping_the_container(sandbox):
    sandbox.running(agent_polls=None)
    sandbox.scenario(failures={"exec ps": {"exit": 126, "stderr": "OCI runtime exec failed: permission denied"}})
    result = sandbox.down()

    assert result.returncode == 1, _diagnostic(result)
    fields = _contract(result.stderr)
    assert fields.get("error") == "container_down.engine_failed", _diagnostic(result)
    assert "permission denied" in fields["message"]
    assert sandbox.calls_named("stop") == [] and sandbox.calls_named("rm") == [], sandbox.commands()
    assert sandbox.container_exists()
