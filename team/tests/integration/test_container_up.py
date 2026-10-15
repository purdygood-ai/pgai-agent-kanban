"""
test_container_up.py
====================
scripts/container-up.sh runs the kanban detached in one named container:

  * from nothing, it issues exactly one detached run named pgai-kanban with
    the HOME and kanban-root mounts and environment lib/container_mounts.sh
    assembles, then one scheduler start (the tmux session "pseudocron"),
    then reports the scheduler's confirmed heartbeat; a heartbeat that never
    comes is reported with a non-zero exit inside the bounded wait;
  * it is idempotent: a container already running with a live scheduler gets
    no run and no second scheduler; one running without a scheduler gets a
    scheduler and no run; a stopped one is removed and recreated;
  * cron XOR pseudocron: with a kanban wake line in the user's crontab it
    refuses (exit 1, dual-channel) before any engine call, and the remedy it
    prints, run as printed, removes the wake lines;
  * a missing image is an error naming docker/build.sh, with no run, build
    or pull; --image-tag and --name override kanban.cfg [docker];
  * inside an agent wake it prints SKIPPED and calls no engine.

No test reaches a real engine or the real crontab. Every subprocess runs
with an environment built from an explicit set
(tests.fixtures.scrubbed_subprocess_env), a throwaway kanban root and HOME,
a fake engine (tests.fixtures.fake_container_engine) and a crontab stub
first on PATH. The authorized runs carry PGAI_CONTAINER_TIER=1 and no
PGAI_AGENT_WAKE_ACTIVE, which build_scrubbed_env never inherits.
"""

from __future__ import annotations

import pathlib
import shlex
import subprocess
import sys
import time

import pytest

from pgai_agent_kanban.container import lifecycle
from tests.fixtures.fake_container_engine import (
    FakeEngine,
    container,
    install_fake_crontab,
    scheduler_process,
)
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_UP_SH = _TEAM_DIR / "scripts" / "container-up.sh"
_CRONTAB_TEMPLATE = _TEAM_DIR / "templates" / "install" / "crontab-small.example"

_IMAGE = "pgai-kanban:latest"
_NAME = "pgai-kanban"
_SKIP_KEY = "key=container_tier.operator_only"


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
    """A throwaway kanban root, HOME and temp dir, a fake engine and a
    crontab stub (no crontab unless a test installs one) first on PATH."""

    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.tmp = tmp_path
        self.root = (tmp_path / "kanban_root").resolve()
        self.root.mkdir()
        self.home = (tmp_path / "home").resolve()
        self.home.mkdir()
        self.temp = tmp_path / "pgai_tmp"
        self.temp.mkdir()
        self.engine = FakeEngine(tmp_path / "engine", name="docker", scenario={
            "images": [_IMAGE],
            "scheduler": {"log": str(self.root / "logs" / "pseudocron.log"), "fires": True},
        })
        self.cron_bin = tmp_path / "cron_bin"
        install_fake_crontab(self.cron_bin, None)
        # The interpreter running this test answers python3 inside the script.
        self.py_bin = tmp_path / "py_bin"
        self.py_bin.mkdir()
        (self.py_bin / "python3").symlink_to(pathlib.Path(sys.executable))

    @property
    def heartbeat_log(self) -> pathlib.Path:
        return self.root / "logs" / "pseudocron.log"

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
            extra_path_dirs=(self.engine.bin_dir, self.cron_bin, self.py_bin),
            extra_env=extra,
        )
        if not wake:
            assert "PGAI_AGENT_WAKE_ACTIVE" not in env
        return env

    def up(self, *args: str, wake: bool = False) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(_UP_SH), *args],
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

    def crontab_calls(self) -> list[str]:
        log = self.cron_bin / "crontab-calls.log"
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


@pytest.fixture
def sandbox(tmp_path: pathlib.Path) -> _Sandbox:
    return _Sandbox(tmp_path)


def _expected_mount_args(sb: _Sandbox) -> list[str]:
    home, root = str(sb.home), str(sb.root)
    return [
        "-v", f"{home}:{home}",
        "-v", f"{root}:{root}",
        "-e", f"HOME={home}",
        "-e", f"PGAI_AGENT_KANBAN_ROOT_PATH={root}",
    ]


def _assert_scheduler_start(argv: list[str], sb: _Sandbox, name: str = _NAME) -> None:
    assert argv[0] == "exec" and name in argv, argv
    tmux = argv[argv.index("tmux"):]
    assert tmux[:5] == ["tmux", "new", "-d", "-s", "pseudocron"], tmux
    command = tmux[5]
    assert f"{sb.root}/scripts/pseudocron.py" in command
    assert f">> {sb.root}/logs/pseudocron.log 2>&1" in command


# ---------------------------------------------------------------------------
# Fresh start: one run, one scheduler, a confirmed heartbeat
# ---------------------------------------------------------------------------


def test_fresh_start_runs_one_detached_container_then_one_scheduler_and_confirms_heartbeat(sandbox):
    result = sandbox.up()
    assert result.returncode == 0, _diagnostic(result)

    runs = sandbox.calls_named("run")
    assert len(runs) == 1, sandbox.commands()
    run = runs[0]
    assert run[:5] == ["run", "-d", "-i", "-t", "--name"] and run[5] == _NAME, run
    assert run[6:-1] == _expected_mount_args(sandbox), run
    assert run[-1] == _IMAGE

    starts = sandbox.calls_named("exec tmux")
    assert len(starts) == 1, sandbox.commands()
    _assert_scheduler_start(starts[0], sandbox)

    commands = sandbox.commands()
    assert commands.index("run") < commands.index("exec tmux"), commands
    assert not {"build", "pull", "rm"} & set(commands), commands
    assert "heartbeat confirmed" in result.stdout, _diagnostic(result)

    # One docker-use session records the whole run with its exit code.
    use_log = (sandbox.root / "logs" / "docker-use.log").read_text(encoding="utf-8")
    assert "START actor=operator" in use_log and "script=container-up.sh" in use_log, use_log
    assert "exit=0" in use_log, use_log


def test_unconfirmed_heartbeat_is_reported_non_zero_within_the_bounded_wait(sandbox):
    sandbox.scenario(scheduler={"log": str(sandbox.heartbeat_log), "fires": False})
    started = time.monotonic()
    result = sandbox.up("--heartbeat-timeout", "2")
    elapsed = time.monotonic() - started

    assert result.returncode == 1, _diagnostic(result)
    assert elapsed < 20, f"the bounded wait took {elapsed:.1f}s"
    fields = _contract(result.stderr)
    assert fields.get("error") == "container_up.heartbeat_unconfirmed", _diagnostic(result)
    assert fields["remedy"] == f"tail -n 20 {sandbox.heartbeat_log}"
    assert len(sandbox.calls_named("run")) == 1
    assert len(sandbox.calls_named("exec tmux")) == 1


def test_heartbeat_from_an_earlier_scheduler_run_does_not_count(sandbox):
    sandbox.heartbeat_log.parent.mkdir(parents=True)
    sandbox.heartbeat_log.write_text(
        "2020-01-01T00:00:00Z fired (minute=00): scripts/wake-batch.sh --agent=pm\n", encoding="utf-8"
    )
    sandbox.scenario(scheduler={"log": str(sandbox.heartbeat_log), "fires": False})
    result = sandbox.up("--heartbeat-timeout", "0")
    assert result.returncode == 1, _diagnostic(result)
    assert _contract(result.stderr).get("error") == "container_up.heartbeat_unconfirmed"


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------


def test_running_container_with_live_scheduler_gets_no_run_and_no_second_scheduler(sandbox):
    sandbox.scenario(containers={_NAME: container(processes=[scheduler_process(40, sandbox.root)])})
    result = sandbox.up()

    assert result.returncode == 0, _diagnostic(result)
    assert "already running" in result.stdout, _diagnostic(result)
    assert sandbox.calls_named("run") == []
    assert sandbox.calls_named("exec tmux") == []


def test_second_invocation_issues_no_second_run_or_scheduler(sandbox):
    first = sandbox.up()
    assert first.returncode == 0, _diagnostic(first)
    second = sandbox.up()

    assert second.returncode == 0, _diagnostic(second)
    assert "already running" in second.stdout, _diagnostic(second)
    assert len(sandbox.calls_named("run")) == 1, sandbox.commands()
    assert len(sandbox.calls_named("exec tmux")) == 1, sandbox.commands()


def test_stopped_container_is_removed_and_recreated_with_one_run(sandbox):
    sandbox.scenario(containers={_NAME: container(status="exited")})
    result = sandbox.up()

    assert result.returncode == 0, _diagnostic(result)
    commands = sandbox.commands()
    assert sandbox.calls_named("rm") == [["rm", _NAME]], commands
    assert len(sandbox.calls_named("run")) == 1, commands
    assert commands.index("rm") < commands.index("run") < commands.index("exec tmux"), commands


def test_running_container_without_scheduler_gets_one_scheduler_and_no_run(sandbox):
    sandbox.scenario(containers={_NAME: container()})
    result = sandbox.up()

    assert result.returncode == 0, _diagnostic(result)
    assert sandbox.calls_named("run") == []
    starts = sandbox.calls_named("exec tmux")
    assert len(starts) == 1, sandbox.commands()
    _assert_scheduler_start(starts[0], sandbox)


# ---------------------------------------------------------------------------
# Cron XOR pseudocron
# ---------------------------------------------------------------------------


def test_host_cron_wake_line_refuses_before_any_engine_call(sandbox):
    install_fake_crontab(sandbox.cron_bin, "MAILTO=\"\"\n" + _template_wake_line(sandbox.root) + "\n")
    result = sandbox.up()

    assert result.returncode == 1, _diagnostic(result)
    fields = _contract(result.stderr)
    assert fields.get("error") == "container_up.host_cron_wake_lines", _diagnostic(result)
    assert "1 kanban wake line" in fields["message"]
    assert "--wake-tier none" in fields["message"]
    remedy = fields["remedy"]
    assert "<" not in remedy and "upgrade.sh" not in remedy, remedy
    assert sandbox.engine.records() == [], sandbox.commands()
    assert sandbox.crontab_calls() == ["-l"]


def _install_stateful_crontab(bin_dir: pathlib.Path, text: str) -> pathlib.Path:
    """A crontab stub that lists (-l) and installs (-) one stored crontab.

    Like the real crontab, ``-`` reads all of stdin before it replaces the
    stored crontab, so ``crontab -l | ... | crontab -`` is not a race."""
    store = bin_dir / "crontab.state"
    store.write_text(text, encoding="utf-8")
    stub = bin_dir / "crontab"
    q = shlex.quote(str(store))
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"if [[ \"$*\" == \"-l\" ]]; then cat {q}; exit 0; fi\n"
        f"if [[ \"$*\" == \"-\" ]]; then new=\"$(cat; echo .)\"; printf '%s' \"${{new%.}}\" > {q}; exit 0; fi\n"
        "echo \"stub crontab: unsupported: $*\" >&2; exit 1\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return store


def test_cron_refusal_remedy_removes_the_wake_lines_when_run_as_printed(sandbox):
    keep = "15 3 * * * /usr/local/bin/backup.sh --nightly"
    original = f"{keep}\n{_template_wake_line(sandbox.root)}\n"
    store = _install_stateful_crontab(sandbox.cron_bin, original)
    refused = sandbox.up()
    assert refused.returncode == 1, _diagnostic(refused)
    remedy = _contract(refused.stderr)["remedy"]

    applied = subprocess.run(
        ["bash", "-c", remedy], capture_output=True, text=True,
        env=sandbox.env(), cwd=str(sandbox.tmp), timeout=30,
    )
    assert applied.returncode == 0, _diagnostic(applied)

    after = store.read_text(encoding="utf-8")
    assert lifecycle.crontab_wake_lines(after) == [], after
    assert keep in after
    assert (sandbox.home / "crontab-before-container-up.bak").read_text(encoding="utf-8") == original

    retried = sandbox.up()
    assert retried.returncode == 0, _diagnostic(retried)
    assert len(sandbox.calls_named("run")) == 1


# ---------------------------------------------------------------------------
# Image and name
# ---------------------------------------------------------------------------


def test_missing_image_names_build_script_and_runs_builds_and_pulls_nothing(sandbox):
    sandbox.scenario(images=[])
    result = sandbox.up()

    assert result.returncode == 1, _diagnostic(result)
    fields = _contract(result.stderr)
    assert fields.get("error") == "container_up.image_missing", _diagnostic(result)
    assert "docker/build.sh" in fields["message"] and "docker/build.sh" in fields["remedy"]
    assert not {"run", "build", "pull", "exec tmux"} & set(sandbox.commands()), sandbox.commands()


def test_kanban_cfg_names_the_image_and_container(sandbox):
    sandbox.write_cfg("[docker]\ncontainer_name = cfg-kanban\nimage = pgai-kanban:rocky9\n")
    sandbox.scenario(images=["pgai-kanban:rocky9"])
    result = sandbox.up()

    assert result.returncode == 0, _diagnostic(result)
    (run,) = sandbox.calls_named("run")
    assert run[run.index("--name") + 1] == "cfg-kanban" and run[-1] == "pgai-kanban:rocky9", run
    _assert_scheduler_start(sandbox.calls_named("exec tmux")[0], sandbox, name="cfg-kanban")


def test_image_tag_and_name_flags_override_kanban_cfg(sandbox):
    sandbox.write_cfg("[docker]\ncontainer_name = cfg-kanban\nimage = pgai-kanban:rocky9\n")
    sandbox.scenario(images=["pgai-kanban:rocky9", "pgai-kanban:debian"])
    result = sandbox.up("--image-tag", "pgai-kanban:debian", "--name", "flag-kanban")

    assert result.returncode == 0, _diagnostic(result)
    (run,) = sandbox.calls_named("run")
    assert run[run.index("--name") + 1] == "flag-kanban" and run[-1] == "pgai-kanban:debian", run
    assert "cfg-kanban" not in str(sandbox.engine.calls())


# ---------------------------------------------------------------------------
# Operator-only and usage
# ---------------------------------------------------------------------------


def test_agent_wake_prints_skipped_and_calls_no_engine(sandbox):
    result = sandbox.up(wake=True)

    assert result.returncode == 0, _diagnostic(result)
    assert "SKIPPED" in result.stdout and _SKIP_KEY in result.stdout, _diagnostic(result)
    assert sandbox.engine.records() == []
    assert sandbox.crontab_calls() == []


def test_unknown_argument_is_a_usage_refusal(sandbox):
    result = sandbox.up("--bogus")

    assert result.returncode == 2, _diagnostic(result)
    assert _contract(result.stderr).get("error") == "container_up.usage"
    assert sandbox.engine.records() == []
