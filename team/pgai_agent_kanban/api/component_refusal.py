"""
component_refusal.py — the HTTP refusal every route returns for a value that
is not one plain name.

``validation.require_component`` decides what is refused; this module turns
that ``ComponentRefused`` into the route's HTTP 422 response. The read
routes (``api/reads.py``) and the action routes (``api/routers/operations.py``)
both import ``_component_refusal`` from here, so the refusal key, message,
remedy and body shape are defined once.

The remedy is a runnable ``curl`` line built from the request's own URL:

- an action route passes ``example_body`` -- a literal JSON body -- and the
  remedy re-POSTs it (``example_body_curl``);
- a read route passes ``None`` and the remedy re-sends the same GET with an
  example value in place of the refused parameter, in the path or the
  query string, wherever it arrived.

Public API:
    _component_refusal(request, verb, fields, example_body, warnings)
    example_body_curl(request, example_body) -> str
    COMPONENT_EXAMPLES  (per-field example values a read remedy re-sends)
"""

from __future__ import annotations

import shlex

from fastapi import Request
from fastapi.responses import JSONResponse

from .adapter import HTTP_UNPROCESSABLE_ENTITY
from .validation import ComponentRefused, require_component

__all__ = ["COMPONENT_EXAMPLES", "_component_refusal", "example_body_curl"]

#: Example values a read route's remedy re-sends in place of a refused
#: parameter; any other field gets ``my-app``.
COMPONENT_EXAMPLES: dict[str, str] = {
    "project": "my-app",
    "key": "v1.2.3",
    "rc": "v1.2.3",
}


def example_body_curl(request: Request, example_body: str) -> str:
    """Return a runnable curl line re-sending *example_body* to this route.

    Built from the request's own URL, so the printed command targets the
    server the caller actually reached (never a placeholder host).
    *example_body* is a literal JSON string the caller can paste as-is.
    """
    return f"curl -sS -X POST {request.url} -H 'Content-Type: application/json' " f"-d '{example_body}'"


def _resend_with_example(request: Request, field: str) -> str:
    """Return a runnable curl line re-sending this GET with an example value
    in place of *field*, in the path segment or query parameter it arrived in."""
    example = COMPONENT_EXAMPLES.get(field, "my-app")
    if field in request.path_params:
        path = request.url.path.replace(f"/{request.path_params[field]}/", f"/{example}/", 1)
        corrected = request.url.replace(path=path)
    else:
        corrected = request.url.include_query_params(**{field: example})
    return f"curl -sS {shlex.quote(str(corrected))}"


def _component_refusal(
    request: Request,
    verb: str,
    fields: dict[str, object],
    example_body: str | None,
    warnings: list[str],
) -> JSONResponse | None:
    """Refuse a request whose project, key or selector value is not one plain name.

    The one place every route's confinement check lands (see
    ``validation.require_component`` for what is refused and why). Each
    entry of *fields* maps a body field, path or query parameter name to the
    value the request carried; an absent value (``None`` or ``""``) is
    skipped, since whether it is required is the route's own check. The
    first refused value returns HTTP 422 with the dual-channel triple
    ``<verb_snake>.<field>_invalid`` -- ``remove_project.project_invalid``,
    ``status.project_invalid`` -- a message naming the field and what is
    wrong with it, a runnable curl remedy, and the ``warnings`` list. Runs
    before any path is built, any file is read, any dry-run is described or
    any script is run.

    Args:
        request:      The incoming request; its URL builds the remedy.
        verb:         The route's verb name (``halt``, ``status``).
        fields:       Field name -> value the request carried.
        example_body: An action route's literal JSON example body, which the
                      remedy re-POSTs; ``None`` for a read route, whose
                      remedy re-sends the GET with an example value.
        warnings:     Unknown-field warnings already accumulated.

    Returns:
        None when every supplied value is a plain name; otherwise the 422
        refusal response.
    """
    for field, value in fields.items():
        if value is None or value == "":
            continue
        try:
            require_component(value, field)
        except ComponentRefused as exc:
            if example_body is None:
                remedy = _resend_with_example(request, field)
            else:
                remedy = example_body_curl(request, example_body)
            triple = exc.contract(verb, remedy)
            # BLOCK-WORTHY (docs/blocking-site-census.md): an invariant by
            # design -- the value becomes a path component, a glob prefix, a
            # sentinel line or an argv value, so a value that is not one
            # plain name could reach outside the kanban root or change what
            # runs. Refused here, dual-channel, before anything acts on it.
            # CENSUS-ANCHOR: component-refusal-field-not-component-01
            return JSONResponse(
                content={**triple, "warnings": warnings},
                status_code=HTTP_UNPROCESSABLE_ENTITY,
            )
    return None
