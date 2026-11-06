"""
test_gated_suites_interrupt_leaves_no_runner_process.py
=======================================================
An interrupted run-gated-suites.sh leaves nothing it started alive: each
suite runner starts in its own process group, and on SIGINT, SIGTERM or
SIGHUP the entry point stops every process group in each runner's process
tree, so a runner's sleeping grandchild dies with it.

HOW
---
The REAL entry point runs from a throwaway scripts/ directory (its own lib/
copied beside it) whose two runners are stand-ins, under a sandbox kanban
root with ``[testing] workers = 3`` so both runners start at the same time.
Each stand-in records its own pid and starts a child shell whose own child
is a plain ``sleep`` -- the runner's grandchild, one level deeper than the
runner's direct children, the shape of a pytest worker under pytest:

- the unit stand-in keeps that shell in its own process group;
- the integration stand-in starts it with ``setsid`` in a session of its
  own and absorbs SIGTERM while it waits, as run-integration-tests.sh does
  with pytest, so signalling the runner's group alone would not reach it.

Once both grandchildren are running, the test signals the entry point and
asserts that it exits 128 + the signal number and that no process in either
runner's group, or in the grandchild's group, is alive afterwards.

The entry point is started through a trampoline that resets SIGINT and
SIGHUP to their default dispositions before exec'ing bash, so a signal the
test process happens to ignore is not inherited as ignored (bash cannot
trap a signal ignored on entry). The test owns every process it starts: the
entry point runs in its own session, and every group the stand-ins report
is killed in teardown whatever the verdict.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import signal
import subprocess
import sys
import time

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_ENTRY_POINT = "run-gated-suites.sh"
_SUITES = ("unit", "integration")
_RUNNERS = {"unit": "run-unit-tests.sh", "integration": "run-integration-tests.sh"}

# The unit stand-in: grandchild in the runner's own process group.
_UNIT_STAND_IN = r"""#!/usr/bin/env bash
d="$STUB_DIR"
echo "$$" > "$d/unit.runner.pid.tmp" && mv "$d/unit.runner.pid.tmp" "$d/unit.runner.pid"
bash -c 'sleep 120 & echo "$!" > "$1.tmp" && mv "$1.tmp" "$1"; wait' _ "$d/unit.grandchild.pid" &
wait
"""

# The integration stand-in: grandchild in a session of its own, with the
# runner absorbing SIGTERM and waiting on, as run-integration-tests.sh does.
_INTEGRATION_STAND_IN = r"""#!/usr/bin/env bash
d="$STUB_DIR"
echo "$$" > "$d/integration.runner.pid.tmp" && mv "$d/integration.runner.pid.tmp" "$d/integration.runner.pid"
setsid bash -c 'sleep 120 & echo "$!" > "$1.tmp" && mv "$1.tmp" "$1"; wait' _ "$d/integration.grandchild.pid" &
child=$!
trap 'wait "$child"' TERM
wait "$child"
"""

# Reset the dispositions bash cannot trap when ignored on entry, then exec.
_TRAMPOLINE = (
    "import os, signal, sys\n"
    "signal.signal(signal.SIGINT, signal.SIG_DFL)\n"
    "signal.signal(signal.SIGHUP, signal.SIG_DFL)\n"
    "os.execvp(sys.argv[1], sys.argv[1:])\n"
)


def _build_tree(tmp_path: pathlib.Path) -> dict:
    scripts = tmp_path / "fixture" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(_SCRIPTS_DIR / _ENTRY_POINT, scripts / _ENTRY_POINT)
    shutil.copytree(_SCRIPTS_DIR / "lib", scripts / "lib", ignore=shutil.ignore_patterns("__pycache__"))
    for suite, body in (("unit", _UNIT_STAND_IN), ("integration", _INTEGRATION_STAND_IN)):
        runner = scripts / _RUNNERS[suite]
        runner.write_text(body, encoding="utf-8")
        runner.chmod(0o755)
    paths = {name: tmp_path / name for name in ("kanban_root", "temp_root", "home", "stub")}
    for path in paths.values():
        path.mkdir()
    (paths["kanban_root"] / "kanban.cfg").write_text("[testing]\nworkers = 3\n", encoding="utf-8")
    paths["scripts"] = scripts
    return paths


def _read_pid(path: pathlib.Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return int(text) if text.isdigit() else None


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_until_gone(pgid: int, polls: int = 200) -> bool:
    for _ in range(polls):
        if not _group_alive(pgid):
            return True
        time.sleep(0.05)
    return not _group_alive(pgid)


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP], ids=["TERM", "INT", "HUP"])
def test_interrupt_leaves_no_process_from_either_runners_group(tmp_path: pathlib.Path, signum: int) -> None:
    tree = _build_tree(tmp_path)
    env = build_scrubbed_env(
        home_dir=tree["home"],
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(tree["kanban_root"]),
            "KANBAN_ROOT": str(tree["kanban_root"]),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tree["temp_root"]),
            "STUB_DIR": str(tree["stub"]),
        },
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", _TRAMPOLINE, "bash", str(tree["scripts"] / _ENTRY_POINT)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=str(tree["scripts"]),
        start_new_session=True,
    )
    groups: dict[str, int] = {}
    stdout = stderr = ""
    try:
        for _ in range(400):
            pids = {
                f"{suite}.{role}": _read_pid(tree["stub"] / f"{suite}.{role}.pid")
                for suite in _SUITES
                for role in ("runner", "grandchild")
            }
            if all(pids.values()) or proc.poll() is not None:
                break
            time.sleep(0.05)
        assert all(pids.values()), f"the stand-ins did not all start: {pids}"
        for name, pid in pids.items():
            groups[name] = os.getpgid(pid)
        for suite in _SUITES:
            runner_pid = pids[f"{suite}.runner"]
            assert (
                groups[f"{suite}.runner"] == runner_pid
            ), f"the {suite} runner (pid {runner_pid}) is not the leader of its own process group"
        assert groups["unit.grandchild"] == groups["unit.runner"]
        assert groups["integration.grandchild"] != groups["integration.runner"]

        proc.send_signal(signum)
        stdout, stderr = proc.communicate(timeout=60)
        outcome = f"stdout:\n{stdout}\nstderr:\n{stderr}"

        assert proc.returncode == 128 + signum, outcome
        for name, pgid in groups.items():
            assert _wait_until_gone(
                pgid
            ), f"process group {pgid} ({name}) still has a live member after the entry point exited\n{outcome}"
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
        for pgid in set(groups.values()):
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
