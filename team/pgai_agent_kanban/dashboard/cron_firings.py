#!/usr/bin/env python3
"""
cron_firings.py — CLI wrapper around cron_parser.next_firings() for the
pgai kanban dashboard.

This module replaces the inline Python heredoc blocks previously embedded in
team/scripts/dashboard/show-header.sh and show-status-window.sh.  The output
format and conversion logic are preserved exactly — do NOT change label
thresholds (<=0 → "now", ==1 → "in 1 min", >1 → "in N min") without filing a
separate bug.

Usage (CLI):
    python3 team/pgai_agent_kanban/dashboard/cron_firings.py \\
        <crontab_text> <cron_parser_path> [<now_ts>]

    crontab_text      — raw crontab text (passed as a single positional arg)
    cron_parser_path  — absolute path to team/pm-agent/lib/cron_parser.py
    now_ts            — optional Unix timestamp (float) to fix the reference
                        "now" for all cron-firing computations; when omitted,
                        datetime.now() is used at call time.

Output:
    JSON object mapping agent name to human-readable firing label.
    E.g.: {"pm": "in 6 min", "coder": "in 1 min", "cleanup": "Sun 4am"}

Exit codes:
    0 — always (errors in parsing produce an empty JSON object {})

Notes:
    - cron_parser.py is loaded via importlib so the caller does not need it on
      sys.path; only the file path is required.
    - next_firings() returns int seconds until next firing for regular schedules
      and a str sentinel (e.g. "Sun 4am") for weekly schedules.
    - The seconds-to-label conversion uses whole minutes (val // 60) matching
      the original heredoc behaviour.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from datetime import datetime


def _load_cron_parser(parser_path: pathlib.Path):
    """Load cron_parser module from an absolute file path via importlib."""
    spec = importlib.util.spec_from_file_location("cron_parser", parser_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seconds_to_label(val: int) -> str:
    """Convert seconds until next firing to a human-readable label.

    Matches the conversion used in the original heredoc blocks:
      <= 0  → "now"
      == 1 min  → "in 1 min"
      > 1 min   → "in N min"
    """
    minutes = val // 60
    if minutes <= 0:
        return "now"
    if minutes == 1:
        return "in 1 min"
    return f"in {minutes} min"


def cron_firings(
    crontab_text: str,
    cron_parser_path: str,
    now_ts: "float | None" = None,
) -> dict:
    """Return agent -> human-label mapping for next cron firings.

    Args:
        crontab_text:      Raw crontab text.
        cron_parser_path:  Absolute path to cron_parser.py.
        now_ts:            Optional Unix timestamp (float) to fix "now".
                           When None, datetime.now() is used.

    Returns:
        dict mapping agent name (str) to firing label (str).
        Empty dict when crontab_text is empty or parsing fails.
    """
    if not crontab_text:
        return {}

    parser_path = pathlib.Path(cron_parser_path)
    if not parser_path.is_file():
        return {}

    now: "datetime | None" = None
    if now_ts is not None:
        now = datetime.fromtimestamp(now_ts)

    mod = _load_cron_parser(parser_path)

    # next_firings returns int seconds for regular schedules, str sentinel
    # for weekly schedules (e.g. "Sun 4am").
    raw_result = mod.next_firings(crontab_text, now=now)

    output: dict[str, str] = {}
    for agent, val in raw_result.items():
        if isinstance(val, int):
            output[agent] = _seconds_to_label(val)
        else:
            output[agent] = str(val)

    return output


def main() -> None:
    """CLI entry point: read args, call cron_firings(), print JSON."""
    if len(sys.argv) < 3:
        sys.stderr.write(
            "Usage: cron_firings.py <crontab_text> <cron_parser_path> [<now_ts>]\n"
        )
        print(json.dumps({}))
        return

    crontab_text = sys.argv[1]
    cron_parser_path = sys.argv[2]
    now_ts: "float | None" = None
    if len(sys.argv) > 3 and sys.argv[3]:
        try:
            now_ts = float(sys.argv[3])
        except ValueError:
            pass

    try:
        result = cron_firings(crontab_text, cron_parser_path, now_ts)
    except Exception:  # noqa: BLE001 — must not raise; dashboard degrades gracefully
        result = {}

    print(json.dumps(result))


if __name__ == "__main__":
    main()
