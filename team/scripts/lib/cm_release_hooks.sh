#!/usr/bin/env bash
# team/scripts/lib/cm_release_hooks.sh
#
# Shared library for CM release hook resolution, visibility printing, and
# required-hook enforcement.  Used by both release.sh and ship-rc.sh so that
# hook logic lives in exactly one implementation (one-implementation rule).
#
# Precedence order (highest to lowest):
#   (a) project.cfg [hooks] cm_release_<phase>_hook          (cfg)
#   (b) projects/<name>/hooks/cm-release-<phase>.sh          (kanban-side)
#   (c) <dev_tree_path>/.pgai/hooks/cm-release-<phase>.sh   (in-repo)
# Deployment overrides app default — the highest-precedence tier that supplies
# a path wins.
#
# Hook execution is delegated to _run_release_hook (defined in release.sh /
# ship-rc.sh).  This library does NOT execute hooks itself; it resolves, prints,
# and enforces.
#
# Public API
# ----------
#   cm_resolve_and_enforce_hook <project_name> <phase> <project_hooks_dir> <dev_tree_path>
#
#       Resolves the hook path for <phase> (pre-squash | pre-tag | post-tag).
#       Prints exactly one line to stdout:
#           "<phase> hook: <path> (source: cfg|kanban-side|in-repo)"
#         or
#           "<phase> hook: none configured"
#
#       Sets the global variable CM_RESOLVED_HOOK_PATH to the resolved absolute
#       path, or empty string when no hook is found.
#
#       Required-flag enforcement: reads cm_release_<phase>_hook_required from
#       project.cfg.  When true and no hook is found at any tier, calls cm_halt
#       (which the caller must have defined before sourcing this library) with a
#       message naming the phase, all three searched locations, and the config key.
#       cm_halt is expected to exit non-zero.
#
#       Non-executable in-repo hook: when tier (c) resolves a path and the file
#       exists but is not executable, prints an error naming the path and the
#       chmod fix, then returns 1.  The caller must handle the non-zero return
#       (typically by halting the release).
#
#       Returns:
#           0   — hook resolved (or none configured with required=false)
#           1   — non-executable in-repo hook detected (caller must halt)
#         exits — when required=true and no hook found (via cm_halt)
#
# Dependencies (must be sourced by the caller before sourcing this file)
# ----------------------------------------------------------------------
#   team/scripts/lib/project_paths.sh  — provides _pp_project_cfg_file,
#                                        _pp_read_cfg_key, pp_project_root
#
# The caller (release.sh / ship-rc.sh) must also define cm_halt <trigger> <reason>
# before any cm_resolve_and_enforce_hook call.
#
# Source order
# ------------
#   source project_paths.sh          # first
#   source projects.sh               # provides projects_resolve_release_hook_path (optional)
#   source cm_release_hooks.sh       # last

# Guard against double-sourcing.
if declare -F cm_resolve_and_enforce_hook >/dev/null 2>&1; then
    return 0
fi

# ---------------------------------------------------------------------------
# cm_resolve_and_enforce_hook <project_name> <phase> <project_hooks_dir> <dev_tree_path>
#
# Arguments:
#   project_name       — project name as registered in projects.cfg
#   phase              — one of: pre-squash | pre-tag | post-tag
#   project_hooks_dir  — path to $KANBAN_ROOT/projects/<name>/hooks/
#   dev_tree_path      — absolute path to the project's git checkout (dev tree)
#
# Side effects:
#   Sets CM_RESOLVED_HOOK_PATH (global) to the resolved absolute hook path,
#   or empty string when no hook is configured.
#   Prints one resolution line to stdout (always — 'none configured' is explicit).
#
# Returns 0 on success, 1 on non-executable in-repo hook.
# Calls cm_halt (and therefore does not return) when required=true and no hook found.
# ---------------------------------------------------------------------------
cm_resolve_and_enforce_hook() {
    local _creh_project_name="${1:?cm_resolve_and_enforce_hook: project_name is required}"
    local _creh_phase="${2:?cm_resolve_and_enforce_hook: phase is required}"
    local _creh_project_hooks_dir="${3:?cm_resolve_and_enforce_hook: project_hooks_dir is required}"
    local _creh_dev_tree_path="${4:?cm_resolve_and_enforce_hook: dev_tree_path is required}"

    # Validate phase
    local _creh_field_name
    case "$_creh_phase" in
        pre-squash)  _creh_field_name="cm_release_pre_squash_hook" ;;
        pre-tag)     _creh_field_name="cm_release_pre_tag_hook"    ;;
        post-tag)    _creh_field_name="cm_release_post_tag_hook"   ;;
        *)
            echo "cm_resolve_and_enforce_hook: unknown phase '${_creh_phase}'; expected pre-squash | pre-tag | post-tag" >&2
            return 1
            ;;
    esac

    # Derive the required-flag field name from the phase
    local _creh_required_field_name
    case "$_creh_phase" in
        pre-squash)  _creh_required_field_name="cm_release_pre_squash_hook_required" ;;
        pre-tag)     _creh_required_field_name="cm_release_pre_tag_hook_required"    ;;
        post-tag)    _creh_required_field_name="cm_release_post_tag_hook_required"   ;;
    esac

    # Locate project.cfg
    local _creh_project_root
    _creh_project_root="$(KANBAN_ROOT="${KANBAN_ROOT:-${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}}" \
        pp_project_root "$_creh_project_name" 2>/dev/null)" || _creh_project_root=""
    local _creh_cfg_file=""
    if [[ -n "$_creh_project_root" ]]; then
        _creh_cfg_file="$(_pp_project_cfg_file "$_creh_project_root")"
    fi

    # ---------------------------------------------------------------------------
    # Tier (a): project.cfg [hooks] cm_release_<phase>_hook
    # ---------------------------------------------------------------------------
    local _creh_cfg_raw=""
    if [[ -n "$_creh_cfg_file" && -f "$_creh_cfg_file" ]]; then
        _creh_cfg_raw="$(_pp_read_cfg_key "$_creh_cfg_file" "hooks" "$_creh_field_name" "")"
    fi

    local _creh_resolved_path=""
    local _creh_source_label=""

    if [[ -n "$_creh_cfg_raw" ]]; then
        # Resolve absolute or relative (relative to dev_tree_path)
        if [[ "$_creh_cfg_raw" == /* ]]; then
            _creh_resolved_path="$_creh_cfg_raw"
        else
            _creh_resolved_path="${_creh_dev_tree_path}/${_creh_cfg_raw}"
        fi
        _creh_source_label="cfg"
    fi

    # ---------------------------------------------------------------------------
    # Tier (b): kanban-side projects/<name>/hooks/cm-release-<phase>.sh
    # ---------------------------------------------------------------------------
    if [[ -z "$_creh_resolved_path" ]]; then
        local _creh_kanban_side="${_creh_project_hooks_dir}/cm-release-${_creh_phase}.sh"
        if [[ -f "$_creh_kanban_side" ]]; then
            _creh_resolved_path="$_creh_kanban_side"
            _creh_source_label="kanban-side"
        fi
    fi

    # ---------------------------------------------------------------------------
    # Tier (c): in-repo <dev_tree_path>/.pgai/hooks/cm-release-<phase>.sh
    # ---------------------------------------------------------------------------
    if [[ -z "$_creh_resolved_path" ]]; then
        local _creh_in_repo="${_creh_dev_tree_path}/.pgai/hooks/cm-release-${_creh_phase}.sh"
        if [[ -f "$_creh_in_repo" ]]; then
            # Non-executable in-repo hook is an ERROR (fail-loud), not a silent skip.
            if [[ ! -x "$_creh_in_repo" ]]; then
                echo "[cm-release] ERROR: in-repo hook exists but is not executable: ${_creh_in_repo}" >&2
                echo "[cm-release] ERROR: Fix with: chmod +x ${_creh_in_repo}" >&2
                CM_RESOLVED_HOOK_PATH=""
                return 1
            fi
            _creh_resolved_path="$_creh_in_repo"
            _creh_source_label="in-repo"
        fi
    fi

    # ---------------------------------------------------------------------------
    # Required-flag enforcement
    # ---------------------------------------------------------------------------
    local _creh_required="false"
    if [[ -n "$_creh_cfg_file" && -f "$_creh_cfg_file" ]]; then
        _creh_required="$(_pp_read_cfg_key "$_creh_cfg_file" "hooks" "$_creh_required_field_name" "false")"
    fi
    # Normalize to lowercase
    _creh_required="${_creh_required,,}"

    if [[ -z "$_creh_resolved_path" && "$_creh_required" == "true" ]]; then
        # Compute the three searched locations for the HALT message
        local _creh_loc_cfg=""
        if [[ -n "$_creh_cfg_file" ]]; then
            _creh_loc_cfg="$_creh_cfg_file [hooks] ${_creh_field_name}"
        else
            _creh_loc_cfg="project.cfg [hooks] ${_creh_field_name} (no project.cfg found)"
        fi
        local _creh_loc_kanban="${_creh_project_hooks_dir}/cm-release-${_creh_phase}.sh"
        local _creh_loc_inrepo="${_creh_dev_tree_path}/.pgai/hooks/cm-release-${_creh_phase}.sh"

        CM_RESOLVED_HOOK_PATH=""
        cm_halt \
            "Required hook missing: ${_creh_phase} hook not found at any tier" \
            "HALT before ${_creh_phase} phase: ${_creh_required_field_name}=true but no hook found. Searched: (cfg) ${_creh_loc_cfg}; (kanban-side) ${_creh_loc_kanban}; (in-repo) ${_creh_loc_inrepo}. Add the hook or set ${_creh_required_field_name}=false."
        # cm_halt calls exit; we never return here.
        return 1
    fi

    # ---------------------------------------------------------------------------
    # Print exactly one resolution line (always)
    # ---------------------------------------------------------------------------
    if [[ -n "$_creh_resolved_path" ]]; then
        echo "${_creh_phase} hook: ${_creh_resolved_path} (source: ${_creh_source_label})"
    else
        echo "${_creh_phase} hook: none configured"
    fi

    # Export the resolved path for the caller
    CM_RESOLVED_HOOK_PATH="$_creh_resolved_path"
    return 0
}
