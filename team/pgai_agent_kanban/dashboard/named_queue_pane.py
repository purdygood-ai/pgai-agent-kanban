"""
named_queue_pane.py — the renderer behind ``team/scripts/dashboard/queue-pane.sh``.

Renders one agent queue file (``tasks/queues/<name>_backlog.md``) of one
project: a ``=== <queue> ===`` header with done / working / waiting /
blocked / pending counts, one ``[STATE] <task-id>`` line per entry, a
``⚠ task folder missing`` warning for an entry with no task folder, the
unmet prerequisites under each WAITING entry, and an ``updated HH:MM:SS``
footer.

QUEUE LINES
-----------
An entry is ``- [M] <task-id>[ — description]``. The task ID must have the
canonical shape ``kanban_task_id_pattern`` (``team/scripts/lib/task_ids.sh``)
prints -- current role-prefixed IDs (``CODER-YYYYMMDD-NNN-slug``) and the
legacy ``CLAUDE-`` prefixed form alike -- followed by whitespace or the end
of the line. The caller passes that pattern in, so this module never carries
its own copy of the shape. Any other line is ignored.

The bracket content is read by the shared queue-marker reading
(``pgai_agent_kanban.ops.bracket_marker``), so the pane accepts what the
wake's queue scans accept: ``[ ]``, the empty ``[]`` alias, and surrounding
whitespace inside the brackets.

Markers: ``x`` done, ``A`` working, ``W`` waiting, ``B`` blocked, anything
else -- ``[ ]`` and ``[]`` included -- pending. The badge shown is the task's ``## State`` from its
``status.md`` where the marker defers to it (pending and working entries,
and a done entry whose status.md disagrees); a WAITING entry lists every
``## Prerequisites`` task in its README.md that is not DONE or WONT-DO.

USAGE
-----
::

    python3 -m pgai_agent_kanban.dashboard.named_queue_pane \\
        QUEUE_FILE TASKS_DIR QUEUE_NAME --task-id-pattern PATTERN \\
        [--colors LABEL BOLD DONE WORKING BLOCKED WAITING DIM WARN RESET]

``--colors`` takes the nine escape codes the pane uses, in that order
(empty strings for no colour; the default is no colour). Prints the pane on
stdout and exits 0; an unreadable queue file renders as an empty queue.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import pathlib
import re
import sys
from typing import Optional

if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.ops import bracket_marker  # noqa: E402
from pgai_agent_kanban.task_status import read_section, section_value  # noqa: E402

__all__ = [
    "PaneColors",
    "QueueEntry",
    "main",
    "parse_queue_entries",
    "read_prereqs",
    "render_named_queue",
]

DONE_MARKERS = {"x"}
WORKING_MARKERS = {"A"}
WAITING_MARKERS = {"W"}
BLOCKED_MARKERS = {"B"}


@dataclasses.dataclass(frozen=True)
class PaneColors:
    """The escape codes one render uses ("" for each when colour is off)."""

    label: str = ""
    bold: str = ""
    done: str = ""
    working: str = ""
    blocked: str = ""
    waiting: str = ""
    dim: str = ""
    warn: str = ""
    reset: str = ""


@dataclasses.dataclass(frozen=True)
class QueueEntry:
    """One queue line: its marker (as :func:`bracket_marker` reads it), task ID and description."""

    marker: str
    task_id: str
    description: str


def _unanchored(task_id_pattern: str) -> str:
    """*task_id_pattern* without its leading ``^`` and trailing ``$``."""
    pattern = task_id_pattern.strip()
    if pattern.startswith("^"):
        pattern = pattern[1:]
    if pattern.endswith("$"):
        pattern = pattern[:-1]
    return pattern


def _entry_regex(task_id_pattern: str) -> "re.Pattern[str]":
    task_id = _unanchored(task_id_pattern)
    return re.compile(rf"^\s*-\s+\[(?P<bracket>[^\]\n]*)\]\s+(?P<task_id>{task_id})(?=\s|$)(?P<rest>.*)$")


def _prereq_regex(task_id_pattern: str) -> "re.Pattern[str]":
    task_id = _unanchored(task_id_pattern)
    return re.compile(rf"^-?\s*(?P<task_id>{task_id})\s*$")


def parse_queue_entries(text: str, task_id_pattern: str) -> list[QueueEntry]:
    """Return the entries of a queue file's *text*, in file order.

    Args:
        text:            The queue file's contents.
        task_id_pattern: The canonical task-ID shape (``kanban_task_id_pattern``),
                         anchored or not.

    Returns:
        One :class:`QueueEntry` per ``- [M] <task-id>`` line, ``- [] <task-id>``
        included; the description is whatever follows the ID with one leading
        dash or em/en dash removed.
    """
    entry_re = _entry_regex(task_id_pattern)
    entries = []
    for line in text.splitlines():
        match = entry_re.match(line)
        if not match:
            continue
        rest = (match.group("rest") or "").strip()
        description = re.sub(r"^[—–-]\s*", "", rest).strip()
        marker = bracket_marker(match.group("bracket"))
        entries.append(QueueEntry(marker, match.group("task_id"), description))
    return entries


def read_prereqs(text: str, task_id_pattern: str) -> list[str]:
    """Return the task IDs listed in a README's ``## Prerequisites`` section.

    Each line of the section that is a task ID, bare or as a ``- `` list
    item, contributes that ID; any other line is skipped.
    """
    prereq_re = _prereq_regex(task_id_pattern)
    prereqs = []
    for line in (read_section(text, "Prerequisites") or "").splitlines():
        match = prereq_re.match(line.strip())
        if match:
            prereqs.append(match.group("task_id"))
    return prereqs


def _task_state(task_dir: pathlib.Path) -> str:
    """``## State`` from *task_dir*/status.md, uppercased; BACKLOG when blank, "" when unreadable."""
    status_file = task_dir / "status.md"
    if not status_file.is_file():
        return ""
    try:
        state = section_value(status_file.read_text(errors="replace"), "State").upper()
    except OSError:
        return ""
    return state if state else "BACKLOG"


def _prereqs_status(tasks_root: pathlib.Path, task_id: str, task_id_pattern: str) -> list[tuple[str, str]]:
    """(prerequisite ID, its state) for each prerequisite of *task_id*; MISSING for no folder."""
    readme_file = tasks_root / task_id / "README.md"
    if not readme_file.is_file():
        return []
    try:
        prereqs = read_prereqs(readme_file.read_text(errors="replace"), task_id_pattern)
    except OSError:
        return []
    result = []
    for pid in prereqs:
        pdir = tasks_root / pid
        result.append((pid, _task_state(pdir) if pdir.is_dir() else "MISSING"))
    return result


def _badge(state: str, c: PaneColors) -> str:
    if state in ("DONE", "WONT-DO"):
        return f"{c.done}[{state}]{c.reset}"
    if state == "WORKING":
        return f"{c.working}[{state}]{c.reset}"
    if state == "BLOCKED":
        return f"{c.blocked}[{state}]{c.reset}"
    if state == "WAITING":
        return f"{c.waiting}[{state}]{c.reset}"
    if state in ("BACKLOG", ""):
        return f"{c.dim}[BACKLOG]{c.reset}"
    return f"{c.warn}[{state}]{c.reset}"


def _display_state(entry: QueueEntry, task_dir: pathlib.Path) -> str:
    """The state an entry's badge shows: its marker, or its status.md where the marker defers."""
    marker = entry.marker
    if marker in DONE_MARKERS:
        if not task_dir.is_dir():
            return "DONE"
        actual = _task_state(task_dir)
        return actual if actual else "DONE"
    if marker in WORKING_MARKERS:
        return _task_state(task_dir) if task_dir.is_dir() else "WORKING"
    if marker in WAITING_MARKERS:
        return "WAITING"
    if marker in BLOCKED_MARKERS:
        return "BLOCKED"
    return _task_state(task_dir) if task_dir.is_dir() else "BACKLOG"


def render_named_queue(
    queue_file: pathlib.Path,
    tasks_root: pathlib.Path,
    queue_name: str,
    task_id_pattern: str,
    colors: PaneColors = PaneColors(),
    now: Optional[datetime.datetime] = None,
) -> str:
    """Render the pane for one queue file.

    Args:
        queue_file:      The queue file; unreadable reads as empty.
        tasks_root:      The project's ``tasks/`` directory, where each
                         entry's task folder is looked up.
        queue_name:      The name shown in the header (``coder``, ``pm``, ...).
        task_id_pattern: The canonical task-ID shape (``kanban_task_id_pattern``).
        colors:          Escape codes; the default renders without colour.
        now:             Time for the footer; the current local time when None.

    Returns:
        The pane text, newline-terminated.
    """
    c = colors
    try:
        content = pathlib.Path(queue_file).read_text(errors="replace")
    except OSError:
        content = ""
    entries = parse_queue_entries(content, task_id_pattern)
    tasks_root = pathlib.Path(tasks_root)

    total = len(entries)
    done = sum(1 for e in entries if e.marker in DONE_MARKERS)
    working = sum(1 for e in entries if e.marker in WORKING_MARKERS)
    waiting = sum(1 for e in entries if e.marker in WAITING_MARKERS)
    blocked = sum(1 for e in entries if e.marker in BLOCKED_MARKERS)
    pending = total - done - working - waiting - blocked

    count_parts = []
    for count, label, color in ((done, "done", c.done), (working, "working", c.working),
                                (waiting, "waiting", c.waiting), (blocked, "blocked", c.blocked),
                                (pending, "pending", c.dim)):
        if count:
            count_parts.append(f"{color}{count} {label}{c.reset}")
    count_str = ", ".join(count_parts) if count_parts else f"{c.dim}empty{c.reset}"

    lines = [f"{c.label}{c.bold}=== {queue_name} ==={c.reset}  {count_str}", f"{c.dim}{'─' * 60}{c.reset}"]

    if not entries:
        lines.append(f"{c.dim}(no entries){c.reset}")
    for entry in entries:
        task_dir = tasks_root / entry.task_id
        state = _display_state(entry, task_dir)
        warn = (f"  {c.warn}⚠ task folder missing{c.reset}"
                if not task_dir.is_dir() and entry.marker not in DONE_MARKERS else "")
        short_desc = f"  {c.dim}{entry.description[:50]}{c.reset}" if entry.description else ""
        lines.append(f"  {_badge(state, c)} {c.dim}{entry.task_id}{c.reset}{warn}{short_desc}")

        if state == "WAITING" and task_dir.is_dir():
            unmet = [(pid, ps) for pid, ps in _prereqs_status(tasks_root, entry.task_id, task_id_pattern)
                     if ps not in ("DONE", "WONT-DO")]
            if unmet:
                lines.append(f"    {c.waiting}waiting on:{c.reset}")
                lines.extend(f"      {_badge(ps, c)} {c.dim}{pid}{c.reset}" for pid, ps in unmet)

    stamp = (now or datetime.datetime.now()).strftime("%H:%M:%S")
    lines.append(f"\n{c.dim}updated {stamp}{c.reset}")
    return "\n".join(lines) + "\n"


def main(argv: "list[str] | None" = None) -> int:
    """CLI entry point; see the module docstring for the arguments."""
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.dashboard.named_queue_pane",
        description="Render one agent queue file with per-entry task states.",
    )
    parser.add_argument("queue_file", help="the queue file, e.g. <project>/tasks/queues/coder_backlog.md")
    parser.add_argument("tasks_dir", help="the project's tasks/ directory")
    parser.add_argument("queue_name", help="the name shown in the header, e.g. coder")
    parser.add_argument("--task-id-pattern", required=True,
                        help="the canonical task-ID regex (kanban_task_id_pattern in task_ids.sh)")
    parser.add_argument("--colors", nargs=9, default=None,
                        metavar=("LABEL", "BOLD", "DONE", "WORKING", "BLOCKED", "WAITING", "DIM", "WARN", "RESET"),
                        help="escape codes, in this order; omit for no colour")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    colors = PaneColors(*args.colors) if args.colors else PaneColors()
    sys.stdout.write(render_named_queue(
        pathlib.Path(args.queue_file), pathlib.Path(args.tasks_dir), args.queue_name,
        args.task_id_pattern, colors,
    ))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI wiring
    sys.exit(main())
