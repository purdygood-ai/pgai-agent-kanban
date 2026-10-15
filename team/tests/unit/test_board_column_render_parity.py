"""
test_board_column_render_parity.py — GET /board and the dashboard's column
panes (team/scripts/dashboard/column-render.sh) assign every item on one
board the same status, WONT-DO included, in both the single-project and the
multi-project (--all-projects) pane.

The board is the golden board (tests/fixtures/golden_board.py, with its
CODER queue: one task per state) plus intake files for every bug status
(and one still carrying the retired "superseded", which reads as wont-do),
a requirements column spanning shipped / active-RC / unshipped targets, and
one task whose queue marker disagrees with its status.md.

Two checks per column:

  1. Classification — the renderer's own entry builders
     (pgai_agent_kanban.dashboard.column_render: single.queue_items,
     single.classify_input_file, multi.gather_project_entries) give each
     item exactly the status GET /board reports, so "wont-do" stays
     "wont-do" rather than "done".
  2. Rendering — column-render.sh, run end to end in each mode, draws the
     same set of items /board lists, each in the colour of its /board
     status (done and wont-do share green; an active-RC requirement carries
     " [active]").
"""

from __future__ import annotations

import pathlib
import re
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.board import get_active_rc_for_project, get_last_released_for_project  # noqa: E402
from pgai_agent_kanban.dashboard.column_render import ProjectSource, gather_project_entries  # noqa: E402
from pgai_agent_kanban.dashboard.column_render.cells import compact_id  # noqa: E402
from pgai_agent_kanban.dashboard.column_render.single import classify_input_file, queue_items  # noqa: E402
from tests.fixtures.dashboard_render_fixture import (  # noqa: E402
    AMBIENT_LEAK_ENV_VARS_TO_CLEAR,
    clean_subprocess_env,
    run_dashboard_script,
)
from tests.fixtures.golden_board import PROJECT_NAME, build_synthetic_board  # noqa: E402
from tests.fixtures.route_cli_parity import build_api_client  # noqa: E402

_PKG_DIR = _TEAM_DIR / "pgai_agent_kanban"

#: The SGR parameters column-render.sh draws each /board status in.
_SGR_BY_BOARD_STATUS = {
    "open": "0;37",
    "working": "0;33",
    "label": "0;33",
    "done": "0;32",
    "wont-do": "0;32",
    "blocked": "0;31",
}

_STALE_MARKER_TASK = "CODER-20260101-007-golden-stale-marker"

# One row: the SGR escape opening the item's text, then its display ID.
_ROW_RE = re.compile(r"\x1b\[([0-9;]*)m(BUG-\d+|v\d+\.\d+\.\d+(?: \[active\])?|\d{8}-\d{3})\x1b\[0m")


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _add_board_items(project_dir: pathlib.Path) -> None:
    """Widen the golden board: every bug status, a requirements column, a stale marker."""
    for number, status in ((2, "wont-do"), (3, "done"), (4, "running"), (5, "blocked"), (6, "superseded")):
        _write(project_dir / "bugs" / f"BUG-000{number}-parity-{status}.md",
               f"# BUG-000{number}\n\n**Severity:** high\n\n## Status\n{status}\n")
    for version, status in (("v1.0.0", "done"), ("v1.2.0", "running"), ("v1.3.0", "running"), ("v1.4.0", "wont-do")):
        _write(project_dir / "requirements" / f"{version}-parity.md",
               f"# {version}\n\n## Target Version\n{version}\n\n## Status\n{status}\n")
    _write(project_dir / "release-state.md",
           "# Release State\n\n## Active RC\nv1.2.0\n\n## RC Opened At\n2026-01-01T00:00:00Z\n\n"
           "## RC Opened By Task\nparity-fixture\n\n## Last Released\nv1.1.0\n")
    _write(project_dir / "tasks" / _STALE_MARKER_TASK / "status.md", "## State\nBLOCKED\n")
    backlog = project_dir / "tasks" / "queues" / "coder_backlog.md"
    backlog.write_text(backlog.read_text(encoding="utf-8") + f"- [ ] {_STALE_MARKER_TASK} — stale marker\n",
                       encoding="utf-8")


@pytest.fixture
def board_root(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    root = tmp_path / "board"
    build_synthetic_board(root, with_queues=True)
    _add_board_items(root / "projects" / PROJECT_NAME)
    # column-render.sh reaches the package beside its own scripts/ tree.
    (root / "pgai_agent_kanban").symlink_to(_PKG_DIR, target_is_directory=True)
    for key in AMBIENT_LEAK_ENV_VARS_TO_CLEAR:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
    monkeypatch.setenv("KANBAN_ROOT", str(root))
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(scratch))
    return root


def _display_id(column: str, key: str) -> str:
    if column == "CODER":
        date, seq = re.match(r"^[A-Z]+-(\d{8})-(\d+)-", key).groups()
        return f"{date}-{int(seq):03d}"
    return key


def _board(root: pathlib.Path) -> dict:
    """Return {column: {display_id: status}} from GET /board."""
    with build_api_client(root) as client:
        resp = client.get("/board")
    assert resp.status_code == 200
    return {
        column["name"]: {_display_id(column["name"], item["key"]): item["status"] for item in column["items"]}
        for column in resp.json()["columns"]
        if column["name"] in ("BUGS", "REQUIREMENTS", "CODER")
    }


def _render(root: pathlib.Path, tmp_path: pathlib.Path, *args: str) -> dict:
    """Run column-render.sh in colour; return {display_id: SGR parameters}."""
    env = clean_subprocess_env(kanban_root=root)
    env.pop("NO_COLOR", None)
    env["TERM"] = "xterm-256color"
    env["KANBAN_ROOT"] = str(root)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(tmp_path / "scratch")
    result = run_dashboard_script(
        ["bash", str(root / "scripts" / "dashboard" / "column-render.sh"), *args, "--kanban-root", str(root)],
        env=env,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    rows = {}
    for line in result.stdout.splitlines():
        match = _ROW_RE.search(line)
        if match:
            rows[match.group(2)] = match.group(1)
    return rows


def _expected_rows(board_column: dict) -> dict:
    return {
        (f"{key} [active]" if status == "label" else key): _SGR_BY_BOARD_STATUS[status]
        for key, status in board_column.items()
    }


# ---------------------------------------------------------------------------
# The fixture itself carries what the comparison is about
# ---------------------------------------------------------------------------


def test_board_fixture_carries_wont_do_on_both_column_kinds(board_root: pathlib.Path) -> None:
    board = _board(board_root)
    assert "wont-do" in board["CODER"].values()
    assert board["BUGS"]["BUG-0002"] == "wont-do"
    assert set(board["REQUIREMENTS"].values()) == {"done", "label", "open"}
    assert board["CODER"][_display_id("CODER", _STALE_MARKER_TASK)] == "blocked"


# ---------------------------------------------------------------------------
# 1. Classification: the renderer's entry builders match /board exactly
# ---------------------------------------------------------------------------


def test_renderer_classifies_queue_items_as_board_does(board_root: pathlib.Path) -> None:
    board = _board(board_root)["CODER"]
    project = board_root / "projects" / PROJECT_NAME
    backlog = project / "tasks" / "queues" / "coder_backlog.md"
    single = {
        _display_id("CODER", item["task_id"]): item["status"]
        for item in queue_items(backlog, project / "tasks")
    }
    multi_entries = gather_project_entries(
        ProjectSource(PROJECT_NAME, "#378ADD", max_rows=50, backlog_file=str(backlog), tasks_dir=str(project / "tasks")),
        subcommand="queue", column_type="coder", use_color=False,
    )
    multi = {e["display_id"]: e["board_status"] for e in multi_entries}
    assert single == board
    assert multi == board


@pytest.mark.parametrize("column, dirname", [("BUGS", "bugs"), ("REQUIREMENTS", "requirements")])
def test_renderer_classifies_input_items_as_board_does(board_root: pathlib.Path, column: str, dirname: str) -> None:
    board = _board(board_root)[column]
    project = board_root / "projects" / PROJECT_NAME
    active_rc = get_active_rc_for_project(project)
    last_released = get_last_released_for_project(project)
    is_req = column == "REQUIREMENTS"
    single = {
        compact_id(path.stem, is_req): classify_input_file(
            path, is_requirements=is_req, active_rc=active_rc, last_released=last_released,
        )[0]
        for path in (project / dirname).glob("*.md")
    }
    multi_entries = gather_project_entries(
        ProjectSource(PROJECT_NAME, "#378ADD", max_rows=50, input_dir=str(project / dirname),
                      active_rc=active_rc, last_released=last_released),
        subcommand="input", column_type=dirname, use_color=False,
    )
    multi = {e["display_id"].replace(" [active]", ""): e["board_status"] for e in multi_entries}
    assert single == board
    assert multi == board


# ---------------------------------------------------------------------------
# 2. Rendering: column-render.sh draws /board's items in /board's colours
# ---------------------------------------------------------------------------


def test_single_project_queue_pane_matches_board(board_root: pathlib.Path, tmp_path: pathlib.Path) -> None:
    backlog = board_root / "projects" / PROJECT_NAME / "tasks" / "queues" / "coder_backlog.md"
    rows = _render(board_root, tmp_path, "queue", str(backlog), "20", "40")
    assert rows == _expected_rows(_board(board_root)["CODER"])


def test_multi_project_queue_pane_matches_board(board_root: pathlib.Path, tmp_path: pathlib.Path) -> None:
    rows = _render(board_root, tmp_path, "queue", "none", "20", "40", "--all-projects", "--column-type", "coder")
    assert rows == _expected_rows(_board(board_root)["CODER"])


@pytest.mark.parametrize("column, dirname", [("BUGS", "bugs"), ("REQUIREMENTS", "requirements")])
def test_single_project_input_pane_matches_board(
    board_root: pathlib.Path, tmp_path: pathlib.Path, column: str, dirname: str,
) -> None:
    input_dir = board_root / "projects" / PROJECT_NAME / dirname
    rows = _render(board_root, tmp_path, "input", str(input_dir), "none", "20", "40")
    assert rows == _expected_rows(_board(board_root)[column])


def test_retired_superseded_status_reads_closed_on_the_board_and_both_panes(
    board_root: pathlib.Path, tmp_path: pathlib.Path,
) -> None:
    """A bug whose existing ## Status is the retired "superseded" is wont-do on
    GET /board and is drawn in the closed (green) colour by column-render.sh
    in both the single-project and the --all-projects pane."""
    assert _board(board_root)["BUGS"]["BUG-0006"] == "wont-do"
    bugs_dir = board_root / "projects" / PROJECT_NAME / "bugs"
    single = _render(board_root, tmp_path, "input", str(bugs_dir), "none", "20", "40")
    multi = _render(board_root, tmp_path, "input", "none", "none", "20", "40",
                    "--all-projects", "--column-type", "bugs")
    assert single["BUG-0006"] == _SGR_BY_BOARD_STATUS["wont-do"]
    assert multi["BUG-0006"] == _SGR_BY_BOARD_STATUS["wont-do"]


@pytest.mark.parametrize("column, column_type", [("BUGS", "bugs"), ("REQUIREMENTS", "requirements")])
def test_multi_project_input_pane_matches_board(
    board_root: pathlib.Path, tmp_path: pathlib.Path, column: str, column_type: str,
) -> None:
    rows = _render(board_root, tmp_path, "input", "none", "none", "20", "40",
                   "--all-projects", "--column-type", column_type)
    assert rows == _expected_rows(_board(board_root)[column])
