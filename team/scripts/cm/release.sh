#!/usr/bin/env bash
# cm-release.sh
# Human-invoked: closes an active release candidate branch.
#
# Usage:
#   cm-release.sh [--project <name>] [--help]
#
# --project <name>   explicit project name (overrides PGAI_PROJECT_NAME env var)
# --help, -h         print full usage and exit 0
#
# Project context resolution (highest to lowest precedence):
#   1. --project <name> flag
#   2. PGAI_PROJECT_NAME environment variable
#   3. FAIL — prints error naming both knobs, exits non-zero
#
# No positional arguments. Reads Active RC from the project-scoped release-state.md at
# $KANBAN_ROOT/projects/<name>/release-state.md — the canonical live install file.
# Works regardless of which branch is currently checked out.
#
# Behavior (ship sequence):
#   1.  git fetch origin --tags
#   2.  Find active RC branch (rc/*) and validate it exists
#   3.  Validate rc/<ACTIVE_RC> exists locally and on origin; re-push if origin absent
#       but local ref present (resume from interrupted-before-Step-13 state)
#   4.  Refuse if ACTIVE_RC == none
#   4c. Check last-3-RCs-NON-FUNCTIONAL pattern (HALT if triggered)
#   4d. Read TESTER report; apply ship-policy decision matrix (HALT if required)
#   4e. Stamp release-notes ## Status placeholder (PENDING-RELEASE) with ship decision
#       (if WRITER-authored notes exist on the RC branch; no-op if absent)
#       HALT if placeholder survives after stamp, or no recognizable Status line found.
#   4f. Squash pollution guard: inspect RC→main diff for stray non-source paths
#       (repo-root artifacts/, misplaced PRIORITY-*.md, misplaced requirements v*.md,
#       task-ID dirs outside projects/<name>/tasks/).
#       Warns loudly; halts if CM_SQUASH_STRICT_POLLUTION_GUARD=true.
#   5.  git checkout main && git pull --ff-only
#   6.  git merge --squash rc/<ACTIVE_RC>
#   7.  git commit -m "Release <ACTIVE_RC>"
#   7a. Fidelity gate: git diff --quiet rc/<ACTIVE_RC> main
#       HALT before release-notes commit and tag if trees diverge.
#   8.  Generate release-notes/<ACTIVE_RC>.md from RC branch commits; commit on main
#       (skipped when WRITER-authored notes were stamped in Step 4e and are already in HEAD)
#       Auto-generated notes include ## Status field from ship-policy recommendation.
#   8b. Commit any uncommitted WRITER polish of release-notes/<ACTIVE_RC>.md on main
#       (no-op when file is unchanged; must run BEFORE Step 11b so changelog_writer
#       reads the polished notes — keeps the freshness gate green on the tip).
#   11b. Regenerate CHANGELOG.md and commit on main.
#   11d. Write bare release version to $repo_root/VERSION and commit on main.
#        Byte-compare idempotency: no-op when VERSION already matches.
#        Pre-tag gate (Step 13) asserts VERSION == release tag before tagging.
#   NOTE: main is staged locally; auto-push is attempted at Step 18 (best-effort)
#   9.  git push origin --delete rc/<ACTIVE_RC>
#   10. git branch -D rc/<ACTIVE_RC>
#   11. Update project-scoped release-state.md (live install) — Active RC cleared
#       Last Released* fields are NOT written; the git tag is the canonical record.
#   12. git tag <ACTIVE_RC>  ← tag points to the final housekeeping commit on main
#       After this tag exists, pp_last_released_version returns <ACTIVE_RC>.
#   NOTE: tag auto-pushed at Step 18 (best-effort); operator may push manually if needed
#   12b. Promote bundled items from 'running' to 'done'
#   13. Best-effort auto-push of main and tags to origin
#       Push failures are non-fatal — script always exits 0 if release shipped locally
#   14. Print "Next Recommended Step" block and exit 0
#
# HALT triggers (create $KANBAN_ROOT/HALT and exit 1):
#   - TESTER state is BLOCKED (verification could not complete)
#   - TESTER systemic_risk is high
#   - Any finding Fix Effort=large in a SHIP-WITH-SERIOUS-CONCERNS context
#   - Pre-squash hook fails
#   - Squash pollution guard fires in strict mode (CM_SQUASH_STRICT_POLLUTION_GUARD=true)
#   - Squash to main has conflicts
#   - Fidelity gate fires (rc/<version> tree diverges from main after squash)
#   - Push to origin fails after retries (checked in Step 18)
#   - Tag already exists on remote
#   - Last 3 consecutive RCs for this project were all marked NON-FUNCTIONAL
#
# release-state.md is the project-scoped live install file only:
#   $KANBAN_ROOT/projects/<name>/release-state.md
# The dev tree's team/release-state.md is NOT read or written by this script.
#
# Operator handoff: after this script exits, the operator runs cm-finalize-release.sh
# (or manually runs: git push origin main && git push origin <VERSION>) to push main
# and the tag to origin. The GitHub release can also be created at that point.
#
# Tag ordering: the tag is created AFTER the squash commits on main so that
# `git describe --tags` on main returns the clean tag (e.g. v0.17.1) with
# no trailing commit offset.
#
# Safety: release-state.md is ONLY updated after all git operations succeed.
# On any git failure, the script halts with a message naming the failed step.
#
# Configuration:
#   PGAI_PROJECT_NAME — project name (required when --project flag not used)
#   REPO_ROOT         — override path to the repository root (normally derived
#                       from the project's project.cfg dev_tree_path)

# --- Argument parsing ---
# Done before strict mode so error messages are clean.
PROJECT_ARG=""

_cm_release_usage() {
  echo "Usage: $(basename "$0") [--project <name>] [--help]" >&2
  echo "" >&2
  echo "  --project <name>  project name (required when PGAI_PROJECT_NAME not set)" >&2
  echo "  --help, -h        print full usage and exit 0" >&2
}

_cm_release_help() {
  cat <<HELPTEXT
Usage: $(basename "$0") [--project <name>] [--help]

Close an active release candidate: squash-merge to main, tag, and ship.

Reads the Active RC from \$KANBAN_ROOT/projects/<name>/release-state.md.
No positional arguments — the version is derived from the live release-state.

Arguments:
  --project <name>  project name; overrides PGAI_PROJECT_NAME env var
  --help, -h        print this message and exit 0

Project context resolution (highest to lowest precedence):
  1. --project <name> flag
  2. PGAI_PROJECT_NAME environment variable
  3. FAIL — prints error naming both knobs, exits non-zero

Exit codes:
  0  Release shipped (or --help requested)
  1  HALT condition, pre-condition error, or git failure
     (a \$KANBAN_ROOT/HALT file is created on HALT conditions)

Example:
  cm-release.sh --project my-project

Configuration:
  PGAI_PROJECT_NAME              fallback project name (when --project flag not used)
  REPO_ROOT                      override path to the repository root
  CM_SQUASH_STRICT_POLLUTION_GUARD
                                 set to 'true' to HALT on squash pollution detection
HELPTEXT
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      _cm_release_help
      exit 0
      ;;
    --project)
      if [[ -z "${2:-}" ]]; then
        echo "ERROR: --project requires a value" >&2
        echo "" >&2
        _cm_release_usage
        exit 1
      fi
      PROJECT_ARG="$2"
      shift 2
      ;;
    --*)
      echo "ERROR: unknown flag: $1" >&2
      echo "" >&2
      _cm_release_usage
      exit 1
      ;;
    *)
      echo "ERROR: unexpected argument: $1" >&2
      echo "" >&2
      _cm_release_usage
      exit 1
      ;;
  esac
done

# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

# --- Source optional config files (BEFORE strict mode) ---
# The kanban bashrc/env may have unset vars, non-zero returns, or interactive
# aliases that would trip strict mode. Source them first.
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
TEAM_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
[[ -f "$KANBAN_ROOT/bashrc" ]] && source "$KANBAN_ROOT/bashrc"
[[ -f "$KANBAN_ROOT/env" ]] && source "$KANBAN_ROOT/env"
# $HOME/.config/pgai-kanban.cfg is operator-local bash config; sourced as-is.
[[ -f "$HOME/.config/pgai-kanban.cfg" ]] && source "$HOME/.config/pgai-kanban.cfg"

# --- Source project path helpers ---
# shellcheck source=lib/project_paths.sh
# NOTE: project_paths.sh loads ini_parser.sh (read_ini), which is used below.
source "$(dirname "${BASH_SOURCE[0]}")/../lib/project_paths.sh"

# --- Read INI config (kanban.cfg) ---
# kanban.cfg (INI format) replaces the legacy bash-style config.cfg.
# read_ini is available here because project_paths.sh (sourced above) loads
# ini_parser.sh.  Falls back to sourcing config.cfg (legacy) if kanban.cfg
# is absent — supports upgrade path before migrate-config-to-ini.sh has run.
_CM_KANBAN_CFG="${TEAM_ROOT}/kanban.cfg"
if [[ -f "$_CM_KANBAN_CFG" ]]; then
    # Source: kanban.cfg [paths] dev_tree_path
    export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(read_ini "$_CM_KANBAN_CFG" paths dev_tree_path "")}"
    # Source: kanban.cfg [chain] pm_mode
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "$_CM_KANBAN_CFG" chain pm_mode automatic)}"
fi

# --- Source project registry helpers (provides projects_resolve_release_hook_path) ---
# shellcheck source=lib/projects.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/projects.sh"

# --- Source CM release hook resolution/printing/enforcement library ---
# cm_release_hooks.sh provides cm_resolve_and_enforce_hook, the single call site
# for hook resolution used by both release.sh and ship-rc.sh (one-implementation rule).
# shellcheck source=lib/cm_release_hooks.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/cm_release_hooks.sh"

# --- Source the shared Python invocation helper ---
# shellcheck source=lib/pp_run_ops.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/pp_run_ops.sh"

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

# Resolve branch/tag prefix for this project (empty string for pure-AI installs)
_RELEASE_PREFIX="$(pp_branch_prefix "$PROJECT_NAME")"

# --- Resolve base branch name via pp_prefix_branch ---
# For projects with branch_prefix=ai_, MAIN_BRANCH=ai_main.
# For projects with no branch_prefix, MAIN_BRANCH=main unchanged.
MAIN_BRANCH="$(pp_prefix_branch "$PROJECT_NAME" "main")"

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

# --- Resolve project root and hooks directory ---
# _CM_PROJECT_ROOT is the kanban-side project directory (not the dev tree).
# It is used by _run_release_hook to populate PGAI_PROJECT_ROOT for hook env.
# PROJECT_HOOKS_DIR is where per-project hook scripts live.
_CM_PROJECT_ROOT="$(KANBAN_ROOT="$KANBAN_ROOT" pp_project_root "$PROJECT_NAME" 2>/dev/null)" || _CM_PROJECT_ROOT=""
PROJECT_HOOKS_DIR="${_CM_PROJECT_ROOT}/hooks"

# --- Read push_to_remote flag via pp_push_to_remote helper ---
# Default: 'true' — pushes branches and tags to origin (existing behavior preserved).
# Set [project] push_to_remote = false in project.cfg to complete the full local
# release (squash/merge/tag) without any git push origin calls.  Useful for
# developer-workstation or demo installs where a human controls when work reaches
# the company remote.
_CM_PUSH_TO_REMOTE="$(KANBAN_ROOT="$KANBAN_ROOT" pp_push_to_remote "$PROJECT_NAME")"

# Log the push policy that will be in effect for this run.
if [[ "$_CM_PUSH_TO_REMOTE" == "true" ]]; then
  echo "[cm-release] Push policy: push_to_remote=true — branches and tag will be pushed to origin."
else
  echo "[cm-release] Push policy: push_to_remote=false — branches and tag stay local. Operator must push manually."
fi

# --- Clean exit handling ---
cleanup_on_exit() {
  local exit_code=$?
  jobs -p 2>/dev/null | xargs -r kill 2>/dev/null || true
  wait 2>/dev/null || true
  exit $exit_code
}
trap cleanup_on_exit EXIT

# --- Paths ---
# Canonical release state file: project-scoped live install path.
# This is the ONLY file cm-release.sh reads from and writes to.
# The dev tree's team/release-state.md is NOT consulted.
RELEASE_STATE="$(pp_release_state "$PROJECT_NAME")"

# Helper: run a git command and report the step name on failure.
# Usage: git_step "Step N description" git -C ... <args>
git_step() {
  local step_desc="$1"
  shift
  if ! "$@"; then
    echo "" >&2
    echo "ERROR: git operation failed at step: $step_desc" >&2
    echo "The release-state.md has NOT been modified." >&2
    echo "Recover manually by checking the git state and re-running or reverting as needed." >&2
    exit 1
  fi
}

# Helper: run a lifecycle hook for this release.
# Usage: _run_release_hook <hook-name> <hook-path> [--no-block]
#
# hook-name  : identifier used in log prefixes (e.g. "cm-release-pre-squash")
# hook-path  : absolute path to the hook script
# --no-block : if set, hook failure is a logged warning; release continues
#
# Hook runs with:
#   cwd  = $REPO_ROOT (the dev tree path)
#   env  = six required PGAI_* variables plus the ambient environment
#
# stdout and stderr are captured and prefixed with "[hook <name>]" in the log.
# Return codes: 0 = success or benign skip; non-zero = blocking failure (when
#   --no-block is absent).
_run_release_hook() {
  local hook_name="$1"
  local hook_path="$2"
  local no_block="${3:-}"

  if [[ ! -f "$hook_path" ]]; then
    echo "[cm-release] hook ${hook_name}: not present, skipping"
    return 0
  fi
  if [[ ! -x "$hook_path" ]]; then
    echo "[cm-release] WARNING: hook ${hook_name} exists but is not executable, skipping" >&2
    return 0
  fi

  echo "[cm-release] running hook ${hook_name}..."
  local _hook_rc=0
  (
    cd "$REPO_ROOT"
    PGAI_TARGET_VERSION="$ACTIVE_RC" \
    PGAI_PROJECT_NAME="$PROJECT_NAME" \
    PGAI_PROJECT_ROOT="$_CM_PROJECT_ROOT" \
    PGAI_DEV_TREE_PATH="$REPO_ROOT" \
    PGAI_RC_BRANCH="$RC_BRANCH" \
    PGAI_KANBAN_ROOT="$KANBAN_ROOT" \
    "$hook_path" 2>&1 | sed "s/^/[hook ${hook_name}] /"
    exit "${PIPESTATUS[0]}"
  ) || _hook_rc=$?

  if [[ $_hook_rc -ne 0 ]]; then
    if [[ "$no_block" == "--no-block" ]]; then
      echo "[cm-release] WARNING: hook ${hook_name} failed (rc=$_hook_rc), continuing per --no-block" >&2
      return 0
    fi
    echo "[cm-release] ERROR: hook ${hook_name} failed (rc=$_hook_rc), blocking release" >&2
    echo "[cm-release] ERROR: see [hook ${hook_name}] lines above for the hook's output" >&2
    exit 1
  fi
  echo "[cm-release] hook ${hook_name}: completed successfully"
  return 0
}

# Accumulator for auto-resolved UD/DU paths (set by _cm_autoresolve_ud_conflicts).
# Appended to release-state.md after Step 15 (which overwrites the file with 'cat >').
_CM_AUTORESOLVE_LOG=""

# --- Step 1: git fetch origin --tags ---
# Fetch first so we can find and read the RC branch without depending on the working tree.
# Gated by push_to_remote flag: skipped entirely when push_to_remote=false (no origin).
if [[ "$_CM_PUSH_TO_REMOTE" == "true" ]]; then
  echo "[Step 1] Fetching origin and tags..."
  git_step "git fetch origin --tags" git -C "$REPO_ROOT" fetch origin --tags
else
  echo "[Step 1] push_to_remote=false — skipping git fetch origin (local-only install, no origin to fetch from)."
fi

# --- Defensive stash check ---
# Warn if stashes exist. Stashed changes could indicate uncommitted work that
# was intended for the RC. This is advisory; the human operator decides.
STASH_OUTPUT="$(git -C "$REPO_ROOT" stash list 2>/dev/null || true)"
if [[ -n "$STASH_OUTPUT" ]]; then
  echo "" >&2
  echo "WARNING: git stashes found in the repository:" >&2
  echo "$STASH_OUTPUT" >&2
  echo "" >&2
  echo "Stashes may contain uncommitted work intended for this release." >&2
  echo "Consider reviewing them before proceeding." >&2
  echo "" >&2
fi

# --- Step 2: Find and validate the active RC branch ---
# Discover rc/* branches. We do not read the working-tree release-state.md.
echo "[Step 2] Finding active RC branch..."

# Collect local rc/* branch names (prefix-aware: uses _RELEASE_PREFIX so hybrid
# installs with branch_prefix=ai_ discover ai_rc/* instead of rc/*).
_RC_GLOB="${_RELEASE_PREFIX}rc/*"
mapfile -t RC_BRANCHES < <(git -C "$REPO_ROOT" branch --list "$_RC_GLOB" --format='%(refname:short)' 2>/dev/null || true)

if [[ ${#RC_BRANCHES[@]} -eq 0 ]]; then
  echo "ERROR: no local ${_RC_GLOB} branch found." >&2
  echo "There is no active release candidate to close." >&2
  exit 1
fi

if [[ ${#RC_BRANCHES[@]} -gt 1 ]]; then
  echo "ERROR: multiple ${_RC_GLOB} branches found locally: ${RC_BRANCHES[*]}" >&2
  echo "Only one RC branch may be active at a time. Resolve manually." >&2
  exit 1
fi

RC_BRANCH="${RC_BRANCHES[0]}"
# Strip the leading "${_RELEASE_PREFIX}rc/" to get just the version token
ACTIVE_RC="${RC_BRANCH#${_RELEASE_PREFIX}rc/}"

echo "  Found RC branch: $RC_BRANCH"

# --- Step 3: Validate RC branch exists locally and on origin ---
# Gated by push_to_remote flag:
#   push_to_remote=true  — validate the RC branch exists on origin (original behavior).
#     Idempotency path A: origin RC already absent AND local tag exists → Step 13 already
#       ran in a prior partial run; continue without re-pushing.
#     Idempotency path B: origin RC already absent AND local tag absent AND local RC
#       branch present → interrupted before Step 13; re-establish the RC branch on
#       origin from the known-good local ref and continue (avoids manual recovery push).
#   push_to_remote=false — RC was never pushed to origin (cm-open-rc.sh honored the flag
#     and kept the RC local). Validate locally via refs/heads/ instead.
if [[ "$_CM_PUSH_TO_REMOTE" == "true" ]]; then
  echo "[Step 3] Validating $RC_BRANCH exists locally and on origin..."
  if ! git -C "$REPO_ROOT" rev-parse --verify "refs/heads/$RC_BRANCH" >/dev/null 2>&1; then
    echo "ERROR: branch '$RC_BRANCH' does not exist locally" >&2
    exit 1
  fi
  if ! git -C "$REPO_ROOT" ls-remote --exit-code --heads origin "$RC_BRANCH" >/dev/null 2>&1; then
    # Origin RC is absent. Determine which intermediate state we are in.
    _step3_release_tag="$(pp_prefix_tag "$PROJECT_NAME" "$ACTIVE_RC" 2>/dev/null)" || _step3_release_tag="$ACTIVE_RC"
    _step3_tag_sha="$(git -C "$REPO_ROOT" rev-parse "${_step3_release_tag}^{}" 2>/dev/null)" || _step3_tag_sha=""
    if [[ -n "$_step3_tag_sha" ]]; then
      # Path A: tag already exists locally → Step 13 ran in a prior partial run. Skip re-push.
      echo "[Step 3] origin RC already absent AND local tag ${_step3_release_tag} exists — Step 13 already ran in prior partial run. Continuing."
    else
      # Path B: tag absent + local RC branch present → release was interrupted before Step 13.
      # Re-establish the RC branch on origin from the known-good local ref so downstream
      # steps (squash, merge, tag) can proceed without manual intervention.
      echo "[Step 3] origin RC absent and tag absent — local RC branch present; re-establishing ${RC_BRANCH} on origin..."
      _step3_push_out=$(git -C "$REPO_ROOT" push origin "${RC_BRANCH}" 2>&1); _step3_push_rc=$?
      if [[ $_step3_push_rc -ne 0 ]]; then
        echo "ERROR: failed to re-push ${RC_BRANCH} to origin (rc=${_step3_push_rc})" >&2
        echo "  git push output: ${_step3_push_out}" >&2
        echo "  Recover manually: git push origin ${RC_BRANCH}" >&2
        exit 1
      fi
      echo "[Step 3] ${RC_BRANCH} re-pushed to origin successfully. Resuming release."
    fi
  else
    echo "  Confirmed: $RC_BRANCH exists locally and on origin."
  fi
else
  # push_to_remote=false: RC was kept local by cm-open-rc.sh; validate locally only.
  echo "[Step 3] push_to_remote=false — validating $RC_BRANCH exists locally (no origin check)..."
  if ! git -C "$REPO_ROOT" rev-parse --verify "refs/heads/$RC_BRANCH" >/dev/null 2>&1; then
    echo "ERROR: branch '$RC_BRANCH' does not exist locally" >&2
    exit 1
  fi
  echo "  Confirmed: $RC_BRANCH exists locally (local-only install, origin check skipped)."
fi

# --- Step 4: Read Active RC from project-scoped release-state.md ---
# Read from the canonical live install file, not from git show.
# The active RC must already be set there by cm-open-rc.sh.
echo "[Step 4] Reading Active RC from project-scoped release-state.md..."

if [[ ! -f "$RELEASE_STATE" ]]; then
  echo "ERROR: project-scoped release-state.md not found: $RELEASE_STATE" >&2
  exit 1
fi

# --- Read a named field from release-state.md ---
# Usage: read_field <field_heading>
# Finds "## <field_heading>" then returns the next non-blank non-comment line.
read_field() {
  local heading="$1"
  python3 "$KANBAN_ROOT/pgai_agent_kanban/cm/read_state_field.py" "$RELEASE_STATE" "$heading"
}

# Helper: read a named field from an arbitrary markdown file.
# Usage: read_md_field <filepath> <heading>
# Returns the first non-blank non-comment line after "## <heading>", or "none".
read_md_field() {
  local filepath="$1"
  local heading="$2"
  python3 "$KANBAN_ROOT/pgai_agent_kanban/cm/read_state_field.py" "$filepath" "$heading"
}

# Helper: write the HALT file with a structured comment header.
# Usage: write_halt_file <reason_one_line>
# Creates $KANBAN_ROOT/HALT with timestamp, reason, and resolution pointer.
# Does NOT create the HALT file if it already exists (preserves earlier halt reason).
write_halt_file() {
  local reason="$1"
  local halt_file="${KANBAN_ROOT}/HALT"
  local timestamp
  timestamp="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  if [[ -f "$halt_file" ]]; then
    echo "[cm-release] HALT file already exists at $halt_file — not overwriting." >&2
    echo "[cm-release] Existing HALT file content:" >&2
    cat "$halt_file" >&2
    return 0
  fi
  cat > "$halt_file" <<EOF
# HALT created by CM at ${timestamp}
# Reason: ${reason}
# Resolution: Operator review required. See CM task status.md for full reason.
EOF
  echo "[cm-release] HALT file created: $halt_file"
  echo "[cm-release] Reason: $reason"
}

# Helper: append a HALT Event to the project-scoped release-state.md.
# Usage: append_halt_event_to_release_state <trigger_description> <reason_one_line>
append_halt_event_to_release_state() {
  local trigger="$1"
  local reason="$2"
  local timestamp
  timestamp="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  if [[ ! -f "$RELEASE_STATE" ]]; then
    echo "[cm-release] WARNING: release-state.md not found at $RELEASE_STATE — cannot append HALT event." >&2
    return 0
  fi
  cat >> "$RELEASE_STATE" <<EOF

## HALT Event
Timestamp: ${timestamp}
Trigger: ${trigger}
CM Task: cm-release.sh (automated)
Reason: ${reason}
EOF
  echo "[cm-release] HALT Event appended to release-state.md"
}

# Helper: full HALT procedure — write halt file, append event to release-state, exit 1.
# Usage: cm_halt <trigger_description> <reason_one_line>
# This is the single exit point for all HALT conditions.
cm_halt() {
  local trigger="$1"
  local reason="$2"
  echo "" >&2
  echo "[cm-release] HALT: $reason" >&2
  echo "[cm-release] Trigger: $trigger" >&2
  write_halt_file "$reason"
  append_halt_event_to_release_state "$trigger" "$reason"
  echo "" >&2
  echo "HALT: cm-release.sh stopped. The release has NOT been shipped." >&2
  echo "The dev tree, branches, and RC state are unchanged for operator inspection." >&2
  echo "Remove $KANBAN_ROOT/HALT to resume the autonomous chain after resolving the issue." >&2
  exit 1
}

# Helper: append an auto-resolution note to the project-scoped release-state.md.
# Usage: append_autoresolve_note_to_release_state <squash_description> <paths_resolved>
# Called only when UD/DU conflicts were auto-resolved (no HALT fired).
# Records what was done for operator visibility in the release audit trail.
append_autoresolve_note_to_release_state() {
  local squash_desc="$1"
  local paths_resolved="$2"
  local timestamp
  timestamp="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  if [[ ! -f "$RELEASE_STATE" ]]; then
    echo "[cm-release] WARNING: release-state.md not found — cannot append auto-resolve note." >&2
    return 0
  fi
  cat >> "$RELEASE_STATE" <<EOF

## Auto-Resolve Event
Timestamp: ${timestamp}
Squash: ${squash_desc}
CM Task: cm-release.sh (automated)
Action: Auto-resolved modify/delete (UD/DU) conflicts by taking main's side.
Resolved Paths:
${paths_resolved}
EOF
  echo "[cm-release] Auto-resolve note appended to release-state.md"
}

# Helper: auto-resolve UD/DU modify/delete conflicts after a failed git merge --squash.
# Usage: _cm_autoresolve_ud_conflicts <repo_root> <squash_label> <source_branch_label>
#
# squash_label      : human-readable description for log lines, e.g. "rc/v0.63.1 into main"
# source_branch_label : the name of the branch being squashed IN (the "theirs" side),
#                       used in log lines, e.g. "main" or "rc/v0.63.1"
#
# Returns:
#   0  — all conflicts were UD/DU and have been resolved; caller may proceed with commit.
#   1  — one or more UU (content) conflicts found; caller MUST halt.
#   2  — unexpected conflict type or git status parsing failure; caller MUST halt.
#
# Side effects on exit 0:
#   - All UD/DU paths have been staged (git rm or git add as appropriate).
#   - Each resolution is logged to stdout with the exact line:
#       cm-release: auto-resolved modify/delete: <path> (took main's deletion)
#       cm-release: auto-resolved modify/delete: <path> (took main's version)
#   - DOES NOT commit — the caller is responsible for the commit step.
#
# Side effects on exit 1 or 2:
#   - NO UD/DU paths have been staged (working tree left as-is for operator).
#
_cm_autoresolve_ud_conflicts() {
  local repo_root="$1"
  local squash_label="$2"
  local source_branch_label="$3"

  echo "[cm-release] Squash produced conflicts; inspecting git status..."

  # Collect all conflict lines from git status --porcelain.
  local porcelain_output
  porcelain_output="$(git -C "$repo_root" status --porcelain 2>&1)"

  # Scan for any UU (content conflict) entries first.
  # If ANY UU entry exists, we must not auto-resolve — return 1 immediately.
  local has_uu=0
  local has_ud=0
  local ud_paths=()
  local ud_actions=()

  while IFS= read -r _line; do
    [[ -z "$_line" ]] && continue
    local _xy="${_line:0:2}"
    local _path="${_line:3}"

    case "$_xy" in
      UU)
        has_uu=1
        echo "[cm-release] Content conflict (UU) detected: $_path — cannot auto-resolve." >&2
        ;;
      UD)
        # Our side (HEAD/target branch) updated, their side (source branch) deleted.
        # Taking source branch's (main's) side = delete the file.
        has_ud=1
        ud_paths+=("$_path")
        ud_actions+=("delete")
        ;;
      DU)
        # Our side (HEAD/target branch) deleted, their side (source branch) updated.
        # Taking source branch's (main's) side = keep main's version.
        has_ud=1
        ud_paths+=("$_path")
        ud_actions+=("keep")
        ;;
      AA|DD|AU|UA|DA|AD)
        # Other conflict types — not UD/DU, not UU.
        has_uu=1  # Treat as a conflict we cannot auto-resolve.
        echo "[cm-release] Unexpected conflict type '${_xy}' for path: $_path — cannot auto-resolve." >&2
        ;;
      *)
        # Non-conflict status entries (M, A, D, R, etc.) — ignore.
        ;;
    esac
  done <<< "$porcelain_output"

  # If no conflicts detected at all, this shouldn't happen (squash failed but no U entries).
  # Return 2 so the caller halts with a diagnostic.
  if [[ $has_ud -eq 0 && $has_uu -eq 0 ]]; then
    echo "[cm-release] WARNING: squash returned non-zero but git status shows no U entries." >&2
    echo "[cm-release] This is unexpected; halting for operator inspection." >&2
    return 2
  fi

  # If any UU (or unresolvable) conflicts exist, caller must halt.
  # Do NOT apply any UD resolutions into a partial commit.
  if [[ $has_uu -eq 1 ]]; then
    echo "[cm-release] UU content conflict(s) detected; Trigger 5 HALT required." >&2
    echo "[cm-release] UD/DU paths (if any) will NOT be auto-resolved into a partial commit." >&2
    return 1
  fi

  # Pure UD/DU case: auto-resolve each path by taking main's side.
  echo "[cm-release] All conflicts are modify/delete (UD/DU); auto-resolving by taking ${source_branch_label}'s side..."
  local _resolved_paths_log=""
  local i
  for (( i=0; i<${#ud_paths[@]}; i++ )); do
    local _p="${ud_paths[$i]}"
    local _action="${ud_actions[$i]}"
    if [[ "$_action" == "delete" ]]; then
      # Their (source branch's) side deleted the file — take the deletion.
      git -C "$repo_root" rm --force -- "$_p" >/dev/null 2>&1
      echo "cm-release: auto-resolved modify/delete: ${_p} (took main's deletion)"
      _resolved_paths_log="${_resolved_paths_log}  - ${_p} (took main's deletion)"$'\n'
    else
      # Their (source branch's) side kept/modified the file — take their version.
      git -C "$repo_root" checkout --theirs -- "$_p"
      git -C "$repo_root" add -- "$_p"
      echo "cm-release: auto-resolved modify/delete: ${_p} (took main's version)"
      _resolved_paths_log="${_resolved_paths_log}  - ${_p} (took main's version)"$'\n'
    fi
  done

  # Verify no U entries remain.
  local _remaining_u
  _remaining_u="$(git -C "$repo_root" status --porcelain 2>/dev/null | grep -E '^(UU|UD|DU|AA|DD|AU|UA|DA|AD)' || true)"
  if [[ -n "$_remaining_u" ]]; then
    echo "[cm-release] ERROR: U entries remain after auto-resolve attempt:" >&2
    echo "$_remaining_u" >&2
    echo "[cm-release] Halting for operator inspection." >&2
    return 2
  fi

  local _n="${#ud_paths[@]}"
  echo "[cm-release] Auto-resolve complete: ${_n} modify/delete path(s) resolved for squash: ${squash_label}"

  # Store resolved paths for later recording in release-state.md after Step 15.
  # Step 15 overwrites release-state.md with 'cat >', so we must append AFTER it.
  # _CM_AUTORESOLVE_LOG is a script-level accumulator; each squash site appends here.
  _CM_AUTORESOLVE_LOG="${_CM_AUTORESOLVE_LOG:-}--- ${squash_label} ---"$'\n'"${_resolved_paths_log}"

  return 0
}

# Helper: apply the ship-policy decision matrix.
# Usage: cm_ship_policy <tester_state> <systemic_risk> <recommendation> <has_large_fix_effort>
# Outputs one of: SHIP-FUNCTIONAL  SHIP-KNOWN-BUGS  SHIP-NON-FUNCTIONAL  HALT
# has_large_fix_effort: "yes" if any finding has Fix Effort=large; "no" otherwise.
# Evaluation is top-to-bottom; first matching row wins (per CM.md specification).
cm_ship_policy() {
  local tester_state="${1:-none}"
  local systemic_risk="${2:-none}"
  local recommendation="${3:-none}"
  local has_large_fix_effort="${4:-no}"

  # Normalize to lowercase for comparison robustness.
  tester_state="$(echo "$tester_state" | tr '[:upper:]' '[:lower:]')"
  systemic_risk="$(echo "$systemic_risk" | tr '[:upper:]' '[:lower:]')"
  recommendation="$(echo "$recommendation" | tr '[:upper:]' '[:lower:]')"
  has_large_fix_effort="$(echo "$has_large_fix_effort" | tr '[:upper:]' '[:lower:]')"

  # Row 1: TESTER state is BLOCKED → HALT regardless of other fields.
  if [[ "$tester_state" == "blocked" ]]; then
    echo "HALT"
    return 0
  fi

  # Rows 2–6: TESTER state is DONE.
  if [[ "$tester_state" == "done" ]]; then
    # Row 2: systemic_risk=high → HALT.
    if [[ "$systemic_risk" == "high" ]]; then
      echo "HALT"
      return 0
    fi
    # Rows 3–6: systemic_risk is low or medium (or none/unrecognized → default ship).
    # Row 3: recommendation=PASS → SHIP-FUNCTIONAL.
    if [[ "$recommendation" == "pass" ]]; then
      echo "SHIP-FUNCTIONAL"
      return 0
    fi
    # Row 4: recommendation=SHIP-WITH-CONCERNS → SHIP-KNOWN-BUGS.
    if [[ "$recommendation" == "ship-with-concerns" ]]; then
      echo "SHIP-KNOWN-BUGS"
      return 0
    fi
    # Rows 5–6: recommendation=SHIP-WITH-SERIOUS-CONCERNS.
    if [[ "$recommendation" == "ship-with-serious-concerns" ]]; then
      if [[ "$has_large_fix_effort" == "yes" ]]; then
        # Row 6: any large fix effort → HALT.
        echo "HALT"
        return 0
      else
        # Row 5: all small/medium → SHIP-NON-FUNCTIONAL.
        echo "SHIP-NON-FUNCTIONAL"
        return 0
      fi
    fi
    # Unrecognized recommendation → default SHIP-FUNCTIONAL (per CM.md "unrecognized values" row).
    echo "SHIP-FUNCTIONAL"
    return 0
  fi

  # No TESTER report / unrecognized state → default SHIP-FUNCTIONAL.
  echo "SHIP-FUNCTIONAL"
}

# Helper: scan release-notes/ directory to detect the last-3-NON-FUNCTIONAL pattern.
# Usage: check_last_three_non_functional <release_notes_dir>
# Reads ## Status from the last three release-notes/*.md files (by semver order).
# Returns exit code 0 if pattern is detected (last 3 are all NON-FUNCTIONAL), 1 otherwise.
# Prints a one-line result to stdout for caller to log.
check_last_three_non_functional() {
  local notes_dir="$1"
  python3 - "$notes_dir" <<'PY'
import pathlib, sys, re

notes_dir = pathlib.Path(sys.argv[1])
if not notes_dir.is_dir():
    print("NON-FUNCTIONAL pattern check: release-notes/ directory not found — skipping check.")
    sys.exit(1)

def parse_semver(filename):
    """Parse vMAJOR.MINOR.PATCH from filename for sorting. Returns tuple of ints."""
    m = re.match(r'v?(\d+)\.(\d+)\.(\d+)', filename)
    if not m:
        return (0, 0, 0)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))

def read_status_field(filepath):
    """Read ## Status field value from a release-notes markdown file."""
    try:
        text = filepath.read_text(encoding="utf-8")
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.strip() == "## Status":
                for follow in lines[i+1:]:
                    v = follow.strip()
                    if v and not v.startswith("#"):
                        return v.upper()
                break
    except Exception:
        pass
    return "UNKNOWN"

# Collect all .md files in release-notes/ and sort by semver.
note_files = sorted(
    [f for f in notes_dir.glob("*.md") if re.match(r'v?\d+\.\d+\.\d+', f.name)],
    key=lambda f: parse_semver(f.name)
)

if len(note_files) < 3:
    print(f"NON-FUNCTIONAL pattern check: only {len(note_files)} release(s) found — need at least 3.")
    sys.exit(1)

# Read the last 3 releases.
last_three = note_files[-3:]
statuses = [read_status_field(f) for f in last_three]
names = [f.name for f in last_three]

print(f"NON-FUNCTIONAL pattern check: last 3 releases: {names[0]}={statuses[0]}, {names[1]}={statuses[1]}, {names[2]}={statuses[2]}")

if all(s == "NON-FUNCTIONAL" for s in statuses):
    print("NON-FUNCTIONAL pattern DETECTED: all 3 consecutive releases marked NON-FUNCTIONAL.")
    sys.exit(0)  # Exit 0 means pattern detected → HALT
else:
    sys.exit(1)  # Exit 1 means pattern NOT detected → OK to proceed
PY
}

STATE_ACTIVE_RC="$(read_field "Active RC")"

if [[ "$STATE_ACTIVE_RC" == "none" ]]; then
  echo "ERROR: project-scoped release-state.md shows Active RC = none" >&2
  echo "  Path: $RELEASE_STATE" >&2
  echo "Nothing to release. The RC branch may be in an inconsistent state." >&2
  exit 1
fi

if [[ "$STATE_ACTIVE_RC" != "$ACTIVE_RC" ]]; then
  echo "ERROR: branch name mismatch: branch is '$RC_BRANCH' but release-state.md says Active RC = '$STATE_ACTIVE_RC'" >&2
  echo "  Path: $RELEASE_STATE" >&2
  echo "Resolve manually." >&2
  exit 1
fi

echo "Active RC: $ACTIVE_RC"
echo "RC branch: $RC_BRANCH"
echo ""

# --- Step 4c: Last-3-RCs-NON-FUNCTIONAL pattern check ---
# Scan release-notes/ directory (in the dev tree) for the last three completed releases.
# If all three are marked NON-FUNCTIONAL, the autonomous chain is shipping degraded work
# repeatedly without self-correcting. HALT to force operator review.
echo "[Step 4c] Checking last-3-RCs-NON-FUNCTIONAL pattern..."
_NOTES_DIR_FOR_PATTERN_CHECK="$REPO_ROOT/release-notes"
_PATTERN_CHECK_RESULT=""
# Initialize to 1 (pattern NOT detected / skipped) so a missing notes dir is a safe pass.
_PATTERN_DETECTED=1
if [[ -d "$_NOTES_DIR_FOR_PATTERN_CHECK" ]]; then
  # A prior '|| true' guard prevented set -e from killing the shell on a
  # non-zero exit, but ALSO masked the exit code —
  # _PATTERN_DETECTED was always 0 (true) regardless of whether the pattern
  # was actually detected. set +e / capture / set -e preserves both.
  set +e
  _PATTERN_CHECK_RESULT="$(check_last_three_non_functional "$_NOTES_DIR_FOR_PATTERN_CHECK" 2>&1)"
  _PATTERN_DETECTED=$?
  set -e
  # Print each line of the check output with a two-space indent.
  while IFS= read -r _pat_line; do
    [[ -n "$_pat_line" ]] && echo "  $_pat_line"
  done <<< "$_PATTERN_CHECK_RESULT"
else
  echo "  release-notes/ directory not found in dev tree — skipping pattern check."
fi

if [[ $_PATTERN_DETECTED -eq 0 ]]; then
  cm_halt \
    "Trigger 8: Last 3 consecutive releases are all marked NON-FUNCTIONAL" \
    "Last-3-RCs-NON-FUNCTIONAL pattern detected in $PROJECT_NAME: autonomous chain is shipping degraded work repeatedly. Operator review required before proceeding."
fi
echo "  Pattern check passed — not all 3 consecutive releases are NON-FUNCTIONAL."

# --- Step 4d: TESTER prerequisite check + ship-policy decision ---
# Read the TESTER report for this RC from the project's task tree.
# Policy: TESTER state must be DONE AND systemic_risk must not be high to proceed.
# See CM.md "Ship-Policy Decision Matrix" for the full table.
echo "[Step 4d] Checking TESTER prerequisite and applying ship-policy decision matrix..."

# Locate the TESTER task's status.md and report.md for this RC.
# Convention: TESTER task folders contain "TESTER" in the name and match ## Release Version = ACTIVE_RC.
_TESTER_STATUS="none"
_TESTER_RECOMMENDATION="none"
_TESTER_SYSTEMIC_RISK="none"
_TESTER_REPORT_PATH=""
_TESTER_TASK_ID="none"

_CM_TASKS_DIR="${_CM_PROJECT_ROOT}/tasks"
if [[ -d "$_CM_TASKS_DIR" ]]; then
  while IFS= read -r -d '' _task_dir; do
    _task_readme="${_task_dir}/README.md"
    _task_status_file="${_task_dir}/status.md"
    if [[ ! -f "$_task_readme" || ! -f "$_task_status_file" ]]; then
      continue
    fi
    # Match TESTER tasks for this RC's release version.
    _task_role="$(read_md_field "$_task_readme" "Role")"
    _task_version="$(read_md_field "$_task_readme" "Release Version")"
    if [[ "$_task_role" != "TESTER" ]]; then
      continue
    fi
    if [[ "$_task_version" != "$ACTIVE_RC" ]]; then
      continue
    fi
    # Found a TESTER task for this RC.
    _TESTER_TASK_ID="$(basename "$_task_dir")"
    _TESTER_STATUS="$(read_md_field "$_task_status_file" "State")"
    # Look for the TESTER report artifact.
    _candidate_report="${_task_dir}/artifacts/report.md"
    if [[ -f "$_candidate_report" ]]; then
      _TESTER_REPORT_PATH="$_candidate_report"
      _TESTER_RECOMMENDATION="$(read_md_field "$_TESTER_REPORT_PATH" "Recommendation")"
      _TESTER_SYSTEMIC_RISK="$(read_md_field "$_TESTER_REPORT_PATH" "Systemic Risk")"
    fi
    break
  done < <(find "$_CM_TASKS_DIR" -maxdepth 1 -mindepth 1 -type d -print0 2>/dev/null | sort -z)
fi

echo "  TESTER task:         ${_TESTER_TASK_ID}"
echo "  TESTER state:        ${_TESTER_STATUS}"
echo "  TESTER report:       ${_TESTER_REPORT_PATH:-not found}"
echo "  Recommendation:      ${_TESTER_RECOMMENDATION}"
echo "  Systemic Risk:       ${_TESTER_SYSTEMIC_RISK}"

# Determine whether any finding has Fix Effort = large (needed for SHIP-WITH-SERIOUS-CONCERNS rows).
_HAS_LARGE_FIX_EFFORT="no"
if [[ -n "$_TESTER_REPORT_PATH" && -f "$_TESTER_REPORT_PATH" ]]; then
  if grep -qi "Fix Effort.*large\|fix_effort.*large" "$_TESTER_REPORT_PATH" 2>/dev/null; then
    _HAS_LARGE_FIX_EFFORT="yes"
    echo "  Has large fix effort: yes (found 'Fix Effort: large' in report)"
  else
    echo "  Has large fix effort: no"
  fi
fi

# Apply the ship-policy decision matrix (table-driven, single function, no scattered if-else).
_SHIP_DECISION="$(cm_ship_policy "$_TESTER_STATUS" "$_TESTER_SYSTEMIC_RISK" "$_TESTER_RECOMMENDATION" "$_HAS_LARGE_FIX_EFFORT")"
echo "  Ship decision:       $_SHIP_DECISION"

# Determine release-notes Status field value from ship decision (set before any HALT check).
# Exported so Step 11a can use it when generating release notes.
case "$_SHIP_DECISION" in
  SHIP-FUNCTIONAL)    _RELEASE_NOTES_STATUS="FUNCTIONAL" ;;
  SHIP-KNOWN-BUGS)    _RELEASE_NOTES_STATUS="KNOWN-BUGS" ;;
  SHIP-NON-FUNCTIONAL) _RELEASE_NOTES_STATUS="NON-FUNCTIONAL" ;;
  HALT)               _RELEASE_NOTES_STATUS="" ;;  # No release notes written on HALT
  *)                  _RELEASE_NOTES_STATUS="FUNCTIONAL" ;;
esac

# Apply HALT decisions from the policy matrix.
if [[ "$_SHIP_DECISION" == "HALT" ]]; then
  if [[ "$_TESTER_STATUS" == "BLOCKED" || "$(echo "$_TESTER_STATUS" | tr '[:upper:]' '[:lower:]')" == "blocked" ]]; then
    cm_halt \
      "Trigger 1: TESTER state is BLOCKED" \
      "TESTER task ${_TESTER_TASK_ID} is BLOCKED — verification could not complete. Release of ${ACTIVE_RC} refused."
  elif [[ "$(echo "$_TESTER_SYSTEMIC_RISK" | tr '[:upper:]' '[:lower:]')" == "high" ]]; then
    cm_halt \
      "Trigger 2: TESTER systemic_risk is high" \
      "TESTER report systemic_risk=high for ${ACTIVE_RC} (task ${_TESTER_TASK_ID}). Indicates broader framework regression. Release refused."
  elif [[ "$(echo "$_TESTER_RECOMMENDATION" | tr '[:upper:]' '[:lower:]')" == "ship-with-serious-concerns" && "$_HAS_LARGE_FIX_EFFORT" == "yes" ]]; then
    cm_halt \
      "Trigger 3: SHIP-WITH-SERIOUS-CONCERNS with Fix Effort=large" \
      "TESTER recommendation=SHIP-WITH-SERIOUS-CONCERNS AND a finding has Fix Effort=large for ${ACTIVE_RC}. Scope too large to auto-ship. Operator must review."
  else
    cm_halt \
      "Trigger: ship-policy matrix returned HALT" \
      "Ship-policy matrix returned HALT for ${ACTIVE_RC}. TESTER state=${_TESTER_STATUS} systemic_risk=${_TESTER_SYSTEMIC_RISK} recommendation=${_TESTER_RECOMMENDATION}."
  fi
fi

echo "  Proceeding with release: $_SHIP_DECISION (release notes Status: ${_RELEASE_NOTES_STATUS})"
echo ""

# --- Hook: cm-release-pre-squash ---
# Runs on the RC branch after verification, before squash to main.
# Any commits made by this hook become part of what gets squashed.
# Failure here triggers HALT (Trigger 4).
# Resolution, visibility printing, and required-flag enforcement are handled by
# cm_resolve_and_enforce_hook (see team/scripts/lib/cm_release_hooks.sh).
# NOTE: _run_release_hook calls exit 1 on hook failure (not return 1), so we run
# it in a subshell to capture the exit code without exiting the parent script.
echo "[Step 4b] Running pre-squash hook (if present)..."
CM_RESOLVED_HOOK_PATH=""
cm_resolve_and_enforce_hook "$PROJECT_NAME" "pre-squash" "$PROJECT_HOOKS_DIR" "$REPO_ROOT" || {
  cm_halt \
    "Trigger 4: Pre-squash in-repo hook is not executable" \
    "cm-release-pre-squash in-repo hook exists but is not executable. Fix with: chmod +x (path shown above)"
}
_pre_squash_rc=0
if [[ -n "$CM_RESOLVED_HOOK_PATH" ]]; then
  ( _run_release_hook "cm-release-pre-squash" "$CM_RESOLVED_HOOK_PATH" ) || _pre_squash_rc=$?
fi
if [[ $_pre_squash_rc -ne 0 ]]; then
  cm_halt \
    "Trigger 4: Pre-squash hook failed (rc=${_pre_squash_rc})" \
    "cm-release-pre-squash hook exited non-zero (rc=${_pre_squash_rc}) for ${ACTIVE_RC}. Finalization mechanic broken; squash cannot proceed safely."
fi

# --- Step 4e: Stamp release-notes ## Status placeholder with ship-policy decision ---
# When WRITER authors release notes on the RC branch before the ship decision is known,
# the notes file contains a "## Status: PENDING-RELEASE" placeholder (per CM.md convention).
# This step replaces that placeholder with the actual ship decision value
# (_RELEASE_NOTES_STATUS, set by Step 4d above) and commits the result on the RC branch
# so the squash carries the stamped notes directly to main.
#
# If no WRITER-authored notes file exists for this RC, this step is a no-op — Step 11a
# generates fresh notes with the correct status after the squash to main.
#
# Idempotency: if the placeholder is absent (already stamped or never present), the step
# logs and skips — composing cleanly with idempotent re-run semantics.
#
# HALT triggers:
#   - Notes file exists but placeholder survives after the stamp attempt (stamp bug)
#   - Notes file exists but no recognizable ## Status line found (malformed notes)
echo "[Step 4e] Checking for WRITER-authored release notes to stamp..."
_STAMP_NOTES_FILE="$REPO_ROOT/release-notes/${ACTIVE_RC}.md"
if [[ ! -f "$_STAMP_NOTES_FILE" ]]; then
  echo "[Step 4e] No WRITER-authored release notes found at release-notes/${ACTIVE_RC}.md — skipping stamp (Step 11a will auto-generate)."
else
  # File exists — check whether it contains the PENDING-RELEASE placeholder.
  if ! grep -q "PENDING-RELEASE" "$_STAMP_NOTES_FILE" 2>/dev/null; then
    # Placeholder is absent.  Either already stamped (re-run) or WRITER used a different value.
    # Verify a recognizable ## Status line exists; warn if not.
    if grep -qE "^## Status" "$_STAMP_NOTES_FILE" 2>/dev/null; then
      echo "[Step 4e] step 4e: already complete, continuing — release-notes/${ACTIVE_RC}.md has no PENDING-RELEASE placeholder (already stamped or WRITER used a concrete status value)."
    else
      cm_halt \
        "Trigger 9: Release notes ${ACTIVE_RC}.md has no recognizable ## Status line" \
        "release-notes/${ACTIVE_RC}.md exists but contains no '## Status' heading. Notes are malformed; cannot verify stamp or auto-generate status. Operator must inspect ${_STAMP_NOTES_FILE}."
    fi
  else
    # Placeholder present — perform the stamp.
    echo "[Step 4e] Stamping release-notes/${ACTIVE_RC}.md: PENDING-RELEASE -> ${_RELEASE_NOTES_STATUS}..."
    python3 - \
      "$_STAMP_NOTES_FILE" \
      "${_RELEASE_NOTES_STATUS}" \
      <<'STAMP_PY'
import sys
import re
import pathlib

notes_path     = pathlib.Path(sys.argv[1])
target_status  = sys.argv[2]

text = notes_path.read_text(encoding="utf-8")
lines = text.splitlines(keepends=True)

new_lines = []
in_status = False
replaced  = False
for line in lines:
    if not replaced and re.match(r"^## Status\s*$", line):
        new_lines.append(line)
        in_status = True
        continue
    if in_status:
        stripped = line.strip()
        if stripped == "PENDING-RELEASE":
            new_lines.append(f"{target_status}\n")
            replaced = True
            in_status = False
            continue
        elif stripped.startswith("#") or (stripped and stripped != "PENDING-RELEASE"):
            # Next heading or unexpected non-empty content — stop looking for the placeholder.
            in_status = False
        # Blank lines between ## Status and the value are passed through unchanged.
    new_lines.append(line)

if not replaced:
    # Placeholder not found on this pass — leave file unchanged; guard below will detect it.
    print("WARNING: PENDING-RELEASE placeholder not found under ## Status heading.", file=sys.stderr)
    sys.exit(1)

notes_path.write_text("".join(new_lines), encoding="utf-8")
print(f"  Stamped ## Status: {target_status} in release-notes/{notes_path.name}", flush=True)
STAMP_PY
    _stamp_py_rc=$?
    if [[ $_stamp_py_rc -ne 0 ]]; then
      cm_halt \
        "Trigger 9: Release notes stamp failed for ${ACTIVE_RC}" \
        "Python stamp step exited non-zero (rc=${_stamp_py_rc}) for release-notes/${ACTIVE_RC}.md. PENDING-RELEASE placeholder could not be replaced. Operator must inspect ${_STAMP_NOTES_FILE}."
    fi

    # Guard: verify the placeholder no longer appears in the notes after the stamp.
    if grep -q "PENDING-RELEASE" "$_STAMP_NOTES_FILE" 2>/dev/null; then
      cm_halt \
        "Trigger 9: PENDING-RELEASE placeholder survived stamp step for ${ACTIVE_RC}" \
        "release-notes/${ACTIVE_RC}.md still contains 'PENDING-RELEASE' after the stamp step. Stamp logic did not apply cleanly. Operator must inspect ${_STAMP_NOTES_FILE}."
    fi

    # Guard: verify a recognizable ## Status line exists in the stamped notes.
    if ! grep -qE "^## Status" "$_STAMP_NOTES_FILE" 2>/dev/null; then
      cm_halt \
        "Trigger 9: No recognizable ## Status line in stamped notes for ${ACTIVE_RC}" \
        "release-notes/${ACTIVE_RC}.md has no '## Status' heading after stamp attempt. Operator must inspect ${_STAMP_NOTES_FILE}."
    fi

    echo "[Step 4e] Stamp complete. Committing stamped release notes on RC branch..."
    git_step "git add release-notes stamp" git -C "$REPO_ROOT" add "release-notes/${ACTIVE_RC}.md"
    if git -C "$REPO_ROOT" diff --cached --quiet 2>/dev/null; then
      echo "[Step 4e] step 4e: already complete, continuing — release notes stamp commit already present (no staged changes)."
    else
      _stamp_commit_out=""
      _stamp_commit_rc=0
      _stamp_commit_out="$(git -C "$REPO_ROOT" commit -m "Stamp release-notes/${ACTIVE_RC}.md ## Status: ${_RELEASE_NOTES_STATUS}" 2>&1)" || _stamp_commit_rc=$?
      if [[ $_stamp_commit_rc -ne 0 ]]; then
        echo "" >&2
        echo "ERROR: git commit release-notes stamp failed (rc=${_stamp_commit_rc})" >&2
        echo "$_stamp_commit_out" >&2
        exit 1
      else
        echo "$_stamp_commit_out"
      fi
    fi
    echo "[Step 4e] Release notes stamped and committed on ${RC_BRANCH}."
  fi
fi

# --- Step 4f: Squash pollution guard ---
# Before squashing, inspect the set of files that the RC branch introduces
# relative to the squash target (main).  Flag any file matching a
# non-source / stray pattern:
#
#   (a) repo-root artifacts/          — task scratch escaped to wrong location
#   (c) task-ID-named paths at repo   — CODER/TESTER/etc. dirs outside
#       root or outside projects/*/   projects/<name>/tasks/
#   (d) PRIORITY-*.md outside         — priority-intake files outside their
#       projects/<name>/priority/       sanctioned project priority/ directory
#   (e) requirements v*.md outside    — requirements-intake files outside their
#       projects/<name>/requirements/   sanctioned project requirements/ directory
#
# Legitimate paths that MUST pass silently:
#   projects/<name>/artifacts/**        — document deliverables
#   projects/<name>/tasks/*/artifacts/**— per-task outputs
#   projects/<name>/priority/PRIORITY-*.md  — correctly-placed priority files
#   projects/<name>/requirements/v*.md      — correctly-placed requirements files
#   team/templates/**                  — template source tree; files here are
#                                        framework scaffolding, not intake strays
#
# Default mode: warn loudly (POLLUTION-GUARD WARN) for each flagged path.
# Strict mode  (CM_SQUASH_STRICT_POLLUTION_GUARD=true): cm_halt after listing
# all flagged paths.
#
# The guard logs to stdout so the release log captures every flagged path.
# No files are modified or removed — the guard is observation-only.
echo "[Step 4f] Running squash pollution guard on ${RC_BRANCH}..."
_guard_flagged=()
_guard_diff_base=""

# Compute the merge-base of main and RC so the diff covers exactly
# what the squash would commit (files introduced or changed on the RC).
_guard_diff_base="$(git -C "$REPO_ROOT" merge-base "$MAIN_BRANCH" "$RC_BRANCH" 2>/dev/null || true)"
if [[ -z "$_guard_diff_base" ]]; then
  echo "[Step 4f] WARNING: could not compute merge-base for pollution guard; skipping path scan." >&2
else
  while IFS= read -r _gpath; do
    [[ -z "$_gpath" ]] && continue

    # --- Templates tree: location-based exclusion ---
    # Files under team/templates/ are framework scaffolding (source), not
    # deposited intake items.  Skip all pattern checks for this tree so that
    # legitimate template files (BUG-TEMPLATE.md, PRIORITY-TEMPLATE.md, etc.)
    # are never flagged — regardless of their names.  A real intake stray
    # deposited in an actual intake/backlog directory will NOT match this
    # prefix and will still be caught by the patterns below.
    if [[ "$_gpath" == team/templates/* ]]; then
      continue
    fi

    # --- Pattern (a): repo-root artifacts/ tree ---
    # Any path starting with "artifacts/" at the repo root is stray.
    # Legitimate: projects/<name>/artifacts/ (starts with "projects/")
    if [[ "$_gpath" == artifacts/* ]]; then
      _guard_flagged+=("$_gpath  [stray: repo-root artifacts/]")
      continue
    fi

    # --- Pattern (c): task-ID-named dirs outside projects/<name>/tasks/ ---
    # Matches the canonical task-ID shape: ROLE-YYYYMMDD-NNN-slug
    # Legitimate: projects/<name>/tasks/ROLE-YYYYMMDD-NNN-slug/
    # Stray: ROLE-YYYYMMDD-NNN-slug/ at repo root OR under team/ etc.
    if echo "$_gpath" | grep -qE '^[A-Z]+-[0-9]{8}-[0-9]+-[a-z0-9-]+/' ; then
      _guard_flagged+=("$_gpath  [stray: task-ID-named path outside projects/<name>/tasks/]")
      continue
    fi
    if echo "$_gpath" | grep -qE '^team/[A-Z]+-[0-9]{8}-[0-9]+-[a-z0-9-]+/' ; then
      _guard_flagged+=("$_gpath  [stray: task-ID-named path under team/ (outside projects/<name>/tasks/)]")
      continue
    fi

    # --- Pattern (d): PRIORITY-*.md misplaced intake files ---
    # Sanctioned location: projects/<name>/priority/PRIORITY-*.md
    # Any PRIORITY-*.md outside projects/<name>/priority/ is stray.
    _basename_gpath_d="$(basename "$_gpath")"
    if [[ "$_basename_gpath_d" == PRIORITY-*.md ]]; then
      if [[ "$_gpath" != projects/*/priority/PRIORITY-*.md ]]; then
        _guard_flagged+=("$_gpath  [stray: priority-intake file outside projects/<name>/priority/]")
        continue
      fi
    fi

    # --- Pattern (e): requirements v*.md misplaced intake files ---
    # Sanctioned location: projects/<name>/requirements/v*.md
    # Any v*.md (requirements version file) outside projects/<name>/requirements/ is stray.
    _basename_gpath_e="$(basename "$_gpath")"
    if [[ "$_basename_gpath_e" == v*.md ]]; then
      if [[ "$_gpath" != projects/*/requirements/v*.md ]]; then
        _guard_flagged+=("$_gpath  [stray: requirements-intake file outside projects/<name>/requirements/]")
        continue
      fi
    fi

  done < <(git -C "$REPO_ROOT" diff --name-only "$_guard_diff_base" "$RC_BRANCH" 2>/dev/null)
fi

if [[ "${#_guard_flagged[@]}" -gt 0 ]]; then
  echo "" >&2
  echo "[cm-release] POLLUTION-GUARD: ${#_guard_flagged[@]} stray path(s) detected in squash set for ${ACTIVE_RC}:" >&2
  for _gf in "${_guard_flagged[@]}"; do
    echo "[cm-release] POLLUTION-GUARD WARN:  ${_gf}" >&2
  done
  echo "[cm-release] POLLUTION-GUARD: These paths will be committed into release history unless removed before squash." >&2
  echo "[cm-release] POLLUTION-GUARD: To prevent absorption: operator should remove/unstage the stray files and re-run." >&2
  echo "[cm-release] POLLUTION-GUARD: Set CM_SQUASH_STRICT_POLLUTION_GUARD=true to halt instead of warn." >&2
  echo ""
  if [[ "${CM_SQUASH_STRICT_POLLUTION_GUARD:-false}" == "true" ]]; then
    cm_halt \
      "Trigger 10: Squash pollution guard: stray paths in ${ACTIVE_RC}" \
      "POLLUTION-GUARD strict mode: ${#_guard_flagged[@]} stray path(s) would be absorbed by squash. Remove them from the RC branch and re-run."
  fi
else
  echo "[Step 4f] Squash pollution guard: no stray paths detected — proceeding."
fi
unset _guard_flagged _guard_diff_base _gpath _gf _basename_gpath_d _basename_gpath_e

# --- Step 5: git checkout main (or prefixed equivalent) && git pull --ff-only ---
# Idempotency: on a re-run, local main may already be AHEAD of origin/main (push_to_remote=false,
# or a partial re-run where push succeeded on the prior run).  git merge --ff-only fails in that
# case ("not possible to fast-forward").  Guard: skip the ff-only pull when local main is already
# ahead of or equal to origin/main, logging the idempotent step.
echo "[Step 5] Checking out $MAIN_BRANCH and pulling..."
git_step "git checkout $MAIN_BRANCH" git -C "$REPO_ROOT" checkout "$MAIN_BRANCH"

_main_local_sha="$(git -C "$REPO_ROOT" rev-parse "$MAIN_BRANCH" 2>/dev/null)" || _main_local_sha=""
_main_origin_sha="$(git -C "$REPO_ROOT" rev-parse "origin/$MAIN_BRANCH" 2>/dev/null)" || _main_origin_sha=""
if [[ -z "$_main_origin_sha" ]]; then
  # origin/main doesn't exist yet (local-only install) — skip the ff pull.
  echo "[Step 5] origin/$MAIN_BRANCH not found — skipping ff-only pull (local-only or push_to_remote=false install)."
elif [[ "$_main_local_sha" == "$_main_origin_sha" ]]; then
  echo "[Step 5] $MAIN_BRANCH already up-to-date with origin/$MAIN_BRANCH — skipping ff-only pull."
else
  # Check whether local is already ahead of origin (merge-base == origin SHA).
  _main_merge_base="$(git -C "$REPO_ROOT" merge-base "$MAIN_BRANCH" "origin/$MAIN_BRANCH" 2>/dev/null)" || _main_merge_base=""
  if [[ -n "$_main_merge_base" && "$_main_merge_base" == "$_main_origin_sha" ]]; then
    echo "[Step 5] step 5: already complete, continuing — $MAIN_BRANCH is already ahead of origin/$MAIN_BRANCH (re-run after partial release)."
  else
    git_step "git pull --ff-only $MAIN_BRANCH" git -C "$REPO_ROOT" merge --ff-only "origin/$MAIN_BRANCH"
  fi
fi

# --- Step 6: git merge --squash rc/<ACTIVE_RC> ---
# Idempotency: if main already contains all of the RC's content (SHA-equality or
# an empty index after squash), treat the squash as already complete.
echo "[Step 6] Squash-merging $RC_BRANCH into $MAIN_BRANCH..."
MAIN_SHA="$(git -C "$REPO_ROOT" rev-parse "$MAIN_BRANCH")"
RC_SHA="$(git -C "$REPO_ROOT" rev-parse "$RC_BRANCH")"

if [[ "$MAIN_SHA" == "$RC_SHA" ]]; then
  echo "[Step 6] step 6: already complete, continuing — $MAIN_BRANCH is already at $RC_BRANCH tip ($(echo "$MAIN_SHA" | cut -c1-7)). Skipping squash."
  echo "[Step 7] step 7: already complete, continuing — no commit needed ($MAIN_BRANCH already contains RC content)."
else
  _squash_main_rc=0
  git -C "$REPO_ROOT" merge --squash "$RC_BRANCH" || _squash_main_rc=$?
  if [[ $_squash_main_rc -ne 0 ]]; then
    # Attempt UD/DU auto-resolve before halting.
    _autoresolve_main_rc=0
    _cm_autoresolve_ud_conflicts "$REPO_ROOT" "${RC_BRANCH} into ${MAIN_BRANCH}" "main" || _autoresolve_main_rc=$?
    if [[ $_autoresolve_main_rc -ne 0 ]]; then
      cm_halt \
        "Trigger 5: Squash of ${RC_BRANCH} into ${MAIN_BRANCH} produced conflicts (rc=${_squash_main_rc})" \
        "git merge --squash ${RC_BRANCH} into ${MAIN_BRANCH} failed (rc=${_squash_main_rc}). Git state may be damaged; operator must resolve before any branch mutation continues."
    fi
  fi

  # --- Step 7: git commit ---
  # Idempotency: an empty index after the squash merge means the RC's content was
  # already present on main (e.g., from a prior run that committed the squash before
  # dying, or an operator manual commit). Detect via exit code, not git prose.
  echo "[Step 7] Committing squash on $MAIN_BRANCH..."
  if git -C "$REPO_ROOT" diff --cached --quiet 2>/dev/null; then
    echo "[Step 7] step 7: already complete, continuing — index is empty after squash (main already contained release content from prior run or manual operator commit)."
  else
    _commit_main_out=""
    _commit_main_rc=0
    _commit_main_out="$(git -C "$REPO_ROOT" commit -m "Release $ACTIVE_RC" 2>&1)" || _commit_main_rc=$?
    if [[ $_commit_main_rc -ne 0 ]]; then
      echo "" >&2
      echo "ERROR: git commit (main squash) failed (rc=${_commit_main_rc})" >&2
      echo "$_commit_main_out" >&2
      echo "The release-state.md has NOT been modified." >&2
      echo "Recover manually by checking the git state and re-running or reverting as needed." >&2
      exit 1
    else
      echo "$_commit_main_out"
    fi
  fi
fi

# --- Step 7a: Fidelity gate ---
# After the squash commit on main and BEFORE release-notes and tag:
# assert that rc/<ACTIVE_RC> and $MAIN_BRANCH are byte-identical.
# At this moment no release-notes have landed, so no diff exclusions are needed.
# On divergence: print git diff --stat, write HALT, exit 1.
# A clean gate prints a single OK line and continues.
#
# When divergence is detected it means an unexpected commit landed on main
# after the RC branched from it (e.g. an operator commit mid-RC).  The squash
# absorbed the RC's changes but the foreign commit is now present on main and
# not in the RC — a difference the tag would silently lock in.
#
# Idempotency (resume): if release-notes for this RC are already committed to
# main HEAD, the fidelity gate already passed on the prior run; skip it here.
# Main legitimately contains more commits than the RC branch at this point
# (the release artifacts from the prior run), so a diff would falsely fire.
_fidelity_resume=0
_fidelity_rn_ls="$(git -C "$REPO_ROOT" ls-tree --name-only HEAD "release-notes/${ACTIVE_RC}.md" 2>/dev/null)" || true
[[ -n "$_fidelity_rn_ls" ]] && _fidelity_resume=1
if [[ $_fidelity_resume -eq 1 ]]; then
  echo "[Step 7a] Fidelity gate: skip — release-notes/${ACTIVE_RC}.md already committed to $MAIN_BRANCH HEAD (resume path; gate passed on prior run)."
else
  echo "[Step 7a] Fidelity gate: verifying rc/$ACTIVE_RC tree matches $MAIN_BRANCH after squash..."
  if git -C "$REPO_ROOT" diff --quiet "$RC_BRANCH" "$MAIN_BRANCH" 2>/dev/null; then
    echo "[Step 7a] Fidelity gate: OK — rc/$ACTIVE_RC and $MAIN_BRANCH are identical."
  else
    echo "" >&2
    echo "[Step 7a] Fidelity gate: DIVERGENCE DETECTED — rc/$ACTIVE_RC and $MAIN_BRANCH differ:" >&2
    git -C "$REPO_ROOT" diff --stat "$RC_BRANCH" "$MAIN_BRANCH" >&2 || true
    cm_halt \
      "Trigger 11: Fidelity gate fired — rc/${ACTIVE_RC} tree diverges from ${MAIN_BRANCH} after squash" \
      "Post-squash fidelity gate failed for ${ACTIVE_RC}: ${MAIN_BRANCH} contains commits not present in rc/${ACTIVE_RC}. A commit landed on main mid-RC. Operator must inspect the divergent paths shown above and resolve before re-running."
  fi
fi

# --- Step 8: Generate release notes from RC branch commits ---
# Done here, after the fidelity gate (Step 7a) and squash commit (Step 7), but
# before the RC branch is deleted (Steps 9-10).  The RC branch must still exist
# so git log can enumerate the feature/bug commits it contributed.
#
# Strategy (in priority order):
#   1. If a requirements bundle file exists for this RC, read each bundled item's
#      ## Category field and map it to an industry-standard release notes section.
#   2. Fallback: collect git commit subjects from the RC branch and place all
#      of them in the "Other Changes" section.
#
# Output structure (8 sections, all present even when empty):
#   ## Breaking Changes
#   ## Upgrade Notes
#   ## Features
#   ## Bug Fixes
#   ## Deprecations
#   ## Documentation
#   ## Other Changes
#   ## Known Issues
echo "[Step 8] Generating release notes for $ACTIVE_RC..."

RELEASE_NOTES_DIR="$REPO_ROOT/release-notes"
RELEASE_NOTES_FILE="$RELEASE_NOTES_DIR/${ACTIVE_RC}.md"
RELEASE_DATE="$(date +%Y-%m-%d)"

# Idempotency: if release-notes/${ACTIVE_RC}.md is already committed to HEAD on main,
# the entire generation + add + commit block is already complete.  Re-running the Python
# generator could produce different output (e.g., if RC_BRANCH now points at main tip
# after a reset), creating a new commit that advances main HEAD past the existing tag.
# Guard: skip the block entirely when the file is already present in HEAD.
# NOTE: git ls-tree exits 0 even when the path is absent (output is empty in that case).
# Detect presence by checking whether stdout is non-empty.
_rn_in_head=0
_rn_ls_out="$(git -C "$REPO_ROOT" ls-tree --name-only HEAD "release-notes/${ACTIVE_RC}.md" 2>/dev/null)" || true
[[ -n "$_rn_ls_out" ]] && _rn_in_head=1
if [[ $_rn_in_head -eq 1 ]]; then
  echo "[Step 8] step 8: already complete, continuing — release-notes/${ACTIVE_RC}.md is already committed to $MAIN_BRANCH HEAD."
else

# Ensure the release-notes/ directory exists
mkdir -p "$RELEASE_NOTES_DIR"

# Identify the point where the RC branch diverged from main.
# merge-base gives us the last common ancestor commit.
RC_MERGE_BASE="$(git -C "$REPO_ROOT" merge-base "$MAIN_BRANCH" "$RC_BRANCH" 2>/dev/null)" || {
  echo "WARNING: could not find merge-base for $RC_BRANCH and $MAIN_BRANCH; using empty commit list." >&2
  RC_MERGE_BASE=""
}

# Locate the requirements bundle file for this RC (same logic as Step 16b).
# Used to read per-item ## Category fields for section classification.
_cm_bundle_file_for_notes=""
_cm_notes_project_root="$(KANBAN_ROOT="$KANBAN_ROOT" pp_project_root "$PROJECT_NAME" 2>/dev/null)" || _cm_notes_project_root=""
_cm_notes_requirements_dir="${_cm_notes_project_root}/requirements"
if [[ -n "$_cm_notes_project_root" && -d "$_cm_notes_requirements_dir" ]]; then
  while IFS= read -r -d '' _f; do
    _cm_bundle_file_for_notes="$_f"
    break
  done < <(find "$_cm_notes_requirements_dir" -maxdepth 1 -type f \
            -name "${ACTIVE_RC}-*.md" -print0 2>/dev/null | sort -z)
fi

# Use Python to generate the structured release notes file.
# Arguments passed to the embedded script (positional):
#   1: bundle file path (or "none" if not found)
#   2: REPO_ROOT
#   3: ACTIVE_RC (version string)
#   4: _RELEASE_NOTES_STATUS
#   5: RELEASE_DATE
#   6: PROJECT_NAME
#   7: RC_MERGE_BASE (or "none")
#   8: RC_BRANCH
#   9: _TESTER_TASK_ID
python3 - \
  "${_cm_bundle_file_for_notes:-none}" \
  "$REPO_ROOT" \
  "$ACTIVE_RC" \
  "${_RELEASE_NOTES_STATUS:-FUNCTIONAL}" \
  "$RELEASE_DATE" \
  "$PROJECT_NAME" \
  "${RC_MERGE_BASE:-none}" \
  "$RC_BRANCH" \
  "${_TESTER_TASK_ID:-unknown-tester-task}" \
  <<'RELNOTES_PY'
import sys
import re
import pathlib
import subprocess

bundle_file_arg = sys.argv[1]   # path or "none"
repo_root       = sys.argv[2]
active_rc       = sys.argv[3]
status          = sys.argv[4]
release_date    = sys.argv[5]
project_name    = sys.argv[6]
merge_base      = sys.argv[7]   # sha or "none"
rc_branch       = sys.argv[8]
tester_task_id  = sys.argv[9]

# Output path: REPO_ROOT/release-notes/ACTIVE_RC.md
out_dir = pathlib.Path(repo_root) / "release-notes"
out_dir.mkdir(parents=True, exist_ok=True)
out_file = out_dir / f"{active_rc}.md"

# Category -> section mapping.
CATEGORY_SECTION = {
    "breaking":    "Breaking Changes",
    "feature":     "Features",
    "bugfix":      "Bug Fixes",
    "deprecation": "Deprecations",
    "removal":     "Deprecations",
    "docs":        "Documentation",
    "misc":        "Other Changes",
}
SECTION_ORDER = [
    "Breaking Changes",
    "Upgrade Notes",
    "Features",
    "Bug Fixes",
    "Deprecations",
    "Documentation",
    "Other Changes",
    "Known Issues",
]


def read_item_category(item_path):
    """Read ## Category field from an item file. Default: 'misc'."""
    try:
        text = pathlib.Path(item_path).read_text(encoding="utf-8")
    except OSError:
        return "misc"
    lines = text.splitlines()
    in_category = False
    for line in lines:
        if re.match(r"^\s*##\s+Category\s*$", line, re.IGNORECASE):
            in_category = True
            continue
        if in_category:
            stripped = line.strip()
            if stripped.startswith("#"):
                break  # next section — no value found
            if stripped:
                return stripped.lower()
    return "misc"


def get_item_title(item_path):
    """Return first H1 line stripped of leading '#', or the file stem."""
    try:
        text = pathlib.Path(item_path).read_text(encoding="utf-8")
    except OSError:
        return pathlib.Path(item_path).stem
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return pathlib.Path(item_path).stem


# Build sections dict: section name -> list of bullet strings
sections = {s: [] for s in SECTION_ORDER}

bundle_used = False

if bundle_file_arg != "none":
    bundle_path = pathlib.Path(bundle_file_arg)
    if bundle_path.exists():
        bundle_text = bundle_path.read_text(encoding="utf-8")
        # Find ## Bundled Items section
        b_lines = bundle_text.splitlines()
        in_bundled = False
        item_paths = []
        for line in b_lines:
            if re.match(r"^\s*##\s+Bundled Items\s*$", line, re.IGNORECASE):
                in_bundled = True
                continue
            if in_bundled:
                if re.match(r"^\s*##", line):
                    break
                m = re.search(r"\(\`([^`]+)\`\)", line)
                if m:
                    item_paths.append(m.group(1).strip())
        if item_paths:
            bundle_used = True
            for item_path in item_paths:
                p = pathlib.Path(item_path)
                category = read_item_category(item_path)
                section_name = CATEGORY_SECTION.get(category, "Other Changes")
                title = get_item_title(item_path)
                sections[section_name].append(f"- {title}")
        else:
            # Bundle file found but has no ## Bundled Items entries —
            # treat as operator-authored; use the bundle title itself.
            bundle_used = True
            category = read_item_category(bundle_file_arg)
            section_name = CATEGORY_SECTION.get(category, "Other Changes")
            title = get_item_title(bundle_file_arg)
            sections[section_name].append(f"- {title}")

if not bundle_used:
    # Fallback: collect git commit subjects, all go to "Other Changes".
    if merge_base != "none":
        try:
            result = subprocess.run(
                ["git", "-C", repo_root, "log", "--no-merges",
                 "--format=%s", f"{merge_base}..{rc_branch}"],
                capture_output=True, text=True, check=False
            )
            for subj in result.stdout.splitlines():
                subj = subj.strip()
                if not subj:
                    continue
                if subj.startswith("Set Active RC"):
                    continue
                if subj.startswith("Sync release-state"):
                    continue
                sections["Other Changes"].append(f"- {subj}")
        except OSError:
            pass

# Build the output file
lines_out = []
lines_out.append(f"# Release Notes: {project_name} {active_rc}")
lines_out.append("")
lines_out.append(f"**Release Date:** {release_date}")
lines_out.append("**Released By:** cm-release.sh (automated)")
lines_out.append("")
lines_out.append("## Status")
lines_out.append(status)
lines_out.append("")

for section in SECTION_ORDER:
    lines_out.append(f"## {section}")
    if section == "Upgrade Notes":
        lines_out.append("None")
    elif section == "Known Issues":
        if status == "NON-FUNCTIONAL":
            lines_out.append(
                "This release is shipped non-functional. "
                "Do not use in production. Fix expected in the next patch."
            )
            lines_out.append(
                f"(See TESTER report for filed bug IDs: {tester_task_id})"
            )
        else:
            lines_out.append("None")
    else:
        items = sections[section]
        if items:
            lines_out.extend(items)
        else:
            lines_out.append("None")
    lines_out.append("")

out_file.write_text("\n".join(lines_out), encoding="utf-8")
print(f"  Release notes written to: release-notes/{active_rc}.md", flush=True)
RELNOTES_PY

# Commit the release notes file on main (alongside the squash commit just made).
# Idempotency: if release notes already exist unchanged from a prior run, the index
# is empty after git add — detect via exit code, not git prose.
git_step "git add release-notes" git -C "$REPO_ROOT" add "release-notes/${ACTIVE_RC}.md"
if git -C "$REPO_ROOT" diff --cached --quiet 2>/dev/null; then
  echo "[Step 11a] step 11a: already complete, continuing — release notes already committed from prior run (no staged changes)."
else
  _commit_rn_out=""
  _commit_rn_rc=0
  _commit_rn_out="$(git -C "$REPO_ROOT" commit -m "Add release notes for ${ACTIVE_RC}" 2>&1)" || _commit_rn_rc=$?
  if [[ $_commit_rn_rc -ne 0 ]]; then
    echo "" >&2
    echo "ERROR: git commit release-notes failed (rc=${_commit_rn_rc})" >&2
    echo "$_commit_rn_out" >&2
    exit 1
  else
    echo "$_commit_rn_out"
  fi
fi

fi  # end: skip if release-notes/${ACTIVE_RC}.md already in HEAD

# --- Step 8b: Commit WRITER polish of release-notes/<ACTIVE_RC>.md if present ---
# When WRITER authored release notes on the RC branch, the squash merge already
# brought those notes onto main.  If the WRITER also left uncommitted polish
# changes on disk (working tree), commit them NOW — before Step 11b regenerates
# CHANGELOG.md — so the changelog_writer reads the polished notes as input.
# This ordering ensures that after the release: (1) the polished notes are committed,
# (2) CHANGELOG.md reflects those polished notes, and (3) the tag lands on that
# CHANGELOG commit, leaving the freshness gate green on the tip (tag == tip).
#
# Idempotency: git status --porcelain is empty when the file matches HEAD.
# A re-run with no uncommitted polish produces no commit — this step is a no-op.
#
# Cases handled:
#   - Working-tree changes to the file (uncommitted WRITER polish) → commit them
#   - File already committed (no diff) or unchanged from generated stub → no-op
#   - File missing (notes were never generated and WRITER never authored them)  → warning
_BARE_VERSION_FOR_POLISH="$(pp_strip_prefix_from_tag "$PROJECT_NAME" "$ACTIVE_RC" 2>/dev/null)" || _BARE_VERSION_FOR_POLISH="$ACTIVE_RC"
_POLISH_NOTES_FILE="$REPO_ROOT/release-notes/${_BARE_VERSION_FOR_POLISH}.md"
echo "[Step 8b] Checking for uncommitted WRITER polish of release-notes/${_BARE_VERSION_FOR_POLISH}.md..."
if [[ ! -f "$_POLISH_NOTES_FILE" ]]; then
  echo "  [Step 8b] release-notes/${_BARE_VERSION_FOR_POLISH}.md not found; polish step skipped." >&2
else
  _POLISH_PORCELAIN="$(git -C "$REPO_ROOT" status --porcelain "release-notes/${_BARE_VERSION_FOR_POLISH}.md" 2>/dev/null || true)"
  if [[ -n "$_POLISH_PORCELAIN" ]]; then
    echo "  [Step 8b] Uncommitted WRITER polish detected; committing before CHANGELOG regeneration..."
    git_step "git add release-notes (polish)" git -C "$REPO_ROOT" add "release-notes/${_BARE_VERSION_FOR_POLISH}.md"
    if git -C "$REPO_ROOT" diff --cached --quiet 2>/dev/null; then
      echo "  [Step 8b] step 8b: already complete, continuing — no staged polish changes to commit."
    else
      _commit_polish_out=""
      _commit_polish_rc=0
      _commit_polish_out="$(git -C "$REPO_ROOT" commit -m "Polish release notes for ${ACTIVE_RC}" 2>&1)" || _commit_polish_rc=$?
      if [[ $_commit_polish_rc -ne 0 ]]; then
        echo "" >&2
        echo "ERROR: git commit release-notes polish failed (rc=${_commit_polish_rc})" >&2
        echo "$_commit_polish_out" >&2
        exit 1
      else
        echo "$_commit_polish_out"
        echo "  [Step 8b] Polish commit created before CHANGELOG regeneration."
      fi
    fi
  else
    echo "  [Step 8b] release-notes/${_BARE_VERSION_FOR_POLISH}.md is clean (no uncommitted polish)."
  fi
fi

# --- Step 11b: Regenerate CHANGELOG.md at project root ---
# Regenerates the full CHANGELOG.md from all release-notes/vX.Y.Z.md files and
# the bug ledger via changelog_writer.py (newest release first). The writer is
# the single source of truth; using it here keeps the released artifact
# byte-identical to what the freshness gate verifies.
echo "[Step 11b] Updating CHANGELOG.md for ${ACTIVE_RC}..."

# Idempotency: always render a fresh changelog_writer buffer (PYTHONHASHSEED=0
# for deterministic heading order) and compare it byte-for-byte against the
# checked-in CHANGELOG.md.  Only skip regeneration when the two are byte-identical.
#
# A heading-presence-only check (the prior approach) was insufficient: a
# WRITER-authored section satisfies the heading check but diverges from the
# canonical writer output, and a stale section would silently merge to main.
# The byte-compare catches any divergence — including hand-authored content,
# reordered bullets, or retained internal BUG-NNNN identifiers that the writer
# strips.
_cl_bugs_dir="${_CM_PROJECT_ROOT}/bugs"
_cl_temp_dir="${PGAI_AGENT_KANBAN_TEMP_DIR:-/tmp/pgai_kanban_tmp}"
mkdir -p "$_cl_temp_dir"
_cl_buf="$(mktemp "${_cl_temp_dir}/cl_buf_XXXXXXXX")"
# Ensure the temp buffer is cleaned up on any exit path.
trap 'rm -f "$_cl_buf"' EXIT

# Render the canonical CHANGELOG.md to the temp buffer.
# PYTHONHASHSEED=0 ensures frozenset iteration over heading sets is stable
# across invocations so two runs on identical inputs are byte-identical.
# pp_run_ops sets PYTHONPATH to include the own-tree root and KANBAN_ROOT fallback.
PYTHONHASHSEED=0 \
  pp_run_ops pgai_agent_kanban.cm.changelog_writer \
  "$REPO_ROOT" "$_cl_bugs_dir" \
  > "$_cl_buf"

# Byte-compare: skip regeneration only when the checked-in file is byte-identical
# to the fresh render.  A missing CHANGELOG.md on disk always diverges.
_cl_needs_regen=1
if [[ -f "$REPO_ROOT/CHANGELOG.md" ]] && cmp -s "$REPO_ROOT/CHANGELOG.md" "$_cl_buf"; then
  _cl_needs_regen=0
fi

if [[ $_cl_needs_regen -eq 0 ]]; then
  echo "[Step 11b] step 11b: already complete, continuing — CHANGELOG.md is byte-identical to a fresh changelog_writer render."
else

# Install the fresh render as CHANGELOG.md.
cp "$_cl_buf" "$REPO_ROOT/CHANGELOG.md"
echo "  CHANGELOG.md regenerated in full for ${ACTIVE_RC}."

# Safety pass: no internal BUG-[0-9] token may survive in the regenerated
# CHANGELOG.md.  The changelog_writer strips these via its documented safety
# pass; if any survive (e.g. from a writer defect), fail loudly here so the
# release is blocked rather than shipping an internal identifier.
if grep -qE "BUG-[0-9]" "$REPO_ROOT/CHANGELOG.md"; then
  echo "" >&2
  echo "ERROR: [Step 11b] Post-regeneration safety check failed: internal BUG-[0-9] token(s) found in CHANGELOG.md." >&2
  echo "  Matching lines:" >&2
  grep -nE "BUG-[0-9]" "$REPO_ROOT/CHANGELOG.md" >&2
  echo "  The changelog_writer safety pass should strip these. Investigate changelog_writer.py." >&2
  exit 1
fi
echo "  [Step 11b] Safety pass: no internal BUG-[0-9] tokens in regenerated CHANGELOG.md."

git_step "git add CHANGELOG.md" git -C "$REPO_ROOT" add "CHANGELOG.md"
# Idempotency: CHANGELOG.md already committed from a prior run — detect via exit code,
# not git prose. An empty index after git add means nothing changed.
if git -C "$REPO_ROOT" diff --cached --quiet 2>/dev/null; then
  echo "[Step 11b] step 11b: already complete, continuing — CHANGELOG.md already committed from prior run (no staged changes)."
else
  _commit_cl_out=""
  _commit_cl_rc=0
  _commit_cl_out="$(git -C "$REPO_ROOT" commit -m "Update CHANGELOG.md for ${ACTIVE_RC}" 2>&1)" || _commit_cl_rc=$?
  if [[ $_commit_cl_rc -ne 0 ]]; then
    echo "" >&2
    echo "ERROR: git commit CHANGELOG.md failed (rc=${_commit_cl_rc})" >&2
    echo "$_commit_cl_out" >&2
    exit 1
  else
    echo "$_commit_cl_out"
  fi
fi

fi  # end: skip if CHANGELOG.md is byte-identical to fresh render

# --- Step 11c: Add CHANGELOG.md reference to README.md (one-time) ---
# If the project README does not already mention CHANGELOG.md, append a
# pointer so readers know where to find release history.
if [[ -f "$REPO_ROOT/README.md" ]]; then
  if ! grep -q "CHANGELOG.md" "$REPO_ROOT/README.md"; then
    printf '\nSee CHANGELOG.md for release history.\n' >> "$REPO_ROOT/README.md"
    git_step "git add README.md (changelog ref)" git -C "$REPO_ROOT" add "README.md"
    git_step "git commit README.md (changelog ref)" \
      git -C "$REPO_ROOT" commit -m "Add CHANGELOG.md reference to README.md"
    echo "  README.md updated with CHANGELOG.md reference."
  else
    echo "  README.md already references CHANGELOG.md — skipping."
  fi
fi

# --- Step 11d: Write VERSION file into the release commit ---
# Writes the clean bare release version (e.g. v1.23.9) to $REPO_ROOT/VERSION and
# stages it into a dedicated release commit alongside CHANGELOG.md.  This makes
# every checkout, clone, zip download, and tag carry the committed version without
# requiring a git-describe or any stamping step.
#
# Content: the bare version string (prefix stripped), single line, one trailing newline.
# Idempotency: byte-compare the on-disk file against the expected content; skip
# write and commit when already byte-identical (re-runs are clean no-ops).
#
# VERSION_DETAIL remains tool-written at deploy time by stamp_version_files — do not
# touch it here.
echo "[Step 11d] Writing VERSION for ${ACTIVE_RC}..."
_CLEAN_RELEASE_VERSION="$(pp_strip_prefix_from_tag "$PROJECT_NAME" "$ACTIVE_RC" 2>/dev/null)" || _CLEAN_RELEASE_VERSION="$ACTIVE_RC"
_version_expected_content="$(printf '%s\n' "$_CLEAN_RELEASE_VERSION")"

# Byte-compare: skip write when the on-disk file already matches.
_version_needs_write=1
if [[ -f "$REPO_ROOT/VERSION" ]]; then
  _version_on_disk="$(cat "$REPO_ROOT/VERSION" 2>/dev/null || true)"
  if [[ "$_version_on_disk" == "$_version_expected_content" ]]; then
    _version_needs_write=0
  fi
fi

if [[ $_version_needs_write -eq 0 ]]; then
  # File already matches; check whether it is already staged/committed.
  git_step "git add VERSION (idempotency)" git -C "$REPO_ROOT" add "VERSION"
  if git -C "$REPO_ROOT" diff --cached --quiet 2>/dev/null; then
    echo "[Step 11d] step 11d: already complete, continuing — VERSION is byte-identical and already committed (no staged changes)."
  else
    _commit_ver_out=""
    _commit_ver_rc=0
    _commit_ver_out="$(git -C "$REPO_ROOT" commit -m "Add committed VERSION for ${ACTIVE_RC}" 2>&1)" || _commit_ver_rc=$?
    if [[ $_commit_ver_rc -ne 0 ]]; then
      echo "" >&2
      echo "ERROR: git commit VERSION failed (rc=${_commit_ver_rc})" >&2
      echo "$_commit_ver_out" >&2
      exit 1
    else
      echo "$_commit_ver_out"
      echo "  [Step 11d] VERSION committed (was already correct on disk; staged and committed)."
    fi
  fi
else
  # Write the version string to disk.
  printf '%s\n' "$_CLEAN_RELEASE_VERSION" > "$REPO_ROOT/VERSION"
  echo "  VERSION written: $_CLEAN_RELEASE_VERSION"

  git_step "git add VERSION" git -C "$REPO_ROOT" add "VERSION"
  if git -C "$REPO_ROOT" diff --cached --quiet 2>/dev/null; then
    echo "[Step 11d] step 11d: already complete, continuing — VERSION already committed from prior run (no staged changes)."
  else
    _commit_ver_out=""
    _commit_ver_rc=0
    _commit_ver_out="$(git -C "$REPO_ROOT" commit -m "Add committed VERSION for ${ACTIVE_RC}" 2>&1)" || _commit_ver_rc=$?
    if [[ $_commit_ver_rc -ne 0 ]]; then
      echo "" >&2
      echo "ERROR: git commit VERSION failed (rc=${_commit_ver_rc})" >&2
      echo "$_commit_ver_out" >&2
      exit 1
    else
      echo "$_commit_ver_out"
      echo "  [Step 11d] VERSION committed for ${ACTIVE_RC}."
    fi
  fi
fi

# --- Step 9: main is NOT pushed here ---
# The release commits on main stay local until the operator runs cm-finalize-release.sh.
# Operator will push main (and the tag created in Step 12) as the human-gated final step.
echo "[Step 9] main is staged locally — NOT pushing to origin (operator will push via cm-finalize-release.sh)."

# --- Step 10: git push origin --delete rc/<ACTIVE_RC> ---
# Gated by push_to_remote flag: skipped when push_to_remote=false.
# Idempotency: if the RC branch is already absent on origin (e.g., deleted in a prior run),
# treat that as success and continue with an audit log line.
echo "[Step 10] Deleting $RC_BRANCH on origin..."
if [[ "$_CM_PUSH_TO_REMOTE" == "true" ]]; then
  if ! git -C "$REPO_ROOT" ls-remote --exit-code --heads origin "$RC_BRANCH" >/dev/null 2>&1; then
    echo "[Step 10] step 10: already complete, continuing — $RC_BRANCH is already absent on origin (deleted in prior run or never pushed)."
  else
    git_step "git push origin --delete $RC_BRANCH" git -C "$REPO_ROOT" push origin --delete "$RC_BRANCH"
  fi
else
  echo "[push_to_remote=false] skipping origin push for ${PROJECT_NAME}: git push origin --delete $RC_BRANCH (Step 10)"
fi

# --- Step 11: git branch -D rc/<ACTIVE_RC> ---
# Idempotency: if the local RC branch is already absent (deleted in a prior run),
# treat that as success and continue with an audit log line.
echo "[Step 11] Deleting local branch $RC_BRANCH..."
if ! git -C "$REPO_ROOT" rev-parse --verify "refs/heads/$RC_BRANCH" >/dev/null 2>&1; then
  echo "[Step 11] step 11: already complete, continuing — local branch $RC_BRANCH is already absent (deleted in prior run)."
else
  git_step "git branch -D $RC_BRANCH" git -C "$REPO_ROOT" branch -D "$RC_BRANCH"
fi

# --- Step 12: Update project-scoped release-state.md (canonical format) ---
# Only reached here if ALL git operations above succeeded.
# Write the complete file from a here-doc; no post-hoc sed or regex modifications.
# Fields written: Active RC, RC Opened At, RC Opened By Task, Last Released.
# Last Released is set to the just-shipped version (ACTIVE_RC, normalized to vX.Y.Z)
# so that drain.py's rc:vX.Y.Z branch can read it with semver >=.
# Written directly to the live install path — dev tree team/release-state.md is NOT touched.
echo "[Step 12] Updating project-scoped release-state.md..."

cat > "$RELEASE_STATE" <<EOF
# Release State

## Active RC
none

## RC Opened At
none

## RC Opened By Task
none

## Last Released
${ACTIVE_RC}
EOF

echo "  release-state.md updated: Active RC -> none, Last Released -> ${ACTIVE_RC}"
echo "  Path: $RELEASE_STATE"

# --- Step 12 post-write: Append auto-resolve audit trail (if any) ---
# If UD/DU conflicts were auto-resolved at Step 6, _CM_AUTORESOLVE_LOG is set.
# Step 12 just overwrote release-state.md with 'cat >', so we append the audit note now.
if [[ -n "${_CM_AUTORESOLVE_LOG:-}" ]]; then
  _ar_timestamp="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  cat >> "$RELEASE_STATE" <<EOF

## Auto-Resolve Event
Timestamp: ${_ar_timestamp}
CM Task: cm-release.sh (automated)
Action: Auto-resolved modify/delete (UD/DU) conflicts by taking main's side.
Resolved Paths:
${_CM_AUTORESOLVE_LOG}
EOF
  echo "[cm-release] Auto-resolve audit trail appended to release-state.md."
fi

# --- Step 12b: Run token usage aggregator (per-RC roll-up) ---
# Runs AFTER the squash-to-main commits (Steps 7-8) and BEFORE the tag (Step 12).
# Produces projects/<name>/usage/rc/<ACTIVE_RC>-tokens.json from every task that
# contributed to this RC (identified by ## Release Version in task README.md).
#
# The aggregator is NON-BLOCKING: if it exits non-zero, the failure is logged
# and the release continues to the tag step.  Per the autonomy principle:
# ship the release, do not stop on a metrics-collection failure.
echo "[Step 12b] Running token usage aggregator for $ACTIVE_RC..."
_agg_script="$KANBAN_ROOT/pm-agent/aggregate_tokens.py"
if [[ -f "$_agg_script" ]]; then
  (
    set +e
    python3 "$_agg_script" \
      --project "$PROJECT_NAME" \
      --rc "$ACTIVE_RC" \
      --kanban-root "$KANBAN_ROOT" \
      2>&1 | sed 's/^/[aggregate_tokens] /'
    _agg_rc=$?
    if [[ $_agg_rc -ne 0 ]]; then
      echo "[aggregate_tokens] WARNING: aggregator exited with code $_agg_rc — token roll-up may be incomplete; release continues." >&2
    fi
  ) || true
else
  echo "[Step 12b] WARNING: aggregate_tokens.py not found at $_agg_script — skipping token roll-up." >&2
fi

# --- Hook: cm-release-pre-tag ---
# Runs after squash to main and release-notes commit, before the git tag is created.
# Use for final consistency checks or generating release artifacts.
# Failure here blocks the release (tag has NOT yet been created).
# Resolution, visibility printing, and required-flag enforcement are handled by
# cm_resolve_and_enforce_hook (see team/scripts/lib/cm_release_hooks.sh).
echo "[Step 12c] Running pre-tag hook (if present)..."
CM_RESOLVED_HOOK_PATH=""
cm_resolve_and_enforce_hook "$PROJECT_NAME" "pre-tag" "$PROJECT_HOOKS_DIR" "$REPO_ROOT" || {
  cm_halt \
    "Trigger: Pre-tag in-repo hook is not executable" \
    "cm-release-pre-tag in-repo hook exists but is not executable. Fix with: chmod +x (path shown above)"
}
if [[ -n "$CM_RESOLVED_HOOK_PATH" ]]; then
  _run_release_hook "cm-release-pre-tag" "$CM_RESOLVED_HOOK_PATH"
fi

# --- Step 13: git tag <RELEASE_TAG> ---
# Tag is created HERE, after the squash commit and release-notes commit on main, so that
# `git describe --tags` on main returns the clean tag (e.g. v0.17.1) with
# no trailing commit offset.
# RELEASE_TAG is the prefixed tag name (e.g. ai_v0.31.0 in hybrid mode,
# or v0.31.0 in pure-AI mode where prefix is empty).
RELEASE_TAG="$(pp_prefix_tag "$PROJECT_NAME" "$ACTIVE_RC")"
echo "[Step 13] Tagging $RELEASE_TAG on the final commit on main..."

# Idempotency: check local tag first.
#   - Local tag exists AND points at HEAD: already done, continue (log audit line).
#   - Local tag exists AND points at a DIFFERENT commit: HALT (genuine divergence, Trigger 5b).
#   - Local tag absent: proceed to origin check, then create.
_local_tag_sha="$(git -C "$REPO_ROOT" rev-parse "${RELEASE_TAG}^{}" 2>/dev/null)" || _local_tag_sha=""
if [[ -n "$_local_tag_sha" ]]; then
  _current_head_sha="$(git -C "$REPO_ROOT" rev-parse "$MAIN_BRANCH")"
  if [[ "$_local_tag_sha" == "$_current_head_sha" ]]; then
    echo "[Step 13] step 13: already complete, continuing — local tag $RELEASE_TAG already exists and points at the expected commit ($(echo "$_local_tag_sha" | cut -c1-7))."
  else
    cm_halt \
      "Trigger 5b: Tag ${RELEASE_TAG} exists locally but points at wrong commit" \
      "Local tag ${RELEASE_TAG} points at $(echo "$_local_tag_sha" | cut -c1-7) but expected $(echo "$_current_head_sha" | cut -c1-7) ($MAIN_BRANCH HEAD). This indicates a diverged re-run. Operator must inspect and resolve."
  fi
else
  # Local tag absent: check origin (existing Trigger 7 check) before creating.
  if git -C "$REPO_ROOT" ls-remote --exit-code --tags origin "refs/tags/${RELEASE_TAG}" >/dev/null 2>&1; then
    cm_halt \
      "Trigger 7: Tag ${RELEASE_TAG} already exists on remote origin" \
      "git tag ${RELEASE_TAG} would be a duplicate — tag already exists on origin. This indicates a race condition or repeated invocation against an already-shipped version. Operator must inspect and resolve."
  fi

  # --- Pre-tag VERSION gate ---
  # Assert that VERSION on disk equals the tag about to be cut.  This is the
  # single gate that prevents a committed VERSION from lying about the release.
  # Fires AFTER VERSION is written (Step 11d) and BEFORE git tag — on mismatch
  # the release exits non-zero without creating the tag and without pushing anything.
  _pretag_version_on_disk=""
  if [[ -f "$REPO_ROOT/VERSION" ]]; then
    _pretag_version_on_disk="$(cat "$REPO_ROOT/VERSION" 2>/dev/null || true)"
  fi
  # Strip trailing newline from on-disk read for comparison (printf '%s\n' wrote one).
  _pretag_version_trimmed="${_pretag_version_on_disk%$'\n'}"
  # _CLEAN_RELEASE_VERSION was set in Step 11d; fall back to computing it here for
  # re-entry paths where Step 11d was skipped (VERSION already correct).
  _pretag_expected="${_CLEAN_RELEASE_VERSION:-$(pp_strip_prefix_from_tag "$PROJECT_NAME" "$ACTIVE_RC" 2>/dev/null || echo "$ACTIVE_RC")}"
  if [[ "$_pretag_version_trimmed" != "$_pretag_expected" ]]; then
    echo "" >&2
    echo "ERROR: [Pre-tag VERSION gate] VERSION content does not match the tag about to be created." >&2
    echo "  On-disk VERSION : '${_pretag_version_trimmed}'" >&2
    echo "  Expected (tag)  : '${_pretag_expected}'" >&2
    echo "  Release tag     : ${RELEASE_TAG}" >&2
    echo "  No tag has been created. Investigate why VERSION diverged before re-running." >&2
    exit 1
  fi
  echo "[Step 13] Pre-tag VERSION gate: OK — VERSION='${_pretag_version_trimmed}' matches tag '${_pretag_expected}'."

  _tag_rc=0
  git -C "$REPO_ROOT" tag "$RELEASE_TAG" || _tag_rc=$?
  if [[ $_tag_rc -ne 0 ]]; then
    echo "" >&2
    echo "ERROR: git tag ${RELEASE_TAG} failed (rc=${_tag_rc})" >&2
    echo "The release-state.md has NOT been modified." >&2
    exit 1
  fi
fi

# --- Step 13a: tag is NOT pushed here ---
# The tag was created locally in Step 13. The operator pushes it via
# cm-finalize-release.sh (or manually: git push origin <VERSION>).
echo "[Step 13a] Tag $RELEASE_TAG created locally — NOT pushing to origin (operator will push via cm-finalize-release.sh)."

# --- Hook: cm-release-post-tag ---
# Runs after the tag is created (tag has NOT yet been pushed to origin — push
# is best-effort at Step 14).  Use for external notifications, asset uploads,
# or downstream triggers.  Failure here is a LOGGED WARNING ONLY and does NOT
# block the release; the tag already exists locally and will be pushed at Step 14.
# Resolution, visibility printing, and required-flag enforcement are handled by
# cm_resolve_and_enforce_hook (see team/scripts/lib/cm_release_hooks.sh).
echo "[Step 13b] Running post-tag hook (if present)..."
CM_RESOLVED_HOOK_PATH=""
cm_resolve_and_enforce_hook "$PROJECT_NAME" "post-tag" "$PROJECT_HOOKS_DIR" "$REPO_ROOT" || {
  echo "[cm-release] WARNING: post-tag in-repo hook exists but is not executable — skipping (post-tag does not block release)" >&2
}
if [[ -n "$CM_RESOLVED_HOOK_PATH" ]]; then
  _run_release_hook "cm-release-post-tag" "$CM_RESOLVED_HOOK_PATH" --no-block
fi

# --- Step 13c: Promote bundled items from running -> done ---
# This step runs ONLY after all git operations above have succeeded (including
# tag creation). It is on the success path only — any git_step failure above
# would have exited before reaching here.
#
# Resolution: locate the requirements bundle file that produced this RC, then:
#   (a) Promote each referenced bundled item's ## Status from running to done.
#   (b) Promote the bundle requirements file's own ## Status from running to done.
#
# (b) is the complement of the discovery pipeline's idempotency mechanism
# (discovery.sh "Idempotency invariant").  Once the bundle file is marked 'done',
# discovery_step_requirements will skip it forever without any pm_backlog lookup.
#
# Only files whose ## Status is currently "running" are touched. Files at
# "open" or "done" (or any other value) are skipped.
echo "[Step 13c] Promoting bundled items and bundle file from 'running' to 'done'..."

# Locate the project root for the active project (where requirements/ lives).
_cm_project_name="$PROJECT_NAME"
_cm_project_root="$(KANBAN_ROOT="$KANBAN_ROOT" pp_project_root "$_cm_project_name" 2>/dev/null)" || _cm_project_root=""
_cm_requirements_dir="${_cm_project_root}/requirements"

_cm_bundle_file=""
if [[ -n "$_cm_project_root" && -d "$_cm_requirements_dir" ]]; then
  # Match any requirements file whose name starts with "${ACTIVE_RC}-".
  # This covers both auto-generated bundle files (e.g. v0.22.0-bugfix-bundle-20260510.md)
  # AND operator-authored files (e.g. v0.22.0-workflow-types-foundation.md).
  # The glob "${ACTIVE_RC}-*.md" matches both auto-generated bundle files (e.g.
  # v0.22.0-bugfix-bundle-20260510.md) and operator-authored files (e.g.
  # v0.22.0-workflow-types-foundation.md) regardless of whether they contain "bundle".
  while IFS= read -r -d '' _f; do
    _cm_bundle_file="$_f"
    break
  done < <(find "$_cm_requirements_dir" -maxdepth 1 -type f \
            -name "${ACTIVE_RC}-*.md" -print0 2>/dev/null | sort -z)
fi

if [[ -z "$_cm_bundle_file" ]]; then
  echo "  WARNING: no requirements file found for $ACTIVE_RC in $_cm_requirements_dir" >&2
  echo "  The source requirements file's ## Status will remain at 'running'." >&2
  echo "  Run migrate-bug-status-done.sh to recover, or promote ## Status manually." >&2
else
  echo "  Requirements file: $_cm_bundle_file"
  # Promote the source requirements file's ## Status from 'running' to 'done' (Step b),
  # and — only if the file contains a ## Bundled Items section — also promote each
  # referenced item's ## Status from 'running' to 'done' (Step a).
  #
  # Operator-authored files without a ## Bundled Items section get ONLY their own
  # Status promoted. No item iteration is attempted on such files.
  #
  # Promotion logic lives in promote_bundled_items.py so it is importable
  # by the regression test suite (test_step16b_bundled_bug_status_done.py).
  # The script is located alongside this file in team/scripts/cm/.
  _cm_promote_script="$(dirname "${BASH_SOURCE[0]}")/promote_bundled_items.py"
  if [[ ! -f "$_cm_promote_script" ]]; then
    echo "  ERROR: promote_bundled_items.py not found at $_cm_promote_script" >&2
    echo "  Step 13c cannot promote bundled items. Check your installation." >&2
    # Non-fatal: release is already locally complete (tag created). Log and continue.
    echo "  WARNING: bundled item ## Status fields NOT promoted. Run promote_bundled_items.py manually." >&2
  else
    # Pass tasks_dir and bugs_dir so the derivation path can promote bugs that
    # shipped in this release but were not enumerated in ## Bundled Items.
    # Both paths must exist as directories; if either is absent, the derivation path
    # is disabled inside promote_bundled_items.py (a WARN is emitted and the script
    # falls back to the Bundled Items enumeration path only).
    _cm_tasks_dir="${_cm_project_root}/tasks"
    _cm_bugs_dir="${_cm_project_root}/bugs"
    python3 "$_cm_promote_script" "$_cm_bundle_file" "$ACTIVE_RC" \
      "$_cm_tasks_dir" "$_cm_bugs_dir"
  fi
fi

# --- Step 13d: Record shipped state in per-RC release-state JSON ---
# Fires unconditionally after the local tag is created (Step 13), the canonical
# shipped signal per this script's own comments.  This ensures closed_at is
# populated for every successful release regardless of push_to_remote mode.
#
# The write_rc_state.py ship call fires here (keyed off the local tag) so that
# closed_at is populated for every successful release regardless of push_to_remote
# mode.  Step-14 writes ship state a second time after origin push when
# push_to_remote=true; write_ship() is idempotent so the double write is harmless.
#
# For push_to_remote=true releases the Step-14 branch writes ship state a second
# time after the push succeeds; write_ship() is idempotent (it always overwrites
# closed_at and outcome), so the double write is harmless.
#
# Non-blocking: any failure is logged as a warning; the release continues.
echo "[Step 13d] Recording shipped state in per-RC release-state JSON (closed_at + outcome=shipped)..."
_rc_state_dir="${KANBAN_ROOT}/projects/${PROJECT_NAME}/release-state"
_rc_state_json="${_rc_state_dir}/${ACTIVE_RC}.json"
_closed_at_utc="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
mkdir -p "$_rc_state_dir" 2>/dev/null || true
python3 "$KANBAN_ROOT/pgai_agent_kanban/cm/write_rc_state.py" ship \
    "$_rc_state_json" "$ACTIVE_RC" "$_closed_at_utc" 2>&1 || \
  echo "[cm-release] WARNING: could not update per-RC release-state JSON at $_rc_state_json" >&2

# --- Step 14: Best-effort auto-push of main and tags to origin ---
# All critical release work is complete above this line (squash commit,
# tag, RC branch deleted, release-state.md updated).  Attempt to
# push main and tags so upgrade.sh sees the new release without operator intervention.
#
# Both pushes are best-effort: the script ALWAYS exits 0 after a successful release,
# regardless of push outcome.  Push failures are non-fatal — the release is real and
# complete locally; the operator can push manually later.
#
# Branch context is explicit: we push origin main from the main branch (already
# checked out above) rather than relying on whatever branch happened to be active.
#
# Gated by push_to_remote flag: when push_to_remote=false, the entire auto-push
# block is skipped and the release is considered complete locally.
echo "[Step 14] Attempting best-effort auto-push of main and tags to origin..."
if [[ "$_CM_PUSH_TO_REMOTE" == "true" ]]; then
(
  set +e
  # Push main (or prefixed equivalent) — capture output to variable so push-result marker can detect UP-TO-DATE.
  # $? after a simple command substitution captures git's exit code directly.
  _push_main_out=$(git -C "$REPO_ROOT" push origin "$MAIN_BRANCH" 2>&1); _push_main_rc=$?
  printf '%s\n' "$_push_main_out" | sed 's/^/[cm-release auto-push] /'
  if [[ $_push_main_rc -ne 0 ]]; then
    echo "[cm-release auto-push] Push to origin $MAIN_BRANCH failed (rc=$_push_main_rc). Retrying in 3 seconds..."
    sleep 3
    _push_main_out=$(git -C "$REPO_ROOT" push origin "$MAIN_BRANCH" 2>&1); _push_main_rc=$?
    printf '%s\n' "$_push_main_out" | sed 's/^/[cm-release auto-push retry] /'
  fi
  # Emit single grep-able push-result marker for Step 14 main push.
  if [[ $_push_main_rc -ne 0 ]]; then
    _push_main_outcome=FAILED
  elif printf '%s\n' "$_push_main_out" | grep -q "Everything up-to-date"; then
    _push_main_outcome=UP-TO-DATE
  else
    _push_main_outcome=PUSHED
  fi
  echo "[cm-release push-result] target=$MAIN_BRANCH rc=${ACTIVE_RC} outcome=${_push_main_outcome} outcome_code=${_push_main_rc}"

  # Push tags — same output-capture pattern and one-time retry.
  _push_tags_out=$(git -C "$REPO_ROOT" push origin --tags 2>&1); _push_tags_rc=$?
  printf '%s\n' "$_push_tags_out" | sed 's/^/[cm-release auto-push] /'
  if [[ $_push_tags_rc -ne 0 ]]; then
    echo "[cm-release auto-push] Push of tags failed (rc=$_push_tags_rc). Retrying in 3 seconds..."
    sleep 3
    _push_tags_out=$(git -C "$REPO_ROOT" push origin --tags 2>&1); _push_tags_rc=$?
    printf '%s\n' "$_push_tags_out" | sed 's/^/[cm-release auto-push retry] /'
  fi
  # Emit single grep-able push-result marker for Step 14 tags push.
  if [[ $_push_tags_rc -ne 0 ]]; then
    _push_tags_outcome=FAILED
  elif printf '%s\n' "$_push_tags_out" | grep -q "Everything up-to-date"; then
    _push_tags_outcome=UP-TO-DATE
  else
    _push_tags_outcome=PUSHED
  fi
  echo "[cm-release push-result] target=tags rc=${ACTIVE_RC} outcome=${_push_tags_outcome} outcome_code=${_push_tags_rc}"

  if [[ $_push_main_rc -ne 0 || $_push_tags_rc -ne 0 ]]; then
    echo "[cm-release auto-push] WARNING: one or more push attempts failed after retry (main rc=$_push_main_rc, tags rc=$_push_tags_rc)."
    echo "[cm-release auto-push] The release is complete locally. The watchdog should catch the remaining work."
  else
    echo "[cm-release auto-push] main and tags pushed successfully."

    # --- Write/update per-RC release-state JSON with shipped outcome ---
    # Only written after git push --tags succeeds (the authoritative shipped signal).
    # Reads existing opened_at from the file if present (written by open-rc.sh).
    # Non-blocking: any failure is logged as a warning; release continues.
    _rc_state_dir="${KANBAN_ROOT}/projects/${PROJECT_NAME}/release-state"
    _rc_state_json="${_rc_state_dir}/${ACTIVE_RC}.json"
    _closed_at_utc="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    mkdir -p "$_rc_state_dir" 2>/dev/null || true
    python3 "$KANBAN_ROOT/pgai_agent_kanban/cm/write_rc_state.py" ship \
        "$_rc_state_json" "$ACTIVE_RC" "$_closed_at_utc" 2>&1 || \
      echo "[cm-release] WARNING: could not update per-RC release-state JSON at $_rc_state_json" >&2
  fi
) || true
else
  echo "[push_to_remote=false] skipping origin push for ${PROJECT_NAME}: auto-push of $MAIN_BRANCH and tags (Step 14)"
fi

# --- Step 15: Metrics aggregation and CSV append ---
# This is the final step in the release lifecycle.  It runs AFTER all git
# operations (squash, tag, push) and AFTER status.md is updated (Step 12).
#
# Two sub-steps:
#   15a. Invoke metrics_aggregator.py to write the per-RC JSON rollup at
#        projects/<name>/metrics/rc/<ACTIVE_RC>.json.
#   15b. Invoke metrics_csv_writer.py to append one row to the cumulative
#        history CSV at projects/<name>/metrics/history.csv.
#
# Both sub-steps are NON-BLOCKING: any failure is captured to stderr with a
# [metrics] WARNING prefix and the script continues to exit 0.  The release
# is complete regardless of whether metrics generation succeeds.
#
# Idempotency: aggregate_rc() fully recomputes from source tokens.json files
# (deterministic JSON, atomic write via os.replace).  append_rc_row() skips
# duplicate rows inside an exclusive flock, so running twice on the same RC
# produces at most one history.csv row.
#
# Arguments are passed explicitly: --project and --rc (or equivalent).
# Neither script is allowed to infer the project name from cwd.
echo "[Step 15] Running metrics aggregation for $ACTIVE_RC (non-blocking)..."

_metrics_agg_script="$KANBAN_ROOT/scripts/lib/metrics_aggregator.py"
_metrics_csv_script="$KANBAN_ROOT/scripts/lib/metrics_csv_writer.py"
_metrics_rc_json=""  # set if 15a succeeds; used by 15b

# 15a: per-RC JSON rollup
if [[ -f "$_metrics_agg_script" ]]; then
  _metrics_agg_rc=0
  (
    set +e
    python3 "$_metrics_agg_script" \
      --project "$PROJECT_NAME" \
      --rc      "$ACTIVE_RC" \
      --kanban-root "$KANBAN_ROOT" \
      2>&1 | sed 's/^/[metrics] /'
    exit "${PIPESTATUS[0]}"
  ) || _metrics_agg_rc=$?
  if [[ $_metrics_agg_rc -ne 0 ]]; then
    echo "[metrics] WARNING: metrics_aggregator.py exited with code $_metrics_agg_rc — per-RC JSON rollup may be incomplete; release continues." >&2
  else
    # Derive the rollup path from the known output convention so 15b can read it.
    _cm_proj_dir="$(KANBAN_ROOT="$KANBAN_ROOT" pp_project_root "$PROJECT_NAME" 2>/dev/null)" || _cm_proj_dir=""
    if [[ -n "$_cm_proj_dir" ]]; then
      _metrics_rc_json="${_cm_proj_dir}/metrics/rc/${ACTIVE_RC}.json"
      if [[ ! -f "$_metrics_rc_json" ]]; then
        echo "[metrics] WARNING: expected rollup file not found after aggregation: $_metrics_rc_json" >&2
        _metrics_rc_json=""
      fi
    fi
  fi
else
  echo "[Step 15a] WARNING: metrics_aggregator.py not found at $_metrics_agg_script — skipping per-RC JSON rollup." >&2
fi

# 15b: append row to cumulative history.csv
if [[ -n "$_metrics_rc_json" && -f "$_metrics_csv_script" ]]; then
  _cm_proj_dir_csv="$(KANBAN_ROOT="$KANBAN_ROOT" pp_project_root "$PROJECT_NAME" 2>/dev/null)" || _cm_proj_dir_csv=""
  if [[ -n "$_cm_proj_dir_csv" ]]; then
    _history_csv="${_cm_proj_dir_csv}/metrics/history.csv"
    _metrics_csv_rc=0
    (
      set +e
      python3 "$_metrics_csv_script" \
        --csv-path    "$_history_csv" \
        --rollup-json "$_metrics_rc_json" \
        2>&1 | sed 's/^/[metrics] /'
      exit "${PIPESTATUS[0]}"
    ) || _metrics_csv_rc=$?
    if [[ $_metrics_csv_rc -ne 0 ]]; then
      echo "[metrics] WARNING: metrics_csv_writer.py exited with code $_metrics_csv_rc — history.csv row may not have been appended; release continues." >&2
    fi
  else
    echo "[Step 15b] WARNING: could not resolve project directory for '$PROJECT_NAME' — skipping history.csv append." >&2
  fi
elif [[ -z "$_metrics_rc_json" ]]; then
  echo "[Step 15b] WARNING: per-RC JSON rollup was not written — skipping history.csv append." >&2
else
  echo "[Step 15b] WARNING: metrics_csv_writer.py not found at $_metrics_csv_script — skipping history.csv append." >&2
fi

# --- Step 16: Success-gated RC temp cleanup (non-blocking) ---
# Runs ONLY here on the explicit success path — ALL git operations, tag, state
# updates, bundled-item promotion, and metrics are complete above this line.
# MUST NOT be placed in cleanup_on_exit / trap EXIT (those fire on failure too).
#
# Step 20a: git worktree prune — removes stale worktree refs from the dev tree.
#
# Step 20b (FS straggler-sweep) is intentionally ABSENT.
# The old Step 20b enumerated a worktree base directory and deleted any subdir
# not registered in git's worktree list.  With all projects sharing a flat
# $(pgai_temp_dir)/worktrees/ namespace that sweep could reach and delete
# another project's in-flight worktrees — cross-project data loss.
#
# The fix: repo-scoped git worktree prune (Step 20a) is sufficient to clean
# stale git refs.  Per-task teardown (pgai_worktree_teardown) removes the
# on-disk directory when each task completes normally.  Stale on-disk dirs from
# abnormally-terminated tasks can be cleared by the operator via
#   pgai_temp_cleanup $(pgai_worktree_path <task_id>)
# targeting only THIS project's subtree.  No code path here enumerates a
# directory that can contain another project's temp.
echo "[Step 16] Success-gated RC temp cleanup (non-blocking)..."
(
  set +e
  # 16a: prune stale worktree refs from the git registry (repo-scoped; safe).
  echo "[Step 16a] Running git worktree prune on $REPO_ROOT..."
  git -C "$REPO_ROOT" worktree prune 2>&1 | sed 's/^/[worktree prune] /' || \
    echo "[Step 16a] WARNING: git worktree prune exited non-zero — continuing." >&2
  echo "[Step 16a] git worktree prune complete (no FS sweep — safe by construction)."
) || true

# --- Step 16b: Stamp ## Fixed In: <ACTIVE_RC> on every bug that shipped in this release ---
# Iterates bugs_dir (the project-scoped bug ledger) and writes ## Fixed In: <ACTIVE_RC>
# to any bug file whose ## Status is 'done' (promoted in Step 13c) and whose
# ## Fixed In field is currently absent or empty.  Bugs already carrying a ## Fixed In
# value are left unchanged — never overwrite a set field.
#
# Non-blocking: any Python error is logged and the release summary continues.
echo "[Step 16b] Stamping ## Fixed In: ${ACTIVE_RC} on bugs closed in this release..."
(
  set +e
  _16b_bugs_dir="${_cm_bugs_dir:-${_CM_PROJECT_ROOT}/bugs}"
  _16b_kanban_root="${KANBAN_ROOT}"
  python3 - "$_16b_bugs_dir" "$ACTIVE_RC" "$_16b_kanban_root" \
    <<'FIXED_IN_PY'
import sys
import pathlib
import importlib.util
import re

bugs_dir    = pathlib.Path(sys.argv[1])
active_rc   = sys.argv[2]
kanban_root = pathlib.Path(sys.argv[3])

# Import parse_bug_file from the changelog_writer library.
_writer_path = kanban_root / "pgai_agent_kanban" / "cm" / "changelog_writer.py"
_spec = importlib.util.spec_from_file_location("changelog_writer", _writer_path)
_mod  = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

if not bugs_dir.exists():
    print(f"[Step 16b] bugs_dir not found: {bugs_dir} — skipping Fixed-In writeback.")
    sys.exit(0)

stamped = 0
skipped_already_set = 0
skipped_not_done = 0

for bug_path in sorted(bugs_dir.glob("BUG-*.md"), key=lambda p: p.name):
    try:
        rec = _mod.parse_bug_file(bug_path)
    except Exception as exc:
        print(f"[Step 16b] WARNING: could not parse {bug_path.name}: {exc}", flush=True)
        continue

    # Only stamp bugs whose status is 'done' in this release.
    if rec.status != "done":
        skipped_not_done += 1
        continue

    # If Fixed In is already set, do not overwrite.
    if rec.fixed_in:
        skipped_already_set += 1
        continue

    # Write ## Fixed In: <ACTIVE_RC> into the bug file.
    text = bug_path.read_text(encoding="utf-8")
    section_pattern = re.compile(
        r"(^##\s+Fixed In\s*$)([\s\S]*?)(?=^##\s+|\Z)",
        re.MULTILINE,
    )
    match = section_pattern.search(text)
    if match:
        # Section exists but body is empty — fill it in.
        existing_body = match.group(2)
        content_clean = re.sub(r"<!--[\s\S]*?-->", "", existing_body).strip()
        if not content_clean:
            comment_match = re.search(r"<!--[\s\S]*?-->", existing_body)
            if comment_match:
                insert_pos = match.start(2) + comment_match.end()
                new_text = text[:insert_pos] + f"\n{active_rc}\n" + text[insert_pos:]
            else:
                heading_end = match.start(2)
                new_text = (
                    text[:heading_end]
                    + f"\n{active_rc}\n"
                    + text[heading_end:].lstrip("\n")
                )
            bug_path.write_text(new_text, encoding="utf-8")
            stamped += 1
        else:
            # Already has content (race condition or prior partial run).
            skipped_already_set += 1
    else:
        # Section absent — append at end of file.
        new_text = text.rstrip("\n") + f"\n\n## Fixed In\n{active_rc}\n"
        bug_path.write_text(new_text, encoding="utf-8")
        stamped += 1

print(
    f"[Step 16b] Fixed-In writeback: {stamped} stamped, "
    f"{skipped_already_set} already set, {skipped_not_done} not-done skipped.",
    flush=True,
)
FIXED_IN_PY
) || echo "[Step 16b] WARNING: Fixed-In writeback encountered an error — release continues." >&2

echo ""
echo "Local release preparation complete."
echo "  Version:     $ACTIVE_RC"
echo "  State file:  $RELEASE_STATE"
echo ""
echo "Summary of what was done:"
echo "  - $RC_BRANCH squashed to $MAIN_BRANCH (single-lane squash commit)"
echo "  - Post-squash fidelity gate passed: $RC_BRANCH and $MAIN_BRANCH trees are identical"
echo "  - Tag $RELEASE_TAG created locally and best-effort pushed to origin"
echo "  - $RC_BRANCH deleted from origin and locally"
echo "  - release-state.md updated: Active RC -> none"
echo "  - release-notes/${ACTIVE_RC}.md generated and committed on $MAIN_BRANCH"
echo "  - bundled items promoted from 'running' to 'done' (Step 13c)"
echo "  - ## Fixed In: ${ACTIVE_RC} stamped on bugs that shipped in this release (Step 16b)"
echo "  - metrics aggregation + history.csv append attempted (Step 15, non-blocking)"
echo "  - auto-push of $MAIN_BRANCH and tags attempted (Step 14, best-effort — see [cm-release auto-push] lines above)"
echo "  - RC temp cleanup attempted (Step 16, non-blocking — git worktree prune; no FS sweep by design)"
echo ""
echo "## Next Recommended Step"
echo "If the auto-push above succeeded, the release is complete on origin."
echo "If the auto-push failed, push manually from a shell where the PreToolUse hook does not apply:"
echo "    git push origin $MAIN_BRANCH"
echo "    git push origin ${RELEASE_TAG}"
echo "Or run the convenience script:"
echo "    bash ${KANBAN_ROOT}/scripts/cm/finalize-release.sh"
echo "The release is complete locally."
exit 0
