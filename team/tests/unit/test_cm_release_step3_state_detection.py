"""
test_cm_release_step3_state_detection.py
=========================================
Unit tests for the Step 3 state-detection logic in team/scripts/cm/release.sh.

Step 3 detects which intermediate release state the repository is in and
chooses the appropriate reconciliation path.  The three states when the
origin RC branch is absent are:

  Path A — tag already exists locally  →  Step 13 ran in a prior partial
            run; origin RC deletion already happened.  Skip re-push;
            continue downstream.

  Path B — tag absent AND local RC branch present  →  release was
            interrupted before Step 13; origin RC deletion occurred
            prematurely (or never completed).  Re-push the local RC branch
            to origin so subsequent steps can proceed without manual git push.

  Error   — tag absent AND local RC branch absent  →  the RC was never
            pushed; no known-good local ref to recover from.  Exit non-zero
            with an actionable error.

These tests exercise the detection/decision logic in isolation using a
minimal synthetic git repository so the tests can run without access to a
live remote.  The bash snippet under test mirrors the Step 3 logic; when
Step 3 in release.sh changes, this snippet must be kept in sync.

Test naming follows SOP.md Anti-pattern 6: names describe observed behavior,
not bug IDs, version numbers, or scaffolding labels.
"""

from __future__ import annotations

import pathlib
import subprocess
import textwrap

import pytest

from tests.unit.shell_harness import run_bash

# ---------------------------------------------------------------------------
# Bash snippet: Step 3 state-detection logic extracted for unit testing
# ---------------------------------------------------------------------------
# Mirrors the push_to_remote=true branch of Step 3 in release.sh.
#
# The snippet defines detect_step3_state(), which accepts three arguments:
#   $1  REPO_ROOT    — path to the git repo under test
#   $2  RC_BRANCH    — e.g. "rc/v0.99.0"
#   $3  RELEASE_TAG  — e.g. "v0.99.0" (the local tag to check)
#
# It prints one of four outcome tokens to stdout:
#   "origin-present"        — origin RC exists (happy path)
#   "tag-present-skip"      — origin RC absent, tag present (path A)
#   "repush-ok"             — origin RC absent, tag absent, local RC present;
#                             re-push succeeded (path B)
#   "repush-failed"         — origin RC absent, tag absent, local RC present;
#                             re-push failed
#   "error-no-rc-no-tag"    — origin RC absent, tag absent, local RC absent
#
# To simulate origin behaviour without a real network, the fixture creates a
# bare clone in a temp directory and points the repo's "origin" remote at it.
# Tests manipulate the bare clone (push/delete branches, create/delete tags)
# to exercise each state.

_STEP3_SNIPPET = textwrap.dedent("""\
    detect_step3_state() {
        local repo_root="$1"
        local rc_branch="$2"
        local release_tag="$3"

        # Check local RC branch exists
        if ! git -C "$repo_root" rev-parse --verify "refs/heads/${rc_branch}" >/dev/null 2>&1; then
            echo "error-no-rc-no-tag"
            return 1
        fi

        # Check if origin has the RC branch
        if git -C "$repo_root" ls-remote --exit-code --heads origin "${rc_branch}" >/dev/null 2>&1; then
            echo "origin-present"
            return 0
        fi

        # Origin RC is absent. Determine which path we are on.
        local _tag_sha
        _tag_sha="$(git -C "$repo_root" rev-parse "${release_tag}^{}" 2>/dev/null)" || _tag_sha=""

        if [[ -n "$_tag_sha" ]]; then
            # Path A: tag exists locally — Step 13 already ran in a prior partial run.
            echo "tag-present-skip"
            return 0
        fi

        # Path B: tag absent + local RC present — re-push to origin.
        local _push_out _push_rc
        _push_out=$(git -C "$repo_root" push origin "${rc_branch}" 2>&1); _push_rc=$?
        if [[ $_push_rc -ne 0 ]]; then
            echo "repush-failed"
            return 1
        fi
        echo "repush-ok"
        return 0
    }
""")


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _init_repo_pair(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """Create a (bare origin, local clone) git repo pair under tmp_path.

    Returns:
        (local_repo, bare_origin) — both as absolute Path objects.

    The local repo has 'origin' pointing at the bare repo.  An initial
    commit is made so both repos are non-empty and branch operations work.
    """
    origin = tmp_path / "origin.git"
    local = tmp_path / "local"

    # Bare origin
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)

    # Local clone
    subprocess.run(["git", "clone", str(origin), str(local)], check=True, capture_output=True)

    # Initial commit so the repo is non-empty (bare repos need at least one ref
    # before branch operations work reliably)
    (local / "README.md").write_text("# test repo\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(local), "config", "user.email", "test@example.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(local), "config", "user.name", "Test"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(local), "add", "README.md"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(local), "commit", "-m", "initial"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(local), "push", "origin", "HEAD"],
                   check=True, capture_output=True)

    return local, origin


def _create_rc_branch(local: pathlib.Path, rc_branch: str) -> None:
    """Create a local RC branch and push it to origin."""
    subprocess.run(["git", "-C", str(local), "checkout", "-b", rc_branch],
                   check=True, capture_output=True)
    (local / "rc-marker.txt").write_text(f"# {rc_branch}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(local), "add", "rc-marker.txt"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(local), "commit", "-m", f"open {rc_branch}"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(local), "push", "origin", rc_branch],
                   check=True, capture_output=True)


def _delete_origin_rc(local: pathlib.Path, rc_branch: str) -> None:
    """Delete the RC branch on origin (simulates Step 13 running or premature deletion)."""
    subprocess.run(["git", "-C", str(local), "push", "origin", "--delete", rc_branch],
                   check=True, capture_output=True)


def _create_local_tag(local: pathlib.Path, tag: str) -> None:
    """Create a local tag on the current HEAD (simulates Step 16 tag creation)."""
    subprocess.run(["git", "-C", str(local), "tag", tag],
                   check=True, capture_output=True)


def _run_detect(
    tmp_path: pathlib.Path,
    local: pathlib.Path,
    rc_branch: str,
    release_tag: str,
) -> str:
    """Run detect_step3_state() and return its stdout token."""
    script = _STEP3_SNIPPET + (
        f"detect_step3_state {local!s} {rc_branch!r} {release_tag!r}"
    )
    result = run_bash(tmp_path, script, timeout=15)
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_happy_path_origin_rc_present(tmp_path: pathlib.Path) -> None:
    """When origin has the RC branch, Step 3 reports origin-present and continues.

    This is the normal, uninterrupted release path.  No re-push should occur.
    """
    local, _origin = _init_repo_pair(tmp_path)
    _create_rc_branch(local, "rc/v0.99.0")

    outcome = _run_detect(tmp_path, local, "rc/v0.99.0", "v0.99.0")
    assert outcome == "origin-present", (
        f"Expected 'origin-present' when origin RC exists; got {outcome!r}"
    )


def test_path_a_tag_exists_skip_repush(tmp_path: pathlib.Path) -> None:
    """When origin RC is absent and the local tag exists, Step 3 skips re-push.

    This is the idempotency path A: Step 13 already deleted the origin RC and
    Step 16 already created the tag in a prior partial run.  Re-pushing would
    recreate a branch that should stay deleted.
    """
    local, _origin = _init_repo_pair(tmp_path)
    _create_rc_branch(local, "rc/v0.99.0")
    _delete_origin_rc(local, "rc/v0.99.0")
    _create_local_tag(local, "v0.99.0")

    outcome = _run_detect(tmp_path, local, "rc/v0.99.0", "v0.99.0")
    assert outcome == "tag-present-skip", (
        f"Expected 'tag-present-skip' when origin RC absent and tag present; got {outcome!r}"
    )


def test_path_b_repush_succeeds_when_origin_rc_absent_and_tag_absent(
    tmp_path: pathlib.Path,
) -> None:
    """When origin RC is absent and tag is absent, Step 3 re-pushes the local RC branch.

    This is idempotency path B: the release was interrupted after the origin RC
    was deleted but before the tag was created.  Without the re-push, subsequent
    steps that depend on origin RC being present would fail or require manual
    operator intervention.
    """
    local, _origin = _init_repo_pair(tmp_path)
    _create_rc_branch(local, "rc/v0.99.0")
    _delete_origin_rc(local, "rc/v0.99.0")
    # No local tag — simulates interruption before Step 16

    outcome = _run_detect(tmp_path, local, "rc/v0.99.0", "v0.99.0")
    assert outcome == "repush-ok", (
        f"Expected 'repush-ok' when origin RC absent, tag absent, local RC present;"
        f" got {outcome!r}"
    )


def test_path_b_origin_rc_restored_after_repush(tmp_path: pathlib.Path) -> None:
    """After path B re-push, origin has the RC branch again.

    Confirms the re-push is not just logged but actually lands on origin,
    so the next step that queries origin finds the branch present.
    """
    local, origin = _init_repo_pair(tmp_path)
    _create_rc_branch(local, "rc/v0.99.0")
    _delete_origin_rc(local, "rc/v0.99.0")

    _run_detect(tmp_path, local, "rc/v0.99.0", "v0.99.0")

    # Verify the branch is now present on origin
    result = subprocess.run(
        ["git", "-C", str(local), "ls-remote", "--heads", "origin", "rc/v0.99.0"],
        capture_output=True, text=True,
    )
    assert result.stdout.strip() != "", (
        "Expected rc/v0.99.0 to be restored on origin after path B re-push;"
        " but ls-remote returned empty output."
    )


def test_error_when_local_rc_branch_absent(tmp_path: pathlib.Path) -> None:
    """When the local RC branch is absent, Step 3 exits with an error token.

    This is the unrecoverable state: neither origin nor local has the RC branch,
    and there is no tag.  There is no known-good ref to recover from.
    """
    local, _origin = _init_repo_pair(tmp_path)
    # Do not create the RC branch at all

    outcome = _run_detect(tmp_path, local, "rc/v0.99.0", "v0.99.0")
    assert outcome == "error-no-rc-no-tag", (
        f"Expected 'error-no-rc-no-tag' when local RC branch is absent; got {outcome!r}"
    )
