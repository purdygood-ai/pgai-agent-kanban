"""
dependencies.py — Shared FastAPI dependencies for the pgai-agent-kanban operator API.

Provides reusable Depends() callables that are wired into read (GET) route handlers.
Currently exports one dependency:

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

from typing import Any

from fastapi import Request


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
