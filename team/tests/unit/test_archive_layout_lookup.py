"""
test_archive_layout_lookup.py
=============================
Unit tests for pgai_agent_kanban.archive.layout and .lookup:

  - The archive tree for a project is <kanban_root>/archive/<project>/ and
    every sub-path (bugs/, priority/, requirements/, tasks/, tasks/queues/,
    logs/training/<role>/, ARCHIVE-LEDGER.tsv) carries the same relative
    name as the live project's.
  - A project name or role that is not one safe path component is refused
    dual-channel before any path is built.
  - An item ID (BUG-NNNN, PRIORITY-NNNN, a vX.Y.Z requirements file name, a
    task ID) resolves to its archived path when archived, and to None when
    it is not archived, when its shape is unrecognised, and when the
    project has no archive directory at all.

Every test builds its own kanban root under pytest's tmp_path; nothing here
reads or writes the live install.
"""

from __future__ import annotations

import pathlib

import pytest

from pgai_agent_kanban.archive import (
    ArchiveLayoutError,
    archive_layout,
    archive_root,
    classify_item_id,
    find_archived,
    find_item,
    live_layout,
)
from pgai_agent_kanban.lib.error_contract import parse_error_contract

_PROJECT = "demo-project"
_TASK_ID = "CODER-20261007-004-add-widget"
_LEGACY_TASK_ID = "CLAUDE-CODER-20260101-001-old-form"


@pytest.fixture
def kanban_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """A sandbox kanban root with an archive holding one item per category."""
    root = tmp_path / "kanban"
    archive = root / "archive" / _PROJECT
    (archive / "bugs").mkdir(parents=True)
    (archive / "bugs" / "BUG-0012-widget-crash.md").write_text("# BUG-0012\n", encoding="utf-8")
    (archive / "priority").mkdir()
    (archive / "priority" / "PRIORITY-0003-speed-up.md").write_text("# P\n", encoding="utf-8")
    (archive / "requirements").mkdir()
    (archive / "requirements" / "v2.1.0-widgets.md").write_text("# R\n", encoding="utf-8")
    (archive / "tasks" / _TASK_ID).mkdir(parents=True)
    (archive / "tasks" / _LEGACY_TASK_ID).mkdir()
    (archive / "tasks" / "queues").mkdir()
    (root / "projects" / _PROJECT).mkdir(parents=True)
    return root


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def test_archive_root_is_under_kanban_root_archive_project(tmp_path: pathlib.Path) -> None:
    assert archive_root(tmp_path, _PROJECT) == tmp_path / "archive" / _PROJECT


def test_archive_and_live_layouts_share_relative_names(tmp_path: pathlib.Path) -> None:
    """Each archive sub-path sits at the same relative location as its live
    counterpart, so an item keeps its relative path when it moves."""
    archived = archive_layout(tmp_path, _PROJECT)
    live = live_layout(tmp_path, _PROJECT)
    assert live.root == tmp_path / "projects" / _PROJECT
    expected = {
        "bugs": "bugs",
        "priority": "priority",
        "requirements": "requirements",
        "tasks": "tasks",
        "queues": "tasks/queues",
        "training": "logs/training",
    }
    for attr, relative in expected.items():
        assert getattr(archived, attr) == archived.root / relative, attr
        assert getattr(live, attr).relative_to(live.root) == getattr(archived, attr).relative_to(archived.root)
    assert archived.training_for("coder") == archived.root / "logs" / "training" / "coder"
    assert archived.ledger == archived.root / "ARCHIVE-LEDGER.tsv"


def test_layout_builds_paths_without_touching_the_filesystem(tmp_path: pathlib.Path) -> None:
    layout = archive_layout(tmp_path, _PROJECT)
    _ = (layout.bugs, layout.queues, layout.ledger, layout.training_for("tester"))
    assert not (tmp_path / "archive").exists()


@pytest.mark.parametrize("name", ["../escape", "a/b", "", ".", "..", "Upper", "-leading-dash", "with space"])
def test_unsafe_project_name_is_refused_dual_channel(tmp_path: pathlib.Path, name: str) -> None:
    with pytest.raises(ArchiveLayoutError) as caught:
        archive_root(tmp_path, name)
    fields = parse_error_contract(str(caught.value))
    assert fields is not None, str(caught.value)
    assert fields["error"] == "archive.invalid_path_component"
    assert f"'{name}'" in fields["message"]
    assert fields["remedy"].startswith("ls -1 "), fields["remedy"]
    assert "<" not in fields["remedy"], "remedy must be runnable as printed"


def test_unsafe_training_role_is_refused(tmp_path: pathlib.Path) -> None:
    with pytest.raises(ArchiveLayoutError):
        archive_layout(tmp_path, _PROJECT).training_for("../coder")


# ---------------------------------------------------------------------------
# Item ID classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "item_id, category, canonical",
    [
        ("BUG-0012", "bugs", "BUG-0012"),
        ("  bug-12 \n", "bugs", "BUG-0012"),
        ("BUG_0012", "bugs", "BUG-0012"),
        ("BUG-0012-widget-crash", "bugs", "BUG-0012"),
        ("BUG-0012-widget-crash.md", "bugs", "BUG-0012"),
        ("PRIORITY-0003", "priority", "PRIORITY-0003"),
        ("priority-3", "priority", "PRIORITY-0003"),
        ("v2.1.0-widgets", "requirements", "v2.1.0-widgets.md"),
        ("v2.1.0-widgets.md", "requirements", "v2.1.0-widgets.md"),
        ("V2.1.0-widgets.MD", "requirements", "v2.1.0-widgets.md"),
        (_TASK_ID, "tasks", _TASK_ID),
        (_LEGACY_TASK_ID, "tasks", _LEGACY_TASK_ID),
    ],
)
def test_item_ids_classify_to_their_category(item_id: str, category: str, canonical: str) -> None:
    parsed = classify_item_id(item_id)
    assert parsed is not None, item_id
    assert (parsed.category, parsed.canonical) == (category, canonical)


@pytest.mark.parametrize(
    "item_id",
    ["", "   ", "queues", "README", "../tasks", "BUG-", "v2.1", "v2.1.0/../../x", "CODER-2026-001-x", "coder-20261007-004-add-widget"],
)
def test_unrecognised_item_ids_classify_to_none(item_id: str) -> None:
    assert classify_item_id(item_id) is None


# ---------------------------------------------------------------------------
# Lookup: found, not found, no archive directory
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "item_id, relative",
    [
        ("BUG-0012", "bugs/BUG-0012-widget-crash.md"),
        ("bug-12", "bugs/BUG-0012-widget-crash.md"),
        ("PRIORITY-0003", "priority/PRIORITY-0003-speed-up.md"),
        ("v2.1.0-widgets", "requirements/v2.1.0-widgets.md"),
        ("v2.1.0-widgets.md", "requirements/v2.1.0-widgets.md"),
        (_TASK_ID, f"tasks/{_TASK_ID}"),
        (_LEGACY_TASK_ID, f"tasks/{_LEGACY_TASK_ID}"),
    ],
)
def test_archived_item_is_found_by_id(kanban_root: pathlib.Path, item_id: str, relative: str) -> None:
    assert find_archived(kanban_root, _PROJECT, item_id) == kanban_root / "archive" / _PROJECT / relative


@pytest.mark.parametrize(
    "item_id",
    ["BUG-0013", "PRIORITY-0004", "v2.1.0-other", "v2.1.0", "CODER-20261007-005-not-archived", "queues", "not-an-id"],
)
def test_item_not_in_archive_is_none(kanban_root: pathlib.Path, item_id: str) -> None:
    assert find_archived(kanban_root, _PROJECT, item_id) is None


def test_lookup_ignores_live_items(kanban_root: pathlib.Path) -> None:
    """A bug present only in the live project is not reported as archived."""
    live_bugs = kanban_root / "projects" / _PROJECT / "bugs"
    live_bugs.mkdir()
    (live_bugs / "BUG-0099-live.md").write_text("# live\n", encoding="utf-8")
    assert find_archived(kanban_root, _PROJECT, "BUG-0099") is None
    assert find_item(live_layout(kanban_root, _PROJECT), "BUG-0099") == live_bugs / "BUG-0099-live.md"


def test_task_id_naming_a_file_rather_than_a_folder_is_none(kanban_root: pathlib.Path) -> None:
    stray = kanban_root / "archive" / _PROJECT / "tasks" / "CODER-20261007-006-stray-file"
    stray.write_text("not a task folder\n", encoding="utf-8")
    assert find_archived(kanban_root, _PROJECT, stray.name) is None


def test_lookup_with_no_archive_directory_is_none(tmp_path: pathlib.Path) -> None:
    """A project that has never been archived has no archive/<project>/
    directory; every lookup answers None and nothing is created."""
    root = tmp_path / "kanban"
    (root / "projects" / _PROJECT).mkdir(parents=True)
    for item_id in ("BUG-0001", "PRIORITY-0001", "v1.0.0-first", _TASK_ID):
        assert find_archived(root, _PROJECT, item_id) is None, item_id
    assert not (root / "archive").exists()


def test_lookup_with_archive_root_but_no_category_directory_is_none(tmp_path: pathlib.Path) -> None:
    (tmp_path / "archive" / _PROJECT).mkdir(parents=True)
    assert find_archived(tmp_path, _PROJECT, "BUG-0001") is None
    assert find_archived(tmp_path, _PROJECT, _TASK_ID) is None
