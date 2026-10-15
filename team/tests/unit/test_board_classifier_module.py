"""
test_board_classifier_module.py — Unit tests for pgai_agent_kanban.board, the
one board status classifier behind GET /board and the dashboard's column
panes.

Coverage:

  1. The package: ``import pgai_agent_kanban.board`` exposes the classifier's
     public surface, and the classifier has exactly one home (no second copy
     under the API package).
  2. The importers: GET /board's router, GET /projects' router and the
     GET /dashboard/{pane} handler bind the classifier from
     pgai_agent_kanban.board, and the dashboard's column-render panes are
     exactly the board's column kinds.
  3. The WONT-DO decision: a WONT-DO task state and a ``wont-do`` intake file
     both classify as ``WONT_DO_STATUS`` ("wont-do"), never "done"; the rest
     of the status vocabulary is pinned beside it.
  4. Behaviour against the committed golden
     (tests/fixtures/golden/board_rest.json, recorded from the route before
     the classifier moved): GET /board on the golden board is unchanged, the
     WONT-DO task included. GET /dashboard/{input,queue} with a column
     selected returns exactly the rows column-render.sh prints for that
     column on the same board, and refuses 422 dual-channel, running no
     script, when the request names no column or one the pane does not
     render.
  5. The decision's premise: the tmux renderer draws a WONT-DO row in the
     same colour as a DONE row, so classifying WONT-DO as "wont-do" changes
     nothing an operator sees.
"""

from __future__ import annotations

import importlib
import json
import os
import pathlib
import re
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

import pgai_agent_kanban.board as board  # noqa: E402
from pgai_agent_kanban.board import classifier  # noqa: E402
from tests.fixtures.dashboard_render_fixture import (  # noqa: E402
    AMBIENT_LEAK_ENV_VARS_TO_CLEAR,
    clean_subprocess_env,
    run_dashboard_script,
)
from tests.fixtures.golden_board import (  # noqa: E402
    PROJECT_NAME,
    build_synthetic_board,
    task_ids_by_state,
)
from tests.fixtures.route_cli_parity import build_api_client  # noqa: E402
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402

_GOLDEN = _TEAM_DIR / "tests" / "fixtures" / "golden" / "board_rest.json"
_PKG_DIR = _TEAM_DIR / "pgai_agent_kanban"

# Every task state the golden board carries and the board status it classifies as.
_STATUS_BY_STATE = {
    "BACKLOG": "open",
    "WAITING": "open",
    "WORKING": "working",
    "BLOCKED": "blocked",
    "DONE": "done",
    "WONT-DO": "wont-do",
}


@pytest.fixture
def golden_root(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Build the golden board with its CODER queue and point the process env at it.

    The /dashboard/{pane} handler runs column-render.sh with this process's
    environment, so every root variable names the throwaway board and the
    ambient project-identity variables are cleared.
    """
    root = tmp_path / "board"
    build_synthetic_board(root, with_queues=True)
    for key in AMBIENT_LEAK_ENV_VARS_TO_CLEAR:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
    monkeypatch.setenv("KANBAN_ROOT", str(root))
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(scratch))
    return root


# ---------------------------------------------------------------------------
# 1. The package
# ---------------------------------------------------------------------------


def test_package_exposes_the_classifier_surface() -> None:
    assert sorted(board.__all__) == sorted(classifier.__all__)
    for name in board.__all__:
        assert getattr(board, name) is getattr(classifier, name), name


def test_classifier_has_one_home() -> None:
    assert not (_PKG_DIR / "api" / "board_classifier.py").exists()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("pgai_agent_kanban.api.board_classifier")


# ---------------------------------------------------------------------------
# 2. The importers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_name, names",
    [
        (
            "pgai_agent_kanban.api.routers.board",
            ["COLUMN_ORDER", "COLUMN_KINDS", "classify_input_item", "classify_queue_item",
             "get_active_rc_for_project", "get_last_released_for_project",
             "get_target_version_for_item", "marker_to_state", "parse_queue_line",
             "read_task_state_from_status_md"],
        ),
        (
            "pgai_agent_kanban.api.routers.projects",
            ["get_active_rc_for_project", "get_last_released_for_project",
             "marker_to_state", "parse_queue_line", "read_task_state_from_status_md"],
        ),
        ("pgai_agent_kanban.api.reads", ["COLUMN_KINDS"]),
    ],
)
def test_routes_bind_the_board_package(module_name: str, names: list[str]) -> None:
    module = importlib.import_module(module_name)
    for name in names:
        assert getattr(module, name) is getattr(board, name), f"{module_name}.{name}"


def test_dashboard_column_panes_are_the_board_column_kinds() -> None:
    from pgai_agent_kanban.api import reads

    assert reads._COLUMN_PANES == frozenset(board.COLUMN_KINDS.values()) == {"input", "queue"}
    assert reads._COLUMN_PANES <= reads._VALID_PANES


# ---------------------------------------------------------------------------
# 3. The WONT-DO decision
# ---------------------------------------------------------------------------


def test_wont_do_status_is_its_own_token() -> None:
    assert board.WONT_DO_STATUS == "wont-do"
    assert board.WONT_DO_STATUS != board.classify_queue_item("DONE")


@pytest.mark.parametrize("state, expected", sorted(_STATUS_BY_STATE.items()))
def test_classify_queue_item_vocabulary(state: str, expected: str) -> None:
    assert board.classify_queue_item(state) == expected
    assert board.classify_queue_item(state.lower()) == expected


@pytest.mark.parametrize(
    "file_status, expected",
    [
        ("open", "open"),
        ("running", "working"),
        ("done", "done"),
        ("wont-do", "wont-do"),
        ("WONT-DO", "wont-do"),
        ("blocked", "blocked"),
        ("unknown-word", "open"),
    ],
)
def test_classify_input_item_vocabulary(tmp_path: pathlib.Path, file_status: str, expected: str) -> None:
    bug = tmp_path / "BUG-0002-example.md"
    bug.write_text(f"# BUG-0002\n\n## Status\n{file_status}\n", encoding="utf-8")
    assert board.classify_input_item(bug, "BUGS", "", "") == expected


def test_golden_board_wont_do_task_classifies_as_wont_do(golden_root: pathlib.Path) -> None:
    tasks_dir = golden_root / "projects" / PROJECT_NAME / "tasks"
    wont_do_id = task_ids_by_state()["WONT-DO"]
    state = board.read_task_state_from_status_md(tasks_dir, wont_do_id)
    assert state == "WONT-DO"
    assert board.classify_queue_item(state) == board.WONT_DO_STATUS


def test_golden_board_queue_markers_derive_from_state(golden_root: pathlib.Path) -> None:
    """with_queues=True lists every task under its state's marker, so the
    marker fallback agrees with status.md for every state but the two that
    share the [x] marker (DONE and WONT-DO both read back as DONE)."""
    backlog = golden_root / "projects" / PROJECT_NAME / "tasks" / "queues" / "coder_backlog.md"
    parsed = [board.parse_queue_line(line) for line in backlog.read_text(encoding="utf-8").splitlines()]
    by_id = {p[1]: board.marker_to_state(p[0]) for p in parsed if p}
    expected = {tid: ("DONE" if state == "WONT-DO" else state) for state, tid in task_ids_by_state().items()}
    assert by_id == expected


# ---------------------------------------------------------------------------
# 4. Behaviour against the committed golden
# ---------------------------------------------------------------------------


def _display_id(task_id: str) -> str:
    """The ``YYYYMMDD-NNN`` id column-render.sh draws for *task_id*."""
    date, seq = re.match(r"^[A-Z]+-(\d{8})-(\d+)-", task_id).groups()
    return f"{date}-{int(seq):03d}"



def test_board_response_matches_golden(golden_root: pathlib.Path) -> None:
    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    with build_api_client(golden_root) as client:
        resp = client.get("/board")
    assert resp.status_code == 200
    body = resp.json()
    assert body.pop("generated_at")
    assert body == golden["board"]


def test_board_coder_column_carries_one_item_per_state(golden_root: pathlib.Path) -> None:
    with build_api_client(golden_root) as client:
        body = client.get("/board").json()
    coder = next(c for c in body["columns"] if c["name"] == "CODER")
    got = {item["key"]: item["status"] for item in coder["items"]}
    want = {tid: _STATUS_BY_STATE[state] for state, tid in task_ids_by_state().items()}
    assert got == want


@pytest.fixture
def colour_render_env(
    golden_root: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, str]:
    """Render the golden board in colour, under one explicit environment.

    The /dashboard/{pane} handler runs column-render.sh with this process's
    environment, so the process environment is replaced by an explicit,
    minimal one for the test -- the same names the CLI run each test
    compares against is given, so the comparison depends on the tree alone.
    column-render.sh's frame stage imports the package from the kanban root,
    so the board gets the package symlinked in. Colour stays on so each
    row's SGR code -- the colour of its board status -- is part of what the
    parity tests compare. The route renders into its own snapshot directory,
    so the CLI run renders afresh rather than reading the route's cached
    text.

    Returns the environment the route renders under.
    """
    (golden_root / "pgai_agent_kanban").symlink_to(_PKG_DIR, target_is_directory=True)
    home = tmp_path / "home"
    home.mkdir()
    route_scratch = tmp_path / "route-scratch"
    route_scratch.mkdir()
    env = build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(golden_root),
            "KANBAN_ROOT": str(golden_root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(route_scratch),
            "TERM": "xterm-256color",
        },
    )
    for key in list(os.environ):
        monkeypatch.delenv(key)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return env


def _column_render_cli(env: dict[str, str], scratch: pathlib.Path, pane: str, column: str):
    """Run ``column-render.sh <pane> none --all-projects --column-type <column>``.

    Runs on the board *env* names, under *env* with a separate snapshot
    directory at *scratch*.
    """
    scratch.mkdir()
    root = pathlib.Path(env["PGAI_AGENT_KANBAN_ROOT_PATH"])
    return run_dashboard_script(
        ["bash", str(root / "scripts" / "dashboard" / "column-render.sh"),
         pane, "none", "--kanban-root", str(root), "--all-projects", "--column-type", column],
        env={**env, "PGAI_AGENT_KANBAN_TEMP_DIR": str(scratch)},
        timeout=60,
    )


@pytest.mark.parametrize(
    "pane, column_type, cli_column",
    [
        ("queue", "coder", "coder"),
        ("queue", "CODER", "coder"),
        ("input", "bugs", "bugs"),
    ],
)
def test_dashboard_column_pane_matches_renderer(
    colour_render_env: dict[str, str],
    tmp_path: pathlib.Path,
    pane: str,
    column_type: str,
    cli_column: str,
) -> None:
    root = pathlib.Path(colour_render_env["PGAI_AGENT_KANBAN_ROOT_PATH"])
    with build_api_client(root) as client:
        resp = client.get(f"/dashboard/{pane}", params={"column_type": column_type})
    cli = _column_render_cli(colour_render_env, tmp_path / "cli-scratch", pane, cli_column)
    assert cli.returncode == 0, cli.stderr

    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert body["exit_code"] == 0, body["stderr"]
    assert body["warnings"] == []
    assert body["stdout"] == cli.stdout
    assert body["stdout"].strip() not in ("", "(empty)")


def test_dashboard_queue_pane_draws_every_coder_task_in_its_board_colour(
    colour_render_env: dict[str, str],
) -> None:
    """The CODER column carries one task per state, WONT-DO included, each
    in the colour of the status GET /board reports for it."""
    root = pathlib.Path(colour_render_env["PGAI_AGENT_KANBAN_ROOT_PATH"])
    with build_api_client(root) as client:
        board_body = client.get("/board").json()
        pane_body = client.get("/dashboard/queue", params={"column_type": "coder"}).json()
    coder = next(c for c in board_body["columns"] if c["name"] == "CODER")
    board_status = {item["key"]: item["status"] for item in coder["items"]}

    colour_by_display_id: dict[str, str] = {}
    for line in pane_body["stdout"].splitlines():
        codes = _ANSI_SGR_RE.findall(line)
        match = re.search(r"(\d{8}-\d{3})\s*$", _ANSI_SGR_RE.sub("", line))
        if match and len(codes) >= 3:
            # Codes: project tag colour, reset, entry colour, reset.
            colour_by_display_id[match.group(1)] = codes[2]

    ids = task_ids_by_state()
    assert set(colour_by_display_id) == {_display_id(tid) for tid in ids.values()}
    assert board_status[ids["WONT-DO"]] == board.WONT_DO_STATUS
    colour_by_status: dict[str, set[str]] = {}
    for tid, status in board_status.items():
        colour_by_status.setdefault(status, set()).add(colour_by_display_id[_display_id(tid)])
    # One colour per board status; done and wont-do share the settled green.
    assert all(len(colours) == 1 for colours in colour_by_status.values()), colour_by_status
    assert colour_by_status["wont-do"] == colour_by_status["done"]
    assert colour_by_status["done"] != colour_by_status["open"]


@pytest.mark.parametrize(
    "pane, params",
    [
        ("queue", {}),
        ("queue", {"column_type": ""}),
        ("queue", {"column_type": "nosuchcolumn"}),
        ("queue", {"column_type": "bugs"}),
        ("input", {}),
        ("input", {"column_type": "coder"}),
    ],
)
def test_dashboard_column_pane_refuses_without_a_column(
    golden_root: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    pane: str,
    params: dict,
) -> None:
    from pgai_agent_kanban.api import adapter, reads

    def _no_script(*args, **kwargs):
        raise AssertionError(f"a refused /dashboard/{pane} request ran a script: {args!r}")

    monkeypatch.setattr(adapter.subprocess, "Popen", _no_script)
    with build_api_client(golden_root) as client:
        resp = client.get(f"/dashboard/{pane}", params=params)

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == reads.DASHBOARD_COLUMN_REQUIRED_ERROR_KEY == "dashboard_pane.column_required"
    accepted = reads._PANE_COLUMN_TYPES[pane]
    for column in accepted:
        assert column in body["message"]
    assert body["remedy"].startswith("curl ")
    assert f"/dashboard/{pane}?column_type={accepted[0]}" in body["remedy"]
    assert "<" not in body["remedy"]
    assert body["warnings"] == []
    assert "stdout" not in body


def test_pane_column_types_partition_the_board_columns() -> None:
    from pgai_agent_kanban.api import reads

    assert reads._PANE_COLUMN_TYPES == {
        "input": ("bugs", "priorities", "requirements"),
        "queue": ("pm", "coder", "writer", "tester", "cm"),
    }
    assert sorted(c for cols in reads._PANE_COLUMN_TYPES.values() for c in cols) == sorted(
        name.lower() for name in board.COLUMN_ORDER
    )


# ---------------------------------------------------------------------------
# 5. The decision's premise: the renderer draws WONT-DO and DONE alike
# ---------------------------------------------------------------------------

_ANSI_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")


def test_renderer_draws_wont_do_and_done_rows_alike(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "board"
    build_synthetic_board(root, with_queues=True)
    # column-render.sh's heredocs import the package from the kanban root.
    (root / "pgai_agent_kanban").symlink_to(_PKG_DIR, target_is_directory=True)
    env = clean_subprocess_env(kanban_root=root)
    env.pop("NO_COLOR", None)
    env["TERM"] = "xterm-256color"
    env["KANBAN_ROOT"] = str(root)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(tmp_path / "scratch")
    (tmp_path / "scratch").mkdir()

    result = run_dashboard_script(
        ["bash", str(root / "scripts" / "dashboard" / "column-render.sh"),
         "queue", "none", "10", "60", "--label", "CODER",
         "--kanban-root", str(root), "--all-projects", "--column-type", "coder"],
        env=env,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr

    ids = task_ids_by_state()
    colour_by_display_id: dict[str, str] = {}
    for line in result.stdout.splitlines():
        codes = _ANSI_SGR_RE.findall(line)
        plain = _ANSI_SGR_RE.sub("", line)
        match = re.search(r"(\d{8}-\d{3})\s*$", plain)
        if match and len(codes) >= 3:
            # Codes: project tag colour, reset, entry colour, reset.
            colour_by_display_id[match.group(1)] = codes[2]

    def display_id(task_id: str) -> str:
        date, seq = re.match(r"^[A-Z]+-(\d{8})-(\d+)-", task_id).groups()
        return f"{date}-{int(seq):03d}"

    done_colour = colour_by_display_id[display_id(ids["DONE"])]
    wont_do_colour = colour_by_display_id[display_id(ids["WONT-DO"])]
    open_colour = colour_by_display_id[display_id(ids["BACKLOG"])]
    assert wont_do_colour == done_colour
    assert done_colour != open_colour
