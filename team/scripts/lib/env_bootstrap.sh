#!/usr/bin/env bash
# team/scripts/lib/env_bootstrap.sh
# Single source-of-truth bootstrap prelude for kanban entry-point scripts.
#
# PURPOSE
# -------
# Source this file as the first act (after the shebang and set-lines) in any
# entry-point script that depends on PGAI_AGENT_KANBAN_ROOT_PATH.  It:
#
#   1. If PGAI_AGENT_KANBAN_ROOT_PATH is already set, exits immediately
#      (the operator's explicit env wins; sourcing twice is a safe no-op).
#   2. Derives a candidate kanban root from the CALLING script's location
#      (BASH_SOURCE[1]) by walking upward past the scripts/ subdirectory
#      layer.  Entry points live at:
#        <root>/scripts/<name>.sh          (depth 1 below root)
#        <root>/scripts/cm/<name>.sh       (depth 2 below root)
#        <root>/scripts/dashboard/<name>.sh (depth 2 below root)
#   3. Sources <candidate>/shell-env when it exists, giving shell-env the
#      opportunity to export PGAI_AGENT_KANBAN_ROOT_PATH.
#   3.5. Sources <candidate>/claude-env when it exists — AFTER shell-env,
#      always. claude-env carries Claude CLI runtime tuning (see
#      claude-env_example); it is optional, and an absent file is a clean
#      no-op. A claude-env with a bash syntax error fails this script loud
#      rather than silently half-sourcing (see the refusal site below).
#   4. If PGAI_AGENT_KANBAN_ROOT_PATH is STILL unset after the source
#      attempt, emits a diagnostic message on stderr and returns 1 (which
#      exits the calling script when that script runs under set -e).
#   5. On success: absolutizes the winning value via realpath and exports it.
#      Zero bytes written to stdout.
#
# USAGE
# -----
# In any entry-point script under team/scripts/:
#
#   #!/usr/bin/env bash
#   set -euo pipefail
#   # shellcheck source=lib/env_bootstrap.sh
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"   # from scripts/
#   # … or from scripts/cm/ or scripts/dashboard/:
#   source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"
#
# IDEMPOTENCY
# -----------
# Sourcing this file twice is harmless.  The first source sets and exports
# PGAI_AGENT_KANBAN_ROOT_PATH; the second source sees it already set and
# returns 0 immediately without re-deriving or re-sourcing shell-env.
#
# OPERATOR ENV WINS
# -----------------
# When PGAI_AGENT_KANBAN_ROOT_PATH is set in the caller's environment
# (e.g. via an explicit shell export or --kanban-root processing in the
# calling script), this prelude leaves it unchanged.  The operator's
# explicit env is always the highest-priority source.

# --- Idempotency guard: already set → absolutize, export, and return clean ---
# When the caller has already set PGAI_AGENT_KANBAN_ROOT_PATH, honor it as-is
# (the operator's explicit env wins).  Absolutize to ensure consistency with
# the derive-from-source path below; use realpath -m so this succeeds even
# when the operator-set path does not yet exist on disk.
if [[ -n "${PGAI_AGENT_KANBAN_ROOT_PATH:-}" ]]; then
    PGAI_AGENT_KANBAN_ROOT_PATH="$(realpath -m "${PGAI_AGENT_KANBAN_ROOT_PATH}")"
    export PGAI_AGENT_KANBAN_ROOT_PATH
    return 0
fi

# --- Derive the candidate kanban root from the calling script's location ---
#
# BASH_SOURCE[1] is the path of the script that sourced this file.
# Entry-point locations and the corresponding walk:
#
#   <root>/scripts/foo.sh           → dirname → <root>/scripts/
#                                     basename = "scripts" → up one → <root>
#
#   <root>/scripts/cm/foo.sh        → dirname → <root>/scripts/cm/
#                                     basename = "cm"      → up one → <root>/scripts/
#                                     basename = "scripts" → up one → <root>
#
#   <root>/scripts/dashboard/foo.sh → dirname → <root>/scripts/dashboard/
#                                     basename = "dashboard" → up one → <root>/scripts/
#                                     basename = "scripts"   → up one → <root>
#
# The walk stops as soon as the current directory's basename is NOT one of
# the known scripts-layer names (scripts, cm, dashboard).  What remains is
# the candidate root.

_eb_caller="${BASH_SOURCE[1]:-}"
_eb_candidate=""

if [[ -n "${_eb_caller}" ]]; then
    _eb_dir="$(cd "$(dirname "${_eb_caller}")" 2>/dev/null && pwd)" || _eb_dir=""

    if [[ -n "${_eb_dir}" ]]; then
        # Walk upward past the scripts-layer directories.
        _eb_walk="${_eb_dir}"
        while true; do
            _eb_base="$(basename "${_eb_walk}")"
            if [[ "${_eb_base}" == "scripts" || \
                  "${_eb_base}" == "cm"      || \
                  "${_eb_base}" == "dashboard" ]]; then
                _eb_walk="$(dirname "${_eb_walk}")"
            else
                break
            fi
        done
        _eb_candidate="${_eb_walk}"
    fi
fi

# --- Source shell-env when present ---
if [[ -n "${_eb_candidate}" && -f "${_eb_candidate}/shell-env" ]]; then
    # shellcheck source=/dev/null
    source "${_eb_candidate}/shell-env"
fi

# --- Source claude-env when present (AFTER shell-env; see claude-env_example) ---
# claude-env carries Claude CLI runtime tuning -- a concern shell-env,
# secrets, and kanban.cfg do not own (claude-env_example's header states the
# four-file separation in full). It is optional -- most installs will never
# create one -- and an absent file is a clean no-op: no error, no warning,
# no output, no exit-status change, mirroring shell-env's own optional-file
# idiom immediately above. It must be sourced strictly AFTER shell-env
# because shell-env is what establishes PGAI_AGENT_KANBAN_ROOT_PATH and the
# virtualenv -- a claude-env sourced first would have no root to resolve
# against.
#
# A syntax error in claude-env is checked with `bash -n` BEFORE sourcing,
# never discovered mid-source: `source` executes a script's earlier
# top-level statements as it parses along, so sourcing a syntactically
# broken file directly can silently half-configure the caller's
# environment before the parser ever reaches the bad line. Refusing here is
# BLOCK-WORTHY (docs/blocking-site-census.md): a half-sourced claude-env is
# a state-corrupting failure a cron or pseudocron firing has no human
# present to notice, so the wake firing must stop rather than continue on a
# partial, unverifiable environment. Note: naming the file's PATH below is
# fine; this refusal never prints the VALUE of anything claude-env sets.
if [[ -n "${_eb_candidate}" && -f "${_eb_candidate}/claude-env" ]]; then
    _eb_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    # shellcheck source=error_contract.sh
    source "${_eb_lib_dir}/error_contract.sh"
    if ! bash -n "${_eb_candidate}/claude-env" 2>/dev/null; then
        pgai_emit_error_contract \
            "env_bootstrap.claude_env_syntax_error" \
            "claude-env at ${_eb_candidate}/claude-env has a bash syntax error and was not sourced." \
            "Run: bash -n ${_eb_candidate}/claude-env -- fix the reported syntax error, then re-run this script."
        # CENSUS-ANCHOR: env-bootstrap-claude-env-01
        unset _eb_caller _eb_candidate _eb_dir _eb_walk _eb_base _eb_lib_dir
        return 1
    fi
    # shellcheck source=/dev/null
    source "${_eb_candidate}/claude-env"
    unset _eb_lib_dir
fi

# --- Fail loud when the root is still unset after the source attempt ---
# Partial remedy (docs/coding-standards.md "Gates prescribe, not just
# diagnose"): the candidate shell-env path is fully derivable and named below, but the
# correct PGAI_AGENT_KANBAN_ROOT_PATH value for a non-standard install layout
# is site-specific — only the operator knows it — so the fix command below is
# a filled-in example the reader adapts, not a claimed universal literal.
if [[ -z "${PGAI_AGENT_KANBAN_ROOT_PATH:-}" ]]; then
    echo "PGAI_AGENT_KANBAN_ROOT_PATH not set — shell-env missing or broken at ${_eb_candidate}/shell-env" >&2
    echo "" >&2
    echo "Cause: no PGAI_AGENT_KANBAN_ROOT_PATH export reached this shell — the" >&2
    echo "derived candidate shell-env file at ${_eb_candidate}/shell-env is either" >&2
    echo "missing or present but does not export the variable." >&2
    echo "" >&2
    echo "To fix:" >&2
    echo "  1. ls -la \"${_eb_candidate}/shell-env\"" >&2
    echo "  2. printf 'export PGAI_AGENT_KANBAN_ROOT_PATH=\"%s\"\\n' \"${_eb_candidate}\" >> \"${_eb_candidate}/shell-env\"" >&2
    echo "  3. Re-run this script — env_bootstrap.sh re-sources shell-env on the next invocation." >&2
    echo "" >&2
    echo "Rule: every kanban entry-point script resolves PGAI_AGENT_KANBAN_ROOT_PATH" >&2
    echo "through this shared prelude before its first use; a missing or broken" >&2
    echo "shell-env fails loud here rather than silently defaulting." >&2
    unset _eb_caller _eb_candidate _eb_dir _eb_walk _eb_base
    return 1
fi

# --- Absolutize and export the winning root ---
# Use realpath -m so a valid config root that does not exist yet still resolves.
PGAI_AGENT_KANBAN_ROOT_PATH="$(realpath -m "${PGAI_AGENT_KANBAN_ROOT_PATH}")"
export PGAI_AGENT_KANBAN_ROOT_PATH

# --- Clean up local temporaries ---
unset _eb_caller _eb_candidate _eb_dir _eb_walk _eb_base
