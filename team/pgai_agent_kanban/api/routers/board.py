"""
board.py — GET /board aggregation endpoint for the pgai-agent-kanban operator API.

Returns the unified kanban board view: all registered projects plus eight fixed
columns (BUGS, PRIORITIES, REQUIREMENTS, PM, CODER, WRITER, TESTER, CM) with
structured item objects.

Response shape:
  {
    "generated_at": "<ISO-8601 UTC>",
    "projects": [
      {"name": str, "color": str, "halt": bool, "halt_text": str},
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
            "status": str,        # open | working | carried | needs-operator | done
                                  # | wont-do | duplicate | blocked | label
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

Column items:
  Every column is read through the dashboard's own column code,
  pgai_agent_kanban.dashboard.column_render, so /board and the tmux
  renderer list the same items from the same code:
    - input columns: single.input_files (the *.md listing, README*
      excluded, newest modification time first) and
      single.classify_input_file (the item's status);
    - queue columns: single.queue_items (newest (date, sequence) first,
      state from the task's status.md, else the queue marker);
    - display keys: cells.compact_id.
  Statuses come from pgai_agent_kanban.board, the classifier both share;
  a WONT-DO task is "wont-do", never "done".  A requirement is classified
  against the project's active RC (release-state.md) and the last release
  the requirements pane reads (board_gather.requirements_last_released:
  the highest release tag in the project's git dev tree, or
  release-state.md's ## Last Released when the project has no git dev
  tree -- no dev_tree_path, or one that is not a git repository; a git
  dev tree with no release tag has no last release), so it has the
  pane's status.  The
  row windowing the panes apply is display-only: the per-project cap and
  the truncated count below are this endpoint's own.  test_board.py and
  tests/unit/test_board_column_render_parity.py pin the parity.

Constraints:
  - GET only; no mutations.
  - ?project=<name> returns 422 (unfiltered aggregation; the UI filters client-side).
  - Per-project item caps mirror the tmux renderer's dashboard_max_rows setting:
    the projects.cfg value clamped to 5-100, else DASHBOARD_MAX_ROWS from the
    server's environment, else kanban.cfg [dashboard] max_rows, else 20.
    Items beyond the cap are counted in `truncated`, not returned.
  - Quarantined files (in the project's rejected/ directory, and in the legacy
    bugs/.rejected/, priority/.rejected/ and requirements/.rejected/) surface
    as kind=quarantine items with status=blocked, in the column of the intake
    directory they came from (the .reason sidecar's original_dir /
    original_type, else the BUG-/PRIORITY- name prefix). A file whose origin
    cannot be told belongs to no column and is not shown.
  - `color` (#RRGGBB) comes from ops.projects.dashboard_registry_rows: the
    rule projects_cfg_color applies and GET /projects/{name} shares: the
    registry's dashboard_color verbatim, else the default-palette entry at
    the project's ordinal in priority order. The server never emits CSS.
  - `halt` / `halt_text` are every HALT the dashboard header shows for the
    project, from
    pgai_agent_kanban.dashboard.halt_state.effective_halt_flag_and_text:
    `halt` is true for a project HALT or, with no project sentinel, the
    kanban-wide HALT; `halt_text` is "", "HALT" or "HALT-AFTER <event>" (a
    draining project, or with no project sentinel and no kanban-wide HALT,
    a kanban-wide HALT-AFTER; `halt` false).
  - Loopback trust boundary: dev_tree_path and git_repo fields are not redacted.
"""

from __future__ import annotations

import pathlib
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from pgai_agent_kanban.dashboard._rejected import quarantined_files
from pgai_agent_kanban.dashboard.column_render.board_gather import requirements_last_released
from pgai_agent_kanban.dashboard.column_render.cells import compact_id
from pgai_agent_kanban.dashboard.column_render.single import (
    classify_input_file,
    input_files,
    queue_items,
)
from pgai_agent_kanban.dashboard.halt_state import effective_halt_flag_and_text
from pgai_agent_kanban.board import (
    COLUMN_ORDER,
    COLUMN_KINDS,
    get_active_rc_for_project,
    get_target_version_for_item,
    intake_carried_names,
)
from ...ops.context import OpsContext
from ...ops.projects import DEFAULT_DASHBOARD_MAX_ROWS, dashboard_registry_rows
from ..adapter import HTTP_UNPROCESSABLE_ENTITY
from ..dependencies import warn_unknown_query_params

__all__ = ["router"]

router = APIRouter(tags=["board"])

# ---------------------------------------------------------------------------
# projects.cfg helpers
# ---------------------------------------------------------------------------

#: The cap a project gets when its resolved row cap is not a positive whole
#: number (a malformed DASHBOARD_MAX_ROWS); the resolver's own default.
_DEFAULT_MAX_ROWS = int(DEFAULT_DASHBOARD_MAX_ROWS)


def _read_projects_registry(
    kanban_root: pathlib.Path,
) -> list[dict]:
    """Read all registered projects as the tmux board draws them.

    Returns a list of project dicts with keys:
      - name: str
      - color: str        (hex color string, ``projects_cfg_color``'s answer)
      - max_rows: int     (the per-project row cap)

    Rows come from ``ops.projects.dashboard_registry_rows`` -- the reader
    the tmux board pane uses -- so both apply one row-cap chain: the
    project's ``dashboard_max_rows`` clamped to 5..100, else
    ``DASHBOARD_MAX_ROWS``, else ``kanban.cfg [dashboard] max_rows``, else
    20. Projects are in ``projects_cfg_list`` order (priority, then
    registration). An absent or colon-legacy projects.cfg reads as no
    projects.
    """
    rows = dashboard_registry_rows(kanban_root) or []
    return [
        {"name": name, "color": color or "", "max_rows": _row_cap(max_rows)}
        for name, color, max_rows in rows
    ]


def _row_cap(raw: str) -> int:
    """*raw* (the resolver's string) as an item cap: a positive whole number, else 20."""
    text = raw.strip()
    if text.isdigit() and int(text) >= 1:
        return int(text)
    return _DEFAULT_MAX_ROWS


# ---------------------------------------------------------------------------
# Column items -- read through the dashboard's column code
# ---------------------------------------------------------------------------

#: The intake directory each input column lists, under the project directory.
_INPUT_DIRS = {
    "BUGS": "bugs",
    "PRIORITIES": "priority",
    "REQUIREMENTS": "requirements",
}


def _kind_for_column(column: str) -> str:
    """Return the item 'kind' string for input items in a given column."""
    if column == "BUGS":
        return "bug"
    if column == "PRIORITIES":
        return "priority"
    if column == "REQUIREMENTS":
        return "requirement"
    return "task"


def _cap(items: list[dict], max_rows: int) -> tuple[list[dict], int]:
    """Return ``(items[:max_rows], number of items beyond max_rows)``."""
    return items[:max_rows], max(0, len(items) - max_rows)


def _input_column_items(
    project_name: str,
    project_color: str,
    project_dir: pathlib.Path,
    rejected_dir: pathlib.Path,
    column: str,
    active_rc: str,
    last_released: str,
    max_rows: int,
) -> tuple[list[dict], int]:
    """Return one project's items for an input column (BUGS, PRIORITIES, REQUIREMENTS).

    Returns ``(items, truncated)``: items capped at *max_rows*, and the count
    of items beyond that cap.

    The column's quarantined ``.md`` files -- those
    ``dashboard._rejected.quarantined_files`` finds in *rejected_dir* or the
    legacy ``.rejected/`` directories whose origin is this column's intake
    directory -- come first, as kind=quarantine items, and count against
    the cap.  The intake files follow in the input pane's own listing
    (``column_render.single.input_files``: newest modification time first),
    each classified by ``single.classify_input_file`` and keyed by
    ``cells.compact_id``.
    """
    input_dir = project_dir / _INPUT_DIRS[column]
    if not input_dir.is_dir():
        return [], 0
    kind = _kind_for_column(column)
    is_requirements = column == "REQUIREMENTS"

    items = []
    for quarantined in quarantined_files(project_dir, rejected_dir):
        entry = quarantined.path
        if quarantined.origin != input_dir.name or not entry.name.endswith(".md"):
            continue
        key = compact_id(entry.stem, is_requirements)
        items.append({
            "id": f"{project_name}/quarantine/{key}",
            "project": project_name,
            "kind": "quarantine",
            "key": key,
            "title": entry.stem,
            "status": "blocked",
            "version_label": "",
            "active_rc": False,
            "color": project_color,
        })

    carried = frozenset() if is_requirements else intake_carried_names(input_dir)
    for path in input_files(input_dir):
        key = compact_id(path.stem, is_requirements)
        status, _raw_status = classify_input_file(
            path, is_requirements=is_requirements, active_rc=active_rc, last_released=last_released,
            carried=carried,
        )
        items.append({
            "id": f"{project_name}/{kind}/{key}",
            "project": project_name,
            "kind": kind,
            "key": key,
            "title": path.stem,
            "status": status,
            # A requirement carries its target version; other intake items none.
            "version_label": get_target_version_for_item(path) if is_requirements else "",
            "active_rc": status == "label",
            "color": project_color,
        })

    return _cap(items, max_rows)


def _queue_column_items(
    project_name: str,
    project_color: str,
    project_dir: pathlib.Path,
    agent: str,
    max_rows: int,
) -> tuple[list[dict], int]:
    """Return one project's items for an agent queue column.

    Returns ``(items, truncated)``: items capped at *max_rows*, and the count
    beyond the cap.

    The items are ``column_render.single.queue_items`` over
    ``tasks/queues/<agent>_backlog.md``: newest (date, sequence) first, each
    task's state from its status.md, else the queue marker.
    """
    tasks_dir = project_dir / "tasks"
    backlog_path = tasks_dir / "queues" / f"{agent.lower()}_backlog.md"
    if not backlog_path.is_file():
        return [], 0

    items = [
        {
            "id": f"{project_name}/task/{item['task_id']}",
            "project": project_name,
            "kind": "task",
            "key": item["task_id"],
            "title": item["task_id"],
            "status": item["status"],
            "version_label": "",
            "active_rc": item["state"] == "WORKING",
            "color": project_color,
        }
        for item in queue_items(backlog_path, tasks_dir)
    ]
    return _cap(items, max_rows)


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
    pgai_agent_kanban.board for the shared logic.

    Column order (fixed): BUGS, PRIORITIES, REQUIREMENTS, PM, CODER, WRITER, TESTER, CM.

    Item composite id format: ``<project>/<kind>/<key>``

    Item status vocabulary: ``open | working | carried | needs-operator |
    done | wont-do | duplicate | blocked | label``
    - ``label``   — requirements item targeting the currently active RC
    - ``working`` — task in WORKING state (renderer calls this "running"),
      or a running bug/priority a live bundle (or none) still lists
    - ``carried`` — running bug/priority whose every listing bundle is
      closed: the release shipped without fixing it
    - ``needs-operator`` — bug/priority waiting on an operator-inbox answer
    - ``blocked`` — task in BLOCKED state, or a quarantined intake file
    - ``done``    — task in DONE state, or a shipped requirements item
    - ``wont-do`` — task in WONT-DO state
    - ``duplicate`` — bug/priority closed as a duplicate of another item
    - ``open``    — all other items

    Per-column per-project item caps mirror the tmux renderer's
    ``dashboard_max_rows`` setting (from projects.cfg; default 20).
    Items beyond the cap are counted in ``truncated``, not returned.

    Quarantined files (in the project's ``rejected/`` directory, and in the
    legacy ``bugs/.rejected/``, ``priority/.rejected/`` and
    ``requirements/.rejected/``) surface as kind=quarantine items with
    status=blocked, in the column of the intake directory they came from.

    Query parameters:

    - ``project`` (forbidden) — returns 422; the board is always unfiltered.

    Response fields:

    - ``generated_at``  — ISO-8601 UTC timestamp of this response.
    - ``projects``      — list of registered projects with name, color, halt,
                          halt_text (the dashboard's HALT flag and text).
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
    ops_ctx = OpsContext(kanban_root=kanban_root)

    # Read all registered projects.
    projects_meta = _read_projects_registry(kanban_root)

    # Build projects list for response.
    projects_out = []
    for proj in projects_meta:
        project_dir = kanban_root / "projects" / proj["name"]
        halt_flag, halt_text = effective_halt_flag_and_text(project_dir, kanban_root)
        projects_out.append({
            "name": proj["name"],
            "color": proj["color"],
            "halt": halt_flag == "yes",
            "halt_text": halt_text,
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
                # Only a requirement is classified against the release
                # state; the last release is the requirements pane's own
                # read (git tags first), so the two agree.
                active_rc = get_active_rc_for_project(project_dir)
                last_released = (
                    requirements_last_released(kanban_root, proj["name"])
                    if col_name == "REQUIREMENTS" else ""
                )

                items, truncated = _input_column_items(
                    project_name=proj["name"],
                    project_color=proj["color"],
                    project_dir=project_dir,
                    rejected_dir=ops_ctx.rejected_dir(proj["name"]),
                    column=col_name,
                    active_rc=active_rc,
                    last_released=last_released,
                    max_rows=max_rows,
                )
            else:
                # Queue column.
                agent = agent_queue_map[col_name]
                items, truncated = _queue_column_items(
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
