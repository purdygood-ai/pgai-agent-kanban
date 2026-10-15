#!/usr/bin/env bash
# team/scripts/cm/finalize-release.sh
# Operator convenience script: commits any WRITER polish to release notes, then
# pushes main and the release tag to origin (or the configured REMOTE).
#
# Usage:
#   cm/finalize-release.sh [--project <name>] [-y] [VERSION] [--help]
#
#   --project <name>   explicit project name (overrides PGAI_PROJECT_NAME env var)
#   -y, --yes          Skip the confirmation prompt (non-interactive / CI mode).
#   VERSION            The version tag to push (e.g. v0.17.1). If omitted, the
#                      script reads the most recent tag reachable from HEAD on main.
#   --help, -h         print full usage and exit 0
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
#   0. Decide the push policy through the shared resolver (pp_push_policy):
#      only a project registered in projects.cfg whose own project.cfg names
#      dev_tree_path may push. An unresolved project logs one line
#        [cm-finalize-release] skipped-unresolved-project: project '<name>': missing <piece>: ...
#      and then runs exactly like push_to_remote=false (Steps 3-5 skipped, no
#      remote contacted). REPO_ROOT or PGAI_DEV_TREE_PATH in the environment
#      never makes a project pushable.
#   1. Resolve the version (from $1 or from the most recent tag on main)
#   2. Confirm the push with the operator (skip with -y)
#   2b. Safety check: if release-notes/<VERSION>.md has uncommitted WRITER polish
#       that arrived after cm/release.sh ran, commit both the polish AND a fresh
#       CHANGELOG.md regeneration together so the CHANGELOG.md on the tagged
#       commit matches the release notes the tag carries, then move the not-yet-published release tag onto that commit so the tag
#       carries the polished notes. It refuses, committing nothing, when HEAD is
#       not the main branch, when the tag is not on main's tip or is already on
#       the remote (moving it would change a published release), or when the
#       CHANGELOG.md render fails (CHANGELOG.md is replaced only after a
#       successful render). In the normal flow cm/release.sh Step 8b handles
#       this before CHANGELOG regeneration; this step is a no-op when the file
#       is unchanged or already committed. If the file is missing, a warning is
#       logged and the step is skipped.
#   3. git push $REMOTE $MAIN_BRANCH  (includes the polish commit if Step 2b ran)
#   4. git push $REMOTE <VERSION>
#   5. Optionally create a GitHub release via gh CLI if available and authenticated.
#      Gated by push_to_remote like Steps 3-4: a GitHub release is a publication,
#      so with push_to_remote=false gh is never invoked and the step is skipped.
#   6. Print a summary
#
# Prerequisites:
#   - PGAI_PROJECT_NAME must be set (or --project passed)
#   - main branch must be checked out (or REPO_ROOT must be set)
#   - The tag must already exist locally (cm/release.sh creates it)
#
# Configuration:
#   PGAI_PROJECT_NAME — project name (required when --project flag not used)
#   REPO_ROOT         — override path to the repository root (normally derived
#                       from the project's project.cfg dev_tree_path)

# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

# --- Source optional config files (BEFORE strict mode) ---
# The kanban bashrc/env may have unset vars, non-zero returns, or interactive
# aliases that would trip strict mode. Source them first.
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
[[ -f "$KANBAN_ROOT/bashrc" ]] && source "$KANBAN_ROOT/bashrc"
[[ -f "$KANBAN_ROOT/env" ]] && source "$KANBAN_ROOT/env"
[[ -f "$HOME/.config/pgai-kanban.cfg" ]] && source "$HOME/.config/pgai-kanban.cfg"

# --- Source project path helpers ---
# shellcheck source=lib/project_paths.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/project_paths.sh"

# --- Source version stamp helper for git_describe_tag_pattern ---
# shellcheck source=lib/version_stamp.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/version_stamp.sh"

# --- Source the shared Python invocation helper ---
# shellcheck source=lib/pp_run_ops.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/pp_run_ops.sh"

# --- Source the same-directory atomic replace (Step 2b's CHANGELOG.md) ---
# shellcheck source=lib/atomic_write.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/atomic_write.sh"

set -euo pipefail

# --- Parse arguments ---
PROJECT_ARG=""
SKIP_CONFIRM=false
VERSION=""

_cm_finalize_release_usage() {
  echo "Usage: $(basename "$0") [--project <name>] [-y] [VERSION] [--help]" >&2
  echo "" >&2
  echo "  --project <name>  project name (required when PGAI_PROJECT_NAME not set)" >&2
  echo "  -y, --yes         skip the confirmation prompt" >&2
  echo "  VERSION           version tag to push (e.g. v0.17.1); auto-detected if omitted" >&2
  echo "  --help, -h        print full usage and exit 0" >&2
}

_cm_finalize_release_help() {
  cat <<HELPTEXT
Usage: $(basename "$0") [--project <name>] [-y] [VERSION] [--help]

Push main and the release tag to the project's git remote (GIT_REMOTE_NAME
when set, otherwise project.cfg [project] git_remote_name, origin when that
is absent or blank); optionally commit WRITER polish to release notes
beforehand, moving the not-yet-published release tag onto the polish
commit (refused when HEAD is not main, the tag is not on main's tip or is
already published, or the CHANGELOG.md render fails). Run from
a regular operator terminal (not inside the Claude agent) so the PreToolUse
hook does not intercept pushes.

Push policy: main, the tag and the GitHub release are published only for a
project registered in projects.cfg whose own project.cfg names
dev_tree_path and does not set push_to_remote = false. Any other project
finalizes locally and contacts no remote; an unresolved one logs a
skipped-unresolved-project line naming the missing piece.

Arguments:
  --project <name>  project name; overrides PGAI_PROJECT_NAME env var
  -y, --yes         skip the interactive confirmation prompt (CI / scripted use)
  VERSION           version tag to push (e.g. v0.17.1); when omitted the script
                    reads the most recent tag reachable from HEAD on main
  --help, -h        print this message and exit 0

Project context resolution (highest to lowest precedence):
  1. --project <name> flag
  2. PGAI_PROJECT_NAME environment variable
  3. FAIL — prints error naming both knobs, exits non-zero

Exit codes:
  0  Push complete (or --help requested, or operator aborted at the prompt)
  1  Missing VERSION, tag not found locally, or git push failure

Example:
  scripts/cm/finalize-release.sh --project my-project v1.5.0
  scripts/cm/finalize-release.sh --project my-project -y

Configuration:
  PGAI_PROJECT_NAME  fallback project name (when --project flag not used)
  REPO_ROOT          override path to the repository root
  GIT_REMOTE_NAME    remote name (default: the project's git_remote_name, else origin)
HELPTEXT
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      _cm_finalize_release_help
      exit 0
      ;;
    --project)
      if [[ -z "${2:-}" ]]; then
        echo "ERROR: --project requires a value" >&2
        echo "" >&2
        _cm_finalize_release_usage
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
      echo "" >&2
      _cm_finalize_release_usage
      exit 1
      ;;
    *)
      if [[ -z "$VERSION" ]]; then
        VERSION="$1"
      else
        echo "ERROR: Unexpected argument: $1" >&2
        echo "" >&2
        _cm_finalize_release_usage
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
  # BLOCK-WORTHY: release finalization cannot resolve which project's
  # main branch/tag to push without an unambiguous project context.
  # CENSUS-ANCHOR: cm-finalize-release-project-required-01
  exit 1
}

# --- Push decision (shared resolver), taken before any git command ---
# pp_push_policy asks pp_push_decision: only a project registered in
# projects.cfg whose own project.cfg names dev_tree_path may push. An
# unresolved project logs one skipped-unresolved-project line here and takes
# the local-only path (_CM_PUSH_TO_REMOTE=false): no push and no gh release.
# A REPO_ROOT or PGAI_DEV_TREE_PATH from the environment never makes a
# project pushable. Decided before REPO_ROOT so the line is logged even when
# the dev tree cannot be resolved below.
pp_push_policy "$PROJECT_NAME" "[cm-finalize-release]"
_CM_PUSH_TO_REMOTE="$PP_PUSH_TO_REMOTE"

# --- Remote name ---
# Every remote operation below names this remote: GIT_REMOTE_NAME from the
# environment when set, otherwise the project's own (pp_git_remote_name:
# [project] git_remote_name, origin when absent or blank; an invalid value
# refuses dual-channel there).
REMOTE="${GIT_REMOTE_NAME:-}"
if [[ -z "$REMOTE" ]]; then
  REMOTE="$(pp_git_remote_name "$PROJECT_NAME")"
fi

# --- Resolve REPO_ROOT from project.cfg dev_tree_path ---
# REPO_ROOT may be overridden by the environment; otherwise derive from project.cfg.
if [[ -z "${REPO_ROOT:-}" ]]; then
  pp_load_config "$PROJECT_NAME" || {
    echo "ERROR: could not load project.cfg for project '$PROJECT_NAME'" >&2
    echo "  Expected: $(pp_project_root "$PROJECT_NAME" 2>/dev/null || echo "<unresolvable>")/project.cfg" >&2
    # BLOCK-WORTHY: an unloadable project.cfg leaves REPO_ROOT unknown --
    # finalize-release cannot push to a dev tree it cannot locate.
    # CENSUS-ANCHOR: cm-finalize-release-config-unloadable-01
    exit 1
  }
  REPO_ROOT="${PP_dev_tree_path:-}"
  if [[ -z "$REPO_ROOT" ]]; then
    echo "ERROR: dev_tree_path is not set in project.cfg for project '$PROJECT_NAME'" >&2
    echo "  Add 'dev_tree_path=<path>' to $(pp_project_root "$PROJECT_NAME")/project.cfg" >&2
    # BLOCK-WORTHY: same reasoning as cm-finalize-release-config-unloadable-01
    # -- a blank dev_tree_path is as unusable as a missing project.cfg.
    # CENSUS-ANCHOR: cm-finalize-release-dev-tree-path-unset-01
    exit 1
  fi
fi

# --- Resolve MAIN_BRANCH via pp_prefix_branch ---
# For projects with branch_prefix=ai_, MAIN_BRANCH=ai_main.
# For projects with no branch_prefix, MAIN_BRANCH=main unchanged.
MAIN_BRANCH="$(pp_prefix_branch "$PROJECT_NAME" "main")"

# --- Resolve branch_prefix for prefix-aware git describe --match pattern ---
# The describe match pattern must reflect the project's tag naming convention
# (e.g. "ai_v[0-9]*" for prefixed lanes) so describe never walks past a
# prefixed tag to a bare v-ancestor.  git_describe_tag_pattern constructs
# the pattern; defaults to "v[0-9]*" when prefix is empty.
_FINALIZE_BRANCH_PREFIX="$(pp_branch_prefix "$PROJECT_NAME" 2>/dev/null || true)"
_FINALIZE_TAG_PATTERN="$(git_describe_tag_pattern "$_FINALIZE_BRANCH_PREFIX")"

# --- Narrate the push policy decided above ---
# A resolved project with no push_to_remote key pushes main and the tag (the
# default); push_to_remote = false completes finalize-release locally without
# any git push to ${REMOTE}. An unresolved project already logged its skip line.
if [[ "$PP_PUSH_DECISION" == "push" ]]; then
  echo "[cm-finalize-release] Push policy: push_to_remote=true — $MAIN_BRANCH and tag will be pushed to ${REMOTE}."
elif [[ "$PP_PUSH_DECISION" == "no-push" ]]; then
  echo "[cm-finalize-release] Push policy: push_to_remote=false — $MAIN_BRANCH and tag stay local. Operator must push manually."
fi

# --- Resolve version from most recent tag on $MAIN_BRANCH if not provided ---
if [[ -z "$VERSION" ]]; then
  echo "No version specified; reading most recent tag on ${MAIN_BRANCH}..."
  # Ensure we are on $MAIN_BRANCH or can read its tip
  CURRENT_BRANCH="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  if [[ "$CURRENT_BRANCH" != "$MAIN_BRANCH" ]]; then
    echo "  Current branch is '$CURRENT_BRANCH'; reading tag from ${REMOTE}/${MAIN_BRANCH}..."
    VERSION="$(git -C "$REPO_ROOT" describe --tags --match "$_FINALIZE_TAG_PATTERN" --abbrev=0 "${REMOTE}/${MAIN_BRANCH}" 2>/dev/null)" || true
    if [[ -z "$VERSION" ]]; then
      # Try local $MAIN_BRANCH ref directly
      VERSION="$(git -C "$REPO_ROOT" describe --tags --match "$_FINALIZE_TAG_PATTERN" --abbrev=0 "$MAIN_BRANCH" 2>/dev/null)" || true
    fi
  else
    # HEAD is on $MAIN_BRANCH: describe with the prefix-aware tag pattern is
    # safe here.  This code path only runs when CURRENT_BRANCH == MAIN_BRANCH
    # (the if-branch above handles the non-main case by targeting
    # origin/$MAIN_BRANCH or $MAIN_BRANCH directly).  On main, HEAD is the
    # latest merged commit, so describe walks back to the most recent release
    # tag matching the project's own tag shape — exactly the released tag.
    # The pattern prevents alias tags (e.g. 'latest') and bare-v ancestors
    # from being picked on prefixed lanes.
    VERSION="$(git -C "$REPO_ROOT" describe --tags --match "$_FINALIZE_TAG_PATTERN" --abbrev=0 HEAD 2>/dev/null)" || true
  fi
  if [[ -z "$VERSION" ]]; then
    echo "ERROR: Could not determine version from tags on ${MAIN_BRANCH}." >&2
    echo "Pass the version explicitly: scripts/cm/finalize-release.sh <VERSION>" >&2
    # BLOCK-WORTHY: finalize-release cannot push a release it cannot name --
    # an unresolvable version is a release-mechanics stop, not a guess.
    # CENSUS-ANCHOR: cm-finalize-release-version-unresolvable-01
    exit 1
  fi
  echo "  Resolved version: $VERSION"
fi

# --- Verify the tag exists locally ---
if ! git -C "$REPO_ROOT" rev-parse --verify "refs/tags/$VERSION" >/dev/null 2>&1; then
  echo "ERROR: Tag '$VERSION' does not exist locally." >&2
  echo "Run scripts/cm/release.sh first to create the tag, then re-run this script." >&2
  # BLOCK-WORTHY: pushing a tag that was never created would push nothing
  # -- refuse rather than silently no-op the release.
  # CENSUS-ANCHOR: cm-finalize-release-tag-missing-01
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

# --- Step 2b: Safety check for uncommitted WRITER polish of release notes ---
# Normally, cm/release.sh Step 8b commits any uncommitted WRITER polish of
# release-notes/<VERSION>.md BEFORE regenerating CHANGELOG.md, so by the time
# this script runs the working tree should be clean and CHANGELOG.md is already
# fresh for the tip.
#
# This step guards against the uncommon case where WRITER polish arrived AFTER
# cm/release.sh ran (unexpected timing): if uncommitted changes to the release-notes
# file are detected here, committing the polish alone would leave the tagged
# CHANGELOG.md behind the release notes the tag carries. Instead, both the polish
# AND a fresh CHANGELOG.md regeneration are committed together so the two match
# on the tagged commit.
#
# The case where CM already committed the polish (file is clean, commit is stranded
# on local main, unpushed) is still handled: Step 2b is a no-op, and the
# unconditional push in Step 3 carries the stranded commit to origin.
#
# Four cases:
#   - File modified (uncommitted working-tree changes after cm/release.sh) →
#       commit polish + regenerate and commit CHANGELOG.md (so it matches the tagged notes)
#   - File already committed but not yet pushed  → Step 2b no-op; Step 3 pushes it
#   - File unchanged from the committed state (no diff) → no-op
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
    echo "[Step 2b] Detected uncommitted WRITER polish for ${VERSION} after scripts/cm/release.sh."
    _polish_patch="${PGAI_AGENT_KANBAN_TEMP_DIR:-/tmp/pgai_kanban_tmp}/${PROJECT_NAME}-${_BARE_VERSION}-notes-polish.patch"
    _polish_set_aside="git -C '${REPO_ROOT}' diff -- 'release-notes/${_BARE_VERSION}.md' > '${_polish_patch}' && git -C '${REPO_ROOT}' checkout -- 'release-notes/${_BARE_VERSION}.md' && scripts/cm/finalize-release.sh --project ${PROJECT_NAME} ${VERSION}"

    _polish_branch="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
    if [[ "$_polish_branch" != "$MAIN_BRANCH" ]]; then
      # BLOCK-WORTHY (docs/blocking-site-census.md): the polish commit would
      # land on '${_polish_branch}' while Step 3 pushes ${MAIN_BRANCH}, so the
      # release would ship without it -- refused before anything is staged.
      pgai_emit_error_contract "cm-finalize-release.polish-off-main" \
        "Step 2b: WRITER polish for ${VERSION} is pending but HEAD in ${REPO_ROOT} is '${_polish_branch}', not ${MAIN_BRANCH}; committing it here would leave the release without it. Nothing was staged or committed." \
        "git -C '${REPO_ROOT}' checkout ${MAIN_BRANCH} && scripts/cm/finalize-release.sh --project ${PROJECT_NAME} ${VERSION}" \
        "$PROJECT_NAME"
      # CENSUS-ANCHOR: cm-finalize-release-polish-off-main-01
      exit 1
    fi

    # The tag must be able to follow the polish: it has to sit on main's tip
    # (so moving it adds only the polish commit) and must not be published
    # yet. A local-only project (push_to_remote=false) contacts no remote.
    _polish_tag_sha="$(git -C "$REPO_ROOT" rev-parse "refs/tags/${VERSION}^{commit}")"
    _polish_head_sha="$(git -C "$REPO_ROOT" rev-parse HEAD)"
    _polish_tag_problem=""
    if [[ "$_polish_tag_sha" != "$_polish_head_sha" ]]; then
      _polish_tag_problem="tag ${VERSION} is on $(echo "$_polish_tag_sha" | cut -c1-7), not ${MAIN_BRANCH}'s tip $(echo "$_polish_head_sha" | cut -c1-7)"
    elif [[ "$_CM_PUSH_TO_REMOTE" == "true" ]] \
        && git -C "$REPO_ROOT" ls-remote --exit-code --tags "$REMOTE" "refs/tags/${VERSION}" >/dev/null 2>&1; then
      _polish_tag_problem="tag ${VERSION} is already published on ${REMOTE}"
    fi
    if [[ -n "$_polish_tag_problem" ]]; then
      # BLOCK-WORTHY (docs/blocking-site-census.md): committing the polish
      # would leave the release tag behind its own notes, and moving a tag
      # that is not on main's tip or is already published would change what
      # the release contains -- refused before anything is staged.
      pgai_emit_error_contract "cm-finalize-release.polish-would-strand-tag" \
        "Step 2b: WRITER polish for ${VERSION} is pending but ${_polish_tag_problem}, so the tag cannot be moved onto a polish commit. Nothing was staged or committed; polish belongs before scripts/cm/release.sh, or in the next release." \
        "${_polish_set_aside}" \
        "$PROJECT_NAME"
      # CENSUS-ANCHOR: cm-finalize-release-polish-would-strand-tag-01
      exit 1
    fi

    echo "  Committing polish and regenerating CHANGELOG.md together so the tagged CHANGELOG.md matches the polished notes..."
    # Regenerate CHANGELOG.md from the polished notes so the committed artifact
    # matches a fresh regeneration.  The render is hash-seed-independent (the
    # writer sorts its heading sets); PYTHONHASHSEED=0 is kept as defense in
    # depth, as in cm/release.sh Step 11b. The render goes
    # to a scratch file beside CHANGELOG.md and replaces it only when the
    # writer succeeds; the notes are staged only after that.
    _cl_bugs_dir="${KANBAN_ROOT}/projects/${PROJECT_NAME}/bugs"
    _finalize_render_changelog() {
      PYTHONHASHSEED=0 pp_run_ops pgai_agent_kanban.cm.changelog_writer "$REPO_ROOT" "$_cl_bugs_dir"
    }
    if ! pgai_atomic_write "$REPO_ROOT/CHANGELOG.md" _finalize_render_changelog; then
      # BLOCK-WORTHY (docs/blocking-site-census.md): committing the polish
      # without a matching CHANGELOG.md would tag a CHANGELOG.md that does not
      # match the release notes the tag carries; the old CHANGELOG.md is left
      # byte-for-byte and nothing is committed.
      pgai_emit_error_contract "cm-finalize-release.changelog-render-failed" \
        "Step 2b: regenerating CHANGELOG.md for ${VERSION} failed (the writer's own message is above); CHANGELOG.md is unchanged and the polish was not committed." \
        "PYTHONHASHSEED=0 scripts/lib/pp_run_ops.sh pgai_agent_kanban.cm.changelog_writer '${REPO_ROOT}' '${_cl_bugs_dir}' > /dev/null && scripts/cm/finalize-release.sh --project ${PROJECT_NAME} ${VERSION}" \
        "$PROJECT_NAME"
      # CENSUS-ANCHOR: cm-finalize-release-changelog-render-failed-01
      exit 1
    fi
    git -C "$REPO_ROOT" add "release-notes/${_BARE_VERSION}.md" "CHANGELOG.md"
    git -C "$REPO_ROOT" commit -m "Polish release notes for ${VERSION}"
    git -C "$REPO_ROOT" tag -f "$VERSION" HEAD >/dev/null
    POLISH_COMMIT_STATUS="committed (with CHANGELOG.md regeneration; tag ${VERSION} moved onto it)"
    echo "  [Step 2b] Polish commit created (release-notes + CHANGELOG.md); local tag ${VERSION} moved onto it ($(git -C "$REPO_ROOT" rev-parse --short HEAD))."
  else
    echo "[Step 2b] release-notes/${_BARE_VERSION}.md unchanged; no polish commit needed."
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
  echo "[push_to_remote=false] skipping ${REMOTE} push for ${PROJECT_NAME}: git push ${REMOTE} $MAIN_BRANCH (Step 3)"
fi

# --- Step 4: git push origin <VERSION> ---
# Gated by push_to_remote flag: skipped when push_to_remote=false.
if [[ "$_CM_PUSH_TO_REMOTE" == "true" ]]; then
  echo "[Step 4] Pushing tag $VERSION to ${REMOTE}..."
  git -C "$REPO_ROOT" push "${REMOTE}" "$VERSION"
  echo "  Done."
else
  echo "[push_to_remote=false] skipping ${REMOTE} push for ${PROJECT_NAME}: git push ${REMOTE} $VERSION (Step 4)"
fi

# --- Step 5: Optionally create GitHub release ---
# Gated by push_to_remote flag, exactly as Steps 3-4: a GitHub release publishes
# the version off-host, so with push_to_remote=false gh is not invoked at all
# (not even `gh auth status`) and the step is skipped with a narration line.
GH_RELEASE_STATUS="skipped"
# _BARE_VERSION already set above; release-notes files always use bare semver name
RELEASE_NOTES_FILE="$REPO_ROOT/release-notes/${_BARE_VERSION}.md"

if [[ "$_CM_PUSH_TO_REMOTE" != "true" ]]; then
  GH_RELEASE_STATUS="skipped (push_to_remote=false)"
  echo "[push_to_remote=false] skipping GitHub release for ${PROJECT_NAME}: gh release create $VERSION (Step 5)"
elif command -v gh >/dev/null 2>&1; then
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
