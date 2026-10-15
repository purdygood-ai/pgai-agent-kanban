"""
test_pm_materialize_readme_git_fields.py
========================================
Materializer fixtures for the README fields that must agree with the wake
dispatch: ``## Working Directory``, ``## Git Repo`` and the RC branch names
written in prose.

Behavior covered:

  1. A worktree-dispatched task (CODER/WRITER/TESTER in a git workflow)
     renders ``## Working Directory`` as the dispatch wording; any other task
     keeps the plan's own value.
  2. In a git workflow ``## Git Repo`` is the configured ``git_repo_url``
     when ``push_to_remote`` is not ``false``, otherwise
     ``local (no remote in use)`` — never ``none``.
  3. Prose fields (Goal, Required Output, Constraints, Acceptance Criteria,
     Notes) name RC branches in the project's prefixed form; an already
     prefixed branch is never prefixed twice; ``## Source Branch`` is
     unchanged.
  4. With an empty ``branch_prefix`` the README differs from the legacy
     rendering only in the Git Repo and Working Directory lines.
  5. ``main()`` threads the workflow capabilities through, so a whole
     release plan materializes with the new fields.

Every task folder is written under pytest's ``tmp_path`` (a sandbox root);
no live install, git repository or remote is touched.
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

_RELEASE_CAPS = {"git_mode": "rw", "finalize": "tag", "agents": "pm,coder,writer,tester,cm"}
_TESTING_ONLY_CAPS = {"git_mode": "ro", "finalize": "report", "agents": "pm,tester"}
_DOCUMENT_CAPS = {"git_mode": "none", "finalize": "publish", "agents": "pm,writer,tester,cm"}

_PROSE_SECTIONS = ("Goal", "Required Output", "Constraints", "Acceptance Criteria", "Notes")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_project_cfg(
    project_root: pathlib.Path,
    branch_prefix: str,
    git_repo_url: str = "",
    push_to_remote: str = "false",
) -> None:
    """Write a project.cfg carrying the three keys the README rendering reads."""
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "project.cfg").write_text(
        "[project]\n"
        "project_name = fixture\n"
        "workflow_type = release\n"
        f"git_repo_url = {git_repo_url}\n"
        f"branch_prefix = {branch_prefix}\n"
        f"push_to_remote = {push_to_remote}\n",
        encoding="utf-8",
    )


def _work_task(role: str = "CODER", slug: str = "feat") -> dict:
    """A plan work task whose prose names RC branches in every shape."""
    task_id = f"{role}-20260101-001-{slug}"
    return {
        "task_id": task_id,
        "owner": "CLAUDE",
        "role": role,
        "assigned_agent": role,
        "slug": slug,
        "title": "Fixture task",
        "working_directory": "$PGAI_DEV_TREE_PATH",
        "git_repo": "none",
        "feature_branch": "none",
        "source_branch": "none",
        "goal": "Merge into rc/v9.9.9 once the fixture passes.",
        "inputs": [],
        "context_paths": [],
        "required_output": "A commit merged --no-ff into rc/v9.9.9 (already ai_rc/v9.9.9 here).",
        "constraints": ["Work on rc/vX.Y.Z, never on main.", "Leave refs/heads/rc/v9.9.9 alone."],
        "acceptance_criteria": ["git log rc/v9.9.9 shows the merge."],
        "depends_on": [],
        "prerequisite_ids": [],
        "notes": "The canonical branch is rc/v<version>.",
    }


def _render(
    tmp_path: pathlib.Path,
    task: dict,
    wf_caps,
    workflow_type: str = "release",
    name: str = "out",
) -> str:
    """Materialize *task* into a fresh tasks dir under *tmp_path*; return its README."""
    tasks_root = tmp_path / name
    tasks_root.mkdir()
    pm.create_task_folder(
        dict(task),
        str(tmp_path / "project"),
        tasks_root=str(tasks_root),
        release_version="v9.9.9",
        workflow_type=workflow_type,
        wf_caps=wf_caps,
    )
    return (tasks_root / task["task_id"] / "README.md").read_text(encoding="utf-8")


def _field(readme: str, heading: str) -> str:
    """Return the body of ``## <heading>`` up to the next ``## `` heading."""
    match = re.search(
        rf"^##\s+{re.escape(heading)}\s*\n+(.*?)(?=^##\s|\Z)", readme, re.M | re.S
    )
    assert match, f"'## {heading}' not found in README:\n{readme}"
    return match.group(1).strip()


def _bare_rc_hits(text: str) -> list:
    """Every 'rc/v' in *text* that is not directly preceded by a branch-name character."""
    return [m.start() for m in re.finditer(r"(?<![A-Za-z0-9_-])rc/v", text)]


# ---------------------------------------------------------------------------
# 1-3. branch_prefix = ai_, empty git_repo_url, push_to_remote = false
# ---------------------------------------------------------------------------


def test_coder_ticket_ai_prefix_local_project(tmp_path: pathlib.Path) -> None:
    """CODER README: local Git Repo, dispatch Working Directory, prefixed prose, Source Branch unchanged."""
    _write_project_cfg(tmp_path / "project", branch_prefix="ai_")
    task = _work_task("CODER")

    readme = _render(tmp_path, task, _RELEASE_CAPS)
    legacy = _render(tmp_path, task, None, name="legacy")

    assert _field(readme, "Git Repo") == "local (no remote in use)"
    assert _field(readme, "Working Directory") == "(assigned at dispatch — isolated worktree)"
    for section in _PROSE_SECTIONS:
        body = _field(readme, section)
        assert _bare_rc_hits(body) == [], f"bare rc/v left in ## {section}: {body!r}"
        assert "ai_ai_" not in body, f"doubled prefix in ## {section}: {body!r}"
    assert _field(readme, "Source Branch") == "ai_rc/v9.9.9"
    assert _field(readme, "Source Branch") == _field(legacy, "Source Branch")


def test_tester_ticket_gets_dispatch_wording(tmp_path: pathlib.Path) -> None:
    """TESTER runs in a detached worktree, so its README carries the dispatch wording too."""
    _write_project_cfg(tmp_path / "project", branch_prefix="ai_")
    readme = _render(tmp_path, _work_task("TESTER", slug="verify"), _RELEASE_CAPS)
    assert _field(readme, "Working Directory") == "(assigned at dispatch — isolated worktree)"
    assert _field(readme, "Git Repo") == "local (no remote in use)"


def test_testing_only_tester_gets_dispatch_wording(tmp_path: pathlib.Path) -> None:
    """A read-only git workflow (git_mode = ro) still dispatches TESTER into a worktree."""
    _write_project_cfg(tmp_path / "project", branch_prefix="ai_")
    readme = _render(
        tmp_path, _work_task("TESTER", slug="verify"), _TESTING_ONLY_CAPS,
        workflow_type="testing-only",
    )
    assert _field(readme, "Working Directory") == "(assigned at dispatch — isolated worktree)"


def test_cm_ticket_keeps_working_directory(tmp_path: pathlib.Path) -> None:
    """CM is not worktree-dispatched: its Working Directory is the plan value; Git Repo is still truthful."""
    _write_project_cfg(tmp_path / "project", branch_prefix="ai_")
    task = _work_task("CM", slug="open-rc")
    task["working_directory"] = "/srv/dev-tree"
    readme = _render(tmp_path, task, _RELEASE_CAPS)
    assert _field(readme, "Working Directory") == "/srv/dev-tree"
    assert _field(readme, "Git Repo") == "local (no remote in use)"


def test_assigned_agent_decides_dispatch(tmp_path: pathlib.Path) -> None:
    """Assigned Agent, not Role, picks the dispatched agent — as the wake does."""
    _write_project_cfg(tmp_path / "project", branch_prefix="ai_")
    task = _work_task("CODER", slug="routed")
    task["assigned_agent"] = "CM"
    task["working_directory"] = "/srv/dev-tree"
    readme = _render(tmp_path, task, _RELEASE_CAPS)
    assert _field(readme, "Working Directory") == "/srv/dev-tree"


def test_non_git_workflow_keeps_plan_values(tmp_path: pathlib.Path) -> None:
    """git_mode = none (document workflow): no worktree, no repository — plan values stand."""
    _write_project_cfg(tmp_path / "project", branch_prefix="ai_")
    task = _work_task("WRITER", slug="draft")
    task["working_directory"] = "none"
    readme = _render(tmp_path, task, _DOCUMENT_CAPS, workflow_type="document")
    assert _field(readme, "Working Directory") == "none"
    assert _field(readme, "Git Repo") == "none"


# ---------------------------------------------------------------------------
# Git Repo: configured URL vs local
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "git_repo_url, push_to_remote, expected",
    [
        ("git@example.com:org/repo.git", "true", "git@example.com:org/repo.git"),
        ("git@example.com:org/repo.git", '"true"', "git@example.com:org/repo.git"),
        # push_to_remote absent from the file reads as push (push-decision rule).
        ("git@example.com:org/repo.git", None, "git@example.com:org/repo.git"),
        ("git@example.com:org/repo.git", "false", "local (no remote in use)"),
        ("git@example.com:org/repo.git", '"false"', "local (no remote in use)"),
        ("", "true", "local (no remote in use)"),
        ('""', "true", "local (no remote in use)"),
    ],
)
def test_git_repo_field_follows_project_cfg(
    tmp_path: pathlib.Path, git_repo_url: str, push_to_remote, expected: str
) -> None:
    """## Git Repo is the URL only when one is configured and pushing is not disabled."""
    project_root = tmp_path / "project"
    _write_project_cfg(project_root, branch_prefix="ai_", git_repo_url=git_repo_url,
                       push_to_remote=push_to_remote or "false")
    if push_to_remote is None:
        cfg = project_root / "project.cfg"
        cfg.write_text(
            "\n".join(l for l in cfg.read_text(encoding="utf-8").splitlines()
                      if not l.startswith("push_to_remote")) + "\n",
            encoding="utf-8",
        )
    readme = _render(tmp_path, _work_task("CODER"), _RELEASE_CAPS)
    assert _field(readme, "Git Repo") == expected


def test_git_repo_field_without_project_cfg_is_local(tmp_path: pathlib.Path) -> None:
    """No project config at all: the project has no remote configured."""
    (tmp_path / "project").mkdir()
    assert pm.render_git_repo_field(str(tmp_path / "project")) == "local (no remote in use)"


# ---------------------------------------------------------------------------
# 4. Empty branch_prefix: only the two workspace lines differ
# ---------------------------------------------------------------------------


def test_empty_prefix_differs_only_in_workspace_lines(tmp_path: pathlib.Path) -> None:
    """With no prefix, prose is untouched and only Git Repo / Working Directory change."""
    _write_project_cfg(tmp_path / "project", branch_prefix="")
    task = _work_task("CODER")

    readme = _render(tmp_path, task, _RELEASE_CAPS)
    legacy = _render(tmp_path, task, None, name="legacy")

    new_lines = readme.splitlines()
    old_lines = legacy.splitlines()
    assert len(new_lines) == len(old_lines)
    changed = [
        (old_lines[i - 1], old, new)
        for i, (old, new) in enumerate(zip(old_lines, new_lines))
        if old != new
    ]
    assert changed == [
        ("## Working Directory", "$PGAI_DEV_TREE_PATH", "(assigned at dispatch — isolated worktree)"),
        ("## Git Repo", "none", "local (no remote in use)"),
    ]
    assert "rc/v9.9.9" in _field(readme, "Goal")
    assert _field(readme, "Source Branch") == "rc/v9.9.9"


# ---------------------------------------------------------------------------
# prefix_bare_rc_branches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, prefix, expected",
    [
        ("merge into rc/v1.2.3", "ai_", "merge into ai_rc/v1.2.3"),
        ("`rc/vX.Y.Z`", "ai_", "`ai_rc/vX.Y.Z`"),
        ("rc/v<version> and rc/v{version}", "ai_", "ai_rc/v<version> and ai_rc/v{version}"),
        ("refs/heads/rc/v1.2.3", "ai_", "refs/heads/ai_rc/v1.2.3"),
        ("ai_rc/v1.2.3 stays", "ai_", "ai_rc/v1.2.3 stays"),
        ("x_rc/v1.2.3", "x_", "x_rc/v1.2.3"),
        ("rc/v1.2.3", "x_", "x_rc/v1.2.3"),
        ("rc/v1.2.3", "team-", "team-rc/v1.2.3"),
        ("team-rc/v1.2.3", "team-", "team-rc/v1.2.3"),
        ("src/vendor and rc/version", "ai_", "src/vendor and rc/version"),
        ("rc/v1.2.3", "", "rc/v1.2.3"),
    ],
)
def test_prefix_bare_rc_branches(text: str, prefix: str, expected: str) -> None:
    """Bare RC branches gain the prefix; prefixed ones and ordinary words do not."""
    assert pm.prefix_bare_rc_branches(text, prefix) == expected


def test_prefix_bare_rc_branches_is_idempotent() -> None:
    """Rewriting twice equals rewriting once — a prefix is never doubled."""
    once = pm.prefix_bare_rc_branches("rc/v1.2.3, rc/vX.Y.Z", "ai_")
    assert pm.prefix_bare_rc_branches(once, "ai_") == once


# ---------------------------------------------------------------------------
# 5. main(): a whole release plan
# ---------------------------------------------------------------------------


def _build_kanban_root(tmp_path: pathlib.Path) -> tuple:
    """A sandbox kanban root with one ai_-prefixed, remote-less release project."""
    root = tmp_path / "kanban"
    project_root = root / "projects" / "fixture"
    _write_project_cfg(project_root, branch_prefix="ai_")
    (root / "projects.cfg").write_text("[project:fixture]\nenabled=true\n", encoding="utf-8")
    (project_root / "release-state.md").write_text(
        "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n"
        "## RC Opened By Task\nnone\n\n## Last Released\nnone\n",
        encoding="utf-8",
    )
    queues = project_root / "tasks" / "queues"
    queues.mkdir(parents=True)
    for agent in ("coder", "writer", "tester", "cm", "pm"):
        (queues / f"{agent}_backlog.md").write_text(f"# {agent} backlog\n", encoding="utf-8")
    (root / "workflows").mkdir()
    for plugin in ("release",):
        src = _REAL_WORKFLOWS_DIR / plugin / "workflow.cfg"
        (root / "workflows" / plugin).mkdir()
        (root / "workflows" / plugin / "workflow.cfg").write_text(
            src.read_text(encoding="utf-8"), encoding="utf-8"
        )
    return root, project_root


def test_main_materializes_release_plan_with_truthful_fields(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end through main(): work, TESTER and CM tickets carry the new fields."""
    root, project_root = _build_kanban_root(tmp_path)
    work = _work_task("CODER", slug="feat")
    for key in ("task_id", "owner", "feature_branch", "prerequisite_ids"):
        work.pop(key)
    work["sequence"] = 1
    plan = {
        "workflow_type": "release",
        "project_name": "fixture",
        "target_version": "v9.9.9",
        "test_required": "true",
        "human_approval_required": "auto",
        "requirements_path": "none",
        "tasks": [work],
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

    tasks_root = project_root / "tasks"
    readmes = {
        d.name.split("-")[0] + ("-open" if "open-rc" in d.name else ""): (d / "README.md").read_text(encoding="utf-8")
        for d in tasks_root.iterdir() if (d / "README.md").is_file()
    }
    assert {"CODER", "TESTER", "CM-open", "CM"} <= set(readmes), sorted(readmes)

    for key in ("CODER", "TESTER"):
        assert _field(readmes[key], "Working Directory") == "(assigned at dispatch — isolated worktree)", key
    for key, readme in readmes.items():
        assert _field(readme, "Git Repo") == "local (no remote in use)", key

    coder = readmes["CODER"]
    for section in _PROSE_SECTIONS:
        assert _bare_rc_hits(_field(coder, section)) == [], section
    assert _field(coder, "Source Branch") == "ai_rc/v9.9.9"
