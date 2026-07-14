"""
test_pm_materialize_source_ref.py
==================================
Tests for plan-level source_branch propagation and the fail-closed
source-ref guard in pm_materialize.py.

Covers three acceptance criteria from task
CODER-20260713-027-propagate-source-ref-fail-closed:

  AC-1  Per-ticket override retained (regression): a ticket with its own
        explicit source_branch keeps it; propagation does not overwrite.
  AC-2  Materialize-time fail-closed refusal: a plan with no resolvable
        source ref and a TESTER ticket without an override causes sys.exit(1)
        via _validate_source_ref_for_worktree_roles, naming the ticket.
  AC-3  Default-fill: a plan with a plan-level source_branch propagates it
        onto tickets that have source_branch absent/empty/"none".

Additional tests verify the helper functions independently.
"""

from __future__ import annotations

import pathlib
import sys
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Import with installed-package / direct-checkout fallback
# ---------------------------------------------------------------------------

try:
    import pm_agent.pm_materialize as pm
except ImportError:
    sys.path.insert(
        0, str(pathlib.Path(__file__).parent.parent.parent / "pm-agent")
    )
    import pm_materialize as pm  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Helper: build a minimal TESTER task dict
# ---------------------------------------------------------------------------


def _tester_task(source_branch: str = "none",
                 git_repo: str = "git@github.com:org/repo.git",
                 task_id: str = "TESTER-20260101-001-verify") -> dict:
    """Return a minimal TESTER task dict for tests."""
    return {
        "task_id": task_id,
        "slug": "verify",
        "role": "TESTER",
        "assigned_agent": "TESTER",
        "source_branch": source_branch,
        "git_repo": git_repo,
        "_tester": True,
        "_synthetic": True,
    }


def _coder_task(source_branch: str = "main",
                git_repo: str = "git@github.com:org/repo.git",
                task_id: str = "CODER-20260101-001-impl") -> dict:
    """Return a minimal CODER task dict for tests."""
    return {
        "task_id": task_id,
        "slug": "impl",
        "role": "CODER",
        "assigned_agent": "coder",
        "source_branch": source_branch,
        "git_repo": git_repo,
    }


# Workflow caps for a testing-only-shaped workflow (git_mode=ro).
_TESTING_ONLY_CAPS = {"git_mode": "ro", "finalize": "report", "agents": "pm,tester"}

# Workflow caps for a workflow with no git mode (git_mode=none).
_NO_GIT_CAPS = {"git_mode": "none", "finalize": "report", "agents": "pm,tester"}


# ---------------------------------------------------------------------------
# _read_source_branch_from_requirements
# ---------------------------------------------------------------------------


def test_read_source_branch_from_requirements_parses_value(tmp_path: pathlib.Path) -> None:
    """Reads the ## Source Branch value from a requirements doc."""
    req = tmp_path / "req.md"
    req.write_text(
        "# Requirements\n\n## Source Branch\n\nrc/v2.5.0\n\n## Goal\n\nDo things.\n",
        encoding="utf-8",
    )
    result = pm._read_source_branch_from_requirements(str(req))
    assert result == "rc/v2.5.0"


def test_read_source_branch_from_requirements_missing_field(tmp_path: pathlib.Path) -> None:
    """Returns empty string when ## Source Branch is absent from the doc."""
    req = tmp_path / "req.md"
    req.write_text("# Requirements\n\n## Goal\n\nDo things.\n", encoding="utf-8")
    result = pm._read_source_branch_from_requirements(str(req))
    assert result == ""


def test_read_source_branch_from_requirements_none_path() -> None:
    """Returns empty string immediately for path 'none'."""
    assert pm._read_source_branch_from_requirements("none") == ""


def test_read_source_branch_from_requirements_empty_path() -> None:
    """Returns empty string immediately for empty path."""
    assert pm._read_source_branch_from_requirements("") == ""


def test_read_source_branch_from_requirements_missing_file(tmp_path: pathlib.Path) -> None:
    """Returns empty string when the file does not exist."""
    result = pm._read_source_branch_from_requirements(str(tmp_path / "nonexistent.md"))
    assert result == ""


# ---------------------------------------------------------------------------
# _resolve_plan_source_branch
# ---------------------------------------------------------------------------


def test_resolve_plan_source_branch_from_plan_json() -> None:
    """Returns plan['source_branch'] when it is set."""
    plan = {"source_branch": "rc/v3.1.0"}
    result = pm._resolve_plan_source_branch(plan, "none")
    assert result == "rc/v3.1.0"


def test_resolve_plan_source_branch_falls_back_to_requirements_doc(tmp_path: pathlib.Path) -> None:
    """Falls back to ## Source Branch in requirements doc when plan has no value."""
    req = tmp_path / "req.md"
    req.write_text("# R\n\n## Source Branch\n\nrc/v4.2.0\n", encoding="utf-8")
    plan: dict = {}
    result = pm._resolve_plan_source_branch(plan, str(req))
    assert result == "rc/v4.2.0"


def test_resolve_plan_source_branch_plan_wins_over_requirements_doc(tmp_path: pathlib.Path) -> None:
    """plan['source_branch'] takes precedence over the requirements doc."""
    req = tmp_path / "req.md"
    req.write_text("# R\n\n## Source Branch\n\nrc/v4.2.0\n", encoding="utf-8")
    plan = {"source_branch": "rc/v5.0.0"}
    result = pm._resolve_plan_source_branch(plan, str(req))
    assert result == "rc/v5.0.0"


def test_resolve_plan_source_branch_returns_empty_when_none_available() -> None:
    """Returns empty string when neither plan nor requirements doc has a value."""
    plan: dict = {}
    result = pm._resolve_plan_source_branch(plan, "none")
    assert result == ""


def test_resolve_plan_source_branch_ignores_literal_none_in_plan() -> None:
    """Treats plan source_branch of 'none' as absent and falls back to requirements."""
    plan = {"source_branch": "none"}
    result = pm._resolve_plan_source_branch(plan, "none")
    assert result == ""


# ---------------------------------------------------------------------------
# _propagate_plan_source_branch
# ---------------------------------------------------------------------------


def test_propagate_fills_tasks_with_no_source_branch() -> None:
    """Default-fills source_branch onto tasks that have none, empty, or 'none'."""
    tasks = [
        {"task_id": "A", "source_branch": "none"},
        {"task_id": "B", "source_branch": ""},
        {"task_id": "C"},  # key absent
    ]
    count = pm._propagate_plan_source_branch(tasks, "rc/v9.0.0")
    assert count == 3
    for t in tasks:
        assert t["source_branch"] == "rc/v9.0.0"


def test_propagate_does_not_overwrite_explicit_source_branch() -> None:
    """AC-1: per-ticket explicit source_branch is preserved (default-fill semantics)."""
    explicit_value = "feature/my-explicit-branch"
    tasks = [
        {"task_id": "A", "source_branch": explicit_value},
    ]
    count = pm._propagate_plan_source_branch(tasks, "rc/v9.0.0")
    assert count == 0  # nothing updated
    assert tasks[0]["source_branch"] == explicit_value


def test_propagate_mixed_tasks() -> None:
    """Fills tasks without explicit source_branch; leaves those with explicit value alone."""
    tasks = [
        {"task_id": "A", "source_branch": "explicit-branch"},  # must NOT change
        {"task_id": "B", "source_branch": "none"},             # must be filled
        {"task_id": "C"},                                       # must be filled
    ]
    count = pm._propagate_plan_source_branch(tasks, "rc/v2.0.0")
    assert count == 2
    assert tasks[0]["source_branch"] == "explicit-branch"
    assert tasks[1]["source_branch"] == "rc/v2.0.0"
    assert tasks[2]["source_branch"] == "rc/v2.0.0"


def test_propagate_is_noop_when_plan_source_branch_is_empty() -> None:
    """No-op when plan_source_branch is empty string."""
    tasks = [{"task_id": "A", "source_branch": "none"}]
    count = pm._propagate_plan_source_branch(tasks, "")
    assert count == 0
    assert tasks[0]["source_branch"] == "none"


def test_propagate_returns_zero_for_empty_task_list() -> None:
    """No error on empty task list; returns 0."""
    assert pm._propagate_plan_source_branch([], "rc/v1.0.0") == 0


# ---------------------------------------------------------------------------
# _validate_source_ref_for_worktree_roles
# ---------------------------------------------------------------------------


def test_validate_passes_when_tester_has_source_branch() -> None:
    """No error when TESTER task carries a valid source_branch."""
    tasks = [_tester_task(source_branch="rc/v2.5.0")]
    # Must not raise SystemExit
    pm._validate_source_ref_for_worktree_roles(tasks, _TESTING_ONLY_CAPS, "testing-only")


def test_validate_exits_when_tester_has_no_source_branch() -> None:
    """AC-2: exits non-zero with a stderr message when TESTER task has source_branch='none'."""
    tasks = [_tester_task(source_branch="none")]
    with pytest.raises(SystemExit) as exc_info:
        pm._validate_source_ref_for_worktree_roles(tasks, _TESTING_ONLY_CAPS, "testing-only")
    assert exc_info.value.code != 0


def test_validate_stderr_names_offending_task(capsys) -> None:
    """AC-2: stderr message names the offending task ID and the missing field."""
    bad_task_id = "TESTER-20260101-002-verify-and-report"
    tasks = [_tester_task(source_branch="none", task_id=bad_task_id)]
    with pytest.raises(SystemExit):
        pm._validate_source_ref_for_worktree_roles(tasks, _TESTING_ONLY_CAPS, "testing-only")
    captured = capsys.readouterr()
    assert bad_task_id in captured.err
    assert "Source Branch" in captured.err or "source_branch" in captured.err.lower()


def test_validate_exits_when_tester_has_empty_source_branch() -> None:
    """Exits non-zero when TESTER source_branch is empty string."""
    tasks = [_tester_task(source_branch="")]
    with pytest.raises(SystemExit):
        pm._validate_source_ref_for_worktree_roles(tasks, _TESTING_ONLY_CAPS, "testing-only")


def test_validate_skips_release_workflow() -> None:
    """Guard is bypassed entirely for release workflows (determine_source_branch handles it)."""
    tasks = [_tester_task(source_branch="none")]
    # Must not raise, even though TESTER has no source_branch
    pm._validate_source_ref_for_worktree_roles(tasks, _TESTING_ONLY_CAPS, "release")


def test_validate_skips_when_git_mode_is_none() -> None:
    """Guard is bypassed when the workflow's git_mode is 'none' (no worktree needed)."""
    tasks = [_tester_task(source_branch="none")]
    pm._validate_source_ref_for_worktree_roles(tasks, _NO_GIT_CAPS, "testing-only")


def test_validate_skips_when_caps_empty() -> None:
    """Guard is bypassed when workflow caps are empty (pre-manifest plugin)."""
    tasks = [_tester_task(source_branch="none")]
    pm._validate_source_ref_for_worktree_roles(tasks, {}, "testing-only")


def test_validate_skips_non_tester_roles() -> None:
    """Guard does not fire for CODER tasks, even with source_branch='none'."""
    tasks = [_coder_task(source_branch="none")]
    pm._validate_source_ref_for_worktree_roles(tasks, _TESTING_ONLY_CAPS, "testing-only")


def test_validate_skips_tester_with_no_git_repo() -> None:
    """Guard does not fire for TESTER tasks whose git_repo is 'none' (no worktree needed)."""
    tasks = [_tester_task(source_branch="none", git_repo="none")]
    pm._validate_source_ref_for_worktree_roles(tasks, _TESTING_ONLY_CAPS, "testing-only")


def test_validate_multiple_tester_tasks_all_named_on_error(capsys) -> None:
    """When multiple TESTER tasks lack source_branch, all are named in the error."""
    id1 = "TESTER-20260101-001-verify"
    id2 = "TESTER-20260101-002-verify-and-report"
    tasks = [
        _tester_task(source_branch="none", task_id=id1),
        _tester_task(source_branch="none", task_id=id2),
    ]
    with pytest.raises(SystemExit):
        pm._validate_source_ref_for_worktree_roles(tasks, _TESTING_ONLY_CAPS, "testing-only")
    captured = capsys.readouterr()
    assert id1 in captured.err
    assert id2 in captured.err


# ---------------------------------------------------------------------------
# Integration: propagate then validate — the end-to-end path
# ---------------------------------------------------------------------------


def test_propagate_then_validate_succeeds_when_plan_has_source_branch() -> None:
    """AC-3 + AC-2 combined: propagation supplies the source ref; guard passes."""
    tasks = [
        _coder_task(source_branch="none"),
        _tester_task(source_branch="none"),
    ]
    plan_sb = "rc/v2.0.0"
    pm._propagate_plan_source_branch(tasks, plan_sb)
    # After propagation both tasks have source_branch set
    for t in tasks:
        assert t["source_branch"] == plan_sb
    # Guard must pass (no SystemExit)
    pm._validate_source_ref_for_worktree_roles(tasks, _TESTING_ONLY_CAPS, "testing-only")


def test_propagate_then_validate_fails_when_no_source_branch_anywhere() -> None:
    """AC-2: no plan source_branch + TESTER without override → guard exits non-zero."""
    tasks = [_tester_task(source_branch="none")]
    # propagate with empty plan source branch is a no-op
    pm._propagate_plan_source_branch(tasks, "")
    assert tasks[0]["source_branch"] == "none"
    with pytest.raises(SystemExit) as exc_info:
        pm._validate_source_ref_for_worktree_roles(tasks, _TESTING_ONLY_CAPS, "testing-only")
    assert exc_info.value.code != 0


def test_per_ticket_override_retained_after_propagation() -> None:
    """AC-1: per-ticket explicit source_branch survives propagation unchanged."""
    explicit = "rc/v0.10.2"
    tasks = [
        _tester_task(source_branch=explicit, task_id="TESTER-20260101-001-verify"),
        _tester_task(source_branch="none", task_id="TESTER-20260101-002-verify-and-report"),
    ]
    pm._propagate_plan_source_branch(tasks, "rc/v0.11.0")
    # Ticket 1's explicit value must be unchanged
    assert tasks[0]["source_branch"] == explicit
    # Ticket 2 (missing) should be filled with the plan value
    assert tasks[1]["source_branch"] == "rc/v0.11.0"


# ---------------------------------------------------------------------------
# Guard-ordering proof: post-assembly injected ticket with source_branch='none'
# ---------------------------------------------------------------------------


def test_guard_fires_on_post_assembly_injected_tester_with_no_source_branch(
    capsys,
) -> None:
    """Guard fires when a TESTER ticket is appended after assembly without a source ref.

    This is the guard-ordering proof for an earlier defect acceptance criterion #3:
    a synthetic injector that appends a TESTER ticket with source_branch='none'
    AFTER plan assembly (bypassing the normal propagation step) is refused by
    _validate_source_ref_for_worktree_roles with sys.exit(1) before any task
    folder is created.

    The guard must fire regardless of when the ticket was added — catching
    rogue injection that bypasses source-ref propagation.
    """
    # Normal assembled task list: one TESTER with a resolved source branch.
    assembled_tasks = [
        _tester_task(source_branch="rc/v1.22.5", task_id="TESTER-20260714-001-verify"),
    ]

    # Simulate post-assembly injection: rogue injector appends a ticket
    # AFTER the guard would normally have been satisfied.
    rogue_id = "TESTER-20260714-002-post-assembly-injection"
    assembled_tasks.append(
        _tester_task(source_branch="none", task_id=rogue_id)
    )

    # Guard must refuse with sys.exit(1) (non-zero).
    with pytest.raises(SystemExit) as exc_info:
        pm._validate_source_ref_for_worktree_roles(
            assembled_tasks, _TESTING_ONLY_CAPS, "testing-only"
        )
    assert exc_info.value.code != 0, (
        "Expected sys.exit with non-zero code for post-assembly injected "
        f"TESTER with source_branch='none'; got code {exc_info.value.code!r}."
    )

    # Diagnostic must name the offending task.
    captured = capsys.readouterr()
    assert rogue_id in captured.err, (
        f"Expected the offending task ID {rogue_id!r} in stderr diagnostic.\n"
        f"stderr: {captured.err}"
    )
    assert "Source Branch" in captured.err or "source_branch" in captured.err.lower(), (
        f"Expected 'Source Branch' or 'source_branch' mentioned in stderr diagnostic.\n"
        f"stderr: {captured.err}"
    )


def test_guard_passes_when_all_assembled_tasks_have_source_branch() -> None:
    """Guard passes when every TESTER in the assembled list has a valid source ref.

    Confirms that normal assembly (where propagation has filled all source refs)
    allows the guard to pass without raising SystemExit.
    """
    assembled_tasks = [
        _tester_task(source_branch="rc/v1.22.5", task_id="TESTER-20260714-001-verify"),
        _tester_task(source_branch="rc/v1.22.5", task_id="TESTER-20260714-002-verify-and-report"),
    ]
    # Must not raise SystemExit — all tasks have a valid source ref.
    pm._validate_source_ref_for_worktree_roles(
        assembled_tasks, _TESTING_ONLY_CAPS, "testing-only"
    )


def test_guard_names_all_injected_tickets_lacking_source_branch(capsys) -> None:
    """When multiple post-assembly injected tickets lack source_branch, all are named.

    The diagnostic must be complete: every offending task ID is listed so the
    operator knows the full scope of the injection failure.
    """
    rogue_id_1 = "TESTER-20260714-003-injected-first"
    rogue_id_2 = "TESTER-20260714-004-injected-second"
    assembled_tasks = [
        _tester_task(source_branch="rc/v1.22.5", task_id="TESTER-20260714-001-verify"),
        _tester_task(source_branch="none", task_id=rogue_id_1),
        _tester_task(source_branch="none", task_id=rogue_id_2),
    ]

    with pytest.raises(SystemExit):
        pm._validate_source_ref_for_worktree_roles(
            assembled_tasks, _TESTING_ONLY_CAPS, "testing-only"
        )

    captured = capsys.readouterr()
    assert rogue_id_1 in captured.err, (
        f"Expected {rogue_id_1!r} named in stderr diagnostic.\nstderr: {captured.err}"
    )
    assert rogue_id_2 in captured.err, (
        f"Expected {rogue_id_2!r} named in stderr diagnostic.\nstderr: {captured.err}"
    )
