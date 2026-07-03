#!/usr/bin/env bash
# team/scripts/lib/operator_ops.sh
# Stateful operations module for operator CLI tools.
#
# Source this file to get the op_* functions in your shell:
#   source "$(dirname "${BASH_SOURCE[0]}")/operator_ops.sh"
#
# DESIGN OVERVIEW
# ---------------
# This library is the canonical home for all state-mutation operations
# performed by operator CLI tools (and future REST adapters).
#
# ARCHITECTURAL RULE (load-bearing):
#   All state mutation lives here.  The op_* functions are side-effect-pure
#   adapters: no argument parsing, no UI echo, no exit baked into them.
#   They return status codes.
#   CLI wrappers call these functions; they do not contain mutation logic.
#
# Bash baseline: bash 5.1+ (RHEL 9 / Rocky 9).
#
# HALT operations
# ---------------
# halt, unhalt, halt_after, halt_global, unhalt_global are implemented in
# Python: pgai_agent_kanban.ops.write.  The halt*.sh CLI wrappers delegate
# directly to that module.  No bash op_halt* functions exist in this file.
#
# Functions
# ---------
#   (No op_* functions remain in this file — all have been ported to Python.)
#
#   op_wontdo — REMOVED.  Logic now lives in wontdo_item() in
#       pgai_agent_kanban.ops.write (Python).  wontdo.sh delegates to that
#       module directly.  See team/pgai_agent_kanban/ops/write.py.
#
#   op_delete — REMOVED.  Logic now lives in delete_item() in
#       pgai_agent_kanban.ops.write (Python).  delete.sh delegates to that
#       module directly.  See team/pgai_agent_kanban/ops/write.py.
#
#   op_close — REMOVED.  Logic now lives in close_item() in
#       pgai_agent_kanban.ops.write (Python).  close.sh delegates to that
#       module directly.  See team/pgai_agent_kanban/ops/write.py.
#
#   op_intake — REMOVED.  Intake logic now lives in deposit_intake() in
#       pgai_agent_kanban.ops.write (Python).  intake.sh delegates to that
#       module directly.  See team/pgai_agent_kanban/ops/write.py.
#
# Include guard
# -------------
# Double-sourcing is safe; the second source is a no-op.
#
# API stability
# -------------
# This module is a load-bearing foundation for all operator wrappers and the
# future REST adapter.  Add functions additively; do not rename or remove
# existing ones.

# ---------------------------------------------------------------------------
# Include guard: prevent double-loading in the same shell process.
# ---------------------------------------------------------------------------
if [[ -n "${_OPERATOR_OPS_SH_LOADED:-}" ]]; then
    return 0
fi
_OPERATOR_OPS_SH_LOADED=1

# ---------------------------------------------------------------------------
# Python resolve_item shim path.
# All callers that previously called the bash resolve_item() now delegate
# to the Python implementation via this shim script.
# ---------------------------------------------------------------------------
_OPERATOR_OPS_SHIM="$(cd "${BASH_SOURCE[0]%/*}" && pwd)/resolve_item_shim.py"

# ---------------------------------------------------------------------------
# Shared marker-sync helpers
#
# These are the single source of truth for queue/backlog marker mutations.
# reset.sh sources this file and calls these helpers so all four terminal-
# state tools (reset / close / wontdo / delete) use the same matching logic.
#
# Marker forms handled: [ ] [W] [A] [x] [B] [R] and combinations.
# All helpers are operate-if-present: a missing file or unmatched item is
# a no-op (return 0).
# ---------------------------------------------------------------------------


