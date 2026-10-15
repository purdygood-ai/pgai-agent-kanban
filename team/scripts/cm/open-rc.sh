#!/usr/bin/env bash
# team/scripts/cm/open-rc.sh
# Human-invoked or CM-agent-invoked: opens a release candidate branch from main.
#
# Usage:
#   cm/open-rc.sh [--project <name>] <version> [task-id] [--help]
#
# --project <name>   explicit project name (overrides PGAI_PROJECT_NAME env var)
# --help, -h         print full usage and exit 0
# <version>          must be in format vX.Y.Z (e.g. v0.4.0)
# [task-id]          optional: the CM agent task ID that invoked this script.
#                    Recorded as "RC Opened By Task" in release-state.md.
#                    Defaults to "cm/open-rc.sh (manual)" if not provided.
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
#   3a. Decides the push policy through the shared resolver (pp_push_policy):
#       only a project registered in projects.cfg whose own project.cfg names
#       dev_tree_path may push. An unresolved project logs one line
#         [cm-open-rc] skipped-unresolved-project: project '<name>': missing <piece>: ...
#       and then runs exactly like push_to_remote=false: no fetch, ls-remote,
#       pull or push, and no remote contacted. REPO_ROOT or PGAI_DEV_TREE_PATH
#       in the environment never makes a project pushable.
#   3b. Resolves the remote every fetch, ls-remote, pull and push below
#       names: project.cfg [project] git_remote_name, origin when absent or
#       blank (pp_git_remote_name). An invalid value refuses dual-channel
#       (git_remote_name.invalid) and exits 1 before any git command.
#   4. Checks rc/<version> does not already exist locally or on the remote
#      (the remote check, and the version lookup's tag fetch, are skipped
#      when the project does not push), and that the release tag
#      <branch_prefix><version> (e.g. ai_v0.4.0) does not exist locally
#   5. git fetch <remote>                    (skipped when push_to_remote=false, and
#                                              skipped with a narrated line when
#                                              PGAI_AGENT_ROLE names a role other than
#                                              cm -- only CM touches the remote)
#   6. git checkout main && git pull --ff-only    (pull skipped when push_to_remote=false)
#   7. Updates project-scoped release-state.md fields in place (appended blocks kept)
#      Writes the three RC-tracking fields (Active RC, RC Opened At, RC Opened
#      By Task) plus the pre-existing Last Released field, read from the file
#      being overwritten and carried forward unchanged (or "none" if absent).
#      The canonical "what version are we at" answer is still the git tag
#      created by cm/release.sh (via pp_last_released_version); Last Released
#      in release-state.md is a narrower, latching field consumed only by the
#      HALT-AFTER rc:vX.Y.Z drain check.
#   8. git checkout -b rc/<version>
#   9. git push -u <remote> rc/<version>
#  10. git checkout main
#  11. Prints success message and exits 0
#
# Release-state.md is the project-scoped live install file only:
#   $KANBAN_ROOT/projects/<name>/release-state.md
# The dev tree's team/release-state.md is NOT read or written by this script.
#
# On any failure mid-sequence, the rollback_on_exit trap attempts rollback:
#   - Deletes rc/<version> from the remote if it was pushed
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

_cm_open_rc_usage() {
  echo "Usage: $(basename "$0") [--project <name>] <version> [task-id] [--help]" >&2
  echo "" >&2
  echo "  --project <name>  project name (required when PGAI_PROJECT_NAME not set)" >&2
  echo "  <version>         RC version to open (required; format: vX.Y.Z)" >&2
  echo "  [task-id]         CM agent task ID logged in release-state.md (optional)" >&2
  echo "  --help, -h        print full usage and exit 0" >&2
}

_cm_open_rc_help() {
  cat <<HELPTEXT
Usage: $(basename "$0") [--project <name>] <version> [task-id] [--help]

Open a release candidate branch from the project main branch.

Push policy: the rc branch is pushed to the project's remote -- project.cfg
[project] git_remote_name, origin when absent or blank -- only for a project
registered in projects.cfg whose own project.cfg names dev_tree_path and
does not set push_to_remote = false. A git_remote_name that is not a valid
remote name refuses (git_remote_name.invalid) before any git command. Any other project opens the RC
locally and contacts no remote; an unresolved one logs a
skipped-unresolved-project line naming the missing piece.

Arguments:
  --project <name>  project name; overrides PGAI_PROJECT_NAME env var
  <version>         RC version to open (required; format: vX.Y.Z, e.g. v0.4.0)
  [task-id]         CM agent task ID recorded as "RC Opened By Task" in
                    release-state.md (default: scripts/cm/open-rc.sh (manual))
  --help, -h        print this message and exit 0

Project context resolution (highest to lowest precedence):
  1. --project <name> flag
  2. PGAI_PROJECT_NAME environment variable
  3. FAIL — prints error naming both knobs, exits non-zero

Exit codes:
  0  RC branch opened successfully (or --help requested)
  1  Validation failure, pre-condition error, or mid-sequence git error
     (rollback attempted on non-zero exit)

Example:
  scripts/cm/open-rc.sh --project my-project v1.5.0
  scripts/cm/open-rc.sh --project my-project v1.5.0 CM-YYYYMMDD-NNN-open-rc

Configuration:
  PGAI_PROJECT_NAME  fallback project name (when --project flag not used)
  REPO_ROOT          override path to the repository root
HELPTEXT
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      _cm_open_rc_help
      exit 0
      ;;
    --project)
      if [[ -z "${2:-}" ]]; then
        echo "ERROR: --project requires a value" >&2
        echo "" >&2
        _cm_open_rc_usage
        exit 1
      fi
      PROJECT_ARG="$2"
      shift 2
      ;;
    --*)
      echo "ERROR: unknown flag: $1" >&2
      echo "" >&2
      _cm_open_rc_usage
      exit 1
      ;;
    *)
      _POSITIONAL+=("$1")
      shift
      ;;
  esac
done

VERSION="${_POSITIONAL[0]:-}"
TASK_ID="${_POSITIONAL[1]:-cm/open-rc.sh (manual)}"

if [[ -z "$VERSION" ]]; then
  echo "ERROR: missing required argument <version>" >&2
  echo "" >&2
  _cm_open_rc_usage
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

# --- Source pp_run_ops (JSON field reads of the per-RC state records) ---
# shellcheck source=lib/pp_run_ops.sh
declare -F pp_run_ops >/dev/null || source "$(dirname "${BASH_SOURCE[0]}")/../lib/pp_run_ops.sh"

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

# --- Push decision (shared resolver), taken before any git command ---
# pp_push_policy asks pp_push_decision: only a project registered in
# projects.cfg whose own project.cfg names dev_tree_path may push. An
# unresolved project logs one skipped-unresolved-project line here and runs
# the local-only path (_CM_PUSH_TO_REMOTE=false): no fetch, no ls-remote, no
# push. A REPO_ROOT or PGAI_DEV_TREE_PATH from the environment never makes a
# project pushable. Decided before REPO_ROOT so the line is logged even when
# the dev tree cannot be resolved below.
pp_push_policy "$PROJECT_NAME" "[cm-open-rc]"
_CM_PUSH_TO_REMOTE="$PP_PUSH_TO_REMOTE"

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

# --- Resolve the remote every git network step below names ---
# project.cfg [project] git_remote_name, origin when absent or blank. Resolved
# on the local-only path too, so its narration names the configured remote.
if ! GIT_REMOTE="$(pp_git_remote_name "$PROJECT_NAME")"; then
  # BLOCK-WORTHY (docs/blocking-site-census.md): a git_remote_name that
  # cannot name a remote would point the fetch, the RC-branch check and the
  # push at a destination nobody configured; pp_git_remote_name has printed
  # the dual-channel block naming the key and the file.
  # CENSUS-ANCHOR: open-rc-git-remote-name-01
  exit 1
fi

# --- Narrate the push policy decided above ---
# A resolved project with no push_to_remote key pushes the rc branch (the
# default); push_to_remote = false completes the open-rc locally without any
# git push to the remote. An unresolved project already logged its skip line.
if [[ "$PP_PUSH_DECISION" == "push" ]]; then
  echo "[cm-open-rc] Push policy: push_to_remote=true — rc branch will be pushed to ${GIT_REMOTE}."
elif [[ "$PP_PUSH_DECISION" == "no-push" ]]; then
  echo "[cm-open-rc] Push policy: push_to_remote=false — rc branch stays local. Operator must push manually."
fi

# --- Rollback state tracking ---
# These flags are set as the atomic sequence progresses so that
# rollback_on_exit knows how far we got and what to undo.
RC_BRANCH_CREATED=0     # set to 1 after local rc branch created
RC_BRANCH_PUSHED=0      # set to 1 after rc branch pushed to the remote
RELEASE_STATE_WRITTEN=0 # set to 1 after project-scoped release-state.md is written
RELEASE_STATE_ORIGINAL="" # populated with original content before we overwrite

# --- Cleanup / rollback on exit ---
# On non-zero exit, rolls back partial state from the atomic sequence.
# Sets Needs Human: yes in a status note if rollback itself fails.
rollback_on_exit() {
  local exit_code=$?
  if [[ $exit_code -ne 0 ]]; then
    echo "" >&2
    echo "--- scripts/cm/open-rc.sh FAILED (exit $exit_code) — attempting rollback ---" >&2

    # Rollback: delete rc branch from the remote if it was pushed
    if [[ $RC_BRANCH_PUSHED -eq 1 ]]; then
      echo "  Rolling back: deleting $RC_BRANCH from ${GIT_REMOTE}..." >&2
      if git -C "$REPO_ROOT" push "$GIT_REMOTE" --delete "$RC_BRANCH" 2>/dev/null; then
        echo "  Rolled back: $RC_BRANCH deleted from ${GIT_REMOTE}." >&2
      else
        echo "  WARNING: could not delete $RC_BRANCH from ${GIT_REMOTE}. Needs Human: yes" >&2
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
trap rollback_on_exit EXIT

# --- Paths ---
# Canonical release state file: project-scoped live install path.
# This is the ONLY file cm/open-rc.sh reads from and writes to.
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
# read_field <field_heading>: the value under "## <field_heading>" in
# RELEASE_STATE, or "none" (lib/cm/common.sh).
# shellcheck source=../lib/cm/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/cm/common.sh"

ACTIVE_RC="$(read_field "Active RC")"

if [[ "$ACTIVE_RC" != "none" ]]; then
  echo "ERROR: an active RC already exists: $ACTIVE_RC" >&2
  echo "Close the existing RC before opening a new one." >&2
  exit 1
fi

# Read the pre-existing Last Released value so the rewrite below can carry it
# forward unchanged. This field is monotonic and latching (roles/CM.md
# "Release state") -- open-rc must never clear or alter it, only preserve it.
# read_field returns "none" when the heading is absent or empty (fresh
# project that has never shipped), which is itself a valid schema value.
# Named distinctly from LAST_RELEASED (Guard 2 below) -- that variable holds
# pp_last_released_version's git-tag-derived answer, a different value used
# only for the version-collision check, not the release-state.md field value.
RELEASE_STATE_LAST_RELEASED="$(read_field "Last Released")"

# --- Check rc/<version> does not already exist locally ---
if git -C "$REPO_ROOT" rev-parse --verify "refs/heads/$RC_BRANCH" >/dev/null 2>&1; then
  echo "ERROR: branch '$RC_BRANCH' already exists locally" >&2
  exit 1
fi

# --- Check rc/<version> does not already exist on the remote ---
# Only a pushing project asks the remote; the local-only path contacts none.
if [[ "$_CM_PUSH_TO_REMOTE" == "true" ]] && \
   git -C "$REPO_ROOT" ls-remote --exit-code --heads "$GIT_REMOTE" "$RC_BRANCH" >/dev/null 2>&1; then
  echo "ERROR: branch '$RC_BRANCH' already exists on ${GIT_REMOTE}" >&2
  exit 1
fi

# --- Guard 1: reject if the release tag for this version already exists ---
# A pre-existing tag means cm/release.sh would fail at the `git tag` step.
# The tag checked is the one the release cuts, pp_prefix_tag's
# <branch_prefix><version> (ai_vX.Y.Z on a prefixed project), so a prefixed
# lane sees its own shipped release and is not stopped by another lane's
# bare vX.Y.Z tag.
RELEASE_TAG="$(pp_prefix_tag "$PROJECT_NAME" "$VERSION")"
if git -C "$REPO_ROOT" rev-parse --verify "refs/tags/$RELEASE_TAG" >/dev/null 2>&1; then
  echo "ERROR: git tag '$RELEASE_TAG' already exists in $REPO_ROOT" >&2
  echo "  Opening an RC at a version that is already tagged would collide with" >&2
  echo "  the existing release. Choose a version that has not yet been tagged." >&2
  exit 1
fi

# --- Guard 2: reject if VERSION is not strictly greater than last released ---
# pp_last_released_version returns the highest semver tag merged into
# <remote>/<prefix>main (the same remote, pp_git_remote_name).
# On the local-only path PGAI_GIT_LOCAL_ONLY skips its best-effort tag fetch,
# so the lookup reads local refs and contacts no remote.
if [[ "$_CM_PUSH_TO_REMOTE" == "true" ]]; then
  LAST_RELEASED="$(pp_last_released_version "$PROJECT_NAME")"
else
  LAST_RELEASED="$(PGAI_GIT_LOCAL_ONLY=1 pp_last_released_version "$PROJECT_NAME")"
fi
if ! semver_gt "$VERSION" "$LAST_RELEASED"; then
  echo "ERROR: version '$VERSION' is not strictly greater than the last released version '$LAST_RELEASED'" >&2
  echo "  The RC version must be greater than the last release to avoid collision." >&2
  echo "  Last released: $LAST_RELEASED" >&2
  echo "  Requested RC:  $VERSION" >&2
  echo "  Choose a version greater than $LAST_RELEASED (e.g. the next patch, minor, or major)." >&2
  exit 1
fi

# --- git fetch (gated by push_to_remote: no remote traffic in local-only mode) ---
# Also skipped, with a narrated line, when PGAI_AGENT_ROLE names a role other
# than cm (pp_agent_role_is_non_cm): CM is the only role that touches the
# remote, and the boundary guard would refuse a non-CM fetch anyway -- running
# it would change nothing and only spend an evidence record. An unset role (an
# operator's own shell) keeps the fetch.
if [[ "$_CM_PUSH_TO_REMOTE" != "true" ]]; then
  echo "push_to_remote=false — skipping git fetch ${GIT_REMOTE} (local-only install)."
elif pp_agent_role_is_non_cm; then
  echo "PGAI_AGENT_ROLE=${PGAI_AGENT_ROLE} — skipping git fetch ${GIT_REMOTE} (only the cm role touches the remote; local refs are used as-is)."
else
  echo "Fetching ${GIT_REMOTE}..."
  git -C "$REPO_ROOT" fetch "$GIT_REMOTE"
fi

# --- Checkout main and pull ---
# Gated by push_to_remote flag: the ff-only pull from the remote is skipped
# when push_to_remote=false ($GIT_REMOTE/$MAIN_BRANCH need not exist).  The checkout
# still runs — the rc branch is cut from MAIN_BRANCH as current HEAD.
echo "Checking out $MAIN_BRANCH and pulling..."
git -C "$REPO_ROOT" checkout "$MAIN_BRANCH"
if [[ "$_CM_PUSH_TO_REMOTE" == "true" ]]; then
  git -C "$REPO_ROOT" merge --ff-only "${GIT_REMOTE}/$MAIN_BRANCH"
else
  echo "push_to_remote=false — skipping ff-only pull from ${GIT_REMOTE}/$MAIN_BRANCH (local-only install)."
fi

# --- Update project-scoped release-state.md fields in place ---
# The shared field writer (write_rc_state.py release-state) changes only the
# four schema fields and keeps every other line -- including appended HALT,
# override, campaign-spend and decision blocks -- byte-for-byte, writing
# atomically. Written directly to the live install path.
# The three RC-tracking fields are refreshed for the new RC; Last Released is
# carried forward unchanged from RELEASE_STATE_LAST_RELEASED (read above) --
# open-rc never computes or fabricates that value, it only preserves whatever
# was already on disk (an absent heading is written as 'none').
# The dev tree's team/release-state.md is NOT modified.
OPENED_AT="$(date -Iseconds)"

echo "Updating project-scoped release-state.md fields in place..."
python3 "$KANBAN_ROOT/pgai_agent_kanban/cm/write_rc_state.py" release-state "$RELEASE_STATE" \
  --active-rc "$VERSION" \
  --rc-opened-at "$OPENED_AT" \
  --rc-opened-by-task "$TASK_ID" \
  --last-released "$RELEASE_STATE_LAST_RELEASED"
RELEASE_STATE_WRITTEN=1

# --- Atomic sequence: create rc branch and push ---
# Rollback flags are set as we progress so rollback_on_exit can undo partial work.
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
# Schema: { "rc", "opened_at", "closed_at", "outcome", "attempt", "family_key", "cargo" }
# attempt/family_key/cargo are the retry-counter fields — see
# pgai_agent_kanban/cm/bundle_family.py for how they are computed.
# All timestamps are ISO8601 UTC (date -u +%Y-%m-%dT%H:%M:%SZ).
RC_STATE_DIR="$(pp_project_root "$PROJECT_NAME")/release-state"
RC_STATE_JSON="${RC_STATE_DIR}/${VERSION}.json"
OPENED_AT_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
mkdir -p "$RC_STATE_DIR"

# If a prior cancelled JSON exists at this slot, archive it to history/ first
# so the audit trail is preserved and the fresh record does not overwrite it.
# This is the freeing-semantics step: a cancelled version's history record is
# moved to history/ by cancel-rc.sh, but if it wasn't (e.g. cancel ran before
# this fix shipped) we handle it here as well. Idempotent — no-op when absent.
if [[ -f "$RC_STATE_JSON" ]]; then
  # The record's outcome field; empty when absent or the file is not a
  # readable JSON object.
  _prior_outcome="$(pp_run_ops pgai_agent_kanban.lib.json_field --file "$RC_STATE_JSON" outcome 2>/dev/null)" \
    || _prior_outcome=""
  if [[ "$_prior_outcome" == "cancelled" ]]; then
    python3 "$KANBAN_ROOT/pgai_agent_kanban/cm/write_rc_state.py" archive-cancelled \
      "$RC_STATE_JSON" "$VERSION" "$OPENED_AT_UTC" 2>&1 || \
      echo "  WARNING: could not archive prior cancelled JSON at $RC_STATE_JSON" >&2
  elif [[ "$_prior_outcome" == "in_progress" ]]; then
    # A live in_progress JSON already exists — this should not happen because
    # open-rc blocks on Active RC != none. Warn and overwrite so recovery runs proceed.
    echo "  WARNING: stale in_progress JSON found at $RC_STATE_JSON; overwriting (recovery run)." >&2
  fi
fi

# --- Compute bundle family + retry attempt (the retry counter) ---
# Locates the requirements bundle for $VERSION (same glob CM.md documents for
# the Step 16b promotion / release-notes generator: <version>-*.md under
# requirements/) and derives a family key from its sorted cargo set (bundled
# item basenames). Family identity survives cancel -> rebundle: the same
# cargo set coming back under a new patch number is attempt N+1, not a fresh
# attempt 1. A bundle with no ## Bundled Items (or no bundle at all, e.g. an
# operator-authored requirements doc) has no family to track and always
# starts at attempt 1 — the "happy path never exceeds 1" case.
REQUIREMENTS_DIR="$(pp_requirements_dir "$PROJECT_NAME")"
BUNDLE_FAMILY_JSON="$(python3 "$KANBAN_ROOT/pgai_agent_kanban/cm/bundle_family.py" compute \
  "$REQUIREMENTS_DIR" "$VERSION" "$RC_STATE_DIR")"
RC_ATTEMPT="$(pp_run_ops pgai_agent_kanban.lib.json_field "$BUNDLE_FAMILY_JSON" attempt)"
RC_FAMILY_KEY="$(pp_run_ops pgai_agent_kanban.lib.json_field --null none "$BUNDLE_FAMILY_JSON" family_key)"
RC_CARGO_JSON="$(pp_run_ops pgai_agent_kanban.lib.json_field --json "$BUNDLE_FAMILY_JSON" cargo)"

python3 "$KANBAN_ROOT/pgai_agent_kanban/cm/write_rc_state.py" open-with-attempt \
  "$VERSION" "$OPENED_AT_UTC" "$RC_ATTEMPT" "$RC_FAMILY_KEY" "$RC_CARGO_JSON" > "$RC_STATE_JSON"
echo "  Per-RC release-state JSON written: $RC_STATE_JSON (attempt ${RC_ATTEMPT}, family ${RC_FAMILY_KEY})"

# Push rc branch to the remote (gated by push_to_remote flag)
if [[ "$_CM_PUSH_TO_REMOTE" == "true" ]]; then
  echo "Pushing $RC_BRANCH to ${GIT_REMOTE}..."
  git -C "$REPO_ROOT" push -u "$GIT_REMOTE" "$RC_BRANCH"
  RC_BRANCH_PUSHED=1
else
  echo "[push_to_remote=false] skipping ${GIT_REMOTE} push for ${PROJECT_NAME}: git push -u ${GIT_REMOTE} $RC_BRANCH"
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
