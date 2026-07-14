"""
test_pm_materialize_regression.py
==================================
Byte-behavior regression tests for pm_materialize.py task-set composition.

These tests pin the materialized task-set output for a representative release
requirement and a representative document requirement.  They compare current
output to golden fixtures byte-for-byte (at the task-list level), enforcing
the strangler guarantee: the move of pipeline.yaml into plugin directories
must be invisible at the task-set level — output before and after the move
must be identical.

Golden fixtures live under:
  tests/fixtures/golden/release_decomposition.json
  tests/fixtures/golden/document_decomposition.json

Each fixture records the sequence of tasks (role, task_id, slug) that
pm_materialize's injection functions produce for a deterministic, fixed-seed
invocation.  When the golden files and the live output diverge, the move was
NOT invisible — investigate before regenerating.

Design notes:
  - All inputs (date_str, base_seq, version, owner) are fixed so the output
    is deterministic regardless of when the test runs.
  - The document test supplies project_cfg_path=None (no review_agent
    configured) so the optional review step is skipped; this is the
    simple short-form path and the most stable golden target.
  - The release test uses a single feature task with a pre-assigned task_id
    so inject_cm_bookends produces a fully deterministic task list.
  - Golden fields captured: role, task_id, slug.  These three fields uniquely
    identify the task sequence and its composition.  Implementation-internal
    fields (_synthetic, cm_operation, etc.) are excluded so the golden is not
    brittle against internal refactors that do not change observable behavior.
  - File paths in test names describe behavior, not bug IDs or task IDs (SOP
    anti-pattern 6).
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

# ---------------------------------------------------------------------------
# Import with installed-package / direct-checkout fallback
# ---------------------------------------------------------------------------

try:
    import pm_agent.pm_materialize as pm  # installed via pm_agent package
except ImportError:
    sys.path.insert(
        0, str(pathlib.Path(__file__).parent.parent.parent / "pm-agent")
    )
    import pm_materialize as pm  # type: ignore[no-redef]

try:
    from pm_agent.lib import workflow_loader
    from pm_agent.lib.workflow_loader import load_workflow
except ImportError:
    sys.path.insert(
        0, str(pathlib.Path(__file__).parent.parent.parent / "pm-agent")
    )
    from lib import workflow_loader  # type: ignore[no-redef]
    from lib.workflow_loader import load_workflow  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Paths to golden fixture files and the dev tree (for load_workflow)
# ---------------------------------------------------------------------------

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_GOLDEN_DIR = _TEAM_DIR / "tests" / "fixtures" / "golden"
_RELEASE_GOLDEN = _GOLDEN_DIR / "release_decomposition.json"
_DOCUMENT_GOLDEN = _GOLDEN_DIR / "document_decomposition.json"

# load_workflow searches $KANBAN_ROOT/team/workflows/<type>/pipeline.yaml.
# The dev tree root (parent of team/) is used as the kanban root so the
# real pipeline.yaml files under team/workflows/ are found.
_DEV_TREE_ROOT = _TEAM_DIR.parent


# ---------------------------------------------------------------------------
# Helper: extract stable fields from a task list
# ---------------------------------------------------------------------------


def _task_signature(tasks: list) -> list:
    """Return a list of dicts with the stable, comparable fields of each task.

    Extracts only the fields that define task-set composition: role, task_id,
    and slug.  Internal implementation fields are excluded so the golden is
    not brittle against refactors that do not affect observable behavior.

    Args:
        tasks:  The assembled task list produced by an injection function.

    Returns:
        list[dict] — one entry per task, with keys 'role', 'task_id', 'slug'.
    """
    return [
        {
            "role": t["role"],
            "task_id": t.get("task_id", ""),
            "slug": t.get("slug", ""),
        }
        for t in tasks
    ]


# ---------------------------------------------------------------------------
# Release decomposition byte-behavior regression
# ---------------------------------------------------------------------------


def test_release_decomposition_matches_golden() -> None:
    """Release task-set composition is byte-identical to the pre-move golden fixture.

    Constructs a one-feature-task release plan with fixed seed values and
    calls inject_cm_bookends().  The resulting task sequence (role, task_id,
    slug for each task in order) must exactly match the golden fixture
    recorded before pipeline.yaml was moved into the plugin directory.  A
    mismatch means the move was NOT invisible at this layer; investigate
    before regenerating the golden.
    """
    assert _RELEASE_GOLDEN.exists(), (
        f"Release golden fixture not found: {_RELEASE_GOLDEN}. "
        "Run the golden-generation step to create it."
    )
    expected = json.loads(_RELEASE_GOLDEN.read_text(encoding="utf-8"))

    # Fixed-seed inputs — must match the values used to generate the golden.
    tasks = [
        {
            "sequence": 1,
            "slug": "implement",
            "title": "Implement the feature",
            "role": "CODER",
            "assigned_agent": "coder",
            "working_directory": "none",
            "git_repo": "none",
            "source_branch": "main",
            "goal": "Do the work.",
            "inputs": ["requirements.md"],
            "context_paths": [],
            "required_output": "Done.",
            "constraints": [],
            "acceptance_criteria": ["It works."],
            "depends_on": [],
            "prerequisite_ids": [],
            "notes": "none",
            "task_id": "CODER-20260101-001-implement",
        },
    ]
    pm.inject_cm_bookends(
        tasks,
        target_version="v1.3.0",
        date_str="20260101",
        base_seq=100,
        owner="CLAUDE",
    )

    actual = _task_signature(tasks)
    assert actual == expected, (
        "Release task-set composition changed — the pipeline.yaml move is NOT invisible.\n"
        f"Expected: {json.dumps(expected, indent=2)}\n"
        f"Actual:   {json.dumps(actual, indent=2)}"
    )


def test_release_decomposition_task_count_matches_golden() -> None:
    """Release decomposition produces the same number of tasks as the golden fixture."""
    expected = json.loads(_RELEASE_GOLDEN.read_text(encoding="utf-8"))
    tasks = [
        {
            "sequence": 1, "slug": "implement", "title": "Implement",
            "role": "CODER", "assigned_agent": "coder",
            "working_directory": "none", "git_repo": "none",
            "source_branch": "main", "goal": "Do the work.",
            "inputs": ["requirements.md"], "context_paths": [],
            "required_output": "Done.", "constraints": [],
            "acceptance_criteria": ["It works."], "depends_on": [],
            "prerequisite_ids": [], "notes": "none",
            "task_id": "CODER-20260101-001-implement",
        },
    ]
    pm.inject_cm_bookends(tasks, "v1.3.0", "20260101", 100, "CLAUDE")
    assert len(tasks) == len(expected), (
        f"Task count changed: got {len(tasks)}, expected {len(expected)}"
    )


def test_release_decomposition_role_sequence_matches_golden() -> None:
    """Release decomposition role sequence is byte-identical to the golden fixture."""
    expected = json.loads(_RELEASE_GOLDEN.read_text(encoding="utf-8"))
    expected_roles = [e["role"] for e in expected]

    tasks = [
        {
            "sequence": 1, "slug": "implement", "title": "Implement",
            "role": "CODER", "assigned_agent": "coder",
            "working_directory": "none", "git_repo": "none",
            "source_branch": "main", "goal": "Do the work.",
            "inputs": ["requirements.md"], "context_paths": [],
            "required_output": "Done.", "constraints": [],
            "acceptance_criteria": ["It works."], "depends_on": [],
            "prerequisite_ids": [], "notes": "none",
            "task_id": "CODER-20260101-001-implement",
        },
    ]
    pm.inject_cm_bookends(tasks, "v1.3.0", "20260101", 100, "CLAUDE")
    actual_roles = [t["role"] for t in tasks]
    assert actual_roles == expected_roles, (
        f"Role sequence changed: got {actual_roles}, expected {expected_roles}"
    )


# ---------------------------------------------------------------------------
# Document decomposition byte-behavior regression
# ---------------------------------------------------------------------------


def test_document_decomposition_matches_golden() -> None:
    """Document task-set composition is byte-identical to the pre-move golden fixture.

    Constructs a short-form document plan (no sections, no review agent) with
    fixed seed values and calls inject_document_workflow_tasks() using the real
    document pipeline.yaml loaded from the dev tree.  The resulting task
    sequence (role, task_id, slug for each task in order) must exactly match
    the golden fixture recorded before the move.  A mismatch means the move
    was NOT invisible; investigate before regenerating the golden.

    The document pipeline.yaml is loaded via load_workflow('document',
    kanban_root=_DEV_TREE_ROOT), exercising the same
    team/workflows/document/pipeline.yaml resolution path as production.
    """
    assert _DOCUMENT_GOLDEN.exists(), (
        f"Document golden fixture not found: {_DOCUMENT_GOLDEN}. "
        "Run the golden-generation step to create it."
    )
    expected = json.loads(_DOCUMENT_GOLDEN.read_text(encoding="utf-8"))

    wf_def = load_workflow("document", kanban_root=str(_DEV_TREE_ROOT))

    tasks: list = []
    pm.inject_document_workflow_tasks(
        tasks,
        workflow_def=wf_def,
        sections=[],          # short-form: no sections
        date_str="20260101",
        base_seq=100,
        owner="CLAUDE",
        git_repo="none",
        target_version="v1.3.0",
        project_cfg_path=None,   # no review_agent → review step skipped
        artifact_name="",
        resolved_source_paths=None,
    )

    actual = _task_signature(tasks)
    assert actual == expected, (
        "Document task-set composition changed — the pipeline.yaml move is NOT invisible.\n"
        f"Expected: {json.dumps(expected, indent=2)}\n"
        f"Actual:   {json.dumps(actual, indent=2)}"
    )


def test_document_decomposition_task_count_matches_golden() -> None:
    """Document decomposition produces the same number of tasks as the golden fixture."""
    expected = json.loads(_DOCUMENT_GOLDEN.read_text(encoding="utf-8"))
    wf_def = load_workflow("document", kanban_root=str(_DEV_TREE_ROOT))
    tasks: list = []
    pm.inject_document_workflow_tasks(
        tasks,
        workflow_def=wf_def,
        sections=[],
        date_str="20260101",
        base_seq=100,
        owner="CLAUDE",
        git_repo="none",
        target_version="v1.3.0",
        project_cfg_path=None,
        artifact_name="",
        resolved_source_paths=None,
    )
    assert len(tasks) == len(expected), (
        f"Task count changed: got {len(tasks)}, expected {len(expected)}"
    )


def test_document_decomposition_role_sequence_matches_golden() -> None:
    """Document decomposition role sequence is byte-identical to the golden fixture."""
    expected = json.loads(_DOCUMENT_GOLDEN.read_text(encoding="utf-8"))
    expected_roles = [e["role"] for e in expected]

    wf_def = load_workflow("document", kanban_root=str(_DEV_TREE_ROOT))
    tasks: list = []
    pm.inject_document_workflow_tasks(
        tasks,
        workflow_def=wf_def,
        sections=[],
        date_str="20260101",
        base_seq=100,
        owner="CLAUDE",
        git_repo="none",
        target_version="v1.3.0",
        project_cfg_path=None,
        artifact_name="",
        resolved_source_paths=None,
    )
    actual_roles = [t["role"] for t in tasks]
    assert actual_roles == expected_roles, (
        f"Role sequence changed: got {actual_roles}, expected {expected_roles}"
    )


def test_document_decomposition_starts_with_cm_open_doc() -> None:
    """Document decomposition always begins with a CM open-doc task."""
    wf_def = load_workflow("document", kanban_root=str(_DEV_TREE_ROOT))
    tasks: list = []
    pm.inject_document_workflow_tasks(
        tasks,
        workflow_def=wf_def,
        sections=[],
        date_str="20260101",
        base_seq=100,
        owner="CLAUDE",
        git_repo="none",
        target_version="v1.3.0",
        project_cfg_path=None,
        artifact_name="",
        resolved_source_paths=None,
    )
    assert tasks, "Document decomposition must produce at least one task"
    first = tasks[0]
    assert first["role"] == "CM", (
        f"First task must be CM (open-doc); got role={first['role']!r}"
    )
    assert "open-doc" in first.get("slug", ""), (
        f"First task must be the open-doc CM task; got slug={first.get('slug')!r}"
    )


def test_document_decomposition_ends_with_cm_finalize() -> None:
    """Document decomposition always ends with a CM finalize task."""
    wf_def = load_workflow("document", kanban_root=str(_DEV_TREE_ROOT))
    tasks: list = []
    pm.inject_document_workflow_tasks(
        tasks,
        workflow_def=wf_def,
        sections=[],
        date_str="20260101",
        base_seq=100,
        owner="CLAUDE",
        git_repo="none",
        target_version="v1.3.0",
        project_cfg_path=None,
        artifact_name="",
        resolved_source_paths=None,
    )
    assert tasks, "Document decomposition must produce at least one task"
    last = tasks[-1]
    assert last["role"] == "CM", (
        f"Last task must be CM (finalize); got role={last['role']!r}"
    )
    assert "finalize" in last.get("slug", ""), (
        f"Last task must be the finalize CM task; got slug={last.get('slug')!r}"
    )
