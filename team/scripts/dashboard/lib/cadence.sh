#!/usr/bin/env bash
# cadence.sh — visibility-aware refresh cadence for dashboard panes.
#
# WHAT THIS DOES
# --------------
# Every dashboard pane loop (cadence-loop.sh) runs through this file, and
# this file holds the ONE function that decides whether a pane is visible:
# dashboard_pane_visible. A pane is visible only when its window is the
# session's active window AND the session has at least one attached
# client. Anything else -- another window is active, or nobody is attached
# (a detached, unattended dashboard) -- is hidden.
#
# A pane renders only while it is visible:
#   1. Arrival render: a pane renders once when its loop starts, so it
#      always has content to show.
#   2. Visible: it re-renders every <interval> seconds.
#   3. Hidden: it suspends -- no renders at all. The loop sleeps and only
#      re-checks visibility (one tmux query, no render) every
#      <interval> x PGAI_DASHBOARD_HIDDEN_MULTIPLIER seconds, as a safety
#      net behind the activation signal below.
#   4. Activation: the activation hook (lib/activation_hook.sh /
#      activation-hook-sender.sh) wakes the loops of the window that just
#      became visible -- on a window switch and when a client attaches. The
#      wake cuts the loop's sleep short; the loop re-checks visibility and
#      renders at once if visible. A pane in the window just left notices
#      it is hidden at its next tick and suspends without rendering.
#      See "HOW A LOOP IS WOKEN" below for which signal reaches the loop.
#   5. Per-pane stagger jitter: a bounded, deterministic-per-pane one-time
#      delay before a pane's first render, so panes launched together do
#      not all tick on the same wall-clock beat.
#
# Stream panes (a renderer that never returns, e.g. logs.sh following its
# logs with `tail -F`) run under dashboard_cadence_run_stream instead: the
# renderer runs in its own process group, which is stopped (SIGSTOP) while
# the pane is hidden and continued (SIGCONT) when it becomes visible, so a
# hidden stream pane does no work either. Output a stopped stream would
# have shown is caught up when it continues.
#
# HOW A LOOP IS WOKEN
# --------------------
# pane_readiness.sh installs `trap '' PGAI_DASHBOARD_PANE_SIGNAL` (ignore)
# as every pane's LITERAL FIRST statement, so the pane's shell survives the
# activation signal from the moment it starts. cadence-loop.sh runs as a
# CHILD of that shell and inherits the ignored disposition -- and bash does
# not let a non-interactive script trap a signal that was ignored when it
# started, so the loop cannot catch PGAI_DASHBOARD_PANE_SIGNAL there. The
# loop is therefore woken with its own signal, PGAI_DASHBOARD_LOOP_SIGNAL
# (default USR2), which no pane ignores:
#   1. The loop installs its handler (for both signals -- the pane signal
#      still works when the loop is started outside a pane, as tests do).
#   2. Only THEN does it register itself: it writes its pid to
#      $(pgai_temp_subdir dashboard/loops)/<pane-id>, keyed by $TMUX_PANE.
#   3. The sender signals a pane's registered loop pid -- after checking
#      that pid is still a cadence-loop.sh process -- never a pane that has
#      not registered. A loop is never signalled before its handler exists,
#      so the default (terminate) disposition is never hit.
# The registration is removed when the loop exits.
#
# WHERE PANE IDENTITY COMES FROM
# --------------------------------
# The pane is identified at RUNTIME: tmux sets $TMUX_PANE (the pane id,
# e.g. %12) in every pane's environment, and visibility is read for that
# exact pane. The window name and pane index (for the debug log label and
# the jitter offset) come from `tmux display-message`. This lets one *_CMD
# string in create.sh be sent verbatim to more than one pane target; each
# running instance discovers its own identity.

# WHY RENDER STDERR IS CAPTURED HERE
# ------------------------------------
# In a tmux pane, stderr paints the pane's own terminal exactly like stdout
# does -- there is no separate display surface for it. lib/snapshot.sh's
# cooperative-cache narration (and any other quality-class stderr a renderer
# emits) is written on stderr BY DESIGN so it never touches stdout and keeps
# render goldens byte-identical (see snapshot.sh's own header), but running
# a renderer with stderr uncaptured inside a pane loop means that narration
# lands on the pane's display anyway, stamping the rendered columns.
# dashboard_cadence_render_once() below redirects each render's stderr to a
# pane-scoped debug log file instead -- the display stream carries ONLY
# rendered columns, and the narration survives for inspection in the log.

# Include guard: safe to source multiple times.
[[ -n "${_PGAI_DASHBOARD_CADENCE_SH_LOADED:-}" ]] && return 0
_PGAI_DASHBOARD_CADENCE_SH_LOADED=1

# The signal that wakes a registered cadence loop (see "HOW A LOOP IS
# WOKEN" above). Must differ from PGAI_DASHBOARD_PANE_SIGNAL, which every
# pane's shell -- and so every loop started from it -- ignores.
PGAI_DASHBOARD_LOOP_SIGNAL="${PGAI_DASHBOARD_LOOP_SIGNAL:-USR2}"

_PGAI_DASHBOARD_CADENCE_SH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Dependency: pgai_temp_subdir (team/scripts/lib/temp.sh) resolves the
# pane-scoped debug log directory through the framework's existing temp
# conventions (never a bare /tmp entry). Source only if not already
# available -- every real caller already sources it via env_bootstrap.sh's
# shell-env chain in practice, but a standalone sourcing of this file (as
# team/tests/unit/test_dashboard_cadence.py does) still works.
if ! command -v pgai_temp_subdir >/dev/null 2>&1; then
    # this file: team/scripts/dashboard/lib/ -> team/scripts/lib/ is one dir up + lib
    # shellcheck source=../../lib/temp.sh
    source "${_PGAI_DASHBOARD_CADENCE_SH_DIR}/../../lib/temp.sh"
fi

# ---------------------------------------------------------------------------
# dashboard_cadence_pane_debug_log_path <pane_label>
# Echoes the absolute path of the pane-scoped debug log a running
# cadence-loop.sh instance appends its renderer's stderr to. One file per
# pane label (e.g. "visibility.0"), under the framework temp root's
# dashboard/logs subtree -- cleaned up the same way lib/snapshot.sh's
# snapshots are (kill.sh's `pgai_temp_cleanup "$(pgai_temp_subdir dashboard)"`
# removes the whole dashboard/ subtree, logs included).
#
# <pane_label> is sanitized to a safe filesystem basename: characters
# outside [A-Za-z0-9._-] become "_", and an empty or dot-only result (e.g.
# pane identity unresolved outside tmux -- see dashboard_cadence_pane_identity)
# falls back to "unknown-pane" rather than producing "." or "..", which are
# not valid single-component filenames.
# ---------------------------------------------------------------------------
dashboard_cadence_pane_debug_log_path() {
    local pane_label="${1:-}"
    local dir
    dir="$(pgai_temp_subdir "dashboard/logs")"

    local safe
    safe="$(printf '%s' "$pane_label" | tr -c 'A-Za-z0-9._-' '_')"
    case "$safe" in
        ""|.|..) safe="unknown-pane" ;;
    esac

    printf '%s/%s.log\n' "$dir" "$safe"
}

# ---------------------------------------------------------------------------
# dashboard_cadence_loop_registry_path <pane-id>
# Echoes the path of the file a running loop for tmux pane <pane-id> (e.g.
# "%12", the value of $TMUX_PANE) registers its pid in. The id is
# sanitized to a safe basename ("%12" -> "_12").
# ---------------------------------------------------------------------------
dashboard_cadence_loop_registry_path() {
    local pane_id="${1:?dashboard_cadence_loop_registry_path: pane id required}"
    local dir safe
    dir="$(pgai_temp_subdir "dashboard/loops")"
    safe="$(printf '%s' "$pane_id" | tr -c 'A-Za-z0-9._-' '_')"
    printf '%s/%s\n' "$dir" "$safe"
}

# ---------------------------------------------------------------------------
# dashboard_cadence_wake_loop <pane-id>
# Wakes the cadence loop registered for <pane-id> with
# PGAI_DASHBOARD_LOOP_SIGNAL. Returns 0 when a registered, still-running
# cadence-loop.sh process was signalled; 1 when the pane has no
# registration, or the registered pid is gone or is no longer a
# cadence-loop.sh process (a stale file never sends a signal to an
# unrelated process).
# ---------------------------------------------------------------------------
dashboard_cadence_wake_loop() {
    local pane_id="${1:-}" registry pid cmdline
    [[ -n "$pane_id" ]] || return 1
    registry="$(dashboard_cadence_loop_registry_path "$pane_id" 2>/dev/null)" || return 1
    [[ -r "$registry" ]] || return 1
    pid="$(head -n 1 "$registry" 2>/dev/null)"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    cmdline="$(tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null)" || return 1
    [[ "$cmdline" == *cadence-loop.sh* ]] || return 1
    kill -s "$PGAI_DASHBOARD_LOOP_SIGNAL" "$pid" 2>/dev/null
}

# ---------------------------------------------------------------------------
# _dashboard_cadence_register_loop
# Records this loop's pid under its pane's registry path (only inside a
# tmux pane, where $TMUX_PANE is set) and removes the record when the loop
# exits -- only if it still names this loop. Call it after the activation
# handler is installed. Best-effort: an unwritable registry leaves the
# loop unwakeable (it still re-checks visibility on its own schedule).
# ---------------------------------------------------------------------------
_dashboard_cadence_register_loop() {
    [[ -n "${TMUX_PANE:-}" ]] || return 0
    _PGAI_CADENCE_REGISTRY="$(dashboard_cadence_loop_registry_path "$TMUX_PANE" 2>/dev/null)" || return 0
    { printf '%s\n' "$$" > "$_PGAI_CADENCE_REGISTRY"; } 2>/dev/null || return 0
    _pgai_cadence_unregister() {
        if [[ -n "${_PGAI_CADENCE_REGISTRY:-}" ]] \
           && [[ "$(head -n 1 "$_PGAI_CADENCE_REGISTRY" 2>/dev/null)" == "$$" ]]; then
            rm -f "$_PGAI_CADENCE_REGISTRY" 2>/dev/null
        fi
    }
    trap _pgai_cadence_unregister EXIT
    return 0
}

# ---------------------------------------------------------------------------
# dashboard_cadence_pane_identity
# Echoes "<session>\t<window>\t<pane_label>" for the pane this function
# runs inside. Falls back to empty strings when tmux cannot resolve a
# current pane (e.g. run standalone outside any tmux pane, as the
# CPU-sample test does).
# ---------------------------------------------------------------------------
dashboard_cadence_pane_identity() {
    local session window pane_index
    local -a target=()
    [[ -n "${TMUX_PANE:-}" ]] && target=(-t "${TMUX_PANE}")
    session="$(tmux display-message -p "${target[@]}" '#S' 2>/dev/null || true)"
    window="$(tmux display-message -p "${target[@]}" '#W' 2>/dev/null || true)"
    pane_index="$(tmux display-message -p "${target[@]}" '#P' 2>/dev/null || true)"
    printf '%s\t%s\t%s.%s\n' "$session" "$window" "$window" "$pane_index"
}

# ---------------------------------------------------------------------------
# dashboard_cadence_jitter_seconds <pane_label> <bound>
# Deterministic per-pane offset in [0, bound), computed once by the caller
# and applied only before the loop's first render — never re-rolled on
# later ticks (see dashboard_cadence_run_loop). bound <= 0 disables jitter
# (echoes 0).
#
# <pane_label> is "<window>.<pane_index>" (dashboard_cadence_pane_identity's
# shape). The offset is (hash(window) + pane_index) mod bound rather than a
# hash of the whole label string, so that within a single window, any two
# panes with consecutive tmux pane indices (the common "launched together"
# case — e.g. "visibility.0" and "visibility.1") are GUARANTEED distinct
# offsets for any bound >= 2, not merely "distinct with high probability".
# A window with more panes than <bound> still wraps (e.g. bound=3 gives
# visibility.0 and visibility.3 the same offset), which is an accepted
# trade-off against keeping the bound small enough to never meaningfully
# delay a pane's first paint.
# ---------------------------------------------------------------------------
dashboard_cadence_jitter_seconds() {
    local pane_label="${1:?dashboard_cadence_jitter_seconds: pane_label required}"
    local bound="${2:?dashboard_cadence_jitter_seconds: bound required}"

    if [[ ! "$bound" =~ ^[0-9]+$ ]] || [[ "$bound" -le 0 ]]; then
        echo 0
        return 0
    fi

    local window_part="${pane_label%.*}"
    local index_part="${pane_label##*.}"
    if [[ ! "$index_part" =~ ^[0-9]+$ ]]; then
        index_part=0
    fi

    local window_hash
    window_hash="$(printf '%s' "$window_part" | cksum | awk '{print $1}')"
    echo $(( (window_hash + index_part) % bound ))
}

# ---------------------------------------------------------------------------
# dashboard_pane_visible [<pane-target>]
# THE visibility decision for every dashboard pane. Echoes "1" when the
# pane's window is its session's active window AND the session has at
# least one attached client; echoes "0" otherwise (another window is
# active, or no client is attached -- a detached dashboard shows nobody
# anything, so every pane is hidden).
#
# <pane-target> defaults to $TMUX_PANE, the id tmux gives every pane's
# environment. One `tmux display-message` call answers both halves for
# that exact pane, so window names (which may contain spaces, as the
# "drill-N: <project>" windows do) never have to be matched.
#
# Tolerant of an unresolvable pane (no tmux context at all, or a session
# torn down mid-query): echoes "1", so a pane that cannot tell is never
# silently starved.
#
# Test-only override: PGAI_DASHBOARD_CADENCE_FORCE_STATE, when set to
# "active" or "hidden", short-circuits the tmux query; when set to
# "file:<path>", the state ("active" or "hidden") is read from <path> on
# every call, so a test can change it while a loop runs. Unset in
# production.
# ---------------------------------------------------------------------------
dashboard_pane_visible() {
    local forced="${PGAI_DASHBOARD_CADENCE_FORCE_STATE:-}"
    if [[ "$forced" == file:* ]]; then
        forced="$(cat "${forced#file:}" 2>/dev/null || true)"
        forced="${forced//[[:space:]]/}"
    fi
    case "$forced" in
        active) echo 1; return 0 ;;
        hidden) echo 0; return 0 ;;
    esac

    local target="${1:-${TMUX_PANE:-}}"
    if [[ -z "$target" ]]; then
        echo 1
        return 0
    fi

    local attached active
    if ! read -r attached active < <(tmux display-message -p -t "$target" \
            '#{session_attached} #{window_active}' 2>/dev/null) \
       || [[ ! "$attached" =~ ^[0-9]+$ ]]; then
        echo 1
        return 0
    fi
    if [[ "$attached" -gt 0 && "$active" == "1" ]]; then
        echo 1
    else
        echo 0
    fi
}

# ---------------------------------------------------------------------------
# dashboard_cadence_render_once <debug_log_path> -- <renderer argv...>
# Flicker-free redraw: cursor-home, then print each line of the renderer's
# stdout followed by a clear-to-end-of-line (erasing stale trailing
# characters from the previous, possibly-wider render), then clear
# everything below the new content.
#
# The renderer's STDERR is appended to <debug_log_path> instead of being
# left connected to this loop's own stderr (which, inside a tmux pane, IS
# the pane's display -- see this file's "WHY RENDER STDERR IS CAPTURED
# HERE" header). <debug_log_path> is resolved once by the caller
# (dashboard_cadence_run_loop), which already verified it is writable
# (falling back to /dev/null otherwise), so the redirection here can never
# itself fail and block a render.
# ---------------------------------------------------------------------------
dashboard_cadence_render_once() {
    local debug_log="${1:?dashboard_cadence_render_once: debug_log_path required}"
    shift
    [[ "${1:-}" == "--" ]] && shift

    tput cup 0 0
    local _line
    while IFS= read -r _line; do
        printf '%s' "$_line"
        tput el
        printf '\n'
    done < <("$@" 2>>"$debug_log")
    tput ed
}

# ---------------------------------------------------------------------------
# _dashboard_cadence_debug_log
# Resolves this pane's debug log target ONCE for a loop's whole lifetime
# and verifies it is writable now. A pane whose target cannot be written
# (missing/unwritable parent, read-only filesystem) falls back to
# /dev/null, so it still renders -- it just loses the narration. stderr is
# suppressed on the resolution itself: pgai_temp_subdir's own `mkdir -p`
# prints a diagnostic on an unwritable temp root, and inside a real tmux
# pane this loop's stderr IS the display.
# ---------------------------------------------------------------------------
_dashboard_cadence_debug_log() {
    local pane_label="$1" path
    path="$(dashboard_cadence_pane_debug_log_path "$pane_label" 2>/dev/null)"
    if ! { : >>"$path"; } 2>/dev/null; then
        path="/dev/null"
    fi
    printf '%s\n' "$path"
}

# ---------------------------------------------------------------------------
# _dashboard_cadence_wait <seconds>
# Sleeps <seconds>, cut short by the activation handler. Shared by both
# loop shapes; uses the caller's _pgai_cadence_activated flag and
# _pgai_cadence_sleep_pid (see dashboard_cadence_run_loop for why the flag
# and the kill together cover every point the signal can land).
# ---------------------------------------------------------------------------
_dashboard_cadence_wait() {
    local seconds="$1" sleep_pid
    [[ "$_pgai_cadence_activated" -eq 1 ]] && return 0
    sleep "$seconds" &
    _pgai_cadence_sleep_pid=$!
    sleep_pid="$_pgai_cadence_sleep_pid"
    if [[ "$_pgai_cadence_activated" -eq 0 ]]; then
        wait "$sleep_pid" 2>/dev/null
    fi
    # Forget the pid as soon as the wait is over, so the handler never
    # signals a pid this loop has already reaped.
    _pgai_cadence_sleep_pid=""
    if [[ "$_pgai_cadence_activated" -eq 1 ]]; then
        # The activation cut the interval short: make sure the sleep is
        # gone and reap it rather than leaving it behind as an orphan.
        kill "$sleep_pid" 2>/dev/null || true
        wait "$sleep_pid" 2>/dev/null || true
    fi
    return 0
}

# ---------------------------------------------------------------------------
# _dashboard_cadence_hidden_seconds <active_interval> <hidden_multiplier>
# The hidden re-check interval: <active_interval> x <hidden_multiplier>.
# ---------------------------------------------------------------------------
_dashboard_cadence_hidden_seconds() {
    awk -v i="$1" -v m="$2" 'BEGIN { printf "%s", i * m }'
}

# ---------------------------------------------------------------------------
# dashboard_cadence_run_loop <active_interval> <hidden_multiplier> <jitter_bound> -- <renderer argv...>
# The pane's whole lifetime: one-time jitter delay, the arrival render,
# then forever: sleep, re-check visibility, render only if visible. The
# sleep is <active_interval> while visible and <active_interval> x
# <hidden_multiplier> while hidden (a visibility re-check only -- a hidden
# pane never renders), and PGAI_DASHBOARD_PANE_SIGNAL cuts it short.
# ---------------------------------------------------------------------------
dashboard_cadence_run_loop() {
    local active_interval="${1:?dashboard_cadence_run_loop: active_interval required}"
    local hidden_multiplier="${2:?dashboard_cadence_run_loop: hidden_multiplier required}"
    local jitter_bound="${3:?dashboard_cadence_run_loop: jitter_bound required}"
    shift 3
    if [[ "${1:-}" == "--" ]]; then
        shift
    fi

    local identity pane_label
    identity="$(dashboard_cadence_pane_identity)"
    pane_label="$(printf '%s' "$identity" | cut -f3)"

    local pane_debug_log hidden_interval
    pane_debug_log="$(_dashboard_cadence_debug_log "$pane_label")"
    hidden_interval="$(_dashboard_cadence_hidden_seconds "$active_interval" "$hidden_multiplier")"

    # Override the ignore-disposition pane_readiness.sh's preamble already
    # installed (see this file's header docstring for why that is safe
    # here) with a real handler. The handler never renders; it records the
    # activation in a flag and kills the pending interval sleep, if one is
    # running, so the loop re-checks visibility immediately.
    #
    # The activation hook signals this loop's pid (see "HOW A LOOP IS
    # WOKEN"), never its process group, so the background sleep does not
    # die on its own and the
    # activation is honoured only if the loop notices it. The flag and the
    # kill together cover every point the signal can land:
    #   - during a render or the visibility query: the flag is reset only
    #     after the wait, so it survives until the next wait, which then
    #     returns at once.
    #   - after the sleep is spawned but before `wait` blocks: the handler
    #     kills the recorded sleep pid, so `wait` returns at once (or the
    #     flag check ahead of `wait` skips it).
    #   - during `wait`: bash interrupts the wait to run the handler.
    local _pgai_cadence_activated=0
    local _pgai_cadence_sleep_pid=""
    _pgai_cadence_on_activate() {
        _pgai_cadence_activated=1
        if [[ -n "$_pgai_cadence_sleep_pid" ]]; then
            kill "$_pgai_cadence_sleep_pid" 2>/dev/null || true
        fi
    }
    trap _pgai_cadence_on_activate "${PGAI_DASHBOARD_PANE_SIGNAL:-USR1}"
    trap _pgai_cadence_on_activate "${PGAI_DASHBOARD_LOOP_SIGNAL}"
    _dashboard_cadence_register_loop

    # One-time stagger, applied ONLY before the first render (never
    # re-rolled per tick — see dashboard_cadence_jitter_seconds).
    local jitter
    jitter="$(dashboard_cadence_jitter_seconds "$pane_label" "$jitter_bound")"
    if [[ "$jitter" != "0" ]]; then
        sleep "$jitter" &
        wait $! 2>/dev/null
    fi

    clear
    # Arrival render: the pane always has content, visible or not.
    dashboard_cadence_render_once "$pane_debug_log" -- "$@"
    local visible
    visible="$(dashboard_pane_visible)"
    while true; do
        if [[ "$visible" == "1" ]]; then
            _dashboard_cadence_wait "$active_interval"
        else
            _dashboard_cadence_wait "$hidden_interval"
        fi
        # Reset BEFORE the visibility query and render, never after them:
        # an activation that arrives during either must still cut the next
        # wait short.
        _pgai_cadence_activated=0
        visible="$(dashboard_pane_visible)"
        if [[ "$visible" == "1" ]]; then
            dashboard_cadence_render_once "$pane_debug_log" -- "$@"
        fi
    done
}

# ---------------------------------------------------------------------------
# dashboard_cadence_run_stream <active_interval> <hidden_multiplier> -- <renderer argv...>
# The lifetime of a pane whose renderer streams forever (it never returns
# for a loop to re-invoke -- e.g. logs.sh following logs with tail -F).
# The renderer runs in its own session and process group (setsid), with
# stdout on the pane and stderr in the pane's debug log. While the pane is
# hidden that whole group is stopped (SIGSTOP); when it is visible again
# the group continues (SIGCONT) and catches up. Visibility is re-checked
# every <active_interval> seconds while visible and every
# <active_interval> x <hidden_multiplier> while hidden, and at once on
# PGAI_DASHBOARD_PANE_SIGNAL. The loop ends when the renderer exits; when
# the loop itself is ended (the pane closes: SIGHUP; or SIGTERM/SIGINT) the
# renderer's group is continued and terminated with it, so no stopped
# stream outlives its pane. Without setsid on PATH the renderer runs in the
# loop's own group and is never stopped (narrated once to the debug log).
# ---------------------------------------------------------------------------
dashboard_cadence_run_stream() {
    local active_interval="${1:?dashboard_cadence_run_stream: active_interval required}"
    local hidden_multiplier="${2:?dashboard_cadence_run_stream: hidden_multiplier required}"
    shift 2
    if [[ "${1:-}" == "--" ]]; then
        shift
    fi

    local identity pane_label pane_debug_log hidden_interval
    identity="$(dashboard_cadence_pane_identity)"
    pane_label="$(printf '%s' "$identity" | cut -f3)"
    pane_debug_log="$(_dashboard_cadence_debug_log "$pane_label")"
    hidden_interval="$(_dashboard_cadence_hidden_seconds "$active_interval" "$hidden_multiplier")"

    local _pgai_cadence_activated=0
    local _pgai_cadence_sleep_pid=""
    _pgai_cadence_on_activate() {
        _pgai_cadence_activated=1
        if [[ -n "$_pgai_cadence_sleep_pid" ]]; then
            kill "$_pgai_cadence_sleep_pid" 2>/dev/null || true
        fi
    }
    trap _pgai_cadence_on_activate "${PGAI_DASHBOARD_PANE_SIGNAL:-USR1}"
    trap _pgai_cadence_on_activate "${PGAI_DASHBOARD_LOOP_SIGNAL}"
    _dashboard_cadence_register_loop

    local stream_pid="" can_pause=1
    if command -v setsid >/dev/null 2>&1; then
        # Started from a non-interactive shell (no job control), the child
        # is not a process-group leader, so setsid makes it the leader of a
        # new session and group without forking: its pid is the group id.
        setsid "$@" 2>>"$pane_debug_log" &
        stream_pid=$!
    else
        can_pause=0
        printf 'cadence-loop: setsid not on PATH; stream pane %s keeps running while hidden\n' \
            "$pane_label" >>"$pane_debug_log" 2>/dev/null
        "$@" 2>>"$pane_debug_log" &
        stream_pid=$!
    fi

    _pgai_cadence_stream_stop() {
        if [[ "$can_pause" -eq 1 ]]; then
            kill -CONT -- "-${stream_pid}" 2>/dev/null || true
            kill -TERM -- "-${stream_pid}" 2>/dev/null || true
        else
            kill -TERM "$stream_pid" 2>/dev/null || true
        fi
    }
    trap '_pgai_cadence_stream_stop; exit 129' HUP
    trap '_pgai_cadence_stream_stop; exit 143' TERM
    trap '_pgai_cadence_stream_stop; exit 130' INT

    local visible paused=0
    while kill -0 "$stream_pid" 2>/dev/null; do
        _pgai_cadence_activated=0
        visible="$(dashboard_pane_visible)"
        if [[ "$can_pause" -eq 1 ]]; then
            if [[ "$visible" != "1" && "$paused" -eq 0 ]]; then
                kill -STOP -- "-${stream_pid}" 2>/dev/null && paused=1
            elif [[ "$visible" == "1" && "$paused" -eq 1 ]]; then
                kill -CONT -- "-${stream_pid}" 2>/dev/null
                paused=0
            fi
        fi
        if [[ "$visible" == "1" ]]; then
            _dashboard_cadence_wait "$active_interval"
        else
            _dashboard_cadence_wait "$hidden_interval"
        fi
    done
    wait "$stream_pid" 2>/dev/null
}
