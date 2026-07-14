#!/usr/bin/env bash
# team/scripts/lib/workflow.sh
#
# Workflow-type dispatcher for the pgai-agent-kanban framework.
#
# This library resolves a project's workflow_type from project.cfg,
# discovery-scans the workflows root for a matching plugin directory,
# validates the plugin's workflow.cfg manifest, sources the plugin's
# workflow.sh, and exposes a uniform wf_* call surface to engine callers.
#
# Fail-closed contract
# --------------------
# Any of the following conditions cause wf_load_plugin to return non-zero
# and set WF_LOAD_ERROR to a human-readable message naming the type:
#   - workflow_type value not found in any scanned plugin directory
#   - manifest (workflow.cfg) missing or unreadable
#   - manifest [workflow] status = scaffold
#   - manifest missing required fields
#   - plugin workflow.sh missing or not sourced cleanly
#
# NEVER silently defaults to release behavior. Every unknown or broken
# workflow_type is a hard failure that the caller must surface as BLOCKED.
#
# Workflows root resolution order
# --------------------------------
# The following precedence is used (first non-empty path that exists wins):
#   1. Explicit --workflows-dir argument passed to wf_load_plugin
#   2. $PGAI_AGENT_KANBAN_ROOT_PATH/workflows/   (live-install path)
#   3. team/workflows/   relative to the dev tree (TEAM_ROOT or script dir)
#
# Public API
# ----------
#   wf_load_plugin [--workflows-dir DIR] <workflow_type>
#       Discover, validate, and source the plugin. Returns 0 on success,
#       non-zero on any failure (error in WF_LOAD_ERROR). Sets WF_PLUGIN_DIR
#       to the sourced plugin directory on success.
#
#   wf_resolve_target_version [args...]
#       How the workflow type derives/validates the release version.
#       Must be called after wf_load_plugin.
#
#   wf_git_mode
#       Prints the git mode capability: none | ro | rw.
#       Must be called after wf_load_plugin.
#
#   wf_pre_task [args...]
#       Per-task setup (worktree creation for git modes; no-op for none).
#       Must be called after wf_load_plugin.
#
#   wf_post_task [args...]
#       Per-task teardown (worktree removal for git modes; no-op for none).
#       Must be called after wf_load_plugin.
#
#   wf_finalize [args...]
#       Finalization capability: tag | publish | report.
#       Must be called after wf_load_plugin.
#
#   wf_agents
#       Prints the ordered agent roster for PM decomposition.
#       Must be called after wf_load_plugin.
#
#   wf_bundle_source_branch [args...]
#       Prints the source branch name for discovery bundle writes.
#       Must be called after wf_load_plugin.
#
#   wf_dashboard_render [args...]
#       Per-type dashboard display rule.
#       Must be called after wf_load_plugin.
#
#   wf_version_semantics
#       Prints the version_semantics capability declared in the plugin's
#       workflow.cfg manifest.  Common values: semver | label | none.
#       Must be called after wf_load_plugin.
#
# State variables set by wf_load_plugin
# --------------------------------------
#   WF_PLUGIN_DIR   Absolute path to the sourced plugin directory (success only).
#   WF_LOAD_ERROR   Human-readable error message (failure only; empty on success).
#   WF_TYPE         The workflow_type string that was resolved (set always).
#
# Safety invariants
# -----------------
#   - No top-level side effects when sourced — only function definitions.
#   - All state variables (WF_*) are reset at the start of wf_load_plugin.
#   - read_ini must be available before sourcing this file, or ini_parser.sh
#     must be locatable at the same directory as this script.

# ---------------------------------------------------------------------------
# Bootstrap: ensure read_ini is available.
# ---------------------------------------------------------------------------
if ! command -v read_ini >/dev/null 2>&1; then
    # shellcheck source=ini_parser.sh
    source "$(dirname "${BASH_SOURCE[0]}")/ini_parser.sh"
fi

# ---------------------------------------------------------------------------
# State variables (reset by wf_load_plugin at entry).
# ---------------------------------------------------------------------------
WF_PLUGIN_DIR=""
WF_LOAD_ERROR=""
WF_TYPE=""

# ---------------------------------------------------------------------------
# Stub definitions for the wf_* surface.
#
# These definitions are active before wf_load_plugin is called and will be
# overwritten by the plugin's own definitions after a successful load.
# Calling any stub before a successful wf_load_plugin returns non-zero with
# an actionable error.
# ---------------------------------------------------------------------------

wf_resolve_target_version() {
    echo "workflow dispatcher: wf_load_plugin must be called before wf_resolve_target_version" >&2
    return 1
}

wf_git_mode() {
    echo "workflow dispatcher: wf_load_plugin must be called before wf_git_mode" >&2
    return 1
}

wf_pre_task() {
    echo "workflow dispatcher: wf_load_plugin must be called before wf_pre_task" >&2
    return 1
}

wf_post_task() {
    echo "workflow dispatcher: wf_load_plugin must be called before wf_post_task" >&2
    return 1
}

wf_finalize() {
    echo "workflow dispatcher: wf_load_plugin must be called before wf_finalize" >&2
    return 1
}

wf_agents() {
    echo "workflow dispatcher: wf_load_plugin must be called before wf_agents" >&2
    return 1
}

wf_bundle_source_branch() {
    echo "workflow dispatcher: wf_load_plugin must be called before wf_bundle_source_branch" >&2
    return 1
}

wf_dashboard_render() {
    echo "workflow dispatcher: wf_load_plugin must be called before wf_dashboard_render" >&2
    return 1
}

wf_version_semantics() {
    echo "workflow dispatcher: wf_load_plugin must be called before wf_version_semantics" >&2
    return 1
}

# ---------------------------------------------------------------------------
# _wf_resolve_workflows_root [explicit_dir]
#
# Resolves the workflows root directory using the three-tier precedence:
#   1. Explicit directory argument (when non-empty and exists)
#   2. $PGAI_AGENT_KANBAN_ROOT_PATH/workflows/
#   3. team/workflows/ relative to TEAM_ROOT (or this script's location)
#
# Prints the resolved path on success; returns 0.
# Prints an error to stderr and returns 1 when no root can be found.
# ---------------------------------------------------------------------------
_wf_resolve_workflows_root() {
    local explicit_dir="${1:-}"

    # Tier 1: explicit argument.
    if [[ -n "$explicit_dir" ]]; then
        if [[ -d "$explicit_dir" ]]; then
            printf '%s' "$explicit_dir"
            return 0
        else
            echo "workflow dispatcher: explicit workflows-dir does not exist: ${explicit_dir}" >&2
            return 1
        fi
    fi

    # Tier 2: live-install path under PGAI_AGENT_KANBAN_ROOT_PATH.
    if [[ -n "${PGAI_AGENT_KANBAN_ROOT_PATH:-}" ]]; then
        local _live_dir="${PGAI_AGENT_KANBAN_ROOT_PATH}/workflows"
        if [[ -d "$_live_dir" ]]; then
            printf '%s' "$_live_dir"
            return 0
        fi
    fi

    # Tier 3: team/workflows/ relative to TEAM_ROOT or this script's grandparent.
    # TEAM_ROOT is set by wake scripts when running inside the kanban.
    # Fall back to the directory two levels above this script (team/scripts/lib/ → team/).
    local _team_root="${TEAM_ROOT:-}"
    if [[ -z "$_team_root" ]]; then
        _team_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
    fi
    local _team_dir="${_team_root}/team/workflows"
    if [[ -d "$_team_dir" ]]; then
        printf '%s' "$_team_dir"
        return 0
    fi

    # Also try team/ directly under the script's grandparent (when BASH_SOURCE
    # is team/scripts/lib/workflow.sh, two parents up is the repo root and
    # team/workflows is a sibling of team/scripts).
    local _script_team_dir
    _script_team_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/workflows"
    if [[ -d "$_script_team_dir" ]]; then
        printf '%s' "$_script_team_dir"
        return 0
    fi

    echo "workflow dispatcher: no workflows root found (checked: PGAI_AGENT_KANBAN_ROOT_PATH/workflows, team/workflows)" >&2
    return 1
}

# ---------------------------------------------------------------------------
# _wf_parse_manifest <manifest_path> <workflow_type>
#
# Validates a workflow.cfg manifest file and reads its fields into
# WF_MANIFEST_* variables in the caller's scope.
#
# Returns 0 on success (manifest valid, status=ready).
# Returns non-zero and sets WF_LOAD_ERROR on any validation failure.
#
# Fields read:
#   WF_MANIFEST_NAME             — [workflow] name
#   WF_MANIFEST_DESCRIPTION      — [workflow] description
#   WF_MANIFEST_STATUS           — [workflow] status (scaffold | ready)
#   WF_MANIFEST_VERSION_SEMANTICS — [capabilities] version_semantics
#   WF_MANIFEST_GIT_MODE         — [capabilities] git_mode
#   WF_MANIFEST_FINALIZE         — [capabilities] finalize
#   WF_MANIFEST_AGENTS           — [capabilities] agents
# ---------------------------------------------------------------------------
_wf_parse_manifest() {
    local manifest_path="$1"
    local workflow_type="$2"

    # Guard: manifest must exist and be readable.
    if [[ ! -f "$manifest_path" ]]; then
        WF_LOAD_ERROR="workflow type '${workflow_type}': manifest not found at ${manifest_path}"
        return 1
    fi
    if [[ ! -r "$manifest_path" ]]; then
        WF_LOAD_ERROR="workflow type '${workflow_type}': manifest not readable at ${manifest_path}"
        return 1
    fi

    # Read [workflow] section.
    WF_MANIFEST_NAME="$(read_ini "$manifest_path" workflow name "")"
    WF_MANIFEST_DESCRIPTION="$(read_ini "$manifest_path" workflow description "")"
    WF_MANIFEST_STATUS="$(read_ini "$manifest_path" workflow status "")"

    # Read [capabilities] section.
    WF_MANIFEST_VERSION_SEMANTICS="$(read_ini "$manifest_path" capabilities version_semantics "")"
    WF_MANIFEST_GIT_MODE="$(read_ini "$manifest_path" capabilities git_mode "")"
    WF_MANIFEST_FINALIZE="$(read_ini "$manifest_path" capabilities finalize "")"
    WF_MANIFEST_AGENTS="$(read_ini "$manifest_path" capabilities agents "")"

    # Validate: status must be present and not empty.
    if [[ -z "$WF_MANIFEST_STATUS" ]]; then
        WF_LOAD_ERROR="workflow type '${workflow_type}': manifest missing [workflow] status field in ${manifest_path}"
        return 1
    fi

    # Fail-closed: status=scaffold means the plugin is not yet implemented.
    if [[ "$WF_MANIFEST_STATUS" == "scaffold" ]]; then
        WF_LOAD_ERROR="workflow type '${workflow_type}': plugin status is 'scaffold' — not ready for use; flip status to 'ready' after implementing all hooks"
        return 1
    fi

    # Validate: status must be "ready".
    if [[ "$WF_MANIFEST_STATUS" != "ready" ]]; then
        WF_LOAD_ERROR="workflow type '${workflow_type}': manifest [workflow] status must be 'ready' or 'scaffold', got '${WF_MANIFEST_STATUS}' in ${manifest_path}"
        return 1
    fi

    # Validate required capability fields.
    if [[ -z "$WF_MANIFEST_GIT_MODE" ]]; then
        WF_LOAD_ERROR="workflow type '${workflow_type}': manifest missing [capabilities] git_mode in ${manifest_path}"
        return 1
    fi
    if [[ -z "$WF_MANIFEST_AGENTS" ]]; then
        WF_LOAD_ERROR="workflow type '${workflow_type}': manifest missing [capabilities] agents in ${manifest_path}"
        return 1
    fi

    # Validate git_mode value.
    case "$WF_MANIFEST_GIT_MODE" in
        none|ro|rw) ;;
        *)
            WF_LOAD_ERROR="workflow type '${workflow_type}': invalid [capabilities] git_mode '${WF_MANIFEST_GIT_MODE}' (must be none, ro, or rw) in ${manifest_path}"
            return 1
            ;;
    esac

    return 0
}

# ---------------------------------------------------------------------------
# wf_load_plugin [--workflows-dir DIR] <workflow_type>
#
# Main entry point. Resolves the workflows root, finds the plugin directory
# for <workflow_type>, validates its manifest, sources its workflow.sh, and
# makes the wf_* surface active.
#
# Arguments:
#   --workflows-dir DIR   Optional. Explicit workflows root (overrides the
#                         two-tier env/path resolution). Used by tests and
#                         the generator.
#   workflow_type         Required. The workflow type string to resolve.
#
# On success:
#   - Returns 0
#   - WF_PLUGIN_DIR is set to the plugin directory path
#   - WF_TYPE is set to the workflow_type argument
#   - WF_LOAD_ERROR is empty
#   - All wf_* functions now delegate to the plugin's implementations
#
# On failure:
#   - Returns non-zero
#   - WF_LOAD_ERROR describes the failure (includes the workflow_type name)
#   - WF_PLUGIN_DIR is empty
#   - wf_* stubs remain active (calling them returns non-zero)
# ---------------------------------------------------------------------------
wf_load_plugin() {
    # Reset state at entry.
    WF_PLUGIN_DIR=""
    WF_LOAD_ERROR=""
    WF_TYPE=""

    # Reset manifest fields.
    WF_MANIFEST_NAME=""
    WF_MANIFEST_DESCRIPTION=""
    WF_MANIFEST_STATUS=""
    WF_MANIFEST_VERSION_SEMANTICS=""
    WF_MANIFEST_GIT_MODE=""
    WF_MANIFEST_FINALIZE=""
    WF_MANIFEST_AGENTS=""

    # Parse arguments.
    local _workflows_dir=""
    local _workflow_type=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --workflows-dir)
                _workflows_dir="$2"
                shift 2
                ;;
            --workflows-dir=*)
                _workflows_dir="${1#--workflows-dir=}"
                shift
                ;;
            --)
                shift
                break
                ;;
            -*)
                echo "wf_load_plugin: unknown option: $1" >&2
                return 1
                ;;
            *)
                _workflow_type="$1"
                shift
                ;;
        esac
    done

    if [[ -z "$_workflow_type" ]]; then
        WF_LOAD_ERROR="wf_load_plugin: workflow_type argument is required"
        return 1
    fi

    WF_TYPE="$_workflow_type"

    # Resolve the workflows root.
    local _root=""
    local _root_exit=0
    set +e
    _root="$(_wf_resolve_workflows_root "$_workflows_dir")"
    _root_exit=$?
    set -e
    if [[ $_root_exit -ne 0 || -z "$_root" ]]; then
        WF_LOAD_ERROR="workflow type '${_workflow_type}': cannot resolve workflows root (no valid directory found)"
        return 1
    fi

    # Discovery scan: look for a plugin directory matching the type.
    local _plugin_dir="${_root}/${_workflow_type}"
    if [[ ! -d "$_plugin_dir" ]]; then
        WF_LOAD_ERROR="workflow type '${_workflow_type}': no plugin directory found at ${_plugin_dir} (scanned root: ${_root})"
        return 1
    fi

    # Validate the manifest.
    local _manifest="${_plugin_dir}/workflow.cfg"
    if ! _wf_parse_manifest "$_manifest" "$_workflow_type"; then
        # WF_LOAD_ERROR already set by _wf_parse_manifest.
        return 1
    fi

    # Check the plugin script exists.
    local _plugin_sh="${_plugin_dir}/workflow.sh"
    if [[ ! -f "$_plugin_sh" ]]; then
        WF_LOAD_ERROR="workflow type '${_workflow_type}': plugin script not found at ${_plugin_sh}"
        return 1
    fi
    if [[ ! -r "$_plugin_sh" ]]; then
        WF_LOAD_ERROR="workflow type '${_workflow_type}': plugin script not readable at ${_plugin_sh}"
        return 1
    fi

    # Source the plugin. The plugin is expected to define (at minimum) the
    # wf_* functions it supports. Sourcing in the current shell replaces
    # the stub definitions with the plugin's implementations.
    # shellcheck disable=SC1090
    if ! source "$_plugin_sh"; then
        WF_LOAD_ERROR="workflow type '${_workflow_type}': failed to source plugin at ${_plugin_sh}"
        return 1
    fi

    WF_PLUGIN_DIR="$_plugin_dir"
    WF_LOAD_ERROR=""

    # Expose version_semantics via the public accessor.  The value comes from
    # the manifest [capabilities] version_semantics field, which _wf_parse_manifest
    # stores in WF_MANIFEST_VERSION_SEMANTICS.  The plugin does not need to
    # restate it in a hook function — the dispatcher owns this accessor and
    # reads it from the module-level state variable.
    # shellcheck disable=SC2120
    wf_version_semantics() {
        echo "${WF_MANIFEST_VERSION_SEMANTICS}"
    }

    return 0
}
