"""
test_wake_now_detaches_fired_agents.py
======================================
Every agent ``wake-now.sh`` fires in the background (``--bg`` or ``--all``)
is detached explicitly into a session of its own and its pid is recorded:
the script prints each fired pid, that pid is the session and process-group
leader of the firing (so ``kill -- -PID`` stops the whole wake cycle), and
wake-now.sh returns without waiting for it.

Each test runs the real wake-now.sh from a throwaway copy of this tree's
scripts/ in which wake-batch.sh is a stub that records its own pid and
arguments and then becomes a plain ``sleep``, under a sandbox kanban root
with every root variable pointed into it, so nothing under the operator's
live install is read or written and no real wake is fired. Every fired
process group is killed in teardown whatever the verdict.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import signal
import subprocess
import time

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_PIPELINE_AGENTS = ("pm", "coder", "cm", "writer", "tester")

# Records "<pid> <args>" and then stays alive as a plain sleep under the same
# pid, so the test can inspect the fired process after wake-now.sh exits.
_STUB = """#!/usr/bin/env bash
printf '%s %s\\n' "$$" "$*" >> "{record}"
exec sleep 30
"""

_PID_LINE = re.compile(r"\[(\w+)\]\s+PID\s+(\d+)\s+\(detached into its own session")


def _sandbox(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    root = tmp_path / "kanban"
    root.mkdir()
    (root / "tmp").mkdir()
    shutil.copytree(_TEAM_DIR / "scripts", root / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(_TEAM_DIR / "config", root / "config")
    record = tmp_path / "fired.txt"
    stub = root / "scripts" / "wake-batch.sh"
    stub.write_text(_STUB.format(record=record), encoding="utf-8")
    stub.chmod(0o755)
    return root, record


def _wake_now(tmp_path: pathlib.Path, root: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    (tmp_path / "home").mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
        },
    )
    # Output goes to files, not pipes: a fired agent keeps this script's
    # stdout and stderr, and a pipe would stay open until it exits.
    out_path, err_path = tmp_path / "wake-now.out", tmp_path / "wake-now.err"
    with open(out_path, "wb") as out, open(err_path, "wb") as err:
        rc = subprocess.run(
            ["bash", str(root / "scripts" / "wake-now.sh"), *args],
            stdout=out,
            stderr=err,
            env=env,
            cwd=str(tmp_path),
            stdin=subprocess.DEVNULL,
            timeout=60,
        ).returncode
    return subprocess.CompletedProcess(
        args, rc, out_path.read_text(encoding="utf-8"), err_path.read_text(encoding="utf-8")
    )


def _recorded(record: pathlib.Path, expect: int) -> dict[int, str]:
    """Return {pid: args} for each stub firing, waiting for ``expect`` of them."""
    deadline = time.monotonic() + 15
    lines: list[str] = []
    while time.monotonic() < deadline:
        lines = record.read_text(encoding="utf-8").splitlines() if record.is_file() else []
        if len(lines) >= expect:
            break
        time.sleep(0.05)
    return {int(pid): rest for pid, _, rest in (line.partition(" ") for line in lines)}


@pytest.fixture
def fired_groups():
    """Pids the test fired; in teardown each one's process group and the pid
    itself are killed, so a firing that was not detached dies too."""
    groups: list[int] = []
    yield groups
    for pid in set(groups):
        for kill in (os.killpg, os.kill):
            try:
                kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


def _assert_detached_and_recorded(pid: int) -> None:
    assert os.getsid(pid) == pid, f"fired pid {pid} is not the leader of its own session (sid {os.getsid(pid)})"
    assert os.getpgid(pid) == pid, f"fired pid {pid} is not the leader of its own process group"


def test_bg_detaches_the_agent_and_prints_its_pid(tmp_path: pathlib.Path, fired_groups: list[int]) -> None:
    root, record = _sandbox(tmp_path)

    result = _wake_now(tmp_path, root, "--agent", "coder", "--bg")
    printed = {int(pid): agent for agent, pid in _PID_LINE.findall(result.stdout)}
    fired_groups.extend(printed)
    recorded = _recorded(record, expect=1)
    fired_groups.extend(recorded)

    assert result.returncode == 0, result.stdout + result.stderr
    assert list(printed.values()) == ["coder"], result.stdout + result.stderr
    assert set(recorded) == set(printed), f"printed pids {printed} != fired pids {recorded}"
    (pid,) = printed
    assert recorded[pid] == "--agent=coder --sleep=0"
    _assert_detached_and_recorded(pid)
    assert f"Fired pids: {pid} " in result.stdout, result.stdout


def test_all_detaches_every_pipeline_agent_and_records_each_pid(
    tmp_path: pathlib.Path, fired_groups: list[int]
) -> None:
    root, record = _sandbox(tmp_path)

    result = _wake_now(tmp_path, root, "--all")
    printed = {int(pid): agent for agent, pid in _PID_LINE.findall(result.stdout)}
    fired_groups.extend(printed)
    recorded = _recorded(record, expect=len(_PIPELINE_AGENTS))
    fired_groups.extend(recorded)

    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(printed.values()) == sorted(_PIPELINE_AGENTS), result.stdout + result.stderr
    assert set(recorded) == set(printed), f"printed pids {printed} != fired pids {recorded}"
    for pid, agent in printed.items():
        assert recorded[pid] == f"--agent={agent} --sleep=0"
        _assert_detached_and_recorded(pid)
    fired_line = next((line for line in result.stdout.splitlines() if line.startswith("Fired pids:")), "")
    assert {int(p) for p in re.findall(r"\d+", fired_line)} == set(printed), result.stdout
