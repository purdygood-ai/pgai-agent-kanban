#!/usr/bin/env python3
"""
agent_timing.py — per-agent-role average wake-time metrics for the pgai
kanban system.

Parses wake log lines of the form:

    [<ISO8601>] wake(<agent>): project <project>: done: completed=N elapsed=Ns reason=...

Aggregates per-agent-role, per-project wake times and emits a plain-text
table with columns: agent_role, project, avg_seconds, wake_count,
total_elapsed_seconds.

Usage (CLI):
    python3 team/pgai_agent_kanban/reports/agent_timing.py [--days N] [--all-time]

Flags:
    --days N       Limit to wake log lines from the last N days (default: 7).
    --all-time     Include all wake log lines regardless of age.

Log discovery:
    Reads log files under $PGAI_AGENT_KANBAN_ROOT_PATH/logs/agents/ (defaults to
    ~/pgai_agent_kanban when the env var is not set).  All files matching
    *-batch-*.log are scanned.

Exit codes:
    0 — always (errors in parsing produce a "no data" message)
"""

from __future__ import annotations

import os
import pathlib
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import NamedTuple


# ---------------------------------------------------------------------------
# Log line pattern
# ---------------------------------------------------------------------------

# Matches lines of the form:
#   [2026-05-24T18:03:44+00:00] wake(cm): project pgai-agent-kanban: done: completed=1 elapsed=103s reason="..."
_LINE_RE = re.compile(
    r"^\[(?P<ts>[^\]]+)\]\s+"
    r"wake\((?P<agent>[^)]+)\):\s+"
    r"project\s+(?P<project>\S+):\s+"
    r"done:.*?elapsed=(?P<elapsed>\d+)s",
)


class WakeEntry(NamedTuple):
    """A single parsed wake-done log entry."""

    ts: datetime
    agent: str
    project: str
    elapsed_seconds: int


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_timestamp(ts_str: str) -> datetime | None:
    """Parse an ISO 8601 timestamp string into a timezone-aware datetime.

    Handles both '+00:00' and 'Z' suffix forms.  Returns None when the
    string cannot be parsed so callers can skip malformed lines gracefully.
    """
    ts_str = ts_str.strip()
    # Normalise 'Z' -> '+00:00' for Python < 3.11 which lacks fromisoformat('...Z')
    if ts_str.endswith("Z"):
        ts_str = ts_str[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(ts_str)
    except ValueError:
        return None


def parse_log_file(path: pathlib.Path, cutoff: datetime | None) -> list[WakeEntry]:
    """Parse one log file and return matching WakeEntry records.

    Args:
        path:    Path to a wake log file.
        cutoff:  Earliest timestamp to include, or None for all entries.

    Returns:
        List of WakeEntry objects matching the wake-done pattern and within
        the requested time window.
    """
    entries: list[WakeEntry] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return entries

    for line in text.splitlines():
        m = _LINE_RE.match(line)
        if m is None:
            continue
        ts = _parse_timestamp(m.group("ts"))
        if ts is None:
            continue
        if cutoff is not None and ts < cutoff:
            continue
        entries.append(
            WakeEntry(
                ts=ts,
                agent=m.group("agent").lower(),
                project=m.group("project"),
                elapsed_seconds=int(m.group("elapsed")),
            )
        )
    return entries


def collect_log_files(logs_dir: pathlib.Path) -> list[pathlib.Path]:
    """Return all agent batch log files under logs_dir/agents/.

    Looks for files matching the naming convention *-batch-*.log used by
    the wake script.  Falls back to scanning the top-level logs_dir when
    the agents/ subdirectory does not exist.
    """
    agents_dir = logs_dir / "agents"
    search_dirs = [agents_dir] if agents_dir.is_dir() else [logs_dir]

    files: list[pathlib.Path] = []
    for d in search_dirs:
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.suffix == ".log" and "-batch-" in f.name:
                files.append(f)
    return sorted(files)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


class RoleProjectKey(NamedTuple):
    """Composite key for per-role, per-project aggregation."""

    agent: str
    project: str


def aggregate(entries: list[WakeEntry]) -> dict[RoleProjectKey, list[int]]:
    """Group elapsed-seconds values by (agent, project).

    Returns a dict mapping each (agent, project) pair to a list of
    elapsed-second values seen in the log entries.
    """
    buckets: dict[RoleProjectKey, list[int]] = {}
    for e in entries:
        key = RoleProjectKey(agent=e.agent, project=e.project)
        buckets.setdefault(key, []).append(e.elapsed_seconds)
    return buckets


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------

_COL_WIDTHS = {
    "agent_role": 12,
    "project": 36,
    "avg_seconds": 12,
    "wake_count": 11,
    "total_elapsed_seconds": 22,
}


def _hr(widths: dict[str, int]) -> str:
    """Build a horizontal rule matching column widths."""
    parts = ["-" * w for w in widths.values()]
    return "+-" + "-+-".join(parts) + "-+"


def _row(values: list[str], widths: dict[str, int]) -> str:
    """Format one table row."""
    cells = []
    for val, (_, w) in zip(values, widths.items()):
        cells.append(val.ljust(w) if len(val) <= w else val[:w])
    return "| " + " | ".join(cells) + " |"


def render_table(
    buckets: dict[RoleProjectKey, list[int]],
    window_label: str,
) -> str:
    """Render aggregated wake-time data as a plain-text table.

    Args:
        buckets:      Output of aggregate().
        window_label: Human-readable description of the time window.

    Returns:
        Multi-line string ready to print.
    """
    lines: list[str] = []
    lines.append(f"Agent wake-time metrics  ({window_label})")
    lines.append("")

    if not buckets:
        lines.append("  no wake log data found for the requested window")
        return "\n".join(lines)

    widths = _COL_WIDTHS
    hr = _hr(widths)
    header = _row(list(widths.keys()), widths)

    lines.append(hr)
    lines.append(header)
    lines.append(hr)

    # Sort rows: agent ascending, then project ascending for a stable, readable layout
    for key in sorted(buckets.keys()):
        elapsed_list = buckets[key]
        wake_count = len(elapsed_list)
        total_elapsed = sum(elapsed_list)
        avg_seconds = total_elapsed / wake_count if wake_count else 0

        row = _row(
            [
                key.agent,
                key.project,
                f"{avg_seconds:.1f}",
                str(wake_count),
                str(total_elapsed),
            ],
            widths,
        )
        lines.append(row)

    lines.append(hr)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Log path discovery
# ---------------------------------------------------------------------------


def resolve_logs_dir() -> pathlib.Path:
    """Return the logs directory path based on env var or default location.

    Priority:
    1. $PGAI_AGENT_KANBAN_ROOT_PATH/logs/
    2. ~/pgai_agent_kanban/logs/

    """
    root_env = os.environ.get("PGAI_AGENT_KANBAN_ROOT_PATH", "").strip()
    if root_env:
        root = pathlib.Path(root_env).expanduser()
    else:
        root = pathlib.Path.home() / "pgai_agent_kanban"
    return root / "logs"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point: parse args, collect logs, aggregate, print table."""
    # --- Argument parsing (stdlib only — no argparse to keep imports minimal) ---
    days: int | None = 7
    all_time = False

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--all-time":
            all_time = True
            days = None
            i += 1
        elif arg == "--days":
            if i + 1 >= len(args):
                sys.stderr.write("ERROR: --days requires an integer argument\n")
                sys.exit(1)
            try:
                days = int(args[i + 1])
                if days < 1:
                    raise ValueError("days must be >= 1")
            except ValueError as exc:
                sys.stderr.write(f"ERROR: --days: {exc}\n")
                sys.exit(1)
            i += 2
        elif arg in ("-h", "--help"):
            # Print the module docstring as help text
            doc = __doc__ or ""
            sys.stdout.write(doc.lstrip("\n"))
            sys.exit(0)
        else:
            sys.stderr.write(f"ERROR: unknown argument: {arg}\n")
            sys.exit(1)

    # --- Time window ---
    now = datetime.now(tz=timezone.utc)
    if all_time:
        cutoff: datetime | None = None
        window_label = "all time"
    else:
        effective_days = days if days is not None else 7
        cutoff = now - timedelta(days=effective_days)
        window_label = f"last {effective_days} day{'s' if effective_days != 1 else ''}"

    # --- Discover and parse log files ---
    logs_dir = resolve_logs_dir()
    if not logs_dir.is_dir():
        sys.stderr.write(
            f"WARNING: logs directory not found: {logs_dir}\n"
            "  Set PGAI_AGENT_KANBAN_ROOT_PATH to the kanban install root.\n"
        )
        print(render_table({}, window_label))
        return

    log_files = collect_log_files(logs_dir)
    if not log_files:
        sys.stderr.write(f"WARNING: no agent batch log files found under {logs_dir}\n")
        print(render_table({}, window_label))
        return

    all_entries: list[WakeEntry] = []
    for lf in log_files:
        all_entries.extend(parse_log_file(lf, cutoff))

    buckets = aggregate(all_entries)
    print(render_table(buckets, window_label))


if __name__ == "__main__":
    main()
