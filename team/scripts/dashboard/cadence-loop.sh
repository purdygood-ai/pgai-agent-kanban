#!/usr/bin/env bash
# cadence-loop.sh — visibility-aware refresh-cadence pane loop.
#
# The pane command create.sh sends for every rendering dashboard pane.
# Renders <renderer> once on arrival, then every <active_interval> seconds
# while the pane is visible -- its window is the active one AND a client is
# attached (lib/cadence.sh dashboard_pane_visible, the one visibility
# decision). While hidden it does not render at all: it only re-checks
# visibility every PGAI_DASHBOARD_HIDDEN_MULTIPLIER x <active_interval>
# seconds, and renders at once on the dashboard's activation-hook signal
# when its window becomes visible. Applies a bounded,
# per-pane-deterministic stagger jitter once before the first render.
#
# With --stream, <renderer> is a command that never returns (logs.sh,
# debug-logs.sh, cron-tail.sh following their logs): it is started once in
# its own process group, which is stopped while the pane is hidden and
# continued when it is visible again. See lib/cadence.sh for the full
# design and the race-safety argument for installing a real signal handler
# here.
#
# <renderer>'s STDERR is never left connected to the pane's display: it is
# appended to a pane-scoped debug log file under
# $(pgai_temp_subdir dashboard)/logs/<pane-label>.log (resolved once per
# loop invocation; falls back to /dev/null if that target cannot be
# written, so a pane still renders its columns even then). This keeps
# quality-class narration a renderer writes to stderr -- e.g.
# lib/snapshot.sh's cooperative-cache cache-miss lines -- off the rendered
# columns, which is what stdout painting a tmux pane otherwise stamps it
# onto (see lib/cadence.sh's "WHY RENDER STDERR IS CAPTURED HERE" header).
#
# Usage:
#   cadence-loop.sh --interval N [--stream] [--hidden-multiplier N] \
#       [--jitter-bound N] -- <renderer> [args...]
#
# Flags:
#   --interval           Visible refresh interval in seconds (required;
#                          this is PGAI_DASHBOARD_REFRESH_SECONDS, or a
#                          pane-specific fixed interval such as the git/
#                          metadata windows' 10s or training-logs' 30s). For
#                          --stream, how often visibility is re-checked.
#   --stream             <renderer> never returns: run it once and stop/
#                          continue it with the pane's visibility
#   --hidden-multiplier   Overrides PGAI_DASHBOARD_HIDDEN_MULTIPLIER (testing only)
#   --jitter-bound        Overrides PGAI_DASHBOARD_JITTER_MAX_SECONDS (testing only)
#   -h, --help            Show this help and exit
#
# Environment:
#   PGAI_DASHBOARD_PANE_SIGNAL             Signal the activation hook sends
#                                            (default: USR1)
#   PGAI_DASHBOARD_CADENCE_FORCE_STATE     Test-only: "active", "hidden", or
#                                            "file:<path>" (state read from
#                                            <path> on every check); bypasses
#                                            the real tmux visibility query
#                                            (see lib/cadence.sh)

set -uo pipefail
# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/dashboard_constants.sh
source "${SCRIPT_DIR}/../lib/dashboard_constants.sh"
# shellcheck source=lib/cadence.sh
source "${SCRIPT_DIR}/lib/cadence.sh"

INTERVAL=""
STREAM=false
HIDDEN_MULTIPLIER="${PGAI_DASHBOARD_HIDDEN_MULTIPLIER}"
JITTER_BOUND="${PGAI_DASHBOARD_JITTER_MAX_SECONDS}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --interval)
            INTERVAL="${2:-}"
            shift 2
            ;;
        --stream)
            STREAM=true
            shift
            ;;
        --hidden-multiplier)
            HIDDEN_MULTIPLIER="${2:-}"
            shift 2
            ;;
        --jitter-bound)
            JITTER_BOUND="${2:-}"
            shift 2
            ;;
        --)
            shift
            break
            ;;
        -h|--help)
            # shellcheck source=../lib/header_help.sh
            source "$(dirname "${BASH_SOURCE[0]}")/../lib/header_help.sh"
            header_help_print "${BASH_SOURCE[0]}"
            exit 0
            ;;
        *)
            echo "ERROR: Unknown argument: $1" >&2
            echo "Usage: $0 --interval N [--stream] [--hidden-multiplier N] [--jitter-bound N] -- <renderer> [args...]" >&2
            exit 1
            ;;
    esac
done

if [[ -z "$INTERVAL" ]]; then
    echo "ERROR: --interval is required" >&2
    exit 1
fi

if [[ $# -eq 0 ]]; then
    echo "ERROR: a renderer command is required after --" >&2
    exit 1
fi

if [[ "$STREAM" == "true" ]]; then
    dashboard_cadence_run_stream "$INTERVAL" "$HIDDEN_MULTIPLIER" -- "$@"
else
    dashboard_cadence_run_loop "$INTERVAL" "$HIDDEN_MULTIPLIER" "$JITTER_BOUND" -- "$@"
fi
