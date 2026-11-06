#!/usr/bin/env bash
# team/scripts/deep-verify-status.sh
# Read-only operator verb: print a project's DEEP-VERIFY ledger entries and
# current status.
#
# Usage:
#   deep-verify-status.sh --project <name> [--help]
#
# Read-only twin of GET /deep-verify-status (team/pgai_agent_kanban/api/
# reads.py) -- both surfaces resolve through the exact same function,
# pgai_agent_kanban.ops.read.get_deep_verify_status, which itself delegates
# to pgai_agent_kanban.deep_verify.ledger's parse_records/latest_record
# ("the one reader every other consumer calls, never re-derived", per that
# module's own docstring) and to
# pgai_agent_kanban.deep_verify.schedule.resolve_mode for the project's
# configured cadence ([release] deep_verify, resolved from project.cfg, then
# kanban.cfg, then the default 'weekly') -- never a second independent parse
# of the ledger.
#
# This is the read-only status surface; running the lane itself is the
# separate "deep-verify" action verb (team/scripts/deep-verify.sh,
# POST /operations/deep-verify) -- this script never invokes the cold-build
# lane and never writes a ledger record.
#
# Output (stdout):
#   project: <name>
#   mode: weekly|off
#   latest: never-run
#     -- or --
#   latest: <verdict> (<timestamp>, <version>, <commit>)
#
#   Ledger (<N> entries):
#   ## Deep-Verify Run: <timestamp> — <project> — <version>
#   - Commit: <commit>
#   - Verdict: <verdict>
#   - Detail: <detail>
#   ... (one block per entry, oldest first -- same shape the ledger file
#   itself uses, rendered via the ledger module's own format_record rather
#   than a second hand-rolled block format)
#
# The rendering is pgai_agent_kanban.ops.read.render_deep_verify_status,
# reached through pp_run_ops as the get_deep_verify_status verb of
# pgai_agent_kanban.ops.
#
# Exit codes:
#   0   Status printed to stdout.
#   1   Usage error (undeclared flag, missing --project).
#   2   Project not found.
#   3   The ledger is malformed, or the [release] deep_verify value in
#       force (project.cfg, else kanban.cfg) is neither weekly/off nor a
#       recognized legacy boolean
#       alias -- the dual-channel error-contract block (error/message/
#       remedy) is printed to stderr verbatim rather than a guessed value
#       being printed to stdout.

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lib/env_bootstrap.sh"

_DVS_SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
_DVS_LIB_DIR="${_DVS_SCRIPT_DIR}/lib"

# shellcheck source=lib/operator_args.sh
source "${_DVS_LIB_DIR}/operator_args.sh"
# shellcheck source=lib/project_paths.sh
source "${_DVS_LIB_DIR}/project_paths.sh"
# shellcheck source=lib/pp_run_ops.sh
source "${_DVS_LIB_DIR}/pp_run_ops.sh"

OPERATOR_VALID_FLAGS=(project help)

operator_args_parse "$@"

if argparse_has help; then
    operator_args_render_help_for_flags "deep-verify-status.sh" \
        "Print a project's DEEP-VERIFY ledger entries and current status (read-only)." \
        OPERATOR_VALID_FLAGS \
        "" \
        "Prints the project's resolved cadence (mode: weekly|off, from" \
        "[release] deep_verify in project.cfg, else kanban.cfg, else the" \
        "default weekly), the most recent verdict, and every ledger" \
        "record -- resolved through the SAME functions GET /deep-verify-status" \
        "calls (never a second, independent parse of the ledger)." \
        "" \
        "Read-only: never runs the cold-build lane and never writes a ledger" \
        "record. To run a verification pass, use deep-verify.sh instead." \
        "" \
        "Exit codes:" \
        "  0  Status printed" \
        "  1  Usage error" \
        "  2  Project not found" \
        "  3  Malformed ledger, or an unrecognized deep_verify config value" \
        "     (dual-channel error/message/remedy block printed to stderr)"
    exit 0
fi

operator_args_validate_known "deep-verify-status.sh" OPERATOR_VALID_FLAGS || exit 1

_dvs_project="$(operator_args_project)"
if [[ -z "${_dvs_project}" ]]; then
    printf 'deep-verify-status.sh: --project is required\n' >&2
    exit 1
fi

_dvs_project="$(pp_resolve_project_name "${_dvs_project}")" || exit 1

# pgai_agent_kanban.ops.read.render_deep_verify_status prints the status
# (exit 0), or refuses: exit 2 for a project with no directory, exit 3 for a
# malformed ledger or an unrecognized deep_verify value.
_dvs_rc=0
pp_run_ops pgai_agent_kanban.ops get_deep_verify_status \
    "${PGAI_AGENT_KANBAN_ROOT_PATH}" "${_dvs_project}" || _dvs_rc=$?
exit "${_dvs_rc}"
