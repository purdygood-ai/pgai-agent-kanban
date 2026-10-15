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
#   pp_resolve_project_name [name] — resolve --project by exact match then
#                               unambiguous prefix against projects.cfg;
#                               dual-channel refusal on not-found/ambiguous
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
#   pp_workflow_type  [name]  — echoes the workflow type string from [project] workflow_type;
#                               default 'release' when absent or empty
#   pp_branch_prefix  [name]  — optional branch name prefix from [project] branch_prefix;
#                               returns empty string when key is absent or empty
#   pp_push_to_remote [name]  — echoes 'true' only when the shared push-decision
#                               resolver (push_decision.sh) answers push: a
#                               registered project whose own project.cfg names
#                               dev_tree_path and does not set push_to_remote =
#                               false; 'false' otherwise, unresolved included
#   pp_push_policy <name> <site-tag> — the same decision for a CM release
#                               entry point: sets PP_PUSH_DECISION and
#                               PP_PUSH_TO_REMOTE, and logs one
#                               skipped-unresolved-project line when unresolved
#   pp_hook_required  [name] <phase> — echoes 'true' or 'false' from [hooks]
#                               cm_release_<phase>_hook_required; default 'false' when absent.
#                               <phase>: pre-squash | pre-tag | post-tag
#   validate_branch_prefix <value> — returns 0 for valid prefix (empty or [a-zA-Z0-9_-]+);
#                               returns non-zero and prints error for unsafe characters
#   pp_prefix_branch  [name] <branch> — returns PREFIX+branch; identity when prefix is empty
#   pp_prefix_tag     [name] <tag>    — returns PREFIX+tag; identity when prefix is empty
#   pp_strip_prefix_from_tag [name] <tag> — strips prefix from tag; returns bare semver
#   validate_git_remote_name <value> — returns 0 for a remote name the release
#                               machinery may pass to git, 1 otherwise (silent)
#   pp_git_remote_name [--quiet] [name] — the project's git remote: [project]
#                               git_remote_name, origin when absent or blank;
#                               an invalid value returns 1 (refusing
#                               dual-channel unless --quiet)
#   pp_repo_lock_path [name]  — path to the per-project repo flock file; single
#                               source of truth shared by wake dispatch and OVERWATCH
#   pp_max_rc_retries [name]  — effective RC-retry ceiling: project.cfg
#                               [release] max_rc_retries override when present,
#                               else kanban.cfg [release] max_rc_retries default.
#                               FAILS LOUD (non-zero, stderr message) when the
#                               key is absent from both sources — no silent
#                               fallback, unlike the other pp_max_* ceilings.
#   pp_deep_verify_mode [name]  — the project's DEEP-VERIFY scheduled-dispatch
#                               mode: project.cfg [release] deep_verify,
#                               else kanban.cfg [release] deep_verify,
#                               resolved to "weekly" or "off" on stdout.
#                               Accepts "weekly"/"true" and "off"/"false"
#                               (the legacy boolean spellings are recognized
#                               aliases, never echoed back). Absent/blank in
#                               both defaults to "weekly". FAILS LOUD
#                               (non-zero, stderr message naming the file,
#                               no stdout) for any other non-blank value —
#                               an unrecognized value must never be silently
#                               guessed into a schedule mode.
#   pp_deep_verify_offhours_window [name]  — the project's DEEP-VERIFY
#                               off-hours window ("the schedule knob"):
#                               project.cfg [release]
#                               deep_verify_offhours_window, else the same
#                               kanban.cfg key, "HH:MM-HH:MM". A malformed
#                               value counts as absent at its tier (one
#                               warning); with no well-formed value the
#                               shipped default (03:00-03:59) applies —
#                               this knob is a scheduling nicety, not an
#                               invariant, so it never fails loud.
#   pp_overwatch_resolve [name]  — whether OVERWATCH may autonomously act on
#                               a CM block for this project: project.cfg
#                               [release] overwatch_resolve override when
#                               present, else kanban.cfg [release]
#                               overwatch_resolve default. Accepts only the
#                               exact strings "true"/"false" on stdout.
#                               Absent, blank, or unreadable resolves to
#                               "false" — the opposite failure direction
#                               from pp_max_rc_retries: this key must never
#                               fail loud and must never default to "true".
#                               An unrecognized non-boolean value (a typo,
#                               'yes', a quoted empty string) is refused
#                               dual-channel through team/scripts/lib/
#                               error_contract.sh rather than guessed into
#                               either state.
#   pp_block_triage [name]  — whether OVERWATCH's block-triage sweep may
#                               act on a blocked task's Block Record for
#                               this project (requeue, promote, escalate),
#                               or only write the record and its
#                               diagnosis: project.cfg [overwatch]
#                               block_triage override when present, else
#                               kanban.cfg [overwatch] block_triage
#                               default. Same resolver semantics as
#                               pp_overwatch_resolve — exact "true"/"false"
#                               on stdout, an unrecognized value refused
#                               dual-channel — but absent, blank, or
#                               unreadable in both files resolves to
#                               "true", the shipped default.
#   pp_stale_lock_reap_after_wake_periods [name]  — the stale-lock reap
#                               threshold, in wake periods: project.cfg
#                               [locks] stale_lock_reap_after_wake_periods
#                               override when present, else kanban.cfg
#                               [locks] stale_lock_reap_after_wake_periods
#                               default (shipped default 3 when absent from
#                               both). Consumed by lock_provenance.sh's
#                               contention-path reaper.
#   pp_ship_posture [name]  — the project's ship posture, exactly "matrix"
#                               or "always": project.cfg [release]
#                               ship_posture override when present, else
#                               kanban.cfg [release] ship_posture, else
#                               "matrix". An unrecognized value resolves to
#                               "matrix" with one stderr warning line. On a
#                               push_to_remote = true lane, "always" also
#                               needs [release] ship_posture_confirm_remote
#                               = true (same chain), else "matrix" with one
#                               warning line. Never a refusal: status 0.
#
# Key Registry
# ------------
# Every project.cfg key any pp_* accessor above reads is listed here. This
# is a DISTINCT key set from team/scripts/lib/config_loader.sh's own Key
# Registry, which speaks only for kanban.cfg — see that block's own header
# note for why the two are not merged into one file. [release], [locks],
# [overwatch] and [archive] are the sections genuinely shared by both cfg
# files (a project.cfg override of a kanban.cfg-wide default); each is
# declared once, in config_loader.sh's registry, not duplicated here — see
# pp_max_rc_retries, pp_deep_verify_offhours_window,
# pp_overwatch_resolve, and pp_ship_posture (ship_posture and
# ship_posture_confirm_remote) above for the five [release] keys,
# pp_stale_lock_reap_after_wake_periods above for the one [locks] key,
# pp_block_triage above for [overwatch] block_triage,
# resolve_requeue_budget() in team/pgai_agent_kanban/overwatch/
# block_requeue.py for [overwatch] requeue_budget, and
# resolve_archive_count() in team/pgai_agent_kanban/archive/config.py for
# the one [archive] key (the last two read in Python; no pp_* accessor).
# The whole read set, shared sections included, is PROJECT_CFG_KEYS in
# team/pgai_agent_kanban/ops/projects.py; the project.cfg template
# (team/config/project.cfg_example) documents every key in it, and every
# new project.cfg is rendered from that template.
#
# Status: REQUIRED = the pp_* accessor has no default; absence is a caller
#         error. OPTIONAL = the pp_* accessor applies its own documented
#         default when absent.
# Default shown is the value the matching pp_* accessor returns when the
# key is absent, EXCEPT where marked N/A: a per-installation identity or
# example field (every real project.cfg legitimately carries a different
# value here) has no single canonical default to check a document against,
# so the default-value comparison is skipped for it — see
# lint_doc_command_truth.py's "Config-key parity" section for why.
#
# [project]
#   project_name            OPTIONAL  N/A  (per-installation identity;
#                                            create-project.sh and
#                                            add-project.sh write it)
#   dev_tree_path           OPTIONAL  N/A  (per-installation path; see above)
#   git_repo_url            OPTIONAL  N/A  (per-installation URL; see above)
#   git_remote_name         OPTIONAL  origin
#   workflow_type           OPTIONAL  release
#   branch_prefix           OPTIONAL  N/A  (pp_branch_prefix's own absent-key
#                                           fallback is "" — no prefix — but
#                                           create-project.sh scaffolds every
#                                           new project.cfg with "ai_"; the
#                                           two are different claims about
#                                           "the default," so this key is
#                                           not defaulted-checked rather than
#                                           picking one and calling the
#                                           other line wrong)
#   push_to_remote          OPTIONAL  true
#   temp_dir                OPTIONAL  N/A  (per-installation name; absent
#                                           resolves to projects/<project_name>
#                                           under the framework temp root,
#                                           itself installation-specific)
#
# [versioning]
#   max_patch               OPTIONAL  21
#   max_minor               OPTIONAL  13
#   max_major               OPTIONAL  0
#                           (the three version ceilings: an absent or empty
#                            key, or no project.cfg at all, reads as the
#                            default shown, held once in
#                            pgai_agent_kanban.lib.version_ceiling
#                            CEILING_DEFAULTS; `none` means no ceiling and
#                            is the only spelling that removes one; deleting
#                            the line restores the default rather than
#                            removing the cap; any other value blocks the
#                            project with one narrated line)
#   max_priorities_per_run  OPTIONAL  1
#
# [hooks]
#   cm_release_pre_squash_hook           OPTIONAL  ""
#   cm_release_pre_squash_hook_required  OPTIONAL  false
#   cm_release_pre_tag_hook              OPTIONAL  ""
#   cm_release_pre_tag_hook_required     OPTIONAL  false
#   cm_release_post_tag_hook             OPTIONAL  ""
#   cm_release_post_tag_hook_required    OPTIONAL  false
#
# [debug]
#   verbose_mode            OPTIONAL  false
#   verbose_agents          OPTIONAL  pm,coder,writer,tester,cm
#
# [training]
#   reasoning_trace         OPTIONAL  false
#   training_agents         OPTIONAL  ""

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
# shellcheck source=error_contract.sh
if ! declare -F pgai_emit_error_contract >/dev/null 2>&1; then
    source "${_PP_LIB_DIR}/error_contract.sh"
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
# Project Name Resolution (prefix matching against the projects.cfg registry)
# ---------------------------------------------------------------------------
#
# pp_project_root (above) and every other pp_* path helper builds a path by
# string concatenation only — a typo or an intentionally-shortened --project
# value silently yields a path to a project that does not exist. The
# resolver below is the opt-in fix: callers that are looking up an EXISTING,
# already-registered project (every operator verb except create-project.sh /
# add-project.sh, whose whole job is naming a NOT-YET-registered project)
# should resolve the operator's raw --project value through
# pp_resolve_project_name before handing it to pp_project_root. This
# mirrors, at the project-name layer, the same exact-then-prefix algorithm
# pgai_agent_kanban.ops.resolve.resolve_item already uses for --key: try an
# exact match first, then an unambiguous prefix match, refusing dual-channel
# (never guessing) on not-found or on ambiguous.

# ---------------------------------------------------------------------------
# _pp_registered_project_names <projects_cfg_path>
# Internal helper. Echoes every registered project name, one per line, from
# either projects.cfg format (INI `[project:NAME]` sections, or the legacy
# `name:priority[:color]` colon form) — the same two formats
# lib/projects.sh's projects_cfg_list already parses. This helper is
# self-contained (no dependency on lib/projects.sh) specifically because
# lib/projects.sh's own header requires it be sourced AFTER this file
# (projects_cfg_active needs pp_project_halted); sourcing it from here would
# invert that ordering constraint. Order does not matter for the caller —
# pp_resolve_project_name only needs the set of names, not registration
# order or priority.
# ---------------------------------------------------------------------------
_pp_registered_project_names() {
    local cfg="$1"
    [[ -f "$cfg" ]] || return 0
    awk '
        /^[[:space:]]*#/ { next }
        /^[[:space:]]*$/ { next }
        /^\[project:[a-zA-Z0-9_-]+\]$/ {
            print substr($0, 10, length($0) - 10)
            next
        }
        /^[[:space:]]*\[/ { next }
        {
            # Colon-legacy: name:priority[:color]. Lines with no ':' (e.g.
            # INI key=value fields like "priority=1") split to a single
            # field and are silently skipped — this awk block runs
            # unconditionally over both formats rather than branching on a
            # detected format string.
            n = split($0, f, ":")
            if (n >= 2) {
                name = f[1]
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
                if (name != "") print name
            }
        }
    ' "$cfg"
}

# ---------------------------------------------------------------------------
# _pp_join_names <name> [name...]
# Internal helper. Echoes the given names joined with ", " for use in a
# refusal message's candidate list. ("$*" with IFS set only uses the FIRST
# character of IFS to join — a two-character separator like ", " requires
# an explicit loop, not the IFS-and-"$*" idiom.)
# ---------------------------------------------------------------------------
_pp_join_names() {
    local _pjn_out="" _pjn_n
    for _pjn_n in "$@"; do
        if [[ -z "$_pjn_out" ]]; then
            _pjn_out="$_pjn_n"
        else
            _pjn_out="${_pjn_out}, ${_pjn_n}"
        fi
    done
    echo "$_pjn_out"
}

# ---------------------------------------------------------------------------
# pp_resolve_project_name [name]
#
# Resolves a project name by exact match, then by unambiguous prefix,
# against the projects registered in projects.cfg. Echoes the resolved,
# canonical project name on success.
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME (via
# pp_require_project_context), then error (that error is unchanged from
# pp_require_project_context's own — this function adds registry
# resolution, it does not change what happens when no name is supplied at
# all).
#
# Prefix matching keeps resolve_item's hyphen-boundary rule (see
# pgai_agent_kanban.ops.resolve._collect_prefix_matches): a candidate name
# matches the typed prefix only when it equals the prefix exactly, or
# continues immediately with a literal hyphen ("<prefix>-..."). This is the
# same boundary that stops a shorter key from matching an unrelated longer
# name that merely happens to start with the same characters.
#
# Returns 0 and echoes the resolved name on success:
#   - projects.cfg does not exist at all — nothing to resolve against, so
#     the candidate is echoed back unchanged (best-effort pass-through, not
#     a refusal — this keeps every caller that operates on a project
#     directory without a maintained registry working exactly as it did
#     before this resolver existed; the caller's own existing
#     directory-existence check is still what catches a genuine typo in
#     that case);
#   - the candidate matches exactly one registered project, by exact name
#     or by unambiguous prefix.
# Returns 1 (dual-channel refusal to stderr, nothing on stdout) when:
#   - projects.cfg exists but no registered project matches the given name
#     or prefix (not-found);
#   - two or more registered projects share the given prefix (ambiguous).
# ---------------------------------------------------------------------------
pp_resolve_project_name() {
    local candidate
    candidate="$(pp_require_project_context "${1:-}")" || return 1

    local root
    root="$(_pp_kanban_root)" || return 1
    local cfg="${root}/projects.cfg"

    if [[ ! -f "$cfg" ]]; then
        # Pass-through, not a refusal: with no registry file at all there is
        # nothing to resolve a prefix against, and every existing caller of
        # pp_project_root already worked this way (raw name straight into a
        # path). Requiring a registry here would turn a project directory
        # that has never been run through create-project.sh/add-project.sh
        # into an unaddressable one — the caller's own directory-existence
        # check (present in every wired verb) is still the backstop that
        # catches a genuine typo when no registry exists to disambiguate
        # against.
        echo "$candidate"
        return 0
    fi

    local -a _pp_names=()
    local _pp_n
    while IFS= read -r _pp_n; do
        [[ -n "$_pp_n" ]] && _pp_names+=("$_pp_n")
    done < <(_pp_registered_project_names "$cfg")

    # Exact match first — mirrors resolve_item's own exact-then-prefix order.
    for _pp_n in "${_pp_names[@]+"${_pp_names[@]}"}"; do
        if [[ "$_pp_n" == "$candidate" ]]; then
            echo "$_pp_n"
            return 0
        fi
    done

    # Prefix match: candidate name must continue with a literal hyphen right
    # after the typed prefix (the hyphen-boundary rule described above).
    local -a _pp_matches=()
    for _pp_n in "${_pp_names[@]+"${_pp_names[@]}"}"; do
        if [[ "$_pp_n" == "${candidate}-"* ]]; then
            _pp_matches+=("$_pp_n")
        fi
    done

    if [[ "${#_pp_matches[@]}" -eq 1 ]]; then
        echo "${_pp_matches[0]}"
        return 0
    fi

    if [[ "${#_pp_matches[@]}" -eq 0 ]]; then
        # BLOCK-WORTHY (docs/blocking-site-census.md): a typo or an
        # unregistered name has no directory this framework should ever
        # write to — refused rather than silently concatenating a path to
        # a project that does not exist.
        local _pp_remedy
        if [[ "${#_pp_names[@]}" -eq 0 ]]; then
            # No project is registered at all — naming the (empty) list
            # gives the reader nothing to act on, so point at the verb
            # that registers one instead.
            _pp_remedy="No project is registered in ${cfg}. Register one first: team/scripts/add-project.sh --project ${candidate}."
        else
            _pp_remedy="Check the project name against projects.cfg (registered: $(_pp_join_names "${_pp_names[@]}"))."
        fi
        pgai_emit_error_contract \
            "project_resolve.not_found" \
            "pp_resolve_project_name: no project matches '${candidate}' in ${cfg}." \
            "${_pp_remedy}"
        # CENSUS-ANCHOR: project-resolve-not-found-01
        return 1
    fi

    # BLOCK-WORTHY (docs/blocking-site-census.md): two or more registered
    # projects share this prefix — picking one silently would risk operating
    # on the wrong project's data, so this refuses and names both rather
    # than guessing.
    pgai_emit_error_contract \
        "project_resolve.ambiguous" \
        "pp_resolve_project_name: '${candidate}' matches multiple registered projects: $(_pp_join_names "${_pp_matches[@]}")." \
        "Re-run with a longer, unambiguous prefix or the full name, e.g. --project ${_pp_matches[0]}."
    # CENSUS-ANCHOR: project-resolve-ambiguous-01
    return 1
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
# pp_agent_role_is_non_cm
# True (exit 0) when PGAI_AGENT_ROLE is set to a non-empty value naming any
# role other than "cm" (compared case-insensitively); false (exit 1) when
# the role is "cm" or when the variable is unset or empty.
#
# The single answer to "would the remote boundary refuse this process's
# fetch, pull or push?" for callers that want to skip a best-effort remote
# reach rather than have team/scripts/lib/git_boundary_guard.sh refuse it
# and spend an evidence record on a crossing nobody attempted (the CM
# scripts' pre-flight `git fetch origin`, for instance). An unset or empty
# role -- an operator running a script by hand, outside the wake/guard
# machinery -- is deliberately NOT non-CM: that shell's fetch is not
# guaranteed a refusal, so callers leave its behaviour unchanged.
#
# Usage:
#   if pp_agent_role_is_non_cm; then
#       echo "skipping git fetch origin (role ${PGAI_AGENT_ROLE} never touches the remote)"
#   else
#       git -C "$repo" fetch origin
#   fi
pp_agent_role_is_non_cm() {
    local _role_lc
    _role_lc="$(printf '%s' "${PGAI_AGENT_ROLE:-}" | tr '[:upper:]' '[:lower:]')"
    [[ -n "$_role_lc" && "$_role_lc" != "cm" ]]
}

# pp_last_released_version [name]
# Echoes the highest bare semver tag (vX.Y.Z) reachable in the project's dev
# tree, after stripping the configured branch_prefix from candidate tags.
#
# When branch_prefix is non-empty (e.g. "ai_"), the preferred source is the
# local "${prefix}main" branch (e.g. "ai_main") for merged-tag lookups.
# When branch_prefix is empty, the preferred source is "main".
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
#
# Resolution path:
#   1. Reads dev_tree_path from $(pp_project_root <name>)/project.cfg or PROJECT.cfg.
#   2. Reads branch_prefix from the same config file (may be empty).
#   3. Runs git -C "$dev_tree" fetch <remote> --tags --quiet, where <remote>
#      is pp_git_remote_name's answer (origin unless [project]
#      git_remote_name names another; an invalid value skips this step and
#      step 4) (best-effort;
#      failure is ignored so callers in air-gapped or read-only environments
#      are not broken). SKIPPED in two cases, either one sufficient on its
#      own: PGAI_GIT_LOCAL_ONLY is set to any non-empty value — render-path
#      callers (dashboards) opt into this so a version lookup never reaches
#      the network, and the wake layer (team/scripts/lib/wake_guard_wiring.sh)
#      exports it for every dispatched role other than "cm", so a non-CM
#      agent wake's lookup stays local whatever copy of this file the task
#      sources — or PGAI_AGENT_ROLE names anything other than "cm"
#      (case-insensitive). The role check is automatic, not caller-declared:
#      team/scripts/lib/git_boundary_guard.sh refuses this exact fetch for
#      every non-CM role, so attempting it never changes the answer, only
#      appends a boundary-evidence record for a crossing that never
#      happened. The two mechanisms are deliberately redundant: the flag
#      lives in the launcher and survives an installed copy of this file
#      that predates the role check; the role check covers a caller that
#      sets the role without the flag. An unset role — a human running this
#      directly, outside the wake/guard machinery — is left unchanged, since
#      its fetch is not guaranteed to be refused. Either skip leaves local
#      tags authoritative and resolution proceeds straight to step 4 using
#      whatever refs are already on disk.
#   4. Tries to list git tags merged into <remote>/<main_branch> (e.g. origin/ai_main).
#      When prefix is non-empty: filters to tags starting with PREFIX followed
#      by 'v'; strips the prefix from each candidate before semver comparison.
#      Tags from a different prefix (e.g. human_v0.31.0 when prefix=ai_) are
#      excluded.
#      When prefix is empty: filters to bare ^v[0-9]+\.[0-9]+\.[0-9]+$ tags.
#      Sorts stripped candidates with sort -V, returns the highest as bare vX.Y.Z.
#   5. When <remote>/<main_branch> is unavailable (remote not configured, not yet
#      pushed, or air-gapped), retries the same tag scan against the local
#      <main_branch> ref (e.g. ai_main) instead of <remote>/<main_branch>.
#   6. When neither the remote ref nor local <main_branch> is available, falls back to
#      scanning ALL local tags in the repo that match the prefix pattern, and
#      returns the highest.  This covers a repo that has real release tags but
#      whose main branch is absent locally (e.g. a shallow clone or a
#      re-registered project that was never pushed from this machine).
#   7. Falls back to release-state.md '## Last Released' only when no git repo
#      is available: document-workflow projects have no git repo, so the git-tag
#      path always misses; finalize.sh writes '## Last Released' to
#      release-state.md after publishing, and this resolver honours it.
#      The release-state.md fallback is skipped for projects that have a
#      working git repo — code projects prefer git tags.
#   8. Falls back to "v0.0.0" sentinel when:
#        - project.cfg/PROJECT.cfg missing or unreadable
#        - dev_tree_path unset or not a git repo AND release-state.md absent
#          or has no Last Released value (or it is 'none')
#        - no matching tags found anywhere in the repo
#
# The function does NOT change the caller's CWD.  All git operations use
# git -C "$dev_tree" rather than cd.
#
# Return value is ALWAYS bare vX.Y.Z — callers must not add or remove prefix.
#
# Example:
#   pp_last_released_version "pgai-agent-kanban"
#   # -> v1.2.2  (from tag ai_v1.2.2 when branch_prefix=ai_, even without origin/ai_main)
#   # -> v0.30.1  (from tag v0.30.1 when branch_prefix is empty)
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
    # Reads '## Last Released' from release-state.md in the project root
    # through the one release-state reader (release_state_section in
    # lib/cm/common.sh, loaded here on first use so callers on the git-tag
    # path never pay for it). Returns a valid bare vX.Y.Z semver, or empty
    # string when the file, heading or value is missing or not a version.
    # Used as the fallback for document-workflow (no-git-repo) projects.
    # ---------------------------------------------------------------------------
    _pp_read_last_released_from_state() {
        local _root="$1"
        if ! declare -F release_state_section >/dev/null 2>&1; then
            # shellcheck source=cm/common.sh
            source "${_PP_LIB_DIR}/cm/common.sh" 2>/dev/null || { echo ""; return 0; }
        fi
        local _val
        _val="$(release_state_section "${_root}/release-state.md" "Last Released")" || _val=""
        if [[ "$_val" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            echo "$_val"
        else
            echo ""
        fi
    }

    # ---------------------------------------------------------------------------
    # Internal helper: _pp_scan_tags_from_ref <dev_tree> <ref> <prefix>
    # Lists tags merged into <ref> (or all tags when ref is empty), filtered to
    # the given prefix, strips the prefix, sorts by semver, and echoes the
    # highest.  Echoes empty string when no matching tags are found.
    # When <ref> is empty, scans ALL local tags (unrestricted merge filter).
    # ---------------------------------------------------------------------------
    _pp_scan_tags_from_ref() {
        local _dt="$1"
        local _ref="$2"
        local _pfx="$3"
        local _escaped_pfx
        _escaped_pfx="$(printf '%s' "$_pfx" | sed 's/[.[\*^${}|()]/\\&/g')"

        local _raw_tags
        if [[ -n "$_ref" ]]; then
            _raw_tags="$(git -C "$_dt" tag --merged "$_ref" 2>/dev/null || true)"
        else
            _raw_tags="$(git -C "$_dt" tag -l 2>/dev/null || true)"
        fi

        if [[ -n "$_pfx" ]]; then
            printf '%s\n' "$_raw_tags" \
                | grep -E "^${_escaped_pfx}v[0-9]+\.[0-9]+\.[0-9]+\$" \
                | sed "s|^${_escaped_pfx}||" \
                | sort -V \
                | tail -n1
        else
            printf '%s\n' "$_raw_tags" \
                | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' \
                | sort -V \
                | tail -n1
        fi
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

    # Best-effort fetch — never fail the function.  Skipped, and resolution
    # proceeds with whatever tags/refs already exist on disk, under either:
    #   - PGAI_GIT_LOCAL_ONLY set to any non-empty value — render-path
    #     callers (dashboards) opt into this so a version lookup never
    #     performs a network operation, and the wake layer
    #     (wake_guard_wiring.sh) exports it for every non-CM dispatch so
    #     the skip holds even where this file's own role check below is
    #     absent from the copy being sourced; or
    #   - PGAI_AGENT_ROLE naming anything other than "cm" (case-insensitive)
    #     — every non-CM role's fetch is refused by this same repo's
    #     team/scripts/lib/git_boundary_guard.sh, so running it changes no
    #     answer and only records a boundary-evidence line for a crossing
    #     that never happened. CM and an unset role (a human
    #     invoking this directly, outside the wake/guard machinery) still
    #     get the real fetch, since neither is guaranteed a refusal.
    local _plrv_role_lc=""
    if [[ -n "${PGAI_AGENT_ROLE:-}" ]]; then
        _plrv_role_lc="$(printf '%s' "$PGAI_AGENT_ROLE" | tr '[:upper:]' '[:lower:]')"
    fi
    # The remote is the project's own (pp_git_remote_name): origin when
    # git_remote_name is absent or blank. An invalid value is not refused
    # here -- this is a read -- it skips the fetch and the remote tier, and
    # resolution continues from the local refs.
    local remote=""
    remote="$(pp_git_remote_name --quiet "$name")" || remote=""
    if [[ -z "${PGAI_GIT_LOCAL_ONLY:-}" && ( -z "$_plrv_role_lc" || "$_plrv_role_lc" == "cm" ) ]]; then
        if [[ -n "$remote" ]]; then
            git -C "$dev_tree" fetch "$remote" --tags --quiet 2>/dev/null || true
        fi
    fi

    # Resolve the canonical main branch name for this project.
    # When branch_prefix is non-empty (e.g. "ai_"), release.sh operates on
    # ai_main rather than bare main.  Tags are created on ai_main commits, so
    # we prefer <remote>/ai_main for the merged-tag scan.
    local main_branch
    main_branch="${prefix}main"

    local highest=""

    # --- Tier 1: tags merged into <remote>/<main_branch> ---
    if [[ -n "$remote" ]] && git -C "$dev_tree" rev-parse --verify "${remote}/${main_branch}" &>/dev/null 2>&1; then
        highest="$(_pp_scan_tags_from_ref "$dev_tree" "${remote}/${main_branch}" "$prefix")"
    fi

    # --- Tier 2: tags merged into local <main_branch> (when the remote ref is unavailable) ---
    if [[ -z "$highest" ]]; then
        if git -C "$dev_tree" rev-parse --verify "${main_branch}" &>/dev/null 2>&1; then
            highest="$(_pp_scan_tags_from_ref "$dev_tree" "${main_branch}" "$prefix")"
        fi
    fi

    # --- Tier 3: scan ALL local tags (covers repos with tags but no local main) ---
    if [[ -z "$highest" ]]; then
        highest="$(_pp_scan_tags_from_ref "$dev_tree" "" "$prefix")"
    fi

    if [[ -z "$highest" ]]; then
        echo "$_sentinel"
        return 0
    fi

    echo "$highest"
}

# ---------------------------------------------------------------------------
# pp_max_patch / pp_max_minor / pp_max_major [name]
# Echo the version ceiling in effect for one semver component of the named
# project, read from project.cfg [versioning] max_patch / max_minor /
# max_major (or the bare key in a legacy PROJECT.cfg).
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
#
# What each accessor echoes (exit 0 unless noted):
#   - the configured non-negative whole number (0 is a real ceiling: only
#     component value 0 is allowed);
#   - the documented default when the key is absent or empty, or the project
#     has no project.cfg at all: 21 for max_patch, 13 for max_minor, 0 for
#     max_major. The defaults live in one place,
#     pgai_agent_kanban.lib.version_ceiling (CEILING_DEFAULTS), read here
#     through pp_run_ops only when a key is absent. Deleting the line
#     therefore restores the default; it never removes the cap;
#   - an empty string for the explicit "no ceiling" spelling `none` (any
#     case) — empty is the "no constraint" signal every caller tests with
#     [[ -n ]];
#   - nothing, exit 1, for a malformed value (anything else): exactly one
#     line on stderr names the file, the key and the bad value. A caller
#     must not discard that line and must treat the project as
#     ceiling-blocked — never as "no ceiling". Also exit 1 when the project
#     cannot be resolved or the default cannot be read.
# One pair of matching surrounding quotes is stripped before parsing.
#
# Example:
#   pp_max_major "pgai-agent-kanban"   # -> 0 (configured or default), or "" for none
# ---------------------------------------------------------------------------
_pp_version_ceiling() {
    local accessor="$1" key="$2" name
    name="$(pp_require_project_context "${3:-}")" || return 1
    local project_root cfg_file=""
    project_root="$(pp_project_root "$name")" || return 1
    cfg_file="$(_pp_project_cfg_file "$project_root")"

    local raw=""
    [[ -n "$cfg_file" ]] && raw="$(_pp_read_cfg_key "$cfg_file" versioning "$key" "")"
    if [[ ${#raw} -ge 2 && ( "$raw" == \"*\" || "$raw" == \'*\' ) ]]; then
        raw="${raw:1:${#raw}-2}"
    fi

    if [[ -z "$raw" ]]; then
        # Absent — the documented default from the shared source.
        local default
        if ! default="$(_pp_version_ceiling_default "$key")"; then
            echo "project_paths.sh: ${accessor}: cannot read the default for ${key} from pgai_agent_kanban.lib.version_ceiling; the project '${name}' is ceiling-blocked until the kanban install is repaired" >&2
            return 1
        fi
        echo "$default"
        return 0
    fi

    if [[ "${raw,,}" == "none" ]]; then
        echo ""
        return 0
    fi

    if ! [[ "$raw" =~ ^[0-9]+$ ]]; then
        local default_text
        default_text="$(_pp_version_ceiling_default "$key" 2>/dev/null)" || default_text="?"
        echo "project_paths.sh: ${accessor}: version ceiling unreadable: ${cfg_file} [versioning] ${key} = '${raw}' is not a non-negative whole number or none; the project is ceiling-blocked until it is fixed -- write a number, write none for no ceiling, or delete the line for the default ${default_text}" >&2
        return 1
    fi

    echo "$raw"
}

# _pp_version_ceiling_default <key> — echo the documented default for one
# ceiling key from pgai_agent_kanban.lib.version_ceiling.
_pp_version_ceiling_default() {
    if ! declare -F pp_run_ops >/dev/null 2>&1; then
        # shellcheck source=pp_run_ops.sh
        source "${_PP_LIB_DIR}/pp_run_ops.sh"
    fi
    pp_run_ops pgai_agent_kanban.lib.version_ceiling default "$1"
}

pp_max_patch() { _pp_version_ceiling pp_max_patch max_patch "${1:-}"; }
pp_max_minor() { _pp_version_ceiling pp_max_minor max_minor "${1:-}"; }
pp_max_major() { _pp_version_ceiling pp_max_major max_major "${1:-}"; }

# ---------------------------------------------------------------------------
# pp_max_priorities_per_run [name]
# Echoes the configured max_priorities_per_run value from project.cfg for the
# named project, or an empty string when the field is absent.
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
#
# Same non-negative-integer validation as the version ceilings above, but
# with no default and no `none` spelling.
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
# pp_max_rc_retries [name]
# Echoes the effective RC-retry ceiling for the named project: the
# per-project override from project.cfg [release] max_rc_retries when
# present, otherwise the kanban-wide default from $KANBAN_ROOT/kanban.cfg
# [release] max_rc_retries.
#
# This is "the ceiling": OVERWATCH's resolution matcher compares an RC's
# attempt count against this value; at or above the ceiling a block routes
# to HALT-hard with the full retry history instead of a further
# cancel-and-rebundle attempt.
#
# Unlike pp_max_priorities_per_run (which returns an empty string when
# unset) and the version ceilings pp_max_minor / pp_max_major / pp_max_patch
# (which apply their documented defaults when unset), this
# ceiling has no safe empty meaning — the resolver needs a concrete integer
# to compare an attempt count against. So absence in BOTH project.cfg and
# kanban.cfg is NOT treated as "unlimited retries"; it fails loud instead:
# prints an error naming the key and both file paths checked, and returns
# non-zero. Callers must not substitute a hardcoded fallback on a non-zero
# return — that would silently reintroduce the exact "assume a default
# when a value is required" behavior Goal 4 forbids.
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
#
# Source (checked in this order):
#   1. project.cfg [release] max_rc_retries  (or bare key in legacy PROJECT.cfg)
#   2. kanban.cfg  [release] max_rc_retries  (kanban root, shipped default 3)
#
# Validation: must be a non-negative integer in whichever source supplies it.
# An invalid (non-integer) value is also a fail-loud non-zero return, naming
# the offending file and value — never silently coerced or ignored.
#
# Example:
#   pp_max_rc_retries "pgai-agent-kanban"   # -> 1  (explicit self-build override)
#   pp_max_rc_retries "some-other-project"  # -> 3  (falls through to kanban.cfg default)
# ---------------------------------------------------------------------------
pp_max_rc_retries() {
    local name
    name="$(pp_require_project_context "${1:-}")" || return 1

    local kroot
    kroot="$(_pp_kanban_root)" || return 1
    local kanban_cfg="${kroot}/kanban.cfg"

    local project_root cfg_file
    project_root="${kroot}/projects/${name}"
    cfg_file="$(_pp_project_cfg_file "$project_root")"

    # Tier 1: project.cfg override.
    local project_raw=""
    if [[ -n "$cfg_file" ]]; then
        project_raw="$(_pp_read_cfg_key "$cfg_file" release max_rc_retries "")"
    fi
    if [[ -n "$project_raw" ]]; then
        if ! [[ "$project_raw" =~ ^[0-9]+$ ]]; then
            echo "project_paths.sh: pp_max_rc_retries: invalid value '${project_raw}' for project '${name}' in ${cfg_file} [release] max_rc_retries (expected non-negative integer)" >&2
            return 1
        fi
        echo "$project_raw"
        return 0
    fi

    # Tier 2: kanban.cfg default.
    local kanban_raw
    kanban_raw="$(read_ini "$kanban_cfg" release max_rc_retries "")"
    if [[ -n "$kanban_raw" ]]; then
        if ! [[ "$kanban_raw" =~ ^[0-9]+$ ]]; then
            echo "project_paths.sh: pp_max_rc_retries: invalid value '${kanban_raw}' in ${kanban_cfg} [release] max_rc_retries (expected non-negative integer)" >&2
            return 1
        fi
        echo "$kanban_raw"
        return 0
    fi

    # Fail loud: no source supplied a value. Never assume a default here —
    # a silent fallback is exactly the behavior Goal 4 forbids for a
    # required-at-resolve-time ceiling.
    echo "project_paths.sh: pp_max_rc_retries: max_rc_retries is required but absent for project '${name}'. Checked ${cfg_file:-<no project.cfg found for ${name}>} [release] max_rc_retries and ${kanban_cfg} [release] max_rc_retries — set one of them." >&2
    return 1
}

# ---------------------------------------------------------------------------
# pp_overwatch_resolve [name]
# Echoes "true" or "false": whether OVERWATCH is authorized to act
# autonomously on a resolvable CM block for the named project, rather than
# only observing and escalating. Same project.cfg-overrides-kanban.cfg
# precedence as pp_max_rc_retries above:
#
#   1. project.cfg [release] overwatch_resolve  (or bare key in legacy PROJECT.cfg)
#   2. kanban.cfg  [release] overwatch_resolve  (kanban root, shipped default: absent)
#
# Unlike pp_max_rc_retries, absence must never be an error and must never
# resolve to "true" — this key gates an autonomy grant, so the fail-safe
# direction is the opposite of a required ceiling. Absent, blank, or
# unreadable in BOTH sources resolves to "false" with no diagnostic at all;
# this is the ordinary, expected state for every project until an operator
# deliberately opts in.
#
# Validation: only the exact strings "true" and "false" are accepted. Any
# other non-blank value (a typo, 'yes', a quoted empty string) is NEVER
# guessed into either boolean — it is refused dual-channel through
# team/scripts/lib/error_contract.sh, naming the key, the offending value,
# the file it came from, and a runnable remedy.
#
# CENSUS (docs/blocking-site-census.md): BLOCK-WORTHY. A malformed
# overwatch_resolve value could otherwise silently leave OVERWATCH either
# granted or denied autonomous resolution authority against the operator's
# actual intent — resolving it wrong in either direction is a real
# authority mistake, not a cosmetic one, so this site refuses rather than
# guesses.
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
#
# Example:
#   pp_overwatch_resolve "pgai-agent-kanban"  # -> 'false' (key unset in both files; shipped default)
#   pp_overwatch_resolve "opted-in-project"   # -> 'true'  (project.cfg [release] overwatch_resolve = true)
# ---------------------------------------------------------------------------
pp_overwatch_resolve() {
    local name
    name="$(pp_require_project_context "${1:-}")" || return 1

    local kroot
    kroot="$(_pp_kanban_root)" || return 1
    local kanban_cfg="${kroot}/kanban.cfg"

    local project_root cfg_file
    project_root="${kroot}/projects/${name}"
    cfg_file="$(_pp_project_cfg_file "$project_root")"

    # Tier 1: project.cfg override.
    local raw="" source_file=""
    if [[ -n "$cfg_file" ]]; then
        raw="$(_pp_read_cfg_key "$cfg_file" release overwatch_resolve "")"
    fi
    if [[ -n "$raw" ]]; then
        source_file="$cfg_file"
    else
        # Tier 2: kanban.cfg default. A read_ini failure (unreadable file)
        # prints nothing to stdout, so it folds into the same blank-raw
        # path as "absent" — exactly the fail-safe-to-false behavior this
        # key requires.
        raw="$(read_ini "$kanban_cfg" release overwatch_resolve "")"
        source_file="$kanban_cfg"
    fi

    case "$raw" in
        ""|false)
            echo "false"
            return 0
            ;;
        true)
            echo "true"
            return 0
            ;;
        *)
            pgai_emit_error_contract \
                "overwatch_resolve.invalid_value" \
                "pp_overwatch_resolve: unrecognized value '${raw}' for project '${name}' in ${source_file} [release] overwatch_resolve (expected 'true' or 'false')." \
                "Set [release] overwatch_resolve to 'true' or 'false' in ${source_file}, or remove/comment out the key to default to 'false'." \
                "$name"
            # CENSUS-ANCHOR: overwatch-resolve-invalid-01
            return 1
            ;;
    esac
}

# ---------------------------------------------------------------------------
# pp_block_triage [name]
# Echoes "true" or "false": whether OVERWATCH's block-triage sweep may take
# action on a blocked task's Block Record for the named project (requeue,
# promote, escalate), or must only write the record and its diagnosis.
# Same resolver semantics as pp_overwatch_resolve above, under its own
# [overwatch] section:
#
#   1. project.cfg [overwatch] block_triage  (or bare key in legacy PROJECT.cfg)
#   2. kanban.cfg  [overwatch] block_triage  (kanban root, shipped default: true)
#
# Absent, blank, or unreadable in BOTH sources resolves to "true" with no
# diagnostic. That is the opposite default from pp_overwatch_resolve on
# purpose: an upgrade leaves an existing kanban.cfg untouched, so an install
# that predates this key must behave the way kanban.cfg_example ships it
# (triage on) rather than silently differently. An operator who wants
# record-and-diagnose only sets the key to false in either file.
#
# Validation: only the exact strings "true" and "false" are accepted. Any
# other non-blank value (a typo, 'yes', 'on') is NEVER guessed into either
# boolean — it is refused dual-channel through
# team/scripts/lib/error_contract.sh, naming the key, the offending value,
# the file it came from, and a runnable remedy.
#
# CENSUS (docs/blocking-site-census.md): BLOCK-WORTHY. A malformed
# block_triage value could otherwise silently grant or withhold the
# sweep's authority to mutate task state against the operator's intent.
# The stop is scoped to the caller's triage action alone: the sweep treats
# a non-zero return as a safety refusal — the task is left unchanged and
# the refusal is logged — so nothing here can stop a wake or a release.
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
#
# Example:
#   pp_block_triage "pgai-agent-kanban"  # -> 'true'  (key unset in both files; shipped default)
#   pp_block_triage "watch-only-project" # -> 'false' (project.cfg [overwatch] block_triage = false)
# ---------------------------------------------------------------------------
pp_block_triage() {
    local name
    name="$(pp_require_project_context "${1:-}")" || return 1

    local kroot
    kroot="$(_pp_kanban_root)" || return 1
    local kanban_cfg="${kroot}/kanban.cfg"

    local project_root cfg_file
    project_root="${kroot}/projects/${name}"
    cfg_file="$(_pp_project_cfg_file "$project_root")"

    # Tier 1: project.cfg override.
    local raw="" source_file=""
    if [[ -n "$cfg_file" ]]; then
        raw="$(_pp_read_cfg_key "$cfg_file" overwatch block_triage "")"
    fi
    if [[ -n "$raw" ]]; then
        source_file="$cfg_file"
    else
        # Tier 2: kanban.cfg default. A read_ini failure (unreadable file)
        # prints nothing to stdout, so it folds into the same blank-raw
        # path as "absent" and takes the shipped default.
        raw="$(read_ini "$kanban_cfg" overwatch block_triage "")"
        source_file="$kanban_cfg"
    fi

    case "$raw" in
        ""|true)
            echo "true"
            return 0
            ;;
        false)
            echo "false"
            return 0
            ;;
        *)
            pgai_emit_error_contract \
                "block_triage.invalid_value" \
                "pp_block_triage: unrecognized value '${raw}' for project '${name}' in ${source_file} [overwatch] block_triage (accepted: true|false)." \
                "sed -i 's/^[[:space:]]*block_triage[[:space:]]*=.*/block_triage = true/' \"${source_file}\"" \
                "$name"
            # CENSUS-ANCHOR: block-triage-invalid-01
            return 1
            ;;
    esac
}

# ---------------------------------------------------------------------------
# pp_stale_lock_reap_after_wake_periods [name]
# Echoes the effective stale-lock reap threshold, in wake periods, for the
# named project: the per-project override from project.cfg [locks]
# stale_lock_reap_after_wake_periods when present, otherwise the
# kanban-wide default from $KANBAN_ROOT/kanban.cfg [locks]
# stale_lock_reap_after_wake_periods. Same project.cfg-overrides-kanban.cfg
# precedence as pp_max_rc_retries above.
#
# team/scripts/lib/lock_provenance.sh's pgai_lock_acquire multiplies this
# count by its own fixed wake-period length to get a wall-clock age beyond
# which a contended lock is reaped purely on age, independent of the
# recorded holder's pid liveness -- the third of that reaper's three
# independently-verifiable conditions (dead pid, no provenance, or age).
#
# Unlike pp_max_rc_retries, absence in both files is never an error: this
# knob carries a documented, safe shipped default (3 wake periods), so a
# project that never touches it still gets ordinary staleness protection
# rather than a fail-loud acquisition failure over a config lookup.
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
#
# Source (checked in this order):
#   1. project.cfg [locks] stale_lock_reap_after_wake_periods
#   2. kanban.cfg  [locks] stale_lock_reap_after_wake_periods (kanban root, shipped default 3)
#
# Validation: must be a positive integer in whichever source supplies it.
# An invalid (non-integer or zero) value is a fail-loud non-zero return
# naming the offending file and value -- never silently coerced or ignored,
# since a garbled threshold could otherwise silently disable the reaper (0)
# or make it absurdly slow, and neither failure mode should pass quietly.
#
# Example:
#   pp_stale_lock_reap_after_wake_periods "pgai-agent-kanban"  # -> 3 (shipped default)
#   pp_stale_lock_reap_after_wake_periods "patient-project"    # -> 10 (project.cfg override)
# ---------------------------------------------------------------------------
pp_stale_lock_reap_after_wake_periods() {
    local name
    name="$(pp_require_project_context "${1:-}")" || return 1

    local kroot
    kroot="$(_pp_kanban_root)" || return 1
    local kanban_cfg="${kroot}/kanban.cfg"

    local project_root cfg_file
    project_root="${kroot}/projects/${name}"
    cfg_file="$(_pp_project_cfg_file "$project_root")"

    # Tier 1: project.cfg override.
    local project_raw=""
    if [[ -n "$cfg_file" ]]; then
        project_raw="$(_pp_read_cfg_key "$cfg_file" locks stale_lock_reap_after_wake_periods "")"
    fi
    if [[ -n "$project_raw" ]]; then
        if ! [[ "$project_raw" =~ ^[0-9]+$ ]] || [[ "$project_raw" -eq 0 ]]; then
            echo "project_paths.sh: pp_stale_lock_reap_after_wake_periods: invalid value '${project_raw}' for project '${name}' in ${cfg_file} [locks] stale_lock_reap_after_wake_periods (expected a positive integer)" >&2
            return 1
        fi
        echo "$project_raw"
        return 0
    fi

    # Tier 2: kanban.cfg default.
    local kanban_raw
    kanban_raw="$(read_ini "$kanban_cfg" locks stale_lock_reap_after_wake_periods "")"
    if [[ -n "$kanban_raw" ]]; then
        if ! [[ "$kanban_raw" =~ ^[0-9]+$ ]] || [[ "$kanban_raw" -eq 0 ]]; then
            echo "project_paths.sh: pp_stale_lock_reap_after_wake_periods: invalid value '${kanban_raw}' in ${kanban_cfg} [locks] stale_lock_reap_after_wake_periods (expected a positive integer)" >&2
            return 1
        fi
        echo "$kanban_raw"
        return 0
    fi

    # Tier 3: shipped default -- absence in both files is the ordinary,
    # expected state for every project that never touches this key.
    echo 3
    return 0
}

# ---------------------------------------------------------------------------
# _pp_release_key_chain <cfg_file> <kanban_cfg> <key>
# Internal. Reads [release] <key> through the project.cfg-then-kanban.cfg
# chain every [release] resolver in this file uses: the project's own
# config (project.cfg, or a legacy PROJECT.cfg) first, then the kanban-wide
# kanban.cfg. A blank value counts as absent and falls through.
#
# Prints two lines on stdout: the first non-blank raw value, then the path
# of the file it came from. Both lines are empty when neither file carries a
# non-blank value. <cfg_file> may be empty (no per-project config file).
# ---------------------------------------------------------------------------
_pp_release_key_chain() {
    local cfg_file="$1"
    local kanban_cfg="$2"
    local key="$3"

    local raw=""
    if [[ -n "$cfg_file" ]]; then
        raw="$(_pp_read_cfg_key "$cfg_file" release "$key" "")"
        if [[ -n "$raw" ]]; then
            printf '%s\n%s\n' "$raw" "$cfg_file"
            return 0
        fi
    fi
    raw="$(read_ini "$kanban_cfg" release "$key" "")"
    if [[ -n "$raw" ]]; then
        printf '%s\n%s\n' "$raw" "$kanban_cfg"
        return 0
    fi
    printf '\n\n'
}

# ---------------------------------------------------------------------------
# pp_ship_posture [name]
# Echoes the named project's resolved ship posture, exactly "matrix" or
# "always": how CM answers a ship-policy trigger that would otherwise HALT.
# "matrix" applies the ship-policy matrix as written; "always" ships through
# the triggers the posture covers and records what they would have stopped
# on. Every caller that branches on the posture reads it through this
# function (or its Python counterpart, resolve_ship_posture() in
# team/pgai_agent_kanban/overwatch/resolution_matrix.py), never by reading
# the key itself.
#
# Source (first non-blank wins), the same chain as pp_max_rc_retries:
#   1. project.cfg [release] ship_posture  (or bare key in legacy PROJECT.cfg)
#   2. kanban.cfg  [release] ship_posture  (kanban root, shipped value matrix)
#   3. "matrix" when both are absent or blank
#
# Accepted values: matrix | always, matched exactly. Any other value
# resolves to "matrix" and prints ONE warning line on stderr naming the file
# and the raw value. This is a fallback, not a refusal: the return status is
# 0 and the caller carries on under "matrix".
#
# Remote-lane guard. On a lane whose project.cfg does not set
# push_to_remote = false (_pp_push_to_remote_configured reports "true" -- the
# configured intent, deliberately not the resolver's answer, so the guard
# fails safe and agrees with the Python resolver),
# "always" takes effect only when [release] ship_posture_confirm_remote is
# exactly "true", read through the same project.cfg-then-kanban.cfg chain.
# Anything short of that (absent, blank, "false", a typo) resolves to
# "matrix" and prints ONE warning line on stderr — the guard fails safe, so
# a lane that publishes to origin never drifts into "always" by accident.
# Like the unrecognized-value fallback, it is not a HALT and not a refusal:
# return status 0. On a push_to_remote = false lane the confirm key is not
# consulted.
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then
# error (the only non-zero return, alongside an unset KANBAN_ROOT).
#
# Example:
#   pp_ship_posture "pgai-agent-kanban"      # -> matrix (key unset anywhere)
#   pp_ship_posture "local-only-project"     # -> always (ship_posture = always,
#                                            #    push_to_remote = false)
#   pp_ship_posture "published-project"      # -> matrix + warning (always set,
#                                            #    push_to_remote = true, no confirm)
# ---------------------------------------------------------------------------
pp_ship_posture() {
    local name
    name="$(pp_require_project_context "${1:-}")" || return 1

    local kroot
    kroot="$(_pp_kanban_root)" || return 1
    local kanban_cfg="${kroot}/kanban.cfg"

    local project_root cfg_file
    project_root="${kroot}/projects/${name}"
    cfg_file="$(_pp_project_cfg_file "$project_root")"

    local -a chain
    local raw source_file
    mapfile -t chain < <(_pp_release_key_chain "$cfg_file" "$kanban_cfg" ship_posture)
    raw="${chain[0]:-}"
    source_file="${chain[1]:-}"

    case "$raw" in
        ""|matrix)
            echo "matrix"
            return 0
            ;;
        always)
            ;;
        *)
            echo "project_paths.sh: pp_ship_posture: WARNING: unrecognized value '${raw}' for project '${name}' in ${source_file} [release] ship_posture (accepted: matrix|always); resolving to 'matrix'." >&2
            echo "matrix"
            return 0
            ;;
    esac

    if [[ "$(_pp_push_to_remote_configured "$name")" != "true" ]]; then
        echo "always"
        return 0
    fi

    local confirm_raw confirm_source
    mapfile -t chain < <(_pp_release_key_chain "$cfg_file" "$kanban_cfg" ship_posture_confirm_remote)
    confirm_raw="${chain[0]:-}"
    confirm_source="${chain[1]:-}"
    if [[ "$confirm_raw" == "true" ]]; then
        echo "always"
        return 0
    fi

    # The remedy names the file whose confirm value wins the chain: the one
    # already carrying a non-true value, else the one that set "always".
    local confirm_desc confirm_target
    if [[ -n "$confirm_raw" ]]; then
        confirm_desc="'${confirm_raw}' in ${confirm_source}"
        confirm_target="$confirm_source"
    else
        confirm_desc="absent from ${cfg_file:-${project_root}/project.cfg} and ${kanban_cfg}"
        confirm_target="$source_file"
    fi
    echo "project_paths.sh: pp_ship_posture: WARNING: ship_posture 'always' for project '${name}' in ${source_file} is not confirmed for a push_to_remote = true lane ([release] ship_posture_confirm_remote is ${confirm_desc}); resolving to 'matrix'. Set ship_posture_confirm_remote = true under [release] in ${confirm_target} to confirm." >&2
    echo "matrix"
    return 0
}

# ---------------------------------------------------------------------------
# pp_workflow_type [name]
# Echoes the configured workflow type from project.cfg for the named project.
# Returns 'release' when the field is absent or empty (backward-compat default).
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
#
# Source in project.cfg: [project] workflow_type
#
# Example:
#   pp_workflow_type "pgai-agent-kanban"   # -> 'release'
#   pp_workflow_type "my-testing-project"  # -> 'testing-only'
# ---------------------------------------------------------------------------
pp_workflow_type() {
    local name
    name="$(pp_require_project_context "${1:-}")" || { echo "release"; return 0; }
    local project_root cfg_file
    project_root="$(pp_project_root "$name" 2>/dev/null)" || { echo "release"; return 0; }
    cfg_file="$(_pp_project_cfg_file "$project_root")"

    [[ -n "$cfg_file" ]] || { echo "release"; return 0; }

    # Source: project.cfg [project] workflow_type
    local raw
    raw="$(_pp_read_cfg_key "$cfg_file" project workflow_type "")"

    # Empty / absent — default to 'release' (backward-compat).
    if [[ -z "$raw" ]]; then
        echo "release"
        return 0
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
# Internal: _pp_load_push_decision
# Makes pp_push_decision (push_decision.sh, beside this file) available,
# sourcing it on first use. Returns 1 when that file is missing, which every
# caller treats as "do not push" -- the gate fails closed.
# ---------------------------------------------------------------------------
_pp_load_push_decision() {
    declare -F pp_push_decision >/dev/null 2>&1 && return 0
    [[ -f "${_PP_LIB_DIR}/push_decision.sh" ]] || return 1
    # shellcheck source=push_decision.sh
    source "${_PP_LIB_DIR}/push_decision.sh"
}

# ---------------------------------------------------------------------------
# pp_push_to_remote [name]
# Echoes 'true' when CM may push the named project's refs to origin, 'false'
# otherwise. Decided by the shared push-decision resolver (pp_push_decision,
# push_decision.sh), so the release path applies the same rule as every
# other push site: the project must be registered in projects.cfg, its own
# project.cfg (or legacy PROJECT.cfg) must exist, and that file must name a
# dev_tree_path. An inherited PGAI_DEV_TREE_PATH or REPO_ROOT never counts.
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then
# 'false' (nothing to resolve, so nothing to push).
#
# Answers:
#   - Resolved project, [project] push_to_remote absent, blank, 'true' or any
#     value other than the exact string 'false' -> 'true' (the default for a
#     real registered project is unchanged). One surrounding double quote is
#     stripped from each side before the comparison.
#   - Resolved project, push_to_remote = false -> 'false'.
#   - Unresolved project (unregistered, no project config, no dev_tree_path),
#     no project context, unset KANBAN_ROOT, or a resolver that cannot run
#     -> 'false'. Never 'true' by default.
#
# Quiet: prints only the answer. A site that must also log why an
# unresolved project is skipped calls pp_push_policy instead.
#
# Example:
#   pp_push_to_remote "pgai-agent-kanban"   # -> 'true' (registered, key unset)
#   pp_push_to_remote "local-only-project"  # -> 'false' (push_to_remote = false)
#   pp_push_to_remote "unregistered"        # -> 'false' (no [project:...] section)
# ---------------------------------------------------------------------------
pp_push_to_remote() {
    local name kroot
    name="$(pp_require_project_context "${1:-}" 2>/dev/null)" || { echo "false"; return 0; }
    kroot="$(_pp_kanban_root 2>/dev/null)" || { echo "false"; return 0; }
    _pp_load_push_decision || { echo "false"; return 0; }
    KANBAN_ROOT="$kroot" pp_push_decision "$name"
    if [[ "${PP_PUSH_DECISION:-}" == "push" ]]; then
        echo "true"
    else
        echo "false"
    fi
}

# ---------------------------------------------------------------------------
# pp_push_policy <name> <site-tag>
# The release-path push decision for one CM entry point (open-rc, release,
# finalize-release), taken once, before any git command. Asks the shared
# resolver (pp_push_decision) and sets, in the caller's scope:
#   PP_PUSH_DECISION    push | no-push | unresolved (see push_decision.sh)
#   PP_PUSH_TO_REMOTE   'true' only for push; 'false' for no-push and
#                       unresolved, so the site takes its local-only path
# For an unresolved project it also prints, on stdout, exactly one line:
#   <site-tag> skipped-unresolved-project: project '<name>': missing <piece>: <detail>; nothing pushed and no remote contacted
# -- the status token and 'missing <piece>' shape check-push-lag.sh and
# cm/push-watchdog.sh log. Push and no-push print nothing here; the site
# narrates its own push-policy line. Must be called directly, not in $(...),
# or the variables are lost. Always returns 0; fails closed (a missing
# resolver reads as unresolved with piece 'resolver').
#
# Example:
#   pp_push_policy "$PROJECT_NAME" "[cm-open-rc]"
#   _CM_PUSH_TO_REMOTE="$PP_PUSH_TO_REMOTE"
# ---------------------------------------------------------------------------
pp_push_policy() {
    local name="${1:-}" tag="${2:-}"
    PP_PUSH_TO_REMOTE="false"
    if _pp_load_push_decision; then
        pp_push_decision "$name"
    else
        PP_PUSH_DECISION="unresolved"
        PP_PUSH_STATUS_LINE="skipped-unresolved-project: project '${name}': missing resolver: ${_PP_LIB_DIR}/push_decision.sh does not exist; nothing pushed and no remote contacted"
    fi
    if [[ "$PP_PUSH_DECISION" == "push" ]]; then
        PP_PUSH_TO_REMOTE="true"
    elif [[ "$PP_PUSH_DECISION" != "no-push" ]]; then
        echo "${tag:+${tag} }${PP_PUSH_STATUS_LINE}"
    fi
    return 0
}

# ---------------------------------------------------------------------------
# Internal: _pp_push_to_remote_configured [name]
# The push intent a project's own config states, without the resolver's
# registration and dev-tree checks: 'false' only for the exact value 'false'
# in [project] push_to_remote (one surrounding double quote stripped from
# each side); 'true' for an absent key or config file. Used by
# pp_ship_posture's remote-lane guard, which must fail safe (treat anything
# short of an explicit 'false' as a publishing lane) and agrees with the
# Python resolver (resolution_matrix._project_pushes_to_remote). Never use
# it to decide whether to push -- that is pp_push_to_remote.
# ---------------------------------------------------------------------------
_pp_push_to_remote_configured() {
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
# PP_GIT_REMOTE_NAME_DEFAULT
# The remote pp_git_remote_name answers when [project] git_remote_name is
# absent or blank, or the project has no config file at all.
# ---------------------------------------------------------------------------
PP_GIT_REMOTE_NAME_DEFAULT="origin"

# ---------------------------------------------------------------------------
# validate_git_remote_name <value>
# Returns 0 when <value> is a remote name the release machinery may hand to
# git, 1 otherwise; prints nothing. The rule, shared with the Python twin
# (pgai_agent_kanban.lib.git_remote.is_valid_remote_name): letters, digits,
# '.', '_' and '-', starting with a letter or digit, with no '..' and no
# trailing '.' or '.lock'. Narrower than git's own rule on purpose: the
# value becomes a git push / fetch argument and the <remote> in
# refs/remotes/<remote>/..., so a leading '-', a '/', whitespace or a URL is
# refused rather than passed through.
#
# Examples that pass:  origin  upstream  my-fork  gh.mirror_2
# Examples that fail:  ""  -x  "up stream"  a/b  ..x  x.  x.lock
# ---------------------------------------------------------------------------
validate_git_remote_name() {
    local value="${1:-}"
    [[ "$value" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || return 1
    [[ "$value" != *..* && "$value" != *. && "$value" != *.lock ]]
}

# ---------------------------------------------------------------------------
# pp_git_remote_name [--quiet] [name]
# Echoes the git remote the named project's release machinery talks to:
# project.cfg [project] git_remote_name (or the bare key in a legacy
# PROJECT.cfg), one surrounding double quote stripped from each side. Every
# CM push, fetch, ls-remote and <remote>/<branch> ref, and the push-lag and
# released-version reads, name their remote through this function.
#
# Answers (exit 0, the name on stdout):
#   - absent or blank key, no config file, no project root, or no project
#     context -> PP_GIT_REMOTE_NAME_DEFAULT ("origin");
#   - a value validate_git_remote_name accepts -> that value.
#
# A configured value validate_git_remote_name rejects prints nothing on
# stdout and returns 1. Without --quiet it first refuses dual-channel on
# stderr through the shared emitter (error key git_remote_name.invalid,
# naming the key, the value and the file, and recorded in the project's
# refusal ledger); the Python twin raises the byte-identical block. With
# --quiet nothing is printed or recorded: a read-only caller (a dashboard,
# a released-version lookup) skips its remote tier and narrates its own
# line instead.
#
# CENSUS (docs/blocking-site-census.md): BLOCK-WORTHY. A value that cannot
# name a remote would hand git an option, a path or a URL where a release
# expects its remote -- a push to, a branch delete on, or a fetch from a
# destination nobody configured -- so it is refused, never guessed.
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then
# the default.
#
# Example:
#   pp_git_remote_name "pgai-agent-kanban"   # -> origin  (key absent or "origin")
#   pp_git_remote_name "fork-project"        # -> upstream (git_remote_name = upstream)
#   remote="$(pp_git_remote_name "$PROJECT_NAME")" || exit 1
# ---------------------------------------------------------------------------
pp_git_remote_name() {
    local quiet=false
    if [[ "${1:-}" == "--quiet" ]]; then
        quiet=true
        shift
    fi
    local name
    name="$(pp_require_project_context "${1:-}" 2>/dev/null)" || { echo "$PP_GIT_REMOTE_NAME_DEFAULT"; return 0; }
    local project_root cfg_file
    project_root="$(pp_project_root "$name" 2>/dev/null)" || { echo "$PP_GIT_REMOTE_NAME_DEFAULT"; return 0; }
    cfg_file="$(_pp_project_cfg_file "$project_root")"
    [[ -n "$cfg_file" ]] || { echo "$PP_GIT_REMOTE_NAME_DEFAULT"; return 0; }

    # Source: project.cfg [project] git_remote_name (or bare key in legacy PROJECT.cfg)
    local raw
    raw="$(_pp_read_cfg_key "$cfg_file" project git_remote_name "")"
    raw="${raw%\"}"
    raw="${raw#\"}"

    if [[ -z "${raw//[[:space:]]/}" ]]; then
        echo "$PP_GIT_REMOTE_NAME_DEFAULT"
        return 0
    fi
    if validate_git_remote_name "$raw"; then
        echo "$raw"
        return 0
    fi

    if [[ "$quiet" != true ]]; then
        local dev_tree listed=""
        dev_tree="$(_pp_read_cfg_key "$cfg_file" project dev_tree_path "")"
        [[ -n "$dev_tree" ]] && listed=" listed by 'git -C ${dev_tree} remote'"
        pgai_emit_error_contract \
            "git_remote_name.invalid" \
            "[project] git_remote_name is '${raw}' for project '${name}' in ${cfg_file}, which is not a valid git remote name (letters, digits, '.', '_' and '-', starting with a letter or digit; no '..', no trailing '.' or '.lock'); no git remote operation was run." \
            "Set git_remote_name in the [project] section of ${cfg_file} to a remote name${listed} (for example: git_remote_name = ${PP_GIT_REMOTE_NAME_DEFAULT}), or delete the line to use ${PP_GIT_REMOTE_NAME_DEFAULT}." \
            "$name"
    fi
    # CENSUS-ANCHOR: git-remote-name-invalid-02
    return 1
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
# Rules (ceilings as pp_max_major / pp_max_minor / pp_max_patch resolve them):
#   - version's major component > max_major → exceeds (1)
#   - version's minor component > max_minor → exceeds (1)
#   - version's patch component > max_patch → exceeds (1)
#   - An absent ceiling key (or no project.cfg) applies its documented
#     default (max_major 0, max_minor 13, max_patch 21); only an explicit
#     `none` means "no constraint" for that component.
#   - A malformed ceiling value → the accessor's one stderr line passes
#     through and this returns 1: the project is ceiling-blocked.
#   - Malformed version string → prints error to stderr and returns 1.
#
# NOTE: max_patch=0 is a real ceiling (only v X.Y.0 accepted).
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
#   if pp_project_halted "pgai-chomp-man"; then
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
        # pp_git_remote_name's answer: origin when absent or blank; empty when
        # the value is invalid (call pp_git_remote_name itself for the refusal).
        export PP_git_remote_name="$(pp_git_remote_name --quiet "$name")"
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
            if grep -qx "$var" <<< "$before_vars"; then
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

# ---------------------------------------------------------------------------
# pp_hook_required [name] <phase>
#
# Reads the cm_release_<phase>_hook_required key from project.cfg [hooks]
# and echoes 'true' or 'false'.
#
# Arguments:
#   [name]  — project name; resolved from PGAI_PROJECT_NAME when absent.
#   <phase> — one of: pre-squash | pre-tag | post-tag
#
# Returns 'false' (the safe default) when:
#   - project.cfg is not found
#   - the key is absent from [hooks]
#   - the value is unrecognized
#
# This function requires the project's config file to be present.  It does NOT
# load the config into the environment; it reads only the required flag.
#
# Example:
#   required="$(pp_hook_required my-project pre-squash)"
#   if [[ "$required" == "true" ]]; then ...
# ---------------------------------------------------------------------------
pp_hook_required() {
    local name phase

    if [[ $# -ge 2 ]]; then
        name="$(pp_require_project_context "${1:-}")" || return 1
        phase="${2:-}"
    else
        name="$(pp_require_project_context "")" || return 1
        phase="${1:-}"
    fi

    if [[ -z "$phase" ]]; then
        echo "project_paths.sh: pp_hook_required: phase argument is required (pre-squash | pre-tag | post-tag)" >&2
        return 1
    fi

    # Map phase to the config key name.
    local required_key
    case "$phase" in
        pre-squash)  required_key="cm_release_pre_squash_hook_required" ;;
        pre-tag)     required_key="cm_release_pre_tag_hook_required"    ;;
        post-tag)    required_key="cm_release_post_tag_hook_required"   ;;
        *)
            echo "project_paths.sh: pp_hook_required: unknown phase '${phase}'; expected pre-squash | pre-tag | post-tag" >&2
            return 1
            ;;
    esac

    local project_root cfg_file
    project_root="$(pp_project_root "$name")" || { echo "false"; return 0; }
    cfg_file="$(_pp_project_cfg_file "$project_root")"

    if [[ -z "$cfg_file" || ! -f "$cfg_file" ]]; then
        echo "false"
        return 0
    fi

    local raw_val
    raw_val="$(_pp_read_cfg_key "$cfg_file" "hooks" "$required_key" "false")"
    # Normalize to lowercase; default to 'false' when absent or unrecognized.
    raw_val="${raw_val,,}"
    if [[ "$raw_val" == "true" ]]; then
        echo "true"
    else
        echo "false"
    fi
    return 0
}

# ---------------------------------------------------------------------------
# pp_repo_lock_path [name]
# Echoes the absolute path to the per-project repo flock file:
#   $KANBAN_ROOT/locks/repo-wake-<project-repo-id>.lock
#
# <project-repo-id> is derived from the project name by replacing '/' with
# '_' and stripping every character that is not alphanumeric, '_', or '-'.
# This is the SAME sanitization the wake scripts (wake/claude.sh,
# wake/codex.sh, wake/harness.sh) apply when they acquire the per-project
# repo lock before processing a project's task queue.
#
# This is the single source of truth for the per-project repo lock path.
# Every caller that needs to know "which lock file does the wake hold for
# project X" — wake dispatch itself, or an independent observer like
# OVERWATCH that must not act while a wake is mid-flight — MUST resolve the
# path through this function. Do not re-derive the sanitization or the
# "repo-wake-<id>.lock" naming convention elsewhere; a second independent
# derivation is how the lock path a wake holds and the lock path an
# observer inspects can silently diverge.
#
# Arguments:
#   [name]  — project name (optional when PGAI_PROJECT_NAME is set)
#
# Example:
#   pp_repo_lock_path "pgai-agent-kanban"
#   # -> /path/to/kanban/locks/repo-wake-pgai-agent-kanban.lock
# ---------------------------------------------------------------------------
pp_repo_lock_path() {
    local name
    name="$(pp_require_project_context "${1:-}")" || return 1
    local root
    root="$(_pp_kanban_root)" || return 1

    local repo_id
    repo_id="$(printf '%s' "$name" | tr '/' '_' | tr -cd '[:alnum:]_-')"

    echo "${root}/locks/repo-wake-${repo_id}.lock"
}

# ---------------------------------------------------------------------------
# Internal: _pp_find_key_section <cfg_file> <key> <expected_section>
# Scans an INI file for the first section OTHER than <expected_section>
# that carries <key> (comments and blank lines ignored, same section-
# assignment rule as read_ini: a key belongs to the nearest header above
# it). Used only to build the misplaced-key warning below — it never
# changes which section a key resolves from; read_ini's own [release]-only
# read stays authoritative for the value itself. A recognised deep-verify
# key sitting outside [release] must not resolve silently — this helper
# is what lets the callers below say where it actually landed.
#
# Echoes the section name if the key is found elsewhere in the file, or
# nothing if it is absent everywhere. Never fails loud — an unreadable or
# missing file just yields no match, since the caller only uses this to
# decide whether to print an advisory warning before falling back to the
# documented default.
# ---------------------------------------------------------------------------
_pp_find_key_section() {
    local file="$1"
    local key="$2"
    local expected_section="$3"

    [[ -r "$file" ]] || return 0

    awk \
        -v target_key="$key" \
        -v expected_section="$expected_section" \
        '
        /^\[/ {
            secname = $0
            sub(/^\[[ \t]*/, "", secname)
            sub(/[ \t]*\].*$/, "", secname)
            cur_section = secname
            next
        }
        /^[ \t]*$/ { next }
        /^[ \t]*[#;]/ { next }
        {
            eq_pos = index($0, "=")
            if (eq_pos == 0) { next }
            raw_key = substr($0, 1, eq_pos - 1)
            gsub(/^[ \t]+|[ \t]+$/, "", raw_key)
            if (raw_key == target_key && cur_section != expected_section && cur_section != "") {
                print cur_section
                exit 0
            }
        }
        ' "$file"
}

# ---------------------------------------------------------------------------
# _pp_warn_release_key_misplaced <caller> <file> <key> <scope> <next_tier>
# Internal. Prints ONE advisory stderr line when <key> is absent from
# [release] in <file> but present under some other section, where the
# [release]-scoped reader never looks. <scope> is the phrase naming whose
# file it is ("for project 'x'" or empty for kanban.cfg); <next_tier> names
# what applies instead. Prints nothing when the key is absent everywhere
# or the file is unreadable. Always returns 0.
# ---------------------------------------------------------------------------
_pp_warn_release_key_misplaced() {
    local caller="$1"
    local file="$2"
    local key="$3"
    local scope="$4"
    local next_tier="$5"

    [[ -n "$file" ]] || return 0
    local found_section
    found_section="$(_pp_find_key_section "$file" "$key" release)"
    if [[ -n "$found_section" ]]; then
        echo "project_paths.sh: ${caller}: '${key}' found in [${found_section}], not [release],${scope:+ ${scope}} in ${file} — only [release] ${key} is honored, so it is ignored there; ${next_tier} applies. Move the key under [release] to activate it." >&2
    fi
    return 0
}

# ---------------------------------------------------------------------------
# pp_deep_verify_mode [name]
# Echoes the effective DEEP-VERIFY scheduled-dispatch mode for the named
# project, "weekly" or "off".
#
# Source (first non-blank wins), the same chain as pp_ship_posture:
#   1. project.cfg [release] deep_verify  (or bare key in legacy PROJECT.cfg)
#   2. kanban.cfg  [release] deep_verify  (the kanban-wide value; setting
#      "off" here turns scheduled DEEP-VERIFY off for every project that
#      does not set its own)
#   3. "weekly" when both are absent or blank
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
#
# Default when absent or blank everywhere: "weekly" — deliberately opt-OUT
# rather than opt-in. A lane that only runs when someone remembers to run
# it is a lane that stops running; an absent key silently meaning "off"
# would recreate exactly that failure mode for every project that never
# touches this key, including this framework's own self-build.
# Projects that ship no images, or that want to skip the cost, opt out
# explicitly with deep_verify = off.
#
# Misplaced key: when a tier's file carries deep_verify under some section
# other than [release] (and not under [release]), ONE advisory stderr line
# names the section and the file, and resolution continues with the next
# tier. Exit status stays 0.
#
# Validation: the accepted non-blank values are "weekly", "off", and the
# legacy boolean aliases "true" (-> "weekly") and "false" (-> "off") --
# the same four spellings team/scripts/lib/config_loader.sh accepts for
# this key in kanban.cfg, and the set every shipped project.cfg/kanban.cfg
# example advertises. An alias is recognized, not guessed: stdout on
# success carries exactly one of the two mode words ("weekly" or "off"),
# never the alias spelling echoed back. Any other value in the tier that
# supplies it (a typo, a stray value from another vocabulary) is a
# fail-loud, non-zero return with a stderr diagnostic naming that file and
# the key, and NOTHING on stdout — never a guessed mode, and never a fall
# through to the next tier. This is what lets a caller distinguish
# "resolved to a mode" (stdout has exactly one of the two words) from
# "refused" (non-zero exit, nothing usable on stdout) without parsing
# prose. team/scripts/deep-verify.sh's --scheduled path is the caller that
# turns a non-zero return here into the scheduled path's own dual-channel
# refusal (CENSUS-ANCHOR deep-verify-02) — this function does not emit the
# dual-channel contract itself, matching every other pp_* reader in this
# file (the emitter lives at the site of refusal, not in the shared
# reader).
#
# Python counterpart: _read_deep_verify_mode_raw() and
# get_deep_verify_status() in team/pgai_agent_kanban/ops/read.py read the
# same chain through resolution_matrix._release_key_chain().
#
# Example:
#   pp_deep_verify_mode "pgai-agent-kanban"    # -> "weekly" (unset in both files)
#   pp_deep_verify_mode "quiet-project"        # -> "off" (project.cfg opt-out,
#                                              #    or kanban.cfg deep_verify = off)
#   pp_deep_verify_mode "legacy-project"       # -> "weekly" (deep_verify=true alias)
# ---------------------------------------------------------------------------
pp_deep_verify_mode() {
    local name
    name="$(pp_require_project_context "${1:-}")" || return 1

    local kroot
    kroot="$(_pp_kanban_root)" || return 1
    local kanban_cfg="${kroot}/kanban.cfg"

    local project_root cfg_file
    project_root="$(pp_project_root "$name")" || return 1
    cfg_file="$(_pp_project_cfg_file "$project_root")"

    local -a chain
    local raw source_file
    mapfile -t chain < <(_pp_release_key_chain "$cfg_file" "$kanban_cfg" deep_verify)
    raw="${chain[0]:-}"
    source_file="${chain[1]:-}"

    # A tier the chain passed over may still carry the key under the wrong
    # section; say so rather than let the operator's intent vanish silently.
    if [[ -n "$cfg_file" && "$source_file" != "$cfg_file" ]]; then
        _pp_warn_release_key_misplaced pp_deep_verify_mode "$cfg_file" deep_verify \
            "for project '${name}'" "${kanban_cfg} [release] deep_verify, else 'weekly',"
    fi
    if [[ -z "$raw" ]]; then
        _pp_warn_release_key_misplaced pp_deep_verify_mode "$kanban_cfg" deep_verify \
            "" "the default 'weekly'"
        echo "weekly"
        return 0
    fi

    # weekly/true and off/false are recognized aliases of the same two
    # modes, not four independent outcomes: stdout always carries the
    # canonical word ("weekly" or "off"), never the alias spelling that
    # was actually in the config file, so every caller downstream (the
    # off-mode literal comparison in deep-verify.sh included) keeps
    # comparing against exactly two strings.
    case "$raw" in
        weekly|true)
            echo "weekly"
            return 0
            ;;
        off|false)
            echo "off"
            return 0
            ;;
        *)
            echo "project_paths.sh: pp_deep_verify_mode: unrecognized value '${raw}' for project '${name}' in ${source_file} [release] deep_verify (expected 'weekly' or 'off', aliases 'true'/'false' also accepted; blank/absent in project.cfg and kanban.cfg defaults to 'weekly')" >&2
            return 1
            ;;
    esac
}

# ---------------------------------------------------------------------------
# pp_deep_verify_offhours_window [name]
# Echoes the DEEP-VERIFY off-hours window for the named project — "the
# schedule knob" — as a literal "HH:MM-HH:MM" string (UTC).
#
# Source (first well-formed value wins):
#   1. project.cfg [release] deep_verify_offhours_window  (or bare key in
#      legacy PROJECT.cfg)
#   2. kanban.cfg  [release] deep_verify_offhours_window  (the kanban-wide
#      value)
#   3. "03:00-03:59" when neither tier supplies a well-formed value
#
# Project name resolution: explicit argument, then PGAI_PROJECT_NAME, then error.
#
# Unlike pp_deep_verify_mode, this knob never fails loud: a scheduling
# nicety that lets an operator stagger multiple projects' weekly runs
# across different hours is not an invariant worth halting a cron job
# over. A malformed value at a tier (not HH:MM-HH:MM, hours 0-23, minutes
# 00-59) counts as absent at that tier: ONE warning line on stderr names
# the file and the value, and resolution continues with the next tier. A
# key under the wrong section gets the same one-line advisory as
# pp_deep_verify_mode's. Exit status is 0 on every one of those paths.
# The accepted shape matches _WINDOW_RE in
# team/pgai_agent_kanban/deep_verify/schedule.py, whose
# resolve_offhours_window() still applies its own default fallback to
# whatever string reaches it.
#
# Example:
#   pp_deep_verify_offhours_window "pgai-agent-kanban"  # -> "03:00-03:59" (default)
#   pp_deep_verify_offhours_window "night-owl-project"  # -> "01:00-01:59" (project.cfg
#                                                       #    or kanban.cfg value)
# ---------------------------------------------------------------------------
pp_deep_verify_offhours_window() {
    local name
    name="$(pp_require_project_context "${1:-}")" || return 1

    local kroot
    kroot="$(_pp_kanban_root)" || return 1
    local kanban_cfg="${kroot}/kanban.cfg"

    local project_root cfg_file
    project_root="$(pp_project_root "$name")" || return 1
    cfg_file="$(_pp_project_cfg_file "$project_root")"

    local default_window="03:00-03:59"
    local window_re='^([01]?[0-9]|2[0-3]):[0-5][0-9]-([01]?[0-9]|2[0-3]):[0-5][0-9]$'

    local tier_file raw scope next_tier
    for tier_file in "$cfg_file" "$kanban_cfg"; do
        [[ -n "$tier_file" ]] || continue
        if [[ "$tier_file" == "$kanban_cfg" ]]; then
            raw="$(read_ini "$tier_file" release deep_verify_offhours_window "")"
            scope=""
            next_tier="the default '${default_window}'"
        else
            raw="$(_pp_read_cfg_key "$tier_file" release deep_verify_offhours_window "")"
            scope="for project '${name}'"
            next_tier="${kanban_cfg} [release] deep_verify_offhours_window, else '${default_window}',"
        fi

        if [[ -z "$raw" ]]; then
            _pp_warn_release_key_misplaced pp_deep_verify_offhours_window "$tier_file" \
                deep_verify_offhours_window "$scope" "$next_tier"
            continue
        fi
        if [[ "$raw" =~ $window_re ]]; then
            echo "$raw"
            return 0
        fi
        echo "project_paths.sh: pp_deep_verify_offhours_window: WARNING: malformed value '${raw}'${scope:+ ${scope}} in ${tier_file} [release] deep_verify_offhours_window (expected HH:MM-HH:MM, UTC); ignoring it — ${next_tier} applies." >&2
    done

    echo "$default_window"
    return 0
}
