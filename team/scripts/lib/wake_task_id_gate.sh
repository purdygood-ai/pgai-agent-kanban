#!/usr/bin/env bash
# team/scripts/lib/wake_task_id_gate.sh
#
# Wake task-id gate: where a task id read out of a queue line or a
# ## Prerequisites token is validated before it can be joined onto
# TASKS_ROOT. It carries the queue-line refusal the three queue scans in
# wake_task_state.sh (pending-task, WAITING, BLOCKED) and the stale-active
# sweep in wake_reconcile_sweeps.sh ([A]) call before they read ids, and
# check_prerequisites itself — a task-id reader end to end: token,
# shape check, status lookup. One of the single-concern lib stages
# wake_task_state.sh sources, kept separate so that stage file stays under
# the engine-decomposition line ceiling (team/scripts/lint_engine_size_dupe.py).
#
# The shape every id is validated against is the one task_ids.sh's
# kanban_task_id_pattern prints — a single source the bash callers and the
# Python modules they call read identically. An id outside it ('..', '../x', 'a.b',
# anything with a separator or a dot) is refused dual-channel here, naming
# the file and line, and the caller skips it: a refused id never becomes a
# path. Both refusals are CONVERT (docs/blocking-site-census.md): a
# malformed line is a defect in that one line, not grounds to stop the
# wake, so every well-formed entry is still served.
#
# Functions
# ---------
#   _malformed_queue_entries <queue_file> <marker_class> <canonical_pattern>
#   _narrate_malformed_queue_task_ids <queue_file> <marker_class> <scan_name>
#   _narrate_malformed_prerequisite_ids <readme> <check_output>
#   _drop_archived_prerequisites <check_output>
#   check_prerequisites <readme>
#       Prints one `<id>:<STATE|MISSING|UNPARSEABLE|MALFORMED>` line per
#       unsatisfied prerequisite and exits 1; exits 0 with no output when
#       every prerequisite is DONE or WONT-DO (or there are none). Called
#       by wake_driver_prep.sh at dispatch and by recheck_waiting_tasks.
#       Both sections are read with read_md_field (md_field.sh): a body
#       stops at the next "##" line, "###" included, so an empty
#       ## Prerequisites means none and an empty prerequisite ## State
#       prints as `<id>:` (unsatisfied), never as the next section's text.
#       A README that cannot be read prints nothing and exits 1.
#       A prerequisite is read from its live TASKS_ROOT/<id>/status.md
#       first; only one with no live folder at all is looked up in the
#       project's archive (<kanban_root>/archive/<project>/tasks/<id>/,
#       derived from TASKS_ROOT), and an archived one counts as DONE —
#       only finished tasks are ever archived. One that is neither live
#       nor archived still prints as `<id>:MISSING`.
#
# Dependencies: TASKS_ROOT (wake_bootstrap.sh), task_ids.sh
# (kanban_task_id_pattern), error_contract.sh (pgai_emit_error_contract),
# pp_run_ops.sh (pp_run_ops, which reaches the archive lookup as the
# archive module's `archived_tasks` verb, and the queue scan as
# pgai_agent_kanban.lib.queue_task_id_scan) and md_field.sh (read_md_field,
# the section reader) — all function-only and self-sourced below; python3
# on PATH. pp_run_ops.sh is loaded only when no pp_run_ops is defined yet,
# and loading md_field.sh puts the caller's pp_run_ops back afterwards, so
# a caller's own definition (a wrapper, or a test stub) stays the active
# one for the archive lookup. The section reads and the queue scan always run
# this tree's own pp_run_ops (_prerequisite_section_body,
# _malformed_queue_entries), so such a definition never stands in for them.

_WAKE_TASK_ID_GATE_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=task_ids.sh
source "${_WAKE_TASK_ID_GATE_LIB_DIR}/task_ids.sh"
# shellcheck source=error_contract.sh
source "${_WAKE_TASK_ID_GATE_LIB_DIR}/error_contract.sh"
# A caller's own pp_run_ops (wake_bootstrap.sh's, or a test stub) wins.
# shellcheck source=pp_run_ops.sh
declare -F pp_run_ops >/dev/null || source "${_WAKE_TASK_ID_GATE_LIB_DIR}/pp_run_ops.sh"
# md_field.sh leaves a defined pp_run_ops in place; the definition active
# before it is also saved and restored around the load.
if ! declare -F read_md_field >/dev/null; then
  _wake_task_id_gate_runner="$(declare -f pp_run_ops)"
  # shellcheck source=md_field.sh
  source "${_WAKE_TASK_ID_GATE_LIB_DIR}/md_field.sh"
  eval "$_wake_task_id_gate_runner"
  unset _wake_task_id_gate_runner
fi

# ---------------------------------------------------------------------------
# _malformed_queue_entries <queue_file> <marker_class> <canonical_pattern>
#
# Print one `<line number>\t<line>` per entry of <queue_file> whose marker
# is in <marker_class> and whose task id <canonical_pattern> does not match
# (pgai_agent_kanban.lib.queue_task_id_scan; its docstring has the entry
# shape and the '[]' alias rule). An unreadable queue file prints nothing.
# Runs in a subshell that loads this tree's own pp_run_ops first: a
# caller's pp_run_ops is aimed at the archive lookup and never answers for
# a queue file. Exits as the module does: 0.
# ---------------------------------------------------------------------------
_malformed_queue_entries() (
  # shellcheck source=pp_run_ops.sh
  source "${_WAKE_TASK_ID_GATE_LIB_DIR}/pp_run_ops.sh"
  pp_run_ops pgai_agent_kanban.lib.queue_task_id_scan -- "$1" "$2" "$3"
)

# ---------------------------------------------------------------------------
# _narrate_malformed_queue_task_ids <queue_file> <marker_class> <scan_name>
#
# Refuse, dual-channel, every line of <queue_file> that is shaped like a
# queue entry whose marker character is in <marker_class> (the body of a
# regex character class naming the markers the calling scan acts on, e.g.
# ' B' for the pending scan, 'W ' for the WAITING scan) but whose task id is
# outside the canonical shape (kanban_task_id_pattern, task_ids.sh). Each
# refusal names the queue file, the line number and the line, and prints
# the sed command that shows that line. The calling scan matches ids with
# the same pattern, so a refused id is never joined onto TASKS_ROOT and
# never becomes a path.
#
# CONVERT (docs/blocking-site-census.md): a malformed queue line is a defect
# in that one line, not grounds to stop the wake — it is narrated and
# skipped, every well-formed line is still served, and the operator fixes
# the line the remedy names.
#
# Prints nothing to stdout. Returns 0 always: a refusal ends the line, not
# the scan.
# ---------------------------------------------------------------------------
_narrate_malformed_queue_task_ids() {
  local queue_file="$1"
  local marker_class="$2"
  local scan_name="$3"
  local malformed
  malformed="$(_malformed_queue_entries "$queue_file" "$marker_class" "$(kanban_task_id_pattern)")"
  [[ -z "$malformed" ]] && return 0
  local pattern number line
  pattern="$(kanban_task_id_pattern)"
  while IFS=$'\t' read -r number line; do
    [[ -z "$number" ]] && continue
    # CONVERT (docs/blocking-site-census.md): narrate the line and skip it; the wake continues.
    pgai_emit_error_contract \
      "wake.queue.malformed-task-id" \
      "wake_task_id_gate: ${scan_name}: ${queue_file} line ${number} ('${line}') names a task id outside the canonical shape ${pattern}; the line is skipped and its id is never used as a path. A task line reads '- [ ] AGENT-YYYYMMDD-NNN-slug'." \
      "sed -n '${number}p' -- '${queue_file}'"
    # CENSUS-ANCHOR: wake-task-id-gate-malformed-queue-id-01
  done <<< "$malformed"
  return 0
}

# ---------------------------------------------------------------------------
# _narrate_malformed_prerequisite_ids <readme> <check_output>
#
# For every `<token>:MALFORMED` entry check_prerequisites printed, refuse the
# token dual-channel naming the README it came from. A malformed prerequisite
# token is one outside the canonical task-id shape (kanban_task_id_pattern);
# check_prerequisites reports it as unsatisfied rather than joining it onto
# TASKS_ROOT, so the task waits, narrated, until the line is corrected.
#
# CONVERT (docs/blocking-site-census.md): the wake continues — only the one
# task whose README carries the token is held, in WAITING, and the operator
# fixes the token the remedy shows.
#
# Prints nothing to stdout. Returns 0 always.
# ---------------------------------------------------------------------------
_narrate_malformed_prerequisite_ids() {
  local readme="$1"
  local check_output="$2"
  [[ -z "$check_output" ]] && return 0
  local pattern entry token
  pattern="$(kanban_task_id_pattern)"
  while IFS= read -r entry; do
    [[ "$entry" == *:MALFORMED ]] || continue
    token="${entry%:MALFORMED}"
    # CONVERT (docs/blocking-site-census.md): the token is held as unsatisfied and narrated; the wake continues.
    pgai_emit_error_contract \
      "wake.prerequisite.malformed-task-id" \
      "wake_task_id_gate: prerequisite parse: ${readme} ## Prerequisites names '${token}', which is outside the canonical task-id shape ${pattern}; it is treated as unsatisfied and never used as a path. A prerequisite line reads '- AGENT-YYYYMMDD-NNN-slug'." \
      "grep -n -A 3 -- '^## Prerequisites' '${readme}'"
    # CENSUS-ANCHOR: wake-task-id-gate-malformed-prerequisite-id-01
  done <<< "$check_output"
  return 0
}

# ---------------------------------------------------------------------------
# _drop_archived_prerequisites <check_output>
#
# Print <check_output> (check_prerequisites' unsatisfied lines) without every
# `<id>:MISSING` line whose task has been archived. The lookup is
# `pgai_agent_kanban.archive archived_tasks` through pp_run_ops, given
# TASKS_ROOT and every MISSING id in one call; it answers only for ids with
# no live folder under TASKS_ROOT, and consults only the archive of the
# kanban root TASKS_ROOT sits in, so a sandboxed TASKS_ROOT never reads
# another install's archive.
#
# Fails closed: with no MISSING line, no TASKS_ROOT, or a lookup that exits
# non-zero (narrated on stderr), the output is printed unchanged and every
# MISSING prerequisite stays unsatisfied. Every other line (a live state,
# UNPARSEABLE, MALFORMED) is printed unchanged.
#
# Returns 0 always.
# ---------------------------------------------------------------------------
_drop_archived_prerequisites() {
  local check_output="$1"
  local -a missing_ids=()
  local entry
  while IFS= read -r entry; do
    [[ "$entry" == *:MISSING ]] && missing_ids+=("${entry%:MISSING}")
  done <<< "$check_output"
  if [[ ${#missing_ids[@]} -eq 0 || -z "${TASKS_ROOT:-}" ]]; then
    [[ -n "$check_output" ]] && printf '%s\n' "$check_output"
    return 0
  fi

  local archived lookup_exit=0
  archived="$(pp_run_ops pgai_agent_kanban.archive archived_tasks \
    --tasks-root "$TASKS_ROOT" "${missing_ids[@]}")" || lookup_exit=$?
  if [[ $lookup_exit -ne 0 ]]; then
    echo "wake_task_id_gate: archive lookup for missing prerequisites under ${TASKS_ROOT} exited ${lookup_exit}; they stay unsatisfied" >&2
    [[ -n "$check_output" ]] && printf '%s\n' "$check_output"
    return 0
  fi

  local -A archived_set=()
  local archived_id
  while IFS= read -r archived_id; do
    [[ -n "$archived_id" ]] && archived_set["$archived_id"]=1
  done <<< "$archived"
  while IFS= read -r entry; do
    [[ -z "$entry" ]] && continue
    if [[ "$entry" == *:MISSING && -n "${archived_set["${entry%:MISSING}"]:-}" ]]; then
      continue
    fi
    printf '%s\n' "$entry"
  done <<< "$check_output"
  return 0
}

# ---------------------------------------------------------------------------
# _prerequisite_section_body <file> <heading>
#
# read_md_field over <file>'s ## <heading> (the next "##" line, "###"
# included, ends the body), run in a subshell that loads this tree's own
# pp_run_ops first: a caller's pp_run_ops is aimed at the archive lookup
# and never answers for a README or status.md. Prints and exits as
# read_md_field: 0 present (empty line for an empty body), 3 absent, 1
# unreadable.
# ---------------------------------------------------------------------------
_prerequisite_section_body() (
  # shellcheck source=pp_run_ops.sh
  source "${_WAKE_TASK_ID_GATE_LIB_DIR}/pp_run_ops.sh"
  read_md_field --end-at-subheading "$1" "$2"
)

# ---------------------------------------------------------------------------
# _unsatisfied_prerequisites <readme>
#
# Prints one `<id>:<STATE|MISSING|UNPARSEABLE|MALFORMED>` line per
# unsatisfied entry of <readme>'s ## Prerequisites and returns 1; returns 0
# printing nothing when every entry is DONE or WONT-DO, or there are none.
# The body has its <!-- --> comments removed; each line drops one leading
# "-", and holds comma-separated ids; an empty token or "none" (any case)
# is skipped. Every MALFORMED line comes first, then the rest in order. A
# README that cannot be read returns 1 printing nothing.
# ---------------------------------------------------------------------------
_unsatisfied_prerequisites() {
  local readme="$1"
  local body rc=0
  body="$(_prerequisite_section_body "$readme" Prerequisites)" || rc=$?
  [[ $rc -eq 3 ]] && return 0
  [[ $rc -eq 0 ]] || return 1

  local head rest
  while [[ "$body" == *"<!--"*"-->"* ]]; do
    head="${body%%<!--*}"
    rest="${body#*<!--}"
    [[ "$rest" == *"-->"* ]] || break
    body="${head}${rest#*-->}"
  done

  local pattern line token state
  local -a tokens=() prereqs=() unsatisfied=()
  pattern="$(kanban_task_id_pattern)"
  while IFS= read -r line; do
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    if [[ "$line" == -* ]]; then
      line="${line:1}"
      line="${line#"${line%%[![:space:]]*}"}"
    fi
    [[ -z "$line" || "${line,,}" == "none" ]] && continue
    IFS=',' read -ra tokens <<< "$line"
    for token in "${tokens[@]}"; do
      token="${token#"${token%%[![:space:]]*}"}"
      token="${token%"${token##*[![:space:]]}"}"
      [[ -z "$token" || "${token,,}" == "none" ]] && continue
      if [[ "$token" =~ $pattern ]]; then
        prereqs+=("$token")
      else
        unsatisfied+=("${token}:MALFORMED")
      fi
    done
  done <<< "$body"

  local prereq_id status_file
  for prereq_id in "${prereqs[@]}"; do
    status_file="${TASKS_ROOT}/${prereq_id}/status.md"
    if [[ ! -f "$status_file" ]]; then
      unsatisfied+=("${prereq_id}:MISSING")
      continue
    fi
    rc=0
    state="$(_prerequisite_section_body "$status_file" State)" || rc=$?
    if [[ $rc -ne 0 ]]; then
      unsatisfied+=("${prereq_id}:UNPARSEABLE")
      continue
    fi
    state="${state^^}"
    [[ "$state" == "DONE" || "$state" == "WONT-DO" ]] || unsatisfied+=("${prereq_id}:${state}")
  done

  [[ ${#unsatisfied[@]} -eq 0 ]] && return 0
  printf '%s\n' "${unsatisfied[@]}"
  return 1
}

check_prerequisites() {
  local readme="$1"
  local check_output
  local check_exit=0
  check_output="$(_unsatisfied_prerequisites "$readme")" || check_exit=$?
  # A prerequisite with no live folder may have been archived; an archived
  # task is finished, so it no longer holds this task. The check is
  # satisfied only when every unsatisfied line was an archived MISSING one
  # (an empty output from a failed parse is left as it was).
  if [[ $check_exit -eq 1 && "$check_output" == *:MISSING* ]]; then
    check_output="$(_drop_archived_prerequisites "$check_output")"
    [[ -z "$check_output" ]] && check_exit=0
  fi
  _narrate_malformed_prerequisite_ids "$readme" "$check_output"
  [[ -n "$check_output" ]] && printf '%s\n' "$check_output"
  return "$check_exit"
}
