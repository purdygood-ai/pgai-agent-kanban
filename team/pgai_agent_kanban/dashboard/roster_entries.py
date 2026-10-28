#!/usr/bin/env python3
"""
roster_entries.py — shared next-fire roster builder for the dashboard's
roster band renderer.

Wraps cron_parser.next_firings() into the ordered, per-entry structure the
roster band renderer (team/scripts/dashboard/lib/roster_band.sh) packs into
either a compact columnar band or, when the hosting pane is too narrow, a
vertical fallback list. Both next-fire homes -- next-cron-firings.sh's
schedule-window band and show-header.sh's window-0 tile -- go through the
same renderer, which goes through this same module, so the two never
disagree about roster content or order: "one renderer, two call sites, no
second copy of the fitting logic."

Roster order is preserved exactly as next_firings() yields it -- pm first,
then crontab first-seen order -- never re-sorted by remaining time. Only the
countdown/mark values change between refreshes; row identity and order do
not.

Usage (CLI):
    python3 team/pgai_agent_kanban/dashboard/roster_entries.py \\
        <crontab_text> <cron_parser_path> [<now_ts>]

    crontab_text      — raw crontab text (passed as a single positional arg)
    cron_parser_path  — absolute path to team/pm-agent/lib/cron_parser.py
    now_ts            — optional Unix timestamp (float) fixing "now" for all
                        per-entry computations in this render cycle; when
                        omitted, datetime.now() is used at call time.

Output, one line per roster entry, in roster order:
    INT <AGENT> <:MM> <M:SS>   — sub-hour entry: fire-mark + live countdown
    STR <AGENT> <label...>     — weekly/infrequent entry: day+time hint

<AGENT> is upper-cased (matches the pre-existing next-cron-firings.sh
convention); <label...> may itself contain spaces ("Thu 3am"), so CLI
consumers split on the first two fields only and treat the remainder as the
label.

Exit codes:
    0 — always. A parse failure or empty/unparseable crontab prints nothing
    (empty roster), matching cron_firings.py's degrade-empty contract.
"""

from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timedelta

# Run by file path (``python3 .../roster_entries.py``) or loaded from a file
# outside its package, this module has no parent package and the interpreter
# puts only its own directory on sys.path, so the package import below would
# not resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# import resolves from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib.cron_parser_loader import load_cron_parser as _load_cron_parser  # noqa: E402

# Entries with at least this many seconds remaining render as a day+time
# hint instead of a MM:SS countdown. Mirrors the pre-existing threshold in
# next-cron-firings.sh's embedded parser (never changed by this module).
FAR_FUTURE_THRESHOLD = 3600  # 1 hour

_DOW_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def _day_time_hint(now: datetime, secs_remaining: int) -> str:
    """Convert seconds-remaining to a 'DayName HHam/pm' hint string."""
    firing_dt = now + timedelta(seconds=secs_remaining)
    # Python weekday(): Mon=0..Sun=6 -> cron: Sun=0..Sat=6
    cron_dow = (firing_dt.weekday() + 1) % 7
    dow_name = _DOW_NAMES[cron_dow]
    hour = firing_dt.hour
    if hour == 0:
        hour_str = "12am"
    elif hour < 12:
        hour_str = f"{hour}am"
    elif hour == 12:
        hour_str = "12pm"
    else:
        hour_str = f"{hour - 12}pm"
    return f"{dow_name} {hour_str}"


def _fire_mark(now: datetime, secs_remaining: int) -> str:
    """The ':MM' fire mark -- the minute-of-hour the entry next fires at,
    derived from the same firing instant the countdown counts down to (never
    a second, independently-parsed cron field), so mark and countdown can
    never disagree."""
    firing_dt = now + timedelta(seconds=secs_remaining)
    return f":{firing_dt.minute:02d}"


def _format_remaining(secs: int) -> str:
    """Format seconds as M:SS (e.g. 102 -> '1:42')."""
    s = int(secs)
    m = s // 60
    ss = s % 60
    return f"{m}:{ss:02d}"


def roster_entries(
    crontab_text: str,
    cron_parser_path: str,
    now_ts: "float | None" = None,
) -> list:
    """Return the ordered roster as a list of entry dicts.

    Each dict has key "agent" (upper-cased) and "kind" ("INT" or "STR"), plus:
      - kind == "INT": "mark" (":MM") and "countdown" ("M:SS")
      - kind == "STR": "label" (e.g. "Thu 3am")

    Roster order matches next_firings()'s own dict order (pm first, then
    crontab first-seen order) -- never re-sorted here.

    Returns [] when crontab_text is empty, the parser path does not exist,
    or parsing raises (degrades empty, never raises to the caller).
    """
    if not crontab_text:
        return []

    parser_path = pathlib.Path(cron_parser_path)
    if not parser_path.is_file():
        return []

    now = datetime.fromtimestamp(now_ts) if now_ts is not None else datetime.now()

    try:
        mod = _load_cron_parser(parser_path)
        firings = mod.next_firings(crontab_text, now=now)
    except Exception:  # noqa: BLE001 — must not raise; dashboard degrades gracefully
        firings = {}

    entries = []
    for agent, val in firings.items():
        if isinstance(val, int):
            if val >= FAR_FUTURE_THRESHOLD:
                entries.append(
                    {
                        "agent": agent.upper(),
                        "kind": "STR",
                        "label": _day_time_hint(now, val),
                    }
                )
            else:
                entries.append(
                    {
                        "agent": agent.upper(),
                        "kind": "INT",
                        "mark": _fire_mark(now, val),
                        "countdown": _format_remaining(val),
                    }
                )
        else:
            entries.append({"agent": agent.upper(), "kind": "STR", "label": str(val)})

    return entries


def main() -> None:
    """CLI entry point: read args, call roster_entries(), print roster lines."""
    if len(sys.argv) < 3:
        sys.stderr.write(
            "Usage: roster_entries.py <crontab_text> <cron_parser_path> [<now_ts>]\n"
        )
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
        entries = roster_entries(crontab_text, cron_parser_path, now_ts)
    except Exception:  # noqa: BLE001 — must not raise; dashboard degrades gracefully
        entries = []

    for entry in entries:
        if entry["kind"] == "INT":
            print(f"INT {entry['agent']} {entry['mark']} {entry['countdown']}")
        else:
            print(f"STR {entry['agent']} {entry['label']}")


if __name__ == "__main__":
    main()
