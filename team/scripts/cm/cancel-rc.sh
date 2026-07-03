#!/usr/bin/env bash
# cm-cancel-rc.sh
# Human-invoked: cleanly abandons an active release candidate branch.
#
# Usage:
#   cm-cancel-rc.sh --project <name> --key vX.Y.Z [--yes] [--help]
#
# Both --project and --key are REQUIRED. Positional invocation is not supported.
#
# --project <name>   project name (required; overrides PGAI_PROJECT_NAME env var)
# --key vX.Y.Z       RC version to cancel (required; format: vX.Y.Z)
# --yes              skip interactive confirmation prompt (for scripted/non-interactive use)
# --help             print this message and exit 0
#
# Project context resolution:
#   --project flag drives project resolution via the pp layer.
#
# =============================================================================
# FOOTPRINT — what this script touches and what it deliberately leaves alone
# =============================================================================
#
# TOUCHES (modifies or deletes):
#   rc/<version> branch on origin
#     - Deleted via `git push origin --delete rc/<version>` if the branch
#       exists on origin. Skipped (idempotent) if already absent.
#   rc/<version> branch locally
#     - Deleted via `git branch -D rc/<version>` if the branch exists locally.
#       Switches to develop first if currently on the rc branch.
#       Skipped (idempotent) if already absent.
#   $KANBAN_ROOT/projects/<name>/release-state.md
#     - Three fields are reset to `none`:
#         Active RC
#         RC Opened At
#         RC Opened By Task
#     - Skipped (idempotent) if Active RC is already `none`.
#   develop branch (local + origin)
#     - After resetting release-state.md the script checks out develop, runs
#       `merge --ff-only origin/develop`, then `git push origin develop`.
#       This syncs develop with origin but does not change its commit history.
#
# DOES NOT TOUCH (explicitly preserved):
#   main branch             — never checked out, never merged, never pushed
#   git tags                — no tags are created, moved, or deleted
#   Last Released fields in release-state.md
#                           — "Last Released", "Last Released At", and
#                             "Last Released By Task" are never written; the
#                             git tag created by cm-release.sh is the canonical
#                             Last Released record
#   Task folders            — task status.md files are not modified by this script;
#                             caller is responsible for marking tasks WONT-DO
#   Queue files             — coder_backlog.md, cm_backlog.md, etc. are not modified
#   requirements/ files     — requirements files are not renamed or deleted
#   priority/ files         — priority files are not modified
#   bugs/ files             — bug files are not modified
#   Artifacts               — task artifacts/ directories are not modified
#   Dev tree source files   — no files under the git worktree are modified
#
# NOTE: The top-level team/scripts/cancel-rc.sh script (distinct from this one)
# performs a broader "full unwind" that DOES touch task folders, queue files,
# requirements files, priority files, bugs files, and PM plan markers. Use that
# script when a full cancellation is needed. Use this script (cm/cancel-rc.sh)
# when only the git branch and release-state.md need to be reset.
#
# If you need to re-run the CM release step without deleting the rc branch, use
# reset.sh --project <name> --key <version> instead.
#
# =============================================================================
#
# Behavior:
#   1.  Validates version format
#   2.  Validates repository state (must be on develop or rc/<version>)
#   3.  git fetch origin   (skipped when push_to_remote=false)
#   4.  Reads Active RC from project-scoped release-state.md (live install) and
#       verifies Active RC = <version>
#   5.  Lists pending tasks for the cancelled RC version
#   6.  Prompts for confirmation (unless --yes passed)
#   7.  Deletes rc/<version> branch from origin (if it exists there)
#   8.  Deletes rc/<version> branch locally (if it exists)
#   9.  Resets project-scoped release-state.md: clears Active RC + RC Opened At +
#       RC Opened By Task. Last Released* fields are NOT touched — the git tag
#       created by cm-release.sh is the canonical Last Released record.
#  10.  Prints success message and exits 0
#
# release-state.md is the project-scoped live install file only:
#   $KANBAN_ROOT/projects/<name>/release-state.md
# The dev tree's team/release-state.md is NOT read or written by this script.
#
# Idempotency:
#   Safe to re-run if a step failed midway. Steps 7 and 8 are skipped if the
#   branch does not exist. Steps 9-10 are skipped if Active RC is already none.
#
# DOES NOT touch main, does not touch tags. Conservative cleanup only.
#
# Configuration:
#   PGAI_PROJECT_NAME — fallback project name (when --project flag not used)
#   REPO_ROOT         — override path to the repository root (normally derived
#                       from the project's project.cfg dev_tree_path)
#   KANBAN_ROOT       — path to the kanban root (default: $HOME/pgai_agent_kanban)

# --- Argument parsing ---
# Done before strict mode so error messages are clean.
PROJECT_ARG=""
VERSION=""
YES_FLAG=0

_cm_cancel_rc_usage() {
  echo "Usage: $(basename "$0") --project <name> --key vX.Y.Z [--yes] [--help]" >&2
  echo "" >&2
  echo "  --project <name>  project name (required)" >&2
  echo "  --key vX.Y.Z      RC version to cancel (required; format: vX.Y.Z)" >&2
  echo "  --yes             skip interactive confirmation prompt" >&2
  echo "  --help            print full usage and footprint documentation" >&2
}

_cm_cancel_rc_help() {
  cat <<HELPTEXT
Usage: $(basename "$0") --project <name> --key vX.Y.Z [--yes] [--help]

Cancel an active release candidate branch and reset release-state.md.

Both --project and --key are REQUIRED. Positional invocation is not supported.

Arguments:
  --project <name>  project name; drives project resolution via the pp layer
  --key vX.Y.Z      RC version to cancel (format: vX.Y.Z, e.g. v0.15.4)
  --yes             skip the interactive confirmation prompt
  --help            print this message and exit 0

FOOTPRINT — what this script touches vs. leaves alone:

  TOUCHES:
    origin/rc/<version>          deleted (git push origin --delete)
    local rc/<version>           deleted (git branch -D)
    release-state.md             Active RC / RC Opened At / RC Opened By Task
                                 reset to 'none'
    develop branch (local+origin) fast-forward synced and pushed (no history change)

  DOES NOT TOUCH:
    main branch                  never checked out, merged, or pushed
    git tags                     never created, moved, or deleted
    release-state.md Last Released fields
                                 preserved verbatim (canonical from git tag)
    task folders / status.md     not modified; caller must mark tasks WONT-DO
    queue files                  coder_backlog.md, cm_backlog.md, etc. not modified
    requirements/ files          not renamed or deleted
    priority/ files              not modified
    bugs/ files                  not modified
    artifacts/                   not modified
    dev tree source files        not modified

  NOTE: For a full unwind (task folders, queue markers, requirements file,
  priority files, bug files, PM plan markers) use the top-level
  team/scripts/cancel-rc.sh instead.

  NOTE: To re-run the CM release step without deleting the rc branch, use
  reset.sh --project <name> --key <version> instead.

Configuration:
  PGAI_PROJECT_NAME   fallback project name (when --project flag not used)
  REPO_ROOT           override path to the repository root
  KANBAN_ROOT         path to the kanban root (default: \$HOME/pgai_agent_kanban)
HELPTEXT
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      _cm_cancel_rc_help
      exit 0
      ;;
    --project)
      if [[ -z "${2:-}" ]]; then
        echo "ERROR: --project requires a value" >&2
        echo "" >&2
        _cm_cancel_rc_usage
        exit 1
      fi
      PROJECT_ARG="$2"
      shift 2
      ;;
    --key)
      if [[ -z "${2:-}" ]]; then
        echo "ERROR: --key requires a value" >&2
        echo "" >&2
        _cm_cancel_rc_usage
        exit 1
      fi
      VERSION="$2"
      shift 2
      ;;
    --yes|-y)
      YES_FLAG=1
      shift
      ;;
    --*)
      echo "ERROR: unknown flag: $1" >&2
      echo "" >&2
      _cm_cancel_rc_usage
      exit 1
      ;;
    *)
      echo "ERROR: positional arguments are not supported; use --project and --key flags" >&2
      echo "" >&2
      _cm_cancel_rc_usage
      exit 1
      ;;
  esac
done

if [[ -z "$VERSION" ]]; then
  echo "ERROR: missing required flag --key vX.Y.Z" >&2
  echo "" >&2
  _cm_cancel_rc_usage
  exit 1
fi

if [[ -z "$PROJECT_ARG" && -z "${PGAI_PROJECT_NAME:-}" ]]; then
  echo "ERROR: missing required flag --project <name>" >&2
  echo "" >&2
  _cm_cancel_rc_usage
  exit 1
fi

# --- Resolve script directory ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Source optional config files (BEFORE strict mode) ---
# The kanban bashrc/env may have unset vars, non-zero returns, or interactive
# aliases that would trip strict mode. Source them first.
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"
TEAM_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"
[[ -f "$KANBAN_ROOT/bashrc" ]] && source "$KANBAN_ROOT/bashrc"
[[ -f "$KANBAN_ROOT/env" ]] && source "$KANBAN_ROOT/env"
[[ -f "$HOME/.config/pgai-kanban.cfg" ]] && source "$HOME/.config/pgai-kanban.cfg"
# Source ini_parser.sh for read_ini (needed before project_paths.sh is available).
# shellcheck source=lib/ini_parser.sh
[[ -f "${SCRIPT_DIR}/../lib/ini_parser.sh" ]] && source "${SCRIPT_DIR}/../lib/ini_parser.sh"
# Source kanban.cfg — INI format replaces legacy config.cfg
if [[ -f "$TEAM_ROOT/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "$TEAM_ROOT/kanban.cfg" chain pm_mode automatic)}"
    export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(read_ini "$TEAM_ROOT/kanban.cfg" paths dev_tree_path "")}"
fi

# --- Source project path helpers ---
# shellcheck source=lib/project_paths.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/project_paths.sh"

# --- Enable strict mode for our own code ---
set -euo pipefail

# --- Resolve project context ---
# Resolution order: --project flag > PGAI_PROJECT_NAME env var > error.
# pp_require_project_context prints a diagnostic and returns 1 when neither is set.
PROJECT_NAME="$(pp_require_project_context "${PROJECT_ARG:-}")" || {
  echo "" >&2
  echo "ERROR: project context is required." >&2
  echo "  Pass --project <name>, or set PGAI_PROJECT_NAME=<name> in the environment." >&2
  exit 1
}

# --- Resolve REPO_ROOT from project.cfg dev_tree_path ---
# REPO_ROOT may be overridden by the environment; otherwise derive from project.cfg.
if [[ -z "${REPO_ROOT:-}" ]]; then
  pp_load_config "$PROJECT_NAME" || {
    echo "ERROR: could not load project.cfg for project '$PROJECT_NAME'" >&2
    echo "  Expected: $(pp_project_root "$PROJECT_NAME" 2>/dev/null || echo "<unresolvable>")/project.cfg" >&2
    exit 1
  }
  REPO_ROOT="${PP_dev_tree_path:-}"
  if [[ -z "$REPO_ROOT" ]]; then
    echo "ERROR: dev_tree_path is not set in project.cfg for project '$PROJECT_NAME'" >&2
    echo "  Add 'dev_tree_path=<path>' to $(pp_project_root "$PROJECT_NAME")/project.cfg" >&2
    exit 1
  fi
fi

# --- Resolve base branch names via pp_prefix_branch ---
# For projects with branch_prefix=ai_, DEVELOP_BRANCH=ai_develop.
# For projects with no branch_prefix, DEVELOP_BRANCH=develop unchanged.
DEVELOP_BRANCH="$(pp_prefix_branch "$PROJECT_NAME" "develop")"

# --- Read push_to_remote flag via pp_push_to_remote helper ---
# Default: 'true' — deletes rc branch from origin and pushes develop (existing behavior).
# Set [project] push_to_remote = false in project.cfg to perform only local cleanup
# (no origin branch delete, no develop push).
_CM_PUSH_TO_REMOTE="$(KANBAN_ROOT="$KANBAN_ROOT" pp_push_to_remote "$PROJECT_NAME")"
if [[ "$_CM_PUSH_TO_REMOTE" == "true" ]]; then
  echo "[cm-cancel-rc] Push policy: push_to_remote=true — origin branch delete and develop push will proceed."
else
  echo "[cm-cancel-rc] Push policy: push_to_remote=false — skipping origin operations. Local cleanup only."
fi

# --- Paths ---
# Canonical release state file: project-scoped live install path.
# This is the ONLY file cm-cancel-rc.sh reads from and writes to.
# The dev tree's team/release-state.md is NOT consulted.
RELEASE_STATE="$(pp_release_state "$PROJECT_NAME")"

# --- Validate version format ---
VERSION_REGEX='^v[0-9]+\.[0-9]+\.[0-9]+$'
if [[ ! "$VERSION" =~ $VERSION_REGEX ]]; then
  echo "ERROR: invalid version format: '$VERSION'" >&2
  echo "Expected format: vX.Y.Z (e.g. v0.15.4)" >&2
  exit 1
fi

RC_BRANCH="$(pp_prefix_branch "$PROJECT_NAME" "rc/$VERSION")"

# --- Read a named field from release-state.md ---
# Usage: read_field <field_heading>
# Finds "## <field_heading>" then returns the next non-blank non-comment line.
read_field() {
  local heading="$1"
  python3 "$KANBAN_ROOT/pgai_agent_kanban/cm/read_state_field.py" "$RELEASE_STATE" "$heading"
}

# --- Step 1: git fetch (gated by push_to_remote: no origin traffic in local-only mode) ---
if [[ "$_CM_PUSH_TO_REMOTE" == "true" ]]; then
  echo "Fetching origin..."
  git -C "$REPO_ROOT" fetch origin
else
  echo "push_to_remote=false — skipping git fetch origin (local-only install)."
fi

# --- Step 2: Check current branch is develop (or prefixed equivalent) or rc/<version> ---
CURRENT_BRANCH="$(git -C "$REPO_ROOT" symbolic-ref --short HEAD 2>/dev/null || echo "DETACHED")"
if [[ "$CURRENT_BRANCH" != "$DEVELOP_BRANCH" && "$CURRENT_BRANCH" != "$RC_BRANCH" ]]; then
  echo "ERROR: must be on '$DEVELOP_BRANCH' or '$RC_BRANCH' to cancel this RC" >&2
  echo "Current branch: $CURRENT_BRANCH" >&2
  echo "Please checkout $DEVELOP_BRANCH or $RC_BRANCH first." >&2
  exit 1
fi

# --- Step 3: Read Active RC from project-scoped release-state.md ---
# Read from the canonical live install file — this is the authoritative source.
if [[ ! -f "$RELEASE_STATE" ]]; then
  echo "ERROR: project-scoped release-state.md not found: $RELEASE_STATE" >&2
  echo "Expected path: \$KANBAN_ROOT/projects/<name>/release-state.md" >&2
  exit 1
fi

ACTIVE_RC="$(read_field "Active RC")"

# --- Step 4: Check idempotency — if already cancelled, report and exit cleanly ---
RC_LOCAL_EXISTS=0
RC_REMOTE_EXISTS=0

if git -C "$REPO_ROOT" rev-parse --verify "refs/heads/$RC_BRANCH" >/dev/null 2>&1; then
  RC_LOCAL_EXISTS=1
fi
if git -C "$REPO_ROOT" ls-remote --exit-code --heads origin "$RC_BRANCH" >/dev/null 2>&1; then
  RC_REMOTE_EXISTS=1
fi

if [[ "$ACTIVE_RC" == "none" && $RC_LOCAL_EXISTS -eq 0 && $RC_REMOTE_EXISTS -eq 0 ]]; then
  echo "INFO: RC cancellation already complete (idempotent run)." >&2
  echo "  Active RC on develop: none"
  echo "  $RC_BRANCH: not found locally or on origin"
  echo ""
  echo "Nothing to do. Exiting cleanly."
  exit 0
fi

# --- Step 5: Validate that Active RC matches the version we are cancelling ---
# Allow the case where Active RC is already none (partial cancellation recovery)
if [[ "$ACTIVE_RC" != "none" && "$ACTIVE_RC" != "$VERSION" ]]; then
  echo "ERROR: Active RC mismatch." >&2
  echo "  develop's release-state.md shows Active RC = '$ACTIVE_RC'" >&2
  echo "  You requested cancel of: '$VERSION'" >&2
  echo "  Only the active RC can be cancelled." >&2
  exit 1
fi

# --- Step 6: List pending tasks for the cancelled RC ---
echo ""
echo "RC to cancel: $RC_BRANCH"
echo "Current Active RC on develop: $ACTIVE_RC"
echo ""

# Search for tasks referencing this RC version in the kanban root
PENDING_TASKS=()
TASK_DIRS=()
for d in "$(pp_tasks_dir "$PROJECT_NAME")"/*/; do
  [[ -d "$d" ]] || continue
  status_file="$d/status.md"
  [[ -f "$status_file" ]] || continue
  readme_file="$d/README.md"
  [[ -f "$readme_file" ]] || continue

  # Check if task references this RC version and is not done/wont-do
  state_line="$(grep -m1 '^## State' "$status_file" 2>/dev/null || true)"
  state_val="$(echo "$state_line" | sed 's/## State//' | xargs 2>/dev/null || true)"
  if [[ "$state_val" == "DONE" || "$state_val" == "WONT-DO" ]]; then
    continue
  fi

  # Check if README references this version
  if grep -q "$VERSION" "$readme_file" 2>/dev/null; then
    task_id="$(basename "$d")"
    PENDING_TASKS+=("$task_id ($state_val)")
    TASK_DIRS+=("$d")
  fi
done

if [[ ${#PENDING_TASKS[@]} -gt 0 ]]; then
  echo "Pending tasks for $VERSION:"
  for t in "${PENDING_TASKS[@]}"; do
    echo "  - $t"
  done
  echo ""
  echo "NOTE: These tasks will NOT be automatically cancelled by this script."
  echo "      You should manually set them to WONT-DO after cancellation."
  echo ""
else
  echo "No pending tasks found for $VERSION in kanban root."
  echo ""
fi

# --- Step 7: Confirmation prompt (unless --yes) ---
if [[ $YES_FLAG -eq 0 ]]; then
  echo "This will:"
  if [[ $RC_REMOTE_EXISTS -eq 1 ]]; then
    echo "  - Delete $RC_BRANCH from origin"
  fi
  if [[ $RC_LOCAL_EXISTS -eq 1 ]]; then
    echo "  - Delete local branch $RC_BRANCH"
  fi
  if [[ "$ACTIVE_RC" != "none" ]]; then
    echo "  - Reset develop's release-state.md: Active RC -> none"
    echo "  - Commit and push the reset on develop"
  fi
  echo ""
  echo "This does NOT touch main, does NOT delete any tags."
  echo ""
  read -r -p "Proceed with cancellation of $RC_BRANCH? [y/N] " confirm
  case "$confirm" in
    [yY]|[yY][eE][sS]) ;;
    *)
      echo "Cancelled. No changes made."
      exit 1
      ;;
  esac
  echo ""
fi

# --- Step 8: Delete rc branch from origin (if it exists) ---
# Gated by push_to_remote flag: skipped when push_to_remote=false.
if [[ "$_CM_PUSH_TO_REMOTE" == "true" ]]; then
  if [[ $RC_REMOTE_EXISTS -eq 1 ]]; then
    echo "Deleting $RC_BRANCH from origin..."
    git -C "$REPO_ROOT" push origin --delete "$RC_BRANCH"
    echo "  Deleted $RC_BRANCH from origin."
  else
    echo "  $RC_BRANCH not found on origin — skipping remote delete (idempotent)."
  fi
else
  echo "[push_to_remote=false] skipping origin push for ${PROJECT_NAME}: git push origin --delete $RC_BRANCH (Step 8)"
fi

# --- Step 9: Delete rc branch locally (if it exists) ---
if [[ $RC_LOCAL_EXISTS -eq 1 ]]; then
  # Must not be on the branch we are deleting
  if [[ "$CURRENT_BRANCH" == "$RC_BRANCH" ]]; then
    echo "Switching to $DEVELOP_BRANCH before deleting local branch..."
    git -C "$REPO_ROOT" checkout "$DEVELOP_BRANCH"
    if [[ "$_CM_PUSH_TO_REMOTE" == "true" ]]; then
      git -C "$REPO_ROOT" merge --ff-only "origin/$DEVELOP_BRANCH"
    fi
  fi
  echo "Deleting local branch $RC_BRANCH..."
  git -C "$REPO_ROOT" branch -D "$RC_BRANCH"
  echo "  Deleted local branch $RC_BRANCH."
else
  echo "  Local branch $RC_BRANCH not found — skipping local delete (idempotent)."
fi

# --- Step 10: Reset release-state.md on develop ---
if [[ "$ACTIVE_RC" != "none" ]]; then
  # Write only the RC fields. Last Released* fields are intentionally NOT written —
  # the git tag created by cm-release.sh is the canonical Last Released record.
  # If Last Released* sections happen to be present in an existing release-state.md
  # they are dropped here (forward-compat: the new schema has no Last Released fields).
  echo "Resetting project-scoped release-state.md (Active RC -> none)..."
  cat > "$RELEASE_STATE" <<EOF
# Release State

## Active RC
none

## RC Opened At
none

## RC Opened By Task
none
EOF

  echo "  release-state.md reset."
  echo "  Path: $RELEASE_STATE"

  # Sync develop branch (or prefixed equivalent) with origin (no release-state.md git commit
  # needed — the file lives in the live install, outside the git repo).
  # The checkout and local fast-forward always happen; only the push is gated.
  echo "Syncing $DEVELOP_BRANCH branch..."
  git -C "$REPO_ROOT" checkout "$DEVELOP_BRANCH"
  if [[ "$_CM_PUSH_TO_REMOTE" == "true" ]]; then
    git -C "$REPO_ROOT" merge --ff-only "origin/$DEVELOP_BRANCH"
    git -C "$REPO_ROOT" push origin "$DEVELOP_BRANCH"
    echo "  $DEVELOP_BRANCH synced and pushed to origin."
  else
    echo "[push_to_remote=false] skipping origin push for ${PROJECT_NAME}: git push origin $DEVELOP_BRANCH (Step 10)"
    echo "  $DEVELOP_BRANCH checked out locally; no push (push_to_remote=false)."
  fi
else
  echo "  Active RC already none — skipping release-state.md reset (idempotent)."
fi

# --- Write/update per-RC release-state JSON with cancelled outcome ---
# Written after the cancel operation completes (branch deleted, release-state.md cleared).
# Reads existing opened_at from the file if present (written by open-rc.sh).
# If no state file exists (open-rc ran before this fix shipped), writes a minimal record.
# Non-blocking: any failure is logged as a warning; cancel exit code is unaffected.
_rc_state_dir="${KANBAN_ROOT}/projects/${PROJECT_NAME}/release-state"
_rc_state_json="${_rc_state_dir}/${VERSION}.json"
_cancel_closed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
mkdir -p "$_rc_state_dir" 2>/dev/null || true
python3 "$KANBAN_ROOT/pgai_agent_kanban/cm/write_rc_state.py" cancel \
  "$_rc_state_json" "$VERSION" "$_cancel_closed_at" 2>&1 || \
  echo "  WARNING: could not update per-RC release-state JSON at $_rc_state_json" >&2

echo ""
echo "RC cancellation complete."
echo "  Cancelled: $RC_BRANCH"
echo "  Cancelled at: $(date -Iseconds)"
echo ""
if [[ ${#PENDING_TASKS[@]} -gt 0 ]]; then
  echo "Reminder: mark these tasks as WONT-DO in the kanban:"
  for t in "${PENDING_TASKS[@]}"; do
    echo "  - $t"
  done
  echo ""
fi
echo "You may now open a new RC with cm-open-rc.sh <version>."
exit 0
