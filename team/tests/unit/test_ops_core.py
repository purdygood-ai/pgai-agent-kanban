"""
test_ops_core.py
================
Behavioral unit tests for the pgai_agent_kanban.ops package.

Covers:
  - ops/errors.py  — exception hierarchy and Ambiguous attributes
  - ops/context.py — OpsContext path-resolution helpers and from_env()
  - ops/resolve.py — resolve_item: exact match, prefix-glob, NotFound,
                     Ambiguous, task-without-status.md, intake-state fallback
  - ops/write.py   — halt/unhalt idempotency, halt_after sentinel content,
                     halt_global/unhalt_global, deposit_intake routing,
                     close_item task/intake paths, wontdo_item refusal on
                     non-task, delete_item terminal-state guard,
                     reset_item WORKING-state refusal

All tests use tmp_path.  No live filesystem paths, no process-env leakage.
"""

from __future__ import annotations

import json
import pathlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pgai_agent_kanban.ops.errors import (
    Ambiguous,
    IoError,
    NotFound,
    OpsError,
    Refused,
)
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.resolve import (
    ResolveResult,
    _collect_prefix_matches,
    _read_intake_state,
    _read_task_state,
    resolve_item,
)
from pgai_agent_kanban.lib.terminal_states import is_terminal, normalize
from pgai_agent_kanban.ops.write import (
    halt,
    halt_after,
    halt_global,
    unhalt,
    unhalt_global,
    deposit_intake,
    close_item,
    delete_item,
    wontdo_item,
    reset_item,
)


# ---------------------------------------------------------------------------
# Helpers / mini fixture builders
# ---------------------------------------------------------------------------


def _make_project_tree(
    tmp_path: Path,
    project: str = "my-project",
) -> tuple[Path, OpsContext]:
    """Create a minimal project tree and return (project_root, ctx)."""
    kanban_root = tmp_path / "kanban"
    project_root = kanban_root / "projects" / project
    (project_root / "tasks" / "queues").mkdir(parents=True)
    (project_root / "bugs").mkdir(parents=True)
    (project_root / "priority").mkdir(parents=True)
    (project_root / "requirements").mkdir(parents=True)
    return project_root, OpsContext(kanban_root=kanban_root)


def _write_status_md(task_dir: Path, state: str, role: str = "CODER") -> None:
    """Write a minimal status.md into task_dir."""
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "status.md").write_text(
        f"# Status\n\n## Task\n{task_dir.name}\n\n## Role\n{role}\n\n"
        f"## State\n{state}\n\n## Blockers\nnone\n\n## Needs Human\nno\n",
        encoding="utf-8",
    )


def _write_intake_file(intake_dir: Path, name: str, status: str = "open") -> Path:
    """Write a minimal intake .md file and return its path."""
    intake_dir.mkdir(parents=True, exist_ok=True)
    p = intake_dir / f"{name}.md"
    p.write_text(
        f"# {name}\n\n## Status\n{status}\n",
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------------------
# ops/errors — exception hierarchy
# ---------------------------------------------------------------------------


def test_notfound_is_ops_error() -> None:
    """NotFound inherits from OpsError."""
    err = NotFound("missing item")
    assert isinstance(err, OpsError)


def test_ambiguous_is_ops_error() -> None:
    """Ambiguous inherits from OpsError."""
    err = Ambiguous("ambiguous match")
    assert isinstance(err, OpsError)


def test_refused_is_ops_error() -> None:
    """Refused inherits from OpsError."""
    err = Refused("operation not allowed")
    assert isinstance(err, OpsError)


def test_ioerror_is_ops_error() -> None:
    """IoError inherits from OpsError."""
    err = IoError("disk failure")
    assert isinstance(err, OpsError)


def test_ambiguous_carries_candidates_attribute() -> None:
    """Ambiguous exception stores the list of candidate paths in .candidates."""
    candidates = [pathlib.Path("/a"), pathlib.Path("/b")]
    err = Ambiguous("two matches", candidates=candidates)
    assert err.candidates == candidates


def test_ambiguous_stores_result_attribute() -> None:
    """Ambiguous exception stores the first-match result in .result."""
    fake_result = ResolveResult(item_type="task", path=pathlib.Path("/a"), state="DONE")
    err = Ambiguous("two matches", result=fake_result)
    assert err.result is fake_result


def test_ambiguous_defaults_candidates_to_empty_list() -> None:
    """Ambiguous defaults to an empty candidates list when none is supplied."""
    err = Ambiguous("no candidates given")
    assert err.candidates == []


def test_ambiguous_defaults_result_to_none() -> None:
    """Ambiguous defaults result to None when not supplied."""
    err = Ambiguous("no result given")
    assert err.result is None


def test_ioerror_preserves_cause_chain() -> None:
    """IoError can be chained from an OSError and preserves __cause__."""
    original = OSError("disk full")
    err = IoError("write failed")
    try:
        raise err from original
    except IoError as caught:
        assert caught.__cause__ is original


# ---------------------------------------------------------------------------
# ops/context — OpsContext path helpers
# ---------------------------------------------------------------------------


def test_ops_context_project_root_returns_projects_subdir(tmp_path: Path) -> None:
    """OpsContext.project_root returns kanban_root/projects/<project>."""
    ctx = OpsContext(kanban_root=tmp_path)
    assert ctx.project_root("alpha") == tmp_path / "projects" / "alpha"


def test_ops_context_tasks_dir_returns_tasks_subdir(tmp_path: Path) -> None:
    """OpsContext.tasks_dir returns kanban_root/projects/<project>/tasks."""
    ctx = OpsContext(kanban_root=tmp_path)
    assert ctx.tasks_dir("alpha") == tmp_path / "projects" / "alpha" / "tasks"


def test_ops_context_release_state_path(tmp_path: Path) -> None:
    """OpsContext.release_state_path returns the release-state.md path."""
    ctx = OpsContext(kanban_root=tmp_path)
    expected = tmp_path / "projects" / "alpha" / "release-state.md"
    assert ctx.release_state_path("alpha") == expected


def test_ops_context_queue_path(tmp_path: Path) -> None:
    """OpsContext.queue_path returns the <agent>_backlog.md path."""
    ctx = OpsContext(kanban_root=tmp_path)
    expected = (
        tmp_path / "projects" / "alpha" / "tasks" / "queues" / "coder_backlog.md"
    )
    assert ctx.queue_path("alpha", "coder") == expected


def test_ops_context_normalizes_kanban_root(tmp_path: Path) -> None:
    """OpsContext normalizes a string kanban_root to an absolute Path."""
    ctx = OpsContext(kanban_root=str(tmp_path))
    assert isinstance(ctx.kanban_root, pathlib.Path)
    assert ctx.kanban_root.is_absolute()


def test_ops_context_from_env_reads_kanban_root_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OpsContext.from_env reads KANBAN_ROOT env var when present."""
    monkeypatch.setenv("KANBAN_ROOT", str(tmp_path))
    ctx = OpsContext.from_env()
    assert ctx.kanban_root == tmp_path.resolve()


def test_ops_context_from_env_falls_back_to_pgai_root_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OpsContext.from_env falls back to PGAI_AGENT_KANBAN_ROOT_PATH when KANBAN_ROOT is absent."""
    monkeypatch.delenv("KANBAN_ROOT", raising=False)
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(tmp_path))
    ctx = OpsContext.from_env()
    assert ctx.kanban_root == tmp_path.resolve()


# ---------------------------------------------------------------------------
# ops/resolve — _collect_prefix_matches helper
# ---------------------------------------------------------------------------


def test_collect_prefix_matches_returns_empty_when_directory_absent(
    tmp_path: Path,
) -> None:
    """_collect_prefix_matches returns [] when the base directory does not exist."""
    result = _collect_prefix_matches(tmp_path / "nonexistent", "KEY", suffix="", is_dir=True)
    assert result == []


def test_collect_prefix_matches_finds_exact_directory_match(tmp_path: Path) -> None:
    """_collect_prefix_matches returns the exact directory when it exists."""
    d = tmp_path / "CODER-20260101-001-slug"
    d.mkdir()
    result = _collect_prefix_matches(tmp_path, "CODER-20260101-001-slug", suffix="", is_dir=True)
    assert result == [d]


def test_collect_prefix_matches_finds_prefix_directory_match(tmp_path: Path) -> None:
    """_collect_prefix_matches finds directories where name starts with key + '-'."""
    d = tmp_path / "CODER-20260101-001-myfunc"
    d.mkdir()
    result = _collect_prefix_matches(tmp_path, "CODER-20260101-001", suffix="", is_dir=True)
    assert result == [d]


def test_collect_prefix_matches_enforces_hyphen_boundary_on_prefix(
    tmp_path: Path,
) -> None:
    """_collect_prefix_matches does not match CODER-001-0020 for prefix 'CODER-001-002'."""
    # CODER-001-0020 should NOT match prefix CODER-001-002 (no hyphen after 002)
    d_wrong = tmp_path / "CODER-001-0020-slug"
    d_match = tmp_path / "CODER-001-002-slug"
    d_wrong.mkdir()
    d_match.mkdir()
    result = _collect_prefix_matches(tmp_path, "CODER-001-002", suffix="", is_dir=True)
    assert d_match in result
    assert d_wrong not in result


def test_collect_prefix_matches_finds_exact_file_match(tmp_path: Path) -> None:
    """_collect_prefix_matches returns the exact .md file when it exists."""
    f = tmp_path / "BUG-0001.md"
    f.write_text("# Bug", encoding="utf-8")
    result = _collect_prefix_matches(tmp_path, "BUG-0001", suffix=".md", is_dir=False)
    assert result == [f]


# ---------------------------------------------------------------------------
# ops/resolve — _read_task_state and _read_intake_state
# ---------------------------------------------------------------------------


def test_read_task_state_returns_state_value(tmp_path: Path) -> None:
    """_read_task_state extracts the ## State value from status.md."""
    status = tmp_path / "status.md"
    status.write_text("## State\nWORKING\n", encoding="utf-8")
    assert _read_task_state(status) == "WORKING"


def test_read_task_state_returns_empty_string_when_heading_absent(
    tmp_path: Path,
) -> None:
    """_read_task_state returns '' when ## State is not in the file."""
    status = tmp_path / "status.md"
    status.write_text("## Role\nCODER\n", encoding="utf-8")
    assert _read_task_state(status) == ""


def test_read_intake_state_reads_status_heading(tmp_path: Path) -> None:
    """_read_intake_state reads ## Status from intake files."""
    f = tmp_path / "BUG-0001.md"
    f.write_text("## Status\nrunning\n", encoding="utf-8")
    assert _read_intake_state(f) == "running"


def test_read_intake_state_falls_back_to_state_heading(tmp_path: Path) -> None:
    """_read_intake_state falls back to ## State when ## Status is absent."""
    f = tmp_path / "BUG-0001.md"
    f.write_text("## State\ndone\n", encoding="utf-8")
    assert _read_intake_state(f) == "done"


def test_read_intake_state_returns_empty_when_neither_heading_present(
    tmp_path: Path,
) -> None:
    """_read_intake_state returns '' when neither ## Status nor ## State is present."""
    f = tmp_path / "BUG-0001.md"
    f.write_text("# Title\n\n## Notes\nsome notes\n", encoding="utf-8")
    assert _read_intake_state(f) == ""


# ---------------------------------------------------------------------------
# ops/resolve — resolve_item
# ---------------------------------------------------------------------------


def test_resolve_item_raises_ops_error_when_project_root_absent(
    tmp_path: Path,
) -> None:
    """resolve_item raises OpsError when the project root directory does not exist."""
    ctx = OpsContext(kanban_root=tmp_path)
    with pytest.raises(OpsError, match="does not exist"):
        resolve_item(ctx, "nonexistent-project", "any-key")


def test_resolve_item_raises_not_found_when_key_matches_nothing(
    tmp_path: Path,
) -> None:
    """resolve_item raises NotFound when the key matches no task or intake item."""
    project_root, ctx = _make_project_tree(tmp_path)
    with pytest.raises(NotFound):
        resolve_item(ctx, "my-project", "CODER-99999999-999-ghost")


def test_resolve_item_resolves_task_exact_match(tmp_path: Path) -> None:
    """resolve_item returns a task ResolveResult for an exact task directory match."""
    project_root, ctx = _make_project_tree(tmp_path)
    task_dir = project_root / "tasks" / "CODER-20260101-001-myfunc"
    _write_status_md(task_dir, "BACKLOG")
    result = resolve_item(ctx, "my-project", "CODER-20260101-001-myfunc")
    assert result.item_type == "task"
    assert result.path == task_dir
    assert result.state == "BACKLOG"


def test_resolve_item_resolves_task_by_prefix(tmp_path: Path) -> None:
    """resolve_item resolves a task when a prefix uniquely matches one directory."""
    project_root, ctx = _make_project_tree(tmp_path)
    task_dir = project_root / "tasks" / "CODER-20260101-001-myfunc"
    _write_status_md(task_dir, "DONE")
    result = resolve_item(ctx, "my-project", "CODER-20260101-001")
    assert result.item_type == "task"
    assert result.state == "DONE"


def test_resolve_item_raises_ambiguous_when_prefix_matches_multiple_tasks(
    tmp_path: Path,
) -> None:
    """resolve_item raises Ambiguous when a prefix matches more than one task directory."""
    project_root, ctx = _make_project_tree(tmp_path)
    _write_status_md(project_root / "tasks" / "CODER-20260101-001-alpha", "DONE")
    _write_status_md(project_root / "tasks" / "CODER-20260101-001-beta", "DONE")
    with pytest.raises(Ambiguous) as exc_info:
        resolve_item(ctx, "my-project", "CODER-20260101-001")
    assert len(exc_info.value.candidates) == 2


def test_resolve_item_ambiguous_provides_first_candidate_as_result(
    tmp_path: Path,
) -> None:
    """resolve_item's Ambiguous exception includes a .result pointing to the first match."""
    project_root, ctx = _make_project_tree(tmp_path)
    _write_status_md(project_root / "tasks" / "CODER-20260101-001-alpha", "DONE")
    _write_status_md(project_root / "tasks" / "CODER-20260101-001-beta", "DONE")
    with pytest.raises(Ambiguous) as exc_info:
        resolve_item(ctx, "my-project", "CODER-20260101-001")
    assert exc_info.value.result is not None
    assert exc_info.value.result.item_type == "task"


def test_resolve_item_raises_ops_error_when_task_dir_missing_status_md(
    tmp_path: Path,
) -> None:
    """resolve_item raises OpsError when the task directory exists but lacks status.md."""
    project_root, ctx = _make_project_tree(tmp_path)
    task_dir = project_root / "tasks" / "CODER-20260101-002-broken"
    task_dir.mkdir(parents=True)
    # No status.md written
    with pytest.raises(OpsError, match="status.md is missing"):
        resolve_item(ctx, "my-project", "CODER-20260101-002-broken")


def test_resolve_item_resolves_bug_intake_file(tmp_path: Path) -> None:
    """resolve_item resolves a bug intake file in bugs/."""
    project_root, ctx = _make_project_tree(tmp_path)
    _write_intake_file(project_root / "bugs", "BUG-0001", status="open")
    result = resolve_item(ctx, "my-project", "BUG-0001")
    assert result.item_type == "bug"
    assert result.state == "open"


def test_resolve_item_resolves_priority_intake_file(tmp_path: Path) -> None:
    """resolve_item resolves a priority intake file in priority/."""
    project_root, ctx = _make_project_tree(tmp_path)
    _write_intake_file(project_root / "priority", "PRIORITY-0001", status="open")
    result = resolve_item(ctx, "my-project", "PRIORITY-0001")
    assert result.item_type == "priority"


def test_resolve_item_resolves_requirement_intake_file(tmp_path: Path) -> None:
    """resolve_item resolves a requirement intake file in requirements/."""
    project_root, ctx = _make_project_tree(tmp_path)
    _write_intake_file(project_root / "requirements", "v0.5.0-my-feature", status="running")
    result = resolve_item(ctx, "my-project", "v0.5.0-my-feature")
    assert result.item_type == "requirement"
    assert result.state == "running"


def test_resolve_item_searches_tasks_before_bugs(tmp_path: Path) -> None:
    """resolve_item resolves as task when the key matches both a task and a bug."""
    project_root, ctx = _make_project_tree(tmp_path)
    # Create a task AND a bug with similar names (task wins by resolution order)
    _write_status_md(project_root / "tasks" / "SHARED-001-foo", "DONE")
    _write_intake_file(project_root / "bugs", "SHARED-001", status="open")
    result = resolve_item(ctx, "my-project", "SHARED-001-foo")
    assert result.item_type == "task"


# ---------------------------------------------------------------------------
# ops/write — halt / unhalt
# ---------------------------------------------------------------------------


def test_halt_creates_halt_sentinel(tmp_path: Path) -> None:
    """halt creates PROJECT_ROOT/HALT when it does not yet exist."""
    project_root, ctx = _make_project_tree(tmp_path)
    halt(ctx, "my-project")
    assert (project_root / "HALT").exists()


def test_halt_is_idempotent(tmp_path: Path) -> None:
    """halt does not raise when PROJECT_ROOT/HALT already exists."""
    project_root, ctx = _make_project_tree(tmp_path)
    (project_root / "HALT").touch()
    halt(ctx, "my-project")  # second call must not raise
    assert (project_root / "HALT").exists()


def test_halt_raises_ops_error_when_project_root_absent(tmp_path: Path) -> None:
    """halt raises OpsError when the project root does not exist."""
    ctx = OpsContext(kanban_root=tmp_path)
    with pytest.raises(OpsError):
        halt(ctx, "nonexistent-project")


def test_unhalt_removes_halt_sentinel(tmp_path: Path) -> None:
    """unhalt removes PROJECT_ROOT/HALT when it is present."""
    project_root, ctx = _make_project_tree(tmp_path)
    (project_root / "HALT").touch()
    unhalt(ctx, "my-project")
    assert not (project_root / "HALT").exists()


def test_unhalt_is_idempotent_when_halt_absent(tmp_path: Path) -> None:
    """unhalt does not raise when PROJECT_ROOT/HALT does not exist."""
    project_root, ctx = _make_project_tree(tmp_path)
    unhalt(ctx, "my-project")  # no HALT file present — should be silent


def test_unhalt_raises_ops_error_when_project_root_absent(tmp_path: Path) -> None:
    """unhalt raises OpsError when the project root does not exist."""
    ctx = OpsContext(kanban_root=tmp_path)
    with pytest.raises(OpsError):
        unhalt(ctx, "nonexistent-project")


# ---------------------------------------------------------------------------
# ops/write — halt_after sentinel content
# ---------------------------------------------------------------------------


def test_halt_after_writes_token_to_halt_after_file(tmp_path: Path) -> None:
    """halt_after writes the token followed by a newline to HALT-AFTER."""
    project_root, ctx = _make_project_tree(tmp_path)
    halt_after(ctx, "my-project", token="coder")
    content = (project_root / "HALT-AFTER").read_text(encoding="utf-8")
    assert content == "coder\n"


def test_halt_after_default_token_is_rc(tmp_path: Path) -> None:
    """halt_after defaults to the 'rc' token when none is specified."""
    project_root, ctx = _make_project_tree(tmp_path)
    halt_after(ctx, "my-project")
    content = (project_root / "HALT-AFTER").read_text(encoding="utf-8")
    assert content.strip() == "rc"


def test_halt_after_overwrites_existing_sentinel(tmp_path: Path) -> None:
    """halt_after overwrites any existing HALT-AFTER file."""
    project_root, ctx = _make_project_tree(tmp_path)
    (project_root / "HALT-AFTER").write_text("pm\n", encoding="utf-8")
    halt_after(ctx, "my-project", token="coder")
    content = (project_root / "HALT-AFTER").read_text(encoding="utf-8")
    assert content.strip() == "coder"


def test_halt_after_raises_ops_error_when_project_root_absent(tmp_path: Path) -> None:
    """halt_after raises OpsError when the project root does not exist."""
    ctx = OpsContext(kanban_root=tmp_path)
    with pytest.raises(OpsError):
        halt_after(ctx, "nonexistent-project")


# ---------------------------------------------------------------------------
# ops/write — halt_global / unhalt_global
# ---------------------------------------------------------------------------


def test_halt_global_creates_kanban_root_halt(tmp_path: Path) -> None:
    """halt_global creates KANBAN_ROOT/HALT."""
    kanban_root = tmp_path / "kanban"
    kanban_root.mkdir()
    ctx = OpsContext(kanban_root=kanban_root)
    halt_global(ctx)
    assert (kanban_root / "HALT").exists()


def test_halt_global_is_idempotent(tmp_path: Path) -> None:
    """halt_global does not raise when KANBAN_ROOT/HALT already exists."""
    kanban_root = tmp_path / "kanban"
    kanban_root.mkdir()
    (kanban_root / "HALT").touch()
    ctx = OpsContext(kanban_root=kanban_root)
    halt_global(ctx)  # second call should be silent


def test_unhalt_global_removes_kanban_root_halt(tmp_path: Path) -> None:
    """unhalt_global removes KANBAN_ROOT/HALT."""
    kanban_root = tmp_path / "kanban"
    kanban_root.mkdir()
    (kanban_root / "HALT").touch()
    ctx = OpsContext(kanban_root=kanban_root)
    unhalt_global(ctx)
    assert not (kanban_root / "HALT").exists()


def test_unhalt_global_is_idempotent_when_halt_absent(tmp_path: Path) -> None:
    """unhalt_global does not raise when KANBAN_ROOT/HALT is already absent."""
    kanban_root = tmp_path / "kanban"
    kanban_root.mkdir()
    ctx = OpsContext(kanban_root=kanban_root)
    unhalt_global(ctx)  # no HALT sentinel — must be silent


# ---------------------------------------------------------------------------
# ops/write — deposit_intake routing
# ---------------------------------------------------------------------------


def test_deposit_intake_routes_bug_prefix_to_bugs_directory(tmp_path: Path) -> None:
    """deposit_intake copies BUG-* files into the bugs/ subdirectory."""
    project_root, ctx = _make_project_tree(tmp_path)
    src = tmp_path / "BUG-0042.md"
    src.write_text("# Bug report\n", encoding="utf-8")
    dest = deposit_intake(ctx, "my-project", src)
    assert dest == project_root / "bugs" / "BUG-0042.md"
    assert dest.exists()


def test_deposit_intake_routes_priority_prefix_to_priority_directory(
    tmp_path: Path,
) -> None:
    """deposit_intake copies PRIORITY-* files into the priority/ subdirectory."""
    project_root, ctx = _make_project_tree(tmp_path)
    src = tmp_path / "PRIORITY-0001.md"
    src.write_text("# Priority item\n", encoding="utf-8")
    dest = deposit_intake(ctx, "my-project", src)
    assert dest == project_root / "priority" / "PRIORITY-0001.md"


def test_deposit_intake_routes_versioned_md_to_requirements_directory(
    tmp_path: Path,
) -> None:
    """deposit_intake copies v[0-9]*.md files into the requirements/ subdirectory."""
    project_root, ctx = _make_project_tree(tmp_path)
    src = tmp_path / "v1.2.3-my-feature.md"
    src.write_text("# Requirements\n", encoding="utf-8")
    dest = deposit_intake(ctx, "my-project", src)
    assert dest == project_root / "requirements" / "v1.2.3-my-feature.md"


def test_deposit_intake_raises_refused_for_unrecognized_filename(
    tmp_path: Path,
) -> None:
    """deposit_intake raises Refused when the filename does not match any routing pattern."""
    project_root, ctx = _make_project_tree(tmp_path)
    src = tmp_path / "random-file.md"
    src.write_text("content\n", encoding="utf-8")
    with pytest.raises(Refused):
        deposit_intake(ctx, "my-project", src)


def test_deposit_intake_raises_ops_error_when_source_absent(tmp_path: Path) -> None:
    """deposit_intake raises OpsError when the source file does not exist."""
    project_root, ctx = _make_project_tree(tmp_path)
    with pytest.raises(OpsError, match="does not exist"):
        deposit_intake(ctx, "my-project", tmp_path / "nonexistent.md")


def test_deposit_intake_raises_ops_error_when_target_already_exists(
    tmp_path: Path,
) -> None:
    """deposit_intake raises OpsError (no clobber) when the target already exists."""
    project_root, ctx = _make_project_tree(tmp_path)
    src = tmp_path / "BUG-0042.md"
    src.write_text("content\n", encoding="utf-8")
    # Pre-create the destination
    (project_root / "bugs" / "BUG-0042.md").write_text("existing\n", encoding="utf-8")
    with pytest.raises(OpsError, match="no clobber"):
        deposit_intake(ctx, "my-project", src)


def test_deposit_intake_returns_deposited_path(tmp_path: Path) -> None:
    """deposit_intake returns the Path of the deposited file on success."""
    project_root, ctx = _make_project_tree(tmp_path)
    src = tmp_path / "BUG-0099.md"
    src.write_text("# Bug\n", encoding="utf-8")
    result = deposit_intake(ctx, "my-project", src)
    assert isinstance(result, pathlib.Path)
    assert result.is_file()


def test_deposit_intake_preserves_file_content(tmp_path: Path) -> None:
    """deposit_intake copies the source content verbatim to the destination."""
    project_root, ctx = _make_project_tree(tmp_path)
    src = tmp_path / "BUG-0050.md"
    content = "# Bug Title\n\n## Details\nsome detail\n"
    src.write_text(content, encoding="utf-8")
    dest = deposit_intake(ctx, "my-project", src)
    assert dest.read_text(encoding="utf-8") == content


# ---------------------------------------------------------------------------
# ops/write — close_item on agent task
# ---------------------------------------------------------------------------


def _setup_task_with_queue(
    tmp_path: Path, task_id: str, state: str
) -> tuple[Path, OpsContext]:
    """Build a project tree with a task and a queue entry, return (project_root, ctx)."""
    project_root, ctx = _make_project_tree(tmp_path)
    agent = task_id.split("-")[0].lower()
    task_dir = project_root / "tasks" / task_id
    _write_status_md(task_dir, state)
    queue_file = project_root / "tasks" / "queues" / f"{agent}_backlog.md"
    queue_file.write_text(f"- [ ] {task_id}\n", encoding="utf-8")
    return project_root, ctx


def test_close_item_sets_task_state_to_done(tmp_path: Path) -> None:
    """close_item writes DONE to ## State in the task's status.md."""
    task_id = "CODER-20260101-001-myfunc"
    project_root, ctx = _setup_task_with_queue(tmp_path, task_id, "WORKING")
    close_item(ctx, "my-project", task_id)
    text = (project_root / "tasks" / task_id / "status.md").read_text(encoding="utf-8")
    assert "## State\nDONE" in text


def test_close_item_clears_blockers_to_none(tmp_path: Path) -> None:
    """close_item sets ## Blockers to 'none' in the task's status.md."""
    task_id = "CODER-20260101-001-myfunc"
    project_root, ctx = _setup_task_with_queue(tmp_path, task_id, "WORKING")
    # Add a blocker
    status_path = project_root / "tasks" / task_id / "status.md"
    content = status_path.read_text(encoding="utf-8").replace(
        "## Blockers\nnone\n", "## Blockers\nsomething blocking\n"
    )
    status_path.write_text(content, encoding="utf-8")
    close_item(ctx, "my-project", task_id)
    text = status_path.read_text(encoding="utf-8")
    assert "## Blockers\nnone" in text


def test_close_item_raises_not_found_for_missing_task(tmp_path: Path) -> None:
    """close_item raises NotFound when the task key does not exist."""
    project_root, ctx = _make_project_tree(tmp_path)
    with pytest.raises(NotFound):
        close_item(ctx, "my-project", "CODER-99999999-001-ghost")


def test_close_item_raises_ops_error_for_invalid_intake_state(
    tmp_path: Path,
) -> None:
    """close_item raises OpsError when an invalid state is supplied for an intake item."""
    project_root, ctx = _make_project_tree(tmp_path)
    _write_intake_file(project_root / "bugs", "BUG-0001", status="open")
    with pytest.raises(OpsError, match="invalid state"):
        close_item(ctx, "my-project", "BUG-0001", state="invalid-state")


def test_close_item_dry_run_does_not_modify_status_md(tmp_path: Path) -> None:
    """close_item with dry_run=True prints the intended change without writing."""
    task_id = "CODER-20260101-001-myfunc"
    project_root, ctx = _setup_task_with_queue(tmp_path, task_id, "WORKING")
    original = (project_root / "tasks" / task_id / "status.md").read_text(encoding="utf-8")
    close_item(ctx, "my-project", task_id, dry_run=True)
    after = (project_root / "tasks" / task_id / "status.md").read_text(encoding="utf-8")
    assert after == original


# ---------------------------------------------------------------------------
# ops/write — wontdo_item
# ---------------------------------------------------------------------------


def test_wontdo_item_sets_task_state_to_wont_do(tmp_path: Path) -> None:
    """wontdo_item writes WONT-DO to ## State in the task's status.md."""
    task_id = "CODER-20260101-002-wont"
    project_root, ctx = _setup_task_with_queue(tmp_path, task_id, "BACKLOG")
    wontdo_item(ctx, "my-project", task_id)
    text = (project_root / "tasks" / task_id / "status.md").read_text(encoding="utf-8")
    assert "## State\nWONT-DO" in text


def test_wontdo_item_raises_ops_error_for_bug_intake_item(tmp_path: Path) -> None:
    """wontdo_item raises OpsError when the resolved item is not a task."""
    project_root, ctx = _make_project_tree(tmp_path)
    _write_intake_file(project_root / "bugs", "BUG-0001", status="open")
    with pytest.raises(OpsError, match="task-only"):
        wontdo_item(ctx, "my-project", "BUG-0001")


# ---------------------------------------------------------------------------
# ops/write — delete_item terminal-state guard
# ---------------------------------------------------------------------------


def test_delete_item_raises_refused_for_non_terminal_task(tmp_path: Path) -> None:
    """delete_item raises Refused when the task is in a non-terminal state."""
    task_id = "CODER-20260101-003-active"
    project_root, ctx = _setup_task_with_queue(tmp_path, task_id, "WORKING")
    with pytest.raises(Refused, match="not a terminal state"):
        delete_item(ctx, "my-project", task_id)


def test_delete_item_removes_terminal_task_directory(tmp_path: Path) -> None:
    """delete_item removes the task directory when the task is in a terminal state."""
    task_id = "CODER-20260101-004-done"
    project_root, ctx = _setup_task_with_queue(tmp_path, task_id, "DONE")
    task_dir = project_root / "tasks" / task_id
    delete_item(ctx, "my-project", task_id)
    assert not task_dir.exists()


def test_delete_item_force_bypasses_terminal_state_guard(tmp_path: Path) -> None:
    """delete_item with force=True deletes regardless of state."""
    task_id = "CODER-20260101-005-forced"
    project_root, ctx = _setup_task_with_queue(tmp_path, task_id, "WORKING")
    task_dir = project_root / "tasks" / task_id
    delete_item(ctx, "my-project", task_id, force=True)
    assert not task_dir.exists()


def test_delete_item_raises_not_found_for_missing_key(tmp_path: Path) -> None:
    """delete_item raises NotFound when the key matches no task or intake item."""
    project_root, ctx = _make_project_tree(tmp_path)
    with pytest.raises(NotFound):
        delete_item(ctx, "my-project", "CODER-ghost-key")


# ---------------------------------------------------------------------------
# ops/write — reset_item WORKING state refusal
# ---------------------------------------------------------------------------


def test_reset_item_raises_refused_when_task_is_working(tmp_path: Path) -> None:
    """reset_item raises Refused and leaves the task untouched when state is WORKING."""
    task_id = "CODER-20260101-006-running"
    project_root, ctx = _setup_task_with_queue(tmp_path, task_id, "WORKING")
    status_path = project_root / "tasks" / task_id / "status.md"
    original = status_path.read_text(encoding="utf-8")
    with pytest.raises(Refused, match="WORKING"):
        reset_item(ctx, "my-project", task_id)
    # Status must remain unmodified
    assert status_path.read_text(encoding="utf-8") == original


def test_reset_item_regenerates_status_md_for_done_task(tmp_path: Path) -> None:
    """reset_item rewrites status.md to BACKLOG state for a DONE task."""
    task_id = "CODER-20260101-007-done"
    project_root, ctx = _setup_task_with_queue(tmp_path, task_id, "DONE")
    reset_item(ctx, "my-project", task_id)
    text = (project_root / "tasks" / task_id / "status.md").read_text(encoding="utf-8")
    assert "## State\nBACKLOG" in text


def test_reset_item_raises_not_found_for_missing_task(tmp_path: Path) -> None:
    """reset_item raises NotFound when the key matches nothing."""
    project_root, ctx = _make_project_tree(tmp_path)
    with pytest.raises(NotFound):
        reset_item(ctx, "my-project", "CODER-ghost-task")


# ---------------------------------------------------------------------------
# ops/write — reset_item requirement intake (fresh-decompose recipe, an earlier defect)
# ---------------------------------------------------------------------------


def _write_requirement_file(
    project_root: Path,
    stem: str,
    status: str = "running",
    pm_task_id: str = "PM-20260101-001-decompose-v0-5-0",
) -> Path:
    """Write a requirement intake .md file with ## Status and ## PM Task fields."""
    req_dir = project_root / "requirements"
    req_dir.mkdir(parents=True, exist_ok=True)
    p = req_dir / f"{stem}.md"
    p.write_text(
        f"# {stem}\n\n"
        f"## Status\n{status}\n\n"
        f"## PM Task\n{pm_task_id}\n",
        encoding="utf-8",
    )
    return p


def test_reset_item_requirement_sets_status_to_open(tmp_path: Path) -> None:
    """reset_item sets ## Status to 'open' in a requirement file."""
    project_root, ctx = _make_project_tree(tmp_path)
    req_path = _write_requirement_file(project_root, "v0.5.0-my-feature", status="running")
    reset_item(ctx, "my-project", "v0.5.0-my-feature")
    text = req_path.read_text(encoding="utf-8")
    assert "## Status\nopen" in text


def test_reset_item_requirement_clears_pm_task_to_none(tmp_path: Path) -> None:
    """reset_item clears ## PM Task to 'none' in a requirement file (fresh-decompose recipe)."""
    project_root, ctx = _make_project_tree(tmp_path)
    req_path = _write_requirement_file(
        project_root,
        "v0.5.0-my-feature",
        status="running",
        pm_task_id="PM-20260101-001-decompose-v0-5-0",
    )
    reset_item(ctx, "my-project", "v0.5.0-my-feature")
    text = req_path.read_text(encoding="utf-8")
    assert "## PM Task\nnone" in text


def test_reset_item_requirement_leaves_pm_backlog_untouched(tmp_path: Path) -> None:
    """reset_item does NOT flip pm_backlog.md marker for requirement resets (an earlier defect).

    The fresh-decompose recipe leaves pm_backlog.md byte-identical so GUARD 4
    does not see a dangling non-[x] entry and defer all project selection.
    """
    project_root, ctx = _make_project_tree(tmp_path)
    pm_task_id = "PM-20260101-001-decompose-v0-5-0"
    _write_requirement_file(
        project_root, "v0.5.0-my-feature", status="running", pm_task_id=pm_task_id
    )
    # Pre-populate pm_backlog with the PM task marked done ([x]).
    pm_backlog = project_root / "tasks" / "queues" / "pm_backlog.md"
    original_backlog = f"- [x] {pm_task_id}\n"
    pm_backlog.write_text(original_backlog, encoding="utf-8")

    reset_item(ctx, "my-project", "v0.5.0-my-feature")

    # pm_backlog.md must be byte-identical after the reset.
    assert pm_backlog.read_text(encoding="utf-8") == original_backlog, (
        "reset_item must not modify pm_backlog.md for requirement resets"
    )


# ---------------------------------------------------------------------------
# lib.terminal_states — normalize and is_terminal
# ---------------------------------------------------------------------------


def test_normalize_lowercases_and_strips() -> None:
    """normalize returns a lowercased, stripped string."""
    assert normalize("DONE") == "done"
    assert normalize("  WONT-DO  ") == "wont-do"
    assert normalize("wont-do") == "wont-do"
    assert normalize("WORKING") == "working"


def test_is_terminal_accepts_lowercase_done() -> None:
    """is_terminal returns True for lowercase 'done'."""
    assert is_terminal("done") is True


def test_is_terminal_accepts_uppercase_done() -> None:
    """is_terminal returns True for uppercase 'DONE' (task form)."""
    assert is_terminal("DONE") is True


def test_is_terminal_accepts_lowercase_wont_do() -> None:
    """is_terminal returns True for lowercase 'wont-do' (intake form written by close.sh)."""
    assert is_terminal("wont-do") is True


def test_is_terminal_accepts_uppercase_wont_do() -> None:
    """is_terminal returns True for uppercase 'WONT-DO' (task form)."""
    assert is_terminal("WONT-DO") is True


def test_is_terminal_rejects_running() -> None:
    """is_terminal returns False for 'running' (non-terminal intake state)."""
    assert is_terminal("running") is False


def test_is_terminal_rejects_open() -> None:
    """is_terminal returns False for 'open' (non-terminal intake state)."""
    assert is_terminal("open") is False


def test_is_terminal_rejects_working() -> None:
    """is_terminal returns False for 'WORKING' (non-terminal task state)."""
    assert is_terminal("WORKING") is False


def test_is_terminal_rejects_blocked() -> None:
    """is_terminal returns False for 'blocked' (non-terminal state)."""
    assert is_terminal("blocked") is False


def test_is_terminal_rejects_superseded() -> None:
    """is_terminal returns False for 'superseded' (close state but not terminal for delete)."""
    assert is_terminal("superseded") is False


# ---------------------------------------------------------------------------
# delete_item — an earlier defect regression: wont-do intake item
# ---------------------------------------------------------------------------


def test_delete_item_accepts_wont_do_intake_item(tmp_path: Path) -> None:
    """delete_item deletes an intake item with state 'wont-do' without --force (an earlier defect)."""
    project_root, ctx = _make_project_tree(tmp_path)
    bug_path = _write_intake_file(project_root / "bugs", "BUG-0029", status="wont-do")
    delete_item(ctx, "my-project", "BUG-0029")
    assert not bug_path.exists()


def test_delete_item_accepts_done_intake_item(tmp_path: Path) -> None:
    """delete_item deletes an intake item with state 'done' without --force."""
    project_root, ctx = _make_project_tree(tmp_path)
    bug_path = _write_intake_file(project_root / "bugs", "BUG-0099", status="done")
    delete_item(ctx, "my-project", "BUG-0099")
    assert not bug_path.exists()


def test_delete_item_refuses_running_intake_item(tmp_path: Path) -> None:
    """delete_item raises Refused for a 'running' intake item (negative preserved)."""
    project_root, ctx = _make_project_tree(tmp_path)
    bug_path = _write_intake_file(project_root / "bugs", "BUG-0100", status="running")
    with pytest.raises(Refused, match="not a terminal state"):
        delete_item(ctx, "my-project", "BUG-0100")
    assert bug_path.exists()


def test_delete_item_accepts_wont_do_task(tmp_path: Path) -> None:
    """delete_item deletes a task in WONT-DO state without --force."""
    task_id = "CODER-20260101-010-wontdo"
    project_root, ctx = _setup_task_with_queue(tmp_path, task_id, "WONT-DO")
    task_dir = project_root / "tasks" / task_id
    delete_item(ctx, "my-project", task_id)
    assert not task_dir.exists()


def test_delete_item_refusal_message_uses_lowercase_canonical_form(
    tmp_path: Path,
) -> None:
    """delete_item's refusal message names terminal states in canonical lowercase form."""
    project_root, ctx = _make_project_tree(tmp_path)
    bug_path = _write_intake_file(project_root / "bugs", "BUG-0101", status="running")
    with pytest.raises(Refused) as exc_info:
        delete_item(ctx, "my-project", "BUG-0101")
    message = str(exc_info.value)
    # The remedy text must mention the canonical lowercase names.
    assert "done" in message
    assert "wont-do" in message
