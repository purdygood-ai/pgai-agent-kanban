"""
test_overwatch_fix_deferred_under_halt_bash.py
==============================================
A present HALT means OVERWATCH keeps observing read-only and skips the fix.

Covered, each against a throwaway kanban root under pytest's tmp_path:

  1. overwatch_halt_first_fix (scripts/lib/overwatch_protocol.sh):
     - an operator's project HALT, and separately the fleet-wide HALT,
       defer the wrapped fix: it never runs, the wrapper returns sentinel 5,
       the HALT file is byte-identical afterwards, no project HALT is
       created, the repo lock is free again, and one "fix deferred: HALT
       present" row is logged dual-channel and in the action log;
     - --ship-policy-resolution, the one opt-in, runs the fix under a
       project HALT and leaves that HALT exactly as it was, but the
       fleet-wide HALT still defers it;
     - an unknown option is refused and nothing runs.
  2. check-block-triage.sh under a project HALT: an ordinary block is left
     unchanged and deferred, while a ship_policy_halt block is still handed
     to the resolution stage through the opt-in; under the fleet-wide HALT
     the stage is not called at all.
  3. overwatch-sweep.sh with a HALT on one of two projects: the halted
     project's worktree fix is deferred and its read-only scans still run
     (the stale-worktree scan inspects it, the blocked-task report lists
     its blocked task); the other project is repaired as usual.

No test touches the live install: every root, repository and worktree is
built under tmp_path, and every subprocess starts from an explicit
environment.
"""

from __future__ import annotations

import fcntl
import os
import pathlib
import shlex
import subprocess

import pytest

from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.test_block_triage_stage_deferral_and_operator_verbs import (
    _PROJECT as _TRIAGE_PROJECT,
    _SHIP_POLICY_HALT,
    Sandbox as TriageSandbox,
)

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_LIB = _TEAM_DIR / "scripts" / "lib"
_SWEEP = _TEAM_DIR / "scripts" / "overwatch-sweep.sh"
_OPERATOR_HALT = "operator halt: hand patch in progress\n"


@pytest.fixture(autouse=True)
def _require_git() -> None:
    require_binary("git", label="overwatch-fix-deferred-under-halt")


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _env(root: pathlib.Path, home: pathlib.Path, **extra: str) -> dict:
    home.mkdir(parents=True, exist_ok=True)
    return build_scrubbed_env(
        home_dir=home,
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
            "HALT_OVERWATCH": "",
            **extra,
        },
    )


def _build_root(root: pathlib.Path, *projects: str) -> None:
    """A minimal kanban root registering *projects*, each with OVERWATCH state."""
    (root / "locks").mkdir(parents=True, exist_ok=True)
    (root / "tmp").mkdir(parents=True, exist_ok=True)
    (root / "projects.cfg").write_text(
        "".join(f"[project:{name}]\npriority = {i + 1}\n\n" for i, name in enumerate(projects)), encoding="utf-8"
    )
    for name in projects:
        state = root / "projects" / name / "overwatch"
        (state / "backups").mkdir(parents=True, exist_ok=True)
        (state / "actions.log").write_text("", encoding="utf-8")


def _actions(root: pathlib.Path, project: str) -> str:
    return (root / "projects" / project / "overwatch" / "actions.log").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. The wrapper
# ---------------------------------------------------------------------------


def _run_wrapper(tmp_path: pathlib.Path, root: pathlib.Path, project: str, option: str = "") -> subprocess.CompletedProcess:
    """Run overwatch_halt_first_fix around a fix that leaves a marker file."""
    marker = tmp_path / "fix-ran"
    script = (
        "set +e\n"
        f"cd {shlex.quote(str(_TEAM_DIR))}\n"
        f"source {shlex.quote(str(_LIB / 'project_paths.sh'))}\n"
        f"source {shlex.quote(str(_LIB / 'overwatch_protocol.sh'))}\n"
        f"_fix() {{ touch {shlex.quote(str(marker))}; }}\n"
        f"overwatch_halt_first_fix {option} _fix\n"
        'echo "WRAPPER_EXIT=$?"\n'
    )
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=60, cwd=str(tmp_path),
        env=_env(root, tmp_path / "home", OVERWATCH_PROJECT=project),
    )


def _lock_is_free(root: pathlib.Path, project: str) -> bool:
    lock = root / "locks" / f"repo-wake-{project}.lock"
    if not lock.exists():
        return True
    with open(lock, "a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        fcntl.flock(handle, fcntl.LOCK_UN)
    return True


@pytest.mark.parametrize("scope", ["project", "fleet"])
def test_a_present_halt_defers_the_wrapped_fix_and_changes_nothing(tmp_path, scope):
    root = tmp_path / "kanban"
    _build_root(root, "proj-a")
    project_halt = root / "projects" / "proj-a" / "HALT"
    halt = project_halt if scope == "project" else root / "HALT"
    halt.write_text(_OPERATOR_HALT, encoding="utf-8")

    result = _run_wrapper(tmp_path, root, "proj-a")

    assert "WRAPPER_EXIT=5" in result.stdout, _diagnostic(result)
    assert not (tmp_path / "fix-ran").exists(), "the fix ran under a HALT"
    assert halt.read_text(encoding="utf-8") == _OPERATOR_HALT
    if scope == "fleet":
        assert not project_halt.exists(), "a deferred fix must not create the project HALT"
    assert _lock_is_free(root, "proj-a"), "the deferral left the repo lock held"
    assert "error:   overwatch.fix_deferred_halt_present" in result.stderr, _diagnostic(result)
    assert f"fix deferred: HALT present at {halt}" in result.stderr, _diagnostic(result)
    assert "remedy:  cat " in result.stderr
    rows = [r for r in _actions(root, "proj-a").splitlines() if "\tfix-deferred\t" in r]
    assert len(rows) == 1, _actions(root, "proj-a")
    assert f"fix deferred: HALT present at {halt}" in rows[0]
    assert "\t_fix\t" in rows[0], rows[0]


def test_the_ship_policy_opt_in_runs_the_fix_under_the_project_halt_and_leaves_it(tmp_path):
    root = tmp_path / "kanban"
    _build_root(root, "proj-a")
    halt = root / "projects" / "proj-a" / "HALT"
    halt.write_text(_SHIP_POLICY_HALT, encoding="utf-8")

    result = _run_wrapper(tmp_path, root, "proj-a", "--ship-policy-resolution")

    assert "WRAPPER_EXIT=0" in result.stdout, _diagnostic(result)
    assert (tmp_path / "fix-ran").exists(), _diagnostic(result)
    assert halt.read_text(encoding="utf-8") == _SHIP_POLICY_HALT
    assert "fix-deferred" not in _actions(root, "proj-a")
    assert _lock_is_free(root, "proj-a")


def test_the_fleet_halt_defers_even_the_ship_policy_opt_in(tmp_path):
    root = tmp_path / "kanban"
    _build_root(root, "proj-a")
    (root / "projects" / "proj-a" / "HALT").write_text(_SHIP_POLICY_HALT, encoding="utf-8")
    (root / "HALT").write_text(_OPERATOR_HALT, encoding="utf-8")

    result = _run_wrapper(tmp_path, root, "proj-a", "--ship-policy-resolution")

    assert "WRAPPER_EXIT=5" in result.stdout, _diagnostic(result)
    assert not (tmp_path / "fix-ran").exists()
    assert f"fix deferred: HALT present at {root / 'HALT'}" in _actions(root, "proj-a")


def test_without_a_halt_the_fix_runs_and_its_own_halt_is_removed(tmp_path):
    root = tmp_path / "kanban"
    _build_root(root, "proj-a")
    result = _run_wrapper(tmp_path, root, "proj-a")
    assert "WRAPPER_EXIT=0" in result.stdout, _diagnostic(result)
    assert (tmp_path / "fix-ran").exists()
    assert not (root / "projects" / "proj-a" / "HALT").exists()
    assert "fix-deferred" not in _actions(root, "proj-a")


def test_an_unknown_wrapper_option_is_refused_and_nothing_runs(tmp_path):
    root = tmp_path / "kanban"
    _build_root(root, "proj-a")
    result = _run_wrapper(tmp_path, root, "proj-a", "--force-through-halt")
    assert "WRAPPER_EXIT=1" in result.stdout, _diagnostic(result)
    assert "unknown option '--force-through-halt'" in result.stderr
    assert not (tmp_path / "fix-ran").exists()


# ---------------------------------------------------------------------------
# 2. Block triage under a HALT
# ---------------------------------------------------------------------------


@pytest.fixture
def triage(tmp_path: pathlib.Path) -> TriageSandbox:
    return TriageSandbox(tmp_path)


def _narration(project: str) -> tuple:
    """The two files a deferral narrates into (the action log and the
    most-recent-refusal ledger the shared emitter records) -- never task,
    queue, bug or HALT state."""
    return (f"projects/{project}/overwatch/actions.log", f"projects/{project}/refusal-ledger.md")


def test_block_triage_leaves_an_ordinary_block_unchanged_under_an_operator_project_halt(triage):
    triage.block("missing_readme", ["worktree=gone"])
    triage.halt.write_text(_OPERATOR_HALT, encoding="utf-8")
    before = triage.tree(skip=_narration(_TRIAGE_PROJECT))

    result = triage.run_check()

    assert triage.tree(skip=_narration(_TRIAGE_PROJECT)) == before, "a deferred triage changed project state"
    assert "fix-deferred" in triage.actions, triage.actions
    assert "block-triage:" not in triage.actions
    assert "fix deferred: HALT present" in result.stderr


def test_block_triage_still_hands_a_ship_policy_block_to_the_stage_under_its_halt(triage):
    triage.halt.write_text(_SHIP_POLICY_HALT, encoding="utf-8")
    triage.block("ship_policy_halt", [f"halt=projects/{_TRIAGE_PROJECT}/HALT"], owner="CM")

    result = triage.run_check()

    assert "\tresolution-stage\t" in triage.actions, _diagnostic(result)
    assert "block-triage:ship_policy_halt:defer_to_resolution_stage" in triage.actions
    assert "fix-deferred" not in triage.actions
    assert triage.halt.read_text(encoding="utf-8") == _SHIP_POLICY_HALT


def test_the_fleet_halt_keeps_the_ship_policy_block_from_the_stage(triage):
    triage.halt.write_text(_SHIP_POLICY_HALT, encoding="utf-8")
    triage.block("ship_policy_halt", [f"halt=projects/{_TRIAGE_PROJECT}/HALT"], owner="CM")
    (triage.root / "HALT").write_text(_OPERATOR_HALT, encoding="utf-8")
    before = triage.tree(skip=_narration(_TRIAGE_PROJECT))

    triage.run_check()

    assert triage.tree(skip=_narration(_TRIAGE_PROJECT)) == before
    assert "\tresolution-stage\t" not in triage.actions
    assert "fix-deferred" in triage.actions


# ---------------------------------------------------------------------------
# 3. The sweep with one of two projects halted
# ---------------------------------------------------------------------------

_DONE_TASK = "CODER-20260101-001-finished-work"
_BLOCKED_TASK = "CODER-20260101-002-waiting-on-a-human"


def _git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True, text=True)


def _status(state: str) -> str:
    return f"# Status\n\n## State\n{state}\n\n## Summary\nFixture.\n\n## Needs Human\nyes\n"


def _sweep_project(root: pathlib.Path, scratch: pathlib.Path, name: str) -> pathlib.Path:
    """A project with a dev tree, a DONE task whose old worktree is clean, and
    a BLOCKED task with no Block Record. Returns the DONE task's worktree."""
    repo = scratch / f"repo-{name}"
    _git("init", "-q", "-b", "main", str(repo))
    _git("-C", str(repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "commit", "-q", "--allow-empty", "-m", "initial")
    _git("-C", str(repo), "branch", f"ai_feature/{_DONE_TASK}")
    worktree = scratch / f"worktrees-{name}" / _DONE_TASK
    _git("-C", str(repo), "worktree", "add", "-q", str(worktree), f"ai_feature/{_DONE_TASK}")
    os.utime(worktree, (1_600_000_000, 1_600_000_000))

    proj = root / "projects" / name
    (proj / "project.cfg").write_text(f"[project]\nproject_name = {name}\ndev_tree_path = {repo}\n", encoding="utf-8")
    queues = proj / "tasks" / "queues"
    queues.mkdir(parents=True)
    (queues / "coder_backlog.md").write_text(
        f"# CODER Backlog\n\n## Queue\n\n- [x] {_DONE_TASK} — done\n- [B] {_BLOCKED_TASK} — blocked\n", encoding="utf-8"
    )
    for task, state in ((_DONE_TASK, "DONE"), (_BLOCKED_TASK, "BLOCKED")):
        (proj / "tasks" / task).mkdir(parents=True)
        (proj / "tasks" / task / "status.md").write_text(_status(state), encoding="utf-8")
    return worktree


def _run_sweep(tmp_path: pathlib.Path, root: pathlib.Path, only: str) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["bash", str(_SWEEP), "--only", only], capture_output=True, text=True, timeout=300, cwd=str(tmp_path),
        env=_env(root, tmp_path / "home", OVERWATCH_STALE_WORKTREE_THRESHOLD_DAYS="1"),
    )
    assert result.returncode == 0, _diagnostic(result)
    return result


def test_the_sweep_defers_fixes_for_a_halted_project_and_still_scans_it(tmp_path):
    root = tmp_path / "kanban"
    _build_root(root, "halted", "running")
    halted_wt = _sweep_project(root, tmp_path, "halted")
    running_wt = _sweep_project(root, tmp_path, "running")
    halt = root / "projects" / "halted" / "HALT"
    halt.write_text(_OPERATOR_HALT, encoding="utf-8")

    worktrees = _run_sweep(tmp_path, root, "stale-worktrees")

    # The halted project: scanned, nothing repaired.
    assert halted_wt.is_dir(), "the halted project's worktree was removed"
    assert f"[halted] project HALT present ({halt})" in worktrees.stderr, _diagnostic(worktrees)
    assert f"worktree {halted_wt}: task={_DONE_TASK} is finished" in worktrees.stderr, _diagnostic(worktrees)
    halted_log = _actions(root, "halted")
    assert "\tsweep-fixes-deferred\t" in halted_log and "\tfix-deferred\t" in halted_log, halted_log
    assert "auto-fix:worktree-pruned" not in halted_log
    assert halt.read_text(encoding="utf-8") == _OPERATOR_HALT
    # The sibling project: repaired as usual, no HALT of its own left behind.
    assert not running_wt.exists(), _diagnostic(worktrees)
    assert "auto-fix:worktree-pruned" in _actions(root, "running")
    assert "fix-deferred" not in _actions(root, "running")
    assert not (root / "projects" / "running" / "HALT").exists()

    # A read-only report check still reports the halted project's blocked task.
    blocked = _run_sweep(tmp_path, root, "blocked-tasks")
    halted_part = blocked.stderr.split("[halted] sweep start", 1)[1].split("[halted] sweep end", 1)[0]
    assert _BLOCKED_TASK in halted_part, _diagnostic(blocked)
