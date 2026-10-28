"""
pgai_agent_kanban.dashboard.column_render.board_pane — one mixed-project
board column, resolved and rendered in this process.

:func:`render_board_pane` returns the lines

    column-render.sh <input|queue> none --kanban-root <root> --all-projects --column-type <column>

prints for the same tree and environment (no ``--label``, the default
width), without starting a process or writing a file.  The script resolves
its render settings in bash before it calls this package; here each one is
resolved by the Python twin of the bash step that sets it on the board
column path (``column_render_board_serve.sh``):

    bash step                              resolved here by
    -------------------------------------  ------------------------------------------
    column_render_config_color_mode        lib.metrics.color_enabled
    column_render_config_ansi (ansi_code)  lib.palette.ansi_code
    (the needs-operator colour)            lib.palette.resolve_semantic_color, as the
                                           column_render command line reads it
    column_render_config_max_rows          ops.projects.dashboard_default_max_rows
                                           (DASHBOARD_MAX_ROWS, else kanban.cfg
                                           [dashboard] max_rows, else 20)
    column_render_config_status_glyph      DASHBOARD_STATUS_GLYPH, else kanban.cfg
                                           [dashboard] dashboard_status_glyph, else "■"
    column_render_config_layout            kanban.cfg [dashboard] min/max_rows_per_
                                           column/project (defaults 13, 34, 3, 8)
    _column_render_multi_width             38 (input) or 22 (queue)
    status_glyphs.sh                       GLYPH_DEFERRED_BUG / GLYPH_DEFER_WARNING,
                                           read from this tree's own
                                           scripts/lib/status_glyphs.sh

The per-project sources come from :mod:`board_gather`, gathered for the
requested column alone, and the column is drawn by
:func:`~pgai_agent_kanban.dashboard.column_render.multi.render_multi_project_column`
-- the gather and renderer the script's board path runs.  No snapshot and no
pre-rendered column is read or written: every call gathers afresh.

Failures
--------
A tree :mod:`board_gather` does not read raises
:class:`~pgai_agent_kanban.dashboard.column_render.board_gather.BoardGatherUnavailable`;
any other error while gathering or rendering propagates as raised.  There
is no fallback: the caller reports the failure.

Processes
---------
None for the bugs, priorities and queue columns.  The requirements column
reads each project's last release through ``lib.released_version``, which
runs a local ``git`` read when the project's ``dev_tree_path`` is a git
repository -- the same read the script makes there.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import re
from typing import Mapping, Optional

from pgai_agent_kanban.dashboard.column_render import board_gather
from pgai_agent_kanban.dashboard.column_render.cells import NEEDS_OPERATOR_PALETTE_KEY, Palette
from pgai_agent_kanban.dashboard.column_render.multi import (
    DEFAULT_STATUS_GLYPH,
    Layout,
    render_multi_project_column,
)
from pgai_agent_kanban.lib import ini
from pgai_agent_kanban.lib.metrics import color_enabled
from pgai_agent_kanban.lib.palette import ansi_code, resolve_semantic_color
from pgai_agent_kanban.ops.projects import dashboard_default_max_rows

__all__ = [
    "PaneSettings",
    "STATUS_GLYPHS_FILE",
    "default_width",
    "read_deferral_glyphs",
    "render_board_pane",
    "resolve_pane_settings",
]

#: The bash glyph map this tree's own scripts/ tree carries (``status_glyphs.sh``).
STATUS_GLYPHS_FILE = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "lib" / "status_glyphs.sh"

#: Pane width when the script is given none (``_column_render_multi_width``).
_DEFAULT_WIDTH = {"input": 38, "queue": 22}

_POSITIVE_INT_RE = re.compile(r"[0-9]+")

#: ``NAME="value"`` assignment lines in status_glyphs.sh (whitespace tolerated).
_GLYPH_ASSIGNMENT_RE = re.compile(r'^\s*(GLYPH_[A-Z_]+)\s*=\s*"([^"]*)"\s*$', re.MULTILINE)


@dataclasses.dataclass(frozen=True)
class PaneSettings:
    """What ``column-render.sh`` resolves before it draws a board column."""

    palette: Palette
    width: int
    layout: Layout
    status_glyph: str
    dashboard_max_rows: str
    glyph_deferred_bug: str
    glyph_defer_warning: str


def default_width(subcommand: str) -> int:
    """The width the script draws *subcommand*'s column at when it is given none."""
    return _DEFAULT_WIDTH.get(subcommand, _DEFAULT_WIDTH["input"])


def _cfg_value(kanban_cfg: pathlib.Path, key: str, default: str) -> str:
    """``read_ini <kanban.cfg> dashboard <key> <default>``."""
    value = ini.read_line_value(kanban_cfg, "dashboard", key)
    return default if value is None else value


def _layout_value(kanban_cfg: pathlib.Path, key: str, default: int) -> int:
    """A ``[dashboard]`` layout value; anything but a positive integer reads as *default*."""
    raw = _cfg_value(kanban_cfg, key, str(default))
    if _POSITIVE_INT_RE.fullmatch(raw) and int(raw) > 0:
        return int(raw)
    return default


def _layout(kanban_cfg: pathlib.Path) -> Layout:
    """``column_render_config_layout``: the row-allocation values from kanban.cfg."""
    defaults = Layout()
    return Layout(
        **{
            field.name: _layout_value(kanban_cfg, field.name, getattr(defaults, field.name))
            for field in dataclasses.fields(Layout)
        }
    )


def _status_glyph(kanban_cfg: pathlib.Path, env: Mapping[str, str]) -> str:
    """``column_render_config_status_glyph``: environment, then kanban.cfg, then the default."""
    value = env.get("DASHBOARD_STATUS_GLYPH", "")
    if not value:
        value = _cfg_value(kanban_cfg, "dashboard_status_glyph", DEFAULT_STATUS_GLYPH) or DEFAULT_STATUS_GLYPH
    return value


def read_deferral_glyphs(path: pathlib.Path = STATUS_GLYPHS_FILE) -> tuple:
    """Return ``(GLYPH_DEFERRED_BUG, GLYPH_DEFER_WARNING)`` as *path* assigns them.

    status_glyphs.sh is the single home of the dashboard's glyph literals;
    it is read here as data rather than copied.  Raises ``OSError`` when the
    file cannot be read and ``ValueError`` when it assigns neither name.
    """
    text = path.read_text(encoding="utf-8")
    values = dict(_GLYPH_ASSIGNMENT_RE.findall(text))
    if "GLYPH_DEFERRED_BUG" not in values and "GLYPH_DEFER_WARNING" not in values:
        raise ValueError(f"{path} assigns neither GLYPH_DEFERRED_BUG nor GLYPH_DEFER_WARNING")
    return values.get("GLYPH_DEFERRED_BUG", ""), values.get("GLYPH_DEFER_WARNING", "")


def resolve_pane_settings(
    kanban_root: "pathlib.Path | str",
    subcommand: str,
    environ: Optional[Mapping[str, str]] = None,
) -> PaneSettings:
    """Resolve a board column's render settings as the script's board path does.

    *environ* is the environment the script would have run under (default:
    this process's).  Raises ``OSError`` / ``ValueError`` from
    :func:`read_deferral_glyphs`.
    """
    env = os.environ if environ is None else environ
    kanban_cfg = pathlib.Path(kanban_root) / "kanban.cfg"
    use_color = color_enabled(False, env)
    palette = Palette(
        use_color,
        *(ansi_code(name, use_color) for name in ("red", "yellow", "green", "white", "dim", "reset")),
        needs_operator=resolve_semantic_color(NEEDS_OPERATOR_PALETTE_KEY, use_color),
    )
    glyph_deferred_bug, glyph_defer_warning = read_deferral_glyphs()
    return PaneSettings(
        palette=palette,
        width=default_width(subcommand),
        layout=_layout(kanban_cfg),
        status_glyph=_status_glyph(kanban_cfg, env),
        dashboard_max_rows=dashboard_default_max_rows(kanban_root, env),
        glyph_deferred_bug=glyph_deferred_bug,
        glyph_defer_warning=glyph_defer_warning,
    )


def render_board_pane(
    kanban_root: "pathlib.Path | str",
    *,
    subcommand: str,
    column_type: str,
    environ: Optional[Mapping[str, str]] = None,
) -> list:
    """Return the lines of the mixed-project *column_type* column for *kanban_root*.

    *subcommand* is ``input`` or ``queue``, the kind *column_type* belongs
    to (``board_snapshot.BOARD_COLUMNS``).  Raises
    ``board_gather.BoardGatherUnavailable`` for a tree the in-process gather
    does not read, and whatever else gathering or rendering raises; see the
    module docstring.
    """
    settings = resolve_pane_settings(kanban_root, subcommand, environ)
    sources = board_gather.gather_board_sources(
        kanban_root,
        dashboard_max_rows=settings.dashboard_max_rows,
        environ={
            "GLYPH_DEFERRED_BUG": settings.glyph_deferred_bug,
            "GLYPH_DEFER_WARNING": settings.glyph_defer_warning,
        },
        column_types=(column_type,),
    )
    return render_multi_project_column(
        sources[column_type],
        subcommand=subcommand,
        column_type=column_type,
        width=settings.width,
        palette=settings.palette,
        layout=settings.layout,
        status_glyph=settings.status_glyph,
    )
