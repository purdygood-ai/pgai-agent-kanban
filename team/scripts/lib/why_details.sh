#!/usr/bin/env bash
# team/scripts/lib/why_details.sh
#
# Rendering helpers for the two sections of team/scripts/why.sh's --details
# pane that are not part of the lane verdict: the project's most recent
# discovery/gate refusal and the install's last docker use. The verdict
# line and the locks, queue tails, show-queues --details and posture
# sections are rendered by resolve_verdicts in
# team/pgai_agent_kanban/ops/verdict.py, the function GET /verdicts also
# calls. why.sh stays a thin CLI surface (parse args, call these, print)
# per docs/coding-standards.md directive 2.
#
# Each render function is READ-ONLY: no file is created, edited, or
# removed by anything in this file. Every function echoes an EXPLICIT
# empty-state line when it has nothing to report -- a section that prints
# nothing when it has nothing is indistinguishable from a section that
# failed to run, which is the same silent-skip failure the deep-verify
# tier's no-silent-skips rule exists to prevent (team/SOP-core.md).
#
# Public API
# ----------
#   why_details_render_discovery_skip <project>
#       The project's most recent discovery/gate refusal, read from the
#       refusal ledger (team/scripts/lib/refusal_ledger.sh) -- never
#       re-derived by re-running discovery, and never grepped from the
#       wake log. Echoes an explicit empty-state line when the project has
#       never recorded a refusal. Returns 1 (no output) when the ledger
#       file exists but is malformed.
#
#       Display-only filter: the ledger holds exactly one
#       most-recent record, so a record that did not come from the
#       install's own operation -- a pytest fixture's sandboxed lock,
#       most concretely -- would otherwise dilute or hide the install's
#       real last refusal outright. A record failing either check below
#       is treated exactly like "never recorded": the empty-state line
#       above prints, naming neither the skipped record nor its path.
#       The ledger file itself is never rewritten, trimmed, or deleted by
#       this filter -- it is a rendering decision only.
#         - context == "test" (stamped by the writer from
#           PYTEST_CURRENT_TEST) is always skipped.
#         - independently of context (so a record written before that
#           field existed is still caught): any absolute-path token
#           found in the record's surface, message, or remedy text that
#           does not resolve under PGAI_AGENT_KANBAN_ROOT_PATH skips the
#           record too -- a lock or file-guard refusal embeds the exact
#           path it refused on directly in its message
#           (team/scripts/lib/lock_provenance.sh's own refusal strings),
#           so this needs no extra field to work. A record naming no
#           absolute path at all is accepted on this check alone --
#           absence of a path is not evidence a record is test-scoped.
#       See _why_details_refusal_is_install_scoped below.
#
#   why_details_render_docker_use
#       The last line of the install's docker-use log
#       ($PGAI_AGENT_KANBAN_ROOT_PATH/logs/docker-use.log, written by
#       team/scripts/lib/docker_use.sh): who last reached for a container
#       engine, for which script and images, and with what outcome. The log
#       is install-wide, not per project. Echoes an explicit empty-state
#       line when no docker use has been recorded.
#
# Dependencies (must already be sourced by the caller, or are sourced
# defensively below when missing)
# -----------------------------------------------------------------------
#   team/scripts/lib/refusal_ledger.sh   — refusal_ledger_read_latest
#   team/scripts/lib/docker_use.sh       — docker_use_last_line, docker_use_log_path
# KANBAN_ROOT must be exported (every dependency above requires it).

# Include guard: safe to source multiple times (only loads once).
[[ -n "${_PGAI_WHY_DETAILS_SH_LOADED:-}" ]] && return 0
_PGAI_WHY_DETAILS_SH_LOADED=1

_WHY_DETAILS_SH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! declare -F refusal_ledger_read_latest >/dev/null 2>&1; then
    # shellcheck source=refusal_ledger.sh
    source "${_WHY_DETAILS_SH_DIR}/refusal_ledger.sh"
fi
if ! declare -F docker_use_last_line >/dev/null 2>&1; then
    # shellcheck source=docker_use.sh
    source "${_WHY_DETAILS_SH_DIR}/docker_use.sh"
fi

# ---------------------------------------------------------------------------
# _why_details_refusal_is_install_scoped <context> <surface> <message> <remedy>
# Belt-and-braces filter deciding whether a ledger record came from the
# install's own operation. See why_details_render_discovery_skip's
# "Public API" entry above for the full rationale; this is the predicate
# alone, kept separate so each half (context, path) is independently
# testable and neither is buried in the render function's control flow.
#
# Returns 0 (accept -- render it) or 1 (reject -- render nothing, exactly
# as though no refusal were ever recorded). Read-only: never touches the
# ledger file, only judges the fields already read from it.
# ---------------------------------------------------------------------------
_why_details_refusal_is_install_scoped() {
    local context="$1" surface="$2" message="$3" remedy="$4"

    # Belt: a record the writer itself stamped as test-context is always
    # rejected, full stop -- this is the strong signal the ledger writer
    # derives from PYTEST_CURRENT_TEST, so no path check can override it.
    [[ "${context}" == "test" ]] && return 1

    # Braces: independent of context, so a record written before the
    # context field existed ("unknown") is still caught. The kanban root
    # comes from PGAI_AGENT_KANBAN_ROOT_PATH only -- never a hardcoded
    # path, and never the legacy KANBAN_ROOT bash alias, matching
    # record_refusal()'s own resolution choice in refusal_ledger.py.
    local kanban_root="${PGAI_AGENT_KANBAN_ROOT_PATH:-}"
    [[ -z "${kanban_root}" ]] && return 0

    # Whitespace-delimited tokens only -- a token must itself START with
    # "/" to count as an absolute-path subject. Deliberately NOT a bare
    # '/' grep-anywhere: a relative path like "projects/demo/bugs/" (a
    # perfectly ordinary thing for a discovery refusal's remedy to name)
    # contains an embedded "/" that is not the start of the token, and
    # matching it as though it were an absolute path outside the kanban
    # root would misfire on ordinary relative-path remedies.
    local -a words
    read -ra words <<< "${surface} ${message} ${remedy}"
    local w
    for w in "${words[@]}"; do
        [[ "${w}" == /* ]] || continue
        case "${w}" in
            "${kanban_root}"/*|"${kanban_root}")
                ;;
            *)
                return 1
                ;;
        esac
    done

    return 0
}

# ---------------------------------------------------------------------------
# why_details_render_discovery_skip <project>
# See "Public API" above.
# ---------------------------------------------------------------------------
why_details_render_discovery_skip() {
    local project="$1"

    local latest
    latest="$(refusal_ledger_read_latest "${project}")" || return 1

    local -A fields=()
    while IFS= read -r line; do
        [[ -z "${line}" ]] && continue
        local key="${line%%=*}"
        local val="${line#*=}"
        fields["${key}"]="${val}"
    done <<< "${latest}"

    if [[ "${fields[found]:-false}" != "true" ]]; then
        echo "no discovery/gate refusal recorded for this project"
        return 0
    fi

    if ! _why_details_refusal_is_install_scoped \
        "${fields[context]:-unknown}" "${fields[surface]:-}" \
        "${fields[message]:-}" "${fields[remedy]:-}"; then
        echo "no discovery/gate refusal recorded for this project"
        return 0
    fi

    printf 'most recent refusal: %s\n' "${fields[timestamp]:-unknown}"
    printf '  surface: %s\n' "${fields[surface]:-unknown}"
    printf '  error: %s\n' "${fields[error]:-unknown}"
    printf '  message: %s\n' "${fields[message]:-unknown}"
    printf '  remedy: %s\n' "${fields[remedy]:-unknown}"
    return 0
}

# ---------------------------------------------------------------------------
# why_details_render_docker_use
# See "Public API" above.
# ---------------------------------------------------------------------------
why_details_render_docker_use() {
    local last
    last="$(docker_use_last_line)"
    if [[ -z "${last}" ]]; then
        printf 'no docker use recorded (%s)\n' "$(docker_use_log_path)"
        return 0
    fi
    printf 'last docker use: %s\n' "${last}"
    return 0
}
