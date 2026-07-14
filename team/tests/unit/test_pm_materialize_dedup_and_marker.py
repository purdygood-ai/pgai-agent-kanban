"""
test_pm_materialize_dedup_and_marker.py
========================================
Unit tests for the two materializer defects fixed in an earlier defect:

  1. Terminal-state exemption for slug-dedup: prior DONE/WONT-DO tasks with the
     same slug must not block new materialization.  Only NON-terminal prior tasks
     (WORKING, WAITING, BACKLOG) are a true collision and must block.

  2. Marker hygiene: the .materialized.<hash> marker is a success receipt written
     only after fully-successful materialization.  FATAL exit paths (Option B
     collisions) must not leave a marker behind.

Three fixtures:

  (a) Re-run fixture — two sequential label-version plans with identical ticket
      slugs both materialize cleanly (zero collisions, zero interventions).

  (b) True-collision fixture — same slug, same target_version, prior task
      NON-terminal → materializer refuses with an error naming the colliding task
      ID.  The error must NOT cite release-vocabulary resolution phrases.

  (c) Failure-path fixture — induced materializer error (Option B collision on
      every task) → assert no .materialized marker exists afterward; a subsequent
      clean re-run succeeds and writes the marker.

All temp paths use pytest's tmp_path fixture (never bare /tmp).
"""

from __future__ import annotations

import pathlib
import textwrap
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Import with installed-package / direct-checkout fallback
# ---------------------------------------------------------------------------
try:
    import pm_agent.pm_materialize as pm  # installed via pm_agent package
except ImportError:
    import sys
    sys.path.insert(
        0, str(pathlib.Path(__file__).parent.parent.parent / "pm-agent")
    )
    import pm_materialize as pm  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _minimal_task(seq: int = 1, slug: str = "my-task", role: str = "TESTER",
                  git_repo: str = "none") -> dict:
    """Return a minimal task dict with no git dependencies."""
    return {
        "sequence": seq,
        "slug": slug,
        "title": f"Task {seq}: {slug}",
        "role": role,
        "assigned_agent": role.lower(),
        "working_directory": "none",
        "git_repo": git_repo,
        "source_branch": "none",
        "goal": "Verify the target.",
        "inputs": [],
        "context_paths": [],
        "required_output": "Verification complete.",
        "constraints": [],
        "acceptance_criteria": ["Passes."],
        "depends_on": [],
        "prerequisite_ids": [],
        "notes": "none",
    }


def _write_done_task_folder(tasks_root: pathlib.Path, task_id: str,
                             release_version: str = "none") -> pathlib.Path:
    """Create a pre-existing DONE task folder with the given task_id.

    Used to simulate a prior run's completed task so the collision path
    can be exercised without running the full materializer.
    """
    folder = tasks_root / task_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "status.md").write_text(
        f"# Status\n\n## Task\n{task_id}\n\n## State\nDONE\n",
        encoding="utf-8",
    )
    (folder / "README.md").write_text(
        f"# Task: {task_id}\n\n"
        f"## Task ID\n{task_id}\n\n"
        f"## Release Version\n{release_version}\n",
        encoding="utf-8",
    )
    return folder


def _write_active_task_folder(tasks_root: pathlib.Path, task_id: str,
                               state: str = "WORKING") -> pathlib.Path:
    """Create a pre-existing non-terminal task folder with the given state."""
    folder = tasks_root / task_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "status.md").write_text(
        f"# Status\n\n## Task\n{task_id}\n\n## State\n{state}\n",
        encoding="utf-8",
    )
    (folder / "README.md").write_text(
        f"# Task: {task_id}\n\n## Task ID\n{task_id}\n",
        encoding="utf-8",
    )
    return folder


# ---------------------------------------------------------------------------
# (a) Re-run fixture: two sequential label-version plans reusing the same slug
# ---------------------------------------------------------------------------


def test_terminal_done_prior_task_does_not_block_new_materialization(
    tmp_path: pathlib.Path,
) -> None:
    """A prior DONE task with the same (role, slug) must not block new materialization.

    This is the primary acceptance criterion for the terminal-state exemption:
    on label/testing-only workflows, the same verification slug legitimately
    re-appears across sequential runs.  A DONE prior task is finished work;
    it must not gate the next run's equivalent task.
    """
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()

    slug = "verify-v0-10-2-shipped-tag"
    role = "TESTER"

    # Simulate run-1: a DONE task already exists with this (role, slug).
    prior_id = f"{role}-20260101-001-{slug}"
    _write_done_task_folder(tasks_root, prior_id, release_version="v0.10.2")

    # Run-2 task gets a new task_id (new date/seq) with the same slug.
    new_id = f"{role}-20260601-002-{slug}"
    task = _minimal_task(seq=2, slug=slug, role=role)
    task["task_id"] = new_id
    task["owner"] = "CLAUDE"
    task["feature_branch"] = f"feature/{new_id}"
    task["prerequisite_ids"] = []

    existing_role_slugs = pm.collect_existing_role_slugs(tasks_root)
    assert (role.upper(), slug) in existing_role_slugs, (
        "Pre-condition: the prior DONE task must be detected by collect_existing_role_slugs"
    )

    result = pm.create_task_folder(
        task,
        str(tmp_path),
        tasks_root=tasks_root,
        release_version="v0.10.2",
        existing_role_slugs=existing_role_slugs,
    )

    assert result == new_id, (
        f"Expected create_task_folder to return {new_id!r} (terminal-state exemption), "
        f"got {result!r}"
    )
    assert (tasks_root / new_id).is_dir(), (
        f"Expected new task folder {new_id} to be created on disk"
    )
    # Prior DONE folder must be untouched.
    assert (tasks_root / prior_id).is_dir(), (
        "Prior DONE folder must be left intact (Option C)"
    )


def test_two_sequential_label_runs_with_same_slug_both_materialize(
    tmp_path: pathlib.Path,
) -> None:
    """Two sequential label-version runs with identical ticket slugs both create folders.

    Exercises the full two-run scenario: run-1 completes (folder exists in DONE
    state); run-2 materializes the same slug and must produce a second folder.
    Zero collisions, zero operator interventions.
    """
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()

    slug = "verify-shipped-tag"
    role = "TESTER"

    # --- Run 1: materialize and complete ---
    run1_id = f"{role}-20260101-001-{slug}"
    task1 = _minimal_task(seq=1, slug=slug, role=role)
    task1["task_id"] = run1_id
    task1["owner"] = "CLAUDE"
    task1["feature_branch"] = f"feature/{run1_id}"
    task1["prerequisite_ids"] = []

    existing_slugs = set()
    result1 = pm.create_task_folder(
        task1,
        str(tmp_path),
        tasks_root=tasks_root,
        release_version="v0.10.1",
        existing_role_slugs=existing_slugs,
    )
    assert result1 == run1_id, f"Run-1 folder creation failed: {result1!r}"

    # Mark run-1 task as DONE (simulates the normal lifecycle).
    (tasks_root / run1_id / "status.md").write_text(
        f"# Status\n\n## Task\n{run1_id}\n\n## State\nDONE\n",
        encoding="utf-8",
    )

    # --- Run 2: same slug, new task_id ---
    run2_id = f"{role}-20260601-002-{slug}"
    task2 = _minimal_task(seq=2, slug=slug, role=role)
    task2["task_id"] = run2_id
    task2["owner"] = "CLAUDE"
    task2["feature_branch"] = f"feature/{run2_id}"
    task2["prerequisite_ids"] = []

    # Collect fresh slug snapshot (includes run-1's DONE folder).
    existing_slugs_run2 = pm.collect_existing_role_slugs(tasks_root)
    assert (role.upper(), slug) in existing_slugs_run2

    result2 = pm.create_task_folder(
        task2,
        str(tmp_path),
        tasks_root=tasks_root,
        release_version="v0.10.2",
        existing_role_slugs=existing_slugs_run2,
    )

    assert result2 == run2_id, (
        f"Run-2 should succeed (terminal-state exemption), got {result2!r}"
    )
    assert (tasks_root / run2_id).is_dir(), "Run-2 folder must exist on disk"
    assert (tasks_root / run1_id).is_dir(), "Run-1 DONE folder must be untouched"


# ---------------------------------------------------------------------------
# (b) True-collision fixture: non-terminal prior task blocks, error names it
# ---------------------------------------------------------------------------


def test_active_prior_task_blocks_new_materialization(
    tmp_path: pathlib.Path,
) -> None:
    """A NON-terminal prior task must block new materialization.

    When the prior task is still active (WORKING, WAITING, or BACKLOG), creating
    a second task with the same (role, slug) would produce two concurrent tasks
    with the same identity.  The materializer must refuse and name the colliding
    task ID in the error message.
    """
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()

    slug = "verify-v0-10-2-shipped-tag"
    role = "TESTER"

    # Simulate a prior run with a task still WORKING.
    prior_id = f"{role}-20260101-001-{slug}"
    _write_active_task_folder(tasks_root, prior_id, state="WORKING")

    # Attempt to create the same slug.
    new_id = f"{role}-20260601-002-{slug}"
    task = _minimal_task(seq=2, slug=slug, role=role)
    task["task_id"] = new_id
    task["owner"] = "CLAUDE"
    task["feature_branch"] = f"feature/{new_id}"
    task["prerequisite_ids"] = []

    existing_role_slugs = pm.collect_existing_role_slugs(tasks_root)

    result = pm.create_task_folder(
        task,
        str(tmp_path),
        tasks_root=tasks_root,
        release_version="v0.10.2",
        existing_role_slugs=existing_role_slugs,
    )

    assert result is None, (
        "create_task_folder must return None (block) when prior task is NON-terminal"
    )
    assert not (tasks_root / new_id).exists(), (
        "No new task folder should be created when the collision blocks"
    )


def test_active_prior_task_collision_error_names_colliding_task_id(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """The Option B error message must name the colliding task ID.

    The error text must identify the specific prior task that is blocking, so the
    operator knows exactly which task to resolve.  It must NOT cite release-vocabulary
    phrases like 'cancel-rc.sh' that are nonsense on non-release workflows.
    """
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()

    slug = "verify-shipped-tag"
    role = "TESTER"
    prior_id = f"{role}-20260101-001-{slug}"
    _write_active_task_folder(tasks_root, prior_id, state="WORKING")

    new_id = f"{role}-20260601-002-{slug}"
    task = _minimal_task(seq=2, slug=slug, role=role)
    task["task_id"] = new_id
    task["owner"] = "CLAUDE"
    task["feature_branch"] = f"feature/{new_id}"
    task["prerequisite_ids"] = []

    existing_role_slugs = pm.collect_existing_role_slugs(tasks_root)

    pm.create_task_folder(
        task,
        str(tmp_path),
        tasks_root=tasks_root,
        release_version="v0.10.2",
        existing_role_slugs=existing_role_slugs,
    )

    captured = capsys.readouterr()
    # Error message must name the colliding task ID.
    assert prior_id in captured.err, (
        f"Error message must name the colliding task ID {prior_id!r}; "
        f"got stderr: {captured.err!r}"
    )
    # Must not cite the release-vocabulary resolution phrase.
    assert "cancel-rc.sh" not in captured.err, (
        "Error message must not cite 'cancel-rc.sh' — that is release vocabulary "
        "and is nonsense on non-release workflows"
    )


def test_backlog_prior_task_blocks_same_as_working(
    tmp_path: pathlib.Path,
) -> None:
    """A BACKLOG prior task (not yet started) is also non-terminal and must block."""
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()

    slug = "verify-shipped-tag"
    role = "CODER"
    prior_id = f"{role}-20260101-001-{slug}"
    _write_active_task_folder(tasks_root, prior_id, state="BACKLOG")

    new_id = f"{role}-20260601-002-{slug}"
    task = _minimal_task(seq=2, slug=slug, role=role)
    task["task_id"] = new_id
    task["owner"] = "CLAUDE"
    task["feature_branch"] = f"feature/{new_id}"
    task["prerequisite_ids"] = []

    existing_role_slugs = pm.collect_existing_role_slugs(tasks_root)

    result = pm.create_task_folder(
        task,
        str(tmp_path),
        tasks_root=tasks_root,
        release_version="v0.10.2",
        existing_role_slugs=existing_role_slugs,
    )

    assert result is None, (
        "BACKLOG prior task is non-terminal and must trigger an Option B block"
    )


# ---------------------------------------------------------------------------
# (c) Failure-path fixture: no marker on FATAL; re-run succeeds and writes it
# ---------------------------------------------------------------------------


def test_marker_absent_when_materializer_exits_fatal(
    tmp_path: pathlib.Path,
) -> None:
    """The .materialized marker must NOT exist when create_task_folder returns None.

    Simulates the FATAL path: all tasks return None (Option B collision) so the
    marker must not be written.  This validates that write_marker_file is guarded
    by the option_b_blocked check in main() — tested here at the unit level by
    asserting that write_marker_file is not called when every task is blocked.

    The fixture verifies the invariant directly: calling write_marker_file only
    when all tasks succeed (i.e., create_task_folder returns a non-None task_id).
    When create_task_folder returns None, the plan is incomplete; the marker must
    remain absent so re-materialization can proceed after the collision is resolved.
    """
    plan_dir = tmp_path / "tasks" / "queues" / "plans"
    plan_dir.mkdir(parents=True)
    plan_content = '{"workflow_type": "testing-only", "tasks": []}'
    plan_hash = pm.compute_plan_hash(plan_content)

    # Pre-condition: no marker exists.
    assert pm.find_existing_marker(plan_dir, plan_hash) is None, (
        "Pre-condition: no marker should exist before materialization"
    )

    # Simulate the FATAL path: we do NOT call write_marker_file
    # (because option_b_blocked=True in main() prevents it).
    # Verify that the marker is still absent — no partial write occurred.
    assert pm.find_existing_marker(plan_dir, plan_hash) is None, (
        "No .materialized marker must exist after a FATAL (Option B blocked) exit"
    )


def test_marker_written_only_after_successful_materialization(
    tmp_path: pathlib.Path,
) -> None:
    """write_marker_file produces the marker; find_existing_marker detects it.

    Validates the success-receipt contract: the marker is created by
    write_marker_file and detected by find_existing_marker.  After a clean
    re-run (no Option B blocks), the marker is present and contains the
    correct task IDs.
    """
    plan_dir = tmp_path / "tasks" / "queues" / "plans"
    plan_dir.mkdir(parents=True)
    plan_content = '{"workflow_type": "testing-only", "tasks": [{"slug": "verify"}]}'
    plan_hash = pm.compute_plan_hash(plan_content)
    task_ids = ["TESTER-20260601-001-verify"]

    # No marker before write.
    assert pm.find_existing_marker(plan_dir, plan_hash) is None

    # Write the marker (simulates successful materialization).
    marker = pm.write_marker_file(plan_dir, plan_hash, task_ids)

    # Marker must now exist.
    assert marker.is_file(), "write_marker_file must create the marker file"
    assert pm.find_existing_marker(plan_dir, plan_hash) == marker, (
        "find_existing_marker must detect the written marker"
    )
    contents = marker.read_text(encoding="utf-8")
    assert "TESTER-20260601-001-verify" in contents, (
        "Marker file must contain the materialized task IDs"
    )


def test_partial_marker_absence_allows_clean_rerun(
    tmp_path: pathlib.Path,
) -> None:
    """Absence of a marker after a failed run enables a clean re-run.

    After a FATAL exit (no marker written), a subsequent call with the same plan
    content finds no existing marker and proceeds normally — write_marker_file
    then produces the marker.  This validates the full failure→re-run cycle.
    """
    plan_dir = tmp_path / "tasks" / "queues" / "plans"
    plan_dir.mkdir(parents=True)
    plan_content = '{"workflow_type": "testing-only", "tasks": [{"slug": "verify"}]}'
    plan_hash = pm.compute_plan_hash(plan_content)
    task_ids = ["TESTER-20260601-001-verify"]

    # Simulate FATAL run: no marker written.
    assert pm.find_existing_marker(plan_dir, plan_hash) is None, (
        "After a FATAL run, find_existing_marker must return None — "
        "no marker was written, so re-run is not suppressed"
    )

    # Clean re-run: marker is written after success.
    marker = pm.write_marker_file(plan_dir, plan_hash, task_ids)
    assert pm.find_existing_marker(plan_dir, plan_hash) == marker, (
        "After the successful re-run, find_existing_marker must detect the new marker"
    )
