#!/usr/bin/env bash
# schedule_layout.sh — the dashboard schedule window's band heights, and the
# tmux hooks that keep them.
#
# The schedule window (create.sh, "Window: schedule") is two full-width
# bands: the per-agent list (status-band.sh) on top and the event band
# (event-band.sh) below. The list must always show its column header and
# one row per roster agent. tmux, though, resizes a window's panes in
# proportion whenever the window changes size — a client attaching from a
# smaller terminal, a live terminal resize, or an explicit resize — so a
# list sized once at creation is squeezed and its first agents scroll off.
#
# This file owns both halves of keeping it whole:
#   - dashboard_schedule_list_rows: the list band's exact height for a
#     roster of N agents — the header row, one row per agent, and one row
#     of headroom (cadence-loop.sh ends every line with a newline, so
#     content exactly as tall as the pane scrolls the header off).
#   - dashboard_install_schedule_layout_hook /
#     dashboard_remove_schedule_layout_hook: tmux hooks that re-apply that
#     height to the list pane. The event band takes whatever is left, so
#     when the window is short it is the band that shrinks; the list keeps
#     its rows.
#
# Which hook does the work. A client-driven size change (a client
# attaching at another size, the first view of the window from that
# client, a live terminal resize) is applied by tmux lazily: client-resized,
# client-attached and session-window-changed all fire BEFORE tmux resizes
# the window, so a height set from them is rescaled away moments later, and
# after-resize-window fires only for an explicit `resize-window` command.
# tmux 3.2 has no window-resized hook. The one hook that fires AFTER the
# new size is in place is the window's own window-layout-changed, which
# tmux raises at the end of every window resize — so that hook, set on the
# schedule window alone (`set-hook -w`), is what keeps the list whole on
# attach, first view and live resize. The session hooks stay as well: they
# re-apply the height on an explicit resize-window and on a window switch
# at an unchanged size, where no layout change follows.
#
# Every hook runs the same tmux command, no shell:
#   if-shell -F -t <session>:schedule '#{?window_zoomed_flag,,1}' \
#       "resize-pane -t '<session>:schedule.0' -y <rows>"
# The zoom test matters because resize-pane unzooms its window and zooming
# is itself a layout change: without it, zooming either schedule pane
# would be undone at once. While zoomed nothing is re-applied; unzooming
# is another layout change, which re-applies the height.
#
# A command run from a hook raises no hooks or notifications of its own,
# so the resize-pane's layout change never re-fires window-layout-changed
# and re-applying the height can never loop.

# Include guard: safe to source multiple times.
[[ -n "${_PGAI_SCHEDULE_LAYOUT_SH_LOADED:-}" ]] && return 0
_PGAI_SCHEDULE_LAYOUT_SH_LOADED=1

if [[ -z "${PGAI_STATUS_BAND_RESERVED_ROWS:-}" ]]; then
    # shellcheck source=../../lib/dashboard_constants.sh
    source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../lib/dashboard_constants.sh"
fi

# The session-scoped tmux hooks the layout lifecycle manages. Indexed
# members [1] share the client-attached and session-window-changed hooks
# with the activation hook (lib/activation_hook.sh), which sets index 0 of
# each — so these must be installed after it, since setting a hook without
# an index clears the array.
DASHBOARD_SCHEDULE_LAYOUT_HOOK_NAMES=(
    after-resize-window
    client-resized
    "client-attached[1]"
    "session-window-changed[1]"
)

# The window-scoped hooks, set on the schedule window only (`set-hook -w -t
# <session>:schedule`). window-layout-changed is a window option in tmux:
# set with a bare session target it would land on whichever window happens
# to be current, not on the schedule window.
DASHBOARD_SCHEDULE_LAYOUT_WINDOW_HOOK_NAMES=(
    window-layout-changed
)

# ---------------------------------------------------------------------------
# dashboard_schedule_list_rows <roster_count>
# Echo the list band's height for <roster_count> agents: the header rows
# status-band.sh prints (PGAI_STATUS_BAND_RESERVED_ROWS), one row per agent,
# and one row of headroom. A non-numeric count reads as 0.
# ---------------------------------------------------------------------------
dashboard_schedule_list_rows() {
    local roster_count="${1:-0}"
    [[ "$roster_count" =~ ^[0-9]+$ ]] || roster_count=0
    printf '%s\n' "$(( roster_count + PGAI_STATUS_BAND_RESERVED_ROWS + 1 ))"
}

# ---------------------------------------------------------------------------
# dashboard_schedule_layout_command <session_name> <list_rows>
# Echo the tmux command that sets the schedule window's list pane to
# <list_rows> rows unless the schedule window is zoomed — what each layout
# hook runs.
# ---------------------------------------------------------------------------
dashboard_schedule_layout_command() {
    local session_name="${1:?dashboard_schedule_layout_command: session_name required}"
    local list_rows="${2:?dashboard_schedule_layout_command: list_rows required}"
    # shellcheck disable=SC2016  # '#{...}' is a tmux format, not a shell expansion
    printf "if-shell -F -t '%s:schedule' '#{?window_zoomed_flag,,1}' \"resize-pane -t '%s:schedule.0' -y %s\"\n" \
        "$session_name" "$session_name" "$list_rows"
}

# ---------------------------------------------------------------------------
# dashboard_install_schedule_layout_hook <session_name> <list_rows>
# Registers the layout command for every name in
# DASHBOARD_SCHEDULE_LAYOUT_HOOK_NAMES, scoped to <session_name> (`-t`,
# never `-g`), and for every name in
# DASHBOARD_SCHEDULE_LAYOUT_WINDOW_HOOK_NAMES on <session_name>'s schedule
# window (`-w -t <session_name>:schedule`), so no other tmux session or
# window on the server is touched.
# ---------------------------------------------------------------------------
dashboard_install_schedule_layout_hook() {
    local session_name="${1:?dashboard_install_schedule_layout_hook: session_name required}"
    local list_rows="${2:?dashboard_install_schedule_layout_hook: list_rows required}"
    local command hook_name
    command="$(dashboard_schedule_layout_command "$session_name" "$list_rows")"
    for hook_name in "${DASHBOARD_SCHEDULE_LAYOUT_HOOK_NAMES[@]}"; do
        tmux set-hook -t "$session_name" "$hook_name" "$command"
    done
    for hook_name in "${DASHBOARD_SCHEDULE_LAYOUT_WINDOW_HOOK_NAMES[@]}"; do
        tmux set-hook -w -t "${session_name}:schedule" "$hook_name" "$command"
    done
}

# ---------------------------------------------------------------------------
# dashboard_remove_schedule_layout_hook <session_name>
# Unsets every hook dashboard_install_schedule_layout_hook installed for the
# session and its schedule window. Each unset is swallowed on its own, so a
# session or window that is gone, or never had the hooks, is not an error
# and one failure never skips the rest.
# ---------------------------------------------------------------------------
dashboard_remove_schedule_layout_hook() {
    local session_name="${1:?dashboard_remove_schedule_layout_hook: session_name required}"
    local hook_name
    for hook_name in "${DASHBOARD_SCHEDULE_LAYOUT_HOOK_NAMES[@]}"; do
        tmux set-hook -u -t "$session_name" "$hook_name" 2>/dev/null || true
    done
    for hook_name in "${DASHBOARD_SCHEDULE_LAYOUT_WINDOW_HOOK_NAMES[@]}"; do
        tmux set-hook -u -w -t "${session_name}:schedule" "$hook_name" 2>/dev/null || true
    done
}
