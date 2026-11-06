"""
test_verify_window0_geometry.py
===============================
Behaviour tests for how ``scripts/dashboard/verify-window0-geometry.sh``
finds the queues pane: the pane whose child or grandchild process has
``--mode queues`` in its arguments. Once that pane is found, the script checks
that it is the rightmost pane and about ``PGAI_WINDOW0_RIGHT_COL_PCT`` percent
of the window wide.

Each case builds a real process tree for every pane: a pane shell, its
child, and that child's own child. The dashboard's queues pane is pane
shell -> ``cadence-loop.sh ... -- show-multi.sh --mode queues ...``, a
direct child whose own arguments carry the marker; the grandchild cases
stand for a wrapper child whose own arguments lack it. ``exec -a`` gives a
``sleep`` the queues command line. A tmux stub on PATH reports the panes'
geometry and PIDs, so no tmux server is involved.

A ``ps`` shim on PATH widens procps's pid column (``-o pid=`` becomes
``-o pid:12=``) and passes everything else to the real ``ps``. Every child
PID is then narrower than its column and printed with leading spaces,
whatever PIDs this host hands out. The real ``ps`` still decides what a
padded ``--ppid`` value means.

Covered behaviour:

  * a queues process two levels below the pane shell, reached through a
    space-padded child PID, identifies its pane: no "No pane found" warning,
    no fallback, and exit 0 for a correct layout;
  * the pane that search finds is the one checked: placed on the left, it
    fails the rightmost check instead of being replaced by the fallback guess;
  * a pane with no children, or whose grandchild is not the queues process,
    is passed over without error under the script's ``set -euo pipefail``;
  * a queues process directly below the pane shell is found by the
    direct-child check;
  * with no queues process anywhere the script warns, names the fallback
    rightmost pane, and applies the geometric check to it.

The script runs against a throwaway kanban root under ``tmp_path`` with an
environment built from an explicit set (``build_scrubbed_env``). Every
process a tree starts runs in its own session and is killed with its whole
process group at teardown, whether the test passed or failed.
"""

from __future__ import annotations

import os
import pathlib
import shlex
import signal
import subprocess
import sys
import textwrap
import time
from collections.abc import Iterator

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from tests.fixtures.binary_guard import require_binary  # noqa: E402
from tests.fixtures.dashboard_render_fixture import run_dashboard_script  # noqa: E402
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402

_SCRIPT = _TEAM_DIR / "scripts" / "dashboard" / "verify-window0-geometry.sh"
_TIMEOUT_SECONDS = 60
_READY_SECONDS = 10.0
#: How long each stand-in process sleeps. Teardown kills it long before.
_STAND_IN_SECONDS = 120
_WINDOW_WIDTH = 200
_QUEUES_MARKER = "--mode queues"
_QUEUES_ARGV = "show-multi.sh --mode queues --interval 5"
_NO_PANE_WARNING = "No pane found with a child process containing '--mode queues'"
_FALLBACK_LINE = "Fallback rightmost pane"

#: Pane kinds: what each pane shell runs, keyed by kind.
CHILDLESS = "childless"
QUEUES_CHILD = "queues-child"
QUEUES_GRANDCHILD = "queues-grandchild"
PLAIN_GRANDCHILD = "plain-grandchild"

_TMUX_STUB = textwrap.dedent("""\
    #!/bin/bash
    # tmux stand-in. Answers the queries verify-window0-geometry.sh makes from
    # files beside it: panes.txt holds one "<index> <left> <width> <pid>" line
    # per pane, window_width the window's width.
    here="$(dirname "$0")"
    case "$1" in
      list-panes)
        if [[ "$*" == *pane_top* ]]; then
          while read -r index left width pid; do
            echo "$index pane_left=$left pane_top=0 pane_width=$width pane_height=40 pid=$pid"
          done < "$here/panes.txt"
        else
          cat "$here/panes.txt"
        fi
        exit 0 ;;
      display-message)
        [[ "$*" == *window_width* ]] && cat "$here/window_width"
        exit 0 ;;
    esac
    exit 0
""")

_PS_SHIM = textwrap.dedent("""\
    #!/bin/bash
    # ps stand-in. Widens the pid column past any PID's width, so every PID
    # prints right-aligned with leading spaces, and hands the call to the real
    # ps unchanged otherwise.
    args=()
    for arg in "$@"; do
      [[ "$arg" == "pid=" ]] && arg="pid:12="
      args+=("$arg")
    done
    exec @REAL_PS@ "${args[@]}"
""")


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"--- exit {result.returncode} ---\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


class _Sandbox:
    """A throwaway kanban root, the tmux stub and ps shim, and the pane process trees."""

    def __init__(self, tmp_path: pathlib.Path, real_ps: str) -> None:
        self.tmp_path = tmp_path
        self.real_ps = real_ps
        self.root = tmp_path / "root"
        (self.root / "tmp").mkdir(parents=True)
        (tmp_path / "home").mkdir()
        self.bin_dir = tmp_path / "bin"
        self.bin_dir.mkdir()
        self.trees_dir = tmp_path / "trees"
        self.trees_dir.mkdir()
        self._write_executable(self.bin_dir / "tmux", _TMUX_STUB)
        self._write_executable(self.bin_dir / "ps", _PS_SHIM.replace("@REAL_PS@", shlex.quote(real_ps)))
        (self.bin_dir / "window_width").write_text(f"{_WINDOW_WIDTH}\n", encoding="utf-8")
        self._procs: list[subprocess.Popen] = []
        self.env = build_scrubbed_env(
            home_dir=tmp_path / "home",
            extra_path_dirs=[self.bin_dir],
            extra_env={
                "KANBAN_ROOT": str(self.root),
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.root / "tmp"),
            },
        )

    @staticmethod
    def _write_executable(path: pathlib.Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)

    # -- process trees ------------------------------------------------------

    def _ps(self, *args: str) -> list[str]:
        """Lines the real ps prints; an empty list when it matches nothing."""
        result = subprocess.run([self.real_ps, *args], capture_output=True, text=True, timeout=10, cwd=self.tmp_path)
        return [line for line in result.stdout.splitlines() if line.strip()]

    def children(self, pid: int) -> list[int]:
        return [int(line) for line in self._ps("--ppid", str(pid), "-o", "pid=")]

    def descendant_args(self, pid: int, depth: int) -> list[str]:
        """The argument lists of *pid*'s descendants exactly *depth* levels down."""
        level = [pid]
        for _ in range(depth - 1):
            level = [child for parent in level for child in self.children(parent)]
        return [line for parent in level for line in self._ps("--ppid", str(parent), "-o", "args=")]

    def _pane_script(self, kind: str) -> str:
        sleeper = f"sleep {_STAND_IN_SECONDS}"
        queues = f"(exec -a {shlex.quote(_QUEUES_ARGV)} {sleeper}) & wait\n"
        if kind == CHILDLESS:
            return f"exec {sleeper}\n"
        if kind == QUEUES_CHILD:
            return queues
        inner = self.trees_dir / f"watch-{kind}.sh"
        inner.write_text(queues if kind == QUEUES_GRANDCHILD else f"{sleeper} & wait\n", encoding="utf-8")
        return f"bash {shlex.quote(str(inner))} & wait\n"

    def _ready(self, kind: str, pid: int) -> bool:
        if kind == CHILDLESS:
            return self._ps("-p", str(pid), "-o", "args=") == [f"sleep {_STAND_IN_SECONDS}"]
        if kind == QUEUES_CHILD:
            return any(_QUEUES_MARKER in args for args in self.descendant_args(pid, 1))
        if kind == QUEUES_GRANDCHILD:
            return any(_QUEUES_MARKER in args for args in self.descendant_args(pid, 2))
        return any(args.startswith("sleep ") for args in self.descendant_args(pid, 2))

    def spawn_pane(self, kind: str) -> int:
        """Start a pane shell of *kind* and return its PID once its tree is complete."""
        script = self.trees_dir / f"pane-{len(self._procs)}.sh"
        script.write_text(self._pane_script(kind), encoding="utf-8")
        proc = subprocess.Popen(
            ["bash", str(script)],
            cwd=self.tmp_path,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self._procs.append(proc)
        deadline = time.monotonic() + _READY_SECONDS
        while not self._ready(kind, proc.pid):
            assert proc.poll() is None, f"the {kind} pane shell exited early with {proc.returncode}"
            assert time.monotonic() < deadline, f"the {kind} pane's process tree did not form"
            time.sleep(0.05)
        return proc.pid

    def kill_all(self) -> None:
        """Kill every pane shell's whole process group, then reap the shells."""
        for proc in self._procs:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for proc in self._procs:
            proc.wait(timeout=10)

    # -- the script under test ---------------------------------------------

    def layout(self, *panes: tuple[str, int, int]) -> list[int]:
        """Start one pane per ``(kind, left, width)`` and publish them to the tmux stub.

        Returns the pane PIDs in pane-index order.
        """
        pids = [self.spawn_pane(kind) for kind, _, _ in panes]
        lines = [f"{index} {left} {width} {pid}" for index, ((_, left, width), pid) in enumerate(zip(panes, pids))]
        (self.bin_dir / "panes.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return pids

    def run(self) -> subprocess.CompletedProcess:
        return run_dashboard_script(
            [str(_SCRIPT), "main", "--kanban-root", str(self.root)],
            env=self.env,
            timeout=_TIMEOUT_SECONDS,
        )

    def child_pid_lines(self, pid: int) -> list[str]:
        """What the script's own ``ps --ppid <pid> -o pid=`` capture reads, padding kept."""
        result = subprocess.run(
            ["ps", "--ppid", str(pid), "-o", "pid="],
            capture_output=True,
            text=True,
            timeout=10,
            env=self.env,
            cwd=self.tmp_path,
        )
        return [line for line in result.stdout.splitlines() if line.strip()]


@pytest.fixture
def sandbox(tmp_path: pathlib.Path) -> Iterator[_Sandbox]:
    guard = require_binary("ps", label="verify-window0-geometry pane search")
    box = _Sandbox(tmp_path, guard.path)
    try:
        yield box
    finally:
        box.kill_all()


def _assert_found_without_fallback(result: subprocess.CompletedProcess) -> None:
    output = result.stdout + result.stderr
    assert _NO_PANE_WARNING not in output, _diagnostic(result)
    assert _FALLBACK_LINE not in output, _diagnostic(result)


def _assert_padded_child_pid(sandbox: _Sandbox, pane_pid: int) -> None:
    """The pane's child PID reaches the script with leading spaces, as the case needs."""
    lines = sandbox.child_pid_lines(pane_pid)
    assert len(lines) == 1, f"the pane shell should have exactly one child: {lines!r}"
    assert lines[0] != lines[0].lstrip(), f"the child PID line must carry leading spaces: {lines[0]!r}"
    assert int(lines[0]) == sandbox.children(pane_pid)[0]


@pytest.mark.parametrize("left_pane", [CHILDLESS, PLAIN_GRANDCHILD])
def test_queues_grandchild_behind_a_padded_child_pid_identifies_its_pane(sandbox: _Sandbox, left_pane: str) -> None:
    _, queues_pid = sandbox.layout((left_pane, 0, 150), (QUEUES_GRANDCHILD, 150, 50))
    _assert_padded_child_pid(sandbox, queues_pid)

    result = sandbox.run()

    assert result.returncode == 0, _diagnostic(result)
    _assert_found_without_fallback(result)
    assert "Queues pane: index=1 pane_left=150 pane_width=50" in result.stdout, _diagnostic(result)
    assert "RESULT: PASS" in result.stdout, _diagnostic(result)


def test_queues_pane_found_through_a_padded_child_pid_is_the_one_geometry_checks(sandbox: _Sandbox) -> None:
    queues_pid, _ = sandbox.layout((QUEUES_GRANDCHILD, 0, 150), (CHILDLESS, 150, 50))
    _assert_padded_child_pid(sandbox, queues_pid)

    result = sandbox.run()

    assert result.returncode == 1, _diagnostic(result)
    _assert_found_without_fallback(result)
    assert "Queues pane: index=0 pane_left=0 pane_width=150" in result.stdout, _diagnostic(result)
    assert "FAIL: pane 1 has pane_left=150 which is >= queues pane_left=0." in result.stderr, _diagnostic(result)


def test_queues_process_directly_below_the_pane_shell_is_found_by_the_direct_child_check(sandbox: _Sandbox) -> None:
    sandbox.layout((CHILDLESS, 0, 150), (QUEUES_CHILD, 150, 50))

    result = sandbox.run()

    assert result.returncode == 0, _diagnostic(result)
    _assert_found_without_fallback(result)
    assert "Queues pane: index=1 pane_left=150 pane_width=50" in result.stdout, _diagnostic(result)
    assert "RESULT: PASS" in result.stdout, _diagnostic(result)


@pytest.mark.parametrize(
    ("right_width", "expected_exit", "verdict"),
    [(50, 0, "RESULT: PASS"), (100, 1, "FAIL: queues pane width=100 is NOT approximately 25% of 200.")],
)
def test_no_queues_process_falls_back_to_the_rightmost_pane_and_checks_its_width(
    sandbox: _Sandbox, right_width: int, expected_exit: int, verdict: str
) -> None:
    left_width = _WINDOW_WIDTH - right_width
    _, plain_pid = sandbox.layout((CHILDLESS, 0, left_width), (PLAIN_GRANDCHILD, left_width, right_width))
    _assert_padded_child_pid(sandbox, plain_pid)

    result = sandbox.run()

    assert result.returncode == expected_exit, _diagnostic(result)
    assert f"WARNING: {_NO_PANE_WARNING}." in result.stderr, _diagnostic(result)
    expected_fallback = f"INFO: {_FALLBACK_LINE}: index=1 pane_left={left_width} pane_width={right_width}"
    assert expected_fallback in result.stdout, _diagnostic(result)
    assert verdict in result.stdout + result.stderr, _diagnostic(result)


def test_pane_trees_leave_no_process_behind_after_teardown(tmp_path: pathlib.Path) -> None:
    guard = require_binary("ps", label="verify-window0-geometry pane search")
    box = _Sandbox(tmp_path, guard.path)
    try:
        pane_pids = box.layout((QUEUES_GRANDCHILD, 0, 150), (PLAIN_GRANDCHILD, 150, 50))
    finally:
        box.kill_all()

    def survivors() -> list[str]:
        """Live (non-zombie) processes still in any pane shell's session."""
        lines = [line for pid in pane_pids for line in box._ps("--sid", str(pid), "-o", "pid=,stat=")]
        return [line for line in lines if not line.split()[1].startswith("Z")]

    deadline = time.monotonic() + _READY_SECONDS
    while survivors() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert survivors() == [], f"processes outlived teardown: {survivors()!r}"
