"""
test_dashboard_log_pane_timestamps.py
=====================================
The dashboard's merged log panes (team/scripts/dashboard/logs.sh and
debug-logs.sh) stamp every line with the time without starting a process
per line.

A hidden pane is stopped and resumed when shown, and ``tail -F`` then
replays the whole backlog through the pane's awk program at once; a
``date`` fork per line turns a day's backlog into tens of thousands of
processes on a CPU-capped host. The awk program now reads the time from
awk's own ``strftime()``, or -- in an awk without it -- runs ``date`` at
most once per second.

Each case runs the real script end to end in a sandbox root, with shims
ahead of the standard toolchain on PATH:
  * ``tail`` prints one ``==> <path> <==`` header naming a real log path the
    script passed it, then 2,000 lines, and exits (standing in for a resumed
    ``tail -F`` replaying its backlog);
  * ``date`` counts each ``+%H:%M:%S`` call, then runs the real ``date``;
  * ``sleep`` returns at once (the scripts pause after the stream ends);
  * for the fallback case, ``awk`` is gawk in ``--posix`` mode, which has no
    ``strftime()``.

Asserted for each pane and each awk: at most a handful of ``date`` calls
for 2,000 lines, every line tagged, and the tag formats unchanged --
``[agent HH:MM:SS]`` for logs.sh and single-project debug-logs.sh,
``[project/agent HH:MM:SS]`` for debug-logs.sh with two verbose projects.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_LOGS_SH = _TEAM_DIR / "scripts" / "dashboard" / "logs.sh"
_DEBUG_LOGS_SH = _TEAM_DIR / "scripts" / "dashboard" / "debug-logs.sh"
_LINES = 2000
_MAX_DATE_CALLS = 5
_GAWK = shutil.which("gawk")
_DATE = shutil.which("date") or "/usr/bin/date"


def _shims(tmp_path: pathlib.Path, *, posix_awk: bool) -> pathlib.Path:
    bin_dir = tmp_path / "shim-bin"
    bin_dir.mkdir()
    shims = {
        "tail": (
            "#!/usr/bin/env bash\n"
            'pick=""\n'
            'for a in "$@"; do case "$a" in *"/$PANE_TEST_PICK") pick="$a" ;; esac; done\n'
            "printf '==> %s <==\\n' \"$pick\"\n"
            f"seq -f 'line %g' {_LINES}\n"
        ),
        "date": (
            "#!/usr/bin/env bash\n"
            'case " $* " in *"+%H:%M:%S"*) echo call >> "$PANE_TEST_DATE_COUNT" ;; esac\n'
            f'exec {_DATE} "$@"\n'
        ),
        "sleep": "#!/usr/bin/env bash\nexit 0\n",
    }
    if posix_awk:
        shims["awk"] = f'#!/usr/bin/env bash\nexec {_GAWK} --posix "$@"\n'
    for name, body in shims.items():
        path = bin_dir / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
    return bin_dir


def _root(tmp_path: pathlib.Path, verbose_projects: tuple[str, ...]) -> pathlib.Path:
    root = tmp_path / "kanban"
    (root / "logs").mkdir(parents=True)
    (root / "tmp").mkdir()
    registry = ""
    for priority, name in enumerate(verbose_projects, start=1):
        registry += f"[project:{name}]\npriority = {priority}\n\n"
        project = root / "projects" / name
        (project / "logs" / "debug").mkdir(parents=True)
        (project / "project.cfg").write_text(
            f"[project]\nproject_name = {name}\n\n[debug]\nverbose_mode = true\n", encoding="utf-8"
        )
    (root / "projects.cfg").write_text(registry, encoding="utf-8")
    return root


def _run_pane(tmp_path: pathlib.Path, script: pathlib.Path, root: pathlib.Path, pick: str, *, posix_awk: bool):
    count_file = tmp_path / "date-calls"
    home = tmp_path / "home"
    home.mkdir()
    env = build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[_shims(tmp_path, posix_awk=posix_awk)],
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
            "NO_COLOR": "1",
            "TERM": "dumb",
            "PANE_TEST_PICK": pick,
            "PANE_TEST_DATE_COUNT": str(count_file),
        },
    )
    result = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, env=env, cwd=str(tmp_path),
        timeout=120, stdin=subprocess.DEVNULL,
    )
    calls = len(count_file.read_text(encoding="utf-8").splitlines()) if count_file.exists() else 0
    return result, calls


_AWKS = [
    pytest.param(False, id="host-awk"),
    pytest.param(
        True, id="posix-awk-fallback",
        marks=pytest.mark.skipif(_GAWK is None, reason="gawk is needed to run an awk without strftime"),
    ),
]


def _assert_tagged(stdout: str, label: str) -> None:
    tag = re.compile(rf"^\[{re.escape(label)} \d\d:\d\d:\d\d\] line (\d+)$")
    tagged = [m for m in (tag.match(line) for line in stdout.splitlines()) if m]
    assert len(tagged) == _LINES, f"expected {_LINES} lines tagged [{label} HH:MM:SS]; got {len(tagged)}:\n{stdout[:600]}"
    assert [int(m.group(1)) for m in tagged[:3]] == [1, 2, 3]


@pytest.mark.parametrize("posix_awk", _AWKS)
def test_cron_log_pane_does_not_fork_date_per_line(tmp_path: pathlib.Path, posix_awk: bool) -> None:
    root = _root(tmp_path, ())

    result, calls = _run_pane(tmp_path, _LOGS_SH, root, "cron-coder.log", posix_awk=posix_awk)

    assert "log stream ended" in result.stdout, result.stderr
    assert calls <= _MAX_DATE_CALLS, f"{calls} date calls for {_LINES} lines"
    _assert_tagged(result.stdout, "coder")


@pytest.mark.parametrize("posix_awk", _AWKS)
def test_debug_log_pane_does_not_fork_date_per_line(tmp_path: pathlib.Path, posix_awk: bool) -> None:
    root = _root(tmp_path, ("alpha",))

    result, calls = _run_pane(tmp_path, _DEBUG_LOGS_SH, root, "alpha/logs/debug/coder.log", posix_awk=posix_awk)

    assert "debug log stream ended" in result.stdout, result.stderr
    assert calls <= _MAX_DATE_CALLS, f"{calls} date calls for {_LINES} lines"
    _assert_tagged(result.stdout, "coder")


@pytest.mark.parametrize("posix_awk", _AWKS)
def test_multi_project_debug_pane_keeps_the_project_agent_tag(tmp_path: pathlib.Path, posix_awk: bool) -> None:
    root = _root(tmp_path, ("alpha", "beta"))

    result, calls = _run_pane(tmp_path, _DEBUG_LOGS_SH, root, "beta/logs/debug/tester.log", posix_awk=posix_awk)

    assert calls <= _MAX_DATE_CALLS, f"{calls} date calls for {_LINES} lines"
    _assert_tagged(result.stdout, "beta/tester")
