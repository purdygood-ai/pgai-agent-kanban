"""
test_rc_metrics_rest_parity.py — golden-pair proof that rc-metrics' Blocked
column resolves to the SAME data through the CLI (``show-metrics.sh``) and
its REST twin (``GET /metrics?format=json``).

Neither surface re-derives the Blocked figure: both read the same
``metrics/history.csv`` ``blocked_minutes`` column, itself written at
RC-close time by ``pgai_agent_kanban.metrics.blocked_ledger.blocked_minutes()``
(``team/scripts/lib/metrics_aggregator.py`` -> ``team/scripts/lib/
metrics_csv_writer.py``). This test proves that shared-source contract end
to end: a real ``bash show-metrics.sh`` subprocess (the CLI side) and a
real HTTP call through the production entrypoint on a loopback ephemeral
port (the REST side), over the SAME seeded ``history.csv`` row, resolve to
the identical version/wall/blocked/tokens/tasks figures -- never a second,
independently written parser that could quietly diverge from the
production one.

Integration test contract (team/tests/integration/conftest.py): no
integration point is mocked. The REST server is a real uvicorn subprocess;
the CLI call is a real `bash show-metrics.sh` subprocess; only the
history.csv row's DATA is fake.

Test-name policy (roles/TESTER.md Anti-pattern 6): names describe behavior,
never bug IDs, task IDs, or scaffolding labels.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import urllib.request

from tests.fixtures.api_server import api_server_fixture
from tests.fixtures.golden_board import PROJECT_NAME, build_synthetic_board

# Column order metrics_csv_writer.py's HISTORY_COLUMNS declares -- the row
# below is written in that exact order so a real reader (show-metrics.sh's
# csv.DictReader, lib.metrics.history.read_history_csv's csv.DictReader) parses it identically to
# a row the real aggregator would have appended.
_HISTORY_HEADER = (
    "rc,project,workflow_type,opened_at,closed_at,wall_time_minutes,outcome,"
    "tasks_total,tasks_pm,tasks_coder,tasks_writer,tasks_tester,tasks_cm,"
    "input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,"
    "cache_hit_rate_pct,bugs_filed_during,operator_waivers,blocked_minutes"
)

# A single RC row with a non-zero blocked_minutes -- the fixture's "blocked
# task" is build_synthetic_board's own BLOCKED-state task; this row is the
# already-aggregated figure the real pipeline would have written for an RC
# whose chain stalled on it (blocked_ledger.blocked_minutes() resolves the
# ledger, metrics_csv_writer.py appends the row -- neither of which this
# test re-runs; it seeds their OUTPUT so both read surfaces are proven
# against the same already-resolved number).
_RC_VERSION = "v1.0.0"
_WALL_MINUTES = 120
_BLOCKED_MINUTES = 45
_TASKS_TOTAL = 5
_INPUT_TOKENS = 10000
_OUTPUT_TOKENS = 5000
_CACHE_READ_TOKENS = 2000
_CACHE_WRITE_TOKENS = 500

_HISTORY_ROW = (
    f"{_RC_VERSION},{PROJECT_NAME},release,2026-01-01T00:00:00+00:00,"
    f"2026-01-01T02:00:00+00:00,{_WALL_MINUTES},SHIPPED,{_TASKS_TOTAL},1,2,1,1,0,"
    f"{_INPUT_TOKENS},{_OUTPUT_TOKENS},{_CACHE_READ_TOKENS},{_CACHE_WRITE_TOKENS},"
    f"16.7,0,0,{_BLOCKED_MINUTES}"
)

# The CLI's rendered data row is 8 whitespace-padded, non-embedded-space
# tokens: RC, Wall, Blocked, Input, Output, Cache Read, Cache Write, Tasks
# (team/scripts/dashboard/show-metrics.sh's PY_HISTORY renderer).
_DATA_ROW_RE = re.compile(
    r"^(v\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$"
)


def _seed_history_csv(project_dir: pathlib.Path) -> None:
    metrics_dir = project_dir / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    (metrics_dir / "history.csv").write_text(
        _HISTORY_HEADER + "\n" + _HISTORY_ROW + "\n", encoding="utf-8"
    )


def _cli_env(kanban_root: pathlib.Path) -> dict[str, str]:
    """Same contract as the other golden-pair tests' own ``_cli_env``."""
    env = dict(os.environ)
    env.pop("PGAI_PROJECT_NAME", None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    return env


def _parse_int(raw: str) -> int:
    return int(raw.replace(",", ""))


def _cli_rc_metrics_row(board_root: pathlib.Path) -> dict[str, int | str]:
    """Run the real show-metrics.sh script and parse its single data row.

    Returns a dict with the fields this test compares against the REST
    JSON row: version, wall_seconds, blocked_seconds, tokens_in, tokens_out,
    tasks. Units are converted to seconds to match the REST side -- the CLI
    itself renders raw minutes (fmt_wall's pass-through) and comma-formatted
    integers.
    """
    show_metrics_sh = board_root / "scripts" / "dashboard" / "show-metrics.sh"
    result = subprocess.run(
        ["bash", str(show_metrics_sh), "--project", PROJECT_NAME],
        capture_output=True,
        text=True,
        env=_cli_env(board_root),
        timeout=15,
    )
    assert result.returncode == 0, f"show-metrics.sh failed: {result.stderr!r}"

    rows = []
    for line in result.stdout.splitlines():
        m = _DATA_ROW_RE.match(line)
        if not m:
            continue
        rows.append(m.groups())

    assert len(rows) == 1, f"expected exactly one data row, got: {rows}\nfull stdout:\n{result.stdout}"
    rc, wall, blocked, input_tok, output_tok, _cache_rd, _cache_wrt, tasks = rows[0]

    return {
        "version": rc,
        "wall_seconds": _parse_int(wall) * 60,
        "blocked_seconds": _parse_int(blocked) * 60,
        "tokens_in": _parse_int(input_tok),
        "tokens_out": _parse_int(output_tok),
        "tasks": _parse_int(tasks),
    }


def _get_json(url: str) -> object:
    with urllib.request.urlopen(url, timeout=10) as resp:
        assert resp.status == 200, f"GET {url} returned HTTP {resp.status}"
        return json.loads(resp.read().decode("utf-8"))


def _rest_rc_metrics_row(port: int) -> dict[str, int | str]:
    """GET /metrics?format=json&project=... and pick the comparable subset
    of the one row's fields -- the ones the CLI's own text table also
    renders (cache_read_pct and est_cost have no CLI-rendered analogue: the
    text table shows raw Cache Read/Cache Write token counts, never a
    percentage or a cost column, so those two REST-only fields are outside
    what this golden pair can compare)."""
    rows = _get_json(f"http://127.0.0.1:{port}/metrics?format=json&project={PROJECT_NAME}")
    assert isinstance(rows, list) and len(rows) == 1, f"expected exactly one row: {rows}"
    row = rows[0]
    return {
        "version": row["version"],
        "wall_seconds": row["wall_seconds"],
        "blocked_seconds": row["blocked_seconds"],
        "tokens_in": row["tokens_in"],
        "tokens_out": row["tokens_out"],
        "tasks": row["tasks"],
    }


def test_rc_metrics_blocked_column_matches_between_cli_and_rest_with_a_blocked_task_on_the_board(
    tmp_path: pathlib.Path,
) -> None:
    """``show-metrics.sh --project NAME`` (the CLI side) and
    ``GET /metrics?format=json&project=NAME`` (the REST side) resolve to
    the identical version/wall/blocked/tokens/tasks figures for the SAME
    seeded RC row, on a board that also carries a BLOCKED-state task
    (build_synthetic_board's own shape) -- and the Blocked figure itself is
    non-zero on both surfaces, proving the REST twin actually carries the
    column rather than omitting or zero-filling it."""
    board_root = tmp_path / "board"
    project_dir = build_synthetic_board(board_root)
    _seed_history_csv(project_dir)

    cli_row = _cli_rc_metrics_row(board_root)

    server_logs = tmp_path / "server_logs"
    server_logs.mkdir(parents=True, exist_ok=True)
    with api_server_fixture(server_logs, kanban_root=board_root) as handle:
        rest_row = _rest_rc_metrics_row(handle.port)

    assert cli_row == rest_row, (
        "CLI and REST rc-metrics rows diverge over the SAME fixture.\n"
        f"CLI:  {cli_row}\n"
        f"REST: {rest_row}"
    )

    # Sanity: Blocked is populated and non-zero on both surfaces -- a
    # regression that silently dropped or zero-filled the column would
    # still pass a bare equality check if both sides degraded identically.
    assert cli_row["blocked_seconds"] == _BLOCKED_MINUTES * 60 > 0
    assert rest_row["blocked_seconds"] == _BLOCKED_MINUTES * 60 > 0
