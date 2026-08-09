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
# RC-branch-aware resolution (tiers a-relative and c)
# ----------------------------------------------------
# Tiers (a)-with-a-relative-path and (c) both name a script that lives inside
# the dev tree's own git history.  When the caller supplies an rc_branch (see
# Public API below), those two tiers resolve the script's content from that
# branch's git tree via `git show <rc_branch>:<path>` rather than from
# whatever the dev tree's working copy currently has checked out.  This makes
# hook discovery a function of the RC being released, never of the branch an
# operator or a prior script step happened to leave checked out — no dev-tree
# parking workaround required.  The resolved content is materialized into a
# framework temp file (via pgai_mktemp) so it can still be handed to the
# caller as an executable path; CM_RESOLVED_HOOK_IS_TEMP tells the caller
# whether that materialized file should be removed after the hook runs.
# Tier (b) is kanban-side (not part of the dev tree's git history) and tier
# (a) with an absolute path point outside the repo entirely — neither is
# affected by which branch the dev tree has checked out, so both keep the
# original filesystem-based resolution.  When rc_branch is omitted (legacy
# callers), tiers (a)-relative and (c) fall back to the original
# filesystem-based resolution against the dev tree's current checkout.
#
# Public API
# ----------
#   cm_resolve_and_enforce_hook <project_name> <phase> <project_hooks_dir> <dev_tree_path> [rc_branch]
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
#       Sets the global variable CM_RESOLVED_HOOK_IS_TEMP to "1" when
#       CM_RESOLVED_HOOK_PATH points at a materialized temp file (resolved from
#       rc_branch's git tree) that the caller should remove after running the
#       hook, or "0" otherwise (a persistent path on disk; do not remove it).
#
#       rc_branch (optional): the RC branch whose git tree tiers (a)-relative
#       and (c) resolve against.  Omit for legacy filesystem-based resolution.
#
#       Required-flag enforcement: reads cm_release_<phase>_hook_required from
#       project.cfg.  When true and no hook is found at any tier, calls cm_halt
#       (which the caller must have defined before sourcing this library) with a
#       message naming the phase, all three searched locations, and the config key.
#       cm_halt is expected to exit non-zero.
#
#       Non-executable in-repo hook: when tier (c) (or tier (a)-relative with
#       rc_branch set) resolves a path and the file exists (or its RC-branch
#       git blob exists) but is not executable, prints a full three-part
#       remedy block (docs/coding-standards.md "Gates prescribe, not just
#       diagnose": Cause(s), a numbered To-fix command block, and the Rule
#       line) to stderr, then returns 1.  The caller must handle the non-zero
#       return (typically by halting the release).
#
#       On that same failure path, also sets two global variables so the
#       caller can build a placeholder-free HALT reason string without
#       re-deriving the path or the fix command:
#         CM_HOOK_NONEXEC_PATH     — the offending hook path (with an
#                                    "(on <rc_branch>)" suffix when resolved
#                                    from an RC branch's git tree rather than
#                                    the working-tree filesystem).
#         CM_HOOK_NONEXEC_FIX_CMD  — the verbatim-runnable fix command
#                                    (chmod +x ... or git update-index
#                                    --chmod=+x ... && git commit ...).
#       Both are reset to "" at the top of every call, including successful
#       ones, so a caller reading them after a return-0 call sees the empty
#       defaults rather than a stale value from a prior invocation.
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
# The caller (release.sh / ship-rc.sh) must also define
# cm_halt <error_key> <message> <remedy> (the dual-channel error contract —
# see team/scripts/lib/error_contract.sh)
# before any cm_resolve_and_enforce_hook call.
#
# Source order
# ------------
#   source project_paths.sh          # first
#   source projects.sh               # provides projects_resolve_release_hook_path (optional)
#   source cm_release_hooks.sh       # last
# temp.sh (pgai_mktemp) is self-sourced below when not already loaded.

# Guard against double-sourcing.
if declare -F cm_resolve_and_enforce_hook >/dev/null 2>&1; then
    return 0
fi

# Self-source temp.sh for pgai_mktemp (materializing RC-branch-resolved hook
# content into a framework-managed temp file). Guarded: no-op if the caller
# already sourced it.
if ! command -v pgai_mktemp >/dev/null 2>&1; then
    # shellcheck source=temp.sh
    source "$(dirname "${BASH_SOURCE[0]}")/temp.sh"
fi

# ---------------------------------------------------------------------------
# _creh_git_resolve_from_ref <dev_tree_path> <ref> <relative_path>
#
# Resolves *relative_path* from the git tree at *ref* (typically the RC
# branch being released) instead of from the dev tree's current checkout.
#
# On success: materializes the blob's content into a framework temp file
# (pgai_mktemp), marks it executable on disk, and sets:
#   _CREH_GIT_RESOLVED_FILE  — path to the materialized temp file
#   _CREH_GIT_RESOLVED_MODE  — the blob's git mode ("100755" or "100644")
# Returns 0.
#
# When *relative_path* does not exist in *ref*'s tree, sets both globals to
# empty and returns 1 — the caller treats this identically to "not found at
# this tier" and falls through to the next tier.
# ---------------------------------------------------------------------------
_creh_git_resolve_from_ref() {
    local _gr_dev_tree="$1"
    local _gr_ref="$2"
    local _gr_relpath="$3"

    _CREH_GIT_RESOLVED_FILE=""
    _CREH_GIT_RESOLVED_MODE=""

    local _gr_ls_tree
    _gr_ls_tree="$(git -C "$_gr_dev_tree" ls-tree "$_gr_ref" -- "$_gr_relpath" 2>/dev/null)"
    if [[ -z "$_gr_ls_tree" ]]; then
        return 1
    fi

    # ls-tree line shape: "<mode> blob <sha>\t<path>" — mode is the first field.
    local _gr_mode
    _gr_mode="$(awk '{print $1}' <<< "$_gr_ls_tree")"

    local _gr_tmp
    _gr_tmp="$(pgai_mktemp "cm-release-hook")"
    if ! git -C "$_gr_dev_tree" show "${_gr_ref}:${_gr_relpath}" > "$_gr_tmp" 2>/dev/null; then
        rm -f "$_gr_tmp"
        return 1
    fi
    chmod +x "$_gr_tmp"

    _CREH_GIT_RESOLVED_FILE="$_gr_tmp"
    _CREH_GIT_RESOLVED_MODE="$_gr_mode"
    return 0
}

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
    local _creh_rc_branch="${5:-}"

    # Default the "is a materialized temp file" flag; overwritten below when a
    # git-ref resolution succeeds.  Caller reads this after every call.
    CM_RESOLVED_HOOK_IS_TEMP="0"

    # Default the non-executable-hook remedy globals; overwritten below only
    # on the non-executable-hook failure path (return 1).  A caller building a
    # HALT message from these after a successful (return 0) call sees the
    # empty defaults, which is correct — no remedy applies when nothing failed.
    CM_HOOK_NONEXEC_PATH=""
    CM_HOOK_NONEXEC_FIX_CMD=""

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
        if [[ "$_creh_cfg_raw" == /* ]]; then
            # Absolute path: lives outside the dev tree's git history (e.g. a
            # shared ops script) — not RC-branch-relative. Read from disk as-is.
            _creh_resolved_path="$_creh_cfg_raw"
            _creh_source_label="cfg"
        elif [[ -n "$_creh_rc_branch" ]]; then
            # Relative path: versioned inside the dev tree, so resolve it from
            # the RC branch's git tree rather than the dev tree's current
            # checkout (see "RC-branch-aware resolution" above).
            if _creh_git_resolve_from_ref "$_creh_dev_tree_path" "$_creh_rc_branch" "$_creh_cfg_raw"; then
                if [[ "$_CREH_GIT_RESOLVED_MODE" != "100755" ]]; then
                    CM_HOOK_NONEXEC_PATH="${_creh_cfg_raw} (on ${_creh_rc_branch})"
                    CM_HOOK_NONEXEC_FIX_CMD="git update-index --chmod=+x ${_creh_cfg_raw} && git commit -m 'chmod +x ${_creh_cfg_raw}'  # run on ${_creh_rc_branch}"
                    echo "[cm-release] ERROR: cfg-declared hook: FAIL — hook blob exists on ${_creh_rc_branch} but is not executable (git mode ${_CREH_GIT_RESOLVED_MODE})" >&2
                    echo "[cm-release] ERROR:" >&2
                    echo "[cm-release] ERROR: Cause(s):" >&2
                    echo "[cm-release] ERROR:   - ${_creh_cfg_raw} on ${_creh_rc_branch} — git mode ${_CREH_GIT_RESOLVED_MODE}, expected 100755" >&2
                    echo "[cm-release] ERROR:" >&2
                    echo "[cm-release] ERROR: To fix:" >&2
                    echo "[cm-release] ERROR:   1. git update-index --chmod=+x ${_creh_cfg_raw}" >&2
                    echo "[cm-release] ERROR:   2. git commit -m \"chmod +x ${_creh_cfg_raw}\"   # run on ${_creh_rc_branch}" >&2
                    echo "[cm-release] ERROR:   3. Re-run cm-release.sh to re-resolve the ${_creh_phase} hook." >&2
                    echo "[cm-release] ERROR:" >&2
                    echo "[cm-release] ERROR: Rule: a hook committed to the RC branch must carry the executable git mode (100755), or the release cannot invoke it deterministically." >&2
                    CM_RESOLVED_HOOK_PATH=""
                    return 1
                fi
                _creh_resolved_path="$_CREH_GIT_RESOLVED_FILE"
                _creh_source_label="cfg (${_creh_rc_branch})"
                CM_RESOLVED_HOOK_IS_TEMP="1"
            fi
            # else: the RC branch's tree does not contain this path — treat as
            # unconfigured at this tier and fall through to tiers (b)/(c) below.
        else
            # No RC branch supplied (legacy caller) — fall back to a plain
            # filesystem check against the dev tree's current checkout.
            local _creh_fs_path="${_creh_dev_tree_path}/${_creh_cfg_raw}"
            if [[ -f "$_creh_fs_path" ]]; then
                _creh_resolved_path="$_creh_fs_path"
                _creh_source_label="cfg"
            fi
        fi
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
        local _creh_in_repo_relpath=".pgai/hooks/cm-release-${_creh_phase}.sh"
        if [[ -n "$_creh_rc_branch" ]]; then
            # Resolve from the RC branch's git tree, not the dev tree's current
            # checkout — the operator workaround this replaces was manually
            # parking the dev tree on the RC before every release.
            if _creh_git_resolve_from_ref "$_creh_dev_tree_path" "$_creh_rc_branch" "$_creh_in_repo_relpath"; then
                if [[ "$_CREH_GIT_RESOLVED_MODE" != "100755" ]]; then
                    CM_HOOK_NONEXEC_PATH="${_creh_in_repo_relpath} (on ${_creh_rc_branch})"
                    CM_HOOK_NONEXEC_FIX_CMD="git update-index --chmod=+x ${_creh_in_repo_relpath} && git commit -m 'chmod +x ${_creh_in_repo_relpath}'  # run on ${_creh_rc_branch}"
                    echo "[cm-release] ERROR: in-repo hook: FAIL — hook blob exists on ${_creh_rc_branch} but is not executable (git mode ${_CREH_GIT_RESOLVED_MODE})" >&2
                    echo "[cm-release] ERROR:" >&2
                    echo "[cm-release] ERROR: Cause(s):" >&2
                    echo "[cm-release] ERROR:   - ${_creh_in_repo_relpath} on ${_creh_rc_branch} — git mode ${_CREH_GIT_RESOLVED_MODE}, expected 100755" >&2
                    echo "[cm-release] ERROR:" >&2
                    echo "[cm-release] ERROR: To fix:" >&2
                    echo "[cm-release] ERROR:   1. git update-index --chmod=+x ${_creh_in_repo_relpath}" >&2
                    echo "[cm-release] ERROR:   2. git commit -m \"chmod +x ${_creh_in_repo_relpath}\"   # run on ${_creh_rc_branch}" >&2
                    echo "[cm-release] ERROR:   3. Re-run cm-release.sh to re-resolve the ${_creh_phase} hook." >&2
                    echo "[cm-release] ERROR:" >&2
                    echo "[cm-release] ERROR: Rule: a hook committed to the RC branch must carry the executable git mode (100755), or the release cannot invoke it deterministically." >&2
                    CM_RESOLVED_HOOK_PATH=""
                    return 1
                fi
                _creh_resolved_path="$_CREH_GIT_RESOLVED_FILE"
                _creh_source_label="in-repo (${_creh_rc_branch})"
                CM_RESOLVED_HOOK_IS_TEMP="1"
            fi
        else
            local _creh_in_repo="${_creh_dev_tree_path}/${_creh_in_repo_relpath}"
            if [[ -f "$_creh_in_repo" ]]; then
                # Non-executable in-repo hook is an ERROR (fail-loud), not a silent skip.
                if [[ ! -x "$_creh_in_repo" ]]; then
                    CM_HOOK_NONEXEC_PATH="${_creh_in_repo}"
                    CM_HOOK_NONEXEC_FIX_CMD="chmod +x ${_creh_in_repo}"
                    echo "[cm-release] ERROR: in-repo hook: FAIL — hook file exists but is not executable" >&2
                    echo "[cm-release] ERROR:" >&2
                    echo "[cm-release] ERROR: Cause(s):" >&2
                    echo "[cm-release] ERROR:   - ${_creh_in_repo} — file mode denies execute permission" >&2
                    echo "[cm-release] ERROR:" >&2
                    echo "[cm-release] ERROR: To fix:" >&2
                    echo "[cm-release] ERROR:   1. chmod +x ${_creh_in_repo}" >&2
                    echo "[cm-release] ERROR:   2. Re-run cm-release.sh to re-resolve the ${_creh_phase} hook." >&2
                    echo "[cm-release] ERROR:" >&2
                    echo "[cm-release] ERROR: Rule: a hook file that exists at a resolved tier must be executable, or the release cannot invoke it deterministically." >&2
                    CM_RESOLVED_HOOK_PATH=""
                    return 1
                fi
                _creh_resolved_path="$_creh_in_repo"
                _creh_source_label="in-repo"
            fi
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
        local _creh_loc_inrepo_qualifier=" (dev tree working copy)"
        [[ -n "$_creh_rc_branch" ]] && _creh_loc_inrepo_qualifier=" (as of ${_creh_rc_branch})"

        CM_RESOLVED_HOOK_PATH=""
        cm_halt \
            "cm-release.hook-required-missing.${_creh_phase}" \
            "HALT before ${_creh_phase} phase: ${_creh_required_field_name}=true but no hook was found at any tier. Searched: (cfg) ${_creh_loc_cfg}; (kanban-side) ${_creh_loc_kanban}; (in-repo) ${_creh_loc_inrepo}${_creh_loc_inrepo_qualifier}." \
            "Add the hook at one of the searched locations above, or set ${_creh_required_field_name}=false in project.cfg, then re-run the release script."
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
