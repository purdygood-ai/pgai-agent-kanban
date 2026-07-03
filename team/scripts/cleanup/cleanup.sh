#!/usr/bin/env bash
# cleanup.sh
# Standalone cleanup script for the pgai-agent-kanban system.
# Designed for cron invocation. Pure bash, no LLM involvement.
#
# Actions (full run, default):
#   0. Purge trivial logs (small AND old) from $KANBAN_ROOT/logs/agents/
#   1. Delete log files older than PGAI_CLEANUP_RETENTION_DAYS from PGAI_LOGS_DIR
#   2. Delete DONE/WONT-DO task folders older than N days from PGAI_TASKS_DIR
#   3. Archive requirements docs where Target Version <= Last Released
#   4. Rotate per-project debug logs whose mtime is from a previous UTC date
#   5. Archive briefs older than N days from PGAI_BRIEFS_DIR
#   6. Purge contents of PGAI_AGENT_KANBAN_TEMP_DIR (framework temp space)
#   7. Write a summary log to PGAI_LOGS_DIR/cleanup-YYYYMMDD-HHMMSS.log
#
# With --trivial-only:
#   Runs ONLY Step 0 (trivial-log purge) and exits. Intended for daily cron
#   invocation to keep batch-log directories from accumulating thousands of
#   near-empty wake-script logs.
#
# With --temp-only:
#   Runs ONLY the temp-dir purge (Step 6) and exits. Safe to run at any time;
#   does NOT touch logs, task folders, archives, or any non-temp path.
#   Purges all contents of PGAI_AGENT_KANBAN_TEMP_DIR without deleting
#   the directory itself, so the temp root stays around for the next run.
#
# Usage:
#   cleanup.sh --project <name> [--dry-run] [--trivial-only] [--temp-only]
#
# Options:
#   --project <name>  Project name (required when PGAI_PROJECT_NAME is not set)
#   --dry-run         Preview actions without making any changes
#   --trivial-only    Run only the trivial-log purge (Step 0) and exit
#   --temp-only       Run only the framework temp-dir purge (Step 6) and exit
#
# Configuration (set in config.cfg or environment):
#   PGAI_CLEANUP_RETENTION_DAYS        Days to retain logs (default: 30)
#   PGAI_CLEANUP_TASK_RETENTION_DAYS   Days to retain terminal task folders before deletion
#                                      (default: 7). Independent of log retention.
#   PGAI_CLEANUP_TRIVIAL_LOG_BYTES     Byte threshold for "trivial" logs (default: 1700).
#                                      Logs SMALLER than this value are eligible for
#                                      aggressive purge when also older than the hours
#                                      threshold below.
#   PGAI_CLEANUP_TRIVIAL_LOG_HOURS     Age threshold in hours for trivial purge (default: 6).
#                                      Only logs OLDER than this many hours are purged.
#                                      Two-tier model: trivial (small+old) purged daily;
#                                      substantive logs kept for PGAI_CLEANUP_RETENTION_DAYS.
#   PGAI_LOGS_DIR                      Directory for log files
#   PGAI_TASKS_DIR                Directory for task folders
#   PGAI_BRIEFS_DIR               Directory for PM briefs
#   PGAI_ARCHIVE_DIR              Archive destination root
#   PGAI_AGENT_KANBAN_ROOT_PATH  Kanban root (default: $HOME/pgai_agent_kanban)
#   PGAI_AGENT_KANBAN_TEMP_DIR          Framework temp directory to purge (canonical override)
#                                       (resolved via temp.sh resolver; see temp.sh for fallback)
#
# Config file sourcing order (lowest to highest precedence):
#   1. $HOME/.pgairc (user-wide, if present)
#   2. $PGAI_AGENT_KANBAN_ROOT_PATH/config.cfg (per-install, if present)
#   3. Environment variables already set in the calling shell
#
# Exit codes:
#   0 = success
#   1 = error (archive dir not writable, release-state.md unreadable, etc.)

# ---------------------------------------------------------------------------
# Bootstrap: resolve kanban root and source config files BEFORE strict mode.
# User config files may contain unset variable refs or interactive checks that
# would trip set -euo pipefail.
# ---------------------------------------------------------------------------

TEAM_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"

# Source project_paths lib for pp_* helpers and temp lib for pgai_temp_* helpers
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/project_paths.sh
source "${_SCRIPT_DIR}/../lib/project_paths.sh"
# shellcheck source=lib/temp.sh
source "${_SCRIPT_DIR}/../lib/temp.sh"
# shellcheck source=lib/projects.sh
source "${_SCRIPT_DIR}/../lib/projects.sh"
# shellcheck source=lib/dev_tree.sh
source "${_SCRIPT_DIR}/../lib/dev_tree.sh"
# shellcheck source=lib/temp_purge.sh
source "${_SCRIPT_DIR}/../lib/temp_purge.sh"
unset _SCRIPT_DIR

# Source user-wide config first (lowest precedence among files)
[[ -f "$HOME/.pgairc" ]] && source "$HOME/.pgairc"

# Source user config file using the standard pgai-kanban.cfg name
[[ -f "$HOME/.config/pgai-kanban.cfg" ]] && source "$HOME/.config/pgai-kanban.cfg"

# Source per-install config — INI format (kanban.cfg) replaces legacy config.cfg
# read_ini available via project_paths.sh (sourced above).
if [[ -f "$TEAM_ROOT/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "$TEAM_ROOT/kanban.cfg" chain pm_mode automatic)}"
    export PGAI_CLEANUP_RETENTION_DAYS="${PGAI_CLEANUP_RETENTION_DAYS:-$(read_ini "$TEAM_ROOT/kanban.cfg" paths cleanup_retention_days 30)}"
fi
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"
# Classification (a): cleanup purges logs, task folders, archives, briefs, and
# temp dirs under $KANBAN_ROOT; it does not access any project dev tree.
# Global require_dev_tree removed (D5).

# ---------------------------------------------------------------------------
# Strict mode — enable AFTER sourcing config files
# ---------------------------------------------------------------------------
set -euo pipefail

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
DRY_RUN=false
TRIVIAL_ONLY=false
TEMP_ONLY=false
_CLEANUP_PROJECT_ARG=""
_args=("$@")
_ai=0
while [[ $_ai -lt ${#_args[@]} ]]; do
  arg="${_args[$_ai]}"
  case "$arg" in
    --dry-run)
      DRY_RUN=true
      ;;
    --trivial-only)
      TRIVIAL_ONLY=true
      ;;
    --temp-only)
      TEMP_ONLY=true
      ;;
    --project)
      _next=$(( _ai + 1 ))
      if [[ $_next -ge ${#_args[@]} ]] || [[ -z "${_args[$_next]:-}" ]]; then
        echo "ERROR: --project requires a project name" >&2
        echo "Usage: $(basename "$0") --project <name> [--dry-run] [--trivial-only] [--temp-only]" >&2
        exit 1
      fi
      _CLEANUP_PROJECT_ARG="${_args[$_next]}"
      _ai=$(( _ai + 1 ))
      ;;
    *)
      echo "ERROR: unknown argument: $arg" >&2
      echo "Usage: $(basename "$0") --project <name> [--dry-run] [--trivial-only] [--temp-only]" >&2
      exit 1
      ;;
  esac
  _ai=$(( _ai + 1 ))
done
unset _args _ai _next

# ---------------------------------------------------------------------------
# Paths and tunables (apply defaults after config is sourced)
# ---------------------------------------------------------------------------
PGAI_CLEANUP_RETENTION_DAYS="${PGAI_CLEANUP_RETENTION_DAYS:-30}"
# Separate threshold for task folder deletion (default: 7 days).
# Keeps recently-completed tasks available for short-term inspection even if
# log retention is set lower. Override via PGAI_CLEANUP_TASK_RETENTION_DAYS.
PGAI_CLEANUP_TASK_RETENTION_DAYS="${PGAI_CLEANUP_TASK_RETENTION_DAYS:-7}"
# Trivial-log purge thresholds (two-tier retention model).
# A log is "trivial" if it is SMALLER than PGAI_CLEANUP_TRIVIAL_LOG_BYTES bytes
# AND OLDER than PGAI_CLEANUP_TRIVIAL_LOG_HOURS hours. These logs are purged
# aggressively (Step 0 / --trivial-only) to prevent cron-driven accumulation.
# Substantive logs (>= threshold size) are kept for PGAI_CLEANUP_RETENTION_DAYS.
PGAI_CLEANUP_TRIVIAL_LOG_BYTES="${PGAI_CLEANUP_TRIVIAL_LOG_BYTES:-1700}"
PGAI_CLEANUP_TRIVIAL_LOG_HOURS="${PGAI_CLEANUP_TRIVIAL_LOG_HOURS:-6}"

# --- Resolve target project (required) ---
# Resolution order: --project flag > $PGAI_PROJECT_NAME > fail loud.
# There is no fallback to the first registered project.
_CLEANUP_PROJECT="${_CLEANUP_PROJECT_ARG:-${PGAI_PROJECT_NAME:-}}"
if [[ -z "$_CLEANUP_PROJECT" ]]; then
  echo "ERROR: no project specified; pass --project <name> or set PGAI_PROJECT_NAME" >&2
  exit 1
fi

_pp_tasks="$(pp_tasks_dir "$_CLEANUP_PROJECT")"
_pp_req="$(pp_requirements_dir "$_CLEANUP_PROJECT")"
# Wake batch logs live at $KANBAN_ROOT/logs/agents/.
PGAI_LOGS_DIR="${PGAI_LOGS_DIR:-${KANBAN_ROOT}/logs/agents}"
PGAI_TASKS_DIR="${PGAI_TASKS_DIR:-${_pp_tasks}}"
PGAI_BRIEFS_DIR="${PGAI_BRIEFS_DIR:-$TEAM_ROOT/briefs}"
PGAI_ARCHIVE_DIR="${PGAI_ARCHIVE_DIR:-${_pp_tasks}/archive}"
PGAI_REQUIREMENTS_DIR="${PGAI_REQUIREMENTS_DIR:-${_pp_req}}"
unset _pp_tasks _pp_req

# release-state.md location.
# Prefer project-scoped release-state.md; fall back to legacy team/release-state.md
# only if the project-scoped file does not exist.
RELEASE_STATE_FILE=""
if command -v pp_project_root >/dev/null 2>&1; then
  if [[ -f "$(pp_project_root "$_CLEANUP_PROJECT" 2>/dev/null)/release-state.md" ]]; then
    RELEASE_STATE_FILE="$(pp_project_root "$_CLEANUP_PROJECT")/release-state.md"
  fi
fi
[[ -z "$RELEASE_STATE_FILE" ]] && RELEASE_STATE_FILE="$TEAM_ROOT/release-state.md"

# Log file for this run
RUN_TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
CLEANUP_LOG="$PGAI_LOGS_DIR/cleanup-${RUN_TIMESTAMP}.log"

# ---------------------------------------------------------------------------
# Cleanup trap
# ---------------------------------------------------------------------------
cleanup_on_exit() {
  local exit_code=$?
  jobs -p 2>/dev/null | xargs -r kill 2>/dev/null || true
  wait 2>/dev/null || true
  exit $exit_code
}
trap cleanup_on_exit EXIT

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------
# Ensure logs dir exists before we try to open the log file.
# (Logs dir should always exist; we just make sure before writing.)
mkdir -p "$PGAI_LOGS_DIR"

log() {
  local msg="[$(date -Iseconds)] cleanup: $*"
  echo "$msg"
  echo "$msg" >> "$CLEANUP_LOG"
}

log_action() {
  # Log an action. In dry-run mode prefix with [DRY-RUN].
  if [[ "$DRY_RUN" == "true" ]]; then
    log "[DRY-RUN] $*"
  else
    log "$*"
  fi
}

# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------
TRIVIAL_LOGS_DELETED=0
LOGS_DELETED=0
TASKS_DELETED=0
REQUIREMENTS_ARCHIVED=0
LOGS_ROTATED=0
BRIEFS_ARCHIVED=0
TEMP_ITEMS_PURGED=0

# ---------------------------------------------------------------------------
# Semver helpers (sourced from shared lib)
# ---------------------------------------------------------------------------
# shellcheck source=lib/semver.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/semver.sh"

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------
# In --trivial-only or --temp-only mode we do not need release-state.md
# (no archival step).  Skip the preflight in those cases so these flags are
# safe to run even before the first release ships.
LAST_RELEASED=""
if [[ "$TRIVIAL_ONLY" == "false" && "$TEMP_ONLY" == "false" ]]; then
  if [[ ! -f "$RELEASE_STATE_FILE" ]]; then
    echo "ERROR: release-state.md not found at $RELEASE_STATE_FILE" >&2
    exit 1
  fi

  # Resolve last released version via canonical helper (pp_last_released_version).
  # The helper reads git tags merged into origin/main on the project's dev tree,
  # so it works correctly after install.sh --upgrade removes the deprecated
  # "Last Released:" field from release-state.md.  It returns the v0.0.0
  # sentinel when no releases exist yet; treat that the same as "none".
  LAST_RELEASED="$(pp_last_released_version "$_CLEANUP_PROJECT")"

  if [[ -z "$LAST_RELEASED" ]] || [[ "$LAST_RELEASED" == "none" ]] || [[ "$LAST_RELEASED" == "v0.0.0" ]]; then
    LAST_RELEASED=""
    log "release-state.md has no Last Released version; skipping requirements archival"
  fi
fi

# ---------------------------------------------------------------------------
# Startup log
# ---------------------------------------------------------------------------
log "starting cleanup run (dry_run=$DRY_RUN trivial_only=$TRIVIAL_ONLY temp_only=$TEMP_ONLY retention_days=$PGAI_CLEANUP_RETENTION_DAYS task_retention_days=$PGAI_CLEANUP_TASK_RETENTION_DAYS trivial_bytes=$PGAI_CLEANUP_TRIVIAL_LOG_BYTES trivial_hours=$PGAI_CLEANUP_TRIVIAL_LOG_HOURS)"
log "last_released=${LAST_RELEASED:-n/a}"

# ---------------------------------------------------------------------------
# Lazy create archive directories
# ---------------------------------------------------------------------------
ensure_archive_dirs() {
  local dirs=(
    "$PGAI_ARCHIVE_DIR"
    "$PGAI_ARCHIVE_DIR/requirements"
    "$PGAI_ARCHIVE_DIR/requirements/priority"
    "$PGAI_ARCHIVE_DIR/briefs"
  )
  for d in "${dirs[@]}"; do
    if [[ ! -d "$d" ]]; then
      if [[ "$DRY_RUN" == "true" ]]; then
        log "[DRY-RUN] would create directory: $d"
      else
        mkdir -p "$d"
        log "created archive directory: $d"
      fi
    fi
  done

  # Validate writability (only when not in dry-run)
  if [[ "$DRY_RUN" == "false" ]]; then
    for d in "${dirs[@]}"; do
      if [[ -d "$d" ]] && [[ ! -w "$d" ]]; then
        log "ERROR: archive directory not writable: $d"
        exit 1
      fi
    done
  fi
}

if [[ "$TRIVIAL_ONLY" == "false" && "$TEMP_ONLY" == "false" ]]; then
  ensure_archive_dirs
fi

# ---------------------------------------------------------------------------
# Step 0: Trivial-log purge (small AND old batch logs)
# ---------------------------------------------------------------------------
# Purge target: ONLY $KANBAN_ROOT/logs/agents/ — the wake-script per-firing
# batch log directory. Per-task logs at projects/*/tasks/<task_id>/
# logs/ are unaffected (different path; not high-volume cron-driven).
# cron-<agent>.log files at $KANBAN_ROOT/logs/ are also unaffected — they
# grow by append, not by file count, and are managed by the normal log
# retention sweep.
#
# A log is "trivial" if:
#   - File size < PGAI_CLEANUP_TRIVIAL_LOG_BYTES (default 1700 bytes)
#   - File age  > PGAI_CLEANUP_TRIVIAL_LOG_HOURS (default 6 hours)
#
# Substantive logs (>= size threshold) are left for the normal retention sweep.
# ---------------------------------------------------------------------------

purge_trivial_logs() {
  local trivial_bytes="$1"
  local trivial_hours="$2"
  local dry_run="$3"
  local trivial_minutes=$(( trivial_hours * 60 ))
  local count=0
  local batch_log_dir="${KANBAN_ROOT}/logs/agents"

  if [[ ! -d "$batch_log_dir" ]]; then
    echo "$count"
    return 0
  fi

  # Use process substitution + null-delimited read for safety with unusual filenames.
  while IFS= read -r -d '' logfile; do
    count=$(( count + 1 ))
    if [[ "$dry_run" == "true" ]]; then
      log "[DRY-RUN] trivial-purge would delete: $logfile"
    else
      rm -f "$logfile"
    fi
  done < <(find "$batch_log_dir" \
      -type f \
      -name "*-batch-*.log" \
      -mmin "+${trivial_minutes}" \
      -size "-${trivial_bytes}c" \
      -print0 \
      2>/dev/null)

  echo "$count"
}

# ---------------------------------------------------------------------------
# --temp-only fast path: purge temp dir and exit without touching anything else.
# --temp-only skips Steps 0-5 entirely; only the temp purge runs.
# No logs are touched, no task folders are inspected.
#
# Sweeps two categories of temp space:
#   1. The install-wide framework temp root (PGAI_AGENT_KANBAN_TEMP_DIR).
#      Only contents are removed; the root directory itself is preserved.
#   2. Each registered project's per-project temp dir (PP_TEMP_DIR, resolved
#      via pp_load_config for each project registered in projects.cfg).
#      Only contents are removed; per-project root dirs are preserved.
#      Projects without a temp_dir config default to projects/<project_name>
#      under the install-wide root (pp_load_config applies the default).
# ---------------------------------------------------------------------------

# Internal helper: sweep all contents of a single temp directory, preserving
# the directory root itself. Logs each item removed. Updates TEMP_ITEMS_PURGED.
# Used for per-project temp dirs whose contents are project-owned and not
# subject to the install-wide allowlist.  For allowlist-guarded sweeps of the
# install-wide root, use temp_purge_sweep_dir (from lib/temp_purge.sh).
#   $1 — directory path to sweep
#   $2 — label for log messages (e.g. "install-wide" or "project pgai-agent-kanban")
_sweep_temp_dir() {
  local _dir="$1"
  local _label="$2"
  if [[ -d "$_dir" ]]; then
    for _item in "${_dir}"/*; do
      [[ -e "$_item" ]] || continue
      log_action "temp-only purge (${_label}): $_item"
      if [[ "$DRY_RUN" == "false" ]]; then
        rm -rf "$_item"
      fi
      TEMP_ITEMS_PURGED=$(( TEMP_ITEMS_PURGED + 1 ))
    done
  else
    log "temp-only: directory does not exist, skipping (${_label}): $_dir"
  fi
}

if [[ "$TEMP_ONLY" == "true" ]]; then
  _temp_root="$(pgai_temp_dir)"

  # --- Wake lock guard: abort temp purge if any wake lock is held ---
  # Wake scripts acquire flock locks under $TEAM_ROOT/locks/ while agents are
  # running.  Purging temp space while a wake lock is held risks destroying
  # active agent worktrees, token-capture buffers, and other live transients.
  # When a held lock is detected, log a warning and exit 0 (the rest of the
  # sweep — logs, archives, task folders — is not running in --temp-only mode,
  # so there is nothing left to do).
  if ! temp_purge_check_wake_locks "${TEAM_ROOT}/locks"; then
    log "WARNING: temp purge aborted due to held wake lock — re-run after all agents finish"
    log "--- Summary (temp-only mode) ---"
    log "dry_run:              $DRY_RUN"
    log "temp_dir:             $(pgai_temp_dir)"
    log "temp_items_purged:    0 (aborted — wake lock held)"
    log "log written to:       $CLEANUP_LOG"
    log "cleanup complete (temp-only, aborted)"
    exit 0
  fi

  # --- 1. Enumerate per-project temp dirs BEFORE the install-wide sweep ---
  # This must happen first so we can exclude per-project dirs from the
  # install-wide loop.  Per-project dirs nest UNDER the install-wide root
  # (<framework_temp_root>/<temp_dir>); if the install-wide sweep ran first
  # it would rm -rf those subdirs and the per-project sweep would find nothing.
  # Enumerate per-project paths first, exclude them from the install-wide sweep,
  # then sweep each per-project dir's contents via _sweep_temp_dir (which already
  # preserves the root).
  log "--- temp-only: enumerating per-project temp directories ---"
  declare -A _per_project_dirs  # associative array: path → project label
  _project_list="$(projects_cfg_list 2>/dev/null)" || _project_list=""
  if [[ -n "$_project_list" ]]; then
    while IFS= read -r _proj; do
      [[ -n "$_proj" ]] || continue
      if pp_load_config "$_proj" 2>/dev/null; then
        _proj_temp_dir="${PP_TEMP_DIR:-}"
        if [[ -n "$_proj_temp_dir" ]]; then
          _per_project_dirs["$_proj_temp_dir"]="project ${_proj}"
          log "temp-only: registered per-project temp dir for '$_proj': $_proj_temp_dir"
        else
          log "temp-only: PP_TEMP_DIR empty after pp_load_config for '$_proj', skipping"
        fi
      else
        log "temp-only: pp_load_config failed for project '$_proj' (no project.cfg?), skipping"
      fi
    done <<< "$_project_list"
  else
    log "temp-only: no projects registered in projects.cfg"
  fi
  unset _project_list _proj _proj_temp_dir

  # --- 2. Sweep install-wide framework temp root, excluding per-project dirs ---
  # Iterate top-level entries under the install-wide root.  Any entry whose
  # absolute path is a registered per-project temp dir is skipped here — it
  # will be swept individually in step 3, preserving the root.
  log "--- temp-only: purging install-wide framework temp directory (excluding per-project roots): $_temp_root ---"
  if [[ -d "$_temp_root" ]]; then
    for _item in "${_temp_root}"/*; do
      [[ -e "$_item" ]] || continue
      # Per-project dir exclusion: skip entries whose absolute path is a
      # registered per-project temp root (they are swept individually in step 3).
      if [[ -n "${_per_project_dirs["$_item"]+_}" ]]; then
        log "temp-only: skipping per-project root during install-wide sweep: $_item (${_per_project_dirs[$_item]})"
        continue
      fi
      # Allowlist check: skip foreign entries (not kanban-owned transients).
      _item_basename="$(basename "$_item")"
      if ! temp_purge_basename_allowed "$_item_basename"; then
        log "temp purge: skipping foreign entry $_item"
        continue
      fi
      # worktrees/ gets per-task WORKING-state treatment.
      if [[ "$_item_basename" == "worktrees" && -d "$_item" ]]; then
        temp_purge_sweep_worktrees "$_item" "install-wide/worktrees" "${PGAI_TASKS_DIR:-}"
        continue
      fi
      log_action "temp-only purge (install-wide): $_item"
      if [[ "$DRY_RUN" == "false" ]]; then
        rm -rf "$_item"
      fi
      TEMP_ITEMS_PURGED=$(( TEMP_ITEMS_PURGED + 1 ))
    done
  else
    log "temp-only: install-wide temp root does not exist, skipping: $_temp_root"
  fi
  unset _item _item_basename

  # --- 3. Sweep each per-project dir's CONTENTS (preserving the root dirs) ---
  log "--- temp-only: purging per-project temp directory contents ---"
  for _ppath in "${!_per_project_dirs[@]}"; do
    _plabel="${_per_project_dirs[$_ppath]}"
    log "temp-only: sweeping per-project temp dir (${_plabel}): $_ppath"
    _sweep_temp_dir "$_ppath" "$_plabel"
  done
  unset _ppath _plabel _per_project_dirs

  unset _temp_root

  log "--- Summary (temp-only mode) ---"
  log "dry_run:              $DRY_RUN"
  log "temp_dir:             $(pgai_temp_dir)"
  log "temp_items_purged:    $TEMP_ITEMS_PURGED"
  log "log written to:       $CLEANUP_LOG"
  log "cleanup complete (temp-only)"
  exit 0
fi

log "--- Step 0: purge trivial logs (size<${PGAI_CLEANUP_TRIVIAL_LOG_BYTES}B, age>${PGAI_CLEANUP_TRIVIAL_LOG_HOURS}h) ---"

_trivial_result=0
if [[ -d "${TEAM_ROOT}/projects" ]]; then
  _trivial_result="$(purge_trivial_logs \
      "$PGAI_CLEANUP_TRIVIAL_LOG_BYTES" \
      "$PGAI_CLEANUP_TRIVIAL_LOG_HOURS" \
      "$DRY_RUN")"
else
  log "projects/ directory not found under $TEAM_ROOT, skipping trivial-log purge"
fi

TRIVIAL_LOGS_DELETED="${_trivial_result:-0}"
unset _trivial_result

if [[ "$DRY_RUN" == "true" ]]; then
  log "[DRY-RUN] trivial-purge: would delete $TRIVIAL_LOGS_DELETED trivial logs (size<${PGAI_CLEANUP_TRIVIAL_LOG_BYTES}B, age>${PGAI_CLEANUP_TRIVIAL_LOG_HOURS}h)"
else
  log "trivial-purge: deleted $TRIVIAL_LOGS_DELETED trivial logs (size<${PGAI_CLEANUP_TRIVIAL_LOG_BYTES}B, age>${PGAI_CLEANUP_TRIVIAL_LOG_HOURS}h)"
fi

# If --trivial-only, emit summary and exit now — skip all remaining steps.
if [[ "$TRIVIAL_ONLY" == "true" ]]; then
  log "--- Summary (trivial-only mode) ---"
  log "dry_run:                    $DRY_RUN"
  log "trivial_log_bytes:          $PGAI_CLEANUP_TRIVIAL_LOG_BYTES"
  log "trivial_log_hours:          $PGAI_CLEANUP_TRIVIAL_LOG_HOURS"
  log "trivial_logs_deleted:  $TRIVIAL_LOGS_DELETED"
  log "log written to:        $CLEANUP_LOG"
  log "cleanup complete (trivial-only)"
  exit 0
fi

# ---------------------------------------------------------------------------
# Step 1: Delete old log files
# ---------------------------------------------------------------------------
log "--- Step 1: prune old log files from $PGAI_LOGS_DIR ---"

if [[ -d "$PGAI_LOGS_DIR" ]]; then
  while IFS= read -r -d '' logfile; do
    # Skip the current cleanup log itself
    [[ "$logfile" == "$CLEANUP_LOG" ]] && continue
    log_action "delete log: $logfile"
    if [[ "$DRY_RUN" == "false" ]]; then
      rm -f "$logfile"
    fi
    LOGS_DELETED=$(( LOGS_DELETED + 1 ))
  done < <(find "$PGAI_LOGS_DIR" -maxdepth 1 -type f -mtime +"$PGAI_CLEANUP_RETENTION_DAYS" -print0 2>/dev/null)
else
  log "logs directory not found, skipping: $PGAI_LOGS_DIR"
fi

log "logs pruned: $LOGS_DELETED"

# ---------------------------------------------------------------------------
# Step 2: Delete terminal task folders (DONE or WONT-DO) older than N days
# ---------------------------------------------------------------------------
log "--- Step 2: prune terminal task folders from $PGAI_TASKS_DIR ---"

# Walk CLAUDE-* folders, skip queues/ subdirectory
if [[ -d "$PGAI_TASKS_DIR" ]]; then
  for task_dir in "$PGAI_TASKS_DIR"/CLAUDE-*/; do
    [[ -d "$task_dir" ]] || continue

    # Skip the queues subdirectory (safety guard in case glob matched it)
    case "$task_dir" in
      */queues/*|*/queues/) continue ;;
    esac

    status_file="$task_dir/status.md"
    [[ -f "$status_file" ]] || continue

    # Read state from status.md
    task_state=""
    while IFS= read -r line; do
      if [[ "$line" =~ ^##[[:space:]]*State[[:space:]]*$ ]]; then
        while IFS= read -r val_line; do
          val_line="${val_line#"${val_line%%[![:space:]]*}"}"
          if [[ -n "$val_line" ]] && [[ "$val_line" != "##"* ]]; then
            task_state="$val_line"
            break
          fi
          [[ "$val_line" == "##"* ]] && break
        done
        break
      fi
    done < "$status_file"

    # Safeguard: only delete terminal-state folders (DONE or WONT-DO).
    # Any other state (WORKING, BLOCKED, WAITING, BACKLOG, empty, unknown)
    # is explicitly skipped to prevent accidental deletion of active work.
    if [[ "$task_state" != "DONE" ]] && [[ "$task_state" != "WONT-DO" ]]; then
      log "SAFEGUARD: skip non-terminal task (state=${task_state:-UNKNOWN}): $task_dir"
      continue
    fi

    # Check folder age using the status.md modification time
    # (status.md is written last when a task finishes).
    # Uses PGAI_CLEANUP_TASK_RETENTION_DAYS (default: 7), which is independent of
    # the log-retention threshold (PGAI_CLEANUP_RETENTION_DAYS, default: 30).
    # Skip tasks that are too recent (not yet old enough to delete).
    if ! find "$status_file" -mtime +"$PGAI_CLEANUP_TASK_RETENTION_DAYS" -print 2>/dev/null | grep -q .; then
      log "SAFEGUARD: skip recently-completed task (state=$task_state, age<=${PGAI_CLEANUP_TASK_RETENTION_DAYS}d): $task_dir"
      continue
    fi

    # Defense-in-depth: re-read and verify state immediately before deletion.
    # Protects against the state changing between the read above and the rm below
    # (e.g. a concurrent agent updating the task while cleanup is running).
    local_state_check=""
    while IFS= read -r recheck_line; do
      if [[ "$recheck_line" =~ ^##[[:space:]]*State[[:space:]]*$ ]]; then
        while IFS= read -r recheck_val; do
          recheck_val="${recheck_val#"${recheck_val%%[![:space:]]*}"}"
          if [[ -n "$recheck_val" ]] && [[ "$recheck_val" != "##"* ]]; then
            local_state_check="$recheck_val"
            break
          fi
          [[ "$recheck_val" == "##"* ]] && break
        done
        break
      fi
    done < "$status_file"

    if [[ "$local_state_check" != "DONE" ]] && [[ "$local_state_check" != "WONT-DO" ]]; then
      log "SAFEGUARD: skipping deletion of $task_dir — state changed to '$local_state_check' during cleanup run"
      continue
    fi

    log_action "delete task folder (state=$local_state_check age>${PGAI_CLEANUP_TASK_RETENTION_DAYS}d): $task_dir"
    if [[ "$DRY_RUN" == "false" ]]; then
      rm -rf "$task_dir"
    fi
    TASKS_DELETED=$(( TASKS_DELETED + 1 ))
  done
else
  log "tasks directory not found, skipping: $PGAI_TASKS_DIR"
fi

log "task folders deleted: $TASKS_DELETED"

# ---------------------------------------------------------------------------
# Step 3: Archive requirements docs where Target Version <= Last Released
# ---------------------------------------------------------------------------
log "--- Step 3: archive shipped requirements from $PGAI_REQUIREMENTS_DIR ---"

archive_requirements_from_dir() {
  local src_dir="$1"
  local dest_dir="$2"
  local label="$3"

  [[ -d "$src_dir" ]] || return 0
  [[ -n "$LAST_RELEASED" ]] && [[ "$LAST_RELEASED" != "none" ]] || return 0

  for req_file in "$src_dir"/*.md "$src_dir"/*.txt; do
    [[ -f "$req_file" ]] || continue

    # Parse Target Version from the requirements file.
    # Canonical two-line markdown format:
    #   ## Target Version
    #   vX.Y.Z
    target_version=""
    while IFS= read -r line; do
      if [[ "$line" =~ ^##[[:space:]]*Target[[:space:]]*Version[[:space:]]*$ ]]; then
        # Next non-empty line is the value
        while IFS= read -r val_line; do
          val_line="${val_line#"${val_line%%[![:space:]]*}"}"  # ltrim
          if [[ -n "$val_line" ]] && [[ "$val_line" != "##"* ]]; then
            target_version="$val_line"
            target_version="${target_version%"${target_version##*[![:space:]]}"}"  # rtrim
            break
          fi
          [[ "$val_line" == "##"* ]] && break
        done
        break
      fi
    done < "$req_file"

    [[ -n "$target_version" ]] || continue

    if semver_lte "$target_version" "$LAST_RELEASED"; then
      local dest_file="$dest_dir/$(basename "$req_file")"
      # Idempotent: skip if already archived
      if [[ -f "$dest_file" ]]; then
        log "already archived ($label): $(basename "$req_file") (target=$target_version)"
        continue
      fi
      log_action "archive $label requirement: $(basename "$req_file") (target=$target_version <= last_released=$LAST_RELEASED)"
      if [[ "$DRY_RUN" == "false" ]]; then
        cp "$req_file" "$dest_file"
        rm -f "$req_file"
      fi
      REQUIREMENTS_ARCHIVED=$(( REQUIREMENTS_ARCHIVED + 1 ))
    fi
  done
}

# Archive standard requirements
# Defensive guard — when PGAI_REQUIREMENTS_DIR is set (non-empty) but points to a
# path that does not exist, log a clear warning and skip rather than silently
# no-op'ing. This most commonly happens when config.cfg has stale paths that
# override the per-project helper (_pp_req).
# If PGAI_REQUIREMENTS_DIR is unset, the ${VAR:-default} assignment above already
# resolved it via _pp_req to the correct per-project path; no guard needed.
if [[ -n "$PGAI_REQUIREMENTS_DIR" ]] && [[ ! -d "$PGAI_REQUIREMENTS_DIR" ]]; then
  log "WARNING: PGAI_REQUIREMENTS_DIR='$PGAI_REQUIREMENTS_DIR' does not exist; skipping archive step"
  log "  Likely cause: config.cfg has stale pre-multi-project paths that override the per-project helper."
  log "  Fix: comment out PGAI_REQUIREMENTS_DIR in config.cfg (see example_config.cfg for instructions)."
else
  archive_requirements_from_dir \
    "$PGAI_REQUIREMENTS_DIR" \
    "$PGAI_ARCHIVE_DIR/requirements" \
    "standard"
fi

# Archive priority requirements
PGAI_PRIORITY_DIR="${PGAI_PRIORITY_DIR:-$(pp_tasks_dir "$_CLEANUP_PROJECT")/queues/priority}"
archive_requirements_from_dir \
  "$PGAI_REQUIREMENTS_DIR/priority" \
  "$PGAI_ARCHIVE_DIR/requirements/priority" \
  "priority"

log "requirements archived: $REQUIREMENTS_ARCHIVED"

# ---------------------------------------------------------------------------
# Step 4: Rotate debug logs
# ---------------------------------------------------------------------------
# Rotates debug log files whose mtime date (UTC) differs from today's UTC date.
# Two scopes are covered:
#
#   A) Kanban-wide:  $KANBAN_ROOT/logs/debug/<agent>.log
#   B) Per-project:  $KANBAN_ROOT/projects/<name>/logs/debug/<agent>.log
#                    rotated into projects/<name>/logs/debug/archive/
#
# Rotation action (same for both scopes):
#   mv <debug_dir>/<agent>.log -> <debug_dir>/archive/<YYYYMMDD>-<agent>.log
#   then touch a fresh empty <agent>.log for today.
#
# Idempotent: a file already rotated today has today's mtime, so the date
# comparison yields equal and no further rotation occurs.
#
# Training corpus ($KANBAN_ROOT/logs/training/) is NOT touched — each task
# produces a unique file and no rotation is needed there.
# ---------------------------------------------------------------------------
log "--- Step 4: rotate debug logs (kanban-wide and per-project) ---"

LOGS_ROTATED=0

# _rotate_debug_dir <debug_dir> <label>
# Rotate all *.log files in <debug_dir> whose mtime date (UTC) is not today.
# <label> is a human-readable prefix used in log messages (e.g. "kanban-wide"
# or "project foo").
_rotate_debug_dir() {
  local debug_dir="$1"
  local label="$2"
  local archive_dir="${debug_dir}/archive"
  local today
  today="$(date -u +%Y%m%d)"

  # Skip when debug dir absent.
  if [[ ! -d "$debug_dir" ]]; then
    return 0
  fi

  # Ensure archive directory exists.
  if [[ ! -d "$archive_dir" ]]; then
    if [[ "$DRY_RUN" == "true" ]]; then
      log "[DRY-RUN] would create archive directory: $archive_dir"
    else
      mkdir -p "$archive_dir"
      log "created archive directory: $archive_dir"
    fi
  fi

  for log_file in "${debug_dir}"/*.log; do
    # Glob expansion produces a literal pattern string when no files match;
    # skip it.
    [[ -f "$log_file" ]] || continue

    # Determine the file's mtime date in UTC.
    local file_date
    file_date="$(date -u -r "$log_file" +%Y%m%d 2>/dev/null)" || continue

    # If the mtime date matches today, no rotation needed (idempotent guard).
    if [[ "$file_date" == "$today" ]]; then
      continue
    fi

    # Extract the agent name from the filename (strip .log suffix).
    local agent
    agent="$(basename "$log_file" .log)"

    local archived="${archive_dir}/${file_date}-${agent}.log"

    log_action "rotate debug log [${label}]: ${agent}.log (mtime_date=${file_date}) -> archive/${file_date}-${agent}.log"

    if [[ "$DRY_RUN" == "false" ]]; then
      mv "$log_file" "$archived"
      # Create a fresh empty file for today's writes.
      touch "$log_file"
    fi

    LOGS_ROTATED=$(( LOGS_ROTATED + 1 ))
  done
}

rotate_debug_logs() {
  # A) Kanban-wide debug log tree (legacy; kept for backward compatibility).
  _rotate_debug_dir "${KANBAN_ROOT}/logs/debug" "kanban-wide"

  # B) Per-project debug log trees — iterate all registered projects.
  local _proj_list
  _proj_list="$(projects_cfg_list 2>/dev/null)" || _proj_list=""
  if [[ -n "$_proj_list" ]]; then
    while IFS= read -r _proj; do
      [[ -n "$_proj" ]] || continue
      local _proj_root
      _proj_root="$(pp_project_root "$_proj" 2>/dev/null)" || {
        log "rotate debug logs: could not resolve root for project '$_proj', skipping"
        continue
      }
      _rotate_debug_dir "${_proj_root}/logs/debug" "project ${_proj}"
    done <<< "$_proj_list"
  else
    log "rotate debug logs: no projects registered in projects.cfg; skipping per-project rotation"
  fi
  unset _proj_list _proj _proj_root
}

rotate_debug_logs

log "debug logs rotated: $LOGS_ROTATED"

# ---------------------------------------------------------------------------
# Step 5: Archive old briefs
# ---------------------------------------------------------------------------
log "--- Step 5: archive old briefs from $PGAI_BRIEFS_DIR ---"

if [[ -d "$PGAI_BRIEFS_DIR" ]]; then
  while IFS= read -r -d '' brief_file; do
    local_dest="$PGAI_ARCHIVE_DIR/briefs/$(basename "$brief_file")"
    # Idempotent: skip if already archived
    if [[ -f "$local_dest" ]]; then
      log "already archived brief: $(basename "$brief_file")"
      continue
    fi
    log_action "archive brief: $brief_file"
    if [[ "$DRY_RUN" == "false" ]]; then
      cp "$brief_file" "$local_dest"
      rm -f "$brief_file"
    fi
    BRIEFS_ARCHIVED=$(( BRIEFS_ARCHIVED + 1 ))
  done < <(find "$PGAI_BRIEFS_DIR" -maxdepth 1 -type f -mtime +"$PGAI_CLEANUP_RETENTION_DAYS" -print0 2>/dev/null)
else
  log "briefs directory not found, skipping: $PGAI_BRIEFS_DIR"
fi

log "briefs archived: $BRIEFS_ARCHIVED"

# ---------------------------------------------------------------------------
# Step 6: Purge framework temp directory
# ---------------------------------------------------------------------------
# Clear kanban-owned contents of the resolved framework temp root
# (PGAI_AGENT_KANBAN_TEMP_DIR or config-driven path via pgai_temp_dir).
# The root directory itself is preserved.  Uses the shared allowlist +
# live-state guard from lib/temp_purge.sh:
#   - Foreign entries (claude-*, codex-*, unknown) are skipped with a notice.
#   - If any wake lock under $TEAM_ROOT/locks/ is held, the purge is aborted
#     (the rest of the full run — logs, archives — has already completed).
#   - worktrees/<task-id> entries whose task status is WORKING are preserved.
# ---------------------------------------------------------------------------
log "--- Step 6: purge framework temp directory ---"

_temp_root_default="$(pgai_temp_dir)"
log "temp_dir: $_temp_root_default"

# Wake lock guard: skip temp purge if agents are active.
if ! temp_purge_check_wake_locks "${TEAM_ROOT}/locks"; then
  log "WARNING: Step 6 temp purge skipped due to held wake lock — no temp content removed"
else
  temp_purge_sweep_dir "$_temp_root_default" "step6" "${PGAI_TASKS_DIR:-}"
fi
unset _temp_root_default

log "temp items purged: $TEMP_ITEMS_PURGED"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
log "--- Summary ---"
log "dry_run:                    $DRY_RUN"
log "retention_days:             $PGAI_CLEANUP_RETENTION_DAYS"
log "task_retention_days:        $PGAI_CLEANUP_TASK_RETENTION_DAYS"
log "trivial_log_bytes:          $PGAI_CLEANUP_TRIVIAL_LOG_BYTES"
log "trivial_log_hours:          $PGAI_CLEANUP_TRIVIAL_LOG_HOURS"
log "trivial_logs_deleted:  $TRIVIAL_LOGS_DELETED"
log "logs_deleted:          $LOGS_DELETED"
log "task_folders_deleted:  $TASKS_DELETED"
log "requirements_archived: $REQUIREMENTS_ARCHIVED"
log "debug_logs_rotated:    $LOGS_ROTATED"
log "briefs_archived:       $BRIEFS_ARCHIVED"
log "temp_items_purged:     $TEMP_ITEMS_PURGED"
log "log written to:        $CLEANUP_LOG"
log "cleanup complete"

exit 0
