"""
test_schedule_window_layout.py
==============================
The dashboard's schedule window (window 4) as create.sh really builds it:
two bands -- the per-agent list (status-band.sh) on top and the event band
(event-band.sh) below -- with no next-cron-firings.sh pane, and a list band
that keeps its header plus every roster row when the window shrinks.

tmux resizes a window's panes in proportion when the window changes size,
so a list sized once at creation used to be squeezed until only its last
few agents showed. The hooks lib/schedule_layout.sh installs re-apply the
list's height. These tests drive an explicit resize-window, a window
switch, a zoom, and -- the operator's own path -- a real client attached
through a pty at a smaller size, viewing the window for the first time and
then resizing its terminal live. A client-driven size change is applied by
tmux after the client and session hooks have already fired, so only the
pty-driven test proves the list survives it.

Every test runs the real team/scripts/dashboard/create.sh against a
sandbox kanban root under tmp_path, on a private tmux server (its own -L
socket under a short private TMUX_TMPDIR, reached through a PATH-shadowing
``tmux`` wrapper, with TMUX unset) with a unique session name -- never the
operator's live dashboard session or server. The crontab is the autouse
sandbox stub (team/tests/conftest.py), loaded with a seven-agent roster.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import pathlib
import pty
import select
import shlex
import shutil
import signal
import struct
import subprocess
import tempfile
import termios
import threading
import time
import uuid
from typing import Callable, Iterator, NamedTuple

import pytest

from tests.fixtures.binary_guard import narrate_binary_skip, try_resolve_binary
from tests.fixtures.process_progress import require_procfs, run_until_exit

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_CREATE_SH = _TEAM_DIR / "scripts" / "dashboard" / "create.sh"
_TMUX_SKIP_LABEL = "schedule-window-layout"

_ROSTER_CRONTAB = (
    "* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=pm     --sleep=0\n"
    "* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=coder  --sleep=12\n"
    "* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=writer --sleep=24\n"
    "* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=tester --sleep=36\n"
    "* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=cm     --sleep=48\n"
    "0 3 * * 4 $KANBAN_ROOT/scripts/wake-batch.sh --agent=overwatch\n"
    "0 4 * * 0 $KANBAN_ROOT/scripts/cleanup.sh\n"
)
_ROSTER_AGENTS = ("PM", "CM", "TESTER", "CODER", "WRITER", "CLEANUP", "OVERWATCH")
# Header row + one row per agent + one row of headroom (lib/schedule_layout.sh).
_LIST_ROWS = 1 + len(_ROSTER_AGENTS) + 1
_RENDER_WAIT_S = 90
_SETTLE_WAIT_S = 30


class _Dashboard(NamedTuple):
    tmux: str
    socket: str
    session: str
    env: dict

    def run(self, *args: str) -> subprocess.CompletedProcess:
        return run_until_exit([self.tmux, "-L", self.socket, *args], env=self.env, cmdline_marker=self.socket)

    def query(self, *args: str) -> str:
        result = self.run(*args)
        assert result.returncode == 0, f"tmux {' '.join(args)} failed: {result.stderr}"
        return result.stdout

    def pane_heights(self) -> list[int]:
        out = self.query("list-panes", "-t", f"{self.session}:schedule", "-F", "#{pane_height}")
        return [int(line) for line in out.split()]

    def list_text(self) -> str:
        return self.query("capture-pane", "-p", "-t", f"{self.session}:schedule.0")

    def window_size(self) -> tuple[int, int]:
        out = self.query("display-message", "-p", "-t", f"{self.session}:schedule",
                         "#{window_width} #{window_height}")
        width, height = out.split()
        return int(width), int(height)

    def zoomed(self) -> bool:
        return self.query("display-message", "-p", "-t", f"{self.session}:schedule",
                          "#{window_zoomed_flag}").strip() == "1"


def _visible_rows(text: str) -> list[str]:
    return [line.split()[0] for line in text.splitlines() if line.strip()]


def _wait_for_full_list(dash: _Dashboard) -> str:
    deadline = time.monotonic() + _RENDER_WAIT_S
    text = ""
    while time.monotonic() < deadline:
        text = dash.list_text()
        rows = _visible_rows(text)
        if rows[:1] == ["AGENT"] and all(agent in rows for agent in _ROSTER_AGENTS):
            return text
        time.sleep(0.5)
    pytest.fail(f"the list band never showed its header and every roster row:\n{text}")


def _wait_until(condition: Callable[[], bool], what: str, diagnostics: Callable[[], str]) -> None:
    deadline = time.monotonic() + _SETTLE_WAIT_S
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.2)
    pytest.fail(f"timed out waiting for {what}: {diagnostics()}")


def _wait_for_list_layout(dash: _Dashboard, min_event_rows: int = 1) -> tuple[list[int], str]:
    """Wait until the list band is back at its full height and shows the
    header plus every roster row, with the event band at least
    *min_event_rows* high; fail with the last layout seen otherwise."""
    deadline = time.monotonic() + _RENDER_WAIT_S
    heights: list[int] = []
    text = ""
    while time.monotonic() < deadline:
        heights = dash.pane_heights()
        text = dash.list_text()
        rows = _visible_rows(text)
        if (heights[0] == _LIST_ROWS and heights[1] >= min_event_rows
                and rows[:1] == ["AGENT"] and all(agent in rows for agent in _ROSTER_AGENTS)):
            return heights, text
        time.sleep(0.5)
    pytest.fail(
        f"the list band never kept its header and every roster row at window size "
        f"{dash.window_size()}: pane heights {heights} (want [{_LIST_ROWS}, >={min_event_rows}]), "
        f"list pane shows:\n{text}"
    )


def _set_winsize(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


@contextlib.contextmanager
def _attached_client(dash: _Dashboard, cols: int, rows: int) -> Iterator[tuple[int, int]]:
    """Attach a real tmux client to the dashboard through a pty sized
    *cols* x *rows*; yield (client pid, pty master fd) and detach after."""
    pid, fd = pty.fork()
    if pid == 0:  # pragma: no cover - child process
        try:
            _set_winsize(0, cols, rows)
            env = dict(dash.env, TERM="xterm-256color")
            os.execve(dash.tmux, [dash.tmux, "-L", dash.socket, "attach-session", "-t", dash.session], env)
        finally:
            os._exit(127)
    stop = threading.Event()

    def _drain() -> None:
        while not stop.is_set():
            try:
                ready, _, _ = select.select([fd], [], [], 0.1)
                if ready and not os.read(fd, 65536):
                    return
            except OSError:
                return

    drainer = threading.Thread(target=_drain, daemon=True)
    drainer.start()
    try:
        _wait_until(
            lambda: dash.run("display-message", "-p", "-t", dash.session, "#{session_attached}").stdout.strip()
            not in ("", "0"),
            "a client to attach",
            lambda: dash.run("list-clients").stdout,
        )
        yield pid, fd
    finally:
        dash.run("detach-client", "-s", dash.session)
        with contextlib.suppress(ChildProcessError):
            os.waitpid(pid, 0)
        stop.set()
        drainer.join(timeout=5)
        with contextlib.suppress(OSError):
            os.close(fd)


def _descendant_cmdlines(pid: int) -> list[str]:
    found: list[str] = []
    stack = [pid]
    while stack:
        current = stack.pop()
        try:
            found.append(pathlib.Path(f"/proc/{current}/cmdline").read_bytes().replace(b"\0", b" ").decode())
            for task in pathlib.Path(f"/proc/{current}/task").iterdir():
                stack.extend(int(c) for c in (task / "children").read_text().split())
        except OSError:
            continue
    return found


@pytest.fixture
def dashboard(tmp_path: pathlib.Path, pgai_tests_temp_dir: pathlib.Path) -> Iterator[_Dashboard]:
    guard = try_resolve_binary("tmux")
    socket = f"pgai-test-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    session = f"sched-layout-{uuid.uuid4().hex[:8]}"
    # A short private TMUX_TMPDIR: tmp_path can be long enough to push the
    # server socket's path past the kernel's unix-socket length limit.
    tmux_tmpdir = tempfile.mkdtemp(prefix="tmx", dir=str(pgai_tests_temp_dir))
    env: dict = {}
    try:
        if not guard.resolved:
            narrate_binary_skip("tmux", label=_TMUX_SKIP_LABEL)
        require_procfs(_TMUX_SKIP_LABEL)

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        wrapper = bin_dir / "tmux"
        wrapper.write_text(
            f"#!/usr/bin/env bash\nexec {shlex.quote(guard.path)} -L {shlex.quote(socket)} \"$@\"\n"
        )
        wrapper.chmod(0o755)

        root = tmp_path / "kanban"
        (root / "tmp").mkdir(parents=True)
        env = {k: v for k, v in os.environ.items() if k not in {"PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT", "TMUX"}}
        env.update(
            PATH=f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
            KANBAN_ROOT=str(root),
            PGAI_AGENT_KANBAN_ROOT_PATH=str(root),
            PGAI_AGENT_KANBAN_TEMP_DIR=str(root / "tmp"),
            TMUX_TMPDIR=tmux_tmpdir,
        )
        installed = subprocess.run(["crontab", "-"], input=_ROSTER_CRONTAB, text=True,
                                   capture_output=True, env=env, timeout=10)
        assert installed.returncode == 0, installed.stderr

        created = subprocess.run(
            ["bash", str(_CREATE_SH), "--kanban-root", str(root), "--session", session],
            env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=240,
        )
        # create.sh ends by attaching, which has no terminal here; everything
        # before it -- every window, the readiness handshake, the hooks -- ran.
        assert "Attaching to session" in created.stdout, created.stdout + created.stderr

        yield _Dashboard(guard.path, socket, session, env)
    finally:
        guard.teardown(
            lambda path: run_until_exit([path, "-L", socket, "kill-server"], env=env or None, cmdline_marker=socket)
        )
        shutil.rmtree(tmux_tmpdir, ignore_errors=True)


def test_schedule_window_is_two_bands_without_next_cron_firings(dashboard: _Dashboard) -> None:
    panes = dashboard.query(
        "list-panes", "-t", f"{dashboard.session}:schedule", "-F", "#{pane_index} #{pane_pid}"
    ).split("\n")
    panes = [line.split() for line in panes if line.strip()]

    assert [index for index, _ in panes] == ["0", "1"]
    _wait_for_full_list(dashboard)
    for index, pid in panes:
        cmdlines = " ".join(_descendant_cmdlines(int(pid)))
        assert "next-cron-firings.sh" not in cmdlines, f"pane {index} runs next-cron-firings.sh"
    commands = {index: " ".join(_descendant_cmdlines(int(pid))) for index, pid in panes}
    assert "status-band.sh" in commands["0"] and "event-band.sh" in commands["1"], commands


def test_list_band_keeps_every_roster_row_when_the_window_shrinks(dashboard: _Dashboard) -> None:
    _wait_for_full_list(dashboard)

    dashboard.query("resize-window", "-t", f"{dashboard.session}:schedule", "-x", "176", "-y", "35")
    heights = dashboard.pane_heights()
    text = _wait_for_full_list(dashboard)

    assert heights[0] == _LIST_ROWS, (heights, text)
    assert _visible_rows(text)[0] == "AGENT"

    dashboard.query("resize-window", "-t", f"{dashboard.session}:schedule", "-x", "176", "-y", "24")
    heights = dashboard.pane_heights()
    text = _wait_for_full_list(dashboard)

    assert heights[0] == _LIST_ROWS, (heights, text)
    assert heights[1] >= 5, f"the event band has fewer than 5 rows at 176x24: {heights}"


def test_switching_to_the_window_re_applies_the_list_height(dashboard: _Dashboard) -> None:
    """The window-switch hook restores the list after anything squeezed it.

    The window's own layout hook would undo the squeeze at once (see the
    next test), so it is unset here to leave the window-switch hook alone
    under test."""
    dashboard.query("set-hook", "-u", "-w", "-t", f"{dashboard.session}:schedule", "window-layout-changed")
    dashboard.query("resize-pane", "-t", f"{dashboard.session}:schedule.0", "-y", "3")
    assert dashboard.pane_heights()[0] == 3

    dashboard.query("select-window", "-t", f"{dashboard.session}:main")
    dashboard.query("select-window", "-t", f"{dashboard.session}:schedule")

    assert dashboard.pane_heights()[0] == _LIST_ROWS


def test_squeezing_the_list_by_hand_is_undone(dashboard: _Dashboard) -> None:
    """Any layout change to the window, a hand resize included, re-applies
    the list's height."""
    dashboard.query("resize-pane", "-t", f"{dashboard.session}:schedule.0", "-y", "3")

    _wait_until(lambda: dashboard.pane_heights()[0] == _LIST_ROWS, "the list band to return to its full height",
                lambda: str(dashboard.pane_heights()))


def test_attached_client_keeps_every_roster_row_on_first_view_and_live_resize(dashboard: _Dashboard) -> None:
    """A real client attached at 176x35 sees the full list the first time it
    views the window, and keeps it when its terminal shrinks to 176x24."""
    _wait_for_full_list(dashboard)
    assert dashboard.window_size() == (220, 50)

    with _attached_client(dashboard, 176, 35) as (client_pid, fd):
        dashboard.query("select-window", "-t", f"{dashboard.session}:schedule")
        _wait_until(lambda: dashboard.window_size()[0] == 176, "the window to take the client's 176-column width",
                    lambda: str(dashboard.window_size()))
        heights, text = _wait_for_list_layout(dashboard)
        assert dashboard.window_size()[1] <= 35, (dashboard.window_size(), heights)

        _set_winsize(fd, 176, 24)
        os.kill(client_pid, signal.SIGWINCH)
        _wait_until(lambda: dashboard.window_size()[1] <= 24, "the window to shrink to the 24-row terminal",
                    lambda: str(dashboard.window_size()))
        heights, text = _wait_for_list_layout(dashboard, min_event_rows=5)

    assert _visible_rows(text)[0] == "AGENT"
    assert heights[1] >= 5, f"the event band has fewer than 5 rows at 176x24: {heights}"


def test_zooming_a_schedule_pane_is_not_undone(dashboard: _Dashboard) -> None:
    """Zooming is a layout change, and resize-pane unzooms: the layout hook
    leaves a zoomed window alone, and re-applies the height on unzoom."""
    dashboard.query("resize-pane", "-Z", "-t", f"{dashboard.session}:schedule.1")
    # A few server round-trips let any queued hook run before the check.
    for _ in range(5):
        dashboard.pane_heights()
    assert dashboard.zoomed(), "zooming the event band was undone"

    dashboard.query("resize-pane", "-Z", "-t", f"{dashboard.session}:schedule.1")
    assert not dashboard.zoomed()
    assert dashboard.pane_heights()[0] == _LIST_ROWS
