"""
test_process_reap.py
====================
pgai_agent_kanban.lib.process_reap finds processes by an environment entry
or a working directory and stops them, and signals nothing it must not:

  1. find_by_env finds a process that inherited the entry and not one that
     did not; find_by_cwd finds a process rooted in the directory.
  2. A process that carries the entry but is the scanner itself or one of
     its ancestors is never named.
  3. A record whose pid now belongs to a different process (its start time
     no longer matches) is never signalled.
  4. stop() takes a SIGTERM-ignoring process down with SIGKILL and reports
     "killed"; one that exits on SIGTERM is "terminated".
  5. The command line refuses an --env without both NAME and VALUE and a
     --cwd-under that is "/" or not a directory, exit 2, naming the value.
  6. The --cwd-under and --grace refusals print a remedy scoped to the
     configured temp root that runs as printed and exits 0, whether that
     directory exists yet or not.

Every process a test starts carries a marker unique to the test in its
argv and is killed in teardown, pass or fail.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import time
import uuid

import pytest

from pgai_agent_kanban.lib import process_reap
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture
def spawn(tmp_path: pathlib.Path):
    marker = f"pgai-process-reap-{uuid.uuid4().hex[:10]}"
    procs: list[subprocess.Popen] = []

    def start(label: str, *, env_entry: str = "", cwd: pathlib.Path = tmp_path, ignore_term: bool = False):
        trap = "trap '' TERM; " if ignore_term else ""
        extra = dict([env_entry.split("=", 1)]) if env_entry else {}
        proc = subprocess.Popen(
            ["bash", "-c", f"{trap}exec -a {marker}-{label} sleep 600"],
            cwd=str(cwd),
            env=build_scrubbed_env(home_dir=tmp_path, extra_env=extra),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        procs.append(proc)
        for _ in range(50):
            try:
                if f"{marker}-{label}" in pathlib.Path(f"/proc/{proc.pid}/cmdline").read_bytes().decode(
                    errors="replace"
                ):
                    return proc
            except OSError:
                pass
            time.sleep(0.1)
        raise AssertionError(f"{label} never started")

    yield start
    for proc in procs:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
    subprocess.run(["pkill", "-KILL", "-f", f"{marker}-"], capture_output=True, timeout=10, check=False)


def test_scans_find_only_the_processes_that_match(tmp_path, spawn) -> None:
    entry = f"PGAI_AGENT_RUN_ID=test-{uuid.uuid4().hex}"
    rooted_dir = tmp_path / "rooted"
    rooted_dir.mkdir()
    carrier = spawn("carrier", env_entry=entry)
    bystander = spawn("bystander")
    rooted = spawn("rooted", cwd=rooted_dir)

    by_env = {r.pid for r in process_reap.find_by_env(entry)}
    by_cwd = {r.pid for r in process_reap.find_by_cwd(rooted_dir)}

    assert by_env == {carrier.pid}
    assert by_cwd == {rooted.pid}
    assert bystander.pid not in by_env | by_cwd


def test_the_scanner_and_its_ancestors_are_never_named(tmp_path) -> None:
    """A child python that carries the entry itself, as does its parent shell,
    finds neither of them."""
    entry_value = f"test-{uuid.uuid4().hex}"
    script = (
        "from pgai_agent_kanban.lib import process_reap\n"
        "import os\n"
        f"found = process_reap.find_by_env('PGAI_AGENT_RUN_ID={entry_value}')\n"
        "print(len(found), os.getpid())\n"
    )
    env = build_scrubbed_env(
        home_dir=tmp_path,
        extra_env={"PGAI_AGENT_RUN_ID": entry_value, "PYTHONPATH": str(_TEAM_DIR)},
    )
    result = subprocess.run(
        ["bash", "-c", f'"{sys.executable}" -c "$SCRIPT"; exit $?'],
        env={**env, "SCRIPT": script},
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.split()[0] == "0", result.stdout + result.stderr


def test_a_recycled_pid_is_never_signalled(spawn) -> None:
    proc = spawn("recycled")
    (record,) = [
        r for r in process_reap.find_by_cwd(pathlib.Path(f"/proc/{proc.pid}/cwd").resolve()) if r.pid == proc.pid
    ]
    stale = process_reap.ProcessRecord(
        pid=record.pid,
        start_time="1",
        command=record.command,
        cwd=record.cwd,
        has_terminal=False,
    )

    outcomes = process_reap.stop([stale], grace_seconds=1)

    assert outcomes == [(stale, "terminated")]
    assert proc.poll() is None, "a pid whose start time no longer matches was signalled"


def test_stop_escalates_to_sigkill_and_reports_each_outcome(tmp_path, spawn) -> None:
    stubborn_dir = tmp_path / "stubborn"
    stubborn_dir.mkdir()
    polite_dir = tmp_path / "polite"
    polite_dir.mkdir()
    stubborn = spawn("stubborn", cwd=stubborn_dir, ignore_term=True)
    polite = spawn("polite", cwd=polite_dir)
    records = process_reap.find_by_cwd(stubborn_dir) + process_reap.find_by_cwd(polite_dir)

    outcomes = {record.pid: outcome for record, outcome in process_reap.stop(records, grace_seconds=1)}

    assert outcomes == {stubborn.pid: "killed", polite.pid: "terminated"}
    assert stubborn.wait(timeout=10) is not None and polite.wait(timeout=10) is not None


@pytest.mark.parametrize(
    "args, named",
    [
        (["--env", "PGAI_AGENT_RUN_ID="], "'PGAI_AGENT_RUN_ID='"),
        (["--env", "=value"], "'=value'"),
        (["--cwd-under", "/"], "'/'"),
        (["--cwd-under", "does-not-exist"], "'does-not-exist'"),
    ],
)
def test_the_command_line_refuses_a_scope_that_could_match_too_much(tmp_path, args, named) -> None:
    env = build_scrubbed_env(home_dir=tmp_path, extra_env={"PYTHONPATH": str(_TEAM_DIR)})
    result = subprocess.run(
        [sys.executable, "-m", "pgai_agent_kanban.lib.process_reap", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert named in result.stderr, result.stdout + result.stderr
    assert result.stdout == "", result.stdout + result.stderr


@pytest.mark.parametrize("temp_root_exists", [True, False], ids=["temp-root-present", "temp-root-absent"])
@pytest.mark.parametrize(
    "args, error_key",
    [
        (["--cwd-under", "does-not-exist"], "process_reap.cwd_scope_invalid"),
        (["--cwd-under", "does-not-exist", "--grace", "-1"], "process_reap.grace_negative"),
    ],
    ids=["cwd-scope-invalid", "grace-negative"],
)
def test_a_scope_refusal_remedy_runs_as_printed_under_the_configured_temp_root(
    tmp_path, args, error_key, temp_root_exists
) -> None:
    temp_root = tmp_path / "sandbox temp"
    if temp_root_exists:
        temp_root.mkdir()
    env = build_scrubbed_env(
        home_dir=tmp_path,
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={"PYTHONPATH": str(_TEAM_DIR), "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_root)},
    )
    refused = subprocess.run(
        [sys.executable, "-m", "pgai_agent_kanban.lib.process_reap", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
        check=False,
    )
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert f"error:   {error_key}" in refused.stderr, refused.stderr
    (remedy,) = [line.split(":", 1)[1].strip() for line in refused.stderr.splitlines() if line.startswith("remedy:")]
    assert f"--cwd-under '{temp_root}'" in remedy and remedy.endswith("--dry-run"), remedy

    ran = subprocess.run(
        ["bash", "-c", remedy],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
        check=False,
    )

    assert ran.returncode == 0, f"remedy {remedy!r} exited {ran.returncode}: {ran.stdout}{ran.stderr}"
    assert ran.stderr == "", ran.stderr
    assert temp_root.is_dir()
