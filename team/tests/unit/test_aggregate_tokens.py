"""
test_aggregate_tokens.py
========================
Behavioral unit tests for team/pm-agent/aggregate_tokens.py.

Covers:
  - is_new_schema(): distinguish new-schema from legacy-schema token records.
  - is_captured(): detect uncaptured token records (captured: false).
  - read_field_from_markdown(): extract a named section value from markdown text.
  - normalise_version(): ensure version strings start with 'v'.
  - compute_cost(): per-category cost calculation from pricing table (legacy schema).
  - CategoryCosts.total_cost_usd: property adds the four cost categories.
  - all_rc_versions() / all_days(): helper extractors for --all mode.
  - build_rc_rollup(): per-RC roll-up JSON structure and token accumulation.
  - build_day_rollup(): per-day roll-up JSON structure and agent/model breakdowns.
  - load_tokens_json(): graceful handling of absent and malformed tokens.json.
  - read_rc_version() / read_agent(): task metadata extractors.
  - scan_tasks(): task directory walk with schema detection and captured flag.

Edge cases covered:
  - Empty input (no tasks, no records).
  - Malformed tokens.json (JSON parse error, wrong top-level type).
  - Missing tokens.json (task with no artifacts/tokens.json).
  - Uncaptured records (captured: false) excluded from token/cost totals.
  - Legacy-schema records with zero cache fields.
  - New-schema records with model_usage block.
  - Mixed new + legacy records in a single rollup.
  - Version normalisation (with and without leading 'v').
  - Invalid day format in build_day_rollup (sys.exit path not tested directly;
    the parser logic is tested via ts_in_day helper indirectly via build_day_rollup).

All filesystem interactions use tmp_path.  No subprocess calls, no live kanban tree.
No bare /tmp paths.

Test function names describe the behavior under test (SOP.md anti-pattern 6).
"""

from __future__ import annotations

import json
import pathlib
import sys
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Import with installed-package / direct-checkout fallback
# ---------------------------------------------------------------------------
try:
    import pm_agent.aggregate_tokens as agg
    from pm_agent.aggregate_tokens import (
        is_new_schema,
        is_captured,
        read_field_from_markdown,
        normalise_version,
        compute_cost,
        CategoryCosts,
        all_rc_versions,
        all_days,
        build_rc_rollup,
        build_day_rollup,
        load_tokens_json,
        read_rc_version,
        read_agent,
        scan_tasks,
        _zero_agent_entry,
    )
except ImportError:
    sys.path.insert(
        0, str(pathlib.Path(__file__).parent.parent.parent / "pm-agent")
    )
    import aggregate_tokens as agg  # type: ignore[no-redef]
    from aggregate_tokens import (  # type: ignore[no-redef]
        is_new_schema,
        is_captured,
        read_field_from_markdown,
        normalise_version,
        compute_cost,
        CategoryCosts,
        all_rc_versions,
        all_days,
        build_rc_rollup,
        build_day_rollup,
        load_tokens_json,
        read_rc_version,
        read_agent,
        scan_tasks,
        _zero_agent_entry,
    )


# ---------------------------------------------------------------------------
# Helpers: build fixture data
# ---------------------------------------------------------------------------

def _make_tokens_dir(task_dir: pathlib.Path) -> pathlib.Path:
    """Ensure artifacts/ exists inside task_dir and return its path."""
    artifacts = task_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    return artifacts


def _write_tokens_json(task_dir: pathlib.Path, data: dict) -> pathlib.Path:
    """Write data as JSON to task_dir/artifacts/tokens.json and return the path."""
    artifacts = _make_tokens_dir(task_dir)
    p = artifacts / "tokens.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _write_readme(task_dir: pathlib.Path, release_version: str | None = None,
                  role: str = "CODER") -> pathlib.Path:
    """Write a minimal README.md with an optional Release Version section."""
    lines = ["# Task: My Task\n\n"]
    lines.append(f"## Role\n{role}\n\n")
    if release_version is not None:
        lines.append(f"## Release Version\n{release_version}\n\n")
    p = task_dir / "README.md"
    p.write_text("".join(lines), encoding="utf-8")
    return p


def _minimal_legacy_token(
    model: str = "claude-sonnet-4-6",
    provider: str = "claude",
    agent: str = "coder",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    invocations: int = 1,
    timestamp: str = "2026-05-17T03:24:46Z",
) -> dict:
    """Return a minimal legacy-schema tokens.json dict."""
    return {
        "model":                       model,
        "provider":                    provider,
        "agent":                       agent,
        "input_tokens":                input_tokens,
        "output_tokens":               output_tokens,
        "cache_creation_input_tokens": cache_creation_input_tokens,
        "cache_read_input_tokens":     cache_read_input_tokens,
        "invocations":                 invocations,
        "elapsed_seconds":             60,
        "timestamp":                   timestamp,
    }


def _minimal_new_schema_token(
    agent: str = "coder",
    total_cost_usd: float = 0.027,
    invocations: int = 2,
    timestamp: str = "2026-05-17T03:24:46Z",
    model_name: str = "claude-opus-4-7",
    model_cost_usd: float = 0.026,
) -> dict:
    """Return a minimal new-schema tokens.json dict."""
    return {
        "provider":        "claude",
        "agent":           agent,
        "invocations":     invocations,
        "elapsed_seconds": 90,
        "timestamp":       timestamp,
        "total_cost_usd":  total_cost_usd,
        "model_usage": {
            model_name: {
                "input_tokens":                12,
                "output_tokens":               14,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens":     52366,
                "cost_usd":                    model_cost_usd,
            }
        },
    }


def _minimal_pricing() -> dict:
    """Return a minimal token_pricing.json dict for claude-sonnet-4-6."""
    return {
        "providers": {
            "claude": {
                "models": {
                    "claude-sonnet-4-6": {
                        "input_per_1m":           3.0,
                        "cache_creation_per_1m":  3.75,
                        "cache_read_per_1m":      0.30,
                        "output_per_1m":          15.0,
                    }
                }
            }
        }
    }


# ---------------------------------------------------------------------------
# is_new_schema()
# ---------------------------------------------------------------------------


def test_is_new_schema_returns_true_for_valid_new_schema_record() -> None:
    """is_new_schema returns True when total_cost_usd and model_usage are present."""
    tok = _minimal_new_schema_token()
    assert is_new_schema(tok) is True


def test_is_new_schema_returns_false_for_legacy_record() -> None:
    """is_new_schema returns False for a record with no total_cost_usd."""
    tok = _minimal_legacy_token()
    assert is_new_schema(tok) is False


def test_is_new_schema_returns_false_when_model_usage_is_empty_dict() -> None:
    """is_new_schema returns False when model_usage is present but empty."""
    tok = {"total_cost_usd": 0.01, "model_usage": {}}
    assert is_new_schema(tok) is False


def test_is_new_schema_returns_false_when_total_cost_is_missing() -> None:
    """is_new_schema returns False when total_cost_usd is absent."""
    tok = {"model_usage": {"some-model": {"cost_usd": 0.01}}}
    assert is_new_schema(tok) is False


def test_is_new_schema_returns_false_for_empty_dict() -> None:
    """is_new_schema returns False for an empty dict."""
    assert is_new_schema({}) is False


def test_is_new_schema_accepts_integer_zero_as_total_cost() -> None:
    """is_new_schema returns True when total_cost_usd is 0 (int) with non-empty model_usage."""
    tok = {"total_cost_usd": 0, "model_usage": {"model-x": {"cost_usd": 0}}}
    assert is_new_schema(tok) is True


# ---------------------------------------------------------------------------
# is_captured()
# ---------------------------------------------------------------------------


def test_is_captured_returns_true_when_field_is_absent() -> None:
    """is_captured returns True when the 'captured' field is absent (default assumption)."""
    assert is_captured({}) is True


def test_is_captured_returns_true_when_captured_is_true() -> None:
    """is_captured returns True when captured is explicitly True."""
    assert is_captured({"captured": True}) is True


def test_is_captured_returns_false_when_captured_is_false() -> None:
    """is_captured returns False only when captured is explicitly False."""
    assert is_captured({"captured": False}) is False


def test_is_captured_returns_true_when_captured_is_zero() -> None:
    """is_captured returns True for captured=0 (falsy but not False)."""
    # The spec says 'only explicit False means not captured'
    assert is_captured({"captured": 0}) is True


def test_is_captured_returns_true_when_captured_is_none() -> None:
    """is_captured returns True when captured is None (not explicitly False)."""
    assert is_captured({"captured": None}) is True


# ---------------------------------------------------------------------------
# read_field_from_markdown()
# ---------------------------------------------------------------------------


def test_read_field_from_markdown_extracts_simple_value() -> None:
    """read_field_from_markdown returns the first non-blank line after the heading."""
    text = "# Title\n\n## Release Version\nv0.23.22\n"
    assert read_field_from_markdown(text, "Release Version") == "v0.23.22"


def test_read_field_from_markdown_returns_none_for_missing_heading() -> None:
    """read_field_from_markdown returns None when the heading does not exist."""
    text = "## Other Section\nsome value\n"
    assert read_field_from_markdown(text, "Release Version") is None


def test_read_field_from_markdown_skips_blank_lines_after_heading() -> None:
    """read_field_from_markdown skips blank lines to find the value."""
    text = "## Role\n\n\nCODER\n"
    assert read_field_from_markdown(text, "Role") == "CODER"


def test_read_field_from_markdown_returns_none_when_no_value_follows(
) -> None:
    """read_field_from_markdown returns None when the heading has no body."""
    text = "## Release Version\n"
    assert read_field_from_markdown(text, "Release Version") is None


def test_read_field_from_markdown_skips_hash_prefixed_lines_to_find_value() -> None:
    """read_field_from_markdown skips '#'-prefixed lines and returns the first non-blank non-hash line."""
    # '## Another Heading' starts with '#' and is skipped; 'v0.1.0' is returned.
    text = "## Release Version\n## Another Heading\nv0.1.0\n"
    result = read_field_from_markdown(text, "Release Version")
    assert result == "v0.1.0"


def test_read_field_from_markdown_handles_empty_text() -> None:
    """read_field_from_markdown returns None for empty input."""
    assert read_field_from_markdown("", "Release Version") is None


# ---------------------------------------------------------------------------
# normalise_version()
# ---------------------------------------------------------------------------


def test_normalise_version_adds_v_prefix_when_absent() -> None:
    """normalise_version prepends 'v' to a bare version number."""
    assert normalise_version("0.23.22") == "v0.23.22"


def test_normalise_version_leaves_v_prefix_intact() -> None:
    """normalise_version does not double-prefix versions that already start with 'v'."""
    assert normalise_version("v0.23.22") == "v0.23.22"


def test_normalise_version_strips_surrounding_whitespace() -> None:
    """normalise_version strips leading/trailing whitespace before processing."""
    assert normalise_version("  v0.1.0  ") == "v0.1.0"


def test_normalise_version_handles_empty_string() -> None:
    """normalise_version returns an empty string for empty input."""
    assert normalise_version("") == ""


# ---------------------------------------------------------------------------
# CategoryCosts.total_cost_usd
# ---------------------------------------------------------------------------


def test_category_costs_total_sums_four_components() -> None:
    """CategoryCosts.total_cost_usd is the sum of all four cost components."""
    costs = CategoryCosts(
        input_cost_usd=0.003,
        cache_creation_cost_usd=0.001,
        cache_read_cost_usd=0.0005,
        output_cost_usd=0.015,
    )
    expected = 0.003 + 0.001 + 0.0005 + 0.015
    assert abs(costs.total_cost_usd - expected) < 1e-9


def test_category_costs_all_zeros_sums_to_zero() -> None:
    """CategoryCosts(0, 0, 0, 0).total_cost_usd is 0.0."""
    costs = CategoryCosts(0.0, 0.0, 0.0, 0.0)
    assert costs.total_cost_usd == 0.0


# ---------------------------------------------------------------------------
# compute_cost() — legacy schema cost computation
# ---------------------------------------------------------------------------


def test_compute_cost_returns_zero_when_pricing_is_empty() -> None:
    """compute_cost returns all-zero costs when pricing dict is empty."""
    tok = _minimal_legacy_token(input_tokens=1000, output_tokens=500)
    result = compute_cost({}, tok)
    assert result == CategoryCosts(0.0, 0.0, 0.0, 0.0)


def test_compute_cost_computes_input_tokens_correctly() -> None:
    """compute_cost correctly prices input tokens at the per-1M rate."""
    pricing = _minimal_pricing()
    # 1,000,000 input tokens at $3.00/1M => $3.00
    tok = _minimal_legacy_token(input_tokens=1_000_000, output_tokens=0)
    result = compute_cost(pricing, tok)
    assert abs(result.input_cost_usd - 3.0) < 1e-6


def test_compute_cost_computes_output_tokens_correctly() -> None:
    """compute_cost correctly prices output tokens at the per-1M rate."""
    pricing = _minimal_pricing()
    # 1,000,000 output tokens at $15.00/1M => $15.00
    tok = _minimal_legacy_token(input_tokens=0, output_tokens=1_000_000)
    result = compute_cost(pricing, tok)
    assert abs(result.output_cost_usd - 15.0) < 1e-6


def test_compute_cost_computes_cache_creation_tokens_correctly() -> None:
    """compute_cost correctly prices cache_creation tokens."""
    pricing = _minimal_pricing()
    # 1,000,000 cache_creation tokens at $3.75/1M => $3.75
    tok = _minimal_legacy_token(
        input_tokens=0, output_tokens=0, cache_creation_input_tokens=1_000_000
    )
    result = compute_cost(pricing, tok)
    assert abs(result.cache_creation_cost_usd - 3.75) < 1e-6


def test_compute_cost_computes_cache_read_tokens_correctly() -> None:
    """compute_cost correctly prices cache_read tokens."""
    pricing = _minimal_pricing()
    # 1,000,000 cache_read tokens at $0.30/1M => $0.30
    tok = _minimal_legacy_token(
        input_tokens=0, output_tokens=0, cache_read_input_tokens=1_000_000
    )
    result = compute_cost(pricing, tok)
    assert abs(result.cache_read_cost_usd - 0.30) < 1e-6


def test_compute_cost_returns_zero_for_unknown_model(capsys) -> None:
    """compute_cost returns all-zero costs and emits a warning for an unknown model."""
    # Reset the per-run model-warning set so this test is independent.
    import aggregate_tokens as _agg_mod  # direct import for internal state
    _agg_mod._MODEL_WARNED.clear()

    pricing = _minimal_pricing()
    tok = _minimal_legacy_token(model="unknown-model-xyz", input_tokens=1000)
    result = compute_cost(pricing, tok)
    assert result == CategoryCosts(0.0, 0.0, 0.0, 0.0)
    captured = capsys.readouterr()
    assert "WARNING" in captured.err


def test_compute_cost_handles_null_token_fields_as_zero() -> None:
    """compute_cost treats None/missing token count fields as 0."""
    pricing = _minimal_pricing()
    # Provide a record with None for token counts (edge case from shell capture)
    tok = {
        "model": "claude-sonnet-4-6",
        "provider": "claude",
        "input_tokens": None,
        "output_tokens": None,
        "cache_creation_input_tokens": None,
        "cache_read_input_tokens": None,
    }
    result = compute_cost(pricing, tok)
    assert result.total_cost_usd == 0.0


# ---------------------------------------------------------------------------
# load_tokens_json()
# ---------------------------------------------------------------------------


def test_load_tokens_json_returns_dict_for_valid_file(tmp_path: pathlib.Path) -> None:
    """load_tokens_json returns the parsed dict for a valid tokens.json."""
    task_dir = tmp_path / "CODER-20260101-001-slug"
    task_dir.mkdir()
    tok = _minimal_legacy_token()
    _write_tokens_json(task_dir, tok)

    result = load_tokens_json(task_dir)
    assert isinstance(result, dict)
    assert result.get("model") == "claude-sonnet-4-6"


def test_load_tokens_json_returns_none_when_file_absent(tmp_path: pathlib.Path) -> None:
    """load_tokens_json returns None when artifacts/tokens.json does not exist."""
    task_dir = tmp_path / "CODER-20260101-001-no-tokens"
    task_dir.mkdir()

    result = load_tokens_json(task_dir)
    assert result is None


def test_load_tokens_json_returns_none_for_malformed_json(
    tmp_path: pathlib.Path, capsys
) -> None:
    """load_tokens_json returns None (and emits a warning) for malformed JSON."""
    task_dir = tmp_path / "CODER-20260101-001-bad-json"
    task_dir.mkdir()
    artifacts = task_dir / "artifacts"
    artifacts.mkdir()
    (artifacts / "tokens.json").write_text("{not valid json", encoding="utf-8")

    result = load_tokens_json(task_dir)
    assert result is None
    captured = capsys.readouterr()
    assert "WARNING" in captured.err


def test_load_tokens_json_returns_none_for_json_array_at_top_level(
    tmp_path: pathlib.Path, capsys
) -> None:
    """load_tokens_json returns None when tokens.json top-level value is a list."""
    task_dir = tmp_path / "CODER-20260101-001-list-json"
    task_dir.mkdir()
    artifacts = task_dir / "artifacts"
    artifacts.mkdir()
    (artifacts / "tokens.json").write_text("[1, 2, 3]", encoding="utf-8")

    result = load_tokens_json(task_dir)
    assert result is None
    captured = capsys.readouterr()
    assert "WARNING" in captured.err


# ---------------------------------------------------------------------------
# read_rc_version()
# ---------------------------------------------------------------------------


def test_read_rc_version_extracts_version_from_readme(tmp_path: pathlib.Path) -> None:
    """read_rc_version returns the Release Version field from README.md."""
    task_dir = tmp_path / "CODER-20260101-001-slug"
    task_dir.mkdir()
    _write_readme(task_dir, release_version="v0.23.22")

    result = read_rc_version(task_dir)
    assert result == "v0.23.22"


def test_read_rc_version_returns_none_when_readme_absent(
    tmp_path: pathlib.Path,
) -> None:
    """read_rc_version returns None when README.md does not exist."""
    task_dir = tmp_path / "CODER-20260101-001-no-readme"
    task_dir.mkdir()

    result = read_rc_version(task_dir)
    assert result is None


def test_read_rc_version_returns_none_when_field_absent(
    tmp_path: pathlib.Path,
) -> None:
    """read_rc_version returns None when README.md has no Release Version section."""
    task_dir = tmp_path / "CODER-20260101-001-no-version"
    task_dir.mkdir()
    _write_readme(task_dir, release_version=None)

    result = read_rc_version(task_dir)
    assert result is None


# ---------------------------------------------------------------------------
# read_agent()
# ---------------------------------------------------------------------------


def test_read_agent_reads_from_readme_role_field(tmp_path: pathlib.Path) -> None:
    """read_agent returns the lowercased Role value from README.md."""
    task_dir = tmp_path / "CODER-20260101-001-slug"
    task_dir.mkdir()
    _write_readme(task_dir, role="WRITER")

    result = read_agent(task_dir)
    assert result == "writer"


def test_read_agent_derives_role_from_task_id_when_no_readme(
    tmp_path: pathlib.Path,
) -> None:
    """read_agent falls back to the task-ID prefix when README.md is absent."""
    task_dir = tmp_path / "TESTER-20260101-001-verify"
    task_dir.mkdir()

    result = read_agent(task_dir)
    assert result == "tester"


def test_read_agent_reads_status_md_agent_field_first(
    tmp_path: pathlib.Path,
) -> None:
    """read_agent prefers the status.md Agent field over README.md Role."""
    task_dir = tmp_path / "CODER-20260101-001-slug"
    task_dir.mkdir()
    _write_readme(task_dir, role="CODER")
    status = task_dir / "status.md"
    status.write_text("## Agent\nwriter (claude-sonnet-4-6)\n", encoding="utf-8")

    result = read_agent(task_dir)
    assert result == "writer"


def test_read_agent_strips_model_suffix_from_agent_field(
    tmp_path: pathlib.Path,
) -> None:
    """read_agent strips the '(model-name)' suffix from the status.md Agent field."""
    task_dir = tmp_path / "CODER-20260101-001-slug"
    task_dir.mkdir()
    status = task_dir / "status.md"
    status.write_text("## Agent\ncoder (claude-opus-4-7)\n", encoding="utf-8")

    result = read_agent(task_dir)
    assert result == "coder"


# ---------------------------------------------------------------------------
# all_rc_versions() / all_days()
# ---------------------------------------------------------------------------


def test_all_rc_versions_collects_distinct_versions() -> None:
    """all_rc_versions returns a sorted list of unique normalised version strings."""
    records = [
        {"rc_version": "v0.1.0", "tokens": {}},
        {"rc_version": "v0.2.0", "tokens": {}},
        {"rc_version": "v0.1.0", "tokens": {}},  # duplicate
        {"rc_version": None,     "tokens": {}},  # no version — skipped
    ]
    result = all_rc_versions(records)
    assert result == ["v0.1.0", "v0.2.0"]


def test_all_rc_versions_returns_empty_for_no_records() -> None:
    """all_rc_versions returns [] when records list is empty."""
    assert all_rc_versions([]) == []


def test_all_days_collects_distinct_utc_days() -> None:
    """all_days returns a sorted list of unique UTC date strings."""
    records = [
        {"tokens": {"timestamp": "2026-05-17T03:24:46Z"}},
        {"tokens": {"timestamp": "2026-05-18T10:00:00Z"}},
        {"tokens": {"timestamp": "2026-05-17T23:59:59Z"}},  # same day as first
        {"tokens": {}},  # no timestamp — skipped
    ]
    result = all_days(records)
    assert result == ["2026-05-17", "2026-05-18"]


def test_all_days_returns_empty_for_no_records() -> None:
    """all_days returns [] when records list is empty."""
    assert all_days([]) == []


def test_all_days_returns_empty_when_all_timestamps_malformed() -> None:
    """all_days skips records with malformed timestamps."""
    records = [
        {"tokens": {"timestamp": "not-a-date"}},
        {"tokens": {"timestamp": ""}},
    ]
    assert all_days(records) == []


# ---------------------------------------------------------------------------
# scan_tasks()
# ---------------------------------------------------------------------------


def test_scan_tasks_returns_empty_when_tasks_dir_absent(
    tmp_path: pathlib.Path,
) -> None:
    """scan_tasks returns [] when the tasks directory does not exist."""
    result = scan_tasks(tmp_path / "tasks")
    assert result == []


def test_scan_tasks_skips_tasks_without_tokens_json(
    tmp_path: pathlib.Path,
) -> None:
    """scan_tasks skips task directories that have no artifacts/tokens.json."""
    tasks_dir = tmp_path / "tasks"
    task_dir = tasks_dir / "CODER-20260101-001-no-tokens"
    task_dir.mkdir(parents=True)
    # No tokens.json written

    result = scan_tasks(tasks_dir)
    assert result == []


def test_scan_tasks_includes_task_with_valid_tokens_json(
    tmp_path: pathlib.Path,
) -> None:
    """scan_tasks returns one record for a task with a valid tokens.json."""
    tasks_dir = tmp_path / "tasks"
    task_dir = tasks_dir / "CODER-20260101-001-slug"
    task_dir.mkdir(parents=True)
    _write_tokens_json(task_dir, _minimal_legacy_token())
    _write_readme(task_dir, release_version="v0.1.0")

    result = scan_tasks(tasks_dir)
    assert len(result) == 1
    assert result[0]["task_id"] == "CODER-20260101-001-slug"
    assert result[0]["rc_version"] == "v0.1.0"
    assert result[0]["new_schema"] is False


def test_scan_tasks_detects_new_schema_record(tmp_path: pathlib.Path) -> None:
    """scan_tasks sets new_schema=True for a new-schema tokens.json."""
    tasks_dir = tmp_path / "tasks"
    task_dir = tasks_dir / "CODER-20260101-001-slug"
    task_dir.mkdir(parents=True)
    _write_tokens_json(task_dir, _minimal_new_schema_token())
    _write_readme(task_dir)

    result = scan_tasks(tasks_dir)
    assert len(result) == 1
    assert result[0]["new_schema"] is True


def test_scan_tasks_skips_queues_subdirectory(tmp_path: pathlib.Path) -> None:
    """scan_tasks does not descend into the 'queues' subdirectory."""
    tasks_dir = tmp_path / "tasks"
    queues_dir = tasks_dir / "queues"
    queues_dir.mkdir(parents=True)
    # Write a tokens.json inside queues — it must not be picked up
    (queues_dir / "artifacts").mkdir()
    (queues_dir / "artifacts" / "tokens.json").write_text("{}", encoding="utf-8")

    result = scan_tasks(tasks_dir)
    assert result == []


def test_scan_tasks_captures_flag_on_uncaptured_record(
    tmp_path: pathlib.Path,
) -> None:
    """scan_tasks sets captured=False for a record with captured: false."""
    tasks_dir = tmp_path / "tasks"
    task_dir = tasks_dir / "CODER-20260101-001-killed"
    task_dir.mkdir(parents=True)
    tok = _minimal_legacy_token()
    tok["captured"] = False
    _write_tokens_json(task_dir, tok)
    _write_readme(task_dir)

    result = scan_tasks(tasks_dir)
    assert len(result) == 1
    assert result[0]["captured"] is False


# ---------------------------------------------------------------------------
# build_rc_rollup() — per-RC roll-up
# ---------------------------------------------------------------------------


def _make_rc_record(
    task_id: str = "CODER-20260101-001-slug",
    rc_version: str = "v0.1.0",
    tok: dict | None = None,
    new_schema: bool = False,
    captured: bool = True,
) -> dict:
    """Build a synthetic scan record for build_rc_rollup / build_day_rollup."""
    if tok is None:
        tok = _minimal_legacy_token()
    return {
        "task_id":    task_id,
        "task_dir":   pathlib.Path("/synthetic") / task_id,
        "rc_version": rc_version,
        "tokens":     tok,
        "agent":      task_id.split("-")[0].lower(),
        "new_schema": new_schema,
        "captured":   captured,
    }


def test_build_rc_rollup_produces_valid_json_output(
    tmp_path: pathlib.Path,
) -> None:
    """build_rc_rollup writes a JSON file that can be parsed back."""
    usage_rc_dir = tmp_path / "usage" / "rc"
    records = [_make_rc_record()]

    out_path = build_rc_rollup(
        project="test-project",
        rc_version="v0.1.0",
        records=records,
        pricing={},
        usage_rc_dir=usage_rc_dir,
    )

    assert out_path.exists()
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)


def test_build_rc_rollup_sets_version_field(tmp_path: pathlib.Path) -> None:
    """build_rc_rollup normalises and stores the RC version in the output."""
    usage_rc_dir = tmp_path / "usage" / "rc"
    records = [_make_rc_record(rc_version="v0.1.0")]

    out_path = build_rc_rollup(
        project="test-project",
        rc_version="0.1.0",  # bare version — should be normalised
        records=records,
        pricing={},
        usage_rc_dir=usage_rc_dir,
    )

    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["version"] == "v0.1.0"


def test_build_rc_rollup_includes_task_in_tasks_array(
    tmp_path: pathlib.Path,
) -> None:
    """build_rc_rollup includes each matching task in the 'tasks' array."""
    usage_rc_dir = tmp_path / "usage" / "rc"
    records = [_make_rc_record(task_id="CODER-20260101-001-slug", rc_version="v0.1.0")]

    out_path = build_rc_rollup("p", "v0.1.0", records, {}, usage_rc_dir)

    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert len(data["tasks"]) == 1
    assert data["tasks"][0]["task_id"] == "CODER-20260101-001-slug"


def test_build_rc_rollup_excludes_tasks_from_other_versions(
    tmp_path: pathlib.Path,
) -> None:
    """build_rc_rollup only includes tasks whose rc_version matches the target version."""
    usage_rc_dir = tmp_path / "usage" / "rc"
    records = [
        _make_rc_record(task_id="CODER-20260101-001-a", rc_version="v0.1.0"),
        _make_rc_record(task_id="CODER-20260101-002-b", rc_version="v0.2.0"),
    ]

    out_path = build_rc_rollup("p", "v0.1.0", records, {}, usage_rc_dir)

    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert len(data["tasks"]) == 1
    assert data["tasks"][0]["task_id"] == "CODER-20260101-001-a"


def test_build_rc_rollup_sums_invocations_in_totals(tmp_path: pathlib.Path) -> None:
    """build_rc_rollup totals.invocations is the sum across all matching tasks."""
    usage_rc_dir = tmp_path / "usage" / "rc"
    tok1 = _minimal_legacy_token(invocations=3)
    tok2 = _minimal_legacy_token(invocations=5)
    records = [
        _make_rc_record("CODER-20260101-001-a", "v0.1.0", tok1),
        _make_rc_record("CODER-20260101-002-b", "v0.1.0", tok2),
    ]

    out_path = build_rc_rollup("p", "v0.1.0", records, {}, usage_rc_dir)

    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["totals"]["invocations"] == 8


def test_build_rc_rollup_handles_empty_records(tmp_path: pathlib.Path) -> None:
    """build_rc_rollup produces a valid output with zero tasks when records is empty."""
    usage_rc_dir = tmp_path / "usage" / "rc"

    out_path = build_rc_rollup("p", "v0.1.0", [], {}, usage_rc_dir)

    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["tasks"] == []
    assert data["totals"]["invocations"] == 0
    assert data["totals"]["cost_usd"] == 0.0


def test_build_rc_rollup_marks_uncaptured_tasks(tmp_path: pathlib.Path) -> None:
    """build_rc_rollup marks uncaptured tasks and counts them in totals.uncaptured_tasks."""
    usage_rc_dir = tmp_path / "usage" / "rc"
    tok = _minimal_legacy_token()
    tok["captured"] = False
    records = [_make_rc_record("CODER-20260101-001-killed", "v0.1.0", tok, captured=False)]

    out_path = build_rc_rollup("p", "v0.1.0", records, {}, usage_rc_dir)

    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["totals"]["uncaptured_tasks"] == 1
    assert data["tasks"][0].get("captured") is False


def test_build_rc_rollup_uses_new_schema_total_cost(tmp_path: pathlib.Path) -> None:
    """build_rc_rollup uses total_cost_usd (not pricing table) for new-schema records."""
    usage_rc_dir = tmp_path / "usage" / "rc"
    tok = _minimal_new_schema_token(total_cost_usd=0.05)
    records = [_make_rc_record("CODER-20260101-001-slug", "v0.1.0", tok, new_schema=True)]

    out_path = build_rc_rollup("p", "v0.1.0", records, {}, usage_rc_dir)

    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert abs(data["totals"]["cost_usd"] - 0.05) < 1e-6


def test_build_rc_rollup_creates_output_directory(tmp_path: pathlib.Path) -> None:
    """build_rc_rollup creates usage/rc/ if it does not exist."""
    usage_rc_dir = tmp_path / "does" / "not" / "exist"
    records = [_make_rc_record()]

    out_path = build_rc_rollup("p", "v0.1.0", records, {}, usage_rc_dir)

    assert out_path.exists()


def test_build_rc_rollup_totals_contain_required_schema_keys(
    tmp_path: pathlib.Path,
) -> None:
    """build_rc_rollup totals block contains all documented output schema keys."""
    usage_rc_dir = tmp_path / "usage" / "rc"
    records = [_make_rc_record()]
    required_keys = {
        "input_tokens", "output_tokens", "cache_creation_tokens",
        "cache_read_tokens", "cost_usd", "input_cost_usd",
        "cache_creation_cost_usd", "cache_read_cost_usd", "output_cost_usd",
        "invocations", "uncaptured_tasks",
    }

    out_path = build_rc_rollup("p", "v0.1.0", records, {}, usage_rc_dir)

    data = json.loads(out_path.read_text(encoding="utf-8"))
    for key in required_keys:
        assert key in data["totals"], f"Missing totals key: {key}"


# ---------------------------------------------------------------------------
# build_day_rollup() — per-day roll-up
# ---------------------------------------------------------------------------


def _make_day_record(
    task_id: str = "CODER-20260101-001-slug",
    rc_version: str = "v0.1.0",
    timestamp: str = "2026-05-17T03:24:46Z",
    tok: dict | None = None,
    new_schema: bool = False,
    captured: bool = True,
) -> dict:
    """Build a synthetic scan record with an explicit timestamp for day rollup tests."""
    if tok is None:
        tok = _minimal_legacy_token(timestamp=timestamp)
    rec = _make_rc_record(task_id, rc_version, tok, new_schema, captured)
    rec["tokens"] = dict(tok)
    rec["tokens"]["timestamp"] = timestamp
    return rec


def test_build_day_rollup_produces_valid_json_output(tmp_path: pathlib.Path) -> None:
    """build_day_rollup writes a JSON file that can be parsed back."""
    usage_daily_dir = tmp_path / "usage" / "daily"
    records = [_make_day_record(timestamp="2026-05-17T03:24:46Z")]

    out_path = build_day_rollup("p", "2026-05-17", records, {}, usage_daily_dir)

    assert out_path.exists()
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)


def test_build_day_rollup_filters_records_by_day(tmp_path: pathlib.Path) -> None:
    """build_day_rollup includes only records whose timestamp falls on the target day."""
    usage_daily_dir = tmp_path / "usage" / "daily"
    records = [
        _make_day_record("CODER-20260101-001-a", timestamp="2026-05-17T03:24:46Z"),
        _make_day_record("CODER-20260101-002-b", timestamp="2026-05-18T10:00:00Z"),
    ]

    out_path = build_day_rollup("p", "2026-05-17", records, {}, usage_daily_dir)

    data = json.loads(out_path.read_text(encoding="utf-8"))
    # Only one task falls on 2026-05-17
    assert data["totals"]["invocations"] == 1


def test_build_day_rollup_empty_records_produces_zero_totals(
    tmp_path: pathlib.Path,
) -> None:
    """build_day_rollup with no matching records produces a valid zero-totals output."""
    usage_daily_dir = tmp_path / "usage" / "daily"

    out_path = build_day_rollup("p", "2026-05-17", [], {}, usage_daily_dir)

    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["totals"]["invocations"] == 0
    assert data["totals"]["cost_usd"] == 0.0
    assert data["by_agent"] == {}
    assert data["by_model"] == {}


def test_build_day_rollup_collects_rc_versions(tmp_path: pathlib.Path) -> None:
    """build_day_rollup lists distinct RC versions seen on the day in rcs_shipped."""
    usage_daily_dir = tmp_path / "usage" / "daily"
    records = [
        _make_day_record("CODER-20260101-001-a", rc_version="v0.1.0",
                         timestamp="2026-05-17T01:00:00Z"),
        _make_day_record("CODER-20260101-002-b", rc_version="v0.2.0",
                         timestamp="2026-05-17T02:00:00Z"),
        _make_day_record("CODER-20260101-003-c", rc_version="v0.1.0",
                         timestamp="2026-05-17T03:00:00Z"),  # duplicate RC
    ]

    out_path = build_day_rollup("p", "2026-05-17", records, {}, usage_daily_dir)

    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert sorted(data["rcs_shipped"]) == ["v0.1.0", "v0.2.0"]


def test_build_day_rollup_accumulates_by_agent(tmp_path: pathlib.Path) -> None:
    """build_day_rollup groups invocations by agent name in by_agent."""
    usage_daily_dir = tmp_path / "usage" / "daily"
    tok1 = _minimal_legacy_token(agent="coder", invocations=2,
                                  timestamp="2026-05-17T01:00:00Z")
    tok2 = _minimal_legacy_token(agent="writer", invocations=3,
                                  timestamp="2026-05-17T02:00:00Z")
    records = [
        _make_day_record("CODER-20260101-001-a", tok=tok1,
                         timestamp="2026-05-17T01:00:00Z"),
        _make_day_record("WRITER-20260101-001-b", tok=tok2,
                         timestamp="2026-05-17T02:00:00Z"),
    ]
    # Override agent keys
    records[0]["agent"] = "coder"
    records[1]["agent"] = "writer"

    out_path = build_day_rollup("p", "2026-05-17", records, {}, usage_daily_dir)

    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert "coder" in data["by_agent"]
    assert "writer" in data["by_agent"]
    assert data["by_agent"]["coder"]["invocations"] == 2
    assert data["by_agent"]["writer"]["invocations"] == 3


def test_build_day_rollup_accumulates_by_model_for_new_schema(
    tmp_path: pathlib.Path,
) -> None:
    """build_day_rollup populates by_model using model_usage from new-schema records."""
    usage_daily_dir = tmp_path / "usage" / "daily"
    tok = _minimal_new_schema_token(
        model_name="claude-opus-4-7",
        model_cost_usd=0.026,
        timestamp="2026-05-17T03:24:46Z",
    )
    records = [_make_day_record("CODER-20260101-001-slug", tok=tok,
                                 new_schema=True, timestamp="2026-05-17T03:24:46Z")]
    records[0]["new_schema"] = True

    out_path = build_day_rollup("p", "2026-05-17", records, {}, usage_daily_dir)

    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert "claude-opus-4-7" in data["by_model"]


def test_build_day_rollup_excludes_uncaptured_from_totals(
    tmp_path: pathlib.Path,
) -> None:
    """build_day_rollup does not add token/cost totals for uncaptured records."""
    usage_daily_dir = tmp_path / "usage" / "daily"
    tok = _minimal_legacy_token(input_tokens=1000, timestamp="2026-05-17T01:00:00Z")
    tok["captured"] = False
    records = [_make_day_record("CODER-20260101-001-killed", tok=tok,
                                 captured=False, timestamp="2026-05-17T01:00:00Z")]
    records[0]["captured"] = False

    out_path = build_day_rollup("p", "2026-05-17", records, {}, usage_daily_dir)

    data = json.loads(out_path.read_text(encoding="utf-8"))
    # Cost and tokens should be 0 since the one record is uncaptured
    assert data["totals"]["input_tokens"] == 0
    assert data["totals"]["uncaptured_tasks"] == 1


def test_build_day_rollup_output_has_required_schema_keys(
    tmp_path: pathlib.Path,
) -> None:
    """build_day_rollup output contains all required top-level and totals keys."""
    usage_daily_dir = tmp_path / "usage" / "daily"
    records = [_make_day_record(timestamp="2026-05-17T01:00:00Z")]
    required_top_level = {"date", "project", "rcs_shipped", "totals", "by_agent", "by_model"}
    required_totals = {
        "input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens",
        "cost_usd", "input_cost_usd", "cache_creation_cost_usd",
        "cache_read_cost_usd", "output_cost_usd", "invocations", "uncaptured_tasks",
    }

    out_path = build_day_rollup("p", "2026-05-17", records, {}, usage_daily_dir)

    data = json.loads(out_path.read_text(encoding="utf-8"))
    for key in required_top_level:
        assert key in data, f"Missing top-level key: {key}"
    for key in required_totals:
        assert key in data["totals"], f"Missing totals key: {key}"


def test_build_day_rollup_creates_output_directory(tmp_path: pathlib.Path) -> None:
    """build_day_rollup creates usage/daily/ if it does not exist."""
    usage_daily_dir = tmp_path / "does" / "not" / "exist"
    records = []

    out_path = build_day_rollup("p", "2026-05-17", records, {}, usage_daily_dir)

    assert out_path.exists()


def test_build_day_rollup_sets_date_and_project_fields(
    tmp_path: pathlib.Path,
) -> None:
    """build_day_rollup sets 'date' and 'project' fields correctly in the output."""
    usage_daily_dir = tmp_path / "usage" / "daily"

    out_path = build_day_rollup("my-project", "2026-05-17", [], {}, usage_daily_dir)

    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["date"] == "2026-05-17"
    assert data["project"] == "my-project"
