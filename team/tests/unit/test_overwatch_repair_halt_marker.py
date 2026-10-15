"""
test_overwatch_repair_halt_marker.py
====================================
The project HALT overwatch_halt_first_fix creates names its creator, so a
HALT left by a sweep that was killed mid-repair can be told apart from an
operator's:

  1. While a repair runs, the HALT carries the creator marker naming this
     shell's pid and start time; it is removed when the repair ends.
  2. The next sweep removes a repair HALT whose creator is gone and logs
     the removal; the project's fixes then run instead of being deferred.
  3. A repair HALT whose creator still runs is left in place.
  4. An operator HALT -- empty, free text, or one that merely mentions the
     marker word -- is never removed, by the sweep or by the protocol.

Every kanban root is a throwaway directory under tmp_path; the sweep runs
with --only leaked-listeners, which touches nothing in these fixtures.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_PROTOCOL = "scripts/lib/overwatch_protocol.sh"
_SWEEP = _TEAM_DIR / "scripts" / "overwatch-sweep.sh"
_PROJECT = "halt-fixture"


def _root(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "kanban"
    proj = root / "projects" / _PROJECT
    (proj / "overwatch" / "backups").mkdir(parents=True)
    (root / "tmp").mkdir()
    (root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8")
    (root / "kanban.cfg").write_text("[paths]\n", encoding="utf-8")
    (proj / "project.cfg").write_text(f"[project]\nproject_name = {_PROJECT}\n", encoding="utf-8")
    return root


def _env(root: pathlib.Path) -> dict:
    return {
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
        "OVERWATCH_PROJECT": _PROJECT,
    }


def _halt(root: pathlib.Path) -> pathlib.Path:
    return root / "projects" / _PROJECT / "HALT"


def _actions(root: pathlib.Path) -> str:
    log = root / "projects" / _PROJECT / "overwatch" / "actions.log"
    return log.read_text(encoding="utf-8") if log.exists() else ""


def _start_time(pid: int) -> str:
    stat = pathlib.Path(f"/proc/{pid}/stat").read_text()
    return stat.rsplit(") ", 1)[1].split()[19]


def _marker(pid: int, start: str) -> str:
    return (
        "# HALT created by OVERWATCH while it repairs this project (fix_fn); removed when the repair ends.\n"
        f"# overwatch-repair-halt: pid={pid} start={start} fn=fix_fn at=2026-01-01T00:00:00Z\n"
    )


def _sweep(tmp_path: pathlib.Path, root: pathlib.Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    for name in ("PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT", "PGAI_PROJECT_NAME", "OVERWATCH_LEAKED_LISTENER_SCAN"):
        env.pop(name, None)
    env.update(_env(root))
    return subprocess.run(
        ["bash", str(_SWEEP), "--only", "leaked-listeners"],
        capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=120,
    )


@pytest.fixture
def sleeper():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield proc
    proc.kill()
    proc.wait(timeout=10)


def test_repair_halt_names_its_creator_and_is_removed_after_the_repair(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path)
    seen = tmp_path / "halt-during-repair.txt"
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_PROTOCOL}
        overwatch_log_action() {{ :; }}
        fix_fn() {{ cat "{_halt(root)}" > "{seen}"; echo "PID=$$"; }}
        overwatch_halt_first_fix fix_fn
    """)
    result = run_bash(tmp_path, script, extra_env=_env(root), timeout=60)

    assert result.returncode == 0, result.stderr
    pid = result.stdout.split("PID=")[1].split()[0]
    text = seen.read_text(encoding="utf-8")
    assert f"# overwatch-repair-halt: pid={pid} start=" in text, text
    assert " fn=fix_fn at=" in text
    assert not _halt(root).exists(), "the repair's own HALT outlived it"


def test_next_sweep_removes_a_repair_halt_whose_creator_is_gone(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    start = _start_time(dead.pid) if pathlib.Path(f"/proc/{dead.pid}").exists() else "1"
    dead.wait(timeout=10)
    _halt(root).write_text(_marker(dead.pid, start), encoding="utf-8")

    result = _sweep(tmp_path, root)

    assert result.returncode == 0, result.stderr
    assert not _halt(root).exists(), result.stderr
    assert "stale-repair-halt-removed" in _actions(root)
    assert "fixes deferred" not in result.stderr, "the removed HALT still deferred the sweep's fixes"


def test_a_live_repairs_halt_is_left_in_place(tmp_path: pathlib.Path, sleeper) -> None:
    root = _root(tmp_path)
    _halt(root).write_text(_marker(sleeper.pid, _start_time(sleeper.pid)), encoding="utf-8")
    before = _halt(root).read_bytes()

    result = _sweep(tmp_path, root)

    assert result.returncode == 0, result.stderr
    assert _halt(root).read_bytes() == before
    assert "stale-repair-halt-removed" not in _actions(root)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "# operator: stopped for maintenance\n",
        "# overwatch-repair-halt: copied by hand, no fields\n",
        "keep stopped; see # overwatch-repair-halt: pid=1 start=1 fn=x at=2026-01-01T00:00:00Z\n",
    ],
    ids=["empty", "operator-text", "marker-word-only", "marker-not-at-line-start"],
)
def test_an_operator_halt_is_never_removed(tmp_path: pathlib.Path, text: str) -> None:
    root = _root(tmp_path)
    _halt(root).write_text(text, encoding="utf-8")

    result = _sweep(tmp_path, root)

    assert result.returncode == 0, result.stderr
    assert _halt(root).read_text(encoding="utf-8") == text
    assert "stale-repair-halt-removed" not in _actions(root)

    script = textwrap.dedent(f"""\
        set -uo pipefail
        source {_PROTOCOL}
        overwatch_log_action() {{ :; }}
        fix_fn() {{ echo FIX-RAN; }}
        overwatch_halt_first_fix fix_fn
        echo "RC=$?"
    """)
    fix = run_bash(tmp_path, script, extra_env=_env(root), timeout=60)
    assert "RC=5" in fix.stdout and "FIX-RAN" not in fix.stdout, fix.stderr
    assert _halt(root).read_text(encoding="utf-8") == text
