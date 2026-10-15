#!/usr/bin/env bash
# team/scripts/lib/defer_posture.sh
#
# Single resolver for the deferral posture in force right now: every
# consumer (discovery, CM, the dashboard, the why verb) calls this instead
# of re-deriving the precedence itself, so no two callers can disagree.
#
# Precedence (highest wins): DEFER-LOW-BUGS campaign file > project.cfg
# [release] > kanban.cfg [release] > built-in default.
#
# Usage
# -----
#   source "$(dirname "${BASH_SOURCE[0]}")/defer_posture.sh"
#   resolve_defer_posture [project_name]
#
# Public API
# ----------
#   resolve_defer_posture [project_name]
#     Prints five lines to stdout, one per resolved field, each shaped
#     "<field>=<value>|<tier>":
#
#       deferred_bugs=<true|false>|<tier>
#       deferred_bug_level=<low|medium|high>|<tier>
#       defer_piggyback=<true|false>|<tier>
#       until=<vX.Y.Z or empty>|<tier>
#       campaign_file=<absolute path or empty>|<tier>
#
#     <tier> is one of "campaign file", "project.cfg", "kanban.cfg",
#     "default" (the value came from the built-in default), or — for
#     "until" and "campaign_file" only, when no campaign file is in force —
#     "none".
#
#     campaign_file names the exact file the campaign-file tier resolved
#     from (project-scoped or root-scoped — see the precedence note in
#     resolve_defer_posture below), empty when no campaign file is in
#     force. It shares "until"'s tier by construction: both are set from
#     the one campaign file this resolution consulted, or from neither.
#     Added for the why verb (why.sh), which must name the file a live
#     campaign resolved from without re-deriving the project/root lookup
#     itself.
#
#     <project_name> is optional; when omitted it falls back to
#     PGAI_PROJECT_NAME, and when that is also unset the resolver runs in
#     root-only mode (no project.cfg tier, no project-scoped campaign file
#     — only the root-scoped campaign file, kanban.cfg, and the default).
#
#     Returns 0 on success. Returns 1 and emits a dual-channel refusal to
#     stderr — never a partial or fallback resolution — when:
#       - a campaign file is present but its body has no parseable
#         'until=' line, or its until= value is not well-formed vX.Y.Z
#         semver;
#       - a [release] value actually consulted at the project.cfg or
#         kanban.cfg tier is not in that key's accepted set.
#     A value present at a tier that is never consulted (for example, an
#     invalid deferred_bug_level sitting in kanban.cfg while a campaign
#     file already forces the level) is not read and cannot cause a
#     refusal — only the tier the precedence chain actually lands on is
#     validated.
#
#     On a return-1 refusal, the global DEFER_POSTURE_LAST_ERROR_FILE names
#     the exact file whose content caused it (the campaign file, project.cfg,
#     or kanban.cfg — whichever validator actually refused). resolve_defer_posture
#     clears it to empty at the start of every call, so a caller that checks
#     it immediately after a failed call always reads the file responsible for
#     THAT call, never a stale value from an earlier one. Added for callers
#     that must show the offending path on a refusal without re-deriving
#     which tier failed themselves — see
#     team/scripts/dashboard/lib/defer_scope.sh, the dashboard's
#     unparseable-flag fallback.
#
#   Read-only: this resolver never writes, edits, or removes the campaign
#   file, and never mutates project.cfg or kanban.cfg. Writing the
#   campaign file and spending it at release time are separate tickets.
#
# Design note — what the campaign file overrides
# ------------------------------------------------
# A DEFER-LOW-BUGS campaign is, by its name, a campaign to defer LOW bugs
# specifically. Its presence therefore forces exactly the two keys that
# name is about: deferred_bugs=true (deferral is in force) and
# deferred_bug_level=low (only low-severity bugs wait — the same meaning
# deferred_bug_level=low already carries outside a campaign), both
# reported with tier "campaign file", regardless of what project.cfg or
# kanban.cfg say for either key. defer_piggyback governs consolidation
# behavior, not which bugs are deferred, so it is untouched by the
# campaign and always resolves through the project.cfg / kanban.cfg /
# default chain on its own.
#
# Key Registry cross-reference
# -----------------------------
# The three keys' accepted values, defaults, and boolean-strictness
# doctrine are documented once in team/scripts/lib/config_loader.sh's
# "[release]" Key Registry — this file does not repeat that registry, but
# its own refusals (below) echo the same accepted-set wording so an
# operator reading either refusal sees the same answer to "what values are
# right".

# ---------------------------------------------------------------------------
# Bootstrap: ensure read_ini is available.
# ---------------------------------------------------------------------------
if ! command -v read_ini >/dev/null 2>&1; then
    # shellcheck source=ini_parser.sh
    source "$(dirname "${BASH_SOURCE[0]}")/ini_parser.sh"
fi

# ---------------------------------------------------------------------------
# Bootstrap: ensure the shared dual-channel error-contract emitter is
# available. Every refusal below speaks through pgai_emit_error_contract
# rather than a bare "echo ... >&2" — see team/SOP-core.md ("Gates and
# Refusals") for why every new gate must.
# ---------------------------------------------------------------------------
if ! command -v pgai_emit_error_contract >/dev/null 2>&1; then
    # shellcheck source=error_contract.sh
    source "$(dirname "${BASH_SOURCE[0]}")/error_contract.sh"
fi

# ---------------------------------------------------------------------------
# Bootstrap: ensure pgai_mktemp is available (used by _defer_posture_capture
# below to avoid a subshell on the internal helper calls resolve_defer_posture
# makes).
# ---------------------------------------------------------------------------
if ! command -v pgai_mktemp >/dev/null 2>&1; then
    # shellcheck source=temp.sh
    source "$(dirname "${BASH_SOURCE[0]}")/temp.sh"
fi

# ---------------------------------------------------------------------------
# _defer_posture_capture <out_var> -- <command...>
#
# Runs <command...> directly in the current shell — never inside a $(...)
# command substitution, which would fork a subshell — and stores its
# trimmed-of-nothing stdout into the caller's variable named <out_var>.
# Returns <command...>'s own exit status unchanged.
#
# Why this exists: resolve_defer_posture's own internal helper calls
# (_defer_posture_parse_campaign_file, _defer_posture_resolve_tiered_value)
# set DEFER_POSTURE_LAST_ERROR_FILE on a refusal. A caller that captured
# their output the ordinary way — value="$(helper ...)" — would fork a
# subshell for the capture, and a subshell's write to a global never reaches
# back out to the parent shell once the subshell exits: the global would
# read as empty every time, silently breaking the very side channel
# DEFER_POSTURE_LAST_ERROR_FILE exists to provide. Routing every internal
# capture through this helper instead keeps the call in the current shell,
# so the global is reliably visible to resolve_defer_posture's own caller
# immediately after a refusal.
# ---------------------------------------------------------------------------
_defer_posture_capture() {
    local __dpc_out_var="$1"
    shift
    local __dpc_tmp
    __dpc_tmp="$(pgai_mktemp defer_posture_capture)"
    "$@" >"$__dpc_tmp"
    local __dpc_rc=$?
    printf -v "$__dpc_out_var" '%s' "$(cat "$__dpc_tmp" 2>/dev/null)"
    rm -f "$__dpc_tmp"
    return $__dpc_rc
}

# ---------------------------------------------------------------------------
# _defer_posture_validate_boolean <key> <value> <cfg>
#
# Validates a true/false-only [release] key (deferred_bugs, defer_piggyback).
# 'on'/'off' are deliberately not accepted spellings — matching the same
# boolean-strictness doctrine team/scripts/lib/config_loader.sh applies to
# these same keys when read from kanban.cfg.
#
# Every refusal here is BLOCK-WORTHY (docs/blocking-site-census.md): an
# unrecognized value cannot be silently resolved into a posture nobody
# asked for — the same invariant-by-design reasoning CENSUS-ANCHOR
# config-loader-deferred-bugs-01 already applies to this key's kanban.cfg
# reading.
# ---------------------------------------------------------------------------
_defer_posture_validate_boolean() {
    local key="$1" value="$2" cfg="$3"
    case "$value" in
        true|false) return 0 ;;
    esac
    DEFER_POSTURE_LAST_ERROR_FILE="$cfg"
    pgai_emit_error_contract \
        "defer_posture.invalid_${key}" \
        "defer_posture: resolve_defer_posture: [release] ${key}='${value}' in ${cfg} is not an accepted value." \
        "Set [release] ${key} to 'true' or 'false' in ${cfg} (accepted: true|false; 'on'/'off' are not accepted spellings)."
    # CENSUS-ANCHOR: defer-posture-invalid-boolean-01
    return 1
}

# ---------------------------------------------------------------------------
# _defer_posture_validate_level <value> <cfg>
#
# Validates the deferred_bug_level [release] key (low|medium|high).
#
# 'critical' is deliberately excluded from this accepted set, not merely
# unimplemented: critical is a severity rung discovery_defer.sh's own
# _disc_severity_rank ranks above high (see _DISC_SEVERITY_VOCAB in that
# file), but deferred_bug_level names the highest rung an operator may
# configure the deferral gate to WAIT ON, and critical is the rung
# SOP-core.md's "Rating a Finding" defines as build-blocking. Letting an
# operator set deferred_bug_level=critical would let the gate be
# configured to defer the one class of bug that must never wait. The two
# vocabularies overlap on low|medium|high and diverge on purpose above it.
#
# BLOCK-WORTHY (docs/blocking-site-census.md): same invariant-by-design
# reasoning as the boolean validator above — an unrecognized severity word
# is refused rather than guessed.
# ---------------------------------------------------------------------------
_defer_posture_validate_level() {
    local value="$1" cfg="$2"
    case "$value" in
        low|medium|high) return 0 ;;
    esac
    DEFER_POSTURE_LAST_ERROR_FILE="$cfg"
    pgai_emit_error_contract \
        "defer_posture.invalid_deferred_bug_level" \
        "defer_posture: resolve_defer_posture: [release] deferred_bug_level='${value}' in ${cfg} is not an accepted value." \
        "Set [release] deferred_bug_level to one of low|medium|high in ${cfg}."
    # CENSUS-ANCHOR: defer-posture-invalid-level-01
    return 1
}

# ---------------------------------------------------------------------------
# _defer_posture_validate <key> <value> <cfg>
# Dispatches to the boolean or level validator by key name.
# ---------------------------------------------------------------------------
_defer_posture_validate() {
    local key="$1" value="$2" cfg="$3"
    case "$key" in
        deferred_bugs|defer_piggyback)
            _defer_posture_validate_boolean "$key" "$value" "$cfg"
            ;;
        deferred_bug_level)
            _defer_posture_validate_level "$value" "$cfg"
            ;;
        *)
            echo "defer_posture: _defer_posture_validate: unknown key '${key}'" >&2
            return 1
            ;;
    esac
}

# ---------------------------------------------------------------------------
# _defer_posture_resolve_tiered_value <key> <project_cfg> <kanban_cfg> <default>
#
# Walks the project.cfg > kanban.cfg > default chain for one [release] key.
# project_cfg and/or kanban_cfg may be empty strings (no project context, or
# the file does not exist) — read_ini treats a missing/absent path as "key
# absent" and returns the empty default, so no existence check is needed
# here.
#
# Echoes "<value>|<tier>" on success. On an invalid value at whichever tier
# actually supplies it, the validator above emits the refusal and this
# function returns 1 without echoing anything — a lower tier is never
# consulted once a higher tier's value fails validation.
# ---------------------------------------------------------------------------
_defer_posture_resolve_tiered_value() {
    local key="$1" project_cfg="$2" kanban_cfg="$3" default_value="$4"
    local value

    value="$(read_ini "$project_cfg" release "$key" "")"
    if [[ -n "$value" ]]; then
        _defer_posture_validate "$key" "$value" "$project_cfg" || return 1
        printf '%s|project.cfg' "$value"
        return 0
    fi

    value="$(read_ini "$kanban_cfg" release "$key" "")"
    if [[ -n "$value" ]]; then
        _defer_posture_validate "$key" "$value" "$kanban_cfg" || return 1
        printf '%s|kanban.cfg' "$value"
        return 0
    fi

    printf '%s|default' "$default_value"
    return 0
}

# ---------------------------------------------------------------------------
# _defer_posture_parse_campaign_file <file>
#
# Parses a DEFER-LOW-BUGS campaign file's body for its 'until=' line and
# validates the value is well-formed vX.Y.Z semver. Echoes the until value
# on success.
#
# The brief's 'never silently off' rule: a campaign file that cannot be
# parsed at all, or whose until= value is malformed, refuses loud rather
# than falling through to the cfg tiers (which would resolve to whatever
# they say — possibly deferral-off, the exact failure mode the brief
# names). Both refusals below are BLOCK-WORTHY (docs/blocking-site-census.md):
# an unparseable campaign file is an invariant-by-design case — the whole
# point of the file is to force deferral on, so silently ignoring it is
# worse than refusing.
# ---------------------------------------------------------------------------
_defer_posture_parse_campaign_file() {
    local file="$1"
    local until_line
    until_line="$(grep -E '^[[:space:]]*until[[:space:]]*=' "$file" 2>/dev/null | head -n1)"
    if [[ -z "$until_line" ]]; then
        DEFER_POSTURE_LAST_ERROR_FILE="$file"
        pgai_emit_error_contract \
            "defer_posture.campaign_unparseable" \
            "defer_posture: resolve_defer_posture: campaign file ${file} has no parseable 'until=' line." \
            "Add a line reading 'until=vX.Y.Z' to ${file}, naming the release the deferral campaign ends at."
        # CENSUS-ANCHOR: defer-posture-campaign-unparseable-01
        return 1
    fi

    local value
    value="$(printf '%s\n' "$until_line" | sed -E 's/^[[:space:]]*until[[:space:]]*=[[:space:]]*//; s/[[:space:]]*$//')"
    if ! [[ "$value" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        DEFER_POSTURE_LAST_ERROR_FILE="$file"
        pgai_emit_error_contract \
            "defer_posture.campaign_malformed_until" \
            "defer_posture: resolve_defer_posture: campaign file ${file} until='${value}' is not well-formed semver." \
            "Set 'until=' in ${file} to a well-formed vX.Y.Z version (got '${value}')."
        # CENSUS-ANCHOR: defer-posture-campaign-malformed-until-01
        return 1
    fi

    printf '%s' "$value"
    return 0
}

# ---------------------------------------------------------------------------
# resolve_defer_posture [project_name]
# See the file header for the full contract.
# ---------------------------------------------------------------------------
resolve_defer_posture() {
    local project="${1:-${PGAI_PROJECT_NAME:-}}"

    # Cleared on every call so a caller reading it after a failed call never
    # sees a stale path left over from an earlier, unrelated failure — see
    # the "DEFER_POSTURE_LAST_ERROR_FILE" contract in the file header above.
    DEFER_POSTURE_LAST_ERROR_FILE=""

    if [[ -z "${KANBAN_ROOT:-}" ]]; then
        echo "defer_posture: resolve_defer_posture: KANBAN_ROOT is not set" >&2
        return 1
    fi

    local kanban_cfg="${KANBAN_ROOT}/kanban.cfg"
    local project_cfg="" project_campaign=""
    if [[ -n "$project" ]]; then
        project_cfg="${KANBAN_ROOT}/projects/${project}/project.cfg"
        project_campaign="${KANBAN_ROOT}/projects/${project}/DEFER-LOW-BUGS"
    fi
    local root_campaign="${KANBAN_ROOT}/DEFER-LOW-BUGS"

    # Campaign-file tier: project-scoped file wins over the root-scoped one
    # (matching the HALT family idiom already in the tree).
    local campaign_file=""
    if [[ -n "$project_campaign" && -f "$project_campaign" ]]; then
        campaign_file="$project_campaign"
    elif [[ -f "$root_campaign" ]]; then
        campaign_file="$root_campaign"
    fi

    local until_value="" until_tier="none"
    local forced_deferred_bugs="" forced_level=""
    if [[ -n "$campaign_file" ]]; then
        _defer_posture_capture until_value _defer_posture_parse_campaign_file "$campaign_file" || return 1
        until_tier="campaign file"
        forced_deferred_bugs="true"
        forced_level="low"
    fi

    local resolved=""
    local deferred_bugs="" deferred_bugs_tier=""
    if [[ -n "$forced_deferred_bugs" ]]; then
        deferred_bugs="$forced_deferred_bugs"
        deferred_bugs_tier="campaign file"
    else
        _defer_posture_capture resolved _defer_posture_resolve_tiered_value deferred_bugs "$project_cfg" "$kanban_cfg" "false" || return 1
        deferred_bugs="${resolved%%|*}"
        deferred_bugs_tier="${resolved#*|}"
    fi

    local deferred_bug_level="" deferred_bug_level_tier=""
    if [[ -n "$forced_level" ]]; then
        deferred_bug_level="$forced_level"
        deferred_bug_level_tier="campaign file"
    else
        _defer_posture_capture resolved _defer_posture_resolve_tiered_value deferred_bug_level "$project_cfg" "$kanban_cfg" "low" || return 1
        deferred_bug_level="${resolved%%|*}"
        deferred_bug_level_tier="${resolved#*|}"
    fi

    # defer_piggyback governs consolidation, not which bugs are deferred —
    # the campaign file never touches it; always resolve through the cfg
    # chain, campaign in force or not.
    _defer_posture_capture resolved _defer_posture_resolve_tiered_value defer_piggyback "$project_cfg" "$kanban_cfg" "true" || return 1
    local defer_piggyback="${resolved%%|*}"
    local defer_piggyback_tier="${resolved#*|}"

    printf 'deferred_bugs=%s|%s\n' "$deferred_bugs" "$deferred_bugs_tier"
    printf 'deferred_bug_level=%s|%s\n' "$deferred_bug_level" "$deferred_bug_level_tier"
    printf 'defer_piggyback=%s|%s\n' "$defer_piggyback" "$defer_piggyback_tier"
    printf 'until=%s|%s\n' "$until_value" "$until_tier"
    printf 'campaign_file=%s|%s\n' "$campaign_file" "$until_tier"
    return 0
}
