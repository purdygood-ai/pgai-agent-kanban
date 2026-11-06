"""
test_dashboard_metrics_pane.py
==============================
The dashboard Metrics pane renderer,
``pgai_agent_kanban.lib.metrics.metrics_pane.metrics_pane_lines``, and the
``metrics_pane`` verb ``scripts/dashboard/metrics.sh`` reaches it through.

Covered behaviour:

  * a fixture projects JSON -- one project with a day roll-up and an RC
    roll-up, one with an active RC but no roll-up, one with neither --
    renders exact lines with colour off, and the same text wrapped in the
    given escapes with colour on;
  * an empty project list, and text that is not JSON, render "(no projects
    registered)" in every section; projects with no active RC render "(no
    RC active in any project)";
  * the Reliability section follows Current RC, ahead of the footer; a
    descriptor without a reliability file shows its placeholder (the
    streak lines themselves are covered by test_reliability_view.py);
  * the formatting helpers: compact token counts, wall time rounded up,
    elapsed time from ``opened_at`` (future, unparseable, offset-less), and
    the blocked figure's real 0;
  * the verb prints the lines the function yields, and refuses a wrong
    argument count with exit 2;
  * metrics.sh, run as bash on a throwaway kanban root, prints the module's
    lines.

Every tree is built under pytest's tmp_path. Subprocesses run with an
environment built from an explicit set and a throwaway HOME.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from datetime import datetime, UTC

import pytest

from pgai_agent_kanban.lib.metrics.metrics_pane import (
    fmt_blocked,
    fmt_elapsed_since,
    fmt_tokens,
    fmt_wall_time,
    metrics_pane_lines,
)
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_PP_RUN_OPS = _TEAM_DIR / "scripts" / "lib" / "pp_run_ops.sh"
_METRICS_SH = _TEAM_DIR / "scripts" / "dashboard" / "metrics.sh"
_MODULE = "pgai_agent_kanban.lib.metrics"

RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
CYAN, GREEN, YELLOW = "\033[0;36m", "\033[0;32m", "\033[0;33m"
_ESCAPES = (RESET, BOLD, DIM, CYAN, GREEN, YELLOW)

_TODAY = "2026-10-18"
_NOW = datetime(2026, 10, 18, 3, 39, 30, tzinfo=UTC)
_UPDATED = _NOW.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


@pytest.fixture
def projects_json(tmp_path: pathlib.Path) -> str:
    """Descriptors for three projects: alpha (day and RC roll-ups), beta
    (an active RC with no roll-up yet) and gamma (no RC)."""
    day_file = tmp_path / "alpha-day.json"
    day_file.write_text(
        json.dumps(
            {
                "tokens": {
                    "total": {"input": 1_200_000, "output": 340_000, "cache_read": 45_000_000, "cache_write": 900_000}
                },
                "rcs_included": ["v1.2.0", "v1.2.1"],
                "wall_time_minutes": 187.3,
                "tasks": {"total": 12345},
            }
        ),
        encoding="utf-8",
    )
    rc_file = tmp_path / "alpha-rc.json"
    rc_file.write_text(
        json.dumps(
            {
                "rc": "v1.3.0",
                "wall_time_minutes": 42,
                "blocked_minutes": 1534,
                "tasks": {"total": 7},
                "tokens": {"total": {"input": 800, "output": 50, "cache_read": 0, "cache_write": 0}},
            }
        ),
        encoding="utf-8",
    )
    return json.dumps(
        [
            {
                "name": "alpha",
                "day_file": str(day_file),
                "rc": "v1.3.0",
                "rc_file": str(rc_file),
                "opened_at": "2026-01-01T00:00:00Z",
            },
            {"name": "beta", "day_file": "", "rc": "v0.4.0", "rc_file": "", "opened_at": "2026-10-18T00:00:00+00:00"},
            {"name": "gamma", "day_file": str(tmp_path / "absent.json"), "rc": "", "rc_file": "", "opened_at": ""},
        ]
    )


_EXPECTED_PLAIN = [
    "=== Today (2026-10-18) ===",
    "",
    "  alpha",
    "    RCs shipped:  2",
    "    Wall time:    3h 08m",
    "    Tokens:       47.4M  (cache hit: 95%)",
    "    Tasks:        12,345",
    "",
    "  beta",
    "    (no activity yet today)",
    "",
    "  gamma",
    "    (no activity yet today)",
    "",
    "=== Current RC ===",
    "",
    "  alpha v1.3.0",
    "    Tasks:    7 done",
    "    Elapsed:  42m",
    "    Blocked:  1,534",
    "    Tokens:   850 (so far)",
    "",
    "  beta v0.4.0",
    "    Tasks:    (no data yet)",
    "    Elapsed:  3h 39m",
    "    Blocked:  0",
    "    Tokens:   (no data yet)",
    "",
    "=== Reliability ===",
    "",
    "  alpha",
    "    (no reliability data yet)",
    "",
    "  beta",
    "    (no reliability data yet)",
    "",
    "  gamma",
    "    (no reliability data yet)",
    "",
    f"Updated: {_UPDATED}",
    "History CSV: projects/*/metrics/history.csv",
]


def test_fixture_projects_render_exact_lines_with_colour_off(projects_json: str) -> None:
    assert list(metrics_pane_lines(False, _TODAY, projects_json, now=_NOW)) == _EXPECTED_PLAIN


def test_fixture_projects_render_in_the_given_escapes_with_colour_on(projects_json: str) -> None:
    lines = list(metrics_pane_lines(True, _TODAY, projects_json, *_ESCAPES, now=_NOW))
    assert len(lines) == len(_EXPECTED_PLAIN)
    assert lines[0] == f"{CYAN}{BOLD}=== Today (2026-10-18) ==={RESET}"
    assert lines[2] == f"  {BOLD}alpha{RESET}"
    assert lines[9] == f"    {DIM}(no activity yet today){RESET}"
    assert lines[14] == f"{CYAN}{BOLD}=== Current RC ==={RESET}"
    assert lines[16] == f"  {BOLD}alpha v1.3.0{RESET}"
    assert lines[-2:] == [
        f"{DIM}Updated: {_UPDATED}{RESET}",
        f"{DIM}History CSV: projects/*/metrics/history.csv{RESET}",
    ]
    # Every figure line is unstyled: the same text as with colour off.
    assert lines[3:8] == _EXPECTED_PLAIN[3:8]
    assert lines[17:22] == _EXPECTED_PLAIN[17:22]
    # Green and yellow are accepted but draw nothing.
    assert not any(GREEN in line or YELLOW in line for line in lines)


@pytest.mark.parametrize("projects_text", ["[]", "not json", ""])
def test_empty_or_unreadable_project_list_shows_no_projects_registered(projects_text: str) -> None:
    lines = list(metrics_pane_lines(True, _TODAY, projects_text, *_ESCAPES, now=_NOW))
    assert lines == [
        f"{CYAN}{BOLD}=== Today (2026-10-18) ==={RESET}",
        "",
        f"  {DIM}(no projects registered){RESET}",
        f"{CYAN}{BOLD}=== Current RC ==={RESET}",
        "",
        f"  {DIM}(no projects registered){RESET}",
        f"{CYAN}{BOLD}=== Reliability ==={RESET}",
        "",
        f"  {DIM}(no projects registered){RESET}",
        f"{DIM}Updated: {_UPDATED}{RESET}",
        f"{DIM}History CSV: projects/*/metrics/history.csv{RESET}",
    ]


def test_projects_without_an_active_rc_say_so() -> None:
    projects = json.dumps([{"name": "solo", "day_file": "", "rc": "", "rc_file": "", "opened_at": ""}])
    lines = list(metrics_pane_lines(False, _TODAY, projects, now=_NOW))
    start = lines.index("=== Current RC ===")
    assert lines[start : start + 3] == [
        "=== Current RC ===",
        "",
        "  (no RC active in any project)",
    ]
    assert lines[start + 3] == "=== Reliability ==="
    assert lines[-2:] == [
        f"Updated: {_UPDATED}",
        "History CSV: projects/*/metrics/history.csv",
    ]


def test_rc_rollup_without_wall_time_reads_elapsed_from_opened_at(tmp_path: pathlib.Path) -> None:
    rc_file = tmp_path / "rc.json"
    rc_file.write_text(json.dumps({"tasks": {"total": 2}, "tokens": {"total": {"input": 834_000}}}), encoding="utf-8")
    projects = json.dumps([{"name": "p", "rc": "v1.0.0", "rc_file": str(rc_file), "opened_at": "2026-10-18T03:00:00Z"}])
    lines = list(metrics_pane_lines(False, _TODAY, projects, now=_NOW))
    start = lines.index("  p v1.0.0")
    assert lines[start + 1 : start + 5] == [
        "    Tasks:    2 done",
        "    Elapsed:  39m",
        "    Blocked:  0",
        "    Tokens:   834K (so far)",
    ]


@pytest.mark.parametrize(
    "n, text", [(0, "0"), (999, "999"), (1_000, "1K"), (834_499, "834K"), (1_000_000, "1.0M"), (47_440_000, "47.4M")]
)
def test_fmt_tokens_is_compact(n: int, text: str) -> None:
    assert fmt_tokens(n) == text


@pytest.mark.parametrize(
    "minutes, text", [(None, "(unknown)"), (0, "0m"), (59.1, "1h 00m"), (59, "59m"), (125, "2h 05m")]
)
def test_fmt_wall_time_rounds_up(minutes, text: str) -> None:
    assert fmt_wall_time(minutes) == text


@pytest.mark.parametrize(
    "opened_at, text",
    [
        (None, "(unknown)"),
        ("", "(unknown)"),
        ("yesterday", "(unknown)"),
        ("2026-10-19T00:00:00Z", "0m"),  # in the future
        ("2026-10-18T02:00:00", "1h 39m"),  # no offset: read as UTC
        ("2026-10-18T05:00:00+02:00", "39m"),  # an explicit offset
    ],
)
def test_fmt_elapsed_since(opened_at, text: str) -> None:
    assert fmt_elapsed_since(opened_at, _NOW) == text


def test_fmt_blocked_is_a_real_zero_when_absent() -> None:
    assert (fmt_blocked(None), fmt_blocked(0), fmt_blocked(15), fmt_blocked(1534)) == ("0", "0", "15", "1,534")


# ---------------------------------------------------------------------------
# The verb and the pane
# ---------------------------------------------------------------------------


def _pp_run_ops(tmp_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    env = build_scrubbed_env(home_dir=tmp_path / "home", extra_path_dirs=[pathlib.Path(sys.executable).parent])
    return subprocess.run(
        ["bash", str(_PP_RUN_OPS), _MODULE, *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=60,
        check=False,
    )


def _without_clock(lines: list[str]) -> list[str]:
    """Drop the lines that read the wall clock (the footer and an elapsed
    time measured from opened_at), which may tick between two renders."""
    return [line for line in lines if "Updated:" not in line and "Elapsed:" not in line]


def test_verb_prints_the_lines_the_function_yields(tmp_path: pathlib.Path, projects_json: str) -> None:
    result = _pp_run_ops(tmp_path, "metrics_pane", "true", _TODAY, projects_json, *_ESCAPES)
    assert result.returncode == 0, _diagnostic(result)
    expected = list(metrics_pane_lines(True, _TODAY, projects_json, *_ESCAPES))
    assert _without_clock(result.stdout.splitlines()) == _without_clock(expected), _diagnostic(result)
    assert result.stdout.endswith("History CSV: projects/*/metrics/history.csv\033[0m\n")


def test_verb_refuses_a_wrong_argument_count(tmp_path: pathlib.Path) -> None:
    result = _pp_run_ops(tmp_path, "metrics_pane", "false", _TODAY, "[]")
    assert (result.returncode, result.stdout) == (2, ""), _diagnostic(result)
    assert "metrics_pane: expected" in result.stderr


def test_metrics_pane_script_prints_the_module_lines(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "root"
    (root / "tmp").mkdir(parents=True)
    (root / "projects.cfg").write_text("[project:demo]\npriority = 1\n", encoding="utf-8")
    proj = root / "projects" / "demo"
    (proj / "metrics" / "rc").mkdir(parents=True)
    (proj / "release-state.md").write_text(
        "## Active RC\nv2.0.0\n## RC Opened At\n2026-01-01T00:00:00Z\n## RC Opened By Task\nnone\n", encoding="utf-8"
    )
    rc_file = proj / "metrics" / "rc" / "v2.0.0.json"
    rc_file.write_text(
        json.dumps(
            {
                "wall_time_minutes": 61,
                "blocked_minutes": 3,
                "tasks": {"total": 4},
                "tokens": {"total": {"input": 2_500}},
            }
        ),
        encoding="utf-8",
    )
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
            "NO_COLOR": "1",
        },
    )
    result = subprocess.run(
        ["bash", str(_METRICS_SH), "--kanban-root", str(root)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, _diagnostic(result)
    today = result.stdout.splitlines()[0].removeprefix("=== Today (").removesuffix(") ===")
    descriptors = json.dumps(
        [{"name": "demo", "day_file": "", "rc": "v2.0.0", "rc_file": str(rc_file), "opened_at": "2026-01-01T00:00:00Z"}]
    )
    expected = list(metrics_pane_lines(False, today, descriptors))
    assert _without_clock(result.stdout.splitlines()) == _without_clock(expected), _diagnostic(result)
    assert "    Elapsed:  1h 01m" in result.stdout.splitlines()
