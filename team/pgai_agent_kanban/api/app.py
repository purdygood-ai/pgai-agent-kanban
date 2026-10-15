"""
app.py — FastAPI application factory for the pgai-agent-kanban operator API.

Call ``create_app()`` to obtain a configured FastAPI instance.  The app is
wired with:

  - GET /health          — service liveness check; returns kanban root, installed
                           version (read from the VERSION file per-request), and
                           running version (baked once at app startup).
  - GET /docs            — auto-generated Swagger UI (FastAPI default, loopback-only).
  - GET /status          — kanban status summary (kanban-status.sh).
  - GET /show            — show a task or intake item (show.sh).
  - GET /test-report     — show a TESTER verification report (show-test-report.sh).
  - GET /metrics         — historical RC metrics (dashboard/show-metrics.sh).
  - GET /costs           — cost report (cost-report.sh).
  - GET /rejected        — quarantined intake inventory (list-rejected.sh).
  - GET /projects        — registered project list (projects.cfg direct read).
  - GET /projects/{name} — project metadata card (config, release state, queue counts).
  - GET /projects/{project}/tasks/{key}/readme — a task's README.md bytes (show-readme.sh).
  - GET /board           — unified kanban board aggregation (all eight columns).
  - GET /logs/{kind}     — tail a named log file (wake, cm, agent, debug, overwatch, api-server).
  - GET /traces          — training-trace index (newest-first, optional project/agent/limit filters).
  - GET /traces/{id}     — fetch one trace by opaque server-minted id.
  - GET /dashboard/{pane} — render a named dashboard pane.
  - POST /operations/halt          — halt.sh (per-project HALT signal).
  - POST /operations/unhalt        — unhalt.sh (remove per-project HALT).
  - POST /operations/halt-after    — halt-after.sh (soft-drain HALT-AFTER signal).
  - POST /operations/halt-global   — halt-global.sh (global HALT signal).
  - POST /operations/unhalt-global — unhalt-global.sh (remove global HALT).
  - POST /operations/reset         — reset.sh (reset task or intake item).
  - POST /operations/close         — close.sh (close/resolve a task or intake item).
  - POST /operations/wontdo        — wontdo.sh (mark task WONT-DO).
  - POST /operations/delete        — delete.sh (delete task or intake item).
  - POST /operations/intake        — intake.sh (deposit intake file into project).
  - POST /operations/unwind-rc     — unwind-rc.sh (unwind an in-flight RC).

Security note: no authentication or TLS in this release.  The service binds
to loopback only; access from elsewhere is via SSH tunnel or SOCKS proxy.
Authentication and TLS are documented as future work.

Trust model -- three layers, each holding the same loopback story:

  1. Bind layer: uvicorn listens on a loopback address only (main.py refuses
     to start on anything else), so no packet from another host reaches the
     socket.

  2. Host layer (``LoopbackHostMiddleware``, outermost): every request --
     read or mutating, any route, including the CORS preflight -- must carry
     a ``Host`` header naming a loopback host: ``127.0.0.1`` (any 127/8
     address), ``localhost``, or ``[::1]``, each with or without a port.
     Anything else is refused HTTP 400 with a dual-channel JSON body.  A
     loopback bind alone does not stop a DNS-rebinding page: the browser
     resolves the attacker's hostname to 127.0.0.1 and sends the request to
     the socket with the attacker's hostname in ``Host``, same-origin from
     the browser's point of view, so CORS never applies.  The Host check is
     what turns that request away.  The allow-list is fixed by design and is
     not a tunable -- widening it is exactly the exposure this layer closes.

  3. Content-type layer (``JsonMutationMiddleware``, innermost): a request
     with a mutating method (anything but GET, HEAD, OPTIONS) must carry
     ``Content-Type: application/json`` (parameters such as ``charset``
     allowed); form-encoded, multipart, text/plain, or absent content types
     are refused HTTP 415 with a dual-channel JSON body before the route --
     and so the script adapter -- is reached.  Read routes are untouched.

Both refusals carry the same top-level ``error`` / ``message`` / ``remedy``
keys the action routes use for their own pre-dispatch refusals, plus an
empty ``warnings`` list, so a client parses one refusal shape whichever
layer refused.

Test clients: the allow-list carries no allowance for a test host.  The
framework-wide pytest fixture in ``team/tests/conftest.py``
(``_loopback_test_client_host``) points every ``TestClient`` at a loopback
``base_url`` instead, so the suite runs against the production allow-list
exactly as an operator's browser or curl does.

CORS: browser pages served from any loopback origin (127.0.0.1 or localhost,
any port) may call the API.  Non-loopback origins receive no CORS headers.
The open-wildcard origin form is intentionally not used; the loopback-regex form
is required because the UI's serving port is operator-chosen.  The loopback
story holds at the CORS layer exactly as it holds at the bind layer.
"""

import importlib.resources
import ipaddress
import os
import pathlib
import shlex

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import ApiConfig, load_api_config
from .dependencies import warn_unknown_query_params
from .reads import router as reads_router
from .routers.board import router as board_router
from .routers.logs import router as logs_router
from .routers.operations import router as operations_router
from .routers.projects import router as projects_router
from .routers.traces import router as traces_router

_VERSION_FALLBACK = "unknown"

# Package-data resource anchor — ICD_VERSION travels with this package on any
# install topology, including live installs where the dev-tree docs/ directory
# is absent.  importlib.resources.files() resolves the file from the installed
# package itself, regardless of where __file__ lives.
_ICD_VERSION_RESOURCE = importlib.resources.files(__package__).joinpath("ICD_VERSION")


def _read_kanban_version(kanban_root: pathlib.Path) -> str:
    """Return the installed VERSION string from the kanban root.

    Reads ``<kanban_root>/VERSION`` (install-generated file).  Returns
    ``"unknown"`` when the file is absent or unreadable.

    This mirrors the Tier-1 resolution in ``team/scripts/dashboard/lib/version.sh``.
    Used by the /health endpoint to report the deployed kanban release version.
    """
    version_file = kanban_root / "VERSION"
    try:
        content = version_file.read_text(encoding="utf-8").strip()
        return content if content else _VERSION_FALLBACK
    except OSError:
        return _VERSION_FALLBACK


def _read_icd_version() -> str:
    """Return the API contract version from the package-data ICD_VERSION resource.

    Reads ``ICD_VERSION`` from the ``pgai_agent_kanban.api`` package data
    directory using ``importlib.resources``.  The file is shipped alongside
    this module so it is available on any install topology, including live
    installs where the dev-tree ``docs/api/ICD_VERSION`` path is absent.

    Returns ``"unknown"`` only when the package-data resource is missing or
    unreadable — a genuinely broken install state that should not occur on
    any correct deployment.

    The ``"unknown"`` fallback is retained as a fault signal; it is not
    reachable on a correctly assembled package.
    """
    try:
        content = _ICD_VERSION_RESOURCE.read_text(encoding="utf-8").strip()
        return content if content else _VERSION_FALLBACK
    except (OSError, FileNotFoundError, TypeError):
        return _VERSION_FALLBACK


def _is_loopback(host: str) -> bool:
    """Return True when *host* is a loopback address.

    Accepts:
    - Any address in 127.0.0.0/8 (IPv4 loopback range).
    - The IPv6 loopback address ``::1``.
    - The hostname ``localhost`` (which conventionally resolves to loopback;
      accepted without DNS lookup to avoid network dependency at startup).

    Rejects anything else, including ``0.0.0.0``, ``::`` (all-interfaces),
    or any routable address.
    """
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # Unrecognised host string — refuse.
        return False


def _host_header_name(host_header: str) -> str:
    """Return the bare host name carried by a ``Host`` header value.

    Strips an optional ``:port`` and the brackets of an IPv6 literal, so
    ``127.0.0.1:8300`` -> ``127.0.0.1``, ``[::1]:8300`` and ``[::1]`` ->
    ``::1``, ``localhost`` -> ``localhost``.  Surrounding whitespace is
    dropped.  A value with more than one colon and no brackets is returned
    as-is (a bare IPv6 literal such as ``::1`` stays intact; anything
    malformed falls through to :func:`_is_loopback`, which refuses it).
    """
    value = host_header.strip()
    if value.startswith("["):
        end = value.find("]")
        return value[1:end] if end != -1 else value
    if value.count(":") == 1:
        return value.rsplit(":", 1)[0]
    return value


def _host_header_port(host_header: str) -> str | None:
    """Return the ``:port`` suffix of a ``Host`` header value, or None.

    The inverse of :func:`_host_header_name` for the one field it discards:
    ``127.0.0.1:8300`` -> ``"8300"``, ``[::1]:8300`` -> ``"8300"``,
    ``localhost`` -> None.  Used only to render a remedy that targets the
    port the caller actually reached.
    """
    value = host_header.strip()
    if value.startswith("["):
        end = value.find("]")
        tail = value[end + 1:] if end != -1 else ""
        return tail[1:] if tail.startswith(":") and tail[1:].isdigit() else None
    if value.count(":") == 1:
        port = value.rsplit(":", 1)[1]
        return port if port.isdigit() else None
    return None


_JSON_MEDIA_TYPE = "application/json"
# Methods that never mutate and so never need a JSON body: the content-type
# gate lets these through untouched (OPTIONS is the CORS preflight).
_NON_MUTATING_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _is_json_content_type(content_type: str) -> bool:
    """Return True when *content_type* names ``application/json``.

    Compares the media type alone, case-insensitively; parameters after the
    first ``;`` (``charset=utf-8``) are ignored.  Nothing else qualifies --
    not ``text/plain``, not a form encoding, not an empty header.
    """
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type == _JSON_MEDIA_TYPE


def _refusal_response(status_code: int, error: str, message: str, remedy: str) -> JSONResponse:
    """Return a middleware-layer refusal as a dual-channel JSON body.

    Same four keys the action routes' own pre-dispatch refusals carry
    (``error``/``message``/``remedy``/``warnings``) so a client parses one
    refusal shape whichever layer refused; ``warnings`` is empty because no
    route ran to collect any.
    """
    return JSONResponse(
        content={"error": error, "message": message, "remedy": remedy, "warnings": []},
        status_code=status_code,
    )


def _request_target(scope: Scope) -> str:
    """Return the request path plus query string from an ASGI *scope*."""
    query = scope.get("query_string", b"").decode("latin-1")
    path = scope.get("path", "/")
    return f"{path}?{query}" if query else path


def _curl_remedy(method: str, url: str) -> str:
    """Return a runnable ``curl`` line for *method* against *url*.

    A mutating method gets the JSON content type and an empty-object body
    as a literal example the caller can paste and then fill in; a read
    method is a bare fetch.  *url* is shell-quoted so a query string with
    ``&`` survives the paste.
    """
    if method in _NON_MUTATING_METHODS:
        return f"curl -sS {shlex.quote(url)}"
    return (
        f"curl -sS -X {method} {shlex.quote(url)} "
        f"-H 'Content-Type: {_JSON_MEDIA_TYPE}' -d '{{}}'"
    )


class LoopbackHostMiddleware:
    """Refuse every request whose ``Host`` header is not a loopback name.

    Pure-ASGI middleware, applied to ``http`` and ``websocket`` scopes and
    transparent to ``lifespan``.  The accepted names are exactly what
    :func:`_is_loopback` accepts -- any 127/8 address, ``::1``, or
    ``localhost`` -- with or without a port (see :func:`_host_header_name`).
    A missing or empty ``Host`` is refused too: HTTP/1.1 requires the
    header, and a request without it has not named the server it means to
    reach.

    Args:
        app:       The next ASGI application in the stack.
        bind_port: The port the server is configured to listen on; used in
                   the remedy when the refused ``Host`` named no port.
    """

    def __init__(self, app: ASGIApp, *, bind_port: int) -> None:
        self.app = app
        self.bind_port = bind_port

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        host_header = Headers(scope=scope).get("host", "")
        host_name = _host_header_name(host_header)
        if host_name and _is_loopback(host_name):
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET").upper()
        port = _host_header_port(host_header) or str(self.bind_port)
        loopback_url = f"http://127.0.0.1:{port}{_request_target(scope)}"
        # BLOCK-WORTHY (docs/blocking-site-census.md): an invariant by design --
        # a non-loopback Host is the DNS-rebinding shape the loopback bind
        # cannot see, so the request is turned away before routing, before
        # CORS, and before any subprocess is spawned.
        # CENSUS-ANCHOR: app-host-not-loopback-01
        response = _refusal_response(
            400,
            "api.host_not_loopback",
            (
                f"the request named Host {host_header!r}, which is not a loopback "
                "host; this API answers only to 127.0.0.1, localhost or [::1] "
                "(with or without a port). Nothing was run."
            ),
            _curl_remedy(method, loopback_url),
        )
        await response(scope, receive, send)


class JsonMutationMiddleware:
    """Refuse a mutating request that does not carry a JSON content type.

    Pure-ASGI middleware over ``http`` scopes only.  Requests whose method
    is in :data:`_NON_MUTATING_METHODS` pass untouched; every other method
    must carry ``Content-Type: application/json`` (parameters allowed) or
    is refused HTTP 415 before routing.  A browser form cannot produce that
    content type without a preflight, so a simple-request forgery never
    reaches a route.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET").upper()
        if method in _NON_MUTATING_METHODS:
            await self.app(scope, receive, send)
            return

        content_type = Headers(scope=scope).get("content-type", "")
        if _is_json_content_type(content_type):
            await self.app(scope, receive, send)
            return

        received = repr(content_type) if content_type else "no Content-Type header"
        url = f"http://{Headers(scope=scope).get('host', '127.0.0.1')}{_request_target(scope)}"
        # BLOCK-WORTHY (docs/blocking-site-census.md): an invariant by design --
        # a mutating route reads a JSON body and nothing else, so a request
        # that cannot carry one is refused here, before the route and before
        # any subprocess is spawned.
        # CENSUS-ANCHOR: app-content-type-not-json-01
        response = _refusal_response(
            415,
            "api.content_type_not_json",
            (
                f"{method} {scope.get('path', '/')} requires Content-Type: "
                f"{_JSON_MEDIA_TYPE}; the request carried {received}. "
                "Nothing was run."
            ),
            _curl_remedy(method, url),
        )
        await response(scope, receive, send)


def create_app(cfg: ApiConfig | None = None) -> FastAPI:
    """Create and return a configured FastAPI application.

    The running version is baked once here at factory time — it is the
    kanban version that was installed when this server process started.
    It never changes for the life of the process, even if the on-disk
    VERSION file is updated by a subsequent install.

    Args:
        cfg: Pre-loaded ApiConfig.  When None, ``load_api_config()`` is called
             to read from kanban.cfg.  Passing an explicit config is useful in
             tests.

    Returns:
        A FastAPI instance with the /health endpoint registered.
    """
    if cfg is None:
        cfg = load_api_config()

    # Bake the running version at startup.  This is the version of the code
    # this server process is actually executing.  An orphaned server that
    # survived a deploy will report its true startup-time version here, not
    # the current on-disk VERSION, so callers can detect the mismatch.
    _running_version: str = _read_kanban_version(cfg.kanban_root)

    app = FastAPI(
        title="pgai-agent-kanban operator API",
        description=(
            "Localhost-only REST API exposing the pgai-agent-kanban operator "
            "command surface.  A route calls the shared pgai_agent_kanban.ops "
            "function its operator script runs, in process, and returns the "
            "result in a JSON envelope.  Only four routes start a process: "
            "GET /dashboard/{pane} runs the pane's dashboard render script, "
            "POST /operations/wake-now runs wake-now.sh, "
            "POST /operations/deep-verify launches deep-verify.sh detached, and "
            "POST /operations/cold-build-now launches cold-build-now.sh detached."
        ),
        version=_read_icd_version(),
    )

    # Middleware stack, innermost first (add_middleware wraps what is already
    # there, so the LAST one added is the OUTERMOST):
    #
    #   LoopbackHostMiddleware   -- outermost: a foreign Host is refused before
    #                               CORS can answer its preflight.
    #   CORSMiddleware           -- answers the loopback preflight itself.
    #   JsonMutationMiddleware   -- innermost: a mutating request without a
    #                               JSON content type is refused before routing.
    #
    # The three layers are described under "Trust model" in the module
    # docstring.  There is no test-host allowance in any of them: the test
    # suite points its TestClient at a loopback base_url instead, so the
    # production allow-list is the one every test runs under.
    app.add_middleware(JsonMutationMiddleware)

    # CORS: allow browser pages served from any loopback origin to call the
    # API.  The regex matches http(s)://127.0.0.1[:<port>] and
    # http(s)://localhost[:<port>].  Non-loopback origins receive no
    # Access-Control-Allow-Origin header.  Credentials are disabled;
    # the loopback-only bind and Host guards are the primary access control.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$",
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    app.add_middleware(LoopbackHostMiddleware, bind_port=cfg.port)

    # Store config on app.state so endpoints can access it without a module-
    # level singleton (friendlier to test isolation).
    app.state.api_cfg = cfg

    @app.get(
        "/health",
        summary="Service liveness check",
        response_description=(
            "JSON object with service name, kanban root path, installed version, "
            "running version, and warnings list."
        ),
    )
    def health(
        request: Request,
        warnings: list[str] = Depends(warn_unknown_query_params),
    ) -> dict:
        """Return service liveness information.

        Response body fields:

        - ``service``      — fixed string identifying this API service.
        - ``kanban_root``  — resolved kanban root path (from PGAI_AGENT_KANBAN_ROOT_PATH).
        - ``version``      — installed kanban version read from the VERSION file
                             on each request (same value as ``installed``; kept for
                             backward compatibility with existing consumers).
        - ``installed``    — kanban version currently on disk (read from VERSION on each
                             request); reflects the most recent install.
        - ``running``      — kanban version that was on disk when this server process
                             started; baked once at startup and never re-read.  An
                             orphaned server that survived a deploy will report its
                             true startup-time version here, allowing callers to detect
                             version skew between running code and installed code.
        - ``warnings``     — always-present list; empty on a clean call; populated with
                             one entry per unknown query parameter supplied by the caller.
        """
        installed = _read_kanban_version(app.state.api_cfg.kanban_root)
        return {
            "service": "pgai-agent-kanban-api",
            "kanban_root": str(app.state.api_cfg.kanban_root),
            "version": installed,
            "installed": installed,
            "running": _running_version,
            "warnings": warnings,
        }

    # Register read endpoints (status, show, test-report, metrics, costs,
    # rejected, projects, dashboard panes).
    app.include_router(reads_router)

    # Register the board aggregation endpoint (GET /board).
    app.include_router(board_router)

    # Register the project metadata endpoint (GET /projects/{name}).
    app.include_router(projects_router)

    # Register the log tail endpoint (GET /logs/{kind}).
    app.include_router(logs_router)

    # Register the training-trace endpoints (GET /traces, GET /traces/{id}).
    app.include_router(traces_router)

    # Register mutation endpoints (halt family, reset, close, wontdo, delete,
    # intake, unwind-rc) under /operations/*.
    app.include_router(operations_router)

    return app
