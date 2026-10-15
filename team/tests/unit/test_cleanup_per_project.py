"""
test_cleanup_per_project.py
===========================
Behavioral unit tests for team/scripts/cleanup/cleanup.sh when it is invoked
without a project.

With neither --project nor PGAI_PROJECT_NAME, cleanup.sh runs once per
project registered in projects.cfg (priority order) by re-invoking itself as
``cleanup.sh --project <p>`` with the same mode flags. It prints one result
line per project, keeps going past a failing project, and exits non-zero only
when a per-project run failed. This is the shape the shipped weekly cron line
uses (a bare ``cleanup.sh >> logs/cron-cleanup.log``).

Locked contracts:

  1. A project-less --dry-run against a sandbox projects.cfg of two projects
     runs both, each against its own tasks directory, and mutates nothing.
  2. A failing project is reported, the next project still runs, and the
     overall exit status is non-zero.
  3. --trivial-only and --temp-only keep working without --project.
  4. --project <p> still runs exactly that one project, with no iteration.
  5. An empty registry is a logged no-op (exit 0), not a failure.
  6. With both root variables unset, the project-less path fails closed and
     never reads the $HOME/pgai_agent_kanban registry.
  7. Every cleanup.sh invocation in the shipped crontab and pseudocron
     templates succeeds against a two-project sandbox -- none of them fails
     for lack of a project.
  8. --temp-only keeps a live task's worktree under
     <temp>/projects/<p>/worktrees/ (untracked files included), removes a
     DONE task's, keeps a non-allowlisted entry in a project's temp dir, and
     judges each project's worktrees against that project's own tasks dir.

Every invocation runs against a throwaway kanban root built under pytest's
tmp_path with all five live-install names (PGAI_AGENT_KANBAN_ROOT_PATH,
KANBAN_ROOT, PGAI_TASKS_DIR, PGAI_PROJECT_ROOT, PGAI_AGENT_KANBAN_TEMP_DIR)
set or cleared, PGAI_PROJECT_NAME cleared, and HOME pointed at an empty
directory so no user-level config file can redirect a path. Nothing here
points cleanup.sh at the live install.
"""

from __future__ import annotations

import os
import pathlib
import re
import shlex
import time

import pytest

from tests.unit.shell_harness import run_bash

_CLEANUP_SCRIPT = "scripts/cleanup/cleanup.sh"
_PROJECTS = ("alpha", "beta")

# team/ -- this file lives at team/tests/unit/.
_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_TEMPLATE_DIR = _TEAM_DIR / "templates" / "install"
_TEMPLATE_GLOBS = ("crontab*.example", "pseudocron-*.cfg.example")

# A scheduled cleanup.sh job line, commented-out or live: the script path
# always carries its cleanup/ directory, which prose mentions of
# "cleanup.sh" in template comments do not. The arguments run up to the
# first output redirection (or end of line).
_INVOCATION_RE = re.compile(r"\S*/cleanup/cleanup\.sh\b(?P<args>[^>\n]*)")


def _diagnostic(result) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _snapshot(root: pathlib.Path) -> set[str]:
    """Return the set of relative paths for every file under *root*."""
    if not root.exists():
        return set()
    return {str(p.relative_to(root)) for p in root.rglob("*")}


def _sandbox_env(root: pathlib.Path, home: pathlib.Path) -> dict[str, str]:
    home.mkdir(parents=True, exist_ok=True)
    return {
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "temp"),
        # Cleared so each per-project child derives its own paths from the
        # sandbox root instead of refusing on an inherited export.
        "PGAI_TASKS_DIR": "",
        "PGAI_PROJECT_ROOT": "",
        # Cleared so the project-less path is the one under test.
        "PGAI_PROJECT_NAME": "",
        "HOME": str(home),
    }


def _seed_project(root: pathlib.Path, name: str, *, release_state: bool = True) -> pathlib.Path:
    """Create a minimal project tree a full cleanup run walks, including one
    terminal task folder old enough for Step 2 to count it. Returns the
    project's tasks directory."""
    project_dir = root / "projects" / name
    tasks_dir = project_dir / "tasks"
    task_dir = tasks_dir / f"CLAUDE-{name}-old-terminal-task"
    task_dir.mkdir(parents=True)
    (project_dir / "requirements").mkdir()
    status_file = task_dir / "status.md"
    status_file.write_text("## State\nDONE\n", encoding="utf-8")
    old_time = time.time() - (10 * 86400)
    os.utime(status_file, (old_time, old_time))
    if release_state:
        (project_dir / "release-state.md").write_text(
            "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n"
            "## RC Opened By Task\nnone\n",
            encoding="utf-8",
        )
    return tasks_dir


def _seed_registry(root: pathlib.Path, names=_PROJECTS) -> None:
    lines = []
    for priority, name in enumerate(names, start=1):
        lines.append(f"[project:{name}]\npriority = {priority}\n")
    (root / "projects.cfg").write_text("\n".join(lines), encoding="utf-8")


def _two_project_sandbox(tmp_path: pathlib.Path) -> tuple[pathlib.Path, dict[str, pathlib.Path]]:
    root = tmp_path / "kanban_root"
    (root / "logs" / "agents").mkdir(parents=True)
    tasks = {name: _seed_project(root, name) for name in _PROJECTS}
    _seed_registry(root)
    return root, tasks


# ---------------------------------------------------------------------------
# 1. Project-less dry-run runs every registered project
# ---------------------------------------------------------------------------


def test_projectless_dry_run_runs_every_registered_project(tmp_path: pathlib.Path) -> None:
    root, tasks = _two_project_sandbox(tmp_path)
    seeded = {
        name: tasks[name] / f"CLAUDE-{name}-old-terminal-task" / "status.md"
        for name in _PROJECTS
    }

    result = run_bash(
        tmp_path,
        f"bash {_CLEANUP_SCRIPT} --dry-run",
        extra_env=_sandbox_env(root, tmp_path / "home"),
    )

    assert result.returncode == 0, _diagnostic(result)
    assert "no project specified" not in result.stdout + result.stderr, _diagnostic(result)
    assert "running once per registered project (2): alpha beta" in result.stdout, _diagnostic(result)
    for name in _PROJECTS:
        # Each project's own run walked its own tasks directory ...
        assert f"Step 2: report terminal task folders in {tasks[name]} (none are deleted) ---" in result.stdout, (
            _diagnostic(result)
        )
        # ... and logged exactly one result line.
        assert result.stdout.count(f"cleanup: project '{name}': ok") == 1, _diagnostic(result)
        # The old terminal folder is counted, never deleted.
        assert seeded[name].is_file(), _diagnostic(result)
    assert "per-project runs complete: 2 ok, 0 failed" in result.stdout, _diagnostic(result)
    # Registry order is preserved: alpha (priority 1) runs before beta.
    assert result.stdout.index("project 'alpha': ok") < result.stdout.index("project 'beta': ok")


# ---------------------------------------------------------------------------
# 2. A failing project is reported, the rest still run, exit is non-zero
# ---------------------------------------------------------------------------


def test_projectless_run_continues_past_a_failing_project_and_exits_nonzero(
    tmp_path: pathlib.Path,
) -> None:
    root, _tasks = _two_project_sandbox(tmp_path)
    # alpha's preflight refuses: no release-state.md for it anywhere.
    (root / "projects" / "alpha" / "release-state.md").unlink()

    result = run_bash(
        tmp_path,
        f"bash {_CLEANUP_SCRIPT} --dry-run",
        extra_env=_sandbox_env(root, tmp_path / "home"),
    )

    assert result.returncode != 0, _diagnostic(result)
    assert "cleanup: project 'alpha': FAILED (exit 1)" in result.stdout, _diagnostic(result)
    assert "cleanup: project 'beta': ok" in result.stdout, _diagnostic(result)
    assert "per-project runs complete: 1 ok, 1 failed" in result.stdout, _diagnostic(result)
    # The failing child's own error reached the operator.
    assert "release-state.md not found" in result.stderr, _diagnostic(result)


# ---------------------------------------------------------------------------
# 3. --trivial-only and --temp-only work without --project
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode_flag", ["--trivial-only", "--temp-only"])
def test_projectless_single_purge_modes_succeed(tmp_path: pathlib.Path, mode_flag: str) -> None:
    root, _tasks = _two_project_sandbox(tmp_path)
    mode = mode_flag.removeprefix("--")

    result = run_bash(
        tmp_path,
        f"bash {_CLEANUP_SCRIPT} {mode_flag} --dry-run",
        extra_env=_sandbox_env(root, tmp_path / "home"),
    )

    assert result.returncode == 0, _diagnostic(result)
    assert "no project specified" not in result.stdout + result.stderr, _diagnostic(result)
    assert result.stdout.count(f"cleanup complete ({mode})") == len(_PROJECTS), _diagnostic(result)
    for name in _PROJECTS:
        assert f"cleanup: project '{name}': ok" in result.stdout, _diagnostic(result)
    # The single-purge modes never reach the full run's steps.
    assert "Step 2:" not in result.stdout, _diagnostic(result)


# ---------------------------------------------------------------------------
# 4. --project <p> runs only that project
# ---------------------------------------------------------------------------


def test_project_flag_runs_only_the_named_project(tmp_path: pathlib.Path) -> None:
    root, tasks = _two_project_sandbox(tmp_path)

    result = run_bash(
        tmp_path,
        f"bash {_CLEANUP_SCRIPT} --project beta --dry-run",
        extra_env=_sandbox_env(root, tmp_path / "home"),
    )

    assert result.returncode == 0, _diagnostic(result)
    assert "running once per registered project" not in result.stdout, _diagnostic(result)
    assert "cleanup: project '" not in result.stdout, _diagnostic(result)
    assert f"Step 2: report terminal task folders in {tasks['beta']} (none are deleted) ---" in result.stdout, (
        _diagnostic(result)
    )
    assert str(tasks["alpha"]) not in result.stdout, _diagnostic(result)
    assert result.stdout.count("cleanup: cleanup complete") == 1, _diagnostic(result)


# ---------------------------------------------------------------------------
# 5. No registered project: logged no-op
# ---------------------------------------------------------------------------


def test_projectless_run_with_no_registered_project_is_a_logged_noop(
    tmp_path: pathlib.Path,
) -> None:
    root = tmp_path / "kanban_root"
    root.mkdir()

    result = run_bash(
        tmp_path,
        f"bash {_CLEANUP_SCRIPT} --dry-run",
        extra_env=_sandbox_env(root, tmp_path / "home"),
    )

    assert result.returncode == 0, _diagnostic(result)
    assert "no project registered in" in result.stdout, _diagnostic(result)
    assert "nothing to clean" in result.stdout, _diagnostic(result)
    assert "Step " not in result.stdout, _diagnostic(result)
    assert "ERROR" not in result.stderr, _diagnostic(result)


# ---------------------------------------------------------------------------
# 6. Both roots unset: fail closed, never read the $HOME registry
# ---------------------------------------------------------------------------


def test_projectless_run_with_both_roots_unset_fails_closed(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """With neither root variable set, projects_cfg_path would fall back to
    $HOME/pgai_agent_kanban/projects.cfg. HOME points at a decoy holding a
    registry there; the run must refuse before iterating it."""
    decoy_home = tmp_path / "decoy_home"
    decoy_root = decoy_home / "pgai_agent_kanban"
    decoy_root.mkdir(parents=True)
    _seed_registry(decoy_root)
    before = _snapshot(decoy_home)
    monkeypatch.delenv("KANBAN_ROOT", raising=False)
    monkeypatch.delenv("PGAI_AGENT_KANBAN_ROOT_PATH", raising=False)

    result = run_bash(
        tmp_path,
        f"bash {_CLEANUP_SCRIPT} --dry-run",
        extra_env={
            "HOME": str(decoy_home),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "temp"),
            "PGAI_TASKS_DIR": "",
            "PGAI_PROJECT_ROOT": "",
            "PGAI_PROJECT_NAME": "",
        },
    )

    assert result.returncode != 0, _diagnostic(result)
    assert "KANBAN_ROOT is not set" in result.stderr, _diagnostic(result)
    assert "running once per registered project" not in result.stdout, _diagnostic(result)
    assert "cleanup: project '" not in result.stdout, _diagnostic(result)
    assert _snapshot(decoy_home) == before, _diagnostic(result)


# ---------------------------------------------------------------------------
# 7. Shipped template invocations never fail for lack of a project
# ---------------------------------------------------------------------------


def _template_invocations() -> list[tuple[str, str]]:
    """Return (template filename, argument string) for every cleanup.sh job
    line -- live or commented-out -- in the shipped crontab and pseudocron
    templates."""
    found = []
    for pattern in _TEMPLATE_GLOBS:
        for path in sorted(_TEMPLATE_DIR.glob(pattern)):
            for line in path.read_text(encoding="utf-8").splitlines():
                match = _INVOCATION_RE.search(line)
                if match:
                    found.append((path.name, match.group("args").strip()))
    return found


_INVOCATIONS = _template_invocations()


def test_every_shipped_template_schedules_cleanup() -> None:
    """Every crontab and pseudocron template carries at least one cleanup.sh
    job line, so the parametrized check below is never vacuous, and every
    crontab template keeps the bare weekly line operators already have
    installed."""
    templates = sorted(
        p.name for pattern in _TEMPLATE_GLOBS for p in _TEMPLATE_DIR.glob(pattern)
    )
    assert templates, f"no templates found under {_TEMPLATE_DIR}"
    with_invocation = {name for name, _args in _INVOCATIONS}
    missing = [name for name in templates if name not in with_invocation]
    assert not missing, f"templates with no cleanup.sh job line: {missing}"
    for name in templates:
        if name.startswith("crontab"):
            assert (name, "") in _INVOCATIONS, (
                f"{name} no longer carries the bare weekly cleanup.sh line"
            )


@pytest.mark.parametrize(
    ("template", "args"),
    _INVOCATIONS,
    ids=[f"{name}:{args or 'bare'}" for name, args in _INVOCATIONS],
)
def test_template_cleanup_invocation_succeeds_without_a_project(
    tmp_path: pathlib.Path, template: str, args: str
) -> None:
    """Runs the template's own arguments exactly as scheduled (no --dry-run
    added) against a two-project sandbox root."""
    root, _tasks = _two_project_sandbox(tmp_path)
    argv = " ".join(shlex.quote(a) for a in shlex.split(args))

    result = run_bash(
        tmp_path,
        f"bash {_CLEANUP_SCRIPT} {argv}",
        extra_env=_sandbox_env(root, tmp_path / "home"),
    )

    assert result.returncode == 0, f"{template}: cleanup.sh {args}\n" + _diagnostic(result)
    assert "no project specified" not in result.stdout + result.stderr, _diagnostic(result)
    if "--project" not in args:
        for name in _PROJECTS:
            assert f"cleanup: project '{name}': ok" in result.stdout, _diagnostic(result)


# ---------------------------------------------------------------------------
# 8. --temp-only judges each project's worktrees against its own tasks
# ---------------------------------------------------------------------------


def _task(tasks_dir: pathlib.Path, task_id: str, state: str) -> None:
    task_dir = tasks_dir / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "status.md").write_text(f"# Status\n\n## State\n{state}\n", encoding="utf-8")


def _worktree(temp: pathlib.Path, project: str, task_id: str) -> pathlib.Path:
    worktree = temp / "projects" / project / "worktrees" / task_id
    worktree.mkdir(parents=True)
    (worktree / "untracked-work.txt").write_text("uncommitted\n", encoding="utf-8")
    return worktree


def test_temp_only_keeps_live_task_worktrees_per_project(tmp_path: pathlib.Path) -> None:
    root, tasks = _two_project_sandbox(tmp_path)
    temp = root / "temp"
    for name in _PROJECTS:
        (root / "projects" / name / "project.cfg").write_text(
            f"[project]\nproject_name = {name}\n", encoding="utf-8"
        )
    _task(tasks["alpha"], "CODER-20260101-001-alpha-working", "WORKING")
    _task(tasks["alpha"], "CODER-20260101-002-alpha-done", "DONE")
    _task(tasks["beta"], "CODER-20260101-003-beta-working", "WORKING")
    _task(tasks["beta"], "TESTER-20260101-004-beta-blocked", "BLOCKED")

    alpha_working = _worktree(temp, "alpha", "CODER-20260101-001-alpha-working")
    alpha_done = _worktree(temp, "alpha", "CODER-20260101-002-alpha-done")
    beta_working = _worktree(temp, "beta", "CODER-20260101-003-beta-working")
    beta_blocked = _worktree(temp, "beta", "TESTER-20260101-004-beta-blocked")
    # Named after alpha's live task, but under beta: beta has no such task.
    beta_stray = _worktree(temp, "beta", "CODER-20260101-001-alpha-working")
    foreign = temp / "projects" / "alpha" / "agent-notes"
    foreign.mkdir(parents=True)
    allowlisted = temp / "projects" / "alpha" / "pgai_tmp.abc123"
    allowlisted.write_text("scratch\n", encoding="utf-8")

    result = run_bash(
        tmp_path,
        f"bash {_CLEANUP_SCRIPT} --temp-only",
        extra_env=_sandbox_env(root, tmp_path / "home"),
    )

    assert result.returncode == 0, _diagnostic(result)
    for kept in (alpha_working, beta_working, beta_blocked):
        assert (kept / "untracked-work.txt").exists(), f"live task worktree removed: {kept}\n" + _diagnostic(result)
    assert not alpha_done.exists(), "a DONE task's worktree must be removed\n" + _diagnostic(result)
    assert not beta_stray.exists(), (
        "beta's worktrees are judged against beta's tasks, where this task does not exist\n" + _diagnostic(result)
    )
    assert foreign.is_dir(), "a non-allowlisted entry in a project temp dir is foreign and kept"
    assert not allowlisted.exists(), "an allowlisted transient in a project temp dir is still purged"
    assert (temp / "projects" / "alpha").is_dir() and (temp / "projects" / "beta").is_dir()
