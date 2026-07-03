#!/usr/bin/env bash
# cm-push-watchdog.sh
# Push unpushed main commits and tags to origin for every registered project.
#
# Designed to run from cron OUTSIDE the Claude Code tool-use environment,
# so PreToolUse hook interception cannot block git push to main/origin.
#
# Usage:
#   cm-push-watchdog.sh [--dry-run] [--project <name>]
#
# Options:
#   --dry-run          Show what would be pushed without actually pushing.
#   --project <name>   Operate on a single named project instead of all.
#   --help, -h         Show this help and exit.
#
# Environment:
#   KANBAN_ROOT        Path to the live kanban install (default: $HOME/pgai_agent_kanban).
#                      Also honoured via PGAI_AGENT_KANBAN_ROOT_PATH.
#
# Behaviour per project:
#   1. Skip projects with a per-project HALT file (KANBAN_ROOT/projects/<name>/HALT).
#   2. Read dev_tree_path from the project's PROJECT.cfg.
#   3. Skip projects whose dev_tree_path is not a git repository.
#   4. Check whether local main is ahead of origin/main.
#   5. Check for local tags absent from origin.
#   6. Push the missing commits and/or tags.
#   7. Log every action to KANBAN_ROOT/projects/<name>/logs/cm-push-watchdog.log.
#
# Idempotent and safe: when there is nothing to push the script exits 0
# without touching origin.  Network / credential failures are logged and
# treated as non-fatal (script exits 0 to avoid noisy cron mail on transient
# errors; individual project failures are recorded in the per-project log).
#
# Exit code: always 0 (idempotent safety net design — cron should not
# produce mail just because a network push failed on one project; the log
# captures the failure for operator review).

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve KANBAN_ROOT
# ---------------------------------------------------------------------------
KANBAN_ROOT="${KANBAN_ROOT:-${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}}"

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
DRY_RUN=false
SINGLE_PROJECT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)   DRY_RUN=true; shift ;;
    --project)   SINGLE_PROJECT="${2:-}"; shift 2 ;;
    --help|-h)
      cat <<'EOF'
Usage: cm-push-watchdog.sh [--dry-run] [--project <name>]

Pushes unpushed main commits and tags to origin for each registered project.
Safe to run on every cron tick — no-op when there is nothing to push.

Options:
  --dry-run          Report what would be pushed without pushing.
  --project <name>   Operate on a single named project.
  --help, -h         Show this help.

Environment:
  KANBAN_ROOT (or PGAI_AGENT_KANBAN_ROOT_PATH)
    Path to the live kanban install root. Default: $HOME/pgai_agent_kanban
EOF
      exit 0
      ;;
    *)
      echo "cm-push-watchdog: unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Source projects.sh library (handles both INI and colon-legacy projects.cfg)
# ---------------------------------------------------------------------------
# This file lives under $KANBAN_ROOT/scripts/ at runtime (install.sh strips
# the team/ prefix). The library helpers handle both projects.cfg formats:
#   - INI ([project:NAME] sections)
#   - colon-legacy (NAME:PRIORITY[:COLOR])
# Without these helpers, an ad-hoc 'awk -F:' parser silently extracted the
# literal string '[project' from INI headers and created a ghost
# projects/[project/ directory on every cron tick.
_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib"
if [[ -f "${_LIB_DIR}/projects.sh" ]]; then
    # shellcheck source=lib/projects.sh
    source "${_LIB_DIR}/projects.sh"
else
    echo "cm-push-watchdog: ERROR: projects.sh library not found at ${_LIB_DIR}/projects.sh" >&2
    exit 1
fi
# project_paths.sh provides pp_project_root, _pp_project_cfg_file, _pp_read_cfg_key
if [[ -f "${_LIB_DIR}/project_paths.sh" ]]; then
    # shellcheck source=lib/project_paths.sh
    source "${_LIB_DIR}/project_paths.sh"
else
    echo "cm-push-watchdog: ERROR: project_paths.sh library not found at ${_LIB_DIR}/project_paths.sh" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------
# Global log (kanban-root scope; written even when no project is specified)
GLOBAL_LOG_DIR="${KANBAN_ROOT}/logs"
mkdir -p "$GLOBAL_LOG_DIR"
GLOBAL_LOG="${GLOBAL_LOG_DIR}/cm-push-watchdog.log"

_ts() { date '+%Y-%m-%d %H:%M:%S'; }

_log_global() {
  echo "$(_ts) [watchdog] $*" | tee -a "$GLOBAL_LOG" >&2
}

# Per-project log helper — call after PROJECT_LOG is set.
_log() {
  local msg="$*"
  echo "$(_ts) [watchdog][$CURRENT_PROJECT] $msg" | tee -a "$PROJECT_LOG" >> "$GLOBAL_LOG" 2>/dev/null || true
  echo "$(_ts) [watchdog][$CURRENT_PROJECT] $msg" >&2
}

# ---------------------------------------------------------------------------
# projects.cfg parser — delegate to projects_cfg_list (handles INI + legacy)
# ---------------------------------------------------------------------------
_list_projects() {
  local cfg="${KANBAN_ROOT}/projects.cfg"
  if [[ ! -f "$cfg" ]]; then
    _log_global "projects.cfg not found at $cfg — nothing to do"
    return 0
  fi
  # projects_cfg_list emits project names in priority order, one per line,
  # parsing INI [project:NAME] sections correctly. Silence stderr to avoid
  # interleaving the library's missing-cfg warning with our log line.
  projects_cfg_list 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# Validate a project name corresponds to a real directory under projects/.
# Returns 0 if valid, 1 otherwise. Logs a warning on rejection.
# ---------------------------------------------------------------------------
_validate_project_name() {
  local name="$1"
  if [[ -z "$name" ]]; then
    return 1
  fi
  # Reject names that contain shell-suspicious characters
  # (defensive against ad-hoc parser leakage like '[project').
  if [[ ! "$name" =~ ^[a-zA-Z0-9_-]+$ ]]; then
    _log_global "Rejecting suspicious project name: '$name' (does not match [a-zA-Z0-9_-]+)"
    return 1
  fi
  if [[ ! -d "${KANBAN_ROOT}/projects/${name}" ]]; then
    _log_global "Project directory does not exist: ${KANBAN_ROOT}/projects/${name} — skipping '$name'"
    return 1
  fi
  return 0
}

# ---------------------------------------------------------------------------
# Main per-project logic
# ---------------------------------------------------------------------------
_process_project() {
  local project_name="$1"
  CURRENT_PROJECT="$project_name"

  local project_dir="${KANBAN_ROOT}/projects/${project_name}"
  local log_dir="${project_dir}/logs"
  mkdir -p "$log_dir"
  PROJECT_LOG="${log_dir}/cm-push-watchdog.log"

  # --- HALT check ---
  if [[ -f "${project_dir}/HALT" ]]; then
    _log "HALT file present — skipping project"
    return 0
  fi

  # --- push_to_remote check ---
  # Projects with push_to_remote=false are origin-isolated by design; the
  # watchdog MUST NOT push them out-of-band.  Skip immediately and log so the
  # operator can grep for the local-only signal.
  local _watchdog_push_to_remote
  _watchdog_push_to_remote="$(KANBAN_ROOT="$KANBAN_ROOT" PGAI_PROJECT_NAME="$project_name" \
    pp_push_to_remote "$project_name" 2>/dev/null || echo "true")"
  if [[ "$_watchdog_push_to_remote" == "false" ]]; then
    _log "[push_to_remote=false] skipping origin push for ${project_name}: push_to_remote=false — project is origin-isolated"
    return 0
  fi

  # --- Read project config for dev_tree_path ---
  # Use the project_paths.sh helpers so we handle both project.cfg (INI)
  # and PROJECT.cfg (legacy bash-style key=value).
  local cfg_file dev_tree
  cfg_file="$(_pp_project_cfg_file "$project_dir")"

  if [[ -z "$cfg_file" ]]; then
    _log "No project.cfg or PROJECT.cfg found in $project_dir — skipping"
    return 0
  fi

  dev_tree="$(_pp_read_cfg_key "$cfg_file" project dev_tree_path "")"

  if [[ -z "$dev_tree" ]]; then
    _log "dev_tree_path not set in $cfg_file — skipping"
    return 0
  fi

  # --- Resolve MAIN_BRANCH via pp_prefix_branch ---
  # For projects with branch_prefix=ai_, MAIN_BRANCH=ai_main.
  # For projects with no branch_prefix, MAIN_BRANCH=main unchanged.
  local MAIN_BRANCH
  MAIN_BRANCH="$(KANBAN_ROOT="$KANBAN_ROOT" PGAI_PROJECT_NAME="$project_name" \
    pp_prefix_branch "$project_name" "main" 2>/dev/null || echo "main")"

  # --- Verify git repo ---
  if ! git -C "$dev_tree" rev-parse --git-dir &>/dev/null 2>&1; then
    _log "dev_tree_path '$dev_tree' is not a git repository — skipping"
    return 0
  fi

  # --- Fetch from origin to get current remote state ---
  # Best-effort: network or credential failure just means we can't compare accurately.
  local fetch_ok=true
  if ! git -C "$dev_tree" fetch origin --tags --quiet 2>/dev/null; then
    _log "WARNING: fetch from origin failed — push detection may use stale remote state"
    fetch_ok=false
  fi

  # --- Check: is local MAIN_BRANCH ahead of origin/MAIN_BRANCH? ---
  local push_main=false
  # Verify both refs exist before comparing
  if git -C "$dev_tree" rev-parse --verify "$MAIN_BRANCH" &>/dev/null 2>&1 && \
     git -C "$dev_tree" rev-parse --verify "origin/$MAIN_BRANCH" &>/dev/null 2>&1; then
    local commits_ahead
    commits_ahead="$(git -C "$dev_tree" rev-list --count "origin/${MAIN_BRANCH}..${MAIN_BRANCH}" 2>/dev/null || echo 0)"
    if [[ "$commits_ahead" -gt 0 ]]; then
      _log "$MAIN_BRANCH is $commits_ahead commit(s) ahead of origin/$MAIN_BRANCH — push needed"
      push_main=true
    else
      _log "$MAIN_BRANCH is up to date with origin/$MAIN_BRANCH"
    fi
  elif git -C "$dev_tree" rev-parse --verify "$MAIN_BRANCH" &>/dev/null 2>&1; then
    # Local MAIN_BRANCH exists but origin/MAIN_BRANCH does not — definitely needs push
    _log "origin/$MAIN_BRANCH does not exist — local $MAIN_BRANCH needs initial push"
    push_main=true
  else
    _log "local $MAIN_BRANCH branch does not exist — nothing to push for main"
  fi

  # --- Check: local tags absent from origin ---
  # Compare local tags to tags known on origin after fetch.
  local missing_tags=()
  local all_local_tags
  all_local_tags="$(git -C "$dev_tree" tag 2>/dev/null | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' || true)"
  if [[ -n "$all_local_tags" ]]; then
    while IFS= read -r tag; do
      [[ -z "$tag" ]] && continue
      # A tag is "on origin" when it appears in the remote-tracking namespace
      if ! git -C "$dev_tree" ls-remote --tags origin "refs/tags/${tag}" 2>/dev/null | grep -q .; then
        missing_tags+=("$tag")
      fi
    done <<< "$all_local_tags"
  fi

  if [[ ${#missing_tags[@]} -gt 0 ]]; then
    _log "Tags missing from origin: ${missing_tags[*]}"
  else
    _log "All local tags are present on origin"
  fi

  # --- Nothing to do? ---
  if [[ "$push_main" == "false" && ${#missing_tags[@]} -eq 0 ]]; then
    _log "Nothing to push — idempotent exit"
    return 0
  fi

  # --- Dry-run mode ---
  if [[ "$DRY_RUN" == "true" ]]; then
    if [[ "$push_main" == "true" ]]; then
      _log "[dry-run] would: git -C '$dev_tree' push origin $MAIN_BRANCH"
    fi
    if [[ ${#missing_tags[@]} -gt 0 ]]; then
      _log "[dry-run] would: git -C '$dev_tree' push origin --tags"
    fi
    return 0
  fi

  # --- Push MAIN_BRANCH ---
  local push_main_rc=0
  if [[ "$push_main" == "true" ]]; then
    _log "Pushing $MAIN_BRANCH to origin..."
    if git -C "$dev_tree" push origin "$MAIN_BRANCH" 2>&1 | \
       while IFS= read -r line; do _log "  git: $line"; done; then
      _log "$MAIN_BRANCH pushed successfully"
    else
      push_main_rc=$?
      _log "WARNING: push origin $MAIN_BRANCH failed (exit code $push_main_rc) — will retry on next tick"
    fi
  fi

  # --- Push missing tags ---
  local push_tags_rc=0
  if [[ ${#missing_tags[@]} -gt 0 ]]; then
    _log "Pushing tags to origin..."
    if git -C "$dev_tree" push origin --tags 2>&1 | \
       while IFS= read -r line; do _log "  git: $line"; done; then
      _log "Tags pushed successfully"
    else
      push_tags_rc=$?
      _log "WARNING: push origin --tags failed (exit code $push_tags_rc) — will retry on next tick"
    fi
  fi

  # Log overall outcome for this project
  if [[ $push_main_rc -eq 0 && $push_tags_rc -eq 0 ]]; then
    _log "Push complete for $project_name"
  else
    _log "One or more pushes failed for $project_name (main_rc=$push_main_rc tags_rc=$push_tags_rc)"
  fi

  # Always return 0 — individual push failures are logged, not fatal.
  return 0
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
_log_global "Starting cm-push-watchdog (kanban_root=$KANBAN_ROOT dry_run=$DRY_RUN)"

# Initialise CURRENT_PROJECT and PROJECT_LOG with safe defaults
# (overwritten inside _process_project)
CURRENT_PROJECT="(none)"
PROJECT_LOG="$GLOBAL_LOG"

if [[ -n "$SINGLE_PROJECT" ]]; then
  _log_global "Single-project mode: $SINGLE_PROJECT"
  if _validate_project_name "$SINGLE_PROJECT"; then
    _process_project "$SINGLE_PROJECT"
  else
    _log_global "Invalid or unknown project: $SINGLE_PROJECT"
    exit 0
  fi
else
  while IFS= read -r project; do
    [[ -z "$project" ]] && continue
    if _validate_project_name "$project"; then
      _process_project "$project"
    fi
  done < <(_list_projects)
fi

_log_global "cm-push-watchdog finished"
exit 0
