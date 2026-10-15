"""
reads.py — GET (read-only) endpoints for the pgai-agent-kanban operator API.

Implements every read endpoint of the operator REST API:

  GET /status             → pgai_agent_kanban.ops.read.read_status() over
                            get_status() (no shell-out); the function
                            kanban-status.sh reaches through pp_run_ops, with its
                            stdout/stderr/exit code in the envelope plus the
                            project's resolved ship_posture.
  GET /show               → pgai_agent_kanban.ops.read.get_task_status() (no
                            shell-out); the function show.sh reaches through
                            pp_run_ops, with show.sh's own stdout/stderr/exit code
                            -- see GET /show docstring.
  GET /test-report        → pgai_agent_kanban.ops.read.render_test_report() over
                            get_test_report() (no shell-out); the function
                            show-test-report.sh reaches through pp_run_ops.
  GET /metrics            → pgai_agent_kanban.lib.metrics.show_metrics() (no
                            shell-out); the function dashboard/show-metrics.sh
                            reaches through pp_run_ops, with its stdout/stderr/exit
                            code in the envelope -- see GET /metrics docstring.
  GET /metrics?format=json → lib.metrics.metrics_rows_for_project() (Python reader, no shell-out)
  GET /costs              → pgai_agent_kanban.lib.metrics.cost_report() (no
                            shell-out); the function cost-report.sh reaches through
                            pp_run_ops, given the query parameters as the script's
                            own --project/--month/--day/--rc arguments.
  GET /costs?format=json  → lib.metrics.costs_rows_for_scope() (Python reader, no shell-out)
  GET /rejected           → pgai_agent_kanban.ops.read.render_rejected() over
                            list_rejected() (no shell-out); the function
                            list-rejected.sh reaches through pp_run_ops.
  GET /projects           → registry read via _projects_cfg_list() (no shell-out)
  GET /agent-metrics      → pgai_agent_kanban.lib.agent_metrics.select_rc_span()/
                            compute_agent_averages() (through its pgai_agent_kanban.
                            lib.metrics re-export)/round_one_decimal() (Python reader,
                            no shell-out); per-agent, per-project one-decimal average
                            wall/tokens/tasks and total tokens/tasks over a span of
                            shipped RCs (window/last/all/since) -- the REST twin of
                            team/scripts/dashboard/agent-metrics.sh, calling the
                            EXACT SAME averaging function -- see GET /agent-metrics
                            docstring.
  GET /dashboard/{pane}   → pane-specific dashboard script (see _PANE_SCRIPTS table)
  GET /verdicts           → pgai_agent_kanban.ops.verdict.resolve_verdicts() (no
                            shell-out); per-project lane verdicts (RUNNING/BLOCKED/
                            HALTED/STUCK-LOCK/IDLE + cause + remedy) -- the function
                            why.sh and verdict-report.sh reach through pp_run_ops,
                            rendered as JSON by ops.verdict.verdicts_json() -- see
                            GET /verdicts docstring.
  GET /projects/{project}/tasks/{key}/readme
                          → pgai_agent_kanban.ops.read.render_task_readme() over
                            get_task_readme() (no shell-out); the function
                            show-readme.sh reaches through pp_run_ops. Returns a
                            task's README.md, byte-identical to the CLI tool.
  GET /show-queues        → pgai_agent_kanban.ops.read.get_queues() (no shell-out);
                            the REST twin of the "show-queues" operator verb -- the
                            function dashboard/show-queues.sh reaches through
                            pp_run_ops, rendered by the same
                            dashboard.queue_pane renderer (compact) or returned as
                            JSON (details=true) -- see GET /show-queues docstring.
  GET /verify-rc-state    → pgai_agent_kanban.ops.rc_state.verify_rc_state() (no
                            shell-out); the REST twin of the "verify-rc-state"
                            operator verb -- the function verify-rc-state.sh
                            reaches through pp_run_ops. <project> is a REQUIRED
                            query parameter, the script's positional argument
                            -- see GET /verify-rc-state docstring.
  GET /release-locks      → pgai_agent_kanban.ops.locks.list_locks() (no
                            shell-out), rendered by render_lock_listing(); the
                            function release-locks.sh (no flags) reaches through
                            pp_run_ops -- the REST twin of the "release-locks"
                            operator verb's READ-ONLY LISTING MODE only. The
                            mutating --release/--force half of that verb is POST
                            /operations/release-locks (routers/operations.py): a
                            GET route must never mutate state, so this endpoint
                            only lists and refuses (422, naming the POST twin) if
                            a caller supplies ?release= or ?force= -- see docstring.
  GET /release-state      → pgai_agent_kanban.ops.read.get_release_state()
                            (Python reader, no shell-out); the REST twin of the
                            "release-state" read verb -- returns release-state.md's
                            four validated fields for a project, through the SAME
                            schema-driven validating loader the CLI twin
                            (team/scripts/release-state.sh) calls -- see docstring.
  GET /deep-verify-status → pgai_agent_kanban.ops.read.get_deep_verify_status()
                            (Python reader, no shell-out); the REST twin of the
                            "deep-verify-status" read verb -- returns a project's
                            DEEP-VERIFY ledger entries and its OWN configured
                            cadence, through the SAME functions the CLI twin
                            (team/scripts/deep-verify-status.sh) calls -- see
                            docstring.

JSON format contract (?format=json on /metrics and /costs):
  Both JSON branches read the same underlying per-RC data files that the text renderers
  read, through the same pgai_agent_kanban.lib.metrics readers (sibling rule: one
  source, two formats).  The text path is byte-identical to the CLI's output when no
  format parameter is supplied.

  Row shape (fields present only when the underlying data has a non-empty value):
    version         str   — RC version string
    tasks           int   — total tasks in the RC
    wall_seconds    float — wall time in seconds (metrics only; absent when not populated)
    blocked_seconds float — blocked time in seconds (metrics only; history.csv's
                            blocked_minutes column * 60 — the same figure
                            show-metrics.sh's text "Blocked" column renders,
                            written at RC-close time by
                            pgai_agent_kanban.metrics.blocked_ledger's
                            blocked_minutes() resolver; absent when the
                            column predates this RC or the ledger could not
                            be read, never a fabricated zero)
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
    status-window.  input and queue require column_type (column-render.sh's
    --column-type) naming the board column to render, and refuse 422
    dual-channel without one.
  - /projects reads projects.cfg directly (no shell-out).
  - Script paths are resolved relative to cfg.kanban_root / "scripts".
  - Every ``project`` and ``key`` parameter, and /costs' ``rc`` selector,
    passes ``validation.require_component`` before anything is read: a value
    that is not one plain name (``..``, a path separator, NUL or another
    control character, a glob character ``* ? [ ]``, a leading ``-``) is
    refused 422 by ``_component_refusal`` with the dual-channel triple
    ``<verb>.<field>_invalid`` (``status.project_invalid``), a runnable curl
    remedy and the ``warnings`` list. The route's own required-parameter
    check runs first; an absent optional parameter is not checked.

Envelope format (from adapter.py):
  {"exit_code": int, "stdout": str, "stderr": str}

HTTP status mapping (adapter.http_status_for, through _read_http_status):
  exit_code == 0                          → 200 OK
  a refusal naming something missing     → 404 Not Found (an ops NotFound,
                                            a not-found error key, or the
                                            read scripts' exit code 3)
  a refusal for a conflicting state       → 409 Conflict
  a refusal for an invalid value          → 422 Unprocessable Entity
  any other non-zero                      → 500 Internal Server Error
  validation fail                         → 422 Unprocessable Entity

Security note: no authentication or TLS in this release.  Loopback-only binding
is the sole access-control mechanism.
"""

from __future__ import annotations

import os
import pathlib
import shlex
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .adapter import (
    HTTP_INTERNAL_SERVER_ERROR,
    HTTP_UNPROCESSABLE_ENTITY,
    OpsOutcome,
    ShellResult,
    HTTP_NOT_FOUND,
    ValidationError,
    ops_call,
    result_http_status,
    run_bounded,
    shell_out,
    validate_required,
)
from .dependencies import route_kanban_root, warn_unknown_query_params
from .validation import ComponentRefused, has_traversal, require_component
from ..board import COLUMN_KINDS, COLUMN_ORDER
from ..dashboard import queue_pane
from ..dashboard.queue_details import empty_queue_details
from ..dashboard.scan_auto_resolutions import collect_auto_resolutions
from ..dashboard.scan_human_approvals import collect_pending_approvals
from ..lib.agent_metrics import (
    RcSpan,
    round_one_decimal,
    select_rc_span,
)
from ..lib import ini
from ..lib.metrics import (
    color_enabled,
    compute_agent_averages,
    cost_report,
    costs_rows_for_scope,
    metrics_rows_for_project,
    show_metrics,
)
from ..lib.error_contract import dual_channel_from_text, parse_error_contract
from ..ops.context import OpsContext
from ..ops.errors import NotFound, Refused
from ..ops import locks as ops_locks
from ..ops import read as ops_read
from ..ops import resolve as ops_resolve
from ..ops import verdict as ops_verdict
from ..ops.rc_state import verify_rc_state
from ..ops.read import (
    RELEASE_STATE_REFUSAL_ERROR_KEY,
    RELEASE_STATE_REFUSAL_FALLBACK_REMEDY,
    get_deep_verify_status,
    get_release_state,
)

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
# uses positional arguments rather than --flag style.  Those panes are the
# board's two column kinds (pgai_agent_kanban.board.COLUMN_KINDS: "input"
# and "queue"), whose item statuses the board classifier defines.
_PANE_SCRIPTS: dict[str, str] = {
    "input": "dashboard/column-render.sh",       # positional-arg script
    "queue": "dashboard/column-render.sh",        # positional-arg script
    "metrics": "dashboard/show-metrics.sh",
    "attention": "dashboard/show-attention.sh",
    "header": "dashboard/show-header.sh",
    "status-window": "dashboard/show-status-window.sh",
}

_VALID_PANES = frozenset(_PANE_SCRIPTS)

#: The column-render.sh-backed panes: one per board column kind.
_COLUMN_PANES = frozenset(COLUMN_KINDS.values())

#: Per column pane, the ``column_type`` values it renders: the board's
#: column names of that kind, lower-cased into column-render.sh's
#: ``--column-type`` vocabulary, in board order (``COLUMN_ORDER``).
_PANE_COLUMN_TYPES: dict[str, tuple[str, ...]] = {
    pane: tuple(name.lower() for name in COLUMN_ORDER if COLUMN_KINDS[name] == pane)
    for pane in sorted(_COLUMN_PANES)
}

#: The machine key of the refusal a column pane returns when the request
#: names no column, or one that pane does not render.
DASHBOARD_COLUMN_REQUIRED_ERROR_KEY = "dashboard_pane.column_required"


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
        relative: Relative path within scripts/; e.g. ``"verify-rc-state.sh"``
                  or ``"dashboard/show-header.sh"``.

    Returns:
        Absolute script path string suitable for subprocess invocation.
    """
    return str(_scripts_dir(request) / relative)


def _make_envelope(
    result: ShellResult,
    warnings: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    """Convert a ShellResult into the standard JSON envelope response.

    The envelope format is:
      {"exit_code": int, "stdout": str, "stderr": str, "warnings": list[str]}

    The ``warnings`` field is always present.  It is an empty list on clean
    responses and carries one entry per unknown query parameter detected by
    the shared ``warn_unknown_query_params`` dependency.

    HTTP status is derived by :func:`_read_http_status`.

    Args:
        result:   The ShellResult envelope from the adapter.
        warnings: Warning strings collected by the route's dependency injection.
                  Defaults to an empty list when not supplied.
        extra:    Route-specific fields appended after the four envelope
                  fields, in the order given (e.g. GET /status's
                  ``ship_posture``). None adds nothing.

    Returns:
        A JSONResponse with the appropriate HTTP status and envelope body.
    """
    content: dict[str, Any] = {
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "warnings": warnings if warnings is not None else [],
    }
    if extra:
        content.update(extra)
    return JSONResponse(content=content, status_code=_read_http_status(result))


#: The exit code every read script (show.sh, show-readme.sh,
#: show-test-report.sh, release-locks.sh) and the ops CLI use for "nothing
#: matched".
_READ_NOT_FOUND_EXIT = 3


def _read_http_status(result: ShellResult) -> int:
    """HTTP status for a read route's result.

    ``result_http_status`` first -- the ops exception behind an ops call, or
    the refusal's own error key when stderr carries one. A read that
    otherwise exits with the read scripts' shared "nothing matched" code is
    404, not a server error.
    """
    triple = parse_error_contract(result.stderr) if result.exit_code else None
    status = result_http_status(result, (triple or {}).get("error", ""))
    if status == HTTP_INTERNAL_SERVER_ERROR and result.exit_code == _READ_NOT_FOUND_EXIT:
        return HTTP_NOT_FOUND
    return status


def _read_output_result(output: "ops_read.ReadOutput") -> ShellResult:
    """The envelope fields of a script-shaped ``ops.read`` render result."""
    return ShellResult(exit_code=output.exit_code, stdout=output.stdout, stderr=output.stderr)


# Example values the component refusal's remedy substitutes for a refused
# query or path field -- each one a plain name the field accepts.
_COMPONENT_EXAMPLES: dict[str, str] = {
    "project": "my-app",
    "key": "v1.2.3",
    "rc": "v1.2.3",
}


def _component_refusal(
    request: Request,
    verb: str,
    fields: dict[str, Optional[str]],
    warnings: list[str],
) -> Optional[JSONResponse]:
    """Refuse a read whose project, key or selector parameter is not one plain name.

    The one place every read route's confinement check lands (see
    ``validation.require_component`` for what is refused and why). Each
    entry of *fields* maps a query or path parameter name to the value the
    request carried; an absent value (``None`` or ``""``) is skipped, since
    whether a parameter is required is the route's own check. The first
    refused parameter returns HTTP 422 with the dual-channel triple
    ``<verb_snake>.<field>_invalid`` -- ``status.project_invalid``,
    ``show.key_invalid`` -- a message naming the parameter and what is wrong
    with it, a runnable curl remedy re-sending the request with an example
    value in its place, and the ``warnings`` list. Runs before any path is
    built, any file is read or any script is run.

    Returns:
        None when every supplied parameter is a plain name; otherwise the
        422 refusal response.
    """
    for field, value in fields.items():
        if value is None or value == "":
            continue
        try:
            require_component(value, field)
        except ComponentRefused as exc:
            example = _COMPONENT_EXAMPLES.get(field, "my-app")
            if field in request.path_params:
                path = request.url.path.replace(
                    f"/{request.path_params[field]}/", f"/{example}/", 1
                )
                corrected = request.url.replace(path=path)
            else:
                corrected = request.url.include_query_params(**{field: example})
            triple = exc.contract(verb, f"curl -sS {shlex.quote(str(corrected))}")
            # BLOCK-WORTHY (docs/blocking-site-census.md): an invariant by
            # design -- the parameter becomes a path component, a glob
            # prefix or an argv value, so a value that is not one plain name
            # could read outside the kanban root or change what runs.
            # Refused here, dual-channel, before anything is read.
            # CENSUS-ANCHOR: reads-field-not-component-01
            return JSONResponse(
                content={**triple, "warnings": warnings},
                status_code=HTTP_UNPROCESSABLE_ENTITY,
            )
    return None


def _projects_cfg_list(kanban_root: pathlib.Path) -> list[str]:
    """Return project names from projects.cfg in priority order.

    Delegates to ``pgai_agent_kanban.lib.ini.projects_registry``, the
    registry reader ``ops.read.list_projects`` and the ``projects_cfg_list``
    bash function in team/scripts/lib/projects.sh agree with: names sorted
    by priority (ascending), with registration order as the tie-break.

    Returns an empty list when projects.cfg is absent or contains no projects.

    Args:
        kanban_root: Resolved kanban root directory (from ApiConfig).

    Returns:
        List of project names in priority order.
    """
    return [entry.name for entry in ini.projects_registry(kanban_root)]


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------


@router.get(
    "/status",
    summary="Kanban status summary",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr of the status summary "
        "(the same text and exit code kanban-status.sh produces), plus the "
        "project's resolved ship_posture."
    ),
)
def get_status(
    request: Request,
    project: str,
    no_color: Optional[bool] = None,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return the status summary ``kanban-status.sh --project <name>`` prints.

    Calls ``pgai_agent_kanban.ops.read.read_status`` over
    ``ops.read.get_status`` -- the function kanban-status.sh reaches through
    ``pp_run_ops`` -- in-process; no subprocess is started for the script.
    Colour follows the server's own ``TERM`` / ``NO_COLOR`` the way the
    script follows its caller's (a server with no ``TERM`` renders plain
    text). The version lookups read local git refs only for a project whose
    ``project.cfg`` names a git dev tree (``Last:``) or a kanban root without
    a ``VERSION`` file (the version header); see
    ``pgai_agent_kanban.lib.released_version``.

    Query parameters:

    - ``project`` (required) — project name.
    - ``no_color`` (optional, bool) — when true, renders without colour, as
      ``--no-color`` does.

    Response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list[str],
      "ship_posture": str | null}``

    ``ship_posture`` is the project's resolved ship posture, ``"matrix"`` or
    ``"always"`` -- the value the summary's ``Ship posture:`` line shows, from
    the same read. It is resolved after the remote-lane guard: a project that
    pushes to origin with ``always`` set but not confirmed by
    ``ship_posture_confirm_remote = true`` reads ``"matrix"``, and the
    resolver's warning line is in ``stderr`` (as on the script's stderr).
    ``null`` when the read was refused (``exit_code`` non-zero).

    HTTP status:
      200 on exit_code 0, the refusal's own status on non-zero (404 / 409 /
      422, else 500), 422 on parameter validation failure.
    """
    try:
        validate_required({"project": project}, required_keys=["project"])
    except ValidationError as exc:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY, detail=exc.detail
        ) from exc

    refused = _component_refusal(request, "status", {"project": project}, warnings)
    if refused is not None:
        return refused

    ctx = OpsContext(kanban_root=request.app.state.api_cfg.kanban_root)
    read = ops_read.read_status(ctx, project, no_color=bool(no_color))
    ship_posture = read.snapshot.ship_posture if read.snapshot is not None else None
    return _make_envelope(
        _read_output_result(read.output), warnings, extra={"ship_posture": ship_posture}
    )


# ---------------------------------------------------------------------------
# GET /show
# ---------------------------------------------------------------------------


@router.get(
    "/show",
    summary="Show a task or intake item",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr of the show "
        "operation (the same text and exit code show.sh produces)."
    ),
)
def get_show(
    request: Request,
    project: str,
    key: str,
    file: Optional[str] = None,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return a task's or intake item's document, as ``show.sh`` prints it.

    Calls ``pgai_agent_kanban.ops.read.get_task_status`` -- the function
    show.sh reaches through ``pp_run_ops`` -- after resolving ``project``
    exactly as show.sh does (``ops.resolve.resolve_project_name``); no
    subprocess is started. ``stdout`` is the document, and ``stderr`` and
    ``exit_code`` carry what show.sh prints and exits with: the ambiguity
    notice for a key prefix matching several items (exit 0, first match
    emitted), ``project_resolve.*`` refusals (1), a key matching nothing
    (3), a task without the requested document (1).

    A key matching nothing live is read from the project's archive
    (``<kanban_root>/archive/<project>/``): ``stdout`` is the archived
    document, unchanged, and ``stderr`` carries the ``archived: <key> is
    not in the live project; read from the archive: <path>`` line (exit 0).
    A live item always wins over an archived one.

    Query parameters:

    - ``project`` (required) — project name or unambiguous prefix.
    - ``key``     (required) — task key or intake item identifier, or an
                               unambiguous prefix of one.
    - ``file``    (optional) — ``status`` (default) or ``readme``; selects
                               which file to emit for task keys.

    Response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list[str]}``

    HTTP status: 200 on exit_code 0, the refusal's own status on non-zero
    (404 / 409 / 422, else 500), 422 when ``project``
    or ``key`` is missing.
    """
    try:
        validate_required({"project": project, "key": key}, required_keys=["project", "key"])
    except ValidationError as exc:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY, detail=exc.detail
        ) from exc

    refused = _component_refusal(request, "show", {"project": project, "key": key}, warnings)
    if refused is not None:
        return refused

    document_file = file or "status"
    if document_file not in ops_read.TASK_DOCUMENT_FILES:
        # show.sh's own --file check, before it resolves anything: the same
        # stderr line and exit code, in the envelope.
        result = ShellResult(
            exit_code=1,
            stdout="",
            stderr=f'show.sh: --file value must be "status" or "readme"; got: {document_file}\n',
        )
        return _make_envelope(result, warnings)

    ctx = OpsContext(kanban_root=request.app.state.api_cfg.kanban_root)
    with ops_call(exit_code=1) as outcome:
        resolved = ops_resolve.resolve_project_name(ctx, project)
    if outcome.ok:
        with ops_call() as outcome:
            document = ops_read.get_task_status(ctx, resolved, key, document_file)
        if outcome.ok:
            outcome.stderr += document.ambiguity_notice()
            outcome.stderr += document.archived_notice()
            outcome.stdout += document.text
        elif outcome.exit_code == 3:
            outcome.stderr += f"show.sh: key not found: {key}\n"
    return _make_envelope(outcome.as_shell_result(), warnings)


# ---------------------------------------------------------------------------
# GET /test-report
# ---------------------------------------------------------------------------


@router.get(
    "/test-report",
    summary="Show a TESTER verification report",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr of the report read "
        "(the same text and exit code show-test-report.sh produces)."
    ),
)
def get_test_report(
    request: Request,
    project: str,
    key: str,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return the TESTER report ``show-test-report.sh --project <name> --key <key>`` prints.

    Calls ``pgai_agent_kanban.ops.read.render_test_report`` over
    ``ops.read.get_test_report`` -- the function show-test-report.sh reaches
    through ``pp_run_ops`` -- in-process; no subprocess is started.
    ``stdout`` is the report, ``stderr`` and ``exit_code`` what the script
    prints and exits with: 2 (report still in ``stdout``) for an ambiguous
    key prefix, 3 for a key or version matching no TESTER report, 1 for a
    project directory that does not exist.

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

    refused = _component_refusal(request, "test-report", {"project": project, "key": key}, warnings)
    if refused is not None:
        return refused

    ctx = OpsContext(kanban_root=request.app.state.api_cfg.kanban_root)
    output = ops_read.render_test_report(ctx, project, key)
    return _make_envelope(_read_output_result(output), warnings)


# ---------------------------------------------------------------------------
# GET /metrics
# ---------------------------------------------------------------------------


@router.get(
    "/metrics",
    summary="Historical RC metrics",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr of the rc-metrics view "
        "dashboard/show-metrics.sh prints, rendered in process by "
        "pgai_agent_kanban.lib.metrics.show_metrics(); or a JSON array of per-RC "
        "row objects (including blocked_seconds, the REST twin of the text "
        "renderer's Blocked column) when ?format=json is supplied."
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

    When ``format`` is omitted, calls ``pgai_agent_kanban.lib.metrics.show_metrics()``
    in process -- the function ``dashboard/show-metrics.sh`` reaches through
    ``pp_run_ops`` -- and returns its exit code, stdout and stderr in the
    standard text envelope, byte-identical to the script's own output. Colour
    follows the server's ``NO_COLOR`` and ``TERM`` the way the script does.

    When ``format=json`` is supplied and ``project`` is specified, returns a JSON
    array of per-RC row objects read from the same ``metrics/history.csv`` that
    the text renderer uses (sibling rule: one source, two formats).  A ``project``
    parameter is required for ``format=json``; omitting it returns 422.

    Query parameters:

    - ``project``   (optional) — project name; omit to iterate all projects (text only).
    - ``last``      (optional, int) — show last N rows (default: 10).
    - ``per_agent`` (optional, bool) — when true, the text renderer's
                    ``--per-agent`` view: the per-agent token breakdown of the
                    most recent RC (``project``, else the server's
                    ``PGAI_PROJECT_NAME``, is required for it).
                    Not applicable for ``format=json``.
    - ``format``    (optional) — when ``json``, returns a JSON array of row objects
                    instead of the text envelope.

    Text response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list[str]}``

    JSON response (``?format=json``):
      ``[{"version": str, "tasks": int, "wall_seconds": float, "blocked_seconds": float, "tokens_in": int, ...}, ...]``
      Fields present only when the underlying source has a non-empty value.
      ``blocked_seconds`` is the row's ``history.csv`` ``blocked_minutes``
      column converted to seconds -- the same figure the text renderer's
      "Blocked" column shows, resolved through
      ``pgai_agent_kanban.metrics.blocked_ledger.blocked_minutes()`` at
      RC-close time, never recomputed here from task folders.
      Note: this array response does not carry a top-level ``warnings`` key;
      adding one requires a non-additive shape change tracked separately.
    """
    refused = _component_refusal(request, "metrics", {"project": project}, warnings)
    if refused is not None:
        return refused

    if format is not None and format.lower() == "json":
        # JSON branch: read history.csv via Python; project is required.
        if not project:
            raise HTTPException(
                status_code=HTTP_UNPROCESSABLE_ENTITY,
                detail="project is required when format=json is specified.",
            )
        project_root = route_kanban_root(request) / "projects" / project
        rows = metrics_rows_for_project(project_root, last_n=last)
        return JSONResponse(content=rows, status_code=200)

    # Text branch: the rc-metrics view, rendered in process.
    rendered = show_metrics(
        route_kanban_root(request),
        project=project or "",
        last=str(last) if last is not None else "10",
        per_agent=bool(per_agent),
        use_color=color_enabled(False),
        env_project=os.environ.get("PGAI_PROJECT_NAME", ""),
    )
    result = ShellResult(exit_code=rendered.exit_code, stdout=rendered.stdout, stderr=rendered.stderr)
    return _make_envelope(result, warnings)


# ---------------------------------------------------------------------------
# GET /costs
# ---------------------------------------------------------------------------


@router.get(
    "/costs",
    summary="Cost report",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr of the report cost-report.sh "
        "prints, rendered in process by pgai_agent_kanban.lib.metrics.cost_report(); "
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

    When ``format`` is omitted, calls ``pgai_agent_kanban.lib.metrics.cost_report()``
    in process -- the function ``cost-report.sh`` reaches through ``pp_run_ops``
    -- with the query parameters as the script's own ``--project``/``--month``/
    ``--day``/``--rc`` arguments, and returns its exit code, stdout and stderr in
    the standard text envelope, byte-identical to the script's output. A roll-up
    the scope needs that is missing is reported as "no data": this handler starts
    no process, so it never runs the aggregator the CLI runs for one.

    When ``format=json`` is supplied, returns a bare JSON array of per-RC row
    objects, with no envelope and no ``warnings`` key.  A ``project``
    parameter is required for ``format=json``; omitting it returns 422.

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
    - ``format``  (optional) — when ``json``, returns the bare JSON array of
                               per-RC rows instead of the text envelope.

    Text response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list[str]}``

    JSON response (``?format=json``):
      ``[{"version": str, "tokens_in": int, "est_cost": float, ...}, ...]``
      Fields present only when the underlying source has a non-empty value.
      Note: this array response does not carry a top-level ``warnings`` key;
      adding one requires a non-additive shape change tracked separately.
    """
    refused = _component_refusal(request, "costs", {"project": project, "rc": rc}, warnings)
    if refused is not None:
        return refused

    if format is not None and format.lower() == "json":
        # JSON branch: read usage/rc/ files via Python; project is required.
        if not project:
            raise HTTPException(
                status_code=HTTP_UNPROCESSABLE_ENTITY,
                detail="project is required when format=json is specified.",
            )
        project_root = route_kanban_root(request) / "projects" / project

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

    # Text branch: the report, rendered in process from the script's arguments.
    argv: list[str] = []
    for flag, value in (("--project", project), ("--month", month), ("--day", day), ("--rc", rc)):
        if value:
            argv += [flag, value]
    rendered = cost_report(
        argv,
        default_kanban_root=str(route_kanban_root(request)),
        default_project=os.environ.get("PGAI_PROJECT_NAME", ""),
    )
    result = ShellResult(exit_code=rendered.exit_code, stdout=rendered.stdout, stderr=rendered.stderr)
    return _make_envelope(result, warnings)


# ---------------------------------------------------------------------------
# GET /agent-metrics
# ---------------------------------------------------------------------------

# The span query parameters, in the order a mutual-exclusivity refusal names
# them -- the same order and names as the CLI twin's --window / --last /
# --all / --since flags.
_AGENT_METRICS_SPAN_PARAMS = ("window", "last", "all", "since")

# The newest-N span used when no span parameter is given; the CLI twin's
# own default (--window 10).
_AGENT_METRICS_DEFAULT_WINDOW = 10


def _agent_metrics_span(
    request: Request,
    window: int,
    last: Optional[int],
    all_shipped: bool,
    since: Optional[str],
) -> RcSpan:
    """Build the RcSpan the /agent-metrics span parameters name.

    Mirrors the CLI twin's span flags rule for rule: at most one of
    ``window``, ``last``, ``all`` (when true) and ``since`` may be given;
    ``window`` and ``last`` must be positive integers; ``since`` must be a
    version of the form vX.Y.Z. ``window`` counts as given only when it is
    present in the query string (its default of 10 is not a choice the
    caller made). With no span parameter the span is the newest 10
    shipped RCs. Every violation raises HTTPException 422 whose detail
    names the parameter(s).
    """
    given = [
        name
        for name, present in zip(
            _AGENT_METRICS_SPAN_PARAMS,
            ("window" in request.query_params, last is not None, all_shipped, since is not None),
        )
        if present
    ]
    if len(given) > 1:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY,
            detail=(
                "window, last, all and since are mutually exclusive; give at most one "
                f"(got: {', '.join(given)})."
            ),
        )

    if last is not None:
        if last <= 0:
            raise HTTPException(
                status_code=HTTP_UNPROCESSABLE_ENTITY,
                detail=f"last must be a positive integer (got: {last}).",
            )
        return RcSpan.last(last)
    if all_shipped:
        return RcSpan.all_shipped()
    if since is not None:
        try:
            return RcSpan.since_version(since)
        except ValueError:
            raise HTTPException(
                status_code=HTTP_UNPROCESSABLE_ENTITY,
                detail=f"since must be a version of the form vX.Y.Z (got: {since}).",
            ) from None
    if window <= 0:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY,
            detail=f"window must be a positive integer (got: {window}).",
        )
    return RcSpan.last(window)


@router.get(
    "/agent-metrics",
    summary="Per-agent, per-project averages and totals over a span of shipped RCs (CLI/REST parity)",
    response_description=(
        "JSON object with 'project', 'window', 'span', 'shipped_count', "
        "'excluded_count' and an 'agents' array -- the REST twin of "
        "team/scripts/dashboard/agent-metrics.sh, computed by calling the SAME "
        "select_rc_span()/compute_agent_averages()/round_one_decimal() "
        "functions (pgai_agent_kanban.lib.agent_metrics) the CLI renders as text."
    ),
)
def get_agent_metrics(
    request: Request,
    project: str,
    window: int = _AGENT_METRICS_DEFAULT_WINDOW,
    last: Optional[int] = None,
    all: bool = False,  # noqa: A002 -- the query parameter mirrors the CLI's --all flag
    since: Optional[str] = None,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return per-agent average wall time, tokens and tasks, and total tokens
    and tasks, over a span of shipped RCs for one project.

    The REST twin of ``team/scripts/dashboard/agent-metrics.sh``: both
    surfaces select the span with ``select_rc_span()``, compute with
    ``compute_agent_averages()`` and round every average with
    ``round_one_decimal()``, all from ``pgai_agent_kanban.lib.agent_metrics``
    (the CLI's own ``team/scripts/lib/agent_metrics.py`` imports the same
    functions rather than defining copies), over the per-RC metrics rollups
    under ``projects/<name>/metrics/rc/v*.json``. This handler never
    re-derives the span, averaging or rounding arithmetic. Only rollups whose
    own ``outcome`` is ``shipped`` enter any figure; readable non-shipped
    rollups inside the span are counted in ``excluded_count``. Thousands
    separation is a presentation concern the CLI's renderer alone applies --
    every number here is a plain JSON number.

    Query parameters:

    - ``project`` (required) — project name. No registration check against
      ``projects.cfg`` is performed -- this exactly matches the CLI twin's
      own ``--project`` handling, which accepts any name and renders the
      "no metrics yet" placeholder (here: an empty ``agents`` array) when
      the project has no ``metrics/rc/`` directory, whether because the
      name is unregistered or because the project has not shipped an RC
      yet. Missing entirely returns 422.
    - ``window`` (optional, int) — the newest N shipped RCs (default: 10,
      the CLI's own ``--window`` default). A non-positive value returns 422.
    - ``last`` (optional, int) — the newest N shipped RCs; the same span as
      ``window`` (the CLI's ``--last N``). A non-positive value returns 422.
    - ``all`` (optional, bool) — when true, every shipped RC (the CLI's
      ``--all``). ``all=false`` selects nothing and is the same as omitting it.
    - ``since`` (optional, str) — every shipped RC at or above ``vX.Y.Z`` by
      semantic-version order (the CLI's ``--since vX.Y.Z``; the leading
      ``v`` is optional). A value that is not a version returns 422.

    At most one of ``window``, ``last``, ``all=true`` and ``since`` may be
    given -- the CLI's span flags are mutually exclusive the same way; two
    or more return 422 naming them. ``window`` counts as given only when it
    appears in the query string.

    Response body (HTTP 200):
      ``{"project": str, "window": int,
         "span": {"kind": "last"|"all"|"since",
                  "count": int|null, "since": str|null},
         "shipped_count": int, "excluded_count": int,
         "agents": [{"agent": str, "avg_wall_minutes": float|null,
                      "avg_tokens": float, "avg_tasks": float,
                      "rc_count": int, "total_tokens": int,
                      "total_tasks": int},
                     ...],
         "warnings": [...]}``

      ``window`` is the span's RC bound: N for a ``window``/``last`` span
      (10 by default), and ``shipped_count`` for an ``all`` or ``since``
      span. ``span`` names the selected span: ``kind`` is ``last`` with
      ``count`` N, ``all``, or ``since`` with ``since`` the version bound
      as given; the unused member is ``null``. ``shipped_count`` is the
      number of shipped RCs the span selected (it can be below N when
      fewer have shipped) and ``excluded_count`` the number of non-shipped
      rollups inside the span's range -- the two numbers the CLI's footer
      narrates ("window: 5 shipped RCs (1 cancelled excluded)").

      ``agents`` is sorted by agent name ascending (matches the CLI
      renderer's own sort). An agent with zero invocations in the span is
      simply absent -- never a zero-average or a division-by-zero entry.
      ``avg_wall_minutes``, ``avg_tokens`` and ``avg_tasks`` carry exactly
      one decimal, rounded half away from zero by the shared one-decimal
      rule the CLI uses; ``total_tokens`` and ``total_tasks`` are raw
      integer sums over the agent's invocation RCs (``rc_count``).
      ``avg_wall_minutes`` is a mean PER TASK (a different denominator from
      ``avg_tokens``/``avg_tasks``, which are each a mean per invocation
      RC): it pools each invocation RC's own ``wall``.``by_agent`` entry
      for the agent and is ``null`` when none of those RCs carry one (the
      CLI renders that case as ``--``). ``agents`` is an empty list when
      the span holds no shipped rollup -- the same placeholder condition
      the CLI renders as "(no metrics yet)".

    HTTP status:
      200 — success, including an empty ``agents`` array.
      422 — ``project`` is missing or empty; ``window`` or ``last`` is not
            a positive integer; ``since`` is not a vX.Y.Z version; or more
            than one of ``window``, ``last``, ``all=true`` and ``since`` is
            given.
    """
    try:
        validate_required({"project": project}, required_keys=["project"])
    except ValidationError as exc:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY, detail=exc.detail
        ) from exc

    refused = _component_refusal(request, "agent-metrics", {"project": project}, warnings)
    if refused is not None:
        return refused

    span = _agent_metrics_span(request, window, last, all, since)

    live_root = route_kanban_root(request)
    rc_dir = live_root / "projects" / project / "metrics" / "rc"

    selection = select_rc_span(rc_dir, span)
    averages = compute_agent_averages(selection.rollups)

    agents_out: list[dict] = []
    for agent_name in sorted(averages.keys()):
        a = averages[agent_name]
        agents_out.append(
            {
                "agent": agent_name,
                "avg_wall_minutes": round_one_decimal(a["avg_wall_minutes"]),
                "avg_tokens": round_one_decimal(a["avg_tokens"]),
                "avg_tasks": round_one_decimal(a["avg_tasks"]),
                "rc_count": a["rc_count"],
                "total_tokens": a["total_tokens"],
                "total_tasks": a["total_tasks"],
            }
        )

    return JSONResponse(
        content={
            "project": project,
            "window": span.count if span.count is not None else selection.shipped_count,
            "span": {"kind": span.kind, "count": span.count, "since": span.since},
            "shipped_count": selection.shipped_count,
            "excluded_count": selection.excluded_count,
            "agents": agents_out,
            "warnings": warnings,
        },
        status_code=200,
    )


# ---------------------------------------------------------------------------
# GET /rejected
# ---------------------------------------------------------------------------


@router.get(
    "/rejected",
    summary="Inventory of quarantined (rejected) intake files",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr of the quarantine "
        "inventory (the same text and exit code list-rejected.sh produces)."
    ),
)
def get_rejected(
    request: Request,
    project: Optional[str] = None,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return the quarantine inventory ``list-rejected.sh [--project <name>]`` prints.

    Calls ``pgai_agent_kanban.ops.read.render_rejected`` over
    ``ops.read.list_rejected`` -- the function list-rejected.sh reaches
    through ``pp_run_ops`` -- in-process; no subprocess is started. An
    unknown project, or a kanban root with no ``projects/`` directory,
    reports exit code 1 with the script's message in ``stderr``.

    Query parameters:

    - ``project`` (optional) — project name; omit to show all projects.

    Response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list[str]}``
    """
    refused = _component_refusal(request, "rejected", {"project": project}, warnings)
    if refused is not None:
        return refused

    ctx = OpsContext(kanban_root=request.app.state.api_cfg.kanban_root)
    output = ops_read.render_rejected(ctx, project or None)
    return _make_envelope(_read_output_result(output), warnings)


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


def _dashboard_column_refusal(
    request: Request,
    pane: str,
    column_type: Optional[str],
    accepted: tuple[str, ...],
    warnings: list[str],
) -> dict:
    """Return the dual-channel body refusing a column pane request.

    The body carries the same three keys every API-layer refusal carries
    (``error``/``message``/``remedy``) plus the accumulated ``warnings``;
    ``exit_code``/``stdout``/``stderr`` are absent because no script ran.

    Args:
        request:     The refused request; the remedy re-sends its own URL so
                     it targets the server the caller actually reached.
        pane:        The column pane requested (``input`` or ``queue``).
        column_type: The raw ``column_type`` value, or None when absent.
        accepted:    The column types this pane renders, in board order.
        warnings:    Unknown-parameter warnings already accumulated.
    """
    choices = ", ".join(accepted)
    if column_type is None or not column_type.strip():
        problem = "names no board column"
    else:
        problem = f"names column_type {column_type.strip()!r}, which is not a {pane} column"
    message = (
        f"GET /dashboard/{pane} {problem}; nothing was rendered. The {pane} pane "
        f"draws one board column at a time -- pass column_type as one of: {choices}."
    )
    example_url = request.url.include_query_params(column_type=accepted[0])
    return {
        "error": DASHBOARD_COLUMN_REQUIRED_ERROR_KEY,
        "message": message,
        "remedy": f"curl -sS '{example_url}'",
        "warnings": warnings,
    }



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
    column_type: Optional[str] = None,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return the rendered output for a named dashboard pane.

    Accepted pane names (case-sensitive):

    - ``input``         — Calls ``column-render.sh input`` (discovery-pipeline input column).
    - ``queue``         — Calls ``column-render.sh queue`` (agent queue column).

    ``input`` and ``queue`` are the board's two column kinds
    (``pgai_agent_kanban.board.COLUMN_KINDS``); the board classifier there
    defines the status each column item carries.
    - ``metrics``       — Calls ``dashboard/show-metrics.sh``.
    - ``attention``     — Calls ``dashboard/show-attention.sh``.
    - ``header``        — Calls ``dashboard/show-header.sh``.
    - ``status-window`` — Calls ``dashboard/show-status-window.sh``.

    Query parameters:

    - ``project`` (optional) — project name.  When supplied for
      ``column-render``-backed panes (input, queue), the script is invoked with
      ``--all-projects`` mode against the kanban root; for flag-style scripts the
      ``--project`` flag is passed through.
    - ``column_type`` (required for ``input`` and ``queue``) — the board
      column to render, passed through as column-render.sh's
      ``--column-type``.  ``input`` renders ``bugs``, ``priorities`` or
      ``requirements``; ``queue`` renders ``pm``, ``coder``, ``writer``,
      ``tester`` or ``cm``.  Matched case-insensitively, so a ``GET /board``
      column name (``CODER``) is accepted too.  The response is the text
      ``column-render.sh <pane> none --all-projects --column-type <column>``
      prints for the server's kanban root: one row per item across every
      registered project, drawn in the colour of the status ``GET /board``
      reports for it.  Ignored, with a warning, by the other panes.

    Response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list[str]}``

    Response body on refusal (``input`` or ``queue`` with ``column_type``
    missing or not one the pane renders):
      ``{"error": "dashboard_pane.column_required", "message": str,
      "remedy": str, "warnings": list[str]}`` -- the message names the
      columns the pane accepts and the remedy is a runnable ``curl`` line
      re-sending the request with one of them.  No script is run.

    HTTP status: 422 when an unknown pane is requested, or a column pane is
    requested without a column it renders; otherwise 200/500 from the
    underlying script.
    """
    if pane not in _VALID_PANES:
        valid = ", ".join(sorted(_VALID_PANES))
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY,
            detail=f"Unknown pane '{pane}'. Valid panes: {valid}.",
        )

    refused = _component_refusal(request, "dashboard-pane", {"project": project}, warnings)
    if refused is not None:
        return refused

    scripts_dir = _scripts_dir(request)
    kanban_root: pathlib.Path = request.app.state.api_cfg.kanban_root

    if pane in _COLUMN_PANES:
        accepted = _PANE_COLUMN_TYPES[pane]
        column = (column_type or "").strip().lower()
        if column not in accepted:
            # BLOCK-WORTHY (docs/blocking-site-census.md): an invariant by
            # design -- a column pane renders exactly one board column, and in
            # --all-projects mode column-render.sh gathers nothing for a
            # column type it does not know, so rendering anyway would answer
            # 200 with an "(empty)" pane indistinguishable from an empty
            # board. Refused here, dual-channel, before any script runs.
            # CENSUS-ANCHOR: reads-dashboard-column-required-01
            return JSONResponse(
                content=_dashboard_column_refusal(request, pane, column_type, accepted, warnings),
                status_code=HTTP_UNPROCESSABLE_ENTITY,
            )
        # column-render.sh uses positional arguments, not --flag style.
        # Call: column-render.sh <input|queue> none --kanban-root <root>
        #       --all-projects --column-type <column>
        script = str(scripts_dir / "dashboard" / "column-render.sh")
        argv = [
            script,
            pane,         # positional subcommand
            "none",       # placeholder for dir/backlog_file; --all-projects overrides it
            "--kanban-root", str(kanban_root),
            "--all-projects",
            "--column-type", column,
        ]
        result = run_bounded(argv, timeout_seconds=request.app.state.api_cfg.shell_out_timeout_seconds)
        return _make_envelope(result, warnings)

    # Flag-style dashboard scripts: metrics, attention, header, status-window
    if column_type is not None:
        warnings.append(
            f"ignored parameter: column_type (only the {' and '.join(sorted(_COLUMN_PANES))} "
            "panes render a board column)"
        )
    script_rel = _PANE_SCRIPTS[pane]
    flags: dict = {}
    if project:
        flags["project"] = project

    result = shell_out(
        _script_path(request, script_rel), flags,
        timeout_seconds=request.app.state.api_cfg.shell_out_timeout_seconds,
    )
    return _make_envelope(result, warnings)


# ---------------------------------------------------------------------------
# GET /approvals
# ---------------------------------------------------------------------------


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
    refused = _component_refusal(request, "approvals", {"project": project}, warnings)
    if refused is not None:
        return refused

    live_root = route_kanban_root(request)

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
    refused = _component_refusal(request, "auto-resolutions", {"project": project}, warnings)
    if refused is not None:
        return refused

    live_root = route_kanban_root(request)

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
# GET /verdicts
# ---------------------------------------------------------------------------


@router.get(
    "/verdicts",
    summary="Per-project lane verdicts (CLI/REST parity)",
    response_description=(
        "JSON object with a 'verdicts' array (one entry per registered "
        "project, in projects.cfg priority order) and a 'warnings' array. "
        "Every entry is resolved by pgai_agent_kanban.ops.verdict."
        "resolve_verdicts, the function team/scripts/why.sh reaches through "
        "pp_run_ops -- the CLI and this endpoint can never disagree about "
        "what a lane's verdict is."
    ),
)
def get_verdicts(
    request: Request,
    project: Optional[str] = None,
    details: Optional[bool] = None,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return every registered project's lane verdict as JSON.

    The REST twin of the ``why`` verb. Calls
    ``pgai_agent_kanban.ops.verdict.resolve_verdicts`` -- the function
    ``why.sh`` and ``verdict-report.sh`` reach through ``pp_run_ops`` -- in
    process and returns ``ops.verdict.verdicts_json`` of its report; no
    process is started and no script output is parsed.

    ``details=true`` extends the same parity guarantee to why.sh's
    ``--details`` diagnostic pane -- release locks, queue tails, and the
    resolved posture: the JSON and ``why.sh --details``' text sections are
    rendered from the same resolved details.

    Query parameters:

    - ``project`` (optional) — project name; when absent, every project
      registered in ``projects.cfg`` is returned, in the same priority
      order ``why.sh`` (no ``--project``) walks them. When supplied and the
      name is not registered in ``projects.cfg`` at all, returns 422. When
      supplied and the name IS registered but its ``projects/<name>``
      directory does not exist on disk, returns 200 with a single
      "unresolved" entry (see below) — a known registry entry with a
      missing directory is a diagnostic condition, not an unknown name.
    - ``details`` (optional, bool) — when true, adds ``posture``,
      ``release_locks``, and ``queue_tails`` to the response (see below).
      Requires ``project`` — a posture/lock/queue report is always
      per-project, exactly like ``why.sh --project NAME --details`` itself
      requires ``--project``; omitting it returns 422.

    Response body (HTTP 200, ``details`` absent or false):
      ``{"verdicts": [...], "warnings": [...]}``

    Response body (HTTP 200, ``details=true``, project resolved):
      ``{"verdicts": [...], "posture": {...}, "release_locks": [...],
         "queue_tails": [...], "warnings": [...]}``. ``posture``,
      ``release_locks``, and ``queue_tails`` are omitted when the named
      project is "unresolved" (registered but no directory on disk) — there
      is nothing to detail for a project with no directory, matching
      ``why.sh --details``' own refusal for that case.

      ``posture`` — ``{"halt_state": "normal"|"halted"|"draining",
      "halt_event": str (present only when halt_state is not "normal" and
      an event was recorded), "deferred_bugs": {"value": str, "tier": str},
      "deferred_bug_level": {"value": str, "tier": str}, "defer_piggyback":
      {"value": str, "tier": str}, "until": {"value": str, "tier": str,
      "campaign_file": str (present only when tier is "campaign file")},
      "active_rc": "none"|"vX.Y.Z", "pending_bundles": [{"name": str,
      "status": str}, ...]}``.

      ``release_locks`` — one entry per lock held for the project:
      ``{"path": str, "age_seconds": int, "held_seconds": int|null,
      "provenance": str, "stale_threshold_seconds": int,
      "stale_threshold_derivation": str, "violation": bool}``. Empty when
      the project holds no locks. ``held_seconds`` is how long the lock
      has been held, computed from the lock record's ``acquired_at`` (the
      same value ``why.sh --details`` renders as ``held: <duration>``);
      it is ``null`` when the record carries no ``acquired_at`` (a
      pre-upgrade lock: "acquired_at not recorded"). ``age_seconds`` is
      the seconds since the lock file was last refreshed (its mtime) — a
      heartbeat age the held-lock refresh keeps resetting, not how long
      the lock has been held.

      ``queue_tails`` — one entry per ``tasks/queues/*_backlog.md`` file:
      ``{"agent": str, "entries": [{"marker": str, "task_id": str,
      "status_state": str}, ...]}``. ``entries`` is empty for a queue file
      with no matching lines. Empty overall when the project has no
      ``tasks/queues/`` directory at all.

    Each entry in ``verdicts`` is one of two shapes:

      Resolved (the project directory exists and a verdict was reached):
        ``{"project": str, "verdict": "RUNNING"|"BLOCKED"|"HALTED"|"STUCK-LOCK"|"IDLE",
        "exit_code": int, "cause": str, "remedy": str, "line": str,
        "task_id": str, "task_state": str, "minutes_in_state": int}``.
        ``task_id``/``task_state`` are present only for RUNNING and BLOCKED
        (the only two verdicts naming an active task); ``minutes_in_state``
        is present only for RUNNING. A field with no content for a given
        project is omitted — never null or zero-filled. STALLED is not a
        distinct verdict word: a RUNNING task past the stall threshold keeps
        ``verdict: "RUNNING"`` with ``exit_code: 3`` (see
        ``pgai_agent_kanban.ops.verdict``) — this same entry shape is the
        "stall verdict" data ``details=true`` adds no separate field for.

      Unresolved (the project is registered in ``projects.cfg`` but has no
      directory on disk — a diagnostic condition distinct from any of the
      five verdict words; never silently reported as a false IDLE, which is
      what an empty task scan against a nonexistent directory would read
      as):
        ``{"project": str, "error": "project directory not found",
        "cause": str, "remedy": str}``.

    HTTP status:
      200 — success, including when individual entries are "unresolved".
      422 — ``?project=`` names a value that is not registered in
            ``projects.cfg`` at all, or ``?details=true`` is given without
            ``?project=``.
      500 — ``details=true`` and the project's deferral posture does not
            resolve (a malformed DEFER-LOW-BUGS campaign file or an invalid
            ``[release]`` value): ``detail`` carries the resolver's
            dual-channel error/message/remedy block, the refusal
            ``verdict-report.sh --details`` and ``why.sh --details`` print.

    Security: every string in the response is composed from project names,
    task ids/states read from status.md, minute counts, and the resolver's
    own cause/remedy sentences — no credential, token, or absolute path
    outside the kanban root is ever included. A HALT-file path named in a
    HALTED entry's cause/line is inside the kanban root — the same path
    why.sh's own CLI line already prints.
    """
    # Data (projects.cfg, project directories) is read from the server's own
    # kanban root (route_kanban_root), the one root every route reads --
    # load_api_config resolved it from PGAI_AGENT_KANBAN_ROOT_PATH at
    # startup, so it is the root the CLI twin reads.
    refused = _component_refusal(request, "verdicts", {"project": project}, warnings)
    if refused is not None:
        return refused

    if details and not project:
        # Mirrors why.sh --details' own refusal: a posture/lock/queue
        # report is always per-project.
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY,
            detail=(
                "?details=true requires ?project=<name> -- the diagnostic "
                "pane is always per-project."
            ),
        )

    live_root = route_kanban_root(request)
    registered = _projects_cfg_list(live_root)

    if project is not None and project not in registered:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY,
            detail=f"Unknown project '{project}'. Not registered in projects.cfg.",
        )

    if project is None and not registered:
        # No project named and nothing registered at all -- an empty walk,
        # not an error. Mirrors get_projects()'s "always 200, even empty".
        return JSONResponse(content={"verdicts": [], "warnings": warnings}, status_code=200)

    report = ops_verdict.resolve_verdicts(
        live_root, [project] if project else registered, details=bool(details)
    )
    if report.details is not None and report.details.posture_error is not None:
        raise HTTPException(
            status_code=HTTP_INTERNAL_SERVER_ERROR,
            detail=f"the deferral posture for '{project}' did not resolve:\n{report.details.posture_error}",
        )
    return JSONResponse(content={**ops_verdict.verdicts_json(report), "warnings": warnings}, status_code=200)


# ---------------------------------------------------------------------------
# GET /projects/{project}/tasks/{key}/readme
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project}/tasks/{key}/readme",
    summary="Fetch a task's README.md",
    response_description=(
        "JSON envelope with exit_code, stdout (README.md content), and stderr "
        "of the README read (the same content show-readme.sh prints)."
    ),
)
def get_task_readme(
    project: str,
    key: str,
    request: Request,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return a task's README.md content, byte-identical to show-readme.sh.

    Calls ``pgai_agent_kanban.ops.read.render_task_readme`` over
    ``ops.read.get_task_readme`` -- the function show-readme.sh reaches
    through ``pp_run_ops`` -- in-process; no subprocess is started.

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
            path is built or any file is opened.

    Security — path confinement:

    ``get_task_readme`` resolves ``key`` via the same resolver used by
    show.sh, close.sh, and friends; that resolver concatenates the raw key
    into filesystem paths with no traversal sanitisation of its own.  This
    handler is therefore the confinement gate: ``project`` and ``key`` are
    checked for traversal sequences (raw and URL-encoded) BEFORE the read
    runs — a traversal-shaped value in either parameter is rejected before
    any path is built or file opened.  This mirrors the discipline the logs
    and traces routers apply to their own query parameters.
    """
    if has_traversal(project) or has_traversal(key):
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

    refused = _component_refusal(request, "task-readme", {"project": project, "key": key}, warnings)
    if refused is not None:
        return refused

    ctx = OpsContext(kanban_root=request.app.state.api_cfg.kanban_root)
    result = ops_read.render_task_readme(ctx, project, key)

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

    # Any non-zero exit from the read means there is no README
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


# ---------------------------------------------------------------------------
# GET /show-queues
# ---------------------------------------------------------------------------


@router.get(
    "/show-queues",
    summary="Queue-state pane for one or every registered project (read-only)",
    response_description=(
        "Compact mode (no details=true): JSON envelope whose stdout is the "
        "queue pane show-queues.sh prints. details=true: a structured "
        "'details' body -- see docstring."
    ),
)
def get_show_queues(
    request: Request,
    project: Optional[str] = None,
    details: Optional[bool] = None,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return show-queues.sh's queue-state pane, compact or per-agent detail.

    The REST twin of the "show-queues" operator verb -- the same queue-state
    pane the tmux dashboard's queues pane and ``why.sh --details`` embed.
    Both modes call ``pgai_agent_kanban.ops.read.get_queues`` -- the
    function show-queues.sh reaches through ``pp_run_ops`` -- in process;
    no subprocess is started.

    Compact mode (``details`` absent or false) resolves ``project`` exactly
    as show-queues.sh does (``ops.resolve.resolve_project_name``) and
    renders ``get_queues``' result through
    ``pgai_agent_kanban.dashboard.queue_pane`` -- the renderer the CLI uses
    -- so ``stdout`` is the pane show-queues.sh prints (colour follows the
    server's ``TERM``/``NO_COLOR``; with no ``project`` the counters come
    from the server's ``PGAI_PROJECT_NAME`` project). With no ``project``
    and no ``PGAI_PROJECT_NAME`` on the server there is no project to read,
    so the request refuses with ``show_queues.no_project``: exit_code 1,
    empty ``stdout``, the dual-channel block in ``stderr``, and no pane --
    the route never renders counters it did not read.

    ``details=true`` returns ``get_queues``' ``details`` -- the
    ``queue_details.resolve_queue_details`` structure the CLI's
    ``--details`` listing renders -- as JSON rather than coloured text.

    Query parameters:

    - ``project`` (optional) — project to render.
      - Compact mode: a registered name or unambiguous prefix.
      - ``details=true``: a single project's shape when given; every
        registered project, nested by name, when omitted (iterate-all,
        matching the CLI's own ``--details`` behavior with no ``--project``).
    - ``details`` (optional, bool) — per-agent, per-state task listing
      instead of the compact one-line-per-agent summary.

    Note: the script's own ``--kanban-root`` override flag is dashboard-
    internal plumbing and is deliberately NOT exposed as a query parameter —
    every REST invocation renders against the server's own configured kanban
    root, never a caller-supplied path.

    Response body, compact mode:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list[str]}``

    Response body, ``details=true``, ``project`` given:
      ``{"project": str, "details": {agent: {state: [{"task_id": str,
      "warning": str|null}, ...]}}, "warnings": list[str]}`` -- every
      ``AGENT_ORDER`` x ``STATE_ORDER`` key present (see
      ``queue_details.resolve_queue_details``'s own docstring for the
      warning vocabulary).

    Response body, ``details=true``, ``project`` omitted:
      ``{"project": null, "details": {<project-name>: {agent: {state:
      [...]}}}, "warnings": list[str]}``.

    HTTP status: 200 on success. Compact mode refuses with exit_code 1, an
    empty ``stdout`` and the dual-channel block in ``stderr``: 404 when
    ``project`` names no registered project (``project_resolve.not_found``)
    or no project directory (``show_queues.project_directory_missing``);
    409 when ``project`` is an ambiguous prefix
    (``project_resolve.ambiguous``), or is omitted and the server has no
    ``PGAI_PROJECT_NAME`` (``show_queues.no_project``). ``details=true``:
    404 when an explicit ``project`` does not resolve to a registered
    project. Either mode: 422 when ``project`` is not one plain name.
    """
    refused = _component_refusal(request, "show-queues", {"project": project}, warnings)
    if refused is not None:
        return refused

    kanban_root: pathlib.Path = request.app.state.api_cfg.kanban_root
    ctx = OpsContext(kanban_root=kanban_root)

    if details:
        if project:
            project_root = ctx.project_root(project)
            if not project_root.exists():
                return JSONResponse(
                    content={
                        "project": project,
                        "error": "show_queues.project_not_found",
                        "message": f"Project not found: {project!r} (expected at {project_root})",
                        "remedy": (
                            "Check the project name against projects.cfg, or create "
                            f"it first with team/scripts/create-project.sh --project {project}."
                        ),
                        "warnings": warnings,
                    },
                    status_code=404,
                )
            body = {
                "project": project,
                "details": ops_read.get_queues(ctx, project)["details"],
                "warnings": warnings,
            }
            return JSONResponse(content=body, status_code=200)

        # No --project: iterate every registered project, nested by name --
        # matching show-queues.sh --details' own iterate-all behavior. A
        # registered project with no directory lists no task.
        all_details: dict = {}
        for name in _projects_cfg_list(kanban_root):
            all_details[name] = (
                ops_read.get_queues(ctx, name)["details"]
                if ctx.project_root(name).is_dir()
                else empty_queue_details()
            )
        return JSONResponse(
            content={"project": None, "details": all_details, "warnings": warnings},
            status_code=200,
        )

    resolved = ""
    outcome = OpsOutcome()
    if project:
        with ops_call(exit_code=1) as outcome:
            resolved = ops_resolve.resolve_project_name(ctx, project)
    if outcome.ok:
        data_project = resolved or os.environ.get("PGAI_PROJECT_NAME", "")
        with ops_call(exit_code=1) as outcome:
            plan = queue_pane.plan_queue_pane(ctx, resolved, data_project, False)
            queues = {name: ops_read.get_queues(ctx, name) for name in plan.fetch}
            pane = queue_pane.render_queue_pane(ctx, plan, queues, environ=os.environ, now=time.time())
        if outcome.ok:
            outcome.stdout += pane.stdout
            outcome.stderr += pane.stderr
    return _make_envelope(outcome.as_shell_result(), warnings)


# ---------------------------------------------------------------------------
# GET /verify-rc-state
# ---------------------------------------------------------------------------


@router.get(
    "/verify-rc-state",
    summary="Read-only consistency checker for a project's RC state",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr; stdout is the report "
        "verify-rc-state.sh prints, from ops.rc_state.verify_rc_state."
    ),
)
def get_verify_rc_state(
    request: Request,
    project: str,
    verbose: Optional[bool] = None,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return the RC-state consistency report ``verify-rc-state.sh`` prints.

    The REST twin of the "verify-rc-state" operator verb. The handler calls
    ``pgai_agent_kanban.ops.rc_state.verify_rc_state`` in process -- the
    same function ``verify-rc-state.sh <project> [--verbose]`` reaches
    through ``pp_run_ops`` -- so the envelope carries the exit code, report
    and diagnostics the CLI produces for the same arguments. No subprocess
    is started and no check result is re-derived here.

    Query parameters:

    - ``project`` (required) — project name; the value the CLI takes as its
      positional ``<project>`` argument.
    - ``verbose`` (optional, bool) — when true, reports every check (OK,
      WARN, ERROR), as the CLI's ``--verbose`` does; by default only
      WARN/ERROR and the final summary line are reported, matching the
      CLI's own default.

    Response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list[str]}``

    HTTP status:
      200 on exit_code 0 (no ERROR findings).
      500 on exit_code 1, the script's "1 -- ERROR findings" outcome.
      On exit_code 2, the script's "2 -- pre-flight failure" outcome, the
      status the refusal's key names: 404 for a ``project`` naming no
      registered project (``project_resolve.not_found``) or no project
      directory (``verify_rc_state.project_directory_missing``), 409 for an
      ambiguous prefix (``project_resolve.ambiguous``), 500 for an
      unloadable project config or an unset dev tree path.
      422 when ``project`` is missing, empty, or not one plain name.
    """
    # The docstring above is the route's published contract text (it is the
    # ICD description, frozen with the ICD version); edit it only under an
    # ICD bump.
    try:
        validate_required({"project": project}, required_keys=["project"])
    except ValidationError as exc:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY, detail=exc.detail
        ) from exc

    refused = _component_refusal(request, "verify-rc-state", {"project": project}, warnings)
    if refused is not None:
        return refused

    ctx = OpsContext(kanban_root=request.app.state.api_cfg.kanban_root)
    output = verify_rc_state(ctx, project, verbose=bool(verbose))
    return _make_envelope(_read_output_result(output), warnings)


# ---------------------------------------------------------------------------
# GET /release-locks
# ---------------------------------------------------------------------------


@router.get(
    "/release-locks",
    summary="List every advisory lock under the kanban root (read-only listing mode)",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr; stdout is the listing "
        "release-locks.sh prints with no flags, from ops.locks.list_locks."
    ),
)
def get_release_locks(
    request: Request,
    release: Optional[str] = None,
    force: Optional[bool] = None,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return the lock listing ``release-locks.sh`` prints with no flags (list mode).

    Calls ``pgai_agent_kanban.ops.locks.list_locks`` -- the function
    release-locks.sh reaches through ``pp_run_ops`` -- in process and renders
    it with ``render_lock_listing``, the renderer the CLI prints through; no
    subprocess is started, and nothing is written.

    The REST twin of the "release-locks" operator verb's READ-ONLY LISTING
    MODE only. The verb's mutating ``--release NAME`` mode (optionally
    ``--force``) has its REST twin at ``POST /operations/release-locks``
    (routers/operations.py), never this GET, because a GET route must never
    mutate state (this API's hard read/write boundary; see this file's
    module docstring). ``release`` and ``force`` are declared here only so a
    caller who supplies either gets a loud, explicit refusal naming the POST
    twin instead of a silently ignored parameter that might read as "it
    tried to release something."

    Query parameters:

    - ``release`` (forbidden) — returns 422 naming the mutating twin:
      ``POST /operations/release-locks`` with a JSON body
      ``{"release": NAME}``.
    - ``force`` (forbidden) — same as ``release``; only meaningful alongside
      a release request this endpoint never performs.

    Response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list[str]}``

    HTTP status:
      200 on exit_code 0 (listing printed, including an empty listing).
      422 when ``release`` or ``force`` is supplied.
    """
    if release is not None or force is not None:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY,
            detail=(
                "GET /release-locks is the read-only listing mode; it never "
                "releases a lock. To release one, POST /operations/release-locks "
                "with a JSON body {\"release\": NAME} (add \"force\": true to "
                "override a live holder), or run the CLI verb directly: "
                "team/scripts/release-locks.sh --release NAME [--force]."
            ),
        )

    ctx = OpsContext(kanban_root=request.app.state.api_cfg.kanban_root)
    listing = ops_locks.list_locks(ctx)
    result = ShellResult(exit_code=0, stdout=ops_locks.render_lock_listing(listing), stderr="")
    return _make_envelope(result, warnings)


# ---------------------------------------------------------------------------
# Dual-channel error body helper (shared by /release-state and
# /deep-verify-status -- both raise pgai_agent_kanban.ops.errors.Refused
# with a message that MAY already be the three-line error/message/remedy
# house shape from pgai_agent_kanban.lib.error_contract.format_error_contract
# (release-state's ReleaseStateValidationError always is; deep-verify's
# LedgerError/InvalidDeepVerifyMode are plain sentences, not that shape).
# This helper parses the house shape when present and falls back to a
# generic wrap when it is not, so the JSON body always carries real
# error/message/remedy strings rather than an empty field.
# ---------------------------------------------------------------------------

def _refused_to_dual_channel(exc: Refused, fallback_error_key: str, fallback_remedy: str) -> dict:
    """Convert a ``Refused`` exception into ``{"error", "message", "remedy"}``.

    Delegates to the lib's own ``dual_channel_from_text`` -- the one
    renderer shared with the CLI twins (team/scripts/release-state.sh
    renders the same ``str(exc)`` through the same function), so a
    refusal's three fields are produced by one code path for both
    surfaces: the house shape is parsed out of ``str(exc)`` when the
    raiser produced it, otherwise the plain text becomes the message under
    *fallback_error_key* with *fallback_remedy* as the next step.
    """
    return dual_channel_from_text(str(exc), fallback_error_key, fallback_remedy)


# ---------------------------------------------------------------------------
# GET /release-state
# ---------------------------------------------------------------------------


@router.get(
    "/release-state",
    summary="A project's release-state.md fields, validated (read-only)",
    response_description=(
        "JSON body with project, active_rc, rc_opened_at, rc_opened_by_task, "
        "last_released, and warnings on success; project, error, message, "
        "remedy, and warnings on a validation refusal or a not-found project."
    ),
)
def get_release_state_route(
    request: Request,
    project: str,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return release-state.md's four validated fields for *project*.

    The REST twin of the "release-state" read verb (team/scripts/
    release-state.sh). Both surfaces resolve through the exact same
    function, ``pgai_agent_kanban.ops.read.get_release_state``, which
    itself delegates to the shared, schema-driven validating loader
    (``pgai_agent_kanban.cm.read_state_field.load_release_state``) --
    release-state.md is CM's file; this route only ever reads it, never
    writes it (team/SOP-core.md "Release State Fields").

    A field that is legitimately unset (e.g. a project that has never
    opened an RC) is present in the response with its value normalised
    to JSON ``null`` rather than the ops-layer's own literal string
    ``"none"`` -- a presentational choice matching this API's existing
    ``/projects`` convention for the same two release-state fields
    (active_rc, last_released), extended here to all four.

    Query parameters:

    - ``project`` (required) — project name as registered in projects.cfg.

    Response body (200):
      ``{"project": str, "active_rc": str|null, "rc_opened_at": str|null,
      "rc_opened_by_task": str|null, "last_released": str|null,
      "warnings": list[str]}``

    Response body on refusal (404 project not found, 422 malformed field):
      ``{"project": str, "error": str, "message": str, "remedy": str,
      "warnings": list[str]}`` -- the same machine key / plain-language
      message / runnable remedy the CLI twin prints to stderr, never a
      guessed value in place of a field that failed validation.

    HTTP status:
      200 on success.
      404 when ``project`` does not resolve to a registered project.
      422 when release-state.md fails schema validation.
    """
    refused = _component_refusal(request, "release-state", {"project": project}, warnings)
    if refused is not None:
        return refused

    kanban_root: pathlib.Path = request.app.state.api_cfg.kanban_root
    ctx = OpsContext(kanban_root=kanban_root)

    try:
        fields = get_release_state(ctx, project)
    except NotFound as exc:
        return JSONResponse(
            content={
                "project": project,
                "error": "release_state.project_not_found",
                "message": str(exc),
                "remedy": (
                    "Check the project name against projects.cfg, or create "
                    f"it first with team/scripts/create-project.sh --project {project}."
                ),
                "warnings": warnings,
            },
            status_code=404,
        )
    except Refused as exc:
        body = _refused_to_dual_channel(
            exc,
            RELEASE_STATE_REFUSAL_ERROR_KEY,
            RELEASE_STATE_REFUSAL_FALLBACK_REMEDY,
        )
        body["project"] = project
        body["warnings"] = warnings
        return JSONResponse(content=body, status_code=HTTP_UNPROCESSABLE_ENTITY)

    def _none_to_null(value: str) -> "str | None":
        return None if value.strip().lower() == "none" else value

    return JSONResponse(
        content={
            "project": project,
            "active_rc": _none_to_null(fields["Active RC"]),
            "rc_opened_at": _none_to_null(fields["RC Opened At"]),
            "rc_opened_by_task": _none_to_null(fields["RC Opened By Task"]),
            "last_released": _none_to_null(fields["Last Released"]),
            "warnings": warnings,
        },
        status_code=200,
    )


# ---------------------------------------------------------------------------
# GET /deep-verify-status
# ---------------------------------------------------------------------------


@router.get(
    "/deep-verify-status",
    summary="A project's DEEP-VERIFY ledger entries and current status (read-only)",
    response_description=(
        "JSON body with project, mode, entries, latest, and warnings on "
        "success; project, error, message, remedy, and warnings on a "
        "refusal or a not-found project."
    ),
)
def get_deep_verify_status_route(
    request: Request,
    project: str,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return *project*'s DEEP-VERIFY ledger entries and current status.

    The REST twin of the "deep-verify-status" read verb (team/scripts/
    deep-verify-status.sh). Both surfaces resolve through the exact same
    function, ``pgai_agent_kanban.ops.read.get_deep_verify_status``, which
    delegates to ``pgai_agent_kanban.deep_verify.ledger`` for the ledger
    data and to ``pgai_agent_kanban.deep_verify.schedule.resolve_mode`` for
    the project's configured cadence ([release] deep_verify, resolved from
    project.cfg, then kanban.cfg, then the default ``weekly``). It is never
    a route fronting the "deep-verify" ACTION verb (POST /operations/deep-verify,
    which runs the cold-build lane): this route only ever reads the
    ledger, never runs anything and never writes a record.

    Query parameters:

    - ``project`` (required) — project name as registered in projects.cfg.

    Response body (200):
      ``{"project": str, "mode": "weekly"|"off", "entries": [{"timestamp",
      "project", "version", "commit", "verdict", "detail"}, ...],
      "latest": {same shape}|null, "warnings": list[str]}``. ``entries`` is
      ``[]`` and ``latest`` is ``null`` when the project has never been
      deep-verified.

    Response body on refusal (404 project not found, 422 malformed ledger
    or unrecognized deep_verify config value):
      ``{"project": str, "error": str, "message": str, "remedy": str,
      "warnings": list[str]}``.

    HTTP status:
      200 on success.
      404 when ``project`` does not resolve to a registered project.
      422 when the ledger is malformed or the [release] deep_verify value
      in force (project.cfg, else kanban.cfg) is outside its accepted
      vocabulary.
    """
    refused = _component_refusal(request, "deep-verify-status", {"project": project}, warnings)
    if refused is not None:
        return refused

    kanban_root: pathlib.Path = request.app.state.api_cfg.kanban_root
    ctx = OpsContext(kanban_root=kanban_root)

    try:
        status = get_deep_verify_status(ctx, project)
    except NotFound as exc:
        return JSONResponse(
            content={
                "project": project,
                "error": "deep_verify_status.project_not_found",
                "message": str(exc),
                "remedy": (
                    "Check the project name against projects.cfg, or create "
                    f"it first with team/scripts/create-project.sh --project {project}."
                ),
                "warnings": warnings,
            },
            status_code=404,
        )
    except Refused as exc:
        body = _refused_to_dual_channel(
            exc,
            "deep_verify_status.refused",
            (
                f"Run team/scripts/deep-verify-status.sh --project {project} "
                "to see the full diagnostic, then fix the named field."
            ),
        )
        body["project"] = project
        body["warnings"] = warnings
        return JSONResponse(content=body, status_code=HTTP_UNPROCESSABLE_ENTITY)

    return JSONResponse(
        content={
            "project": project,
            "mode": status["mode"],
            "entries": status["entries"],
            "latest": status["latest"],
            "warnings": warnings,
        },
        status_code=200,
    )
