#!/usr/bin/env bash
# team/scripts/wake-batch.sh
# Provider-agnostic wake dispatcher.
#
# Reads kanban.cfg [providers] active and execs the matching
# provider-specific wake script under wake/<provider>.sh, once per
# registered project (see below).
#
# All arguments are passed through verbatim to the dispatched script.
# The agent is read from them the way the provider wake scripts read it
# (lib/wake_batch_guard.sh::wake_batch_guard_agent); when no agent can be
# determined the firing refuses, exit 1, and starts no provider wake
# script -- without an agent there is no guard to take.
#
# The active provider is read once per firing from kanban.cfg
# [providers] active (via lib/active_provider.sh::read_active_provider).
# Defaults to 'claude' if unset or invalid.
#
# To switch providers without editing crontab:
#   scripts/switch-provider.sh codex
# (Updates kanban.cfg [providers] active; the next cron firing
# dispatches to wake/codex.sh.)
#
# Firings of one agent never overlap within a project, while different
# projects run concurrently. One firing covers every registered, enabled
# project (projects.cfg): for each one it forks a batch that first claims
# that (project, agent) pair's single-instance guard (lib/wake_batch_guard.sh)
# -- a record under <kanban_root>/locks/ naming the batch's pid, which the
# exec of the provider wake script keeps for the whole batch -- and then
# runs the provider wake for that project alone (PGAI_WAKE_BATCH_PROJECT,
# read by wake_common_run). A pair whose earlier batch is still running gets
# one log line naming the project, that batch's pid and age, and nothing
# else; a record whose pid is dead (or reused) is taken over. The firing
# waits for the batches it started and exits with the first non-zero
# status among them, else 0. Different agents never wait on each other,
# and within one project the per-project repo lock still lets only one
# agent's chain run at a time. MAX_TASKS_PER_WAKE and the [wake] runtime
# caps apply to each (project, agent) batch.
#
# The registry is read once per firing. A projects.cfg that exists but
# cannot be read is refused: the firing prints a dual-channel refusal
# naming the file, starts no provider wake script, exits 1 and leaves one
# open operator-inbox question about it. A missing projects.cfg, or one that
# registers no enabled project, is not a fault: one "nothing to wake" line,
# exit 0. A registered name that is not a plain project name (one the
# project-name rule PROJECTS_CFG_NAME_RE in lib/projects_cfg.sh accepts: a
# lowercase letter or digit, then lowercase letters, digits and '-') gets a
# log line naming it, a dual-channel refusal and one open operator-inbox question,
# and no batch; every other project is still woken. That includes a
# [project:...] section whose name the registry does not accept at all
# ([project:bad.name], [project:bad name], [project:My_App]; lib/projects.sh's
# projects_cfg_unaccepted_names), which no registry reader lists. Each question goes to
# the inbox (pgai_agent_kanban.overwatch.inbox, written with --once, so a
# firing that meets the same fault again adds no second entry) of the first
# registered project that has a directory, else of the first project
# directory under <kanban_root>/projects/ whose name the same rule accepts.
#
# This script is intentionally minimal — see wake/<provider>.sh for
# the actual wake-loop logic and wake_common.sh for shared substrate.

_wake_batch_usage() {
    cat <<'EOF'
Usage: wake-batch.sh --agent=AGENT [--sleep=N] [--max-tasks=N]
       wake-batch.sh AGENT      # legacy positional form (deprecated)

Provider-agnostic wake dispatcher.  Reads kanban.cfg [providers] active
and execs the matching provider-specific wake script under wake/<provider>.sh,
once per registered project.
All arguments are forwarded verbatim to the dispatched script.

Firings of one agent never overlap within a project, while different
projects run concurrently: one firing starts a batch for every registered,
enabled project that has no running batch of the agent, and logs one line
for each project that does, naming that batch's pid and age. Each batch
works its own project only; MAX_TASKS_PER_WAKE and the [wake] runtime caps
apply per (project, agent) batch.

A projects.cfg that exists but cannot be read is refused: nothing is started
and one open operator-inbox question names the file. A registered name that
is not a plain project name (a lowercase letter or digit, then lowercase
letters, digits and '-'), a [project:...] section such as [project:bad.name]
or [project:My_App] included, is logged
by name, gets a dual-channel refusal (error wake_batch.project_name_not_plain),
no batch and one open operator-inbox question; every other project is still
woken. A firing that meets the same fault again adds no second entry.

Arguments:
  --agent=AGENT        Agent role to wake: pm|coder|writer|tester|cm|po (required)
  AGENT                Legacy positional form (deprecated; emits a warning)
  --sleep=N            Seconds to sleep before running (default: 0; use for cron stagger)
  --max-tasks=N        Maximum tasks to process per invocation (default: from kanban.cfg)
  --help, -h           Print this help and exit

Exit codes:
  0   Every batch completed successfully (or nothing to process: projects.cfg
      is missing or registers no enabled project; or every project already
      has a running batch of the agent). A skipped name that is not a plain
      project name does not change the exit code.
  1   Configuration error, provider script not found, projects.cfg exists
      but cannot be read, or no agent could be determined from the arguments
      (nothing is started); otherwise the first non-zero status among the
      batches this firing started.

Examples:
  wake-batch.sh --agent=pm
  wake-batch.sh --agent=coder --sleep=21 --max-tasks=5
  wake-batch.sh coder          # deprecated positional form

Cron examples:
  */2 * * * *    wake-batch.sh --agent=pm     --sleep=0
  */2 * * * *    wake-batch.sh --agent=cm     --sleep=21
  1-59/2 * * * * wake-batch.sh --agent=coder  --sleep=0
EOF
}

case "${1:-}" in
    --help|-h)
        _wake_batch_usage
        exit 0
        ;;
esac

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lib/env_bootstrap.sh"

# Resolve kanban root: canonical var with default install path fallback.
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

# Resolve active provider from kanban.cfg [providers] active.
# read_active_provider always returns a valid name (defaults to 'claude').
# shellcheck source=lib/active_provider.sh
source "$SCRIPT_DIR/lib/active_provider.sh"
PROVIDER="$(read_active_provider "$KANBAN_ROOT")"

WAKE_SCRIPT="$SCRIPT_DIR/wake/${PROVIDER}.sh"

if [[ ! -x "$WAKE_SCRIPT" ]]; then
    echo "wake-batch.sh: ERROR: provider wake script not found or not executable: $WAKE_SCRIPT" >&2
    echo "  Active provider: ${PROVIDER}" >&2
    echo "  Check: ls -la $SCRIPT_DIR/wake/" >&2
    # BLOCK-WORTHY: a wake firing cannot dispatch to a provider wake script
    # that does not exist or is not executable.
    # CENSUS-ANCHOR: wake-batch-provider-script-missing-01
    exit 1
fi

# Single-instance guard: one running batch per (project, agent) (see the
# header).
# shellcheck source=lib/error_contract.sh
source "$SCRIPT_DIR/lib/error_contract.sh"
# shellcheck source=lib/wake_batch_guard.sh
source "$SCRIPT_DIR/lib/wake_batch_guard.sh"
_WB_AGENT="$(wake_batch_guard_agent "$@")"
if [[ -z "$_WB_AGENT" ]]; then
    # A --help / -h after other arguments still prints the usage.
    for _wb_arg in "$@"; do
        if [[ "$_wb_arg" == "--help" || "$_wb_arg" == "-h" ]]; then
            _wake_batch_usage
            exit 0
        fi
    done
    # BLOCK-WORTHY: without an agent there is no (project, agent) guard to
    # take, so starting the provider wake script would run it unguarded;
    # the firing starts nothing and exits 1.
    ( unset PGAI_PROJECT_NAME
      pgai_emit_error_contract "wake_batch.agent_undetermined" \
        "wake-batch.sh: no agent could be determined from the arguments '${*}'; a firing needs one plain agent name (--agent=AGENT, --agent AGENT, -a AGENT or the legacy positional AGENT), so it started no provider wake script." \
        "${SCRIPT_DIR}/wake-batch.sh --agent=coder --sleep=0" ) || true
    # CENSUS-ANCHOR: wake-batch-agent-undetermined-01
    exit 1
fi

# shellcheck source=lib/projects.sh
source "$SCRIPT_DIR/lib/projects.sh"

# _wake_batch_run_project <project> <args...>
# Runs in its own forked subshell: claims the (project, agent) guard for
# this subshell's pid, then execs the provider wake script for <project>
# alone, so the record names the running batch for its whole life.
_wake_batch_run_project() {
    local project="$1"
    shift
    local guard_rc=0
    wake_batch_guard_take "$KANBAN_ROOT" "$_WB_AGENT" "$project" "$BASHPID" || guard_rc=$?
    if [[ $guard_rc -eq 1 ]]; then
        echo "wake-batch(${_WB_AGENT}): previous firing still running for project ${project} (pid ${WAKE_BATCH_GUARD_HOLDER_PID:-unknown}, age ${WAKE_BATCH_GUARD_HOLDER_AGE:-unknown}); skipping"
        # BLOCK-WORTHY: an invariant by design -- one running batch per
        # (project, agent); a second would contend with the first for that
        # project's lock and queue. This project gets no batch from this
        # firing; every other project's batch runs, and the firing exits 0.
        ( unset PGAI_PROJECT_NAME
          pgai_emit_error_contract "wake_batch.previous_firing_running" \
            "wake-batch.sh: an earlier ${_WB_AGENT} batch for project ${project} (pid ${WAKE_BATCH_GUARD_HOLDER_PID:-unknown}, running ${WAKE_BATCH_GUARD_HOLDER_AGE:-unknown}) is still running, so this firing started none for that project; the next firing after it ends runs as usual." \
            "ps -o pid,etime,args -p ${WAKE_BATCH_GUARD_HOLDER_PID:-1}" ) || true
        # CENSUS-ANCHOR: wake-batch-previous-firing-running-01
        exit 0
    elif [[ $guard_rc -ne 0 ]]; then
        echo "wake-batch(${_WB_AGENT}): single-instance guard for project ${project} under ${KANBAN_ROOT}/locks could not be read or written; running unguarded" >&2
    fi
    export PGAI_WAKE_BATCH_PROJECT="$project"
    exec "$WAKE_SCRIPT" "$@"
}

# _wake_batch_inbox_project
# Echo the project whose operator inbox a registry question goes to: the
# first registered project (in _WB_PROJECTS order), else the first project
# directory under <kanban_root>/projects/, whose name the inbox accepts and
# whose directory exists -- the inbox accepts a name the project-name rule
# (PROJECTS_CFG_NAME_RE, lib/projects_cfg.sh) accepts. Echoes nothing when
# there is none.
_wake_batch_inbox_project() {
    local -a candidates=(${_WB_PROJECTS[@]+"${_WB_PROJECTS[@]}"})
    local dir name
    for dir in "${KANBAN_ROOT}/projects"/*/; do
        [[ -d "$dir" ]] && candidates+=("$(basename "$dir")")
    done
    for name in ${candidates[@]+"${candidates[@]}"}; do
        if [[ "$name" =~ $PROJECTS_CFG_NAME_RE && -d "${KANBAN_ROOT}/projects/${name}" ]]; then
            printf '%s\n' "$name"
            return 0
        fi
    done
    return 0
}

# _wake_batch_park_question <question> <meanwhile> <how_to_answer>
# Leave <question> as one open operator-inbox entry (source "wake-batch")
# through pgai_agent_kanban.overwatch.inbox, written with --once so the same
# question met on every firing stays one entry. Logs the entry's ID, or one
# line saying why none was written; never changes the firing's outcome.
_wake_batch_park_question() {
    local question="$1" meanwhile="$2" how_to_answer="$3"
    local project out rc=0
    project="$(_wake_batch_inbox_project)"
    if [[ -z "$project" ]]; then
        echo "wake-batch(${_WB_AGENT}): no project directory under ${KANBAN_ROOT}/projects to hold an operator inbox entry; the question is in this log only"
        return 0
    fi
    how_to_answer+=" Then close this entry: $(printf '%q' "${KANBAN_ROOT}/scripts/inbox.sh") --project ${project} close --id followed by this entry's INBOX ID."
    out="$(pp_run_ops pgai_agent_kanban.overwatch.inbox write_entry --once \
        --kanban-root "$KANBAN_ROOT" --project "$project" --source "wake-batch" \
        --question "$question" --meanwhile "$meanwhile" --how-to-answer "$how_to_answer" 2>&1)" || rc=$?
    if [[ $rc -eq 0 && "$out" =~ (INBOX-[0-9]+) ]]; then
        echo "wake-batch(${_WB_AGENT}): operator inbox entry ${BASH_REMATCH[1]} in project ${project} is open for this"
    else
        echo "wake-batch(${_WB_AGENT}): could not write the operator inbox entry in project ${project} (exit ${rc}): ${out//$'\n'/ }" >&2
    fi
    return 0
}

_WB_REGISTRY="$(projects_cfg_path)"
_WB_REGISTRY_Q="$(printf '%q' "$_WB_REGISTRY")"
declare -a _WB_PROJECTS=()

# A registry that exists but cannot be read is not "nothing to wake": no
# project could be woken until it is fixed, so say so and stop.
if [[ -e "$_WB_REGISTRY" && ! -r "$_WB_REGISTRY" ]]; then
    echo "wake-batch(${_WB_AGENT}): projects.cfg at ${_WB_REGISTRY} exists but cannot be read; no project is woken until it can be"
    _wake_batch_park_question \
        "The project registry ${_WB_REGISTRY} exists but cannot be read by the account cron runs as, so wake-batch starts no wake for any project. Make it readable?" \
        "Every wake-batch firing refuses, with exit status 1, and starts no provider wake script for any project until the registry can be read." \
        "Run chmod u+r ${_WB_REGISTRY_Q} as the file's owner; the next firing wakes every registered project again."
    # BLOCK-WORTHY: verification unable to run -- without the registry the
    # firing cannot tell which projects to wake, so it starts nothing and
    # exits 1 rather than report "nothing to wake".
    ( unset PGAI_PROJECT_NAME
      pgai_emit_error_contract "wake_batch.registry_unreadable" \
        "wake-batch.sh: the project registry ${_WB_REGISTRY} exists but cannot be read, so this firing cannot tell which projects to wake; it started no provider wake script, and the question is parked in the operator inbox." \
        "chmod u+r ${_WB_REGISTRY_Q}" ) || true
    # CENSUS-ANCHOR: wake-batch-registry-unreadable-01
    exit 1
fi

mapfile -t _WB_PROJECTS < <(projects_cfg_list 2>/dev/null || true)
# A [project:...] section whose name the registry does not accept
# ([project:bad.name], [project:bad name], [project:]) is no project to
# projects_cfg_list, so it is read separately and refused below by name
# like any other name that is not plain.
declare -a _WB_UNACCEPTED=()
mapfile -t _WB_UNACCEPTED < <(projects_cfg_unaccepted_names 2>/dev/null || true)

declare -a _WB_CANDIDATES=()
for _wb_project in ${_WB_PROJECTS[@]+"${_WB_PROJECTS[@]}"}; do
    [[ -n "$_wb_project" ]] && _WB_CANDIDATES+=("$_wb_project")
done
_WB_CANDIDATES+=(${_WB_UNACCEPTED[@]+"${_WB_UNACCEPTED[@]}"})

declare -a _WB_CHILDREN=()
for _wb_project in ${_WB_CANDIDATES[@]+"${_WB_CANDIDATES[@]}"}; do
    if [[ ! "$_wb_project" =~ $PROJECTS_CFG_NAME_RE ]]; then
        echo "wake-batch(${_WB_AGENT}): project '${_wb_project}' in ${_WB_REGISTRY} is not a plain project name (one matching ${PROJECTS_CFG_NAME_RE}); skipped, every other project is still woken"
        _wake_batch_park_question \
            "The project registry ${_WB_REGISTRY} registers '${_wb_project}', which is not a plain project name (one matching ${PROJECTS_CFG_NAME_RE}), so wake-batch never wakes it. Rename or remove that entry?" \
            "Every other registered project is still woken; '${_wb_project}' is skipped on every firing until its entry is fixed." \
            "Edit ${_WB_REGISTRY_Q}: rename the entry to a plain name such as my-project, or remove it."
        # BLOCK-WORTHY: an invariant by design -- the name becomes part of
        # paths under locks/ and projects/, so only a plain name is woken.
        # This project gets no batch; every other project's batch runs, and
        # the firing's exit code is theirs.
        ( unset PGAI_PROJECT_NAME
          pgai_emit_error_contract "wake_batch.project_name_not_plain" \
            "wake-batch.sh: the project registry ${_WB_REGISTRY} registers '${_wb_project}', which is not a plain project name (one matching ${PROJECTS_CFG_NAME_RE}), so this firing started no batch for it and woke every other registered project." \
            "grep -n -F -- $(printf '%q' "$_wb_project") ${_WB_REGISTRY_Q}" ) || true
        # CENSUS-ANCHOR: wake-batch-project-name-not-plain-01
        continue
    fi
    if ! projects_cfg_enabled "$_wb_project"; then
        echo "wake-batch(${_WB_AGENT}): $(projects_cfg_disabled_skip_line "$_wb_project")"
        continue
    fi
    _wake_batch_run_project "$_wb_project" "$@" &
    _WB_CHILDREN+=("$!")
done
unset _wb_project

if (( ${#_WB_CHILDREN[@]} == 0 )); then
    echo "wake-batch(${_WB_AGENT}): no registered, enabled project in $(projects_cfg_path); nothing to wake"
    exit 0
fi

# A signal meant for the firing reaches every batch it started.
_wake_batch_forward_signal() {
    kill -s "$1" "${_WB_CHILDREN[@]}" 2>/dev/null || true
}
trap '_wake_batch_forward_signal TERM' TERM
trap '_wake_batch_forward_signal INT' INT
trap '_wake_batch_forward_signal HUP' HUP

# Wait for every batch. A trapped signal interrupts `wait` with a status
# above 128 while the batch still runs; wait for it again in that case.
_wb_exit=0
for _wb_child in "${_WB_CHILDREN[@]}"; do
    while :; do
        _wb_status=0
        wait "$_wb_child" || _wb_status=$?
        if (( _wb_status > 128 )) && kill -0 "$_wb_child" 2>/dev/null; then
            continue
        fi
        break
    done
    if (( _wb_exit == 0 && _wb_status != 0 )); then
        _wb_exit=$_wb_status
    fi
done
exit "$_wb_exit"
