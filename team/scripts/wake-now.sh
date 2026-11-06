#!/usr/bin/env bash
# team/scripts/wake-now.sh
# Convenience wrapper: fire one or all agents immediately (--sleep=0).
# --agent overwatch runs the OVERWATCH sweep (overwatch-sweep.sh), which is
# what every OVERWATCH firing is; it never starts a model wake.
#
# Usage:
#   wake-now.sh --agent <role> [--bg]   Fire one named agent; --bg backgrounds it.
#   wake-now.sh --all                   Fire all pipeline agents (pm coder cm writer tester), backgrounded.
#   wake-now.sh --help                  Print this message.
#
# Options:
#   --agent ROLE     Agent role to wake: pm|coder|cm|writer|tester|overwatch (required unless --all);
#                    overwatch runs overwatch-sweep.sh
#   --all            Fire all pipeline agents backgrounded (pm coder cm writer tester)
#   --bg             Background the agent process instead of running in foreground
#   --help, -h       Print this help and exit
#
# Valid agent names: pm, coder, cm, writer, tester, overwatch
# --all fires: pm, coder, cm, writer, tester  (overwatch excluded from burst)
#
# Backgrounded firings (--bg, --all) are detached into a session of their own
# with setsid, and each one's pid is printed and is its process-group id:
# `kill -- -PID` stops that agent's whole wake cycle. Without setsid on PATH
# the script cannot detach them, so it waits for every fired pid instead.

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lib/env_bootstrap.sh"

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
[[ -f "$KANBAN_ROOT/shell-env" ]] && source "$KANBAN_ROOT/shell-env"

# Source operator_args.sh for parsing and uniform --help.
# shellcheck source=lib/operator_args.sh
source "${SCRIPT_DIR}/lib/operator_args.sh"

# Source the dual-channel error-contract emitter.
# shellcheck source=lib/error_contract.sh
source "${SCRIPT_DIR}/lib/error_contract.sh"

# This script as installed under the resolved kanban root, shell-quoted, so
# a remedy that names it runs as printed from any directory.
_wake_now_cmd="$(printf '%q' "${KANBAN_ROOT}/scripts/wake-now.sh")"

VALID_AGENTS=(pm coder cm writer tester overwatch)
PIPELINE_AGENTS=(pm coder cm writer tester)

# Declared flag vocabulary: ALL flags this command accepts or consumes.
# agent, all, and bg are included so operator_args_validate_known rejects any
# unlisted flag.  --agent is the protected dispatch flag; --all and --bg are its
# sibling dispatch flags.  All must be declared before rejection is enabled.
OPERATOR_VALID_FLAGS=(agent all bg help)

# --all and --bg are outside the canonical vocabulary, so their help lines are
# given here (the header's Options block describes them the same way); --agent
# names this verb's own roles and what overwatch runs.
declare -A OPERATOR_FLAG_HELP=(
    [agent]="  --agent ROLE       Agent to fire: pm|coder|cm|writer|tester, or overwatch (runs overwatch-sweep.sh)"
    [all]="  --all              Fire all pipeline agents backgrounded (pm coder cm writer tester), each detached into its own session"
    [bg]="  --bg               Background the agent process, detached into its own session, instead of running in foreground"
)

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
# Value-taking: agent (dispatch flag).
# Boolean: all, bg, help.
operator_args_argparse \
    --value-flags "agent" \
    -- "$@"

# Emit error for value-taking flags given with no value.
if argparse_missing "agent"; then
    # BLOCK-WORTHY (docs/blocking-site-census.md): a bare --agent with no
    # value names no role to wake -- there is nothing this verb could fire
    # against, so it refuses rather than guessing a default agent.
    pgai_emit_error_contract \
        "wake_now.agent_value_required" \
        "wake-now.sh: --agent requires a value." \
        "Run ${_wake_now_cmd} --agent pm (or coder, cm, writer, tester, overwatch)."
    # CENSUS-ANCHOR: wake-now-agent-value-required-01
    exit 1
fi

# Handle --help / -h.
if argparse_has "help" || argparse_has "h"; then
    operator_args_render_help_for_flags "wake-now.sh" \
        "Fire one or all agents immediately (--sleep=0)." \
        OPERATOR_VALID_FLAGS
    exit 0
fi

# Reject unexpected positional arguments (hard-cut: no positional agent name).
if [[ "${#ARGPARSE_POSITIONAL[@]}" -gt 0 ]]; then
    # BLOCK-WORTHY (docs/blocking-site-census.md): this verb takes no
    # positional arguments -- a stray one is ambiguous input (a typo'd
    # flag, or a misremembered agent-as-positional form), and guessing its
    # intent risks firing the wrong agent or none at all.
    pgai_emit_error_contract \
        "wake_now.unexpected_positional_argument" \
        "wake-now.sh: unexpected positional argument: ${ARGPARSE_POSITIONAL[0]}." \
        "Run ${_wake_now_cmd} --agent pm, or ${_wake_now_cmd} --all, with no positional arguments."
    # CENSUS-ANCHOR: wake-now-unexpected-positional-01
    echo "" >&2
    echo "Usage: wake-now.sh --agent <role> [--bg]  OR  wake-now.sh --all" >&2
    exit 1
fi

# Reject any flag not in the declared vocabulary.
# agent, all, and bg are explicitly in OPERATOR_VALID_FLAGS (pre-flip safety:
# all dispatch flags verified present before rejection was enabled).
operator_args_validate_known "$(basename "$0")" OPERATOR_VALID_FLAGS || exit 1

# ---------------------------------------------------------------------------
# Extract values
# ---------------------------------------------------------------------------
BG=false
FIRE_ALL=false

if argparse_has "bg";  then BG=true; fi
if argparse_has "all"; then FIRE_ALL=true; fi

AGENT=""
if argparse_has "agent"; then
    AGENT="${ARGPARSE_FLAGS[agent]}"
fi

# ---------------------------------------------------------------------------
# Validate mutual exclusivity and required args
# ---------------------------------------------------------------------------
if [[ "$FIRE_ALL" == "true" && -n "$AGENT" ]]; then
    # BLOCK-WORTHY (docs/blocking-site-census.md): --all fires every
    # pipeline agent and --agent fires exactly one -- both at once is a
    # contradiction the caller must resolve, not a shape this verb can
    # satisfy by picking one silently.
    pgai_emit_error_contract \
        "wake_now.agent_all_mutually_exclusive" \
        "wake-now.sh: --all and --agent are mutually exclusive." \
        "Run ${_wake_now_cmd} --agent pm OR ${_wake_now_cmd} --all, not both."
    # CENSUS-ANCHOR: wake-now-agent-all-mutually-exclusive-01
    exit 1
fi

if [[ "$FIRE_ALL" == "false" && -z "$AGENT" ]]; then
    # BLOCK-WORTHY (docs/blocking-site-census.md): with neither --agent nor
    # --all supplied, this verb has no agent to fire -- there is nothing
    # to dispatch, so it refuses rather than defaulting to a guessed agent
    # or a silent no-op.
    pgai_emit_error_contract \
        "wake_now.agent_required" \
        "wake-now.sh: --agent is required (or use --all to fire all pipeline agents)." \
        "Run ${_wake_now_cmd} --agent pm, or ${_wake_now_cmd} --all to fire every pipeline agent."
    # CENSUS-ANCHOR: wake-now-agent-required-01
    echo "" >&2
    echo "Usage: wake-now.sh --agent <role> [--bg]  OR  wake-now.sh --all" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
is_valid_agent() {
    local name="$1"
    for a in "${VALID_AGENTS[@]}"; do
        [[ "$a" == "$name" ]] && return 0
    done
    return 1
}

# fire_command <agent> -- the command line one agent's firing runs. OVERWATCH
# has no model wake: its firing is the sweep, the same script the hourly cron
# line and the on-BLOCK nudge run.
fire_command() {
    if [[ "$1" == "overwatch" ]]; then
        FIRE_CMD=("$SCRIPT_DIR/overwatch-sweep.sh")
    else
        FIRE_CMD=("$SCRIPT_DIR/wake-batch.sh" --agent="$1" --sleep=0)
    fi
}

# Every backgrounded firing is detached explicitly into a session of its own
# (setsid), so it is no part of this script's process group or terminal and
# its pid is also its session and process-group id: `kill -- -PID` stops
# that agent's whole wake cycle. Its stdin is /dev/null; stdout and stderr
# stay this script's. Without setsid on PATH a firing cannot be detached, so
# this script stays in the foreground and waits for every recorded pid.
_WAKE_NOW_SETSID=true
if ! command -v setsid >/dev/null 2>&1; then
    _WAKE_NOW_SETSID=false
fi

# Pids of every agent fired in the background, in firing order.
FIRED_PIDS=()

# fire_one_bg <agent> -- start one agent's firing in the background and
# record its pid in FIRED_PIDS. Started from this non-interactive shell (no
# job control) the child is not a process-group leader, so setsid makes it
# the leader of a new session without forking: $! is the agent's own pid.
fire_one_bg() {
    local agent="$1"
    local pid
    fire_command "$agent"
    if [[ "$_WAKE_NOW_SETSID" == "true" ]]; then
        setsid "${FIRE_CMD[@]}" < /dev/null &
        pid=$!
        echo "  [${agent}] PID ${pid} (detached into its own session; stop it with: kill -- -${pid})"
    else
        "${FIRE_CMD[@]}" < /dev/null &
        pid=$!
        echo "  [${agent}] PID ${pid} (not detached: setsid is not on PATH)"
    fi
    FIRED_PIDS+=("$pid")
}

# finish_bg -- the end of every backgrounded run: report the recorded pids,
# and when the firings could not be detached, wait for each of them so none
# outlives this script unrecorded. Exits with the first non-zero status a
# waited firing returned, else 0.
finish_bg() {
    local pid rc status=0
    if [[ "$_WAKE_NOW_SETSID" == "true" ]]; then
        echo "Fired pids: ${FIRED_PIDS[*]} (each detached into its own session)."
        return 0
    fi
    echo "setsid is not on PATH, so the fired agents cannot be detached; waiting for pids ${FIRED_PIDS[*]} to finish (install util-linux to get setsid)." >&2
    for pid in "${FIRED_PIDS[@]}"; do
        rc=0
        wait "$pid" || rc=$?
        if (( rc != 0 && status == 0 )); then
            status=$rc
        fi
    done
    return "$status"
}

# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------
if [[ "$FIRE_ALL" == "true" ]]; then
    echo "Firing all pipeline agents (--sleep=0, backgrounded):"
    for a in "${PIPELINE_AGENTS[@]}"; do
        fire_one_bg "$a"
    done
    echo "Done. ${#PIPELINE_AGENTS[@]} agents started."
    finish_bg
else
    if ! is_valid_agent "$AGENT"; then
        # BLOCK-WORTHY (docs/blocking-site-census.md): an agent name outside
        # the declared vocabulary has no wake-batch.sh dispatch target --
        # firing it would either error one level down with a less specific
        # message or silently do nothing, so this refuses here instead.
        pgai_emit_error_contract \
            "wake_now.unknown_agent" \
            "wake-now.sh: unknown agent '${AGENT}'." \
            "Run ${_wake_now_cmd} --agent pm (valid agents: ${VALID_AGENTS[*]})."
        # CENSUS-ANCHOR: wake-now-unknown-agent-01
        exit 1
    fi
    if [[ "$BG" == "true" ]]; then
        fire_one_bg "$AGENT"
        finish_bg
    else
        fire_command "$AGENT"
        exec "${FIRE_CMD[@]}"
    fi
fi
