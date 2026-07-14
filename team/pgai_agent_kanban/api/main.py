"""
main.py — uvicorn entrypoint for the pgai-agent-kanban operator API.

Applies the loopback-only startup guard before starting uvicorn.  If the
configured host is not a loopback address, the process exits non-zero with a
clear message naming the offending host.

Intended to be invoked by ``scripts/api-server.sh start``, which manages the
process lifecycle (pidfile, log redirection).  Can also be run directly:

    python3 -m pgai_agent_kanban.api.main
    # or (dev tree, with team/ on PYTHONPATH):
    python3 -m team.pgai_agent_kanban.api.main

Security note: no authentication or TLS in this release.  Loopback binding is
the only access-control mechanism.  Authentication and TLS are future work.
"""

import sys

import uvicorn

from .app import _is_loopback, create_app
from .config import load_api_config


def main() -> None:
    """Load config, enforce loopback guard, and start the uvicorn server.

    Exits non-zero when the configured host is not a loopback address.
    """
    cfg = load_api_config()

    # Loopback-only guard.
    # The service must never bind to a routable address.  If the operator
    # configures a non-loopback host, refuse to start with a clear message.
    if not _is_loopback(cfg.host):
        print(
            f"ERROR: Refusing to start — configured host '{cfg.host}' is not a "
            "loopback address.  The pgai-agent-kanban API must bind to "
            "127.0.0.1 (or another loopback address).  "
            "Access from remote hosts is by SSH tunnel or SOCKS proxy.",
            file=sys.stderr,
        )
        sys.exit(1)

    app = create_app(cfg=cfg)

    uvicorn.run(
        app,
        host=cfg.host,
        port=cfg.port,
        # Single-worker: the API is a thin shell-out adapter on a loopback socket;
        # concurrency beyond one process is not needed and would complicate
        # pidfile management.
        workers=1,
        # Access log to stdout so api-server.sh can redirect to a log file.
        access_log=True,
        # Disable the uvicorn reload feature; the operator restarts via
        # api-server.sh stop && api-server.sh start.
        reload=False,
    )


if __name__ == "__main__":
    main()
