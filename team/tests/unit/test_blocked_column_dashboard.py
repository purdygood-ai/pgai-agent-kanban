"""
test_blocked_column_dashboard.py
=================================
Behavioral unit tests for the Blocked column added to the RC-metrics
history table and the current-RC block:

  - team/scripts/dashboard/show-metrics.sh  (history CSV renderer --
    the right pane of window 7 and the standalone rc-metrics CLI)
  - team/scripts/dashboard/metrics.sh       (the left pane's Current RC
    block)

Both are exercised end-to-end via subprocess against synthetic fixture
CSVs and rollup JSON files -- no live kanban root or real history.csv is
read. The `blocked_minutes` column contract (a real 0 for a clean RC, an
empty cell for a row that predates the column or whose ledger could not
be read) is established by team/tests/unit/test_metrics_csv_writer.py;
this file covers the renderer that reads it, not the writer.

All filesystem seams are constructed in tmp_path (pytest-managed).
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

_REPO_ROOT = pathlib.Path(__file__).parent.parent.parent.parent  # team/../..
_DASHBOARD_DIR = _REPO_ROOT / "team" / "scripts" / "dashboard"
_SHOW_METRICS_SH = _DASHBOARD_DIR / "show-metrics.sh"
_METRICS_SH = _DASHBOARD_DIR / "metrics.sh"

_TIMEOUT_SECONDS = 30

# The full 20-column history.csv header, matching
# team/scripts/lib/metrics_csv_writer.py's HISTORY_COLUMNS (blocked_minutes
# is the twentieth and final column).
_HISTORY_HEADER = (
    "rc,project,workflow_type,opened_at,closed_at,wall_time_minutes,outcome,"
    "tasks_total,tasks_pm,tasks_coder,tasks_writer,tasks_tester,tasks_cm,"
    "input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,"
    "cache_hit_rate_pct,bugs_filed_during,operator_waivers,blocked_minutes"
)


def _history_row(rc: str, blocked: str, wall: str = "60") -> str:
    """Build one history.csv data row with the given rc and blocked_minutes
    cell; every other field is a fixed, uninteresting placeholder."""
    return (
        f"{rc},demo-project,release,2026-01-01T00:00:00Z,2026-01-01T01:00:00Z,"
        f"{wall},SHIPPED,9,1,5,1,1,1,1000,500,0,0,0,0,0,{blocked}"
    )


def _clean_env(kanban_root: pathlib.Path) -> dict[str, str]:
    """A subprocess environment isolated from ambient project identity,
    pointed at the synthetic fixture kanban root, colorless and
    deterministic -- matches the seam already used by test_agent_metrics.py."""
    env = dict(os.environ)
    for key in (
        "PGAI_PROJECT_NAME",
        "PGAI_DEV_TREE_PATH",
        "PGAI_PROJECT_ROOT",
        "PGAI_AGENT_KANBAN_ROOT_PATH",
    ):
        env.pop(key, None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    return env


@pytest.fixture
def fixture_kanban_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """A minimal kanban root with one registered project and no
    history.csv/rc rollups -- individual tests populate what they need."""
    root = tmp_path / "kanban"
    root.mkdir()
    (root / "projects.cfg").write_text(
        "[project:demo-project]\npriority=1\nenabled=true\n",
        encoding="utf-8",
    )
    return root


def _run_show_metrics(history_csv: pathlib.Path, kanban_root: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "bash", str(_SHOW_METRICS_SH),
            "--history-csv", str(history_csv),
            "--project", "demo-project",
            "--no-color",
        ],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_SECONDS,
        env=_clean_env(kanban_root),
    )


# ---------------------------------------------------------------------------
# show-metrics.sh history table -- header and column position
# ---------------------------------------------------------------------------


class TestHistoryTableBlockedColumn:
    def test_header_shows_blocked_immediately_right_of_wall(self, fixture_kanban_root, tmp_path):
        csv_path = tmp_path / "history.csv"
        csv_path.write_text(
            _HISTORY_HEADER + "\n" + _history_row("v2.24.0", "0") + "\n",
            encoding="utf-8",
        )
        result = _run_show_metrics(csv_path, fixture_kanban_root)
        assert result.returncode == 0, result.stderr
        header_line = next(l for l in result.stdout.splitlines() if l.lstrip().startswith("RC"))
        columns = header_line.split()
        assert columns[:3] == ["RC", "Wall", "Blocked"]
        # No other column renamed or reordered.
        assert columns[3:] == ["Input", "Output", "Cache", "Read", "Cache", "Write", "Tasks"]

    def test_fifty_blocked_minutes_renders_in_its_row(self, fixture_kanban_root, tmp_path):
        csv_path = tmp_path / "history.csv"
        csv_path.write_text(
            _HISTORY_HEADER + "\n" + _history_row("v2.24.0", "50") + "\n",
            encoding="utf-8",
        )
        result = _run_show_metrics(csv_path, fixture_kanban_root)
        assert result.returncode == 0, result.stderr
        row_line = next(l for l in result.stdout.splitlines() if l.startswith("v2.24.0"))
        assert " 50 " in f" {row_line} " or row_line.split()[2] == "50"

    def test_clean_rc_renders_zero_not_blank_or_dash(self, fixture_kanban_root, tmp_path):
        csv_path = tmp_path / "history.csv"
        csv_path.write_text(
            _HISTORY_HEADER + "\n" + _history_row("v2.24.0", "0") + "\n",
            encoding="utf-8",
        )
        result = _run_show_metrics(csv_path, fixture_kanban_root)
        assert result.returncode == 0, result.stderr
        row_line = next(l for l in result.stdout.splitlines() if l.startswith("v2.24.0"))
        blocked_cell = row_line.split()[2]
        assert blocked_cell == "0"
        assert blocked_cell not in ("", "--")

    def test_pre_era_row_renders_single_dash_and_one_footer_line(self, fixture_kanban_root, tmp_path):
        csv_path = tmp_path / "history.csv"
        csv_path.write_text(
            _HISTORY_HEADER + "\n" + _history_row("v2.23.9", "") + "\n",
            encoding="utf-8",
        )
        result = _run_show_metrics(csv_path, fixture_kanban_root)
        assert result.returncode == 0, result.stderr
        row_line = next(l for l in result.stdout.splitlines() if l.startswith("v2.23.9"))
        blocked_cell = row_line.split()[2]
        assert blocked_cell == "-"
        footer_lines = [l for l in result.stdout.splitlines() if "Blocked tracked from v2.24.0" in l]
        assert len(footer_lines) == 1

    def test_no_pre_era_row_prints_no_footer_line(self, fixture_kanban_root, tmp_path):
        csv_path = tmp_path / "history.csv"
        csv_path.write_text(
            _HISTORY_HEADER + "\n"
            + _history_row("v2.24.0", "0") + "\n"
            + _history_row("v2.24.1", "50") + "\n",
            encoding="utf-8",
        )
        result = _run_show_metrics(csv_path, fixture_kanban_root)
        assert result.returncode == 0, result.stderr
        assert "Blocked tracked from v2.24.0" not in result.stdout

    def test_six_figure_blocked_value_comma_formatted(self, fixture_kanban_root, tmp_path):
        csv_path = tmp_path / "history.csv"
        csv_path.write_text(
            _HISTORY_HEADER + "\n" + _history_row("v2.24.0", "123456") + "\n",
            encoding="utf-8",
        )
        result = _run_show_metrics(csv_path, fixture_kanban_root)
        assert result.returncode == 0, result.stderr
        assert "123,456" in result.stdout

    def test_missing_blocked_column_entirely_renders_dash_and_footer(self, fixture_kanban_root, tmp_path):
        """A CSV predating the blocked_minutes column (no such header at
        all) degrades exactly like an explicit empty cell -- a dash, and
        the footer, never a crash."""
        csv_path = tmp_path / "history.csv"
        legacy_header = _HISTORY_HEADER.rsplit(",blocked_minutes", 1)[0]
        legacy_row = _history_row("v2.23.9", "").rsplit(",", 1)[0]
        csv_path.write_text(legacy_header + "\n" + legacy_row + "\n", encoding="utf-8")
        result = _run_show_metrics(csv_path, fixture_kanban_root)
        assert result.returncode == 0, result.stderr
        row_line = next(l for l in result.stdout.splitlines() if l.startswith("v2.23.9"))
        assert row_line.split()[2] == "-"
        assert "Blocked tracked from v2.24.0" in result.stdout


# ---------------------------------------------------------------------------
# metrics.sh -- Current RC pane
# ---------------------------------------------------------------------------


class TestCurrentRcBlockedFigure:
    def _write_open_rc(self, kanban_root: pathlib.Path, rc_json: dict | None) -> None:
        proj_root = kanban_root / "projects" / "demo-project"
        (proj_root / "metrics" / "rc").mkdir(parents=True, exist_ok=True)
        (proj_root / "metrics" / "day").mkdir(parents=True, exist_ok=True)
        (proj_root / "release-state.md").write_text(
            "## Active RC\nv2.24.0\n"
            "## Last Released\nv2.23.9\n"
            "## RC Opened At\n2026-01-01T00:00:00Z\n"
            "## RC Opened By Task\nnone\n",
            encoding="utf-8",
        )
        if rc_json is not None:
            (proj_root / "metrics" / "rc" / "v2.24.0.json").write_text(
                json.dumps(rc_json), encoding="utf-8"
            )

    def _run_metrics(self, kanban_root: pathlib.Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(_METRICS_SH), "--kanban-root", str(kanban_root), "--no-color"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            env=_clean_env(kanban_root),
        )

    def test_prints_blocked_figure_beside_elapsed(self, fixture_kanban_root):
        self._write_open_rc(
            fixture_kanban_root,
            {
                "rc": "v2.24.0",
                "wall_time_minutes": 42,
                "blocked_minutes": 15,
                "tasks": {"total": 3},
                "tokens": {"total": {"input": 100, "output": 50, "cache_read": 0, "cache_write": 0}},
            },
        )
        result = self._run_metrics(fixture_kanban_root)
        assert result.returncode == 0, result.stderr
        lines = result.stdout.splitlines()
        elapsed_idx = next(i for i, l in enumerate(lines) if l.strip().startswith("Elapsed:"))
        assert lines[elapsed_idx + 1].strip() == "Blocked:  15"

    def test_prints_zero_when_rollup_has_no_ledger_reading(self, fixture_kanban_root):
        """A rollup written before blocked_minutes existed (key absent) or
        with an explicit None (ledger present but unreadable) both print a
        real 0 in this pane -- never blank."""
        self._write_open_rc(
            fixture_kanban_root,
            {
                "rc": "v2.24.0",
                "wall_time_minutes": 42,
                "tasks": {"total": 3},
                "tokens": {"total": {"input": 100, "output": 50, "cache_read": 0, "cache_write": 0}},
            },
        )
        result = self._run_metrics(fixture_kanban_root)
        assert result.returncode == 0, result.stderr
        assert "Blocked:  0" in result.stdout

    def test_prints_zero_when_no_rc_rollup_file_exists(self, fixture_kanban_root):
        """The RC just opened and no rollup file has been written yet --
        still a real 0, never blank."""
        self._write_open_rc(fixture_kanban_root, rc_json=None)
        result = self._run_metrics(fixture_kanban_root)
        assert result.returncode == 0, result.stderr
        assert "Blocked:  0" in result.stdout
