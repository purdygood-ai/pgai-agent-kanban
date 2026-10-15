"""
test_worktree_copy_git_isolation.py
====================================
Regression coverage for the linked-worktree copy hazard the live-install-
safety canon in docs/test-authoring-guidelines.md names ("A copied worktree
is not an isolated tree"): a linked worktree's `.git` is a one-line pointer
back to the shared repository's metadata, so a recursive copy of the working
directory does not isolate git state -- a git command run inside the copy
still mutates the source repository's branches, HEAD, and index. The
documentation prescribes a safe alternative (cloning the worktree) that does
isolate.

Run once by hand, that claim is an anecdote. This module makes it a standing
test: it builds a scratch repository and a linked worktree entirely under
the framework temp root and demonstrates both halves --

  - the hazard: a commit made inside a recursive (`cp -r`-shaped) copy of the
    worktree moves the branch in the source repository.
  - the alternative: the same commit, made inside a `git clone` of the
    worktree (docs/test-authoring-guidelines.md, "Three safe ways to get a
    tree you can experiment in", option 1 -- "the default answer"), leaves
    the source repository's branch at the commit it started on.

The negative case is what makes the positive case mean something: if a
future editor rewrites the documented alternative into something that does
not actually isolate git state, this module's second case is what fails.

Isolation: every repository, worktree, and clone this module builds lives
under pytest's `tmp_path` (redirected under $PGAI_AGENT_KANBAN_TEMP_DIR by
the parent conftest's autouse temp-root fixture), never under the dev tree
or the live install this suite is itself running from. Every `git` call
runs with an explicit environment that clears the GIT_DIR/GIT_WORK_TREE
family of variables so no ambient value inherited from the invoking agent's
shell can redirect a command away from the throwaway repository it was
built to target.

Naming: test names describe the behavior under test, never a bug ID or task
ID (roles/TESTER.md "Test Authoring Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess

import pytest

from tests.fixtures.binary_guard import require_binary

# Environment variables that redirect git's repository/worktree discovery
# away from the `-C <dir>` target. Cleared explicitly (never inherited) so
# the fixture's isolation does not silently depend on the invoking agent's
# shell happening to leave them unset.
_GIT_REDIRECT_ENV_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_CEILING_DIRECTORIES",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH -- both cases
    build and drive real git repositories, and there is no non-git way to
    exercise either half of the claim."""
    require_binary("git", label="worktree-copy-git-isolation")


def _git_clean_env() -> dict[str, str]:
    """Return a copy of the process environment with every git repository-
    redirect variable removed, so each `_git()` call below resolves purely
    from the `-C <dir>` argument it was given, never from an ambient value
    the invoking agent's environment happens to export."""
    env = dict(os.environ)
    for var in _GIT_REDIRECT_ENV_VARS:
        env.pop(var, None)
    return env


def _git(repo: pathlib.Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        env=_git_clean_env(),
    )
    return result.stdout.strip()


def _build_scratch_repo_with_linked_worktree(
    tmp_path: pathlib.Path,
) -> tuple[pathlib.Path, pathlib.Path, str]:
    """Build a throwaway repository with one commit, a branch, and a linked
    worktree checked out on that branch -- the exact shape
    docs/test-authoring-guidelines.md describes, entirely under *tmp_path*.

    Returns (repo, worktree, branch).
    """
    branch = "feature"
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "one")
    _git(repo, "branch", branch)

    worktree = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", str(worktree), branch)

    return repo, worktree, branch


def test_recursive_copy_of_linked_worktree_moves_source_branch(
    tmp_path: pathlib.Path,
) -> None:
    """A git command run inside a recursive copy of a linked worktree
    resolves through the copied `.git` pointer file and mutates the SOURCE
    repository -- the hazard docs/test-authoring-guidelines.md names. This
    is the negative case: if the hazard ever stops reproducing (a future git
    version, a changed fixture), the assertion below fails loud instead of
    the suite silently confirming nothing."""
    repo, worktree, branch = _build_scratch_repo_with_linked_worktree(tmp_path)

    assert not (worktree / ".git").is_dir(), (
        "a linked worktree's .git must be a pointer FILE, not a directory -- "
        "if this ever stops being true the fixture itself no longer builds "
        "the shape the hazard depends on"
    )

    before = _git(repo, "rev-parse", branch)

    # Copy the worktree the way an agent reaching for a scratch tree would --
    # including the .git pointer file, which is the whole hazard.
    copy = tmp_path / "copy"
    shutil.copytree(worktree, copy)
    assert (copy / ".git").is_file(), (
        "the copy's .git must still be the pointer file (not a directory) "
        "for this case to exercise the documented hazard rather than an "
        "accidental clone"
    )

    # A history-rewriting verb -- docs/test-authoring-guidelines.md names
    # revert/reset/checkout -B/rebase; a commit is the same shape (it moves
    # the branch the copy's pointer resolves to) and needs no upstream ref.
    _git(copy, "commit", "-q", "--allow-empty", "-m", "made inside the copy")

    after = _git(repo, "rev-parse", branch)

    assert after != before, (
        f"a commit made inside a recursively copied worktree was expected to "
        f"move '{branch}' in the source repository at {repo} (the hazard "
        f"docs/test-authoring-guidelines.md names), but the branch stayed at "
        f"{before} -- either the hazard no longer reproduces on this git "
        f"version or this fixture stopped exercising it; either way this "
        f"case failing here is the intended signal, not a bug in the test"
    )


def test_documented_clone_alternative_leaves_source_branch_unmoved(
    tmp_path: pathlib.Path,
) -> None:
    """The alternative docs/test-authoring-guidelines.md lists first ("Three
    safe ways to get a tree you can experiment in", option 1 -- "the default
    answer: it is the only one of the three where a mistake cannot reach the
    source repository at all") is `git clone`. A commit made inside a clone
    of the worktree does not touch the source repository's branch, because
    the clone owns its own `.git` directory and its own refs."""
    repo, worktree, branch = _build_scratch_repo_with_linked_worktree(tmp_path)

    before = _git(repo, "rev-parse", branch)

    # The documented alternative, run exactly as
    # docs/test-authoring-guidelines.md states it:
    #   git clone "$(git rev-parse --show-toplevel)" "$SCRATCH/clone"
    # -- from inside the worktree, so --show-toplevel resolves to the
    # worktree's own working directory rather than the shared repo's.
    toplevel = _git(worktree, "rev-parse", "--show-toplevel")
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", toplevel, str(clone)],
        cwd=str(worktree),
        capture_output=True,
        text=True,
        check=True,
        env=_git_clean_env(),
    )
    assert (clone / ".git").is_dir(), (
        "a clone must own its own .git directory, not a pointer file, for "
        "this case to exercise the documented alternative rather than "
        "reproducing the hazard by accident"
    )

    _git(clone, "commit", "-q", "--allow-empty", "-m", "made inside the clone")

    after = _git(repo, "rev-parse", branch)

    assert after == before, (
        f"the documented safe alternative (git clone) was expected to leave "
        f"'{branch}' in the source repository at {repo} unmoved at {before}, "
        f"but it changed to {after}. If docs/test-authoring-guidelines.md "
        f"was edited to a different alternative that does not actually "
        f"isolate git state, that is a finding about the documentation, not "
        f"this test -- record the observed hashes in the task Summary "
        f"rather than loosening this assertion"
    )
