"""
provider_pause.py — recognise a provider's usage-limit output and keep the
fleet-wide pause it calls for.

When a provider account reaches its usage limit, every dispatch to that
provider fails the same way until the limit resets. Retrying burns each
task's retries in seconds, so a usage limit is not a transient error and not
a task's lack of progress: it is a timed pause for the whole fleet on that
provider.

Recognising it
--------------
:func:`detect` reads the tail of an agent log (the same last
:data:`TAIL_LINES` lines the transient-error detector reads) for a
usage-limit signature (:data:`USAGE_LIMIT_RE`) in the shape the provider
CLI prints it: the message opens its own line, after optional leading
whitespace and an optional ``Error:`` / ``API Error:`` label. Examples that
count:

* ``Claude AI usage limit reached|1760151600``
* ``Claude usage limit reached. Your limit resets 3pm (UTC).``
* ``You've hit your limit · resets 3pm (America/New_York)``
* ``5-hour limit reached ∙ resets 4pm`` / ``Weekly limit reached · resets ...``
* ``ERROR: You've hit your usage limit. Try again in 2h.``
* ``error: usage_limit_exceeded``

The log tail also carries the agent's own closing message, so the bare
words are not enough: a sentence that merely mentions "usage limit", a
"usage-limit pause" or the ``usage_limit_pause_seconds`` key, or quotes a
signature mid-line, is prose about the feature, not the provider reporting
a limit, and records nothing. Billing failures (``insufficient_quota``, a
credit balance too low) are deliberately not matched: they do not reset on
a clock.

The reset time is read from the output when it states one, in any of these
shapes (examples):

* ``Claude AI usage limit reached|1760151600`` — epoch seconds after ``|``;
* ``resets 2026-10-11T15:00:00Z`` — an ISO-8601 time after "reset";
* ``try again in 2h 30m`` / ``resets in 45 minutes`` — a relative delay;
* ``resets 3pm (America/New_York)`` / ``reset at 15:00`` — a clock time,
  in the named IANA zone or else the host's local zone, at its next
  occurrence;
* ``resets Oct 14, 9am (UTC)`` — a month, day and clock time, the same way,
  in its next occurrence.

A stated time already past, or more than :data:`MAX_STATED_PAUSE_S` ahead,
is not trusted. Without a trusted stated time the pause lasts the configured
default (``[wake] usage_limit_pause_seconds``, exported as
``USAGE_LIMIT_PAUSE_SECONDS``; :data:`DEFAULT_PAUSE_S` when blank).

The marker
----------
The pause is one file per provider under the kanban root,
``<kanban_root>/provider-pause/<provider>.pause``, plain ``key=value``
lines::

    provider=claude
    paused_at=2026-10-11T12:00:00+00:00
    resumes_at=2026-10-11T15:00:00+00:00
    source=stated            # stated | default
    signature=Claude usage limit reached; resets 3pm (UTC)
    task=CODER-20261011-001-example
    project=example-project

Recording a pause while one is already in force keeps the later
``resumes_at``. :func:`check` reads the marker at dispatch time: in force
until ``resumes_at``; once that has passed the marker is removed and the
end reported, once, by whichever wake sees it first.

Usage (CLI; the wake calls it through pp_run_ops)::

    python3 -m pgai_agent_kanban.lib.provider_pause record \\
        KANBAN_ROOT PROVIDER LOG_FILE [--default-seconds N] [--now ISO] \\
        [--task ID] [--project NAME]
    python3 -m pgai_agent_kanban.lib.provider_pause check \\
        KANBAN_ROOT PROVIDER [--now ISO]

``record`` exits 0 and prints ``resumes_at=``, ``source=``, ``signature=``
and ``marker=`` lines when the log tail carries a usage-limit signature and
the pause was recorded; it exits 1, printing nothing, when there is no
signature. ``check`` prints ``state=paused`` (exit 3), ``state=ended``
(exit 0, the marker just removed) or ``state=clear`` (exit 0), plus the
marker's ``resumes_at=``, ``source=``, ``signature=`` and ``marker=`` lines
for the first two. Usage errors exit 2.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import os
import pathlib
import re
import sys
from pathlib import Path
from typing import Optional

# Run by file path, this module has no parent package; put its own package
# root first on sys.path so the imports below resolve from the same tree.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib.atomic_write import atomic_write  # noqa: E402
from pgai_agent_kanban.lib.timeutil import format_iso, try_parse_iso  # noqa: E402

__all__ = [
    "DEFAULT_PAUSE_S",
    "MAX_STATED_PAUSE_S",
    "TAIL_LINES",
    "USAGE_LIMIT_RE",
    "UsageLimit",
    "PauseState",
    "detect",
    "parse_reset",
    "marker_path",
    "record",
    "check",
    "main",
]

#: Seconds a pause lasts when the output states no usable reset time.
DEFAULT_PAUSE_S = 3600

#: A stated reset further ahead than this is not trusted (8 days covers a
#: weekly limit with room for clock skew).
MAX_STATED_PAUSE_S = 8 * 86400

#: Lines of the agent log read, as the transient-error detector reads.
TAIL_LINES = 50

#: Usage-limit signatures as the provider CLI prints them (case-insensitive),
#: matched against one line at a time. The ``^`` anchor is the point: the
#: CLI's message opens its line (after whitespace and an optional
#: ``Error:`` / ``API Error:`` label), while an agent's prose about the
#: feature carries the same words mid-sentence. Billing failures are excluded.
USAGE_LIMIT_RE = re.compile(
    r"^\s*(?:(?:api\s+)?error\s*:?\s*)?"
    r"(?:claude(?:\s+ai)?\s+usage\s+limit\s+reached"
    r"|you(?:'|\u2019|\s+ha)?ve\s+(?:hit|reached)\s+your\s+(?:usage\s+|weekly\s+|daily\s+|session\s+|opus\s+)?limit"
    r"|\d+[- ]hour\s+limit\s+reached"
    r"|(?:weekly|daily|session|opus)\s+limit\s+reached"
    r"|usage_limit_(?:exceeded|reached))\b",
    re.IGNORECASE,
)

_EPOCH_RE = re.compile(r"limit reached\s*\|\s*(\d{9,11})\b", re.IGNORECASE)
_ISO_RE = re.compile(
    r"reset(?:s)?(?:\s+at)?\s*[:=]?\s*"
    r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)",
    re.IGNORECASE,
)
_UNIT_SECONDS = {"d": 86400, "h": 3600, "m": 60, "s": 1}
_DURATION_PART = r"\d+\s*(?:days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b"
_RELATIVE_RE = re.compile(
    r"(?:try again|resets?|available again)\s+in\s+"
    rf"((?:{_DURATION_PART}[\s,]*(?:and\s+)?)+)",
    re.IGNORECASE,
)
_DURATION_PART_RE = re.compile(r"(\d+)\s*([dhms])", re.IGNORECASE)
_CLOCK_RE = re.compile(
    r"reset(?:s)?(?:\s+at)?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?"
    r"(?:\s*\(\s*([A-Za-z_]+(?:/[A-Za-z0-9_+-]+)*)\s*\))?",
    re.IGNORECASE,
)

_DATE_CLOCK_RE = re.compile(
    r"reset(?:s)?(?:\s+(?:at|on))?\s+([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(?:at\s+)?"
    r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?"
    r"(?:\s*\(\s*([A-Za-z_]+(?:/[A-Za-z0-9_+-]+)*)\s*\))?",
    re.IGNORECASE,
)
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")

_MARKER_DIR = "provider-pause"
_MARKER_SUFFIX = ".pause"
_PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SIGNATURE_MAX = 160

EXIT_OK = 0
EXIT_NO_SIGNATURE = 1
EXIT_USAGE = 2
EXIT_PAUSED = 3


@dataclasses.dataclass(frozen=True)
class UsageLimit:
    """A usage-limit signature found in a log tail.

    ``source`` is ``stated`` when ``resumes_at`` was read from the output,
    ``default`` when the configured default was applied.
    """

    signature: str
    resumes_at: datetime.datetime
    source: str


@dataclasses.dataclass(frozen=True)
class PauseState:
    """What a marker says at a given time: ``paused``, ``ended`` or ``clear``."""

    state: str
    fields: dict
    marker: Path


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _local_zone(name: Optional[str]) -> datetime.tzinfo:
    """The IANA zone *name*, or the host's local zone when absent or unknown."""
    if name:
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo(name)
        except Exception:  # noqa: BLE001 - an unknown zone falls back to local
            pass
    return datetime.datetime.now().astimezone().tzinfo or datetime.timezone.utc


def _hour_minute(hour_text: str, minute_text: Optional[str], meridiem: Optional[str]) -> Optional[tuple[int, int]]:
    """24-hour (hour, minute) from a clock time, or None when it is not one."""
    if minute_text is None and meridiem is None:
        return None  # a bare number is not a clock time
    hour = int(hour_text)
    minute = int(minute_text or 0)
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem.lower() == "pm" else 0)
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def _clock_reset(match: "re.Match[str]", now: datetime.datetime) -> Optional[datetime.datetime]:
    hour_text, minute_text, meridiem, zone = match.groups()
    clock = _hour_minute(hour_text, minute_text, meridiem)
    if clock is None:
        return None
    local_now = now.astimezone(_local_zone(zone))
    candidate = local_now.replace(hour=clock[0], minute=clock[1], second=0, microsecond=0)
    if candidate <= local_now:
        candidate += datetime.timedelta(days=1)
    return candidate.astimezone(datetime.timezone.utc)


def _date_clock_reset(match: "re.Match[str]", now: datetime.datetime) -> Optional[datetime.datetime]:
    month_text, day_text, hour_text, minute_text, meridiem, zone = match.groups()
    month_key = month_text[:3].lower()
    clock = _hour_minute(hour_text, minute_text, meridiem)
    if month_key not in _MONTHS or clock is None:
        return None
    local_now = now.astimezone(_local_zone(zone))
    for year in (local_now.year, local_now.year + 1):
        try:
            candidate = local_now.replace(
                year=year, month=_MONTHS.index(month_key) + 1, day=int(day_text),
                hour=clock[0], minute=clock[1], second=0, microsecond=0,
            )
        except ValueError:
            return None
        if candidate > local_now:
            return candidate.astimezone(datetime.timezone.utc)
    return None


def parse_reset(text: str, now: datetime.datetime) -> Optional[datetime.datetime]:
    """Return the reset time *text* states, or None when it states none.

    Tries the epoch, ISO-8601, relative, date-and-clock and clock shapes
    the module docstring lists, taking the last mention of each shape and the first
    shape that yields a time. A time not after *now*, or more than
    :data:`MAX_STATED_PAUSE_S` after it, is not trusted (None).
    """
    found: Optional[datetime.datetime] = None
    epochs = _EPOCH_RE.findall(text)
    if epochs:
        found = datetime.datetime.fromtimestamp(int(epochs[-1]), datetime.timezone.utc)
    if found is None:
        isos = _ISO_RE.findall(text)
        if isos:
            found = try_parse_iso(isos[-1].replace(" ", "T", 1))
    if found is None:
        relatives = _RELATIVE_RE.findall(text)
        if relatives:
            seconds = sum(
                int(n) * _UNIT_SECONDS[unit.lower()] for n, unit in _DURATION_PART_RE.findall(relatives[-1])
            )
            if seconds > 0:
                found = now + datetime.timedelta(seconds=seconds)
    if found is None:
        for match in reversed(list(_DATE_CLOCK_RE.finditer(text))):
            found = _date_clock_reset(match, now)
            if found is not None:
                break
    if found is None:
        clocks = list(_CLOCK_RE.finditer(text))
        for match in reversed(clocks):
            found = _clock_reset(match, now)
            if found is not None:
                break
    if found is None:
        return None
    if found <= now or (found - now).total_seconds() > MAX_STATED_PAUSE_S:
        return None
    return found.astimezone(datetime.timezone.utc)


def detect(text: str, now: datetime.datetime, default_s: int = DEFAULT_PAUSE_S) -> Optional[UsageLimit]:
    """Return the usage limit *text* reports, or None when it reports none.

    Only the last :data:`TAIL_LINES` lines are read, and a line counts only
    when it opens with a provider usage-limit message (:data:`USAGE_LIMIT_RE`);
    agent prose that mentions the usage limit mid-sentence does not. The
    signature is the last line carrying a match; the reset time is read from
    that line and the lines after it (where a CLI prints the reset), else
    *default_s* seconds after *now* applies.
    """
    lines = text.splitlines()[-TAIL_LINES:]
    hit = None
    for index in range(len(lines) - 1, -1, -1):
        if USAGE_LIMIT_RE.search(lines[index]):
            hit = index
            break
    if hit is None:
        return None
    signature = " ".join(lines[hit].split())[:_SIGNATURE_MAX]
    stated = parse_reset("\n".join(lines[hit:]), now)
    if stated is not None:
        return UsageLimit(signature, stated, "stated")
    return UsageLimit(signature, now + datetime.timedelta(seconds=default_s), "default")


# ---------------------------------------------------------------------------
# The marker
# ---------------------------------------------------------------------------


def marker_path(kanban_root: "Path | str", provider: str) -> Path:
    """``<kanban_root>/provider-pause/<provider>.pause``."""
    return Path(kanban_root) / _MARKER_DIR / f"{provider}{_MARKER_SUFFIX}"


def _read_marker(path: Path) -> dict:
    fields: dict = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return fields
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


def _one_line(value: str) -> str:
    return " ".join(str(value).split())


def record(
    kanban_root: "Path | str",
    provider: str,
    limit: UsageLimit,
    now: datetime.datetime,
    *,
    task: str = "none",
    project: str = "none",
) -> tuple[Path, dict]:
    """Write (or extend) the provider's pause marker; return its path and fields.

    A marker already in force with a later ``resumes_at`` is kept as it is.
    """
    path = marker_path(kanban_root, provider)
    existing = _read_marker(path)
    existing_until = try_parse_iso(existing.get("resumes_at", ""))
    if existing_until is not None and existing_until >= limit.resumes_at:
        return path, existing
    fields = {
        "provider": provider,
        "paused_at": format_iso(now),
        "resumes_at": format_iso(limit.resumes_at),
        "source": limit.source,
        "signature": _one_line(limit.signature) or "usage limit",
        "task": _one_line(task) or "none",
        "project": _one_line(project) or "none",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, "".join(f"{k}={v}\n" for k, v in fields.items()), prefix=".provider-pause-")
    return path, fields


def check(kanban_root: "Path | str", provider: str, now: datetime.datetime) -> PauseState:
    """Whether *provider* is paused at *now*.

    ``paused`` while ``resumes_at`` is ahead; ``ended`` when it has passed
    (the marker is removed here, so the end is reported once); ``clear``
    when there is no marker. A marker whose ``resumes_at`` does not parse
    cannot be timed, so it is removed and reported ``ended``.
    """
    path = marker_path(kanban_root, provider)
    if not path.exists():
        return PauseState("clear", {}, path)
    fields = _read_marker(path)
    until = try_parse_iso(fields.get("resumes_at", ""))
    if until is not None and now < until:
        return PauseState("paused", fields, path)
    try:
        os.unlink(path)
    except OSError:
        pass  # already removed by another wake, or not removable: still over
    return PauseState("ended", fields, path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _now(value: str) -> datetime.datetime:
    if value.strip():
        parsed = try_parse_iso(value)
        if parsed is None:
            raise argparse.ArgumentTypeError(f"--now {value!r} is not an ISO-8601 time")
        return parsed
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


def _provider(value: str) -> str:
    if not _PROVIDER_RE.match(value):
        raise argparse.ArgumentTypeError(f"provider {value!r} must match {_PROVIDER_RE.pattern}")
    return value


def _seconds(value: str) -> int:
    text = (value or "").strip()
    return int(text) if text.isdigit() else DEFAULT_PAUSE_S


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.lib.provider_pause",
        description="Recognise provider usage-limit output and keep the fleet-wide pause it calls for.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_record = sub.add_parser("record", help="record a pause when a log tail shows a usage limit")
    p_record.add_argument(
        "kanban_root",
        help="kanban root; the pause marker is <root>/provider-pause/<provider>.pause",
    )
    p_record.add_argument(
        "provider",
        type=_provider,
        help="provider name, lowercase letters, digits, - and _ (e.g. claude)",
    )
    p_record.add_argument(
        "log_file",
        help="log file whose tail is searched for a usage-limit message",
    )
    p_record.add_argument(
        "--default-seconds",
        default="",
        help=f"pause length when no reset time is stated ([wake] usage_limit_pause_seconds); blank means {DEFAULT_PAUSE_S}",
    )
    p_record.add_argument("--now", default="", help="ISO-8601 time to record at (default: now)")
    p_record.add_argument("--task", default="none", help="the task whose output showed the limit")
    p_record.add_argument("--project", default="none", help="that task's project")

    p_check = sub.add_parser("check", help="report whether the provider is paused now")
    p_check.add_argument("kanban_root", help="kanban root holding the provider-pause/ markers")
    p_check.add_argument(
        "provider",
        type=_provider,
        help="provider name, lowercase letters, digits, - and _ (e.g. claude)",
    )
    p_check.add_argument("--now", default="", help="ISO-8601 time to check at (default: now)")
    return parser


def _print_fields(fields: dict, marker: Path) -> None:
    for key in ("resumes_at", "source", "signature"):
        print(f"{key}={fields.get(key, 'unknown')}")
    print(f"marker={marker}")


def main(argv: Optional[list[str]] = None) -> int:
    """Run the provider-pause CLI.

    ``record KANBAN_ROOT PROVIDER LOG_FILE [...]`` records a pause when the log
    shows a usage-limit message and prints the marker's fields; ``check
    KANBAN_ROOT PROVIDER [--now ISO]`` prints ``state=clear``, ``paused`` or
    ``ended`` and, unless clear, the marker's fields. Exit codes: 0 (``EXIT_OK``) recorded,
    or not paused; 1 (``EXIT_NO_SIGNATURE``) the log is unreadable or shows no
    usage limit; 2 (``EXIT_USAGE``) usage error; 3 (``EXIT_PAUSED``) the
    provider is paused now.
    """
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        now = _now(args.now)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))

    if args.command == "record":
        try:
            with open(args.log_file, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            return EXIT_NO_SIGNATURE
        limit = detect(text, now, _seconds(args.default_seconds))
        if limit is None:
            return EXIT_NO_SIGNATURE
        path, fields = record(args.kanban_root, args.provider, limit, now, task=args.task, project=args.project)
        _print_fields(fields, path)
        return EXIT_OK

    state = check(args.kanban_root, args.provider, now)
    print(f"state={state.state}")
    if state.state != "clear":
        _print_fields(state.fields, state.marker)
    return EXIT_PAUSED if state.state == "paused" else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
