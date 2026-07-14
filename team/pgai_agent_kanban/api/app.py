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

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

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
            "command surface.  Each endpoint shells out to the canonical "
            "operator script and returns the result in a JSON envelope."
        ),
        version=_read_icd_version(),
    )

    # CORS: allow browser pages served from any loopback origin to call the
    # API.  The regex matches http(s)://127.0.0.1[:<port>] and
    # http(s)://localhost[:<port>].  Non-loopback origins receive no
    # Access-Control-Allow-Origin header.  Credentials are disabled;
    # the loopback-only bind guard is the primary access control.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$",
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        allow_credentials=False,
    )

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
