"""lib/cron_parser.py — Parse crontab text to find kanban agent schedules.

Identifies entries that invoke wake-batch.sh, wake/<provider>.sh, or
cleanup.sh and computes how many seconds until the next firing for each agent.

Public API
----------
    next_firings(crontab_text, now=None) -> dict[str, int | str]

Returns a mapping of agent name (or special keys like 'cleanup') to either:
  - int: seconds until next work-start (cron fire delta + --sleep=N offset)
  - str: human-readable sentinel for infrequent schedules (e.g. weekly)

The --sleep=N stagger offset (used by crontab-large.example to pipeline agents
within the same cron minute) is added to the raw cron-fire delta so the
returned value reflects when the agent actually begins work, not just when
cron fires.  Agents without a --sleep flag (small/medium tiers, integer-minute
offsets) get +0 — unchanged behaviour.

Note: seconds precision allows the dashboard to display MM:SS countdowns
that change on every 5-second watch refresh tick.
"""

import re
from datetime import datetime, timedelta
from typing import Optional

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Match variable-substituted paths like $KANBAN_ROOT, ${KANBAN_ROOT}, $HOME
_VAR_RE = re.compile(r'\$\{?[A-Za-z_][A-Za-z0-9_]*\}?')

# Match a wake script invocation with an agent name.
#
# Supports two invocation styles:
#   Flag form:       .../scripts/wake-batch.sh --agent=NAME [--sleep=N]
#   Positional form: .../scripts/wake-batch.sh NAME
#   Provider subdir: .../scripts/wake/claude.sh --agent=NAME
#                    .../scripts/wake/claude.sh NAME
#
# Captures the agent name from either the --agent=NAME flag form or the
# positional argument form. The flag form is the cron template default.
# Positional form is accepted for operators who have hand-edited crontabs.
_WAKE_SCRIPT_RE = re.compile(
    # The path prefix: $VAR or absolute path, then /scripts/, then one of the
    # known wake script names:
    r'(?:\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|[/~][^\s]*)/'
    r'scripts/'
    r'(?:'
        r'wake-batch\.sh|'                      # batch dispatcher
        r'wake/(?:claude|codex|gemini)\.sh'     # provider subdir layout
    r')'
    # The agent name: either --agent=NAME (preferred) or positional NAME
    r'\s+'
    r'(?:--agent=([A-Za-z0-9_-]+)|([A-Za-z0-9_-]+))',
    re.IGNORECASE,
)

# Match --sleep=N (equals form, canonical) or --sleep N (positional form).
#
# The large-tier crontab template (templates/install/crontab-large.example) uses the
# equals form exclusively: --sleep=0, --sleep=12, --sleep=24, etc.  The
# positional form is accepted for robustness (operators may hand-edit crontabs
# or use older templates).  Only the first --sleep occurrence per line is used.
#
# Group 1 captures the value for the --sleep=N form.
# Group 2 captures the value for the positional --sleep N form.
_SLEEP_RE = re.compile(
    r'--sleep=(\d+)'          # --sleep=N  (equals form)
    r'|'
    r'--sleep\s+(\d+)',       # --sleep N  (positional / space-separated form)
)

# Match a cleanup.sh invocation.
# Recognizes both scripts/cleanup/cleanup.sh and scripts/cleanup.sh layouts.
_CLEANUP_RE = re.compile(
    r'(?:\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|[/~][^\s]*)/'
    r'scripts/(?:cleanup/)?cleanup\.sh',
    re.IGNORECASE,
)

# Match a cron line: 5 fields followed by the command
# Allow any whitespace between fields
_CRON_LINE_RE = re.compile(
    r'^\s*'
    r'(\S+)\s+'   # minute
    r'(\S+)\s+'   # hour
    r'(\S+)\s+'   # day-of-month
    r'(\S+)\s+'   # month
    r'(\S+)\s+'   # day-of-week
    r'(.+)$',     # command (rest of line)
)

# Day-of-week sentinel labels (used for weekly schedules)
_DOW_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

# ---------------------------------------------------------------------------
# Cron field evaluator
# ---------------------------------------------------------------------------

def _parse_field(field: str, min_val: int, max_val: int) -> list:
    """Parse a single cron field (e.g. '*/5', '0,15,30', '1-5') into a
    sorted list of integer values within [min_val, max_val].

    Returns an empty list if the field cannot be parsed.
    """
    values = set()
    try:
        for part in field.split(','):
            part = part.strip()
            if part == '*':
                values.update(range(min_val, max_val + 1))
            elif '/' in part:
                # e.g. */5 or 0-59/5
                base, step_str = part.split('/', 1)
                step = int(step_str)
                if step < 1:
                    return []
                if base == '*':
                    start, end = min_val, max_val
                elif '-' in base:
                    lo, hi = base.split('-', 1)
                    start, end = int(lo), int(hi)
                else:
                    start = int(base)
                    end = max_val
                values.update(range(start, end + 1, step))
            elif '-' in part:
                lo, hi = part.split('-', 1)
                values.update(range(int(lo), int(hi) + 1))
            else:
                values.add(int(part))
    except (ValueError, TypeError):
        return []

    return sorted(v for v in values if min_val <= v <= max_val)


def _next_minute_from_fields(minute_vals: list, hour_vals: list,
                              dow_vals: list, now: datetime) -> Optional[int]:
    """Compute minutes until next firing given evaluated cron fields.

    Scans forward up to 8 days (to handle weekly schedules) looking for the
    first (dow, hour, minute) combination that matches.

    Returns minutes as an integer, or None if no match found within the window.

    Thin wrapper around _next_seconds_from_fields that converts the result
    to whole minutes. Callers that need MM:SS precision should use
    _next_seconds_from_fields directly.
    """
    secs = _next_seconds_from_fields(minute_vals, hour_vals, dow_vals, now)
    if secs is None:
        return None
    return int(secs // 60)


def _next_seconds_from_fields(minute_vals: list, hour_vals: list,
                               dow_vals: list, now: datetime) -> Optional[int]:
    """Compute seconds until next firing given evaluated cron fields.

    Scans forward up to 8 days (to handle weekly schedules) looking for the
    first (dow, hour, minute) combination that matches.

    Returns the delta in whole seconds from *now* to the start of the matching
    cron minute boundary, or None if no match is found within the window.

    Using seconds (rather than minutes) allows callers to display MM:SS
    countdowns that change on every 5-second dashboard refresh tick.
    """
    if not minute_vals or not hour_vals or not dow_vals:
        return None

    # Start from the next minute boundary
    candidate = now + timedelta(minutes=1)
    # Strip seconds/microseconds so we land exactly on a minute boundary
    candidate = candidate.replace(second=0, microsecond=0)

    # We search up to 8 days = 11520 minutes
    for _ in range(11520):
        c_dow = candidate.weekday()
        # Python weekday(): Mon=0..Sun=6, cron: Sun=0..Sat=6
        c_cron_dow = (c_dow + 1) % 7

        if c_cron_dow in dow_vals:
            if candidate.hour in hour_vals:
                if candidate.minute in minute_vals:
                    delta = candidate - now
                    return int(delta.total_seconds())

        candidate += timedelta(minutes=1)

    return None


# ---------------------------------------------------------------------------
# Weekly schedule detector & sentinel builder
# ---------------------------------------------------------------------------

def _is_every_minute(minute_vals: list, hour_vals: list, dow_vals: list) -> bool:
    """Return True when the schedule fires every minute of every hour of every day.

    This is the `* * * * *` pattern: all 60 minute values, all 24 hour values,
    and all 7 day-of-week values are present.  Used to detect the large-tier
    stagger case where the current-minute slot may still be in the future.

    Examples
    --------
    >>> _is_every_minute(list(range(60)), list(range(24)), list(range(7)))
    True
    >>> _is_every_minute([0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55], list(range(24)), list(range(7)))
    False
    """
    return len(minute_vals) == 60 and len(hour_vals) == 24 and len(dow_vals) == 7


def _is_weekly(dow_field: str, dom_field: str, month_field: str) -> bool:
    """Return True when the schedule fires at most once per week."""
    # If month or day-of-month are restricted it may be less than weekly,
    # but for display purposes we treat any specific dow as 'weekly-like'.
    if dom_field != '*' or month_field != '*':
        return False
    # A specific single day-of-week (no wildcards) = weekly
    dow_field = dow_field.strip()
    # Single numeric value or named day (0-7 where both 0 and 7 = Sunday)
    if re.fullmatch(r'[0-7]', dow_field):
        return True
    # Named abbreviated day
    dow_names_lower = [d.lower() for d in _DOW_NAMES]
    if dow_field.lower() in dow_names_lower:
        return True
    return False


def _weekly_sentinel(minute_vals: list, hour_vals: list, dow_vals: list) -> str:
    """Build a human-readable sentinel like 'Sun 4am' for weekly schedules."""
    try:
        if not dow_vals or not hour_vals:
            return 'weekly'
        dow = dow_vals[0]
        hour = hour_vals[0]
        dow_name = _DOW_NAMES[dow % 7]
        if hour == 0:
            hour_str = '12am'
        elif hour < 12:
            hour_str = f'{hour}am'
        elif hour == 12:
            hour_str = '12pm'
        else:
            hour_str = f'{hour - 12}pm'
        return f'{dow_name} {hour_str}'
    except (IndexError, TypeError):
        return 'weekly'


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def next_firings(crontab_text: str, now: Optional[datetime] = None) -> dict:
    """Parse crontab text and return next firing times for kanban agent entries.

    Parameters
    ----------
    crontab_text:
        Raw text of a crontab file (may include comments, blank lines, etc.)
    now:
        Reference time for computing next firings. Defaults to datetime.now().
        Callers should pass an explicit ``now`` when they need a consistent
        reference across multiple calls within the same render cycle.

    Returns
    -------
    dict mapping agent name -> seconds until next firing (int), or a sentinel
    display string (str) for infrequent (weekly) schedules.

    Returning seconds (rather than whole minutes) lets the dashboard display
    MM:SS countdowns that change on every 5-second watch refresh tick.

    Examples
    --------
    >>> result = next_firings("*/5 * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=coder")
    >>> isinstance(result.get('coder'), int)
    True
    """
    if now is None:
        now = datetime.now()

    result = {}

    if not crontab_text:
        return result

    for raw_line in crontab_text.splitlines():
        # Strip inline comments (but only outside quotes — simple heuristic)
        line = raw_line.strip()

        # Skip blank lines and comment lines
        if not line or line.startswith('#'):
            continue

        # Strip trailing comment (heuristic: '#' not inside quotes)
        comment_pos = _find_comment_pos(line)
        if comment_pos >= 0:
            line = line[:comment_pos].rstrip()

        m = _CRON_LINE_RE.match(line)
        if not m:
            continue

        min_field, hour_field, dom_field, month_field, dow_field, command = (
            m.group(1), m.group(2), m.group(3), m.group(4), m.group(5), m.group(6)
        )

        # Parse cron fields
        minute_vals = _parse_field(min_field, 0, 59)
        hour_vals = _parse_field(hour_field, 0, 23)
        dom_vals = _parse_field(dom_field, 1, 31)  # noqa: F841 — reserved for future use
        month_vals = _parse_field(month_field, 1, 12)  # noqa: F841 — reserved
        dow_vals = _parse_field(dow_field, 0, 7)
        # Cron allows 0 and 7 to both mean Sunday; normalise 7 -> 0
        dow_vals = sorted(set(v % 7 for v in dow_vals))

        if not minute_vals or not hour_vals or not dow_vals:
            continue

        # Detect cleanup entries
        if _CLEANUP_RE.search(command):
            key = 'cleanup'
            if _is_weekly(dow_field, dom_field, month_field):
                result[key] = _weekly_sentinel(minute_vals, hour_vals, dow_vals)
            else:
                secs = _next_seconds_from_fields(minute_vals, hour_vals, dow_vals, now)
                if secs is not None:
                    result[key] = secs
            continue

        # Detect wake script entries (any of: wake-batch.sh, wake/claude.sh,
        # wake/codex.sh, wake/gemini.sh). Agent name is in group(1) when the
        # --agent=NAME form was used, group(2) for the positional form.
        wm = _WAKE_SCRIPT_RE.search(command)
        if wm:
            agent = wm.group(1) or wm.group(2)
            if _is_weekly(dow_field, dom_field, month_field):
                result[agent] = _weekly_sentinel(minute_vals, hour_vals, dow_vals)
            else:
                stagger = _sleep_seconds(command)
                if stagger > 0 and _is_every_minute(minute_vals, hour_vals, dow_vals):
                    # Slot-aware calculation for the large-tier every-minute schedule.
                    #
                    # On a `* * * * *` cron each minute boundary is a firing, so
                    # the agent's work-start = (nearest minute boundary + stagger).
                    # Two candidates exist for "nearest":
                    #   1. This minute:  stagger - pos  seconds  (valid if pos < stagger)
                    #   2. Next minute:  (60 - pos) + stagger    (always valid)
                    #
                    # Prefer the current-minute slot when it has not yet passed.
                    #
                    # pos: fractional seconds elapsed within the current minute
                    pos = now.second + now.microsecond / 1_000_000
                    if pos <= stagger:
                        # Current-minute slot is upcoming (or firing right now).
                        # int() truncates fractional remainder so the result is
                        # always in [0, 59].
                        result[agent] = int(stagger - pos)
                    else:
                        # Current-minute slot has passed; use next minute's slot.
                        # Result is in [1, 59] since pos > stagger means pos > 0.
                        result[agent] = int((60 - pos) + stagger)
                else:
                    secs = _next_seconds_from_fields(minute_vals, hour_vals, dow_vals, now)
                    if secs is not None:
                        # Add the --sleep=N stagger offset so the value reflects
                        # when the agent starts work, not just when cron fires.
                        # Agents without --sleep get +0 (unchanged behaviour).
                        result[agent] = secs + stagger

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _sleep_seconds(command: str) -> int:
    """Extract the --sleep=N (or --sleep N) value from a cron command string.

    Returns the integer sleep offset in seconds, or 0 when no --sleep flag is
    present (small/medium tier schedules that stagger via cron minute offsets
    rather than sub-minute sleeps).

    Only the first --sleep occurrence per command line is used.

    Parameters
    ----------
    command:
        The command portion of a cron line (everything after the five
        schedule fields).

    Examples
    --------
    >>> _sleep_seconds("$ROOT/scripts/wake-batch.sh --agent=coder --sleep=12")
    12
    >>> _sleep_seconds("$ROOT/scripts/wake-batch.sh --agent=pm --sleep=0")
    0
    >>> _sleep_seconds("$ROOT/scripts/wake-batch.sh --agent=pm")
    0
    >>> _sleep_seconds("$ROOT/scripts/wake-batch.sh --agent=pm --sleep 36")
    36
    """
    m = _SLEEP_RE.search(command)
    if m is None:
        return 0
    raw = m.group(1) if m.group(1) is not None else m.group(2)
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 0


def _find_comment_pos(line: str) -> int:
    """Return position of first unquoted '#', or -1 if none found."""
    in_single = False
    in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == '#' and not in_single and not in_double:
            return i
    return -1
