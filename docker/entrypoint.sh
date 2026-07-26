#!/usr/bin/env bash
# docker/entrypoint.sh
# Minimal workspace entrypoint for the pgai-agent-kanban container.
#
# The run invocation is responsible for supplying $HOME (the user's home)
# and $PGAI_AGENT_KANBAN_ROOT_PATH (the kanban install path), whether via
# environment variables, bind mounts, or both. This script assumes both are
# already correct — it does not enforce mount contracts and does not pick a
# startup mode. It verifies only that the kanban root path exists, lands the
# shell there, and exits. Starting pseudocron and the dashboard is the
# operator's job, using the same commands as running the kanban on a host.
#
# Usage: no arguments. The container invokes this script directly; it always
# cd's into $PGAI_AGENT_KANBAN_ROOT_PATH and execs an interactive login bash.

set -euo pipefail

if [[ -z "${PGAI_AGENT_KANBAN_ROOT_PATH:-}" || ! -d "${PGAI_AGENT_KANBAN_ROOT_PATH}" ]]; then
    echo "ERROR: entrypoint.sh — PGAI_AGENT_KANBAN_ROOT_PATH is not set to an existing directory: '${PGAI_AGENT_KANBAN_ROOT_PATH:-}'" >&2
    exit 1
fi

cd "${PGAI_AGENT_KANBAN_ROOT_PATH}"
exec bash -l
