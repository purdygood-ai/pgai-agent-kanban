#!/usr/bin/env bash
# team/scripts/unhalt-global.sh
# CLI wrapper: remove the global HALT signal at ${KANBAN_ROOT}/HALT.
#
# Usage: unhalt-global.sh [-h|--help]
#
# Takes no project or key arguments.  Removes the global HALT file so the
# wake loop resumes processing all projects at the next iteration.
#
# Thin adapter: delegates the on-disk change to the Python unhalt_global
# function in pgai_agent_kanban.ops.write.  Idempotent.

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# --- Locate lib directory relative to this script ---
_UNHALT_GLOBAL_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_UNHALT_GLOBAL_LIB_DIR="${_UNHALT_GLOBAL_SCRIPT_DIR}/lib"

# --- Source the standard argument layer ---
# shellcheck source=lib/operator_args.sh
source "${_UNHALT_GLOBAL_LIB_DIR}/operator_args.sh"

# --- Source the shared Python invocation helper ---
# shellcheck source=lib/pp_run_ops.sh
source "${_UNHALT_GLOBAL_LIB_DIR}/pp_run_ops.sh"

# --- Declare accepted flags for this script ---
# unhalt-global takes no project/key arguments; only --help is accepted.
OPERATOR_VALID_FLAGS=(help)

# --- Resolve KANBAN_ROOT via standard env-var / default fallback ---
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
export KANBAN_ROOT

# --- Parse arguments ---
operator_args_parse "$@"

# --- Handle --help ---
if argparse_has help; then
    operator_args_render_help_for_flags "unhalt-global.sh" \
        "Remove the global HALT — resumes all projects at next wake." \
        OPERATOR_VALID_FLAGS \
        "" \
        "  Global HALT is removed from \${KANBAN_ROOT}/HALT." \
        "  Idempotent.  Set global HALT with halt-global.sh."
    exit 0
fi

# --- Reject any flag not in the declared vocabulary ---
operator_args_validate_known "unhalt-global.sh" OPERATOR_VALID_FLAGS || exit 1

# --- Validate KANBAN_ROOT exists ---
if [[ ! -d "${KANBAN_ROOT}" ]]; then
    printf 'unhalt-global.sh: KANBAN_ROOT does not exist or is not a directory: %s\n' \
        "${KANBAN_ROOT}" >&2
    exit 1
fi

_halt_path="${KANBAN_ROOT}/HALT"

# --- Idempotent: no HALT present (check before calling Python for fast path) ---
if [[ ! -f "${_halt_path}" ]]; then
    printf 'unhalt-global.sh: no global HALT was set (%s not found)\n' "${_halt_path}"
    exit 0
fi

# --- Remove the HALT sentinel via Python library ---
if pp_run_ops pgai_agent_kanban.ops unhalt_global "${KANBAN_ROOT}"; then
    printf 'global HALT cleared: %s removed — all projects will resume at next wake\n' "${_halt_path}"
    exit 0
else
    printf 'unhalt-global.sh: failed to remove global HALT at %s\n' "${_halt_path}" >&2
    exit 1
fi
