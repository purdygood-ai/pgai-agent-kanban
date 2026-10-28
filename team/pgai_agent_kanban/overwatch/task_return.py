"""
task_return.py — the one write that returns a stopped task to BACKLOG.

The decision executor's ``reset-to-backlog`` and its block-triage requeues
(:mod:`pgai_agent_kanban.overwatch.decision_executor`) and the release's
verification requeue (:mod:`pgai_agent_kanban.cm.verification_retry`) all
return a task to BACKLOG the same way ``promote_blocked_to_backlog`` does in
the wake: ``## State`` BACKLOG, ``## Needs Human`` no, ``## Blockers`` none
and a ``## Summary`` naming why, through the status-writer
(:func:`pgai_agent_kanban.task_status.write_task_status`);
``## Blocked By Agent`` and ``## Blocked Reason`` reset to ``none`` where
present; then the task's queue line marker rewritten to ``[ ]``
(:func:`pgai_agent_kanban.ops.write.set_marker`). This module is that write,
stated once.

It takes no lock and runs no HALT check: each caller already holds the
envelope its own lane requires (the executor's HALT-first protocol and repo
lock; the release's own wake chain).

Usage (import)::

    from pgai_agent_kanban.overwatch.task_return import find_queue_file, return_to_backlog
    queue = find_queue_file(project_root, task_id)
    return_to_backlog(status_file, queue, project="example", task_id=task_id,
                      summary="Returned to BACKLOG by OVERWATCH: the blocker was cleared.")
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from pgai_agent_kanban import task_status as ts
from pgai_agent_kanban.ops.write import set_marker

__all__ = ["find_queue_file", "return_to_backlog"]

#: Sections reset to ``none`` on the return, when the status file has them.
_STALE_BLOCK_SECTIONS = ("Blocked By Agent", "Blocked Reason")


def find_queue_file(project_root: "Path | str", task_id: str) -> Optional[Path]:
    """The project queue file carrying a line for *task_id*, or None."""
    line_re = re.compile(r"^\s*-?\s*\[[^\]\n]*\]\s+" + re.escape(task_id) + r"(\s|$)", re.MULTILINE)
    queues = Path(project_root) / "tasks" / "queues"
    for candidate in sorted(queues.glob("*_backlog.md")) + sorted(queues.glob("*/*_backlog.md")):
        try:
            if line_re.search(candidate.read_text(encoding="utf-8")):
                return candidate
        except (OSError, UnicodeDecodeError):
            continue
    return None


def return_to_backlog(
    status_file: Path,
    queue_file: Optional[Path],
    *,
    project: str,
    task_id: str,
    summary: str,
) -> None:
    """Return *task_id* to BACKLOG with Needs Human no (see the module docstring).

    Raises:
        pgai_agent_kanban.task_status.TaskStatusError: the status-writer
            refused (a missing status.md); nothing was written.
    """
    text = Path(status_file).read_text(encoding="utf-8")
    ts.write_task_status(
        Path(status_file), state="BACKLOG", needs_human="no", blockers="none",
        summary=summary, project=project, key=task_id,
    )
    stale = {heading: "none" for heading in _STALE_BLOCK_SECTIONS if ts.read_section(text, heading)}
    if stale:
        ts.set_sections(Path(status_file), stale)
    if queue_file is not None:
        set_marker(queue_file, task_id, " ")
