"""
dispatch_ledger.py -- write and read a task's dispatch ledger.

The wake appends to ``<task_dir>/logs/dispatch.jsonl`` around each provider
invocation, and OVERWATCH's transient-error check reads the latest record
to find the span of the firing's batch log that belongs to the task.
``team/scripts/lib/overwatch_protocol.sh`` (``dispatch_record_append``,
``dispatch_record_latest``) owns the ledger path and calls this module
through ``pp_run_ops``; its ``dispatch_record_*`` comment block describes
the record and when each event is written.

A record is one JSON object per line, keys sorted::

    {"agent": ..., "batch_log": ..., "event": "dispatched"|"returned",
     "exit_code": <int>|null, "offset_end": <int>|null,
     "offset_start": <int>, "provider": ..., "recorded_at": "<UTC>Z"}

The last usable line is the task's most recent dispatch.

Usage:
    python3 -m pgai_agent_kanban.lib.dispatch_ledger append -- LEDGER EVENT AGENT PROVIDER BATCH_LOG OFFSET_START [OFFSET_END] [EXIT_CODE]
    python3 -m pgai_agent_kanban.lib.dispatch_ledger latest -- LEDGER

Verbs:
  append  Append one record (see ``dispatch_record``). Prints nothing and
          exits 0; when LEDGER cannot be written, prints
          ``dispatch_record_append: cannot write <LEDGER>: <error>`` on
          stderr and exits 1.
  latest  Print the latest usable record as one tab-separated line,
          ``<batch_log>\\t<offset_start>\\t<offset_end>`` (see
          ``latest_dispatch_span``), and exit 0. Exit 1, printing nothing,
          when LEDGER is missing or holds no usable record; exit 1 with one
          stderr line when it exists but cannot be read.

``--help`` (on its own or after a verb) prints usage and exits 0.

Public API:
    RECORDED_AT_FORMAT
    as_int(raw) -> int | None
    dispatch_record(event, agent, provider, batch_log, offset_start,
                    offset_end="", exit_code="", now=None) -> dict
    append_dispatch_record(ledger, record) -> None
    latest_dispatch_span(ledger) -> tuple[str, str, str] | None
    main(argv=None) -> int
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys
from typing import Optional

# Run by file path, this module has no parent package and only its own
# directory is on sys.path: put the package root this file sits in first,
# so the import below resolves from the same tree, never another. Imported
# as a package member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib.subcommand_cli import subcommand_main  # noqa: E402

__all__ = [
    "RECORDED_AT_FORMAT",
    "append_dispatch_record",
    "as_int",
    "dispatch_record",
    "latest_dispatch_span",
    "main",
]

#: strftime format of a record's ``recorded_at`` (UTC, second precision).
RECORDED_AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def as_int(raw: Optional[str]) -> Optional[int]:
    """Return *raw* as an int when, stripped, it is all digits; otherwise None.

    An empty or None *raw*, a sign, a decimal point or any other character
    reads as None, so a field the wake could not measure is stored as null.
    """
    raw = (raw or "").strip()
    return int(raw) if raw.isdigit() else None


def dispatch_record(
    event: str,
    agent: str,
    provider: str,
    batch_log: str,
    offset_start: str,
    offset_end: str = "",
    exit_code: str = "",
    now: Optional[datetime.datetime] = None,
) -> dict:
    """Build one ledger record from the wake's string arguments.

    ``offset_start`` is stored as an int, 0 when it is not all digits;
    ``offset_end`` and ``exit_code`` are stored as ints, or None (null)
    when empty or not all digits (see ``as_int``). ``recorded_at`` is *now*
    (default the current UTC time) in ``RECORDED_AT_FORMAT``.
    """
    stamp = now if now is not None else datetime.datetime.now(datetime.timezone.utc)
    return {
        "recorded_at": stamp.strftime(RECORDED_AT_FORMAT),
        "event": event,
        "agent": agent,
        "provider": provider,
        "batch_log": batch_log,
        "offset_start": as_int(offset_start) or 0,
        "offset_end": as_int(offset_end),
        "exit_code": as_int(exit_code),
    }


def append_dispatch_record(ledger: str, record: dict) -> None:
    """Append *record* to *ledger* as one JSON line with sorted keys.

    The file is created when absent; its directory must already exist.
    Raises OSError when the file cannot be opened or written.
    """
    with open(ledger, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def latest_dispatch_span(ledger: str) -> Optional[tuple]:
    """Return ``(batch_log, offset_start, offset_end)`` of *ledger*'s latest record.

    The latest record is the last line that parses as a JSON object with a
    non-empty ``batch_log``; blank lines, lines that are not JSON, and JSON
    that is not such an object are skipped, so a torn write never masks an
    earlier good record. Undecodable bytes are replaced, not fatal.

    All three values are strings: ``offset_start`` is the record's value
    when it is a non-negative int, else ``"0"``; ``offset_end`` is the
    record's value when it is a non-negative int, else ``""`` (the span is
    still open and runs to the end of the batch log).

    Returns None when the file holds no usable record. Raises OSError when
    it cannot be opened (a missing file included), so callers check
    existence first.
    """
    latest = None
    with open(ledger, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict) and obj.get("batch_log"):
                latest = obj
    if latest is None:
        return None
    start = latest.get("offset_start")
    end = latest.get("offset_end")
    return (
        str(latest["batch_log"]),
        str(start if isinstance(start, int) and start >= 0 else 0),
        str(end) if isinstance(end, int) and end >= 0 else "",
    )


def _cmd_append(args: argparse.Namespace) -> int:
    record = dispatch_record(
        args.event,
        args.agent,
        args.provider,
        args.batch_log,
        args.offset_start,
        args.offset_end,
        args.exit_code,
    )
    try:
        append_dispatch_record(args.ledger, record)
    except OSError as exc:
        print(f"dispatch_record_append: cannot write {args.ledger}: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_latest(args: argparse.Namespace) -> int:
    try:
        span = latest_dispatch_span(args.ledger)
    except FileNotFoundError:
        return 1
    except OSError as exc:
        print(f"dispatch_record_latest: cannot read {args.ledger}: {exc}", file=sys.stderr)
        return 1
    if span is None:
        return 1
    print("\t".join(span))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.lib.dispatch_ledger",
        description="Append to, or read the latest record of, a task's dispatch ledger.",
    )
    verbs = parser.add_subparsers(dest="verb", metavar="VERB")
    verbs.required = True

    append = verbs.add_parser(
        "append",
        help="Append one dispatch record.",
        description="Append one record; non-digit offsets and exit codes are stored as null.",
    )
    append.add_argument("ledger", help="The task's logs/dispatch.jsonl path.")
    append.add_argument("event", help="dispatched or returned.")
    append.add_argument("agent", help="Agent role, e.g. coder.")
    append.add_argument("provider", help="Provider name, e.g. claude.")
    append.add_argument("batch_log", help="Absolute path of the firing's batch log.")
    append.add_argument("offset_start", help="Byte offset where the task's output begins, e.g. 0.")
    append.add_argument("offset_end", nargs="?", default="", help="Byte offset where it ends; empty while open.")
    append.add_argument("exit_code", nargs="?", default="", help="Provider exit code; empty while open.")
    append.set_defaults(func=_cmd_append)

    latest = verbs.add_parser(
        "latest",
        help="Print the latest record's batch log and byte span.",
        description="Print '<batch_log>\\t<offset_start>\\t<offset_end>'; exit 1 when there is no usable record.",
    )
    latest.add_argument("ledger", help="The task's logs/dispatch.jsonl path.")
    latest.set_defaults(func=_cmd_latest)

    return parser


main = subcommand_main(_build_parser)


if __name__ == "__main__":
    sys.exit(main())
