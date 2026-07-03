"""
team/tests/fixtures
===================
Reusable test-fidelity helpers for pgai-agent-kanban tests.

Modules
-------
installed_root  — Build a temp directory that mirrors the installed (not dev)
                  kanban tree layout, with no shim package present. Closes
                  BUG-0158 (tests run in dev tree where shim was present).

two_project     — Build a temp kanban root containing two registered projects
                  (project_a, project_b). Closes BUG-0160 (single-project
                  fixtures only; cross-project isolation untested).

log_stub        — Faithful stub of the production log() bash function that
                  tees output to both stdout and a log file. Closes BUG-0161
                  (stderr-only stub masked command-substitution contamination
                  bug).

See PRIORITY-0074 for the full rationale and the failure modes these helpers
close.
"""
