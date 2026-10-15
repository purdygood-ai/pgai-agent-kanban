#!/usr/bin/env bash
# team/scripts/migrations/migrate_v2.22.5_to_v2.23.0.sh
#
# Seeds the commented `overwatch_resolve` [release] key into every
# already-registered project's project.cfg, so an existing install ships
# the same dark-by-default OVERWATCH switch that create-project.sh writes
# into a brand-new project.cfg (see team/scripts/lib/overwatch_resolve_seed.sh,
# the single source of truth for the wording both paths emit).
#
# What this migration does:
#   - For each targeted project.cfg, checks whether an `overwatch_resolve`
#     line (commented or not, any value) already exists ANYWHERE in the
#     file.
#   - If absent: inserts the commented documentation block + the commented
#     `# overwatch_resolve = false` line at the end of the [release]
#     section (creating a bare [release] section at end-of-file first if
#     the cfg has none at all — an older cfg that skipped every
#     intervening migration may lack one, so that case is handled rather
#     than assumed away).
#   - If present: makes NO change to that project.cfg. This covers both an
#     operator who already set the key by hand and a project.cfg that was
#     already migrated — the second run (or the tenth) is a byte-identical
#     no-op.
#
# Contract (team/scripts/migrations/README.md):
#   - Additive:      only ever adds lines; never removes or reorders any
#                     existing line.
#   - Non-destructive: an already-present overwatch_resolve line (commented
#                     or an operator-set true/false) is left untouched,
#                     byte-for-byte -- including the case where the
#                     operator's value is uncommented/true.
#   - Idempotent:     running this script N times against the same
#                     project.cfg produces the same file N=1 produced.
#
# Usage:
#   migrate_v2.22.5_to_v2.23.0.sh                    # every registered project
#   migrate_v2.22.5_to_v2.23.0.sh --project <name>   # one project only
#   migrate_v2.22.5_to_v2.23.0.sh --dry-run          # preview only, no writes
#   migrate_v2.22.5_to_v2.23.0.sh --project <name> --dry-run
#
# Exit codes:
#   0 — success (including "nothing to do" no-ops, and an empty registry
#       for the all-projects mode)
#   1 — usage error
#   2 — a named project (--project) has no project.cfg on disk
#   3 — KANBAN_ROOT not set or not found

set -euo pipefail
# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
if [[ ! -d "$KANBAN_ROOT" ]]; then
    echo "ERROR: kanban root not found: $KANBAN_ROOT" >&2
    echo "Set PGAI_AGENT_KANBAN_ROOT_PATH or run install.sh" >&2
    exit 3
fi

_MIG_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/operator_args.sh
source "${_MIG_SCRIPT_DIR}/../lib/operator_args.sh"
# shellcheck source=../lib/projects.sh
source "${_MIG_SCRIPT_DIR}/../lib/projects.sh"
# shellcheck source=../lib/project_paths.sh
source "${_MIG_SCRIPT_DIR}/../lib/project_paths.sh"
# shellcheck source=../lib/overwatch_resolve_seed.sh
source "${_MIG_SCRIPT_DIR}/../lib/overwatch_resolve_seed.sh"
unset _MIG_SCRIPT_DIR

OPERATOR_VALID_FLAGS=(project dry-run help)

_USAGE_TEXT="$(operator_args_render_help_for_flags "migrate_v2.22.5_to_v2.23.0.sh" \
    "Seed the commented overwatch_resolve [release] key into project.cfg (one project or every registered project)." \
    OPERATOR_VALID_FLAGS)"

operator_args_argparse --value-flags "project" -- "$@"

if argparse_missing "project"; then
    echo "ERROR: --project requires a value" >&2
    exit 1
fi

if argparse_has "help" || argparse_has "h"; then
    printf '%s\n' "$_USAGE_TEXT" >&2
    exit 0
fi

if [[ "${#ARGPARSE_POSITIONAL[@]}" -gt 0 ]]; then
    echo "ERROR: unexpected positional argument: ${ARGPARSE_POSITIONAL[0]}" >&2
    printf '%s\n' "$_USAGE_TEXT" >&2
    exit 1
fi

operator_args_validate_known "$(basename "$0")" OPERATOR_VALID_FLAGS || exit 1

DRY_RUN="false"
argparse_has "dry-run" && DRY_RUN="true"
SINGLE_PROJECT="$(operator_args_project)"

# ---------------------------------------------------------------------------
# _already_seeded <cfg_file>
# True (0) if an overwatch_resolve line -- commented or not, any value --
# already exists anywhere in the file. That is the whole idempotency test:
# any prior line, however it got there (a previous migration run, or an
# operator's own edit), means this script has nothing to add.
# ---------------------------------------------------------------------------
_already_seeded() {
    local cfg="$1"
    grep -qE '^[[:space:]]*#?[[:space:]]*overwatch_resolve[[:space:]]*=' "$cfg"
}

# ---------------------------------------------------------------------------
# _seed_project_cfg <cfg_file>
# Inserts the commented overwatch_resolve block at the end of the
# [release] section (or appends a fresh [release] section at EOF when the
# cfg has none). Writes via a temp file + mv so a mid-write failure never
# leaves a half-written project.cfg on disk.
# ---------------------------------------------------------------------------
_seed_project_cfg() {
    local cfg="$1"
    local block
    block="$(overwatch_resolve_seed_comment_block)"

    local release_line
    release_line="$(grep -n '^\[release\]' "$cfg" | head -n1 | cut -d: -f1 || true)"

    local tmp
    tmp="$(mktemp "${cfg}.XXXXXXXX")"

    if [[ -z "$release_line" ]]; then
        # No [release] section at all -- append a fresh one at EOF.
        cp "$cfg" "$tmp"
        if [[ -s "$tmp" ]]; then
            local last_byte
            last_byte="$(tail -c1 "$tmp" | od -An -tx1 | tr -d ' ')"
            [[ "$last_byte" != "0a" ]] && printf '\n' >> "$tmp"
        fi
        {
            printf '\n[release]\n\n'
            printf '%s\n' "$block"
        } >> "$tmp"
    else
        # Find the next section header strictly after the [release] line;
        # insert the block right before it (i.e. as the last content of
        # the [release] section). No next header -- [release] is the last
        # section in the file -- append at EOF instead.
        local next_rel_line
        next_rel_line="$(tail -n "+$((release_line + 1))" "$cfg" | grep -n '^\[' | head -n1 | cut -d: -f1 || true)"

        if [[ -n "$next_rel_line" ]]; then
            local insert_at=$((release_line + next_rel_line))
            head -n "$((insert_at - 1))" "$cfg" > "$tmp"
            printf '%s\n\n' "$block" >> "$tmp"
            tail -n "+${insert_at}" "$cfg" >> "$tmp"
        else
            cp "$cfg" "$tmp"
            if [[ -s "$tmp" ]]; then
                local last_byte2
                last_byte2="$(tail -c1 "$tmp" | od -An -tx1 | tr -d ' ')"
                [[ "$last_byte2" != "0a" ]] && printf '\n' >> "$tmp"
            fi
            printf '\n%s\n' "$block" >> "$tmp"
        fi
    fi

    mv "$tmp" "$cfg"
}

# ---------------------------------------------------------------------------
# _migrate_one <project_name>
# Returns 0 whether the project was seeded or already up to date. Returns 2
# only when the project's project.cfg is missing on disk.
# ---------------------------------------------------------------------------
_migrate_one() {
    local name="$1"
    local cfg="${KANBAN_ROOT}/projects/${name}/project.cfg"

    if [[ ! -f "$cfg" ]]; then
        echo "ERROR: project '${name}' not found -- no project.cfg at: ${cfg}" >&2
        return 2
    fi

    if _already_seeded "$cfg"; then
        echo "  [skip]  ${name}: overwatch_resolve already present in project.cfg -- no change"
        return 0
    fi

    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  [would-seed]  ${name}: would add commented overwatch_resolve block to ${cfg}"
        return 0
    fi

    _seed_project_cfg "$cfg"
    echo "  [seeded]  ${name}: added commented overwatch_resolve block to ${cfg}"
    return 0
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if [[ -n "$SINGLE_PROJECT" ]]; then
    SINGLE_PROJECT="$(pp_resolve_project_name "$SINGLE_PROJECT")" || exit 1
    echo "migrate_v2.22.5_to_v2.23.0.sh: seeding overwatch_resolve for project '${SINGLE_PROJECT}'"
    _migrate_one "$SINGLE_PROJECT"
    exit $?
fi

echo "migrate_v2.22.5_to_v2.23.0.sh: seeding overwatch_resolve for every registered project"
_MIG_SAW_PROJECT=0
_MIG_EXIT=0
while IFS= read -r _mig_project; do
    [[ -z "$_mig_project" ]] && continue
    _MIG_SAW_PROJECT=1
    _migrate_one "$_mig_project" || _MIG_EXIT=$?
done < <(projects_cfg_list 2>/dev/null || true)

if [[ "$_MIG_SAW_PROJECT" -eq 0 ]]; then
    echo "migrate_v2.22.5_to_v2.23.0.sh: no registered projects found (see $(projects_cfg_path)) -- nothing to seed"
fi

exit "$_MIG_EXIT"
