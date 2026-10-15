#!/usr/bin/env bash
# team/scripts/migrations/run-migrations.sh
#
# Destination-version-selected migration runner. Applies every migration
# script in this directory whose DESTINATION version -- the "vA.B.C" half of
# a migrate_vX.Y.Z_to_vA.B.C.sh filename -- falls in the half-open interval
# (--from, --to], in ascending destination-version order. That selection
# rule is defined once in team/scripts/migrations/README.md ("Selection rule
# (destination-keyed)"); this script is its executable form, not a second
# copy of the rule.
#
# This is the auto-runner team/scripts/migrations/README.md promised would
# be integrated "when the first real migration is written". It is invoked
# by team/scripts/upgrade.sh on every upgrade, passing the installed and
# post-deposit VERSION as --from/--to, so an existing install picks up every
# migration whose destination it has not yet reached without an operator
# having to know a script exists. It can also be run by hand -- see Usage.
#
# Usage:
#   run-migrations.sh --from vX.Y.Z --to vA.B.C [--project <name>] [--dry-run]
#
# --from vX.Y.Z      The version currently installed (exclusive lower bound).
# --to   vA.B.C      The version being installed (inclusive upper bound).
# --project <name>   Forwarded to every selected migration script's own
#                     --project flag; limits the run to one project. Omit to
#                     run every registered project (each migration script's
#                     own default when --project is not passed).
# --dry-run          Forwarded to every selected migration script's own
#                     --dry-run flag; preview only, no writes.
#
# Selection rule (destination-keyed), restated for the executable: a script
# named migrate_vX.Y.Z_to_vA.B.C.sh runs when vA.B.C is strictly greater than
# --from AND less than or equal to --to. The "from" half of the filename
# documents intent only; it is never the sort or selection key.
#
# Sparse rule: zero migration scripts in this directory, or zero whose
# destination falls in the selected range, is the common case (most version
# bumps need no migration) and is NOT an error -- see README.md "Sparse
# rule". This runner reports "nothing to apply" and exits 0.
#
# Migration contract (README.md "Migration contract"): every migration
# script is additive, non-destructive, and idempotent, so re-running this
# runner with the same --from/--to (or a range that re-covers an
# already-applied script) is safe -- each script detects its own prior
# application and no-ops.
#
# Exit codes:
#   0 — success, including the sparse no-op case.
#   1 — usage error (missing or malformed --from/--to, unknown flag).
#   2 — a selected migration script exited non-zero. The runner aborts
#       immediately on the first failure (per the migration contract, no
#       partially-applied migration set is left silently unreported) rather
#       than continuing to the next script in the ordered list.

set -euo pipefail
# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

_RUN_MIG_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/argparse.sh
source "${_RUN_MIG_SCRIPT_DIR}/../lib/argparse.sh"
# shellcheck source=../lib/operator_args.sh
source "${_RUN_MIG_SCRIPT_DIR}/../lib/operator_args.sh"
# shellcheck source=../lib/semver.sh
source "${_RUN_MIG_SCRIPT_DIR}/../lib/semver.sh"

OPERATOR_VALID_FLAGS=(from to project dry-run help)
# Separate array for --help rendering only: "from"/"to"/"project" all need
# wording that differs from operator_args.sh's canonical descriptions (the
# library has no "from"/"to" entry at all, and this script's --project does
# NOT fall back to $PGAI_PROJECT_NAME -- see the SINGLE_PROJECT assignment
# below), so all three are rendered as hand-written EXTRA_LINE text instead.
# OPERATOR_VALID_FLAGS itself (used by operator_args_validate_known) still
# declares all five as accepted flags.
_HELP_FLAGS=(dry-run help)

_USAGE_TEXT="$(operator_args_render_help_for_flags "run-migrations.sh" \
    "Apply every migrations/migrate_vX.Y.Z_to_vA.B.C.sh script whose destination version falls in (--from, --to], ascending (see team/scripts/migrations/README.md, Selection rule)." \
    _HELP_FLAGS \
    "  --from vX.Y.Z      Installed version (exclusive lower bound)" \
    "  --to vX.Y.Z        Target version (inclusive upper bound)" \
    "  --project NAME     Limit to one registered project (default: every registered project; NOT \$PGAI_PROJECT_NAME)")"

operator_args_argparse --value-flags "from to project" -- "$@"

if argparse_has "help" || argparse_has "h"; then
    printf '%s\n' "$_USAGE_TEXT" >&2
    exit 0
fi

operator_args_validate_known "$(basename "$0")" OPERATOR_VALID_FLAGS || exit 1

if [[ "${#ARGPARSE_POSITIONAL[@]}" -gt 0 ]]; then
    echo "ERROR: unexpected positional argument: ${ARGPARSE_POSITIONAL[0]}" >&2
    printf '%s\n' "$_USAGE_TEXT" >&2
    exit 1
fi

if ! argparse_has "from" || argparse_missing "from"; then
    echo "ERROR: --from requires a value (e.g. --from v2.22.5)" >&2
    exit 1
fi
if ! argparse_has "to" || argparse_missing "to"; then
    echo "ERROR: --to requires a value (e.g. --to v2.23.0)" >&2
    exit 1
fi
if argparse_missing "project"; then
    echo "ERROR: --project requires a value" >&2
    exit 1
fi

FROM_VERSION="${ARGPARSE_FLAGS[from]}"
TO_VERSION="${ARGPARSE_FLAGS[to]}"

if [[ ! "$FROM_VERSION" =~ ^v?[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "ERROR: --from value '${FROM_VERSION}' is not well-formed semver (expected vX.Y.Z)." >&2
    exit 1
fi
if [[ ! "$TO_VERSION" =~ ^v?[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "ERROR: --to value '${TO_VERSION}' is not well-formed semver (expected vX.Y.Z)." >&2
    exit 1
fi

DRY_RUN="false"
argparse_has "dry-run" && DRY_RUN="true"
# Deliberately NOT operator_args_project(): this runner's whole-framework
# default (every registered project) must not be silently narrowed by an
# ambient $PGAI_PROJECT_NAME the invoking shell happens to carry (e.g. a
# kanban agent's own task context) -- only an explicit --project narrows
# the scope here.
SINGLE_PROJECT="$(operator_args_get project)"

# ---------------------------------------------------------------------------
# _destination_version <migration_script_path>
# Echoes the "vA.B.C" destination half of a migrate_vX.Y.Z_to_vA.B.C.sh
# filename, or an empty string if the filename does not match the
# convention (team/scripts/migrations/README.md "Naming convention").
# ---------------------------------------------------------------------------
_destination_version() {
    local base
    base="$(basename "$1")"
    [[ "$base" =~ ^migrate_v[0-9]+\.[0-9]+\.[0-9]+_to_(v[0-9]+\.[0-9]+\.[0-9]+)\.sh$ ]] || return 0
    printf '%s' "${BASH_REMATCH[1]}"
}

# ---------------------------------------------------------------------------
# Build the ordered list of migration scripts whose destination version
# falls in (FROM_VERSION, TO_VERSION], ascending by destination version.
# Non-conforming filenames (no destination match) are silently skipped --
# this directory may hold README.md and other non-script files.
# ---------------------------------------------------------------------------
_SELECTED=()
while IFS= read -r -d '' _mig_file; do
    _mig_dest="$(_destination_version "$_mig_file")"
    [[ -z "$_mig_dest" ]] && continue
    if semver_gt "$_mig_dest" "$FROM_VERSION" && semver_lte "$_mig_dest" "$TO_VERSION"; then
        _SELECTED+=("${_mig_dest}	${_mig_file}")
    fi
done < <(find "${_RUN_MIG_SCRIPT_DIR}" -maxdepth 1 -name 'migrate_v*.sh' -print0 | sort -z)
unset _mig_file _mig_dest

if [[ "${#_SELECTED[@]}" -eq 0 ]]; then
    echo "run-migrations.sh: no migration scripts have a destination version in (${FROM_VERSION}, ${TO_VERSION}] -- nothing to apply"
    exit 0
fi

# Ascending destination-version order.
mapfile -t _SELECTED_SORTED < <(printf '%s\n' "${_SELECTED[@]}" | sort -t $'\t' -k1,1V)

echo "run-migrations.sh: applying ${#_SELECTED_SORTED[@]} migration(s) selected for (${FROM_VERSION}, ${TO_VERSION}]"

for _entry in "${_SELECTED_SORTED[@]}"; do
    _dest="${_entry%%$'\t'*}"
    _script="${_entry#*$'\t'}"
    _args=()
    [[ -n "$SINGLE_PROJECT" ]] && _args+=(--project "$SINGLE_PROJECT")
    [[ "$DRY_RUN" == "true" ]] && _args+=(--dry-run)

    echo "  [run] $(basename "$_script") (destination ${_dest})"
    _mig_rc=0
    if [[ -n "$SINGLE_PROJECT" ]]; then
        bash "$_script" "${_args[@]}" || _mig_rc=$?
    else
        # No --project was given to THIS runner: the intended scope is every
        # registered project. Clear PGAI_PROJECT_NAME for the child so an
        # ambient value in the invoking shell (e.g. a kanban agent's own task
        # context) cannot silently narrow the migration script's own
        # operator_args_project() fallback to one project.
        env -u PGAI_PROJECT_NAME bash "$_script" "${_args[@]}" || _mig_rc=$?
    fi
    if [[ "$_mig_rc" -ne 0 ]]; then
        echo "ERROR: migration script failed: $(basename "$_script") (exit ${_mig_rc})" >&2
        echo "Per the migration contract (additive/non-destructive/idempotent), it is safe to fix the" >&2
        echo "underlying cause and re-run run-migrations.sh --from ${FROM_VERSION} --to ${TO_VERSION};" >&2
        echo "already-applied migrations in this range detect their own prior application and no-op." >&2
        # Classification (docs/blocking-site-census.md vocabulary): BLOCK-WORTHY
        # -- an existing install's project.cfg does not carry the config change
        # a later release depends on; letting the upgrade report success here
        # would silently leave that install in a layout no later migration is
        # guaranteed to detect or repair. This directory is not on the census
        # lint's scanned file list (team/scripts/lint_gate_census_compliance.py
        # _CENSUS_BASH_FILES) as of this writing, so no formal CENSUS-ANCHOR /
        # Locator Index entry exists yet; the classification is recorded here
        # in prose for the next reader, matching this script's own operating
        # precedent rather than inventing a half-registered anchor.
        exit 2
    fi
    unset _dest _script _args
done
unset _entry

echo "run-migrations.sh: all selected migrations applied"
exit 0
