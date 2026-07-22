#!/usr/bin/env python3
"""
metrics_csv_writer.py -- Append-only CSV writer for cumulative RC metrics history.

Implements the cumulative CSV format specified in
# provenance-allowlist: remediation-pending — cited ID belongs in commit history; remove when rewriting comment
PRIORITY-0037-20260517-metrics-canonicalization-and-rollup.md.

OUTPUT FORMAT
=============

File location: projects/<name>/metrics/history.csv

Columns (in order):
  rc, project, workflow_type, opened_at, closed_at, wall_time_minutes,
  outcome, tasks_total, tasks_pm, tasks_coder, tasks_writer, tasks_tester,
  tasks_cm, input_tokens, output_tokens, cache_read_tokens,
  cache_write_tokens, cache_hit_rate_pct, bugs_filed_during, operator_waivers

The file is created with the header row on first call.  Subsequent calls
append one data row without re-writing the header.

ATOMIC WRITE / CONCURRENCY
===========================

Concurrent RC closes (e.g. two CM-release.sh invocations racing) can both
call append_rc_row().  To prevent torn or interleaved rows:

  1. The history.csv file is opened with ``open(..., 'a')``.
  2. ``fcntl.flock(fd, LOCK_EX)`` acquires an exclusive advisory lock.
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
          # provenance-allowlist: remediation-pending — cited ID belongs in commit history; remove when rewriting comment
          "bugs_filed_during_verification": ["BUG-0099"],
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
import json
import os
import pathlib
import sys
from typing import Any

# ---------------------------------------------------------------------------
# Column specification — public contract.
# Do NOT change column order or names without a migration plan.
# ---------------------------------------------------------------------------
HISTORY_COLUMNS: list[str] = [
    "rc",
    "project",
    "workflow_type",
    "opened_at",
    "closed_at",
    "wall_time_minutes",
    "outcome",
    "tasks_total",
    "tasks_pm",
    "tasks_coder",
    "tasks_writer",
    "tasks_tester",
    "tasks_cm",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cache_hit_rate_pct",
    "bugs_filed_during",
    "operator_waivers",
]


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
    }


# ---------------------------------------------------------------------------
# Core: append_rc_row
# ---------------------------------------------------------------------------


def append_rc_row(
    csv_path: pathlib.Path | str,
    rollup: dict[str, Any],
) -> bool:
    """Append one row to the cumulative history CSV for an RC close.

    Creates the file (with header) on first call.  All subsequent calls
    append a data row without re-writing the header.

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

    # Open in append mode (creates the file if it does not exist).
    with open(csv_path, "a", newline="", encoding="utf-8") as fh:
        # Acquire exclusive lock — blocks until any concurrent holder releases.
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            # Re-read the current file size to decide if it is empty.
            fh.flush()
            file_size = fh.seek(0, os.SEEK_END)

            # --------------- Duplicate detection (TOCTOU-safe) ---------------
            # Read the file from the beginning to check for an existing row
            # for this RC.  We do this inside the lock so no other process can
            # have appended between our open() and now.
            if file_size > 0:
                with open(csv_path, "r", newline="", encoding="utf-8") as rfh:
                    reader = csv.DictReader(rfh)
                    for existing in reader:
                        if existing.get("rc") == rc:
                            print(
                                f"[metrics_csv_writer] WARNING: RC '{rc}' already "
                                f"present in {csv_path}; skipping duplicate append.",
                                file=sys.stderr,
                            )
                            return False

            # --------------- Write header if file is empty -------------------
            writer = csv.DictWriter(
                fh,
                fieldnames=HISTORY_COLUMNS,
                lineterminator="\n",
                extrasaction="ignore",
            )
            if file_size == 0:
                writer.writeheader()

            # --------------- Append data row ---------------------------------
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
    parser = argparse.ArgumentParser(
        description=(
            "Append one RC row to the cumulative metrics history CSV.\n"
            # provenance-allowlist: remediation-pending — cited ID belongs in commit history; remove when rewriting comment
            "Implements the append-only format specified in PRIORITY-0037."
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
