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

Table-driven: each helper's cases share one assertion shape (input(s) ->
expected return value), so they are grouped under a single parametrized
test per helper rather than one function per literal input.
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


@pytest.mark.parametrize(
    "field, min_val, max_val, expected",
    [
        pytest.param("*", 0, 4, [0, 1, 2, 3, 4], id="star_expands_to_all_values"),
        pytest.param("5", 0, 59, [5], id="single_value"),
        pytest.param("1-3", 0, 59, [1, 2, 3], id="range"),
        pytest.param("*/5", 0, 59, list(range(0, 60, 5)), id="step_on_star"),
        pytest.param("0-10/2", 0, 59, [0, 2, 4, 6, 8, 10], id="step_on_range"),
        pytest.param("0,15,30,45", 0, 59, [0, 15, 30, 45], id="comma_list"),
        pytest.param("0,50,100", 0, 59, [0, 50], id="values_clipped_to_range"),
        pytest.param("bogus", 0, 59, [], id="invalid_returns_empty_list"),
        pytest.param("*/0", 0, 59, [], id="step_zero_returns_empty"),
    ],
)
def test_parse_field(field: str, min_val: int, max_val: int, expected: list) -> None:
    """_parse_field(field, min_val, max_val) returns the sorted integer list the cron syntax describes."""
    assert _parse_field(field, min_val, max_val) == expected


# ---------------------------------------------------------------------------
# _sleep_seconds()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command, expected",
    [
        pytest.param(
            "$ROOT/scripts/wake-batch.sh --agent=coder --sleep=12", 12,
            id="extracts_equals_form",
        ),
        pytest.param(
            "$ROOT/scripts/wake-batch.sh --agent=pm --sleep 36", 36,
            id="extracts_positional_form",
        ),
        pytest.param(
            "$ROOT/scripts/wake-batch.sh --agent=pm", 0,
            id="returns_zero_when_absent",
        ),
        pytest.param(
            "$ROOT/scripts/wake-batch.sh --agent=po --sleep=0", 0,
            id="returns_zero_for_explicit_zero",
        ),
    ],
)
def test_sleep_seconds(command: str, expected: int) -> None:
    """_sleep_seconds extracts the --sleep=N (or --sleep N) offset, defaulting to 0."""
    assert _sleep_seconds(command) == expected


# ---------------------------------------------------------------------------
# _is_weekly()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dow, dom, month, expected",
    [
        pytest.param("0", "*", "*", True, id="single_numeric_dow_returns_true"),
        pytest.param("Sun", "*", "*", True, id="named_day_returns_true"),
        pytest.param("*", "*", "*", False, id="star_dow_returns_false"),
        pytest.param("0", "1", "*", False, id="restricted_dom_returns_false"),
        pytest.param("0", "*", "1", False, id="restricted_month_returns_false"),
    ],
)
def test_is_weekly(dow: str, dom: str, month: str, expected: bool) -> None:
    """_is_weekly is True only for a single specific day-of-week with unrestricted dom/month."""
    assert _is_weekly(dow, dom, month) is expected


# ---------------------------------------------------------------------------
# _weekly_sentinel()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "minute_vals, hour_vals, dow_vals, expected",
    [
        pytest.param([0], [0], [0], "Sun 12am", id="midnight"),
        pytest.param([0], [12], [1], "Mon 12pm", id="noon"),
        pytest.param([0], [14], [5], "Fri 2pm", id="afternoon"),
        pytest.param([0], [9], [2], "Tue 9am", id="morning_not_12am"),
        pytest.param([], [], [], "weekly", id="empty_lists_falls_back_to_weekly"),
    ],
)
def test_weekly_sentinel(minute_vals: list, hour_vals: list, dow_vals: list, expected: str) -> None:
    """_weekly_sentinel renders 'Day Ham/pm', falling back to 'weekly' when fields are empty."""
    assert _weekly_sentinel(minute_vals, hour_vals, dow_vals) == expected


# ---------------------------------------------------------------------------
# _is_every_minute()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "minute_vals, hour_vals, dow_vals, expected",
    [
        pytest.param(
            list(range(60)), list(range(24)), list(range(7)), True,
            id="all_fields_complete",
        ),
        pytest.param(
            list(range(0, 60, 5)), list(range(24)), list(range(7)), False,
            id="partial_minutes_returns_false",
        ),
    ],
)
def test_is_every_minute(minute_vals: list, hour_vals: list, dow_vals: list, expected: bool) -> None:
    """_is_every_minute is True only for the full `* * * * *` field set."""
    assert _is_every_minute(minute_vals, hour_vals, dow_vals) is expected


# ---------------------------------------------------------------------------
# _find_comment_pos()
# ---------------------------------------------------------------------------

_LINE_WITH_UNQUOTED_HASH = "* * * * * command # comment"
_LINE_WITHOUT_COMMENT = "* * * * * command"
_LINE_WITH_QUOTED_HASH = "* * * * * echo '#not-a-comment'"


@pytest.mark.parametrize(
    "line, expected",
    [
        pytest.param(
            _LINE_WITH_UNQUOTED_HASH, _LINE_WITH_UNQUOTED_HASH.index("#"),
            id="unquoted_hash",
        ),
        pytest.param(_LINE_WITHOUT_COMMENT, -1, id="no_comment_returns_negative_one"),
        pytest.param(_LINE_WITH_QUOTED_HASH, -1, id="hash_inside_single_quotes_ignored"),
    ],
)
def test_find_comment_pos(line: str, expected: int) -> None:
    """_find_comment_pos locates an unquoted '#', ignoring '#' inside single quotes."""
    assert _find_comment_pos(line) == expected


# ---------------------------------------------------------------------------
# next_firings() — public API
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "crontab",
    [
        pytest.param("", id="empty_crontab"),
        pytest.param("# This is a comment\n", id="comment_lines_are_ignored"),
        pytest.param("\n\n\n", id="blank_lines_are_ignored"),
    ],
)
def test_next_firings_returns_empty_dict(crontab: str) -> None:
    """next_firings returns {} when no entry line yields a parseable agent/cleanup match."""
    assert next_firings(crontab, now=_NOW) == {}


@pytest.mark.parametrize(
    "crontab, key",
    [
        pytest.param(
            "*/5 * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=coder\n",
            "coder", id="simple_every_5_minutes_agent",
        ),
        pytest.param(
            "*/5 * * * * $KANBAN_ROOT/scripts/wake-batch.sh pm\n",
            "pm", id="wake_script_positional_agent_form",
        ),
        pytest.param(
            "*/5 * * * * $KANBAN_ROOT/scripts/wake/claude.sh --agent=writer\n",
            "writer", id="provider_subdir_wake_script",
        ),
        pytest.param(
            "0 3 * * * $KANBAN_ROOT/scripts/cleanup.sh\n",
            "cleanup", id="cleanup_entry_is_detected",
        ),
        pytest.param(
            "0 3 * * * $KANBAN_ROOT/scripts/cleanup/cleanup.sh\n",
            "cleanup", id="cleanup_entry_nested_path",
        ),
        pytest.param(
            "*/5 * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=tester # every 5 min\n",
            "tester", id="inline_comment_stripped_before_parse",
        ),
    ],
)
def test_next_firings_extracts_agent_or_cleanup_key_as_integer_seconds(crontab: str, key: str) -> None:
    """next_firings resolves the agent/cleanup key and returns a positive integer seconds value."""
    result = next_firings(crontab, now=_NOW)
    assert key in result
    assert isinstance(result[key], int)
    assert result[key] > 0


@pytest.mark.parametrize(
    "crontab, key, expected_substring",
    [
        pytest.param(
            "0 4 * * 0 $KANBAN_ROOT/scripts/wake-batch.sh --agent=po\n",
            "po", "Sun", id="weekly_agent_returns_sentinel_string",
        ),
        pytest.param(
            "0 2 * * 0 $KANBAN_ROOT/scripts/cleanup.sh\n",
            "cleanup", None, id="weekly_cleanup_returns_sentinel_string",
        ),
        pytest.param(
            "0 4 * * 7 $KANBAN_ROOT/scripts/wake-batch.sh --agent=po\n",
            "po", "Sun", id="dow_normalises_7_to_sunday",
        ),
    ],
)
def test_next_firings_weekly_schedule_returns_sentinel_string(
    crontab: str, key: str, expected_substring: str | None
) -> None:
    """next_firings returns a human-readable sentinel string (not an int) for weekly schedules."""
    result = next_firings(crontab, now=_NOW)
    assert key in result
    assert isinstance(result[key], str)
    if expected_substring is not None:
        assert expected_substring in result[key]


@pytest.mark.parametrize(
    "now, crontab, key, expected",
    [
        pytest.param(
            datetime(2024, 1, 15, 10, 30, 0),
            "* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=coder --sleep=12\n",
            "coder", 12,
            id="stagger_with_every_minute_schedule_pos_before_stagger",
        ),
        pytest.param(
            datetime(2024, 1, 15, 10, 30, 30),
            "* * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=coder --sleep=12\n",
            "coder", 42,
            id="stagger_past_slot_uses_next_minute",
        ),
        pytest.param(
            _NOW,
            "*/5 * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=cm --sleep=30\n",
            "cm", 330,
            id="sleep_offset_added_to_regular_schedule",
        ),
    ],
)
def test_next_firings_computes_exact_seconds_with_stagger(
    now: datetime, crontab: str, key: str, expected: int
) -> None:
    """next_firings adds the --sleep=N stagger to the cron-fire delta, using slot-aware
    math for the every-minute schedule (current-minute slot if not yet passed, else next-minute)."""
    result = next_firings(crontab, now=now)
    assert result[key] == expected


def test_next_firings_multiple_agents_in_crontab() -> None:
    """next_firings returns separate entries for each agent (and cleanup) in the crontab."""
    crontab = (
        "*/5 * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=coder\n"
        "*/5 * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=tester\n"
        "0 1 * * * $KANBAN_ROOT/scripts/cleanup.sh\n"
    )
    result = next_firings(crontab, now=_NOW)
    assert "coder" in result
    assert "tester" in result
    assert "cleanup" in result
