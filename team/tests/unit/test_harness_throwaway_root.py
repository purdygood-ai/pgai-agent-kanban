"""
test_harness_throwaway_root.py
================================
Behavioral unit tests for team/scripts/harness/lib/throwaway-root.sh's
harness_init_scratch_repo() guard.

harness_init_scratch_repo() stands up a fresh scratch git repo and plants
one initial empty commit on it -- the same boilerplate
team/demos/*/README.md documents by hand. The guard stops the function
from being pointed at an existing checkout (a caller passing the wrong
path, or the doc's "mkdir+cd" step accidentally being skipped and the
boilerplate copy-pasted directly into a real repository): unguarded, the
function would silently plant a real, unaccounted-for empty commit into
whatever that checkout currently had checked out.

Acceptance criteria exercised:
  (1) A fresh, empty directory (the normal call shape, mirroring
      harness_new_throwaway_root's output) is accepted: the function
      succeeds, and the resulting repo carries exactly one commit, the
      empty "initial empty commit".
  (2) A directory that is already a git repository with existing commits
      (standing in for the caller's real checkout) is refused: the
      function returns non-zero, and no new commit is added -- HEAD
      before and after the call is byte-identical.
  (3) A directory NESTED inside an existing git repository (no .git of its
      own, but an ancestor directory has one) is refused the same way --
      `git -C <dir> rev-parse --git-dir` walks up the tree, so this shape
      must be caught too, not just the repo-root case.

All tests run against synthetic repos under tmp_path; the live kanban root
and the real project checkout are never touched.

Naming: function names describe behavior under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import pathlib
import textwrap

import pytest

from tests.fixtures.binary_guard import require_binary
from tests.unit.shell_harness import run_bash

_THROWAWAY_ROOT_SH = "scripts/harness/lib/throwaway-root.sh"


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Every test here stands up a real scratch git repository via
    harness_init_scratch_repo(); a module-wide autouse guard keeps that
    requirement in one place instead of repeating a per-test check at each
    git-dependent call site.
    """
    require_binary("git", label="harness-throwaway-root")


def _git_head(repo: pathlib.Path) -> str:
    result = run_bash(
        repo,
        f'git -C "{repo}" rev-parse --verify -q HEAD 2>/dev/null || echo NO_HEAD',
    )
    return result.stdout.strip()


def test_init_scratch_repo_succeeds_on_a_fresh_directory(tmp_path: pathlib.Path) -> None:
    """The normal call shape (a brand-new, never-touched directory) is
    accepted: the function succeeds and the repo carries exactly one
    commit, the empty 'initial empty commit'."""
    repo_path = tmp_path / "fresh-scratch-repo"

    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_THROWAWAY_ROOT_SH}
        harness_init_scratch_repo "{repo_path}" main
        echo "RC=$?"
        git -C "{repo_path}" log --oneline --format=%s
        git -C "{repo_path}" rev-list --count HEAD
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "RC=0" in result.stdout
    assert "initial empty commit" in result.stdout
    assert "1" in result.stdout.splitlines()[-1]


def test_init_scratch_repo_refuses_an_existing_checkout(tmp_path: pathlib.Path) -> None:
    """A directory that already has git history (standing in for the
    caller's real checkout) is refused: non-zero return, and the empty
    commit is never planted -- HEAD is unchanged."""
    repo_path = tmp_path / "real-checkout"
    repo_path.mkdir()
    setup = textwrap.dedent(f"""\
        set -euo pipefail
        git -C "{repo_path}" init -q
        git -C "{repo_path}" config --local user.email "test@example.com"
        git -C "{repo_path}" config --local user.name "Test"
        (cd "{repo_path}" && echo real work > file.txt)
        git -C "{repo_path}" add -A
        git -C "{repo_path}" -c commit.gpgsign=false commit -q -m "real work, not a scratch repo"
    """)
    setup_result = run_bash(tmp_path, setup)
    assert setup_result.returncode == 0, setup_result.stderr
    head_before = _git_head(repo_path)
    assert head_before != "NO_HEAD"

    script = textwrap.dedent(f"""\
        set +e
        source {_THROWAWAY_ROOT_SH}
        harness_init_scratch_repo "{repo_path}" main
        echo "RC=$?"
    """)
    result = run_bash(tmp_path, script)
    assert "RC=1" in result.stdout, (
        f"Expected refusal (rc=1) against an existing checkout.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "refusing" in result.stderr

    head_after = _git_head(repo_path)
    assert head_after == head_before, (
        "harness_init_scratch_repo must not add a commit to an existing checkout."
    )


def test_init_scratch_repo_refuses_a_directory_nested_inside_an_existing_repo(
    tmp_path: pathlib.Path,
) -> None:
    """A directory with no .git of its own, but nested inside an ancestor
    that IS a git repository, is refused the same way -- `git -C <dir>
    rev-parse --git-dir` walks up the tree to find it."""
    parent_repo = tmp_path / "real-checkout"
    parent_repo.mkdir()
    nested_dir = parent_repo / "subdir" / "scratch"
    setup = textwrap.dedent(f"""\
        set -euo pipefail
        git -C "{parent_repo}" init -q
        git -C "{parent_repo}" config --local user.email "test@example.com"
        git -C "{parent_repo}" config --local user.name "Test"
        (cd "{parent_repo}" && echo real work > file.txt)
        git -C "{parent_repo}" add -A
        git -C "{parent_repo}" -c commit.gpgsign=false commit -q -m "real work"
    """)
    setup_result = run_bash(tmp_path, setup)
    assert setup_result.returncode == 0, setup_result.stderr
    head_before = _git_head(parent_repo)

    script = textwrap.dedent(f"""\
        set +e
        source {_THROWAWAY_ROOT_SH}
        harness_init_scratch_repo "{nested_dir}" main
        echo "RC=$?"
    """)
    result = run_bash(tmp_path, script)
    assert "RC=1" in result.stdout, (
        f"Expected refusal (rc=1) against a nested directory inside an "
        f"existing checkout.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )

    head_after = _git_head(parent_repo)
    assert head_after == head_before, (
        "harness_init_scratch_repo must not add a commit to the enclosing checkout."
    )
