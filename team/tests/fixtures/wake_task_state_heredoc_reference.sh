#!/usr/bin/env bash
# team/tests/fixtures/wake_task_state_heredoc_reference.sh
#
# Heredoc-backed reference implementation of the status.md and queue-file
# readers and writers in team/scripts/lib/wake_task_state.sh: the same
# function names and contracts, each doing its parsing in its own inline
# Python heredoc instead of a pgai_agent_kanban module. The bodies are kept
# verbatim so tests/unit/test_wake_task_state_module_parity.py can run them
# next to the module-backed functions on the same fixtures and assert the
# same stdout, stderr, exit code and file bytes.
#
# Test-only: nothing outside the test tree sources this file. Load it the
# way the parity test does — source the real wake_task_state.sh first (for
# _status_section_re, atomic_write.sh and the task-id gate), then this file,
# whose definitions replace the module-backed functions of the same names.
# Every function here is defined at top level with no side effects.

_read_status_section() {
  python3 - "$1" "$(_status_section_re "$2")" <<'PY'
import pathlib, re, sys
m = re.search(sys.argv[2], pathlib.Path(sys.argv[1]).read_text(), flags=re.M)
if not m:
    raise SystemExit(1)
print(m.group(2).strip())
PY
}

get_first_pending_task() {
  _narrate_malformed_queue_task_ids "$BACKLOG" ' B' "pending-task scan"
  python3 - "$BACKLOG" "$TASKS_ROOT" "$(kanban_task_id_pattern)" "$(_status_section_re '## State')" <<'PY'
import re, sys, pathlib
backlog = pathlib.Path(sys.argv[1])
tasks_root = pathlib.Path(sys.argv[2])
canonical = re.compile(sys.argv[3])
state_re = re.compile(sys.argv[4], re.M)
text = backlog.read_text()
for line in text.splitlines():
    m = re.match(r'^\s*-?\s*\[\s*[ B]\s*\]\s+(\S+)(\s+.*)?$', line)
    if not m or not canonical.match(m.group(1)):
        continue
    task_id = m.group(1)
    status_file = tasks_root / task_id / "status.md"
    if not status_file.is_file():
        print(task_id)
        raise SystemExit(0)
    status_text = status_file.read_text()
    state_match = state_re.search(status_text)
    state = state_match.group(2).strip().upper() if state_match else ""
    if state in ("", "BACKLOG"):
        print(task_id)
        raise SystemExit(0)
raise SystemExit(1)
PY
}

get_state() {
  local state
  state="$(_read_status_section "$1" '## State')" \
    || { echo "Could not parse ## State block in $1" >&2; return 1; }
  printf '%s\n' "${state^^}"
}

normalize_status_file() {
  local status_file="$1"
  pgai_atomic_write "$status_file" python3 - "$status_file" <<'PY'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
text = path.read_text()
text = re.sub(r'\n\n\n+', '\n\n', text)
sys.stdout.write(text)
PY
}

set_state() {
  local status_file="$1"
  local new_state="$2"
  pgai_atomic_write "$status_file" python3 - "$status_file" "$new_state" <<'PY'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
new_state = sys.argv[2]
text = path.read_text()
# Section pattern: the heading line, then body lines none of which starts with
# "##", so an empty body can never run into the next section. The separator
# after the body is re-emitted exactly as found: the "\n+##" run before the
# next heading, or at end of file the trailing newlines (one when none).
def _section_tail(m):
    return m.group(3) or "\n"
text_new, n = re.subn(
    r'(^## State[ \t]*)((?:\n(?!##)[^\n]*)*?)(\n+##|\n*\Z)',
    lambda m: m.group(1) + "\n" + new_state.strip() + _section_tail(m),
    text,
    flags=re.M,
)
if n == 0:
    raise SystemExit("Could not update ## State block")
sys.stdout.write(text_new)
PY
}

set_block() {
  local status_file="$1"
  local heading="$2"
  local body="$3"
  pgai_atomic_write "$status_file" python3 - "$status_file" "$heading" "$body" <<'PY'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
heading = sys.argv[2]
body = sys.argv[3]
text = path.read_text()
# Section pattern: the heading line, then body lines none of which starts with
# "##", so an empty body can never run into the next section. The separator
# after the body is re-emitted exactly as found: the "\n+##" run before the
# next heading, or at end of file the trailing newlines (one when none).
def _section_tail(m):
    return m.group(3) or "\n"
pattern = rf'(^## {re.escape(heading)}[ \t]*)((?:\n(?!##)[^\n]*)*?)(\n+##|\n*\Z)'
text_new, n = re.subn(
    pattern,
    lambda m: m.group(1) + "\n" + body.strip() + _section_tail(m),
    text,
    flags=re.M,
)
if n == 0:
    raise SystemExit(f"Could not update ## {heading} block")
sys.stdout.write(text_new)
PY
}

stamp_model_field() {
  local status_file="$1"
  local model_value="$2"
  pgai_atomic_write "$status_file" python3 - "$status_file" "$model_value" <<'PY'
import pathlib, re, sys

path = pathlib.Path(sys.argv[1])
model_value = sys.argv[2].strip()

try:
    text = path.read_text()
except OSError as e:
    raise SystemExit(f"stamp_model_field: cannot read {path}: {e}")

# If ## Model section already exists, replace its body (section-scoped update).
if re.search(r'^## Model\s*$', text, flags=re.M):
    text_new, n = re.subn(
        r'(^## Model[ \t]*)((?:\n(?!##)[^\n]*)*?)(\n+##|\n*\Z)',
        lambda m: m.group(1) + "\n" + model_value + (m.group(3) or "\n"),
        text,
        flags=re.M,
    )
    if n == 0:
        raise SystemExit("stamp_model_field: found ## Model header but subn matched 0 times")
    sys.stdout.write(text_new)
    raise SystemExit(0)

# Section absent — insert after ## Role, or ## Participant, or at end.
# The replacement captures (anchor-header + body)(newline(s) + next-##) and
# injects the new ## Model section between them, normalising spacing so the
# output reads as two blank-line-separated sections.
new_section = f"## Model\n{model_value}\n"
for anchor in (r'^(## Role\s*\n.*?)(\n+##)', r'^(## Participant\s*\n.*?)(\n+##)'):
    text_new, n = re.subn(
        anchor,
        lambda m: m.group(1) + "\n\n" + new_section + "\n" + m.group(2).lstrip('\n'),
        text,
        count=1,
        flags=re.S | re.M,
    )
    if n > 0:
        # Collapse any run of 3+ newlines introduced by the splice.
        text_new = re.sub(r'\n{3,}', '\n\n', text_new)
        sys.stdout.write(text_new)
        raise SystemExit(0)

# Fallback: append at end.
sys.stdout.write(text.rstrip('\n') + "\n\n" + new_section)
PY
  local _stamp_exit=$?
  if [[ $_stamp_exit -ne 0 ]]; then
    echo "WARNING: stamp_model_field failed (exit ${_stamp_exit}) for ${status_file}; continuing" >&2
  fi
}

mark_backlog() {
  local task_id="$1"
  local marker="$2"
  pgai_atomic_write "$BACKLOG" python3 - "$BACKLOG" "$task_id" "$marker" <<'PY'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
task_id = sys.argv[2]
marker = sys.argv[3]
text = path.read_text()
pattern = rf'^(\s*-\s*)\[[xRBWA\s]*\](\s+{re.escape(task_id)})(\s.*)?$'
text_new, n = re.subn(pattern, lambda m: m.group(1) + f'[{marker}]' + m.group(2) + (m.group(3) if m.group(3) else ''), text, flags=re.M)
if n == 0:
    raise SystemExit(f"Could not update backlog entry for {task_id}")
sys.stdout.write(text_new)
PY
}

promote_waiting_to_backlog() {
  local task_id="$1"
  local task_status="$2"
  if ! pgai_atomic_write "$task_status" python3 - "$BACKLOG" "$task_id" "$task_status" <<'PY'
import pathlib, re, sys

backlog_path = pathlib.Path(sys.argv[1])
task_id = sys.argv[2]
status_path = pathlib.Path(sys.argv[3])

try:
    queue_text = backlog_path.read_text()
except OSError as e:
    raise SystemExit(f"Cannot read queue file: {e}")

try:
    status_text = status_path.read_text()
except OSError as e:
    raise SystemExit(f"Cannot read status file: {e}")

queue_pattern = rf'^(\s*-\s*)\[\s*[W\s]\s*\](\s+{re.escape(task_id)})(\s.*)?$'
if not re.search(queue_pattern, queue_text, flags=re.M):
    raise SystemExit(f"Could not find [W] or [ ] queue entry for {task_id}")

new_summary = "Promoted from WAITING to BACKLOG by wake script: prerequisites satisfied."

def replace_block(text, heading, new_body):
    pattern = rf'(^## {re.escape(heading)}[ \t]*)((?:\n(?!##)[^\n]*)*?)(\n+##|\n*\Z)'
    text_new, n = re.subn(
        pattern,
        lambda m: m.group(1) + "\n" + new_body.strip() + (m.group(3) or "\n"),
        text,
        flags=re.M,
    )
    if n == 0:
        raise SystemExit(f"Could not update ## {heading} block in status.md")
    return text_new

try:
    status_new = replace_block(status_text, "State", "BACKLOG")
    status_new = replace_block(status_new, "Blockers", "none")
    status_new = replace_block(status_new, "Summary", new_summary)
except SystemExit:
    raise

status_new = re.sub(r'\n\n\n+', '\n\n', status_new)

sys.stdout.write(status_new)
PY
  then
    return 1
  fi
  _promote_mark_pending "$task_id" "$task_status"
}

promote_blocked_to_backlog() {
  local task_id="$1"
  local task_status="$2"
  local cleared_reason="$3"
  if ! pgai_atomic_write "$task_status" python3 - "$BACKLOG" "$task_id" "$task_status" "$cleared_reason" <<'PY'
import pathlib, re, sys

backlog_path = pathlib.Path(sys.argv[1])
task_id = sys.argv[2]
status_path = pathlib.Path(sys.argv[3])
cleared_reason = sys.argv[4]

try:
    queue_text = backlog_path.read_text()
except OSError as e:
    raise SystemExit(f"Cannot read queue file: {e}")

try:
    status_text = status_path.read_text()
except OSError as e:
    raise SystemExit(f"Cannot read status file: {e}")

queue_pattern = rf'^(\s*-\s*)\[\s*B\s*\](\s+{re.escape(task_id)})(\s.*)?$'
if not re.search(queue_pattern, queue_text, flags=re.M):
    raise SystemExit(f"Could not find [B] queue entry for {task_id}")

new_summary = f"Promoted from BLOCKED to BACKLOG by wake script: blocker cleared ({cleared_reason})."

def replace_block(text, heading, new_body):
    pattern = rf'(^## {re.escape(heading)}[ \t]*)((?:\n(?!##)[^\n]*)*?)(\n+##|\n*\Z)'
    text_new, n = re.subn(
        pattern,
        lambda m: m.group(1) + "\n" + new_body.strip() + (m.group(3) or "\n"),
        text,
        flags=re.M,
    )
    if n == 0:
        return text
    return text_new

try:
    status_new = replace_block(status_text, "State", "BACKLOG")
    status_new = replace_block(status_new, "Blockers", "none")
    status_new = replace_block(status_new, "Blocked By Agent", "none")
    status_new = replace_block(status_new, "Blocked Reason", "none")
    status_new = replace_block(status_new, "Needs Human", "no")
    status_new = replace_block(status_new, "Summary", new_summary)
except SystemExit:
    raise

status_new = re.sub(r'\n\n\n+', '\n\n', status_new)

sys.stdout.write(status_new)
PY
  then
    return 1
  fi
  _promote_mark_pending "$task_id" "$task_status"
}

recheck_waiting_tasks() {
  log "re-checking WAITING tasks for satisfied prerequisites..."

  _narrate_malformed_queue_task_ids "$BACKLOG" 'W ' "WAITING scan"
  local waiting_ids
  waiting_ids=$(python3 - "$BACKLOG" "$TASKS_ROOT" "$(kanban_task_id_pattern)" \
    "$(_status_section_re '##[^\S\n]+State')" <<'PY'
import re, sys, pathlib

backlog_path = pathlib.Path(sys.argv[1])
tasks_root = pathlib.Path(sys.argv[2])
canonical = re.compile(sys.argv[3])
state_re = re.compile(sys.argv[4], re.M)
text = backlog_path.read_text()

for line in text.splitlines():
    m = re.match(r'^\s*-?\s*\[\s*W\s*\]\s+(\S+)(\s+.*)?$', line)
    if m and canonical.match(m.group(1)):
        print(m.group(1))
        continue
    m = re.match(r'^\s*-?\s*\[\s*\]\s+(\S+)(\s+.*)?$', line)
    if m and canonical.match(m.group(1)):
        task_id = m.group(1)
        status_file = tasks_root / task_id / "status.md"
        if not status_file.is_file():
            continue
        status_text = status_file.read_text()
        state_match = state_re.search(status_text)
        if state_match and state_match.group(2).strip().upper() == "WAITING":
            print(task_id)
PY
)

  if [[ -z "$waiting_ids" ]]; then
    log "no WAITING tasks to re-check"
    return 0
  fi

  local promoted=0
  while IFS= read -r task_id; do
    [[ -z "$task_id" ]] && continue
    local task_dir="${TASKS_ROOT}/${task_id}"
    local task_readme="${task_dir}/README.md"
    local task_status="${task_dir}/status.md"

    if [[ ! -f "$task_readme" ]] || [[ ! -f "$task_status" ]]; then
      log "WAITING task ${task_id} missing files; skipping re-check"
      continue
    fi

    local check_exit=0
    set +e
    check_prerequisites "$task_readme" >/dev/null
    check_exit=$?
    set -e

    if [[ $check_exit -eq 0 ]]; then
      log "WAITING task ${task_id}: prerequisites satisfied, promoting to BACKLOG"
      local promote_exit=0
      set +e
      promote_waiting_to_backlog "$task_id" "$task_status"
      promote_exit=$?
      set -e
      if [[ $promote_exit -ne 0 ]]; then
        log "WARNING: atomic promotion failed for ${task_id}; skipping (no partial update)"
        continue
      fi
      promoted=$((promoted + 1))
    fi
  done <<< "$waiting_ids"

  log "re-check complete: promoted ${promoted} task(s) from WAITING to BACKLOG"
}

recheck_blocked_tasks() {
  log "re-checking BLOCKED tasks for cleared blockers..."

  _narrate_malformed_queue_task_ids "$BACKLOG" 'B' "BLOCKED scan"
  local blocked_ids
  blocked_ids=$(python3 - "$BACKLOG" "$TASKS_ROOT" "$(kanban_task_id_pattern)" \
    "$(_status_section_re '##[^\S\n]+State')" <<'PY'
import re, sys, pathlib

backlog_path = pathlib.Path(sys.argv[1])
tasks_root = pathlib.Path(sys.argv[2])
canonical = re.compile(sys.argv[3])
state_re = re.compile(sys.argv[4], re.M)
text = backlog_path.read_text()

for line in text.splitlines():
    m = re.match(r'^\s*-?\s*\[\s*B\s*\]\s+(\S+)(\s+.*)?$', line)
    if m and canonical.match(m.group(1)):
        task_id = m.group(1)
        status_file = tasks_root / task_id / "status.md"
        if not status_file.is_file():
            continue
        status_text = status_file.read_text()
        state_match = state_re.search(status_text)
        if state_match and state_match.group(2).strip().upper() == "BLOCKED":
            print(task_id)
PY
)

  if [[ -z "$blocked_ids" ]]; then
    log "no BLOCKED tasks to re-check"
    return 0
  fi

  local promoted=0
  while IFS= read -r task_id; do
    [[ -z "$task_id" ]] && continue
    local task_dir="${TASKS_ROOT}/${task_id}"
    local task_readme="${task_dir}/README.md"
    local task_status="${task_dir}/status.md"

    if [[ ! -f "$task_readme" ]] || [[ ! -f "$task_status" ]]; then
      log "BLOCKED task ${task_id} missing files; skipping re-check"
      continue
    fi

    local needs_human
    # An absent or empty ## Needs Human reads as "yes": never auto-resolve a
    # task whose human flag is unrecorded.
    needs_human="$(_read_status_section "$task_status" '##[^\S\n]+Needs Human')" || needs_human=""
    needs_human="${needs_human,,}"
    [[ -n "$needs_human" ]] || needs_human="yes"
    if [[ "$needs_human" == "yes" ]]; then
      log "BLOCKED task ${task_id}: Needs Human=yes, skipping auto-resolve"
      continue
    fi

    local blocked_reason
    blocked_reason="$(_read_status_section "$task_status" '##[^\S\n]+Blocked Reason')" || blocked_reason=""

    local cleared=false
    local cleared_desc=""

    if echo "$blocked_reason" | grep -qi "active rc"; then
      local check_exit=0
      set +e
      _blocked_check_active_rc "$task_id" "$blocked_reason"
      check_exit=$?
      set -e
      if [[ $check_exit -eq 0 ]]; then
        cleared=true
        cleared_desc="Active RC=none"
      fi
    fi

    # --- Transient API error auto-retry ---
    # A task whose durable ## Transient Retry record has reason_code
    # transient_api_error and Needs Human=no (confirmed above) is eligible
    # for auto-promotion to BACKLOG. Reads the SAME durable record
    # handle_working_after_exit writes — never ## Blocked Reason prose
    # (reset to "none" on every promotion, so untrustworthy as a count
    # source). The record's own retry_limit is preferred (judges a
    # promoted-then-reclassified task against the ceiling in effect when
    # recorded); transient_retry_limit() falls back for older/malformed
    # records. The "< ceiling" comparison mirrors
    # handle_working_after_exit's exactly, so the two paths can never
    # disagree; this branch is a safety double-check for TRANSIENT_RETRY_CAP
    # having been lowered between the record's write and this recheck.
    if [[ "$cleared" == "false" ]]; then
      local _retry_reason_code
      _retry_reason_code="$(transient_retry_field "$task_status" "reason_code" "")"
      if [[ "$_retry_reason_code" == "transient_api_error" ]]; then
        local _transient_cap
        _transient_cap="$(transient_retry_field "$task_status" "retry_limit" "")"
        [[ "$_transient_cap" =~ ^[0-9]+$ ]] || _transient_cap="$(transient_retry_limit)"
        local _retry_count
        _retry_count="$(transient_retry_field "$task_status" "retry_count" "0")"
        [[ "$_retry_count" =~ ^[0-9]+$ ]] || _retry_count=0

        if [[ "$_retry_count" -lt "$_transient_cap" ]]; then
          cleared=true
          cleared_desc="transient API error auto-retry (attempt ${_retry_count}/${_transient_cap})"
          log "BLOCKED task ${task_id}: transient API error — scheduling auto-retry (attempt ${_retry_count}/${_transient_cap})"
        else
          log "BLOCKED task ${task_id}: transient API error — retry cap reached (${_retry_count}/${_transient_cap}); leaving for human"
        fi
      fi
    fi

    if [[ "$cleared" == "true" ]]; then
      log "BLOCKED task ${task_id}: blocker cleared (${cleared_desc}), promoting to BACKLOG"
      local promote_exit=0
      set +e
      promote_blocked_to_backlog "$task_id" "$task_status" "$cleared_desc"
      promote_exit=$?
      set -e
      if [[ $promote_exit -ne 0 ]]; then
        log "WARNING: atomic promotion failed for ${task_id}; skipping (no partial update)"
        continue
      fi
      promoted=$((promoted + 1))
    fi
  done <<< "$blocked_ids"

  log "re-check complete: promoted ${promoted} BLOCKED task(s) to BACKLOG"
}
