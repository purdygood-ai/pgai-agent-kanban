"""
test_history_csv_short_header.py — a history.csv whose header predates the
blocked_minutes column reads correctly on every surface, before and after
the writer widens its header.

The fixture is the shape such a file takes in the field: the 20-name
header (everything up to operator_waivers), three rows written before
blocked_minutes existed (20 fields), and two rows written since (21 fields:
one measured figure, one real 0). Every reading surface goes through the
one shared reader, pgai_agent_kanban.lib.metrics.history.read_history_csv:

  - read_history_csv itself,
  - lib.metrics.metrics_rows_for_project (GET /metrics?format=json's rows),
  - lib.metrics.show_metrics (show-metrics.sh and GET /metrics' text),
  - GET /metrics, both formats, through the in-process API client.

Each is checked on the unmigrated file and again after one
metrics_csv_writer.append_rc_row call has widened the header. A header that
is not a prefix of the column list is read exactly as written.

All files are built under pytest's tmp_path; no live kanban root is read.
"""

from __future__ import annotations

import pathlib
import re
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))
_WRITER_DIR = _TEAM_DIR / "scripts" / "lib"
if str(_WRITER_DIR) not in sys.path:
    sys.path.insert(0, str(_WRITER_DIR))

import metrics_csv_writer as mcw  # noqa: E402
from pgai_agent_kanban.lib import metrics  # noqa: E402
from pgai_agent_kanban.lib.metrics.history import (  # noqa: E402
    HISTORY_COLUMNS,
    is_short_history_header,
    read_history_csv,
)
from tests.fixtures import metrics_cases as cases  # noqa: E402

PROJECT = "demo"
_SHORT_HEADER = HISTORY_COLUMNS[:-1]  # every column up to operator_waivers

# (rc, blocked_minutes field or None for a row written before the column)
_FIXTURE_ROWS = (
    ("v1.0.0", None),
    ("v1.1.0", None),
    ("v1.2.0", None),
    ("v1.3.0", "836.1"),
    ("v1.4.0", "0"),
)
_EXPECTED_BLOCKED = [None, None, None, "836.1", "0"]

# One rendered history-table data row: RC, Wall, Blocked, then five more.
_TABLE_ROW_RE = re.compile(r"^(v\S+)\s+(\S+)\s+(\S+)\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s*$")


def _line(rc: str, blocked: str | None) -> str:
    values = {c: "" for c in _SHORT_HEADER}
    values.update({
        "rc": rc, "project": PROJECT, "workflow_type": "release", "wall_time_minutes": "60",
        "outcome": "SHIPPED", "tasks_total": "2", "input_tokens": "1000", "output_tokens": "500",
        "cache_read_tokens": "0", "cache_write_tokens": "0",
    })
    fields = [values[c] for c in _SHORT_HEADER]
    if blocked is not None:
        fields.append(blocked)
    return ",".join(fields)


@pytest.fixture
def kanban_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """A kanban root whose one project holds the short-header history.csv."""
    csv_path = tmp_path / "projects" / PROJECT / "metrics" / "history.csv"
    csv_path.parent.mkdir(parents=True)
    lines = [",".join(_SHORT_HEADER)] + [_line(rc, b) for rc, b in _FIXTURE_ROWS]
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def client(kanban_root):
    return cases.build_client(kanban_root)


def _history(root: pathlib.Path) -> pathlib.Path:
    return root / "projects" / PROJECT / "metrics" / "history.csv"


def _append_new_rc(root: pathlib.Path, blocked: float) -> None:
    rollup = {
        "rc": "v1.5.0", "project": PROJECT, "workflow_type": "release", "outcome": "SHIPPED",
        "wall_time_minutes": 60, "blocked_minutes": blocked,
        "tasks": {"total": 2, "by_agent": {}},
        "tokens": {"total": {"input": 1000, "output": 500, "cache_read": 0, "cache_write": 0}},
    }
    assert mcw.append_rc_row(_history(root), rollup) is True


def _blocked_column(text: str) -> dict[str, str]:
    """RC -> the Blocked cell of each rendered history-table row."""
    out = {}
    for line in text.splitlines():
        m = _TABLE_ROW_RE.match(line)
        if m:
            out[m.group(1)] = m.group(3)
    return out


# ---------------------------------------------------------------------------
# The header test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header, expected",
    [
        (list(HISTORY_COLUMNS[:-1]), True),
        (["rc"], True),
        ([" rc", "project "], True),
        (list(HISTORY_COLUMNS), False),
        ([], False),
        (["project", "rc"], False),
        (list(HISTORY_COLUMNS) + ["extra"], False),
    ],
)
def test_short_header_is_a_strict_non_empty_prefix(header, expected):
    assert is_short_history_header(header) is expected


# ---------------------------------------------------------------------------
# Unmigrated file: the reader already yields the trailing values
# ---------------------------------------------------------------------------


def test_reader_keys_fields_past_a_short_header_by_column_name(kanban_root):
    fieldnames, rows = read_history_csv(_history(kanban_root))

    assert fieldnames == list(HISTORY_COLUMNS)
    assert [r["rc"] for r in rows] == [rc for rc, _ in _FIXTURE_ROWS]
    assert [r["blocked_minutes"] for r in rows] == _EXPECTED_BLOCKED
    assert all(None not in r for r in rows)


def test_rows_carry_blocked_seconds_only_where_recorded(kanban_root):
    rows = metrics.metrics_rows_for_project(kanban_root / "projects" / PROJECT)

    assert [r.get("blocked_seconds") for r in rows] == [None, None, None, round(836.1 * 60, 1), 0.0]
    assert "blocked_seconds" not in rows[0]  # a pre-column row omits the key, never fills 0


def test_rendered_table_shows_recorded_minutes_and_dash_for_older_rows(kanban_root):
    result = metrics.show_metrics(kanban_root, project=PROJECT)

    assert result.exit_code == 0, result.stderr
    assert _blocked_column(result.stdout) == {
        "v1.0.0": "-", "v1.1.0": "-", "v1.2.0": "-", "v1.3.0": "836", "v1.4.0": "0",
    }


def test_metrics_route_reads_the_unmigrated_file(kanban_root, client):
    with cases.rest_environment(kanban_root):
        as_json = client.get("/metrics", params={"project": PROJECT, "format": "json"})
        as_text = client.get("/metrics", params={"project": PROJECT})

    assert as_json.status_code == 200
    assert [r.get("blocked_seconds") for r in as_json.json()] == [None, None, None, round(836.1 * 60, 1), 0.0]
    assert as_text.status_code == 200
    assert _blocked_column(as_text.json()["stdout"])["v1.3.0"] == "836"


def test_history_csv_view_carries_the_widened_column(kanban_root):
    text = metrics.history_csv(_history(kanban_root))

    lines = text.splitlines()
    assert lines[0] == ",".join(HISTORY_COLUMNS)
    assert lines[4].endswith(",836.1")
    assert lines[1].endswith(",")  # a pre-column row prints an empty blocked_minutes cell


# ---------------------------------------------------------------------------
# After one append the writer has widened the header
# ---------------------------------------------------------------------------


def test_after_one_append_every_surface_reads_the_same_values(kanban_root, client):
    _append_new_rc(kanban_root, blocked=26.1)

    header = _history(kanban_root).read_text(encoding="utf-8").splitlines()[0]
    assert header == ",".join(HISTORY_COLUMNS)

    fieldnames, rows = read_history_csv(_history(kanban_root))
    assert fieldnames == list(HISTORY_COLUMNS)
    assert [r["blocked_minutes"] for r in rows] == _EXPECTED_BLOCKED + ["26.1"]

    with cases.rest_environment(kanban_root):
        as_json = client.get("/metrics", params={"project": PROJECT, "format": "json"})
        as_text = client.get("/metrics", params={"project": PROJECT})
    assert [r.get("blocked_seconds") for r in as_json.json()] == [
        None, None, None, round(836.1 * 60, 1), 0.0, round(26.1 * 60, 1),
    ]
    assert _blocked_column(as_text.json()["stdout"]) == {
        "v1.0.0": "-", "v1.1.0": "-", "v1.2.0": "-", "v1.3.0": "836", "v1.4.0": "0", "v1.5.0": "26",
    }


# ---------------------------------------------------------------------------
# A header that is not a prefix is read as written
# ---------------------------------------------------------------------------


def test_unrecognised_header_is_read_exactly_as_written(tmp_path):
    csv_path = tmp_path / "history.csv"
    csv_path.write_text("project,rc\ndemo,v1.0.0,surplus\n", encoding="utf-8")

    fieldnames, rows = read_history_csv(csv_path)

    assert fieldnames == ["project", "rc"]
    assert rows == [{"project": "demo", "rc": "v1.0.0", None: ["surplus"]}]


def test_fields_beyond_the_last_known_column_stay_surplus(tmp_path):
    csv_path = tmp_path / "history.csv"
    csv_path.write_text(
        ",".join(_SHORT_HEADER) + "\n" + _line("v1.0.0", "5") + ",unexpected\n", encoding="utf-8"
    )

    fieldnames, rows = read_history_csv(csv_path)

    assert fieldnames == list(HISTORY_COLUMNS)
    assert rows[0]["blocked_minutes"] == "5"
    assert rows[0][None] == ["unexpected"]
