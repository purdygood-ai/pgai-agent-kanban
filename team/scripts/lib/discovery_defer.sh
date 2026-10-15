#!/usr/bin/env bash
# team/scripts/lib/discovery_defer.sh
#
# Discovery bug-step deferral gate: the founding rule that a deferred bug
# never opens an RC. This file answers three separate, individually
# testable questions -- kept apart on purpose so a later ticket that
# carries deferred bugs along with a triggering bug (the piggyback half of
# this release's deferral behaviour) can reuse the severity decision and
# the queue-emptiness check without unpicking the bug-step orchestration
# below them:
#
#   "what severity did the operator write on this bug file?"
#                          -> _disc_bug_severity (pure I/O, no judgment)
#   "is THIS bug, at its own severity, held back under the resolved
#    posture?"             -> _disc_severity_defer_decision (pure, no I/O)
#   "is there nothing else left in the requirements queue for the chain to
#    do, so the hygiene sweep may bundle the litter?"
#                          -> _disc_requirements_queue_empty
#   "given this tick's candidate bug files, which ones does
#    discovery_step_bugs actually bundle?"
#                          -> _disc_bug_deferral_filter (the orchestration;
#                             calls the three functions above)
#
# Sourced by team/scripts/lib/discovery.sh; consumed by discovery_step_bugs
# in discovery_steps.sh.
#
# Public entry points
# --------------------
#   _disc_bug_severity <bug_file>
#       Reads the bug file's own "**Severity:**" field (BUG-TEMPLATE.md) and
#       echoes its raw value, or the empty string when the field is absent
#       or the file cannot be read. Read-only; never judges the value.
#   _disc_severity_defer_decision <severity_raw> <resolved_level>
#       Pure decision function -- no file I/O. Echoes exactly one of
#       "deferred" | "kept" | "unparseable" to stdout and always returns 0
#       (a query, not a guard; the caller decides what to do with the
#       answer). "At or below the level" is the rule: deferred when the
#       severity's rank is <= the resolved level's rank on the
#       low < medium < high < critical scale (_DISC_SEVERITY_VOCAB below is
#       the one stated home for that ordering -- see _disc_severity_rank).
#       critical ranks strictly above high, so it can never be deferred at
#       any of the three settable deferred_bug_level values (low|medium|
#       high -- critical is deliberately not among them; see
#       _disc_severity_defer_decision's own comment on the level check).
#       A severity that is not exactly one of low/medium/high/critical
#       (case-insensitive) -- empty or a typo -- resolves to "unparseable":
#       never silently swallowed into "deferred", so it always remains a
#       candidate that can trigger an RC. Callers narrate this case naming
#       the file (see _disc_bug_deferral_filter).
#   _disc_requirements_queue_empty <project_name>
#       Returns 0 when the requirements queue holds no unprocessed
#       requirement (nothing eligible, or every eligible file is either
#       ceiling-blocked, auto-invalid or superseded, none of which Step 3
#       can ever act on). Returns 1 when at least one file is real pending
#       or in-flight work, and when a version ceiling is unreadable (its
#       accessor narrates it). Eligibility -- and with it each file's
#       "## Status", read through the canonical state reader so a legacy
#       "superseded" reads as "wont-do" -- is
#       pgai_agent_kanban.discovery.eligibility's answer; a
#       "*.SUPERSEDED-on-cancel-*" name (a bundle an RC unwind superseded)
#       is closed whatever its "## Status" says. Read-only: this function never queues PM, never mutates a
#       bundle's ## Status, and never writes to bug_backlog.md or
#       priority_backlog.md. This is what lets discovery_step_bugs answer
#       "is there nothing else for the chain to do" without duplicating
#       discovery_step_requirements's own eligibility scan or racing it.
#   _disc_bug_deferral_filter <project_name> <candidate_files>
#       Resolves the deferral posture via defer_posture.sh's
#       resolve_defer_posture, then partitions the newline-separated
#       <candidate_files> into deferred and kept sets using the two
#       functions above. Echoes the newline-separated set of files
#       discovery_step_bugs should actually bundle THIS TICK:
#         - deferred_bugs=false (or unresolvable): echoes <candidate_files>
#           back unchanged -- today's behaviour, no deferral line narrated.
#         - deferred_bugs=true and at least one candidate is kept (severity
#           above the level, or unparseable) AND defer_piggyback=false (or
#           there is nothing deferred to carry): echoes only the kept
#           files. The deferred files are left completely alone -- not
#           part of the echoed set, so the caller never marks or bundles
#           them.
#         - deferred_bugs=true, at least one candidate is kept, AND
#           defer_piggyback=true AND at least one candidate is deferred:
#           the RC is opening anyway (a kept item triggered it), so every
#           currently-deferred candidate rides along for free. Echoes the
#           kept files unchanged, plus each deferred file with a trailing
#           tab and a "RIDE_ALONG:<trigger-id>" marker -- the internal
#           wire format _disc_write_bundle's caller-side loop and
#           _disc_order_bundle_items both understand (path\tannotation;
#           a line with no tab is an ordinary, unannotated item). Every
#           ride-along in one call names the SAME trigger id: the first
#           kept candidate encountered in <candidate_files> order (a
#           deferred bug never becomes a trigger, and multiple simultaneous
#           triggers are not ranked -- one bundle, one
#           trigger, by construction). Narrates
#           "piggybacking N deferred bugs (trigger: <trigger-id> <severity>)"
#           once, naming the count and the chosen trigger.
#         - deferred_bugs=true and every candidate is deferred: echoes the
#           deferred set ONLY when _disc_requirements_queue_empty also
#           holds (the hygiene sweep firing); otherwise echoes nothing, so
#           the caller bundles nothing this tick and every deferred bug
#           stays untouched. A deferred bug never becomes a trigger, so the
#           piggyback carry never applies here -- there is no kept item to
#           ride along on.
#       Every deferral decision narrates via the caller's log() shim: a run
#       that defers bugs states the count and the resolved level; a run
#       that lets every bug through says nothing new. Returns 1 only when
#       resolve_defer_posture itself refuses (a malformed campaign file or
#       an out-of-range cfg value) -- the caller treats that as "nothing to
#       bundle this tick", matching how the pipeline treats every other
#       upstream refusal it cannot safely paper over.
#
# Dependencies (must already be sourced/defined by the caller before this
# file's functions are called)
# -----------------------------------------------------------------------
#   team/scripts/lib/project_paths.sh — pp_require_project_context,
#     pp_requirements_dir, pp_last_released_version, pp_max_minor,
#     pp_max_major, pp_max_patch, pp_workflow_type.
#   team/scripts/lib/workflow.sh — wf_load_plugin, wf_version_semantics.
#   team/scripts/lib/discovery_eligibility.sh — _disc_list_all_eligible_requirements.
#   team/scripts/lib/discovery.sh itself — the log() shim.
#
# This file sources team/scripts/lib/defer_posture.sh itself (guarded, like
# defer_posture.sh sources its own ini_parser.sh/error_contract.sh
# dependencies) because resolve_defer_posture -- the one resolver for "what
# is the deferral posture right now" -- is not part of discovery.sh's
# existing sourcing chain, and every caller of discovery.sh (pm-agent.sh,
# wake/claude.sh, wake/codex.sh, tests) should not have to remember to
# source it separately just because discovery_step_bugs now consumes it.
#
# python3 must be on PATH (_disc_bug_severity reads the field through
# pgai_agent_kanban.discovery.items severity, via pp_run_ops, self-sourced
# below).

# Guard against double-sourcing.
if declare -F _disc_bug_deferral_filter >/dev/null 2>&1; then
    return 0
fi

_DISC_DEFER_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! declare -F pp_run_ops >/dev/null 2>&1; then
    # shellcheck source=pp_run_ops.sh
    source "${_DISC_DEFER_LIB_DIR}/pp_run_ops.sh"
fi
if ! declare -F resolve_defer_posture >/dev/null 2>&1; then
    # shellcheck source=defer_posture.sh
    source "${_DISC_DEFER_LIB_DIR}/defer_posture.sh"
fi
unset _DISC_DEFER_LIB_DIR

# ---------------------------------------------------------------------------
# _disc_bug_severity <bug_file>
# See "Public entry points" above.
#
# The regex (_SEVERITY_RE in pgai_agent_kanban.discovery.items) is kept
# byte-identical to _extract_severity's own
# "\*\*Severity:\*\*\s*(.+)" pattern in team/pm-agent/lib/bug_scanner.py so
# the two severity readers in the tree can never disagree about what a bug
# file's severity field says.
# ---------------------------------------------------------------------------
_disc_bug_severity() {
    local bug_file="${1:-}"

    if [[ -z "$bug_file" || ! -r "$bug_file" ]]; then
        echo ""
        return 0
    fi

    pp_run_ops pgai_agent_kanban.discovery.items severity "$bug_file"
}

# ---------------------------------------------------------------------------
# _DISC_SEVERITY_VOCAB
# The single stated home for the severity vocabulary this file's deferral
# logic recognises, ordered ascending (weakest first) so array index + 1 is
# the rank. _disc_severity_rank is the only reader; every other consumer in
# this file goes through it rather than re-listing the words itself, so a
# future rung (or a reordering) is a one-line change in exactly one place.
#
# This is a strictly WIDER vocabulary than deferred_bug_level's own
# accepted set (low|medium|high -- validated by resolve_defer_posture in
# defer_posture.sh and by config_loader.sh's load_config). That is
# deliberate, not an oversight: deferred_bug_level names the highest rung
# an operator may configure the gate to WAIT ON, and critical -- the rung
# SOP-core.md's "Rating a Finding" defines as build-blocking -- must never
# be a settable wait target. A severity can rank higher than anything
# deferred_bug_level can be set to; that headroom is exactly what keeps a
# critical bug un-deferrable at every accepted level (see
# _disc_severity_defer_decision below).
# ---------------------------------------------------------------------------
_DISC_SEVERITY_VOCAB=(low medium high critical)

# ---------------------------------------------------------------------------
# _disc_severity_rank <value>
# Internal: echo the numeric rank (1=low, 2=medium, 3=high, 4=critical) for
# <value> (case-insensitive, whitespace- and trailing-period-tolerant),
# looked up in _DISC_SEVERITY_VOCAB above. Returns 1 (nothing echoed) when
# <value> is not one of the recognised rungs. Not part of the public API --
# _disc_severity_defer_decision is the entry point every caller outside
# this file uses.
# ---------------------------------------------------------------------------
_disc_severity_rank() {
    local norm="${1,,}"
    # Trim surrounding whitespace and a trailing period -- forgiving of
    # "high." or " high " without trying to be a general text normaliser.
    norm="${norm#"${norm%%[![:space:]]*}"}"
    norm="${norm%"${norm##*[![:space:]]}"}"
    norm="${norm%.}"

    local _i
    for _i in "${!_DISC_SEVERITY_VOCAB[@]}"; do
        if [[ "$norm" == "${_DISC_SEVERITY_VOCAB[$_i]}" ]]; then
            echo $((_i + 1))
            return 0
        fi
    done
    return 1
}

# ---------------------------------------------------------------------------
# _disc_severity_defer_decision <severity_raw> <resolved_level>
# See "Public entry points" above.
# ---------------------------------------------------------------------------
_disc_severity_defer_decision() {
    local severity_raw="${1:-}"
    local level="${2:-}"

    local sev_rank level_rank
    sev_rank="$(_disc_severity_rank "$severity_raw")" || {
        echo "unparseable"
        return 0
    }
    # The resolved level always comes from resolve_defer_posture, which
    # validates it to exactly low|medium|high (rank 1-3; critical is
    # deliberately not a settable level -- see _DISC_SEVERITY_VOCAB above)
    # -- but never trust a value blindly inside a decision function that is
    # also unit-tested directly with arbitrary strings. An unrecognised
    # level is the same "cannot judge this" shape as an unrecognised
    # severity: never silently resolved into a guessed comparison. Because
    # critical always ranks 4 and no valid level ever exceeds rank 3, a
    # critical severity is "kept" against every accepted level by
    # construction -- there is no level value that defers it.
    level_rank="$(_disc_severity_rank "$level")" || {
        echo "unparseable"
        return 0
    }

    if (( sev_rank <= level_rank )); then
        echo "deferred"
    else
        echo "kept"
    fi
    return 0
}

# ---------------------------------------------------------------------------
# _disc_requirements_queue_empty <project_name>
# See "Public entry points" above.
# ---------------------------------------------------------------------------
_disc_requirements_queue_empty() {
    local project_name="$1"
    project_name="$(pp_require_project_context "${project_name:-}")" || return 1

    local requirements_dir last_released
    requirements_dir="$(pp_requirements_dir "$project_name")"
    last_released="$(pp_last_released_version "$project_name" 2>/dev/null || echo "v0.0.0")"
    [[ -z "$last_released" || "$last_released" == "none" ]] && last_released="v0.0.0"

    # No requirements/ directory at all -- nothing pending by construction.
    [[ -d "$requirements_dir" ]] || return 0

    # Ceilings exactly as Step 3 reads them (a number, or empty for `none`).
    # A malformed value: the accessor's one stderr line is left visible and
    # the queue is reported not empty -- nothing is concluded from a scan
    # whose ceilings cannot be read.
    local max_minor max_major max_patch _ceiling_unreadable=0
    max_minor="$(pp_max_minor "$project_name")" || _ceiling_unreadable=1
    max_major="$(pp_max_major "$project_name")" || _ceiling_unreadable=1
    max_patch="$(pp_max_patch "$project_name")" || _ceiling_unreadable=1
    (( _ceiling_unreadable )) && return 1

    # Resolve version_semantics the same way discovery_step_requirements
    # does, so this read-only check and Step 3's own eligibility scan can
    # never disagree about what "eligible" means for this project.
    local _wf_type _version_semantics
    _wf_type="$(pp_workflow_type "$project_name" 2>/dev/null)" || _wf_type="release"
    local _wf_load_exit=0
    set +e
    wf_load_plugin "$_wf_type"
    _wf_load_exit=$?
    set -e
    if [[ $_wf_load_exit -ne 0 ]]; then
        log "_disc_requirements_queue_empty: WARNING cannot load workflow plugin '${_wf_type}': ${WF_LOAD_ERROR}; defaulting to semver eligibility"
        _version_semantics="semver"
    else
        _version_semantics="$(wf_version_semantics)"
    fi

    local all_eligible
    all_eligible="$(_disc_list_all_eligible_requirements "$requirements_dir" "$last_released" "$max_minor" "$max_major" "$max_patch" "$_version_semantics")"

    [[ -z "$all_eligible" ]] && return 0

    # Ceiling-blocked and auto-invalid entries can never be picked up by
    # Step 3 -- it skips them forever with a named reason -- so they are
    # not "something else for the chain to do." Neither is a superseded
    # bundle: an unwind renamed it and closed it, even when the file
    # still reads "running". Any other survivor (explicit-eligible, or an
    # auto-sentinel awaiting version resolution) is real
    # unprocessed-or-in-flight work: the queue is not empty.
    while IFS= read -r file_raw; do
        [[ -z "$file_raw" ]] && continue
        if [[ "$file_raw" == *:ceiling-blocked:* || "$file_raw" == *:ceiling-blocked || "$file_raw" == *:auto-invalid ]]; then
            continue
        fi
        if [[ "$file_raw" == *.SUPERSEDED-on-cancel-* ]]; then
            continue
        fi
        return 1
    done <<< "$all_eligible"

    return 0
}

# ---------------------------------------------------------------------------
# _disc_bug_deferral_filter <project_name> <candidate_files>
# See "Public entry points" above.
#
# <candidate_files> is the newline-separated list of unhandled bug file
# paths discovery_step_bugs already found via _disc_find_unhandled_items --
# this function never re-scans bugs/ itself.
# ---------------------------------------------------------------------------
_disc_bug_deferral_filter() {
    local project_name="$1"
    local candidates="$2"

    local posture
    posture="$(resolve_defer_posture "$project_name" 2>&1)" || {
        log "discovery_step_bugs: ERROR resolve_defer_posture failed: ${posture}"
        return 1
    }

    local deferred_bugs="" deferred_level="" defer_piggyback=""
    while IFS= read -r _line; do
        [[ -z "$_line" ]] && continue
        local _field="${_line%%=*}"
        local _rest="${_line#*=}"
        case "$_field" in
            deferred_bugs) deferred_bugs="${_rest%%|*}" ;;
            deferred_bug_level) deferred_level="${_rest%%|*}" ;;
            defer_piggyback) defer_piggyback="${_rest%%|*}" ;;
        esac
    done <<< "$posture"
    deferred_bugs="${deferred_bugs:-false}"
    deferred_level="${deferred_level:-low}"
    defer_piggyback="${defer_piggyback:-true}"

    # Off: today's behaviour, unchanged, no deferral line narrated. This is
    # the "narration carries no deferral line" acceptance criterion -- the
    # severity field is never even read when deferral is off.
    if [[ "$deferred_bugs" != "true" ]]; then
        printf '%s\n' "$candidates"
        return 0
    fi

    local kept="" deferred="" deferred_count=0
    local trigger_file="" trigger_severity=""
    while IFS= read -r _f; do
        [[ -z "$_f" ]] && continue
        local _sev _decision
        _sev="$(_disc_bug_severity "$_f")"
        _decision="$(_disc_severity_defer_decision "$_sev" "$deferred_level")"
        case "$_decision" in
            deferred)
                deferred="${deferred:+${deferred}$'\n'}${_f}"
                deferred_count=$((deferred_count + 1))
                ;;
            unparseable)
                log "discovery_step_bugs: WARNING severity field in $(basename "$_f") is missing or unparseable ('${_sev}'); treated as above the deferral level so it remains a candidate"
                kept="${kept:+${kept}$'\n'}${_f}"
                [[ -z "$trigger_file" ]] && trigger_file="$_f" && trigger_severity="$_sev"
                ;;
            *)
                kept="${kept:+${kept}$'\n'}${_f}"
                [[ -z "$trigger_file" ]] && trigger_file="$_f" && trigger_severity="$_sev"
                ;;
        esac
    done <<< "$candidates"

    if [[ "$deferred_count" -gt 0 ]]; then
        log "discovery_step_bugs: deferring ${deferred_count} bug(s) at or below level=${deferred_level}"
    fi

    # At least one candidate survives the gate at this severity level -- a
    # trigger exists. Bundle it; whether the deferred ones ride along too
    # depends on defer_piggyback.
    if [[ -n "$kept" ]]; then
        # Piggyback carry: the RC is opening anyway because "$trigger_file"
        # (the first kept/above-level candidate encountered) triggered it,
        # so with defer_piggyback=true every currently-deferred candidate
        # rides along for free rather than waiting for its own hygiene
        # sweep. Each ride-along is annotated "path\tRIDE_ALONG:<trigger-id>"
        # -- the wire format _disc_write_bundle's item-rendering loop and
        # _disc_order_bundle_items both recognise -- so the bundle writer
        # can render the exact ride-along suffix team/roles/PM.md specifies
        # on that item's own ## Bundled Items line. A deferred bug never
        # becomes a trigger: the id named below always comes from the kept
        # set. With defer_piggyback=false the deferred candidates are left
        # completely alone.
        if [[ "$defer_piggyback" == "true" && "$deferred_count" -gt 0 ]]; then
            local trigger_id
            trigger_id="$(basename "${trigger_file%.md}")"
            local trigger_severity_trimmed="$trigger_severity"
            trigger_severity_trimmed="${trigger_severity_trimmed#"${trigger_severity_trimmed%%[![:space:]]*}"}"
            trigger_severity_trimmed="${trigger_severity_trimmed%"${trigger_severity_trimmed##*[![:space:]]}"}"

            log "discovery_step_bugs: piggybacking ${deferred_count} deferred bugs (trigger: ${trigger_id} ${trigger_severity_trimmed})"

            printf '%s\n' "$kept"
            while IFS= read -r _d; do
                [[ -z "$_d" ]] && continue
                printf '%s\tRIDE_ALONG:%s\n' "$_d" "$trigger_id"
            done <<< "$deferred"
            return 0
        fi

        printf '%s\n' "$kept"
        return 0
    fi

    # Nothing was a candidate in the first place (deferred_count is 0 and
    # kept is empty means <candidates> was itself empty) -- nothing to echo.
    if [[ "$deferred_count" -eq 0 ]]; then
        return 0
    fi

    # Every candidate is deferred. The hygiene sweep bundles the held-back
    # litter, but only when there is truly nothing else for the chain to
    # do -- the requirements queue must also hold no unprocessed
    # requirement. Deferral sets the tempo down; it does not set it to
    # zero.
    if _disc_requirements_queue_empty "$project_name"; then
        log "discovery_step_bugs: hygiene sweep -- bug queue and requirements queue both empty; bundling ${deferred_count} deferred bug(s) held at level=${deferred_level}"
        printf '%s\n' "$deferred"
        return 0
    fi

    log "discovery_step_bugs: ${deferred_count} bug(s) remain deferred at level=${deferred_level}; requirements queue not empty, hygiene sweep withheld"
    return 0
}
