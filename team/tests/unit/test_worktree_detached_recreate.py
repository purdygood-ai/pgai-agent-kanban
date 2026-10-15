"""
test_worktree_detached_recreate.py
==================================
create_detached_worktree (team/scripts/lib/worktree.sh) clears whatever a
previous dispatch left at the task's worktree path before adding the new
detached worktree, and never removes anything outside the worktree base.

A TESTER worktree is kept when the task ends BLOCKED; when the block is
retried and the task dispatched again, the create runs at that same path.
Without a pre-clean git refuses with "already exists" and the wake records a
human-gated worktree-create block instead of re-running the verification.

Fixtures (a real git repository, PGAI_WORKTREE_BASE pinned under tmp_path):
  (A) Create a detached worktree for a task id at one commit, leave an
      untracked file in it, advance the branch, create again for the same
      id: exit 0, the same path printed, HEAD at the new commit.
  (B) A registration whose directory was deleted by hand is pruned and the
      create succeeds at that path.
  (B2) Another task's stale registration -- a feature branch whose worktree
      directory is gone -- is left registered: only the task's own path is
      cleared, so OVERWATCH's residue triage still sees the other one.
  (C) A task path that resolves outside the base -- a symlink whose target
      is another registered worktree, and the base's parent ('..') -- is
      refused dual-channel; the target worktree, its files and the parent's
      sentinel all survive.

All fixtures live under tmp_path; nothing touches a kanban root.
"""

from __future__ import annotations

import pathlib
import subprocess
import textwrap

from tests.fixtures.binary_guard import require_binary

_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "scripts"
_WORKTREE_LIB = _SCRIPTS_DIR / "lib" / "worktree.sh"
_TEMP_LIB = _SCRIPTS_DIR / "lib" / "temp.sh"
_REFUSAL_KEY = "error:   worktree.residue.outside-base"
_TASK = "TESTER-20260101-001-verify-demo"


def _git(dev_tree: pathlib.Path, *args: str) -> str:
    git = require_binary("git", label="worktree-detached-recreate").path
    result = subprocess.run([git, "-C", str(dev_tree), *args], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout.strip()


def _repo(tmp_path: pathlib.Path) -> pathlib.Path:
    dev_tree = tmp_path / "dev_tree"
    dev_tree.mkdir()
    _git(dev_tree, "init", "-q", "-b", "ai_rc/v1.0.0")
    _git(dev_tree, "config", "user.email", "test@example.invalid")
    _git(dev_tree, "config", "user.name", "test")
    (dev_tree / "README.md").write_text("one\n", encoding="utf-8")
    _git(dev_tree, "add", "README.md")
    _git(dev_tree, "commit", "-q", "-m", "one")
    return dev_tree


def _commit(dev_tree: pathlib.Path, text: str) -> str:
    (dev_tree / "README.md").write_text(text, encoding="utf-8")
    _git(dev_tree, "commit", "-q", "-am", text.strip())
    return _git(dev_tree, "rev-parse", "HEAD")


def _create(tmp_path: pathlib.Path, base: pathlib.Path, dev_tree: pathlib.Path, task_id: str, ref: str):
    script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -uo pipefail
        export PGAI_AGENT_KANBAN_TEMP_DIR="{tmp_path / 'kanban_temp'}"
        export PGAI_WORKTREE_BASE="{base}"
        unset PP_project_name PGAI_AGENT_KANBAN_ROOT_PATH PGAI_PROJECT_NAME
        source "{_TEMP_LIB}"
        source "{_WORKTREE_LIB}"
        out="$(create_detached_worktree "{task_id}" "{ref}" "{dev_tree}")"
        rc=$?
        printf 'RC=[%s]\\nOUT=[%s]\\n' "$rc" "$out"
        """)
    path = tmp_path / "run.sh"
    path.write_text(script, encoding="utf-8")
    return subprocess.run(["bash", str(path)], capture_output=True, text=True, timeout=60, cwd=str(tmp_path))


def _base(tmp_path: pathlib.Path) -> pathlib.Path:
    base = tmp_path / "scratch" / "worktrees"
    base.mkdir(parents=True)
    return base


def test_second_create_for_the_same_task_succeeds_at_the_new_ref(tmp_path: pathlib.Path) -> None:
    dev_tree, base = _repo(tmp_path), _base(tmp_path)
    expected = base / _TASK

    first = _create(tmp_path, base, dev_tree, _TASK, "ai_rc/v1.0.0")
    assert "RC=[0]" in first.stdout and f"OUT=[{expected}]" in first.stdout, first.stderr
    (expected / "partial-report.md").write_text("left by the blocked run\n", encoding="utf-8")

    new_head = _commit(dev_tree, "two\n")
    second = _create(tmp_path, base, dev_tree, _TASK, "ai_rc/v1.0.0")

    assert "RC=[0]" in second.stdout, f"stdout={second.stdout}\nstderr={second.stderr}"
    assert f"OUT=[{expected}]" in second.stdout, "the path alone is printed on stdout"
    assert _git(expected, "rev-parse", "HEAD") == new_head, "the worktree is checked out at the new ref"
    assert (expected / "README.md").read_text(encoding="utf-8") == "two\n"
    assert "already exists at" in second.stderr, second.stderr
    listed = _git(dev_tree, "worktree", "list", "--porcelain")
    assert listed.count(f"worktree {expected}") == 1, listed


def test_stale_registration_without_a_directory_is_pruned_before_create(tmp_path: pathlib.Path) -> None:
    dev_tree, base = _repo(tmp_path), _base(tmp_path)
    expected = base / _TASK
    assert "RC=[0]" in _create(tmp_path, base, dev_tree, _TASK, "ai_rc/v1.0.0").stdout
    subprocess.run(["rm", "-rf", "--", str(expected)], check=True)

    result = _create(tmp_path, base, dev_tree, _TASK, "ai_rc/v1.0.0")

    assert "RC=[0]" in result.stdout, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert (expected / "README.md").exists()


def test_another_tasks_stale_registration_is_not_pruned(tmp_path: pathlib.Path) -> None:
    dev_tree, base = _repo(tmp_path), _base(tmp_path)
    _git(dev_tree, "branch", "ai_feature/CODER-20260101-009-other")
    other = base / "CODER-20260101-009-other"
    _git(dev_tree, "worktree", "add", "-q", str(other), "ai_feature/CODER-20260101-009-other")
    subprocess.run(["rm", "-rf", "--", str(other)], check=True)
    assert "RC=[0]" in _create(tmp_path, base, dev_tree, _TASK, "ai_rc/v1.0.0").stdout

    result = _create(tmp_path, base, dev_tree, _TASK, "ai_rc/v1.0.0")

    assert "RC=[0]" in result.stdout, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert f"worktree {other}" in _git(dev_tree, "worktree", "list", "--porcelain"), (
        "another task's stale registration was pruned"
    )


def test_symlink_to_a_worktree_outside_the_base_is_refused_and_kept(tmp_path: pathlib.Path) -> None:
    dev_tree, base = _repo(tmp_path), _base(tmp_path)
    outside = tmp_path / "elsewhere" / "operator-worktree"
    outside.parent.mkdir()
    _git(dev_tree, "worktree", "add", "-q", "--detach", str(outside), "ai_rc/v1.0.0")
    (outside / "keep-me.txt").write_text("operator work\n", encoding="utf-8")
    link = base / _TASK
    link.symlink_to(outside, target_is_directory=True)

    result = _create(tmp_path, base, dev_tree, _TASK, "ai_rc/v1.0.0")

    assert "RC=[1]" in result.stdout, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert _REFUSAL_KEY in result.stderr, result.stderr
    assert (outside / "keep-me.txt").exists(), "a worktree outside the base was removed"
    assert f"worktree {outside}" in _git(dev_tree, "worktree", "list", "--porcelain")
    assert link.is_symlink(), "the symlink is left for the operator to inspect"


def test_task_path_resolving_to_the_base_parent_is_refused_with_nothing_deleted(tmp_path: pathlib.Path) -> None:
    dev_tree, base = _repo(tmp_path), _base(tmp_path)
    sentinel = base.parent / "parent-sentinel"
    sentinel.write_text("must survive\n", encoding="utf-8")

    result = _create(tmp_path, base, dev_tree, "..", "ai_rc/v1.0.0")

    assert "RC=[1]" in result.stdout, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert _REFUSAL_KEY in result.stderr, result.stderr
    assert sentinel.exists() and base.is_dir()
