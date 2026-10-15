#!/usr/bin/env python3
"""
task_counts.py — per-state and per-role task counts for one project's tasks/.

The one scan behind two readers of the same numbers:

  - ``team/scripts/dashboard/data.sh`` runs this module's CLI and passes its
    ``KEY=value`` lines straight through as part of the dashboard data
    contract (``TOTAL_TICKETS``, ``QUEUE_<ROLE>_<STAT>``,
    ``WORKING_TASK_*``, ``LAST_DONE_*``).
  - ``pgai_agent_kanban.ops.read.get_queues`` calls :func:`scan_task_counts`
    for the queue pane (``show-queues.sh`` and ``GET /show-queues``).

WHAT IS COUNTED
---------------
Every directory under *tasks_root* that holds a ``status.md`` file, except
``archive``, ``queues``, ``plans`` and any name starting with ``TASK-``.

  - State: the first non-blank line of the ``## State`` section
    (``task_status.read_section``), uppercased; an absent or empty State
    counts as BACKLOG. ``DONE`` and ``WONT-DO`` count as done. Any other
    value counts toward the total only.
  - Role: the first non-blank line of README.md's ``## Role`` section,
    uppercased, up to the first ``|``. A missing README or a role outside
    PM, CODER, WRITER, TESTER, CM counts as CODER.
  - Working task: the WORKING task whose status.md was modified most
    recently (the first in directory-name order on a tie).
  - Last done: the five done tasks whose status.md was modified most
    recently, newest first.

The first-line reader keeps a value exactly as written apart from case: a
bold ``**DONE**`` is not unwrapped, so it counts toward the total only, as it
always has on the dashboard.

Usage (CLI)::

    python3 -m pgai_agent_kanban.dashboard.task_counts <tasks_root>

Prints one ``KEY=value`` line per counter (see :func:`format_task_counts`)
and exits 0. A *tasks_root* that does not exist prints all-zero counters.
"""

from __future__ import annotations

import dataclasses
import pathlib
import sys

# Run by file path (``python3 .../task_counts.py``) or loaded from a file
# outside its package, this module has no parent package and the interpreter
# puts only its own directory on sys.path, so the package import below would
# not resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# import resolves from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.task_status import read_section  # noqa: E402

__all__ = [
    "QUEUE_ROLES",
    "QUEUE_STATS",
    "TaskCounts",
    "WorkingTask",
    "format_task_counts",
    "scan_task_counts",
]

#: The roles the queue counters are kept for, in display order.
QUEUE_ROLES: tuple[str, ...] = ("PM", "CODER", "WRITER", "TESTER", "CM")

#: The counters kept per role, in output order.
QUEUE_STATS: tuple[str, ...] = ("TOTAL", "DONE", "WORKING", "BLOCKED", "WAITING")

# The role a task without a recognised README role is counted under.
_DEFAULT_ROLE = "CODER"
_SKIP_DIRS = frozenset({"archive", "queues", "plans"})
_DONE_STATES = frozenset({"DONE", "WONT-DO"})
_LAST_DONE_SLOTS = 5


@dataclasses.dataclass(frozen=True)
class WorkingTask:
    """The most recently touched WORKING task.

    Attributes:
        task_id: The task directory name.
        mtime:   Its status.md modification time, whole epoch seconds.
        role:    The role it is counted under (one of ``QUEUE_ROLES``).
    """

    task_id: str
    mtime: int
    role: str


@dataclasses.dataclass(frozen=True)
class TaskCounts:
    """What one scan of a tasks/ directory found.

    Attributes:
        totals:    ``TOTAL``, ``DONE``, ``WORKING``, ``BLOCKED``, ``WAITING``
                   and ``BACKLOG`` counts across every counted task.
        queues:    Per-role counters: ``queues[role][stat]`` for every role
                   in ``QUEUE_ROLES`` and stat in ``QUEUE_STATS``.
        working:   The most recently touched WORKING task, or ``None``.
        last_done: Up to five ``(task_id, role)`` pairs, newest first.
    """

    totals: dict[str, int]
    queues: dict[str, dict[str, int]]
    working: "WorkingTask | None"
    last_done: tuple[tuple[str, str], ...]


def _first_line_upper(text: str, heading: str) -> str:
    """The first non-blank line of the ``## <heading>`` section, uppercased."""
    body = read_section(text, heading)
    if not body:
        return ""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped.upper()
    return ""


def _mtime(path: pathlib.Path) -> int:
    try:
        return int(path.stat().st_mtime)
    except OSError:
        return 0


def _task_role(task_dir: pathlib.Path) -> str:
    readme = task_dir / "README.md"
    role = ""
    if readme.is_file():
        try:
            role = _first_line_upper(readme.read_text(errors="replace"), "Role")
        except OSError:
            role = ""
        role = role.split("|")[0].strip()
    return role if role in QUEUE_ROLES else _DEFAULT_ROLE


def scan_task_counts(tasks_root: "pathlib.Path | str") -> TaskCounts:
    """Count the tasks under *tasks_root* by state and by role.

    Args:
        tasks_root: A project's ``tasks/`` directory. A path that is not a
                    directory yields all-zero counts.

    Returns:
        The counts; see :class:`TaskCounts` and the module docstring for
        what is counted and how.
    """
    root = pathlib.Path(tasks_root)
    totals = {key: 0 for key in ("TOTAL", "DONE", "WORKING", "BLOCKED", "WAITING", "BACKLOG")}
    queues = {role: {stat: 0 for stat in QUEUE_STATS} for role in QUEUE_ROLES}
    working: "WorkingTask | None" = None
    done_entries: list[tuple[int, str, str]] = []

    if root.is_dir():
        for task_dir in sorted(root.iterdir()):
            if not task_dir.is_dir():
                continue
            task_id = task_dir.name
            if task_id in _SKIP_DIRS or task_id.startswith("TASK-"):
                continue
            status_file = task_dir / "status.md"
            if not status_file.is_file():
                continue

            totals["TOTAL"] += 1
            try:
                status_text = status_file.read_text(errors="replace")
            except OSError:
                continue

            state = _first_line_upper(status_text, "State") or "BACKLOG"
            role = _task_role(task_dir)
            queues[role]["TOTAL"] += 1

            if state in _DONE_STATES:
                totals["DONE"] += 1
                queues[role]["DONE"] += 1
                done_entries.append((_mtime(status_file), task_id, role))
            elif state == "WORKING":
                totals["WORKING"] += 1
                queues[role]["WORKING"] += 1
                mtime = _mtime(status_file)
                if working is None or mtime > working.mtime:
                    working = WorkingTask(task_id=task_id, mtime=mtime, role=role)
            elif state == "BLOCKED":
                totals["BLOCKED"] += 1
                queues[role]["BLOCKED"] += 1
            elif state == "WAITING":
                totals["WAITING"] += 1
                queues[role]["WAITING"] += 1
            else:
                totals["BACKLOG"] += 1

    done_entries.sort(reverse=True)
    last_done = tuple((task_id, role) for _mtime_s, task_id, role in done_entries[:_LAST_DONE_SLOTS])
    return TaskCounts(totals=totals, queues=queues, working=working, last_done=last_done)


def format_task_counts(counts: TaskCounts) -> str:
    """Render *counts* as the ``KEY=value`` lines data.sh publishes.

    Lines, in order: ``TOTAL_TICKETS`` and ``<STATE>_COUNT`` for DONE,
    WORKING, BLOCKED, WAITING, BACKLOG; ``QUEUE_<ROLE>_<STAT>`` for every
    role and stat; ``WORKING_TASK_ID`` / ``_MTIME`` / ``_ROLE`` (empty, 0
    and empty with no working task); ``LAST_DONE_<n>`` and
    ``LAST_DONE_ROLE_<n>`` for n = 1..5, empty past the last done task.
    """
    lines = [f"TOTAL_TICKETS={counts.totals['TOTAL']}"]
    lines += [f"{key}_COUNT={counts.totals[key]}" for key in ("DONE", "WORKING", "BLOCKED", "WAITING", "BACKLOG")]
    for role in QUEUE_ROLES:
        lines += [f"QUEUE_{role}_{stat}={counts.queues[role][stat]}" for stat in QUEUE_STATS]
    working = counts.working
    lines.append(f"WORKING_TASK_ID={working.task_id if working else ''}")
    lines.append(f"WORKING_TASK_MTIME={working.mtime if working else 0}")
    lines.append(f"WORKING_TASK_ROLE={working.role if working else ''}")
    for index in range(1, _LAST_DONE_SLOTS + 1):
        task_id, role = counts.last_done[index - 1] if index <= len(counts.last_done) else ("", "")
        lines.append(f"LAST_DONE_{index}={task_id}")
        lines.append(f"LAST_DONE_ROLE_{index}={role}")
    return "\n".join(lines) + "\n"


def main(argv: "list[str] | None" = None) -> int:
    """CLI entry point: print the counters for ``argv[0]`` (a tasks/ directory)."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python3 -m pgai_agent_kanban.dashboard.task_counts <tasks_root>", file=sys.stderr)
        return 1
    sys.stdout.write(format_task_counts(scan_task_counts(args[0])))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI wiring
    sys.exit(main())
