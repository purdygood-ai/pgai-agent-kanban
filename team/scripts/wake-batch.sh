#!/usr/bin/env bash
# team/scripts/wake-batch.sh
# Provider-agnostic wake dispatcher.
#
# Reads kanban.cfg [providers] active and execs the matching
# provider-specific wake script under wake/<provider>.sh.
#
# All arguments are passed through verbatim to the dispatched script.
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
# This script is intentionally minimal — see wake/<provider>.sh for
# the actual wake-loop logic and wake_common.sh for shared substrate.

case "${1:-}" in
    --help|-h)
        cat <<'EOF'
Usage: wake-batch.sh --agent=AGENT [--sleep=N] [--max-tasks=N]
       wake-batch.sh AGENT      # legacy positional form (deprecated)

Provider-agnostic wake dispatcher.  Reads kanban.cfg [providers] active
and execs the matching provider-specific wake script under wake/<provider>.sh.
All arguments are forwarded verbatim to the dispatched script.

Arguments:
  --agent=AGENT        Agent role to wake: pm|coder|writer|tester|cm|po (required)
  AGENT                Legacy positional form (deprecated; emits a warning)
  --sleep=N            Seconds to sleep before running (default: 0; use for cron stagger)
  --max-tasks=N        Maximum tasks to process per invocation (default: from kanban.cfg)
  --help, -h           Print this help and exit

Exit codes:
  0   Wake completed successfully (or nothing to process).
  1   Configuration error, provider script not found, or dispatched script failure.

Examples:
  wake-batch.sh --agent=pm
  wake-batch.sh --agent=coder --sleep=21 --max-tasks=5
  wake-batch.sh coder          # deprecated positional form

Cron examples:
  */2 * * * *    wake-batch.sh --agent=pm     --sleep=0
  */2 * * * *    wake-batch.sh --agent=cm     --sleep=21
  1-59/2 * * * * wake-batch.sh --agent=coder  --sleep=0
EOF
        exit 0
        ;;
esac

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# Resolve kanban root: canonical var with default install path fallback.
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"

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
