"""
test_pm_materialize_mainline_source_branch.py
=============================================
The mainline branch the materializer writes into ``## Source Branch`` is the
project's real branch: ``<branch_prefix>main`` — ``ai_main`` under
``branch_prefix = ai_``, plain ``main`` when the prefix is empty.

Behavior covered:

  1. determine_source_branch's three mainline fallbacks — the CM-open
     bookend, a release with no target_version, and a non-release task with
     no source_branch and no active RC — return the prefixed mainline, under
     both an ``ai_`` and an empty prefix.
  2. The paths that do not fall back are unchanged: release work tickets get
     ``<branch_prefix>rc/<target_version>``, a non-release task's explicit
     source_branch is honored as given, and an active RC wins over the
     mainline.
  3. End to end through ``main()``: a release plan materialized on an
     ``ai_``-prefixed fixture project writes ``ai_main`` on the open-rc
     ticket and ``ai_rc/v9.9.9`` on work tickets; with an empty prefix it
     writes ``main`` and ``rc/v9.9.9``.

Every file is written under pytest's ``tmp_path``; no live install, git
repository or remote is touched.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

import pytest

try:
    import pm_agent.pm_materialize as pm  # installed via pm_agent package
except ImportError:
    sys.path.insert(
        0, str(pathlib.Path(__file__).parent.parent.parent / "pm-agent")
    )
    import pm_materialize as pm  # type: ignore[no-redef]


_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_REAL_WORKFLOWS_DIR = _TEAM_DIR / "workflows"

# (branch_prefix as written in project.cfg, the mainline it must yield,
#  the RC branch for v9.9.9 it must yield)
_PREFIX_CASES = [
    pytest.param("ai_", "ai_main", "ai_rc/v9.9.9", id="ai_-prefix"),
    pytest.param("", "main", "rc/v9.9.9", id="empty-prefix"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_project_cfg(project_root: pathlib.Path, branch_prefix: str) -> None:
    """Write a remote-less release project.cfg carrying *branch_prefix*."""
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "project.cfg").write_text(
        "[project]\n"
        "project_name = fixture\n"
        "workflow_type = release\n"
        "git_repo_url = \n"
        f"branch_prefix = {branch_prefix}\n"
        "push_to_remote = false\n",
        encoding="utf-8",
    )


def _write_release_state(project_root: pathlib.Path, active_rc: str = "none") -> None:
    """Write a release-state.md naming *active_rc* as the Active RC."""
    (project_root / "release-state.md").write_text(
        f"# Release State\n\n## Active RC\n{active_rc}\n\n## RC Opened At\nnone\n\n"
        "## RC Opened By Task\nnone\n\n## Last Released\nnone\n",
        encoding="utf-8",
    )


def _cm_open_task() -> dict:
    """The CM bookend that creates the RC branch (as PM's plan would carry it)."""
    return {
        "slug": "open-rc-9-9-9",
        "role": "CM",
        "assigned_agent": "CM",
        "source_branch": "main",
        "cm_operation": "open-rc",
    }


def _coder_task(source_branch: str = "") -> dict:
    """A CODER task whose plan-supplied source_branch is *source_branch*."""
    return {
        "slug": "feat",
        "role": "CODER",
        "assigned_agent": "CODER",
        "source_branch": source_branch,
    }


def _field(readme: str, heading: str) -> str:
    """Return the body of ``## <heading>`` up to the next ``## `` heading."""
    match = re.search(
        rf"^##\s+{re.escape(heading)}\s*\n+(.*?)(?=^##\s|\Z)", readme, re.M | re.S
    )
    assert match, f"'## {heading}' not found in README:\n{readme}"
    return match.group(1).strip()


# ---------------------------------------------------------------------------
# 1. The three mainline fallbacks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prefix, mainline, _rc", _PREFIX_CASES)
def test_cm_open_bookend_sources_from_the_prefixed_mainline(
    tmp_path: pathlib.Path, prefix: str, mainline: str, _rc: str
) -> None:
    """The open-rc ticket branches from the real mainline, never a bare main under a prefix."""
    _write_project_cfg(tmp_path, prefix)
    result = pm.determine_source_branch(
        _cm_open_task(), str(tmp_path), workflow_type="release", target_version="v9.9.9"
    )
    assert result == mainline


@pytest.mark.parametrize("prefix, mainline, _rc", _PREFIX_CASES)
@pytest.mark.parametrize("target_version", [None, "", "none"])
def test_release_without_target_version_falls_back_to_the_prefixed_mainline(
    tmp_path: pathlib.Path, prefix: str, mainline: str, _rc: str, target_version
) -> None:
    """A release plan with no usable target_version sources work from the real mainline."""
    _write_project_cfg(tmp_path, prefix)
    result = pm.determine_source_branch(
        _coder_task(), str(tmp_path), workflow_type="release", target_version=target_version
    )
    assert result == mainline


@pytest.mark.parametrize("prefix, mainline, _rc", _PREFIX_CASES)
@pytest.mark.parametrize("source_branch", ["", "none", "None"])
def test_non_release_without_source_or_active_rc_falls_back_to_the_prefixed_mainline(
    tmp_path: pathlib.Path, prefix: str, mainline: str, _rc: str, source_branch: str
) -> None:
    """A feature task with no source_branch and no active RC sources from the real mainline."""
    _write_project_cfg(tmp_path, prefix)
    _write_release_state(tmp_path, active_rc="none")
    result = pm.determine_source_branch(
        _coder_task(source_branch), str(tmp_path), workflow_type="feature", target_version=None
    )
    assert result == mainline


# ---------------------------------------------------------------------------
# 2. Paths that do not fall back are unchanged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prefix, _mainline, rc", _PREFIX_CASES)
@pytest.mark.parametrize("role", ["CODER", "WRITER", "TESTER"])
def test_release_work_ticket_still_gets_the_prefixed_rc_branch(
    tmp_path: pathlib.Path, prefix: str, _mainline: str, rc: str, role: str
) -> None:
    """Release work tickets keep <branch_prefix>rc/<target_version>, whatever the plan said."""
    _write_project_cfg(tmp_path, prefix)
    task = _coder_task("main")
    task["role"] = task["assigned_agent"] = role
    result = pm.determine_source_branch(
        task, str(tmp_path), workflow_type="release", target_version="v9.9.9"
    )
    assert result == rc


@pytest.mark.parametrize("prefix, _mainline, rc", _PREFIX_CASES)
def test_non_release_active_rc_wins_over_the_mainline(
    tmp_path: pathlib.Path, prefix: str, _mainline: str, rc: str
) -> None:
    """With an active RC, a feature task with no source_branch targets that RC, not the mainline."""
    _write_project_cfg(tmp_path, prefix)
    _write_release_state(tmp_path, active_rc="v9.9.9")
    result = pm.determine_source_branch(
        _coder_task(""), str(tmp_path), workflow_type="feature", target_version=None
    )
    assert result == rc


def test_non_release_explicit_source_branch_is_honored_as_given(tmp_path: pathlib.Path) -> None:
    """An explicit feature-workflow source_branch is returned verbatim, never re-prefixed."""
    _write_project_cfg(tmp_path, "ai_")
    result = pm.determine_source_branch(
        _coder_task("ai_feature/shared"), str(tmp_path), workflow_type="feature", target_version=None
    )
    assert result == "ai_feature/shared"


# ---------------------------------------------------------------------------
# 3. main(): a whole release plan
# ---------------------------------------------------------------------------


def _build_kanban_root(tmp_path: pathlib.Path, branch_prefix: str) -> tuple:
    """A sandbox kanban root with one remote-less release project using *branch_prefix*."""
    root = tmp_path / "kanban"
    project_root = root / "projects" / "fixture"
    _write_project_cfg(project_root, branch_prefix)
    (root / "projects.cfg").write_text("[project:fixture]\nenabled=true\n", encoding="utf-8")
    _write_release_state(project_root)
    queues = project_root / "tasks" / "queues"
    queues.mkdir(parents=True)
    for agent in ("coder", "writer", "tester", "cm", "pm"):
        (queues / f"{agent}_backlog.md").write_text(f"# {agent} backlog\n", encoding="utf-8")
    src = _REAL_WORKFLOWS_DIR / "release" / "workflow.cfg"
    (root / "workflows" / "release").mkdir(parents=True)
    (root / "workflows" / "release" / "workflow.cfg").write_text(
        src.read_text(encoding="utf-8"), encoding="utf-8"
    )
    return root, project_root


def _plan_work_task() -> dict:
    """One CODER work task as PM's plan JSON carries it."""
    return {
        "sequence": 1,
        "role": "CODER",
        "assigned_agent": "CODER",
        "slug": "feat",
        "title": "Fixture task",
        "working_directory": "$PGAI_DEV_TREE_PATH",
        "git_repo": "none",
        "source_branch": "none",
        "goal": "Do the work.",
        "inputs": [],
        "context_paths": [],
        "required_output": "A merged change.",
        "constraints": ["Be careful."],
        "acceptance_criteria": ["It works."],
        "depends_on": [],
        "notes": "none",
    }


@pytest.mark.parametrize("prefix, mainline, rc", _PREFIX_CASES)
def test_materialized_release_plan_writes_real_branch_names(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, prefix: str, mainline: str, rc: str
) -> None:
    """End to end: open-rc ticket names the real mainline; work tickets name the real RC branch."""
    root, project_root = _build_kanban_root(tmp_path, prefix)
    plan = {
        "workflow_type": "release",
        "project_name": "fixture",
        "target_version": "v9.9.9",
        "test_required": "true",
        "human_approval_required": "auto",
        "requirements_path": "none",
        "tasks": [_plan_work_task()],
    }
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(plan), encoding="utf-8")

    for name in ("PGAI_TASKS_DIR", "PGAI_REQUIREMENTS_DIR", "PGAI_DEV_TREE_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
    monkeypatch.setenv("PGAI_PROJECT_ROOT", str(project_root))
    monkeypatch.setattr(
        sys, "argv",
        ["pm_materialize.py", str(plan_file), "--team-root", str(project_root), "--owner", "CLAUDE"],
    )
    try:
        pm.main()
    except SystemExit as exc:  # main() may exit 0 explicitly
        assert exc.code in (0, None), f"pm_materialize main() exited {exc.code}"

    sources = {
        d.name: _field((d / "README.md").read_text(encoding="utf-8"), "Source Branch")
        for d in (project_root / "tasks").iterdir() if (d / "README.md").is_file()
    }
    open_rc = {name: sb for name, sb in sources.items() if "open-rc" in name}
    work = {name: sb for name, sb in sources.items() if name.startswith(("CODER-", "TESTER-"))}
    assert len(open_rc) == 1, sorted(sources)
    assert {"CODER", "TESTER"} <= {name.split("-")[0] for name in work}, sorted(sources)

    assert list(open_rc.values()) == [mainline], open_rc
    for name, sb in work.items():
        assert sb == rc, f"{name}: ## Source Branch {sb!r}, expected {rc!r}"
