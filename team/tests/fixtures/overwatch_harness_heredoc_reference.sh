#!/usr/bin/env bash
# team/tests/fixtures/overwatch_harness_heredoc_reference.sh
#
# Reference implementation for test_overwatch_harness_module_parity.py: the
# heredoc-backed bodies of the durable-record readers and writers
# (transient_retry_field / transient_retry_write, zero_progress_field /
# zero_progress_record in team/scripts/lib/overwatch_protocol.sh), of
# OVERWATCH's transient requeue and escalate status writers (_ctae_do_requeue
# / _ctae_do_escalate in
# team/scripts/lib/overwatch-checks/check-transient-api-error.sh), and of the
# harness provider's ## State / ## Summary writes (team/scripts/wake/harness.sh,
# wrapped here in the two functions that file now defines), kept verbatim as
# they read before those call sites moved to pgai_agent_kanban.task_status.
# Sourced AFTER the real libs, so each definition here replaces its
# module-backed counterpart and the parity test can run both on the same
# fixtures.
#
# Test-only: nothing under team/scripts sources this file.

transient_retry_field() {
    local status_file="$1"
    local field_name="$2"
    local default_value="${3:-}"

    python3 - "${status_file}" "${field_name}" "${default_value}" <<'PY'
import pathlib, re, sys

status_file, field_name, default_value = sys.argv[1], sys.argv[2], sys.argv[3]
path = pathlib.Path(status_file)
text = path.read_text(encoding="utf-8") if path.is_file() else ""

value = default_value
m = re.search(r'(^## Transient Retry[^\S\n]*)((?:\n(?!##)[^\n]*)*?)(\n+##|\n*\Z)', text, flags=re.M)
if m:
    body = m.group(2).strip()
    if body and body.strip().lower() != "none":
        fm = re.search(rf'^{re.escape(field_name)}:\s*(.*)$', body, flags=re.M)
        if fm:
            value = fm.group(1).strip()

print(value)
PY
}

transient_retry_write() {
    local status_file="$1"
    local reason_code="$2"
    local retry_count="$3"
    local retry_limit="$4"
    local provider="$5"
    local last_error="$6"

    python3 - "${status_file}" "${reason_code}" "${retry_count}" "${retry_limit}" "${provider}" "${last_error}" <<'PY'
import pathlib, re, sys

status_file, reason_code, retry_count, retry_limit, provider, last_error = sys.argv[1:7]
path = pathlib.Path(status_file)

try:
    text = path.read_text(encoding="utf-8")
except OSError as e:
    raise SystemExit(f"transient_retry_write: cannot read {status_file}: {e}")

new_body = "\n".join([
    f"reason_code: {reason_code}",
    f"retry_count: {retry_count}",
    f"retry_limit: {retry_limit}",
    f"provider: {provider}",
    f"last_error: {last_error}",
])

# Section boundary: the heading line, then body lines none of which starts
# with "##" -- the boundary set_block (wake_task_state.sh) splices with -- so
# an empty body is filled in place and never runs into the next section. The
# separator after the body is re-emitted as found: the "\n+##" run before the
# next heading, or at end of file the trailing newlines (one when none).
pattern = r'(^## Transient Retry[^\S\n]*)((?:\n(?!##)[^\n]*)*?)(\n+##|\n*\Z)'
new_text, n = re.subn(
    pattern,
    lambda m: m.group(1) + "\n" + new_body + (m.group(3) or "\n"),
    text,
    count=1,
    flags=re.M,
)
if n == 0:
    if not text.endswith("\n"):
        text += "\n"
    new_text = text + f"\n## Transient Retry\n{new_body}\n"

try:
    path.write_text(new_text, encoding="utf-8")
except OSError as e:
    raise SystemExit(f"transient_retry_write: cannot write {status_file}: {e}")
PY
}

zero_progress_field() {
    local status_file="$1"
    local field_name="$2"
    local default_value="${3:-}"

    python3 - "${status_file}" "${field_name}" "${default_value}" <<'PY'
import pathlib, re, sys

status_file, field_name, default_value = sys.argv[1], sys.argv[2], sys.argv[3]
path = pathlib.Path(status_file)
text = path.read_text(encoding="utf-8") if path.is_file() else ""

value = default_value
m = re.search(r'(^## Zero Progress[^\S\n]*)((?:\n(?!##)[^\n]*)*?)(\n+##|\n*\Z)', text, flags=re.M)
if m:
    body = m.group(2).strip()
    if body and body.strip().lower() != "none":
        fm = re.search(rf'^{re.escape(field_name)}:\s*(.*)$', body, flags=re.M)
        if fm:
            value = fm.group(1).strip()

print(value)
PY
}

zero_progress_record() {
    local status_file="$1"
    local reason_code="$2"
    local count="$3"
    local limit="$4"
    local detail="$5"

    python3 - "${status_file}" "${reason_code}" "${count}" "${limit}" "${detail}" <<'PY'
import pathlib, re, sys

status_file, reason_code, count, limit, detail = sys.argv[1:6]
path = pathlib.Path(status_file)

try:
    text = path.read_text(encoding="utf-8")
except OSError as e:
    raise SystemExit(f"zero_progress_record: cannot read {status_file}: {e}")

new_body = "\n".join([
    f"reason_code: {reason_code}",
    f"count: {count}",
    f"limit: {limit}",
    f"detail: {detail}",
])

# Section boundary: the heading line, then body lines none of which starts
# with "##" -- the boundary set_block (wake_task_state.sh) splices with -- so
# an empty body is filled in place and never runs into the next section. The
# separator after the body is re-emitted as found: the "\n+##" run before the
# next heading, or at end of file the trailing newlines (one when none).
pattern = r'(^## Zero Progress[^\S\n]*)((?:\n(?!##)[^\n]*)*?)(\n+##|\n*\Z)'
new_text, n = re.subn(
    pattern,
    lambda m: m.group(1) + "\n" + new_body + (m.group(3) or "\n"),
    text,
    count=1,
    flags=re.M,
)
if n == 0:
    if not text.endswith("\n"):
        text += "\n"
    new_text = text + f"\n## Zero Progress\n{new_body}\n"

try:
    path.write_text(new_text, encoding="utf-8")
except OSError as e:
    raise SystemExit(f"zero_progress_record: cannot write {status_file}: {e}")
PY
}

_ctae_do_requeue() {
    local task_id="${_CTAE_TASK_ID}"
    local status_file="${_CTAE_STATUS_FILE}"
    local new_count="${_CTAE_NEW_COUNT}"
    local retry_limit
    retry_limit="$(transient_retry_limit)"

    # Backup status.md before any change
    local backup_path="none"
    local bpath
    bpath="$(overwatch_backup_file "${status_file}")" || {
        echo "check-transient-api-error: backup failed for ${status_file}" >&2
        return 1
    }
    backup_path="${bpath}"

    # Update the ONE durable retry record first (transient_retry_write is the
    # shared writer defined in overwatch_protocol.sh; see the module header).
    if ! transient_retry_write "${status_file}" "transient_api_error" "${new_count}" \
        "${retry_limit}" "overwatch" "transient signature matched in provider batch log"; then
        echo "check-transient-api-error: transient_retry_write failed for ${status_file}" >&2
        return 1
    fi

    # Use Python to atomically update the remaining status.md fields.
    local edit_exit=0
    python3 - "${status_file}" "${new_count}" <<'PY' || edit_exit=$?
import pathlib, re, sys

status_path = pathlib.Path(sys.argv[1])
new_count   = sys.argv[2]

try:
    text = status_path.read_text(encoding="utf-8")
except OSError as e:
    raise SystemExit(f"Cannot read status file: {e}")

def replace_section(text, heading, new_body):
    """Replace the body of a ## heading section; return (new_text, replaced_bool).

    Section boundary: the heading line, then body lines none of which starts
    with "##" -- the same boundary set_block in wake_task_state.sh splices
    with -- so an empty body is rewritten in place and never runs into the
    next section. The separator after the body is re-emitted as found: the
    "\n+##" run before the next heading, or at end of file the trailing
    newlines (one when none). No other section is touched.
    """
    pattern = rf'(^## {re.escape(heading)}[^\S\n]*)((?:\n(?!##)[^\n]*)*?)(\n+##|\n*\Z)'
    new_text, n = re.subn(
        pattern,
        lambda m: m.group(1) + "\n" + new_body.strip() + (m.group(3) or "\n"),
        text,
        flags=re.M,
    )
    return new_text, n > 0

def append_section(text, heading, body):
    """Append a new ## heading section at the end of the file."""
    if not text.endswith('\n'):
        text += '\n'
    text += f"\n## {heading}\n{body.strip()}\n"
    return text

# 1. Reset State to BACKLOG
text, _ = replace_section(text, "State", "BACKLOG")

# 2. Update or insert Labels with TRANSIENT
labels_raw, labels_found = "", False
label_match = re.search(r'(^## Labels[^\S\n]*)((?:\n(?!##)[^\n]*)*?)(\n+##|\n*\Z)', text, flags=re.M)
if label_match:
    labels_raw = label_match.group(2).strip()
    labels_found = True

if labels_found:
    if "TRANSIENT" not in labels_raw:
        new_labels = (labels_raw + " TRANSIENT").strip() if labels_raw else "TRANSIENT"
        text, _ = replace_section(text, "Labels", new_labels)
else:
    text = append_section(text, "Labels", "TRANSIENT")

# 3. Clear blockers / needs human
text, _ = replace_section(text, "Blockers", "none")
text, _ = replace_section(text, "Needs Human", "no")

# Update Summary
new_summary = (
    f"Auto-requeued by OVERWATCH check-transient-api-error: "
    f"transient API error detected in provider batch log. Requeue #{new_count}."
)
text, _ = replace_section(text, "Summary", new_summary)

# Normalize: collapse 3+ blank lines to 2
text = re.sub(r'\n\n\n+', '\n\n', text)

try:
    status_path.write_text(text, encoding="utf-8")
except OSError as e:
    raise SystemExit(f"Failed to write status file: {e}")
PY

    if (( edit_exit != 0 )); then
        echo "check-transient-api-error: status update failed for ${task_id}" >&2
        return 1
    fi

    # Log the action
    overwatch_log_action \
        "check-transient-api-error" \
        "${task_id}" \
        "transient-auto-requeued" \
        "${backup_path}" \
        "Transient API error in provider batch log; reset to BACKLOG (requeue #${new_count} of ${retry_limit})" \
    || true

    return 0
}

_ctae_do_escalate() {
    local task_id="${_CTAE_TASK_ID}"
    local status_file="${_CTAE_STATUS_FILE}"
    local current_count="${_CTAE_CURRENT_COUNT}"
    local cap="${_CTAE_CAP}"

    local backup_path="none"
    local bpath
    bpath="$(overwatch_backup_file "${status_file}")" || {
        echo "check-transient-api-error: backup failed for ${status_file}" >&2
        return 1
    }
    backup_path="${bpath}"

    local edit_exit=0
    python3 - "${status_file}" "${current_count}" "${cap}" <<'PY' || edit_exit=$?
import pathlib, re, sys

status_path = pathlib.Path(sys.argv[1])
current_count, cap = sys.argv[2], sys.argv[3]

try:
    text = status_path.read_text(encoding="utf-8")
except OSError as e:
    raise SystemExit(f"Cannot read status file: {e}")

def replace_section(text, heading, new_body):
    """Replace the body of a ## heading section; return (new_text, replaced_bool).

    Section boundary: the heading line, then body lines none of which starts
    with "##" -- the same boundary set_block in wake_task_state.sh splices
    with -- so an empty body is rewritten in place and never runs into the
    next section. The separator after the body is re-emitted as found: the
    "\n+##" run before the next heading, or at end of file the trailing
    newlines (one when none). No other section is touched.
    """
    pattern = rf'(^## {re.escape(heading)}[^\S\n]*)((?:\n(?!##)[^\n]*)*?)(\n+##|\n*\Z)'
    new_text, n = re.subn(
        pattern,
        lambda m: m.group(1) + "\n" + new_body.strip() + (m.group(3) or "\n"),
        text,
        flags=re.M,
    )
    return new_text, n > 0

text, _ = replace_section(text, "Needs Human", "yes")
text, replaced = replace_section(
    text, "Blocked Reason",
    f"Transient API error retry ceiling reached ({current_count}/{cap}, see ## Transient Retry record); human investigation required.",
)
if not replaced:
    if not text.endswith("\n"):
        text += "\n"
    text += (
        "\n## Blocked Reason\n"
        f"Transient API error retry ceiling reached ({current_count}/{cap}, see ## Transient Retry record); human investigation required.\n"
    )

text = re.sub(r'\n\n\n+', '\n\n', text)

try:
    status_path.write_text(text, encoding="utf-8")
except OSError as e:
    raise SystemExit(f"Failed to write status file: {e}")
PY

    if (( edit_exit != 0 )); then
        echo "check-transient-api-error: needs-human escalation failed for ${task_id}" >&2
        return 1
    fi

    overwatch_log_action \
        "check-transient-api-error" \
        "${task_id}" \
        "transient-ceiling-needs-human-escalated" \
        "${backup_path}" \
        "Transient retry ceiling ${current_count}/${cap} reached; Needs Human set to yes" \
    || true

    return 0
}

_harness_write_state_done() {
  local _task_status="$1"
    python3 - "$_task_status" <<'PY'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
text = path.read_text()
# Section boundary: heading line, then body lines never starting with "##"
# (set_block's boundary in wake_task_state.sh), so an empty body is filled
# in place and the next section survives; the separator is re-emitted.
text_new, n = re.subn(
    r'(^## State[^\S\n]*)((?:\n(?!##)[^\n]*)*?)(\n+##|\n*\Z)',
    lambda m: m.group(1) + '\n' + 'DONE' + (m.group(3) or '\n'),
    text,
    flags=re.M,
)
if n == 0:
    # Try to add State if absent
    text_new = text + '\n## State\nDONE\n'
path.write_text(text_new)
PY
}

_harness_write_summary() {
  local _task_status="$1"
    python3 - "$_task_status" <<'PY'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
text = path.read_text()
# Section boundary: heading line, then body lines never starting with "##"
# (set_block's boundary in wake_task_state.sh), so an empty body is filled
# in place and the next section survives; the separator is re-emitted.
text_new, n = re.subn(
    r'(^## Summary[^\S\n]*)((?:\n(?!##)[^\n]*)*?)(\n+##|\n*\Z)',
    lambda m: m.group(1) + '\n' + 'Harness stub: task completed by integration test harness.' + (m.group(3) or '\n'),
    text,
    flags=re.M,
)
if n > 0:
    path.write_text(text_new)
PY
}
