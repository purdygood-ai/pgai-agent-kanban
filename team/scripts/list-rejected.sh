#!/usr/bin/env bash
# team/scripts/list-rejected.sh
#
# Inventory of quarantined files across all projects (or a single project).
#
# For each project that has a non-empty priority/.rejected/ or bugs/.rejected/
# directory, prints one section with the quarantined filename(s), their
# quarantine timestamp (mtime), and the rejection reason (derived from pattern
# check; falls back to "unknown" for unexpected filename shapes).
#
# Output format (one section per directory with quarantined files):
#
#   project=<name> dir=priority
#     PRIORITY-NNNN-example.md  (quarantined 2026-05-17T12:31)
#     Reason: filename does not match expected pattern (no YYYYMMDD component)
#
#   project=<name> dir=bugs
#     (none)
#
#   To recover: scripts/recover-rejected.sh --project <name> --file <filename> [--rename NEW]
#
# Usage:
#   list-rejected.sh [--project <name>]
#
# Options:
#   --project NAME   Show only the named project (default: all projects)
#   --help, -h       Print this help and exit
#
# Environment:
#   PGAI_AGENT_KANBAN_ROOT_PATH — kanban root (default: $HOME/pgai_agent_kanban)
#   NO_COLOR=1 — suppress ANSI codes

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source operator_args.sh for parsing and uniform --help.
# shellcheck source=lib/operator_args.sh
source "${SCRIPT_DIR}/lib/operator_args.sh"

# ---------------------------------------------------------------------------
# Resolve kanban root
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"

# Declared flag vocabulary: all flags this command accepts.
OPERATOR_VALID_FLAGS=(project help h)

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
# Value-taking: project.
# Boolean: help.
argparse_parse \
    --value-flags "project" \
    -- "$@"

# Emit error for value-taking flags given with no value.
if argparse_missing "project"; then
    echo "list-rejected.sh: error: --project requires a value" >&2
    exit 1
fi

# Handle --help / -h.
if argparse_has "help" || argparse_has "h"; then
    operator_args_render_help_for_flags "list-rejected.sh" \
        "Inventory of quarantined files across all projects (or a single project)." \
        OPERATOR_VALID_FLAGS
    exit 0
fi

# Reject unexpected positional arguments.
if [[ "${#ARGPARSE_POSITIONAL[@]}" -gt 0 ]]; then
    echo "list-rejected.sh: error: unexpected positional argument: ${ARGPARSE_POSITIONAL[0]}" >&2
    echo "Usage: list-rejected.sh [--project <name>]" >&2
    exit 1
fi

# Reject any flag not in the declared vocabulary.
operator_args_validate_known "list-rejected.sh" OPERATOR_VALID_FLAGS || exit 1

# Extract project filter.
# Use argparse_has to check explicit flag, not the env-var fallback from
# operator_args_project — list-rejected iterates all projects when no --project is given.
PROJECT_NAME=""
if argparse_has "project"; then
    PROJECT_NAME="${ARGPARSE_FLAGS[project]}"
fi

# Source: kanban.cfg [chain/paths] — INI format replaces legacy config.cfg
# Source ini_parser.sh for read_ini; dev_tree.sh for resolve/require helpers.
# shellcheck source=lib/ini_parser.sh
[[ -f "${SCRIPT_DIR}/lib/ini_parser.sh" ]] && source "${SCRIPT_DIR}/lib/ini_parser.sh"
# shellcheck source=lib/dev_tree.sh
source "${SCRIPT_DIR}/lib/dev_tree.sh"
if [[ -f "${KANBAN_ROOT}/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" chain pm_mode automatic)}"
fi
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"
# Classification (a): list-rejected only reads .rejected/ directories under
# $KANBAN_ROOT/projects/<name>; no dev tree access required.
# Global require_dev_tree removed (D5).

# ---------------------------------------------------------------------------
# ANSI support
# ---------------------------------------------------------------------------
USE_COLOR=true
if [[ "${TERM:-}" == "dumb" ]] || [[ "${NO_COLOR:-}" == "1" ]]; then
    USE_COLOR=false
fi

_ansi() {
    [[ "$USE_COLOR" != "true" ]] && echo "" && return
    case "$1" in
        red)    echo $'\033[0;31m' ;;
        yellow) echo $'\033[0;33m' ;;
        dim)    echo $'\033[2m'    ;;
        bold)   echo $'\033[1m'    ;;
        reset)  echo $'\033[0m'    ;;
        *)      echo ""            ;;
    esac
}

C_RED="$(_ansi red)"
C_YELLOW="$(_ansi yellow)"
C_DIM="$(_ansi dim)"
C_BOLD="$(_ansi bold)"
C_RESET="$(_ansi reset)"

# ---------------------------------------------------------------------------
# _rejection_reason <filename>
# Derive a human-readable reason from the filename pattern check.
# Mirrors the patterns validated by _disc_find_unhandled_items.
# ---------------------------------------------------------------------------
_rejection_reason() {
    local fname="$1"
    if [[ "$fname" == PRIORITY-* ]]; then
        if [[ "$fname" =~ ^PRIORITY-[0-9]{4,}-.+\.md$ ]]; then
            echo "pattern matches — unknown rejection reason (see logs)"
        else
            echo "filename does not match expected pattern (^PRIORITY-[0-9]{4,}-.+\\.md\$)"
        fi
    elif [[ "$fname" == BUG-* ]]; then
        if [[ "$fname" =~ ^BUG-[0-9]{4,}-.+\.md$ ]]; then
            echo "pattern matches — unknown rejection reason (see logs)"
        else
            echo "filename does not match expected pattern (^BUG-[0-9]{4,}-.+\\.md\$)"
        fi
    else
        echo "unknown prefix — unexpected file type in .rejected/"
    fi
}

# ---------------------------------------------------------------------------
# _quarantine_timestamp <filepath>
# Echo the file's mtime in ISO-8601 short form (YYYY-MM-DDTHH:MM).
# Falls back to "unknown" on stat failure.
# ---------------------------------------------------------------------------
_quarantine_timestamp() {
    local fpath="$1"
    # GNU stat (Linux): output is "YYYY-MM-DD HH:MM:SS.xxxxxxxxx +ZZZZ"
    if stat --version &>/dev/null 2>&1; then
        local ts
        ts="$(stat -c '%y' "$fpath" 2>/dev/null | sed 's/ /T/; s/\.[0-9]*//' | cut -c1-16 || true)"
        [[ -n "$ts" ]] && { echo "$ts"; return; }
    fi
    # BSD stat (macOS fallback)
    local ts
    ts="$(stat -f '%Sm' -t '%Y-%m-%dT%H:%M' "$fpath" 2>/dev/null || true)"
    [[ -n "$ts" ]] && { echo "$ts"; return; }
    echo "unknown"
}

# ---------------------------------------------------------------------------
# Main: iterate projects
# ---------------------------------------------------------------------------
projects_dir="${KANBAN_ROOT}/projects"
found_any=false

_scan_project() {
    local proj_name="$1"
    local proj_root="$2"
    local printed_project=false

    for dir_name in priority bugs; do
        local rejected_dir="${proj_root}/${dir_name}/.rejected"
        [[ -d "$rejected_dir" ]] || continue

        # Collect non-hidden regular files
        local files=()
        while IFS= read -r -d '' f; do
            files+=("$(basename "$f")")
        done < <(find "$rejected_dir" -maxdepth 1 -type f -not -name '.*' -print0 2>/dev/null | sort -z)

        if [[ "${#files[@]}" -eq 0 ]]; then
            continue
        fi

        found_any=true

        if [[ "$printed_project" == "false" ]]; then
            printed_project=true
        fi

        printf '%s%sproject=%s dir=%s%s\n' \
            "$C_BOLD" "$C_RED" "$proj_name" "$dir_name" "$C_RESET"

        for fname in "${files[@]}"; do
            local fpath="${rejected_dir}/${fname}"
            local ts
            ts="$(_quarantine_timestamp "$fpath")"
            local reason
            reason="$(_rejection_reason "$fname")"
            printf '  %s  (quarantined %s)\n' "$fname" "$ts"
            printf '  %sReason: %s%s\n' "$C_DIM" "$reason" "$C_RESET"
        done
        printf '\n'
    done
}

if [[ -d "$projects_dir" ]]; then
    if [[ -n "$PROJECT_NAME" ]]; then
        # Single project requested.
        proj_entry="${projects_dir}/${PROJECT_NAME}"
        if [[ ! -d "$proj_entry" ]]; then
            echo "list-rejected.sh: error: project '${PROJECT_NAME}' not found under ${projects_dir}" >&2
            exit 1
        fi
        _scan_project "$PROJECT_NAME" "$proj_entry"
    else
        # All projects.
        for proj_entry in "$projects_dir"/*/; do
            [[ -d "$proj_entry" ]] || continue
            proj_name="$(basename "$proj_entry")"
            [[ "$proj_name" == .* ]] && continue
            _scan_project "$proj_name" "$proj_entry"
        done
    fi
else
    # No projects/ directory: cannot determine project name without a hardcoded default.
    echo "ERROR: no project specified and none resolvable from projects.cfg" >&2
    echo "       projects/ directory not found under ${KANBAN_ROOT}." >&2
    echo "       Create \${KANBAN_ROOT}/projects/<name>/ or migrate to the multi-project layout." >&2
    exit 1
fi

if [[ "$found_any" == "false" ]]; then
    printf '%s(no quarantined files found across all projects)%s\n' "$C_DIM" "$C_RESET"
else
    printf '%sTo recover: %steam/scripts/recover-rejected.sh --project <name> --file <filename> [--rename NEW]%s\n' \
        "$C_DIM" "$C_BOLD" "$C_RESET"
fi
