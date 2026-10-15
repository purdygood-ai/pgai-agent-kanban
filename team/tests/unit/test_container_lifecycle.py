"""
test_container_lifecycle.py
===========================
The container lifecycle module (pgai_agent_kanban.container.lifecycle) is the
one place the lifecycle scripts read the kanban container's state from:

  * container state -- absent / stopped / running, image and uptime -- from
    ``container inspect`` output, with "no such container" read as absent and
    any other engine failure refused;
  * the scheduler -- pseudocron processes in the container's process table,
    counting the scheduler itself and not the tmux / sh wrappers around it;
  * the heartbeat -- the last ``fired (minute=NN)`` line in
    logs/pseudocron.log; a banner-only log or no log has none;
  * host-crontab wake lines -- the shipped templates' wake-batch.sh lines,
    and nothing that is a comment, an assignment or another job;
  * agent sessions -- liveness records whose pid runs in the container and
    whose command line names the record's task or provider;
  * wake drivers -- wake-batch.sh, wake.sh and wake-now.sh processes in the
    container's table, which keep the container busy with no liveness
    record at all.

The contract is asserted at the module's functions; the command-line verbs
the bash scripts call are asserted for their exit codes and output shape.
The engine-output cases run the fake engine (tests/fixtures/
fake_container_engine.py) through the real pgai_docker helper, with the fake
first on a PATH this test builds, so no real container engine is ever
reached.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import signal
import subprocess
import sys

import pytest

from pgai_agent_kanban.container import lifecycle
from pgai_agent_kanban.container.lifecycle import (
    STATE_ABSENT,
    STATE_RUNNING,
    STATE_STOPPED,
    ContainerQueryError,
    Process,
    build_status_report,
    classify_query_failure,
    crontab_wake_lines,
    list_agent_sessions,
    parse_container_state,
    parse_heartbeat,
    parse_process_table,
    read_heartbeat,
    render_status_text,
    overwatch_sweep_processes,
    scheduler_processes,
    wake_driver_processes,
)
from tests.fixtures.fake_container_engine import (
    FakeEngine,
    agent_process,
    container,
    install_fake_crontab,
    scheduler_process,
    wake_driver_process,
)
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_DOCKER_USE = _TEAM_DIR / "scripts" / "lib" / "docker_use.sh"
_TEMPLATES = _TEAM_DIR / "templates" / "install"
_NAME = "pgai-kanban"
_IMAGE = "pgai-kanban:latest"
_NOW = datetime.datetime(2026, 10, 11, 8, 0, 0, tzinfo=datetime.timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"--- exit {result.returncode} ---\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _run(argv: list[str], *, env: dict, cwd: pathlib.Path, input_text: str = "") -> subprocess.CompletedProcess:
    """Run *argv* in its own process group; a timeout kills the whole group."""
    proc = subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env, cwd=str(cwd), start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(input=input_text, timeout=60)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.communicate()
        raise
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)


@pytest.fixture()
def kanban_root(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "kanban"
    (root / "logs").mkdir(parents=True)
    (root / "locks" / "task-liveness").mkdir(parents=True)
    return root


def _env(tmp_path: pathlib.Path, kanban_root: pathlib.Path, *path_dirs: pathlib.Path) -> dict:
    """A rebuilt environment: the fake engine first on PATH, the throwaway
    kanban root (where docker_use.sh writes its log), the package importable."""
    return build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_path_dirs=path_dirs,
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
            "KANBAN_ROOT": str(kanban_root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "tmp"),
            "PYTHONPATH": str(_TEAM_DIR),
        },
    )


def _engine_call(engine: FakeEngine, tmp_path: pathlib.Path, kanban_root: pathlib.Path, *engine_args: str) -> subprocess.CompletedProcess:
    """Run one engine command through the real pgai_docker helper."""
    script = f'source "{_DOCKER_USE}"; pgai_docker {engine.name} "$@"'
    return _run(
        ["bash", "-c", script, "engine-call", *engine_args],
        env=_env(tmp_path, kanban_root, engine.bin_dir),
        cwd=tmp_path,
    )


def _lifecycle_cli(tmp_path: pathlib.Path, kanban_root: pathlib.Path, *args: str, input_text: str = "") -> subprocess.CompletedProcess:
    return _run(
        [sys.executable, "-m", "pgai_agent_kanban.container.lifecycle", *args],
        env=_env(tmp_path, kanban_root),
        cwd=tmp_path,
        input_text=input_text,
    )


def _write_record(kanban_root: pathlib.Path, task_id: str, pid: int, holder: str = "claude") -> None:
    path = kanban_root / "locks" / "task-liveness" / f"{task_id}.record"
    path.write_text(f"holder={holder}\npid={pid}\nop=dispatch\ntask={task_id}\n", encoding="utf-8")


def _template_crontab(name: str) -> str:
    text = (_TEMPLATES / name).read_text(encoding="utf-8")
    return text.replace("__KANBAN_ROOT__", "/srv/kanban").replace("__CLAUDE_PATH__", "/usr/local/bin")


# ---------------------------------------------------------------------------
# Container state, from fake engine output
# ---------------------------------------------------------------------------


def _inspect_via_fake(tmp_path, kanban_root, scenario) -> subprocess.CompletedProcess:
    engine = FakeEngine(tmp_path / "engine", scenario=scenario)
    return _engine_call(engine, tmp_path, kanban_root, "container", "inspect", _NAME)


def test_absent_container_from_fake_engine_reads_absent(tmp_path, kanban_root):
    result = _inspect_via_fake(tmp_path, kanban_root, {"images": [_IMAGE]})
    assert result.returncode != 0, _diagnostic(result)
    assert classify_query_failure(result.stderr, result.returncode) == STATE_ABSENT, _diagnostic(result)
    state = parse_container_state(result.stdout, name=_NAME, now=_NOW)
    assert state.state == STATE_ABSENT, _diagnostic(result)
    assert state.image is None and state.uptime_seconds is None


def test_stopped_container_from_fake_engine_reads_stopped_with_image_and_no_uptime(tmp_path, kanban_root):
    scenario = {"images": [_IMAGE], "containers": {_NAME: container(status="exited", image="pgai-kanban:rocky9")}}
    result = _inspect_via_fake(tmp_path, kanban_root, scenario)
    assert result.returncode == 0, _diagnostic(result)
    state = parse_container_state(result.stdout, name=_NAME, now=_NOW)
    assert state.state == STATE_STOPPED
    assert state.image == "pgai-kanban:rocky9"
    assert state.uptime_seconds is None, "a stopped container has no uptime"


def test_running_container_from_fake_engine_reads_image_and_uptime(tmp_path, kanban_root):
    scenario = {"images": [_IMAGE], "containers": {_NAME: container(started_at="2026-10-11T06:58:30Z")}}
    result = _inspect_via_fake(tmp_path, kanban_root, scenario)
    assert result.returncode == 0, _diagnostic(result)
    state = parse_container_state(result.stdout, name=_NAME, now=_NOW)
    assert state.state == STATE_RUNNING and state.running
    assert state.image == _IMAGE
    assert state.uptime_seconds == 3690  # 06:58:30 -> 08:00:00
    assert state.to_dict()["uptime"] == "1h 1m"


def test_engine_nanosecond_start_time_and_podman_image_field_are_read():
    podman_like = json.dumps([{
        "Name": _NAME,
        "ImageName": "localhost/pgai-kanban:latest",
        "State": {"Status": "running", "Running": True, "StartedAt": "2026-10-11T07:59:00.123456789+00:00"},
    }])
    state = parse_container_state(podman_like, now=_NOW)
    assert state.name == _NAME
    assert state.image == "localhost/pgai-kanban:latest"
    assert state.uptime_seconds == 59


@pytest.mark.parametrize("status", ["exited", "created", "paused", "restarting", "dead", "stopped", "configured"])
def test_every_non_running_engine_status_reads_stopped(status):
    text = json.dumps([{"State": {"Status": status}, "Config": {"Image": _IMAGE}}])
    assert parse_container_state(text, now=_NOW).state == STATE_STOPPED


def test_empty_inspect_output_reads_absent():
    assert parse_container_state("", name=_NAME).state == STATE_ABSENT
    assert parse_container_state("[]\n", name=_NAME).state == STATE_ABSENT


def test_engine_failure_other_than_no_such_container_is_refused():
    with pytest.raises(ContainerQueryError, match="Cannot connect to the Docker daemon"):
        classify_query_failure("Cannot connect to the Docker daemon at unix:///var/run/docker.sock.\n", 1)
    with pytest.raises(ContainerQueryError, match="not inspect JSON"):
        parse_container_state("permission denied")


def test_container_state_verb_exit_codes_follow_the_state(tmp_path, kanban_root):
    running = json.dumps([{"State": {"Status": "running", "StartedAt": "2026-10-11T07:00:00Z"}, "Config": {"Image": _IMAGE}}])
    result = _lifecycle_cli(tmp_path, kanban_root, "container-state", "--name", _NAME, "--now", "2026-10-11T08:00:00Z", input_text=running)
    assert result.returncode == 0, _diagnostic(result)
    assert "state=running" in result.stdout.splitlines(), _diagnostic(result)
    assert "uptime_seconds=3600" in result.stdout.splitlines(), _diagnostic(result)

    result = _lifecycle_cli(tmp_path, kanban_root, "container-state", "--name", _NAME, input_text="[]")
    assert result.returncode == 3, _diagnostic(result)
    assert "state=absent" in result.stdout.splitlines(), _diagnostic(result)

    stderr_file = tmp_path / "inspect.err"
    stderr_file.write_text("Error response from daemon: dial unix docker.sock: connect: permission denied\n", encoding="utf-8")
    result = _lifecycle_cli(tmp_path, kanban_root, "container-state", "--exit-code", "1", "--stderr-file", str(stderr_file))
    assert result.returncode == 1, _diagnostic(result)
    assert "permission denied" in result.stderr, _diagnostic(result)


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


def test_one_scheduler_counts_once_despite_its_tmux_and_shell_wrappers():
    table = parse_process_table(
        "      1 bash -l\n"
        "     20 tmux new -d -s pseudocron python3 /k/scripts/pseudocron.py >> /k/logs/pseudocron.log 2>&1\n"
        "     21 sh -c python3 /k/scripts/pseudocron.py >> /k/logs/pseudocron.log 2>&1\n"
        "     22 python3 /k/scripts/pseudocron.py\n"
    )
    assert [p.pid for p in scheduler_processes(table)] == [22]


def test_two_schedulers_are_both_counted_and_no_scheduler_is_none():
    table = [Process(30, "/usr/bin/python3.12 -u /k/scripts/pseudocron.py"), Process(31, "/k/scripts/pseudocron.py")]
    assert [p.pid for p in scheduler_processes(table)] == [30, 31]
    assert scheduler_processes([Process(1, "bash -l"), Process(9, "tail -f /k/logs/pseudocron.log")]) == []


def test_process_table_skips_header_and_noise_lines():
    table = parse_process_table("    PID COMMAND\n      7 sleep 60\n\nps: warning\n")
    assert table == [Process(7, "sleep 60")]


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

_BANNER = (
    "pseudocron starting: 60 jobs loaded from /k/pseudocron.cfg\n"
    "pseudocron starting: 4 vars loaded from /k/pseudocron.env\n"
)


def test_fired_lines_yield_the_last_heartbeat_age(kanban_root):
    log = kanban_root / "logs" / "pseudocron.log"
    log.write_text(
        _BANNER
        + "2026-10-11T07:55:00Z fired (minute=55): scripts/wake-batch.sh --agent=pm --sleep=0 >> logs/cron-pm.log 2>&1\n"
        + "2026-10-11T07:58:00Z fired (minute=58): scripts/wake-batch.sh --agent=coder --sleep=0 >> logs/cron-coder.log 2>&1\n"
        + "ERROR 2026-10-11T07:59:00Z spawn failed (minute=59): scripts/x.sh (OSError: boom)\n",
        encoding="utf-8",
    )
    heartbeat = read_heartbeat(log, now=_NOW)
    assert heartbeat.found
    assert heartbeat.last_fired_at == datetime.datetime(2026, 10, 11, 7, 58, tzinfo=datetime.timezone.utc)
    assert heartbeat.age_seconds == 120


def test_banner_only_log_has_no_heartbeat(kanban_root):
    log = kanban_root / "logs" / "pseudocron.log"
    log.write_text(_BANNER, encoding="utf-8")
    heartbeat = read_heartbeat(log, now=_NOW)
    assert not heartbeat.found and heartbeat.age_seconds is None


def test_missing_log_has_no_heartbeat(kanban_root):
    heartbeat = read_heartbeat(kanban_root / "logs" / "pseudocron.log", now=_NOW)
    assert not heartbeat.found and heartbeat.last_fired_at is None


def test_heartbeat_before_since_does_not_count():
    text = _BANNER + "2026-10-11T07:58:00Z fired (minute=58): scripts/wake-batch.sh\n"
    since = datetime.datetime(2026, 10, 11, 7, 59, tzinfo=datetime.timezone.utc)
    assert not parse_heartbeat(text, now=_NOW, since=since).found
    assert parse_heartbeat(text, now=_NOW, since=since - datetime.timedelta(minutes=5)).found


def test_backward_scan_finds_a_fired_line_split_across_read_chunks(kanban_root, monkeypatch):
    log = kanban_root / "logs" / "pseudocron.log"
    log.write_text(
        _BANNER
        + "".join(f"2026-10-11T07:{m:02d}:00Z fired (minute={m:02d}): scripts/wake-batch.sh --agent=pm\n" for m in range(40, 50))
        + "pseudocron shutting down (signal 15)\n" + _BANNER,
        encoding="utf-8",
    )
    # Chunks far smaller than one line force every line across a boundary.
    monkeypatch.setattr(lifecycle, "_TAIL_CHUNK_BYTES", 7)
    heartbeat = read_heartbeat(log, now=_NOW)
    assert heartbeat.last_fired_at == datetime.datetime(2026, 10, 11, 7, 49, tzinfo=datetime.timezone.utc)


def test_heartbeat_verb_reads_the_kanban_root_log(tmp_path, kanban_root):
    (kanban_root / "logs" / "pseudocron.log").write_text(
        _BANNER + "2026-10-11T07:59:30Z fired (minute=59): scripts/wake-batch.sh\n", encoding="utf-8"
    )
    result = _lifecycle_cli(tmp_path, kanban_root, "heartbeat", "--kanban-root", str(kanban_root), "--now", "2026-10-11T08:00:00Z")
    assert result.returncode == 0, _diagnostic(result)
    assert "age_seconds=30" in result.stdout.splitlines(), _diagnostic(result)

    result = _lifecycle_cli(tmp_path, kanban_root, "heartbeat", "--kanban-root", str(kanban_root), "--since", "2026-10-11T08:00:00Z")
    assert result.returncode == 3, _diagnostic(result)
    assert "heartbeat=no" in result.stdout.splitlines(), _diagnostic(result)


def test_heartbeat_verb_defaults_to_the_environment_kanban_root(tmp_path, kanban_root):
    (kanban_root / "logs" / "pseudocron.log").write_text(
        _BANNER + "2026-10-11T07:59:30Z fired (minute=59): scripts/wake-batch.sh\n", encoding="utf-8"
    )
    result = _lifecycle_cli(tmp_path, kanban_root, "heartbeat", "--now", "2026-10-11T08:00:00Z")
    assert result.returncode == 0, _diagnostic(result)
    assert "age_seconds=30" in result.stdout.splitlines(), _diagnostic(result)


@pytest.mark.parametrize("unset_value", [None, ""])
def test_root_needing_verbs_without_a_kanban_root_are_usage_errors(tmp_path, kanban_root, unset_value):
    env = _env(tmp_path, kanban_root)
    del env["PGAI_AGENT_KANBAN_ROOT_PATH"]
    if unset_value is not None:
        env["PGAI_AGENT_KANBAN_ROOT_PATH"] = unset_value
    for verb, message in (
        ("heartbeat", "heartbeat needs --log or --kanban-root (or PGAI_AGENT_KANBAN_ROOT_PATH set)"),
        ("sessions", "sessions needs --kanban-root (or PGAI_AGENT_KANBAN_ROOT_PATH set)"),
    ):
        result = _run(
            [sys.executable, "-m", "pgai_agent_kanban.container.lifecycle", verb],
            env=env, cwd=tmp_path,
        )
        assert result.returncode == 2, _diagnostic(result)
        assert message in result.stderr, _diagnostic(result)
        assert "Traceback" not in result.stderr, _diagnostic(result)


# ---------------------------------------------------------------------------
# Host crontab
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "template",
    ["crontab.example", "crontab-small.example", "crontab-medium.example", "crontab-large.example"],
)
def test_shipped_template_wake_lines_are_detected(template):
    text = _template_crontab(template)
    expected = [
        line.strip() for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "/scripts/wake-batch.sh " in line
    ]
    assert expected, f"{template} carries no wake-batch.sh job line to detect"
    assert crontab_wake_lines(text) == expected


def test_empty_crontab_has_no_wake_lines():
    assert crontab_wake_lines("") == []
    assert crontab_wake_lines("\n\n") == []


def test_comments_assignments_and_other_jobs_are_not_wake_lines():
    text = (
        "# pgai-agent-kanban crontab\n"
        "# */5 * * * * /srv/kanban/scripts/wake-batch.sh --agent=pm --sleep=0\n"
        "   #3-59/5 * * * * /srv/kanban/scripts/wake-batch.sh --agent=coder\n"
        "PGAI_AGENT_KANBAN_ROOT_PATH=/srv/kanban\n"
        "WAKE=/srv/kanban/scripts/wake-batch.sh\n"
        "0 4 * * 0 /srv/kanban/scripts/cleanup/cleanup.sh >> /srv/kanban/logs/cron-cleanup.log 2>&1\n"
        "30 * * * * /srv/kanban/scripts/overwatch-sweep.sh >> /srv/kanban/logs/cron-overwatch-sweep.log 2>&1\n"
        "15 2 * * * /usr/local/bin/backup.sh --wake-batch.sh-style-flag\n"
        "@daily /home/op/bin/rotate-logs\n"
    )
    assert crontab_wake_lines(text) == []


def test_wake_line_forms_beyond_the_template_are_detected():
    text = (
        "@reboot /srv/kanban/scripts/wake-batch.sh --agent=pm\n"
        "*/2 * * * * cd /srv/kanban && scripts/wake.sh --agent=cm\n"
        "*/2 * * * * bash /srv/kanban/scripts/wake-now.sh\n"
    )
    assert len(crontab_wake_lines(text)) == 3


def test_crontab_verb_prints_wake_lines_and_exits_zero_only_when_present(tmp_path, kanban_root):
    result = _lifecycle_cli(tmp_path, kanban_root, "crontab-wake-lines", input_text=_template_crontab("crontab-small.example"))
    assert result.returncode == 0, _diagnostic(result)
    assert all("/scripts/wake-batch.sh " in line for line in result.stdout.splitlines()), _diagnostic(result)

    result = _lifecycle_cli(tmp_path, kanban_root, "crontab-wake-lines", input_text="# nothing here\n")
    assert result.returncode == 3, _diagnostic(result)
    assert result.stdout == "", _diagnostic(result)


def test_fake_crontab_stub_lists_its_text_and_refuses_changes(tmp_path, kanban_root):
    bin_dir = tmp_path / "cronbin"
    install_fake_crontab(bin_dir, _template_crontab("crontab-small.example"))
    env = _env(tmp_path, kanban_root, bin_dir)
    listed = _run(["crontab", "-l"], env=env, cwd=tmp_path)
    assert listed.returncode == 0, _diagnostic(listed)
    assert crontab_wake_lines(listed.stdout), _diagnostic(listed)
    removed = _run(["crontab", "-r"], env=env, cwd=tmp_path)
    assert removed.returncode == 1, _diagnostic(removed)
    assert (bin_dir / "crontab-calls.log").read_text(encoding="utf-8").splitlines() == ["-l", "-r"]

    install_fake_crontab(bin_dir, None)
    none = _run(["crontab", "-l"], env=env, cwd=tmp_path)
    assert none.returncode == 1 and "no crontab for" in none.stderr, _diagnostic(none)


# ---------------------------------------------------------------------------
# Agent sessions
# ---------------------------------------------------------------------------

_TASK = "CODER-20261011-001-add-widget"


def test_no_records_means_no_sessions(kanban_root):
    assert list_agent_sessions(kanban_root, [Process(1, "bash -l"), Process(50, "claude -p x")]) == []


def test_one_running_session_reports_role_and_task(kanban_root):
    _write_record(kanban_root, _TASK, 77)
    sessions = list_agent_sessions(kanban_root, parse_process_table(f"  1 bash -l\n 77 claude -p process the kanban task at /k/tasks/{_TASK}\n"))
    assert len(sessions) == 1
    assert (sessions[0].role, sessions[0].task_id, sessions[0].pid, sessions[0].provider) == ("CODER", _TASK, 77, "claude")


def test_records_whose_process_is_gone_or_unrelated_are_not_sessions(kanban_root):
    _write_record(kanban_root, _TASK, 77)                         # pid no longer in the table
    _write_record(kanban_root, "WRITER-20261011-002-docs", 88)    # pid reused by an unrelated process
    (kanban_root / "locks" / "task-liveness" / "TESTER-20261011-003-x.record").write_text("holder=claude\npid=\n", encoding="utf-8")
    table = [Process(1, "bash -l"), Process(88, "sleep 30")]
    assert list_agent_sessions(kanban_root, table) == []


def test_session_matches_on_provider_when_command_line_omits_the_task(kanban_root):
    _write_record(kanban_root, "WRITER-20261011-002-docs", 91, holder="codex")
    sessions = list_agent_sessions(kanban_root, [Process(91, "/usr/local/bin/codex exec --full-auto")])
    assert [(s.role, s.task_id) for s in sessions] == [("WRITER", "WRITER-20261011-002-docs")]


def test_sessions_verb_lists_role_and_task(tmp_path, kanban_root):
    result = _lifecycle_cli(tmp_path, kanban_root, "sessions", "--kanban-root", str(kanban_root), input_text="  1 bash -l\n")
    assert result.returncode == 3, _diagnostic(result)
    assert result.stdout == "", _diagnostic(result)

    _write_record(kanban_root, _TASK, 77)
    result = _lifecycle_cli(
        tmp_path, kanban_root, "sessions", "--kanban-root", str(kanban_root),
        input_text=f"  1 bash -l\n 77 claude -p task {_TASK}\n",
    )
    assert result.returncode == 0, _diagnostic(result)
    assert result.stdout.splitlines() == [f"role=CODER task={_TASK} pid=77 provider=claude"], _diagnostic(result)


# ---------------------------------------------------------------------------
# Wake drivers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args, script",
    [
        ("bash /k/scripts/wake-batch.sh --agent=coder --sleep=0", "wake-batch.sh"),
        ("/bin/bash /k/scripts/wake.sh --agent=pm", "wake.sh"),
        ("/k/scripts/wake-now.sh --all", "wake-now.sh"),
        ("bash -c scripts/wake-batch.sh --agent=pm --sleep=0", "wake-batch.sh"),
        ("sh scripts/wake.sh", "wake.sh"),
    ],
    ids=["shebang-batch", "absolute-shell", "direct", "bash-c", "sh"],
)
def test_wake_script_processes_are_drivers(args, script):
    assert wake_driver_processes([Process(1, "bash -l"), Process(50, args)]) == [
        lifecycle.WakeDriver(pid=50, script=script)
    ]


@pytest.mark.parametrize(
    "args",
    [
        "bash -l",
        "python3 /k/scripts/pseudocron.py",
        "tail -f /k/logs/wake.sh.log",
        "vim /k/scripts/wake-batch.sh",
        "bash /k/scripts/wake-batch-helper.sh",
        "claude -p process the kanban task via scripts/wake.sh",
    ],
)
def test_processes_that_only_mention_a_wake_script_are_not_drivers(args):
    assert wake_driver_processes([Process(60, args)]) == []


def test_a_batch_and_the_wake_it_started_count_as_two_drivers_in_pid_order():
    table = parse_process_table(
        "  1 bash -l\n 72 bash /k/scripts/wake.sh --agent=coder\n 70 bash /k/scripts/wake-batch.sh --agent=coder\n"
    )
    assert [(d.pid, d.script) for d in wake_driver_processes(table)] == [(70, "wake-batch.sh"), (72, "wake.sh")]


def test_sessions_verb_reports_busy_for_a_wake_driver_with_no_provider_pid(tmp_path, kanban_root):
    """A wake-batch.sh process and no liveness record: the container is busy."""
    table = f"  1 bash -l\n 40 python3 {kanban_root}/scripts/pseudocron.py\n 50 bash {kanban_root}/scripts/wake-batch.sh --agent=coder\n"
    result = _lifecycle_cli(tmp_path, kanban_root, "sessions", "--kanban-root", str(kanban_root), input_text=table)
    assert result.returncode == 0, _diagnostic(result)
    assert result.stdout.splitlines() == ["driver=wake-batch.sh pid=50"], _diagnostic(result)

    as_json = _lifecycle_cli(
        tmp_path, kanban_root, "sessions", "--kanban-root", str(kanban_root), "--format", "json", input_text=table,
    )
    assert as_json.returncode == 0, _diagnostic(as_json)
    assert json.loads(as_json.stdout) == {
        "sessions": [],
        "wake_drivers": [{"pid": 50, "script": "wake-batch.sh"}],
        "overwatch_sweeps": [],
    }


def test_sessions_verb_reports_idle_with_no_session_and_no_wake_driver(tmp_path, kanban_root):
    table = f"  1 bash -l\n 40 python3 {kanban_root}/scripts/pseudocron.py\n 61 tail -f {kanban_root}/logs/wake.sh.log\n"
    result = _lifecycle_cli(tmp_path, kanban_root, "sessions", "--kanban-root", str(kanban_root), input_text=table)
    assert result.returncode == 3, _diagnostic(result)
    assert result.stdout == "", _diagnostic(result)


def test_sessions_verb_lists_sessions_before_drivers(tmp_path, kanban_root):
    _write_record(kanban_root, _TASK, 77)
    table = f"  1 bash -l\n 70 bash {kanban_root}/scripts/wake-batch.sh\n 77 claude -p task {_TASK}\n"
    result = _lifecycle_cli(tmp_path, kanban_root, "sessions", "--kanban-root", str(kanban_root), input_text=table)
    assert result.returncode == 0, _diagnostic(result)
    assert result.stdout.splitlines() == [
        f"role=CODER task={_TASK} pid=77 provider=claude",
        "driver=wake-batch.sh pid=70",
    ], _diagnostic(result)


# ---------------------------------------------------------------------------
# OVERWATCH sweeps
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        "bash /k/scripts/overwatch-sweep.sh --only block-triage --lock-wait 60",
        "/k/scripts/overwatch-sweep.sh",
    ],
    ids=["nudge-shebang", "direct"],
)
def test_overwatch_sweep_processes_are_found(args):
    table = [Process(1, "bash -l"), Process(50, args), Process(51, "tail -f /k/logs/overwatch-trigger.log")]
    assert overwatch_sweep_processes(table) == [lifecycle.OverwatchSweep(pid=50, script="overwatch-sweep.sh")]
    assert wake_driver_processes(table) == []


def test_processes_that_only_mention_the_sweep_are_not_sweeps():
    table = [Process(60, "vim /k/scripts/overwatch-sweep.sh"), Process(61, "bash /k/scripts/overwatch-sweep-helper.sh")]
    assert overwatch_sweep_processes(table) == []


def test_sessions_verb_reports_busy_for_a_running_overwatch_sweep(tmp_path, kanban_root):
    """A detached sweep and nothing else: the container is busy."""
    table = f"  1 bash -l\n 40 python3 {kanban_root}/scripts/pseudocron.py\n 90 bash {kanban_root}/scripts/overwatch-sweep.sh --only block-triage --lock-wait 60\n"
    result = _lifecycle_cli(tmp_path, kanban_root, "sessions", "--kanban-root", str(kanban_root), input_text=table)
    assert result.returncode == 0, _diagnostic(result)
    assert result.stdout.splitlines() == ["sweep=overwatch-sweep.sh pid=90"], _diagnostic(result)

    as_json = _lifecycle_cli(
        tmp_path, kanban_root, "sessions", "--kanban-root", str(kanban_root), "--format", "json", input_text=table,
    )
    assert json.loads(as_json.stdout) == {
        "sessions": [],
        "wake_drivers": [],
        "overwatch_sweeps": [{"pid": 90, "script": "overwatch-sweep.sh"}],
    }


def test_fake_engine_wake_driver_is_read_as_a_driver(kanban_root):
    proc = wake_driver_process(50, kanban_root, script="wake-now.sh", args="--all")
    assert wake_driver_processes([Process(proc["pid"], proc["args"])]) == [lifecycle.WakeDriver(50, "wake-now.sh")]


# ---------------------------------------------------------------------------
# The fake engine itself, through pgai_docker
# ---------------------------------------------------------------------------


def test_fake_engine_logs_each_invocation_and_answers_from_its_scenario(tmp_path, kanban_root):
    engine = FakeEngine(tmp_path / "engine", scenario={
        "images": [_IMAGE],
        "containers": {_NAME: container(processes=[scheduler_process(40, kanban_root)])},
    })
    result = _engine_call(engine, tmp_path, kanban_root, "exec", _NAME, "ps", "-eo", "pid=,args=")
    assert result.returncode == 0, _diagnostic(result)
    assert [p.pid for p in scheduler_processes(parse_process_table(result.stdout))] == [40], _diagnostic(result)

    missing = _engine_call(engine, tmp_path, kanban_root, "image", "inspect", "pgai-kanban:nosuch")
    assert missing.returncode == 1 and "No such image" in missing.stderr, _diagnostic(missing)

    assert engine.calls() == [
        ["exec", _NAME, "ps", "-eo", "pid=,args="],
        ["image", "inspect", "pgai-kanban:nosuch"],
    ]
    assert engine.commands() == ["exec ps", "image inspect"]


def test_fake_engine_keeps_state_across_run_scheduler_start_stop_and_rm(tmp_path, kanban_root):
    log = kanban_root / "logs" / "pseudocron.log"
    engine = FakeEngine(tmp_path / "engine", scenario={
        "images": [_IMAGE],
        "scheduler": {"log": str(log), "fires": True},
    })
    run = _engine_call(engine, tmp_path, kanban_root, "run", "-d", "--name", _NAME, "-v", "/a:/a", _IMAGE)
    assert run.returncode == 0, _diagnostic(run)
    inspect = _engine_call(engine, tmp_path, kanban_root, "container", "inspect", _NAME)
    assert parse_container_state(inspect.stdout).state == STATE_RUNNING, _diagnostic(inspect)

    start = _engine_call(
        engine, tmp_path, kanban_root, "exec", _NAME, "tmux", "new", "-d", "-s", "pseudocron",
        f"python3 {kanban_root}/scripts/pseudocron.py >> {log} 2>&1",
    )
    assert start.returncode == 0, _diagnostic(start)
    assert read_heartbeat(log).found, "a scheduler start with fires=true writes a heartbeat"
    second = _engine_call(engine, tmp_path, kanban_root, "exec", _NAME, "tmux", "new", "-d", "-s", "pseudocron", "python3 x/pseudocron.py")
    assert second.returncode == 1, _diagnostic(second)

    assert _engine_call(engine, tmp_path, kanban_root, "rm", _NAME).returncode == 1, "a running container is not removed without -f"
    assert _engine_call(engine, tmp_path, kanban_root, "stop", _NAME).returncode == 0
    assert _engine_call(engine, tmp_path, kanban_root, "rm", _NAME).returncode == 0
    gone = _engine_call(engine, tmp_path, kanban_root, "container", "inspect", _NAME)
    assert classify_query_failure(gone.stderr, gone.returncode) == STATE_ABSENT, _diagnostic(gone)

    docker_use = (kanban_root / "logs" / "docker-use.log").read_text(encoding="utf-8")
    assert "command=docker_run" in docker_use and "command=docker_rm" in docker_use


def test_fake_agent_session_leaves_after_its_polls(tmp_path, kanban_root):
    _write_record(kanban_root, _TASK, 77)
    engine = FakeEngine(tmp_path / "engine", scenario={
        "containers": {_NAME: container(processes=[agent_process(77, _TASK, exits_after_polls=2)])},
    })
    seen = []
    for _ in range(3):
        result = _engine_call(engine, tmp_path, kanban_root, "exec", _NAME, "ps", "-eo", "pid=,args=")
        assert result.returncode == 0, _diagnostic(result)
        seen.append([s.task_id for s in list_agent_sessions(kanban_root, parse_process_table(result.stdout))])
    assert seen == [[_TASK], [_TASK], []]


def test_fake_engine_forced_failure_answers_before_doing_anything(tmp_path, kanban_root):
    engine = FakeEngine(tmp_path / "engine", scenario={
        "containers": {_NAME: container()},
        "failures": {"inspect": {"exit": 1, "stderr": "Cannot connect to the Docker daemon"}},
    })
    result = _engine_call(engine, tmp_path, kanban_root, "container", "inspect", _NAME)
    assert result.returncode == 1, _diagnostic(result)
    with pytest.raises(ContainerQueryError):
        classify_query_failure(result.stderr, result.returncode)


# ---------------------------------------------------------------------------
# Status report
# ---------------------------------------------------------------------------


def test_status_report_running_with_scheduler_session_and_cron_conflict(kanban_root):
    (kanban_root / "logs" / "pseudocron.log").write_text(_BANNER + "2026-10-11T07:59:00Z fired (minute=59): x\n", encoding="utf-8")
    _write_record(kanban_root, _TASK, 77)
    state = parse_container_state(json.dumps([{
        "Name": "/" + _NAME, "State": {"Status": "running", "StartedAt": "2026-10-11T07:00:00Z"}, "Config": {"Image": _IMAGE},
    }]), now=_NOW)
    table = [Process(1, "bash -l"), Process(40, "python3 /k/scripts/pseudocron.py"), Process(77, f"claude -p {_TASK}")]
    report = build_status_report(state, processes=table, kanban_root=kanban_root,
                                 crontab_text=_template_crontab("crontab-small.example"), now=_NOW)
    assert report.exit_code == 0
    data = report.to_dict()
    assert data["container"]["state"] == "running" and data["container"]["image"] == _IMAGE
    assert data["container"]["uptime_seconds"] == 3600
    assert data["scheduler"] == {"running": True, "pids": [40]}
    assert data["heartbeat"]["age_seconds"] == 60
    assert data["agent_sessions"] == [{"role": "CODER", "task_id": _TASK, "pid": 77, "provider": "claude"}]
    assert data["host_crontab"]["wake_lines_present"] is True
    text = render_status_text(report)
    assert any(line.startswith("ERROR: host crontab carries") for line in text.splitlines()), text


@pytest.mark.parametrize(
    "scenario_container, expected_state, expected_exit",
    [
        (None, "absent", 3),
        (container(status="exited"), "stopped", 3),
        (container(), "running", 3),
        ("with-scheduler", "running", 0),
    ],
)
def test_status_verb_exit_code_per_state_from_fake_engine(tmp_path, kanban_root, scenario_container, expected_state, expected_exit):
    if scenario_container == "with-scheduler":
        scenario_container = container(processes=[scheduler_process(40, kanban_root)])
    engine = FakeEngine(tmp_path / "engine", scenario={
        "images": [_IMAGE], "containers": {_NAME: scenario_container} if scenario_container else {},
    })
    inspect = _engine_call(engine, tmp_path, kanban_root, "container", "inspect", _NAME)
    inspect_file, stderr_file, ps_file = tmp_path / "inspect.json", tmp_path / "inspect.err", tmp_path / "ps.txt"
    inspect_file.write_text(inspect.stdout, encoding="utf-8")
    stderr_file.write_text(inspect.stderr, encoding="utf-8")
    args = ["status", "--format", "json", "--name", _NAME, "--kanban-root", str(kanban_root),
            "--input", str(inspect_file), "--exit-code", str(inspect.returncode), "--stderr-file", str(stderr_file)]
    if expected_state == "running":
        ps = _engine_call(engine, tmp_path, kanban_root, "exec", _NAME, "ps", "-eo", "pid=,args=")
        ps_file.write_text(ps.stdout, encoding="utf-8")
        args += ["--ps-file", str(ps_file)]
    result = _lifecycle_cli(tmp_path, kanban_root, *args)
    assert result.returncode == expected_exit, _diagnostic(result)
    data = json.loads(result.stdout)
    assert data["container"]["state"] == expected_state, _diagnostic(result)
    assert data["exit_code"] == expected_exit
    assert set(data) >= {"container", "scheduler", "heartbeat", "agent_sessions", "host_crontab", "errors"}
