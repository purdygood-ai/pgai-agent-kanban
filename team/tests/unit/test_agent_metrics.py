"""
test_agent_metrics.py
======================
Behavioral unit tests for:
  - team/scripts/lib/number_format.py   (the shared thousands-separated
    integer and one-decimal formatters every dashboard metrics renderer
    imports) and the shared one-decimal rounding rule
    (pgai_agent_kanban.lib.agent_metrics.round_one_decimal)
  - team/scripts/lib/agent_metrics.py    (the per-agent, per-project
    averaging arithmetic and the standalone renderer's supporting helpers)
  - pgai_agent_kanban.lib.agent_metrics  (shipped-only span selection --
    newest N, all shipped, at-or-above a version -- the excluded count,
    and per-agent totals; seeded through tests/fixtures/rc_rollup_history.py)
  - team/scripts/dashboard/agent-metrics.sh (the standalone CLI, exercised
    end-to-end via subprocess against a synthetic fixture kanban root:
    one-decimal averages, total columns, the shipped/excluded footer, and
    the --window/--last/--all/--since span flags and their usage errors)

Also covers the "converged onto the shared formatter without changing
output" acceptance criterion for the two pre-existing renderers
(show-metrics.sh, metrics.sh) by asserting their comma-formatted output
against a small fixture is unchanged from their pre-conversion behavior.

All filesystem seams are constructed in tmp_path (pytest-managed). No real
kanban root, git repo, or live disk state is accessed.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

# ---------------------------------------------------------------------------
# Ensure number_format and agent_metrics are importable from the dev tree,
# matching the sys.path seam already used by test_metrics_aggregator.py and
# test_metrics_csv_writer.py.
# ---------------------------------------------------------------------------
_REPO_ROOT = pathlib.Path(__file__).parent.parent.parent.parent  # team/../..
_LIB_PATH = _REPO_ROOT / "team" / "scripts" / "lib"
if str(_LIB_PATH) not in sys.path:
    sys.path.insert(0, str(_LIB_PATH))

import agent_metrics as am  # noqa: E402
import number_format as nf  # noqa: E402
from pgai_agent_kanban.lib import agent_metrics as core  # noqa: E402
from tests.fixtures.rc_rollup_history import (  # noqa: E402
    AgentLoad,
    RcSeed,
    build_rc_history,
    write_rollups,
)

_DASHBOARD_DIR = _REPO_ROOT / "team" / "scripts" / "dashboard"
_AGENT_METRICS_SH = _DASHBOARD_DIR / "agent-metrics.sh"
_SHOW_METRICS_SH = _DASHBOARD_DIR / "show-metrics.sh"
_METRICS_SH = _DASHBOARD_DIR / "metrics.sh"

_TIMEOUT_SECONDS = 30


# ---------------------------------------------------------------------------
# number_format.format_int
# ---------------------------------------------------------------------------


class TestFormatInt:
    def test_thousands_separated_integer(self):
        assert nf.format_int(5644879) == "5,644,879"

    def test_small_integer_no_separator_needed(self):
        assert nf.format_int(42) == "42"

    def test_none_renders_placeholder(self):
        assert nf.format_int(None) == "--"

    def test_empty_string_renders_placeholder(self):
        assert nf.format_int("") == "--"

    def test_whitespace_only_string_renders_placeholder(self):
        assert nf.format_int("   ") == "--"

    def test_numeric_string_is_formatted(self):
        assert nf.format_int("123456") == "123,456"

    def test_float_string_is_truncated_and_formatted(self):
        # history.csv round-trips ints with a trailing ".0" in some rows.
        assert nf.format_int("1234.0") == "1,234"

    def test_non_numeric_string_falls_back_to_raw_value(self):
        # Matches the pre-existing PY_HISTORY fmt_num() behavior this
        # module replaces: an unparseable value is returned unchanged,
        # not masked behind the placeholder.
        assert nf.format_int("N/A") == "N/A"

    def test_zero_is_not_the_placeholder(self):
        assert nf.format_int(0) == "0"

    def test_cli_formats_single_argument(self):
        result = subprocess.run(
            [sys.executable, str(_LIB_PATH / "number_format.py"), "5644879"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "5,644,879"

    def test_cli_usage_error_on_wrong_arg_count(self):
        result = subprocess.run(
            [sys.executable, str(_LIB_PATH / "number_format.py")],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
        assert result.returncode == 1


# ---------------------------------------------------------------------------
# number_format.format_one_decimal and the shared one-decimal rounding rule
# ---------------------------------------------------------------------------


class TestFormatOneDecimal:
    @pytest.mark.parametrize(
        "value, expected",
        [
            (2, "2.0"),
            (2.0, "2.0"),
            (4.7, "4.7"),
            (41000, "41,000.0"),
            (41000.0, "41,000.0"),
            (1234567.8, "1,234,567.8"),
            (0, "0.0"),
            ("1250", "1,250.0"),
        ],
    )
    def test_exactly_one_decimal_with_thousands_separators(self, value, expected):
        assert nf.format_one_decimal(value) == expected

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_absent_value_renders_the_shared_placeholder(self, value):
        assert nf.format_one_decimal(value) == "--"
        assert nf.format_one_decimal(value) == nf.format_int(value)

    def test_non_numeric_string_falls_back_to_raw_value(self):
        assert nf.format_one_decimal("n/a") == "n/a"

    def test_cli_one_decimal_flag(self):
        result = subprocess.run(
            [sys.executable, str(_LIB_PATH / "number_format.py"), "--one-decimal", "41000"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
        assert result.returncode == 0
        assert result.stdout == "41,000.0\n"


class TestRoundOneDecimal:
    @pytest.mark.parametrize(
        "value, expected",
        [
            (2.25, 2.3),   # half rounds away from zero, not to even
            (2.35, 2.4),
            (-2.25, -2.3),
            (4.66666, 4.7),
            (4.64, 4.6),
            (2, 2.0),
            (41000, 41000.0),
            (0.05, 0.1),
        ],
    )
    def test_rounds_half_away_from_zero_on_the_decimal_digits(self, value, expected):
        assert core.round_one_decimal(value) == expected

    def test_absent_average_passes_through(self):
        assert core.round_one_decimal(None) is None

    def test_rule_differs_from_builtin_half_to_even_where_it_matters(self):
        # The built-in rounds 2.25 to 2.2; the shared rule prints what a
        # person rounding by hand writes.
        assert "{:.1f}".format(2.25) == "2.2"
        assert nf.format_one_decimal(core.round_one_decimal(2.25)) == "2.3"

    def test_cli_renderer_uses_the_shared_rule(self):
        assert am.round_one_decimal is core.round_one_decimal


# ---------------------------------------------------------------------------
# agent_metrics.total_tokens
# ---------------------------------------------------------------------------


class TestTotalTokens:
    def test_sums_all_four_fields(self):
        entry = {"input": 100, "output": 50, "cache_read": 25, "cache_write": 10}
        assert am.total_tokens(entry) == 185

    def test_none_entry_is_zero(self):
        assert am.total_tokens(None) == 0

    def test_missing_fields_default_to_zero(self):
        assert am.total_tokens({"input": 10}) == 10


# ---------------------------------------------------------------------------
# agent_metrics.compute_agent_averages -- the averaging arithmetic
# ---------------------------------------------------------------------------


def _rc(tasks_by_agent, tokens_by_agent, wall_by_agent=None):
    """Build a synthetic RC rollup dict.

    `wall_by_agent` is the per-RC "wall"."by_agent" block optionally
    present in metrics_aggregator.py's aggregate_rc() output
    (`{"<agent>": {"seconds": <num>, "task_count": <int>}}`). Omitting it
    (the default) produces a rollup shaped like one written BEFORE that
    field existed -- the absent-data / legacy-rollup case avg_wall_minutes
    must render as `None` for, never an exception. The RC-level
    "wall_time_minutes" field is deliberately never written here:
    compute_agent_averages() must not read it (that RC-shared figure is
    exactly the join bug this module fixes).
    """
    rc: dict = {
        "tasks": {"by_agent": tasks_by_agent},
        "tokens": {"by_agent": tokens_by_agent},
    }
    if wall_by_agent is not None:
        rc["wall"] = {"by_agent": wall_by_agent}
    return rc


class TestComputeAgentAverages:
    def test_averaging_arithmetic_across_two_rcs(self):
        rollups = [
            _rc(
                {"coder": 2},
                {"coder": {"input": 1000, "output": 500, "cache_read": 0, "cache_write": 0}},
                wall_by_agent={"coder": {"seconds": 3000, "task_count": 2}},
            ),
            _rc(
                {"coder": 4},
                {"coder": {"input": 3000, "output": 500, "cache_read": 0, "cache_write": 0}},
                wall_by_agent={"coder": {"seconds": 900, "task_count": 4}},
            ),
        ]
        result = am.compute_agent_averages(rollups)
        assert result["coder"]["rc_count"] == 2
        assert result["coder"]["avg_tasks"] == pytest.approx(3.0)  # (2+4)/2 -- per RC
        assert result["coder"]["avg_tokens"] == pytest.approx(2500.0)  # (1500+3500)/2 -- per RC
        # avg_wall_minutes pools seconds and task_count across BOTH RCs
        # first, then divides -- (3000+900) seconds / (2+4) tasks / 60 --
        # never an average of each RC's own per-RC average.
        assert result["coder"]["avg_wall_minutes"] == pytest.approx((3000 + 900) / (2 + 4) / 60)

    def test_zero_invocations_agent_is_absent_never_a_zero_or_division_error(self):
        # "writer" never appears in any RC's tasks.by_agent in this window.
        rollups = [
            _rc(
                {"coder": 1},
                {"coder": {"input": 100, "output": 0, "cache_read": 0, "cache_write": 0}},
                wall_by_agent={"coder": {"seconds": 1800, "task_count": 1}},
            ),
        ]
        result = am.compute_agent_averages(rollups)
        assert "writer" not in result
        assert "coder" in result

    def test_agent_with_explicit_zero_task_count_does_not_count_as_an_invocation(self):
        rollups = [
            _rc({"coder": 0}, {}),
        ]
        result = am.compute_agent_averages(rollups)
        assert result == {}

    def test_wall_entry_for_a_non_invoked_agent_never_manufactures_a_row(self):
        # "coder" has a wall.by_agent entry but zero tasks in tasks.by_agent
        # in this RC (and no other RC in the window) -- the result is built
        # from the invocation set (tasks.by_agent), not from wall data, so
        # coder must stay absent rather than appear with only a wall figure.
        rollups = [
            _rc({"coder": 0}, {}, wall_by_agent={"coder": {"seconds": 1800, "task_count": 1}}),
        ]
        result = am.compute_agent_averages(rollups)
        assert result == {}

    def test_empty_window_produces_empty_result_without_error(self):
        assert am.compute_agent_averages([]) == {}

    def test_rc_lacking_a_wall_entry_for_the_agent_excludes_that_rc_from_wall_average_only(self):
        # coder is invoked in both RCs, but only the second carries a
        # wall.by_agent entry for coder. avg_wall must be derived from just
        # that one RC's seconds/task_count while avg_tokens/avg_tasks still
        # average over both invocation RCs.
        rollups = [
            _rc(
                {"coder": 1},
                {"coder": {"input": 100, "output": 0, "cache_read": 0, "cache_write": 0}},
                # no wall_by_agent -- this RC predates per-task capture
            ),
            _rc(
                {"coder": 3},
                {"coder": {"input": 300, "output": 0, "cache_read": 0, "cache_write": 0}},
                wall_by_agent={"coder": {"seconds": 2400, "task_count": 3}},
            ),
        ]
        result = am.compute_agent_averages(rollups)
        assert result["coder"]["rc_count"] == 2
        assert result["coder"]["avg_tasks"] == pytest.approx(2.0)  # (1+3)/2
        assert result["coder"]["avg_tokens"] == pytest.approx(200.0)  # (100+300)/2
        assert result["coder"]["avg_wall_minutes"] == pytest.approx(2400 / 3 / 60)  # only the second RC

    def test_all_invocation_rcs_missing_wall_data_renders_none(self):
        # Both RCs predate the "wall"."by_agent" field entirely -- the
        # legacy-rollup / absent-data contract: the wall column must
        # render the placeholder, never a wrong number and never an
        # exception.
        rollups = [
            _rc({"coder": 1}, {"coder": {"input": 100, "output": 0, "cache_read": 0, "cache_write": 0}}),
            _rc({"coder": 2}, {"coder": {"input": 200, "output": 0, "cache_read": 0, "cache_write": 0}}),
        ]
        result = am.compute_agent_averages(rollups)
        assert result["coder"]["avg_wall_minutes"] is None
        assert result["coder"]["avg_tasks"] == pytest.approx(1.5)

    def test_wall_entry_with_non_numeric_seconds_is_ignored(self):
        rollups = [
            _rc(
                {"coder": 2},
                {"coder": {"input": 100, "output": 0, "cache_read": 0, "cache_write": 0}},
                wall_by_agent={"coder": {"seconds": "not-a-number", "task_count": 2}},
            ),
        ]
        result = am.compute_agent_averages(rollups)
        assert result["coder"]["avg_wall_minutes"] is None

    def test_wall_entry_with_zero_task_count_is_ignored_not_a_division_by_zero(self):
        rollups = [
            _rc(
                {"coder": 2},
                {"coder": {"input": 100, "output": 0, "cache_read": 0, "cache_write": 0}},
                wall_by_agent={"coder": {"seconds": 500, "task_count": 0}},
            ),
        ]
        result = am.compute_agent_averages(rollups)
        assert result["coder"]["avg_wall_minutes"] is None

    def test_wall_entry_with_bool_task_count_is_ignored(self):
        # bool is an int subclass in Python; a stray `True`/`False` must
        # never silently count as 1/0 task -- mirrors metrics_aggregator.py's
        # own _extract_elapsed_seconds() guard against the same class of bug.
        rollups = [
            _rc(
                {"coder": 1},
                {"coder": {"input": 100, "output": 0, "cache_read": 0, "cache_write": 0}},
                wall_by_agent={"coder": {"seconds": 60, "task_count": True}},
            ),
        ]
        result = am.compute_agent_averages(rollups)
        assert result["coder"]["avg_wall_minutes"] is None


# ---------------------------------------------------------------------------
# agent_metrics.load_rc_window
# ---------------------------------------------------------------------------


class TestLoadRcWindow:
    def _write_rc(self, rc_dir: pathlib.Path, version: str, content: dict | None = None) -> None:
        rc_dir.mkdir(parents=True, exist_ok=True)
        payload = (
            content
            if content is not None
            else {"rc": version, "outcome": "shipped", "tasks": {"by_agent": {}}, "tokens": {"by_agent": {}}}
        )
        (rc_dir / f"{version}.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_missing_directory_returns_empty_list(self, tmp_path):
        assert am.load_rc_window(tmp_path / "nonexistent", window=10) == []

    def test_window_limits_to_newest_n_by_version(self, tmp_path):
        rc_dir = tmp_path / "rc"
        for v in ["v0.1.0", "v0.2.0", "v0.3.0", "v0.4.0"]:
            self._write_rc(rc_dir, v)
        rollups = am.load_rc_window(rc_dir, window=2)
        versions = sorted(r["rc"] for r in rollups)
        assert versions == ["v0.3.0", "v0.4.0"]

    def test_malformed_file_is_skipped_and_does_not_consume_a_window_slot(self, tmp_path, capsys):
        rc_dir = tmp_path / "rc"
        rc_dir.mkdir(parents=True)
        (rc_dir / "v0.1.0.json").write_text("{not valid json", encoding="utf-8")
        self._write_rc(rc_dir, "v0.2.0")
        rollups = am.load_rc_window(rc_dir, window=5)
        assert len(rollups) == 1
        assert rollups[0]["rc"] == "v0.2.0"
        assert "malformed" in capsys.readouterr().err

    def test_non_object_json_is_skipped(self, tmp_path):
        rc_dir = tmp_path / "rc"
        rc_dir.mkdir(parents=True)
        (rc_dir / "v0.1.0.json").write_text("[1, 2, 3]", encoding="utf-8")
        assert am.load_rc_window(rc_dir, window=5) == []


# ---------------------------------------------------------------------------
# Shipped-only span selection and per-agent totals
# (pgai_agent_kanban.lib.agent_metrics, seeded through the shared
# tests/fixtures/rc_rollup_history.py builder)
# ---------------------------------------------------------------------------


def _cm_only(tasks: int) -> dict:
    return {"cm": AgentLoad(tasks=tasks, input=100 * tasks, output=10 * tasks)}


def _five_shipped_one_cancelled(rc_dir: pathlib.Path) -> list[RcSeed]:
    """Five shipped rollups with cm: 2 tasks and one cancelled rollup with
    cm: 7 tasks, the cancelled one in the middle of the newest-10 range."""
    seeds = [
        RcSeed("v1.0.0", "shipped", _cm_only(2)),
        RcSeed("v1.1.0", "shipped", _cm_only(2)),
        RcSeed("v1.2.0", "cancelled", _cm_only(7)),
        RcSeed("v1.3.0", "shipped", _cm_only(2)),
        RcSeed("v1.4.0", "shipped", _cm_only(2)),
        RcSeed("v1.5.0", "shipped", _cm_only(2)),
    ]
    write_rollups(rc_dir, seeds)
    return seeds


class TestShippedSpanSelection:
    def test_cancelled_rollup_in_range_is_excluded_from_every_cm_value_and_counted(self, tmp_path):
        rc_dir = tmp_path / "rc"
        _five_shipped_one_cancelled(rc_dir)

        selection = core.select_rc_span(rc_dir, core.RcSpan.last(10))
        cm = core.compute_agent_averages(selection.rollups)["cm"]

        assert selection.excluded_count == 1
        assert selection.shipped_count == 5
        assert cm["avg_tasks"] == 2.0
        assert cm["rc_count"] == 5
        assert cm["total_tasks"] == 10

    def test_removing_the_cancelled_rollup_changes_no_agent_value(self, tmp_path):
        rc_dir = tmp_path / "rc"
        _five_shipped_one_cancelled(rc_dir)
        before = core.select_rc_span(rc_dir, core.RcSpan.last(10))

        (rc_dir / "v1.2.0.json").unlink()
        after = core.select_rc_span(rc_dir, core.RcSpan.last(10))

        assert core.compute_agent_averages(after.rollups) == core.compute_agent_averages(before.rollups)
        assert after.excluded_count == 0
        assert after.shipped_count == 5

    def test_non_shipped_rollups_consume_no_window_slot(self, tmp_path):
        rc_dir = tmp_path / "rc"
        # 14 rollups oldest-first; the two cancelled ones are among the
        # newest by version (indices 11 and 13).
        seeds = build_rc_history(rc_dir, 14, cancelled={11, 13})

        selection = core.select_rc_span(rc_dir, core.RcSpan.last(10))

        shipped_newest_first = [s.version for s in reversed(seeds) if s.outcome == "shipped"]
        assert [r["rc"] for r in selection.rollups] == shipped_newest_first[:10]
        assert all(r["outcome"] == "shipped" for r in selection.rollups)
        assert selection.shipped_count == 10
        assert selection.excluded_count == 2

    def test_non_shipped_rollup_past_the_filled_window_is_not_counted(self, tmp_path):
        rc_dir = tmp_path / "rc"
        # oldest v0.9.0 is cancelled; the newest 3 shipped fill the window
        # before the scan reaches it.
        build_rc_history(rc_dir, 5, cancelled={0})
        selection = core.select_rc_span(rc_dir, core.RcSpan.last(3))
        assert selection.shipped_count == 3
        assert selection.excluded_count == 0

    @pytest.mark.parametrize(
        "outcome",
        ["cancelled", "failed", "in_progress", "unknown", "UNKNOWN", None, 1, ""],
    )
    def test_every_outcome_other_than_shipped_is_excluded(self, tmp_path, outcome):
        rc_dir = tmp_path / "rc"
        build_rc_history(rc_dir, 3, outcomes={1: outcome})
        selection = core.select_rc_span(rc_dir, core.RcSpan.all_shipped())
        assert selection.shipped_count == 2
        assert selection.excluded_count == 1
        assert "v0.9.1" not in [r["rc"] for r in selection.rollups]

    def test_shipped_outcome_tolerates_case_and_surrounding_whitespace(self):
        assert core.is_shipped({"outcome": " SHIPPED\n"})
        assert core.is_shipped({"outcome": "shipped"})
        assert not core.is_shipped({})

    def test_malformed_rollup_is_skipped_with_a_warning_and_is_not_counted_excluded(self, tmp_path, capsys):
        rc_dir = tmp_path / "rc"
        build_rc_history(rc_dir, 3)
        (rc_dir / "v9.0.0.json").write_text("{not valid json", encoding="utf-8")
        (rc_dir / "v9.0.1.json").write_text("[1, 2]", encoding="utf-8")

        selection = core.select_rc_span(rc_dir, core.RcSpan.last(10))

        assert selection.shipped_count == 3
        assert selection.excluded_count == 0
        err = capsys.readouterr().err
        assert "v9.0.0.json" in err and "v9.0.1.json" in err

    def test_missing_directory_is_an_empty_selection(self, tmp_path):
        selection = core.select_rc_span(tmp_path / "absent", core.RcSpan.all_shipped())
        assert selection.rollups == []
        assert selection.excluded_count == 0

    def test_load_rc_window_returns_only_the_newest_shipped_rollups(self, tmp_path):
        rc_dir = tmp_path / "rc"
        build_rc_history(rc_dir, 6, cancelled={5})
        rollups = core.load_rc_window(rc_dir, window=2)
        assert [r["rc"] for r in rollups] == ["v0.11.0", "v0.10.1"]

    def test_selection_reports_the_span_it_was_given(self, tmp_path):
        span = core.RcSpan.since_version("v0.10.0")
        assert core.select_rc_span(tmp_path, span).span == span


class TestFullHistorySpans:
    """A 40-rollup history, v0.9.0 .. v0.28.1, with cancelled rollups on
    both sides of v0.20.0."""

    _CANCELLED = {1, 15, 22, 38}

    @pytest.fixture
    def history(self, tmp_path):
        rc_dir = tmp_path / "rc"
        seeds = build_rc_history(rc_dir, 40, cancelled=self._CANCELLED)
        return rc_dir, seeds

    def test_all_shipped_span_returns_every_shipped_rollup(self, history):
        rc_dir, seeds = history
        selection = core.select_rc_span(rc_dir, core.RcSpan.all_shipped())
        expected = {s.version for s in seeds if s.outcome == "shipped"}
        assert {r["rc"] for r in selection.rollups} == expected
        assert selection.shipped_count == 36
        assert selection.excluded_count == 4

    def test_since_span_uses_semantic_version_order_not_string_order(self, history):
        rc_dir, seeds = history
        selection = core.select_rc_span(rc_dir, core.RcSpan.since_version("v0.20.0"))

        selected = {r["rc"] for r in selection.rollups}
        # Hand-listed from the series: v0.20.0 is index 22 (cancelled), so
        # the shipped versions at or above it are indices 23..39 minus 38.
        expected = {seeds[i].version for i in range(23, 40) if i != 38}
        assert selected == expected
        assert "v0.9.0" not in selected  # "v0.9.0" > "v0.20.0" as strings
        assert "v0.20.1" in selected
        assert selection.excluded_count == 2  # v0.20.0 and v0.28.0

    def test_single_digit_minor_bound_still_includes_every_double_digit_minor(self, history):
        rc_dir, seeds = history
        # "v0.28.1" < "v0.9.1" as strings; by semver every v0.1x/v0.2x
        # rollup is above the bound.
        selection = core.select_rc_span(rc_dir, core.RcSpan.since_version("v0.9.1"))
        expected = {s.version for s in seeds[1:] if s.outcome == "shipped"}
        assert {r["rc"] for r in selection.rollups} == expected
        assert selection.excluded_count == len(self._CANCELLED)  # v0.9.1 itself is cancelled

    def test_since_bound_accepts_a_version_without_the_v_prefix(self, history):
        rc_dir, _ = history
        with_prefix = core.select_rc_span(rc_dir, core.RcSpan.since_version("v0.20.0"))
        without_prefix = core.select_rc_span(rc_dir, core.RcSpan.since_version("0.20.0"))
        assert with_prefix.rollups == without_prefix.rollups

    def test_totals_equal_hand_sums_over_each_agents_invocation_rcs(self, history):
        rc_dir, seeds = history
        selection = core.select_rc_span(rc_dir, core.RcSpan.all_shipped())
        result = core.compute_agent_averages(selection.rollups)

        shipped = [s for s in seeds if s.outcome == "shipped"]
        for agent in ("cm", "coder", "writer"):
            loads = [s.agents[agent] for s in shipped if agent in s.agents and s.agents[agent].tasks > 0]
            hand_tokens = sum(ld.input + ld.output + ld.cache_read + ld.cache_write for ld in loads)
            hand_tasks = sum(ld.tasks for ld in loads)
            row = result[agent]
            assert row["rc_count"] == len(loads), agent
            assert row["total_tokens"] == hand_tokens, agent
            assert row["total_tasks"] == hand_tasks, agent
            assert row["avg_tokens"] * row["rc_count"] == pytest.approx(row["total_tokens"]), agent
            assert row["avg_tasks"] * row["rc_count"] == pytest.approx(row["total_tasks"]), agent

    def test_averages_are_unrounded_floats(self, history):
        rc_dir, _ = history
        selection = core.select_rc_span(rc_dir, core.RcSpan.all_shipped())
        coder = core.compute_agent_averages(selection.rollups)["coder"]
        assert isinstance(coder["avg_tasks"], float)
        assert coder["avg_tasks"] != round(coder["avg_tasks"])


class TestSpanBoundValidation:
    @pytest.mark.parametrize("bad", ["notaversion", "v1.2", "v1.2.3.4", "", "vX.Y.Z", "v1.-2.0", None])
    def test_malformed_since_version_is_a_usage_error_not_an_empty_span(self, bad):
        with pytest.raises(ValueError, match="since"):
            core.RcSpan.since_version(bad)

    @pytest.mark.parametrize("bad", [0, -3, True, 2.5, "10"])
    def test_last_count_must_be_a_positive_integer(self, bad):
        with pytest.raises(ValueError, match="last"):
            core.RcSpan.last(bad)

    def test_unknown_span_kind_is_rejected(self):
        with pytest.raises(ValueError, match="unknown span kind"):
            core.RcSpan(kind="newest")


# ---------------------------------------------------------------------------
# End-to-end: team/scripts/dashboard/agent-metrics.sh via subprocess
# ---------------------------------------------------------------------------


def _write_rc_json(path: pathlib.Path, payload: dict) -> None:
    """Write a shipped RC rollup (outcome defaults to "shipped"; only
    shipped rollups enter the averaging span)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"outcome": "shipped", **payload}), encoding="utf-8")


def _clean_env(kanban_root: pathlib.Path) -> dict[str, str]:
    """A subprocess environment isolated from ambient project identity,
    pointed at the synthetic fixture kanban root, colorless and deterministic."""
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
    """A minimal kanban root with two registered projects:

      demo-project  -- two RC rollups: v0.1.0 carries a "wall"."by_agent"
                        block (the optional per-agent wall-time field) for both agents,
                        with DIFFERENT per-task figures for coder and
                        writer -- proving the two don't coincidentally
                        share a value the way the RC-level wall_time_minutes
                        join bug used to produce. v0.2.0 has no "wall" key
                        at all (a legacy rollup predating that field),
                        exercising the absent-data placeholder contract,
                        and also contributes a second invocation RC to
                        coder's avg_tokens/avg_tasks (but not avg_wall).
                        "writer" only appears in v0.1.0, exercising the
                        zero-invocation-elsewhere case.
      empty-project -- registered, but has no metrics/rc/ directory at all.
    """
    root = tmp_path / "kanban"
    root.mkdir()
    (root / "projects.cfg").write_text(
        "[project:demo-project]\npriority=1\nenabled=true\n\n"
        "[project:empty-project]\npriority=2\nenabled=true\n",
        encoding="utf-8",
    )

    rc_dir = root / "projects" / "demo-project" / "metrics" / "rc"
    _write_rc_json(
        rc_dir / "v0.1.0.json",
        {
            "rc": "v0.1.0",
            "wall_time_minutes": 30,
            "tasks": {"total": 3, "by_agent": {"coder": 2, "writer": 1}},
            "tokens": {
                "by_agent": {
                    "coder": {"input": 1000, "output": 500, "cache_read": 0, "cache_write": 0},
                    "writer": {"input": 2000, "output": 1000, "cache_read": 0, "cache_write": 0},
                }
            },
            "wall": {
                "by_agent": {
                    "coder": {"seconds": 1200, "task_count": 2},   # 10 min/task
                    "writer": {"seconds": 2700, "task_count": 1},  # 45 min/task
                }
            },
        },
    )
    _write_rc_json(
        rc_dir / "v0.2.0.json",
        {
            "rc": "v0.2.0",
            "wall_time_minutes": None,
            "tasks": {"total": 1, "by_agent": {"coder": 1}},
            "tokens": {
                "by_agent": {
                    "coder": {"input": 500, "output": 500, "cache_read": 0, "cache_write": 0},
                }
            },
            # No "wall" key -- a legacy rollup predating the per-agent wall-time field.
        },
    )

    (root / "projects" / "empty-project" / "metrics").mkdir(parents=True)

    return root


class TestAgentMetricsShEndToEnd:
    def test_golden_output_one_block_per_project_one_row_per_agent(self, fixture_kanban_root):
        result = subprocess.run(
            ["bash", str(_AGENT_METRICS_SH), "--kanban-root", str(fixture_kanban_root), "--no-color"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            env=_clean_env(fixture_kanban_root),
        )
        assert result.returncode == 0, result.stderr

        # Hand-computed from the fixture: coder is invoked in v0.1.0 (2
        # tasks, 1,500 tokens, 1,200 s over 2 tasks) and v0.2.0 (1 task,
        # 1,000 tokens, no wall data) -> Avg Wall 10.0, Avg Tokens 1,250.0,
        # Avg Tasks 1.5, totals 2,500 / 3. writer only in v0.1.0 -> 45.0,
        # 3,000.0, 1.0, totals 3,000 / 1. Both rollups shipped.
        expected = (
            "=== Agent Metrics: demo-project (last 10 RCs) ===\n"
            "\n"
            "Agent         Avg Wall (min/task)  Avg Tokens (/RC)  Avg Tasks (/RC)   Total Tokens  Total Tasks\n"
            "coder                        10.0           1,250.0              1.5          2,500            3\n"
            "writer                       45.0           3,000.0              1.0          3,000            1\n"
            "window: 2 shipped RCs (0 cancelled excluded)\n"
            "\n"
            "=== Agent Metrics: empty-project (last 10 RCs) ===\n"
            "\n"
            "  (no metrics yet)\n"
            "\n"
        )
        assert result.stdout == expected
        # coder (10 min/task) and writer (45 min/task) are invoked in the
        # same set of RCs yet render DIFFERENT wall figures -- confirming
        # the reader no longer joins on the RC's own shared elapsed time
        # (which every agent touching that RC would report identically);
        # each is now that agent's own per-task mean.
        assert "coder                        10.0" in result.stdout
        assert "writer                       45.0" in result.stdout

    def test_project_with_no_metrics_files_still_exits_zero_with_placeholder(self, fixture_kanban_root):
        result = subprocess.run(
            [
                "bash", str(_AGENT_METRICS_SH),
                "--kanban-root", str(fixture_kanban_root),
                "--project", "empty-project",
                "--no-color",
            ],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            env=_clean_env(fixture_kanban_root),
        )
        assert result.returncode == 0, result.stderr
        assert "(no metrics yet)" in result.stdout

    def test_window_flag_narrows_the_averaging_horizon(self, fixture_kanban_root):
        result = subprocess.run(
            [
                "bash", str(_AGENT_METRICS_SH),
                "--kanban-root", str(fixture_kanban_root),
                "--project", "demo-project",
                "--window", "1",
                "--no-color",
            ],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            env=_clean_env(fixture_kanban_root),
        )
        assert result.returncode == 0, result.stderr
        assert "last 1 RCs" in result.stdout
        # Only v0.2.0 (the newest) is in a window of 1: writer had zero
        # invocations in that single RC, so it must not appear at all, and
        # coder's wall time must render '--' (v0.2.0 has no "wall" key at
        # all -- a legacy rollup predating per-task wall-time capture).
        assert "writer" not in result.stdout
        assert (
            "coder                          --           1,000.0              1.0          1,000            1"
            in result.stdout
        )
        assert "window: 1 shipped RCs (0 cancelled excluded)" in result.stdout

    def test_help_flag_prints_usage_and_exits_zero(self):
        result = subprocess.run(
            ["bash", str(_AGENT_METRICS_SH), "--help"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
        assert result.returncode == 0
        for flag in ("--all", "--since", "--last", "--window", "--project", "--kanban-root", "--no-color"):
            assert flag in result.stdout, flag
        assert "mutually exclusive" in result.stdout

    def test_every_averaged_column_header_states_its_own_denominator(self, fixture_kanban_root):
        # A bare "Avg Tasks" label used to carry no denominator at all --
        # readable as a window total rather than a per-RC mean. Every
        # averaged column now names its own denominator, and Avg Wall's
        # ("per task") must read differently from the other two's
        # ("per RC") since they are pooled over different data.
        result = subprocess.run(
            ["bash", str(_AGENT_METRICS_SH), "--kanban-root", str(fixture_kanban_root), "--no-color"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            env=_clean_env(fixture_kanban_root),
        )
        assert result.returncode == 0, result.stderr
        assert "Avg Wall (min/task)" in result.stdout
        assert "Avg Tokens (/RC)" in result.stdout
        assert "Avg Tasks (/RC)" in result.stdout

    def test_agents_sharing_invocation_rcs_no_longer_share_a_coincidental_wall_value(
        self, fixture_kanban_root
    ):
        # coder and writer are both invoked only in v0.1.0. A reader that
        # joins on the RC's own shared wall_time_minutes would print the
        # SAME figure for both instead of either agent's own per-task
        # time -- they must render DIFFERENT values.
        result = subprocess.run(
            ["bash", str(_AGENT_METRICS_SH), "--kanban-root", str(fixture_kanban_root), "--no-color"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            env=_clean_env(fixture_kanban_root),
        )
        assert result.returncode == 0, result.stderr
        lines = {ln.split()[0]: ln for ln in result.stdout.splitlines() if ln.startswith(("coder", "writer"))}
        coder_wall = lines["coder"].split()[1]
        writer_wall = lines["writer"].split()[1]
        assert coder_wall != writer_wall
        assert coder_wall == "10.0"
        assert writer_wall == "45.0"

    def test_rejects_non_positive_window(self, fixture_kanban_root):
        result = subprocess.run(
            ["bash", str(_AGENT_METRICS_SH), "--kanban-root", str(fixture_kanban_root), "--window", "0"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            env=_clean_env(fixture_kanban_root),
        )
        assert result.returncode == 1
        assert "--window" in result.stderr


# ---------------------------------------------------------------------------
# End-to-end: decimals, totals, footer and the span flags
# ---------------------------------------------------------------------------


def _span_kanban_root(tmp_path: pathlib.Path, rc_seeder) -> pathlib.Path:
    """A kanban root with one registered project, "p", whose rollups are
    written by `rc_seeder(rc_dir)`."""
    root = tmp_path / "kanban"
    root.mkdir()
    (root / "projects.cfg").write_text("[project:p]\npriority=1\nenabled=true\n", encoding="utf-8")
    rc_seeder(root / "projects" / "p" / "metrics" / "rc")
    return root


def _run_agent_metrics(kanban_root: pathlib.Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "bash", str(_AGENT_METRICS_SH),
            "--project", "p",
            "--kanban-root", str(kanban_root),
            "--no-color",
            *extra,
        ],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_SECONDS,
        env=_clean_env(kanban_root),
    )


# A rendered agent row: name, three one-decimal averages (or '--'), two
# integer totals -- the row shape render_project_block() documents.
_ROW_RE = re.compile(
    r"^(\S+)\s+(--|[\d,]+\.\d)\s+(--|[\d,]+\.\d)\s+(--|[\d,]+\.\d)\s+([\d,]+)\s+([\d,]+)\s*$"
)
_FOOTER_RE = re.compile(r"^(window|all|since \S+): ([\d,]+) shipped RCs \(([\d,]+) cancelled excluded\)$")


def _rows(stdout: str) -> dict[str, tuple[str, ...]]:
    rows = {}
    for line in stdout.splitlines():
        m = _ROW_RE.match(line)
        if m and m.group(1) != "Agent":
            rows[m.group(1)] = m.groups()[1:]
    return rows


def _footer(stdout: str) -> tuple[str, int, int]:
    matches = [_FOOTER_RE.match(line) for line in stdout.splitlines()]
    found = [m for m in matches if m]
    assert len(found) == 1, stdout
    label, shipped, excluded = found[0].groups()
    return label, int(shipped.replace(",", "")), int(excluded.replace(",", ""))


class TestAgentMetricsDecimalsTotalsAndFooter:
    def test_cancelled_rc_in_range_changes_no_cm_figure_and_is_narrated(self, tmp_path):
        root = _span_kanban_root(tmp_path, _five_shipped_one_cancelled)
        result = _run_agent_metrics(root)
        assert result.returncode == 0, result.stderr

        # Five shipped RCs, cm: 2 tasks and 220 tokens each (100*2 input +
        # 10*2 output); the cancelled RC's 7 tasks enter nothing.
        expected = (
            "=== Agent Metrics: p (last 10 RCs) ===\n"
            "\n"
            "Agent         Avg Wall (min/task)  Avg Tokens (/RC)  Avg Tasks (/RC)   Total Tokens  Total Tasks\n"
            "cm                             --             220.0              2.0          1,100           10\n"
            "window: 5 shipped RCs (1 cancelled excluded)\n"
            "\n"
        )
        assert result.stdout == expected

    def test_large_totals_and_averages_carry_thousands_separators(self, tmp_path):
        def seed(rc_dir):
            write_rollups(
                rc_dir,
                [
                    RcSeed("v1.0.0", "shipped", {"coder": AgentLoad(tasks=3, input=1_000_000, output=234_567)}),
                    RcSeed("v1.1.0", "shipped", {"coder": AgentLoad(tasks=4, input=2_000_000)}),
                ],
            )

        result = _run_agent_metrics(_span_kanban_root(tmp_path, seed))
        assert result.returncode == 0, result.stderr
        # tokens 1,234,567 + 2,000,000 = 3,234,567 over 2 RCs; tasks 7.
        assert _rows(result.stdout)["coder"] == ("--", "1,617,283.5", "3.5", "3,234,567", "7")

    def test_footer_prints_when_nothing_was_excluded(self, tmp_path):
        root = _span_kanban_root(tmp_path, lambda d: build_rc_history(d, 3))
        result = _run_agent_metrics(root)
        assert result.returncode == 0, result.stderr
        assert _footer(result.stdout) == ("window", 3, 0)

    def test_no_shipped_rc_renders_the_placeholder_and_counts_the_excluded(self, tmp_path):
        root = _span_kanban_root(tmp_path, lambda d: build_rc_history(d, 2, cancelled={0, 1}))
        result = _run_agent_metrics(root)
        assert result.returncode == 0, result.stderr
        assert "(no metrics yet)" in result.stdout
        assert _footer(result.stdout) == ("window", 0, 2)


class TestAgentMetricsSpanFlags:
    """A 40-rollup history, v0.9.0 .. v0.28.1, with cancelled rollups at
    indices 1, 15, 22 (v0.20.0) and 38. Every expected figure is summed
    here from the seeds' literal loads, not by calling the library."""

    _CANCELLED = {1, 15, 22, 38}

    @pytest.fixture
    def history(self, tmp_path):
        seeds_box = {}

        def seed(rc_dir):
            seeds_box["seeds"] = build_rc_history(rc_dir, 40, cancelled=self._CANCELLED)

        root = _span_kanban_root(tmp_path, seed)
        return root, seeds_box["seeds"]

    @staticmethod
    def _assert_rows_match_hand_sums(stdout: str, selected: list) -> None:
        rows = _rows(stdout)
        agents = sorted({a for s in selected for a, ld in s.agents.items() if ld.tasks > 0})
        assert sorted(rows) == agents
        for agent in agents:
            loads = [s.agents[agent] for s in selected if agent in s.agents and s.agents[agent].tasks > 0]
            hand_tokens = sum(ld.token_total for ld in loads)
            hand_tasks = sum(ld.tasks for ld in loads)
            wall_s = sum(ld.wall_seconds for ld in loads)
            wall_n = sum(ld.wall_task_count for ld in loads)
            wall, avg_tokens, avg_tasks, total_tokens, total_tasks = rows[agent]
            assert total_tokens == f"{hand_tokens:,}", agent
            assert total_tasks == f"{hand_tasks:,}", agent
            # One-decimal cells sit within half a tenth of the exact mean.
            assert abs(float(avg_tokens.replace(",", "")) - hand_tokens / len(loads)) <= 0.05 + 1e-9, agent
            assert abs(float(avg_tasks) - hand_tasks / len(loads)) <= 0.05 + 1e-9, agent
            assert abs(float(wall) - wall_s / wall_n / 60) <= 0.05 + 1e-9, agent

    def test_all_covers_every_shipped_rc(self, history):
        root, seeds = history
        result = _run_agent_metrics(root, "--all")
        assert result.returncode == 0, result.stderr
        assert "=== Agent Metrics: p (all shipped RCs) ===" in result.stdout
        assert _footer(result.stdout) == ("all", 36, 4)
        self._assert_rows_match_hand_sums(result.stdout, [s for s in seeds if s.outcome == "shipped"])

    def test_since_uses_semantic_version_order(self, history):
        root, seeds = history
        result = _run_agent_metrics(root, "--since", "v0.20.0")
        assert result.returncode == 0, result.stderr
        assert "=== Agent Metrics: p (shipped RCs since v0.20.0) ===" in result.stdout
        # Indices 22..39 are at or above v0.20.0; 22 and 38 are cancelled.
        selected = [seeds[i] for i in range(23, 40) if i != 38]
        assert _footer(result.stdout) == ("since v0.20.0", 16, 2)
        self._assert_rows_match_hand_sums(result.stdout, selected)

    def test_last_takes_the_newest_shipped_rcs(self, history):
        root, seeds = history
        result = _run_agent_metrics(root, "--last", "3")
        assert result.returncode == 0, result.stderr
        assert "=== Agent Metrics: p (last 3 RCs) ===" in result.stdout
        # Newest first: 39, 38 (cancelled, takes no slot), 37, 36.
        assert _footer(result.stdout) == ("window", 3, 1)
        self._assert_rows_match_hand_sums(result.stdout, [seeds[39], seeds[37], seeds[36]])

    def test_window_and_last_select_the_same_rows(self, history):
        root, _ = history
        by_window = _run_agent_metrics(root, "--window", "3")
        by_last = _run_agent_metrics(root, "--last", "3")
        assert by_window.returncode == 0 and by_last.returncode == 0
        assert by_window.stdout == by_last.stdout

    def test_default_is_the_newest_ten_shipped_rcs(self, history):
        root, seeds = history
        result = _run_agent_metrics(root)
        assert result.returncode == 0, result.stderr
        assert "=== Agent Metrics: p (last 10 RCs) ===" in result.stdout
        newest_shipped = [s for s in reversed(seeds) if s.outcome == "shipped"][:10]
        assert _footer(result.stdout) == ("window", 10, 1)
        self._assert_rows_match_hand_sums(result.stdout, newest_shipped)

    @pytest.mark.parametrize(
        "flags, named",
        [
            (["--all", "--last", "3"], "--all"),
            (["--window", "3", "--all"], "--window"),
            (["--since", "v0.20.0", "--last", "2"], "--since"),
            (["--last", "0"], "--last"),
            (["--last", "three"], "--last"),
            (["--last"], "--last"),
            (["--since", "notaversion"], "--since"),
            (["--since", "v1.2"], "--since"),
            (["--window", "0"], "--window"),
        ],
    )
    def test_invalid_span_flags_are_usage_errors(self, history, flags, named):
        root, _ = history
        result = _run_agent_metrics(root, *flags)
        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr.startswith("ERROR: ")
        assert named in result.stderr

    @pytest.mark.parametrize(
        "flags, named",
        [
            (["--all", "--last", "3"], "--all"),
            (["--last", "0"], "--last"),
            (["--since", "notaversion"], "--since"),
            (["--window", "0"], "--window"),
        ],
    )
    def test_python_entry_point_rejects_the_same_flags_with_exit_1(self, history, flags, named):
        root, _ = history
        result = subprocess.run(
            [sys.executable, str(_LIB_PATH / "agent_metrics.py"), "--kanban-root", str(root), *flags],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
        assert result.returncode == 1
        assert result.stderr.startswith("ERROR: ")
        assert named in result.stderr

    def test_python_entry_point_renders_the_same_block_as_the_script(self, history):
        root, _ = history
        via_script = _run_agent_metrics(root, "--since", "v0.20.0")
        via_python = subprocess.run(
            [
                sys.executable, str(_LIB_PATH / "agent_metrics.py"),
                "--kanban-root", str(root), "--project", "p", "--since", "v0.20.0",
            ],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
        assert via_python.returncode == 0, via_python.stderr
        assert via_python.stdout == via_script.stdout


# ---------------------------------------------------------------------------
# Regression: show-metrics.sh and metrics.sh still produce their
# pre-conversion output after converging onto the shared formatter.
# ---------------------------------------------------------------------------


class TestExistingRenderersUnchangedAfterConvergingOnSharedFormatter:
    def test_show_metrics_per_agent_view_unchanged(self, fixture_kanban_root):
        result = subprocess.run(
            [
                "bash", str(_SHOW_METRICS_SH),
                "--kanban-root", str(fixture_kanban_root),
                "--project", "demo-project",
                "--per-agent",
                "--no-color",
            ],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            env=_clean_env(fixture_kanban_root),
        )
        assert result.returncode == 0, result.stderr
        assert "=== RC Metrics: demo-project — per-agent (v0.2.0) ===" in result.stdout
        # v0.2.0 is the newest RC; only coder appears in it.
        assert "coder               500        500            0             0" in result.stdout

    def test_show_metrics_history_csv_comma_formatting_unchanged(self, fixture_kanban_root, tmp_path):
        history_csv = fixture_kanban_root / "projects" / "demo-project" / "metrics" / "history.csv"
        history_csv.write_text(
            "rc,wall_time_minutes,input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,tasks_total\n"
            "v0.1.0,,5644879,123456,0,0,10\n"
            "v0.2.0,,500,500,0,0,\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                "bash", str(_SHOW_METRICS_SH),
                "--kanban-root", str(fixture_kanban_root),
                "--project", "demo-project",
                "--no-color",
            ],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            env=_clean_env(fixture_kanban_root),
        )
        assert result.returncode == 0, result.stderr
        assert "5,644,879" in result.stdout
        assert "123,456" in result.stdout
        # tasks_total is blank for v0.2.0 -- must render the placeholder, not "0".
        lines = [l for l in result.stdout.splitlines() if l.startswith("v0.2.0")]
        assert lines and lines[0].rstrip().endswith("--")

    def test_metrics_sh_task_count_now_comma_formatted(self, fixture_kanban_root):
        day_dir = fixture_kanban_root / "projects" / "demo-project" / "metrics" / "day"
        day_dir.mkdir(parents=True, exist_ok=True)
        (fixture_kanban_root / "projects" / "demo-project" / "release-state.md").write_text(
            "## Active RC\nnone\n", encoding="utf-8"
        )
        import datetime

        today = datetime.datetime.now().strftime("%Y-%m-%d")
        (day_dir / f"{today}.json").write_text(
            json.dumps(
                {
                    "date": today,
                    "project": "demo-project",
                    "rcs_included": ["v0.1.0"],
                    "tokens": {"total": {"input": 1000, "output": 500, "cache_read": 0, "cache_write": 0}},
                    "tasks": {"total": 12345, "by_agent": {}},
                }
            ),
            encoding="utf-8",
        )
        result = subprocess.run(
            ["bash", str(_METRICS_SH), "--kanban-root", str(fixture_kanban_root), "--no-color"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            env=_clean_env(fixture_kanban_root),
        )
        assert result.returncode == 0, result.stderr
        assert "Tasks:        12,345" in result.stdout
