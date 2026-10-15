"""
transient_backoff.py — when the transient-error handlers may requeue a task
blocked by a transient provider error.

Two controllers requeue a transient block: the wake's auto-retry
(``recheck_blocked_tasks`` in team/scripts/lib/wake_task_state.sh) and
OVERWATCH's sweep check (team/scripts/lib/overwatch-checks/
check-transient-api-error.sh). Both share one retry count and one ceiling
(the ``## Transient Retry`` record and ``[wake] transient_retry_cap``); both
ask this module, through the bash wrapper ``transient_backoff_due`` in
overwatch_protocol.sh, whether the backoff window before the next requeue
has passed.

The window
----------
The window comes from the ``transient_api_error`` row of the block-triage
classifier table (``block-classifier.yaml``: ``cooldown_s`` 300, ``backoff``
factor 2, cap 1200 s as shipped), so the schedule has one source. With the
record's ``retry_count`` at *c*, the next requeue waits::

    window(c) = min(cooldown_s * factor ** (max(c, 1) - 1), cap_s)

after the task's latest block (the ``## Block Record`` ``last_blocked_at``)
— 5, 10, then 20 minutes for c = 1, 2, 3 as shipped.

Fail open
---------
The backoff is quality class: it delays a retry, it never stops one for
good. A task with no Block Record (nothing to measure from), a table that
does not load, or a status.md that cannot be read is reported due, which is
how the handlers behaved before the backoff existed. The ceiling is the
handlers' own and is unaffected.

Usage (CLI; the bash wrapper calls it):
    python3 -m pgai_agent_kanban.overwatch.transient_backoff due \\
        STATUS_FILE [--now ISO] [--table YAML_FILE]

Prints ``due`` and exits 0 when the next requeue may happen now; prints
``not-before <ISO>`` and exits 3 when it must wait. Usage errors exit 2.

Usage (import):
    from pgai_agent_kanban.overwatch.transient_backoff import requeue_due
    due, not_before = requeue_due(status_file)
"""

from __future__ import annotations

import argparse
import datetime
import pathlib
import sys
from pathlib import Path
from typing import Optional

# Run by file path, this module has no parent package; put its own package
# root first on sys.path so the imports below resolve from the same tree.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib.timeutil import format_iso, try_parse_iso  # noqa: E402
from pgai_agent_kanban.overwatch.block_classifier import TableError, load_table  # noqa: E402
from pgai_agent_kanban.overwatch.block_record import BlockRecordError, read_block_record  # noqa: E402
from pgai_agent_kanban.overwatch.block_requeue import transient_retry_state  # noqa: E402

#: The classifier row the schedule is read from.
TRANSIENT_REASON_CODE = "transient_api_error"

EXIT_DUE = 0
EXIT_USAGE = 2
EXIT_NOT_YET = 3


def backoff_window_s(retry_count: int, table_path: "str | Path | None" = None) -> Optional[int]:
    """Seconds the requeue after a record at *retry_count* waits after the block.

    None when the table does not load or has no transient row (fail open).
    """
    try:
        rule = load_table(table_path).rules.get(TRANSIENT_REASON_CODE)
    except TableError:
        return None
    if rule is None:
        return None
    return rule.window_s(max(retry_count, 1) - 1)


def requeue_not_before(
    status_text: str, table_path: "str | Path | None" = None
) -> Optional[datetime.datetime]:
    """The earliest time the next transient requeue may happen, or None for "now".

    Measured from the Block Record's ``last_blocked_at``; None when there is
    no readable record or no window (fail open).
    """
    try:
        record = read_block_record(status_text)
    except BlockRecordError:
        return None
    if record is None:
        return None
    blocked_at = try_parse_iso(record.last_blocked_at)
    count, _limit = transient_retry_state(status_text)
    window = backoff_window_s(count, table_path)
    if blocked_at is None or window is None:
        return None
    return blocked_at + datetime.timedelta(seconds=window)


def requeue_due(
    status_file: "str | Path",
    *,
    now: "datetime.datetime | None" = None,
    table_path: "str | Path | None" = None,
) -> tuple[bool, Optional[str]]:
    """``(due, not_before)`` for the task's next transient requeue.

    ``not_before`` is the canonical ISO time the window ends, or None when
    there is no window to wait out. An unreadable status.md is due.
    """
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    try:
        text = Path(status_file).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return True, None
    not_before = requeue_not_before(text, table_path)
    if not_before is None:
        return True, None
    return moment >= not_before, format_iso(not_before)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="transient_backoff",
        description=(
            "Say whether the backoff window before a transient-error requeue has passed. "
            "The window grows with the ## Transient Retry count (the transient_api_error row "
            "of the block-triage classifier table) and runs from the Block Record's "
            "last_blocked_at. Fails open: anything unreadable is due."
        ),
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    due = sub.add_parser(
        "due",
        help="print 'due' (exit 0) or 'not-before ISO' (exit 3) for one task",
        description="Print 'due' and exit 0, or 'not-before <ISO>' and exit 3, for STATUS_FILE.",
    )
    due.add_argument("status_file", metavar="STATUS_FILE", help="the blocked task's status.md")
    due.add_argument("--now", metavar="ISO", help="the decision time (ISO-8601); default: the current UTC time")
    due.add_argument("--table", metavar="YAML_FILE", help="the classifier table; default: the shipped table")
    return parser


def main(argv: Optional[list] = None) -> int:
    """CLI entry point; returns the exit code."""
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return EXIT_DUE
    now = None
    if args.now is not None:
        now = try_parse_iso(args.now)
        if now is None:
            parser.error(f"--now {args.now!r} is not an ISO-8601 time (e.g. 2026-01-01T00:00:00+00:00)")
    is_due, not_before = requeue_due(args.status_file, now=now, table_path=args.table)
    if is_due:
        sys.stdout.write("due\n")
        return EXIT_DUE
    sys.stdout.write(f"not-before {not_before}\n")
    return EXIT_NOT_YET


if __name__ == "__main__":
    sys.exit(main())
