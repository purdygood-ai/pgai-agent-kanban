#!/usr/bin/env bash
# team/scripts/halt-after.sh
# CLI wrapper: arm the HALT-AFTER soft-drain signal for a project.
#
# Usage: halt-after.sh [OPTIONS]
#   --project NAME    Project to arm (default: $PGAI_PROJECT_NAME)
#   --key TOKEN       Drain event token (default: rc)
#   --until vX.Y.Z    Arm the versioned rc:vX.Y.Z token directly (rc scope
#                      only). A leading 'v' is optional and normalised,
#                      matching defer.sh's --until handling.
#   --help, -h        Show help and exit
#
# Thin adapter: parses --project, --key, and --until via operator_args.sh,
# resolves the project root via project_paths.sh, and delegates the on-disk
# change to the Python halt_after function in pgai_agent_kanban.ops.write.
# No mutation logic lives in this file.
#
# Provenance: a write that changes the sentinel also appends one record --
# time, op, path, entry, pid, parent (this script's command line), user --
# to ${KANBAN_ROOT}/logs/sentinel-writes.log, so the gated runners' sentinel
# check can tell this arm from a test leak. The sentinel's bytes, this
# script's output and its exit codes are unchanged; a record that cannot
# be written is one stderr warning, never a failure.
#
# HALT-AFTER semantics:
#   The HALT-AFTER file is evaluated by the Python halt_after module each
#   wake cycle.  When the drain condition for TOKEN is satisfied, the module
#   promotes the sentinel to a hard HALT.  The wake loop does NOT check
#   HALT-AFTER directly.
#
#   Supported tokens:
#     rc      — drain after current RC ships (default)
#     pm      — drain after current PM task completes
#     coder   — drain after current CODER task completes
#     writer  — drain after current WRITER task completes
#     tester  — drain after current TESTER task completes
#     cm      — drain after current CM task completes
#
#   --until vX.Y.Z writes the SAME versioned token drain.py's own in-flight
#   RC capture produces (rc:vX.Y.Z) -- it is not a second drain mode, just
#   a second way to fill in the one existing token. It only ever binds to
#   the rc scope: the five role tokens (pm/coder/writer/tester/cm) drain on
#   task state and have no version boundary for --until to name, so
#   combining --until with a --key naming one of them refuses rather than
#   silently ignoring one of the two flags.

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# --- Locate the lib directory relative to this script ---
_HALT_AFTER_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_HALT_AFTER_LIB_DIR="${_HALT_AFTER_SCRIPT_DIR}/lib"

# --- Source the standard argument layer ---
# shellcheck source=lib/operator_args.sh
source "${_HALT_AFTER_LIB_DIR}/operator_args.sh"

# --- Source the project path helpers ---
# shellcheck source=lib/project_paths.sh
source "${_HALT_AFTER_LIB_DIR}/project_paths.sh"

# --- Source the shared Python invocation helper ---
# shellcheck source=lib/pp_run_ops.sh
source "${_HALT_AFTER_LIB_DIR}/pp_run_ops.sh"

# --- Source the dual-channel error-contract emitter ---
# shellcheck source=lib/error_contract.sh
source "${_HALT_AFTER_LIB_DIR}/error_contract.sh"

# --- Declare accepted flags for this script ---
OPERATOR_VALID_FLAGS=(project key until help)

# --- Parse arguments (--until is outside the canonical operator_args.sh
#     value-flag vocabulary, so it is added for this parse only) ---
operator_args_parse --extra-value-flags "until" "$@"

# --- Handle --help ---
if argparse_has help; then
    operator_args_render_help_for_flags "halt-after.sh" \
        "Arm the HALT-AFTER soft-drain signal for a project." \
        OPERATOR_VALID_FLAGS \
        "" \
        "The --key flag sets the drain event token (default: rc)." \
        "Supported tokens: rc  pm  coder  writer  tester  cm" \
        "--until vX.Y.Z arms the versioned rc:vX.Y.Z token directly (rc scope only)."
    exit 0
fi

# --- Reject any flag not in the declared vocabulary ---
operator_args_validate_known "halt-after.sh" OPERATOR_VALID_FLAGS || exit 1

# --- Resolve kanban root ---
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
export KANBAN_ROOT

# --- Resolve the raw --until and --key values up front. The scope-mismatch
#     refusal immediately below applies whether or not a project was named,
#     so it must run before the project-required check that follows it. ---
_until_raw="$(operator_args_get until)"
_key_raw="$(operator_args_get key)"

# --- Refuse --until combined with any non-rc scope ---
if [[ -n "${_until_raw}" && -n "${_key_raw}" ]]; then
    _key_lower="${_key_raw,,}"
    if [[ "${_key_lower}" != "rc" ]]; then
        # BLOCK-WORTHY (docs/blocking-site-census.md): --until names a
        # release boundary; the five role tokens (pm/coder/writer/tester/cm)
        # drain on task state and have no version boundary for --until to
        # bind to. Honouring the flag here would mean either silently
        # ignoring --until or silently promoting the scope to rc, and both
        # reinterpret the operator's arm request instead of obeying it.
        pgai_emit_error_contract \
            "halt-after.until_scope_mismatch" \
            "halt-after.sh: --until targets a release boundary; scope '${_key_raw}' has none." \
            "Drop --until to arm the ${_key_raw} scope's own drain (team/scripts/halt-after.sh --key ${_key_raw}), or drop --key ${_key_raw} to arm the versioned rc scope (team/scripts/halt-after.sh --project your-project --until vX.Y.Z)."
        # CENSUS-ANCHOR: halt-after-until-scope-mismatch-01
        exit 1
    fi
fi

# --- Resolve project ---
_project_raw="$(operator_args_project)"
if [[ -z "${_project_raw}" ]]; then
    # BLOCK-WORTHY (docs/blocking-site-census.md): the HALT-AFTER sentinel,
    # its drain evaluation, and its promotion audit trail are always
    # per-project -- arming with no project named has no location to write
    # to, an invariant-by-design stop rather than a judgement call.
    pgai_emit_error_contract \
        "halt-after.project_required" \
        "halt-after.sh: no project named -- the HALT-AFTER sentinel is always per-project." \
        "Set \$PGAI_PROJECT_NAME to a project registered in projects.cfg, or pass --project directly, e.g. team/scripts/halt-after.sh --project your-project."
    # CENSUS-ANCHOR: halt-after-project-required-01
    exit 1
fi

# --- Validate a supplied --until value at write time, the earlier of the
#     two validation points: a malformed target is refused before it ever
#     reaches disk. Normalise an optional leading 'v'/'V', matching
#     defer.sh. ---
_until_normalised=""
if [[ -n "${_until_raw}" ]]; then
    _until_normalised="${_until_raw#v}"
    _until_normalised="${_until_normalised#V}"
    _until_normalised="v${_until_normalised}"
    if ! [[ "${_until_normalised}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        # BLOCK-WORTHY (docs/blocking-site-census.md): the same invariant
        # defer.sh's write-time --until check enforces -- a malformed
        # version is refused rather than resolved to a guessed one.
        pgai_emit_error_contract \
            "halt-after.until_malformed" \
            "halt-after.sh: --until value '${_until_raw}' is not well-formed semver (expected vX.Y.Z)." \
            "Re-run with a well-formed version, e.g. --until v2.14.0."
        # CENSUS-ANCHOR: halt-after-until-malformed-01
        exit 1
    fi
fi

# --- Resolve project name by exact match then unambiguous prefix ---
_project="$(pp_resolve_project_name "${_project_raw}")" || exit 1

# --- Resolve drain token: a validated --until writes the versioned rc
#     token, byte-identical to drain.py's own in-flight-RC capture;
#     otherwise fall back to --key (default: rc), unchanged from before
#     --until existed. ---
if [[ -n "${_until_normalised}" ]]; then
    _token="rc:${_until_normalised}"
else
    _token="${_key_raw:-rc}"
fi

# --- Resolve project root path ---
_project_root="$(pp_project_root "${_project}")" || exit 1

# --- Render the token for the arm confirmation through the one shared
#     "until vX.Y.Z" definition (pgai_agent_kanban.halt_after.token.
#     describe_token) rather than composing the phrase locally -- why.sh
#     --details' posture section (pgai_agent_kanban.ops.verdict) calls the
#     same function. Falls back to the raw token when PYTHONPATH cannot be
#     resolved (never blocks the arm itself on a describe-only failure). ---
_halt_after_describe_token() {
    local token="$1"
    pp_run_ops pgai_agent_kanban.halt_after.describe describe "${token}" 2>/dev/null || printf '%s' "${token}"
}

# --- Execute the operation via Python library ---
if pp_run_ops pgai_agent_kanban.ops halt_after "${_project_root}" "${_token}"; then
    _described="$(_halt_after_describe_token "${_token}")"
    if [[ "${_described}" != "${_token}" ]]; then
        printf 'halt-after: HALT-AFTER signal armed for project %s (token: %s, %s, path: %s/HALT-AFTER)\n' \
            "${_project}" "${_token}" "${_described}" "${_project_root}"
    else
        printf 'halt-after: HALT-AFTER signal armed for project %s (token: %s, path: %s/HALT-AFTER)\n' \
            "${_project}" "${_token}" "${_project_root}"
    fi
    exit 0
else
    printf 'halt-after: failed to arm HALT-AFTER signal for project %s\n' "${_project}" >&2
    exit 1
fi
