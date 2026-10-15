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

Public API:
    parse_iso(value, *, naive_as_utc=True) -> datetime
    try_parse_iso(value, *, naive_as_utc=True) -> datetime | None
    format_iso(dt) -> str
    utc_now_iso() -> str
"""

from __future__ import annotations

import datetime
from typing import Optional

__all__ = ["format_iso", "parse_iso", "try_parse_iso", "utc_now_iso"]

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
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def try_parse_iso(value: str, *, naive_as_utc: bool = True) -> Optional[datetime.datetime]:
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
        dt = dt.astimezone(datetime.timezone.utc)
    return dt.strftime(_UTC_FORMAT)


def utc_now_iso() -> str:
    """Return the current time as ``YYYY-MM-DDThh:mm:ss+00:00`` in UTC."""
    return format_iso(datetime.datetime.now(datetime.timezone.utc))
