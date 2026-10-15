"""
test_blocked_ledger.py
========================
Unit tests for pgai_agent_kanban.metrics.blocked_ledger — the per-RC
blocked-interval ledger: the library that stamps block-start/block-end
boundary events and turns them into a single merged, clamped
blocked-minutes number.

Coverage:
  - A fixture RC with a 40-minute blocked interval and a 20-minute
    interval overlapping it by 10 minutes reads back as 50 blocked
    minutes (overlap merge, not naive summing).
  - A fixture RC with no ledger file at all reads back as 0 blocked
    minutes — absence is a valid state, never an error.
  - An interval that starts before the RC's opened timestamp, and one
    that is still open at the RC's closed timestamp, each contribute
    only the portion inside the RC window; an interval falling entirely
    outside the window contributes 0.
  - A malformed ledger is refused through the dual-channel error
    contract (stable error key, plain-language message, runnable
    remedy) — no number returned, no file rewritten.
  - append_block_start / append_block_end pairing rules: a second start
    for an already-open (version, source) refuses; an end with no open
    start refuses; two different sources may be concurrently open and
    merge correctly at read time.

All tests use tmp_path; no live filesystem paths. Function names describe
behaviour under test, never bug/task IDs (roles/TESTER.md "Test Authoring
Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pgai_agent_kanban.metrics.blocked_ledger import (
    LedgerError,
    append_block_end,
    append_block_start,
    blocked_minutes,
    ledger_path_for_project,
    parse_records,
)

WINDOW_OPENED = "2026-09-18T00:00:00+00:00"
WINDOW_CLOSED = "2026-09-19T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Absence is a valid state, never an error
# ---------------------------------------------------------------------------


def test_missing_ledger_file_reads_back_as_zero_blocked_minutes(tmp_path: Path) -> None:
    ledger_path = str(tmp_path / "blocked-intervals.jsonl")
    minutes = blocked_minutes(ledger_path, "v2.24.0", WINDOW_OPENED, WINDOW_CLOSED)
    assert minutes == 0.0


def test_empty_ledger_file_reads_back_as_zero_blocked_minutes(tmp_path: Path) -> None:
    ledger_path = tmp_path / "blocked-intervals.jsonl"
    ledger_path.write_text("", encoding="utf-8")
    minutes = blocked_minutes(str(ledger_path), "v2.24.0", WINDOW_OPENED, WINDOW_CLOSED)
    assert minutes == 0.0


def test_ledger_with_other_versions_only_reads_back_as_zero_for_unblocked_version(
    tmp_path: Path,
) -> None:
    ledger_path = str(tmp_path / "blocked-intervals.jsonl")
    append_block_start(ledger_path, "v2.23.0", "halt:project", "2026-09-01T00:00:00+00:00")
    append_block_end(ledger_path, "v2.23.0", "halt:project", "2026-09-01T00:30:00+00:00")

    minutes = blocked_minutes(ledger_path, "v2.24.0", WINDOW_OPENED, WINDOW_CLOSED)
    assert minutes == 0.0


# ---------------------------------------------------------------------------
# Overlap merge
# ---------------------------------------------------------------------------


def test_overlapping_intervals_merge_before_summing(tmp_path: Path) -> None:
    ledger_path = str(tmp_path / "blocked-intervals.jsonl")
    version = "v2.24.0"

    # 40-minute interval: 09:00 -> 09:40
    append_block_start(ledger_path, version, "halt:project", "2026-09-18T09:00:00+00:00")
    append_block_end(ledger_path, version, "halt:project", "2026-09-18T09:40:00+00:00")

    # 20-minute interval overlapping the first by 10 minutes: 09:30 -> 09:50
    append_block_start(ledger_path, version, "task:CODER-example", "2026-09-18T09:30:00+00:00")
    append_block_end(ledger_path, version, "task:CODER-example", "2026-09-18T09:50:00+00:00")

    minutes = blocked_minutes(ledger_path, version, WINDOW_OPENED, WINDOW_CLOSED)
    assert minutes == 50.0


def test_non_overlapping_intervals_sum_without_merging(tmp_path: Path) -> None:
    ledger_path = str(tmp_path / "blocked-intervals.jsonl")
    version = "v2.24.0"

    append_block_start(ledger_path, version, "halt:project", "2026-09-18T09:00:00+00:00")
    append_block_end(ledger_path, version, "halt:project", "2026-09-18T09:10:00+00:00")

    append_block_start(ledger_path, version, "halt:project", "2026-09-18T10:00:00+00:00")
    append_block_end(ledger_path, version, "halt:project", "2026-09-18T10:05:00+00:00")

    minutes = blocked_minutes(ledger_path, version, WINDOW_OPENED, WINDOW_CLOSED)
    assert minutes == 15.0


# ---------------------------------------------------------------------------
# Clamp to the RC's own opened-to-closed window
# ---------------------------------------------------------------------------


def test_interval_starting_before_rc_opened_contributes_only_the_portion_inside_window(
    tmp_path: Path,
) -> None:
    ledger_path = str(tmp_path / "blocked-intervals.jsonl")
    version = "v2.24.0"

    # Starts 30 minutes before the window opens, ends 10 minutes after it
    # opens -- only the 10 minutes inside the window should count.
    append_block_start(ledger_path, version, "halt:project", "2026-09-17T23:30:00+00:00")
    append_block_end(ledger_path, version, "halt:project", "2026-09-18T00:10:00+00:00")

    minutes = blocked_minutes(ledger_path, version, WINDOW_OPENED, WINDOW_CLOSED)
    assert minutes == 10.0


def test_interval_still_open_at_rc_closed_contributes_only_the_portion_inside_window(
    tmp_path: Path,
) -> None:
    ledger_path = str(tmp_path / "blocked-intervals.jsonl")
    version = "v2.24.0"

    # Opens 20 minutes before the window closes and never closes (the RC
    # shipped while still blocked) -- only the 20 minutes inside the
    # window should count.
    append_block_start(ledger_path, version, "halt:project", "2026-09-18T23:40:00+00:00")

    minutes = blocked_minutes(ledger_path, version, WINDOW_OPENED, WINDOW_CLOSED)
    assert minutes == 20.0


def test_interval_entirely_outside_window_contributes_zero(tmp_path: Path) -> None:
    ledger_path = str(tmp_path / "blocked-intervals.jsonl")
    version = "v2.24.0"

    # Entirely before the window opens.
    append_block_start(ledger_path, version, "halt:project", "2026-09-10T00:00:00+00:00")
    append_block_end(ledger_path, version, "halt:project", "2026-09-10T01:00:00+00:00")

    minutes = blocked_minutes(ledger_path, version, WINDOW_OPENED, WINDOW_CLOSED)
    assert minutes == 0.0


def test_clamp_and_merge_combine_for_the_deliverable_fixture(tmp_path: Path) -> None:
    """One ledger exercising all three deliverable behaviours together:
    an overlap-merged pair inside the window, a pre-open start clamped at
    the window edge, a still-open interval clamped at ship, and a fully
    outside interval contributing nothing."""
    ledger_path = str(tmp_path / "blocked-intervals.jsonl")
    version = "v2.24.0"

    # Overlap-merge pair: 40 min + 20 min overlapping 10 min = 50 min.
    append_block_start(ledger_path, version, "halt:project", "2026-09-18T09:00:00+00:00")
    append_block_end(ledger_path, version, "halt:project", "2026-09-18T09:40:00+00:00")
    append_block_start(ledger_path, version, "task:example", "2026-09-18T09:30:00+00:00")
    append_block_end(ledger_path, version, "task:example", "2026-09-18T09:50:00+00:00")

    # Starts before window opens, clamped: contributes 10 min.
    append_block_start(ledger_path, version, "halt:global", "2026-09-17T23:30:00+00:00")
    append_block_end(ledger_path, version, "halt:global", "2026-09-18T00:10:00+00:00")

    # Still open at ship, clamped: contributes 20 min.
    append_block_start(ledger_path, version, "task:still-open", "2026-09-18T23:40:00+00:00")

    # Entirely outside the window: contributes 0.
    append_block_start(ledger_path, version, "halt:unrelated", "2026-08-01T00:00:00+00:00")
    append_block_end(ledger_path, version, "halt:unrelated", "2026-08-01T01:00:00+00:00")

    minutes = blocked_minutes(ledger_path, version, WINDOW_OPENED, WINDOW_CLOSED)
    assert minutes == 50.0 + 10.0 + 20.0


# ---------------------------------------------------------------------------
# Malformed ledger — dual-channel refusal, no number, no rewrite
# ---------------------------------------------------------------------------


def test_unparseable_line_refuses_with_dual_channel_error_and_no_number(tmp_path: Path) -> None:
    ledger_path = tmp_path / "blocked-intervals.jsonl"
    ledger_path.write_text("not json at all\n", encoding="utf-8")
    before = ledger_path.read_text(encoding="utf-8")

    with pytest.raises(LedgerError) as excinfo:
        blocked_minutes(str(ledger_path), "v2.24.0", WINDOW_OPENED, WINDOW_CLOSED)

    exc = excinfo.value
    assert exc.error == "blocked-ledger.malformed"
    assert exc.message
    assert exc.remedy
    rendered = str(exc)
    assert rendered.startswith("error:   blocked-ledger.malformed")
    assert "message:" in rendered
    assert "remedy:" in rendered

    # The refusal never rewrites the ledger.
    assert ledger_path.read_text(encoding="utf-8") == before


def test_line_missing_required_field_refuses_as_malformed(tmp_path: Path) -> None:
    ledger_path = tmp_path / "blocked-intervals.jsonl"
    record = {"version": "v2.24.0", "kind": "block-start", "source": "halt:project"}
    ledger_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(LedgerError) as excinfo:
        blocked_minutes(str(ledger_path), "v2.24.0", WINDOW_OPENED, WINDOW_CLOSED)
    assert excinfo.value.error == "blocked-ledger.malformed"


def test_out_of_vocabulary_kind_refuses_as_malformed(tmp_path: Path) -> None:
    ledger_path = tmp_path / "blocked-intervals.jsonl"
    record = {
        "version": "v2.24.0",
        "kind": "block-pause",
        "source": "halt:project",
        "timestamp": "2026-09-18T09:00:00+00:00",
    }
    ledger_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(LedgerError) as excinfo:
        blocked_minutes(str(ledger_path), "v2.24.0", WINDOW_OPENED, WINDOW_CLOSED)
    assert excinfo.value.error == "blocked-ledger.malformed"


def test_orphaned_block_end_refuses_as_malformed(tmp_path: Path) -> None:
    ledger_path = tmp_path / "blocked-intervals.jsonl"
    record = {
        "version": "v2.24.0",
        "kind": "block-end",
        "source": "halt:project",
        "timestamp": "2026-09-18T09:00:00+00:00",
    }
    ledger_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(LedgerError) as excinfo:
        blocked_minutes(str(ledger_path), "v2.24.0", WINDOW_OPENED, WINDOW_CLOSED)
    assert excinfo.value.error == "blocked-ledger.malformed"


def test_invalid_window_timestamp_refuses_as_malformed(tmp_path: Path) -> None:
    ledger_path = str(tmp_path / "blocked-intervals.jsonl")
    with pytest.raises(LedgerError):
        blocked_minutes(ledger_path, "v2.24.0", "not-a-timestamp", WINDOW_CLOSED)


# ---------------------------------------------------------------------------
# append_block_start / append_block_end pairing rules
# ---------------------------------------------------------------------------


def test_second_block_start_for_open_source_refuses_and_writes_nothing(tmp_path: Path) -> None:
    ledger_path = str(tmp_path / "blocked-intervals.jsonl")
    version = "v2.24.0"
    append_block_start(ledger_path, version, "halt:project", "2026-09-18T09:00:00+00:00")
    before = Path(ledger_path).read_text(encoding="utf-8")

    with pytest.raises(LedgerError):
        append_block_start(ledger_path, version, "halt:project", "2026-09-18T09:10:00+00:00")

    assert Path(ledger_path).read_text(encoding="utf-8") == before


def test_block_end_with_no_open_start_refuses_and_writes_nothing(tmp_path: Path) -> None:
    ledger_path = str(tmp_path / "blocked-intervals.jsonl")
    version = "v2.24.0"

    with pytest.raises(LedgerError):
        append_block_end(ledger_path, version, "halt:project", "2026-09-18T09:00:00+00:00")

    assert not Path(ledger_path).exists()


def test_concurrent_sources_open_and_close_independently(tmp_path: Path) -> None:
    ledger_path = str(tmp_path / "blocked-intervals.jsonl")
    version = "v2.24.0"

    append_block_start(ledger_path, version, "halt:project", "2026-09-18T09:00:00+00:00")
    append_block_start(ledger_path, version, "task:example", "2026-09-18T09:05:00+00:00")
    append_block_end(ledger_path, version, "task:example", "2026-09-18T09:15:00+00:00")
    append_block_end(ledger_path, version, "halt:project", "2026-09-18T09:20:00+00:00")

    records = parse_records(ledger_path)
    assert len(records) == 4

    minutes = blocked_minutes(ledger_path, version, WINDOW_OPENED, WINDOW_CLOSED)
    assert minutes == 20.0  # 09:00 -> 09:20, merged (task interval nested inside)


# ---------------------------------------------------------------------------
# Path convention helper
# ---------------------------------------------------------------------------


def test_ledger_path_for_project_matches_documented_convention() -> None:
    path = ledger_path_for_project("/kanban-root", "pgai-agent-kanban")
    assert path == "/kanban-root/projects/pgai-agent-kanban/metrics/blocked-intervals.jsonl"
