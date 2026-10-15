"""team/pgai_agent_kanban/api — FastAPI service sub-package.

This sub-package provides the localhost-only REST API for the pgai-agent-kanban
operator interface.  It is a thin adapter over the same code the operator
scripts run: a route calls the shared ``pgai_agent_kanban.ops`` function (the
one its CLI script reaches through ``pp_run_ops``) in process and returns the
result in a JSON envelope.  Only four routes start a process:

  GET  /dashboard/{pane}           runs the pane's dashboard render script
  POST /operations/wake-now        runs wake-now.sh
  POST /operations/deep-verify     launches deep-verify.sh detached
  POST /operations/cold-build-now  launches cold-build-now.sh detached

Every other route calls the shared ops function in process.

Entry point: main.py (uvicorn entrypoint)
App factory: app.py (create_app())
Configuration: config.py (reads [api] section from kanban.cfg)

No auth/TLS in this release — the service binds to loopback only.
Authentication and TLS are documented as future work.
"""
