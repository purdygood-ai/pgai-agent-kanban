"""
test_cm_release_operator_force_override.py
============================================
End-to-end integration tests for cm-release.sh's operator force-override:
--force --reason "<text>", scoped to exactly the ship-policy matrix's own
judgement-call HALTs (trigger-1/2/3), refused outright without a reason or
inside an agent wake, and never reaching any mechanical integrity gate
(squash conflicts, the release-notes stamp guard, and everything else
outside its enumerated scope).

Fixture pattern (git repo + kanban root + TESTER task folder) mirrors
test_cm_release_synthetic.py's established helpers, trimmed to what this
module's scenarios need — each integration module in this tree builds its
own fixture surface rather than importing another test module's private
helpers (see that module's own docstring).

Coverage:
  - --force with no --reason (or a whitespace-only one) is refused
    dual-channel before any project/repo state is touched.
  - --force --reason "..." is refused dual-channel when the agent
    wake-context signal (PGAI_AGENT_WAKE_ACTIVE=1) is set.
  - --force --reason "..." passes a matrix stop (TESTER BLOCKED, trigger-1),
    ships the release, and leaves both permanent records: an Operator
    Override event in release-state.md, and a release-notes ## Status of
    SHIPPED-BY-OPERATOR-OVERRIDE.
  - --force does NOT reach a squash conflict (trigger-5) or a malformed
    release-notes ## Status heading (trigger-9) — both still refuse exactly
    as they do without the flag.
  - The release immediately following a forced one applies the matrix
    normally with no flag present (single-use: nothing --force writes
    persists as a mode for a later invocation).
"""

from __future__ import annotations

import os
import pathlib
import subprocess
from typing import Optional
import pytest
from tests.fixtures.binary_guard import require_binary


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Every test here builds, inspects, or drives a real git repository; a
    module-wide autouse guard keeps that requirement in one place instead
    of repeating a per-test check at each git-dependent call site.
    """
    require_binary("git", label="cm-release-operator-force-override")

# ---------------------------------------------------------------------------
# Path helpers — mirrors test_cm_release_synthetic.py's own resolution.
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_CM_SCRIPTS_DIR = _TEAM_DIR / "scripts" / "cm"
_OPEN_RC_SCRIPT = _CM_SCRIPTS_DIR / "open-rc.sh"
_RELEASE_SCRIPT = _CM_SCRIPTS_DIR / "release.sh"


# ---------------------------------------------------------------------------
# Fixture helpers (trimmed copies of test_cm_release_synthetic.py's own).
# ---------------------------------------------------------------------------


def _init_git_repo_with_remote(
    repo_path: pathlib.Path, bare_remote: pathlib.Path, *, main_branch: str = "main"
) -> None:
    bare_remote.mkdir(parents=True, exist_ok=True)
    repo_path.mkdir(parents=True, exist_ok=True)

    subprocess.run(["git", "init", "--bare", str(bare_remote)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(repo_path), "init", f"--initial-branch={main_branch}"],
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

    (repo_path / "README.md").write_text("# Synthetic release test repo\n", encoding="utf-8")
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
        ["git", "-C", str(repo_path), "push", "origin", main_branch],
        capture_output=True, check=True,
    )


def _build_kanban_root(
    parent: pathlib.Path, project_name: str, repo_path: pathlib.Path, *, push_to_remote: str = "false"
) -> pathlib.Path:
    root = parent / "kanban_root"
    root.mkdir(parents=True, exist_ok=True)

    (root / "kanban.cfg").write_text(
        "[paths]\n\n[chain]\npm_mode = automatic\n\n"
        "[wake]\nmax_tasks_per_wake = 1\nmax_runtime_seconds = 600\n",
        encoding="utf-8",
    )
    (root / "projects.cfg").write_text(
        f"[project:{project_name}]\npriority=1\ndescription=Operator force-override test project\nenabled=true\n",
        encoding="utf-8",
    )

    proj = root / "projects" / project_name
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "project.cfg").write_text(
        f"[project]\nproject_name = {project_name}\ndev_tree_path = {repo_path}\n"
        f"git_repo_url = git@example.com:{project_name}.git\ngit_remote_name = origin\n"
        f"workflow_type = release\nis_self_build = false\npush_to_remote = {push_to_remote}\n\n"
        f"[versioning]\nmax_patch = 99\nmax_minor = 9\nmax_major = 0\n",
        encoding="utf-8",
    )
    (proj / "release-state.md").write_text(
        "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n## RC Opened By Task\nnone\n",
        encoding="utf-8",
    )
    (proj / "release-state").mkdir(parents=True, exist_ok=True)
    (proj / "tasks" / "queues").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "locks").mkdir(parents=True, exist_ok=True)

    cm_py_dir = root / "pgai_agent_kanban" / "cm"
    cm_py_dir.mkdir(parents=True, exist_ok=True)
    src_cm_dir = _TEAM_DIR / "pgai_agent_kanban" / "cm"
    for py_file in src_cm_dir.glob("*.py"):
        (cm_py_dir / py_file.name).write_text(py_file.read_text(encoding="utf-8"), encoding="utf-8")

    return root


def _run_cm_script(
    script_path: pathlib.Path, args: list[str], kanban_root: pathlib.Path, project_name: str,
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
    # This harness never runs under a real agent wake, but strip the signal
    # defensively so a test's assertions about its ABSENCE can never be
    # invalidated by an inherited value from the invoking shell.
    base_env.pop("PGAI_AGENT_WAKE_ACTIVE", None)
    if extra_env:
        base_env.update(extra_env)
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
    *, state: str = "DONE", recommendation: str = "PASS", systemic_risk: str = "low",
) -> pathlib.Path:
    """Synthetic TESTER task folder in the shape cm-release.sh's Step 4d
    discovers. Mirrors test_cm_release_synthetic.py's own _build_tester_task."""
    task_dir = kanban_root / "projects" / project_name / "tasks" / task_id
    (task_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (task_dir / "README.md").write_text(
        "# Task: Synthetic operator-force-override verification\n\n"
        f"## Task ID\n{task_id}\n\n## Role\nTESTER\n\n"
        f"## Release Version\n{release_version}\n",
        encoding="utf-8",
    )
    (task_dir / "status.md").write_text(
        f"# Status\n\n## Task\n{task_id}\n\n## Role\nTESTER\n\n## State\n{state}\n",
        encoding="utf-8",
    )
    (task_dir / "artifacts" / "report.md").write_text(
        f"# Verification Report: {release_version}\n\n"
        f"## Recommendation\n{recommendation}\n\n"
        f"## Systemic Risk\n{systemic_risk}\n\n"
        "## Findings\n\nNo bug, gap, or stale-assertion findings — suites green.\n\n"
        "## Lint Findings (non-blocking)\n\nNone.\n",
        encoding="utf-8",
    )
    return task_dir


def _release_state_text(kanban_root: pathlib.Path, project_name: str) -> str:
    return (kanban_root / "projects" / project_name / "release-state.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# --force argument-validation refusals (no RC needs to exist for either —
# both refusals fire before any project/repo state is touched).
# ---------------------------------------------------------------------------


def test_force_without_reason_is_refused_and_ships_nothing(tmp_path: pathlib.Path) -> None:
    """--force with no --reason refuses dual-channel and touches nothing.

    Asserts:
      - cm-release.sh exits non-zero.
      - stderr carries the reason-required error key.
      - release-state.md is byte-identical to before the invocation.
      - No project HALT file was created.
    """
    project_name = "force_no_reason_test"
    bare_remote = tmp_path / "bare_remote.git"
    repo_path = tmp_path / "dev_repo"
    _init_git_repo_with_remote(repo_path, bare_remote)
    kanban_root = _build_kanban_root(tmp_path, project_name, repo_path, push_to_remote="false")

    release_state_path = kanban_root / "projects" / project_name / "release-state.md"
    release_state_before = release_state_path.read_text(encoding="utf-8")

    result = _run_cm_script(
        _RELEASE_SCRIPT, ["--force"], kanban_root, project_name, repo_root=repo_path,
    )
    assert result.returncode != 0, (
        f"cm-release.sh --force with no --reason must exit non-zero.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "cm-release.operator-force.reason-required" in combined, (
        f"Expected the reason-required error key in output.\noutput:\n{combined}"
    )
    assert release_state_path.read_text(encoding="utf-8") == release_state_before, (
        "release-state.md must be byte-identical after a refused --force invocation."
    )
    assert not (kanban_root / "projects" / project_name / "HALT").exists(), (
        "A refused --force invocation must never create a project HALT file."
    )


def test_force_with_reason_refused_in_wake_context(tmp_path: pathlib.Path) -> None:
    """--force --reason "..." refuses when the wake-context signal is set.

    Asserts:
      - cm-release.sh exits non-zero when PGAI_AGENT_WAKE_ACTIVE=1 is set,
        even though a non-empty reason was supplied.
      - stderr carries the agent-wake-refused error key.
      - release-state.md is byte-identical to before the invocation.
    """
    project_name = "force_wake_context_test"
    bare_remote = tmp_path / "bare_remote.git"
    repo_path = tmp_path / "dev_repo"
    _init_git_repo_with_remote(repo_path, bare_remote)
    kanban_root = _build_kanban_root(tmp_path, project_name, repo_path, push_to_remote="false")

    release_state_path = kanban_root / "projects" / project_name / "release-state.md"
    release_state_before = release_state_path.read_text(encoding="utf-8")

    result = _run_cm_script(
        _RELEASE_SCRIPT, ["--force", "--reason", "operator says ship it anyway"],
        kanban_root, project_name, repo_root=repo_path,
        extra_env={"PGAI_AGENT_WAKE_ACTIVE": "1"},
    )
    assert result.returncode != 0, (
        f"cm-release.sh --force must refuse under PGAI_AGENT_WAKE_ACTIVE=1 even with a reason.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "cm-release.operator-force.agent-wake-refused" in combined, (
        f"Expected the agent-wake-refused error key in output.\noutput:\n{combined}"
    )
    assert release_state_path.read_text(encoding="utf-8") == release_state_before, (
        "release-state.md must be byte-identical after a refused --force invocation."
    )


# ---------------------------------------------------------------------------
# A forced ship over a matrix stop (trigger-1: TESTER BLOCKED) writes both
# permanent records and single-use is proven by the immediately-following
# unforced release applying the matrix normally.
# ---------------------------------------------------------------------------


def test_forced_ship_over_tester_blocked_writes_both_records_and_next_release_is_unforced(
    tmp_path: pathlib.Path,
) -> None:
    """--force passes trigger-1 (TESTER BLOCKED), ships, and writes both records;
    the very next release (no flag) applies the matrix normally.

    Asserts (first release, forced):
      - cm-release.sh exits 0 despite a BLOCKED TESTER task.
      - release-state.md carries an "## Operator Override" event naming the
        reason and "cm-release.trigger-1.tester-blocked".
      - release-notes/<version>.md carries "## Status" SHIPPED-BY-OPERATOR-OVERRIDE.
      - The release tag exists (it actually shipped).

    Asserts (second release, no flag — single-use proof):
      - A second RC with its own BLOCKED TESTER task HALTs normally (Trigger 1
        fires, non-zero exit) — nothing the first --force wrote persisted as
        a mode.
    """
    version = "v0.30.0"
    project_name = "forced_ship_single_use_test"

    bare_remote = tmp_path / "bare_remote.git"
    repo_path = tmp_path / "dev_repo"
    _init_git_repo_with_remote(repo_path, bare_remote)
    kanban_root = _build_kanban_root(tmp_path, project_name, repo_path, push_to_remote="false")

    result_open = _run_cm_script(_OPEN_RC_SCRIPT, [version], kanban_root, project_name, repo_root=repo_path)
    assert result_open.returncode == 0, (
        f"cm-open-rc.sh failed.\nstdout: {result_open.stdout}\nstderr: {result_open.stderr}"
    )
    rc_branch = f"rc/{version}"
    _commit_on_branch(
        repo_path, rc_branch, "feature_forced_ship.txt", "forced-ship test feature content\n",
        "feat: add feature for forced-ship test", return_branch="main",
    )

    tester_task_id = "TESTER-20260908-910-forced-ship-blocked"
    _build_tester_task(kanban_root, project_name, tester_task_id, version, state="BLOCKED")

    reason_text = "Verification infra was down; the diff is a docs-only change reviewed by hand."
    result_release = _run_cm_script(
        _RELEASE_SCRIPT, ["--force", "--reason", reason_text],
        kanban_root, project_name, repo_root=repo_path,
    )
    assert result_release.returncode == 0, (
        "cm-release.sh --force --reason must pass a BLOCKED-TESTER trigger-1 HALT and ship.\n"
        f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
    )

    release_state_text = _release_state_text(kanban_root, project_name)
    assert "## Operator Override" in release_state_text, (
        f"Expected an '## Operator Override' event in release-state.md.\n{release_state_text}"
    )
    assert "cm-release.trigger-1.tester-blocked" in release_state_text, (
        f"Expected the overridden trigger key in the event.\n{release_state_text}"
    )
    assert reason_text in release_state_text, (
        f"Expected the operator's reason verbatim in the event.\n{release_state_text}"
    )

    notes_text = (repo_path / "release-notes" / f"{version}.md").read_text(encoding="utf-8")
    assert "## Status\nSHIPPED-BY-OPERATOR-OVERRIDE" in notes_text, (
        f"Expected the release notes to record the operator override status.\n{notes_text}"
    )

    assert _git_tag_exists(repo_path, version), (
        f"Release tag '{version}' not found after a forced ship.\n"
        f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
    )

    # --- Single-use proof: open a second RC, block it the same way, and
    # release WITHOUT --force. It must HALT exactly as it would have without
    # ever having seen --force before.
    version2 = "v0.30.1"
    result_open2 = _run_cm_script(_OPEN_RC_SCRIPT, [version2], kanban_root, project_name, repo_root=repo_path)
    assert result_open2.returncode == 0, (
        f"cm-open-rc.sh (second RC) failed.\nstdout: {result_open2.stdout}\nstderr: {result_open2.stderr}"
    )
    rc_branch2 = f"rc/{version2}"
    _commit_on_branch(
        repo_path, rc_branch2, "feature_second_rc.txt", "second RC feature content\n",
        "feat: add feature for second RC", return_branch="main",
    )
    tester_task_id2 = "TESTER-20260908-911-second-rc-blocked"
    _build_tester_task(kanban_root, project_name, tester_task_id2, version2, state="BLOCKED")

    result_release2 = _run_cm_script(
        _RELEASE_SCRIPT, [], kanban_root, project_name, repo_root=repo_path,
    )
    assert result_release2.returncode != 0, (
        "The release immediately following a forced one must apply the matrix "
        "normally with no --force flag present — nothing the first override "
        "wrote may persist as a mode.\n"
        f"stdout: {result_release2.stdout}\nstderr: {result_release2.stderr}"
    )
    combined2 = result_release2.stdout + result_release2.stderr
    assert "cm-release.trigger-1.tester-blocked" in combined2, (
        f"Expected the second (unforced) release to HALT on trigger-1 normally.\noutput:\n{combined2}"
    )
    assert not _git_tag_exists(repo_path, version2), (
        f"Release tag '{version2}' must NOT exist — the second release was not forced and must HALT."
    )


# ---------------------------------------------------------------------------
# --force does not reach mechanical integrity gates: squash conflict
# (trigger-5) and a malformed release-notes ## Status heading (trigger-9).
# ---------------------------------------------------------------------------


def test_forced_ship_still_halts_on_squash_conflict(tmp_path: pathlib.Path) -> None:
    """--force --reason "..." does not pass a Trigger-5 squash conflict.

    Scenario mirrors test_cm_release_synthetic.py's
    TestConflictingMidRcMainCommit: a conflicting commit on main mid-RC.
    No TESTER task is built (ship-policy defaults to SHIP-FUNCTIONAL, Row 7),
    isolating the assertion to the squash conflict alone.
    """
    version = "v0.31.0"
    project_name = "forced_ship_squash_conflict_test"

    bare_remote = tmp_path / "bare_remote.git"
    repo_path = tmp_path / "dev_repo"
    _init_git_repo_with_remote(repo_path, bare_remote)
    kanban_root = _build_kanban_root(tmp_path, project_name, repo_path, push_to_remote="false")

    _commit_on_branch(repo_path, "main", "shared.txt", "line A\nline B\nline C\n", "chore: add shared file before RC")
    subprocess.run(["git", "-C", str(repo_path), "push", "origin", "main"], capture_output=True, check=True)

    result_open = _run_cm_script(_OPEN_RC_SCRIPT, [version], kanban_root, project_name, repo_root=repo_path)
    assert result_open.returncode == 0, (
        f"cm-open-rc.sh failed.\nstdout: {result_open.stdout}\nstderr: {result_open.stderr}"
    )
    rc_branch = f"rc/{version}"
    _commit_on_branch(
        repo_path, rc_branch, "shared.txt", "line A\nline B — RC version\nline C\n",
        "feat: RC modifies shared.txt", return_branch="main",
    )
    _commit_on_branch(
        repo_path, "main", "shared.txt", "line A\nline B — main operator version\nline C\n",
        "fix: operator commits conflicting change to main mid-RC",
    )

    result_release = _run_cm_script(
        _RELEASE_SCRIPT, ["--force", "--reason", "trying to force past a squash conflict"],
        kanban_root, project_name, repo_root=repo_path,
    )
    assert result_release.returncode != 0, (
        "--force must NOT pass a squash conflict — this gate is outside its scope.\n"
        f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
    )
    combined = result_release.stdout + result_release.stderr
    assert "Trigger 5" in combined, (
        f"Expected the Trigger-5 squash-conflict HALT to fire unchanged under --force.\noutput:\n{combined}"
    )
    assert not _git_tag_exists(repo_path, version), (
        f"Release tag '{version}' must NOT exist — --force does not reach the squash-conflict gate."
    )


def test_forced_ship_still_halts_on_malformed_release_notes_status_heading(tmp_path: pathlib.Path) -> None:
    """--force --reason "..." does not pass a Trigger-9 malformed-notes HALT.

    A WRITER-authored release-notes file with no recognizable "## Status"
    heading at all HALTs at Step 4e regardless of --force — this is a
    mechanical integrity guard, not a ship-policy matrix judgement call.
    """
    version = "v0.32.0"
    project_name = "forced_ship_notes_stamp_guard_test"

    bare_remote = tmp_path / "bare_remote.git"
    repo_path = tmp_path / "dev_repo"
    _init_git_repo_with_remote(repo_path, bare_remote)
    kanban_root = _build_kanban_root(tmp_path, project_name, repo_path, push_to_remote="false")

    result_open = _run_cm_script(_OPEN_RC_SCRIPT, [version], kanban_root, project_name, repo_root=repo_path)
    assert result_open.returncode == 0, (
        f"cm-open-rc.sh failed.\nstdout: {result_open.stdout}\nstderr: {result_open.stderr}"
    )
    rc_branch = f"rc/{version}"
    _commit_on_branch(
        repo_path, rc_branch, "feature_notes_guard.txt", "notes-guard test feature content\n",
        "feat: add feature for notes-stamp-guard test",
    )

    malformed_notes = (
        f"# Release Notes: {project_name} {version}\n\n"
        "Some release prose with no recognizable Status heading at all.\n"
    )
    (repo_path / "release-notes" / f"{version}.md").write_text(malformed_notes, encoding="utf-8")
    _git_local(repo_path, "add", f"release-notes/{version}.md")
    _git_local(repo_path, "commit", "-m", "docs: malformed notes missing Status heading")
    _git_local(repo_path, "checkout", "main")

    tester_task_id = "TESTER-20260908-912-notes-guard-pass"
    _build_tester_task(kanban_root, project_name, tester_task_id, version, state="DONE", recommendation="PASS", systemic_risk="low")

    result_release = _run_cm_script(
        _RELEASE_SCRIPT, ["--force", "--reason", "trying to force past a malformed notes file"],
        kanban_root, project_name, repo_root=repo_path,
    )
    assert result_release.returncode != 0, (
        "--force must NOT pass the release-notes Status-heading guard — this "
        "gate is outside its scope.\n"
        f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
    )
    combined = result_release.stdout + result_release.stderr
    assert "cm-release.trigger-9.notes-missing-status-heading" in combined, (
        f"Expected the Trigger-9 malformed-notes HALT to fire unchanged under --force.\noutput:\n{combined}"
    )
    assert not _git_tag_exists(repo_path, version), (
        f"Release tag '{version}' must NOT exist — --force does not reach the notes-stamp guard."
    )
