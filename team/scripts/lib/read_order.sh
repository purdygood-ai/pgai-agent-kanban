#!/usr/bin/env bash
# team/scripts/lib/read_order.sh
# Resolver for the per-role read-order manifests under read-order/.
#
# WHAT THIS IS
# ------------
# A manifest is one plain-text file per role (read-order/<role>.txt)
# listing, in order, the governance documents that role's wake prompt
# names. This library is the only code in the tree that knows the
# manifest format; it turns a role name into the ordered list of
# existing absolute paths the manifest names, plus the summed byte
# size of those files. Nothing else parses read-order/*.txt directly.
#
# MANIFEST FORMAT
# ----------------
# Plain text, one path per line, relative to the kanban root as the
# RUNTIME layout resolves it (install.sh flattens team/ onto the
# kanban root, so a manifest line reads "roles/CODER.md", never
# "team/roles/CODER.md"). Blank lines and lines beginning with # are
# ignored. No other dialect (no key=value, no sections) is supported.
#
# ROOT RESOLUTION
# ----------------
# A manifest line is resolved against a root directory that mirrors
# the runtime kanban-root layout. Both existing layouts satisfy this:
#   - Runtime (installed):  $KANBAN_ROOT/read-order/<role>.txt
#                            resolves against $KANBAN_ROOT itself.
#   - Dev tree (checkout):  team/read-order/<role>.txt
#                            resolves against team/ itself.
# In both cases the manifest directory's own parent is the correct
# root, so the resolver self-locates it from this file's own location
# by default (team/scripts/lib/read_order.sh -> root is two levels up)
# rather than reading an env var — each tree finds itself. Callers that
# need to resolve against a different root (tests, an alternate
# checkout) pass it explicitly as the optional second argument.
#
# Source this file to get read_order_resolve_role and
# read_order_role_byte_total in your shell:
#   source "$(dirname "${BASH_SOURCE[0]}")/read_order.sh"
#
# Functions
# ---------
#   read_order_resolve_role <role> [root_dir]
#     Prints the ordered, resolved absolute paths of the existing
#     files named in read-order/<role>.txt, one per line. A manifest
#     line naming a file that does not exist is skipped (narrated to
#     stderr), never a hard failure. A role with no manifest file
#     prints nothing and returns 0 -- not fatal; the caller falls back.
#
#   read_order_role_byte_total <role> [root_dir]
#     Prints the summed byte size (decimal, no trailing newline data
#     beyond the number itself) of exactly the files
#     read_order_resolve_role emitted for that role. Prints 0 for a
#     role with no manifest or whose manifest resolves to zero files.
#
# Safety invariants:
#   - No top-level side effects when sourced -- only function definitions.
#   - Functions do not modify the caller's environment (all locals).
#   - stdout carries only the resolved paths / byte total; narration
#     about skipped lines goes to stderr.

# ---------------------------------------------------------------------------
# _read_order_default_root (private)
# Self-locates the root directory that mirrors the kanban-root layout,
# from this file's own location: team/scripts/lib/read_order.sh -> team/
# (dev tree) or scripts/lib/read_order.sh -> $KANBAN_ROOT (installed).
# Two directories up from this file's own directory in both layouts.
# ---------------------------------------------------------------------------
_read_order_default_root() {
    local lib_dir
    lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    (cd "${lib_dir}/../.." && pwd)
}

# ---------------------------------------------------------------------------
# read_order_resolve_role <role> [root_dir]
# ---------------------------------------------------------------------------
read_order_resolve_role() {
    local role="$1"
    local root_dir="${2:-$(_read_order_default_root)}"
    local manifest="${root_dir}/read-order/${role}.txt"

    if [[ ! -f "$manifest" ]]; then
        printf 'read_order: no manifest for role "%s" (expected %s) -- caller falls back\n' \
            "$role" "$manifest" >&2
        return 0
    fi

    local line resolved
    while IFS= read -r line || [[ -n "$line" ]]; do
        # Strip leading/trailing whitespace.
        line="${line#"${line%%[![:space:]]*}"}"
        line="${line%"${line##*[![:space:]]}"}"

        # Skip blank lines and # comments.
        [[ -z "$line" ]] && continue
        [[ "$line" == \#* ]] && continue

        resolved="${root_dir}/${line}"
        if [[ -f "$resolved" ]]; then
            printf '%s\n' "$resolved"
        else
            printf 'read_order: skipping "%s" manifest line "%s" -- resolved file not found: %s\n' \
                "$role" "$line" "$resolved" >&2
        fi
    done < "$manifest"

    return 0
}

# ---------------------------------------------------------------------------
# read_order_role_byte_total <role> [root_dir]
# ---------------------------------------------------------------------------
read_order_role_byte_total() {
    local role="$1"
    local root_dir="${2:-$(_read_order_default_root)}"
    local total=0
    local path size

    while IFS= read -r path; do
        [[ -z "$path" ]] && continue
        size="$(wc -c < "$path" 2>/dev/null || echo 0)"
        total=$(( total + size ))
    done < <(read_order_resolve_role "$role" "$root_dir" 2>/dev/null)

    printf '%d' "$total"
}
