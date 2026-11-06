"""
timeutil.py — ISO-8601 timestamp parsing and formatting.

The kanban writes timestamps as ``YYYY-MM-DDThh:mm:ss`` with a ``+HH:MM``
offset or a ``Z`` suffix, and some older writers leave the offset off. Every
Python module that reads or writes one of those timestamps calls this module
rather than carrying its own ``fromisoformat`` wrapper.

Parsing
-------
``parse_iso`` accepts the three forms the writers produce:

    2026-10-03T21:41:15Z         -> aware, UTC
    2026-10-03T21:41:15+02:00    -> aware, offset kept as written
    2026-10-03T21:41:15          -> no offset (see ``naive_as_utc``)

Surrounding whitespace is ignored, and a trailing ``Z`` / ``z`` is read as
``+00:00`` on every Python version. A no-offset value is returned as an
aware UTC datetime by default; ``naive_as_utc=False`` returns it naive, for
callers that compare it against other naive datetimes.

``parse_iso`` raises ``ValueError`` on an unparseable value;
``try_parse_iso`` returns ``None`` instead, for readers that skip malformed
lines.

Formatting
----------
``format_iso`` renders a datetime as ``YYYY-MM-DDThh:mm:ss+00:00`` in UTC,
the form the ledgers and release-state events record; ``utc_now_iso`` is
``format_iso`` of the current time.

``compact_utc_stamp`` renders a datetime as ``YYYYMMDDThhmmssZ`` in UTC, for
a file name that sorts by time. ``format_days_hours`` renders a duration
as whole days and hours (``3d4h``, or ``5h`` under a day), for a one-line
"last run N ago" message.

A decision time
---------------
``moment_or_now`` turns a caller's optional "now" (a datetime, ISO text or
None) into an aware datetime: a naive datetime is read as UTC, and None or
text that does not parse is the current UTC time.

Public API:
    parse_iso(value, *, naive_as_utc=True) -> datetime
    try_parse_iso(value, *, naive_as_utc=True) -> datetime | None
    format_iso(dt) -> str
    utc_now_iso() -> str
    compact_utc_stamp(moment) -> str
    format_days_hours(delta) -> str
    moment_or_now(now) -> datetime
"""

from __future__ import annotations

import datetime

__all__ = [
    "compact_utc_stamp",
    "format_days_hours",
    "format_iso",
    "moment_or_now",
    "parse_iso",
    "try_parse_iso",
    "utc_now_iso",
]

# strftime pattern for the canonical UTC form: seconds precision, explicit
# +00:00 offset (never "Z"), matching what the ledgers already record.
_UTC_FORMAT = "%Y-%m-%dT%H:%M:%S+00:00"


def parse_iso(value: str, *, naive_as_utc: bool = True) -> datetime.datetime:
    """Parse an ISO-8601 timestamp.

    Args:
        value:        Timestamp text, e.g. ``"2026-10-03T21:41:15Z"``.
        naive_as_utc: When the value carries no offset, return it as an
                      aware UTC datetime (``True``, the default) or leave it
                      naive (``False``).

    Returns:
        The parsed datetime. A value written with ``Z`` or an offset is
        always aware.

    Raises:
        ValueError: *value* is not an ISO-8601 timestamp.
    """
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    dt = datetime.datetime.fromisoformat(text)
    if naive_as_utc and dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    return dt


def try_parse_iso(value: str, *, naive_as_utc: bool = True) -> datetime.datetime | None:
    """Parse an ISO-8601 timestamp, returning ``None`` when it does not parse.

    Same accepted forms and ``naive_as_utc`` meaning as ``parse_iso``.
    """
    try:
        return parse_iso(value, naive_as_utc=naive_as_utc)
    except ValueError:
        return None


def format_iso(dt: datetime.datetime) -> str:
    """Render *dt* as ``YYYY-MM-DDThh:mm:ss+00:00`` in UTC.

    An aware datetime is converted to UTC first; a naive one is taken to
    already be UTC. Sub-second precision is dropped.
    """
    if dt.tzinfo is not None:
        dt = dt.astimezone(datetime.UTC)
    return dt.strftime(_UTC_FORMAT)


def utc_now_iso() -> str:
    """Return the current time as ``YYYY-MM-DDThh:mm:ss+00:00`` in UTC."""
    return format_iso(datetime.datetime.now(datetime.UTC))


def compact_utc_stamp(moment: datetime.datetime) -> str:
    """Render *moment* as ``YYYYMMDDThhmmssZ`` in UTC, e.g. ``20261026T065400Z``.

    The moment is converted to UTC first; a naive one is read as local time,
    as ``datetime.astimezone`` reads it. Sub-second precision is dropped.
    """
    return moment.astimezone(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")


def format_days_hours(delta: datetime.timedelta) -> str:
    """Render *delta* as whole days and hours: ``3d4h``, or ``5h`` under a day.

    Minutes and seconds are dropped (the hours are floored), so anything
    under an hour reads ``0h``.
    """
    total_hours = int(delta.total_seconds() // 3600)
    days, hours = divmod(total_hours, 24)
    if days:
        return f"{days}d{hours}h"
    return f"{hours}h"


def moment_or_now(now: datetime.datetime | str | None) -> datetime.datetime:
    """*now* as an aware datetime; the current UTC time when None or unparseable.

    A naive datetime is taken to be UTC; ISO text is read by ``try_parse_iso``.
    """
    if isinstance(now, datetime.datetime):
        return now if now.tzinfo is not None else now.replace(tzinfo=datetime.UTC)
    parsed = try_parse_iso(now) if isinstance(now, str) else None
    return parsed or datetime.datetime.now(datetime.UTC)
