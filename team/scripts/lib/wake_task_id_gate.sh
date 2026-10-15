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
# python3 heredocs read identically. An id outside it ('..', '../x', 'a.b',
# anything with a separator or a dot) is refused dual-channel here, naming
# the file and line, and the caller skips it: a refused id never becomes a
# path. Both refusals are CONVERT (docs/blocking-site-census.md): a
# malformed line is a defect in that one line, not grounds to stop the
# wake, so every well-formed entry is still served.
#
# Functions
# ---------
#   _narrate_malformed_queue_task_ids <queue_file> <marker_class> <scan_name>
#   _narrate_malformed_prerequisite_ids <readme> <check_output>
#   _drop_archived_prerequisites <check_output>
#   check_prerequisites <readme>
#       Prints one `<id>:<STATE|MISSING|UNPARSEABLE|MALFORMED>` line per
#       unsatisfied prerequisite and exits 1; exits 0 with no output when
#       every prerequisite is DONE or WONT-DO (or there are none). Called
#       by wake_driver_prep.sh at dispatch and by recheck_waiting_tasks.
#       A section body stops at the next "##" line, so an empty
#       ## Prerequisites means none and an empty prerequisite ## State
#       prints as `<id>:` (unsatisfied), never as the next section's text.
#       A prerequisite is read from its live TASKS_ROOT/<id>/status.md
#       first; only one with no live folder at all is looked up in the
#       project's archive (<kanban_root>/archive/<project>/tasks/<id>/,
#       derived from TASKS_ROOT), and an archived one counts as DONE —
#       only finished tasks are ever archived. One that is neither live
#       nor archived still prints as `<id>:MISSING`.
#
# Dependencies: TASKS_ROOT (wake_bootstrap.sh), task_ids.sh
# (kanban_task_id_pattern), error_contract.sh (pgai_emit_error_contract)
# and pp_run_ops.sh (pp_run_ops, which reaches the archive lookup as the
# archive module's `archived_tasks` verb) — all three function-only and
# self-sourced below; python3 on PATH. pp_run_ops.sh is loaded only when
# no pp_run_ops is defined yet, so a caller's own definition (a wrapper,
# or a test stub) stays the active one.

_WAKE_TASK_ID_GATE_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=task_ids.sh
source "${_WAKE_TASK_ID_GATE_LIB_DIR}/task_ids.sh"
# shellcheck source=error_contract.sh
source "${_WAKE_TASK_ID_GATE_LIB_DIR}/error_contract.sh"
# A caller's own pp_run_ops (wake_bootstrap.sh's, or a test stub) wins.
# shellcheck source=pp_run_ops.sh
declare -F pp_run_ops >/dev/null || source "${_WAKE_TASK_ID_GATE_LIB_DIR}/pp_run_ops.sh"

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
  malformed="$(python3 - "$queue_file" "$marker_class" "$(kanban_task_id_pattern)" <<'PY'
import pathlib, re, sys
queue_path = pathlib.Path(sys.argv[1])
marker_class = sys.argv[2]
canonical = re.compile(sys.argv[3])
# The BACKLOG marker is a space, so only a class that names it also admits
# the empty '[]' alias; any other class must see its marker character.
if " " in marker_class:
    entry = re.compile(r'^\s*-?\s*\[\s*(?:[' + marker_class + r'])?\s*\]\s+(\S+)(\s+.*)?$')
else:
    entry = re.compile(r'^\s*-?\s*\[\s*[' + marker_class + r']\s*\]\s+(\S+)(\s+.*)?$')
try:
    lines = queue_path.read_text().splitlines()
except OSError:
    raise SystemExit(0)
for number, line in enumerate(lines, start=1):
    m = entry.match(line)
    if m and not canonical.match(m.group(1)):
        print(f"{number}\t{line.strip()}")
PY
)"
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

check_prerequisites() {
  local readme="$1"
  local check_output
  local check_exit=0
  check_output="$(python3 - "$readme" "$TASKS_ROOT" "$(kanban_task_id_pattern)" <<'PY'
import pathlib, re, sys
readme = pathlib.Path(sys.argv[1])
tasks_root = pathlib.Path(sys.argv[2])
canonical = re.compile(sys.argv[3])

text = readme.read_text()
m = re.search(r'(^## Prerequisites[^\S\n]*)((?:\n(?!##)[^\n]*)*?)(\n+##|\n*\Z)', text, flags=re.M)
if not m:
    raise SystemExit(0)

body = m.group(2).strip()
body = re.sub(r'<!--.*?-->', '', body, flags=re.S).strip()
if not body or body.lower() == "none":
    raise SystemExit(0)

prereqs = []
malformed = []
for line in body.splitlines():
    line = line.strip()
    if line.startswith("-"):
        line = line[1:].strip()
    if not line or line.lower() == "none":
        continue
    for token in line.split(","):
        token = token.strip()
        if not token or token.lower() == "none":
            continue
        if canonical.match(token):
            prereqs.append(token)
        else:
            malformed.append(token)

if not prereqs and not malformed:
    raise SystemExit(0)

SATISFIED = {"DONE", "WONT-DO"}

unsatisfied = [f"{token}:MALFORMED" for token in malformed]
for prereq_id in prereqs:
    status_file = tasks_root / prereq_id / "status.md"
    if not status_file.is_file():
        unsatisfied.append(f"{prereq_id}:MISSING")
        continue
    status_text = status_file.read_text()
    sm = re.search(r'(^## State[^\S\n]*)((?:\n(?!##)[^\n]*)*?)(\n+##|\n*\Z)', status_text, flags=re.M)
    if not sm:
        unsatisfied.append(f"{prereq_id}:UNPARSEABLE")
        continue
    state = sm.group(2).strip().upper()
    if state not in SATISFIED:
        unsatisfied.append(f"{prereq_id}:{state}")

if unsatisfied:
    for u in unsatisfied:
        print(u)
    raise SystemExit(1)
raise SystemExit(0)
PY
)" || check_exit=$?
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
