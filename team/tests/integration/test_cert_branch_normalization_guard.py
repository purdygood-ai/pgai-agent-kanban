"""
test_cert_branch_normalization_guard.py
========================================
Certification fixture: pm_materialize.py computes the canonical RC/mainline
branch and normalizes-or-fails plan-supplied branch strings (BUG-0037:
materializer trusts model-authored branch names).

This test exercises the fix through the real production entry point —
pm_materialize.py invoked as a subprocess, exactly as pm-agent.sh invokes
it — proving the following acceptance criteria end to end:

  1. A doubled-prefix source_branch ("ai_rc/ai_v9.9.9") for a v9.9.9 release
     decompose produces a task README carrying the canonical "ai_rc/v9.9.9",
     with normalization narrated once on stderr.
  2. The canonical form, the unprefixed "rc/<version>", and the bare
     "<version>" spelling all normalize to the same "## Source Branch" value
     in the materialized task README (byte-identical across the three
     spellings).
  3. An irreconcilable value (rc/v8.8.8 for a v9.9.9 decompose) makes the
     materializer exit non-zero naming given vs expected, with NO task
     folders written (fail-closed, no partial materialization).
  4. Feature-mode: a doubled-prefix parent_branch is normalized and the
     create-shared-branch task's "## Source Branch" carries the canonical
     mainline.
  5. The branch_prefix is read from project.cfg — an alternate prefix
     ('x_') fixture proves no hardcoded 'ai_' anywhere in the guard.

Design notes
------------
- pm_materialize.py is invoked as a real subprocess (no mocking) so the
  guard is exercised end-to-end: CLI arg parsing, plan JSON reading, the
  branch-normalization guard itself, and README file writing.
- Each fixture uses its own fresh tmp_path-derived kanban root so task-ID
  sequence numbers are deterministic and comparable across spellings.
- All temp paths use pytest's tmp_path (never bare /tmp).
- Test names describe behavior, never bug IDs or ticket IDs (SOP.md
  anti-pattern 6); the BUG-0037 reference above is doc-only context.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Path constants derived from this file's location.
#
# team/tests/integration/test_cert_branch_normalization_guard.py
#   └── team/tests/integration/
#       └── team/tests/
#           └── team/
#               └── <dev-tree-root>
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_PM_MATERIALIZE = _TEAM_DIR / "pm-agent" / "pm_materialize.py"
_REAL_WORKFLOWS_DIR = _TEAM_DIR / "workflows"

_WORKFLOW_PLUGINS = ["release", "document", "testing-only", "feature"]


# ---------------------------------------------------------------------------
# Fixture tree builder
# ---------------------------------------------------------------------------


def _build_release_kanban_root(
    parent: pathlib.Path,
    project_name: str = "branch_guard_cert",
    branch_prefix: str = "ai_",
    root_name: str = "kanban_branch_guard",
) -> pathlib.Path:
    """Build a minimal kanban root for a release-workflow project.

    Args:
        parent:        Parent temp directory (use pytest's tmp_path).
        project_name:  Name of the single project to create.
        branch_prefix: [project] branch_prefix value written to project.cfg.
        root_name:     Directory name for the kanban root (must be unique
                       within *parent* when a test builds more than one root).

    Returns:
        pathlib.Path — the kanban root.
    """
    root = parent / root_name
    root.mkdir(parents=True, exist_ok=True)

    (root / "kanban.cfg").write_text(
        "[paths]\n\n[chain]\npm_mode = automatic\n\n[wake]\nmax_tasks_per_wake = 1\n",
        encoding="utf-8",
    )
    (root / "projects.cfg").write_text(
        f"[project:{project_name}]\n"
        "priority=1\n"
        "description=branch normalization certification project\n"
        "enabled=true\n",
        encoding="utf-8",
    )

    proj = root / "projects" / project_name
    proj.mkdir(parents=True, exist_ok=True)

    (proj / "project.cfg").write_text(
        f"[project]\n"
        f"project_name = {project_name}\n"
        f"workflow_type = release\n"
        f"dev_tree_path = /dev/null\n"
        f"git_repo_url = git@example.com:{project_name}.git\n"
        f"git_remote_name = origin\n"
        f"branch_prefix = {branch_prefix}\n"
        f"push_to_remote = false\n"
        f"is_self_build = false\n\n"
        f"[versioning]\n"
        f"max_patch = 99\n"
        f"max_minor = 9\n"
        f"max_major = 0\n",
        encoding="utf-8",
    )

    (proj / "release-state.md").write_text(
        "# Release State\n\n"
        "## Active RC\nnone\n\n"
        "## RC Opened At\nnone\n\n"
        "## RC Opened By Task\nnone\n\n"
        "## Last Released\nnone\n",
        encoding="utf-8",
    )

    queues_dir = proj / "tasks" / "queues"
    queues_dir.mkdir(parents=True, exist_ok=True)
    (queues_dir / "plans").mkdir(parents=True, exist_ok=True)
    for agent in ("coder", "pm", "writer", "tester", "cm", "bug", "priority"):
        (queues_dir / f"{agent}_backlog.md").write_text(
            f"# {agent.upper()} Backlog\n\n<!-- Format: - [ ] TASK-ID -->\n",
            encoding="utf-8",
        )

    (proj / "logs").mkdir(parents=True, exist_ok=True)
    (root / "logs" / "agents").mkdir(parents=True, exist_ok=True)
    (root / "logs" / "debug" / "archive").mkdir(parents=True, exist_ok=True)
    (root / "locks").mkdir(parents=True, exist_ok=True)
    (root / "tasks" / "queues" / "plans").mkdir(parents=True, exist_ok=True)

    wf_dir = root / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    for plugin_name in _WORKFLOW_PLUGINS:
        src_plugin = _REAL_WORKFLOWS_DIR / plugin_name
        if src_plugin.is_dir():
            dest_plugin = wf_dir / plugin_name
            if dest_plugin.exists():
                shutil.rmtree(dest_plugin)
            shutil.copytree(src_plugin, dest_plugin)

    return root


# ---------------------------------------------------------------------------
# Plan builders
# ---------------------------------------------------------------------------


def _release_task(source_branch: str) -> dict:
    """Build a single CODER work task for a release-workflow plan."""
    return {
        "sequence": 1,
        "slug": "implement-the-feature",
        "title": "CODER: Implement the feature",
        "role": "CODER",
        "assigned_agent": "CODER",
        "working_directory": "/dev/null",
        "git_repo": "git@example.com:branch_guard_cert.git",
        "source_branch": source_branch,
        "goal": "Implement the certification feature.",
        "inputs": [],
        "context_paths": [],
        "required_output": "Feature implemented.",
        "constraints": [],
        "acceptance_criteria": ["It works."],
        "depends_on": [],
        "notes": "Certification task for branch normalization guard.",
    }


def _build_release_plan(project_name: str, target_version: str, source_branch: str) -> dict:
    """Build a release-workflow plan JSON with a single CODER work task."""
    return {
        "workflow_type": "release",
        "project_name": project_name,
        "target_version": target_version,
        "test_required": "false",
        "human_approval_required": "auto",
        "requirements_path": "none",
        "tasks": [_release_task(source_branch)],
    }


def _feature_plan(project_name: str, parent_branch: str) -> dict:
    """Build a feature-workflow plan JSON with a single CODER work task."""
    return {
        "workflow_type": "feature",
        "project_name": project_name,
        "source_branch": "feature/shared-work",
        "parent_branch": parent_branch,
        "test_required": "false",
        "human_approval_required": "auto",
        "requirements_path": "none",
        "tasks": [
            {
                "sequence": 1,
                "slug": "implement-the-feature",
                "title": "CODER: Implement the feature",
                "role": "CODER",
                "assigned_agent": "CODER",
                "working_directory": "/dev/null",
                "git_repo": "git@example.com:branch_guard_cert.git",
                "source_branch": "none",
                "goal": "Implement the certification feature.",
                "inputs": [],
                "context_paths": [],
                "required_output": "Feature implemented.",
                "constraints": [],
                "acceptance_criteria": ["It works."],
                "depends_on": [],
                "notes": "Certification task for parent_branch normalization guard.",
            },
        ],
    }


# ---------------------------------------------------------------------------
# pm_materialize.py subprocess runner
# ---------------------------------------------------------------------------


def _run_pm_materialize(
    plan_file: pathlib.Path,
    kanban_root: pathlib.Path,
    project_root: pathlib.Path,
) -> subprocess.CompletedProcess:
    """Invoke pm_materialize.py as a subprocess against a temp kanban tree."""
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["PGAI_PROJECT_ROOT"] = str(project_root)
    env.pop("PGAI_TASKS_DIR", None)
    env.pop("PGAI_REQUIREMENTS_DIR", None)
    env.pop("PGAI_DEV_TREE_PATH", None)

    cmd = [
        "python3",
        str(_PM_MATERIALIZE),
        str(plan_file),
        "--team-root",
        str(project_root),
        "--owner",
        "CLAUDE",
    ]

    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(kanban_root),
        timeout=60,
    )


def _read_source_branch_field(readme_path: pathlib.Path) -> str:
    """Extract the '## Source Branch' field value from a task README."""
    text = readme_path.read_text(encoding="utf-8")
    match = re.search(r"## Source Branch\n(.+?)\n", text)
    assert match, f"'## Source Branch' field not found in {readme_path}"
    return match.group(1).strip()


def _find_task_readme(
    tasks_root: pathlib.Path, role_prefix: str, name_contains: str = "",
) -> pathlib.Path:
    """Find the single task README under tasks_root for the given role prefix.

    name_contains: optional substring the folder name must also contain, to
    disambiguate when a plan produces more than one task folder with the
    same role prefix (e.g. feature-mode's create-shared-branch task and the
    work task are both role=CODER).
    """
    dirs = [
        d for d in tasks_root.iterdir()
        if d.is_dir() and d.name.startswith(role_prefix)
        and (not name_contains or name_contains in d.name)
    ]
    assert len(dirs) == 1, (
        f"Expected exactly one {role_prefix}*{name_contains}* task folder; found: "
        f"{[d.name for d in dirs]}"
    )
    return dirs[0] / "README.md"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestReleaseSourceBranchNormalization:
    """Release-workflow: per-task source_branch is advisory, normalized against
    the materializer-computed canonical RC branch."""

    def test_doubled_prefix_source_branch_normalizes_with_narration(
        self, tmp_path: pathlib.Path,
    ) -> None:
        """A doubled-prefix source_branch normalizes to the canonical RC branch.

        Acceptance criterion 1: plan with source_branch 'ai_rc/ai_v9.9.9' for a
        v9.9.9 release decompose produces a task README carrying 'ai_rc/v9.9.9',
        with normalization narrated on stderr.
        """
        root = _build_release_kanban_root(tmp_path, branch_prefix="ai_")
        proj = root / "projects" / "branch_guard_cert"
        plan = _build_release_plan("branch_guard_cert", "v9.9.9", "ai_rc/ai_v9.9.9")
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")

        result = _run_pm_materialize(plan_file, root, proj)
        assert result.returncode == 0, (
            f"Expected success; got {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        readme = _find_task_readme(proj / "tasks", "CODER-")
        assert _read_source_branch_field(readme) == "ai_rc/v9.9.9"

        assert "normalized source_branch" in result.stderr
        assert "ai_rc/ai_v9.9.9" in result.stderr
        assert "ai_rc/v9.9.9" in result.stderr

    def test_accepted_spellings_produce_byte_identical_canonical_branch(
        self, tmp_path: pathlib.Path,
    ) -> None:
        """canonical, unprefixed 'rc/<version>', and bare '<version>' all
        normalize to the same '## Source Branch' value.

        Acceptance criterion 2: the three accepted spellings all resolve to
        the identical canonical branch in the materialized task output.
        """
        spellings = ["ai_rc/v9.9.9", "rc/v9.9.9", "v9.9.9"]
        resolved_values = []

        for i, spelling in enumerate(spellings):
            root = _build_release_kanban_root(
                tmp_path, branch_prefix="ai_", root_name=f"kanban_spelling_{i}"
            )
            proj = root / "projects" / "branch_guard_cert"
            plan = _build_release_plan("branch_guard_cert", "v9.9.9", spelling)
            plan_file = tmp_path / f"plan_{i}.json"
            plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")

            result = _run_pm_materialize(plan_file, root, proj)
            assert result.returncode == 0, (
                f"Spelling {spelling!r} failed to materialize; "
                f"got {result.returncode}.\nstderr: {result.stderr}"
            )
            readme = _find_task_readme(proj / "tasks", "CODER-")
            resolved_values.append(_read_source_branch_field(readme))

        assert resolved_values == ["ai_rc/v9.9.9"] * 3, (
            f"Expected byte-identical canonical branch across all three "
            f"spellings; got: {resolved_values}"
        )

    def test_irreconcilable_value_fails_closed_with_no_tasks_written(
        self, tmp_path: pathlib.Path,
    ) -> None:
        """An irreconcilable source_branch exits non-zero with zero task folders.

        Acceptance criterion 3: 'rc/v8.8.8' for a v9.9.9 decompose is refused —
        materializer exits non-zero naming given vs expected, no tasks written.
        """
        root = _build_release_kanban_root(tmp_path, branch_prefix="ai_")
        proj = root / "projects" / "branch_guard_cert"
        plan = _build_release_plan("branch_guard_cert", "v9.9.9", "rc/v8.8.8")
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")

        result = _run_pm_materialize(plan_file, root, proj)
        assert result.returncode != 0, (
            f"Expected non-zero exit (guard fired); got {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Diagnostic must name given vs expected.
        assert "rc/v8.8.8" in result.stderr
        assert "ai_rc/v9.9.9" in result.stderr

        # No task folders written — fail-closed, no partial materialization.
        tasks_root = proj / "tasks"
        task_dirs = [
            d for d in tasks_root.iterdir()
            if d.is_dir() and d.name != "queues"
        ] if tasks_root.is_dir() else []
        assert len(task_dirs) == 0, (
            f"Expected zero task folders after guard fired; "
            f"found: {[d.name for d in task_dirs]}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_garbage_source_branch_fails_closed(
        self, tmp_path: pathlib.Path,
    ) -> None:
        """A garbage, non-branch-shaped source_branch string is refused, not accepted."""
        root = _build_release_kanban_root(tmp_path, branch_prefix="ai_")
        proj = root / "projects" / "branch_guard_cert"
        plan = _build_release_plan("branch_guard_cert", "v9.9.9", "totally-not-a-branch")
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")

        result = _run_pm_materialize(plan_file, root, proj)
        assert result.returncode != 0

        tasks_root = proj / "tasks"
        task_dirs = [
            d for d in tasks_root.iterdir()
            if d.is_dir() and d.name != "queues"
        ] if tasks_root.is_dir() else []
        assert len(task_dirs) == 0

    def test_golden_path_correct_name_produces_zero_narration(
        self, tmp_path: pathlib.Path,
    ) -> None:
        """A plan already emitting the canonical branch name produces zero narration.

        Golden-path constraint: flagship-lane plans emitting correct names
        produce unchanged output with zero narration.
        """
        root = _build_release_kanban_root(tmp_path, branch_prefix="ai_")
        proj = root / "projects" / "branch_guard_cert"
        plan = _build_release_plan("branch_guard_cert", "v9.9.9", "ai_rc/v9.9.9")
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")

        result = _run_pm_materialize(plan_file, root, proj)
        assert result.returncode == 0

        readme = _find_task_readme(proj / "tasks", "CODER-")
        assert _read_source_branch_field(readme) == "ai_rc/v9.9.9"
        assert "BRANCH NORMALIZATION" not in result.stderr, (
            f"Expected zero narration on the golden path; stderr: {result.stderr}"
        )


class TestFeatureParentBranchNormalization:
    """Feature-workflow: parent_branch is advisory, normalized against the
    materializer-computed canonical mainline branch."""

    def test_doubled_prefix_parent_branch_normalizes_to_canonical_mainline(
        self, tmp_path: pathlib.Path,
    ) -> None:
        """A doubled-prefix parent_branch normalizes; the mainline is canonical.

        Acceptance criterion 4: feature-mode fixture with a doubled-prefix
        parent_branch — the create-shared-branch task's source is the
        canonical (correctly prefixed) mainline.
        """
        root = _build_release_kanban_root(
            tmp_path, branch_prefix="ai_", root_name="kanban_feature_guard"
        )
        proj = root / "projects" / "branch_guard_cert"
        plan = _feature_plan("branch_guard_cert", "ai_ai_main")
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")

        result = _run_pm_materialize(plan_file, root, proj)
        assert result.returncode == 0, (
            f"Expected success; got {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        readme = _find_task_readme(proj / "tasks", "CODER-", name_contains="create-shared-branch")
        assert _read_source_branch_field(readme) == "ai_main"
        assert "normalized parent_branch" in result.stderr
        assert "ai_ai_main" in result.stderr

    def test_irreconcilable_parent_branch_fails_closed(
        self, tmp_path: pathlib.Path,
    ) -> None:
        """An irreconcilable parent_branch exits non-zero with zero task folders written."""
        root = _build_release_kanban_root(
            tmp_path, branch_prefix="ai_", root_name="kanban_feature_guard_fail"
        )
        proj = root / "projects" / "branch_guard_cert"
        plan = _feature_plan("branch_guard_cert", "develop")
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")

        result = _run_pm_materialize(plan_file, root, proj)
        assert result.returncode != 0
        assert "develop" in result.stderr
        assert "ai_main" in result.stderr

        tasks_root = proj / "tasks"
        task_dirs = [
            d for d in tasks_root.iterdir()
            if d.is_dir() and d.name != "queues"
        ] if tasks_root.is_dir() else []
        assert len(task_dirs) == 0


class TestAlternatePrefixNoHardcoding:
    """Proves branch_prefix is read from project.cfg — no hardcoded 'ai_'."""

    def test_alternate_prefix_produces_analogous_canonical_branch(
        self, tmp_path: pathlib.Path,
    ) -> None:
        """Acceptance criterion 5: an alternate prefix ('x_') proves no 'ai_'
        hardcoding anywhere in the branch-normalization guard."""
        root = _build_release_kanban_root(
            tmp_path, branch_prefix="x_", root_name="kanban_alt_prefix"
        )
        proj = root / "projects" / "branch_guard_cert"
        plan = _build_release_plan("branch_guard_cert", "v9.9.9", "x_rc/x_v9.9.9")
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")

        result = _run_pm_materialize(plan_file, root, proj)
        assert result.returncode == 0, (
            f"Expected success; got {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        readme = _find_task_readme(proj / "tasks", "CODER-")
        resolved = _read_source_branch_field(readme)
        assert resolved == "x_rc/v9.9.9"
        assert "ai_" not in resolved
