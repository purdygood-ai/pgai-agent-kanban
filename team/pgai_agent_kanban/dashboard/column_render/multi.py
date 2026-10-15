"""
pgai_agent_kanban.dashboard.column_render.multi — the mixed-project board
column pane (``column-render.sh --all-projects``).

Every registered project gets its own block of rows, in registry order, so
each project keeps its share of the column whatever the others hold.  Each
row is prefixed with a project tag — the status glyph in the project's
``display_color``, or ``[<first four letters>] `` without colour — and its
text is coloured by the item's board status (``pgai_agent_kanban.board``,
the classifier ``GET /board`` uses).

Rows per project
----------------
:func:`compute_layout` sizes every project's block from the
``[dashboard]`` layout values: the largest per-project count between
``min_rows_per_project`` and ``max_rows_per_project`` for which all
projects fit in ``max_rows_per_column``, or ``min_rows_per_project`` when
none fits.

* Requirements: a project's items newest version first; when they exceed
  the block, the window is kept around the first active-RC item (or the
  newest items, with a "... (N more below)" marker, when none is active),
  with marker rows for hidden sides inside the block.
* Bugs and priorities: newest ID first, already narrowed to the project's
  ``max_rows`` by ``status_priority_cap`` (running and blocked rows are
  kept first); a project with more items than the block shows
  ``block - 1`` of them and a "... and N more" row.
* Agent queues: newest (date, sequence) first, narrowed by
  ``status_priority_cap``, then chosen by ``cells.middle_position_window``
  within the block.

A pane with no rows at all prints "(empty)".

Input contract
--------------
The bash gather stage (``team/scripts/lib/dashboard/column_render_data_multi.sh``)
resolves each project's paths and release values and exports them as
newline-separated lists, one line per project in registry order;
:func:`sources_from_env` reads them:

    _PROJ_NAMES_STR, _PROJ_COLORS_STR, _PROJ_MAX_ROWS_STR,
    _PROJ_INPUT_DIRS_STR, _PROJ_BACKLOG_FILES_STR, _PROJ_TASKS_DIRS_STR,
    _ALL_ACTIVE_RCS, _ALL_LAST_RELEASED, _PROJ_DEFER_INFO_STR,
    DFS_SEP, GLYPH_DEFERRED_BUG, GLYPH_DEFER_WARNING

Blank lines are dropped and short lists padded ("none", the palette's
last-resort grey for colours, 20 for max_rows), so a list is read by
position.  ``none`` marks a path or version the project does not have.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import re
from typing import Mapping, Optional

from pgai_agent_kanban.board import (
    classify_intake_status,
    classify_requirement,
    get_target_version_for_item,
    read_intake_status,
)
from pgai_agent_kanban.dashboard.column_render.cells import (
    DeferCards,
    Palette,
    active_window,
    compact_id,
    display_status,
    middle_position_window,
    status_color,
    truncate_ansi,
)
from pgai_agent_kanban.dashboard.column_render.single import parse_max_rows, queue_items
from pgai_agent_kanban.dashboard.defer_card_glyph import parse_proj_defer_info
from pgai_agent_kanban.dashboard.status_priority_cap import status_priority_cap
from pgai_agent_kanban.lib.palette import PaletteFileError, load_default_palette
from pgai_agent_kanban.lib.semver import parse_version

__all__ = [
    "DEFAULT_STATUS_GLYPH",
    "Layout",
    "ProjectSource",
    "compute_layout",
    "gather_project_entries",
    "render_multi_project_column",
    "render_project_entries",
    "sources_from_env",
]

#: The project-tag glyph when ``[dashboard] dashboard_status_glyph`` is unset.
DEFAULT_STATUS_GLYPH = "■"

# The last-resort grey when team/config/default_palette.txt cannot be read:
# the pane still renders rather than failing on a missing palette file.
_GRAY_SENTINEL = "#888780"

_NUMERIC_SUFFIX_RE = re.compile(r"(\d+)\s*$")

_INPUT_COLUMNS = ("bugs", "priorities", "requirements")


@dataclasses.dataclass(frozen=True)
class Layout:
    """The ``[dashboard]`` row-allocation values (kanban.cfg defaults)."""

    min_rows_per_column: int = 13
    max_rows_per_column: int = 34
    min_rows_per_project: int = 3
    max_rows_per_project: int = 8


@dataclasses.dataclass(frozen=True)
class ProjectSource:
    """What the pane reads for one project; ``"none"`` marks an absent path."""

    name: str
    color: str
    max_rows: int = 20
    input_dir: str = "none"
    backlog_file: str = "none"
    tasks_dir: str = "none"
    active_rc: str = ""
    last_released: str = ""
    defer: DeferCards = DeferCards()


def compute_layout(n_projects: int, layout: Layout) -> tuple:
    """Return ``(rows_per_project, rows_in_column)`` for *n_projects*."""
    effective = layout.max_rows_per_project
    while effective > layout.min_rows_per_project and n_projects * effective > layout.max_rows_per_column:
        effective -= 1
    if n_projects * effective > layout.max_rows_per_column:
        effective = layout.min_rows_per_project  # even the minimum overflows; accept it
    total = min(max(n_projects * effective, layout.min_rows_per_column), layout.max_rows_per_column)
    return effective, total


def _gray_fallback() -> str:
    try:
        return load_default_palette()[-1]
    except PaletteFileError:
        return _GRAY_SENTINEL


def _env_lines(environ: Mapping[str, str], name: str) -> list:
    return [ln for ln in environ.get(name, "").splitlines() if ln]


def _pad(values: list, n: int, default: str = "none") -> list:
    return values + [default] * (n - len(values))


def _version_or_blank(value: str) -> str:
    return value if value.lower() not in ("none", "") else ""


def sources_from_env(environ: Optional[Mapping[str, str]] = None, column_suffix: str = "") -> list:
    """Build the per-project sources from the gather stage's exported lists.

    *column_suffix* selects one column's lists when the gather stage
    exported every column at once (the board snapshot build): the
    column-specific names (input dirs, backlog files, active RCs, last
    releases, deferral info) are read with the suffix appended, e.g.
    ``_PROJ_INPUT_DIRS_STR__bugs``; the per-project lists every column
    shares (names, colours, max_rows, tasks dirs) are read as they are.
    """
    env = os.environ if environ is None else environ
    sfx = column_suffix
    names = _env_lines(env, "_PROJ_NAMES_STR")
    n = len(names)
    colors = _pad(_env_lines(env, "_PROJ_COLORS_STR"), n, _gray_fallback()) if n else []
    max_rows = _pad(_env_lines(env, "_PROJ_MAX_ROWS_STR"), n, "20")
    input_dirs = _pad(_env_lines(env, "_PROJ_INPUT_DIRS_STR" + sfx), n)
    backlog_files = _pad(_env_lines(env, "_PROJ_BACKLOG_FILES_STR" + sfx), n)
    tasks_dirs = _pad(_env_lines(env, "_PROJ_TASKS_DIRS_STR"), n)
    active_rcs = _pad(_env_lines(env, "_ALL_ACTIVE_RCS" + sfx), n, "")
    last_released = _pad(_env_lines(env, "_ALL_LAST_RELEASED" + sfx), n, "")
    defer_modes, defer_ids = parse_proj_defer_info(
        _env_lines(env, "_PROJ_DEFER_INFO_STR" + sfx), env.get("DFS_SEP", "\x1f"), n,
    )
    glyph_deferred_bug = env.get("GLYPH_DEFERRED_BUG", "")
    glyph_defer_warning = env.get("GLYPH_DEFER_WARNING", "")
    return [
        ProjectSource(
            name=names[i],
            color=colors[i],
            max_rows=parse_max_rows(max_rows[i]),
            input_dir=input_dirs[i],
            backlog_file=backlog_files[i],
            tasks_dir=tasks_dirs[i],
            active_rc=_version_or_blank(active_rcs[i]),
            last_released=_version_or_blank(last_released[i]),
            defer=DeferCards(
                mode=defer_modes[i],
                ids=frozenset(defer_ids[i]),
                glyph_deferred_bug=glyph_deferred_bug,
                glyph_defer_warning=glyph_defer_warning,
            ),
        )
        for i in range(n)
    ]


def _input_sort_key(cid: str, is_requirements: bool):
    """Newest-first key for an intake ID: semver for requirements, else the trailing number."""
    if is_requirements:
        return parse_version(cid) or (0, 0, 0)
    m = _NUMERIC_SUFFIX_RE.search(cid)
    return int(m.group(1)) if m else -1


def gather_project_entries(
    source: ProjectSource,
    *,
    subcommand: str,
    column_type: str,
    use_color: bool,
) -> list:
    """Return one project's classified, sorted and capped entries.

    Each entry is a dict with ``proj_name``, ``proj_color``, ``display_id``,
    ``board_status`` (from ``pgai_agent_kanban.board``), ``status`` (its
    display status), ``id_sort_key`` and ``state`` (the task state, queue
    columns only), plus ``defer_glyph`` on input columns.  Requirements are
    returned uncapped; every other column is narrowed to the project's
    ``max_rows`` by ``status_priority_cap``.
    """
    if subcommand == "input":
        if source.input_dir == "none" or not pathlib.Path(source.input_dir).is_dir():
            return []
        is_req = column_type == "requirements"
        entries = []
        for path in pathlib.Path(source.input_dir).iterdir():
            if not path.is_file() or not path.name.endswith(".md") or path.name.upper().startswith("README"):
                continue
            cid = compact_id(path.stem, is_req)
            if is_req:
                raw_status = ""
                board_status = classify_requirement(
                    get_target_version_for_item(path), source.active_rc, source.last_released,
                )
            else:
                raw_status = read_intake_status(path)
                board_status = classify_intake_status(raw_status)
            glyph = source.defer.suffix(cid, raw_status, use_color) if column_type == "bugs" else ""
            entries.append({
                "proj_name": source.name,
                "proj_color": source.color,
                "display_id": f"{cid} [active]" if board_status == "label" else cid,
                "board_status": board_status,
                "status": display_status(board_status),
                "id_sort_key": _input_sort_key(cid, is_req),
                "state": None,
                "defer_glyph": glyph,
            })
        entries.sort(key=lambda e: e["id_sort_key"], reverse=True)
        return entries if is_req else status_priority_cap(entries, source.max_rows)

    if subcommand == "queue":
        if source.backlog_file == "none" or not pathlib.Path(source.backlog_file).is_file():
            return []
        entries = [
            {
                "proj_name": source.name,
                "proj_color": source.color,
                "display_id": f"{item['date_str']}-{item['seq']:03d}",
                "board_status": item["status"],
                "status": display_status(item["status"]),
                "id_sort_key": (item["date_str"], item["seq"]),
                "state": item["state"],
            }
            for item in queue_items(pathlib.Path(source.backlog_file), pathlib.Path(source.tasks_dir))
        ]
        return status_priority_cap(entries, source.max_rows)

    return []


def _hex_to_rgb(hex_color: str):
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return None
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return None


def _project_tag_color(hex_color: str, palette: Palette) -> str:
    """Return the truecolor foreground escape for a #RRGGBB project colour."""
    if not palette.use_color:
        return ""
    rgb = _hex_to_rgb(hex_color)
    if rgb is None:
        return ""
    r, g, b = rgb
    return f"\033[38;2;{r};{g};{b}m"


def _tagged_row(entry: dict, width: int, palette: Palette, status_glyph: str) -> str:
    """Render one entry as "<tag><text><glyph>", fitting *width* visible characters."""
    glyph = entry.get("defer_glyph", "")
    if palette.use_color:
        tag = f"{_project_tag_color(entry['proj_color'], palette)}{status_glyph}{palette.reset} "
        tag_vis = len(status_glyph) + 1
        text = f"{status_color(entry['status'], palette)}{entry['display_id']}{palette.reset}"
    else:
        tag = f"[{entry['proj_name'][:4]}] "
        tag_vis = len(tag)
        text = entry["display_id"]
    # The deferral glyph follows the ID, so every ID starts right after the
    # tag whether or not its card carries a glyph.
    avail = max(1, width - tag_vis - len(glyph))
    return tag + truncate_ansi(text, avail, palette) + glyph


def _requirements_block(entries: list, budget: int, width: int, palette: Palette, glyph: str) -> list:
    n = len(entries)
    if n <= budget:
        start, end = 0, n
    else:
        active_idx = next((i for i, e in enumerate(entries) if e["status"] == "running"), None)
        start, end = (0, budget) if active_idx is None else active_window(n, budget, active_idx)
    lines = []
    if start > 0:
        lines.append(truncate_ansi(f"... ({start} more above)", width, palette))
    lines += [_tagged_row(e, width, palette, glyph) for e in entries[start:end]]
    if end < n:
        lines.append(truncate_ansi(f"... ({n - end} more below)", width, palette))
    return lines


def _intake_block(entries: list, budget: int, width: int, palette: Palette, glyph: str) -> list:
    if len(entries) > budget:
        lines = [_tagged_row(e, width, palette, glyph) for e in entries[:budget - 1]]
        lines.append(truncate_ansi(f"... and {len(entries) - (budget - 1)} more", width, palette))
        return lines
    return [_tagged_row(e, width, palette, glyph) for e in entries[:budget]]


def _queue_block(entries: list, budget: int, width: int, palette: Palette, glyph: str) -> list:
    window = middle_position_window(entries, lambda e: e["state"], budget)
    return [_tagged_row(e, width, palette, glyph) for e in window]


def render_project_entries(
    project_entries: list,
    *,
    subcommand: str,
    column_type: str,
    width: int,
    palette: Palette,
    layout: Layout = Layout(),
    status_glyph: str = DEFAULT_STATUS_GLYPH,
) -> list:
    """Render the mixed-project pane from already-gathered entries.

    *project_entries* holds one list per registered project, in registry
    order, each as :func:`gather_project_entries` returned it (an empty list
    for a project with nothing to show); the row budget depends on how many
    projects there are, so every project has a list. Returns the pane's
    lines ("(empty)" when none).
    """
    n_projects = len(project_entries)
    budget, _rows_in_column = compute_layout(n_projects, layout) if n_projects else (layout.min_rows_per_project, 0)
    status_glyph = status_glyph or DEFAULT_STATUS_GLYPH
    if subcommand == "input":
        block = _requirements_block if column_type == "requirements" else _intake_block
    elif subcommand == "queue":
        block = _queue_block
    else:
        return ["(empty)"]
    lines = []
    for entries in project_entries:
        if entries:
            lines += block(entries, budget, width, palette, status_glyph)
    return lines or ["(empty)"]


def render_multi_project_column(
    sources: list,
    *,
    subcommand: str,
    column_type: str,
    width: int,
    palette: Palette,
    layout: Layout = Layout(),
    status_glyph: str = DEFAULT_STATUS_GLYPH,
) -> list:
    """Gather every project's entries and render the mixed-project pane."""
    if subcommand not in ("input", "queue"):
        return ["(empty)"]
    project_entries = [
        gather_project_entries(source, subcommand=subcommand, column_type=column_type, use_color=palette.use_color)
        for source in sources
    ]
    return render_project_entries(
        project_entries, subcommand=subcommand, column_type=column_type, width=width,
        palette=palette, layout=layout, status_glyph=status_glyph,
    )
