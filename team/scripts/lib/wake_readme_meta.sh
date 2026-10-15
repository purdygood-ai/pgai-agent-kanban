#!/usr/bin/env bash
# team/scripts/lib/wake_readme_meta.sh
#
# Wake README-metadata stage: pure readers for the per-task README fields
# that drive dispatch — ## Role, ## Assigned Agent, and ## Force Model / ##
# Model Override — plus the shared role-name validator both readers feed
# into. Extracted from team/scripts/lib/wake_common.sh's former inline body
# so wake_common.sh's own remaining code stays a thinner shell over sourced
# lib stages. Behavior-preserving: no stdout/stderr line, exit code, or
# on-disk file content changes as a result of this file existing — it is
# one stage split out of a larger driver so each stays a focused,
# independently testable unit.
#
# Public entry point
# -------------------
#   get_role_from_readme <readme-path>
#       Extracts a task README's raw ## Role value, uppercased and trimmed.
#       Called by both wake provider scripts (claude.sh, codex.sh) during
#       task dispatch, before the extracted value is validated against
#       role_is_valid_agent and used to select a subagent.
#
# Other functions in this file — get_assigned_agent_from_readme,
# role_is_valid_agent, get_model_override_from_readme — are shared
# primitives, not private helpers of get_role_from_readme alone. Both wake
# provider scripts call all four directly during dispatch. Neither reader
# validates or defaults the value it extracts; callers fail closed (BLOCKED,
# never a silent CODER fallback) on a bad or unrecognized value. Defined at
# source time, available as soon as this file is sourced.
#
# Dependencies (must already be sourced/defined by the caller before this
# file's functions are called)
# -----------------------------------------------------------------------
#   None beyond python3 on PATH (used by all four functions' heredocs).
#
# Sourced by wake_common.sh alongside its other README-metadata-adjacent
# stages; has no dependency on wake_bootstrap.sh or wake_task_state.sh
# globals, so sourcing order relative to those two is not load-bearing.

# get_role_from_readme <readme-path>
# Extracts the raw ## Role value, uppercased and trimmed. Prints an empty
# string when the section is absent or empty. Does NOT validate or default
# the value — callers must check the result against role_is_valid_agent
# and fail closed (BLOCKED, never a silent CODER fallback) on a bad value.
get_role_from_readme() {
  local readme="$1"
  python3 - "$readme" <<'PY'
import pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text()
m = re.search(r'(^## Role[^\S\n]*)((?:\n(?!##)[^\n]*)*?)(\n+##|\n*\Z)', text, flags=re.M)
if not m:
    print("")
    raise SystemExit(0)
role = m.group(2).strip().split('|')[0].strip().upper()
print(role)
PY
}

# get_assigned_agent_from_readme <readme-path>
# Extracts the raw ## Assigned Agent value, uppercased and trimmed. Prints
# an empty string when the section is absent, empty, or explicitly "none"
# (case-insensitive) — those all mean "not present" to dispatch. A
# non-empty result may still be invalid; callers validate with
# role_is_valid_agent before using it to select a subagent.
get_assigned_agent_from_readme() {
  local readme="$1"
  python3 - "$readme" <<'PY'
import pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text()
m = re.search(r'(^## Assigned Agent[^\S\n]*)((?:\n(?!##)[^\n]*)*?)(\n+##|\n*\Z)', text, flags=re.M)
if not m:
    print("")
    raise SystemExit(0)
value = m.group(2).strip().split('|')[0].strip().upper()
if not value or value == "NONE":
    value = ""
print(value)
PY
}

# role_is_valid_agent <value>
# True (exit 0) when <value> is one of the six recognized task roles /
# subagent names. Shared by every wake provider's dispatch assertion so the
# valid-role set is defined in exactly one place.
role_is_valid_agent() {
  case "$1" in
    CODER|WRITER|CM|TESTER|PO|PM) return 0 ;;
    *) return 1 ;;
  esac
}

get_model_override_from_readme() {
  local readme="$1"
  python3 - "$readme" <<'PY'
import pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text()
m = re.search(r'^##\s+(?:Force\s+Model|Model\s+Override)[^\S\n]*\n(.*?)(\n+##|\Z)', text, flags=re.S | re.M | re.I)
if not m:
    raise SystemExit(0)
value = m.group(1).strip()
if not value or value.lower() == 'none':
    raise SystemExit(0)
print(value)
PY
}
