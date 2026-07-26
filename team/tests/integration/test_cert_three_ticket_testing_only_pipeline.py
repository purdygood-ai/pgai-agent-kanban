"""
test_cert_three_ticket_testing_only_pipeline.py
===============================================
Certification fixture: three-ticket testing-only wake dispatch closure.

This test exercises the end-to-end fix for BUG-0068 through the REAL wake
substrate (close_intake_on_finalize_report + completion census from
CODER-20260715-007) using a three-ticket testing-only PM plan.

BUG-0068 production diagnosis anchor:
  [2026-07-14T14:04:10+00:00] wake(tester): task
  TESTER-20260714-008-verify-pvg-closure-check:
  close_intake_on_finalize_report: finalize_mode:report not found
  in README constraints — skipping intake closure (not a
  finalize=report terminal ticket)

Root cause: the old guard looked for a 'finalize_mode:report' marker
in the task README ## Constraints section.  That marker was only written
by inject_simple_tester_task (the synthetic companion path), not by
PM-authored tickets.  The census fix (CODER-20260715-007) replaced the
marker guard with a full sibling scan so the logic is shape-blind
regardless of how many tickets the PM emits.

Three-ticket topology
---------------------
The plan has THREE TESTER work tickets.  For a testing-only workflow with
three work tickets, pm_materialize does NOT inject a companion (the companion
injection logic only applies to exactly-one-work-ticket plans).  All three
tickets are PM-authored, none carries a 'finalize_mode:report' marker.
This is the worst-case topology for the old bug: three tickets, none with
the required marker, so the old code would skip closure for all three.

Test topology
-------------
1. TestThreeTicketWakeDispatchClosure.test_wake_dispatch_closes_intake_on_all_terminal_tickets
   Full closure: all three TESTER siblings reach DONE via the harness.
2. TestThreeTicketWakeDispatchClosure.test_wake_dispatch_census_skips_closure_when_sibling_nonterminal
   Mid-run guard: one sibling pre-seeded to WORKING; census skips closure.
3. TestThreeTicketWakeDispatchClosure.test_wake_dispatch_closure_fires_with_wontdo_corpse_sibling
   Corpse tolerance: one sibling WONT-DO; closure still fires when remaining
   two are DONE.

Design notes
------------
- wake/harness.sh is invoked as a subprocess (never called directly from Python).
- close_intake_on_finalize_report is exercised via the REAL wake substrate.
  No test calls the function from Python or simulates closure inline.
- All temp paths use pytest's tmp_path (never bare /tmp).
- Test names describe behavior, not bug IDs or ticket IDs (SOP.md anti-pattern 6).
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
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_PM_MATERIALIZE = _TEAM_DIR / "pm-agent" / "pm_materialize.py"
_REAL_WORKFLOWS_DIR = _TEAM_DIR / "workflows"
_WAKE_HARNESS = _TEAM_DIR / "scripts" / "wake" / "harness.sh"

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

    Args:
        parent:        Parent temp directory (use pytest's tmp_path).
        project_name:  Name of the project to create.
        source_branch: Source branch recorded in the requirements doc.

    Returns:
        pathlib.Path — the kanban root.
    """
    root = parent / "kanban_cert"
    root.mkdir(parents=True, exist_ok=True)

    (root / "kanban.cfg").write_text(
        "[paths]\n\n[chain]\npm_mode = automatic\n\n[wake]\nmax_tasks_per_wake = 1\n",
        encoding="utf-8",
    )

    (root / "projects.cfg").write_text(
        f"[project:{project_name}]\n"
        "priority=1\n"
        "description=certification fixture project\n"
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
            f"# {agent.upper()} Backlog\n\n"
            "<!-- Format: - [ ] TASK-ID -->\n",
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
    req_file = req_dir / "v1.0.0-three-ticket-testing-run.md"
    req_file.write_text(
        "# v20260715-three-ticket-testing-run: three ticket testing run\n\n"
        "## Status\nrunning\n\n"
        "## Target Version\nv20260715-three-ticket-testing-run\n\n"
        "## Workflow Type\ntesting-only\n\n"
        "## Source Branch\n"
        f"{source_branch}\n\n"
        "## PM Task\nnone\n\n"
        "## Summary\n"
        "Certification requirements doc for three-ticket testing-only pipeline.\n",
        encoding="utf-8",
    )

    return root


# ---------------------------------------------------------------------------
# Plan JSON builder — three TESTER work tickets
# ---------------------------------------------------------------------------


def _build_three_tester_plan(
    project_name: str,
    source_branch: str,
    requirements_path: str,
) -> dict:
    """Build a plan JSON with three TESTER work tickets for a testing-only workflow.

    Three PM-authored TESTER tickets with the same requirements path in ##
    Inputs.  None carries a 'finalize_mode:report' constraint marker — this
    is the worst-case topology for BUG-0068: the old marker-based guard would
    skip closure on all three tickets.  The census-based fix must detect that
    all three are terminal and fire closure exactly once (when the last ticket
    completes).

    Args:
        project_name:       Project name for the plan JSON.
        source_branch:      Plan-level source_branch.
        requirements_path:  Absolute path to the requirements doc.

    Returns:
        dict — plan JSON structure ready for json.dumps().
    """
    tasks = [
        {
            "sequence": 1,
            "slug": "verify-phase-one",
            "title": "TESTER: Verify phase one",
            "role": "TESTER",
            "assigned_agent": "TESTER",
            "working_directory": "none",
            "git_repo": "none",
            "source_branch": source_branch,
            "goal": "Verify the first phase of the three-part certification run.",
            "inputs": [requirements_path],
            "context_paths": [],
            "required_output": "Verification pass or named failure.",
            "constraints": ["tester_operation: verify-and-report"],
            "acceptance_criteria": ["Phase one acceptance criteria pass."],
            "depends_on": [],
            "notes": "First of three TESTER tickets in a three-ticket testing-only plan.",
        },
        {
            "sequence": 2,
            "slug": "verify-phase-two",
            "title": "TESTER: Verify phase two",
            "role": "TESTER",
            "assigned_agent": "TESTER",
            "working_directory": "none",
            "git_repo": "none",
            "source_branch": source_branch,
            "goal": "Verify the second phase of the three-part certification run.",
            "inputs": [requirements_path],
            "context_paths": [],
            "required_output": "Verification pass or named failure.",
            "constraints": ["tester_operation: verify-and-report"],
            "acceptance_criteria": ["Phase two acceptance criteria pass."],
            "depends_on": [1],
            "notes": "Second of three TESTER tickets.",
        },
        {
            "sequence": 3,
            "slug": "verify-phase-three",
            "title": "TESTER: Verify phase three and write report",
            "role": "TESTER",
            "assigned_agent": "TESTER",
            "working_directory": "none",
            "git_repo": "none",
            "source_branch": source_branch,
            "goal": "Verify the third phase and write the final test report.",
            "inputs": [requirements_path],
            "context_paths": [],
            "required_output": "Final test report artifact.",
            "constraints": ["tester_operation: verify-and-report"],
            "acceptance_criteria": ["Report written to artifacts/."],
            "depends_on": [2],
            "notes": "Third of three TESTER tickets; no finalize_mode:report marker (BUG-0068 topology).",
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
# Wake-dispatch harness helpers
# ---------------------------------------------------------------------------


def _build_harness_kanban_root(
    parent: pathlib.Path,
    project_name: str = "cert_proj",
    source_branch: str = "rc/v1.22.4",
) -> pathlib.Path:
    """Build a kanban root configured for the harness wake provider.

    Extends _build_testing_only_kanban_root with [providers] active = harness
    in kanban.cfg so harness.sh passes its own active-provider guard.

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
    intake closure path runs end-to-end in a subprocess; only the LLM
    invocation (provider_invoke_agent) is stubbed.

    Args:
        kanban_root:  Kanban root (must have [providers] active = harness).
        project_root: Project root (projects/<name>/ inside kanban_root).
        max_tasks:    --max-tasks argument.
        agent:        --agent argument (default: tester).

    Returns:
        subprocess.CompletedProcess with returncode, stdout, stderr.
    """
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["PGAI_PROJECT_ROOT"] = str(project_root)
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
    """Return the ## Status value from an intake requirements doc."""
    if not req_path.is_file():
        return ""
    text = req_path.read_text(encoding="utf-8")
    m = re.search(r"^##\s+Status\s*\n\s*(\S+)", text, re.MULTILINE)
    return m.group(1).strip().lower() if m else ""


def _plant_task_state(status_file: pathlib.Path, state: str) -> None:
    """Write a specific ## State value into a status.md file.

    Used to pre-seed sibling task states before running the harness,
    simulating mid-run (WORKING) or prior-run (WONT-DO) scenarios.

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
        text_new = text + f"\n## State\n{state}\n"
    status_file.write_text(text_new, encoding="utf-8")


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestThreeTicketWakeDispatchClosure:
    """Wake-dispatch certification fixture: three-ticket testing-only closure.

    Each test exercises the real wake substrate (close_intake_on_finalize_report
    + completion census from CODER-20260715-007, BUG-0068 fix) via subprocess
    invocation of wake/harness.sh against a three-ticket PM plan.

    BUG-0068 production diagnosis anchor (must be caught by these tests):
      [2026-07-14T14:04:10+00:00] wake(tester): task
      TESTER-20260714-008-verify-pvg-closure-check:
      close_intake_on_finalize_report: finalize_mode:report not found
      in README constraints — skipping intake closure (not a
      finalize=report terminal ticket)

    Three-ticket topology: pm_materialize produces three TESTER tickets,
    none with a 'finalize_mode:report' constraint marker.  The census-based
    fix must detect all siblings terminal and fire closure exactly once when
    the last ticket completes.

    None of the tests call close_intake_on_finalize_report directly from Python.
    """

    def test_wake_dispatch_closes_intake_on_all_terminal_tickets(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Harness run closes the intake item when all three TESTER siblings reach DONE.

        With three TESTER work tickets (no injected companion for a three-ticket
        plan), the harness processes all three.  When all reach DONE, the census
        returns ok:3 and close_intake_on_finalize_report closes the intake item.

        This is the strongest regression test for BUG-0068: three PM-authored
        tickets, none with the 'finalize_mode:report' marker — the old guard
        would skip closure on every ticket; the census fix must succeed.

        Asserts:
        - Intake ## Status == 'done' after harness run.
        - Wake log contains 'closed done' (census confirmation).
        - At least one TESTER task artifact dir contains report.md.
        """
        source_branch = "rc/v1.22.4"
        root = _build_harness_kanban_root(tmp_path, source_branch=source_branch)
        proj = root / "projects" / "cert_proj"
        req_path = proj / "requirements" / "v1.0.0-three-ticket-testing-run.md"

        plan = _build_three_tester_plan(
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

        # Run the harness: processes all BACKLOG TESTER tasks.
        wake_result = _run_wake_harness(root, proj, max_tasks=10)
        combined_log = wake_result.stdout + wake_result.stderr

        assert wake_result.returncode == 0, (
            f"wake/harness.sh exited {wake_result.returncode}.\n"
            f"stdout: {wake_result.stdout}\nstderr: {wake_result.stderr}"
        )

        # Guard 1: intake ## Status must be 'done'.
        intake_status = _get_intake_status(req_path)
        assert intake_status == "done", (
            f"Expected intake ## Status == 'done' after harness wake dispatch "
            f"of three TESTER tickets; got {intake_status!r}.\n"
            f"Requirements file: {req_path}\n"
            f"Content:\n{req_path.read_text(encoding='utf-8') if req_path.is_file() else 'FILE MISSING'}\n"
            f"Wake log:\n{combined_log}"
        )

        # Guard 2: wake log must contain the closure confirmation.
        assert "closed done" in combined_log, (
            f"Expected 'closed done' in wake log "
            f"(close_intake_on_finalize_report census confirmation).\n"
            f"Wake log:\n{combined_log}"
        )

        # Guard 3: at least one TESTER task artifact dir has report.md.
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

        Pre-seeds two TESTER siblings to WORKING, then runs the harness with
        max-tasks=1.  The census sees WORKING siblings and emits
        'census incomplete — skipping' instead of closing the intake.

        Asserts:
        - Intake ## Status remains 'running'.
        - Wake log contains 'census incomplete'.
        """
        source_branch = "rc/v1.22.4"
        root = _build_harness_kanban_root(tmp_path, source_branch=source_branch)
        proj = root / "projects" / "cert_proj"
        req_path = proj / "requirements" / "v1.0.0-three-ticket-testing-run.md"

        plan = _build_three_tester_plan(
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

        # Pre-seed two TESTER siblings to WORKING.
        tasks_root = proj / "tasks"
        tester_dirs = sorted(
            d for d in tasks_root.iterdir()
            if d.is_dir() and d.name.startswith("TESTER-")
        )
        assert len(tester_dirs) >= 3, (
            f"Expected at least 3 TESTER task folders; found {len(tester_dirs)}"
        )
        _plant_task_state(tester_dirs[1] / "status.md", "WORKING")
        _plant_task_state(tester_dirs[2] / "status.md", "WORKING")

        # Run harness with max-tasks=1: processes only the first BACKLOG task.
        wake_result = _run_wake_harness(root, proj, max_tasks=1)
        combined_log = wake_result.stdout + wake_result.stderr

        assert wake_result.returncode == 0, (
            f"wake/harness.sh exited {wake_result.returncode}.\n"
            f"stdout: {wake_result.stdout}\nstderr: {wake_result.stderr}"
        )

        # Guard 1: intake must NOT be closed.
        intake_status = _get_intake_status(req_path)
        assert intake_status != "done", (
            f"Intake ## Status is 'done' but census should have skipped due to "
            f"WORKING siblings.\n"
            f"Content:\n{req_path.read_text(encoding='utf-8') if req_path.is_file() else 'FILE MISSING'}\n"
            f"Wake log:\n{combined_log}"
        )

        # Guard 2: census skip must be logged.
        assert "census incomplete" in combined_log, (
            f"Expected 'census incomplete' in wake log.\n"
            f"Wake log:\n{combined_log}"
        )

    def test_wake_dispatch_closure_fires_with_wontdo_corpse_sibling(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Census treats WONT-DO prior-run corpse siblings as terminal.

        Pre-seeds one TESTER task as WONT-DO (prior-run corpse), then runs the
        harness to complete the remaining two BACKLOG tasks.  When all remaining
        tasks reach DONE, census sees DONE + DONE + WONT-DO = all terminal →
        closes intake.

        Asserts:
        - Intake ## Status == 'done' after harness run.
        - Wake log contains 'closed done'.
        """
        source_branch = "rc/v1.22.4"
        root = _build_harness_kanban_root(tmp_path, source_branch=source_branch)
        proj = root / "projects" / "cert_proj"
        req_path = proj / "requirements" / "v1.0.0-three-ticket-testing-run.md"

        plan = _build_three_tester_plan(
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
        assert len(tester_dirs) >= 3, (
            f"Expected at least 3 TESTER task folders; found {len(tester_dirs)}"
        )
        _plant_task_state(tester_dirs[2] / "status.md", "WONT-DO")

        # Run harness with max-tasks=10: processes all remaining BACKLOG tasks.
        wake_result = _run_wake_harness(root, proj, max_tasks=10)
        combined_log = wake_result.stdout + wake_result.stderr

        assert wake_result.returncode == 0, (
            f"wake/harness.sh exited {wake_result.returncode}.\n"
            f"stdout: {wake_result.stdout}\nstderr: {wake_result.stderr}"
        )

        # Guard 1: intake ## Status must be 'done'.
        intake_status = _get_intake_status(req_path)
        assert intake_status == "done", (
            f"Expected intake ## Status == 'done' after harness wake dispatch "
            f"with WONT-DO corpse sibling; got {intake_status!r}.\n"
            f"Requirements file: {req_path}\n"
            f"Content:\n{req_path.read_text(encoding='utf-8') if req_path.is_file() else 'FILE MISSING'}\n"
            f"Wake log:\n{combined_log}"
        )

        # Guard 2: closure confirmation in wake log.
        assert "closed done" in combined_log, (
            f"Expected 'closed done' in wake log.\n"
            f"Wake log:\n{combined_log}"
        )
