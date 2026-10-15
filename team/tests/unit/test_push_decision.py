"""
test_push_decision.py
=====================
Unit tests for the shared push-decision resolver
(``pgai_agent_kanban.lib.push_decision``) and its bash wrapper
(``scripts/lib/push_decision.sh``).

The resolver answers ``push``, ``no-push`` or ``unresolved`` for one project
from that project's own registration and config only:

  - registered + own project.cfg + dev_tree_path, no push_to_remote -> push
    (the default for a real project is unchanged);
  - the same with push_to_remote = false -> no-push;
  - no project.cfg -> unresolved, missing project-cfg;
  - not in projects.cfg (or no projects.cfg at all) -> unresolved, missing
    registration;
  - project.cfg without dev_tree_path -> unresolved, missing dev-tree-path,
    even when PGAI_DEV_TREE_PATH is set in the environment.

Every fixture is a throwaway kanban root under pytest's tmp_path; nothing
reads the live install or contacts any remote.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from pgai_agent_kanban.lib import push_decision as pd
from tests.unit.shell_harness import run_bash

_WRAPPER = "scripts/lib/push_decision.sh"
_PROJECT = "sample-project"


def _kanban(
    root: pathlib.Path,
    *,
    registered: bool = True,
    cfg: "str | None" = "[project]\ndev_tree_path = /srv/dev/sample\n",
    cfg_name: str = "project.cfg",
    projects_cfg: bool = True,
) -> pathlib.Path:
    """A kanban root with one project directory; returns the root."""
    project_dir = root / "projects" / _PROJECT
    project_dir.mkdir(parents=True)
    if projects_cfg:
        name = _PROJECT if registered else "other-project"
        (root / "projects.cfg").write_text(f"[project:{name}]\npriority = 1\n", encoding="utf-8")
    if cfg is not None:
        (project_dir / cfg_name).write_text(cfg, encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# Resolved projects: push / no-push
# ---------------------------------------------------------------------------


def test_registered_project_without_push_key_resolves_to_push(tmp_path):
    root = _kanban(tmp_path / "k")
    decision = pd.resolve_push_decision(root, _PROJECT)
    assert decision.decision == pd.PUSH
    assert decision.may_push
    assert decision.dev_tree == "/srv/dev/sample"
    assert decision.cfg_file == str(root / "projects" / _PROJECT / "project.cfg")
    assert decision.missing == ""
    assert decision.status_line == ""


@pytest.mark.parametrize("value", ["true", "yes", "", '"true"', "False"])
def test_any_push_value_other_than_false_resolves_to_push(tmp_path, value):
    root = _kanban(
        tmp_path / "k",
        cfg=f"[project]\ndev_tree_path = /srv/dev/sample\npush_to_remote = {value}\n",
    )
    assert pd.resolve_push_decision(root, _PROJECT).decision == pd.PUSH


@pytest.mark.parametrize("value", ["false", '"false"'])
def test_push_to_remote_false_resolves_to_no_push(tmp_path, value):
    root = _kanban(
        tmp_path / "k",
        cfg=f"[project]\ndev_tree_path = /srv/dev/sample\npush_to_remote = {value}\n",
    )
    decision = pd.resolve_push_decision(root, _PROJECT)
    assert decision.decision == pd.NO_PUSH
    assert not decision.may_push
    assert decision.dev_tree == "/srv/dev/sample"


def test_legacy_project_cfg_is_the_projects_own_config(tmp_path):
    root = _kanban(
        tmp_path / "k",
        cfg="dev_tree_path=/srv/dev/legacy\npush_to_remote=false\n",
        cfg_name="PROJECT.cfg",
    )
    decision = pd.resolve_push_decision(root, _PROJECT)
    assert decision.decision == pd.NO_PUSH
    assert decision.dev_tree == "/srv/dev/legacy"


# ---------------------------------------------------------------------------
# Unresolved projects: each names its missing piece
# ---------------------------------------------------------------------------


def test_registered_project_without_project_cfg_is_unresolved(tmp_path):
    root = _kanban(tmp_path / "k", cfg=None)
    decision = pd.resolve_push_decision(root, _PROJECT)
    assert decision.decision == pd.UNRESOLVED
    assert not decision.may_push
    assert decision.missing == "project-cfg"
    assert decision.dev_tree == ""
    assert decision.status_line.startswith("skipped-unresolved-project: ")
    assert "project.cfg does not exist" in decision.status_line


def test_project_missing_from_projects_cfg_is_unresolved(tmp_path):
    root = _kanban(tmp_path / "k", registered=False)
    decision = pd.resolve_push_decision(root, _PROJECT)
    assert decision.decision == pd.UNRESOLVED
    assert decision.missing == "registration"
    assert decision.dev_tree == ""
    assert f"[project:{_PROJECT}]" in decision.status_line


def test_absent_projects_cfg_is_unresolved(tmp_path):
    root = _kanban(tmp_path / "k", projects_cfg=False)
    decision = pd.resolve_push_decision(root, _PROJECT)
    assert decision.decision == pd.UNRESOLVED
    assert decision.missing == "registration"
    assert "projects.cfg does not exist" in decision.status_line


def test_unreadable_projects_cfg_is_unresolved(tmp_path):
    root = _kanban(tmp_path / "k")
    (root / "projects.cfg").write_text(f"{_PROJECT}:1\n", encoding="utf-8")
    decision = pd.resolve_push_decision(root, _PROJECT)
    assert decision.decision == pd.UNRESOLVED
    assert decision.missing == "registration"
    assert "cannot be read as INI" in decision.status_line


def test_project_cfg_without_dev_tree_path_is_unresolved_despite_inherited_dev_tree(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PGAI_DEV_TREE_PATH", str(tmp_path / "inherited-dev-tree"))
    root = _kanban(tmp_path / "k", cfg="[project]\nproject_name = sample-project\n")
    decision = pd.resolve_push_decision(root, _PROJECT)
    assert decision.decision == pd.UNRESOLVED
    assert decision.missing == "dev-tree-path"
    assert decision.dev_tree == ""
    assert "names no dev_tree_path" in decision.status_line
    assert "inherited-dev-tree" not in decision.status_line


@pytest.mark.parametrize("name", ["", "../sample-project", "a/b", "two words"])
def test_name_no_registry_can_hold_is_unresolved(tmp_path, name):
    root = _kanban(tmp_path / "k")
    decision = pd.resolve_push_decision(root, name)
    assert decision.decision == pd.UNRESOLVED
    assert decision.missing == "registration"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli(*args: str) -> subprocess.CompletedProcess:
    team_dir = pathlib.Path(__file__).resolve().parents[2]
    return subprocess.run(
        [sys.executable, "-m", "pgai_agent_kanban.lib.push_decision", *args],
        capture_output=True,
        text=True,
        cwd=team_dir,
        check=False,
    )


def test_cli_prints_key_value_lines_and_exits_zero_when_unresolved(tmp_path):
    root = _kanban(tmp_path / "k", cfg=None)
    result = _cli("--kanban-root", str(root), "--project", _PROJECT)
    assert result.returncode == 0, result.stderr
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines())
    assert fields["decision"] == "unresolved"
    assert fields["missing"] == "project-cfg"
    assert fields["status_line"].startswith("skipped-unresolved-project: ")
    assert set(fields) == {"decision", "project", "cfg_file", "dev_tree", "missing", "status_line"}


def test_cli_help_exits_zero():
    result = _cli("--help")
    assert result.returncode == 0
    assert "--kanban-root" in result.stdout and "--project" in result.stdout


# ---------------------------------------------------------------------------
# Bash wrapper
# ---------------------------------------------------------------------------


def _wrapper(tmp_path: pathlib.Path, root: "pathlib.Path | None", body: str):
    env = {"KANBAN_ROOT": str(root)} if root is not None else {}
    unset = "" if root is not None else "unset KANBAN_ROOT\n"
    script = f"set -euo pipefail\n{unset}source {_WRAPPER}\n{body}\n"
    return run_bash(tmp_path, script, extra_env=env)


def _show() -> str:
    return (
        'printf "%s|%s|%s|%s\\n" "$PP_PUSH_DECISION" "$PP_PUSH_DEV_TREE" '
        '"$PP_PUSH_MISSING" "$PP_PUSH_STATUS_LINE"'
    )


def test_wrapper_sets_push_fields(tmp_path):
    root = _kanban(tmp_path / "k")
    result = _wrapper(tmp_path, root, f"pp_push_decision {_PROJECT}\n{_show()}")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "push|/srv/dev/sample||"


def test_wrapper_sets_unresolved_fields(tmp_path):
    root = _kanban(tmp_path / "k", cfg="[project]\n")
    result = _wrapper(tmp_path, root, f"pp_push_decision {_PROJECT}\n{_show()}")
    assert result.returncode == 0, result.stderr
    decision, dev_tree, missing, line = result.stdout.strip().split("|", 3)
    assert (decision, dev_tree, missing) == ("unresolved", "", "dev-tree-path")
    assert line.startswith("skipped-unresolved-project: ")


def test_wrapper_fails_closed_when_the_resolver_cannot_run(tmp_path):
    root = _kanban(tmp_path / "k")
    body = f"pp_run_ops() {{ return 3; }}\npp_push_decision {_PROJECT}\n{_show()}"
    result = _wrapper(tmp_path, root, body)
    assert result.returncode == 0, result.stderr
    decision, dev_tree, missing, line = result.stdout.strip().split("|", 3)
    assert (decision, dev_tree, missing) == ("unresolved", "", "resolver")
    assert line.startswith("skipped-unresolved-project: ")


def test_wrapper_fails_closed_without_kanban_root(tmp_path):
    result = _wrapper(tmp_path, None, f"pp_push_decision {_PROJECT}\n{_show()}")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().startswith("unresolved||resolver|skipped-unresolved-project: ")
