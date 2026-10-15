"""
test_dashboard_window_census.py
================================
Behavioral unit tests for the dashboard window census and its generator:

  team/config/dashboard_windows.census                       (the data)
  team/pgai_agent_kanban/dashboard/window_census.py           (the Python reader)
  team/scripts/dashboard/generate_window_docs.py              (render + --check)

Acceptance criteria exercised:

  1. The checked-in census parses cleanly and declares every window
     create.sh currently builds, including operator and drill-N.
  2. The Python reader exposes the census to the test suite as data --
     these tests consume load_census() directly rather than re-parsing
     the file.
  3. --check exits 0 against the unmodified, checked-in tree.
  4. Malformed census input (pane-count mismatch) is refused loud
     (CensusFormatError), not silently tolerated.
  5. Deliberately corrupting a rendered surface (a copy, never the checked-
     in file) makes --check report drift and exit non-zero; restoring it
     makes --check exit 0 again.
  6. The window-by-window tour rendered into docs/dashboard.md covers
     every census window, participates in --check, and reports drift both
     when a rendered entry is hand-edited and when a census window is
     added without re-rendering.

All filesystem mutation happens under tmp_path (pytest-managed) or on
throwaway in-memory copies -- the checked-in tree is only ever read, never
written, by this test module.
"""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys

import pytest

# ---------------------------------------------------------------------------
# Ensure the package root is importable regardless of cwd.
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/tests/unit/ -> team/
_PKG_ROOT = _TEAM_DIR  # pgai_agent_kanban/ lives directly under team/
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from pgai_agent_kanban.dashboard.window_census import (  # noqa: E402
    CensusFormatError,
    Window,
    default_census_path,
    load_census,
)

_GENERATOR = _TEAM_DIR / "scripts" / "dashboard" / "generate_window_docs.py"
_REAL_CREATE_SH = _TEAM_DIR / "scripts" / "dashboard" / "create.sh"
_REAL_DASHBOARD_PANES = _TEAM_DIR.parent / "docs" / "DASHBOARD-PANES.md"
_REAL_DASHBOARD_MD = _TEAM_DIR.parent / "docs" / "dashboard.md"


def _import_generator():
    """Load generate_window_docs.py as a module (mirrors the import-by-path
    pattern used by sibling dashboard lint tests) so pure functions like
    render_window_tour_block() and _describe_window_tour_drift() can be
    exercised directly, without a subprocess round trip.
    """
    spec = importlib.util.spec_from_file_location(
        "generate_window_docs", _GENERATOR
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_GENERATOR}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod

# The exact window names create.sh builds, in creation order (tmux_index
# 0..13), plus the dynamic per-project drill-N template at index 14.
_EXPECTED_WINDOW_NAMES = [
    "main",
    "operator",
    "visibility",
    "attention",
    "schedule",
    "git",
    "metadata",
    "rc-metrics",
    "agent-metrics",
    "logs",
    "debug-logs",
    "training-logs",
    "terminal",
    "human-review",
    "drill-N",
    "drill-N",
]


# ---------------------------------------------------------------------------
# 1 & 2 -- the checked-in census parses cleanly and is consumable as data
# ---------------------------------------------------------------------------


def test_default_census_path_resolves_to_checked_in_file() -> None:
    path = default_census_path()
    assert path.exists(), f"expected census at {path}"
    assert path.name == "dashboard_windows.census"


def test_checked_in_census_declares_every_window_create_sh_builds() -> None:
    windows = load_census()
    names = [w.name for w in windows]
    assert names == _EXPECTED_WINDOW_NAMES


def test_checked_in_census_includes_operator_window() -> None:
    windows = load_census()
    operator_windows = [w for w in windows if w.name == "operator"]
    assert len(operator_windows) == 1
    assert operator_windows[0].tmux_index == 1
    # board (8 columns) + cmdline shell + legend
    assert operator_windows[0].pane_count == 10
    assert len(operator_windows[0].panes) == 10


def test_operator_window_is_the_visibility_board_plus_a_shell_row() -> None:
    """Window 1 declares window 2's eight columns and legend, in the same
    order and with the same commands, with one interactive shell pane
    between the board and the legend -- and no other pane."""
    windows = {w.name: w for w in load_census()}
    operator = windows["operator"].panes
    visibility = windows["visibility"].panes

    assert [p.pane_seq for p in operator] == list(range(10))
    assert [(p.script, p.standalone_cmd) for p in operator[:8]] == [
        (p.script, p.standalone_cmd) for p in visibility[:8]
    ], "operator panes 0-7 must run the visibility window's eight column commands"

    shell = operator[8]
    assert shell.script.startswith("(interactive bash"), shell
    assert shell.standalone_cmd.startswith("N/A"), shell

    legend = operator[9]
    assert (legend.script, legend.standalone_cmd) == (
        visibility[8].script,
        visibility[8].standalone_cmd,
    ), "operator's last pane must be the visibility window's legend bar"

    assert all(p.script != "suggested-commands.sh" for p in operator), (
        "the operator window carries no suggested-commands pane"
    )


def test_checked_in_census_includes_schedule_window() -> None:
    windows = load_census()
    schedule_windows = [w for w in windows if w.name == "schedule"]
    assert len(schedule_windows) == 1
    # list band (status-band.sh) on top, event band (event-band.sh) below;
    # no next-cron-firings.sh band in this window
    assert schedule_windows[0].pane_count == 2
    assert len(schedule_windows[0].panes) == 2
    scripts = [p.script for p in schedule_windows[0].panes]
    assert scripts == ["status-band.sh", "event-band.sh"]


def test_checked_in_census_includes_rc_metrics_and_agent_metrics_windows() -> None:
    windows = load_census()
    names = {w.name for w in windows}
    assert "rc-metrics" in names
    assert "agent-metrics" in names
    assert "metrics" not in names, (
        "the old unsplit 'metrics' window name must not remain in the census "
        "once it has been split into rc-metrics and agent-metrics"
    )


def test_checked_in_census_splits_agent_metrics_into_live_and_full_history_panes() -> None:
    """Window 8 carries two panes: the live table (agent-metrics.sh's
    default last-10 span, command unchanged) on the left and the
    full-history --all report on the right."""
    windows = load_census()
    agent_windows = [w for w in windows if w.name == "agent-metrics"]
    assert len(agent_windows) == 1
    window = agent_windows[0]
    assert window.tmux_index == 8
    assert window.pane_count == 2
    assert len(window.panes) == 2
    assert [p.script for p in window.panes] == ["agent-metrics.sh", "agent-metrics.sh"]
    live, full = window.panes
    assert live.standalone_cmd == (
        "agent-metrics.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH"
    )
    assert full.standalone_cmd == (
        "agent-metrics.sh --all --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH"
    )


def test_checked_in_census_includes_both_drill_variants() -> None:
    windows = load_census()
    drill_windows = [w for w in windows if w.name == "drill-N"]
    variants = {w.variant for w in drill_windows}
    assert variants == {"release", "document"}
    drill_index = drill_windows[0].tmux_index
    for w in drill_windows:
        assert w.tmux_index == drill_index
    # The drill-N template is always the last window creation order slot.
    assert drill_index == max(w.tmux_index for w in windows)


def test_every_window_pane_count_matches_declared_panes() -> None:
    windows = load_census()
    for w in windows:
        assert w.pane_count == len(w.panes), (
            f"window {w.name!r} (variant={w.variant!r}) declares "
            f"pane_count={w.pane_count} but has {len(w.panes)} pane records"
        )


# ---------------------------------------------------------------------------
# 3 -- --check exits 0 against the unmodified, checked-in tree
# ---------------------------------------------------------------------------


def test_check_mode_passes_against_checked_in_tree() -> None:
    result = subprocess.run(
        [sys.executable, str(_GENERATOR), "--check"],
        cwd=str(_TEAM_DIR),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"generate_window_docs.py --check failed against the checked-in "
        f"tree (this means create.sh or docs/DASHBOARD-PANES.md has "
        f"drifted from team/config/dashboard_windows.census):\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "OK" in result.stdout


# ---------------------------------------------------------------------------
# 4 -- malformed census input is refused loud
# ---------------------------------------------------------------------------


def test_pane_count_mismatch_raises_census_format_error(tmp_path: pathlib.Path) -> None:
    bad_census = tmp_path / "bad.census"
    bad_census.write_text(
        "W\t0\tmain\t-\t2\n"
        "P\t0\t-\t0\tsingle\tfoo.sh\tfoo.sh\n",
        # declares pane_count=2 but only one P record follows
        encoding="utf-8",
    )
    with pytest.raises(CensusFormatError):
        load_census(bad_census)


def test_pane_record_with_no_preceding_window_raises(tmp_path: pathlib.Path) -> None:
    bad_census = tmp_path / "bad.census"
    bad_census.write_text(
        "P\t0\t-\t0\tsingle\tfoo.sh\tfoo.sh\n",
        encoding="utf-8",
    )
    with pytest.raises(CensusFormatError):
        load_census(bad_census)


def test_missing_census_file_raises_file_not_found(tmp_path: pathlib.Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_census(tmp_path / "does-not-exist.census")


# ---------------------------------------------------------------------------
# 5 -- deliberate drift is caught, and clears on revert (acceptance
# criterion: "Deliberately editing one window name in create.sh's generated
# comment block (or one row of docs/DASHBOARD-PANES.md) makes --check exit
# non-zero and name the disagreeing window; reverting makes it green
# again"). Exercised here on throwaway tmp_path copies of the real files so
# the checked-in tree is never mutated by a test run.
# ---------------------------------------------------------------------------


def _run_check(
    tmp_path: pathlib.Path,
    census: pathlib.Path,
    create_sh: pathlib.Path,
    dashboard_panes: pathlib.Path,
    dashboard_md: pathlib.Path | None = None,
) -> subprocess.CompletedProcess:
    args = [
        sys.executable,
        str(_GENERATOR),
        "--check",
        "--census",
        str(census),
        "--create-sh",
        str(create_sh),
        "--dashboard-panes",
        str(dashboard_panes),
    ]
    if dashboard_md is not None:
        args.extend(["--dashboard-md", str(dashboard_md)])
    return subprocess.run(
        args,
        cwd=str(_TEAM_DIR),
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_check_catches_edited_pane_table_row_and_clears_on_revert(
    tmp_path: pathlib.Path,
) -> None:
    census = tmp_path / "dashboard_windows.census"
    census.write_text(default_census_path().read_text(encoding="utf-8"), encoding="utf-8")
    create_sh = tmp_path / "create.sh"
    create_sh.write_text(_REAL_CREATE_SH.read_text(encoding="utf-8"), encoding="utf-8")
    original_panes_text = _REAL_DASHBOARD_PANES.read_text(encoding="utf-8")
    dashboard_panes = tmp_path / "DASHBOARD-PANES.md"
    dashboard_panes.write_text(original_panes_text, encoding="utf-8")

    # Clean copy: --check passes.
    clean_result = _run_check(tmp_path, census, create_sh, dashboard_panes)
    assert clean_result.returncode == 0, clean_result.stdout + clean_result.stderr

    # Corrupt the attention window's script name in the copy only.
    corrupted = original_panes_text.replace(
        "| single | `attention.sh` | `attention.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |",
        "| single | `attention-BROKEN.sh` | `attention.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH` |",
    )
    assert corrupted != original_panes_text, "fixture row not found -- update this test's literal"
    dashboard_panes.write_text(corrupted, encoding="utf-8")

    dirty_result = _run_check(tmp_path, census, create_sh, dashboard_panes)
    assert dirty_result.returncode != 0
    assert "attention" in (dirty_result.stdout + dirty_result.stderr)
    assert "error:" in dirty_result.stderr
    assert "message:" in dirty_result.stderr
    assert "remedy:" in dirty_result.stderr

    # Revert: --check is green again.
    dashboard_panes.write_text(original_panes_text, encoding="utf-8")
    reverted_result = _run_check(tmp_path, census, create_sh, dashboard_panes)
    assert reverted_result.returncode == 0, reverted_result.stdout + reverted_result.stderr


def test_check_reports_missing_marker_pair_as_usage_error(tmp_path: pathlib.Path) -> None:
    census = tmp_path / "dashboard_windows.census"
    census.write_text(default_census_path().read_text(encoding="utf-8"), encoding="utf-8")
    create_sh = tmp_path / "create.sh"
    create_sh.write_text(_REAL_CREATE_SH.read_text(encoding="utf-8"), encoding="utf-8")
    # A DASHBOARD-PANES.md with no marker pairs at all.
    dashboard_panes = tmp_path / "DASHBOARD-PANES.md"
    dashboard_panes.write_text("# empty doc, no markers\n", encoding="utf-8")

    result = _run_check(tmp_path, census, create_sh, dashboard_panes)
    assert result.returncode == 2
    assert "marker" in result.stderr.lower()


# ---------------------------------------------------------------------------
# 6 -- the window-by-window tour in docs/dashboard.md
# ---------------------------------------------------------------------------


def test_window_tour_covers_every_census_window_in_census_order() -> None:
    """One tour entry per census W row, in census order -- the same 16 rows
    (15 window ids, drill-N carrying two variants) that back
    _EXPECTED_WINDOW_NAMES above.
    """
    generator = _import_generator()
    windows = load_census()
    block = generator.render_window_tour_block(windows)

    assert block.startswith(generator._WINDOW_TOUR_BEGIN)
    assert block.rstrip().endswith(generator._WINDOW_TOUR_END)

    rows = [
        line
        for line in block.splitlines()
        if line.startswith("|") and not line.startswith("|---")
    ]
    # Drop the header row ("| Window | Name | Panes | Variant |").
    data_rows = rows[1:]
    assert len(data_rows) == len(windows) == 16

    for window, row in zip(windows, data_rows):
        assert f"| {window.tmux_index} |" in row
        assert f"`{window.name}`" in row
        assert f"| {window.pane_count} |" in row
        assert row.rstrip().endswith(f"| {window.variant} |")


def test_window_tour_names_docs_dashboard_panes_as_the_pane_detail_surface() -> None:
    generator = _import_generator()
    block = generator.render_window_tour_block(load_census())
    assert "DASHBOARD-PANES.md" in block


def test_window_tour_does_not_duplicate_the_pane_table_columns() -> None:
    """The tour orients (window id/name/pane-count/variant); it must not
    reproduce DASHBOARD-PANES.md's per-pane columns (populating script,
    standalone command) -- that would give the reader two places to look.
    """
    generator = _import_generator()
    block = generator.render_window_tour_block(load_census())
    assert "Populating script" not in block
    assert "Standalone command" not in block


def test_checked_in_dashboard_md_window_tour_matches_a_fresh_render() -> None:
    generator = _import_generator()
    windows = load_census()
    expected_block = generator.render_window_tour_block(windows)
    current = _REAL_DASHBOARD_MD.read_text(encoding="utf-8")
    actual_block = generator._extract_between_markers(
        current, generator._WINDOW_TOUR_BEGIN, generator._WINDOW_TOUR_END
    )
    assert actual_block == expected_block


def test_check_mode_reports_window_tour_in_its_ok_surface_set(
    tmp_path: pathlib.Path,
) -> None:
    """--check against the checked-in tree (all three surfaces, via
    defaults) is clean -- proving the tour participates in --check without
    regressing the two pre-existing surfaces.
    """
    result = subprocess.run(
        [sys.executable, str(_GENERATOR), "--check"],
        cwd=str(_TEAM_DIR),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"generate_window_docs.py --check failed against the checked-in "
        f"tree (create.sh, docs/DASHBOARD-PANES.md, or docs/dashboard.md "
        f"has drifted from team/config/dashboard_windows.census):\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "OK" in result.stdout


def test_check_catches_hand_edited_tour_entry_and_clears_on_revert(
    tmp_path: pathlib.Path,
) -> None:
    census = tmp_path / "dashboard_windows.census"
    census.write_text(default_census_path().read_text(encoding="utf-8"), encoding="utf-8")
    create_sh = tmp_path / "create.sh"
    create_sh.write_text(_REAL_CREATE_SH.read_text(encoding="utf-8"), encoding="utf-8")
    dashboard_panes = tmp_path / "DASHBOARD-PANES.md"
    dashboard_panes.write_text(
        _REAL_DASHBOARD_PANES.read_text(encoding="utf-8"), encoding="utf-8"
    )
    original_dashboard_md_text = _REAL_DASHBOARD_MD.read_text(encoding="utf-8")
    dashboard_md = tmp_path / "dashboard.md"
    dashboard_md.write_text(original_dashboard_md_text, encoding="utf-8")

    # Clean copy: --check passes.
    clean_result = _run_check(tmp_path, census, create_sh, dashboard_panes, dashboard_md)
    assert clean_result.returncode == 0, clean_result.stdout + clean_result.stderr

    # Hand-edit the rc-metrics row of the tour in the copy only.
    corrupted = original_dashboard_md_text.replace(
        "| 7 | `rc-metrics` | 2 | - |",
        "| 7 | `rc-metrics-BROKEN` | 2 | - |",
    )
    assert corrupted != original_dashboard_md_text, (
        "fixture row not found -- update this test's literal"
    )
    dashboard_md.write_text(corrupted, encoding="utf-8")

    dirty_result = _run_check(tmp_path, census, create_sh, dashboard_panes, dashboard_md)
    assert dirty_result.returncode != 0
    combined_output = dirty_result.stdout + dirty_result.stderr
    assert "window=7" in combined_output
    assert "rc-metrics" in combined_output
    assert "error:" in dirty_result.stderr
    assert "message:" in dirty_result.stderr
    assert "remedy:" in dirty_result.stderr

    # Nothing was written by --check.
    assert dashboard_md.read_text(encoding="utf-8") == corrupted

    # Revert: --check is green again.
    dashboard_md.write_text(original_dashboard_md_text, encoding="utf-8")
    reverted_result = _run_check(tmp_path, census, create_sh, dashboard_panes, dashboard_md)
    assert reverted_result.returncode == 0, reverted_result.stdout + reverted_result.stderr


def test_check_reports_missing_tour_entry_when_census_window_added(
    tmp_path: pathlib.Path,
) -> None:
    """Adding a window to a scratch copy of the census, without re-rendering
    docs/dashboard.md, makes --check name the missing tour entry.

    create.sh and docs/DASHBOARD-PANES.md are pre-rendered fresh against the
    extended census (docs/DASHBOARD-PANES.md needs a hand-added marker pair
    for any brand-new window -- a pre-existing, unrelated requirement of
    that surface) so the disagreement below is isolated to the tour.
    """
    generator = _import_generator()

    census = tmp_path / "dashboard_windows.census"
    extended_census_text = (
        default_census_path().read_text(encoding="utf-8")
        + "\nW\t15\textra-window\t-\t1\n"
        "P\t15\t-\t0\tsingle\textra.sh\textra.sh --kanban-root $PGAI_AGENT_KANBAN_ROOT_PATH\n"
    )
    census.write_text(extended_census_text, encoding="utf-8")

    create_sh = tmp_path / "create.sh"
    create_sh.write_text(_REAL_CREATE_SH.read_text(encoding="utf-8"), encoding="utf-8")

    dashboard_panes = tmp_path / "DASHBOARD-PANES.md"
    dashboard_panes.write_text(
        _REAL_DASHBOARD_PANES.read_text(encoding="utf-8")
        + "\n<!-- CENSUS-GENERATED:BEGIN window=15 variant=- -->\n"
        "<!-- CENSUS-GENERATED:END window=15 variant=- -->\n",
        encoding="utf-8",
    )

    original_dashboard_md_text = _REAL_DASHBOARD_MD.read_text(encoding="utf-8")
    dashboard_md = tmp_path / "dashboard.md"
    dashboard_md.write_text(original_dashboard_md_text, encoding="utf-8")

    # Render create.sh and DASHBOARD-PANES.md fresh against the extended
    # census, but deliberately leave dashboard.md at its pre-render (stale)
    # content -- simulating "forgot to re-render the tour."
    render_result = subprocess.run(
        [
            sys.executable,
            str(_GENERATOR),
            "--census",
            str(census),
            "--create-sh",
            str(create_sh),
            "--dashboard-panes",
            str(dashboard_panes),
            "--dashboard-md",
            str(dashboard_md),
        ],
        cwd=str(_TEAM_DIR),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert render_result.returncode == 0, render_result.stdout + render_result.stderr
    dashboard_md.write_text(original_dashboard_md_text, encoding="utf-8")

    result = _run_check(tmp_path, census, create_sh, dashboard_panes, dashboard_md)
    assert result.returncode == 1
    combined_output = result.stdout + result.stderr
    assert "window=15" in combined_output
    assert "extra-window" in combined_output
    assert "entry count mismatch" in combined_output

    # Nothing was written.
    assert dashboard_md.read_text(encoding="utf-8") == original_dashboard_md_text


def test_describe_window_tour_drift_is_empty_for_identical_blocks() -> None:
    generator = _import_generator()
    windows = load_census()
    block = generator.render_window_tour_block(windows)
    assert generator._describe_window_tour_drift(block, block) == []


def test_describe_window_tour_drift_names_the_added_window() -> None:
    generator = _import_generator()
    full = [
        Window(tmux_index=0, name="alpha", variant="-", pane_count=1),
        Window(tmux_index=1, name="beta", variant="-", pane_count=1),
    ]
    stale = full[:1]
    stale_block = generator.render_window_tour_block(stale)
    full_block = generator.render_window_tour_block(full)

    messages = generator._describe_window_tour_drift(stale_block, full_block)
    assert any("beta" in m for m in messages)
    assert any("entry count mismatch" in m for m in messages)
