#!/usr/bin/env python3
"""
cron_firings.py — CLI wrapper around cron_parser.next_firings() for the
pgai kanban dashboard.

The dashboard scripts read their next-firing labels from this module.  The output format and conversion logic are a contract
— do NOT change label thresholds (<=0 → "now", ==1 → "in 1 min", >1 → "in N min") without filing a
separate bug.

Usage (CLI):
    python3 team/pgai_agent_kanban/dashboard/cron_firings.py \\
        <crontab_text> <cron_parser_path> [<now_ts>]

    crontab_text      — raw crontab text (passed as a single positional arg)
    cron_parser_path  — absolute path to team/pm-agent/lib/cron_parser.py
    now_ts            — optional Unix timestamp (float) to fix the reference
                        "now" for all cron-firing computations; when omitted,
                        datetime.now() is used at call time.

    python3 -m pgai_agent_kanban.dashboard.cron_firings --count \\
        <crontab_text> <cron_parser_path>

    python3 -m pgai_agent_kanban.dashboard.cron_firings --key-value <json_text>

    python3 -m pgai_agent_kanban.dashboard.cron_firings --line \\
        <crontab_text> <cron_parser_path>

Output:
    JSON object mapping agent name to human-readable firing label.
    E.g.: {"pm": "in 6 min", "coder": "in 1 min", "cleanup": "Sun 4am"}

    With --count: the number of agents the crontab resolves to (the size of
    that mapping), the roster the dashboard sizes its schedule panes for;
    0 when the crontab is empty or the parser fails or is missing.

    With --key-value: the JSON object a plain run printed, flattened to one
    ``agent=label`` line per entry in the object's order (see
    ``flatten_firings_json``), the form show-status-window.sh reads into its
    firing table.  An empty object prints nothing.

    With --line: the multi-project header's "Next cron firings" entries as
    one line (see ``next_firings_line``), the form show-multi.sh prints
    after its own label; an empty line when the crontab has no agent
    entries.

Exit codes:
    0 — always (errors in parsing produce an empty JSON object {}), except
        --key-value given text that is not a JSON object: it prints nothing
        on stdout, one line on stderr, and exits 1; and --line, which exits
        1 with nothing on stdout and one line on stderr when the parser
        cannot be loaded or raises, so the caller prints its own fallback.

Notes:
    - cron_parser.py is loaded via importlib so the caller does not need it on
      sys.path; only the file path is required.
    - next_firings() returns int seconds until next firing for regular schedules
      and a str sentinel (e.g. "Sun 4am") for weekly schedules.
    - The seconds-to-label conversion uses whole minutes (val // 60).
"""

from __future__ import annotations

import json
import pathlib
import sys
from datetime import datetime

# Run by file path (``python3 .../cron_firings.py``) or loaded from a file
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


def _seconds_to_label(val: int) -> str:
    """Convert seconds until next firing to a human-readable label.

    Conversion:
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
    now_ts: float | None = None,
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

    now: datetime | None = None
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


def flatten_firings_json(json_text: str) -> list[str]:
    """Flatten a firing-label JSON object into ``key=value`` lines.

    Each entry becomes one ``f"{key}={value}"`` line, in the object's own
    order, so a non-string value renders as Python's ``str()`` of it.  An
    empty object yields no lines.

    Args:
        json_text: The JSON object text a plain run of this module prints,
                   e.g. ``{"pm": "in 6 min", "cleanup": "Sun 4am"}``.

    Returns:
        The lines, without trailing newlines.

    Raises:
        ValueError: *json_text* is not valid JSON (``json.JSONDecodeError``
                    is a ``ValueError``) or holds something other than an
                    object.
    """
    data = json.loads(json_text)
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object, got {type(data).__name__}")
    return [f"{key}={value}" for key, value in data.items()]


def _line_label(val: object) -> str:
    """Label one parser value for the multi-project firing line.

    An integer is the seconds until the firing, as ``next_firings()``
    returns it, and gets the same whole-minute label the single-project
    panes print (see ``_seconds_to_label``): under a minute is "now", 60-119
    seconds is "in 1 min", and N whole minutes is "in N min" — 360 seconds
    reads "in 6 min".  Any other value (a weekly sentinel such as
    "Sun 4am") is its ``str()``.
    """
    if isinstance(val, int):
        return _seconds_to_label(val)
    return str(val)


def next_firings_line(
    crontab_text: str,
    cron_parser_path: str,
    now: datetime | None = None,
) -> str:
    """Return the multi-project header's next-firing entries as one line.

    Each agent ``next_firings()`` finds in *crontab_text* becomes
    ``"<agent>: <label>"`` (see ``_line_label``), in crontab line order,
    joined by three spaces; an agent whose value is None is left out.  A
    crontab with no agent entries gives "".

    Args:
        crontab_text:      Raw crontab text.
        cron_parser_path:  Path to cron_parser.py, loaded by path.
        now:               Reference time for the parser; the current time
                           when None.

    Raises:
        Whatever loading or running the parser raises (a missing file, a
        parser error); the caller owns the fallback.
    """
    mod = _load_cron_parser(pathlib.Path(cron_parser_path))
    result = mod.next_firings(crontab_text, now=now)
    # Roster comes from the schedule source itself: iterating result.items()
    # keeps crontab line order, never a literal agent tuple that can go
    # stale as the installed schedule grows.
    parts = [f"{agent}: {_line_label(val)}" for agent, val in result.items() if val is not None]
    return "   ".join(parts)


def _print_line(argv: list[str]) -> int:
    """Run the --line mode on *argv* (the arguments after the flag)."""
    if len(argv) != 2:
        sys.stderr.write("Usage: cron_firings.py --line <crontab_text> <cron_parser_path>\n")
        return 1
    try:
        line = next_firings_line(argv[0], argv[1])
    except Exception as exc:  # noqa: BLE001 -- the caller prints its own fallback
        sys.stderr.write(f"cron_firings.py --line: cannot compute firings: {exc}\n")
        return 1
    print(line)
    return 0


def _print_key_value(argv: list[str]) -> int:
    """Run the --key-value mode on *argv* (the arguments after the flag)."""
    if len(argv) != 1:
        sys.stderr.write("Usage: cron_firings.py --key-value <json_text>\n")
        return 1
    try:
        lines = flatten_firings_json(argv[0])
    except ValueError as exc:
        sys.stderr.write(f"cron_firings.py --key-value: not a JSON object: {exc}\n")
        return 1
    for line in lines:
        print(line)
    return 0


def main() -> None:
    """CLI entry point: read args, call cron_firings(), print JSON (or, with
    a leading --count, the number of entries; with a leading --key-value,
    flatten the given JSON object to key=value lines; with a leading --line,
    print the multi-project firing line)."""
    if len(sys.argv) > 1 and sys.argv[1] == "--key-value":
        sys.exit(_print_key_value(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "--line":
        sys.exit(_print_line(sys.argv[2:]))
    count_only = len(sys.argv) > 1 and sys.argv[1] == "--count"
    if count_only:
        del sys.argv[1]
    if len(sys.argv) < 3:
        sys.stderr.write("Usage: cron_firings.py <crontab_text> <cron_parser_path> [<now_ts>]\n")
        print(0 if count_only else json.dumps({}))
        return

    crontab_text = sys.argv[1]
    cron_parser_path = sys.argv[2]
    now_ts: float | None = None
    if len(sys.argv) > 3 and sys.argv[3]:
        try:
            now_ts = float(sys.argv[3])
        except ValueError:
            pass

    try:
        result = cron_firings(crontab_text, cron_parser_path, now_ts)
    except Exception:  # noqa: BLE001 — must not raise; dashboard degrades gracefully
        result = {}

    print(len(result) if count_only else json.dumps(result))


if __name__ == "__main__":
    main()
