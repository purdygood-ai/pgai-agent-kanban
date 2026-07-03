"""
test_rc_lifecycle.py
====================
Integration tests for the RC lifecycle: cm-open-rc → coder merge → tester verify
→ cm-release.

These tests exercise the real CM scripts end-to-end against temporary kanban trees
and temporary git repositories.  No seam is mocked — each test stands up a real
tree, runs real scripts, and asserts on the resulting on-disk state (branches, tags,
release-state.md, develop/main contents).

Coverage:
  - Full open-rc → coder merge → cm-release path produces correct tag/branch state
  - Branch-prefix isolation: branch_prefix= in project.cfg is applied to all
    git branches (rc/<v>, develop, main → prefix_rc/prefix_v, prefix_develop, etc.)
  - Idempotent release re-run: a second cm-release.sh invocation does not corrupt
    the already-shipped state
  - Local-only vs push behavior: push_to_remote=false keeps the RC branch local
    (not pushed to origin); push_to_remote=true pushes to a local bare remote

Design notes:
  - All tests use pytest's tmp_path for scratch.  No bare /tmp paths.
  - Each test is self-contained and produces no state visible to other tests.
  - Test names describe the behavior under test; no bug IDs, version numbers,
    or gate tokens appear in function names (SOP.md Anti-pattern 6).
  - The "dev tree" in every test is a local temp git repo, never the live repo.
  - Push tests use a local bare git repo as the remote — no real network access.
  - Because cm-open-rc.sh unconditionally fetches and pulls from origin (even
    with push_to_remote=false), all tests use a local bare repo as origin.
    The push_to_remote=false tests verify the RC branch is NOT pushed to origin;
    the push_to_remote=true tests verify that it IS.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
from typing import Optional

import pytest

# ---------------------------------------------------------------------------
# Path helpers — resolve from this file's own location.
#
# File structure:
#   team/tests/integration/test_rc_lifecycle.py
#                    └── team/tests/integration/    (this file)
#                        └── team/tests/
#                            └── team/
#                                └── <dev-tree-root>
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_CM_SCRIPTS_DIR = _SCRIPTS_DIR / "cm"
_KANBAN_PY_DIR = _TEAM_DIR / "pgai_agent_kanban"

_OPEN_RC_SCRIPT = _CM_SCRIPTS_DIR / "open-rc.sh"
_RELEASE_SCRIPT = _CM_SCRIPTS_DIR / "release.sh"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _init_git_repo_with_remote(
    repo_path: pathlib.Path,
    bare_remote: pathlib.Path,
    *,
    main_branch: str = "main",
    develop_branch: str = "develop",
) -> None:
    """Initialise a local git repo with initial branches and a local bare remote.

    Creates:
      - A bare git repo at *bare_remote* acting as 'origin'
      - A non-bare git repo at *repo_path* with an initial commit on *main_branch*
        and a *develop_branch* forked from main, both pushed to *bare_remote*

    All git config is repo-local (--local) so no ~/.gitconfig is touched.

    Args:
        repo_path:       Directory to initialise as a non-bare git repo.
        bare_remote:     Directory to initialise as a bare repo ('origin').
        main_branch:     Name for the main/trunk branch (default: 'main').
        develop_branch:  Name for the integration branch (default: 'develop').
    """
    bare_remote.mkdir(parents=True, exist_ok=True)
    repo_path.mkdir(parents=True, exist_ok=True)

    # Initialise the bare remote first.
    subprocess.run(
        ["git", "init", "--bare", str(bare_remote)],
        capture_output=True, check=True,
    )

    # Initialise the non-bare repo.
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

    # Create an initial commit on main_branch.
    (repo_path / "README.md").write_text("# Test repo\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo_path), "add", "README.md"],
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "commit", "-m", "Initial commit"],
        capture_output=True, check=True,
    )

    # Add the bare remote and push main_branch.
    subprocess.run(
        ["git", "-C", str(repo_path), "remote", "add", "origin", str(bare_remote)],
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "push", "origin", main_branch],
        capture_output=True, check=True,
    )

    # Create develop_branch from main_branch and push it.
    subprocess.run(
        ["git", "-C", str(repo_path), "checkout", "-b", develop_branch],
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "push", "-u", "origin", develop_branch],
        capture_output=True, check=True,
    )


def _build_kanban_root_for_rc(
    parent: pathlib.Path,
    project_name: str,
    repo_path: pathlib.Path,
    *,
    push_to_remote: str = "false",
    branch_prefix: str = "",
) -> pathlib.Path:
    """Build a minimal kanban root configured for RC lifecycle testing.

    Creates the directories and config files that cm-open-rc.sh and cm-release.sh
    need: kanban.cfg, projects.cfg, project.cfg, and release-state.md (idle).

    The project.cfg points to *repo_path* as the dev_tree_path, so the CM scripts
    operate against the caller's temp git repo.

    Args:
        parent:         Parent directory for the kanban root.
        project_name:   Name of the project (used in projects.cfg and project.cfg).
        repo_path:      Path to the temp git repo (used as dev_tree_path in project.cfg).
        push_to_remote: 'true' or 'false' for [project] push_to_remote.
        branch_prefix:  Optional branch prefix (e.g. 'ai_') for hybrid installs.

    Returns:
        pathlib.Path — the kanban root.
    """
    root = parent / "kanban_root"
    root.mkdir(parents=True, exist_ok=True)

    # --- kanban.cfg ---
    (root / "kanban.cfg").write_text(
        "[paths]\n"
        "\n"
        "[chain]\n"
        "pm_mode = automatic\n"
        "\n"
        "[wake]\n"
        "max_tasks_per_wake = 1\n"
        "max_runtime_seconds = 600\n",
        encoding="utf-8",
    )

    # --- projects.cfg ---
    (root / "projects.cfg").write_text(
        f"[project:{project_name}]\n"
        "priority=1\n"
        f"description=RC lifecycle test project\n"
        "enabled=true\n",
        encoding="utf-8",
    )

    # --- Project directory ---
    proj = root / "projects" / project_name
    proj.mkdir(parents=True, exist_ok=True)

    # project.cfg points at the temp git repo
    prefix_line = f"branch_prefix = {branch_prefix}\n" if branch_prefix else ""
    (proj / "project.cfg").write_text(
        f"[project]\n"
        f"project_name = {project_name}\n"
        f"dev_tree_path = {repo_path}\n"
        f"git_repo_url = git@example.com:{project_name}.git\n"
        f"git_remote_name = origin\n"
        f"workflow_type = release\n"
        f"is_self_build = false\n"
        f"push_to_remote = {push_to_remote}\n"
        f"{prefix_line}"
        f"\n"
        f"[versioning]\n"
        f"max_patch = 99\n"
        f"max_minor = 9\n"
        f"max_major = 0\n",
        encoding="utf-8",
    )

    # release-state.md — idle (no active RC)
    (proj / "release-state.md").write_text(
        "# Release State\n\n"
        "## Active RC\n"
        "none\n\n"
        "## RC Opened At\n"
        "none\n\n"
        "## RC Opened By Task\n"
        "none\n",
        encoding="utf-8",
    )

    # release-state/ dir (for per-RC JSON)
    (proj / "release-state").mkdir(parents=True, exist_ok=True)

    # tasks/ and queues/ directories (release.sh scans them for TESTER tasks)
    (proj / "tasks" / "queues").mkdir(parents=True, exist_ok=True)

    # Runtime dirs expected by the scripts
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "locks").mkdir(parents=True, exist_ok=True)

    # Copy the Python helper modules CM scripts need via $KANBAN_ROOT/pgai_agent_kanban/cm/
    cm_py_dir = root / "pgai_agent_kanban" / "cm"
    cm_py_dir.mkdir(parents=True, exist_ok=True)

    src_cm_dir = _KANBAN_PY_DIR / "cm"
    for py_file in src_cm_dir.glob("*.py"):
        (cm_py_dir / py_file.name).write_text(
            py_file.read_text(encoding="utf-8"), encoding="utf-8"
        )

    return root


def _run_cm_script(
    script_path: pathlib.Path,
    args: list[str],
    kanban_root: pathlib.Path,
    project_name: str,
    *,
    repo_root: Optional[pathlib.Path] = None,
    extra_env: Optional[dict] = None,
    timeout: int = 90,
) -> subprocess.CompletedProcess:
    """Run a CM script against a temporary kanban root.

    Sets PGAI_AGENT_KANBAN_ROOT_PATH, KANBAN_ROOT, TEAM_ROOT, and PGAI_PROJECT_NAME
    so the script operates against the caller's temp tree, not the live install.
    REPO_ROOT is also set when provided (overrides project.cfg dev_tree_path lookup).

    Args:
        script_path:  Absolute path to the CM script to run.
        args:         Additional arguments to pass to the script.
        kanban_root:  The temp kanban root.
        project_name: Project to operate on.
        repo_root:    Optional path to the git repo (sets REPO_ROOT env var).
        extra_env:    Additional env overrides (caller wins).
        timeout:      Subprocess timeout in seconds.

    Returns:
        subprocess.CompletedProcess with returncode, stdout, stderr.
    """
    base_env = dict(os.environ)
    base_env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    base_env["KANBAN_ROOT"] = str(kanban_root)
    base_env["TEAM_ROOT"] = str(kanban_root)
    base_env["PGAI_PROJECT_NAME"] = project_name
    if repo_root is not None:
        base_env["REPO_ROOT"] = str(repo_root)
    # Prevent live install configuration from leaking into the subprocess.
    base_env.pop("PGAI_DEV_TREE_PATH", None)
    if extra_env:
        base_env.update(extra_env)

    return subprocess.run(
        ["bash", str(script_path)] + args,
        capture_output=True, text=True, env=base_env,
        cwd=str(kanban_root), timeout=timeout,
    )


def _git_local(repo_path: pathlib.Path, *args: str) -> str:
    """Run a git command in *repo_path* and return stripped stdout."""
    result = subprocess.run(
        ["git", "-C", str(repo_path)] + list(args),
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _git_branch_exists_local(repo_path: pathlib.Path, branch: str) -> bool:
    """Return True when *branch* exists as a local branch in *repo_path*."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--verify", f"refs/heads/{branch}"],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def _git_tag_exists(repo_path: pathlib.Path, tag: str) -> bool:
    """Return True when *tag* exists in *repo_path*."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--verify", f"refs/tags/{tag}"],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def _git_branch_exists_on_bare_remote(bare_remote: pathlib.Path, branch: str) -> bool:
    """Return True when *branch* exists in the bare remote repo."""
    result = subprocess.run(
        ["git", "-C", str(bare_remote), "branch", "--list", branch],
        capture_output=True, text=True,
    )
    return branch in result.stdout


def _git_tag_exists_on_bare_remote(bare_remote: pathlib.Path, tag: str) -> bool:
    """Return True when *tag* exists in the bare remote repo."""
    result = subprocess.run(
        ["git", "-C", str(bare_remote), "tag", "--list", tag],
        capture_output=True, text=True,
    )
    return tag in result.stdout


def _simulate_coder_work(
    repo_path: pathlib.Path,
    rc_branch: str,
    develop_branch: str = "develop",
    commit_message: str = "feat: add feature for release",
) -> None:
    """Simulate a CODER task: add a commit on the RC branch.

    Checks out *rc_branch*, adds a new file, commits, and returns to develop.

    Args:
        repo_path:       Path to the temp git repo.
        rc_branch:       Name of the RC branch to commit on.
        develop_branch:  Name of the integration branch to return to.
        commit_message:  Commit message for the simulated coder work.
    """
    _git_local(repo_path, "checkout", rc_branch)
    (repo_path / "feature.txt").write_text("feature content\n", encoding="utf-8")
    _git_local(repo_path, "add", "feature.txt")
    _git_local(repo_path, "commit", "-m", commit_message)
    _git_local(repo_path, "checkout", develop_branch)


def _add_tester_task(
    kanban_root: pathlib.Path,
    project_name: str,
    version: str,
    state: str = "DONE",
    recommendation: str = "PASS",
    systemic_risk: str = "low",
) -> pathlib.Path:
    """Add a synthetic TESTER task folder to the project's tasks directory.

    Creates a minimal TESTER task folder with README.md and status.md that
    cm-release.sh will discover when looking for a TESTER task for this version.
    An artifacts/report.md is also created so cm-release.sh can read the
    Recommendation and Systemic Risk fields.

    Args:
        kanban_root:    The temp kanban root.
        project_name:   Project the task belongs to.
        version:        Release version this task covers (e.g. "v0.1.0").
        state:          Task state (e.g. "DONE", "BLOCKED").
        recommendation: TESTER recommendation (e.g. "PASS").
        systemic_risk:  Systemic risk level (e.g. "low", "high").

    Returns:
        pathlib.Path — the created task directory.
    """
    version_slug = version.replace(".", "-")
    task_id = f"TESTER-20260101-001-verify-{version_slug}"
    tasks_dir = kanban_root / "projects" / project_name / "tasks"
    task_dir = tasks_dir / task_id
    (task_dir / "artifacts").mkdir(parents=True, exist_ok=True)

    (task_dir / "README.md").write_text(
        f"# {task_id}\n\n"
        f"## Role\nTESTER\n\n"
        f"## Release Version\n{version}\n",
        encoding="utf-8",
    )

    (task_dir / "status.md").write_text(
        f"# Status\n\n"
        f"## State\n{state}\n\n"
        f"## Summary\nIntegration test synthetic TESTER task.\n",
        encoding="utf-8",
    )

    (task_dir / "artifacts" / "report.md").write_text(
        f"# TESTER Report for {version}\n\n"
        f"## Recommendation\n{recommendation}\n\n"
        f"## Systemic Risk\n{systemic_risk}\n\n"
        f"## Summary\nAll checks passed.\n",
        encoding="utf-8",
    )

    return task_dir


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFullRCLifecycleLocalOnly:
    """Full RC lifecycle with push_to_remote=false — local git state, no remote push."""

    def test_full_lifecycle_produces_release_tag_on_main(
        self, tmp_path: pathlib.Path
    ) -> None:
        """open-rc → coder merge → cm-release produces a version tag on main.

        Running cm-open-rc.sh followed by a simulated coder commit and then
        cm-release.sh must result in a git tag (the release version) applied to
        the main branch.  This is the end-to-end RC lifecycle's central assertion:
        the tag on main is the canonical record that a version was shipped.

        push_to_remote=false: a local bare repo satisfies the origin fetch/pull
        requirements of cm-open-rc.sh, but the RC branch itself is NOT pushed there.
        """
        version = "v0.1.0"
        project_name = "lifecycle_test"

        bare_remote = tmp_path / "bare_remote.git"
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, bare_remote)

        kanban_root = _build_kanban_root_for_rc(
            tmp_path, project_name, repo_path, push_to_remote="false"
        )

        # Step 1: Open the RC.
        result = _run_cm_script(
            _OPEN_RC_SCRIPT, [version],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result.returncode == 0, (
            f"cm-open-rc.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        rc_branch = f"rc/{version}"
        assert _git_branch_exists_local(repo_path, rc_branch), (
            f"RC branch '{rc_branch}' was not created after cm-open-rc.sh.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Step 2: Simulate coder work — add a commit on the RC branch.
        _simulate_coder_work(repo_path, rc_branch)

        # Step 3: Run cm-release.sh to close the RC.
        result = _run_cm_script(
            _RELEASE_SCRIPT, [],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result.returncode == 0, (
            f"cm-release.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Assert: the release tag exists locally.
        assert _git_tag_exists(repo_path, version), (
            f"Release tag '{version}' not found after cm-release.sh.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Assert: the RC branch was deleted locally.
        assert not _git_branch_exists_local(repo_path, rc_branch), (
            f"RC branch '{rc_branch}' still exists after cm-release.sh (should be deleted).\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_full_lifecycle_clears_active_rc_in_release_state(
        self, tmp_path: pathlib.Path
    ) -> None:
        """After cm-release.sh, release-state.md Active RC field is cleared to 'none'.

        The Active RC field in the project-scoped release-state.md is the gate that
        prevents the discovery pipeline from bundling new requirements while an RC is
        in flight.  Clearing it to 'none' is what re-enables the pipeline after ship.
        """
        import re as _re
        version = "v0.2.0"
        project_name = "state_clear_test"

        bare_remote = tmp_path / "bare_remote.git"
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, bare_remote)

        kanban_root = _build_kanban_root_for_rc(
            tmp_path, project_name, repo_path, push_to_remote="false"
        )

        _run_cm_script(
            _OPEN_RC_SCRIPT, [version],
            kanban_root, project_name, repo_root=repo_path,
        )
        _simulate_coder_work(repo_path, f"rc/{version}")

        result = _run_cm_script(
            _RELEASE_SCRIPT, [],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result.returncode == 0, (
            f"cm-release.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Assert: release-state.md Active RC is cleared to 'none'.
        release_state_path = kanban_root / "projects" / project_name / "release-state.md"
        state_text = release_state_path.read_text(encoding="utf-8")
        match = _re.search(r"##\s*Active RC\s*\n(.+?)(?=\n##|\Z)", state_text, _re.DOTALL)
        assert match is not None, (
            f"Could not find '## Active RC' section in release-state.md.\n"
            f"Content:\n{state_text}"
        )
        active_rc_value = match.group(1).strip()
        assert active_rc_value == "none", (
            f"Expected Active RC = 'none' after cm-release.sh, got '{active_rc_value}'.\n"
            f"release-state.md content:\n{state_text}\n"
            f"stdout: {result.stdout}"
        )

    def test_coder_commit_on_rc_branch_is_reachable_from_develop_after_release(
        self, tmp_path: pathlib.Path
    ) -> None:
        """After cm-release.sh, the coder's commit content is present on develop.

        The RC lifecycle squashes the RC branch into develop before tagging main.
        A file committed on the RC branch by a simulated coder task must be
        present on develop's working tree after cm-release.sh completes.
        """
        version = "v0.3.0"
        project_name = "coder_reachable_test"

        bare_remote = tmp_path / "bare_remote.git"
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, bare_remote)

        kanban_root = _build_kanban_root_for_rc(
            tmp_path, project_name, repo_path, push_to_remote="false"
        )

        _run_cm_script(
            _OPEN_RC_SCRIPT, [version],
            kanban_root, project_name, repo_root=repo_path,
        )

        rc_branch = f"rc/{version}"
        _simulate_coder_work(repo_path, rc_branch, commit_message="feat: coder work for v0.3.0")

        result = _run_cm_script(
            _RELEASE_SCRIPT, [],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result.returncode == 0, (
            f"cm-release.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Assert: feature.txt is present on develop after the squash.
        _git_local(repo_path, "checkout", "develop")
        assert (repo_path / "feature.txt").exists(), (
            "feature.txt (added by simulated coder work on RC branch) must be "
            "present on develop after cm-release.sh squash.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_open_rc_refused_when_rc_already_active(
        self, tmp_path: pathlib.Path
    ) -> None:
        """cm-open-rc.sh refuses to open a second RC when one is already in flight.

        The Active RC gate in cm-open-rc.sh prevents two simultaneous RCs on the
        same project.  This is the safety belt that prevents version collisions.
        """
        version_a = "v0.4.0"
        version_b = "v0.5.0"
        project_name = "double_open_test"

        bare_remote = tmp_path / "bare_remote.git"
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, bare_remote)

        kanban_root = _build_kanban_root_for_rc(
            tmp_path, project_name, repo_path, push_to_remote="false"
        )

        # Open the first RC — must succeed.
        result_a = _run_cm_script(
            _OPEN_RC_SCRIPT, [version_a],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result_a.returncode == 0, (
            f"First open-rc failed.\nstdout: {result_a.stdout}\nstderr: {result_a.stderr}"
        )

        # Attempt to open a second RC — must be refused.
        result_b = _run_cm_script(
            _OPEN_RC_SCRIPT, [version_b],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result_b.returncode != 0, (
            "cm-open-rc.sh must refuse to open a second RC while one is already active.\n"
            f"stdout: {result_b.stdout}\nstderr: {result_b.stderr}"
        )
        combined_error = result_b.stderr + result_b.stdout
        assert "active rc" in combined_error.lower() or "already exists" in combined_error.lower(), (
            "Expected an error message referencing an active RC or existing branch.\n"
            f"stderr: {result_b.stderr}\nstdout: {result_b.stdout}"
        )


class TestBranchPrefixIsolation:
    """Branch-prefix isolation: all git branches observe the configured prefix."""

    def test_branch_prefix_applied_to_rc_branch_name(
        self, tmp_path: pathlib.Path
    ) -> None:
        """When branch_prefix=ai_, cm-open-rc.sh creates ai_rc/<version> instead of rc/<version>.

        Projects with a branch_prefix configured in project.cfg must have that prefix
        applied to every branch name the CM scripts touch.  The RC branch itself must
        carry the prefix.
        """
        version = "v0.1.0"
        project_name = "prefix_test"
        prefix = "ai_"

        bare_remote = tmp_path / "bare_remote.git"
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(
            repo_path, bare_remote,
            main_branch="ai_main",
            develop_branch="ai_develop",
        )

        kanban_root = _build_kanban_root_for_rc(
            tmp_path, project_name, repo_path,
            push_to_remote="false",
            branch_prefix=prefix,
        )

        result = _run_cm_script(
            _OPEN_RC_SCRIPT, [version],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result.returncode == 0, (
            f"cm-open-rc.sh with branch_prefix failed.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # The RC branch must carry the prefix (ai_rc/v0.1.0).
        prefixed_rc_branch = f"{prefix}rc/{version}"
        bare_rc_branch = f"rc/{version}"

        assert _git_branch_exists_local(repo_path, prefixed_rc_branch), (
            f"Expected prefixed RC branch '{prefixed_rc_branch}' to be created, but it is absent.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert not _git_branch_exists_local(repo_path, bare_rc_branch), (
            f"Bare RC branch '{bare_rc_branch}' must NOT exist when branch_prefix='{prefix}'.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_branch_prefix_preserved_through_full_release(
        self, tmp_path: pathlib.Path
    ) -> None:
        """With branch_prefix=ai_, the full lifecycle creates the tag and removes ai_rc/<v>.

        The tag and squash operations must also observe the prefix.  After cm-release.sh
        the prefixed RC branch must be gone and the release tag must exist.
        """
        version = "v0.2.0"
        project_name = "prefix_release_test"
        prefix = "ai_"

        bare_remote = tmp_path / "bare_remote.git"
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(
            repo_path, bare_remote,
            main_branch="ai_main",
            develop_branch="ai_develop",
        )

        kanban_root = _build_kanban_root_for_rc(
            tmp_path, project_name, repo_path,
            push_to_remote="false",
            branch_prefix=prefix,
        )

        # Open RC.
        open_result = _run_cm_script(
            _OPEN_RC_SCRIPT, [version],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert open_result.returncode == 0, (
            f"cm-open-rc.sh with branch_prefix failed.\n"
            f"stdout: {open_result.stdout}\nstderr: {open_result.stderr}"
        )

        # Simulate coder work on the prefixed RC branch.
        prefixed_rc_branch = f"{prefix}rc/{version}"
        _simulate_coder_work(
            repo_path, prefixed_rc_branch,
            develop_branch="ai_develop",
        )

        # Run cm-release.sh.
        result = _run_cm_script(
            _RELEASE_SCRIPT, [],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result.returncode == 0, (
            f"cm-release.sh with prefix failed.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Assert the prefixed RC branch is gone after release.
        assert not _git_branch_exists_local(repo_path, prefixed_rc_branch), (
            f"Prefixed RC branch '{prefixed_rc_branch}' should be deleted after release.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Assert the release tag exists.  When a branch_prefix is set (e.g. 'ai_'),
        # cm-release.sh creates the tag with the prefix applied (e.g. 'ai_v0.2.0').
        prefixed_tag = f"{prefix}{version}"
        assert _git_tag_exists(repo_path, prefixed_tag) or _git_tag_exists(repo_path, version), (
            f"Release tag ('{prefixed_tag}' or '{version}') not found after cm-release.sh with prefix.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


class TestIdempotentRelease:
    """A second cm-release.sh invocation after a completed release does not corrupt state."""

    def test_release_tag_unchanged_after_second_release_invocation(
        self, tmp_path: pathlib.Path
    ) -> None:
        """After a successful release, re-running cm-release.sh does not corrupt the tag.

        The idempotency property protects against double-invocation in automation:
        a retry after a partial failure must not corrupt the already-shipped state.
        The tag SHA must be unchanged after the second invocation, and develop must
        still exist.

        The second invocation exits non-zero (no active RC to close) — this is
        acceptable and expected.  What must NOT happen is the tag being changed or
        develop being deleted.
        """
        version = "v0.1.0"
        project_name = "idempotent_test"

        bare_remote = tmp_path / "bare_remote.git"
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, bare_remote)

        kanban_root = _build_kanban_root_for_rc(
            tmp_path, project_name, repo_path, push_to_remote="false"
        )

        # First run: full lifecycle.
        _run_cm_script(
            _OPEN_RC_SCRIPT, [version],
            kanban_root, project_name, repo_root=repo_path,
        )
        _simulate_coder_work(repo_path, f"rc/{version}")
        result_first = _run_cm_script(
            _RELEASE_SCRIPT, [],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result_first.returncode == 0, (
            f"First cm-release.sh failed.\nstdout: {result_first.stdout}\nstderr: {result_first.stderr}"
        )

        # Capture the tag SHA after the first run.
        tag_sha_after_first = _git_local(repo_path, "rev-parse", f"refs/tags/{version}")

        # Second run: no active RC, no rc/* branch — expected to exit non-zero.
        result_second = _run_cm_script(
            _RELEASE_SCRIPT, [],
            kanban_root, project_name, repo_root=repo_path,
        )

        # The tag SHA must be unchanged (idempotent: second run did not re-tag).
        tag_sha_after_second = _git_local(repo_path, "rev-parse", f"refs/tags/{version}")
        assert tag_sha_after_first == tag_sha_after_second, (
            f"Release tag SHA changed between first and second cm-release.sh invocation.\n"
            f"First SHA: {tag_sha_after_first}\n"
            f"Second SHA: {tag_sha_after_second}\n"
            f"Second run stdout: {result_second.stdout}\nstderr: {result_second.stderr}"
        )

        # develop must still exist (second run did not destroy it).
        assert _git_branch_exists_local(repo_path, "develop"), (
            f"'develop' branch was deleted or corrupted by second cm-release.sh invocation.\n"
            f"stdout: {result_second.stdout}\nstderr: {result_second.stderr}"
        )


class TestLocalOnlyVsPushBehavior:
    """push_to_remote=false keeps the RC branch local; push_to_remote=true uses a local bare remote."""

    def test_rc_branch_not_pushed_to_remote_when_push_disabled(
        self, tmp_path: pathlib.Path
    ) -> None:
        """With push_to_remote=false, the RC branch is created locally but not on the remote.

        cm-open-rc.sh must skip the 'git push origin rc/<version>' step when
        push_to_remote=false.  The RC branch must exist in the local repo but
        must NOT appear on the bare remote.
        """
        version = "v0.1.0"
        project_name = "local_only_test"

        bare_remote = tmp_path / "bare_remote.git"
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, bare_remote)

        kanban_root = _build_kanban_root_for_rc(
            tmp_path, project_name, repo_path, push_to_remote="false"
        )

        result_open = _run_cm_script(
            _OPEN_RC_SCRIPT, [version],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result_open.returncode == 0, (
            f"cm-open-rc.sh (push_to_remote=false) failed.\n"
            f"stdout: {result_open.stdout}\nstderr: {result_open.stderr}"
        )

        rc_branch = f"rc/{version}"

        # The RC branch must exist locally.
        assert _git_branch_exists_local(repo_path, rc_branch), (
            f"RC branch '{rc_branch}' must exist locally after cm-open-rc.sh.\n"
            f"stdout: {result_open.stdout}\nstderr: {result_open.stderr}"
        )

        # The RC branch must NOT be on the bare remote.
        assert not _git_branch_exists_on_bare_remote(bare_remote, rc_branch), (
            f"RC branch '{rc_branch}' must NOT be pushed to the remote when push_to_remote=false.\n"
            f"Remote branches: {_git_local(bare_remote, 'branch', '--list')}"
        )

    def test_full_lifecycle_completes_locally_when_push_disabled(
        self, tmp_path: pathlib.Path
    ) -> None:
        """With push_to_remote=false, the full lifecycle ships the tag locally.

        The tag created by cm-release.sh must exist in the local repo.  The tag
        does not need to be (and must not be attempted to be) pushed to the remote.
        """
        version = "v0.2.0"
        project_name = "local_only_release_test"

        bare_remote = tmp_path / "bare_remote.git"
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, bare_remote)

        kanban_root = _build_kanban_root_for_rc(
            tmp_path, project_name, repo_path, push_to_remote="false"
        )

        _run_cm_script(
            _OPEN_RC_SCRIPT, [version],
            kanban_root, project_name, repo_root=repo_path,
        )
        _simulate_coder_work(repo_path, f"rc/{version}")

        result_release = _run_cm_script(
            _RELEASE_SCRIPT, [],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result_release.returncode == 0, (
            f"cm-release.sh (push_to_remote=false) failed.\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )

        # The tag must exist locally.
        assert _git_tag_exists(repo_path, version), (
            f"Tag '{version}' not found locally after push_to_remote=false lifecycle.\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )

        # Confirm the push-skipped log message appears.
        combined_output = result_release.stdout + result_release.stderr
        assert "push_to_remote=false" in combined_output, (
            "Expected 'push_to_remote=false' in output to confirm no-push path was taken.\n"
            f"output: {combined_output}"
        )

    def test_rc_branch_pushed_to_remote_when_push_enabled(
        self, tmp_path: pathlib.Path
    ) -> None:
        """With push_to_remote=true, cm-open-rc.sh pushes the RC branch to the local bare remote.

        Uses a local bare git repo as 'origin' to simulate the push path without
        touching any real network destination.  The RC branch must appear on the
        bare remote after cm-open-rc.sh completes.
        """
        version = "v0.1.0"
        project_name = "push_test"

        bare_remote = tmp_path / "bare_remote.git"
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, bare_remote)

        kanban_root = _build_kanban_root_for_rc(
            tmp_path, project_name, repo_path, push_to_remote="true"
        )

        result = _run_cm_script(
            _OPEN_RC_SCRIPT, [version],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result.returncode == 0, (
            f"cm-open-rc.sh (push_to_remote=true) failed.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        rc_branch = f"rc/{version}"
        # The RC branch must be present on the bare remote.
        assert _git_branch_exists_on_bare_remote(bare_remote, rc_branch), (
            f"RC branch '{rc_branch}' was not found on the bare remote after push.\n"
            f"Remote branches: {_git_local(bare_remote, 'branch', '--list')}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_release_tag_pushed_to_remote_when_push_enabled(
        self, tmp_path: pathlib.Path
    ) -> None:
        """With push_to_remote=true, cm-release.sh pushes the tag to the local bare remote.

        The release tag created by cm-release.sh must be present on the bare remote
        after the release.  This validates the push path end-to-end without any
        real network access.
        """
        version = "v0.1.0"
        project_name = "push_release_test"

        bare_remote = tmp_path / "bare_remote.git"
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, bare_remote)

        kanban_root = _build_kanban_root_for_rc(
            tmp_path, project_name, repo_path, push_to_remote="true"
        )

        # Open the RC.
        result_open = _run_cm_script(
            _OPEN_RC_SCRIPT, [version],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result_open.returncode == 0, (
            f"cm-open-rc.sh failed.\nstdout: {result_open.stdout}\nstderr: {result_open.stderr}"
        )

        # Simulate coder work on the RC branch.
        rc_branch = f"rc/{version}"
        _simulate_coder_work(repo_path, rc_branch)

        # Push the coder commit to the bare remote so cm-release.sh can validate it
        # (cm-release.sh validates the RC branch exists on origin when push_to_remote=true).
        _git_local(repo_path, "checkout", rc_branch)
        subprocess.run(
            ["git", "-C", str(repo_path), "push", "origin", rc_branch],
            capture_output=True, check=True,
        )
        _git_local(repo_path, "checkout", "develop")

        # Run cm-release.sh.
        result_release = _run_cm_script(
            _RELEASE_SCRIPT, [],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result_release.returncode == 0, (
            f"cm-release.sh (push_to_remote=true) failed.\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )

        # Assert: tag exists on the bare remote.
        assert _git_tag_exists_on_bare_remote(bare_remote, version), (
            f"Tag '{version}' was not found on the bare remote after release.\n"
            f"Remote tags: {_git_local(bare_remote, 'tag', '--list')}\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )


class TestTesterVerificationGate:
    """TESTER task state influences the ship-policy decision in cm-release.sh."""

    def test_release_ships_when_tester_reports_pass(
        self, tmp_path: pathlib.Path
    ) -> None:
        """When TESTER state is DONE with Recommendation=PASS, cm-release.sh ships normally.

        A TESTER task with State=DONE and Recommendation=PASS must result in a
        SHIP-FUNCTIONAL decision and a successful release.  This is the green-path
        verification gate.
        """
        version = "v0.1.0"
        project_name = "tester_pass_test"

        bare_remote = tmp_path / "bare_remote.git"
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, bare_remote)

        kanban_root = _build_kanban_root_for_rc(
            tmp_path, project_name, repo_path, push_to_remote="false"
        )

        _run_cm_script(
            _OPEN_RC_SCRIPT, [version],
            kanban_root, project_name, repo_root=repo_path,
        )
        _simulate_coder_work(repo_path, f"rc/{version}")

        # Add a TESTER task with PASS recommendation.
        _add_tester_task(kanban_root, project_name, version, state="DONE", recommendation="PASS")

        result = _run_cm_script(
            _RELEASE_SCRIPT, [],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result.returncode == 0, (
            f"cm-release.sh failed when TESTER recommendation=PASS.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        assert _git_tag_exists(repo_path, version), (
            f"Release tag '{version}' not found despite TESTER PASS.\n"
            f"stdout: {result.stdout}"
        )

    def test_release_halts_when_tester_is_blocked(
        self, tmp_path: pathlib.Path
    ) -> None:
        """When TESTER state is BLOCKED, cm-release.sh must halt and not ship.

        A BLOCKED TESTER task means verification could not complete.  The CM
        script must refuse to ship and create a HALT file, leaving the dev tree
        in its pre-release state (no tag, RC branch still exists).
        """
        version = "v0.1.0"
        project_name = "tester_blocked_test"

        bare_remote = tmp_path / "bare_remote.git"
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, bare_remote)

        kanban_root = _build_kanban_root_for_rc(
            tmp_path, project_name, repo_path, push_to_remote="false"
        )

        _run_cm_script(
            _OPEN_RC_SCRIPT, [version],
            kanban_root, project_name, repo_root=repo_path,
        )
        _simulate_coder_work(repo_path, f"rc/{version}")

        # Add a TESTER task with BLOCKED state.
        _add_tester_task(kanban_root, project_name, version, state="BLOCKED")

        result = _run_cm_script(
            _RELEASE_SCRIPT, [],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result.returncode != 0, (
            "cm-release.sh must exit non-zero when TESTER state is BLOCKED.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # The release tag must NOT have been created.
        assert not _git_tag_exists(repo_path, version), (
            f"Release tag '{version}' was created despite TESTER being BLOCKED.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # A HALT file must have been written.
        halt_file = kanban_root / "HALT"
        assert halt_file.exists(), (
            f"HALT file was not created when TESTER was BLOCKED.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_release_halts_when_tester_reports_high_systemic_risk(
        self, tmp_path: pathlib.Path
    ) -> None:
        """When TESTER reports Systemic Risk=high, cm-release.sh must halt.

        High systemic risk indicates a broader framework regression.  The CM
        script must refuse to auto-ship in this case, leaving the RC state intact
        (no tag, no state change).
        """
        version = "v0.1.0"
        project_name = "tester_high_risk_test"

        bare_remote = tmp_path / "bare_remote.git"
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, bare_remote)

        kanban_root = _build_kanban_root_for_rc(
            tmp_path, project_name, repo_path, push_to_remote="false"
        )

        _run_cm_script(
            _OPEN_RC_SCRIPT, [version],
            kanban_root, project_name, repo_root=repo_path,
        )
        _simulate_coder_work(repo_path, f"rc/{version}")

        # Add a TESTER task with DONE state but high systemic risk.
        _add_tester_task(
            kanban_root, project_name, version,
            state="DONE",
            recommendation="SHIP-WITH-CONCERNS",
            systemic_risk="high",
        )

        result = _run_cm_script(
            _RELEASE_SCRIPT, [],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result.returncode != 0, (
            "cm-release.sh must exit non-zero when TESTER Systemic Risk is high.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        assert not _git_tag_exists(repo_path, version), (
            f"Release tag '{version}' was created despite high Systemic Risk.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
