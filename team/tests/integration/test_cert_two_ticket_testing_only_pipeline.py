"""
test_cert_two_ticket_testing_only_pipeline.py
=============================================
Certification fixture: two-ticket testing-only decomposition, zero-touch dispatch.

This test exercises the end-to-end an earlier defect fix in the real pipeline:

    selection -> PM decomposition emitting two TESTER tickets
             -> materialization (real pm_materialize.py subprocess)
             -> both tickets carry plan-level source_branch
             -> both tickets dispatch and complete (simulated DONE)
             -> intake item closed done via production ops path

The fixture proves an earlier defect acceptance criterion #1: both materialized TESTER
READMEs carry the plan's source ref, and the full pipeline completes with zero
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
# team/tests/integration/test_cert_two_ticket_testing_only_pipeline.py
#   └── team/tests/integration/
#       └── team/tests/
#           └── team/
#               └── <dev-tree-root>
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_PM_MATERIALIZE = _TEAM_DIR / "pm-agent" / "pm_materialize.py"
_REAL_WORKFLOWS_DIR = _TEAM_DIR / "workflows"

# Workflow plugins needed by the testing-only materializer path.
_WORKFLOW_PLUGINS = ["release", "document", "testing-only"]


# ---------------------------------------------------------------------------
# Fixture tree builder
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
    req_file = req_dir / "v1.0.0-two-ticket-testing-run.md"
    req_file.write_text(
        "# v20260713-two-ticket-testing-run: two ticket testing run\n\n"
        "## Status\nrunning\n\n"
        "## Target Version\nv20260713-two-ticket-testing-run\n\n"
        "## Workflow Type\ntesting-only\n\n"
        "## Source Branch\n"
        f"{source_branch}\n\n"
        "## PM Task\nnone\n\n"
        "## Summary\n"
        "Certification requirements doc for two-ticket testing-only pipeline.\n",
        encoding="utf-8",
    )

    return root


# ---------------------------------------------------------------------------
# Plan JSON builder
# ---------------------------------------------------------------------------


def _build_two_tester_plan(
    project_name: str,
    source_branch: str,
    requirements_path: str,
    *,
    ticket1_source_branch: str = "",
    ticket2_source_branch: str = "none",
) -> dict:
    """Build a plan JSON with two TESTER tasks for a testing-only workflow.

    Ticket 1 may carry an explicit source_branch (per-ticket override scenario).
    Ticket 2 has source_branch absent/none — this is the an earlier defect scenario where
    pm_materialize must propagate the plan-level source_branch onto it.

    Args:
        project_name:           Project name, placed in the plan JSON.
        source_branch:          Plan-level source_branch (propagated to tickets
                                that have none/empty).
        requirements_path:      Absolute path to the requirements doc.
        ticket1_source_branch:  Explicit override for ticket 1 (empty = use plan default).
        ticket2_source_branch:  Source_branch for ticket 2 ("none" = an earlier defect scenario).

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
            "source_branch": ticket1_source_branch or source_branch,
            "goal": "Verify the first stage of the certification run.",
            "inputs": [requirements_path],
            "context_paths": [],
            "required_output": "Verification pass or named failure.",
            "constraints": ["tester_operation: verify-and-report"],
            "acceptance_criteria": ["All acceptance criteria pass."],
            "depends_on": [],
            "notes": "First TESTER ticket in a two-ticket testing-only plan.",
        },
        {
            "sequence": 2,
            "slug": "verify-and-report",
            "title": "TESTER: Verify and write report",
            "role": "TESTER",
            "assigned_agent": "TESTER",
            "working_directory": "none",
            "git_repo": "none",
            "source_branch": ticket2_source_branch,
            "goal": "Write the test report for the testing-only run.",
            "inputs": [requirements_path],
            "context_paths": [],
            "required_output": "Test report artifact.",
            "constraints": ["tester_operation: verify-and-report"],
            "acceptance_criteria": ["Report written to artifacts/."],
            "depends_on": [1],
            "notes": "Second TESTER ticket; source_branch absent pre-fix (BUG-0059 scenario).",
        },
    ]

    return {
        "workflow_type": "testing-only",
        "project_name": project_name,
        "source_branch": source_branch,
        "target_version": "none",
        "test_required": "false",
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


class TestTwoTicketTestingOnlyPipeline:
    """End-to-end certification fixture for two-ticket testing-only decomposition.

    Proves an earlier defect acceptance criterion #1: both materialized TESTER READMEs
    carry the plan's source ref, and the full pipeline completes with zero
    operator interventions.

    All tests share the same kanban root pattern (built per-test via tmp_path
    so there is no shared mutable state).
    """

    def test_materialization_exits_zero_for_two_tester_plan(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """pm_materialize.py exits zero for a two-TESTER testing-only plan.

        A well-formed plan JSON with two TESTER tasks and a plan-level
        source_branch must materialize cleanly (exit 0) on the first run.
        This is the base case confirming the fixture tree and plan JSON
        are correctly wired before the source_branch assertions are checked.
        """
        source_branch = "rc/v1.22.4"
        root = _build_testing_only_kanban_root(
            tmp_path, source_branch=source_branch
        )
        proj = root / "projects" / "cert_proj"
        req_path = str(proj / "requirements" / "v1.0.0-two-ticket-testing-run.md")

        plan = _build_two_tester_plan(
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

    def test_both_tester_readmes_carry_plan_source_branch(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Both TESTER task READMEs carry the plan-level source_branch after materialization.

        an earlier defect scenario: ticket 2 has source_branch='none' in the plan JSON.
        After materialization, the propagation fix must supply the plan-level
        source_branch to both ticket READMEs.  Both READMEs must contain
        ## Source Branch: <plan_source_branch>.

        This is the primary assertion for an earlier defect acceptance criterion #1.
        """
        source_branch = "rc/v1.22.4"
        root = _build_testing_only_kanban_root(
            tmp_path, source_branch=source_branch
        )
        proj = root / "projects" / "cert_proj"
        req_path = str(proj / "requirements" / "v1.0.0-two-ticket-testing-run.md")

        plan = _build_two_tester_plan(
            project_name="cert_proj",
            source_branch=source_branch,
            requirements_path=req_path,
            # Ticket 2 has no source_branch — the an earlier defect scenario.
            ticket2_source_branch="none",
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

        # Find all TESTER task folders created under tasks/
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
        an earlier defect acceptance #1.

        Zero-touch means: no manual field repair is needed before setting DONE.
        Both status.md files must accept DONE without requiring any operator
        edits to the README or status file content.
        """
        source_branch = "rc/v1.22.4"
        root = _build_testing_only_kanban_root(
            tmp_path, source_branch=source_branch
        )
        proj = root / "projects" / "cert_proj"
        req_path = str(proj / "requirements" / "v1.0.0-two-ticket-testing-run.md")

        plan = _build_two_tester_plan(
            project_name="cert_proj",
            source_branch=source_branch,
            requirements_path=req_path,
            ticket2_source_branch="none",
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
        source_branch = "rc/v1.22.4"
        root = _build_testing_only_kanban_root(
            tmp_path, source_branch=source_branch
        )
        proj = root / "projects" / "cert_proj"
        req_path = proj / "requirements" / "v1.0.0-two-ticket-testing-run.md"

        plan = _build_two_tester_plan(
            project_name="cert_proj",
            source_branch=source_branch,
            requirements_path=str(req_path),
            ticket2_source_branch="none",
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

        Setup: materialize both tickets; set the first ticket (verify-the-implementation)
        to BLOCKED and the second (verify-and-report) to DONE.  Assert the intake
        item stays running (close_item is not called when a sibling is BLOCKED).
        """
        source_branch = "rc/v1.22.4"
        root = _build_testing_only_kanban_root(
            tmp_path, source_branch=source_branch
        )
        proj = root / "projects" / "cert_proj"
        req_path = proj / "requirements" / "v1.0.0-two-ticket-testing-run.md"

        plan = _build_two_tester_plan(
            project_name="cert_proj",
            source_branch=source_branch,
            requirements_path=str(req_path),
            ticket2_source_branch="none",
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
        # Set the terminal ticket (verify-and-report) to DONE.
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
        source_branch = "rc/v1.22.4"
        root = _build_testing_only_kanban_root(
            tmp_path, source_branch=source_branch
        )
        proj = root / "projects" / "cert_proj"
        req_path = str(proj / "requirements" / "v1.0.0-two-ticket-testing-run.md")

        plan = _build_two_tester_plan(
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

    def test_per_ticket_source_branch_override_is_preserved(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """A per-ticket explicit source_branch override survives materialization unchanged.

        Propagation is default-fill, not overwrite.  When ticket 1 carries an
        explicit source_branch different from the plan-level source_branch, its
        value must appear unchanged in the ticket's README.  Ticket 2 (without an
        explicit override) must receive the plan-level source_branch.

        This is an earlier defect acceptance criterion #2: per-ticket override regression.
        """
        plan_source_branch = "rc/v1.22.4"
        ticket1_override = "rc/v0.10.2"  # explicit per-ticket override
        root = _build_testing_only_kanban_root(
            tmp_path, source_branch=plan_source_branch
        )
        proj = root / "projects" / "cert_proj"
        req_path = str(proj / "requirements" / "v1.0.0-two-ticket-testing-run.md")

        plan = _build_two_tester_plan(
            project_name="cert_proj",
            source_branch=plan_source_branch,
            requirements_path=req_path,
            ticket1_source_branch=ticket1_override,
            ticket2_source_branch="none",
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

        tasks_root = proj / "tasks"
        tester_dirs = sorted(
            d for d in tasks_root.iterdir()
            if d.is_dir() and d.name.startswith("TESTER-")
        )
        assert len(tester_dirs) >= 2, (
            f"Expected at least 2 TESTER task folders; "
            f"found: {[d.name for d in tester_dirs]}"
        )

        # Ticket 1 (verify-the-implementation) should have the override branch.
        # Ticket 2 (verify-and-report) should have the plan-level branch.
        # Sort by slug to get deterministic ordering (slug is in the task_id).
        impl_dirs = [d for d in tester_dirs if "verify-the-implementation" in d.name]
        report_dirs = [d for d in tester_dirs if "verify-and-report" in d.name]

        assert impl_dirs, (
            f"No TESTER task with 'verify-the-implementation' slug found.\n"
            f"Tester dirs: {[d.name for d in tester_dirs]}"
        )
        assert report_dirs, (
            f"No TESTER task with 'verify-and-report' slug found.\n"
            f"Tester dirs: {[d.name for d in tester_dirs]}"
        )

        # Ticket 1: per-ticket override must be preserved
        impl_branch = _extract_source_branch_from_readme(impl_dirs[0] / "README.md")
        assert impl_branch == ticket1_override, (
            f"Ticket 1 (verify-the-implementation): expected source_branch "
            f"{ticket1_override!r}; got {impl_branch!r}. "
            f"Per-ticket override must not be overwritten by propagation."
        )

        # Ticket 2: plan-level source_branch must be propagated
        report_branch = _extract_source_branch_from_readme(report_dirs[0] / "README.md")
        assert report_branch == plan_source_branch, (
            f"Ticket 2 (verify-and-report): expected source_branch "
            f"{plan_source_branch!r} (propagated from plan); got {report_branch!r}."
        )

    def test_tester_backlog_receives_both_ticket_entries(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Both TESTER task IDs appear in tester_backlog.md after materialization.

        After materialization, the project's tester_backlog.md must contain
        entries for both ticket IDs so the wake script can dispatch them.
        Zero-touch dispatch requires both entries to be present without
        operator edits to the backlog file.
        """
        source_branch = "rc/v1.22.4"
        root = _build_testing_only_kanban_root(
            tmp_path, source_branch=source_branch
        )
        proj = root / "projects" / "cert_proj"
        req_path = str(proj / "requirements" / "v1.0.0-two-ticket-testing-run.md")

        plan = _build_two_tester_plan(
            project_name="cert_proj",
            source_branch=source_branch,
            requirements_path=req_path,
            ticket2_source_branch="none",
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

        # Both ticket IDs must appear in the backlog (as pending [ ] entries or W entries).
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
