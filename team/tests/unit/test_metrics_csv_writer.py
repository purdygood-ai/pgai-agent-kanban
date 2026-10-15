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
  - append_rc_row on an existing file: a short header is widened in one
    atomic, mode-keeping rewrite; any other header mismatch warns and the
    row is still appended; a caller waiting on a replaced file appends to
    the file now at the path
"""

from __future__ import annotations

import csv
import os
import pathlib
import stat
import sys
import threading
import time
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
    blocked_minutes: float | int | None = 0,
    include_blocked_minutes: bool = True,
) -> dict[str, Any]:
    """Return a minimal per-RC rollup dict for testing.

    blocked_minutes defaults to 0 (the "no ledger" real-zero case).  Pass
    None to simulate an unreadable/not-yet-computed ledger, or
    include_blocked_minutes=False to simulate a rollup written before this
    field existed (the key absent entirely) -- both writer-side cases the
    CSV row extractor must degrade to an empty cell.
    """
    rollup: dict[str, Any] = {
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
    if include_blocked_minutes:
        rollup["blocked_minutes"] = blocked_minutes
    return rollup


# ---------------------------------------------------------------------------
# HISTORY_COLUMNS — contract
# ---------------------------------------------------------------------------


_PRE_BLOCKED_COLUMNS = [
    "rc", "project", "workflow_type", "opened_at", "closed_at",
    "wall_time_minutes", "outcome", "tasks_total", "tasks_pm", "tasks_coder",
    "tasks_writer", "tasks_tester", "tasks_cm", "input_tokens", "output_tokens",
    "cache_read_tokens", "cache_write_tokens", "cache_hit_rate_pct",
    "bugs_filed_during", "operator_waivers",
]


def test_history_columns_starts_with_rc() -> None:
    """HISTORY_COLUMNS must start with 'rc' as the first column."""
    assert mcw.HISTORY_COLUMNS[0] == "rc"


def test_history_columns_contains_all_required_fields() -> None:
    """HISTORY_COLUMNS includes all documented column names, including the
    new blocked_minutes column."""
    required = set(_PRE_BLOCKED_COLUMNS) | {"blocked_minutes"}
    assert required == set(mcw.HISTORY_COLUMNS)


def test_history_columns_ends_with_blocked_minutes() -> None:
    """blocked_minutes is the LAST column -- a public-contract requirement
    so every existing positional reader keeps reading the prior
    fields at the same offsets."""
    assert mcw.HISTORY_COLUMNS[-1] == "blocked_minutes"


def test_history_columns_leaves_pre_existing_columns_in_order() -> None:
    """The columns before blocked_minutes are byte-identical, in order, to the
    pre-change HISTORY_COLUMNS -- adding blocked_minutes must not reorder,
    rename, or remove any of them."""
    assert mcw.HISTORY_COLUMNS[:-1] == _PRE_BLOCKED_COLUMNS


# ---------------------------------------------------------------------------
# _cache_hit_rate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "input_tokens, cache_read_tokens, expected",
    [
        (0, 0, 0.0),  # both inputs zero
        (1000, 1000, 50.0),  # cache_read / (input + cache_read) * 100
        (0, 5000, 100.0),  # input is 0 and cache_read is non-zero -> 100%
    ],
)
def test_cache_hit_rate(input_tokens: int, cache_read_tokens: int, expected: float) -> None:
    """_cache_hit_rate computes cache_read / (input + cache_read) * 100."""
    assert mcw._cache_hit_rate(input_tokens, cache_read_tokens) == expected


def test_cache_hit_rate_rounds_to_one_decimal() -> None:
    """_cache_hit_rate rounds the result to one decimal place."""
    # 1 cache_read, 2 input -> 1/3 * 100 = 33.3...
    result = mcw._cache_hit_rate(2, 1)
    assert result == round(1 / 3 * 100, 1)


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
# _rollup_to_row — blocked_minutes
# ---------------------------------------------------------------------------


def test_rollup_to_row_blocked_minutes_real_zero_preserved() -> None:
    """A rollup with no ledger (blocked_minutes=0, the real-zero case) writes
    0, not an empty cell."""
    row = mcw._rollup_to_row(_minimal_rollup(blocked_minutes=0))
    assert row["blocked_minutes"] == 0


def test_rollup_to_row_blocked_minutes_numeric_value_preserved() -> None:
    """A rollup carrying a measured blocked-minutes total preserves it
    unchanged."""
    row = mcw._rollup_to_row(_minimal_rollup(blocked_minutes=50))
    assert row["blocked_minutes"] == 50


def test_rollup_to_row_blocked_minutes_none_writes_empty_and_warns(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An explicit None (ledger present but unreadable) writes an empty
    cell and prints a warning to stderr."""
    row = mcw._rollup_to_row(_minimal_rollup(blocked_minutes=None))
    assert row["blocked_minutes"] == ""
    captured = capsys.readouterr()
    assert "blocked_minutes" in captured.err
    assert "WARNING" in captured.err


def test_rollup_to_row_blocked_minutes_missing_key_writes_empty_and_warns(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A rollup written before this field existed (the key absent
    entirely) degrades the same way an explicit None does -- an empty
    cell with a warning, never a fabricated zero."""
    row = mcw._rollup_to_row(_minimal_rollup(include_blocked_minutes=False))
    assert row["blocked_minutes"] == ""
    captured = capsys.readouterr()
    assert "WARNING" in captured.err


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


# ---------------------------------------------------------------------------
# append_rc_row — blocked_minutes column
# ---------------------------------------------------------------------------


def test_append_rc_row_header_ends_with_blocked_minutes(tmp_path: pathlib.Path) -> None:
    """A freshly written history.csv header ends with blocked_minutes, and
    the fields before it are, in order, identical to the pre-change
    HISTORY_COLUMNS."""
    csv_path = tmp_path / "history.csv"
    mcw.append_rc_row(csv_path, _minimal_rollup())
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames
    assert fieldnames[-1] == "blocked_minutes"
    assert fieldnames[:-1] == _PRE_BLOCKED_COLUMNS


def test_append_rc_row_no_ledger_writes_zero(tmp_path: pathlib.Path) -> None:
    """Appending a row for an RC with no ledger (blocked_minutes=0 in the
    rollup) writes 0 in the new column."""
    csv_path = tmp_path / "history.csv"
    mcw.append_rc_row(csv_path, _minimal_rollup(rc="v0.24.7", blocked_minutes=0))
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    assert rows[0]["blocked_minutes"] == "0"


def test_append_rc_row_unreadable_ledger_writes_empty_cell_and_warns(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Appending a row for an RC whose ledger is unreadable (rollup carries
    blocked_minutes=None) writes an empty cell, prints a warning, and the
    call still succeeds (returns True, raises nothing)."""
    csv_path = tmp_path / "history.csv"
    result = mcw.append_rc_row(csv_path, _minimal_rollup(rc="v0.24.7", blocked_minutes=None))
    assert result is True
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    assert rows[0]["blocked_minutes"] == ""
    captured = capsys.readouterr()
    assert "WARNING" in captured.err


def test_append_rc_row_measured_blocked_minutes_written_correctly(
    tmp_path: pathlib.Path,
) -> None:
    """Appending a row for an RC whose ledger totals a real figure writes
    that figure into the new column."""
    csv_path = tmp_path / "history.csv"
    mcw.append_rc_row(csv_path, _minimal_rollup(rc="v0.24.7", blocked_minutes=50))
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    assert rows[0]["blocked_minutes"] == "50"


def test_append_rc_row_widens_pre_existing_short_header_and_keeps_prior_rows(
    tmp_path: pathlib.Path,
) -> None:
    """A history.csv written before blocked_minutes existed gains the full
    HISTORY_COLUMNS header on the next append; its prior row is kept
    byte-identical and reads blocked_minutes as missing, while the new row
    carries its value under the blocked_minutes key."""
    csv_path = tmp_path / "history.csv"
    old_header = ",".join(_PRE_BLOCKED_COLUMNS)
    old_row = ",".join(
        {
            "rc": "v0.24.6", "project": "test-project", "workflow_type": "release",
            "opened_at": "", "closed_at": "", "wall_time_minutes": "",
            "outcome": "SHIPPED", "tasks_total": "1", "tasks_pm": "0",
            "tasks_coder": "1", "tasks_writer": "0", "tasks_tester": "0",
            "tasks_cm": "0", "input_tokens": "10", "output_tokens": "5",
            "cache_read_tokens": "0", "cache_write_tokens": "0",
            "cache_hit_rate_pct": "0.0", "bugs_filed_during": "0",
            "operator_waivers": "0",
        }[col]
        for col in _PRE_BLOCKED_COLUMNS
    )
    csv_path.write_text(old_header + "\n" + old_row + "\n", encoding="utf-8")

    assert mcw.append_rc_row(csv_path, _minimal_rollup(rc="v0.24.7", blocked_minutes=50)) is True

    lines = csv_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == ",".join(mcw.HISTORY_COLUMNS)  # header widened
    assert lines[1] == old_row  # prior row untouched, not reordered
    with open(csv_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["rc"] for r in rows] == ["v0.24.6", "v0.24.7"]
    assert rows[0]["blocked_minutes"] is None  # short row: missing, never 0
    assert rows[1]["blocked_minutes"] == "50"


# ---------------------------------------------------------------------------
# append_rc_row — an existing file's header
# ---------------------------------------------------------------------------


def _row_values(rc: str, blocked: str | None) -> list[str]:
    """One history.csv row's values in column order: the pre-blocked columns,
    plus a trailing blocked_minutes value when *blocked* is not None."""
    values = {c: "" for c in _PRE_BLOCKED_COLUMNS}
    values.update({"rc": rc, "project": "test-project", "outcome": "SHIPPED"})
    out = [values[c] for c in _PRE_BLOCKED_COLUMNS]
    if blocked is not None:
        out.append(blocked)
    return out


def _write_short_header_history(csv_path: pathlib.Path) -> list[str]:
    """A history.csv with the 20-name header, 3 short rows and 2 rows that
    carry a 21st (blocked_minutes) field; returns its data lines."""
    data_lines = [
        ",".join(_row_values("v1.0.0", None)),
        ",".join(_row_values("v1.1.0", None)),
        ",".join(_row_values("v1.2.0", None)),
        ",".join(_row_values("v1.3.0", "836.1")),
        ",".join(_row_values("v1.4.0", "0")),
    ]
    csv_path.write_text(
        ",".join(_PRE_BLOCKED_COLUMNS) + "\n" + "\n".join(data_lines) + "\n",
        encoding="utf-8",
    )
    return data_lines


def test_short_header_is_widened_once_and_every_row_reads_by_name(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One append to a file whose header predates blocked_minutes rewrites
    the header to the full column list, keeps all five prior rows
    byte-identical, and appends the new row; the short rows read
    blocked_minutes as missing, the long rows and the new row as their
    values."""
    csv_path = tmp_path / "history.csv"
    data_lines = _write_short_header_history(csv_path)

    assert mcw.append_rc_row(csv_path, _minimal_rollup(rc="v1.5.0", blocked_minutes=26.1)) is True

    lines = csv_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == ",".join(mcw.HISTORY_COLUMNS)
    assert lines[1:6] == data_lines
    assert len(lines) == 7
    with open(csv_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["blocked_minutes"] for r in rows] == [None, None, None, "836.1", "0", "26.1"]
    assert all(None not in r for r in rows)  # no surplus fields anywhere
    assert "blocked_minutes" in capsys.readouterr().err  # the migration is narrated


def test_short_header_migration_keeps_the_file_mode(tmp_path: pathlib.Path) -> None:
    """The header rewrite replaces the file, but the new file keeps the old
    one's permission bits."""
    csv_path = tmp_path / "history.csv"
    _write_short_header_history(csv_path)
    csv_path.chmod(0o640)

    mcw.append_rc_row(csv_path, _minimal_rollup(rc="v1.5.0"))

    assert stat.S_IMODE(csv_path.stat().st_mode) == 0o640
    assert csv_path.read_text(encoding="utf-8").startswith(",".join(mcw.HISTORY_COLUMNS) + "\n")


def test_short_header_migration_leaves_no_scratch_file(tmp_path: pathlib.Path) -> None:
    """The atomic rewrite's temp file is renamed over history.csv, so the
    directory holds history.csv alone afterwards."""
    metrics_dir = tmp_path / "metrics"
    metrics_dir.mkdir()
    csv_path = metrics_dir / "history.csv"
    _write_short_header_history(csv_path)

    mcw.append_rc_row(csv_path, _minimal_rollup(rc="v1.5.0"))

    assert sorted(p.name for p in metrics_dir.iterdir()) == ["history.csv"]


def test_duplicate_rc_on_short_header_file_changes_nothing(tmp_path: pathlib.Path) -> None:
    """A duplicate RC is skipped before any header work: the file, short
    header included, is left byte-identical."""
    csv_path = tmp_path / "history.csv"
    _write_short_header_history(csv_path)
    before = csv_path.read_bytes()

    assert mcw.append_rc_row(csv_path, _minimal_rollup(rc="v1.3.0")) is False

    assert csv_path.read_bytes() == before


def test_unrecognised_header_warns_and_still_appends(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A header that is not a prefix of HISTORY_COLUMNS is left as is, a
    warning names the header found and the one expected, and the row is
    still appended in HISTORY_COLUMNS order."""
    csv_path = tmp_path / "history.csv"
    odd_header = "rc,project,notes"
    csv_path.write_text(odd_header + "\nv1.0.0,test-project,hand-edited\n", encoding="utf-8")

    assert mcw.append_rc_row(csv_path, _minimal_rollup(rc="v1.1.0", blocked_minutes=7)) is True

    err = capsys.readouterr().err
    assert "WARNING" in err
    assert odd_header in err
    assert ",".join(mcw.HISTORY_COLUMNS) in err
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == odd_header
    assert len(lines) == 3
    with open(csv_path, newline="", encoding="utf-8") as fh:
        appended = list(csv.reader(fh))[-1]
    assert appended[0] == "v1.1.0"
    assert appended[-1] == "7"
    assert len(appended) == len(mcw.HISTORY_COLUMNS)


def test_unrecognised_header_via_cli_exits_zero(tmp_path: pathlib.Path) -> None:
    """Through the CLI a header mismatch is a warning, never a failure: the
    process exits 0 and the row lands."""
    import json
    import subprocess

    csv_path = tmp_path / "history.csv"
    csv_path.write_text("version,when\nv1.0.0,yesterday\n", encoding="utf-8")
    rollup_path = tmp_path / "rollup.json"
    rollup_path.write_text(json.dumps(_minimal_rollup(rc="v1.1.0")), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(_LIB_PATH / "metrics_csv_writer.py"),
         "--csv-path", str(csv_path), "--rollup-json", str(rollup_path)],
        capture_output=True, text=True, timeout=60,
        env={"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "LANG": "C.UTF-8"},
    )

    assert result.returncode == 0, result.stderr
    assert "WARNING" in result.stderr
    assert csv_path.read_text(encoding="utf-8").splitlines()[-1].startswith("v1.1.0,")


def test_append_waiting_on_a_replaced_file_lands_in_the_new_file(tmp_path: pathlib.Path) -> None:
    """A caller blocked on the lock while another writer replaces
    history.csv (as a header migration does) appends to the file now at the
    path, not to the replaced one, so its row is never lost."""
    csv_path = tmp_path / "history.csv"
    _write_short_header_history(csv_path)
    migrated = csv_path.read_text(encoding="utf-8").replace(
        ",".join(_PRE_BLOCKED_COLUMNS) + "\n", ",".join(mcw.HISTORY_COLUMNS) + "\n", 1
    )

    holder = open(csv_path, "a", encoding="utf-8")
    mcw.fcntl.flock(holder.fileno(), mcw.fcntl.LOCK_EX)
    results: list[bool] = []
    waiter = threading.Thread(
        target=lambda: results.append(mcw.append_rc_row(csv_path, _minimal_rollup(rc="v1.5.0")))
    )
    try:
        waiter.start()
        time.sleep(0.5)  # let the waiter open the old file and block on its lock
        mcw.atomic_write(csv_path, migrated, preserve_mode=True)
    finally:
        mcw.fcntl.flock(holder.fileno(), mcw.fcntl.LOCK_UN)
        holder.close()
    waiter.join(timeout=30)

    assert results == [True]
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == ",".join(mcw.HISTORY_COLUMNS)
    assert lines[-1].startswith("v1.5.0,")
