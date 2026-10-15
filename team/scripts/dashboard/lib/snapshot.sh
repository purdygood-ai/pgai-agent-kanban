#!/usr/bin/env bash
# team/scripts/dashboard/lib/snapshot.sh
#
# Per-tick render snapshot cache shared by twin dashboard views.
#
# WHY THIS EXISTS
# ----------------
# Several regions of the board render the same underlying data twice: the
# operator window's aggregated board columns and the visibility window's
# per-column panes run the literal same column-render.sh invocation (same
# subcommand/label/rows/width/--all-projects args) in two independent
# cadence-loop.sh processes, and a drill-N window's queues/progress panes
# re-scan the same project's tasks/ directory (via data.sh) that the
# overview window's per-project loop already scanned. Without sharing, every
# tick pays for the same scan twice (or more), and the two renders can
# disagree when their scans land on either side of a state change (a task
# flips WORKING -> DONE between the two independent scans).
#
# This library gives every twin ONE shared per-tick snapshot to read instead
# of two independent scans that merely happen to agree: the first pane to
# tick within the staleness bound computes and caches; every other twin
# within that bound reads the cached bytes verbatim. Parity is by
# construction (same cached string), not by coincidence.
#
# STALENESS BOUND
# ----------------
# A snapshot older than its TTL is treated exactly like a missing one: it is
# never read, the caller re-computes directly, and the miss is narrated to
# stderr. The TTL is resolved by dashboard_snapshot_ttl_seconds() below:
#   1. PGAI_DASHBOARD_SNAPSHOT_TTL_SECONDS (env override, testing/operator)
#   2. kanban.cfg [dashboard] snapshot_ttl_seconds
#   3. Default: 5 seconds (matches the dashboard's default active-window
#      refresh interval -- PGAI_DASHBOARD_REFRESH_SECONDS' own default).
# No pane ever renders a snapshot older than this bound silently.
#
# MISSING / PARTIAL / UNREADABLE DEGRADE
# ----------------------------------------
# dashboard_snapshot_render() is the entry point every caller uses. It never
# blanks a pane and never propagates a crash from a stale cache: a missing,
# stale, or unreadable snapshot falls back to computing the content directly
# (the exact pre-snapshot code path) and narrates the fallback to STDERR
# ONLY -- never stdout. Because narration never touches stdout, a cache MISS
# renders byte-identical stdout to a cache HIT (and to the pre-snapshot
# direct-render path), which is what keeps existing render goldens intact
# after this library was introduced.
#
# NARRATION VOLUME
# ------------------
# A cache miss caused by ordinary TTL expiry is the design's routine case:
# with a cadence slower than the TTL, the first twin to tick every cycle
# finds no fresh snapshot BY DESIGN, not because anything is wrong. Stamping
# that at the same volume as a real problem turns a pane's stderr (now
# routed to its debug log by cadence.sh -- see cadence-loop.sh /
# lib/cadence.sh) into a per-tick metronome that buries genuine signal.
# dashboard_snapshot_read() below distinguishes the reason with its exit
# code (0 hit, 1 missing/unreadable, 2 stale-past-TTL) so
# dashboard_snapshot_render() can tag the two cases differently:
#   - stale-past-TTL:        "[dashboard-snapshot-debug]" -- routine, demoted
#   - missing or unreadable: "[dashboard-snapshot]"        -- normal volume
# A failed dashboard_snapshot_write() (a twin's cache refresh could not be
# persisted) also narrates at normal volume -- callers depending on the
# cache next tick need to know it is not being kept warm.
#
# ATOMIC WRITES
# --------------
# dashboard_snapshot_write() writes to a temp file in the SAME directory as
# the final snapshot path (mktemp "${path}.XXXXXX") and then renames it into
# place with `mv -f` -- a single rename(2) syscall on the same filesystem.
# A concurrent reader therefore always observes either the complete previous
# snapshot or the complete new one; a half-written file is never observable
# (see team/tests/unit/test_dashboard_snapshot.py for the concurrent
# write/read proof).
#
# STORAGE / CLEANUP
# -------------------
# Snapshots live under $(pgai_temp_subdir dashboard)/snapshots -- the pgai
# temp root, never bare /tmp, never inside the kanban root. kill.sh already
# runs `pgai_temp_cleanup "$(pgai_temp_subdir dashboard)"` on session
# teardown, which removes the whole dashboard/ subtree (snapshots included)
# with no additional wiring needed here.
#
# PUBLIC ENTRY POINTS
# ---------------------
#   dashboard_snapshot_ttl_seconds
#       Echo the resolved staleness bound in seconds (positive integer).
#   dashboard_snapshot_render <key> <ttl_seconds> <label> -- <command...>
#       Print the rendered content for <key>: a fresh cached snapshot when
#       one exists, otherwise run <command...>, print its output, and
#       (best-effort) refresh the cache. This is the function every caller
#       (data.sh readers, column-render.sh) uses; the lower-level
#       read/write/path functions below exist mainly for its own use and
#       for direct test coverage.
#   dashboard_snapshot_read <key> <ttl_seconds>
#       Print cached content and return 0 on a fresh hit. On a miss, print
#       nothing and return 1 (missing or unreadable/partial) or 2 (present
#       but older than <ttl_seconds>) -- the two codes dashboard_snapshot_render
#       uses to pick a narration volume; see "NARRATION VOLUME" above.
#   dashboard_snapshot_write <key>
#       Read content from stdin, write it atomically to <key>'s snapshot path.
#   dashboard_snapshot_path <key>
#       Echo the resolved on-disk path for <key> (does not create the file).
#   dashboard_snapshot_dir
#       Echo (and create) the snapshot storage directory.

# Include guard: safe to source multiple times.
[[ -n "${_PGAI_DASHBOARD_SNAPSHOT_SH_LOADED:-}" ]] && return 0
_PGAI_DASHBOARD_SNAPSHOT_SH_LOADED=1

_PGAI_DASHBOARD_SNAPSHOT_SH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Dependencies: pgai_temp_subdir (team/scripts/lib/temp.sh) and read_ini
# (team/scripts/lib/ini_parser.sh). Source only if not already available --
# every real caller already sources these via env_bootstrap.sh /
# project_paths.sh, but a standalone test harness sourcing this file alone
# still works.
if ! command -v pgai_temp_subdir >/dev/null 2>&1; then
    # this file: team/scripts/dashboard/lib/ -> team/scripts/lib/ is one dir up + lib
    # shellcheck source=../../lib/temp.sh
    source "${_PGAI_DASHBOARD_SNAPSHOT_SH_DIR}/../../lib/temp.sh"
fi
if ! command -v read_ini >/dev/null 2>&1; then
    # shellcheck source=../../lib/ini_parser.sh
    source "${_PGAI_DASHBOARD_SNAPSHOT_SH_DIR}/../../lib/ini_parser.sh"
fi

# ---------------------------------------------------------------------------
# dashboard_snapshot_dir
# ---------------------------------------------------------------------------
dashboard_snapshot_dir() {
    pgai_temp_subdir "dashboard/snapshots"
}

# ---------------------------------------------------------------------------
# dashboard_snapshot_ttl_seconds
# See the "STALENESS BOUND" header comment above for the resolution order.
# ---------------------------------------------------------------------------
dashboard_snapshot_ttl_seconds() {
    local ttl="${PGAI_DASHBOARD_SNAPSHOT_TTL_SECONDS:-}"

    if [[ -z "$ttl" ]]; then
        local root="${PGAI_AGENT_KANBAN_ROOT_PATH:-}"
        if [[ -n "$root" && -f "${root}/kanban.cfg" ]]; then
            ttl="$(read_ini "${root}/kanban.cfg" dashboard snapshot_ttl_seconds 5 2>/dev/null || echo 5)"
        fi
    fi

    [[ -z "$ttl" ]] && ttl=5
    if ! [[ "$ttl" =~ ^[0-9]+$ ]] || (( ttl <= 0 )); then
        ttl=5
    fi
    echo "$ttl"
}

# ---------------------------------------------------------------------------
# _dashboard_snapshot_hash <key>
# Internal: collapse an arbitrary cache-key string into a filesystem-safe
# basename. Prefers sha1sum (coreutils, present on every target host);
# falls back to md5sum, then to cksum so this never hard-fails on a stripped
# environment that has neither.
# ---------------------------------------------------------------------------
_dashboard_snapshot_hash() {
    local key="$1"
    if command -v sha1sum >/dev/null 2>&1; then
        printf '%s' "$key" | sha1sum | awk '{print $1}'
    elif command -v md5sum >/dev/null 2>&1; then
        printf '%s' "$key" | md5sum | awk '{print $1}'
    else
        printf '%s' "$key" | cksum | awk '{print $1 "-" $2}'
    fi
}

# ---------------------------------------------------------------------------
# dashboard_snapshot_path <key>
# ---------------------------------------------------------------------------
dashboard_snapshot_path() {
    local key="$1"
    local dir hash
    dir="$(dashboard_snapshot_dir)"
    hash="$(_dashboard_snapshot_hash "$key")"
    echo "${dir}/${hash}.snap"
}

# ---------------------------------------------------------------------------
# dashboard_snapshot_write <key>
# Reads full content from stdin; writes it atomically to <key>'s path.
# Returns 0 on success, 1 on any I/O failure (never removes an existing
# valid snapshot on failure -- the temp file is discarded, the prior
# snapshot, if any, is left untouched).
# ---------------------------------------------------------------------------
dashboard_snapshot_write() {
    local key="$1"
    local path dir tmpfile
    path="$(dashboard_snapshot_path "$key")"
    dir="$(dirname "$path")"
    mkdir -p "$dir" 2>/dev/null || return 1

    tmpfile="$(mktemp "${path}.XXXXXX" 2>/dev/null)" || return 1

    if ! cat > "$tmpfile" 2>/dev/null; then
        rm -f "$tmpfile" 2>/dev/null
        return 1
    fi

    # Atomic rename onto the final path (same directory => same filesystem
    # => rename(2) is atomic). A reader mid-flight sees the old complete
    # file or the new complete file, never a partial one.
    if ! mv -f "$tmpfile" "$path" 2>/dev/null; then
        rm -f "$tmpfile" 2>/dev/null
        return 1
    fi
    return 0
}

# ---------------------------------------------------------------------------
# dashboard_snapshot_read <key> <ttl_seconds>
# Prints cached content and returns 0 on a fresh hit. On a miss, prints
# nothing and returns a code that tells the caller WHY, so
# dashboard_snapshot_render can narrate at the right volume (see "NARRATION
# VOLUME" above):
#   1 -- missing or unreadable/partial (real-problem miss; normal volume)
#   2 -- present but older than <ttl_seconds> (routine TTL expiry; debug volume)
# ---------------------------------------------------------------------------
dashboard_snapshot_read() {
    local key="$1" ttl="${2:-5}"
    local path
    path="$(dashboard_snapshot_path "$key")"

    [[ -f "$path" && -r "$path" ]] || return 1

    local mtime
    mtime="$(stat -c '%Y' "$path" 2>/dev/null)" || mtime="$(stat -f '%m' "$path" 2>/dev/null)" || return 1
    [[ -n "$mtime" && "$mtime" =~ ^[0-9]+$ ]] || return 1

    local now age
    now="$(date +%s)"
    age=$(( now - mtime ))
    (( age < 0 )) && age=0
    # Strict "<" (not "<="): a snapshot exactly at the bound is treated as
    # stale, not fresh -- with whole-second mtime/now granularity, "age ==
    # ttl" already means at least that many seconds have elapsed, so the
    # conservative read is to degrade rather than serve one tick's worth of
    # extra staleness on a rounding coincidence.
    (( age >= ttl )) && return 2

    local content
    content="$(cat "$path" 2>/dev/null)" || return 1
    [[ -n "$content" ]] || return 1   # empty file reads as unreadable/partial

    printf '%s' "$content"
    return 0
}

# ---------------------------------------------------------------------------
# dashboard_snapshot_render <key> <ttl_seconds> <label> -- <command...>
# See the module header for the full contract.
# ---------------------------------------------------------------------------
dashboard_snapshot_render() {
    local key="$1" ttl="$2" label="$3"
    shift 3
    [[ "${1:-}" == "--" ]] && shift

    # `cmd && rc=0 || rc=$?` rather than a bare `var=$(cmd); rc=$?`: every
    # real caller runs under `set -euo pipefail` (column-render.sh,
    # show-queues.sh, show-progress.sh), and a failing command-substitution
    # ASSIGNMENT aborts the calling script right there under `set -e` --
    # before the `rc=$?` line this miss-reason branch depends on ever runs.
    # The compound form always exits 0 for `set -e` purposes, matching this
    # function's own "a miss is handled here, never propagated" contract.
    local cached read_rc
    cached="$(dashboard_snapshot_read "$key" "$ttl")" && read_rc=0 || read_rc=$?
    if [[ "$read_rc" -eq 0 ]]; then
        printf '%s\n' "$cached"
        return 0
    fi

    # Miss: narrate to stderr ONLY -- stdout stays byte-identical to the
    # pre-snapshot direct-render path (see "MISSING / PARTIAL / UNREADABLE
    # DEGRADE" above). Volume depends on the reason (see "NARRATION VOLUME"
    # above): a routine TTL expiry (read_rc 2) is the design's normal case
    # and is demoted to debug volume; a missing or unreadable snapshot
    # (read_rc 1) is narrated at normal volume.
    if [[ "$read_rc" -eq 2 ]]; then
        echo "[dashboard-snapshot-debug] ${label}: snapshot older than ${ttl}s -- rendering directly (routine expiry)" >&2
    else
        echo "[dashboard-snapshot] ${label}: no fresh snapshot (missing or unreadable) -- rendering directly" >&2
    fi

    local output
    if ! output="$("$@")"; then
        return 1
    fi

    printf '%s\n' "$output"
    if ! dashboard_snapshot_write "$key" <<<"$output" 2>/dev/null; then
        # A failed cache refresh is a real problem for this key's next
        # reader (it will miss again next tick even though this render
        # succeeded) -- normal volume, not demoted.
        echo "[dashboard-snapshot] ${label}: failed to write snapshot for twin readers" >&2
    fi
    return 0
}

# ---------------------------------------------------------------------------
# dashboard_data_snapshot_get <data.sh-path> [args...]
# Twin-parity wrapper for team/scripts/dashboard/data.sh -- the per-project
# scanner both show-queues.sh and show-progress.sh call. The overview
# window's per-project loop (show-multi.sh --mode queues|progress, one
# data.sh call per registered project) and a drill-N window's own
# queues/progress panes for that SAME project are twins: without sharing,
# each ticks its own independent `data.sh --project X` scan. This wrapper
# caches data.sh's full key=value stdout, keyed by its exact argv, so two
# callers invoking data.sh with identical arguments (same project, same
# --kanban-root) read one shared per-tick scan instead of each re-scanning
# tasks/ on its own.
# ---------------------------------------------------------------------------
dashboard_data_snapshot_get() {
    local data_sh="$1"
    shift
    local key="data.sh $*"
    local ttl
    ttl="$(dashboard_snapshot_ttl_seconds)"
    dashboard_snapshot_render "$key" "$ttl" "data.sh $*" -- "$data_sh" "$@"
}
