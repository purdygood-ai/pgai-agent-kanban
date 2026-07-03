#!/usr/bin/env bash
# team/scripts/halt-global.sh
# CLI wrapper: create the global HALT signal at ${KANBAN_ROOT}/HALT.
#
# Usage: halt-global.sh [-h|--help]
#
# Takes no project or key arguments.  The global HALT blocks ALL projects at
# the next wake-loop iteration (discovery.sh checks ${TEAM_ROOT}/HALT before
# running any pipeline step).
#
# Thin adapter: delegates the on-disk change to the Python halt_global function
# in pgai_agent_kanban.ops.write.  Idempotent and fully reversible via
# unhalt-global.sh.

set -euo pipefail

# --- Locate lib directory relative to this script ---
_HALT_GLOBAL_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_HALT_GLOBAL_LIB_DIR="${_HALT_GLOBAL_SCRIPT_DIR}/lib"

# --- Source the standard argument layer ---
# shellcheck source=lib/operator_args.sh
source "${_HALT_GLOBAL_LIB_DIR}/operator_args.sh"

# --- Declare accepted flags for this script ---
# halt-global takes no project/key arguments; only --help is accepted.
OPERATOR_VALID_FLAGS=(help)

# --- Resolve KANBAN_ROOT via standard env-var / default fallback ---
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"
export KANBAN_ROOT

# --- Parse arguments ---
operator_args_parse "$@"

# --- Handle --help ---
if argparse_has help; then
    operator_args_render_help_for_flags "halt-global.sh" \
        "Create the global HALT — stops all projects at next wake." \
        OPERATOR_VALID_FLAGS \
        "" \
        "  Global HALT is created at \${KANBAN_ROOT}/HALT." \
        "  Idempotent.  Reverse with unhalt-global.sh."
    exit 0
fi

# --- Reject any flag not in the declared vocabulary ---
operator_args_validate_known "halt-global.sh" OPERATOR_VALID_FLAGS || exit 1

# --- Validate KANBAN_ROOT exists ---
if [[ ! -d "${KANBAN_ROOT}" ]]; then
    printf 'halt-global.sh: KANBAN_ROOT does not exist or is not a directory: %s\n' \
        "${KANBAN_ROOT}" >&2
    exit 1
fi

_halt_path="${KANBAN_ROOT}/HALT"

# --- Idempotent: already halted (check before calling Python for fast path) ---
if [[ -f "${_halt_path}" ]]; then
    printf 'halt-global.sh: global HALT already set: %s\n' "${_halt_path}"
    exit 0
fi

# --- Create the HALT sentinel via Python library ---
if python3 -m pgai_agent_kanban.ops halt_global "${KANBAN_ROOT}"; then
    printf 'global HALT set: %s — all projects will stop at next wake\n' "${_halt_path}"
    exit 0
else
    printf 'halt-global.sh: failed to create global HALT at %s\n' "${_halt_path}" >&2
    exit 1
fi
