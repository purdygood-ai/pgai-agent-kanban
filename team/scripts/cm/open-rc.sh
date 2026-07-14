#!/usr/bin/env bash
# cm-open-rc.sh
# Human-invoked or CM-agent-invoked: opens a release candidate branch from main.
#
# Usage:
#   cm-open-rc.sh [--project <name>] <version> [task-id]
#
# --project <name>   explicit project name (overrides PGAI_PROJECT_NAME env var)
# <version>          must be in format vX.Y.Z (e.g. v0.4.0)
# [task-id]          optional: the CM agent task ID that invoked this script.
#                    Recorded as "RC Opened By Task" in release-state.md.
#                    Defaults to "cm-open-rc.sh (manual)" if not provided.
#
# Project context resolution (highest to lowest precedence):
#   1. --project <name> flag
#   2. PGAI_PROJECT_NAME environment variable
#   3. FAIL — prints error naming both knobs, exits non-zero
#
# Behavior:
#   1. Validates version format
#   2. Reads project-scoped release-state.md ($KANBAN_ROOT/projects/<name>/release-state.md)
#   3. Refuses if Active RC != none
#   4. Checks rc/<version> does not already exist locally or on origin
#   5. git fetch origin                      (skipped when push_to_remote=false)
#   6. git checkout main && git pull --ff-only    (pull skipped when push_to_remote=false)
#   7. Writes project-scoped release-state.md using here-doc template (canonical format)
#      Only RC fields are written (Active RC, RC Opened At, RC Opened By Task).
#      Last Released* fields are NOT read from or written to release-state.md;
#      the canonical Last Released record is the git tag created by cm-release.sh.
#   8. git checkout -b rc/<version>
#   9. git push -u origin rc/<version>
#  10. git checkout main
#  11. Prints success message and exits 0
#
# Release-state.md is the project-scoped live install file only:
#   $KANBAN_ROOT/projects/<name>/release-state.md
# The dev tree's team/release-state.md is NOT read or written by this script.
#
# On any failure mid-sequence, the cleanup_on_exit trap attempts rollback:
#   - Deletes rc/<version> from origin if it was pushed
#   - Deletes local rc/<version> branch if it was created
#   - Restores the project-scoped release-state.md to its pre-run content
#
# Configuration:
#   PGAI_PROJECT_NAME — project name (required when --project flag not used)
#   REPO_ROOT         — override path to the repository root (normally derived
#                       from the project's project.cfg dev_tree_path)

# --- Argument parsing ---
# Done before strict mode so error messages are clean.
PROJECT_ARG=""
VERSION=""
TASK_ID=""
_POSITIONAL=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)
      if [[ -z "${2:-}" ]]; then
        echo "ERROR: --project requires a value" >&2
        echo "Usage: $(basename "$0") [--project <name>] <version> [task-id]" >&2
        exit 1
      fi
      PROJECT_ARG="$2"
      shift 2
      ;;
    --*)
      echo "ERROR: unknown flag: $1" >&2
      echo "Usage: $(basename "$0") [--project <name>] <version> [task-id]" >&2
      exit 1
      ;;
    *)
      _POSITIONAL+=("$1")
      shift
      ;;
  esac
done

VERSION="${_POSITIONAL[0]:-}"
TASK_ID="${_POSITIONAL[1]:-cm-open-rc.sh (manual)}"

if [[ -z "$VERSION" ]]; then
  echo "ERROR: missing required argument <version>" >&2
  echo "" >&2
  echo "Usage: $(basename "$0") [--project <name>] <version> [task-id]" >&2
  echo "" >&2
  echo "  --project: project name (overrides PGAI_PROJECT_NAME env var)" >&2
  echo "  version:   format vX.Y.Z (e.g. v0.4.0)" >&2
  echo "  task-id:   optional CM agent task ID (default: cm-open-rc.sh (manual))" >&2
  exit 1
fi

# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

# --- Resolve script directory ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Source optional config files (BEFORE strict mode) ---
# The kanban bashrc/env may have unset vars, non-zero returns, or interactive
# aliases that would trip strict mode. Source them first.
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
TEAM_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
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

# --- Source semver helpers ---
# shellcheck source=lib/semver.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/semver.sh"

# --- Enable strict mode for our own code ---
set -euo pipefail

# --- Resolve project context ---
# Resolution order: --project flag > PGAI_PROJECT_NAME env var > error.
# pp_require_project_context prints a diagnostic and returns 1 when neither is set.
PROJECT_NAME="$(pp_require_project_context "${PROJECT_ARG:-}")" || {
  echo "" >&2
  echo "ERROR: project context is required." >&2
  echo "  Set PGAI_PROJECT_NAME=<name> in the environment, or pass --project <name>." >&2
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

# --- Resolve base branch name via pp_prefix_branch ---
# For projects with branch_prefix=ai_, MAIN_BRANCH=ai_main.
# For projects with no branch_prefix, MAIN_BRANCH=main unchanged.
MAIN_BRANCH="$(pp_prefix_branch "$PROJECT_NAME" "main")"

# --- Read push_to_remote flag via pp_push_to_remote helper ---
# Default: 'true' — pushes rc branch to origin (existing behavior preserved).
# Set [project] push_to_remote = false in project.cfg to complete the full local
# open-rc operation without any git push origin calls.
_CM_PUSH_TO_REMOTE="$(KANBAN_ROOT="$KANBAN_ROOT" pp_push_to_remote "$PROJECT_NAME")"
if [[ "$_CM_PUSH_TO_REMOTE" == "true" ]]; then
  echo "[cm-open-rc] Push policy: push_to_remote=true — rc branch will be pushed to origin."
else
  echo "[cm-open-rc] Push policy: push_to_remote=false — rc branch stays local. Operator must push manually."
fi

# --- Rollback state tracking ---
# These flags are set as the atomic sequence progresses so that
# cleanup_on_exit knows how far we got and what to undo.
RC_BRANCH_CREATED=0     # set to 1 after local rc branch created
RC_BRANCH_PUSHED=0      # set to 1 after rc branch pushed to origin
RELEASE_STATE_WRITTEN=0 # set to 1 after project-scoped release-state.md is written
RELEASE_STATE_ORIGINAL="" # populated with original content before we overwrite

# --- Cleanup / rollback on exit ---
# On non-zero exit, rolls back partial state from the atomic sequence.
# Sets Needs Human: yes in a status note if rollback itself fails.
cleanup_on_exit() {
  local exit_code=$?
  if [[ $exit_code -ne 0 ]]; then
    echo "" >&2
    echo "--- cm-open-rc.sh FAILED (exit $exit_code) — attempting rollback ---" >&2

    # Rollback: delete rc branch from origin if it was pushed
    if [[ $RC_BRANCH_PUSHED -eq 1 ]]; then
      echo "  Rolling back: deleting $RC_BRANCH from origin..." >&2
      if git -C "$REPO_ROOT" push origin --delete "$RC_BRANCH" 2>/dev/null; then
        echo "  Rolled back: $RC_BRANCH deleted from origin." >&2
      else
        echo "  WARNING: could not delete $RC_BRANCH from origin. Needs Human: yes" >&2
      fi
    fi

    # Rollback: delete local rc branch if it was created
    if [[ $RC_BRANCH_CREATED -eq 1 ]]; then
      echo "  Rolling back: deleting local branch $RC_BRANCH..." >&2
      if git -C "$REPO_ROOT" checkout "$MAIN_BRANCH" 2>/dev/null && \
         git -C "$REPO_ROOT" branch -D "$RC_BRANCH" 2>/dev/null; then
        echo "  Rolled back: local branch $RC_BRANCH deleted." >&2
      else
        echo "  WARNING: could not delete local branch $RC_BRANCH. Needs Human: yes" >&2
      fi
    fi

    # Rollback: restore project-scoped release-state.md to its pre-run content
    if [[ $RELEASE_STATE_WRITTEN -eq 1 && -n "$RELEASE_STATE_ORIGINAL" && -n "${RELEASE_STATE:-}" ]]; then
      echo "  Rolling back: restoring project-scoped release-state.md..." >&2
      if printf '%s' "$RELEASE_STATE_ORIGINAL" > "$RELEASE_STATE" 2>/dev/null; then
        echo "  Rolled back: project-scoped release-state.md restored." >&2
      else
        echo "  WARNING: could not restore project-scoped release-state.md. Needs Human: yes" >&2
      fi
    fi

    echo "--- Rollback complete. Inspect repository state before retrying. ---" >&2
    echo "--- If rollback warnings appeared above, set Needs Human: yes and do not update state. ---" >&2
  fi

  jobs -p 2>/dev/null | xargs -r kill 2>/dev/null || true
  wait 2>/dev/null || true
  exit $exit_code
}
trap cleanup_on_exit EXIT

# --- Paths ---
# Canonical release state file: project-scoped live install path.
# This is the ONLY file cm-open-rc.sh reads from and writes to.
# The dev tree's team/release-state.md is NOT consulted.
RELEASE_STATE="$(pp_release_state "$PROJECT_NAME")"

# --- Validate version format ---
VERSION_REGEX='^v[0-9]+\.[0-9]+\.[0-9]+$'
if [[ ! "$VERSION" =~ $VERSION_REGEX ]]; then
  echo "ERROR: invalid version format: '$VERSION'" >&2
  echo "Expected format: vX.Y.Z (e.g. v0.4.0)" >&2
  exit 1
fi

RC_BRANCH="$(pp_prefix_branch "$PROJECT_NAME" "rc/$VERSION")"

# --- Validate release-state.md exists ---
if [[ ! -f "$RELEASE_STATE" ]]; then
  echo "ERROR: project-scoped release-state.md not found: $RELEASE_STATE" >&2
  echo "Expected path: \$KANBAN_ROOT/projects/<name>/release-state.md" >&2
  echo "Ensure PGAI_AGENT_KANBAN_ROOT_PATH is set and the project directory exists." >&2
  exit 1
fi

# Capture original content before any modification (used by rollback).
RELEASE_STATE_ORIGINAL="$(cat "$RELEASE_STATE")"

# --- Read a named field from release-state.md ---
# Usage: read_field <field_heading>
# Finds "## <field_heading>" then returns the next non-blank non-comment line.
read_field() {
  local heading="$1"
  python3 "$KANBAN_ROOT/pgai_agent_kanban/cm/read_state_field.py" "$RELEASE_STATE" "$heading"
}

ACTIVE_RC="$(read_field "Active RC")"

if [[ "$ACTIVE_RC" != "none" ]]; then
  echo "ERROR: an active RC already exists: $ACTIVE_RC" >&2
  echo "Close the existing RC before opening a new one." >&2
  exit 1
fi

# Note: Last Released* fields are intentionally not read here.
# The canonical Last Released record is the git tag created by cm-release.sh.
# release-state.md tracks only in-flight RC state.

# --- Check rc/<version> does not already exist locally ---
if git -C "$REPO_ROOT" rev-parse --verify "refs/heads/$RC_BRANCH" >/dev/null 2>&1; then
  echo "ERROR: branch '$RC_BRANCH' already exists locally" >&2
  exit 1
fi

# --- Check rc/<version> does not already exist on origin ---
if git -C "$REPO_ROOT" ls-remote --exit-code --heads origin "$RC_BRANCH" >/dev/null 2>&1; then
  echo "ERROR: branch '$RC_BRANCH' already exists on origin" >&2
  exit 1
fi

# --- Guard 1: reject if a git tag for this version already exists ---
# A pre-existing tag means cm-release.sh would fail at the `git tag` step.
if git -C "$REPO_ROOT" rev-parse --verify "refs/tags/$VERSION" >/dev/null 2>&1; then
  echo "ERROR: git tag '$VERSION' already exists in $REPO_ROOT" >&2
  echo "  Opening an RC at a version that is already tagged would collide with" >&2
  echo "  the existing release. Choose a version that has not yet been tagged." >&2
  exit 1
fi

# --- Guard 2: reject if VERSION is not strictly greater than last released ---
# pp_last_released_version returns the highest semver tag merged into origin/main.
LAST_RELEASED="$(pp_last_released_version "$PROJECT_NAME")"
if ! semver_gt "$VERSION" "$LAST_RELEASED"; then
  echo "ERROR: version '$VERSION' is not strictly greater than the last released version '$LAST_RELEASED'" >&2
  echo "  The RC version must be greater than the last release to avoid collision." >&2
  echo "  Last released: $LAST_RELEASED" >&2
  echo "  Requested RC:  $VERSION" >&2
  echo "  Choose a version greater than $LAST_RELEASED (e.g. the next patch, minor, or major)." >&2
  exit 1
fi

# --- git fetch (gated by push_to_remote: no origin traffic in local-only mode) ---
if [[ "$_CM_PUSH_TO_REMOTE" == "true" ]]; then
  echo "Fetching origin..."
  git -C "$REPO_ROOT" fetch origin
else
  echo "push_to_remote=false — skipping git fetch origin (local-only install)."
fi

# --- Checkout main and pull ---
# Gated by push_to_remote flag: the ff-only pull from origin is skipped when
# push_to_remote=false (origin/$MAIN_BRANCH need not exist).  The checkout
# still runs — the rc branch is cut from MAIN_BRANCH as current HEAD.
echo "Checking out $MAIN_BRANCH and pulling..."
git -C "$REPO_ROOT" checkout "$MAIN_BRANCH"
if [[ "$_CM_PUSH_TO_REMOTE" == "true" ]]; then
  git -C "$REPO_ROOT" merge --ff-only "origin/$MAIN_BRANCH"
else
  echo "push_to_remote=false — skipping ff-only pull from origin/$MAIN_BRANCH (local-only install)."
fi

# --- Write project-scoped release-state.md using here-doc template (canonical format) ---
# Single blank line between sections. Written directly to the live install path.
# Only RC fields are written. Last Released* fields are NOT written — the git tag
# created by cm-release.sh is the canonical Last Released record.
# The dev tree's team/release-state.md is NOT modified.
OPENED_AT="$(date -Iseconds)"

echo "Writing project-scoped release-state.md with canonical format..."
cat > "$RELEASE_STATE" <<EOF
# Release State

## Active RC
${VERSION}

## RC Opened At
${OPENED_AT}

## RC Opened By Task
${TASK_ID}
EOF
RELEASE_STATE_WRITTEN=1

# --- Atomic sequence: create rc branch and push ---
# Rollback flags are set as we progress so cleanup_on_exit can undo partial work.
# Any failure in this block halts the script (set -e) and triggers rollback.
# release-state.md is written to the project-scoped live install path only;
# no git commit of release-state.md is made in the dev tree.

# Create the RC branch from main (or the prefixed equivalent)
echo "Creating branch $RC_BRANCH from $MAIN_BRANCH..."
git -C "$REPO_ROOT" checkout -b "$RC_BRANCH"
RC_BRANCH_CREATED=1

# --- Write per-RC release-state JSON ---
# Written after successful RC branch creation so it is only present when
# the branch actually exists. Overwritten on recovery re-runs (idempotent).
# Schema: { "rc", "opened_at", "closed_at", "outcome" }
# All timestamps are ISO8601 UTC (date -u +%Y-%m-%dT%H:%M:%SZ).
RC_STATE_DIR="$(pp_project_root "$PROJECT_NAME")/release-state"
RC_STATE_JSON="${RC_STATE_DIR}/${VERSION}.json"
OPENED_AT_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
mkdir -p "$RC_STATE_DIR"
python3 "$KANBAN_ROOT/pgai_agent_kanban/cm/write_rc_state.py" open "$VERSION" "$OPENED_AT_UTC" > "$RC_STATE_JSON"
echo "  Per-RC release-state JSON written: $RC_STATE_JSON"

# Push rc branch to origin (gated by push_to_remote flag)
if [[ "$_CM_PUSH_TO_REMOTE" == "true" ]]; then
  echo "Pushing $RC_BRANCH to origin..."
  git -C "$REPO_ROOT" push -u origin "$RC_BRANCH"
  RC_BRANCH_PUSHED=1
else
  echo "[push_to_remote=false] skipping origin push for ${PROJECT_NAME}: git push -u origin $RC_BRANCH"
fi

# Switch back to main
echo "Switching back to $MAIN_BRANCH..."
git -C "$REPO_ROOT" checkout "$MAIN_BRANCH"

echo ""
echo "RC branch opened successfully."
echo "  Project:    $PROJECT_NAME"
echo "  Branch:     $RC_BRANCH"
echo "  Repo root:  $REPO_ROOT"
echo "  Opened at:  $OPENED_AT"
echo "  State file: $RELEASE_STATE"
echo "  RC state:   $RC_STATE_JSON"
echo ""
echo "Next: run pm-agent.sh to decompose the release work."
exit 0
