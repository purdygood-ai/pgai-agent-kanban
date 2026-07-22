#!/usr/bin/env bash
# docker/entrypoint.sh
# Container entrypoint for the pgai-agent-kanban image.
#
# Responsibilities:
#   1. Verify the four expected bind mounts exist; exit 1 naming each missing one.
#   2. Export PGAI_AGENT_KANBAN_ROOT_PATH to the kanban mount path.
#   3. Exec the configured role:
#        default (no args / "pseudocron") — pseudocron in foreground as PID 1
#        "shell"     — interactive bash
#        "dashboard" — tmux dashboard session
#
# Usage (docker run CMD / docker-compose command):
#   (no args)          — pseudocron mode (default)
#   pseudocron         — pseudocron mode (explicit)
#   shell              — interactive bash
#   dashboard          — tmux dashboard session
#   bash               — passthrough to bash (for --entrypoint override or debug)
#   -- <any>           — passthrough: exec the remaining tokens verbatim
#
# Environment exported to child processes:
#   PGAI_AGENT_KANBAN_ROOT_PATH=/pgai_agent_kanban
#   TERM (defaulted to xterm-256color if unset)
#
# Mount contract (all four must be present):
#   /pgai_agent_kanban   — kanban install volume (populated by host install.sh)
#   /home/<user>         — user workspace (dev trees, project repos)
#   /claude              — site-specific payload directory
#   ~/.claude            — agent CLI config directory (credentials; never baked in)
#
# The /home/<user> workspace check:
#   If PGAI_WORKSPACE_MOUNT is set and non-empty, that exact path must exist.
#   Otherwise, any /home/*/ directory that is NOT the runtime user's image-baked
#   home (/home/${USER:-kanban}) must exist.  The image-baked directory is excluded
#   because useradd --create-home creates it at build time; it is always present
#   even when no workspace bind-mount is provided, which would make the check
#   silently pass.  See BUG-0086 for the full analysis.
# The ~/.claude check resolves to /root/.claude or /home/<user>/.claude depending
# on the runtime USER; the entrypoint checks the resolved path.

set -euo pipefail

# ---------------------------------------------------------------------------
# Mount verification — fail loud naming each missing mount
# ---------------------------------------------------------------------------

_MISSING=()

# Mount 1: /pgai_agent_kanban — the kanban install
if [[ ! -d "/pgai_agent_kanban" ]]; then
    _MISSING+=("/pgai_agent_kanban")
fi

# Mount 2: /home/<user> — user workspace
# Two modes, controlled by PGAI_WORKSPACE_MOUNT:
#
#   PGAI_WORKSPACE_MOUNT set and non-empty:
#     The named path must exist.  This is the explicit, unambiguous form and
#     the recommended way to configure the workspace mount.  Set it in your
#     docker-compose.yaml environment section to the container-side path of
#     your workspace bind-mount (e.g. PGAI_WORKSPACE_MOUNT=/home/operator).
#
#   PGAI_WORKSPACE_MOUNT unset or empty (legacy / auto-detect):
#     Search /home/*/ for any directory that is NOT the runtime user's
#     image-baked home (/home/${USER:-kanban}).  The image-baked directory is
#     excluded because useradd --create-home always creates it, so its mere
#     presence does not confirm that a workspace bind-mount was provided.
_HOME_MOUNT_FOUND=false
if [[ -n "${PGAI_WORKSPACE_MOUNT:-}" ]]; then
    # Explicit path mode: verify the declared workspace path exists.
    if [[ -d "${PGAI_WORKSPACE_MOUNT}" ]]; then
        _HOME_MOUNT_FOUND=true
    fi
    _HOME_MOUNT_MISSING_LABEL="${PGAI_WORKSPACE_MOUNT}"
else
    # Auto-detect mode: any /home/*/ directory except the runtime user's
    # image-baked home satisfies the workspace-mount requirement.
    _baked_home="/home/${USER:-kanban}"
    for _candidate in /home/*/; do
        if [[ -d "${_candidate}" && "${_candidate%/}" != "${_baked_home}" ]]; then
            _HOME_MOUNT_FOUND=true
            break
        fi
    done
    unset _candidate _baked_home
    _HOME_MOUNT_MISSING_LABEL="/home/<user> (set PGAI_WORKSPACE_MOUNT to the container-side workspace path)"
fi
if [[ "${_HOME_MOUNT_FOUND}" != "true" ]]; then
    _MISSING+=("${_HOME_MOUNT_MISSING_LABEL}")
fi
unset _HOME_MOUNT_FOUND _HOME_MOUNT_MISSING_LABEL

# Mount 3: /claude — site-specific payload directory
if [[ ! -d "/claude" ]]; then
    _MISSING+=("/claude")
fi

# Mount 4: ~/.claude — agent CLI config directory (secrets/config; never baked in)
# Resolve the expected path from the runtime HOME variable.
_CLAUDE_CONFIG_DIR="${HOME:-/root}/.claude"
if [[ ! -d "${_CLAUDE_CONFIG_DIR}" ]]; then
    _MISSING+=("${_CLAUDE_CONFIG_DIR} (~/.claude agent CLI config)")
fi
unset _CLAUDE_CONFIG_DIR

if [[ ${#_MISSING[@]} -gt 0 ]]; then
    echo "ERROR: entrypoint.sh — required bind mount(s) not found:" >&2
    for _mount in "${_MISSING[@]}"; do
        echo "  MISSING: ${_mount}" >&2
    done
    echo "" >&2
    echo "Each of the four mounts must be present before the container starts:" >&2
    echo "  /pgai_agent_kanban   — kanban install volume" >&2
    echo "  /home/<user>         — user workspace (set PGAI_WORKSPACE_MOUNT env var to the" >&2
    echo "                         container-side path, e.g. PGAI_WORKSPACE_MOUNT=/home/operator)" >&2
    echo "  /claude              — site-specific payload directory" >&2
    echo "  ~/.claude            — agent CLI config (credentials)" >&2
    echo "" >&2
    echo "Example (docker-compose):" >&2
    echo "  environment:" >&2
    echo "    - PGAI_WORKSPACE_MOUNT=/home/operator" >&2
    echo "  volumes:" >&2
    echo "    - /path/to/kanban/install:/pgai_agent_kanban" >&2
    echo "    - /home/operator:/home/operator" >&2
    echo "    - /path/to/claude/payload:/claude" >&2
    echo "    - \${HOME}/.claude:/root/.claude" >&2
    exit 1
fi
unset _MISSING _mount

# ---------------------------------------------------------------------------
# Export canonical kanban root (no silent default — must be the mount path)
# ---------------------------------------------------------------------------
export PGAI_AGENT_KANBAN_ROOT_PATH="/pgai_agent_kanban"

# ---------------------------------------------------------------------------
# Default TERM to xterm-256color if unset or empty (tput fails with unknown TERM)
# ---------------------------------------------------------------------------
export TERM="${TERM:-xterm-256color}"

# ---------------------------------------------------------------------------
# Exec the configured role
# ---------------------------------------------------------------------------
_MODE="${1:-pseudocron}"

case "${_MODE}" in

    pseudocron|"")
        # Default: run pseudocron in the foreground as PID 1.
        # Logs go to stdout so they appear in `docker logs`.
        # pseudocron.py reads pseudocron.cfg and pseudocron.env from the
        # kanban root (already set via PGAI_AGENT_KANBAN_ROOT_PATH).
        echo "entrypoint: starting pseudocron (PID 1 mode)" >&2
        exec python3 "${PGAI_AGENT_KANBAN_ROOT_PATH}/scripts/pseudocron.py"
        ;;

    shell)
        # Interactive bash shell — operator diagnostic path.
        echo "entrypoint: starting interactive shell" >&2
        exec /bin/bash
        ;;

    dashboard)
        # tmux dashboard session — operator diagnostic path.
        # Launches the kanban dashboard via the standard create/attach script.
        echo "entrypoint: starting dashboard tmux session" >&2
        exec /bin/bash "${PGAI_AGENT_KANBAN_ROOT_PATH}/scripts/dashboard/create.sh"
        ;;

    --)
        # Explicit passthrough: exec remaining arguments verbatim.
        shift
        exec "$@"
        ;;

    *)
        # Unknown mode: passthrough — exec the remaining tokens as a command.
        # Allows `docker run ... /bin/bash -c "..."` without --entrypoint override.
        echo "entrypoint: passthrough exec: $*" >&2
        exec "$@"
        ;;

esac
