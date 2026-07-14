"""
traces.py — GET /traces and GET /traces/{id} endpoints for the pgai-agent-kanban operator API.

Training traces are per-task markdown documents written by agents under:

  <kanban_root>/projects/<project_name>/logs/training/<agent>/<basename>.md

where <basename> follows the convention: ``<YYYYMMDDTHHmmss>-<TASK-KEY>``
(e.g. ``20260703T133901-CODER-20260703-002-entry-point-and-config``).

Endpoints:

  GET /traces
    Returns the training-trace INDEX sorted newest-first.

    Query parameters (all optional):
      project  — filter by project name; absent = all registered projects.
      agent    — filter by agent role; must be one of the fixed agent-role set.
      limit    — maximum entries to return; default 50, hard cap 2000.

    Response: JSON array of trace objects::

      [
        {
          "id":            "<opaque server-minted token (file stem)>",
          "project":       "<project name>",
          "agent":         "<agent role>",
          "task_key":      "<TASK-KEY extracted from filename>",
          "timestamp":     "<ISO-8601 string derived from filename prefix>",
          "path_basename": "<filename with extension>"
        },
        ...
      ]

  GET /traces/{id}
    Fetch one trace by its opaque id.

    The id is resolved ONLY by walking the server's own enumeration.  No
    client-supplied path component ever reaches the filesystem.  An id that
    does not appear in the server enumeration — including traversal-shaped
    inputs such as ``../../etc/passwd`` — returns HTTP 404.

    Response: standard envelope::

      {"exit_code": 0, "stdout": "<markdown body>", "stderr": ""}

HTTP status:

  GET /traces: 200 always (empty array when no matches).
  GET /traces/{id}: 200 when found; 404 when id is not in the enumeration.

Security:

  Opaque-id discipline is the primary security surface.  The id is the file
  stem and is looked up by matching against the server's own enumeration of
  known trace files — never by constructing a filesystem path from the id.

  For the optional ``project`` and ``agent`` query parameters:
    - Traversal sequences (``..``, ``/``, URL-encoded variants) → 422 before
      any filesystem access.
    - Agent values are validated against the fixed agent-role whitelist.
    - Unknown project or agent names return HTTP 200 with an empty array
      (valid filter; zero matches).

Agent whitelist (fixed role set):

  pm, po, coder, writer, tester, cm, overwatch
"""

from __future__ import annotations

import pathlib
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ..adapter import HTTP_UNPROCESSABLE_ENTITY
from ..dependencies import warn_unknown_query_params

__all__ = ["router"]

router = APIRouter(tags=["traces"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_LIMIT_DEFAULT = 50
_LIMIT_MAX = 2000

# Fixed agent-role set — same whitelist used by the logs router.
_VALID_AGENTS: frozenset[str] = frozenset(
    {"pm", "po", "coder", "writer", "tester", "cm", "overwatch"}
)

# Filename convention: <YYYYMMDDTHHmmss>-<TASK-KEY>.md
# The timestamp prefix is exactly 15 characters: 8 digits + "T" + 6 digits.
_TIMESTAMP_RE = re.compile(r"^(\d{8}T\d{6})-(.+)$")


# ---------------------------------------------------------------------------
# Traversal guard (same logic as logs router)
# ---------------------------------------------------------------------------


def _has_traversal(value: str) -> bool:
    """Return True when the value contains a path traversal sequence.

    Checks both raw and URL-encoded forms before any filesystem access.
    A traversal is detected when the value contains:
      - ".." (dot-dot)
      - "/" or "\\" (path separator)
      - URL-encoded variants: "%2F" (slash), "%5C" (backslash), "%2E" (dot)
      - Null bytes (path confusion)

    This check fires BEFORE any filesystem access for query parameters.
    It does NOT apply to the {id} path parameter — traversal-shaped ids are
    simply not in the server's enumeration and resolve to 404.
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


# ---------------------------------------------------------------------------
# Limit parameter parser
# ---------------------------------------------------------------------------


def _parse_limit(raw: Optional[str]) -> int:
    """Parse and validate the ``limit`` query parameter.

    Returns an integer in [1, 2000].  Values above 2000 are clamped.
    Returns the default (50) when absent.  Raises HTTPException (422) when
    the value is present but not a positive integer.
    """
    if raw is None:
        return _LIMIT_DEFAULT

    try:
        n = int(raw)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY,
            detail=(
                f"Invalid 'limit' value: {raw!r}. "
                "limit must be a positive integer (1–2000)."
            ),
        )

    if n <= 0:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY,
            detail=(
                f"Invalid 'limit' value: {n!r}. "
                "limit must be a positive integer (greater than 0)."
            ),
        )

    return min(n, _LIMIT_MAX)


# ---------------------------------------------------------------------------
# Trace enumeration
# ---------------------------------------------------------------------------


def _parse_trace_filename(stem: str) -> tuple[str, str] | None:
    """Parse a trace file stem into (timestamp_iso, task_key).

    The stem follows the convention: ``<YYYYMMDDTHHmmss>-<TASK-KEY>``

    Returns (timestamp_iso, task_key) when the stem matches the convention,
    or None when it does not.  The timestamp_iso string is in ISO-8601 format
    (``YYYY-MM-DDTHH:MM:SS+00:00``).
    """
    m = _TIMESTAMP_RE.match(stem)
    if not m:
        return None

    ts_raw, task_key = m.group(1), m.group(2)
    # ts_raw is "YYYYMMDDTHHmmss" — parse to a datetime and format as ISO.
    try:
        dt = datetime.strptime(ts_raw, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None

    return dt.isoformat(), task_key


def _enumerate_traces(
    kanban_root: pathlib.Path,
    project_filter: Optional[str],
    agent_filter: Optional[str],
) -> list[dict]:
    """Walk the training-trace tree and return all matching trace records.

    Scans: <kanban_root>/projects/<project>/logs/training/<agent>/<stem>.md

    When project_filter is set, only that project's directory is scanned.
    When agent_filter is set, only that agent's subdirectory is scanned.
    Files whose names do not match the expected convention are skipped silently.

    Returns a list of trace dicts with keys: id, project, agent, task_key,
    timestamp, path_basename.  NOT yet sorted or limited.
    """
    traces: list[dict] = []

    projects_root = kanban_root / "projects"
    if not projects_root.is_dir():
        return traces

    # Determine which projects to scan.
    if project_filter is not None:
        project_dirs = [projects_root / project_filter]
    else:
        try:
            project_dirs = [p for p in sorted(projects_root.iterdir()) if p.is_dir()]
        except OSError:
            return traces

    for project_dir in project_dirs:
        if not project_dir.is_dir():
            continue

        project_name = project_dir.name
        training_root = project_dir / "logs" / "training"
        if not training_root.is_dir():
            continue

        # Determine which agents to scan.
        if agent_filter is not None:
            agent_dirs = [training_root / agent_filter]
        else:
            try:
                agent_dirs = [
                    a for a in sorted(training_root.iterdir()) if a.is_dir()
                ]
            except OSError:
                continue

        for agent_dir in agent_dirs:
            if not agent_dir.is_dir():
                continue

            agent_name = agent_dir.name
            try:
                files = list(agent_dir.iterdir())
            except OSError:
                continue

            for f in files:
                if f.suffix != ".md":
                    continue

                parsed = _parse_trace_filename(f.stem)
                if parsed is None:
                    continue

                timestamp_iso, task_key = parsed
                traces.append(
                    {
                        "id": f.stem,
                        "project": project_name,
                        "agent": agent_name,
                        "task_key": task_key,
                        "timestamp": timestamp_iso,
                        "path_basename": f.name,
                    }
                )

    return traces


def _sort_newest_first(traces: list[dict]) -> list[dict]:
    """Sort trace records newest-first by timestamp, then by id for determinism.

    The timestamp field is ISO-8601 and lexicographically sortable, so string
    comparison is sufficient.  The id (file stem) is a stable tiebreaker.
    """
    return sorted(traces, key=lambda t: (t["timestamp"], t["id"]), reverse=True)


# ---------------------------------------------------------------------------
# GET /traces — index
# ---------------------------------------------------------------------------


@router.get(
    "/traces",
    summary="List training-trace index",
    response_description=(
        "JSON array of trace objects sorted newest-first."
    ),
)
def get_traces_index(
    request: Request,
    project: Optional[str] = None,
    agent: Optional[str] = None,
    limit: Optional[str] = None,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return the training-trace index sorted newest-first.

    Query parameters (all optional):

    - ``project`` — filter by project name; absent = all projects.
                    Traversal sequences → 422 before any filesystem access.
    - ``agent``   — filter by agent role.  Must be one of the fixed agent-role
                    names when provided: pm, po, coder, writer, tester, cm,
                    overwatch.  Traversal sequences → 422.
    - ``limit``   — maximum number of entries to return.  Default: 50.
                    Hard cap: 2000 (values above are clamped).
                    ``limit == 0`` or negative → 422.

    Response: JSON array of trace objects, newest-first::

      [
        {
          "id":            "<opaque server-minted id>",
          "project":       "<project name>",
          "agent":         "<agent role>",
          "task_key":      "<TASK-KEY from filename>",
          "timestamp":     "<ISO-8601 timestamp from filename>",
          "path_basename": "<filename.md>"
        },
        ...
      ]

    HTTP status:

    - 200 — always (empty array when no traces match the filters).
    - 422 — traversal sequence in project or agent param; invalid limit;
            agent value not in the fixed agent-role set.

    Security:

    The project and agent query parameters are validated for traversal sequences
    before any filesystem access.  The agent parameter is validated against the
    fixed agent-role whitelist.  Unknown project or agent names result in an
    empty array (not an error).
    """
    # -----------------------------------------------------------------------
    # Validate and parse limit.
    # -----------------------------------------------------------------------
    limit_n = _parse_limit(limit)

    # -----------------------------------------------------------------------
    # Validate optional project parameter.
    # Traversal check fires before any filesystem access.
    # -----------------------------------------------------------------------
    project_filter: Optional[str] = None
    if project is not None and project != "":
        if _has_traversal(project):
            raise HTTPException(
                status_code=HTTP_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Invalid 'project' value: traversal sequence detected in {project!r}. "
                    "The project parameter must be a plain project name."
                ),
            )
        project_filter = project

    # -----------------------------------------------------------------------
    # Validate optional agent parameter.
    # Traversal check fires first; then whitelist check.
    # -----------------------------------------------------------------------
    agent_filter: Optional[str] = None
    if agent is not None and agent != "":
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
        agent_filter = agent_lower

    # -----------------------------------------------------------------------
    # Enumerate and filter traces.
    # -----------------------------------------------------------------------
    kanban_root: pathlib.Path = request.app.state.api_cfg.kanban_root
    all_traces = _enumerate_traces(kanban_root, project_filter, agent_filter)
    sorted_traces = _sort_newest_first(all_traces)
    result = sorted_traces[:limit_n]

    # Note: this array response does not carry a top-level warnings key;
    # adding one requires a non-additive shape change tracked separately.
    # The warnings dependency is wired so unknown query params are captured.
    return JSONResponse(content=result, status_code=200)


# ---------------------------------------------------------------------------
# GET /traces/{id} — fetch one trace by opaque id
# ---------------------------------------------------------------------------


@router.get(
    "/traces/{trace_id}",
    summary="Fetch one training trace by opaque id",
    response_description=(
        "Standard envelope with the trace markdown body in stdout."
    ),
)
def get_trace_by_id(
    trace_id: str,
    request: Request,
    warnings: list[str] = Depends(warn_unknown_query_params),
) -> JSONResponse:
    """Return the markdown body of a training trace by its opaque id.

    Path parameter:

    - ``trace_id`` — opaque server-minted id returned by GET /traces.
                     Resolved ONLY against the server's own enumeration of
                     known trace files.  A fabricated or traversal-shaped id
                     that is not in the enumeration returns HTTP 404 — path
                     components inside the id are NEVER interpreted as
                     filesystem navigation.

    Response envelope (HTTP 200 when found)::

      {"exit_code": 0, "stdout": "<markdown body>", "stderr": ""}

    HTTP status:

    - 200 — trace found; markdown body in stdout.
    - 404 — id not in the server's enumeration (includes traversal-shaped ids
            and fabricated ids).

    Security:

    The id is resolved ONLY by enumerating the server's known trace files and
    matching against their stems.  No path component from the id reaches the
    filesystem — the only filesystem read is ``path.read_text()`` on the
    canonical path the server already knows about from the enumeration.
    """
    kanban_root: pathlib.Path = request.app.state.api_cfg.kanban_root

    # Enumerate ALL traces (no filter) and find the one whose id matches.
    # This is the opaque-id discipline: we never construct a path from the id.
    all_traces = _enumerate_traces(kanban_root, None, None)

    matched_path: Optional[pathlib.Path] = None
    for entry in all_traces:
        if entry["id"] == trace_id:
            # Reconstruct the canonical path from trusted components.
            matched_path = (
                kanban_root
                / "projects"
                / entry["project"]
                / "logs"
                / "training"
                / entry["agent"]
                / entry["path_basename"]
            )
            break

    if matched_path is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "trace not found", "id": trace_id},
        )

    # Read the file — the ONLY filesystem read in this handler, using a path
    # constructed entirely from the server's own enumeration.
    try:
        content = matched_path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        # Should not happen (file was in the enumeration), but handle gracefully.
        raise HTTPException(
            status_code=404,
            detail={"error": "trace not found", "id": trace_id},
        )
    except OSError as exc:
        return JSONResponse(
            content={
                "exit_code": 1,
                "stdout": "",
                "stderr": f"Could not read trace file: {exc}",
                "warnings": warnings,
            },
            status_code=200,
        )

    return JSONResponse(
        content={
            "exit_code": 0,
            "stdout": content,
            "stderr": "",
            "warnings": warnings,
        },
        status_code=200,
    )
