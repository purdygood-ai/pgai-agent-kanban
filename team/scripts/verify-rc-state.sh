#!/usr/bin/env bash
# team/scripts/verify-rc-state.sh
# Read-only consistency checker for kanban project RC state.
#
# Usage:
#   verify-rc-state.sh PROJECT [--verbose]
#
# Arguments:
#   PROJECT      project name; resolved by exact match then unambiguous
#                prefix against projects.cfg (see pp_resolve_project_name)
#   --verbose    print every check (OK, WARN, ERROR); by default only WARN/ERROR
#                and the final summary line are printed
#
# Checks performed:
#   a. release-state.md consistency:
#      - Active RC is `none` OR matching rc/<version> branch exists in dev tree
#      - Last Released matches most-recent git tag
#      - All timestamps are valid ISO-8601
#   b. Queue <-> folder consistency:
#      - Every non-[x] task ID in *_backlog.md has a matching task folder on disk
#      - Every task folder has a matching queue entry
#   c. status.md state <-> queue marker consistency:
#      - [ ] = BACKLOG, [W] = WAITING, [A] = WORKING, [B] = BLOCKED,
#        [x] = DONE or WONT-DO
#   d. Cross-task Prerequisites references resolve:
#      - Every ID in a task README's ## Prerequisites section resolves to a folder
#   e. release-state.md <-> git branch consistency:
#      - Active RC: vX.Y.Z  → rc/vX.Y.Z must exist locally
#      - Active RC: none     → no rc/* branches should exist (WARN, not ERROR)
#      - Branch names are resolved through pp_prefix_branch, so a project
#        configured with [project] branch_prefix looks for <prefix>rc/vX.Y.Z
#        instead of rc/vX.Y.Z; unprefixed projects are unaffected
#   f. Bundle invariants:
#      - Every PRIORITY/BUG file referenced in a requirements bundle exists
#      - Every PRIORITY/BUG marked [x] in its backlog corresponds to a bundled
#        requirements file
#
# Output format:
#   [OK]    <message>
#   [WARN]  <message>
#   [ERROR] <message>
#   verify-rc-state: N errors, N warnings, N ok
#
# Exit codes:
#   0   no ERRORs found (WARN findings do not affect exit code)
#   1   one or more ERROR findings
#   2   pre-flight failure (bad arguments, project not found, etc.)
#
# Configuration:
#   KANBAN_ROOT           — path to the kanban live install root
#                           (default: $HOME/pgai_agent_kanban)
#   PGAI_DEV_TREE_PATH    — path to the dev tree git clone, used when the
#                           project config names none (kanban.cfg [paths]
#                           dev_tree_path after that)
#
# Implementation: this script parses its arguments and reaches
# pgai_agent_kanban.ops.rc_state.verify_rc_state through
# `pp_run_ops pgai_agent_kanban.ops verify_rc_state`; GET /verify-rc-state
# calls the same function in process. The project resolution, the
# pre-flight refusals and every check live there.

# ---------------------------------------------------------------------------
# Argument parsing (before strict mode for cleaner error output)
# ---------------------------------------------------------------------------
PROJECT_ARG=""
VERBOSE=0

# shellcheck source=lib/script_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/script_common.sh"
# Brings this script into the same declared-flag-vocabulary / uniform-
# unknown-flag-refusal contract every other operator verb uses
# (team/scripts/show.sh is the model read verb) -- operator_args_parse,
# argparse_has, operator_args_validate_known, and
# operator_args_render_help_for_flags.
# shellcheck source=lib/operator_args.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/operator_args.sh"
# Sourced early (before strict mode, before env_bootstrap) so the
# argument-shape refusals below -- which fire during argument parsing,
# ahead of project_paths.sh's own later, guarded source of this same file
# -- can already speak the dual-channel contract.
# shellcheck source=lib/error_contract.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/error_contract.sh"

# ---------------------------------------------------------------------------
# Declared flag vocabulary for this script.
# verify-rc-state accepts: --verbose --help, plus the positional PROJECT.
# The positional argument is NOT part of OPERATOR_VALID_FLAGS -- argparse.sh
# records it in ARGPARSE_POSITIONAL, not ARGPARSE_FLAGS, because this
# script's argument shape is "PROJECT [--verbose]", not a --project flag
# like most other operator verbs.
# ---------------------------------------------------------------------------
OPERATOR_VALID_FLAGS=(help verbose)
declare -A OPERATOR_FLAG_HELP=(
    [verbose]="  --verbose          Print every check (OK, WARN, ERROR); by default only WARN/ERROR and the summary line print"
)

# Usage text as data (per lib/script_common.sh's print_usage_and_exit
# contract), built from the caller's declared flag array so --help and
# unknown-flag rejection can never drift from each other.
_USAGE_TEXT="$(operator_args_render_help_for_flags \
    "$(basename "$0")" \
    "Read-only consistency checker for kanban project RC state." \
    OPERATOR_VALID_FLAGS \
    "" \
    "Usage: $(basename "$0") PROJECT [--verbose]" \
    "" \
    "Arguments:" \
    "  PROJECT      Project name; resolved by exact match then unambiguous" \
    "               prefix against projects.cfg." \
    "" \
    "Checks performed:" \
    "  a. release-state.md consistency (Active RC format, timestamps, Last Released)." \
    "  b. Queue <-> folder consistency (open queue entries have matching task folders)." \
    "  c. status.md state <-> queue marker consistency." \
    "  d. Cross-task Prerequisites references resolve to existing task folders." \
    "  e. release-state.md <-> git branch consistency." \
    "  f. Bundle invariants (PRIORITY/BUG files referenced in requirements bundles exist)." \
    "" \
    "Output format:" \
    "  [OK]    <message>" \
    "  [WARN]  <message>" \
    "  [ERROR] <message>" \
    "  verify-rc-state: N errors, N warnings, N ok" \
    "" \
    "Exit codes:" \
    "  0   No ERRORs found (WARN findings do not affect exit code)." \
    "  1   One or more ERROR findings." \
    "  2   Pre-flight failure (bad arguments, project not found, etc.)." \
    "" \
    "Configuration:" \
    "  KANBAN_ROOT           Path to the kanban live install root" \
    "                        (default: \$HOME/pgai_agent_kanban)." \
    "  PGAI_DEV_TREE_PATH    Path to the dev tree git clone" \
    "                        (derived from project config when not set).")"

operator_args_parse "$@"

# --help always wins, before unknown-flag rejection (matching show.sh /
# why.sh: --help succeeds even alongside other flags).
if argparse_has help; then
    print_usage_and_exit 0 "$_USAGE_TEXT"
fi

# Reject any flag not in the declared vocabulary, via the same uniform
# "<script>: unknown argument: --FLAG" message every operator verb emits.
# Exit 2, not the generic 1 -- this script's own exit-code contract above
# already reserves 2 for "bad arguments", and an unknown flag is exactly
# that; changing it to 1 here would collide with the "ERROR findings"
# meaning 1 carries in every other exit path of this script.
if ! operator_args_validate_known "$(basename "$0")" OPERATOR_VALID_FLAGS; then
    echo "" >&2
    print_usage_and_exit 2 "$_USAGE_TEXT"
fi

argparse_has verbose && VERBOSE=1

# Both argument-shape refusals below speak the dual-channel contract AND
# still print the full usage block afterward: the dual-channel triple
# gives a tool (or a stranger scanning stderr) the stable key and a
# runnable one-liner, and the usage block that follows is the same
# existing --help path everyone already knows to scroll up to -- neither
# channel replaces the other, so both print, in that order, ahead of the
# single exit.
case "${#ARGPARSE_POSITIONAL[@]}" in
    0)
        # BLOCK-WORTHY (docs/blocking-site-census.md): with no project
        # named, there is nothing on disk this checker could report
        # against -- refusing here is the same "never render a plausible
        # result for an unresolved target" contract the resolver refusal
        # below applies.
        pgai_emit_error_contract \
            "verify_rc_state.project_required" \
            "verify-rc-state.sh: a project name is required." \
            "Run team/scripts/verify-rc-state.sh PROJECT, e.g. team/scripts/verify-rc-state.sh pgai-agent-kanban."
        # CENSUS-ANCHOR: verify-rc-state-project-required-01
        echo "" >&2
        print_usage_and_exit 2 "$_USAGE_TEXT"
        ;;
    1)
        PROJECT_ARG="${ARGPARSE_POSITIONAL[0]}"
        ;;
    *)
        # BLOCK-WORTHY (docs/blocking-site-census.md): this verb checks
        # exactly one project's state -- a second positional argument is
        # ambiguous input, and guessing which one the operator meant risks
        # reporting on the wrong project.
        pgai_emit_error_contract \
            "verify_rc_state.unexpected_positional_argument" \
            "verify-rc-state.sh: unexpected positional argument: ${ARGPARSE_POSITIONAL[1]}." \
            "Run team/scripts/verify-rc-state.sh PROJECT with exactly one project name."
        # CENSUS-ANCHOR: verify-rc-state-unexpected-positional-01
        echo "" >&2
        print_usage_and_exit 2 "$_USAGE_TEXT"
        ;;
esac

# ---------------------------------------------------------------------------
# Bootstrap: self-locate → source shell-env → fail loud
# ---------------------------------------------------------------------------
# Must happen before the first use of PGAI_AGENT_KANBAN_ROOT_PATH so the
# script runs from a fresh shell without manual pre-sourcing.  Explicit
# operator exports win via env_bootstrap.sh's idempotency guard.
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh" || exit 1

# ---------------------------------------------------------------------------
# Resolve script / library paths (BEFORE strict mode)
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Resolve KANBAN_ROOT
# PGAI_AGENT_KANBAN_ROOT_PATH is now set by env_bootstrap.sh or the operator.
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"

# Source optional env / config files (they may use unset vars — pre-strict)
[[ -f "$KANBAN_ROOT/bashrc" ]]               && source "$KANBAN_ROOT/bashrc"
[[ -f "$KANBAN_ROOT/env" ]]                  && source "$KANBAN_ROOT/env"
[[ -f "$HOME/.config/pgai-kanban.cfg" ]]     && source "$HOME/.config/pgai-kanban.cfg"

# shellcheck source=lib/pp_run_ops.sh
source "${SCRIPT_DIR}/lib/pp_run_ops.sh"

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve the project, run checks a-f and print the report: exit 0 with no
# ERROR finding, 1 with any, 2 when the project cannot be resolved or has no
# directory, config or dev tree.
# ---------------------------------------------------------------------------
_verify_rc=0
pp_run_ops pgai_agent_kanban.ops verify_rc_state "$KANBAN_ROOT" "$PROJECT_ARG" "$VERBOSE" || _verify_rc=$?
exit "$_verify_rc"
