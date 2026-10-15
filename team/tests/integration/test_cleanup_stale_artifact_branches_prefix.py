"""
test_cleanup_stale_artifact_branches_prefix.py
================================================
Integration tests for team/scripts/
cleanup-stale-artifact-only-branches.sh's RC-branch auto-detection block.

The guarded defect: an auto-detection that hardcodes a literal "ai_rc/*"
glob check before falling back to a bare "rc/*" -- so a project configured
with any OTHER branch_prefix (not "ai_") can never auto-detect its own RC
branch at all, and the script exits 3 ("Could not determine RC branch")
even though a perfectly good, correctly prefixed RC branch existed.

Auto-detection now reads the project's own configured branch_prefix
straight from project.cfg (the same source pp_prefix_branch resolves from)
and globs "<branch_prefix>rc/*" first, falling back to a bare "rc/*" --
so it works for ANY configured prefix, not just "ai_", and stays
byte-identical for an unprefixed project.

Coverage:
  - A project configured with branch_prefix = "ai_" auto-detects its own
    ai_rc/<version> branch (regression coverage for the prior behavior).
  - A project configured with a DIFFERENT prefix (branch_prefix = "test_")
    auto-detects its own test_rc/<version> branch -- the case the
    hardcoded "ai_" literal could never handle.
  - An unprefixed project (no branch_prefix configured) auto-detects a
    bare rc/<version> branch exactly as before.
  - When no branch resolves under any glob, the script still exits 3 with
    its existing "Could not determine RC branch" remedy.
  - An explicit --rc override still short-circuits auto-detection.

Design notes:
  - All tests use tmp_path for scratch; no bare /tmp paths.
  - cleanup-stale-artifact-only-branches.sh is invoked via subprocess
    against a temporary kanban root and a temporary local git repo --
    never the live install.
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
    require_binary("git", label="cleanup-stale-artifact-branches-prefix")

# ---------------------------------------------------------------------------
# Path helpers -- resolve from this file's own location.
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_CLEANUP_SCRIPT = _TEAM_DIR / "scripts" / "cleanup-stale-artifact-only-branches.sh"


def _init_local_repo(repo_path: pathlib.Path) -> None:
    repo_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo_path, check=True)
    (repo_path / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo_path, check=True)


def _create_branch(repo_path: pathlib.Path, branch_name: str) -> None:
    subprocess.run(["git", "branch", branch_name], cwd=repo_path, check=True)


def _build_kanban_root(
    parent: pathlib.Path,
    project_name: str,
    repo_path: pathlib.Path,
    branch_prefix: str = "",
) -> pathlib.Path:
    root = parent / "kanban_root"
    (root / "logs").mkdir(parents=True, exist_ok=True)
    proj = root / "projects" / project_name
    (proj / "tasks").mkdir(parents=True, exist_ok=True)
    cfg_lines = [
        "[project]",
        f"project_name = {project_name}",
        f"dev_tree_path = {repo_path}",
        "workflow_type = release",
    ]
    if branch_prefix:
        cfg_lines.append(f"branch_prefix = {branch_prefix}")
    (proj / "project.cfg").write_text("\n".join(cfg_lines) + "\n", encoding="utf-8")
    return root


def _run_cleanup(
    kanban_root: pathlib.Path, project_root: pathlib.Path, repo_path: pathlib.Path
) -> subprocess.CompletedProcess:
    base_env = dict(os.environ)
    base_env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    base_env.pop("PGAI_DEV_TREE_PATH", None)
    base_env.pop("PGAI_PROJECT_ROOT", None)
    return subprocess.run(
        [
            "bash", str(_CLEANUP_SCRIPT),
            "--kanban-root", str(kanban_root),
            "--project-root", str(project_root),
        ],
        capture_output=True,
        text=True,
        env=base_env,
        cwd=str(repo_path),
        timeout=30,
    )


def test_auto_detects_rc_branch_for_ai_prefix(tmp_path: pathlib.Path) -> None:
    """A project configured with branch_prefix = 'ai_' auto-detects its own
    ai_rc/<version> branch (the default-prefix case, which must keep working)."""
    repo = tmp_path / "repo"
    _init_local_repo(repo)
    _create_branch(repo, "ai_rc/v1.2.3")
    project_root = (
        _build_kanban_root(tmp_path, "cleanup-test-ai", repo, "ai_")
        / "projects" / "cleanup-test-ai"
    )

    result = _run_cleanup(project_root.parent.parent, project_root, repo)

    assert "RC branch: ai_rc/v1.2.3" in result.stdout, result.stdout + result.stderr
    assert result.returncode == 0, result.stdout + result.stderr


def test_auto_detects_rc_branch_for_a_non_ai_prefix(tmp_path: pathlib.Path) -> None:
    """A project configured with a DIFFERENT prefix (not 'ai_')
    auto-detects its own <prefix>rc/<version> branch -- the case the
    prior hardcoded 'ai_rc/*' literal could never handle."""
    repo = tmp_path / "repo"
    _init_local_repo(repo)
    _create_branch(repo, "test_rc/v4.5.6")
    project_root = (
        _build_kanban_root(tmp_path, "cleanup-test-other", repo, "test_")
        / "projects" / "cleanup-test-other"
    )

    result = _run_cleanup(project_root.parent.parent, project_root, repo)

    assert "RC branch: test_rc/v4.5.6" in result.stdout, result.stdout + result.stderr
    assert result.returncode == 0, result.stdout + result.stderr


def test_auto_detects_bare_rc_branch_for_unprefixed_project(
    tmp_path: pathlib.Path,
) -> None:
    """No branch_prefix configured -- auto-detection still resolves a bare
    rc/<version> branch exactly as before this change."""
    repo = tmp_path / "repo"
    _init_local_repo(repo)
    _create_branch(repo, "rc/v7.8.9")
    project_root = (
        _build_kanban_root(tmp_path, "cleanup-test-unprefixed", repo)
        / "projects" / "cleanup-test-unprefixed"
    )

    result = _run_cleanup(project_root.parent.parent, project_root, repo)

    assert "RC branch: rc/v7.8.9" in result.stdout, result.stdout + result.stderr
    assert result.returncode == 0, result.stdout + result.stderr


def test_exits_3_with_existing_remedy_when_nothing_resolves(
    tmp_path: pathlib.Path,
) -> None:
    """No branch of any shape exists -- the script still exits 3 with its
    existing 'Could not determine RC branch' remedy, unchanged."""
    repo = tmp_path / "repo"
    _init_local_repo(repo)
    project_root = (
        _build_kanban_root(tmp_path, "cleanup-test-none", repo, "ai_")
        / "projects" / "cleanup-test-none"
    )

    result = _run_cleanup(project_root.parent.parent, project_root, repo)

    assert result.returncode == 3, result.stdout + result.stderr
    assert "Could not determine RC branch" in result.stderr, result.stderr
    assert "--rc BRANCH explicitly" in result.stderr, result.stderr


def test_explicit_rc_override_short_circuits_auto_detection(
    tmp_path: pathlib.Path,
) -> None:
    """An explicit --rc still wins outright, regardless of any configured
    branch_prefix or which branches exist locally."""
    repo = tmp_path / "repo"
    _init_local_repo(repo)
    _create_branch(repo, "ai_rc/v1.0.0")
    _create_branch(repo, "custom/override-branch")
    kanban_root = _build_kanban_root(tmp_path, "cleanup-test-override", repo, "ai_")
    project_root = kanban_root / "projects" / "cleanup-test-override"

    base_env = dict(os.environ)
    base_env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    base_env.pop("PGAI_DEV_TREE_PATH", None)
    base_env.pop("PGAI_PROJECT_ROOT", None)
    result = subprocess.run(
        [
            "bash", str(_CLEANUP_SCRIPT),
            "--kanban-root", str(kanban_root),
            "--project-root", str(project_root),
            "--rc", "custom/override-branch",
        ],
        capture_output=True,
        text=True,
        env=base_env,
        cwd=str(repo),
        timeout=30,
    )

    assert "RC branch: custom/override-branch" in result.stdout, (
        result.stdout + result.stderr
    )
