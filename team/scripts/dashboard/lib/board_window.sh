#!/usr/bin/env bash
# board_window.sh — the one pane-geometry builder for the dashboard's
# board windows: window 2 (visibility) and window 1 (operator).
#
# Both windows show the same eight board columns at the same widths over
# the same one-row legend bar; the operator window adds one full-width
# interactive shell row between the board and the legend. Building both
# from this single function is what keeps their column widths identical —
# two hand-maintained split ladders drift (one resized every column to an
# absolute width, the other left tmux's chained-percentage widths).
#
# Public API
# ----------
#   dashboard_build_board_window <session> <window> <shell_pct> [<shell_min_rows>]
#       Splits an already-created, still single-pane tmux window
#       "<session>:<window>" into the board layout and force-resizes every
#       pane to absolute character dimensions. Sends no pane commands —
#       the caller dispatches those (create.sh routes every pane's first
#       command through its own dashboard_send_armed_keys).
#
#       shell_pct       0 builds the board + legend only (9 panes). A
#                       positive integer adds a full-width shell row
#                       between the board and the legend (10 panes) whose
#                       height is floor(window_height * shell_pct / 100),
#                       raised to shell_min_rows when smaller.
#       shell_min_rows  Floor for the shell row height. Default 3.
#
#       Sets, for the caller:
#         DASHBOARD_BOARD_SHELL_PANE   pane index of the shell row, or ""
#                                      when shell_pct is 0
#         DASHBOARD_BOARD_LEGEND_PANE  pane index of the legend row (always
#                                      the last pane)
#
# Final pane order (tmux pane index), top to bottom, left to right:
#   0 BUGS  1 PRIORITIES  2 REQUIREMENTS            (left region,  ~40% w)
#   3 PM    4 CODER  5 WRITER  6 TESTER  7 CM       (right region, ~60% w)
#   8 shell (only when shell_pct > 0)               (full width)
#   8 or 9 legend                                   (full width, 1 row)
#
# SIZING — explicit absolute widths, not chained percentages
# -----------------------------------------------------------
# Chained "split-window -h -l N%" applies each percentage to the shrinking
# remainder of the previous split, and tmux's integer rounding compounds at
# every step, so the splits below only create the panes; the resize pass
# afterwards sets every pane's exact size from the window's own dimensions:
#
#   TOP_BUDGET = floor(W * 40 / 100)        left region budget
#   TOP_AVAIL  = TOP_BUDGET - 2             minus 2 inter-column borders
#     REQUIREMENTS = floor(TOP_AVAIL * 35 / 100)
#     BUGS         = floor(TOP_AVAIL * 30 / 100)
#     PRIORITIES   = TOP_AVAIL - REQUIREMENTS - BUGS  (exact remainder)
#   BOT_BUDGET = W - TOP_BUDGET - 1         minus the left/right divider
#   BOT_AVAIL  = BOT_BUDGET - 4             minus 4 inter-column borders
#     BOT_COL = max(15, floor(BOT_AVAIL / 5)) for PM, CODER, TESTER, CM
#     WRITER  = BOT_COL + (BOT_AVAIL mod 5) (non-edge column takes the rest)
#
#   LEGEND_H  = 1
#   SHELL_H   = max(shell_min_rows, floor(H * shell_pct / 100))  (shell only)
#   CONTENT_H = H - LEGEND_H - 1 [- SHELL_H - 1]   one border per row break,
#               floor 14 (13 data rows + 1 label line per column)
#
# Window dimensions fall back to 120x24 when tmux reports nothing, and are
# floored to 45 columns (3 x 15) and 16 rows (+ the shell row and its
# border when there is one).
#
# Every tmux call goes through the bare `tmux` word, so a caller (or a
# test) that defines a `tmux` shell function — e.g. one pinning a private
# -L socket — routes this builder's calls through it as well.

# Source guard: safe to source multiple times.
[[ -n "${_PGAI_DASHBOARD_BOARD_WINDOW_SH_LOADED:-}" ]] && return 0
_PGAI_DASHBOARD_BOARD_WINDOW_SH_LOADED=1

dashboard_build_board_window() {
    local session="$1" window="$2" shell_pct="${3:-0}" shell_min_rows="${4:-3}"
    local target="${session}:${window}"

    local win_w win_h
    win_w="$(tmux display-message -t "$target" -p '#{window_width}')"
    win_h="$(tmux display-message -t "$target" -p '#{window_height}')"
    win_w="${win_w:-120}"
    win_h="${win_h:-24}"

    local legend_h=1 shell_h=0 min_h=16
    if [[ "$shell_pct" -gt 0 ]]; then
        shell_h=$(( win_h * shell_pct / 100 ))
        [[ "$shell_h" -lt "$shell_min_rows" ]] && shell_h="$shell_min_rows"
        min_h=$(( min_h + shell_h + 1 ))
    fi
    [[ "$win_w" -lt 45 ]] && win_w=45
    [[ "$win_h" -lt "$min_h" ]] && win_h="$min_h"

    # --- Splits: create the panes; sizes are corrected by the resize pass ---
    # Legend first, as the bottom row of the full window:
    #   0=content, 1=legend
    tmux split-window -t "${target}.0" -v -l "$legend_h"
    # Shell row between the content and the legend (operator only):
    #   0=content, 1=shell, 2=legend
    if [[ "$shell_h" -gt 0 ]]; then
        tmux split-window -t "${target}.0" -v -l "$shell_h"
    fi
    # Board content → left (~40%) and right (~60%) regions. Each split
    # inserts its new pane directly after the pane it targets, pushing the
    # shell and legend rows one index further down every time.
    tmux split-window -t "${target}.0" -h -l 60%
    # Left region → BUGS | PRIORITIES | REQUIREMENTS
    tmux split-window -t "${target}.0" -h -l 67%
    tmux split-window -t "${target}.1" -h -l 50%
    # Right region (now pane 3) → PM | CODER | WRITER | TESTER | CM
    tmux split-window -t "${target}.3" -h -l 80%
    tmux split-window -t "${target}.4" -h -l 75%
    tmux split-window -t "${target}.5" -h -l 67%
    tmux split-window -t "${target}.6" -h -l 50%

    if [[ "$shell_h" -gt 0 ]]; then
        DASHBOARD_BOARD_SHELL_PANE=8
        DASHBOARD_BOARD_LEGEND_PANE=9
    else
        DASHBOARD_BOARD_SHELL_PANE=""
        DASHBOARD_BOARD_LEGEND_PANE=8
    fi

    # --- Absolute sizes (see SIZING in the header) ---
    local top_budget top_avail req_col bugs_col prio_col
    top_budget=$(( win_w * 40 / 100 ))
    top_avail=$(( top_budget - 2 ))
    req_col=$(( top_avail * 35 / 100 ))
    bugs_col=$(( top_avail * 30 / 100 ))
    prio_col=$(( top_avail - req_col - bugs_col ))

    local bot_budget bot_avail bot_col bot_rem bot_mid_col
    bot_budget=$(( win_w - top_budget - 1 ))
    bot_avail=$(( bot_budget - 4 ))
    bot_col=$(( bot_avail / 5 ))
    bot_rem=$(( bot_avail % 5 ))
    [[ "$bot_col" -lt 15 ]] && bot_col=15
    bot_mid_col=$(( bot_col + bot_rem ))

    local content_h
    content_h=$(( win_h - legend_h - 1 ))
    if [[ "$shell_h" -gt 0 ]]; then
        content_h=$(( content_h - shell_h - 1 ))
    fi
    [[ "$content_h" -lt 14 ]] && content_h=14

    # --- Resize pass: every pane gets an explicit size ---
    tmux resize-pane -t "${target}.0" -x "$bugs_col"    -y "$content_h"
    tmux resize-pane -t "${target}.1" -x "$prio_col"    -y "$content_h"
    tmux resize-pane -t "${target}.2" -x "$req_col"     -y "$content_h"
    tmux resize-pane -t "${target}.3" -x "$bot_col"     -y "$content_h"
    tmux resize-pane -t "${target}.4" -x "$bot_col"     -y "$content_h"
    tmux resize-pane -t "${target}.5" -x "$bot_mid_col" -y "$content_h"
    tmux resize-pane -t "${target}.6" -x "$bot_col"     -y "$content_h"
    tmux resize-pane -t "${target}.7" -x "$bot_col"     -y "$content_h"
    if [[ -n "$DASHBOARD_BOARD_SHELL_PANE" ]]; then
        tmux resize-pane -t "${target}.${DASHBOARD_BOARD_SHELL_PANE}" -x "$win_w" -y "$shell_h"
    fi
    tmux resize-pane -t "${target}.${DASHBOARD_BOARD_LEGEND_PANE}" -x "$win_w" -y "$legend_h"
}
