"""
test_pm_materialize_default_workspace.py
========================================
How pm_materialize.ensure_default_workspace narrates the default workspace.

When a plan's tasks name no working directory and the project's dev tree
is a git checkout, the dev tree becomes the default workspace. The stderr
line labels it ``self-build`` only when that dev tree is the kanban's own
source tree (decided by its contents, never by the project name); every
other git project is labelled ``project dev tree``. A project with no git
dev tree gets a workspace under its own root, unlabelled.

Every tree here is built under pytest's ``tmp_path``; no live install or
real repository is touched (one case only reads this checkout's layout).
"""

from __future__ import annotations

import pathlib
import sys

try:
    import pm_agent.pm_materialize as pm  # installed via pm_agent package
except ImportError:
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent / "pm-agent"))
    import pm_materialize as pm  # type: ignore[no-redef]


_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_DEV_TREE_ROOT = _TEAM_DIR.parent


def _git_checkout(path: pathlib.Path, files=()) -> pathlib.Path:
    """A directory that reads as a git checkout, holding *files*."""
    (path / ".git").mkdir(parents=True)
    for rel in files:
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n", encoding="utf-8")
    return path


def _project_root(tmp_path: pathlib.Path, dev_tree: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "project_root"
    root.mkdir()
    (root / "project.cfg").write_text(
        f"[project]\ndev_tree_path = {dev_tree}\n",
        encoding="utf-8",
    )
    return root


def _narration(capsys) -> list:
    return [line for line in capsys.readouterr().err.splitlines() if "Default workspace" in line]


def test_arbitrary_git_project_workspace_is_not_labelled_self_build(tmp_path, capsys) -> None:
    dev_tree = _git_checkout(tmp_path / "example-app", ["src/main.py", "README.md"])
    tasks = [{"working_directory": "none"}]
    pm.ensure_default_workspace(_project_root(tmp_path, dev_tree), "example-app", tasks)

    lines = _narration(capsys)
    assert lines == [f"[pm-materialize] Default workspace (project dev tree): {dev_tree}"]
    assert "self-build" not in lines[0]
    assert tasks[0]["working_directory"] == str(dev_tree)


def test_kanban_source_tree_workspace_is_labelled_self_build(tmp_path, capsys) -> None:
    dev_tree = _git_checkout(tmp_path / "kanban-checkout", pm.KANBAN_SOURCE_TREE_MARKERS)
    tasks = [{"working_directory": ""}]
    pm.ensure_default_workspace(_project_root(tmp_path, dev_tree), "any-name", tasks)

    assert _narration(capsys) == [f"[pm-materialize] Default workspace (self-build): {dev_tree}"]
    assert tasks[0]["working_directory"] == str(dev_tree)


def test_label_follows_the_tree_not_the_project_name(tmp_path, capsys) -> None:
    dev_tree = _git_checkout(tmp_path / "other", ["install.sh"])
    pm.ensure_default_workspace(
        _project_root(tmp_path, dev_tree),
        "pgai-agent-kanban",
        [{"working_directory": "none"}],
    )
    (line,) = _narration(capsys)
    assert "(project dev tree)" in line
    assert "self-build" not in line


def test_this_checkout_is_recognised_as_the_kanban_source_tree() -> None:
    assert pm.is_kanban_source_tree(_DEV_TREE_ROOT) is True
    assert pm.default_workspace_label(_DEV_TREE_ROOT) == "self-build"


def test_project_without_git_dev_tree_gets_an_unlabelled_project_workspace(tmp_path, capsys) -> None:
    dev_tree = tmp_path / "plain-dir"
    dev_tree.mkdir()
    root = _project_root(tmp_path, dev_tree)
    tasks = [{"working_directory": "none"}]
    pm.ensure_default_workspace(root, "docs-project", tasks)

    expected = root / "workspaces" / "projects" / "docs-project"
    assert _narration(capsys) == [f"[pm-materialize] Default workspace: {expected}"]
    assert tasks[0]["working_directory"] == str(expected)
    assert expected.is_dir()
