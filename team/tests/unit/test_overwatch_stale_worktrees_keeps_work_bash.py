"""
test_overwatch_stale_worktrees_keeps_work_bash.py
=================================================
OVERWATCH's stale-worktree check (scripts/lib/overwatch-checks/
check-stale-worktrees.sh) removes only what a removal cannot lose.

Covered, each against a throwaway kanban root and a throwaway git
repository under pytest's tmp_path (no worktree outside them is touched):

  1. Only DONE and WONT-DO are finished: a BLOCKED task whose old worktree
     holds an uncommitted edit keeps the worktree and the edit.
  2. A DONE (and a WONT-DO) task whose old worktree is clean has it
     removed, with the project's repo lock held at the moment of removal,
     without --force, and its branch kept.
  3. A finished task's worktree holding an uncommitted edit, or an
     untracked file, is kept and reported dual-channel, never removed.
  4. Under the lock the task's state is read again: a task requeued to
     BACKLOG between the scan and the fix keeps its worktree; and the
     worktree is re-checked: an edit made between the scan and the fix
     keeps it too.
  5. The integration branch comes from branch_prefix: with xy_, a branch
     merged into xy_main is removable, while one held only by ai_main is
     not merged into any integration branch and is bug-filed, not removed.
  6. A HALT on the project defers the removal.
"""

from __future__ import annotations

import os
import pathlib
import shlex
import subprocess

import pytest

from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_LIB = _TEAM_DIR / "scripts" / "lib"
_CHECK = _LIB / "overwatch-checks" / "check-stale-worktrees.sh"
_PROJECT = "wt-proj"
_TASK = "CODER-20260101-001-old-task-work"
_OLD = 1_600_000_000  # an mtime far beyond the one-day threshold the tests use


@pytest.fixture(autouse=True)
def _require_git() -> None:
    require_binary("git", label="overwatch-stale-worktrees")


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


class Fixture:
    """A kanban root with one project whose dev tree has one task worktree."""

    def __init__(self, tmp_path: pathlib.Path, *, state: str, branch_prefix: str = "") -> None:
        self.tmp = tmp_path
        self.root = tmp_path / "kanban"
        self.proj = self.root / "projects" / _PROJECT
        self.repo = tmp_path / "repo"
        self.branch = f"ai_feature/{_TASK}"
        self.worktree = tmp_path / "worktrees" / _TASK
        for d in (self.proj / "overwatch" / "backups", self.proj / "bugs", self.root / "locks", self.root / "tmp"):
            d.mkdir(parents=True, exist_ok=True)
        (self.proj / "overwatch" / "actions.log").write_text("", encoding="utf-8")
        (self.root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8")
        cfg = f"[project]\nproject_name = {_PROJECT}\ndev_tree_path = {self.repo}\n"
        if branch_prefix:
            cfg += f"branch_prefix = {branch_prefix}\n"
        (self.proj / "project.cfg").write_text(cfg, encoding="utf-8")
        _git("init", "-q", "-b", "main", str(self.repo))
        (self.repo / "tracked.txt").write_text("original\n", encoding="utf-8")
        _git("-C", str(self.repo), "add", "tracked.txt")
        self.commit(self.repo, "initial")
        _git("-C", str(self.repo), "branch", self.branch)
        _git("-C", str(self.repo), "worktree", "add", "-q", str(self.worktree), self.branch)
        self.set_state(state)

    @staticmethod
    def commit(where: pathlib.Path, message: str) -> None:
        _git("-C", str(where), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "commit", "-q", "--allow-empty", "-m", message)

    @property
    def status(self) -> pathlib.Path:
        return self.proj / "tasks" / _TASK / "status.md"

    def set_state(self, state: str) -> None:
        self.status.parent.mkdir(parents=True, exist_ok=True)
        self.status.write_text(f"# Status\n\n## State\n{state}\n\n## Summary\nFixture.\n", encoding="utf-8")

    def age(self) -> None:
        os.utime(self.worktree, (_OLD, _OLD))

    @property
    def actions(self) -> str:
        return (self.proj / "overwatch" / "actions.log").read_text(encoding="utf-8")

    def registered(self) -> bool:
        return str(self.worktree) in _git("-C", str(self.repo), "worktree", "list", "--porcelain")

    def run(self, before_lock: str = "", at_removal: str = "") -> subprocess.CompletedProcess:
        """Run the check. *before_lock* runs as the fix asks for the repo lock
        (after the scan); *at_removal* runs as the fix calls the removal."""
        hooks = ""
        if before_lock:
            hooks += (
                f"source {shlex.quote(str(_LIB / 'lock_provenance.sh'))}\n"
                'eval "_real_acquire_waiting() $(declare -f pgai_lock_acquire_waiting | tail -n +2)"\n'
                f"pgai_lock_acquire_waiting() {{ {before_lock}; _real_acquire_waiting \"$@\"; }}\n"
            )
        if at_removal:
            hooks += (
                f"source {shlex.quote(str(_LIB / 'pp_run_ops.sh'))}\n"
                'eval "_real_run_ops() $(declare -f pp_run_ops | tail -n +2)"\n'
                f'pp_run_ops() {{ [[ " $* " == *" remove-finished "* ]] && {{ {at_removal}; }}; _real_run_ops "$@"; }}\n'
            )
        script = (
            "set -uo pipefail\n"
            f"cd {shlex.quote(str(_TEAM_DIR))}\n"
            f"{hooks}"
            f"source {shlex.quote(str(_CHECK))}\n"
            "rc=0\noverwatch_check_stale_worktrees || rc=$?\n"
            'echo "CHECK_EXIT=$rc"\n'
        )
        env = build_scrubbed_env(
            home_dir=self.tmp / "home",
            extra_env={
                "KANBAN_ROOT": str(self.root),
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.root / "tmp"),
                "OVERWATCH_PROJECT": _PROJECT,
                "OVERWATCH_STALE_WORKTREE_THRESHOLD_DAYS": "1",
                "HALT_OVERWATCH": "",
            },
        )
        (self.tmp / "home").mkdir(exist_ok=True)
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=120,
                                env=env, cwd=str(self.tmp))
        assert "CHECK_EXIT=0" in result.stdout, _diagnostic(result)
        return result


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


# ---------------------------------------------------------------------------
# 1. A BLOCKED task keeps its worktree and its uncommitted edit
# ---------------------------------------------------------------------------


def test_a_blocked_task_keeps_its_old_worktree_and_its_uncommitted_edit(tmp_path):
    fx = Fixture(tmp_path, state="BLOCKED")
    (fx.worktree / "tracked.txt").write_text("work a human was asked to look at\n", encoding="utf-8")
    fx.age()

    result = fx.run()

    assert fx.worktree.is_dir() and fx.registered(), _diagnostic(result)
    assert (fx.worktree / "tracked.txt").read_text(encoding="utf-8") == "work a human was asked to look at\n"
    assert "is not finished (state BLOCKED)" in result.stderr, _diagnostic(result)
    assert "auto-fix:" not in fx.actions


# ---------------------------------------------------------------------------
# 2. A finished task's clean worktree is removed under the repo lock
# ---------------------------------------------------------------------------

_LOCK_PROBE = (
    f'if flock -n "$KANBAN_ROOT/locks/repo-wake-{_PROJECT}.lock" true; '
    'then echo LOCK_FREE_AT_REMOVAL >&2; else echo LOCK_HELD_AT_REMOVAL >&2; fi'
)


@pytest.mark.parametrize("state", ["DONE", "WONT-DO"])
def test_a_finished_task_has_its_clean_old_worktree_removed_under_the_repo_lock(tmp_path, state):
    require_binary("flock", label="overwatch-stale-worktrees-lock-probe")
    fx = Fixture(tmp_path, state=state)
    fx.age()

    result = fx.run(at_removal=_LOCK_PROBE)

    assert not fx.worktree.exists() and not fx.registered(), _diagnostic(result)
    assert "LOCK_HELD_AT_REMOVAL" in result.stderr, _diagnostic(result)
    assert fx.branch in _git("-C", str(fx.repo), "branch", "--list", fx.branch), "the branch must be kept"
    rows = [r for r in fx.actions.splitlines() if "\tauto-fix:worktree-pruned\t" in r]
    assert len(rows) == 1 and "without --force" in rows[0], fx.actions
    assert not (fx.proj / "HALT").exists(), "the fix's own HALT must be removed afterwards"


# ---------------------------------------------------------------------------
# 3. A finished task's worktree holding changes is kept and reported
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("change", ["modified", "untracked"])
def test_a_finished_task_worktree_holding_changes_is_kept_and_reported(tmp_path, change):
    fx = Fixture(tmp_path, state="DONE")
    target = fx.worktree / ("tracked.txt" if change == "modified" else "notes.txt")
    target.write_text("not committed anywhere\n", encoding="utf-8")
    fx.age()

    result = fx.run()

    assert fx.worktree.is_dir() and fx.registered(), _diagnostic(result)
    assert target.read_text(encoding="utf-8") == "not committed anywhere\n"
    assert "error:   overwatch.stale_worktree_kept" in result.stderr, _diagnostic(result)
    assert "holds uncommitted or untracked changes" in result.stderr
    assert f"remedy:  git -C {fx.worktree} status --short" in result.stderr
    assert "\tworktree-kept:holds-work\t" in fx.actions, fx.actions
    assert "auto-fix:" not in fx.actions


# ---------------------------------------------------------------------------
# 4. Re-read under the lock: state and worktree
# ---------------------------------------------------------------------------


def test_a_task_requeued_between_the_scan_and_the_fix_keeps_its_worktree(tmp_path):
    fx = Fixture(tmp_path, state="DONE")
    fx.age()
    requeue = f"sed -i 's/^DONE$/BACKLOG/' {shlex.quote(str(fx.status))}"

    result = fx.run(before_lock=requeue)

    assert "## State\nBACKLOG" in fx.status.read_text(encoding="utf-8"), "the hook did not requeue the task"
    assert fx.worktree.is_dir() and fx.registered(), _diagnostic(result)
    assert "\tworktree-kept:task-not-finished\t" in fx.actions, fx.actions
    assert "the task is BACKLOG now" in result.stderr, _diagnostic(result)
    assert "auto-fix:" not in fx.actions


def test_an_edit_made_between_the_scan_and_the_fix_keeps_the_worktree(tmp_path):
    fx = Fixture(tmp_path, state="DONE")
    fx.age()
    late_edit = f"echo late > {shlex.quote(str(fx.worktree / 'late.txt'))}"

    result = fx.run(before_lock=late_edit)

    assert fx.worktree.is_dir() and (fx.worktree / "late.txt").is_file(), _diagnostic(result)
    assert "error:   overwatch.stale_worktree_kept" in result.stderr, _diagnostic(result)
    assert "\tworktree-kept:holds-work\t" in fx.actions, fx.actions


# ---------------------------------------------------------------------------
# 5. Integration branch from branch_prefix
# ---------------------------------------------------------------------------


def test_with_branch_prefix_xy_the_xy_main_branch_is_the_integration_branch(tmp_path):
    fx = Fixture(tmp_path, state="DONE", branch_prefix="xy_")
    fx.commit(fx.worktree, "task work")
    _git("-C", str(fx.repo), "branch", "xy_main", fx.branch)
    fx.age()

    result = fx.run()

    assert not fx.worktree.exists(), _diagnostic(result)
    assert "auto-fix:worktree-pruned" in fx.actions


def test_with_branch_prefix_xy_a_branch_held_only_by_ai_main_is_not_merged(tmp_path):
    fx = Fixture(tmp_path, state="DONE", branch_prefix="xy_")
    fx.commit(fx.worktree, "task work")
    _git("-C", str(fx.repo), "branch", "ai_main", fx.branch)
    fx.age()

    result = fx.run()

    assert fx.worktree.is_dir() and fx.registered(), _diagnostic(result)
    assert "carries commits; bug-filing" in result.stderr, _diagnostic(result)
    assert "auto-fix:" not in fx.actions


# ---------------------------------------------------------------------------
# 6. A HALT defers the removal
# ---------------------------------------------------------------------------


def test_a_project_halt_defers_the_removal(tmp_path):
    fx = Fixture(tmp_path, state="DONE")
    fx.age()
    (fx.proj / "HALT").write_text("operator halt\n", encoding="utf-8")

    result = fx.run()

    assert fx.worktree.is_dir() and fx.registered(), _diagnostic(result)
    assert "fix deferred: HALT present" in result.stderr
    assert "\tfix-deferred\t" in fx.actions and "auto-fix:" not in fx.actions
    assert (fx.proj / "HALT").read_text(encoding="utf-8") == "operator halt\n"
