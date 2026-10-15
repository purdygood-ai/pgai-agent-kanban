"""
test_verify_rc_state_check_e_branch_prefix.py
==============================================
Integration tests for team/scripts/verify-rc-state.sh's Check e
(release-state.md <-> git branch consistency), covering a project
configured with [project] branch_prefix.

Coverage:
  - A prefixed project (branch_prefix = ai_) whose Active RC has a matching
    <prefix>rc/<version> branch produces an [OK] line naming the prefixed
    branch, and the script exits 0.
  - A prefixed project whose Active RC names a version with no matching
    <prefix>rc/<version> branch produces an [ERROR] line naming the
    prefixed branch it looked for, and the script exits 1.
  - A prefixed project with Active RC=none and a stray <prefix>rc/<version>
    branch present produces a [WARN] line naming that branch.
  - An unprefixed project (branch_prefix absent) resolves rc/<version> and
    reports [OK]/[ERROR]/[WARN] exactly as it always has -- asserted
    explicitly here rather than left to be inferred from the prefixed
    cases above.

Design notes:
  - All tests use tmp_path for scratch; no bare /tmp paths.
  - verify-rc-state.sh is invoked via subprocess against a temporary kanban
    root and a temporary local git repo -- never the live install.
  - Fixture construction mirrors
    test_verify_rc_state_release_check.py's pattern, trimmed to what
    Check e needs, plus an optional branch_prefix knob.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest
from tests.fixtures.binary_guard import require_binary


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Every test here builds, inspects, or drives a real git repository; a
    module-wide autouse guard keeps that requirement in one place instead
    of repeating a per-test check at each git-dependent call site.
    """
    require_binary("git", label="verify-rc-state-check-e-branch-prefix")

# ---------------------------------------------------------------------------
# Path helpers — resolve from this file's own location.
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_VERIFY_SCRIPT = _TEAM_DIR / "scripts" / "verify-rc-state.sh"


def _init_local_repo(repo_path: pathlib.Path) -> None:
    """Initialise a minimal local git repo with one commit -- no remote."""
    repo_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_path, check=True,
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo_path, check=True)
    (repo_path / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo_path, check=True)


def _create_branch(repo_path: pathlib.Path, branch_name: str) -> None:
    """Create branch_name at the repo's current HEAD without checking it out."""
    subprocess.run(["git", "branch", branch_name], cwd=repo_path, check=True)


def _build_kanban_root(
    parent: pathlib.Path,
    project_name: str,
    repo_path: pathlib.Path,
    branch_prefix: str = "",
) -> pathlib.Path:
    """Build a minimal kanban root that verify-rc-state.sh can run against.

    branch_prefix, when non-empty, is written to project.cfg's
    [project] branch_prefix so Check e must resolve RC branch names
    through pp_prefix_branch rather than bare 'rc/' concatenation.
    """
    root = parent / "kanban_root"
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "locks").mkdir(parents=True, exist_ok=True)
    (root / "kanban.cfg").write_text(
        "[paths]\n\n[chain]\npm_mode = automatic\n\n[wake]\nmax_tasks_per_wake = 1\n",
        encoding="utf-8",
    )
    (root / "projects.cfg").write_text(
        f"[project:{project_name}]\npriority=1\ndescription=verify test\nenabled=true\n",
        encoding="utf-8",
    )
    proj = root / "projects" / project_name
    (proj / "tasks" / "queues").mkdir(parents=True, exist_ok=True)
    cfg_lines = [
        "[project]",
        f"project_name = {project_name}",
        f"dev_tree_path = {repo_path}",
        f"git_repo_url = git@example.com:{project_name}.git",
        "git_remote_name = origin",
        "workflow_type = release",
        "is_self_build = false",
        "push_to_remote = false",
    ]
    if branch_prefix:
        cfg_lines.append(f"branch_prefix = {branch_prefix}")
    cfg_lines += [
        "",
        "[versioning]",
        "max_patch = 99",
        "max_minor = 9",
        "max_major = 0",
        "",
    ]
    (proj / "project.cfg").write_text("\n".join(cfg_lines), encoding="utf-8")
    return root


def _write_release_state(project_root: pathlib.Path, active_rc: str) -> None:
    (project_root / "release-state.md").write_text(
        "# Release State\n\n"
        f"## Active RC\n{active_rc}\n\n"
        "## RC Opened At\nnone\n\n"
        "## RC Opened By Task\nnone\n\n"
        "## Last Released\nnone\n",
        encoding="utf-8",
    )


def _run_verify_rc_state(
    kanban_root: pathlib.Path, project_name: str
) -> subprocess.CompletedProcess:
    base_env = dict(os.environ)
    base_env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    base_env["KANBAN_ROOT"] = str(kanban_root)
    base_env.pop("PGAI_DEV_TREE_PATH", None)
    return subprocess.run(
        ["bash", str(_VERIFY_SCRIPT), project_name, "--verbose"],
        capture_output=True, text=True, env=base_env, timeout=60,
    )


# ---------------------------------------------------------------------------
# Prefixed project (branch_prefix = ai_)
# ---------------------------------------------------------------------------


def test_check_e_prefixed_project_ok_when_matching_branch_exists(
    tmp_path: pathlib.Path,
) -> None:
    """Active RC names a version, the matching ai_rc/<version> branch
    exists -- Check e prints [OK] naming the prefixed branch and the
    whole run exits 0."""
    repo = tmp_path / "repo"
    _init_local_repo(repo)
    _create_branch(repo, "ai_rc/v2.11.1")
    kanban_root = _build_kanban_root(tmp_path, "verifytest_prefixed", repo, "ai_")
    _write_release_state(
        kanban_root / "projects" / "verifytest_prefixed", "v2.11.1"
    )

    result = _run_verify_rc_state(kanban_root, "verifytest_prefixed")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "[OK]    Active RC v2.11.1 has matching local branch: ai_rc/v2.11.1"
        in result.stdout
    ), result.stdout
    assert "0 errors" in result.stdout


def test_check_e_prefixed_project_error_when_branch_missing(
    tmp_path: pathlib.Path,
) -> None:
    """Active RC names a version with no matching ai_rc/<version> branch --
    Check e prints [ERROR] naming the prefixed branch it looked for, and
    the whole run exits 1."""
    repo = tmp_path / "repo"
    _init_local_repo(repo)
    # No ai_rc/v9.9.9 branch created.
    kanban_root = _build_kanban_root(tmp_path, "verifytest_prefixed_err", repo, "ai_")
    _write_release_state(
        kanban_root / "projects" / "verifytest_prefixed_err", "v9.9.9"
    )

    result = _run_verify_rc_state(kanban_root, "verifytest_prefixed_err")

    assert result.returncode == 1, result.stdout + result.stderr
    error_lines = [
        line for line in result.stdout.splitlines()
        if line.startswith("[ERROR]") and "Active RC" in line
    ]
    assert error_lines, result.stdout
    assert "ai_rc/v9.9.9" in error_lines[0], error_lines[0]


def test_check_e_prefixed_project_warn_on_stray_branch_when_active_rc_none(
    tmp_path: pathlib.Path,
) -> None:
    """Active RC is none but a stray ai_rc/<version> branch exists -- Check e
    prints [WARN] naming that branch (never surfacing it via a bare 'rc/*'
    glob that would miss a prefixed stray branch entirely)."""
    repo = tmp_path / "repo"
    _init_local_repo(repo)
    _create_branch(repo, "ai_rc/v0.0.1")
    kanban_root = _build_kanban_root(tmp_path, "verifytest_prefixed_warn", repo, "ai_")
    _write_release_state(
        kanban_root / "projects" / "verifytest_prefixed_warn", "none"
    )

    result = _run_verify_rc_state(kanban_root, "verifytest_prefixed_warn")

    warn_lines = [
        line for line in result.stdout.splitlines()
        if line.startswith("[WARN]") and "ai_rc/v0.0.1" in line
    ]
    assert warn_lines, result.stdout


def test_check_e_prefixed_project_warns_on_stray_unprefixed_branch_when_active_rc_none(
    tmp_path: pathlib.Path,
) -> None:
    """A prefixed project (branch_prefix = ai_) with Active RC=none
    and a stray UNPREFIXED rc/v0.0.9 branch present must not produce a false
    all-clear -- Check e prints [WARN] naming the unprefixed stray even
    though it never matches the prefixed ai_rc/* glob."""
    repo = tmp_path / "repo"
    _init_local_repo(repo)
    _create_branch(repo, "rc/v0.0.9")
    kanban_root = _build_kanban_root(tmp_path, "verifytest_stray_unprefixed", repo, "ai_")
    _write_release_state(
        kanban_root / "projects" / "verifytest_stray_unprefixed", "none"
    )

    result = _run_verify_rc_state(kanban_root, "verifytest_stray_unprefixed")

    warn_lines = [
        line for line in result.stdout.splitlines()
        if line.startswith("[WARN]") and "rc/v0.0.9" in line
    ]
    assert warn_lines, result.stdout
    assert "ai_rc/v0.0.9" not in warn_lines[0], (
        f"the stray is a bare rc/v0.0.9 branch, not a prefixed one\n{warn_lines[0]}"
    )


def test_check_e_prefixed_project_ok_for_active_rc_and_warn_for_stray_unprefixed(
    tmp_path: pathlib.Path,
) -> None:
    """With Active RC set and ai_rc/<version> present, Check e
    prints [OK] for the Active RC branch AND [WARN] for a co-existing
    stray unprefixed rc/v0.0.9 branch -- the two findings are independent
    and both must surface."""
    repo = tmp_path / "repo"
    _init_local_repo(repo)
    _create_branch(repo, "ai_rc/v2.11.1")
    _create_branch(repo, "rc/v0.0.9")
    kanban_root = _build_kanban_root(tmp_path, "verifytest_stray_plus_active", repo, "ai_")
    _write_release_state(
        kanban_root / "projects" / "verifytest_stray_plus_active", "v2.11.1"
    )

    result = _run_verify_rc_state(kanban_root, "verifytest_stray_plus_active")

    assert (
        "[OK]    Active RC v2.11.1 has matching local branch: ai_rc/v2.11.1"
        in result.stdout
    ), result.stdout
    warn_lines = [
        line for line in result.stdout.splitlines()
        if line.startswith("[WARN]") and "rc/v0.0.9" in line
    ]
    assert warn_lines, result.stdout
    assert "ai_rc/v0.0.9" not in warn_lines[0], warn_lines[0]


# ---------------------------------------------------------------------------
# Unprefixed project (branch_prefix absent) -- asserted explicitly, not
# inferred by omission, per the acceptance criteria.
# ---------------------------------------------------------------------------


def test_check_e_unprefixed_project_ok_when_matching_branch_exists(
    tmp_path: pathlib.Path,
) -> None:
    """No branch_prefix configured -- Check e still resolves the bare
    rc/<version> branch name and reports [OK] exactly as before this
    change."""
    repo = tmp_path / "repo"
    _init_local_repo(repo)
    _create_branch(repo, "rc/v1.0.0")
    kanban_root = _build_kanban_root(tmp_path, "verifytest_unprefixed", repo)
    _write_release_state(kanban_root / "projects" / "verifytest_unprefixed", "v1.0.0")

    result = _run_verify_rc_state(kanban_root, "verifytest_unprefixed")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "[OK]    Active RC v1.0.0 has matching local branch: rc/v1.0.0"
        in result.stdout
    ), result.stdout
    assert "0 errors" in result.stdout


def test_check_e_unprefixed_project_error_when_branch_missing(
    tmp_path: pathlib.Path,
) -> None:
    """No branch_prefix configured -- a missing rc/<version> branch still
    produces [ERROR] naming the unprefixed branch, and exit 1."""
    repo = tmp_path / "repo"
    _init_local_repo(repo)
    kanban_root = _build_kanban_root(tmp_path, "verifytest_unprefixed_err", repo)
    _write_release_state(
        kanban_root / "projects" / "verifytest_unprefixed_err", "v9.9.9"
    )

    result = _run_verify_rc_state(kanban_root, "verifytest_unprefixed_err")

    assert result.returncode == 1, result.stdout + result.stderr
    error_lines = [
        line for line in result.stdout.splitlines()
        if line.startswith("[ERROR]") and "Active RC" in line
    ]
    assert error_lines, result.stdout
    assert "rc/v9.9.9" in error_lines[0], error_lines[0]
    assert "ai_rc/v9.9.9" not in error_lines[0], error_lines[0]


def test_check_e_unprefixed_project_warn_on_stray_branch_when_active_rc_none(
    tmp_path: pathlib.Path,
) -> None:
    """No branch_prefix configured -- Active RC=none with a stray rc/<version>
    branch present still produces [WARN] naming that branch."""
    repo = tmp_path / "repo"
    _init_local_repo(repo)
    _create_branch(repo, "rc/v0.0.1")
    kanban_root = _build_kanban_root(tmp_path, "verifytest_unprefixed_warn", repo)
    _write_release_state(
        kanban_root / "projects" / "verifytest_unprefixed_warn", "none"
    )

    result = _run_verify_rc_state(kanban_root, "verifytest_unprefixed_warn")

    warn_lines = [
        line for line in result.stdout.splitlines()
        if line.startswith("[WARN]") and "rc/v0.0.1" in line
    ]
    assert warn_lines, result.stdout


def test_check_e_unprefixed_project_reports_each_stray_branch_exactly_once(
    tmp_path: pathlib.Path,
) -> None:
    """With an empty branch_prefix, the prefixed glob and the bare
    'rc/*' glob are identical, so the stray-unprefixed-branch surfacing
    added for prefixed projects must not fire a second time here -- each
    rc/* branch is named by exactly one [WARN] line, never two."""
    repo = tmp_path / "repo"
    _init_local_repo(repo)
    _create_branch(repo, "rc/v0.0.1")
    kanban_root = _build_kanban_root(tmp_path, "verifytest_unprefixed_once", repo)
    _write_release_state(
        kanban_root / "projects" / "verifytest_unprefixed_once", "none"
    )

    result = _run_verify_rc_state(kanban_root, "verifytest_unprefixed_once")

    warn_lines = [
        line for line in result.stdout.splitlines()
        if line.startswith("[WARN]") and "rc/v0.0.1" in line
    ]
    assert len(warn_lines) == 1, (
        f"expected exactly one [WARN] line naming rc/v0.0.1, found "
        f"{len(warn_lines)}:\n{result.stdout}"
    )
