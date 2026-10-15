#!/usr/bin/env bash
# team/scripts/why.sh
# CLI verb: answers "why?" for one project's lane, or for every registered
# project at once, in exactly one verdict per project.
#
# Usage:
#   why.sh                     Print the verdict line of the project
#                               $PGAI_PROJECT_NAME names when it is set. Only
#                               when neither --project nor $PGAI_PROJECT_NAME
#                               is given, walk every registered project
#                               (projects.cfg priority order) and print one
#                               verdict line each.
#   why.sh --project NAME      Print NAME's verdict line only (--project wins
#                               over $PGAI_PROJECT_NAME).
#   why.sh --project NAME --details
#                               Print NAME's verdict line, unchanged, followed
#                               by the full diagnostic pane: every lock held
#                               for this project (held duration/provenance), the
#                               last five entries of every task queue with
#                               their raw markers (an entry whose task or bug
#                               is no longer live but is in the project's
#                               archive shows its archived state, marked
#                               "(archived)"), the show-queues --details
#                               pane embedded verbatim, the whole posture in
#                               force (HALT/HALT-AFTER, the resolved deferral
#                               posture and its tiers, the Active RC, and
#                               every pending requirements bundle with its
#                               ## Status), and the project's most recent
#                               discovery/gate refusal. --details is
#                               additive: everything --project alone prints
#                               still prints, with the detail pane below it.
#   why.sh --help, -h          Show help and exit.
#
# What changed from the original deferral-posture-only why.sh
# --------------------------------------------------------------
# This verb used to answer one narrow question only ("what deferral posture
# is in force for this project?"), required --project, and spoke in
# resolver vocabulary (field=value (tier)) rather than a verdict. It now
# answers the operator's actual question -- "why did this lane stop moving?"
# -- for one project or every project at once, in a sentence that carries
# the verdict, its cause, and a runnable remedy together. The original
# deferral-posture report did not go away: its four lines are still printed
# byte-identical, now folded into --details' "== posture ==" section
# alongside HALT/HALT-AFTER, the Active RC, and pending bundles (see
# team/tests/unit/test_defer_operator_verbs.py and
# team/tests/integration/test_defer_campaign_lifecycle.py, which call
# --details explicitly and assert on those same four lines). --details is
# additive: the verdict line prints first, unchanged, followed by locks,
# queue tails, the embedded show-queues --details pane, posture, the
# project's most recent discovery/gate refusal and the last docker use.
#
# The verdict decision itself is NOT computed here. It lives in exactly one
# place -- resolve_verdicts in team/pgai_agent_kanban/ops/verdict.py, which
# this script reaches through pp_run_ops and GET /verdicts calls in process
# -- so the CLI and the REST endpoint can never disagree about what a lane's
# verdict is. Read that module's docstring for the five-word vocabulary, the
# fixed precedence order, and the stall-detection rule. It also renders the
# verdict line and the locks, queue tails, show-queues and posture sections
# of --details; the discovery-skip and docker-use sections come from
# team/scripts/lib/why_details.sh. This script parses flags, refuses what
# has no target, and exits with the verdict's code.
#
# Exit codes (the full, stable contract; ops/verdict.py's EXIT_* constants
# are the canonical definitions this table mirrors)
# --------------------------------------------------------------------------
#   0   RUNNING            healthy: the active task is WORKING within the
#                           stall threshold.
#   1   pre-flight refusal no verdict could be determined at all -- no
#                           project named and none walkable (see
#                           why.project_not_found below), or a --details
#                           section refusal (an unparseable campaign file,
#                           an invalid [release] value, or a malformed
#                           refusal ledger). Distinct from every verdict
#                           code below: a refusal means no verdict was
#                           reached, not that a bad one was.
#   2   IDLE                nothing is WORKING or BLOCKED. Covers a project
#                           disabled via projects.cfg (enabled=false), a
#                           project with only queued (BACKLOG/WAITING) work,
#                           and a project with no non-terminal tasks at all
#                           -- the rendered line's text names which.
#   3   RUNNING (stalled)   the active task has been WORKING past
#                           STALL_THRESHOLD_MINUTES (ops/verdict.py) --
#                           the one case where the verdict WORD stays
#                           RUNNING but the exit code is not the healthy 0.
#   4   BLOCKED             no task is WORKING; a task is BLOCKED.
#   5   HALTED              a project-scoped HALT file is present.
#   6   STUCK-LOCK          no task is WORKING or BLOCKED, but work is
#                           queued (BACKLOG/WAITING) and this project's own
#                           lock is stale (past the reaper's own age
#                           threshold) or carries no provenance record at
#                           all -- the queued work has nowhere to go, and a
#                           bare IDLE would misreport that as "nothing to
#                           do." The rendered line carries the
#                           team/scripts/release-locks.sh remedy inline.
#
# Aggregation rule (all-projects mode: neither --project nor
# $PGAI_PROJECT_NAME names a project): the process exit
# code is the MAXIMUM numeric exit code produced across every project
# walked -- the codes above are deliberately ordered by ascending severity
# (0 best, 6 worst) so a plain numeric max IS the worst-verdict rule. Code 1
# is never folded into this max: projects_cfg_list only returns registered
# project names, so a mid-walk pre-flight failure for one of them is a
# tree-consistency problem, not a bad verdict, and the whole invocation
# refuses at exit 1 rather than being averaged into the per-project result.
#
# Read-only and side-effect-free: this script creates no file, removes no
# file, and mutates no cfg or task state. Running it twice leaves every
# file under every project root byte-identical.
#
# A malformed campaign file or refusal ledger (under --details) is never
# printed over. Every detail section is computed before anything is
# printed; a resolver refusal (dual-channel, naming the file by path) means
# nothing from --details prints at all, not a half-rendered pane.
#
# Scope: this verb reports a lane's verdict, and under --details its full
# diagnostic pane. It is not a general-purpose task-status browser --
# team/scripts/kanban-status.sh and the dashboard cover that ground.

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# --- Locate the lib directory relative to this script ---
_WHY_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_WHY_LIB_DIR="${_WHY_SCRIPT_DIR}/lib"

# --- Source the standard argument layer ---
# shellcheck source=lib/operator_args.sh
source "${_WHY_LIB_DIR}/operator_args.sh"
# shellcheck source=lib/project_paths.sh
source "${_WHY_LIB_DIR}/project_paths.sh"
# shellcheck source=lib/projects.sh
source "${_WHY_LIB_DIR}/projects.sh"
# shellcheck source=lib/error_contract.sh
source "${_WHY_LIB_DIR}/error_contract.sh"
# shellcheck source=lib/pp_run_ops.sh
source "${_WHY_LIB_DIR}/pp_run_ops.sh"
# shellcheck source=lib/why_details.sh
source "${_WHY_LIB_DIR}/why_details.sh"

# --- Declare accepted flags for this script ---
OPERATOR_VALID_FLAGS=(project details help)

# --details is outside the canonical vocabulary, so its help line is given here.
# The shared --project line names only the $PGAI_PROJECT_NAME default; this
# verb also walks every registered project when neither is set, so its line
# says both.
declare -A OPERATOR_FLAG_HELP=(
    [project]="  --project NAME     Project to report (default: \$PGAI_PROJECT_NAME; with neither set, every registered project)"
    [details]="  --details          Also print the full diagnostic pane: locks, queue tails, posture and refusals (needs --project or \$PGAI_PROJECT_NAME)"
)

# --- Parse arguments ---
operator_args_parse "$@"

# --- Handle --help ---
if argparse_has help; then
    operator_args_render_help_for_flags "why.sh" \
        "Report why a project's lane stopped moving (every registered project's when neither --project nor \$PGAI_PROJECT_NAME names one), or its deferral posture with --details." \
        OPERATOR_VALID_FLAGS \
        "" \
        "With no --project: report the project \$PGAI_PROJECT_NAME names when" \
        "it is set. Only when neither --project nor \$PGAI_PROJECT_NAME is" \
        "given, walk every registered project in projects.cfg priority order" \
        "and print one verdict line each (RUNNING / BLOCKED / HALTED /" \
        "STUCK-LOCK / IDLE), exiting with the worst verdict's code." \
        "" \
        "--details: needs a project, named by --project or, when --project" \
        "is omitted, by \$PGAI_PROJECT_NAME; with neither set it refuses." \
        "Prints that project's verdict line, then the full diagnostic pane" \
        "below it: every lock held for this project" \
        "(held duration/provenance), the last five entries of every task queue" \
        "with their raw markers, the show-queues --details pane embedded" \
        "verbatim, the whole posture in force (HALT/HALT-AFTER, the resolved" \
        "deferral posture and its tiers, the Active RC, and every pending" \
        "requirements bundle with its ## Status), the project's most" \
        "recent discovery/gate refusal, and the install's last docker use" \
        "(the last line of logs/docker-use.log)."
    exit 0
fi

# --- Reject any flag not in the declared vocabulary ---
operator_args_validate_known "why.sh" OPERATOR_VALID_FLAGS || exit 1

# --- Resolve kanban root ---
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
export KANBAN_ROOT

# --- Resolve project: --project, else $PGAI_PROJECT_NAME (optional: empty
#     means neither is set, so walk every registered project) ---
_project="$(operator_args_project)"

# --- Print one project's full diagnostic pane: the verdict line, unchanged,
#     then the detail sections. resolve_verdicts renders the verdict line and
#     the locks, queue tails, show-queues --details and posture sections
#     and exits with the verdict's code (1 when a section refused);
#     why_details.sh renders the discovery-skip and docker-use sections.
#
#     Every section is computed FIRST; nothing is printed until every
#     section has resolved cleanly. A malformed campaign file or a malformed
#     refusal ledger refuses loud (exit 1) with NOTHING printed to stdout. ---
_why_print_details() {
    local project="$1"
    local _pane _rc=0 _discovery_section _docker_use_section
    _pane="$(pp_run_ops pgai_agent_kanban.ops resolve_verdicts \
        --kanban-root "${KANBAN_ROOT}" --render why --details -- "${project}")" || _rc=$?
    [[ "${_rc}" -eq 1 ]] && exit 1
    _discovery_section="$(why_details_render_discovery_skip "${project}")" || exit 1
    _docker_use_section="$(why_details_render_docker_use)" || exit 1

    printf '%s\n' "${_pane}"
    printf '\n== discovery skip reason ==\n%s\n' "${_discovery_section}"
    printf '\n== docker use ==\n%s\n' "${_docker_use_section}"
    exit "${_rc}"
}

# --- Print one verdict line per named project and exit with the worst
#     verdict's code (resolve_verdicts prints the lines and picks it). ---
_why_print_verdicts() {
    local _rc=0
    pp_run_ops pgai_agent_kanban.ops resolve_verdicts \
        --kanban-root "${KANBAN_ROOT}" --render why -- "$@" || _rc=$?
    exit "${_rc}"
}

if [[ -n "${_project}" ]]; then
    # --- Resolve project name by exact match then unambiguous prefix ---
    _project="$(pp_resolve_project_name "${_project}")" || exit 1

    # --- Resolve project root; refuse an unregistered project rather than
    #     silently reporting on a project directory that does not exist.
    #     This is now effectively unreachable when the prefix resolution
    #     above succeeds (pp_resolve_project_name already guarantees the
    #     resolved name matches a projects.cfg entry) but is left in place
    #     as a belt-and-suspenders check. ---
    _project_root="$(pp_project_root "${_project}")" || exit 1

    if [[ ! -d "${_project_root}" ]]; then
        # BLOCK-WORTHY (docs/blocking-site-census.md): an unidentifiable target --
        # printing any report for a project directory that does not exist would
        # answer "why" with a confident-looking report about a project that was
        # never resolved, the exact overclaim this verb exists to avoid.
        pgai_emit_error_contract \
            "why.project_not_found" \
            "why.sh: project '${_project}' has no directory at ${_project_root}." \
            "Check the project name against projects.cfg."
        # CENSUS-ANCHOR: why-project-not-found-01
        exit 1
    fi

    if argparse_has details; then
        # Exits with the verdict's own code; a section refusal exits 1.
        _why_print_details "${_project}"
    fi

    _why_print_verdicts "${_project}"
fi

# --- No project named (neither --project nor $PGAI_PROJECT_NAME): --details
#     has nothing to attach to (a posture is always per-project -- its cfg
#     tiers and campaign file both live under a named project's
#     directory). ---
if argparse_has details; then
    # BLOCK-WORTHY (docs/blocking-site-census.md): an unidentifiable target --
    # --details reports a per-project resolved posture; with no project
    # named there is nothing for it to resolve against.
    pgai_emit_error_contract \
        "why.details_requires_project" \
        "why.sh: --details has no project named -- the deferral posture is always per-project." \
        "Set \$PGAI_PROJECT_NAME to a project registered in projects.cfg, or pass --project directly, e.g. team/scripts/why.sh --project pgai-agent-kanban --details."
    # CENSUS-ANCHOR: why-details-requires-project-01
    exit 1
fi

# --- Walk every registered project in priority order, print one verdict
#     line each, and exit with the worst verdict code across all of them ---
_why_projects="$(projects_cfg_list)" || exit 1

_why_names=()
while IFS= read -r _why_project; do
    [[ -n "${_why_project}" ]] && _why_names+=("${_why_project}")
done <<< "${_why_projects}"

_why_print_verdicts "${_why_names[@]}"
