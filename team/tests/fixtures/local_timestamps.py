"""
local_timestamps.py — wall-clock timestamps in the host's zone, DST-safe.

The wake's log() writes ``date -Iseconds`` stamps: the local date and time
with the UTC offset in force at that instant. A stamp built by taking
"now" and replacing its hour keeps now's offset, which is wrong for an hour
on the other side of a daylight-saving change. These helpers attach the
offset that applies to the wall-clock time itself, and do arithmetic in UTC
so a shifted stamp carries its own offset too.

    today_local_iso(9, 0, 5)          -> "2026-10-31T09:00:05+01:00"
    local_at(days_ago=1, hour=12)     -> yesterday 12:00 local, aware
    shift(stamp, minutes=-2)          -> two minutes earlier, aware, own offset
"""

from __future__ import annotations

import datetime as _dt


def local_at(days_ago: int = 0, hour: int = 0, minute: int = 0, second: int = 0) -> _dt.datetime:
    """An aware datetime for a wall-clock time *days_ago* days before today, local zone."""
    day = _dt.date.today() - _dt.timedelta(days=days_ago)
    return _dt.datetime.combine(day, _dt.time(hour, minute, second)).astimezone()


def today_local_iso(hour: int, minute: int, second: int) -> str:
    """``date -Iseconds`` for today at *hour*:*minute*:*second* local time."""
    return local_at(0, hour, minute, second).isoformat(timespec="seconds")


def shift(stamp: _dt.datetime, **delta: float) -> _dt.datetime:
    """*stamp* moved by ``timedelta(**delta)``, computed in UTC, back in the local zone."""
    return (stamp.astimezone(_dt.UTC) + _dt.timedelta(**delta)).astimezone()
