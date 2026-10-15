#!/usr/bin/env python3
"""
generate_window_docs.py
========================
Renders the dashboard window/pane surfaces that must never disagree with
`team/config/dashboard_windows.census` — the machine-readable single
source of truth for the tmux dashboard's layout — and, in `--check` mode,
refuses when a rendered surface has drifted from what's on disk.

`team/scripts/dashboard/create.sh` is what actually BUILDS the tmux
session. This script does not touch create.sh's functional tmux calls; it
only keeps three DERIVED, human-facing surfaces in sync with the census
that describes what create.sh builds:

  (a) create.sh's "Window order:" comment block (between the
      `# CENSUS-GENERATED:BEGIN window-order` / `...END` marker pair).
  (b) The per-window pane tables in docs/DASHBOARD-PANES.md (between
      `<!-- CENSUS-GENERATED:BEGIN window=N variant=V -->` /
      `<!-- ...END window=N variant=V -->` marker pairs, one pair per
      window/variant the census declares).
  (c) The window-by-window tour in docs/dashboard.md (between the
      `<!-- CENSUS-GENERATED:BEGIN window-tour -->` / `<!-- ...END
      window-tour -->` marker pair) -- one entry per census W row, in
      census order, naming each window's id, name, pane count and
      variant, and pointing at docs/DASHBOARD-PANES.md for the per-pane
      detail. The tour orients; it does not repeat the pane tables.

All three surfaces are regenerated from `team/pgai_agent_kanban.dashboard.
window_census.load_census()` — the one Python reader for the census file.
Neither this script nor any downstream consumer hand-parses the census a
second time.

Usage:
    python3 generate_window_docs.py            # render: write all surfaces
    python3 generate_window_docs.py --check    # verify: report drift, don't write

Options:
    --check                 Compare rendered content against what's on disk;
                             print every disagreement and exit 1 without
                             writing anything. Exit 0 when everything
                             already matches.
    --census PATH           Census file. Default: team/config/dashboard_windows.census.
    --create-sh PATH        create.sh file to render/check. Default:
                             team/scripts/dashboard/create.sh.
    --dashboard-panes PATH  docs/DASHBOARD-PANES.md file to render/check.
                             Default: docs/DASHBOARD-PANES.md.
    --dashboard-md PATH     docs/dashboard.md file to render/check (the
                             window tour). Default: docs/dashboard.md.
    --help, -h              Show this help and exit.

Exit codes:
    0   Render succeeded, or --check found no drift.
    1   --check found one or more disagreements (CONVERT: documentation
        drift carries zero runtime-failure risk -- see the refusal site
        below for the full classification rationale).
    2   Usage error, missing/malformed census, or a target file missing
        its marker pair (a structural problem the render can't repair by
        itself).
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
import textwrap

# ---------------------------------------------------------------------------
# Dev-tree root resolution -- each tree finds itself.
# This file lives at team/scripts/dashboard/generate_window_docs.py.
# Three parent levels: dashboard/ -> scripts/ -> team/ -> project_root/
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent
_DEV_TREE_ROOT = _TEAM_DIR.parent

_team_str = str(_TEAM_DIR)
if _team_str not in sys.path:
    sys.path.insert(0, _team_str)

from pgai_agent_kanban.dashboard.window_census import (  # noqa: E402
    CensusFormatError,
    Window,
    default_census_path,
    load_census,
)
from pgai_agent_kanban.lib.error_contract import emit_error_contract  # noqa: E402

_DEFAULT_CREATE_SH = _TEAM_DIR / "scripts" / "dashboard" / "create.sh"
_DEFAULT_DASHBOARD_PANES = _DEV_TREE_ROOT / "docs" / "DASHBOARD-PANES.md"
_DEFAULT_DASHBOARD_MD = _DEV_TREE_ROOT / "docs" / "dashboard.md"

_WINDOW_ORDER_BEGIN = "# CENSUS-GENERATED:BEGIN window-order"
_WINDOW_ORDER_END = "# CENSUS-GENERATED:END window-order"

_WINDOW_TOUR_BEGIN = "<!-- CENSUS-GENERATED:BEGIN window-tour -->"
_WINDOW_TOUR_END = "<!-- CENSUS-GENERATED:END window-tour -->"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_window_order_block(windows: list[Window]) -> str:
    """Render create.sh's "Window order:" comment block (marker to marker).

    Args:
        windows: Census windows in file order (as returned by load_census()).

    Returns:
        The full text to place between the BEGIN/END markers, including the
        markers themselves, each line already carrying its own leading "#".
    """
    names: list[str] = []
    seen_indices: set[int] = set()
    for w in windows:
        if w.tmux_index in seen_indices:
            continue  # a second variant of the same window (e.g. drill-N) doesn't repeat
        seen_indices.add(w.tmux_index)
        if w.name == "drill-N":
            names.append("drill-1, drill-2, ...")
        else:
            names.append(w.name)

    body = ", ".join(names)
    wrapped_lines = textwrap.wrap(body, width=66)

    lines = [_WINDOW_ORDER_BEGIN]
    lines.append(
        "# generated by team/scripts/dashboard/generate_window_docs.py from"
    )
    lines.append(
        "# team/config/dashboard_windows.census. Do not hand-edit between"
    )
    lines.append(
        "# these markers; run 'python3 team/scripts/dashboard/"
        "generate_window_docs.py'"
    )
    lines.append("# (from the repo root) and commit the result instead.")
    for i, wrapped_line in enumerate(wrapped_lines):
        prefix = "# Window order: " if i == 0 else "#               "
        lines.append(prefix + wrapped_line)
    lines.append(_WINDOW_ORDER_END)
    return "\n".join(lines)


def render_window_tour_block(windows: list[Window]) -> str:
    """Render docs/dashboard.md's window-by-window tour (marker to marker).

    One row per census W row, in census order -- drill-N's two variants
    each get their own row, since each is a distinct W record with its
    own pane_count, and the acceptance bar for this surface is that it
    reconciles line for line with the census W rows. The table names each
    window's id, name, pane count and variant and points at
    docs/DASHBOARD-PANES.md for the per-pane detail; it does not repeat
    the pane tables themselves.

    Args:
        windows: Census windows in file order (as returned by load_census()).

    Returns:
        The full text to place between the BEGIN/END markers, including the
        markers themselves.
    """
    lines = [_WINDOW_TOUR_BEGIN]
    lines.append(
        "<!-- generated by team/scripts/dashboard/generate_window_docs.py"
    )
    lines.append(
        "     from team/config/dashboard_windows.census. Do not hand-edit"
    )
    lines.append(
        "     between these markers; run 'python3 team/scripts/dashboard/"
    )
    lines.append(
        "     generate_window_docs.py' (from the repo root) and commit the"
    )
    lines.append(
        "     result instead. Per-pane detail lives in DASHBOARD-PANES.md;"
    )
    lines.append("     this list orients, it does not repeat that table. -->")
    lines.append("")
    lines.append("| Window | Name | Panes | Variant |")
    lines.append("|---|---|---|---|")
    for w in windows:
        lines.append(f"| {w.tmux_index} | `{w.name}` | {w.pane_count} | {w.variant} |")
    lines.append("")
    lines.append(_WINDOW_TOUR_END)
    return "\n".join(lines)


def _md_cell(text: str) -> str:
    """Render one table-cell value, wrapping in backticks unless it's an
    "N/A" note or a parenthesized non-script note (matching the existing
    docs/DASHBOARD-PANES.md convention where prose notes are plain text and
    real script names / commands are code-formatted).
    """
    if text.startswith("N/A") or text.startswith("("):
        return text
    return f"`{text}`"


def render_pane_table(window: Window) -> str:
    """Render one window's pane table as GitHub-flavored markdown.

    Returns:
        A 3-column pipe table: "Pane position | Populating script |
        Standalone command", one row per pane in pane_seq order (the order
        panes already appear in the census, which is pane_seq order by
        construction of the census file).
    """
    lines = ["| Pane position | Populating script | Standalone command |"]
    lines.append("|---|---|---|")
    for pane in window.panes:
        lines.append(
            f"| {pane.position} | {_md_cell(pane.script)} | {_md_cell(pane.standalone_cmd)} |"
        )
    return "\n".join(lines)


def render_dashboard_panes_marker_block(window: Window) -> str:
    """Render one <!-- CENSUS-GENERATED:BEGIN ... --> ... END --> block."""
    begin = f"<!-- CENSUS-GENERATED:BEGIN window={window.tmux_index} variant={window.variant} -->"
    end = f"<!-- CENSUS-GENERATED:END window={window.tmux_index} variant={window.variant} -->"
    return "\n".join([begin, render_pane_table(window), end])


# ---------------------------------------------------------------------------
# Marker-pair replace/extract (shared by both target files)
# ---------------------------------------------------------------------------


class MarkerNotFoundError(ValueError):
    """A required BEGIN/END marker pair is missing from a target file."""


def _replace_between_markers(
    content: str, begin_marker: str, end_marker: str, new_inner: str
) -> str:
    """Return *content* with the text between begin_marker and end_marker
    (both inclusive) replaced by *new_inner* (which itself carries its own
    begin/end marker lines).

    Raises:
        MarkerNotFoundError: begin_marker or end_marker is not present.
    """
    begin_idx = content.find(begin_marker)
    if begin_idx == -1:
        raise MarkerNotFoundError(f"marker not found: {begin_marker!r}")
    end_idx = content.find(end_marker, begin_idx)
    if end_idx == -1:
        raise MarkerNotFoundError(f"marker not found: {end_marker!r}")
    end_idx += len(end_marker)
    return content[:begin_idx] + new_inner + content[end_idx:]


def _extract_between_markers(content: str, begin_marker: str, end_marker: str) -> str:
    """Return the text between begin_marker and end_marker, both inclusive.

    Raises:
        MarkerNotFoundError: begin_marker or end_marker is not present.
    """
    begin_idx = content.find(begin_marker)
    if begin_idx == -1:
        raise MarkerNotFoundError(f"marker not found: {begin_marker!r}")
    end_idx = content.find(end_marker, begin_idx)
    if end_idx == -1:
        raise MarkerNotFoundError(f"marker not found: {end_marker!r}")
    end_idx += len(end_marker)
    return content[begin_idx:end_idx]


# ---------------------------------------------------------------------------
# Per-target render/check
# ---------------------------------------------------------------------------


def _render_create_sh(windows: list[Window], create_sh_path: pathlib.Path) -> tuple[str, str]:
    """Return (current_content, rendered_content) for create.sh.

    Raises:
        MarkerNotFoundError: the marker pair is absent from create_sh_path.
    """
    current = create_sh_path.read_text(encoding="utf-8")
    new_block = render_window_order_block(windows)
    rendered = _replace_between_markers(
        current, _WINDOW_ORDER_BEGIN, _WINDOW_ORDER_END, new_block
    )
    return current, rendered


def _render_dashboard_panes(
    windows: list[Window], dashboard_panes_path: pathlib.Path
) -> tuple[str, str]:
    """Return (current_content, rendered_content) for docs/DASHBOARD-PANES.md.

    Raises:
        MarkerNotFoundError: a window/variant's marker pair is absent.
    """
    current = dashboard_panes_path.read_text(encoding="utf-8")
    rendered = current
    for window in windows:
        begin = f"<!-- CENSUS-GENERATED:BEGIN window={window.tmux_index} variant={window.variant} -->"
        end = f"<!-- CENSUS-GENERATED:END window={window.tmux_index} variant={window.variant} -->"
        new_block = render_dashboard_panes_marker_block(window)
        try:
            rendered = _replace_between_markers(rendered, begin, end, new_block)
        except MarkerNotFoundError:
            raise MarkerNotFoundError(
                f"docs/DASHBOARD-PANES.md is missing the marker pair for "
                f"window={window.tmux_index} name={window.name!r} "
                f"variant={window.variant!r} ({begin!r} / {end!r}); add the "
                "marker pair (see an existing window's section for the "
                "shape) and re-run."
            ) from None
    return current, rendered


def _render_dashboard_md(
    windows: list[Window], dashboard_md_path: pathlib.Path
) -> tuple[str, str]:
    """Return (current_content, rendered_content) for docs/dashboard.md.

    Raises:
        MarkerNotFoundError: the window-tour marker pair is absent.
    """
    current = dashboard_md_path.read_text(encoding="utf-8")
    new_block = render_window_tour_block(windows)
    try:
        rendered = _replace_between_markers(
            current, _WINDOW_TOUR_BEGIN, _WINDOW_TOUR_END, new_block
        )
    except MarkerNotFoundError:
        raise MarkerNotFoundError(
            f"docs/dashboard.md is missing the window-tour marker pair "
            f"({_WINDOW_TOUR_BEGIN!r} / {_WINDOW_TOUR_END!r}); add the "
            "marker pair and re-run."
        ) from None
    return current, rendered


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="generate_window_docs.py",
        description=(
            "Render (or, with --check, verify) the dashboard window-order "
            "comment in create.sh and the per-window pane tables in "
            "docs/DASHBOARD-PANES.md from team/config/dashboard_windows.census."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify only -- report drift and exit non-zero, write nothing.",
    )
    parser.add_argument("--census", metavar="PATH", help="Census file path.")
    parser.add_argument("--create-sh", metavar="PATH", help="create.sh path.")
    parser.add_argument(
        "--dashboard-panes", metavar="PATH", help="docs/DASHBOARD-PANES.md path."
    )
    parser.add_argument(
        "--dashboard-md",
        metavar="PATH",
        help="docs/dashboard.md path (the window tour).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the dashboard window-docs generator: render, or with ``--check`` compare.

    Exit codes: 0 rendered, or ``--check`` found no drift; 1 ``--check`` found
    one or more disagreements (printed; nothing written); 2 usage error, a
    missing or malformed census, or a target file missing its markers.
    """
    args = _parse_args(argv)

    census_path = pathlib.Path(args.census) if args.census else default_census_path()
    create_sh_path = pathlib.Path(args.create_sh) if args.create_sh else _DEFAULT_CREATE_SH
    dashboard_panes_path = (
        pathlib.Path(args.dashboard_panes) if args.dashboard_panes else _DEFAULT_DASHBOARD_PANES
    )
    dashboard_md_path = (
        pathlib.Path(args.dashboard_md) if args.dashboard_md else _DEFAULT_DASHBOARD_MD
    )

    try:
        windows = load_census(census_path)
    except FileNotFoundError:
        print(f"generate_window_docs: census file not found: {census_path}", file=sys.stderr)
        return 2
    except CensusFormatError as exc:
        print(f"generate_window_docs: malformed census: {exc}", file=sys.stderr)
        return 2

    try:
        create_sh_current, create_sh_rendered = _render_create_sh(windows, create_sh_path)
        panes_current, panes_rendered = _render_dashboard_panes(windows, dashboard_panes_path)
        dashboard_md_current, dashboard_md_rendered = _render_dashboard_md(
            windows, dashboard_md_path
        )
    except MarkerNotFoundError as exc:
        print(f"generate_window_docs: {exc}", file=sys.stderr)
        return 2

    if not args.check:
        if create_sh_rendered != create_sh_current:
            create_sh_path.write_text(create_sh_rendered, encoding="utf-8")
            print(f"generate_window_docs: wrote {create_sh_path}")
        else:
            print(f"generate_window_docs: {create_sh_path} already up to date")
        if panes_rendered != panes_current:
            dashboard_panes_path.write_text(panes_rendered, encoding="utf-8")
            print(f"generate_window_docs: wrote {dashboard_panes_path}")
        else:
            print(f"generate_window_docs: {dashboard_panes_path} already up to date")
        if dashboard_md_rendered != dashboard_md_current:
            dashboard_md_path.write_text(dashboard_md_rendered, encoding="utf-8")
            print(f"generate_window_docs: wrote {dashboard_md_path}")
        else:
            print(f"generate_window_docs: {dashboard_md_path} already up to date")
        return 0

    # --check mode: report every disagreement, write nothing.
    disagreements: list[str] = []
    if create_sh_rendered != create_sh_current:
        disagreements.append(_diff_summary("create.sh", create_sh_path, create_sh_current, create_sh_rendered))
    for window in windows:
        begin = f"<!-- CENSUS-GENERATED:BEGIN window={window.tmux_index} variant={window.variant} -->"
        end = f"<!-- CENSUS-GENERATED:END window={window.tmux_index} variant={window.variant} -->"
        current_block = _extract_between_markers(panes_current, begin, end)
        rendered_block = _extract_between_markers(panes_rendered, begin, end)
        if current_block != rendered_block:
            disagreements.append(
                f"docs/DASHBOARD-PANES.md: window={window.tmux_index} "
                f"name={window.name!r} variant={window.variant!r} table "
                "disagrees with the census"
            )
    current_tour = _extract_between_markers(
        dashboard_md_current, _WINDOW_TOUR_BEGIN, _WINDOW_TOUR_END
    )
    rendered_tour = _extract_between_markers(
        dashboard_md_rendered, _WINDOW_TOUR_BEGIN, _WINDOW_TOUR_END
    )
    disagreements.extend(_describe_window_tour_drift(current_tour, rendered_tour))

    if not disagreements:
        print("generate_window_docs --check: OK -- all surfaces match the census.")
        return 0

    for d in disagreements:
        print(f"generate_window_docs --check: DRIFT -- {d}", file=sys.stderr)

    # CONVERT: documentation-generation drift between the census and its
    # rendered surfaces (create.sh's window-order comment, docs/
    # DASHBOARD-PANES.md's pane tables) carries zero runtime-failure risk --
    # the tmux session create.sh builds is unaffected either way. This
    # refusal only ever stops a quality-class pre-flight lint
    # (gated_runner_run_quality_lint in team/scripts/run-unit-tests.sh),
    # never the runner itself; see team/SOP-core.md "Gates and Refusals".
    emit_error_contract(
        "dashboard.window-docs-drift",
        (
            f"{len(disagreements)} dashboard doc surface(s) disagree with "
            f"team/config/dashboard_windows.census: "
            + "; ".join(disagreements)
        ),
        "run 'python3 team/scripts/dashboard/generate_window_docs.py' from "
        "the repo root and commit the result (or, if create.sh's actual "
        "layout changed, update team/config/dashboard_windows.census first, "
        "then regenerate).",
    )
    # CENSUS-ANCHOR: generate-window-docs-check-drift-01
    sys.exit(1)


_TOUR_ROW_RE = re.compile(
    r"^\|\s*(\d+)\s*\|\s*`([^`]+)`\s*\|\s*(\d+)\s*\|\s*(\S+)\s*\|$"
)


def _describe_window_tour_drift(current_tour: str, rendered_tour: str) -> list[str]:
    """Return one message per differing line between the two window-tour
    blocks, naming the window a differing table row belongs to whenever the
    rendered (census-derived) line still matches the row shape.
    """
    current_lines = current_tour.splitlines()
    rendered_lines = rendered_tour.splitlines()
    messages: list[str] = []
    for i, rendered_line in enumerate(rendered_lines):
        current_line = current_lines[i] if i < len(current_lines) else None
        if current_line == rendered_line:
            continue
        match = _TOUR_ROW_RE.match(rendered_line)
        if match:
            tmux_index, name, _pane_count, variant = match.groups()
            messages.append(
                f"docs/dashboard.md: window tour entry for window={tmux_index} "
                f"name={name!r} variant={variant!r} disagrees with the census"
            )
        else:
            messages.append(
                "docs/dashboard.md: window tour disagrees with the census"
            )
    if len(current_lines) != len(rendered_lines):
        messages.append(
            "docs/dashboard.md: window tour disagrees with the census "
            "(entry count mismatch -- a window was likely added to or "
            "removed from the census without re-rendering)"
        )
    return messages


def _diff_summary(
    label: str, path: pathlib.Path, current: str, rendered: str
) -> str:
    """One-line summary naming which marker block in *path* disagrees."""
    return f"{label} ({path}): window-order comment disagrees with the census"


if __name__ == "__main__":
    sys.exit(main())
