"""
test_cert_source_ref_guard_ordering.py
=======================================
Certification fixture: guard-ordering proof for the source-ref fail-closed
validation step in pm_materialize.py.

This test proves an earlier defect acceptance criterion #3:

    A TESTER ticket appended to the plan AFTER normal assembly with
    source_branch='none' (and no plan-level source_branch to propagate)
    is refused by _validate_source_ref_for_worktree_roles with
    sys.exit(1) BEFORE any task folder is created on disk.

The guard fires at Step 4b in pm_materialize.py, immediately after
source-ref propagation (Step 4a) and BEFORE create_task_folder.  When
the guard fires, the output directory must be empty of TESTER task
folders — proving the guard is in the correct position in the pipeline.

Design notes
------------
- pm_materialize.py is invoked as a real subprocess (no mocking) so the
  guard-ordering proof exercises the full pipeline path from plan JSON
  to sys.exit(1) decision.
- "No task folders on disk" is verified by checking tasks_root after
  the subprocess exits non-zero.
- All temp paths use pytest's tmp_path (never bare /tmp).
- Test names describe behavior, never bug IDs or ticket IDs (SOP.md anti-pattern 6).
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
# team/tests/integration/test_cert_source_ref_guard_ordering.py
#   └── team/tests/integration/
#       └── team/tests/
#           └── team/
#               └── <dev-tree-root>
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_PM_MATERIALIZE = _TEAM_DIR / "pm-agent" / "pm_materialize.py"
_REAL_WORKFLOWS_DIR = _TEAM_DIR / "workflows"

_WORKFLOW_PLUGINS = ["release", "document", "testing-only"]


# ---------------------------------------------------------------------------
# Fixture tree builder (minimal; shared with other cert fixtures)
# ---------------------------------------------------------------------------


def _build_testing_only_kanban_root(
    parent: pathlib.Path,
    project_name: str = "guard_cert",
    source_branch: str = "",
) -> pathlib.Path:
    """Build a minimal kanban root for testing the source-ref guard.

    source_branch may be empty to simulate a plan with no resolvable
    source ref — the scenario that causes the guard to fire.

    Args:
        parent:        Parent temp directory (use pytest's tmp_path).
        project_name:  Name of the single project to create.
        source_branch: Source branch to record in the requirements doc.
                       Empty string produces a requirements doc without
                       a ## Source Branch field.

    Returns:
        pathlib.Path — the kanban root.
    """
    root = parent / "kanban_guard"
    root.mkdir(parents=True, exist_ok=True)

    (root / "kanban.cfg").write_text(
        "[paths]\n\n[chain]\npm_mode = automatic\n\n[wake]\nmax_tasks_per_wake = 1\n",
        encoding="utf-8",
    )
    (root / "projects.cfg").write_text(
        f"[project:{project_name}]\n"
        "priority=1\n"
        "description=guard ordering certification project\n"
        "enabled=true\n",
        encoding="utf-8",
    )

    proj = root / "projects" / project_name
    proj.mkdir(parents=True, exist_ok=True)

    (proj / "project.cfg").write_text(
        f"[project]\n"
        f"project_name = {project_name}\n"
        f"dev_tree_path = /dev/null\n"
        f"git_repo_url = git@example.com:{project_name}.git\n"
        f"git_remote_name = origin\n"
        f"workflow_type = testing-only\n"
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
        "## RC Opened By Task\nnone\n",
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

    req_dir = proj / "requirements"
    req_dir.mkdir(parents=True, exist_ok=True)
    req_file = req_dir / "v1.0.0-guard-ordering-run.md"

    # Build the requirements doc; omit ## Source Branch field when
    # source_branch is empty so the materializer has no plan-level ref to use.
    source_branch_section = (
        f"## Source Branch\n{source_branch}\n\n" if source_branch else ""
    )
    req_file.write_text(
        "# v20260714-guard-ordering-run: guard ordering certification run\n\n"
        "## Status\nrunning\n\n"
        "## Target Version\nv20260714-guard-ordering-run\n\n"
        "## Workflow Type\ntesting-only\n\n"
        + source_branch_section
        + "## PM Task\nnone\n\n"
        "## Summary\n"
        "Certification requirements doc for source-ref guard ordering test.\n",
        encoding="utf-8",
    )

    return root


# ---------------------------------------------------------------------------
# Plan builders
# ---------------------------------------------------------------------------


def _build_no_source_branch_plan(
    project_name: str,
    requirements_path: str,
) -> dict:
    """Build a plan JSON with a TESTER task and NO plan-level source_branch.

    The TESTER task also has source_branch='none'.  After propagation (which
    is a no-op with no plan-level value), the guard must refuse with
    sys.exit(1) because the TESTER has no resolvable source ref.

    Args:
        project_name:      Project name.
        requirements_path: Absolute path to the requirements doc.

    Returns:
        dict — plan JSON with no plan-level source_branch.
    """
    tasks = [
        {
            "sequence": 1,
            "slug": "verify-the-implementation",
            "title": "TESTER: Verify the implementation",
            "role": "TESTER",
            "assigned_agent": "TESTER",
            "working_directory": "none",
            "git_repo": "none",
            "source_branch": "none",
            "goal": "Verify the certification guard-ordering run.",
            "inputs": [requirements_path],
            "context_paths": [],
            "required_output": "Verification pass or named failure.",
            "constraints": ["tester_operation: verify-and-report"],
            "acceptance_criteria": ["All acceptance criteria pass."],
            "depends_on": [],
            "notes": "TESTER ticket with no source_branch — triggers the guard.",
        },
    ]

    # No 'source_branch' key at the plan level — this is the guard scenario.
    return {
        "workflow_type": "testing-only",
        "project_name": project_name,
        "target_version": "none",
        "test_required": "true",
        "human_approval_required": "auto",
        "requirements_path": requirements_path,
        "tasks": tasks,
    }


# ---------------------------------------------------------------------------
# pm_materialize.py subprocess runner
# ---------------------------------------------------------------------------


def _run_pm_materialize(
    plan_file: pathlib.Path,
    kanban_root: pathlib.Path,
    project_root: pathlib.Path,
    *,
    requirements_path: str = "",
) -> subprocess.CompletedProcess:
    """Invoke pm_materialize.py as a subprocess against a temp kanban tree.

    Args:
        plan_file:          Path to the plan JSON file.
        kanban_root:        Kanban root for the temp tree.
        project_root:       Project root (projects/<name>/ inside kanban_root).
        requirements_path:  Optional --requirements-path CLI arg.

    Returns:
        subprocess.CompletedProcess with returncode, stdout, stderr.
    """
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
    if requirements_path:
        cmd += ["--requirements-path", requirements_path]

    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(kanban_root),
        timeout=60,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSourceRefGuardOrdering:
    """Guard-ordering proof: source-ref validation fires before task folder creation.

    Proves that pm_materialize.py's Step 4b guard fires with sys.exit(1) when
    a TESTER ticket has no resolvable source_branch — and that no task folders
    are created on disk when the guard fires.
    """

    def test_guard_fires_before_task_folder_creation(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """pm_materialize.py exits non-zero before creating any task folder.

        When a TESTER ticket has source_branch='none' and the plan carries no
        plan-level source_branch (so propagation is a no-op), the
        _validate_source_ref_for_worktree_roles guard must refuse with a
        non-zero exit code BEFORE any task folder is written to disk.

        This is the guard-ordering proof for an earlier defect acceptance criterion #3:
        the guard fires in the correct position in the pipeline (Step 4b),
        before create_task_folder (Step 5+).
        """
        root = _build_testing_only_kanban_root(
            tmp_path,
            project_name="guard_cert",
            source_branch="",  # no source_branch in requirements doc
        )
        proj = root / "projects" / "guard_cert"
        req_path = str(proj / "requirements" / "v1.0.0-guard-ordering-run.md")

        plan = _build_no_source_branch_plan(
            project_name="guard_cert",
            requirements_path=req_path,
        )
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")

        result = _run_pm_materialize(
            plan_file, root, proj, requirements_path=req_path
        )

        # Guard must refuse with non-zero exit.
        assert result.returncode != 0, (
            f"Expected pm_materialize.py to exit non-zero (guard fired); "
            f"got exit code {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert result.returncode == 1, (
            f"Expected exit code 1 (sys.exit(1) from guard); "
            f"got {result.returncode}.\n"
            f"stderr: {result.stderr}"
        )

        # No task folders must exist on disk — the guard fired before any
        # folder was created.
        tasks_root = proj / "tasks"
        tester_dirs = [
            d for d in tasks_root.iterdir()
            if d.is_dir() and d.name.startswith("TESTER-")
        ] if tasks_root.is_dir() else []

        assert len(tester_dirs) == 0, (
            f"Expected zero TESTER task folders on disk after guard fired; "
            f"found: {[d.name for d in tester_dirs]}\n"
            f"Guard did not fire before folder creation.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_guard_diagnostic_names_offending_task(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Guard diagnostic names the TESTER task that triggered the failure.

        The stderr output from pm_materialize.py must contain enough context
        for an operator to identify which task lacks a source_branch.
        The task slug or a recognisable identifier from the task must appear
        in the diagnostic so the failure is actionable without re-reading logs.
        """
        root = _build_testing_only_kanban_root(
            tmp_path,
            project_name="guard_cert",
            source_branch="",
        )
        proj = root / "projects" / "guard_cert"
        req_path = str(proj / "requirements" / "v1.0.0-guard-ordering-run.md")

        plan = _build_no_source_branch_plan(
            project_name="guard_cert",
            requirements_path=req_path,
        )
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")

        result = _run_pm_materialize(
            plan_file, root, proj, requirements_path=req_path
        )
        assert result.returncode != 0

        # Diagnostic must mention source_branch or Source Branch.
        assert "Source Branch" in result.stderr or \
               "source_branch" in result.stderr.lower(), (
            f"Expected source_branch mentioned in guard diagnostic.\n"
            f"stderr: {result.stderr}"
        )

        # Diagnostic must name the TESTER role or the slug so the operator
        # can identify the offending task without additional lookups.
        assert "TESTER" in result.stderr or "verify-the-implementation" in result.stderr, (
            f"Expected the offending TESTER task to be identified in stderr.\n"
            f"stderr: {result.stderr}"
        )

    def test_guard_does_not_fire_when_source_branch_is_resolvable(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Guard passes and materialization succeeds when source_branch is resolvable.

        When the plan carries a plan-level source_branch that propagation can
        supply to all tickets, the guard must pass and pm_materialize.py must
        exit 0 with task folders created normally.
        """
        source_branch = "rc/v1.22.5"
        root = _build_testing_only_kanban_root(
            tmp_path,
            project_name="guard_cert",
            source_branch=source_branch,
        )
        proj = root / "projects" / "guard_cert"
        req_path = str(proj / "requirements" / "v1.0.0-guard-ordering-run.md")

        # Plan with a plan-level source_branch: propagation fills the ticket;
        # guard passes; folders are created.
        plan = {
            "workflow_type": "testing-only",
            "project_name": "guard_cert",
            "source_branch": source_branch,
            "target_version": "none",
            "test_required": "true",
            "human_approval_required": "auto",
            "requirements_path": req_path,
            "tasks": [
                {
                    "sequence": 1,
                    "slug": "verify-the-implementation",
                    "title": "TESTER: Verify the implementation",
                    "role": "TESTER",
                    "assigned_agent": "TESTER",
                    "working_directory": "none",
                    "git_repo": "none",
                    "source_branch": "none",  # will be filled by propagation
                    "goal": "Verify the certification guard-ordering run.",
                    "inputs": [req_path],
                    "context_paths": [],
                    "required_output": "Verification pass or named failure.",
                    "constraints": ["tester_operation: verify-and-report"],
                    "acceptance_criteria": ["All acceptance criteria pass."],
                    "depends_on": [],
                    "notes": "Source_branch will be filled by propagation.",
                },
            ],
        }
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")

        result = _run_pm_materialize(
            plan_file, root, proj, requirements_path=req_path
        )
        assert result.returncode == 0, (
            f"Expected pm_materialize.py to exit 0 when source_branch is "
            f"resolvable via propagation; got {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Task folders must be created when the guard passes.
        tasks_root = proj / "tasks"
        tester_dirs = [
            d for d in tasks_root.iterdir()
            if d.is_dir() and d.name.startswith("TESTER-")
        ]
        assert len(tester_dirs) >= 1, (
            f"Expected at least 1 TESTER task folder after successful materialization; "
            f"found: {[d.name for d in tester_dirs]}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
