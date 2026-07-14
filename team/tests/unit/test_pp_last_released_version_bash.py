"""
test_pp_last_released_version_bash.py
======================================
Behavioral unit tests for the pp_last_released_version function in
team/scripts/lib/project_paths.sh.

Fixtures exercised:
  (a)  Prefixed project with real tags, no release-state record — resolver
       returns the highest tag stripped of its prefix (not the sentinel).
       A stale v0.9.0 requirement would be REJECTED by the acceptance check.
  (a') Unprefixed project with real tags — resolver returns the bare semver.
  (b)  Tagless repo — resolver returns v0.0.0 sentinel; principle-9 first-doc
       behavior is preserved for genuinely fresh repos.
  (c)  release-state.md has a shipped record — state wins unchanged (no
       regression from changing the tag-scan fallback path).

These tests use the bash harness with synthetic git repos in tmp_path so they
never touch the live kanban root or any real dev tree.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from tests.unit.shell_harness import run_bash

_LIB = "scripts/lib/project_paths.sh"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project_cfg(
    proj_dir: pathlib.Path,
    dev_tree: pathlib.Path,
    branch_prefix: str = "",
) -> None:
    """Write a minimal project.cfg pointing at the given dev_tree."""
    lines = ["[project]\n", f"project_name={proj_dir.name}\n",
             f"dev_tree_path={dev_tree}\n"]
    if branch_prefix:
        lines.append(f"branch_prefix={branch_prefix}\n")
    (proj_dir / "project.cfg").write_text("".join(lines), encoding="utf-8")


def _git(repo: pathlib.Path, *args: str) -> str:
    """Run a git command inside repo and return stripped stdout."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _make_git_repo(repo: pathlib.Path) -> None:
    """Initialise a bare git repo with an initial commit (no remote)."""
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")


def _make_tag(repo: pathlib.Path, tag: str) -> None:
    """Create an annotated tag on HEAD of repo."""
    _git(repo, "tag", "-a", tag, "-m", f"release {tag}")


def _ensure_branch(repo: pathlib.Path, branch: str) -> None:
    """Create (or reset) a local branch pointing at HEAD."""
    try:
        _git(repo, "branch", branch)
    except subprocess.CalledProcessError:
        _git(repo, "branch", "-f", branch)


def _run(
    tmp_path: pathlib.Path,
    kanban_root: pathlib.Path,
    func_call: str,
    **extra_kv: str,
) -> object:
    """Source project_paths.sh and run func_call with KANBAN_ROOT pointing at kanban_root."""
    env = {"KANBAN_ROOT": str(kanban_root)}
    env.update(extra_kv)
    return run_bash(tmp_path, f"source {_LIB} && {func_call}", extra_env=env)


# ---------------------------------------------------------------------------
# Fixture (a) — prefixed project with tags, no shipped release-state record
# ---------------------------------------------------------------------------


def test_prefixed_project_returns_highest_tag_not_sentinel(
    tmp_path: pathlib.Path,
) -> None:
    """(a) Prefixed project with ai_v1.2.0 as newest tag returns v1.2.0, not v0.0.0."""
    kanban_root = tmp_path / "kanban"
    dev_tree = tmp_path / "dev"

    # Build a synthetic git repo with prefixed release tags.
    _make_git_repo(dev_tree)
    _make_tag(dev_tree, "ai_v1.0.0")

    # Add another commit and the newer tag.
    (dev_tree / "file.txt").write_text("change\n", encoding="utf-8")
    _git(dev_tree, "add", ".")
    _git(dev_tree, "commit", "-m", "bump")
    _make_tag(dev_tree, "ai_v1.2.0")

    # Ensure ai_main branch exists pointing at HEAD (Tier 2 lookup).
    _ensure_branch(dev_tree, "ai_main")

    # Set up kanban with a project pointing at this dev tree.
    proj_dir = kanban_root / "projects" / "myproject"
    proj_dir.mkdir(parents=True)
    _make_project_cfg(proj_dir, dev_tree, branch_prefix="ai_")

    result = _run(tmp_path, kanban_root, "pp_last_released_version myproject")
    assert result.returncode == 0
    assert result.stdout.strip() == "v1.2.0"


def test_prefixed_project_rejects_stale_version_requirement(
    tmp_path: pathlib.Path,
) -> None:
    """(a) With floor v1.2.0, a v0.9.0 requirement does NOT satisfy semver_gt floor."""
    kanban_root = tmp_path / "kanban"
    dev_tree = tmp_path / "dev"

    _make_git_repo(dev_tree)
    _make_tag(dev_tree, "ai_v1.2.0")
    _ensure_branch(dev_tree, "ai_main")

    proj_dir = kanban_root / "projects" / "myproject"
    proj_dir.mkdir(parents=True)
    _make_project_cfg(proj_dir, dev_tree, branch_prefix="ai_")

    # Check that v0.9.0 is NOT greater than v1.2.0 (semver_gt v0.9.0 v1.2.0 -> false).
    result = run_bash(
        tmp_path,
        f"source scripts/lib/semver.sh && "
        f"source {_LIB} && "
        f"KANBAN_ROOT={kanban_root} "
        f"floor=$(pp_last_released_version myproject) && "
        f"if semver_gt v0.9.0 \"$floor\"; then echo ACCEPTED; else echo REJECTED; fi",
        extra_env={"KANBAN_ROOT": str(kanban_root)},
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "REJECTED", (
        f"v0.9.0 should be REJECTED when floor is {result.stdout.strip()!r}"
    )


# ---------------------------------------------------------------------------
# Fixture (a') — unprefixed project with real tags
# ---------------------------------------------------------------------------


def test_unprefixed_project_returns_highest_tag(
    tmp_path: pathlib.Path,
) -> None:
    """(a') Unprefixed project with v1.0.0 returns v1.0.0."""
    kanban_root = tmp_path / "kanban"
    dev_tree = tmp_path / "dev"

    _make_git_repo(dev_tree)
    _make_tag(dev_tree, "v0.9.0")

    (dev_tree / "file.txt").write_text("change\n", encoding="utf-8")
    _git(dev_tree, "add", ".")
    _git(dev_tree, "commit", "-m", "bump")
    _make_tag(dev_tree, "v1.0.0")

    # Ensure a local main branch for the Tier 2 lookup.
    _ensure_branch(dev_tree, "main")

    proj_dir = kanban_root / "projects" / "plainproject"
    proj_dir.mkdir(parents=True)
    _make_project_cfg(proj_dir, dev_tree, branch_prefix="")

    result = _run(tmp_path, kanban_root, "pp_last_released_version plainproject")
    assert result.returncode == 0
    assert result.stdout.strip() == "v1.0.0"


def test_unprefixed_project_ignores_prefixed_tags(
    tmp_path: pathlib.Path,
) -> None:
    """(a') Unprefixed project ignores tags that carry a prefix like ai_v2.0.0."""
    kanban_root = tmp_path / "kanban"
    dev_tree = tmp_path / "dev"

    _make_git_repo(dev_tree)
    _make_tag(dev_tree, "v1.0.0")

    (dev_tree / "file.txt").write_text("change\n", encoding="utf-8")
    _git(dev_tree, "add", ".")
    _git(dev_tree, "commit", "-m", "bump")
    # Tag from a different lane (should be excluded when prefix is empty).
    _make_tag(dev_tree, "ai_v2.0.0")

    _ensure_branch(dev_tree, "main")

    proj_dir = kanban_root / "projects" / "plainproject"
    proj_dir.mkdir(parents=True)
    _make_project_cfg(proj_dir, dev_tree, branch_prefix="")

    result = _run(tmp_path, kanban_root, "pp_last_released_version plainproject")
    assert result.returncode == 0
    assert result.stdout.strip() == "v1.0.0"


# ---------------------------------------------------------------------------
# Fixture (b) — tagless repo returns v0.0.0 sentinel
# ---------------------------------------------------------------------------


def test_tagless_repo_returns_sentinel(
    tmp_path: pathlib.Path,
) -> None:
    """(b) Repo with no release tags returns v0.0.0 sentinel (principle-9 guard)."""
    kanban_root = tmp_path / "kanban"
    dev_tree = tmp_path / "dev"

    _make_git_repo(dev_tree)
    # No tags — genuinely fresh.
    _ensure_branch(dev_tree, "ai_main")

    proj_dir = kanban_root / "projects" / "freshproject"
    proj_dir.mkdir(parents=True)
    _make_project_cfg(proj_dir, dev_tree, branch_prefix="ai_")

    result = _run(tmp_path, kanban_root, "pp_last_released_version freshproject")
    assert result.returncode == 0
    assert result.stdout.strip() == "v0.0.0", (
        "tagless repo must return sentinel so first-doc-declares principle is preserved"
    )


def test_tagless_unprefixed_repo_returns_sentinel(
    tmp_path: pathlib.Path,
) -> None:
    """(b) Tagless unprefixed repo also returns v0.0.0 sentinel."""
    kanban_root = tmp_path / "kanban"
    dev_tree = tmp_path / "dev"

    _make_git_repo(dev_tree)
    _ensure_branch(dev_tree, "main")

    proj_dir = kanban_root / "projects" / "plainfresh"
    proj_dir.mkdir(parents=True)
    _make_project_cfg(proj_dir, dev_tree, branch_prefix="")

    result = _run(tmp_path, kanban_root, "pp_last_released_version plainfresh")
    assert result.returncode == 0
    assert result.stdout.strip() == "v0.0.0"


# ---------------------------------------------------------------------------
# Fixture (c) — release-state.md shipped record present, state wins
# ---------------------------------------------------------------------------


def test_release_state_shipped_record_returned_for_no_git_project(
    tmp_path: pathlib.Path,
) -> None:
    """(c) No dev_tree in config — release-state.md Last Released is the source."""
    kanban_root = tmp_path / "kanban"

    proj_dir = kanban_root / "projects" / "docproject"
    proj_dir.mkdir(parents=True)
    # project.cfg without dev_tree_path — document-workflow project.
    (proj_dir / "project.cfg").write_text(
        "[project]\nproject_name=docproject\n",
        encoding="utf-8",
    )
    (proj_dir / "release-state.md").write_text(
        "## Active RC\nnone\n\n## Last Released\nv2.3.4\n",
        encoding="utf-8",
    )

    result = _run(tmp_path, kanban_root, "pp_last_released_version docproject")
    assert result.returncode == 0
    assert result.stdout.strip() == "v2.3.4"


def test_git_project_with_tags_ignores_release_state(
    tmp_path: pathlib.Path,
) -> None:
    """(c) For a git project, the tag scan is authoritative even when release-state exists."""
    kanban_root = tmp_path / "kanban"
    dev_tree = tmp_path / "dev"

    _make_git_repo(dev_tree)
    _make_tag(dev_tree, "ai_v1.5.0")
    _ensure_branch(dev_tree, "ai_main")

    proj_dir = kanban_root / "projects" / "gitproject"
    proj_dir.mkdir(parents=True)
    _make_project_cfg(proj_dir, dev_tree, branch_prefix="ai_")
    # release-state.md exists but has no Last Released field (simulates re-registered project).
    (proj_dir / "release-state.md").write_text(
        "## Active RC\nnone\n\n## RC Opened At\nnone\n",
        encoding="utf-8",
    )

    result = _run(tmp_path, kanban_root, "pp_last_released_version gitproject")
    assert result.returncode == 0
    assert result.stdout.strip() == "v1.5.0", (
        "git project must use tag scan even when release-state has no Last Released"
    )


def test_tags_sorted_by_semver_not_lexicographically(
    tmp_path: pathlib.Path,
) -> None:
    """Tags are sorted by semantic version, so ai_v1.10.0 beats ai_v1.9.0."""
    kanban_root = tmp_path / "kanban"
    dev_tree = tmp_path / "dev"

    _make_git_repo(dev_tree)
    _make_tag(dev_tree, "ai_v1.2.0")

    (dev_tree / "f1.txt").write_text("x\n", encoding="utf-8")
    _git(dev_tree, "add", ".")
    _git(dev_tree, "commit", "-m", "bump")
    _make_tag(dev_tree, "ai_v1.9.0")

    (dev_tree / "f2.txt").write_text("y\n", encoding="utf-8")
    _git(dev_tree, "add", ".")
    _git(dev_tree, "commit", "-m", "bump2")
    _make_tag(dev_tree, "ai_v1.10.0")

    _ensure_branch(dev_tree, "ai_main")

    proj_dir = kanban_root / "projects" / "semverproject"
    proj_dir.mkdir(parents=True)
    _make_project_cfg(proj_dir, dev_tree, branch_prefix="ai_")

    result = _run(tmp_path, kanban_root, "pp_last_released_version semverproject")
    assert result.returncode == 0
    assert result.stdout.strip() == "v1.10.0", (
        "v1.10.0 must win over v1.9.0 in semver sort (not lexicographic)"
    )
