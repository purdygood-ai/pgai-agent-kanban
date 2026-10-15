"""
test_overwatch_lane_b_ship.py
================================
Integration tests for the OVERWATCH Lane B force-ship playbook —
pgai_agent_kanban.overwatch.resolution_stage._run_lane_b() /
_run_cm_release_force(), reached from run_resolution_stage() when the
three-lane classifier (pgai_agent_kanban.overwatch.resolution_matrix.
classify_lane()) returns LANE_B — and for cm/release.sh's own narrow Lane B
admission carved out of its pre-existing operator-force-wake-context
refusal.

Fixture pattern: merges test_overwatch_lane_a_drill.py's kanban-root builder
(copies team/scripts/ and pgai_agent_kanban/cm/*.py wholesale so
resolution_stage.py's subprocess calls resolve against the fixture root
exactly as they do in a real install) with test_cm_release_operator_force_
override.py's real-git-repo-plus-bare-remote-plus-open-rc.sh technique (Lane
B's playbook, unlike Lane A's, actually invokes cm/release.sh and must ship
a real release end to end). Also copies pgai_agent_kanban/overwatch/
intent.py — release.sh's own new Lane B admission check
(_cm_release_lane_b_intent_verified) imports
pgai_agent_kanban.overwatch.intent, which the pre-existing Lane A fixture
never needed to copy.

Coverage:
  - Golden path: a ship-policy HALT (TESTER BLOCKED, trigger-1) whose
    evidence is fully explained by a live INTENT.md entry classifies Lane
    B, force-ships the RC through the REAL cm/release.sh subprocess (not a
    mock), and release-state.md afterwards carries both an "## Operator
    Override" event (reason == the INTENT.md entry's text, verbatim) and
    an "## OVERWATCH Decision" event (Lane: B). The ship-policy HALT file
    is cleared and the release tag exists.
  - The identical HALT with no matching INTENT.md entry does not ship —
    classifies Lane C instead, leaves the HALT file in place, and no tag
    is created.
  - With overwatch_resolve=false, the matched-entry fixture ships nothing
    and leaves release-state.md byte-identical.
  - cm/release.sh's own dual-channel refusal: a --force --reason "..."
    invocation under PGAI_AGENT_WAKE_ACTIVE=1 that is NOT a matched Lane B
    path (no --overwatch-lane-b-ship flag, or the flag present but the
    reason does not match any live INTENT.md entry) still refuses, with
    the message and remedy intact, and touches nothing.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
from typing import Optional

import pytest

from pgai_agent_kanban.overwatch.resolution_stage import (
    DECLINED,
    EXECUTED,
    run_resolution_stage,
)
from tests.fixtures.binary_guard import require_binary


@pytest.fixture(autouse=True)
def _require_git() -> None:
    require_binary("git", label="overwatch-lane-b-ship")


_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_RELEASE_SCRIPT_REL = pathlib.Path("scripts") / "cm" / "release.sh"
_OPEN_RC_SCRIPT_REL = pathlib.Path("scripts") / "cm" / "open-rc.sh"


# ---------------------------------------------------------------------------
# Fixture builders (adapted from test_overwatch_lane_a_drill.py's and
# test_cm_release_operator_force_override.py's own helpers; duplicated
# rather than imported cross-module per this test tree's established
# convention of one self-contained fixture set per file)
# ---------------------------------------------------------------------------


def _init_git_repo_with_remote(repo_path: pathlib.Path, bare_remote: pathlib.Path) -> None:
    bare_remote.mkdir(parents=True, exist_ok=True)
    repo_path.mkdir(parents=True, exist_ok=True)

    subprocess.run(["git", "init", "--bare", str(bare_remote)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(repo_path), "init", "--initial-branch=main"],
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "config", "--local", "user.email", "test@pgai.local"],
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "config", "--local", "user.name", "PGAI Test"],
        capture_output=True, check=True,
    )

    (repo_path / "README.md").write_text("# Lane B ship test repo\n", encoding="utf-8")
    (repo_path / "release-notes").mkdir(parents=True, exist_ok=True)
    (repo_path / "release-notes" / "PUBLISHED").write_text("", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo_path), "add", "README.md", "release-notes/PUBLISHED"],
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "commit", "-m", "Initial commit"],
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "remote", "add", "origin", str(bare_remote)],
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "push", "origin", "main"],
        capture_output=True, check=True,
    )


def _build_kanban_root(
    tmp_path: pathlib.Path, project_name: str, repo_path: pathlib.Path, overwatch_resolve: bool
) -> pathlib.Path:
    """Live-install-shaped fixture root: team/scripts/ copied wholesale (so
    resolution_stage.py's `kanban_root/scripts/cm/release.sh` subprocess
    path resolves) plus the pgai_agent_kanban.cm and pgai_agent_kanban.
    overwatch packages copied in (the latter for release.sh's own new
    Lane B admission check, which imports pgai_agent_kanban.overwatch.
    intent — see this module's own docstring).
    """
    root = tmp_path / "kanban_root"
    root.mkdir(parents=True, exist_ok=True)

    (root / "kanban.cfg").write_text(
        "[paths]\n\n[chain]\npm_mode = automatic\n\n"
        "[wake]\nmax_tasks_per_wake = 1\nmax_runtime_seconds = 600\n\n"
        "[release]\nmax_rc_retries = 3\n",
        encoding="utf-8",
    )
    (root / "projects.cfg").write_text(
        f"[project:{project_name}]\npriority=1\ndescription=Lane B ship test project\nenabled=true\n",
        encoding="utf-8",
    )

    proj = root / "projects" / project_name
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "project.cfg").write_text(
        f"[project]\nproject_name = {project_name}\ndev_tree_path = {repo_path}\n"
        f"git_repo_url = git@example.com:{project_name}.git\ngit_remote_name = origin\n"
        "workflow_type = release\nis_self_build = false\npush_to_remote = false\n\n"
        "[versioning]\nmax_patch = 99\nmax_minor = 9\nmax_major = 0\n\n"
        f"[release]\nmax_rc_retries = 3\noverwatch_resolve = {'true' if overwatch_resolve else 'false'}\n",
        encoding="utf-8",
    )
    (proj / "release-state.md").write_text(
        "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n## RC Opened By Task\nnone\n",
        encoding="utf-8",
    )
    (proj / "release-state").mkdir(parents=True, exist_ok=True)
    (proj / "requirements").mkdir(parents=True, exist_ok=True)
    (proj / "bugs").mkdir(parents=True, exist_ok=True)
    (proj / "priority").mkdir(parents=True, exist_ok=True)
    (proj / "tasks" / "queues").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "locks").mkdir(parents=True, exist_ok=True)

    # The copied scripts/ tree below self-locates its own-tree-root as
    # `root` (pp_run_ops.sh walks up from scripts/lib/), so every
    # `pp_run_ops`/`pp_pythonpath`-driven python call cm/release.sh (and
    # its sourced lib stages — e.g. refusal_ledger.sh's best-effort
    # refusal-ledger write) makes needs the WHOLE pgai_agent_kanban
    # package here, not just the cm/ and overwatch/ submodules this
    # fixture's own test code happens to touch directly. Copying only a
    # subset left refusal_ledger.sh's `pp_run_ops
    # pgai_agent_kanban.lib.refusal_ledger` call unable to import, which —
    # despite that call site's "best effort" naming — still tripped
    # `set -e` and killed cm_halt() before it reached write_halt_file(),
    # so no HALT file was ever written under a partial copy.
    shutil.copytree(_TEAM_DIR / "pgai_agent_kanban", root / "pgai_agent_kanban", ignore=shutil.ignore_patterns("__pycache__"))

    shutil.copytree(_TEAM_DIR / "scripts", root / "scripts")
    # config/default_palette.txt is a scripts/-sibling dependency (colorized
    # dual-channel output resolves it via scripts/lib/../../config/) that a
    # real install always carries alongside scripts/ — copy it too so the
    # fixture root looks like a real install rather than a partial one.
    shutil.copytree(_TEAM_DIR / "config", root / "config")

    return root


def _run_cm_script(
    script_rel: pathlib.Path, args: list, kanban_root: pathlib.Path, project_name: str,
    *, repo_root: Optional[pathlib.Path] = None, extra_env: Optional[dict] = None, timeout: int = 90,
) -> subprocess.CompletedProcess:
    base_env = dict(os.environ)
    base_env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    base_env["KANBAN_ROOT"] = str(kanban_root)
    base_env["TEAM_ROOT"] = str(kanban_root)
    base_env["PGAI_PROJECT_NAME"] = project_name
    if repo_root is not None:
        base_env["REPO_ROOT"] = str(repo_root)
    base_env.pop("PGAI_DEV_TREE_PATH", None)
    if extra_env:
        base_env.update(extra_env)
    else:
        base_env.pop("PGAI_AGENT_WAKE_ACTIVE", None)
    script_path = kanban_root / script_rel
    return subprocess.run(
        ["bash", str(script_path)] + args,
        capture_output=True, text=True, env=base_env, cwd=str(kanban_root), timeout=timeout,
    )


def _git_local(repo_path: pathlib.Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path)] + list(args), capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _git_tag_exists(repo_path: pathlib.Path, tag: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--verify", f"refs/tags/{tag}"],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def _commit_on_branch(
    repo_path: pathlib.Path, branch: str, filename: str, content: str, message: str,
    return_branch: Optional[str] = None,
) -> None:
    _git_local(repo_path, "checkout", branch)
    (repo_path / filename).write_text(content, encoding="utf-8")
    _git_local(repo_path, "add", filename)
    _git_local(repo_path, "commit", "-m", message)
    if return_branch is not None:
        _git_local(repo_path, "checkout", return_branch)


def _build_tester_task(
    kanban_root: pathlib.Path, project_name: str, task_id: str, release_version: str,
) -> pathlib.Path:
    """A BLOCKED-state TESTER task: cm/release.sh's own ship-policy stage
    (trigger-1) HALTs on this shape (test_cm_release_operator_force_override.
    py's own proven fixture shape). Deliberately BLOCKED rather than DONE:
    resolution_stage.py's find_tester_report() only considers a DONE TESTER
    task "found", so this task contributes no findings to classify_lane() —
    Lane A's regression check fails closed (no findings), leaving Lane B's
    INTENT.md match the only positive path, exactly what these tests need.
    """
    task_dir = kanban_root / "projects" / project_name / "tasks" / task_id
    (task_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (task_dir / "README.md").write_text(
        "# Task: Synthetic Lane B ship verification\n\n"
        f"## Task ID\n{task_id}\n\n## Role\nTESTER\n\n"
        f"## Release Version\n{release_version}\n",
        encoding="utf-8",
    )
    (task_dir / "status.md").write_text(
        f"# Status\n\n## Task\n{task_id}\n\n## Role\nTESTER\n\n## State\nBLOCKED\n",
        encoding="utf-8",
    )
    (task_dir / "artifacts" / "report.md").write_text(
        f"# Verification Report: {release_version}\n\n"
        "## Recommendation\nPASS\n\n## Systemic Risk\nlow\n\n"
        "## Findings\n\nNo bug, gap, or stale-assertion findings — suites green.\n\n"
        "## Lint Findings (non-blocking)\n\nNone.\n",
        encoding="utf-8",
    )
    return task_dir


def _write_intent_md(proj: pathlib.Path, declaration: Optional[str]) -> None:
    if declaration is None:
        (proj / "INTENT.md").write_text(
            "# INTENT\n\n## Entries\n\n<!-- no live entries -->\n", encoding="utf-8"
        )
        return
    (proj / "INTENT.md").write_text(
        f"# INTENT\n\n## Entries\n- 2026-09-10: {declaration}\n", encoding="utf-8"
    )


def _release_state_text(kanban_root: pathlib.Path, project_name: str) -> str:
    return (kanban_root / "projects" / project_name / "release-state.md").read_text(encoding="utf-8")


def _build_lane_b_fixture(tmp_path: pathlib.Path, project_name: str, version: str, overwatch_resolve: bool):
    """Build a complete Lane B fixture: open RC, a direct (non-merge) commit
    on the RC branch (makes operator_commits_in_lineage extract True, so
    the block fails the shipped CANCEL-AND-REBUNDLE matrix row before ever
    reaching classify_lane() — same technique test_overwatch_lane_a_drill.
    py's own module docstring documents), a BLOCKED TESTER task (trigger-1),
    and an unforced cm/release.sh run that produces a real ship-policy
    HALT exactly as CM's own chain would.

    Returns (kanban_root, proj, repo_path, halt_path, rc_branch).
    """
    repo_path = tmp_path / "dev_repo"
    bare_remote = tmp_path / "bare_remote.git"
    _init_git_repo_with_remote(repo_path, bare_remote)
    kanban_root = _build_kanban_root(tmp_path, project_name, repo_path, overwatch_resolve)
    proj = kanban_root / "projects" / project_name

    result_open = _run_cm_script(_OPEN_RC_SCRIPT_REL, [version], kanban_root, project_name, repo_root=repo_path)
    assert result_open.returncode == 0, (
        f"open-rc.sh failed.\nstdout: {result_open.stdout}\nstderr: {result_open.stderr}"
    )
    rc_branch = f"rc/{version}"
    _commit_on_branch(
        repo_path, rc_branch, "feature_lane_b.txt", "lane b ship test feature content\n",
        "feat: add feature for lane b ship test", return_branch="main",
    )

    tester_task_id = f"TESTER-20260910-{version[-1]}50-lane-b-blocked"
    _build_tester_task(kanban_root, project_name, tester_task_id, version)

    result_first = _run_cm_script(_RELEASE_SCRIPT_REL, [], kanban_root, project_name, repo_root=repo_path)
    assert result_first.returncode != 0, (
        f"cm/release.sh (unforced) was expected to HALT on trigger-1.\n"
        f"stdout: {result_first.stdout}\nstderr: {result_first.stderr}"
    )
    combined_first = result_first.stdout + result_first.stderr
    assert "cm-release.trigger-1.tester-blocked" in combined_first, (
        f"Expected the unforced run to HALT on trigger-1.\noutput:\n{combined_first}"
    )
    halt_path = proj / "HALT"
    assert halt_path.is_file(), "Expected a project-scoped ship-policy HALT file after the unforced run."

    return kanban_root, proj, repo_path, halt_path, rc_branch


# ---------------------------------------------------------------------------
# Golden path: matched INTENT.md entry -> Lane B force-ships
# ---------------------------------------------------------------------------


def test_lane_b_matched_intent_force_ships_and_writes_both_records(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_name = "laneb_project"
    version = "v2.23.5"

    kanban_root, proj, repo_path, halt_path, rc_branch = _build_lane_b_fixture(
        tmp_path, project_name, version, overwatch_resolve=True
    )

    declaration = (
        f"Operator declares: {rc_branch} is being force-shipped despite the known "
        "TESTER block; this is expected and authorised."
    )
    _write_intent_md(proj, declaration)

    # run_resolution_stage() spawns cm/release.sh as a real subprocess that
    # inherits os.environ — set the wake-context signal here exactly as an
    # OVERWATCH wake would carry it into this process.
    monkeypatch.setenv("PGAI_AGENT_WAKE_ACTIVE", "1")

    outcome = run_resolution_stage(project_name, str(kanban_root))

    assert outcome.status == EXECUTED, outcome.reason
    assert not outcome.action_errors, outcome.action_errors

    release_state_text = _release_state_text(kanban_root, project_name)
    assert "## Operator Override" in release_state_text, (
        f"Expected an '## Operator Override' event.\n{release_state_text}"
    )
    assert declaration in release_state_text, (
        f"Expected the INTENT.md entry's text verbatim as the Override reason.\n{release_state_text}"
    )
    assert "## OVERWATCH Decision" in release_state_text, (
        f"Expected an '## OVERWATCH Decision' event.\n{release_state_text}"
    )
    assert "Lane: B" in release_state_text

    assert not halt_path.exists(), "Expected the ship-policy HALT to be cleared after a successful Lane B ship."
    assert _git_tag_exists(repo_path, version), f"Expected release tag '{version}' after a Lane B ship."


# ---------------------------------------------------------------------------
# No matching entry -> Lane C, nothing ships
# ---------------------------------------------------------------------------


def test_lane_b_unmatched_intent_classifies_lane_c_and_ships_nothing(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_name = "laneb_unmatched_project"
    version = "v2.23.6"

    kanban_root, proj, repo_path, halt_path, rc_branch = _build_lane_b_fixture(
        tmp_path, project_name, version, overwatch_resolve=True
    )
    # An INTENT.md entry that does NOT name this block's identifiers
    # (a different RC branch entirely) — no full explanation, no match.
    _write_intent_md(proj, "Unrelated deliberate state: rc/v0.0.1 rebase, nothing to do with this block.")

    monkeypatch.setenv("PGAI_AGENT_WAKE_ACTIVE", "1")

    outcome = run_resolution_stage(project_name, str(kanban_root))

    assert outcome.status == DECLINED, outcome.reason

    release_state_text = _release_state_text(kanban_root, project_name)
    assert "## OVERWATCH Decision" in release_state_text
    assert "Lane: C" in release_state_text
    assert "## Operator Override" not in release_state_text, (
        "No Operator Override event should exist — nothing was force-shipped."
    )

    assert halt_path.exists(), "The ship-policy HALT must remain — Lane C never clears it."
    assert not _git_tag_exists(repo_path, version), f"Release tag '{version}' must NOT exist for a Lane C block."


# ---------------------------------------------------------------------------
# overwatch_resolve=false: matched fixture ships nothing, release-state.md
# is byte-identical before and after.
# ---------------------------------------------------------------------------


def test_lane_b_matched_intent_ships_nothing_when_overwatch_resolve_false(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_name = "laneb_resolve_off_project"
    version = "v2.23.7"

    kanban_root, proj, repo_path, halt_path, rc_branch = _build_lane_b_fixture(
        tmp_path, project_name, version, overwatch_resolve=False
    )
    declaration = f"Operator declares: {rc_branch} is being force-shipped; expected and authorised."
    _write_intent_md(proj, declaration)

    release_state_before = _release_state_text(kanban_root, project_name)

    monkeypatch.setenv("PGAI_AGENT_WAKE_ACTIVE", "1")

    outcome = run_resolution_stage(project_name, str(kanban_root))

    assert outcome.status == DECLINED, outcome.reason
    release_state_after = _release_state_text(kanban_root, project_name)
    assert release_state_after == release_state_before, (
        "release-state.md must be byte-identical when overwatch_resolve is false — "
        "no lane classification, no Decision event, nothing shipped."
    )
    assert halt_path.exists()
    assert not _git_tag_exists(repo_path, version)


# ---------------------------------------------------------------------------
# cm/release.sh's own refusal: a --force invocation under
# PGAI_AGENT_WAKE_ACTIVE=1 that is not a matched Lane B path still refuses
# dual-channel, message and remedy intact, and touches nothing.
# ---------------------------------------------------------------------------


def test_release_sh_refuses_force_under_wake_context_without_lane_b_flag(
    tmp_path: pathlib.Path,
) -> None:
    project_name = "laneb_bare_force_project"
    version = "v2.23.8"

    kanban_root, proj, repo_path, halt_path, rc_branch = _build_lane_b_fixture(
        tmp_path, project_name, version, overwatch_resolve=True
    )
    declaration = f"Operator declares: {rc_branch} is being force-shipped; expected and authorised."
    _write_intent_md(proj, declaration)

    release_state_before = _release_state_text(kanban_root, project_name)

    result = _run_cm_script(
        _RELEASE_SCRIPT_REL, ["--force", "--reason", declaration],
        kanban_root, project_name, repo_root=repo_path,
        extra_env={"PGAI_AGENT_WAKE_ACTIVE": "1"},
    )
    assert result.returncode != 0, (
        f"A bare --force under wake context (no --overwatch-lane-b-ship) must still refuse.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "cm-release.operator-force.agent-wake-refused" in combined, (
        f"Expected the surviving agent-wake-refused error key.\noutput:\n{combined}"
    )
    assert "remedy" in combined.lower() or "From an interactive operator shell" in combined, (
        f"Expected a runnable remedy in the refusal output.\noutput:\n{combined}"
    )
    assert _release_state_text(kanban_root, project_name) == release_state_before, (
        "release-state.md must be byte-identical after a refused --force invocation."
    )
    assert halt_path.exists()
    assert not _git_tag_exists(repo_path, version)


def test_release_sh_refuses_lane_b_flag_with_unmatched_reason(tmp_path: pathlib.Path) -> None:
    """--overwatch-lane-b-ship is present, but --reason does not verbatim-
    match any live INTENT.md entry -- refuses through the NEW, separately
    anchored overwatch-lane-b-intent-unmatched-01 site, dual-channel,
    touching nothing. Proves the flag alone is not sufficient admission."""
    project_name = "laneb_flag_no_match_project"
    version = "v2.23.9"

    kanban_root, proj, repo_path, halt_path, rc_branch = _build_lane_b_fixture(
        tmp_path, project_name, version, overwatch_resolve=True
    )
    # A live entry exists, but it does not name this block's identifiers.
    _write_intent_md(proj, "Unrelated deliberate state: rc/v0.0.1 rebase.")

    release_state_before = _release_state_text(kanban_root, project_name)

    result = _run_cm_script(
        _RELEASE_SCRIPT_REL,
        ["--force", "--reason", "a reason that matches nothing in INTENT.md", "--overwatch-lane-b-ship"],
        kanban_root, project_name, repo_root=repo_path,
        extra_env={"PGAI_AGENT_WAKE_ACTIVE": "1"},
    )
    assert result.returncode != 0, (
        f"--overwatch-lane-b-ship with an unmatched --reason must refuse.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "cm-release.overwatch-lane-b.intent-not-matched" in combined, (
        f"Expected the new overwatch-lane-b intent-not-matched error key.\noutput:\n{combined}"
    )
    assert _release_state_text(kanban_root, project_name) == release_state_before, (
        "release-state.md must be byte-identical after a refused Lane B invocation."
    )
    assert halt_path.exists()
    assert not _git_tag_exists(repo_path, version)


def test_release_sh_admits_lane_b_flag_with_matched_reason_directly(tmp_path: pathlib.Path) -> None:
    """Script-level proof (independent of resolution_stage.py) that the
    narrow admission works: --force --reason "<verbatim INTENT.md entry>"
    --overwatch-lane-b-ship under wake context ships, where the identical
    invocation without the flag (proven above) refuses."""
    project_name = "laneb_direct_admit_project"
    version = "v2.23.10"

    kanban_root, proj, repo_path, halt_path, rc_branch = _build_lane_b_fixture(
        tmp_path, project_name, version, overwatch_resolve=True
    )
    declaration = f"Operator declares: {rc_branch} is being force-shipped; expected and authorised."
    _write_intent_md(proj, declaration)

    result = _run_cm_script(
        _RELEASE_SCRIPT_REL,
        ["--force", "--reason", declaration, "--overwatch-lane-b-ship"],
        kanban_root, project_name, repo_root=repo_path,
        extra_env={"PGAI_AGENT_WAKE_ACTIVE": "1"},
    )
    assert result.returncode == 0, (
        f"--overwatch-lane-b-ship with a matched --reason must ship under wake context.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    release_state_text = _release_state_text(kanban_root, project_name)
    assert "## Operator Override" in release_state_text
    assert declaration in release_state_text
    assert _git_tag_exists(repo_path, version)
