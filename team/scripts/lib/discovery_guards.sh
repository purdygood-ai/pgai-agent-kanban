#!/usr/bin/env bash
# team/scripts/lib/discovery_guards.sh
#
# Discovery release-state guard stage: the RC-in-flight, ceiling, chain-idle,
# PM-ticket-in-flight, and git-tag primitives that decide whether a discovery
# step is allowed to act. Sourced by team/scripts/lib/discovery.sh; extracted
# from that file's former inline "Internal helpers" section so discovery.sh's
# own body stays a thin driver over sourced lib stages. This is a
# behavior-preserving move: no stdout/stderr line, exit code, or on-disk
# write changes as a result of this file existing — it is one stage of a
# driver that was split into single-purpose stage libraries so each stays
# a focused, independently testable unit.
#
# Public entry point
# -------------------
#   _disc_chain_idle
#       The composite chain-busy test discovery_run_pipeline runs before any
#       scan (and again under the repo lock before its first write):
#       returns 0 only when Active RC is "none" AND every task entry named
#       in every agent backlog (pm, cm, coder, tester, writer) resolves —
#       via its own status.md ## State, never the queue marker — to a
#       terminal state (DONE or WONT-DO). Logs the specific offending
#       condition (project, agent, task, and its state) and returns 1
#       otherwise. Each backlog is resolved in one batched read
#       (_disc_backlog_task_states), so the test stays cheap however many
#       entries a queue has accumulated. See _disc_task_terminal and
#       _disc_chain_idle's own header comments for the full
#       status-vs-marker rationale.
#
# Other functions in this file (_disc_get_release_field, _disc_task_terminal,
# _disc_backlog_task_states, _disc_active_rc_blocks,
# _disc_check_ceiling_and_log, _disc_pm_ticket_in_flight,
# _disc_git_tag_exists) are shared primitives, not private helpers of
# _disc_chain_idle alone:
#   - _disc_get_release_field is called directly by
#     team/scripts/pm-agent.sh, team/scripts/wake/claude.sh, and
#     team/scripts/wake/codex.sh (all of which source discovery.sh, which in
#     turn sources this file), in addition to being used internally here by
#     _disc_active_rc_blocks and _disc_chain_idle.
#   - _disc_active_rc_blocks and _disc_check_ceiling_and_log are called
#     directly by discovery_step_bugs and discovery_step_priority in
#     discovery.sh.
#   - _disc_pm_ticket_in_flight is called directly by discovery_run_pipeline's
#     GUARD 4 (pending PM ticket) in discovery.sh.
#   - _disc_backlog_task_states is the batched status.md reader both
#     _disc_chain_idle and _disc_pm_ticket_in_flight use.
#   - _disc_git_tag_exists is called directly by discovery_compute_next_patch
#     in discovery.sh (the bump-around loop).
# They are defined at source time (available as soon as this file is
# sourced, independent of when _disc_chain_idle itself is invoked).
#
# Dependencies (must already be sourced/defined by the caller before this
# file's functions are called)
# -----------------------------------------------------------------------
#   team/scripts/lib/project_paths.sh — pp_require_project_context,
#     pp_release_state, pp_queue_path, pp_max_major, pp_max_minor,
#     pp_max_patch.
#   team/scripts/lib/discovery.sh itself — the log() shim (falls back to a
#     stderr printer when the caller has not defined its own).
#   team/scripts/lib/error_contract.sh — self-sourced below (mirrors
#     lib/lock_provenance.sh's own self-sourcing idiom) so
#     pgai_emit_error_contract is available whether this file is sourced
#     through the full wake/discovery pipeline or on its own, as unit
#     tests do.
#
#   team/scripts/lib/cm/common.sh — self-sourced below, the same way, for
#     release_state_section: _disc_get_release_field reads release-state.md
#     through the one release-state reader (cm/read_state_field.py), so
#     python3 must be on PATH.
#   team/scripts/lib/pp_run_ops.sh — self-sourced below, for
#     _disc_task_terminal's read of a task's ## State
#     (pgai_agent_kanban.discovery.items task-state) and
#     _disc_backlog_task_states's batched read of a whole queue's states
#     (pgai_agent_kanban.discovery.items task-states).

# Guard against double-sourcing.
if declare -F _disc_chain_idle >/dev/null 2>&1; then
    return 0
fi

if ! declare -F pp_run_ops >/dev/null 2>&1; then
    # shellcheck source=pp_run_ops.sh
    source "$(dirname "${BASH_SOURCE[0]}")/pp_run_ops.sh"
fi

if ! declare -F pgai_emit_error_contract >/dev/null 2>&1; then
    # shellcheck source=error_contract.sh
    source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/error_contract.sh"
fi

if ! declare -F release_state_section >/dev/null 2>&1; then
    # shellcheck source=cm/common.sh
    source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/cm/common.sh"
fi

# _disc_active_rc_blocks <step_name> <project_name>
#
# Shared active-RC guard: returns 1 (blocks) when an RC is in flight for the
# given project, 0 (allows) when the Active RC field is "none".
#
# Callers (discovery_step_bugs and discovery_step_priority) use this helper
# so the two bundler steps cannot diverge in their Active-RC guard semantics.
#
# Three cases:
#   - release-state.md missing or unreadable → log WARNING, return 1 (safe-fail)
#   - Active RC = "none" (case-insensitive, whitespace-stripped) → return 0 (allow)
#   - Active RC = any non-"none" value → log skip message, return 1 (block)
_disc_active_rc_blocks() {
    local step_name="$1"
    local project_name="$2"
    local _active_rc
    if ! _active_rc="$(_disc_get_release_field "$project_name" "Active RC" 2>/dev/null)"; then
        log "${step_name}: WARNING release-state.md missing or unreadable for ${project_name}; skipping bundle (cannot verify RC state)"
        pgai_emit_error_contract \
            "discovery.release-state-unreadable" \
            "discovery could not verify Active RC for project '${project_name}' because release-state.md is missing or unreadable, so the ${step_name} bundle was skipped this cycle" \
            "Confirm ${project_name}/release-state.md exists and is readable (check file permissions, or re-run CM-open-rc if the project has never shipped); discovery retries automatically on the next wake." \
            "${project_name}"
        # BLOCK-WORTHY: verification unable to run — without a readable
        # release-state.md, discovery cannot verify whether an RC is in
        # flight, so it conservatively skips rather than risking a
        # scope-creep bundle (same doctrine family as row 5, "Active-RC
        # single-flight guard", in docs/blocking-site-census.md).
        # CENSUS-ANCHOR: discovery-guards-release-state-unreadable-01
        return 1
    fi
    _active_rc="${_active_rc:-none}"
    # Normalise: strip leading/trailing whitespace, compare case-insensitively
    _active_rc="$(echo "$_active_rc" | tr -d '[:space:]')"
    if [[ "${_active_rc,,}" != "none" ]]; then
        log "skipping bundle — Active RC ${_active_rc} in flight"
        pgai_emit_error_contract \
            "discovery.active-rc-in-flight" \
            "discovery skipped the ${step_name} bundle for project '${project_name}' because an RC (${_active_rc}) is already in flight" \
            "Wait for the in-flight RC to ship (CM ships it) -- discovery re-checks on the next wake; if it is stuck and must be cleared, run: team/scripts/unwind-rc.sh --project ${project_name} --key ${_active_rc}" \
            "${project_name}"
        # BLOCK-WORTHY: invariant-by-design — allowing PM to decompose a
        # second release while an RC is in flight would inject unbounded
        # mid-release scope creep, moving the release backwards (rollup
        # row 5, "Active-RC single-flight guard", in
        # docs/blocking-site-census.md).
        # CENSUS-ANCHOR: discovery-guards-active-rc-in-flight-01
        return 1
    fi
    return 0
}

# _disc_check_ceiling_and_log <project_name> <version> <caller_label>
#
# Reads max_major, max_minor, and max_patch from project.cfg (or PROJECT.cfg) and checks all
# three against the given version. If any ceiling is exceeded, emits the
# documented rejection log line:
#
#   [<timestamp>] discovery: PM not queued for <version>: <component> version
#                 <value> exceeds <ceiling_name>=<ceiling_value>
#
# Returns 0 when the version is within all ceilings in effect (proceed).
# Returns 1 when at least one ceiling is exceeded, or a ceiling value is
# unreadable (skip/defer).
#
# The first failing ceiling component is reported and the function returns
# immediately (one log line per call per rejected version — as required).
_disc_check_ceiling_and_log() {
    local project_name="$1"
    local version="$2"
    # caller_label is unused in log output (log format is operator-facing)
    # but kept as a positional argument for future diagnostic use.

    # Strip leading 'v' or 'V'.
    local v_clean="${version#v}"
    v_clean="${v_clean#V}"

    # Parse X.Y.Z — malformed versions are treated as exceeding ceiling
    # (conservative: do not queue PM for an unparseable target version).
    if ! [[ "$v_clean" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
        log "PM not queued for ${version}: target version is malformed (expected vX.Y.Z)"
        pgai_emit_error_contract \
            "discovery.ceiling-version-malformed" \
            "discovery would not queue PM for project '${project_name}' at version ${version} because the target version is malformed (expected vX.Y.Z)" \
            "Fix the Target Version field in the requirements/bundle file to a well-formed vX.Y.Z string; discovery re-evaluates on the next wake." \
            "${project_name}"
        # BLOCK-WORTHY: approval/invariant-by-design — an unparseable
        # target version cannot be checked against the operator's
        # configured version ceilings at all, so discovery conservatively
        # refuses to queue PM rather than guessing a version was intended.
        # CENSUS-ANCHOR: discovery-guards-ceiling-malformed-01
        return 1
    fi
    local ver_major="${BASH_REMATCH[1]}"
    local ver_minor="${BASH_REMATCH[2]}"
    local ver_patch="${BASH_REMATCH[3]}"

    # Each ceiling is a number (configured or its documented default) or
    # empty for an explicit `none`. A malformed value: the accessor's one
    # stderr line (file, key, value) is left visible and the version is
    # treated as exceeding the ceiling -- never as "no ceiling".
    local max_major max_minor max_patch _ceiling_unreadable=0
    max_major="$(pp_max_major "$project_name")" || _ceiling_unreadable=1
    max_minor="$(pp_max_minor "$project_name")" || _ceiling_unreadable=1
    max_patch="$(pp_max_patch "$project_name")" || _ceiling_unreadable=1
    if (( _ceiling_unreadable )); then
        log "PM not queued for ${version}: a version ceiling is unreadable (named above)"
        # BLOCK-WORTHY: approval/invariant-by-design -- same reasoning as
        # the max_major case below: an unreadable ceiling cannot be honoured,
        # so the version is not queued past it.
        return 1
    fi

    if [[ -n "$max_major" ]] && (( ver_major > max_major )); then
        log "PM not queued for ${version}: major version ${ver_major} exceeds max_major=${max_major}"
        pgai_emit_error_contract \
            "discovery.ceiling-major-exceeded" \
            "discovery would not queue PM for project '${project_name}' at version ${version} because major version ${ver_major} exceeds the configured max_major=${max_major}" \
            "Raise max_major in ${project_name}/project.cfg [versioning] if this major version is intentional, or leave the ceiling as-is and let discovery skip this version until a lower one is queued." \
            "${project_name}"
        # BLOCK-WORTHY: approval/invariant-by-design — the operator
        # explicitly capped max_major in project.cfg, and honoring that
        # ceiling by refusing to queue a version past it is the point of
        # the setting.
        # CENSUS-ANCHOR: discovery-guards-ceiling-major-01
        return 1
    fi

    if [[ -n "$max_minor" ]] && (( ver_minor > max_minor )); then
        log "PM not queued for ${version}: minor version ${ver_minor} exceeds max_minor=${max_minor}"
        pgai_emit_error_contract \
            "discovery.ceiling-minor-exceeded" \
            "discovery would not queue PM for project '${project_name}' at version ${version} because minor version ${ver_minor} exceeds the configured max_minor=${max_minor}" \
            "Raise max_minor in ${project_name}/project.cfg [versioning] if this minor version is intentional, or leave the ceiling as-is and let discovery skip this version until a lower one is queued." \
            "${project_name}"
        # BLOCK-WORTHY: approval/invariant-by-design — same reasoning as
        # the max_major case above, for the operator's configured
        # max_minor ceiling.
        # CENSUS-ANCHOR: discovery-guards-ceiling-minor-01
        return 1
    fi

    if [[ -n "$max_patch" ]] && (( ver_patch > max_patch )); then
        log "PM not queued for ${version}: patch version ${ver_patch} exceeds max_patch=${max_patch}"
        pgai_emit_error_contract \
            "discovery.ceiling-patch-exceeded" \
            "discovery would not queue PM for project '${project_name}' at version ${version} because patch version ${ver_patch} exceeds the configured max_patch=${max_patch}" \
            "Raise max_patch in ${project_name}/project.cfg [versioning] if this patch version is intentional, or leave the ceiling as-is and let discovery skip this version until a lower one is queued." \
            "${project_name}"
        # BLOCK-WORTHY: approval/invariant-by-design — same reasoning as
        # the max_major case above, for the operator's configured
        # max_patch ceiling.
        # CENSUS-ANCHOR: discovery-guards-ceiling-patch-01
        return 1
    fi

    return 0
}

# _disc_get_release_field <project> <field> — read ## FIELD from release-state.md
# Prints the field's value, or an empty line when the heading has no value
# before the next "##" heading. Returns 1 with no output when release-state.md
# is missing or unreadable or the heading is absent.
_disc_get_release_field() {
    local project_name="$1"
    local field="$2"
    local value
    value="$(release_state_section "$(pp_release_state "$project_name")" "$field")" || return 1
    printf '%s\n' "$value"
}

# _disc_task_terminal <project_name> <task_id>
#
# Single-task status.md reader. The two decision functions below read a
# whole queue at once through _disc_backlog_task_states, which classifies
# every entry exactly as this function does. Echoes exactly one of:
#   DONE | WONT-DO   — the task's status.md ## State reads a terminal value
#   <STATE>          — any other parsed ## State value, uppercased verbatim
#                       (BACKLOG, WAITING, WORKING, BLOCKED, or any future
#                       value the state machine grows), meaning the task
#                       still occupies the chain
#   NO-FOLDER        — <tasks_dir>/<task_id>/status.md does not exist
#   UNPARSEABLE      — status.md exists but no ## State block could be parsed
#
# This is the pattern get_first_pending_task (wake_task_state.sh) already
# uses for BACKLOG pulls: read status.md, never the queue marker, because
# the marker is a hint the wake script writes for humans and dashboards,
# and status.md is the one place a task's real state lives (SOP: 'Per-Agent
# Queues and Marker Correctness'). This file does not source
# wake_task_state.sh (discovery.sh is loaded from contexts — pm-agent.sh,
# wake/codex.sh — that do not always source it first), so the read is
# reimplemented here rather than depending on it.
#
# NO-FOLDER / UNPARSEABLE design decision — the two cases are handled
# differently by the callers below, and deliberately so:
#
#   NO-FOLDER   — the task folder itself is not created ANYWHERE in this
#                 wake/kanban toolchain except by the same machinery that
#                 wrote the backlog entry in the first place, and task
#                 folders are never deleted once created (they are the
#                 permanent historical record — see roles/*.md and
#                 SOP-core.md's task-folder model). A backlog entry naming
#                 a task ID with no folder therefore has nothing left to
#                 verify: it is either a fixture/placeholder entry that was
#                 never meant to be a real task, or a pre-existing entry
#                 whose folder predates this convention. Treating it as
#                 blocking would let a single unresolvable historical
#                 reference wedge the chain-idle gate PERMANENTLY — there
#                 is no mechanism anywhere in the wake toolchain that heals
#                 a missing task folder, so a block here never clears on
#                 its own. Callers therefore fold NO-FOLDER into "does not
#                 block" (the same bucket as a terminal state), while still
#                 logging it distinctly so an operator scanning a wake log
#                 can see the anomaly.
#   UNPARSEABLE — the folder AND status.md both exist, so this is active
#                 corruption of a file the wake toolchain itself owns and
#                 writes in one fixed shape (ensure_status_file / set_state
#                 in wake_task_state.sh) — a real, fixable anomaly worth a
#                 hard stop. Callers fold UNPARSEABLE into "the task still
#                 occupies the chain" (blocking), consistent with this
#                 file's other safe-fail primitives (e.g.
#                 _disc_active_rc_blocks treats an unreadable
#                 release-state.md as blocking, not as permission to
#                 proceed).
_disc_task_terminal() {
    local project_name="$1"
    local task_id="$2"
    local tasks_dir status_file
    tasks_dir="$(pp_tasks_dir "$project_name" 2>/dev/null)" || { echo "NO-FOLDER"; return 0; }
    status_file="${tasks_dir}/${task_id}/status.md"
    if [[ ! -f "$status_file" ]]; then
        echo "NO-FOLDER"
        return 0
    fi
    pp_run_ops pgai_agent_kanban.discovery.items task-state "$status_file"
}

# _disc_backlog_task_states <project_name> <backlog_file> [id_filter_regex]
#
# Batched counterpart of _disc_task_terminal for a whole queue file: prints
# one "<task_id><TAB><STATE>" line per task entry in <backlog_file>, in file
# order, where STATE is exactly what _disc_task_terminal would echo for that
# entry (DONE, WONT-DO, any other parsed ## State, NO-FOLDER, UNPARSEABLE).
# The entry lines are picked out the same way the per-entry readers always
# did (a "- [<marker>] <id>" list item, marker ignored); when
# [id_filter_regex] is given, only entry lines that also match it (ERE) are
# read. One python process answers the whole queue -- the per-entry reader
# costs one process per entry, which on a queue of a few hundred entries
# turns a "cheap" busy test into tens of seconds. Prints nothing (returns 0)
# for a missing backlog file or a queue with no entries.
_disc_backlog_task_states() {
    local project_name="$1"
    local backlog_file="$2"
    local id_filter="${3:-}"
    [[ -f "$backlog_file" ]] || return 0

    local _ids
    _ids="$(grep -E '^\s*-\s+\[[^]]*\]\s+\S' "$backlog_file" 2>/dev/null \
        | { if [[ -n "$id_filter" ]]; then grep -E "$id_filter" || true; else cat; fi; } \
        | sed -E 's/^\s*-\s+\[[^]]*\]\s+([^[:space:]]+).*/\1/')" || true
    [[ -n "$_ids" ]] || return 0

    local tasks_dir
    if ! tasks_dir="$(pp_tasks_dir "$project_name" 2>/dev/null)" || [[ -z "$tasks_dir" ]]; then
        # Same fallback as _disc_task_terminal: no resolvable tasks dir
        # means no folder to read for any entry.
        local _id
        while IFS= read -r _id; do
            [[ -n "$_id" ]] && printf '%s\tNO-FOLDER\n' "$_id"
        done <<< "$_ids"
        return 0
    fi
    printf '%s\n' "$_ids" | pp_run_ops pgai_agent_kanban.discovery.items task-states "$tasks_dir"
}

# _disc_chain_idle <project_name>
#
# Returns 0 (true) when the entire RC chain is idle, meaning discovery may
# scan and write (bundle, queue a PM-decompose task). Returns 1 (false, the
# chain is busy) and emits a log line naming the project, agent, and task --
# or the Active RC -- that makes it busy. discovery_run_pipeline calls this
# before any scan, so a busy chain idle-exits without scanning, and calls it
# again under the repo lock before its first write, so a chain that turned
# busy during the unlocked scan is never written into.
#
# The chain is idle when ALL of the following hold:
#   1. Active RC in release-state.md is "none"
#   2. EVERY task entry named in each of the named agent backlogs resolves
#      (via _disc_task_terminal, i.e. that task's own status.md) to DONE or
#      WONT-DO — or the backlog has no task entries at all, which counts as
#      idle.
#
# Agent backlogs checked: pm, cm, coder, tester, writer (missing backlog
# file counts as idle for that agent — no tasks assigned yet).
#
# This decides from status.md, never from the queue marker. A marker left
# stale by an interrupted wake ([ ], [W], [A] — anything but [x]) must not
# idle-block this gate when the task it names has already reached a
# terminal status.md state; that is precisely the failure this rewrite
# closes (SOP: 'Per-Agent Queues and Marker Correctness' — "the marker is a
# hint, not ground truth").
#
# How many entries are consulted, and why: EVERY task entry in each
# backlog, not just the last line. The prior implementation checked only
# the tail entry, which is a second, independent bug hiding behind the
# marker read: a terminal tail says nothing about an earlier entry still
# mid-flight (e.g. two coder tasks queued back to back, the first still
# WORKING when the second task's line was appended below it — the old tail
# check would have missed the first task entirely). The gate is idle only
# when every entry named in every backlog resolves to a terminal state.
#
# NO-FOLDER entries do not block idle (treated the same as terminal, since
# there is nothing left to verify and no mechanism ever heals a missing
# folder); UNPARSEABLE entries do block idle. See _disc_task_terminal's
# header for the full rationale behind the split.
#
# Usage:
#   if _disc_chain_idle "$project_name"; then
#       # safe to queue PM
#   fi
_disc_chain_idle() {
    local project_name="$1"
    project_name="$(pp_require_project_context "${project_name:-}")" || return 1

    # Check 1: Active RC must be "none"
    local active_rc
    active_rc="$(_disc_get_release_field "$project_name" "Active RC" 2>/dev/null || echo "none")"
    if [[ -n "$active_rc" && "$active_rc" != "none" ]]; then
        log "discovery_run_pipeline [${project_name}]: chain busy — Active RC=${active_rc}"
        return 1
    fi

    # Check 2: every task entry named in every agent backlog must resolve to
    # a terminal status.md state. Use pp_queue_path for each agent so queue
    # paths resolve correctly; each backlog is read in one batched call.
    local _backlog_agents=("pm" "cm" "coder" "tester" "writer")
    local _agent _backlog_file _task_id _task_state

    for _agent in "${_backlog_agents[@]}"; do
        _backlog_file="$(pp_queue_path "$project_name" "$_agent" 2>/dev/null)" || continue
        [[ -f "$_backlog_file" ]] || continue

        while IFS=$'\t' read -r _task_id _task_state; do
            [[ -z "$_task_id" ]] && continue
            case "$_task_state" in
                DONE|WONT-DO)
                    continue
                    ;;
                NO-FOLDER)
                    log "discovery_run_pipeline [${project_name}]: NOTE — ${_agent}_backlog names ${_task_id} with no task folder; treating as terminal (does not block idle)"
                    continue
                    ;;
                UNPARSEABLE|"")
                    log "discovery_run_pipeline [${project_name}]: chain busy — ${_agent}_backlog names ${_task_id} with unparseable status.md"
                    return 1
                    ;;
                *)
                    log "discovery_run_pipeline [${project_name}]: chain busy — ${_agent}_backlog task ${_task_id} is ${_task_state}"
                    return 1
                    ;;
            esac
        done < <(_disc_backlog_task_states "$project_name" "$_backlog_file")
    done

    # All checks passed — chain is idle
    return 0
}

# _disc_pm_ticket_in_flight <project_name>
#
# Returns 0 (true) when pm_backlog.md names at least one decompose-v* entry
# whose own status.md has not reached a terminal state (DONE or WONT-DO).
# Returns 1 (false) when every decompose-v* entry is terminal, or the
# backlog does not exist, or it has no matching entries.
#
# This decides from status.md, never from the queue marker, for the same
# reason _disc_chain_idle above does: a marker left stale by an interrupted
# wake must not be read as "still pending" once the task it names has
# actually finished. Either state (queued or actively worked) means an RC
# is conceptually in flight even if Active RC in release-state.md has not
# yet been set (i.e. CM-open-rc has not fired yet).
#
# Pattern rationale (kept from the prior implementation, unchanged): only
# entries whose task ID contains "decompose-v" followed by a digit (e.g.
# "decompose-v0-21-40-") are matched. This is conservative — it avoids
# false-positives on hypothetical non-decompose PM tickets. The pattern
# matches both dot-format (future) and hyphen-format (current pm-agent.sh
# output) by anchoring on "decompose-v" and a digit. Unlike the prior
# implementation, ALL entries matching this pattern are inspected regardless
# of their queue marker — the marker no longer participates in the decision
# at all, only in identifying which lines to inspect.
#
# NO-FOLDER entries are treated the same as terminal (not in-flight) — same
# reasoning as _disc_chain_idle above: nothing is left to verify, and no
# mechanism ever heals a missing task folder, so treating it as in-flight
# would wedge RC-opening forever. UNPARSEABLE entries are treated as
# in-flight (blocking) — the folder and status.md both exist, so this is
# active corruption worth a hard stop. See _disc_task_terminal's header for
# the full rationale behind the split.
#
# Usage:
#   if _disc_pm_ticket_in_flight "$project_name"; then
#       # PM ticket pending; exit before Steps 1/2/3
#   fi
_disc_pm_ticket_in_flight() {
    local project_name="$1"
    project_name="$(pp_require_project_context "${project_name:-}")" || return 1

    local pm_backlog_file
    pm_backlog_file="$(pp_queue_path "$project_name" "pm" 2>/dev/null)" || return 1

    # No backlog file → nothing in flight
    [[ -f "$pm_backlog_file" ]] || return 1

    local _task_id _task_state
    while IFS=$'\t' read -r _task_id _task_state; do
        [[ -z "$_task_id" ]] && continue
        case "$_task_state" in
            DONE|WONT-DO)
                continue
                ;;
            NO-FOLDER)
                log "discovery_run_pipeline [${project_name}]: NOTE — pm decompose ticket ${_task_id} has no task folder; treating as terminal (not in flight)"
                continue
                ;;
            *)
                log "discovery_run_pipeline [${project_name}]: pm decompose ticket ${_task_id} is ${_task_state:-UNPARSEABLE} (RC opening soon)"
                return 0  # in-flight (includes UNPARSEABLE — active corruption, conservative)
                ;;
        esac
    done < <(_disc_backlog_task_states "$project_name" "$pm_backlog_file" 'decompose-v[0-9]')

    return 1  # every decompose-v* entry is terminal, or there are none
}

# _disc_git_tag_exists <tag> [dev_tree] — check both local and origin tags
# Resolution order for the git working directory:
#   1. Explicit dev_tree argument (passed by discovery_compute_next_patch)
#   2. $PGAI_DEV_TREE_PATH environment variable (for callers that set it directly)
#   3. cwd-based fallback (preserves test behavior when neither is set)
#
# Using an explicit dev_tree keeps bump-around consistent with
# pp_last_released_version, which reads its dev tree from project.cfg (or PROJECT.cfg).
_disc_git_tag_exists() {
    local tag="$1"
    local dev_tree="${2:-${PGAI_DEV_TREE_PATH:-}}"

    # Local-only guard: PGAI_GIT_LOCAL_ONLY, checked BEFORE the ls-remote
    # fallthrough in both branches below (dev-tree path and cwd fallback).
    # When set to any non-empty value, local tags are authoritative and the
    # function returns without ever invoking `git ls-remote`. Two callers
    # set it: render-path scripts (dashboards) export it so a refresh never
    # reaches the network, and the wake layer
    # (team/scripts/lib/wake_guard_wiring.sh) exports it for every
    # dispatched role other than "cm", so agent-side discovery under a
    # non-CM role is local-only by design -- CM is the only role that
    # touches origin, and the dev tree's own tags are what CM tags and
    # pushes from. Left unset (a CM dispatch, an operator's own shell),
    # behavior is unchanged: a tag not found locally still falls through to
    # the origin check.
    #
    # Each git listing feeds grep through process substitution, not a pipe:
    # callers run under `set -euo pipefail`, where grep -q exiting on its
    # first match could SIGPIPE a git still writing and turn a found tag into
    # "not found". A process substitution's exit status never reaches the
    # verdict, which is grep's alone.
    local _local_only="${PGAI_GIT_LOCAL_ONLY:-}"

    # Prefer dev tree if set and valid
    if [[ -n "$dev_tree" && -d "$dev_tree/.git" ]]; then
        grep -qx "$tag" < <(git -C "$dev_tree" tag -l "$tag" 2>/dev/null) && return 0
        [[ -n "$_local_only" ]] && return 1
        # Also check origin (cheap if refs already fetched recently)
        grep -q "refs/tags/$tag" < <(git -C "$dev_tree" ls-remote --tags origin "refs/tags/$tag" 2>/dev/null) && return 0
        return 1
    fi

    # Fallback: cwd-based check (preserves behavior when dev tree is not configured)
    if git rev-parse --git-dir >/dev/null 2>&1; then
        grep -qx "$tag" < <(git tag -l "$tag" 2>/dev/null) && return 0
        [[ -n "$_local_only" ]] && return 1
        grep -q "refs/tags/$tag" < <(git ls-remote --tags origin "refs/tags/$tag" 2>/dev/null) && return 0
    fi
    return 1
}
