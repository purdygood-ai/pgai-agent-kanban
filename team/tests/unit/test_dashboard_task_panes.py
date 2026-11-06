"""
test_dashboard_task_panes.py
============================
The task-folder scans behind three dashboard panes,
``pgai_agent_kanban.dashboard.task_panes``:

  * ``status_window_blocked_lines`` — show-status-window.sh's ATTENTION
    list: BLOCKED task IDs with a one-line reason cut to 60 characters;
  * ``attention_blocked_lines`` — show-attention.sh's blocked-task blocks:
    blocked-since time, up to three reason and next-step lines;
  * ``document_pipeline_lines`` — show-document-drill.sh's per-role
    writer/tester/cm counts.

Each renderer is checked against one fixture tasks directory with colour
off (exact lines), once with colour on, and with an empty and a missing
tasks directory; each renderer also with an empty tasks path from a cwd
holding a task folder it would count (a BLOCKED one for the blocked-task
scans, a WRITER one for the document pipeline), which it must not read.
The command line is checked to print the same lines the
function yields, and the two panes that call it directly
(show-attention.sh, show-document-drill.sh) are run as bash scripts on a
throwaway kanban root to show the module's lines reach the pane verbatim.

Every tree is built under pytest's tmp_path. Subprocesses run with an
environment built from an explicit set and a throwaway HOME.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
from datetime import datetime

import pytest

from pgai_agent_kanban.dashboard.task_panes import (
    attention_blocked_lines,
    document_pipeline_lines,
    status_window_blocked_lines,
)
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_MODULE = "pgai_agent_kanban.dashboard.task_panes"
_PP_RUN_OPS = _TEAM_DIR / "scripts" / "lib" / "pp_run_ops.sh"
_DASHBOARD = _TEAM_DIR / "scripts" / "dashboard"

RESET, RED, BOLD, DIM = "\033[0m", "\033[0;31m", "\033[1m", "\033[2m"
CYAN, YELLOW, GREEN = "\033[0;36m", "\033[0;33m", "\033[0;32m"

_LONG_REASON = "A reason long enough that the status window has to cut it short here"
_NOW = 1_800_000_000


def _task(
    tasks: pathlib.Path, name: str, status_body: str, role: str | None = None, age: int | None = None
) -> pathlib.Path:
    """Write tasks/<name>/status.md (and README.md when *role* is given)."""
    folder = tasks / name
    folder.mkdir(parents=True)
    status = folder / "status.md"
    status.write_text(f"# Status\n\n{status_body}", encoding="utf-8")
    if role is not None:
        (folder / "README.md").write_text(f"# Task\n\n## Role\n{role}\n", encoding="utf-8")
    if age is not None:
        os.utime(status, (_NOW - age, _NOW - age))
    return status


@pytest.fixture
def blocked_tasks(tmp_path: pathlib.Path) -> pathlib.Path:
    """A tasks directory with three BLOCKED tasks among others and non-task folders."""
    tasks = tmp_path / "tasks"
    _task(
        tasks,
        "CODER-001-long",
        "## State\nBLOCKED\n\n"
        f"## Blocked Reason\n{_LONG_REASON}\nline two\nline three\nline four\n\n"
        "## Next Recommended Step\nFix the thing\nrerun\n",
        age=7500,
    )
    _task(tasks, "CODER-002-blockers", "## State\nblocked\n\n## Blockers\nshort blocker\n", age=42)
    _task(tasks, "CODER-003-working", "## State\nWORKING\n", age=10)
    _task(tasks, "WRITER-004-bare", "## State\nBLOCKED\n", age=600)
    _task(tasks, "archive", "## State\nBLOCKED\n")
    _task(tasks, "queues", "## State\nBLOCKED\n")
    (tasks / "plans").mkdir()
    (tasks / "NO-STATUS").mkdir()
    (tasks / "loose-file.md").write_text("## State\nBLOCKED\n", encoding="utf-8")
    return tasks


def _since(status: pathlib.Path) -> str:
    return datetime.fromtimestamp(int(status.stat().st_mtime)).strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# status_window_blocked_lines
# ---------------------------------------------------------------------------


def test_status_window_lists_each_blocked_task_with_its_cut_reason(blocked_tasks: pathlib.Path) -> None:
    assert list(status_window_blocked_lines(str(blocked_tasks))) == [
        "  CODER-001-long",
        f"  Reason: {_LONG_REASON[:57]}...",
        "",
        "  CODER-002-blockers",
        "  Reason: short blocker",
        "",
        "  WRITER-004-bare",
        "  Reason: (see status.md)",
        "",
    ]


def test_status_window_wraps_id_and_reason_in_the_given_escapes(blocked_tasks: pathlib.Path) -> None:
    lines = list(status_window_blocked_lines(str(blocked_tasks), RESET, RED, DIM, BOLD))
    assert lines[:3] == [
        f"  {RED}{BOLD}CODER-001-long{RESET}",
        f"  {DIM}Reason: {_LONG_REASON[:57]}...{RESET}",
        "",
    ]


@pytest.mark.parametrize("which", ["empty", "missing"])
def test_status_window_shows_none_without_blocked_tasks(tmp_path: pathlib.Path, which: str) -> None:
    tasks = tmp_path / "tasks"
    if which == "empty":
        tasks.mkdir()
    assert list(status_window_blocked_lines(str(tasks), RESET, RED, DIM, BOLD)) == [f"  {DIM}(none){RESET}"]


# ---------------------------------------------------------------------------
# attention_blocked_lines
# ---------------------------------------------------------------------------


def test_attention_renders_since_reason_and_next_step_blocks(blocked_tasks: pathlib.Path) -> None:
    long_since = _since(blocked_tasks / "CODER-001-long" / "status.md")
    short_since = _since(blocked_tasks / "CODER-002-blockers" / "status.md")
    bare_since = _since(blocked_tasks / "WRITER-004-bare" / "status.md")
    assert list(attention_blocked_lines(str(blocked_tasks), False, now=_NOW)) == [
        "> CODER-001-long",
        f"  Blocked since: {long_since} (2h 5m ago)",
        "  Reason:",
        f"    {_LONG_REASON}",
        "    line two",
        "    line three",
        "  Recommended next step:",
        "    Fix the thing",
        "    rerun",
        "",
        "> CODER-002-blockers",
        f"  Blocked since: {short_since} (42s)",
        "  Reason:",
        "    short blocker",
        "",
        "> WRITER-004-bare",
        f"  Blocked since: {bare_since} (10m ago)",
        "",
    ]


def test_attention_in_colour_uses_the_arrow_glyph_and_escapes(blocked_tasks: pathlib.Path) -> None:
    lines = list(attention_blocked_lines(str(blocked_tasks), True, RESET, RED, BOLD, DIM, CYAN, YELLOW, now=_NOW))
    since = _since(blocked_tasks / "CODER-001-long" / "status.md")
    assert lines[:3] == [
        f"▶ {RED}{BOLD}CODER-001-long{RESET}",
        f"  Blocked since: {DIM}{since} (2h 5m ago){RESET}",
        f"  {YELLOW}Reason:{RESET}",
    ]
    assert f"  {CYAN}Recommended next step:{RESET}" in lines


@pytest.mark.parametrize("which", ["empty", "missing"])
def test_attention_shows_the_running_normally_line_without_blocked_tasks(
    tmp_path: pathlib.Path,
    which: str,
) -> None:
    tasks = tmp_path / "tasks"
    if which == "empty":
        tasks.mkdir()
    assert list(attention_blocked_lines(str(tasks), True, RESET, dim=DIM)) == [
        f"  {DIM}(no blocked tasks — system running normally){RESET}",
        "",
    ]


def test_blocked_scans_given_an_empty_path_render_the_empty_state_not_the_cwd(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cwd = tmp_path / "caller-cwd"
    _task(cwd, "CODER-20260101-001-not-a-real-task", "## State\nBLOCKED\n\n## Blockers\ncwd folder\n")
    monkeypatch.chdir(cwd)

    attention = list(attention_blocked_lines("", False, now=_NOW))
    window = list(status_window_blocked_lines(""))

    assert attention == ["  (no blocked tasks — system running normally)", ""]
    assert window == ["  (none)"]
    assert not any("not-a-real-task" in line for line in attention + window)


# ---------------------------------------------------------------------------
# document_pipeline_lines
# ---------------------------------------------------------------------------


@pytest.fixture
def pipeline_tasks(tmp_path: pathlib.Path) -> pathlib.Path:
    """Writer 1 done / 1 working / 1 blocked, tester 1 waiting, cm 1 done / 1 backlog."""
    tasks = tmp_path / "tasks"
    _task(tasks, "WRITER-001", "## State\nDONE\n", role="WRITER")
    _task(tasks, "WRITER-002", "## State\nWORKING\n", role="writer | doc")
    _task(tasks, "WRITER-003", "## State\nBLOCKED\n", role="Writer")
    _task(tasks, "TESTER-004", "## State\nWAITING\n", role="TESTER")
    _task(tasks, "CM-005", "## State\nWONT-DO\n", role="CM")
    _task(tasks, "CM-006", "## State\nsomething else\n", role="CM")
    _task(tasks, "CODER-007", "## State\nDONE\n", role="CODER")
    _task(tasks, "TASK-legacy", "## State\nDONE\n", role="WRITER")
    _task(tasks, "WRITER-008-no-readme", "## State\nDONE\n")
    _task(tasks, "archive", "## State\nDONE\n", role="WRITER")
    return tasks


def test_document_pipeline_counts_each_role(pipeline_tasks: pathlib.Path) -> None:
    assert list(document_pipeline_lines(str(pipeline_tasks), False, CYAN, GREEN)) == [
        "writer: 1/3  1W 1B               33%",
        "tester: 0/1  1Wa                  0%",
        "cm:     1/2  1Wa                 50%",
    ]


def test_document_pipeline_in_colour_colours_each_code(pipeline_tasks: pathlib.Path) -> None:
    lines = list(document_pipeline_lines(str(pipeline_tasks), True, CYAN, GREEN, YELLOW, RED, DIM, RESET))
    assert lines[0] == (f"{CYAN}writer: {RESET}{GREEN}1/3{RESET}  {YELLOW}1W{RESET} {RED}1B{RESET}" f"{' ' * 15}33%")
    assert lines[1] == f"{CYAN}tester: {RESET}{GREEN}0/1{RESET}  {DIM}1Wa{RESET}{' ' * 18}0%"


@pytest.mark.parametrize("which", ["empty", "missing"])
def test_document_pipeline_shows_zero_of_zero_without_tasks(tmp_path: pathlib.Path, which: str) -> None:
    tasks = tmp_path / "tasks"
    if which == "empty":
        tasks.mkdir()
    assert list(document_pipeline_lines(str(tasks), True, CYAN, dim=DIM, reset=RESET)) == [
        f"{CYAN}writer: {RESET}{DIM}0/0{RESET}",
        f"{CYAN}tester: {RESET}{DIM}0/0{RESET}",
        f"{CYAN}cm:     {RESET}{DIM}0/0{RESET}",
    ]


@pytest.mark.parametrize("tasks_path", ["", "   "], ids=["empty", "blank"])
def test_document_pipeline_given_an_empty_path_counts_nothing_from_the_cwd(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    tasks_path: str,
) -> None:
    cwd = tmp_path / "caller-cwd"
    _task(cwd, "WRITER-20260101-002-doc-task", "## State\nWORKING\n", role="WRITER")
    monkeypatch.chdir(cwd)

    assert list(document_pipeline_lines(tasks_path, False)) == [
        "writer: 0/0",
        "tester: 0/0",
        "cm:     0/0",
    ]


# ---------------------------------------------------------------------------
# Command line, run the way the panes run it
# ---------------------------------------------------------------------------


def _pp_run_ops(tmp_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    env = build_scrubbed_env(home_dir=tmp_path / "home", extra_path_dirs=[pathlib.Path(sys.executable).parent])
    return subprocess.run(
        ["bash", str(_PP_RUN_OPS), _MODULE, *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=60,
        check=False,
    )


def test_cli_verbs_print_the_lines_the_functions_yield(
    tmp_path: pathlib.Path,
    blocked_tasks: pathlib.Path,
) -> None:
    window = _pp_run_ops(
        tmp_path,
        "status-window-blocked",
        str(blocked_tasks),
        "--reset",
        RESET,
        "--red",
        RED,
        "--dim",
        DIM,
        "--bold",
        BOLD,
    )
    assert window.returncode == 0, window.stderr
    assert window.stdout == "".join(
        f"{line}\n" for line in status_window_blocked_lines(str(blocked_tasks), RESET, RED, DIM, BOLD)
    )

    pipeline = _pp_run_ops(
        tmp_path,
        "document-pipeline",
        str(blocked_tasks),
        "--use-color",
        "TRUE",
        "--label",
        CYAN,
        "--dim",
        DIM,
        "--reset",
        RESET,
    )
    assert pipeline.returncode == 0, pipeline.stderr
    assert pipeline.stdout == "".join(
        f"{line}\n" for line in document_pipeline_lines(str(blocked_tasks), True, CYAN, dim=DIM, reset=RESET)
    )

    attention = _pp_run_ops(tmp_path, "attention-blocked", str(tmp_path / "absent"), "--use-color", "false")
    assert attention.returncode == 0, attention.stderr
    assert attention.stdout == "  (no blocked tasks — system running normally)\n\n"


def test_cli_help_exits_zero(tmp_path: pathlib.Path) -> None:
    for args in (["--help"], ["attention-blocked", "--help"]):
        result = _pp_run_ops(tmp_path, *args)
        assert result.returncode == 0, result.stderr
        assert "usage:" in result.stdout


# ---------------------------------------------------------------------------
# The panes, run as bash scripts on a throwaway kanban root
# ---------------------------------------------------------------------------


def _pane(tmp_path: pathlib.Path, script: str, *args: str) -> subprocess.CompletedProcess:
    root = tmp_path / "root"
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
            "NO_COLOR": "1",
        },
    )
    return subprocess.run(
        ["bash", str(_DASHBOARD / script), "--kanban-root", str(root), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=120,
        check=False,
    )


def _registered_root(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "root"
    (root / "tmp").mkdir(parents=True)
    (root / "projects.cfg").write_text("[project:demo]\npriority = 1\n", encoding="utf-8")
    return root / "projects" / "demo" / "tasks"


def test_attention_pane_prints_the_module_lines(tmp_path: pathlib.Path) -> None:
    tasks = _registered_root(tmp_path)
    _task(tasks, "CODER-001", "## State\nBLOCKED\n\n## Blockers\nwaiting on a fix\n")
    result = _pane(tmp_path, "show-attention.sh")
    assert result.returncode == 0, result.stderr
    module_lines = list(attention_blocked_lines(str(tasks), False))
    # The blocked-since clock may tick between the two renders; compare the rest.
    stable = [line for line in module_lines if "Blocked since:" not in line]
    pane = [line for line in result.stdout.splitlines() if "Blocked since:" not in line]
    assert pane == ["! BLOCKED TASKS", "", *stable], result.stdout


def test_document_drill_pane_prints_the_module_lines(tmp_path: pathlib.Path) -> None:
    tasks = _registered_root(tmp_path)
    _task(tasks, "WRITER-001", "## State\nDONE\n", role="WRITER")
    _task(tasks, "TESTER-002", "## State\nBLOCKED\n", role="TESTER")
    result = _pane(tmp_path, "show-document-drill.sh", "--project", "demo")
    assert result.returncode == 0, result.stderr
    tail = result.stdout.splitlines()[-4:]
    assert tail == ["=== Document Pipeline ===", *document_pipeline_lines(str(tasks), False)], result.stdout
