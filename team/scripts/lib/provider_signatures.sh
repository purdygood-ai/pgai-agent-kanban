#!/usr/bin/env bash
# team/scripts/lib/provider_signatures.sh
#
# The bash reader of the provider-error signature table
# (config/provider-error-signatures.txt; its header documents the line format,
# the three classes -- transient, refusal, billing -- and the action each
# takes). The table is parsed by one module only,
# pgai_agent_kanban.lib.provider_signatures: on first use this file runs that
# module's "patterns" subcommand through the pp_run_ops.sh beside it, so the
# table is the one this tree installed, and keeps one combined grep -E pattern
# per class for the rest of the shell's life. Matching itself is grep -iE, one
# here-string per class.
#
# Here-strings, never `echo | grep -q`: grep -q exits on its first match, and
# a writer still filling the pipe then dies of SIGPIPE; under `set -o pipefail`
# (every caller's mode) that reports a match as a miss. A here-string hands
# grep its input before grep runs, so there is no writer to lose.
#
# SOURCE THIS FILE; do not execute it directly. Sourcing only defines the
# functions; it sets no shell options and runs nothing.
#
#   # shellcheck source=provider_signatures.sh
#   source "$(dirname "${BASH_SOURCE[0]}")/provider_signatures.sh"
#
# FUNCTIONS
# ---------
# provider_signatures_load
#     Read the table once into this shell. Later calls return at once. Call it
#     before a loop or a command substitution so the table is read in the
#     current shell, not once per subshell. Exit 0 loaded; 2 the table cannot
#     be read (the module's error contract, naming the table's path, is on
#     stderr).
#
# provider_signature_class <text>
#     Print the class of <text> -- the highest-precedence class whose
#     signature it carries (billing, then refusal, then transient) -- and
#     exit 0; print nothing and exit 1 when it carries none; exit 2 when the
#     table cannot be read.
#
# provider_signature_is <class> <text>
#     Exit 0 when <text> classes as <class>, 1 when it does not (another
#     class, or none), 2 when the table cannot be read. Prints nothing.
#
# provider_signature_pattern <class> [--extract]
#     Print <class>'s combined grep -E pattern. With --extract the pattern
#     runs from the first signature to the end of its line, for grep -o to
#     quote what was matched. Exit 1 for a class the table does not define,
#     2 when the table cannot be read.
#
# Requires python3 on PATH.

[[ -n "${_PGAI_PROVIDER_SIGNATURES_SH_LOADED:-}" ]] && return 0
_PGAI_PROVIDER_SIGNATURES_SH_LOADED=1

_PGAI_PROVIDER_SIGNATURES_LIB_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# Classes in precedence order, and each one's combined pattern, index-aligned.
declare -ga _PGAI_PROVIDER_SIGNATURE_CLASSES=()
declare -ga _PGAI_PROVIDER_SIGNATURE_PATTERNS=()

provider_signatures_load() {
  (( ${#_PGAI_PROVIDER_SIGNATURE_CLASSES[@]} > 0 )) && return 0
  local _out _klass _pattern
  # This tree's own pp_run_ops, in a subshell, so the module and the table it
  # reads come from beside this file whatever pp_run_ops the caller defined.
  _out="$(
    # shellcheck source=pp_run_ops.sh
    source "${_PGAI_PROVIDER_SIGNATURES_LIB_DIR}/pp_run_ops.sh" || exit 2
    pp_run_ops pgai_agent_kanban.lib.provider_signatures patterns
  )" || return 2
  while IFS=$'\t' read -r _klass _pattern; do
    [[ -n "${_klass}" && -n "${_pattern}" ]] || continue
    _PGAI_PROVIDER_SIGNATURE_CLASSES+=("${_klass}")
    _PGAI_PROVIDER_SIGNATURE_PATTERNS+=("${_pattern}")
  done <<< "${_out}"
  if (( ${#_PGAI_PROVIDER_SIGNATURE_CLASSES[@]} == 0 )); then
    echo "provider_signatures: the signature table reader printed no class; to see why, run: source ${_PGAI_PROVIDER_SIGNATURES_LIB_DIR}/pp_run_ops.sh && pp_run_ops pgai_agent_kanban.lib.provider_signatures patterns" >&2
    return 2
  fi
  return 0
}

provider_signature_class() {
  local _text="${1:-}" _i
  provider_signatures_load || return 2
  for _i in "${!_PGAI_PROVIDER_SIGNATURE_CLASSES[@]}"; do
    if grep -qiE -- "${_PGAI_PROVIDER_SIGNATURE_PATTERNS[_i]}" <<< "${_text}"; then
      printf '%s\n' "${_PGAI_PROVIDER_SIGNATURE_CLASSES[_i]}"
      return 0
    fi
  done
  return 1
}

provider_signature_is() {
  local _want="${1:-}" _text="${2:-}" _i
  provider_signatures_load || return 2
  for _i in "${!_PGAI_PROVIDER_SIGNATURE_CLASSES[@]}"; do
    if grep -qiE -- "${_PGAI_PROVIDER_SIGNATURE_PATTERNS[_i]}" <<< "${_text}"; then
      if [[ "${_PGAI_PROVIDER_SIGNATURE_CLASSES[_i]}" == "${_want}" ]]; then
        return 0
      fi
      return 1
    fi
  done
  return 1
}

provider_signature_pattern() {
  local _want="${1:-}" _extract="${2:-}" _i
  provider_signatures_load || return 2
  for _i in "${!_PGAI_PROVIDER_SIGNATURE_CLASSES[@]}"; do
    if [[ "${_PGAI_PROVIDER_SIGNATURE_CLASSES[_i]}" == "${_want}" ]]; then
      if [[ "${_extract}" == "--extract" ]]; then
        printf '(%s).*\n' "${_PGAI_PROVIDER_SIGNATURE_PATTERNS[_i]}"
      else
        printf '%s\n' "${_PGAI_PROVIDER_SIGNATURE_PATTERNS[_i]}"
      fi
      return 0
    fi
  done
  echo "provider_signature_pattern: unknown class '${_want}'; accepted: ${_PGAI_PROVIDER_SIGNATURE_CLASSES[*]}" >&2
  return 1
}
