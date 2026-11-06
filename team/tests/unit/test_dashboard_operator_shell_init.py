"""
test_dashboard_operator_shell_init.py
=====================================
Behavioural coverage for the operator window's two interactive shell panes
-- the cmdline row and the workbench column -- as started by
dashboard_init_operator_shell
(team/scripts/dashboard/lib/operator_shell.sh) from the real
team/scripts/dashboard/create.sh, and for create.sh's
[dashboard] operator_workbench_pct / operator_workbench_min_cols /
operator_board_agents handling.

It also covers the configuration create.sh, kill.sh and attach.sh resolve
for themselves:
  - the tmux session name, as --session, then PGAI_DASHBOARD_SESSION_NAME,
    then kanban.cfg [dashboard] session_name, then pgai-kanban-dashboard
    (dashboard/lib/session_name.sh, which all of them source);
  - the rows each board column renders, as DASHBOARD_ROWS_PER_COLUMN, then
    kanban.cfg [dashboard] rows_per_column, then 21 — the default
    kanban.cfg_example documents.

After create.sh returns with the default config on a 220-column session,
the cmdline pane and the workbench pane must each show a clean screen --
the shell's prompt on one line and nothing else, no start-up line the
dashboard typed left above it -- and hold a resident shell that:
  - has PGAI_AGENT_KANBAN_ROOT_PATH set to the kanban root,
  - sits in the kanban root,
  - resolves an alias defined only in the test ~/.bashrc (the guarded
    bashrc source ran in the resident shell, not in the exec'd-away one),
  - ignores the pane signal (`trap -p` shows the re-armed ignore trap),
  - carries shell-env's value for a variable ~/.bashrc also sets (shell-env
    is sourced after bashrc),
  - holds in its own history each line the dashboard typed into it (the
    re-arm, the ~/.bashrc and shell-env sources, the screen clear), every one
    ending with the typed-line source tag.
Every line create.sh types into any pane -- each pane's wrapped first
command, the terminal and operator-shell re-arms, and the operator shells'
follow-up lines -- ends with that tag, as recorded by the tmux wrapper.
the window must hold the default (merged) board's four columns, the
cmdline pane titled "operator", the workbench pane titled "workbench" and
the legend, and the window's active pane must be the cmdline pane.

With operator_workbench_pct = 0, and separately on a 120-column session
under the default 160-column threshold, create.sh must build no workbench
and still complete its all-armed handshake; the narrow run's output must
carry the one notice line naming both widths. Without a workbench the
operator window and the visibility window must each still carry their own
board-reflow.sh layout hook, the session hooks beside them intact. An invalid value for either
key must fall back to its default with one warning line naming the key.

A real client attached through a pty to the session create.sh built (220
columns, shipped defaults): from a 120-column terminal, narrower than the
default 160-column threshold, the operator window shows no workbench --
the pane waits in a window of its own named "workbench" -- and has exactly
the geometry the board builder gives with no workbench at the window's
size; widening that terminal brings the workbench back. A client 180
columns wide, at least the threshold, keeps the workbench, with the
geometry the builder gives at its size.

The operator board's column set ([dashboard] operator_board_agents):
  - absent, the board is BUGS, PRIORITIES, REQUIREMENTS and AGENTS, pane 3
    running column-render.sh --column-type agents under cadence-loop.sh,
    with the merged golden geometry (tests/fixtures/board_window_geometry.py);
  - split, the window is the eleven panes whose columns and legend run
    window 2's commands, with the eight-column golden geometry;
  - under either value window 2 keeps its nine panes, its eight column
    commands and its golden geometry;
  - an invalid value builds the merged board and prints exactly one warning
    line naming the key; merged and split print none.
Which command each pane was started with is read from the tmux wrapper's
record of create.sh's send-keys calls: a detached session has no client,
so its column panes pause before their first render and show nothing to
capture.

Isolation (live-install safety): create.sh runs only against a private
tmux server (tmux -L <unique socket>, reached through a PATH-prepended
`tmux` wrapper, the pattern test_dashboard_pane_signal_traps.py's
end-to-end test uses), a throwaway kanban root and kanban temp dir under
tmp_path, and a throwaway HOME holding the test ~/.bashrc. The operator's
default tmux socket, live install and real dotfiles are never touched.
Every process create.sh starts -- inside the private server or, like the
status-bottom loop it backgrounds, outside it -- inherits a marker naming
the private socket; the fixture's teardown stops the server and kills every
process still carrying the marker, and a test here proves none is alive
once that teardown completes.
create.sh always creates its session 220 columns wide; for the narrow-window
run the wrapper rewrites that one `new-session -x` value (and `-y`, for the
window 2 golden size), which is the only way to give a detached session a
different size. The test narrates a skip when tmux is not on PATH.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import pathlib
import pty
import select
import shlex
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
import uuid
from typing import NamedTuple
from collections.abc import Iterator

import pytest

from tests.fixtures.binary_guard import (
    narrate_binary_skip,
    teardown_private_tmux,
    try_resolve_binary,
)
from tests.fixtures.process_progress import (
    proc_stat_fields,
    require_procfs,
    wait_while_progressing,
)

_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/tests/unit/ -> team/
_CREATE_SH = _TEAM_DIR / "scripts" / "dashboard" / "create.sh"
_KILL_SH = _TEAM_DIR / "scripts" / "dashboard" / "kill.sh"
_ATTACH_SH = _TEAM_DIR / "scripts" / "dashboard" / "attach.sh"
_DETACH_SH = _TEAM_DIR / "scripts" / "dashboard" / "detach.sh"
_SESSION_NAME_LIB = _TEAM_DIR / "scripts" / "dashboard" / "lib" / "session_name.sh"
_BOARD_WINDOW_SH = _TEAM_DIR / "scripts" / "dashboard" / "lib" / "board_window.sh"
_DASHBOARD_CONSTANTS_SH = _TEAM_DIR / "scripts" / "lib" / "dashboard_constants.sh"

if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.dashboard.window_census import Window, load_census  # noqa: E402
from tests.fixtures.board_window_geometry import (  # noqa: E402
    OPERATOR_MERGED_GEOMETRY,
    OPERATOR_SPLIT_GEOMETRY_WITH_WORKBENCH_220X50,
    VISIBILITY_GEOMETRY,
)

_TMUX_SKIP_LABEL = "operator-shell-init"

# The pane signal create.sh's panes ignore; pinned so the trap assertion
# does not depend on whatever the invoking shell exported.
_PANE_SIGNAL = "USR1"

# A name the test ~/.bashrc defines and nothing else on the host does.
_BASHRC_ALIAS = "pgai_operator_shell_test_alias"
_BASHRC_ALIAS_OUTPUT = "operator-shell-alias-resolved"

# A variable both ~/.bashrc and shell-env set; shell-env's value must win.
_ORDER_VAR = "PGAI_OPERATOR_SHELL_ORDER_PROBE"

_PROBE_DONE = "PROBE-DONE"

# The trailing comment every line the dashboard types into a pane ends with,
# pinned as the operator-facing contract a shell-history grep relies on.
_TYPED_TAG = " # pgai-agent-kanban-dashboard"

# The lines dashboard_init_operator_shell types into an operator shell pane,
# in order, each identified by a fragment of its command: the wrapped first
# command, the re-arm, the guarded ~/.bashrc source, the shell-env re-source
# and the screen clear. The resident shell runs (and records) all but the
# first.
_OPERATOR_SHELL_LINE_FRAGMENTS = ("exec bash -l'", ": > ", ".bashrc", "/shell-env", "printf '\\033[H")

# A fragment of each start-up line create.sh types into an operator shell
# pane: the wrapped first command, the trap re-arm, the guarded ~/.bashrc
# source and the shell-env re-source. None may be left on screen.
_STARTUP_LINE_FRAGMENTS = ("exec bash -l", "trap ''", ".bashrc", "shell-env")

# The size create.sh gives every session it creates, and the workbench and
# board defaults create.sh applies when kanban.cfg says nothing.
_SESSION_COLS = 220
_SESSION_ROWS = 50
_DEFAULT_WORKBENCH_PCT = 40
_DEFAULT_WORKBENCH_MIN_COLS = 160
_DEFAULT_BOARD_AGENTS = "merged"

# The session name the dashboard scripts fall back to, and the rows each
# board column renders when neither DASHBOARD_ROWS_PER_COLUMN nor
# [dashboard] rows_per_column says otherwise (kanban.cfg_example's default).
_DEFAULT_SESSION_NAME = "pgai-kanban-dashboard"
_DEFAULT_ROWS_PER_COLUMN = "21"

# The tmux wrapper rewrites `new-session -x <cols>` / `-y <rows>` to these
# variables' values when they are set (the narrow-window and window 2
# golden-size runs).
_SESSION_COLS_ENV = "TEST_TMUX_NEW_SESSION_COLS"
_SESSION_ROWS_ENV = "TEST_TMUX_NEW_SESSION_ROWS"

# The tmux wrapper appends every send-keys call's arguments here: each
# argument followed by a unit separator, each call followed by a record
# separator.
_SEND_LOG_ENV = "TEST_TMUX_SEND_LOG"
_ARG_SEP = "\x1f"
_CALL_SEP = "\x1e"

# Set to the private socket name in the env every test subprocess inherits,
# so it reaches the tmux server, every pane, and whatever create.sh starts
# outside tmux; teardown reaps every live process carrying it.
_SOCKET_MARKER_ENV = "TEST_TMUX_PRIVATE_SOCKET"

# The board column labels each column set's panes 0.. carry, and window 2's.
_MERGED_LABELS = ["BUGS", "PRIORITIES", "REQUIREMENTS", "AGENTS"]
_SPLIT_LABELS = ["BUGS", "PRIORITIES", "REQUIREMENTS", "PM", "CODER", "WRITER", "TESTER", "CM"]


def _operator_window(variant: str = _DEFAULT_BOARD_AGENTS) -> Window:
    """The census's operator window record for one board column set."""
    (operator_window,) = [w for w in load_census() if w.name == "operator" and w.variant == variant]
    return operator_window


def _operator_pane_index(script: str, variant: str = _DEFAULT_BOARD_AGENTS) -> int:
    """An operator window pane's index in the census's wide layout, matched
    by its populating-script label rather than a hardcoded number."""
    (pane,) = [p for p in _operator_window(variant).panes if p.script == script]
    return pane.pane_seq


def _cmdline_pane_index(variant: str = _DEFAULT_BOARD_AGENTS) -> int:
    return _operator_pane_index("(interactive bash, kanban shell-env sourced)", variant)


def _workbench_pane_index(variant: str = _DEFAULT_BOARD_AGENTS) -> int:
    return _operator_pane_index("none (interactive shell)", variant)


def _operator_pane_count(variant: str = _DEFAULT_BOARD_AGENTS) -> int:
    return _operator_window(variant).pane_count


class PrivateTmux(NamedTuple):
    """A private tmux server and the env every subprocess must inherit to
    land on it (create.sh calls the bare `tmux` word). *send_log* is where
    the wrapper records every send-keys call."""

    real_tmux: str
    socket_name: str
    env: dict[str, str]
    send_log: pathlib.Path

    def run(self, *args: str, timeout: float = 15) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.real_tmux, "-L", self.socket_name, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )


def _processes_launched_under(socket_name: str) -> list[tuple[int, str]]:
    """(pid, command line) of every live process whose environment carries
    the private server's socket marker: the tmux server, its panes and
    everything they run, and whatever create.sh started outside tmux. A
    zombie has already exited and is left out; so is any process this user
    cannot read. Empty on a host without /proc."""
    proc = pathlib.Path("/proc")
    if not (proc / "self" / "environ").exists():
        return []
    needle = f"{_SOCKET_MARKER_ENV}={socket_name}".encode()
    found = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            environ = (entry / "environ").read_bytes()
            if needle not in environ.split(b"\0"):
                continue
            cmdline = (entry / "cmdline").read_bytes()
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        fields = proc_stat_fields(int(entry.name))
        if fields is None or fields[0] in ("Z", "X", "x"):
            continue
        found.append((int(entry.name), cmdline.replace(b"\0", b" ").decode(errors="replace").strip()))
    return sorted(found)


def _reap_processes_launched_under(socket_name: str) -> None:
    """SIGKILL every live process carrying *socket_name*'s marker, rescanning
    until none is left, so a process that forked between a scan and its
    kill is caught by the next scan. Ends on the processes being gone; fails
    only if the set of survivors stops changing for the stall bound."""

    def _none_left() -> bool:
        survivors = _processes_launched_under(socket_name)
        for pid, _cmdline in survivors:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGKILL)
        return not survivors

    wait_while_progressing(
        _none_left,
        lambda: _processes_launched_under(socket_name),
        what=f"reaping the processes started under tmux socket {socket_name}",
        on_stall="a process survived SIGKILL; it is stuck in the kernel, not slow",
    )


def _stop_private_tmux(real_tmux: str, socket_name: str) -> None:
    """Stop the private server, then reap every process started under it
    that is still alive: a pane child that ignored the hangup, or a process
    create.sh backgrounded outside tmux, which only notices the server is
    gone on its next wake and could write into this test's tmp_path after
    pytest has handed the same directory name to the next test."""
    subprocess.run([real_tmux, "-L", socket_name, "kill-server"], capture_output=True, timeout=10)
    _reap_processes_launched_under(socket_name)


@contextlib.contextmanager
def _private_tmux_server(tmp_path: pathlib.Path) -> Iterator[PrivateTmux]:
    """A `tmux` wrapper on PATH that pins every call to a private socket.
    The wrapper calls the real tmux by absolute path; a bare `tmux` inside
    it would resolve back to the wrapper and recurse. When
    TEST_TMUX_NEW_SESSION_COLS / TEST_TMUX_NEW_SESSION_ROWS is set it
    replaces the `-x` / `-y` value of a `new-session` call, so a test can
    give create.sh's session another size. Every send-keys call's arguments
    are appended to TEST_TMUX_SEND_LOG. The env carries the socket marker,
    and leaving the block stops the server and reaps every process that
    carries it (_stop_private_tmux)."""
    guard = try_resolve_binary("tmux")
    socket_name = f"pgai-test-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    try:
        if not guard.resolved:
            narrate_binary_skip("tmux", label=_TMUX_SKIP_LABEL)
        real_tmux = guard.path

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        wrapper = bin_dir / "tmux"
        send_log = tmp_path / "send-keys.log"
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            'if [[ "${1:-}" == new-session ]]; then\n'
            "    args=() prev=''\n"
            '    for arg in "$@"; do\n'
            f'        [[ "$prev" == -x && -n "${{{_SESSION_COLS_ENV}:-}}" ]] && arg="${{{_SESSION_COLS_ENV}}}"\n'
            f'        [[ "$prev" == -y && -n "${{{_SESSION_ROWS_ENV}:-}}" ]] && arg="${{{_SESSION_ROWS_ENV}}}"\n'
            '        args+=("$arg"); prev="$arg"\n'
            "    done\n"
            '    set -- "${args[@]}"\n'
            "fi\n"
            f'if [[ "${{1:-}}" == send-keys && -n "${{{_SEND_LOG_ENV}:-}}" ]]; then\n'
            f"    {{ printf '%s\\x1f' \"$@\"; printf '\\x1e'; }} >> \"${{{_SEND_LOG_ENV}}}\"\n"
            "fi\n"
            f'exec {shlex.quote(real_tmux)} -L {shlex.quote(socket_name)} "$@"\n'
        )
        wrapper.chmod(0o755)

        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
        env.pop(_SESSION_COLS_ENV, None)
        env.pop(_SESSION_ROWS_ENV, None)
        env[_SEND_LOG_ENV] = str(send_log)
        env[_SOCKET_MARKER_ENV] = socket_name
        yield PrivateTmux(real_tmux=real_tmux, socket_name=socket_name, env=env, send_log=send_log)
    finally:
        teardown_private_tmux(guard, socket_name, kill=lambda path: _stop_private_tmux(path, socket_name))


@pytest.fixture
def private_tmux(tmp_path: pathlib.Path) -> Iterator[PrivateTmux]:
    """A private tmux server for one test (_private_tmux_server); no process
    started under it outlives the test's teardown."""
    with _private_tmux_server(tmp_path) as server:
        yield server


class Sandbox(NamedTuple):
    kanban_root: pathlib.Path
    env: dict[str, str]
    session: str


def _sandbox(
    private_tmux: PrivateTmux,
    tmp_path: pathlib.Path,
    kanban_cfg: str | None = None,
    session_cols: int | None = None,
    session_rows: int | None = None,
    extra_env: dict[str, str] | None = None,
) -> Sandbox:
    """A throwaway kanban root (shell-env, optional kanban.cfg), kanban temp
    dir and HOME (the test ~/.bashrc), and create.sh's environment: both
    kanban roots and the temp dir point into the sandbox, the two
    pre-resolved exports that would outrank them are cleared, HOME is the
    throwaway home, and no outer tmux client leaks in.
    PGAI_DASHBOARD_SESSION_NAME and DASHBOARD_ROWS_PER_COLUMN are cleared
    too, so only *extra_env* sets them; the session name reaches create.sh
    through --session alone (_run_create). All three live in a directory
    made fresh for this call, a name no earlier test on the worker can have
    used, whatever pytest numbered tmp_path."""
    sandbox_dir = pathlib.Path(tempfile.mkdtemp(prefix="sandbox-", dir=tmp_path))
    kanban_root = sandbox_dir / "kanban"
    kanban_root.mkdir()
    kanban_temp = sandbox_dir / "kanban-temp"
    kanban_temp.mkdir()
    home = sandbox_dir / "home"
    home.mkdir()

    (home / ".bashrc").write_text(
        f"alias {_BASHRC_ALIAS}='echo {_BASHRC_ALIAS_OUTPUT}'\n" f"export {_ORDER_VAR}=from-bashrc\n"
    )
    (kanban_root / "shell-env").write_text(
        f"export PGAI_AGENT_KANBAN_ROOT_PATH={shlex.quote(str(kanban_root))}\n" f"export {_ORDER_VAR}=from-shell-env\n"
    )
    if kanban_cfg is not None:
        (kanban_root / "kanban.cfg").write_text(kanban_cfg)

    session = f"opshell-{uuid.uuid4().hex[:6]}"
    env = dict(private_tmux.env)
    for name in (
        "PGAI_TASKS_DIR",
        "PGAI_PROJECT_ROOT",
        "PGAI_PROJECT_NAME",
        "TMUX",
        "TMUX_PANE",
        "PGAI_DASHBOARD_SESSION_NAME",
        "DASHBOARD_ROWS_PER_COLUMN",
    ):
        env.pop(name, None)
    env.update(
        {
            "KANBAN_ROOT": str(kanban_root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(kanban_temp),
            "HOME": str(home),
            "TERM": "xterm-256color",
            "PGAI_DASHBOARD_PANE_SIGNAL": _PANE_SIGNAL,
        }
    )
    if extra_env:
        env.update(extra_env)
    if session_cols is not None:
        env[_SESSION_COLS_ENV] = str(session_cols)
    if session_rows is not None:
        env[_SESSION_ROWS_ENV] = str(session_rows)
    return Sandbox(kanban_root=kanban_root, env=env, session=session)


def _run_create(private_tmux: PrivateTmux, tmp_path: pathlib.Path, sandbox: Sandbox, *extra_args: str) -> str:
    """Run the real create.sh in *sandbox*; return its combined output. For a
    session run, assert the all-armed handshake did not refuse and the
    session exists."""
    result = subprocess.run(
        ["bash", str(_CREATE_SH), "--kanban-root", str(sandbox.kanban_root), "--session", sandbox.session, *extra_args],
        capture_output=True,
        text=True,
        timeout=90,
        env=sandbox.env,
        cwd=str(tmp_path),
        stdin=subprocess.DEVNULL,
    )
    output = f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    if extra_args:
        return output

    # The final exec-attach fails headless ("open terminal failed"); the
    # session already exists detached by then. What must not appear is the
    # all-armed handshake refusal.
    assert "dashboard_create.pane_arm_timeout" not in output, f"create.sh refused the all-armed handshake:\n{output}"
    assert (
        private_tmux.run("has-session", "-t", sandbox.session).returncode == 0
    ), f"expected the dashboard session to exist after create.sh ran:\n{output}"
    return output


class Pane(NamedTuple):
    index: int
    title: str
    width: int


def _operator_panes(private_tmux: PrivateTmux, session: str) -> list[Pane]:
    listed = private_tmux.run(
        "list-panes", "-t", f"{session}:operator", "-F", "#{pane_index}\t#{pane_title}\t#{pane_width}"
    )
    assert listed.returncode == 0, listed.stderr
    panes = []
    for line in listed.stdout.splitlines():
        index, title, width = line.split("\t")
        panes.append(Pane(int(index), title, int(width)))
    return panes


def _active_operator_pane(private_tmux: PrivateTmux, session: str) -> tuple[str, str]:
    active = private_tmux.run("display-message", "-p", "-t", f"{session}:operator", "#{pane_index}\t#{pane_title}")
    assert active.returncode == 0, active.stderr
    index, _, title = active.stdout.rstrip("\n").partition("\t")
    return index, title


def _pane_geometry(private_tmux: PrivateTmux, session: str, window: str) -> list[tuple[int, ...]]:
    """(pane_index, pane_left, pane_top, pane_width, pane_height) per pane, the
    shape tests/fixtures/board_window_geometry.py records its goldens in."""
    listed = private_tmux.run(
        "list-panes",
        "-t",
        f"{session}:{window}",
        "-F",
        "#{pane_index} #{pane_left} #{pane_top} #{pane_width} #{pane_height}",
    )
    assert listed.returncode == 0, listed.stderr
    return [tuple(int(v) for v in line.split()) for line in listed.stdout.splitlines()]


def _typed_lines(private_tmux: PrivateTmux) -> list[tuple[str, str]]:
    """Every (target, typed line) create.sh sent with `send-keys -t <target>
    <line> Enter`, in send order, from the wrapper's record."""
    text = private_tmux.send_log.read_text() if private_tmux.send_log.exists() else ""
    typed = []
    for call in text.split(_CALL_SEP):
        args = call.split(_ARG_SEP)
        if len(args) < 4 or args[0] != "send-keys" or args[1] != "-t":
            continue
        typed.append((args[2], args[3]))
    return typed


def _first_pane_commands(private_tmux: PrivateTmux, session: str, window: str) -> dict[int, str]:
    """The first command create.sh typed into each pane of *window*, from the
    wrapper's send-keys record, cut to the pane's own command: everything
    from its cadence-loop.sh (the armed-wrapper preamble before it names the
    pane's marker file, so it differs between windows). A pane whose first
    command runs no cadence loop (an interactive shell) keeps the whole
    typed line."""
    target_prefix = f"{session}:{window}."
    first: dict[int, str] = {}
    for target, command in _typed_lines(private_tmux):
        if not target.startswith(target_prefix):
            continue
        pane = int(target[len(target_prefix) :])
        if pane in first:
            continue
        loop_at = command.find("/cadence-loop.sh ")
        if loop_at != -1:
            command = command[command.rfind(" ", 0, loop_at) + 1 :]
        first[pane] = command
    return first


def _label(command: str) -> str | None:
    """The --label a board column command passes column-render.sh."""
    words = command.split()
    if "--label" not in words:
        return None
    return words[words.index("--label") + 1]


def _board_agents_warning_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.startswith("WARNING:") and "operator_board_agents" in line]


def _assert_merged_board(private_tmux: PrivateTmux, sandbox: Sandbox, output: str) -> None:
    """The operator window is the merged board — BUGS, PRIORITIES,
    REQUIREMENTS, AGENTS — then the cmdline, the workbench and the legend,
    at the merged golden geometry, with the cmdline pane active."""
    commands = _first_pane_commands(private_tmux, sandbox.session, "operator")
    geometry = _pane_geometry(private_tmux, sandbox.session, "operator")
    context = f"commands: {commands}\ngeometry: {geometry}\n{output}"

    assert len(geometry) == _operator_pane_count("merged") == 7, context
    assert [_label(commands.get(i, "")) for i in range(4)] == _MERGED_LABELS, context
    for i in range(4):
        assert "/cadence-loop.sh --interval " in commands[i], context
        assert "/column-render.sh " in commands[i], context
        assert f"--all-projects --kanban-root {sandbox.kanban_root} " in commands[i], context
    agents = commands[3].split()
    assert agents[agents.index("--column-type") + 1] == "agents", context
    assert agents[agents.index("--") + 2] == "queue", context

    assert (
        geometry
        == OPERATOR_MERGED_GEOMETRY[
            (_SESSION_COLS, _SESSION_ROWS, f"{_DEFAULT_WORKBENCH_PCT} {_DEFAULT_WORKBENCH_MIN_COLS}")
        ]
    ), context
    panes = _operator_panes(private_tmux, sandbox.session)
    assert panes[_cmdline_pane_index("merged")].title == "operator", context
    assert panes[_workbench_pane_index("merged")].title == "workbench", context
    assert _active_operator_pane(private_tmux, sandbox.session)[0] == str(_cmdline_pane_index("merged")), context
    legend = _first_pane_commands(private_tmux, sandbox.session, "visibility")[8]
    assert commands[6] == legend, f"the operator legend must run window 2's legend command:\n{context}"


def _assert_visibility_board(private_tmux: PrivateTmux, sandbox: Sandbox, size: tuple[int, int], output: str) -> None:
    """Window 2 is its nine panes — the eight role and input columns at its
    own --board-widths, then the legend — at its golden geometry."""
    commands = _first_pane_commands(private_tmux, sandbox.session, "visibility")
    geometry = _pane_geometry(private_tmux, sandbox.session, "visibility")
    context = f"commands: {commands}\ngeometry: {geometry}\n{output}"
    assert sorted(commands) == list(range(9)), context
    assert [_label(commands[i]) for i in range(8)] == _SPLIT_LABELS, context
    for i in range(8):
        assert commands[i].endswith(f" --board-widths input=29,queue=26{_TYPED_TAG}"), context
        assert "--column-type" not in commands[i], context
    assert "/legend-render.sh --kanban-root " in commands[8], context
    assert geometry == VISIBILITY_GEOMETRY[size], context


def _wait_for_probe(probe_out: pathlib.Path, deadline_seconds: float) -> str:
    """Poll for the probe output's final marker line; return the text."""
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        if probe_out.exists():
            text = probe_out.read_text()
            if _PROBE_DONE in text:
                return text
        time.sleep(0.1)
    return probe_out.read_text() if probe_out.exists() else ""


def _assert_clean_prompt(private_tmux: PrivateTmux, sandbox: Sandbox, pane: int, label: str) -> None:
    """Operator pane *pane* settles on one non-blank screen line -- the
    prompt -- carrying no start-up line. Polled, because create.sh returns
    once the panes are armed, before the shell has run its last start-up
    line; checked before anything else is typed into the pane."""
    target = f"{sandbox.session}:operator.{pane}"
    deadline = time.monotonic() + 20
    lines: list[str] = []
    capture = ""
    while time.monotonic() < deadline:
        capture = private_tmux.run("capture-pane", "-p", "-t", target).stdout
        lines = [line for line in capture.splitlines() if line.strip()]
        if len(lines) == 1:
            break
        time.sleep(0.1)
    context = f"{label} pane {pane} capture:\n{capture}"
    assert len(lines) == 1, (
        f"expected the {label} pane to show only its prompt, with no start-up " f"line above it:\n{context}"
    )
    left = [fragment for fragment in _STARTUP_LINE_FRAGMENTS if fragment in lines[0]]
    assert not left, f"the {label} pane's one screen line is a start-up line ({left}):\n{context}"


def _assert_resident_shell_initialised(
    private_tmux: PrivateTmux, tmp_path: pathlib.Path, sandbox: Sandbox, pane: int, label: str
) -> None:
    """Probe the resident shell in operator pane *pane*: typed after
    create.sh's own follow-ups, so it runs once they have."""
    probe_out = tmp_path / f"probe-{label}.out"
    probe = (
        "{ "
        'echo "ROOT=$PGAI_AGENT_KANBAN_ROOT_PATH"; '
        'echo "PWD=$(pwd)"; '
        f'echo "ORDER=${_ORDER_VAR}"; '
        f"{_BASHRC_ALIAS}; "
        f"trap -p {_PANE_SIGNAL}; "
        "history | sed 's/^ *[0-9]* *//; s/^/HISTORY: /'; "
        f"echo {_PROBE_DONE}; "
        f"}} > {shlex.quote(str(probe_out))} 2>&1"
    )
    target = f"{sandbox.session}:operator.{pane}"
    sent = private_tmux.run("send-keys", "-t", target, probe, "Enter")
    assert sent.returncode == 0, sent.stderr

    text = _wait_for_probe(probe_out, deadline_seconds=20)
    capture = private_tmux.run("capture-pane", "-p", "-t", target).stdout
    context = f"{label} pane {pane} probe output:\n{text}\npane capture:\n{capture}"
    assert _PROBE_DONE in text, f"the {label} pane never ran the probe:\n{context}"

    kanban_root = sandbox.kanban_root
    lines = text.splitlines()
    assert f"ROOT={kanban_root}" in lines, f"PGAI_AGENT_KANBAN_ROOT_PATH is not the kanban root:\n{context}"
    assert f"PWD={kanban_root}" in lines, f"the shell's cwd is not the kanban root:\n{context}"
    assert _BASHRC_ALIAS_OUTPUT in lines, (
        f"the alias defined only in the test ~/.bashrc did not resolve -- the "
        f"guarded bashrc source did not run in the resident shell:\n{context}"
    )
    assert "ORDER=from-shell-env" in lines, (
        f"shell-env's value did not win over ~/.bashrc's -- shell-env must be " f"sourced after bashrc:\n{context}"
    )
    trap_lines = [line for line in lines if line.startswith("trap -- ")]
    assert trap_lines and trap_lines[0] == f"trap -- '' SIG{_PANE_SIGNAL}", (
        f"expected `trap -p` to show {_PANE_SIGNAL} ignored in the resident " f"shell:\n{context}"
    )
    # The resident shell's history: the four lines the dashboard typed into
    # it, in order and each tagged, then this probe (recorded as it runs).
    history = [line.removeprefix("HISTORY: ") for line in lines if line.startswith("HISTORY: ")]
    expected_fragments = _OPERATOR_SHELL_LINE_FRAGMENTS[1:]
    assert len(history) == len(expected_fragments) + 1 and _PROBE_DONE in history[-1], (
        f"expected the resident shell's history to hold the {len(expected_fragments)} "
        f"lines the dashboard typed into it, then the probe:\n{context}"
    )
    for fragment, line in zip(expected_fragments, history):
        assert fragment in line and line.endswith(_TYPED_TAG), (
            f"expected history line {line!r} to be the dashboard's {fragment!r} line "
            f"ending with {_TYPED_TAG!r}:\n{context}"
        )


def _assert_every_typed_line_tagged(private_tmux: PrivateTmux, sandbox: Sandbox, output: str) -> None:
    """Every line create.sh typed into any pane ends with the typed-line
    source tag; each operator shell pane got exactly the five
    dashboard_init_operator_shell lines, in order; the terminal window's
    shell got its wrapped first command, then its re-arm."""
    typed = _typed_lines(private_tmux)
    context = "typed lines:\n" + "\n".join(f"{t}: {c}" for t, c in typed) + f"\n{output}"
    assert typed, f"the tmux wrapper recorded no send-keys call:\n{context}"
    untagged = [(target, line) for target, line in typed if not line.endswith(_TYPED_TAG)]
    assert not untagged, f"typed line(s) without the source tag {_TYPED_TAG!r}: {untagged}\n{context}"

    for pane in (_cmdline_pane_index(), _workbench_pane_index()):
        target = f"{sandbox.session}:operator.{pane}"
        lines = [line for t, line in typed if t == target]
        assert len(lines) == len(_OPERATOR_SHELL_LINE_FRAGMENTS) and all(
            fragment in line for fragment, line in zip(_OPERATOR_SHELL_LINE_FRAGMENTS, lines)
        ), f"operator shell pane {pane} lines {lines}:\n{context}"
        assert lines[1].startswith(f"trap '' {_PANE_SIGNAL}; : > "), context

    terminal = [line for t, line in typed if t == f"{sandbox.session}:terminal.1"]
    assert len(terminal) == 2 and terminal[0].startswith(f"trap '' {_PANE_SIGNAL}; cd "), context
    assert terminal[1].startswith(f"trap '' {_PANE_SIGNAL}; : > "), context


def _assert_no_workbench(private_tmux: PrivateTmux, sandbox: Sandbox, output: str) -> None:
    """The operator window is the board, the cmdline row and the legend, with
    the cmdline pane active and the board and cmdline row at full width."""
    panes = _operator_panes(private_tmux, sandbox.session)
    context = f"panes: {panes}\n{output}"
    assert len(panes) == _operator_pane_count() - 1, (
        f"expected the operator window without a workbench " f"({_operator_pane_count() - 1} panes):\n{context}"
    )
    assert all(
        p.title != "workbench" for p in panes
    ), f"a pane is titled 'workbench' although no workbench should be built:\n{context}"
    cmdline = panes[_cmdline_pane_index()]
    assert cmdline.title == "operator", f"expected the cmdline pane titled 'operator':\n{context}"
    window_cols = int(
        private_tmux.run("display-message", "-p", "-t", f"{sandbox.session}:operator", "#{window_width}").stdout.strip()
    )
    assert (
        cmdline.width == window_cols
    ), f"expected the cmdline row to keep the full {window_cols}-column width:\n{context}"
    assert _active_operator_pane(private_tmux, sandbox.session)[0] == str(
        _cmdline_pane_index()
    ), f"expected the cmdline pane to be the operator window's active pane:\n{context}"


def _workbench_warning_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.startswith("WARNING:") and "operator_workbench_" in line]


def test_cmdline_and_workbench_shells_are_initialised_and_cmdline_is_active(
    private_tmux: PrivateTmux, tmp_path: pathlib.Path
) -> None:
    """With the default config on a 220-column session the operator window
    holds the merged board's four columns, the cmdline pane titled 'operator', the
    workbench pane titled 'workbench' (40% of the width) and the legend;
    the cmdline pane is active, both shells open on a clean prompt, and
    both run a login shell in the kanban root with the kanban env, the
    operator's ~/.bashrc aliases, shell-env's values over bashrc's, and the
    pane signal ignored. Every line create.sh typed, into these panes or
    any other, ends with the typed-line source tag, and so does every line
    the dashboard left in each resident shell's history."""
    sandbox = _sandbox(private_tmux, tmp_path)
    output = _run_create(private_tmux, tmp_path, sandbox)

    shell_pane = _cmdline_pane_index()
    workbench_pane = _workbench_pane_index()
    panes = _operator_panes(private_tmux, sandbox.session)
    context = f"panes: {panes}\n{output}"
    assert len(panes) == _operator_pane_count(), (
        f"expected {_operator_pane_count()} operator panes (4 board columns, "
        f"cmdline, workbench, legend):\n{context}"
    )
    assert panes[shell_pane].title == "operator", context
    assert panes[workbench_pane].title == "workbench", f"expected pane {workbench_pane} titled 'workbench':\n{context}"
    expected_width = _SESSION_COLS * _DEFAULT_WORKBENCH_PCT // 100
    assert (
        abs(panes[workbench_pane].width - expected_width) <= 1
    ), f"expected the workbench {expected_width}±1 columns wide:\n{context}"

    active_index, active_title = _active_operator_pane(private_tmux, sandbox.session)
    assert active_index == str(shell_pane), (
        f"expected the operator window's active pane to be the cmdline pane " f"{shell_pane}; got pane {active_index!r}"
    )
    assert active_title == "operator", f"expected the cmdline pane's title to be 'operator'; got {active_title!r}"

    _assert_every_typed_line_tagged(private_tmux, sandbox, output)
    _assert_clean_prompt(private_tmux, sandbox, shell_pane, "cmdline")
    _assert_clean_prompt(private_tmux, sandbox, workbench_pane, "workbench")
    _assert_resident_shell_initialised(private_tmux, tmp_path, sandbox, shell_pane, "cmdline")
    _assert_resident_shell_initialised(private_tmux, tmp_path, sandbox, workbench_pane, "workbench")


def test_zero_workbench_pct_builds_no_workbench(private_tmux: PrivateTmux, tmp_path: pathlib.Path) -> None:
    """operator_workbench_pct = 0 leaves the operator window without a
    workbench, and the all-armed handshake still completes."""
    sandbox = _sandbox(private_tmux, tmp_path, kanban_cfg="[dashboard]\noperator_workbench_pct = 0\n")
    output = _run_create(private_tmux, tmp_path, sandbox)
    _assert_no_workbench(private_tmux, sandbox, output)
    assert (
        "workbench threshold" not in output
    ), f"the narrow-window notice should not fire when the workbench is disabled:\n{output}"


def _window_hook_lines(private_tmux: PrivateTmux, target: str, hook_name: str) -> list[str]:
    """The entries of one hook array, as `show-hooks` lists them."""
    listed = private_tmux.run("show-hooks", *(["-w"] if ":" in target else []), "-t", target, hook_name)
    return listed.stdout.splitlines() if listed.returncode == 0 else []


def test_both_board_windows_carry_the_reflow_hook_without_a_workbench(
    private_tmux: PrivateTmux, tmp_path: pathlib.Path
) -> None:
    """With operator_workbench_pct = 0 the operator window and the
    visibility window each carry their own window-layout-changed hook running
    board-reflow.sh on that window, so both re-apply the board widths when a
    client attaches at another size; the session hooks' index-0 and index-1
    entries are still in place beside them."""
    sandbox = _sandbox(private_tmux, tmp_path, kanban_cfg="[dashboard]\noperator_workbench_pct = 0\n")
    output = _run_create(private_tmux, tmp_path, sandbox)
    _assert_no_workbench(private_tmux, sandbox, output)

    for window in ("operator", "visibility"):
        target = f"{sandbox.session}:{window}"
        window_id = private_tmux.run("display-message", "-p", "-t", target, "#{window_id}").stdout.strip()
        hooks = _window_hook_lines(private_tmux, target, "window-layout-changed")
        assert len(hooks) == 1, f"{window}: expected one window-layout-changed hook: {hooks}"
        assert (
            "board-reflow.sh" in hooks[0] and f"--window {window_id}" in hooks[0]
        ), f"{window}: the layout hook does not run board-reflow.sh on {window_id}: {hooks}"
    for hook_name in ("client-attached", "session-window-changed"):
        entries = _window_hook_lines(private_tmux, sandbox.session, hook_name)
        indices = [line.split()[0] for line in entries]
        assert indices == [
            f"{hook_name}[0]",
            f"{hook_name}[1]",
        ], f"the session's {hook_name} hooks lost an entry: {entries}"


def test_narrow_window_builds_no_workbench_and_says_so(private_tmux: PrivateTmux, tmp_path: pathlib.Path) -> None:
    """On a 120-column session under the default 160-column threshold no
    workbench is built, the handshake completes, and create.sh's output
    carries one notice line naming both widths."""
    sandbox = _sandbox(private_tmux, tmp_path, session_cols=120)
    output = _run_create(private_tmux, tmp_path, sandbox)
    _assert_no_workbench(private_tmux, sandbox, output)

    notices = [line for line in output.splitlines() if "workbench threshold" in line]
    assert len(notices) == 1, f"expected exactly one narrow-window notice line:\n{output}"
    assert "120" in notices[0] and str(_DEFAULT_WORKBENCH_MIN_COLS) in notices[0], (
        f"the notice must name the window width (120) and the threshold "
        f"({_DEFAULT_WORKBENCH_MIN_COLS}): {notices[0]!r}"
    )


# ---------------------------------------------------------------------------
# A client attaching at another width. create.sh builds the session 220
# columns wide whatever the operator's terminal; tmux then resizes the
# operator window to the attached client (window-size latest) and the
# window's reflow hook re-runs the workbench decision at that width.
# ---------------------------------------------------------------------------

_NARROW_CLIENT_COLS = 120
_WIDE_CLIENT_COLS = 180
_CLIENT_ROWS = 50
_PARKED_WORKBENCH_WINDOW = "workbench"
# How long the operator window may take to reach a client's size and be
# re-laid out: the hook runs in the background after tmux resizes it.
_CLIENT_LAYOUT_WAIT_S = 30


def _set_winsize(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


@contextlib.contextmanager
def _attached_client(private_tmux: PrivateTmux, sandbox: Sandbox, cols: int, rows: int) -> Iterator[tuple[int, int]]:
    """Attach a real tmux client to *sandbox*'s session through a pty sized
    *cols* x *rows*; yield (client pid, pty master fd), detach after."""
    pid, fd = pty.fork()
    if pid == 0:  # pragma: no cover - child process
        try:
            _set_winsize(0, cols, rows)
            os.execve(
                private_tmux.real_tmux,
                [private_tmux.real_tmux, "-L", private_tmux.socket_name, "attach-session", "-t", sandbox.session],
                dict(sandbox.env, TERM="xterm-256color"),
            )
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
        deadline = time.monotonic() + _CLIENT_LAYOUT_WAIT_S
        attached = ""
        while time.monotonic() < deadline:
            attached = private_tmux.run(
                "display-message", "-p", "-t", sandbox.session, "#{session_attached}"
            ).stdout.strip()
            if attached not in ("", "0"):
                break
            time.sleep(0.2)
        assert attached not in ("", "0"), f"no client attached: {private_tmux.run('list-clients').stdout!r}"
        yield pid, fd
    finally:
        private_tmux.run("detach-client", "-s", sandbox.session)
        with contextlib.suppress(ChildProcessError):
            os.waitpid(pid, 0)
        stop.set()
        drainer.join(timeout=5)
        with contextlib.suppress(OSError):
            os.close(fd)


def _operator_window_size(private_tmux: PrivateTmux, session: str) -> tuple[int, int]:
    out = private_tmux.run(
        "display-message", "-p", "-t", f"{session}:operator", "#{window_width} #{window_height}"
    ).stdout.split()
    return (int(out[0]), int(out[1])) if len(out) == 2 else (0, 0)


def _wait_for_operator_width(private_tmux: PrivateTmux, session: str, cols: int) -> tuple[int, int]:
    """Wait until tmux has resized the operator window to *cols* columns;
    return its (width, height)."""
    deadline = time.monotonic() + _CLIENT_LAYOUT_WAIT_S
    size = _operator_window_size(private_tmux, session)
    while size[0] != cols and time.monotonic() < deadline:
        time.sleep(0.2)
        size = _operator_window_size(private_tmux, session)
    assert size[0] == cols, f"the operator window never took the client's {cols} columns: {size}"
    return size


def _builder_geometry(
    private_tmux: PrivateTmux, sandbox: Sandbox, size: tuple[int, int], workbench_args: str
) -> list[tuple[int, ...]]:
    """The operator window the board builder makes at *size* with
    *workbench_args* and the default column set, built in a separate
    detached session on the same private server and read back."""
    session = f"reference-{uuid.uuid4().hex[:6]}"
    script = f"""
set -euo pipefail
source {shlex.quote(str(_DASHBOARD_CONSTANTS_SH))}
source {shlex.quote(str(_BOARD_WINDOW_SH))}
tmux new-session -d -s {session} -n main -x {size[0]} -y {size[1]}
tmux new-window -t {session} -n operator
dashboard_build_board_window {session} operator "$PGAI_OPERATOR_SHELL_PCT" \
    "$PGAI_OPERATOR_SHELL_MIN_ROWS" {workbench_args} {_DEFAULT_BOARD_AGENTS}
"""
    built = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60, env=sandbox.env)
    assert built.returncode == 0, built.stdout + built.stderr
    try:
        return _pane_geometry(private_tmux, session, "operator")
    finally:
        private_tmux.run("kill-session", "-t", session)


def _wait_for_operator_geometry(private_tmux: PrivateTmux, session: str, expected: list[tuple[int, ...]]) -> None:
    deadline = time.monotonic() + _CLIENT_LAYOUT_WAIT_S
    geometry = _pane_geometry(private_tmux, session, "operator")
    while geometry != expected and time.monotonic() < deadline:
        time.sleep(0.2)
        geometry = _pane_geometry(private_tmux, session, "operator")
    windows = private_tmux.run("list-windows", "-t", session, "-F", "#{window_name}").stdout.split()
    assert geometry == expected, f"operator window {geometry}, expected {expected}; windows {windows}"


def _window_pane_titles(private_tmux: PrivateTmux, session: str, window: str) -> list[str]:
    listed = private_tmux.run("list-panes", "-t", f"{session}:{window}", "-F", "#{pane_title}")
    return listed.stdout.splitlines() if listed.returncode == 0 else []


def test_narrow_client_gets_no_workbench_and_the_pct0_board(private_tmux: PrivateTmux, tmp_path: pathlib.Path) -> None:
    """A client attaching from a 120-column terminal, under the shipped
    defaults, sees the operator window without the workbench and with the
    board the pct-0 build gives at the window's size; the workbench waits in
    its own window and comes back when the terminal is widened."""
    sandbox = _sandbox(private_tmux, tmp_path)
    output = _run_create(private_tmux, tmp_path, sandbox)
    assert len(_operator_panes(private_tmux, sandbox.session)) == _operator_pane_count(), output

    with _attached_client(private_tmux, sandbox, _NARROW_CLIENT_COLS, _CLIENT_ROWS) as (pid, fd):
        private_tmux.run("select-window", "-t", f"{sandbox.session}:operator")
        size = _wait_for_operator_width(private_tmux, sandbox.session, _NARROW_CLIENT_COLS)
        _wait_for_operator_geometry(
            private_tmux, sandbox.session, _builder_geometry(private_tmux, sandbox, size, "0 0")
        )
        _assert_no_workbench(private_tmux, sandbox, output)
        assert _window_pane_titles(private_tmux, sandbox.session, _PARKED_WORKBENCH_WINDOW) == [
            "workbench"
        ], "the workbench pane should wait in its own window while the client is narrow"

        _set_winsize(fd, _SESSION_COLS, _CLIENT_ROWS)
        os.kill(pid, signal.SIGWINCH)
        size = _wait_for_operator_width(private_tmux, sandbox.session, _SESSION_COLS)
        _wait_for_operator_geometry(
            private_tmux,
            sandbox.session,
            _builder_geometry(private_tmux, sandbox, size, f"{_DEFAULT_WORKBENCH_PCT} {_DEFAULT_WORKBENCH_MIN_COLS}"),
        )
        panes = _operator_panes(private_tmux, sandbox.session)
        assert panes[_workbench_pane_index()].title == "workbench", panes
        assert _window_pane_titles(private_tmux, sandbox.session, _PARKED_WORKBENCH_WINDOW) == []


def test_client_at_least_the_threshold_wide_keeps_the_workbench(
    private_tmux: PrivateTmux, tmp_path: pathlib.Path
) -> None:
    """A client 180 columns wide, at least the default 160-column
    threshold, keeps the workbench beside the board, every pane at the size
    the builder gives at the window's size."""
    sandbox = _sandbox(private_tmux, tmp_path)
    _run_create(private_tmux, tmp_path, sandbox)

    with _attached_client(private_tmux, sandbox, _WIDE_CLIENT_COLS, _CLIENT_ROWS):
        private_tmux.run("select-window", "-t", f"{sandbox.session}:operator")
        size = _wait_for_operator_width(private_tmux, sandbox.session, _WIDE_CLIENT_COLS)
        _wait_for_operator_geometry(
            private_tmux,
            sandbox.session,
            _builder_geometry(private_tmux, sandbox, size, f"{_DEFAULT_WORKBENCH_PCT} {_DEFAULT_WORKBENCH_MIN_COLS}"),
        )
        panes = _operator_panes(private_tmux, sandbox.session)
        assert len(panes) == _operator_pane_count(), panes
        assert panes[_workbench_pane_index()].title == "workbench", panes


def test_invalid_workbench_values_fall_back_to_defaults(private_tmux: PrivateTmux, tmp_path: pathlib.Path) -> None:
    """An invalid value for each key draws one warning line naming that key,
    and the workbench is built with the defaults: 40% wide, and present on
    the 220-column session (the out-of-range threshold would have
    suppressed it had it been used)."""
    sandbox = _sandbox(
        private_tmux,
        tmp_path,
        kanban_cfg="[dashboard]\noperator_workbench_pct = forty\noperator_workbench_min_cols = 99999\n",
    )
    output = _run_create(private_tmux, tmp_path, sandbox)

    warnings = _workbench_warning_lines(output)
    assert len(warnings) == 2, f"expected one warning line per invalid key:\n{output}"
    assert sum("operator_workbench_pct" in w for w in warnings) == 1, warnings
    assert sum("operator_workbench_min_cols" in w for w in warnings) == 1, warnings

    panes = _operator_panes(private_tmux, sandbox.session)
    workbench = panes[_workbench_pane_index()] if len(panes) == _operator_pane_count() else None
    assert (
        workbench is not None and workbench.title == "workbench"
    ), f"expected the default workbench to be built:\npanes: {panes}\n{output}"
    expected_width = _SESSION_COLS * _DEFAULT_WORKBENCH_PCT // 100
    assert (
        abs(workbench.width - expected_width) <= 1
    ), f"expected the workbench at the default {expected_width}±1 columns:\npanes: {panes}"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("operator_workbench_pct", "100"),
        ("operator_workbench_pct", "-5"),
        ("operator_workbench_pct", "4o"),
        ("operator_workbench_pct", "2.5"),
        ("operator_workbench_min_cols", "10001"),
        ("operator_workbench_min_cols", "wide"),
        ("operator_workbench_min_cols", "-1"),
    ],
)
def test_invalid_workbench_value_warns_once_naming_its_key(
    private_tmux: PrivateTmux, tmp_path: pathlib.Path, key: str, value: str
) -> None:
    """Each non-integer or out-of-range value draws exactly one warning line,
    naming the key and its default. Run through create.sh's
    --print-schedule-floors dry run, which exits after reading the config
    and before creating a session."""
    sandbox = _sandbox(private_tmux, tmp_path, kanban_cfg=f"[dashboard]\n{key} = {value}\n")
    output = _run_create(private_tmux, tmp_path, sandbox, "--print-schedule-floors")

    warnings = _workbench_warning_lines(output)
    default = _DEFAULT_WORKBENCH_PCT if key == "operator_workbench_pct" else _DEFAULT_WORKBENCH_MIN_COLS
    assert len(warnings) == 1, f"expected exactly one warning line for {key} = {value}:\n{output}"
    assert key in warnings[0] and f"defaulting to {default}" in warnings[0], warnings[0]


def test_valid_workbench_values_draw_no_warning(private_tmux: PrivateTmux, tmp_path: pathlib.Path) -> None:
    """The range ends are accepted silently."""
    sandbox = _sandbox(
        private_tmux,
        tmp_path,
        kanban_cfg="[dashboard]\noperator_workbench_pct = 99\noperator_workbench_min_cols = 0\n",
    )
    output = _run_create(private_tmux, tmp_path, sandbox, "--print-schedule-floors")
    assert _workbench_warning_lines(output) == [], output


def test_default_board_is_three_input_columns_and_one_agents_column(
    private_tmux: PrivateTmux, tmp_path: pathlib.Path
) -> None:
    """With no operator_board_agents line the operator board is BUGS,
    PRIORITIES, REQUIREMENTS and AGENTS, pane 3 running column-render.sh
    with the agents column type under cadence-loop.sh; the cmdline,
    workbench and legend follow at the merged golden geometry, no warning
    names the key, and window 2 is untouched."""
    sandbox = _sandbox(private_tmux, tmp_path, kanban_cfg="[dashboard]\nrefresh_seconds = 5\n")
    output = _run_create(private_tmux, tmp_path, sandbox)

    _assert_merged_board(private_tmux, sandbox, output)
    assert _board_agents_warning_lines(output) == [], output
    commands = _first_pane_commands(private_tmux, sandbox.session, "visibility")
    assert [_label(commands[i]) for i in range(8)] == _SPLIT_LABELS, commands


def test_split_board_is_the_eleven_pane_window_running_window_2s_commands(
    private_tmux: PrivateTmux, tmp_path: pathlib.Path
) -> None:
    """operator_board_agents = split builds the eleven-pane operator window:
    panes 0-7 and the legend run exactly window 2's column and legend
    commands, the cmdline and workbench sit at panes 8 and 9 with the
    cmdline active, and the geometry is the eight-column golden."""
    sandbox = _sandbox(private_tmux, tmp_path, kanban_cfg="[dashboard]\noperator_board_agents = split\n")
    output = _run_create(private_tmux, tmp_path, sandbox)

    operator = _first_pane_commands(private_tmux, sandbox.session, "operator")
    visibility = _first_pane_commands(private_tmux, sandbox.session, "visibility")
    geometry = _pane_geometry(private_tmux, sandbox.session, "operator")
    context = f"operator: {operator}\nvisibility: {visibility}\ngeometry: {geometry}\n{output}"

    assert len(geometry) == _operator_pane_count("split") == 11, context
    assert [operator[i] for i in range(8)] == [visibility[i] for i in range(8)], context
    assert operator[10] == visibility[8], context
    assert geometry == OPERATOR_SPLIT_GEOMETRY_WITH_WORKBENCH_220X50, context

    panes = _operator_panes(private_tmux, sandbox.session)
    assert (_cmdline_pane_index("split"), _workbench_pane_index("split")) == (8, 9), context
    assert panes[8].title == "operator" and panes[9].title == "workbench", context
    assert _active_operator_pane(private_tmux, sandbox.session)[0] == "8", context
    assert _board_agents_warning_lines(output) == [], output


@pytest.mark.parametrize("value", ["merged", "split"])
def test_visibility_window_ignores_operator_board_agents(
    private_tmux: PrivateTmux, tmp_path: pathlib.Path, value: str
) -> None:
    """Under either value window 2 has its nine panes running the eight
    board column commands and the legend, at its golden geometry. Run at
    237x60, one of the sizes that golden is recorded at."""
    size = (237, 60)
    sandbox = _sandbox(
        private_tmux,
        tmp_path,
        kanban_cfg=f"[dashboard]\noperator_board_agents = {value}\n",
        session_cols=size[0],
        session_rows=size[1],
    )
    output = _run_create(private_tmux, tmp_path, sandbox)
    _assert_visibility_board(private_tmux, sandbox, size, output)
    expected_operator_panes = _operator_pane_count(value)
    assert len(_pane_geometry(private_tmux, sandbox.session, "operator")) == expected_operator_panes, output


def test_invalid_board_agents_value_builds_merged_board_and_warns_once(
    private_tmux: PrivateTmux, tmp_path: pathlib.Path
) -> None:
    """operator_board_agents = both builds the merged board and prints
    exactly one warning line, naming the key and the merged default."""
    sandbox = _sandbox(private_tmux, tmp_path, kanban_cfg="[dashboard]\noperator_board_agents = both\n")
    output = _run_create(private_tmux, tmp_path, sandbox)

    warnings = _board_agents_warning_lines(output)
    assert len(warnings) == 1, f"expected exactly one operator_board_agents warning:\n{output}"
    assert "'both'" in warnings[0] and f"defaulting to {_DEFAULT_BOARD_AGENTS}" in warnings[0], warnings
    _assert_merged_board(private_tmux, sandbox, output)


@pytest.mark.parametrize("value", ["both", "3", "merged,split", "agents"])
def test_invalid_board_agents_value_warns_once_naming_the_key(
    private_tmux: PrivateTmux, tmp_path: pathlib.Path, value: str
) -> None:
    """Each value other than merged or split draws exactly one warning line
    naming the key and its default. Run through create.sh's
    --print-schedule-floors dry run, which exits after reading the config
    and before creating a session."""
    sandbox = _sandbox(private_tmux, tmp_path, kanban_cfg=f"[dashboard]\noperator_board_agents = {value}\n")
    output = _run_create(private_tmux, tmp_path, sandbox, "--print-schedule-floors")

    warnings = _board_agents_warning_lines(output)
    assert len(warnings) == 1, f"expected exactly one warning line for operator_board_agents = {value}:\n{output}"
    assert f"defaulting to {_DEFAULT_BOARD_AGENTS}" in warnings[0], warnings[0]


@pytest.mark.parametrize(
    "line",
    [
        "operator_board_agents = merged",
        "operator_board_agents = split",
        "operator_board_agents = Split",
        "operator_board_agents =",
    ],
)
def test_valid_or_blank_board_agents_value_draws_no_warning(
    private_tmux: PrivateTmux, tmp_path: pathlib.Path, line: str
) -> None:
    """merged and split (in any case) and a blank value are accepted
    silently."""
    sandbox = _sandbox(private_tmux, tmp_path, kanban_cfg=f"[dashboard]\n{line}\n")
    output = _run_create(private_tmux, tmp_path, sandbox, "--print-schedule-floors")
    assert _board_agents_warning_lines(output) == [], output


# ---------------------------------------------------------------------------
# Session name: --session, then PGAI_DASHBOARD_SESSION_NAME, then kanban.cfg
# [dashboard] session_name, then pgai-kanban-dashboard.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("flag", "env_value", "cfg_text", "expected"),
    [
        ("from-flag", "from-env", "[dashboard]\nsession_name = from-cfg\n", "from-flag"),
        ("", "from-env", "[dashboard]\nsession_name = from-cfg\n", "from-env"),
        ("", None, "[dashboard]\nsession_name = from-cfg\n", "from-cfg"),
        ("", None, "[dashboard]\nsession_name =\n", _DEFAULT_SESSION_NAME),
        ("", None, "[dashboard]\nrefresh_seconds = 5\n", _DEFAULT_SESSION_NAME),
        ("", None, None, _DEFAULT_SESSION_NAME),
    ],
)
def test_session_name_resolver_takes_the_first_non_empty_tier(
    tmp_path: pathlib.Path, flag: str, env_value: str | None, cfg_text: str | None, expected: str
) -> None:
    """dashboard_resolve_session_name prints the flag, else the environment
    variable, else a non-blank kanban.cfg session_name, else the default;
    a missing kanban.cfg reads like one without the key."""
    kanban_root = tmp_path / "kanban"
    kanban_root.mkdir()
    if cfg_text is not None:
        (kanban_root / "kanban.cfg").write_text(cfg_text)
    env = {k: v for k, v in os.environ.items() if k != "PGAI_DASHBOARD_SESSION_NAME"}
    if env_value is not None:
        env["PGAI_DASHBOARD_SESSION_NAME"] = env_value
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; dashboard_resolve_session_name "$2" "$3"',
            "_",
            str(_SESSION_NAME_LIB),
            flag,
            str(kanban_root),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{expected}\n", result


def _session_names(private_tmux: PrivateTmux) -> set[str]:
    """Every session on the private tmux server (none when no server runs)."""
    listed = private_tmux.run("list-sessions", "-F", "#{session_name}")
    return set(listed.stdout.split()) if listed.returncode == 0 else set()


def _new_bare_session(tmp_path: pathlib.Path, sandbox: Sandbox, name: str) -> None:
    """A detached one-pane session on the private server, started through
    the wrapper with the sandbox environment, whose pane only sleeps."""
    started = subprocess.run(
        [str(tmp_path / "bin" / "tmux"), "new-session", "-d", "-s", name, "sleep", "600"],
        capture_output=True,
        text=True,
        timeout=15,
        env=sandbox.env,
    )
    assert started.returncode == 0, started.stderr


def _run_dashboard_script(
    script: pathlib.Path, tmp_path: pathlib.Path, sandbox: Sandbox, *args: str
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(script), "--kanban-root", str(sandbox.kanban_root), *args],
        capture_output=True,
        text=True,
        timeout=60,
        env=sandbox.env,
        cwd=str(tmp_path),
        stdin=subprocess.DEVNULL,
    )


def test_create_session_flag_wins_when_kanban_cfg_names_no_session(
    private_tmux: PrivateTmux, tmp_path: pathlib.Path
) -> None:
    """With a kanban.cfg that has no session_name and
    PGAI_DASHBOARD_SESSION_NAME unset, create.sh --session <unique> builds
    <unique>, not the default session."""
    sandbox = _sandbox(private_tmux, tmp_path, kanban_cfg="[dashboard]\nrefresh_seconds = 5\n")
    assert "PGAI_DASHBOARD_SESSION_NAME" not in sandbox.env
    output = _run_create(private_tmux, tmp_path, sandbox)

    sessions = _session_names(private_tmux)
    assert sandbox.session in sessions, f"sessions: {sessions}\n{output}"
    assert _DEFAULT_SESSION_NAME not in sessions, f"sessions: {sessions}\n{output}"


def test_create_session_flag_wins_over_env_and_kanban_cfg(private_tmux: PrivateTmux, tmp_path: pathlib.Path) -> None:
    """--session outranks both PGAI_DASHBOARD_SESSION_NAME and kanban.cfg's
    session_name: only the flag's session is built."""
    env_name = f"from-env-{uuid.uuid4().hex[:6]}"
    cfg_name = f"from-cfg-{uuid.uuid4().hex[:6]}"
    sandbox = _sandbox(
        private_tmux,
        tmp_path,
        kanban_cfg=f"[dashboard]\nsession_name = {cfg_name}\n",
        extra_env={"PGAI_DASHBOARD_SESSION_NAME": env_name},
    )
    output = _run_create(private_tmux, tmp_path, sandbox)

    sessions = _session_names(private_tmux)
    assert sessions == {sandbox.session}, f"sessions: {sessions}\n{output}"


def test_kill_session_flag_wins_over_env_and_kanban_cfg(private_tmux: PrivateTmux, tmp_path: pathlib.Path) -> None:
    """kill.sh --session <name> kills <name> and leaves the sessions the
    environment variable and kanban.cfg name running."""
    env_name = f"from-env-{uuid.uuid4().hex[:6]}"
    cfg_name = f"from-cfg-{uuid.uuid4().hex[:6]}"
    sandbox = _sandbox(
        private_tmux,
        tmp_path,
        kanban_cfg=f"[dashboard]\nsession_name = {cfg_name}\n",
        extra_env={"PGAI_DASHBOARD_SESSION_NAME": env_name},
    )
    for name in (sandbox.session, env_name, cfg_name):
        _new_bare_session(tmp_path, sandbox, name)

    result = _run_dashboard_script(_KILL_SH, tmp_path, sandbox, "--session", sandbox.session)
    output = f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"

    assert result.returncode == 0, output
    assert f"Killed session '{sandbox.session}'." in result.stdout, output
    assert _session_names(private_tmux) == {env_name, cfg_name}, output


@pytest.mark.parametrize(
    ("script", "acted_on"),
    [(_ATTACH_SH, "Attaching to session '{}'..."), (_DETACH_SH, "Detached all clients from session '{}'.")],
    ids=["attach", "detach"],
)
def test_attach_and_detach_session_flag_wins_over_env(
    private_tmux: PrivateTmux, tmp_path: pathlib.Path, script: pathlib.Path, acted_on: str
) -> None:
    """With PGAI_DASHBOARD_SESSION_NAME naming a session that does not exist,
    --session <name> still acts on <name>: the script names it and does not
    report the environment's session as missing. (attach.sh's final
    attach-session fails headless; the line before it is what is checked.)"""
    env_name = f"absent-{uuid.uuid4().hex[:6]}"
    sandbox = _sandbox(
        private_tmux,
        tmp_path,
        kanban_cfg="[dashboard]\nrefresh_seconds = 5\n",
        extra_env={"PGAI_DASHBOARD_SESSION_NAME": env_name},
    )
    _new_bare_session(tmp_path, sandbox, sandbox.session)

    result = _run_dashboard_script(script, tmp_path, sandbox, "--session", sandbox.session)
    output = f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"

    assert acted_on.format(sandbox.session) in result.stdout, output
    assert env_name not in output, output


# ---------------------------------------------------------------------------
# Rows per column: DASHBOARD_ROWS_PER_COLUMN, then kanban.cfg [dashboard]
# rows_per_column, then 21.
# ---------------------------------------------------------------------------


def _column_rows(command: str) -> str | None:
    """The rows argument a board column command passes column-render.sh:
    the fourth positional after `input`, the third after `queue`."""
    words = command.split()
    for i, word in enumerate(words):
        if word.endswith("/column-render.sh"):
            return words[i + 4] if words[i + 1] == "input" else words[i + 3]
    return None


_NO_KANBAN_CFG = object()


@pytest.mark.parametrize(
    ("cfg_line", "env_rows", "expected"),
    [
        ("rows_per_column = 21", None, "21"),
        ("rows_per_column = 17", None, "17"),
        (None, None, _DEFAULT_ROWS_PER_COLUMN),
        (_NO_KANBAN_CFG, None, _DEFAULT_ROWS_PER_COLUMN),
        ("rows_per_column = 17", "9", "9"),
        ("rows_per_column = 0", None, _DEFAULT_ROWS_PER_COLUMN),
    ],
    ids=["key-21", "key-17", "key-absent", "no-kanban-cfg", "env-over-key", "key-invalid"],
)
def test_board_columns_carry_the_resolved_rows_per_column(
    private_tmux: PrivateTmux,
    tmp_path: pathlib.Path,
    cfg_line: object,
    env_rows: str | None,
    expected: str,
) -> None:
    """Every board column command in window 2 and on the operator board
    carries DASHBOARD_ROWS_PER_COLUMN when it is set, else kanban.cfg's
    rows_per_column, else the documented default of 21; a value that is not
    a positive whole number falls back to that default."""
    if cfg_line is _NO_KANBAN_CFG:
        kanban_cfg = None
    else:
        kanban_cfg = "[dashboard]\nrefresh_seconds = 5\n" + (f"{cfg_line}\n" if cfg_line else "")
    sandbox = _sandbox(
        private_tmux,
        tmp_path,
        kanban_cfg=kanban_cfg,
        extra_env={"DASHBOARD_ROWS_PER_COLUMN": env_rows} if env_rows else None,
    )
    output = _run_create(private_tmux, tmp_path, sandbox)

    visibility = _first_pane_commands(private_tmux, sandbox.session, "visibility")
    operator = _first_pane_commands(private_tmux, sandbox.session, "operator")
    context = f"visibility: {visibility}\noperator: {operator}\n{output}"
    assert [_column_rows(visibility.get(i, "")) for i in range(8)] == [expected] * 8, context
    assert [_column_rows(operator.get(i, "")) for i in range(4)] == [expected] * 4, context


# ---------------------------------------------------------------------------
# Teardown: no process the dashboard started outlives the test.
# ---------------------------------------------------------------------------


def test_teardown_leaves_no_process_create_started_alive(tmp_path: pathlib.Path) -> None:
    """create.sh starts processes inside the private tmux server (its panes)
    and outside it (the status-bottom loop it backgrounds, which outlives
    create.sh itself). While the session runs, the marker scan sees both
    kinds; once the private server's teardown completes, no process carrying
    the marker is alive."""
    require_procfs(_TMUX_SKIP_LABEL)
    with _private_tmux_server(tmp_path) as private_tmux:
        sandbox = _sandbox(private_tmux, tmp_path)
        output = _run_create(private_tmux, tmp_path, sandbox)

        running = _processes_launched_under(private_tmux.socket_name)
        context = "\n".join(f"{pid} {cmdline}" for pid, cmdline in running) + f"\n{output}"
        assert any("status-bottom-loop.sh" in cmdline for _pid, cmdline in running), (
            f"expected the scan to see the status-bottom loop create.sh backgrounds " f"outside tmux:\n{context}"
        )
        assert any(
            f"-L {private_tmux.socket_name}" in cmdline for _pid, cmdline in running
        ), f"expected the scan to see the private tmux server:\n{context}"
        assert any(
            "/cadence-loop.sh" in cmdline for _pid, cmdline in running
        ), f"expected the scan to see the board panes' cadence loops:\n{context}"

    survivors = _processes_launched_under(private_tmux.socket_name)
    assert (
        survivors == []
    ), "processes started under the private tmux socket are still alive after " "teardown:\n" + "\n".join(
        f"{pid} {cmdline}" for pid, cmdline in survivors
    )
