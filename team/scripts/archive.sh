#!/usr/bin/env bash
# team/scripts/archive.sh
# Operator script: move a project's finished items beyond [archive] archive_count
# out of the live project into $KANBAN_ROOT/archive/<project>/, preview that
# move, restore one archived item, or move queue lines left naming items that
# are no longer live.
#
# Usage:
#   archive.sh --project NAME [--dry-run] [--count N]
#   archive.sh --project NAME --restore ID
#   archive.sh --project NAME --reconcile-queues [--dry-run]
#   archive.sh --help
#
# Options:
#   --project NAME   Project to archive (required; exact name, or an unambiguous
#                    prefix of a name registered in projects.cfg). There is no
#                    $PGAI_PROJECT_NAME fallback: archiving moves files, so the
#                    project is always named on the command line.
#   --dry-run        Print the planned moves per category (bugs, priority,
#                    requirements, tasks), the queue lines and training logs
#                    that follow them, the older items that stay live and
#                    why, and the files of any legacy requirements archive
#                    (below) that would move. Nothing on disk changes.
#   --count N        Keep the newest N items per category (tasks: per role)
#                    live, overriding [archive] archive_count (project.cfg,
#                    then kanban.cfg, then 55). Decimal digits only; 0 moves
#                    nothing.
#   --restore ID     Move one archived item back into the live project with its
#                    queue lines and training logs, and record a restore row in
#                    the ledger. ID is BUG-NNNN, PRIORITY-NNNN, a requirements
#                    file name (vX.Y.Z-slug.md) or a task ID. Not combined with
#                    --dry-run or --count.
#   --reconcile-queues
#                    Instead of an archive run, move every line in the live
#                    tasks/queues/*_backlog.md files that names an item no
#                    longer live to archive/<project>/tasks/queues/<same file>,
#                    marker and text unchanged: a line whose task, bug or
#                    priority is archived and not live (any marker), and a
#                    [x] line whose task folder is in neither the live
#                    project nor the archive (only when every live line for
#                    that task is [x]). A line whose item is live is never
#                    touched; other lines naming a missing item are listed and
#                    left. With --dry-run, list the lines that would move and
#                    those left, with the reason, and move nothing. Takes the
#                    repo lock like an archive run; a second run moves
#                    nothing. Runs whatever archive_count is; not combined
#                    with --count or --restore.
#   --help, -h       Show this help and exit 0.
#
# Without --dry-run or --restore the run plans, then applies: it takes the
# project's repo lock (and moves nothing when another process holds it),
# moves each selected item with the queue lines and training logs that follow
# it, and appends one row per item to archive/<project>/ARCHIVE-LEDGER.tsv.
# Only finished items older than the newest N per category move (tasks are
# counted per role, ordered by release version, then date and sequence);
# anything unfinished, unreadable, in the Active RC or still referenced by
# live work stays. Nothing is ever deleted. A second run moves nothing.
#
# When that finishes, the run also empties the requirements archive older
# installs kept inside the live project, projects/<p>/tasks/archive/
# requirements/ (and its priority/ sub-directory): each file moves to
# archive/<project>/priority/ when it is a PRIORITY-NNNN item and to
# archive/<project>/requirements/ otherwise, with one ledger row per file.
# A file whose name is already taken there keeps both copies, the moved one
# renamed <stem>.legacy-<n><suffix>. The emptied directories are left in
# place. With archive_count 0 this step is skipped too.
#
# This script parses flags, prints --help and refuses malformed arguments.
# Everything else -- resolving the project, validating --count, the plan,
# the moves and the restore -- is pgai_agent_kanban.archive.run.run_archive,
# reached through pp_run_ops. POST /operations/archive calls the same
# function in process, so its dry_run reports the same planned moves.
#
# Examples:
#   archive.sh --project pgai-agent-kanban --dry-run
#   archive.sh --project pgai-agent-kanban --count 100
#   archive.sh --project pgai-agent-kanban --restore BUG-NNNN
#   archive.sh --project pgai-agent-kanban --reconcile-queues --dry-run
#   archive.sh --project pgai-agent-kanban --reconcile-queues
#
# Exit codes:
#   0   The run finished (items moved, or nothing to move), the dry run was
#       printed, the item was restored, the queue lines were reconciled, or
#       --help was shown.
#   1   Refused before anything moved: an unknown argument, a missing
#       --project or option value, a project that does not resolve or has no
#       directory, an invalid --count, --restore combined with --dry-run,
#       --count or --reconcile-queues, --reconcile-queues combined with
#       --count, or a restore of an ID that is not archived or whose live
#       path is taken. Each prints error / message / remedy on stderr.
#   2   The run stopped at a failing item (archive.run_failed); items moved
#       before it are in the ledger, and the next run retries.
#   3   Skipped: another process holds the project's repo lock
#       (archive.repo_lock_busy); nothing moved.
#
# Configuration:
#   PGAI_AGENT_KANBAN_ROOT_PATH  the kanban root (resolved by env_bootstrap.sh)
#   [archive] archive_count      in projects/<p>/project.cfg or kanban.cfg

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

_ARCHIVE_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_ARCHIVE_LIB_DIR="${_ARCHIVE_SCRIPT_DIR}/lib"

# shellcheck source=lib/operator_args.sh
source "${_ARCHIVE_LIB_DIR}/operator_args.sh"
# shellcheck source=lib/error_contract.sh
source "${_ARCHIVE_LIB_DIR}/error_contract.sh"
# shellcheck source=lib/pp_run_ops.sh
source "${_ARCHIVE_LIB_DIR}/pp_run_ops.sh"

# Declared flag vocabulary: drives --help and the unknown-argument check.
OPERATOR_VALID_FLAGS=(project dry-run count restore reconcile-queues help)

# --project has no $PGAI_PROJECT_NAME fallback here, and --count / --restore
# are outside the canonical vocabulary, so their help lines are given here.
declare -A OPERATOR_FLAG_HELP=(
    [project]="  --project NAME     Project to archive (required)"
    [dry-run]="  --dry-run          Print the planned moves per category; change nothing"
    [count]="  --count N          Keep the newest N items per category (tasks: per role) live (default: archive_count)"
    [restore]="  --restore ID       Move one archived item back, with its queue lines and logs"
    [reconcile-queues]="  --reconcile-queues Move live queue lines naming items no longer live to the archive"
)

# --count and --restore take values; the canonical set covers --project.
operator_args_parse --extra-value-flags "count restore" "$@"

if argparse_has help; then
    operator_args_render_help_for_flags "archive.sh" \
        "Move a project's finished items beyond archive_count into \$KANBAN_ROOT/archive/<project>/, preview it, restore one item, or reconcile queue lines." \
        OPERATOR_VALID_FLAGS \
        "" \
        "Without --dry-run or --restore the run plans, then moves: only finished items older" \
        "than the newest N per category (tasks: per role) move, with their queue lines and" \
        "training logs, and each move is recorded in archive/<project>/ARCHIVE-LEDGER.tsv." \
        "Nothing is deleted." \
        "The run then moves any legacy projects/<p>/tasks/archive/requirements/ files into" \
        "archive/<project>/requirements/ (PRIORITY-NNNN items into priority/), one ledger row each." \
        "--restore is not combined with --dry-run or --count." \
        "" \
        "--reconcile-queues runs instead of an archive run: each live tasks/queues/*_backlog.md" \
        "line naming an archived, not-live item, or a [x] line whose task folder is in neither" \
        "the live project nor the archive, moves to archive/<project>/tasks/queues/<same file>." \
        "Lines naming live items are never touched; a second run moves nothing. Add --dry-run" \
        "to list what would move (and what is left, with why) first. Not combined with --count." \
        "" \
        "Examples:" \
        "  archive.sh --project pgai-agent-kanban --dry-run" \
        "  archive.sh --project pgai-agent-kanban --restore BUG-NNNN" \
        "  archive.sh --project pgai-agent-kanban --reconcile-queues --dry-run" \
        "" \
        "Exit codes:" \
        "  0  finished (or nothing to move), dry run printed, item restored, or help shown" \
        "  1  refused before anything moved (error / message / remedy on stderr)" \
        "  2  stopped at a failing item; earlier moves are in the ledger" \
        "  3  skipped: the project's repo lock is held by another process"
    exit 0
fi

# ---------------------------------------------------------------------------
# _archive_refuse <error_key> <message> <remedy>
# Print an argument refusal dual-channel on stderr. Each caller exits 1
# itself, before anything is read or moved.
# ---------------------------------------------------------------------------
_archive_refuse() {
    pgai_emit_error_contract "$1" "$2" "$3"
}

_ARCHIVE_ACCEPTED="Accepted: --project NAME, --dry-run, --count N, --restore ID, --reconcile-queues, --help."
_ARCHIVE_HELP_REMEDY="${_ARCHIVE_SCRIPT_DIR}/archive.sh --help"

_archive_unknown=""
if ! _archive_unknown="$(operator_args_validate_known "archive.sh" OPERATOR_VALID_FLAGS 2>&1)"; then
    # BLOCK-WORTHY (docs/blocking-site-census.md): an argument this script
    # does not define names no archive operation it could run; refused
    # rather than ignored, before anything is read or moved.
    _archive_refuse "archive.unknown_argument" \
        "${_archive_unknown//$'\n'/; }. ${_ARCHIVE_ACCEPTED} Nothing was moved." \
        "${_ARCHIVE_HELP_REMEDY}"
    # CENSUS-ANCHOR: archive-cli-unknown-argument-01
    exit 1
fi
if [[ "${#ARGPARSE_POSITIONAL[@]}" -gt 0 ]]; then
    # BLOCK-WORTHY: the same refusal for a bare word, which no flag takes.
    _archive_refuse "archive.unknown_argument" \
        "archive.sh: unknown argument: ${ARGPARSE_POSITIONAL[0]}. ${_ARCHIVE_ACCEPTED} Nothing was moved." \
        "${_ARCHIVE_HELP_REMEDY}"
    # CENSUS-ANCHOR: archive-cli-unknown-argument-02
    exit 1
fi

for _archive_flag in project count restore; do
    if argparse_missing "${_archive_flag}"; then
        # BLOCK-WORTHY (docs/blocking-site-census.md): an option given
        # without its value names nothing to apply; refused, not guessed.
        _archive_refuse "archive.option_value_required" \
            "archive.sh: --${_archive_flag} needs a value and none was given. Nothing was moved." \
            "${_ARCHIVE_SCRIPT_DIR}/archive.sh --project pgai-agent-kanban --dry-run --count 55"
        # CENSUS-ANCHOR: archive-cli-option-value-required-01
        exit 1
    fi
done

_archive_project="$(operator_args_get project)"
if [[ -z "${_archive_project}" ]]; then
    # BLOCK-WORTHY (docs/blocking-site-census.md): archiving moves files out
    # of one project, so the project is always named; refused rather than
    # taken from the ambient environment.
    _archive_refuse "archive.project_required" \
        "archive.sh: --project is required; archiving moves one named project's finished items and never picks a project on its own. Nothing was moved." \
        "${_ARCHIVE_SCRIPT_DIR}/archive.sh --project pgai-agent-kanban --dry-run"
    # CENSUS-ANCHOR: archive-cli-project-required-01
    exit 1
fi

KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
export KANBAN_ROOT

_archive_args=(--kanban-root "${KANBAN_ROOT}" --project "${_archive_project}")
argparse_has dry-run && _archive_args+=(--dry-run)
argparse_has count && _archive_args+=(--count "$(operator_args_get count)")
argparse_has restore && _archive_args+=(--restore "$(operator_args_get restore)")
argparse_has reconcile-queues && _archive_args+=(--reconcile-queues)

# run_archive prints the narration on stdout, any refusal or failure block on
# stderr, and exits with the code documented in the header.
pp_run_ops pgai_agent_kanban.archive run_archive "${_archive_args[@]}"
