#!/usr/bin/env bash
# team/scripts/lib/workflow-contract.sh
#
# Shared contract-check library for pgai-agent-kanban workflow plugins.
#
# This library validates that a workflow plugin directory satisfies the
# four contractual requirements every plugin must meet before it can be
# used in production:
#
#   1. Manifest validity    — workflow.cfg exists, has required fields,
#                             status is "ready" (not "scaffold" or absent).
#   2. Hook presence        — workflow.sh exists and defines all eight
#                             wf_* hook functions.
#   3. Stub detection       — no hook body contains "NOT IMPLEMENTED",
#                             which marks a generated scaffold not yet
#                             implemented by the plugin author.
#   4. Capability validity  — capability field values are in the allowed
#                             sets for git_mode, version_semantics, and
#                             finalize.
#
# Usage
# -----
# Source this file, then call any of the four individual checks or the
# combined check:
#
#   source "$(dirname "${BASH_SOURCE[0]}")/workflow-contract.sh"
#
#   wfc_check_all "/path/to/workflows/root" "my-type"
#   if [[ $? -ne 0 ]]; then
#       echo "Contract violation: $WFC_ERROR"
#       exit 1
#   fi
#
# Public API
# ----------
#   wfc_check_all <plugin_dir> <type_name>
#       Run all four contract checks. Returns 0 when all pass.
#       Returns non-zero on the first failure; WFC_ERROR names the problem.
#
#   wfc_check_manifest <plugin_dir> <type_name>
#       Validate workflow.cfg manifest: file present, required fields
#       present and non-empty, status = ready.
#       Returns 0 on pass, non-zero on failure (WFC_ERROR set).
#
#   wfc_check_hooks <plugin_dir> <type_name>
#       Verify workflow.sh exists and defines all required wf_* hooks.
#       Returns 0 on pass, non-zero on failure (WFC_ERROR set).
#
#   wfc_check_stubs <plugin_dir> <type_name>
#       Detect scaffold stubs: hook bodies that contain "NOT IMPLEMENTED".
#       Returns 0 when no stubs found, non-zero when stubs are present
#       (WFC_ERROR names the offending hooks).
#
#   wfc_check_capabilities <plugin_dir> <type_name>
#       Validate capability field values against allowed sets.
#       Returns 0 on pass, non-zero on failure (WFC_ERROR set).
#
# State variables set by each check
# ----------------------------------
#   WFC_ERROR   Human-readable description of the failure (empty on pass).
#               Each check resets this at entry.
#
# Safety invariants
# -----------------
#   - No top-level side effects when sourced — only function definitions.
#   - All state variables are set (not just printed) so callers can inspect
#     failure details without capturing stdout.
#   - Checks are pure read-only: they never modify the plugin directory.
#   - ini_parser.sh must be locatable at the same lib/ directory as this
#     file, or read_ini must already be available in the caller's scope.

# ---------------------------------------------------------------------------
# Bootstrap: ensure read_ini is available.
# ---------------------------------------------------------------------------
if ! command -v read_ini >/dev/null 2>&1; then
    # shellcheck source=ini_parser.sh
    source "$(dirname "${BASH_SOURCE[0]}")/ini_parser.sh"
fi

# ---------------------------------------------------------------------------
# State variable (reset by each check at entry).
# ---------------------------------------------------------------------------
WFC_ERROR=""

# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

# The full set of wf_* hook names every plugin must define.
_WFC_REQUIRED_HOOKS=(
    wf_resolve_target_version
    wf_git_mode
    wf_pre_task
    wf_post_task
    wf_finalize
    wf_agents
    wf_bundle_source_branch
    wf_dashboard_render
)

# Allowed values for capability fields.
_WFC_VALID_GIT_MODES="none ro rw"
_WFC_VALID_VERSION_SEMANTICS="semver label none"
_WFC_VALID_FINALIZE="tag publish report"

# ---------------------------------------------------------------------------
# _wfc_in_set <value> <allowed_values_space_separated>
#
# Returns 0 when value is in the allowed set, non-zero otherwise.
# ---------------------------------------------------------------------------
_wfc_in_set() {
    local value="$1"
    local allowed="$2"
    local item
    for item in $allowed; do
        if [[ "$item" == "$value" ]]; then
            return 0
        fi
    done
    return 1
}

# ---------------------------------------------------------------------------
# wfc_check_manifest <plugin_dir> <type_name>
#
# Validates the workflow.cfg manifest for the plugin at <plugin_dir>.
#
# Checks:
#   - workflow.cfg exists and is readable
#   - [workflow] name is present
#   - [workflow] status is present and equals "ready" (not "scaffold")
#   - [capabilities] git_mode is present
#   - [capabilities] agents is present
#
# Returns 0 on pass.
# Returns 1 and sets WFC_ERROR on the first violation found.
# ---------------------------------------------------------------------------
wfc_check_manifest() {
    local plugin_dir="$1"
    local type_name="$2"
    WFC_ERROR=""

    local manifest="${plugin_dir}/workflow.cfg"

    # Guard: manifest must exist.
    if [[ ! -e "$manifest" ]]; then
        WFC_ERROR="workflow type '${type_name}': manifest not found at ${manifest}"
        return 1
    fi

    # Guard: manifest must be readable.
    if [[ ! -r "$manifest" ]]; then
        WFC_ERROR="workflow type '${type_name}': manifest not readable at ${manifest}"
        return 1
    fi

    # Read required fields.
    local _name
    _name="$(read_ini "$manifest" workflow name "")"
    local _status
    _status="$(read_ini "$manifest" workflow status "")"
    local _git_mode
    _git_mode="$(read_ini "$manifest" capabilities git_mode "")"
    local _agents
    _agents="$(read_ini "$manifest" capabilities agents "")"

    # [workflow] name must be present.
    if [[ -z "$_name" ]]; then
        WFC_ERROR="workflow type '${type_name}': manifest missing [workflow] name in ${manifest}"
        return 1
    fi

    # [workflow] status must be present.
    if [[ -z "$_status" ]]; then
        WFC_ERROR="workflow type '${type_name}': manifest missing [workflow] status in ${manifest}"
        return 1
    fi

    # Fail-closed: scaffold status means the plugin is not ready for use.
    if [[ "$_status" == "scaffold" ]]; then
        WFC_ERROR="workflow type '${type_name}': plugin status is 'scaffold' — implement all hooks and flip status to 'ready' before use"
        return 1
    fi

    # Status must be "ready".
    if [[ "$_status" != "ready" ]]; then
        WFC_ERROR="workflow type '${type_name}': manifest [workflow] status must be 'ready' or 'scaffold', got '${_status}' in ${manifest}"
        return 1
    fi

    # [capabilities] git_mode must be present.
    if [[ -z "$_git_mode" ]]; then
        WFC_ERROR="workflow type '${type_name}': manifest missing [capabilities] git_mode in ${manifest}"
        return 1
    fi

    # [capabilities] agents must be present.
    if [[ -z "$_agents" ]]; then
        WFC_ERROR="workflow type '${type_name}': manifest missing [capabilities] agents in ${manifest}"
        return 1
    fi

    return 0
}

# ---------------------------------------------------------------------------
# wfc_check_hooks <plugin_dir> <type_name>
#
# Verifies that the plugin's workflow.sh exists and defines all required
# wf_* hook functions.
#
# Detection: each hook is expected to appear in the workflow.sh file as a
# function definition matching the pattern:
#   <hook_name>() or function <hook_name>
#
# Returns 0 when all required hooks are defined.
# Returns 1 and sets WFC_ERROR listing the first missing hook when any
# are absent.
# ---------------------------------------------------------------------------
wfc_check_hooks() {
    local plugin_dir="$1"
    local type_name="$2"
    WFC_ERROR=""

    local plugin_sh="${plugin_dir}/workflow.sh"

    # Guard: plugin script must exist.
    if [[ ! -e "$plugin_sh" ]]; then
        WFC_ERROR="workflow type '${type_name}': plugin script not found at ${plugin_sh}"
        return 1
    fi

    # Guard: plugin script must be readable.
    if [[ ! -r "$plugin_sh" ]]; then
        WFC_ERROR="workflow type '${type_name}': plugin script not readable at ${plugin_sh}"
        return 1
    fi

    # Check each required hook is defined.
    local missing=()
    local hook
    for hook in "${_WFC_REQUIRED_HOOKS[@]}"; do
        # Match bash function definition patterns:
        #   hookname() { ...
        #   hookname () { ...
        #   function hookname() { ...
        #   function hookname { ...
        # grep -F (fixed string) on the function name avoids ERE escaping
        # issues, then awk confirms it is a function definition line.
        local _found
        _found=$(grep -n "$hook" "$plugin_sh" 2>/dev/null | awk -v h="$hook" '
            {
                line = $0
                sub(/^[0-9]+:/, "", line)
                # Strip leading whitespace
                stripped = line
                sub(/^[[:space:]]+/, "", stripped)
                # Strip optional "function " prefix
                sub(/^function[[:space:]]+/, "", stripped)
                # The line is a function header if it starts with the hook name
                # followed immediately by "(" or " (" or "()"
                if (index(stripped, h "(") == 1 || index(stripped, h " (") == 1) {
                    print "found"
                    exit
                }
            }
        ')
        if [[ "$_found" != "found" ]]; then
            missing+=("$hook")
        fi
    done

    if [[ ${#missing[@]} -gt 0 ]]; then
        local missing_list
        missing_list="$(printf '%s, ' "${missing[@]}")"
        missing_list="${missing_list%, }"
        WFC_ERROR="workflow type '${type_name}': plugin script missing required hook(s): ${missing_list} (in ${plugin_sh})"
        return 1
    fi

    return 0
}

# ---------------------------------------------------------------------------
# wfc_check_stubs <plugin_dir> <type_name>
#
# Detects scaffold stubs in the plugin's workflow.sh. A stub is a hook whose
# body contains the literal string "NOT IMPLEMENTED" — the marker placed by
# the workflow generator in scaffold outputs to indicate the hook has not
# been implemented yet.
#
# A plugin with any stubs present is not ready for production use. The
# validator must reject it with a message naming the stubs found.
#
# Returns 0 when no stubs are found.
# Returns 1 and sets WFC_ERROR listing the hooks with stubs when any are
# found.
# ---------------------------------------------------------------------------
wfc_check_stubs() {
    local plugin_dir="$1"
    local type_name="$2"
    WFC_ERROR=""

    local plugin_sh="${plugin_dir}/workflow.sh"

    # Guard: plugin script must exist (wfc_check_hooks verifies this in depth;
    # here we just return cleanly if it is absent so the caller can sequence
    # checks without redundant error messages).
    if [[ ! -r "$plugin_sh" ]]; then
        WFC_ERROR="workflow type '${type_name}': plugin script not readable at ${plugin_sh}"
        return 1
    fi

    # Scan for hooks whose bodies contain "NOT IMPLEMENTED".
    # Strategy: parse the file into per-function blocks, then check each block.
    #
    # Simple heuristic: for each required hook, extract the lines in the file
    # between its function definition and the next function definition (or EOF),
    # and search that block for "NOT IMPLEMENTED".
    local stubbed=()
    local hook
    local content
    content="$(cat "$plugin_sh")"

    for hook in "${_WFC_REQUIRED_HOOKS[@]}"; do
        # Use awk to extract the function body for this hook and check for stubs.
        # Match from the function definition line until the closing brace at the
        # start of a line (a sufficient heuristic for bash functions with
        # top-level `}` closers).
        #
        # Build the function header pattern as a string comparison rather than
        # a dynamic regex to avoid awk ERE escaping issues with parentheses in
        # hook names that contain no special characters but whose context
        # string concatenation could produce unbalanced `(` in the pattern.
        local in_stub
        in_stub=$(awk \
            -v hookname="$hook" \
            '
            function is_func_header(line,    stripped) {
                stripped = line
                # Strip leading whitespace
                sub(/^[[:space:]]+/, "", stripped)
                # Strip optional "function " prefix
                sub(/^function[[:space:]]+/, "", stripped)
                # Check that the stripped line starts with hookname followed by ( or whitespace+(
                return (index(stripped, hookname "(") == 1 || \
                        index(stripped, hookname " (") == 1 || \
                        index(stripped, hookname "() ") == 1 || \
                        index(stripped, hookname "()") == 1)
            }
            BEGIN { in_func=0; found=0 }
            is_func_header($0) { in_func=1; next }
            in_func && /NOT IMPLEMENTED/ { found=1 }
            # A top-level closing brace ends the function body.
            in_func && /^\}[[:space:]]*$/ { in_func=0 }
            END { print found }
            ' "$plugin_sh")

        if [[ "$in_stub" == "1" ]]; then
            stubbed+=("$hook")
        fi
    done

    if [[ ${#stubbed[@]} -gt 0 ]]; then
        local stub_list
        stub_list="$(printf '%s, ' "${stubbed[@]}")"
        stub_list="${stub_list%, }"
        WFC_ERROR="workflow type '${type_name}': plugin has unimplemented scaffold stubs in hook(s): ${stub_list} — implement the hooks and remove the 'NOT IMPLEMENTED' markers before use"
        return 1
    fi

    return 0
}

# ---------------------------------------------------------------------------
# wfc_check_capabilities <plugin_dir> <type_name>
#
# Validates that capability field values in the manifest are from the
# allowed sets:
#   git_mode         : none | ro | rw
#   version_semantics: semver | label | none
#   finalize         : tag | publish | report  (when non-empty)
#
# version_semantics and finalize are optional fields; they are checked only
# when present (non-empty).
#
# Returns 0 when all present values are valid.
# Returns 1 and sets WFC_ERROR on the first invalid value found.
# ---------------------------------------------------------------------------
wfc_check_capabilities() {
    local plugin_dir="$1"
    local type_name="$2"
    WFC_ERROR=""

    local manifest="${plugin_dir}/workflow.cfg"

    # Guard: manifest must be readable (prerequisite; manifest check should
    # have run first, but be defensive).
    if [[ ! -r "$manifest" ]]; then
        WFC_ERROR="workflow type '${type_name}': manifest not readable at ${manifest}"
        return 1
    fi

    local _git_mode
    _git_mode="$(read_ini "$manifest" capabilities git_mode "")"
    local _version_semantics
    _version_semantics="$(read_ini "$manifest" capabilities version_semantics "")"
    local _finalize
    _finalize="$(read_ini "$manifest" capabilities finalize "")"

    # git_mode: required; must be in the allowed set.
    if [[ -z "$_git_mode" ]]; then
        WFC_ERROR="workflow type '${type_name}': manifest missing [capabilities] git_mode in ${manifest}"
        return 1
    fi
    if ! _wfc_in_set "$_git_mode" "$_WFC_VALID_GIT_MODES"; then
        WFC_ERROR="workflow type '${type_name}': invalid [capabilities] git_mode '${_git_mode}' (allowed: ${_WFC_VALID_GIT_MODES}) in ${manifest}"
        return 1
    fi

    # version_semantics: optional; validate when present.
    if [[ -n "$_version_semantics" ]]; then
        if ! _wfc_in_set "$_version_semantics" "$_WFC_VALID_VERSION_SEMANTICS"; then
            WFC_ERROR="workflow type '${type_name}': invalid [capabilities] version_semantics '${_version_semantics}' (allowed: ${_WFC_VALID_VERSION_SEMANTICS}) in ${manifest}"
            return 1
        fi
    fi

    # finalize: optional; validate when present.
    if [[ -n "$_finalize" ]]; then
        if ! _wfc_in_set "$_finalize" "$_WFC_VALID_FINALIZE"; then
            WFC_ERROR="workflow type '${type_name}': invalid [capabilities] finalize '${_finalize}' (allowed: ${_WFC_VALID_FINALIZE}) in ${manifest}"
            return 1
        fi
    fi

    return 0
}

# ---------------------------------------------------------------------------
# wfc_check_all <plugin_dir> <type_name>
#
# Runs all four contract checks in sequence:
#   1. wfc_check_manifest
#   2. wfc_check_hooks
#   3. wfc_check_stubs
#   4. wfc_check_capabilities
#
# Stops at the first failure. WFC_ERROR is set to the failure message from
# whichever check failed.
#
# Returns 0 when all four checks pass.
# Returns 1 on the first failure.
# ---------------------------------------------------------------------------
wfc_check_all() {
    local plugin_dir="$1"
    local type_name="$2"
    WFC_ERROR=""

    wfc_check_manifest "$plugin_dir" "$type_name" || return 1
    wfc_check_hooks    "$plugin_dir" "$type_name" || return 1
    wfc_check_stubs    "$plugin_dir" "$type_name" || return 1
    wfc_check_capabilities "$plugin_dir" "$type_name" || return 1

    return 0
}
