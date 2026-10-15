#!/usr/bin/env bash
# team/scripts/lib/cm/release_campaign_spend.sh
#
# Ship-time campaign-file spend step: keeps the second half of a
# DEFER-LOW-BUGS promise. A campaign file is armed by team/scripts/defer.sh
# with an until=vX.Y.Z target; the operator's promise is that deferral ends
# when a release at or past that target ships. This file is where CM makes
# good on that promise — the resolver (team/scripts/lib/defer_posture.sh)
# only ever reads the campaign file, and discovery never touches it either;
# this is the one and only place a campaign file is removed.
#
# Public entry point
# -------------------
#   cm_release_spend_campaign_file <project_name> <shipped_version> \
#       <project_root> <release_state_path>
#
#     Called from team/scripts/lib/cm/release_finalize.sh's Step 12, after
#     release-state.md has been rewritten for this ship (and after the
#     auto-resolve / operator-override audit appends, so this step's own
#     append lands after them, matching arrival order). Reads ONLY the
#     project-scoped campaign file at <project_root>/DEFER-LOW-BUGS — a
#     root-scoped $KANBAN_ROOT/DEFER-LOW-BUGS is never touched here, because
#     it speaks for every project that has no file of its own and a single
#     project's release does not get to spend a promise made fleet-wide.
#
#     Comparison is semver_gte(shipped_version, until_target) — see
#     team/scripts/lib/semver.sh — never a string equality: a campaign file
#     with until=v2.14.0 is spent by v2.14.0, v2.14.1, or any higher
#     version, the same latching >= reasoning HALT-AFTER's rc:vX.Y.Z drain
#     uses (docs/cm-release-reference.md, "HALT-AFTER in full").
#
#     Outcomes:
#       - No campaign file present: no-op, one narration line.
#       - Campaign file present but unparseable / malformed until=: leaves
#         the file in place, WARNs to stderr, and returns 0. A malformed
#         file is a finding an operator should look at, not a reason to
#         fail an already-tagged release.
#       - shipped_version < until target: leaves the file in place, one
#         narration line naming the target still in force (never silent).
#       - shipped_version >= until target: removes the file and appends a
#         "## Campaign Spend Event" audit entry to release_state_path,
#         mirroring the shape and placement of the HALT-AFTER auto-promotion
#         entry (team/pgai_agent_kanban/halt_after/promote.py) — same
#         "## <Noun> Event" heading, same UTC ISO-8601 Timestamp bullet,
#         same append-only append. After the file is gone, the resolver
#         naturally falls through to the project.cfg / kanban.cfg tiers on
#         its very next call — no code in this file changes what the
#         resolver does; removing the file IS the behavior change.
#
#     Never fails the release: every failure path inside this function
#     (rm failure, audit-append failure, an unexpected error) is caught,
#     logged as a WARNING, and the function still returns 0. The release
#     has already tagged by the time this step runs (see release_finalize.sh
#     Step 13 above Step 12's positioning in call order) — a spend problem
#     is a finding, never a rollback.
#
# Dependencies
# ------------
#   team/scripts/lib/semver.sh — semver_gte (sourced below if not already
#   loaded).

# Guard against double-sourcing.
if declare -F cm_release_spend_campaign_file >/dev/null 2>&1; then
    return 0
fi

if ! command -v semver_gte >/dev/null 2>&1; then
    # shellcheck source=../semver.sh
    source "$(dirname "${BASH_SOURCE[0]}")/../semver.sh"
fi

# ---------------------------------------------------------------------------
# _cm_release_campaign_spend_parse_until <file>
#
# Echoes the campaign file's until= value on success. Returns 1 (echoing
# nothing) when the file has no parseable 'until=' line or the value is not
# well-formed vX.Y.Z semver — the caller treats either case as a finding,
# not a refusal, because by ship time the release has already tagged.
# ---------------------------------------------------------------------------
_cm_release_campaign_spend_parse_until() {
    local file="$1"
    local until_line
    until_line="$(grep -E '^[[:space:]]*until[[:space:]]*=' "$file" 2>/dev/null | head -n1)"
    if [[ -z "$until_line" ]]; then
        return 1
    fi

    local value
    value="$(printf '%s\n' "$until_line" | sed -E 's/^[[:space:]]*until[[:space:]]*=[[:space:]]*//; s/[[:space:]]*$//')"
    if ! [[ "$value" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        return 1
    fi

    printf '%s' "$value"
    return 0
}

# ---------------------------------------------------------------------------
# _cm_release_campaign_spend_append_audit <release_state_path> <project_name> \
#     <shipped_version> <until_target>
#
# Appends the "## Campaign Spend Event" block. Returns 1 without writing
# anything when release_state_path does not exist — the caller logs a
# WARNING and continues rather than treating this as fatal.
# ---------------------------------------------------------------------------
_cm_release_campaign_spend_append_audit() {
    local release_state="$1" project_name="$2" shipped_version="$3" until_target="$4"

    if [[ ! -f "$release_state" ]]; then
        return 1
    fi

    local ts
    ts="$(date -u +"%Y-%m-%dT%H:%M:%S+00:00")"

    cat >> "$release_state" <<EOF

## Campaign Spend Event
- Timestamp: ${ts}
- Trigger: DEFER-LOW-BUGS campaign spend at ship of ${shipped_version}
- Until target satisfied: ${until_target}
- Resulting posture: resolves from project.cfg / kanban.cfg tiers (DEFER-LOW-BUGS removed)
EOF
    return 0
}

# ---------------------------------------------------------------------------
# cm_release_spend_campaign_file <project_name> <shipped_version> \
#     <project_root> <release_state_path>
# See the file header for the full contract.
# ---------------------------------------------------------------------------
cm_release_spend_campaign_file() {
    local project_name="$1"
    local shipped_version="$2"
    local project_root="$3"
    local release_state="$4"

    local campaign_file="${project_root}/DEFER-LOW-BUGS"

    if [[ ! -f "$campaign_file" ]]; then
        echo "[cm-release] Campaign spend: no project-scoped DEFER-LOW-BUGS at ${campaign_file} — nothing to spend."
        return 0
    fi

    local until_target
    if ! until_target="$(_cm_release_campaign_spend_parse_until "$campaign_file")"; then
        echo "[cm-release] WARNING: campaign spend: ${campaign_file} has no parseable 'until=' line, or its value is not well-formed vX.Y.Z semver — leaving the file in place. This is a finding for the operator, not a release failure." >&2
        return 0
    fi

    if semver_gte "$shipped_version" "$until_target"; then
        if ! rm -f "$campaign_file"; then
            echo "[cm-release] WARNING: campaign spend: failed to remove ${campaign_file} after ${shipped_version} satisfied until=${until_target} — leaving it as a finding; release continues." >&2
            return 0
        fi
        echo "[cm-release] Campaign spend: DEFER-LOW-BUGS at ${campaign_file} removed — ${shipped_version} satisfies until=${until_target}; deferral posture now resumes falling through to the project.cfg / kanban.cfg tiers with no operator action."
        if ! _cm_release_campaign_spend_append_audit "$release_state" "$project_name" "$shipped_version" "$until_target"; then
            echo "[cm-release] WARNING: campaign spend: could not append the audit entry to ${release_state} (file not found)." >&2
        fi
    else
        echo "[cm-release] Campaign spend: DEFER-LOW-BUGS at ${campaign_file} still in force — ${shipped_version} has not yet reached until=${until_target}."
    fi

    return 0
}
