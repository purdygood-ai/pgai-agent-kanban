#!/usr/bin/env bash
# team/scripts/cleanup-stale-artifact-only-branches.sh
#
# Scan the local dev-tree for feature branches that belong to artifact-only
# DONE tasks and whose branches sit at the RC merge-base (zero unique commits).
# These branches are safe to delete because no work was committed to them —
# the task's entire deliverable lives inside the task folder.
#
# Detection criteria mirror TESTER.md Section 2a (Check-2 exemption rules)
# and the false-positive lineage documented in the defect ledger.  The canonical
# example is WRITER-20260710-013-sop-disposition-table: Required Output names
# only artifacts/sop-disposition-table.md, Constraints forbids all source-tree
# edits, and the feature branch carries zero unique commits by design.
#
# Usage:
#   cleanup-stale-artifact-only-branches.sh [--apply] [--kanban-root DIR]
#                                            [--project-root DIR] [--rc BRANCH]
#                                            [--help]
#
# Options:
#   --apply              Delete the candidate branches (default: dry-run, print only)
#   --kanban-root DIR    Path to PGAI_AGENT_KANBAN_ROOT_PATH (default: env var or
#                        $HOME/pgai_agent_kanban)
#   --project-root DIR   Path to $PGAI_PROJECT_ROOT (default: auto-detected from
#                        kanban-root + active project)
#   --rc BRANCH          RC branch to compare against (default: auto-detected
#                        from first rc/* or ai_rc/* branch found in the repo)
#   --help               Print this usage text and exit 0
#
# Exit codes:
#   0  — dry-run completed (no deletions)
#   0  — --apply completed; all candidates deleted
#   1  — argument error
#   2  — --apply refused: a candidate branch is currently checked out in a
#          worktree (branch is active; deletion would lose the worktree context)
#   3  — RC branch could not be determined
#
# Protected branches (never deleted, even if they match detection criteria):
#   rc/*, ai_rc/*, main, ai_main, develop, ai_develop
#
# Detection criteria (MUST match TESTER.md Section 2a artifact-only exemption):
#   A feature branch is a candidate when ALL of:
#     1. The task's status.md shows State: DONE
#     2. The task's README.md has a ## Feature Branch that names the branch
#        (not "none")
#     3. The branch exists locally (git rev-parse succeeds)
#     4. The task is artifact-only per the exemption rules:
#          (a) ## Feature Branch is the literal string 'none' in README — but
#              this can't apply here since we need a real branch; OR
#          (b) ## Required Output names ONLY paths under artifacts/ AND
#              ## Constraints contains an explicit forbidding phrase
#     5. The branch has zero unique commits beyond the merge-base with the RC
#
# Note: This script never calls `git push` or `git fetch`. All operations
# are local. Deletion uses `git branch -D` (force-delete) because the branch
# has zero unique commits — there is nothing to lose, and `-d` would refuse
# to delete a branch that is not fully merged into HEAD (which may not be
# the RC branch in all worktree configurations).

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_NAME="$(basename "$0")"

# Protected branch patterns — never delete these
PROTECTED_PATTERNS=(
  "^rc/"
  "^ai_rc/"
  "^main$"
  "^ai_main$"
  "^develop$"
  "^ai_develop$"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

usage() {
  sed -n '/^# Usage:/,/^# Exit codes:/{ /^# Exit codes:/d; s/^# \{0,1\}//; p }' "$0"
}

log_info()  { printf '[INFO]  %s\n' "$*"; }
log_warn()  { printf '[WARN]  %s\n' "$*" >&2; }
log_error() { printf '[ERROR] %s\n' "$*" >&2; }

# Returns 0 if the branch name matches any protected pattern.
is_protected() {
  local branch="$1"
  local pat
  for pat in "${PROTECTED_PATTERNS[@]}"; do
    if [[ "$branch" =~ $pat ]]; then
      return 0
    fi
  done
  return 1
}

# Returns 0 if the task README describes an artifact-only DONE task.
# Detection criteria match TESTER.md Section 2a Check-2 exemption rule (b).
# Rule (a) — Feature Branch: none — is handled at the caller level (we only
# call this function for tasks that DO have a named feature branch).
task_is_artifact_only() {
  local readme="$1"
  local req_body con_body

  req_body=$(awk '/^## Required Output/{flag=1; next} /^## /{flag=0} flag' "$readme")
  con_body=$(awk '/^## Constraints/{flag=1; next} /^## /{flag=0} flag' "$readme")

  # Both sections must be present.
  [[ -n "$req_body" && -n "$con_body" ]] || return 1

  # Extract every backtick-quoted token containing a '/' from Required Output.
  local paths inside outside
  paths=$(printf '%s\n' "$req_body" | grep -oE '`[^`]+`' | tr -d '`' | awk '/\//')
  inside=$(printf '%s\n' "$paths" | awk 'NF && /^artifacts\//')
  outside=$(printf '%s\n' "$paths" | awk 'NF && !/^artifacts\//')

  # Must have at least one artifacts/ path AND no path pointing outside the task folder.
  [[ -n "$inside" && -z "$outside" ]] || return 1

  # Constraints must explicitly forbid source-tree edits.
  printf '%s\n' "$con_body" \
    | grep -qiE \
        'do not edit|do not modify|no source[- ]tree edits|forbids? source[- ]tree edits' \
    || return 1

  return 0
}

# Returns 0 if the given branch is currently checked out in any worktree.
branch_is_checked_out() {
  local branch="$1"
  git worktree list --porcelain \
    | awk '/^branch /{print $2}' \
    | grep -qxF "refs/heads/${branch}"
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

APPLY=false
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
PROJECT_ROOT="${PGAI_PROJECT_ROOT:-}"
RC_BRANCH=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)        APPLY=true;           shift ;;
    --kanban-root)  KANBAN_ROOT="$2";     shift 2 ;;
    --project-root) PROJECT_ROOT="$2";    shift 2 ;;
    --rc)           RC_BRANCH="$2";       shift 2 ;;
    --help|-h)      usage; exit 0 ;;
    *)
      log_error "Unknown argument: $1"
      usage >&2
      exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Resolve project root
# ---------------------------------------------------------------------------

if [[ -z "$PROJECT_ROOT" ]]; then
  # Default: use the kanban root as the project root (single-project mode)
  PROJECT_ROOT="$KANBAN_ROOT"
fi

TASKS_DIR="$PROJECT_ROOT/tasks"
if [[ ! -d "$TASKS_DIR" ]]; then
  log_error "Tasks directory not found: $TASKS_DIR"
  log_error "Set --project-root or PGAI_PROJECT_ROOT to the correct project root."
  exit 1
fi

# ---------------------------------------------------------------------------
# Resolve RC branch
# ---------------------------------------------------------------------------

if [[ -z "$RC_BRANCH" ]]; then
  # Auto-detect: prefer ai_rc/* then rc/*
  RC_BRANCH=$(git branch --list 'ai_rc/*' --format='%(refname:short)' | sort -V | tail -1)
  if [[ -z "$RC_BRANCH" ]]; then
    RC_BRANCH=$(git branch --list 'rc/*' --format='%(refname:short)' | sort -V | tail -1)
  fi
fi

if [[ -z "$RC_BRANCH" ]]; then
  log_error "Could not determine RC branch. Pass --rc BRANCH explicitly."
  exit 3
fi

log_info "RC branch: $RC_BRANCH"
log_info "Project root: $PROJECT_ROOT"
log_info "Tasks directory: $TASKS_DIR"
if [[ "$APPLY" == true ]]; then
  log_info "Mode: --apply (branches will be deleted)"
else
  log_info "Mode: dry-run (pass --apply to delete)"
fi
printf '\n'

# ---------------------------------------------------------------------------
# Scan for candidates
# ---------------------------------------------------------------------------

CANDIDATES=()
SKIPPED_PROTECTED=()
SKIPPED_MISSING=()
SKIPPED_NOT_ARTIFACT_ONLY=()
SKIPPED_HAS_COMMITS=()

for task_dir in "$TASKS_DIR"/*/; do
  [[ -d "$task_dir" ]] || continue

  status_file="$task_dir/status.md"
  readme_file="$task_dir/README.md"
  [[ -f "$status_file" && -f "$readme_file" ]] || continue

  # Must be DONE
  state=$(awk '/^## State/{getline; gsub(/^[[:space:]]+|[[:space:]]+$/,""); print; exit}' \
           "$status_file")
  [[ "$state" == "DONE" ]] || continue

  # Must have a named feature branch (not "none")
  branch=$(awk '/^## Feature Branch/{getline; gsub(/^[[:space:]]+|[[:space:]]+$/,""); print; exit}' \
            "$readme_file")
  [[ -n "$branch" && "$branch" != "none" ]] || continue

  task_id="$(basename "$task_dir")"

  # Branch must exist locally
  if ! git rev-parse --verify "refs/heads/$branch" >/dev/null 2>&1; then
    SKIPPED_MISSING+=("$branch (task: $task_id) — branch already deleted")
    continue
  fi

  # Never delete protected branches
  if is_protected "$branch"; then
    SKIPPED_PROTECTED+=("$branch (task: $task_id) — protected branch pattern")
    continue
  fi

  # Must be artifact-only (TESTER.md Section 2a, exemption rule (b))
  if ! task_is_artifact_only "$readme_file"; then
    SKIPPED_NOT_ARTIFACT_ONLY+=("$branch (task: $task_id) — not artifact-only")
    continue
  fi

  # Must have zero unique commits beyond RC merge-base (stale/no-op branch)
  merge_base=$(git merge-base "$RC_BRANCH" "$branch" 2>/dev/null || true)
  if [[ -z "$merge_base" ]]; then
    SKIPPED_NOT_ARTIFACT_ONLY+=("$branch (task: $task_id) — no merge-base with $RC_BRANCH")
    continue
  fi

  unique_commits=$(git log --oneline "${merge_base}..${branch}" 2>/dev/null || true)
  if [[ -n "$unique_commits" ]]; then
    SKIPPED_HAS_COMMITS+=("$branch (task: $task_id) — has unique commits beyond merge-base")
    continue
  fi

  CANDIDATES+=("$branch")
done

# ---------------------------------------------------------------------------
# Report candidates
# ---------------------------------------------------------------------------

if [[ ${#CANDIDATES[@]} -eq 0 ]]; then
  log_info "No stale artifact-only feature branches found. Nothing to do."
  printf '\n'
  if [[ ${#SKIPPED_MISSING[@]} -gt 0 ]]; then
    log_info "Already-deleted branches (skipped):"
    for entry in "${SKIPPED_MISSING[@]}"; do
      printf '  already deleted: %s\n' "$entry"
    done
  fi
  exit 0
fi

printf 'Stale artifact-only feature branches (candidates for deletion):\n'
for branch in "${CANDIDATES[@]}"; do
  printf '  candidate: %s\n' "$branch"
done
printf '\n'

if [[ ${#SKIPPED_HAS_COMMITS[@]} -gt 0 ]]; then
  printf 'Artifact-only branches with commits (not candidates — kept for safety):\n'
  for entry in "${SKIPPED_HAS_COMMITS[@]}"; do
    printf '  skipped: %s\n' "$entry"
  done
  printf '\n'
fi

# ---------------------------------------------------------------------------
# Guard: refuse --apply if any candidate is currently checked out
# ---------------------------------------------------------------------------

if [[ "$APPLY" == true ]]; then
  ACTIVE=()
  for branch in "${CANDIDATES[@]}"; do
    if branch_is_checked_out "$branch"; then
      ACTIVE+=("$branch")
    fi
  done

  if [[ ${#ACTIVE[@]} -gt 0 ]]; then
    log_error "--apply refused: the following candidate branches are currently checked out in a worktree:"
    for branch in "${ACTIVE[@]}"; do
      log_error "  $branch"
    done
    log_error "Switch to a different branch in the affected worktree(s) first, then re-run with --apply."
    exit 2
  fi
fi

# ---------------------------------------------------------------------------
# Dry-run exit
# ---------------------------------------------------------------------------

if [[ "$APPLY" == false ]]; then
  printf 'Dry-run complete. Re-run with --apply to delete the candidate branches listed above.\n'
  exit 0
fi

# ---------------------------------------------------------------------------
# Apply: delete candidates
# ---------------------------------------------------------------------------

DELETED=()
FAILED=()

for branch in "${CANDIDATES[@]}"; do
  if git branch -D "$branch"; then
    DELETED+=("$branch")
    log_info "Deleted: $branch"
  else
    FAILED+=("$branch")
    log_warn "Failed to delete: $branch"
  fi
done

printf '\n'
printf 'Deletion summary:\n'
printf '  Deleted: %d\n' "${#DELETED[@]}"
printf '  Failed:  %d\n' "${#FAILED[@]}"

if [[ ${#FAILED[@]} -gt 0 ]]; then
  log_warn "Some branches could not be deleted (see warnings above). Manual cleanup may be needed."
fi

exit 0
