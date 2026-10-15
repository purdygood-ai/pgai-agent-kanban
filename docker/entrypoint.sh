#!/usr/bin/env bash
# docker/entrypoint.sh
# Minimal workspace entrypoint for the pgai-agent-kanban container.
#
# The launcher (team/scripts/container-shell.sh) promises this script three
# things before it ever starts the container: the kanban root is bind-mounted
# at its host path and exported as PGAI_AGENT_KANBAN_ROOT_PATH, the operator's
# home is bind-mounted at its host path and exported as HOME, and the
# directory presented as the kanban root is one install.sh has populated —
# never a raw repository checkout. This script enforces all three at startup
# and refuses loud, naming the unmet condition, the offending path, and a
# remedy the reader can run on the host, before it does anything else. It
# does not pick a startup mode: once the contract holds, it cd's into the
# kanban root and execs an interactive login bash. Starting pseudocron and
# the dashboard is the operator's job, using the same commands as running
# the kanban on a host.
#
# Usage: no arguments. The container invokes this script directly.

set -euo pipefail

# ---------------------------------------------------------------------------
# Condition 1: PGAI_AGENT_KANBAN_ROOT_PATH names an existing directory.
# ---------------------------------------------------------------------------
_KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-}"
if [[ -z "${_KANBAN_ROOT}" || ! -d "${_KANBAN_ROOT}" ]]; then
    echo "ERROR: entrypoint.sh — PGAI_AGENT_KANBAN_ROOT_PATH is not set to an existing directory: '${_KANBAN_ROOT}'" >&2
    echo "Remedy (on the host): export PGAI_AGENT_KANBAN_ROOT_PATH=<kanban-install-dir> before running team/scripts/container-shell.sh, or pass --kanban-root <kanban-install-dir>." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Condition 2: HOME names an existing directory.
#
# The launcher mounts the operator's real $HOME at a matching container path
# and exports HOME to match, so the login shell below sources the operator's
# real dotfiles and the provider CLI reads real credentials from ~/.claude.
# A missing or unmounted HOME leaves that login shell sourcing nothing.
# ---------------------------------------------------------------------------
_CONTAINER_HOME="${HOME:-}"
if [[ -z "${_CONTAINER_HOME}" || ! -d "${_CONTAINER_HOME}" ]]; then
    echo "ERROR: entrypoint.sh — HOME is not set to an existing directory: '${_CONTAINER_HOME}'" >&2
    echo "Remedy (on the host): run team/scripts/container-shell.sh, which mounts and exports your host \$HOME automatically; if invoking the container engine directly, pass -v \"\$HOME:\$HOME\" -e \"HOME=\$HOME\"." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Condition 3: the kanban root is an install.sh-populated install, not a raw
# repository checkout.
#
# The two layouts put helpers at different paths: install.sh copies
# team/scripts/ (source layout) to scripts/ (install layout) at the kanban
# root, so scripts/pseudocron.py exists only in an install. A repository
# checkout keeps its helpers at team/scripts/ instead and has no top-level
# scripts/ at all — mounted in an install's place, none of the runbook
# commands in docs/docker.md resolve.
# ---------------------------------------------------------------------------
if [[ ! -e "${_KANBAN_ROOT}/scripts/pseudocron.py" ]]; then
    if [[ -d "${_KANBAN_ROOT}/team/scripts" ]]; then
        echo "ERROR: entrypoint.sh — '${_KANBAN_ROOT}' looks like a raw repository checkout, not an install.sh-populated kanban install: helpers were found at team/scripts/ (checkout layout) instead of scripts/ (install layout, e.g. scripts/pseudocron.py)." >&2
    else
        echo "ERROR: entrypoint.sh — '${_KANBAN_ROOT}' is not an install.sh-populated kanban install: scripts/pseudocron.py is missing." >&2
    fi
    echo "Remedy (on the host, before mounting): PGAI_AGENT_KANBAN_ROOT_PATH=<install-dir> ./install.sh --no-system-cron — then mount <install-dir> instead of the checkout." >&2
    exit 1
fi

cd "${_KANBAN_ROOT}"
exec bash -l
