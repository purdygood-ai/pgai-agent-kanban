#!/usr/bin/env bash
# team/scripts/inbox.sh
# Operator verb: list, answer and close the questions in a project's operator
# inbox (projects/<name>/operator-inbox.md) -- the questions the chain parked
# for a human answer while it carried on.
#
# Usage:
#   inbox.sh --project NAME list
#   inbox.sh --project NAME answer --id INBOX-NNNN --answer TEXT
#   inbox.sh --project NAME close  --id INBOX-NNNN
#   inbox.sh --help
#
#   list    Print the open entries under "## Open", then the closed ones
#           under "## Done", each in the file's own shape.
#   answer  Record TEXT as the answer on an open entry. The entry stays open
#           until it is closed; a second answer replaces the first. When the
#           entry is about a bug or priority (its Source names the item), the
#           answer, entry ID and time are also appended to the item's
#           "## Operator Answers" section, and an item at needs-operator is
#           returned to open.
#   close   Move an open entry to Done, keeping its answer if it has one.
#
# --project is required: $PGAI_PROJECT_NAME never stands in for it, so an
# answer cannot land in the wrong project's inbox from an agent shell. The
# name resolves like every project-scoped verb's (exact, then unambiguous
# prefix of a name registered in projects.cfg). Entry IDs are read liberally:
# INBOX-0007, inbox_7 and 7 name the same entry.
#
# Thin adapter: every read and write is pgai_agent_kanban.overwatch.inbox --
# the module that is the inbox file's only reader and writer -- reached
# through pp_run_ops by the name of the function each command runs
# (read_inbox, answer_entry, close_entry). The REST twins call the same
# functions in process: GET /projects/{name}/inbox (read_inbox) and
# POST /operations/inbox-answer (answer_entry).
#
# Exit codes:
#   0   Done; list printed, or the answered/closed entry printed.
#   1   --project names no registered project (project_resolve.* block).
#   2   Bad input: a missing or unknown command, a missing --project, a flag
#       the command does not take, an empty answer, a malformed entry ID, an
#       entry already closed, or the inbox lock stayed busy.
#   3   The entry or the project directory does not exist.
#   4   The inbox file or its lock could not be read or written.
# Every refusal prints the dual-channel error/message/remedy block on stderr
# and writes nothing to the inbox.

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lib/env_bootstrap.sh"

_INBOX_SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
_INBOX_LIB_DIR="${_INBOX_SCRIPT_DIR}/lib"
_INBOX_SELF="$(printf '%q' "${_INBOX_SCRIPT_DIR}/inbox.sh")"

# shellcheck source=lib/operator_args.sh
source "${_INBOX_LIB_DIR}/operator_args.sh"
# shellcheck source=lib/project_paths.sh
source "${_INBOX_LIB_DIR}/project_paths.sh"
# shellcheck source=lib/error_contract.sh
source "${_INBOX_LIB_DIR}/error_contract.sh"
# shellcheck source=lib/pp_run_ops.sh
source "${_INBOX_LIB_DIR}/pp_run_ops.sh"

OPERATOR_VALID_FLAGS=(project id answer help)

operator_args_parse --extra-value-flags "id answer" "$@"

if argparse_has help; then
    declare -A OPERATOR_FLAG_HELP=(
        [project]="  --project NAME     Project whose inbox to use (required; \$PGAI_PROJECT_NAME is not read)"
        [id]="  --id ID            Entry ID for answer and close, e.g. INBOX-0001 (case and leading zeros ignored)"
        [answer]="  --answer TEXT      The answer to record (answer only)"
    )
    operator_args_render_help_for_flags "inbox.sh" \
        "List, answer and close the questions in a project's operator inbox." \
        OPERATOR_VALID_FLAGS \
        "" \
        "Commands:" \
        "  list                          Print the open entries (## Open), then the" \
        "                                closed ones (## Done)." \
        "  answer --id ID --answer TEXT  Record an answer on an open entry; it stays" \
        "                                open until closed. A second answer replaces" \
        "                                the first. Answering an entry about a bug" \
        "                                or priority also appends the answer to the" \
        "                                item's ## Operator Answers section and returns" \
        "                                a needs-operator item to open." \
        "  close --id ID                 Move an open entry to Done, keeping its answer." \
        "" \
        "Examples:" \
        "  inbox.sh --project pgai-agent-kanban list" \
        "  inbox.sh --project pgai-agent-kanban answer --id INBOX-0001 --answer keep" \
        "  inbox.sh --project pgai-agent-kanban close --id INBOX-0001" \
        "" \
        "The same inbox over HTTP: GET /projects/{name}/inbox and" \
        "POST /operations/inbox-answer (docs/operator-api.md)." \
        "" \
        "Exit codes:" \
        "  0  Done" \
        "  1  --project names no registered project" \
        "  2  Bad input (command, flag, answer or entry ID), an entry already" \
        "     closed, or the inbox lock stayed busy" \
        "  3  The entry or the project directory does not exist" \
        "  4  The inbox file or its lock could not be read or written" \
        "Every refusal prints error/message/remedy on stderr and writes nothing."
    exit 0
fi

# _inbox_refuse ERROR MESSAGE REMEDY
# The one refusal site for a malformed call, before the inbox module runs.
_inbox_refuse() {
    # AGENT-RESOLVABLE (docs/blocking-site-census.md): a malformed call is
    # refused before the inbox module is reached, so the inbox file is
    # untouched; the caller corrects its own next call in the same session
    # and nothing needs a human.
    pgai_emit_error_contract "$1" "$2" "$3"
    # CENSUS-ANCHOR: inbox-sh-refusal-01
    exit 2
}

if ! _inbox_unknown="$(operator_args_validate_known "inbox.sh" OPERATOR_VALID_FLAGS 2>&1)"; then
    _inbox_refuse "inbox.unknown_flag" \
        "${_inbox_unknown//$'\n'/; }; inbox.sh takes --project, --id, --answer and --help, so nothing was run." \
        "${_INBOX_SELF} --help"
fi

_inbox_project_raw="$(operator_args_get project)"
_inbox_command="${ARGPARSE_POSITIONAL[0]:-}"
_inbox_example_project="${_inbox_project_raw:-pgai-agent-kanban}"

# _inbox_extra_argument_remedy
# Print the remedy for a call carrying a word after its command, derived from
# that call. 'close X' becomes 'close --id X'; 'answer X --answer TEXT'
# becomes 'answer --id X --answer TEXT'; 'list X' becomes the plain 'list'.
# A command that writes is printed only when --project was given, exactly one
# word follows the command, and no flag the call already carries competes
# with it -- so the remedy never aims a write at an entry, an answer or a
# project the caller did not name. An unknown command gets --help; anything
# else (no --answer, several words, a second --id) gets the read-only list.
_inbox_extra_argument_remedy() {
    local project word answer
    project="$(printf '%q' "${_inbox_example_project}")"
    word="${ARGPARSE_POSITIONAL[1]}"
    case "${_inbox_command}" in
        list|answer|close) ;;
        *)
            printf '%s --help\n' "${_INBOX_SELF}"
            return 0
            ;;
    esac
    if [[ -n "${_inbox_project_raw}" && ${#ARGPARSE_POSITIONAL[@]} -eq 2 \
          && -n "${word//[[:space:]]/}" ]] && ! argparse_has id; then
        if [[ "${_inbox_command}" == "close" ]] && ! argparse_has answer; then
            printf '%s --project %s close --id %q\n' "${_INBOX_SELF}" "${project}" "${word}"
            return 0
        fi
        answer="$(operator_args_get answer)"
        if [[ "${_inbox_command}" == "answer" && -n "${answer//[[:space:]]/}" ]]; then
            printf '%s --project %s answer --id %q --answer %q\n' \
                "${_INBOX_SELF}" "${project}" "${word}" "${answer}"
            return 0
        fi
    fi
    printf '%s --project %s list\n' "${_INBOX_SELF}" "${project}"
}

if [[ ${#ARGPARSE_POSITIONAL[@]} -gt 1 ]]; then
    _inbox_refuse "inbox.extra_argument" \
        "inbox.sh takes one command (list, answer or close) and passes everything else as flags; it was also given '${ARGPARSE_POSITIONAL[1]}', so nothing was run." \
        "$(_inbox_extra_argument_remedy)"
fi

if [[ -z "${_inbox_command}" ]]; then
    _inbox_refuse "inbox.command_required" \
        "inbox.sh needs a command -- list, answer or close -- and none was given, so nothing was run." \
        "${_INBOX_SELF} --project $(printf '%q' "${_inbox_example_project}") list"
fi

case "${_inbox_command}" in
    list) _inbox_allowed="" ;;
    answer) _inbox_allowed="id answer" ;;
    close) _inbox_allowed="id" ;;
    *)
        _inbox_refuse "inbox.unknown_command" \
            "'${_inbox_command}' is not an inbox.sh command (list, answer or close), so nothing was run." \
            "${_INBOX_SELF} --help"
        ;;
esac

if [[ -z "${_inbox_project_raw}" ]]; then
    _inbox_refuse "inbox.project_required" \
        "inbox.sh ${_inbox_command} needs --project naming the inbox to use; \$PGAI_PROJECT_NAME is never read in its place, so nothing was run." \
        "${_INBOX_SELF} --project pgai-agent-kanban list"
fi

for _inbox_flag in id answer; do
    if argparse_has "${_inbox_flag}" && [[ " ${_inbox_allowed} " != *" ${_inbox_flag} "* ]]; then
        _inbox_refuse "inbox.flag_not_used" \
            "inbox.sh ${_inbox_command} does not take --${_inbox_flag}; only answer takes --answer, and only answer and close take --id, so nothing was run." \
            "${_INBOX_SELF} --help"
    fi
done

# AGENT-RESOLVABLE (docs/blocking-site-census.md): --project names no
# registered project (or a prefix two share); the shared resolver has
# already printed its dual-channel project_resolve.* block naming the
# registered projects, nothing was read or written, and the caller re-runs
# with a registered name.
# CENSUS-ANCHOR: inbox-sh-project-resolve-01
_inbox_project="$(pp_resolve_project_name "${_inbox_project_raw}")" || exit 1
_inbox_root="${PGAI_AGENT_KANBAN_ROOT_PATH}"
_inbox_id="$(operator_args_get id)"
_inbox_answer="$(operator_args_get answer)"

# Values go in --flag=value form so an answer that starts with '-' is never
# read as a flag. The module refuses an empty answer or a malformed ID.
_inbox_rc=0
case "${_inbox_command}" in
    list)
        pp_run_ops pgai_agent_kanban.overwatch.inbox read_inbox \
            "--kanban-root=${_inbox_root}" "--project=${_inbox_project}" --all || _inbox_rc=$?
        ;;
    answer)
        pp_run_ops pgai_agent_kanban.overwatch.inbox answer_entry \
            "--kanban-root=${_inbox_root}" "--project=${_inbox_project}" \
            "--id=${_inbox_id}" "--answer=${_inbox_answer}" || _inbox_rc=$?
        ;;
    close)
        pp_run_ops pgai_agent_kanban.overwatch.inbox close_entry \
            "--kanban-root=${_inbox_root}" "--project=${_inbox_project}" \
            "--id=${_inbox_id}" || _inbox_rc=$?
        ;;
esac
exit "${_inbox_rc}"
