#!/usr/bin/env bash
# team/scripts/deep-verify-freshness-check.sh
# The pre-release promotion gate: does the project's DEEP-VERIFY ledger hold
# a fresh green record for the exact version about to be promoted to a
# public tag?
#
# docs/promotion-playbook.md lists this as a "When to promote" precondition
# -- promotion to a public tag is the last mile between "shipped internally"
# and "a user can clone it," and the expensive cold-build lane no longer
# runs on every RC (team/scripts/deep-verify.sh runs it on demand, on a
# weekly schedule, or here, pre-release). That trade only holds if the lane
# provably ran, green, against the tree state actually being disclosed.
# This script is that named, executable promotion-checklist step -- short
# enough to paste into the playbook.
#
# A separate verb, not a mode on deep-verify.sh: deep-verify.sh's job is to
# RUN the lane and record what happened; this script's job is to READ what
# was already recorded and decide whether that record still speaks for the
# version being promoted. Splitting them keeps deep-verify.sh's argument
# surface (--scheduled dispatch, the cold-lane invocation, the bug filer)
# untouched by an unrelated read-only decision, and gives the playbook a
# short, purpose-named command rather than a mode flag buried in a busier
# script's help text.
#
# Usage: deep-verify-freshness-check.sh --project <name> --version <vX.Y.Z> [--help|-h]
#
# Freshness policy (the one place it is stated; see
# pgai_agent_kanban.deep_verify.freshness's own module docstring for the
# full rationale -- this comment and that one must describe the same
# policy in the same words):
#
#   A deep-verify record satisfies promotion of version V when ALL of:
#     1. The project has a record at all (never run refuses).
#     2. The record's Verdict is exactly "built" -- "failed-to-build" and
#        "could-not-attempt" both refuse (SOP-core.md, "Gates and
#        Refusals" -- "Unusable build blocks; an unavailable build
#        environment does not").
#     3. The record's Version field equals V exactly -- a green record for
#        a different version does not speak for the tree state being
#        promoted now.
#     4. The record's Timestamp is no older than PGAI_DEEP_VERIFY_
#        FRESHNESS_WINDOW_DAYS days (default: 7, the same cadence the
#        DEEP-VERIFY tier's own weekly scheduled mode uses) before this
#        check runs.
#
# This check is READ-ONLY: it reads the project's deep-verify ledger and
# the project's registration/config (project.cfg via projects.cfg), and
# writes nothing at all -- not the ledger, not release-state.md, not a
# HALT file. It never runs the lane itself; a stale or red result is
# reported for the operator to act on, never silently repaired by kicking
# off the thirteen-minute lane the operator did not ask for.
#
# This gate governs operator promotion only. It is not wired into
# cm/release.sh, the ship-policy matrix, or any agent wake path -- the
# autonomous chain's ship behavior is unchanged by this script's existence.
#
# Exit codes:
#   0  = the ledger's most recent record for --project is Verdict=built,
#        for exactly --version, within the freshness window -- promotion
#        may proceed
#   1  = one of: --project/--version missing or --project unregistered
#        (usage-class refusal); no record at all; the ledger is malformed
#        or unreadable; the most recent record's Verdict is
#        failed-to-build or could-not-attempt; the most recent record is
#        Verdict=built but for a different version and/or older than the
#        freshness window
#
# Configuration:
#   PGAI_AGENT_KANBAN_ROOT_PATH             — kanban root (default: $HOME/pgai_agent_kanban)
#   PGAI_DEEP_VERIFY_FRESHNESS_WINDOW_DAYS  — override the freshness window
#                                              in days (default: 7).
#   PGAI_DEEP_VERIFY_FRESHNESS_NOW          — ISO-8601 UTC timestamp
#                                              overriding "now" (e.g.
#                                              2026-09-03T12:00:00+00:00).
#                                              Tests use this seam to inject
#                                              the clock instead of
#                                              depending on the wall-clock
#                                              date of the test run. Unset
#                                              or empty means "use the real
#                                              current UTC time."

set -euo pipefail
# --- Bootstrap: self-locate → source shell-env → fail loud ---
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# --- Locate the lib directory relative to this script ---
_FC_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_FC_LIB_DIR="${_FC_SCRIPT_DIR}/lib"

# --- Source the standard argument layer ---
# shellcheck source=lib/operator_args.sh
source "${_FC_LIB_DIR}/operator_args.sh"

# --- Source the project path helpers ---
# shellcheck source=lib/project_paths.sh
source "${_FC_LIB_DIR}/project_paths.sh"

# --- Source the projects.cfg registry helpers (for --project validation) ---
# shellcheck source=lib/projects.sh
source "${_FC_LIB_DIR}/projects.sh"

# --- Source the dual-channel error-contract emitter ---
# shellcheck source=lib/error_contract.sh
source "${_FC_LIB_DIR}/error_contract.sh"

# --- Source the shared Python invocation helper ---
# shellcheck source=lib/pp_run_ops.sh
source "${_FC_LIB_DIR}/pp_run_ops.sh"

# --- Source the deep-verify ledger read adapter ---
# shellcheck source=lib/deep_verify_ledger.sh
source "${_FC_LIB_DIR}/deep_verify_ledger.sh"

# --- Source the container-tier helpers: every remedy below that re-runs
# deep-verify.sh is built by container_tier_operator_command, so it carries
# the operator authorization and an image selection and runs as printed
# from an operator's shell. ---
# shellcheck source=lib/container_tier.sh
source "${_FC_LIB_DIR}/container_tier.sh"

# --- Declare accepted flags for this script (--version is not in the
# canonical value-flag set) ---
OPERATOR_VALID_FLAGS=(project version help)
declare -A OPERATOR_FLAG_HELP=(
    [version]="  --version vX.Y.Z   Version being promoted (required); the latest ledger record must be for exactly it"
)

# --- Parse arguments ---
operator_args_parse --extra-value-flags "version" "$@"

# --- Handle --help ---
if argparse_has help; then
    operator_args_render_help_for_flags "deep-verify-freshness-check.sh" \
        "Refuse promotion unless the deep-verify ledger holds a fresh green record for --version." \
        OPERATOR_VALID_FLAGS \
        "" \
        "Read-only pre-release promotion gate (docs/promotion-playbook.md," \
        "'When to promote'): reads the named project's deep-verify ledger" \
        "(\$KANBAN_ROOT/projects/<name>/deep-verify-ledger.md) and exits 0" \
        "only when the most recent record is Verdict=built, for exactly" \
        "--version, within the freshness window. Writes nothing; never runs" \
        "the lane itself." \
        "" \
        "Freshness policy (default): Verdict=built, Version matches" \
        "--version exactly, and Timestamp is no older than 7 days (override" \
        "with PGAI_DEEP_VERIFY_FRESHNESS_WINDOW_DAYS)." \
        "" \
        "Exit codes: 0 = fresh green record found. 1 = no record at all; a" \
        "malformed/unreadable ledger; Verdict=failed-to-build or" \
        "could-not-attempt; or a built record that is for a different" \
        "version and/or older than the freshness window."
    exit 0
fi

# --- Reject any flag not in the declared vocabulary ---
operator_args_validate_known "deep-verify-freshness-check.sh" OPERATOR_VALID_FLAGS || exit 1

# --- Resolve kanban root ---
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
export KANBAN_ROOT

# --- Resolve --project (required; usage-class refusal, not a promotion
# policy verdict, so it stays a plain message -- mirrors deep-verify.sh's
# own "--project is required" line). ---
_fc_project="$(operator_args_project)"
if [[ -z "${_fc_project}" ]]; then
    printf 'deep-verify-freshness-check.sh: --project is required (or set $PGAI_PROJECT_NAME)\n' >&2
    exit 1
fi

# --- Resolve --version (required; the vX.Y.Z tag about to be promoted --
# named explicitly by the operator, the same way every other step in
# docs/promotion-playbook.md names vX.Y.Z explicitly rather than having each
# step re-derive "the" current version). Usage-class refusal. ---
_fc_target_version="$(operator_args_get version)"
if [[ -z "${_fc_target_version}" ]]; then
    printf 'deep-verify-freshness-check.sh: --version is required (the vX.Y.Z tag being promoted)\n' >&2
    exit 1
fi
if [[ ! "${_fc_target_version}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    printf 'deep-verify-freshness-check.sh: --version value %q is not well-formed semver (expected vX.Y.Z)\n' "${_fc_target_version}" >&2
    exit 1
fi

# --- The deep-verify run every lane-related remedy below names: operator
# authorization prefix, absolute script path, --image all (a promotion
# needs every declared image verified). A remedy is the command itself,
# with any context after a shell comment, so it runs verbatim. ---
_fc_rerun_cmd="$(container_tier_operator_command "${_FC_SCRIPT_DIR}/deep-verify.sh" --project "${_fc_project}" --image all)"

# --- Validate --project registration. ---
if ! projects_cfg_has "${_fc_project}"; then
    # BLOCK-WORTHY (roles/CODER.md, "Authoring a new gate"): an unregistered project
    # name cannot be silently resolved to a directory that may not exist --
    # the ledger this gate reads would be an orphaned path no promotion
    # ever happened against. Refusing is an invariant-by-design stop, not a
    # self-service or quality-class finding, and it writes nothing.
    pgai_emit_error_contract \
        "freshness_gate.unregistered_project" \
        "--project '${_fc_project}' is not registered in $(projects_cfg_path)." \
        "Register the project first: team/scripts/add-project.sh --project ${_fc_project}; or correct --project to a name already listed in $(projects_cfg_path)."
    # CENSUS-ANCHOR: deep-verify-freshness-check-01
    exit 1
fi

# --- Read the ledger's most recent record. A non-zero return here means
# the ledger file exists but could not be parsed at all, or its most
# recent record is missing a required field or carries an out-of-
# vocabulary verdict -- deep_verify_ledger_read_latest's own contract
# (team/scripts/lib/deep_verify_ledger.sh). ---
if ! _fc_latest_output="$(deep_verify_ledger_read_latest "${_fc_project}" 2>&1)"; then
    # BLOCK-WORTHY (roles/CODER.md, "Authoring a new gate"): a record that
    # is absent, unreadable, malformed, ambiguous, or whose verdict cannot
    # be determined refuses. A
    # malformed ledger cannot be trusted to answer "is this fresh" -- it
    # refuses rather than silently reporting never-run (which would let a
    # promotion proceed on stale silence) or crashing with an unhandled
    # error.
    pgai_emit_error_contract \
        "freshness_gate.ledger_unreadable" \
        "the deep-verify ledger for project '${_fc_project}' exists but could not be read: ${_fc_latest_output}" \
        "${_fc_rerun_cmd}  # after repairing or removing the malformed record in $(deep_verify_ledger_path "${_fc_project}")"
    # CENSUS-ANCHOR: deep-verify-freshness-check-02
    exit 1
fi

# --- Parse the "key=value" block into fields. ---
declare -A _fc_fields=()
while IFS= read -r _fc_line; do
    case "${_fc_line}" in
        found=*) _fc_fields[found]="${_fc_line#found=}" ;;
        timestamp=*) _fc_fields[timestamp]="${_fc_line#timestamp=}" ;;
        project=*) _fc_fields[project]="${_fc_line#project=}" ;;
        version=*) _fc_fields[version]="${_fc_line#version=}" ;;
        commit=*) _fc_fields[commit]="${_fc_line#commit=}" ;;
        verdict=*) _fc_fields[verdict]="${_fc_line#verdict=}" ;;
        detail=*) _fc_fields[detail]="${_fc_line#detail=}" ;;
    esac
done <<< "${_fc_latest_output}"

if [[ "${_fc_fields[found]:-false}" != "true" ]]; then
    # BLOCK-WORTHY (roles/CODER.md, "Authoring a new gate"): the project has never
    # been deep-verified. Silence must never buy a promotion (this
    # ticket's own Constraints) -- refuse rather than treating "no record"
    # as an implicit pass.
    pgai_emit_error_contract \
        "freshness_gate.no_record" \
        "project '${_fc_project}' has no deep-verify record at all -- the DEEP-VERIFY tier has never run for it." \
        "${_fc_rerun_cmd}"
    # CENSUS-ANCHOR: deep-verify-freshness-check-03
    exit 1
fi

# --- Resolve the freshness window and clock (both test-overridable). ---
_fc_window_days="${PGAI_DEEP_VERIFY_FRESHNESS_WINDOW_DAYS:-7}"
_fc_now_iso="${PGAI_DEEP_VERIFY_FRESHNESS_NOW:-$(date -u +%Y-%m-%dT%H:%M:%S+00:00)}"

# --- Classify the record (pure decision, routed through pp_run_ops per
# this file's own convention -- never a bare python module invocation). ---
_fc_result="" _fc_age=""
_fc_reasons=()
while IFS= read -r _fc_out_line; do
    case "${_fc_out_line}" in
        result=*) _fc_result="${_fc_out_line#result=}" ;;
        age=*) _fc_age="${_fc_out_line#age=}" ;;
        reason=*) _fc_reasons+=("${_fc_out_line#reason=}") ;;
    esac
done < <(pp_run_ops pgai_agent_kanban.deep_verify.freshness \
    "${_fc_target_version}" "${_fc_fields[found]}" "${_fc_fields[timestamp]}" \
    "${_fc_fields[version]}" "${_fc_fields[verdict]}" "${_fc_now_iso}" "${_fc_window_days}")

case "${_fc_result}" in
    fresh)
        echo "deep-verify-freshness-check: project=${_fc_project} version=${_fc_target_version} verdict=built (fresh; ${_fc_age} old, within ${_fc_window_days}-day window) -- OK to promote"
        echo "deep-verify-freshness-check: record timestamp=${_fc_fields[timestamp]} tree-state: version=${_fc_fields[version]} commit=${_fc_fields[commit]}"
        exit 0
        ;;
    failed_to_build)
        # BLOCK-WORTHY (roles/CODER.md, "Authoring a new gate"): the most
        # recent record is a real tree defect (SOP-core.md, "Gates and
        # Refusals" -- "Unusable build blocks; an unavailable build
        # environment does not") -- promoting on top of
        # it publishes something known not to build.
        pgai_emit_error_contract \
            "freshness_gate.failed_to_build" \
            "the most recent deep-verify record for '${_fc_project}' (timestamp ${_fc_fields[timestamp]}) has Verdict=failed-to-build: ${_fc_fields[detail]} -- this is a tree defect and must be fixed before promotion." \
            "${_fc_rerun_cmd}  # after fixing the failing build named in the record's detail"
        # CENSUS-ANCHOR: deep-verify-freshness-check-04
        exit 1
        ;;
    could_not_attempt)
        # BLOCK-WORTHY (roles/CODER.md, "Authoring a new gate"): a
        # could-not-attempt verdict means nothing was learned about
        # whether the images build (SOP-core.md, "Gates and Refusals" --
        # "Unusable build blocks; an unavailable build environment does
        # not," outcome 3); an exit-1 record can still name a failing
        # engine-free cold test, whose bug that run filed. It can never
        # satisfy promotion, regardless of how recently it ran.
        pgai_emit_error_contract \
            "freshness_gate.could_not_attempt" \
            "the most recent deep-verify record for '${_fc_project}' (timestamp ${_fc_fields[timestamp]}) has Verdict=could-not-attempt: ${_fc_fields[detail]} -- nothing was learned about whether the images build, so this record can never satisfy promotion no matter how recent (a failing engine-free cold test named in the detail is a separate finding, already filed by that run)." \
            "${_fc_rerun_cmd}  # on a host with a working container engine and reachable registries"
        # CENSUS-ANCHOR: deep-verify-freshness-check-05
        exit 1
        ;;
    stale)
        _fc_reason_text="$(IFS='; '; echo "${_fc_reasons[*]}")"
        # BLOCK-WORTHY (roles/CODER.md, "Authoring a new gate"): the record is
        # green, but it does not speak for the version being promoted --
        # either a different tree state, or older than the freshness
        # policy allows. Conservative direction is refuse.
        pgai_emit_error_contract \
            "freshness_gate.stale_record" \
            "the most recent deep-verify record for '${_fc_project}' is Verdict=built but does not speak for version ${_fc_target_version}: ${_fc_reason_text} -- freshness policy requires a built record for this exact version within ${_fc_window_days} days." \
            "${_fc_rerun_cmd}"
        # CENSUS-ANCHOR: deep-verify-freshness-check-06
        exit 1
        ;;
    *)
        # Defensive-only: pgai_agent_kanban.deep_verify.ledger.latest_record()
        # already guarantees Verdict is one of built/failed-to-build/
        # could-not-attempt before this script ever reaches the classifier,
        # so this branch is not reachable in normal operation. Refuse
        # rather than guess if it is ever hit (a renamed/added result value
        # the classifier started emitting without this script's case
        # statement being updated to match).
        printf 'deep-verify-freshness-check.sh: internal error: unrecognized classifier result %q for project %s -- refusing rather than guessing whether promotion is safe\n' "${_fc_result}" "${_fc_project}" >&2
        exit 1
        ;;
esac
