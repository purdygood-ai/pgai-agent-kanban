#!/usr/bin/env bash
# pane_readiness.sh — shared pane-loop signal-safety preamble + the
# all-armed readiness handshake create.sh blocks its return on.
#
# THE BUG THIS CLOSES
# --------------------
# Every pane loop under team/scripts/dashboard/ (watch invocations, the
# hand-rolled "clear; while true; do ...; sleep; done" visibility loops,
# and the interactive shells) is, from tmux's point of view, one long-lived
# foreground process (the pane's own top-level shell, tmux's "pane_pid").
# Before this file existed that process had no disposition for
# PGAI_DASHBOARD_PANE_SIGNAL other than the OS default -- terminate -- so a
# signal delivered to it during the window between pane start and first
# render (or at any later point) killed the pane outright, tearing down
# whatever it was showing and, for window "main"'s pane 0 (the session's
# very first process), the entire tmux server.  A future activation hook
# will signal every armed pane on window/pane focus change to force an
# immediate refresh; without this file that hook would kill the dashboard
# it is meant to speed up.
#
# THE FIX'S SHAPE
# ----------------
# 1. dashboard_pane_wrap_cmd prepends `trap '' <signal>` as the pane
#    command's literal first statement, before ANYTHING else -- including
#    env_bootstrap.sh sourcing, which happens later, inside the scripts the
#    wrapped command goes on to invoke.  `trap '' SIG` (an ignore, not a
#    handler function) is the deliberate choice: POSIX preserves an
#    IGNORED disposition across fork(2) AND exec(2) (only a caught/handled
#    disposition reverts to default on exec), so one trap installed at the
#    top of the pane's shell protects every descendant that shell ever
#    forks or execs into -- `watch`, a `while true` loop running in the
#    SAME shell process, or the operator-shell pane's own
#    `bash -l -c '...; exec bash -l'` re-exec chain.  Verified empirically
#    (not just reasoned about) against tmux 3.2a: a bare `kill -USR1
#    $pane_pid` against an unwrapped `watch` pane takes the whole private
#    tmux server down (the session's only pane's shell dies); the same
#    signal against a `trap '' USR1`-wrapped pane leaves pane_dead=0 and
#    the watch loop still running.
# 2. The same wrapped statement touches a marker file in a per-session
#    "armed" directory -- proof to the outside (create.sh, and later the
#    activation-hook's own signal sender) that THIS pane's trap is
#    installed and it is therefore safe to signal.
# 3. dashboard_wait_all_armed polls that directory in a tight loop (never a
#    fixed sleep -- a fixed sleep is exactly the race this file exists to
#    close) until every expected pane has reported armed, or a bounded
#    timeout elapses.
#
# WHERE MARKERS LIVE
# -------------------
# Under the pgai temp root via team/scripts/lib/temp.sh's pgai_temp_subdir
# -- never bare /tmp, never inside the kanban root (SOP "Temporary File
# Convention").  Nested under the "dashboard" subdir that kill.sh already
# wipes wholesale via pgai_temp_cleanup on every session teardown it
# performs, so a marker never outlives the session that created it.
#
# NO EXEMPTIONS
# -------------
# Every pane command create.sh sends via dashboard_send_armed_keys (see
# create.sh) goes through dashboard_pane_wrap_cmd -- interactive shell
# panes (the operator cmdline, the terminal window's shell) included. The
# ignore-disposition is silent and behavior-preserving for an interactive
# shell (no output, no prompt change, no effect on job control or Ctrl-C),
# so uniform coverage costs nothing and there is no pane in the census
# this file's design needs to exclude.

# Source guard: safe to source multiple times.
[[ -n "${_PGAI_PANE_READINESS_SH_LOADED:-}" ]] && return 0
_PGAI_PANE_READINESS_SH_LOADED=1

# dashboard_pane_armed_dir (below) calls pgai_temp_subdir; source temp.sh
# here so any caller of this file gets it for free, the same
# self-contained-library convention team/scripts/dashboard/lib/halt_scope.sh
# uses for helper_resolver.sh. temp.sh's own include guard makes re-sourcing
# from a caller that already loaded it (e.g. create.sh in a later ticket) a
# safe no-op.
_pgai_pane_readiness_self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/temp.sh
source "${_pgai_pane_readiness_self_dir}/../../lib/temp.sh"
unset _pgai_pane_readiness_self_dir

# The signal every pane's top-level shell ignores as its first act.
# Overridable for tests; production callers use the default.
PGAI_DASHBOARD_PANE_SIGNAL="${PGAI_DASHBOARD_PANE_SIGNAL:-USR1}"

# ---------------------------------------------------------------------------
# dashboard_pane_armed_dir <kanban_root> <session_name>
# Echo the per-session directory readiness markers live under, creating it
# (and clearing any stale markers left by an earlier run under the same
# session name) if it does not already exist empty.  Callers that want a
# guaranteed-fresh directory for a new session should rm its contents
# themselves before dispatching pane commands (create.sh does this) --
# this function only guarantees the directory EXISTS, matching the
# idempotent-creator contract every other pgai_temp_* helper carries.
# ---------------------------------------------------------------------------
dashboard_pane_armed_dir() {
    local kanban_root="${1:?dashboard_pane_armed_dir: kanban_root required}"
    local session_name="${2:?dashboard_pane_armed_dir: session_name required}"
    PGAI_AGENT_KANBAN_ROOT_PATH="$kanban_root" pgai_temp_subdir "dashboard/armed/${session_name}"
}

# ---------------------------------------------------------------------------
# dashboard_pane_wrap_cmd <armed_dir> <pane_label> <inner_cmd>
# Echo a command string suitable for `tmux send-keys ... Enter`:
#   1. `trap '' <signal>` -- the pane's first executed statement, always.
#   2. Touch <armed_dir>/<pane_label> -- the armed report.
#   3. Run <inner_cmd> -- unchanged, verbatim, last.
#
# Test-only delay hook: when PGAI_DASHBOARD_PANE_ARM_DELAY_LABEL is set and
# equals <pane_label>, a `sleep PGAI_DASHBOARD_PANE_ARM_DELAY_SECONDS`
# (default 1) is inserted between the trap and the armed-report touch, so
# the readiness-handshake test suite can simulate a slow-to-arm pane
# without editing create.sh's real window/pane set. Unset in production;
# the trap installation itself is never delayed by this hook -- only the
# armed report that follows it, which is the only thing a test needs to
# control to exercise the partial-arm / timeout paths.
# ---------------------------------------------------------------------------
dashboard_pane_wrap_cmd() {
    local armed_dir="${1:?dashboard_pane_wrap_cmd: armed_dir required}"
    local pane_label="${2:?dashboard_pane_wrap_cmd: pane_label required}"
    local inner_cmd="${3:?dashboard_pane_wrap_cmd: inner_cmd required}"

    local delay_clause=""
    if [[ -n "${PGAI_DASHBOARD_PANE_ARM_DELAY_LABEL:-}" && \
          "${PGAI_DASHBOARD_PANE_ARM_DELAY_LABEL}" == "${pane_label}" ]]; then
        local delay_seconds="${PGAI_DASHBOARD_PANE_ARM_DELAY_SECONDS:-1}"
        delay_clause="sleep ${delay_seconds}; "
    fi

    printf 'trap %s %s; %s: > %q; %s' \
        "''" \
        "${PGAI_DASHBOARD_PANE_SIGNAL}" \
        "${delay_clause}" \
        "${armed_dir}/${pane_label}" \
        "${inner_cmd}"
}

# ---------------------------------------------------------------------------
# dashboard_pane_wrap_cmd_no_marker <inner_cmd>
# Same trap-install half as dashboard_pane_wrap_cmd, WITHOUT touching the
# armed marker -- for the small set of panes whose first command's LAST
# statement is a bare `exec bash` / `exec bash -l` (no further exec, no
# script argument): verified empirically against tmux 3.2a / bash 5.x
# (via /proc/<pid>/status SigIgn straddling the exec) that a freshly
# started interactive bash resets an inherited SIG_IGN disposition for a
# non-job-control signal rather than trusting what it inherited --
# contrary to the plain POSIX fork/exec rule dashboard_pane_wrap_cmd's own
# ignore-trap otherwise relies on for every other pane. The process that
# actually stays resident in the pane is therefore UNPROTECTED again for
# a real (if usually brief) window starting the instant that exec lands.
#
# Touching the marker here, before that exec even runs, would let both
# create.sh's own all-armed handshake and every later activation-hook
# firing treat the pane as protected during exactly that window -- a
# signal landing there hits the OS default disposition (terminate) and
# kills the pane outright, intermittently, under a rapid-switch storm.
# The caller is responsible for re-installing the trap AND touching the
# marker together, as one `tmux send-keys` call typed into the shell that
# actually stays resident (see create.sh's own re-arm call sites for the
# operator shell pane and the terminal window's interactive shell) --
# never as two separate calls, so the marker's existence is always proof
# of the CURRENTLY-running shell's own trap, never a since-superseded one.
# ---------------------------------------------------------------------------
dashboard_pane_wrap_cmd_no_marker() {
    local inner_cmd="${1:?dashboard_pane_wrap_cmd_no_marker: inner_cmd required}"

    printf 'trap %s %s; %s' \
        "''" \
        "${PGAI_DASHBOARD_PANE_SIGNAL}" \
        "${inner_cmd}"
}

# ---------------------------------------------------------------------------
# dashboard_wait_all_armed <armed_dir> <timeout_seconds> <label> [<label> ...]
# Blocks until every named pane label has a marker file under <armed_dir>,
# polling in a tight loop -- NOT a fixed sleep, the race this file exists
# to remove.  Poll granularity is 0.1s.
#
# Returns 0 once every label is armed.  Returns 1 once <timeout_seconds>
# has elapsed with NO further label reporting armed while at least one is
# still unarmed; on timeout, prints the still-pending labels to stdout,
# one per line, sorted, so the caller can name them in a refusal without
# re-deriving the diff.
#
# The bound is a stall bound, not a total-elapsed bound: every poll that
# finds the pending set smaller than the last poll did restarts the clock.
# What the refusal exists to catch is a pane whose script never reaches
# its first line -- a pane that will never arm, however long the wait.
# A loaded host arms the same panes slowly but steadily, and a total-time
# bound refused exactly that healthy-but-slow dashboard while every pane
# was still on its way to arming; a stall bound refuses only when arming
# has stopped making progress, so the verdict tracks the stuck pane rather
# than whatever else the host happened to be running.
# ---------------------------------------------------------------------------
dashboard_wait_all_armed() {
    local armed_dir="${1:?dashboard_wait_all_armed: armed_dir required}"
    local timeout_seconds="${2:?dashboard_wait_all_armed: timeout_seconds required}"
    shift 2
    local labels=("$@")

    if [[ "${#labels[@]}" -eq 0 ]]; then
        return 0
    fi

    # Nanosecond-resolution deadline -- NOT second-granularity `date +%s`,
    # which can undercut the requested timeout by up to ~1s depending on
    # where within the current second the wait started (measured: a
    # requested 2s timeout returning in 1.5s under `date +%s` arithmetic).
    local start_ns
    start_ns="$(date +%s%N)"
    local timeout_ns=$(( timeout_seconds * 1000000000 ))
    local pending=()
    local label
    # Smallest pending count seen so far; a poll that beats it is progress.
    local fewest_pending="${#labels[@]}"

    while true; do
        pending=()
        for label in "${labels[@]}"; do
            [[ -e "${armed_dir}/${label}" ]] || pending+=("$label")
        done

        if [[ "${#pending[@]}" -eq 0 ]]; then
            return 0
        fi

        local now_ns
        now_ns="$(date +%s%N)"
        if (( ${#pending[@]} < fewest_pending )); then
            # At least one more pane armed since the last poll: arming is
            # still progressing, so the stall clock starts over from now.
            fewest_pending="${#pending[@]}"
            start_ns="$now_ns"
        fi
        if (( now_ns - start_ns >= timeout_ns )); then
            printf '%s\n' "${pending[@]}" | sort
            return 1
        fi

        sleep 0.1
    done
}
