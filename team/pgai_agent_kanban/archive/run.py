"""
run.py — the one archive operation the CLI and the REST route both call.

``run_archive(ctx, project, ...)`` is what ``team/scripts/archive.sh``
reaches through ``pp_run_ops pgai_agent_kanban.archive run_archive`` and
what ``POST /operations/archive`` calls in process. It resolves the project
name, validates the options, then calls the package's own functions:

- the default run: :func:`~pgai_agent_kanban.archive.plan.plan`, then
  :func:`~pgai_agent_kanban.archive.apply.apply` on that plan, then -- when
  that finished and ``archive_count`` is not 0 --
  :func:`~pgai_agent_kanban.archive.legacy.migrate_legacy`, which empties
  the requirements archive older installs kept at
  ``projects/<project>/tasks/archive/requirements/`` into the archive tree;
- ``dry_run``: :func:`~pgai_agent_kanban.archive.plan.plan` only, rendered
  per category by :func:`render_plan`, followed by the legacy migration's
  plan (:func:`~pgai_agent_kanban.archive.legacy.render_legacy_plan`);
  nothing on disk changes;
- ``restore_id``: :func:`~pgai_agent_kanban.archive.apply.restore` for that
  one item;
- ``reconcile_queues``: the queue reconciliation in
  :mod:`~pgai_agent_kanban.archive.reconcile` instead of an archive run --
  every live ``tasks/queues/*_backlog.md`` line naming an item that is
  archived and not live, or a finished (``[x]``) task whose folder is in
  neither tree, moves to the archive's queue file of the same name. With
  ``dry_run`` it lists those lines (and the lines it would leave, with the
  reason) and moves nothing. A line whose item is live is never touched,
  and a second apply moves nothing. It runs whatever ``archive_count`` is.
  The CLI (``--reconcile-queues``) exposes it; ``POST /operations/archive``
  does not.

Both surfaces hand in two line callbacks, ``out`` and ``err``, and get back
an :class:`ArchiveRun` whose ``exit_code`` is the CLI's. Because the text
each callback receives is produced here and nowhere else, the CLI's
``--dry-run`` output and the REST route's ``dry_run`` ``stdout`` are the
same text for the same board.

Order of checks
---------------
1. ``project`` resolves through ``ops.resolve.resolve_project_name`` (exact
   name, then an unambiguous prefix of a name registered in projects.cfg);
   its ``project_resolve.not_found`` / ``.ambiguous`` block is printed as
   the resolver formats it.
2. ``restore_id`` is not combined with ``dry_run``, ``count`` or
   ``reconcile_queues``, and ``reconcile_queues`` is not combined with
   ``count`` (``archive.conflicting_options``).
3. ``count`` is decimal digits (``archive.invalid_count``); ``None`` reads
   the project's ``[archive] archive_count``, and a configured value that
   is not a whole number prints ``archive.invalid_archive_count`` and
   archives nothing, as every archive run does.
4. ``projects/<project>/`` exists (``archive.project_missing``).

Every refusal is printed to ``err`` as the three-line dual-channel block
and nothing on disk changes.

Exit codes
----------
==  ===============================================================
0   The run finished: items moved, nothing to move, the dry run was
    printed, the item was restored, or the queue lines were reconciled.
1   Refused before anything moved: the project did not resolve, an
    option was invalid, the project directory is missing, or the
    restore was refused (the ID is not archived, or its live path is
    taken).
2   The run stopped at a failing item (``archive.run_failed``), in the
    archive run, in the legacy migration after it, or in a queue
    reconciliation; items moved before it are recorded in the ledger
    (queue lines already moved stay moved) and the next run retries.
3   Skipped: another process holds the project's repo lock
    (``archive.repo_lock_busy``); nothing moved, or -- when the lock was
    taken between the archive run and the legacy migration -- the legacy
    files stay where they are until the next run.
==  ===============================================================

The wake chain's own lock
-------------------------
``accept_chain_lock`` (CLI ``--accept-chain-lock``) is for a caller that
runs inside a wake chain, which holds the project's repo lock for its
whole run: the release finalize's archive step passes it. With it, a
repo lock held by this process or one of its ancestors is that chain's
own hold, and the archive run, a restore and the legacy migration all
work under it instead of exiting 3. A lock held by any other process
still exits 3 and moves nothing. ``scripts/archive.sh`` and
``POST /operations/archive`` do not pass it.

Archiving is quality-class work: none of these is raised as an exception.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
import shlex
from typing import Callable, Optional, Union

from pgai_agent_kanban.archive.apply import (
    NO_TASK,
    RESULT_OK,
    RESULT_SKIPPED,
    ArchiveRestoreError,
    RunResult,
    apply,
    restore,
)
from pgai_agent_kanban.archive.config import effective_archive_count
from pgai_agent_kanban.archive.layout import (
    ITEM_CATEGORIES,
    ArchiveLayoutError,
    archive_root,
    display_relpath,
    live_layout,
)
from pgai_agent_kanban.archive.legacy import migrate_legacy, plan_legacy, render_legacy_plan
from pgai_agent_kanban.archive.plan import ArchivePlan, plan
from pgai_agent_kanban.archive.reconcile import (
    QueueReconcilePlan,
    apply_queue_reconcile,
    plan_queue_reconcile,
    render_queue_reconcile_plan,
)
from pgai_agent_kanban.lib.error_contract import format_error_contract, parse_error_contract
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import OpsError
from pgai_agent_kanban.ops.resolve import resolve_project_name

__all__ = [
    "CLI_VERBS",
    "EXIT_FAILED",
    "EXIT_OK",
    "EXIT_REFUSED",
    "EXIT_SKIPPED",
    "ArchiveRun",
    "render_plan",
    "run_archive",
]

#: Verbs ``python3 -m pgai_agent_kanban.archive`` routes to this module.
CLI_VERBS: tuple[str, ...] = ("run_archive",)

#: Exit codes (see the module docstring).
EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_FAILED = 2
EXIT_SKIPPED = 3

#: The operator script, relative to the kanban root, as printed in a remedy.
_SCRIPT_RELPATH = ("scripts", "archive.sh")

_DIGITS_RE = re.compile(r"\s*[0-9]+\s*")

Line = Callable[[str], None]


@dataclasses.dataclass(frozen=True)
class ArchiveRun:
    """What one :func:`run_archive` call did.

    Attributes:
        exit_code: The CLI exit code (``EXIT_*``).
        project:   The resolved project name; the name as given when it did
                   not resolve.
        mode:      ``"apply"``, ``"dry-run"``, ``"restore"``,
                   ``"reconcile-queues"`` or ``"reconcile-queues-dry-run"``.
        plan:      The plan that was rendered or applied; ``None`` for a
                   restore or a refusal.
        result:    The :class:`~pgai_agent_kanban.archive.apply.RunResult`
                   of an apply or restore; ``None`` for a dry run or a
                   refusal.
        legacy:    The :class:`~pgai_agent_kanban.archive.apply.RunResult`
                   of the legacy requirements-archive migration that
                   follows a finished apply; ``None`` when it did not run
                   (a dry run, a restore, a refusal, ``archive_count`` 0,
                   or an apply that did not finish).
        queue_reconcile: The :class:`~pgai_agent_kanban.archive.reconcile.QueueReconcilePlan`
                   a queue reconciliation dry run rendered; ``None``
                   otherwise (an applied reconciliation's moves are in
                   ``result``).
    """

    exit_code: int
    project: str
    mode: str
    plan: Optional[ArchivePlan] = None
    result: Optional[RunResult] = None
    legacy: Optional[RunResult] = None
    queue_reconcile: Optional[QueueReconcilePlan] = None

    @property
    def ok(self) -> bool:
        """True when the run finished (exit code 0)."""
        return self.exit_code == EXIT_OK


class _LineStream:
    """A minimal text stream that hands each written line to a callback.

    Lets a function that prints to a stream (``effective_archive_count``)
    write through the caller's ``err`` callback instead of the process's
    own stderr, so the REST route returns the text in its envelope.
    """

    def __init__(self, line: Line) -> None:
        self._line = line
        self._pending = ""

    def write(self, text: str) -> int:
        self._pending += text
        while "\n" in self._pending:
            head, self._pending = self._pending.split("\n", 1)
            self._line(head)
        return len(text)

    def flush(self) -> None:
        if self._pending:
            self._line(self._pending)
            self._pending = ""


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_plan(planned: ArchivePlan) -> list[str]:
    """The dry-run report of *planned*, one line per list entry.

    Per category, in ``ITEM_CATEGORIES`` order, the items that would move
    and where to; then the queue lines and training logs that would follow
    them, the older items that stay live with the reason each stays, and
    any notes the plan carries. Paths are relative to the kanban root, so
    the same board renders the same text whichever surface asked.
    """
    root = pathlib.Path(planned.kanban_root)
    archive_rel = display_relpath(root, archive_root(root, planned.project))
    lines = [
        f"archive: dry run for project '{planned.project}' (archive_count {planned.count}, "
        f"Active RC {planned.active_rc}, Last Released {planned.last_released}); nothing is moved.",
    ]
    if planned.count == 0:
        lines.append("archive: archive_count is 0, so archiving is disabled and nothing would move.")
    for category in ITEM_CATEGORIES:
        items = planned.items_in(category)
        lines.append(f"{category}: {len(items)} item(s) would move")
        for item in items:
            lines.append(f"  {item.item_id}  {display_relpath(root, item.path)} -> {archive_rel}/{item.relpath}")
    lines.append(f"queue lines: {len(planned.queue_lines)} would follow their items")
    for line in planned.queue_lines:
        lines.append(f"  {display_relpath(root, line.queue_file)}:{line.line_number}  {line.text.strip()}")
    lines.append(f"training logs: {len(planned.training_logs)} would follow their tasks")
    for log in planned.training_logs:
        lines.append(f"  {display_relpath(root, log.path)} -> {archive_rel}/{log.relpath}")
    lines.append(f"held: {len(planned.held)} older item(s) stay live")
    for held in planned.held:
        lines.append(f"  {held.category} {held.item_id}: {held.code} -- {held.detail}")
    for note in planned.notes:
        lines.append(f"note: {note}")
    return lines


def _render_result(result: RunResult) -> list[str]:
    """One line per item an apply or restore moved, from its ledger rows."""
    return [f"archive: {row.category} {row.item_id}: {row.source} -> {row.destination}" for row in result.rows]


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def _refuse(err: Line, error: str, message: str, remedy: str) -> None:
    err(format_error_contract(error, message, remedy))


def _script(ctx: OpsContext) -> str:
    """The kanban root's own archive.sh, shell-quoted, for a runnable remedy."""
    return shlex.quote(str(pathlib.Path(ctx.kanban_root).joinpath(*_SCRIPT_RELPATH)))


def _parse_count(raw: Union[int, str, None]) -> Optional[int]:
    """``raw`` as a non-negative int, ``None`` when absent; raise ValueError when invalid."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        raise ValueError(raw)
    if isinstance(raw, int):
        if raw < 0:
            raise ValueError(raw)
        return raw
    if not _DIGITS_RE.fullmatch(str(raw)):
        raise ValueError(raw)
    return int(str(raw).strip())


# ---------------------------------------------------------------------------
# The operation
# ---------------------------------------------------------------------------


def run_archive(
    ctx: OpsContext,
    project: str,
    *,
    dry_run: bool = False,
    count: Union[int, str, None] = None,
    restore_id: Optional[str] = None,
    task: str = NO_TASK,
    out: Optional[Line] = None,
    err: Optional[Line] = None,
    accept_chain_lock: bool = False,
    reconcile_queues: bool = False,
) -> ArchiveRun:
    """Archive finished items of *project*, preview the run, or restore one item.

    Args:
        ctx:        The kanban root to work in.
        project:    Project name as typed: exact, or an unambiguous prefix
                    of a name registered in projects.cfg.
        dry_run:    Print the plan per category and move nothing.
        count:      Items kept live per category, overriding
                    ``[archive] archive_count``; decimal digits (an int or
                    its text). ``None`` or ``""`` reads the configured value.
                    ``0`` moves nothing.
        restore_id: Restore this one archived item (``BUG-NNNN``,
                    ``PRIORITY-NNNN``, a requirements file name or a task ID)
                    with its queue lines and training logs, instead of
                    archiving. Not combined with *dry_run* or *count*.
        task:       The task the run is for, recorded in the repo-lock
                    provenance; ``none`` when it is not for one task.
        out:        Receives each narration line (default: discarded).
        err:        Receives each refusal or failure block (default:
                    discarded).
        accept_chain_lock: Work under the project's repo lock when the
                    wake chain this process runs inside holds it (see
                    "The wake chain's own lock" above); a lock held by
                    any other process still skips with exit 3.
        reconcile_queues: Run the queue reconciliation instead of an
                    archive run (with *dry_run*, list it and move
                    nothing). Not combined with *restore_id* or *count*.

    Returns:
        An :class:`ArchiveRun`; see the module docstring for exit codes.
    """
    say = out or (lambda line: None)
    warn = err or (lambda line: None)

    def narrate(line: str) -> None:
        # apply()/restore() log through one channel; a dual-channel block
        # (a skip or a failure) belongs on err, narration on out.
        (warn if parse_error_contract(line) is not None else say)(line)

    if restore_id:
        mode = "restore"
    elif reconcile_queues:
        mode = "reconcile-queues-dry-run" if dry_run else "reconcile-queues"
    else:
        mode = "dry-run" if dry_run else "apply"
    try:
        resolved = resolve_project_name(ctx, project)
    except OpsError as exc:
        warn(str(exc))
        return ArchiveRun(EXIT_REFUSED, project, mode)

    has_count = count not in (None, "")
    conflict, remedy = "", ""
    if restore_id:
        given = " and ".join(
            name
            for name, on in (("--dry-run", dry_run), ("--count", has_count), ("--reconcile-queues", reconcile_queues))
            if on
        )
        if given:
            conflict = (
                f"--restore cannot be combined with {given}; a restore moves the one named item back "
                "and nothing else"
            )
            remedy = f"{_script(ctx)} --project {shlex.quote(resolved)} --restore {shlex.quote(restore_id)}"
    elif reconcile_queues and has_count:
        conflict = (
            "--reconcile-queues cannot be combined with --count; it moves queue lines whose item is "
            "no longer live and keeps no number of items"
        )
        remedy = f"{_script(ctx)} --project {shlex.quote(resolved)} --reconcile-queues --dry-run"
    if conflict:
        # BLOCK-WORTHY (docs/blocking-site-census.md): a restore moves one
        # named item back and has no plan to preview or count to apply, and
        # a queue reconciliation keeps no count, so a request mixing them is
        # ambiguous; it is refused, nothing moves.
        # CENSUS-ANCHOR: archive-run-conflicting-options-01
        _refuse(
            warn,
            "archive.conflicting_options",
            f"archive: {conflict}. Nothing was moved for project '{resolved}'.",
            remedy,
        )
        return ArchiveRun(EXIT_REFUSED, resolved, mode)

    try:
        keep = _parse_count(count)
    except ValueError:
        # BLOCK-WORTHY (docs/blocking-site-census.md): a count that is not a
        # whole number names no number of items to keep live; it is refused
        # rather than guessed, before anything is read or moved.
        # CENSUS-ANCHOR: archive-run-invalid-count-01
        _refuse(
            warn,
            "archive.invalid_count",
            f"archive: --count '{count}' is not a non-negative whole number (decimal digits "
            f"only; 0 moves nothing). Nothing was moved for project '{resolved}'.",
            f"{_script(ctx)} --project {shlex.quote(resolved)} --dry-run --count 55",
        )
        return ArchiveRun(EXIT_REFUSED, resolved, mode)

    root = pathlib.Path(ctx.kanban_root)
    try:
        layout = live_layout(root, resolved)
    except ArchiveLayoutError as exc:
        warn(str(exc))
        return ArchiveRun(EXIT_REFUSED, resolved, mode)
    if not layout.root.is_dir():
        # BLOCK-WORTHY (docs/blocking-site-census.md): with no live project
        # directory there is nothing to plan or restore into, and an empty
        # plan would read as "nothing to archive"; refused, nothing moves.
        # CENSUS-ANCHOR: archive-run-project-missing-01
        _refuse(
            warn,
            "archive.project_missing",
            f"archive: project '{resolved}' has no directory at {layout.root}; nothing was "
            "planned or moved.",
            f"ls -1 {shlex.quote(str(layout.root.parent))}",
        )
        return ArchiveRun(EXIT_REFUSED, resolved, mode)

    if reconcile_queues:
        if dry_run:
            reconciled = plan_queue_reconcile(resolved, kanban_root=root)
            for line in render_queue_reconcile_plan(reconciled):
                say(line)
            return ArchiveRun(EXIT_OK, resolved, mode, queue_reconcile=reconciled)
        result = apply_queue_reconcile(
            resolved, kanban_root=root, task=task, log=narrate, accept_chain_lock=accept_chain_lock
        )
        for line in result.queue_lines:
            say(f"archive: moved queue line: {line}")
        return ArchiveRun(_result_exit_code(result), resolved, mode, result=result)

    if restore_id:
        try:
            result = restore(
                resolved, restore_id, kanban_root=root, task=task, log=narrate, accept_chain_lock=accept_chain_lock
            )
        except ArchiveRestoreError as exc:
            warn(str(exc))
            return ArchiveRun(EXIT_REFUSED, resolved, mode)
        for line in _render_result(result):
            say(line)
        return ArchiveRun(_result_exit_code(result), resolved, mode, result=result)

    if keep is None:
        stream = _LineStream(warn)
        keep = effective_archive_count(root, resolved, stream=stream)
        stream.flush()
    planned = plan(resolved, keep, kanban_root=root)

    if dry_run:
        for line in render_plan(planned):
            say(line)
        if planned.count == 0:
            say("legacy requirements archive: archive_count is 0, so it would not be migrated.")
        else:
            for line in render_legacy_plan(plan_legacy(resolved, kanban_root=root)):
                say(line)
        return ArchiveRun(EXIT_OK, resolved, mode, plan=planned)

    if planned.count == 0:
        say(f"archive: archive_count is 0 for project '{resolved}', so archiving is disabled.")
    result = apply(planned, task=task, log=narrate, accept_chain_lock=accept_chain_lock)
    for line in _render_result(result):
        say(line)
    if result.status != RESULT_OK or planned.count == 0:
        return ArchiveRun(_result_exit_code(result), resolved, mode, plan=planned, result=result)

    legacy = migrate_legacy(
        resolved, kanban_root=root, task=task, log=narrate, accept_chain_lock=accept_chain_lock
    )
    for line in _render_result(legacy):
        say(line)
    return ArchiveRun(_result_exit_code(legacy), resolved, mode, plan=planned, result=result, legacy=legacy)


def _result_exit_code(result: RunResult) -> int:
    if result.status == RESULT_OK:
        return EXIT_OK
    if result.status == RESULT_SKIPPED:
        return EXIT_SKIPPED
    return EXIT_FAILED


# ---------------------------------------------------------------------------
# Module CLI: python3 -m pgai_agent_kanban.archive run_archive ...
# ---------------------------------------------------------------------------


def _cli_main(argv: Optional[list[str]] = None) -> int:
    """Run :func:`run_archive` from the command line.

    ``run_archive --kanban-root ROOT --project NAME [--dry-run] [--count N]
    [--restore ID] [--reconcile-queues] [--task ID] [--accept-chain-lock]``. Narration goes to
    stdout, refusal and failure blocks to stderr; the exit code is the
    run's.
    """
    import argparse
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in CLI_VERBS:
        sys.stderr.write(f"pgai_agent_kanban.archive: VERB must be one of: {', '.join(CLI_VERBS)}\n")
        return EXIT_REFUSED
    parser = argparse.ArgumentParser(prog="python3 -m pgai_agent_kanban.archive run_archive")
    parser.add_argument(
        "--kanban-root",
        required=True,
        help="kanban root holding projects.cfg and projects/ (required)",
    )
    parser.add_argument(
        "--project",
        required=True,
        help="project name: exact, or an unambiguous prefix of a registered name (required)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan per category and move nothing",
    )
    parser.add_argument(
        "--count",
        default=None,
        help=(
            "items kept live per category, overriding [archive] archive_count; 0 moves nothing "
            "(default: the configured value)"
        ),
    )
    parser.add_argument(
        "--restore",
        default=None,
        help=(
            "restore this one archived item (a bug, priority, requirements file or task ID) "
            "instead of archiving"
        ),
    )
    parser.add_argument(
        "--reconcile-queues",
        action="store_true",
        help=(
            "move live queue lines naming an archived item, or a finished ([x]) task with no folder "
            "live or archived, to the archive's queue files; with --dry-run, list them and move nothing"
        ),
    )
    parser.add_argument(
        "--task",
        default=NO_TASK,
        help="the task the run is for, recorded in the repo-lock provenance (default: none)",
    )
    parser.add_argument(
        "--accept-chain-lock",
        action="store_true",
        help="work under the project's repo lock when the wake chain this process runs inside holds it",
    )
    opts = parser.parse_args(args[1:])

    def _out(line: str) -> None:
        print(line, flush=True)

    def _err(line: str) -> None:
        print(line, file=sys.stderr, flush=True)

    run = run_archive(
        OpsContext(kanban_root=pathlib.Path(opts.kanban_root)),
        opts.project,
        dry_run=opts.dry_run,
        count=opts.count,
        restore_id=opts.restore or None,
        task=opts.task or NO_TASK,
        out=_out,
        err=_err,
        accept_chain_lock=opts.accept_chain_lock,
        reconcile_queues=opts.reconcile_queues,
    )
    return run.exit_code
