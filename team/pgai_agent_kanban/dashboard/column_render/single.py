"""
pgai_agent_kanban.dashboard.column_render.single — one project's board
column pane: an input column (bugs/, priority/, requirements/) or an agent
queue column (one ``<agent>_backlog.md``).

Each item's status comes from ``pgai_agent_kanban.board`` — the classifier
``GET /board`` uses — and its row is coloured by that status
(``cells.display_status`` / ``cells.status_color``).

Input column
------------
Files are the ``*.md`` entries of the directory (README* excluded), newest
modification time first.

* Requirements, when there are more files than *rows*: the window is kept
  around the first file targeting the active RC, with "... (N more
  above)" / "... (N more below)" marker rows for the hidden sides.  With no
  active file the newest *rows* files are shown, without markers.
* Otherwise, when there are more files than *max_rows*: the newest
  ``max_rows - 1`` files and a "... and N more" row.
* Otherwise: the newest ``min(rows, max_rows)`` files.

A requirements row shows its vX.Y.Z, with " [active]" for the active RC,
and an open requirement the project's version ceilings stop carries
" [ceiling]" (its target exceeds a ceiling) or " [ceiling?]" (a ceiling is
unreadable, so nothing is queued) after its coloured label
(``CeilingCards``); a bug or priority row its BUG-/PRIORITY- ID, followed on
bug cards by the deferral glyph (``DeferCards``).  Rows are truncated to *width* visible
characters.  An absent or empty directory renders "(empty)".

Queue column
------------
Every queue line (``- [m] <TASK-ID> ...``, old ``CLAUDE-AGENT-...`` and new
``AGENT-...`` IDs) is an item, newest (date, sequence) first.  Its state is
the task's ``status.md`` ``## State`` under *tasks_dir*, or the queue
marker's state when there is no status.md.  At most ``min(rows, 13)`` rows
are chosen by ``cells.middle_position_window``; when the backlog holds more
than *max_rows* items the window is cut to ``max_rows - 1`` rows and a
"... and N more" row follows.  Each row is the item's ``YYYYMMDD-NNN``.
"""

from __future__ import annotations

import pathlib

from pgai_agent_kanban.board import (
    classify_intake_status,
    classify_queue_item,
    classify_requirement,
    get_target_version_for_item,
    marker_to_state,
    parse_queue_line,
    read_intake_status,
    read_task_state_from_status_md,
)
from pgai_agent_kanban.dashboard.column_render.cells import (
    CeilingCards,
    DeferCards,
    Palette,
    active_window,
    compact_id,
    display_status,
    middle_position_window,
    status_color,
    truncate_ansi,
)

__all__ = [
    "QUEUE_MAX_ROWS",
    "classify_input_file",
    "parse_max_rows",
    "queue_items",
    "render_input_column",
    "render_queue_column",
]

#: A queue pane never shows more than this many task rows, whatever *rows* is.
QUEUE_MAX_ROWS = 13

_EMPTY = ["(empty)"]


def parse_max_rows(value: str, default: int = 20) -> int:
    """Parse a per-project ``max_rows`` value; *default* when not a positive int."""
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 1 else default


def _input_files(input_dir: pathlib.Path) -> list:
    """Return the directory's *.md files (README* excluded), newest first."""
    files = [
        entry for entry in input_dir.iterdir()
        if entry.is_file() and entry.name.endswith(".md") and not entry.name.upper().startswith("README")
    ]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def classify_input_file(
    path: pathlib.Path,
    *,
    is_requirements: bool,
    active_rc: str,
    last_released: str,
) -> tuple:
    """Return ``(board_status, raw_status)`` for one input file.

    *raw_status* is the bug/priority file's own ``## Status`` word ("" when
    it has none), which the deferral glyph decision reads; it is "" for a
    requirement.
    """
    if is_requirements:
        return classify_requirement(get_target_version_for_item(path), active_rc, last_released), ""
    raw = read_intake_status(path, default="")
    return classify_intake_status(raw), raw


def render_input_column(
    input_dir: pathlib.Path,
    *,
    rows: int,
    width: int,
    is_requirements: bool,
    active_rc: str,
    last_released: str,
    max_rows: int,
    palette: Palette,
    defer: DeferCards = DeferCards(),
    ceiling: CeilingCards = CeilingCards(),
) -> list:
    """Render one project's input column; returns the pane's lines."""
    if not input_dir.is_dir():
        return list(_EMPTY)
    files = _input_files(input_dir)
    if not files:
        return list(_EMPTY)

    statuses: dict = {}

    def classify(path):
        if path not in statuses:
            statuses[path] = classify_input_file(
                path, is_requirements=is_requirements, active_rc=active_rc, last_released=last_released,
            )
        return statuses[path]

    def row(path) -> str:
        board_status, raw_status = classify(path)
        cid = compact_id(path.stem, is_requirements)
        colour = status_color(display_status(board_status), palette)
        if is_requirements:
            label = f"{cid} [active]" if board_status == "label" else cid
            mark = ceiling.suffix(board_status, get_target_version_for_item(path)) if ceiling.active else ""
            text = f"{colour}{label}{palette.reset}{mark}" if palette.use_color and colour else f"{label}{mark}"
        else:
            # The glyph follows the coloured ID so every ID starts in the
            # same column whether or not its card carries a glyph.
            glyph = defer.suffix(cid, raw_status, palette.use_color)
            text = f"{colour}{cid}{palette.reset}{glyph}" if palette.use_color and colour else f"{cid}{glyph}"
        return truncate_ansi(text, width, palette)

    total = len(files)
    lines = []
    if is_requirements and total > rows:
        active_idx = next((i for i, p in enumerate(files) if classify(p)[0] == "label"), None)
        if active_idx is None:
            lines = [row(p) for p in files[:rows]]
        else:
            start, end = active_window(total, rows, active_idx)
            if start > 0:
                lines.append(truncate_ansi(f"... ({start} more above)", width, palette))
            lines += [row(p) for p in files[start:end]]
            if end < total:
                lines.append(truncate_ansi(f"... ({total - end} more below)", width, palette))
    elif total > max_rows:
        lines = [row(p) for p in files[:max_rows - 1]]
        lines.append(truncate_ansi(f"... and {total - (max_rows - 1)} more", width, palette))
    else:
        lines = [row(p) for p in files[:min(rows, max_rows)]]
    return lines or list(_EMPTY)


def queue_items(backlog_file: pathlib.Path, tasks_dir: pathlib.Path) -> list:
    """Return a backlog's items, newest first, as dicts.

    Each item carries ``task_id``, ``date_str``, ``seq``, ``state`` (from the
    task's status.md, else from the queue marker) and ``status`` (the board
    status ``classify_queue_item`` assigns that state).
    """
    content = backlog_file.read_text(encoding="utf-8", errors="replace")
    items = []
    for line in content.splitlines():
        parsed = parse_queue_line(line)
        if not parsed:
            continue
        marker, task_id, date_str, seq = parsed
        state = read_task_state_from_status_md(tasks_dir, task_id) or marker_to_state(marker)
        items.append({
            "task_id": task_id,
            "date_str": date_str,
            "seq": seq,
            "state": state,
            "status": classify_queue_item(state),
        })
    items.sort(key=lambda e: (e["date_str"], e["seq"]), reverse=True)
    return items


def render_queue_column(
    backlog_file: pathlib.Path,
    *,
    rows: int,
    width: int,
    tasks_dir: pathlib.Path,
    max_rows: int,
    palette: Palette,
) -> list:
    """Render one agent queue column; returns the pane's lines."""
    if not backlog_file.is_file():
        return list(_EMPTY)
    items = queue_items(backlog_file, tasks_dir)
    if not items:
        return list(_EMPTY)

    window = middle_position_window(items, lambda e: e["state"], min(rows, QUEUE_MAX_ROWS))
    n_more = 0
    if len(items) > max_rows:
        window = window[:max_rows - 1]
        n_more = len(items) - len(window)

    lines = []
    for item in window:
        short_id = f"{item['date_str']}-{item['seq']:03d}"
        colour = status_color(display_status(item["status"]), palette) if palette.use_color else ""
        text = f"{colour}{short_id}{palette.reset}" if palette.use_color and colour else short_id
        lines.append(truncate_ansi(text, width, palette))
    if n_more:
        lines.append(truncate_ansi(f"... and {n_more} more", width, palette))
    return lines or list(_EMPTY)
