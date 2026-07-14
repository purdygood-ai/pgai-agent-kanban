"""
logs.py — GET /logs/{kind} tailable stream endpoint for the pgai-agent-kanban operator API.

Returns the tail of a named log file.  The security surface is path confinement:
each (kind, params) tuple maps to a KNOWN filesystem path via a server-side table.
No client-supplied path fragment ever reaches the filesystem.

Kinds and their required params:

  wake       (param: agent)           → <kanban_root>/logs/cron-<agent>.log
  cm         (no params)              → <kanban_root>/logs/cron-cm.log
  agent      (params: project, agent) → <kanban_root>/projects/<project>/logs/agents/<agent>.log
  debug      (params: project, agent) → <kanban_root>/projects/<project>/logs/debug/<agent>.log
  overwatch  (param: project)         → <kanban_root>/projects/<project>/logs/overwatch/sweep.log
                                        when project == "global":
                                        <kanban_root>/logs/overwatch.log
  api-server (no params)              → <temp_root>/api/api-server.log

Query parameters:

  tail      (int, default 200, hard cap 2000) — number of lines to return from
             the end of the log file.  Values above 2000 are clamped to 2000.
             tail == 0 or tail < 0 → 422 Unprocessable Entity.

Response shape:

  The standard envelope: {"exit_code": int, "stdout": str, "stderr": str}

  exit_code == 0  : the tail text is in stdout; stderr is empty.
  exit_code == 1  : the log file was not found; stdout is empty, stderr describes it.

HTTP status:

  200  : always returned on successful validation (regardless of whether the file
         exists on disk — file-not-found is not a 404, it is a 200 with exit_code 1).
  422  : validation failure — missing required param, unknown kind, traversal detected,
         invalid agent value, or invalid tail value.
  404  : unknown kind (returned as a JSON object, not a detail string).

Path confinement invariant:

  Every path reaching the filesystem is FULLY CONSTRUCTED from trusted components
  (kanban_root from app config, a literal kind-specific sub-path, and validated
  whitelist-checked param values).  No user-supplied string is concatenated into
  a path without first being checked against a whitelist.  Traversal attempts in
  agent or project params (e.g. ``../../etc/passwd``, ``..%2F..``) return 422
  BEFORE any filesystem open() is attempted.

Agent whitelist (the fixed agent-role set):

  pm, po, coder, writer, tester, cm, overwatch

Project validation:

  The project param is validated against the kanban's projects.cfg registry
  (same source as GET /projects/{name}).  Unknown project name → 422.
  The literal value "global" is accepted for the overwatch kind only.
"""

from __future__ import annotations

import configparser
import os
import pathlib
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ..adapter import HTTP_UNPROCESSABLE_ENTITY
from ..dependencies import warn_unknown_query_params

__all__ = ["router"]

router = APIRouter(tags=["logs"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default and maximum tail lines.
_TAIL_DEFAULT = 200
_TAIL_MAX = 2000

# The fixed agent-role set.  Validated server-side — client-supplied agent
# values outside this set are rejected with 422.
_VALID_AGENTS: frozenset[str] = frozenset(
    {"pm", "po", "coder", "writer", "tester", "cm", "overwatch"}
)

# Known kinds.  Any other kind value → 404.
_KNOWN_KINDS: frozenset[str] = frozenset(
    {"wake", "cm", "agent", "debug", "overwatch", "api-server"}
)

# The literal project sentinel for the overwatch global log.
_GLOBAL_SENTINEL = "global"


# ---------------------------------------------------------------------------
# Path confinement helpers
# ---------------------------------------------------------------------------


def _kanban_temp_root() -> pathlib.Path:
    """Return the framework temp root directory.

    Resolution matches the bash pgai_mktemp convention in team/scripts/lib/temp.sh:
      1. PGAI_AGENT_KANBAN_TEMP_DIR env var if set.
      2. /tmp/pgai_kanban_tmp as the hard fallback.
    """
    env_val = os.environ.get("PGAI_AGENT_KANBAN_TEMP_DIR", "").strip()
    return pathlib.Path(env_val) if env_val else pathlib.Path("/tmp/pgai_kanban_tmp")


def _read_registered_projects(kanban_root: pathlib.Path) -> frozenset[str]:
    """Return the set of project names registered in projects.cfg.

    Returns an empty frozenset when projects.cfg is absent or unreadable.
    """
    cfg_path = kanban_root / "projects.cfg"
    if not cfg_path.is_file():
        return frozenset()

    parser = configparser.RawConfigParser()
    try:
        parser.read(str(cfg_path), encoding="utf-8")
    except configparser.Error:
        return frozenset()

    names: set[str] = set()
    for section in parser.sections():
        if section.startswith("project:"):
            names.add(section[len("project:"):])

    return frozenset(names)


def _has_traversal(value: str) -> bool:
    """Return True when the value contains a path traversal sequence.

    Checks both raw and common URL-encoded forms before any filesystem access.
    A traversal is detected when the value contains:
      - ".." (dot-dot)
      - "/" or "\\" (path separator)
      - URL-encoded variants: "%2F" (slash), "%5C" (backslash), "%2E" (dot),
        or the two-dot sequence "%2E%2E".
    Case-insensitive for URL-encoded forms.

    This check is the confinement gate: it fires BEFORE any open() call.
    """
    upper = value.upper()
    # Raw sequences.
    if ".." in value:
        return True
    if "/" in value:
        return True
    if "\\" in value:
        return True
    # URL-encoded slash / backslash / dot.
    if "%2F" in upper:
        return True
    if "%5C" in upper:
        return True
    if "%2E" in upper:
        return True
    # Null bytes (path confusion).
    if "\x00" in value or "%00" in upper:
        return True
    return False


# ---------------------------------------------------------------------------
# Tail helper
# ---------------------------------------------------------------------------


def _tail_file(path: pathlib.Path, n: int) -> tuple[int, str, str]:
    """Return (exit_code, stdout, stderr) from tailing the last ``n`` lines of *path*.

    exit_code == 0: file found; stdout contains the tail text.
    exit_code == 1: file not found or unreadable; stdout empty; stderr describes the issue.

    This is the ONLY function in this module that opens a file.  It is called
    only after all param validation and the confinement table lookup have
    completed; user-supplied values never reach here as path components.
    """
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return (
            1,
            "",
            f"Log file not found: {path.name}",
        )
    except OSError as exc:
        return (
            1,
            "",
            f"Could not read log file: {exc}",
        )

    lines = content.splitlines()
    tail_lines = lines[-n:] if n < len(lines) else lines
    return 0, "\n".join(tail_lines) + ("\n" if tail_lines else ""), ""


# ---------------------------------------------------------------------------
# Query-parameter validation
# ---------------------------------------------------------------------------


def _parse_tail(raw: Optional[str]) -> int:
    """Parse and validate the ``tail`` query parameter.

    Returns the tail line count (integer in [1, 2000]).

    Raises HTTPException (422) when:
      - The value is present but not a valid integer.
      - The value is 0 or negative.

    Values above 2000 are clamped to 2000.
    When absent (None), returns the default of 200.
    """
    if raw is None:
        return _TAIL_DEFAULT

    try:
        n = int(raw)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY,
            detail=(
                f"Invalid 'tail' value: {raw!r}. "
                "tail must be a positive integer (1–2000)."
            ),
        )

    if n <= 0:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY,
            detail=(
                f"Invalid 'tail' value: {n!r}. "
                "tail must be a positive integer (greater than 0)."
            ),
        )

    return min(n, _TAIL_MAX)


def _validate_agent_param(agent: Optional[str]) -> str:
    """Validate the ``agent`` query parameter against the fixed agent-role set.

    Returns the agent string (lowercased) when valid.
    Raises HTTPException (422) when the param is missing, empty, or not in
    the valid set — or when it contains a traversal sequence.
    """
    if not agent:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY,
            detail="Required parameter 'agent' is missing or empty.",
        )

    # Confinement gate: traversal check fires before the whitelist check.
    if _has_traversal(agent):
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY,
            detail=(
                f"Invalid 'agent' value: traversal sequence detected in {agent!r}. "
                "The agent parameter must be a plain agent-role name."
            ),
        )

    agent_lower = agent.lower()
    if agent_lower not in _VALID_AGENTS:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY,
            detail=(
                f"Unknown agent: {agent!r}. "
                f"Valid agents: {sorted(_VALID_AGENTS)!r}."
            ),
        )

    return agent_lower


def _validate_project_param(
    project: Optional[str],
    registered: frozenset[str],
    allow_global: bool = False,
) -> str:
    """Validate the ``project`` query parameter against the project registry.

    Returns the project name when valid.
    Raises HTTPException (422) when:
      - The param is missing or empty.
      - The param contains a traversal sequence.
      - The param is not in the registered project set
        (and is not the "global" sentinel when allow_global is True).
    """
    if not project:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY,
            detail="Required parameter 'project' is missing or empty.",
        )

    # Confinement gate: traversal check fires before registry lookup.
    if _has_traversal(project):
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY,
            detail=(
                f"Invalid 'project' value: traversal sequence detected in {project!r}. "
                "The project parameter must be a registered project name."
            ),
        )

    if allow_global and project == _GLOBAL_SENTINEL:
        return project

    if project not in registered:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY,
            detail=(
                f"Unknown project: {project!r}. "
                "The project parameter must match a registered project name."
            ),
        )

    return project


# ---------------------------------------------------------------------------
# Confinement table: (kind, validated_params) → canonical path
# ---------------------------------------------------------------------------


def _resolve_wake_path(kanban_root: pathlib.Path, agent: str) -> pathlib.Path:
    """Resolve the wake cron log path for the given agent.

    Path: <kanban_root>/logs/cron-<agent>.log
    """
    return kanban_root / "logs" / f"cron-{agent}.log"


def _resolve_cm_path(kanban_root: pathlib.Path) -> pathlib.Path:
    """Resolve the CM cron log path.

    Path: <kanban_root>/logs/cron-cm.log
    """
    return kanban_root / "logs" / "cron-cm.log"


def _resolve_agent_path(
    kanban_root: pathlib.Path, project: str, agent: str
) -> pathlib.Path:
    """Resolve the per-project agent log path.

    Path: <kanban_root>/projects/<project>/logs/agents/<agent>.log
    """
    return kanban_root / "projects" / project / "logs" / "agents" / f"{agent}.log"


def _resolve_debug_path(
    kanban_root: pathlib.Path, project: str, agent: str
) -> pathlib.Path:
    """Resolve the per-project agent debug log path.

    Path: <kanban_root>/projects/<project>/logs/debug/<agent>.log
    """
    return kanban_root / "projects" / project / "logs" / "debug" / f"{agent}.log"


def _resolve_overwatch_path(
    kanban_root: pathlib.Path, project: str
) -> pathlib.Path:
    """Resolve the overwatch sweep log path.

    When project == "global": <kanban_root>/logs/overwatch.log
    Otherwise: <kanban_root>/projects/<project>/logs/overwatch/sweep.log
    """
    if project == _GLOBAL_SENTINEL:
        return kanban_root / "logs" / "overwatch.log"
    return kanban_root / "projects" / project / "logs" / "overwatch" / "sweep.log"


def _resolve_api_server_path() -> pathlib.Path:
    """Resolve the API server log path.

    Path: <temp_root>/api/api-server.log
    """
    return _kanban_temp_root() / "api" / "api-server.log"


# ---------------------------------------------------------------------------
# GET /logs/{kind}
# ---------------------------------------------------------------------------


@router.get(
    "/logs/{kind}",
    summary="Tail a named log file",
    response_description=(
        "JSON envelope with exit_code, stdout (tail text), and stderr."
    ),
)
def get_log_tail(
    kind: str,
    request: Request,
    agent: Optional[str] = None,
    project: Optional[str] = None,
    tail: Optional[str] = None,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return the tail of a named log file.

    Path parameter:

    - ``kind`` — log kind selector.  One of: ``wake``, ``cm``, ``agent``,
                 ``debug``, ``overwatch``, ``api-server``.
                 Unknown kind → 404.

    Query parameters (all optional unless noted):

    - ``agent``   — required for ``wake``, ``agent``, ``debug`` kinds.
                    Must be one of the fixed agent-role names:
                    pm, po, coder, writer, tester, cm, overwatch.
                    Traversal sequences → 422 before any file access.
    - ``project`` — required for ``agent``, ``debug`` kinds.
                    Must match a name in the project registry.
                    For ``overwatch``, accepts a registered project name or
                    the literal ``global`` for the global overwatch log.
                    Traversal sequences → 422 before any file access.
    - ``tail``    — number of lines to return from the end of the file.
                    Default: 200.  Hard cap: 2000 (values above are clamped).
                    ``tail == 0`` or negative → 422.

    Response envelope (HTTP 200 on success):

    - ``exit_code`` — 0 when the file was found and read; 1 when not found.
    - ``stdout``    — the tail text (empty when exit_code is 1).
    - ``stderr``    — error description when exit_code is 1; empty otherwise.
    - ``warnings``  — always-present list; one entry per unknown query parameter.

    HTTP status:

    - 200  — successful response (file found or not; see exit_code).
    - 404  — unknown kind value.
    - 422  — validation failure (missing param, traversal, invalid agent/project,
              invalid tail value).

    Path confinement:

    The (kind, params) tuple is mapped to a KNOWN path via a server-side table.
    No client-supplied string is concatenated into a filesystem path without first
    being validated against a whitelist.  Traversal attempts return 422 before
    any filesystem access.
    """
    # -----------------------------------------------------------------------
    # Step 1: Validate kind.
    # Unknown kind → 404 before any param validation.
    # -----------------------------------------------------------------------
    if kind not in _KNOWN_KINDS:
        return JSONResponse(
            content={"error": "unknown log kind", "kind": kind},
            status_code=404,
        )

    # -----------------------------------------------------------------------
    # Step 2: Parse tail param (validation; raises 422 on bad input).
    # -----------------------------------------------------------------------
    tail_n = _parse_tail(tail)

    # -----------------------------------------------------------------------
    # Step 3: Dispatch by kind — validate required params and resolve path.
    # ALL traversal and whitelist checks fire here, BEFORE any filesystem call.
    # -----------------------------------------------------------------------
    kanban_root: pathlib.Path = request.app.state.api_cfg.kanban_root

    log_path: pathlib.Path

    if kind == "wake":
        # Required: agent
        agent_val = _validate_agent_param(agent)
        log_path = _resolve_wake_path(kanban_root, agent_val)

    elif kind == "cm":
        # No params required
        log_path = _resolve_cm_path(kanban_root)

    elif kind == "agent":
        # Required: project, agent
        # Traversal and agent whitelist checks fire before any filesystem access.
        # The registry read (projects.cfg) comes only after traversal is ruled out.
        if not project:
            raise HTTPException(
                status_code=HTTP_UNPROCESSABLE_ENTITY,
                detail="Required parameter 'project' is missing or empty.",
            )
        if _has_traversal(project):
            raise HTTPException(
                status_code=HTTP_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Invalid 'project' value: traversal sequence detected in {project!r}. "
                    "The project parameter must be a registered project name."
                ),
            )
        agent_val = _validate_agent_param(agent)
        registered = _read_registered_projects(kanban_root)
        project_val = _validate_project_param(project, registered, allow_global=False)
        log_path = _resolve_agent_path(kanban_root, project_val, agent_val)

    elif kind == "debug":
        # Required: project, agent
        # Traversal and agent whitelist checks fire before any filesystem access.
        # The registry read (projects.cfg) comes only after traversal is ruled out.
        if not project:
            raise HTTPException(
                status_code=HTTP_UNPROCESSABLE_ENTITY,
                detail="Required parameter 'project' is missing or empty.",
            )
        if _has_traversal(project):
            raise HTTPException(
                status_code=HTTP_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Invalid 'project' value: traversal sequence detected in {project!r}. "
                    "The project parameter must be a registered project name."
                ),
            )
        agent_val = _validate_agent_param(agent)
        registered = _read_registered_projects(kanban_root)
        project_val = _validate_project_param(project, registered, allow_global=False)
        log_path = _resolve_debug_path(kanban_root, project_val, agent_val)

    elif kind == "overwatch":
        # Required: project (or "global")
        # Traversal check fires before the registry read.
        if not project:
            raise HTTPException(
                status_code=HTTP_UNPROCESSABLE_ENTITY,
                detail="Required parameter 'project' is missing or empty.",
            )
        if _has_traversal(project):
            raise HTTPException(
                status_code=HTTP_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Invalid 'project' value: traversal sequence detected in {project!r}. "
                    "The project parameter must be a registered project name or 'global'."
                ),
            )
        if project == _GLOBAL_SENTINEL:
            project_val = project
        else:
            registered = _read_registered_projects(kanban_root)
            project_val = _validate_project_param(project, registered, allow_global=False)
        log_path = _resolve_overwatch_path(kanban_root, project_val)

    else:
        # kind == "api-server"
        # No params required
        log_path = _resolve_api_server_path()

    # -----------------------------------------------------------------------
    # Step 4: Read the file — the ONLY filesystem access in this handler.
    # The path was fully constructed from trusted components above.
    # -----------------------------------------------------------------------
    exit_code, stdout, stderr = _tail_file(log_path, tail_n)

    return JSONResponse(
        content={
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "warnings": warnings,
        },
        status_code=200,
    )
