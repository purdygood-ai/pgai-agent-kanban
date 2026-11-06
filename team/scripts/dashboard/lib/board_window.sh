#!/usr/bin/env bash
# board_window.sh — the one pane-geometry builder for the dashboard's
# board windows: window 2 (visibility) and window 1 (operator).
#
# Both windows show the same board columns at the same widths over the same
# one-row legend bar; the operator window adds one full-width interactive
# shell row between the board and the legend, and can add a workbench
# column — a tall interactive pane on the far right spanning the board and
# the shell row. Building both windows from this single function is what
# keeps their column widths identical — two hand-maintained split ladders
# drift (one resized every column to an absolute width, the other left
# tmux's chained-percentage widths).
#
# Two column sets, one split ladder:
#   split   3 + 5 — BUGS, PRIORITIES, REQUIREMENTS, then the five agent-queue
#                   columns PM, CODER, WRITER, TESTER, CM (8 columns)
#   merged  3 + 1 — BUGS, PRIORITIES, REQUIREMENTS, then one AGENTS column
#                   (4 columns)
# The merged set skips the four splits that cut the right region into five
# agent columns; everything else runs the same calls for both.
#
# Public API
# ----------
#   dashboard_build_board_window <session> <window> <shell_pct> [<shell_min_rows>]
#                                [<workbench_pct>] [<workbench_min_cols>]
#                                [<column_set>]
#       Splits an already-created, still single-pane tmux window
#       "<session>:<window>" into the board layout and force-resizes every
#       pane to absolute character dimensions. Sends no pane commands —
#       the caller dispatches those (create.sh routes every pane's first
#       command through its own dashboard_send_armed_keys).
#
#       shell_pct       0 builds the board + legend only. A positive integer
#                       adds a shell row under the board, above the legend,
#                       whose height is floor(window_height * shell_pct /
#                       100), raised to shell_min_rows when smaller.
#       shell_min_rows  Floor for the shell row height. Default 3.
#       workbench_pct   Width of the workbench column, an integer percent
#                       (1-99) of the window width. 0 or omitted builds no
#                       workbench, and every pane keeps exactly the geometry
#                       it has without this argument. Default 0.
#       workbench_min_cols
#                       Narrow-window guard: when the window is narrower
#                       than this many columns, no workbench is built, the
#                       board keeps the full width, and one NOTICE line on
#                       stderr names the window width and this threshold.
#                       Default 0 (no guard).
#       column_set      "split" (3 + 5, 8 columns) or "merged" (3 + 1, 4
#                       columns). Default "split": omitting it builds the
#                       8-column board with exactly the geometry it has
#                       without this argument. Any other value is refused
#                       (return 1, a dual-channel error on stderr) before
#                       any tmux call, leaving the three pane-index
#                       variables below empty.
#
#       Fit guard: a workbench is built only when every board column still
#       gets at least 15 columns beside it (dashboard_board_workbench_decision
#       below). When the columns would not fit, no workbench is built, the
#       board keeps the full width, and one NOTICE line on stderr names the
#       window width, the workbench percentage and the column count.
#
#       Degraded layout: a split board region too narrow for its eight
#       columns to keep 15 columns each (under 127 columns) is shared evenly
#       between them instead (SIZING below), and one NOTICE line on stderr
#       names the region's width, the 127 it needs and the width each
#       column gets.
#
#       Pane count: columns + 1 legend [+ 1 shell] [+ 1 workbench] — 9 to 11
#       panes for split, 5 to 7 for merged.
#
#       Records the build on the window as tmux user options, which
#       dashboard_reflow_board_window reads: @pgai_board_column_set,
#       @pgai_board_shell_pct, @pgai_board_shell_min_rows,
#       @pgai_board_workbench_pct, @pgai_board_workbench_min_cols,
#       @pgai_board_workbench_pane (the workbench's pane id, or empty when
#       none was built) and @pgai_board_size (the WIDTHxHEIGHT the panes
#       were last sized for). @pgai_board_notice holds the degraded-layout
#       NOTICE line for the size the panes were last sized for, or is empty
#       when every column keeps the floor; the builder and every reflow set
#       it, and the window's legend bar shows it (legend-render.sh).
#
#       Sets, for the caller:
#         DASHBOARD_BOARD_SHELL_PANE      pane index of the shell row, or ""
#                                         when shell_pct is 0
#         DASHBOARD_BOARD_WORKBENCH_PANE  pane index of the workbench
#                                         column, or "" when none was built
#         DASHBOARD_BOARD_LEGEND_PANE     pane index of the legend row
#                                         (always the last pane)
#         DASHBOARD_BOARD_LAYOUT_NOTICE   the degraded-layout NOTICE line
#                                         printed, or "" when none was
#       Each follows the column count: the first index after the columns
#       is the shell row's (8 for split, 4 for merged).
#
#   dashboard_board_workbench_decision <window_width> <workbench_pct>
#                                      <workbench_min_cols> <column_set>
#       Whether a window this wide gets a workbench; no tmux calls. Sets
#       DASHBOARD_BOARD_WORKBENCH_W to the workbench width (0 for none) and
#       DASHBOARD_BOARD_WORKBENCH_SKIP to why there is none: "" (one is
#       built, or workbench_pct is 0), "narrow" (the window is under
#       workbench_min_cols) or "fit" (some board column would get fewer
#       than 15 columns beside it).
#
#   dashboard_reflow_board_window <window>
#       Re-lays out a board window the builder made — with or without a
#       workbench — for the size it has now (see RESIZE below);
#       board-reflow.sh runs it from the window's layout hook. Prints
#       nothing: a degraded layout's NOTICE goes to @pgai_board_notice only.
#
#   dashboard_install_board_reflow_hook <window> <reflow_script>
#       Sets <window>'s own window-layout-changed hook to run
#       "<reflow_script> --window <window_id>" whenever the window's size
#       differs from @pgai_board_size and the window is not zoomed.
#
# Final pane order (tmux pane index), top to bottom, left to right — split:
#   0 BUGS  1 PRIORITIES  2 REQUIREMENTS            (board left,  ~40% of board w)
#   3 PM    4 CODER  5 WRITER  6 TESTER  7 CM       (board right, ~60% of board w)
#   8 shell (only when shell_pct > 0)               (board width)
#   next: workbench (only when built)               (far right, board + shell h)
#   last: legend                                    (full window width, 1 row)
# so the split operator window with both is 0-7 columns, 8 shell,
# 9 workbench, 10 legend. Merged:
#   0 BUGS  1 PRIORITIES  2 REQUIREMENTS            (board left,  ~75% of board w)
#   3 AGENTS                                        (board right, ~25% of board w)
#   4 shell, then workbench, then legend, as above
# so the merged operator window with both is 0-3 columns, 4 shell,
# 5 workbench, 6 legend.
#
# SIZING — explicit absolute widths, not chained percentages
# -----------------------------------------------------------
# Chained "split-window -h -l N%" applies each percentage to the shrinking
# remainder of the previous split, and tmux's integer rounding compounds at
# every step, so the splits below only create the panes; the resize pass
# afterwards sets every pane's exact size from the window's own dimensions.
# W is the window width; B is the board region's width — W itself, or with
# a workbench, what is left of the window beside it. Both column sets size
# their columns against B, and their widths plus dividers fill B exactly:
#
#   WORKBENCH_W = floor(W * workbench_pct / 100)       (workbench only)
#   B           = W [- WORKBENCH_W - 1]                minus its divider
#
# split (3 + 5):
#   TOP_BUDGET = floor(B * 40 / 100)        left region budget
#   TOP_AVAIL  = TOP_BUDGET - 2             minus 2 inter-column borders
#     REQUIREMENTS = floor(TOP_AVAIL * 35 / 100)
#     BUGS         = floor(TOP_AVAIL * 30 / 100)
#     PRIORITIES   = TOP_AVAIL - REQUIREMENTS - BUGS  (exact remainder)
#   BOT_BUDGET = B - TOP_BUDGET - 1         minus the left/right divider
#   BOT_AVAIL  = BOT_BUDGET - 4             minus 4 inter-column borders
#     BOT_COL = max(15, floor(BOT_AVAIL / 5)) for PM, CODER, TESTER, CM
#     WRITER  = BOT_COL + (BOT_AVAIL mod 5) (non-edge column takes the rest)
#
#   When BOT_AVAIL is under 5 x 15 the shortfall is taken from PRIORITIES,
#   then REQUIREMENTS, then BUGS — none below 15 — and added to BOT_AVAIL
#   before BOT_COL is computed, with or without a workbench.
#
#   Floor pass: widths that are all at least 15 and, with their seven
#   dividers, fill B exactly are used as they are — every B of 130 or more,
#   and the widths beside the default workbench at 220. Otherwise, on a
#   board region narrow enough that a column falls under 15 or the floors
#   overflow B:
#     B >= 8 x 15 + 7 = 127  every column under 15 is raised to 15, and the
#                            excess is taken one column at a time from the
#                            widest column (the leftmost of equals), none
#                            below 15 — every column keeps the floor;
#     B <  127               degraded layout: the eight columns share B
#                            evenly, floor((B - 7) / 8) each, the first
#                            (B - 7) mod 8 columns one wider, and the build
#                            prints one NOTICE line saying so (the reflow
#                            records it in @pgai_board_notice instead).
#   No column is ever crushed below its share while the others keep theirs.
#
# merged (3 + 1):
#   AVAIL  = B - 3                          minus 3 inter-column borders
#   AGENTS = max(15, floor(AVAIL * 25 / 100))
#   IN_AVAIL = AVAIL - AGENTS               the three input columns' share
#     REQUIREMENTS = floor(IN_AVAIL * 35 / 100)
#     BUGS         = floor(IN_AVAIL * 30 / 100)
#     PRIORITIES   = IN_AVAIL - REQUIREMENTS - BUGS   (exact remainder)
#
#   Any input column under 15 is raised to 15, taken from AGENTS, then
#   PRIORITIES, REQUIREMENTS, BUGS — none below 15 — so no column is under
#   15 whenever B is at least 4 x 15 + 3 = 63.
#
#   The workbench is built only when the widths computed for the board
#   region beside it are all at least 15 and, with their dividers, fill B
#   exactly; otherwise the window is sized as if workbench_pct were 0.
#   For split that needs a board region of at least 127 columns (a window
#   of 212 at 40%, so not 160-211 under the default threshold); merged, 63.
#
#   LEGEND_H  = 1
#   SHELL_H   = max(shell_min_rows, floor(H * shell_pct / 100))  (shell only)
#   CONTENT_H = H - LEGEND_H - 1 [- SHELL_H - 1]   one border per row break,
#               floor 14 (13 data rows + 1 label line per column)
#   WORKBENCH_H = H - LEGEND_H - 1          the board and shell rows together
#
# Window dimensions fall back to 120x24 when tmux reports nothing, and are
# floored to 45 columns (3 x 15) and 16 rows (+ the shell row and its
# border when there is one).
#
# RESIZE — a client at another width
# ----------------------------------
# A dashboard is built detached at a fixed size (create.sh: 220 columns),
# and tmux resizes a window to the client that views it (window-size
# latest), taking the whole difference from one pane — a narrower client
# crushes the leftmost column while the others keep their creation-time
# widths. So every board window — the operator window with or without a
# workbench, and the visibility window — gets a window-layout-changed hook
# (dashboard_install_board_reflow_hook): tmux raises it after the window has
# its new size, whether a client attached at another size, a terminal was
# resized, or resize-window ran (lib/schedule_layout.sh explains why the
# client and session hooks fire too early). The hook runs board-reflow.sh,
# which calls dashboard_reflow_board_window:
#   - the build's own decision (dashboard_board_workbench_decision) at the
#     new width, for a window built with a workbench;
#   - too narrow: the workbench pane moves to a window of its own named
#     "workbench" (break-pane) in the board window's own session — never
#     whichever session the server last used — its shell still running,
#     and the board and shell row take the full width;
#   - wide enough again: the legend is lifted out, the workbench is joined
#     back full height beside the board and shell (join-pane -f -h), and the
#     legend is joined back full width beneath them (join-pane -f -v) —
#     the tree and pane order the builder makes;
#   - then every pane is resized by the builder's own resize pass
#     (_dashboard_board_resize_panes), so the columns get the SIZING widths
#     for the new width — the degraded layout included, its NOTICE recorded
#     in @pgai_board_notice. A window built without a workbench only gets
#     this last step.
# The hook runs only when the window's size differs from @pgai_board_size,
# which the reflow records first, so the layout changes the reflow itself
# makes never re-run it. A zoomed window is left alone (resize-pane would
# unzoom it), as is a window whose pane count no longer matches the build —
# the operator split or closed a pane — or whose workbench shell has exited.
# A workbench the build skipped (the window too narrow at creation) is
# never created by a reflow: the board takes the full width at every size.
#
# Every tmux call goes through the bare `tmux` word, so a caller (or a
# test) that defines a `tmux` shell function — e.g. one pinning a private
# -L socket — routes this builder's calls through it as well.

# Source guard: safe to source multiple times.
[[ -n "${_PGAI_DASHBOARD_BOARD_WINDOW_SH_LOADED:-}" ]] && return 0
_PGAI_DASHBOARD_BOARD_WINDOW_SH_LOADED=1

# The narrowest any board column is sized, and the narrowest the workbench
# decision lets a column beside the workbench be.
DASHBOARD_BOARD_COLUMN_FLOOR=15

# The window a workbench pane waits in while its board window is too narrow
# for it (dashboard_reflow_board_window).
DASHBOARD_BOARD_PARKED_WORKBENCH_WINDOW="workbench"

# ---------------------------------------------------------------------------
# _dashboard_board_column_count <column_set>
# Sets _DBW_COLUMN_COUNT to the set's column count (8 split, 4 merged);
# returns 1, with the count 0, for any other set.
# ---------------------------------------------------------------------------
_dashboard_board_column_count() {
    case "$1" in
        split)  _DBW_COLUMN_COUNT=8 ;;
        merged) _DBW_COLUMN_COUNT=4 ;;
        *)      _DBW_COLUMN_COUNT=0; return 1 ;;
    esac
}

# ---------------------------------------------------------------------------
# _dashboard_board_split_hold_floor <board_w>
# The split set's floor pass over _DBW_COLUMN_WIDTHS (SIZING in the header).
# Widths that are all at least DASHBOARD_BOARD_COLUMN_FLOOR and, with their
# seven dividers, fill <board_w> exactly are left as they are. Otherwise,
# when the region holds eight floor-wide columns, every column under the
# floor is raised to it and the excess is trimmed one column at a time from
# the widest column (the leftmost of equals), never below the floor; when it
# does not, the columns share the region evenly — the degraded layout,
# flagged in _DBW_COLUMNS_DEGRADED.
# ---------------------------------------------------------------------------
_dashboard_board_split_hold_floor() {
    local board_w="$1" floor="$DASHBOARD_BOARD_COLUMN_FLOOR"
    local count="${#_DBW_COLUMN_WIDTHS[@]}" total=0 under=0 i widest
    for i in "${!_DBW_COLUMN_WIDTHS[@]}"; do
        total=$(( total + _DBW_COLUMN_WIDTHS[i] ))
        [[ "${_DBW_COLUMN_WIDTHS[$i]}" -lt "$floor" ]] && under=1
    done
    total=$(( total + count - 1 ))
    [[ "$under" -eq 0 && "$total" -eq "$board_w" ]] && return 0

    if [[ "$board_w" -lt $(( count * floor + count - 1 )) ]]; then
        local share=$(( (board_w - count + 1) / count ))
        local extra=$(( (board_w - count + 1) % count ))
        for i in "${!_DBW_COLUMN_WIDTHS[@]}"; do
            _DBW_COLUMN_WIDTHS[i]=$(( share + (i < extra ? 1 : 0) ))
        done
        _DBW_COLUMNS_DEGRADED=1
        return 0
    fi

    total=$(( count - 1 ))
    for i in "${!_DBW_COLUMN_WIDTHS[@]}"; do
        [[ "${_DBW_COLUMN_WIDTHS[$i]}" -lt "$floor" ]] && _DBW_COLUMN_WIDTHS[i]="$floor"
        total=$(( total + _DBW_COLUMN_WIDTHS[i] ))
    done
    while [[ "$total" -gt "$board_w" ]]; do
        widest=0
        for i in "${!_DBW_COLUMN_WIDTHS[@]}"; do
            [[ "${_DBW_COLUMN_WIDTHS[$i]}" -gt "${_DBW_COLUMN_WIDTHS[$widest]}" ]] && widest="$i"
        done
        _DBW_COLUMN_WIDTHS[widest]=$(( _DBW_COLUMN_WIDTHS[widest] - 1 ))
        total=$(( total - 1 ))
    done
}

# ---------------------------------------------------------------------------
# _dashboard_board_column_widths <board_w> <column_set>
# Sets the array _DBW_COLUMN_WIDTHS to the column widths, left to right in
# pane order, for a board region <board_w> columns wide (SIZING in the
# header), and _DBW_COLUMNS_DEGRADED to 1 when the region is too narrow for
# every split column to keep the floor (the even-share layout), else 0.
# ---------------------------------------------------------------------------
_dashboard_board_column_widths() {
    local board_w="$1" column_set="$2"
    local col give
    _DBW_COLUMN_WIDTHS=()
    _DBW_COLUMNS_DEGRADED=0
    if [[ "$column_set" == "split" ]]; then
        local top_budget top_avail req_col bugs_col prio_col
        top_budget=$(( board_w * 40 / 100 ))
        top_avail=$(( top_budget - 2 ))
        req_col=$(( top_avail * 35 / 100 ))
        bugs_col=$(( top_avail * 30 / 100 ))
        prio_col=$(( top_avail - req_col - bugs_col ))

        local bot_budget bot_avail bot_col bot_rem bot_mid_col
        bot_budget=$(( board_w - top_budget - 1 ))
        bot_avail=$(( bot_budget - 4 ))

        # On a narrow board region (beside a workbench, or a narrow window)
        # the right region's five 15-column floors can outgrow its 60%
        # budget; move the shortfall over from the left columns' surplus.
        local shortfall=$(( 5 * DASHBOARD_BOARD_COLUMN_FLOOR - bot_avail ))
        for col in prio_col req_col bugs_col; do
            [[ "$shortfall" -gt 0 ]] || break
            give=$(( ${!col} - DASHBOARD_BOARD_COLUMN_FLOOR ))
            [[ "$give" -gt "$shortfall" ]] && give="$shortfall"
            [[ "$give" -gt 0 ]] || continue
            printf -v "$col" '%d' $(( ${!col} - give ))
            shortfall=$(( shortfall - give ))
            bot_avail=$(( bot_avail + give ))
        done

        bot_col=$(( bot_avail / 5 ))
        bot_rem=$(( bot_avail % 5 ))
        [[ "$bot_col" -lt "$DASHBOARD_BOARD_COLUMN_FLOOR" ]] && bot_col="$DASHBOARD_BOARD_COLUMN_FLOOR"
        bot_mid_col=$(( bot_col + bot_rem ))

        _DBW_COLUMN_WIDTHS=("$bugs_col" "$prio_col" "$req_col"
                            "$bot_col" "$bot_col" "$bot_mid_col" "$bot_col" "$bot_col")
        _dashboard_board_split_hold_floor "$board_w"
    else
        local avail agents_col in_avail req_col bugs_col prio_col
        avail=$(( board_w - 3 ))
        agents_col=$(( avail * 25 / 100 ))
        [[ "$agents_col" -lt "$DASHBOARD_BOARD_COLUMN_FLOOR" ]] && agents_col="$DASHBOARD_BOARD_COLUMN_FLOOR"
        in_avail=$(( avail - agents_col ))
        req_col=$(( in_avail * 35 / 100 ))
        bugs_col=$(( in_avail * 30 / 100 ))
        prio_col=$(( in_avail - req_col - bugs_col ))

        # On a narrow board region raise any input column under 15 to 15,
        # taking the difference from the columns that still have more.
        local need donor
        for col in bugs_col prio_col req_col; do
            need=$(( DASHBOARD_BOARD_COLUMN_FLOOR - ${!col} ))
            for donor in agents_col prio_col req_col bugs_col; do
                [[ "$need" -gt 0 ]] || break
                [[ "$donor" == "$col" ]] && continue
                give=$(( ${!donor} - DASHBOARD_BOARD_COLUMN_FLOOR ))
                [[ "$give" -gt "$need" ]] && give="$need"
                [[ "$give" -gt 0 ]] || continue
                printf -v "$donor" '%d' $(( ${!donor} - give ))
                printf -v "$col" '%d' $(( ${!col} + give ))
                need=$(( need - give ))
            done
        done

        _DBW_COLUMN_WIDTHS=("$bugs_col" "$prio_col" "$req_col" "$agents_col")
    fi
}

# ---------------------------------------------------------------------------
# _dashboard_board_columns_fit <board_w> <column_set>
# Returns 0 when the column widths for a board region <board_w> columns wide
# beside a workbench are each at least DASHBOARD_BOARD_COLUMN_FLOOR and,
# with one divider between each pair, fill the region exactly — so tmux
# can give every column the width it is asked for.
# ---------------------------------------------------------------------------
_dashboard_board_columns_fit() {
    local board_w="$1" column_set="$2" width total=0
    _dashboard_board_column_widths "$board_w" "$column_set"
    for width in "${_DBW_COLUMN_WIDTHS[@]}"; do
        [[ "$width" -ge "$DASHBOARD_BOARD_COLUMN_FLOOR" ]] || return 1
        total=$(( total + width ))
    done
    [[ $(( total + ${#_DBW_COLUMN_WIDTHS[@]} - 1 )) -eq "$board_w" ]]
}

# ---------------------------------------------------------------------------
# dashboard_board_workbench_decision <window_width> <workbench_pct>
#                                    <workbench_min_cols> <column_set>
# See the header. The width is the one tmux reports, before the builder's
# 45-column floor.
# ---------------------------------------------------------------------------
dashboard_board_workbench_decision() {
    local win_w="$1" workbench_pct="${2:-0}" workbench_min_cols="${3:-0}"
    local column_set="${4:-split}" workbench_w
    DASHBOARD_BOARD_WORKBENCH_W=0
    DASHBOARD_BOARD_WORKBENCH_SKIP=""
    [[ "$workbench_pct" -gt 0 ]] || return 0
    if [[ "$win_w" -lt "$workbench_min_cols" ]]; then
        DASHBOARD_BOARD_WORKBENCH_SKIP="narrow"
        return 0
    fi
    workbench_w=$(( win_w * workbench_pct / 100 ))
    if [[ "$workbench_w" -gt 0 ]] \
        && _dashboard_board_columns_fit $(( win_w - workbench_w - 1 )) "$column_set"; then
        DASHBOARD_BOARD_WORKBENCH_W="$workbench_w"
    else
        DASHBOARD_BOARD_WORKBENCH_SKIP="fit"
    fi
}

# ---------------------------------------------------------------------------
# _dashboard_board_window_size <win_w> <win_h> <shell_pct> <shell_min_rows>
# Sets _DBW_SHELL_H to the shell row's height (0 without one), and _DBW_WIN_W
# and _DBW_WIN_H to the window dimensions the panes are sized against: the
# reported ones, floored to 45 columns and 16 rows (+ the shell row and its
# border).
# ---------------------------------------------------------------------------
_dashboard_board_window_size() {
    local win_w="$1" win_h="$2" shell_pct="$3" shell_min_rows="$4" min_h=16
    _DBW_SHELL_H=0
    if [[ "$shell_pct" -gt 0 ]]; then
        _DBW_SHELL_H=$(( win_h * shell_pct / 100 ))
        [[ "$_DBW_SHELL_H" -lt "$shell_min_rows" ]] && _DBW_SHELL_H="$shell_min_rows"
        min_h=$(( min_h + _DBW_SHELL_H + 1 ))
    fi
    [[ "$win_w" -lt 45 ]] && win_w=45
    [[ "$win_h" -lt "$min_h" ]] && win_h="$min_h"
    _DBW_WIN_W="$win_w"
    _DBW_WIN_H="$win_h"
}

# ---------------------------------------------------------------------------
# _dashboard_board_resize_panes <target> <win_w> <win_h> <shell_h>
#     <workbench_w> <column_set> <shell_pane> <workbench_pane> <legend_pane>
# The resize pass (SIZING in the header) — the one sizing both the builder
# and dashboard_reflow_board_window run: gives every pane of the board
# window <target> its absolute size. <win_w>/<win_h> are the floored window
# dimensions; <workbench_w> 0 sizes the board at the full width.
# <shell_pane> and <workbench_pane> are pane indices, empty for none.
# Sets DASHBOARD_BOARD_LAYOUT_NOTICE to the degraded-layout NOTICE line, or
# "" when every column keeps the floor, and records the same text on the
# window as @pgai_board_notice.
# ---------------------------------------------------------------------------
_dashboard_board_resize_panes() {
    local target="$1" win_w="$2" win_h="$3" shell_h="$4" workbench_w="$5"
    local column_set="$6" shell_pane="$7" workbench_pane="$8" legend_pane="$9"
    local legend_h=1 board_w="$win_w" content_h i count

    if [[ "$workbench_w" -gt 0 ]]; then
        board_w=$(( win_w - workbench_w - 1 ))
    fi
    _dashboard_board_column_widths "$board_w" "$column_set"

    DASHBOARD_BOARD_LAYOUT_NOTICE=""
    if [[ "$_DBW_COLUMNS_DEGRADED" -eq 1 ]]; then
        count="${#_DBW_COLUMN_WIDTHS[@]}"
        DASHBOARD_BOARD_LAYOUT_NOTICE="NOTICE: ${target} has a ${board_w}-column board region, under the $(( count * DASHBOARD_BOARD_COLUMN_FLOOR + count - 1 )) its ${count} columns need to keep ${DASHBOARD_BOARD_COLUMN_FLOOR} columns each; the columns share the width evenly, at least ${_DBW_COLUMN_WIDTHS[$(( count - 1 ))]} columns each."
    fi
    tmux set-option -w -t "$target" @pgai_board_notice "$DASHBOARD_BOARD_LAYOUT_NOTICE"

    content_h=$(( win_h - legend_h - 1 ))
    if [[ "$shell_h" -gt 0 ]]; then
        content_h=$(( content_h - shell_h - 1 ))
    fi
    [[ "$content_h" -lt 14 ]] && content_h=14

    # The workbench goes first: fixing its width fixes the board region's,
    # which the column resizes below then divide up.
    if [[ -n "$workbench_pane" ]]; then
        tmux resize-pane -t "${target}.${workbench_pane}" \
            -x "$workbench_w" -y "$(( win_h - legend_h - 1 ))"
    fi
    for i in "${!_DBW_COLUMN_WIDTHS[@]}"; do
        tmux resize-pane -t "${target}.${i}" -x "${_DBW_COLUMN_WIDTHS[$i]}" -y "$content_h"
    done
    if [[ -n "$shell_pane" ]]; then
        tmux resize-pane -t "${target}.${shell_pane}" -x "$board_w" -y "$shell_h"
    fi
    tmux resize-pane -t "${target}.${legend_pane}" -x "$win_w" -y "$legend_h"
}

dashboard_build_board_window() {
    local session="$1" window="$2" shell_pct="${3:-0}" shell_min_rows="${4:-3}"
    local workbench_pct="${5:-0}" workbench_min_cols="${6:-0}"
    local column_set="${7:-split}"
    local target="${session}:${window}"

    DASHBOARD_BOARD_SHELL_PANE=""
    DASHBOARD_BOARD_WORKBENCH_PANE=""
    DASHBOARD_BOARD_LEGEND_PANE=""
    DASHBOARD_BOARD_LAYOUT_NOTICE=""

    if ! _dashboard_board_column_count "$column_set"; then
        if ! declare -F pgai_emit_error_contract >/dev/null 2>&1; then
            # shellcheck source=../../lib/error_contract.sh
            source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../lib/error_contract.sh"
        fi
        pgai_emit_error_contract \
            "dashboard-board-window.unknown-column-set" \
            "dashboard_build_board_window was asked for column set '${column_set}' for ${target}; it builds only 'split' (3 + 5 columns) or 'merged' (3 + 1 columns), so no panes were created" \
            "dashboard_build_board_window '${session}' '${window}' '${shell_pct}' '${shell_min_rows}' '${workbench_pct}' '${workbench_min_cols}' merged"
        # BLOCK-WORTHY: building a column set other than the one asked
        # for would hand the caller panes its column dispatch does not
        # match — column commands landing in the shell, workbench or
        # legend panes — so refuse before any split (team/SOP-core.md,
        # "Gates and Refusals").
        # CENSUS-ANCHOR: dashboard-board-window-column-set-01
        return 1
    fi
    local column_count="$_DBW_COLUMN_COUNT"

    local win_w win_h
    win_w="$(tmux display-message -t "$target" -p '#{window_width}')"
    win_h="$(tmux display-message -t "$target" -p '#{window_height}')"
    win_w="${win_w:-120}"
    win_h="${win_h:-24}"

    # Workbench guards: decided on the width tmux reports, before the floors
    # below, so each notice names the operator's real terminal width.
    dashboard_board_workbench_decision \
        "$win_w" "$workbench_pct" "$workbench_min_cols" "$column_set"
    local workbench_w="$DASHBOARD_BOARD_WORKBENCH_W"
    case "$DASHBOARD_BOARD_WORKBENCH_SKIP" in
        narrow)
            echo "NOTICE: ${target} is ${win_w} columns wide, under the ${workbench_min_cols}-column workbench threshold; workbench pane not created, the board keeps the full width." >&2
            ;;
        fit)
            echo "NOTICE: ${target} is ${win_w} columns wide, too narrow for its ${column_count} board columns to keep ${DASHBOARD_BOARD_COLUMN_FLOOR} columns each beside a ${workbench_pct}% workbench; workbench pane not created, the board keeps the full width." >&2
            ;;
    esac

    local legend_h=1
    _dashboard_board_window_size "$win_w" "$win_h" "$shell_pct" "$shell_min_rows"
    local shell_h="$_DBW_SHELL_H"
    win_w="$_DBW_WIN_W"
    win_h="$_DBW_WIN_H"

    # --- Splits: create the panes; sizes are corrected by the resize pass ---
    # Legend first, as the bottom row of the full window:
    #   0=content, 1=legend
    tmux split-window -t "${target}.0" -v -l "$legend_h"
    # Workbench column off the right of everything above the legend, so it
    # spans the board and the shell row (operator only):
    #   0=board region, 1=workbench, 2=legend
    if [[ "$workbench_w" -gt 0 ]]; then
        tmux split-window -t "${target}.0" -h -l "${workbench_pct}%"
    fi
    # Shell row under the board, inside the board region (operator only):
    #   0=content, 1=shell, [2=workbench,] 2 or 3=legend
    if [[ "$shell_h" -gt 0 ]]; then
        tmux split-window -t "${target}.0" -v -l "$shell_h"
    fi
    # Board content → left and right regions (split ~40/60, merged ~75/25;
    # the resize pass sets the real widths). Each split inserts its new pane
    # directly after the pane it targets, pushing the shell, workbench and
    # legend panes one index further down every time.
    tmux split-window -t "${target}.0" -h -l 60%
    # Left region → BUGS | PRIORITIES | REQUIREMENTS
    tmux split-window -t "${target}.0" -h -l 67%
    tmux split-window -t "${target}.1" -h -l 50%
    # Right region (now pane 3): AGENTS as it stands for merged; for split
    # → PM | CODER | WRITER | TESTER | CM
    if [[ "$column_set" == "split" ]]; then
        tmux split-window -t "${target}.3" -h -l 80%
        tmux split-window -t "${target}.4" -h -l 75%
        tmux split-window -t "${target}.5" -h -l 67%
        tmux split-window -t "${target}.6" -h -l 50%
    fi

    local next_pane="$column_count"
    if [[ "$shell_h" -gt 0 ]]; then
        DASHBOARD_BOARD_SHELL_PANE="$next_pane"
        next_pane=$(( next_pane + 1 ))
    fi
    if [[ "$workbench_w" -gt 0 ]]; then
        DASHBOARD_BOARD_WORKBENCH_PANE="$next_pane"
        next_pane=$(( next_pane + 1 ))
    fi
    DASHBOARD_BOARD_LEGEND_PANE="$next_pane"

    # --- Absolute sizes (see SIZING in the header) ---
    _dashboard_board_resize_panes "$target" "$win_w" "$win_h" "$shell_h" \
        "$workbench_w" "$column_set" "$DASHBOARD_BOARD_SHELL_PANE" \
        "$DASHBOARD_BOARD_WORKBENCH_PANE" "$DASHBOARD_BOARD_LEGEND_PANE"
    if [[ -n "$DASHBOARD_BOARD_LAYOUT_NOTICE" ]]; then
        echo "$DASHBOARD_BOARD_LAYOUT_NOTICE" >&2
    fi

    # --- What dashboard_reflow_board_window needs, kept on the window ---
    local workbench_pane_id="" size
    if [[ -n "$DASHBOARD_BOARD_WORKBENCH_PANE" ]]; then
        workbench_pane_id="$(tmux display-message -p \
            -t "${target}.${DASHBOARD_BOARD_WORKBENCH_PANE}" '#{pane_id}')"
    fi
    size="$(tmux display-message -t "$target" -p '#{window_width}x#{window_height}')"
    tmux set-option -w -t "$target" @pgai_board_column_set "$column_set" ';' \
        set-option -w -t "$target" @pgai_board_shell_pct "$shell_pct" ';' \
        set-option -w -t "$target" @pgai_board_shell_min_rows "$shell_min_rows" ';' \
        set-option -w -t "$target" @pgai_board_workbench_pct "$workbench_pct" ';' \
        set-option -w -t "$target" @pgai_board_workbench_min_cols "$workbench_min_cols" ';' \
        set-option -w -t "$target" @pgai_board_workbench_pane "$workbench_pane_id" ';' \
        set-option -w -t "$target" @pgai_board_size "$size"
}

# ---------------------------------------------------------------------------
# _dashboard_board_rejoin_workbench <session_id> <window_id>
#     <workbench_pane_id> <anchor_index> <legend_index>
# Moves a parked workbench pane back into <window_id> with the builder's
# tree: the legend (pane <legend_index>) is lifted into a window of its own
# in <session_id>, the workbench joins at full height on the right, and the
# legend joins at full width beneath — the workbench straight after pane
# <anchor_index> (the shell row, or the last column), the legend straight
# after it. Returns 1, with the legend put back, when the workbench cannot
# join.
# ---------------------------------------------------------------------------
_dashboard_board_rejoin_workbench() {
    local session_id="$1" window_id="$2" workbench_pane_id="$3"
    local anchor_index="$4" legend_index="$5"
    local anchor_id legend_id
    anchor_id="$(tmux display-message -p -t "${window_id}.${anchor_index}" '#{pane_id}')" || return 1
    legend_id="$(tmux display-message -p -t "${window_id}.${legend_index}" '#{pane_id}')" || return 1
    tmux break-pane -d -s "$legend_id" -t "${session_id}:" || return 1
    if ! tmux join-pane -d -f -h -s "$workbench_pane_id" -t "$anchor_id"; then
        tmux join-pane -d -f -v -s "$legend_id" -t "$anchor_id"
        return 1
    fi
    tmux join-pane -d -f -v -s "$legend_id" -t "$workbench_pane_id"
}

# ---------------------------------------------------------------------------
# dashboard_reflow_board_window <window>
# See the header (Public API and RESIZE). Every tmux failure ends the
# reflow quietly: it runs from a hook, with no one to read an error.
# ---------------------------------------------------------------------------
dashboard_reflow_board_window() {
    local target="${1:?dashboard_reflow_board_window: window required}"
    local state
    state="$(tmux display-message -p -t "$target" \
        '#{session_id}|#{window_id}|#{window_width}|#{window_height}|#{window_zoomed_flag}|#{window_panes}|#{@pgai_board_column_set}|#{@pgai_board_shell_pct}|#{@pgai_board_shell_min_rows}|#{@pgai_board_workbench_pct}|#{@pgai_board_workbench_min_cols}|#{@pgai_board_workbench_pane}' \
        2>/dev/null)" || return 0
    local session_id window_id win_w win_h zoomed pane_count column_set shell_pct
    local shell_min_rows workbench_pct workbench_min_cols workbench_pane_id
    IFS='|' read -r session_id window_id win_w win_h zoomed pane_count column_set shell_pct \
        shell_min_rows workbench_pct workbench_min_cols workbench_pane_id <<< "$state"

    # Only a window the builder recorded is re-laid out — with or without a
    # workbench; a zoomed one is left as it is until it is unzoomed (itself
    # a layout change).
    _dashboard_board_column_count "$column_set" || return 0
    [[ "$zoomed" == "1" ]] && return 0
    tmux set-option -w -t "$window_id" @pgai_board_size "${win_w}x${win_h}" || return 0

    local column_count="$_DBW_COLUMN_COUNT"
    dashboard_board_workbench_decision \
        "$win_w" "$workbench_pct" "$workbench_min_cols" "$column_set"
    local wanted_w="$DASHBOARD_BOARD_WORKBENCH_W"
    _dashboard_board_window_size "$win_w" "$win_h" "$shell_pct" "$shell_min_rows"

    # Where the workbench is now: in this window, parked, gone (its shell
    # exited), or never built (no pane id recorded). The pane count must be
    # the build's, with or without the workbench; anything else is the
    # operator's own layout.
    local workbench_window="" built_panes=$(( column_count + 1 )) in_window=0
    if [[ -n "$workbench_pane_id" ]]; then
        workbench_window="$(tmux display-message -p -t "$workbench_pane_id" '#{window_id}' 2>/dev/null)" \
            || workbench_window=""
    fi
    [[ "$_DBW_SHELL_H" -gt 0 ]] && built_panes=$(( built_panes + 1 ))
    if [[ "$workbench_window" == "$window_id" ]]; then
        in_window=1
        [[ "$pane_count" -eq $(( built_panes + 1 )) ]] || return 0
    else
        [[ "$pane_count" -eq "$built_panes" ]] || return 0
    fi

    local shell_pane="" anchor_index=$(( column_count - 1 ))
    if [[ "$_DBW_SHELL_H" -gt 0 ]]; then
        shell_pane="$column_count"
        anchor_index="$column_count"
    fi
    if [[ "$in_window" -eq 1 && "$wanted_w" -eq 0 ]]; then
        tmux break-pane -d -s "$workbench_pane_id" -t "${session_id}:" \
            -n "$DASHBOARD_BOARD_PARKED_WORKBENCH_WINDOW" || return 0
        in_window=0
    elif [[ "$in_window" -eq 0 && "$wanted_w" -gt 0 && -n "$workbench_window" ]]; then
        _dashboard_board_rejoin_workbench "$session_id" "$window_id" "$workbench_pane_id" \
            "$anchor_index" "$(( built_panes - 1 ))" && in_window=1
    fi

    local workbench_w=0 workbench_pane="" legend_pane=$(( built_panes - 1 ))
    if [[ "$in_window" -eq 1 ]]; then
        workbench_w="$wanted_w"
        workbench_pane="$(( anchor_index + 1 ))"
        legend_pane="$built_panes"
    fi
    _dashboard_board_resize_panes "$window_id" "$_DBW_WIN_W" "$_DBW_WIN_H" "$_DBW_SHELL_H" \
        "$workbench_w" "$column_set" "$shell_pane" "$workbench_pane" "$legend_pane" \
        2>/dev/null || return 0
}

# ---------------------------------------------------------------------------
# dashboard_install_board_reflow_hook <window> <reflow_script>
# See the header. The hook is set on the window itself (`set-hook -w`):
# window-layout-changed is a window option, so no other window or session
# on the server is touched, and it goes when the window does.
# ---------------------------------------------------------------------------
dashboard_install_board_reflow_hook() {
    local target="${1:?dashboard_install_board_reflow_hook: window required}"
    local reflow_script="${2:?dashboard_install_board_reflow_hook: reflow_script required}"
    local window_id
    window_id="$(tmux display-message -p -t "$target" '#{window_id}')" || return 1
    # shellcheck disable=SC2016  # '#{...}' is a tmux format, not a shell expansion
    tmux set-hook -w -t "$window_id" window-layout-changed \
        "if-shell -F -t '${window_id}' '#{&&:#{?window_zoomed_flag,0,1},#{!=:#{window_width}x#{window_height},#{@pgai_board_size}}}' \"run-shell -b '${reflow_script} --window ${window_id}'\""
}
