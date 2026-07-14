# team/pgai_agent_kanban/api — FastAPI service sub-package.
#
# This sub-package provides the localhost-only REST API for the pgai-agent-kanban
# operator interface.  It is a thin adapter: every endpoint shells out to the
# canonical operator script and returns the result in a JSON envelope.
#
# Entry point: main.py (uvicorn entrypoint)
# App factory: app.py (create_app())
# Configuration: config.py (reads [api] section from kanban.cfg)
#
# No auth/TLS in this release — the service binds to loopback only.
# Authentication and TLS are documented as future work.
