#!/usr/bin/env bash
# team/scripts/lib/exit_trap.sh
#
# One EXIT trap, many cleanups. A bare `trap '<cmd>' EXIT` replaces any
# EXIT trap set before it, so an earlier cleanup silently never runs; a
# library that installs one also replaces the trap of the script that
# sourced it. Every EXIT cleanup in team/scripts/lib/ goes through this
# file instead, and so may any script.
#
# Functions:
#   exit_trap_add <command>
#       Register <command> (a string, eval'ed at exit, so variables in it
#       expand then) to run when this shell exits. Commands run most recent
#       first; after them runs whatever EXIT trap was already installed when
#       the first command was registered (a script's own `trap ... EXIT`),
#       so nothing set earlier is lost. Each command sees the exit status
#       the shell is exiting with as $?, and one that fails does not stop
#       the others; one that calls `exit` ends the chain there.
#   exit_trap_remove <command>
#       Drop a registered command (exact string match) without running it:
#       the cleanup already ran on the ordinary path. Other commands and the
#       earlier trap stay registered.
#   exit_trap_list
#       Print the registered commands, one per line, in the order they run.
#
# A subshell does not inherit its parent's EXIT trap, so registering inside
# a subshell starts that subshell's own empty chain; the parent's commands
# never run when a subshell exits.
#
# Must be called directly (never through command substitution) so the trap
# lands in the caller's shell. Safe to source more than once.

if [[ -z "${_PGAI_EXIT_TRAP_SH_LOADED:-}" ]]; then
    _PGAI_EXIT_TRAP_SH_LOADED=1
    declare -ga _PGAI_EXIT_TRAP_CMDS=()
    declare -g _PGAI_EXIT_TRAP_PRIOR=""
    declare -g _PGAI_EXIT_TRAP_OWNER=""
fi

# The exit status the chain hands each command, as $?.
_pgai_exit_trap_status() {
    return "$1"
}

# The EXIT trap body: every registered command, most recent first, then the
# trap that was installed before the first one.
_pgai_exit_trap_run() {
    local _rc=$? _i
    set +e
    for (( _i=${#_PGAI_EXIT_TRAP_CMDS[@]}-1; _i>=0; _i-- )); do
        _pgai_exit_trap_status "$_rc"
        eval "${_PGAI_EXIT_TRAP_CMDS[_i]}"
    done
    if [[ -n "$_PGAI_EXIT_TRAP_PRIOR" ]]; then
        _pgai_exit_trap_status "$_rc"
        eval "$_PGAI_EXIT_TRAP_PRIOR"
    fi
    return "$_rc"
}

# Install the dispatcher in this shell unless it is already the active trap.
_pgai_exit_trap_install() {
    local _spec _current=""
    _spec="$(trap -p EXIT)"
    if [[ -n "$_spec" ]]; then
        local -a _parts=()
        eval "_parts=(${_spec})"
        _current="${_parts[2]:-}"
    fi
    if [[ "$_PGAI_EXIT_TRAP_OWNER" != "$BASHPID" ]]; then
        # A new shell (the first registration, or a subshell): its own chain.
        _PGAI_EXIT_TRAP_CMDS=()
        _PGAI_EXIT_TRAP_PRIOR=""
        _PGAI_EXIT_TRAP_OWNER="$BASHPID"
        # A subshell reports its parent's trap through `trap -p` without
        # running it, so only the owning shell's own trap is kept.
        if [[ "$BASHPID" == "$$" && "$_current" != "_pgai_exit_trap_run" ]]; then
            _PGAI_EXIT_TRAP_PRIOR="$_current"
        fi
    elif [[ -n "$_current" && "$_current" != "_pgai_exit_trap_run" ]]; then
        # Something replaced the dispatcher since: keep it, run it last.
        _PGAI_EXIT_TRAP_PRIOR="$_current"
    fi
    trap _pgai_exit_trap_run EXIT
}

exit_trap_add() {
    local _cmd="${1:-}"
    [[ -n "$_cmd" ]] || return 0
    _pgai_exit_trap_install
    _PGAI_EXIT_TRAP_CMDS+=("$_cmd")
}

exit_trap_remove() {
    local _cmd="${1:-}" _entry
    local -a _kept=()
    [[ "$_PGAI_EXIT_TRAP_OWNER" == "$BASHPID" ]] || return 0
    for _entry in "${_PGAI_EXIT_TRAP_CMDS[@]}"; do
        [[ "$_entry" == "$_cmd" ]] || _kept+=("$_entry")
    done
    _PGAI_EXIT_TRAP_CMDS=("${_kept[@]}")
}

exit_trap_list() {
    local _i
    [[ "$_PGAI_EXIT_TRAP_OWNER" == "$BASHPID" ]] || return 0
    for (( _i=${#_PGAI_EXIT_TRAP_CMDS[@]}-1; _i>=0; _i-- )); do
        printf '%s\n' "${_PGAI_EXIT_TRAP_CMDS[_i]}"
    done
}
