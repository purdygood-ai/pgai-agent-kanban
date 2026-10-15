"""
test_overwatch_block_triage_wake_driven.py
==========================================
The overwatch wake runs block triage, driven through the REAL production
entry point (``bash scripts/wake/claude.sh --agent=overwatch``) against a
self-contained kanban root built by the shared wake-driven fixture builders.

Coverage:
  - A BLOCKED coder task carrying an always-human Block Record is escalated
    by the overwatch wake: Needs Human yes, a one-paragraph OVERWATCH
    Diagnosis, the block-triage action-log row and the wake's own
    overwatch-block-triage.log — proving the check runs before the chain's
    repo lock (under that lock every task would be refused as contended).
  - A coder wake leaves the same task untouched: the hook is overwatch-only.

No real provider is invoked: every agent backlog the builder seeds is empty,
so the wake dispatches nothing. All paths live under pytest's tmp_path.
"""

from __future__ import annotations

import pathlib
import re

from tests.integration.test_overwatch_wake_driven_fixtures import (
    _action_log,
    _build_kanban_root,
    _init_git_repo,
    _run_wake,
    _write_release_state_md,
)

_TASK = "CODER-20260101-001-example-blocked-task"


def _seed_blocked_task(proj: pathlib.Path) -> pathlib.Path:
    task_dir = proj / "tasks" / _TASK
    (task_dir / "artifacts").mkdir(parents=True)
    (task_dir / "README.md").write_text("# Task: example\n\n## Role\nCODER\n", encoding="utf-8")
    status = task_dir / "status.md"
    status.write_text(
        "# Status\n\n## State\nBLOCKED\n\n## Summary\nBlocked.\n\n## Needs Human\nno\n\n"
        "## Block Record\nreason_code: workflow_type_unparseable\nowner_role: CODER\nattempts: 0\n"
        "first_blocked_at: 2026-01-01T10:00:00+00:00\nlast_blocked_at: 2026-01-01T10:00:00+00:00\n"
        "cooldown_until: none\nevidence: none\nresume: fresh\n",
        encoding="utf-8",
    )
    queue = proj / "tasks" / "queues" / "coder_backlog.md"
    queue.write_text(queue.read_text(encoding="utf-8") + f"\n- [B] {_TASK} — example\n", encoding="utf-8")
    return status


def _section(text: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}[ \t]*\n(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
    return m.group(1).strip() if m else ""


def _build(tmp_path: pathlib.Path, project_name: str) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    repo_path = tmp_path / f"dev_repo_{project_name}"
    _init_git_repo(repo_path, "ai_main", "ai_rc/v2.0.1")
    kanban_root = _build_kanban_root(tmp_path, project_name, repo_path)
    proj = kanban_root / "projects" / project_name
    _write_release_state_md(proj, "none")
    return kanban_root, proj, _seed_blocked_task(proj)


def test_overwatch_wake_escalates_an_always_human_block(tmp_path: pathlib.Path) -> None:
    kanban_root, proj, status = _build(tmp_path, "wd_block_triage")
    result = _run_wake(kanban_root, "overwatch")
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    text = status.read_text(encoding="utf-8")
    triage_log = kanban_root / "logs" / "overwatch-block-triage.log"
    detail = f"\nstatus.md:\n{text}\ntriage log:\n{triage_log.read_text(encoding='utf-8') if triage_log.is_file() else '(none)'}"
    assert _section(text, "State") == "BLOCKED", detail
    assert _section(text, "Needs Human") == "yes", detail
    assert _section(text, "OVERWATCH Diagnosis").startswith("Block epoch 2026-01-01T10:00:00+00:00"), detail
    assert "block-triage:workflow_type_unparseable:escalate" in _action_log(proj), detail
    assert "repo lock held" not in triage_log.read_text(encoding="utf-8")


def test_coder_wake_leaves_the_blocked_task_alone(tmp_path: pathlib.Path) -> None:
    kanban_root, proj, status = _build(tmp_path, "wd_block_triage_coder")
    before = status.read_bytes()
    result = _run_wake(kanban_root, "coder")
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert status.read_bytes() == before
    assert "block-triage:" not in _action_log(proj)
    assert not (kanban_root / "logs" / "overwatch-block-triage.log").exists()
