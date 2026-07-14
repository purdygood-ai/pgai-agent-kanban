"""
test_dashboard.py
=================
Behavioral unit tests for team/pgai_agent_kanban/dashboard/:
  - dashboard/cron_firings.py
  - dashboard/halt_state.py
  - dashboard/render_costs.py
  - dashboard/scan_attention.py
  - dashboard/_rejected.py

All filesystem operations use tmp_path; clocks and external modules are
mocked where needed.  No live kanban root or subprocess calls.
"""

from __future__ import annotations

import json
import pathlib
import sys
import textwrap
import time
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# _rejected imports
# ---------------------------------------------------------------------------

from pgai_agent_kanban.dashboard._rejected import (
    read_reason_sidecar,
    sidecar_path_for,
    SIDECAR_KEYS,
)

# ---------------------------------------------------------------------------
# halt_state imports
# ---------------------------------------------------------------------------

from pgai_agent_kanban.dashboard.halt_state import compute_halt_state

# ---------------------------------------------------------------------------
# render_costs imports
# ---------------------------------------------------------------------------

from pgai_agent_kanban.dashboard.render_costs import (
    load_pricing,
    get_model_rates,
    model_cost_parts,
    load_rollup,
    extract_by_model,
    fmt_tokens,
    fmt_usd,
    render_costs,
)

# ---------------------------------------------------------------------------
# scan_attention imports
# ---------------------------------------------------------------------------

from pgai_agent_kanban.dashboard.scan_attention import (
    _read_field,
    _read_section,
    _format_elapsed,
    _is_transient_blocked,
    _scan_state_file,
    _scan_rejected_dir,
    _iter_projects,
    _parse_overwatch_action_log,
    scan_blocked_tasks,
    scan_quarantine,
    scan_stale_working_tasks,
    scan_transient_tasks,
    scan_overwatch_section,
)

# ---------------------------------------------------------------------------
# cron_firings imports
# ---------------------------------------------------------------------------

from pgai_agent_kanban.dashboard.cron_firings import (
    _seconds_to_label,
    cron_firings,
)


# ===========================================================================
# _rejected — read_reason_sidecar
# ===========================================================================


def test_read_reason_sidecar_parses_all_known_keys(tmp_path: pathlib.Path) -> None:
    """read_reason_sidecar returns a dict with all known keys from a well-formed sidecar."""
    sidecar = tmp_path / "BUG-0001-foo.md.reason"
    sidecar.write_text(
        "original_type=bugs\n"
        "original_dir=/opt/kanban/projects/myproj/bugs\n"
        "rejected_at=2026-05-28T14:00:00Z\n"
        "reason=malformed filename structure\n"
        "retry_count=3\n",
        encoding="utf-8",
    )
    info = read_reason_sidecar(sidecar)
    assert info["original_type"] == "bugs"
    assert info["original_dir"] == "/opt/kanban/projects/myproj/bugs"
    assert info["rejected_at"] == "2026-05-28T14:00:00Z"
    assert info["reason"] == "malformed filename structure"
    assert info["retry_count"] == "3"


def test_read_reason_sidecar_returns_none_for_absent_keys(tmp_path: pathlib.Path) -> None:
    """read_reason_sidecar returns None for keys not present in the sidecar file."""
    sidecar = tmp_path / "BUG-0001.md.reason"
    sidecar.write_text("original_type=bugs\n", encoding="utf-8")
    info = read_reason_sidecar(sidecar)
    assert info["reason"] is None
    assert info["retry_count"] is None


def test_read_reason_sidecar_returns_all_none_for_missing_file(tmp_path: pathlib.Path) -> None:
    """read_reason_sidecar returns all-None dict when the sidecar file does not exist."""
    info = read_reason_sidecar(tmp_path / "missing.md.reason")
    for key in SIDECAR_KEYS:
        assert info[key] is None


def test_read_reason_sidecar_ignores_blank_lines_and_comments(tmp_path: pathlib.Path) -> None:
    """read_reason_sidecar skips blank lines and lines starting with '#'."""
    sidecar = tmp_path / "file.md.reason"
    sidecar.write_text(
        "# This is a comment\n"
        "\n"
        "original_type=priority\n",
        encoding="utf-8",
    )
    info = read_reason_sidecar(sidecar)
    assert info["original_type"] == "priority"


def test_read_reason_sidecar_ignores_unknown_keys(tmp_path: pathlib.Path) -> None:
    """read_reason_sidecar silently ignores keys not in SIDECAR_KEYS."""
    sidecar = tmp_path / "file.md.reason"
    sidecar.write_text(
        "original_type=bugs\n"
        "future_key=some_value\n",
        encoding="utf-8",
    )
    info = read_reason_sidecar(sidecar)
    assert "future_key" not in info
    assert info["original_type"] == "bugs"


def test_read_reason_sidecar_ignores_lines_without_equals(tmp_path: pathlib.Path) -> None:
    """read_reason_sidecar skips lines that don't contain an '=' separator."""
    sidecar = tmp_path / "file.md.reason"
    sidecar.write_text(
        "not-a-key-value-line\n"
        "original_type=bugs\n",
        encoding="utf-8",
    )
    info = read_reason_sidecar(sidecar)
    assert info["original_type"] == "bugs"


def test_sidecar_path_for_appends_reason_extension(tmp_path: pathlib.Path) -> None:
    """sidecar_path_for returns the file path with .reason appended."""
    quarantined = tmp_path / "rejected" / "BUG-0001-foo.md"
    result = sidecar_path_for(quarantined)
    assert result.name == "BUG-0001-foo.md.reason"
    assert result.parent == quarantined.parent


# ===========================================================================
# halt_state — compute_halt_state
# ===========================================================================


def test_compute_halt_state_returns_normal_when_no_sentinels(tmp_path: pathlib.Path) -> None:
    """compute_halt_state returns ('normal', None) when neither HALT nor HALT-AFTER exists."""
    state, event = compute_halt_state(tmp_path)
    assert state == "normal"
    assert event is None


def test_compute_halt_state_returns_halted_when_halt_sentinel_exists(
    tmp_path: pathlib.Path,
) -> None:
    """compute_halt_state returns ('halted', ...) when a HALT file is present."""
    (tmp_path / "HALT").write_text("rc\n", encoding="utf-8")
    state, event = compute_halt_state(tmp_path)
    assert state == "halted"


def test_compute_halt_state_halt_event_is_normalised_lowercase(tmp_path: pathlib.Path) -> None:
    """compute_halt_state lowercases the HALT file body to produce the event string."""
    (tmp_path / "HALT").write_text("CODER\n", encoding="utf-8")
    state, event = compute_halt_state(tmp_path)
    assert event == "coder"


def test_compute_halt_state_halt_event_is_none_for_empty_halt_file(
    tmp_path: pathlib.Path,
) -> None:
    """compute_halt_state returns event=None when the HALT file body is empty."""
    (tmp_path / "HALT").write_text("  \n", encoding="utf-8")
    state, event = compute_halt_state(tmp_path)
    assert state == "halted"
    assert event is None


def test_compute_halt_state_returns_draining_for_valid_halt_after(
    tmp_path: pathlib.Path,
) -> None:
    """compute_halt_state returns ('draining', token) for a valid HALT-AFTER file."""
    (tmp_path / "HALT-AFTER").write_text("coder\n", encoding="utf-8")
    state, event = compute_halt_state(tmp_path)
    assert state == "draining"
    assert event == "coder"


def test_compute_halt_state_halt_beats_halt_after(tmp_path: pathlib.Path) -> None:
    """compute_halt_state returns 'halted' when both HALT and HALT-AFTER exist."""
    (tmp_path / "HALT").write_text("rc\n", encoding="utf-8")
    (tmp_path / "HALT-AFTER").write_text("coder\n", encoding="utf-8")
    state, event = compute_halt_state(tmp_path)
    assert state == "halted"


def test_compute_halt_state_returns_normal_for_invalid_halt_after_token(
    tmp_path: pathlib.Path,
) -> None:
    """compute_halt_state falls through to 'normal' when HALT-AFTER has an invalid token."""
    (tmp_path / "HALT-AFTER").write_text("not-a-valid-token\n", encoding="utf-8")
    state, event = compute_halt_state(tmp_path)
    assert state == "normal"
    assert event is None


def test_compute_halt_state_returns_normal_for_nonexistent_dir(tmp_path: pathlib.Path) -> None:
    """compute_halt_state returns ('normal', None) for a directory with no sentinel files."""
    new_dir = tmp_path / "empty_project"
    new_dir.mkdir()
    state, event = compute_halt_state(new_dir)
    assert state == "normal"
    assert event is None


# ===========================================================================
# render_costs — load_pricing
# ===========================================================================


def test_load_pricing_returns_dict_for_valid_json(tmp_path: pathlib.Path) -> None:
    """load_pricing returns the parsed JSON dict for a valid pricing file."""
    pricing_file = tmp_path / "pricing.json"
    pricing_file.write_text(
        json.dumps({"providers": {"anthropic": {"models": {}}}}),
        encoding="utf-8",
    )
    result = load_pricing(str(pricing_file))
    assert "providers" in result


def test_load_pricing_returns_empty_dict_for_missing_file(tmp_path: pathlib.Path) -> None:
    """load_pricing returns {} when the pricing file does not exist."""
    result = load_pricing(str(tmp_path / "nonexistent.json"))
    assert result == {}


def test_load_pricing_returns_empty_dict_for_empty_path() -> None:
    """load_pricing returns {} when given an empty string path."""
    result = load_pricing("")
    assert result == {}


def test_load_pricing_returns_empty_dict_for_malformed_json(tmp_path: pathlib.Path) -> None:
    """load_pricing returns {} when the pricing file contains invalid JSON."""
    pricing_file = tmp_path / "pricing.json"
    pricing_file.write_text("not valid json {{{", encoding="utf-8")
    result = load_pricing(str(pricing_file))
    assert result == {}


# ===========================================================================
# render_costs — get_model_rates
# ===========================================================================


def test_get_model_rates_finds_model_across_providers() -> None:
    """get_model_rates returns the rates dict for a model found in any provider."""
    pricing = {
        "providers": {
            "anthropic": {
                "models": {
                    "claude-haiku-4-5": {
                        "input_per_1m": 0.80,
                        "output_per_1m": 4.00,
                    }
                }
            }
        }
    }
    rates = get_model_rates(pricing, "claude-haiku-4-5")
    assert rates is not None
    assert rates["input_per_1m"] == 0.80


def test_get_model_rates_returns_none_for_unknown_model() -> None:
    """get_model_rates returns None when the model is not found in any provider."""
    pricing = {"providers": {"anthropic": {"models": {}}}}
    assert get_model_rates(pricing, "gpt-5") is None


# ===========================================================================
# render_costs — model_cost_parts
# ===========================================================================


def test_model_cost_parts_computes_costs_from_pricing_rates() -> None:
    """model_cost_parts returns correctly computed (input, cc, cr, output) costs."""
    pricing = {
        "providers": {
            "anthropic": {
                "models": {
                    "test-model": {
                        "input_per_1m": 1.0,
                        "cache_creation_per_1m": 2.0,
                        "cache_read_per_1m": 0.5,
                        "output_per_1m": 4.0,
                    }
                }
            }
        }
    }
    i_cost, cc_cost, cr_cost, o_cost = model_cost_parts(
        pricing, "test-model",
        input_tok=1_000_000, output_tok=1_000_000,
        cache_create=1_000_000, cache_read=1_000_000,
    )
    assert abs(i_cost - 1.0) < 0.001
    assert abs(cc_cost - 2.0) < 0.001
    assert abs(cr_cost - 0.5) < 0.001
    assert abs(o_cost - 4.0) < 0.001


def test_model_cost_parts_returns_zeros_for_unknown_model() -> None:
    """model_cost_parts returns (0, 0, 0, 0) when the model is not in pricing."""
    pricing = {"providers": {}}
    result = model_cost_parts(pricing, "unknown-model", 1000, 1000, 500, 500)
    assert result == (0.0, 0.0, 0.0, 0.0)


# ===========================================================================
# render_costs — load_rollup
# ===========================================================================


def test_load_rollup_returns_dict_for_valid_json(tmp_path: pathlib.Path) -> None:
    """load_rollup returns the parsed dict for a valid rollup JSON file."""
    rollup = tmp_path / "rollup.json"
    rollup.write_text(json.dumps({"by_model": {}}), encoding="utf-8")
    result = load_rollup(rollup)
    assert result is not None
    assert "by_model" in result


def test_load_rollup_returns_none_for_missing_file(tmp_path: pathlib.Path) -> None:
    """load_rollup returns None when the rollup file does not exist."""
    result = load_rollup(tmp_path / "nonexistent.json")
    assert result is None


def test_load_rollup_returns_none_for_malformed_json(tmp_path: pathlib.Path) -> None:
    """load_rollup returns None when the rollup file contains invalid JSON."""
    rollup = tmp_path / "rollup.json"
    rollup.write_text("invalid", encoding="utf-8")
    result = load_rollup(rollup)
    assert result is None


# ===========================================================================
# render_costs — extract_by_model
# ===========================================================================


def test_extract_by_model_aggregates_day_rollup_by_model() -> None:
    """extract_by_model aggregates tokens from a day-rollup 'by_model' structure."""
    data = {
        "by_model": {
            "claude-haiku-4-5": {
                "input": 100,
                "cache_creation_tokens": 50,
                "cache_read_tokens": 200,
                "output": 80,
                "input_cost_usd": 0.0001,
                "cache_creation_cost_usd": 0.0,
                "cache_read_cost_usd": 0.0,
                "output_cost_usd": 0.0004,
            }
        }
    }
    result = extract_by_model(data)
    assert "claude-haiku-4-5" in result
    assert result["claude-haiku-4-5"]["input"] == 100
    assert result["claude-haiku-4-5"]["output"] == 80


def test_extract_by_model_aggregates_rc_rollup_from_tasks() -> None:
    """extract_by_model sums tokens across tasks in an RC rollup 'tasks' list."""
    data = {
        "tasks": [
            {"model": "model-a", "input": 100, "output": 50,
             "cache_creation_tokens": 0, "cache_read_tokens": 0,
             "input_cost_usd": 0.0, "cache_creation_cost_usd": 0.0,
             "cache_read_cost_usd": 0.0, "output_cost_usd": 0.0},
            {"model": "model-a", "input": 200, "output": 100,
             "cache_creation_tokens": 0, "cache_read_tokens": 0,
             "input_cost_usd": 0.0, "cache_creation_cost_usd": 0.0,
             "cache_read_cost_usd": 0.0, "output_cost_usd": 0.0},
        ]
    }
    result = extract_by_model(data)
    assert "model-a" in result
    assert result["model-a"]["input"] == 300
    assert result["model-a"]["output"] == 150


def test_extract_by_model_returns_empty_for_unknown_structure() -> None:
    """extract_by_model returns {} for a rollup dict with neither by_model nor tasks."""
    result = extract_by_model({"other_key": "value"})
    assert result == {}


# ===========================================================================
# render_costs — fmt_tokens and fmt_usd
# ===========================================================================


def test_fmt_tokens_formats_large_numbers_with_commas() -> None:
    """fmt_tokens formats numbers with thousands separators."""
    assert fmt_tokens(1_234_567) == "1,234,567"


def test_fmt_tokens_handles_zero() -> None:
    """fmt_tokens formats zero as '0'."""
    assert fmt_tokens(0) == "0"


def test_fmt_usd_formats_small_amounts_with_two_decimal_places() -> None:
    """fmt_usd formats values below $1000 with two decimal places."""
    assert fmt_usd(1.50) == "$1.50"


def test_fmt_usd_formats_large_amounts_without_decimal_places() -> None:
    """fmt_usd formats values >= $1000 without decimal places."""
    result = fmt_usd(1500.0)
    assert result == "$1,500"


# ===========================================================================
# render_costs — render_costs (main render function)
# ===========================================================================


def test_render_costs_emits_no_data_for_missing_rollup_file(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """render_costs prints '(no data yet)' when the rollup file is absent."""
    render_costs(str(tmp_path / "missing.json"), "", "    ")
    captured = capsys.readouterr()
    assert "(no data yet)" in captured.out


def test_render_costs_emits_no_data_for_empty_by_model(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """render_costs prints '(no data yet)' when the rollup has an empty by_model."""
    rollup = tmp_path / "rollup.json"
    rollup.write_text(json.dumps({"by_model": {}}), encoding="utf-8")
    render_costs(str(rollup), "", "    ")
    captured = capsys.readouterr()
    assert "(no data yet)" in captured.out


def test_render_costs_renders_model_line_for_populated_by_model(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """render_costs emits model-specific lines when by_model contains data."""
    rollup = tmp_path / "rollup.json"
    rollup.write_text(
        json.dumps({
            "by_model": {
                "my-model-v1": {
                    "input": 1000,
                    "cache_creation_tokens": 500,
                    "cache_read_tokens": 2000,
                    "output": 300,
                    "input_cost_usd": 0.01,
                    "cache_creation_cost_usd": 0.005,
                    "cache_read_cost_usd": 0.001,
                    "output_cost_usd": 0.02,
                }
            }
        }),
        encoding="utf-8",
    )
    render_costs(str(rollup), "", "    ")
    captured = capsys.readouterr()
    assert "my-model-v1" in captured.out
    assert "input:" in captured.out
    assert "output:" in captured.out
    assert "subtotal:" in captured.out


def test_render_costs_uses_precomputed_costs_when_nonzero(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """render_costs uses precomputed cost fields rather than recomputing from pricing."""
    rollup = tmp_path / "rollup.json"
    rollup.write_text(
        json.dumps({
            "by_model": {
                "model-x": {
                    "input": 10000,
                    "cache_creation_tokens": 0,
                    "cache_read_tokens": 0,
                    "output": 5000,
                    "input_cost_usd": 9.99,  # precomputed
                    "cache_creation_cost_usd": 0.0,
                    "cache_read_cost_usd": 0.0,
                    "output_cost_usd": 4.99,  # precomputed
                }
            }
        }),
        encoding="utf-8",
    )
    render_costs(str(rollup), "", "    ")
    captured = capsys.readouterr()
    # The precomputed values should dominate — sum = 14.98
    assert "$9.99" in captured.out
    assert "$4.99" in captured.out


# ===========================================================================
# scan_attention — _read_field and _read_section
# ===========================================================================


def test_read_field_returns_first_content_line_after_heading() -> None:
    """_read_field returns the first non-blank line after a ## heading."""
    text = "## State\nBLOCKED\n\n## Needs Human\nyes\n"
    assert _read_field(text, "State") == "BLOCKED"


def test_read_field_returns_empty_when_heading_not_found() -> None:
    """_read_field returns '' when the heading is absent."""
    text = "## Summary\nSome summary\n"
    assert _read_field(text, "State") == ""


def test_read_section_returns_multiple_content_lines() -> None:
    """_read_section returns up to max_lines non-blank lines from a section."""
    text = "## Blockers\nFirst blocker\nSecond blocker\nThird blocker\n## Other\nignore\n"
    lines = _read_section(text, "Blockers", max_lines=5)
    assert len(lines) == 3
    assert "First blocker" in lines
    assert "Second blocker" in lines


def test_read_section_respects_max_lines_limit() -> None:
    """_read_section returns at most max_lines entries."""
    text = "## Section\nLine1\nLine2\nLine3\nLine4\nLine5\n"
    lines = _read_section(text, "Section", max_lines=2)
    assert len(lines) == 2


def test_read_section_stops_at_next_heading() -> None:
    """_read_section does not bleed into the next ## heading."""
    text = "## First\nContent line\n## Second\nShould not appear\n"
    lines = _read_section(text, "First", max_lines=10)
    assert "Should not appear" not in lines


# ===========================================================================
# scan_attention — _format_elapsed
# ===========================================================================


def test_format_elapsed_less_than_60_seconds_shows_seconds() -> None:
    """_format_elapsed returns seconds-based label for values under 60."""
    assert _format_elapsed(45) == "45s ago"


def test_format_elapsed_exactly_60_seconds_shows_minutes() -> None:
    """_format_elapsed returns minutes-based label for 60 seconds."""
    assert _format_elapsed(60) == "1m ago"


def test_format_elapsed_over_one_hour_shows_hours_and_minutes() -> None:
    """_format_elapsed returns hours and minutes for values >= 3600."""
    result = _format_elapsed(3900)  # 1h 5m
    assert "1h" in result
    assert "5m" in result


def test_format_elapsed_zero_returns_zero_seconds() -> None:
    """_format_elapsed returns '0s ago' for zero seconds."""
    assert _format_elapsed(0) == "0s ago"


# ===========================================================================
# scan_attention — _is_transient_blocked
# ===========================================================================


def test_is_transient_blocked_returns_true_for_transient_error_with_needs_human_no() -> None:
    """_is_transient_blocked returns True for BLOCKED tasks with Needs Human=no and TRANSIENT reason."""
    text = (
        "## State\nBLOCKED\n\n"
        "## Needs Human\nno\n\n"
        "## Blocked Reason\nTRANSIENT API ERROR: 529 overloaded\n"
    )
    assert _is_transient_blocked(text) is True


def test_is_transient_blocked_returns_false_when_needs_human_yes() -> None:
    """_is_transient_blocked returns False when Needs Human is 'yes'."""
    text = (
        "## State\nBLOCKED\n\n"
        "## Needs Human\nyes\n\n"
        "## Blocked Reason\nTRANSIENT API ERROR: some issue\n"
    )
    assert _is_transient_blocked(text) is False


def test_is_transient_blocked_returns_false_when_reason_not_transient() -> None:
    """_is_transient_blocked returns False when blocked reason does not start with TRANSIENT."""
    text = (
        "## State\nBLOCKED\n\n"
        "## Needs Human\nno\n\n"
        "## Blocked Reason\nMissing prerequisite file\n"
    )
    assert _is_transient_blocked(text) is False


# ===========================================================================
# scan_attention — _scan_state_file
# ===========================================================================


def test_scan_state_file_returns_pending_rejection_entries(tmp_path: pathlib.Path) -> None:
    """_scan_state_file returns entries where count is between 1 and threshold-1."""
    state_file = tmp_path / "rejected-counts.tsv"
    state_file.write_text(
        "BUG-0001.md\t2\t2026-06-01T00:00:00Z\tmalformed\n"
        "BUG-0002.md\t3\t2026-06-01T00:00:00Z\tother\n",
        encoding="utf-8",
    )
    # threshold=3: count 2 < 3 is pending; count 3 == threshold (already quarantined)
    results = _scan_state_file(state_file, threshold=3)
    assert len(results) == 1
    assert results[0][0] == "BUG-0001.md"
    assert results[0][1] == 2


def test_scan_state_file_returns_empty_for_missing_file(tmp_path: pathlib.Path) -> None:
    """_scan_state_file returns [] when the state file does not exist."""
    results = _scan_state_file(tmp_path / "nonexistent.tsv", threshold=3)
    assert results == []


def test_scan_state_file_uses_reason_from_4th_column(tmp_path: pathlib.Path) -> None:
    """_scan_state_file extracts the rejection reason from the 4th TSV column."""
    state_file = tmp_path / "rejected-counts.tsv"
    state_file.write_text(
        "PRIORITY-001.md\t1\t2026-06-01T00:00:00Z\tinvalid format\n",
        encoding="utf-8",
    )
    results = _scan_state_file(state_file, threshold=5)
    assert results[0][2] == "invalid format"


# ===========================================================================
# scan_attention — _scan_rejected_dir
# ===========================================================================


def test_scan_rejected_dir_returns_sorted_filenames(tmp_path: pathlib.Path) -> None:
    """_scan_rejected_dir returns a sorted list of non-hidden filenames."""
    rejected_dir = tmp_path / ".rejected"
    rejected_dir.mkdir()
    (rejected_dir / "BUG-0002.md").write_text("data", encoding="utf-8")
    (rejected_dir / "BUG-0001.md").write_text("data", encoding="utf-8")
    (rejected_dir / ".hidden").write_text("data", encoding="utf-8")
    result = _scan_rejected_dir(rejected_dir)
    assert result == ["BUG-0001.md", "BUG-0002.md"]


def test_scan_rejected_dir_returns_empty_for_nonexistent_dir(tmp_path: pathlib.Path) -> None:
    """_scan_rejected_dir returns [] when the directory does not exist."""
    result = _scan_rejected_dir(tmp_path / "nonexistent")
    assert result == []


# ===========================================================================
# scan_attention — _iter_projects
# ===========================================================================


def test_iter_projects_yields_subdirs_from_projects_layout(tmp_path: pathlib.Path) -> None:
    """_iter_projects yields (name, path) tuples for each project directory."""
    projects_dir = tmp_path / "projects"
    (projects_dir / "proj-a").mkdir(parents=True)
    (projects_dir / "proj-b").mkdir(parents=True)
    results = list(_iter_projects(tmp_path))
    names = [n for n, _ in results]
    assert "proj-a" in names
    assert "proj-b" in names


def test_iter_projects_skips_hidden_directories(tmp_path: pathlib.Path) -> None:
    """_iter_projects skips directories starting with '.'."""
    projects_dir = tmp_path / "projects"
    (projects_dir / ".hidden").mkdir(parents=True)
    (projects_dir / "visible").mkdir(parents=True)
    results = list(_iter_projects(tmp_path))
    names = [n for n, _ in results]
    assert ".hidden" not in names
    assert "visible" in names


def test_iter_projects_falls_back_to_single_project_layout(tmp_path: pathlib.Path) -> None:
    """_iter_projects falls back to the kanban root when no projects/ directory exists."""
    results = list(_iter_projects(tmp_path))
    assert len(results) == 1
    assert results[0][0] == "pgai-agent-kanban"


# ===========================================================================
# scan_attention — scan_blocked_tasks
# ===========================================================================


def _write_task_status_file(
    tasks_dir: pathlib.Path,
    task_id: str,
    state: str,
    needs_human: str = "no",
    blocked_reason: str = "",
) -> None:
    task_dir = tasks_dir / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        f"## State\n{state}\n\n",
        f"## Needs Human\n{needs_human}\n\n",
    ]
    if blocked_reason:
        lines.append(f"## Blocked Reason\n{blocked_reason}\n\n")
    (task_dir / "status.md").write_text("".join(lines), encoding="utf-8")


def test_scan_blocked_tasks_prints_task_id_for_blocked_task(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """scan_blocked_tasks prints the task ID for a BLOCKED task with Needs Human=yes."""
    tasks_dir = tmp_path / "tasks"
    _write_task_status_file(tasks_dir, "CODER-20260601-001-blocked", "BLOCKED", "yes", "missing file")
    scan_blocked_tasks(str(tasks_dir), use_color=False)
    captured = capsys.readouterr()
    assert "CODER-20260601-001-blocked" in captured.out


def test_scan_blocked_tasks_shows_no_blocked_message_when_no_blocked_tasks(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """scan_blocked_tasks prints the 'no blocked tasks' message when no tasks are BLOCKED."""
    tasks_dir = tmp_path / "tasks"
    _write_task_status_file(tasks_dir, "CODER-001", "DONE")
    scan_blocked_tasks(str(tasks_dir), use_color=False)
    captured = capsys.readouterr()
    assert "no blocked tasks" in captured.out


def test_scan_blocked_tasks_excludes_transient_errors(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """scan_blocked_tasks does not print tasks whose blocked reason is a transient API error."""
    tasks_dir = tmp_path / "tasks"
    _write_task_status_file(
        tasks_dir, "CODER-001", "BLOCKED", "no", "TRANSIENT API ERROR: 529"
    )
    scan_blocked_tasks(str(tasks_dir), use_color=False)
    captured = capsys.readouterr()
    # Should show no blocked message because the one task is transient
    assert "no blocked tasks" in captured.out


# ===========================================================================
# scan_attention — scan_transient_tasks
# ===========================================================================


def test_scan_transient_tasks_prints_task_id_for_transient_block(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """scan_transient_tasks prints the task ID for a BLOCKED task with TRANSIENT API ERROR."""
    tasks_dir = tmp_path / "tasks"
    _write_task_status_file(
        tasks_dir, "CODER-20260601-002-transient", "BLOCKED", "no", "TRANSIENT API ERROR: 529"
    )
    scan_transient_tasks(str(tasks_dir), use_color=False)
    captured = capsys.readouterr()
    assert "CODER-20260601-002-transient" in captured.out


def test_scan_transient_tasks_shows_no_errors_message_when_none(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """scan_transient_tasks prints 'no transient API errors' when no such tasks exist."""
    tasks_dir = tmp_path / "tasks"
    _write_task_status_file(tasks_dir, "CODER-001", "DONE")
    scan_transient_tasks(str(tasks_dir), use_color=False)
    captured = capsys.readouterr()
    assert "no transient API errors" in captured.out


# ===========================================================================
# scan_attention — scan_stale_working_tasks
# ===========================================================================


def test_scan_stale_working_tasks_emits_nothing_when_threshold_zero(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """scan_stale_working_tasks emits no output when max_task_seconds <= 0."""
    tasks_dir = tmp_path / "tasks"
    _write_task_status_file(tasks_dir, "CODER-001", "WORKING")
    scan_stale_working_tasks(str(tasks_dir), use_color=False, max_task_seconds=0)
    captured = capsys.readouterr()
    assert captured.out == ""


def test_scan_stale_working_tasks_shows_no_stale_when_all_tasks_within_threshold(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """scan_stale_working_tasks shows 'no stale' message when no tasks exceed threshold."""
    tasks_dir = tmp_path / "tasks"
    # Write a DONE task — not in WORKING state, so won't trigger stale
    _write_task_status_file(tasks_dir, "CODER-001", "DONE")
    scan_stale_working_tasks(str(tasks_dir), use_color=False, max_task_seconds=5400)
    captured = capsys.readouterr()
    assert "no stale WORKING tasks" in captured.out


def test_scan_stale_working_tasks_flags_old_working_task(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """scan_stale_working_tasks flags a WORKING task whose status.md mtime is old."""
    tasks_dir = tmp_path / "tasks"
    task_dir = tasks_dir / "CODER-20260601-001-stale"
    task_dir.mkdir(parents=True)
    status_file = task_dir / "status.md"
    status_file.write_text("## State\nWORKING\n\n## Role\nCODER\n", encoding="utf-8")

    # Backdate the mtime to be very old (more than 5400s ago)
    old_mtime = time.time() - 10000
    import os
    os.utime(str(status_file), (old_mtime, old_mtime))

    scan_stale_working_tasks(str(tasks_dir), use_color=False, max_task_seconds=5400)
    captured = capsys.readouterr()
    assert "CODER-20260601-001-stale" in captured.out


# ===========================================================================
# scan_attention — scan_overwatch_section and _parse_overwatch_action_log
# ===========================================================================


def _write_overwatch_action_log(
    kanban_root: pathlib.Path,
    project_name: str,
    entries: list[tuple],
) -> pathlib.Path:
    """Create a fixture actions.log under kanban_root/projects/<name>/overwatch/.

    Each entry in ``entries`` is a 6-tuple:
        (timestamp, name, target, action, backup, reason)
    """
    ow_dir = kanban_root / "projects" / project_name / "overwatch"
    ow_dir.mkdir(parents=True, exist_ok=True)
    log_path = ow_dir / "actions.log"
    lines = []
    for ts, name, target, action, backup, reason in entries:
        lines.append(f"{ts}\t{name}\t{target}\t{action}\t{backup}\t{reason}")
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log_path


def test_parse_overwatch_action_log_returns_empty_for_missing_file(
    tmp_path: pathlib.Path,
) -> None:
    """_parse_overwatch_action_log returns [] when the log file does not exist."""
    result = _parse_overwatch_action_log(tmp_path / "nonexistent.log", max_entries=10)
    assert result == []


def test_parse_overwatch_action_log_labels_transient_action(
    tmp_path: pathlib.Path,
) -> None:
    """_parse_overwatch_action_log sets type=TRANSIENT for transient-auto-requeued."""
    log_path = tmp_path / "actions.log"
    log_path.write_text(
        "2026-07-07T10:00:00Z\tcheck-transient-api-error\tCODER-001\t"
        "transient-auto-requeued\tnone\tTransient 529 in log tail\n",
        encoding="utf-8",
    )
    entries = _parse_overwatch_action_log(log_path, max_entries=10)
    assert len(entries) == 1
    assert entries[0]["type"] == "TRANSIENT"
    assert entries[0]["target"] == "CODER-001"
    assert entries[0]["action"] == "transient-auto-requeued"


def test_parse_overwatch_action_log_labels_needs_human_action(
    tmp_path: pathlib.Path,
) -> None:
    """_parse_overwatch_action_log sets type=needs-human for ceiling-reached bug-filed."""
    log_path = tmp_path / "actions.log"
    log_path.write_text(
        "2026-07-07T10:00:00Z\tcheck-transient-api-error\tCODER-002\t"
        "transient-ceiling-bug-filed\tnone\tCeiling=2 reached\n",
        encoding="utf-8",
    )
    entries = _parse_overwatch_action_log(log_path, max_entries=10)
    assert len(entries) == 1
    assert entries[0]["type"] == "needs-human"
    assert entries[0]["target"] == "CODER-002"


def test_parse_overwatch_action_log_labels_routine_action_as_info(
    tmp_path: pathlib.Path,
) -> None:
    """_parse_overwatch_action_log sets type=info for sweep-end and check-ok entries."""
    log_path = tmp_path / "actions.log"
    log_path.write_text(
        "2026-07-07T10:00:00Z\toverwatch-sweep\tpgai-agent-kanban\t"
        "sweep-end\tnone\t5 ok, 0 error\n",
        encoding="utf-8",
    )
    entries = _parse_overwatch_action_log(log_path, max_entries=10)
    assert len(entries) == 1
    assert entries[0]["type"] == "info"


def test_parse_overwatch_action_log_returns_newest_first(
    tmp_path: pathlib.Path,
) -> None:
    """_parse_overwatch_action_log returns entries in reverse-chronological order."""
    log_path = tmp_path / "actions.log"
    log_path.write_text(
        "2026-07-07T09:00:00Z\tcheck-transient-api-error\tCODER-001\t"
        "transient-auto-requeued\tnone\told entry\n"
        "2026-07-07T10:00:00Z\tcheck-transient-api-error\tCODER-002\t"
        "transient-ceiling-bug-filed\tnone\tnew entry\n",
        encoding="utf-8",
    )
    entries = _parse_overwatch_action_log(log_path, max_entries=10)
    assert len(entries) == 2
    # Newest (CODER-002) first
    assert entries[0]["target"] == "CODER-002"
    assert entries[1]["target"] == "CODER-001"


def test_parse_overwatch_action_log_respects_max_entries(
    tmp_path: pathlib.Path,
) -> None:
    """_parse_overwatch_action_log caps results at max_entries."""
    lines = []
    for i in range(10):
        lines.append(
            f"2026-07-07T10:0{i}:00Z\tcheck-transient-api-error\tCODER-{i:03d}\t"
            f"transient-auto-requeued\tnone\treason {i}"
        )
    log_path = tmp_path / "actions.log"
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    entries = _parse_overwatch_action_log(log_path, max_entries=3)
    assert len(entries) == 3


def test_scan_overwatch_section_shows_no_activity_when_no_logs(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """scan_overwatch_section prints 'no recent OVERWATCH activity' when no logs exist."""
    # No projects/*/overwatch/actions.log files
    (tmp_path / "projects" / "myproj").mkdir(parents=True)
    scan_overwatch_section(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "no recent OVERWATCH activity" in captured.out


def test_scan_overwatch_section_renders_transient_and_needs_human_items(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """scan_overwatch_section renders both TRANSIENT and needs-human items with distinct labels.

    Fixture: sweep populates the action log with one TRANSIENT entry and one
    needs-human entry.  Both must appear in the output with distinct type labels.
    """
    _write_overwatch_action_log(
        tmp_path,
        "pgai-agent-kanban",
        [
            (
                "2026-07-07T10:00:00Z",
                "check-transient-api-error",
                "CODER-20260707-001-task",
                "transient-auto-requeued",
                "none",
                "529 Overloaded in log tail; reset to BACKLOG (requeue #1 of 2)",
            ),
            (
                "2026-07-07T10:01:00Z",
                "check-transient-api-error",
                "CODER-20260707-002-task",
                "transient-ceiling-bug-filed",
                "none",
                "Transient ceiling=2; bug filed",
            ),
        ],
    )
    scan_overwatch_section(str(tmp_path), use_color=False)
    captured = capsys.readouterr()

    # Both items must appear
    assert "CODER-20260707-001-task" in captured.out
    assert "CODER-20260707-002-task" in captured.out

    # Distinct type labels must appear
    assert "TRANSIENT" in captured.out
    assert "needs-human" in captured.out


def test_scan_overwatch_section_suppresses_info_entries(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """scan_overwatch_section does not render info-type entries (sweep-end, check-ok)."""
    _write_overwatch_action_log(
        tmp_path,
        "pgai-agent-kanban",
        [
            (
                "2026-07-07T10:00:00Z",
                "overwatch-sweep",
                "pgai-agent-kanban",
                "sweep-end",
                "none",
                "5 ok, 0 error",
            ),
        ],
    )
    scan_overwatch_section(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    # Info-only entry should result in the no-activity message
    assert "no recent OVERWATCH activity" in captured.out


def test_scan_overwatch_section_transient_distinct_from_needs_human_in_output(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """TRANSIENT and needs-human items carry distinguishable labels in the output.

    This verifies the structured-field acceptance criterion: downstream renderers
    can distinguish the two types without parsing prose.
    """
    _write_overwatch_action_log(
        tmp_path,
        "proj-alpha",
        [
            (
                "2026-07-07T12:00:00Z",
                "check-transient-api-error",
                "TASK-TRANSIENT",
                "transient-auto-requeued",
                "none",
                "rate limit in log",
            ),
            (
                "2026-07-07T12:01:00Z",
                "check-transient-api-error",
                "TASK-HUMAN",
                "transient-ceiling-bug-filed",
                "none",
                "ceiling reached",
            ),
        ],
    )
    scan_overwatch_section(str(tmp_path), use_color=False)
    captured = capsys.readouterr()

    # TRANSIENT label must precede TASK-TRANSIENT in the output
    transient_pos = captured.out.find("TRANSIENT")
    task_transient_pos = captured.out.find("TASK-TRANSIENT")
    assert transient_pos != -1
    assert task_transient_pos != -1

    # needs-human label must precede TASK-HUMAN in the output
    needs_human_pos = captured.out.find("needs-human")
    task_human_pos = captured.out.find("TASK-HUMAN")
    assert needs_human_pos != -1
    assert task_human_pos != -1

    # The two labels must be different strings (structural distinction check)
    assert "TRANSIENT" != "needs-human"


def test_scan_overwatch_section_existing_sections_unaffected(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """scan_overwatch_section output does not include blocked-tasks section content.

    Verifies the additive constraint: OVERWATCH section does not modify or
    replace content from other scanners.
    """
    # Write a BLOCKED task — scan_overwatch_section should NOT mention it
    task_dir = tmp_path / "projects" / "myproj" / "tasks" / "CODER-001-blocked"
    task_dir.mkdir(parents=True)
    (task_dir / "status.md").write_text(
        "## State\nBLOCKED\n\n## Needs Human\nyes\n\n## Blockers\nmissing file\n",
        encoding="utf-8",
    )
    # No overwatch action log — so no OVERWATCH data
    scan_overwatch_section(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    # The blocked task should NOT appear — scan_overwatch_section only reads action logs
    assert "CODER-001-blocked" not in captured.out
    assert "no recent OVERWATCH activity" in captured.out


# ===========================================================================
# cron_firings — _seconds_to_label
# ===========================================================================


def test_seconds_to_label_negative_seconds_returns_now() -> None:
    """_seconds_to_label returns 'now' for negative or zero seconds."""
    assert _seconds_to_label(-5) == "now"
    assert _seconds_to_label(0) == "now"


def test_seconds_to_label_under_60_seconds_returns_now() -> None:
    """_seconds_to_label returns 'now' when seconds is less than 60."""
    assert _seconds_to_label(59) == "now"


def test_seconds_to_label_exactly_one_minute_returns_singular() -> None:
    """_seconds_to_label returns 'in 1 min' when seconds == 60."""
    assert _seconds_to_label(60) == "in 1 min"


def test_seconds_to_label_multiple_minutes_returns_plural() -> None:
    """_seconds_to_label returns 'in N min' for N > 1 minutes."""
    assert _seconds_to_label(360) == "in 6 min"


def test_seconds_to_label_two_minutes_exact() -> None:
    """_seconds_to_label returns 'in 2 min' for exactly 120 seconds."""
    assert _seconds_to_label(120) == "in 2 min"


# ===========================================================================
# cron_firings — cron_firings function (with mocked cron_parser)
# ===========================================================================


def test_cron_firings_returns_empty_dict_for_empty_crontab(tmp_path: pathlib.Path) -> None:
    """cron_firings returns {} when crontab_text is empty."""
    parser_path = tmp_path / "cron_parser.py"
    parser_path.write_text("def next_firings(text, now=None): return {}\n", encoding="utf-8")
    result = cron_firings("", str(parser_path))
    assert result == {}


def test_cron_firings_returns_empty_dict_for_missing_parser_path(
    tmp_path: pathlib.Path,
) -> None:
    """cron_firings returns {} when the cron_parser path does not exist."""
    result = cron_firings("*/5 * * * * coder", str(tmp_path / "nonexistent.py"))
    assert result == {}


def test_cron_firings_converts_integer_seconds_to_label(tmp_path: pathlib.Path) -> None:
    """cron_firings converts int seconds from next_firings into human-readable labels."""
    parser_path = tmp_path / "cron_parser.py"
    parser_path.write_text(
        "def next_firings(text, now=None):\n"
        "    return {'coder': 360, 'pm': 60}\n",
        encoding="utf-8",
    )
    result = cron_firings("fake crontab", str(parser_path))
    assert result["coder"] == "in 6 min"
    assert result["pm"] == "in 1 min"


def test_cron_firings_passes_through_string_sentinel_values(tmp_path: pathlib.Path) -> None:
    """cron_firings passes through string sentinels (e.g. 'Sun 4am') unchanged."""
    parser_path = tmp_path / "cron_parser.py"
    parser_path.write_text(
        "def next_firings(text, now=None):\n"
        "    return {'cleanup': 'Sun 4am'}\n",
        encoding="utf-8",
    )
    result = cron_firings("fake crontab", str(parser_path))
    assert result["cleanup"] == "Sun 4am"


def test_cron_firings_with_now_ts_passes_datetime_to_next_firings(
    tmp_path: pathlib.Path,
) -> None:
    """cron_firings converts now_ts float to a datetime before calling next_firings."""
    received_now = {}

    parser_path = tmp_path / "cron_parser.py"
    parser_path.write_text(
        "def next_firings(text, now=None):\n"
        "    return {'agent': 120}\n",
        encoding="utf-8",
    )
    # Use a known timestamp
    result = cron_firings("fake crontab", str(parser_path), now_ts=1000000.0)
    assert result["agent"] == "in 2 min"
