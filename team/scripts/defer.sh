#!/usr/bin/env bash
# team/scripts/defer.sh
# CLI wrapper: arm the DEFER-LOW-BUGS deferral campaign for a project.
#
# Usage: defer.sh --project NAME --until vX.Y.Z [OPTIONS]
#   --project NAME    Project to arm the campaign for (required)
#   --until vX.Y.Z    Release the campaign runs until (required). A leading
#                      'v' is optional and normalised, matching
#                      halt-after.sh's arm-time handling.
#   --help, -h        Show help and exit
#
# Thin adapter: parses the flags via operator_args.sh, refuses a call with
# no project named, and hands the rest to the Python defer function in
# pgai_agent_kanban.ops.write (the same function POST /operations/defer
# calls). That function validates --until, resolves the project by exact
# match then unambiguous prefix, and writes PROJECT_ROOT/DEFER-LOW-BUGS
# atomically with an 'until=vX.Y.Z' line -- the body shape
# team/scripts/lib/defer_posture.sh and pgai_agent_kanban.lib.defer_posture
# read. It prints the one-line narration below, or the refusal on stderr.
#
# Validation runs before the first byte touches disk, so a refusal leaves the
# state on disk byte-identical to the state before it. Re-arming with the
# SAME until= target is a no-op that keeps the existing file (mtime and any
# operator comment lines) and says so; a DIFFERENT target replaces it, so
# exactly one campaign file exists afterwards.
#
# Provenance: a write that changes the sentinel also appends one record --
# time, op, path, entry, pid, parent (this script's command line), user --
# to ${KANBAN_ROOT}/logs/sentinel-writes.log, so the gated runners' sentinel
# check can tell this arm from a test leak. The sentinel's bytes, this
# script's output and its exit codes are unchanged; a record that cannot
# be written is one stderr warning, never a failure.
#
# Exit codes: 0 armed or already armed; 1 any refusal (no project, missing or
# malformed --until, unknown or ambiguous project, no project directory, a
# write failure).

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# --- Locate the lib directory relative to this script ---
_DEFER_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_DEFER_LIB_DIR="${_DEFER_SCRIPT_DIR}/lib"

# --- Source the standard argument layer ---
# shellcheck source=lib/operator_args.sh
source "${_DEFER_LIB_DIR}/operator_args.sh"
# shellcheck source=lib/error_contract.sh
source "${_DEFER_LIB_DIR}/error_contract.sh"
# shellcheck source=lib/pp_run_ops.sh
source "${_DEFER_LIB_DIR}/pp_run_ops.sh"

# --- Declare accepted flags for this script ---
OPERATOR_VALID_FLAGS=(project until help)

# --- Parse arguments (--until is outside the canonical operator_args.sh
#     value-flag vocabulary, so it is added for this parse only) ---
operator_args_parse --extra-value-flags "until" "$@"

# --- Handle --help ---
if argparse_has help; then
    operator_args_render_help_for_flags "defer.sh" \
        "Arm the DEFER-LOW-BUGS deferral campaign for a project, until the named release ships." \
        OPERATOR_VALID_FLAGS \
        "" \
        "Writes PROJECT_ROOT/DEFER-LOW-BUGS carrying the validated until= target." \
        "See undefer.sh to clear the campaign and why.sh to see the posture it produces."
    exit 0
fi

# --- Reject any flag not in the declared vocabulary ---
operator_args_validate_known "defer.sh" OPERATOR_VALID_FLAGS || exit 1

# --- Resolve kanban root ---
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
export KANBAN_ROOT

# --- Resolve project (flag, then $PGAI_PROJECT_NAME) ---
_project_raw="$(operator_args_project)"
if [[ -z "${_project_raw}" ]]; then
    # BLOCK-WORTHY (docs/blocking-site-census.md): a version boundary names a
    # different release in every project's version space, so a campaign
    # without a named project has no target to resolve against -- an
    # invariant-by-design stop, not a judgement call.
    pgai_emit_error_contract \
        "defer.project_required" \
        "defer.sh: no project named -- a deferral campaign is always per-project (an until= target names a different release boundary in every project's version space)." \
        "Set \$PGAI_PROJECT_NAME to a project registered in projects.cfg, or pass --project directly, e.g. team/scripts/defer.sh --project your-project --until vX.Y.Z."
    # CENSUS-ANCHOR: defer-arm-project-required-01
    exit 1
fi

# --- Validate, resolve and arm via the Python library ---
pp_run_ops pgai_agent_kanban.ops defer "${KANBAN_ROOT}" "${_project_raw}" "$(operator_args_get until)" || exit 1
exit 0
