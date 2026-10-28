#!/usr/bin/env bash
# team/scripts/lib/md_field.sh
#
# The one bash reader of a "## Heading" section in a markdown file
# (status.md, release-state.md, task READMEs, requirement and bug files).
# read_md_field parses nothing itself: it runs the section reader's own CLI,
# the "read" subcommand of the pgai_agent_kanban.task_status module, through
# pp_run_ops, so bash and Python read every section by the same rules
# (task_status read_section's docstring states them: heading match, blank
# lines, end of section, missing heading versus empty value, CRLF).
#
# SOURCE THIS FILE; do not execute it directly. Sourcing only defines the
# function: it sets no shell options, installs no trap and parses no
# arguments. It loads pp_run_ops.sh from beside it only when no pp_run_ops
# is defined yet, so a pp_run_ops the caller already defined (a wrapper, a
# test stub) stays the active one. read_md_field itself never runs the
# caller's: each call runs the section reader through the pp_run_ops.sh
# beside this file, in a subshell, so the reader runs from this file's own
# tree whatever pp_run_ops is defined around it.
#
#   # From team/scripts/<dir>/*.sh:
#   # shellcheck source=../lib/md_field.sh
#   source "$(dirname "${BASH_SOURCE[0]}")/../lib/md_field.sh"
#
# FUNCTION
# --------
# read_md_field [--value] [READING...] [--] <file> <heading> [<heading>...]
#
#   <file>     the markdown file to read.
#   <heading>  a heading name without the leading "## " (e.g. "Active RC").
#
#   Output, default (body) mode -- exactly one <heading>:
#     the section body with surrounding whitespace removed, every line
#     kept; an empty line when the heading is present with no body.
#   Output, --value mode -- one or more <heading>s:
#     one line per heading, in the order named: the first value line of
#     its body (blank, "#" comment and HTML comment lines skipped, a "---"
#     rule ends the walk), or an empty line when the heading has no value
#     or is absent.
#
#   READING (any of these; each defaults to the status.md reading, in
#   which the heading needs one or more blanks after "##", any letter case
#   and an inline "## Heading: value" are accepted, and the next "##"
#   heading that is not "###" ends the section):
#     --exact-heading       the line must read exactly "## <heading>" once
#                           stripped (letter case counts, no inline value);
#                           any line starting "##" after its indentation
#                           ends the section
#     --free-spacing        "##" and each word of <heading> may be
#                           separated by any run of blanks, none included;
#                           any letter case; no inline value
#     --comments            a "##" line inside an HTML comment block
#                           neither starts nor ends a section
#     --end-at-subheading   a "###" heading ends the section too
#     --end-at-rule         a "---" rule starting a line ends it too
#
#   Exit status:
#     0  every heading is present -- including a heading whose body or
#        value is empty, which prints an empty line
#     3  a heading is absent: body mode prints nothing; --value mode
#        prints every line (an empty one for the absent heading) first
#     1  <file> is missing or unreadable (one line on stderr, nothing on
#        stdout), or a usage error (one line on stderr naming it)
#
#   A byte in <file> that is not valid UTF-8 does not make it unreadable:
#   it reads as U+FFFD and every section is read as usual.
#
#   Examples:
#     state="$(read_md_field --value "$status_file" State)"
#     body="$(read_md_field "$readme" Goal)" || [[ $? -eq 3 ]]
#     read_md_field --value --exact-heading --comments "$release_state" \
#         "Active RC" "RC Opened At"

# Guard against double-sourcing.
[[ -n "${_PGAI_MD_FIELD_SH_LOADED:-}" ]] && return 0
_PGAI_MD_FIELD_SH_LOADED=1

_PGAI_MD_FIELD_LIB_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# A pp_run_ops the caller already defined wins; read_md_field does not use it.
if ! declare -F pp_run_ops >/dev/null 2>&1; then
  # shellcheck source=pp_run_ops.sh
  source "${_PGAI_MD_FIELD_LIB_DIR}/pp_run_ops.sh"
fi

read_md_field() {
  local mode="--raw"
  local -a reading=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --value) mode="--value"; shift ;;
      --exact-heading|--free-spacing|--comments|--end-at-subheading|--end-at-rule)
        reading+=("$1"); shift ;;
      --) shift; break ;;
      --*)
        echo "read_md_field: unknown option '$1'; accepted: --value --exact-heading --free-spacing --comments --end-at-subheading --end-at-rule" >&2
        return 1 ;;
      *) break ;;
    esac
  done
  if [[ $# -lt 2 ]]; then
    echo "read_md_field: needs <file> and at least one <heading>, e.g. read_md_field --value status.md State" >&2
    return 1
  fi
  if [[ "$mode" == "--raw" && $# -gt 2 ]]; then
    echo "read_md_field: body mode reads one <heading>; pass --value to read several" >&2
    return 1
  fi
  local file="$1" heading
  shift
  local -a sections=()
  for heading in "$@"; do
    sections+=("--section=${heading}")
  done
  # This tree's own pp_run_ops, loaded in a subshell so the caller's
  # definition is neither used nor replaced.
  (
    # shellcheck source=pp_run_ops.sh
    source "${_PGAI_MD_FIELD_LIB_DIR}/pp_run_ops.sh" || exit 1
    pp_run_ops pgai_agent_kanban.task_status read "$mode" "${reading[@]}" "${sections[@]}" -- "$file"
  )
}
