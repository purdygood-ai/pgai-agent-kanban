#!/usr/bin/env python3
"""
legend.py — the visibility window's one-line colour legend.

``legend_line()`` assembles the line ``team/scripts/dashboard/legend-render.sh``
prints by substituting two blocks into ``DASHBOARD_LEGEND_TEMPLATE``
(``team/scripts/lib/dashboard_legend.sh``):

  * ``{PROJECTS_BLOCK}`` — one ``<tag> <name>`` pair per project, joined by
    two spaces, or ``(no projects)``.  In colour the tag is a filled square
    in the project's ``#RRGGBB`` colour (truecolor); without colour it is
    the first four characters of the name in brackets (``[alph]``).
  * ``{STATUS_BLOCK}`` — the status keywords joined by two spaces, each in
    its status colour when colour is on: ``running``; ``done``, ``wont-do``
    and ``duplicate``; ``blocked``; ``needs-operator``; anything else as
    ``open``.  The colours come from the semantic palette's ``status_*``
    keys in ``config/default_palette.txt``.  ``carried`` is drawn as the
    marker a carried row carries (``cells.status_marker``): ``↻ carried``
    in colour, ``[carried]`` without.

A project without a colour of its own takes the palette's last entry, the
documented last-resort gray (``#888780`` when the palette file cannot be
read).  An empty template reads as the canonical
``PROJECT: {PROJECTS_BLOCK}   |   STATUS: {STATUS_BLOCK}``, and an empty
keyword list as ``open running done blocked needs-operator carried``.

Usage (CLI):
    python3 -m pgai_agent_kanban.dashboard.legend [--use-color true|false]
        [--template TEXT] [--status-keywords TEXT]
        [--names TEXT] [--colors TEXT]

    --use-color        "true" (any case) turns colour on; default false.
    --template         the legend template; default "" (the canonical one).
    --status-keywords  space-separated keywords; default "" (the canonical six).
    --names, --colors  newline-separated project names and their colours,
                       in the same order; blank lines are skipped.

    Prints the legend line and exits 0; ``--help`` prints usage and exits 0.

Public API:
    legend_line(use_color, project_names, project_colors, template="",
                status_keywords=()) -> str
    main(argv=None) -> int
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from collections.abc import Sequence

# Run by file path (``python3 .../legend.py``) or loaded from a file outside
# its package, this module has no parent package and the interpreter puts
# only its own directory on sys.path, so the package imports below would not
# resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# imports resolve from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.board import (  # noqa: E402
    CARRIED_STATUS,
    DUPLICATE_STATUS,
    NEEDS_OPERATOR_STATUS,
    WONT_DO_STATUS,
)
from pgai_agent_kanban.dashboard.column_render.cells import (  # noqa: E402
    CARRIED_GLYPH_MARKER,
    CARRIED_TEXT_MARKER,
    NEEDS_OPERATOR_PALETTE_KEY,
    Palette,
)
from pgai_agent_kanban.dashboard.column_render.multi import _project_tag_color  # noqa: E402
from pgai_agent_kanban.lib.palette import (  # noqa: E402
    PaletteFileError,
    ansi_code,
    load_default_palette,
    resolve_semantic_color,
)

__all__ = [
    "DEFAULT_STATUS_KEYWORDS",
    "DEFAULT_TEMPLATE",
    "PROJECT_TAG_GLYPH",
    "legend_line",
    "main",
]

#: The template used when the caller passes an empty one; the same string as
#: ``DASHBOARD_LEGEND_TEMPLATE`` in team/scripts/lib/dashboard_legend.sh.
DEFAULT_TEMPLATE = "PROJECT: {PROJECTS_BLOCK}   |   STATUS: {STATUS_BLOCK}"

#: The keywords used when the caller passes none.
DEFAULT_STATUS_KEYWORDS: tuple[str, ...] = (
    "open",
    "running",
    "done",
    "blocked",
    NEEDS_OPERATOR_STATUS,
    CARRIED_STATUS,
)

#: The project tag in colour: a filled square.
PROJECT_TAG_GLYPH = "■"

#: The gray a colourless project takes when the palette file cannot be read.
_GRAY_SENTINEL = "#888780"


def _status_color(status: str, use_color: bool) -> str:
    """Return the escape for a status keyword, "" when colour is off."""
    if not use_color:
        return ""
    if status == "running":
        return resolve_semantic_color("status_running", use_color)
    if status in ("done", WONT_DO_STATUS, DUPLICATE_STATUS):
        return resolve_semantic_color("status_done", use_color)
    if status == "blocked":
        return resolve_semantic_color("status_blocked", use_color)
    if status == NEEDS_OPERATOR_STATUS:
        return resolve_semantic_color(NEEDS_OPERATOR_PALETTE_KEY, use_color)
    return resolve_semantic_color("status_open", use_color)


def _status_entry(status: str, use_color: bool) -> str:
    """Return a status keyword's legend text: carried shows its row marker."""
    if status == CARRIED_STATUS:
        return f"{CARRIED_GLYPH_MARKER.strip()} {status}" if use_color else CARRIED_TEXT_MARKER.strip()
    return status


def legend_line(
    use_color: bool,
    project_names: Sequence[str],
    project_colors: Sequence[str],
    template: str = "",
    status_keywords: Sequence[str] = (),
) -> str:
    """Return the legend line, without a trailing newline.

    Args:
        use_color: Whether to draw the project tags and status keywords in
            colour.
        project_names: The projects, in legend order.
        project_colors: Each project's ``#RRGGBB`` colour, in the same
            order.  A shorter list is padded with the palette's last-resort
            gray; a colour that is not six hex digits draws its tag with no
            colour escape.
        template: The legend template with ``{PROJECTS_BLOCK}`` and
            ``{STATUS_BLOCK}`` placeholders; surrounding whitespace is
            stripped, and an empty template is ``DEFAULT_TEMPLATE``.
        status_keywords: The status keywords in order; empty is
            ``DEFAULT_STATUS_KEYWORDS``.

    Raises:
        pgai_agent_kanban.lib.palette.PaletteKeyError: Colour is on and the
            palette file declares no ``status_*`` key a keyword needs.
    """
    try:
        gray_fallback = load_default_palette()[-1]
    except PaletteFileError:
        gray_fallback = _GRAY_SENTINEL

    reset = ansi_code("reset", use_color)
    tag_palette = Palette(use_color=use_color)

    legend_template = template.strip() or DEFAULT_TEMPLATE
    keywords = list(status_keywords) or list(DEFAULT_STATUS_KEYWORDS)

    names = list(project_names)
    colors = list(project_colors)
    while len(colors) < len(names):
        colors.append(gray_fallback)

    project_parts = []
    for name, color in zip(names, colors):
        if use_color:
            tag = f"{_project_tag_color(color, tag_palette)}{PROJECT_TAG_GLYPH}{reset}"
        else:
            short = name[:4] if name else "????"
            tag = f"[{short}]"
        project_parts.append(f"{tag} {name}")
    projects_block = "  ".join(project_parts) if project_parts else "(no projects)"

    status_parts = []
    for keyword in keywords:
        entry = _status_entry(keyword, use_color)
        if use_color:
            status_parts.append(f"{_status_color(keyword, use_color)}{entry}{reset}")
        else:
            status_parts.append(entry)
    status_block = "  ".join(status_parts)

    return legend_template.replace("{PROJECTS_BLOCK}", projects_block).replace("{STATUS_BLOCK}", status_block)


def _nonblank_lines(text: str) -> list[str]:
    """Split newline-separated *text* into its non-empty lines."""
    return [line for line in text.splitlines() if line]


def main(argv: list[str] | None = None) -> int:
    """Print the legend line built from the command-line options (see the
    module docstring); exit 0."""
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.dashboard.legend",
        description="Print the visibility window's one-line colour legend.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--use-color",
        default="false",
        help='"true" (any case) draws the legend in colour (default: false)',
    )
    parser.add_argument(
        "--template",
        default="",
        help="legend template with {PROJECTS_BLOCK} and {STATUS_BLOCK} (default: the canonical one)",
    )
    parser.add_argument(
        "--status-keywords",
        default="",
        help="space-separated status keywords (default: open running done blocked needs-operator carried)",
    )
    parser.add_argument(
        "--names",
        default="",
        help="newline-separated project names, in legend order (default: none)",
    )
    parser.add_argument(
        "--colors",
        default="",
        help="newline-separated #RRGGBB colours, one per name (default: none)",
    )
    args = parser.parse_args(argv)

    print(
        legend_line(
            args.use_color.lower() == "true",
            _nonblank_lines(args.names),
            _nonblank_lines(args.colors),
            template=args.template,
            status_keywords=args.status_keywords.strip().split(),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
