"""
test_dashboard_named_queue_pane.py — ``team/scripts/dashboard/queue-pane.sh``
and its renderer, ``pgai_agent_kanban.dashboard.named_queue_pane``.

What is proven here:

  1. Given a project (``--project``, else ``PGAI_PROJECT_NAME``), the pane
     renders that project's named queue: counts and one ``[STATE] <id>``
     line per entry, the state read from the task's status.md.
  2. With no project resolvable -- none given, or a name projects.cfg does
     not register -- it prints ``<queue>: empty (queue file not found)`` and
     exits 0, and never renders a registered project's queue in its place.
  3. The renderer lists current role-prefixed IDs and legacy ``CLAUDE-``
     IDs, using the shape ``kanban_task_id_pattern`` prints; a line whose
     token is not a task ID is not an entry.
  4. A WAITING entry lists its unmet prerequisites, in either ID form.
  5. An entry written with the empty ``[]`` alias marker is listed as
     pending and counted, the same as ``[ ]``.

Every run uses a throwaway kanban root under ``tmp_path`` and an
environment built by ``build_scrubbed_env``; nothing reads the live install.
"""

from __future__ import annotations

import datetime
import pathlib
import subprocess
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.dashboard import named_queue_pane  # noqa: E402
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402

_QUEUE_PANE = _TEAM_DIR / "scripts" / "dashboard" / "queue-pane.sh"
_TASK_IDS_SH = _TEAM_DIR / "scripts" / "lib" / "task_ids.sh"

_PROJECT = "demo"
_CURRENT_ID = "CODER-20261016-002-x"
_LEGACY_ID = "CLAUDE-CODER-20260101-001-legacy"
_NOT_FOUND = "coder: empty (queue file not found)"


@pytest.fixture(scope="module")
def task_id_pattern() -> str:
    """The canonical task-ID shape, as task_ids.sh prints it."""
    result = subprocess.run(
        ["bash", "-c", f'source "{_TASK_IDS_SH}"\nkanban_task_id_pattern'],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


class _Root:
    """A throwaway kanban root with HOME and the temp dir inside it."""

    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.root = tmp_path / "kanban"
        self.home = tmp_path / "home"
        (self.root / "tmp").mkdir(parents=True)
        self.home.mkdir()

    def tasks(self, project: str) -> pathlib.Path:
        return self.root / "projects" / project / "tasks"

    def write_queue(self, project: str, body: str, name: str = "coder") -> None:
        queues = self.tasks(project) / "queues"
        queues.mkdir(parents=True, exist_ok=True)
        (queues / f"{name}_backlog.md").write_text(body, encoding="utf-8")

    def write_task(self, project: str, task_id: str, state: str, prerequisites: tuple[str, ...] = ()) -> None:
        task_dir = self.tasks(project) / task_id
        task_dir.mkdir(parents=True)
        (task_dir / "status.md").write_text(f"# Status\n\n## State\n{state}\n", encoding="utf-8")
        if prerequisites:
            lines = "".join(f"- {pid}\n" for pid in prerequisites)
            (task_dir / "README.md").write_text(f"# Task\n\n## Prerequisites\n{lines}", encoding="utf-8")

    def register(self, *projects: str) -> None:
        body = "".join(f"[project:{name}]\npriority={index}\n\n" for index, name in enumerate(projects, 1))
        (self.root / "projects.cfg").write_text(body, encoding="utf-8")

    def run(self, *args: str, project_name: str | None = None) -> subprocess.CompletedProcess:
        extra = {
            "KANBAN_ROOT": str(self.root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.root / "tmp"),
            "NO_COLOR": "1",
        }
        if project_name is not None:
            extra["PGAI_PROJECT_NAME"] = project_name
        env = build_scrubbed_env(
            home_dir=self.home,
            extra_path_dirs=[pathlib.Path(sys.executable).parent],
            extra_env=extra,
        )
        return subprocess.run(
            [str(_QUEUE_PANE), *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(self.root),
            timeout=60,
        )


@pytest.fixture
def kr(tmp_path: pathlib.Path) -> _Root:
    return _Root(tmp_path)


# ---------------------------------------------------------------------------
# 1. A resolved project's queue is rendered
# ---------------------------------------------------------------------------


def test_pane_lists_a_current_task_id_with_its_state_for_the_named_project(kr) -> None:
    kr.write_queue(_PROJECT, f"- [ ] {_CURRENT_ID}\n")
    result = kr.run("coder", "--project", _PROJECT)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "=== coder ===  1 pending", result.stdout
    assert f"  [BACKLOG] {_CURRENT_ID}  ⚠ task folder missing" in lines, result.stdout
    assert _NOT_FOUND not in result.stdout


def test_pane_reads_each_entry_state_from_its_status_file(kr) -> None:
    kr.write_queue(_PROJECT, f"- [ ] {_CURRENT_ID}\n- [A] CODER-20261016-003-y — in progress\n")
    kr.write_task(_PROJECT, _CURRENT_ID, "BLOCKED")
    kr.write_task(_PROJECT, "CODER-20261016-003-y", "WORKING")
    result = kr.run("coder", "--project", _PROJECT)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "=== coder ===  1 working, 1 pending", result.stdout
    assert f"  [BLOCKED] {_CURRENT_ID}" in lines, result.stdout
    assert "  [WORKING] CODER-20261016-003-y  in progress" in lines, result.stdout


def test_pane_falls_back_to_pgai_project_name(kr) -> None:
    kr.write_queue(_PROJECT, f"- [ ] {_CURRENT_ID}\n")
    result = kr.run("coder", project_name=_PROJECT)
    assert result.returncode == 0, result.stderr
    assert f"  [BACKLOG] {_CURRENT_ID}  ⚠ task folder missing" in result.stdout.splitlines(), result.stdout


def test_pane_resolves_a_registered_project_by_prefix(kr) -> None:
    kr.register("demo-board")
    kr.write_queue("demo-board", f"- [ ] {_CURRENT_ID}\n")
    result = kr.run("coder", "--project", "demo")
    assert result.returncode == 0, result.stderr
    assert f"  [BACKLOG] {_CURRENT_ID}  ⚠ task folder missing" in result.stdout.splitlines(), result.stdout


# ---------------------------------------------------------------------------
# 2. No resolvable project
# ---------------------------------------------------------------------------


def test_pane_without_a_project_prints_the_not_found_line_and_exits_zero(kr) -> None:
    result = kr.run("coder")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["=== coder ===", _NOT_FOUND]


def test_pane_without_a_project_never_renders_a_registered_project(kr) -> None:
    kr.register(_PROJECT)
    kr.write_queue(_PROJECT, f"- [ ] {_CURRENT_ID}\n")
    result = kr.run("coder")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["=== coder ===", _NOT_FOUND]


def test_pane_with_an_unregistered_project_prints_the_not_found_line(kr) -> None:
    kr.register(_PROJECT)
    kr.write_queue(_PROJECT, f"- [ ] {_CURRENT_ID}\n")
    result = kr.run("coder", "--project", "other")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["=== coder ===", _NOT_FOUND]
    assert "project_resolve.not_found" in result.stderr


# ---------------------------------------------------------------------------
# 3-4. The renderer's task-ID matching
# ---------------------------------------------------------------------------


def test_renderer_lists_current_and_legacy_ids_and_skips_other_lines(tmp_path, task_id_pattern) -> None:
    queue = tmp_path / "coder_backlog.md"
    queue.write_text(
        "# Coder Backlog\n\n- `[ ]` pending (BACKLOG, ready to pull)\n"
        f"- [x] {_LEGACY_ID}\n- [ ] {_CURRENT_ID} — first\n- [ ] ../escape-attempt\n- [ ] not-a-task\n",
        encoding="utf-8",
    )
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    text = named_queue_pane.render_named_queue(
        queue,
        tasks,
        "coder",
        task_id_pattern,
        now=datetime.datetime(2026, 1, 1, 12, 0, 0),
    )
    assert text.splitlines() == [
        "=== coder ===  1 done, 1 pending",
        "─" * 60,
        f"  [DONE] {_LEGACY_ID}",
        f"  [BACKLOG] {_CURRENT_ID}  ⚠ task folder missing  first",
        "",
        "updated 12:00:00",
    ]


def test_waiting_entry_lists_unmet_prerequisites_in_either_id_form(kr, task_id_pattern) -> None:
    waiting_id = "WRITER-20261016-005-docs"
    kr.write_queue(_PROJECT, f"- [W] {waiting_id}\n")
    kr.write_task(_PROJECT, waiting_id, "WAITING", prerequisites=(_CURRENT_ID, _LEGACY_ID, "CM-20261016-001-open"))
    kr.write_task(_PROJECT, _CURRENT_ID, "WORKING")
    kr.write_task(_PROJECT, _LEGACY_ID, "BACKLOG")
    kr.write_task(_PROJECT, "CM-20261016-001-open", "DONE")
    tasks = kr.tasks(_PROJECT)
    text = named_queue_pane.render_named_queue(tasks / "queues" / "coder_backlog.md", tasks, "coder", task_id_pattern)
    lines = text.splitlines()
    start = lines.index(f"  [WAITING] {waiting_id}")
    assert lines[start + 1 : start + 4] == [
        "    waiting on:",
        f"      [WORKING] {_CURRENT_ID}",
        f"      [BACKLOG] {_LEGACY_ID}",
    ], text


# ---------------------------------------------------------------------------
# 5. The empty-bracket alias marker
# ---------------------------------------------------------------------------


def test_renderer_lists_and_counts_an_empty_bracket_entry_as_pending(tmp_path, task_id_pattern) -> None:
    alias_id = "CODER-20261016-004-alias"
    queue = tmp_path / "coder_backlog.md"
    queue.write_text(
        f"- [ ] {_CURRENT_ID}\n- [x] CODER-20261016-003-y — done one\n"
        f"- [A] CLAUDE-20250101-001-legacy\n- [] {alias_id}\n",
        encoding="utf-8",
    )
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    text = named_queue_pane.render_named_queue(
        queue,
        tasks,
        "coder",
        task_id_pattern,
        now=datetime.datetime(2026, 1, 1, 12, 0, 0),
    )
    lines = text.splitlines()
    assert lines[0] == "=== coder ===  1 done, 1 working, 2 pending", text
    entry_lines = [line for line in lines[2:] if line.startswith("  [")]
    assert len(entry_lines) == 4, text
    assert f"  [BACKLOG] {alias_id}  ⚠ task folder missing" in lines, text


def test_pane_lists_an_empty_bracket_entry_beside_a_canonical_one(kr) -> None:
    alias_id = "CODER-20261016-004-alias"
    kr.write_queue(_PROJECT, f"- [ ] {_CURRENT_ID}\n- [] {alias_id}\n")
    kr.write_task(_PROJECT, alias_id, "BACKLOG")
    result = kr.run("coder", "--project", _PROJECT)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "=== coder ===  2 pending", result.stdout
    assert f"  [BACKLOG] {_CURRENT_ID}  ⚠ task folder missing" in lines, result.stdout
    assert f"  [BACKLOG] {alias_id}" in lines, result.stdout
