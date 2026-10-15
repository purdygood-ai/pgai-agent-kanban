#!/usr/bin/env bash
# activation_hook.sh — session-scoped tmux activation-hook lifecycle.
#
# WHAT THIS CLOSES
# -----------------
# A hidden pane does not render (lib/cadence.sh), so something must tell a
# pane the instant its window becomes visible, or it would stay stale until
# its slow visibility re-check. This file gives create.sh hooks it can
# install, scoped to the dashboard's own tmux session, that fire on every
# window switch (session-window-changed) and every client attach
# (client-attached -- a detached dashboard has no visible pane, so the
# window a client attaches to becomes visible at that moment) and hand off
# to lib/../activation-hook-sender.sh (the "sender") to signal the
# now-visible window's armed panes.
#
# WHY INSTALL TIMING MATTERS
# ---------------------------
# The hook must be installed ONLY after every pane create.sh dispatched has
# reported armed (lib/pane_readiness.sh's all-armed handshake). A pane
# whose ignore-trap has not landed yet is signalled exactly like any other
# process receives an un-trapped signal — the OS default, terminate.
# Installing the hook during pane creation (or any point before the
# handshake resolves) reopens the exact race the readiness handshake was
# built to close. See create.sh's own install call site for the ordering
# proof.
#
# WHY THE HOOK NAME IS session-window-changed, NOT window-pane-changed
# ----------------------------------------------------------------------
# tmux's option/hook system scopes each hook NAME to a fixed level
# (global, session, window, or pane) rather than letting the caller choose
# the scope via a flag. `session-window-changed` is a session-level array
# option — `set-hook -t <session>` (a bare session target, no window/pane
# qualifier) attaches it to that session's own hooks table, verified
# empirically: a second, unrelated session on the same server shows no
# entry for it under `tmux show-hooks -t <that-session>`. `window-pane-
# changed`, by contrast, is a WINDOW-level option — setting it against a
# session target silently attaches it to whichever window happens to be
# current at set-hook time, not the session as a whole, which would need
# re-registration on every new window and is not handled here (the
# activation fixture exercises window switches only).
# `session-window-changed` fires on exactly that: every window switch
# within the session it is set on, and never elsewhere.
#
# WHERE THE SENDER LIVES
# -----------------------
# team/scripts/dashboard/activation-hook-sender.sh, invoked via
# `run-shell -b` (backgrounded — the hook must never block the window
# switch it is reacting to). It resolves the session's now-current window
# on its own and is tolerant of an unarmed pane (skips it, narrates why,
# and continues to every other pane) — see that script's own header for
# the skip-and-narrate contract (team/SOP-core.md "Gates and Refusals":
# CONVERT-class, a continue, never a stop).

# Include guard: safe to source multiple times.
[[ -n "${_PGAI_ACTIVATION_HOOK_SH_LOADED:-}" ]] && return 0
_PGAI_ACTIVATION_HOOK_SH_LOADED=1

# The tmux hook names this file's lifecycle manages. An array so create.sh's
# install and kill.sh's remove iterate the exact same list and can never
# drift apart from each other. Both are session-level hooks: set with
# `-t <session>` they fire for that session only (client-attached verified
# the same way as session-window-changed: a second session on the same
# server shows no entry for it).
DASHBOARD_ACTIVATION_HOOK_NAMES=(session-window-changed client-attached)

# ---------------------------------------------------------------------------
# dashboard_install_activation_hook <kanban_root> <session_name> <script_dir>
# Registers the sender as a session-scoped tmux hook (`-t <session_name>`,
# never `-g`) for every name in DASHBOARD_ACTIVATION_HOOK_NAMES. Callers
# MUST call this only after the all-armed handshake has already succeeded
# for this session — see the header above.
# ---------------------------------------------------------------------------
dashboard_install_activation_hook() {
    local kanban_root="${1:?dashboard_install_activation_hook: kanban_root required}"
    local session_name="${2:?dashboard_install_activation_hook: session_name required}"
    local script_dir="${3:?dashboard_install_activation_hook: script_dir required}"
    local sender="${script_dir}/activation-hook-sender.sh"
    local hook_name

    for hook_name in "${DASHBOARD_ACTIVATION_HOOK_NAMES[@]}"; do
        tmux set-hook -t "$session_name" "$hook_name" \
            "run-shell -b \"${sender} --kanban-root ${kanban_root} --session ${session_name}\""
    done
}

# ---------------------------------------------------------------------------
# dashboard_remove_activation_hook <session_name>
# Unsets every hook name dashboard_install_activation_hook installed, for
# the given session only. Safe to call on a session that never had the
# hook installed (unsetting an unset hook is a tmux no-op) and tolerant of
# a session that no longer exists — every unset is individually swallowed
# so one failure never skips the rest. kill.sh calls this before
# `tmux kill-session`, mirroring its existing idempotent no-session exit
# and its scoped pgai_temp_cleanup call: an explicit, verifiable removal
# step rather than relying solely on the hook vanishing as an incidental
# side effect of the session object being destroyed.
# ---------------------------------------------------------------------------
dashboard_remove_activation_hook() {
    local session_name="${1:?dashboard_remove_activation_hook: session_name required}"
    local hook_name

    for hook_name in "${DASHBOARD_ACTIVATION_HOOK_NAMES[@]}"; do
        tmux set-hook -u -t "$session_name" "$hook_name" 2>/dev/null || true
    done
}
