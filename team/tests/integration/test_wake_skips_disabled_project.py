"""
test_wake_skips_disabled_project.py
===================================
A project projects.cfg sets ``enabled=false`` for is skipped by the wake
loop -- every agent's wake and the daily OVERWATCH wake -- with exactly one
"disabled in projects.cfg (enabled=false) — skipped" line per firing, and
nothing is dispatched, locked or begun for it. Its sibling keeps running.

Every case drives the REAL production entry point (``bash
scripts/wake/claude.sh --agent=...``) against a throwaway, self-contained
kanban root (tests/integration/wake_entrypoint_acceptance_harness.py) with
two document-workflow projects (git_mode=none: no dev tree or git
repository needed):

  alpha -- ``enabled=false`` (or a variant spelling), one BACKLOG writer task
  beta  -- no ``enabled`` key, one BACKLOG writer task

The provider CLI is the PATH-shadowing stub from
tests/fixtures/claude_provider_stub.py: each dispatch completes its task
and dumps the environment it received, whose ``PGAI_PROJECT_NAME`` names
the project it was dispatched for. No real model is reached; all scratch
lives under pytest's tmp_path.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import sys

import pytest

from tests.fixtures.claude_provider_stub import call_count, call_env, install_claude_provider_stub
from tests.integration.wake_entrypoint_acceptance_harness import (
    TEAM_DIR,
    make_foreign_cwd,
    run_wake_entrypoint,
    scrubbed_wake_env,
)

_MIRRORED_DIRS = ("scripts", "pgai_agent_kanban", "workflows", "pm-agent", "config")
_SKIP_LINE = "project alpha: disabled in projects.cfg (enabled=false) — skipped"
_TIMEOUT_SECONDS = 240


def _task_id(project: str) -> str:
    return f"WRITER-20260101-001-{project}-task"


def _build_root(tmp_path: pathlib.Path, alpha_enabled_line: str) -> pathlib.Path:
    """Register alpha (with *alpha_enabled_line*) and beta (no enabled key),
    each with one BACKLOG writer task, in a self-contained kanban root."""
    root = tmp_path / "kanban_root"
    root.mkdir(parents=True)
    (root / "kanban.cfg").write_text(
        "[chain]\npm_mode = automatic\n\n[release]\nmax_rc_retries = 3\n\n"
        "[wake]\nmax_tasks_per_wake = 5\nlock_wait_seconds = 0\n",
        encoding="utf-8",
    )
    (root / "projects.cfg").write_text(
        f"[project:alpha]\npriority=1\n{alpha_enabled_line}\n\n[project:beta]\npriority=2\n",
        encoding="utf-8",
    )
    for project in ("alpha", "beta"):
        proj = root / "projects" / project
        (proj / "tasks" / "queues").mkdir(parents=True)
        (proj / "project.cfg").write_text(
            f"[project]\nproject_name = {project}\nworkflow_type = document\n", encoding="utf-8"
        )
        task_id = _task_id(project)
        task_dir = proj / "tasks" / task_id
        task_dir.mkdir()
        (task_dir / "README.md").write_text(
            f"# Task: {task_id}\n\n## Task ID\n{task_id}\n\n## Role\nWRITER\n\n"
            "## Workflow Type\ndocument\n\n## Working Directory\nnone\n\n## Source Branch\nnone\n",
            encoding="utf-8",
        )
        (task_dir / "status.md").write_text(
            f"# Status\n\n## Task\n{task_id}\n\n## State\nBACKLOG\n\n## Blockers\nnone\n", encoding="utf-8"
        )
        for agent in ("pm", "writer", "tester", "cm", "overwatch"):
            body = f"- [ ] {task_id} — fixture task\n" if agent == "writer" else "No tasks queued.\n"
            (proj / "tasks" / "queues" / f"{agent}_backlog.md").write_text(
                f"# {project} {agent} backlog\n\n{body}", encoding="utf-8"
            )
    (root / "logs").mkdir()
    (root / "locks").mkdir()
    for dirname in _MIRRORED_DIRS:
        shutil.copytree(TEAM_DIR / dirname, root / dirname)
    return root


def _wake(tmp_path: pathlib.Path, root: pathlib.Path, agent: str):
    bin_dir = tmp_path / "stub_bin"
    control_dir = tmp_path / "stub_control"
    install_claude_provider_stub(bin_dir, control_dir)
    python3_link = bin_dir / "python3"
    if not python3_link.exists():
        python3_link.symlink_to(pathlib.Path(sys.executable).resolve())
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = scrubbed_wake_env(
        root,
        temp_dir=tmp_path / "scratch",
        home_dir=home,
        stub_bin_dir=bin_dir,
        extra_env={"PAUSE_BETWEEN_TASKS": "0", "MAX_TASK_SECONDS": "120", "KILL_GRACE_SECONDS": "5"},
    )
    result = run_wake_entrypoint(
        root, cwd=make_foreign_cwd(tmp_path), env=env, agent=agent, max_tasks=5, timeout=_TIMEOUT_SECONDS
    )
    dispatched = [call_env(control_dir, n).get("PGAI_PROJECT_NAME", "") for n in range(1, call_count(control_dir) + 1)]
    return result, dispatched


def _state(root: pathlib.Path, project: str) -> str:
    text = (root / "projects" / project / "tasks" / _task_id(project) / "status.md").read_text(encoding="utf-8")
    m = re.search(r"^##\s+State\s*\n+(\S+)", text, re.M)
    return m.group(1) if m else ""


@pytest.mark.parametrize("enabled_line", ["enabled=false", "enabled=False", "enabled= false "])
def test_wake_dispatches_for_the_enabled_project_only(tmp_path: pathlib.Path, enabled_line: str) -> None:
    root = _build_root(tmp_path, enabled_line)

    result, dispatched = _wake(tmp_path, root, "writer")

    out = result.combined
    assert result.returncode == 0, out
    assert dispatched == ["beta"], f"dispatch ledger {dispatched}\n{out}"
    assert out.count(_SKIP_LINE) == 1, f"expected exactly one skip line for alpha\n{out}"
    assert "project alpha: beginning chain" not in out, out
    assert "project beta: beginning chain" in out, out
    assert _state(root, "alpha") == "BACKLOG" and _state(root, "beta") == "DONE", out
    assert not list((root / "locks").glob("*alpha*")), "no lock may be taken for a disabled project"


def test_unrecognised_enabled_value_warns_once_and_stays_enabled(tmp_path: pathlib.Path) -> None:
    root = _build_root(tmp_path, "enabled=no")

    result, dispatched = _wake(tmp_path, root, "writer")

    out = result.combined
    assert result.returncode == 0, out
    assert sorted(dispatched) == ["alpha", "beta"], f"dispatch ledger {dispatched}\n{out}"
    assert out.count("WARNING: enabled for 'alpha' is 'no'") == 1, out
    assert "disabled in projects.cfg" not in out, out


def test_daily_overwatch_wake_skips_the_disabled_project(tmp_path: pathlib.Path) -> None:
    root = _build_root(tmp_path, "enabled=false")

    result, _dispatched = _wake(tmp_path, root, "overwatch")

    out = result.combined
    assert result.returncode == 0, out
    assert out.count(_SKIP_LINE) == 1, f"expected exactly one skip line for alpha\n{out}"
    assert "project alpha: beginning chain" not in out, out
    assert "project beta: beginning chain" in out, out
    assert not (root / "projects" / "alpha" / "overwatch").exists(), (
        "the OVERWATCH wake must create no state for a disabled project"
    )
