"""
test_dashboard_board_window_geometry.py
=======================================
Pane geometry of the dashboard's two board windows, as built by the shared
builder ``dashboard_build_board_window`` in
``team/scripts/dashboard/lib/board_window.sh`` -- the function create.sh
builds both window 1 (operator) and window 2 (visibility) from.

Each test starts a private tmux server (``tmux -L <unique>``) with a
fixed-size detached session (``new-session -x/-y``), builds an operator
window and a visibility window in it exactly the way create.sh does
(operator with the dashboard_constants.sh shell percentage and floor,
visibility with no shell row), and reads the result back with
``list-panes``. The operator's live dashboard session on the default socket
is never touched.

What is pinned here, at fixture sizes 160x48 and 237x60:
  - operator panes 0-7 have exactly the widths of visibility panes 0-7, and
    the two legend panes have the same width;
  - operator has exactly 10 panes: 8 columns, then a full-width shell whose
    height is floor(15% of the window height) (minimum 3 rows) sitting
    directly below the board, then a 1-row legend directly below the shell
    as the window's bottom row;
  - visibility's geometry is the fixed set of values it had before the
    builder was shared (a regression lock on window 2);
  - the shell row's minimum height holds when the percentage yields fewer
    rows.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import uuid
from typing import Iterator, NamedTuple

import pytest
from tests.fixtures.binary_guard import narrate_binary_skip, try_resolve_binary

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_BOARD_WINDOW_SH = _TEAM_DIR / "scripts" / "dashboard" / "lib" / "board_window.sh"
_DASHBOARD_CONSTANTS_SH = _TEAM_DIR / "scripts" / "lib" / "dashboard_constants.sh"

_SESSION = "board-geometry"

# Window 2's pane geometry at each fixture size, recorded from the
# visibility window's own split-and-resize code before it moved into the
# shared builder: (pane_index, pane_left, pane_top, pane_width, pane_height).
_VISIBILITY_GEOMETRY = {
    (160, 48): [
        (0, 0, 0, 18, 46),
        (1, 19, 0, 23, 46),
        (2, 43, 0, 21, 46),
        (3, 65, 0, 18, 46),
        (4, 84, 0, 18, 46),
        (5, 103, 0, 19, 46),
        (6, 123, 0, 18, 46),
        (7, 142, 0, 18, 46),
        (8, 0, 47, 160, 1),
    ],
    (237, 60): [
        (0, 0, 0, 27, 58),
        (1, 28, 0, 33, 58),
        (2, 62, 0, 32, 58),
        (3, 95, 0, 27, 58),
        (4, 123, 0, 27, 58),
        (5, 151, 0, 30, 58),
        (6, 182, 0, 27, 58),
        (7, 210, 0, 27, 58),
        (8, 0, 59, 237, 1),
    ],
}

_FIXTURE_SIZES = sorted(_VISIBILITY_GEOMETRY)


class Pane(NamedTuple):
    index: int
    left: int
    top: int
    width: int
    height: int


class BoardWindows(NamedTuple):
    width: int
    height: int
    operator: list[Pane]
    visibility: list[Pane]


@pytest.fixture
def tmux_socket() -> Iterator[tuple[str, str]]:
    """A private tmux socket name and the real tmux path; the server is killed afterwards."""
    guard = try_resolve_binary("tmux")
    socket_name = f"pgai-board-geom-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    try:
        if not guard.resolved:
            narrate_binary_skip("tmux", label="dashboard-board-window-geometry")
        yield guard.path, socket_name
    finally:
        guard.teardown(
            lambda path: subprocess.run(
                [path, "-L", socket_name, "kill-server"],
                capture_output=True,
                timeout=10,
            )
        )


def _list_panes(real_tmux: str, socket_name: str, window: str) -> list[Pane]:
    result = subprocess.run(
        [
            real_tmux, "-L", socket_name, "list-panes", "-t", f"{_SESSION}:{window}",
            "-F", "#{pane_index} #{pane_left} #{pane_top} #{pane_width} #{pane_height}",
        ],
        capture_output=True, text=True, check=True, timeout=10,
    )
    return [Pane(*map(int, line.split())) for line in result.stdout.splitlines()]


def _build_board_windows(
    tmux_socket: tuple[str, str],
    width: int,
    height: int,
    *,
    operator_shell_pct: str = "${PGAI_OPERATOR_SHELL_PCT}",
) -> BoardWindows:
    """Build both board windows in a fresh fixed-size session the way create.sh does."""
    real_tmux, socket_name = tmux_socket
    script = f"""
set -euo pipefail
tmux() {{ command {real_tmux} -L {socket_name} -f /dev/null "$@"; }}
source {_DASHBOARD_CONSTANTS_SH}
source {_BOARD_WINDOW_SH}
tmux new-session -d -s {_SESSION} -n main -x {width} -y {height}
tmux new-window -t {_SESSION} -n operator
dashboard_build_board_window {_SESSION} operator "{operator_shell_pct}" "${{PGAI_OPERATOR_SHELL_MIN_ROWS}}"
echo "operator shell=${{DASHBOARD_BOARD_SHELL_PANE}} legend=${{DASHBOARD_BOARD_LEGEND_PANE}}"
tmux new-window -t {_SESSION} -n visibility
dashboard_build_board_window {_SESSION} visibility 0
echo "visibility shell=${{DASHBOARD_BOARD_SHELL_PANE}} legend=${{DASHBOARD_BOARD_LEGEND_PANE}}"
"""
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "operator shell=8 legend=9" in result.stdout, result.stdout
    assert "visibility shell= legend=8" in result.stdout, result.stdout

    window_height = int(subprocess.run(
        [real_tmux, "-L", socket_name, "display-message", "-t", f"{_SESSION}:operator",
         "-p", "#{window_height}"],
        capture_output=True, text=True, check=True, timeout=10,
    ).stdout.strip())
    assert window_height == height, (
        f"fixture session was asked for {height} rows but tmux made the window "
        f"{window_height} rows; the geometry below would not be at the fixture size"
    )
    return BoardWindows(
        width=width,
        height=height,
        operator=_list_panes(real_tmux, socket_name, "operator"),
        visibility=_list_panes(real_tmux, socket_name, "visibility"),
    )


@pytest.mark.parametrize("size", _FIXTURE_SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_operator_columns_and_legend_have_visibility_widths(
    tmux_socket: tuple[str, str], size: tuple[int, int]
) -> None:
    board = _build_board_windows(tmux_socket, *size)

    operator_widths = [p.width for p in board.operator[:8]]
    visibility_widths = [p.width for p in board.visibility[:8]]
    assert operator_widths == visibility_widths, (
        f"operator columns {operator_widths} differ from visibility columns "
        f"{visibility_widths} at {size}"
    )
    assert [p.left for p in board.operator[:8]] == [p.left for p in board.visibility[:8]]
    assert board.operator[-1].width == board.visibility[-1].width == board.width


@pytest.mark.parametrize("size", _FIXTURE_SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_operator_shell_row_sits_between_board_and_legend(
    tmux_socket: tuple[str, str], size: tuple[int, int]
) -> None:
    board = _build_board_windows(tmux_socket, *size)
    panes = board.operator
    assert [p.index for p in panes] == list(range(10)), panes

    columns, shell, legend = panes[:8], panes[8], panes[9]
    board_bottom = max(p.top + p.height for p in columns)
    assert all(p.top == 0 for p in columns), columns

    expected_shell_h = max(3, board.height * 15 // 100)
    assert (shell.left, shell.width) == (0, board.width), shell
    assert shell.height == expected_shell_h, shell
    assert shell.top == board_bottom + 1, (
        f"shell row top {shell.top} is not directly below the board (bottom {board_bottom})"
    )

    assert (legend.left, legend.width, legend.height) == (0, board.width, 1), legend
    assert legend.top == shell.top + shell.height + 1, (
        f"legend top {legend.top} is not directly below the shell row {shell}"
    )
    assert legend.top + legend.height == board.height, "legend must be the window's bottom row"


@pytest.mark.parametrize("size", _FIXTURE_SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_visibility_geometry_is_unchanged(
    tmux_socket: tuple[str, str], size: tuple[int, int]
) -> None:
    board = _build_board_windows(tmux_socket, *size)
    assert [tuple(p) for p in board.visibility] == _VISIBILITY_GEOMETRY[size]


def test_shell_row_never_drops_below_its_minimum_rows(
    tmux_socket: tuple[str, str],
) -> None:
    # 5% of 48 rows floors to 2; the floor raises it to the minimum of 3.
    board = _build_board_windows(tmux_socket, 160, 48, operator_shell_pct="5")
    shell = board.operator[8]
    assert shell.height == 3, shell
    assert [p.width for p in board.operator[:8]] == [p.width for p in board.visibility[:8]]
