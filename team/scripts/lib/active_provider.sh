#!/usr/bin/env bash
# team/scripts/lib/active_provider.sh
# Active-provider read helper for the pgai-agent-kanban framework.
#
# Source this file to get read_active_provider in your shell:
#   source "$(dirname "${BASH_SOURCE[0]}")/active_provider.sh"
#
# The active provider is read from kanban.cfg [providers] active.
#
# Valid values: claude, codex, gemini
# Case-insensitive input; canonicalized to lowercase on output.
# Surrounding whitespace (spaces, tabs, newlines) is stripped before
# canonicalization and comparison.
#
# Default behaviour
# -----------------
# When kanban.cfg is missing, [providers] active is unset, or the value is
# unrecognized, read_active_provider returns 'claude' and logs a warning to
# stderr. It never exits non-zero; callers can always count on receiving a
# valid provider name.
#
# Performance
# -----------
# The function does a single INI read (~50-100ms on a modern VPS). Every
# wake firing on the inactive provider pays this cost once and then
# fast-exits. The 21-second cron stagger leaves ample headroom.
#
# Functions
# ---------
#   read_active_provider <kanban_root>
#       Echo the active provider name (claude|codex|gemini).
#       kanban_root: path to the kanban root directory (required).

# ---------------------------------------------------------------------------
# read_active_provider <kanban_root>
# Echo the active provider: 'claude' | 'codex' | 'gemini'.
# Defaults to 'claude' with a warning logged to stderr when kanban.cfg is
# missing or [providers] active is unset/invalid.
# ---------------------------------------------------------------------------
read_active_provider() {
    local kanban_root="${1:-}"
    if [[ -z "$kanban_root" ]]; then
        echo "active_provider.sh: read_active_provider: kanban_root argument is required" >&2
        echo "claude"
        return 0
    fi

    local cfg="${kanban_root}/kanban.cfg"
    if [[ ! -f "$cfg" ]]; then
        echo "active_provider.sh: kanban.cfg not found at '${cfg}'; defaulting to 'claude'" >&2
        echo "claude"
        return 0
    fi

    # Source ini_parser.sh if read_ini isn't already defined (defensive — most
    # callers already have it sourced via project_paths.sh).
    if ! command -v read_ini >/dev/null 2>&1; then
        local ini_parser
        ini_parser="$(dirname "${BASH_SOURCE[0]}")/ini_parser.sh"
        if [[ -f "$ini_parser" ]]; then
            # shellcheck source=/dev/null
            source "$ini_parser"
        else
            echo "active_provider.sh: ini_parser.sh not found at '${ini_parser}'; defaulting to 'claude'" >&2
            echo "claude"
            return 0
        fi
    fi

    local raw
    raw="$(read_ini "$cfg" providers active "")"
    # Strip whitespace and lowercase
    local value
    value=$(printf '%s' "$raw" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')

    case "$value" in
        claude|codex|gemini)
            echo "$value"
            ;;
        "")
            echo "active_provider.sh: kanban.cfg [providers] active is unset or empty; defaulting to 'claude'" >&2
            echo "claude"
            ;;
        *)
            echo "active_provider.sh: unrecognized provider '${value}' in kanban.cfg [providers] active; valid values: claude codex gemini; defaulting to 'claude'" >&2
            echo "claude"
            ;;
    esac
    return 0
}
