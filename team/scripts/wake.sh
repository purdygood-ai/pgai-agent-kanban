#!/usr/bin/env bash
# team/scripts/wake.sh
# Single-task wake convenience wrapper.
#
# Delegates to wake-batch.sh with --max-tasks=1 prepended. Use for one-off
# operator invocations where you want exactly one task processed and then exit.
#
# For continuous / cron-driven multi-task processing, invoke wake-batch.sh
# directly with the operator's preferred --max-tasks=N value (or rely on
# the kanban.cfg [wake] max_tasks_per_wake default of 5).

case "${1:-}" in
    --help|-h)
        cat <<'EOF'
Usage: wake.sh --agent=AGENT [--max-tasks=N]
       wake.sh AGENT            # legacy positional form

Convenience wrapper around wake-batch.sh that sets --max-tasks=1,
processing exactly one task per invocation.  All arguments are forwarded
to wake-batch.sh after the --max-tasks=1 flag is prepended.

Arguments:
  --agent=AGENT        Agent role to wake: pm|coder|writer|tester|cm|po (required)
  AGENT                Legacy positional form (deprecated; emits a warning)
  --max-tasks=N        Override the default of 1 task per invocation
  --help, -h           Print this help and exit

Exit codes:
  0   Task processed successfully (or nothing to process).
  1   Configuration error or dispatched script failure.

Examples:
  wake.sh --agent=coder
  wake.sh coder              # deprecated positional form
  wake.sh --agent=pm --max-tasks=2
EOF
        exit 0
        ;;
esac

exec "$(dirname "${BASH_SOURCE[0]}")/wake-batch.sh" --max-tasks=1 "$@"
