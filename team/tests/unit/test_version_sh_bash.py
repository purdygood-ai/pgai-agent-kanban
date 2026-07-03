"""
test_version_sh_bash.py
=======================
Behavioral unit tests for team/scripts/dashboard/lib/version.sh.

Tests source the shell library and invoke get_latest_released_tag() via the
bash harness.  All git operations use throwaway git repositories built under
tmp_path (pytest-managed, respects PGAI_AGENT_KANBAN_TEMP_DIR) — the live dev
tree and live kanban install are never read or written.

Key behavioral requirement covered:
  get_latest_released_tag() returns the latest tag merged into origin/main
  independently of which branch is currently checked out.  This is the
  regression fixture for BUG-0017, which confirmed that the old `git describe
  HEAD` query returned an OLDER tag when the dev tree was on `develop` even
  though the latest released tag was visible via `git tag --merged origin/main`.

Fixture design
--------------
The git fixture reproduces tonight's exact state (per BUG-0017 "Notes for TESTER"):

  1. Create a local git repo with two commits.
  2. Tag the first commit v0.101.2 (older tag, reachable from the feature branch).
  3. Tag the second commit v0.106.3 (latest tag, merged into main).
  4. Set refs/remotes/origin/main to point at the second commit — this makes
     `git tag --merged origin/main` work without a real remote or network call.
  5. Check out a branch rooted at the FIRST commit (simulating develop's HEAD
     being behind main).  In this state:
       - git describe --tags --abbrev=0 HEAD → v0.101.2   (branch-sensitive, WRONG)
       - git tag --merged origin/main | sort -V | tail -1 → v0.106.3  (correct)
  6. Assert get_latest_released_tag() returns v0.106.3.

This is the honest proof: same repo, two branches, the resolver must return the
same latest tag regardless of HEAD position.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from tests.unit.shell_harness import run_bash

# Path to the library under test, relative to the team/ directory where pytest runs.
_LIB = "scripts/dashboard/lib/version.sh"


# ---------------------------------------------------------------------------
# Shared git fixture helper
# ---------------------------------------------------------------------------


def _build_branch_sensitivity_fixture(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a throwaway git repo that reproduces the branch-sensitivity condition.

    Returns the path to the repo root.  After this call:
      - The repo contains two commits: commit A (older) and commit B (newer).
      - v0.101.2 is tagged on commit A.
      - v0.106.3 is tagged on commit B.
      - refs/remotes/origin/main points at commit B (the newer commit).
      - HEAD is on branch 'develop-sim', rooted at commit A (the older commit).

    In this state:
      git describe --tags --abbrev=0 HEAD   → v0.101.2  (wrong; branch-sensitive)
      git tag --merged origin/main | sort -V | tail -1  → v0.106.3  (correct)

    The fixture never talks to a real remote — it uses git update-ref to set the
    local remote-tracking ref directly.

    Parameters
    ----------
    tmp_path:
        Directory under which the repo is created.  Must be a pytest-managed
        path (PGAI_AGENT_KANBAN_TEMP_DIR is honoured via pytest_configure).

    Returns
    -------
    pathlib.Path
        Absolute path to the git repo root.
    """
    repo = tmp_path / "fixture_repo"
    repo.mkdir()

    def _git(*args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    # Initialise with a deterministic main branch name.
    _git("init", "-b", "main")
    _git("config", "user.email", "test@example.com")
    _git("config", "user.name", "Test")

    # Commit A: tagged as the OLDER version (what develop's describe resolves to).
    (repo / "a.txt").write_text("commit A\n", encoding="utf-8")
    _git("add", "a.txt")
    _git("commit", "-m", "Commit A — older tag")
    _git("tag", "v0.101.2")
    older_sha = _git("rev-parse", "HEAD")

    # Commit B: tagged as the NEWER version (the latest released tag, on main).
    (repo / "b.txt").write_text("commit B\n", encoding="utf-8")
    _git("add", "b.txt")
    _git("commit", "-m", "Commit B — newer tag (latest released)")
    _git("tag", "v0.106.3")
    main_sha = _git("rev-parse", "HEAD")

    # Set the remote-tracking ref directly so `git tag --merged origin/main` works
    # without a real remote or any network call.
    _git("update-ref", "refs/remotes/origin/main", main_sha)

    # Check out a branch rooted at the OLDER commit — simulating develop's HEAD.
    # On this branch, git describe --tags --abbrev=0 HEAD returns v0.101.2 (wrong).
    _git("checkout", "-b", "develop-sim", older_sha)

    return repo


# ---------------------------------------------------------------------------
# Core regression test: branch independence
# ---------------------------------------------------------------------------


def test_get_latest_released_tag_returns_newest_tag_independent_of_branch(
    tmp_path: pathlib.Path,
) -> None:
    """get_latest_released_tag returns the latest main-merged tag regardless of branch.

    This is the regression fixture for BUG-0017.  With HEAD on a branch where
    `git describe HEAD` resolves to an older tag (v0.101.2), the function must
    still return the latest main-merged tag (v0.106.3).

    The git fixture reproduces tonight's exact broken condition:
      - HEAD on develop-sim (rooted at the older commit)
      - v0.101.2 reachable from HEAD via git describe
      - v0.106.3 merged into origin/main but NOT reachable from HEAD via describe

    The assertion proves branch independence: the resolver sees the same latest
    tag regardless of which branch HEAD is on.
    """
    repo = _build_branch_sensitivity_fixture(tmp_path)

    # Sanity-check the fixture state before asserting on get_latest_released_tag.
    # git describe from HEAD (develop-sim, older commit) must return the OLDER tag.
    describe_result = subprocess.run(
        ["git", "-C", str(repo), "describe", "--tags", "--abbrev=0", "HEAD"],
        capture_output=True,
        text=True,
    )
    assert describe_result.returncode == 0, (
        f"Fixture setup error: git describe failed: {describe_result.stderr}"
    )
    assert describe_result.stdout.strip() == "v0.101.2", (
        f"Fixture must have describe returning v0.101.2 from HEAD, "
        f"got: {describe_result.stdout.strip()!r}"
    )

    # Now verify get_latest_released_tag() returns the latest main-merged tag,
    # NOT the branch-sensitive describe result.
    result = run_bash(
        tmp_path,
        f"source {_LIB} && get_latest_released_tag '{repo}'",
    )
    assert result.returncode == 0, f"get_latest_released_tag exited non-zero: {result.stderr}"
    resolved = result.stdout.strip()
    assert resolved == "v0.106.3", (
        f"get_latest_released_tag must return the latest main-merged tag (v0.106.3) "
        f"even when HEAD is on a branch where git describe returns v0.101.2; "
        f"got: {resolved!r}"
    )


# ---------------------------------------------------------------------------
# Guard: empty / non-git directory
# ---------------------------------------------------------------------------


def test_get_latest_released_tag_returns_empty_for_non_git_directory(
    tmp_path: pathlib.Path,
) -> None:
    """get_latest_released_tag returns empty string and exits 0 for a non-git directory.

    Best-effort contract: the function never fails the caller; it returns empty
    when the directory is not a valid git repo.
    """
    not_a_repo = tmp_path / "not_a_git_repo"
    not_a_repo.mkdir()

    result = run_bash(
        tmp_path,
        f"source {_LIB} && get_latest_released_tag '{not_a_repo}'",
    )
    assert result.returncode == 0, (
        f"get_latest_released_tag must exit 0 (best-effort) for a non-git directory; "
        f"exited {result.returncode}: {result.stderr}"
    )
    assert result.stdout.strip() == "", (
        f"get_latest_released_tag must return empty string for a non-git directory; "
        f"got: {result.stdout.strip()!r}"
    )


def test_get_latest_released_tag_returns_empty_for_missing_directory(
    tmp_path: pathlib.Path,
) -> None:
    """get_latest_released_tag returns empty string and exits 0 for a missing path."""
    absent = tmp_path / "no_such_dir"
    # Deliberately do NOT create the directory.

    result = run_bash(
        tmp_path,
        f"source {_LIB} && get_latest_released_tag '{absent}'",
    )
    assert result.returncode == 0, (
        f"get_latest_released_tag must exit 0 (best-effort) for a missing directory; "
        f"exited {result.returncode}: {result.stderr}"
    )
    assert result.stdout.strip() == "", (
        f"get_latest_released_tag must return empty string for a missing directory; "
        f"got: {result.stdout.strip()!r}"
    )


# ---------------------------------------------------------------------------
# Correctness: only main-merged tags are returned
# ---------------------------------------------------------------------------


def test_get_latest_released_tag_excludes_tags_not_merged_into_main(
    tmp_path: pathlib.Path,
) -> None:
    """get_latest_released_tag returns only tags merged into origin/main.

    Tags on unmerged branches are excluded from the result.  This verifies
    the `--merged origin/main` filter is active and not bypassed.
    """
    repo = tmp_path / "filter_test_repo"
    repo.mkdir()

    def _git(*args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    _git("init", "-b", "main")
    _git("config", "user.email", "test@example.com")
    _git("config", "user.name", "Test")

    # Commit A: tagged v1.0.0 — will be merged into origin/main.
    (repo / "a.txt").write_text("A\n", encoding="utf-8")
    _git("add", "a.txt")
    _git("commit", "-m", "A")
    _git("tag", "v1.0.0")
    main_sha = _git("rev-parse", "HEAD")

    # Set origin/main to the commit that has v1.0.0.
    _git("update-ref", "refs/remotes/origin/main", main_sha)

    # Commit B on a separate branch: tagged v2.0.0 — NOT merged into origin/main.
    _git("checkout", "-b", "feature-not-merged", main_sha)
    (repo / "b.txt").write_text("B\n", encoding="utf-8")
    _git("add", "b.txt")
    _git("commit", "-m", "B — unmerged feature")
    _git("tag", "v2.0.0")

    # The resolver must return v1.0.0 (merged) and NOT v2.0.0 (unmerged).
    result = run_bash(
        tmp_path,
        f"source {_LIB} && get_latest_released_tag '{repo}'",
    )
    assert result.returncode == 0, f"get_latest_released_tag exited non-zero: {result.stderr}"
    resolved = result.stdout.strip()
    assert resolved == "v1.0.0", (
        f"get_latest_released_tag must return only tags merged into origin/main; "
        f"v2.0.0 is on an unmerged branch and must be excluded; "
        f"got: {resolved!r}"
    )


# ---------------------------------------------------------------------------
# Correctness: highest semver wins (not lexicographic order)
# ---------------------------------------------------------------------------


def test_get_latest_released_tag_returns_highest_semver_not_lexicographic(
    tmp_path: pathlib.Path,
) -> None:
    """get_latest_released_tag returns the highest semver tag, not the last lexicographic.

    v0.10.0 sorts AFTER v0.9.0 numerically but BEFORE v0.9.0 lexicographically.
    The resolver must use `sort -V` (version sort) to pick the correct winner.
    """
    repo = tmp_path / "semver_order_repo"
    repo.mkdir()

    def _git(*args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    _git("init", "-b", "main")
    _git("config", "user.email", "test@example.com")
    _git("config", "user.name", "Test")

    # First commit: v0.9.0
    (repo / "a.txt").write_text("A\n", encoding="utf-8")
    _git("add", "a.txt")
    _git("commit", "-m", "A")
    _git("tag", "v0.9.0")

    # Second commit: v0.10.0 — higher numerically but lower lexicographically.
    (repo / "b.txt").write_text("B\n", encoding="utf-8")
    _git("add", "b.txt")
    _git("commit", "-m", "B")
    _git("tag", "v0.10.0")
    latest_sha = _git("rev-parse", "HEAD")

    _git("update-ref", "refs/remotes/origin/main", latest_sha)

    result = run_bash(
        tmp_path,
        f"source {_LIB} && get_latest_released_tag '{repo}'",
    )
    assert result.returncode == 0, f"get_latest_released_tag exited non-zero: {result.stderr}"
    resolved = result.stdout.strip()
    assert resolved == "v0.10.0", (
        f"get_latest_released_tag must use version sort (sort -V), not lexicographic sort; "
        f"v0.10.0 > v0.9.0 numerically; got: {resolved!r}"
    )
