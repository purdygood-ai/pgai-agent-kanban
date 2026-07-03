#!/usr/bin/env bash
# team/scripts/lib/project_paths.sh
# Helper functions for resolving project-scoped paths within a kanban root.
#
# Source this file to get the pp_* functions in your shell:
#   source "$(dirname "${BASH_SOURCE[0]}")/project_paths.sh"
#
# All functions require KANBAN_ROOT to be set in the environment (or passed
# implicitly — they read the exported variable, not an argument).
#
# Layout: each project lives at:
#   $KANBAN_ROOT/projects/<name>/
# The projects/ subdirectory is required; callers must ensure it exists.
#
# Project Name Resolution
# -----------------------
# Every pp_* path helper accepts an explicit project name as its first
# positional argument.  When the argument is absent or empty the helper
# checks the PGAI_PROJECT_NAME environment variable.  If neither is set the
# helper prints an error that names PGAI_PROJECT_NAME and returns 1.
#
# Resolution order (highest to lowest precedence):
#   1. Explicit argument $1 passed by the caller
#   2. $PGAI_PROJECT_NAME environment variable
#   3. FAIL — print diagnostic, return 1
#
# Callers that need the project name without a path may call
# pp_require_project_context directly.
#
# Config File Resolution (project.cfg / PROJECT.cfg)
# --------------------------------------------------
# Per-project config is stored in INI format at:
#   $(pp_project_root <name>)/project.cfg   (preferred)
# Legacy bash-style config at:
#   $(pp_project_root <name>)/PROJECT.cfg   (still read when project.cfg is absent)
#
# Functions that read per-project config prefer project.cfg (INI) when it
# exists. They fall back to PROJECT.cfg (bash key=value, format compatible
# with INI key lookup) when project.cfg is absent.
#
# Functions
# ---------
#   pp_require_project_context [name]  — resolve project name; fail loudly when absent
#   pp_project_root   [name]  — root dir of the named project
#   pp_tasks_dir      [name]  — tasks/ subpath under the project root
#   pp_requirements_dir [name] — requirements/ subpath
#   pp_bugs_dir       [name]  — bugs/ subpath
#   pp_priority_dir   [name]  — priority/ subpath
#   pp_rejected_dir   [name]  — rejected/ subpath (creates dir; sidecar format documented inline)
#   pp_release_state  [name]  — release-state.md path under the project root
#   pp_load_config    [name]  — reads project.cfg (INI) and exports PP_* vars
#   pp_queue_path     [name] <agent> — path to the agent's backlog queue file
#   pp_verbose_mode   [name]  — echoes 'true' or 'false' from [debug] verbose_mode;
#                               default 'false' when absent
#   pp_verbose_agents [name]  — echoes comma-separated agents list from [debug] verbose_agents;
#                               default 'pm,coder,writer,tester,cm' when absent
#   pp_reasoning_trace [name] — echoes 'true' or 'false' from [training] reasoning_trace;
#                               default 'false' when absent
#   pp_training_agents [name] — echoes comma-separated agents list from [training] training_agents;
#                               default '' (empty string) when absent
#   pp_branch_prefix  [name]  — optional branch name prefix from [project] branch_prefix;
#                               returns empty string when key is absent or empty
#   pp_push_to_remote [name]  — echoes 'true' or 'false' from [project] push_to_remote;
#                               default 'true' when absent, blank, or malformed
#   validate_branch_prefix <value> — returns 0 for valid prefix (empty or [a-zA-Z0-9_-]+);
#                               returns non-zero and prints error for unsafe characters
#   pp_prefix_branch  [name] <branch> — returns PREFIX+branch; identity when prefix is empty
#   pp_prefix_tag     [name] <tag>    — returns PREFIX+tag; identity when prefix is empty
#   pp_strip_prefix_from_tag [name] <tag> — strips prefix from tag; returns bare semver

# ---------------------------------------------------------------------------
# Source the INI parser (read_ini) once when this library is loaded.
# Safe to source multiple times — functions are idempotent redefinitions.
# ---------------------------------------------------------------------------
_PP_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=ini_parser.sh
source "${_PP_LIB_DIR}/ini_parser.sh"
# shellcheck source=temp.sh
if ! declare -F pgai_temp_dir >/dev/null 2>&1; then
    source "${_PP_LIB_DIR}/temp.sh"
fi

# ---------------------------------------------------------------------------
# Internal: resolve the kanban root, abort loudly if unset.
# ---------------------------------------------------------------------------
_pp_kanban_root() {
    if [[ -z "${KANBAN_ROOT:-}" ]]; then
        echo "project_paths.sh: KANBAN_ROOT is not set" >&2
        return 1
    fi
    echo "$KANBAN_ROOT"
}

# ---------------------------------------------------------------------------
# Internal: _pp_project_cfg_file <project_root>
# Resolves the per-project config file path.
# Prefers project.cfg (INI format); falls back to PROJECT.cfg (bash key=value).
# Echoes the resolved path. Returns 0 even when neither file exists (the
# empty path is the caller's problem to handle).
# ---------------------------------------------------------------------------
_pp_project_cfg_file() {
    local proj_root="$1"
    local new_cfg="${proj_root}/project.cfg"   # INI format (preferred)
    local old_cfg="${proj_root}/PROJECT.cfg"   # bash key=value (fallback when project.cfg absent)
    if [[ -f "$new_cfg" ]]; then
        echo "$new_cfg"
    elif [[ -f "$old_cfg" ]]; then
        echo "$old_cfg"
    else
        echo ""
    fi
}

# ---------------------------------------------------------------------------
# Internal: _pp_read_cfg_key <cfg_file> <section> <key> [default]
# Read a per-project config value, handling both INI (project.cfg) and
# bash key=value (PROJECT.cfg) formats transparently.
#
# For INI files (project.cfg): delegates to read_ini with the given section.
# For PROJECT.cfg (no section headers): first tries read_ini; on empty result
#   falls back to a bare-assignment grep.
#
# Strategy: try read_ini first.  If that returns empty AND the file looks like
# a bare-assignment file (PROJECT.cfg), grep for the bare key.
# ---------------------------------------------------------------------------
_pp_read_cfg_key() {
    local cfg_file="$1"
    local section="$2"
    local key="$3"
    local default="${4:-}"

    local val
    val="$(read_ini "$cfg_file" "$section" "$key" "")"

    if [[ -n "$val" ]]; then
        printf '%s' "$val"
        return 0
    fi

    # If the file is PROJECT.cfg (no INI section headers), fall back to a
    # bare-assignment grep so callers work on both file formats.
    if [[ "$(basename "$cfg_file")" == "PROJECT.cfg" ]]; then
        local legacy_val
        legacy_val="$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "$cfg_file" 2>/dev/null \
            | head -n1 \
            | sed 's|^[^=]*=[[:space:]]*||; s|[[:space:]]*$||; s|^["'"'"']||; s|["'"'"']$||')"
        if [[ -n "$legacy_val" ]]; then
            printf '%s' "$legacy_val"
            return 0
        fi
    fi

    printf '%s' "$default"
}

# ---------------------------------------------------------------------------
# pp_require_project_context [name]
# Resolves the active project name from the following sources, in order:
#   1. The first positional argument $1 (when non-empty)
#   2. The PGAI_PROJECT_NAME environment variable (when non-empty)
#   3. ERROR — prints a diagnostic to stderr and returns 1.
#
# Echoes the resolved project name to stdout on success.
#
# Callers that derive a project name from an argument or env var should route
# through this function rather than re-implementing the resolution logic.
#
# Example:
#   project="$(pp_require_project_context "${1:-}")" || return 1
# ---------------------------------------------------------------------------
pp_require_project_context() {
    local _arg="${1:-}"
    if [[ -n "$_arg" ]]; then
        echo "$_arg"
        return 0
    fi
    if [[ -n "${PGAI_PROJECT_NAME:-}" ]]; then
        echo "$PGAI_PROJECT_NAME"
        return 0
    fi
    echo "project_paths.sh: project name is required — pass it as an argument or set PGAI_PROJECT_NAME" >&2
    return 1
}

# ---------------------------------------------------------------------------
# pp_project_root [name]
# Echoes $KANBAN_ROOT/projects/<name>.
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
# ---------------------------------------------------------------------------
pp_project_root() {
    local name
    name="$(pp_require_project_context "${1:-}")" || return 1
    local root
    root="$(_pp_kanban_root)" || return 1
    echo "${root}/projects/${name}"
}

# ---------------------------------------------------------------------------
# pp_tasks_dir [name]
# Echoes the tasks/ subpath under the project root for the named project.
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
# ---------------------------------------------------------------------------
pp_tasks_dir() {
    local name
    name="$(pp_require_project_context "${1:-}")" || return 1
    local project_root
    project_root="$(pp_project_root "$name")" || return 1
    echo "${project_root}/tasks"
}

# ---------------------------------------------------------------------------
# pp_requirements_dir [name]
# Echoes the requirements/ subpath under the project root for the named
# project.
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
# ---------------------------------------------------------------------------
pp_requirements_dir() {
    local name
    name="$(pp_require_project_context "${1:-}")" || return 1
    local project_root
    project_root="$(pp_project_root "$name")" || return 1
    echo "${project_root}/requirements"
}

# ---------------------------------------------------------------------------
# pp_bugs_dir [name]
# Echoes the bugs/ subpath under the project root for the named project.
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
# ---------------------------------------------------------------------------
pp_bugs_dir() {
    local name
    name="$(pp_require_project_context "${1:-}")" || return 1
    local project_root
    project_root="$(pp_project_root "$name")" || return 1
    echo "${project_root}/bugs"
}

# ---------------------------------------------------------------------------
# pp_priority_dir [name]
# Echoes the priority/ subpath under the project root for the named project.
# Supports operator-authored priority intake.
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
# ---------------------------------------------------------------------------
pp_priority_dir() {
    local name
    name="$(pp_require_project_context "${1:-}")" || return 1
    local project_root
    project_root="$(pp_project_root "$name")" || return 1
    echo "${project_root}/priority"
}

# ---------------------------------------------------------------------------
# pp_rejected_dir [name]
# Echoes the rejected/ subpath under the project root for the named project
# and creates the directory if it does not yet exist (idempotent via mkdir -p).
#
# Callers that only need the path without side-effects may redirect stderr or
# check whether the directory exists themselves; this function will not error
# on repeated calls.
#
# Sidecar format — every quarantined file <F> may have a companion file <F>.reason
# co-located in the same rejected/ directory:
#
#   # Sidecar format: <filename>.reason co-located with quarantined file
#   # Key=value lines, newline terminated, single-line values (escape \n if needed)
#   # Keys: original_type (bugs|priority|requirements)
#   #       original_dir (absolute path of source directory)
#   #       rejected_at (ISO 8601 UTC, e.g. 2026-05-28T14:00:00Z)
#   #       reason (single line free text)
#   #       retry_count (integer)
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
# ---------------------------------------------------------------------------
pp_rejected_dir() {
    local name
    name="$(pp_require_project_context "${1:-}")" || return 1
    local project_root
    project_root="$(pp_project_root "$name")" || return 1
    local rejected_path="${project_root}/rejected"
    mkdir -p "$rejected_path"
    echo "$rejected_path"
}

# ---------------------------------------------------------------------------
# pp_release_state [name]
# Echoes the release-state.md path under the project root for the named
# project.
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
# ---------------------------------------------------------------------------
pp_release_state() {
    local name
    name="$(pp_require_project_context "${1:-}")" || return 1
    local project_root
    project_root="$(pp_project_root "$name")" || return 1
    echo "${project_root}/release-state.md"
}

# ---------------------------------------------------------------------------
# pp_last_released_version [name]
# Echoes the highest bare semver tag (vX.Y.Z) that has been merged into
# origin/<main_branch> on the project's dev tree, after stripping the
# configured branch_prefix from candidate tags.
#
# When branch_prefix is non-empty (e.g. "ai_"), <main_branch> is
# "${prefix}main" (e.g. "ai_main").  This matches the production layout
# where release.sh squashes and tags on the prefixed main branch.
# When branch_prefix is empty, <main_branch> is "main".
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
#
# Resolution path:
#   1. Reads dev_tree_path from $(pp_project_root <name>)/project.cfg or PROJECT.cfg.
#   2. Reads branch_prefix from the same config file (may be empty).
#   3. Runs git -C "$dev_tree" fetch origin --tags --quiet (best-effort;
#      failure is ignored so callers in air-gapped or read-only environments
#      are not broken).
#   4. Lists git tags merged into origin/<main_branch>.
#   5. When prefix is non-empty: filters to tags starting with PREFIX followed
#      by 'v'; strips the prefix from each candidate before semver comparison.
#      Tags from a different prefix (e.g. human_v0.31.0 when prefix=ai_) are
#      excluded.
#      When prefix is empty: filters to bare ^v[0-9]+\.[0-9]+\.[0-9]+$ tags.
#   6. Sorts stripped candidates with sort -V, returns the highest as bare
#      vX.Y.Z (prefix never appears in the return value).
#   7. Falls back to release-state.md '## Last Released' when no git repo is
#      available: document-workflow projects have no git repo, so the git-tag
#      path always misses; finalize.sh writes '## Last Released' to
#      release-state.md after publishing, and this resolver honours it.
#      The release-state.md fallback is skipped for projects that have a
#      working git repo — code projects prefer the git tag.
#   8. Falls back to "v0.0.0" on any error:
#        - project.cfg/PROJECT.cfg missing or unreadable
#        - dev_tree_path unset or not a git repo AND release-state.md absent
#          or has no Last Released value (or it is 'none')
#        - no matching tags found and no valid release-state.md entry
#
# The function does NOT change the caller's CWD.  All git operations use
# git -C "$dev_tree" rather than cd.
#
# Return value is ALWAYS bare vX.Y.Z — callers must not add or remove prefix.
#
# Example:
#   pp_last_released_version "pgai-agent-kanban"
#   # -> v0.31.0  (from tag ai_v0.31.0 on origin/ai_main when branch_prefix=ai_)
#   # -> v0.30.1  (from tag v0.30.1 on origin/main when branch_prefix is empty)
#   pp_last_released_version "pgai-three-bears"
#   # -> v0.0.1   (from release-state.md ## Last Released when no git repo)
# ---------------------------------------------------------------------------
pp_last_released_version() {
    local name
    name="$(pp_require_project_context "${1:-}")" || {
        echo "v0.0.0"
        return 0
    }
    local _sentinel="v0.0.0"

    # Locate project.cfg (INI) or PROJECT.cfg (legacy fallback).
    local project_root cfg_file
    project_root="$(pp_project_root "$name" 2>/dev/null)" || {
        echo "$_sentinel"
        return 0
    }
    cfg_file="$(_pp_project_cfg_file "$project_root")"

    # ---------------------------------------------------------------------------
    # Internal helper: _pp_read_last_released_from_state <project_root>
    # Reads '## Last Released' from release-state.md in the project root.
    # Returns a valid bare vX.Y.Z semver, or empty string when absent/invalid.
    # Used as the fallback for document-workflow (no-git-repo) projects.
    # ---------------------------------------------------------------------------
    _pp_read_last_released_from_state() {
        local _root="$1"
        local _rs_path="${_root}/release-state.md"
        if [[ ! -f "$_rs_path" ]]; then
            echo ""
            return 0
        fi
        local _val
        _val="$(python3 - "$_rs_path" 2>/dev/null <<'PY' || true
import re, sys, pathlib
text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
m = re.search(
    r"##\s*Last\s+Released\s*\n(.*?)(?=\n##|\Z)",
    text,
    re.IGNORECASE | re.DOTALL,
)
if m:
    val = m.group(1).strip()
    if val and val.lower() != "none" and re.match(r"^v[0-9]+\.[0-9]+\.[0-9]+$", val):
        print(val)
    else:
        print("")
else:
    print("")
PY
)"
        echo "${_val:-}"
    }

    if [[ -z "$cfg_file" ]]; then
        # No config file — try release-state.md fallback before returning sentinel.
        local _rs_val
        _rs_val="$(_pp_read_last_released_from_state "$project_root")"
        if [[ -n "$_rs_val" ]]; then
            echo "$_rs_val"
        else
            echo "$_sentinel"
        fi
        return 0
    fi

    # Extract dev_tree_path from the project config.
    # _pp_read_cfg_key handles both INI [project] section (project.cfg) and
    # bare key=value lines (legacy PROJECT.cfg).
    # Source: project.cfg [project] dev_tree_path
    local dev_tree
    dev_tree="$(_pp_read_cfg_key "$cfg_file" project dev_tree_path "")"

    if [[ -z "$dev_tree" ]]; then
        # No dev_tree configured — this is a document-workflow (no-git) project.
        # Fall back to release-state.md '## Last Released'.
        local _rs_val
        _rs_val="$(_pp_read_last_released_from_state "$project_root")"
        if [[ -n "$_rs_val" ]]; then
            echo "$_rs_val"
        else
            echo "$_sentinel"
        fi
        return 0
    fi

    # Read branch_prefix; default to empty (no prefix applied when absent).
    # Source: project.cfg [project] branch_prefix
    local prefix
    prefix="$(_pp_read_cfg_key "$cfg_file" project branch_prefix "")"

    # Strip surrounding double-quotes so that `branch_prefix = ""` is treated
    # as empty rather than as the 2-char string "".
    prefix="${prefix%\"}"
    prefix="${prefix#\"}"

    # Verify the path is a git repo before doing any git work.
    if ! git -C "$dev_tree" rev-parse --git-dir &>/dev/null 2>&1; then
        # Not a git repo — fall back to release-state.md.
        local _rs_val
        _rs_val="$(_pp_read_last_released_from_state "$project_root")"
        if [[ -n "$_rs_val" ]]; then
            echo "$_rs_val"
        else
            echo "$_sentinel"
        fi
        return 0
    fi

    # Best-effort fetch — never fail the function.
    git -C "$dev_tree" fetch origin --tags --quiet 2>/dev/null || true

    # Resolve the canonical main branch name for this project.
    # When branch_prefix is non-empty (e.g. "ai_"), release.sh operates on
    # ai_main rather than bare main.  Tags are created on ai_main commits, so
    # we must query origin/ai_main (not origin/main) to find merged tags.
    local main_branch
    main_branch="${prefix}main"

    # Resolve origin/<main_branch>.  If the remote tracking ref does not exist
    # (fresh repo with no remote, or the branch was never pushed), fall back to
    # release-state.md rather than returning the sentinel directly.
    if ! git -C "$dev_tree" rev-parse --verify "origin/${main_branch}" &>/dev/null 2>&1; then
        local _rs_val
        _rs_val="$(_pp_read_last_released_from_state "$project_root")"
        if [[ -n "$_rs_val" ]]; then
            echo "$_rs_val"
        else
            echo "$_sentinel"
        fi
        return 0
    fi

    # List tags merged into origin/<main_branch>, filter to tags matching this
    # project's prefix, strip the prefix, sort by semver, take the highest.
    #
    # When prefix is non-empty:
    #   - Accept only tags whose literal prefix is exactly "$prefix" followed by
    #     a 'v', e.g. "ai_v0.31.0" for prefix "ai_".
    #   - Tags from a different prefix (e.g. "human_v0.31.0") are excluded.
    #   - The prefix is stripped before semver comparison and before output.
    # When prefix is empty:
    #   - Accept only bare vX.Y.Z tags.
    #   - main_branch is "main" (the literal, unchanged).
    local highest
    if [[ -n "$prefix" ]]; then
        # Build an anchored pattern matching PREFIX + strict semver.
        # We strip the prefix inline so sort -V and tail operate on bare semver.
        highest="$(git -C "$dev_tree" tag --merged "origin/${main_branch}" 2>/dev/null \
            | grep -E "^$(printf '%s' "$prefix" | sed 's/[.[\*^${}|()]/\\&/g')v[0-9]+\.[0-9]+\.[0-9]+\$" \
            | sed "s|^$(printf '%s' "$prefix" | sed 's/[.[\*^${}|()]/\\&/g')||" \
            | sort -V \
            | tail -n1)"
    else
        # No prefix — legacy behaviour: match bare vX.Y.Z only.
        highest="$(git -C "$dev_tree" tag --merged "origin/${main_branch}" 2>/dev/null \
            | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' \
            | sort -V \
            | tail -n1)"
    fi

    if [[ -z "$highest" ]]; then
        echo "$_sentinel"
        return 0
    fi

    echo "$highest"
}

# ---------------------------------------------------------------------------
# pp_max_minor [name]
# Echoes the configured max_minor value from PROJECT.cfg for the named
# project, or an empty string when the field is absent.
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
#
# Only non-negative integers are valid.  Malformed values (non-numeric,
# negative, empty after stripping whitespace) are rejected: the function
# prints an error to stderr and returns 1.
#
# Missing field is NOT an error — it means "no constraint on minor".
#
# Example:
#   pp_max_minor "pgai-agent-kanban"   # -> 21 (or empty if unset)
# ---------------------------------------------------------------------------
pp_max_minor() {
    local name
    name="$(pp_require_project_context "${1:-}")" || { echo ""; return 0; }
    local project_root cfg_file
    project_root="$(pp_project_root "$name" 2>/dev/null)" || { echo ""; return 0; }
    cfg_file="$(_pp_project_cfg_file "$project_root")"

    [[ -n "$cfg_file" ]] || { echo ""; return 0; }

    # Source: project.cfg [versioning] max_minor (or bare key in legacy PROJECT.cfg)
    local raw
    raw="$(_pp_read_cfg_key "$cfg_file" versioning max_minor "")"

    # Empty / absent — no constraint.
    [[ -z "$raw" ]] && { echo ""; return 0; }

    # Validate: must be a non-negative integer.
    if ! [[ "$raw" =~ ^[0-9]+$ ]]; then
        echo "project_paths.sh: pp_max_minor: invalid value '${raw}' for project '${name}' (expected non-negative integer)" >&2
        return 1
    fi

    echo "$raw"
}

# ---------------------------------------------------------------------------
# pp_max_major [name]
# Echoes the configured max_major value from PROJECT.cfg for the named
# project, or an empty string when the field is absent.
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
#
# Same validation rules as pp_max_minor.
#
# Example:
#   pp_max_major "pgai-agent-kanban"   # -> 0 (or empty if unset)
# ---------------------------------------------------------------------------
pp_max_major() {
    local name
    name="$(pp_require_project_context "${1:-}")" || { echo ""; return 0; }
    local project_root cfg_file
    project_root="$(pp_project_root "$name" 2>/dev/null)" || { echo ""; return 0; }
    cfg_file="$(_pp_project_cfg_file "$project_root")"

    [[ -n "$cfg_file" ]] || { echo ""; return 0; }

    # Source: project.cfg [versioning] max_major (or bare key in legacy PROJECT.cfg)
    local raw
    raw="$(_pp_read_cfg_key "$cfg_file" versioning max_major "")"

    # Empty / absent — no constraint.
    [[ -z "$raw" ]] && { echo ""; return 0; }

    # Validate: must be a non-negative integer.
    if ! [[ "$raw" =~ ^[0-9]+$ ]]; then
        echo "project_paths.sh: pp_max_major: invalid value '${raw}' for project '${name}' (expected non-negative integer)" >&2
        return 1
    fi

    echo "$raw"
}

# ---------------------------------------------------------------------------
# pp_max_patch [name]
# Echoes the configured max_patch value from PROJECT.cfg for the named
# project, or an empty string when the field is absent.
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
#
# Same validation rules as pp_max_minor and pp_max_major.
#
# IMPORTANT: 0 is a valid ceiling value (meaning only patch 0 is allowed).
# Empty/absent field is the unset sentinel meaning "no patch ceiling".
#
# Example:
#   pp_max_patch "pgai-agent-kanban"   # -> 5 (or empty if unset)
# ---------------------------------------------------------------------------
pp_max_patch() {
    local name
    name="$(pp_require_project_context "${1:-}")" || { echo ""; return 0; }
    local project_root cfg_file
    project_root="$(pp_project_root "$name" 2>/dev/null)" || { echo ""; return 0; }
    cfg_file="$(_pp_project_cfg_file "$project_root")"

    [[ -n "$cfg_file" ]] || { echo ""; return 0; }

    # Source: project.cfg [versioning] max_patch (or bare key in legacy PROJECT.cfg)
    local raw
    raw="$(_pp_read_cfg_key "$cfg_file" versioning max_patch "")"

    # Empty / absent — no constraint.
    [[ -z "$raw" ]] && { echo ""; return 0; }

    # Validate: must be a non-negative integer.
    if ! [[ "$raw" =~ ^[0-9]+$ ]]; then
        echo "project_paths.sh: pp_max_patch: invalid value '${raw}' for project '${name}' (expected non-negative integer)" >&2
        return 1
    fi

    echo "$raw"
}

# ---------------------------------------------------------------------------
# pp_max_priorities_per_run [name]
# Echoes the configured max_priorities_per_run value from project.cfg for the
# named project, or an empty string when the field is absent.
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
#
# Same validation rules as pp_max_patch, pp_max_minor, and pp_max_major.
#
# IMPORTANT: 0 is a valid value (meaning no priorities processed per run).
# Empty/absent field is the unset sentinel; callers apply their own default
# (typically 1) — this helper does NOT inject a default.
#
# Source in project.cfg: [versioning] max_priorities_per_run
#
# Example:
#   pp_max_priorities_per_run "pgai-agent-kanban"   # -> 3 (or empty if unset)
# ---------------------------------------------------------------------------
pp_max_priorities_per_run() {
    local name
    name="$(pp_require_project_context "${1:-}")" || { echo ""; return 0; }
    local project_root cfg_file
    project_root="$(pp_project_root "$name" 2>/dev/null)" || { echo ""; return 0; }
    cfg_file="$(_pp_project_cfg_file "$project_root")"

    [[ -n "$cfg_file" ]] || { echo ""; return 0; }

    # Source: project.cfg [versioning] max_priorities_per_run (or bare key in legacy PROJECT.cfg)
    local raw
    raw="$(_pp_read_cfg_key "$cfg_file" versioning max_priorities_per_run "")"

    # Empty / absent — no constraint; caller applies default of 1.
    [[ -z "$raw" ]] && { echo ""; return 0; }

    # Validate: must be a non-negative integer.
    if ! [[ "$raw" =~ ^[0-9]+$ ]]; then
        echo "project_paths.sh: pp_max_priorities_per_run: invalid value '${raw}' for project '${name}' (expected non-negative integer)" >&2
        return 1
    fi

    echo "$raw"
}

# ---------------------------------------------------------------------------
# pp_verbose_mode [name]
# Echoes 'true' when the named project has [debug] verbose_mode = true in
# project.cfg; echoes 'false' otherwise (absent section, absent key, or any
# value other than the string 'true').
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
#
# Default when absent or empty: 'false'.
#
# Source in project.cfg: [debug] verbose_mode
#
# Example:
#   pp_verbose_mode "pgai-agent-kanban"   # -> 'false' (key unset)
#   pp_verbose_mode "my-project"                 # -> 'true' (when configured)
# ---------------------------------------------------------------------------
pp_verbose_mode() {
    local name
    name="$(pp_require_project_context "${1:-}")" || { echo "false"; return 0; }
    local project_root cfg_file
    project_root="$(pp_project_root "$name" 2>/dev/null)" || { echo "false"; return 0; }
    cfg_file="$(_pp_project_cfg_file "$project_root")"

    [[ -n "$cfg_file" ]] || { echo "false"; return 0; }

    # Source: project.cfg [debug] verbose_mode
    local raw
    raw="$(_pp_read_cfg_key "$cfg_file" debug verbose_mode "")"

    # Only the exact string 'true' enables verbose mode; everything else is false.
    if [[ "$raw" == "true" ]]; then
        echo "true"
    else
        echo "false"
    fi
}

# ---------------------------------------------------------------------------
# pp_verbose_agents [name]
# Echoes the comma-separated list of agent names for which verbose mode is
# active, from [debug] verbose_agents in project.cfg.
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
#
# Default when absent or empty: 'pm,coder,writer,tester,cm'.
#
# The value is returned verbatim when set; the caller is responsible for
# splitting on commas.  Malformed values (non-empty but effectively garbage)
# are returned as-is — the caller applies its own filtering.
#
# Source in project.cfg: [debug] verbose_agents
#
# Example:
#   pp_verbose_agents "pgai-agent-kanban"   # -> 'pm,coder,writer,tester,cm' (unset)
#   pp_verbose_agents "my-project"                 # -> 'coder,writer' (when configured)
# ---------------------------------------------------------------------------
pp_verbose_agents() {
    local name
    name="$(pp_require_project_context "${1:-}")" || { echo "pm,coder,writer,tester,cm"; return 0; }
    local project_root cfg_file
    project_root="$(pp_project_root "$name" 2>/dev/null)" || { echo "pm,coder,writer,tester,cm"; return 0; }
    cfg_file="$(_pp_project_cfg_file "$project_root")"

    [[ -n "$cfg_file" ]] || { echo "pm,coder,writer,tester,cm"; return 0; }

    # Source: project.cfg [debug] verbose_agents
    local raw
    raw="$(_pp_read_cfg_key "$cfg_file" debug verbose_agents "")"

    # Empty / absent — return the all-agents default.
    if [[ -z "$raw" ]]; then
        echo "pm,coder,writer,tester,cm"
        return 0
    fi

    echo "$raw"
}

# ---------------------------------------------------------------------------
# pp_reasoning_trace [name]
# Echoes 'true' when the named project has [training] reasoning_trace = true in
# project.cfg; echoes 'false' otherwise (absent section, absent key, or any
# value other than the string 'true').
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
#
# Default when absent or empty: 'false' (reasoning trace is OFF by default).
#
# Source in project.cfg: [training] reasoning_trace
#
# Example:
#   pp_reasoning_trace "pgai-agent-kanban"   # -> 'false' (key unset)
#   pp_reasoning_trace "my-project"                 # -> 'true' (when configured)
# ---------------------------------------------------------------------------
pp_reasoning_trace() {
    local name
    name="$(pp_require_project_context "${1:-}")" || { echo "false"; return 0; }
    local project_root cfg_file
    project_root="$(pp_project_root "$name" 2>/dev/null)" || { echo "false"; return 0; }
    cfg_file="$(_pp_project_cfg_file "$project_root")"

    [[ -n "$cfg_file" ]] || { echo "false"; return 0; }

    # Source: project.cfg [training] reasoning_trace
    local raw
    raw="$(_pp_read_cfg_key "$cfg_file" training reasoning_trace "")"

    # Only the exact string 'true' enables reasoning trace; everything else is false.
    if [[ "$raw" == "true" ]]; then
        echo "true"
    else
        echo "false"
    fi
}

# ---------------------------------------------------------------------------
# pp_training_agents [name]
# Echoes the comma-separated list of agent names for which training mode is
# active, from [training] training_agents in project.cfg.
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
#
# Default when absent or empty: '' (empty string — no agents; explicit opt-in required).
#
# The value is returned verbatim when set; the caller is responsible for
# splitting on commas.  Malformed values (non-empty but effectively garbage)
# are returned as-is — the caller applies its own filtering.
#
# Source in project.cfg: [training] training_agents
#
# Example:
#   pp_training_agents "pgai-agent-kanban"   # -> '' (unset)
#   pp_training_agents "my-project"                 # -> 'coder,writer' (when configured)
# ---------------------------------------------------------------------------
pp_training_agents() {
    local name
    name="$(pp_require_project_context "${1:-}")" || { echo ""; return 0; }
    local project_root cfg_file
    project_root="$(pp_project_root "$name" 2>/dev/null)" || { echo ""; return 0; }
    cfg_file="$(_pp_project_cfg_file "$project_root")"

    [[ -n "$cfg_file" ]] || { echo ""; return 0; }

    # Source: project.cfg [training] training_agents
    local raw
    raw="$(_pp_read_cfg_key "$cfg_file" training training_agents "")"

    # Empty / absent — return empty string (narrow-start default: no agents).
    if [[ -z "$raw" ]]; then
        echo ""
        return 0
    fi

    echo "$raw"
}

# ---------------------------------------------------------------------------
# validate_branch_prefix <value>
# Validates the candidate branch_prefix string against the allowed character
# class: letters (a-z, A-Z), digits (0-9), underscore (_), hyphen (-).
#
# Rules:
#   - Empty string is valid (pure-AI shop; no prefix applied).
#   - Non-empty strings must match ^[a-zA-Z0-9_-]+$ exactly.
#   - Values containing spaces, slashes, dots, or any other character outside
#     the allowed class are rejected with an explanatory error on stderr.
#
# Returns:
#   0 — value is valid (empty string or matches allowed character class)
#   1 — value contains disallowed characters (error written to stderr)
#
# Examples that must pass:  ai_  team-  MyOrg_  (empty string)
# Examples that must fail:  "my prefix"  feat/  v1.0
#
# This function is intentionally standalone (no project-name argument) so it
# can be unit-tested independently and called before a project context is
# available.
# ---------------------------------------------------------------------------
validate_branch_prefix() {
    local value="${1:-}"

    # Empty string is always valid — means "no prefix applied".
    if [[ -z "$value" ]]; then
        return 0
    fi

    # Allow only letters, digits, underscore, and hyphen.
    if [[ "$value" =~ ^[a-zA-Z0-9_-]+$ ]]; then
        return 0
    fi

    echo "project_paths.sh: validate_branch_prefix: invalid branch_prefix '${value}'" \
         "(allowed characters: a-z A-Z 0-9 _ -; spaces, slashes, dots, and other" \
         "special characters are not permitted)" >&2
    return 1
}

# ---------------------------------------------------------------------------
# pp_branch_prefix [name]
# Echoes the configured branch_prefix value from project.cfg for the named
# project, or an empty string when the field is absent or empty.
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
#
# The value is validated on read: if the stored value contains disallowed
# characters (anything outside [a-zA-Z0-9_-]) this function prints an error
# to stderr and returns 1 so that callers surface misconfigured installs.
#
# Default when absent or empty: "" (empty string — no prefix applied, which is
# the correct default for pure-AI shops and for all existing installs that do
# not set this key).
#
# Source in project.cfg: [project] branch_prefix
#
# Example:
#   pp_branch_prefix "pgai-agent-kanban"   # -> "" (key unset)
#   pp_branch_prefix "my-project"                 # -> "ai_" (when configured)
# ---------------------------------------------------------------------------
pp_branch_prefix() {
    local name
    name="$(pp_require_project_context "${1:-}")" || { echo ""; return 0; }
    local project_root cfg_file
    project_root="$(pp_project_root "$name" 2>/dev/null)" || { echo ""; return 0; }
    cfg_file="$(_pp_project_cfg_file "$project_root")"

    [[ -n "$cfg_file" ]] || { echo ""; return 0; }

    # Source: project.cfg [project] branch_prefix (or bare key in legacy PROJECT.cfg)
    local raw
    raw="$(_pp_read_cfg_key "$cfg_file" project branch_prefix "")"

    # Strip surrounding double-quotes so that `branch_prefix = ""` is treated
    # as empty rather than as the 2-char string "".
    raw="${raw%\"}"
    raw="${raw#\"}"

    # Empty / absent — no prefix.
    [[ -z "$raw" ]] && { echo ""; return 0; }

    # Validate the stored value before returning it.
    if ! validate_branch_prefix "$raw"; then
        echo "project_paths.sh: pp_branch_prefix: invalid branch_prefix in config for project '${name}'" >&2
        return 1
    fi

    echo "$raw"
}

# ---------------------------------------------------------------------------
# pp_push_to_remote [name]
# Echoes 'true' when CM should push to origin for the named project; echoes
# 'false' when the project is configured for local-only / demo mode.
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
#
# Source in project.cfg: [project] push_to_remote
#
# Normalization rules (defensive — all of these return 'true' or 'false'):
#   - Absent key, blank value, or any value other than the exact string 'false'
#     → 'true'  (default; preserves existing behavior for all existing projects)
#   - Exact string 'false' (case-sensitive, no surrounding quotes) → 'false'
#   - Surrounding double-quotes are stripped before comparison, matching the
#     same strip-surrounding-quotes treatment used by pp_branch_prefix.
#
# Default when absent or empty: 'true'.
#
# Example:
#   pp_push_to_remote "pgai-agent-kanban"   # -> 'true' (key unset)
#   pp_push_to_remote "local-only-project"  # -> 'false' (push_to_remote = false)
# ---------------------------------------------------------------------------
pp_push_to_remote() {
    local name
    name="$(pp_require_project_context "${1:-}")" || { echo "true"; return 0; }
    local project_root cfg_file
    project_root="$(pp_project_root "$name" 2>/dev/null)" || { echo "true"; return 0; }
    cfg_file="$(_pp_project_cfg_file "$project_root")"

    [[ -n "$cfg_file" ]] || { echo "true"; return 0; }

    # Source: project.cfg [project] push_to_remote (or bare key in legacy PROJECT.cfg)
    local raw
    raw="$(_pp_read_cfg_key "$cfg_file" project push_to_remote "")"

    # Strip surrounding double-quotes so that `push_to_remote = ""` is treated
    # as absent rather than as the 2-char string "".
    raw="${raw%\"}"
    raw="${raw#\"}"

    # Only the exact string 'false' opts out of remote pushes; everything else
    # (absent, blank, 'true', malformed) defaults to 'true'.
    if [[ "$raw" == "false" ]]; then
        echo "false"
    else
        echo "true"
    fi
}

# ---------------------------------------------------------------------------
# pp_prefix_branch [name] <branch>
# Returns PREFIX+branch, where PREFIX is the value from pp_branch_prefix for
# the named project.  When the prefix is empty (the common case for pure-AI
# installs) the branch name is returned unchanged.
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
#
# This is the single source of truth for constructing prefixed branch names.
# All git-touching scripts must call this rather than prepending the prefix
# inline, so that config changes propagate automatically.
#
# Example:
#   pp_prefix_branch "pgai-agent-kanban" "rc/v0.31.0"
#   # -> "ai_rc/v0.31.0"  (when branch_prefix=ai_)
#   # -> "rc/v0.31.0"     (when branch_prefix is empty)
# ---------------------------------------------------------------------------
pp_prefix_branch() {
    local name
    name="$(pp_require_project_context "${1:-}")" || return 1
    local branch="${2:-}"
    local prefix
    prefix="$(pp_branch_prefix "$name")" || return 1
    printf '%s' "${prefix}${branch}"
}

# ---------------------------------------------------------------------------
# pp_prefix_tag [name] <tag>
# Returns PREFIX+tag, where PREFIX is the value from pp_branch_prefix for
# the named project.  When the prefix is empty the tag is returned unchanged.
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
#
# This is the single source of truth for constructing prefixed git tag names.
# All git-touching scripts must call this rather than prepending the prefix
# inline.
#
# Example:
#   pp_prefix_tag "pgai-agent-kanban" "v0.31.0"
#   # -> "ai_v0.31.0"  (when branch_prefix=ai_)
#   # -> "v0.31.0"     (when branch_prefix is empty)
# ---------------------------------------------------------------------------
pp_prefix_tag() {
    local name
    name="$(pp_require_project_context "${1:-}")" || return 1
    local tag="${2:-}"
    local prefix
    prefix="$(pp_branch_prefix "$name")" || return 1
    printf '%s' "${prefix}${tag}"
}

# ---------------------------------------------------------------------------
# pp_strip_prefix_from_tag [name] <tag>
# Strips the configured branch_prefix from the front of <tag> and returns the
# bare semver string.  When the prefix is empty the tag is returned unchanged
# (bare semver is already the input).
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
#
# Uses bash parameter expansion (${tag#"$prefix"}) to remove the prefix so
# that the result is always a prefix-free string regardless of whether the
# caller stored the tag with or without a prefix.
#
# Example:
#   pp_strip_prefix_from_tag "pgai-agent-kanban" "ai_v0.31.0"
#   # -> "v0.31.0"   (when branch_prefix=ai_)
#   pp_strip_prefix_from_tag "pgai-agent-kanban" "v0.31.0"
#   # -> "v0.31.0"   (when branch_prefix is empty — no-op)
# ---------------------------------------------------------------------------
pp_strip_prefix_from_tag() {
    local name
    name="$(pp_require_project_context "${1:-}")" || return 1
    local tag="${2:-}"
    local prefix
    prefix="$(pp_branch_prefix "$name")" || return 1
    printf '%s' "${tag#"$prefix"}"
}

# ---------------------------------------------------------------------------
# pp_version_within_ceiling [name] <version>
# Returns 0 (true) when <version> is within the ceiling defined in PROJECT.cfg
# for the named project; returns 1 when the version exceeds it.
#
# Project name resolution for the first argument: explicit argument, then
# PGAI_PROJECT_NAME, then error.  When called with one argument, that
# argument is treated as the version and project name is resolved from
# PGAI_PROJECT_NAME.  When called with two arguments, the first is the
# project name and the second is the version.
#
# Rules:
#   - If max_major is set and version's major component > max_major → exceeds (1)
#   - If max_minor is set and version's minor component > max_minor → exceeds (1)
#   - If max_patch is set and version's patch component > max_patch → exceeds (1)
#   - Empty/absent ceiling means "no constraint" for that component.
#   - If no ceiling is set, any version is within ceiling → returns 0.
#   - Malformed version string → prints error to stderr and returns 1.
#
# NOTE: max_patch=0 is a real ceiling (only v X.Y.0 accepted). Empty string
# is the unset signal. This differs from many parsers that treat 0 as unset.
#
# The version argument accepts a leading "v" prefix (e.g. v0.21.5 or 0.21.5).
#
# Example (two-arg form, preferred):
#   pp_version_within_ceiling "pgai-agent-kanban" "v0.21.5"  # -> 0
#   pp_version_within_ceiling "pgai-agent-kanban" "v0.22.0"  # -> 1
# Example (one-arg form, uses PGAI_PROJECT_NAME):
#   PGAI_PROJECT_NAME=pgai-agent-kanban pp_version_within_ceiling "v0.21.5"
# ---------------------------------------------------------------------------
pp_version_within_ceiling() {
    local name version

    if [[ $# -ge 2 ]]; then
        name="$1"
        version="$2"
    else
        # One-arg form: $1 is the version; resolve project from env.
        version="${1:-}"
        name="$(pp_require_project_context "")" || return 1
    fi

    # Strip leading 'v' or 'V'.
    local v_clean="${version#v}"
    v_clean="${v_clean#V}"

    # Validate format: must be X.Y.Z with non-negative integers.
    if ! [[ "$v_clean" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "project_paths.sh: pp_version_within_ceiling: malformed version '${version}'" >&2
        return 1
    fi

    local major minor patch
    major="${v_clean%%.*}"
    local rest="${v_clean#*.}"
    minor="${rest%%.*}"
    patch="${rest#*.}"

    local max_major max_minor max_patch
    max_major="$(pp_max_major "$name")" || return 1
    max_minor="$(pp_max_minor "$name")" || return 1
    max_patch="$(pp_max_patch "$name")" || return 1

    if [[ -n "$max_major" ]] && (( major > max_major )); then
        return 1
    fi

    if [[ -n "$max_minor" ]] && (( minor > max_minor )); then
        return 1
    fi

    if [[ -n "$max_patch" ]] && (( patch > max_patch )); then
        return 1
    fi

    return 0
}

# ---------------------------------------------------------------------------
# pp_project_halted [name]
# Returns 0 (true) when a per-project HALT file exists for the named project;
# returns 1 (false) when it does not.
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
#
# The HALT file path is:
#   $(pp_project_root <name>)/HALT
#
# Callers (wake scripts, discovery pipeline) use this to skip a project
# without affecting the global HALT state.  The global HALT check lives
# at $KANBAN_ROOT/HALT and is evaluated separately by those callers.
#
# Example:
#   if pp_project_halted "video-generator"; then
#       echo "project is halted — skipping" >&2
#   fi
# ---------------------------------------------------------------------------
pp_project_halted() {
    local name
    name="$(pp_require_project_context "${1:-}")" || return 1
    local project_root
    project_root="$(pp_project_root "$name")" || return 1
    [[ -f "${project_root}/HALT" ]]
}

# ---------------------------------------------------------------------------
# pp_load_config [name]
# Reads the project config file and exports every known field with a PP_*
# prefix so callers can distinguish them from ambient environment variables.
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
#
# Config file resolution order (highest to lowest precedence):
#   1. $(pp_project_root <name>)/project.cfg  — INI format (preferred)
#   2. $(pp_project_root <name>)/PROJECT.cfg  — legacy bash key=value (compat)
#
# For project.cfg (INI format), known fields are read explicitly from
# [project] and [versioning] sections via read_ini and exported with PP_ prefix.
#
# For PROJECT.cfg (legacy), the file is sourced into a subshell and new
# variables are re-exported with PP_ prefix (backward-compat path).
#
# Known fields exported (both paths):
#   PP_project_name, PP_dev_tree_path, PP_git_repo_url, PP_git_remote_name,
#   PP_workflow_type, PP_branch_prefix,
#   PP_max_patch, PP_max_minor, PP_max_major, PP_max_priorities_per_run,
#   PP_cm_release_pre_squash_hook, PP_cm_release_pre_tag_hook,
#   PP_cm_release_post_tag_hook,
#   PP_TEMP_DIR
#
# PP_TEMP_DIR resolution:
#   Reads [project] temp_dir from project.cfg (or temp_dir= from PROJECT.cfg).
#   When temp_dir is absent or empty, defaults to "projects/<project_name>"
#   (per-project subtree so cleanup of one project cannot reach another).
#   The exported PP_TEMP_DIR is the ABSOLUTE path:
#     $(pgai_temp_dir)/<temp_dir>
#   This ensures all per-project transient files land under one predictable
#   umbrella root, isolated by project, and cleanly sweepable per project.
# ---------------------------------------------------------------------------
pp_load_config() {
    local name
    name="$(pp_require_project_context "${1:-}")" || return 1
    local project_root
    project_root="$(pp_project_root "$name")" || return 1

    local new_cfg="${project_root}/project.cfg"   # INI format (preferred)
    local old_cfg="${project_root}/PROJECT.cfg"   # legacy bash key=value

    # ---------------------------------------------------------------------------
    # Internal helper: resolve PP_TEMP_DIR from a temp_dir value and project name.
    # Arguments:
    #   $1 — temp_dir value read from config (may be empty)
    #   $2 — project name (used to compute the default when temp_dir is absent)
    # Echoes the absolute path: <framework_temp_root>/<temp_dir>
    #
    # Default layout: when temp_dir is absent or empty, the project gets its own
    # subtree under the framework root:
    #   <framework_temp_root>/projects/<project_name>/
    # This isolates per-project temp so cleanup of one project cannot reach
    # another project's worktrees, doc dirs, or other transient files.
    # ---------------------------------------------------------------------------
    local _pp_resolve_temp_dir
    _pp_resolve_temp_dir() {
        local _td="${1:-}"
        local _pname="${2:-}"
        local _framework_root
        _framework_root="$(pgai_temp_dir)"
        # Default to projects/<project_name> when temp_dir is absent or empty.
        if [[ -z "$_td" ]]; then
            _td="projects/${_pname}"
        fi
        printf '%s/%s' "$_framework_root" "$_td"
    }

    if [[ -f "$new_cfg" ]]; then
        # --- INI path (project.cfg) ---
        # Read each known field from [project] section; source: project.cfg [project] <key>
        export PP_project_name="$(read_ini "$new_cfg" project project_name "")"
        export PP_dev_tree_path="$(read_ini "$new_cfg" project dev_tree_path "")"
        export PP_git_repo_url="$(read_ini "$new_cfg" project git_repo_url "")"
        export PP_git_remote_name="$(read_ini "$new_cfg" project git_remote_name "origin")"
        export PP_workflow_type="$(read_ini "$new_cfg" project workflow_type "release")"
        export PP_branch_prefix="$(read_ini "$new_cfg" project branch_prefix "")"
        # Source: project.cfg [project] temp_dir (per-project temp directory name)
        local _raw_temp_dir
        _raw_temp_dir="$(read_ini "$new_cfg" project temp_dir "")"
        export PP_TEMP_DIR="$(_pp_resolve_temp_dir "$_raw_temp_dir" "$name")"
        # Source: project.cfg [versioning] <key>
        export PP_max_patch="$(read_ini "$new_cfg" versioning max_patch "")"
        export PP_max_minor="$(read_ini "$new_cfg" versioning max_minor "")"
        export PP_max_major="$(read_ini "$new_cfg" versioning max_major "")"
        export PP_max_priorities_per_run="$(read_ini "$new_cfg" versioning max_priorities_per_run "")"
        # Source: project.cfg [hooks] <key>
        export PP_cm_release_pre_squash_hook="$(read_ini "$new_cfg" hooks cm_release_pre_squash_hook "")"
        export PP_cm_release_pre_tag_hook="$(read_ini "$new_cfg" hooks cm_release_pre_tag_hook "")"
        export PP_cm_release_post_tag_hook="$(read_ini "$new_cfg" hooks cm_release_post_tag_hook "")"
        return 0

    elif [[ -f "$old_cfg" ]]; then
        # --- Legacy path (PROJECT.cfg bash key=value) ---
        # Capture the set of variable names that exist before sourcing.
        local before_vars
        before_vars="$(compgen -v)"

        # Source the legacy config file in the current shell context.
        # shellcheck source=/dev/null
        source "$old_cfg"

        # Capture the set of variable names after sourcing and export new ones
        # with the PP_* prefix.
        local after_vars var value
        after_vars="$(compgen -v)"

        while IFS= read -r var; do
            if echo "$before_vars" | grep -qx "$var"; then
                continue
            fi
            case "$var" in
                PP_*|_|PIPESTATUS|BASH_*|FUNCNAME|LINENO|SECONDS|RANDOM|OLDPWD|REPLY)
                    continue ;;
            esac
            value="${!var}"
            export "PP_${var}=${value}"
        done < <(comm -13 <(echo "$before_vars" | sort) <(echo "$after_vars" | sort))

        # PP_TEMP_DIR: apply the default when temp_dir was absent from PROJECT.cfg.
        # When temp_dir was present, the loop above set PP_temp_dir (lowercase);
        # promote it to PP_TEMP_DIR and apply framework-root resolution.
        local _legacy_td="${PP_temp_dir:-}"
        export PP_TEMP_DIR="$(_pp_resolve_temp_dir "$_legacy_td" "$name")"
        return 0

    else
        echo "project_paths.sh: PROJECT.cfg not found at ${old_cfg} (and project.cfg not found at ${new_cfg})" >&2
        return 1
    fi
}

# ---------------------------------------------------------------------------
# pp_queue_path [name] <agent>
# Echoes the path to the named agent's backlog queue file for the given
# project: <tasks_dir>/queues/<agent>_backlog.md
#
# Arguments:
#   $1 — project name (optional when PGAI_PROJECT_NAME is set)
#   $2 — agent type (e.g. "coder", "pm", "tester", "cm", "writer", "bug")
#
# When called with one argument, that argument is the agent type and the
# project name is resolved from PGAI_PROJECT_NAME.
# When called with two arguments, the first is the project name and the
# second is the agent type.
#
# This is the single source of truth for queue file locations.
# Do not add parallel resolution logic elsewhere.
#
# Example:
#   pp_queue_path "pgai-agent-kanban" "coder"
#   # -> /path/to/kanban/projects/pgai-agent-kanban/tasks/queues/coder_backlog.md
# ---------------------------------------------------------------------------
pp_queue_path() {
    local name agent

    if [[ $# -ge 2 ]]; then
        # Two-argument form: <project_name> <agent>
        name="$(pp_require_project_context "${1:-}")" || return 1
        agent="${2:-}"
    else
        # One-argument form: <agent>; project name from env.
        name="$(pp_require_project_context "")" || return 1
        agent="${1:-}"
    fi

    if [[ -z "$agent" ]]; then
        echo "project_paths.sh: pp_queue_path: agent argument is required" >&2
        return 1
    fi

    local tasks_dir
    tasks_dir="$(pp_tasks_dir "$name")" || return 1

    echo "${tasks_dir}/queues/${agent}_backlog.md"
}
