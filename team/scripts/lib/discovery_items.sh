#!/usr/bin/env bash
# team/scripts/lib/discovery_items.sh
#
# Discovery item-scanning stage: the intake-item eligibility scan (bug-ID
# collision detection, quarantine of malformed filenames, the settle guard
# that defers a just-reset item for one tick, and the unhandled-item scan
# itself). Sourced by team/scripts/lib/discovery.sh; extracted from that
# file's former inline "Internal helpers" section so discovery.sh's own body
# stays a thin driver over sourced lib stages. This is a behavior-preserving
# move: no stdout/stderr line, exit code, quarantine move, sidecar write, or
# settle-file write changes as a result of this file existing — it is one
# stage of a driver that was split into single-purpose stage libraries so
# each stays a focused, independently testable unit.
#
# Public entry point
# -------------------
#   _disc_find_unhandled_items <dir> <backlog_cache> <id_prefix> [state_dir] [project_name]
#       Scans <dir> for BUG-/PRIORITY- items eligible for bundling: matches
#       the canonical filename pattern, quarantines malformed names via
#       _disc_maybe_quarantine once the per-file rejection threshold is
#       reached, applies the ## Status header as the authoritative
#       eligibility gate, and suppresses any item whose settle guard has not
#       yet expired. Called directly by discovery_step_bugs and
#       discovery_step_priority in discovery_steps.sh.
#
#       stdout contract: stdout carries ONLY newline-separated absolute item
#       paths -- every line starts with '/' -- because callers capture it
#       with $(...) as bundle content. Every other line (a filename
#       rejection, quarantine narration, a settle-guard deferral, and the
#       log() output of every helper that runs inside the finder) goes to
#       stderr. The contract is enforced structurally rather than by
#       convention: the scan's own stdout is captured into a variable, the
#       quarantine and settle phases then run with stdout rebound to stderr
#       while validated paths are written through a separate descriptor
#       bound to the caller's real stdout, and a scan line that does not
#       start with '/' is dropped and narrated on stderr instead of
#       emitted. A log() implementation that taps stdout, or a helper that
#       echoes, therefore cannot reach the caller's captured value.
#
# Other functions in this file (_disc_check_bug_id_collisions,
# _disc_maybe_quarantine, _disc_settle_guard_active, _disc_write_settle_marker,
# _disc_reset_item_status_to_open) are shared primitives, not private helpers
# of _disc_find_unhandled_items alone:
#   - _disc_check_bug_id_collisions is called directly by discovery_step_bugs
#     in discovery.sh, in addition to being a standalone non-fatal scan.
#   - _disc_maybe_quarantine is called internally by
#     _disc_find_unhandled_items, and also directly by
#     team/verification/verify_quarantine_visibility.sh for synthetic
#     rejection testing.
#   - _disc_settle_guard_active is called internally by
#     _disc_find_unhandled_items to suppress not-yet-settled items.
#   - _disc_write_settle_marker and _disc_reset_item_status_to_open implement
#     the running→open reset path (cancel-rc integration, operator recovery
#     tooling); _disc_reset_item_status_to_open calls
#     _disc_set_status_field, defined in
#     team/scripts/lib/discovery_state.sh (a cross-file dependency — see
#     Dependencies below).
# They are defined at source time (available as soon as this file is
# sourced, independent of when _disc_find_unhandled_items itself is invoked).
#
# Dependencies (must already be sourced/defined by the caller before this
# file's functions are called)
# -----------------------------------------------------------------------
#   team/scripts/lib/temp.sh — pgai_mktemp (used by _disc_find_unhandled_items
#     for its quarantine-reason scratch file).
#   team/scripts/lib/project_paths.sh — pp_rejected_dir (used by
#     _disc_maybe_quarantine when project_name is supplied).
#   team/scripts/lib/discovery_state.sh — _disc_set_status_field (called by
#     _disc_reset_item_status_to_open).
#   team/scripts/lib/discovery.sh itself — the log() shim, and TEAM_ROOT for
#     _disc_check_bug_id_collisions's bug_scanner.py search (falls back to
#     self-locating from BASH_SOURCE when TEAM_ROOT is unset).
#   team/scripts/lib/pp_run_ops.sh — self-sourced below. Every scan and
#     state-file rewrite is a call into pgai_agent_kanban.discovery.items
#     (the item scan, the rejection counter, the BUG-ID collision report),
#     which imports pgai_agent_kanban.lib.ledger_filename_contract for the
#     canonical BUG-/PRIORITY- filename pattern and
#     pgai_agent_kanban.lib.bundle_items for the carried- and
#     orphaned-item rules.
#
# python3 must be on PATH.

# Guard against double-sourcing.
if declare -F _disc_find_unhandled_items >/dev/null 2>&1; then
    return 0
fi

if ! declare -F pp_run_ops >/dev/null 2>&1; then
    # shellcheck source=pp_run_ops.sh
    source "$(dirname "${BASH_SOURCE[0]}")/pp_run_ops.sh"
fi

# _disc_check_bug_id_collisions <bugs_dir>
#
# Scan bugs_dir for files sharing the same BUG-NNNN numeric prefix and emit a
# WARNING log line for each collision detected.  Delegates to
# detect_duplicate_bug_ids() in pm-agent/lib/bug_scanner.py.
# Non-fatal: always returns 0 so the pipeline continues.
_disc_check_bug_id_collisions() {
    local bugs_dir="$1"
    [[ -d "$bugs_dir" ]] || return 0

    # Locate the bug_scanner module.  Search order:
    #   1. ${TEAM_ROOT}/pm-agent  — live install layout
    #   2. ${_disc_this_dir}/../../../team/pm-agent — dev-tree layout when sourced
    #      from team/scripts/lib/ (BASH_SOURCE[0] resolves 3 levels up to repo root,
    #      then descend into team/pm-agent)
    #   3. ${_disc_this_dir}/../pm-agent — alternate dev-tree layout
    # The first directory whose lib/bug_scanner.py exists is used.
    local _disc_this_dir _scanner_dir
    _disc_this_dir="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

    local _candidate
    for _candidate in \
        "${TEAM_ROOT:-}/pm-agent" \
        "${_disc_this_dir}/../../pm-agent" \
        "${_disc_this_dir}/../../../team/pm-agent" \
        "${_disc_this_dir}/../pm-agent"
    do
        if [[ -f "${_candidate}/lib/bug_scanner.py" ]]; then
            _scanner_dir="$_candidate"
            break
        fi
    done

    if [[ -z "$_scanner_dir" ]]; then
        # Cannot locate bug_scanner; skip collision check silently (non-fatal).
        return 0
    fi

    # Each duplicated number prints one WARNING line on stderr, where the
    # caller's log() shim sees it.
    pp_run_ops pgai_agent_kanban.discovery.items collisions "$bugs_dir" "$_scanner_dir" || true
    return 0
}

# _disc_maybe_quarantine <state_dir> <parent_dir> <filename> [reason] [project_name]
#
# Increment the per-file rejection counter for <filename> in the state file at
# <state_dir>/rejected-counts.tsv.  If the counter reaches the configured
# threshold (PGAI_DISCOVERY_REJECT_THRESHOLD, default 3), move the file from
# <parent_dir>/<filename> to projects/<project_name>/rejected/<filename>
# (project-level rejected directory via pp_rejected_dir).
#
# When project_name is empty or pp_rejected_dir fails, falls back to the
# <parent_dir>/.rejected/<filename> destination.
#
# After a successful move, a sidecar file <filename>.reason is written into
# the same rejected/ directory with the following key=value fields:
#   original_type  — basename of parent_dir (bugs|priority|requirements)
#   original_dir   — absolute path of parent_dir (used by recover-rejected.sh)
#   reason         — rejection reason string (may be empty)
#   rejected_at    — ISO 8601 UTC timestamp of the quarantine event
#   retry_count    — number of times this file has been quarantined (starts at 0)
#
# Re-quarantine: if the sidecar already exists in the rejected/ directory,
# retry_count is read from it, incremented by 1, and the sidecar is rewritten
# with an updated rejected_at.  Sidecar write failures are logged but do not
# abort the move (the file move is the authoritative effect).
#
# State file format (TSV, one record per file):
#   <filename>\t<count>\t<last-seen-ISO8601>\t<reason>
#
# The optional 4th column <reason> carries the last rejection reason string
# (e.g. "malformed filename (expected pattern: BUG-NNNN-<slug>.md)").
# Rows with only 3 columns have the reason field defaulting to empty string.
#
# Constraints:
#   - Files are moved, never deleted.
#   - Rejected directory is created on demand (via pp_rejected_dir or mkdir -p).
#   - The state file is safe to delete; it is recreated on the next rejection.
#   - Counter resets implicitly when a file is moved (the source path no longer
#     exists, so the next cron tick does not see it for rejection; if a same-named
#     file is later re-introduced, it starts with count=0 in the state file or is
#     absent from it, so it gets a fresh count).
#
# Logs actions to stderr (operator-visible).
# ---------------------------------------------------------------------------
_disc_maybe_quarantine() {
    local state_dir="$1"
    local parent_dir="$2"
    local filename="$3"
    local reason="${4:-}"        # optional rejection reason (4th column in TSV)
    local project_name="${5:-}"  # optional project name for pp_rejected_dir routing
    local source_file="${parent_dir}/${filename}"

    # Guard: source file must still exist (could have been moved by a concurrent call)
    [[ -f "$source_file" ]] || return 0

    local threshold="${PGAI_DISCOVERY_REJECT_THRESHOLD:-3}"

    # Ensure state directory exists
    mkdir -p "$state_dir"

    local state_file="${state_dir}/rejected-counts.tsv"
    local now
    now="$(date -Iseconds 2>/dev/null || date '+%Y-%m-%dT%H:%M:%S')"

    # Read current count for this filename (0 if absent).
    # State file format: <filename>\t<count>\t<timestamp>[\t<reason>]
    # 3-column rows are accepted; reason defaults to ''.
    local current_count=0
    if [[ -f "$state_file" ]]; then
        local found_line
        found_line="$(grep -P "^${filename}\t" "$state_file" 2>/dev/null | head -1 || true)"
        if [[ -n "$found_line" ]]; then
            current_count="$(echo "$found_line" | cut -f2)"
            # Validate numeric
            [[ "$current_count" =~ ^[0-9]+$ ]] || current_count=0
        fi
    fi

    local new_count=$(( current_count + 1 ))

    # Update or insert the record in the state file. Writes 4-column rows:
    # filename, count, timestamp, reason. Other files' rows -- 3-column
    # ones included -- are kept as written; only <filename>'s row is
    # rewritten, in 4-column form.
    pp_run_ops pgai_agent_kanban.discovery.items record-rejection \
        "$state_file" "$filename" "$new_count" "$now" "$reason"

    if [[ "$new_count" -lt "$threshold" ]]; then
        log "_disc_maybe_quarantine: ${filename}: rejection count ${new_count}/${threshold}; not yet quarantined"
        return 0
    fi

    # Threshold reached: move file to the project-level rejected/ directory.
    # Use pp_rejected_dir when project_name is known; fall back to
    # <parent_dir>/.rejected/ when it is empty or the helper is unavailable.
    local rejected_dir=""
    if [[ -n "$project_name" ]] && declare -F pp_rejected_dir >/dev/null 2>&1; then
        rejected_dir="$(pp_rejected_dir "$project_name" 2>/dev/null)" || rejected_dir=""
    fi
    if [[ -z "$rejected_dir" ]]; then
        # Legacy fallback: keep files co-located with their source directory.
        rejected_dir="${parent_dir}/.rejected"
        mkdir -p "$rejected_dir"
    fi
    local dest_file="${rejected_dir}/${filename}"

    # Determine sidecar retry_count: if the sidecar already exists (re-quarantine),
    # read the existing retry_count and increment; otherwise start at 0.
    local sidecar_file="${rejected_dir}/${filename}.reason"
    local retry_count=0
    if [[ -f "$sidecar_file" ]]; then
        local _existing_count
        _existing_count="$(grep -E '^retry_count=' "$sidecar_file" 2>/dev/null | head -1 | cut -d= -f2 || true)"
        if [[ "$_existing_count" =~ ^[0-9]+$ ]]; then
            retry_count=$(( _existing_count + 1 ))
        else
            retry_count=1
        fi
    fi

    # Derive original_type from the basename of parent_dir (bugs|priority|requirements).
    local original_type
    original_type="$(basename "$parent_dir")"

    # ISO 8601 UTC timestamp for the sidecar.
    local rejected_at
    rejected_at="$(date -u +'%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -Iseconds)"

    if mv "$source_file" "$dest_file"; then
        # Emit multi-line quarantine log with reason and recovery hint.
        # Line 1 — summary
        log "_disc_maybe_quarantine: ${filename}: quarantined after ${new_count} failed parse(s)."
        # Line 2 — reason (from the 4th TSV column written by the validator)
        if [[ -n "$reason" ]]; then
            log "  Reason: ${reason}"
        else
            log "  Reason: unknown"
        fi
        # Line 3 — recovery hint with example rename, chosen based on filename prefix.
        # Constraints: use the reason/pattern already stored; do not re-derive it.
        # Note: PRIORITY and BUG share the same date-optional-after-the-number
        # pattern family (^{PREFIX}-[0-9]{4}(-[0-9]{8})?-[a-z0-9][a-z0-9-]*\.md$);
        # the PRIORITY branch is intentionally absent here — a dateless PRIORITY
        # is valid and does not need renaming.
        if [[ "$filename" == BUG-* ]]; then
            log "  Recovery: rename file to match pattern ^BUG-[0-9]{4}(-[0-9]{8})?-[a-z0-9][a-z0-9-]*\\.md\$, e.g."
            local _stem="${filename%.md}"
            local _num _slug _example_name
            _num="$(echo "$_stem" | sed 's/^BUG-\([0-9]*\)-.*/\1/')"
            _slug="$(echo "$_stem" | sed 's/^BUG-[0-9]*-//')"
            _example_name="BUG-${_num}-${_slug}.md"
            log "    '${_example_name}', then move back to $(basename "${parent_dir}")/"
        else
            log "  Recovery: rename file to match the expected pattern, then move back to $(basename "${parent_dir}")/"
        fi
        # Line 4 — quarantine path
        log "  Quarantine path: ${dest_file}"

        # Write sidecar <filename>.reason co-located with the quarantined file.
        # Atomicity: the file move above is the authoritative effect; sidecar write
        # failures are logged and do not abort.  On re-quarantine, the sidecar is
        # overwritten with an incremented retry_count and refreshed rejected_at.
        if printf '%s\n' \
            "original_type=${original_type}" \
            "original_dir=${parent_dir}" \
            "reason=${reason}" \
            "rejected_at=${rejected_at}" \
            "retry_count=${retry_count}" \
            > "$sidecar_file" 2>/dev/null; then
            log "  Sidecar written: ${sidecar_file} (retry_count=${retry_count})"
        else
            log "  WARNING: failed to write sidecar ${sidecar_file}; quarantine move succeeded but reason metadata is absent"
        fi

        # Remove the entry from the state file so a re-introduced same-named file
        # starts with a fresh counter.
        pp_run_ops pgai_agent_kanban.discovery.items forget-rejection "$state_file" "$filename"
    else
        log "_disc_maybe_quarantine: WARNING: failed to move ${source_file} to ${dest_file}; will retry next cron tick"
    fi
}

# _disc_find_unhandled_items <dir> <backlog_cache> <id_prefix> [state_dir] [project_name]
# Find items in <dir> that:
#   1. Match the canonical filename pattern for the given id_prefix,
#      sourced from pgai_agent_kanban.lib.ledger_filename_contract (the scan
#      itself is pgai_agent_kanban.discovery.items scan):
#      BUG-      -> ^BUG-[0-9]{4}(-[0-9]{8})?-[a-z0-9][a-z0-9-]*\.md$
#      PRIORITY- -> ^PRIORITY-[0-9]{4}(-[0-9]{8})?-[a-z0-9][a-z0-9-]*\.md$
#                  OR (TESTER Path C) ^v[0-9]+\.[0-9]+\.[0-9]+-bugfix-\S+\.md$
#      The numeric field is exactly 4 digits; an 8-digit date segment is
#      permitted only immediately AFTER the number (historical compatibility).
#      BUG- and PRIORITY- must stay in sync — both consumers import the same
#      module, so tightening one tightens the other automatically.
#   2. Have ## Status: open (or no ## Status) in their header -- or
#      ## Status: running while every bundle in the sibling requirements/
#      directory that lists them is done or wont-do: a release shipped
#      them without a DONE task claiming their fix (carried), and they are
#      candidates for the next bundle like any open item. A ## Status:
#      running item that no bundle in requirements/ lists at all is
#      orphaned -- its bundle was archived, purged or deleted while it was
#      unfinished -- and is re-offered too, with a "WARNING: re-offering
#      <name>" line on stderr.
# <backlog_cache> is accepted for call-shape compatibility and not read:
# the cache is derived from the same headers and never gates an item.
# Outputs newline-separated absolute paths on stdout.
#
# .md files in the directory that do NOT match the canonical pattern (and are
# not README.md and not inside templates/) produce a rejection message on
# stderr ONLY — never on stdout.  This ensures that rejection log output
# cannot contaminate the validated-items list returned to the caller.  For
# BUG-/PRIORITY- prefixes the rejection message is a prescriptive remedy
# naming the specific defect (date-first, under-padded, or other malformation)
# and the next free number — refuse-and-prescribe, never auto-rename.
# Rejection format: ERROR: rejecting <filename>: <reason>
#
# When the optional [state_dir] argument is supplied, each rejected file is
# passed to _disc_maybe_quarantine, which increments a per-file counter and
# moves the file to projects/<project_name>/rejected/<filename> (or
# <dir>/.rejected/<filename> when project_name is absent) once the counter
# reaches PGAI_DISCOVERY_REJECT_THRESHOLD (default 3).  This stops the
# discovery loop from re-rejecting the same misnamed file on every cron tick.
#
# The optional [project_name] argument (5th) is forwarded to
# _disc_maybe_quarantine to enable project-level rejected/ routing via
# pp_rejected_dir.  When absent, quarantine uses the local .rejected/ fallback.
#
# All rejection output goes directly to stderr from the scan — stdout carries
# only validated absolute paths. The full stdout contract, and how the
# function enforces it against its own helpers, is stated in this file's
# header under "Public entry point".
_disc_find_unhandled_items() {
    local dir="$1"
    local cache_file="$2"       # accepted for call-shape compatibility; not read
    local id_prefix="$3"
    local state_dir="${4:-}"    # optional: path to .discovery-state/ for quarantine tracking
    local project_name="${5:-}" # optional: project name for pp_rejected_dir routing

    [[ -d "$dir" ]] || return 0

    # When quarantine tracking is requested, the scan writes rejected basename+reason
    # pairs to a temp file so bash can call _disc_maybe_quarantine without touching
    # stdout.  Format: one TSV line per rejected file: <basename>\t<reason>
    local rejected_tmp=""
    if [[ -n "$state_dir" ]]; then
        rejected_tmp="$(pgai_mktemp disc_rejected)"
    fi

    # stdout  → validated item paths (captured by caller via $())
    # stderr  → rejection and skip messages (operator-visible; never captured as content)
    # $rejected_tmp (when set) → one "<basename>\t<reason>" line per rejected file
    #
    # Settle guard: after the scan, each candidate is passed through
    # _disc_settle_guard_active.  Items whose settle window has not yet expired
    # are suppressed from stdout so the caller never sees them.  The settle
    # guard fires only when state_dir is set and a fresh settle file exists.
    local _py_validated_paths=""
    _py_validated_paths="$(pp_run_ops pgai_agent_kanban.discovery.items scan \
        "$dir" "$id_prefix" "${rejected_tmp:-}")"

    # stdout contract enforcement (see this file's header): for the rest of
    # the function stdout is rebound to stderr, so the quarantine and settle
    # phases -- and any log() implementation they call, whatever channel it
    # writes to -- cannot reach the value the caller captures. fd 3 is bound
    # to the caller's real stdout and is the only place a validated path is
    # written.
    {
        # Quarantine tracking: process each rejected basename+reason through _disc_maybe_quarantine.
        # Each line in rejected_tmp is: <basename>\t<reason>.
        # Lines with no tab character are treated as basename-only (reason='').
        if [[ -n "$state_dir" && -n "$rejected_tmp" && -s "$rejected_tmp" ]]; then
            while IFS=$'\t' read -r rejected_name rejected_reason; do
                [[ -z "$rejected_name" ]] && continue
                _disc_maybe_quarantine "$state_dir" "$dir" "$rejected_name" "${rejected_reason:-}" "${project_name:-}"
            done < "$rejected_tmp"
        fi

        # Clean up the temp file (ignore errors — failure to clean is not fatal)
        [[ -n "$rejected_tmp" ]] && rm -f "$rejected_tmp" 2>/dev/null || true

        # Settle guard: emit only items whose settle window has expired (or no settle
        # file exists).  Items with a fresh settle file are suppressed and logged.
        # This closes the cancel→reset→re-tick double-handling race: after an operator
        # resets an intake item's Status back to 'open', the guard defers pickup for
        # one settle window (PGAI_DISCOVERY_SETTLE_SECONDS, default 60) so that the
        # cancellation bookkeeping completes before the item is re-processed.
        if [[ -n "$_py_validated_paths" ]]; then
            while IFS= read -r _candidate_path; do
                [[ -z "$_candidate_path" ]] && continue
                # A scan line that is not an absolute path is never emitted:
                # whatever wrote it is narrated verbatim so the occurrence
                # diagnoses itself, and the genuine paths still flow.
                if [[ "$_candidate_path" != /* ]]; then
                    log "_disc_find_unhandled_items: dropping non-path line from the item scan's stdout (absolute paths only): '${_candidate_path}'"
                    continue
                fi
                if [[ -n "$state_dir" ]] && _disc_settle_guard_active "$state_dir" "$_candidate_path"; then
                    log "_disc_find_unhandled_items: settle guard active for $(basename "${_candidate_path}") — deferring pickup until settle window expires"
                else
                    printf '%s\n' "$_candidate_path" >&3
                fi
            done <<< "$_py_validated_paths"
        fi
    } 3>&1 1>&2
}

# ---------------------------------------------------------------------------
# Settle guard: protect against cancel→reset→re-tick double-handling.
#
# When an intake item (bug, priority, or requirements bundle) transitions from
# 'running' back to 'open' — because an operator or cancel-rc resets it — the
# discovery pipeline must not act on it again in the same tick window.  Acting
# on it immediately risks double-handling: a bug could be re-bundled while the
# previous bundle's PM task is still being cleaned up, or a requirements file
# could be re-queued while the prior RC is still being cancelled.
#
# The guard uses a per-item settle file stored at:
#   <state_dir>/settle/<item-basename>.settle
# The file records the UTC timestamp of the reset event.  When discovery finds
# an item eligible (Status=open), it checks whether a settle file exists and is
# less than PGAI_DISCOVERY_SETTLE_SECONDS old (default: 60).  If so, it skips
# the item for this iteration and emits a log line.  On the next tick (after the
# settle window expires), the item is processed normally.
#
# Contract:
#   - Settle files are WRITTEN by _disc_reset_item_status_to_open (the
#     running→open transition wrapper).  Direct callers of _disc_set_status_field
#     for non-reset transitions (bundling step marks running; cm-release marks done)
#     do NOT write settle files — the guard only applies to the reset path.
#   - Settle files are READ by _disc_settle_guard_active (called from
#     _disc_find_unhandled_items).
#   - Settle files are not deleted after use; they expire by age comparison.
#     This avoids write-delete races under concurrent discovery runs.
#   - PGAI_DISCOVERY_SETTLE_SECONDS defaults to 60.  Tests set it to 0 to
#     exercise the "guard fires immediately" path.
# ---------------------------------------------------------------------------

# _disc_settle_guard_active <state_dir> <item_path>
#
# Returns 0 (guard active — SKIP this item) when a settle file for the item
# exists in <state_dir>/settle/ AND was written less than
# PGAI_DISCOVERY_SETTLE_SECONDS ago.
#
# Returns 1 (guard inactive — proceed) in all other cases:
#   - state_dir is empty or does not exist
#   - settle file does not exist
#   - settle file exists but is older than the window
#   - PGAI_DISCOVERY_SETTLE_SECONDS is 0 (guard disabled)
#
# This is intentionally a non-fatal, non-side-effecting check: it reads
# the settle file and compares timestamps only.  No files are written here.
_disc_settle_guard_active() {
    local state_dir="$1"
    local item_path="$2"

    # Disabled when state_dir is absent (settle tracking not configured).
    [[ -z "$state_dir" ]] && return 1

    local settle_window="${PGAI_DISCOVERY_SETTLE_SECONDS:-60}"

    # A window of 0 means "disabled" — skip the check entirely.
    # This prevents divide-by-zero and provides a test escape hatch.
    if [[ "$settle_window" -eq 0 ]]; then
        return 1
    fi

    local item_basename
    item_basename="$(basename "$item_path")"
    local settle_file="${state_dir}/settle/${item_basename}.settle"

    [[ -f "$settle_file" ]] || return 1

    # Read the epoch timestamp written into the settle file (first non-blank line).
    local settle_epoch
    settle_epoch="$(grep -m1 '^[0-9]' "$settle_file" 2>/dev/null || true)"
    [[ -n "$settle_epoch" ]] || return 1

    # Compare against current time.
    local now_epoch
    now_epoch="$(date +%s 2>/dev/null || echo 0)"

    local age=$(( now_epoch - settle_epoch ))
    if [[ "$age" -lt "$settle_window" ]]; then
        return 0  # guard active: settle window not yet expired
    fi

    return 1  # guard inactive: settle window has passed
}

# _disc_write_settle_marker <state_dir> <item_path>
#
# Write a settle file for the given item.  Called by _disc_reset_item_status_to_open
# immediately after setting Status=open so that the next discovery tick knows a
# recent reset occurred and defers pickup.
#
# The settle file contains the current UTC epoch timestamp on the first line
# and the item path on the second line (for operator diagnostics).
# The settle/ directory is created on demand.
_disc_write_settle_marker() {
    local state_dir="$1"
    local item_path="$2"

    [[ -z "$state_dir" ]] && return 0

    local settle_dir="${state_dir}/settle"
    mkdir -p "$settle_dir" 2>/dev/null || true

    local item_basename
    item_basename="$(basename "$item_path")"
    local settle_file="${settle_dir}/${item_basename}.settle"
    local now_epoch
    now_epoch="$(date +%s 2>/dev/null || echo 0)"

    printf '%s\n%s\n' "$now_epoch" "$item_path" > "$settle_file" 2>/dev/null || true

    log "_disc_write_settle_marker: settle guard armed for ${item_basename} (expires in ${PGAI_DISCOVERY_SETTLE_SECONDS:-60}s)"
}

# _disc_reset_item_status_to_open <file> <state_dir>
#
# Reset an intake item's ## Status to 'open' and arm the settle guard.
# This is the canonical entry point for the running→open transition.
# Callers that need to reset an intake item (cancel-rc integration, operator
# tooling, recovery scripts) should use this function rather than calling
# _disc_set_status_field directly so that the settle guard fires on the next
# discovery tick.
#
# If <state_dir> is empty, the settle file is not written (settle guard
# inactive for this reset).  This matches the behaviour of _disc_set_status_field
# in codepaths that do not have a project-scoped state directory.
#
# Note: the Status field is unconditionally written to 'open' regardless of
# the current value.  Callers are responsible for ensuring this is appropriate
# (e.g., only calling this for items whose prior status was 'running').
_disc_reset_item_status_to_open() {
    local file="$1"
    local state_dir="${2:-}"

    # Update the Status field first (this is the authoritative mutation).
    _disc_set_status_field "$file" "open"

    # Arm the settle guard so the next discovery tick defers pickup.
    if [[ -n "$state_dir" ]]; then
        _disc_write_settle_marker "$state_dir" "$file"
    fi
}
