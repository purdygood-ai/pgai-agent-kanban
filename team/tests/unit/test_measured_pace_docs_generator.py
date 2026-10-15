"""
test_measured_pace_docs_generator.py
======================================
Behavioral unit tests for the measured-pace doc generator:

  team/scripts/generate_measured_pace_docs.py   (compute + render + --check)

Acceptance criteria exercised:

  1. compute_pace_stats() over fixture rollups with a known-by-construction
     range, median, and composed RC figure produces exactly that output.
  2. A render writes the measured-pace block into both target docs; --check
     immediately afterward exits 0.
  3. Hand-editing a rendered figure makes --check report drift naming the
     figure, exit non-zero, and leave the file untouched.
  4. Zero usable rollups renders an explicit "not available yet" block with
     no numeric figure in it at all -- never a fabricated zero.
  5. A thin sample (fewer than the thin threshold) still renders the real,
     honestly-computed numbers but flags them inline as provisional.
  6. The shared per-agent averaging module (compute_agent_averages) is the
     one source of the composed-RC-cost formula's per-agent task counts --
     this generator does not define a second, disagreeing average.
  7. A doc missing the marker pair is refused loud as a structural problem,
     not silently skipped or drift-reported.

All filesystem seams are constructed in tmp_path (pytest-managed) or under
PGAI_AGENT_KANBAN_TEMP_DIR via the framework temp helper. No real kanban
root or live disk state is accessed.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

# ---------------------------------------------------------------------------
# Ensure the package root is importable regardless of cwd.
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/tests/unit/ -> team/
_DEV_TREE_ROOT = _TEAM_DIR.parent
_GENERATOR = _TEAM_DIR / "scripts" / "generate_measured_pace_docs.py"

# White-box import of the generator module itself (a script, not a package
# member) for the function-level tests below -- same seam
# test_operator_verb_docs_generator.py uses for its own generator.
_spec = importlib.util.spec_from_file_location(
    "generate_measured_pace_docs_under_test", _GENERATOR
)
_gen = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules[_spec.name] = _gen  # dataclass() needs the module registered to resolve types
_spec.loader.exec_module(_gen)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _rc_rollup(
    rc: str,
    wall_time_minutes: float | None,
    tasks_by_agent: dict[str, int],
) -> dict:
    """Build one per-RC rollup dict with just the fields this generator
    reads (outcome, wall_time_minutes, tasks.total, tasks.by_agent) -- matching
    metrics_aggregator.py's own aggregate_rc() output shape closely enough
    for compute_pace_stats()/compute_agent_averages() to operate on.
    """
    total = sum(tasks_by_agent.values())
    return {
        "rc": rc,
        "outcome": "shipped",
        "wall_time_minutes": wall_time_minutes,
        "tasks": {"total": total, "by_agent": dict(tasks_by_agent)},
        "tokens": {"by_agent": {a: {} for a in tasks_by_agent}},
    }


def _write_rc_dir(tmp_path: pathlib.Path, rollups: list[dict]) -> pathlib.Path:
    rc_dir = tmp_path / "rc"
    rc_dir.mkdir()
    for rollup in rollups:
        (rc_dir / f"{rollup['rc']}.json").write_text(
            json.dumps(rollup), encoding="utf-8"
        )
    return rc_dir


def _copy_real_doc(tmp_path: pathlib.Path, name: str) -> pathlib.Path:
    doc = tmp_path / name
    doc.write_text((_DEV_TREE_ROOT / name).read_text(encoding="utf-8"), encoding="utf-8")
    return doc


def _run(args: list[str], timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_GENERATOR), *args],
        cwd=str(_TEAM_DIR),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# 1 -- compute_pace_stats() over fixture rollups with a known range/median
# ---------------------------------------------------------------------------


def test_compute_pace_stats_known_range_and_median():
    # Three RCs, hand-picked so wall/tasks divides evenly and the range,
    # median, and sample size are known by construction:
    #   v1.0.0: 40 min / 4 tasks = 10.0 min/task
    #   v1.0.1: 60 min / 4 tasks = 15.0 min/task
    #   v1.0.2: 100 min / 5 tasks = 20.0 min/task
    rollups = [
        _rc_rollup("v1.0.2", 100.0, {"pm": 1, "coder": 2, "tester": 1, "cm": 1}),
        _rc_rollup("v1.0.1", 60.0, {"pm": 1, "coder": 2, "tester": 1}),
        _rc_rollup("v1.0.0", 40.0, {"pm": 1, "coder": 2, "cm": 1}),
    ]

    stats = _gen.compute_pace_stats(rollups, window_requested=3)

    assert stats.has_data is True
    assert stats.rc_count == 3
    assert stats.task_count == 4 + 4 + 5
    assert stats.per_task_low == 10.0
    assert stats.per_task_high == 20.0
    assert stats.per_task_median == 15.0
    assert stats.thin is False  # 3 meets the default threshold


def test_compute_pace_stats_composed_rc_uses_shared_averaging_module():
    # coder appears in all 3 RCs with 2 tasks each -> avg_tasks[coder] == 2.0.
    rollups = [
        _rc_rollup("v1.0.2", 100.0, {"pm": 1, "coder": 2, "tester": 1, "cm": 1}),
        _rc_rollup("v1.0.1", 60.0, {"pm": 1, "coder": 2, "tester": 1}),
        _rc_rollup("v1.0.0", 40.0, {"pm": 1, "coder": 2, "cm": 1}),
    ]
    stats = _gen.compute_pace_stats(rollups, window_requested=3)

    # Reuse the SAME shared function directly and assert the generator's
    # agent_avg_tasks is exactly what it returns -- not a second, locally
    # re-derived average that could disagree with the dashboard.
    from pgai_agent_kanban.lib.agent_metrics import compute_agent_averages

    expected = compute_agent_averages(rollups)
    assert stats.agent_avg_tasks == {
        agent: vals["avg_tasks"] for agent, vals in expected.items()
    }
    assert stats.agent_avg_tasks["coder"] == 2.0

    # composed[agent] == avg_tasks[agent] * median (15.0)
    assert stats.composed["coder"] == pytest.approx(2.0 * 15.0, abs=0.05)
    assert stats.composed_total == pytest.approx(
        sum(stats.composed.values()), abs=0.05
    )


def test_compute_pace_stats_skips_rc_with_no_wall_time_or_no_tasks():
    rollups = [
        _rc_rollup("v1.0.0", 30.0, {"coder": 3}),
        _rc_rollup("v1.0.1", None, {"coder": 3}),  # RC not yet closed
        _rc_rollup("v1.0.2", 30.0, {}),  # zero tasks
    ]
    stats = _gen.compute_pace_stats(rollups, window_requested=3)
    assert stats.rc_count == 1
    assert stats.task_count == 3
    assert stats.per_task_low == stats.per_task_high == 10.0


# ---------------------------------------------------------------------------
# 4 -- zero usable rollups: no fabricated number
# ---------------------------------------------------------------------------


def test_compute_pace_stats_no_data_renders_no_number():
    stats = _gen.compute_pace_stats([], window_requested=0)
    assert stats.has_data is False
    assert stats.rc_count == 0
    assert stats.per_task_median is None

    block = _gen.render_pace_block(stats)
    assert "not available yet" in block
    # No numeric pace figure anywhere -- incidental digits (e.g. "python3"
    # in the re-run command it names) are fine; a number of minutes is not.
    import re

    assert re.search(r"\d[\d.]*\s*(min|×|RC\(s\)|task\(s\))", block) is None


# ---------------------------------------------------------------------------
# 5 -- thin sample: real numbers, flagged as provisional
# ---------------------------------------------------------------------------


def test_compute_pace_stats_thin_sample_flagged():
    rollups = [_rc_rollup("v1.0.0", 42.0, {"pm": 1, "coder": 1, "cm": 1})]
    stats = _gen.compute_pace_stats(rollups, window_requested=1)
    assert stats.has_data is True
    assert stats.rc_count == 1
    assert stats.thin is True
    assert stats.per_task_low == stats.per_task_high == 14.0

    block = _gen.render_pace_block(stats)
    assert "14.0" in block
    assert "thin sample" in block


# ---------------------------------------------------------------------------
# End-to-end render / --check against real target docs (tmp_path copies)
# ---------------------------------------------------------------------------


def test_render_writes_block_and_check_passes(tmp_path: pathlib.Path):
    readme = _copy_real_doc(tmp_path, "README.md")
    how_to = _copy_real_doc(tmp_path, "HOW_TO.md")
    rollups = [
        _rc_rollup("v1.0.2", 100.0, {"pm": 1, "coder": 2, "tester": 1, "cm": 1}),
        _rc_rollup("v1.0.1", 60.0, {"pm": 1, "coder": 2, "tester": 1}),
        _rc_rollup("v1.0.0", 40.0, {"pm": 1, "coder": 2, "cm": 1}),
    ]
    rc_dir = _write_rc_dir(tmp_path, rollups)

    render = _run(
        [
            "--rc-dir", str(rc_dir),
            "--readme", str(readme),
            "--how-to", str(how_to),
        ]
    )
    assert render.returncode == 0, render.stdout + render.stderr
    assert "median 15.0" in readme.read_text(encoding="utf-8")
    assert "median 15.0" in how_to.read_text(encoding="utf-8")

    check = _run(
        [
            "--check",
            "--rc-dir", str(rc_dir),
            "--readme", str(readme),
            "--how-to", str(how_to),
        ]
    )
    assert check.returncode == 0, check.stdout + check.stderr
    assert "OK" in check.stdout


def test_check_reports_drift_on_hand_edited_figure_and_writes_nothing(
    tmp_path: pathlib.Path,
):
    readme = _copy_real_doc(tmp_path, "README.md")
    how_to = _copy_real_doc(tmp_path, "HOW_TO.md")
    rollups = [_rc_rollup("v1.0.0", 40.0, {"pm": 1, "coder": 2, "cm": 1})]
    rc_dir = _write_rc_dir(tmp_path, rollups)

    render = _run(
        [
            "--rc-dir", str(rc_dir),
            "--readme", str(readme),
            "--how-to", str(how_to),
        ]
    )
    assert render.returncode == 0, render.stdout + render.stderr

    original = readme.read_text(encoding="utf-8")
    assert "median 10.0" in original, (
        "rendered figure literal changed -- update this test's expectation"
    )
    corrupted = original.replace("median 10.0", "median 999.0", 1)
    assert corrupted != original
    readme.write_text(corrupted, encoding="utf-8")

    check = _run(
        [
            "--check",
            "--rc-dir", str(rc_dir),
            "--readme", str(readme),
            "--how-to", str(how_to),
        ]
    )
    assert check.returncode != 0
    combined = check.stdout + check.stderr
    assert str(readme) in combined
    assert "error:" in check.stderr
    assert "message:" in check.stderr
    assert "remedy:" in check.stderr

    after = readme.read_text(encoding="utf-8")
    assert after == corrupted, "--check must never write to the doc it is checking"


# ---------------------------------------------------------------------------
# 7 -- missing marker pair is a structural refusal, not a silent skip
# ---------------------------------------------------------------------------


def test_render_reports_missing_marker_pair_as_usage_error(tmp_path: pathlib.Path):
    readme = tmp_path / "README.md"
    readme.write_text("# empty doc, no markers\n", encoding="utf-8")
    how_to = tmp_path / "HOW_TO.md"
    how_to.write_text("# empty doc, no markers\n", encoding="utf-8")

    rollups = [_rc_rollup("v1.0.0", 40.0, {"coder": 4})]
    rc_dir = _write_rc_dir(tmp_path, rollups)

    result = _run(
        [
            "--rc-dir", str(rc_dir),
            "--readme", str(readme),
            "--how-to", str(how_to),
        ]
    )
    assert result.returncode == 2
    assert "marker" in result.stderr.lower()
    assert readme.read_text(encoding="utf-8") == "# empty doc, no markers\n"


# ---------------------------------------------------------------------------
# full_window_size() -- "every rollup present" default
# ---------------------------------------------------------------------------


def test_full_window_size_counts_rollup_files(tmp_path: pathlib.Path):
    rollups = [
        _rc_rollup("v1.0.0", 10.0, {"coder": 1}),
        _rc_rollup("v1.0.1", 10.0, {"coder": 1}),
    ]
    rc_dir = _write_rc_dir(tmp_path, rollups)
    assert _gen.full_window_size(rc_dir) == 2


def test_full_window_size_missing_directory_is_zero(tmp_path: pathlib.Path):
    assert _gen.full_window_size(tmp_path / "does-not-exist") == 0


# ---------------------------------------------------------------------------
# Real end-to-end smoke test against this repo's own real docs and the
# generator's own CLI, using the fixture rc-dir (not the live kanban root --
# CODER never points a test at the live install).
# ---------------------------------------------------------------------------


def test_cli_prints_sample_size_as_rcs_and_tasks(tmp_path: pathlib.Path):
    readme = _copy_real_doc(tmp_path, "README.md")
    how_to = _copy_real_doc(tmp_path, "HOW_TO.md")
    rollups = [
        _rc_rollup("v1.0.1", 60.0, {"pm": 1, "coder": 2, "tester": 1}),
        _rc_rollup("v1.0.0", 40.0, {"pm": 1, "coder": 2, "cm": 1}),
    ]
    rc_dir = _write_rc_dir(tmp_path, rollups)

    result = _run(
        [
            "--rc-dir", str(rc_dir),
            "--readme", str(readme),
            "--how-to", str(how_to),
        ]
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "n=2 RC(s)/8 task(s)" in result.stdout
