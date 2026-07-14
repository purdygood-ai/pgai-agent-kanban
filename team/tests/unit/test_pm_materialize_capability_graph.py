"""
test_pm_materialize_capability_graph.py
=========================================
Unit tests for the capability-graph fix introduced in an earlier defect.

Tests cover:
  (a) load_workflow_capabilities() — reads the correct fields from workflow.cfg
      for both the testing-only and release workflow plugins
  (b) _workflow_requires_cm_bookends() — gates CM bookend emission on
      git_mode=rw + finalize in {tag, publish}
  (c) _parse_manifest_roster() — normalises agent names to uppercase list
  (d) _check_plan_roster_against_manifest() — calls sys.exit(1) with an
      actionable message when a plan task requests an out-of-roster role;
      names both the offending agent and the full roster
  (e) inject_simple_tester_task() — injects a TESTER task with finalize-report
      responsibility; no CM tasks present; task has correct role and flag fields
  (f) Backward-compat: empty capabilities dict causes CM bookends to be emitted
      (conservative fallback for unknown workflow types)

All filesystem writes target the autouse _block_live_kanban_writes fixture
(via PGAI_AGENT_KANBAN_ROOT_PATH pointing to a safe_root that contains the
testing-only and release workflow.cfg files).  No test writes to bare /tmp.

Naming: function names describe the behavior under test, not the bug ID
(SOP anti-pattern 6).
"""

from __future__ import annotations

import pathlib
import sys
import textwrap

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
# Helpers
# ---------------------------------------------------------------------------

def _minimal_task(seq: int = 1, slug: str = "my-task",
                  role: str = "PM") -> dict:
    """Return a minimal task dict with a pre-assigned task_id."""
    return {
        "sequence": seq,
        "slug": slug,
        "title": f"Task {seq}",
        "role": role,
        "assigned_agent": role,
        "working_directory": "none",
        "git_repo": "none",
        "source_branch": "none",
        "task_id": f"{role}-20260101-{seq:03d}-{slug}",
        "goal": "Do the work.",
        "inputs": [],
        "context_paths": [],
        "required_output": "Done.",
        "constraints": [],
        "acceptance_criteria": ["It works."],
        "depends_on": [],
        "prerequisite_ids": [],
        "notes": "none",
    }


# ===========================================================================
# (a) load_workflow_capabilities — reads workflow.cfg correctly
# ===========================================================================


def test_load_workflow_capabilities_testing_only_returns_dict(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """load_workflow_capabilities returns a non-empty dict for the testing-only plugin.

    The autouse _block_live_kanban_writes fixture sets PGAI_AGENT_KANBAN_ROOT_PATH
    to a safe_root that already contains team/workflows/testing-only/workflow.cfg
    (copied via _copy_workflow_plugins).  load_workflow_capabilities resolves
    the manifest via the env var without needing a kanban_root argument.
    """
    caps = pm.load_workflow_capabilities("testing-only")
    assert isinstance(caps, dict)
    assert caps  # non-empty


def test_load_workflow_capabilities_testing_only_git_mode_is_ro(
    tmp_path: pathlib.Path,
) -> None:
    """load_workflow_capabilities returns git_mode='ro' for testing-only plugin."""
    caps = pm.load_workflow_capabilities("testing-only")
    assert caps.get("git_mode") == "ro"


def test_load_workflow_capabilities_testing_only_finalize_is_report(
    tmp_path: pathlib.Path,
) -> None:
    """load_workflow_capabilities returns finalize='report' for testing-only plugin."""
    caps = pm.load_workflow_capabilities("testing-only")
    assert caps.get("finalize") == "report"


def test_load_workflow_capabilities_testing_only_agents_field(
    tmp_path: pathlib.Path,
) -> None:
    """load_workflow_capabilities returns the agents field for testing-only plugin."""
    caps = pm.load_workflow_capabilities("testing-only")
    agents = caps.get("agents", "")
    assert "pm" in agents.lower()
    assert "tester" in agents.lower()
    # CM is NOT in the testing-only roster
    assert "cm" not in agents.lower()


def test_load_workflow_capabilities_testing_only_version_semantics_is_label(
    tmp_path: pathlib.Path,
) -> None:
    """load_workflow_capabilities returns version_semantics='label' for testing-only."""
    caps = pm.load_workflow_capabilities("testing-only")
    assert caps.get("version_semantics") == "label"


def test_load_workflow_capabilities_release_git_mode_is_rw(
    tmp_path: pathlib.Path,
) -> None:
    """load_workflow_capabilities returns git_mode='rw' for release plugin."""
    caps = pm.load_workflow_capabilities("release")
    assert caps.get("git_mode") == "rw"


def test_load_workflow_capabilities_release_finalize_is_tag(
    tmp_path: pathlib.Path,
) -> None:
    """load_workflow_capabilities returns finalize='tag' for release plugin."""
    caps = pm.load_workflow_capabilities("release")
    assert caps.get("finalize") == "tag"


def test_load_workflow_capabilities_release_agents_includes_cm(
    tmp_path: pathlib.Path,
) -> None:
    """load_workflow_capabilities release roster includes cm."""
    caps = pm.load_workflow_capabilities("release")
    agents = caps.get("agents", "")
    assert "cm" in agents.lower()


def test_load_workflow_capabilities_unknown_type_returns_empty_dict(
    tmp_path: pathlib.Path,
) -> None:
    """load_workflow_capabilities returns {} for an unknown workflow type."""
    caps = pm.load_workflow_capabilities("nonexistent-workflow-type-xyzzy")
    assert caps == {}


def test_load_workflow_capabilities_uses_explicit_kanban_root(
    tmp_path: pathlib.Path,
) -> None:
    """load_workflow_capabilities accepts an explicit kanban_root override."""
    # Build a minimal synthetic kanban root with a testing-only plugin.
    wf_dir = tmp_path / "team" / "workflows" / "testing-only"
    wf_dir.mkdir(parents=True)
    (wf_dir / "workflow.cfg").write_text(
        textwrap.dedent("""\
            [workflow]
            name = testing-only
            status = active

            [capabilities]
            version_semantics = label
            git_mode = ro
            finalize = report
            agents = pm,tester
        """),
        encoding="utf-8",
    )

    caps = pm.load_workflow_capabilities("testing-only", kanban_root=str(tmp_path))
    assert caps.get("git_mode") == "ro"
    assert caps.get("finalize") == "report"


def test_load_workflow_capabilities_missing_section_returns_empty_dict(
    tmp_path: pathlib.Path,
) -> None:
    """load_workflow_capabilities returns {} when workflow.cfg has no [capabilities] section."""
    wf_dir = tmp_path / "team" / "workflows" / "no-caps"
    wf_dir.mkdir(parents=True)
    (wf_dir / "workflow.cfg").write_text(
        textwrap.dedent("""\
            [workflow]
            name = no-caps
            status = active
        """),
        encoding="utf-8",
    )

    caps = pm.load_workflow_capabilities("no-caps", kanban_root=str(tmp_path))
    assert caps == {}


# ===========================================================================
# (b) _workflow_requires_cm_bookends — predicate logic
# ===========================================================================


def test_requires_cm_bookends_true_for_release_caps(
    tmp_path: pathlib.Path,
) -> None:
    """_workflow_requires_cm_bookends returns True for git_mode=rw + finalize=tag."""
    caps = {"git_mode": "rw", "finalize": "tag", "agents": "pm,coder,tester,cm"}
    assert pm._workflow_requires_cm_bookends(caps) is True


def test_requires_cm_bookends_true_for_publish_finalize(
    tmp_path: pathlib.Path,
) -> None:
    """_workflow_requires_cm_bookends returns True for git_mode=rw + finalize=publish."""
    caps = {"git_mode": "rw", "finalize": "publish", "agents": "pm,writer,tester,cm"}
    assert pm._workflow_requires_cm_bookends(caps) is True


def test_requires_cm_bookends_false_for_testing_only_caps(
    tmp_path: pathlib.Path,
) -> None:
    """_workflow_requires_cm_bookends returns False for git_mode=ro + finalize=report."""
    caps = {"git_mode": "ro", "finalize": "report", "agents": "pm,tester"}
    assert pm._workflow_requires_cm_bookends(caps) is False


def test_requires_cm_bookends_false_for_ro_with_tag_finalize(
    tmp_path: pathlib.Path,
) -> None:
    """_workflow_requires_cm_bookends returns False when git_mode=ro even with finalize=tag."""
    caps = {"git_mode": "ro", "finalize": "tag", "agents": "pm,cm"}
    assert pm._workflow_requires_cm_bookends(caps) is False


def test_requires_cm_bookends_false_for_rw_with_report_finalize(
    tmp_path: pathlib.Path,
) -> None:
    """_workflow_requires_cm_bookends returns False when finalize=report even with git_mode=rw."""
    caps = {"git_mode": "rw", "finalize": "report", "agents": "pm,tester"}
    assert pm._workflow_requires_cm_bookends(caps) is False


def test_requires_cm_bookends_true_for_empty_caps_fallback(
    tmp_path: pathlib.Path,
) -> None:
    """_workflow_requires_cm_bookends returns True (conservative) when caps is empty dict."""
    # Empty caps means manifest not found — default to old behavior (emit bookends).
    assert pm._workflow_requires_cm_bookends({}) is True


def test_requires_cm_bookends_false_for_none_git_mode(
    tmp_path: pathlib.Path,
) -> None:
    """_workflow_requires_cm_bookends returns False when git_mode=none."""
    caps = {"git_mode": "none", "finalize": "publish", "agents": "pm,writer,cm"}
    assert pm._workflow_requires_cm_bookends(caps) is False


def test_requires_cm_bookends_case_insensitive(
    tmp_path: pathlib.Path,
) -> None:
    """_workflow_requires_cm_bookends normalises case before comparison."""
    caps = {"git_mode": "RW", "finalize": "TAG"}
    assert pm._workflow_requires_cm_bookends(caps) is True


# ===========================================================================
# (c) _parse_manifest_roster — roster normalisation
# ===========================================================================


def test_parse_manifest_roster_testing_only_agents(
    tmp_path: pathlib.Path,
) -> None:
    """_parse_manifest_roster returns ['PM', 'TESTER'] for testing-only capabilities."""
    caps = {"agents": "pm,tester"}
    result = pm._parse_manifest_roster(caps)
    assert result == ["PM", "TESTER"]


def test_parse_manifest_roster_release_agents(
    tmp_path: pathlib.Path,
) -> None:
    """_parse_manifest_roster returns uppercase names for release roster."""
    caps = {"agents": "pm,coder,writer,tester,cm"}
    result = pm._parse_manifest_roster(caps)
    assert result == ["PM", "CODER", "WRITER", "TESTER", "CM"]


def test_parse_manifest_roster_empty_agents_field_returns_empty_list(
    tmp_path: pathlib.Path,
) -> None:
    """_parse_manifest_roster returns [] when agents field is empty."""
    caps = {"agents": ""}
    result = pm._parse_manifest_roster(caps)
    assert result == []


def test_parse_manifest_roster_missing_agents_key_returns_empty_list(
    tmp_path: pathlib.Path,
) -> None:
    """_parse_manifest_roster returns [] when agents key is not in caps."""
    caps = {"git_mode": "ro", "finalize": "report"}
    result = pm._parse_manifest_roster(caps)
    assert result == []


def test_parse_manifest_roster_strips_whitespace(
    tmp_path: pathlib.Path,
) -> None:
    """_parse_manifest_roster strips whitespace from each agent name."""
    caps = {"agents": " pm , tester "}
    result = pm._parse_manifest_roster(caps)
    assert "PM" in result
    assert "TESTER" in result


# ===========================================================================
# (d) _check_plan_roster_against_manifest — roster guard fires for out-of-roster agent
# ===========================================================================


def test_roster_guard_accepts_in_roster_task(
    tmp_path: pathlib.Path,
) -> None:
    """_check_plan_roster_against_manifest accepts all tasks when all roles are in roster."""
    tasks = [
        _minimal_task(seq=1, slug="plan", role="PM"),
        _minimal_task(seq=2, slug="test", role="TESTER"),
    ]
    roster = ["PM", "TESTER"]
    # Should not raise or exit.
    pm._check_plan_roster_against_manifest(tasks, roster, "testing-only")


def test_roster_guard_rejects_out_of_roster_agent_exits_nonzero(
    tmp_path: pathlib.Path,
) -> None:
    """_check_plan_roster_against_manifest calls sys.exit(1) for an out-of-roster role."""
    tasks = [
        _minimal_task(seq=1, slug="plan", role="PM"),
        _minimal_task(seq=2, slug="code", role="CODER"),  # CODER not in testing-only roster
    ]
    roster = ["PM", "TESTER"]
    with pytest.raises(SystemExit) as exc_info:
        pm._check_plan_roster_against_manifest(tasks, roster, "testing-only")
    assert exc_info.value.code == 1


def test_roster_guard_error_names_offending_agent(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """Roster guard error message names the offending agent role."""
    tasks = [_minimal_task(seq=1, slug="write", role="WRITER")]
    roster = ["PM", "TESTER"]
    with pytest.raises(SystemExit):
        pm._check_plan_roster_against_manifest(tasks, roster, "testing-only")
    captured = capsys.readouterr()
    assert "WRITER" in captured.err


def test_roster_guard_error_names_plugin_roster(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """Roster guard error message names the full plugin roster."""
    tasks = [_minimal_task(seq=1, slug="cm-task", role="CM")]
    roster = ["PM", "TESTER"]
    with pytest.raises(SystemExit):
        pm._check_plan_roster_against_manifest(tasks, roster, "testing-only")
    captured = capsys.readouterr()
    # Roster must appear in the error message (comma-separated display).
    assert "PM" in captured.err
    assert "TESTER" in captured.err


def test_roster_guard_error_names_workflow_type(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """Roster guard error message names the workflow_type for context."""
    tasks = [_minimal_task(seq=1, slug="cm-task", role="CM")]
    roster = ["PM", "TESTER"]
    with pytest.raises(SystemExit):
        pm._check_plan_roster_against_manifest(tasks, roster, "testing-only")
    captured = capsys.readouterr()
    assert "testing-only" in captured.err


def test_roster_guard_skips_synthetic_tasks(
    tmp_path: pathlib.Path,
) -> None:
    """_check_plan_roster_against_manifest skips tasks with _synthetic=True."""
    tasks = [
        _minimal_task(seq=1, slug="plan", role="PM"),
        {**_minimal_task(seq=2, slug="cm-open", role="CM"), "_synthetic": True},
    ]
    roster = ["PM", "TESTER"]
    # Should not exit — synthetic CM task is skipped.
    pm._check_plan_roster_against_manifest(tasks, roster, "testing-only")


def test_roster_guard_skips_check_when_roster_is_empty(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """_check_plan_roster_against_manifest skips the check when manifest_roster is empty.

    An empty roster means the manifest was not found or the agents field is absent.
    The guard should warn but not exit so unknown plugin types pass.
    """
    tasks = [_minimal_task(seq=1, slug="any-role", role="WRITER")]
    # Empty roster — guard must NOT call sys.exit.
    pm._check_plan_roster_against_manifest(tasks, [], "unknown-plugin")
    captured = capsys.readouterr()
    # Should log a warning about the empty roster.
    assert "unknown-plugin" in captured.err or "skipping" in captured.err.lower()


def test_roster_guard_skips_task_with_empty_role(
    tmp_path: pathlib.Path,
) -> None:
    """_check_plan_roster_against_manifest ignores tasks with no role field."""
    tasks = [
        {"slug": "no-role-task", "task_id": "X-001"},  # no role
        _minimal_task(seq=2, slug="pm-task", role="PM"),
    ]
    roster = ["PM", "TESTER"]
    # No exit: the empty-role task is silently skipped.
    pm._check_plan_roster_against_manifest(tasks, roster, "testing-only")


# ===========================================================================
# (e) inject_simple_tester_task — TESTER task carries finalize-report responsibility
# ===========================================================================


def test_inject_simple_tester_task_appends_tester(
    tmp_path: pathlib.Path,
) -> None:
    """inject_simple_tester_task appends exactly one TESTER task."""
    tasks = [_minimal_task(seq=1, slug="run-tests", role="PM")]
    initial_len = len(tasks)
    pm.inject_simple_tester_task(
        tasks, date_str="20260101", base_seq=100,
        owner="CLAUDE", finalize_mode="report",
    )
    assert len(tasks) == initial_len + 1
    tester = tasks[-1]
    assert tester["role"] == "TESTER"


def test_inject_simple_tester_task_has_no_cm_tasks(
    tmp_path: pathlib.Path,
) -> None:
    """inject_simple_tester_task injects zero CM tasks."""
    tasks = [_minimal_task(seq=1, slug="run-tests", role="PM")]
    pm.inject_simple_tester_task(
        tasks, date_str="20260101", base_seq=100,
        owner="CLAUDE", finalize_mode="report",
    )
    cm_tasks = [t for t in tasks if t.get("role") == "CM"]
    assert cm_tasks == []


def test_inject_simple_tester_task_goal_mentions_finalize_report(
    tmp_path: pathlib.Path,
) -> None:
    """inject_simple_tester_task TESTER task goal mentions finalize=report language."""
    tasks = [_minimal_task(seq=1, slug="run-tests", role="PM")]
    pm.inject_simple_tester_task(
        tasks, date_str="20260101", base_seq=100,
        owner="CLAUDE", finalize_mode="report",
    )
    tester = tasks[-1]
    goal = tester.get("goal", "").lower()
    assert "report" in goal or "finalize" in goal, (
        f"TESTER task goal must mention report/finalize responsibility; got: {goal!r}"
    )


def test_inject_simple_tester_task_notes_mention_no_cm_release(
    tmp_path: pathlib.Path,
) -> None:
    """inject_simple_tester_task TESTER notes make explicit there is no CM release step."""
    tasks = [_minimal_task(seq=1, slug="run-tests", role="PM")]
    pm.inject_simple_tester_task(
        tasks, date_str="20260101", base_seq=100,
        owner="CLAUDE", finalize_mode="report",
    )
    tester = tasks[-1]
    notes = tester.get("notes", "").lower()
    # The notes should indicate that no CM release follows.
    assert "no cm" in notes or "final step" in notes or "finalize" in notes, (
        f"TESTER task notes must communicate finalize-report responsibility; got: {notes!r}"
    )


def test_inject_simple_tester_task_has_finalize_report_flag(
    tmp_path: pathlib.Path,
) -> None:
    """inject_simple_tester_task sets _finalize_report=True on the TESTER task."""
    tasks = [_minimal_task(seq=1, slug="run-tests", role="PM")]
    pm.inject_simple_tester_task(
        tasks, date_str="20260101", base_seq=100,
        owner="CLAUDE", finalize_mode="report",
    )
    tester = tasks[-1]
    assert tester.get("_finalize_report") is True


def test_inject_simple_tester_task_is_marked_synthetic(
    tmp_path: pathlib.Path,
) -> None:
    """inject_simple_tester_task marks the injected task as _synthetic=True."""
    tasks = [_minimal_task(seq=1, slug="run-tests", role="PM")]
    pm.inject_simple_tester_task(
        tasks, date_str="20260101", base_seq=100,
        owner="CLAUDE", finalize_mode="report",
    )
    tester = tasks[-1]
    assert tester.get("_synthetic") is True


def test_inject_simple_tester_task_prerequisite_ids_contain_work_tasks(
    tmp_path: pathlib.Path,
) -> None:
    """inject_simple_tester_task TESTER depends on all work task IDs."""
    tasks = [
        _minimal_task(seq=1, slug="run-suite-a", role="PM"),
        _minimal_task(seq=2, slug="run-suite-b", role="PM"),
    ]
    work_ids = [t["task_id"] for t in tasks]
    pm.inject_simple_tester_task(
        tasks, date_str="20260101", base_seq=100,
        owner="CLAUDE", finalize_mode="report",
    )
    tester = tasks[-1]
    for wid in work_ids:
        assert wid in tester.get("prerequisite_ids", []), (
            f"Expected work task {wid!r} in TESTER prerequisite_ids"
        )


def test_inject_simple_tester_task_task_id_has_correct_format(
    tmp_path: pathlib.Path,
) -> None:
    """inject_simple_tester_task TESTER task_id follows the TESTER-YYYYMMDD-NNN-slug format."""
    tasks = [_minimal_task(seq=1, slug="run-tests", role="PM")]
    pm.inject_simple_tester_task(
        tasks, date_str="20260101", base_seq=100,
        owner="CLAUDE", finalize_mode="report",
    )
    tester = tasks[-1]
    tid = tester.get("task_id", "")
    assert tid.startswith("TESTER-20260101-100-"), (
        f"Expected task_id to start with 'TESTER-20260101-100-'; got {tid!r}"
    )


def test_inject_simple_tester_task_finalize_mode_appears_in_constraints(
    tmp_path: pathlib.Path,
) -> None:
    """inject_simple_tester_task includes finalize_mode in task constraints."""
    tasks = [_minimal_task(seq=1, slug="run-tests", role="PM")]
    pm.inject_simple_tester_task(
        tasks, date_str="20260101", base_seq=100,
        owner="CLAUDE", finalize_mode="report",
    )
    tester = tasks[-1]
    constraints = tester.get("constraints", [])
    constraints_str = " ".join(constraints).lower()
    assert "report" in constraints_str, (
        f"Expected finalize_mode 'report' in constraints; got {constraints!r}"
    )


def test_inject_simple_tester_task_returns_list_with_injected_task(
    tmp_path: pathlib.Path,
) -> None:
    """inject_simple_tester_task returns a list containing only the injected task."""
    tasks = [_minimal_task(seq=1, slug="run-tests", role="PM")]
    result = pm.inject_simple_tester_task(
        tasks, date_str="20260101", base_seq=100,
        owner="CLAUDE", finalize_mode="report",
    )
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["role"] == "TESTER"


# ===========================================================================
# (f) Backward-compat: empty caps dict causes CM bookends to be emitted
# ===========================================================================


def test_backward_compat_empty_caps_returns_true_for_cm_bookends() -> None:
    """_workflow_requires_cm_bookends({}) returns True — conservative fallback.

    When workflow.cfg is absent (unknown plugin type), the materializer must
    not silently suppress CM bookends.  The safe default is to emit them,
    matching the behavior before an earlier defect was introduced.
    """
    # Empty dict = manifest not found.
    assert pm._workflow_requires_cm_bookends({}) is True


def test_load_workflow_capabilities_absent_manifest_returns_empty_dict(
    tmp_path: pathlib.Path,
) -> None:
    """load_workflow_capabilities({}) returns {} for a type that has no manifest.

    The caller is responsible for detecting the empty dict and applying the
    conservative fallback via _workflow_requires_cm_bookends.
    """
    caps = pm.load_workflow_capabilities(
        "nonexistent-workflow-xyz", kanban_root=str(tmp_path)
    )
    assert caps == {}
    # Conservative fallback: empty dict means emit bookends.
    assert pm._workflow_requires_cm_bookends(caps) is True


# ===========================================================================
# Integration: testing-only workflow capabilities drive no-CM-bookends decision
# ===========================================================================


def test_testing_only_caps_drive_no_cm_bookends_decision(
    tmp_path: pathlib.Path,
) -> None:
    """End-to-end: testing-only capabilities resolve to emit_cm_bookends=False.

    This is the primary acceptance criterion for an earlier defect: reading the
    testing-only workflow.cfg must cause the materializer to skip CM bookends.
    """
    caps = pm.load_workflow_capabilities("testing-only")
    assert caps, "testing-only capabilities must be readable from safe_root"
    assert pm._workflow_requires_cm_bookends(caps) is False, (
        f"testing-only workflow (git_mode={caps.get('git_mode')!r}, "
        f"finalize={caps.get('finalize')!r}) must NOT require CM bookends"
    )


def test_release_caps_drive_cm_bookends_decision(
    tmp_path: pathlib.Path,
) -> None:
    """End-to-end: release capabilities resolve to emit_cm_bookends=True.

    The release workflow (git_mode=rw, finalize=tag) must still produce
    CM bookends after the earlier fix — regression guard.
    """
    caps = pm.load_workflow_capabilities("release")
    assert caps, "release capabilities must be readable from safe_root"
    assert pm._workflow_requires_cm_bookends(caps) is True, (
        f"release workflow (git_mode={caps.get('git_mode')!r}, "
        f"finalize={caps.get('finalize')!r}) must still require CM bookends"
    )


def test_testing_only_roster_excludes_cm(
    tmp_path: pathlib.Path,
) -> None:
    """End-to-end: testing-only plugin roster does not include CM.

    When the roster guard runs against the testing-only roster, a task with
    role=CM is rejected.  This confirms the roster guard and the manifest
    roster parsing work together correctly for the testing-only plugin.
    """
    caps = pm.load_workflow_capabilities("testing-only")
    roster = pm._parse_manifest_roster(caps)
    assert "CM" not in roster, (
        f"testing-only roster must not include CM; got {roster!r}"
    )
    # Confirm the roster guard rejects a CM task.
    tasks = [_minimal_task(seq=1, slug="cm-fake", role="CM")]
    with pytest.raises(SystemExit) as exc_info:
        pm._check_plan_roster_against_manifest(tasks, roster, "testing-only")
    assert exc_info.value.code == 1


def test_release_roster_includes_cm(
    tmp_path: pathlib.Path,
) -> None:
    """End-to-end: release plugin roster includes CM.

    The roster guard must accept CM tasks in a release workflow decomposition.
    """
    caps = pm.load_workflow_capabilities("release")
    roster = pm._parse_manifest_roster(caps)
    assert "CM" in roster, (
        f"release roster must include CM; got {roster!r}"
    )
    # CM task in release workflow must pass the guard.
    tasks = [_minimal_task(seq=1, slug="open-rc", role="CM")]
    # Should not exit.
    pm._check_plan_roster_against_manifest(tasks, roster, "release")
