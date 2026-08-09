"""
test_cm_release_worktree_removal_guard.py
==========================================
Regression coverage for the Step 10.5 leftover-worktree removal guard
(_cm_remove_stale_worktrees_for_branch) in team/scripts/cm/release.sh (BUG-0051).

WHY THIS EXISTS
----------------
The v2.3.7 fix for BUG-0049 added `_cm_remove_stale_worktrees_for_branch()` and
a "Step 10.5" call site that force-removes any linked git worktree still
checked out on the RC branch before `git branch -D` runs. Neither the helper
nor the call site carried an automated regression test — test_cm_release_
synthetic.py's fixtures create only the main worktree, so the guard's removal
path was never exercised by the suite. A future refactor could silently drop
or reorder the Step 10.5 call and every existing test would stay green while
re-opening BUG-0049.

WHY EXTRACTION, NOT REPLICATION
--------------------------------
`_cm_remove_stale_worktrees_for_branch` is a private function defined inline
in release.sh (not factored into team/scripts/lib/), so it cannot be
`source`d in isolation the way library functions can. Rather than hand-retype
its body into the test (risking silent drift from the real script), this file
extracts the function verbatim from the live release.sh text at test time and
sources the extracted text into a bash harness — the same technique used by
tests/unit/test_cm_release_hook_failure_remedy.py for `_run_release_hook`.
The test therefore exercises the production function body, not a copy of it.

This file lives under tests/integration/ (rather than tests/unit/, where the
sibling extraction test lives) so it is picked up by run-integration-tests.sh
per this ticket's acceptance criteria, even though the technique itself is a
focused function-level test rather than a full cm-open-rc.sh -> cm-release.sh
end-to-end run.

COVERAGE
--------
  - leftover-worktree-removed: a linked worktree pinned to the target branch
    is force-removed by the guard, unblocking a subsequent `git branch -D`
    that fails BEFORE the guard runs (documenting the BUG-0049 failure mode)
    and succeeds AFTER.
  - no-op: no worktree is pinned to the target branch -> the guard is a
    silent success (rc 0, no removal message), and `git branch -D` already
    works without the guard doing anything.
  - main-worktree-skip: the main worktree itself (repo_root) reports the
    target branch -> the guard logs a skip NOTE and does not attempt
    `git worktree remove` against the main worktree (which would fail),
    still returning rc 0 and leaving the main worktree untouched.

All git repos are created under pytest's tmp_path fixture (already redirected
under $PGAI_AGENT_KANBAN_TEMP_DIR by the parent conftest's autouse safety
net) — no bare /tmp paths and no writes to the live install.
"""

from __future__ import annotations

import pathlib
import subprocess

from tests.unit.shell_harness import run_bash, BashResult

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_RELEASE_SH = _TEAM_DIR / "scripts" / "cm" / "release.sh"

_FUNCTION_NAME = "_cm_remove_stale_worktrees_for_branch"
_TEST_BRANCH = "rc/v9.9.9"


def _extract_function(source_path: pathlib.Path, function_name: str) -> str:
    """Extract a top-level bash function definition verbatim.

    Scans for a line exactly matching "<function_name>() {" and returns
    every line up to and including the next line that is exactly "}"
    (the closing brace of a function defined at column 0 — the convention
    every function in release.sh follows). Raises AssertionError if the
    function or its closing brace cannot be found, so a rename or reformat
    upstream fails this test loudly rather than silently testing nothing.
    """
    text = source_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    header = f"{function_name}() {{"
    start = next((i for i, l in enumerate(lines) if l == header), None)
    assert start is not None, (
        f"Could not find '{header}' in {source_path}. "
        "The function may have been renamed or reformatted."
    )
    end = next((i for i in range(start + 1, len(lines)) if lines[i] == "}"), None)
    assert end is not None, (
        f"Could not find the closing brace for {function_name}() in {source_path}."
    )
    return "\n".join(lines[start : end + 1])


def _git(
    *args: str, cwd: pathlib.Path, check: bool = True
) -> subprocess.CompletedProcess:
    """Run a git command in *cwd* and return the CompletedProcess."""
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _init_repo_with_branch(repo_root: pathlib.Path, branch: str) -> None:
    """Initialise a repo at *repo_root* with an initial commit on main and
    *branch* branched off it (not checked out anywhere)."""
    repo_root.mkdir(parents=True, exist_ok=True)
    _git("init", "--initial-branch=main", cwd=repo_root)
    _git("config", "--local", "user.email", "test@pgai.local", cwd=repo_root)
    _git("config", "--local", "user.name", "PGAI Test", cwd=repo_root)
    (repo_root / "README.md").write_text(
        "# worktree removal guard test repo\n", encoding="utf-8"
    )
    _git("add", "README.md", cwd=repo_root)
    _git("commit", "-m", "Initial commit", cwd=repo_root)
    _git("branch", branch, cwd=repo_root)


def _run_guard(
    tmp_path: pathlib.Path, repo_root: pathlib.Path, branch: str
) -> BashResult:
    """Source the real _cm_remove_stale_worktrees_for_branch() and invoke it
    against *repo_root* and *branch*."""
    fn_body = _extract_function(_RELEASE_SH, _FUNCTION_NAME)
    script = f"""\
set -uo pipefail

{fn_body}

_cm_remove_stale_worktrees_for_branch {str(repo_root)!r} {branch!r}
"""
    return run_bash(tmp_path, script)


# ---------------------------------------------------------------------------
# Leftover-worktree-removed case
# ---------------------------------------------------------------------------


class TestLeftoverWorktreeRemoved:
    """A linked worktree pinned to the target branch is force-removed by the
    guard, unblocking the branch deletion that fails without it."""

    def test_leftover_worktree_is_removed_and_branch_delete_then_succeeds(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Seed a leftover linked worktree on the RC branch (simulating a CODER
        task worktree whose teardown did not run to completion), confirm a bare
        `git branch -D` fails first, run the guard, then confirm the worktree
        is gone and `git branch -D` now succeeds.
        """
        repo_root = tmp_path / "repo"
        _init_repo_with_branch(repo_root, _TEST_BRANCH)

        worktree_path = tmp_path / "leftover_worktree"
        _git("worktree", "add", str(worktree_path), _TEST_BRANCH, cwd=repo_root)

        # Document the BUG-0049 failure mode: a bare `git branch -D` fails
        # while a linked worktree still pins the branch.
        pre_guard_delete = _git(
            "branch", "-D", _TEST_BRANCH, cwd=repo_root, check=False
        )
        assert pre_guard_delete.returncode != 0, (
            "git branch -D must fail BEFORE the guard runs, while a linked "
            "worktree still pins the branch -- this is the failure mode "
            "BUG-0049 fixed and this test documents.\n"
            f"stdout: {pre_guard_delete.stdout}\nstderr: {pre_guard_delete.stderr}"
        )
        assert "worktree" in (
            pre_guard_delete.stdout + pre_guard_delete.stderr
        ).lower(), (
            "Expected the pre-guard branch -D failure to mention 'worktree'.\n"
            f"stdout: {pre_guard_delete.stdout}\nstderr: {pre_guard_delete.stderr}"
        )

        # Run the guard.
        result = _run_guard(tmp_path, repo_root, _TEST_BRANCH)
        assert result.returncode == 0, (
            f"Guard must return 0 after removing the leftover worktree.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert f"Removed stale worktree: {worktree_path}" in result.stdout, (
            f"Expected a 'Removed stale worktree' line naming the worktree path.\n"
            f"stdout: {result.stdout}"
        )
        assert "Removed 1 stale worktree(s)" in result.stdout, (
            f"Expected the removal-count summary line.\nstdout: {result.stdout}"
        )

        # The worktree directory and its registration are both gone.
        assert not worktree_path.exists(), (
            f"Worktree directory {worktree_path} should have been removed from disk."
        )
        wt_list_after = _git("worktree", "list", cwd=repo_root).stdout
        assert str(worktree_path) not in wt_list_after, (
            f"Worktree {worktree_path} should no longer be registered.\n"
            f"git worktree list output: {wt_list_after}"
        )

        # git branch -D now succeeds -- the guard unblocked deletion.
        post_guard_delete = _git(
            "branch", "-D", _TEST_BRANCH, cwd=repo_root, check=False
        )
        assert post_guard_delete.returncode == 0, (
            "git branch -D must succeed after the guard removes the pinning "
            f"worktree.\nstdout: {post_guard_delete.stdout}\n"
            f"stderr: {post_guard_delete.stderr}"
        )


# ---------------------------------------------------------------------------
# No-op case
# ---------------------------------------------------------------------------


class TestNoOpWhenNoWorktreePinned:
    """The common case: no worktree is pinned to the target branch, so the
    guard is a silent success and does not interfere with normal deletion."""

    def test_no_pinned_worktree_is_silent_success(
        self, tmp_path: pathlib.Path
    ) -> None:
        """No leftover worktree exists -> guard returns 0 with no removal
        output, and `git branch -D` succeeds on its own without the guard's
        help."""
        repo_root = tmp_path / "repo"
        _init_repo_with_branch(repo_root, _TEST_BRANCH)

        result = _run_guard(tmp_path, repo_root, _TEST_BRANCH)
        assert result.returncode == 0, (
            f"Guard must return 0 when no worktree is pinned to the branch.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert "Removed" not in combined, (
            f"No-op case must not emit any 'Removed' output.\noutput: {combined}"
        )
        assert "Stale worktree detected" not in combined, (
            f"No-op case must not report a stale worktree.\noutput: {combined}"
        )

        # git branch -D succeeds unaided -- confirms nothing was pinning it.
        delete_result = _git(
            "branch", "-D", _TEST_BRANCH, cwd=repo_root, check=False
        )
        assert delete_result.returncode == 0, (
            "git branch -D should succeed unaided when no worktree ever pinned "
            f"the branch.\nstdout: {delete_result.stdout}\n"
            f"stderr: {delete_result.stderr}"
        )


# ---------------------------------------------------------------------------
# Main-worktree-skip case
# ---------------------------------------------------------------------------


class TestMainWorktreeSkip:
    """When the main worktree itself (repo_root) reports the target branch,
    the guard skips it rather than attempting an unsupported removal."""

    def test_main_worktree_on_target_branch_is_skipped_not_removed(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Defensive-only scenario: by the time Step 10.5 normally runs, the
        main worktree should already be back on the prefixed-main branch
        (Step 5 checks it out), but the guard must not attempt `git worktree
        remove` against the main worktree if it is not -- that call is
        unsupported and would fail. Confirms the guard logs a skip NOTE,
        returns 0, and leaves the main worktree exactly as it was.
        """
        repo_root = tmp_path / "repo"
        _init_repo_with_branch(repo_root, _TEST_BRANCH)
        _git("checkout", _TEST_BRANCH, cwd=repo_root)

        result = _run_guard(tmp_path, repo_root, _TEST_BRANCH)
        assert result.returncode == 0, (
            "Guard must return 0 even when the main worktree reports the "
            f"target branch.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert "skipping" in combined.lower(), (
            f"Expected a skip NOTE for the main worktree.\noutput: {combined}"
        )
        assert "Removed" not in combined, (
            f"Main-worktree-skip case must not report any removal.\noutput: {combined}"
        )

        # The repo_root directory itself was never touched by `git worktree remove`.
        assert repo_root.exists()
        assert (repo_root / "README.md").exists()

        # The branch is still checked out -- nothing was force-removed.
        current_branch = _git(
            "rev-parse", "--abbrev-ref", "HEAD", cwd=repo_root
        ).stdout.strip()
        assert current_branch == _TEST_BRANCH, (
            f"Main worktree should still be checked out on {_TEST_BRANCH} "
            f"(unchanged by the skip path); got '{current_branch}'."
        )
