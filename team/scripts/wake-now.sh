#!/usr/bin/env bash
# team/scripts/wake-now.sh
# Convenience wrapper: fire one or all agents immediately (--sleep=0).
#
# Usage:
#   wake-now.sh --agent <role> [--bg]   Fire one named agent; --bg backgrounds it.
#   wake-now.sh --all                   Fire all pipeline agents (pm coder cm writer tester), backgrounded.
#   wake-now.sh --help                  Print this message.
#
# Options:
#   --agent ROLE     Agent role to wake: pm|coder|cm|writer|tester|overwatch (required unless --all)
#   --all            Fire all pipeline agents backgrounded (pm coder cm writer tester)
#   --bg             Background the agent process instead of running in foreground
#   --help, -h       Print this help and exit
#
# Valid agent names: pm, coder, cm, writer, tester, overwatch
# --all fires: pm, coder, cm, writer, tester  (overwatch excluded from burst)

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
[[ -f "$KANBAN_ROOT/shell-env" ]] && source "$KANBAN_ROOT/shell-env"

# Source operator_args.sh for parsing and uniform --help.
# shellcheck source=lib/operator_args.sh
source "${SCRIPT_DIR}/lib/operator_args.sh"

VALID_AGENTS=(pm coder cm writer tester overwatch)
PIPELINE_AGENTS=(pm coder cm writer tester)

# Declared flag vocabulary: ALL flags this command accepts or consumes.
# agent, all, and bg are included so operator_args_validate_known rejects any
# unlisted flag.  --agent is the protected dispatch flag; --all and --bg are its
# sibling dispatch flags.  All must be declared before rejection is enabled.
OPERATOR_VALID_FLAGS=(agent all bg help)

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
# Value-taking: agent (dispatch flag).
# Boolean: all, bg, help.
argparse_parse \
    --value-flags "agent" \
    -- "$@"

# Emit error for value-taking flags given with no value.
if argparse_missing "agent"; then
    echo "wake-now.sh: error: --agent requires a value" >&2
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
    echo "wake-now.sh: error: unexpected positional argument: ${ARGPARSE_POSITIONAL[0]}" >&2
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
    echo "wake-now.sh: error: --all and --agent are mutually exclusive" >&2
    exit 1
fi

if [[ "$FIRE_ALL" == "false" && -z "$AGENT" ]]; then
    echo "wake-now.sh: error: --agent is required (or use --all to fire all pipeline agents)" >&2
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

fire_one_bg() {
    local agent="$1"
    "$SCRIPT_DIR/wake-batch.sh" --agent="$agent" --sleep=0 &
    local pid=$!
    echo "  [${agent}] PID ${pid}"
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
else
    if ! is_valid_agent "$AGENT"; then
        echo "wake-now.sh: ERROR: unknown agent '${AGENT}'" >&2
        echo "  Valid agents: ${VALID_AGENTS[*]}" >&2
        exit 1
    fi
    if [[ "$BG" == "true" ]]; then
        fire_one_bg "$AGENT"
    else
        exec "$SCRIPT_DIR/wake-batch.sh" --agent="$AGENT" --sleep=0
    fi
fi
