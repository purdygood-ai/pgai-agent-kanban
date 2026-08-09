"""
test_pm_materialize_intra_batch_dedupe.py
==========================================
Unit and end-to-end tests for the intra-materialization dedupe guard added
for BUG-0033 (pm-materialization-accepts-duplicate-tasks).

The guard makes ``team/pm-agent/pm_materialize.py`` fail-closed against a
context-degraded model re-emitting tasks it already produced within a
single plan JSON: a second TESTER-verify task, a second CM open-rc or CM
release task, or a near-duplicate worker task (same role, same or
slug-normalized-identical slug/title) is skipped rather than written.

Two layers of coverage:

  1. Direct unit tests on ``dedupe_materialization_tasks`` and its helper
     functions (``_normalize_dedup_key``, ``_closer_purpose_key``,
     ``_worker_dedup_key``) — fast, precise coverage of the dedupe logic
     itself.
  2. End-to-end tests that invoke the REAL materialization entry point
     (``python3 team/pm-agent/pm_materialize.py <plan.json> ...`` as a
     subprocess, exactly as ``pm-agent.sh`` invokes it) so the guard is
     verified through the production entry point, not only via the helper
     in isolation — per the task's "Verify through the REAL materialization
     entry point" constraint.

All temp paths use pytest's ``tmp_path`` fixture (never bare /tmp).
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
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

# ---------------------------------------------------------------------------
# Paths for the end-to-end (real entry point) tests
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_DEV_TREE_ROOT = _TEAM_DIR.parent                          # dev tree root (parent of team/)
_MATERIALIZE_SCRIPT = _TEAM_DIR / "pm-agent" / "pm_materialize.py"


# ---------------------------------------------------------------------------
# Layer 1 — direct unit tests on the dedupe helpers
# ---------------------------------------------------------------------------


def _task(task_id, role, slug, synthetic=False, cm_operation=None,
          prereqs=None, title=None):
    """Build a minimal already-ID-assigned task dict for dedupe unit tests."""
    t = {
        "task_id": task_id,
        "role": role,
        "slug": slug,
        "title": title or slug,
        "prerequisite_ids": list(prereqs or []),
    }
    if synthetic:
        t["_synthetic"] = True
    if cm_operation:
        t["cm_operation"] = cm_operation
    return t


def test_normalize_dedup_key_collapses_formatting_differences() -> None:
    """Whitespace, case, and separator differences normalize to the same key."""
    assert pm._normalize_dedup_key("Add Login Endpoint") == "add-login-endpoint"
    assert pm._normalize_dedup_key("add_login_endpoint") == "add-login-endpoint"
    assert pm._normalize_dedup_key("  add-login-endpoint  ") == "add-login-endpoint"
    assert pm._normalize_dedup_key("") == ""
    assert pm._normalize_dedup_key(None) == ""


def test_closer_purpose_key_identifies_tester_verify() -> None:
    """A non-doc-review TESTER-role task is the tester-verify purpose when the
    materialization emits CM release-lifecycle bookends (the default)."""
    assert pm._closer_purpose_key({"role": "TESTER", "slug": "verify"}) == "tester-verify"
    assert pm._closer_purpose_key(
        {"role": "TESTER", "slug": "verify"}, emit_cm_bookends=True
    ) == "tester-verify"


def test_closer_purpose_key_tester_falls_through_without_cm_bookends() -> None:
    """BUG-0035: without CM bookends, TESTER is not a chain-closer purpose.

    Workflows that never emit CM bookends (testing-only, feature, document)
    may legitimately carry more than one distinct-purpose TESTER task in the
    same materialization, so the closer bucket must not claim them — they
    fall through to the generic (role, slug) worker-dedup path instead.
    """
    assert pm._closer_purpose_key(
        {"role": "TESTER", "slug": "verify-the-implementation"}, emit_cm_bookends=False
    ) is None
    assert pm._closer_purpose_key(
        {"role": "TESTER", "slug": "verify-and-report"}, emit_cm_bookends=False
    ) is None


def test_closer_purpose_key_excludes_doc_review_tester_tasks() -> None:
    """A document-workflow TESTER-review task is not a tester-verify closer."""
    assert pm._closer_purpose_key(
        {"role": "TESTER", "slug": "review", "_doc_review": True}
    ) is None


def test_closer_purpose_key_uses_cm_operation_when_present() -> None:
    """Synthetic CM tasks are classified via the authoritative cm_operation field."""
    assert pm._closer_purpose_key(
        {"role": "CM", "slug": "anything", "cm_operation": "open-rc"}
    ) == "cm-open-rc"
    assert pm._closer_purpose_key(
        {"role": "CM", "slug": "anything", "cm_operation": "release"}
    ) == "cm-release"


def test_closer_purpose_key_falls_back_to_slug_keywords_for_cm_tasks() -> None:
    """A model-emitted CM task with no cm_operation is classified by slug keywords."""
    assert pm._closer_purpose_key(
        {"role": "CM", "slug": "open-the-rc-branch-again"}
    ) == "cm-open-rc"
    assert pm._closer_purpose_key(
        {"role": "CM", "slug": "cut-the-release-again"}
    ) == "cm-release"


def test_closer_purpose_key_does_not_match_document_cm_operations() -> None:
    """Document-workflow open_doc/finalize CM tasks are not release chain closers."""
    assert pm._closer_purpose_key(
        {"role": "CM", "slug": "open-doc-v1", "cm_operation": "open_doc"}
    ) is None
    assert pm._closer_purpose_key(
        {"role": "CM", "slug": "finalize-v1", "cm_operation": "finalize"}
    ) is None


def test_dedupe_keeps_synthetic_bookend_over_model_emitted_lookalike() -> None:
    """Synthetic chain closers always win, regardless of list position.

    Reproduces the shape assembled by main(): the synthetic CM-open is
    prepended (position 1), but a model-duplicate CM-release-lookalike can
    land inside the ordered work tasks BEFORE the real synthetic CM-release
    (which is always appended last). The synthetic task must still win.
    """
    tasks = [
        _task("CM-1", "CM", "open-rc-v1", synthetic=True, cm_operation="open-rc"),
        _task("CODER-1", "CODER", "implement-thing", prereqs=["CM-1"]),
        _task("CM-fake-open", "CM", "open-the-rc-branch"),
        _task("TESTER-fake", "TESTER", "verify-release"),
        _task("TESTER-1", "TESTER", "verify-and-report", synthetic=True, prereqs=["CODER-1"]),
        _task("CM-fake-release", "CM", "cut-the-release"),
        _task("CM-2", "CM", "release-v1", synthetic=True, cm_operation="release", prereqs=["TESTER-1"]),
    ]

    kept, narrations, count = pm.dedupe_materialization_tasks(tasks)

    assert [t["task_id"] for t in kept] == ["CM-1", "CODER-1", "TESTER-1", "CM-2"], (
        "Only the synthetic chain closers and the real work task must survive"
    )
    assert count == 3
    assert len(narrations) == 3
    for narration in narrations:
        assert narration.startswith("dedupe: skipped duplicate "), narration
        assert " (matches " in narration, narration


def test_dedupe_narration_names_both_the_duplicate_and_the_survivor() -> None:
    """Each narration line names the skipped slug AND the surviving task's key."""
    tasks = [
        _task("CM-1", "CM", "open-rc-v1", synthetic=True, cm_operation="open-rc"),
        _task("CM-fake-open", "CM", "open-the-rc-branch-again"),
    ]
    _, narrations, count = pm.dedupe_materialization_tasks(tasks)
    assert count == 1
    assert narrations == [
        "dedupe: skipped duplicate open-the-rc-branch-again (matches CM-1)"
    ]


def test_dedupe_worker_task_near_duplicate_slug_is_skipped() -> None:
    """Two CODER tasks with identical (slug-normalized) identity: only the first survives."""
    tasks = [
        _task("CODER-1", "CODER", "add-login-endpoint"),
        _task("CODER-2", "CODER", "Add Login Endpoint"),  # normalizes identically
    ]
    kept, narrations, count = pm.dedupe_materialization_tasks(tasks)
    assert [t["task_id"] for t in kept] == ["CODER-1"]
    assert count == 1


def test_dedupe_worker_tasks_with_distinct_slugs_both_survive() -> None:
    """Same-role tasks with genuinely distinct slugs are never false-positived."""
    tasks = [
        _task("CODER-1", "CODER", "add-login-endpoint"),
        _task("CODER-2", "CODER", "add-logout-endpoint"),
    ]
    kept, narrations, count = pm.dedupe_materialization_tasks(tasks)
    assert [t["task_id"] for t in kept] == ["CODER-1", "CODER-2"]
    assert count == 0
    assert narrations == []


def test_dedupe_golden_path_is_byte_identical_no_op() -> None:
    """A normal single-emission task list passes through completely unchanged."""
    tasks = [
        _task("CM-1", "CM", "open-rc-v1", synthetic=True, cm_operation="open-rc"),
        _task("CODER-1", "CODER", "implement-thing", prereqs=["CM-1"]),
        _task("TESTER-1", "TESTER", "verify-and-report", synthetic=True, prereqs=["CODER-1"]),
        _task("CM-2", "CM", "release-v1", synthetic=True, cm_operation="release", prereqs=["TESTER-1"]),
    ]
    kept, narrations, count = pm.dedupe_materialization_tasks(tasks)
    assert kept == tasks
    assert narrations == []
    assert count == 0


def test_dedupe_rewrites_prerequisite_ids_pointing_at_skipped_duplicates() -> None:
    """A kept task that depended on a skipped duplicate is rewired to the survivor."""
    tasks = [
        _task("CM-1", "CM", "open-rc-v1", synthetic=True, cm_operation="open-rc"),
        _task("CM-fake-open", "CM", "open-the-rc-branch-again"),
        # A downstream task mistakenly depends on the duplicate's task_id.
        _task("CODER-1", "CODER", "implement-thing", prereqs=["CM-fake-open"]),
    ]
    kept, _, _ = pm.dedupe_materialization_tasks(tasks)
    coder_task = next(t for t in kept if t["task_id"] == "CODER-1")
    assert coder_task["prerequisite_ids"] == ["CM-1"], (
        "prerequisite_ids referencing a skipped duplicate must be remapped "
        "to the surviving task's task_id"
    )


# ---------------------------------------------------------------------------
# BUG-0035 — slug-aware TESTER closer bucket (no-CM-bookend workflows)
# ---------------------------------------------------------------------------


def test_dedupe_distinct_slug_tester_tasks_both_survive_without_cm_bookends() -> None:
    """The testing-only shape: a plan work-ticket TESTER task plus an injected
    'verify-and-report' companion carry distinct slugs and must both survive
    when the materialization does not emit CM bookends (BUG-0035).
    """
    tasks = [
        _task("TESTER-1", "TESTER", "verify-the-implementation"),
        _task("TESTER-2", "TESTER", "verify-and-report", synthetic=True),
    ]
    kept, narrations, count = pm.dedupe_materialization_tasks(
        tasks, emit_cm_bookends=False
    )
    assert [t["task_id"] for t in kept] == ["TESTER-1", "TESTER-2"]
    assert count == 0
    assert narrations == []


def test_dedupe_same_slug_tester_reemission_still_collapses_without_cm_bookends() -> None:
    """BUG-0033's genuine catch survives the BUG-0035 fix: a wobbling model
    re-emitting the SAME TESTER slug still collapses to one, even in a
    no-CM-bookend workflow, because same-slug identity is exactly what the
    generic worker-dedup path (role, slug) is built to catch.
    """
    tasks = [
        _task("TESTER-1", "TESTER", "verify-the-implementation"),
        _task("TESTER-2", "TESTER", "Verify The Implementation"),  # normalizes identically
    ]
    kept, narrations, count = pm.dedupe_materialization_tasks(
        tasks, emit_cm_bookends=False
    )
    assert [t["task_id"] for t in kept] == ["TESTER-1"]
    assert count == 1
    assert narrations == [
        "dedupe: skipped duplicate Verify The Implementation (matches TESTER-1)"
    ]


def test_dedupe_tester_verify_still_collapses_to_one_with_cm_bookends() -> None:
    """BUG-0033 regression guard: with CM bookends (the release-lifecycle
    shape), any additional TESTER task — regardless of its slug — is still
    presumptively the wobble-duplicate and collapses to the single
    synthetic TESTER closer. This is the behavior BUG-0035 must NOT change.
    """
    tasks = [
        _task("TESTER-1", "TESTER", "verify-v1", synthetic=True),
        _task("TESTER-2", "TESTER", "verify-release-again"),  # distinct slug, same purpose
    ]
    kept, narrations, count = pm.dedupe_materialization_tasks(
        tasks, emit_cm_bookends=True
    )
    assert [t["task_id"] for t in kept] == ["TESTER-1"]
    assert count == 1
    assert narrations == [
        "dedupe: skipped duplicate verify-release-again (matches TESTER-1)"
    ]


def test_dedupe_cm_buckets_unaffected_by_emit_cm_bookends_flag() -> None:
    """The CM open-rc / release purpose buckets are not workflow-shape gated:
    they still collapse doubled emissions with the same cm_operation
    regardless of the emit_cm_bookends flag passed to the dedupe pass (the
    flag only changes TESTER handling). Confirms BUG-0035's fix does not
    over-collapse — or under-collapse — the CM buckets.
    """
    tasks = [
        _task("CM-1", "CM", "open-rc-v1", synthetic=True, cm_operation="open-rc"),
        _task("CM-fake-open", "CM", "open-the-rc-branch-again"),
        _task("CM-2", "CM", "release-v1", synthetic=True, cm_operation="release"),
        _task("CM-fake-release", "CM", "cut-the-release-again"),
    ]
    kept, _, count = pm.dedupe_materialization_tasks(tasks, emit_cm_bookends=False)
    assert [t["task_id"] for t in kept] == ["CM-1", "CM-2"]
    assert count == 2


def test_dedupe_cm_open_rc_and_release_are_distinct_buckets_never_cross_collapse() -> None:
    """A legitimate CM open-rc task and a legitimate CM release task in the
    same materialization are different purposes and must never collapse
    against each other, confirming the CM buckets are not over-collapsed.
    """
    tasks = [
        _task("CM-1", "CM", "open-rc-v1", synthetic=True, cm_operation="open-rc"),
        _task("CM-2", "CM", "release-v1", synthetic=True, cm_operation="release"),
    ]
    kept, narrations, count = pm.dedupe_materialization_tasks(tasks)
    assert [t["task_id"] for t in kept] == ["CM-1", "CM-2"]
    assert count == 0
    assert narrations == []


# ---------------------------------------------------------------------------
# Layer 2 — end-to-end tests through the real materialization entry point
# ---------------------------------------------------------------------------


def _base_task(sequence, slug, role, title=None, depends_on=None):
    """Build a minimal plan-JSON task entry (pre-ID-assignment schema)."""
    return {
        "sequence": sequence,
        "slug": slug,
        "title": title or slug.replace("-", " ").title(),
        "role": role,
        "assigned_agent": role.lower(),
        "working_directory": "REPLACED_BELOW",
        "git_repo": "none",
        "source_branch": "none",
        "goal": "Do the work.",
        "inputs": [],
        "context_paths": [],
        "required_output": "Done.",
        "constraints": [],
        "acceptance_criteria": ["It works."],
        "depends_on": depends_on or [],
        "notes": "none",
    }


def _run_materialize(plan: dict, team_root: pathlib.Path, tmp_path: pathlib.Path):
    """Invoke the real pm_materialize.py CLI entry point as a subprocess.

    Points PGAI_AGENT_KANBAN_ROOT_PATH at THIS dev tree (not the live
    install) so load_workflow()/load_workflow_capabilities() resolve the
    workflow definitions from the code under test, per the Live-Install
    Safety convention (never exercise scripts against the operator's live
    kanban root).
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    for task in plan.get("tasks", []):
        task["working_directory"] = str(workspace)

    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    # Isolation, per CODER.md "Live-Install Safety": strip every PGAI_*_DIR
    # override the ambient environment (or an inherited operator shell) may
    # carry, so --team-root is the sole authority for where this invocation
    # writes. Without this, an inherited PGAI_TASKS_DIR (from the operator's
    # shell profile, or a differently-scoped test harness) silently redirects
    # task-folder writes away from *team_root* and this test's on-disk
    # assertions would either false-fail or, worse, land in a live install.
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(_DEV_TREE_ROOT)
    for _dangerous_var in (
        "PGAI_PROJECT_ROOT", "PGAI_DEV_TREE_PATH", "PGAI_TASKS_DIR",
        "PGAI_REQUIREMENTS_DIR", "PGAI_PRIORITY_DIR", "PGAI_ARCHIVE_DIR",
        "PGAI_BRIEFS_DIR", "PGAI_LOGS_DIR",
    ):
        env.pop(_dangerous_var, None)

    result = subprocess.run(
        [
            sys.executable, str(_MATERIALIZE_SCRIPT), str(plan_path),
            "--team-root", str(team_root),
            "--owner", "CLAUDE",
        ],
        capture_output=True, text=True, env=env, timeout=60,
    )
    return result


def _task_ids_by_role(tasks_root: pathlib.Path, role: str) -> list:
    """Return sorted task_id strings for folders under tasks_root matching role."""
    if not tasks_root.is_dir():
        return []
    return sorted(
        d.name for d in tasks_root.iterdir()
        if d.is_dir() and d.name.upper().startswith(f"{role.upper()}-")
    )


def test_real_entry_point_dedupes_doubled_chain_closers_and_worker_tasks(
    tmp_path: pathlib.Path,
) -> None:
    """End-to-end: doubled TESTER-verify + CM open/release pairs + a near-dup
    CODER task all collapse to exactly one materialized task each.

    Reproduces the BUG-0033 production shape: alongside its two legitimate
    CODER work tasks, the plan JSON also emits its own TESTER-role and
    CM-role tasks (permitted by the release workflow roster) that duplicate
    the purposes the materializer always injects synthetically. The real
    CLI entry point must materialize exactly one CM-open, one TESTER, and
    one CM-release, plus the two distinct-slug CODER tasks — no duplicates
    in the queues, and every skip narrated on stderr.
    """
    team_root = tmp_path / "team_root"
    team_root.mkdir()

    plan = {
        "workflow_type": "release",
        "target_version": "v1.0.0",
        "test_required": "true",
        "human_approval_required": "auto",
        "project_name": "dedupe-guard-fixture",
        "tasks": [
            _base_task(1, "implement-thing", "CODER"),
            _base_task(2, "implement-other-thing", "CODER"),
            # Model-emitted duplicates of the always-injected chain closers.
            _base_task(3, "open-the-rc-branch-again", "CM"),
            _base_task(4, "verify-release-again", "TESTER"),
            _base_task(5, "cut-the-release-again", "CM"),
            # Model-emitted near-duplicate of task 1 (same slug, different case).
            _base_task(6, "Implement Thing", "CODER"),
        ],
    }

    result = _run_materialize(plan, team_root, tmp_path)
    assert result.returncode == 0, (
        f"Materialization failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )

    stderr = result.stderr
    assert "Dedupe: 4 duplicate task(s) skipped" in stderr, stderr
    assert stderr.count("dedupe: skipped duplicate ") == 4, stderr
    assert "Duplicate tasks skipped (dedupe): 4" in stderr, stderr

    tasks_root = team_root / "tasks"
    coder_ids = _task_ids_by_role(tasks_root, "CODER")
    tester_ids = _task_ids_by_role(tasks_root, "TESTER")
    cm_ids = _task_ids_by_role(tasks_root, "CM")

    assert len(coder_ids) == 2, f"Expected exactly 2 CODER tasks, got: {coder_ids}"
    assert len(tester_ids) == 1, f"Expected exactly 1 TESTER task, got: {tester_ids}"
    assert len(cm_ids) == 2, f"Expected exactly 2 CM tasks (open-rc + release), got: {cm_ids}"

    # Queue files must contain no duplicate task IDs.
    for backlog_name in ("coder_backlog.md", "tester_backlog.md", "cm_backlog.md"):
        backlog_path = tasks_root / "queues" / backlog_name
        if not backlog_path.is_file():
            continue
        lines = [l for l in backlog_path.read_text().splitlines() if l.strip()]
        ids_in_queue = [l.split()[-1] for l in lines]
        assert len(ids_in_queue) == len(set(ids_in_queue)), (
            f"{backlog_name} contains duplicate task IDs: {ids_in_queue}"
        )


def test_real_entry_point_golden_path_reports_zero_dedupe(
    tmp_path: pathlib.Path,
) -> None:
    """End-to-end golden path: a normal single-emission decompose materializes
    cleanly with dedupe count 0 and the expected chain-closer shape.

    This is the zero-behavior-change acceptance check: a well-behaved model
    emission (no duplicate tasks) must produce exactly the tasks the
    workflow shape calls for — one CM-open, the work tasks, one TESTER, one
    CM-release — with the dedupe pass reporting zero skips.
    """
    team_root = tmp_path / "team_root"
    team_root.mkdir()

    plan = {
        "workflow_type": "release",
        "target_version": "v2.0.0",
        "test_required": "true",
        "human_approval_required": "auto",
        "project_name": "dedupe-guard-golden-path",
        "tasks": [
            _base_task(1, "implement-thing", "CODER"),
        ],
    }

    result = _run_materialize(plan, team_root, tmp_path)
    assert result.returncode == 0, (
        f"Materialization failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )

    stderr = result.stderr
    assert "Dedupe: 0 duplicate task(s) skipped" in stderr, stderr
    assert "Duplicate tasks skipped (dedupe): 0" in stderr, stderr
    assert "dedupe: skipped duplicate" not in stderr, stderr

    tasks_root = team_root / "tasks"
    coder_ids = _task_ids_by_role(tasks_root, "CODER")
    tester_ids = _task_ids_by_role(tasks_root, "TESTER")
    cm_ids = _task_ids_by_role(tasks_root, "CM")

    assert len(coder_ids) == 1
    assert len(tester_ids) == 1
    assert len(cm_ids) == 2  # open-rc + release


def test_real_entry_point_testing_only_workflow_materializes_two_distinct_tester_tasks(
    tmp_path: pathlib.Path,
) -> None:
    """BUG-0035 end-to-end regression guard, verified through the REAL
    materialization entry point (not the helper in isolation).

    A testing-only decompose (no CM bookends) emits one plan TESTER work
    ticket (slug 'verify-the-implementation'); pm_materialize.py injects the
    synthetic 'verify-and-report' companion because test_required=true.
    Both must survive as distinct TESTER task folders with dedupe count 0 —
    the intra-materialization dedupe guard must not collapse them into one.
    """
    team_root = tmp_path / "team_root"
    team_root.mkdir()

    plan = {
        "workflow_type": "testing-only",
        "target_version": "none",
        "test_required": "true",
        "human_approval_required": "auto",
        "project_name": "dedupe-guard-testing-only-fixture",
        "tasks": [
            _base_task(1, "verify-the-implementation", "TESTER"),
        ],
    }

    result = _run_materialize(plan, team_root, tmp_path)
    assert result.returncode == 0, (
        f"Materialization failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )

    stderr = result.stderr
    assert "Dedupe: 0 duplicate task(s) skipped" in stderr, stderr
    assert "Duplicate tasks skipped (dedupe): 0" in stderr, stderr
    assert "dedupe: skipped duplicate" not in stderr, stderr

    tasks_root = team_root / "tasks"
    tester_ids = _task_ids_by_role(tasks_root, "TESTER")
    assert len(tester_ids) == 2, f"Expected exactly 2 TESTER tasks, got: {tester_ids}"
    assert any("verify-the-implementation" in tid for tid in tester_ids), tester_ids
    assert any("verify-and-report" in tid for tid in tester_ids), tester_ids
