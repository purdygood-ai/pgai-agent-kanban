"""
test_deep_verify_schedule.py
==============================
Unit tests for pgai_agent_kanban.deep_verify.schedule — the pure due-check
decision layer behind team/scripts/deep-verify.sh --scheduled.

Coverage:
  - resolve_mode(): blank/None resolves to "weekly"; "weekly" and "off"
    pass through unchanged; any other non-blank value raises
    InvalidDeepVerifyMode rather than guessing.
  - parse_offhours_window() / resolve_offhours_window(): a well-formed
    "HH:MM-HH:MM" parses; a malformed non-blank value raises from the
    strict parser but the forgiving resolver falls back to the default
    with a warning instead of refusing the whole scheduled invocation.
  - evaluate_due(): a ledger record 8 days old is due; a record 2 days old
    is not due and the narration names the project and the next-due
    timestamp; a project with no prior record (last_run_iso=None) is due
    on its first scheduled invocation; mode="off" is never due regardless
    of ledger age; a "now" outside the off-hours window is not due
    regardless of ledger age.
  - Every decision is driven by an injected `now` datetime, never
    datetime.datetime.now() -- the clock is a parameter, not read from the
    wall clock, so every case is deterministic.

Test names describe behaviour under test, never bug/task IDs (team/roles/TESTER.md,
Test Authoring Guidelines, anti-pattern 6).
"""

from __future__ import annotations

import datetime

import pytest

from pgai_agent_kanban.deep_verify.schedule import (
    DEFAULT_MODE,
    DEFAULT_OFFHOURS_WINDOW,
    MODE_OFF,
    MODE_WEEKLY,
    VALID_MODES,
    InvalidDeepVerifyMode,
    evaluate_due,
    parse_offhours_window,
    resolve_mode,
    resolve_offhours_window,
)

_UTC = datetime.timezone.utc


def _dt(iso: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(iso).replace(tzinfo=_UTC)


# A "now" that always falls inside the default off-hours window
# (03:00-03:59 UTC), used by every test that is not specifically exercising
# the window boundary itself.
_NOW_IN_WINDOW = _dt("2026-09-03T03:15:00")


# ---------------------------------------------------------------------------
# resolve_mode()
# ---------------------------------------------------------------------------


def test_resolve_mode_blank_or_none_defaults_to_weekly() -> None:
    assert resolve_mode(None) == DEFAULT_MODE
    assert resolve_mode("") == DEFAULT_MODE
    assert resolve_mode("   ") == DEFAULT_MODE
    assert DEFAULT_MODE == MODE_WEEKLY


def test_resolve_mode_accepts_weekly_and_off_verbatim() -> None:
    assert resolve_mode("weekly") == MODE_WEEKLY
    assert resolve_mode("off") == MODE_OFF
    assert set(VALID_MODES) == {MODE_WEEKLY, MODE_OFF}


def test_resolve_mode_unrecognized_value_raises_instead_of_guessing() -> None:
    with pytest.raises(InvalidDeepVerifyMode):
        resolve_mode("sometimes")
    with pytest.raises(InvalidDeepVerifyMode):
        resolve_mode("Weekly")  # case-sensitive: no silent normalization
    with pytest.raises(InvalidDeepVerifyMode):
        resolve_mode("daily")


# ---------------------------------------------------------------------------
# parse_offhours_window() / resolve_offhours_window()
# ---------------------------------------------------------------------------


def test_parse_offhours_window_accepts_well_formed_range() -> None:
    start, end = parse_offhours_window("01:00-01:59")
    assert (start.hour, start.minute) == (1, 0)
    assert (end.hour, end.minute) == (1, 59)


def test_parse_offhours_window_blank_defaults_to_shipped_default() -> None:
    window = parse_offhours_window(None)
    assert window == parse_offhours_window(DEFAULT_OFFHOURS_WINDOW)


def test_parse_offhours_window_malformed_value_raises() -> None:
    with pytest.raises(ValueError):
        parse_offhours_window("not-a-window")
    with pytest.raises(ValueError):
        parse_offhours_window("25:00-26:00")


def test_resolve_offhours_window_falls_back_on_malformed_value_with_warning() -> None:
    window, warning = resolve_offhours_window("garbage")
    assert window == parse_offhours_window(DEFAULT_OFFHOURS_WINDOW)
    assert warning is not None
    assert "garbage" in warning


def test_resolve_offhours_window_clean_value_has_no_warning() -> None:
    window, warning = resolve_offhours_window("02:00-02:30")
    assert window == parse_offhours_window("02:00-02:30")
    assert warning is None


# ---------------------------------------------------------------------------
# evaluate_due() — off mode
# ---------------------------------------------------------------------------


def test_off_mode_never_due_regardless_of_ledger_age() -> None:
    decision = evaluate_due(
        project="demo",
        mode=MODE_OFF,
        last_run_iso="2020-01-01T00:00:00+00:00",  # ancient -- would be due if weekly
        now=_NOW_IN_WINDOW,
        offhours_window_raw=None,
    )
    assert decision.due is False
    assert "demo" in decision.narration
    assert "off" in decision.narration


# ---------------------------------------------------------------------------
# evaluate_due() — weekly mode, ledger-age driven
# ---------------------------------------------------------------------------


def test_weekly_mode_never_run_before_is_due_on_first_invocation() -> None:
    decision = evaluate_due(
        project="demo",
        mode=MODE_WEEKLY,
        last_run_iso=None,
        now=_NOW_IN_WINDOW,
        offhours_window_raw=None,
    )
    assert decision.due is True
    assert "demo" in decision.narration


def test_weekly_mode_last_run_eight_days_ago_is_due() -> None:
    eight_days_ago = (_NOW_IN_WINDOW - datetime.timedelta(days=8)).isoformat()
    decision = evaluate_due(
        project="demo",
        mode=MODE_WEEKLY,
        last_run_iso=eight_days_ago,
        now=_NOW_IN_WINDOW,
        offhours_window_raw=None,
    )
    assert decision.due is True


def test_weekly_mode_last_run_exactly_seven_days_ago_is_due() -> None:
    """The interval is >=, not >: exactly seven days counts as due."""
    seven_days_ago = (_NOW_IN_WINDOW - datetime.timedelta(days=7)).isoformat()
    decision = evaluate_due(
        project="demo",
        mode=MODE_WEEKLY,
        last_run_iso=seven_days_ago,
        now=_NOW_IN_WINDOW,
        offhours_window_raw=None,
    )
    assert decision.due is True


def test_weekly_mode_last_run_two_days_ago_is_not_due_and_names_next_due_date() -> None:
    two_days_ago = _NOW_IN_WINDOW - datetime.timedelta(days=2)
    decision = evaluate_due(
        project="demo",
        mode=MODE_WEEKLY,
        last_run_iso=two_days_ago.isoformat(),
        now=_NOW_IN_WINDOW,
        offhours_window_raw=None,
    )
    assert decision.due is False
    assert "demo" in decision.narration
    expected_next_due = two_days_ago + datetime.timedelta(days=7)
    assert expected_next_due.strftime("%Y-%m-%dT%H:%M:%S+00:00") in decision.narration


# ---------------------------------------------------------------------------
# evaluate_due() — off-hours window gating
# ---------------------------------------------------------------------------


def test_weekly_mode_outside_offhours_window_is_not_due_even_when_ledger_is_stale() -> None:
    stale_last_run = "2020-01-01T00:00:00+00:00"
    now_outside_window = _dt("2026-09-03T14:00:00")  # far outside 03:00-03:59
    decision = evaluate_due(
        project="demo",
        mode=MODE_WEEKLY,
        last_run_iso=stale_last_run,
        now=now_outside_window,
        offhours_window_raw=None,
    )
    assert decision.due is False
    assert "demo" in decision.narration


def test_weekly_mode_respects_a_project_specific_offhours_window_override() -> None:
    now_in_custom_window = _dt("2026-09-03T01:30:00")
    decision = evaluate_due(
        project="demo",
        mode=MODE_WEEKLY,
        last_run_iso=None,
        now=now_in_custom_window,
        offhours_window_raw="01:00-01:59",
    )
    assert decision.due is True

    now_outside_custom_window = _dt("2026-09-03T03:15:00")
    decision2 = evaluate_due(
        project="demo",
        mode=MODE_WEEKLY,
        last_run_iso=None,
        now=now_outside_custom_window,
        offhours_window_raw="01:00-01:59",
    )
    assert decision2.due is False


# ---------------------------------------------------------------------------
# Narration always names the project (both due and not-due paths)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode,last_run_iso",
    [
        (MODE_OFF, None),
        (MODE_WEEKLY, None),
        (MODE_WEEKLY, "2026-08-20T00:00:00+00:00"),
        (MODE_WEEKLY, "2026-09-02T00:00:00+00:00"),
    ],
)
def test_narration_always_names_the_project(mode: str, last_run_iso: str | None) -> None:
    decision = evaluate_due(
        project="my-special-project",
        mode=mode,
        last_run_iso=last_run_iso,
        now=_NOW_IN_WINDOW,
        offhours_window_raw=None,
    )
    assert "my-special-project" in decision.narration
    assert decision.narration  # never empty
