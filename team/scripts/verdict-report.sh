#!/usr/bin/env bash
# team/scripts/verdict-report.sh
#
# Machine-readable, multi-project counterpart to why.sh's verdict line.
# This script, why.sh and the REST endpoint (GET /verdicts in
# team/pgai_agent_kanban/api/reads.py) all resolve through one function,
# resolve_verdicts in team/pgai_agent_kanban/ops/verdict.py -- this script
# reaches it through pp_run_ops, the endpoint calls it in process -- so the
# CLI and the API can never disagree about what a lane's verdict is. This
# script prints the resolved fields as one key=value block per project
# instead of why.sh's human sentence.
#
# --details extends the same guarantee to why.sh --details' diagnostic pane
# (release locks, queue tails, the resolved posture and pending bundles):
# the blocks below and why.sh's text sections are rendered from the same
# resolved details, so a terminal running `why.sh --project NAME --details`
# and a REST caller requesting `GET /verdicts?project=NAME&details=true` can
# never disagree about a lock's provenance, a queue's tail, or the
# project's posture.
#
# Usage:
#   verdict-report.sh                  Print the block of the project
#                                       $PGAI_PROJECT_NAME names when it is
#                                       set. Only when neither --project nor
#                                       $PGAI_PROJECT_NAME is given, walk
#                                       every registered project
#                                       (projects.cfg priority order, the
#                                       same walk why.sh makes in that case)
#                                       and print one block per project,
#                                       separated by a blank line.
#   verdict-report.sh --project NAME   Print NAME's block only (--project
#                                       wins over $PGAI_PROJECT_NAME) --
#                                       including when NAME is registered in
#                                       projects.cfg but has no directory on
#                                       disk (an "unresolved" block; see
#                                       below). NAME is not required to be
#                                       registered in projects.cfg at all;
#                                       the caller (the REST endpoint) is
#                                       expected to have already validated
#                                       registration when that distinction
#                                       matters to it.
#   verdict-report.sh --project NAME --details
#                                       Print NAME's verdict block, then
#                                       (when the project directory exists)
#                                       one additional block per release
#                                       lock, one additional block per queue
#                                       entry (or an empty-state block per
#                                       queue with none), one posture block,
#                                       and one additional block per pending
#                                       requirements bundle (or an
#                                       empty-state block when none) -- see
#                                       "Output shape" below. Needs a
#                                       project: --project, or
#                                       $PGAI_PROJECT_NAME when --project
#                                       is omitted (a posture/lock/queue
#                                       report is always per-project, same
#                                       as why.sh --details). When NAME's
#                                       directory does not exist on disk,
#                                       only the "unresolved" verdict block
#                                       prints -- there is nothing to detail
#                                       for a project with no directory.
#   verdict-report.sh --help, -h       Show help and exit.
#
# Output shape
# ------------
# stdout is one or more "blocks", separated by a single blank line. Each
# block is a sequence of "key=value" lines (only the first "=" on a line is
# the delimiter; a value may itself contain "="). Every block carries a
# distinguishing shape (or, under --details, a "kind=" field naming its
# section); a reader groups blocks by that field.
#
#   Resolved verdict block (the project directory exists and a verdict was
#   reached -- the fields of ops/verdict.py's ProjectVerdict; no "kind"
#   field -- this is the one block shape --details does not tag):
#     project=<name>
#     verdict=<RUNNING|BLOCKED|HALTED|STUCK-LOCK|IDLE>
#     exit_code=<int>
#     cause=<sentence fragment>
#     remedy=<sentence fragment>
#     line=<the full sentence why.sh would print for this project>
#     task_id=<value>              -- present only for RUNNING and BLOCKED
#     task_state=<WORKING|BLOCKED> -- present only for RUNNING and BLOCKED
#     minutes_in_state=<int>       -- present only for RUNNING
#
#   Unresolved verdict block (the project has no directory on disk --
#   distinct from any of the five verdict words; never silently reported as
#   a false IDLE -- an empty tasks/ scan and an absent HALT file would read
#   exactly like a healthy, work-free IDLE project). --details never
#   appends further blocks after this one:
#     project=<name>
#     error=project directory not found
#     cause=<sentence fragment>
#     remedy=<sentence fragment>
#
#   --details-only block shapes (each carries kind=<name> plus project=<name>):
#
#     kind=release_lock  -- one per lock held for the project (the lock
#       readers of team/pgai_agent_kanban/ops/locks.py -- the same ones
#       why.sh --details' "== locks ==" section is rendered from):
#         kind=release_lock
#         project=<name>
#         path=<lock file path>
#         age_seconds=<int>        -- seconds since the lock file was last
#                                     refreshed (now minus its mtime). The
#                                     held-lock refresh keeps moving the
#                                     mtime, so this is a heartbeat age,
#                                     NOT how long the lock has been held.
#         held_seconds=<int|none>  -- how long the lock has been held: now
#                                     minus the record's acquired_at, read
#                                     through ops.locks.held_seconds (the
#                                     same value why.sh --details renders
#                                     as "held: <duration>"). The literal
#                                     "none" when the record carries no
#                                     parseable acquired_at (a pre-upgrade
#                                     lock) -- never an mtime number.
#         provenance=<description, or "(no provenance recorded)">
#         stale_threshold_seconds=<int>
#         stale_threshold_derivation=<sentence fragment>
#         violation=<true|false>   -- true exactly when provenance is
#                                     "(no provenance recorded)" (the same
#                                     VIOLATION condition why.sh --details'
#                                     locks section flags)
#       A project holding no locks emits exactly one such block instead,
#       carrying no lock fields at all:
#         kind=release_lock
#         project=<name>
#         none=true
#
#     kind=queue_entry  -- one per queue-tail entry (source: the last five
#       "- [marker] task_id" lines of each tasks/queues/*_backlog.md file,
#       cross-referenced against that task's own status.md ## State --
#       the same walk why.sh --details' queue-tails section shows):
#         kind=queue_entry
#         project=<name>
#         agent=<queue name, e.g. "coder">
#         marker=<raw marker character, exactly as written>
#         task_id=<value>
#         status_state=<the task's real status.md ## State, "(blank)", or
#                        "(no status.md)">
#
#     kind=queue_tail  -- an empty-state block. Emitted per queue file with
#       zero matching entries (carries "agent"), or exactly once with no
#       "agent" field when the project has no tasks/queues/ directory or no
#       *_backlog.md files in it at all:
#         kind=queue_tail
#         project=<name>
#         agent=<value>   -- omitted for the "no queues at all" case
#         none=true
#
#     kind=posture  -- exactly one block, the resolved posture (source:
#       dashboard/halt_state.py's compute_halt_state,
#       lib/defer_posture.py's resolve_defer_posture, and release-state.md's
#       Active RC read through cm/read_state_field.py, shown only when it
#       is vX.Y.Z -- the same values why.sh --details' posture section
#       prints):
#         kind=posture
#         project=<name>
#         halt_state=<normal|halted|draining>
#         halt_event=<value>          -- omitted when halt_state is normal,
#                                        or when draining/halted with no
#                                        event recorded
#         deferred_bugs=<true|false>
#         deferred_bugs_tier=<tier the value resolved from>
#         deferred_bug_level=<value>
#         deferred_bug_level_tier=<tier>
#         defer_piggyback=<true|false>
#         defer_piggyback_tier=<tier>
#         until=<vX.Y.Z, or "(none)">
#         until_tier=<tier>
#         campaign_file=<value>       -- present only when until_tier is
#                                        "campaign file"
#         active_rc=<none|vX.Y.Z>
#
#     kind=pending_bundle  -- one per pending (## Status != done)
#       *-bugfix-bundle-*.md or *-priority-bundle-*.md file under the
#       project's requirements/ directory, its ## Status read through
#       lib/md_fields.py (the same list why.sh --details' posture section
#       prints):
#         kind=pending_bundle
#         project=<name>
#         name=<basename of the requirements file>
#         status=<its ## Status value, or "(no Status field)">
#       A project with no pending bundles emits exactly one such block
#       instead:
#         kind=pending_bundle
#         project=<name>
#         none=true
#
# Field omission: a field not listed above for a given block shape is never
# printed with an empty value -- it is omitted entirely.
#
# Exit codes:
#   0   Every project in the walk (or the single named project) produced a
#       block, whatever its shape. An unresolved project is data, not a
#       script failure, so it does not change the exit code.
#   1   Pre-flight refusal: no project named (neither --project nor
#       $PGAI_PROJECT_NAME) and projects_cfg_list itself
#       could not enumerate any registered project (mirrors why.sh's own
#       "no registered projects" refusal), or --details given with no
#       project named (neither --project nor $PGAI_PROJECT_NAME; mirrors
#       why.sh's own "--details has no project named" refusal). Also
#       under --details when the deferral posture refuses to resolve (a
#       malformed campaign file or an invalid [release] value): the blocks
#       before the posture block are printed, then the resolver's
#       dual-channel refusal on stderr.
#
# Read-only and side-effect-free, exactly like why.sh: creates no file,
# edits no file, removes no file. Running it twice against the same tree
# state produces byte-identical output.

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# --- Locate the lib directory relative to this script ---
_VR_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_VR_LIB_DIR="${_VR_SCRIPT_DIR}/lib"

# --- Source the standard argument layer ---
# shellcheck source=lib/operator_args.sh
source "${_VR_LIB_DIR}/operator_args.sh"
# shellcheck source=lib/project_paths.sh
source "${_VR_LIB_DIR}/project_paths.sh"
# shellcheck source=lib/projects.sh
source "${_VR_LIB_DIR}/projects.sh"
# shellcheck source=lib/error_contract.sh
source "${_VR_LIB_DIR}/error_contract.sh"
# shellcheck source=lib/pp_run_ops.sh
source "${_VR_LIB_DIR}/pp_run_ops.sh"

# --- Declare accepted flags for this script ---
OPERATOR_VALID_FLAGS=(project details help)

# --details is outside the canonical vocabulary, so its help line is given here.
# The shared --project line names only the $PGAI_PROJECT_NAME default; this
# script also walks every registered project when neither is set, so its line
# says both.
declare -A OPERATOR_FLAG_HELP=(
    [project]="  --project NAME     Project to report (default: \$PGAI_PROJECT_NAME; with neither set, every registered project)"
    [details]="  --details          Also print lock, queue-tail, posture and requirements-bundle blocks (needs --project or \$PGAI_PROJECT_NAME)"
)

# --- Parse arguments ---
operator_args_parse "$@"

# --- Handle --help ---
if argparse_has help; then
    operator_args_render_help_for_flags "verdict-report.sh" \
        "Print the shared verdict engine's resolved fields for one project (--project, else \$PGAI_PROJECT_NAME) or, when neither is set, every registered project, as machine-readable key=value blocks -- the REST endpoint's source of truth, never why.sh's rendered sentence." \
        OPERATOR_VALID_FLAGS \
        "" \
        "With no --project: print the block of the project \$PGAI_PROJECT_NAME" \
        "names when it is set. Only when neither --project nor" \
        "\$PGAI_PROJECT_NAME is given, walk every registered project in" \
        "projects.cfg priority order and print one block each, blank-line" \
        "separated." \
        "" \
        "--project NAME: print NAME's block only -- including an" \
        "'unresolved' block (error=project directory not found) when NAME" \
        "has no directory on disk." \
        "" \
        "--project NAME --details: print NAME's verdict block, then one" \
        "block per release lock, queue-tail entry, and pending requirements" \
        "bundle, plus one resolved-posture block -- the machine-readable" \
        "twin of why.sh --project NAME --details' diagnostic pane. With no" \
        "--project, --details reports the project \$PGAI_PROJECT_NAME names;" \
        "with neither set it refuses."
    exit 0
fi

# --- Reject any flag not in the declared vocabulary ---
operator_args_validate_known "verdict-report.sh" OPERATOR_VALID_FLAGS || exit 1

# --- Resolve kanban root ---
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
export KANBAN_ROOT

# --- Resolve project: --project, else $PGAI_PROJECT_NAME (optional: empty
#     means neither is set, so walk every registered project) ---
_vr_project="$(operator_args_project)"

# --- --details has no project named: refuse the same way why.sh does -- a
#     posture/lock/queue report is always per-project. ---
if argparse_has details && [[ -z "${_vr_project}" ]]; then
    # BLOCK-WORTHY (docs/blocking-site-census.md): an unidentifiable target --
    # --details reports a per-project resolved posture; with no project
    # named there is nothing for it to resolve against.
    pgai_emit_error_contract \
        "verdict_report.details_requires_project" \
        "verdict-report.sh: --details has no project named -- the diagnostic pane is always per-project." \
        "Set \$PGAI_PROJECT_NAME to a project registered in projects.cfg, or pass --project directly, e.g. team/scripts/verdict-report.sh --project pgai-agent-kanban --details."
    # CENSUS-ANCHOR: verdict-report-details-requires-project-01
    exit 1
fi

# --- The projects to report on: the one named (registered or not), or
#     every registered project in projects.cfg priority order. ---
_vr_projects=()
if [[ -n "${_vr_project}" ]]; then
    _vr_projects=("${_vr_project}")
else
    _vr_registered="$(projects_cfg_list)" || exit 1
    while IFS= read -r _vr_walk_project; do
        [[ -n "${_vr_walk_project}" ]] && _vr_projects+=("${_vr_walk_project}")
    done <<< "${_vr_registered}"
fi

_vr_flags=()
if argparse_has details; then
    _vr_flags+=(--details)
fi

# --- resolve_verdicts prints the blocks; it exits 0, or 1 on a refusal. ---
_vr_rc=0
pp_run_ops pgai_agent_kanban.ops resolve_verdicts \
    --kanban-root "${KANBAN_ROOT}" --render report "${_vr_flags[@]}" -- "${_vr_projects[@]}" || _vr_rc=$?
exit "${_vr_rc}"
