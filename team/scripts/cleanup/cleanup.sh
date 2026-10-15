#!/usr/bin/env bash
# cleanup.sh
# Standalone cleanup script for the pgai-agent-kanban system.
# Designed for cron invocation. Pure bash, no LLM involvement.
#
# Actions (full run, default):
#   0. Purge trivial logs (small AND old) from $KANBAN_ROOT/logs/agents/
#   1. Delete log files older than PGAI_CLEANUP_RETENTION_DAYS from PGAI_LOGS_DIR
#   2. Report DONE/WONT-DO task folders older than N days in PGAI_TASKS_DIR.
#      Task folders are never deleted: finished tasks, bugs, priorities and
#      shipped requirements leave the live project only through the archive
#      run (scripts/archive.sh, also run after each release), which moves
#      them to $KANBAN_ROOT/archive/<project>/ with a ledger row and can
#      restore them. Nothing in that archive is deleted by cleanup.
#   3. (none: requirements are archived by scripts/archive.sh, which also
#      folds any projects/<p>/tasks/archive/requirements/ left by older
#      installs into $KANBAN_ROOT/archive/<project>/)
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
#   Purges the kanban-owned (allowlisted) contents of PGAI_AGENT_KANBAN_TEMP_DIR
#   and of every project's temp dir without deleting the directories
#   themselves, so the temp roots stay around for the next run. Entries
#   outside the allowlist (lib/temp_purge.sh) are kept, and a task worktree
#   under a project's worktrees/ is kept while that project's own task is
#   WORKING, BLOCKED or TESTING. The wake locks are probed before the purge
#   and again before each project's sweep; a held lock skips the purge.
#
# Without a project (no --project and PGAI_PROJECT_NAME unset or empty):
#   Runs once per project registered in projects.cfg, in projects.cfg
#   priority order, by re-invoking this script as `cleanup.sh --project <p>`
#   with the same --dry-run / --trivial-only / --temp-only flags. Each
#   per-project run is exactly the single-project run described above, with
#   its own summary log. One result line per project is printed; a failing
#   project does not stop the remaining ones. The exit status is 0 when every
#   per-project run succeeded (or no project is registered) and otherwise the
#   exit status of the first per-project run that failed. This is the mode
#   the bare weekly cron line uses.
#   --trivial-only and --temp-only iterate the same way. Their purges are
#   framework-wide (batch logs under $KANBAN_ROOT/logs/agents/, and the
#   install-wide plus every project's temp dir), so the first project's run
#   does the work and later runs find nothing left to remove.
#
# Usage:
#   cleanup.sh [--project <name>] [--dry-run] [--trivial-only] [--temp-only]
#   cleanup.sh --help
#
# Options:
#   --project <name>  Clean only this project. When omitted (and
#                     PGAI_PROJECT_NAME is unset or empty), run once per
#                     project registered in projects.cfg
#   --dry-run         Preview actions without making any changes
#   --trivial-only    Run only the trivial-log purge (Step 0) and exit
#   --temp-only       Run only the framework temp-dir purge (Step 6) and exit
#   --help            Print this usage message and exit 0. Short-circuits
#                     before any filesystem action — no logs, archives, or
#                     temp purge run.
#
# Configuration (kanban.cfg [cleanup], or the environment, which wins):
#   PGAI_CLEANUP_RETENTION_DAYS        Days to retain logs (default: 30).
#                                      kanban.cfg [cleanup] retention_days; the
#                                      deprecated [paths] cleanup_retention_days
#                                      is used when that key is absent, with one
#                                      warning per run.
#   PGAI_CLEANUP_TASK_RETENTION_DAYS   Age in days past which Step 2 counts a terminal task
#                                      folder in its report (default: 7). No folder is
#                                      deleted at any age. Independent of log retention.
#   PGAI_CLEANUP_TRIVIAL_LOG_BYTES     Byte threshold for "trivial" logs (default: 1700).
#                                      kanban.cfg [cleanup] trivial_log_bytes.
#                                      Logs SMALLER than this value are eligible for
#                                      aggressive purge when also older than the hours
#                                      threshold below.
#   PGAI_CLEANUP_TRIVIAL_LOG_HOURS     Age threshold in hours for trivial purge (default: 6).
#                                      kanban.cfg [cleanup] trivial_log_hours.
#                                      Only logs OLDER than this many hours are purged.
#                                      Two-tier model: trivial (small+old) purged daily;
#                                      substantive logs kept for PGAI_CLEANUP_RETENTION_DAYS.
#   PGAI_LOGS_DIR                      Directory for log files
#   PGAI_TASKS_DIR                Directory for task folders
#   PGAI_BRIEFS_DIR               Directory for PM briefs
#   PGAI_ARCHIVE_DIR              Destination root for archived briefs (Step 5)
#   PGAI_AGENT_KANBAN_ROOT_PATH  Kanban root (default: $HOME/pgai_agent_kanban)
#   KANBAN_ROOT                  Kanban root read directly by lib/project_paths.sh.
#                                 Reconciled against PGAI_AGENT_KANBAN_ROOT_PATH before
#                                 any destructive step runs: whichever of the two is
#                                 unset is derived from the other, and the script
#                                 refuses (exit 1) if both are set and disagree, so a
#                                 single override always sandboxes the whole run.
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
#   1 = error (archive dir not writable, release-state.md unreadable,
#       KANBAN_ROOT and PGAI_AGENT_KANBAN_ROOT_PATH disagree, etc.)
#   Without a project, the exit status of the first failing per-project run.

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
# shellcheck source=lib/config_loader.sh
source "${_SCRIPT_DIR}/../lib/config_loader.sh"
unset _SCRIPT_DIR

# Source user-wide config first (lowest precedence among files)
[[ -f "$HOME/.pgairc" ]] && source "$HOME/.pgairc"

# Source user config file using the standard pgai-kanban.cfg name
[[ -f "$HOME/.config/pgai-kanban.cfg" ]] && source "$HOME/.config/pgai-kanban.cfg"

# Source per-install config — INI format (kanban.cfg) replaces legacy config.cfg
# read_ini available via project_paths.sh (sourced above).
if [[ -f "$TEAM_ROOT/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "$TEAM_ROOT/kanban.cfg" chain pm_mode automatic)}"
fi
# The [cleanup] tunables are resolved after root reconciliation below, so
# they are read from the same root every other step uses.
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
HELP=false
_CLEANUP_PROJECT_ARG=""
_args=("$@")
_ai=0
while [[ $_ai -lt ${#_args[@]} ]]; do
  arg="${_args[$_ai]}"
  case "$arg" in
    --help|-h)
      HELP=true
      ;;
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
        echo "Usage: $(basename "$0") [--project <name>] [--dry-run] [--trivial-only] [--temp-only]" >&2
        exit 1
      fi
      _CLEANUP_PROJECT_ARG="${_args[$_next]}"
      _ai=$(( _ai + 1 ))
      ;;
    *)
      echo "ERROR: unknown argument: $arg" >&2
      echo "Usage: $(basename "$0") [--project <name>] [--dry-run] [--trivial-only] [--temp-only]" >&2
      exit 1
      ;;
  esac
  _ai=$(( _ai + 1 ))
done
unset _args _ai _next

# ---------------------------------------------------------------------------
# --help short-circuit: print usage and exit BEFORE any filesystem action.
# Deliberately placed ahead of project resolution, path/tunable defaults,
# and log-directory creation so --help never deletes, archives, purges, or
# writes a summary log — it only needs to know which flags this script
# accepts, not which project it would otherwise run against.
# ---------------------------------------------------------------------------
if [[ "$HELP" == "true" ]]; then
  cat <<EOF
Usage: $(basename "$0") [--project <name>] [--dry-run] [--trivial-only] [--temp-only]
       $(basename "$0") --help

Options:
  --project <name>  Clean only this project. When omitted (and
                    PGAI_PROJECT_NAME is unset or empty), run once per
                    project registered in projects.cfg -- see below
  --dry-run         Preview actions without making any changes
  --trivial-only    Run only the trivial-log purge (Step 0) and exit
  --temp-only       Run only the framework temp-dir purge (Step 6) and exit
  --help            Print this usage message and exit 0 (no filesystem action)

What a full run does (per project):
  Step 0  delete trivial batch logs (small and old) from logs/agents/
  Step 1  delete log files older than the log retention (default 30 days)
  Step 2  report DONE/WONT-DO task folders older than the task retention
          (default 7 days); task folders are never deleted, at any age
  Step 4  rotate debug logs from a previous UTC day into logs/debug/archive/
  Step 5  move briefs older than the log retention into the briefs archive
  Step 6  purge the framework temp directory
  Finished tasks, bugs, priorities and shipped requirements leave the live
  project only through scripts/archive.sh (also run after each release),
  which moves them to archive/<project>/ and can restore them; cleanup never
  deletes anything there. (There is no Step 3: archive.sh archives shipped
  requirements and folds any older projects/<p>/tasks/archive/requirements/
  into archive/<project>/.)

Without a project:
  The run repeats once per project registered in projects.cfg, in priority
  order, as '$(basename "$0") --project <p>' with the same flags. One result
  line is printed per project, and a failing project does not stop the rest.
  Exit status is 0 when every project succeeded (or none is registered),
  otherwise the exit status of the first project run that failed.
  --trivial-only and --temp-only repeat per project too; their purges are
  framework-wide, so the first project's run does the work and later runs
  find nothing left to remove.
EOF
  exit 0
fi

# ---------------------------------------------------------------------------
# Root reconciliation: TEAM_ROOT (above) governs the Step 0 existence guard
# and the optional kanban.cfg lookup; KANBAN_ROOT (required by
# lib/project_paths.sh's pp_* helpers, sourced above) governs every
# destructive step below (1-6) and the summary log's own write path. The
# two normally carry the same value -- the wake bootstrap sets KANBAN_ROOT
# directly from PGAI_AGENT_KANBAN_ROOT_PATH -- but an invocation that
# repoints only one of them (the Live-Install Safety sandboxing procedure
# in the role files names PGAI_AGENT_KANBAN_ROOT_PATH) leaves the two
# disagreeing, and every destructive step would then act on whichever root
# KANBAN_ROOT still holds rather than the one the caller meant to sandbox.
# Reconcile once, here, before either variable is read again: refuse when
# both are explicitly set and disagree (never guess which root the caller
# meant), and derive whichever is unset from the other so a single
# override sandboxes every step, not just the Step 0 guard.
# ---------------------------------------------------------------------------
if [[ -n "${KANBAN_ROOT:-}" && -n "${PGAI_AGENT_KANBAN_ROOT_PATH:-}" \
      && "$KANBAN_ROOT" != "$PGAI_AGENT_KANBAN_ROOT_PATH" ]]; then
  # No project has been resolved yet at this point in the script (the
  # --project/PGAI_PROJECT_NAME resolution below hasn't run), so this
  # refusal is deliberately recorded with no project scope -- run in a
  # subshell with PGAI_PROJECT_NAME cleared so pgai_emit_error_contract's
  # ambient fallback can't record into whichever project an inherited
  # PGAI_PROJECT_NAME happens to name under the disagreeing (and
  # therefore untrusted) KANBAN_ROOT this refusal exists to leave alone.
  (
    unset PGAI_PROJECT_NAME
    pgai_emit_error_contract \
        "cleanup.root_mismatch" \
        "KANBAN_ROOT ('${KANBAN_ROOT}') and PGAI_AGENT_KANBAN_ROOT_PATH ('${PGAI_AGENT_KANBAN_ROOT_PATH}') disagree; cleanup.sh will not guess which root to operate on." \
        "Export both variables to the same root before invoking cleanup.sh, e.g.: env KANBAN_ROOT=<root> PGAI_AGENT_KANBAN_ROOT_PATH=<root> bash team/scripts/cleanup/cleanup.sh --project <name> ..."
  )
  # CENSUS-ANCHOR: cleanup-root-mismatch-01
  # BLOCK-WORTHY (docs/blocking-site-census.md): a disagreement here means
  # the Step 0 guard and the destructive steps (1-6) would act on two
  # different roots; continuing risks mutating whichever root neither the
  # caller's override nor the inherited environment unambiguously named.
  exit 1
fi

if [[ -z "${KANBAN_ROOT:-}" && -n "${PGAI_AGENT_KANBAN_ROOT_PATH:-}" ]]; then
  # Only the role-file-documented override is set; derive KANBAN_ROOT from
  # it so a single override sandboxes both the Step 0 guard and Steps 1-6
  # (mirrors the fallback pattern in lib/projects.sh's projects_cfg_path).
  export KANBAN_ROOT="$PGAI_AGENT_KANBAN_ROOT_PATH"
elif [[ -n "${KANBAN_ROOT:-}" ]]; then
  # KANBAN_ROOT is the authoritative value here (PGAI_AGENT_KANBAN_ROOT_PATH
  # is either unset or already confirmed to agree with it above); point
  # TEAM_ROOT at the same root so the Step 0 guard and kanban.cfg lookup
  # never drift from the root the destructive steps will actually use.
  TEAM_ROOT="$KANBAN_ROOT"
fi
# When both are unset, KANBAN_ROOT stays unset and TEAM_ROOT keeps its
# $HOME/pgai_agent_kanban default from above; project_paths.sh's pp_*
# helpers already fail closed on a missing KANBAN_ROOT (see
# _pp_kanban_root) instead of silently operating against that default --
# that fail-closed behaviour is preserved unchanged.

# ---------------------------------------------------------------------------
# [cleanup] tunables: retention_days, trivial_log_bytes, trivial_log_hours
# from the reconciled root's kanban.cfg, an environment value winning over
# the cfg (config_cleanup_tunables in lib/config_loader.sh). Resolved and
# exported here, ahead of the project-less fan-out below, so each
# per-project child inherits the values and a deprecated
# [paths] cleanup_retention_days is narrated once per run, not once per
# project.
# ---------------------------------------------------------------------------
config_cleanup_tunables "$TEAM_ROOT/kanban.cfg"

# ---------------------------------------------------------------------------
# Project-less invocation: run once per registered project.
# Resolution order for the target project: --project flag, then
# $PGAI_PROJECT_NAME. When neither names a project -- the bare cron line --
# this script re-invokes itself as `cleanup.sh --project <p>` for every
# project projects_cfg_list returns (projects.cfg priority order), passing
# the same --dry-run / --trivial-only / --temp-only flags, so each
# per-project run is exactly the single-project run below. There is still
# no fallback to just the first registered project.
#
# Each child runs in its own process so one project's failure (set -e,
# a preflight refusal, an export-mismatch refusal) cannot abort the
# remaining projects. The child prints its own error; this loop only
# records which project failed and propagates the first failing child's
# exit status, so it adds no refusal site of its own.
# ---------------------------------------------------------------------------
_CLEANUP_PROJECT="${_CLEANUP_PROJECT_ARG:-${PGAI_PROJECT_NAME:-}}"
if [[ -z "$_CLEANUP_PROJECT" ]]; then
  # Fail closed the same way the --project path does when neither root
  # variable is set: _pp_kanban_root prints the error and returns non-zero,
  # which ends the script under set -e. Without this, projects_cfg_path
  # would read $HOME/pgai_agent_kanban/projects.cfg -- an unnamed root.
  _pp_kanban_root >/dev/null

  _each_flags=()
  [[ "$DRY_RUN" == "true" ]] && _each_flags+=(--dry-run)
  [[ "$TRIVIAL_ONLY" == "true" ]] && _each_flags+=(--trivial-only)
  [[ "$TEMP_ONLY" == "true" ]] && _each_flags+=(--temp-only)
  _each_self="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"

  # projects_cfg_list fails when projects.cfg is missing or registers no
  # project. With nothing registered there is nothing to clean, so that is
  # a logged no-op, not a failure; its own stderr error is replaced by the
  # single line below so the cron log does not read as a failed run.
  _each_list="$(projects_cfg_list 2>/dev/null)" || _each_list=""
  if [[ -z "$_each_list" ]]; then
    echo "[$(date -Iseconds)] cleanup: no --project given and no project registered in $(projects_cfg_path); nothing to clean (register one with scripts/create-project.sh or scripts/add-project.sh)"
    exit 0
  fi

  _each_count="$(grep -c . <<< "$_each_list")"
  echo "[$(date -Iseconds)] cleanup: no --project given; running once per registered project (${_each_count}): $(tr '\n' ' ' <<< "$_each_list" | sed 's/ *$//')"

  _each_rc_overall=0
  _each_ok=0
  _each_failed=0
  while IFS= read -r _each_proj; do
    [[ -n "$_each_proj" ]] || continue
    _each_rc=0
    # Child stdin is detached so a child can never consume the project
    # list this loop is reading.
    "${BASH:-bash}" "$_each_self" --project "$_each_proj" \
        ${_each_flags[@]+"${_each_flags[@]}"} < /dev/null || _each_rc=$?
    if [[ "$_each_rc" -eq 0 ]]; then
      _each_ok=$(( _each_ok + 1 ))
      echo "[$(date -Iseconds)] cleanup: project '${_each_proj}': ok"
    else
      _each_failed=$(( _each_failed + 1 ))
      [[ "$_each_rc_overall" -eq 0 ]] && _each_rc_overall="$_each_rc"
      echo "[$(date -Iseconds)] cleanup: project '${_each_proj}': FAILED (exit ${_each_rc}); see the error above"
    fi
  done <<< "$_each_list"

  echo "[$(date -Iseconds)] cleanup: per-project runs complete: ${_each_ok} ok, ${_each_failed} failed"
  exit "$_each_rc_overall"
fi

# ---------------------------------------------------------------------------
# Paths and tunables. PGAI_CLEANUP_RETENTION_DAYS, _TRIVIAL_LOG_BYTES and
# _TRIVIAL_LOG_HOURS were resolved from kanban.cfg [cleanup] above.
# ---------------------------------------------------------------------------
# Age (default: 7 days) past which Step 2 counts a terminal task folder in
# its report. Task folders are never deleted at any age. Independent of the
# log-retention threshold. Override via PGAI_CLEANUP_TASK_RETENTION_DAYS.
PGAI_CLEANUP_TASK_RETENTION_DAYS="${PGAI_CLEANUP_TASK_RETENTION_DAYS:-7}"
# Trivial-log purge thresholds (two-tier retention model).
# A log is "trivial" if it is SMALLER than PGAI_CLEANUP_TRIVIAL_LOG_BYTES bytes
# AND OLDER than PGAI_CLEANUP_TRIVIAL_LOG_HOURS hours. These logs are purged
# aggressively (Step 0 / --trivial-only) to prevent cron-driven accumulation.
# Substantive logs (>= threshold size) are kept for PGAI_CLEANUP_RETENTION_DAYS.

# --- Resolve paths for the target project ---
# _CLEANUP_PROJECT is non-empty here: the project-less case above re-invoked
# this script once per registered project and exited.
_pp_project_root="$(pp_project_root "$_CLEANUP_PROJECT")"
_pp_tasks="$(pp_tasks_dir "$_CLEANUP_PROJECT")"

# ---------------------------------------------------------------------------
# Pre-resolved export reconciliation: PGAI_PROJECT_ROOT and PGAI_TASKS_DIR
# are fully-resolved live-install paths the wake bootstrap exports ahead of
# invoking any agent, and they outrank anything KANBAN_ROOT /
# PGAI_AGENT_KANBAN_ROOT_PATH derive. The root reconciliation above only
# compares the two root variables, so it leaves a residual seam: an
# invocation that repoints both roots at a throwaway directory but still
# carries one of these two exports from an earlier, live-install
# invocation would still resolve Step 6's WORKING-worktree check (which
# decides what the temp purge may remove) or the release-state lookup
# from the exported path instead of the sandboxed one. Refuse rather than
# silently preferring the export, for the same reason the root mismatch
# above refuses rather than guesses: an operator who set PGAI_TASKS_DIR
# deliberately would be surprised by a silent rewrite.
# ---------------------------------------------------------------------------
# The comparison strips a single trailing slash before comparing so a
# value that names the reconciled directory is accepted however it is
# spelled ('<root>' and '<root>/' are the same directory); it still does
# not implement containment — a genuine sub-path ('<root>/subdir') is
# refused exactly as before. The message says "does not match", not
# "does not sit underneath", because equality (mod trailing slash) is the
# only test performed here.
if [[ -n "${PGAI_PROJECT_ROOT:-}" && "${PGAI_PROJECT_ROOT%/}" != "$_pp_project_root" ]]; then
  pgai_emit_error_contract \
      "cleanup.project_root_export_mismatch" \
      "PGAI_PROJECT_ROOT ('${PGAI_PROJECT_ROOT}') does not match the reconciled root ('${_pp_project_root}'); cleanup.sh will not guess which one the caller meant." \
      "Unset PGAI_PROJECT_ROOT, or point it at '${_pp_project_root}' before invoking cleanup.sh." \
      "$_CLEANUP_PROJECT"
  # CENSUS-ANCHOR: cleanup-project-root-export-mismatch-01
  # BLOCK-WORTHY (docs/blocking-site-census.md): a disagreement here means
  # the release-state lookup and every other pp_project_root()-derived
  # path below could be read from outside the root the caller reconciled
  # above.
  exit 1
fi
[[ -n "${PGAI_PROJECT_ROOT:-}" ]] && PGAI_PROJECT_ROOT="${PGAI_PROJECT_ROOT%/}"

if [[ -n "${PGAI_TASKS_DIR:-}" && "${PGAI_TASKS_DIR%/}" != "$_pp_tasks" ]]; then
  pgai_emit_error_contract \
      "cleanup.tasks_dir_export_mismatch" \
      "PGAI_TASKS_DIR ('${PGAI_TASKS_DIR}') does not match the reconciled root ('${_pp_tasks}'); cleanup.sh will not guess which one the caller meant." \
      "Unset PGAI_TASKS_DIR, or point it at '${_pp_tasks}' before invoking cleanup.sh." \
      "$_CLEANUP_PROJECT"
  # CENSUS-ANCHOR: cleanup-tasks-dir-export-mismatch-01
  # BLOCK-WORTHY (docs/blocking-site-census.md): Step 6's temp purge reads
  # task state from this directory to decide which task worktrees are
  # WORKING and must survive the purge (Step 2 only reads it for its
  # report); a disagreement here means a live agent's worktree could be
  # purged on the strength of a tasks directory the caller did not sandbox.
  exit 1
fi
[[ -n "${PGAI_TASKS_DIR:-}" ]] && PGAI_TASKS_DIR="${PGAI_TASKS_DIR%/}"

# Wake batch logs live at $KANBAN_ROOT/logs/agents/.
PGAI_LOGS_DIR="${PGAI_LOGS_DIR:-${KANBAN_ROOT}/logs/agents}"
PGAI_TASKS_DIR="${PGAI_TASKS_DIR:-${_pp_tasks}}"
PGAI_BRIEFS_DIR="${PGAI_BRIEFS_DIR:-$TEAM_ROOT/briefs}"
PGAI_ARCHIVE_DIR="${PGAI_ARCHIVE_DIR:-${_pp_tasks}/archive}"
unset _pp_project_root _pp_tasks

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
# shellcheck source=../lib/script_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/script_common.sh"
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
TASKS_KEPT=0
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
# A full run requires the project's release-state.md and logs the last
# released version; no step's behaviour depends on that version.
# --trivial-only and --temp-only skip the preflight so those flags are safe
# to run even before the first release ships.
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
    log "release-state.md has no Last Released version"
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
# Only the briefs archive (Step 5) lives here. Requirements and task folders
# are archived by scripts/archive.sh under $KANBAN_ROOT/archive/<project>/,
# so no requirements directory is created under PGAI_ARCHIVE_DIR.
ensure_archive_dirs() {
  local dirs=(
    "$PGAI_ARCHIVE_DIR"
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

# Deletes (or, in dry-run, narrates) trivial batch logs and reports how many
# it found through the TRIVIAL_LOGS_DELETED global rather than stdout, so the
# per-file narration this function logs in the dry-run branch never shares a
# channel with the count -- the same nameref/global shape TEMP_ITEMS_PURGED
# already uses below for the analogous temp-purge count. Sets
# TRIVIAL_LOGS_DELETED as a side effect; callers read it after the call
# returns instead of capturing this function's output.
purge_trivial_logs() {
  local trivial_bytes="$1"
  local trivial_hours="$2"
  local dry_run="$3"
  local trivial_minutes=$(( trivial_hours * 60 ))
  local count=0
  local batch_log_dir="${KANBAN_ROOT}/logs/agents"

  TRIVIAL_LOGS_DELETED=0

  if [[ ! -d "$batch_log_dir" ]]; then
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

  TRIVIAL_LOGS_DELETED="$count"
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
  # then sweep each per-project dir's contents via temp_purge_sweep_dir (which
  # preserves the root), judging its worktrees/ against that project's own
  # tasks dir.
  log "--- temp-only: enumerating per-project temp directories ---"
  declare -A _per_project_dirs  # associative array: path → project label
  declare -A _per_project_tasks # associative array: path → that project's tasks dir
  _project_list="$(projects_cfg_list 2>/dev/null)" || _project_list=""
  if [[ -n "$_project_list" ]]; then
    while IFS= read -r _proj; do
      [[ -n "$_proj" ]] || continue
      if pp_load_config "$_proj" 2>/dev/null; then
        _proj_temp_dir="${PP_TEMP_DIR:-}"
        if [[ -n "$_proj_temp_dir" ]]; then
          _per_project_dirs["$_proj_temp_dir"]="project ${_proj}"
          _per_project_tasks["$_proj_temp_dir"]="$(pp_tasks_dir "$_proj" 2>/dev/null || true)"
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
      # worktrees/ gets the per-task live-state guard.
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
  # Same allowlist and live-task guard as the install-wide sweep: a
  # non-allowlisted entry is foreign and kept, and worktrees/ goes through
  # temp_purge_sweep_worktrees against THIS project's tasks dir, so a
  # WORKING, BLOCKED or TESTING task's worktree survives. The wake locks are
  # probed again right before each project: a wake that started since the
  # check above has its project's sweep skipped rather than run under it.
  log "--- temp-only: purging per-project temp directory contents ---"
  for _ppath in "${!_per_project_dirs[@]}"; do
    _plabel="${_per_project_dirs[$_ppath]}"
    if ! temp_purge_check_wake_locks "${TEAM_ROOT}/locks"; then
      log "WARNING: temp purge of per-project temp dir skipped (${_plabel}) — a wake lock is held; re-run after all agents finish"
      continue
    fi
    log "temp-only: sweeping per-project temp dir (${_plabel}): $_ppath"
    temp_purge_sweep_dir "$_ppath" "$_plabel" "${_per_project_tasks[$_ppath]:-}"
  done
  unset _ppath _plabel _per_project_dirs _per_project_tasks

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

TRIVIAL_LOGS_DELETED=0
if [[ -d "${TEAM_ROOT}/projects" ]]; then
  purge_trivial_logs \
      "$PGAI_CLEANUP_TRIVIAL_LOG_BYTES" \
      "$PGAI_CLEANUP_TRIVIAL_LOG_HOURS" \
      "$DRY_RUN"
else
  log "projects/ directory not found under $TEAM_ROOT, skipping trivial-log purge"
fi

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
# Step 2: Report terminal task folders (DONE or WONT-DO) older than N days
# ---------------------------------------------------------------------------
# Task folders are kanban state nothing reconstructs, so cleanup never
# deletes one, at any age, in a dry run or a real one. Finished tasks leave
# the live project only through the archive run (scripts/archive.sh, also
# run after each release), which moves each to
# $KANBAN_ROOT/archive/<project>/tasks/ with a ledger row and can restore it;
# cleanup deletes nothing under that archive either. This step only counts
# the terminal folders whose status.md is older than
# PGAI_CLEANUP_TASK_RETENTION_DAYS and names the command that previews what
# the archive run would move.
# ---------------------------------------------------------------------------
log "--- Step 2: report terminal task folders in $PGAI_TASKS_DIR (none are deleted) ---"

# _task_state <status_file>
# Print the first non-blank value under the '## State' heading, or nothing
# when the heading is absent or has no value.
_task_state() {
  local status_file="$1" line val_line
  while IFS= read -r line; do
    if [[ "$line" =~ ^##[[:space:]]*State[[:space:]]*$ ]]; then
      while IFS= read -r val_line; do
        val_line="${val_line#"${val_line%%[![:space:]]*}"}"
        val_line="${val_line%"${val_line##*[![:space:]]}"}"
        [[ "$val_line" == "##"* ]] && break
        if [[ -n "$val_line" ]]; then
          printf '%s\n' "$val_line"
          break
        fi
      done
      break
    fi
  done < "$status_file"
}

if [[ -d "$PGAI_TASKS_DIR" ]]; then
  for task_dir in "$PGAI_TASKS_DIR"/*/; do
    [[ -d "$task_dir" ]] || continue
    # queues/ holds the agent backlogs and archive/ the briefs archive;
    # neither is a task folder.
    case "$(basename "$task_dir")" in
      queues|archive) continue ;;
    esac
    status_file="${task_dir}status.md"
    [[ -f "$status_file" ]] || continue
    task_state="$(_task_state "$status_file")"
    [[ "$task_state" == "DONE" || "$task_state" == "WONT-DO" ]] || continue
    find "$status_file" -mtime +"$PGAI_CLEANUP_TASK_RETENTION_DAYS" -print 2>/dev/null | grep -q . || continue
    TASKS_KEPT=$(( TASKS_KEPT + 1 ))
  done
  log "terminal task folders older than ${PGAI_CLEANUP_TASK_RETENTION_DAYS}d: ${TASKS_KEPT}; left in place (cleanup never deletes task folders)"
  log "finished tasks move to ${KANBAN_ROOT}/archive/${_CLEANUP_PROJECT}/tasks/ through the archive run; preview it with: scripts/archive.sh --project ${_CLEANUP_PROJECT} --dry-run"
else
  log "tasks directory not found, skipping: $PGAI_TASKS_DIR"
fi

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
#   - worktrees/<task-id> entries whose task status is WORKING, BLOCKED or
#     TESTING are preserved.
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
log "task_folders_kept:     $TASKS_KEPT (terminal, older than ${PGAI_CLEANUP_TASK_RETENTION_DAYS}d; never deleted)"
log "debug_logs_rotated:    $LOGS_ROTATED"
log "briefs_archived:       $BRIEFS_ARCHIVED"
log "temp_items_purged:     $TEMP_ITEMS_PURGED"
log "log written to:        $CLEANUP_LOG"
log "cleanup complete"

exit 0
