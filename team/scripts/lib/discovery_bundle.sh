#!/usr/bin/env bash
# team/scripts/lib/discovery_bundle.sh
#
# Discovery stale-requirements scan stage: a stateless read-only scan over a
# project's requirements/ directory that identifies operator-authored files
# which are orphaned — their target_version has already shipped but no
# matching release-notes entry exists. Sourced by
# team/scripts/lib/discovery.sh; extracted from that file's former inline
# body so discovery.sh's own body stays a thin driver over sourced lib
# stages. This is a behavior-preserving move: no stdout output, exit code,
# or filesystem read changes as a result of this file existing —
# it is one stage of a driver that was split into single-purpose stage
# libraries so each stays a focused, independently testable unit.
#
# Public entry point
# -------------------
#   discovery_scan_stale_requirements <project_name>
#       For each *.md file in requirements/, emits one TSV line
#       (filename, target_version, current_live, cross_project_hint) for
#       every stale, orphaned, non-bundle file found. Always returns 0
#       (empty output when nothing is stale). Called directly by
#       team/scripts/dashboard/attention.sh and
#       team/scripts/list-stale-requirements.sh — neither of which sources
#       this file directly; both source team/scripts/lib/discovery.sh, which
#       sources this file.
#
# parse_target_version_from_filename <filename> and
# is_requirements_bundle_file <filename> are shared primitives used
# internally by discovery_scan_stale_requirements above, and are also
# exported as standalone public helpers in their own right for any external
# caller that needs to inspect a requirements filename directly without
# running the full stale scan.
#
# Dependencies (must already be sourced/defined by the caller before this
# file's functions are called)
# -----------------------------------------------------------------------
#   team/scripts/lib/semver.sh — semver_from_filename, semver_lte.
#   team/scripts/lib/project_paths.sh — pp_require_project_context,
#     pp_requirements_dir, pp_project_root, pp_last_released_version.
#   team/scripts/lib/projects.sh — projects_cfg_list (optional; cross-project
#     hints are skipped silently when this function is not available).
#   team/scripts/lib/discovery_state.sh — _disc_read_bundle_field (used for
#     the ## Status: done retirement check).

# Guard against double-sourcing.
if declare -F discovery_scan_stale_requirements >/dev/null 2>&1; then
    return 0
fi

# ===========================================================================
# Public helpers for stale-requirements detection (used by dashboard-attention.sh
# and any other external caller that needs to inspect requirements/ directly).
# ===========================================================================

# ---------------------------------------------------------------------------
# parse_target_version_from_filename <filename>
#
# Parse the target version (vX.Y.Z) from a requirements file's basename.
# The expected filename pattern is: vX.Y.Z-<slug>.md
#
# On success: echoes the version string (e.g. "v0.2.0") and returns 0.
# On failure (no match): echoes nothing and returns 1.
#
# This is a wrapper around semver_from_filename that makes the return code
# unambiguous (semver_from_filename always returns 0; this returns 1 on miss).
#
# Examples:
#   parse_target_version_from_filename "v0.2.0-dots-and-scoring.md"         -> "v0.2.0"
#   parse_target_version_from_filename "no-version-here.md"                 -> "" (return 1)
# ---------------------------------------------------------------------------
parse_target_version_from_filename() {
    local fname
    fname="$(basename "${1:-}")"
    local ver
    ver="$(semver_from_filename "$fname")"
    if [[ -z "$ver" ]]; then
        return 1
    fi
    echo "$ver"
    return 0
}

# ---------------------------------------------------------------------------
# is_requirements_bundle_file <filename>
#
# Return 0 (true) when the filename matches an auto-generated bundle pattern:
#   *-bugfix-bundle-*.md
#   *-priority-bundle-*.md
#
# Return 1 (false) for all other filenames (operator-authored requirements).
#
# Examples:
#   is_requirements_bundle_file "v0.21.10-bugfix-bundle-20260507.md"   -> 0 (true)
#   is_requirements_bundle_file "v0.2.0-dots-and-scoring.md"           -> 1 (false)
# ---------------------------------------------------------------------------
is_requirements_bundle_file() {
    local fname
    fname="$(basename "${1:-}")"
    [[ "$fname" == *-bugfix-bundle-*.md || "$fname" == *-priority-bundle-*.md ]]
}

# ---------------------------------------------------------------------------
# discovery_scan_stale_requirements <project_name>
#
# Stateless scan: for each *.md file in a project's requirements/ directory,
# identify operator-authored files whose target_version <= current_live and
# which have no matching release-notes/<target_version>*.md entry (i.e.,
# orphaned — never shipped).
#
# For each stale file found, outputs one line in TSV format:
#   <filename>\t<target_version>\t<current_live>\t<cross_project_hint>
#
# Where <cross_project_hint> is the name of another registered project whose
# full name appears verbatim in the filename slug or the file's first 10 lines,
# or an empty string when no unique match is found.
#
# Files are excluded from output when ANY of the following apply:
#   - Filename matches *-bugfix-bundle-*.md or *-priority-bundle-*.md
#   - target_version cannot be parsed (filename lacks vX.Y.Z prefix)
#   - target_version > current_live (normal queued work, not stale)
#   - file's own ## Status header is 'done'
#   - release-notes/<target_version>*.md exists (shipped artifact; not orphaned)
#
# Arguments:
#   $1 — project_name (required)
#
# Requires: semver_lte (from lib/semver.sh), pp_requirements_dir,
#           pp_project_root, projects_cfg_list (from lib/projects.sh, if
#           already loaded by the caller). If projects_cfg_list is not
#           available, cross-project hints are skipped silently.
#
# Returns 0 always (empty output when no stale files found).
# ---------------------------------------------------------------------------
discovery_scan_stale_requirements() {
    local project_name="${1:-}"
    project_name="$(pp_require_project_context "${project_name:-}")" || return 1

    local requirements_dir release_notes_dir dev_release_notes_dir
    requirements_dir="$(pp_requirements_dir "$project_name" 2>/dev/null)" || return 0
    # Primary release-notes dir: under the kanban project root.
    release_notes_dir="$(pp_project_root "$project_name" 2>/dev/null)/release-notes"
    # Secondary and tertiary release-notes dirs: under the dev tree.
    # The dev tree is the canonical location where cm/release.sh writes release notes;
    # the live install's project directory typically does not have release-notes/.
    # Two locations to check in the dev tree:
    #   - projects/<name>/release-notes/  (per-project layout)
    #   - release-notes/                  (root-level location)
    dev_release_notes_dir=""
    local dev_release_notes_root_dir=""
    local _rn_cfg_file
    # Source: project.cfg [project] dev_tree_path (falls back to PROJECT.cfg)
    _rn_cfg_file="$(_pp_project_cfg_file "$(pp_project_root "$project_name" 2>/dev/null)")"
    if [[ -n "$_rn_cfg_file" ]]; then
        local _dev_tree
        _dev_tree="$(_pp_read_cfg_key "$_rn_cfg_file" project dev_tree_path "")"
        if [[ -n "$_dev_tree" ]]; then
            # Per-project release-notes
            dev_release_notes_dir="${_dev_tree}/projects/${project_name}/release-notes"
            # Root-level release-notes
            dev_release_notes_root_dir="${_dev_tree}/release-notes"
        fi
    fi

    [[ -d "$requirements_dir" ]] || return 0

    # Determine current_live for this project.
    local current_live
    current_live="$(pp_last_released_version "$project_name" 2>/dev/null || echo "v0.0.0")"
    [[ -z "$current_live" || "$current_live" == "none" ]] && current_live="v0.0.0"

    # Collect other registered project names (for cross-project hints).
    # projects_cfg_list may not be available if projects.sh was not sourced;
    # in that case we silently skip the hint check.
    local all_project_names=()
    if declare -F projects_cfg_list >/dev/null 2>&1; then
        while IFS= read -r _pn; do
            [[ -z "$_pn" || "$_pn" == "$project_name" ]] && continue
            all_project_names+=("$_pn")
        done < <(projects_cfg_list 2>/dev/null || true)
    fi

    # Iterate requirements/*.md files.
    local req_file
    for req_file in "${requirements_dir}"/*.md; do
        [[ -f "$req_file" ]] || continue
        local fname
        fname="$(basename "$req_file")"

        # Skip bundle files (legitimate stale artifacts from past iterations).
        if is_requirements_bundle_file "$fname"; then
            continue
        fi

        # Parse target_version from filename.
        local target_version
        target_version="$(parse_target_version_from_filename "$fname")" || continue

        # Skip when target_version > current_live (normal queued work).
        if ! semver_lte "$target_version" "$current_live"; then
            continue
        fi

        # Honor ## Status: done as authoritative retirement signal.
        # A requirements file that declares ## Status: done was explicitly marked
        # shipped by the operator.  Skip it unconditionally, before the (more
        # expensive) release-notes lookup.  This mirrors the done-suppression
        # already used in the bundle pickup path (~line 523) and the discovery
        # bundle path (~line 1612).
        local _req_status
        _req_status="$(_disc_read_bundle_field "$req_file" "Status" 2>/dev/null || echo "open")"
        _req_status="${_req_status,,}"  # lowercase
        if [[ "$_req_status" == "done" ]]; then
            continue
        fi

        # Check for a matching release-notes entry (shipped artifact → skip).
        # Three locations are checked in priority order:
        #   1. Kanban project root's release-notes/ (live install)
        #   2. Dev tree's per-project release-notes/
        #   3. Dev tree's root release-notes/
        # Use exact-version match to prevent vX.Y.Z*.md from matching
        # vX.Y.Z(N+1)-... release notes.  Check bare exact vX.Y.Z.md plus
        # hyphen-anchored vX.Y.Z-*.md for any future hyphenated release note names.
        local _release_note_found=false
        if [[ -d "$release_notes_dir" ]] && \
           { compgen -G "${release_notes_dir}/${target_version}-*.md" >/dev/null 2>&1 || \
             [[ -f "${release_notes_dir}/${target_version}.md" ]]; }; then
            _release_note_found=true
        fi
        if [[ "$_release_note_found" == "false" && -n "$dev_release_notes_dir" && \
              -d "$dev_release_notes_dir" ]] && \
           { compgen -G "${dev_release_notes_dir}/${target_version}-*.md" >/dev/null 2>&1 || \
             [[ -f "${dev_release_notes_dir}/${target_version}.md" ]]; }; then
            _release_note_found=true
        fi
        if [[ "$_release_note_found" == "false" && -n "$dev_release_notes_root_dir" && \
              -d "$dev_release_notes_root_dir" ]] && \
           { compgen -G "${dev_release_notes_root_dir}/${target_version}-*.md" >/dev/null 2>&1 || \
             [[ -f "${dev_release_notes_root_dir}/${target_version}.md" ]]; }; then
            _release_note_found=true
        fi
        if [[ "$_release_note_found" == "true" ]]; then
            continue
        fi

        # File is stale and orphaned. Compute cross-project hint.
        local hint=""
        if [[ "${#all_project_names[@]}" -gt 0 ]]; then
            # Scan filename slug and first 10 lines of file content.
            local file_head
            file_head="$(head -n 10 "$req_file" 2>/dev/null || true)"
            local scan_text="${fname} ${file_head}"

            local matched_projects=()
            local _other_proj
            for _other_proj in "${all_project_names[@]}"; do
                # Exact full-name match only (avoids false positives on shared substrings).
                if [[ "$scan_text" == *"${_other_proj}"* ]]; then
                    matched_projects+=("$_other_proj")
                fi
            done

            # Emit hint only when exactly one other project matched.
            if [[ "${#matched_projects[@]}" -eq 1 ]]; then
                hint="${matched_projects[0]}"
            fi
        fi

        # Output TSV line: filename<TAB>target_version<TAB>current_live<TAB>hint
        printf '%s\t%s\t%s\t%s\n' "$fname" "$target_version" "$current_live" "$hint"
    done

    return 0
}
