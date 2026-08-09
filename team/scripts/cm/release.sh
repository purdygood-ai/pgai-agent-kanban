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
# Behavior (ship sequence — verification runs to completion before any
# destructive step; see "Verify before destroy" below):
#   1.  git fetch origin --tags
#   2.  Find active RC branch (rc/*) and validate it exists
#   3.  Validate rc/<ACTIVE_RC> exists locally and on origin; re-push if origin absent
#       but local ref present (resume from interrupted-before-tag state)
#   4.  Refuse if ACTIVE_RC == none
#   4a. git checkout rc/<ACTIVE_RC> — so every RC-scoped step below (hooks
#       included) reads and writes the RC's own working tree, regardless of
#       which branch the dev tree had checked out when this script started.
#   4b. Pre-squash hook (if configured); HALT on failure.
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
#   12b. Run token usage aggregator (non-blocking).
#   12c. Pre-tag hook (if configured); HALT on failure. Tag has NOT been created yet.
#   13. Pre-tag VERSION gate (VERSION on disk must equal the tag about to be cut),
#       then git tag <ACTIVE_RC> — the LAST verification-gated step. Tag points
#       to the final housekeeping commit on main; after this tag exists,
#       pp_last_released_version returns <ACTIVE_RC>.
#   13a. Log: tag created locally, not pushed yet.
#   13b. Post-tag hook (if configured); failure is a logged warning only.
#   --- verification complete — proceeding to finalization (see below) ---
#   9.  git push origin --delete rc/<ACTIVE_RC>
#   10.5. Force-remove (git worktree remove --force) any linked worktree
#       still checked out on rc/<ACTIVE_RC> so a leftover
#       per-task worktree never blocks the branch deletion below. No-op
#       when no such worktree exists.
#   10. git branch -D rc/<ACTIVE_RC>
#   11. Update project-scoped release-state.md (live install) — Active RC cleared
#       Last Released* fields are NOT written; the git tag is the canonical record.
#   13c. Promote bundled items from 'running' to 'done'
#   14. Best-effort auto-push of main and tags to origin
#       Push failures are non-fatal — script always exits 0 if release shipped locally
#   15-16b. Metrics aggregation, RC temp cleanup, bug Fixed-In stamping (all non-blocking)
#   Print "Next Recommended Step" block and exit 0
#
# Verify before destroy: every gate that can veto the release (pre-squash
# hook, squash pollution guard, fidelity gate, pre-tag hook, pre-tag VERSION
# gate, the tag itself) runs BEFORE any destructive step. The only destructive
# steps in this script — RC branch deletion (origin and local, Steps 9-10) and
# the release-state.md rewrite (Step 11) — are placed strictly after the tag
# is created and the "verification complete" narration is logged. If a gate
# fires (exit non-zero) before that narration, the EXIT trap
# (cleanup_on_exit) resets local main back to the commit it was on when this
# release attempt began, so a failed gate leaves the RC branch (local and
# origin), release-state.md, and main exactly as the release attempt found
# them — the remedy is always "fix the cause, re-run the task," never manual
# recovery.
#
# HALT triggers (create $KANBAN_ROOT/projects/<name>/HALT — project-scoped,
# never the global $KANBAN_ROOT/HALT — and exit 1):
#   - TESTER state is BLOCKED (verification could not complete)
#   - TESTER systemic_risk is high
#   - Any finding Fix Effort=large in a SHIP-WITH-SERIOUS-CONCERNS context
#   - Pre-squash hook fails
#   - Squash pollution guard fires in strict mode (CM_SQUASH_STRICT_POLLUTION_GUARD=true)
#   - Squash to main has conflicts
#   - Fidelity gate fires (rc/<version> tree diverges from main after squash)
#   - Pre-tag hook fails, or the pre-tag VERSION gate does not match
#   - Push to origin fails after retries (checked in Step 14)
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
# Safety: release-state.md and the RC branch are ONLY mutated after all
# verification gates and the tag have succeeded (see "Verify before destroy"
# above). On any git failure, the script halts with a message naming the
# failed step.
#
# Hook resolution: pre-squash, pre-tag, and post-tag in-repo hooks
# (<dev_tree_path>/.pgai/hooks/cm-release-<phase>.sh) and cfg-declared
# relative-path hooks are resolved from the RC branch's own git tree, not
# from whatever the dev tree currently has checked out — hook discovery is a
# function of the release being shipped, never of an operator's or a prior
# script step's working-tree state. See team/scripts/lib/cm_release_hooks.sh.
# Hook execution: Step 4a checks out the RC branch before any hook runs, so a
# hook's own git operations (e.g. a version-bump commit) land on the RC
# branch and ride the squash into main, regardless of which branch the dev
# tree had checked out when this script started. Hook discovery being
# RC-scoped does not by itself guarantee this — a discovered hook still
# executes wherever the working tree happens to be checked out unless that
# checkout is forced first.
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
     (a \$KANBAN_ROOT/projects/<name>/HALT file is created on HALT
     conditions — project-scoped; the global \$KANBAN_ROOT/HALT is never
     written by this script)

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

# --- Source the shared dual-channel error-contract emitter ---
# Every fail-loud site below (cm_halt, the blocking-hook-failure path in
# _run_release_hook) speaks both channels through this one primitive: see
# team/scripts/lib/error_contract.sh for the house shape.
# shellcheck source=lib/error_contract.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/error_contract.sh"

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

# --- Resolve the project-scoped HALT file path ---
# Release-flow HALT triggers (this script) freeze projects/<name>/HALT ONLY —
# never $KANBAN_ROOT/HALT. The global HALT is reserved for the operator and
# for framework-wide emergencies (see OVERVIEW.md, SOP.md "HALT Flags"); a
# single project's release failure must never freeze sibling projects' chains.
# wake_common.sh's dispatch loop checks $KANBAN_ROOT/HALT once, globally,
# before iterating projects (fast exit for the whole chain) and separately
# checks pp_project_halted() per project inside the loop — writing here at
# project scope is what keeps a release failure from tripping that global
# fast-exit and starving every other project's chain.
PROJECT_HALT_FILE="${_CM_PROJECT_ROOT}/HALT"

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
# This is the ONLY EXIT trap for the whole script (do not add another `trap ...
# EXIT` further down — it would silently replace this one and disable both the
# job cleanup and the rollback guard below for the remainder of the run; clean
# up any later scratch file by extending this function instead, as done below
# for _cl_buf).
#
# Rollback guard (verify-before-destroy): if the script exits non-zero after
# main has already received local commits (MAIN_SHA is set at Step 6, before
# the squash) but BEFORE all verification completed (_CM_VERIFICATION_COMPLETE
# is set once the pre-tag hook chain, the pre-tag VERSION gate, and the tag
# itself have all succeeded — see the "verification complete" narration below),
# a gate fired partway through committing release artifacts onto main. Rather
# than leave those partial commits stranded on main while re-running requires
# fixing the cause and resubmitting, reset local main back to the commit it
# was on when this release attempt began. The RC branch and release-state.md
# are never touched before verification completes (they are rewritten only in
# Steps 10-12, which run strictly after the "verification complete" narration),
# so no explicit restoration is needed for either of those.
cleanup_on_exit() {
  local exit_code=$?
  # Disable errexit for the remainder of this function. A trap handler must
  # never let one of its own housekeeping commands (an expected-to-sometimes-fail
  # "git merge --abort" when no merge is in progress, an unset scratch variable,
  # etc.) short-circuit the function under `set -e` — that would silently skip
  # the rollback guard below and reintroduce the exact stranding failure mode
  # this trap exists to prevent.
  set +e
  jobs -p 2>/dev/null | xargs -r kill 2>/dev/null
  wait 2>/dev/null

  # Scratch file from the CHANGELOG regeneration step (Step 11b); harmless if unset.
  [[ -n "${_cl_buf:-}" ]] && rm -f "$_cl_buf" 2>/dev/null

  if [[ $exit_code -ne 0 && "${_CM_VERIFICATION_COMPLETE:-0}" != "1" \
        && -n "${MAIN_SHA:-}" && -n "${REPO_ROOT:-}" && -n "${MAIN_BRANCH:-}" ]]; then
    echo "" >&2
    echo "[cm-release] Gate failure before verification completed — rolling back local ${MAIN_BRANCH} to its pre-release commit ($(echo "$MAIN_SHA" | cut -c1-7))." >&2
    git -C "$REPO_ROOT" merge --abort >/dev/null 2>&1
    git -C "$REPO_ROOT" reset --hard "$MAIN_SHA" >/dev/null 2>&1
    echo "[cm-release] Rollback complete: ${MAIN_BRANCH} restored to its pre-release state. RC branch and release-state.md were never touched." >&2
  fi

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
    # Non-deterministic (exempt): the hook is project-supplied, so its failure
    # cause lives inside the hook's own logic, not in anything release.sh can
    # inspect. Per docs/coding-standards.md "The non-deterministic exemption",
    # this failure is exempt from a numbered fix-command block but is NEVER
    # exempt from naming the next diagnostic step — the "[hook <name>]"
    # lines already printed above are that step.
    echo "[cm-release] ERROR: ${hook_name}: FAIL — hook exited non-zero (rc=$_hook_rc)" >&2
    echo "[cm-release] ERROR:" >&2
    echo "[cm-release] ERROR: Cause(s):" >&2
    echo "[cm-release] ERROR:   - ${hook_path} — project-supplied hook returned rc=$_hook_rc" >&2
    echo "[cm-release] ERROR:" >&2
    echo "[cm-release] ERROR: Next diagnostic step: read the \"[hook ${hook_name}]\"-prefixed lines printed above for this hook's own stdout/stderr — that output names the specific failure inside the hook. This gate cannot prescribe a fix command because the hook's internal logic is project-supplied; its failure cause is not knowable from release.sh." >&2
    echo "[cm-release] ERROR:" >&2
    echo "[cm-release] ERROR: Rule: a blocking hook (${hook_name}) must exit 0 for the release to proceed past this phase." >&2
    # Dual-channel error contract (team/scripts/lib/error_contract.sh), in
    # addition to the Cause(s)/Next-diagnostic-step/Rule block above: every
    # fail-loud site speaks both a stable machine key and a plain-language
    # message/remedy. The error key is the hook's own phase name, since that
    # is the documented id an operator or tool would grep for.
    pgai_emit_error_contract \
      "cm-release.hook-failure.${hook_name}" \
      "The ${hook_name} release hook exited non-zero (rc=${_hook_rc}); a blocking release hook must exit 0 for the release to proceed past this phase." \
      "Read the \"[hook ${hook_name}]\"-prefixed lines printed above for the hook's own stdout/stderr, fix the underlying cause inside ${hook_path}, then re-run cm-release.sh."
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
# Usage: write_halt_file <error_key> <message> <remedy>
# Creates the project-scoped HALT file ($PROJECT_HALT_FILE, i.e.
# $KANBAN_ROOT/projects/<name>/HALT) with a timestamp and the dual-channel
# error contract (error/message/remedy — see team/scripts/lib/error_contract.sh)
# so an operator reading the HALT file alone, with no ledger open, sees what
# stopped, why, and what to do next. Deliberately project-scoped, not global:
# a release failure for this project must not freeze sibling projects' chains.
# Does NOT create the HALT file if it already exists (preserves earlier halt reason).
write_halt_file() {
  local error_key="$1"
  local message="$2"
  local remedy="$3"
  local halt_file="${PROJECT_HALT_FILE}"
  local timestamp
  timestamp="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  if [[ -f "$halt_file" ]]; then
    echo "[cm-release] HALT file already exists at $halt_file — not overwriting." >&2
    echo "[cm-release] Existing HALT file content:" >&2
    cat "$halt_file" >&2
    return 0
  fi
  local contract
  contract="$(pgai_format_error_contract "$error_key" "$message" "$remedy")"
  {
    echo "# HALT created by CM at ${timestamp}"
    # Comment-prefix every line of the house-shape block so the file stays a
    # readable shell/markdown comment block, matching the pre-existing
    # "# Reason: ..." convention this replaces.
    while IFS= read -r _contract_line; do
      echo "# ${_contract_line}"
    done <<< "$contract"
    echo "# Resolution: Operator review required. See CM task status.md for full reason."
  } > "$halt_file"
  echo "[cm-release] HALT file created: $halt_file"
  echo "[cm-release] $contract"
}

# Helper: append a HALT Event to the project-scoped release-state.md.
# Usage: append_halt_event_to_release_state <error_key> <message> <remedy>
# Carries all three dual-channel fields so an operator scanning
# release-state.md's audit trail never has to cross-reference the HALT file
# to understand what stopped and why.
append_halt_event_to_release_state() {
  local error_key="$1"
  local message="$2"
  local remedy="$3"
  local timestamp
  timestamp="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  if [[ ! -f "$RELEASE_STATE" ]]; then
    echo "[cm-release] WARNING: release-state.md not found at $RELEASE_STATE — cannot append HALT event." >&2
    return 0
  fi
  cat >> "$RELEASE_STATE" <<EOF

## HALT Event
Timestamp: ${timestamp}
CM Task: cm-release.sh (automated)
$(pgai_format_error_contract "$error_key" "$message" "$remedy")
EOF
  echo "[cm-release] HALT Event appended to release-state.md"
}

# Helper: full HALT procedure — write halt file, append event to release-state, exit 1.
# Usage: cm_halt <error_key> <message> <remedy>
#   error_key — stable machine key matching the trigger/gate/check id documented
#               in team/roles/CM.md "HALT triggers" (greppable, resolution-matrix-
#               addressable; e.g. "cm-release.trigger-5.squash-conflict").
#   message   — what stopped and why, in a stranger's words (no bare internal
#               identifier without a plain-language explanation).
#   remedy    — what to do next, verbatim-runnable where possible.
# This is the single exit point for all HALT conditions. Emits the dual-channel
# error contract (team/scripts/lib/error_contract.sh) to stderr, the HALT file,
# and the release-state.md HALT Event — all three carry the same triple.
cm_halt() {
  local error_key="$1"
  local message="$2"
  local remedy="$3"
  echo "" >&2
  echo "[cm-release] HALT" >&2
  pgai_emit_error_contract "$error_key" "$message" "$remedy"
  write_halt_file "$error_key" "$message" "$remedy"
  append_halt_event_to_release_state "$error_key" "$message" "$remedy"
  echo "" >&2
  echo "HALT: cm-release.sh stopped. The release has NOT been shipped." >&2
  echo "The dev tree, branches, and RC state are unchanged for operator inspection." >&2
  echo "Remove $PROJECT_HALT_FILE to resume this project's chain after resolving the issue." >&2
  echo "(This HALT is scoped to project '$PROJECT_NAME' only; sibling projects' chains are unaffected.)" >&2
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
# Usage: cm_ship_policy <tester_state> <systemic_risk> <recommendation> <has_large_fix_effort> <task_found>
# Outputs one of: SHIP-FUNCTIONAL  SHIP-KNOWN-BUGS  SHIP-NON-FUNCTIONAL  HALT
# has_large_fix_effort: "yes" if any finding has Fix Effort=large; "no" otherwise.
# task_found: "yes" if a TESTER task was located for this Release Version (regardless
#   of its state); "no" if no TESTER task exists at all. This is passed explicitly
#   (rather than inferred from tester_state=="none") so a TESTER task that was FOUND
#   but has an empty/malformed State field is never confused with the "no TESTER task
#   exists" case — see CM.md "(no report, Test Required: false) -> Ship" vs
#   "(unrecognized values) -> HALT" rows, which are distinct.
# Evaluation is top-to-bottom; first matching row wins (per CM.md specification).
cm_ship_policy() {
  local tester_state="${1:-none}"
  local systemic_risk="${2:-none}"
  local recommendation="${3:-none}"
  local has_large_fix_effort="${4:-no}"
  local task_found="${5:-no}"

  # Normalize to lowercase for comparison robustness.
  tester_state="$(echo "$tester_state" | tr '[:upper:]' '[:lower:]')"
  systemic_risk="$(echo "$systemic_risk" | tr '[:upper:]' '[:lower:]')"
  recommendation="$(echo "$recommendation" | tr '[:upper:]' '[:lower:]')"
  has_large_fix_effort="$(echo "$has_large_fix_effort" | tr '[:upper:]' '[:lower:]')"
  task_found="$(echo "$task_found" | tr '[:upper:]' '[:lower:]')"

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

  # Row 7 (no report found): no TESTER task exists for this Release Version at all.
  # This is the documented "(no report, Test Required: false) -> Ship" row — the
  # only case that legitimately defaults to SHIP-FUNCTIONAL when tester_state is
  # not "blocked" or "done".
  if [[ "$task_found" != "yes" ]]; then
    echo "SHIP-FUNCTIONAL"
    return 0
  fi

  # Row 8 (unrecognized values): a TESTER task WAS found (task_found=yes) but its
  # state is neither "blocked" nor "done" (e.g. WONT-DO, WORKING, a typo). Per
  # CM.md's "(unrecognized values) -> HALT -- quote the unrecognized field and
  # value verbatim" row, this must HALT rather than silently default-ship — a
  # high-risk report attached to a non-DONE task must never be treated as
  # equivalent to "no report at all".
  echo "HALT"
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

# --- Step 4a: Check out the RC branch before any RC-scoped work ---
# Everything from here through the squash (Step 4b's pre-squash hook, Step 4c's
# release-notes pattern scan, Step 4e's notes-placeholder stamp, Step 4f's
# pollution guard) reads or writes $REPO_ROOT as a plain filesystem path, not
# via `git show <ref>:<path>`. None of that is meaningful unless the dev
# tree's working copy is actually the RC's tree.
#
# This checkout is the fix for the hook-ordering recurrence (v0.11.0): an
# earlier fix (cm_resolve_and_enforce_hook's rc_branch-aware git-show
# resolution in team/scripts/lib/cm_release_hooks.sh) made hook CONTENT
# discovery independent of the current checkout, but a hook that COMMITS
# (e.g. a version bump) still executes via `cd "$REPO_ROOT"` in
# _run_release_hook — its commit lands on whatever branch the working copy
# was actually on, not on the RC branch, if this checkout is skipped. The
# earlier fix solved "which script runs" but never solved "what branch the
# script's own git operations land on", so a hook-wired release still failed
# whenever the dev tree was parked on any branch other than the RC at
# invocation time. Checking out the RC branch here — before any RC-scoped
# step reads or writes the working tree — closes that gap directly instead
# of asking every future RC-scoped step to individually route through git
# plumbing.
echo "[Step 4a] Checking out $RC_BRANCH so all RC-scoped steps (hooks included) operate on the RC's own working tree..."
git_step "git checkout $RC_BRANCH" git -C "$REPO_ROOT" checkout "$RC_BRANCH"

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
    "cm-release.trigger-8.last-3-rcs-non-functional" \
    "Trigger 8: the last three consecutive releases for ${PROJECT_NAME} were all marked NON-FUNCTIONAL — the autonomous chain is shipping degraded work repeatedly without self-correcting." \
    "Review the NON-FUNCTIONAL releases under release-notes/ in the dev tree, confirm a bug was filed naming the pattern (CM.md Trigger 8 procedure), fix the root cause, then remove \$KANBAN_ROOT/projects/${PROJECT_NAME}/HALT to resume."
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

# Collect every TESTER task matching this RC's Release Version. RCs can be
# cancelled and re-cut (SOP "RC Immutability" / cm-cancel-rc.sh), so more than
# one TESTER attempt per Release Version is a supported, EXPECTED condition —
# not an edge case. `sort -z` yields task directories in ascending lexicographic
# order, which (because task IDs embed a zero-padded date+sequence number) is
# also ascending creation order, so the LAST candidate collected below is the
# most-recently-created TESTER task for this Release Version.
_CM_TASKS_DIR="${_CM_PROJECT_ROOT}/tasks"
_TESTER_CANDIDATE_DIRS=()
_TESTER_CANDIDATE_STATES=()
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
    # Do NOT break on first match — collect every candidate so the selection
    # below can pick the task that reflects the RC's CURRENT state: a stale,
    # superseded TESTER task must not win over the current one just because
    # its task ID sorts lexicographically first.
    _TESTER_CANDIDATE_DIRS+=("$_task_dir")
    _TESTER_CANDIDATE_STATES+=("$(read_md_field "$_task_status_file" "State")")
  done < <(find "$_CM_TASKS_DIR" -maxdepth 1 -mindepth 1 -type d -print0 2>/dev/null | sort -z)
fi

# Select the TESTER task that reflects the RC's current state.
# Preference order: (1) a DONE task over any non-DONE task (WONT-DO, BLOCKED,
# WORKING, etc.) for this Release Version; (2) among equally-preferred
# candidates, the most recently created one (last in the ascending-sorted
# candidate list). If no candidate is DONE, fall back to the most recently
# created candidate overall, so the selection always reflects the RC's latest
# verification attempt rather than an arbitrary or stale one.
_TESTER_SELECTED_DIR=""
_tester_num_candidates="${#_TESTER_CANDIDATE_DIRS[@]}"
if [[ "$_tester_num_candidates" -gt 0 ]]; then
  _tester_most_recent_done_dir=""
  _tester_most_recent_any_dir=""
  for (( _tester_idx=0; _tester_idx<_tester_num_candidates; _tester_idx++ )); do
    _tester_cand_dir="${_TESTER_CANDIDATE_DIRS[$_tester_idx]}"
    _tester_cand_state_lc="$(echo "${_TESTER_CANDIDATE_STATES[$_tester_idx]}" | tr '[:upper:]' '[:lower:]')"
    _tester_most_recent_any_dir="$_tester_cand_dir"
    if [[ "$_tester_cand_state_lc" == "done" ]]; then
      _tester_most_recent_done_dir="$_tester_cand_dir"
    fi
  done
  if [[ -n "$_tester_most_recent_done_dir" ]]; then
    _TESTER_SELECTED_DIR="$_tester_most_recent_done_dir"
  else
    _TESTER_SELECTED_DIR="$_tester_most_recent_any_dir"
  fi
fi

if [[ -n "$_TESTER_SELECTED_DIR" ]]; then
  _task_dir="$_TESTER_SELECTED_DIR"
  _task_status_file="${_task_dir}/status.md"
  _TESTER_TASK_ID="$(basename "$_task_dir")"
  _TESTER_STATUS="$(read_md_field "$_task_status_file" "State")"
  # Look for the TESTER report artifact.
  _candidate_report="${_task_dir}/artifacts/report.md"
  if [[ -f "$_candidate_report" ]]; then
    _TESTER_REPORT_PATH="$_candidate_report"
    _TESTER_RECOMMENDATION="$(read_md_field "$_TESTER_REPORT_PATH" "Recommendation")"
    _TESTER_SYSTEMIC_RISK="$(read_md_field "$_TESTER_REPORT_PATH" "Systemic Risk")"
  fi
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

# Whether a TESTER task was actually located for this Release Version, independent
# of what its State field says. Passed to cm_ship_policy so a found task with an
# unrecognized/empty State is never confused with "no TESTER task exists".
_TESTER_TASK_FOUND="no"
if [[ "$_TESTER_TASK_ID" != "none" ]]; then
  _TESTER_TASK_FOUND="yes"
fi

# Apply the ship-policy decision matrix (table-driven, single function, no scattered if-else).
_SHIP_DECISION="$(cm_ship_policy "$_TESTER_STATUS" "$_TESTER_SYSTEMIC_RISK" "$_TESTER_RECOMMENDATION" "$_HAS_LARGE_FIX_EFFORT" "$_TESTER_TASK_FOUND")"
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
  _TESTER_STATUS_LC="$(echo "$_TESTER_STATUS" | tr '[:upper:]' '[:lower:]')"
  if [[ "$_TESTER_STATUS_LC" == "blocked" ]]; then
    cm_halt \
      "cm-release.trigger-1.tester-blocked" \
      "Trigger 1: TESTER task ${_TESTER_TASK_ID} is BLOCKED for ${ACTIVE_RC} — verification could not complete, so the release of an unverified RC is refused." \
      "Read task ${_TESTER_TASK_ID}'s status.md Blockers field at ${_CM_TASKS_DIR}, resolve the underlying issue, re-run TESTER so it reaches DONE, then re-invoke cm-release.sh."
  elif [[ "$_TESTER_TASK_FOUND" == "yes" && "$_TESTER_STATUS_LC" != "done" ]]; then
    # A TESTER task WAS found for this Release Version, but its State is neither
    # BLOCKED nor DONE (e.g. WONT-DO, WORKING, a typo). Per CM.md's ship-policy
    # matrix "(unrecognized values) -> HALT -- quote the unrecognized field and
    # value verbatim" row: never silently default-ship a found-but-unrecognized
    # TESTER state, regardless of its report's systemic_risk/recommendation —
    # a non-DONE task's high-risk report must never be treated as equivalent
    # to "no report at all".
    cm_halt \
      "cm-release.ship-policy.tester-state-unrecognized" \
      "TESTER task ${_TESTER_TASK_ID} for ${ACTIVE_RC} has State='${_TESTER_STATUS}' — the ship-policy decision matrix only defines rows for State=BLOCKED and State=DONE, so an unrecognized state is refused rather than default-shipped." \
      "Inspect task ${_TESTER_TASK_ID} at ${_CM_TASKS_DIR}, confirm the correct TESTER outcome (re-run TESTER if its State should be DONE or BLOCKED instead), then re-invoke cm-release.sh."
  elif [[ "$(echo "$_TESTER_SYSTEMIC_RISK" | tr '[:upper:]' '[:lower:]')" == "high" ]]; then
    cm_halt \
      "cm-release.trigger-2.systemic-risk-high" \
      "Trigger 2: TESTER report systemic_risk=high for ${ACTIVE_RC} (task ${_TESTER_TASK_ID}) — this indicates a broader framework regression, so the release is refused." \
      "Read ${_TESTER_REPORT_PATH:-the TESTER report for this RC} for the high-risk findings, fix or triage them, re-run TESTER, then re-invoke cm-release.sh."
  elif [[ "$(echo "$_TESTER_RECOMMENDATION" | tr '[:upper:]' '[:lower:]')" == "ship-with-serious-concerns" && "$_HAS_LARGE_FIX_EFFORT" == "yes" ]]; then
    cm_halt \
      "cm-release.trigger-3.serious-concerns-large-fix" \
      "Trigger 3: TESTER recommendation=SHIP-WITH-SERIOUS-CONCERNS and at least one finding has Fix Effort=large for ${ACTIVE_RC} — the scope is too large to auto-ship." \
      "Review the large-effort finding(s) in ${_TESTER_REPORT_PATH:-the TESTER report for this RC}, scope the fix work (or an explicit operator decision to proceed anyway), then re-invoke cm-release.sh."
  else
    cm_halt \
      "cm-release.ship-policy.halt" \
      "Ship-policy matrix returned HALT for ${ACTIVE_RC}: TESTER state=${_TESTER_STATUS} systemic_risk=${_TESTER_SYSTEMIC_RISK} recommendation=${_TESTER_RECOMMENDATION} — none of the known auto-ship rows matched." \
      "Cross-reference team/roles/CM.md's ship-policy decision matrix against these TESTER field values, decide the correct disposition, then re-invoke cm-release.sh."
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
cm_resolve_and_enforce_hook "$PROJECT_NAME" "pre-squash" "$PROJECT_HOOKS_DIR" "$REPO_ROOT" "$RC_BRANCH" || {
  cm_halt \
    "cm-release.trigger-4.pre-squash-hook-not-executable" \
    "Trigger 4: the cm-release-pre-squash in-repo hook exists but is not executable, so the finalization mechanic cannot run and the squash cannot proceed safely." \
    "chmod +x the in-repo hook path printed above by cm_release_hooks.sh, commit the mode change, then re-run cm-release.sh."
}
_pre_squash_rc=0
if [[ -n "$CM_RESOLVED_HOOK_PATH" ]]; then
  ( _run_release_hook "cm-release-pre-squash" "$CM_RESOLVED_HOOK_PATH" ) || _pre_squash_rc=$?
fi
# Clean up the materialized RC-branch hook file (only set when the resolver
# read the hook from the RC branch's git tree rather than a persistent path).
[[ "${CM_RESOLVED_HOOK_IS_TEMP:-0}" == "1" ]] && rm -f "$CM_RESOLVED_HOOK_PATH"
if [[ $_pre_squash_rc -ne 0 ]]; then
  cm_halt \
    "cm-release.trigger-4.pre-squash-hook-failed" \
    "Trigger 4: the cm-release-pre-squash hook exited non-zero (rc=${_pre_squash_rc}) for ${ACTIVE_RC} — the finalization mechanic is broken, so the squash cannot proceed safely." \
    "Inspect the [hook cm-release-pre-squash]-prefixed lines printed above for the hook's own diagnostic output, fix the hook, then re-run cm-release.sh."
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
        "cm-release.trigger-9.notes-missing-status-heading" \
        "Trigger 9: release-notes/${ACTIVE_RC}.md exists but has no recognizable '## Status' heading, so the release record cannot state the actual ship decision." \
        "Inspect ${_STAMP_NOTES_FILE}, add a '## Status' heading with the value ${_RELEASE_NOTES_STATUS}, commit it on the RC branch, then re-run cm-release.sh."
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
        "cm-release.trigger-9.stamp-step-failed" \
        "Trigger 9: the release-notes stamp step exited non-zero (rc=${_stamp_py_rc}) for release-notes/${ACTIVE_RC}.md — the PENDING-RELEASE placeholder could not be replaced." \
        "Inspect ${_STAMP_NOTES_FILE} for a malformed '## Status' block, fix it by hand, then re-run cm-release.sh."
    fi

    # Guard: verify the placeholder no longer appears in the notes after the stamp.
    if grep -q "PENDING-RELEASE" "$_STAMP_NOTES_FILE" 2>/dev/null; then
      cm_halt \
        "cm-release.trigger-9.placeholder-survived" \
        "Trigger 9: release-notes/${ACTIVE_RC}.md still contains the literal 'PENDING-RELEASE' placeholder after the stamp step ran — the stamp logic did not apply cleanly." \
        "Inspect ${_STAMP_NOTES_FILE}, manually replace PENDING-RELEASE with ${_RELEASE_NOTES_STATUS} under '## Status', commit it, then re-run cm-release.sh."
    fi

    # Guard: verify a recognizable ## Status line exists in the stamped notes.
    if ! grep -qE "^## Status" "$_STAMP_NOTES_FILE" 2>/dev/null; then
      cm_halt \
        "cm-release.trigger-9.stamped-notes-missing-status-heading" \
        "Trigger 9: release-notes/${ACTIVE_RC}.md has no recognizable '## Status' heading after the stamp attempt." \
        "Inspect ${_STAMP_NOTES_FILE}, add a '## Status' heading with the value ${_RELEASE_NOTES_STATUS}, commit it, then re-run cm-release.sh."
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
      "cm-release.trigger-10.pollution-guard-strict" \
      "Trigger 10: the squash pollution guard (strict mode) found ${#_guard_flagged[@]} stray path(s) that would be absorbed into release history by squashing ${ACTIVE_RC}." \
      "Remove or unstage the stray path(s) listed above from ${RC_BRANCH}, commit the cleanup, then re-run cm-release.sh (or set CM_SQUASH_STRICT_POLLUTION_GUARD=false to warn instead of halt)."
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
        "cm-release.trigger-5.squash-conflict" \
        "Trigger 5: git merge --squash ${RC_BRANCH} into ${MAIN_BRANCH} produced conflicts (rc=${_squash_main_rc}) that the UD/DU auto-resolver could not clear — git state may be damaged." \
        "Inspect the conflicted paths with 'git status' in ${REPO_ROOT}, resolve them by hand (or run 'git merge --abort' to back out), then re-run cm-release.sh."
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
      "cm-release.trigger-11.fidelity-gate-divergence" \
      "Trigger 11: the post-squash fidelity gate found ${MAIN_BRANCH} diverging from rc/${ACTIVE_RC} after the squash — a commit landed on ${MAIN_BRANCH} mid-RC." \
      "Inspect the divergent paths in the 'git diff --stat' output printed above, decide whether to fold the foreign commit into the next RC or revert it, then re-run cm-release.sh."
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


_H1_LINE_RE = re.compile(r"^#\s+(.*)$")

# A bundled item's own filename is the cheapest reliable signal for what
# kind of ledger entry it is: bug and priority files are always named
# BUG-NNNN-slug.md / PRIORITY-NNNN-slug.md by the filing contract each
# template enforces (see team/templates/project/release/{BUG,PRIORITY}-
# TEMPLATE.md). Both templates' own H1 line IS the bare internal ID
# ("# BUG-NNNN-slug"), which is exactly the leak this dispatch avoids by
# routing those two kinds through their own substance-extraction path
# instead of the generic H1 lookup below.
_BUG_FILENAME_RE = re.compile(r"^BUG-\d+", re.IGNORECASE)
_PRIORITY_FILENAME_RE = re.compile(r"^PRIORITY-\d+", re.IGNORECASE)

# Internal ledger/task identifiers that must never survive into rendered
# release notes, even as an incidental mention inside a Symptom or Fix
# paragraph (e.g. "...see BUG-0011 for the earlier attempt"). This mirrors
# the token set team/scripts/lint_comment_provenance.py enforces against
# release-notes/ prose, so the generator produces lint-clean output by
# construction rather than relying on the lint to catch a leak after the
# fact.
_INTERNAL_ID_RE = re.compile(
    r"\b(?:BUG|PRIORITY)-\d+\b"
    r"|\b(?:CODER|WRITER|TESTER|CM|PM|PO)-\d{8}-\d+\b",
    re.IGNORECASE,
)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _parse_section(text, heading):
    """Return the stripped body of a '## <heading>' section in *text*, or
    '' when the section is absent or empty. Stops at the next '##' heading
    or end of file. Same section-extraction convention
    team/pgai_agent_kanban/cm/changelog_writer.py uses for bug files, kept
    as a local copy here since this code runs as an extracted heredoc with
    no import path back into the package."""
    pattern = re.compile(
        r"^##\s+" + re.escape(heading) + r"\s*$", re.IGNORECASE | re.MULTILINE
    )
    match = pattern.search(text)
    if not match:
        return ""
    start = match.end()
    next_heading = re.search(r"^##\s+", text[start:], re.MULTILINE)
    end = start + next_heading.start() if next_heading else len(text)
    return text[start:end].strip()


def _first_sentence(text):
    """Collapse whitespace (including newlines) to single spaces and return
    the first sentence, or the whole flattened string when no sentence-
    ending punctuation is found."""
    flat = " ".join(text.split())
    if not flat:
        return ""
    match = re.search(r"[.!?](?:\s|$)", flat)
    return flat[: match.start() + 1].strip() if match else flat


def _strip_internal_ids(text):
    """Remove any bare internal ledger/task identifier from *text*, then
    collapse the whitespace and stray leading punctuation the removal can
    leave behind. Defense-in-depth: the by-filename dispatch already avoids
    quoting a bug/priority file's own ID, but a bug's free-text Symptom or
    Fix prose could still mention another ticket inline."""
    cleaned = _INTERNAL_ID_RE.sub("", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
    return cleaned.strip()


def _substance_from_section(text, heading):
    """Extract a '## <heading>' section, strip HTML comments, reduce to one
    clean sentence, and scrub any internal identifiers from the result."""
    raw = _HTML_COMMENT_RE.sub("", _parse_section(text, heading))
    return _strip_internal_ids(_first_sentence(raw))


def get_bug_substance(text):
    """Build a substance description for a bug item from its own Symptom
    and Fix text — the content-described-notes policy: release notes state
    WHAT was wrong and WHAT changed, never the bare internal ledger ID that
    the bug template uses as its own H1 line.

    Prefers the Fix section (or its historical alias, Files Involved)
    because it already describes the resolution in notes-ready language;
    falls back to a 'Fixed: <symptom>' phrasing built from the Symptom
    section when no Fix text was recorded; falls back to a generic,
    ID-free sentence when the bug file carries neither."""
    fix_text = _substance_from_section(text, "Fix")
    if not fix_text:
        fix_text = _substance_from_section(text, "Files Involved")
    if fix_text:
        return fix_text
    symptom_text = _substance_from_section(text, "Symptom")
    if symptom_text:
        return f"Fixed: {symptom_text}"
    return "Fixed an internal defect (no Symptom/Fix text recorded in the bug report)."


def get_priority_substance(text):
    """Build a substance description for a priority item from its own Title
    (falling back to Description), matching the same no-bare-ID policy
    get_bug_substance applies to bugs."""
    title_text = _substance_from_section(text, "Title")
    if title_text:
        return title_text
    description_text = _substance_from_section(text, "Description")
    if description_text:
        return description_text
    return "Completed a requested enhancement (no Title/Description text recorded)."


def get_item_title(item_path):
    """Return a substance description for a bundled item, never a bare
    internal ledger ID.

    - BUG-NNNN-*.md files: description sourced from the bug's own
      Symptom/Fix text (see get_bug_substance). The template's H1 line is
      the bug ID itself, so bug files never take the generic H1 path below.
    - PRIORITY-NNNN-*.md files: description sourced from the item's own
      Title (falling back to Description); see get_priority_substance. Same
      reasoning: the template's H1 line is the priority ID itself.
    - Everything else (operator-authored bundle titles, misc items): the
      first true H1 line (a single leading '#' followed by whitespace),
      stripped of any internal identifier that happens to appear in it, or
      the file stem when no H1 line is present. Deeper headings ('##',
      '###', etc.) never match."""
    filename = pathlib.Path(item_path).name
    try:
        text = pathlib.Path(item_path).read_text(encoding="utf-8")
    except OSError:
        return pathlib.Path(item_path).stem

    if _BUG_FILENAME_RE.match(filename):
        return get_bug_substance(text)
    if _PRIORITY_FILENAME_RE.match(filename):
        return get_priority_substance(text)

    for line in text.splitlines():
        stripped = line.strip()
        match = _H1_LINE_RE.match(stripped)
        if match:
            cleaned = _strip_internal_ids(match.group(1).strip())
            if cleaned:
                return cleaned
            break
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
# Cleaned up by cleanup_on_exit (the script's single EXIT trap) on every exit
# path — do not add a second `trap ... EXIT` here; it would silently replace
# the script-wide trap and disable the rollback guard for every step after
# this point (see cleanup_on_exit's own comment near the top of the script).

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
cm_resolve_and_enforce_hook "$PROJECT_NAME" "pre-tag" "$PROJECT_HOOKS_DIR" "$REPO_ROOT" "$RC_BRANCH" || {
  cm_halt \
    "cm-release.pre-tag-hook-not-executable" \
    "The cm-release-pre-tag hook resolved to '${CM_HOOK_NONEXEC_PATH}' but is not executable, so the pre-tag phase cannot run it. (Full Cause/To-fix/Rule remedy block printed above by cm_release_hooks.sh.)" \
    "${CM_HOOK_NONEXEC_FIX_CMD}"
}
if [[ -n "$CM_RESOLVED_HOOK_PATH" ]]; then
  _run_release_hook "cm-release-pre-tag" "$CM_RESOLVED_HOOK_PATH"
fi
# Clean up the materialized RC-branch hook file (only set when the resolver
# read the hook from the RC branch's git tree rather than a persistent path).
[[ "${CM_RESOLVED_HOOK_IS_TEMP:-0}" == "1" ]] && rm -f "$CM_RESOLVED_HOOK_PATH"

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
      "cm-release.trigger-5b.tag-points-at-wrong-commit" \
      "Trigger 5b: local tag ${RELEASE_TAG} exists but points at $(echo "$_local_tag_sha" | cut -c1-7) instead of the expected $(echo "$_current_head_sha" | cut -c1-7) (${MAIN_BRANCH} HEAD), indicating a diverged re-run." \
      "Inspect the local tag with 'git show ${RELEASE_TAG}' in ${REPO_ROOT}; delete it with 'git tag -d ${RELEASE_TAG}' if it is stale, then re-run cm-release.sh."
  fi
else
  # Local tag absent: check origin (existing Trigger 7 check) before creating.
  if git -C "$REPO_ROOT" ls-remote --exit-code --tags origin "refs/tags/${RELEASE_TAG}" >/dev/null 2>&1; then
    cm_halt \
      "cm-release.trigger-7.tag-exists-on-origin" \
      "Trigger 7: tag ${RELEASE_TAG} already exists on origin — creating it again would be a duplicate, indicating a race condition or a repeated invocation against an already-shipped version." \
      "Run 'git ls-remote --tags origin ${RELEASE_TAG}' to confirm, verify whether ${ACTIVE_RC} already shipped, then either cancel this RC (cm-cancel-rc.sh) or investigate the race."
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
    echo "[Pre-tag VERSION gate] FAIL — on-disk VERSION does not match the tag about to be created. No tag has been created." >&2
    echo "" >&2
    echo "Cause(s):" >&2
    echo "  - ${REPO_ROOT}/VERSION contains '${_pretag_version_trimmed}', expected '${_pretag_expected}' (release tag ${RELEASE_TAG})" >&2
    echo "" >&2
    echo "To fix:" >&2
    echo "  1. printf '%s\n' '${_pretag_expected}' > '${REPO_ROOT}/VERSION'" >&2
    echo "  2. git -C '${REPO_ROOT}' add VERSION && git -C '${REPO_ROOT}' commit -m 'Fix VERSION for ${_pretag_expected}'" >&2
    echo "  3. Re-run cm-release.sh." >&2
    echo "" >&2
    echo "Rule: the committed VERSION file must equal the release tag being cut, or the shipped tag would misreport the version." >&2
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
cm_resolve_and_enforce_hook "$PROJECT_NAME" "post-tag" "$PROJECT_HOOKS_DIR" "$REPO_ROOT" "$RC_BRANCH" || {
  echo "[cm-release] WARNING: post-tag in-repo hook exists but is not executable — skipping (post-tag does not block release)" >&2
}
if [[ -n "$CM_RESOLVED_HOOK_PATH" ]]; then
  _run_release_hook "cm-release-post-tag" "$CM_RESOLVED_HOOK_PATH" --no-block
fi
# Clean up the materialized RC-branch hook file (only set when the resolver
# read the hook from the RC branch's git tree rather than a persistent path).
# Must happen before Step 11 deletes the local RC branch below.
[[ "${CM_RESOLVED_HOOK_IS_TEMP:-0}" == "1" ]] && rm -f "$CM_RESOLVED_HOOK_PATH"

# --- Verification complete: proceeding to finalization ---
# Every gate that can veto the release (pre-squash hook, squash-pollution guard,
# fidelity gate, pre-tag hook, pre-tag VERSION gate, the tag itself, and the
# post-tag hook — which is logged-only and cannot veto) has now run. Nothing
# below this line can be skipped by re-running with the cause fixed: a failure
# from here on is a partial-completion state to resume, not a gate to rerun.
# The steps below are the ONLY destructive ones in this script (RC branch
# deletion, local and origin; release-state.md rewrite) and they run ONLY
# after this point is reached — see cleanup_on_exit's rollback guard above for
# what happens when a step ABOVE this line fails instead.
echo ""
echo "[cm-release] verification complete — proceeding to finalization"
echo ""
_CM_VERIFICATION_COMPLETE=1

# --- Step 9: main is NOT pushed here ---
# The release commits on main stay local until the operator runs cm-finalize-release.sh.
# Operator will push main (and the tag created in Step 13) as the human-gated final step.
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

# Helper: force-remove any linked worktree still checked out on the given
# branch, so that a subsequent `git branch -D <branch>` does not fail with
# "cannot delete branch ... used by worktree at ...".
# Usage: _cm_remove_stale_worktrees_for_branch <repo_root> <branch_name>
#
# Leftover-worktree guard: per-task worktree teardown (pgai_worktree_teardown) is supposed
# to remove a CODER task's linked worktree when the task completes, but
# teardown is not guaranteed to run to completion for every task. When it is
# skipped, the leftover worktree directory stays checked out on the RC
# branch, and Step 11's `git branch -D` fails AFTER all the real release
# work (squash, fidelity gate, notes, tag) has already succeeded. `git
# worktree prune` (Step 16a, later in this script) does not help here even
# if it were reordered earlier — prune only clears administrative refs for
# worktrees whose directory has already been deleted from disk; a live
# worktree directory needs an actual `git worktree remove`.
#
# Defensive and idempotent: when no worktree is pinned to <branch_name> (the
# common case), this is a silent no-op. Returns 0 on success (including the
# no-op case); returns 1 if a pinning worktree was found but could not be
# force-removed, so the caller can HALT with a clear, actionable message
# before attempting the branch deletion.
_cm_remove_stale_worktrees_for_branch() {
  local repo_root="$1"
  local branch_name="$2"
  local target_ref="refs/heads/${branch_name}"
  local wt_path=""
  local wt_branch=""
  local removed_count=0

  while IFS= read -r _line; do
    case "$_line" in
      "worktree "*)
        wt_path="${_line#worktree }"
        wt_branch=""
        ;;
      "branch "*)
        wt_branch="${_line#branch }"
        ;;
      "")
        # Blank line marks the end of one worktree's porcelain record.
        if [[ -n "$wt_path" && "$wt_branch" == "$target_ref" ]]; then
          if [[ "$wt_path" == "$repo_root" ]]; then
            # The main worktree itself (REPO_ROOT) is never removable via
            # `git worktree remove` and should already be on $MAIN_BRANCH by
            # this point in the script (Step 5 checked it out) — skip rather
            # than attempt a removal that would fail on the main worktree.
            echo "[cm-release] NOTE: main worktree ($wt_path) reports branch $branch_name — skipping (git worktree remove does not apply to the main worktree)." >&2
          else
            echo "[cm-release] Stale worktree detected: $wt_path is still checked out on $branch_name — force-removing before branch deletion so the release can finish."
            local _wt_rm_out _wt_rm_rc=0
            _wt_rm_out="$(git -C "$repo_root" worktree remove --force "$wt_path" 2>&1)" || _wt_rm_rc=$?
            if [[ -n "$_wt_rm_out" ]]; then
              echo "$_wt_rm_out" | sed 's/^/[worktree remove] /'
            fi
            if [[ $_wt_rm_rc -eq 0 ]]; then
              echo "[cm-release] Removed stale worktree: $wt_path"
              removed_count=$((removed_count + 1))
            else
              echo "ERROR: failed to force-remove stale worktree at $wt_path (still pinning $branch_name, rc=$_wt_rm_rc)." >&2
              return 1
            fi
          fi
        fi
        wt_path=""
        wt_branch=""
        ;;
    esac
  done < <(git -C "$repo_root" worktree list --porcelain; echo "")

  if [[ $removed_count -gt 0 ]]; then
    echo "[cm-release] Removed ${removed_count} stale worktree(s) pinned to $branch_name."
  fi
  return 0
}

# --- Step 10.5: defensively remove any worktree still pinned to $RC_BRANCH ---
# Runs immediately before Step 11's branch deletion so a leftover per-task
# worktree never blocks `git branch -D` after all the real
# release work has already completed. Idempotent: a no-op when no worktree
# is pinned to $RC_BRANCH, which is the common case on every normal run.
echo "[Step 10.5] Checking for stale worktrees still pinned to $RC_BRANCH..."
_cm_remove_stale_worktrees_for_branch "$REPO_ROOT" "$RC_BRANCH" || cm_halt \
  "cm-release.stale-worktree-blocks-branch-delete" \
  "A worktree is still checked out on ${RC_BRANCH} and could not be force-removed; Step 11 (git branch -D ${RC_BRANCH}) would fail with 'used by worktree at ...'. The worktree path is named in the [cm-release] log line above." \
  "Inspect the worktree path named above, remove it manually with 'git -C ${REPO_ROOT} worktree remove --force <path>' (or delete the directory on disk and run 'git -C ${REPO_ROOT} worktree prune'), then re-run cm-release.sh."

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
# Only reached here after verification completed (see the narration above) and
# ALL git operations above succeeded.
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
