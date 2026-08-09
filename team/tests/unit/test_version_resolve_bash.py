"""
test_version_resolve_bash.py
=============================
Behavioral unit tests for team/scripts/lib/version_resolve.sh — the shared
version-tag resolution and clean-source laydown mechanism sourceable by both
install.sh and team/scripts/upgrade.sh (CODER-20260728-020).

WHAT IS TESTED
--------------
1. version_resolve_normalize   — strips a known branch_prefix (e.g. "ai_")
   when present; no-op otherwise.
2. version_resolve_tag_name    — re-applies branch_prefix to a normalized
   version to build the tag name expected in the repo.
3. version_resolve_resolve_tag — normalize + existence check against a real
   git fixture repo; fail-loud + nearest-tags listing on a miss.
4. version_resolve_materialize / version_resolve_prune — detached worktree
   lifecycle: materializes the resolved tag's tree into a location distinct
   from the repo's own working directory, and tears it down again without
   touching the repo's checked-out branch.
5. version_resolve_run_with_clean_source — the single (a)-(d) orchestration
   entry point: success path, forced-return-failure path, and hard-`exit`
   path all leave `git worktree list` and the checked-out branch unchanged.
6. version_resolve_forward_only_check — semver-aware ordering, including the
   v0.9.7 vs v0.17.1 cross-decade case from the acceptance criteria.

FIXTURE DESIGN
--------------
Tests build a throwaway git repo per test under tmp_path (respects
PGAI_AGENT_KANBAN_TEMP_DIR via the parent conftest's tmp_path redirection),
mirroring the pattern in test_version_stamp_split.py. Tags use the
"ai_v[0-9]*" shape (this project's own branch_prefix) so
git_describe_tag_pattern-filtered lookups exercise the prefixed path, plus a
couple of bare "v[0-9]*" cases for the no-prefix path.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from tests.unit.shell_harness import run_bash

_LIB = "scripts/lib/version_resolve.sh"


def _source(func_call: str) -> str:
    """Return a bash snippet that sources version_resolve.sh then calls func_call."""
    return f"source {_LIB} && {func_call}"


def _git(repo: pathlib.Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout.strip()


def _make_tagged_repo(tmp_path: pathlib.Path, subdir: str = "repo") -> pathlib.Path:
    """Build a git repo with three ai_-prefixed tags across three commits.

    Tags: ai_v1.0.0 (commit 1), ai_v1.1.0 (commit 2), ai_v2.0.0 (commit 3).
    An unrelated alias tag "latest" points at the tip so pattern-filtering
    (excluding non-semver alias tags) can be exercised.
    """
    repo = tmp_path / subdir
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test Fixture")

    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-m", "commit 1")
    _git(repo, "tag", "ai_v1.0.0")

    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-m", "commit 2")
    _git(repo, "tag", "ai_v1.1.0")

    (repo / "c.txt").write_text("c\n", encoding="utf-8")
    _git(repo, "add", "c.txt")
    _git(repo, "commit", "-m", "commit 3")
    _git(repo, "tag", "ai_v2.0.0")
    _git(repo, "tag", "latest")

    return repo


# ---------------------------------------------------------------------------
# version_resolve_normalize
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command, expected",
    [
        ("version_resolve_normalize 'ai_v2.1.0' 'ai_'", "v2.1.0"),  # prefix stripped (AC1)
        ("version_resolve_normalize 'v2.1.0' 'ai_'", "v2.1.0"),  # prefix absent from input, no-op
        ("version_resolve_normalize 'ai_v2.1.0'", "ai_v2.1.0"),  # no branch_prefix given, no-op
    ],
)
def test_version_resolve_normalize(tmp_path: pathlib.Path, command: str, expected: str) -> None:
    """version_resolve_normalize strips the branch_prefix only when both prefix and input agree."""
    result = run_bash(tmp_path, _source(command))
    assert result.returncode == 0
    assert result.stdout.strip() == expected


# ---------------------------------------------------------------------------
# version_resolve_tag_name
# ---------------------------------------------------------------------------


def test_tag_name_reapplies_branch_prefix(tmp_path: pathlib.Path) -> None:
    result = run_bash(tmp_path, _source("version_resolve_tag_name 'v1.1.0' 'ai_'"))
    assert result.returncode == 0
    assert result.stdout.strip() == "ai_v1.1.0"


def test_tag_name_bare_when_no_prefix(tmp_path: pathlib.Path) -> None:
    result = run_bash(tmp_path, _source("version_resolve_tag_name 'v1.1.0'"))
    assert result.returncode == 0
    assert result.stdout.strip() == "v1.1.0"


# ---------------------------------------------------------------------------
# version_resolve_resolve_tag
# ---------------------------------------------------------------------------


def test_resolve_tag_succeeds_for_existing_tag_via_bare_input(
    tmp_path: pathlib.Path,
) -> None:
    """A request for an existing tag resolves, tolerating the bare (no ai_) form."""
    repo = _make_tagged_repo(tmp_path)
    result = run_bash(
        tmp_path,
        _source(f"version_resolve_resolve_tag 'v1.1.0' '{repo!s}' 'ai_'"),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "ai_v1.1.0"


def test_resolve_tag_succeeds_for_existing_tag_via_prefixed_input(
    tmp_path: pathlib.Path,
) -> None:
    """A request that already includes the internal prefix also resolves (AC1)."""
    repo = _make_tagged_repo(tmp_path)
    result = run_bash(
        tmp_path,
        _source(f"version_resolve_resolve_tag 'ai_v1.1.0' '{repo!s}' 'ai_'"),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "ai_v1.1.0"


def test_resolve_tag_fails_loud_and_lists_nearest_tags_for_missing_version(
    tmp_path: pathlib.Path,
) -> None:
    """A nonexistent tag returns non-zero, names the request, and lists nearest tags (AC3)."""
    repo = _make_tagged_repo(tmp_path)
    result = run_bash(
        tmp_path,
        _source(f"version_resolve_resolve_tag 'v9.9.9' '{repo!s}' 'ai_'"),
    )
    assert result.returncode != 0
    assert result.stdout.strip() == "", "no tag should be echoed to stdout on failure"
    assert "v9.9.9" in result.stderr, "failure message must name the requested version"
    assert "ai_v9.9.9" in result.stderr, "failure message must name the resolved tag attempted"
    assert "ai_v1.0.0" in result.stderr, "nearest tags must be listed"
    assert "ai_v2.0.0" in result.stderr, "nearest tags must be listed"
    # The alias tag must never appear as a "nearest available" candidate.
    assert "latest" not in result.stderr.replace("released", "")


def test_resolve_tag_does_not_touch_repo_branch_on_failure(
    tmp_path: pathlib.Path,
) -> None:
    """A failed resolution never touches the repo's checked-out branch/HEAD."""
    repo = _make_tagged_repo(tmp_path)
    before_branch = _git(repo, "symbolic-ref", "--short", "HEAD")
    before_worktrees = _git(repo, "worktree", "list")

    run_bash(
        tmp_path,
        _source(f"version_resolve_resolve_tag 'v9.9.9' '{repo!s}' 'ai_'"),
    )

    after_branch = _git(repo, "symbolic-ref", "--short", "HEAD")
    after_worktrees = _git(repo, "worktree", "list")
    assert before_branch == after_branch
    assert before_worktrees == after_worktrees


# ---------------------------------------------------------------------------
# version_resolve_materialize / version_resolve_prune
# ---------------------------------------------------------------------------


def test_materialize_writes_resolved_tags_tree_to_a_distinct_location(
    tmp_path: pathlib.Path,
) -> None:
    """Materializing an existing tag lays its tree down outside the repo's own dir (AC2)."""
    repo = _make_tagged_repo(tmp_path)
    script = f"""
        source {_LIB}
        clean=$(version_resolve_materialize '{repo!s}' 'ai_v1.1.0') || exit 1
        echo "CLEAN_PATH=$clean"
        cat "$clean/b.txt"
        [[ ! -f "$clean/c.txt" ]] && echo "C_ABSENT_OK"
        version_resolve_prune '{repo!s}' "$clean"
    """
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, result.stderr
    assert "CLEAN_PATH=" in result.stdout
    clean_path_line = [
        line for line in result.stdout.splitlines() if line.startswith("CLEAN_PATH=")
    ][0]
    clean_path = clean_path_line.split("=", 1)[1]
    assert clean_path != str(repo), "materialized tree must live outside the repo's own directory"
    assert "b" in result.stdout
    assert "C_ABSENT_OK" in result.stdout, (
        "ai_v1.1.0's tree must not contain c.txt (added only in the tag-2.0.0 commit)"
    )


def test_materialize_and_prune_leave_repo_branch_and_worktree_list_unchanged(
    tmp_path: pathlib.Path,
) -> None:
    """A full materialize+prune cycle is byte-identical before/after (AC4, success run)."""
    repo = _make_tagged_repo(tmp_path)
    before_branch = _git(repo, "symbolic-ref", "--short", "HEAD")
    before_worktrees = _git(repo, "worktree", "list")

    script = f"""
        source {_LIB}
        clean=$(version_resolve_materialize '{repo!s}' 'ai_v1.1.0') || exit 1
        version_resolve_prune '{repo!s}' "$clean"
    """
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, result.stderr

    after_branch = _git(repo, "symbolic-ref", "--short", "HEAD")
    after_worktrees = _git(repo, "worktree", "list")
    assert before_branch == after_branch
    assert before_worktrees == after_worktrees


def test_prune_is_idempotent_on_already_removed_worktree(tmp_path: pathlib.Path) -> None:
    """Calling prune twice (or on a path that never existed) is a harmless no-op."""
    repo = _make_tagged_repo(tmp_path)
    script = f"""
        source {_LIB}
        clean=$(version_resolve_materialize '{repo!s}' 'ai_v1.1.0') || exit 1
        version_resolve_prune '{repo!s}' "$clean" || exit 1
        version_resolve_prune '{repo!s}' "$clean"
        echo "SECOND_PRUNE_EXIT=$?"
    """
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, result.stderr
    assert "SECOND_PRUNE_EXIT=0" in result.stdout


# ---------------------------------------------------------------------------
# version_resolve_run_with_clean_source
# ---------------------------------------------------------------------------


def test_run_with_clean_source_success_path_invokes_callback_and_cleans_up(
    tmp_path: pathlib.Path,
) -> None:
    repo = _make_tagged_repo(tmp_path)
    before_worktrees = _git(repo, "worktree", "list")

    script = f"""
        source {_LIB}
        my_cb() {{
            local clean="$1"
            cat "$clean/b.txt"
        }}
        version_resolve_run_with_clean_source '{repo!s}' 'v1.1.0' 'ai_' my_cb
        echo "ORCH_EXIT=$?"
    """
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, result.stderr
    assert "b" in result.stdout
    assert "ORCH_EXIT=0" in result.stdout

    after_worktrees = _git(repo, "worktree", "list")
    assert before_worktrees == after_worktrees


def test_run_with_clean_source_forced_failure_return_still_cleans_up(
    tmp_path: pathlib.Path,
) -> None:
    """A callback that returns non-zero still leaves the repo untouched (AC4, forced-failure run)."""
    repo = _make_tagged_repo(tmp_path)
    before_branch = _git(repo, "symbolic-ref", "--short", "HEAD")
    before_worktrees = _git(repo, "worktree", "list")

    script = f"""
        source {_LIB}
        my_cb_fail() {{
            return 1
        }}
        version_resolve_run_with_clean_source '{repo!s}' 'v1.1.0' 'ai_' my_cb_fail
        echo "ORCH_EXIT=$?"
    """
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, result.stderr
    assert "ORCH_EXIT=1" in result.stdout

    after_branch = _git(repo, "symbolic-ref", "--short", "HEAD")
    after_worktrees = _git(repo, "worktree", "list")
    assert before_branch == after_branch
    assert before_worktrees == after_worktrees


def test_run_with_clean_source_hard_exit_in_callback_still_cleans_up(
    tmp_path: pathlib.Path,
) -> None:
    """A callback that calls `exit` directly is still caught by the chained EXIT trap."""
    repo = _make_tagged_repo(tmp_path)
    before_worktrees = _git(repo, "worktree", "list")

    script = f"""
        source {_LIB}
        my_cb_exit() {{
            exit 7
        }}
        version_resolve_run_with_clean_source '{repo!s}' 'v1.1.0' 'ai_' my_cb_exit
        echo "UNREACHABLE"
    """
    result = run_bash(tmp_path, script)
    assert result.returncode == 7
    assert "UNREACHABLE" not in result.stdout

    after_worktrees = _git(repo, "worktree", "list")
    assert before_worktrees == after_worktrees


def test_run_with_clean_source_resolution_failure_never_invokes_callback(
    tmp_path: pathlib.Path,
) -> None:
    """When resolution fails, the callback must never run and nothing is left to prune."""
    repo = _make_tagged_repo(tmp_path)
    before_worktrees = _git(repo, "worktree", "list")

    script = f"""
        source {_LIB}
        my_cb() {{ echo "SHOULD_NOT_RUN"; }}
        version_resolve_run_with_clean_source '{repo!s}' 'v9.9.9' 'ai_' my_cb
        echo "ORCH_EXIT=$?"
    """
    result = run_bash(tmp_path, script)
    assert "SHOULD_NOT_RUN" not in result.stdout
    assert "ORCH_EXIT=1" in result.stdout

    after_worktrees = _git(repo, "worktree", "list")
    assert before_worktrees == after_worktrees


def test_run_with_clean_source_chains_a_preexisting_exit_trap(
    tmp_path: pathlib.Path,
) -> None:
    """A caller's own EXIT trap (e.g. upgrade.sh's _cleanup) survives, not clobbered."""
    repo = _make_tagged_repo(tmp_path)

    script = f"""
        source {_LIB}
        pre_existing_cleanup() {{ echo "PRE_EXISTING_CLEANUP_RAN"; }}
        trap pre_existing_cleanup EXIT

        my_cb() {{ echo "callback ran"; return 0; }}
        version_resolve_run_with_clean_source '{repo!s}' 'v1.1.0' 'ai_' my_cb
        echo "main script continuing"
    """
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, result.stderr
    assert "callback ran" in result.stdout
    assert "main script continuing" in result.stdout
    assert "PRE_EXISTING_CLEANUP_RAN" in result.stdout
    # The pre-existing trap must fire at real script exit, i.e. after the
    # "main script continuing" line — not be triggered/consumed mid-run.
    assert result.stdout.index("main script continuing") < result.stdout.index(
        "PRE_EXISTING_CLEANUP_RAN"
    )


# ---------------------------------------------------------------------------
# version_resolve_forward_only_check (semver-aware comparison entry point)
# ---------------------------------------------------------------------------


def test_forward_only_check_allows_strictly_newer_target_cross_decade(
    tmp_path: pathlib.Path,
) -> None:
    """v0.17.1 is strictly newer than v0.9.7 (cross-decade ordering, AC5)."""
    result = run_bash(
        tmp_path, _source("version_resolve_forward_only_check v0.9.7 v0.17.1")
    )
    assert result.returncode == 0


def test_forward_only_check_refuses_older_target(tmp_path: pathlib.Path) -> None:
    result = run_bash(
        tmp_path, _source("version_resolve_forward_only_check v0.17.1 v0.9.7")
    )
    assert result.returncode != 0


def test_forward_only_check_refuses_equal_versions(tmp_path: pathlib.Path) -> None:
    result = run_bash(
        tmp_path, _source("version_resolve_forward_only_check v0.17.1 v0.17.1")
    )
    assert result.returncode != 0


# ---------------------------------------------------------------------------
# Sourcing version_resolve.sh also exposes semver.sh's functions
# ---------------------------------------------------------------------------


def test_sourcing_version_resolve_also_exposes_semver_compare(
    tmp_path: pathlib.Path,
) -> None:
    """version_resolve.sh re-exports semver.sh so callers get both from one source line."""
    result = run_bash(tmp_path, _source("semver_compare v0.9.7 v0.17.1"))
    assert result.returncode == 0
    assert result.stdout.strip() == "-1"
