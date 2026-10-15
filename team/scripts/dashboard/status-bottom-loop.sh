#!/usr/bin/env bash
# status-bottom-loop.sh — renders the dashboard's bottom status line into a
# tmux option on a fixed cadence, so the status bar never forks a job.
#
# create.sh starts one of these per dashboard session, in the background,
# right after it creates the session. The loop renders <renderer> once at
# start, then every <interval> seconds while at least one client is attached
# to the session, and stores each rendered line in the session's
# <option> (default @pgai_status_bottom). status-format[2] reads that option
# as #{@pgai_status_bottom}, which tmux expands with no fork -- unlike a
# #(...) job, which tmux re-runs on almost every status redraw, once per
# attached client. While no client is attached the loop renders nothing; it
# only checks again every <interval> seconds. Whether a client is attached
# is lib/cadence.sh's dashboard_pane_visible, asked about the session's
# active pane -- the one visibility decision every dashboard loop uses.
#
# The loop ends with its session: it exits at the first check that finds the
# session gone, or a different session now carrying the same name (the
# session id it started with no longer matches). It never outlives the
# session by more than one <interval>.
#
# A render that prints nothing -- the renderer was killed, failed before it
# printed, or had nothing to say -- leaves the previous line in place: the
# option always holds the most recent non-empty render, so the bar never
# blanks between two good renders. A render that prints a line is stored
# whatever its exit status.
#
# <renderer>'s stderr is appended to
# $(pgai_temp_subdir dashboard/logs)/status-bottom.log rather than reaching
# the status bar, together with one note per empty render naming the
# renderer's exit status.
#
# Usage:
#   status-bottom-loop.sh --session NAME --interval N [--option NAME] \
#       -- <renderer> [args...]
#
# Flags:
#   --session NAME   The tmux session whose option is written and whose
#                    lifetime bounds the loop (required)
#   --interval N     Seconds between renders while a client is attached; a
#                    whole number from 1 to 3600 (required; create.sh passes
#                    its PGAI_DASHBOARD_REFRESH_SECONDS value)
#   --option NAME    The tmux user option to write (default:
#                    @pgai_status_bottom)
#   -h, --help       Show this help and exit 0
#
# Exit codes:
#   0   The session ended (the normal way out), or --help.
#   2   A usage error: a missing or malformed flag, or no renderer.

set -uo pipefail
# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/temp.sh
source "${SCRIPT_DIR}/../lib/temp.sh"
# shellcheck source=lib/cadence.sh
source "${SCRIPT_DIR}/lib/cadence.sh"

SESSION=""
INTERVAL=""
OPTION="@pgai_status_bottom"

_usage_error() {
    printf 'status-bottom-loop.sh: %s\n' "$1" >&2
    printf 'Usage: status-bottom-loop.sh --session NAME --interval N [--option NAME] -- <renderer> [args...]\n' >&2
    exit 2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --session)
            [[ $# -ge 2 ]] || _usage_error "--session needs a value"
            SESSION="$2"
            shift 2
            ;;
        --interval)
            [[ $# -ge 2 ]] || _usage_error "--interval needs a value"
            INTERVAL="$2"
            shift 2
            ;;
        --option)
            [[ $# -ge 2 ]] || _usage_error "--option needs a value"
            OPTION="$2"
            shift 2
            ;;
        --)
            shift
            break
            ;;
        -h|--help)
            # Read the whole file via mapfile (nothing downstream can stop
            # early), then print the leading comment block after the shebang.
            mapfile -t __help_lines < "$0"
            for __line in "${__help_lines[@]:1}"; do
                [[ "$__line" == \#* ]] || break
                __line="${__line#\#}"
                printf '%s\n' "${__line# }"
            done
            exit 0
            ;;
        *)
            _usage_error "unknown argument: $1"
            ;;
    esac
done

[[ -n "$SESSION" ]] || _usage_error "--session is required"
[[ "$INTERVAL" =~ ^[0-9]+$ ]] && (( INTERVAL >= 1 && INTERVAL <= 3600 )) \
    || _usage_error "--interval must be a whole number from 1 to 3600, got '${INTERVAL}'"
[[ "$OPTION" == @* ]] || _usage_error "--option must name a tmux user option (starting with @), got '${OPTION}'"
[[ $# -gt 0 ]] || _usage_error "a renderer command is required after --"

STDERR_LOG="/dev/null"
_log_dir="$(pgai_temp_subdir "dashboard/logs" 2>/dev/null)" && [[ -w "$_log_dir" ]] \
    && STDERR_LOG="${_log_dir}/status-bottom.log"
unset _log_dir

# "=NAME:" names the session exactly (never as a prefix of another name)
# and resolves as a target pane too, which display-message and set-option
# take. tmux answers a query against a missing session with an empty
# expansion, so a session that is gone reads as an empty session id.
TARGET="=${SESSION}:"

SESSION_ID="$(tmux display-message -p -t "$TARGET" '#{session_id}' 2>/dev/null)" || exit 0
[[ -n "$SESSION_ID" ]] || exit 0

_render_into_option() {
    local line status
    line="$("$@" 2>>"$STDERR_LOG")"
    status=$?
    # Empty output keeps the last good line: writing it would blank the bar
    # until the next render. Only the emptiness of the output decides; a
    # non-zero exit with a line still stores the line.
    if [[ -z "$line" ]]; then
        printf '%(%Y-%m-%dT%H:%M:%S%z)T status-bottom-loop: render printed nothing (exit %d); kept the previous %s\n' \
            -1 "$status" "$OPTION" >>"$STDERR_LOG" 2>/dev/null
        return 0
    fi
    tmux set-option -q -t "$TARGET" "$OPTION" "$line" 2>/dev/null
}

# One render at start, so the line is there the moment a client attaches.
_render_into_option "$@"

while sleep "$INTERVAL"; do
    _session_now="$(tmux display-message -p -t "$TARGET" '#{session_id}' 2>/dev/null)" || break
    [[ "$_session_now" == "$SESSION_ID" ]] || break
    # The session's active pane is visible exactly when a client is attached.
    [[ "$(dashboard_pane_visible "$TARGET")" == "1" ]] || continue
    _render_into_option "$@"
done
exit 0
