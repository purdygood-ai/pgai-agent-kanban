"""
test_dashboard_column_render_module.py — Unit tests for
pgai_agent_kanban.dashboard.column_render, the renderer behind the
dashboard's board column panes (team/scripts/dashboard/column-render.sh).

Coverage:

  1. cells — display status and colour for every board status (WONT-DO
     drawn like DONE but kept distinct), ANSI-aware truncation, compact IDs,
     and the two row-window selections.
  2. single — the input column (mtime order, requirements active window,
     max_rows cut, deferral glyph) and the queue column (status.md wins
     over the queue marker, middle-position window, max_rows cut).
  3. multi — row allocation, the environment contract, project tags, and
     requirements classified by target version alone, as GET /board does.
  4. The command line — each mode prints the pane; --help exits 0.
"""

from __future__ import annotations

import os
import pathlib
import re
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.dashboard.column_render import (  # noqa: E402
    DeferCards,
    Layout,
    Palette,
    ProjectSource,
    display_status,
    render_input_column,
    render_multi_project_column,
    render_queue_column,
    sources_from_env,
    status_color,
)
from pgai_agent_kanban.dashboard.column_render import __main__ as cli  # noqa: E402
from pgai_agent_kanban.dashboard.column_render.cells import (  # noqa: E402
    active_window,
    compact_id,
    middle_position_window,
    truncate_ansi,
    visible_len,
)
from pgai_agent_kanban.dashboard.column_render.multi import compute_layout  # noqa: E402

RED, YELLOW, GREEN, WHITE, DIM, RESET = (
    "\033[0;31m", "\033[0;33m", "\033[0;32m", "\033[0;37m", "\033[2m", "\033[0m",
)
COLOR = Palette(True, RED, YELLOW, GREEN, WHITE, DIM, RESET)
PLAIN = Palette(False)
_SGR_RE = re.compile(r"\033\[[0-9;]*m")


def _write(path: pathlib.Path, text: str, mtime: int = 0) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (1_700_000_000 + mtime, 1_700_000_000 + mtime))
    return path


def _plain(lines: list) -> list:
    return [_SGR_RE.sub("", line) for line in lines]


# ---------------------------------------------------------------------------
# 1. cells
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "board_status, shown, colour",
    [
        ("working", "running", YELLOW),
        ("label", "running", YELLOW),
        ("done", "done", GREEN),
        ("wont-do", "wont-do", GREEN),
        ("blocked", "blocked", RED),
        ("open", "open", WHITE),
        ("anything-else", "open", WHITE),
    ],
)
def test_each_board_status_has_one_display_status_and_colour(board_status, shown, colour) -> None:
    assert display_status(board_status) == shown
    assert status_color(display_status(board_status), COLOR) == colour


def test_wont_do_stays_distinct_from_done_but_shares_its_colour() -> None:
    assert display_status("wont-do") != display_status("done")
    assert status_color(display_status("wont-do"), COLOR) == status_color(display_status("done"), COLOR)


def test_truncate_keeps_short_text_and_cuts_long_text_to_width() -> None:
    assert truncate_ansi("BUG-0001", 8, PLAIN) == "BUG-0001"
    cut = truncate_ansi("BUG-0001-long", 6, PLAIN)
    assert cut == "BUG-0…"
    assert visible_len(cut) == 6


def test_truncate_ignores_escapes_for_width_and_resets_before_the_ellipsis() -> None:
    text = f"{GREEN}20260101-005{RESET}"
    assert truncate_ansi(text, 12, COLOR) == text
    cut = truncate_ansi(text, 6, COLOR)
    assert cut == f"{GREEN}20260{RESET}…"
    assert visible_len(cut) == 6


@pytest.mark.parametrize(
    "stem, is_req, expected",
    [
        ("BUG-0042-some-slug", False, "BUG-0042"),
        ("PRIORITY-007-x", False, "PRIORITY-007"),
        ("notes", False, "notes"),
        ("v1.2.3-bundle-name", True, "v1.2.3"),
        ("unversioned", True, "unversioned"),
    ],
)
def test_compact_id_shows_the_id_part_of_a_stem(stem, is_req, expected) -> None:
    assert compact_id(stem, is_req) == expected


def test_middle_position_window_puts_open_above_active_above_closed() -> None:
    items = [("o1", "UNKNOWN"), ("a1", "WORKING"), ("c1", "DONE"), ("a2", "BACKLOG"), ("c2", "WONT-DO")]
    window = middle_position_window(items, lambda e: e[1], 5)
    assert [e[0] for e in window] == ["o1", "a1", "a2", "c1", "c2"]


def test_middle_position_window_gives_unused_rows_to_the_other_bucket() -> None:
    closed = [(f"c{i}", "DONE") for i in range(6)]
    window = middle_position_window([("a", "WORKING")] + closed, lambda e: e[1], 4)
    assert [e[0] for e in window] == ["a", "c0", "c1", "c2"]


@pytest.mark.parametrize(
    "n, rows, active, expected",
    [
        (20, 5, 1, (0, 4)),     # near the start: pinned to the top, one marker below
        (20, 5, 18, (16, 20)),  # near the end: pinned to the bottom, one marker above
        (20, 5, 10, (9, 12)),   # middle: centred, markers on both sides
    ],
)
def test_active_window_keeps_the_active_item_visible(n, rows, active, expected) -> None:
    start, end = active_window(n, rows, active)
    assert (start, end) == expected
    assert start <= active < end
    markers = (1 if start > 0 else 0) + (1 if end < n else 0)
    assert (end - start) + markers <= rows


# ---------------------------------------------------------------------------
# 2. single
# ---------------------------------------------------------------------------


def test_input_column_lists_newest_first_and_colours_by_status(tmp_path) -> None:
    bugs = tmp_path / "bugs"
    _write(bugs / "BUG-0001-a.md", "## Status\nopen\n", 1)
    _write(bugs / "BUG-0002-b.md", "## Status\nwont-do\n", 3)
    _write(bugs / "BUG-0003-c.md", "## Status\nrunning\n", 2)
    _write(bugs / "README.md", "## Status\nrunning\n", 9)
    lines = render_input_column(
        bugs, rows=10, width=38, is_requirements=False, active_rc="", last_released="",
        max_rows=20, palette=COLOR,
    )
    assert lines == [f"{GREEN}BUG-0002{RESET}", f"{YELLOW}BUG-0003{RESET}", f"{WHITE}BUG-0001{RESET}"]


def test_input_column_cuts_to_max_rows_with_a_more_row(tmp_path) -> None:
    for i in range(5):
        _write(tmp_path / "bugs" / f"BUG-000{i}.md", "", i)
    lines = render_input_column(
        tmp_path / "bugs", rows=10, width=38, is_requirements=False, active_rc="", last_released="",
        max_rows=3, palette=PLAIN,
    )
    assert lines == ["BUG-0004", "BUG-0003", "... and 3 more"]


def test_requirements_window_follows_the_active_rc_and_ignores_file_status(tmp_path) -> None:
    reqs = tmp_path / "requirements"
    for minor in range(10):
        status = "running" if minor == 2 else "done"
        _write(reqs / f"v1.{minor}.0-req.md", f"## Target Version\nv1.{minor}.0\n\n## Status\n{status}\n", 10 - minor)
    lines = render_input_column(
        reqs, rows=4, width=38, is_requirements=True, active_rc="v1.6.0", last_released="v1.3.0",
        max_rows=20, palette=COLOR,
    )
    assert lines == [
        "... (5 more above)",
        f"{WHITE}v1.5.0{RESET}",
        f"{YELLOW}v1.6.0 [active]{RESET}",
        "... (3 more below)",
    ]
    full = render_input_column(
        reqs, rows=20, width=38, is_requirements=True, active_rc="v1.6.0", last_released="v1.3.0",
        max_rows=20, palette=COLOR,
    )
    # v1.2.0's own "## Status running" plays no part: it is shipped, so green.
    assert f"{GREEN}v1.2.0{RESET}" in full
    assert f"{WHITE}v1.9.0{RESET}" in full


def test_missing_or_empty_input_directory_renders_empty(tmp_path) -> None:
    (tmp_path / "empty").mkdir()
    for path in (tmp_path / "absent", tmp_path / "empty"):
        assert render_input_column(
            path, rows=5, width=20, is_requirements=False, active_rc="", last_released="",
            max_rows=20, palette=PLAIN,
        ) == ["(empty)"]


def test_malformed_posture_warns_only_on_cards_whose_file_says_open(tmp_path) -> None:
    bugs = tmp_path / "bugs"
    _write(bugs / "BUG-0001.md", "## Status\nopen\n", 3)
    _write(bugs / "BUG-0002.md", "no status heading\n", 2)
    _write(bugs / "BUG-0003.md", "## Status\ndone\n", 1)
    defer = DeferCards(mode="malformed", glyph_defer_warning="!")
    lines = _plain(render_input_column(
        bugs, rows=5, width=38, is_requirements=False, active_rc="", last_released="",
        max_rows=20, palette=COLOR, defer=defer,
    ))
    assert lines == ["BUG-0001 !", "BUG-0002", "BUG-0003"]


def _queue(tmp_path: pathlib.Path, entries: list) -> tuple:
    """Write a backlog of (marker, task_id, state-or-None) and return (backlog, tasks_dir)."""
    tasks = tmp_path / "tasks"
    lines = ["# CODER backlog", ""]
    for marker, task_id, state in entries:
        lines.append(f"- [{marker}] {task_id} — x")
        if state is not None:
            _write(tasks / task_id / "status.md", f"## State\n{state}\n")
    backlog = _write(tasks / "queues" / "coder_backlog.md", "\n".join(lines) + "\n")
    return backlog, tasks


def test_queue_column_reads_state_from_status_md_before_the_marker(tmp_path) -> None:
    backlog, tasks = _queue(tmp_path, [
        (" ", "CODER-20260101-001-stale-marker", "BLOCKED"),
        ("x", "CODER-20260101-002-wont-do", "WONT-DO"),
        ("x", "CODER-20260101-003-done", "DONE"),
        ("A", "CODER-20260101-004-no-status", None),
    ])
    lines = render_queue_column(backlog, rows=10, width=22, tasks_dir=tasks, max_rows=20, palette=COLOR)
    assert lines == [
        f"{YELLOW}20260101-004{RESET}",
        f"{GREEN}20260101-003{RESET}",
        f"{GREEN}20260101-002{RESET}",
        f"{RED}20260101-001{RESET}",
    ]


def test_queue_column_cuts_to_max_rows_with_a_more_row(tmp_path) -> None:
    backlog, tasks = _queue(tmp_path, [(" ", f"CODER-20260101-{i:03d}-t", None) for i in range(1, 6)])
    lines = render_queue_column(backlog, rows=10, width=22, tasks_dir=tasks, max_rows=3, palette=PLAIN)
    assert lines == ["20260101-005", "20260101-004", "... and 3 more"]


def test_missing_backlog_renders_empty(tmp_path) -> None:
    assert render_queue_column(
        tmp_path / "nope.md", rows=5, width=22, tasks_dir=tmp_path, max_rows=20, palette=PLAIN,
    ) == ["(empty)"]


# ---------------------------------------------------------------------------
# 3. multi
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "n_projects, expected",
    [(1, (8, 13)), (4, (8, 32)), (5, (6, 30)), (20, (3, 34))],
)
def test_compute_layout_shrinks_blocks_to_fit_the_column(n_projects, expected) -> None:
    assert compute_layout(n_projects, Layout()) == expected


def test_sources_from_env_pads_short_lists_by_position() -> None:
    env = {
        "_PROJ_NAMES_STR": "alpha\nbeta\n",
        "_PROJ_COLORS_STR": "#378ADD\n",
        "_PROJ_MAX_ROWS_STR": "5\nzero\n",
        "_PROJ_BACKLOG_FILES_STR": "/a/q.md\n",
        "_ALL_ACTIVE_RCS": "v1.0.0\nnone\n",
        "_PROJ_DEFER_INFO_STR": "on\x1f\x1fBUG-0001|BUG-0002\n",
        "DFS_SEP": "\x1f",
        "GLYPH_DEFERRED_BUG": "D",
    }
    alpha, beta = sources_from_env(env)
    assert (alpha.name, alpha.color, alpha.max_rows, alpha.backlog_file, alpha.active_rc) == (
        "alpha", "#378ADD", 5, "/a/q.md", "v1.0.0",
    )
    assert alpha.defer.mode == "on" and alpha.defer.ids == {"BUG-0001", "BUG-0002"}
    assert beta.color.startswith("#") and beta.max_rows == 20
    assert (beta.backlog_file, beta.active_rc, beta.defer.mode) == ("none", "", "off")


def test_multi_column_renders_one_tagged_block_per_project(tmp_path) -> None:
    a_backlog, a_tasks = _queue(tmp_path / "a", [("x", "CODER-20260101-001-a", "WONT-DO")])
    b_backlog, b_tasks = _queue(tmp_path / "b", [("A", "CODER-20260102-001-b", "WORKING")])
    sources = [
        ProjectSource("alpha", "#378ADD", backlog_file=str(a_backlog), tasks_dir=str(a_tasks)),
        ProjectSource("betaproject", "#D85A30", backlog_file=str(b_backlog), tasks_dir=str(b_tasks)),
    ]
    plain = render_multi_project_column(sources, subcommand="queue", column_type="coder", width=22, palette=PLAIN)
    assert plain == ["[alph] 20260101-001", "[beta] 20260102-001"]
    coloured = render_multi_project_column(sources, subcommand="queue", column_type="coder", width=22, palette=COLOR)
    assert coloured[0] == f"\033[38;2;55;138;221m■{RESET} {GREEN}20260101-001{RESET}"
    assert coloured[1].endswith(f"{YELLOW}20260102-001{RESET}")


def test_multi_requirements_classify_by_target_version_alone(tmp_path) -> None:
    reqs = tmp_path / "requirements"
    _write(reqs / "v1.0.0-shipped.md", "## Target Version\nv1.0.0\n\n## Status\nopen\n")
    _write(reqs / "v1.1.0-active.md", "## Target Version\nv1.1.0\n\n## Status\nopen\n")
    _write(reqs / "v1.2.0-decomposing.md", "## Target Version\nv1.2.0\n\n## Status\nrunning\n")
    source = ProjectSource("alpha", "#378ADD", input_dir=str(reqs), active_rc="v1.1.0", last_released="v1.0.0")
    lines = render_multi_project_column(
        [source], subcommand="input", column_type="requirements", width=38, palette=COLOR,
    )
    assert [line.split(" ", 1)[1] for line in lines] == [
        f"{WHITE}v1.2.0{RESET}",
        f"{YELLOW}v1.1.0 [active]{RESET}",
        f"{GREEN}v1.0.0{RESET}",
    ]


def test_multi_column_with_no_rows_renders_empty() -> None:
    source = ProjectSource("alpha", "#378ADD")
    assert render_multi_project_column([source], subcommand="input", column_type="bugs", width=20, palette=PLAIN) == ["(empty)"]


# ---------------------------------------------------------------------------
# 4. The command line
# ---------------------------------------------------------------------------


def test_cli_queue_mode_prints_the_pane(tmp_path, capsys) -> None:
    backlog, tasks = _queue(tmp_path, [("x", "CODER-20260101-001-a", "WONT-DO")])
    assert cli.main([
        "queue", f"--backlog-file={backlog}", "--rows=5", "--width=22", f"--tasks-dir={tasks}",
        "--max-rows=20", "--use-color=true", f"--green={GREEN}", f"--reset={RESET}",
    ]) == 0
    assert capsys.readouterr().out == f"{GREEN}20260101-001{RESET}\n"


def test_cli_input_mode_reads_a_non_numeric_max_rows_as_twenty(tmp_path, capsys) -> None:
    for i in range(3):
        _write(tmp_path / "bugs" / f"BUG-000{i}.md", "", i)
    assert cli.main(["input", f"--input-dir={tmp_path / 'bugs'}", "--rows=5", "--width=20", "--max-rows=bogus"]) == 0
    assert capsys.readouterr().out == "BUG-0002\nBUG-0001\nBUG-0000\n"


def test_cli_multi_mode_reads_projects_from_the_environment(tmp_path, capsys, monkeypatch) -> None:
    backlog, tasks = _queue(tmp_path, [(" ", "CODER-20260101-001-a", "BACKLOG")])
    monkeypatch.setenv("_PROJ_NAMES_STR", "alpha\n")
    monkeypatch.setenv("_PROJ_COLORS_STR", "#378ADD\n")
    monkeypatch.setenv("_PROJ_BACKLOG_FILES_STR", f"{backlog}\n")
    monkeypatch.setenv("_PROJ_TASKS_DIRS_STR", f"{tasks}\n")
    assert cli.main(["multi", "--subcommand=queue", "--column-type=coder", "--width=22"]) == 0
    assert capsys.readouterr().out == "[alph] 20260101-001\n"


def test_cli_help_exits_zero(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    assert "input" in capsys.readouterr().out
