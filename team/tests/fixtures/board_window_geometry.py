"""
board_window_geometry.py
========================
Pane-geometry goldens for the dashboard's two board windows, shared by the
tests that pin them: the board builder's own geometry test
(tests/unit/test_dashboard_board_window_geometry.py, which builds the
windows by calling dashboard_build_board_window directly) and the create.sh
test (tests/unit/test_dashboard_operator_shell_init.py, which checks that
the session create.sh builds has exactly these panes).

Every value is (pane_index, pane_left, pane_top, pane_width, pane_height),
as ``tmux list-panes -F "#{pane_index} #{pane_left} #{pane_top}
#{pane_width} #{pane_height}"`` reports it.
"""

from __future__ import annotations

# Window 2's pane geometry at each fixture size, recorded from the
# visibility window's own split-and-resize code before it moved into the
# shared builder: (pane_index, pane_left, pane_top, pane_width, pane_height).
VISIBILITY_GEOMETRY = {
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


# Window 1's eight-column geometry at 220x50 with the 40% / 160-column
# workbench, recorded from the builder before it learned the column set:
# (pane_index, pane_left, pane_top, pane_width, pane_height).
OPERATOR_SPLIT_GEOMETRY_WITH_WORKBENCH_220X50 = [
    (0, 0, 0, 15, 40),
    (1, 16, 0, 17, 40),
    (2, 34, 0, 17, 40),
    (3, 52, 0, 15, 40),
    (4, 68, 0, 15, 40),
    (5, 84, 0, 15, 40),
    (6, 100, 0, 15, 40),
    (7, 116, 0, 15, 40),
    (8, 0, 41, 131, 7),
    (9, 132, 0, 88, 48),
    (10, 0, 49, 220, 1),
]


# Window 1's merged (3 + 1) geometry: BUGS, PRIORITIES, REQUIREMENTS,
# AGENTS, then the shell row, [the workbench,] and the legend. Keyed by
# (width, height, workbench args); values are (pane_index, pane_left,
# pane_top, pane_width, pane_height).
OPERATOR_MERGED_GEOMETRY = {
    (160, 48, "0 0"): [
        (0, 0, 0, 35, 38),
        (1, 36, 0, 42, 38),
        (2, 79, 0, 41, 38),
        (3, 121, 0, 39, 38),
        (4, 0, 39, 160, 7),
        (5, 0, 47, 160, 1),
    ],
    (237, 60, "0 0"): [
        (0, 0, 0, 52, 48),
        (1, 53, 0, 63, 48),
        (2, 117, 0, 61, 48),
        (3, 179, 0, 58, 48),
        (4, 0, 49, 237, 9),
        (5, 0, 59, 237, 1),
    ],
    (220, 50, "40 160"): [
        (0, 0, 0, 28, 40),
        (1, 29, 0, 35, 40),
        (2, 65, 0, 33, 40),
        (3, 99, 0, 32, 40),
        (4, 0, 41, 131, 7),
        (5, 132, 0, 88, 48),
        (6, 0, 49, 220, 1),
    ],
}
