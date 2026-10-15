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
#
# Provenance: a write that changes the sentinel also appends one record --
# time, op, path, entry, pid, parent (this script's command line), user --
# to ${KANBAN_ROOT}/logs/sentinel-writes.log, so the gated runners' sentinel
# check can tell this arm from a test leak. The sentinel's bytes, this
# script's output and its exit codes are unchanged; a record that cannot
# be written is one stderr warning, never a failure.

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# --- Locate lib directory relative to this script ---
_HALT_GLOBAL_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_HALT_GLOBAL_LIB_DIR="${_HALT_GLOBAL_SCRIPT_DIR}/lib"

# --- Source the standard argument layer ---
# shellcheck source=lib/operator_args.sh
source "${_HALT_GLOBAL_LIB_DIR}/operator_args.sh"

# --- Source the shared Python invocation helper ---
# shellcheck source=lib/pp_run_ops.sh
source "${_HALT_GLOBAL_LIB_DIR}/pp_run_ops.sh"

# --- Source the dual-channel error-contract emitter ---
# shellcheck source=lib/error_contract.sh
source "${_HALT_GLOBAL_LIB_DIR}/error_contract.sh"

# --- Declare accepted flags for this script ---
# halt-global takes no project/key arguments; only --help is accepted.
OPERATOR_VALID_FLAGS=(help)

# --- Resolve KANBAN_ROOT via standard env-var / default fallback ---
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
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
    # BLOCK-WORTHY (docs/blocking-site-census.md): the global HALT sentinel
    # lives at KANBAN_ROOT/HALT -- with no such directory there is nowhere
    # to write it, so this refuses rather than letting the write attempt
    # below fail one line later with a less specific message.
    pgai_emit_error_contract \
        "halt_global.kanban_root_missing" \
        "halt-global.sh: KANBAN_ROOT does not exist or is not a directory: ${KANBAN_ROOT}." \
        "Set PGAI_AGENT_KANBAN_ROOT_PATH to an existing kanban root, or create ${KANBAN_ROOT} first."
    # CENSUS-ANCHOR: halt-global-kanban-root-missing-01
    exit 1
fi

_halt_path="${KANBAN_ROOT}/HALT"

# --- Idempotent: already halted (check before calling Python for fast path) ---
if [[ -f "${_halt_path}" ]]; then
    printf 'halt-global.sh: global HALT already set: %s\n' "${_halt_path}"
    exit 0
fi

# --- Create the HALT sentinel via Python library ---
if pp_run_ops pgai_agent_kanban.ops halt_global "${KANBAN_ROOT}"; then
    printf 'global HALT set: %s — all projects will stop at next wake\n' "${_halt_path}"
    exit 0
else
    # BLOCK-WORTHY (docs/blocking-site-census.md): the sentinel write itself
    # failed (permissions, disk, or another OS-level cause) -- the operator
    # cannot rely on the HALT being in effect, so this refuses loud rather
    # than reporting success on an unverified write.
    pgai_emit_error_contract \
        "halt_global.sentinel_create_failed" \
        "halt-global.sh: failed to create global HALT sentinel at ${_halt_path}." \
        "Check that ${KANBAN_ROOT} is writable by the current user, then re-run team/scripts/halt-global.sh."
    # CENSUS-ANCHOR: halt-global-sentinel-create-failed-01
    exit 1
fi
