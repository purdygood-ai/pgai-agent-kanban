#!/usr/bin/env bash
# scripts/init-project-git-repo.sh
#
# Operator-run script: establish the git branch topology a project's chain
# requires on origin, BEFORE the first release.
#
# This closes the first-release block where cm/open-rc.sh fails with
# "origin/<branch> does not exist" because the operator created the base
# branch locally but never pushed it (or never created it at all).
#
# The script reads the project's project.cfg (dev_tree_path, git_repo_url,
# branch_prefix) and creates+pushes the single base branch the chain will
# operate on, resolved via pp_prefix_branch:
#
#   branch_prefix = ai_   ->  ai_main
#   branch_prefix empty   ->  main
#
# OPERATOR TOOLING ONLY.
#
#   This script DOES NOT touch CM or any role file.  It runs as the human
#   operator (who has ordinary git push credentials).  CM's managed-origin
#   constraint is unchanged.  Do not invoke this script from any agent path.
#
# Usage:
#   init-project-git-repo.sh --project <project-name>
#
# Arguments:
#   --project NAME   Name of the project registered in projects.cfg.
#                    Reads dev_tree_path and branch_prefix from its project.cfg.
#
# Behavior:
#   For the chain base branch (main — prefixed when branch_prefix is set):
#     1. Check if the branch already exists on origin (git ls-remote).
#        If already present: skip with "already present" message.
#     2. Check if the branch exists locally.
#        If missing locally: create it from HEAD (git checkout -b <branch>).
#     3. Push the branch to origin with upstream tracking (-u).
#   After processing the branch, print a summary and exit 0.
#
# Idempotent: safe to re-run. Existing remote branches are left untouched.
#
# Exit codes:
#   0  — the required branch is present on origin (created/pushed or
#         already existed); repo is ready for the chain's first release.
#   1  — usage error or missing required arguments
#   2  — project.cfg not found or required fields missing
#   3  — git operations failed (push error, invalid dev_tree_path, etc.)

# --- Source helpers and parse arguments (before strict mode for clean error messages) ---
_INIT_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/operator_args.sh
source "${_INIT_SCRIPT_DIR}/lib/operator_args.sh"
unset _INIT_SCRIPT_DIR

# Declared flag vocabulary: all flags this command accepts.
OPERATOR_VALID_FLAGS=(project help h)

# Value-taking flags: project.
# Boolean flags: help.
argparse_parse --value-flags "project" -- "$@"

# Handle --help / -h.
if argparse_has "help" || argparse_has "h"; then
    operator_args_render_help_for_flags "init-project-git-repo.sh" \
        "Establish git branch topology on origin before a project's first release." \
        OPERATOR_VALID_FLAGS
    exit 0
fi

# Reject unexpected positional arguments (hard-cut: no positional project name).
if [[ "${#ARGPARSE_POSITIONAL[@]}" -gt 0 ]]; then
    echo "ERROR: unexpected positional argument: ${ARGPARSE_POSITIONAL[0]}" >&2
    echo "Usage: $(basename "$0") --project <project-name>" >&2
    exit 1
fi

# Reject any flag not in the declared vocabulary.
operator_args_validate_known "$(basename "$0")" OPERATOR_VALID_FLAGS || exit 1

PROJECT_ARG="$(operator_args_project)"

if [[ -z "$PROJECT_ARG" ]]; then
    echo "ERROR: project name is required" >&2
    echo "" >&2
    echo "Usage: $(basename "$0") --project <project-name>" >&2
    echo "" >&2
    echo "  --project NAME   Name of the project registered in projects.cfg." >&2
    echo "                   Reads dev_tree_path and branch_prefix from its project.cfg." >&2
    exit 1
fi

# --- Bootstrap: self-locate → source shell-env → fail loud ---
# Must happen before the first use of PGAI_AGENT_KANBAN_ROOT_PATH so the
# script runs from a fresh shell without manual pre-sourcing.  Explicit
# operator exports win via env_bootstrap.sh's idempotency guard.
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh" || exit 1

# --- Resolve script and kanban root ---
# PGAI_AGENT_KANBAN_ROOT_PATH is now set by env_bootstrap.sh or the operator.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"

# --- Source optional config files (before strict mode) ---
[[ -f "$KANBAN_ROOT/bashrc" ]] && source "$KANBAN_ROOT/bashrc"
[[ -f "$KANBAN_ROOT/env" ]] && source "$KANBAN_ROOT/env"

# Source ini_parser.sh (needed by project_paths.sh)
# shellcheck source=lib/ini_parser.sh
if [[ -f "${SCRIPT_DIR}/lib/ini_parser.sh" ]]; then
    source "${SCRIPT_DIR}/lib/ini_parser.sh"
elif [[ -f "${KANBAN_ROOT}/team/scripts/lib/ini_parser.sh" ]]; then
    source "${KANBAN_ROOT}/team/scripts/lib/ini_parser.sh"
else
    echo "ERROR: cannot find ini_parser.sh — expected at ${SCRIPT_DIR}/lib/ini_parser.sh" >&2
    exit 2
fi

# Source project_paths.sh (provides pp_prefix_branch, pp_load_config, etc.)
# shellcheck source=lib/project_paths.sh
if [[ -f "${SCRIPT_DIR}/lib/project_paths.sh" ]]; then
    source "${SCRIPT_DIR}/lib/project_paths.sh"
elif [[ -f "${KANBAN_ROOT}/team/scripts/lib/project_paths.sh" ]]; then
    source "${KANBAN_ROOT}/team/scripts/lib/project_paths.sh"
else
    echo "ERROR: cannot find project_paths.sh — expected at ${SCRIPT_DIR}/lib/project_paths.sh" >&2
    exit 2
fi

# --- Enable strict mode for our own code ---
set -euo pipefail

# --- Resolve project context ---
export KANBAN_ROOT
PROJECT_NAME="$(pp_require_project_context "$PROJECT_ARG")" || {
    echo "" >&2
    echo "ERROR: project context is required." >&2
    echo "  Pass --project <name> or set \$PGAI_PROJECT_NAME." >&2
    exit 1
}

echo "Project: $PROJECT_NAME"
echo ""

# --- Load project config ---
pp_load_config "$PROJECT_NAME" || {
    echo "ERROR: could not load project.cfg for project '$PROJECT_NAME'" >&2
    echo "  Expected: $(pp_project_root "$PROJECT_NAME" 2>/dev/null || echo "<unresolvable>")/project.cfg" >&2
    exit 2
}

# --- Verify dev_tree_path ---
DEV_TREE="${PP_dev_tree_path:-}"
if [[ -z "$DEV_TREE" ]]; then
    echo "ERROR: dev_tree_path is not set in project.cfg for project '$PROJECT_NAME'" >&2
    echo "  Edit: $(pp_project_root "$PROJECT_NAME")/project.cfg" >&2
    echo "  Add:  dev_tree_path = /path/to/your/git/repo" >&2
    exit 2
fi

if [[ ! -d "$DEV_TREE" ]]; then
    echo "ERROR: dev_tree_path does not exist: $DEV_TREE" >&2
    exit 2
fi

if ! git -C "$DEV_TREE" rev-parse --git-dir &>/dev/null 2>&1; then
    echo "ERROR: dev_tree_path is not a git repository: $DEV_TREE" >&2
    exit 2
fi

echo "Dev tree:  $DEV_TREE"

# --- Resolve the chain base branch name via pp_prefix_branch ---
# Single source of truth for branch naming — same helper CM scripts use.
MAIN_BRANCH="$(pp_prefix_branch "$PROJECT_NAME" "main")"

echo "Required base branch:"
echo "  $MAIN_BRANCH"
echo ""

# --- Process each base branch ---
# Returns:
#   0 — branch present on origin (existed or just pushed)
#   3 — push failed
_ensure_branch_on_origin() {
    local branch="$1"
    local dev_tree="$2"

    echo "--- Branch: $branch ---"

    # Step 1: Check if branch already exists on origin.
    if git -C "$dev_tree" ls-remote --exit-code --heads origin "$branch" &>/dev/null 2>&1; then
        echo "  [SKIP]   already present on origin/$branch — no action needed."
        return 0
    fi

    # Step 2: Check if branch exists locally.
    if ! git -C "$dev_tree" rev-parse --verify "refs/heads/$branch" &>/dev/null 2>&1; then
        echo "  [CREATE] branch '$branch' not found locally — creating from HEAD..."
        local current_branch
        current_branch="$(git -C "$dev_tree" symbolic-ref --short HEAD 2>/dev/null || echo "")"
        if [[ -z "$current_branch" ]]; then
            echo "ERROR: cannot determine current HEAD branch in $dev_tree" >&2
            return 3
        fi
        git -C "$dev_tree" branch "$branch"
        echo "  [CREATE] created branch '$branch' from HEAD ($current_branch)."
    else
        echo "  [EXISTS] branch '$branch' exists locally."
    fi

    # Step 3: Push to origin with upstream tracking.
    echo "  [PUSH]   pushing $branch to origin..."
    if git -C "$dev_tree" push -u origin "$branch"; then
        echo "  [DONE]   $branch pushed to origin and upstream tracking set."
    else
        echo "ERROR: failed to push '$branch' to origin" >&2
        echo "  Verify you have push credentials for the remote." >&2
        echo "  Remote: $(git -C "$dev_tree" remote get-url origin 2>/dev/null || echo '<unresolvable>')" >&2
        return 3
    fi

    return 0
}

# --- Process main branch ---
main_status="error"
if _ensure_branch_on_origin "$MAIN_BRANCH" "$DEV_TREE"; then
    main_status="ok"
else
    echo "" >&2
    echo "FAILED to ensure $MAIN_BRANCH on origin. See errors above." >&2
    exit 3
fi
echo ""

# --- Verify final state (informational only — we just pushed, so this should succeed) ---
echo "=== Verification ==="
echo "Checking origin branch state..."
remote_branches="$(git -C "$DEV_TREE" ls-remote --heads origin "$MAIN_BRANCH" 2>/dev/null || true)"
echo "$remote_branches" | while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    sha="${line%%$'\t'*}"
    ref="${line##*$'\t'}"
    branch_name="${ref#refs/heads/}"
    printf "  %-30s  %s\n" "$branch_name" "${sha:0:12}"
done

echo ""
echo "=== Summary ==="
printf "  %-30s  %s\n" "$MAIN_BRANCH" "$main_status"
echo ""
echo "Repository is ready for the chain's first release."
echo "  Project:    $PROJECT_NAME"
echo "  Dev tree:   $DEV_TREE"
echo "  Next step:  cm/open-rc.sh --project $PROJECT_NAME <version>"
echo ""
exit 0
