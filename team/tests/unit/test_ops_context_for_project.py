"""
test_ops_context_for_project.py — the shared project-path resolver.

``pgai_agent_kanban.ops.context.context_for_project`` maps a path in the
kanban layout to ``(OpsContext, project name)``: a project root
(``<kanban_root>/projects/<project>/``), or with ``intake_dir=True`` a
directory directly inside one (``bugs/``, ``priority/``). A path outside
the layout is refused with an OpsError naming it. Every case builds its own
layout under tmp_path; nothing reads the environment.
"""

from __future__ import annotations

import pathlib

import pytest

from pgai_agent_kanban.ops.context import OpsContext, context_for_project
from pgai_agent_kanban.ops.errors import OpsError

_PROJECT = "sample-proj"


def _layout(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """``(kanban_root, project_root)`` with bugs/ and priority/ in place."""
    kanban_root = tmp_path / "kanban"
    project_root = kanban_root / "projects" / _PROJECT
    for sub in ("bugs", "priority"):
        (project_root / sub).mkdir(parents=True)
    return kanban_root, project_root


def test_a_project_root_resolves_to_its_kanban_root_and_name(tmp_path: pathlib.Path) -> None:
    kanban_root, project_root = _layout(tmp_path)

    ctx, project = context_for_project(project_root)

    assert isinstance(ctx, OpsContext)
    assert ctx.kanban_root == kanban_root.resolve()
    assert project == _PROJECT
    assert ctx.project_root(project) == project_root.resolve()


@pytest.mark.parametrize("sub", ["bugs", "priority"])
def test_an_intake_dir_resolves_to_the_project_it_sits_in(tmp_path: pathlib.Path, sub: str) -> None:
    kanban_root, project_root = _layout(tmp_path)

    ctx, project = context_for_project(project_root / sub, intake_dir=True)

    assert ctx.kanban_root == kanban_root.resolve()
    assert project == _PROJECT


def test_a_symlinked_project_root_resolves_to_its_target(tmp_path: pathlib.Path) -> None:
    kanban_root, project_root = _layout(tmp_path)
    link = tmp_path / "shortcut"
    link.symlink_to(project_root, target_is_directory=True)

    ctx, project = context_for_project(link)

    assert ctx.kanban_root == kanban_root.resolve()
    assert project == _PROJECT


def test_a_path_outside_the_layout_is_refused_naming_it(tmp_path: pathlib.Path) -> None:
    stray = tmp_path / "elsewhere" / "bugs"
    stray.mkdir(parents=True)

    with pytest.raises(OpsError) as refused:
        context_for_project(stray, intake_dir=True)

    assert str(stray.parent.resolve()) in str(refused.value)
    assert "<kanban_root>/projects/<project>/" in str(refused.value)


def test_an_intake_dir_given_as_a_project_root_is_refused(tmp_path: pathlib.Path) -> None:
    _, project_root = _layout(tmp_path)

    with pytest.raises(OpsError):
        context_for_project(project_root / "bugs")
