"""
pgai_agent_kanban.discovery — the Python side of the discovery pipeline's
bash stages under team/scripts/lib/discovery_*.sh.

The bash stages keep the orchestration (logging, quarantine moves, settle
guards, bundle writes); every scan or parse they need is a function here,
reached through ``pp_run_ops``:

- :mod:`.items` — the intake-item scan (which BUG-/PRIORITY- files are
  candidates for the next bundle, including items a shipped bundle carried),
  the per-file rejection counter, the BUG-ID collision report, the bundle
  item ordering, a bug's severity field, and a task's ``## State``.
- :mod:`.eligibility` — which requirements files are eligible for PM pickup.
"""
