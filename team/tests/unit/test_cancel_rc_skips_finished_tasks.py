"""
test_cancel_rc_skips_finished_tasks.py
======================================
Behavioural tests for which tasks cm/cancel-rc.sh treats as pending work
when it cancels a release candidate.

Contract under test: every task whose README references the cancelled RC
version is a candidate, but a task whose status.md ``## State`` reads
``DONE`` or ``WONT-DO`` is finished and is skipped — only non-terminal tasks
are listed as pending (and repeated in the post-cancel WONT-DO reminder).
The state value lives on the line beneath the ``## State`` heading, which is
the only shape the status writer produces, so these fixtures write it that
way.

Design:
  - The shipped entry point is driven as a real subprocess, from a working
    directory outside the repository, under an environment rebuilt with
    ``build_scrubbed_env`` (the operator's kanban variables never reach it).
  - Every path lives under pytest's ``tmp_path``: a throwaway kanban root,
    and a local git repository with no remote. ``push_to_remote = false``
    keeps the script off origin entirely.
  - The subprocess runs in its own process group, killed whole on timeout.
"""

from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import sys

import pytest
from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent  # team/
_CANCEL_RC = _TEAM_DIR / "scripts" / "cm" / "cancel-rc.sh"
_CM_PY_SRC = _TEAM_DIR / "pgai_agent_kanban" / "cm"

_PROJECT = "cancel-rc-state-fixture"
_VERSION = "v0.1.0"
_TIMEOUT_SECONDS = 60


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip the module when git is absent; every test builds a git repo."""
    require_binary("git", label="cancel-rc-skips-finished-tasks")


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )


def _build_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """A local repo on ``main`` holding a local ``rc/<version>`` branch."""
    repo = tmp_path / "dev_repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "--local", "user.email", "test@pgai.local")
    _git(repo, "config", "--local", "user.name", "PGAI Test")
    (repo / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "Initial commit")
    _git(repo, "branch", f"rc/{_VERSION}")
    return repo


def _build_kanban_root(tmp_path: pathlib.Path, repo: pathlib.Path) -> pathlib.Path:
    """A throwaway kanban root with one project whose Active RC is _VERSION."""
    root = tmp_path / "kanban_root"
    proj = root / "projects" / _PROJECT
    (proj / "tasks" / "queues").mkdir(parents=True)
    (proj / "release-state").mkdir()
    (root / "logs").mkdir()
    (root / "tmp").mkdir()

    (root / "kanban.cfg").write_text(
        "[paths]\n\n[chain]\npm_mode = automatic\n", encoding="utf-8"
    )
    (root / "projects.cfg").write_text(
        f"[project:{_PROJECT}]\npriority=1\nenabled=true\n", encoding="utf-8"
    )
    (proj / "project.cfg").write_text(
        "[project]\n"
        f"project_name = {_PROJECT}\n"
        f"dev_tree_path = {repo}\n"
        "workflow_type = release\n"
        "push_to_remote = false\n",
        encoding="utf-8",
    )
    (proj / "release-state.md").write_text(
        "# Release State\n\n"
        f"## Active RC\n{_VERSION}\n\n"
        "## RC Opened At\n2026-01-01T00:00:00Z\n\n"
        "## RC Opened By Task\nCM-fixture-open\n\n"
        "## Last Released\nnone\n",
        encoding="utf-8",
    )

    # cancel-rc.sh reads release-state.md and writes the per-RC JSON through
    # the cm helpers under the kanban root, as the live install lays them out.
    cm_dir = root / "pgai_agent_kanban" / "cm"
    cm_dir.mkdir(parents=True)
    for src in _CM_PY_SRC.glob("*.py"):
        (cm_dir / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return root


def _add_task(root: pathlib.Path, task_id: str, state_block: str) -> None:
    """Create a task referencing _VERSION whose ## State section is *state_block*."""
    task_dir = root / "projects" / _PROJECT / "tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "README.md").write_text(
        f"# Task: {task_id}\n\n## Release Version\n{_VERSION}\n", encoding="utf-8"
    )
    (task_dir / "status.md").write_text(
        f"# Status\n\n## Task\n{task_id}\n\n{state_block}"
        "## Summary\nfixture\n\n## Needs Human\nno\n",
        encoding="utf-8",
    )


def _canonical_state(state: str) -> str:
    """The ## State section exactly as the status writer lays it out."""
    return f"## State\n{state}\n\n"


# ---------------------------------------------------------------------------
# Running the entry point
# ---------------------------------------------------------------------------


def _run_cancel_rc(tmp_path: pathlib.Path, root: pathlib.Path, repo: pathlib.Path):
    foreign_cwd = tmp_path / "elsewhere"
    foreign_cwd.mkdir()
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        # The interpreter running this suite, so the script's python3 has the
        # same version the tree is written for.
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
            "REPO_ROOT": str(repo),
        },
    )
    (tmp_path / "home").mkdir(exist_ok=True)
    argv = ["bash", str(_CANCEL_RC), "--project", _PROJECT, "--key", _VERSION, "--yes"]
    proc = subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env, cwd=str(foreign_cwd), start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.communicate()
        raise
    return proc.returncode, stdout, stderr


def _diagnostic(rc: int, stdout: str, stderr: str) -> str:
    return f"exit={rc}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}"


def _listed_pending(stdout: str) -> set[str]:
    """Task IDs listed under the 'Pending tasks for <version>:' heading."""
    lines = stdout.splitlines()
    heading = f"Pending tasks for {_VERSION}:"
    if heading not in lines:
        return set()
    listed: set[str] = set()
    for line in lines[lines.index(heading) + 1:]:
        if not line.startswith("  - "):
            break
        listed.add(line[len("  - "):].split(" ", 1)[0])
    return listed


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_only_the_non_terminal_task_is_listed_as_pending(tmp_path: pathlib.Path) -> None:
    """DONE and WONT-DO tasks referencing the RC are skipped; BACKLOG is listed."""
    repo = _build_repo(tmp_path)
    root = _build_kanban_root(tmp_path, repo)
    _add_task(root, "CODER-20260101-001-finished", _canonical_state("DONE"))
    _add_task(root, "CODER-20260101-002-declined", _canonical_state("WONT-DO"))
    _add_task(root, "CODER-20260101-003-live", _canonical_state("BACKLOG"))

    rc, out, err = _run_cancel_rc(tmp_path, root, repo)

    assert rc == 0, _diagnostic(rc, out, err)
    assert _listed_pending(out) == {"CODER-20260101-003-live"}, (
        "Only the BACKLOG task should be listed as pending; finished "
        "(DONE/WONT-DO) tasks must be skipped.\n" + _diagnostic(rc, out, err)
    )
    assert "CODER-20260101-003-live (BACKLOG)" in out, _diagnostic(rc, out, err)
    assert "CODER-20260101-001-finished" not in out, _diagnostic(rc, out, err)
    assert "CODER-20260101-002-declined" not in out, _diagnostic(rc, out, err)


def test_hand_edited_terminal_states_are_still_skipped(tmp_path: pathlib.Path) -> None:
    """A blank line under the heading, trailing spaces, or lowercase still read terminal."""
    repo = _build_repo(tmp_path)
    root = _build_kanban_root(tmp_path, repo)
    _add_task(root, "CODER-20260101-004-spaced", "## State\n\n  DONE  \n\n")
    _add_task(root, "CODER-20260101-005-lower", "## State   \nwont-do\n\n")
    _add_task(root, "CODER-20260101-006-working", "## State\n\nWORKING\n\n")

    rc, out, err = _run_cancel_rc(tmp_path, root, repo)

    assert rc == 0, _diagnostic(rc, out, err)
    assert _listed_pending(out) == {"CODER-20260101-006-working"}, _diagnostic(rc, out, err)


def test_task_with_empty_state_stays_listed(tmp_path: pathlib.Path) -> None:
    """An empty ## State is not terminal: the task stays listed rather than hidden."""
    repo = _build_repo(tmp_path)
    root = _build_kanban_root(tmp_path, repo)
    _add_task(root, "CODER-20260101-007-blank", "## State\n\n")
    _add_task(root, "CODER-20260101-008-finished", _canonical_state("DONE"))

    rc, out, err = _run_cancel_rc(tmp_path, root, repo)

    assert rc == 0, _diagnostic(rc, out, err)
    assert _listed_pending(out) == {"CODER-20260101-007-blank"}, _diagnostic(rc, out, err)
