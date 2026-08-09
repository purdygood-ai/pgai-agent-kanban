"""
reads.py — GET (read-only) endpoints for the pgai-agent-kanban operator API.

Implements every read endpoint defined in the v1.2.0 operator-REST-API requirements:

  GET /status             → kanban-status.sh --project <name>
  GET /show               → show.sh --project <name> --key <key> [--file ...]
  GET /test-report        → show-test-report.sh --project <name> --key <key>
  GET /metrics            → dashboard/show-metrics.sh [--project <name>] [--last N] [--per_agent]
  GET /metrics?format=json → metrics_data.metrics_rows_for_project() (Python reader, no shell-out)
  GET /costs              → cost-report.sh [--project <name>] [--month ...] [--day ...] [--rc ...]
  GET /costs?format=json  → metrics_data.costs_rows_for_scope() (Python reader, no shell-out)
  GET /rejected           → list-rejected.sh [--project <name>]
  GET /projects           → registry read via _projects_cfg_list() (no shell-out)
  GET /dashboard/{pane}   → pane-specific dashboard script (see _PANE_SCRIPTS table)
  GET /projects/{project}/tasks/{key}/readme
                          → show-readme.sh --project <name> --key <key>; returns a
                            task's README.md bytes, byte-identical to the CLI tool.

JSON format contract (?format=json on /metrics and /costs):
  Both JSON branches read the same underlying per-RC data files that the text renderers
  read (sibling rule: one source, two formats).  The text path is unchanged and
  byte-identical when no format parameter is supplied.

  Row shape (fields present only when the underlying data has a non-empty value):
    version         str   — RC version string
    tasks           int   — total tasks in the RC
    wall_seconds    float — wall time in seconds (metrics only; absent when not populated)
    tokens_in       int   — input token count
    tokens_out      int   — output token count
    cache_read_pct  float — cache read percentage (0–100), 1 decimal place
    est_cost        float — estimated cost in USD

  Missing fields are omitted from JSON rows — never null or zero-filled.

Design:
  - All endpoints are GET only; no side effects, no disk writes.
  - Query-parameter names match the underlying script's flag names verbatim.
  - /metrics with no project parameter omits --project, matching show-metrics.sh
    iterate-all behavior.
  - /dashboard/{pane} accepts panes: input, queue, metrics, attention, header,
    status-window.
  - /projects reads projects.cfg directly (no shell-out).
  - Script paths are resolved relative to cfg.kanban_root / "scripts".

Envelope format (from adapter.py):
  {"exit_code": int, "stdout": str, "stderr": str}

HTTP status mapping (from adapter.http_status_for):
  exit_code == 0  → 200 OK
  non-zero        → 500 Internal Server Error
  validation fail → 422 Unprocessable Entity

Security note: no authentication or TLS in this release.  Loopback-only binding
is the sole access-control mechanism.
"""

from __future__ import annotations

import configparser
import pathlib
import subprocess
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .adapter import (
    HTTP_UNPROCESSABLE_ENTITY,
    ShellResult,
    ValidationError,
    http_status_for,
    shell_out,
    validate_required,
)
from .dependencies import warn_unknown_query_params
from .metrics_data import costs_rows_for_scope, metrics_rows_for_project
from ..dashboard.scan_auto_resolutions import collect_auto_resolutions
from ..dashboard.scan_human_approvals import collect_pending_approvals
from ..env import resolve_kanban_root

__all__ = ["router"]

router = APIRouter()

# ---------------------------------------------------------------------------
# Pane → script table
# ---------------------------------------------------------------------------
# Maps the {pane} path parameter to the script name relative to:
#   scripts/dashboard/  (for dashboard-specific scripts)
#   scripts/            (for top-level scripts)
#
# Panes served by column-render.sh use special handling because that script
# uses positional arguments rather than --flag style.
_PANE_SCRIPTS: dict[str, str] = {
    "input": "dashboard/column-render.sh",       # positional-arg script
    "queue": "dashboard/column-render.sh",        # positional-arg script
    "metrics": "dashboard/show-metrics.sh",
    "attention": "dashboard/show-attention.sh",
    "header": "dashboard/show-header.sh",
    "status-window": "dashboard/show-status-window.sh",
}

_VALID_PANES = frozenset(_PANE_SCRIPTS)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _scripts_dir(request: Request) -> pathlib.Path:
    """Return the scripts/ directory path from the app's ApiConfig.

    Scripts live at ``<kanban_root>/scripts/`` in both the live install
    and any install produced by install.sh.  The kanban_root is read from
    cfg stored on app.state.

    Args:
        request: The FastAPI request object (provides access to app.state).

    Returns:
        Absolute path to the scripts/ directory.
    """
    kanban_root: pathlib.Path = request.app.state.api_cfg.kanban_root
    return kanban_root / "scripts"


def _script_path(request: Request, relative: str) -> str:
    """Resolve a script path relative to scripts/ and return it as a string.

    Args:
        request:  FastAPI request (for kanban_root).
        relative: Relative path within scripts/; e.g. ``"kanban-status.sh"``
                  or ``"dashboard/show-metrics.sh"``.

    Returns:
        Absolute script path string suitable for subprocess invocation.
    """
    return str(_scripts_dir(request) / relative)


def _make_envelope(result: ShellResult, warnings: list[str] | None = None) -> JSONResponse:
    """Convert a ShellResult into the standard JSON envelope response.

    The envelope format is:
      {"exit_code": int, "stdout": str, "stderr": str, "warnings": list[str]}

    The ``warnings`` field is always present.  It is an empty list on clean
    responses and carries one entry per unknown query parameter detected by
    the shared ``warn_unknown_query_params`` dependency.

    HTTP status is derived from the exit_code via http_status_for().

    Args:
        result:   The ShellResult envelope from the adapter.
        warnings: Warning strings collected by the route's dependency injection.
                  Defaults to an empty list when not supplied.

    Returns:
        A JSONResponse with the appropriate HTTP status and envelope body.
    """
    return JSONResponse(
        content={
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "warnings": warnings if warnings is not None else [],
        },
        status_code=http_status_for(result.exit_code),
    )


def _projects_cfg_list(kanban_root: pathlib.Path) -> list[str]:
    """Return project names from projects.cfg in priority order.

    Python counterpart of the ``projects_cfg_list`` bash function in
    team/scripts/lib/projects.sh.  Reads the INI-format projects.cfg from
    ``<kanban_root>/projects.cfg`` and returns project names sorted by
    priority (ascending), with registration order as the tie-break.

    Returns an empty list when projects.cfg is absent or contains no projects.

    Args:
        kanban_root: Resolved kanban root directory (from ApiConfig).

    Returns:
        List of project names in priority order.
    """
    cfg_path = kanban_root / "projects.cfg"
    if not cfg_path.is_file():
        return []

    parser = configparser.RawConfigParser()
    parser.read(str(cfg_path), encoding="utf-8")

    # Collect (priority, registration_index, name) for sorting.
    entries: list[tuple[int, int, str]] = []
    for idx, section in enumerate(parser.sections()):
        if not section.startswith("project:"):
            continue
        name = section[len("project:"):]
        try:
            priority = parser.getint(section, "priority")
        except (configparser.NoOptionError, ValueError):
            priority = 999
        entries.append((priority, idx, name))

    entries.sort(key=lambda e: (e[0], e[1]))
    return [name for _, _, name in entries]


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------


@router.get(
    "/status",
    summary="Kanban status summary",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr from kanban-status.sh."
    ),
)
def get_status(
    request: Request,
    project: str,
    no_color: Optional[bool] = None,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return the output of ``kanban-status.sh --project <name>``.

    Query parameters:

    - ``project`` (required) — project name.
    - ``no_color`` (optional, bool) — when true, passes ``--no_color`` to the script.

    Response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list[str]}``

    HTTP status:
      200 on exit_code 0, 500 on non-zero, 422 on parameter validation failure.
    """
    try:
        validate_required({"project": project}, required_keys=["project"])
    except ValidationError as exc:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY, detail=exc.detail
        ) from exc

    flags: dict = {"project": project}
    if no_color:
        flags["no_color"] = True

    result = shell_out(_script_path(request, "kanban-status.sh"), flags)
    return _make_envelope(result, warnings)


# ---------------------------------------------------------------------------
# GET /show
# ---------------------------------------------------------------------------


@router.get(
    "/show",
    summary="Show a task or intake item",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr from show.sh."
    ),
)
def get_show(
    request: Request,
    project: str,
    key: str,
    file: Optional[str] = None,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return the output of ``show.sh --project <name> --key <key> [--file ...]``.

    Query parameters:

    - ``project`` (required) — project name.
    - ``key``     (required) — task key or intake item identifier.
    - ``file``    (optional) — ``status`` (default) or ``readme``; selects
                               which file to emit for task keys.

    Response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list[str]}``
    """
    try:
        validate_required({"project": project, "key": key}, required_keys=["project", "key"])
    except ValidationError as exc:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY, detail=exc.detail
        ) from exc

    flags: dict = {"project": project, "key": key}
    if file:
        flags["file"] = file

    result = shell_out(_script_path(request, "show.sh"), flags)
    return _make_envelope(result, warnings)


# ---------------------------------------------------------------------------
# GET /test-report
# ---------------------------------------------------------------------------


@router.get(
    "/test-report",
    summary="Show a TESTER verification report",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr from show-test-report.sh."
    ),
)
def get_test_report(
    request: Request,
    project: str,
    key: str,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return the output of ``show-test-report.sh --project <name> --key <key>``.

    Query parameters:

    - ``project`` (required) — project name.
    - ``key``     (required) — TESTER task key or RC version (e.g. ``v1.2.0``).

    Response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list[str]}``
    """
    try:
        validate_required({"project": project, "key": key}, required_keys=["project", "key"])
    except ValidationError as exc:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY, detail=exc.detail
        ) from exc

    flags: dict = {"project": project, "key": key}
    result = shell_out(_script_path(request, "show-test-report.sh"), flags)
    return _make_envelope(result, warnings)


# ---------------------------------------------------------------------------
# GET /metrics
# ---------------------------------------------------------------------------


@router.get(
    "/metrics",
    summary="Historical RC metrics",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr from dashboard/show-metrics.sh; "
        "or a JSON array of per-RC row objects when ?format=json is supplied."
    ),
)
def get_metrics(
    request: Request,
    project: Optional[str] = None,
    last: Optional[int] = None,
    per_agent: Optional[bool] = None,
    format: Optional[str] = None,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return historical RC metrics for the requested project(s).

    When ``format`` is omitted, delegates to ``dashboard/show-metrics.sh`` and
    returns the standard text envelope.  The text output is byte-identical to
    the pre-v1.6.0 behavior.

    When ``format=json`` is supplied and ``project`` is specified, returns a JSON
    array of per-RC row objects read from the same ``metrics/history.csv`` that
    the text renderer uses (sibling rule: one source, two formats).  A ``project``
    parameter is required for ``format=json``; omitting it returns 422.

    Query parameters:

    - ``project``   (optional) — project name; omit to iterate all projects (text only).
    - ``last``      (optional, int) — show last N rows (default: 10).
    - ``per_agent`` (optional, bool) — when true, passes ``--per_agent`` to the text
                    renderer; per-agent breakdown for the most recent RC.
                    Not applicable for ``format=json``.
    - ``format``    (optional) — when ``json``, returns a JSON array of row objects
                    instead of the text envelope.

    Text response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list[str]}``

    JSON response (``?format=json``):
      ``[{"version": str, "tasks": int, "tokens_in": int, ...}, ...]``
      Fields present only when the underlying source has a non-empty value.
      Note: this array response does not carry a top-level ``warnings`` key;
      adding one requires a non-additive shape change tracked separately.
    """
    if format is not None and format.lower() == "json":
        # JSON branch: read history.csv via Python; project is required.
        if not project:
            raise HTTPException(
                status_code=HTTP_UNPROCESSABLE_ENTITY,
                detail="project is required when format=json is specified.",
            )
        kanban_root: pathlib.Path = request.app.state.api_cfg.kanban_root
        # kanban_root in ApiConfig is set to the dev-tree team/ directory so that
        # scripts resolve correctly.  For data files we need the live kanban root
        # from the canonical resolver.
        live_root = resolve_kanban_root()
        project_root = live_root / "projects" / project
        rows = metrics_rows_for_project(project_root, last_n=last)
        return JSONResponse(content=rows, status_code=200)

    # Text branch: unchanged shell-out path (byte-identical to pre-v1.6.0).
    flags: dict = {}
    if project:
        flags["project"] = project
    if last is not None:
        flags["last"] = str(last)
    if per_agent:
        flags["per_agent"] = True

    result = shell_out(_script_path(request, "dashboard/show-metrics.sh"), flags)
    return _make_envelope(result, warnings)


# ---------------------------------------------------------------------------
# GET /costs
# ---------------------------------------------------------------------------


@router.get(
    "/costs",
    summary="Cost report",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr from cost-report.sh; "
        "or a JSON array of per-RC row objects when ?format=json is supplied."
    ),
)
def get_costs(
    request: Request,
    project: Optional[str] = None,
    month: Optional[str] = None,
    day: Optional[str] = None,
    rc: Optional[str] = None,
    format: Optional[str] = None,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return cost report data for the requested project and scope.

    When ``format`` is omitted, delegates to ``cost-report.sh`` and returns the
    standard text envelope.  The text output is byte-identical to the pre-v1.6.0
    behavior.

    When ``format=json`` is supplied, returns a JSON object with per-RC row
    objects and the warnings list.  A ``project`` parameter is required for
    ``format=json``; omitting it returns 422.

    Scope selection (at most one of ``month``, ``day``, ``rc``):

    - ``rc``    — single RC; the JSON array contains one row for that RC.
    - ``month`` — all RC files whose shipped_at starts with ``YYYY-MM``.
    - ``day``   — all RC files whose shipped_at starts with ``YYYY-MM-DD``.
    - (none)   — text: current month-to-date (cost-report.sh default);
                  JSON: all RC files in usage/rc/.

    Query parameters:

    - ``project`` (optional) — project name.
    - ``month``   (optional) — calendar month ``YYYY-MM``.
    - ``day``     (optional) — single day ``YYYY-MM-DD``.
    - ``rc``      (optional) — RC version string (e.g. ``v1.2.0``).
    - ``format``  (optional) — when ``json``, returns a JSON object instead of the
                               text envelope.

    Text response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list[str]}``

    JSON response (``?format=json``):
      ``[{"version": str, "tokens_in": int, "est_cost": float, ...}, ...]``
      Fields present only when the underlying source has a non-empty value.
      Note: this array response does not carry a top-level ``warnings`` key;
      adding one requires a non-additive shape change tracked separately.
    """
    if format is not None and format.lower() == "json":
        # JSON branch: read usage/rc/ files via Python; project is required.
        if not project:
            raise HTTPException(
                status_code=HTTP_UNPROCESSABLE_ENTITY,
                detail="project is required when format=json is specified.",
            )
        live_root = resolve_kanban_root()
        project_root = live_root / "projects" / project

        # Determine scope type and value from the supplied filters.
        if rc:
            scope_type = "rc"
            scope_value = rc if rc.startswith("v") else f"v{rc}"
        elif month:
            scope_type = "month"
            scope_value = month
        elif day:
            scope_type = "day"
            scope_value = day
        else:
            # No scope filter: return all available RC rows.
            scope_type = "month"
            scope_value = ""  # costs_rows_for_scope handles empty prefix as "all"

        rows = costs_rows_for_scope(project_root, scope_type, scope_value)
        return JSONResponse(content=rows, status_code=200)

    # Text branch: unchanged shell-out path (byte-identical to pre-v1.6.0).
    flags: dict = {}
    if project:
        flags["project"] = project
    if month:
        flags["month"] = month
    if day:
        flags["day"] = day
    if rc:
        flags["rc"] = rc

    result = shell_out(_script_path(request, "cost-report.sh"), flags)
    return _make_envelope(result, warnings)


# ---------------------------------------------------------------------------
# GET /rejected
# ---------------------------------------------------------------------------


@router.get(
    "/rejected",
    summary="Inventory of quarantined (rejected) intake files",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr from list-rejected.sh."
    ),
)
def get_rejected(
    request: Request,
    project: Optional[str] = None,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return the output of ``list-rejected.sh``.

    Query parameters:

    - ``project`` (optional) — project name; omit to show all projects.

    Response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list[str]}``
    """
    flags: dict = {}
    if project:
        flags["project"] = project

    result = shell_out(_script_path(request, "list-rejected.sh"), flags)
    return _make_envelope(result, warnings)


# ---------------------------------------------------------------------------
# GET /projects
# ---------------------------------------------------------------------------


@router.get(
    "/projects",
    summary="List registered projects",
    response_description="List of project names in priority order.",
)
def get_projects(
    request: Request,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return the list of registered projects from the projects.cfg registry.

    No shell-out: reads projects.cfg directly using the Python INI parser.
    Projects are returned in priority order (ascending ``priority`` value),
    with registration order as the tie-break for equal-priority entries.

    Response body:
      ``{"projects": ["name1", "name2", ...], "warnings": list[str]}``

    HTTP status: always 200 (even when the list is empty).
    """
    kanban_root: pathlib.Path = request.app.state.api_cfg.kanban_root
    projects = _projects_cfg_list(kanban_root)
    return JSONResponse(
        content={"projects": projects, "warnings": warnings},
        status_code=200,
    )


# ---------------------------------------------------------------------------
# GET /dashboard/{pane}
# ---------------------------------------------------------------------------


@router.get(
    "/dashboard/{pane}",
    summary="Render a dashboard pane",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr from the pane's render script."
    ),
)
def get_dashboard_pane(
    pane: str,
    request: Request,
    project: Optional[str] = None,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return the rendered output for a named dashboard pane.

    Accepted pane names (case-sensitive):

    - ``input``         — Calls ``column-render.sh input`` (discovery-pipeline input column).
    - ``queue``         — Calls ``column-render.sh queue`` (agent queue column).
    - ``metrics``       — Calls ``dashboard/show-metrics.sh``.
    - ``attention``     — Calls ``dashboard/show-attention.sh``.
    - ``header``        — Calls ``dashboard/show-header.sh``.
    - ``status-window`` — Calls ``dashboard/show-status-window.sh``.

    Query parameters:

    - ``project`` (optional) — project name.  When supplied for
      ``column-render``-backed panes (input, queue), the script is invoked with
      ``--all-projects`` mode against the kanban root; for flag-style scripts the
      ``--project`` flag is passed through.

    Response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list[str]}``

    HTTP status: 422 when an unknown pane is requested; otherwise 200/500 from
    the underlying script.
    """
    if pane not in _VALID_PANES:
        valid = ", ".join(sorted(_VALID_PANES))
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY,
            detail=f"Unknown pane '{pane}'. Valid panes: {valid}.",
        )

    scripts_dir = _scripts_dir(request)
    kanban_root: pathlib.Path = request.app.state.api_cfg.kanban_root

    if pane in ("input", "queue"):
        # column-render.sh uses positional arguments, not --flag style.
        # Call: column-render.sh <input|queue> none [--kanban-root <root>] [--all-projects]
        script = str(scripts_dir / "dashboard" / "column-render.sh")
        argv = [
            script,
            pane,         # positional subcommand
            "none",       # placeholder for dir/backlog_file; --all-projects overrides it
            "--kanban-root", str(kanban_root),
            "--all-projects",
        ]
        completed = subprocess.run(argv, capture_output=True, text=True)
        result = ShellResult(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        return _make_envelope(result, warnings)

    # Flag-style dashboard scripts: metrics, attention, header, status-window
    script_rel = _PANE_SCRIPTS[pane]
    flags: dict = {}
    if project:
        flags["project"] = project

    result = shell_out(_script_path(request, script_rel), flags)
    return _make_envelope(result, warnings)


# ---------------------------------------------------------------------------
# GET /approvals
# ---------------------------------------------------------------------------


def _live_kanban_root() -> pathlib.Path:
    """Return the live kanban root from the environment.

    Delegates to the canonical resolver (:func:`pgai_agent_kanban.env.resolve_kanban_root`)
    so there is exactly one reader of ``PGAI_AGENT_KANBAN_ROOT_PATH`` in Python.

    Raises:
        RuntimeError: When ``PGAI_AGENT_KANBAN_ROOT_PATH`` is unset or empty.
    """
    return resolve_kanban_root()


def _is_registered_project(kanban_root: pathlib.Path, project_name: str) -> bool:
    """Return True when project_name exists as a directory under kanban_root/projects/.

    A project is considered registered when its directory exists under the
    projects/ layout.  This check is intentionally filesystem-based (not
    projects.cfg-based) so it works correctly in test fixtures that create
    project directories without a full projects.cfg.
    """
    projects_dir = kanban_root / "projects"
    if not projects_dir.is_dir():
        return False
    return (projects_dir / project_name).is_dir()


@router.get(
    "/approvals",
    summary="Pending HUMAN-APPROVE gate tasks",
    response_description=(
        "JSON array of pending approval records (ICD 1.1.0); empty array on a clean system. "
        "Each record includes review_cmds (ordered list of copy-paste-ready review command "
        "strings), approve_cmd, and reject_cmd."
    ),
)
def get_approvals(
    request: Request,
    project: Optional[str] = None,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return pending HUMAN-APPROVE gate tasks as a structured JSON array.

    Reads task state directly from the kanban filesystem — no shell-out.
    One implementation (collect_pending_approvals) backs both this endpoint
    and the window-14 dashboard renderer.

    Query parameters:

    - ``project`` (optional) — project name; when absent, all projects are
      aggregated (no default project, per house rule).  When supplied and the
      project does not exist, returns 422.

    Response body (HTTP 200):
      ``[{"task_id": str, "project": str, "state": str, "rc": str,
          "target_version": str, "age": str, "review": str,
          "review_cmds": list[str], "approve_cmd": str, "reject_cmd": str}, ...]``

      ``review_cmds`` is an ordered list of verbatim copy-paste-ready command
      strings: ``scripts/show.sh --project <p> --key <task-id>`` always first;
      ``scripts/show-test-report.sh --project <p> --key <rc>`` appended when the
      target RC version is known (starts with "v").  Always non-empty.

      Returns an empty array ``[]`` when no approvals are pending.

    Note: the ``warnings`` dependency is wired (unknown query params are
    captured) but the warnings list is not yet surfaced in this response.
    Array-shaped responses cannot carry a top-level ``warnings`` key without
    a non-additive shape change; that ICD bump is tracked separately.

    HTTP status:
      200 — success (including empty array).
      422 — project parameter supplied but project does not exist.
    """
    live_root = _live_kanban_root()

    if project is not None:
        # Validate that the project exists before scanning.
        if not _is_registered_project(live_root, project):
            raise HTTPException(
                status_code=HTTP_UNPROCESSABLE_ENTITY,
                detail=f"Unknown project '{project}'. No such project directory found.",
            )

    records = collect_pending_approvals(str(live_root), project=project)
    return JSONResponse(content=records, status_code=200)


# ---------------------------------------------------------------------------
# GET /auto-resolutions
# ---------------------------------------------------------------------------


@router.get(
    "/auto-resolutions",
    summary="OVERWATCH auto-resolution ledger entries",
    response_description=(
        "JSON array of auto-resolution ledger records; empty array when no "
        "autonomous resolutions have ever run. "
        "Each record includes evidence_paths (TESTER report / HALT file / "
        "release-state links) and ledger_path + entry_id (the ledger entry "
        "link) — the drilldown pair the browser Auto-Resolutions tab renders."
    ),
)
def get_auto_resolutions(
    request: Request,
    project: Optional[str] = None,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return OVERWATCH auto-resolution ledger entries as a structured JSON array.

    Reads the append-only auto-resolutions ledger
    (``projects/<name>/auto-resolutions.md``) directly from the kanban
    filesystem — no shell-out. One implementation
    (``collect_auto_resolutions`` in ``dashboard/scan_auto_resolutions.py``)
    backs both this endpoint and the human-review tmux window's
    Auto-Resolutions section, so the JSON body and the pane are guaranteed
    to agree on which resolution events exist.

    This surface is READ-ONLY: it never writes to the ledger and offers no
    approve/reject/mutate affordance — the surface informs, it never
    mutates state.

    Query parameters:

    - ``project`` (optional) — project name; when absent, all projects are
      aggregated (no default project, per house rule — same convention as
      ``GET /approvals``). When supplied and the project does not exist,
      returns 422.

    Response body (HTTP 200):
      ``[{"project": str, "rc": str, "attempt_from": int|null,
          "attempt_to": int|null, "ceiling": str, "attempt_display": str,
          "decision": str, "outcome": str, "trigger": str,
          "findings": list[str], "evidence_paths": list[str],
          "ledger_path": str, "entry_id": str, "family_key": str|null,
          "timestamp": str}, ...]``

      ``attempt_display`` is the "N/M" string (``attempt_from`` over the
      project's configured retry ceiling); ``ceiling`` renders "?" when the
      retry-ceiling config cannot be resolved for that project (a read-only
      display degrades gracefully rather than raising — same convention as
      the tmux renderer). ``outcome`` is the backfilled terminal state
      (e.g. "SHIPPED attempt 2") or the literal string "pending" while the
      bundle family has not yet reached a terminal state. ``evidence_paths``
      and ``ledger_path``/``entry_id`` together form the drilldown to the
      TESTER report and the ledger entry that the Auto-Resolutions tab
      renders — the ledger is the index, not the record, so evidence paths
      point at the underlying reports rather than duplicating their content.

      Records are newest-first within each project; projects are ordered
      alphabetically (same ordering as the tmux renderer).

      Returns an empty array ``[]`` when no auto-resolutions have ever run.

    HTTP status:
      200 — success (including empty array).
      422 — project parameter supplied but project does not exist.
    """
    live_root = _live_kanban_root()

    if project is not None:
        # Validate that the project exists before scanning.
        if not _is_registered_project(live_root, project):
            raise HTTPException(
                status_code=HTTP_UNPROCESSABLE_ENTITY,
                detail=f"Unknown project '{project}'. No such project directory found.",
            )

    records = collect_auto_resolutions(str(live_root), project=project)
    return JSONResponse(content=records, status_code=200)


# ---------------------------------------------------------------------------
# GET /projects/{project}/tasks/{key}/readme
# ---------------------------------------------------------------------------


def _has_readme_path_traversal(value: str) -> bool:
    """Return True when *value* contains a path-traversal sequence.

    Same detection logic the logs/traces routers apply to their own query
    parameters (see routers/logs.py and routers/traces.py ``_has_traversal``):
    checks raw and common URL-encoded traversal forms.  Used here to confine
    the ``project`` and ``key`` PATH parameters on GET
    ``/projects/{project}/tasks/{key}/readme`` before show-readme.sh is ever
    invoked — show-readme.sh's own key resolver concatenates the raw key into
    filesystem paths with no traversal sanitisation of its own, so this
    handler is the confinement gate for that script.
    """
    upper = value.upper()
    if ".." in value:
        return True
    if "/" in value:
        return True
    if "\\" in value:
        return True
    if "%2F" in upper:
        return True
    if "%5C" in upper:
        return True
    if "%2E" in upper:
        return True
    if "\x00" in value or "%00" in upper:
        return True
    return False


@router.get(
    "/projects/{project}/tasks/{key}/readme",
    summary="Fetch a task's README.md",
    response_description=(
        "JSON envelope with exit_code, stdout (README.md content), and stderr "
        "from show-readme.sh."
    ),
)
def get_task_readme(
    project: str,
    key: str,
    request: Request,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return a task's README.md content, byte-identical to show-readme.sh.

    Path parameters:

    - ``project`` (required) — project name.
    - ``key``     (required) — task key.  Prefix resolution is supported,
                               matching show.sh / show-readme.sh (an
                               agent+date+seq prefix suffices when it
                               resolves unambiguously).

    Response envelope (HTTP 200 on success):
      ``{"exit_code": 0, "stdout": "<README.md content>", "stderr": "", "warnings": [...]}``

    HTTP status:

    - 200 — the key resolved to a task and its README.md was found; ``stdout``
            carries the file content verbatim (byte-identical to
            ``show-readme.sh --project <project> --key <key>``).
    - 404 — no README resource exists at this (project, key): the key is not
            found, the key resolves to a ledger item (bug/priority/requirement
            — READMEs are task artifacts only, same refusal show-readme.sh
            applies), or the task directory has no README.md file.  The body
            carries ``{"error": "task readme not found", "project", "key",
            "detail"}`` with show-readme.sh's stderr message in ``detail``.
    - 422 — ``project`` or ``key`` contains a path-traversal sequence (``..``,
            ``/``, ``\\\\``, or their URL-encoded forms).  Returned before any
            subprocess is spawned or any file is opened.

    Security — path confinement:

    show-readme.sh resolves ``key`` via the same resolver used by show.sh,
    close.sh, and friends; that resolver concatenates the raw key into
    filesystem paths with no traversal sanitisation of its own.  This handler
    is therefore the confinement gate: ``project`` and ``key`` are checked for
    traversal sequences (raw and URL-encoded) BEFORE show-readme.sh is
    invoked — no user-supplied string is concatenated into a filesystem path
    by this handler, and a traversal-shaped value in either parameter is
    rejected before any subprocess is spawned.  This mirrors the discipline
    the logs and traces routers apply to their own query parameters.
    """
    if _has_readme_path_traversal(project) or _has_readme_path_traversal(key):
        return JSONResponse(
            content={
                "error": "invalid path parameter",
                "detail": (
                    "project and key must not contain path-traversal "
                    "sequences ('..', '/', '\\', or their URL-encoded forms)."
                ),
                "project": project,
                "key": key,
            },
            status_code=HTTP_UNPROCESSABLE_ENTITY,
        )

    flags: dict = {"project": project, "key": key}
    result = shell_out(_script_path(request, "show-readme.sh"), flags)

    if result.exit_code == 0:
        return JSONResponse(
            content={
                "exit_code": 0,
                "stdout": result.stdout,
                "stderr": "",
                "warnings": warnings,
            },
            status_code=200,
        )

    # Any non-zero exit from show-readme.sh means there is no README
    # resource at this (project, key): exit 3 is "key not found"; exit 1 is
    # either a ledger-item refusal (key is a bug/priority/requirement, not a
    # task) or a task with no README.md file.  All three collapse to the read
    # layer's 404 convention for GET .../readme — none of them is a usage
    # error, since project and key are both always supplied as path params.
    return JSONResponse(
        content={
            "error": "task readme not found",
            "project": project,
            "key": key,
            "detail": result.stderr.strip(),
        },
        status_code=404,
    )
