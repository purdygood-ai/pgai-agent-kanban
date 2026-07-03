#!/usr/bin/env bash
# team/scripts/wake.sh
# Single-task wake convenience wrapper.
#
# Delegates to wake-batch.sh with --max-tasks=1 prepended. Use for one-off
# operator invocations where you want exactly one task processed and then exit.
#
# Usage:
#   wake.sh --agent=pm
#   wake.sh coder              # legacy positional form
#
# For continuous / cron-driven multi-task processing, invoke wake-batch.sh
# directly with the operator's preferred --max-tasks=N value (or rely on
# the kanban.cfg [wake] max_tasks_per_wake default of 5).

exec "$(dirname "${BASH_SOURCE[0]}")/wake-batch.sh" --max-tasks=1 "$@"
