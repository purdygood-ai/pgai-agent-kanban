#!/usr/bin/env bash
# team/scripts/lib/halt_after_invoke.sh
#
# Shared scope resolution and CLI invocation for the halt_after Python
# module, so the wake loop (team/scripts/lib/wake_project.sh,
# _check_halt_after_for_project) and CM's synchronous ship-time spend
# (team/scripts/lib/cm/release_finalize.sh, Step 12) share exactly one
# implementation of "where does the sentinel live" and "how do we call the
# evaluator" instead of two that could drift.
#
# Public entry points
# --------------------
#   pgai_halt_after_resolve_scope_root <project_root> <kanban_root>
#       Prints the scope root to stdout and returns 0 when a HALT-AFTER
#       sentinel exists there -- project scope
#       (<project_root>/HALT-AFTER) takes precedence over root scope
#       (<kanban_root>/HALT-AFTER). Prints nothing and returns 3 when
#       neither scope has a sentinel.
#
#   pgai_halt_after_run <scope_root> <release_state_root>
#       Invokes `python3 -m halt_after <scope_root> <release_state_root>`
#       with PYTHONPATH set so cron's cwd never controls the import. Sets
#       the globals PGAI_HALT_AFTER_EXIT (the module's exit code -- see
#       team/pgai_agent_kanban/halt_after/__main__.py for the full table)
#       and PGAI_HALT_AFTER_OUTPUT (combined stdout+stderr), and returns
#       the same exit code so `if pgai_halt_after_run ...; then` reads as
#       "promoted".
#
#       Errexit contract: the function leaves the caller's errexit (set -e)
#       state unchanged. It never runs `set -e` or `set +e`; it captures the
#       evaluator's exit code with `|| PGAI_HALT_AFTER_EXIT=$?`, which
#       errexit never trips on, so on return errexit is on or off exactly as
#       the caller had it and the evaluator's code is the return status. It
#       may therefore be called from any errexit context, but the return
#       status is still subject to the caller's own set -e like any other
#       command: an uncaptured bare call that returns non-zero (any answer
#       other than "promoted") ends a set -e script (for example a
#       set -euo pipefail script such as cm/release.sh) at that line. A
#       set -e caller MUST capture a possibly non-zero return in one of
#       these forms:
#           local rc=0
#           pgai_halt_after_run "$scope_root" "$project_root" || rc=$?
#       or make the call from an `if` condition, or bracket it with its
#       own `set +e ... rc=$? ... set -e`.
#
# Dependencies: KANBAN_ROOT must be set by the caller before sourcing.
# python3 must be on PATH.

pgai_halt_after_resolve_scope_root() {
  local project_root="$1"
  local kanban_root="$2"
  if [[ -f "${project_root}/HALT-AFTER" ]]; then
    printf '%s' "$project_root"
    return 0
  fi
  if [[ -f "${kanban_root}/HALT-AFTER" ]]; then
    printf '%s' "$kanban_root"
    return 0
  fi
  return 3
}

pgai_halt_after_run() {
  local scope_root="$1"
  local release_state_root="$2"
  local pythonpath="${KANBAN_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
  # Capture the exit code in an `||` list rather than toggling errexit, so
  # the caller's set -e state is never changed (see the errexit contract in
  # the header).
  PGAI_HALT_AFTER_EXIT=0
  PGAI_HALT_AFTER_OUTPUT="$(PYTHONPATH="$pythonpath" python3 -m halt_after "$scope_root" "$release_state_root" 2>&1)" \
    || PGAI_HALT_AFTER_EXIT="$?"
  return "$PGAI_HALT_AFTER_EXIT"
}
