#!/usr/bin/env bash
# team/scripts/lib/atomic_write.sh
#
# Same-directory temp-file-plus-rename replacement of a file, for every
# writer whose readers may open the file at any moment: a task's status.md,
# the per-agent queue files, and any other machine-parsed surface the API
# and dashboard read while the wake layer is rewriting it.
#
# Why same-directory: rename(2) is atomic only within one filesystem, so
# the scratch copy is created beside its target, never under the framework
# temp root (which may be a different mount). A reader that opens the
# target mid-replacement sees either the complete old bytes or the complete
# new bytes; it can never observe an empty or half-written file, which a
# truncate-then-write (`>` redirection, Python's write_text) exposes for the
# whole duration of the write.
#
# Source this file to get the function:
#   source "$(dirname "${BASH_SOURCE[0]}")/atomic_write.sh"
#
# Function
# --------
#   pgai_atomic_write <target> <command> [args...]
#       Runs <command> with its stdout redirected into a fresh scratch file
#       beside <target>, then renames the scratch file over <target> — but
#       only when the command exits 0. A non-zero command exit removes the
#       scratch file, leaves <target> untouched, and is returned unchanged;
#       stderr passes through, so the command's own message reaches the
#       caller. stdin is inherited, so input attached to the call reaches
#       the command. The usual <command> is a module call that prints the
#       whole new file to stdout and writes nothing itself — the shape
#       every writer in wake_task_state.sh uses:
#           pgai_atomic_write "$status_file" pp_run_ops \
#               pgai_agent_kanban.task_status render "$status_file" \
#               --section=State --body=BLOCKED
#       The scratch file carries <target>'s own mode, or the umask default
#       a plain `>` redirection would give a new file when <target> does
#       not exist yet. Returns 1, writing nothing, when the directory is
#       missing or not writable or the rename fails.
#
# Every failure prints one plain-text line to stderr naming the path and the
# operation that failed. Callers that are refusal sites wrap that text in
# the dual-channel error contract themselves; this file is a primitive, not
# a gate.
#
# Safety invariants:
#   - No top-level side effects when sourced — function definitions only.
#   - On every failure path the scratch file is removed before returning,
#     including a command failure under `set -e` (the command runs as an
#     `if` condition, where errexit is suspended); a successful commit
#     consumes it. Only a kill between create and rename can leave one
#     behind, and its name marks it as scratch.
#   - The scratch name is `.<target-basename>.tmp.XXXXXX`: dot-prefixed and
#     randomly suffixed, so a queue-directory listing or a task-folder
#     parser never mistakes one for a real file.

# Include guard: safe to source multiple times (only loads once).
[[ -n "${_PGAI_ATOMIC_WRITE_SH_LOADED:-}" ]] && return 0
_PGAI_ATOMIC_WRITE_SH_LOADED=1

# ---------------------------------------------------------------------------
# _pgai_atomic_default_mode
# Prints the octal mode a plain `>` redirection would give a brand-new file
# under the current umask (0666 masked), for a scratch file whose target
# does not exist yet. mktemp itself creates 0600, which would leave a new
# status.md unreadable to a dashboard running as another user.
# ---------------------------------------------------------------------------
_pgai_atomic_default_mode() {
  local mask
  mask="$(umask)"
  printf '%04o' "$(( 0666 & ~(8#${mask}) ))"
}

# ---------------------------------------------------------------------------
# _pgai_atomic_stage <target>
# Creates and prints the path of the scratch file beside <target>. Returns 1,
# creating nothing, when the directory is missing or not writable.
# ---------------------------------------------------------------------------
_pgai_atomic_stage() {
  local target="$1"
  local dir base scratch
  dir="$(dirname -- "$target")"
  base="$(basename -- "$target")"
  if [[ ! -d "$dir" ]]; then
    echo "pgai_atomic_write: directory ${dir} does not exist; cannot stage a replacement for ${target}" >&2
    return 1
  fi
  if ! scratch="$(mktemp -- "${dir}/.${base}.tmp.XXXXXX" 2>/dev/null)"; then
    echo "pgai_atomic_write: cannot create a scratch file in ${dir} (directory not writable?); ${target} left untouched" >&2
    return 1
  fi
  if [[ -e "$target" ]]; then
    chmod --reference="$target" -- "$scratch" 2>/dev/null || true
  else
    chmod "$(_pgai_atomic_default_mode)" -- "$scratch" 2>/dev/null || true
  fi
  printf '%s\n' "$scratch"
}

# ---------------------------------------------------------------------------
# _pgai_atomic_commit <scratch> <target>
# Renames <scratch> over <target>. On failure removes <scratch> and returns
# 1; <target> is untouched.
# ---------------------------------------------------------------------------
_pgai_atomic_commit() {
  local scratch="$1"
  local target="$2"
  if ! mv -f -- "$scratch" "$target" 2>/dev/null; then
    rm -f -- "$scratch" 2>/dev/null || true
    echo "pgai_atomic_write: cannot rename ${scratch} over ${target}; ${target} left untouched" >&2
    return 1
  fi
  return 0
}

# ---------------------------------------------------------------------------
# pgai_atomic_write <target> <command> [args...]
# ---------------------------------------------------------------------------
pgai_atomic_write() {
  local target="$1"
  shift
  if (( $# == 0 )); then
    echo "pgai_atomic_write: no command given to produce the new contents of ${target}" >&2
    return 2
  fi
  local scratch rc
  scratch="$(_pgai_atomic_stage "$target")" || return 1
  # Run as an `if` condition so a failing producer under `set -e` still
  # reaches the cleanup below instead of aborting the shell with the
  # scratch file left beside the target.
  if "$@" > "$scratch"; then
    _pgai_atomic_commit "$scratch" "$target"
    return $?
  else
    rc=$?
    rm -f -- "$scratch" 2>/dev/null || true
    return "$rc"
  fi
}
