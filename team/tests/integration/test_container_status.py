"""
test_container_status.py
========================
scripts/container-status.sh reports the kanban container in one block:

  * --json reports each state -- absent, stopped, running without a
    scheduler, running with one -- from the fake engine's output, exiting
    3, 3, 3 and 0;
  * the JSON carries the state, image, uptime, scheduler, last heartbeat
    age, agent sessions (role and task ID) and whether the host crontab
    carries kanban wake lines -- counted, never echoed;
  * the plain block prints each of those fields, and an ERROR line when the
    crontab carries wake lines while the container runs;
  * a failed engine state query, a failed process listing on a running
    container, or an engine that cannot be resolved exits 1, dual-channel;
  * it is read-only and not container-tier gated: inside an agent wake it
    still reports (no SKIPPED line), and it never stops, removes or runs a
    container.

No test reaches a real engine or the real crontab. Every subprocess runs
with an environment built from an explicit set
(tests.fixtures.scrubbed_subprocess_env), a throwaway kanban root and HOME,
and a fake engine (tests.fixtures.fake_container_engine) and a crontab stub
first on PATH. The non-wake runs carry no PGAI_AGENT_WAKE_ACTIVE, which
build_scrubbed_env never inherits.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

from tests.fixtures.fake_container_engine import (
    FakeEngine,
    agent_process,
    container,
    install_fake_crontab,
    scheduler_process,
)
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_STATUS_SH = _TEAM_DIR / "scripts" / "container-status.sh"
_CRONTAB_TEMPLATE = _TEAM_DIR / "templates" / "install" / "crontab-small.example"

_IMAGE = "pgai-kanban:rocky9"
_NAME = "pgai-kanban"
_TASK = "CODER-20261011-901-example-task"
_AGENT_PID = 77
_SCHEDULER_PID = 40
# A start time and a heartbeat safely in the past, so both ages are positive
# whatever the clock reads.
_STARTED_AT = "2026-01-01T00:00:00Z"
_FIRED_AT = "2026-01-01T00:05:00Z"
# A wake line carrying an inline environment assignment the report must not echo.
_CRON_SECRET = "EXAMPLE_TOKEN=not-a-real-value"


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


def _template_wake_line(root: pathlib.Path) -> str:
    """The first wake-batch.sh job line the shipped crontab template carries."""
    for line in _CRONTAB_TEMPLATE.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "scripts/wake-batch.sh" in line:
            return line.replace("__KANBAN_ROOT__", str(root))
    raise AssertionError(f"{_CRONTAB_TEMPLATE} carries no wake-batch.sh line")


class _Sandbox:
    """A throwaway kanban root, HOME and temp dir, a fake engine with no
    container until a test describes one, and a crontab stub (no crontab
    unless a test installs one) first on PATH."""

    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.tmp = tmp_path
        self.root = (tmp_path / "kanban_root").resolve()
        self.root.mkdir()
        self.home = (tmp_path / "home").resolve()
        self.home.mkdir()
        self.temp = tmp_path / "pgai_tmp"
        self.temp.mkdir()
        self.engine = FakeEngine(tmp_path / "engine", name="docker", scenario={"images": [_IMAGE]})
        self.cron_bin = tmp_path / "cron_bin"
        install_fake_crontab(self.cron_bin, None)
        # The interpreter running this test answers python3 inside the script.
        self.py_bin = tmp_path / "py_bin"
        self.py_bin.mkdir()
        (self.py_bin / "python3").symlink_to(pathlib.Path(sys.executable))

    # -- scenario builders ---------------------------------------------------

    def scenario(self, **changes) -> None:
        scenario = self.engine.scenario()
        scenario.update(changes)
        self.engine.set_scenario(scenario)

    def stopped(self) -> None:
        self.scenario(containers={_NAME: container(status="exited", image=_IMAGE, started_at=_STARTED_AT)})

    def running(self, *, scheduler: bool = True, agent: bool = False, name: str = _NAME) -> None:
        processes = []
        if scheduler:
            processes.append(scheduler_process(_SCHEDULER_PID, self.root))
        if agent:
            processes.append(agent_process(_AGENT_PID, _TASK))
            self.liveness_record(_TASK, _AGENT_PID)
        self.scenario(containers={name: container(image=_IMAGE, started_at=_STARTED_AT, processes=processes)})

    def liveness_record(self, task_id: str, pid: int) -> None:
        records = self.root / "locks" / "task-liveness"
        records.mkdir(parents=True, exist_ok=True)
        (records / f"{task_id}.record").write_text(
            f"holder=claude\npid={pid}\nop=dispatch\ntask={task_id}\n", encoding="utf-8"
        )

    def heartbeat(self) -> None:
        log = self.root / "logs" / "pseudocron.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            "pseudocron starting\n"
            f"{_FIRED_AT} fired (minute=5): scripts/wake-batch.sh --agent=pm\n",
            encoding="utf-8",
        )

    def crontab_with_wake_line(self) -> str:
        line = _template_wake_line(self.root)
        # An inline assignment before the command: a wake line all the same.
        fields = line.split(None, 5)
        line = " ".join(fields[:5] + [f"{_CRON_SECRET} {fields[5]}"])
        install_fake_crontab(self.cron_bin, "MAILTO=\"\"\n" + line + "\n")
        return line

    def write_cfg(self, text: str) -> None:
        (self.root / "kanban.cfg").write_text(text, encoding="utf-8")

    # -- running the script ----------------------------------------------------

    def env(self, *, wake: bool = False) -> dict:
        extra = {
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.temp),
        }
        if wake:
            extra["PGAI_AGENT_WAKE_ACTIVE"] = "1"
        env = build_scrubbed_env(
            home_dir=self.home,
            extra_path_dirs=(self.engine.bin_dir, self.cron_bin, self.py_bin),
            extra_env=extra,
        )
        if not wake:
            assert "PGAI_AGENT_WAKE_ACTIVE" not in env
        # Read-only: no run is ever authorized for the container tier.
        assert "PGAI_CONTAINER_TIER" not in env
        return env

    def status(self, *args: str, wake: bool = False) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(_STATUS_SH), *args],
            capture_output=True,
            text=True,
            env=self.env(wake=wake),
            cwd=str(self.tmp),
            timeout=60,
        )

    def status_json(self, *args: str, wake: bool = False) -> tuple[subprocess.CompletedProcess, dict]:
        result = self.status("--json", *args, wake=wake)
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"stdout is not one JSON object ({exc})\n{_diagnostic(result)}") from None
        return result, data

    def commands(self) -> list[str]:
        return self.engine.commands()


@pytest.fixture
def sandbox(tmp_path: pathlib.Path) -> _Sandbox:
    return _Sandbox(tmp_path)


def _assert_read_only(sb: _Sandbox) -> None:
    """Only state queries and a process listing reached the engine."""
    allowed = {"inspect", "exec ps"}
    assert set(sb.commands()) <= allowed, sb.commands()


# ---------------------------------------------------------------------------
# State and exit code
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "setup, expected_state, expected_exit, scheduler_running",
    [
        ("absent", "absent", 3, False),
        ("stopped", "stopped", 3, False),
        ("running-without-scheduler", "running", 3, False),
        ("running-with-scheduler", "running", 0, True),
    ],
)
def test_json_reports_each_state_with_its_exit_code(sandbox, setup, expected_state, expected_exit, scheduler_running):
    if setup == "stopped":
        sandbox.stopped()
    elif setup == "running-without-scheduler":
        sandbox.running(scheduler=False)
    elif setup == "running-with-scheduler":
        sandbox.running()
    result, data = sandbox.status_json()

    assert result.returncode == expected_exit, _diagnostic(result)
    assert data["container"]["name"] == _NAME
    assert data["container"]["state"] == expected_state, _diagnostic(result)
    assert data["scheduler"]["running"] is scheduler_running, _diagnostic(result)
    assert data["exit_code"] == expected_exit
    # A process listing is asked of a running container only.
    assert ("exec ps" in sandbox.commands()) is (expected_state == "running"), sandbox.commands()
    _assert_read_only(sandbox)


def test_json_carries_every_field(sandbox):
    sandbox.running(agent=True)
    sandbox.heartbeat()
    cron_line = sandbox.crontab_with_wake_line()
    result, data = sandbox.status_json()

    assert result.returncode == 0, _diagnostic(result)
    c = data["container"]
    assert c["state"] == "running" and c["image"] == _IMAGE, c
    assert isinstance(c["uptime_seconds"], int) and c["uptime_seconds"] > 0, c
    assert c["uptime"], c
    assert data["scheduler"] == {"running": True, "pids": [_SCHEDULER_PID]}
    hb = data["heartbeat"]
    assert hb["found"] is True and hb["last_fired_at"].startswith("2026-01-01T00:05:00"), hb
    assert isinstance(hb["age_seconds"], int) and hb["age_seconds"] > 0 and hb["age"], hb
    assert data["agent_sessions"] == [
        {"role": "CODER", "task_id": _TASK, "pid": _AGENT_PID, "provider": "claude"}
    ], data["agent_sessions"]
    assert data["host_crontab"] == {"wake_lines_present": True, "wake_line_count": 1}, data["host_crontab"]
    assert any("host crontab carries 1 kanban wake line" in e for e in data["errors"]), data["errors"]
    # Counted, never echoed.
    assert _CRON_SECRET not in result.stdout and cron_line not in result.stdout, result.stdout


def test_json_with_no_crontab_and_no_heartbeat_reports_their_absence(sandbox):
    sandbox.running()
    result, data = sandbox.status_json()

    assert result.returncode == 0, _diagnostic(result)
    assert data["host_crontab"] == {"wake_lines_present": False, "wake_line_count": 0}
    assert data["heartbeat"]["found"] is False and data["heartbeat"]["age_seconds"] is None
    assert data["agent_sessions"] == [] and data["errors"] == []


# ---------------------------------------------------------------------------
# The plain block
# ---------------------------------------------------------------------------


def test_plain_block_prints_every_field_and_an_error_line_for_cron_while_running(sandbox):
    sandbox.running(agent=True)
    sandbox.heartbeat()
    cron_line = sandbox.crontab_with_wake_line()
    result = sandbox.status()

    assert result.returncode == 0, _diagnostic(result)
    lines = result.stdout.splitlines()
    labels = [line.split(":", 1)[0].strip() for line in lines if not line.startswith(" ")]
    for label in ("container", "image", "uptime", "scheduler", "last heartbeat", "agent sessions", "host crontab"):
        assert label in labels, _diagnostic(result)
    by_label = {line.split(":", 1)[0].strip(): line.split(":", 1)[1].strip() for line in lines if ":" in line}
    assert by_label["container"] == f"{_NAME} (running)"
    assert by_label["image"] == _IMAGE
    assert by_label["uptime"] != "-"
    assert by_label["scheduler"] == f"running (pid {_SCHEDULER_PID})"
    assert by_label["last heartbeat"].endswith(" ago (2026-01-01T00:05:00+00:00)"), lines
    assert by_label["agent sessions"] == "1"
    assert any(line.split() == ["CODER", _TASK] for line in lines), _diagnostic(result)
    assert by_label["host crontab"] == "1 kanban wake line(s)"
    errors = [line for line in lines if line.startswith("ERROR:")]
    assert len(errors) == 1 and "host crontab carries 1 kanban wake line" in errors[0], _diagnostic(result)
    assert _CRON_SECRET not in result.stdout and cron_line not in result.stdout


def test_plain_block_has_no_error_line_for_cron_while_the_container_is_stopped(sandbox):
    sandbox.stopped()
    sandbox.crontab_with_wake_line()
    result = sandbox.status()

    assert result.returncode == 3, _diagnostic(result)
    assert f"{_NAME} (stopped)" in result.stdout
    assert "1 kanban wake line(s)" in result.stdout
    assert "ERROR:" not in result.stdout, _diagnostic(result)


def test_plain_block_for_an_absent_container(sandbox):
    result = sandbox.status()

    assert result.returncode == 3, _diagnostic(result)
    assert f"{_NAME} (absent)" in result.stdout, _diagnostic(result)
    assert "not running" in result.stdout and "no kanban wake lines" in result.stdout


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", [(), ("--json",)], ids=["text", "json"])
def test_failed_state_query_exits_one_dual_channel(sandbox, fmt):
    sandbox.running()
    sandbox.scenario(failures={"inspect": {"exit": 1, "stderr": "Cannot connect to the Docker daemon at unix:///var/run/docker.sock"}})
    result = sandbox.status(*fmt)

    assert result.returncode == 1, _diagnostic(result)
    fields = _contract(result.stderr)
    assert fields.get("error") == "container_status.engine_failed", _diagnostic(result)
    assert "Cannot connect to the Docker daemon" in fields["message"]
    assert fields["remedy"] == "docker info", fields
    assert result.stdout == "", _diagnostic(result)
    assert "exec ps" not in sandbox.commands()


def test_failed_process_listing_on_a_running_container_exits_one(sandbox):
    sandbox.running()
    sandbox.scenario(failures={"exec ps": {"exit": 126, "stderr": "OCI runtime exec failed: permission denied"}})
    result = sandbox.status("--json")

    assert result.returncode == 1, _diagnostic(result)
    fields = _contract(result.stderr)
    assert fields.get("error") == "container_status.engine_failed", _diagnostic(result)
    assert "permission denied" in fields["message"]
    assert "<" not in fields["remedy"] and f"exec {_NAME} ps" in fields["remedy"], fields


def test_unresolvable_engine_exits_one_dual_channel(sandbox):
    sandbox.write_cfg("[docker]\ndocker_exe = no-such-container-engine\n")
    sandbox.running()
    result = sandbox.status("--json")

    assert result.returncode == 1, _diagnostic(result)
    fields = _contract(result.stderr)
    assert fields.get("error") == "container_status.engine_not_found", _diagnostic(result)
    assert "no-such-container-engine" in fields["message"]
    assert "<" not in fields["remedy"], fields
    assert sandbox.engine.records() == []


def test_unknown_argument_is_a_usage_refusal_before_any_engine_call(sandbox):
    result = sandbox.status("--bogus")

    assert result.returncode == 2, _diagnostic(result)
    assert _contract(result.stderr).get("error") == "container_status.usage"
    assert sandbox.engine.records() == []


def test_help_prints_usage_and_exits_zero(sandbox):
    result = sandbox.status("--help")

    assert result.returncode == 0, _diagnostic(result)
    assert "Usage:" in result.stdout and "--json" in result.stdout and "--name" in result.stdout
    assert sandbox.engine.records() == []


# ---------------------------------------------------------------------------
# Read-only and ungated
# ---------------------------------------------------------------------------


def test_inside_an_agent_wake_it_still_reports(sandbox):
    sandbox.running(agent=True)
    result, data = sandbox.status_json(wake=True)

    assert result.returncode == 0, _diagnostic(result)
    assert "SKIPPED" not in result.stdout and "SKIPPED" not in result.stderr, _diagnostic(result)
    assert data["container"]["state"] == "running"
    assert [s["task_id"] for s in data["agent_sessions"]] == [_TASK]
    assert "inspect" in sandbox.commands()
    _assert_read_only(sandbox)


def test_docker_use_log_records_the_look(sandbox):
    sandbox.running()
    result = sandbox.status()

    assert result.returncode == 0, _diagnostic(result)
    use_log = (sandbox.root / "logs" / "docker-use.log").read_text(encoding="utf-8")
    assert "START actor=operator" in use_log and "script=container-status.sh" in use_log, use_log
    assert "FINISH" in use_log and "exit=0" in use_log, use_log


def test_kanban_cfg_names_the_container_and_name_flag_overrides_it(sandbox):
    sandbox.write_cfg("[docker]\ncontainer_name = cfg-kanban\n")
    sandbox.running(name="cfg-kanban")
    from_cfg, data = sandbox.status_json()
    assert from_cfg.returncode == 0, _diagnostic(from_cfg)
    assert data["container"]["name"] == "cfg-kanban"
    assert ["container", "inspect", "cfg-kanban"] in sandbox.engine.calls()

    from_flag, data = sandbox.status_json("--name", "flag-kanban")
    assert from_flag.returncode == 3, _diagnostic(from_flag)
    assert (data["container"]["name"], data["container"]["state"]) == ("flag-kanban", "absent")
    assert ["container", "inspect", "flag-kanban"] in sandbox.engine.calls()
