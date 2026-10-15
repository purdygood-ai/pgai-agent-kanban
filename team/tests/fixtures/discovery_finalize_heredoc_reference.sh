#!/usr/bin/env bash
# team/tests/fixtures/discovery_finalize_heredoc_reference.sh
#
# Reference implementation for test_discovery_finalize_module_parity.py: the
# heredoc-backed bodies of the discovery intake/bundle/cache writers
# (team/scripts/lib/discovery_state.sh) and of the finalize ## Model read and
# ## Summary append (team/scripts/lib/wake_driver_finalize.sh), kept
# verbatim as they read before those call sites moved to the shared
# pgai_agent_kanban modules. Sourced AFTER the real libs, so each definition
# here replaces its module-backed counterpart and the parity test can run
# both on the same fixtures.
#
# Test-only: nothing under team/scripts sources this file.

_disc_set_status_field() {
    local file="$1"
    local new_status="$2"
    python3 - "$file" "$new_status" <<'PY'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
new_status = sys.argv[2]
if not path.is_file():
    print(f"ERROR: _disc_set_status_field: skipping non-path line (no such file): {path}", file=sys.stderr)
    sys.exit(0)
text = path.read_text(encoding="utf-8")

# Section boundary: the heading line, then body lines none of which starts
# with "##" (the boundary set_block in wake_task_state.sh splices with), so an
# empty ## Status is filled in place and never runs into the next section.
# The separator after the body is re-emitted as found.
pattern = re.compile(r'(^##[^\S\n]+Status[^\S\n]*)((?:\n(?!##)[^\n]*)*?)(\n+##|\n*\Z)', flags=re.M | re.IGNORECASE)
if pattern.search(text):
    new_text = pattern.sub(lambda m: m.group(1) + "\n" + new_status + (m.group(3) or "\n"), text, count=1)
else:
    # Insert after the first heading (e.g. "# BUG-NNNN-foo")
    lines = text.splitlines(keepends=True)
    insert_idx = 0
    for i, line in enumerate(lines):
        if line.startswith("# "):
            insert_idx = i + 1
            break
    block = f"\n## Status\n{new_status}\n"
    lines.insert(insert_idx, block)
    new_text = "".join(lines)

path.write_text(new_text, encoding="utf-8")
PY
}

_disc_read_bundle_field() {
    local bundle_file="$1"
    local field_name="$2"
    [[ -f "$bundle_file" ]] || return 1
    python3 - "$bundle_file" "$field_name" <<'PY'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
field = sys.argv[2]
text = path.read_text(encoding="utf-8", errors="replace")
# Match the heading, then capture the first non-blank content line.
m = re.search(
    rf'^##\s+{re.escape(field)}\s*\n\s*(\S[^\n]*)',
    text,
    flags=re.M | re.IGNORECASE,
)
if not m:
    sys.exit(1)
print(m.group(1).strip())
PY
}

_disc_update_bundle_pm_queued() {
    local bundle_file="$1"
    local pm_task_id="$2"
    # Update ## Status field
    _disc_set_status_field "$bundle_file" "running"
    # Update ## PM Task field
    python3 - "$bundle_file" "$pm_task_id" <<'PY'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
task_id = sys.argv[2]
text = path.read_text(encoding="utf-8")
pattern = re.compile(
    r'(^##\s+PM Task\s*\n\s*)(none|[^\n]*)(\n?)',
    flags=re.M | re.IGNORECASE,
)
if pattern.search(text):
    new_text = pattern.sub(lambda m: m.group(1) + task_id + "\n", text, count=1)
else:
    # Field absent — append it before the first non-status ## heading
    heading_re = re.compile(r'^##\s+(?!Status\b|PM Task\b)', re.M | re.IGNORECASE)
    m = heading_re.search(text)
    if m:
        insert_pos = m.start()
        new_text = text[:insert_pos] + f"## PM Task\n\n{task_id}\n\n" + text[insert_pos:]
    else:
        suffix = "" if text.endswith("\n") else "\n"
        new_text = text + suffix + f"\n## PM Task\n\n{task_id}\n"
path.write_text(new_text, encoding="utf-8")
PY
}

_disc_mark_cache_bundled() {
    local cache_file="$1"
    local item_id="$2"

    python3 - "$cache_file" "$item_id" <<'PY'
import pathlib, re, sys
cache = pathlib.Path(sys.argv[1])
item_id = sys.argv[2]

if not cache.is_file():
    # Create cache with header and the new entry
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(f"# Backlog Cache\n\n## Queue\n\n- [x] {item_id}\n")
    raise SystemExit(0)

text = cache.read_text()

# Look for existing "- [ ] item_id" or "- [x] item_id" line
entry_re = re.compile(rf'^(\s*-\s+\[)([x ])(\]\s+{re.escape(item_id)})(\b.*)?$', re.M)
m = entry_re.search(text)
if m:
    new_text = entry_re.sub(lambda mm: mm.group(1) + 'x' + mm.group(3) + (mm.group(4) or ''), text, count=1)
    cache.write_text(new_text)
    raise SystemExit(0)

# Not present: append under ## Queue, or create the section
if re.search(r'^## Queue\s*$', text, re.M):
    # Append after the last existing entry under Queue, or right after the Queue heading
    new_text = re.sub(
        r'(^## Queue\s*\n(?:.*\n)*?)(?=\n##|\Z)',
        lambda m: m.group(1).rstrip() + f"\n- [x] {item_id}\n",
        text,
        count=1,
        flags=re.M,
    )
    cache.write_text(new_text)
else:
    # No Queue section — append one
    suffix = "" if text.endswith("\n") else "\n"
    cache.write_text(text + suffix + f"\n## Queue\n\n- [x] {item_id}\n")
PY
}

narrate_model_mismatch() {
  local task_status="$1"
  local tokens_json_path="$2"
  local narrate_task_id="${3:-<unknown>}"

  if [[ -z "$task_status" || ! -f "$task_status" ]]; then
    return 0
  fi

  local stamped_model
  stamped_model="$(python3 - "$task_status" <<'PY'
import pathlib, re, sys
try:
    text = pathlib.Path(sys.argv[1]).read_text()
except OSError:
    sys.exit(0)
m = re.search(r'(^## Model[^\S\n]*)((?:\n(?!##)[^\n]*)*?)(\n+##|\n*\Z)', text, flags=re.M)
if not m:
    sys.exit(0)
value = m.group(2).strip()
if value:
    print(value)
PY
)"

  if [[ -z "$stamped_model" ]]; then
    return 0
  fi

  local observed_model
  observed_model="$(resolve_dominant_output_token_model "$tokens_json_path")" || return 0

  if [[ -z "$observed_model" || "$observed_model" == "$stamped_model" ]]; then
    return 0
  fi

  log "WARNING: model mismatch for task ${narrate_task_id}: status.md ## Model stamped '${stamped_model}' but tokens.json shows '${observed_model}' produced the dominant share of output tokens"
  return 0
}

# The pollution sweep's Summary append, lifted out of wd_finalize_task into
# the function shape the converted lib gives it.
_wd_append_summary_finding() {
  local task_status="$1"
  local _pol_finding="$2"
  python3 - "${task_status}" "${_pol_finding}" <<'PY_POL'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
finding = sys.argv[2]
text = path.read_text()
# Body lines never start with "##" (set_block's boundary): an empty Summary never runs into the next section.
pattern = r'(^## Summary[^\S\n]*)((?:\n(?!##)[^\n]*)*?)(\n+##|\n*\Z)'
def _append_finding(m):
    existing = m.group(2).strip('\n')
    return m.group(1) + '\n' + existing + ('\n\n' if existing else '') + finding + (m.group(3) or '\n')
text_new, n = re.subn(pattern, _append_finding, text, flags=re.M)
if n == 0:
    sys.exit(0)
path.write_text(text_new)
PY_POL
}
