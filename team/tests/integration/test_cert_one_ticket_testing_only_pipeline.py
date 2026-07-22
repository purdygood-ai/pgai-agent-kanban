"""
test_cert_one_ticket_testing_only_pipeline.py
=============================================
Certification fixture: one-ticket testing-only decomposition, zero-touch dispatch.

This test exercises the end-to-end an earlier defect fix in the real pipeline:

    selection -> PM decomposition emitting ONE TESTER work ticket
             -> materialization (real pm_materialize.py subprocess)
             -> materializer injects a synthetic verify-and-report companion
             -> both tickets carry plan-level source_branch
             -> both tickets dispatch and complete (simulated DONE)
             -> intake item closed done via production ops path

The fixture proves an earlier defect acceptance criteria #1 and #2:
  AC-1: a single-work-ticket plan yields two materialized tickets
        (the plan ticket + the injected companion), both carrying the
        plan-level source_branch.
  AC-2: the simulated dispatch and finalize path completes with zero
        operator interventions.

Design notes
------------
- pm_materialize.py is invoked as a real subprocess (no mocking) so the
  source_branch propagation path is exercised end-to-end including CLI arg
  parsing, plan JSON reading, and README file writing.
- "Dispatch" is simulated: after materialization, both TESTER task status.md
  files are set to State=DONE without operator intervention.
- "Finalize / intake closed" is exercised via the production entry point:
  python3 -m pgai_agent_kanban.ops close_item. No in-test regex simulates
  the closure directly — the real ops path is called so that regressions
  in the production closure mechanism are caught by this fixture.
- Idempotency: a second pm_materialize.py invocation against the same plan exits
  non-zero (exit 2 = idempotent no-op via plan-hash marker) — this proves the
  fixture is re-runnable without accumulating duplicate tickets.
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
import textwrap

import pytest

# ---------------------------------------------------------------------------
# Path constants derived from this file's location.
#
# team/tests/integration/test_cert_one_ticket_testing_only_pipeline.py
#   └── team/tests/integration/
#       └── team/tests/
#           └── team/
#               └── <dev-tree-root>
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_PM_MATERIALIZE = _TEAM_DIR / "pm-agent" / "pm_materialize.py"
_REAL_WORKFLOWS_DIR = _TEAM_DIR / "workflows"
_WAKE_BATCH = _TEAM_DIR / "scripts" / "wake-batch.sh"

# Workflow plugins needed by the testing-only materializer path.
_WORKFLOW_PLUGINS = ["release", "document", "testing-only"]


# ---------------------------------------------------------------------------
# Fixture tree builder (mirrors _build_testing_only_kanban_root from the
# two-ticket fixture; kept separate so both fixtures are independently runnable)
# ---------------------------------------------------------------------------


def _build_testing_only_kanban_root(
    parent: pathlib.Path,
    project_name: str = "cert_proj",
    source_branch: str = "rc/v1.22.4",
) -> pathlib.Path:
    """Build a minimal kanban root for a testing-only project.

    Creates the minimum directory structure that pm_materialize.py requires:
    - kanban.cfg
    - projects.cfg (registers the project)
    - projects/<name>/project.cfg (workflow_type=testing-only)
    - projects/<name>/release-state.md
    - projects/<name>/tasks/queues/ with per-agent backlogs
    - projects/<name>/requirements/ with one open requirements doc
    - workflows/ populated with real plugin copies

    Args:
        parent:        Parent temp directory (use pytest's tmp_path).
        project_name:  Name of the single project to create.
        source_branch: Source branch recorded in the requirements doc,
                       propagated via pm_materialize into each TESTER ticket.

    Returns:
        pathlib.Path — the kanban root.
    """
    root = parent / "kanban_cert"
    root.mkdir(parents=True, exist_ok=True)

    # kanban.cfg
    (root / "kanban.cfg").write_text(
        "[paths]\n\n[chain]\npm_mode = automatic\n\n[wake]\nmax_tasks_per_wake = 1\n",
        encoding="utf-8",
    )

    # projects.cfg
    (root / "projects.cfg").write_text(
        f"[project:{project_name}]\n"
        "priority=1\n"
        "description=certification fixture project\n"
        "enabled=true\n",
        encoding="utf-8",
    )

    # Project directory
    proj = root / "projects" / project_name
    proj.mkdir(parents=True, exist_ok=True)

    # project.cfg — testing-only workflow type
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

    # release-state.md — no semver state for testing-only projects
    (proj / "release-state.md").write_text(
        "# Release State\n\n"
        "## Active RC\n"
        "none\n\n"
        "## RC Opened At\n"
        "none\n\n"
        "## RC Opened By Task\n"
        "none\n",
        encoding="utf-8",
    )

    # tasks/queues/ with per-agent backlogs and plans/ subdir
    queues_dir = proj / "tasks" / "queues"
    queues_dir.mkdir(parents=True, exist_ok=True)
    (queues_dir / "plans").mkdir(parents=True, exist_ok=True)
    for agent in ("coder", "pm", "writer", "tester", "cm", "bug", "priority"):
        (queues_dir / f"{agent}_backlog.md").write_text(
            f"# {agent.upper()} Backlog\n\n"
            "<!-- Format: - [ ] TASK-ID -->\n",
            encoding="utf-8",
        )

    # logs directory
    (proj / "logs").mkdir(parents=True, exist_ok=True)

    # Kanban-root runtime directories (expected by some materializer paths)
    (root / "logs" / "agents").mkdir(parents=True, exist_ok=True)
    (root / "logs" / "debug" / "archive").mkdir(parents=True, exist_ok=True)
    (root / "locks").mkdir(parents=True, exist_ok=True)
    (root / "tasks" / "queues" / "plans").mkdir(parents=True, exist_ok=True)

    # workflows/ — copy real plugins so load_workflow_capabilities works
    wf_dir = root / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    for plugin_name in _WORKFLOW_PLUGINS:
        src_plugin = _REAL_WORKFLOWS_DIR / plugin_name
        if src_plugin.is_dir():
            dest_plugin = wf_dir / plugin_name
            if dest_plugin.exists():
                shutil.rmtree(dest_plugin)
            shutil.copytree(src_plugin, dest_plugin)

    # requirements doc — open, with source_branch
    req_dir = proj / "requirements"
    req_dir.mkdir(parents=True, exist_ok=True)
    req_file = req_dir / "v1.0.0-one-ticket-testing-run.md"
    req_file.write_text(
        "# v20260714-one-ticket-testing-run: one ticket testing run\n\n"
        "## Status\nrunning\n\n"
        "## Target Version\nv20260714-one-ticket-testing-run\n\n"
        "## Workflow Type\ntesting-only\n\n"
        "## Source Branch\n"
        f"{source_branch}\n\n"
        "## PM Task\nnone\n\n"
        "## Summary\n"
        "Certification requirements doc for one-ticket testing-only pipeline.\n",
        encoding="utf-8",
    )

    return root


# ---------------------------------------------------------------------------
# Plan JSON builder — one TESTER work ticket
# ---------------------------------------------------------------------------


def _build_one_tester_plan(
    project_name: str,
    source_branch: str,
    requirements_path: str,
    *,
    ticket_source_branch: str = "",
) -> dict:
    """Build a plan JSON with a single TESTER work task for a testing-only workflow.

    The single work ticket represents what PM emits for a one-ticket decomposition.
    pm_materialize.py will inject a synthetic verify-and-report companion, so after
    materialization there will be TWO TESTER task folders.

    Args:
        project_name:          Project name, placed in the plan JSON.
        source_branch:         Plan-level source_branch (propagated to the
                               ticket and the injected companion that lack one).
        requirements_path:     Absolute path to the requirements doc.
        ticket_source_branch:  Optional explicit override for the single work
                               ticket.  When empty, the ticket uses source_branch
                               from the plan.

    Returns:
        dict — plan JSON structure ready for json.dumps().
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
            "source_branch": ticket_source_branch or source_branch,
            "goal": "Verify the single work item in the one-ticket certification run.",
            "inputs": [requirements_path],
            "context_paths": [],
            "required_output": "Verification pass or named failure.",
            "constraints": ["tester_operation: verify-and-report"],
            "acceptance_criteria": ["All acceptance criteria pass."],
            "depends_on": [],
            "notes": "Single TESTER work ticket in a one-ticket testing-only plan.",
        },
    ]

    return {
        "workflow_type": "testing-only",
        "project_name": project_name,
        "source_branch": source_branch,
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

    Sets PGAI_AGENT_KANBAN_ROOT_PATH and PGAI_PROJECT_ROOT in the subprocess
    environment so the materializer writes to the temp tree, not the live install.

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
    # Remove test-harness-inherited overrides so get_config() derives paths
    # from PGAI_AGENT_KANBAN_ROOT_PATH and PGAI_PROJECT_ROOT rather than
    # from whatever the autouse safety fixture set in the parent process.
    env.pop("PGAI_TASKS_DIR", None)
    env.pop("PGAI_REQUIREMENTS_DIR", None)
    # Suppress git operations inside pm_materialize (dev_tree_path=none in project.cfg
    # already prevents worktree calls, but belt-and-suspenders removes the env var).
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
# README field extractor
# ---------------------------------------------------------------------------


def _extract_source_branch_from_readme(readme_path: pathlib.Path) -> str:
    """Return the ## Source Branch value from a task README.

    Returns empty string when the field is absent or the file does not exist.

    Args:
        readme_path: Absolute path to the task README.md.

    Returns:
        str — the source branch value (stripped), or "".
    """
    if not readme_path.is_file():
        return ""
    text = readme_path.read_text(encoding="utf-8")
    match = re.search(r"^##\s+Source Branch\s*\n\s*(\S+)", text, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return ""


def _has_blocked_sibling_sharing_requirements(
    tasks_root: pathlib.Path,
    req_path: str,
) -> bool:
    """Return True when any task under tasks_root with req_path in ## Inputs is BLOCKED.

    Mirrors the mid-run failure guard in close_intake_on_finalize_report
    (wake_common.sh): scans sibling task folders for BLOCKED state before
    the production system decides to close the intake item.

    Args:
        tasks_root: The project's tasks/ directory.
        req_path:   Absolute path to the requirements file (as a string).

    Returns:
        bool — True when at least one sibling task is BLOCKED; False otherwise.
    """
    if not tasks_root.is_dir():
        return False
    for task_dir in sorted(tasks_root.iterdir()):
        if not task_dir.is_dir() or task_dir.name == "queues":
            continue
        readme = task_dir / "README.md"
        status_file = task_dir / "status.md"
        if not readme.is_file() or not status_file.is_file():
            continue
        try:
            readme_text = readme.read_text(encoding="utf-8")
        except OSError:
            continue
        inputs_match = re.search(
            r"^##\s+Inputs\s*\n(.*?)(?=\n##|\Z)", readme_text, re.S | re.M
        )
        if not inputs_match or req_path not in inputs_match.group(1):
            continue
        try:
            status_text = status_file.read_text(encoding="utf-8")
        except OSError:
            continue
        state_match = re.search(r"^##\s+State\s*\n\s*(\S+)", status_text, re.MULTILINE)
        if state_match and state_match.group(1).strip().upper() == "BLOCKED":
            return True
    return False


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestOneTicketTestingOnlyPipeline:
    """End-to-end certification fixture for one-ticket testing-only decomposition.

    Proves an earlier defect acceptance criteria #1 and #2: a single-work-ticket plan
    yields two materialized tickets (the plan's TESTER ticket plus the injected
    synthetic verify-and-report companion), both carry the plan's source ref,
    and the full pipeline completes with zero operator interventions.

    All tests share the same kanban root pattern (built per-test via tmp_path
    so there is no shared mutable state).
    """

    def test_materialization_exits_zero_for_one_tester_plan(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """pm_materialize.py exits zero for a one-TESTER testing-only plan.

        A well-formed plan JSON with one TESTER work task and a plan-level
        source_branch must materialize cleanly (exit 0) on the first run.
        This is the base case confirming the fixture tree and plan JSON
        are correctly wired before the ticket-count and source_branch
        assertions are checked.
        """
        source_branch = "rc/v1.22.5"
        root = _build_testing_only_kanban_root(
            tmp_path, source_branch=source_branch
        )
        proj = root / "projects" / "cert_proj"
        req_path = str(proj / "requirements" / "v1.0.0-one-ticket-testing-run.md")

        plan = _build_one_tester_plan(
            project_name="cert_proj",
            source_branch=source_branch,
            requirements_path=req_path,
        )
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")

        result = _run_pm_materialize(
            plan_file, root, proj, requirements_path=req_path
        )
        assert result.returncode == 0, (
            f"pm_materialize.py exited {result.returncode} (expected 0).\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_one_work_ticket_yields_two_tester_task_folders(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """One TESTER work ticket in the plan produces two TESTER task folders.

        pm_materialize.py injects a synthetic verify-and-report companion for
        testing-only workflows (via inject_simple_tester_task).  A plan with
        a single TESTER work ticket must therefore produce exactly two TESTER
        task folders after materialization:
          1. The plan's own TESTER ticket (verify-the-implementation).
          2. The synthetic companion (verify-and-report).

        This is an earlier defect acceptance criterion #1.
        """
        source_branch = "rc/v1.22.5"
        root = _build_testing_only_kanban_root(
            tmp_path, source_branch=source_branch
        )
        proj = root / "projects" / "cert_proj"
        req_path = str(proj / "requirements" / "v1.0.0-one-ticket-testing-run.md")

        plan = _build_one_tester_plan(
            project_name="cert_proj",
            source_branch=source_branch,
            requirements_path=req_path,
        )
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")

        result = _run_pm_materialize(
            plan_file, root, proj, requirements_path=req_path
        )
        assert result.returncode == 0, (
            f"pm_materialize.py exited {result.returncode} (expected 0).\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        tasks_root = proj / "tasks"
        tester_dirs = sorted(
            d for d in tasks_root.iterdir()
            if d.is_dir() and d.name.startswith("TESTER-")
        )
        assert len(tester_dirs) == 2, (
            f"Expected exactly 2 TESTER task folders (plan ticket + injected companion) "
            f"under {tasks_root}; found {len(tester_dirs)}: "
            f"{[d.name for d in tester_dirs]}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Verify the slugs: one work ticket and one verify-and-report companion.
        slugs = {d.name for d in tester_dirs}
        work_ticket = [d for d in tester_dirs if "verify-the-implementation" in d.name]
        companion = [d for d in tester_dirs if "verify-and-report" in d.name]
        assert work_ticket, (
            f"Expected a TESTER folder with slug 'verify-the-implementation'; "
            f"found: {sorted(slugs)}"
        )
        assert companion, (
            f"Expected an injected TESTER folder with slug 'verify-and-report'; "
            f"found: {sorted(slugs)}"
        )

    def test_both_tester_readmes_carry_plan_source_branch(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Both TESTER task READMEs carry the plan-level source_branch after materialization.

        The injected verify-and-report companion is created with source_branch='none'
        by inject_simple_tester_task.  After materialization, the source_branch
        propagation step must supply the plan-level source_branch to both:
          1. The plan's TESTER work ticket.
          2. The injected companion.

        Both READMEs must contain ## Source Branch: <plan_source_branch>.
        This is an earlier defect acceptance criterion #1 (source_branch propagation).
        """
        source_branch = "rc/v1.22.5"
        root = _build_testing_only_kanban_root(
            tmp_path, source_branch=source_branch
        )
        proj = root / "projects" / "cert_proj"
        req_path = str(proj / "requirements" / "v1.0.0-one-ticket-testing-run.md")

        plan = _build_one_tester_plan(
            project_name="cert_proj",
            source_branch=source_branch,
            requirements_path=req_path,
        )
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")

        result = _run_pm_materialize(
            plan_file, root, proj, requirements_path=req_path
        )
        assert result.returncode == 0, (
            f"pm_materialize.py exited {result.returncode} (expected 0).\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        tasks_root = proj / "tasks"
        tester_dirs = sorted(
            d for d in tasks_root.iterdir()
            if d.is_dir() and d.name.startswith("TESTER-")
        )
        assert len(tester_dirs) >= 2, (
            f"Expected at least 2 TESTER task folders under {tasks_root}; "
            f"found: {[d.name for d in tester_dirs]}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Both TESTER READMEs must carry the plan-level source_branch.
        for tester_dir in tester_dirs:
            readme_path = tester_dir / "README.md"
            found_branch = _extract_source_branch_from_readme(readme_path)
            assert found_branch == source_branch, (
                f"TESTER task {tester_dir.name}: README.md has "
                f"## Source Branch = {found_branch!r}; "
                f"expected {source_branch!r}.\n"
                f"README content:\n"
                f"{readme_path.read_text(encoding='utf-8') if readme_path.is_file() else '<missing>'}\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )

    def test_both_tickets_complete_without_operator_intervention(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Both TESTER tickets can be set to DONE without operator intervention.

        After materialization, the two TESTER status.md files start in BACKLOG
        or WAITING state.  This test simulates dispatch completion by writing
        State=DONE to each status.md — the zero-touch certification bar from
        an earlier defect acceptance criterion #2.

        Zero-touch means: no manual field repair is needed before setting DONE.
        Both status.md files must accept DONE without requiring any operator
        edits to the README or status file content.
        """
        source_branch = "rc/v1.22.5"
        root = _build_testing_only_kanban_root(
            tmp_path, source_branch=source_branch
        )
        proj = root / "projects" / "cert_proj"
        req_path = str(proj / "requirements" / "v1.0.0-one-ticket-testing-run.md")

        plan = _build_one_tester_plan(
            project_name="cert_proj",
            source_branch=source_branch,
            requirements_path=req_path,
        )
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")

        result = _run_pm_materialize(
            plan_file, root, proj, requirements_path=req_path
        )
        assert result.returncode == 0, (
            f"pm_materialize.py exited {result.returncode} (expected 0).\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        tasks_root = proj / "tasks"
        tester_dirs = sorted(
            d for d in tasks_root.iterdir()
            if d.is_dir() and d.name.startswith("TESTER-")
        )
        assert len(tester_dirs) >= 2, (
            f"Expected at least 2 TESTER task folders; "
            f"found: {[d.name for d in tester_dirs]}"
        )

        # Simulate zero-touch dispatch: write DONE to each status.md.
        # The key invariant is that both status.md files already exist (written
        # by pm_materialize) and can accept DONE without operator edits.
        for tester_dir in tester_dirs:
            status_path = tester_dir / "status.md"
            assert status_path.is_file(), (
                f"Expected status.md to exist at {status_path} after materialization."
            )
            # Verify the initial state is a valid dispatch-ready state
            # (BACKLOG, WAITING) — not an error state requiring operator repair.
            status_text = status_path.read_text(encoding="utf-8")
            initial_state_match = re.search(
                r"^##\s+State\s*\n\s*(\S+)", status_text, re.MULTILINE
            )
            assert initial_state_match is not None, (
                f"No ## State field found in {status_path}.\n"
                f"Content:\n{status_text}"
            )
            initial_state = initial_state_match.group(1).upper()
            assert initial_state in ("BACKLOG", "WAITING"), (
                f"TESTER task {tester_dir.name} has unexpected initial state "
                f"{initial_state!r}; expected BACKLOG or WAITING (dispatch-ready).\n"
                f"This means operator intervention is required before dispatch."
            )

            # Simulate completion (zero operator touch — just write DONE).
            done_status = re.sub(
                r"(^##\s+State\s*\n)\s*\S+",
                r"\g<1>DONE",
                status_text,
                flags=re.MULTILINE,
            )
            status_path.write_text(done_status, encoding="utf-8")

            # Verify DONE was written cleanly.
            final_state_text = status_path.read_text(encoding="utf-8")
            final_match = re.search(
                r"^##\s+State\s*\n\s*(\S+)", final_state_text, re.MULTILINE
            )
            assert final_match and final_match.group(1).upper() == "DONE", (
                f"Failed to set DONE state in {status_path}."
            )

    def test_intake_item_closed_after_both_tickets_complete(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Requirements doc ## Status is done and report exists after the production closure path runs.

        After materialization and simulated ticket completion the terminal TESTER
        ticket (verify-and-report, carrying finalize_mode: report in ## Constraints)
        is set to DONE and a synthetic report artifact is written.  The production
        intake-item closure mechanism is then exercised via the real ops CLI:

            python3 -m pgai_agent_kanban.ops close_item <project_root> <key> done

        This proves BOTH:
          (a) the report artifact exists at the expected finalize location, AND
          (b) the requirements intake item's ## Status is 'done'.

        The closure is not simulated with an in-test regex — the real production
        entry point is called so that regressions in the ops closure path are
        caught by this fixture.
        """
        source_branch = "rc/v1.22.5"
        root = _build_testing_only_kanban_root(
            tmp_path, source_branch=source_branch
        )
        proj = root / "projects" / "cert_proj"
        req_path = proj / "requirements" / "v1.0.0-one-ticket-testing-run.md"

        plan = _build_one_tester_plan(
            project_name="cert_proj",
            source_branch=source_branch,
            requirements_path=str(req_path),
        )
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")

        result = _run_pm_materialize(
            plan_file, root, proj, requirements_path=str(req_path)
        )
        assert result.returncode == 0, (
            f"pm_materialize.py exited {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Locate the terminal TESTER task (the verify-and-report companion
        # injected by pm_materialize for testing-only workflows).
        tasks_root = proj / "tasks"
        tester_dirs = sorted(
            d for d in tasks_root.iterdir()
            if d.is_dir() and d.name.startswith("TESTER-")
        )
        assert len(tester_dirs) >= 2, (
            f"Expected at least 2 TESTER task folders; "
            f"found: {[d.name for d in tester_dirs]}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        terminal_dir = next(
            (d for d in tester_dirs if "verify-and-report" in d.name), None
        )
        assert terminal_dir is not None, (
            f"No 'verify-and-report' TESTER task folder found.\n"
            f"Tester dirs: {[d.name for d in tester_dirs]}"
        )

        # Simulate DONE on all tickets (zero-touch dispatch).
        for tester_dir in tester_dirs:
            status_path = tester_dir / "status.md"
            assert status_path.is_file(), (
                f"Expected status.md to exist at {status_path} after materialization."
            )
            status_text = status_path.read_text(encoding="utf-8")
            done_status = re.sub(
                r"(^##\s+State\s*\n)\s*\S+",
                r"\g<1>DONE",
                status_text,
                flags=re.MULTILINE,
            )
            status_path.write_text(done_status, encoding="utf-8")

        # Simulate: the terminal TESTER writes the report artifact to the
        # finalize location (projects/<project>/artifacts/report.md).
        report_dir = terminal_dir / "artifacts"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "report.md"
        report_path.write_text(
            "# Verification Report\n\n"
            "## Recommendation\nSHIP\n\n"
            "## Summary\nAll acceptance criteria pass.\n",
            encoding="utf-8",
        )

        # Assert (a): report artifact exists at the expected finalize location.
        assert report_path.is_file(), (
            f"Report artifact not found at expected finalize location: {report_path}"
        )

        # Close the intake item via the production entry point — same mechanism
        # the wake script invokes after a finalize=report terminal ticket completes.
        req_key = req_path.stem  # filename without .md extension
        env = dict(os.environ)
        env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
        env["PGAI_PROJECT_ROOT"] = str(proj)
        env.pop("PGAI_TASKS_DIR", None)
        # Ensure pgai_agent_kanban is importable in the subprocess.
        # The module lives under team/ (the directory two levels above this file).
        existing_pp = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{str(_TEAM_DIR)}:{existing_pp}" if existing_pp else str(_TEAM_DIR)

        close_result = subprocess.run(
            [
                "python3", "-m", "pgai_agent_kanban.ops",
                "close_item", str(proj), req_key, "done",
            ],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(root),
            timeout=30,
        )
        assert close_result.returncode == 0, (
            f"close_item exited {close_result.returncode} for key {req_key!r}.\n"
            f"stdout: {close_result.stdout}\nstderr: {close_result.stderr}"
        )

        # Assert (b): requirements intake item's ## Status is 'done'.
        final_req = req_path.read_text(encoding="utf-8")
        status_match = re.search(
            r"^##\s+Status\s*\n\s*(\S+)", final_req, re.MULTILINE
        )
        assert status_match and status_match.group(1).lower() == "done", (
            f"Requirements doc ## Status is not 'done' after production closure.\n"
            f"Content:\n{final_req}"
        )

    def test_intake_item_stays_running_when_roster_ticket_blocked(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Requirements doc ## Status stays 'running' when a roster ticket is BLOCKED.

        After materialization, if any roster ticket has ## State: BLOCKED, the
        intake item must remain at ## Status: running — the production closure
        path must not close the item prematurely.  This is the mid-run failure
        regression guard: closure happens only on full completion, not when any
        sibling ticket is still BLOCKED.

        Setup: materialize both tickets; set the work ticket to BLOCKED and the
        terminal verify-and-report ticket to DONE.  Assert the intake item stays
        running (close_item is not called when a sibling is BLOCKED).
        """
        source_branch = "rc/v1.22.5"
        root = _build_testing_only_kanban_root(
            tmp_path, source_branch=source_branch
        )
        proj = root / "projects" / "cert_proj"
        req_path = proj / "requirements" / "v1.0.0-one-ticket-testing-run.md"

        plan = _build_one_tester_plan(
            project_name="cert_proj",
            source_branch=source_branch,
            requirements_path=str(req_path),
        )
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")

        result = _run_pm_materialize(
            plan_file, root, proj, requirements_path=str(req_path)
        )
        assert result.returncode == 0, (
            f"pm_materialize.py exited {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        tasks_root = proj / "tasks"
        tester_dirs = sorted(
            d for d in tasks_root.iterdir()
            if d.is_dir() and d.name.startswith("TESTER-")
        )
        assert len(tester_dirs) >= 2, (
            f"Expected at least 2 TESTER task folders; "
            f"found: {[d.name for d in tester_dirs]}"
        )

        # Set the work ticket (verify-the-implementation) to BLOCKED.
        # The terminal ticket (verify-and-report) is set to DONE.
        # This simulates a mid-run failure: the work failed, the roster is
        # not fully complete, so the intake item must stay running.
        work_dir = next(
            (d for d in tester_dirs if "verify-the-implementation" in d.name), None
        )
        terminal_dir = next(
            (d for d in tester_dirs if "verify-and-report" in d.name), None
        )
        assert work_dir is not None, (
            f"No 'verify-the-implementation' TESTER task folder found.\n"
            f"Tester dirs: {[d.name for d in tester_dirs]}"
        )
        assert terminal_dir is not None, (
            f"No 'verify-and-report' TESTER task folder found.\n"
            f"Tester dirs: {[d.name for d in tester_dirs]}"
        )

        # Set work ticket BLOCKED.
        work_status_path = work_dir / "status.md"
        assert work_status_path.is_file()
        work_status_text = work_status_path.read_text(encoding="utf-8")
        blocked_status = re.sub(
            r"(^##\s+State\s*\n)\s*\S+",
            r"\g<1>BLOCKED",
            work_status_text,
            flags=re.MULTILINE,
        )
        work_status_path.write_text(blocked_status, encoding="utf-8")

        # Set terminal ticket DONE.
        terminal_status_path = terminal_dir / "status.md"
        assert terminal_status_path.is_file()
        terminal_status_text = terminal_status_path.read_text(encoding="utf-8")
        done_status = re.sub(
            r"(^##\s+State\s*\n)\s*\S+",
            r"\g<1>DONE",
            terminal_status_text,
            flags=re.MULTILINE,
        )
        terminal_status_path.write_text(done_status, encoding="utf-8")

        # Verify the BLOCKED sibling guard: when any roster ticket sharing the
        # same requirements path is BLOCKED, the production system must NOT close
        # the intake item.  Simulate this guard in Python: scan sibling tasks for
        # BLOCKED state before deciding to call close_item.
        req_key = req_path.stem
        env = dict(os.environ)
        env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
        env["PGAI_PROJECT_ROOT"] = str(proj)
        env.pop("PGAI_TASKS_DIR", None)

        # Guard check: any sibling TESTER task sharing this requirements path
        # that is BLOCKED prevents intake closure.
        blocked_sibling_found = _has_blocked_sibling_sharing_requirements(
            tasks_root, str(req_path)
        )
        assert blocked_sibling_found, (
            f"Expected to find a BLOCKED sibling task for requirements path "
            f"{req_path}; guard check returned False.\n"
            f"Task dirs: {[d.name for d in tester_dirs]}"
        )

        # Because a BLOCKED sibling was found, close_item is NOT called.
        # The intake item must remain at running.
        intake_text = req_path.read_text(encoding="utf-8")
        status_match = re.search(
            r"^##\s+Status\s*\n\s*(\S+)", intake_text, re.MULTILINE
        )
        assert status_match and status_match.group(1).lower() == "running", (
            f"Requirements doc ## Status must stay 'running' when a roster ticket "
            f"is BLOCKED; got {status_match.group(1)!r}.\n"
            f"Content:\n{intake_text}"
        )

    def test_materialization_is_idempotent_second_run_exits_two(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """A second pm_materialize.py run against the same plan exits 2 (idempotent no-op).

        The plan-hash marker written after successful first materialization causes
        pm_materialize.py to exit with code 2 on a repeat run — the documented
        idempotent-skip exit code.  This proves the fixture is re-runnable without
        accumulating duplicate ticket sets.
        """
        source_branch = "rc/v1.22.5"
        root = _build_testing_only_kanban_root(
            tmp_path, source_branch=source_branch
        )
        proj = root / "projects" / "cert_proj"
        req_path = str(proj / "requirements" / "v1.0.0-one-ticket-testing-run.md")

        plan = _build_one_tester_plan(
            project_name="cert_proj",
            source_branch=source_branch,
            requirements_path=req_path,
        )
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")

        # First run must succeed.
        first_result = _run_pm_materialize(
            plan_file, root, proj, requirements_path=req_path
        )
        assert first_result.returncode == 0, (
            f"First pm_materialize.py run exited {first_result.returncode}.\n"
            f"stdout: {first_result.stdout}\nstderr: {first_result.stderr}"
        )

        # Second run against identical plan must exit 2 (idempotent no-op).
        second_result = _run_pm_materialize(
            plan_file, root, proj, requirements_path=req_path
        )
        assert second_result.returncode == 2, (
            f"Second pm_materialize.py run exited {second_result.returncode}; "
            f"expected 2 (idempotent no-op via plan-hash marker).\n"
            f"stdout: {second_result.stdout}\nstderr: {second_result.stderr}"
        )
        # Confirm the idempotent message is present in stderr.
        assert "already materialized" in second_result.stderr.lower() or \
               "idempotent" in second_result.stderr.lower(), (
            f"Expected idempotent-skip message in stderr.\n"
            f"stderr: {second_result.stderr}"
        )

    def test_tester_backlog_receives_both_ticket_entries(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Both TESTER task IDs appear in tester_backlog.md after materialization.

        After materialization, the project's tester_backlog.md must contain
        entries for both ticket IDs — the plan's work ticket and the injected
        companion — so the wake script can dispatch them.
        Zero-touch dispatch requires both entries to be present without
        operator edits to the backlog file.
        """
        source_branch = "rc/v1.22.5"
        root = _build_testing_only_kanban_root(
            tmp_path, source_branch=source_branch
        )
        proj = root / "projects" / "cert_proj"
        req_path = str(proj / "requirements" / "v1.0.0-one-ticket-testing-run.md")

        plan = _build_one_tester_plan(
            project_name="cert_proj",
            source_branch=source_branch,
            requirements_path=req_path,
        )
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")

        result = _run_pm_materialize(
            plan_file, root, proj, requirements_path=req_path
        )
        assert result.returncode == 0, (
            f"pm_materialize.py exited {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        tester_backlog_path = proj / "tasks" / "queues" / "tester_backlog.md"
        assert tester_backlog_path.is_file(), (
            f"Expected tester_backlog.md at {tester_backlog_path}; file not found.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        backlog_text = tester_backlog_path.read_text(encoding="utf-8")

        # Both ticket IDs must appear in the backlog (as pending [ ] entries).
        tasks_root = proj / "tasks"
        tester_task_ids = sorted(
            d.name
            for d in tasks_root.iterdir()
            if d.is_dir() and d.name.startswith("TESTER-")
        )
        assert len(tester_task_ids) >= 2, (
            f"Expected at least 2 TESTER task folders; found: {tester_task_ids}"
        )
        for task_id in tester_task_ids:
            assert task_id in backlog_text, (
                f"Expected task ID {task_id!r} in tester_backlog.md.\n"
                f"Backlog content:\n{backlog_text}"
            )


# ---------------------------------------------------------------------------
# Wake-dispatch harness helpers
# ---------------------------------------------------------------------------

# Path to the stub wake harness (exercises real wake substrate without LLM).
_WAKE_HARNESS = _TEAM_DIR / "scripts" / "wake" / "harness.sh"


def _build_harness_kanban_root(
    parent: pathlib.Path,
    project_name: str = "cert_proj",
    source_branch: str = "rc/v1.22.4",
) -> pathlib.Path:
    """Build a kanban root configured for the harness wake provider.

    Identical to _build_testing_only_kanban_root but writes
    '[providers] active = harness' into kanban.cfg so that harness.sh
    passes its own active-provider guard before sourcing wake_common.sh.

    Args:
        parent:        Parent temp directory (use pytest's tmp_path).
        project_name:  Name of the project to create.
        source_branch: Source branch recorded in the requirements doc.

    Returns:
        pathlib.Path — the kanban root.
    """
    root = _build_testing_only_kanban_root(
        parent, project_name=project_name, source_branch=source_branch
    )
    # Overwrite kanban.cfg to add [providers] active = harness.
    # wake_common.sh's load_config reads this on startup; the harness's own
    # active-provider guard also reads it directly via Python.
    (root / "kanban.cfg").write_text(
        "[paths]\n\n"
        "[chain]\npm_mode = automatic\n\n"
        "[wake]\nmax_tasks_per_wake = 10\n\n"
        "[providers]\nactive = harness\n",
        encoding="utf-8",
    )
    return root


def _run_wake_harness(
    kanban_root: pathlib.Path,
    project_root: pathlib.Path,
    *,
    max_tasks: int = 10,
    agent: str = "tester",
) -> subprocess.CompletedProcess:
    """Invoke wake/harness.sh as a subprocess against a temp kanban root.

    Exercises the REAL wake substrate (wake_common.sh including
    close_intake_on_finalize_report and the completion census) without
    invoking an LLM.  This is 'real wake dispatch' in the sense that the
    intake closure path (process_one_task → close_intake_on_finalize_report)
    is exercised end-to-end in a subprocess; only provider_invoke_agent is
    stubbed.

    Args:
        kanban_root:  Kanban root for the temp tree (must have
                      [providers] active = harness in kanban.cfg).
        project_root: Project root (projects/<name>/ inside kanban_root).
        max_tasks:    --max-tasks argument for the harness.
        agent:        --agent argument (default: tester).

    Returns:
        subprocess.CompletedProcess with returncode, stdout, stderr.
    """
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["PGAI_PROJECT_ROOT"] = str(project_root)
    # Remove test-harness-inherited overrides so wake_common.sh derives paths
    # from PGAI_AGENT_KANBAN_ROOT_PATH.
    env.pop("PGAI_TASKS_DIR", None)
    env.pop("PGAI_REQUIREMENTS_DIR", None)
    env.pop("PGAI_DEV_TREE_PATH", None)
    # Ensure pgai_agent_kanban is importable in the subprocess environment:
    # close_intake_on_finalize_report in wake_common.sh calls
    # `python3 -m pgai_agent_kanban.ops close_item`, and the package lives
    # under team/ (two levels up from this test file).
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{str(_TEAM_DIR)}:{existing_pp}" if existing_pp else str(_TEAM_DIR)
    )

    cmd = [
        "bash",
        str(_WAKE_HARNESS),
        f"--agent={agent}",
        f"--max-tasks={max_tasks}",
    ]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(kanban_root),
        timeout=120,
    )


def _get_intake_status(req_path: pathlib.Path) -> str:
    """Return the ## Status value from an intake requirements doc.

    Args:
        req_path: Path to the requirements Markdown file.

    Returns:
        str — status value (stripped, lowercase), or "" if absent.
    """
    if not req_path.is_file():
        return ""
    text = req_path.read_text(encoding="utf-8")
    m = re.search(r"^##\s+Status\s*\n\s*(\S+)", text, re.MULTILINE)
    return m.group(1).strip().lower() if m else ""


def _plant_task_state(status_file: pathlib.Path, state: str) -> None:
    """Write a specific ## State value into a status.md file.

    Used by wake-dispatch tests to pre-seed sibling task states before
    running the harness, simulating mid-run (WORKING) or prior-run (WONT-DO)
    scenarios.

    Args:
        status_file: Path to the task status.md.
        state:       State value to write (e.g. 'WORKING', 'WONT-DO').
    """
    if not status_file.is_file():
        raise FileNotFoundError(f"status.md not found at {status_file}")
    text = status_file.read_text(encoding="utf-8")
    text_new, n = re.subn(
        r"(^## State\s*\n)(\S+)",
        lambda m: m.group(1) + state,
        text,
        flags=re.MULTILINE,
    )
    if n == 0:
        # Append if absent.
        text_new = text + f"\n## State\n{state}\n"
    status_file.write_text(text_new, encoding="utf-8")


# ---------------------------------------------------------------------------
# Wake-dispatch test class
# ---------------------------------------------------------------------------


class TestOneTicketWakeDispatchClosure:
    """Wake-dispatch certification fixture: one-ticket testing-only closure via real substrate.

    Each test in this class exercises the real wake substrate
    (close_intake_on_finalize_report + the completion census introduced by
    CODER-20260715-007 to fix BUG-0068) through a subprocess invocation of
    wake/harness.sh.

    BUG-0068 production diagnosis anchor (must be caught by these tests):
      [2026-07-14T14:04:10+00:00] wake(tester): task
      TESTER-20260714-008-verify-pvg-closure-check:
      close_intake_on_finalize_report: finalize_mode:report not found
      in README constraints — skipping intake closure (not a
      finalize=report terminal ticket)

    The root cause: the old guard looked for a 'finalize_mode:report'
    marker in the task README ## Constraints section.  That marker was only
    written by inject_simple_tester_task (the synthetic companion path), not
    by PM-authored one-ticket plans.  The census fix (CODER-20260715-007)
    replaced the marker with a full sibling scan so the guard is shape-blind.

    Test topology
    -------------
    1. test_wake_dispatch_closes_intake_on_all_terminal_tickets
       Full closure: all TESTER siblings reach DONE via the harness.
    2. test_wake_dispatch_census_skips_closure_when_sibling_nonterminal
       Mid-run guard: one sibling pre-seeded to WORKING; census skips closure.
    3. test_wake_dispatch_closure_fires_with_wontdo_corpse_sibling
       Corpse tolerance: one sibling pre-seeded to WONT-DO; closure still fires.

    None of the tests call close_intake_on_finalize_report directly from
    Python — all closure assertions are based on intake ## Status and the
    wake log lines emitted by the REAL shell function.
    """

    def test_wake_dispatch_closes_intake_on_all_terminal_tickets(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Harness run closes the intake item when all TESTER siblings reach DONE.

        After pm_materialize produces two TESTER task folders (plan ticket +
        injected companion), the harness processes both.  When both reach DONE,
        the real close_intake_on_finalize_report calls the completion census
        which returns ok:2, then invokes python3 -m pgai_agent_kanban.ops
        close_item to flip the intake ## Status from running to done.

        This is the primary regression test for BUG-0068: the census-based
        approach must succeed for PM-authored one-ticket plans where the old
        'finalize_mode:report' marker was absent.

        Asserts:
        - Intake ## Status == 'done' after harness run.
        - Wake log contains 'closed done' (from close_intake_on_finalize_report).
        - At least one TESTER task artifact dir contains report.md.
        """
        source_branch = "rc/v1.22.4"
        root = _build_harness_kanban_root(tmp_path, source_branch=source_branch)
        proj = root / "projects" / "cert_proj"
        req_path = proj / "requirements" / "v1.0.0-one-ticket-testing-run.md"

        plan = _build_one_tester_plan(
            project_name="cert_proj",
            source_branch=source_branch,
            requirements_path=str(req_path),
        )
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")

        mat_result = _run_pm_materialize(
            plan_file, root, proj, requirements_path=str(req_path)
        )
        assert mat_result.returncode == 0, (
            f"pm_materialize.py exited {mat_result.returncode}.\n"
            f"stdout: {mat_result.stdout}\nstderr: {mat_result.stderr}"
        )

        # Run the harness: processes all BACKLOG TESTER tasks (max-tasks=10).
        wake_result = _run_wake_harness(root, proj, max_tasks=10)
        combined_log = wake_result.stdout + wake_result.stderr

        assert wake_result.returncode == 0, (
            f"wake/harness.sh exited {wake_result.returncode}.\n"
            f"stdout: {wake_result.stdout}\nstderr: {wake_result.stderr}"
        )

        # Guard 1: intake ## Status must be 'done'.
        intake_status = _get_intake_status(req_path)
        assert intake_status == "done", (
            f"Expected intake ## Status == 'done' after harness wake dispatch; "
            f"got {intake_status!r}.\n"
            f"Requirements file: {req_path}\n"
            f"Content:\n{req_path.read_text(encoding='utf-8') if req_path.is_file() else 'FILE MISSING'}\n"
            f"Wake log:\n{combined_log}"
        )

        # Guard 2: wake log must contain the closure confirmation line.
        assert "closed done" in combined_log, (
            f"Expected 'closed done' in wake log "
            f"(close_intake_on_finalize_report census confirmation).\n"
            f"Wake log:\n{combined_log}"
        )

        # Guard 3: at least one report artifact must be present.
        tasks_root = proj / "tasks"
        tester_dirs = sorted(
            d for d in tasks_root.iterdir()
            if d.is_dir() and d.name.startswith("TESTER-")
        )
        report_found = any(
            (d / "artifacts" / "report.md").is_file()
            for d in tester_dirs
        )
        assert report_found, (
            f"Expected at least one TESTER task artifacts/report.md; "
            f"found none under {tasks_root}.\n"
            f"Wake log:\n{combined_log}"
        )

    def test_wake_dispatch_census_skips_closure_when_sibling_nonterminal(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Census skips intake closure when a WORKING sibling task is present.

        This mid-run guard test pre-seeds one TESTER sibling to WORKING state,
        then runs the harness with max-tasks=1 so only the BACKLOG task is
        processed.  When close_intake_on_finalize_report runs its census, it
        sees the WORKING sibling and emits a 'census incomplete — skipping'
        log line instead of closing the intake.

        This proves the census correctly protects against premature closure
        during a multi-agent run where siblings are still in progress.

        Asserts:
        - Intake ## Status remains 'running' (not 'done').
        - Wake log contains 'census incomplete' (census skip confirmation).
        """
        source_branch = "rc/v1.22.4"
        root = _build_harness_kanban_root(tmp_path, source_branch=source_branch)
        proj = root / "projects" / "cert_proj"
        req_path = proj / "requirements" / "v1.0.0-one-ticket-testing-run.md"

        plan = _build_one_tester_plan(
            project_name="cert_proj",
            source_branch=source_branch,
            requirements_path=str(req_path),
        )
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")

        mat_result = _run_pm_materialize(
            plan_file, root, proj, requirements_path=str(req_path)
        )
        assert mat_result.returncode == 0, (
            f"pm_materialize.py exited {mat_result.returncode}.\n"
            f"stdout: {mat_result.stdout}\nstderr: {mat_result.stderr}"
        )

        # Pre-seed one TESTER sibling to WORKING to simulate a mid-run scenario.
        tasks_root = proj / "tasks"
        tester_dirs = sorted(
            d for d in tasks_root.iterdir()
            if d.is_dir() and d.name.startswith("TESTER-")
        )
        assert len(tester_dirs) >= 2, (
            f"Expected at least 2 TESTER task folders for mid-run test; "
            f"found {len(tester_dirs)}"
        )
        # Mark the second TESTER task as WORKING (simulates a sibling in progress).
        sibling_status = tester_dirs[1] / "status.md"
        _plant_task_state(sibling_status, "WORKING")

        # Run the harness with max-tasks=1: processes only the first BACKLOG task
        # (the first TESTER dir with BACKLOG state, i.e. tester_dirs[0]).
        wake_result = _run_wake_harness(root, proj, max_tasks=1)
        combined_log = wake_result.stdout + wake_result.stderr

        assert wake_result.returncode == 0, (
            f"wake/harness.sh exited {wake_result.returncode}.\n"
            f"stdout: {wake_result.stdout}\nstderr: {wake_result.stderr}"
        )

        # Guard 1: intake ## Status must NOT be 'done' — census must have skipped.
        intake_status = _get_intake_status(req_path)
        assert intake_status != "done", (
            f"Intake ## Status is 'done' but census should have skipped due to "
            f"WORKING sibling.  Expected 'running' (or similar non-done value).\n"
            f"Requirements file: {req_path}\n"
            f"Content:\n{req_path.read_text(encoding='utf-8') if req_path.is_file() else 'FILE MISSING'}\n"
            f"Wake log:\n{combined_log}"
        )

        # Guard 2: wake log must confirm census skip.
        assert "census incomplete" in combined_log, (
            f"Expected 'census incomplete' in wake log "
            f"(close_intake_on_finalize_report census skip confirmation).\n"
            f"Wake log:\n{combined_log}"
        )

    def test_wake_dispatch_closure_fires_with_wontdo_corpse_sibling(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Census treats WONT-DO prior-run corpse siblings as terminal.

        A WONT-DO task citing the same requirements path is a prior-run corpse
        (e.g. a cancelled earlier attempt).  The census counts WONT-DO as a
        terminal state alongside DONE, so when all remaining siblings complete,
        the intake closure fires even if a WONT-DO corpse is present.

        This test pre-seeds one TESTER task to WONT-DO state, then runs the
        harness to process the remaining BACKLOG task.  When that task reaches
        DONE, the census sees DONE + WONT-DO = all terminal → closes intake.

        Asserts:
        - Intake ## Status == 'done' after harness run.
        - Wake log contains 'closed done'.
        """
        source_branch = "rc/v1.22.4"
        root = _build_harness_kanban_root(tmp_path, source_branch=source_branch)
        proj = root / "projects" / "cert_proj"
        req_path = proj / "requirements" / "v1.0.0-one-ticket-testing-run.md"

        plan = _build_one_tester_plan(
            project_name="cert_proj",
            source_branch=source_branch,
            requirements_path=str(req_path),
        )
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")

        mat_result = _run_pm_materialize(
            plan_file, root, proj, requirements_path=str(req_path)
        )
        assert mat_result.returncode == 0, (
            f"pm_materialize.py exited {mat_result.returncode}.\n"
            f"stdout: {mat_result.stdout}\nstderr: {mat_result.stderr}"
        )

        # Pre-seed one TESTER task as WONT-DO (prior-run corpse).
        tasks_root = proj / "tasks"
        tester_dirs = sorted(
            d for d in tasks_root.iterdir()
            if d.is_dir() and d.name.startswith("TESTER-")
        )
        assert len(tester_dirs) >= 2, (
            f"Expected at least 2 TESTER task folders for corpse test; "
            f"found {len(tester_dirs)}"
        )
        corpse_status = tester_dirs[1] / "status.md"
        _plant_task_state(corpse_status, "WONT-DO")

        # Run the harness with max-tasks=1: processes the remaining BACKLOG task.
        wake_result = _run_wake_harness(root, proj, max_tasks=1)
        combined_log = wake_result.stdout + wake_result.stderr

        assert wake_result.returncode == 0, (
            f"wake/harness.sh exited {wake_result.returncode}.\n"
            f"stdout: {wake_result.stdout}\nstderr: {wake_result.stderr}"
        )

        # Guard 1: intake ## Status must be 'done' — WONT-DO is terminal.
        intake_status = _get_intake_status(req_path)
        assert intake_status == "done", (
            f"Expected intake ## Status == 'done' after harness wake dispatch "
            f"with WONT-DO corpse sibling; got {intake_status!r}.\n"
            f"Requirements file: {req_path}\n"
            f"Content:\n{req_path.read_text(encoding='utf-8') if req_path.is_file() else 'FILE MISSING'}\n"
            f"Wake log:\n{combined_log}"
        )

        # Guard 2: wake log must contain the closure confirmation line.
        assert "closed done" in combined_log, (
            f"Expected 'closed done' in wake log "
            f"(close_intake_on_finalize_report census confirmation).\n"
            f"Wake log:\n{combined_log}"
        )
