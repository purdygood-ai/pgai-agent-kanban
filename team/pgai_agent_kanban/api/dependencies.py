"""
dependencies.py — Shared FastAPI dependencies for the pgai-agent-kanban operator API.

Provides reusable Depends() callables that are wired into read (GET) route
handlers, and the per-request path resolvers every route shares:

  route_kanban_root(request)
    The kanban root the server resolved at startup (``app.state.api_cfg``):
    the one root every route reads and writes under -- metrics, costs,
    approvals and verdicts included -- rather than each route re-reading
    the environment.

  route_temp_root(request)
    The framework temp root for that kanban root
    (:func:`pgai_agent_kanban.api.config.kanban_temp_root`).

  route_scripts_dir(request) / route_script_path(request, relative)
    The ``<kanban_root>/scripts/`` directory, and one script under it as an
    absolute path string, for a route that shells out to an operator script.

Dependency:

  warn_unknown_query_params(request)
    Inspects the incoming request's query parameters against the set of parameter
    names that FastAPI has declared for the route, and returns a list of warning
    strings for every parameter name the client supplied that is not among the
    declared ones.

    Warning string format: 'unknown parameter: <name>'

    The dependency is designed to be injected via:

        warnings: list[str] = Depends(warn_unknown_query_params)

    at the route-function signature.  The returned list is empty when all supplied
    query parameters match declared route parameters.

    FastAPI records every declared query-parameter name on the route's dependant
    graph.  This dependency compares request.query_params (the live key set from
    the client) against that declared set, producing one warning string per
    undeclared name.  Path parameters are excluded automatically because they do
    not appear in request.query_params.

Design constraints:
  - ONE shared implementation for all read routes; no per-route duplication.
  - Does NOT raise an exception; unknown params warn and execute (per the recorded defect Design).
  - The warning list is appended to the response by the route handler; this
    dependency only computes and returns it.
  - Only used on read (GET) routes; operation (POST) routes handle unknown body
    fields separately.
"""

from __future__ import annotations

import pathlib
from typing import Any

from fastapi import Request

from .config import kanban_temp_root


def route_kanban_root(request: Request) -> pathlib.Path:
    """Return the kanban root this server answers for (its ``ApiConfig.kanban_root``)."""
    return pathlib.Path(request.app.state.api_cfg.kanban_root)


def route_temp_root(request: Request) -> pathlib.Path:
    """Return the framework temp root for this server's kanban root."""
    return kanban_temp_root(route_kanban_root(request))


def route_scripts_dir(request: Request) -> pathlib.Path:
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


def route_script_path(request: Request, relative: str) -> str:
    """Resolve a script path relative to scripts/ and return it as a string.

    Args:
        request:  FastAPI request (for kanban_root).
        relative: Relative path within scripts/; e.g. ``"verify-rc-state.sh"``
                  or ``"dashboard/show-header.sh"``.

    Returns:
        Absolute script path string suitable for subprocess invocation.
    """
    return str(route_scripts_dir(request) / relative)


def warn_unknown_query_params(request: Request) -> list[str]:
    """Return warning strings for query parameters not declared by this route.

    Compares the request's actual query-parameter names against the set of
    parameter names declared in FastAPI's route dependant graph for the matched
    route.  Returns one warning string per undeclared name.

    Format: ``'unknown parameter: <name>'``

    Returns an empty list when all supplied query parameters are declared, or
    when no query parameters were supplied.

    Args:
        request: The FastAPI request object for the current call.

    Returns:
        A list of warning strings, one per unknown query parameter name.
        Empty list when no unknown parameters were found.
    """
    # Collect the set of parameter names declared for this route.
    # FastAPI stores the matched route on request.scope["route"].
    route: Any = request.scope.get("route")
    declared: set[str] = set()
    if route is not None and hasattr(route, "dependant"):
        for field in route.dependant.query_params:
            declared.add(field.name)

    # Compare against the actual query parameters supplied by the client.
    warnings: list[str] = []
    for name in request.query_params:
        if name not in declared:
            warnings.append(f"unknown parameter: {name}")

    return warnings
