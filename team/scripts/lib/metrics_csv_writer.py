#!/usr/bin/env python3
"""
metrics_csv_writer.py -- Append-only CSV writer for cumulative RC metrics history.

Implements the append-only, cumulative CSV rollup format for RC metrics
history: one fixed-column row per RC, appended in place. Rows are never
rewritten; the header alone is rewritten once when it predates a column
(see HEADER MIGRATION).

OUTPUT FORMAT
=============

File location: projects/<name>/metrics/history.csv

Columns (in order) -- HISTORY_COLUMNS, declared once in
pgai_agent_kanban.lib.metrics.history (the shared history.csv reader) and
imported here:
  rc, project, workflow_type, opened_at, closed_at, wall_time_minutes,
  outcome, tasks_total, tasks_pm, tasks_coder, tasks_writer, tasks_tester,
  tasks_cm, input_tokens, output_tokens, cache_read_tokens,
  cache_write_tokens, cache_hit_rate_pct, bugs_filed_during,
  operator_waivers, blocked_minutes

blocked_minutes is the RC's blocked-interval total from the rollup's own
"blocked_minutes" field (team/scripts/lib/metrics_aggregator.py -- see
that module's "BLOCKED MINUTES" docstring section for how it is computed;
this writer never reads the ledger itself). It sits LAST, added after the
other columns were already a public contract, so every existing
positional reader keeps reading the same fields at the same offsets. A
rollup with a real zero (no ledger, or an empty one) writes 0; a rollup
whose ledger could not be read, or whose value is otherwise unknown
(rollup["blocked_minutes"] is None or the key is missing), writes an
empty cell and prints a warning to stderr -- the write itself still
succeeds and the call still returns normally.

The file is created with the header row on first call.  Subsequent calls
append one data row in HISTORY_COLUMNS order.

HEADER MIGRATION
================

Columns are only ever added at the end of HISTORY_COLUMNS, so a file
created before a column existed has a header that is a strict prefix of
HISTORY_COLUMNS while the rows appended since carry the newer fields.
Before appending, the writer compares the file's header with
HISTORY_COLUMNS:

  * Equal -- the row is appended.
  * A strict prefix -- the header line is replaced by the full
    HISTORY_COLUMNS header and the new row is added, in one atomic rewrite
    (pgai_agent_kanban.lib.atomic_write: same-directory temp file, fsync,
    os.replace) that keeps the file's permission bits. Every existing row
    is kept byte for byte: a row written before a column existed stays
    short and reads that column as missing, never as 0. One stderr line
    names the columns added.
  * Anything else (reordered, renamed, empty, or longer than
    HISTORY_COLUMNS) -- a warning on stderr names the header found and the
    header expected, and the row is still appended in HISTORY_COLUMNS
    order. A header mismatch never fails the append or the release.

Until a file is migrated, the shared reader
(pgai_agent_kanban.lib.metrics.history.read_history_csv) already reads its
surplus trailing fields under their HISTORY_COLUMNS names.

ATOMIC WRITE / CONCURRENCY
===========================

Concurrent RC closes (e.g. two CM-release.sh invocations racing) can both
call append_rc_row().  To prevent torn or interleaved rows:

  1. The history.csv file is opened with ``open(..., 'a')``.
  2. ``fcntl.flock(fd, LOCK_EX)`` acquires an exclusive advisory lock.
     A header migration replaces the file with a new one, so after taking
     the lock the writer checks that its descriptor still names the file
     at csv_path; if not, it reopens and locks again. A caller that waited
     on the replaced file therefore appends to the migrated one.
  3. We re-read the file inside the lock to check for duplicate RC rows
     (TOCTOU-safe: the check and append are performed while holding the lock).
  4. The row is written and flushed before the lock is released.
  5. ``fcntl.flock(fd, LOCK_UN)`` releases the lock (also released on close).

This guarantees that concurrent callers serialize at the OS level and that
each produces exactly one row in the file.

IDEMPOTENCY
===========

If a row whose first field matches the ``rc`` argument already exists in
history.csv, append_rc_row() logs a warning to stderr and returns without
writing a duplicate.  The check is performed inside the exclusive lock to
prevent TOCTOU races.

CACHE HIT RATE
==============

cache_hit_rate_pct is defined as:
  cache_read_tokens / (input_tokens + cache_read_tokens) * 100

rounded to one decimal place.  Both input_tokens and cache_read_tokens are
taken from the rollup dict.  If the denominator is zero the field is 0.0.

USAGE (as module)
=================

  from metrics_csv_writer import append_rc_row

  append_rc_row(
      csv_path=pathlib.Path("projects/pgai-agent-kanban/metrics/history.csv"),
      rollup={
          "rc":               "v0.24.7",
          "project":          "pgai-agent-kanban",
          "workflow_type":    "release",
          "opened_at":        "2026-05-17T21:04:37Z",   # or None
          "closed_at":        "2026-05-17T22:30:15Z",   # or None
          "wall_time_minutes": 86,                       # or None
          "blocked_minutes":  0,                         # real 0, or None if unreadable
          "outcome":          "SHIPPED",
          "tasks": {
              "total": 8,
              "by_agent": {
                  "pm": 1, "coder": 3, "writer": 1, "tester": 1, "cm": 2
              },
          },
          "tokens": {
              "total": {
                  "input":       16234,
                  "output":      58234,
                  "cache_read":  9876543,
                  "cache_write": 945678,
              },
          },
          "bugs_filed_during_verification": ["BUG-NNNN"],
          "operator_interventions":         [],
      },
  )

USAGE (CLI)
===========

  python3 metrics_csv_writer.py \\
      --csv-path projects/pgai-agent-kanban/metrics/history.csv \\
      --rollup-json path/to/rc_rollup.json

  Exit codes:
    0 -- row appended (or skipped as duplicate)
    1 -- usage error, missing input, or unrecoverable I/O failure
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import io
import json
import os
import pathlib
import sys
from typing import IO, Any

# The column contract and the atomic file writer live in the
# pgai_agent_kanban package. This module runs from within the full kanban
# tree (dev tree or live install), where pgai_agent_kanban/ is always a
# sibling of scripts/, so it locates the package from its own path.
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent  # lib/ -> scripts/ -> team/
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))
from pgai_agent_kanban.lib.atomic_write import atomic_write  # noqa: E402
from pgai_agent_kanban.lib.metrics.history import (  # noqa: E402
    HISTORY_COLUMNS as _HISTORY_COLUMNS,
    is_short_history_header,
)

# ---------------------------------------------------------------------------
# Column specification — public contract, declared once in
# pgai_agent_kanban.lib.metrics.history and re-exported here as a list.
# ---------------------------------------------------------------------------
HISTORY_COLUMNS: list[str] = list(_HISTORY_COLUMNS)

#: Scratch-file prefix for a header migration's atomic rewrite, so a file
#: left by a killed process names its writer.
_MIGRATION_TMP_PREFIX = ".metrics_csv_writer_tmp_"


# ---------------------------------------------------------------------------
# Cache hit rate calculation
# ---------------------------------------------------------------------------


def _cache_hit_rate(input_tokens: int, cache_read_tokens: int) -> float:
    """Return cache_hit_rate_pct = cache_read / (input + cache_read) * 100.

    Returns 0.0 when the denominator is zero (no tokens at all).
    """
    denominator = input_tokens + cache_read_tokens
    if denominator == 0:
        return 0.0
    return round(cache_read_tokens / denominator * 100, 1)


# ---------------------------------------------------------------------------
# Rollup dict -> CSV row extractor
# ---------------------------------------------------------------------------


def _rollup_to_row(rollup: dict[str, Any]) -> dict[str, Any]:
    """Extract a flat CSV row dict from a per-RC rollup dict.

    Missing fields default to safe sentinel values (empty string or 0).
    """
    tasks: dict[str, Any] = rollup.get("tasks") or {}
    by_agent: dict[str, Any] = tasks.get("by_agent") or {}

    tokens: dict[str, Any] = rollup.get("tokens") or {}
    total_tokens: dict[str, Any] = tokens.get("total") or {}

    input_tokens:       int = int(total_tokens.get("input",       0) or 0)
    output_tokens:      int = int(total_tokens.get("output",      0) or 0)
    cache_read_tokens:  int = int(total_tokens.get("cache_read",  0) or 0)
    cache_write_tokens: int = int(total_tokens.get("cache_write", 0) or 0)

    cache_hit_rate = _cache_hit_rate(input_tokens, cache_read_tokens)

    # bugs_filed_during: count of items in bugs_filed_during_verification list
    bugs_list = rollup.get("bugs_filed_during_verification") or []
    bugs_filed_during = len(bugs_list) if isinstance(bugs_list, list) else 0

    # operator_waivers: count of items in operator_interventions list
    waivers_list = rollup.get("operator_interventions") or []
    operator_waivers = len(waivers_list) if isinstance(waivers_list, list) else 0

    wall_time = rollup.get("wall_time_minutes")
    if wall_time is None:
        wall_time = ""

    # blocked_minutes: written LAST (see HISTORY_COLUMNS). A present
    # numeric value -- including a real 0 for "no ledger" -- is written
    # as-is. A rollup that never got one (a key missing entirely, e.g. an
    # older rollup) or that recorded an explicit None (the ledger existed
    # but could not be read -- see metrics_aggregator.py's "BLOCKED
    # MINUTES" section) both degrade to an empty cell with a warning:
    # this writer never fabricates a number it was not given.
    blocked_minutes = rollup.get("blocked_minutes")
    if blocked_minutes is None:
        print(
            f"[metrics_csv_writer] WARNING: RC {rollup.get('rc', '')!r} has no "
            "usable blocked_minutes value (ledger unreadable or not yet "
            "computed); leaving the blocked_minutes cell empty.",
            file=sys.stderr,
        )
        blocked_minutes = ""

    return {
        "rc":                 rollup.get("rc", ""),
        "project":            rollup.get("project", ""),
        "workflow_type":      rollup.get("workflow_type", "release"),
        "opened_at":          rollup.get("opened_at") or "",
        "closed_at":          rollup.get("closed_at") or "",
        "wall_time_minutes":  wall_time,
        "outcome":            rollup.get("outcome", "UNKNOWN"),
        "tasks_total":        int(tasks.get("total", 0) or 0),
        "tasks_pm":           int(by_agent.get("pm", 0) or 0),
        "tasks_coder":        int(by_agent.get("coder", 0) or 0),
        "tasks_writer":       int(by_agent.get("writer", 0) or 0),
        "tasks_tester":       int(by_agent.get("tester", 0) or 0),
        "tasks_cm":           int(by_agent.get("cm", 0) or 0),
        "input_tokens":       input_tokens,
        "output_tokens":      output_tokens,
        "cache_read_tokens":  cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "cache_hit_rate_pct": cache_hit_rate,
        "bugs_filed_during":  bugs_filed_during,
        "operator_waivers":   operator_waivers,
        "blocked_minutes":    blocked_minutes,
    }


# ---------------------------------------------------------------------------
# Locking and header handling
# ---------------------------------------------------------------------------


def _open_locked_for_append(csv_path: pathlib.Path) -> IO[str]:
    """Open *csv_path* for append and hold an exclusive flock on it.

    A header migration replaces the file (os.replace), so a lock taken on
    the descriptor of the replaced file guards nothing. After the lock is
    granted the descriptor is compared with the file now at *csv_path*;
    on a mismatch it is closed and the open-and-lock is retried.
    """
    while True:
        fh = open(csv_path, "a", newline="", encoding="utf-8")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            held = os.fstat(fh.fileno())
            try:
                current = os.stat(csv_path)
            except FileNotFoundError:
                current = None
            if current is not None and (held.st_dev, held.st_ino) == (current.st_dev, current.st_ino):
                return fh
        except BaseException:
            fh.close()
            raise
        fh.close()


def _split_header(text: str) -> tuple[str, list[str], str]:
    """Split history.csv *text* into (header line, header names, body).

    The header line keeps its own line ending; the body is everything
    after it, unchanged.
    """
    newline = text.find("\n")
    header_line = text if newline < 0 else text[: newline + 1]
    body = "" if newline < 0 else text[newline + 1:]
    names = next(csv.reader(io.StringIO(header_line)), [])
    return header_line, names, body


def _render_line(fieldnames: list[str], row: dict[str, Any] | None = None) -> str:
    """One CSV line: the header for *fieldnames*, or *row* in that order."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n", extrasaction="ignore")
    if row is None:
        writer.writeheader()
    else:
        writer.writerow(row)
    return buf.getvalue()


def _migrate_header_and_append(
    csv_path: pathlib.Path, header_names: list[str], body: str, row_dict: dict[str, Any]
) -> None:
    """Rewrite a short header to the full HISTORY_COLUMNS and add *row_dict*.

    One atomic rewrite (module docstring, HEADER MIGRATION): the new header,
    every existing row byte for byte, then the new row. The file keeps its
    permission bits. The caller holds the append lock.
    """
    if body and not body.endswith("\n"):
        body += "\n"
    content = _render_line(HISTORY_COLUMNS) + body + _render_line(HISTORY_COLUMNS, row_dict)
    atomic_write(csv_path, content, prefix=_MIGRATION_TMP_PREFIX, preserve_mode=True)
    added = HISTORY_COLUMNS[len(header_names):]
    print(
        f"[metrics_csv_writer] Header of {csv_path} predated {len(added)} "
        f"column(s); rewrote it with the full column list (added: "
        f"{', '.join(added)}). Existing rows are unchanged.",
        file=sys.stderr,
    )


def _warn_unmigratable_header(csv_path: pathlib.Path, header_names: list[str]) -> None:
    """Narrate a header that is neither HISTORY_COLUMNS nor a prefix of it.

    CONVERT: an unexpected header is a quality finding, never a refusal --
    the caller still appends the row and the release is never failed.
    """
    found = ",".join(header_names) if header_names else "(empty)"
    print(
        f"[metrics_csv_writer] WARNING: the header of {csv_path} is not the "
        f"history.csv column list or a prefix of it, so it was left as is; "
        f"the row is appended in the standard column order and readers keyed "
        f"by this header may misread it. Found: {found}. Expected: "
        f"{','.join(HISTORY_COLUMNS)}. To repair, back up the file and replace "
        f"its first line with the expected header, reordering existing rows "
        f"to match if their columns differ.",
        file=sys.stderr,
    )


# ---------------------------------------------------------------------------
# Core: append_rc_row
# ---------------------------------------------------------------------------


def append_rc_row(
    csv_path: pathlib.Path | str,
    rollup: dict[str, Any],
) -> bool:
    """Append one row to the cumulative history CSV for an RC close.

    Creates the file (with header) on first call.  Subsequent calls append
    a data row; when the existing header is a strict prefix of
    HISTORY_COLUMNS it is first widened to the full column list in the same
    atomic rewrite that adds the row, keeping the file's mode. Any other
    header mismatch prints a warning and the row is still appended (module
    docstring, HEADER MIGRATION).

    The call is atomic under concurrent writers: an exclusive advisory file
    lock (fcntl.flock LOCK_EX) is held for the duration of the check-and-
    append operation, preventing torn rows or duplicate writes from races.

    Args:
        csv_path: Absolute or relative path to history.csv.  The parent
                  directory is created if it does not exist.
        rollup:   Per-RC rollup dict matching the schema produced by
                  metrics_aggregator.aggregate_rc().

    Returns:
        True  -- row was appended successfully.
        False -- row was skipped because the RC already exists (duplicate).

    Raises:
        OSError   -- on unrecoverable I/O failure (permissions, disk full …).
        KeyError  -- if rollup is missing the 'rc' key entirely.
    """
    csv_path = pathlib.Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    rc: str = rollup["rc"]  # KeyError if absent; caller must supply it
    row_dict = _rollup_to_row(rollup)

    # Open in append mode (creates the file if it does not exist) and take
    # the exclusive lock -- blocks until any concurrent holder releases.
    fh = _open_locked_for_append(csv_path)
    with fh:
        try:
            # Re-read the current file size to decide if it is empty.
            fh.flush()
            file_size = fh.seek(0, os.SEEK_END)

            header_names: list[str] = []
            body = ""
            if file_size > 0:
                with open(csv_path, "r", newline="", encoding="utf-8") as rfh:
                    existing_text = rfh.read()
                _header_line, header_names, body = _split_header(existing_text)

                # ------------- Duplicate detection (TOCTOU-safe) -------------
                # Read inside the lock so no other process can have appended
                # between our open() and now.
                for existing in csv.DictReader(io.StringIO(existing_text)):
                    if existing.get("rc") == rc:
                        print(
                            f"[metrics_csv_writer] WARNING: RC '{rc}' already "
                            f"present in {csv_path}; skipping duplicate append.",
                            file=sys.stderr,
                        )
                        return False

            # --------------- Header: write, migrate, or narrate --------------
            if file_size > 0 and is_short_history_header(header_names):
                _migrate_header_and_append(csv_path, header_names, body, row_dict)
            else:
                if file_size > 0 and [n.strip() for n in header_names] != HISTORY_COLUMNS:
                    _warn_unmigratable_header(csv_path, header_names)
                writer = csv.DictWriter(
                    fh,
                    fieldnames=HISTORY_COLUMNS,
                    lineterminator="\n",
                    extrasaction="ignore",
                )
                if file_size == 0:
                    writer.writeheader()

                # ------------- Append data row -------------------------------
                writer.writerow(row_dict)
                fh.flush()
                os.fsync(fh.fileno())

        finally:
            # Lock is released when fh is closed (end of 'with' block), but
            # explicit unlock here keeps the intent clear.
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    print(
        f"[metrics_csv_writer] Row appended for RC '{rc}' to {csv_path}.",
        file=sys.stderr,
    )
    return True


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the history CSV writer: append one RC row from a roll-up JSON.

    ``--csv-path PATH --rollup-json PATH``. Exit codes: 0 the row was appended
    or skipped as a duplicate; 1 missing input or an unrecoverable I/O
    failure; 2 usage error (argparse).
    """
    parser = argparse.ArgumentParser(
        description=(
            "Append one RC row to the cumulative metrics history CSV.\n"
            "Rows are appended in place and never rewritten. A header that\n"
            "predates newer columns is widened to the full column list in\n"
            "the same atomic rewrite (file mode kept); any other header\n"
            "mismatch prints a warning and the row is still appended."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--csv-path",
        required=True,
        metavar="PATH",
        help="Absolute or relative path to history.csv.",
    )
    parser.add_argument(
        "--rollup-json",
        required=True,
        metavar="FILE",
        help=(
            "Path to a per-RC rollup JSON file produced by metrics_aggregator.py "
            "(projects/<name>/metrics/rc/<v>.json)."
        ),
    )

    args = parser.parse_args()

    rollup_path = pathlib.Path(args.rollup_json)
    if not rollup_path.is_file():
        print(f"ERROR: rollup JSON not found: {rollup_path}", file=sys.stderr)
        sys.exit(1)

    try:
        rollup: dict[str, Any] = json.loads(
            rollup_path.read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: cannot read rollup JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(rollup, dict):
        print("ERROR: rollup JSON top-level value must be a JSON object.", file=sys.stderr)
        sys.exit(1)

    if "rc" not in rollup:
        print("ERROR: rollup JSON is missing the 'rc' field.", file=sys.stderr)
        sys.exit(1)

    csv_path = pathlib.Path(args.csv_path)
    try:
        append_rc_row(csv_path, rollup)
    except OSError as exc:
        print(f"ERROR: I/O failure writing {csv_path}: {exc}", file=sys.stderr)
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
