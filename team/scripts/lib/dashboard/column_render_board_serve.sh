#!/usr/bin/env bash
# team/scripts/lib/dashboard/column_render_board_serve.sh
#
# Board column fast path for column-render.sh: an --all-projects board
# column (one of the eight types, with the subcommand that type takes) is
# served from the current tick before column-render.sh loads its full
# library set.
#
# WHY
# ----
# A visible board window runs eight column-render.sh processes per tick.
# The shared snapshot (column_render_board.sh) already gathers once per
# tick, but every column still paid for the driver's full start-up (a
# dozen libraries, the kanban.cfg reads) plus a Python process to draw its
# slice, and the column that built the snapshot paid for a bash gather that
# started several processes per project. Here the column that finds the
# tick missing builds it in ONE Python process -- gathered in process
# (pgai_agent_kanban.dashboard.column_render.board_gather), written as the
# snapshot, and every column the panes declare with --board-widths
# pre-rendered beside it -- and every other column of the tick prints its
# pre-rendered file without starting Python at all.
#
# FLOW (column_render_board_serve)
# ---------------------------------
#   1. Resolve the inputs of the snapshot key (colour mode,
#      DASHBOARD_MAX_ROWS, DASHBOARD_STATUS_GLYPH) and this pane's width,
#      exactly as the full path does (column_render_config.sh,
#      _column_render_multi_width).
#   2. Current pre-render: the snapshot is fresh (younger than
#      dashboard_snapshot_ttl_seconds, compared to the microsecond as the
#      Python reader compares it) and this column's pre-render at this
#      width exists and is no older than the snapshot. Print the label
#      header and the pre-render; done.
#   3. Fresh snapshot without a pre-render for this width (a width the
#      panes did not declare, or a snapshot replaced since its build):
#      hand over to the full path, which renders this column from the
#      snapshot -- or rebuilds it when it cannot be read.
#   4. Missing or stale snapshot: build the tick. Under the same build lock
#      the full path takes (lib/lock_provenance.sh, holder "dashboard", op
#      "board-snapshot-build", task "none"): a live builder is waited for
#      (at most one TTL) and its pre-render printed; otherwise the lock is
#      taken, the pre-render re-checked, and board-build --gather=tree
#      gathers, writes the snapshot, counts the gather, pre-renders this
#      column and every --board-widths column, and prints this one. The
#      miss is narrated on stderr as the full path narrates it.
#   Whenever a step cannot complete -- the lock is refused, a wait runs
#   out, the in-process gather does not cover the tree, the build fails --
#   the function returns 1 and column-render.sh's full path runs exactly as
#   it does without this file. A wait that ran out sets
#   _COLUMN_RENDER_BOARD_WAIT_SPENT so the full path does not wait a second
#   time.
#
# Output order. Step 4 prints the label header first, before any lock
# narration or miss narration on stderr -- where the full path prints it --
# and sets _COLUMN_RENDER_HEADER_PRINTED, so a hand-over does not print it
# twice. Steps 2 and 3 print nothing before they succeed or hand over.
#
# Public entry point
# -------------------
#   column_render_board_serve
#       Returns 0 when it printed this pane (label header included), 1 when
#       column-render.sh should continue down its full path.
#
# Contract: called by column-render.sh right after column_render_parse_args
# (column_render_args.sh) and before the driver's own library loads; reads
# what that parser sets (KANBAN_ROOT, SUBCOMMAND, COLUMN_TYPE, POSARGS,
# PANE_LABEL, ALL_PROJECTS, BOARD_WIDTHS and the help/error flags). Sources
# its own dependencies from this tree: dashboard/lib/snapshot.sh (which
# brings lib/temp.sh and lib/ini_parser.sh), column_render_config.sh,
# column_render_data_multi.sh and column_render_board.sh for every call,
# and lib/script_common.sh, lib/status_glyphs.sh, lib/pp_run_ops.sh,
# column_render_cells.sh and lib/lock_provenance.sh only to build.

# This file's own directory, without a subshell: every reader pays for each
# process this path starts, so it starts as few as it can.
_COLUMN_RENDER_BOARD_SERVE_LIB_DIR="${BASH_SOURCE[0]%/*}"
[[ "$_COLUMN_RENDER_BOARD_SERVE_LIB_DIR" == "${BASH_SOURCE[0]}" ]] && _COLUMN_RENDER_BOARD_SERVE_LIB_DIR="."

# _column_render_board_serve_load
#     Source what every call needs (the snapshot key, its path, the TTL).
_column_render_board_serve_load() {
    local lib="${_COLUMN_RENDER_BOARD_SERVE_LIB_DIR}"
    # shellcheck source=../../dashboard/lib/snapshot.sh
    source "${lib}/../../dashboard/lib/snapshot.sh" || return 1
    # shellcheck source=column_render_config.sh
    source "${lib}/column_render_config.sh" || return 1
    # shellcheck source=column_render_data_multi.sh
    source "${lib}/column_render_data_multi.sh" || return 1
    # shellcheck source=column_render_board.sh
    source "${lib}/column_render_board.sh" || return 1
}

# _column_render_board_serve_load_builder
#     Source what a build needs on top: ansi_code, the deferral glyphs,
#     pp_run_ops, the palette arguments and the build lock.
_column_render_board_serve_load_builder() {
    local lib="${_COLUMN_RENDER_BOARD_SERVE_LIB_DIR}"
    # shellcheck source=../script_common.sh
    source "${lib}/../script_common.sh" || return 1
    # shellcheck source=../status_glyphs.sh
    source "${lib}/../status_glyphs.sh" || return 1
    # shellcheck source=../pp_run_ops.sh
    declare -F pp_run_ops >/dev/null || source "${lib}/../pp_run_ops.sh" || return 1
    # shellcheck source=column_render_cells.sh
    declare -F column_render_palette_args >/dev/null || source "${lib}/column_render_cells.sh" || return 1
    # shellcheck source=../lock_provenance.sh
    declare -F pgai_lock_acquire >/dev/null || source "${lib}/../lock_provenance.sh" || return 1
}

# _column_render_board_serve_mtimes <file>...
#     Set _CRBS_MTIMES to each file's mtime in microseconds since the epoch
#     (one stat call for all of them) and _CRBS_NOW_US to the current time
#     in the same unit; return 1 when any file is missing. Both stat's
#     "%.6Y" and EPOCHREALTIME carry six fractional digits, so stripping
#     every non-digit (including a locale's decimal comma) leaves
#     microseconds.
_column_render_board_serve_mtimes() {
    local out line
    _CRBS_MTIMES=()
    out="$(stat -c '%.6Y' -- "$@" 2>/dev/null)" || return 1
    while IFS= read -r line; do
        _CRBS_MTIMES+=("${line//[!0-9]/}")
    done <<< "$out"
    _CRBS_NOW_US="${EPOCHREALTIME//[!0-9]/}"
    [[ ${#_CRBS_MTIMES[@]} -eq $# ]]
}

# _column_render_board_serve_fresh <file> <ttl>
#     True when <file> exists and is younger than <ttl> seconds -- the
#     comparison board_snapshot.read_snapshot makes, to the microsecond.
_column_render_board_serve_fresh() {
    _column_render_board_serve_mtimes "$1" || return 1
    (( _CRBS_NOW_US - _CRBS_MTIMES[0] < $2 * 1000000 ))
}

# _column_render_board_serve_print <snapshot> <prerender> <ttl>
#     Print the label header and <prerender> when it is current (FLOW step
#     2); return 1, printing nothing, otherwise.
_column_render_board_serve_print() {
    local snapshot="$1" prerender="$2" ttl="$3" body=""
    _column_render_board_serve_mtimes "$snapshot" "$prerender" || return 1
    (( _CRBS_NOW_US - _CRBS_MTIMES[0] < ttl * 1000000 )) || return 1
    (( _CRBS_MTIMES[1] >= _CRBS_MTIMES[0] )) || return 1
    # Read the whole file with builtins (read stops at EOF with status 1),
    # then drop trailing newlines exactly as a $(...) capture of the
    # renderer's output does on the full path.
    { IFS= read -r -d '' body < "$prerender"; } 2>/dev/null || true
    body="${body%"${body##*[!$'\n']}"}"
    [[ -n "$body" ]] || return 1
    _column_render_board_serve_header
    printf '%s\n' "$body"
    return 0
}

# _column_render_board_serve_header
#     Print column-render.sh's neutral pane header (no per-column halt tint)
#     once: not when there is no label, and not again after it was printed.
_column_render_board_serve_header() {
    [[ -n "$PANE_LABEL" && "${_COLUMN_RENDER_HEADER_PRINTED:-}" != true ]] || return 0
    printf '=== %s ===\n' "$PANE_LABEL"
    _COLUMN_RENDER_HEADER_PRINTED=true
}

# _column_render_board_serve_build <snapshot> <prerender> <ttl>
#     FLOW step 4.
_column_render_board_serve_build() {
    local snapshot="$1" prerender="$2" ttl="$3"
    local lock_file="${snapshot}.lock"
    _column_render_board_serve_load_builder || return 1
    _column_render_board_serve_header

    if _column_render_board_builder_alive "$lock_file"; then
        if _column_render_board_wait "$lock_file" "$ttl"; then
            _column_render_board_serve_print "$snapshot" "$prerender" "$ttl" && return 0
            return 1
        fi
        _COLUMN_RENDER_BOARD_WAIT_SPENT=true
        return 1
    fi

    local lock_fd="" acquire_rc=0
    PGAI_PROJECT_NAME="" pgai_lock_acquire "$lock_file" dashboard "$BASHPID" \
        board-snapshot-build none lock_fd || acquire_rc=$?
    if [[ "$acquire_rc" -eq 1 ]]; then
        # Lost the race to a column that started building just now.
        if _column_render_board_wait "$lock_file" "$ttl"; then
            _column_render_board_serve_print "$snapshot" "$prerender" "$ttl" && return 0
            return 1
        fi
        _COLUMN_RENDER_BOARD_WAIT_SPENT=true
        return 1
    elif [[ "$acquire_rc" -ne 0 ]]; then
        return 1
    fi

    # Holding the lock: a column may have finished a build meanwhile.
    if _column_render_board_serve_print "$snapshot" "$prerender" "$ttl"; then
        pgai_lock_release "$lock_file" "$lock_fd"
        return 0
    fi
    if _column_render_board_serve_fresh "$snapshot" "$ttl"; then
        pgai_lock_release "$lock_file" "$lock_fd"
        return 1
    fi

    local read_rc=3
    [[ -e "$snapshot" ]] && read_rc=4
    _column_render_board_narrate_miss "$read_rc" "$ttl"
    column_render_config_layout
    column_render_config_ansi
    export GLYPH_DEFERRED_BUG GLYPH_DEFER_WARNING
    local out build_rc=0
    out="$(_column_render_board_py board-build "$snapshot" \
        "--gather=tree" "--kanban-root=${KANBAN_ROOT}" \
        "--dashboard-max-rows=${DASHBOARD_MAX_ROWS:-}" \
        "--prerender=${BOARD_WIDTHS:-}")" || build_rc=$?
    pgai_lock_release "$lock_file" "$lock_fd"
    [[ "$build_rc" -eq 0 && -n "$out" ]] || return 1
    printf '%s\n' "$out"
    return 0
}

column_render_board_serve() {
    [[ "${ALL_PROJECTS:-}" == "true" && "${COLUMN_RENDER_SHOW_HELP:-}" != "true" \
        && -z "${COLUMN_RENDER_ARG_ERROR:-}" ]] || return 1
    case "${COLUMN_TYPE}:${SUBCOMMAND}" in
        bugs:input|priorities:input|requirements:input|pm:queue|coder:queue|writer:queue|tester:queue|cm:queue) ;;
        *) return 1 ;;
    esac
    _column_render_board_serve_load || return 1

    column_render_config_color_mode
    column_render_config_max_rows
    column_render_config_status_glyph
    _column_render_multi_width

    local snapshot ttl prerender
    snapshot="$(column_render_board_snapshot_path)" || return 1
    ttl="$(dashboard_snapshot_ttl_seconds)"
    # column_render_board_prerender_path's name, built in place (no subshell).
    prerender="${snapshot%.json}.${COLUMN_TYPE}.w${WIDTH}.txt"

    _column_render_board_serve_print "$snapshot" "$prerender" "$ttl" && return 0
    _column_render_board_serve_fresh "$snapshot" "$ttl" && return 1
    _column_render_board_serve_build "$snapshot" "$prerender" "$ttl"
}
