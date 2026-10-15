#!/usr/bin/env bash
# team/scripts/lib/dashboard/column_render_board.sh
#
# Shared per-tick board snapshot for the mixed-project (--all-projects)
# board columns: the gather that resolves and classifies every registered
# project's items runs ONCE per tick for all eight columns (bugs,
# priorities, requirements, pm, coder, writer, tester, cm), and every
# column renders its own slice of that one snapshot.
#
# WHY
# ----
# Each board column is its own column-render.sh process on the dashboard's
# refresh cadence. Gathering per column meant eight independent passes over
# every project per tick for one visible board window. The snapshot is
# keyed by the board, not by the column: the first column to tick builds
# it, the rest read it.
#
# FLOW (column_render_board_render)
# -----------------------------------
#   1. Fresh snapshot: render this column's slice from it (one Python
#      process: pgai_agent_kanban.dashboard.column_render board-render).
#   2. Miss (missing, unreadable, stale, or built for the other colour
#      mode):
#        - another column is building it right now (the build lock is held
#          by a live process): wait for that build to finish, then render
#          from its snapshot;
#        - otherwise take the build lock (lib/lock_provenance.sh, holder
#          "dashboard", op "board-snapshot-build", task "none"), re-read
#          the snapshot if its file changed since this column looked (a
#          column may have built it meanwhile), and if it is still a miss
#          build it: column_render_gather_board_multi
#          exports every column's per-project lists and board-build
#          classifies them all, writes the snapshot atomically, counts the
#          gather in <snapshot>.stats, pre-renders this column and every
#          column --board-widths names (BOARD_WIDTHS) beside it, and prints
#          this column.
#   3. Fallback: when the snapshot cannot be used at all (the lock is
#      refused, a wait outlasts its budget of one TTL -- a build slower
#      than a tick is not worth waiting for -- or the build fails) the column
#      gathers and renders itself directly -- the pre-snapshot path -- and
#      says so on stderr. A pane is never blanked.
#
# This flow is column-render.sh's full path. Ahead of it, the board fast
# path (column_render_board_serve.sh) serves a column whose pre-render is
# current, and builds the tick itself in one Python process when the
# snapshot is missing or stale; it hands over to this flow whenever it
# cannot. When the fast path already waited out a live builder, this flow
# does not wait a second time (_COLUMN_RENDER_BOARD_WAIT_SPENT) and renders
# directly.
#
# Narration is stderr only, so stdout is byte-identical on every path. A
# column that builds narrates the miss it found exactly as lib/snapshot.sh
# does ("[dashboard-snapshot] <label>: no fresh snapshot (missing or
# unreadable) -- rendering directly", or the debug-volume
# "[dashboard-snapshot-debug] ... (routine expiry)" for a stale one); a
# column that reads or waits narrates nothing.
#
# The snapshot lives in lib/snapshot.sh's directory
# ($(pgai_temp_subdir dashboard)/snapshots, removed by kill.sh on teardown)
# as board-<key hash>.json, keyed by the kanban root, the colour mode,
# DASHBOARD_MAX_ROWS and DASHBOARD_STATUS_GLYPH -- everything besides the
# files themselves that the gather reads, plus the project-tag glyph the
# pre-rendered columns beside it are drawn with. Its staleness bound is
# dashboard_snapshot_ttl_seconds. The pre-rendered columns are
# board-<key hash>.<column type>.w<width>.txt
# (column_render_board_prerender_path); they are drawn with the build's
# [dashboard] layout values, so a layout edit in kanban.cfg reaches the
# panes with the next build, at most one TTL later.
#
# Public entry points
# --------------------
#   column_render_board_render
#       Print this column's pane (see FLOW). Returns 0.
#   column_render_board_snapshot_path
#       Echo the board snapshot path for this kanban root, colour mode,
#       DASHBOARD_MAX_ROWS and DASHBOARD_STATUS_GLYPH.
#   column_render_board_prerender_path <snapshot> <column type> <width>
#       Echo where a build pre-renders <column type> at <width> -- the
#       name pgai_agent_kanban.dashboard.column_render.board_snapshot.
#       prerender_path writes.
#
# Contract: called by column-render.sh in --all-projects mode for a known
# board column type, after argument parsing, config load and colour setup;
# reads KANBAN_ROOT, SUBCOMMAND, COLUMN_TYPE, POSARGS, PANE_LABEL,
# BOARD_WIDTHS, USE_COLOR, DASHBOARD_MAX_ROWS, DASHBOARD_STATUS_GLYPH and
# everything column_render_frame.sh reads.
# Dependencies (sourced by column-render.sh before this file):
# dashboard/lib/snapshot.sh, column_render_data_multi.sh,
# column_render_data.sh, column_render_frame.sh, column_render_cells.sh,
# lib/pp_run_ops.sh. lib/lock_provenance.sh is sourced on first need.

# This file's own directory (to source lib/lock_provenance.sh on first need),
# without a subshell -- the board fast path sources this file on every tick.
_COLUMN_RENDER_BOARD_LIB_DIR="${BASH_SOURCE[0]%/*}"
[[ "$_COLUMN_RENDER_BOARD_LIB_DIR" == "${BASH_SOURCE[0]}" ]] && _COLUMN_RENDER_BOARD_LIB_DIR="."

column_render_board_snapshot_path() {
    local key
    key="board-snapshot ${KANBAN_ROOT} color=${USE_COLOR} max_rows=${DASHBOARD_MAX_ROWS:-} glyph=${DASHBOARD_STATUS_GLYPH:-}"
    printf '%s/board-%s.json\n' "$(dashboard_snapshot_dir)" "$(_dashboard_snapshot_hash "$key")"
}

column_render_board_prerender_path() {
    printf '%s.%s.w%s.txt\n' "${1%.json}" "$2" "$3"
}

# _column_render_board_py <mode> <snapshot> [extra args...]
#     Run a board mode of the column_render CLI with this column's render
#     arguments (the same ones column_render_render_multi_project passes).
_column_render_board_py() {
    local mode="$1" snapshot="$2"
    shift 2
    column_render_palette_args
    pp_run_ops pgai_agent_kanban.dashboard.column_render "$mode" \
        "--snapshot=${snapshot}" "$@" \
        "--subcommand=${SUBCOMMAND}" \
        "--column-type=${COLUMN_TYPE}" \
        "--width=${WIDTH}" \
        "--status-glyph=${DASHBOARD_STATUS_GLYPH:-}" \
        "--min-rows-per-column=${DASHBOARD_MIN_ROWS_PER_COLUMN}" \
        "--max-rows-per-column=${DASHBOARD_MAX_ROWS_PER_COLUMN}" \
        "--min-rows-per-project=${DASHBOARD_MIN_ROWS_PER_PROJECT}" \
        "--max-rows-per-project=${DASHBOARD_MAX_ROWS_PER_PROJECT}" \
        "${_COLUMN_RENDER_PALETTE_ARGS[@]}"
}

# _column_render_board_read <snapshot> <ttl>
#     Print this column from a fresh snapshot and return 0; otherwise print
#     nothing and return 3 (missing/unusable) or 4 (stale).
_column_render_board_read() {
    local snapshot="$1" ttl="$2" out rc
    out="$(_column_render_board_py board-render "$snapshot" "--ttl=${ttl}")" && rc=0 || rc=$?
    if [[ "$rc" -eq 0 ]]; then
        printf '%s\n' "$out"
        return 0
    fi
    [[ "$rc" -eq 4 ]] && return 4
    return 3
}

# _column_render_board_builder_alive <lock_file>
#     True when the build lock records a holder whose pid is alive.
_column_render_board_builder_alive() {
    local lock_file="$1" content
    [[ -e "$lock_file" ]] || return 1
    content="$(pgai_lock_read_provenance "$lock_file")"
    [[ "$(pgai_lock_pid_liveness_verdict "$content")" == "held" ]]
}

# _column_render_board_identity <snapshot>
#     Echo the snapshot file's inode (empty when absent). Every build
#     renames a new file into place, so a changed identity means a build
#     finished.
_column_render_board_identity() {
    stat -c '%i' "$1" 2>/dev/null || true
}

# _column_render_board_wait <lock_file> <ttl>
#     Wait while a live builder holds <lock_file>, at most <ttl> seconds.
#     Returns 0 when the builder finished, 1 when the wait ran out.
_column_render_board_wait() {
    local lock_file="$1" ttl="$2"
    local deadline=$(( SECONDS + ttl ))
    while _column_render_board_builder_alive "$lock_file"; do
        (( SECONDS >= deadline )) && return 1
        sleep 0.1
    done
    return 0
}

# _column_render_board_direct <reason>
#     The pre-snapshot path: gather and render this column alone.
_column_render_board_direct() {
    local reason="$1" out
    echo "[dashboard-snapshot] column-render ${PANE_LABEL:-$COLUMN_TYPE}: ${reason} -- rendering directly" >&2
    out="$(column_render_gather_data multi; column_render_render_multi_project)" || return 0
    printf '%s\n' "$out"
}

# _column_render_board_narrate_miss <read status> <ttl>
_column_render_board_narrate_miss() {
    local label="column-render ${PANE_LABEL:-$COLUMN_TYPE}"
    if [[ "$1" -eq 4 ]]; then
        echo "[dashboard-snapshot-debug] ${label}: snapshot older than ${2}s -- rendering directly (routine expiry)" >&2
    else
        echo "[dashboard-snapshot] ${label}: no fresh snapshot (missing or unreadable) -- rendering directly" >&2
    fi
}

column_render_board_render() {
    local snapshot ttl lock_file read_rc seen
    snapshot="$(column_render_board_snapshot_path)"
    ttl="$(dashboard_snapshot_ttl_seconds)"
    lock_file="${snapshot}.lock"

    # 1. A fresh snapshot: render this column's slice.
    seen="$(_column_render_board_identity "$snapshot")"
    _column_render_board_read "$snapshot" "$ttl" && return 0 || read_rc=$?

    if ! declare -F pgai_lock_acquire >/dev/null 2>&1; then
        # shellcheck source=../lock_provenance.sh
        source "${_COLUMN_RENDER_BOARD_LIB_DIR}/../lock_provenance.sh"
    fi

    # 2a. Another column is building it: wait, then read what it built.
    # The fast path's wait, when it already ran out, is not repeated.
    if _column_render_board_builder_alive "$lock_file"; then
        if [[ "${_COLUMN_RENDER_BOARD_WAIT_SPENT:-}" != true ]] \
            && _column_render_board_wait "$lock_file" "$ttl"; then
            _column_render_board_read "$snapshot" "$ttl" && return 0
        fi
        _column_render_board_direct "the board snapshot another column was building is not available"
        return 0
    fi

    # 2b. Become the builder. The lock is the dashboard's own, not a
    # project's: a contended attempt is narrated on stderr but never
    # recorded against an ambient project.
    local lock_fd="" acquire_rc=0
    PGAI_PROJECT_NAME="" pgai_lock_acquire "$lock_file" dashboard "$BASHPID" \
        board-snapshot-build none lock_fd || acquire_rc=$?
    if [[ "$acquire_rc" -eq 1 ]]; then
        # Lost the race to another column that started building just now.
        if _column_render_board_wait "$lock_file" "$ttl"; then
            _column_render_board_read "$snapshot" "$ttl" && return 0
        fi
        _column_render_board_direct "the board snapshot another column was building is not available"
        return 0
    elif [[ "$acquire_rc" -ne 0 ]]; then
        _column_render_board_direct "the board snapshot build lock ${lock_file} was refused"
        return 0
    fi

    # Holding the lock: a column may have finished a build while this one
    # was deciding -- if the file changed, read it before building.
    if [[ "$(_column_render_board_identity "$snapshot")" != "$seen" ]] \
        && _column_render_board_read "$snapshot" "$ttl"; then
        pgai_lock_release "$lock_file" "$lock_fd"
        return 0
    fi

    _column_render_board_narrate_miss "$read_rc" "$ttl"
    local out build_rc=0
    out="$(column_render_gather_board_multi
           _column_render_board_py board-build "$snapshot" "--prerender=${BOARD_WIDTHS:-}")" || build_rc=$?
    pgai_lock_release "$lock_file" "$lock_fd"
    if [[ "$build_rc" -ne 0 ]]; then
        _column_render_board_direct "the board snapshot build failed (exit ${build_rc})"
        return 0
    fi
    printf '%s\n' "$out"
    return 0
}
