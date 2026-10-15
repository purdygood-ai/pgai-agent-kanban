"""
test_lint_rc_empty_commits.py — Unit tests for
team/scripts/lint_rc_empty_commits.py.

Every test builds its own throwaway git repository under pytest's
``tmp_path`` (redirected under ``PGAI_AGENT_KANBAN_TEMP_DIR`` by
``team/tests/conftest.py`` — see that file's "Temp-dir redirection for
tmp_path" section). No test ever touches this dev tree's own git state:
each repository is initialised from scratch, has its own branches, and is
discarded with its ``tmp_path`` directory.

Coverage:

  1. **Flagged case**: a synthetic ``main..rc/v1.0.0`` range containing one
     empty non-merge commit (an identical tree to its parent) is caught,
     the offending commit is named in the output, and the lint exits 1.

  2. **Clean case, including a merge**: a range whose every commit either
     changes a file or is a merge (built by branching, committing on the
     branch, and merging it back with ``--no-ff``) reports nothing and
     exits 0.

  3. **Content-identical rewrite is also flagged**: a commit that rewrites
     a tracked file back to byte-identical content produces an empty tree
     diff against its parent and is caught by the same mechanism as case 1
     — this is the deliberate "what counts as empty" definition the task
     asked to be handled on purpose (see the lint's module docstring).

  4. **No RC branch present**: a repo with no ``<prefix>rc/vX.Y.Z`` branch
     is a clean no-op — exit 0, no ranges checked.

  5. **Prefixed branch names** (``ai_main`` / ``ai_rc/vX.Y.Z``, matching
     this project's own naming) are discovered automatically.

A check that always reports nothing fails this suite by construction, not
by a dedicated test: cases 1 and 3 each assert the exact non-empty hit
list a real empty commit produces (``assert [h.sha for h in hits] ==
[expected_sha]``), so swapping ``find_empty_commits`` for an
always-returns-``[]`` stand-in fails those assertions immediately — there
is no separate "does the broken check fail" test to keep in sync with the
real one.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
import pytest
from tests.fixtures.binary_guard import require_binary


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Every test here builds, inspects, or drives a real git repository; a
    module-wide autouse guard keeps that requirement in one place instead
    of repeating a per-test check at each git-dependent call site.
    """
    require_binary("git", label="lint-rc-empty-commits")

# ---------------------------------------------------------------------------
# Locate lint_rc_empty_commits.py and import it as a module (mirrors
# test_lint_hygiene.py's approach so the test exercises the real
# module functions directly, not just the CLI subprocess surface).
#
# This file lives at team/tests/unit/test_lint_rc_empty_commits.py.
# Three parent levels up: unit/ -> tests/ -> team/ -> project_root/
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent
_LINT_SCRIPT = _TEAM_DIR / "scripts" / "lint_rc_empty_commits.py"

_spec = importlib.util.spec_from_file_location("lint_rc_empty_commits", _LINT_SCRIPT)
assert _spec is not None and _spec.loader is not None
lint_rc_empty_commits = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint_rc_empty_commits)


# ---------------------------------------------------------------------------
# Repo-building helpers
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"git {' '.join(args)} failed in {repo}:\n{result.stdout}\n{result.stderr}"
    )
    return result


def _init_repo(repo: Path, *, main_branch: str = "main") -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", f"--initial-branch={main_branch}")
    _git(repo, "config", "--local", "user.email", "test@pgai.local")
    _git(repo, "config", "--local", "user.name", "PGAI Test")


def _write_commit(repo: Path, filename: str, content: str, message: str) -> str:
    (repo / filename).write_text(content, encoding="utf-8")
    _git(repo, "add", filename)
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _empty_commit(repo: Path, message: str) -> str:
    _git(repo, "commit", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


# ---------------------------------------------------------------------------
# Case 1: flagged
# ---------------------------------------------------------------------------


def test_flags_empty_non_merge_commit_in_rc_range(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, main_branch="main")
    _write_commit(repo, "README.md", "hello\n", "initial commit")

    _git(repo, "branch", "rc/v1.0.0")
    _git(repo, "checkout", "rc/v1.0.0")
    real_sha = _write_commit(repo, "feature.txt", "real work\n", "add feature")
    empty_sha = _empty_commit(repo, "initial empty commit")

    hits = lint_rc_empty_commits.find_empty_commits(repo, "main..rc/v1.0.0")

    assert [h.sha for h in hits] == [empty_sha]
    assert real_sha not in [h.sha for h in hits]

    exit_code = lint_rc_empty_commits.main(
        ["--repo", str(repo), "--range", "main..rc/v1.0.0"]
    )
    assert exit_code == 1


# ---------------------------------------------------------------------------
# Case 2: clean range including a merge commit
# ---------------------------------------------------------------------------


def test_reports_nothing_for_a_clean_range_with_a_merge(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, main_branch="main")
    _write_commit(repo, "README.md", "hello\n", "initial commit")

    _git(repo, "branch", "rc/v1.0.0")
    _git(repo, "checkout", "rc/v1.0.0")
    _git(repo, "checkout", "-b", "feature/one")
    _write_commit(repo, "feature.txt", "real work\n", "add feature")
    _git(repo, "checkout", "rc/v1.0.0")
    _git(repo, "merge", "--no-ff", "feature/one", "-m", "Merge feature/one into rc/v1.0.0")

    hits = lint_rc_empty_commits.find_empty_commits(repo, "main..rc/v1.0.0")
    assert hits == []

    exit_code = lint_rc_empty_commits.main(
        ["--repo", str(repo), "--range", "main..rc/v1.0.0"]
    )
    assert exit_code == 0


# ---------------------------------------------------------------------------
# Case 3: content-identical rewrite is also flagged (deliberate "what counts
# as empty" definition -- see the lint's module docstring)
# ---------------------------------------------------------------------------


def test_flags_a_commit_that_rewrites_identical_content(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, main_branch="main")
    _write_commit(repo, "config.txt", "value=1\n", "initial commit")

    _git(repo, "branch", "rc/v1.0.0")
    _git(repo, "checkout", "rc/v1.0.0")
    # Rewrite the file to the exact same bytes it already holds, then force
    # a commit even though the index has nothing staged -- this is the
    # "reverted to identical bytes" shape the task asked to be handled
    # deliberately, and it produces the same empty tree-diff as a bare
    # --allow-empty commit.
    (repo / "config.txt").write_text("value=1\n", encoding="utf-8")
    rewrite_sha = _empty_commit(repo, "rewrite config.txt to identical content")

    hits = lint_rc_empty_commits.find_empty_commits(repo, "main..rc/v1.0.0")
    assert [h.sha for h in hits] == [rewrite_sha]


# ---------------------------------------------------------------------------
# Case 4: no RC branch present -- clean no-op
# ---------------------------------------------------------------------------


def test_no_rc_branch_is_a_clean_noop(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, main_branch="main")
    _write_commit(repo, "README.md", "hello\n", "initial commit")

    ranges = lint_rc_empty_commits.discover_rc_ranges(repo)
    assert ranges == []

    exit_code = lint_rc_empty_commits.main(["--repo", str(repo)])
    assert exit_code == 0


# ---------------------------------------------------------------------------
# Case 5: prefixed branch names (e.g. "ai_main" / "ai_rc/vX.Y.Z") are
# discovered automatically, matching this project's own branch naming.
# ---------------------------------------------------------------------------


def test_discovers_prefixed_main_and_rc_branches(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, main_branch="ai_main")
    _write_commit(repo, "README.md", "hello\n", "initial commit")

    _git(repo, "branch", "ai_rc/v9.9.9")
    _git(repo, "checkout", "ai_rc/v9.9.9")
    empty_sha = _empty_commit(repo, "initial empty commit")

    ranges = lint_rc_empty_commits.discover_rc_ranges(repo)
    assert ranges == ["ai_main..ai_rc/v9.9.9"]

    hits = lint_rc_empty_commits.find_empty_commits(repo, "ai_main..ai_rc/v9.9.9")
    assert [h.sha for h in hits] == [empty_sha]

    exit_code = lint_rc_empty_commits.main(["--repo", str(repo)])
    assert exit_code == 1
