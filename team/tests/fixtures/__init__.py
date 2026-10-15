"""
team/tests/fixtures
===================
Reusable test-fidelity helpers for pgai-agent-kanban tests.

Modules
-------
installed_root  — Build a temp directory that mirrors the installed (not dev)
                  kanban tree layout, with no shim package present. Closes
                  an earlier defect (tests run in dev tree where shim was present).

two_project     — Build a temp kanban root containing two registered projects
                  (project_a, project_b). Closes an earlier defect (single-project
                  fixtures only; cross-project isolation untested).

log_stub        — Faithful stub of the production log() bash function that
                  tees output to both stdout and a log file. Closes an earlier defect
                  (stderr-only stub masked command-substitution contamination
                  bug).

Each module's own entry above states the failure mode it closes and the
rationale for the fixture that closes it.
"""
