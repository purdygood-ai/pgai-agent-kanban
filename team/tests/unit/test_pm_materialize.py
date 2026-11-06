"""
test_pm_materialize.py
======================
Behavioral unit tests for team/pm-agent/pm_materialize.py.

Covers the assembly logic of the materializer:
  - Release-workflow CM bookend injection (CM-open-rc, TESTER, CM-release).
  - Source-branch override to rc/<version> for non-CM-open tasks.
  - git_repo defensive guard: blank or hallucinated PM value overridden by
    the canonical URL from project.cfg [project] git_repo_url.
  - depends_on sequence-to-task-id resolution via resolve_prerequisites().
  - WAITING vs BACKLOG initial state based on prerequisite presence.
  - Queue marker selection ('W' for tasks with prereqs, ' ' for tasks without).
  - Feature-workflow assembly (CODER create-shared-branch, no CM bookends).
  - Utility functions: format_list, format_checklist, normalize_workspace_value,
    validate_model_override, compute_plan_hash.
  - Bundled-item coverage: a plan leaving a ## Bundled Items entry named by
    no ticket and not under needs_operator is refused dual-channel and
    writes nothing; needs_operator, ride-along and a requirement without
    the section pass. These cases run the real CLI as a subprocess against
    a throwaway kanban root with all five root variables set or cleared.

All filesystem writes target tmp_path (never bare /tmp).
No git calls are exercised — functions that read a git dev tree are not
invoked here.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

from pgai_agent_kanban.lib.error_contract import parse_error_contract
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.overwatch import inbox
from pgai_agent_kanban.task_status import read_section
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.fixtures.two_project import add_registered_project, build_two_project_root

# ---------------------------------------------------------------------------
# Import with installed-package / direct-checkout fallback
# ---------------------------------------------------------------------------
try:
    import pm_agent.pm_materialize as pm  # installed via pm_agent package
except ImportError:
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent / "pm-agent"))
    import pm_materialize as pm  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------


def _minimal_task(
    seq: int = 1, slug: str = "my-task", role: str = "CODER", git_repo: str = "git@github.com:org/repo.git"
) -> dict:
    """Return a minimal task dict resembling PM's output."""
    return {
        "sequence": seq,
        "slug": slug,
        "title": f"Task {seq}",
        "role": role,
        "assigned_agent": role,
        "working_directory": "$PGAI_DEV_TREE_PATH",
        "git_repo": git_repo,
        "source_branch": "main",
        "goal": "Do the work.",
        "inputs": ["requirements.md"],
        "context_paths": [],
        "required_output": "Done.",
        "constraints": ["Be careful."],
        "acceptance_criteria": ["It works."],
        "depends_on": [],
        "prerequisite_ids": [],
        "notes": "none",
    }


def _make_tasks(n: int = 2) -> list:
    """Return a list of n minimal feature tasks."""
    return [_minimal_task(seq=i + 1, slug=f"task-{i + 1}") for i in range(n)]


def _stamp_ids(tasks: list, date_str: str = "20260101") -> list:
    """Assign deterministic task_ids to tasks (simulates assign_task_ids output)."""
    for i, task in enumerate(tasks):
        role = task.get("role", "CODER").upper()
        slug = task.get("slug", f"task-{i + 1}")
        task["task_id"] = f"{role}-{date_str}-{i + 1:03d}-{slug}"
        task.setdefault("prerequisite_ids", [])
    return tasks


# ---------------------------------------------------------------------------
# resolve_prerequisites — depends_on sequence-to-task-id resolution
# ---------------------------------------------------------------------------


def test_resolve_prerequisites_converts_sequence_to_task_id() -> None:
    """resolve_prerequisites converts depends_on integers to full task IDs."""
    tasks = [
        {"sequence": 1, "task_id": "CODER-20260101-001-alpha", "depends_on": [], "prerequisite_ids": []},
        {"sequence": 2, "task_id": "CODER-20260101-002-beta", "depends_on": [1], "prerequisite_ids": []},
    ]
    pm.resolve_prerequisites(tasks)
    assert "CODER-20260101-001-alpha" in tasks[1]["prerequisite_ids"]


def test_resolve_prerequisites_empty_depends_on_leaves_empty_list() -> None:
    """resolve_prerequisites produces an empty prerequisite_ids for tasks with no deps."""
    tasks = [
        {"sequence": 1, "task_id": "CODER-20260101-001-solo", "depends_on": [], "prerequisite_ids": []},
    ]
    pm.resolve_prerequisites(tasks)
    assert tasks[0]["prerequisite_ids"] == []


def test_resolve_prerequisites_merges_with_existing_prerequisite_ids() -> None:
    """resolve_prerequisites keeps pre-existing prerequisite_ids alongside new ones."""
    tasks = [
        {"sequence": 1, "task_id": "CM-20260101-001-open-rc", "depends_on": [], "prerequisite_ids": []},
        {
            "sequence": 2,
            "task_id": "CODER-20260101-002-feat",
            "depends_on": [1],
            "prerequisite_ids": ["CM-20260101-001-open-rc"],
        },
    ]
    pm.resolve_prerequisites(tasks)
    prereqs = tasks[1]["prerequisite_ids"]
    # The CM-open entry must appear exactly once (dedup)
    assert prereqs.count("CM-20260101-001-open-rc") == 1


def test_resolve_prerequisites_multi_dep_task_has_all_ids() -> None:
    """resolve_prerequisites resolves multiple depends_on sequences for one task."""
    tasks = [
        {"sequence": 1, "task_id": "CODER-20260101-001-a", "depends_on": [], "prerequisite_ids": []},
        {"sequence": 2, "task_id": "CODER-20260101-002-b", "depends_on": [], "prerequisite_ids": []},
        {"sequence": 3, "task_id": "TESTER-20260101-003-v", "depends_on": [1, 2], "prerequisite_ids": []},
    ]
    pm.resolve_prerequisites(tasks)
    prereqs = tasks[2]["prerequisite_ids"]
    assert "CODER-20260101-001-a" in prereqs
    assert "CODER-20260101-002-b" in prereqs


# ---------------------------------------------------------------------------
# inject_cm_bookends — release-workflow bookend injection
# ---------------------------------------------------------------------------


def test_inject_cm_bookends_prepends_cm_open_task() -> None:
    """inject_cm_bookends adds a CM-open-rc task at position 0 in the tasks list."""
    tasks = _stamp_ids(_make_tasks(2))
    pm.inject_cm_bookends(tasks, "v0.5.0", "20260101", 100, "CLAUDE")
    assert tasks[0].get("cm_operation") == "open-rc"


def test_inject_cm_bookends_appends_tester_task() -> None:
    """inject_cm_bookends injects a TESTER task after the feature tasks."""
    tasks = _stamp_ids(_make_tasks(2))
    pm.inject_cm_bookends(tasks, "v0.5.0", "20260101", 100, "CLAUDE")
    tester_tasks = [t for t in tasks if t.get("role") == "TESTER"]
    assert len(tester_tasks) == 1


def test_inject_cm_bookends_tester_goal_names_autonomous_filing() -> None:
    """The injected TESTER goal names verifying, reporting and filing gaps and bugs.

    TESTER files every gap and bug itself and never escalates to a human, so
    the goal it reads first must say that rather than defer to a reviewer.
    """
    tasks = _stamp_ids(_make_tasks(2))
    pm.inject_cm_bookends(tasks, "v0.5.0", "20260101", 100, "CLAUDE")
    tester = next(t for t in tasks if t.get("role") == "TESTER")
    goal = tester["goal"]
    assert "human review" not in goal.lower(), goal
    assert "v0.5.0" in goal, goal
    assert "requirements document" in goal, goal
    assert "verification report" in goal, goal
    assert "gaps or bugs" in goal, goal
    assert "Path C" in goal, goal


def test_inject_cm_bookends_appends_cm_release_task() -> None:
    """inject_cm_bookends appends a CM-release task last in the tasks list."""
    tasks = _stamp_ids(_make_tasks(2))
    pm.inject_cm_bookends(tasks, "v0.5.0", "20260101", 100, "CLAUDE")
    assert tasks[-1].get("cm_operation") == "release"


def test_inject_cm_bookends_cm_open_has_no_prerequisites() -> None:
    """The injected CM-open-rc task starts with no prerequisites."""
    tasks = _stamp_ids(_make_tasks(1))
    pm.inject_cm_bookends(tasks, "v0.5.0", "20260101", 100, "CLAUDE")
    cm_open = tasks[0]
    assert cm_open.get("prerequisite_ids") == []


def test_inject_cm_bookends_feature_tasks_depend_on_cm_open() -> None:
    """inject_cm_bookends adds CM-open ID to every feature task's prerequisite_ids."""
    tasks = _stamp_ids(_make_tasks(2))
    pm.inject_cm_bookends(tasks, "v0.5.0", "20260101", 100, "CLAUDE")
    cm_open_id = tasks[0]["task_id"]
    # Feature tasks are at positions 1 and 2 (between CM-open and TESTER/CM-release)
    feature_task = tasks[1]
    assert cm_open_id in feature_task.get("prerequisite_ids", [])


def test_inject_cm_bookends_tester_depends_on_all_feature_tasks() -> None:
    """The TESTER task's prerequisite_ids contains all feature task IDs."""
    tasks = _stamp_ids(_make_tasks(2))
    feature_ids = [t["task_id"] for t in tasks]
    pm.inject_cm_bookends(tasks, "v0.5.0", "20260101", 100, "CLAUDE")
    tester = next(t for t in tasks if t.get("role") == "TESTER")
    for fid in feature_ids:
        assert fid in tester["prerequisite_ids"]


def test_inject_cm_bookends_cm_release_depends_on_tester() -> None:
    """The CM-release task depends on the TESTER task when test_required=True."""
    tasks = _stamp_ids(_make_tasks(1))
    pm.inject_cm_bookends(tasks, "v0.5.0", "20260101", 100, "CLAUDE", test_required=True)
    cm_release = tasks[-1]
    tester = next(t for t in tasks if t.get("role") == "TESTER")
    assert tester["task_id"] in cm_release["prerequisite_ids"]


@pytest.mark.parametrize(
    "human_approval_required, expected_human_task_count",
    [
        ("required", 1),  # HUMAN-APPROVE task injected
        ("auto", 0),  # no HUMAN-APPROVE task
        ("invalid-value", 0),  # unrecognized value defaults to 'auto' (no HUMAN gate)
    ],
)
def test_inject_cm_bookends_human_approve_count(human_approval_required: str, expected_human_task_count: int) -> None:
    """inject_cm_bookends injects a HUMAN-APPROVE task only when human_approval_required='required'."""
    tasks = _stamp_ids(_make_tasks(1))
    pm.inject_cm_bookends(tasks, "v0.5.0", "20260101", 100, "CLAUDE", human_approval_required=human_approval_required)
    human_tasks = [t for t in tasks if t.get("role") == "HUMAN"]
    assert len(human_tasks) == expected_human_task_count


def test_inject_cm_bookends_no_tester_when_test_not_required() -> None:
    """inject_cm_bookends skips TESTER when test_required=False."""
    tasks = _stamp_ids(_make_tasks(1))
    pm.inject_cm_bookends(tasks, "v0.5.0", "20260101", 100, "CLAUDE", test_required=False)
    tester_tasks = [t for t in tasks if t.get("role") == "TESTER"]
    assert len(tester_tasks) == 0


def test_inject_cm_bookends_returns_empty_for_none_version() -> None:
    """inject_cm_bookends returns [] when target_version is 'none' (caller error guard)."""
    tasks = _stamp_ids(_make_tasks(1))
    result = pm.inject_cm_bookends(tasks, "none", "20260101", 100, "CLAUDE")
    assert result == []


def test_inject_cm_bookends_returns_empty_for_empty_version() -> None:
    """inject_cm_bookends returns [] when target_version is empty string."""
    tasks = _stamp_ids(_make_tasks(1))
    result = pm.inject_cm_bookends(tasks, "", "20260101", 100, "CLAUDE")
    assert result == []


# ---------------------------------------------------------------------------
# determine_source_branch — rc/<version> override for release workflow
# ---------------------------------------------------------------------------


def test_determine_source_branch_returns_rc_version_for_release_tasks(
    tmp_path: pathlib.Path,
) -> None:
    """determine_source_branch returns rc/<version> for CODER tasks in a release workflow."""
    task = _minimal_task(slug="feat", role="CODER")
    result = pm.determine_source_branch(task, str(tmp_path), workflow_type="release", target_version="v0.5.0")
    assert result == "rc/v0.5.0"


def test_determine_source_branch_returns_main_for_cm_open_task(
    tmp_path: pathlib.Path,
) -> None:
    """determine_source_branch returns 'main' for the CM-open-rc task (creates the RC branch from main)."""
    task = {
        "slug": "open-rc-0-5-0",
        "role": "CM",
        "assigned_agent": "CM",
        "source_branch": "main",
        "cm_operation": "open-rc",
    }
    result = pm.determine_source_branch(task, str(tmp_path), workflow_type="release", target_version="v0.5.0")
    assert result == "main"


def test_determine_source_branch_release_tester_gets_rc_branch(
    tmp_path: pathlib.Path,
) -> None:
    """determine_source_branch returns rc/<version> for TESTER tasks in a release workflow."""
    task = {
        "slug": "verify-0-5-0",
        "role": "TESTER",
        "assigned_agent": "TESTER",
        "source_branch": "main",
    }
    result = pm.determine_source_branch(task, str(tmp_path), workflow_type="release", target_version="v0.5.0")
    assert result == "rc/v0.5.0"


def test_determine_source_branch_release_writer_gets_rc_branch(
    tmp_path: pathlib.Path,
) -> None:
    """determine_source_branch returns rc/<version> for WRITER tasks in a release workflow."""
    task = {
        "slug": "docs",
        "role": "WRITER",
        "assigned_agent": "WRITER",
        "source_branch": "main",
    }
    result = pm.determine_source_branch(task, str(tmp_path), workflow_type="release", target_version="v0.5.0")
    assert result == "rc/v0.5.0"


def test_determine_source_branch_feature_workflow_honors_task_source_branch(
    tmp_path: pathlib.Path,
) -> None:
    """determine_source_branch returns the task's own source_branch for feature workflows."""
    task = _minimal_task(slug="feat", role="CODER")
    task["source_branch"] = "feature/my-shared-branch"
    result = pm.determine_source_branch(task, str(tmp_path), workflow_type="feature", target_version=None)
    assert result == "feature/my-shared-branch"


def test_determine_source_branch_feature_workflow_falls_back_to_main(
    tmp_path: pathlib.Path,
) -> None:
    """determine_source_branch returns 'main' when no task source_branch and no active RC."""
    task = _minimal_task(slug="feat", role="CODER")
    task["source_branch"] = ""
    # No release-state.md in tmp_path — get_active_rc returns None.
    result = pm.determine_source_branch(task, str(tmp_path), workflow_type="feature", target_version=None)
    assert result == "main"


def test_determine_source_branch_with_branch_prefix(
    tmp_path: pathlib.Path,
) -> None:
    """determine_source_branch prepends branch_prefix when set in project.cfg."""
    (tmp_path / "project.cfg").write_text("[project]\nbranch_prefix = myorg\n", encoding="utf-8")
    task = _minimal_task(slug="feat", role="CODER")
    result = pm.determine_source_branch(task, str(tmp_path), workflow_type="release", target_version="v0.5.0")
    assert result == "myorgrc/v0.5.0"


def test_determine_source_branch_no_target_version_returns_main(
    tmp_path: pathlib.Path,
) -> None:
    """determine_source_branch returns 'main' for release workflow without target_version."""
    task = _minimal_task(slug="feat", role="CODER")
    result = pm.determine_source_branch(task, str(tmp_path), workflow_type="release", target_version=None)
    assert result == "main"


# ---------------------------------------------------------------------------
# apply_git_repo_override — git_repo defensive guard (an earlier defect)
# ---------------------------------------------------------------------------


def test_apply_git_repo_override_replaces_hallucinated_url() -> None:
    """apply_git_repo_override overwrites a wrong PM-emitted git_repo with the canonical URL."""
    tasks = [_minimal_task(slug="feat", git_repo="git@github.com:wrong/repo.git")]
    tasks[0]["task_id"] = "CODER-20260101-001-feat"
    canonical = "git@github.com:real/repo.git"
    count = pm.apply_git_repo_override(tasks, canonical)
    assert count == 1
    assert tasks[0]["git_repo"] == canonical


def test_apply_git_repo_override_replaces_blank_git_repo() -> None:
    """apply_git_repo_override replaces an empty git_repo with the canonical URL."""
    tasks = [_minimal_task(slug="feat", git_repo="")]
    tasks[0]["task_id"] = "CODER-20260101-001-feat"
    canonical = "git@github.com:real/repo.git"
    pm.apply_git_repo_override(tasks, canonical)
    assert tasks[0]["git_repo"] == canonical


def test_apply_git_repo_override_replaces_none_literal_git_repo() -> None:
    """apply_git_repo_override replaces a 'none' git_repo with the canonical URL."""
    tasks = [_minimal_task(slug="feat", git_repo="none")]
    tasks[0]["task_id"] = "CODER-20260101-001-feat"
    canonical = "git@github.com:real/repo.git"
    pm.apply_git_repo_override(tasks, canonical)
    assert tasks[0]["git_repo"] == canonical


def test_apply_git_repo_override_no_op_when_already_canonical() -> None:
    """apply_git_repo_override skips tasks whose git_repo already matches canonical."""
    canonical = "git@github.com:real/repo.git"
    tasks = [_minimal_task(slug="feat", git_repo=canonical)]
    tasks[0]["task_id"] = "CODER-20260101-001-feat"
    count = pm.apply_git_repo_override(tasks, canonical)
    assert count == 0
    assert tasks[0]["git_repo"] == canonical


def test_apply_git_repo_override_no_op_when_canonical_is_empty() -> None:
    """apply_git_repo_override is a no-op when canonical_url is empty (no project.cfg value)."""
    tasks = [_minimal_task(slug="feat", git_repo="git@github.com:some/repo.git")]
    tasks[0]["task_id"] = "CODER-20260101-001-feat"
    original = tasks[0]["git_repo"]
    count = pm.apply_git_repo_override(tasks, "")
    assert count == 0
    assert tasks[0]["git_repo"] == original


def test_apply_git_repo_override_skips_synthetic_tasks() -> None:
    """apply_git_repo_override does not modify synthetic CM/TESTER/HUMAN tasks."""
    cm_open = _minimal_task(slug="open-rc", role="CM", git_repo="git@github.com:wrong/repo.git")
    cm_open["task_id"] = "CM-20260101-001-open-rc"
    cm_open["_synthetic"] = True
    canonical = "git@github.com:real/repo.git"
    count = pm.apply_git_repo_override([cm_open], canonical)
    assert count == 0
    assert cm_open["git_repo"] == "git@github.com:wrong/repo.git"


def test_apply_git_repo_override_regenerates_feature_branch_when_repo_was_none() -> None:
    """apply_git_repo_override sets feature_branch when git_repo goes from 'none' to real URL."""
    tasks = [_minimal_task(slug="feat", git_repo="none")]
    tasks[0]["task_id"] = "CODER-20260101-001-feat"
    tasks[0]["feature_branch"] = "none"
    pm.apply_git_repo_override(tasks, "git@github.com:real/repo.git")
    assert tasks[0]["feature_branch"] == "feature/CODER-20260101-001-feat"


def test_apply_git_repo_override_dry_run_does_not_modify_tasks() -> None:
    """apply_git_repo_override dry_run=True reports overrides without mutating tasks."""
    tasks = [_minimal_task(slug="feat", git_repo="git@github.com:wrong/repo.git")]
    tasks[0]["task_id"] = "CODER-20260101-001-feat"
    canonical = "git@github.com:real/repo.git"
    count = pm.apply_git_repo_override(tasks, canonical, dry_run=True)
    assert count == 1
    assert tasks[0]["git_repo"] == "git@github.com:wrong/repo.git"


# ---------------------------------------------------------------------------
# load_project_git_repo_url — read canonical URL from project.cfg
# ---------------------------------------------------------------------------


def test_load_project_git_repo_url_reads_ini_value(
    tmp_path: pathlib.Path,
) -> None:
    """load_project_git_repo_url reads git_repo_url from project.cfg [project] section."""
    (tmp_path / "project.cfg").write_text(
        "[project]\ngit_repo_url = git@github.com:myorg/myrepo.git\n",
        encoding="utf-8",
    )
    result = pm.load_project_git_repo_url(str(tmp_path))
    assert result == "git@github.com:myorg/myrepo.git"


def test_load_project_git_repo_url_returns_empty_when_missing(
    tmp_path: pathlib.Path,
) -> None:
    """load_project_git_repo_url returns '' when project.cfg does not exist."""
    result = pm.load_project_git_repo_url(str(tmp_path))
    assert result == ""


def test_load_project_git_repo_url_returns_empty_when_key_absent(
    tmp_path: pathlib.Path,
) -> None:
    """load_project_git_repo_url returns '' when project.cfg has no git_repo_url key."""
    (tmp_path / "project.cfg").write_text("[project]\nother_key = value\n", encoding="utf-8")
    result = pm.load_project_git_repo_url(str(tmp_path))
    assert result == ""


# ---------------------------------------------------------------------------
# queue_marker_for_task — WAITING vs BACKLOG marker selection
# ---------------------------------------------------------------------------


def test_queue_marker_returns_space_when_no_prerequisites() -> None:
    """queue_marker_for_task returns ' ' (BACKLOG) for a task with no prerequisites."""
    task = {"task_id": "CODER-20260101-001-feat", "prerequisite_ids": []}
    assert pm.queue_marker_for_task(task) == " "


def test_queue_marker_returns_w_when_prerequisites_present() -> None:
    """queue_marker_for_task returns 'W' (WAITING) when task has at least one prerequisite."""
    task = {
        "task_id": "CODER-20260101-002-next",
        "prerequisite_ids": ["CODER-20260101-001-feat"],
    }
    assert pm.queue_marker_for_task(task) == "W"


def test_queue_marker_never_returns_b() -> None:
    """queue_marker_for_task never returns 'B' (BLOCKED is a runtime state, not a queue marker)."""
    task = {"task_id": "CODER-20260101-001-feat", "prerequisite_ids": []}
    result = pm.queue_marker_for_task(task)
    assert result != "B"


# ---------------------------------------------------------------------------
# create_task_folder — initial state WAITING vs BACKLOG
# ---------------------------------------------------------------------------


def test_create_task_folder_initial_state_backlog_when_no_prereqs(
    tmp_path: pathlib.Path,
) -> None:
    """create_task_folder writes status.md with BACKLOG when task has no prerequisites."""
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    task = _minimal_task(slug="solo", role="CODER")
    task["task_id"] = "CODER-20260101-001-solo"
    task["owner"] = "CLAUDE"
    task["feature_branch"] = "feature/CODER-20260101-001-solo"
    task["prerequisite_ids"] = []
    pm.create_task_folder(task, str(tmp_path), tasks_root=tasks_root, release_version="v0.5.0")
    status = (tasks_root / "CODER-20260101-001-solo" / "status.md").read_text()
    assert "BACKLOG" in status


def test_create_task_folder_initial_state_waiting_when_prereqs_present(
    tmp_path: pathlib.Path,
) -> None:
    """create_task_folder writes status.md with WAITING when task has prerequisites."""
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    task = _minimal_task(slug="second", role="CODER")
    task["task_id"] = "CODER-20260101-002-second"
    task["owner"] = "CLAUDE"
    task["feature_branch"] = "feature/CODER-20260101-002-second"
    task["prerequisite_ids"] = ["CODER-20260101-001-first"]
    pm.create_task_folder(task, str(tmp_path), tasks_root=tasks_root, release_version="v0.5.0")
    status = (tasks_root / "CODER-20260101-002-second" / "status.md").read_text()
    assert "WAITING" in status


def test_create_task_folder_readme_contains_task_id(
    tmp_path: pathlib.Path,
) -> None:
    """create_task_folder writes README.md containing the task ID."""
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    task = _minimal_task(slug="feat", role="CODER")
    task["task_id"] = "CODER-20260101-001-feat"
    task["owner"] = "CLAUDE"
    task["feature_branch"] = "feature/CODER-20260101-001-feat"
    task["prerequisite_ids"] = []
    pm.create_task_folder(task, str(tmp_path), tasks_root=tasks_root, release_version="v0.5.0")
    readme = (tasks_root / "CODER-20260101-001-feat" / "README.md").read_text()
    assert "CODER-20260101-001-feat" in readme


def test_create_task_folder_readme_contains_rc_source_branch(
    tmp_path: pathlib.Path,
) -> None:
    """create_task_folder writes rc/<version> as Source Branch for release-workflow tasks."""
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    task = _minimal_task(slug="feat", role="CODER")
    task["task_id"] = "CODER-20260101-001-feat"
    task["owner"] = "CLAUDE"
    task["feature_branch"] = "feature/CODER-20260101-001-feat"
    task["prerequisite_ids"] = []
    pm.create_task_folder(task, str(tmp_path), tasks_root=tasks_root, workflow_type="release", release_version="v0.5.0")
    readme = (tasks_root / "CODER-20260101-001-feat" / "README.md").read_text()
    assert "rc/v0.5.0" in readme


def test_create_task_folder_skips_existing_folder(
    tmp_path: pathlib.Path,
) -> None:
    """create_task_folder skips (idempotent) when the task folder already exists."""
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    task_id = "CODER-20260101-001-dup"
    (tasks_root / task_id).mkdir()
    task = _minimal_task(slug="dup", role="CODER")
    task["task_id"] = task_id
    task["owner"] = "CLAUDE"
    task["feature_branch"] = f"feature/{task_id}"
    task["prerequisite_ids"] = []
    result = pm.create_task_folder(task, str(tmp_path), tasks_root=tasks_root, release_version="v0.5.0")
    assert result == task_id


def test_create_task_folder_raises_when_tasks_root_is_none(
    tmp_path: pathlib.Path,
) -> None:
    """create_task_folder raises ValueError when tasks_root is not provided."""
    task = _minimal_task(slug="feat", role="CODER")
    task["task_id"] = "CODER-20260101-001-feat"
    task["owner"] = "CLAUDE"
    task["feature_branch"] = "feature/CODER-20260101-001-feat"
    task["prerequisite_ids"] = []
    with pytest.raises(ValueError, match="tasks_root must be explicitly provided"):
        pm.create_task_folder(task, str(tmp_path), tasks_root=None, release_version="v0.5.0")


# ---------------------------------------------------------------------------
# inject_feature_workflow_tasks — feature workflow (no CM bookends)
# ---------------------------------------------------------------------------


def test_inject_feature_workflow_prepends_create_shared_branch_task() -> None:
    """inject_feature_workflow_tasks adds a CODER create-shared-branch task at position 0."""
    tasks = _stamp_ids(_make_tasks(2))
    pm.inject_feature_workflow_tasks(
        tasks,
        source_branch="feature/shared",
        test_required=True,
        parent_branch="main",
        date_str="20260101",
        base_seq=100,
        owner="CLAUDE",
    )
    assert tasks[0].get("_create_shared_branch") is True
    assert tasks[0]["role"] == "CODER"


def test_inject_feature_workflow_no_cm_tasks_present() -> None:
    """inject_feature_workflow_tasks does NOT inject any CM tasks."""
    tasks = _stamp_ids(_make_tasks(2))
    pm.inject_feature_workflow_tasks(
        tasks,
        source_branch="feature/shared",
        test_required=False,
        parent_branch="main",
        date_str="20260101",
        base_seq=100,
        owner="CLAUDE",
    )
    cm_tasks = [t for t in tasks if t.get("role") == "CM"]
    assert len(cm_tasks) == 0


def test_inject_feature_workflow_appends_tester_when_test_required() -> None:
    """inject_feature_workflow_tasks appends a TESTER task when test_required=True."""
    tasks = _stamp_ids(_make_tasks(2))
    pm.inject_feature_workflow_tasks(
        tasks,
        source_branch="feature/shared",
        test_required=True,
        parent_branch="main",
        date_str="20260101",
        base_seq=100,
        owner="CLAUDE",
    )
    tester_tasks = [t for t in tasks if t.get("role") == "TESTER"]
    assert len(tester_tasks) == 1


def test_inject_feature_workflow_no_tester_when_test_not_required() -> None:
    """inject_feature_workflow_tasks omits TESTER when test_required=False."""
    tasks = _stamp_ids(_make_tasks(2))
    pm.inject_feature_workflow_tasks(
        tasks,
        source_branch="feature/shared",
        test_required=False,
        parent_branch="main",
        date_str="20260101",
        base_seq=100,
        owner="CLAUDE",
    )
    tester_tasks = [t for t in tasks if t.get("role") == "TESTER"]
    assert len(tester_tasks) == 0


def test_inject_feature_workflow_feature_tasks_depend_on_create_branch() -> None:
    """inject_feature_workflow_tasks adds the create-branch task ID to feature task prereqs."""
    tasks = _stamp_ids(_make_tasks(2))
    feature_ids = [t["task_id"] for t in tasks]
    pm.inject_feature_workflow_tasks(
        tasks,
        source_branch="feature/shared",
        test_required=False,
        parent_branch="main",
        date_str="20260101",
        base_seq=100,
        owner="CLAUDE",
    )
    create_branch_id = tasks[0]["task_id"]
    # Feature tasks are at positions 1 and 2
    feature_task = next(t for t in tasks if t.get("task_id") in feature_ids)
    assert create_branch_id in feature_task.get("prerequisite_ids", [])


# ---------------------------------------------------------------------------
# assign_task_ids — ID generation
# ---------------------------------------------------------------------------


def test_assign_task_ids_generates_role_date_seq_slug_format(
    tmp_path: pathlib.Path,
) -> None:
    """assign_task_ids generates task IDs in ROLE-YYYYMMDD-NNN-slug format."""
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    tasks = [_minimal_task(seq=1, slug="my-feature", role="CODER")]
    pm.assign_task_ids(tasks, "CLAUDE", tasks_root)
    tid = tasks[0]["task_id"]
    parts = tid.split("-")
    assert parts[0] == "CODER"
    assert len(parts[1]) == 8 and parts[1].isdigit()  # YYYYMMDD
    assert parts[2].isdigit()  # NNN
    assert "my-feature" in tid


def test_assign_task_ids_skips_tasks_with_existing_task_id(
    tmp_path: pathlib.Path,
) -> None:
    """assign_task_ids leaves pre-assigned task_ids (synthetic tasks) untouched."""
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    pre_assigned_id = "CM-20260101-001-open-rc-0-5-0"
    tasks = [
        {"sequence": 0, "slug": "open-rc", "role": "CM", "task_id": pre_assigned_id},
        _minimal_task(seq=1, slug="feat", role="CODER"),
    ]
    pm.assign_task_ids(tasks, "CLAUDE", tasks_root)
    # The CM task must keep its original ID
    assert tasks[0]["task_id"] == pre_assigned_id
    # The CODER task gets a new ID
    assert "CM-" not in tasks[1]["task_id"]


def test_assign_task_ids_sets_feature_branch_when_git_repo_present(
    tmp_path: pathlib.Path,
) -> None:
    """assign_task_ids sets feature_branch = feature/<task_id> when git_repo is non-none."""
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    tasks = [_minimal_task(seq=1, slug="feat", role="CODER", git_repo="git@github.com:org/repo.git")]
    pm.assign_task_ids(tasks, "CLAUDE", tasks_root)
    assert tasks[0]["feature_branch"].startswith("feature/CODER-")


def test_assign_task_ids_sets_feature_branch_none_when_no_git_repo(
    tmp_path: pathlib.Path,
) -> None:
    """assign_task_ids sets feature_branch='none' when git_repo is 'none'."""
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    tasks = [_minimal_task(seq=1, slug="feat", role="CODER", git_repo="none")]
    pm.assign_task_ids(tasks, "CLAUDE", tasks_root)
    assert tasks[0]["feature_branch"] == "none"


# ---------------------------------------------------------------------------
# normalize_workspace_value — None / empty / "none" normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, "none"),  # Python None -> 'none'
        ("", "none"),  # empty string -> 'none'
        ("/opt/project", "/opt/project"),  # real path passes through as-is
        ("  /opt/project  ", "/opt/project"),  # surrounding whitespace stripped
    ],
)
def test_normalize_workspace_value(raw, expected: str) -> None:
    """normalize_workspace_value passes through real paths, normalizing empty/None to 'none'."""
    assert pm.normalize_workspace_value(raw) == expected


# ---------------------------------------------------------------------------
# validate_model_override — model_override field normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("opus", "opus"),
        ("sonnet", "sonnet"),
        ("haiku", "haiku"),
        ("none", "none"),  # literal string 'none'
    ],
)
def test_validate_model_override_accepts_alias(raw: str, expected: str) -> None:
    """validate_model_override accepts the known model-alias shortnames and the literal 'none'."""
    assert pm.validate_model_override(raw) == expected


def test_validate_model_override_accepts_full_model_id() -> None:
    """validate_model_override accepts a claude-sonnet-* style model ID."""
    result = pm.validate_model_override("claude-sonnet-4-5")
    assert result == "claude-sonnet-4-5"


def test_validate_model_override_returns_none_for_empty_input() -> None:
    """validate_model_override returns 'none' when the input is empty or None."""
    assert pm.validate_model_override("") == "none"
    assert pm.validate_model_override(None) == "none"


def test_validate_model_override_passes_through_unrecognized_value() -> None:
    """validate_model_override returns unrecognized values with a warning (warn-don't-block)."""
    result = pm.validate_model_override("gpt-4")
    assert result == "gpt-4"


# ---------------------------------------------------------------------------
# format_list / format_checklist — markdown rendering helpers
# ---------------------------------------------------------------------------


def test_format_list_returns_none_string_for_empty_list() -> None:
    """format_list returns 'none' for an empty list."""
    assert pm.format_list([]) == "none"


def test_format_list_prefixes_each_item_with_dash() -> None:
    """format_list produces '- item' lines for each element."""
    result = pm.format_list(["alpha", "beta"])
    assert "- alpha" in result
    assert "- beta" in result


def test_format_checklist_returns_placeholder_for_empty_list() -> None:
    """format_checklist returns a single placeholder checkbox for an empty list."""
    result = pm.format_checklist([])
    assert "- [ ]" in result


def test_format_checklist_prefixes_each_item_with_unchecked_box() -> None:
    """format_checklist produces '- [ ] item' lines for each element."""
    result = pm.format_checklist(["passes tests", "exits cleanly"])
    assert "- [ ] passes tests" in result
    assert "- [ ] exits cleanly" in result


# ---------------------------------------------------------------------------
# compute_plan_hash — SHA-256 fingerprint of plan content
# ---------------------------------------------------------------------------


def test_compute_plan_hash_returns_64_hex_chars() -> None:
    """compute_plan_hash returns a 64-character hex string (SHA-256)."""
    result = pm.compute_plan_hash('{"tasks": []}')
    assert len(result) == 64
    assert all(c in "0123456789abcdef" for c in result)


def test_compute_plan_hash_is_stable_for_same_input() -> None:
    """compute_plan_hash produces the same digest for identical content."""
    content = '{"workflow_type": "release"}'
    assert pm.compute_plan_hash(content) == pm.compute_plan_hash(content)


def test_compute_plan_hash_differs_for_different_content() -> None:
    """compute_plan_hash produces distinct digests for different plan content."""
    assert pm.compute_plan_hash("plan A") != pm.compute_plan_hash("plan B")


# ---------------------------------------------------------------------------
# get_active_rc — read Active RC from release-state.md
# ---------------------------------------------------------------------------


def test_get_active_rc_returns_version_from_release_state(
    tmp_path: pathlib.Path,
) -> None:
    """get_active_rc returns the version string from release-state.md Active RC."""
    (tmp_path / "release-state.md").write_text("# Release State\n\n## Active RC\nv0.5.0\n", encoding="utf-8")
    result = pm.get_active_rc(str(tmp_path))
    assert result == "v0.5.0"


def test_get_active_rc_returns_none_when_active_rc_is_none(
    tmp_path: pathlib.Path,
) -> None:
    """get_active_rc returns None when Active RC field is 'none'."""
    (tmp_path / "release-state.md").write_text("# Release State\n\n## Active RC\nnone\n", encoding="utf-8")
    result = pm.get_active_rc(str(tmp_path))
    assert result is None


def test_get_active_rc_returns_none_when_file_missing(
    tmp_path: pathlib.Path,
) -> None:
    """get_active_rc returns None when release-state.md does not exist."""
    result = pm.get_active_rc(str(tmp_path))
    assert result is None


# ---------------------------------------------------------------------------
# is_human_task / get_queue_path — HUMAN task queue routing
# ---------------------------------------------------------------------------


def test_is_human_task_returns_true_for_human_role() -> None:
    """is_human_task returns True when role is 'HUMAN'."""
    task = {"role": "HUMAN", "assigned_agent": "HUMAN"}
    assert pm.is_human_task(task) is True


def test_is_human_task_returns_false_for_coder_role() -> None:
    """is_human_task returns False for a CODER-role task."""
    task = {"role": "CODER", "assigned_agent": "CODER"}
    assert pm.is_human_task(task) is False


@pytest.mark.parametrize(
    "role, assigned_agent, expected_backlog_filename",
    [
        ("HUMAN", "HUMAN", "human_backlog.md"),  # HUMAN-role gate tasks
        ("CODER", "CODER", "coder_backlog.md"),  # role and assigned_agent agree
        ("CODER", "writer", "writer_backlog.md"),  # assigned_agent overrides role
    ],
)
def test_get_queue_path(tmp_path: pathlib.Path, role: str, assigned_agent: str, expected_backlog_filename: str) -> None:
    """get_queue_path routes tasks to the backlog matching assigned_agent (falling back to role)."""
    task = {"role": role, "assigned_agent": assigned_agent}
    result = pm.get_queue_path(task, str(tmp_path))
    assert result is not None
    assert expected_backlog_filename in str(result)


def test_update_backlog_writes_human_task_to_human_backlog(
    tmp_path: pathlib.Path,
) -> None:
    """update_backlog writes a HUMAN-APPROVE gate task to human_backlog.md."""
    queues_dir = tmp_path / "tasks" / "queues"
    queues_dir.mkdir(parents=True)
    human_task = {
        "task_id": "HUMAN-APPROVE-v1.8.1-065",
        "role": "HUMAN",
        "assigned_agent": "HUMAN",
        "prerequisite_ids": ["TESTER-20260708-005-verify-1-8-1"],
    }
    pm.update_backlog([human_task], str(tmp_path))
    human_backlog = queues_dir / "human_backlog.md"
    assert human_backlog.is_file(), "human_backlog.md was not created"
    content = human_backlog.read_text()
    assert "HUMAN-APPROVE-v1.8.1-065" in content
    # WAITING marker because prerequisites are set
    assert "- [W] HUMAN-APPROVE-v1.8.1-065" in content


def test_update_backlog_coder_entry_byte_identical_with_human_also_present(
    tmp_path: pathlib.Path,
) -> None:
    """coder_backlog.md content is byte-identical whether or not a HUMAN task is also processed.

    Proves that routing HUMAN tasks to human_backlog.md does not perturb
    the coder queue entry.
    """
    queues_dir = tmp_path / "tasks" / "queues"
    queues_dir.mkdir(parents=True)
    coder_task = {
        "task_id": "CODER-20260708-001-implement-feature",
        "role": "CODER",
        "assigned_agent": "CODER",
        "prerequisite_ids": [],
    }
    # Baseline: write coder task alone
    baseline_dir = tmp_path / "baseline"
    baseline_queues = baseline_dir / "tasks" / "queues"
    baseline_queues.mkdir(parents=True)
    pm.update_backlog([coder_task], str(baseline_dir))
    baseline_content = (baseline_queues / "coder_backlog.md").read_text()

    # With HUMAN task also present
    human_task = {
        "task_id": "HUMAN-APPROVE-v1.8.1-065",
        "role": "HUMAN",
        "assigned_agent": "HUMAN",
        "prerequisite_ids": ["TESTER-20260708-005-verify"],
    }
    pm.update_backlog([coder_task, human_task], str(tmp_path))
    combined_content = (queues_dir / "coder_backlog.md").read_text()

    assert combined_content == baseline_content, "coder_backlog.md content changed when HUMAN task was also routed"


def test_validate_and_normalize_queue_catches_malformed_human_queue_marker(
    tmp_path: pathlib.Path,
) -> None:
    """validate_and_normalize_queue rewrites a non-canonical human queue entry.

    Proves the correctness check guards the human queue: a deliberately
    malformed line (missing leading dash) is detected and rewritten.
    """
    human_backlog = tmp_path / "human_backlog.md"
    # Deliberately malformed: missing leading '- '
    human_backlog.write_text("[W] HUMAN-APPROVE-v1.8.1-065\n", encoding="utf-8")
    rewritten = pm.validate_and_normalize_queue(human_backlog)
    assert rewritten == 1, "malformed human queue marker was not detected by correctness check"
    canonical = human_backlog.read_text()
    assert "- [W] HUMAN-APPROVE-v1.8.1-065" in canonical


# ---------------------------------------------------------------------------
# End-to-end assembly: release plan produces correct structure
# ---------------------------------------------------------------------------


def test_release_plan_assembly_produces_cm_open_tester_cm_release(
    tmp_path: pathlib.Path,
) -> None:
    """A two-task release plan results in CM-open, 2 feature tasks, TESTER, CM-release."""
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    tasks = _make_tasks(2)
    # 1. Assign IDs to feature tasks
    pm.assign_task_ids(tasks, "CLAUDE", tasks_root)
    # 2. Inject bookends
    pm.inject_cm_bookends(tasks, "v0.5.0", "20260101", 100, "CLAUDE")
    # 3. Resolve prerequisites
    pm.resolve_prerequisites(tasks)

    roles = [t.get("role") for t in tasks]
    assert roles[0] == "CM"  # CM-open
    assert roles[-1] == "CM"  # CM-release
    assert "TESTER" in roles
    # Feature tasks are in between
    feature_roles = [r for r in roles if r == "CODER"]
    assert len(feature_roles) == 2


def test_release_plan_assembly_source_branch_overridden_to_rc(
    tmp_path: pathlib.Path,
) -> None:
    """A materialized release task has source_branch overridden to rc/<version>."""
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    tasks = _make_tasks(1)
    pm.assign_task_ids(tasks, "CLAUDE", tasks_root)
    pm.inject_cm_bookends(tasks, "v0.5.0", "20260101", 100, "CLAUDE")

    # feature task is at index 1 (after CM-open at 0)
    feature_task = tasks[1]
    pm.create_task_folder(
        feature_task, str(tmp_path), tasks_root=tasks_root, workflow_type="release", release_version="v0.5.0"
    )
    readme = (tasks_root / feature_task["task_id"] / "README.md").read_text()
    assert "rc/v0.5.0" in readme


def test_release_plan_assembly_git_repo_override_applied_before_bookends(
    tmp_path: pathlib.Path,
) -> None:
    """git_repo override from project.cfg is applied to feature tasks, not synthetic CM tasks."""
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    (tmp_path / "project.cfg").write_text(
        "[project]\ngit_repo_url = git@github.com:canonical/repo.git\n",
        encoding="utf-8",
    )
    tasks = _make_tasks(1)
    tasks[0]["git_repo"] = "none"  # simulate PM hallucinating no repo
    pm.assign_task_ids(tasks, "CLAUDE", tasks_root)

    # Apply git_repo override (runs BEFORE bookend injection, like main() does)
    canonical = pm.load_project_git_repo_url(str(tmp_path))
    pm.apply_git_repo_override(tasks, canonical)

    # Feature task should now have canonical URL
    assert tasks[0]["git_repo"] == "git@github.com:canonical/repo.git"

    # Now inject bookends — CM tasks get git_repo from their own defaults
    pm.inject_cm_bookends(tasks, "v0.5.0", "20260101", 100, "CLAUDE")
    # The synthetic CM-open task (which we injected as "_synthetic") should
    # not have been affected by the earlier override
    cm_open = tasks[0]
    assert cm_open.get("cm_operation") == "open-rc"


# ---------------------------------------------------------------------------
# Bundled-item coverage: every ## Bundled Items entry is ticketed or asked about
# ---------------------------------------------------------------------------

_MATERIALIZE_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "pm-agent" / "pm_materialize.py"
_COVERAGE_PROJECT = "bundle-coverage"
_COVERAGE_QUESTION = "Which retry limit should the uploader use?"


def _coverage_sandbox(tmp_path: pathlib.Path) -> tuple:
    """``(kanban_root, project_root)``: a throwaway kanban root with one registered project."""
    root = build_two_project_root(tmp_path)
    project_root = add_registered_project(root, _COVERAGE_PROJECT)
    for sub in ("bugs", "requirements", "priority"):
        (project_root / sub).mkdir(exist_ok=True)
    (root / "tmp").mkdir(exist_ok=True)
    return root, project_root


def _coverage_bug(project_root: pathlib.Path, stem: str, status: str = "open") -> pathlib.Path:
    path = project_root / "bugs" / f"{stem}.md"
    path.write_text(
        f"# {stem}\n\n**Severity:** low\n\n## Status\n{status}\n\n## Symptom\nsynthetic\n", encoding="utf-8"
    )
    return path


def _coverage_requirement(project_root: pathlib.Path, items: list) -> pathlib.Path:
    """A requirement for v9.9.6; with *items* it is a bundle listing them under ## Bundled Items."""
    path = project_root / "requirements" / "v9.9.6-bugfix-bundle-20990101.md"
    bundled = "## Bundled Items\n\n" + "".join(f"- {b.name} (`{b}`)\n" for b in items) if items else ""
    path.write_text(
        "# v9.9.6-bugfix-bundle-20990101\n\n## Status\nrunning\n\n## Target Version\nv9.9.6\n\n"
        f"## Workflow Type\nrelease\n\n{bundled}\n## Acceptance Criteria\n\n- It works.\n",
        encoding="utf-8",
    )
    return path


def _coverage_plan(requirement: pathlib.Path, goal: str, **fields) -> dict:
    task = {
        "sequence": 1,
        "slug": "uploader-fixes",
        "title": "Fix the uploader",
        "role": "CODER",
        "working_directory": "/dev/null",
        "git_repo": "none",
        "source_branch": "rc/v9.9.6",
        "goal": goal,
        "inputs": [],
        "context_paths": [],
        "required_output": "The fix.",
        "constraints": [],
        "acceptance_criteria": ["It works."],
        "depends_on": [],
        "notes": "",
    }
    plan = {
        "project_name": _COVERAGE_PROJECT,
        "workflow_type": "release",
        "target_version": "v9.9.6",
        "test_required": "false",
        "human_approval_required": "auto",
        "requirements_path": str(requirement),
        "tasks": [task],
    }
    plan.update(fields)
    return plan


def _coverage_materialize(root: pathlib.Path, project_root: pathlib.Path, tmp_path: pathlib.Path, plan: dict):
    """Run the real materializer CLI on *plan* with all five kanban-root variables set or cleared."""
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    plan_file = plan_dir / "plan.json"
    plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={
            "KANBAN_ROOT": str(root),
            "TEAM_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
            "PGAI_DEFERRED_BUGS": "false",
        },
    )
    return subprocess.run(
        [
            sys.executable,
            str(_MATERIALIZE_SCRIPT),
            str(plan_file),
            "--team-root",
            str(project_root),
            "--requirements-path",
            plan["requirements_path"],
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(root),
        timeout=120,
    )


def _coverage_snapshot(project_root: pathlib.Path) -> dict:
    """Every byte the materializer could write: tasks, queues, intake, requirements, inbox."""
    paths = [project_root / sub for sub in ("tasks", "bugs", "requirements", "priority")]
    paths.append(project_root / "operator-inbox.md")
    files = [f for p in paths for f in ([p] if p.is_file() else sorted(p.rglob("*")) if p.is_dir() else [])]
    return {str(f): f.read_bytes() for f in files if f.is_file()}


def _coverage_task_dirs(project_root: pathlib.Path) -> list:
    tasks = project_root / "tasks"
    return sorted(d.name for d in tasks.iterdir() if d.is_dir() and d.name != "queues") if tasks.is_dir() else []


def _coverage_status(path: pathlib.Path) -> str:
    return (read_section(path.read_text(encoding="utf-8"), "Status") or "").strip()


def test_a_bundled_item_no_ticket_names_is_refused_and_nothing_is_written(tmp_path) -> None:
    root, project_root = _coverage_sandbox(tmp_path)
    named = _coverage_bug(project_root, "BUG-9961-uploader-timeout")
    omitted = _coverage_bug(project_root, "BUG-9962-uploader-retry")
    requirement = _coverage_requirement(project_root, [named, omitted])
    before = _coverage_snapshot(project_root)

    result = _coverage_materialize(
        root, project_root, tmp_path, _coverage_plan(requirement, "Fix BUG-9961: the uploader times out.")
    )

    assert result.returncode != 0, result.stdout + result.stderr
    refusal = parse_error_contract(result.stderr[result.stderr.find("error:") :])
    assert refusal is not None, result.stderr
    assert refusal["error"] == "materialize.bundled_item_unassigned"
    assert omitted.name in refusal["message"]
    assert named.name not in refusal["message"]
    assert "BUG-9962" in refusal["remedy"] and "<" not in refusal["remedy"]
    assert _coverage_snapshot(project_root) == before
    assert not list((tmp_path / "plan").glob(".materialized.*"))


def test_the_omitted_item_listed_under_needs_operator_materializes_and_asks(tmp_path) -> None:
    root, project_root = _coverage_sandbox(tmp_path)
    named = _coverage_bug(project_root, "BUG-9961-uploader-timeout")
    omitted = _coverage_bug(project_root, "BUG-9962-uploader-retry")
    requirement = _coverage_requirement(project_root, [named, omitted])
    plan = _coverage_plan(
        requirement,
        "Fix BUG-9961: the uploader times out.",
        needs_operator=[{"item": "BUG-9962", "question": _COVERAGE_QUESTION}],
    )

    result = _coverage_materialize(root, project_root, tmp_path, plan)

    assert result.returncode == 0, result.stderr
    assert "bundled_item_unassigned" not in result.stderr
    assert any("uploader-fixes" in name for name in _coverage_task_dirs(project_root))
    assert _coverage_status(omitted) == "needs-operator"
    [entry] = inbox.list_open(OpsContext(kanban_root=root), _COVERAGE_PROJECT)
    assert entry.source == omitted.stem
    assert entry.question == _COVERAGE_QUESTION


@pytest.mark.parametrize(
    ("bundled", "goal"),
    [
        (False, "Fix the uploader."),
        (True, "Fix BUG-9961 and bug_9962 in the uploader."),
    ],
    ids=["no-bundled-items-section", "every-item-named"],
)
def test_a_plan_with_nothing_unassigned_materializes_as_before(tmp_path, bundled, goal) -> None:
    root, project_root = _coverage_sandbox(tmp_path)
    bugs = [
        _coverage_bug(project_root, "BUG-9961-uploader-timeout"),
        _coverage_bug(project_root, "BUG-9962-uploader-retry"),
    ]
    requirement = _coverage_requirement(project_root, bugs if bundled else [])
    intake_before = {k: v for k, v in _coverage_snapshot(project_root).items() if "/tasks/" not in k}

    result = _coverage_materialize(root, project_root, tmp_path, _coverage_plan(requirement, goal))

    assert result.returncode == 0, result.stderr
    assert "bundled_item_unassigned" not in result.stderr
    assert any("uploader-fixes" in name for name in _coverage_task_dirs(project_root))
    assert {k: v for k, v in _coverage_snapshot(project_root).items() if "/tasks/" not in k} == intake_before


def test_an_empty_plan_against_a_bundle_is_still_a_no_op(tmp_path) -> None:
    root, project_root = _coverage_sandbox(tmp_path)
    bug = _coverage_bug(project_root, "BUG-9961-uploader-timeout")
    requirement = _coverage_requirement(project_root, [bug])
    plan = _coverage_plan(requirement, "unused")
    plan["tasks"] = []
    before = _coverage_snapshot(project_root)

    result = _coverage_materialize(root, project_root, tmp_path, plan)

    assert result.returncode == 0, result.stderr
    assert "Nothing to do" in result.stderr
    assert _coverage_snapshot(project_root) == before


def _plan_naming(*texts: str) -> dict:
    return {"tasks": [{"sequence": 1, "goal": texts[0], "inputs": list(texts[1:]), "depends_on": [2]}]}


def _bundle_file(tmp_path: pathlib.Path, *names: str) -> pathlib.Path:
    path = tmp_path / "bundle.md"
    path.write_text(
        "# bundle\n\n## Bundled Items\n\n"
        + "".join(f"- {n} (`/elsewhere/bugs/{n}`)\n" for n in names)
        + "\n## Notes\n\n- BUG-9969-not-an-item.md (`/elsewhere/bugs/BUG-9969-not-an-item.md`)\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    "spelling",
    ["BUG-9961", "bug-9961", "BUG_9961", "BUG 9961", "BUG-09961", "see /x/bugs/BUG-9961-uploader-timeout.md"],
)
def test_a_ticket_names_an_item_by_its_id_in_any_spelling(tmp_path, spelling) -> None:
    bundle = _bundle_file(tmp_path, "BUG-9961-uploader-timeout.md")
    assert pm.uncovered_bundled_items(_plan_naming("Fix it.", spelling), str(bundle)) == []


@pytest.mark.parametrize("text", ["BUG-99610", "BUG-996", "XBUG-9961", "PRIORITY-9961"])
def test_a_longer_shorter_or_other_kind_of_id_does_not_name_the_item(tmp_path, text) -> None:
    bundle = _bundle_file(tmp_path, "BUG-9961-uploader-timeout.md")
    assert pm.uncovered_bundled_items(_plan_naming(text), str(bundle)) == ["BUG-9961-uploader-timeout.md"]


def test_only_the_bundled_items_section_is_read_and_repeats_are_dropped(tmp_path) -> None:
    bundle = _bundle_file(tmp_path, "BUG-9961-a.md", "PRIORITY-9962-b.md", "BUG-9961-a.md", "NOTE-c.md")
    assert pm.uncovered_bundled_items(_plan_naming("nothing"), str(bundle)) == [
        "BUG-9961-a.md",
        "PRIORITY-9962-b.md",
        "NOTE-c.md",
    ]
    assert pm.uncovered_bundled_items(_plan_naming("PRIORITY-9962 and NOTE-c"), str(bundle)) == ["BUG-9961-a.md"]


def test_parked_and_ride_along_items_are_covered_by_file_name(tmp_path) -> None:
    from types import SimpleNamespace

    bundle = _bundle_file(tmp_path, "BUG-9961-a.md", "BUG-9962-b.md", "BUG-9963-c.md")
    intake = SimpleNamespace(
        parked=(tmp_path / "bugs" / "BUG-9961-a.md",),
        ride_along=(SimpleNamespace(path=tmp_path / "bugs" / "BUG-9962-b.md"),),
    )
    assert pm.uncovered_bundled_items(_plan_naming("nothing"), str(bundle), intake) == ["BUG-9963-c.md"]


@pytest.mark.parametrize("requirement", ["none", "", "MISSING"])
def test_no_requirement_or_an_unreadable_one_has_nothing_to_cover(tmp_path, requirement) -> None:
    path = str(tmp_path / "missing.md") if requirement == "MISSING" else requirement
    assert pm.uncovered_bundled_items(_plan_naming("nothing"), path) == []
