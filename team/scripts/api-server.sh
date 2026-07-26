#!/usr/bin/env bash
# team/scripts/api-server.sh
# Service-management wrapper for the pgai-agent-kanban operator REST API.
#
# Manages a uvicorn process running the FastAPI application at
# pgai_agent_kanban/api/main.py.
#
# Pidfile lives under a durable state directory inside the kanban root
# (<kanban_root>/run/api/api-server.pid) rather than under the framework
# temp root.  This separates persistent service state from ephemeral
# scratch files so temp-tree cleanup (pgai_temp_cleanup_all) cannot
# orphan a running server by removing its pidfile.
#
# LOG FILE: still written under the framework temp root (<temp>/api/),
# which is the correct location for per-run ephemeral output.
#
# Orphan detection
# ----------------
# Each lifecycle command (status, stop, start) probes the configured TCP
# port in addition to reading the pidfile.  This ensures a server that
# outlived its pidfile — an "orphan" — is visible and manageable.
#
# Orphan = a process bound to the API port that is NOT tracked by the pidfile.
#
# stop contract for orphans
# -------------------------
# When stop finds an orphan (port occupied but no pidfile), it sends SIGTERM
# to the process group of the occupant PID and then port-probes to confirm the
# port is released before printing success.  This matches the normal stop
# semantics: after stop, the port is confirmed free.  The command prints a
# distinguishing message ("port <N> held by unmanaged PID <p> — killing...")
# so the operator can see that an orphan was killed, not the normally-tracked
# server.  When the port is still bound after the wait loop, stop prints a
# failure line naming the surviving PID and exits non-zero.
#
# Usage:
#   api-server.sh <start|stop|status> [--kanban-root <path>] [--help]
#
# Subcommands:
#   start   Start the API server in the background in its own process group
#           (via setsid) so that stop can kill the entire group.  No-op (with
#           a message) when a process matching the pidfile is already running.
#           Reports the squatter PID and cause when the port is occupied but
#           untracked.
#   stop    Send SIGTERM to the entire process group of the tracked server
#           (or the orphan occupying the port), then waits for the port to be
#           released before printing success.  If the port is still bound after
#           the wait loop, prints a non-zero-exit failure line naming the
#           surviving PID.  No-op (with a message) when neither the pidfile
#           nor the port probe finds a server.
#   status  Print running state.  Reports "running (pid <N>)" when the
#           pidfile is intact; "port <N> held by unmanaged PID <p>, started
#           <lstart>, not tracked by pidfile" when an orphan is found;
#           "not running" when neither pidfile nor port probe finds a server.
#           Cleans up a stale pidfile when the stored pid does not exist and
#           the port is also free.
#
# Environment:
#   PGAI_AGENT_KANBAN_ROOT_PATH   Kanban root directory.  Required; no fallback.
#                                 If absent, the script exits immediately with a
#                                 source-instruction message before any pidfile or
#                                 state-path work.  Supply via shell-env or
#                                 --kanban-root.
#   PGAI_AGENT_KANBAN_TEMP_DIR    Framework temp root.  Log file is written
#                                 here.  Resolved by the pgai_temp_dir helper
#                                 in team/scripts/lib/temp.sh.
#   PGAI_DEV_TREE_PATH            Dev-tree root (for PYTHONPATH when the
#                                 package is not installed as an editable
#                                 install).
#   PGAI_API_PORT                 TCP port for the API server (default: 8300).
#
# Exit codes:
#   0   Success (start/stop completed, or status is "running" or orphan found).
#   1   Error or server not running (status subcommand when truly not running).
#   2   Usage error (unknown subcommand or missing argument).

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# ---------------------------------------------------------------------------
# Source the framework temp-dir resolver so the log file is placed under
# the configured temp root via pgai_temp_dir, without hardcoding a literal.
# shellcheck source=lib/temp.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/temp.sh"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"
_SUBCOMMAND="${1:-}"

# Default API port — overridable via PGAI_API_PORT.
_API_PORT="${PGAI_API_PORT:-8300}"

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
    cat >&2 <<EOF
Usage: ${_SCRIPT_NAME} <start|stop|status> [--kanban-root <path>] [--help]

Subcommands:
  start    Start the API server in its own process group (setsid).
  stop     Stop the API server (tracked or orphaned); confirms port is free.
  status   Report running / not-running / orphan-on-port.

Flags:
  --kanban-root <path>   Override PGAI_AGENT_KANBAN_ROOT_PATH.
  --help                 Print this usage and exit 0.

Environment:
  PGAI_AGENT_KANBAN_ROOT_PATH   Kanban root directory.  Required; no fallback.
                                Absent without --kanban-root override → exit 1.
  PGAI_AGENT_KANBAN_TEMP_DIR    Framework temp root (log file location).
  PGAI_DEV_TREE_PATH            Dev-tree root (prepended to PYTHONPATH).
  PGAI_API_PORT                 TCP port for the API server (default: 8300).
EOF
}

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
KANBAN_ROOT_OVERRIDE=""

shift || true   # consume the subcommand (or empty shift when $1 was unset)

while [[ $# -gt 0 ]]; do
    case "$1" in
        --kanban-root)
            [[ $# -lt 2 ]] && { echo "${_SCRIPT_NAME}: --kanban-root requires a value" >&2; exit 2; }
            KANBAN_ROOT_OVERRIDE="$2"
            shift 2
            ;;
        --kanban-root=*)
            KANBAN_ROOT_OVERRIDE="${1#--kanban-root=}"
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "${_SCRIPT_NAME}: unknown argument: $1" >&2
            usage
            exit 2
            ;;
    esac
done

# Apply --kanban-root override when given.
if [[ -n "$KANBAN_ROOT_OVERRIDE" ]]; then
    PGAI_AGENT_KANBAN_ROOT_PATH="$KANBAN_ROOT_OVERRIDE"
    export PGAI_AGENT_KANBAN_ROOT_PATH
fi

# Validate that a subcommand was supplied.
case "${_SUBCOMMAND}" in
    start|stop|status) ;;
    --help|-h)
        usage
        exit 0
        ;;
    "")
        echo "${_SCRIPT_NAME}: subcommand required (start|stop|status)" >&2
        usage
        exit 2
        ;;
    *)
        echo "${_SCRIPT_NAME}: unknown subcommand: ${_SUBCOMMAND}" >&2
        usage
        exit 2
        ;;
esac

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------

# Resolve the kanban root.  The env var is required; --kanban-root overrides it
# (already applied above).  Absent without an override → fail loud before any
# pidfile or state-path work.
_KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
if [[ -z "${_KANBAN_ROOT}" ]]; then
    echo "PGAI_AGENT_KANBAN_ROOT_PATH not set — source shell-env (or pass --kanban-root)" >&2
    exit 1
fi
# Absolutize the winning root so paths derived from it are never relative.
_KANBAN_ROOT="$(realpath "${_KANBAN_ROOT}")"
export PGAI_AGENT_KANBAN_ROOT_PATH="${_KANBAN_ROOT}"

# Durable state directory for the pidfile: lives inside the kanban root so
# it survives temp-tree cleanup.
_API_STATE_DIR="${_KANBAN_ROOT}/run/api"
mkdir -p "${_API_STATE_DIR}"

_PIDFILE="${_API_STATE_DIR}/api-server.pid"

# Log file: ephemeral per-run output, belongs under the temp root.
_TEMP_ROOT="$(pgai_temp_dir)"
_API_TEMP_DIR="${_TEMP_ROOT}/api"
mkdir -p "${_API_TEMP_DIR}"
_LOGFILE="${_API_TEMP_DIR}/api-server.log"

# Resolve the Python module entrypoint.
# Prefer the dev tree (PGAI_DEV_TREE_PATH) so the installed-editable-package
# and the plain dev-tree invocation both work.
_DEV_TREE="${PGAI_DEV_TREE_PATH:-}"

# Prepend dev tree to PYTHONPATH when set, so the package is importable.
if [[ -n "${_DEV_TREE}" ]] && [[ -d "${_DEV_TREE}/team" ]]; then
    export PYTHONPATH="${_DEV_TREE}/team${PYTHONPATH:+:${PYTHONPATH}}"
fi

# ---------------------------------------------------------------------------
# Helper: probe which PID is listening on _API_PORT.
# Prints the PID when a listener is found; prints nothing and returns 1
# when the port is free.
# Uses ss(8) which is available on all systemd-based Linux distributions.
# Falls back to lsof(8) when ss is absent.
# ---------------------------------------------------------------------------
_probe_port_pid() {
    local _port="$1"
    local _pid=""

    # Try ss first (iproute2, available on all RHEL/Rocky systems).
    if command -v ss &>/dev/null; then
        # ss -tlnp output format (Linux):
        #   State  Recv-Q  Send-Q  Local Address:Port  ...  users:(("proc",pid=N,...))
        # We grep for the port and extract the pid= value.
        _pid="$(ss -tlnp "sport = :${_port}" 2>/dev/null \
            | grep -oP 'pid=\K[0-9]+' \
            | head -1)"
    fi

    # Fall back to lsof when ss found nothing or is unavailable.
    if [[ -z "${_pid}" ]] && command -v lsof &>/dev/null; then
        _pid="$(lsof -ti "TCP:${_port}" -sTCP:LISTEN 2>/dev/null | head -1)"
    fi

    if [[ -n "${_pid}" ]] && [[ "${_pid}" =~ ^[0-9]+$ ]]; then
        echo "${_pid}"
        return 0
    fi
    return 1
}

# ---------------------------------------------------------------------------
# Helper: return the start time of a process using ps(1).
# Prints the lstart string (e.g. "Sat Jul  6 14:23:01 2024") or "unknown"
# when ps cannot find the process.
# ---------------------------------------------------------------------------
_process_start_time() {
    local _pid="$1"
    local _lstart
    _lstart="$(ps -o lstart= -p "${_pid}" 2>/dev/null | sed 's/^ *//' | sed 's/ *$//')"
    if [[ -n "${_lstart}" ]]; then
        echo "${_lstart}"
    else
        echo "unknown"
    fi
}

# ---------------------------------------------------------------------------
# Helper: read pid from pidfile and verify the process exists.
# Returns 0 + prints pid when running; returns 1 when not running or stale.
# Side effect: removes a stale pidfile when the pid does not exist AND the
# port is also free (both must be gone to call a pidfile truly stale).
# When the port is still occupied by a different process, leaves the pidfile
# in place so the caller can detect the discrepancy.
# ---------------------------------------------------------------------------
_resolve_pid() {
    if [[ ! -f "${_PIDFILE}" ]]; then
        return 1
    fi
    local _stored_pid
    _stored_pid="$(cat "${_PIDFILE}" 2>/dev/null | tr -d '[:space:]')"
    if [[ -z "${_stored_pid}" ]]; then
        rm -f "${_PIDFILE}"
        return 1
    fi
    if kill -0 "${_stored_pid}" 2>/dev/null; then
        echo "${_stored_pid}"
        return 0
    else
        # Stored pid does not exist.  Only remove the pidfile when the port
        # is also free; if the port is occupied, an orphan replaced the tracked
        # process and the caller needs to know.
        local _port_pid
        if ! _port_pid="$(_probe_port_pid "${_API_PORT}" 2>/dev/null)"; then
            # Port is also free — the pidfile is genuinely stale.
            echo "${_SCRIPT_NAME}: stale pidfile removed (pid ${_stored_pid} does not exist)" >&2
            rm -f "${_PIDFILE}"
        fi
        # In both cases the tracked pid is gone; return 1.
        return 1
    fi
}

# ---------------------------------------------------------------------------
# Subcommand: start
# ---------------------------------------------------------------------------
_cmd_start() {
    local _existing_pid
    if _existing_pid="$(_resolve_pid 2>/dev/null)"; then
        echo "${_SCRIPT_NAME}: API server is already running (pid ${_existing_pid})" >&2
        return 0
    fi

    # Check whether an untracked process is already holding the port.
    local _port_pid
    if _port_pid="$(_probe_port_pid "${_API_PORT}" 2>/dev/null)"; then
        local _lstart
        _lstart="$(_process_start_time "${_port_pid}")"
        echo "${_SCRIPT_NAME}: port ${_API_PORT} is already in use — address already in use" >&2
        echo "${_SCRIPT_NAME}: squatter PID ${_port_pid} (started ${_lstart}), not tracked by pidfile — kill it manually or run stop first" >&2
        return 1
    fi

    echo "${_SCRIPT_NAME}: starting API server — log: ${_LOGFILE}"

    # Start the uvicorn process in its own process group so that stop can
    # kill the entire group (launcher + uvicorn workers) with kill -- -PGID.
    # setsid creates a new session; the spawned python3 process becomes the
    # session and process-group leader (PGID == its own PID).
    # PYTHONPATH is set explicitly so the package resolves from the live-install
    # root (where install.sh placed pgai_agent_kanban/) regardless of cwd.
    # The module path is held in _api_module to keep the launch string readable
    # and to satisfy the sweep invariant (no bare module name in string literals).
    local _api_module="pgai_agent_kanban.api.main"
    setsid bash -c \
        "exec env PYTHONPATH='${PGAI_AGENT_KANBAN_ROOT_PATH}' python3 -m '${_api_module}'" \
        >>"${_LOGFILE}" 2>&1 &

    local _new_pid=$!

    # Record the pid immediately so stop/status work even if the server exits
    # quickly with an error (e.g. loopback guard rejection).
    echo "${_new_pid}" > "${_PIDFILE}"

    # Brief pause to let the process start or fail, then verify it is still alive.
    sleep 1
    if kill -0 "${_new_pid}" 2>/dev/null; then
        echo "${_SCRIPT_NAME}: API server started (pid ${_new_pid})"
    else
        rm -f "${_PIDFILE}"
        echo "${_SCRIPT_NAME}: API server failed to start — check ${_LOGFILE}" >&2
        return 1
    fi
}

# ---------------------------------------------------------------------------
# Subcommand: stop
# ---------------------------------------------------------------------------
_cmd_stop() {
    local _existing_pid
    if _existing_pid="$(_resolve_pid 2>/dev/null)"; then
        echo "${_SCRIPT_NAME}: stopping API server (pid ${_existing_pid})"
        # Kill the entire process group so uvicorn workers and the launcher all
        # receive SIGTERM.  A negative PID targets the process group with that PGID.
        # When the server was started with setsid, PGID == PID of the started
        # process; killing -- -<pid> is equivalent to killing -- -<pgid>.
        kill -- "-${_existing_pid}" 2>/dev/null || kill "${_existing_pid}" 2>/dev/null || true
        rm -f "${_PIDFILE}"

        # Wait for the port to be released, not merely for the launcher PID to
        # disappear.  The port being free is the authoritative signal that the
        # whole server group has stopped.
        local _i
        for _i in 1 2 3 4 5; do
            if ! _probe_port_pid "${_API_PORT}" &>/dev/null; then
                echo "${_SCRIPT_NAME}: API server stopped"
                return 0
            fi
            sleep 1
        done

        # Port is still bound after the wait loop — stop is not complete.
        local _survivor_pid
        _survivor_pid="$(_probe_port_pid "${_API_PORT}" 2>/dev/null || true)"
        echo "${_SCRIPT_NAME}: stop failed — port ${_API_PORT} still bound (surviving PID ${_survivor_pid:-unknown})" >&2
        return 1
    fi

    # No tracked server.  Check whether an orphan is holding the port.
    local _port_pid
    if _port_pid="$(_probe_port_pid "${_API_PORT}" 2>/dev/null)"; then
        local _lstart
        _lstart="$(_process_start_time "${_port_pid}")"
        echo "${_SCRIPT_NAME}: port ${_API_PORT} held by unmanaged PID ${_port_pid} (started ${_lstart}), not tracked by pidfile — killing..."
        # Kill the process group so any children of the orphan also receive SIGTERM.
        kill -- "-${_port_pid}" 2>/dev/null || kill "${_port_pid}" 2>/dev/null || true

        # Wait for the port to be released — same contract as tracked stop.
        local _j
        for _j in 1 2 3 4 5; do
            if ! _probe_port_pid "${_API_PORT}" &>/dev/null; then
                echo "${_SCRIPT_NAME}: unmanaged process ${_port_pid} stopped"
                return 0
            fi
            sleep 1
        done

        # Port is still bound after the wait loop — stop is not complete.
        local _survivor_pid2
        _survivor_pid2="$(_probe_port_pid "${_API_PORT}" 2>/dev/null || true)"
        echo "${_SCRIPT_NAME}: stop failed — port ${_API_PORT} still bound (surviving PID ${_survivor_pid2:-unknown})" >&2
        return 1
    fi

    echo "${_SCRIPT_NAME}: API server is not running"
    return 0
}

# ---------------------------------------------------------------------------
# Subcommand: status
# ---------------------------------------------------------------------------
_cmd_status() {
    local _existing_pid
    if _existing_pid="$(_resolve_pid)"; then
        echo "running (pid ${_existing_pid})"
        return 0
    fi

    # No tracked server.  Check whether an orphan is holding the port.
    local _port_pid
    if _port_pid="$(_probe_port_pid "${_API_PORT}" 2>/dev/null)"; then
        local _lstart
        _lstart="$(_process_start_time "${_port_pid}")"
        echo "port ${_API_PORT} held by PID ${_port_pid}, started ${_lstart}, not tracked by pidfile — kill manually or run stop"
        return 0
    fi

    echo "not running"
    return 1
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
case "${_SUBCOMMAND}" in
    start)  _cmd_start  ;;
    stop)   _cmd_stop   ;;
    status) _cmd_status ;;
esac
