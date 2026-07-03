"""
test_metrics_csv_writer.py
==========================
Behavioral unit tests for team/scripts/lib/metrics_csv_writer.py.

All filesystem seams are constructed in tmp_path (pytest-managed).  No live
kanban root or real history.csv is accessed.

Functions / behaviors under test:
  - HISTORY_COLUMNS: column order is fixed
  - _cache_hit_rate: calculation formula
  - _rollup_to_row: flat row extraction from rollup dict
  - append_rc_row: write header + data row; idempotency guard; parent mkdir
"""

from __future__ import annotations

import csv
import pathlib
import sys
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Ensure the metrics_csv_writer module is importable from the dev tree.
# ---------------------------------------------------------------------------
_REPO_ROOT = pathlib.Path(__file__).parent.parent.parent.parent  # team/../..
_LIB_PATH = _REPO_ROOT / "team" / "scripts" / "lib"
if str(_LIB_PATH) not in sys.path:
    sys.path.insert(0, str(_LIB_PATH))

import metrics_csv_writer as mcw  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_rollup(
    rc: str = "v0.24.7",
    project: str = "test-project",
    input_tokens: int = 1000,
    output_tokens: int = 500,
    cache_read: int = 0,
    cache_write: int = 0,
    tasks_total: int = 3,
    pm: int = 1,
    coder: int = 1,
    writer: int = 0,
    tester: int = 1,
    cm_count: int = 0,
    opened_at: str | None = None,
    closed_at: str | None = None,
    wall_time_minutes: float | None = None,
    outcome: str = "UNKNOWN",
    bugs_filed: list | None = None,
    operator_interventions: list | None = None,
    workflow_type: str = "release",
) -> dict[str, Any]:
    """Return a minimal per-RC rollup dict for testing."""
    return {
        "rc": rc,
        "project": project,
        "workflow_type": workflow_type,
        "opened_at": opened_at,
        "closed_at": closed_at,
        "wall_time_minutes": wall_time_minutes,
        "outcome": outcome,
        "tasks": {
            "total": tasks_total,
            "by_agent": {
                "pm": pm,
                "coder": coder,
                "writer": writer,
                "tester": tester,
                "cm": cm_count,
            },
        },
        "tokens": {
            "total": {
                "input": input_tokens,
                "output": output_tokens,
                "cache_read": cache_read,
                "cache_write": cache_write,
            }
        },
        "bugs_filed_during_verification": bugs_filed or [],
        "operator_interventions": operator_interventions or [],
    }


# ---------------------------------------------------------------------------
# HISTORY_COLUMNS — contract
# ---------------------------------------------------------------------------


def test_history_columns_starts_with_rc() -> None:
    """HISTORY_COLUMNS must start with 'rc' as the first column."""
    assert mcw.HISTORY_COLUMNS[0] == "rc"


def test_history_columns_contains_all_required_fields() -> None:
    """HISTORY_COLUMNS includes all documented column names."""
    required = {
        "rc", "project", "workflow_type", "opened_at", "closed_at",
        "wall_time_minutes", "outcome", "tasks_total", "tasks_pm", "tasks_coder",
        "tasks_writer", "tasks_tester", "tasks_cm", "input_tokens", "output_tokens",
        "cache_read_tokens", "cache_write_tokens", "cache_hit_rate_pct",
        "bugs_filed_during", "operator_waivers",
    }
    assert required == set(mcw.HISTORY_COLUMNS)


# ---------------------------------------------------------------------------
# _cache_hit_rate
# ---------------------------------------------------------------------------


def test_cache_hit_rate_zero_when_no_tokens() -> None:
    """_cache_hit_rate returns 0.0 when both inputs are zero."""
    assert mcw._cache_hit_rate(0, 0) == 0.0


def test_cache_hit_rate_correct_formula() -> None:
    """_cache_hit_rate = cache_read / (input + cache_read) * 100."""
    # 1000 cache_read, 1000 input -> 50%
    assert mcw._cache_hit_rate(1000, 1000) == 50.0


def test_cache_hit_rate_rounds_to_one_decimal() -> None:
    """_cache_hit_rate rounds the result to one decimal place."""
    # 1 cache_read, 2 input -> 1/3 * 100 = 33.3...
    result = mcw._cache_hit_rate(2, 1)
    assert result == round(1 / 3 * 100, 1)


def test_cache_hit_rate_one_hundred_percent_when_all_cached() -> None:
    """_cache_hit_rate returns 100.0 when input is 0 and cache_read is non-zero."""
    assert mcw._cache_hit_rate(0, 5000) == 100.0


# ---------------------------------------------------------------------------
# _rollup_to_row
# ---------------------------------------------------------------------------


def test_rollup_to_row_extracts_rc_and_project() -> None:
    """_rollup_to_row includes the rc and project fields."""
    row = mcw._rollup_to_row(_minimal_rollup(rc="v0.24.7", project="myproject"))
    assert row["rc"] == "v0.24.7"
    assert row["project"] == "myproject"


def test_rollup_to_row_extracts_token_counts() -> None:
    """_rollup_to_row extracts input/output/cache token counts from tokens.total."""
    row = mcw._rollup_to_row(_minimal_rollup(input_tokens=1234, output_tokens=567, cache_read=89))
    assert row["input_tokens"] == 1234
    assert row["output_tokens"] == 567
    assert row["cache_read_tokens"] == 89


def test_rollup_to_row_computes_cache_hit_rate() -> None:
    """_rollup_to_row correctly computes cache_hit_rate_pct from token counts."""
    row = mcw._rollup_to_row(_minimal_rollup(input_tokens=1000, cache_read=1000))
    assert row["cache_hit_rate_pct"] == 50.0


def test_rollup_to_row_extracts_agent_task_counts() -> None:
    """_rollup_to_row extracts per-agent task counts from tasks.by_agent."""
    row = mcw._rollup_to_row(_minimal_rollup(pm=2, coder=5, writer=1, tester=2, cm_count=3))
    assert row["tasks_pm"] == 2
    assert row["tasks_coder"] == 5
    assert row["tasks_writer"] == 1
    assert row["tasks_tester"] == 2
    assert row["tasks_cm"] == 3


def test_rollup_to_row_counts_bugs_filed_during() -> None:
    """_rollup_to_row sets bugs_filed_during to the length of bugs_filed_during_verification."""
    row = mcw._rollup_to_row(_minimal_rollup(bugs_filed=["BUG-0099", "BUG-0100"]))
    assert row["bugs_filed_during"] == 2


def test_rollup_to_row_counts_operator_waivers() -> None:
    """_rollup_to_row sets operator_waivers to the length of operator_interventions."""
    row = mcw._rollup_to_row(_minimal_rollup(operator_interventions=["waiver-1"]))
    assert row["operator_waivers"] == 1


def test_rollup_to_row_wall_time_empty_when_none() -> None:
    """_rollup_to_row uses empty string for wall_time_minutes when the value is None."""
    row = mcw._rollup_to_row(_minimal_rollup(wall_time_minutes=None))
    assert row["wall_time_minutes"] == ""


def test_rollup_to_row_wall_time_preserved_when_numeric() -> None:
    """_rollup_to_row preserves numeric wall_time_minutes."""
    row = mcw._rollup_to_row(_minimal_rollup(wall_time_minutes=86.5))
    assert row["wall_time_minutes"] == 86.5


def test_rollup_to_row_handles_missing_tasks_by_agent(tmp_path: pathlib.Path) -> None:
    """_rollup_to_row defaults per-agent task counts to 0 when by_agent is absent."""
    rollup = {
        "rc": "v0.24.7",
        "project": "p",
        "workflow_type": "release",
        "outcome": "UNKNOWN",
        "tasks": {"total": 0},
        "tokens": {"total": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}},
        "bugs_filed_during_verification": [],
        "operator_interventions": [],
    }
    row = mcw._rollup_to_row(rollup)
    assert row["tasks_pm"] == 0
    assert row["tasks_coder"] == 0


# ---------------------------------------------------------------------------
# append_rc_row — main I/O function
# ---------------------------------------------------------------------------


def test_append_rc_row_creates_file_with_header_on_first_call(
    tmp_path: pathlib.Path,
) -> None:
    """append_rc_row creates history.csv with a header row on first call."""
    csv_path = tmp_path / "history.csv"
    mcw.append_rc_row(csv_path, _minimal_rollup())
    assert csv_path.exists()
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 2  # header + data row
    assert lines[0].startswith("rc,")


def test_append_rc_row_header_matches_history_columns(tmp_path: pathlib.Path) -> None:
    """append_rc_row writes a header that matches HISTORY_COLUMNS exactly."""
    csv_path = tmp_path / "history.csv"
    mcw.append_rc_row(csv_path, _minimal_rollup())
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == mcw.HISTORY_COLUMNS


def test_append_rc_row_data_row_contains_rc_value(tmp_path: pathlib.Path) -> None:
    """append_rc_row writes the RC version into the first data row."""
    csv_path = tmp_path / "history.csv"
    mcw.append_rc_row(csv_path, _minimal_rollup(rc="v0.24.7"))
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["rc"] == "v0.24.7"


def test_append_rc_row_appends_multiple_rows_without_re_writing_header(
    tmp_path: pathlib.Path,
) -> None:
    """append_rc_row appends subsequent rows without re-writing the header."""
    csv_path = tmp_path / "history.csv"
    mcw.append_rc_row(csv_path, _minimal_rollup(rc="v0.24.7"))
    mcw.append_rc_row(csv_path, _minimal_rollup(rc="v0.24.8"))
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    assert len(rows) == 2
    assert rows[0]["rc"] == "v0.24.7"
    assert rows[1]["rc"] == "v0.24.8"


def test_append_rc_row_returns_true_on_success(tmp_path: pathlib.Path) -> None:
    """append_rc_row returns True when the row is written successfully."""
    csv_path = tmp_path / "history.csv"
    result = mcw.append_rc_row(csv_path, _minimal_rollup())
    assert result is True


def test_append_rc_row_skips_duplicate_rc_and_returns_false(
    tmp_path: pathlib.Path,
) -> None:
    """append_rc_row returns False and skips the write when the RC is already present."""
    csv_path = tmp_path / "history.csv"
    mcw.append_rc_row(csv_path, _minimal_rollup(rc="v0.24.7"))
    result = mcw.append_rc_row(csv_path, _minimal_rollup(rc="v0.24.7"))
    assert result is False
    # File must still have exactly one data row
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    assert len(rows) == 1


def test_append_rc_row_creates_parent_directory_if_absent(
    tmp_path: pathlib.Path,
) -> None:
    """append_rc_row creates parent directories when they do not exist."""
    csv_path = tmp_path / "metrics" / "history.csv"
    assert not csv_path.parent.exists()
    mcw.append_rc_row(csv_path, _minimal_rollup())
    assert csv_path.exists()


def test_append_rc_row_token_counts_are_correct_in_row(tmp_path: pathlib.Path) -> None:
    """append_rc_row writes correct token counts into the CSV row."""
    csv_path = tmp_path / "history.csv"
    mcw.append_rc_row(
        csv_path,
        _minimal_rollup(input_tokens=5000, output_tokens=2500, cache_read=1000),
    )
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    assert rows[0]["input_tokens"] == "5000"
    assert rows[0]["output_tokens"] == "2500"
    assert rows[0]["cache_read_tokens"] == "1000"


def test_append_rc_row_cache_hit_rate_computed_in_row(tmp_path: pathlib.Path) -> None:
    """append_rc_row computes cache_hit_rate_pct correctly in the CSV row."""
    csv_path = tmp_path / "history.csv"
    # input=1000, cache_read=1000 -> 50%
    mcw.append_rc_row(
        csv_path,
        _minimal_rollup(input_tokens=1000, cache_read=1000),
    )
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    assert float(rows[0]["cache_hit_rate_pct"]) == 50.0


def test_append_rc_row_workflow_type_preserved(tmp_path: pathlib.Path) -> None:
    """append_rc_row preserves the workflow_type field from the rollup."""
    csv_path = tmp_path / "history.csv"
    mcw.append_rc_row(csv_path, _minimal_rollup(workflow_type="document"))
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    assert rows[0]["workflow_type"] == "document"
