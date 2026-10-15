"""
reconcile.py — move live queue lines whose item is no longer in the live project.

An archive run moves each item's queue lines with it, so after a run no live
queue line names an item the run archived. Lines can still be left naming an
item that is not live: a line whose item was archived before its line could
be matched, or a finished task whose folder was removed from ``tasks/`` some
other way. This pass finds those lines and moves them to the archive's queue
file of the same name, ``archive/<project>/tasks/queues/<agent>_backlog.md``,
through the same shared queue-marker writer an archive run uses
(``ops.write.queue_entries`` / ``append_queue_lines`` / ``remove_marker``),
so each line keeps its marker and description byte for byte.

Which lines move
----------------
Every Markdown list line ``- [<m>] <ID> ...`` in the live
``tasks/queues/*_backlog.md`` files is read as the archive plan reads it
(``plan._QUEUE_ENTRY_RE``, ``lookup.classify_item_id``). A line naming a
task, bug or priority:

- whose item is live (``tasks/<ID>/``, ``bugs/BUG-NNNN-*.md``,
  ``priority/PRIORITY-NNNN-*.md``) is never touched;
- whose item is absent from the live project and present in the archive
  moves (:data:`REASON_ARCHIVED`), whatever its marker;
- whose task folder is absent from both the live project and the archive
  moves only when every live line naming that task carries the ``[x]``
  (DONE or WONT-DO) marker (:data:`REASON_NO_TASK_FOLDER`). Such a line
  records finished work nothing can open any more; prerequisite resolution
  reads task folders and the archive, never queue lines, so moving it
  changes no task's readiness.

Every other line naming an item that is not live stays where it is and is
listed with the reason (:data:`LEFT_NOT_FINISHED` — a task with no folder
anywhere whose line is not ``[x]``, which may be a task still being
created; :data:`LEFT_ITEM_NOT_FOUND` — a bug or priority found in neither
tree). Requirements files are never named by queue lines and are not read.

Dry run first, then apply
-------------------------
:func:`plan_queue_reconcile` reads and moves nothing; :func:`render_queue_reconcile_plan`
lists every line it would move and every line it would leave, with the
reason. :func:`apply_queue_reconcile` takes the project's repo lock as an
archive run does (``holder=archive``, ``op=archive-reconcile-queues``;
a lock held by another process moves nothing and returns a skipped
result), plans again under it, and moves the planned lines. Lines are
appended to the archive file before they are removed from the live one, so
an interruption leaves a line in both rather than in neither, and a re-run
finishes it (``append_queue_lines`` never appends a byte-identical line
twice). Once applied, nothing qualifies, so a second apply moves nothing.

No ledger row is written: the ledger records items, and an archive run
writes none for the queue lines that follow an item either.

Operator surface: ``scripts/archive.sh --project NAME --reconcile-queues
[--dry-run]`` (``run.run_archive(..., reconcile_queues=True)``).

Public API
----------
``plan_queue_reconcile(project, *, kanban_root=None) -> QueueReconcilePlan``
``apply_queue_reconcile(project, *, kanban_root=None, task="none", log=None, accept_chain_lock=False) -> RunResult``
``render_queue_reconcile_plan(planned) -> list[str]``
``QueueReconcilePlan``, ``ReconcileLine``, ``OP_RECONCILE_QUEUES`` and the
``REASON_*`` / ``LEFT_*`` constants.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import shlex
from typing import Callable, Optional, Union

from pgai_agent_kanban.archive.apply import (
    _RUN_ERRORS,
    NO_TASK,
    RESULT_OK,
    RunResult,
    _fail,
    _move_queue_lines,
    _Run,
    _take_repo_lock,
)
from pgai_agent_kanban.archive.layout import (
    CATEGORY_REQUIREMENTS,
    CATEGORY_TASKS,
    ProjectLayout,
    archive_layout,
    display_relpath,
    live_layout,
)
from pgai_agent_kanban.archive.lookup import classify_item_id, find_item
from pgai_agent_kanban.archive.plan import _QUEUE_ENTRY_RE, _QUEUE_GLOB, _TOKEN_TRIM, QueueLine
from pgai_agent_kanban.env import resolve_kanban_root

__all__ = [
    "LEFT_ITEM_NOT_FOUND",
    "LEFT_NOT_FINISHED",
    "OP_RECONCILE_QUEUES",
    "REASON_ARCHIVED",
    "REASON_NO_TASK_FOLDER",
    "QueueReconcilePlan",
    "ReconcileLine",
    "apply_queue_reconcile",
    "plan_queue_reconcile",
    "render_queue_reconcile_plan",
]

PathLike = Union[str, "os.PathLike[str]"]

#: Repo-lock ``op`` and ``RunResult.op`` of an applied reconciliation.
OP_RECONCILE_QUEUES = "archive-reconcile-queues"

#: ``ReconcileLine.reason`` of a line that moves: its item is archived and not live.
REASON_ARCHIVED = "archived"
#: ``ReconcileLine.reason`` of a line that moves: a finished task with no folder anywhere.
REASON_NO_TASK_FOLDER = "no-task-folder"
#: ``ReconcileLine.reason`` of a line left in place: a task with no folder whose lines are not all ``[x]``.
LEFT_NOT_FINISHED = "not-marked-done"
#: ``ReconcileLine.reason`` of a line left in place: a bug or priority in neither tree.
LEFT_ITEM_NOT_FOUND = "item-not-found"

#: The queue marker DONE and WONT-DO render as (read case-insensitively).
_DONE_MARKER = "x"


@dataclasses.dataclass(frozen=True)
class ReconcileLine:
    """One live queue line naming an item that is not live.

    Attributes:
        queue_file:  The live ``tasks/queues/<agent>_backlog.md`` file.
        line_number: 1-based line number in *queue_file*.
        text:        The line exactly as read, without its line ending.
        category:    The named item's category (tasks, bugs or priority).
        item_id:     The named item's canonical ID.
        reason:      A ``REASON_*`` constant for a line that moves, a
                     ``LEFT_*`` constant for a line left in place.
    """

    queue_file: pathlib.Path
    line_number: int
    text: str
    category: str
    item_id: str
    reason: str

    def as_queue_line(self) -> QueueLine:
        """This line as the archive's queue-line mover takes it."""
        return QueueLine(self.queue_file, self.line_number, self.text, self.category, self.item_id)


@dataclasses.dataclass(frozen=True)
class QueueReconcilePlan:
    """What one reconciliation would move for one project.

    Attributes:
        project:     Project name.
        kanban_root: The kanban root that was read.
        lines:       Lines that would move, by file then line number.
        left:        Lines naming an item that is not live which stay where
                     they are, by file then line number.
        notes:       Plain-language notes on files that could not be read.
    """

    project: str
    kanban_root: pathlib.Path
    lines: tuple[ReconcileLine, ...] = ()
    left: tuple[ReconcileLine, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        """True when the reconciliation moves nothing."""
        return not self.lines

    def to_dict(self) -> dict:
        """A JSON-ready rendering, paths as strings."""

        def _row(line: ReconcileLine) -> dict:
            row = dataclasses.asdict(line)
            row["queue_file"] = str(line.queue_file)
            return row

        return {
            "project": self.project,
            "kanban_root": str(self.kanban_root),
            "lines": [_row(line) for line in self.lines],
            "left": [_row(line) for line in self.left],
            "notes": list(self.notes),
        }


@dataclasses.dataclass(frozen=True)
class _Entry:
    """A parsed live queue line naming a task, bug or priority."""

    queue_file: pathlib.Path
    line_number: int
    text: str
    marker: str
    category: str
    item_id: str


def _queue_entries(live: ProjectLayout, notes: list) -> list[_Entry]:
    """Every live queue line naming a task, bug or priority, by file then line."""
    entries = []
    try:
        names = sorted(os.listdir(live.queues))
    except OSError:
        return entries
    for name in names:
        queue_file = live.queues / name
        if not queue_file.match(_QUEUE_GLOB) or not queue_file.is_file():
            continue
        try:
            text = queue_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            notes.append(f"{queue_file} could not be read; no line in it is reconciled.")
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            entry = _QUEUE_ENTRY_RE.match(line)
            if entry is None:
                continue
            parsed = classify_item_id(entry.group("token").strip(_TOKEN_TRIM))
            if parsed is None or parsed.category == CATEGORY_REQUIREMENTS:
                continue
            entries.append(
                _Entry(queue_file, number, line, entry.group("marker").strip(), parsed.category, parsed.canonical)
            )
    return entries


def plan_queue_reconcile(project: str, *, kanban_root: Optional[PathLike] = None) -> QueueReconcilePlan:
    """Return the live queue lines a reconciliation would move for *project*; move nothing.

    Args:
        project:     Project name (a single path component).
        kanban_root: The kanban root holding ``projects/<project>/`` and
                     ``archive/<project>/``. ``None`` uses
                     ``env.resolve_kanban_root()``.

    Returns:
        A :class:`QueueReconcilePlan`; see the module docstring for which
        lines move and which are left.

    Raises:
        ArchiveLayoutError: *project* is not a valid project name.
        RuntimeError: *kanban_root* is ``None`` and the kanban root
            environment variable is unset.
    """
    root = pathlib.Path(resolve_kanban_root() if kanban_root is None else kanban_root)
    live = live_layout(root, project)
    archive = archive_layout(root, project)
    notes: list[str] = []
    entries = _queue_entries(live, notes)

    # Where each named item is, looked up once per ID.
    where: dict[str, str] = {}
    for entry in entries:
        if entry.item_id in where:
            continue
        if find_item(live, entry.item_id) is not None:
            where[entry.item_id] = "live"
        elif find_item(archive, entry.item_id) is not None:
            where[entry.item_id] = "archived"
        else:
            where[entry.item_id] = "absent"

    # A task with no folder anywhere qualifies only when every one of its
    # live lines is [x]; one unfinished-looking line keeps all of them.
    not_all_done = {
        entry.item_id for entry in entries if where[entry.item_id] == "absent" and entry.marker.lower() != _DONE_MARKER
    }

    lines: list[ReconcileLine] = []
    left: list[ReconcileLine] = []
    for entry in entries:
        place = where[entry.item_id]
        if place == "live":
            continue
        if place == "archived":
            reason, bucket = REASON_ARCHIVED, lines
        elif entry.category != CATEGORY_TASKS:
            reason, bucket = LEFT_ITEM_NOT_FOUND, left
        elif entry.item_id in not_all_done:
            reason, bucket = LEFT_NOT_FINISHED, left
        else:
            reason, bucket = REASON_NO_TASK_FOLDER, lines
        bucket.append(
            ReconcileLine(entry.queue_file, entry.line_number, entry.text, entry.category, entry.item_id, reason)
        )
    return QueueReconcilePlan(project, root, tuple(lines), tuple(left), tuple(notes))


_REASON_TEXT = {
    REASON_ARCHIVED: "archived and not live",
    REASON_NO_TASK_FOLDER: "finished ([x]); no task folder live or archived",
    LEFT_NOT_FINISHED: "no task folder live or archived, but a line for it is not [x]",
    LEFT_ITEM_NOT_FOUND: "in neither the live project nor the archive",
}


def render_queue_reconcile_plan(planned: QueueReconcilePlan) -> list[str]:
    """The dry-run report of *planned*, one line per entry.

    Paths are relative to the kanban root. Each moving line names the
    archive queue file it would be appended to.
    """
    root = pathlib.Path(planned.kanban_root)
    archive_queues = display_relpath(root, archive_layout(root, planned.project).queues)
    out = [
        f"archive: queue reconciliation dry run for project '{planned.project}'; nothing is moved.",
        f"queue lines: {len(planned.lines)} would move to {archive_queues}/",
    ]
    for line in planned.lines:
        out.append(
            f"  {display_relpath(root, line.queue_file)}:{line.line_number}  {line.text.strip()}  "
            f"({_REASON_TEXT[line.reason]}) -> {archive_queues}/{line.queue_file.name}"
        )
    out.append(f"left: {len(planned.left)} line(s) naming an item that is not live stay where they are")
    for line in planned.left:
        out.append(
            f"  {display_relpath(root, line.queue_file)}:{line.line_number}  {line.text.strip()}  ({_REASON_TEXT[line.reason]})"
        )
    for note in planned.notes:
        out.append(f"note: {note}")
    return out


def apply_queue_reconcile(
    project: str,
    *,
    kanban_root: Optional[PathLike] = None,
    task: str = NO_TASK,
    log: Optional[Callable[[str], None]] = None,
    accept_chain_lock: bool = False,
) -> RunResult:
    """Move the live queue lines :func:`plan_queue_reconcile` selects into the archive.

    Args:
        project:     Project name.
        kanban_root: The kanban root; ``None`` uses ``env.resolve_kanban_root()``.
        task:        The task this run is for (repo-lock provenance);
                     ``none`` when it is not for one task.
        log:         Receives each narration line (default: stderr).
        accept_chain_lock: Work under the repo lock when the wake chain this
                     process runs inside holds it, as ``apply.apply``.

    Returns:
        A :class:`~pgai_agent_kanban.archive.apply.RunResult` with op
        :data:`OP_RECONCILE_QUEUES`; ``queue_lines`` holds every line moved,
        as written, and ``rows`` is always empty.

    Raises:
        ArchiveLayoutError: *project* is not a valid project name.
    """
    root = pathlib.Path(resolve_kanban_root() if kanban_root is None else kanban_root)
    run = _Run(root, project, OP_RECONCILE_QUEUES, log)
    if plan_queue_reconcile(project, kanban_root=root).is_empty:
        run.say(f"archive: no live queue line to reconcile for project '{project}'.")
        return run.result(RESULT_OK)

    held = _take_repo_lock(run, task, accept_chain_lock)
    if isinstance(held, RunResult):
        return held
    with held:
        fresh = plan_queue_reconcile(project, kanban_root=root)
        for note in fresh.notes:
            run.say(f"archive: {note}")
        by_item: dict[str, list[QueueLine]] = {}
        for line in fresh.lines:
            by_item.setdefault(line.item_id, []).append(line.as_queue_line())
        for item_id, item_lines in by_item.items():
            try:
                _move_queue_lines(run, item_lines, run.live, run.archive)
            except _RUN_ERRORS as exc:
                return _fail(run, item_id, exc, f"ls -l {shlex.quote(str(run.live.queues))}")
        run.say(
            f"archive: project '{project}': moved {len(run.queue_lines)} queue line(s) naming items that "
            f"are not live to {run.archive.queues}."
        )
        return run.result(RESULT_OK)
