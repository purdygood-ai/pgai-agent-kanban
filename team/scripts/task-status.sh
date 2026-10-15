#!/usr/bin/env bash
# task-status.sh
# Thin wrapper around team/pgai_agent_kanban/task_status.py — the status
# writer tool agents call instead of hand-editing a task's status.md.
#
# All validation, state-vocabulary checking, section-preservation, atomic
# writing, and dual-channel error emission live in task_status.py. This
# wrapper carries no business logic: it delegates to the shared
# pp_run_ops helper (which resolves a correct, cwd-independent PYTHONPATH)
# and forwards every argument to the Python entry point unchanged.
#
# Usage:
#   task-status.sh --project <name> --key <task-id> --state <STATE> \
#       [--needs-human yes|no] [--summary <text>] [--blockers <text>] \
#       [--reason-code agent_<...>] [--action-args <key=value> ...]
#
#   task-status.sh --read-state <path/to/status.md>
#
#   task-status.sh read (<path/to/status.md> | --project <name> --key <task-id>) \
#       [--section <heading>] [--raw]
#
#   task-status.sh set-section (<path/to/status.md> | --project <name> --key <task-id>) \
#       --section <heading> --body <text>
#
#   task-status.sh render <path/to/status.md> [--section <heading> --body <text>]... \
#       [--skip-missing] [--insert-after <heading>]... [--collapse-blank-lines]
#
# <STATE> must be one of: BACKLOG|WAITING|WORKING|BLOCKED|DONE|WONT-DO
# (see team/scripts/lib/validate_state.sh for the sibling bash validator of
# the same six-value vocabulary).
#
# Run 'task-status.sh --help' for the full flag reference (delegates
# straight to task_status.py's argparse --help).
#
# Block Record: a --state BLOCKED write also records the task's
# ## Block Record in the same atomic write (pgai_agent_kanban.overwatch.
# block_record), with owner_role taken from the task's ## Role (README.md,
# then status.md):
#   --reason-code <code>   why the task blocked, from the closed agent list
#                          (agent_operator_data_action, agent_unclassified;
#                          list them with: python3 -m
#                          pgai_agent_kanban.overwatch.block_record codes --agent).
#                          Omitted, the record says agent_unclassified -- unless
#                          the task is already BLOCKED with a record, which is
#                          then kept as it is.
#   --action-args <k=v>... typed key=value pairs, required with
#                          agent_operator_data_action and refused without it:
#                          verb=<allowlisted verb> plus exactly that verb's
#                          arguments (codes --action-verbs lists them), stored
#                          in the record's evidence line. For example:
#     task-status.sh --project pgai-agent-kanban \
#         --key CODER-20260101-001-example-task --state BLOCKED --needs-human yes \
#         --reason-code agent_operator_data_action \
#         --action-args verb=restamp-fixed-in project=pgai-agent-kanban \
#                       version=v2.28.0 bugs=BUG-0412,BUG-0413   (example values)
# Either flag on a state other than BLOCKED, an unknown code, or an unknown,
# repeated, missing or malformed action argument is refused dual-channel and
# nothing is written. A --state DONE or WONT-DO write removes the record.
# A task whose role is not CODER, WRITER, TESTER, CM or PM gets its state
# written, with a warning on stderr and no record.
#
# --read-state is the read-only form: it prints the file's ## State value,
# uppercased (an empty line when the file or value is absent), writes
# nothing, and needs no --project/--key. Shell callers that must tell a
# terminal task (DONE/WONT-DO) from a live one read through it rather than
# parsing status.md themselves.
#
# read is the read mode for callers that name the task by --project/--key
# or by path: with no --section it prints the same uppercased ## State
# value --read-state prints; with --section <heading> it prints that
# section's body. An absent section prints an empty line; an absent
# status.md is refused dual-channel. It writes nothing.
#
# set-section replaces one section's body atomically, preserving every
# other section byte-for-byte, for the sections the --state flags do not
# manage (## Artifacts, ## Next Recommended Step, ...). ## State is refused
# there (use --state, which validates it); a Needs Human body must be
# yes or no; the section must already exist.
#
# read --raw and render are the primitives the wake's own status readers
# and writers (team/scripts/lib/wake_task_state.sh) call. read --raw prints
# a section body exactly as written (## State included) and exits 3 when
# the heading is absent. render prints the file with section bodies
# replaced (## State accepted, nothing validated), a section upserted
# (--insert-after), or blank-line runs cut to one, and writes nothing: the
# caller commits the output through its own atomic writer. Both report a
# failure as one plain stderr line with exit 1 rather than a dual-channel
# refusal; render's missing-section line is "Could not update ## <heading>
# block".
#
# Exit codes:
#   0   status.md written successfully, or --read-state / read printed the
#       value.
#   1   Bad input (missing --project, missing --key, invalid --state,
#       invalid --needs-human, an unknown --reason-code, invalid
#       --action-args, missing status.md, missing section, a missing
#       or conflicting read/set-section target) — a
#       dual-channel error (error key + message + remedy) is printed to
#       stderr via the shared error-contract emitter; nothing is written.
#
# The script resolves the repository root from its own location (via
# pp_run_ops.sh's self-location) so it can be invoked from any working
# directory.

set -euo pipefail

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Check Python 3 is available.
if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 not found in PATH." >&2
    exit 1
fi

# shellcheck source=lib/pp_run_ops.sh
source "${_SCRIPT_DIR}/lib/pp_run_ops.sh"

# Forward all arguments unchanged — task_status.py owns argument parsing,
# validation, and the dual-channel error contract on bad input.
pp_run_ops pgai_agent_kanban.task_status "$@"
