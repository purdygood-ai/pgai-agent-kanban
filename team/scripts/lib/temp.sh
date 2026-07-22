#!/usr/bin/env bash
# team/scripts/lib/temp.sh
# Centralized temp-file management for the pgai-agent-kanban framework.
#
# Source this file to get the pgai_temp_* functions in your shell:
#   source "$(dirname "${BASH_SOURCE[0]}")/temp.sh"
#
# All framework subsystems should use these helpers rather than calling
# mktemp directly or hardcoding /tmp paths.  Centralising through one
# variable (PGAI_AGENT_KANBAN_TEMP_DIR) makes temp space
# discoverable, configurable, and safely cleanable without risk of
# touching unrelated /tmp content.
#
# Temp root resolution (three-tier + fallback, highest precedence first):
#   1. PGAI_AGENT_KANBAN_TEMP_DIR env var — if already set, used as-is.
#   2a. ${tmp_root}/${tmp_subdir} from kanban.cfg [paths] via PGAI_AGENT_KANBAN_ROOT_PATH.
#   2b. Self-locate: derive install root from _PGAI_TEMP_SH_DIR (two dirs up) and
#       try that kanban.cfg when PGAI_AGENT_KANBAN_ROOT_PATH is unset/empty.
#   3. Last-resort fallback /tmp/pgai_kanban_tmp — when config is unreadable.
# The resolver NEVER returns empty or '/'.
#
# Per-project temp directories
# ----------------------------
# When pp_load_config has been called for a project, PP_TEMP_DIR holds the
# absolute per-project temp path (<root>/<temp_dir>).  Use the project-aware
# helpers to place files there:
#
#   pgai_project_temp_dir [project_name]
#                      — echo the per-project temp dir and create it if needed.
#                        When called with no argument (or with an empty argument),
#                        falls back to PP_TEMP_DIR (exported by pp_load_config)
#                        and then to the install-wide root.  When called with a
#                        project name, computes <root>/tmp.<name>.
#   pgai_mktemp_p [prefix] [project_name]
#                      — mktemp a file inside the per-project temp dir.
#   pgai_mktemp_d_p [prefix] [project_name]
#                      — mktemp -d a directory inside the per-project temp dir.
#
# Install-wide (no project context) functions
# -------------------------------------------
#   pgai_temp_dir              — echo the resolved root dir, creating it if needed
#   pgai_temp_subdir <name>    — echo/create a named subdir under the root
#   pgai_mktemp [prefix]       — mktemp a file inside the root
#   pgai_mktemp_d [prefix]     — mktemp -d a directory inside the root
#   pgai_temp_cleanup <path>        — remove one specific path (only if under the root)
#   pgai_temp_cleanup_all           — remove everything under the root (never /tmp itself)
#   pgai_temp_cleanup_tests         — remove ONLY ${root}/tests (scoped cleanup)
#
# Safety invariants:
#   - No top-level side effects when sourced — only function definitions
#     (except _PGAI_TEMP_SH_DIR captured once at source time for ini_parser).
#   - All functions are idempotent (mkdir -p; no errors on second call).
#   - pgai_temp_cleanup refuses paths that are not under the configured root.
#   - pgai_temp_cleanup_all only removes content INSIDE the root; it never
#     deletes the root directory itself and never touches arbitrary /tmp content.

# Include guard: safe to source multiple times (only loads once).
[[ -n "${_PGAI_TEMP_SH_LOADED:-}" ]] && return 0
_PGAI_TEMP_SH_LOADED=1

# Capture the directory of this file at source time so pgai_temp_dir() can
# locate ini_parser.sh without relying on BASH_SOURCE inside a function call.
_PGAI_TEMP_SH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# pgai_temp_dir
# Echo the resolved framework temp root directory and ensure it exists.
# Returns the path (always ends without a trailing slash).
#
# Resolution order:
#   1. PGAI_AGENT_KANBAN_TEMP_DIR — env-var override.
#   2a. ${tmp_root}/${tmp_subdir} from kanban.cfg via PGAI_AGENT_KANBAN_ROOT_PATH.
#   2b. Self-locate: install root derived from _PGAI_TEMP_SH_DIR/../..
#   3. Hard fallback /tmp/pgai_kanban_tmp (config unreadable / absent).
# Never returns empty or '/'.
# ---------------------------------------------------------------------------
pgai_temp_dir() {
    local dir

    # Tier 1: env-var override (set by wake_common.sh or the operator).
    if [[ -n "${PGAI_AGENT_KANBAN_TEMP_DIR:-}" ]]; then
        dir="${PGAI_AGENT_KANBAN_TEMP_DIR}"
    else
        # Tier 2: read tmp_root and tmp_subdir from kanban.cfg [paths].
        #
        # PRINCIPLE: every kanban entry point must establish its own environment
        # by sourcing shell-env before calling pgai_temp_dir (wake scripts do this;
        # create.sh mirrors that).  This self-locate sub-tier is the defensive
        # backstop for any caller whose PGAI_AGENT_KANBAN_ROOT_PATH is unset.
        #
        # Sub-tier 2a: locate kanban.cfg via PGAI_AGENT_KANBAN_ROOT_PATH (the live
        # install root) when available.
        # Sub-tier 2b (self-locate): when PGAI_AGENT_KANBAN_ROOT_PATH is unset/empty,
        # derive the install root from _PGAI_TEMP_SH_DIR (captured at source time as
        # <install>/team/scripts/lib).  The install root is two directories up.  Try
        # that path's kanban.cfg before falling to Tier 3.  This protects callers such
        # as dashboard pane scripts that may not inherit the full framework env.
        # Must NOT use BASH_SOURCE inside the function — _PGAI_TEMP_SH_DIR is the
        # correct reference (captured at source time with BASH_SOURCE, safe there).
        local _cfg_dir="${PGAI_AGENT_KANBAN_ROOT_PATH:-}"
        local _tmp_root="" _tmp_subdir=""

        # Sub-tier 2a: env-var points to a readable kanban.cfg.
        if [[ -n "$_cfg_dir" && -r "${_cfg_dir}/kanban.cfg" ]]; then
            # Source ini_parser.sh only if read_ini is not already available.
            if ! command -v read_ini >/dev/null 2>&1; then
                # shellcheck source=ini_parser.sh
                source "${_PGAI_TEMP_SH_DIR}/ini_parser.sh"
            fi
            _tmp_root="$(read_ini "${_cfg_dir}/kanban.cfg" paths tmp_root "")"
            _tmp_subdir="$(read_ini "${_cfg_dir}/kanban.cfg" paths tmp_subdir "")"
        fi

        # Sub-tier 2b: self-locate fallback when env var was unset/empty or its
        # kanban.cfg was unreadable (i.e. _tmp_root still empty).
        if [[ -z "$_tmp_root" ]]; then
            local _self_root
            # _PGAI_TEMP_SH_DIR is <install>/team/scripts/lib; install root is two up.
            _self_root="$(cd "${_PGAI_TEMP_SH_DIR}/../.." && pwd)"
            if [[ -r "${_self_root}/kanban.cfg" ]]; then
                if ! command -v read_ini >/dev/null 2>&1; then
                    # shellcheck source=ini_parser.sh
                    source "${_PGAI_TEMP_SH_DIR}/ini_parser.sh"
                fi
                _tmp_root="$(read_ini "${_self_root}/kanban.cfg" paths tmp_root "")"
                _tmp_subdir="$(read_ini "${_self_root}/kanban.cfg" paths tmp_subdir "")"
            fi
        fi

        if [[ -n "$_tmp_root" && -n "$_tmp_subdir" ]]; then
            dir="${_tmp_root}/${_tmp_subdir}"
        else
            # Tier 3: last-resort fallback — config absent or unreadable.
            # anti-pattern-allowlist: 2 (justification: this IS the resolver's documented fallback; the literal is intentional and must remain here as the hard-coded safety net)
            dir="/tmp/pgai_kanban_tmp"
        fi
    fi

    # Safety invariant: resolver must never return empty or root '/'.
    if [[ -z "$dir" || "$dir" == "/" ]]; then
        # anti-pattern-allowlist: 2 (justification: safety invariant fallback in the resolver itself; intentional hard-coded sentinel, not a caller site)
        dir="/tmp/pgai_kanban_tmp"
    fi

    mkdir -p "$dir"
    echo "$dir"
}

# ---------------------------------------------------------------------------
# pgai_temp_subdir <name>
# Create and echo a named subdirectory under the framework temp root.
# Idempotent: safe to call multiple times.
# Usage: local subdir; subdir="$(pgai_temp_subdir tests)"
# ---------------------------------------------------------------------------
pgai_temp_subdir() {
    local name="$1"
    if [[ -z "$name" ]]; then
        echo "temp.sh: pgai_temp_subdir requires a name argument" >&2
        return 1
    fi
    local root
    root="$(pgai_temp_dir)"
    local subdir="${root}/${name}"
    mkdir -p "$subdir"
    echo "$subdir"
}

# ---------------------------------------------------------------------------
# pgai_mktemp [prefix]
# Create a uniquely named temp FILE inside the framework temp root.
# The optional prefix is prepended to the generated name; defaults to "pgai_tmp".
# Usage: local f; f="$(pgai_mktemp my_script)"
# ---------------------------------------------------------------------------
pgai_mktemp() {
    local prefix="${1:-pgai_tmp}"
    local root
    root="$(pgai_temp_dir)"
    mktemp "${root}/${prefix}.XXXXXX"
}

# ---------------------------------------------------------------------------
# pgai_mktemp_d [prefix]
# Create a uniquely named temp DIRECTORY inside the framework temp root.
# The optional prefix is prepended to the generated name; defaults to "pgai_tmp".
# Usage: local d; d="$(pgai_mktemp_d scratch)"
# ---------------------------------------------------------------------------
pgai_mktemp_d() {
    local prefix="${1:-pgai_tmp}"
    local root
    root="$(pgai_temp_dir)"
    mktemp -d "${root}/${prefix}.XXXXXX"
}

# ---------------------------------------------------------------------------
# pgai_temp_cleanup <path>
# Remove a specific file or directory, but ONLY if it lives under the
# configured temp root.  Refuses (with a stderr message and non-zero exit)
# to remove paths outside the root — this guards against accidents when
# callers pass a wrong variable.
# Usage: pgai_temp_cleanup "$my_temp_file"
# ---------------------------------------------------------------------------
pgai_temp_cleanup() {
    local target="$1"
    if [[ -z "$target" ]]; then
        echo "temp.sh: pgai_temp_cleanup requires a path argument" >&2
        return 1
    fi

    local root
    root="$(pgai_temp_dir)"

    # Resolve both paths to their canonical form so symlinks and relative
    # components don't allow escaping the root.  Use readlink -m (no
    # requirement that the path exists) so we can check paths to files
    # that have already been partially cleaned up.
    local real_root real_target
    real_root="$(readlink -m "$root")"
    real_target="$(readlink -m "$target")"

    # Require that real_target starts with real_root/ (note the trailing
    # slash guard prevents /tmp/pgai_kanban_tmp_extra from matching).
    if [[ "$real_target" != "${real_root}/"* && "$real_target" != "$real_root" ]]; then
        echo "temp.sh: pgai_temp_cleanup: refusing to remove '${target}' — path is not under the configured temp root '${real_root}'" >&2
        return 1
    fi

    # Safety: never let callers delete the root itself via this function.
    if [[ "$real_target" == "$real_root" ]]; then
        echo "temp.sh: pgai_temp_cleanup: refusing to remove the temp root itself; use pgai_temp_cleanup_all to clear its contents" >&2
        return 1
    fi

    rm -rf "$target"
}

# ---------------------------------------------------------------------------
# pgai_temp_cleanup_tests
# Remove ONLY the ${PGAI_AGENT_KANBAN_TEMP_DIR}/tests subtree.
# Scoped replacement for pgai_temp_cleanup_all in test-runner post-suite
# cleanup. Using the scoped helper prevents the runner from accidentally
# wiping live agent session files that reside elsewhere in the temp root
# (e.g. claude-<session>/ directories).
#
# Refuses (non-zero exit, no deletion) when:
#   - The resolved temp root is empty, '/', or '/tmp' bare.
#   - The resolved tests path equals the temp root itself.
#
# The tests/ directory itself is removed entirely (rm -rf), mirroring the
# behaviour of pgai_temp_cleanup on a subdirectory.
#
# Usage: pgai_temp_cleanup_tests
# ---------------------------------------------------------------------------
pgai_temp_cleanup_tests() {
    local root
    root="$(pgai_temp_dir)"

    # Belt-and-suspenders: refuse to operate on dangerous/bare paths.
    # provenance-allowlist: remediation-pending — cited ID belongs in commit history; remove when rewriting comment
    # Mirrors the guards in pgai_temp_cleanup_all (CODER-20260608-012).
    if [[ -z "$root" ]]; then
        echo "temp.sh: pgai_temp_cleanup_tests: refusing to clean empty path — pgai_temp_dir returned empty string" >&2
        return 1
    fi
    if [[ "$root" == "/" ]]; then
        echo "temp.sh: pgai_temp_cleanup_tests: refusing to clean filesystem root '/' — set PGAI_AGENT_KANBAN_TEMP_DIR to a subdirectory" >&2
        return 1
    fi
    if [[ "$root" == "/tmp" ]]; then
        echo "temp.sh: pgai_temp_cleanup_tests: refusing to clean /tmp directly — set PGAI_AGENT_KANBAN_TEMP_DIR to a subdirectory" >&2
        return 1
    fi

    local tests_path="${root}/tests"

    # Guard: resolved tests path must not equal the root itself.
    if [[ "$tests_path" == "$root" ]]; then
        echo "temp.sh: pgai_temp_cleanup_tests: refusing to remove the temp root itself — tests path resolved to root" >&2
        return 1
    fi

    # Remove the tests/ subtree only if it exists.  No error when absent.
    if [[ -e "$tests_path" ]]; then
        rm -rf "$tests_path"
    fi
}

# ---------------------------------------------------------------------------
# _pgai_temp_is_provider_session_dir <basename>
# Return 0 (true) if <basename> matches a known provider session directory
# pattern that must NEVER be deleted by kanban cleanup routines.
#
# Provider session directories are written by AI provider CLIs (e.g. the
# claude CLI) into PGAI_AGENT_KANBAN_TEMP_DIR via the provider TMPDIR bridge.
# They are foreign to the kanban framework and must be treated as read-only
# transients owned by the provider process, not by the kanban.
#
# Pattern rules (glob syntax):
#   claude-*    — Claude CLI session dirs (e.g. claude-1000, claude-XXXX)
#   codex-*     — OpenAI Codex session dirs
#   gemini-*    — Google Gemini session dirs
#
# This list is intentionally conservative: prefer false-positive (too many
# excluded) over false-negative (a provider dir gets deleted).
# ---------------------------------------------------------------------------
_pgai_temp_is_provider_session_dir() {
    local name="$1"
    case "$name" in
        claude-*|codex-*|gemini-*)
            return 0  # provider session dir — do not delete
            ;;
    esac
    return 1
}

# ---------------------------------------------------------------------------
# pgai_temp_cleanup_all
# Remove all contents INSIDE the framework temp root (subdirs and files).
# The root directory itself is preserved.
# Never removes arbitrary /tmp content — only operates within the configured root.
# Refuses to act (with a stderr message and non-zero exit) when the resolved
# root is empty, '/', or '/tmp' bare.
#
# Provider session directories (claude-*, codex-*, gemini-*) are EXCLUDED
# from deletion. These are foreign to the kanban framework and are owned by
# the AI provider CLI process. Skipping them prevents the provider TMPDIR
# bridge from inadvertently wiping live session state while an agent is running.
#
# Usage: pgai_temp_cleanup_all
# ---------------------------------------------------------------------------
pgai_temp_cleanup_all() {
    local root
    root="$(pgai_temp_dir)"

    # Belt-and-suspenders: refuse to operate on dangerous/bare paths.
    # provenance-allowlist: remediation-pending — cited ID belongs in commit history; remove when rewriting comment
    # Guards added in CODER-20260608-012 to satisfy AC: "refuse to act if
    # computed root is empty, '/', or '/tmp' bare."
    if [[ -z "$root" ]]; then
        echo "temp.sh: pgai_temp_cleanup_all: refusing to clean empty path — pgai_temp_dir returned empty string" >&2
        return 1
    fi
    if [[ "$root" == "/" ]]; then
        echo "temp.sh: pgai_temp_cleanup_all: refusing to clean filesystem root '/' — set PGAI_AGENT_KANBAN_TEMP_DIR to a subdirectory" >&2
        return 1
    fi
    if [[ "$root" == "/tmp" ]]; then
        echo "temp.sh: pgai_temp_cleanup_all: refusing to clean /tmp directly — set PGAI_AGENT_KANBAN_TEMP_DIR to a subdirectory" >&2
        return 1
    fi

    # Remove contents but not the root directory itself.
    # The glob expansion is done by bash; if the dir is empty the loop body
    # never runs (no error from rm on a non-existent target).
    local item _basename
    for item in "${root}"/*; do
        # Guard against the empty-glob case: "${root}/*" expands to the
        # literal string when the directory is empty.
        [[ -e "$item" ]] || continue

        # provenance-allowlist: remediation-pending — cited ID belongs in commit history; remove when rewriting comment
        # Provider session dir fence (CODER-20260613-016): skip claude-*,
        # codex-*, gemini-* dirs — they are owned by the provider CLI process,
        # not by the kanban.  Deleting them while an agent runs destroys live
        # session state.
        _basename="$(basename "$item")"
        if _pgai_temp_is_provider_session_dir "$_basename"; then
            echo "temp.sh: pgai_temp_cleanup_all: skipping provider session dir: $item" >&2
            continue
        fi

        rm -rf "$item"
    done
    unset _basename
}

# ---------------------------------------------------------------------------
# pgai_project_temp_dir [project_name]
# Echo the per-project temp directory and ensure it exists.
#
# Resolution order:
#   1. If project_name is provided: <framework_root>/projects/<project_name>
#   2. If PP_TEMP_DIR is set (exported by pp_load_config): use PP_TEMP_DIR
#   3. Fallback: the install-wide framework root (pgai_temp_dir)
#
# The per-project layout is:
#   $(pgai_temp_dir)/projects/<project_name>/
# Sub-paths under that root carry project-scoped temp (worktrees, doc, etc.).
# Shared non-project scratch (tests/, dashboard/) stays at the framework root.
#
# The directory is created with mkdir -p before being echoed.
# Idempotent: safe to call multiple times with the same arguments.
#
# Usage:
#   # With explicit project name:
#   local ptd; ptd="$(pgai_project_temp_dir my-project)"
#   # After pp_load_config has been called (PP_TEMP_DIR is set):
#   local ptd; ptd="$(pgai_project_temp_dir)"
# ---------------------------------------------------------------------------
pgai_project_temp_dir() {
    local project_name="${1:-}"
    local dir

    if [[ -n "$project_name" ]]; then
        # Explicit project name: resolve as <framework_root>/projects/<project_name>
        # Each project owns its own namespace so cleanup of one project cannot
        # reach another.
        local root
        root="$(pgai_temp_dir)"
        dir="${root}/projects/${project_name}"
    elif [[ -n "${PP_TEMP_DIR:-}" ]]; then
        # pp_load_config has been called; use the pre-resolved absolute path.
        dir="${PP_TEMP_DIR}"
    else
        # No project context — fall back to the install-wide temp root.
        dir="$(pgai_temp_dir)"
        echo "$dir"
        return 0
    fi

    mkdir -p "$dir"
    echo "$dir"
}

# ---------------------------------------------------------------------------
# pgai_mktemp_p [prefix] [project_name]
# Create a uniquely named temp FILE inside the per-project temp directory.
#
# Arguments:
#   prefix        — optional name prefix (default: "pgai_tmp")
#   project_name  — optional project name passed to pgai_project_temp_dir.
#                   When omitted, PP_TEMP_DIR is used if available.
#
# Usage:
#   local f; f="$(pgai_mktemp_p my_script)"
#   local f; f="$(pgai_mktemp_p my_script my-project)"
# ---------------------------------------------------------------------------
pgai_mktemp_p() {
    local prefix="${1:-pgai_tmp}"
    local project_name="${2:-}"
    local dir
    dir="$(pgai_project_temp_dir "$project_name")"
    mktemp "${dir}/${prefix}.XXXXXX"
}

# ---------------------------------------------------------------------------
# pgai_mktemp_d_p [prefix] [project_name]
# Create a uniquely named temp DIRECTORY inside the per-project temp directory.
#
# Arguments:
#   prefix        — optional name prefix (default: "pgai_tmp")
#   project_name  — optional project name passed to pgai_project_temp_dir.
#                   When omitted, PP_TEMP_DIR is used if available.
#
# Usage:
#   local d; d="$(pgai_mktemp_d_p scratch)"
#   local d; d="$(pgai_mktemp_d_p scratch my-project)"
# ---------------------------------------------------------------------------
pgai_mktemp_d_p() {
    local prefix="${1:-pgai_tmp}"
    local project_name="${2:-}"
    local dir
    dir="$(pgai_project_temp_dir "$project_name")"
    mktemp -d "${dir}/${prefix}.XXXXXX"
}

# ---------------------------------------------------------------------------
# _pgai_tmp_is_kanban_residue <basename>
# Return 0 (true) if <basename> looks like a kanban-authored artifact that
# incorrectly landed in bare /tmp rather than under the configured temp root.
#
# The cleanliness check uses this helper to police only the kanban's own
# footprint. Only names that the kanban framework itself would produce (via
# pgai_mktemp / pgai_mktemp_d or direct bare-/tmp writes) are flagged;
# foreign runtime artifacts (e.g. runc-process*, systemd-private-*, snap.*)
# are not kanban residue and must not be flagged.
#
# The kanban's naming convention (from pgai_mktemp and pgai_mktemp_d):
#   pgai_mktemp [prefix]   → mktemp "${root}/${prefix}.XXXXXX"
#   pgai_mktemp_d [prefix] → mktemp -d "${root}/${prefix}.XXXXXX"
# The default prefix is "pgai_tmp"; callers may supply their own prefix.
# Correctly routed calls land under pgai_temp_dir, not bare /tmp.
#
# A bare-/tmp kanban write occurs when kanban code bypasses pgai_mktemp and
# calls mktemp (or a hardcoded path) directly in /tmp.  Such names will have
# one of the following shapes:
#   pgai_*       — kanban-prefixed mktemp calls (e.g. pgai_tmp.XXXXXX,
#                  pgai_kanban_tmp.XXXXXX, pgai_test.XXXXXX)
#   tmp.*        — default bash mktemp output (no template: mktemp → tmp.XXXXXX)
#
# Names produced by foreign runtime tools (runc-process*, systemd-*, snap.*,
# etc.) do not start with "pgai_" or "tmp." and are therefore not kanban
# residue. This is a positive-match design: entries are flagged ONLY when
# they look like kanban output, not when they fail to match a foreign-tool
# allow-list.
#
# Note: the kanban temp root basename (e.g. "pgai_kanban_tmp") is handled by
# the caller before invoking this helper — it is excluded unconditionally.
# ---------------------------------------------------------------------------
_pgai_tmp_is_kanban_residue() {
    local name="$1"
    case "$name" in
        pgai_*)
            # pgai_-prefixed names: written by kanban code using pgai_mktemp or
            # similar, accidentally routed to bare /tmp instead of pgai_temp_dir.
            return 0
            ;;
        tmp.*)
            # tmp.<XXXXXX>: default mktemp output when no template is supplied.
            # Bare "mktemp" calls (no arguments) produce this pattern; they are
            # the canonical bare-/tmp anti-pattern in kanban bash scripts.
            return 0
            ;;
    esac
    return 1  # foreign artifact — not kanban residue
}

# ---------------------------------------------------------------------------
# pgai_tmp_snapshot
# Capture the current set of top-level names inside /tmp as a sorted list,
# one entry per line.  Used by the test-runner harness to take a before/after
# snapshot so the post-suite cleanliness check can report new bare-/tmp
# entries created during the suite run.
#
# Output is written to stdout (redirect to a variable or file as needed).
#
# Usage:
#   _pre_tmp_snapshot="$(pgai_tmp_snapshot)"
# ---------------------------------------------------------------------------
pgai_tmp_snapshot() {
    # List only the top-level names (basenames) inside /tmp, one per line,
    # sorted.  We use 'ls -1A' which lists all entries (including hidden but
    # not . or ..) in a single-column format.  This avoids find/glob
    # pitfalls with filenames containing spaces.
    # The || true prevents non-zero exit if /tmp is empty (extremely unlikely).
    ls -1A /tmp 2>/dev/null | sort || true
}

# ---------------------------------------------------------------------------
# pgai_tmp_cleanliness_check <pre_snapshot_file>
# Compare the current /tmp top-level contents against a pre-run snapshot file
# and report any NEW entries that are NOT under the configured pgai temp root.
#
# The check enforces the kanban's own footprint contract: everything the
# kanban writes must land under pgai_temp_dir (default /tmp/pgai_kanban_tmp).
# Anything else that appears in bare /tmp during a suite run is unexpected
# kanban residue.
#
# This check is intentionally scoped to the KANBAN's footprint only.  It does
# NOT attempt to police other tools' temp dirs (pytest, node, tmux, claude
# CLI, etc.).  The mechanism — comparing before/after snapshots — naturally
# limits scope: only entries that were absent before the suite started are
# candidates for reporting.  If those entries happen to belong to another
# tool that started concurrently, that is a false positive; the operator can
# add a suite-specific opt-out if needed.  In practice, CI runs are isolated
# enough that concurrent /tmp activity is negligible.
#
# Arguments:
#   $1  Path to a file containing the pre-run snapshot (one basename per line,
#       as produced by pgai_tmp_snapshot).
#
# Returns:
#   0  — clean (no new kanban residue in bare /tmp)
#   1  — residue found (names printed to stderr)
#
# Usage:
#   pgai_tmp_cleanliness_check "$_PRE_TMP_SNAPSHOT_FILE"
# ---------------------------------------------------------------------------
pgai_tmp_cleanliness_check() {
    local snapshot_file="${1:-}"
    if [[ -z "$snapshot_file" || ! -f "$snapshot_file" ]]; then
        echo "temp.sh: pgai_tmp_cleanliness_check: snapshot file argument is required and must exist; got: ${snapshot_file:-<empty>}" >&2
        return 1
    fi

    local root
    root="$(pgai_temp_dir)"

    # Resolve the root to a canonical path so basename comparisons are reliable.
    local root_resolved
    root_resolved="$(readlink -m "$root")"

    # The basename of the kanban temp root (e.g. "pgai_kanban_tmp") is expected
    # to appear in /tmp — that is where the framework writes its own temp files.
    # We must not report it as residue.
    local root_basename
    root_basename="$(basename "$root_resolved")"

    # Capture the current /tmp snapshot and compute the diff: entries present
    # now that were absent in the pre-run snapshot.
    local current_snapshot
    current_snapshot="$(pgai_tmp_snapshot)"

    # Find new entries: lines in current_snapshot that are NOT in snapshot_file.
    # comm -23 requires sorted inputs (both are sorted: pgai_tmp_snapshot sorts
    # its output; snapshot_file was written from pgai_tmp_snapshot).
    local new_entries
    new_entries="$(comm -23 <(echo "$current_snapshot") "$snapshot_file" 2>/dev/null || true)"

    # Filter: keep only entries that are kanban-authored residue.
    # Two-step filter:
    #   1. Skip the configured kanban temp root basename (expected framework home).
    #   2. Skip foreign transient artifacts — entries not produced by kanban code.
    #      Uses _pgai_tmp_is_kanban_residue (positive match on kanban naming) so
    #      that tools like runc, systemd, snap, etc. do not trigger false positives.
    #      Only names the kanban framework itself would create (pgai_* or tmp.*)
    #      are treated as residue; everything else is silently ignored.
    local residue=()
    local entry
    while IFS= read -r entry; do
        [[ -z "$entry" ]] && continue
        # Skip the configured kanban temp root (e.g. pgai_kanban_tmp).
        [[ "$entry" == "$root_basename" ]] && continue
        # Skip foreign tool artifacts — only flag kanban-authored names.
        _pgai_tmp_is_kanban_residue "$entry" || continue
        residue+=("$entry")
    done <<< "$new_entries"

    if [[ "${#residue[@]}" -eq 0 ]]; then
        return 0
    fi

    # Residue found: report and return non-zero.
    echo "[pgai-cleanliness] FAIL: kanban suite left residue in bare /tmp." >&2
    echo "[pgai-cleanliness] The following entries appeared in /tmp during the suite run" >&2
    echo "[pgai-cleanliness] and are NOT under the configured kanban temp dir (${root}):" >&2
    for entry in "${residue[@]}"; do
        echo "[pgai-cleanliness]   /tmp/${entry}" >&2
    done
    echo "[pgai-cleanliness] Kanban code must write temp files under pgai_temp_dir (${root})." >&2
    echo "[pgai-cleanliness] Use pgai_mktemp_d / pgai_mktemp from temp.sh instead of bare /tmp paths." >&2
    return 1
}

# ---------------------------------------------------------------------------
# pgai_listener_cleanliness_check [project_subtree]
# Scan for TCP listeners whose owning process cwd or cmdline is under the
# project's temp subtree (or the full framework temp root when no subtree is
# given) and fail non-zero if any are found.
#
# This gates the class of listener-leak residue — the same philosophy as
# pgai_tmp_cleanliness_check does for files.  A test that spawns a network
# server and fails (or skips teardown on an exception path) will leave a
# listener bound to a port.  This check surfaces such leaks after pytest
# exits so the runner can fail non-zero with enough context for the operator
# to correlate the leaked listener to its fixture.
#
# Scope narrowing:
#   When multiple projects share the same framework temp root (e.g.
#   /tmp/pgai_kanban_tmp), a sibling project's leaked listener would be
#   falsely attributed to whichever project's runner happens to check next if
#   only the temp root basename is used.  To prevent cross-project false
#   positives, callers should supply the per-project subtree path so the check
#   matches only listeners rooted in that project's own portion of the temp
#   tree.  When no subtree is supplied the check falls back to the wide-scope
#   basename match (backward-compatible behavior).
#
# Detection mechanism:
#   1. Enumerate TCP listeners via "ss -tlnp" (preferred; RHEL9 standard).
#      Falls back to "lsof -iTCP -sTCP:LISTEN" when ss is absent.
#   2. For each unique listener pid, read /proc/<pid>/cwd (symlink target)
#      and /proc/<pid>/cmdline (NUL-delimited, converted to spaces).
#   3a. When a project subtree is set (see "Arguments" below): flag the pid
#       only when cwd starts with that subtree prefix OR cmdline contains it.
#       This prevents listeners from sibling projects' subtrees from being
#       attributed to this project.
#   3b. When no project subtree is set: flag the pid if cwd or cmdline
#       contains the framework temp root basename (wide-scope fallback, same
#       as pre-narrowing behavior).
#   4. For every flagged pid, print its pid and full cmdline to stderr.
#   5. Return non-zero when at least one flagged listener is found; return 0
#      when no flagged listeners exist.
#
# Scope constraint:
#   The check examines ONLY pids whose /proc entry is readable by the current
#   user.  Listeners owned by root, systemd, sshd, or other users are either
#   unreadable (skipped silently) or lack the temp-root footprint (not flagged).
#   This prevents false positives on foreign system services.
#
# Arguments:
#   $1 (optional) — absolute path to the project's temp subtree
#                   (e.g. /tmp/pgai_kanban_tmp/projects/pgai-agent-kanban).
#                   When provided, only listeners under this prefix are flagged.
#                   Overrides PGAI_PROJECT_TEMP_SUBTREE when both are set.
#
# Environment variables:
#   PGAI_PROJECT_TEMP_SUBTREE (optional) — same as $1; used when the positional
#                   argument is not supplied.  Set by gated runners to scope the
#                   check to the project being tested.
#
# Returns:
#   0  — no project-scoped listeners found (or no listeners found under the
#        framework temp root when running in wide-scope fallback mode)
#   1  — at least one scoped listener found (details printed to stderr)
#
# Usage:
#   pgai_listener_cleanliness_check
#   pgai_listener_cleanliness_check "/tmp/pgai_kanban_tmp/projects/my-project"
#   PGAI_PROJECT_TEMP_SUBTREE="/tmp/pgai_kanban_tmp/projects/my-project" \
#       pgai_listener_cleanliness_check
# ---------------------------------------------------------------------------
pgai_listener_cleanliness_check() {
    local root root_basename
    root="$(pgai_temp_dir)"
    root_basename="$(basename "$root")"

    # Resolve the project subtree scope.  Precedence: positional arg > env var > none.
    # When a subtree is set, the match is a full path-prefix check against cwd and a
    # substring check against cmdline.  When no subtree is set, fall back to the
    # basename substring match (wide-scope; catches all projects sharing this temp root).
    local project_subtree=""
    if [[ -n "${1:-}" ]]; then
        project_subtree="${1%/}"   # strip trailing slash for consistent prefix matching
    elif [[ -n "${PGAI_PROJECT_TEMP_SUBTREE:-}" ]]; then
        project_subtree="${PGAI_PROJECT_TEMP_SUBTREE%/}"
    fi

    # Enumerate listening TCP pids.  We try ss first (iproute2, always present
    # on RHEL9); fall back to lsof for environments where ss is absent.
    local raw_pids=()
    if command -v ss >/dev/null 2>&1; then
        # ss -tlnp output includes pid fields like: users:(("python3",pid=12345,fd=9))
        # Extract all numeric pid values from the users:() columns.
        while IFS= read -r pid_val; do
            [[ -n "$pid_val" ]] && raw_pids+=("$pid_val")
        done < <(ss -tlnp 2>/dev/null \
            | grep -oP 'pid=\K[0-9]+' \
            | sort -u)
    elif command -v lsof >/dev/null 2>&1; then
        # lsof -iTCP -sTCP:LISTEN outputs one line per fd; column 2 is the pid.
        while IFS= read -r pid_val; do
            [[ -n "$pid_val" ]] && raw_pids+=("$pid_val")
        done < <(lsof -iTCP -sTCP:LISTEN -n -P 2>/dev/null \
            | awk 'NR>1 {print $2}' \
            | sort -u)
    else
        # Neither tool is available — skip the check rather than block the run.
        echo "[pgai-listener] WARNING: neither ss nor lsof found; skipping listener cleanliness check." >&2
        return 0
    fi

    # For each listener pid, inspect /proc/<pid>/cwd and /proc/<pid>/cmdline.
    local flagged_pids=() flagged_cmdlines=()
    local pid cwd_target cmdline
    for pid in "${raw_pids[@]}"; do
        # Skip if we cannot read this pid's proc entry (foreign user / race).
        [[ -d "/proc/${pid}" ]] || continue

        # Read cwd symlink target; empty when unreadable.
        cwd_target="$(readlink "/proc/${pid}/cwd" 2>/dev/null || true)"

        # Read cmdline: NUL-delimited arguments; tr converts NULs to spaces so
        # the result is a printable single line.  We pipe through tr rather than
        # using $() on the raw binary file to avoid bash's "ignored null byte"
        # warnings (bash command substitution silently drops NULs but emits the
        # warning; tr handles the conversion cleanly before bash ever sees it).
        cmdline="$(tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null || true)"
        # Trim trailing space produced by the final NUL-to-space conversion.
        cmdline="${cmdline% }"

        # Flag based on the resolved scope:
        #   - Project subtree set: match cwd against the subtree prefix; match
        #     cmdline requiring a directory boundary after the subtree path so that
        #     a sibling project whose name is a prefix extension of this project
        #     (e.g. pgai-agent-kanban-ui vs pgai-agent-kanban) is not falsely
        #     matched.  The cmdline check accepts the subtree followed by '/'
        #     (another path component), ' ' (next whitespace-delimited token), or
        #     at the end of the string (subtree is the final argument).
        #   - No subtree: fall back to the basename substring check (wide scope).
        local matched=0
        if [[ -n "${project_subtree}" ]]; then
            if [[ "$cwd_target" == "${project_subtree}" || \
                  "$cwd_target" == "${project_subtree}/"* || \
                  "$cmdline" == *"${project_subtree}/"* || \
                  "$cmdline" == *"${project_subtree} "* || \
                  "$cmdline" == *"${project_subtree}" ]]; then
                matched=1
            fi
        else
            if [[ "$cwd_target" == *"${root_basename}"* || \
                  "$cmdline" == *"${root_basename}"* ]]; then
                matched=1
            fi
        fi

        if [[ "${matched}" -eq 1 ]]; then
            flagged_pids+=("$pid")
            flagged_cmdlines+=("$cmdline")
        fi
    done

    if [[ "${#flagged_pids[@]}" -eq 0 ]]; then
        return 0
    fi

    # Flagged listeners found: report and return non-zero.
    local scope_label
    if [[ -n "${project_subtree}" ]]; then
        scope_label="${project_subtree}"
    else
        scope_label="${root}"
    fi
    echo "[pgai-listener] FAIL: framework-rooted TCP listeners survived the test suite." >&2
    echo "[pgai-listener] The following listeners have cwd or cmdline under the scoped temp root (${scope_label}):" >&2
    local i
    for i in "${!flagged_pids[@]}"; do
        echo "[pgai-listener]   pid=${flagged_pids[$i]}  cmdline=${flagged_cmdlines[$i]}" >&2
    done
    echo "[pgai-listener] Tests must stop any server they start; use a teardown-guaranteed fixture." >&2
    return 1
}

# ---------------------------------------------------------------------------
# wake_tmp_litter_take_snapshot <snapshot_file> [session_epoch]
# Capture the current top-level entries under /tmp (names + mtimes) to a
# state file so the post-session check can diff against them.
#
# Called pre-dispatch, before the provider CLI runs. The state file lives
# under the task's own temp subtree — never under bare /tmp.
#
# Arguments:
#   $1  Absolute path for the snapshot file (write, create parent dirs).
#   $2  Optional: session start epoch (seconds since Unix epoch).  Defaults
#       to "$(date +%s)".  Stored as a header line so the post-check can
#       apply the age filter.
#
# Output format (written to the snapshot file):
#   Line 1: epoch=<N>          — session start epoch
#   Lines 2+: <mtime_epoch>\t<basename>  — one entry per /tmp top-level item
#             sorted by basename so comm(1) can be used on the name column.
#
# Returns 0 on success; non-zero (and writes an error to stderr) on failure.
# The wake bracket calls this inside a fire-and-forget guard: failure here
# must not block the task.
# ---------------------------------------------------------------------------
wake_tmp_litter_take_snapshot() {
    local snapshot_file="${1:-}"
    local session_epoch="${2:-$(date +%s)}"

    if [[ -z "$snapshot_file" ]]; then
        echo "temp.sh: wake_tmp_litter_take_snapshot: snapshot_file argument required" >&2
        return 1
    fi

    # Create parent directory; refuse to write to bare /tmp.
    local snap_dir
    snap_dir="$(dirname "$snapshot_file")"
    if [[ -z "$snap_dir" || "$snap_dir" == "/tmp" ]]; then
        echo "temp.sh: wake_tmp_litter_take_snapshot: refusing to write snapshot to /tmp directly — use a subdirectory" >&2
        return 1
    fi
    mkdir -p "$snap_dir" || {
        echo "temp.sh: wake_tmp_litter_take_snapshot: cannot create snapshot directory ${snap_dir}" >&2
        return 1
    }

    # Write header and mtime+name pairs for all current /tmp top-level entries.
    {
        echo "epoch=${session_epoch}"
        # stat -c '%Y\t%n' reads mtime epoch and basename in one pass.
        # Use find to enumerate, then stat each; fall back gracefully when /tmp
        # is inaccessible or empty.
        for _f in /tmp/*; do
            [[ -e "$_f" || -L "$_f" ]] || continue
            local _bn _mt
            _bn="$(basename "$_f")"
            _mt="$(stat -c '%Y' "$_f" 2>/dev/null || echo 0)"
            printf '%s\t%s\n' "$_mt" "$_bn"
        done | sort -k2
    } > "$snapshot_file" || {
        echo "temp.sh: wake_tmp_litter_take_snapshot: failed to write ${snapshot_file}" >&2
        return 1
    }
    return 0
}

# ---------------------------------------------------------------------------
# wake_tmp_litter_check_and_report \
#     <snapshot_file> <task_status_path> <task_id> \
#     <log_func_name> <project_name> <kanban_root>
#
# Post-session litter check: diff /tmp top-level entries against the
# pre-dispatch snapshot and report new entries that are not on the allowlist
# or under the framework temp root.
#
# Reporting only — never deletes anything. Fire-and-forget: any internal
# error is logged and the function returns 0 so the wake tail is unaffected.
#
# Arguments:
#   $1  snapshot_file        — path written by wake_tmp_litter_take_snapshot
#   $2  task_status_path     — absolute path to the task's status.md
#   $3  task_id              — task identifier string
#   $4  log_func_name        — name of the log() function in the calling scope
#                              (called as: "$log_func_name" "message")
#   $5  project_name         — project name (for overwatch actions.log path)
#   $6  kanban_root          — kanban root directory (for overwatch actions.log)
#
# Side effects when litter is found:
#   - Appends a `## Temp Litter` section to status.md naming each file
#   - Calls log_func_name with a one-line summary
#   - Appends one info entry to <kanban_root>/projects/<project_name>/overwatch/actions.log
#
# Allowlist (entries created during the session are NOT flagged):
#   systemd-*   — systemd runtime artifacts
#   tmux-*      — tmux socket/session directories
#   pytest-of-* — pytest temporary directories
#   <framework_temp_root> and descendants — kanban's own temp space
#   Entries whose mtime <= session_start_epoch (pre-existed; not flagged)
#
# Returns 0 always (fire-and-forget).
# ---------------------------------------------------------------------------
wake_tmp_litter_check_and_report() {
    # Wrap everything in a subshell so any unhandled error returns 0 to caller.
    (
    set +e

    local snapshot_file="${1:-}"
    local task_status_path="${2:-}"
    local task_id="${3:-}"
    local log_func="${4:-}"
    local project_name="${5:-}"
    local kanban_root="${6:-}"

    # Validate required arguments; bail silently when missing.
    if [[ -z "$snapshot_file" || -z "$task_status_path" || -z "$task_id" ]]; then
        echo "temp.sh: wake_tmp_litter_check_and_report: missing required arguments" >&2
        exit 0
    fi

    # Snapshot must be readable; if not (e.g. pre-snapshot failed), log and exit.
    if [[ ! -f "$snapshot_file" ]]; then
        if [[ -n "$log_func" ]] && command -v "$log_func" >/dev/null 2>&1; then
            "$log_func" "litter check: snapshot file not found (${snapshot_file}); skipping check for ${task_id}"
        else
            echo "temp.sh: wake_tmp_litter_check_and_report: snapshot file not found: ${snapshot_file}" >&2
        fi
        exit 0
    fi

    # Read session epoch from snapshot header.
    local session_epoch=0
    local header_line
    header_line="$(head -1 "$snapshot_file" 2>/dev/null || echo "")"
    if [[ "$header_line" == epoch=* ]]; then
        session_epoch="${header_line#epoch=}"
    fi

    # Resolve the framework temp root so we can exclude it and its descendants.
    local fw_root
    fw_root="$(pgai_temp_dir 2>/dev/null || echo "/tmp/pgai_kanban_tmp")"
    local fw_root_basename
    fw_root_basename="$(basename "$fw_root")"

    # Build the set of basenames that existed before the session (from snapshot).
    # Snapshot lines 2+ have format: <mtime>\t<basename>
    declare -A _pre_names
    while IFS=$'\t' read -r _mt _bn; do
        [[ -z "$_bn" || "$_bn" == epoch=* ]] && continue
        _pre_names["$_bn"]=1
    done < <(tail -n +2 "$snapshot_file" 2>/dev/null)

    # Scan current /tmp top-level and identify litter.
    local litter=()
    for _f in /tmp/*; do
        [[ -e "$_f" || -L "$_f" ]] || continue
        local _bn _mt
        _bn="$(basename "$_f")"
        _mt="$(stat -c '%Y' "$_f" 2>/dev/null || echo 0)"

        # Skip if it was present before the session.
        [[ -n "${_pre_names[$_bn]+_}" ]] && continue

        # Skip the framework temp root.
        [[ "$_bn" == "$fw_root_basename" ]] && continue

        # Skip entries whose mtime predates the session (clock skew guard).
        if [[ "$session_epoch" -gt 0 && "$_mt" -le "$session_epoch" ]]; then
            continue
        fi

        # Allowlist patterns: systemd-*, tmux-*, pytest-of-*
        case "$_bn" in
            systemd-*|tmux-*|pytest-of-*)
                continue
                ;;
        esac

        litter+=("/tmp/${_bn}")
    done

    # Nothing to report — clean session.
    if [[ "${#litter[@]}" -eq 0 ]]; then
        exit 0
    fi

    # --- Report to status.md ---
    if [[ -f "$task_status_path" ]]; then
        {
            printf '\n## Temp Litter\n'
            for _lf in "${litter[@]}"; do
                printf 'created %s — SOP Temporary File Convention\n' "$_lf"
            done
        } >> "$task_status_path" 2>/dev/null || true
    fi

    # --- Log one summary line ---
    local _count="${#litter[@]}"
    local _summary="litter check: ${task_id} left ${_count} bare /tmp entry(ies): ${litter[*]}"
    if [[ -n "$log_func" ]] && command -v "$log_func" >/dev/null 2>&1; then
        "$log_func" "$_summary"
    else
        echo "temp.sh: ${_summary}" >&2
    fi

    # --- Surface to OVERWATCH actions.log ---
    if [[ -n "$project_name" && -n "$kanban_root" ]]; then
        local _ow_log_dir="${kanban_root}/projects/${project_name}/overwatch"
        local _ow_log="${_ow_log_dir}/actions.log"
        mkdir -p "$_ow_log_dir" 2>/dev/null || true
        local _ts
        _ts="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "unknown")"
        # Format: timestamp<TAB>name<TAB>target<TAB>action<TAB>backup<TAB>reason
        printf '%s\tcheck-bare-tmp-litter\t%s\tlitter-reported\tnone\t%d bare /tmp entry(ies) created during session: %s\n' \
            "$_ts" "$task_id" "$_count" "${litter[*]}" >> "$_ow_log" 2>/dev/null || true
    fi

    exit 0
    )
    return 0
}
