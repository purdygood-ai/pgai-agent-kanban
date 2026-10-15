"""
test_ops_write_branch_prefix.py
=================================
Behavioral unit tests for pgai_agent_kanban.ops.
write._warn_if_merged, which previously scanned a hardcoded 'rc/*' glob
regardless of the project's configured branch_prefix -- so on a prefixed
project (where the RC branch is named '<prefix>rc/vX.Y.Z', e.g.
'ai_rc/v2.11.1'), the already-merged warning could never fire at all.

_warn_if_merged now takes an optional branch_prefix argument and scans
'<branch_prefix>rc/*', defaulting to '' (identity, byte-identical to the
old hardcoded 'rc/*' for an unprefixed project).

All tests build real, throwaway local git repositories under tmp_path and
exercise the real production function -- no reimplementation, no mocked
git.

Naming: function names describe behavior under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from pgai_agent_kanban.ops.write import _warn_if_merged, _git_delete_feature_branch
from tests.fixtures.binary_guard import require_binary


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Every test here builds, inspects, or drives a real git repository; a
    module-wide autouse guard keeps that requirement in one place instead
    of repeating a per-test check at each git-dependent call site.
    """
    require_binary("git", label="ops-write-branch-prefix")


def _git(repo: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )


def _build_repo_with_merged_feature_branch(
    tmp_path: pathlib.Path, rc_branch_name: str
) -> tuple[pathlib.Path, str]:
    """Build a repo with an RC branch and a feature branch already merged
    (via --no-ff) into it. Returns (repo_path, feature_branch_name)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "checkout", "-q", "-b", rc_branch_name)
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "initial")

    feature_branch = "feature/CODER-20260101-999-fixture"
    _git(repo, "checkout", "-q", "-b", feature_branch)
    (repo / "work.txt").write_text("work\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "feature work")

    _git(repo, "checkout", "-q", rc_branch_name)
    _git(
        repo, "-c", "commit.gpgsign=false", "merge", "-q", "--no-ff",
        "-m", f"Merge {feature_branch}", feature_branch,
    )
    return repo, feature_branch


# ---------------------------------------------------------------------------
# _warn_if_merged
# ---------------------------------------------------------------------------


def test_warn_if_merged_fires_on_unprefixed_rc_branch(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No branch_prefix argument (default '') -- an unprefixed 'rc/vX.Y.Z'
    branch is scanned by the bare 'rc/*' match, so the warning
    fires when the feature branch is already merged into it."""
    repo, feature_branch = _build_repo_with_merged_feature_branch(tmp_path, "rc/v1.0.0")

    _warn_if_merged(repo, feature_branch)

    captured = capsys.readouterr()
    assert "already merged into" in captured.err
    assert "rc/v1.0.0" in captured.err


def test_warn_if_merged_fires_on_prefixed_rc_branch(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """On a prefixed project (branch_prefix='ai_'), the RC branch
    is named 'ai_rc/vX.Y.Z' -- passing branch_prefix='ai_' makes the scan
    look for 'ai_rc/*' instead of a bare 'rc/*' that would never match, so
    the already-merged warning fires here too."""
    repo, feature_branch = _build_repo_with_merged_feature_branch(tmp_path, "ai_rc/v2.11.1")

    _warn_if_merged(repo, feature_branch, "ai_")

    captured = capsys.readouterr()
    assert "already merged into" in captured.err
    assert "ai_rc/v2.11.1" in captured.err


def test_warn_if_merged_does_not_false_positive_on_unrelated_prefix(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A prefixed RC branch scanned WITHOUT the matching branch_prefix
    (the default '') never matches 'ai_rc/*' via a bare 'rc/*'
    glob -- confirms the match is branch_prefix-driven, not accidental."""
    repo, feature_branch = _build_repo_with_merged_feature_branch(tmp_path, "ai_rc/v2.11.1")

    _warn_if_merged(repo, feature_branch)  # branch_prefix omitted -> ""

    captured = capsys.readouterr()
    assert "already merged into" not in captured.err


def test_warn_if_merged_silent_when_not_merged(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A feature branch that diverged from (but was never merged into) the
    RC branch produces no warning, prefixed or not."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "checkout", "-q", "-b", "ai_rc/v3.0.0")
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "initial")
    feature_branch = "feature/CODER-20260101-998-unmerged"
    _git(repo, "checkout", "-q", "-b", feature_branch)
    (repo / "work.txt").write_text("work\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "unmerged work")

    _warn_if_merged(repo, feature_branch, "ai_")

    captured = capsys.readouterr()
    assert "already merged into" not in captured.err


# ---------------------------------------------------------------------------
# _git_delete_feature_branch threads branch_prefix through to _warn_if_merged
# ---------------------------------------------------------------------------


def test_git_delete_feature_branch_threads_prefix_to_warn_if_merged(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """_git_delete_feature_branch's optional branch_prefix argument reaches
    _warn_if_merged, so the already-merged warning fires (and the branch
    is deleted with -d, since it IS fully merged) on a prefixed project."""
    repo, feature_branch = _build_repo_with_merged_feature_branch(tmp_path, "ai_rc/v2.11.1")

    _git_delete_feature_branch(repo, feature_branch, "ai_")

    captured = capsys.readouterr()
    assert "already merged into" in captured.err
    assert "ai_rc/v2.11.1" in captured.err

    exists = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", f"refs/heads/{feature_branch}"],
        capture_output=True,
    )
    assert exists.returncode != 0, "the feature branch should have been deleted"
