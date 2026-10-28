#!/usr/bin/env bash
# team/scripts/lib/cm_release_hooks.sh
#
# Shared library for CM release hook resolution, visibility printing, and
# required-hook enforcement.  Used by both release.sh and the ship-rc verb
# (pgai_agent_kanban/cm/ship.py, which sources this file in a bash child and
# reads the resolved path back) so that hook logic lives in exactly one
# implementation (one-implementation rule).
#
# Precedence order (highest to lowest):
#   (a) project.cfg [hooks] cm_release_<phase>_hook          (cfg)
#   (b) projects/<name>/hooks/cm-release-<phase>.sh          (kanban-side)
#   (c) <dev_tree_path>/.pgai/hooks/cm-release-<phase>.sh   (in-repo)
# Deployment overrides app default — the highest-precedence tier that supplies
# a path wins.
#
# Hook execution is delegated to _run_release_hook (defined in
# lib/cm/release_finalize.sh for release.sh's callers) and to
# pgai_agent_kanban/cm/ship.py's _run_hook for ship-rc.  This library does
# NOT execute hooks itself; it resolves, prints, and enforces.
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
# Symlinked hooks on the RC branch
# --------------------------------
# A tier (a)-relative or tier (c) hook committed as a symlink (git mode
# 120000) is resolved to its target on the same ref: the link text is read
# from the blob, taken relative to the link's own directory, and followed
# (up to 8 links) to a committed regular file.  That target's mode is checked
# exactly as a regular hook's is, and the materialized file holds the
# target's content, never the link text.  When the target is 100644 the
# refusal names `git update-index --chmod=+x <target>`; when the link cannot
# be followed to a committed regular file (target missing on the ref, outside
# the repository, a directory or submodule, or the chain is too long) the
# refusal's remedy removes the link with `git rm`.  Any other non-file entry
# at the hook path (a directory or a submodule) gets the same `git rm`
# remedy.  `git update-index --chmod=+x` is printed only for a 100644 blob,
# the one mode it can fix.  The no-rc_branch filesystem path uses
# [[ -f ]] / [[ -x ]], which already follow symlinks, so `chmod +x` on the
# link fixes its target there.
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
#       git entry exists) but is not executable — or, on the RC branch, is a
#       symlink that does not reach a committed 100755 regular file, or is
#       not a file at all — prints a full three-part
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
#                                    (chmod +x ..., git update-index
#                                    --chmod=+x ... && git commit ..., or
#                                    git rm -r --quiet -- ... && git commit
#                                    ... for an unresolvable symlink or a
#                                    non-file entry).  RC-branch commands
#                                    run from the dev tree root with the
#                                    RC branch checked out.
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
# The caller (release.sh / cm/ship.py's resolver child) must also define
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

# Most symlinks followed when resolving one RC-branch hook path; a longer
# chain (or a cycle) is refused as unresolvable rather than looped on.
_CREH_SYMLINK_MAX_HOPS=8

# ---------------------------------------------------------------------------
# _creh_git_entry_mode <dev_tree_path> <ref> <relative_path>
#
# Prints the git mode of *relative_path* in *ref*'s tree (100644, 100755,
# 120000, 160000, 040000), or nothing and returns 1 when the path is absent.
# ---------------------------------------------------------------------------
_creh_git_entry_mode() {
    local _em_ls_tree
    _em_ls_tree="$(git -C "$1" ls-tree "$2" -- "$3" 2>/dev/null)"
    [[ -n "$_em_ls_tree" ]] || return 1
    # ls-tree line shape: "<mode> <type> <sha>\t<path>" — mode is the first field.
    awk '{print $1; exit}' <<< "$_em_ls_tree"
}

# ---------------------------------------------------------------------------
# _creh_link_target_relpath <link_relpath> <link_text>
#
# Prints the repo-relative path a symlink at *link_relpath* whose content is
# *link_text* points to, with "." and ".." segments folded.  Returns 1 (and
# prints nothing) when the target is absolute or climbs above the repository
# root — neither can name a blob on the same ref.
# ---------------------------------------------------------------------------
_creh_link_target_relpath() {
    local _lt_link="$1"
    local _lt_text="$2"
    [[ -n "$_lt_text" && "$_lt_text" != /* ]] || return 1

    local _lt_joined="$_lt_text"
    [[ "$_lt_link" == */* ]] && _lt_joined="${_lt_link%/*}/${_lt_text}"

    local -a _lt_parts=()
    local -a _lt_out=()
    local _lt_seg
    IFS='/' read -r -a _lt_parts <<< "$_lt_joined"
    for _lt_seg in "${_lt_parts[@]}"; do
        case "$_lt_seg" in
            ""|.) ;;
            ..)
                (( ${#_lt_out[@]} > 0 )) || return 1
                unset '_lt_out[-1]'
                ;;
            *) _lt_out+=("$_lt_seg") ;;
        esac
    done
    (( ${#_lt_out[@]} > 0 )) || return 1

    local IFS='/'
    printf '%s\n' "${_lt_out[*]}"
}

# ---------------------------------------------------------------------------
# _creh_git_resolve_from_ref <dev_tree_path> <ref> <relative_path>
#
# Resolves *relative_path* from the git tree at *ref* (typically the RC
# branch being released) instead of from the dev tree's current checkout.
# A symlink (git mode 120000) is followed to its target on the same ref (see
# "Symlinked hooks on the RC branch" above).
#
# Returns 1, with every global empty, when *relative_path* does not exist in
# *ref*'s tree — the caller treats this identically to "not found at this
# tier" and falls through to the next tier.  Otherwise returns 0 and sets:
#   _CREH_GIT_ENTRY_MODE         — git mode of *relative_path* itself.
#   _CREH_GIT_RESOLVED_TARGET    — repo-relative path of the regular file
#                                  that supplies the content (*relative_path*
#                                  itself unless it is a symlink); empty when
#                                  a symlink cannot be followed to one.
#   _CREH_GIT_RESOLVED_MODE      — git mode of that target; "100755" means
#                                  usable.  A symlink that cannot be followed
#                                  reports "120000".
#   _CREH_GIT_UNRESOLVED_REASON  — plain-language reason a symlink did not
#                                  reach a committed regular file, else empty.
#   _CREH_GIT_RESOLVED_FILE      — only when the mode is 100755: a framework
#                                  temp file (pgai_mktemp), marked executable,
#                                  holding the target blob's content.
# ---------------------------------------------------------------------------
_creh_git_resolve_from_ref() {
    local _gr_dev_tree="$1"
    local _gr_ref="$2"
    local _gr_relpath="$3"

    _CREH_GIT_RESOLVED_FILE=""
    _CREH_GIT_RESOLVED_MODE=""
    _CREH_GIT_ENTRY_MODE=""
    _CREH_GIT_RESOLVED_TARGET=""
    _CREH_GIT_UNRESOLVED_REASON=""

    local _gr_mode
    _gr_mode="$(_creh_git_entry_mode "$_gr_dev_tree" "$_gr_ref" "$_gr_relpath")" || return 1
    _CREH_GIT_ENTRY_MODE="$_gr_mode"

    local _gr_path="$_gr_relpath"
    local _gr_hops=0
    local _gr_text _gr_next
    while [[ "$_gr_mode" == "120000" ]]; do
        if (( _gr_hops >= _CREH_SYMLINK_MAX_HOPS )); then
            _CREH_GIT_UNRESOLVED_REASON="symlink chain is longer than ${_CREH_SYMLINK_MAX_HOPS} links"
            _CREH_GIT_RESOLVED_MODE="120000"
            return 0
        fi
        _gr_hops=$(( _gr_hops + 1 ))
        _gr_text="$(git -C "$_gr_dev_tree" cat-file blob "${_gr_ref}:${_gr_path}" 2>/dev/null)"
        if ! _gr_next="$(_creh_link_target_relpath "$_gr_path" "$_gr_text")"; then
            _CREH_GIT_UNRESOLVED_REASON="symlink ${_gr_path} points to '${_gr_text}', which is outside the repository"
            _CREH_GIT_RESOLVED_MODE="120000"
            return 0
        fi
        if ! _gr_mode="$(_creh_git_entry_mode "$_gr_dev_tree" "$_gr_ref" "$_gr_next")"; then
            _CREH_GIT_UNRESOLVED_REASON="symlink ${_gr_path} points to ${_gr_next}, which is not committed on ${_gr_ref}"
            _CREH_GIT_RESOLVED_MODE="120000"
            return 0
        fi
        _gr_path="$_gr_next"
    done

    if [[ "$_CREH_GIT_ENTRY_MODE" == "120000" && "$_gr_mode" != "100755" && "$_gr_mode" != "100644" ]]; then
        _CREH_GIT_UNRESOLVED_REASON="symlink target ${_gr_path} is not a regular file (git mode ${_gr_mode})"
        _CREH_GIT_RESOLVED_MODE="120000"
        return 0
    fi

    _CREH_GIT_RESOLVED_TARGET="$_gr_path"
    _CREH_GIT_RESOLVED_MODE="$_gr_mode"
    # Only a usable (100755) hook is materialized; the caller refuses any
    # other mode before it would read the file.
    [[ "$_gr_mode" == "100755" ]] || return 0

    local _gr_tmp
    _gr_tmp="$(pgai_mktemp "cm-release-hook")"
    if ! git -C "$_gr_dev_tree" show "${_gr_ref}:${_gr_path}" > "$_gr_tmp" 2>/dev/null; then
        rm -f "$_gr_tmp"
        _CREH_GIT_RESOLVED_TARGET=""
        _CREH_GIT_RESOLVED_MODE=""
        _CREH_GIT_ENTRY_MODE=""
        return 1
    fi
    chmod +x "$_gr_tmp"
    _CREH_GIT_RESOLVED_FILE="$_gr_tmp"
    return 0
}

# ---------------------------------------------------------------------------
# _creh_report_unusable_ref_hook <tier_label> <relative_path> <rc_branch> <phase>
#
# The refusal for an RC-branch hook that _creh_git_resolve_from_ref found but
# could not make usable (its resolved mode is not 100755).  Shared by tier
# (a)-relative and tier (c) so both refuse the same way.  Sets
# CM_HOOK_NONEXEC_PATH and CM_HOOK_NONEXEC_FIX_CMD and prints the
# Cause(s) / To fix / Rule block to stderr; the caller returns 1.
#
# The remedy depends on what the entry is:
#   100644 blob                  — git update-index --chmod=+x on the hook.
#   symlink to a 100644 blob     — git update-index --chmod=+x on the target.
#   anything else (unresolvable  — git rm -r on the hook path, which clears
#   symlink, directory, submodule)  the refusal; the tier then reads as empty.
# Each numbered git step runs as printed from the dev tree root with the RC
# branch checked out.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# _creh_quote_message <text>
#
# Prints <text> as one single-quoted shell word, each embedded single quote
# written as '\'' so the word survives any character a path can hold.  Used
# for the commit message inside a printed remedy; paths go through
# printf '%q', which leaves an already-safe path byte-identical.
# ---------------------------------------------------------------------------
_creh_quote_message() {
    local _qm_text="$1"
    printf "'%s'" "${_qm_text//\'/\'\\\'\'}"
}

_creh_report_unusable_ref_hook() {
    local _ru_label="$1"
    local _ru_relpath="$2"
    local _ru_rc="$3"
    local _ru_phase="$4"
    local _ru_entry_mode="$_CREH_GIT_ENTRY_MODE"
    local _ru_target="$_CREH_GIT_RESOLVED_TARGET"
    local _ru_mode="$_CREH_GIT_RESOLVED_MODE"

    local _ru_headline _ru_cause _ru_step1 _ru_commit_msg _ru_note=""
    if [[ "$_ru_mode" == "100644" && "$_ru_entry_mode" == "100644" ]]; then
        CM_HOOK_NONEXEC_PATH="${_ru_relpath} (on ${_ru_rc})"
        _ru_headline="hook blob exists on ${_ru_rc} but is not executable (git mode ${_ru_mode})"
        _ru_cause="${_ru_relpath} on ${_ru_rc} — git mode ${_ru_mode}, expected 100755"
        _ru_step1="git update-index --chmod=+x $(printf '%q' "$_ru_relpath")"
        _ru_commit_msg="chmod +x ${_ru_relpath}"
    elif [[ "$_ru_mode" == "100644" ]]; then
        CM_HOOK_NONEXEC_PATH="${_ru_relpath} (symlink to ${_ru_target}, on ${_ru_rc})"
        _ru_headline="hook on ${_ru_rc} is a symlink to ${_ru_target}, which is not executable (git mode ${_ru_mode})"
        _ru_cause="${_ru_relpath} on ${_ru_rc} — symlink to ${_ru_target}, git mode ${_ru_mode}, expected 100755"
        _ru_step1="git update-index --chmod=+x $(printf '%q' "$_ru_target")"
        _ru_commit_msg="chmod +x ${_ru_target}"
    else
        if [[ "$_ru_entry_mode" == "120000" ]]; then
            CM_HOOK_NONEXEC_PATH="${_ru_relpath} (symlink, on ${_ru_rc})"
            _ru_headline="hook on ${_ru_rc} is a symlink that does not reach a committed regular file"
            _ru_cause="${_ru_relpath} on ${_ru_rc} — ${_CREH_GIT_UNRESOLVED_REASON}"
        else
            CM_HOOK_NONEXEC_PATH="${_ru_relpath} (on ${_ru_rc})"
            _ru_headline="hook path on ${_ru_rc} is not a regular file (git mode ${_ru_entry_mode})"
            _ru_cause="${_ru_relpath} on ${_ru_rc} — git mode ${_ru_entry_mode}, expected a 100755 regular file"
        fi
        _ru_step1="git rm -r --quiet -- $(printf '%q' "$_ru_relpath")"
        _ru_commit_msg="Remove unusable release hook ${_ru_relpath}"
        _ru_note="To keep a ${_ru_phase} hook, commit an executable regular file (git mode 100755) at ${_ru_relpath} in place of the removed entry."
    fi
    # Every interpolated path is shell-quoted so the remedy runs as printed
    # whatever characters the hook or target path holds.
    local _ru_commit_word
    _ru_commit_word="$(_creh_quote_message "$_ru_commit_msg")"
    CM_HOOK_NONEXEC_FIX_CMD="${_ru_step1} && git commit -m ${_ru_commit_word}  # run on ${_ru_rc}"

    echo "[cm-release] ERROR: ${_ru_label}: FAIL — ${_ru_headline}" >&2
    echo "[cm-release] ERROR:" >&2
    echo "[cm-release] ERROR: Cause(s):" >&2
    echo "[cm-release] ERROR:   - ${_ru_cause}" >&2
    echo "[cm-release] ERROR:" >&2
    echo "[cm-release] ERROR: To fix:" >&2
    echo "[cm-release] ERROR:   1. ${_ru_step1}" >&2
    echo "[cm-release] ERROR:   2. git commit -m ${_ru_commit_word}   # run on ${_ru_rc}" >&2
    echo "[cm-release] ERROR:   3. Re-run scripts/cm/release.sh to re-resolve the ${_ru_phase} hook." >&2
    [[ -n "$_ru_note" ]] && echo "[cm-release] ERROR:   ${_ru_note}" >&2
    echo "[cm-release] ERROR:" >&2
    echo "[cm-release] ERROR: Rule: a hook committed to the RC branch must carry the executable git mode (100755), or the release cannot invoke it deterministically." >&2
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
                    _creh_report_unusable_ref_hook "cfg-declared hook" "${_creh_cfg_raw}" "${_creh_rc_branch}" "${_creh_phase}"
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
                    _creh_report_unusable_ref_hook "in-repo hook" "${_creh_in_repo_relpath}" "${_creh_rc_branch}" "${_creh_phase}"
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
                    CM_HOOK_NONEXEC_FIX_CMD="chmod +x $(printf '%q' "$_creh_in_repo")"
                    echo "[cm-release] ERROR: in-repo hook: FAIL — hook file exists but is not executable" >&2
                    echo "[cm-release] ERROR:" >&2
                    echo "[cm-release] ERROR: Cause(s):" >&2
                    echo "[cm-release] ERROR:   - ${_creh_in_repo} — file mode denies execute permission" >&2
                    echo "[cm-release] ERROR:" >&2
                    echo "[cm-release] ERROR: To fix:" >&2
                    echo "[cm-release] ERROR:   1. ${CM_HOOK_NONEXEC_FIX_CMD}" >&2
                    echo "[cm-release] ERROR:   2. Re-run scripts/cm/release.sh to re-resolve the ${_creh_phase} hook." >&2
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
