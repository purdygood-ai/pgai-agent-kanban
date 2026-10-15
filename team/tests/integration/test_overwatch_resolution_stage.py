"""
test_overwatch_resolution_stage.py
====================================
Integration tests for the OVERWATCH resolution stage's execution
behavior — pgai_agent_kanban.overwatch.resolution_stage.

Coverage:
  - Golden path: a matched CANCEL-AND-REBUNDLE row executes the full drill
    through the REAL team/scripts/cm/cancel-rc.sh subprocess (not a mock)
    plus the real wontdo_item/close_item/reset_item ops functions, and
    writes exactly one ledger entry.
  - Only the ship-policy HALT is cleared; an operator-authored HALT present
    alongside blocks resolution and leaves BOTH files untouched.
  - A declined/unmatched block (no ship-policy HALT, Active RC none, an
    unmet fact) leaves all state untouched and narrates the reason to the
    OVERWATCH action log.
  - No HALT present at all is a silent no-op (the common case — most
    OVERWATCH firings are not evaluating a ship-policy block).

Design:
  - All tests use pytest tmp_path for scratch — no bare /tmp paths.
  - The dev tree in every test is a local temp git repo (mirrors the
    pattern in test_retry_counter_bundle_family.py).
  - cm/cancel-rc.sh is invoked as a REAL subprocess against the temp kanban
    root; its KANBAN_ROOT-rooted python helpers are copied into the temp
    root's pgai_agent_kanban/cm/ (mirrors the live-install layout those
    scripts assume — same technique test_retry_counter_bundle_family.py
    already established for cm-open-rc.sh).
  - resolution_stage.run_resolution_stage() itself is called directly via
    Python import (this test process already has team/ on sys.path via
    conftest.py), not via the CLI/subprocess path — the CLI plumbing is
    covered separately by test_overwatch_resolution_stage_wiring.py.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess

import pytest

from pgai_agent_kanban.cm.bundle_family import compute_family_key
from pgai_agent_kanban.overwatch.ledger import LedgerEntry, append_entry, ledger_path_for_project, parse_entries
from pgai_agent_kanban.overwatch.resolution_stage import (
    DECLINED,
    EXECUTED,
    NO_OP,
    classify_halt_file,
    run_resolution_stage,
)
from tests.fixtures.binary_guard import require_binary


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Every test here builds, inspects, or drives a real git repository; a
    module-wide autouse guard keeps that requirement in one place instead
    of repeating a per-test check at each git-dependent call site.
    """
    require_binary("git", label="overwatch-resolution-stage")

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_CANCEL_RC_SCRIPT = _TEAM_DIR / "scripts" / "cm" / "cancel-rc.sh"
_KANBAN_PY_CM_DIR = _TEAM_DIR / "pgai_agent_kanban" / "cm"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _init_git_repo(repo_path: pathlib.Path, main_branch: str, rc_branch: str) -> None:
    """Initialise a local git repo with one commit on *main_branch*, then
    branch *rc_branch* off it with no further commits (branch content ==
    main's content — deliberately clean lineage, no direct/no operator
    commits on the RC branch's first-parent chain)."""
    repo_path.mkdir(parents=True, exist_ok=True)
    run = lambda *args: subprocess.run(  # noqa: E731
        ["git", "-C", str(repo_path), *args], capture_output=True, text=True, check=True
    )
    subprocess.run(
        ["git", "init", f"--initial-branch={main_branch}", str(repo_path)],
        capture_output=True, check=True,
    )
    run("config", "--local", "user.email", "test@pgai.local")
    run("config", "--local", "user.name", "PGAI Test")
    (repo_path / "README.md").write_text("# Test repo\n", encoding="utf-8")
    run("add", "README.md")
    run("commit", "-m", "Initial commit")
    run("checkout", "-b", rc_branch)
    run("checkout", main_branch)


def _build_kanban_root(
    tmp_path: pathlib.Path,
    project_name: str,
    repo_path: pathlib.Path,
) -> pathlib.Path:
    """Build a minimal kanban root laid out like the live install, including
    the copied pgai_agent_kanban/cm/*.py helpers cancel-rc.sh shells out to."""
    root = tmp_path / "kanban_root"
    proj = root / "projects" / project_name
    root.mkdir(parents=True, exist_ok=True)

    (root / "kanban.cfg").write_text(
        "[paths]\n\n[chain]\npm_mode = automatic\n\n[release]\nmax_rc_retries = 3\n",
        encoding="utf-8",
    )

    proj.mkdir(parents=True, exist_ok=True)
    (proj / "project.cfg").write_text(
        "[project]\n"
        f"project_name = {project_name}\n"
        f"dev_tree_path = {repo_path}\n"
        "branch_prefix = ai_\n"
        "push_to_remote = false\n"
        "\n[release]\nmax_rc_retries = 3\n",
        encoding="utf-8",
    )

    (proj / "requirements").mkdir(parents=True, exist_ok=True)
    (proj / "bugs").mkdir(parents=True, exist_ok=True)
    (proj / "tasks" / "queues").mkdir(parents=True, exist_ok=True)
    (proj / "release-state").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)

    # Live-install-style layout: cancel-rc.sh shells out to
    # $KANBAN_ROOT/pgai_agent_kanban/cm/*.py (never to team/pgai_agent_kanban).
    cm_py_dir = root / "pgai_agent_kanban" / "cm"
    cm_py_dir.mkdir(parents=True, exist_ok=True)
    for py_file in _KANBAN_PY_CM_DIR.glob("*.py"):
        (cm_py_dir / py_file.name).write_text(py_file.read_text(encoding="utf-8"), encoding="utf-8")

    # Live-install-style layout: resolution_stage.py resolves cm-cancel-rc.sh
    # at <kanban_root>/scripts/cm/cancel-rc.sh (mirrors install.sh's real
    # team/scripts/ -> $KANBAN_ROOT/scripts/ mirroring). Copy the real
    # scripts/ tree (script + its sourced libs) rather than hand-picking
    # individual files, so cancel-rc.sh's own `source ../lib/*.sh` calls
    # resolve exactly as they do in a real install.
    shutil.copytree(_TEAM_DIR / "scripts", root / "scripts")

    return root


def _write_release_state_md(proj: pathlib.Path, active_rc: str, last_released: str = "v0.0.0") -> None:
    (proj / "release-state.md").write_text(
        "# Release State\n\n"
        f"## Active RC\n{active_rc}\n\n"
        "## RC Opened At\n2026-07-27T00:00:00Z\n\n"
        "## RC Opened By Task\nCM-20260727-001-open\n\n"
        f"## Last Released\n{last_released}\n",
        encoding="utf-8",
    )


def _write_ship_policy_halt(path: pathlib.Path) -> None:
    path.write_text(
        "# HALT created by CM at 2026-07-27T00:00:00Z\n"
        "# Reason: TESTER systemic_risk=high in ai_rc/v2.0.1 (TESTER-20260727-001)\n"
        "# Resolution: Operator review required. See CM task status.md for full reason.\n",
        encoding="utf-8",
    )


def _write_operator_halt(path: pathlib.Path) -> None:
    path.write_text("# operator full-stop pending governance review\n", encoding="utf-8")


def _write_task(
    tasks_dir: pathlib.Path,
    task_id: str,
    version: str,
    state: str,
    role: str = "CODER",
) -> None:
    task_dir = tasks_dir / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "README.md").write_text(
        f"# Task: {task_id}\n\n## Task ID\n{task_id}\n\n## Role\n{role}\n\n"
        f"## Source Branch\nai_rc/{version}\n\n## Release Version\n{version}\n",
        encoding="utf-8",
    )
    (task_dir / "status.md").write_text(
        f"# Status\n\n## Task\n{task_id}\n\n## State\n{state}\n\n## Blockers\nnone\n",
        encoding="utf-8",
    )


def _write_tester_report(tasks_dir: pathlib.Path, tester_task_id: str, version: str, bug_id: str) -> None:
    task_dir = tasks_dir / tester_task_id
    (task_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    _write_task(tasks_dir, tester_task_id, version, "DONE", role="TESTER")
    (task_dir / "artifacts" / "report.md").write_text(
        "# TESTER Report\n\n"
        "### Finding 1: sample regression\n"
        f"**Filed As:** {bug_id}\n",
        encoding="utf-8",
    )


def _write_bug(bugs_dir: pathlib.Path, bug_id: str, status: str = "assigned") -> pathlib.Path:
    path = bugs_dir / f"{bug_id}.md"
    path.write_text(
        f"# Bug: {bug_id}\n\n## Status\n{status}\n\n## Symptom\nsample regression\n",
        encoding="utf-8",
    )
    return path


def _write_bundle(requirements_dir: pathlib.Path, version: str, bug_basenames: list, bugs_dir: pathlib.Path) -> pathlib.Path:
    bundle_path = requirements_dir / f"{version}-bugfix-bundle-20260727.md"
    lines = [
        f"# Requirements: {version} — BUG bundle\n\n",
        "## Status\nrunning\n\n",
        f"## Target Version\n{version}\n\n",
        "## Bundled Items\n\n",
    ]
    for name in bug_basenames:
        lines.append(f"- {name} (`{bugs_dir / name}`)\n")
    bundle_path.write_text("".join(lines), encoding="utf-8")
    return bundle_path


def _build_golden_fixture(tmp_path: pathlib.Path, project_name: str = "golden_project"):
    """Build a complete fixture whose facts match the golden 2026-07-18 row:
    ship-policy HALT, all findings filed, nothing shipped, no approval gate,
    clean lineage, attempt 1 < ceiling 3.

    Returns (kanban_root, proj, repo_path, version, halt_path, task_id, bug_id).
    """
    version = "v2.0.1"
    repo_path = tmp_path / "dev_repo"
    _init_git_repo(repo_path, "ai_main", f"ai_rc/{version}")

    kanban_root = _build_kanban_root(tmp_path, project_name, repo_path)
    proj = kanban_root / "projects" / project_name
    _write_release_state_md(proj, version)

    halt_path = proj / "HALT"
    _write_ship_policy_halt(halt_path)

    (proj / "release-state" / f"{version}.json").write_text(
        json.dumps({"rc": version, "attempt": 1, "outcome": "in_progress", "family_key": None}),
        encoding="utf-8",
    )

    bug_id = "BUG-0001-foo"
    _write_bug(proj / "bugs", bug_id)
    (proj / "tasks" / "queues" / "bug_backlog.md").write_text(f"- [x] {bug_id}\n", encoding="utf-8")

    _write_bundle(proj / "requirements", version, [f"{bug_id}.md"], proj / "bugs")

    task_id = "CODER-20260727-100-fix-something"
    _write_task(proj / "tasks", task_id, version, "WORKING")

    cm_task_id = "CM-20260727-001-open"
    _write_task(proj / "tasks", cm_task_id, version, "BLOCKED", role="CM")

    _write_tester_report(proj / "tasks", "TESTER-20260727-001-verify", version, bug_id + ".md")

    return kanban_root, proj, repo_path, version, halt_path, task_id, cm_task_id, bug_id


# ---------------------------------------------------------------------------
# Golden path: matched row executes the full drill
# ---------------------------------------------------------------------------


class TestGoldenPathExecutesTheDrill:
    def test_matched_row_runs_cancel_rc_and_writes_one_ledger_entry(self, tmp_path: pathlib.Path) -> None:
        (
            kanban_root, proj, repo_path, version, halt_path, task_id, cm_task_id, bug_id,
        ) = _build_golden_fixture(tmp_path)

        outcome = run_resolution_stage("golden_project", str(kanban_root))

        assert outcome.status == EXECUTED, outcome.reason
        assert outcome.ledger_entry_id
        assert not outcome.action_errors, outcome.action_errors

        # Ledger: exactly one entry, matched row recorded.
        ledger_path = proj / "auto-resolutions.md"
        assert ledger_path.is_file()
        ledger_text = ledger_path.read_text(encoding="utf-8")
        assert ledger_text.count("## Entry:") == 1
        assert "CANCEL-AND-REBUNDLE" in ledger_text
        assert "attempt 1 → 2" in ledger_text or "attempt 1 -> 2" in ledger_text
        assert bug_id in ledger_text

        # HALT cleared.
        assert not halt_path.exists()

        # release-state.md: Active RC reset to none by the real cancel-rc.sh run.
        release_state_text = (proj / "release-state.md").read_text(encoding="utf-8")
        assert "## Active RC\nnone" in release_state_text

        # Local RC branch deleted by cancel-rc.sh.
        branches = subprocess.run(
            ["git", "-C", str(repo_path), "branch", "--list", f"ai_rc/{version}"],
            capture_output=True, text=True, check=True,
        ).stdout
        assert branches.strip() == ""

        # Task set marked WONT-DO (the active CODER task and the blocked CM task).
        for tid in (task_id, cm_task_id):
            status_text = (proj / "tasks" / tid / "status.md").read_text(encoding="utf-8")
            assert "## State\nWONT-DO" in status_text

        # Bundle intake item closed wont-do.
        bundle_path = proj / "requirements" / f"{version}-bugfix-bundle-20260727.md"
        bundle_text = bundle_path.read_text(encoding="utf-8")
        assert "## Status\nwont-do" in bundle_text

        # Cargo bug reset to open.
        bug_text = (proj / "bugs" / f"{bug_id}.md").read_text(encoding="utf-8")
        assert "## Status\nopen" in bug_text
        backlog_text = (proj / "tasks" / "queues" / "bug_backlog.md").read_text(encoding="utf-8")
        assert f"[ ] {bug_id}" in backlog_text

    def test_second_call_after_execution_is_a_silent_no_op(self, tmp_path: pathlib.Path) -> None:
        """Once the HALT is cleared and Active RC reset, a second firing has
        nothing left to evaluate — proves the stage does not loop or re-fire
        on its own prior work."""
        (kanban_root, proj, *_rest) = _build_golden_fixture(tmp_path)
        first = run_resolution_stage("golden_project", str(kanban_root))
        assert first.status == EXECUTED, first.reason

        ledger_path = proj / "auto-resolutions.md"
        entry_count_after_first = ledger_path.read_text(encoding="utf-8").count("## Entry:")

        second = run_resolution_stage("golden_project", str(kanban_root))
        assert second.status == NO_OP

        entry_count_after_second = ledger_path.read_text(encoding="utf-8").count("## Entry:")
        assert entry_count_after_second == entry_count_after_first


# ---------------------------------------------------------------------------
# Operator-HALT inviolability (Acceptance Criterion 6)
# ---------------------------------------------------------------------------


class TestOperatorHaltInviolability:
    def test_operator_halt_alongside_ship_policy_halt_declines_and_touches_nothing(
        self, tmp_path: pathlib.Path
    ) -> None:
        (
            kanban_root, proj, repo_path, version, halt_path, task_id, cm_task_id, bug_id,
        ) = _build_golden_fixture(tmp_path)

        # An operator-authored HALT sits at the GLOBAL scope alongside the
        # project-scoped ship-policy HALT.
        global_halt = kanban_root / "HALT"
        _write_operator_halt(global_halt)

        outcome = run_resolution_stage("golden_project", str(kanban_root))

        assert outcome.status == DECLINED
        assert "operator-authored HALT" in outcome.reason

        # Both HALT files untouched.
        assert halt_path.is_file()
        assert global_halt.is_file()

        # Nothing else touched: no ledger, Active RC still set, task untouched,
        # bug untouched.
        assert not (proj / "auto-resolutions.md").exists()
        release_state_text = (proj / "release-state.md").read_text(encoding="utf-8")
        assert f"## Active RC\n{version}" in release_state_text
        status_text = (proj / "tasks" / task_id / "status.md").read_text(encoding="utf-8")
        assert "## State\nWORKING" in status_text
        bug_text = (proj / "bugs" / f"{bug_id}.md").read_text(encoding="utf-8")
        assert "## Status\nassigned" in bug_text


# ---------------------------------------------------------------------------
# Fail-closed declines
# ---------------------------------------------------------------------------


class TestFailClosedDeclines:
    def test_no_halt_at_all_is_a_silent_no_op(self, tmp_path: pathlib.Path) -> None:
        (kanban_root, proj, *_rest) = _build_golden_fixture(tmp_path)
        (proj / "HALT").unlink()

        outcome = run_resolution_stage("golden_project", str(kanban_root))

        assert outcome.status == NO_OP
        assert not (proj / "overwatch" / "actions.log").exists()
        assert not (proj / "auto-resolutions.md").exists()

    def test_halt_present_but_not_cm_authored_declines(self, tmp_path: pathlib.Path) -> None:
        (kanban_root, proj, *_rest) = _build_golden_fixture(tmp_path)
        halt_path = proj / "HALT"
        _write_operator_halt(halt_path)

        outcome = run_resolution_stage("golden_project", str(kanban_root))

        assert outcome.status == DECLINED
        assert halt_path.is_file()
        assert not (proj / "auto-resolutions.md").exists()

    def test_active_rc_none_declines_and_leaves_halt_in_place(self, tmp_path: pathlib.Path) -> None:
        (kanban_root, proj, repo_path, version, halt_path, *_rest) = _build_golden_fixture(tmp_path)
        _write_release_state_md(proj, "none")

        outcome = run_resolution_stage("golden_project", str(kanban_root))

        assert outcome.status == DECLINED
        assert "Active RC is none" in outcome.reason
        assert halt_path.is_file()
        assert not (proj / "auto-resolutions.md").exists()

    def test_approval_gate_present_declines_and_leaves_halt_in_place(self, tmp_path: pathlib.Path) -> None:
        (kanban_root, proj, repo_path, version, halt_path, *_rest) = _build_golden_fixture(tmp_path)
        approve_task = proj / "tasks" / f"HUMAN-APPROVE-{version}-001"
        approve_task.mkdir(parents=True, exist_ok=True)
        (approve_task / "status.md").write_text(
            "# Status\n\n## State\nBACKLOG\n", encoding="utf-8"
        )

        outcome = run_resolution_stage("golden_project", str(kanban_root))

        assert outcome.status == DECLINED
        assert halt_path.is_file()
        assert not (proj / "auto-resolutions.md").exists()

    def test_unfiled_finding_declines_and_leaves_halt_in_place(self, tmp_path: pathlib.Path) -> None:
        (kanban_root, proj, repo_path, version, halt_path, task_id, cm_task_id, bug_id) = _build_golden_fixture(tmp_path)
        report_path = proj / "tasks" / "TESTER-20260727-001-verify" / "artifacts" / "report.md"
        report_path.write_text(
            "# TESTER Report\n\n### Finding 1: sample regression\n**Filed As:** none\n",
            encoding="utf-8",
        )

        outcome = run_resolution_stage("golden_project", str(kanban_root))

        assert outcome.status == DECLINED
        assert halt_path.is_file()
        assert not (proj / "auto-resolutions.md").exists()
        # The bug must NOT have been reset — the decline path never executes
        # any part of the drill.
        bug_text = (proj / "bugs" / f"{bug_id}.md").read_text(encoding="utf-8")
        assert "## Status\nassigned" in bug_text


# ---------------------------------------------------------------------------
# The ceiling — hard-block after repeated cancel-and-rebundle attempts
# ---------------------------------------------------------------------------


def _build_ceiling_fixture(tmp_path: pathlib.Path, project_name: str = "ceiling_project"):
    """Build a fixture whose facts otherwise match the golden row EXCEPT the
    active RC's own attempt count equals the configured ceiling (3), and two
    earlier cancelled attempts for the same bundle family exist in
    release-state/history/ plus two still-pending ledger entries for that
    family — the shape "third consecutive failure of one bundle family"
    (requirements v2.0.0 Acceptance Criterion 3) that must HALT-hard with
    the full attempt history rather than execute a fourth cancel drill.
    """
    version = "v2.0.3"
    repo_path = tmp_path / "dev_repo"
    _init_git_repo(repo_path, "ai_main", f"ai_rc/{version}")

    kanban_root = _build_kanban_root(tmp_path, project_name, repo_path)
    proj = kanban_root / "projects" / project_name
    _write_release_state_md(proj, version)

    halt_path = proj / "HALT"
    _write_ship_policy_halt(halt_path)

    bug_id = "BUG-0001-foo"
    _write_bug(proj / "bugs", bug_id)
    (proj / "tasks" / "queues" / "bug_backlog.md").write_text(f"- [x] {bug_id}\n", encoding="utf-8")
    _write_bundle(proj / "requirements", version, [f"{bug_id}.md"], proj / "bugs")

    family_key = compute_family_key([f"{bug_id}.md"])

    state_dir = proj / "release-state"
    history_dir = state_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)

    # Two prior attempts for this exact family, both cancelled — the retry
    # history a ceiling decline's block text must surface in full.
    (history_dir / "v2.0.1-cancelled-2026-07-25T00-00-00Z.json").write_text(
        json.dumps({"rc": "v2.0.1", "attempt": 1, "outcome": "cancelled", "family_key": family_key}),
        encoding="utf-8",
    )
    (history_dir / "v2.0.2-cancelled-2026-07-26T00-00-00Z.json").write_text(
        json.dumps({"rc": "v2.0.2", "attempt": 2, "outcome": "cancelled", "family_key": family_key}),
        encoding="utf-8",
    )

    # The active RC's own per-RC state: attempt 3, the configured ceiling
    # (_build_kanban_root writes [release] max_rc_retries = 3 in both
    # kanban.cfg and project.cfg).
    (state_dir / f"{version}.json").write_text(
        json.dumps({"rc": version, "attempt": 3, "outcome": "in_progress", "family_key": family_key}),
        encoding="utf-8",
    )

    task_id = "CODER-20260727-101-fix-something"
    _write_task(proj / "tasks", task_id, version, "WORKING")
    cm_task_id = "CM-20260727-002-open"
    _write_task(proj / "tasks", cm_task_id, version, "BLOCKED", role="CM")
    _write_tester_report(proj / "tasks", "TESTER-20260727-002-verify", version, bug_id + ".md")

    # Seed the ledger with the two prior resolution entries this family's
    # story already carries — both still "pending" (never backfilled), the
    # shape a real installation would be in after two cancel-and-rebundle
    # cycles that have not yet reached a terminal outcome.
    ledger_path = ledger_path_for_project(str(kanban_root), project_name)
    entry1_id = append_entry(
        ledger_path,
        LedgerEntry(
            timestamp="2026-07-25T00:00:00Z",
            rc="v2.0.1",
            attempt_from=1,
            attempt_to=2,
            trigger="ship-policy-halt",
            matrix_row="CANCEL-AND-REBUNDLE",
            findings=[bug_id],
            actions_taken=["cm-cancel-rc.sh --project ceiling_project --key v2.0.1 --yes"],
            evidence_paths=[],
            family_key=family_key,
        ),
    )
    entry2_id = append_entry(
        ledger_path,
        LedgerEntry(
            timestamp="2026-07-26T00:00:00Z",
            rc="v2.0.2",
            attempt_from=2,
            attempt_to=3,
            trigger="ship-policy-halt",
            matrix_row="CANCEL-AND-REBUNDLE",
            findings=[bug_id],
            actions_taken=["cm-cancel-rc.sh --project ceiling_project --key v2.0.2 --yes"],
            evidence_paths=[],
            family_key=family_key,
        ),
    )

    return (
        kanban_root, proj, repo_path, version, halt_path, task_id, cm_task_id, bug_id,
        ledger_path, entry1_id, entry2_id,
    )


class TestCeilingReached:
    def test_attempt_at_ceiling_declines_with_full_history_and_no_drill_action(
        self, tmp_path: pathlib.Path
    ) -> None:
        (
            kanban_root, proj, repo_path, version, halt_path, task_id, cm_task_id, bug_id,
            ledger_path, entry1_id, entry2_id,
        ) = _build_ceiling_fixture(tmp_path)

        outcome = run_resolution_stage("ceiling_project", str(kanban_root))

        # AC1: attempts == ceiling yields HALT-hard with no cancel action.
        assert outcome.status == DECLINED, outcome.reason
        assert not outcome.ledger_entry_id
        assert not outcome.actions_taken

        # AC2: block text carries the full attempt history for the family —
        # requirements v2.0.0 Goal 4's own example shape.
        assert "attempt 3/3" in outcome.reason
        assert "v2.0.1" in outcome.reason
        assert "v2.0.2" in outcome.reason
        assert "prior attempts cancelled for" in outcome.reason

        # No cancel/reversible action taken — every piece of the drill left
        # exactly as the fixture set it up.
        assert halt_path.is_file()
        release_state_text = (proj / "release-state.md").read_text(encoding="utf-8")
        assert f"## Active RC\n{version}" in release_state_text
        status_text = (proj / "tasks" / task_id / "status.md").read_text(encoding="utf-8")
        assert "## State\nWORKING" in status_text
        cm_status_text = (proj / "tasks" / cm_task_id / "status.md").read_text(encoding="utf-8")
        assert "## State\nBLOCKED" in cm_status_text
        bug_text = (proj / "bugs" / f"{bug_id}.md").read_text(encoding="utf-8")
        assert "## Status\nassigned" in bug_text
        backlog_text = (proj / "tasks" / "queues" / "bug_backlog.md").read_text(encoding="utf-8")
        assert f"[x] {bug_id}" in backlog_text
        bundle_text = (proj / "requirements" / f"{version}-bugfix-bundle-20260727.md").read_text(encoding="utf-8")
        assert "## Status\nrunning" in bundle_text

        # No NEW ledger entry written; the family's two existing entries are
        # backfilled to CEILING-BLOCKED — closing that family's story.
        entries = parse_entries(str(ledger_path))
        assert len(entries) == 2
        for entry in entries:
            assert entry["outcome"] == "CEILING-BLOCKED"

        # Action log carries the same narrated reason (auditable decline).
        action_log = (proj / "overwatch" / "actions.log").read_text(encoding="utf-8")
        assert "attempt 3/3" in action_log

    def test_second_call_after_ceiling_decline_is_idempotent(self, tmp_path: pathlib.Path) -> None:
        """A repeat firing on the same still-blocked state must not error on
        the already-backfilled ledger entries (backfill_family_outcome is
        idempotent — see ledger.py)."""
        (kanban_root, proj, *_rest) = _build_ceiling_fixture(tmp_path)

        first = run_resolution_stage("ceiling_project", str(kanban_root))
        assert first.status == DECLINED

        second = run_resolution_stage("ceiling_project", str(kanban_root))
        assert second.status == DECLINED
        assert "attempt 3/3" in second.reason


# ---------------------------------------------------------------------------
# Missing ceiling config fails loud
# ---------------------------------------------------------------------------


class TestMissingCeilingConfigFailsLoud:
    def test_missing_max_rc_retries_declines_loudly_with_no_action(self, tmp_path: pathlib.Path) -> None:
        (
            kanban_root, proj, repo_path, version, halt_path, task_id, cm_task_id, bug_id,
        ) = _build_golden_fixture(tmp_path, project_name="noceiling_project")

        # Strip [release] max_rc_retries from BOTH config sources — the one
        # fact resolution_matrix.py never silently defaults (requirements
        # Goal 4: "Fail-loud if the key is absent when the resolver needs it").
        (kanban_root / "kanban.cfg").write_text(
            "[paths]\n\n[chain]\npm_mode = automatic\n", encoding="utf-8"
        )
        (proj / "project.cfg").write_text(
            "[project]\n"
            "project_name = noceiling_project\n"
            f"dev_tree_path = {repo_path}\n"
            "branch_prefix = ai_\n"
            "push_to_remote = false\n",
            encoding="utf-8",
        )

        outcome = run_resolution_stage("noceiling_project", str(kanban_root))

        assert outcome.status == DECLINED
        assert "max_rc_retries is required but absent" in outcome.reason
        assert not outcome.actions_taken

        # No action taken: HALT, task state, and bug all untouched.
        assert halt_path.is_file()
        status_text = (proj / "tasks" / task_id / "status.md").read_text(encoding="utf-8")
        assert "## State\nWORKING" in status_text
        bug_text = (proj / "bugs" / f"{bug_id}.md").read_text(encoding="utf-8")
        assert "## Status\nassigned" in bug_text
        assert not (proj / "auto-resolutions.md").exists()

        # The failure is narrated (loud), not silent — the exact CeilingConfigError
        # text lands in the OVERWATCH action log for operator visibility.
        action_log = (proj / "overwatch" / "actions.log").read_text(encoding="utf-8")
        assert "max_rc_retries is required but absent" in action_log


# ---------------------------------------------------------------------------
# Sub-ceiling attempts remain eligible for the matched resolution
# ---------------------------------------------------------------------------


class TestSubCeilingRemainsEligible:
    def test_attempt_below_ceiling_still_executes_matched_resolution(self, tmp_path: pathlib.Path) -> None:
        (
            kanban_root, proj, repo_path, version, halt_path, task_id, cm_task_id, bug_id,
        ) = _build_golden_fixture(tmp_path, project_name="subceiling_project")

        # Second attempt of three (ceiling 3) — still below the ceiling, so
        # the golden row must still match and execute.
        (proj / "release-state" / f"{version}.json").write_text(
            json.dumps({"rc": version, "attempt": 2, "outcome": "in_progress", "family_key": None}),
            encoding="utf-8",
        )

        outcome = run_resolution_stage("subceiling_project", str(kanban_root))

        assert outcome.status == EXECUTED, outcome.reason
        ledger_text = (proj / "auto-resolutions.md").read_text(encoding="utf-8")
        assert "attempt 2 → 3" in ledger_text or "attempt 2 -> 3" in ledger_text
        assert not halt_path.exists()


# ---------------------------------------------------------------------------
# classify_halt_file unit coverage
# ---------------------------------------------------------------------------


class TestClassifyHaltFile:
    def test_absent_file(self, tmp_path: pathlib.Path) -> None:
        assert classify_halt_file(str(tmp_path / "HALT")) == "absent"

    @pytest.mark.parametrize(
        "writer, expected",
        [
            pytest.param(_write_ship_policy_halt, "ship-policy", id="ship_policy_header"),
            pytest.param(_write_operator_halt, "operator", id="operator_content"),
        ],
    )
    def test_classify_by_content(
        self, tmp_path: pathlib.Path, writer, expected: str
    ) -> None:
        path = tmp_path / "HALT"
        writer(path)
        assert classify_halt_file(str(path)) == expected

    def test_empty_file_is_operator(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "HALT"
        path.write_text("", encoding="utf-8")
        assert classify_halt_file(str(path)) == "operator"
