"""
pgai_agent_kanban.dashboard.column_render.board_snapshot — one shared,
per-tick board snapshot that every mixed-project board column renders from.

The board's eight ``--all-projects`` columns (bugs, priorities,
requirements, and the pm/coder/writer/tester/cm queues) each used to gather
and classify every registered project's items on every tick.  Instead, the
first column to tick builds ONE snapshot: every column's per-project
entries, classified through ``pgai_agent_kanban.board`` exactly as
:func:`~pgai_agent_kanban.dashboard.column_render.multi.gather_project_entries`
classifies them for a single column.  Every column then reads its own slice
and only renders it (:func:`render_column`), so its output is byte-identical
to gathering and rendering that column directly.

Freshness
---------
A snapshot is fresh while its file is younger than the caller's TTL (the
dashboard snapshot TTL, the column refresh interval by default).  A missing,
unreadable, malformed or stale snapshot -- or one built for the other
colour mode -- is a miss (:func:`read_snapshot` returns ``None`` with the
reason), and the caller builds a new one or renders the column directly.

The file is written atomically (``lib.atomic_write``), so a reader sees the
previous complete snapshot or the new one, never a partial file.

Gather count
------------
Every build increments ``gathers`` in a small stats file beside the snapshot
(``<snapshot>.stats``: ``gathers=<n>`` and ``last=<ISO-8601 UTC>``), so how
often the board's gather runs can be counted without reading CPU figures.

Pre-rendered columns
--------------------
A build also writes, beside the snapshot, each requested column already
rendered at a given width (:func:`write_prerender`, at
:func:`prerender_path`: ``<snapshot stem>.<column type>.w<width>.txt``, one
pane line per line).  The panes declare the widths of every board column
(column-render.sh ``--board-widths``, parsed by :func:`parse_board_widths`),
so one build renders the whole board and every other column of that tick
prints its file without starting Python.  A pre-render is only ever read
together with its snapshot: the bash reader serves it while the snapshot is
fresh and the pre-render is no older than the snapshot (written after it by
the same build), so a stale, rebuilt or replaced snapshot never pairs with
an older pre-render.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import time
from typing import Mapping, Optional

from pgai_agent_kanban.dashboard.column_render.cells import Palette
from pgai_agent_kanban.dashboard.column_render.multi import (
    DEFAULT_STATUS_GLYPH,
    Layout,
    gather_project_entries,
    render_project_entries,
    sources_from_env,
)
from pgai_agent_kanban.lib.atomic_write import atomic_write

__all__ = [
    "BOARD_COLUMNS",
    "MISS_MISSING",
    "MISS_STALE",
    "SNAPSHOT_FORMAT",
    "build_snapshot",
    "build_snapshot_from_env",
    "column_env_suffix",
    "column_subcommand",
    "parse_board_widths",
    "prerender_path",
    "read_snapshot",
    "read_stats",
    "record_gather",
    "render_column",
    "stats_path",
    "write_prerender",
    "write_snapshot",
]

#: Bumped when the on-disk layout changes; a snapshot of another format is a miss.
SNAPSHOT_FORMAT = 1

#: Every mixed-project board column, as (subcommand, column type).
BOARD_COLUMNS = (
    ("input", "bugs"),
    ("input", "priorities"),
    ("input", "requirements"),
    ("queue", "pm"),
    ("queue", "coder"),
    ("queue", "writer"),
    ("queue", "tester"),
    ("queue", "cm"),
)

#: Miss reasons :func:`read_snapshot` reports.
MISS_MISSING = "missing"
MISS_STALE = "stale"

_SCRATCH_PREFIX = ".board_snapshot_tmp_"


def column_env_suffix(column_type: str) -> str:
    """The suffix the bash gather stage appends to one column's exported lists."""
    return f"__{column_type}"


def column_subcommand(column_type: str) -> Optional[str]:
    """The subcommand (``input`` / ``queue``) a board column type takes, or None."""
    for subcommand, board_column in BOARD_COLUMNS:
        if board_column == column_type:
            return subcommand
    return None


def parse_board_widths(spec: str) -> dict:
    """Parse a ``--board-widths`` value into ``{column type: width}``.

    *spec* is comma-separated ``KEY=WIDTH`` entries.  KEY is a board column
    type (``bugs`` ... ``cm``) or a subcommand (``input`` / ``queue``), which
    sets every column of that kind; a column-type entry wins over its
    kind's.  WIDTH is a positive integer.  Whitespace around keys, values
    and commas is ignored; an entry with an unknown key or a bad width is
    skipped, so a malformed value pre-renders less but never fails a pane.

    Examples:
        >>> parse_board_widths("input=29, queue=26, cm=30")["cm"]
        30
        >>> parse_board_widths("bugs=x,nope=3")
        {}
    """
    by_kind: dict = {}
    by_column: dict = {}
    for entry in (spec or "").split(","):
        key, sep, value = entry.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not value.isdigit() or int(value) <= 0:
            continue
        if key in ("input", "queue"):
            by_kind[key] = int(value)
        elif column_subcommand(key) is not None:
            by_column[key] = int(value)
    widths = {}
    for subcommand, column_type in BOARD_COLUMNS:
        if column_type in by_column:
            widths[column_type] = by_column[column_type]
        elif subcommand in by_kind:
            widths[column_type] = by_kind[subcommand]
    return widths


def prerender_path(snapshot_path: pathlib.Path, column_type: str, width: int) -> pathlib.Path:
    """Where a build writes *column_type* pre-rendered at *width*.

    ``<snapshot stem>.<column type>.w<width>.txt`` beside the snapshot --
    the name ``column_render_board_prerender_path`` in
    ``team/scripts/lib/dashboard/column_render_board.sh`` reads.
    """
    return snapshot_path.with_name(f"{snapshot_path.stem}.{column_type}.w{int(width)}.txt")


def write_prerender(path: pathlib.Path, lines: list) -> None:
    """Write a pre-rendered column atomically, one pane line per line.  Raises ``OSError``."""
    atomic_write(path, "".join(f"{line}\n" for line in lines), prefix=_SCRATCH_PREFIX)


def build_snapshot(sources_by_column: Mapping[str, list], *, use_color: bool) -> dict:
    """Gather and classify every board column's entries for every project.

    *sources_by_column* maps each column type in :data:`BOARD_COLUMNS` to
    its per-project ``ProjectSource`` list (registry order).  Each column
    holds one entry list per project, as ``gather_project_entries`` returns
    it -- the same entries a direct render of that column would draw.
    """
    columns = {}
    for subcommand, column_type in BOARD_COLUMNS:
        columns[column_type] = {
            "subcommand": subcommand,
            "projects": [
                gather_project_entries(
                    source, subcommand=subcommand, column_type=column_type, use_color=use_color,
                )
                for source in sources_by_column.get(column_type, [])
            ],
        }
    return {"format": SNAPSHOT_FORMAT, "use_color": bool(use_color), "columns": columns}


def build_snapshot_from_env(environ: Optional[Mapping[str, str]] = None, *, use_color: bool) -> dict:
    """:func:`build_snapshot` over the per-column lists the bash gather exported."""
    env = os.environ if environ is None else environ
    return build_snapshot(
        {
            column_type: sources_from_env(env, column_env_suffix(column_type))
            for _subcommand, column_type in BOARD_COLUMNS
        },
        use_color=use_color,
    )


def write_snapshot(path: pathlib.Path, snapshot: dict) -> None:
    """Write *snapshot* to *path* atomically.  Raises ``OSError`` on failure."""
    atomic_write(path, json.dumps(snapshot, separators=(",", ":")), prefix=_SCRATCH_PREFIX)


def read_snapshot(
    path: pathlib.Path,
    *,
    ttl_seconds: float,
    use_color: bool,
    now: Optional[float] = None,
) -> tuple:
    """Return ``(snapshot, None)`` for a fresh, usable snapshot, else ``(None, reason)``.

    *reason* is :data:`MISS_STALE` when the file exists but is at least
    *ttl_seconds* old, and :data:`MISS_MISSING` when it is absent,
    unreadable, malformed, of another format, or built for the other colour
    mode.
    """
    try:
        age = (time.time() if now is None else now) - path.stat().st_mtime
    except OSError:
        return None, MISS_MISSING
    if age >= ttl_seconds:
        return None, MISS_STALE
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, MISS_MISSING
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("format") != SNAPSHOT_FORMAT
        or snapshot.get("use_color") is not bool(use_color)
        or not isinstance(snapshot.get("columns"), dict)
    ):
        return None, MISS_MISSING
    return snapshot, None


def render_column(
    snapshot: dict,
    *,
    subcommand: str,
    column_type: str,
    width: int,
    palette: Palette,
    layout: Layout = Layout(),
    status_glyph: str = DEFAULT_STATUS_GLYPH,
) -> Optional[list]:
    """Render one column's slice of *snapshot*; ``None`` when it has no such slice."""
    column = snapshot.get("columns", {}).get(column_type)
    if not isinstance(column, dict) or column.get("subcommand") != subcommand:
        return None
    projects = column.get("projects")
    if not isinstance(projects, list):
        return None
    return render_project_entries(
        projects, subcommand=subcommand, column_type=column_type, width=width,
        palette=palette, layout=layout, status_glyph=status_glyph,
    )


def stats_path(snapshot_path: pathlib.Path) -> pathlib.Path:
    """The gather-count stats file beside *snapshot_path*."""
    return snapshot_path.with_name(snapshot_path.name + ".stats")


def read_stats(snapshot_path: pathlib.Path) -> dict:
    """Return the stats file's ``key=value`` fields (empty when absent or unreadable)."""
    try:
        text = stats_path(snapshot_path).read_text(encoding="utf-8")
    except OSError:
        return {}
    fields = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


def record_gather(snapshot_path: pathlib.Path, now: Optional[datetime.datetime] = None) -> int:
    """Increment the gather count beside *snapshot_path*; return the new count.

    The caller holds the board-build lock, so the read-increment-write is
    not raced by another builder.  Raises ``OSError`` when it cannot write.
    """
    try:
        count = int(read_stats(snapshot_path).get("gathers", "0"))
    except ValueError:
        count = 0
    count += 1
    stamp = (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    atomic_write(stats_path(snapshot_path), f"gathers={count}\nlast={stamp}\n", prefix=_SCRATCH_PREFIX)
    return count
