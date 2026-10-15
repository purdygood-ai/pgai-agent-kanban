"""
test_overwatch_wake_driven_fixtures.py
=======================================
The v2.0.0 gated fixture suite: proves OVERWATCH's autonomous block-resolution
stage end-to-end, driven through the REAL production entry point — the wake
scripts (scripts/wake/claude.sh) a cron firing actually invokes — rather than
through internal Python helpers (pgai_agent_kanban.overwatch.resolution_stage
is never imported and called directly in this file; every fixture below goes
through a real `bash .../scripts/wake/claude.sh --agent=... --max-tasks=1`
subprocess, or, for the ledger-atomicity fixture, the exact
`python3 -m pgai_agent_kanban.overwatch.resolution_stage` CLI invocation
wake_common.sh's run_overwatch_resolution_stage() itself shells out to).

Coverage (requirements/v2.0.0-autonomous-block-resolution.md, Acceptance
Criteria 1-6, 8):

  AC1  Golden fixture — the 2026-07-18 block's facts resolve to
       CANCEL-AND-REBUNDLE with zero interventions, and the next discovery
       tick (a second, independent wake-driven AGENT=pm firing) rebundles
       the reopened bug into a fresh patch-versioned requirements file.
  AC2  Fail-closed fixtures — each single fact off (an unfiled finding, an
       operator commit in the RC lineage, an approval gate present, attempts
       at the ceiling) HALT-hards with no action and a narrated reason.
       (The fifth AC2 sub-case — an operator-authored HALT alongside the
       ship-policy HALT — is covered by TestOperatorHaltInviolability below,
       which doubles as AC6.)
  AC3  Ceiling fixture — a bundle family's third consecutive failure
       HALT-hards with the full attempt history in the narrated block text.
  AC4  Ledger atomicity — a resolution killed mid-execution leaves the
       ledger entry with its actions recorded but the real state untouched;
       OVERWATCH's next sweep (another production wake-driven firing)
       completes the drill and reconciles real state.
  AC5  Counter keying — the same cargo set increments the family's attempt
       counter; a different cargo set starts a fresh family at 1.
  AC6  Operator-HALT inviolability — an operator-placed fleet-wide HALT
       alongside the project's ship-policy HALT stops the overwatch wake
       before any resolution, narrated, touching neither HALT file.

The declines (AC2, AC6) are the primary assertions per the requirements
doc's "Notes for TESTER": a resolver that resolves too much is a worse
defect than one that resolves nothing.

Design notes
------------
- Every kanban root built here is a full, self-contained copy of this dev
  tree's scripts/, pgai_agent_kanban/, and workflows/ directories (the same
  set install.sh mirrors into a live install), so the wake scripts invoked
  as subprocesses are byte-identical to production and self-locate exactly
  as they would on a real host.
- Per roles/OVERWATCH.md, a project only receives
  autonomous OVERWATCH resolution once its overwatch_backlog.md queue file
  exists (create-project.sh does not provision one by default — this is the
  per-project opt-in gate, reusing the existing "queue file must exist"
  convention rather than a bespoke flag). Every fixture below creates that
  file explicitly, mirroring the deliberate operator enablement step.
- The AGENT=overwatch and AGENT=pm queues built here are always empty (no
  BACKLOG-marked entries), so process_one_task returns immediately without
  ever invoking the real `claude` CLI — the wake scripts run their
  substrate logic (dev-tree gate, resolution stage, discovery pipeline)
  with zero external API calls or cost.
- All temp paths use pytest's tmp_path. No bare /tmp.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional

import pytest

from pgai_agent_kanban.cm.bundle_family import compute_family_key
from pgai_agent_kanban.overwatch.ledger import ledger_path_for_project, parse_entries
from tests.integration.helpers import run_entrypoint
from tests.fixtures.binary_guard import require_binary


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Every test here builds, inspects, or drives a real git repository; a
    module-wide autouse guard keeps that requirement in one place instead
    of repeating a per-test check at each git-dependent call site.
    """
    require_binary("git", label="overwatch-wake-driven-fixtures")

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_WAKE_CLAUDE_REL = pathlib.Path("scripts") / "wake" / "claude.sh"


# ---------------------------------------------------------------------------
# Fixture-tree builders
# ---------------------------------------------------------------------------


def _run_git(repo_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_path), *args], capture_output=True, text=True, check=True
    )


def _init_git_repo(repo_path: pathlib.Path, main_branch: str, rc_branch: str) -> None:
    """Initialise a local git repo: one commit on *main_branch*, then branch
    *rc_branch* off it with zero further commits — clean lineage, no direct
    (operator) commits on the RC branch's first-parent chain."""
    repo_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", f"--initial-branch={main_branch}", str(repo_path)],
        capture_output=True, check=True,
    )
    _run_git(repo_path, "config", "--local", "user.email", "test@pgai.local")
    _run_git(repo_path, "config", "--local", "user.name", "PGAI Test")
    (repo_path / "README.md").write_text("# Test repo\n", encoding="utf-8")
    _run_git(repo_path, "add", "README.md")
    _run_git(repo_path, "commit", "-m", "Initial commit")
    _run_git(repo_path, "checkout", "-b", rc_branch)
    _run_git(repo_path, "checkout", main_branch)


def _add_direct_commit_to_branch(repo_path: pathlib.Path, branch: str, filename: str) -> None:
    """Commit directly onto *branch* (a plain, single-parent commit — never a
    --no-ff merge) — the deterministic "operator commit in RC lineage"
    signal resolution_matrix.py's operator_commit_in_rc_lineage() detects."""
    _run_git(repo_path, "checkout", branch)
    (repo_path / filename).write_text("direct operator edit\n", encoding="utf-8")
    _run_git(repo_path, "add", filename)
    _run_git(repo_path, "commit", "-m", "Direct operator commit on RC branch")


def _write_queue_file(path: pathlib.Path, title: str) -> None:
    """An always-empty per-agent backlog: no bracket-marker lines, so
    process_one_task's get_first_pending_task finds nothing and returns
    immediately — no dispatch, no real CLI invocation, ever."""
    path.write_text(
        f"# {title}\n\nNo tasks queued by this fixture.\n", encoding="utf-8"
    )


def _build_kanban_root(
    tmp_path: pathlib.Path,
    project_name: str,
    repo_path: pathlib.Path,
    max_rc_retries: int = 3,
) -> pathlib.Path:
    """Build a full, self-contained kanban root: a real copy of this dev
    tree's scripts/, pgai_agent_kanban/, and workflows/ trees (the same set
    install.sh mirrors into a live install), plus the minimal per-project
    scaffolding the wake scripts and resolution stage need. The wake script
    invoked against this root is therefore byte-identical to production.
    """
    root = tmp_path / f"kanban_root_{project_name}"
    proj = root / "projects" / project_name
    root.mkdir(parents=True, exist_ok=True)

    (root / "kanban.cfg").write_text(
        "[paths]\n\n[chain]\npm_mode = automatic\n\n"
        "[release]\nmax_rc_retries = 3\n\n[wake]\nmax_tasks_per_wake = 1\n",
        encoding="utf-8",
    )
    (root / "projects.cfg").write_text(
        f"[project:{project_name}]\npriority=1\ndescription=wake-driven fixture project\nenabled=true\n",
        encoding="utf-8",
    )

    proj.mkdir(parents=True, exist_ok=True)
    (proj / "project.cfg").write_text(
        "[project]\n"
        f"project_name = {project_name}\n"
        f"dev_tree_path = {repo_path}\n"
        "branch_prefix = ai_\n"
        "push_to_remote = false\n"
        f"\n[release]\nmax_rc_retries = {max_rc_retries}\n",
        encoding="utf-8",
    )

    (proj / "requirements").mkdir(parents=True, exist_ok=True)
    (proj / "bugs").mkdir(parents=True, exist_ok=True)
    (proj / "release-state").mkdir(parents=True, exist_ok=True)
    queues_dir = proj / "tasks" / "queues"
    queues_dir.mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)

    # The deliberate per-project enablement act (roles/OVERWATCH.md):
    # only a project whose overwatch_backlog.md exists gets the
    # autonomous resolution stage invoked at all (run_project_chain's
    # backlog-file gate runs BEFORE run_overwatch_resolution_stage).
    for agent, title in (
        ("overwatch", "Overwatch Backlog"),
        ("pm", "PM Backlog"),
        ("coder", "Coder Backlog"),
        ("writer", "Writer Backlog"),
        ("tester", "Tester Backlog"),
        ("cm", "CM Backlog"),
    ):
        _write_queue_file(queues_dir / f"{agent}_backlog.md", title)
    (queues_dir / "bug_backlog.md").write_text("# Bug Backlog\n\n", encoding="utf-8")
    (queues_dir / "priority_backlog.md").write_text("# Priority Backlog\n\n", encoding="utf-8")

    # Full, real copies — the same set install.sh mirrors into a live
    # install — so the wake scripts run byte-identical to production.
    shutil.copytree(_TEAM_DIR / "scripts", root / "scripts")
    shutil.copytree(_TEAM_DIR / "pgai_agent_kanban", root / "pgai_agent_kanban")
    shutil.copytree(_TEAM_DIR / "workflows", root / "workflows")
    shutil.copytree(_TEAM_DIR / "pm-agent", root / "pm-agent")

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


def _write_tester_report(tasks_dir: pathlib.Path, tester_task_id: str, version: str, filed_as: str) -> None:
    task_dir = tasks_dir / tester_task_id
    (task_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    _write_task(tasks_dir, tester_task_id, version, "DONE", role="TESTER")
    (task_dir / "artifacts" / "report.md").write_text(
        "# TESTER Report\n\n### Finding 1: sample regression\n" f"**Filed As:** {filed_as}\n",
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


@dataclass
class Fixture:
    """Handles for one wake-driven fixture's on-disk state."""

    kanban_root: pathlib.Path
    proj: pathlib.Path
    repo_path: pathlib.Path
    version: str
    halt_path: pathlib.Path
    task_id: str
    cm_task_id: str
    bug_id: str
    bundle_path: pathlib.Path
    ledger_path: pathlib.Path = field(init=False)

    def __post_init__(self) -> None:
        self.ledger_path = pathlib.Path(
            ledger_path_for_project(str(self.kanban_root), self.proj.name)
        )


def _build_golden_fixture(
    tmp_path: pathlib.Path,
    project_name: str,
    bug_id: str = "BUG-0001-foo",
    version: str = "v2.0.1",
    attempt: int = 1,
    ceiling: int = 3,
) -> Fixture:
    """Build a fixture whose facts match the golden 2026-07-18 row: a
    ship-policy HALT, all findings filed, nothing shipped, no approval gate,
    clean lineage, attempt < ceiling. Parametrized on bug_id/version/attempt
    so callers can reuse this for counter-keying and ceiling variants."""
    repo_path = tmp_path / f"dev_repo_{project_name}"
    _init_git_repo(repo_path, "ai_main", f"ai_rc/{version}")

    kanban_root = _build_kanban_root(tmp_path, project_name, repo_path, max_rc_retries=ceiling)
    proj = kanban_root / "projects" / project_name
    _write_release_state_md(proj, version)

    # Project scope, matching CM's real write_halt_file()
    # (team/scripts/lib/cm/release_preflight.sh): CM writes the ship-policy
    # HALT at projects/<name>/HALT, never at the fleet-wide $KANBAN_ROOT/HALT,
    # which only an operator writes. The wake loop's overwatch carve-out lets
    # the firing through the per-project HALT fast-exit so the resolution
    # stage can classify this file; a fleet-wide HALT instead stops the
    # overwatch wake outright.
    halt_path = proj / "HALT"
    _write_ship_policy_halt(halt_path)

    (proj / "release-state" / f"{version}.json").write_text(
        json.dumps({"rc": version, "attempt": attempt, "outcome": "in_progress", "family_key": None}),
        encoding="utf-8",
    )

    _write_bug(proj / "bugs", bug_id)
    (proj / "tasks" / "queues" / "bug_backlog.md").write_text(f"- [x] {bug_id}\n", encoding="utf-8")
    bundle_path = _write_bundle(proj / "requirements", version, [f"{bug_id}.md"], proj / "bugs")

    task_id = "CODER-20260727-100-fix-something"
    _write_task(proj / "tasks", task_id, version, "WORKING")
    cm_task_id = "CM-20260727-001-open"
    _write_task(proj / "tasks", cm_task_id, version, "BLOCKED", role="CM")
    _write_tester_report(proj / "tasks", "TESTER-20260727-001-verify", version, bug_id + ".md")

    return Fixture(
        kanban_root=kanban_root, proj=proj, repo_path=repo_path, version=version,
        halt_path=halt_path, task_id=task_id, cm_task_id=cm_task_id, bug_id=bug_id,
        bundle_path=bundle_path,
    )


def _run_wake(kanban_root: pathlib.Path, agent: str, timeout: int = 90) -> "subprocess.CompletedProcess":
    """Invoke the REAL production entry point: bash scripts/wake/claude.sh
    --agent=<agent> --max-tasks=1, against *kanban_root*. This is the exact
    subprocess a cron firing runs; nothing here is a shortcut or a stub of
    the wake loop itself."""
    wake_script = kanban_root / _WAKE_CLAUDE_REL
    assert wake_script.is_file(), f"wake script not found at {wake_script}"
    result = run_entrypoint(
        ["bash", str(wake_script), f"--agent={agent}", "--max-tasks=1"],
        kanban_root=kanban_root,
        timeout=timeout,
    )
    return result


def _resolution_stage_log(kanban_root: pathlib.Path) -> str:
    log_path = kanban_root / "logs" / "overwatch-resolution-stage.log"
    return log_path.read_text(encoding="utf-8") if log_path.is_file() else ""


def _action_log(proj: pathlib.Path) -> str:
    log_path = proj / "overwatch" / "actions.log"
    return log_path.read_text(encoding="utf-8") if log_path.is_file() else ""


# ---------------------------------------------------------------------------
# AC1 — Golden fixture: wake-driven execute, then next-tick rebundle
# ---------------------------------------------------------------------------


class TestGoldenFixtureWakeDriven:
    def test_matched_block_resolves_with_zero_interventions_via_real_wake_entrypoint(
        self, tmp_path: pathlib.Path
    ) -> None:
        fx = _build_golden_fixture(tmp_path, "wd_golden")

        result = _run_wake(fx.kanban_root, "overwatch")
        assert result.returncode == 0, (
            f"wake/claude.sh --agent=overwatch exited {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

        stage_log = _resolution_stage_log(fx.kanban_root)
        assert "status=executed" in stage_log, f"resolution-stage log:\n{stage_log}"

        # Ledger: exactly one entry, matched row recorded.
        ledger_text = fx.ledger_path.read_text(encoding="utf-8")
        assert ledger_text.count("## Entry:") == 1
        assert "CANCEL-AND-REBUNDLE" in ledger_text
        assert "attempt 1 → 2" in ledger_text or "attempt 1 -> 2" in ledger_text
        assert fx.bug_id in ledger_text

        # HALT cleared; Active RC reset to none by the real cancel-rc.sh run.
        assert not fx.halt_path.exists()
        release_state_text = (fx.proj / "release-state.md").read_text(encoding="utf-8")
        assert "## Active RC\nnone" in release_state_text

        # Local RC branch deleted by the real cancel-rc.sh subprocess.
        branches = _run_git(fx.repo_path, "branch", "--list", f"ai_rc/{fx.version}").stdout
        assert branches.strip() == ""

        # Task set marked WONT-DO; bundle closed wont-do; bug reset to open.
        for tid in (fx.task_id, fx.cm_task_id):
            status_text = (fx.proj / "tasks" / tid / "status.md").read_text(encoding="utf-8")
            assert "## State\nWONT-DO" in status_text
        bundle_text = fx.bundle_path.read_text(encoding="utf-8")
        assert "## Status\nwont-do" in bundle_text
        bug_text = (fx.proj / "bugs" / f"{fx.bug_id}.md").read_text(encoding="utf-8")
        assert "## Status\nopen" in bug_text
        backlog_text = (fx.proj / "tasks" / "queues" / "bug_backlog.md").read_text(encoding="utf-8")
        assert f"[ ] {fx.bug_id}" in backlog_text

        # Zero interventions: nothing above required any action beyond the
        # single wake/claude.sh --agent=overwatch firing.

    def test_next_discovery_tick_rebundles_the_reopened_bug_via_real_pm_wake(
        self, tmp_path: pathlib.Path
    ) -> None:
        fx = _build_golden_fixture(tmp_path, "wd_golden_rebundle")

        overwatch_result = _run_wake(fx.kanban_root, "overwatch")
        assert overwatch_result.returncode == 0, overwatch_result.stderr
        assert not fx.halt_path.exists()
        bug_text = (fx.proj / "bugs" / f"{fx.bug_id}.md").read_text(encoding="utf-8")
        assert "## Status\nopen" in bug_text

        requirements_dir = fx.proj / "requirements"
        pre_files = set(requirements_dir.glob("*.md"))

        pm_result = _run_wake(fx.kanban_root, "pm")
        assert pm_result.returncode == 0, (
            f"wake/claude.sh --agent=pm exited {pm_result.returncode}\n"
            f"stdout:\n{pm_result.stdout}\nstderr:\n{pm_result.stderr}"
        )

        post_files = set(requirements_dir.glob("*.md"))
        new_files = post_files - pre_files
        assert new_files, (
            "Next discovery tick produced no new requirements bundle file. "
            f"pm wake stdout:\n{pm_result.stdout}\nstderr:\n{pm_result.stderr}"
        )
        new_bundle_text = "\n".join(p.read_text(encoding="utf-8") for p in new_files)
        assert fx.bug_id in new_bundle_text, (
            f"Reopened bug {fx.bug_id} not present in newly bundled requirements file(s): {new_files}"
        )


# ---------------------------------------------------------------------------
# AC2 — Fail-closed fixtures (any single fact off HALT-hards, no action)
# ---------------------------------------------------------------------------


def _mutate_unfiled_finding(fx: Fixture) -> None:
    report_path = fx.proj / "tasks" / "TESTER-20260727-001-verify" / "artifacts" / "report.md"
    report_path.write_text(
        "# TESTER Report\n\n### Finding 1: sample regression\n**Filed As:** none\n",
        encoding="utf-8",
    )


def _mutate_operator_commit_in_lineage(fx: Fixture) -> None:
    _add_direct_commit_to_branch(fx.repo_path, f"ai_rc/{fx.version}", "direct_edit.txt")


def _mutate_approval_gate_present(fx: Fixture) -> None:
    approve_task = fx.proj / "tasks" / f"HUMAN-APPROVE-{fx.version}-001"
    approve_task.mkdir(parents=True, exist_ok=True)
    (approve_task / "status.md").write_text("# Status\n\n## State\nBACKLOG\n", encoding="utf-8")


def _mutate_attempts_at_ceiling(fx: Fixture) -> None:
    (fx.proj / "release-state" / f"{fx.version}.json").write_text(
        json.dumps({"rc": fx.version, "attempt": 3, "outcome": "in_progress", "family_key": None}),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "mutator, case_name",
    [
        (_mutate_unfiled_finding, "unfiled_finding"),
        (_mutate_operator_commit_in_lineage, "operator_commit_in_lineage"),
        (_mutate_approval_gate_present, "approval_gate_present"),
        (_mutate_attempts_at_ceiling, "attempts_at_ceiling"),
    ],
)
class TestFailClosedWakeDriven:
    def test_single_fact_off_halt_hards_with_no_action_via_real_wake_entrypoint(
        self, tmp_path: pathlib.Path, mutator, case_name
    ) -> None:
        fx = _build_golden_fixture(tmp_path, f"wd_failclosed_{case_name}")
        mutator(fx)

        result = _run_wake(fx.kanban_root, "overwatch")
        assert result.returncode == 0, result.stderr

        stage_log = _resolution_stage_log(fx.kanban_root)
        assert "status=declined" in stage_log, (
            f"case={case_name}: expected a declined outcome; resolution-stage log:\n{stage_log}"
        )

        # No action taken: HALT, bug, and task state are exactly as the
        # fixture set them up (except for the specific mutation this case
        # introduced), and no ledger was ever created.
        assert not fx.ledger_path.exists(), f"case={case_name}: a ledger entry must never be written on decline"
        assert fx.halt_path.is_file(), f"case={case_name}: the ship-policy HALT must remain in place on decline"
        status_text = (fx.proj / "tasks" / fx.task_id / "status.md").read_text(encoding="utf-8")
        assert "## State\nWORKING" in status_text, f"case={case_name}: the active task must be untouched on decline"
        bug_text = (fx.proj / "bugs" / f"{fx.bug_id}.md").read_text(encoding="utf-8")
        assert "## Status\nassigned" in bug_text, f"case={case_name}: the bug must be untouched on decline"

        # Narrated: the OVERWATCH action log records a human-readable reason.
        action_log = _action_log(fx.proj)
        assert action_log.strip(), f"case={case_name}: decline must be narrated in the OVERWATCH action log"


# ---------------------------------------------------------------------------
# AC3 — Ceiling fixture: third consecutive family failure, full history
# ---------------------------------------------------------------------------


class TestCeilingFixtureWakeDriven:
    def test_third_consecutive_family_failure_halt_hards_with_full_history(
        self, tmp_path: pathlib.Path
    ) -> None:
        project_name = "wd_ceiling"
        fx = _build_golden_fixture(
            tmp_path, project_name, bug_id="BUG-0001-foo", version="v2.0.3", attempt=3, ceiling=3
        )

        family_key = compute_family_key([f"{fx.bug_id}.md"])
        state_dir = fx.proj / "release-state"
        history_dir = state_dir / "history"
        history_dir.mkdir(parents=True, exist_ok=True)

        (history_dir / "v2.0.1-cancelled-2026-07-25T00-00-00Z.json").write_text(
            json.dumps({"rc": "v2.0.1", "attempt": 1, "outcome": "cancelled", "family_key": family_key}),
            encoding="utf-8",
        )
        (history_dir / "v2.0.2-cancelled-2026-07-26T00-00-00Z.json").write_text(
            json.dumps({"rc": "v2.0.2", "attempt": 2, "outcome": "cancelled", "family_key": family_key}),
            encoding="utf-8",
        )
        (state_dir / f"{fx.version}.json").write_text(
            json.dumps({"rc": fx.version, "attempt": 3, "outcome": "in_progress", "family_key": family_key}),
            encoding="utf-8",
        )

        # Seed two pending ledger entries for this family — the shape a real
        # installation carries after two completed cancel-and-rebundle cycles
        # that have not yet reached a terminal outcome.
        from pgai_agent_kanban.overwatch.ledger import LedgerEntry, append_entry

        for rc, attempt_from, attempt_to in (("v2.0.1", 1, 2), ("v2.0.2", 2, 3)):
            append_entry(
                str(fx.ledger_path),
                LedgerEntry(
                    timestamp=f"2026-07-2{attempt_from}T00:00:00Z",
                    rc=rc,
                    attempt_from=attempt_from,
                    attempt_to=attempt_to,
                    trigger="ship-policy-halt",
                    matrix_row="CANCEL-AND-REBUNDLE",
                    findings=[fx.bug_id],
                    actions_taken=[f"cm-cancel-rc.sh --project {project_name} --key {rc} --yes"],
                    evidence_paths=[],
                    family_key=family_key,
                ),
            )

        result = _run_wake(fx.kanban_root, "overwatch")
        assert result.returncode == 0, result.stderr

        stage_log = _resolution_stage_log(fx.kanban_root)
        assert "status=declined" in stage_log, stage_log
        assert "attempt 3/3" in stage_log, stage_log
        assert "v2.0.1" in stage_log and "v2.0.2" in stage_log
        assert "prior attempts cancelled for" in stage_log

        # No cancel/reversible action taken — every piece of the drill left
        # exactly as the fixture set it up.
        assert fx.halt_path.is_file()
        release_state_text = (fx.proj / "release-state.md").read_text(encoding="utf-8")
        assert f"## Active RC\n{fx.version}" in release_state_text
        status_text = (fx.proj / "tasks" / fx.task_id / "status.md").read_text(encoding="utf-8")
        assert "## State\nWORKING" in status_text
        bug_text = (fx.proj / "bugs" / f"{fx.bug_id}.md").read_text(encoding="utf-8")
        assert "## Status\nassigned" in bug_text

        # No NEW ledger entry; the family's two existing entries are
        # backfilled to CEILING-BLOCKED — closing that family's story.
        entries = parse_entries(str(fx.ledger_path))
        assert len(entries) == 2
        for entry in entries:
            assert entry["outcome"] == "CEILING-BLOCKED"


# ---------------------------------------------------------------------------
# AC6 (and the 5th AC2 sub-case) — Operator-HALT inviolability
# ---------------------------------------------------------------------------


class TestOperatorHaltInviolabilityWakeDriven:
    """AC6's realistic wake-driven shape.

    CM's write_halt_file() writes the ship-policy HALT at the project scope
    (projects/<name>/HALT). An operator wanting to place an ADDITIONAL halt
    "alongside" it has one other location: the fleet-wide $KANBAN_ROOT/HALT.

    A fleet-wide HALT is an operator stopping every project, and OVERWATCH
    keeps observing read-only under it: the overwatch wake exits at its
    startup HALT check, before block triage, the resolution stage or any
    task processing for any project, narrating the deferral. Nothing is
    resolved and neither HALT file is touched. (resolution_stage.py's own
    operator_candidates decline for this shape stays covered where the stage
    is called directly: test_overwatch_resolution_stage.py and
    test_overwatch_lane_a_drill.py.)
    """

    def test_operator_halt_alongside_ship_policy_halt_declines_and_touches_neither_file(
        self, tmp_path: pathlib.Path
    ) -> None:
        fx = _build_golden_fixture(tmp_path, "wd_operator_halt")

        fleet_operator_halt = fx.kanban_root / "HALT"
        _write_operator_halt(fleet_operator_halt)
        ship_halt_before = fx.halt_path.read_bytes()
        operator_halt_before = fleet_operator_halt.read_bytes()

        result = _run_wake(fx.kanban_root, "overwatch")
        assert result.returncode == 0, result.stderr

        # The wake defers at its startup HALT check, narrated on the log
        # channel (stderr), never on stdout.
        assert f"HALT flag detected at {fleet_operator_halt}" in result.stderr, result.stderr
        assert "fix deferred: HALT present" in result.stderr, result.stderr
        assert "overwatch carve-out — entering chain" not in result.stderr, result.stderr
        # The resolution stage never ran, so it narrated and logged nothing.
        assert "resolution-stage" not in _action_log(fx.proj)
        assert "operator-authored HALT" not in _resolution_stage_log(fx.kanban_root)

        # Both HALT files byte-identical to before the firing — inviolate.
        assert fx.halt_path.read_bytes() == ship_halt_before
        assert fleet_operator_halt.read_bytes() == operator_halt_before

        # Nothing else touched.
        assert not fx.ledger_path.exists()
        release_state_text = (fx.proj / "release-state.md").read_text(encoding="utf-8")
        assert f"## Active RC\n{fx.version}" in release_state_text
        status_text = (fx.proj / "tasks" / fx.task_id / "status.md").read_text(encoding="utf-8")
        assert "## State\nWORKING" in status_text
        bug_text = (fx.proj / "bugs" / f"{fx.bug_id}.md").read_text(encoding="utf-8")
        assert "## Status\nassigned" in bug_text


# ---------------------------------------------------------------------------
# AC5 — Counter keying: same cargo increments; different cargo fresh at 1
# ---------------------------------------------------------------------------


class TestCounterKeyingWakeDriven:
    def test_same_cargo_increments_and_different_cargo_starts_fresh_family(
        self, tmp_path: pathlib.Path
    ) -> None:
        # Run 1: family A, first attempt (1 -> 2).
        fx_a1 = _build_golden_fixture(
            tmp_path, "wd_counter_a1", bug_id="BUG-0001-foo", version="v2.0.1", attempt=1, ceiling=5
        )
        result_a1 = _run_wake(fx_a1.kanban_root, "overwatch")
        assert result_a1.returncode == 0, result_a1.stderr
        ledger_a1 = fx_a1.ledger_path.read_text(encoding="utf-8")
        assert "attempt 1 → 2" in ledger_a1 or "attempt 1 -> 2" in ledger_a1

        # Run 2: SAME cargo (same bug), simulating a rebundle whose attempt
        # counter has already been bumped by write_rc_state for this family
        # (attempt 2 -> 3).
        fx_a2 = _build_golden_fixture(
            tmp_path, "wd_counter_a2", bug_id="BUG-0001-foo", version="v2.0.2", attempt=2, ceiling=5
        )
        result_a2 = _run_wake(fx_a2.kanban_root, "overwatch")
        assert result_a2.returncode == 0, result_a2.stderr
        ledger_a2 = fx_a2.ledger_path.read_text(encoding="utf-8")
        assert "attempt 2 → 3" in ledger_a2 or "attempt 2 -> 3" in ledger_a2

        # Run 3: DIFFERENT cargo (a different bug) — fresh family at 1 -> 2.
        fx_b = _build_golden_fixture(
            tmp_path, "wd_counter_b", bug_id="BUG-0002-bar", version="v2.0.1", attempt=1, ceiling=5
        )
        result_b = _run_wake(fx_b.kanban_root, "overwatch")
        assert result_b.returncode == 0, result_b.stderr
        ledger_b = fx_b.ledger_path.read_text(encoding="utf-8")
        assert "attempt 1 → 2" in ledger_b or "attempt 1 -> 2" in ledger_b

        family_a = compute_family_key(["BUG-0001-foo.md"])
        family_b = compute_family_key(["BUG-0002-bar.md"])
        assert family_a != family_b

        entries_a1 = parse_entries(str(fx_a1.ledger_path))
        entries_a2 = parse_entries(str(fx_a2.ledger_path))
        entries_b = parse_entries(str(fx_b.ledger_path))
        assert entries_a1[0]["family_key"] == family_a
        assert entries_a2[0]["family_key"] == family_a
        assert entries_b[0]["family_key"] == family_b
        assert entries_a1[0]["family_key"] != entries_b[0]["family_key"]


# ---------------------------------------------------------------------------
# AC4 — Ledger atomicity: killed mid-execution, next sweep reconciles
# ---------------------------------------------------------------------------


def _write_hanging_cancel_rc_stub(kanban_root: pathlib.Path, marker_path: pathlib.Path) -> None:
    """Replace scripts/cm/cancel-rc.sh with a stub that touches *marker_path*
    (proving execute_drill's first action — the cancel-rc.sh invocation —
    has started, which can only happen AFTER append_entry() has already
    returned per resolution_stage.py's decide-and-log ordering) and then
    blocks indefinitely. The test polls for the marker rather than guessing
    a sleep duration, so the kill point is deterministic regardless of host
    speed."""
    stub = kanban_root / "scripts" / "cm" / "cancel-rc.sh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"touch {marker_path}\n"
        "sleep 3600\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)


class TestLedgerAtomicityWakeDriven:
    def test_kill_mid_execution_leaves_recorded_entry_and_next_sweep_reconciles(
        self, tmp_path: pathlib.Path
    ) -> None:
        fx = _build_golden_fixture(tmp_path, "wd_atomicity")
        marker_path = tmp_path / "drill_started.marker"
        _write_hanging_cancel_rc_stub(fx.kanban_root, marker_path)

        # Invoke the exact CLI contract wake_common.sh's
        # run_overwatch_resolution_stage() shells out to (pp_run_ops
        # pgai_agent_kanban.overwatch.resolution_stage --project ...
        # --kanban-root ...) — the production module entry point for this
        # process, launched in its own session so it can be killed cleanly.
        env = dict(os.environ)
        env["PYTHONPATH"] = str(fx.kanban_root) + (
            os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
        )
        proc = subprocess.Popen(
            [
                "python3", "-m", "pgai_agent_kanban.overwatch.resolution_stage",
                "--project", fx.proj.name, "--kanban-root", str(fx.kanban_root),
            ],
            env=env,
            cwd=str(fx.kanban_root),
            start_new_session=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 15
            while not marker_path.exists():
                assert time.monotonic() < deadline, (
                    "cancel-rc.sh stub never started — the resolution stage "
                    "did not reach execute_drill() within the timeout"
                )
                time.sleep(0.05)
            # The marker exists only after append_entry() already returned
            # (execute_drill runs strictly after it in run_resolution_stage),
            # so killing here always lands after the ledger write.
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        finally:
            proc.wait(timeout=10)

        # Invariant after the kill: the ledger entry is present, with its
        # actions already recorded — but the real drill never ran (the stub
        # never cleared the HALT, reset the bug, or deleted the branch).
        assert fx.ledger_path.is_file(), "killed run must still have written its ledger entry (decide-and-log)"
        entries_after_kill = parse_entries(str(fx.ledger_path))
        assert len(entries_after_kill) == 1
        assert entries_after_kill[0]["actions_taken"], "the killed entry must already list its intended actions"
        assert fx.halt_path.is_file(), "a killed drill must not have cleared the HALT"
        bug_text = (fx.proj / "bugs" / f"{fx.bug_id}.md").read_text(encoding="utf-8")
        assert "## Status\nassigned" in bug_text, "a killed drill must not have reset the bug"

        # Next sweep: restore the REAL cancel-rc.sh and fire the production
        # wake entry point again. The ship-policy HALT and Active RC are
        # still exactly as they were (the killed run changed nothing but the
        # ledger), so the same row matches again and the drill completes.
        real_cancel_rc = (_TEAM_DIR / "scripts" / "cm" / "cancel-rc.sh").read_bytes()
        (fx.kanban_root / "scripts" / "cm" / "cancel-rc.sh").write_bytes(real_cancel_rc)
        (fx.kanban_root / "scripts" / "cm" / "cancel-rc.sh").chmod(0o755)

        result = _run_wake(fx.kanban_root, "overwatch")
        assert result.returncode == 0, result.stderr

        stage_log = _resolution_stage_log(fx.kanban_root)
        assert "status=executed" in stage_log, stage_log

        # Reconciled: the real state now matches a completed drill, even
        # though the ledger carries the earlier killed attempt's orphaned
        # entry alongside the completed one.
        assert not fx.halt_path.exists()
        release_state_text = (fx.proj / "release-state.md").read_text(encoding="utf-8")
        assert "## Active RC\nnone" in release_state_text
        bug_text = (fx.proj / "bugs" / f"{fx.bug_id}.md").read_text(encoding="utf-8")
        assert "## Status\nopen" in bug_text
        entries_after_sweep = parse_entries(str(fx.ledger_path))
        assert len(entries_after_sweep) == 2
