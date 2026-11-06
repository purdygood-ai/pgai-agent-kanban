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
  - the operator window built with the workbench arguments omitted, or with
    a workbench width of 0, has the fixed set of values it had before the
    builder learned the workbench column (a regression lock on window 1);
  - the shell row's minimum height holds when the percentage yields fewer
    rows.

And the workbench column:
  - at 220 columns with a 40% workbench and a 160-column threshold the
    operator window has 11 panes, the workbench on the far right spanning
    the board and shell rows and the legend still full width;
  - at 120 columns under the same arguments the narrow-window guard builds
    no workbench, the board keeps the full width, and one notice names the
    width and the threshold;
  - beside the workbench the eight columns keep their pct-0 proportions of
    the board region, none narrower than 15 columns;
  - at 160, 180, 200 and 210 columns with a 40% workbench (threshold 160),
    and at 220 columns with a 60% one, for both column sets: either no
    workbench is built and one notice says so, or every column is at least
    15 wide and the columns end at the workbench divider.

And the reflow hook (dashboard_install_board_reflow_hook running the real
board-reflow.sh), driven by resize-window on a window built with a
workbench at 220x50:
  - at 120 columns the workbench pane moves to its own window named
    "workbench" and the operator window has exactly the geometry the pct-0
    build gives at that size;
  - back at 220x50 the workbench rejoins and the window has its build
    geometry again, the parked window gone.

And the same hook on a board window built at 220 columns and then resized
to 183, 120 and 200 columns -- the operator window with a workbench (both
column sets), the operator window built without one, and the visibility
window: after each resize every pane has the width a fresh build at that
size gives (+/-1), and @pgai_board_notice carries the degraded-layout
notice exactly when that size's split board is too narrow for its columns.

And the split board's 15-column floor, without a workbench: at 127 and 129
columns every column is at least 15 wide; at 120 and 126 columns, too
narrow for eight 15-column columns and their dividers, the columns share
the width evenly (no two more than one column apart), one NOTICE line names
the window, its width and the 127 columns needed, and @pgai_board_notice
holds the same line. Either way the columns and dividers fill the window.

And the legend bar: legend-render.sh, run as the operator window's legend
pane runs it, prints the legend while the split board keeps its floor,
prints @pgai_board_notice (cut to the pane's width) once a resize to 120
columns degrades the board, and prints the legend again after a resize back
to 220 columns clears the notice.

A rebuild in one test replaces the session on the same tmux server, which
is kept alive across it, so it never races that server's exit.

And the column set (the builder's seventh argument):
  - "merged" builds BUGS, PRIORITIES, REQUIREMENTS and one AGENTS column:
    fixed geometry at 160x48 and 237x60 without a workbench and at 220x50
    with one, the four widths plus three dividers filling the board region
    and none narrower than 15 columns; with the workbench the window has 7
    panes (4 columns, shell, workbench, legend);
  - "split" passed explicitly builds exactly the board the argument
    omitted builds, with and without the workbench;
  - any other value is refused before a single split.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
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
from tests.fixtures.board_window_geometry import (
    OPERATOR_MERGED_GEOMETRY,
    OPERATOR_SPLIT_GEOMETRY_WITH_WORKBENCH_220X50,
    VISIBILITY_GEOMETRY,
)
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_BOARD_WINDOW_SH = _TEAM_DIR / "scripts" / "dashboard" / "lib" / "board_window.sh"
_BOARD_REFLOW_SH = _TEAM_DIR / "scripts" / "dashboard" / "board-reflow.sh"
_LEGEND_RENDER_SH = _TEAM_DIR / "scripts" / "dashboard" / "legend-render.sh"
_DASHBOARD_CONSTANTS_SH = _TEAM_DIR / "scripts" / "lib" / "dashboard_constants.sh"

_SESSION = "board-geometry"

# Window 2's pane geometry at each fixture size (tests/fixtures/board_window_geometry.py).
_VISIBILITY_GEOMETRY = VISIBILITY_GEOMETRY

_FIXTURE_SIZES = sorted(_VISIBILITY_GEOMETRY)

# Window 1's pane geometry at each fixture size, recorded from the
# operator window's list-panes output before the builder learned the
# workbench column: (pane_index, pane_left, pane_top, pane_width,
# pane_height).
_OPERATOR_GEOMETRY_WITHOUT_WORKBENCH = {
    (160, 48): [
        (0, 0, 0, 18, 38),
        (1, 19, 0, 23, 38),
        (2, 43, 0, 21, 38),
        (3, 65, 0, 18, 38),
        (4, 84, 0, 18, 38),
        (5, 103, 0, 19, 38),
        (6, 123, 0, 18, 38),
        (7, 142, 0, 18, 38),
        (8, 0, 39, 160, 7),
        (9, 0, 47, 160, 1),
    ],
    (237, 60): [
        (0, 0, 0, 27, 48),
        (1, 28, 0, 33, 48),
        (2, 62, 0, 32, 48),
        (3, 95, 0, 27, 48),
        (4, 123, 0, 27, 48),
        (5, 151, 0, 30, 48),
        (6, 182, 0, 27, 48),
        (7, 210, 0, 27, 48),
        (8, 0, 49, 237, 9),
        (9, 0, 59, 237, 1),
    ],
}

# Window 1's eight-column geometry at 220x50 with the 40% / 160-column
# workbench (tests/fixtures/board_window_geometry.py).
_OPERATOR_GEOMETRY_WITH_WORKBENCH_220X50 = OPERATOR_SPLIT_GEOMETRY_WITH_WORKBENCH_220X50

# Window 1's merged (3 + 1) geometry, keyed by (width, height, workbench
# args) (tests/fixtures/board_window_geometry.py).
_MERGED_GEOMETRY = OPERATOR_MERGED_GEOMETRY

# The pane indices the builder reports for each merged case.
_MERGED_PANE_INDICES = {
    "0 0": {"shell": "4", "workbench": "", "legend": "5"},
    "40 160": {"shell": "4", "workbench": "5", "legend": "6"},
}

# The four merged columns' minimum widths: the 15-column floor, and the
# AGENTS column's room for a glyph, a space and an 18-character
# CODER-YYYYMMDD-NNN key at 220x50 beside the default 40% workbench.
_MERGED_COLUMN_COUNT = 4
_AGENTS_MIN_WIDTH = 20

# Ways of asking the builder for no workbench: the arguments left off (how
# create.sh calls it today), and an explicit width of 0 with a threshold.
_NO_WORKBENCH_ARGS = {"omitted": "", "pct-0": "0 160"}

# Workbench arguments the wide and narrow cases use: width 40%, threshold
# 160 columns.
_WORKBENCH_PCT = 40
_WORKBENCH_MIN_COLS = 160
_WORKBENCH_ARGS = f"{_WORKBENCH_PCT} {_WORKBENCH_MIN_COLS}"

# The builder's narrowest column width.
_COLUMN_FLOOR = 15

# How far, in columns, a board column beside the workbench may sit from its
# pct-0 width scaled to the board region: up to 1 for the floor() in each
# width formula, and up to 1 more because the seven inter-column borders
# keep their width while the columns shrink, and the 15-column floor moves
# that shortfall onto the wider left-hand columns.
_RATIO_TOLERANCE_COLS = 2

# Window widths at which the columns beside the workbench would once fall
# under the floor: the 160-column threshold up to just below the narrowest
# window the split board fits beside a 40% workbench, and the standard
# 220-column session with a 60% workbench. (width, workbench args).
_FIT_CASES = [
    (160, "40 160"),
    (180, "40 160"),
    (200, "40 160"),
    (210, "40 160"),
    (220, "60 160"),
]
_FIT_HEIGHT = 50
_COLUMN_COUNTS = {"split": 8, "merged": _MERGED_COLUMN_COUNT}

# The window a workbench pane is moved to while its board window is too
# narrow for it, and how long a resize may take to be re-laid out by the
# hook (it runs in the background, after tmux has applied the new size).
_PARKED_WORKBENCH_WINDOW = "workbench"
_REFLOW_WAIT_S = 20

# The narrowest board region whose eight split columns all keep the floor:
# eight 15-column columns and their seven dividers.
_SPLIT_FLOOR_WIDTH = 8 * _COLUMN_FLOOR + 7

# Split board widths without a workbench either side of that boundary.
_SPLIT_FLOOR_CASES = [120, 126, 127, 129]

# Board windows re-laid out by the hook after a build at 220x50: the window,
# its workbench arguments and its column set (empty: the builder's default).
_RESIZE_CASES = {
    "operator-workbench-merged": ("operator", _WORKBENCH_ARGS, "merged"),
    "operator-workbench-split": ("operator", _WORKBENCH_ARGS, "split"),
    "operator-no-workbench": ("operator", "0 160", "split"),
    "visibility": ("visibility", "", ""),
}
# The widths each case is resized to, in order, and how far a pane's width
# may sit from a fresh build's at the same size.
_RESIZE_WIDTHS = [183, 120, 200]
_RESIZE_HEIGHT = 50
_RESIZE_TOLERANCE_COLS = 1
# A second session on the same server, created after the board's.
_OTHER_SESSION = "board-geometry-other"


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
    operator_panes: dict[str, str]
    notices: list[str]


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
        teardown_private_tmux(guard, socket_name)


def _list_panes(real_tmux: str, socket_name: str, window: str) -> list[Pane]:
    result = subprocess.run(
        [
            real_tmux,
            "-L",
            socket_name,
            "list-panes",
            "-t",
            f"{_SESSION}:{window}",
            "-F",
            "#{pane_index} #{pane_left} #{pane_top} #{pane_width} #{pane_height}",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return [Pane(*map(int, line.split())) for line in result.stdout.splitlines()]


def _build_board_windows(
    tmux_socket: tuple[str, str],
    width: int,
    height: int,
    *,
    operator_shell_pct: str = "${PGAI_OPERATOR_SHELL_PCT}",
    operator_workbench_args: str = "",
    operator_column_set: str = "",
) -> BoardWindows:
    """Build both board windows in a fresh fixed-size session the way create.sh does.

    *operator_workbench_args* is appended to the operator window's builder
    call (the workbench width and threshold); empty leaves them off, as
    create.sh does today. *operator_column_set* is appended after them as
    the column-set argument; empty leaves it off. Any session left by an
    earlier build in the same test is replaced, so one test may build more
    than once.

    A rebuild replaces only the session, on the same server: exit-empty is
    turned off before the old session is killed, so the server never starts
    exiting and the new session cannot connect to one on its way out. The
    script fails if the server's pid changed across the rebuild.
    """
    real_tmux, socket_name = tmux_socket
    script = f"""
set -euo pipefail
tmux() {{ command {real_tmux} -L {socket_name} -f /dev/null "$@"; }}
source {_DASHBOARD_CONSTANTS_SH}
source {_BOARD_WINDOW_SH}
server_pid=""
if tmux has-session -t {_SESSION} 2>/dev/null; then
    server_pid="$(tmux display-message -p -t {_SESSION} '#{{pid}}')"
    tmux set-option -s exit-empty off
    tmux kill-session -t {_SESSION}
fi
tmux new-session -d -s {_SESSION} -n main -x {width} -y {height}
if [[ -n "$server_pid" && "$(tmux display-message -p -t {_SESSION} '#{{pid}}')" != "$server_pid" ]]; then
    echo "rebuild: the tmux server changed (pid $server_pid before); the session was meant to be replaced on the same server" >&2
    exit 1
fi
tmux new-window -t {_SESSION} -n operator
dashboard_build_board_window {_SESSION} operator "{operator_shell_pct}" "${{PGAI_OPERATOR_SHELL_MIN_ROWS}}" {operator_workbench_args} {operator_column_set}
echo "operator shell=${{DASHBOARD_BOARD_SHELL_PANE}} workbench=${{DASHBOARD_BOARD_WORKBENCH_PANE}} legend=${{DASHBOARD_BOARD_LEGEND_PANE}}"
tmux new-window -t {_SESSION} -n visibility
dashboard_build_board_window {_SESSION} visibility 0
echo "visibility shell=${{DASHBOARD_BOARD_SHELL_PANE}} workbench=${{DASHBOARD_BOARD_WORKBENCH_PANE}} legend=${{DASHBOARD_BOARD_LEGEND_PANE}}"
"""
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    if not operator_workbench_args and not operator_column_set:
        assert "operator shell=8 workbench= legend=9" in result.stdout, result.stdout
    assert "visibility shell= workbench= legend=8" in result.stdout, result.stdout
    operator_line = next(line for line in result.stdout.splitlines() if line.startswith("operator "))
    operator_panes = dict(field.split("=", 1) for field in operator_line.split()[1:])

    window_height = int(
        subprocess.run(
            [real_tmux, "-L", socket_name, "display-message", "-t", f"{_SESSION}:operator", "-p", "#{window_height}"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
    )
    assert window_height == height, (
        f"fixture session was asked for {height} rows but tmux made the window "
        f"{window_height} rows; the geometry below would not be at the fixture size"
    )
    return BoardWindows(
        width=width,
        height=height,
        operator=_list_panes(real_tmux, socket_name, "operator"),
        visibility=_list_panes(real_tmux, socket_name, "visibility"),
        operator_panes=operator_panes,
        notices=[line for line in result.stderr.splitlines() if line.startswith("NOTICE:")],
    )


@pytest.mark.parametrize("size", _FIXTURE_SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_operator_columns_and_legend_have_visibility_widths(
    tmux_socket: tuple[str, str], size: tuple[int, int]
) -> None:
    board = _build_board_windows(tmux_socket, *size)

    operator_widths = [p.width for p in board.operator[:8]]
    visibility_widths = [p.width for p in board.visibility[:8]]
    assert operator_widths == visibility_widths, (
        f"operator columns {operator_widths} differ from visibility columns " f"{visibility_widths} at {size}"
    )
    assert [p.left for p in board.operator[:8]] == [p.left for p in board.visibility[:8]]
    assert board.operator[-1].width == board.visibility[-1].width == board.width


@pytest.mark.parametrize("size", _FIXTURE_SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_operator_shell_row_sits_between_board_and_legend(tmux_socket: tuple[str, str], size: tuple[int, int]) -> None:
    board = _build_board_windows(tmux_socket, *size)
    panes = board.operator
    assert [p.index for p in panes] == list(range(10)), panes

    columns, shell, legend = panes[:8], panes[8], panes[9]
    board_bottom = max(p.top + p.height for p in columns)
    assert all(p.top == 0 for p in columns), columns

    expected_shell_h = max(3, board.height * 15 // 100)
    assert (shell.left, shell.width) == (0, board.width), shell
    assert shell.height == expected_shell_h, shell
    assert (
        shell.top == board_bottom + 1
    ), f"shell row top {shell.top} is not directly below the board (bottom {board_bottom})"

    assert (legend.left, legend.width, legend.height) == (0, board.width, 1), legend
    assert (
        legend.top == shell.top + shell.height + 1
    ), f"legend top {legend.top} is not directly below the shell row {shell}"
    assert legend.top + legend.height == board.height, "legend must be the window's bottom row"


@pytest.mark.parametrize("size", _FIXTURE_SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_visibility_geometry_is_unchanged(tmux_socket: tuple[str, str], size: tuple[int, int]) -> None:
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


@pytest.mark.parametrize("args_id", sorted(_NO_WORKBENCH_ARGS))
@pytest.mark.parametrize("size", _FIXTURE_SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_operator_without_workbench_keeps_its_prior_geometry(
    tmux_socket: tuple[str, str], size: tuple[int, int], args_id: str
) -> None:
    board = _build_board_windows(tmux_socket, *size, operator_workbench_args=_NO_WORKBENCH_ARGS[args_id])
    assert [tuple(p) for p in board.operator] == _OPERATOR_GEOMETRY_WITHOUT_WORKBENCH[size]
    assert [tuple(p) for p in board.visibility] == _VISIBILITY_GEOMETRY[size]
    assert board.operator_panes == {"shell": "8", "workbench": "", "legend": "9"}
    assert board.notices == [], board.notices


def test_workbench_column_spans_board_and_shell_beside_the_board(
    tmux_socket: tuple[str, str],
) -> None:
    board = _build_board_windows(tmux_socket, 220, 50, operator_workbench_args=_WORKBENCH_ARGS)
    panes = board.operator
    assert [p.index for p in panes] == list(range(11)), panes
    assert board.operator_panes == {"shell": "8", "workbench": "9", "legend": "10"}
    assert board.notices == [], board.notices

    columns, shell, workbench, legend = panes[:8], panes[8], panes[9], panes[10]

    expected_workbench_w = board.width * _WORKBENCH_PCT // 100
    assert abs(workbench.width - expected_workbench_w) <= 1, (
        f"workbench is {workbench.width} columns wide; expected "
        f"{expected_workbench_w} +/-1 ({_WORKBENCH_PCT}% of {board.width})"
    )
    assert workbench.left + workbench.width == board.width, "workbench is not the far-right pane"
    assert workbench.top == 0, workbench
    assert (
        workbench.top + workbench.height == shell.top + shell.height
    ), f"workbench {workbench} does not span the board and shell rows (shell {shell})"

    board_w = workbench.left - 1
    assert all(p.top == 0 for p in columns), columns
    assert columns[0].left == 0
    assert columns[-1].left + columns[-1].width == board_w, (
        f"board columns end at {columns[-1].left + columns[-1].width}, not at the " f"workbench divider ({board_w})"
    )
    assert (shell.left, shell.width) == (0, board_w), shell

    assert (legend.left, legend.width, legend.height) == (0, board.width, 1), legend
    assert legend.top + legend.height == board.height, "legend must be the window's bottom row"


def test_narrow_window_builds_no_workbench_and_says_so_once(
    tmux_socket: tuple[str, str],
) -> None:
    board = _build_board_windows(tmux_socket, 120, 40, operator_workbench_args=_WORKBENCH_ARGS)
    panes = board.operator
    assert [p.index for p in panes] == list(range(10)), panes
    assert board.operator_panes == {"shell": "8", "workbench": "", "legend": "9"}

    columns, shell, legend = panes[:8], panes[8], panes[9]
    assert columns[0].left == 0
    assert columns[-1].left + columns[-1].width == board.width, (
        f"board columns end at {columns[-1].left + columns[-1].width}, not at the "
        f"window's right edge ({board.width})"
    )
    assert (shell.left, shell.width) == (0, board.width), shell
    assert (legend.left, legend.width) == (0, board.width), legend

    workbench_notices = [n for n in board.notices if "workbench threshold" in n]
    assert len(workbench_notices) == 1, board.notices
    notice = workbench_notices[0]
    assert str(board.width) in notice and str(_WORKBENCH_MIN_COLS) in notice, notice


def test_columns_beside_workbench_keep_their_pct0_proportions(
    tmux_socket: tuple[str, str],
) -> None:
    width, height = 220, 50
    without = _build_board_windows(tmux_socket, width, height)
    beside = _build_board_windows(tmux_socket, width, height, operator_workbench_args=_WORKBENCH_ARGS)
    board_w = beside.operator[9].left - 1

    for before, after in zip(without.operator[:8], beside.operator[:8]):
        scaled = before.width * board_w / width
        assert abs(after.width - scaled) <= _RATIO_TOLERANCE_COLS, (
            f"column {after.index} is {after.width} wide beside the workbench: "
            f"{after.width}/{board_w} against its pct-0 {before.width}/{width} "
            f"(scaled {scaled:.2f}, tolerance {_RATIO_TOLERANCE_COLS} columns)"
        )
        assert after.width >= _COLUMN_FLOOR, (
            f"column {after.index} is {after.width} wide, under the " f"{_COLUMN_FLOOR}-column floor"
        )


@pytest.mark.parametrize("case", sorted(_MERGED_GEOMETRY), ids=lambda c: f"{c[0]}x{c[1]}-wb{c[2].split()[0]}")
def test_merged_board_geometry(tmux_socket: tuple[str, str], case: tuple[int, int, str]) -> None:
    width, height, workbench_args = case
    board = _build_board_windows(
        tmux_socket,
        width,
        height,
        operator_workbench_args=workbench_args,
        operator_column_set="merged",
    )
    assert [tuple(p) for p in board.operator] == _MERGED_GEOMETRY[case]
    assert board.operator_panes == _MERGED_PANE_INDICES[workbench_args]
    if (width, height) in _VISIBILITY_GEOMETRY:
        assert [tuple(p) for p in board.visibility] == _VISIBILITY_GEOMETRY[(width, height)]

    columns = board.operator[:_MERGED_COLUMN_COUNT]
    workbench_pane = board.operator_panes["workbench"]
    board_w = board.operator[int(workbench_pane)].left - 1 if workbench_pane else board.width
    widths = [p.width for p in columns]
    assert sum(widths) + (_MERGED_COLUMN_COUNT - 1) == board_w, (
        f"merged columns {widths} plus {_MERGED_COLUMN_COUNT - 1} dividers do not "
        f"fill the {board_w}-column board region"
    )
    assert min(widths) >= _COLUMN_FLOOR, f"merged columns {widths} include one under the {_COLUMN_FLOOR}-column floor"


def test_merged_board_with_workbench_has_seven_panes_in_place(
    tmux_socket: tuple[str, str],
) -> None:
    board = _build_board_windows(
        tmux_socket,
        220,
        50,
        operator_workbench_args=_WORKBENCH_ARGS,
        operator_column_set="merged",
    )
    panes = board.operator
    assert [p.index for p in panes] == list(range(7)), panes
    assert board.operator_panes == {"shell": "4", "workbench": "5", "legend": "6"}
    assert board.notices == [], board.notices

    columns, shell, workbench, legend = panes[:4], panes[4], panes[5], panes[6]
    assert all(p.top == 0 for p in columns), columns
    assert columns[0].left == 0
    for left_col, right_col in zip(columns, columns[1:]):
        assert right_col.left == left_col.left + left_col.width + 1, (
            f"column {right_col.index} does not sit directly right of column " f"{left_col.index}: {columns}"
        )

    board_w = workbench.left - 1
    assert columns[-1].left + columns[-1].width == board_w
    assert (
        columns[-1].width >= _AGENTS_MIN_WIDTH
    ), f"AGENTS column is {columns[-1].width} wide, under {_AGENTS_MIN_WIDTH}"
    board_bottom = max(p.top + p.height for p in columns)
    assert (shell.left, shell.width) == (0, board_w), shell
    assert shell.top == board_bottom + 1, shell

    assert workbench.left + workbench.width == board.width, workbench
    assert workbench.top == 0
    assert workbench.top + workbench.height == shell.top + shell.height, workbench

    assert (legend.left, legend.width, legend.height) == (0, board.width, 1), legend
    assert legend.top + legend.height == board.height


@pytest.mark.parametrize("size", _FIXTURE_SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_split_column_set_keeps_the_eight_column_geometry(tmux_socket: tuple[str, str], size: tuple[int, int]) -> None:
    board = _build_board_windows(tmux_socket, *size, operator_workbench_args="0 0", operator_column_set="split")
    assert [tuple(p) for p in board.operator] == _OPERATOR_GEOMETRY_WITHOUT_WORKBENCH[size]
    assert [tuple(p) for p in board.visibility] == _VISIBILITY_GEOMETRY[size]
    assert board.operator_panes == {"shell": "8", "workbench": "", "legend": "9"}


@pytest.mark.parametrize("column_set", ["", "split"], ids=["omitted", "split"])
def test_split_column_set_with_workbench_keeps_its_geometry(tmux_socket: tuple[str, str], column_set: str) -> None:
    board = _build_board_windows(
        tmux_socket,
        220,
        50,
        operator_workbench_args=_WORKBENCH_ARGS,
        operator_column_set=column_set,
    )
    assert [tuple(p) for p in board.operator] == _OPERATOR_GEOMETRY_WITH_WORKBENCH_220X50
    assert board.operator_panes == {"shell": "8", "workbench": "9", "legend": "10"}


def test_unknown_column_set_is_refused_before_any_split(
    tmux_socket: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # No project in scope, so the emitter records no refusal-ledger entry.
    monkeypatch.delenv("PGAI_PROJECT_NAME", raising=False)
    real_tmux, socket_name = tmux_socket
    script = f"""
set -uo pipefail
tmux() {{ command {real_tmux} -L {socket_name} -f /dev/null "$@"; }}
source {_BOARD_WINDOW_SH}
tmux new-session -d -s {_SESSION} -n main -x 160 -y 48
tmux new-window -t {_SESSION} -n operator
DASHBOARD_BOARD_LEGEND_PANE=stale
dashboard_build_board_window {_SESSION} operator 15 3 0 0 three-plus-two
echo "rc=$? shell=${{DASHBOARD_BOARD_SHELL_PANE}} workbench=${{DASHBOARD_BOARD_WORKBENCH_PANE}} legend=${{DASHBOARD_BOARD_LEGEND_PANE}}"
"""
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "rc=1 shell= workbench= legend=" in result.stdout, result.stdout + result.stderr
    assert "error:   dashboard-board-window.unknown-column-set" in result.stderr, result.stderr
    assert "three-plus-two" in result.stderr, result.stderr
    assert len(_list_panes(real_tmux, socket_name, "operator")) == 1


@pytest.mark.parametrize("column_set", sorted(_COLUMN_COUNTS))
@pytest.mark.parametrize("case", _FIT_CASES, ids=lambda c: f"{c[0]}-wb{c[1].split()[0]}")
def test_columns_keep_the_floor_beside_the_workbench_or_no_workbench_is_built(
    tmux_socket: tuple[str, str], case: tuple[int, str], column_set: str
) -> None:
    width, workbench_args = case
    board = _build_board_windows(
        tmux_socket,
        width,
        _FIT_HEIGHT,
        operator_workbench_args=workbench_args,
        operator_column_set=column_set,
    )
    count = _COLUMN_COUNTS[column_set]
    columns = board.operator[:count]
    widths = [p.width for p in columns]
    context = f"{column_set} at {width} columns, workbench {workbench_args}: {board}"

    workbench_pane = board.operator_panes["workbench"]
    if workbench_pane:
        workbench = board.operator[int(workbench_pane)]
        board_w = workbench.left - 1
        assert board.notices == [], context
        assert (
            min(widths) >= _COLUMN_FLOOR
        ), f"columns {widths} include one under the {_COLUMN_FLOOR}-column floor; {context}"
        assert columns[-1].left + columns[-1].width == board_w, (
            f"columns end at {columns[-1].left + columns[-1].width}, not at the "
            f"workbench divider ({board_w}); {context}"
        )
        assert sum(widths) + count - 1 == board_w, context
    else:
        assert len(board.operator) == count + 2, context  # columns, shell, legend
        assert len(board.notices) == 1, context
        assert str(width) in board.notices[0], context
        assert (
            columns[-1].left + columns[-1].width == board.width
        ), f"without a workbench the columns must reach the window's right edge; {context}"


def _operator_geometry(tmux_socket: tuple[str, str], window: str = "operator") -> list[tuple[int, ...]]:
    real_tmux, socket_name = tmux_socket
    return [tuple(p) for p in _list_panes(real_tmux, socket_name, window)]


def _window_names(tmux_socket: tuple[str, str], session: str = _SESSION) -> list[str]:
    real_tmux, socket_name = tmux_socket
    return subprocess.run(
        [real_tmux, "-L", socket_name, "list-windows", "-t", session, "-F", "#{window_name}"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.split()


def _resize_and_wait_for(
    tmux_socket: tuple[str, str], width: int, height: int, expected: list[tuple[int, ...]]
) -> None:
    """resize-window the operator window, then wait until the hook has given
    it *expected*; fail with the last geometry seen."""
    real_tmux, socket_name = tmux_socket
    subprocess.run(
        [
            real_tmux,
            "-L",
            socket_name,
            "resize-window",
            "-t",
            f"{_SESSION}:operator",
            "-x",
            str(width),
            "-y",
            str(height),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    deadline = time.monotonic() + _REFLOW_WAIT_S
    geometry = _operator_geometry(tmux_socket)
    while geometry != expected and time.monotonic() < deadline:
        time.sleep(0.2)
        geometry = _operator_geometry(tmux_socket)
    assert geometry == expected, (
        f"at {width}x{height} the operator window is {geometry}, expected {expected}; "
        f"windows {_window_names(tmux_socket)}"
    )


def test_reflow_hook_parks_the_workbench_when_narrow_and_rejoins_it_when_wide(
    tmux_socket: tuple[str, str],
) -> None:
    narrow = _build_board_windows(tmux_socket, 120, 50, operator_workbench_args="0 0", operator_column_set="merged")
    pct0_at_120 = [tuple(p) for p in narrow.operator]

    built = _build_board_windows(
        tmux_socket,
        220,
        50,
        operator_workbench_args=_WORKBENCH_ARGS,
        operator_column_set="merged",
    )
    build_geometry = [tuple(p) for p in built.operator]
    assert build_geometry == _MERGED_GEOMETRY[(220, 50, _WORKBENCH_ARGS)]

    real_tmux, socket_name = tmux_socket
    install = subprocess.run(
        [
            "bash",
            "-c",
            f"""
set -euo pipefail
tmux() {{ command {real_tmux} -L {socket_name} -f /dev/null "$@"; }}
source {_BOARD_WINDOW_SH}
dashboard_install_board_reflow_hook {_SESSION}:operator {_BOARD_REFLOW_SH}
""",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert install.returncode == 0, install.stdout + install.stderr
    # A session created after the board's is the server's most recent one:
    # the parked workbench must still land in the board's own session.
    subprocess.run(
        [real_tmux, "-L", socket_name, "new-session", "-d", "-s", _OTHER_SESSION, "-x", "80", "-y", "24"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )

    _resize_and_wait_for(tmux_socket, 120, 50, pct0_at_120)
    assert _PARKED_WORKBENCH_WINDOW in _window_names(tmux_socket)
    other_windows = _window_names(tmux_socket, _OTHER_SESSION)
    assert len(other_windows) == 1, f"the other session gained a window: {other_windows}"

    _resize_and_wait_for(tmux_socket, 220, 50, build_geometry)
    assert _PARKED_WORKBENCH_WINDOW not in _window_names(tmux_socket)


def _board_notice(tmux_socket: tuple[str, str], window: str) -> str:
    """The degraded-layout notice the builder or the last reflow recorded."""
    real_tmux, socket_name = tmux_socket
    return subprocess.run(
        [real_tmux, "-L", socket_name, "display-message", "-p", "-t", f"{_SESSION}:{window}", "#{@pgai_board_notice}"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()


def _install_reflow_hook(tmux_socket: tuple[str, str], window: str) -> None:
    real_tmux, socket_name = tmux_socket
    install = subprocess.run(
        [
            "bash",
            "-c",
            f"""
set -euo pipefail
tmux() {{ command {real_tmux} -L {socket_name} -f /dev/null "$@"; }}
source {_BOARD_WINDOW_SH}
dashboard_install_board_reflow_hook {_SESSION}:{window} {_BOARD_REFLOW_SH}
""",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert install.returncode == 0, install.stdout + install.stderr


def _matches_within(geometry: list[tuple[int, ...]], expected: list[tuple[int, ...]], tolerance: int) -> bool:
    """Same panes, same heights, every width within *tolerance* columns."""
    if [p[0] for p in geometry] != [p[0] for p in expected]:
        return False
    return all(abs(got[3] - want[3]) <= tolerance and got[4] == want[4] for got, want in zip(geometry, expected))


@pytest.mark.parametrize("width", _SPLIT_FLOOR_CASES)
def test_split_board_keeps_the_floor_or_shares_the_width_evenly(tmux_socket: tuple[str, str], width: int) -> None:
    # The operator window is the split board with a 15% shell and no
    # workbench under a 160-column threshold; the visibility window is the
    # split board alone.
    board = _build_board_windows(
        tmux_socket,
        width,
        _RESIZE_HEIGHT,
        operator_workbench_args="0 160",
        operator_column_set="split",
    )
    for window, panes in (("operator", board.operator), ("visibility", board.visibility)):
        widths = [p.width for p in panes[:8]]
        context = f"{window} at {width} columns: widths {widths}, notices {board.notices}"
        assert sum(widths) + 7 == width, f"the eight columns and seven dividers do not fill the window; {context}"
        window_notices = [n for n in board.notices if f"{_SESSION}:{window} " in n]
        if width >= _SPLIT_FLOOR_WIDTH:
            assert min(widths) >= _COLUMN_FLOOR, f"a column is under the {_COLUMN_FLOOR}-column floor; {context}"
            assert window_notices == [], context
            assert _board_notice(tmux_socket, window) == "", context
        else:
            assert max(widths) - min(widths) <= 1, f"the degraded layout must share the width evenly; {context}"
            assert min(widths) == (width - 7) // 8, context
            assert len(window_notices) == 1, context
            notice = window_notices[0]
            assert str(width) in notice and str(_SPLIT_FLOOR_WIDTH) in notice, context
            assert _board_notice(tmux_socket, window) == notice, context


@pytest.mark.parametrize("case_id", sorted(_RESIZE_CASES))
def test_reflow_hook_reapplies_the_builder_widths_after_each_resize(tmux_socket: tuple[str, str], case_id: str) -> None:
    window, workbench_args, column_set = _RESIZE_CASES[case_id]

    # What the builder gives at each target size: a fresh build there.
    expected: dict[int, list[tuple[int, ...]]] = {}
    expected_notice: dict[int, bool] = {}
    for width in _RESIZE_WIDTHS:
        fresh = _build_board_windows(
            tmux_socket,
            width,
            _RESIZE_HEIGHT,
            operator_workbench_args=workbench_args,
            operator_column_set=column_set,
        )
        expected[width] = [tuple(p) for p in getattr(fresh, window)]
        expected_notice[width] = bool(_board_notice(tmux_socket, window))

    _build_board_windows(
        tmux_socket,
        220,
        _RESIZE_HEIGHT,
        operator_workbench_args=workbench_args,
        operator_column_set=column_set,
    )
    _install_reflow_hook(tmux_socket, window)

    real_tmux, socket_name = tmux_socket
    for width in _RESIZE_WIDTHS:
        subprocess.run(
            [
                real_tmux,
                "-L",
                socket_name,
                "resize-window",
                "-t",
                f"{_SESSION}:{window}",
                "-x",
                str(width),
                "-y",
                str(_RESIZE_HEIGHT),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        deadline = time.monotonic() + _REFLOW_WAIT_S
        geometry = _operator_geometry(tmux_socket, window)
        while not _matches_within(geometry, expected[width], _RESIZE_TOLERANCE_COLS) and time.monotonic() < deadline:
            time.sleep(0.2)
            geometry = _operator_geometry(tmux_socket, window)
        assert _matches_within(geometry, expected[width], _RESIZE_TOLERANCE_COLS), (
            f"{case_id}: after resizing to {width}x{_RESIZE_HEIGHT} the {window} window is "
            f"{geometry}; a fresh build at that size gives {expected[width]}"
        )
        assert bool(_board_notice(tmux_socket, window)) == expected_notice[width], (
            f"{case_id} at {width} columns: @pgai_board_notice is "
            f"{_board_notice(tmux_socket, window)!r}; a fresh build "
            f"{'records' if expected_notice[width] else 'records no'} notice"
        )


def _resize_window(tmux_socket: tuple[str, str], window: str, width: int, height: int) -> None:
    real_tmux, socket_name = tmux_socket
    subprocess.run(
        [real_tmux, "-L", socket_name, "resize-window", "-t", f"{_SESSION}:{window}", "-x", str(width), "-y", str(height)],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )


def _wait_for_board_notice(tmux_socket: tuple[str, str], window: str, *, present: bool) -> str:
    """Wait until the hook's reflow has set (*present*) or cleared the
    window's board notice; fail with the notice last seen."""
    deadline = time.monotonic() + _REFLOW_WAIT_S
    notice = _board_notice(tmux_socket, window)
    while bool(notice) != present and time.monotonic() < deadline:
        time.sleep(0.2)
        notice = _board_notice(tmux_socket, window)
    assert bool(notice) == present, (
        f"{window}: @pgai_board_notice is {notice!r}; expected the reflow to " f"{'record' if present else 'clear'} it"
    )
    return notice


def _render_legend_pane(
    tmux_socket: tuple[str, str], window: str, root: pathlib.Path, home: pathlib.Path
) -> tuple[str, int]:
    """Run legend-render.sh the way *window*'s legend pane (its last pane)
    runs it -- TMUX naming the private server, TMUX_PANE the legend pane --
    and return its stdout and the pane's width."""
    real_tmux, socket_name = tmux_socket
    legend = subprocess.run(
        [
            real_tmux,
            "-L",
            socket_name,
            "list-panes",
            "-t",
            f"{_SESSION}:{window}",
            "-F",
            "#{socket_path},#{pid},#{session_id} #{pane_id} #{pane_width}",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.splitlines()[-1]
    tmux_env, pane_id, pane_width = legend.split()
    env = build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[pathlib.Path(sys.executable).parent, pathlib.Path(real_tmux).parent],
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
            "NO_COLOR": "1",
            "TERM": "dumb",
            "TMUX": tmux_env.replace("$", ""),
            "TMUX_PANE": pane_id,
        },
    )
    result = subprocess.run(
        ["bash", str(_LEGEND_RENDER_SH), "--kanban-root", str(root)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(root),
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout, int(pane_width)


def test_legend_bar_shows_the_board_notice_until_a_reflow_clears_it(
    tmux_socket: tuple[str, str], tmp_path: pathlib.Path
) -> None:
    # The split operator board without a workbench, built at 220 columns,
    # then resized to 120 -- under the 127 its eight columns need.
    root = tmp_path / "root"
    (root / "tmp").mkdir(parents=True)
    (root / "projects.cfg").write_text("[project:demo]\npriority = 1\ndashboard_color = #FF8800\n", encoding="utf-8")
    home = tmp_path / "home"
    _build_board_windows(
        tmux_socket, 220, _RESIZE_HEIGHT, operator_workbench_args="0 160", operator_column_set="split"
    )
    _install_reflow_hook(tmux_socket, "operator")
    legend, _width = _render_legend_pane(tmux_socket, "operator", root, home)
    assert legend.startswith("PROJECT:"), legend

    _resize_window(tmux_socket, "operator", 120, _RESIZE_HEIGHT)
    notice = _wait_for_board_notice(tmux_socket, "operator", present=True)
    assert notice.startswith("NOTICE:") and "120" in notice and str(_SPLIT_FLOOR_WIDTH) in notice, notice
    shown, width = _render_legend_pane(tmux_socket, "operator", root, home)
    assert width == 120, f"the legend pane is {width} columns wide after the resize to 120"
    assert shown == notice[:width] + "\n", f"the legend bar shows {shown!r}; the window records {notice!r}"

    _resize_window(tmux_socket, "operator", 220, _RESIZE_HEIGHT)
    _wait_for_board_notice(tmux_socket, "operator", present=False)
    cleared, _width = _render_legend_pane(tmux_socket, "operator", root, home)
    assert cleared == legend, f"after the notice is cleared the bar shows {cleared!r}, not the legend {legend!r}"
