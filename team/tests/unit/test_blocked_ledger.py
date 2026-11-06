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
  - The CLI's refusal-ledger routing: with another project ambient, an
    ``end`` for ``--project`` B with no open start (blocked-ledger.no-op)
    is recorded in no project's refusal ledger, and a genuine refusal (a
    malformed ledger) is recorded in B's, not the ambient project's.
  - The optional class / cause / reason_code fields: written by the
    writers and the CLI flags and preserved on read; absent on an old-shape
    record, they read as class "any" and cause "chain"; out-of-vocabulary
    values handed to a writer refuse and write nothing.
  - A block recorded with no Active RC (NO_RC_VERSION) never counts toward
    a real RC's blocked_minutes() total.
  - The escalation mark: an open block marked escalated reads class
    "human" with every earlier line byte-for-byte unchanged; marking a
    block that is not open, or already human, is a no-op refusal.
  - The new fields and escalation lines never change blocked_minutes().

All tests use tmp_path; no live filesystem paths. Function names describe
behaviour under test, never bug/task IDs (roles/TESTER.md "Test Authoring
Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pgai_agent_kanban.lib.error_contract import parse_error_contract
from pgai_agent_kanban.metrics.blocked_ledger import (
    ERROR_NO_OP,
    NO_RC_VERSION,
    LedgerError,
    append_block_end,
    append_block_start,
    block_intervals,
    blocked_minutes,
    ledger_path_for_project,
    mark_block_escalated,
    parse_records,
    record_cause,
    record_class,
    record_reason_code,
    version_for_active_rc,
)
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = Path(__file__).resolve().parents[2]

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


# ---------------------------------------------------------------------------
# CLI refusal-ledger routing
# ---------------------------------------------------------------------------


def _two_project_root(tmp_path: Path) -> Path:
    root = tmp_path / "kanban"
    for name in ("alpha", "beta"):
        (root / "projects" / name).mkdir(parents=True)
    (root / "projects.cfg").write_text(
        "[project:alpha]\npriority = 1\n\n[project:beta]\npriority = 2\n", encoding="utf-8"
    )
    return root


def _run_cli(tmp_path: Path, root: Path, *args: str) -> subprocess.CompletedProcess:
    """Run the ledger CLI with project alpha ambient, as a wake cycle would."""
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={
            "PYTHONPATH": str(_TEAM_DIR),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_PROJECT_NAME": "alpha",
        },
    )
    return subprocess.run(
        [sys.executable, "-m", "pgai_agent_kanban.metrics.blocked_ledger", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=60,
        check=False,
    )


def _refusal_ledger(root: Path, project: str) -> Path:
    return root / "projects" / project / "refusal-ledger.md"


def test_cli_no_op_end_for_another_project_is_recorded_in_no_refusal_ledger(tmp_path: Path) -> None:
    root = _two_project_root(tmp_path)
    ledger = root / "projects" / "beta" / "metrics" / "blocked-intervals.jsonl"

    result = _run_cli(
        tmp_path,
        root,
        "end",
        str(ledger),
        "v0.30.8",
        "halt:project",
        "2026-10-18T02:40:29+00:00",
        "--project",
        "beta",
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert parse_error_contract(result.stderr)["error"] == "blocked-ledger.no-op"
    assert not _refusal_ledger(root, "alpha").exists()
    assert not _refusal_ledger(root, "beta").exists()


def test_cli_genuine_refusal_is_recorded_against_the_named_project(tmp_path: Path) -> None:
    root = _two_project_root(tmp_path)
    ledger = root / "projects" / "beta" / "metrics" / "blocked-intervals.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("not json\n", encoding="utf-8")

    result = _run_cli(
        tmp_path,
        root,
        "start",
        str(ledger),
        "v0.30.8",
        "halt:project",
        "2026-10-18T02:40:29+00:00",
        "--project",
        "beta",
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert parse_error_contract(result.stderr)["error"] == "blocked-ledger.malformed"
    assert not _refusal_ledger(root, "alpha").exists()
    assert "blocked-ledger.malformed" in _refusal_ledger(root, "beta").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Optional fields: class, cause, reason_code
# ---------------------------------------------------------------------------


def test_writers_preserve_class_cause_and_reason_code_through_parse_records(
    tmp_path: Path,
) -> None:
    ledger_path = str(tmp_path / "blocked-intervals.jsonl")
    version = "v2.24.0"
    source = "task:example-task"

    append_block_start(
        ledger_path,
        version,
        source,
        "2026-09-18T09:00:00+00:00",
        block_class="any",
        cause="chain",
        reason_code="agent_unclassified",
    )
    append_block_end(
        ledger_path,
        version,
        source,
        "2026-09-18T09:30:00+00:00",
        block_class="human",
        cause="operator",
        reason_code="reset",
    )

    start, end = parse_records(ledger_path)
    assert (start["kind"], start["class"], start["cause"], start["reason_code"]) == (
        "block-start",
        "any",
        "chain",
        "agent_unclassified",
    )
    assert (end["kind"], end["class"], end["cause"], end["reason_code"]) == (
        "block-end",
        "human",
        "operator",
        "reset",
    )
    assert [record_class(r) for r in (start, end)] == ["any", "human"]
    assert [record_cause(r) for r in (start, end)] == ["chain", "operator"]
    assert [record_reason_code(r) for r in (start, end)] == ["agent_unclassified", "reset"]


def test_writer_without_optional_fields_writes_the_original_four_field_shape(
    tmp_path: Path,
) -> None:
    ledger_path = str(tmp_path / "blocked-intervals.jsonl")
    append_block_start(ledger_path, "v2.24.0", "halt:project", "2026-09-18T09:00:00+00:00")

    (record,) = parse_records(ledger_path)
    assert sorted(record) == ["kind", "source", "timestamp", "version"]


def test_old_shape_records_read_as_cause_chain_and_class_any(tmp_path: Path) -> None:
    ledger_path = tmp_path / "blocked-intervals.jsonl"
    old_shape = [
        {
            "kind": "block-start",
            "source": "halt:project",
            "timestamp": "2026-09-20T02:22:14+00:00",
            "version": "v2.24.7",
        },
        {
            "kind": "block-start",
            "source": "task:example-task",
            "timestamp": "2026-09-20T02:22:32+00:00",
            "version": "v2.24.7",
        },
        {"kind": "block-end", "source": "halt:project", "timestamp": "2026-09-20T13:02:15+00:00", "version": "v2.24.7"},
        {
            "kind": "block-end",
            "source": "task:example-task",
            "timestamp": "2026-09-20T13:05:00+00:00",
            "version": "v2.24.7",
        },
    ]
    ledger_path.write_text("".join(json.dumps(r) + "\n" for r in old_shape), encoding="utf-8")

    records = parse_records(str(ledger_path))
    assert len(records) == 4
    for record in records:
        assert record_cause(record) == "chain"
        assert record_class(record) == "any"
        assert record_reason_code(record) is None

    blocks = block_intervals(str(ledger_path))
    assert [(b["source"], b["cause"], b["class"]) for b in blocks] == [
        ("halt:project", "chain", "any"),
        ("task:example-task", "chain", "any"),
    ]


def test_out_of_vocabulary_optional_field_on_disk_reads_as_its_default(tmp_path: Path) -> None:
    ledger_path = tmp_path / "blocked-intervals.jsonl"
    record = {
        "version": "v2.24.0",
        "kind": "block-start",
        "source": "halt:project",
        "timestamp": "2026-09-18T09:00:00+00:00",
        "class": "maybe",
        "cause": " Operator ",
    }
    ledger_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    (parsed,) = parse_records(str(ledger_path))
    assert record_class(parsed) == "any"
    assert record_cause(parsed) == "operator"


@pytest.mark.parametrize("field, value", [("block_class", "sometimes"), ("cause", "weather")])
def test_writer_refuses_out_of_vocabulary_class_or_cause_and_writes_nothing(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    ledger_path = tmp_path / "blocked-intervals.jsonl"

    with pytest.raises(LedgerError) as excinfo:
        append_block_start(
            str(ledger_path),
            "v2.24.0",
            "halt:project",
            "2026-09-18T09:00:00+00:00",
            **{field: value},
        )

    assert excinfo.value.error == "blocked-ledger.invalid-field"
    assert value in excinfo.value.message
    assert excinfo.value.remedy
    assert not ledger_path.exists()


def test_new_fields_and_escalation_never_change_blocked_minutes(tmp_path: Path) -> None:
    plain = str(tmp_path / "plain.jsonl")
    rich = str(tmp_path / "rich.jsonl")
    version = "v2.24.0"

    append_block_start(plain, version, "halt:project", "2026-09-18T09:00:00+00:00")
    append_block_end(plain, version, "halt:project", "2026-09-18T09:40:00+00:00")
    append_block_start(plain, version, "task:example-task", "2026-09-18T23:40:00+00:00")

    append_block_start(
        rich,
        version,
        "halt:project",
        "2026-09-18T09:00:00+00:00",
        cause="operator",
    )
    append_block_end(rich, version, "halt:project", "2026-09-18T09:40:00+00:00", block_class="human")
    append_block_start(
        rich,
        version,
        "task:example-task",
        "2026-09-18T23:40:00+00:00",
        block_class="any",
        reason_code="agent_unclassified",
    )
    mark_block_escalated(rich, version, "task:example-task", "2026-09-18T23:50:00+00:00")

    expected = blocked_minutes(plain, version, WINDOW_OPENED, WINDOW_CLOSED)
    assert expected == 60.0
    assert blocked_minutes(rich, version, WINDOW_OPENED, WINDOW_CLOSED) == expected


# ---------------------------------------------------------------------------
# No Active RC attribution
# ---------------------------------------------------------------------------


def test_version_for_active_rc_maps_none_to_the_no_rc_version() -> None:
    assert version_for_active_rc("none") == NO_RC_VERSION
    assert version_for_active_rc(" None ") == NO_RC_VERSION
    assert version_for_active_rc("") == NO_RC_VERSION
    assert version_for_active_rc(None) == NO_RC_VERSION
    assert version_for_active_rc("v2.24.0") == "v2.24.0"


def test_block_with_no_active_rc_never_counts_toward_an_rc_total(tmp_path: Path) -> None:
    ledger_path = str(tmp_path / "blocked-intervals.jsonl")
    version = version_for_active_rc("none")
    append_block_start(ledger_path, version, "halt:project", "2026-09-18T09:00:00+00:00")
    append_block_end(ledger_path, version, "halt:project", "2026-09-18T09:30:00+00:00")

    # A real RC whose window covers the block's whole span.
    assert blocked_minutes(ledger_path, "v2.24.0", WINDOW_OPENED, WINDOW_CLOSED) == 0.0

    # The block itself is on record, under the no-RC version.
    (block,) = block_intervals(ledger_path)
    assert block["version"] == NO_RC_VERSION
    assert (block["start"], block["end"]) == (
        "2026-09-18T09:00:00+00:00",
        "2026-09-18T09:30:00+00:00",
    )


# ---------------------------------------------------------------------------
# Escalation mark and block class
# ---------------------------------------------------------------------------


def test_marking_an_open_block_escalated_reads_human_and_leaves_earlier_lines_unchanged(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "blocked-intervals.jsonl"
    version = "v2.24.0"
    source = "task:example-task"
    append_block_start(str(ledger_path), "v2.23.0", "halt:project", "2026-09-17T08:00:00+00:00")
    append_block_start(
        str(ledger_path),
        version,
        source,
        "2026-09-18T09:00:00+00:00",
        reason_code="agent_unclassified",
    )
    (block,) = block_intervals(str(ledger_path), version)
    assert block["class"] == "any"
    before = ledger_path.read_bytes()

    mark_block_escalated(
        str(ledger_path),
        version,
        source,
        "2026-09-18T09:20:00+00:00",
        reason_code="escalate",
    )

    after = ledger_path.read_bytes()
    assert after.startswith(before)
    assert after[len(before) :].count(b"\n") == 1
    (block,) = block_intervals(str(ledger_path), version)
    assert block["class"] == "human"
    assert block["escalated"] is True
    assert block["escalated_at"] == "2026-09-18T09:20:00+00:00"
    assert block["end"] is None
    assert block["reason_code"] == "agent_unclassified"


def test_escalating_a_block_that_is_not_open_is_a_no_op_and_writes_nothing(tmp_path: Path) -> None:
    ledger_path = tmp_path / "blocked-intervals.jsonl"
    version = "v2.24.0"
    append_block_start(str(ledger_path), version, "halt:project", "2026-09-18T09:00:00+00:00")
    append_block_end(str(ledger_path), version, "halt:project", "2026-09-18T09:10:00+00:00")
    before = ledger_path.read_bytes()

    with pytest.raises(LedgerError) as excinfo:
        mark_block_escalated(str(ledger_path), version, "halt:project", "2026-09-18T09:20:00+00:00")

    assert excinfo.value.error == ERROR_NO_OP
    assert ledger_path.read_bytes() == before


def test_escalating_a_block_that_already_reads_human_is_a_no_op_and_writes_nothing(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "blocked-intervals.jsonl"
    version = "v2.24.0"
    append_block_start(str(ledger_path), version, "task:a", "2026-09-18T09:00:00+00:00")
    mark_block_escalated(str(ledger_path), version, "task:a", "2026-09-18T09:05:00+00:00")
    append_block_start(
        str(ledger_path),
        version,
        "task:b",
        "2026-09-18T09:00:00+00:00",
        block_class="human",
    )
    before = ledger_path.read_bytes()

    for source in ("task:a", "task:b"):
        with pytest.raises(LedgerError) as excinfo:
            mark_block_escalated(str(ledger_path), version, source, "2026-09-18T09:30:00+00:00")
        assert excinfo.value.error == ERROR_NO_OP

    assert ledger_path.read_bytes() == before


def test_block_class_follows_its_resolution_class(tmp_path: Path) -> None:
    ledger_path = str(tmp_path / "blocked-intervals.jsonl")
    version = "v2.24.0"
    append_block_start(ledger_path, version, "task:requeued", "2026-09-18T09:00:00+00:00")
    append_block_end(
        ledger_path,
        version,
        "task:requeued",
        "2026-09-18T09:10:00+00:00",
        block_class="any",
        reason_code="requeue",
    )
    append_block_start(ledger_path, version, "task:reset", "2026-09-18T10:00:00+00:00")
    append_block_end(
        ledger_path,
        version,
        "task:reset",
        "2026-09-18T10:10:00+00:00",
        block_class="human",
        reason_code="reset",
    )

    blocks = {b["source"]: b for b in block_intervals(ledger_path, version)}
    assert blocks["task:requeued"]["class"] == "any"
    assert blocks["task:requeued"]["resolution_class"] == "any"
    assert blocks["task:requeued"]["resolution_reason_code"] == "requeue"
    assert blocks["task:reset"]["class"] == "human"
    assert blocks["task:reset"]["resolution_class"] == "human"
    assert blocks["task:reset"]["escalated"] is False


def test_orphaned_escalation_line_refuses_block_intervals_as_malformed(tmp_path: Path) -> None:
    ledger_path = tmp_path / "blocked-intervals.jsonl"
    record = {
        "version": "v2.24.0",
        "kind": "block-escalate",
        "source": "task:a",
        "timestamp": "2026-09-18T09:00:00+00:00",
        "class": "human",
    }
    ledger_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(LedgerError) as excinfo:
        block_intervals(str(ledger_path))
    assert excinfo.value.error == "blocked-ledger.malformed"


def test_cli_writes_optional_fields_and_the_escalation_mark(tmp_path: Path) -> None:
    root = _two_project_root(tmp_path)
    ledger = root / "projects" / "beta" / "metrics" / "blocked-intervals.jsonl"
    common = [str(ledger), NO_RC_VERSION, "task:example-task"]

    steps = [
        [
            "start",
            *common,
            "2026-10-18T02:00:00+00:00",
            "--class",
            "ANY",
            "--cause",
            "chain",
            "--reason-code",
            "agent_unclassified",
            "--project",
            "beta",
        ],
        ["escalate", *common, "2026-10-18T02:10:00+00:00", "--reason-code", "escalate", "--project", "beta"],
        [
            "end",
            *common,
            "2026-10-18T02:40:00+00:00",
            "--class",
            "human",
            "--reason-code",
            "reset",
            "--project",
            "beta",
        ],
    ]
    for step in steps:
        result = _run_cli(tmp_path, root, *step)
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout) == {"written": True}

    start, escalate, end = parse_records(str(ledger))
    assert (start["class"], start["cause"], start["reason_code"]) == ("any", "chain", "agent_unclassified")
    assert (escalate["kind"], escalate["class"], escalate["reason_code"]) == ("block-escalate", "human", "escalate")
    assert (end["class"], end["reason_code"], "cause" in end) == ("human", "reset", False)
    (block,) = block_intervals(str(ledger))
    assert block["class"] == "human"
    assert block["version"] == NO_RC_VERSION


def test_cli_refuses_an_out_of_vocabulary_class_as_a_usage_error(tmp_path: Path) -> None:
    root = _two_project_root(tmp_path)
    ledger = root / "projects" / "beta" / "metrics" / "blocked-intervals.jsonl"

    result = _run_cli(
        tmp_path,
        root,
        "start",
        str(ledger),
        "v0.30.8",
        "halt:project",
        "2026-10-18T02:40:29+00:00",
        "--class",
        "sometimes",
        "--project",
        "beta",
    )

    assert result.returncode == 2
    assert "--class" in result.stderr
    assert not ledger.exists()
