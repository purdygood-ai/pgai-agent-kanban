"""
test_dashboard_agents_column.py
===============================
The AGENTS board column: ``column-render.sh queue none <rows> <width>
--label AGENTS --column-type agents --all-projects`` lists every task of
each registered project's five role backlogs (pm, coder, writer, tester,
cm) in one column.  Each row is the project tag, then the task key with its
role prefix (``<ROLE>-<YYYYMMDD>-<NNN>``); rows are grouped by project in
registry order and sorted newest number first within a project, so the
roles interleave.  Colour, status marker and overflow row are the role
columns' own (``pgai_agent_kanban.dashboard.column_render.multi``).

Covered behaviour:

  * the column over a one-project and a two-project registry matches its
    checked-in text golden, one row per task of all five backlogs, and
    ``--label AGENTS`` alone selects the same column;
  * row order: registry order between projects whatever their numbers,
    numbers descending within a project, roles interleaved (a project's CM
    task with a higher number than its PM task comes first);
  * each row's status colour escape is exactly the one the task's role
    column draws it in (DONE, WORKING, BLOCKED, BACKLOG), and the role
    prefix is drawn in the palette's label colour;
  * more tasks than the row budget: the column keeps the budget, shows the
    role columns' "... and N more" overflow row, and keeps a WORKING task
    that is older than every row shown;
  * the column is identical served from the board fast path, rendered from
    the snapshot, and gathered directly through the bash stage;
  * ``column-render.sh --help`` names the agents column type.

Every root is built under pytest's tmp_path; column-render.sh runs with an
environment built from an explicit set (``build_scrubbed_env``) whose kanban
root and temp dir name the sandbox, and a throwaway HOME.  To re-record the
goldens after a deliberate change, run this file directly with ``--record``
from team/: ``python3 tests/unit/test_dashboard_agents_column.py --record``.
"""

from __future__ import annotations

import configparser
import pathlib
import re
import subprocess
import sys
import tempfile

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.dashboard.column_render.cells import FRAME_LABEL_PALETTE_KEY  # noqa: E402
from pgai_agent_kanban.lib.palette import resolve_semantic_color  # noqa: E402
from pgai_agent_kanban.lib.temp_root import resolve_temp_root  # noqa: E402
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402

_SCRIPTS = _TEAM_DIR / "scripts"
_COLUMN_RENDER = _SCRIPTS / "dashboard" / "column-render.sh"
_GOLDEN_DIR = _TEAM_DIR / "tests" / "fixtures" / "golden"
ONE_PROJECT_GOLDEN = _GOLDEN_DIR / "agents_column_one_project.txt"
TWO_PROJECT_GOLDEN = _GOLDEN_DIR / "agents_column_two_projects.txt"
ROW_ORDER_GOLDEN = _GOLDEN_DIR / "agents_column_row_order.txt"

#: The column's command line after the script, as the operator window runs it.
AGENTS_ARGS = ("queue", "none", "13", "30", "--label", "AGENTS", "--column-type", "agents", "--all-projects")

ROLES = ("pm", "coder", "writer", "tester", "cm")

#: A task: (project, role backlog, task ID, ## State).
#: alpha's tasks: every role, every state the board colours apart.
ALPHA_TASKS = (
    ("alpha", "pm", "PM-20260105-001-plan-release", "DONE"),
    ("alpha", "coder", "CODER-20260105-002-parser", "DONE"),
    ("alpha", "coder", "CODER-20260105-003-renderer", "WORKING"),
    ("alpha", "writer", "WRITER-20260105-004-guide", "BACKLOG"),
    ("alpha", "tester", "TESTER-20260105-005-verify", "WAITING"),
    ("alpha", "cm", "CM-20260105-006-ship", "WAITING"),
    ("alpha", "cm", "CM-20260104-009-open-rc", "DONE"),
    ("alpha", "coder", "CODER-20260104-010-retired", "WONT-DO"),
    ("alpha", "writer", "WRITER-20260104-011-notes", "BLOCKED"),
)

#: bravo's tasks: higher numbers than alpha's, registered second.
BRAVO_TASKS = (
    ("bravo", "pm", "PM-20260106-020-plan", "DONE"),
    ("bravo", "coder", "CODER-20260106-021-api", "WORKING"),
    ("bravo", "tester", "TESTER-20260106-022-verify", "BACKLOG"),
    ("bravo", "cm", "CM-20260106-023-ship", "WAITING"),
)

#: Row order: zeta is registered first (priority 1) with LOWER numbers than
#: acme (priority 2); tasks from four roles (no writer); zeta's CM task has
#: a higher number than its PM task.
ROW_ORDER_REGISTRY = (("zeta", 1), ("acme", 2))
ROW_ORDER_TASKS = (
    ("zeta", "pm", "PM-20260110-001-plan", "DONE"),
    ("zeta", "coder", "CODER-20260110-002-core", "DONE"),
    ("zeta", "tester", "TESTER-20260110-004-verify", "WORKING"),
    ("zeta", "coder", "CODER-20260110-003-cli", "DONE"),
    ("zeta", "cm", "CM-20260110-005-ship", "BACKLOG"),
    ("acme", "cm", "CM-20260120-040-ship", "BACKLOG"),
    ("acme", "pm", "PM-20260120-031-plan", "DONE"),
    ("acme", "coder", "CODER-20260120-033-feature", "WORKING"),
    ("acme", "tester", "TESTER-20260120-032-early", "DONE"),
)
#: The keys in the order the column must list them.
ROW_ORDER_EXPECTED = (
    ("zeta", "CM-20260110-005"),
    ("zeta", "TESTER-20260110-004"),
    ("zeta", "CODER-20260110-003"),
    ("zeta", "CODER-20260110-002"),
    ("zeta", "PM-20260110-001"),
    ("acme", "CM-20260120-040"),
    ("acme", "CODER-20260120-033"),
    ("acme", "TESTER-20260120-032"),
    ("acme", "PM-20260120-031"),
)

#: Status parity: one task per state, each in a different role's backlog.
PARITY_TASKS = (
    ("alpha", "pm", "PM-20260201-001-plan", "DONE"),
    ("alpha", "coder", "CODER-20260201-002-build", "WORKING"),
    ("alpha", "cm", "CM-20260201-003-ship", "BLOCKED"),
    ("alpha", "writer", "WRITER-20260201-004-docs", "BACKLOG"),
)

#: The overflow fixture's task count: more than one project's budget.
OVERFLOW_TASK_COUNT = 25

#: One row with colour on: the SGR escape opening the number, then the number.
_NUMBER_RE = re.compile(r"\x1b\[([0-9;]*)m(\d{8}-\d{3})\x1b\[0m")
_OVERFLOW_RE = re.compile(r"^\.\.\. and (\d+) more$")
_KEY_RE = re.compile(r"[A-Z]+-\d{8}-\d{3}")


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_root(base: pathlib.Path, registry: tuple, tasks: tuple) -> pathlib.Path:
    """A kanban root registering *registry* ((name, priority) pairs) with *tasks*."""
    root = base / "kanban"
    parser = configparser.RawConfigParser()
    for name, priority in registry:
        section = f"project:{name}"
        parser.add_section(section)
        parser.set(section, "priority", str(priority))
        parser.set(section, "enabled", "true")
        (root / "projects" / name / "tasks" / "queues").mkdir(parents=True, exist_ok=True)
    root.mkdir(parents=True, exist_ok=True)
    with (root / "projects.cfg").open("w", encoding="utf-8") as fh:
        parser.write(fh)
    for project, role, task_id, state in tasks:
        tasks_dir = root / "projects" / project / "tasks"
        _write(tasks_dir / task_id / "status.md", f"## State\n{state}\n")
        backlog = tasks_dir / "queues" / f"{role}_backlog.md"
        previous = backlog.read_text(encoding="utf-8") if backlog.is_file() else f"# {role.upper()} backlog\n\n"
        backlog.write_text(f"{previous}- [ ] {task_id} — fixture task\n", encoding="utf-8")
    return root


def _env(root: pathlib.Path, home: pathlib.Path, *, color: bool) -> dict:
    extra = {
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(root.parent / "scratch"),
        "TERM": "xterm-256color" if color else "dumb",
    }
    if not color:
        extra["NO_COLOR"] = "1"
    home.mkdir(parents=True, exist_ok=True)
    return build_scrubbed_env(home_dir=home, extra_path_dirs=[pathlib.Path(sys.executable).parent], extra_env=extra)


def run_column(root: pathlib.Path, home: pathlib.Path, *args: str, color: bool = False) -> subprocess.CompletedProcess:
    """column-render.sh *args* over *root*."""
    return subprocess.run(
        ["bash", str(_COLUMN_RENDER), *args, "--kanban-root", str(root)],
        capture_output=True,
        text=True,
        env=_env(root, home, color=color),
        cwd=str(root),
        timeout=120,
        check=False,
    )


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _stdout(result: subprocess.CompletedProcess) -> str:
    assert result.returncode == 0, _diagnostic(result)
    return result.stdout


def _rows(stdout: str) -> list:
    """The pane's rows after the label header."""
    lines = stdout.splitlines()
    assert lines[0] == "=== AGENTS ===", lines
    return lines[1:]


def _keys(rows: list) -> list:
    """(project tag, task key) per row, colour off."""
    out = []
    for row in rows:
        match = re.fullmatch(r"\[(\w+)\]\s+([A-Z]+-\d{8}-\d{3})", row)
        assert match, f"row {row!r} is not '<tag> <key>'"
        out.append((match.group(1), match.group(2)))
    return out


def _task_key(task_id: str) -> str:
    return _KEY_RE.match(task_id).group(0)


@pytest.fixture
def home(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "home"


# ---------------------------------------------------------------------------
# Text goldens
# ---------------------------------------------------------------------------


def test_one_project_column_matches_the_golden(tmp_path: pathlib.Path, home: pathlib.Path) -> None:
    root = build_root(tmp_path, (("alpha", 1),), ALPHA_TASKS)
    stdout = _stdout(run_column(root, home, *AGENTS_ARGS))
    assert stdout == ONE_PROJECT_GOLDEN.read_text(encoding="utf-8")
    keys = _keys(_rows(stdout))
    assert sorted(key for _tag, key in keys) == sorted(_task_key(t[2]) for t in ALPHA_TASKS)
    assert {key.split("-")[0].lower() for _tag, key in keys} == set(ROLES)


def test_two_project_column_matches_the_golden(tmp_path: pathlib.Path, home: pathlib.Path) -> None:
    root = build_root(tmp_path, (("alpha", 1), ("bravo", 2)), ALPHA_TASKS[:5] + BRAVO_TASKS)
    stdout = _stdout(run_column(root, home, *AGENTS_ARGS))
    assert stdout == TWO_PROJECT_GOLDEN.read_text(encoding="utf-8")
    keys = _keys(_rows(stdout))
    assert [tag for tag, _key in keys] == ["alph"] * 5 + ["brav"] * 4


def test_the_label_alone_selects_the_agents_column(tmp_path: pathlib.Path, home: pathlib.Path) -> None:
    root = build_root(tmp_path, (("alpha", 1), ("bravo", 2)), ALPHA_TASKS[:5] + BRAVO_TASKS)
    by_type = _stdout(run_column(root, home, *AGENTS_ARGS))
    by_label = _stdout(run_column(root, home, *[a for a in AGENTS_ARGS if a not in ("--column-type", "agents")]))
    assert by_label == by_type


def test_rows_group_by_registry_order_then_number_descending(tmp_path: pathlib.Path, home: pathlib.Path) -> None:
    root = build_root(tmp_path, ROW_ORDER_REGISTRY, ROW_ORDER_TASKS)
    stdout = _stdout(run_column(root, home, *AGENTS_ARGS))
    assert stdout == ROW_ORDER_GOLDEN.read_text(encoding="utf-8")
    keys = _keys(_rows(stdout))
    assert keys == [(project[:4], key) for project, key in ROW_ORDER_EXPECTED]
    zeta = [key for tag, key in keys if tag == "zeta"]
    assert zeta.index("CM-20260110-005") < zeta.index("PM-20260110-001")


def test_role_prefixes_are_right_aligned_outside_the_key(tmp_path: pathlib.Path, home: pathlib.Path) -> None:
    root = build_root(tmp_path, (("alpha", 1),), ALPHA_TASKS)
    rows = _rows(_stdout(run_column(root, home, *AGENTS_ARGS)))
    number_columns = {row.index(re.search(r"\d{8}-\d{3}", row).group(0)) for row in rows}
    assert len(number_columns) == 1, rows
    for row in rows:
        assert re.search(r"\s[A-Z]+-\d{8}-\d{3}$", row), f"key not unbroken at the row's end: {row!r}"


# ---------------------------------------------------------------------------
# Status colour parity with the role columns
# ---------------------------------------------------------------------------


def _escapes_by_number(stdout: str) -> dict:
    return {number: sgr for sgr, number in _NUMBER_RE.findall(stdout)}


def test_each_row_has_its_role_columns_status_colour(tmp_path: pathlib.Path, home: pathlib.Path) -> None:
    root = build_root(tmp_path, (("alpha", 1),), PARITY_TASKS)
    agents = _stdout(run_column(root, home, *AGENTS_ARGS, color=True))
    agents_escapes = _escapes_by_number(agents)

    for _project, role, task_id, state in PARITY_TASKS:
        role_column = _stdout(
            run_column(
                root,
                home,
                "queue",
                "none",
                "13",
                "30",
                "--label",
                role.upper(),
                "--all-projects",
                color=True,
            )
        )
        number = _task_key(task_id).split("-", 1)[1]
        role_escapes = _escapes_by_number(role_column)
        assert number in role_escapes, f"{role} column did not draw {number}:\n{role_column}"
        assert agents_escapes.get(number) == role_escapes[number], (
            f"{state} task {number}: AGENTS drew \\e[{agents_escapes.get(number)}m, "
            f"the {role.upper()} column \\e[{role_escapes[number]}m"
        )
    # Four states, four distinct colours: the comparison is not vacuous.
    assert len({agents_escapes[_task_key(t[2]).split("-", 1)[1]] for t in PARITY_TASKS}) == 4


def test_role_prefix_is_drawn_in_the_label_colour(tmp_path: pathlib.Path, home: pathlib.Path) -> None:
    root = build_root(tmp_path, (("alpha", 1),), PARITY_TASKS)
    agents = _stdout(run_column(root, home, *AGENTS_ARGS, color=True))
    label = resolve_semantic_color(FRAME_LABEL_PALETTE_KEY, True)
    for _project, _role, task_id, _state in PARITY_TASKS:
        prefix = _task_key(task_id).split("-", 1)[0] + "-"
        assert f"{label}{prefix}\x1b[0m" in agents, f"{prefix} not in the label colour:\n{agents!r}"


# ---------------------------------------------------------------------------
# Overflow
# ---------------------------------------------------------------------------


def _overflow_tasks() -> tuple:
    """OVERFLOW_TASK_COUNT tasks across the five roles; the oldest (a PM task) WORKING, the rest DONE."""
    roles = [ROLES[(n - 1) % len(ROLES)] for n in range(1, OVERFLOW_TASK_COUNT + 1)]
    return tuple(
        ("alpha", role, f"{role.upper()}-20260301-{n:03d}-task", "WORKING" if n == 1 else "DONE")
        for n, role in enumerate(roles, start=1)
    )


def test_overflow_keeps_the_budget_and_shows_the_role_columns_marker(
    tmp_path: pathlib.Path,
    home: pathlib.Path,
) -> None:
    tasks = _overflow_tasks()
    root = build_root(tmp_path, (("alpha", 1),), tasks)
    rows = _rows(_stdout(run_column(root, home, *AGENTS_ARGS)))

    # One project spends the whole column: the default layout's 13 rows.
    assert len(rows) == 13, rows
    overflow = _OVERFLOW_RE.match(rows[-1])
    assert overflow, rows[-1]
    shown = _keys(rows[:-1])
    assert int(overflow.group(1)) == OVERFLOW_TASK_COUNT - len(shown)
    # The WORKING task is the oldest, yet it is kept; every row stays newest first.
    assert ("alph", "PM-20260301-001") in shown
    numbers = [key.split("-", 1)[1] for _tag, key in shown]
    assert numbers == sorted(numbers, reverse=True)

    # The role columns' own overflow row: a single-project CODER column
    # holding more tasks than its project's max_rows.
    big = root / "projects" / "alpha" / "tasks" / "queues" / "big_backlog.md"
    big.write_text("".join(f"- [x] CODER-20260302-{n:03d}-task — done\n" for n in range(1, 31)), encoding="utf-8")
    role_rows = _stdout(run_column(root, home, "queue", str(big), "13", "30")).splitlines()
    role_marker = role_rows[-1]
    assert _OVERFLOW_RE.match(role_marker), role_rows
    assert re.sub(r"\d+", "N", rows[-1]) == re.sub(r"\d+", "N", role_marker)


# ---------------------------------------------------------------------------
# Served from the fast path, the snapshot, or a direct gather
# ---------------------------------------------------------------------------


def _direct_gather_script(root: pathlib.Path, rows: str, width: str) -> pathlib.Path:
    """A bash script running column-render.sh's direct gather and render stages for the column
    at *rows* rows and *width* columns."""
    lib = _SCRIPTS / "lib"
    script = root.parent / "direct-gather.sh"
    script.write_text(
        "set -euo pipefail\n"
        f"lib='{lib}'\n"
        'source "$lib/project_paths.sh"; source "$lib/projects.sh"; source "$lib/ini_parser.sh"\n'
        'source "$lib/cm/common.sh"; source "$lib/status_glyphs.sh"; source "$lib/script_common.sh"\n'
        f"source '{_SCRIPTS}/dashboard/lib/defer_scope.sh'\n"
        'source "$lib/dashboard/column_render_defer.sh"; source "$lib/dashboard/column_render_data_multi.sh"\n'
        'source "$lib/dashboard/column_render_data.sh"; source "$lib/dashboard/column_render_config.sh"\n'
        'source "$lib/dashboard/column_render_frame.sh"\n'
        f"POSARGS=(queue none {rows} {width}); SUBCOMMAND=queue; COLUMN_TYPE=agents; DASHBOARD_ROWS_PER_COLUMN={rows}\n"
        "column_render_config_color_mode; column_render_config_layout; column_render_config_status_glyph\n"
        "column_render_config_ansi\n"
        "echo '=== AGENTS ==='\n"
        "column_render_gather_data multi\n"
        "column_render_render_multi_project\n",
        encoding="utf-8",
    )
    return script


@pytest.mark.parametrize("color", [False, True])
def test_fast_path_snapshot_and_direct_gather_render_the_same_column(
    tmp_path: pathlib.Path,
    home: pathlib.Path,
    color: bool,
) -> None:
    root = build_root(tmp_path, (("alpha", 1), ("bravo", 2)), ALPHA_TASKS + BRAVO_TASKS)
    args = (*AGENTS_ARGS, "--board-widths", "queue=26,agents=30")
    built = _stdout(run_column(root, home, *args, color=color))  # builds the tick, pre-renders
    prerender = list((root.parent / "scratch").rglob("board-*.agents.w30.txt"))
    assert prerender, "the build did not pre-render the agents column"
    served = _stdout(run_column(root, home, *args, color=color))  # prints the pre-render
    # Width 31 was not declared: the full path renders it from the snapshot.
    from_snapshot = _stdout(
        run_column(root, home, "queue", "none", "13", "31", "--label", "AGENTS", "--all-projects", color=color)
    )
    direct = subprocess.run(
        ["bash", str(_direct_gather_script(root, "13", "31"))],
        capture_output=True,
        text=True,
        env=_env(root, home, color=color),
        cwd=str(root),
        timeout=120,
        check=False,
    )
    assert built == served
    assert from_snapshot == _stdout(direct)
    assert built == from_snapshot  # no row is long enough for width 30 and 31 to differ


def test_queue_width_alone_does_not_pre_render_the_agents_column(tmp_path: pathlib.Path, home: pathlib.Path) -> None:
    root = build_root(tmp_path, (("alpha", 1),), PARITY_TASKS)
    _stdout(
        run_column(
            root,
            home,
            "queue",
            "none",
            "13",
            "26",
            "--label",
            "CODER",
            "--all-projects",
            "--board-widths",
            "input=29,queue=26",
        )
    )
    scratch = root.parent / "scratch"
    assert list(scratch.rglob("board-*.coder.w26.txt"))
    assert not list(scratch.rglob("board-*.agents.*.txt"))


# ---------------------------------------------------------------------------
# --help
# ---------------------------------------------------------------------------


def test_help_names_the_agents_column_type(tmp_path: pathlib.Path, home: pathlib.Path) -> None:
    root = build_root(tmp_path, (("alpha", 1),), ())
    result = run_column(root, home, "--help")
    assert result.returncode == 0, _diagnostic(result)
    assert re.search(r'--column-type T\s.*"agents"', result.stdout), result.stdout
    assert "--label AGENTS" in result.stdout


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def record() -> None:
    """Rewrite the three goldens from the current scripts."""
    scratch_root = pathlib.Path(resolve_temp_root())
    scratch_root.mkdir(parents=True, exist_ok=True)
    cases = (
        (ONE_PROJECT_GOLDEN, (("alpha", 1),), ALPHA_TASKS),
        (TWO_PROJECT_GOLDEN, (("alpha", 1), ("bravo", 2)), ALPHA_TASKS[:5] + BRAVO_TASKS),
        (ROW_ORDER_GOLDEN, ROW_ORDER_REGISTRY, ROW_ORDER_TASKS),
    )
    for golden, registry, tasks in cases:
        with tempfile.TemporaryDirectory(prefix="agents-column-golden-", dir=scratch_root) as scratch:
            base = pathlib.Path(scratch)
            root = build_root(base, registry, tasks)
            golden.write_text(_stdout(run_column(root, base / "home", *AGENTS_ARGS)), encoding="utf-8")


if __name__ == "__main__":
    if sys.argv[1:] != ["--record"]:
        print("usage: python3 tests/unit/test_dashboard_agents_column.py --record", file=sys.stderr)
        sys.exit(2)
    record()
