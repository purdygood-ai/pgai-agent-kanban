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
# Python package resolution
# -------------------------
# The API package is resolved from the tree THIS script lives in, never from
# a repository checkout named by the environment: the package root is the
# parent of this script's own directory — <kanban_root> in the live install
# (where install.sh places pgai_agent_kanban/), <checkout>/team when the
# script runs from a dev tree.  start exports that root as the FIRST entry
# of PYTHONPATH and keeps every entry the caller already had after it, then
# launches python3 with that exported PYTHONPATH unchanged.  PGAI_DEV_TREE_PATH
# is not read: a live install must never import the package from a checkout,
# and a checkout finds its own package the same way.
#
# Port resolution
# ---------------
# The script and the server it launches take the API port from one source,
# with this precedence (highest first):
#   1. PGAI_API_PORT, when set to an integer (blank reads as unset; a
#      non-integer value is ignored with a warning on stderr).
#   2. kanban.cfg [api] port under the kanban root.
#   3. 8300.
# The precedence is implemented once, in load_api_config
# (pgai_agent_kanban/api/config.py, resolved from this script's own package
# root).  Every subcommand asks that function for the port before probing,
# and start hands the resolved value to the server as PGAI_API_PORT, so
# status and stop always probe the port the server actually bound.  When the
# port cannot be resolved (python3 or the package unusable) the script
# refuses dual-channel with exit 1 rather than probe a guessed port.
#
# Orphan detection
# ----------------
# Each lifecycle command (status, stop, start) probes the configured TCP
# port in addition to reading the pidfile.  This ensures a server that
# outlived its pidfile — an "orphan" — is visible and manageable.
#
# Orphan = a process bound to the API port that is NOT tracked by the pidfile.
#
# Target verification before any signal
# -------------------------------------
# stop never signals a pid it has not verified.  Whether the pid came from
# the pidfile or from the port probe, stop reads the process's command line
# (ps -o args=) and requires it to name the API entrypoint module
# (pgai_agent_kanban.api.main — what start launches).  A pid whose command
# line does not name it is refused dual-channel (error / message / remedy on
# stderr, exit 1) and nothing is signalled, so an unrelated listener on the
# API port survives a stop.
#
# A pidfile whose pid is alive but is NOT the API server (the pid was reused
# by another process after the server died) is treated exactly like a
# pidfile whose pid is dead: status reports "not running", stop signals
# nothing, and the stale pidfile is removed once the port is also free.
#
# stop contract for orphans
# -------------------------
# When stop finds an orphan (port occupied but no pidfile) whose command
# line names the API entrypoint, it sends SIGTERM to the process group of
# the occupant PID and then port-probes to confirm the port is released
# before printing success.  This matches the normal stop semantics: after
# stop, the port is confirmed free.  The command prints a distinguishing
# message ("port <N> held by unmanaged PID <p> ... not tracked by pidfile")
# so the operator can see that an orphan was stopped, not the normally-
# tracked server.  When the port is still bound after the wait loop, stop
# prints a failure line naming the surviving PID and exits non-zero.
#
# Usage:
#   api-server.sh <start|stop|status> [--kanban-root <path>] [--help]
#
# Subcommands:
#   start   Start the API server in the background in its own process group
#           (via setsid) so that stop can kill the entire group.  No-op (with
#           a message) when a process matching the pidfile is the running API
#           server.  Reports the squatter PID and cause when the port is
#           occupied but untracked.
#   stop    Verify that the tracked server (or the orphan occupying the port)
#           is the API server by its command line, then send SIGTERM to its
#           entire process group and wait for the port to be released before
#           printing success.  Refuses dual-channel and exits 1, signalling
#           nothing, when the port holder is not the API server.  If the
#           port is still bound after the wait loop, prints a non-zero-exit
#           failure line naming the surviving PID.  No-op (with a message)
#           when neither the pidfile nor the port probe finds a server.
#   status  Print running state.  Reports "running (pid <N>)" when the
#           pidfile names a live API server; "port <N> held by PID <p>,
#           started <lstart>, not tracked by pidfile" when an orphan is
#           found (saying so when that occupant is not the API server);
#           "not running" when neither pidfile nor port probe finds a
#           server — a pidfile whose pid is dead or reused by a non-API
#           process counts as not running.  Cleans up a stale pidfile when
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
#   PYTHONPATH                    Preserved: the launched server sees this
#                                 script's own package root first, then every
#                                 entry the caller had (see "Python package
#                                 resolution" above).
#   PGAI_API_PORT                 TCP port override.  When set to an integer it
#                                 wins over kanban.cfg [api] port for both this
#                                 script and the server it starts; unset, the
#                                 port is kanban.cfg [api] port, else 8300.
#                                 See "Port resolution" above.
#
# Exit codes:
#   0   Success (start/stop completed, or status is "running" or orphan found).
#   1   Error, stop refused (port holder is not the API server), API port
#       could not be resolved, or server not running (status subcommand when
#       truly not running).
#   2   Usage error (unknown subcommand or missing argument).

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# ---------------------------------------------------------------------------
# Source the framework temp-dir resolver so the log file is placed under
# the configured temp root via pgai_temp_dir, without hardcoding a literal.
# shellcheck source=lib/temp.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/temp.sh"
# shellcheck source=lib/script_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/script_common.sh"
# shellcheck source=lib/error_contract.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/error_contract.sh"
# pp_run_ops: the cwd-independent bridge to pgai_agent_kanban modules, used
# for the port lookup below.  Sourced by absolute path so the helper can
# locate its own tree whatever the caller's working directory; the port
# refusal's remedy names this same path.
_PP_RUN_OPS_SH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/pp_run_ops.sh"
# shellcheck source=lib/pp_run_ops.sh
source "${_PP_RUN_OPS_SH}"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"
_SUBCOMMAND="${1:-}"

# The Python module start launches (python3 -m <module>).  It is also the
# identity every stop target must carry on its command line before it is
# signalled: a process whose command line does not name this module is not
# the API server, whatever pid the pidfile or the port probe handed us.
_API_ENTRYPOINT="pgai_agent_kanban.api.main"

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
# Usage text as data (per lib/script_common.sh's documented convention).
# This script's usage intentionally prints to stderr on every path (--help's
# exit 0 included), so the destination is pinned here rather than delegated
# to print_usage_and_exit (which routes exit 0 to stdout).
_USAGE_TEXT="$(cat <<EOT
Usage: ${_SCRIPT_NAME} <start|stop|status> [--kanban-root <path>] [--help]

Subcommands:
  start    Start the API server in its own process group (setsid).
  stop     Stop the API server (tracked or orphaned) after verifying the
           target's command line names ${_API_ENTRYPOINT}; refuses an
           unrelated port holder (exit 1, nothing signalled); confirms the
           port is free.
  status   Report running / not-running / orphan-on-port.  A pidfile whose
           pid is dead or reused by a non-API process reads as not running.

Flags:
  --kanban-root <path>   Override PGAI_AGENT_KANBAN_ROOT_PATH.
  --help                 Print this usage and exit 0.

Environment:
  PGAI_AGENT_KANBAN_ROOT_PATH   Kanban root directory.  Required; no fallback.
                                Absent without --kanban-root override → exit 1.
  PGAI_AGENT_KANBAN_TEMP_DIR    Framework temp root (log file location).
  PYTHONPATH                    Preserved; this script's own package root
                                (parent of its scripts/ directory) is put first.
                                PGAI_DEV_TREE_PATH is not read.
  PGAI_API_PORT                 TCP port override; wins over kanban.cfg when set.

Port precedence (the script and the server it starts agree):
  PGAI_API_PORT if set, else kanban.cfg [api] port, else 8300.
EOT
)"

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
            printf '%s\n' "$_USAGE_TEXT" >&2
            exit 0
            ;;
        *)
            echo "${_SCRIPT_NAME}: unknown argument: $1" >&2
            printf '%s\n' "$_USAGE_TEXT" >&2
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
        printf '%s\n' "$_USAGE_TEXT" >&2
        exit 0
        ;;
    "")
        echo "${_SCRIPT_NAME}: subcommand required (start|stop|status)" >&2
        printf '%s\n' "$_USAGE_TEXT" >&2
        exit 2
        ;;
    *)
        echo "${_SCRIPT_NAME}: unknown subcommand: ${_SUBCOMMAND}" >&2
        printf '%s\n' "$_USAGE_TEXT" >&2
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

# Resolve the Python package root from this script's own location: the parent
# of the directory this file lives in — <kanban_root> in the live install,
# <checkout>/team in a dev tree.  Each tree finds its own package; no tree
# resolves the other's.  The root goes FIRST on PYTHONPATH so it wins over
# any entry the caller already had, and those entries are kept after it.
# start launches python3 with this exported PYTHONPATH as-is.
_PACKAGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="${_PACKAGE_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

# ---------------------------------------------------------------------------
# Resolve the API port through the server's own configuration loader
# (load_api_config), run through pp_run_ops, so the port every subcommand
# probes is the port the server binds: PGAI_API_PORT if set, else kanban.cfg
# [api] port, else 8300.  pp_run_ops keeps the caller's PYTHONPATH entries
# first, so the package root exported above still wins; its own-tree root
# is this same directory.  The loader reads the same environment this
# script runs with, including the kanban root resolved above.
# ---------------------------------------------------------------------------
if ! _API_PORT="$(pp_run_ops pgai_agent_kanban.api.config port)" || [[ ! "${_API_PORT}" =~ ^[0-9]+$ ]]; then
    # BLOCK-WORTHY (docs/blocking-site-census.md): without the port the
    # server binds, status and stop would probe a guessed port — reporting a
    # live server as not running, or stopping another kanban's API server.
    pgai_emit_error_contract \
        "api-server.port.unresolved" \
        "${_SCRIPT_NAME}: could not resolve the API port from load_api_config under ${_PACKAGE_ROOT} (got: '${_API_PORT:-}'). Nothing was probed or signalled." \
        "Run the resolver to see its error: PGAI_AGENT_KANBAN_ROOT_PATH=${_KANBAN_ROOT} PYTHONPATH=${_PACKAGE_ROOT} bash ${_PP_RUN_OPS_SH} pgai_agent_kanban.api.config port"
    # CENSUS-ANCHOR: api-server-port-unresolved-01
    exit 1
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
# Helper: print the full command line of a process (ps -o args=, unlimited
# width).  Prints "unknown" and returns 1 when ps cannot find the process.
# ---------------------------------------------------------------------------
_process_cmdline() {
    local _pid="$1"
    local _args
    _args="$(ps -ww -o args= -p "${_pid}" 2>/dev/null | sed 's/^ *//' | sed 's/ *$//')"
    if [[ -n "${_args}" ]]; then
        echo "${_args}"
        return 0
    fi
    echo "unknown"
    return 1
}

# ---------------------------------------------------------------------------
# Helper: is <pid> the API server?  True when its command line names the
# entrypoint module start launches (_API_ENTRYPOINT) as a whole word.
# A pid ps cannot see is not the API server.
# ---------------------------------------------------------------------------
_pid_is_api_server() {
    local _pid="$1"
    local _args
    _args="$(_process_cmdline "${_pid}")" || return 1
    grep -Fqw -- "${_API_ENTRYPOINT}" <<<"${_args}"
}

# ---------------------------------------------------------------------------
# Helper: read pid from pidfile and verify it is the running API server.
# Returns 0 + prints pid when running; returns 1 when not running or stale.
# A stored pid that is dead, or alive but not the API server (reused pid),
# is stale.  Side effect: removes a stale pidfile when the port is also free
# (both must be gone to call a pidfile truly stale).  When the port is still
# occupied by a different process, leaves the pidfile in place so the caller
# can detect the discrepancy.
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
    local _stale_reason
    if ! kill -0 "${_stored_pid}" 2>/dev/null; then
        _stale_reason="pid ${_stored_pid} does not exist"
    elif ! _pid_is_api_server "${_stored_pid}"; then
        _stale_reason="pid ${_stored_pid} is alive but is not the API server: $(_process_cmdline "${_stored_pid}" || true)"
    else
        echo "${_stored_pid}"
        return 0
    fi

    # The tracked pid is gone or reused.  Only remove the pidfile when the
    # port is also free; if the port is occupied, an orphan replaced the
    # tracked process and the caller needs to know.
    local _port_pid
    if ! _port_pid="$(_probe_port_pid "${_API_PORT}" 2>/dev/null)"; then
        # Port is also free — the pidfile is genuinely stale.
        echo "${_SCRIPT_NAME}: stale pidfile removed (${_stale_reason})" >&2
        rm -f "${_PIDFILE}"
    else
        echo "${_SCRIPT_NAME}: stale pidfile ignored (${_stale_reason})" >&2
    fi
    # In every stale case the tracked pid is not the server; return 1.
    return 1
}

# ---------------------------------------------------------------------------
# Helper: send SIGTERM to the process group of <pid> — but only after its
# command line has been verified to name the API entrypoint.  Both stop
# branches (tracked pid, port occupant) signal through here, so this is the
# one place a pid can be refused.  Returns 1 without signalling anything when
# <pid> is not the API server.
#
# A negative PID targets the process group with that PGID.  When the server
# was started with setsid, PGID == PID of the started process, so uvicorn
# workers and the launcher all receive SIGTERM.
# ---------------------------------------------------------------------------
_signal_api_server_group() {
    local _pid="$1"
    if ! _pid_is_api_server "${_pid}"; then
        local _args
        _args="$(_process_cmdline "${_pid}" || true)"
        # BLOCK-WORTHY (docs/blocking-site-census.md): signalling a process
        # group that is not the API server is a destructive action against
        # something this script does not own — a reused pid or an unrelated
        # listener on the API port.  Refusing here is what keeps stop from
        # killing it.
        pgai_emit_error_contract \
            "api-server.stop.target_not_api_server" \
            "${_SCRIPT_NAME}: stop refused — pid ${_pid} on port ${_API_PORT} is not the API server (its command line does not name ${_API_ENTRYPOINT}; it is: ${_args}). Nothing was signalled." \
            "Inspect it with: ps -ww -o pid=,ppid=,user=,args= -p ${_pid}   — stop it by hand only if it is yours (kill ${_pid}), or run the API on another port: PGAI_API_PORT=8301 ${_SCRIPT_NAME} start"
        # CENSUS-ANCHOR: api-server-stop-target-not-api-01
        return 1
    fi
    kill -- "-${_pid}" 2>/dev/null || kill "${_pid}" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# Helper: wait for the API port to be released, up to five seconds.
# Returns 0 as soon as the port is free; prints the stop-failure line naming
# the surviving PID and returns 1 when it is still bound afterwards.  The
# port being free is the authoritative signal that the whole server group
# has stopped — not merely that the signalled PID disappeared.
# ---------------------------------------------------------------------------
_wait_for_port_release() {
    local _i
    for _i in 1 2 3 4 5; do
        if ! _probe_port_pid "${_API_PORT}" &>/dev/null; then
            return 0
        fi
        sleep 1
    done
    local _survivor_pid
    _survivor_pid="$(_probe_port_pid "${_API_PORT}" 2>/dev/null || true)"
    echo "${_SCRIPT_NAME}: stop failed — port ${_API_PORT} still bound (surviving PID ${_survivor_pid:-unknown})" >&2
    return 1
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
    # The process inherits the PYTHONPATH exported under "Resolve paths":
    # this script's own package root first, the caller's entries after it.
    # Nothing here overrides it.  PGAI_API_PORT is set to the port resolved
    # above so the server binds exactly the port status and stop probe, even
    # if kanban.cfg is edited while the server runs.
    # PGAI_AGENT_KANBAN_TEMP_DIR carries the temp root resolved above
    # (pgai_temp_dir: the env var, else kanban.cfg [paths]), so every route
    # reads and writes under the same root this script logs to.
    PGAI_API_PORT="${_API_PORT}" PGAI_AGENT_KANBAN_TEMP_DIR="${_TEMP_ROOT}" \
        setsid python3 -m "${_API_ENTRYPOINT}" >>"${_LOGFILE}" 2>&1 &

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
        # Verified-then-signalled: the helper re-reads the command line and
        # refuses (exit 1, pidfile untouched) if the pid is not the server.
        _signal_api_server_group "${_existing_pid}" || return 1
        rm -f "${_PIDFILE}"

        if _wait_for_port_release; then
            echo "${_SCRIPT_NAME}: API server stopped"
            return 0
        fi
        return 1
    fi

    # No tracked server.  Check whether an orphan is holding the port.
    local _port_pid
    if _port_pid="$(_probe_port_pid "${_API_PORT}" 2>/dev/null)"; then
        local _lstart
        _lstart="$(_process_start_time "${_port_pid}")"
        echo "${_SCRIPT_NAME}: port ${_API_PORT} held by unmanaged PID ${_port_pid} (started ${_lstart}), not tracked by pidfile"
        # Same verified-then-signalled path as the tracked branch: an
        # unrelated listener on the API port is refused, not killed.
        _signal_api_server_group "${_port_pid}" || return 1
        echo "${_SCRIPT_NAME}: sent SIGTERM to unmanaged API server process group ${_port_pid}"

        if _wait_for_port_release; then
            echo "${_SCRIPT_NAME}: unmanaged process ${_port_pid} stopped"
            return 0
        fi
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
        if _pid_is_api_server "${_port_pid}"; then
            echo "port ${_API_PORT} held by PID ${_port_pid}, started ${_lstart}, not tracked by pidfile — kill manually or run stop"
        else
            echo "port ${_API_PORT} held by PID ${_port_pid}, started ${_lstart}, not tracked by pidfile — not the API server ($(_process_cmdline "${_port_pid}" || true)); stop will not signal it"
        fi
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
