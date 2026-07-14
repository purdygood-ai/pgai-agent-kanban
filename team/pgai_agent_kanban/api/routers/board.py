"""
board.py — GET /board aggregation endpoint for the pgai-agent-kanban operator API.

Returns the unified kanban board view: all registered projects plus eight fixed
columns (BUGS, PRIORITIES, REQUIREMENTS, PM, CODER, WRITER, TESTER, CM) with
structured item objects.

Response shape:
  {
    "generated_at": "<ISO-8601 UTC>",
    "projects": [
      {"name": str, "color": str, "halt": bool},
      ...
    ],
    "columns": [
      {
        "name": "BUGS",
        "items": [
          {
            "id": "<project>/<kind>/<key>",
            "project": str,
            "kind": str,          # bug | priority | requirement | task | quarantine
            "key": str,
            "title": str,
            "status": str,        # open | working | done | wont-do | blocked | label
            "version_label": str,
            "active_rc": bool,
            "color": str
          },
          ...
        ],
        "truncated": int          # number of items beyond the per-project cap (0 = none)
      },
      ...
    ]
  }

Classification contract:
  Status classification is delegated entirely to board_classifier.py, which
  mirrors the classification logic of the tmux dashboard renderer:
    team/scripts/dashboard/column-render.sh

  This is the house-sibling rule: /board and the tmux renderer must produce
  the same status for any given item.  The parity-pin test in test_board.py
  enforces this invariant.

Constraints:
  - GET only; no mutations.
  - ?project=<name> returns 422 (unfiltered aggregation; the UI filters client-side).
  - Per-project item caps mirror the tmux renderer's dashboard_max_rows setting
    (from projects.cfg; default 20).  Items beyond the cap are counted in
    `truncated`, not returned.
  - Quarantined files (in bugs/.rejected/ and priority/.rejected/) surface as
    kind=quarantine items with status=blocked.
  - `color` is the registry's hex color string verbatim (#RRGGBB); the server
    never emits CSS.
  - Loopback trust boundary: dev_tree_path and git_repo fields are not redacted.
"""

from __future__ import annotations

import configparser
import pathlib
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ..adapter import HTTP_UNPROCESSABLE_ENTITY
from ..dependencies import warn_unknown_query_params
from ..board_classifier import (
    COLUMN_ORDER,
    COLUMN_KINDS,
    classify_input_item,
    classify_queue_item,
    get_active_rc_for_project,
    get_last_released_for_project,
    get_target_version_for_item,
    marker_to_state,
    parse_queue_line,
    read_task_state_from_status_md,
)

__all__ = ["router"]

router = APIRouter(tags=["board"])

# ---------------------------------------------------------------------------
# Default per-project item cap (mirrors tmux renderer default)
# (Source: column-render.sh DASHBOARD_MAX_ROWS default ~line 179)
# ---------------------------------------------------------------------------
_DEFAULT_MAX_ROWS = 20

# Default color palette (mirrors PGAI_DEFAULT_PALETTE in projects.sh)
_DEFAULT_PALETTE = [
    "#378ADD",
    "#E24B4A",
    "#639922",
    "#BA7517",
    "#7B5EA7",
    "#2BA0A0",
    "#C4762A",
    "#4A7FA5",
]

# ---------------------------------------------------------------------------
# projects.cfg helpers
# ---------------------------------------------------------------------------


def _read_projects_registry(
    kanban_root: pathlib.Path,
) -> list[dict]:
    """Read all registered projects from projects.cfg.

    Returns a list of project dicts with keys:
      - name: str
      - priority: int
      - color: str        (hex color string, palette fallback when absent)
      - max_rows: int     (dashboard_max_rows, default 20)

    Projects are sorted by (priority, registration_index) ascending.
    """
    cfg_path = kanban_root / "projects.cfg"
    if not cfg_path.is_file():
        return []

    parser = configparser.RawConfigParser()
    parser.read(str(cfg_path), encoding="utf-8")

    entries: list[tuple[int, int, dict]] = []
    for idx, section in enumerate(parser.sections()):
        if not section.startswith("project:"):
            continue
        name = section[len("project:"):]
        try:
            priority = parser.getint(section, "priority")
        except (configparser.NoOptionError, ValueError):
            priority = 999

        # dashboard_color from registry; empty → palette fallback at render time.
        color = parser.get(section, "dashboard_color", fallback="").strip()

        try:
            max_rows = parser.getint(section, "dashboard_max_rows")
            if max_rows < 1:
                max_rows = _DEFAULT_MAX_ROWS
        except (configparser.NoOptionError, ValueError):
            max_rows = _DEFAULT_MAX_ROWS

        entries.append((priority, idx, {"name": name, "priority": priority,
                                        "color": color, "max_rows": max_rows}))

    entries.sort(key=lambda e: (e[0], e[1]))
    return [entry for _, _, entry in entries]


def _assign_palette_colors(projects: list[dict]) -> None:
    """Assign palette colors in-place to projects missing a color.

    Mirrors the palette-fallback logic in projects_cfg_color() in
    team/scripts/lib/projects.sh: projects without an explicit dashboard_color
    get a deterministic palette color based on registration order.
    """
    palette_idx = 0
    for proj in projects:
        if not proj["color"]:
            proj["color"] = _DEFAULT_PALETTE[palette_idx % len(_DEFAULT_PALETTE)]
            palette_idx += 1
        else:
            # Explicit color set; still advance palette index to keep
            # deterministic assignment for projects without colors.
            palette_idx += 1


def _project_is_halted(project_dir: pathlib.Path) -> bool:
    """Return True when the per-project HALT file is present."""
    return (project_dir / "HALT").is_file()


# ---------------------------------------------------------------------------
# Column data readers
# ---------------------------------------------------------------------------

# Compact ID extractors matching column-render.sh patterns
_BUG_ID_RE = re.compile(r"^(BUG-\d+)", re.IGNORECASE)
_PRIORITY_ID_RE = re.compile(r"^(PRIORITY-\d+)", re.IGNORECASE)
_REQ_ID_RE = re.compile(r"^(v\d+\.\d+\.\d+)(?:-.+)?$", re.IGNORECASE)


def _compact_id_for_input(stem: str, column: str) -> str:
    """Return the compact display key for an input item's filename stem.

    Mirrors compact_id() in column-render.sh (~lines 671-682 and ~lines 1499-1511).
    """
    if column == "REQUIREMENTS":
        m = _REQ_ID_RE.match(stem)
        return m.group(1) if m else stem
    m = _BUG_ID_RE.match(stem)
    if m:
        return m.group(1)
    m = _PRIORITY_ID_RE.match(stem)
    if m:
        return m.group(1)
    return stem


def _kind_for_column(column: str) -> str:
    """Return the item 'kind' string for input items in a given column."""
    if column == "BUGS":
        return "bug"
    if column == "PRIORITIES":
        return "priority"
    if column == "REQUIREMENTS":
        return "requirement"
    return "task"


def _read_input_column(
    project_name: str,
    project_color: str,
    project_dir: pathlib.Path,
    column: str,
    active_rc: str,
    last_released: str,
    max_rows: int,
) -> tuple[list[dict], int]:
    """Read items from an input directory column (BUGS, PRIORITIES, or REQUIREMENTS).

    Returns ``(items, truncated)`` where items is capped at max_rows and
    truncated is the count of items beyond that cap.

    Items are sorted newest-first by mtime (matching the tmux renderer's
    default sort for non-requirements columns).  Requirements items are also
    sorted newest-first for simplicity; the active-window scrolling the
    renderer applies for visual centering is not needed in the API response.
    """
    column_dir_map = {
        "BUGS": project_dir / "bugs",
        "PRIORITIES": project_dir / "priority",
        "REQUIREMENTS": project_dir / "requirements",
    }
    input_dir = column_dir_map[column]
    kind = _kind_for_column(column)

    if not input_dir.is_dir():
        return [], 0

    candidates = []
    for entry in input_dir.iterdir():
        if not entry.is_file():
            continue
        if not entry.name.endswith(".md"):
            continue
        if entry.name.upper().startswith("README"):
            continue
        candidates.append(entry)

    # Sort newest-first by mtime (mirrors renderer default).
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    # Also collect quarantined files from the .rejected/ subdirectory.
    rejected_dir = input_dir / ".rejected"
    quarantine_items = []
    if rejected_dir.is_dir():
        for entry in rejected_dir.iterdir():
            if not entry.is_file():
                continue
            if not entry.name.endswith(".md"):
                continue
            stem = entry.stem
            key = _compact_id_for_input(stem, column)
            quarantine_items.append({
                "id": f"{project_name}/quarantine/{key}",
                "project": project_name,
                "kind": "quarantine",
                "key": key,
                "title": stem,
                "status": "blocked",
                "version_label": "",
                "active_rc": False,
                "color": project_color,
            })

    all_items = []
    for path in candidates:
        stem = path.stem
        key = _compact_id_for_input(stem, column)
        status = classify_input_item(path, column, active_rc, last_released)
        is_active_rc = (status == "label")

        # version_label: for requirements, use the target version string.
        version_label = ""
        if column == "REQUIREMENTS":
            version_label = get_target_version_for_item(path)

        all_items.append({
            "id": f"{project_name}/{kind}/{key}",
            "project": project_name,
            "kind": kind,
            "key": key,
            "title": stem,
            "status": status,
            "version_label": version_label,
            "active_rc": is_active_rc,
            "color": project_color,
        })

    # Quarantine items count against the cap.
    combined = quarantine_items + all_items
    total = len(combined)
    truncated = max(0, total - max_rows)
    return combined[:max_rows], truncated


def _read_queue_column(
    project_name: str,
    project_color: str,
    project_dir: pathlib.Path,
    agent: str,
    max_rows: int,
) -> tuple[list[dict], int]:
    """Read items from an agent queue backlog file.

    Returns ``(items, truncated)`` where items is capped at max_rows and
    truncated is the count beyond the cap.

    Items are read from `tasks/queues/<agent>_backlog.md`.
    State is read from the task's status.md; falls back to the queue marker.
    """
    backlog_path = project_dir / "tasks" / "queues" / f"{agent.lower()}_backlog.md"
    tasks_dir = project_dir / "tasks"

    if not backlog_path.is_file():
        return [], 0

    content = backlog_path.read_text(encoding="utf-8", errors="replace")
    items = []

    for line in content.splitlines():
        parsed = parse_queue_line(line)
        if not parsed:
            continue
        marker, task_id, date_str, seq_int = parsed

        # Read state from status.md; fall back to marker.
        state = read_task_state_from_status_md(tasks_dir, task_id)
        if not state:
            state = marker_to_state(marker)

        status = classify_queue_item(state)
        key = f"{date_str}-{seq_int:03d}"

        items.append({
            "id": f"{project_name}/task/{task_id}",
            "project": project_name,
            "kind": "task",
            "key": task_id,
            "title": task_id,
            "status": status,
            "version_label": "",
            "active_rc": (state == "WORKING"),
            "color": project_color,
            "_sort_key": (date_str, seq_int),
        })

    # Sort newest-first by (date_str, seq).
    items.sort(key=lambda e: e["_sort_key"], reverse=True)

    # Strip the internal sort key before returning.
    for item in items:
        del item["_sort_key"]

    total = len(items)
    truncated = max(0, total - max_rows)
    return items[:max_rows], truncated


# ---------------------------------------------------------------------------
# GET /board
# ---------------------------------------------------------------------------


@router.get(
    "/board",
    summary="Unified kanban board aggregation view",
    response_description=(
        "JSON board with project list and eight columns (BUGS, PRIORITIES, "
        "REQUIREMENTS, PM, CODER, WRITER, TESTER, CM), each with structured "
        "item objects carrying composite ids, server-classified statuses, and "
        "per-column truncation counts."
    ),
)
def get_board(
    request: Request,
    project: Optional[str] = Query(
        default=None,
        description=(
            "Not accepted. GET /board is the unfiltered aggregation view; "
            "the UI filters client-side.  Supplying this parameter returns 422."
        ),
    ),
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return the unified kanban board aggregation view.

    Returns all registered projects and all eight columns with structured items.
    Status classification mirrors the tmux dashboard renderer exactly — see
    board_classifier.py for the shared logic.

    Column order (fixed): BUGS, PRIORITIES, REQUIREMENTS, PM, CODER, WRITER, TESTER, CM.

    Item composite id format: ``<project>/<kind>/<key>``

    Item status vocabulary: ``open | working | done | wont-do | blocked | label``
    - ``label``   — requirements item targeting the currently active RC
    - ``working`` — task in WORKING state (renderer calls this "running")
    - ``blocked`` — task in BLOCKED state, or a quarantined intake file
    - ``done``    — task in DONE state, or a shipped requirements item
    - ``wont-do`` — task in WONT-DO state
    - ``open``    — all other items

    Per-column per-project item caps mirror the tmux renderer's
    ``dashboard_max_rows`` setting (from projects.cfg; default 20).
    Items beyond the cap are counted in ``truncated``, not returned.

    Quarantined files (in ``bugs/.rejected/`` and ``priority/.rejected/``)
    surface as kind=quarantine items with status=blocked.

    Query parameters:

    - ``project`` (forbidden) — returns 422; the board is always unfiltered.

    Response fields:

    - ``generated_at``  — ISO-8601 UTC timestamp of this response.
    - ``projects``      — list of registered projects with name, color, halt.
    - ``columns``       — list of eight column objects (name, items, truncated).

    HTTP status: 200 on success, 422 when ``?project=`` is supplied.
    """
    # Reject ?project=<name>: the board is an unfiltered aggregation view.
    if project is not None:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY,
            detail=(
                "GET /board does not accept a 'project' filter. "
                "The board is the unfiltered aggregation view; "
                "the UI applies project filtering client-side."
            ),
        )

    kanban_root: pathlib.Path = request.app.state.api_cfg.kanban_root

    # Read all registered projects.
    projects_meta = _read_projects_registry(kanban_root)
    _assign_palette_colors(projects_meta)

    # Build projects list for response.
    projects_out = []
    for proj in projects_meta:
        project_dir = kanban_root / "projects" / proj["name"]
        projects_out.append({
            "name": proj["name"],
            "color": proj["color"],
            "halt": _project_is_halted(project_dir),
        })

    # Build columns.
    agent_queue_map = {
        "PM": "pm",
        "CODER": "coder",
        "WRITER": "writer",
        "TESTER": "tester",
        "CM": "cm",
    }

    columns_out = []
    for col_name in COLUMN_ORDER:
        col_kind = COLUMN_KINDS[col_name]
        all_items: list[dict] = []
        total_truncated = 0

        for proj in projects_meta:
            project_dir = kanban_root / "projects" / proj["name"]
            max_rows = proj["max_rows"]

            if col_kind == "input":
                # Read release state for this project.
                active_rc = get_active_rc_for_project(project_dir)
                last_released = get_last_released_for_project(project_dir)

                items, truncated = _read_input_column(
                    project_name=proj["name"],
                    project_color=proj["color"],
                    project_dir=project_dir,
                    column=col_name,
                    active_rc=active_rc,
                    last_released=last_released,
                    max_rows=max_rows,
                )
            else:
                # Queue column.
                agent = agent_queue_map[col_name]
                items, truncated = _read_queue_column(
                    project_name=proj["name"],
                    project_color=proj["color"],
                    project_dir=project_dir,
                    agent=agent,
                    max_rows=max_rows,
                )

            all_items.extend(items)
            total_truncated += truncated

        columns_out.append({
            "name": col_name,
            "items": all_items,
            "truncated": total_truncated,
        })

    generated_at = datetime.now(tz=timezone.utc).isoformat()
    return JSONResponse(
        content={
            "generated_at": generated_at,
            "projects": projects_out,
            "columns": columns_out,
            "warnings": warnings,
        },
        status_code=200,
    )
