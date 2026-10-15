"""
blocked_ledger.py — the per-RC blocked-interval ledger.

An RC's rc-metrics "Wall" column is honest open-to-ship elapsed time, and
stays that way. This ledger carries the second number a release can be
read against: how many of those minutes the RC spent unable to proceed at
all — parked under a HALT, or with its chain stalled on a BLOCKED task
waiting on a human — so a caller can report real execution as Wall minus
Blocked without ever mutating Wall itself.

Record path convention: <kanban_root>/projects/<project_name>/metrics/
blocked-intervals.jsonl — a project-scoped metrics sidecar, resolved
through pp_project_root (team/scripts/lib/project_paths.sh). Deliberately
NOT release-state.md: that file is CM's, with four fixed fields that
always exist, so it cannot carry this ledger's lifecycle — an RC that was
never blocked has no entry here at all, and "no entry" must read as zero
blocked minutes rather than as a missing required field.

Lifecycle: the file is created lazily by the first block-start or
block-end event stamped for ANY RC of the project (append_block_start /
append_block_end below). A project that has never had a blocked RC has no
file; an RC within an existing file that was never blocked has no lines
naming its version. Both read back as zero blocked minutes — absence is a
valid state, never an error. The file accumulates boundary events for
every RC of the project across releases (the same per-project, many-
records shape as pgai_agent_kanban.deep_verify.ledger, filtered by a join
key at read time rather than split one-file-per-RC); each RC's own slice
of records is meant to stop changing once that RC ships — this module does
not enforce that immutability itself (it never deletes or rewrites a past
boundary, and the reader never writes), it is a discipline the two writers
(a follow-up ticket) uphold when they stamp events.

On-disk format: one JSON object per line (JSON Lines). An append is a
single self-contained line, so a torn write from a crashed process can
only ever corrupt the file's own last line, and a parse fault is
localized to naming the offending line rather than an entire document:

    {"version": "v2.24.0", "kind": "block-start", "source": "halt:project", "timestamp": "2026-09-18T09:15:22+00:00"}
    {"version": "v2.24.0", "kind": "block-end",   "source": "halt:project", "timestamp": "2026-09-18T09:55:22+00:00"}

Fields, all required, all single-line strings:
  version    the RC's release version, e.g. "v2.24.0" — the join key the
             reader filters on; lines for every other RC (and, since the
             ledger is per-project, only ever this project) coexist
             harmlessly in the same file and are ignored.
  kind       "block-start" or "block-end" (VALID_KINDS below) — no other
             value is ever written or accepted on read.
  source     a free-form label naming WHAT blocked — e.g. "halt:project",
             "halt:global", or "task:<TASK-ID>". Boundaries pair by
             (version, source): two independent, concurrent blockers (a
             HALT and a BLOCKED task) each open and close their own
             interval without one source's end ever closing another's
             still-open start.
  timestamp  ISO-8601, e.g. "2026-09-18T09:15:22+00:00" or the "Z" suffix
             spelling; both parse.

Two write operations, both atomic (temp file in the same directory +
os.replace — the same pattern pgai_agent_kanban.deep_verify.ledger and
pgai_agent_kanban.overwatch.ledger both use for their own atomic writes):

  - append_block_start(ledger_path, version, source, timestamp) — opens a
    new interval for (version, source). Raises LedgerError, writing
    nothing, if (version, source) already has an open, unclosed start —
    a source cannot block twice without unblocking first; this
    invariant is what makes the reader's start/end pairing unambiguous.
  - append_block_end(ledger_path, version, source, timestamp) — closes
    the open interval for (version, source). Raises LedgerError, writing
    nothing, if there is no matching open start.

  Both of the refusals above — a second start while one is already open,
  and an end with no open start to close — mean the boundary was already
  in the state THIS call asked for: a caller re-attempting the same
  boundary event every cycle (the common wake-path shape) hits one of
  these every time after the first and is expected to. Both carry the
  distinct machine error key ``blocked-ledger.no-op`` (ERROR_NO_OP
  below), separate from ``blocked-ledger.malformed``, so a caller — such
  as the wake-path adapter — can treat "already in the requested state"
  as its own designed steady state rather than as a write failure, while
  every other LedgerError (a malformed ledger, an unwritable directory,
  an invalid window) keeps its own distinguishing key and still reads as
  a genuine fault.

Read path — the one reader every consumer of this ledger calls, never
re-derived (this module's whole reason to exist):

  - blocked_minutes(ledger_path, version, window_opened_at, window_closed_at)
    returns the merged, clamped blocked-minutes total (a float; whole
    when every boundary falls on a whole minute) for *version* within the
    RC's own [window_opened_at, window_closed_at] window:
      1. every block-start/block-end pair recorded for *version* (paired
         per source, in file order) becomes one interval; a source still
         open when the file is read — no block-end recorded at all, e.g.
         the RC shipped while still blocked — is treated as open-ended
         and clamped to window_closed_at,
      2. every interval is clamped to [window_opened_at, window_closed_at]:
         a boundary outside the window is cut to the window's own edge,
         and an interval falling entirely outside the window contributes
         nothing,
      3. clamped intervals are merged — overlapping or touching spans
         collapse into one — before summing, so two sources blocked over
         the same wall-clock minutes are never double-counted,
      4. the merged total, in minutes, is returned.
    An absent ledger file, or one with no boundaries recorded for
    *version*, returns 0.0 — absence is a valid state, never an error.
    Raises LedgerError — carrying error/message/remedy fields, and
    rendering as the three-line dual-channel house shape via
    pgai_agent_kanban.lib.error_contract.format_error_contract when
    stringified — when the ledger file has non-whitespace content that
    fails to parse cleanly: an unparseable line, a line missing a
    required field, an out-of-vocabulary kind, or a start/end pairing
    that cannot be resolved (an end with no open start, or two opens for
    the same source with no end between them). A malformed ledger
    refuses rather than silently reporting a fabricated or partial
    number — CENSUS CLASS CONVERT (docs/blocking-site-census.md): the
    refusal never halts a release path on its own; it yields no number
    for the caller to display instead, exactly like the CLI's own `read`
    subcommand below.

Usage (CLI):
    python3 blocked_ledger.py start <ledger_path> <version> <source> <timestamp>
    python3 blocked_ledger.py end   <ledger_path> <version> <source> <timestamp>
    python3 blocked_ledger.py read  <ledger_path> <version> <window_opened_at> <window_closed_at>

Usage (import):
    from pgai_agent_kanban.metrics.blocked_ledger import (
        LedgerError, ERROR_NO_OP, append_block_start, append_block_end,
        blocked_minutes, ledger_path_for_project,
    )
    append_block_start(ledger_path, "v2.24.0", "halt:project", "2026-09-18T09:15:22+00:00")
    append_block_end(ledger_path, "v2.24.0", "halt:project", "2026-09-18T09:55:22+00:00")
    minutes = blocked_minutes(
        ledger_path, "v2.24.0",
        "2026-09-18T00:00:00+00:00", "2026-09-19T00:00:00+00:00",
    )   # -> 40.0
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from datetime import datetime
from typing import Optional

# Run by file path (``python3 .../blocked_ledger.py``) or loaded from a file
# outside its package, this module has no parent package and the interpreter
# puts only its own directory on sys.path, so the package imports below would
# not resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# imports resolve from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib.atomic_write import atomic_write  # noqa: E402
from pgai_agent_kanban.lib.timeutil import try_parse_iso  # noqa: E402

VALID_KINDS = ("block-start", "block-end")

_REQUIRED_FIELDS = ("version", "kind", "source", "timestamp")

# Machine error key for a write attempt that found the boundary already in
# the state the caller asked for -- a second block-start with one already
# open, or a block-end with no open block-start to close. Distinct from
# "blocked-ledger.malformed" (used for actual structural corruption) so a
# caller can tell its own designed steady state apart from a genuine fault.
# See the module docstring's "Two write operations" section.
ERROR_NO_OP = "blocked-ledger.no-op"


class LedgerError(Exception):
    """Raised for structural problems with the ledger: an unparseable
    line, a record missing a required field, an out-of-vocabulary kind,
    or a start/end pairing that does not resolve. Carries the
    dual-channel error-contract fields (error/message/remedy) so a
    caller — CLI or library — can speak the house shape without
    re-deriving it; str(exc) already renders the three-line block."""

    def __init__(self, error: str, message: str, remedy: str):
        from pgai_agent_kanban.lib.error_contract import format_error_contract

        self.error = error
        self.message = message
        self.remedy = remedy
        super().__init__(format_error_contract(error, message, remedy))


def _write_ledger_file(target: str, content: str) -> None:
    """Atomically replace *target* with *content* via the shared writer,
    creating its directory first. A write failure is a LedgerError
    naming the directory to repair.
    """
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    try:
        atomic_write(target, content, prefix=".blocked_ledger_tmp_")
    except OSError as exc:
        raise LedgerError(
            "blocked-ledger.write-failed",
            f"writing the blocked-interval ledger at {target} failed: {exc}",
            f"confirm the parent directory is writable, then retry: "
            f"install -d -m 0755 {os.path.dirname(os.path.abspath(target))}",
        ) from exc


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_records(ledger_path: str) -> list:
    """Parse *ledger_path* into a list of boundary-event dicts, in file
    (append) order.

    Returns [] when the file does not exist, or exists but is empty or
    whitespace-only — both mean "no boundary ever stamped", which is
    exactly what an unblocked project/RC looks like.

    Raises LedgerError on the first line that fails to parse cleanly: not
    valid JSON, missing one of version/kind/source/timestamp, an "kind"
    outside VALID_KINDS, or a "timestamp" that does not parse — a
    malformed ledger refuses rather than silently skipping the bad line
    and reporting a partial answer.
    """
    if not os.path.isfile(ledger_path):
        return []
    with open(ledger_path, "r", encoding="utf-8") as handle:
        text = handle.read()
    if not text.strip():
        return []

    records = []
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LedgerError(
                "blocked-ledger.malformed",
                f"{ledger_path} line {lineno} is not valid JSON: {exc}",
                f"inspect and repair or remove the offending line: "
                f"sed -n '{lineno}p' {ledger_path}",
            ) from exc
        if not isinstance(record, dict):
            raise LedgerError(
                "blocked-ledger.malformed",
                f"{ledger_path} line {lineno} is not a JSON object "
                f"(got {type(record).__name__})",
                f"inspect and repair or remove the offending line: "
                f"sed -n '{lineno}p' {ledger_path}",
            )
        missing = [f for f in _REQUIRED_FIELDS if not record.get(f)]
        if missing:
            raise LedgerError(
                "blocked-ledger.malformed",
                f"{ledger_path} line {lineno} is missing required field(s): "
                f"{', '.join(missing)}",
                f"inspect and repair or remove the offending line: "
                f"sed -n '{lineno}p' {ledger_path}",
            )
        if record["kind"] not in VALID_KINDS:
            raise LedgerError(
                "blocked-ledger.malformed",
                f"{ledger_path} line {lineno} has kind={record['kind']!r}, "
                f"not one of {VALID_KINDS}",
                f"inspect and repair or remove the offending line: "
                f"sed -n '{lineno}p' {ledger_path}",
            )
        if try_parse_iso(str(record["timestamp"]), naive_as_utc=False) is None:
            raise LedgerError(
                "blocked-ledger.malformed",
                f"{ledger_path} line {lineno} has an unparseable timestamp: "
                f"{record['timestamp']!r}",
                f"inspect and repair or remove the offending line: "
                f"sed -n '{lineno}p' {ledger_path}",
            )
        records.append(record)
    return records


# ---------------------------------------------------------------------------
# append_block_start / append_block_end
# ---------------------------------------------------------------------------


def _append_line(ledger_path: str, record: dict) -> None:
    existing = ""
    if os.path.isfile(ledger_path):
        with open(ledger_path, "r", encoding="utf-8") as handle:
            existing = handle.read()
    line = json.dumps(record, sort_keys=True)
    new_content = existing if existing.endswith("\n") or not existing else existing + "\n"
    new_content += line + "\n"
    _write_ledger_file(ledger_path, new_content)


def _open_sources(records: list, version: str) -> dict:
    """Replay *records* for *version* and return {source: start_ts} for
    every source with an unclosed open interval after the replay."""
    open_by_source: dict = {}
    for record in records:
        if record["version"] != version:
            continue
        source = record["source"]
        if record["kind"] == "block-start":
            if source in open_by_source:
                raise LedgerError(
                    "blocked-ledger.malformed",
                    f"version {version!r} source {source!r} has two "
                    "block-start events with no block-end between them",
                    "append a matching block-end for the source before "
                    "appending another block-start, or repair the ledger "
                    "by hand to remove the duplicate start",
                )
            open_by_source[source] = record["timestamp"]
        else:  # block-end
            if source not in open_by_source:
                raise LedgerError(
                    "blocked-ledger.malformed",
                    f"version {version!r} source {source!r} has a "
                    "block-end event with no matching open block-start",
                    "remove the orphaned block-end, or append the missing "
                    "block-start ahead of it, then re-read",
                )
            del open_by_source[source]
    return open_by_source


def append_block_start(ledger_path: str, version: str, source: str, timestamp: str) -> None:
    """Stamp the start of a blocked interval for (*version*, *source*).
    See module docstring "Two write operations" for the pairing rule this
    enforces and the atomicity guarantee. The refusal below is the
    designed steady state for a caller that re-attempts the same
    block-start every cycle while still blocked -- it carries the
    distinct ERROR_NO_OP key precisely so callers can tell it apart from
    genuine ledger corruption."""
    records = parse_records(ledger_path)
    open_by_source = _open_sources(records, version)
    if source in open_by_source:
        raise LedgerError(
            ERROR_NO_OP,
            f"version {version!r} source {source!r} is already blocked "
            f"(open since {open_by_source[source]}) — refusing a second "
            "block-start",
            "append a block-end for this source before starting a new "
            "block, or confirm the caller is not double-firing",
        )
    _append_line(
        ledger_path,
        {"version": version, "kind": "block-start", "source": source, "timestamp": timestamp},
    )


def append_block_end(ledger_path: str, version: str, source: str, timestamp: str) -> None:
    """Stamp the end of a blocked interval for (*version*, *source*). See
    module docstring "Two write operations" for the pairing rule this
    enforces and the atomicity guarantee. The refusal below is the
    designed steady state for a caller that re-attempts the same
    block-end every cycle once the interval is already closed (or was
    never opened) -- it carries the distinct ERROR_NO_OP key precisely so
    callers can tell it apart from genuine ledger corruption."""
    records = parse_records(ledger_path)
    open_by_source = _open_sources(records, version)
    if source not in open_by_source:
        raise LedgerError(
            ERROR_NO_OP,
            f"version {version!r} source {source!r} has no open "
            "block-start to close",
            "append a block-start for this source first, or confirm the "
            "caller is not closing a block that was never opened",
        )
    _append_line(
        ledger_path,
        {"version": version, "kind": "block-end", "source": source, "timestamp": timestamp},
    )


# ---------------------------------------------------------------------------
# blocked_minutes — the single reader
# ---------------------------------------------------------------------------


def _intervals_for_version(records: list, version: str, window_closed_at: datetime) -> list:
    """Return a list of (start, end) datetime pairs for *version*, pairing
    block-start/block-end per source in file order. A source still open
    at the end of the replay is closed at *window_closed_at* — "still
    blocked when the RC shipped" contributes only up to the window edge."""
    open_by_source: dict = {}
    intervals = []
    for record in records:
        if record["version"] != version:
            continue
        source = record["source"]
        ts = try_parse_iso(str(record["timestamp"]), naive_as_utc=False)
        if record["kind"] == "block-start":
            if source in open_by_source:
                raise LedgerError(
                    "blocked-ledger.malformed",
                    f"version {version!r} source {source!r} has two "
                    "block-start events with no block-end between them",
                    "repair the ledger to remove the duplicate start, or "
                    "append the missing block-end",
                )
            open_by_source[source] = ts
        else:  # block-end
            if source not in open_by_source:
                raise LedgerError(
                    "blocked-ledger.malformed",
                    f"version {version!r} source {source!r} has a "
                    "block-end event with no matching open block-start",
                    "repair the ledger to remove the orphaned block-end, "
                    "or append the missing block-start ahead of it",
                )
            intervals.append((open_by_source.pop(source), ts))
    for start in open_by_source.values():
        intervals.append((start, window_closed_at))
    return intervals


def _clamp(start: datetime, end: datetime, window_opened_at: datetime, window_closed_at: datetime):
    """Clamp one (start, end) interval to [window_opened_at,
    window_closed_at]. Returns None when the interval falls entirely
    outside the window (contributes nothing)."""
    clamped_start = max(start, window_opened_at)
    clamped_end = min(end, window_closed_at)
    if clamped_start >= clamped_end:
        return None
    return (clamped_start, clamped_end)


def _merge(intervals: list) -> list:
    """Merge overlapping or touching (start, end) pairs, sorted by
    start."""
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda pair: pair[0])
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def blocked_minutes(
    ledger_path: str,
    version: str,
    window_opened_at: str,
    window_closed_at: str,
) -> float:
    """Return the merged, clamped blocked-minutes total for *version*
    within [window_opened_at, window_closed_at]. See the module
    docstring's "Read path" section for the full contract, including the
    absent-ledger-means-zero rule and the exact LedgerError conditions."""
    opened_dt = try_parse_iso(str(window_opened_at), naive_as_utc=False)
    if opened_dt is None:
        raise LedgerError(
            "blocked-ledger.invalid-window",
            f"window_opened_at {window_opened_at!r} is not a parseable "
            "ISO-8601 timestamp",
            "pass the RC's own opened timestamp in ISO-8601, e.g. "
            "2026-09-18T09:15:22+00:00",
        )
    closed_dt = try_parse_iso(str(window_closed_at), naive_as_utc=False)
    if closed_dt is None:
        raise LedgerError(
            "blocked-ledger.invalid-window",
            f"window_closed_at {window_closed_at!r} is not a parseable "
            "ISO-8601 timestamp",
            "pass the RC's own closed/shipped timestamp in ISO-8601, e.g. "
            "2026-09-18T09:15:22+00:00",
        )

    records = parse_records(ledger_path)
    if not records:
        return 0.0

    raw_intervals = _intervals_for_version(records, version, closed_dt)
    if not raw_intervals:
        return 0.0

    clamped = []
    for start, end in raw_intervals:
        result = _clamp(start, end, opened_dt, closed_dt)
        if result is not None:
            clamped.append(result)
    if not clamped:
        return 0.0

    merged = _merge(clamped)
    total_seconds = sum((end - start).total_seconds() for start, end in merged)
    return total_seconds / 60.0


# ---------------------------------------------------------------------------
# Path convention helper
# ---------------------------------------------------------------------------


def ledger_path_for_project(kanban_root: str, project_name: str) -> str:
    """Return the standard ledger path:
    <kanban_root>/projects/<project_name>/metrics/blocked-intervals.jsonl."""
    return os.path.join(kanban_root, "projects", project_name, "metrics", "blocked-intervals.jsonl")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    """Run the blocked-interval ledger CLI.

    ``start|end LEDGER VERSION SOURCE TIMESTAMP`` stamps one boundary event and
    prints ``{"written": true}``; ``read LEDGER VERSION OPENED_AT CLOSED_AT``
    prints ``{"blocked_minutes": N}``. Exit codes: 0 done; 1 a ledger error,
    reported through the dual-channel error contract; 2 usage error (argparse).
    """
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Append or read the per-project blocked-interval ledger. "
            "'start'/'end' stamp one boundary event. 'read' prints the "
            "merged, clamped blocked-minutes total for a version, clamped "
            "to its own opened/closed window, as JSON."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_start = subparsers.add_parser("start", help="Stamp a block-start event.")
    p_start.add_argument(
        "ledger_path",
        help="path to the project's blocked-intervals.jsonl ledger (created on first write)",
    )
    p_start.add_argument(
        "version",
        help="the RC's release version the event belongs to, e.g. v2.24.0",
    )
    p_start.add_argument(
        "source",
        help="what blocked the RC, e.g. halt:project, halt:global or task:<TASK-ID>",
    )
    p_start.add_argument(
        "timestamp",
        help="ISO-8601 time of the boundary, e.g. 2026-09-18T09:15:22+00:00",
    )

    p_end = subparsers.add_parser("end", help="Stamp a block-end event.")
    p_end.add_argument(
        "ledger_path",
        help="path to the project's blocked-intervals.jsonl ledger (created on first write)",
    )
    p_end.add_argument(
        "version",
        help="the RC's release version the event belongs to, e.g. v2.24.0",
    )
    p_end.add_argument(
        "source",
        help="what blocked the RC, e.g. halt:project, halt:global or task:<TASK-ID>",
    )
    p_end.add_argument(
        "timestamp",
        help="ISO-8601 time of the boundary, e.g. 2026-09-18T09:15:22+00:00",
    )

    p_read = subparsers.add_parser("read", help="Print the merged, clamped blocked-minutes total.")
    p_read.add_argument(
        "ledger_path",
        help="path to the project's blocked-intervals.jsonl ledger (absent reads as zero)",
    )
    p_read.add_argument("version", help="the RC's release version to total, e.g. v2.24.0")
    p_read.add_argument(
        "window_opened_at",
        help="ISO-8601 time the RC opened; intervals are clamped to start no earlier",
    )
    p_read.add_argument(
        "window_closed_at",
        help="ISO-8601 time the RC closed or shipped; intervals are clamped to end no later",
    )

    args = parser.parse_args()

    try:
        if args.command == "start":
            append_block_start(args.ledger_path, args.version, args.source, args.timestamp)
            sys.stdout.write(json.dumps({"written": True}) + "\n")
        elif args.command == "end":
            append_block_end(args.ledger_path, args.version, args.source, args.timestamp)
            sys.stdout.write(json.dumps({"written": True}) + "\n")
        elif args.command == "read":
            minutes = blocked_minutes(
                args.ledger_path, args.version, args.window_opened_at, args.window_closed_at
            )
            sys.stdout.write(json.dumps({"blocked_minutes": minutes}) + "\n")
    except LedgerError as exc:
        # CENSUS CLASS: CONVERT (docs/blocking-site-census.md) — a
        # malformed ledger never halts a release path; it speaks the
        # dual-channel contract and yields no number for the caller to
        # display instead.
        from pgai_agent_kanban.lib.error_contract import emit_error_contract

        # CENSUS-ANCHOR: blocked-ledger-01
        emit_error_contract(exc.error, exc.message, exc.remedy)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
