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
     status (done, wont-do and duplicate share green; needs-operator has the
     palette's status_needs_operator colour; a carried bug keeps the open
     colour and carries the carried marker; an active-RC requirement
     carries " [active]").

Same board, every column: GET /board reads its items through the
dashboard's column code (single.input_files, single.classify_input_file,
single.queue_items, cells.compact_id), and on the golden board widened to
all eight columns its compact IDs are exactly the IDs the mixed-project
pane draws for each column, colour off, with nothing windowed away.

Requirements on a git dev tree: with release tags on the dev tree's main
branch ahead of release-state.md's ## Last Released, GET /board classifies
each requirement against the same last release the mixed-project pane
reads (the tag), so each one is drawn in its /board status's colour.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.api.routers import board as board_router  # noqa: E402
from pgai_agent_kanban.board import (  # noqa: E402
    COLUMN_KINDS,
    COLUMN_ORDER,
    get_active_rc_for_project,
    get_last_released_for_project,
)
from pgai_agent_kanban.dashboard.column_render import ProjectSource, gather_project_entries  # noqa: E402
from pgai_agent_kanban.dashboard.column_render import cells, single  # noqa: E402
from pgai_agent_kanban.dashboard.column_render.cells import CARRIED_GLYPH_MARKER, compact_id  # noqa: E402
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
    # The golden board's needs-operator, duplicate and carried bugs:
    # needs-operator in the palette's status_needs_operator colour (magenta),
    # duplicate in the settled colour, carried in the open colour plus the
    # carried marker after its ID (cells.status_marker).
    "needs-operator": "0;35",
    "duplicate": "0;32",
    "carried": "0;37",
}

#: The [dashboard] layout the widened board renders under: room for every
#: bug it carries in one project block, so no multi-project pane windows a
#: row away (the default holds 8 rows per project).
_KANBAN_CFG = "[dashboard]\nmax_rows_per_project = 20\n"

_STALE_MARKER_TASK = "CODER-20260101-007-golden-stale-marker"

# One row: the SGR escape opening the item's text, then its display ID.
_ROW_RE = re.compile(r"\x1b\[([0-9;]*)m(BUG-\d+|v\d+\.\d+\.\d+(?: \[active\])?|\d{8}-\d{3})\x1b\[0m")


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _add_board_items(project_dir: pathlib.Path) -> None:
    """Widen the golden board: every bug status, a requirements column, a stale marker."""
    for number, status in ((2, "wont-do"), (3, "done"), (4, "running"), (5, "blocked"), (6, "superseded")):
        _write(
            project_dir / "bugs" / f"BUG-000{number}-parity-{status}.md",
            f"# BUG-000{number}\n\n**Severity:** high\n\n## Status\n{status}\n",
        )
    for version, status in (("v1.0.0", "done"), ("v1.2.0", "running"), ("v1.3.0", "running"), ("v1.4.0", "wont-do")):
        _write(
            project_dir / "requirements" / f"{version}-parity.md",
            f"# {version}\n\n## Target Version\n{version}\n\n## Status\n{status}\n",
        )
    _write(
        project_dir / "release-state.md",
        "# Release State\n\n## Active RC\nv1.2.0\n\n## RC Opened At\n2026-01-01T00:00:00Z\n\n"
        "## RC Opened By Task\nparity-fixture\n\n## Last Released\nv1.1.0\n",
    )
    _write(project_dir / "tasks" / _STALE_MARKER_TASK / "status.md", "## State\nBLOCKED\n")
    backlog = project_dir / "tasks" / "queues" / "coder_backlog.md"
    backlog.write_text(
        backlog.read_text(encoding="utf-8") + f"- [ ] {_STALE_MARKER_TASK} — stale marker\n", encoding="utf-8"
    )


@pytest.fixture
def board_root(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    root = tmp_path / "board"
    build_synthetic_board(root, with_queues=True)
    _add_board_items(root / "projects" / PROJECT_NAME)
    _write(root / "kanban.cfg", _KANBAN_CFG)
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
    """Return the ID a pane draws for a /board item key (a task ID as YYYYMMDD-NNN)."""
    if COLUMN_KINDS[column] == "queue":
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


def _render_lines(root: pathlib.Path, tmp_path: pathlib.Path, *args: str) -> list:
    """Run column-render.sh in colour; return the pane's lines."""
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
    return result.stdout.splitlines()


def _render(root: pathlib.Path, tmp_path: pathlib.Path, *args: str) -> dict:
    """Run column-render.sh in colour; return {display_id: SGR parameters}."""
    rows = {}
    for line in _render_lines(root, tmp_path, *args):
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
    single = {_display_id("CODER", item["task_id"]): item["status"] for item in queue_items(backlog, project / "tasks")}
    multi_entries = gather_project_entries(
        ProjectSource(
            PROJECT_NAME, "#378ADD", max_rows=50, backlog_file=str(backlog), tasks_dir=str(project / "tasks")
        ),
        subcommand="queue",
        column_type="coder",
        use_color=False,
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
            path,
            is_requirements=is_req,
            active_rc=active_rc,
            last_released=last_released,
        )[0]
        for path in (project / dirname).glob("*.md")
    }
    multi_entries = gather_project_entries(
        ProjectSource(
            PROJECT_NAME,
            "#378ADD",
            max_rows=50,
            input_dir=str(project / dirname),
            active_rc=active_rc,
            last_released=last_released,
        ),
        subcommand="input",
        column_type=dirname,
        use_color=False,
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
    board_root: pathlib.Path,
    tmp_path: pathlib.Path,
    column: str,
    dirname: str,
) -> None:
    input_dir = board_root / "projects" / PROJECT_NAME / dirname
    rows = _render(board_root, tmp_path, "input", str(input_dir), "none", "20", "40")
    assert rows == _expected_rows(_board(board_root)[column])


def test_retired_superseded_status_reads_closed_on_the_board_and_both_panes(
    board_root: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """A bug whose existing ## Status is the retired "superseded" is wont-do on
    GET /board and is drawn in the closed (green) colour by column-render.sh
    in both the single-project and the --all-projects pane."""
    assert _board(board_root)["BUGS"]["BUG-0006"] == "wont-do"
    bugs_dir = board_root / "projects" / PROJECT_NAME / "bugs"
    single = _render(board_root, tmp_path, "input", str(bugs_dir), "none", "20", "40")
    multi = _render(
        board_root, tmp_path, "input", "none", "none", "20", "40", "--all-projects", "--column-type", "bugs"
    )
    assert single["BUG-0006"] == _SGR_BY_BOARD_STATUS["wont-do"]
    assert multi["BUG-0006"] == _SGR_BY_BOARD_STATUS["wont-do"]


def test_carried_bug_carries_its_marker_in_both_panes(board_root: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """The bug GET /board reports as carried ends its row with the carried
    marker in the single-project and the --all-projects pane; no other bug does."""
    carried = [key for key, status in _board(board_root)["BUGS"].items() if status == "carried"]
    assert carried, "the golden board carries no carried bug, so the check would prove nothing"
    bugs_dir = board_root / "projects" / PROJECT_NAME / "bugs"
    for args in (("input", str(bugs_dir), "none"), ("input", "none", "none")):
        extra = ("--all-projects", "--column-type", "bugs") if args[1] == "none" else ()
        lines = _render_lines(board_root, tmp_path, *args, "20", "40", *extra)
        marked = sorted(
            m.group(2) for line in lines if line.endswith(CARRIED_GLYPH_MARKER) for m in [_ROW_RE.search(line)] if m
        )
        assert marked == sorted(carried), lines


@pytest.mark.parametrize("column, column_type", [("BUGS", "bugs"), ("REQUIREMENTS", "requirements")])
def test_multi_project_input_pane_matches_board(
    board_root: pathlib.Path,
    tmp_path: pathlib.Path,
    column: str,
    column_type: str,
) -> None:
    rows = _render(
        board_root, tmp_path, "input", "none", "none", "20", "40", "--all-projects", "--column-type", column_type
    )
    assert rows == _expected_rows(_board(board_root)[column])


# ---------------------------------------------------------------------------
# 3. Same board, every column: /board's IDs are the mixed-project pane's IDs
# ---------------------------------------------------------------------------

#: Queue tasks for the agent columns the golden board leaves empty:
#: (agent, task ID, state in status.md -- None leaves the marker to decide, marker).
_EXTRA_QUEUE_TASKS = (
    ("PM", "PM-20260102-001-golden-pm-done", "DONE", "x"),
    ("PM", "PM-20260102-002-golden-pm-marker", None, " "),
    ("WRITER", "WRITER-20260103-001-golden-writer-working", "WORKING", "A"),
    ("WRITER", "WRITER-20260103-002-golden-writer-wontdo", "WONT-DO", "x"),
    ("TESTER", "TESTER-20260104-001-golden-tester-blocked", "BLOCKED", "B"),
    ("TESTER", "TESTER-20260104-002-golden-tester-marker", None, "W"),
    ("CM", "CM-20260105-001-golden-cm-backlog", "BACKLOG", " "),
)

#: A row the pane draws when it windows items away; none may appear.
_ELISION_RE = re.compile(r"\.\.\. (and \d+ more|\(\d+ more (above|below)\))")
#: One colourless mixed-project row: "[<tag>] <display ID>[ [active]]".
_PLAIN_ROW_RE = re.compile(r"^\[[^\]]*\]\s+(\S+)")


def _fill_every_column(project_dir: pathlib.Path) -> None:
    """Give PRIORITIES and the PM, WRITER, TESTER and CM queues items too.

    The priority/ README and the non-markdown file are in no listing.
    """
    for number, status in ((1, "open"), (2, "running"), (3, "done")):
        _write(
            project_dir / "priority" / f"PRIORITY-00{number}-parity-{status}.md",
            f"# PRIORITY-00{number}\n\n## Status\n{status}\n",
        )
    _write(project_dir / "priority" / "README.md", "# Priorities\n")
    _write(project_dir / "priority" / "notes.txt", "not an intake file\n")
    lines_by_agent: dict = {}
    for agent, task_id, state, marker in _EXTRA_QUEUE_TASKS:
        if state is not None:
            _write(project_dir / "tasks" / task_id / "status.md", f"## State\n{state}\n")
        lines_by_agent.setdefault(agent, []).append(f"- [{marker}] {task_id} — parity")
    for agent, lines in lines_by_agent.items():
        _write(
            project_dir / "tasks" / "queues" / f"{agent.lower()}_backlog.md",
            f"# {agent} backlog\n\n" + "\n".join(lines) + "\n",
        )


def _board_ids(root: pathlib.Path) -> dict:
    """Return {column: sorted drawn IDs} for every column GET /board returns."""
    with build_api_client(root) as client:
        resp = client.get("/board")
    assert resp.status_code == 200
    columns = resp.json()["columns"]
    for column in columns:
        assert column["truncated"] == 0, column["name"]
    return {
        column["name"]: sorted(_display_id(column["name"], item["key"]) for item in column["items"])
        for column in columns
    }


def _multi_pane_ids(root: pathlib.Path, tmp_path: pathlib.Path, column: str) -> list:
    """Run column-render.sh --all-projects for *column*, colour off; return its sorted IDs."""
    env = clean_subprocess_env(kanban_root=root)  # NO_COLOR=1, TERM=dumb
    env["KANBAN_ROOT"] = str(root)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(tmp_path / "scratch")
    if COLUMN_KINDS[column] == "input":
        args = ["input", "none", "none"]
    else:
        args = ["queue", "none"]
    result = run_dashboard_script(
        [
            "bash",
            str(root / "scripts" / "dashboard" / "column-render.sh"),
            *args,
            "40",
            "60",
            "--all-projects",
            "--column-type",
            column.lower(),
            "--kanban-root",
            str(root),
        ],
        env=env,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert not [ln for ln in lines if _ELISION_RE.search(ln)], result.stdout
    assert all("\x1b[" not in ln for ln in lines), result.stdout
    return sorted(m.group(1) for m in map(_PLAIN_ROW_RE.match, lines) if m)


def test_board_reads_its_columns_through_the_dashboard_column_code() -> None:
    assert board_router.input_files is single.input_files
    assert board_router.classify_input_file is single.classify_input_file
    assert board_router.queue_items is single.queue_items
    assert board_router.compact_id is cells.compact_id


@pytest.mark.parametrize("column", COLUMN_ORDER)
def test_board_ids_equal_the_multi_project_pane_ids(
    board_root: pathlib.Path,
    tmp_path: pathlib.Path,
    column: str,
) -> None:
    _fill_every_column(board_root / "projects" / PROJECT_NAME)
    board = _board_ids(board_root)
    assert list(board) == COLUMN_ORDER
    assert board[column], f"the fixture leaves {column} empty, so the comparison would prove nothing"
    assert board[column] == _multi_pane_ids(board_root, tmp_path, column)


# ---------------------------------------------------------------------------
# 4. Requirements on a git dev tree: one last release for /board and the pane
# ---------------------------------------------------------------------------

#: The release tag the git dev tree carries on main, above release-state.md's
#: v1.1.0, so the tag read and the file read disagree.
_DEV_TREE_TAG = "v1.3.0"
#: Each requirement's status when classified against _DEV_TREE_TAG.
_GIT_TREE_REQUIREMENTS = {"v1.1.0": "done", "v1.2.0": "done", "v1.3.0": "done", "v1.4.0": "open"}
#: The golden board's own closed bundle, shipped below the tag.
_GOLDEN_BUNDLE_REQUIREMENTS = {"v0.1.0": "done"}


@pytest.fixture
def git_tree_root(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """The golden board with open requirements v1.1.0-v1.4.0 and a git dev tree tagged v1.3.0.

    release-state.md records Last Released v1.1.0 and no active RC; the
    project's ``dev_tree_path`` is a throwaway repository, one commit on
    ``main`` tagged ``_DEV_TREE_TAG``, no remote.
    """
    if shutil.which("git") is None:
        pytest.skip("git is not installed; the git dev_tree_path fixture needs it")
    root = tmp_path / "board"
    build_synthetic_board(root, with_queues=True)
    project = root / "projects" / PROJECT_NAME
    for version in _GIT_TREE_REQUIREMENTS:
        _write(
            project / "requirements" / f"{version}-git-tree.md",
            f"# {version}\n\n## Target Version\n{version}\n\n## Status\nopen\n",
        )
    _write(
        project / "release-state.md",
        "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n"
        "## RC Opened By Task\nnone\n\n## Last Released\nv1.1.0\n",
    )
    dev_tree = tmp_path / "dev-tree"
    dev_tree.mkdir()
    git = ["git", "-C", str(dev_tree), "-c", "user.name=parity-fixture", "-c", "user.email=parity@fixture.invalid"]
    for args in (
        ["init", "-q", "-b", "main"],
        ["commit", "-q", "--allow-empty", "-m", "fixture"],
        ["tag", _DEV_TREE_TAG],
    ):
        subprocess.run([*git, *args], check=True, capture_output=True, stdin=subprocess.DEVNULL)
    _write(project / "project.cfg", f"[project]\ndev_tree_path = {dev_tree}\n")
    (root / "pgai_agent_kanban").symlink_to(_PKG_DIR, target_is_directory=True)
    for key in AMBIENT_LEAK_ENV_VARS_TO_CLEAR:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
    monkeypatch.setenv("KANBAN_ROOT", str(root))
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(scratch))
    return root


def test_board_requirements_on_a_git_dev_tree_match_the_multi_project_pane(
    git_tree_root: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """Tags ahead of release-state.md: /board gives each requirement the colour the pane draws."""
    board = _board(git_tree_root)["REQUIREMENTS"]
    assert board == {**_GOLDEN_BUNDLE_REQUIREMENTS, **_GIT_TREE_REQUIREMENTS}
    rows = _render(
        git_tree_root, tmp_path, "input", "none", "none", "20", "40", "--all-projects", "--column-type", "requirements"
    )
    assert rows == _expected_rows(board)
