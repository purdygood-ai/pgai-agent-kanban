"""
apply.py — move a plan's items into the archive, and move one item back.

``apply(plan)`` moves the items an :class:`~pgai_agent_kanban.archive.plan.ArchivePlan`
selected from ``<kanban_root>/projects/<project>/`` to the same relative
paths under ``<kanban_root>/archive/<project>/``, with the queue lines and
training logs that follow them. ``restore(project, item_id)`` moves one
archived item, its queue lines and its training logs back. Both use the
same three primitives, which is why they live together:

- ``move.move_path`` for every file and directory (rename on one
  filesystem; copy, fsync, rename, then remove the source across two);
- ``ops.write.queue_entries`` / ``append_queue_lines`` / ``remove_marker``
  for queue lines — the shared queue-marker writer, so every queue-file
  rewrite is atomic and a line keeps its marker as it moves;
- ``ledger.append_row`` for the one ledger row per item.

The repo lock
-------------
Both take the project's repo lock (``ops.locks.repo_lock_path``, the lock
every wake chain holds while it works the project) before touching
anything, recording ``holder=archive``, the process pid, ``op``
(``archive-apply`` or ``archive-restore``) and ``task`` (the caller's task
ID, ``none`` when the run is not for one task). When another process holds
it, nothing moves: the holder's four fields are logged and a
:data:`RESULT_SKIPPED` result is returned. The lock is released when the
run ends, whatever its result.

A caller that runs inside a wake chain -- the release finalize's archive
step, which CM runs from its own wake -- passes ``accept_chain_lock=True``.
The chain took the repo lock before it started the caller and holds it
until the caller exits, so the lock is busy for the caller's whole run.
With ``accept_chain_lock`` a busy lock whose recorded holder pid is this
process or one of its ancestors (``ops.locks.pid_in_own_ancestry``) is
that chain's own hold: the run narrates the holder's four fields and
works under it, without taking or releasing anything, and the chain's
record stays as it was. A lock held by any other process skips exactly
as above, with or without the flag.

Under the lock, ``apply`` plans again and moves only the items both plans
select, so an item that changed since the plan was made (reopened, picked
up as a prerequisite, already archived) stays live. That is also why a
second ``apply`` — of a fresh plan or of the same plan object — moves
nothing and writes no ledger rows.

Order and failure
-----------------
Items move one at a time, in plan order. For each item the queue lines
move first (appended to ``archive/<project>/tasks/queues/<same file
name>``, then removed from the live file), then its training logs (to
``archive/<project>/logs/training/<role>/``), then the item itself,
followed at once by its ledger row. A failure anywhere stops the run at
that item: it is logged, dual-channel, and a :data:`RESULT_FAILED` result
names the item. Every item moved before it has its ledger row; the
failing item has none, and an item whose ledger row could not be written
is moved back. Because followers move before their item, re-running
after a failure finishes the item: it is still live and still selected,
and its followers are already in place.

``restore`` mirrors this in the other direction, with a ledger row of
category :data:`~pgai_agent_kanban.archive.ledger.CATEGORY_RESTORE`
(``restore``). It refuses, dual-channel (:class:`ArchiveRestoreError`),
an ID that is not in the archive and an item whose live path is already
taken; nothing is moved in either case.

Archiving is quality-class work: neither function raises for a busy lock
or a failed move. A result's ``status`` says what happened and its
``messages`` hold every line that was logged.

Public API
----------
``apply(plan, *, task="none", log=None, accept_chain_lock=False) -> RunResult``
``restore(project, item_id, *, kanban_root=None, task="none", log=None, accept_chain_lock=False) -> RunResult``
``RunResult``, ``ArchiveRestoreError``, ``RESULT_OK``, ``RESULT_SKIPPED``,
``RESULT_FAILED``.

*log* receives each narration line as it happens (default: printed to
stderr).
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import pathlib
import shlex
import sys
from typing import Callable, ContextManager, Iterable, Optional, Union

from pgai_agent_kanban.archive.layout import (
    CATEGORY_REQUIREMENTS,
    CATEGORY_TASKS,
    ProjectLayout,
    archive_layout,
    live_layout,
)
from pgai_agent_kanban.archive.ledger import CATEGORY_RESTORE, LedgerRow, append_row
from pgai_agent_kanban.archive.lookup import classify_item_id, find_item
from pgai_agent_kanban.archive.move import ArchiveMoveError, move_path
from pgai_agent_kanban.archive.plan import (
    _QUEUE_ENTRY_RE,
    ArchivePlan,
    QueueLine,
    TrainingLog,
    _queue_followers,
    _training_followers,
)
from pgai_agent_kanban.archive.plan import plan as _plan
from pgai_agent_kanban.env import resolve_kanban_root
from pgai_agent_kanban.lib.error_contract import format_error_contract
from pgai_agent_kanban.lib.released_version import last_released_version
from pgai_agent_kanban.lib.timeutil import utc_now_iso
from pgai_agent_kanban.ops.errors import OpsError
from pgai_agent_kanban.ops.locks import LockContended, acquire_lock, pid_in_own_ancestry, repo_lock_path
from pgai_agent_kanban.ops.write import append_queue_lines, queue_entries, remove_marker

__all__ = [
    "LOCK_HOLDER",
    "NO_TASK",
    "OP_APPLY",
    "OP_RESTORE",
    "RESULT_FAILED",
    "RESULT_OK",
    "RESULT_SKIPPED",
    "ArchiveRestoreError",
    "RunResult",
    "apply",
    "restore",
]

PathLike = Union[str, "os.PathLike[str]"]

#: ``RunResult.status`` values.
RESULT_OK = "ok"
RESULT_SKIPPED = "skipped"
RESULT_FAILED = "failed"

#: Repo-lock provenance this module records.
LOCK_HOLDER = "archive"
OP_APPLY = "archive-apply"
OP_RESTORE = "archive-restore"
#: The ``task`` provenance of a run not made for one task.
NO_TASK = "none"


@dataclasses.dataclass(frozen=True)
class RunResult:
    """What one :func:`apply` or :func:`restore` call did.

    Attributes:
        status:        :data:`RESULT_OK`, :data:`RESULT_SKIPPED` (the repo
                       lock was held; nothing moved) or :data:`RESULT_FAILED`
                       (stopped at :attr:`failed_item`).
        project:       Project name.
        op:            :data:`OP_APPLY` or :data:`OP_RESTORE`.
        rows:          The ledger rows written, one per item moved, in order.
        queue_lines:   The queue lines moved, as written.
        training_logs: The training logs moved, relative to the project root.
        dropped:       Planned IDs the plan made under the lock no longer
                       selected; they stayed where they were.
        failed_item:   The ID the run stopped at; ``""`` unless failed.
        error:         The dual-channel block for a skip or a failure;
                       ``""`` when ok.
        lock_holder:   ``(holder, pid, op, task)`` of the process holding the
                       repo lock when skipped; ``()`` otherwise.
        messages:      Every line logged, in order.
    """

    status: str
    project: str
    op: str
    rows: tuple[LedgerRow, ...] = ()
    queue_lines: tuple[str, ...] = ()
    training_logs: tuple[str, ...] = ()
    dropped: tuple[str, ...] = ()
    failed_item: str = ""
    error: str = ""
    lock_holder: tuple[str, ...] = ()
    messages: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """True when the run finished (whether or not it moved anything)."""
        return self.status == RESULT_OK

    @property
    def moved_ids(self) -> tuple[str, ...]:
        """The IDs moved, one per ledger row, in order."""
        return tuple(row.item_id for row in self.rows)

    def to_dict(self) -> dict:
        """A JSON-ready rendering for a CLI or API surface."""
        return {
            "status": self.status,
            "project": self.project,
            "op": self.op,
            "rows": [dataclasses.asdict(row) for row in self.rows],
            "queue_lines": list(self.queue_lines),
            "training_logs": list(self.training_logs),
            "dropped": list(self.dropped),
            "failed_item": self.failed_item,
            "error": self.error,
            "lock_holder": list(self.lock_holder),
            "messages": list(self.messages),
        }


class ArchiveRestoreError(Exception):
    """A restore was refused before anything moved.

    ``str(exc)`` is the three-line dual-channel block.

    Attributes:
        error:   ``archive.restore_not_archived`` or
                 ``archive.restore_live_exists``.
        message: The ID and why it cannot be restored.
        remedy:  A runnable command that shows what is there.
        item_id: The ID as the caller passed it.
    """

    def __init__(self, error: str, message: str, remedy: str, item_id: str) -> None:
        self.error = error
        self.message = message
        self.remedy = remedy
        self.item_id = item_id
        super().__init__(format_error_contract(error, message, remedy))


class _LedgerWriteFailed(Exception):
    """A ledger row could not be appended after a move (internal)."""

    def __init__(self, message: str, remedy: str) -> None:
        super().__init__(message)
        self.message = message
        self.remedy = remedy


def _stderr_line(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


class _Run:
    """The state one apply or restore run accumulates."""

    def __init__(
        self, kanban_root: pathlib.Path, project: str, op: str, log: Optional[Callable[[str], None]]
    ) -> None:
        self.root = kanban_root
        self.project = project
        self.op = op
        self.live = live_layout(kanban_root, project)
        self.archive = archive_layout(kanban_root, project)
        self.rows: list[LedgerRow] = []
        self.queue_lines: list[str] = []
        self.training_logs: list[str] = []
        self.messages: list[str] = []
        self._log = log or _stderr_line

    def say(self, line: str) -> None:
        self.messages.append(line)
        self._log(line)

    def rel(self, path: pathlib.Path) -> str:
        """*path* relative to the kanban root, ``/``-separated."""
        return pathlib.Path(path).relative_to(self.root).as_posix()

    def result(self, status: str, **fields) -> RunResult:
        return RunResult(
            status,
            self.project,
            self.op,
            tuple(self.rows),
            tuple(self.queue_lines),
            tuple(self.training_logs),
            messages=tuple(self.messages),
            **fields,
        )


# ---------------------------------------------------------------------------
# The repo lock
# ---------------------------------------------------------------------------


def _take_repo_lock(
    run: _Run, task: str, accept_chain_lock: bool = False
) -> Union[ContextManager[object], RunResult]:
    """The repo lock to work under, or the skipped/failed result to return instead.

    Returns a context manager to run inside: the ``ops.locks.HeldLock`` this
    process took (released on exit), or -- when *accept_chain_lock* is set
    and the busy lock's recorded holder pid is this process or one of its
    ancestors -- a no-op context, because the wake chain this run belongs
    to already holds the lock and releases it itself.
    """
    lock_file = repo_lock_path(run.root, run.project)
    try:
        return acquire_lock(lock_file, holder=LOCK_HOLDER, pid=os.getpid(), op=run.op, task=task)
    except LockContended as busy:
        if accept_chain_lock and pid_in_own_ancestry(busy.pid):
            run.say(
                f"archive: the repo lock {lock_file} is held by the wake chain this run belongs to "
                f"({busy.describe()}); {run.op} for project '{run.project}' runs under that hold."
            )
            return contextlib.nullcontext()
        # CONVERT (docs/blocking-site-census.md): a process outside this
        # run's own wake chain (another chain, an operator run, a second
        # archive) holds the project's repo lock. Nothing is moved, the
        # holder is named, and the run is skipped -- the next run after a
        # release retries. No release, wake or other project is held.
        # CENSUS-ANCHOR: archive-run-lock-busy-01
        block = format_error_contract(
            "archive.repo_lock_busy",
            f"archive: {run.op} for project '{run.project}' moved nothing: the repo lock "
            f"{lock_file} is held by holder={busy.holder or '<unknown>'} pid={busy.pid or '<unknown>'} "
            f"op={busy.op or '<unknown>'} task={busy.task or '<unknown>'}; the run is skipped "
            "and is retried by the next archive run.",
            shlex.quote(str(run.root / "scripts" / "release-locks.sh")),
        )
        run.say(block)
        return run.result(
            RESULT_SKIPPED, error=block, lock_holder=(busy.holder, busy.pid, busy.op, busy.task)
        )
    except OpsError as exc:
        block = _failure_block(run, "", exc, f"ls -ld {shlex.quote(str(lock_file.parent))}")
        run.say(block)
        return run.result(RESULT_FAILED, error=block)


# ---------------------------------------------------------------------------
# Moving followers and items
# ---------------------------------------------------------------------------


def _move_queue_lines(run: _Run, lines: Iterable[QueueLine], source: ProjectLayout, target: ProjectLayout) -> None:
    """Move every queue line naming the same entry as *lines* from *source* to *target*.

    Lines are appended to the target's queue file of the same name before
    they are removed from the source's, so an interruption leaves a line
    in both files rather than in neither.
    """
    done = set()
    for line in lines:
        entry = _QUEUE_ENTRY_RE.match(line.text)
        if entry is None:
            continue
        key = (line.queue_file.name, entry.group("token"))
        if key in done:
            continue
        done.add(key)
        name, token = key
        source_file = source.queues / name
        texts = queue_entries(source_file, token)
        if not texts:
            continue
        target_file = target.queues / name
        target_file.parent.mkdir(parents=True, exist_ok=True)
        append_queue_lines(target_file, texts)
        remove_marker(source_file, token)
        run.queue_lines.extend(texts)


def _move_training_logs(run: _Run, logs: Iterable[TrainingLog], source: ProjectLayout, target: ProjectLayout) -> None:
    """Move each training log to the same relative path under *target*.

    A log already at its target and gone from its source was moved by an
    earlier run and is left alone.
    """
    for log in logs:
        src, dst = source.root / log.relpath, target.root / log.relpath
        if not os.path.lexists(src) and os.path.lexists(dst):
            continue
        move_path(src, dst)
        run.training_logs.append(log.relpath)


def _move_item(run: _Run, item_id: str, src: pathlib.Path, dst: pathlib.Path, release: str, category: str) -> None:
    """Move one item and append its ledger row; move it back if the row cannot be written."""
    move_path(src, dst)
    row = LedgerRow(utc_now_iso(), release, category, item_id, run.rel(src), run.rel(dst))
    try:
        append_row(run.archive.ledger, row)
    except OSError as exc:
        try:
            move_path(dst, src)
            where = f"it was moved back to {src}"
        except ArchiveMoveError as back:
            where = f"moving it back to {src} also failed ({back.message}), so it is at {dst} with no ledger row"
        raise _LedgerWriteFailed(
            f"the ledger row for {item_id} could not be written to {run.archive.ledger} ({exc}); {where}",
            f"ls -l {shlex.quote(str(run.archive.ledger))}",
        ) from exc
    run.rows.append(row)


def _failure_block(run: _Run, item_id: str, exc: BaseException, fallback_remedy: str) -> str:
    """The dual-channel block for a run stopped by *exc* (at *item_id*, when given)."""
    cause = getattr(exc, "message", None) or str(exc)
    where = f" at {item_id}" if item_id else ""
    return format_error_contract(
        "archive.run_failed",
        f"archive: {run.op} for project '{run.project}' stopped{where}: {cause} "
        f"{len(run.rows)} item(s) moved before it are recorded in {run.archive.ledger}; "
        "nothing after it was attempted, and the next archive run retries.",
        getattr(exc, "remedy", None) or fallback_remedy,
    )


def _fail(run: _Run, item_id: str, exc: BaseException, fallback_remedy: str, **fields) -> RunResult:
    # CONVERT (docs/blocking-site-census.md): a move, queue rewrite or
    # ledger write failed. The run stops at this item and reports failure;
    # items already moved keep their ledger rows, and no release or wake
    # waits on the archive.
    # CENSUS-ANCHOR: archive-run-failed-01
    block = _failure_block(run, item_id, exc, fallback_remedy)
    run.say(block)
    return run.result(RESULT_FAILED, failed_item=item_id, error=block, **fields)


_RUN_ERRORS = (ArchiveMoveError, _LedgerWriteFailed, OpsError, OSError)


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


def apply(
    plan: ArchivePlan,
    *,
    task: str = NO_TASK,
    log: Optional[Callable[[str], None]] = None,
    accept_chain_lock: bool = False,
) -> RunResult:
    """Move *plan*'s items, queue lines and training logs into the archive.

    Args:
        plan: An :func:`~pgai_agent_kanban.archive.plan.plan` result; its
              ``kanban_root``, ``project`` and ``count`` say where and how
              many to keep live.
        task: The task this run is for, recorded in the repo-lock
              provenance; ``none`` when it is not for one task.
        log:  Receives each narration line (default: stderr).
        accept_chain_lock: Work under the repo lock when the wake chain
              this process runs inside holds it (see "The repo lock" in
              the module docstring); a lock held by any other process
              still skips.

    Returns:
        A :class:`RunResult`. See the module docstring for the statuses.
    """
    run = _Run(pathlib.Path(plan.kanban_root), plan.project, OP_APPLY, log)
    if plan.is_empty:
        run.say(f"archive: nothing to archive for project '{plan.project}'.")
        return run.result(RESULT_OK)

    held = _take_repo_lock(run, task, accept_chain_lock)
    if isinstance(held, RunResult):
        return held
    with held:
        return _apply_locked(run, plan)


def _apply_locked(run: _Run, planned: ArchivePlan) -> RunResult:
    """:func:`apply`, once the repo lock is held."""
    try:
        fresh = _plan(planned.project, planned.count, kanban_root=planned.kanban_root)
    except (OpsError, OSError, ValueError) as exc:
        return _fail(run, "", exc, f"ls -ld {shlex.quote(str(run.live.root))}")

    still = {(item.category, item.item_id) for item in fresh.items}
    wanted = {(item.category, item.item_id) for item in planned.items}
    dropped = []
    for item in planned.items:
        if (item.category, item.item_id) in still:
            continue
        dropped.append(item.item_id)
        if find_item(run.archive, item.item_id) is not None and find_item(run.live, item.item_id) is None:
            run.say(f"archive: {item.category} {item.item_id} is already archived; left as it is.")
        else:
            run.say(f"archive: {item.category} {item.item_id} is no longer selected; it stays live.")

    for item in fresh.items:
        if (item.category, item.item_id) not in wanted:
            continue
        try:
            _move_queue_lines(run, fresh.queue_lines_for(item.item_id), run.live, run.archive)
            _move_training_logs(run, fresh.training_logs_for(item.item_id), run.live, run.archive)
            _move_item(
                run, item.item_id, item.path, run.archive.root / item.relpath, fresh.last_released, item.category
            )
        except _RUN_ERRORS as exc:
            return _fail(
                run, item.item_id, exc, f"ls -ld {shlex.quote(str(item.path))}", dropped=tuple(dropped)
            )

    run.say(
        f"archive: project '{run.project}': moved {len(run.rows)} item(s), "
        f"{len(run.queue_lines)} queue line(s) and {len(run.training_logs)} training log(s) "
        f"to {run.archive.root}."
    )
    return run.result(RESULT_OK, dropped=tuple(dropped))


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------


def _refuse_not_archived(run: _Run, item_id: str) -> ArchiveRestoreError:
    parsed = classify_item_id(item_id)
    listing = run.archive.category_dir(parsed.category) if parsed is not None else run.archive.root
    shape = "" if parsed is not None else (
        " It is not a BUG-NNNN, PRIORITY-NNNN, vX.Y.Z-slug or task ID either."
    )
    return ArchiveRestoreError(
        "archive.restore_not_archived",
        f"archive: cannot restore '{item_id}' for project '{run.project}': "
        f"it is not in the archive at {run.archive.root}.{shape} Nothing was moved.",
        f"ls -1 {shlex.quote(str(listing))}",
        item_id,
    )


def _refuse_live_exists(run: _Run, item_id: str, archived: pathlib.Path, live: pathlib.Path) -> ArchiveRestoreError:
    return ArchiveRestoreError(
        "archive.restore_live_exists",
        f"archive: cannot restore '{item_id}' for project '{run.project}': the live project already "
        f"holds {live}, and a live item is never overwritten. Nothing was moved.",
        f"diff -r {shlex.quote(str(archived))} {shlex.quote(str(live))}",
        item_id,
    )


def _restore_target(run: _Run, item_id: str) -> tuple[pathlib.Path, pathlib.Path]:
    """``(archived path, live path)`` for *item_id*, or raise the refusal."""
    archived = find_item(run.archive, item_id)
    if archived is None:
        # BLOCK-WORTHY (docs/blocking-site-census.md): a restore of an ID the
        # archive does not hold has nothing to move; refusing names where
        # it looked instead of guessing at a near match. Only this restore
        # stops; nothing on disk changes.
        # CENSUS-ANCHOR: archive-restore-not-archived-01
        raise _refuse_not_archived(run, item_id)
    live = run.live.root / archived.relative_to(run.archive.root)
    existing = live if os.path.lexists(live) else find_item(run.live, item_id)
    if existing is not None:
        # BLOCK-WORTHY (docs/blocking-site-census.md): restoring over a live
        # item would overwrite live state. The restore stops before moving
        # anything; both copies stay as they are.
        # CENSUS-ANCHOR: archive-restore-live-exists-01
        raise _refuse_live_exists(run, item_id, archived, existing)
    return archived, live


def restore(
    project: str,
    item_id: str,
    *,
    kanban_root: Optional[PathLike] = None,
    task: str = NO_TASK,
    log: Optional[Callable[[str], None]] = None,
    accept_chain_lock: bool = False,
) -> RunResult:
    """Move one archived item, its queue lines and its training logs back live.

    Args:
        project:     Project name.
        item_id:     ``BUG-NNNN``, ``PRIORITY-NNNN``, a ``vX.Y.Z-slug``
                     requirements file name, or a task ID (read as
                     ``lookup.classify_item_id`` reads it).
        kanban_root: The kanban root; ``None`` uses
                     ``env.resolve_kanban_root()``.
        task:        The task this restore is for (lock provenance);
                     ``none`` when it is not for one task.
        log:         Receives each narration line (default: stderr).
        accept_chain_lock: Work under the repo lock when the wake chain
                     this process runs inside holds it, as :func:`apply`.

    Returns:
        A :class:`RunResult` whose one ledger row has category ``restore``.

    Raises:
        ArchiveRestoreError: *item_id* is not archived, or its live path is
            taken. Nothing was moved.
        ArchiveLayoutError: *project* is not a valid project name.
    """
    root = pathlib.Path(resolve_kanban_root() if kanban_root is None else kanban_root)
    run = _Run(root, project, OP_RESTORE, log)
    _restore_target(run, item_id)

    held = _take_repo_lock(run, task, accept_chain_lock)
    if isinstance(held, RunResult):
        return held
    with held:
        try:
            archived, live = _restore_target(run, item_id)
        except ArchiveRestoreError as exc:
            return _fail(run, item_id, exc, exc.remedy)
        parsed = classify_item_id(item_id)
        notes: list[str] = []
        followed = {parsed.canonical: parsed.category}
        lines = _queue_followers(run.archive, followed, notes) if parsed.category != CATEGORY_REQUIREMENTS else []
        logs = _training_followers(run.archive, [parsed.canonical], notes) if parsed.category == CATEGORY_TASKS else []
        for note in notes:
            run.say(f"archive: {note}")
        try:
            _move_queue_lines(run, lines, run.archive, run.live)
            _move_training_logs(run, logs, run.archive, run.live)
            _move_item(run, parsed.canonical, archived, live, last_released_version(root, project), CATEGORY_RESTORE)
        except _RUN_ERRORS as exc:
            return _fail(run, parsed.canonical, exc, f"ls -ld {shlex.quote(str(archived))}")
        run.say(
            f"archive: restored {parsed.canonical} to {live} with {len(run.queue_lines)} queue line(s) "
            f"and {len(run.training_logs)} training log(s)."
        )
        return run.result(RESULT_OK)
