#!/usr/bin/env bash
# cm-finalize-release.sh
# Operator convenience script: commits any WRITER polish to release notes, then
# pushes main and the release tag to origin (or the configured REMOTE).
#
# Usage:
#   cm-finalize-release.sh [--project <name>] [-y] [VERSION]
#
#   --project <name>   explicit project name (overrides PGAI_PROJECT_NAME env var)
#   -y                 Skip the confirmation prompt (non-interactive / CI mode).
#   VERSION            The version tag to push (e.g. v0.17.1). If omitted, the
#                      script reads the most recent tag reachable from HEAD on main.
#
# Project context resolution (highest to lowest precedence):
#   1. --project <name> flag
#   2. PGAI_PROJECT_NAME environment variable
#   3. FAIL — prints error naming both knobs, exits non-zero
#
# Run this script from a shell where the PreToolUse hook does NOT apply
# (i.e. from a regular operator terminal, not inside the Claude agent).
#
# What this script does:
#   1. Resolve the version (from $1 or from the most recent tag on main)
#   2. Confirm the push with the operator (skip with -y)
#   2b. If release-notes/<VERSION>.md has uncommitted WRITER polish, commit it
#       as "Polish release notes for <VERSION>" before pushing. If the file is
#       unchanged from the cm-release.sh stub, this step is a no-op. If the
#       file is missing, a warning is logged and the step is skipped.
#   3. git push $REMOTE $MAIN_BRANCH  (includes the polish commit if Step 2b ran)
#   4. git push $REMOTE <VERSION>
#   5. Optionally create a GitHub release via gh CLI if available and authenticated
#   6. Print a summary
#
# Prerequisites:
#   - PGAI_PROJECT_NAME must be set (or --project passed)
#   - main branch must be checked out (or REPO_ROOT must be set)
#   - The tag must already exist locally (cm-release.sh creates it)
#
# Configuration:
#   PGAI_PROJECT_NAME — project name (required when --project flag not used)
#   REPO_ROOT         — override path to the repository root (normally derived
#                       from the project's project.cfg dev_tree_path)

# --- Source optional config files (BEFORE strict mode) ---
# The kanban bashrc/env may have unset vars, non-zero returns, or interactive
# aliases that would trip strict mode. Source them first.
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"
[[ -f "$KANBAN_ROOT/bashrc" ]] && source "$KANBAN_ROOT/bashrc"
[[ -f "$KANBAN_ROOT/env" ]] && source "$KANBAN_ROOT/env"
[[ -f "$HOME/.config/pgai-kanban.cfg" ]] && source "$HOME/.config/pgai-kanban.cfg"

# --- Source project path helpers ---
# shellcheck source=lib/project_paths.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/project_paths.sh"

set -euo pipefail

# --- Remote name ---
# All git-push operations below use this variable. Override via env if the
# project's upstream remote is not named "origin".
REMOTE="${GIT_REMOTE_NAME:-origin}"

# --- Parse arguments ---
PROJECT_ARG=""
SKIP_CONFIRM=false
VERSION=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)
      if [[ -z "${2:-}" ]]; then
        echo "ERROR: --project requires a value" >&2
        echo "Usage: $(basename "$0") [--project <name>] [-y] [VERSION]" >&2
        exit 1
      fi
      PROJECT_ARG="$2"
      shift 2
      ;;
    -y|--yes)
      SKIP_CONFIRM=true
      shift
      ;;
    -*)
      echo "ERROR: Unknown flag: $1" >&2
      echo "Usage: $(basename "$0") [--project <name>] [-y] [VERSION]" >&2
      exit 1
      ;;
    *)
      if [[ -z "$VERSION" ]]; then
        VERSION="$1"
      else
        echo "ERROR: Unexpected argument: $1" >&2
        echo "Usage: $(basename "$0") [--project <name>] [-y] [VERSION]" >&2
        exit 1
      fi
      shift
      ;;
  esac
done

# --- Resolve project context ---
# Resolution order: --project flag > PGAI_PROJECT_NAME env var > error.
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

# --- Resolve MAIN_BRANCH via pp_prefix_branch ---
# For projects with branch_prefix=ai_, MAIN_BRANCH=ai_main.
# For projects with no branch_prefix, MAIN_BRANCH=main unchanged.
MAIN_BRANCH="$(pp_prefix_branch "$PROJECT_NAME" "main")"

# --- Read push_to_remote flag via pp_push_to_remote helper ---
# Default: 'true' — pushes main branch and tag to origin (existing behavior preserved).
# Set [project] push_to_remote = false in project.cfg to complete the full local
# finalize-release operation without any git push origin calls.
_CM_PUSH_TO_REMOTE="$(KANBAN_ROOT="$KANBAN_ROOT" pp_push_to_remote "$PROJECT_NAME")"
if [[ "$_CM_PUSH_TO_REMOTE" == "true" ]]; then
  echo "[cm-finalize-release] Push policy: push_to_remote=true — $MAIN_BRANCH and tag will be pushed to origin."
else
  echo "[cm-finalize-release] Push policy: push_to_remote=false — $MAIN_BRANCH and tag stay local. Operator must push manually."
fi

# --- Resolve version from most recent tag on $MAIN_BRANCH if not provided ---
if [[ -z "$VERSION" ]]; then
  echo "No version specified; reading most recent tag on ${MAIN_BRANCH}..."
  # Ensure we are on $MAIN_BRANCH or can read its tip
  CURRENT_BRANCH="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  if [[ "$CURRENT_BRANCH" != "$MAIN_BRANCH" ]]; then
    echo "  Current branch is '$CURRENT_BRANCH'; reading tag from origin/${MAIN_BRANCH}..."
    VERSION="$(git -C "$REPO_ROOT" describe --tags --abbrev=0 "${REMOTE}/${MAIN_BRANCH}" 2>/dev/null)" || true
    if [[ -z "$VERSION" ]]; then
      # Try local $MAIN_BRANCH ref directly
      VERSION="$(git -C "$REPO_ROOT" describe --tags --abbrev=0 "$MAIN_BRANCH" 2>/dev/null)" || true
    fi
  else
    # HEAD is on $MAIN_BRANCH: describe --tags --abbrev=0 HEAD is safe here.
    # This code path only runs when CURRENT_BRANCH == MAIN_BRANCH (the if-branch
    # above handles the non-main case by targeting origin/$MAIN_BRANCH or
    # $MAIN_BRANCH directly).  On main, HEAD is the latest merged commit, so
    # describe walks back to the most recent tag on main — which is exactly the
    # released tag.  This is NOT the branch-sensitive resolver pattern:
    # upgrade.sh's bug was that it used HEAD while on develop, where describe
    # finds an older tag.  Here, HEAD is already constrained to $MAIN_BRANCH.
    # (Assessed in CODER-20260630-003-repoint-upgrade-version-stamp; no follow-up needed.)
    VERSION="$(git -C "$REPO_ROOT" describe --tags --abbrev=0 HEAD 2>/dev/null)" || true
  fi
  if [[ -z "$VERSION" ]]; then
    echo "ERROR: Could not determine version from tags on ${MAIN_BRANCH}." >&2
    echo "Pass the version explicitly: cm-finalize-release.sh <VERSION>" >&2
    exit 1
  fi
  echo "  Resolved version: $VERSION"
fi

# --- Verify the tag exists locally ---
if ! git -C "$REPO_ROOT" rev-parse --verify "refs/tags/$VERSION" >/dev/null 2>&1; then
  echo "ERROR: Tag '$VERSION' does not exist locally." >&2
  echo "Run cm-release.sh first to create the tag, then re-run this script." >&2
  exit 1
fi

# --- Verify $MAIN_BRANCH is checked out (for push to work cleanly) ---
CURRENT_BRANCH="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
if [[ "$CURRENT_BRANCH" != "$MAIN_BRANCH" ]]; then
  echo "WARNING: Current branch is '$CURRENT_BRANCH', not ${MAIN_BRANCH}." >&2
  echo "The push will push ${REMOTE} ${MAIN_BRANCH} from wherever ${MAIN_BRANCH}'s local ref points." >&2
  echo "Consider running: git checkout ${MAIN_BRANCH}" >&2
fi

# --- Confirmation prompt ---
if [[ "$SKIP_CONFIRM" == false ]]; then
  echo ""
  echo "About to push to ${REMOTE}:"
  echo "  git push ${REMOTE} ${MAIN_BRANCH}"
  echo "  git push ${REMOTE} $VERSION"
  echo ""
  printf "Proceed? [y/N] "
  read -r REPLY
  case "$REPLY" in
    [yY][eE][sS]|[yY]) ;;
    *)
      echo "Aborted. Nothing was pushed."
      exit 0
      ;;
  esac
fi

# --- Compute bare version (strips branch_prefix, e.g. ai_v0.31.0 -> v0.31.0) ---
# Release-notes files are always named with the bare semver (v0.X.Y.md), regardless
# of whether the tag was created with a prefix in hybrid-mode installs.
_BARE_VERSION="$(pp_strip_prefix_from_tag "$PROJECT_NAME" "$VERSION")"

# --- Step 2b: Commit WRITER polish of release notes if present ---
# After cm-release.sh ships, a WRITER polish task may update release-notes/<VERSION>.md
# on disk (local main branch) without committing it. Detect any uncommitted changes
# to that specific file and create a follow-up commit so the polish reaches origin.
#
# This step also covers the case where CM already committed augmentation content
# directly (so the file is clean in the working tree but the commit is stranded
# on local main, unpushed): Step 2b is a no-op here — but the unconditional push
# in Step 3 carries the stranded commit to origin regardless.
# No special handling is needed: Step 3 always pushes all of $MAIN_BRANCH.
#
# Four cases:
#   - File modified (uncommitted working-tree changes)  → commit, then push in Step 3
#   - File already committed but not yet pushed         → Step 2b no-op; Step 3 pushes it
#   - File unchanged from the stub (no diff)            → no-op (stub is the record)
#   - File missing entirely                             → log warning, continue
POLISH_NOTES_FILE="$REPO_ROOT/release-notes/${_BARE_VERSION}.md"
POLISH_COMMIT_STATUS="skipped (no polish detected)"

if [[ ! -f "$POLISH_NOTES_FILE" ]]; then
  echo "WARNING: [Step 2b] release-notes/${_BARE_VERSION}.md not found; WRITER polish commit skipped." >&2
  POLISH_COMMIT_STATUS="skipped (file missing)"
else
  # Capture git status for the specific file only.
  # --porcelain output is non-empty when the file has staged or unstaged changes,
  # or when it is untracked. Empty output means the working tree matches HEAD.
  POLISH_PORCELAIN="$(git -C "$REPO_ROOT" status --porcelain "release-notes/${_BARE_VERSION}.md" 2>/dev/null || true)"
  if [[ -n "$POLISH_PORCELAIN" ]]; then
    echo ""
    echo "[Step 2b] Detected uncommitted WRITER polish for ${VERSION}; committing..."
    git -C "$REPO_ROOT" add "release-notes/${_BARE_VERSION}.md"
    git -C "$REPO_ROOT" commit -m "Polish release notes for ${VERSION}"
    POLISH_COMMIT_STATUS="committed"
    echo "  [Step 2b] Polish commit created."
  else
    echo "[Step 2b] release-notes/${_BARE_VERSION}.md unchanged from stub; no polish commit needed."
    POLISH_COMMIT_STATUS="skipped (file unchanged)"
  fi
fi

# --- Step 3: git push origin $MAIN_BRANCH ---
# Gated by push_to_remote flag: pushes all commits on $MAIN_BRANCH to $REMOTE
# (including any polish commit from Step 2b and any stranded augmentation
# commits) when push_to_remote=true; skipped otherwise.
echo ""
if [[ "$_CM_PUSH_TO_REMOTE" == "true" ]]; then
  echo "[Step 3] Pushing ${MAIN_BRANCH} to ${REMOTE}..."
  git -C "$REPO_ROOT" push "${REMOTE}" "$MAIN_BRANCH"
  echo "  Done."
else
  echo "[push_to_remote=false] skipping origin push for ${PROJECT_NAME}: git push ${REMOTE} $MAIN_BRANCH (Step 3)"
fi

# --- Step 4: git push origin <VERSION> ---
# Gated by push_to_remote flag: skipped when push_to_remote=false.
if [[ "$_CM_PUSH_TO_REMOTE" == "true" ]]; then
  echo "[Step 4] Pushing tag $VERSION to ${REMOTE}..."
  git -C "$REPO_ROOT" push "${REMOTE}" "$VERSION"
  echo "  Done."
else
  echo "[push_to_remote=false] skipping origin push for ${PROJECT_NAME}: git push ${REMOTE} $VERSION (Step 4)"
fi

# --- Step 5: Optionally create GitHub release ---
GH_RELEASE_STATUS="skipped"
# _BARE_VERSION already set above; release-notes files always use bare semver name
RELEASE_NOTES_FILE="$REPO_ROOT/release-notes/${_BARE_VERSION}.md"

if command -v gh >/dev/null 2>&1; then
  if gh auth status >/dev/null 2>&1; then
    echo "[Step 5] Creating GitHub release for $VERSION..."
    if [[ -f "$RELEASE_NOTES_FILE" ]]; then
      if gh release create "$VERSION" \
           --title "Release $VERSION" \
           --notes-file "$RELEASE_NOTES_FILE" \
           --repo "$(git -C "$REPO_ROOT" remote get-url "${REMOTE}" 2>/dev/null || true)" \
           2>&1; then
        GH_RELEASE_STATUS="created"
        echo "  GitHub release created for $VERSION."
      else
        GH_RELEASE_STATUS="failed"
        echo "  WARNING: gh release create failed. You may need to create the GitHub release manually." >&2
      fi
    else
      if gh release create "$VERSION" \
           --title "Release $VERSION" \
           --generate-notes \
           2>&1; then
        GH_RELEASE_STATUS="created (auto-generated notes)"
        echo "  GitHub release created for $VERSION."
      else
        GH_RELEASE_STATUS="failed"
        echo "  WARNING: gh release create failed. You may need to create the GitHub release manually." >&2
      fi
    fi
  else
    GH_RELEASE_STATUS="skipped (gh not authenticated)"
    echo "[Step 5] gh CLI not authenticated; skipping GitHub release creation."
  fi
else
  GH_RELEASE_STATUS="skipped (gh CLI not found)"
  echo "[Step 5] gh CLI not found; skipping GitHub release creation."
fi

# --- Summary ---
echo ""
echo "Finalize complete."
echo "  Version:        $VERSION"
echo "  Polish commit:  $POLISH_COMMIT_STATUS"
if [[ "$_CM_PUSH_TO_REMOTE" == "true" ]]; then
  echo "  ${MAIN_BRANCH} pushed: yes"
  echo "  tag pushed:     yes"
else
  echo "  ${MAIN_BRANCH} pushed: skipped (push_to_remote=false)"
  echo "  tag pushed:     skipped (push_to_remote=false)"
fi
echo "  GitHub release: $GH_RELEASE_STATUS"
echo ""
if [[ "$_CM_PUSH_TO_REMOTE" == "true" ]]; then
  echo "The release is live on ${REMOTE}. Downstream consumers can now pull."
else
  echo "The release is complete locally. Operator must push to ${REMOTE} manually when ready."
fi
exit 0
