#!/usr/bin/env bash
# team/scripts/set-version-ceiling.sh
#
# Operator helper to read, set, or lift the max_minor / max_major version
# ceilings in the [versioning] section of a project's project.cfg.
#
# An absent key is not "no ceiling": it reads as its documented default
# (max_minor 13, max_major 0), so deleting a line restores the default. The
# explicit no-ceiling spelling is `none`, which --no-minor / --no-major
# write. --show prints the value in effect: a number, the default marked
# "(default)", none, or a malformed value marked invalid (discovery treats
# that project as ceiling-blocked).
#
# Thin adapter: parses the flags via operator_args.sh, resolves the project
# by exact match then unambiguous prefix, refuses a call with no action, and
# hands the rest to the Python set_version_ceiling function in
# pgai_agent_kanban.ops.write (the same function
# POST /operations/set-version-ceiling calls). That function validates the
# values, then shows, previews (--dry-run) or applies the change: an
# existing line becomes max_minor=N in place, a missing one is added to
# [versioning], and --no-minor / --no-major write max_minor = none the same
# way. Comments and every other key keep their bytes, and project.cfg is
# replaced atomically with its permission mode unchanged.
#
# Usage:
#   set-version-ceiling.sh --project <name> --show
#   set-version-ceiling.sh --project <name> --minor <N>
#   set-version-ceiling.sh --project <name> --major <N>
#   set-version-ceiling.sh --project <name> --no-minor
#   set-version-ceiling.sh --project <name> --no-major
#   set-version-ceiling.sh --project <name> --minor <N> --major <N>
#   set-version-ceiling.sh --project <name> --no-minor --no-major
#
# Options:
#   --project NAME    Project name (required; or set $PGAI_PROJECT_NAME).
#   --show            Print the max_minor and max_major values in effect
#                     (after any change).
#   --minor <N>       Set max_minor to the non-negative integer N.
#   --major <N>       Set max_major to the non-negative integer N.
#   --no-minor        Write max_minor = none (no minor ceiling).
#   --no-major        Write max_major = none (no major ceiling).
#   --dry-run         Preview changes without writing them.
#   --help | -h       Show this help.
#
# Exit codes:
#   0 — success (or --show with no error)
#   1 — usage error, unknown project, integer validation failure (the
#       dual-channel error/message/remedy block), or write failure
#   2 — project not found (project.cfg missing under KANBAN_ROOT/projects/<name>/)
#   3 — KANBAN_ROOT not set or not found

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# ---------------------------------------------------------------------------
# Resolve KANBAN_ROOT
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"

if [[ ! -d "$KANBAN_ROOT" ]]; then
    echo "ERROR: kanban root not found: $KANBAN_ROOT" >&2
    echo "Set PGAI_AGENT_KANBAN_ROOT_PATH or run install.sh" >&2
    exit 3
fi

# ---------------------------------------------------------------------------
# Source shared argument parser
# ---------------------------------------------------------------------------
_SVC_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/operator_args.sh
source "${_SVC_SCRIPT_DIR}/lib/operator_args.sh"
# shellcheck source=lib/project_paths.sh
source "${_SVC_SCRIPT_DIR}/lib/project_paths.sh"
# shellcheck source=lib/script_common.sh
source "${_SVC_SCRIPT_DIR}/lib/script_common.sh"
# shellcheck source=lib/pp_run_ops.sh
source "${_SVC_SCRIPT_DIR}/lib/pp_run_ops.sh"
unset _SVC_SCRIPT_DIR

# Declared flag vocabulary: ALL flags this command accepts or consumes.
# Script-specific flags are included so operator_args_validate_known rejects
# any flag not in this list (pre-flip safety: consumed ⊆ declared).
OPERATOR_VALID_FLAGS=(project show minor major no-minor no-major dry-run help)

# ---------------------------------------------------------------------------
# Usage / help
# ---------------------------------------------------------------------------
# Usage text as data (per lib/script_common.sh's documented convention).
# This script's usage intentionally prints to stderr on every path (--help's
# exit 0 included), so the destination is pinned here rather than delegated
# to print_usage_and_exit (which routes exit 0 to stdout).
# Note: OPERATOR_VALID_FLAGS includes all flags (canonical + script-specific)
# so that operator_args_validate_known can reject any unlisted flag.
# Script-specific flags have no built-in descriptions, so their lines are
# given in OPERATOR_FLAG_HELP; canonical flags use their built-in descriptions.
declare -A OPERATOR_FLAG_HELP=(
    [show]="  --show             Print the max_minor and max_major values in effect (after any change): a number, the default marked (default) when the key is absent, or none"
    [minor]="  --minor N          Set max_minor to the non-negative integer N"
    [major]="  --major N          Set max_major to the non-negative integer N"
    [no-minor]="  --no-minor         Write max_minor = none: no minor ceiling (wins over --minor; deleting the line instead restores the default 13)"
    [no-major]="  --no-major         Write max_major = none: no major ceiling (wins over --major; deleting the line instead restores the default 0)"
)
_USAGE_TEXT="$(operator_args_render_help_for_flags "set-version-ceiling.sh" \
    "Read, set, or lift (none) max_minor / max_major ceilings in a project's project.cfg." \
    OPERATOR_VALID_FLAGS)"

# ---------------------------------------------------------------------------
# Parse arguments
# Value-taking flags: project plus script-specific (minor, major).
# Boolean flags: show, no-minor, no-major, dry-run, help.
# ---------------------------------------------------------------------------
PROJECT=""
OPT_SHOW="false"
OPT_MINOR=""        # empty = no change; otherwise the value to set
OPT_MAJOR=""
OPT_NO_MINOR="false"
OPT_NO_MAJOR="false"
DRY_RUN="false"

operator_args_argparse --value-flags "project minor major" -- "$@"

# Emit clear errors for value-taking flags given with no value.
if argparse_missing "minor"; then
    echo "ERROR: --minor requires a value" >&2
    exit 1
fi
if argparse_missing "major"; then
    echo "ERROR: --major requires a value" >&2
    exit 1
fi

# Handle --help / -h.
if argparse_has "help" || argparse_has "h"; then
    printf '%s\n' "$_USAGE_TEXT" >&2
    exit 0
fi

# Reject unexpected positional arguments (hard-cut: no positional project name).
if [[ "${#ARGPARSE_POSITIONAL[@]}" -gt 0 ]]; then
    echo "ERROR: unexpected positional argument: ${ARGPARSE_POSITIONAL[0]}" >&2
    echo "Usage: set-version-ceiling.sh --project <name> --show|--minor <N>|--major <N>" >&2
    exit 1
fi

# Reject any flag not in the declared vocabulary.
operator_args_validate_known "$(basename "$0")" OPERATOR_VALID_FLAGS || exit 1

# Extract boolean flags.
if argparse_has "show";     then OPT_SHOW="true"; fi
if argparse_has "no-minor"; then OPT_NO_MINOR="true"; fi
if argparse_has "no-major"; then OPT_NO_MAJOR="true"; fi
if argparse_has "dry-run";  then DRY_RUN="true"; fi

# Extract value flags (--no-minor / --no-major win over --minor / --major;
# the Python function applies that rule).
PROJECT="$(operator_args_project)"
if argparse_has "minor"; then OPT_MINOR="${ARGPARSE_FLAGS[minor]}"; fi
if argparse_has "major"; then OPT_MAJOR="${ARGPARSE_FLAGS[major]}"; fi

# ---------------------------------------------------------------------------
# Validate required arguments
# ---------------------------------------------------------------------------
if [[ -z "$PROJECT" ]]; then
    echo "ERROR: project name is required (--project <name>)" >&2
    printf '%s\n' "$_USAGE_TEXT" >&2
    exit 1
fi

# Resolve by exact match then unambiguous prefix against projects.cfg.
PROJECT="$(pp_resolve_project_name "$PROJECT")" || exit 1

if [[ "$OPT_SHOW" == "false" && -z "$OPT_MINOR" && -z "$OPT_MAJOR" \
      && "$OPT_NO_MINOR" == "false" && "$OPT_NO_MAJOR" == "false" ]]; then
    echo "ERROR: at least one action is required (--show, --minor, --major, --no-minor, --no-major)" >&2
    printf '%s\n' "$_USAGE_TEXT" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Validate, show, preview or apply via the Python library
# ---------------------------------------------------------------------------
# Exit codes pass through: 1 for an invalid value or a write failure, 2 when
# the project has no project.cfg.
_rc=0
pp_run_ops pgai_agent_kanban.ops set_version_ceiling "$KANBAN_ROOT" "$PROJECT" \
    "$OPT_SHOW" "$OPT_MINOR" "$OPT_MAJOR" "$OPT_NO_MINOR" "$OPT_NO_MAJOR" "$DRY_RUN" || _rc=$?
exit "$_rc"
