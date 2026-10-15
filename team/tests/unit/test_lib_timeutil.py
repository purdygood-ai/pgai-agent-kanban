"""
test_lib_timeutil.py
====================
Behavioural tests for pgai_agent_kanban.lib.timeutil: ISO-8601 parsing of
the three forms the kanban's writers produce (``Z``, ``+HH:MM``, no offset)
and the canonical UTC rendering.
"""

from __future__ import annotations

import datetime

import pytest

from pgai_agent_kanban.lib.timeutil import format_iso, parse_iso, try_parse_iso, utc_now_iso

UTC = datetime.timezone.utc


def test_z_suffix_parses_as_utc() -> None:
    dt = parse_iso("2026-10-03T21:41:15Z")
    assert dt == datetime.datetime(2026, 10, 3, 21, 41, 15, tzinfo=UTC)
    assert dt.utcoffset() == datetime.timedelta(0)


def test_lowercase_z_suffix_parses_as_utc() -> None:
    assert parse_iso("2026-10-03T21:41:15z") == datetime.datetime(2026, 10, 3, 21, 41, 15, tzinfo=UTC)


def test_explicit_offset_is_kept() -> None:
    dt = parse_iso("2026-10-03T21:41:15+02:00")
    assert dt.utcoffset() == datetime.timedelta(hours=2)
    assert dt == datetime.datetime(2026, 10, 3, 19, 41, 15, tzinfo=UTC)


def test_negative_offset_is_kept() -> None:
    dt = parse_iso("2026-10-03T21:41:15-05:00")
    assert dt.utcoffset() == datetime.timedelta(hours=-5)


def test_no_offset_defaults_to_utc() -> None:
    dt = parse_iso("2026-10-03T21:41:15")
    assert dt.tzinfo is not None
    assert dt == datetime.datetime(2026, 10, 3, 21, 41, 15, tzinfo=UTC)


def test_no_offset_stays_naive_when_asked() -> None:
    dt = parse_iso("2026-10-03T21:41:15", naive_as_utc=False)
    assert dt.tzinfo is None
    assert dt == datetime.datetime(2026, 10, 3, 21, 41, 15)


def test_offset_value_is_aware_even_when_naive_is_kept() -> None:
    dt = parse_iso("2026-10-03T21:41:15Z", naive_as_utc=False)
    assert dt.utcoffset() == datetime.timedelta(0)


def test_surrounding_whitespace_is_ignored() -> None:
    assert parse_iso("  2026-10-03T21:41:15+00:00\n") == datetime.datetime(2026, 10, 3, 21, 41, 15, tzinfo=UTC)


@pytest.mark.parametrize("bad", ["", "not-a-timestamp", "2026-13-40T00:00:00Z", "yesterday"])
def test_unparseable_value_raises_value_error(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_iso(bad)


@pytest.mark.parametrize("bad", ["", "not-a-timestamp", "2026-13-40T00:00:00Z"])
def test_try_parse_returns_none_for_unparseable_value(bad: str) -> None:
    assert try_parse_iso(bad) is None


def test_try_parse_returns_the_same_value_parse_would() -> None:
    assert try_parse_iso("2026-10-03T21:41:15Z") == parse_iso("2026-10-03T21:41:15Z")
    assert try_parse_iso("2026-10-03T21:41:15", naive_as_utc=False).tzinfo is None


def test_format_renders_utc_with_explicit_offset() -> None:
    dt = datetime.datetime(2026, 10, 3, 21, 41, 15, 987654, tzinfo=UTC)
    assert format_iso(dt) == "2026-10-03T21:41:15+00:00"


def test_format_converts_an_offset_datetime_to_utc() -> None:
    dt = datetime.datetime(2026, 10, 3, 23, 41, 15, tzinfo=datetime.timezone(datetime.timedelta(hours=2)))
    assert format_iso(dt) == "2026-10-03T21:41:15+00:00"


def test_format_treats_naive_as_utc() -> None:
    assert format_iso(datetime.datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02T03:04:05+00:00"


def test_format_output_parses_back_to_the_same_instant() -> None:
    dt = datetime.datetime(2026, 10, 3, 21, 41, 15, tzinfo=UTC)
    assert parse_iso(format_iso(dt)) == dt


def test_utc_now_iso_is_current_utc_time() -> None:
    before = datetime.datetime.now(UTC).replace(microsecond=0)
    stamp = utc_now_iso()
    after = datetime.datetime.now(UTC)
    assert stamp.endswith("+00:00")
    assert before <= parse_iso(stamp) <= after
