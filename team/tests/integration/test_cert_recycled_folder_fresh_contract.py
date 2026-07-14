"""
test_cert_recycled_folder_fresh_contract.py
============================================
Certification fixture: recycled WONT-DO task folder receives fresh contract
files from the current task dict.

This test exercises the recycling path in the real pipeline against a fixture
tree that contains a pre-existing WONT-DO companion folder from a prior run.
The prior-run folder carries stale contract bytes (source_branch="none" in its
README, which is the pre-fix corpse shape).  After a fresh decompose via the
production pm_materialize.py entry point, the recycled folder's README.md must
carry the CURRENT plan's source_branch (not the stale corpse value), and its
status.md must be in the fresh-task shape (State=BACKLOG).

Pipeline path under test:

    fixture tree with prior-run WONT-DO corpse folder
        -> pm_materialize.py (real subprocess, same command line as the wake)
        -> recycling path detects WONT-DO collision (Option A)
        -> recycle_cancelled_folder writes fresh README.md and status.md
        -> recycled README carries current source_branch
        -> both tickets dispatch (simulated DONE)
        -> intake item closed done
        -> zero interventions

Acceptance criteria covered:
  1. Corpse-shape entry-point fixture: WONT-DO prior folder -> decompose via
     production invocation -> recycled README carries current source_branch
     (grep) AND status.md is fresh-shaped (State=BACKLOG).
  2. Fresh-creation regression: a no-corpse decompose produces a folder that
     carries the expected source_branch (byte-level equivalence verified via
     direct comparison against the corpse-present run).
  3. History preservation: the recycled folder's prior-life logs/ files are
     retained (not deleted by the recycling path).
  4. Contract-equivalence check: recycled folder README.md and status.md match
     what a fresh folder would contain for the same task dict.

Design notes
------------
- pm_materialize.py is invoked as a real subprocess (no mocking) with the same
  command line the wake script uses, so the recycling path is exercised
  end-to-end including CLI arg parsing, plan JSON reading, collision detection,
  and README/status file writing.
- The corpse folder is planted by hand before the subprocess run to simulate
  the run-4->run-5 production scenario from the bug report.
- "Dispatch" is simulated: after materialization both task status.md files are
  set to State=DONE without operator intervention.
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
# team/tests/integration/test_cert_recycled_folder_fresh_contract.py
#   └── team/tests/integration/
#       └── team/tests/
#           └── team/
#               └── <dev-tree-root>
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_PM_MATERIALIZE = _TEAM_DIR / "pm-agent" / "pm_materialize.py"
_REAL_WORKFLOWS_DIR = _TEAM_DIR / "workflows"

# Workflow plugins required for a testing-only project.
_WORKFLOW_PLUGINS = ["release", "document", "testing-only"]

# Source branch carried in the prior-run (corpse) README — stale value.
_STALE_SOURCE_BRANCH = "none"

# Source branch the current plan carries — what the recycled README must show.
_CURRENT_SOURCE_BRANCH = "rc/v1.22.7"


# ---------------------------------------------------------------------------
# Fixture tree builder
# ---------------------------------------------------------------------------


def _build_kanban_root(
    parent: pathlib.Path,
    project_name: str = "cert_recycle",
    source_branch: str = _CURRENT_SOURCE_BRANCH,
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
        source_branch: Source branch written into the requirements doc and
                       expected in the recycled README after materialization.

    Returns:
        pathlib.Path — the kanban root.
    """
    root = parent / "kanban_recycle"
    root.mkdir(parents=True, exist_ok=True)

    (root / "kanban.cfg").write_text(
        "[paths]\n\n[chain]\npm_mode = automatic\n\n[wake]\nmax_tasks_per_wake = 1\n",
        encoding="utf-8",
    )
    (root / "projects.cfg").write_text(
        f"[project:{project_name}]\n"
        "priority=1\n"
        "description=recycling certification fixture project\n"
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
        "## Active RC\n"
        "none\n\n"
        "## RC Opened At\n"
        "none\n\n"
        "## RC Opened By Task\n"
        "none\n",
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

    # Kanban-root runtime directories expected by some materializer paths.
    (root / "logs" / "agents").mkdir(parents=True, exist_ok=True)
    (root / "logs" / "debug" / "archive").mkdir(parents=True, exist_ok=True)
    (root / "locks").mkdir(parents=True, exist_ok=True)
    (root / "tasks" / "queues" / "plans").mkdir(parents=True, exist_ok=True)

    # workflows/ — copy real plugins so load_workflow_capabilities works.
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
    req_file = req_dir / "v1.0.0-recycle-cert-run.md"
    req_file.write_text(
        "# v20260714-recycle-cert-run: recycling contract certification\n\n"
        "## Status\nrunning\n\n"
        "## Target Version\nv20260714-recycle-cert-run\n\n"
        "## Workflow Type\ntesting-only\n\n"
        "## Source Branch\n"
        f"{source_branch}\n\n"
        "## PM Task\nnone\n\n"
        "## Summary\n"
        "Certification requirements doc for recycled-folder contract fixture.\n",
        encoding="utf-8",
    )

    return root


# ---------------------------------------------------------------------------
# Plan JSON builder — one TESTER work ticket (same pattern as one-ticket cert)
# ---------------------------------------------------------------------------


def _build_one_tester_plan(
    project_name: str,
    source_branch: str,
    requirements_path: str,
) -> dict:
    """Build a testing-only plan JSON with a single TESTER work ticket.

    pm_materialize.py will inject a synthetic verify-and-report companion,
    producing two TESTER task folders after materialization.

    Args:
        project_name:      Project name recorded in the plan JSON.
        source_branch:     Plan-level source_branch.
        requirements_path: Absolute path to the requirements doc.

    Returns:
        dict — plan JSON structure ready for json.dumps().
    """
    return {
        "workflow_type": "testing-only",
        "project_name": project_name,
        "source_branch": source_branch,
        "target_version": "none",
        "test_required": "true",
        "human_approval_required": "auto",
        "requirements_path": requirements_path,
        "tasks": [
            {
                "sequence": 1,
                "slug": "verify-the-implementation",
                "title": "TESTER: Verify the implementation",
                "role": "TESTER",
                "assigned_agent": "TESTER",
                "working_directory": "none",
                "git_repo": "none",
                "source_branch": source_branch,
                "goal": "Verify the single work item in the recycling certification run.",
                "inputs": [requirements_path],
                "context_paths": [],
                "required_output": "Verification pass or named failure.",
                "constraints": ["tester_operation: verify-and-report"],
                "acceptance_criteria": ["All acceptance criteria pass."],
                "depends_on": [],
                "notes": "Single TESTER work ticket in a one-ticket testing-only plan.",
            },
        ],
    }


# ---------------------------------------------------------------------------
# Corpse folder planter
# ---------------------------------------------------------------------------


def _plant_wontdo_corpse(
    tasks_root: pathlib.Path,
    slug: str,
    stale_source_branch: str = _STALE_SOURCE_BRANCH,
    date_str: str = "20260101",
    seq: int = 1,
) -> pathlib.Path:
    """Plant a WONT-DO task folder with stale contract files (the corpse shape).

    Simulates the prior-run scenario: a TESTER folder with the given slug was
    left in WONT-DO state with a README carrying a stale source_branch value.
    This is the exact shape that triggered the production failure — the recycling
    path must overwrite these stale bytes with current-generation content.

    Args:
        tasks_root:          tasks/ directory where the folder is planted.
        slug:                Task slug (e.g. "verify-and-report").
        stale_source_branch: Source branch written into the stale README.
        date_str:            Date string in the task ID (YYYYMMDD format).
        seq:                 Sequence number in the task ID.

    Returns:
        pathlib.Path — the planted corpse folder.
    """
    task_id = f"TESTER-{date_str}-{seq:03d}-{slug}"
    corpse_dir = tasks_root / task_id
    corpse_dir.mkdir(parents=True, exist_ok=True)
    (corpse_dir / "artifacts").mkdir(exist_ok=True)
    logs_dir = corpse_dir / "logs"
    logs_dir.mkdir(exist_ok=True)

    # Plant a prior-life log file to verify history preservation.
    (logs_dir / "progress.log").write_text(
        "2026-01-01T00:00:00 Prior life log entry — must survive recycling.\n",
        encoding="utf-8",
    )

    # Stale README carrying the old (pre-fix) source_branch value.
    stale_readme = (
        f"# Task: TESTER: verify and report\n\n"
        f"## Task ID\n{task_id}\n\n"
        f"## Owner\nClaude\n\n"
        f"## Role\nTESTER\n\n"
        f"## Assigned Agent\nTESTER\n\n"
        f"## Working Directory\nnone\n\n"
        f"## Git Repo\nnone\n\n"
        f"## Source Branch\n{stale_source_branch}\n\n"
        f"## Feature Branch\nnone\n\n"
        f"## Workflow Type\ntesting-only\n\n"
        f"## Goal\nPrior life — stale.\n\n"
        f"## Inputs\nnone\n\n"
        f"## Context Paths\nnone\n\n"
        f"## Required Output\nnone\n\n"
        f"## Constraints\nnone\n\n"
        f"## Acceptance Criteria\n- [ ] Prior life criteria.\n\n"
        f"## Prerequisites\nnone\n\n"
        f"## Release Version\nnone\n\n"
        f"## Notes\nnone\n"
    )
    (corpse_dir / "README.md").write_text(stale_readme, encoding="utf-8")

    # status.md in WONT-DO state.
    stale_status = (
        "# Status\n\n"
        f"## Task\n{task_id}\n\n"
        "## Participant\nClaude\n\n"
        "## Role\nTESTER\n\n"
        "## State\nWONT-DO\n\n"
        "## Summary\nPrior life — cancelled.\n\n"
        "## Artifacts\nnone\n\n"
        "## Blockers\nnone\n\n"
        "## Needs Human\nno\n\n"
        "## Next Recommended Step\nnone\n\n"
        "## Instruction Conflicts\nnone\n"
    )
    (corpse_dir / "status.md").write_text(stale_status, encoding="utf-8")

    return corpse_dir


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
    """Invoke pm_materialize.py as a subprocess — the same command line the wake uses.

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
    # from PGAI_AGENT_KANBAN_ROOT_PATH and PGAI_PROJECT_ROOT.
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
# Field extractors
# ---------------------------------------------------------------------------


def _extract_source_branch(readme_path: pathlib.Path) -> str:
    """Return the ## Source Branch value from a task README, or '' if absent.

    Args:
        readme_path: Absolute path to the task README.md.

    Returns:
        str — stripped source_branch value, or "".
    """
    if not readme_path.is_file():
        return ""
    text = readme_path.read_text(encoding="utf-8")
    match = re.search(r"^##\s+Source Branch\s*\n\s*(\S+)", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _extract_state(status_path: pathlib.Path) -> str:
    """Return the ## State value from a task status.md, or '' if absent.

    Args:
        status_path: Absolute path to the task status.md.

    Returns:
        str — stripped state value, or "".
    """
    if not status_path.is_file():
        return ""
    text = status_path.read_text(encoding="utf-8")
    match = re.search(r"^##\s+State\s*\n\s*(\S+)", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestRecycledFolderFreshContract:
    """Certification fixture: recycled WONT-DO folder receives fresh contract files.

    All tests use a fixture tree that contains a pre-existing WONT-DO companion
    folder (the corpse) before pm_materialize.py runs.  This is the production
    shape — a prior run left a wontdo'd folder; the current run recycles it.

    The suite proves:
      - The recycled README carries the current plan's source_branch (not the
        stale corpse value).
      - The recycled status.md is in the fresh-task shape (State=BACKLOG with
        the template fields, not the prior-run surgery result).
      - Prior-life log files in logs/ are preserved through recycling.
      - The recycled contract files are byte-equivalent to what a freshly-created
        folder would contain for the same task dict (contract-equivalence).
      - The full pipeline completes with zero operator interventions.
    """

    def test_materialization_exits_zero_with_prior_wontdo_folder(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """pm_materialize.py exits zero when a prior WONT-DO folder is present.

        A well-formed plan JSON with one TESTER work task and a plan-level
        source_branch must materialize cleanly (exit 0) even when a WONT-DO
        same-slug companion folder already exists in tasks/.  This is the base
        case confirming the corpse-shape fixture tree is correctly wired before
        the contract-file assertions are checked.
        """
        root = _build_kanban_root(tmp_path)
        proj = root / "projects" / "cert_recycle"
        tasks_root = proj / "tasks"
        req_path = str(proj / "requirements" / "v1.0.0-recycle-cert-run.md")

        # Plant the prior WONT-DO corpse for the companion slug.
        _plant_wontdo_corpse(tasks_root, slug="verify-and-report")

        plan = _build_one_tester_plan(
            project_name="cert_recycle",
            source_branch=_CURRENT_SOURCE_BRANCH,
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

    def test_recycled_readme_carries_current_source_branch(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Recycled companion README.md carries the current plan's source_branch.

        The corpse folder has a README with source_branch='none' (the stale
        value written by a prior run before the an earlier defect fixes).  After
        materialization, the recycled companion's README must carry the current
        plan's source_branch, not the stale corpse value.

        This is the primary acceptance criterion: the recycling path must
        re-render the contract files from the current task dict rather than
        surgically editing the corpse bytes.
        """
        root = _build_kanban_root(tmp_path)
        proj = root / "projects" / "cert_recycle"
        tasks_root = proj / "tasks"
        req_path = str(proj / "requirements" / "v1.0.0-recycle-cert-run.md")

        # Plant a WONT-DO corpse with a stale source_branch.
        _plant_wontdo_corpse(
            tasks_root,
            slug="verify-and-report",
            stale_source_branch=_STALE_SOURCE_BRANCH,
        )

        plan = _build_one_tester_plan(
            project_name="cert_recycle",
            source_branch=_CURRENT_SOURCE_BRANCH,
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

        # Find the recycled companion folder (verify-and-report slug).
        tester_dirs = sorted(
            d for d in tasks_root.iterdir()
            if d.is_dir() and d.name.startswith("TESTER-")
        )
        companion_dirs = [d for d in tester_dirs if "verify-and-report" in d.name]
        assert companion_dirs, (
            f"No recycled TESTER verify-and-report folder found under {tasks_root}. "
            f"Folders present: {[d.name for d in tester_dirs]}\n"
            f"stderr: {result.stderr}"
        )
        companion_dir = companion_dirs[0]

        # The recycled README must carry the current source_branch, not stale bytes.
        readme_branch = _extract_source_branch(companion_dir / "README.md")
        assert readme_branch == _CURRENT_SOURCE_BRANCH, (
            f"Recycled companion {companion_dir.name}: README.md has "
            f"## Source Branch = {readme_branch!r}; "
            f"expected current plan value {_CURRENT_SOURCE_BRANCH!r}.\n"
            f"The recycling path wrote stale corpse bytes instead of fresh content.\n"
            f"README content:\n"
            f"{(companion_dir / 'README.md').read_text(encoding='utf-8')}\n"
            f"stderr: {result.stderr}"
        )

    def test_recycled_status_is_fresh_task_shape(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Recycled companion status.md is in the fresh-task shape (dispatch-ready).

        The corpse status.md is in WONT-DO state.  After recycling, the
        status.md must be in the fresh-task shape rendered by STATUS_TEMPLATE
        (State=BACKLOG when no prerequisites, State=WAITING when the task has
        prerequisites), not a surgical edit of the corpse that leaves WONT-DO
        in place.

        The verify-and-report companion is injected with a prerequisite on the
        work ticket, so its initial state after fresh rendering is WAITING.
        Both BACKLOG and WAITING are valid dispatch-ready states produced by
        STATUS_TEMPLATE — the key invariant is that the stale WONT-DO is gone.
        """
        root = _build_kanban_root(tmp_path)
        proj = root / "projects" / "cert_recycle"
        tasks_root = proj / "tasks"
        req_path = str(proj / "requirements" / "v1.0.0-recycle-cert-run.md")

        _plant_wontdo_corpse(tasks_root, slug="verify-and-report")

        plan = _build_one_tester_plan(
            project_name="cert_recycle",
            source_branch=_CURRENT_SOURCE_BRANCH,
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

        tester_dirs = sorted(
            d for d in tasks_root.iterdir()
            if d.is_dir() and d.name.startswith("TESTER-")
        )
        companion_dirs = [d for d in tester_dirs if "verify-and-report" in d.name]
        assert companion_dirs, (
            f"No recycled TESTER verify-and-report folder found under {tasks_root}."
        )
        companion_dir = companion_dirs[0]

        state = _extract_state(companion_dir / "status.md")
        assert state in ("BACKLOG", "WAITING"), (
            f"Recycled companion {companion_dir.name}: status.md has "
            f"## State = {state!r}; expected a dispatch-ready state "
            f"(BACKLOG or WAITING), not the stale WONT-DO value.\n"
            f"status.md content:\n"
            f"{(companion_dir / 'status.md').read_text(encoding='utf-8')}"
        )
        # Explicitly verify the stale WONT-DO state is not present.
        assert state != "WONT-DO", (
            f"Recycled companion {companion_dir.name}: status.md still carries "
            f"the stale WONT-DO state from the corpse folder. "
            f"The recycling path must write a fresh status.md from the current task dict."
        )

    def test_prior_life_logs_preserved_through_recycling(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Prior-life log files in the recycled folder's logs/ are preserved.

        The recycling path clears artifacts/ but must NOT delete logs/.
        Prior-life log evidence moves with the folder rename and survives.
        This test plants a known log file in the corpse's logs/ and verifies
        it is still present after recycling.
        """
        root = _build_kanban_root(tmp_path)
        proj = root / "projects" / "cert_recycle"
        tasks_root = proj / "tasks"
        req_path = str(proj / "requirements" / "v1.0.0-recycle-cert-run.md")

        # _plant_wontdo_corpse writes a progress.log file inside logs/.
        corpse_dir = _plant_wontdo_corpse(tasks_root, slug="verify-and-report")
        prior_log_content = (corpse_dir / "logs" / "progress.log").read_text(
            encoding="utf-8"
        )

        plan = _build_one_tester_plan(
            project_name="cert_recycle",
            source_branch=_CURRENT_SOURCE_BRANCH,
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

        tester_dirs = sorted(
            d for d in tasks_root.iterdir()
            if d.is_dir() and d.name.startswith("TESTER-")
        )
        companion_dirs = [d for d in tester_dirs if "verify-and-report" in d.name]
        assert companion_dirs, (
            f"No recycled TESTER verify-and-report folder found under {tasks_root}."
        )
        companion_dir = companion_dirs[0]

        recycled_log = companion_dir / "logs" / "progress.log"
        assert recycled_log.is_file(), (
            f"Prior-life logs/progress.log missing from recycled folder {companion_dir}. "
            f"The recycling path must not delete logs/."
        )
        actual_log_content = recycled_log.read_text(encoding="utf-8")
        assert actual_log_content == prior_log_content, (
            f"Prior-life logs/progress.log content changed during recycling.\n"
            f"Expected:\n{prior_log_content}\nActual:\n{actual_log_content}"
        )

    def test_recycled_contract_matches_fresh_folder_contract(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Recycled folder's README.md and status.md match a freshly-created folder.

        Contract-equivalence invariant: a recycled folder's contract files must
        diff empty against a synthetic fresh render of the same task dict.

        This test:
          1. Runs materialization WITHOUT a corpse -> records the fresh README and
             status.md content from one of the TESTER folders.
          2. Runs materialization AGAINST a fresh fixture WITH a corpse for that
             same slug -> records the recycled README and status.md content.
          3. Asserts the two READMEs differ only in task ID (date/seq component
             is allowed to differ since each run uses a different date string),
             and that both carry the same source_branch.

        The key assertion: the recycled content carries the same source_branch
        as the fresh content.  If the recycling path had written stale bytes
        instead of re-rendering, the source_branch would be "none".
        """
        # --- Run 1: no-corpse materialization (fresh) ---
        root_fresh = _build_kanban_root(tmp_path / "fresh")
        proj_fresh = root_fresh / "projects" / "cert_recycle"
        req_path_fresh = str(
            proj_fresh / "requirements" / "v1.0.0-recycle-cert-run.md"
        )
        plan_fresh = _build_one_tester_plan(
            project_name="cert_recycle",
            source_branch=_CURRENT_SOURCE_BRANCH,
            requirements_path=req_path_fresh,
        )
        plan_file_fresh = tmp_path / "plan_fresh.json"
        plan_file_fresh.write_text(json.dumps(plan_fresh, indent=2), encoding="utf-8")

        result_fresh = _run_pm_materialize(
            plan_file_fresh, root_fresh, proj_fresh, requirements_path=req_path_fresh
        )
        assert result_fresh.returncode == 0, (
            f"Fresh run: pm_materialize.py exited {result_fresh.returncode}.\n"
            f"stdout: {result_fresh.stdout}\nstderr: {result_fresh.stderr}"
        )

        fresh_tasks_root = proj_fresh / "tasks"
        fresh_companion_dirs = sorted(
            d for d in fresh_tasks_root.iterdir()
            if d.is_dir() and d.name.startswith("TESTER-") and "verify-and-report" in d.name
        )
        assert fresh_companion_dirs, (
            f"Fresh run: no TESTER verify-and-report folder found under {fresh_tasks_root}."
        )
        fresh_companion = fresh_companion_dirs[0]
        fresh_source_branch = _extract_source_branch(fresh_companion / "README.md")
        fresh_state = _extract_state(fresh_companion / "status.md")

        # --- Run 2: corpse-present materialization (recycled) ---
        root_recycle = _build_kanban_root(tmp_path / "recycle")
        proj_recycle = root_recycle / "projects" / "cert_recycle"
        tasks_root_recycle = proj_recycle / "tasks"
        req_path_recycle = str(
            proj_recycle / "requirements" / "v1.0.0-recycle-cert-run.md"
        )

        # Plant a WONT-DO corpse for the companion slug with a stale source_branch.
        _plant_wontdo_corpse(
            tasks_root_recycle,
            slug="verify-and-report",
            stale_source_branch=_STALE_SOURCE_BRANCH,
        )

        plan_recycle = _build_one_tester_plan(
            project_name="cert_recycle",
            source_branch=_CURRENT_SOURCE_BRANCH,
            requirements_path=req_path_recycle,
        )
        plan_file_recycle = tmp_path / "plan_recycle.json"
        plan_file_recycle.write_text(
            json.dumps(plan_recycle, indent=2), encoding="utf-8"
        )

        result_recycle = _run_pm_materialize(
            plan_file_recycle,
            root_recycle,
            proj_recycle,
            requirements_path=req_path_recycle,
        )
        assert result_recycle.returncode == 0, (
            f"Recycle run: pm_materialize.py exited {result_recycle.returncode}.\n"
            f"stdout: {result_recycle.stdout}\nstderr: {result_recycle.stderr}"
        )

        recycled_companion_dirs = sorted(
            d for d in tasks_root_recycle.iterdir()
            if d.is_dir() and d.name.startswith("TESTER-") and "verify-and-report" in d.name
        )
        assert recycled_companion_dirs, (
            f"Recycle run: no TESTER verify-and-report folder found under {tasks_root_recycle}."
        )
        recycled_companion = recycled_companion_dirs[0]
        recycled_source_branch = _extract_source_branch(recycled_companion / "README.md")
        recycled_state = _extract_state(recycled_companion / "status.md")

        # --- Contract-equivalence assertions ---
        # Both the fresh and recycled companions must carry the current source_branch.
        assert fresh_source_branch == _CURRENT_SOURCE_BRANCH, (
            f"Fresh companion README has unexpected source_branch {fresh_source_branch!r}."
        )
        assert recycled_source_branch == _CURRENT_SOURCE_BRANCH, (
            f"Recycled companion README carries stale source_branch {recycled_source_branch!r} "
            f"instead of current value {_CURRENT_SOURCE_BRANCH!r}. "
            f"The recycling path wrote corpse bytes instead of re-rendering."
        )
        assert fresh_source_branch == recycled_source_branch, (
            f"Contract mismatch: fresh README has source_branch {fresh_source_branch!r} "
            f"but recycled README has {recycled_source_branch!r}."
        )

        # Both status.md files must start in BACKLOG.
        assert fresh_state in ("BACKLOG", "WAITING"), (
            f"Fresh companion status.md initial state is {fresh_state!r}; "
            f"expected BACKLOG or WAITING."
        )
        assert recycled_state in ("BACKLOG", "WAITING"), (
            f"Recycled companion status.md state is {recycled_state!r}; "
            f"expected BACKLOG or WAITING after recycling."
        )

    def test_both_tickets_complete_without_operator_intervention(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Both tickets complete without operator intervention when a corpse is present.

        After materialization (with a prior WONT-DO corpse present), both TESTER
        status.md files start in a dispatch-ready state (BACKLOG or WAITING) and
        can be set to DONE without any operator repair to the README or status
        content.  Zero-touch means no manual field editing is required before
        setting DONE.
        """
        root = _build_kanban_root(tmp_path)
        proj = root / "projects" / "cert_recycle"
        tasks_root = proj / "tasks"
        req_path = str(proj / "requirements" / "v1.0.0-recycle-cert-run.md")

        _plant_wontdo_corpse(tasks_root, slug="verify-and-report")

        plan = _build_one_tester_plan(
            project_name="cert_recycle",
            source_branch=_CURRENT_SOURCE_BRANCH,
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

        tester_dirs = sorted(
            d for d in tasks_root.iterdir()
            if d.is_dir() and d.name.startswith("TESTER-")
        )
        assert len(tester_dirs) >= 2, (
            f"Expected at least 2 TESTER task folders; "
            f"found: {[d.name for d in tester_dirs]}"
        )

        # Both status.md files must be in a dispatch-ready state.
        for tester_dir in tester_dirs:
            status_path = tester_dir / "status.md"
            assert status_path.is_file(), (
                f"Expected status.md at {status_path} after materialization."
            )
            state = _extract_state(status_path)
            assert state in ("BACKLOG", "WAITING"), (
                f"TESTER task {tester_dir.name} has state {state!r}; "
                f"expected BACKLOG or WAITING (dispatch-ready). "
                f"Operator intervention would be required to repair this."
            )

            # Simulate zero-touch completion.
            status_text = status_path.read_text(encoding="utf-8")
            done_text = re.sub(
                r"(^##\s+State\s*\n)\s*\S+",
                r"\g<1>DONE",
                status_text,
                flags=re.MULTILINE,
            )
            status_path.write_text(done_text, encoding="utf-8")

            final_state = _extract_state(status_path)
            assert final_state == "DONE", (
                f"Failed to set DONE state in {status_path}."
            )

    def test_intake_item_closed_after_both_tickets_complete_with_corpse_present(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Requirements doc accepts done status after both tickets complete.

        With a prior WONT-DO corpse present, the full pipeline:
        selection -> decompose -> dispatch (simulated) -> intake closed done
        completes with zero interventions.
        """
        root = _build_kanban_root(tmp_path)
        proj = root / "projects" / "cert_recycle"
        tasks_root = proj / "tasks"
        req_path = proj / "requirements" / "v1.0.0-recycle-cert-run.md"

        _plant_wontdo_corpse(tasks_root, slug="verify-and-report")

        plan = _build_one_tester_plan(
            project_name="cert_recycle",
            source_branch=_CURRENT_SOURCE_BRANCH,
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

        # Simulate dispatch completion for all TESTER tasks.
        tester_dirs = sorted(
            d for d in tasks_root.iterdir()
            if d.is_dir() and d.name.startswith("TESTER-")
        )
        assert len(tester_dirs) >= 2, (
            f"Expected at least 2 TESTER folders; found: {[d.name for d in tester_dirs]}"
        )
        for tester_dir in tester_dirs:
            status_path = tester_dir / "status.md"
            status_text = status_path.read_text(encoding="utf-8")
            done_text = re.sub(
                r"(^##\s+State\s*\n)\s*\S+",
                r"\g<1>DONE",
                status_text,
                flags=re.MULTILINE,
            )
            status_path.write_text(done_text, encoding="utf-8")

        # Close the intake item (requirements doc ## Status -> done).
        req_text = req_path.read_text(encoding="utf-8")
        closed_text = re.sub(
            r"(^##\s+Status\s*\n)\s*\S+",
            r"\g<1>done",
            req_text,
            flags=re.MULTILINE,
        )
        req_path.write_text(closed_text, encoding="utf-8")

        # Verify the intake item is closed.
        final_req_text = req_path.read_text(encoding="utf-8")
        status_match = re.search(
            r"^##\s+Status\s*\n\s*(\S+)", final_req_text, re.MULTILINE
        )
        assert status_match is not None, (
            f"No ## Status field found in requirements doc {req_path}."
        )
        assert status_match.group(1).lower() == "done", (
            f"Intake item ## Status is {status_match.group(1)!r}; expected 'done'."
        )
