#!/usr/bin/env bash
# team/scripts/cold-build-now.sh
# Narrow operator verb: run the cold-build lane on demand, outside any RC
# (pre-upgrade checks, "upstream just changed, did our images survive it"
# days).
#
# cold-build IS the DEEP-VERIFY tier's only member today (see
# team/scripts/deep-verify.sh), so this script is a thin, named alias: it
# accepts exactly the same --project flag (plus the container-tier flags
# --image and --operator-approved, passed through unchanged), and delegates
# the run and the ledger record to deep-verify.sh rather than
# re-implementing any part of the lane invocation, the verdict
# classification, the operator-only gate, or the ledger write.
# When a second expensive suite joins the tier, deep-verify.sh gains a
# second mode of its own; this script's contract (run the cold lane for
# --project NAME) does not change underneath it.
#
# Usage: PGAI_CONTAINER_TIER=1 cold-build-now.sh --project <name> --image <name|all> [--help|-h]
#
# Operator-only, like deep-verify.sh: without PGAI_CONTAINER_TIER=1 or
# --operator-approved, or from inside an agent wake, deep-verify.sh prints
# one SKIPPED line and exits 0 with no ledger record.
#
# Exit codes: identical to team/scripts/deep-verify.sh's own contract --
#   0  = the run completed and the ledger records verdict=built or
#        verdict=could-not-attempt, with no failing non-build cold test;
#        or the run was SKIPPED or no image was selected (no record written)
#   1  = the run completed and the ledger records verdict=failed-to-build,
#        OR verdict=built but a non-build cold test failed (a bug naming
#        the test is filed or cited), OR verdict=could-not-attempt but the
#        lane exited 1 because a non-build cold test failed while no image
#        could be built (a bug naming the test is filed or cited), OR
#        --project did not resolve to a registered project (no record
#        written in that case)
#   3  = refused: an image name the manifest does not declare (no record)
#
# Configuration:
#   PGAI_AGENT_KANBAN_ROOT_PATH  — kanban root (default: $HOME/pgai_agent_kanban)

set -euo pipefail
# --- Bootstrap: self-locate → source shell-env → fail loud ---
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# --- Locate the lib directory relative to this script ---
_CBN_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_CBN_LIB_DIR="${_CBN_SCRIPT_DIR}/lib"

# --- Source the container-tier flag handling (--image, --operator-approved
# are passed through to deep-verify.sh, which owns the gate) ---
# shellcheck source=lib/container_tier.sh
source "${_CBN_LIB_DIR}/container_tier.sh"

# --- Source the standard argument layer ---
# shellcheck source=lib/operator_args.sh
source "${_CBN_LIB_DIR}/operator_args.sh"

# --- Declare accepted flags for this script (identical vocabulary to
# deep-verify.sh -- this is a named alias for exactly one of its calls). ---
OPERATOR_VALID_FLAGS=(project help)

# --- Parse arguments (container-tier flags separated out first) ---
container_tier_take_args "$@"
operator_args_parse ${CONTAINER_TIER_REST_ARGS[@]+"${CONTAINER_TIER_REST_ARGS[@]}"}

# --- Handle --help ---
if argparse_has help; then
    operator_args_render_help_for_flags "cold-build-now.sh" \
        "Run the cold-build lane on demand, outside any RC." \
        OPERATOR_VALID_FLAGS \
        "" \
        "A thin, named alias for deep-verify.sh run with --project NAME -- cold-build" \
        "is the DEEP-VERIFY tier's only member today, so this verb and that" \
        "one do exactly the same work: run the cold lane, classify the" \
        "outcome, and append one record to the named project's deep-verify" \
        "ledger (\$KANBAN_ROOT/projects/<name>/deep-verify-ledger.md)." \
        "" \
        "Operator-only: without PGAI_CONTAINER_TIER=1 or --operator-approved," \
        "or from inside an agent wake, the run prints one SKIPPED line and" \
        "exits 0 with no ledger record. Operator form, e.g.:" \
        "  PGAI_CONTAINER_TIER=1 scripts/cold-build-now.sh --project pgai-agent-kanban --image all" \
        "" \
        "  --image NAME       Passed to deep-verify.sh: an image from" \
        "                     team/config/container-images.txt (repeatable), or all." \
        "                     Default: kanban.cfg [release] container_images; with" \
        "                     neither, the available images are listed and nothing runs." \
        "  --operator-approved  Operator authorization, same as PGAI_CONTAINER_TIER=1." \
        "" \
        "Exit codes: 0 = verdict is built or could-not-attempt and no" \
        "non-build cold test failed, or the run was SKIPPED or no image was" \
        "selected (no record); 1 = verdict is failed-to-build, or verdict" \
        "is built but a non-build cold test" \
        "failed (a bug naming the test is filed or cited), or verdict is" \
        "could-not-attempt but a non-build cold test failed (the lane exited" \
        "1; a bug naming the test is filed or cited), or --project did" \
        "not resolve to a registered project (no record is written in that" \
        "case); 3 = refused, an image name the manifest does not declare" \
        "(no record)."
    exit 0
fi

# --- Reject any flag not in the declared vocabulary ---
operator_args_validate_known "cold-build-now.sh" OPERATOR_VALID_FLAGS || exit 1

# --- Resolve --project (required; delegated script re-validates and
# re-resolves it independently -- no project-registration logic is
# duplicated here). ---
_cbn_project="$(operator_args_get project)"
if [[ -z "${_cbn_project}" ]]; then
    printf 'cold-build-now.sh: --project is required (or set $PGAI_PROJECT_NAME)\n' >&2
    exit 1
fi

# --- Delegate the run, the classification, and the ledger write to
# deep-verify.sh -- one implementation, two entry points. ---
exec bash "${_CBN_SCRIPT_DIR}/deep-verify.sh" --project "${_cbn_project}" \
    ${CONTAINER_TIER_PASSTHROUGH_ARGS[@]+"${CONTAINER_TIER_PASSTHROUGH_ARGS[@]}"}
