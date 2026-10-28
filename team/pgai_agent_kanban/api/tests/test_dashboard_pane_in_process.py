"""
test_dashboard_pane_in_process.py — GET /dashboard/input and GET
/dashboard/queue render their board column in the server's own process.

Coverage:

  1. No process: with subprocess.run, subprocess.Popen (and every other
     process entry point) and the API's shell-out helpers (run_bounded,
     shell_out) made to record and refuse, both column panes answer 200,
     exit_code 0, with the column drawn -- nothing was started.
     On a board whose project sets a git dev_tree_path, the requirements
     column starts only git invocations (its last-release read, no script
     and no shell); the bugs, priorities and queue columns start nothing.
  2. Same text as the dashboard: on a two-project board that fills all
     eight columns (rows windowed away, the [dashboard] layout and row cap
     set in kanban.cfg), the route's stdout for every column type equals
     what ``column-render.sh <input|queue> none --kanban-root <root>
     --all-projects --column-type <column>`` prints for the same root,
     colour off -- and colour on, deferral glyph included.  The test runs
     the script for the comparison; the route does not.
  3. No fallback: when the in-process gather cannot serve the tree (no
     projects.cfg, a colon-format one, or any error while gathering) the
     route answers a non-zero exit_code, an empty stdout and a stderr
     ``dashboard_pane.gather_unavailable`` block naming the cause, and
     still starts no process.
  4. Nothing written: a column pane render leaves the kanban root and the
     temp root exactly as they were.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
from typing import Any, Callable

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[3]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.api import adapter, reads  # noqa: E402
from pgai_agent_kanban.dashboard.column_render import board_gather  # noqa: E402
from pgai_agent_kanban.dashboard.column_render.board_snapshot import BOARD_COLUMNS  # noqa: E402
from pgai_agent_kanban.lib.error_contract import parse_error_contract  # noqa: E402
from tests.fixtures.dashboard_render_fixture import run_dashboard_script  # noqa: E402
from tests.fixtures.golden_board import PROJECT_NAME, build_synthetic_board  # noqa: E402
from tests.fixtures.route_cli_parity import build_api_client, forbid_subprocess  # noqa: E402
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402

_PKG_DIR = _TEAM_DIR / "pgai_agent_kanban"

#: The second project the board registers, so the pane draws two project blocks.
_SECOND_PROJECT = "second-board"

#: The [dashboard] values the board's kanban.cfg sets, so the comparison
#: covers the settings the script resolves from it (layout, row cap, glyph).
_KANBAN_CFG = (
    "[dashboard]\n"
    "max_rows = 6\n"
    "min_rows_per_column = 4\n"
    "max_rows_per_project = 5\n"
    "dashboard_status_glyph = ◆\n"
)


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fill_project(project_dir: pathlib.Path, *, active_rc: str) -> None:
    """Give one project items in every board column, more than a block holds in some."""
    for number, status in enumerate(("open", "running", "blocked", "done", "wont-do", "open", "open"), start=2):
        _write(project_dir / "bugs" / f"BUG-00{number:02d}-pane-{status}.md",
               f"# BUG\n\n**Severity:** low\n\n## Status\n{status}\n")
    for number, status in ((1, "open"), (2, "running"), (3, "done")):
        _write(project_dir / "priority" / f"PRIORITY-00{number}-pane.md", f"# PRIORITY\n\n## Status\n{status}\n")
    _write(project_dir / "priority" / "README.md", "# Priorities\n")
    for version in ("v1.0.0", "v1.1.0", "v1.2.0", "v1.3.0", "v1.4.0", "v1.5.0", "v1.6.0"):
        _write(project_dir / "requirements" / f"{version}-pane.md",
               f"# {version}\n\n## Target Version\n{version}\n\n## Status\nopen\n")
    _write(project_dir / "release-state.md",
           f"# Release State\n\n## Active RC\n{active_rc}\n\n## RC Opened At\n2026-01-01T00:00:00Z\n\n"
           "## RC Opened By Task\npane-fixture\n\n## Last Released\nv1.1.0\n")
    states = ("BACKLOG", "WORKING", "BLOCKED", "DONE", "WONT-DO", "WAITING", "BACKLOG", "BACKLOG", "DONE")
    markers = {"BACKLOG": " ", "WAITING": "W", "WORKING": "A", "BLOCKED": "B", "DONE": "x", "WONT-DO": "x"}
    for agent in ("pm", "coder", "writer", "tester", "cm"):
        if agent == "coder" and (project_dir / "tasks" / "queues" / "coder_backlog.md").exists():
            continue  # the golden project's own one-task-per-state CODER queue
        lines = [f"# {agent.upper()} backlog", ""]
        for seq, state in enumerate(states, start=1):
            task_id = f"{agent.upper()}-20260201-{seq:03d}-pane-{state.lower()}"
            _write(project_dir / "tasks" / task_id / "status.md", f"## State\n{state}\n")
            lines.append(f"- [{markers[state]}] {task_id} — pane")
        _write(project_dir / "tasks" / "queues" / f"{agent}_backlog.md", "\n".join(lines) + "\n")


@pytest.fixture
def board_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """A two-project board with every board column populated, plus kanban.cfg [dashboard]."""
    root = tmp_path / "board"
    build_synthetic_board(root, with_queues=True)
    # column-render.sh reaches the package beside its own scripts/ tree.
    (root / "pgai_agent_kanban").symlink_to(_PKG_DIR, target_is_directory=True)
    with (root / "projects.cfg").open("a", encoding="utf-8") as fh:
        fh.write(f"\n[project:{_SECOND_PROJECT}]\npriority = 2\nenabled = true\ndashboard_color = #D85A30\n")
    _fill_project(root / "projects" / PROJECT_NAME, active_rc="v1.2.0")
    _fill_project(root / "projects" / _SECOND_PROJECT, active_rc="none")
    _write(root / "kanban.cfg", _KANBAN_CFG)
    return root


def _pane_env(root: pathlib.Path, tmp_path: pathlib.Path, *, colour: bool) -> dict:
    """The explicit environment both the route and the script render under."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    extra = {
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "route-temp"),
        "TERM": "xterm-256color" if colour else "dumb",
    }
    if not colour:
        extra["NO_COLOR"] = "1"
    (tmp_path / "route-temp").mkdir(exist_ok=True)
    return build_scrubbed_env(home_dir=home, extra_path_dirs=[pathlib.Path(sys.executable).parent], extra_env=extra)


def _use_env(monkeypatch: pytest.MonkeyPatch, env: dict) -> None:
    """Replace this process's environment -- the one the route renders under -- with *env*."""
    for key in list(os.environ):
        monkeypatch.delenv(key)
    for key, value in env.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def no_process(monkeypatch: pytest.MonkeyPatch):
    """Make every way of starting a process record the attempt and fail.

    subprocess.run and the API's shell-out helpers are patched where the
    route could reach them (the adapter, and reads' own binding of
    shell_out); forbid_subprocess covers subprocess.Popen and the os /
    asyncio entry points. Yields the attempts list -- the route catches
    errors while rendering, so the list, not an exception, is the verdict.
    """
    attempts: list[str] = []

    def _refuse(label: str) -> Callable[..., Any]:
        def _refused(*args: Any, **kwargs: Any) -> Any:
            attempts.append(f"{label}: {args!r}")
            raise AssertionError(f"{label} called: the column pane must start no process")
        return _refused

    monkeypatch.setattr(subprocess, "run", _refuse("subprocess.run"))
    monkeypatch.setattr(adapter, "run_bounded", _refuse("adapter.run_bounded"))
    monkeypatch.setattr(adapter, "shell_out", _refuse("adapter.shell_out"))
    monkeypatch.setattr(reads, "shell_out", _refuse("reads.shell_out"))
    with forbid_subprocess() as forbidden:
        yield attempts
        attempts.extend(forbidden)


# ---------------------------------------------------------------------------
# 1. No process
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pane, column_type", [("input", "bugs"), ("queue", "coder")])
def test_column_pane_renders_without_starting_a_process(
    board_root: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    no_process: list, pane: str, column_type: str,
) -> None:
    _use_env(monkeypatch, _pane_env(board_root, tmp_path, colour=False))
    with build_api_client(board_root) as client:
        resp = client.get(f"/dashboard/{pane}", params={"column_type": column_type})
    assert no_process == [], f"GET /dashboard/{pane} started a process: {no_process}"
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["exit_code"] == 0, body["stderr"]
    assert body["stderr"] == ""
    assert body["warnings"] == []
    assert f"[{PROJECT_NAME[:4]}] " in body["stdout"], body["stdout"]
    assert f"[{_SECOND_PROJECT[:4]}] " in body["stdout"], body["stdout"]


#: The release tag the git dev tree carries on main, above the v1.1.0 the
#: project's release-state.md records, so the git read is the one that answers.
_DEV_TREE_TAG = "v1.4.0"


@pytest.fixture
def git_board_root(board_root: pathlib.Path, tmp_path: pathlib.Path) -> pathlib.Path:
    """``board_root`` with the golden project's ``dev_tree_path`` set to a git repository.

    The repository has one commit on ``main`` tagged ``_DEV_TREE_TAG`` and no
    remote, so the last-release read runs its local tiers.
    """
    if shutil.which("git") is None:
        pytest.skip("git is not installed; the git dev_tree_path fixture needs it")
    dev_tree = tmp_path / "dev-tree"
    dev_tree.mkdir()
    git = ["git", "-C", str(dev_tree), "-c", "user.name=pane-fixture", "-c", "user.email=pane@fixture.invalid"]
    for args in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "fixture"],
                 ["tag", _DEV_TREE_TAG]):
        subprocess.run([*git, *args], check=True, capture_output=True, stdin=subprocess.DEVNULL)
    _write(board_root / "projects" / PROJECT_NAME / "project.cfg", f"[project]\ndev_tree_path = {dev_tree}\n")
    return board_root


@pytest.fixture
def git_only(monkeypatch: pytest.MonkeyPatch):
    """Let ``git`` run and record it; make every other way of starting a process fail.

    The API's shell-out helpers are refused as in ``no_process``.  Under
    ``forbid_subprocess``, ``subprocess.Popen`` (which ``subprocess.run``
    goes through) is replaced by a recorder that starts an argv-list
    ``git`` command without a shell and refuses anything else.  Yields
    ``(started, refused)``: the argv of each git process started, and a
    label for each refused attempt.
    """
    started: list[list[str]] = []
    refused: list[str] = []
    real_popen = subprocess.Popen

    def _refuse(label: str) -> Callable[..., Any]:
        def _refused(*args: Any, **kwargs: Any) -> Any:
            refused.append(f"{label}: {args!r}")
            raise AssertionError(f"{label} called: the requirements column runs no script")
        return _refused

    class _GitOnlyPopen(real_popen):  # type: ignore[misc,valid-type]
        def __init__(self, args: Any, *rest: Any, **kwargs: Any) -> None:
            argv = list(args) if isinstance(args, (list, tuple)) else [str(args)]
            if kwargs.get("shell") or not argv or os.path.basename(str(argv[0])) != "git":
                refused.append(f"subprocess.Popen: {argv!r} shell={bool(kwargs.get('shell'))}")
                raise AssertionError(f"non-git process started: {argv!r}")
            started.append([str(a) for a in argv])
            super().__init__(args, *rest, **kwargs)

    monkeypatch.setattr(adapter, "run_bounded", _refuse("adapter.run_bounded"))
    monkeypatch.setattr(adapter, "shell_out", _refuse("adapter.shell_out"))
    monkeypatch.setattr(reads, "shell_out", _refuse("reads.shell_out"))
    with forbid_subprocess() as forbidden:
        subprocess.Popen = _GitOnlyPopen  # forbid_subprocess restores the real Popen on exit
        yield started, refused
        refused.extend(forbidden)


def test_requirements_column_on_a_git_dev_tree_starts_only_git(
    git_board_root: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    git_only: tuple,
) -> None:
    """The requirements column reads the last release with git against the dev tree, and nothing else."""
    started, refused = git_only
    dev_tree = str(tmp_path / "dev-tree")
    _use_env(monkeypatch, _pane_env(git_board_root, tmp_path, colour=False))
    with build_api_client(git_board_root) as client:
        resp = client.get("/dashboard/input", params={"column_type": "requirements"})
    assert refused == [], f"GET /dashboard/input?column_type=requirements started a non-git process: {refused}"
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["exit_code"] == 0, body["stderr"]
    assert body["stderr"] == ""
    assert started, "the requirements column read no last release from the git dev tree"
    for argv in started:
        assert os.path.basename(argv[0]) == "git" and argv[1:3] == ["-C", dev_tree], argv
    assert ["git", "-C", dev_tree, "rev-parse", "--git-dir"] in started, started
    assert f"[{PROJECT_NAME[:4]}] " in body["stdout"], body["stdout"]
    # The fixture's dev tree is what answers: its tag, not release-state.md's v1.1.0.
    assert board_gather._last_released(git_board_root, PROJECT_NAME) == _DEV_TREE_TAG


@pytest.mark.parametrize(
    "pane, column_type",
    [pc for pc in BOARD_COLUMNS if pc != ("input", "requirements")],
    ids=[c for p, c in BOARD_COLUMNS if (p, c) != ("input", "requirements")],
)
def test_other_columns_on_a_git_dev_tree_start_no_process(
    git_board_root: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    no_process: list, pane: str, column_type: str,
) -> None:
    _use_env(monkeypatch, _pane_env(git_board_root, tmp_path, colour=False))
    with build_api_client(git_board_root) as client:
        resp = client.get(f"/dashboard/{pane}", params={"column_type": column_type})
    assert no_process == [], f"GET /dashboard/{pane}?column_type={column_type} started a process: {no_process}"
    assert resp.status_code == 200, resp.text
    assert resp.json()["exit_code"] == 0, resp.json()["stderr"]


def test_column_panes_name_no_script() -> None:
    """Only the flag-style panes have a script; the column panes are served in process."""
    assert set(reads._PANE_SCRIPTS) == {"metrics", "attention", "header", "status-window"}
    assert reads._VALID_PANES == reads._COLUMN_PANES | set(reads._PANE_SCRIPTS)
    assert not reads._COLUMN_PANES & set(reads._PANE_SCRIPTS)


# ---------------------------------------------------------------------------
# 2. Same text as the dashboard's column-render.sh
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("colour", [False, True], ids=["colour-off", "colour-on"])
@pytest.mark.parametrize("pane, column_type", BOARD_COLUMNS, ids=[c for _p, c in BOARD_COLUMNS])
def test_column_pane_stdout_equals_column_render_output(
    board_root: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    pane: str, column_type: str, colour: bool,
) -> None:
    env = _pane_env(board_root, tmp_path, colour=colour)
    cli_temp = tmp_path / "cli-temp"
    cli_temp.mkdir()
    cli = run_dashboard_script(
        ["bash", str(board_root / "scripts" / "dashboard" / "column-render.sh"),
         pane, "none", "--kanban-root", str(board_root), "--all-projects", "--column-type", column_type],
        env={**env, "PGAI_AGENT_KANBAN_TEMP_DIR": str(cli_temp)},
        timeout=60,
    )
    assert cli.returncode == 0, _diagnostic(cli)
    assert cli.stdout.strip() not in ("", "(empty)"), _diagnostic(cli)

    _use_env(monkeypatch, env)
    with build_api_client(board_root) as client:
        resp = client.get(f"/dashboard/{pane}", params={"column_type": column_type})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["exit_code"] == 0, body["stderr"]
    assert body["stdout"] == cli.stdout, f"route:\n{body['stdout']}\n{_diagnostic(cli)}"


def test_board_fixture_windows_rows_and_draws_the_deferral_glyph(
    board_root: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The comparison above covers windowed blocks and the colour-only glyph, not just plain rows."""
    _use_env(monkeypatch, _pane_env(board_root, tmp_path, colour=True))
    with build_api_client(board_root) as client:
        bugs = client.get("/dashboard/input", params={"column_type": "bugs"}).json()["stdout"]
        pm = client.get("/dashboard/queue", params={"column_type": "pm"}).json()["stdout"]
    assert "... and " in bugs, bugs  # a block holds fewer bugs than the row cap keeps
    assert " ✋" in bugs, bugs  # an open low bug under the golden project's deferral campaign
    assert "◆" in bugs, bugs  # kanban.cfg's dashboard_status_glyph
    # max_rows_per_project = 5: each project's nine PM tasks are windowed to five rows.
    assert len(pm.splitlines()) == 2 * 5, pm


# ---------------------------------------------------------------------------
# 3. No fallback when the in-process gather cannot serve the tree
# ---------------------------------------------------------------------------


def _assert_gather_unavailable(resp: Any, pane: str, cause: str) -> None:
    assert resp.status_code == 500, resp.text
    body = resp.json()
    assert body["exit_code"] != 0
    assert body["stdout"] == ""
    triple = parse_error_contract(body["stderr"])
    assert triple is not None, body["stderr"]
    assert triple["error"] == reads.DASHBOARD_GATHER_UNAVAILABLE_ERROR_KEY == "dashboard_pane.gather_unavailable"
    assert f"GET /dashboard/{pane}" in triple["message"]
    assert cause in triple["message"], triple["message"]
    assert triple["remedy"] and "<" not in triple["remedy"]


@pytest.mark.parametrize("registry", ["absent", "colon-format"])
@pytest.mark.parametrize("pane, column_type", [("input", "bugs"), ("queue", "coder")])
def test_tree_the_gather_does_not_read_is_reported_without_a_fallback(
    board_root: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    no_process: list, pane: str, column_type: str, registry: str,
) -> None:
    cfg = board_root / "projects.cfg"
    if registry == "absent":
        cfg.unlink()
    else:
        cfg.write_text(f"{PROJECT_NAME}:1\n", encoding="utf-8")
    _use_env(monkeypatch, _pane_env(board_root, tmp_path, colour=False))
    with build_api_client(board_root) as client:
        resp = client.get(f"/dashboard/{pane}", params={"column_type": column_type})
    assert no_process == []
    _assert_gather_unavailable(resp, pane, "no INI project registry")
    assert str(cfg) in resp.json()["stderr"]


@pytest.mark.parametrize("pane, column_type", [("input", "bugs"), ("queue", "coder")])
def test_error_while_gathering_is_reported_without_a_fallback(
    board_root: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    no_process: list, pane: str, column_type: str,
) -> None:
    def _failing_gather(*args: Any, **kwargs: Any) -> dict:
        raise OSError(f"cannot read {board_root / 'projects'}")

    monkeypatch.setattr(board_gather, "gather_board_sources", _failing_gather)
    _use_env(monkeypatch, _pane_env(board_root, tmp_path, colour=False))
    with build_api_client(board_root) as client:
        resp = client.get(f"/dashboard/{pane}", params={"column_type": column_type})
    assert no_process == []
    _assert_gather_unavailable(resp, pane, f"OSError: cannot read {board_root / 'projects'}")
    assert resp.json()["stderr"].rstrip().endswith(f"/dashboard/{pane}?column_type={column_type}'")


def test_gather_unavailable_is_a_distinct_failure_from_the_column_refusal() -> None:
    assert reads.DASHBOARD_GATHER_UNAVAILABLE_ERROR_KEY != reads.DASHBOARD_COLUMN_REQUIRED_ERROR_KEY
    assert adapter.status_for_error_key(reads.DASHBOARD_GATHER_UNAVAILABLE_ERROR_KEY) is None  # stays 500


# ---------------------------------------------------------------------------
# 4. Nothing written
# ---------------------------------------------------------------------------


def _tree_state(root: pathlib.Path) -> dict:
    """Every path under *root* (symlinks not followed) mapped to its size and mtime."""
    state = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = pathlib.Path(dirpath) / name
            stat = path.lstat()
            state[str(path.relative_to(root))] = (stat.st_size, stat.st_mtime_ns)
    return state


@pytest.mark.parametrize("pane, column_type", BOARD_COLUMNS, ids=[c for _p, c in BOARD_COLUMNS])
def test_column_pane_writes_nothing(
    board_root: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    pane: str, column_type: str,
) -> None:
    env = _pane_env(board_root, tmp_path, colour=False)
    temp_root = pathlib.Path(env["PGAI_AGENT_KANBAN_TEMP_DIR"])
    _use_env(monkeypatch, env)
    before = (_tree_state(board_root), _tree_state(temp_root))
    with build_api_client(board_root) as client:
        resp = client.get(f"/dashboard/{pane}", params={"column_type": column_type})
    assert resp.status_code == 200, resp.text
    assert (_tree_state(board_root), _tree_state(temp_root)) == before
    assert _tree_state(temp_root) == {}
