"""
test_deep_verify_freshness.py
===============================
Unit tests for pgai_agent_kanban.deep_verify.freshness — the pure
classification layer behind team/scripts/deep-verify-freshness-check.sh,
the pre-release promotion gate.

Coverage:
  - A record whose Verdict is "built", whose Version matches the promotion
    target, and whose Timestamp is inside the freshness window: RESULT_FRESH.
  - record=None (never run): RESULT_NEVER_RUN.
  - Verdict=failed-to-build and Verdict=could-not-attempt are two distinct
    results, never collapsed into one another.
  - A built record for a DIFFERENT version: RESULT_STALE, naming the
    version mismatch.
  - A built record for the right version but older than the freshness
    window: RESULT_STALE, naming the age.
  - A built record that is both a version mismatch AND too old: RESULT_STALE
    with both reasons present.
  - The freshness window is a parameter (window_days), not a hardcoded
    constant -- overriding it changes the verdict for an otherwise-fixed
    record/now pair.
  - Every decision is driven by an injected `now` datetime, never
    datetime.datetime.now() -- the clock is a parameter, matching the
    project's established pattern (schedule.py's evaluate_due).
  - The CLI (main()) round-trips the same decisions through its
    result=/age=/reason= stdout contract.

Test names describe behaviour under test, never bug/task IDs (team/roles/TESTER.md,
Test Authoring Guidelines, anti-pattern 6).
"""

from __future__ import annotations

import datetime

import pytest

from pgai_agent_kanban.deep_verify.freshness import (
    DEFAULT_FRESHNESS_WINDOW_DAYS,
    RESULT_COULD_NOT_ATTEMPT,
    RESULT_FAILED_TO_BUILD,
    RESULT_FRESH,
    RESULT_NEVER_RUN,
    RESULT_STALE,
    VALID_RESULTS,
    evaluate_freshness,
    main,
)

_UTC = datetime.timezone.utc


def _dt(iso: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(iso).replace(tzinfo=_UTC)


def _built_record(*, version: str = "v2.9.12", timestamp: str = "2026-09-01T03:15:00+00:00") -> dict:
    return {
        "timestamp": timestamp,
        "version": version,
        "commit": "e240ee1",
        "verdict": "built",
        "detail": "docker/claude-code: built; podman/claude-code: built",
    }


# ---------------------------------------------------------------------------
# RESULT_FRESH — the pass case
# ---------------------------------------------------------------------------


def test_built_record_for_target_version_within_window_is_fresh() -> None:
    record = _built_record(version="v2.9.12", timestamp="2026-09-01T03:15:00+00:00")
    now = _dt("2026-09-03T12:00:00+00:00")  # ~2 days later

    decision = evaluate_freshness(target_version="v2.9.12", record=record, now=now)

    assert decision.result == RESULT_FRESH
    assert decision.reasons == ()
    assert decision.age_str  # non-empty; caller prints it for the pass case


def test_fresh_result_reports_the_record_age() -> None:
    record = _built_record(timestamp="2026-09-01T00:00:00+00:00")
    now = _dt("2026-09-03T08:00:00+00:00")  # exactly 2d8h later

    decision = evaluate_freshness(target_version="v2.9.12", record=record, now=now)

    assert decision.result == RESULT_FRESH
    assert decision.age_str == "2d8h"


def test_record_exactly_at_the_window_boundary_is_fresh() -> None:
    record = _built_record(timestamp="2026-08-27T00:00:00+00:00")
    now = _dt("2026-09-03T00:00:00+00:00")  # exactly 7 days later

    decision = evaluate_freshness(
        target_version="v2.9.12", record=record, now=now, window_days=7
    )

    assert decision.result == RESULT_FRESH


# ---------------------------------------------------------------------------
# RESULT_NEVER_RUN
# ---------------------------------------------------------------------------


def test_no_record_at_all_is_never_run() -> None:
    now = _dt("2026-09-03T12:00:00+00:00")

    decision = evaluate_freshness(target_version="v2.9.12", record=None, now=now)

    assert decision.result == RESULT_NEVER_RUN
    assert decision.reasons == ()
    assert decision.age_str == ""


# ---------------------------------------------------------------------------
# Non-green verdicts — two distinct results, distinguishable without
# inspecting `detail` text.
# ---------------------------------------------------------------------------


def test_failed_to_build_verdict_is_its_own_distinct_result() -> None:
    record = _built_record()
    record["verdict"] = "failed-to-build"
    now = _dt("2026-09-01T04:00:00+00:00")

    decision = evaluate_freshness(target_version="v2.9.12", record=record, now=now)

    assert decision.result == RESULT_FAILED_TO_BUILD


def test_could_not_attempt_verdict_is_its_own_distinct_result() -> None:
    record = _built_record()
    record["verdict"] = "could-not-attempt"
    now = _dt("2026-09-01T04:00:00+00:00")

    decision = evaluate_freshness(target_version="v2.9.12", record=record, now=now)

    assert decision.result == RESULT_COULD_NOT_ATTEMPT


def test_failed_to_build_and_could_not_attempt_never_collapse_together() -> None:
    now = _dt("2026-09-01T04:00:00+00:00")

    failed = evaluate_freshness(
        target_version="v2.9.12",
        record={**_built_record(), "verdict": "failed-to-build"},
        now=now,
    )
    could_not = evaluate_freshness(
        target_version="v2.9.12",
        record={**_built_record(), "verdict": "could-not-attempt"},
        now=now,
    )

    assert failed.result != could_not.result


def test_non_green_verdicts_ignore_version_and_age_entirely() -> None:
    # A failed-to-build/could-not-attempt record for the WRONG version and
    # ancient timestamp is still just "not green" -- the verdict itself is
    # disqualifying, independent of freshness.
    record = _built_record(version="v0.0.1", timestamp="2020-01-01T00:00:00+00:00")
    record["verdict"] = "failed-to-build"
    now = _dt("2026-09-03T12:00:00+00:00")

    decision = evaluate_freshness(target_version="v2.9.12", record=record, now=now)

    assert decision.result == RESULT_FAILED_TO_BUILD
    assert decision.age_str == ""


# ---------------------------------------------------------------------------
# RESULT_STALE — green but does not speak for the tree state being promoted
# ---------------------------------------------------------------------------


def test_built_record_for_a_different_version_is_stale_naming_the_mismatch() -> None:
    record = _built_record(version="v2.9.11", timestamp="2026-09-03T00:00:00+00:00")
    now = _dt("2026-09-03T01:00:00+00:00")  # 1 hour later -- age is not the problem

    decision = evaluate_freshness(target_version="v2.9.12", record=record, now=now)

    assert decision.result == RESULT_STALE
    assert len(decision.reasons) == 1
    assert "v2.9.11" in decision.reasons[0]
    assert "v2.9.12" in decision.reasons[0]


def test_built_record_for_the_right_version_but_too_old_is_stale_naming_the_age() -> None:
    record = _built_record(version="v2.9.12", timestamp="2026-08-01T00:00:00+00:00")
    now = _dt("2026-09-03T00:00:00+00:00")  # 33 days later

    decision = evaluate_freshness(
        target_version="v2.9.12", record=record, now=now, window_days=7
    )

    assert decision.result == RESULT_STALE
    assert len(decision.reasons) == 1
    assert "7-day" in decision.reasons[0]
    assert decision.age_str  # age is still reported even though it is stale


def test_stale_record_that_is_both_wrong_version_and_too_old_names_both_reasons() -> None:
    record = _built_record(version="v2.9.9", timestamp="2026-07-01T00:00:00+00:00")
    now = _dt("2026-09-03T00:00:00+00:00")

    decision = evaluate_freshness(
        target_version="v2.9.12", record=record, now=now, window_days=7
    )

    assert decision.result == RESULT_STALE
    assert len(decision.reasons) == 2
    joined = " ".join(decision.reasons)
    assert "v2.9.9" in joined
    assert "7-day" in joined


def test_unparseable_timestamp_on_an_otherwise_matching_record_refuses_as_stale() -> None:
    record = _built_record(version="v2.9.12", timestamp="not-a-timestamp")
    now = _dt("2026-09-03T00:00:00+00:00")

    decision = evaluate_freshness(target_version="v2.9.12", record=record, now=now)

    assert decision.result == RESULT_STALE
    assert decision.reasons  # conservative refuse, not a silent pass


# ---------------------------------------------------------------------------
# The freshness window is a parameter, not a hardcoded constant.
# ---------------------------------------------------------------------------


def test_window_days_parameter_changes_the_verdict_for_a_fixed_record() -> None:
    record = _built_record(version="v2.9.12", timestamp="2026-08-25T00:00:00+00:00")
    now = _dt("2026-09-03T00:00:00+00:00")  # 9 days later

    with_default = evaluate_freshness(target_version="v2.9.12", record=record, now=now)
    assert with_default.result == RESULT_STALE
    assert DEFAULT_FRESHNESS_WINDOW_DAYS == 7

    with_wider_window = evaluate_freshness(
        target_version="v2.9.12", record=record, now=now, window_days=14
    )
    assert with_wider_window.result == RESULT_FRESH


# ---------------------------------------------------------------------------
# VALID_RESULTS vocabulary
# ---------------------------------------------------------------------------


def test_valid_results_names_exactly_the_five_outcomes() -> None:
    assert set(VALID_RESULTS) == {
        RESULT_FRESH,
        RESULT_NEVER_RUN,
        RESULT_FAILED_TO_BUILD,
        RESULT_COULD_NOT_ATTEMPT,
        RESULT_STALE,
    }


# ---------------------------------------------------------------------------
# CLI (main())
# ---------------------------------------------------------------------------


def test_cli_prints_result_age_for_a_fresh_record(capsys: pytest.CaptureFixture) -> None:
    rc = main(
        [
            "v2.9.12",
            "true",
            "2026-09-01T00:00:00+00:00",
            "v2.9.12",
            "built",
            "2026-09-03T08:00:00+00:00",
        ]
    )
    out = capsys.readouterr().out

    assert rc == 0
    assert "result=fresh" in out
    assert "age=2d8h" in out


def test_cli_prints_never_run_when_found_is_false(capsys: pytest.CaptureFixture) -> None:
    rc = main(
        ["v2.9.12", "false", "", "", "", "2026-09-03T08:00:00+00:00"]
    )
    out = capsys.readouterr().out

    assert rc == 0
    assert "result=never_run" in out


def test_cli_prints_one_reason_line_per_stale_reason(capsys: pytest.CaptureFixture) -> None:
    rc = main(
        [
            "v2.9.12",
            "true",
            "2026-07-01T00:00:00+00:00",
            "v2.9.9",
            "built",
            "2026-09-03T00:00:00+00:00",
            "7",
        ]
    )
    out = capsys.readouterr().out

    assert rc == 0
    assert "result=stale" in out
    assert out.count("reason=") == 2


def test_cli_accepts_optional_window_days_argument(capsys: pytest.CaptureFixture) -> None:
    rc = main(
        [
            "v2.9.12",
            "true",
            "2026-08-25T00:00:00+00:00",
            "v2.9.12",
            "built",
            "2026-09-03T00:00:00+00:00",
            "14",
        ]
    )
    out = capsys.readouterr().out

    assert rc == 0
    assert "result=fresh" in out


def test_cli_rejects_wrong_argument_count() -> None:
    assert main(["only-one-arg"]) == 2


def test_cli_rejects_unparseable_now(capsys: pytest.CaptureFixture) -> None:
    rc = main(
        ["v2.9.12", "false", "", "", "", "not-a-timestamp"]
    )
    err = capsys.readouterr().err

    assert rc == 1
    assert "could not parse now" in err
