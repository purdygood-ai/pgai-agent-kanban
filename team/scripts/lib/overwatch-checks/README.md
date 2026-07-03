# overwatch-checks/

## Purpose

This directory holds detection modules for OVERWATCH — the autonomous watchdog
that observes the kanban system for structural anomalies and stale state.

Each module is a sourced bash script that implements a single named check.
Checks emit findings to the action log via `overwatch_log_action` (defined in
`../overwatch_lib.sh`) and return 0 on success, non-zero when a halt-worthy
condition is detected.

## Module Naming Convention

```
check-<slug>.sh
```

Examples:
- `check-stale-working-tasks.sh`
- `check-rc-drift.sh`
- `check-orphaned-feature-branches.sh`

## Module Contract

Every module in this directory must:

1. Be sourceable without side effects (no top-level commands that alter state).
2. Export a single function named `overwatch_check_<slug>` where `<slug>`
   matches the filename's slug.
3. Accept zero arguments. All context is read from `OVERWATCH_STATE_DIR` and
   `KANBAN_ROOT` environment variables (set by the OVERWATCH driver).
4. Return 0 when no issue is detected; return non-zero when an anomaly is
   detected and logged.
5. Use `overwatch_log_action` to record findings — never write directly to
   actions.log.
6. Never mutate kanban task state (status.md files, queue files). Observation
   only — mutations belong to the CODER and CM roles.

## When Modules Are Invoked

OVERWATCH sources each `check-*.sh` file in this directory in lexical order
and invokes its exported function. The OVERWATCH driver manages the
per-firing flock, halt-flag checks, and state-dir bootstrapping before
calling any module.

## Adding a New Check

1. Create `check-<slug>.sh` in this directory following the contract above.
2. Test that `bash -n check-<slug>.sh` passes.
3. Test that sourcing the file produces no output.
4. Verify the check function returns 0 on a clean system.

This directory is intentionally empty on first install. Detection logic
ships in subsequent releases layered on top of the helpers in
`../overwatch_lib.sh`.
