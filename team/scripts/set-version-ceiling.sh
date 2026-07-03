#!/usr/bin/env bash
# team/scripts/set-version-ceiling.sh
#
# Operator helper to read, set, or remove max_minor / max_major ceilings in
# a project's PROJECT.cfg.  Edits are done in-place; all other fields are
# preserved verbatim.
#
# Usage:
#   set-version-ceiling.sh --project <name> --show
#   set-version-ceiling.sh --project <name> --minor <N>
#   set-version-ceiling.sh --project <name> --major <N>
#   set-version-ceiling.sh --project <name> --no-minor
#   set-version-ceiling.sh --project <name> --no-major
#   set-version-ceiling.sh --project <name> --minor <N> --major <N>
#   set-version-ceiling.sh --project <name> --no-minor --no-major
#
# Options:
#   --project NAME    Project name (required; or set $PGAI_PROJECT_NAME).
#   --show            Print current max_minor and max_major values, then exit.
#   --minor <N>       Set max_minor to the non-negative integer N.
#   --major <N>       Set max_major to the non-negative integer N.
#   --no-minor        Remove the max_minor field from PROJECT.cfg.
#   --no-major        Remove the max_major field from PROJECT.cfg.
#   --dry-run         Preview changes without writing them.
#   --help | -h       Show this help.
#
# Exit codes:
#   0 — success (or --show with no error)
#   1 — usage error, integer validation failure, or write failure
#   2 — project not found (PROJECT.cfg missing under KANBAN_ROOT/projects/<name>/)
#   3 — KANBAN_ROOT not set or not found

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve KANBAN_ROOT
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"

if [[ ! -d "$KANBAN_ROOT" ]]; then
    echo "ERROR: kanban root not found: $KANBAN_ROOT" >&2
    echo "Set PGAI_AGENT_KANBAN_ROOT_PATH or run install.sh" >&2
    exit 3
fi

# ---------------------------------------------------------------------------
# Source shared argument parser
# ---------------------------------------------------------------------------
_SVC_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/operator_args.sh
source "${_SVC_SCRIPT_DIR}/lib/operator_args.sh"
unset _SVC_SCRIPT_DIR

# Declared flag vocabulary: ALL flags this command accepts or consumes.
# Script-specific flags are included so operator_args_validate_known rejects
# any flag not in this list (pre-flip safety: consumed ⊆ declared).
OPERATOR_VALID_FLAGS=(project show minor major no-minor no-major dry-run help)

# ---------------------------------------------------------------------------
# Usage / help
# ---------------------------------------------------------------------------
usage() {
    # Note: OPERATOR_VALID_FLAGS includes all flags (canonical + script-specific)
    # so that operator_args_validate_known can reject any unlisted flag.
    # Script-specific flags appear in the flags block without built-in descriptions
    # (they show as --FLAG only); canonical flags use their built-in descriptions.
    operator_args_render_help_for_flags "set-version-ceiling.sh" \
        "Read, set, or remove max_minor / max_major ceilings in a project's project.cfg." \
        OPERATOR_VALID_FLAGS >&2
}

# ---------------------------------------------------------------------------
# Parse arguments
# Value-taking flags: project plus script-specific (minor, major).
# Boolean flags: show, no-minor, no-major, dry-run, help.
# ---------------------------------------------------------------------------
PROJECT=""
OPT_SHOW="false"
OPT_MINOR=""        # empty = no change; a number = set; "REMOVE" = --no-minor
OPT_MAJOR=""
DRY_RUN="false"

argparse_parse --value-flags "project minor major" -- "$@"

# Emit clear errors for value-taking flags given with no value.
if argparse_missing "minor"; then
    echo "ERROR: --minor requires a value" >&2
    exit 1
fi
if argparse_missing "major"; then
    echo "ERROR: --major requires a value" >&2
    exit 1
fi

# Handle --help / -h.
if argparse_has "help" || argparse_has "h"; then
    usage; exit 0
fi

# Reject unexpected positional arguments (hard-cut: no positional project name).
if [[ "${#ARGPARSE_POSITIONAL[@]}" -gt 0 ]]; then
    echo "ERROR: unexpected positional argument: ${ARGPARSE_POSITIONAL[0]}" >&2
    echo "Usage: set-version-ceiling.sh --project <name> --show|--minor <N>|--major <N>" >&2
    exit 1
fi

# Reject any flag not in the declared vocabulary.
operator_args_validate_known "$(basename "$0")" OPERATOR_VALID_FLAGS || exit 1

# Extract boolean flags.
if argparse_has "show";     then OPT_SHOW="true"; fi
if argparse_has "no-minor"; then OPT_MINOR="REMOVE"; fi
if argparse_has "no-major"; then OPT_MAJOR="REMOVE"; fi
if argparse_has "dry-run";  then DRY_RUN="true"; fi

# Extract value flags.
PROJECT="$(operator_args_project)"
# (only set if not already set to REMOVE by --no-minor/--no-major).
if argparse_has "minor" && [[ "$OPT_MINOR" != "REMOVE" ]]; then OPT_MINOR="${ARGPARSE_FLAGS[minor]}"; fi
if argparse_has "major" && [[ "$OPT_MAJOR" != "REMOVE" ]]; then OPT_MAJOR="${ARGPARSE_FLAGS[major]}"; fi

# ---------------------------------------------------------------------------
# Validate required arguments
# ---------------------------------------------------------------------------
if [[ -z "$PROJECT" ]]; then
    echo "ERROR: project name is required (--project <name>)" >&2
    usage
    exit 1
fi

if [[ "$OPT_SHOW" == "false" && -z "$OPT_MINOR" && -z "$OPT_MAJOR" ]]; then
    echo "ERROR: at least one action is required (--show, --minor, --major, --no-minor, --no-major)" >&2
    usage
    exit 1
fi

# ---------------------------------------------------------------------------
# Validate integer values (reject non-integers for --minor / --major)
# ---------------------------------------------------------------------------
_validate_integer() {
    local flag="$1" value="$2"
    if ! [[ "$value" =~ ^[0-9]+$ ]]; then
        echo "ERROR: ${flag} requires a non-negative integer; got: '${value}'" >&2
        exit 1
    fi
}

if [[ -n "$OPT_MINOR" && "$OPT_MINOR" != "REMOVE" ]]; then
    _validate_integer "--minor" "$OPT_MINOR"
fi
if [[ -n "$OPT_MAJOR" && "$OPT_MAJOR" != "REMOVE" ]]; then
    _validate_integer "--major" "$OPT_MAJOR"
fi

# ---------------------------------------------------------------------------
# Locate project.cfg
# ---------------------------------------------------------------------------
# Multi-project layout: project.cfg lives at projects/<name>/project.cfg.
# install.sh always creates $KANBAN_ROOT/projects/, so we don't fall back to
# a kanban-root cfg path.
CFG_FILE="${KANBAN_ROOT}/projects/${PROJECT}/project.cfg"
if [[ ! -f "$CFG_FILE" ]]; then
    echo "ERROR: project '${PROJECT}' not found — no project.cfg at: ${CFG_FILE}" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Helper: read a field value from the cfg file
# ---------------------------------------------------------------------------
_read_field() {
    local field="$1"
    # Matches optional whitespace around '=', strips quotes, strips trailing ws.
    grep -E "^[[:space:]]*${field}[[:space:]]*=" "$CFG_FILE" \
        | head -n1 \
        | sed 's|^[^=]*=[[:space:]]*||; s|[[:space:]]*$||; s|^["'"'"']||; s|["'"'"']$||' \
        || true
}

# ---------------------------------------------------------------------------
# --show: print current values and exit
# ---------------------------------------------------------------------------
if [[ "$OPT_SHOW" == "true" && -z "$OPT_MINOR" && -z "$OPT_MAJOR" ]]; then
    minor="$(_read_field max_minor)"
    major="$(_read_field max_major)"
    if [[ -n "$minor" ]]; then
        echo "max_minor=${minor}"
    else
        echo "max_minor=(unset)"
    fi
    if [[ -n "$major" ]]; then
        echo "max_major=${major}"
    else
        echo "max_major=(unset)"
    fi
    exit 0
fi

# ---------------------------------------------------------------------------
# In-place edit helpers
#
# Strategy:
#   - If the field already exists in the file, use sed to replace it.
#   - If the field is absent and we are setting a value, append it.
#   - If the field is absent and we are removing it, do nothing (idempotent).
#   - All writes go to a temp file first; we replace atomically with mv.
# ---------------------------------------------------------------------------

# _field_exists <field>  — returns 0 if the field line is present, 1 otherwise
_field_exists() {
    local field="$1"
    grep -qE "^[[:space:]]*${field}[[:space:]]*=" "$CFG_FILE"
}

# _set_field <field> <value>
# Replaces the field line in-place.  If the field is absent, appends it.
_set_field() {
    local field="$1" value="$2"
    local tmp
    tmp="$(mktemp "${CFG_FILE}.XXXXXXXX")"

    if _field_exists "$field"; then
        # Replace the existing line (first match only).
        local replaced="false"
        while IFS= read -r line || [[ -n "$line" ]]; do
            if [[ "$replaced" == "false" ]] && echo "$line" | grep -qE "^[[:space:]]*${field}[[:space:]]*="; then
                echo "${field}=${value}"
                replaced="true"
            else
                echo "$line"
            fi
        done < "$CFG_FILE" > "$tmp"
    else
        # Append the field.  Ensure the file ends with a newline first.
        cp "$CFG_FILE" "$tmp"
        # Check last byte: if not a newline, add one before appending.
        if [[ -s "$tmp" ]]; then
            local last_byte
            last_byte="$(tail -c1 "$tmp" | od -An -tx1 | tr -d ' ')"
            if [[ "$last_byte" != "0a" ]]; then
                printf '\n' >> "$tmp"
            fi
        fi
        echo "${field}=${value}" >> "$tmp"
    fi

    mv "$tmp" "$CFG_FILE"
}

# _remove_field <field>
# Deletes the line matching the field from the file.  Idempotent (no-op if absent).
_remove_field() {
    local field="$1"
    if ! _field_exists "$field"; then
        return 0  # already absent, nothing to do
    fi

    local tmp
    tmp="$(mktemp "${CFG_FILE}.XXXXXXXX")"

    local removed="false"
    while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ "$removed" == "false" ]] && echo "$line" | grep -qE "^[[:space:]]*${field}[[:space:]]*="; then
            removed="true"
            # Skip this line (removes it from output)
        else
            echo "$line"
        fi
    done < "$CFG_FILE" > "$tmp"

    mv "$tmp" "$CFG_FILE"
}

# ---------------------------------------------------------------------------
# Dry-run: compute and print intended changes without writing
# ---------------------------------------------------------------------------
if [[ "$DRY_RUN" == "true" ]]; then
    echo "[DRY-RUN] Would apply to: ${CFG_FILE}"
    if [[ -n "$OPT_MINOR" ]]; then
        if [[ "$OPT_MINOR" == "REMOVE" ]]; then
            echo "  remove max_minor"
        else
            echo "  set max_minor=${OPT_MINOR}"
        fi
    fi
    if [[ -n "$OPT_MAJOR" ]]; then
        if [[ "$OPT_MAJOR" == "REMOVE" ]]; then
            echo "  remove max_major"
        else
            echo "  set max_major=${OPT_MAJOR}"
        fi
    fi
    if [[ "$OPT_SHOW" == "true" ]]; then
        echo "  [--show would print after changes are applied]"
    fi
    exit 0
fi

# ---------------------------------------------------------------------------
# Apply changes
# ---------------------------------------------------------------------------
if [[ -n "$OPT_MINOR" ]]; then
    if [[ "$OPT_MINOR" == "REMOVE" ]]; then
        _remove_field "max_minor"
    else
        _set_field "max_minor" "$OPT_MINOR"
    fi
fi

if [[ -n "$OPT_MAJOR" ]]; then
    if [[ "$OPT_MAJOR" == "REMOVE" ]]; then
        _remove_field "max_major"
    else
        _set_field "max_major" "$OPT_MAJOR"
    fi
fi

# ---------------------------------------------------------------------------
# --show (when combined with set/remove actions: print final state)
# ---------------------------------------------------------------------------
if [[ "$OPT_SHOW" == "true" ]]; then
    minor="$(_read_field max_minor)"
    major="$(_read_field max_major)"
    if [[ -n "$minor" ]]; then
        echo "max_minor=${minor}"
    else
        echo "max_minor=(unset)"
    fi
    if [[ -n "$major" ]]; then
        echo "max_major=${major}"
    else
        echo "max_major=(unset)"
    fi
fi
