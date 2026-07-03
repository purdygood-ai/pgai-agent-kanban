#!/usr/bin/env bash
# team/scripts/wake-batch.sh
# Provider-agnostic wake dispatcher.
#
# Reads kanban.cfg [providers] active and execs the matching
# provider-specific wake script under wake/<provider>.sh.
#
# Usage:
#   wake-batch.sh --agent=NAME [--sleep=N] [--max-tasks=N]
#   wake-batch.sh AGENT_NAME                # legacy positional form
#
# All arguments are passed through verbatim to the dispatched script.
#
# The active provider is read once per firing from kanban.cfg
# [providers] active (via lib/active_provider.sh::read_active_provider).
# Defaults to 'claude' if unset or invalid.
#
# Cron usage:
#   */2 * * * * $KANBAN_ROOT/scripts/wake-batch.sh --agent=pm --sleep=0
#
# To switch providers without editing crontab:
#   scripts/switch-provider.sh codex
# (Updates kanban.cfg [providers] active; the next cron firing
# dispatches to wake/codex.sh.)
#
# This script is intentionally minimal — see wake/<provider>.sh for
# the actual wake-loop logic and wake_common.sh for shared substrate.

set -euo pipefail

# Resolve kanban root: canonical var with default install path fallback.
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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
    exit 1
fi

exec "$WAKE_SCRIPT" "$@"
