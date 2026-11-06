#!/usr/bin/env bash
# operator_shell.sh — the one start-up sequence for the operator window's
# interactive shell panes.
#
# An operator shell pane is a resident `bash -l` login shell, cwd the
# kanban root, with the pane signal ignored, the operator's ~/.bashrc
# sourced when present, and the kanban shell-env sourced last, opening on a
# clean screen with only its prompt. Every interactive pane in the operator
# window is started by this one function, so their environments cannot
# drift apart.
#
# Public API
# ----------
#   dashboard_init_operator_shell <window.pane> <title>
#       Starts the shell in "${SESSION_NAME}:<window.pane>" (an existing,
#       still-idle pane) and sets the pane's border title to <title>. Six
#       steps, in this order:
#         1. First command, through dashboard_send_armed_keys
#            --defer-marker: bash -l -c 'source shell-env && cd KANBAN_ROOT;
#            exec bash -l' (the shell-env part only when the file exists).
#            The pane is recorded in PANE_ARMED_LABELS, so create.sh's
#            all-armed handshake waits on it.
#         2. Re-arm, typed into the resident shell: the ignore trap for
#            PGAI_DASHBOARD_PANE_SIGNAL and the pane's armed-marker touch,
#            on ONE typed line.
#         3. Guarded ~/.bashrc source, typed into the resident shell.
#         4. shell-env re-source (only when the file exists).
#         5. Screen clear, typed last: erases the start-up lines above and
#            the pane's scrollback, leaving only the prompt.
#         6. select-pane -T <title>. The window's active pane is left to
#            the caller.
#
#       Every line steps 1-5 type ends with the typed-line source tag
#       (dashboard_typed_line), so each is recognisably the dashboard's in
#       the operator's shell history.
#
#       Runs inside create.sh's process and reads its state: the
#       dashboard_send_armed_keys and dashboard_typed_line functions and the
#       SESSION_NAME, KANBAN_ROOT, PANE_ARMED_DIR and
#       PGAI_DASHBOARD_PANE_SIGNAL variables (the last from
#       lib/pane_readiness.sh).
#
# WHY THE RE-ARM (step 2)
# -----------------------
# The trap the wrapped first command installs survives the `bash -l -c`
# fork, but not the final `exec bash -l`: a freshly started bash resets an
# inherited SIG_IGN for a non-job-control signal (verified against tmux
# 3.2a / bash 5.x via /proc/<pid>/status SigIgn straddling the exec), so
# the shell that stays resident is unprotected until the trap builtin runs
# again inside it. --defer-marker keeps step 1 from touching the armed
# marker, because that touch would be true of the shell running step 1,
# not of the resident one; step 2 touches the marker on the same typed line
# as the trap, so the marker can never outrun the resident shell's own
# trap. A session-window-changed hook (or the all-armed handshake) that
# sees the marker therefore always sees a shell that ignores the signal.
#
# WHY BASHRC IS TYPED, NOT FOLDED INTO STEP 1 (step 3)
# ----------------------------------------------------
# `exec bash -l` replaces the process, which would discard any aliases a
# `source ~/.bashrc` had defined earlier in it. Typed into the resident
# shell, the aliases survive. Guarded, so a host without a ~/.bashrc starts
# silently (no error output in the pane).
#
# WHY SHELL-ENV IS RE-SOURCED LAST (step 4)
# -----------------------------------------
# The operator's ~/.bashrc is arbitrary and may redefine PATH or unset or
# override a PGAI_* value. Whichever source runs LAST wins, so a bashrc run
# after shell-env could clobber the kanban env. Re-sourcing shell-env LAST,
# after bashrc, leaves kanban's values standing whatever bashrc does.
#
# WHY THE SCREEN CLEAR IS TYPED LAST (step 5)
# -------------------------------------------
# Every line the steps above type is echoed into the pane with a prompt, so
# without it the shell opens under four or five lines of start-up commands.
# The resident shell runs typed lines in order, so a clear typed after the
# last of them runs after all of them, and only the next prompt is left on
# screen. It is a printf of the terminal's own erase sequences (home, erase
# display, erase scrollback) rather than clear(1), so it needs no ncurses
# binary and no terminfo entry. It changes no shell state: the trap, the
# bashrc aliases and the shell-env values set above stay in effect. Output
# the operator's ~/.bashrc prints at start-up is cleared with the rest.

# Source guard: safe to source multiple times.
[[ -n "${_PGAI_DASHBOARD_OPERATOR_SHELL_SH_LOADED:-}" ]] && return 0
_PGAI_DASHBOARD_OPERATOR_SHELL_SH_LOADED=1

dashboard_init_operator_shell() {
    local pane_target="${1:?dashboard_init_operator_shell: <window.pane> required}"
    local title="${2:?dashboard_init_operator_shell: <title> required}"
    local tmux_target="${SESSION_NAME}:${pane_target}"
    local shell_env="${KANBAN_ROOT}/shell-env"

    # 1. First command: login shell, cwd the kanban root.
    local shell_init="cd ${KANBAN_ROOT}"
    if [[ -f "$shell_env" ]]; then
        shell_init="source ${shell_env} && ${shell_init}"
    fi
    dashboard_send_armed_keys "${pane_target}" "bash -l -c '${shell_init}; exec bash -l'" --defer-marker

    # 2. Re-arm the trap in the resident shell and touch its armed marker,
    #    on one typed line (see "WHY THE RE-ARM" above).
    tmux send-keys -t "$tmux_target" "$(dashboard_typed_line "trap '' ${PGAI_DASHBOARD_PANE_SIGNAL}; : > $(printf '%q' "${PANE_ARMED_DIR}/${pane_target}")")" Enter

    # 3. Guarded ~/.bashrc source (see "WHY BASHRC IS TYPED" above).
    tmux send-keys -t "$tmux_target" "$(dashboard_typed_line '[ -f "$HOME/.bashrc" ] && source "$HOME/.bashrc"')" Enter

    # 4. shell-env after bashrc, so a clobbering ~/.bashrc never wins.
    if [[ -f "$shell_env" ]]; then
        tmux send-keys -t "$tmux_target" "$(dashboard_typed_line "source ${KANBAN_ROOT}/shell-env")" Enter
    fi

    # 5. Clear the screen and scrollback last, leaving only the prompt
    #    (see "WHY THE SCREEN CLEAR IS TYPED LAST" above).
    tmux send-keys -t "$tmux_target" "$(dashboard_typed_line "printf '\\033[H\\033[2J\\033[3J'")" Enter

    # 6. Border title.
    tmux select-pane -t "$tmux_target" -T "$title"
}
