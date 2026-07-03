"""
test_cron_parser.py
===================
Behavioral unit tests for team/pm-agent/lib/cron_parser.py.

The public entry point is next_firings(crontab_text, now=...).  All tests
pass an explicit ``now`` datetime so behaviour is deterministic and
independent of the host clock.

Internal helpers (_parse_field, _is_weekly, _sleep_seconds, _weekly_sentinel)
are tested directly where their branches are not easily reached through the
public API alone.
"""

from __future__ import annotations

from datetime import datetime

import pytest

try:
    from pm_agent.lib import cron_parser
    from pm_agent.lib.cron_parser import (
        next_firings,
        _parse_field,
        _sleep_seconds,
        _is_weekly,
        _weekly_sentinel,
        _find_comment_pos,
        _is_every_minute,
    )
except ImportError:
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent / "pm-agent"))
    from lib import cron_parser  # type: ignore[no-redef]
    from lib.cron_parser import (  # type: ignore[no-redef]
        next_firings,
        _parse_field,
        _sleep_seconds,
        _is_weekly,
        _weekly_sentinel,
        _find_comment_pos,
        _is_every_minute,
    )


# ---------------------------------------------------------------------------
# Helpers — reference time
# ---------------------------------------------------------------------------

# Fixed reference: Monday 2024-01-15 at 10:30:00 (cron dow=1)
_NOW = datetime(2024, 1, 15, 10, 30, 0)


# ---------------------------------------------------------------------------
# _parse_field()
# ---------------------------------------------------------------------------


def test_parse_field_star_expands_to_all_values() -> None:
    """_parse_field('*') returns every value in [min_val, max_val]."""
    result = _parse_field("*", 0, 4)
    assert result == [0, 1, 2, 3, 4]


def test_parse_field_single_value() -> None:
    """_parse_field('5') returns [5]."""
    assert _parse_field("5", 0, 59) == [5]


def test_parse_field_range() -> None:
    """_parse_field('1-3') returns [1, 2, 3]."""
    assert _parse_field("1-3", 0, 59) == [1, 2, 3]


def test_parse_field_step_on_star() -> None:
    """_parse_field('*/5') returns every 5th value from 0 to max."""
    result = _parse_field("*/5", 0, 59)
    assert result == list(range(0, 60, 5))


def test_parse_field_step_on_range() -> None:
    """_parse_field('0-10/2') returns even values 0..10."""
    result = _parse_field("0-10/2", 0, 59)
    assert result == [0, 2, 4, 6, 8, 10]


def test_parse_field_comma_list() -> None:
    """_parse_field('0,15,30,45') returns those exact values."""
    result = _parse_field("0,15,30,45", 0, 59)
    assert result == [0, 15, 30, 45]


def test_parse_field_values_clipped_to_range() -> None:
    """_parse_field filters out values outside [min_val, max_val]."""
    result = _parse_field("0,50,100", 0, 59)
    assert result == [0, 50]


def test_parse_field_invalid_returns_empty_list() -> None:
    """_parse_field returns [] for unparseable inputs."""
    assert _parse_field("bogus", 0, 59) == []


def test_parse_field_step_zero_returns_empty() -> None:
    """_parse_field returns [] when step is 0 (division by zero guard)."""
    assert _parse_field("*/0", 0, 59) == []


# ---------------------------------------------------------------------------
# _sleep_seconds()
# ---------------------------------------------------------------------------


def test_sleep_seconds_extracts_equals_form() -> None:
    """_sleep_seconds parses --sleep=N and returns N as int."""
    assert _sleep_seconds("$ROOT/scripts/wake-batch.sh --agent=coder --sleep=12") == 12


def test_sleep_seconds_extracts_positional_form() -> None:
    """_sleep_seconds parses --sleep N (space-separated) and returns N."""
    assert _sleep_seconds("$ROOT/scripts/wake-batch.sh --agent=pm --sleep 36") == 36


def test_sleep_seconds_returns_zero_when_absent() -> None:
    """_sleep_seconds returns 0 when no --sleep flag is present."""
    assert _sleep_seconds("$ROOT/scripts/wake-batch.sh --agent=pm") == 0


def test_sleep_seconds_returns_zero_for_explicit_zero() -> None:
    """_sleep_seconds returns 0 when --sleep=0 is given."""
    assert _sleep_seconds("$ROOT/scripts/wake-batch.sh --agent=po --sleep=0") == 0


# ---------------------------------------------------------------------------
# _is_weekly()
# ---------------------------------------------------------------------------


def test_is_weekly_single_numeric_dow_returns_true() -> None:
    """_is_weekly returns True for a single numeric day-of-week with * dom/month."""
    assert _is_weekly("0", "*", "*") is True


def test_is_weekly_named_day_returns_true() -> None:
    """_is_weekly returns True for a named abbreviated day like 'Sun'."""
    assert _is_weekly("Sun", "*", "*") is True


def test_is_weekly_star_dow_returns_false() -> None:
    """_is_weekly returns False when dow is '*' (fires every day)."""
    assert _is_weekly("*", "*", "*") is False


def test_is_weekly_restricted_dom_returns_false() -> None:
    """_is_weekly returns False when dom is restricted (fires less than weekly)."""
    assert _is_weekly("0", "1", "*") is False


def test_is_weekly_restricted_month_returns_false() -> None:
    """_is_weekly returns False when month is restricted."""
    assert _is_weekly("0", "*", "1") is False


# ---------------------------------------------------------------------------
# _weekly_sentinel()
# ---------------------------------------------------------------------------


def test_weekly_sentinel_midnight() -> None:
    """_weekly_sentinel returns '12am' for hour 0."""
    result = _weekly_sentinel([0], [0], [0])
    assert result == "Sun 12am"


def test_weekly_sentinel_noon() -> None:
    """_weekly_sentinel returns '12pm' for hour 12."""
    result = _weekly_sentinel([0], [12], [1])
    assert result == "Mon 12pm"


def test_weekly_sentinel_afternoon() -> None:
    """_weekly_sentinel formats afternoon hours as Xpm."""
    result = _weekly_sentinel([0], [14], [5])
    assert result == "Fri 2pm"


def test_weekly_sentinel_morning() -> None:
    """_weekly_sentinel formats morning hours as Xam (not 12am)."""
    result = _weekly_sentinel([0], [9], [2])
    assert result == "Tue 9am"


def test_weekly_sentinel_empty_lists_returns_weekly() -> None:
    """_weekly_sentinel falls back to 'weekly' when lists are empty."""
    assert _weekly_sentinel([], [], []) == "weekly"


# ---------------------------------------------------------------------------
# _is_every_minute()
# ---------------------------------------------------------------------------


def test_is_every_minute_all_fields_complete() -> None:
    """_is_every_minute returns True for * * * * * schedule."""
    assert _is_every_minute(list(range(60)), list(range(24)), list(range(7))) is True


def test_is_every_minute_partial_minutes_returns_false() -> None:
    """_is_every_minute returns False when minute list is not all 60."""
    assert _is_every_minute(list(range(0, 60, 5)), list(range(24)), list(range(7))) is False


# ---------------------------------------------------------------------------
# _find_comment_pos()
# ---------------------------------------------------------------------------


def test_find_comment_pos_unquoted_hash() -> None:
    """_find_comment_pos returns the index of an unquoted '#'."""
    line = "* * * * * command # comment"
    pos = _find_comment_pos(line)
    assert line[pos] == "#"


def test_find_comment_pos_no_comment_returns_negative_one() -> None:
    """_find_comment_pos returns -1 when there is no unquoted '#'."""
    assert _find_comment_pos("* * * * * command") == -1


def test_find_comment_pos_hash_inside_single_quotes_ignored() -> None:
    """_find_comment_pos does not treat '#' inside single-quoted strings as comments."""
    line = "* * * * * echo '#not-a-comment'"
    assert _find_comment_pos(line) == -1


# ---------------------------------------------------------------------------
# next_firings() — public API
# ---------------------------------------------------------------------------


def test_next_firings_empty_crontab_returns_empty_dict() -> None:
    """next_firings returns {} for empty input."""
    assert next_firings("", now=_NOW) == {}


def test_next_firings_comment_lines_are_ignored() -> None:
    """next_firings ignores lines starting with '#'."""
    crontab = "# This is a comment\n"
    assert next_firings(crontab, now=_NOW) == {}


def test_next_firings_blank_lines_are_ignored() -> None:
    """next_firings ignores blank lines."""
    crontab = "\n\n\n"
    assert next_firings(crontab, now=_NOW) == {}


def test_next_firings_simple_every_5_minutes_agent() -> None:
    """next_firings extracts agent name and returns a positive integer seconds value."""
    crontab = "*/5 * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=coder\n"
    result = next_firings(crontab, now=_NOW)
    assert "coder" in result
    assert isinstance(result["coder"], int)
    assert result["coder"] > 0


def test_next_firings_wake_script_positional_agent_form() -> None:
    """next_firings recognises the positional wake-batch.sh NAME form."""
    crontab = "*/5 * * * * $KANBAN_ROOT/scripts/wake-batch.sh pm\n"
    result = next_firings(crontab, now=_NOW)
    assert "pm" in result
    assert isinstance(result["pm"], int)


def test_next_firings_provider_subdir_wake_script() -> None:
    """next_firings recognises wake/claude.sh provider subdir layout."""
    crontab = "*/5 * * * * $KANBAN_ROOT/scripts/wake/claude.sh --agent=writer\n"
    result = next_firings(crontab, now=_NOW)
    assert "writer" in result


def test_next_firings_cleanup_entry_is_detected() -> None:
    """next_firings detects cleanup.sh entries and keys them as 'cleanup'."""
    crontab = "0 3 * * * $KANBAN_ROOT/scripts/cleanup.sh\n"
    result = next_firings(crontab, now=_NOW)
    assert "cleanup" in result


def test_next_firings_cleanup_entry_nested_path() -> None:
    """next_firings detects cleanup.sh in scripts/cleanup/ subdirectory layout."""
    crontab = "0 3 * * * $KANBAN_ROOT/scripts/cleanup/cleanup.sh\n"
    result = next_firings(crontab, now=_NOW)
    assert "cleanup" in result


def test_next_firings_weekly_agent_returns_sentinel_string() -> None:
    """next_firings returns a sentinel string for weekly schedules."""
    # dow=0 means Sunday; single-day schedule with specific hour
    crontab = "0 4 * * 0 $KANBAN_ROOT/scripts/wake-batch.sh --agent=po\n"
    result = next_firings(crontab, now=_NOW)
    assert "po" in result
    # Should be a string sentinel like "Sun 4am", not an integer
    assert isinstance(result["po"], str)
    assert "Sun" in result["po"]


def test_next_firings_weekly_cleanup_returns_sentinel_string() -> None:
    """next_firings returns a sentinel string for a weekly cleanup entry."""
    crontab = "0 2 * * 0 $KANBAN_ROOT/scripts/cleanup.sh\n"
    result = next_firings(crontab, now=_NOW)
    assert "cleanup" in result
    assert isinstance(result["cleanup"], str)


def test_next_firings_inline_comment_stripped_before_parse() -> None:
    """next_firings strips inline comments before parsing the command."""
    crontab = "*/5 * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=tester # every 5 min\n"
    result = next_firings(crontab, now=_NOW)
    assert "tester" in result
    assert isinstance(result["tester"], int)


def test_next_firings_stagger_with_every_minute_schedule() -> None:
    """next_firings adds --sleep=N stagger to an every-minute (* * * * *) schedule.

    With a * * * * * schedule and --sleep=12, the agent's work-start is
    the current-minute slot (stagger - pos seconds away) when pos <= stagger,
    or the next-minute slot otherwise.  The result must be non-negative.
    """
    # now is at second 0 of the minute, so pos=0 <= stagger=12
    now_at_zero = datetime(2024, 1, 15, 10, 30, 0)
    crontab = "* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=coder --sleep=12\n"
    result = next_firings(crontab, now=now_at_zero)
    assert "coder" in result
    # pos=0, stagger=12 → result should be stagger - pos = 12
    assert result["coder"] == 12


def test_next_firings_stagger_past_slot_uses_next_minute() -> None:
    """When pos > stagger on an every-minute schedule, next-minute slot is used."""
    # second=30 > stagger=12, so we expect (60-30)+12 = 42
    now_at_30 = datetime(2024, 1, 15, 10, 30, 30)
    crontab = "* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=coder --sleep=12\n"
    result = next_firings(crontab, now=now_at_30)
    assert "coder" in result
    assert result["coder"] == 42


def test_next_firings_sleep_offset_added_to_regular_schedule() -> None:
    """next_firings adds --sleep=N to the computed seconds for non-every-minute schedules."""
    # Use a fixed 5-minute schedule (not every-minute) with --sleep=30
    crontab = "*/5 * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=cm --sleep=30\n"
    result = next_firings(crontab, now=_NOW)
    assert "cm" in result
    assert isinstance(result["cm"], int)
    # Result should reflect the cron offset plus the 30-second stagger.
    # At 10:30:00 the next */5 boundary is 10:35:00 = 300s away, plus stagger=30 = 330.
    assert result["cm"] == 330


def test_next_firings_multiple_agents_in_crontab() -> None:
    """next_firings returns separate entries for each agent in the crontab."""
    crontab = (
        "*/5 * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=coder\n"
        "*/5 * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=tester\n"
        "0 1 * * * $KANBAN_ROOT/scripts/cleanup.sh\n"
    )
    result = next_firings(crontab, now=_NOW)
    assert "coder" in result
    assert "tester" in result
    assert "cleanup" in result


def test_next_firings_dow_normalises_7_to_sunday() -> None:
    """next_firings treats dow=7 as Sunday (same as 0) per cron convention."""
    crontab = "0 4 * * 7 $KANBAN_ROOT/scripts/wake-batch.sh --agent=po\n"
    result = next_firings(crontab, now=_NOW)
    assert "po" in result
    # Weekly sentinel for Sunday
    assert isinstance(result["po"], str)
    assert "Sun" in result["po"]
