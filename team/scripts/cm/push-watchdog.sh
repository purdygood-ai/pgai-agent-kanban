#!/usr/bin/env bash
# team/scripts/cm/push-watchdog.sh
# Push unpushed main commits and release tags to each registered project's
# git remote: project.cfg [project] git_remote_name, origin when absent or
# blank (pp_git_remote_name). "origin" below means that remote. Also deletes
# from the remote an RC branch whose deletion a release recorded as refused
# in a release-state.md Push Lag Event, once the remote accepts.
#
# Designed to run from cron OUTSIDE the Claude Code tool-use environment,
# so PreToolUse hook interception cannot block git push to main/origin.
#
# Usage:
#   cm/push-watchdog.sh [--dry-run] [--project <name>]
#
# Options:
#   --dry-run          Show what would be pushed or deleted without doing it.
#   --project <name>   Operate on a single named project instead of all.
#   --help, -h         Show this help and exit.
#
# Environment:
#   KANBAN_ROOT        Path to the live kanban install (default: $HOME/pgai_agent_kanban).
#                      Also honoured via PGAI_AGENT_KANBAN_ROOT_PATH.
#
# Behaviour per project:
#   1. Skip projects with a per-project HALT file (KANBAN_ROOT/projects/<name>/HALT).
#   2. Ask the shared push-decision resolver (pp_push_decision,
#      lib/push_decision.sh) before any git call:
#        unresolved -- the project is not registered in projects.cfg, has no
#                      project.cfg (or legacy PROJECT.cfg), or that file names
#                      no dev_tree_path: log one 'skipped-unresolved-project'
#                      line naming the missing piece, run no git command, skip.
#        no-push    -- push_to_remote = false: origin-isolated by design; skip.
#        push       -- push_to_remote true or absent (the default): continue,
#                      using the resolver's dev tree (the project.cfg
#                      dev_tree_path; PGAI_DEV_TREE_PATH is never consulted).
#   3. Skip projects whose dev_tree_path is not a git repository, and
#      projects whose git_remote_name is not a valid remote name (the
#      dual-channel git_remote_name.invalid block goes to stderr, one line
#      to the project log; no git network command runs).
#   4. Check whether local main is ahead of <remote>/<prefix>main.
#   5. Check for this project's local release tags absent from the remote. A
#      release tag is <branch_prefix>vX.Y.Z -- the name pp_prefix_tag gives
#      the tag the release cuts (ai_v1.2.3 with branch_prefix=ai_, v1.2.3
#      with none). Any other local tag, including another lane's bare vX.Y.Z
#      in a shared repository, is never considered.
#   6. Read the RC branches a recorded Push Lag Event names as still on the
#      remote (the "<branch> (still on <remote>; deletion failed)" items under
#      its "Not On Remote:" list, through pgai_agent_kanban.cm.push_lag_events)
#      and keep those the remote still holds (git ls-remote). Only a branch
#      that is exactly <branch_prefix>rc/<Version> for the event's vX.Y.Z
#      Version, recorded against this remote, is ever deleted; every other
#      such item is logged as skipped. No other remote branch is considered.
#   7. Push the missing commits and/or tags. Each missing tag is pushed by
#      its explicit ref (refs/tags/<tag>); the watchdog never pushes every
#      local tag at once. Then delete each RC branch from step 6 with
#      git push <remote> --delete <branch> -- only once main is on the
#      remote (up to date, or pushed this run); otherwise the delete waits
#      for the next tick.
#   8. Log every action to KANBAN_ROOT/projects/<name>/logs/cm-push-watchdog.log.
#
# Idempotent and safe: when there is nothing to push or delete the script
# exits 0 without changing origin. An RC branch already gone from the remote
# is not deleted again, so a Push Lag Event left in release-state.md is
# harmless once it is cleared.  Network / credential failures are logged and
# treated as non-fatal (script exits 0 to avoid noisy cron mail on transient
# errors; individual project failures are recorded in the per-project log).
#
# Exit code: always 0 (idempotent safety net design — cron should not
# produce mail just because a network push failed on one project; the log
# captures the failure for operator review).

set -euo pipefail
# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/env_bootstrap.sh"

# ---------------------------------------------------------------------------
# Resolve KANBAN_ROOT
# ---------------------------------------------------------------------------
KANBAN_ROOT="${KANBAN_ROOT:-$PGAI_AGENT_KANBAN_ROOT_PATH}"

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
Usage: scripts/cm/push-watchdog.sh [--dry-run] [--project <name>]

Pushes unpushed main commits and release tags to each registered project's
git remote (project.cfg [project] git_remote_name, origin when absent or
blank). Safe to run on every cron tick — no-op when there is nothing to push.

Also retries a refused RC-branch delete: when a release-state.md Push Lag
Event lists "<branch> (still on <remote>; deletion failed)" and the remote
still holds that branch, it runs git push <remote> --delete <branch> once
main is on the remote. Only <branch_prefix>rc/<Version> for the event's own
vX.Y.Z Version and this remote is deleted; any other listed branch is
logged as skipped, and no other remote branch is ever deleted.

A release tag is the project's <branch_prefix>vX.Y.Z (e.g. ai_v1.2.3 with
branch_prefix = ai_, v1.2.3 with none). Only a release tag missing from the
remote is pushed, each by its explicit refs/tags/<tag> ref; no other local
tag is ever pushed.

A project is pushed only when it is registered in projects.cfg, its own
project.cfg exists and names a dev_tree_path, and push_to_remote is true or
absent there. Otherwise it is skipped before any git command runs: one
'skipped-unresolved-project' log line names the missing piece, or a
'[push_to_remote=false]' line for a local-only project.

Options:
  --dry-run          Report what would be pushed or deleted without doing it.
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
# This file lives under $KANBAN_ROOT/scripts/cm/ at runtime (install.sh strips
# the team/ prefix); its libraries are in the sibling scripts/lib/. The
# library helpers handle both projects.cfg formats:
#   - INI ([project:NAME] sections)
#   - colon-legacy (NAME:PRIORITY[:COLOR])
# Without these helpers, an ad-hoc 'awk -F:' parser silently extracted the
# literal string '[project' from INI headers and created a ghost
# projects/[project/ directory on every cron tick.
_LIB_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib" && pwd)"
if [[ -f "${_LIB_DIR}/projects.sh" ]]; then
    # shellcheck source=lib/projects.sh
    source "${_LIB_DIR}/projects.sh"
else
    echo "cm-push-watchdog: ERROR: projects.sh library not found at ${_LIB_DIR}/projects.sh" >&2
    exit 1
fi
# project_paths.sh provides pp_prefix_branch
if [[ -f "${_LIB_DIR}/project_paths.sh" ]]; then
    # shellcheck source=lib/project_paths.sh
    source "${_LIB_DIR}/project_paths.sh"
else
    echo "cm-push-watchdog: ERROR: project_paths.sh library not found at ${_LIB_DIR}/project_paths.sh" >&2
    exit 1
fi
# release_refs.sh provides the release-tag filter and the prefixed main
# branch, shared with OVERWATCH's check-push-lag.sh
if [[ -f "${_LIB_DIR}/release_refs.sh" ]]; then
    # shellcheck source=../lib/release_refs.sh
    source "${_LIB_DIR}/release_refs.sh"
else
    echo "cm-push-watchdog: ERROR: release_refs.sh library not found at ${_LIB_DIR}/release_refs.sh" >&2
    exit 1
fi
# push_decision.sh provides pp_push_decision, the shared push-decision resolver
if [[ -f "${_LIB_DIR}/push_decision.sh" ]]; then
    # shellcheck source=../lib/push_decision.sh
    source "${_LIB_DIR}/push_decision.sh"
else
    echo "cm-push-watchdog: ERROR: push_decision.sh library not found at ${_LIB_DIR}/push_decision.sh" >&2
    exit 1
fi
# pp_run_ops, which runs the Push Lag Event reader
# (pgai_agent_kanban.cm.push_lag_events), comes in with push_decision.sh,
# which sources its sibling pp_run_ops.sh.

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
# Validate a project name: it satisfies the project-name rule and names a
# real directory under projects/.
# Returns 0 if valid, 1 otherwise. Logs a warning on rejection.
# ---------------------------------------------------------------------------
_validate_project_name() {
  local name="$1"
  if [[ -z "$name" ]]; then
    return 1
  fi
  # Reject any name the project-name rule (PROJECTS_CFG_NAME_RE,
  # lib/projects_cfg.sh, sourced with projects.sh) refuses: the registry
  # readers list no such name, and it becomes part of a path below.
  if [[ -z "${PROJECTS_CFG_NAME_RE:-}" || ! "$name" =~ $PROJECTS_CFG_NAME_RE ]]; then
    _log_global "Rejecting suspicious project name: '$name' (does not match the project-name rule ${PROJECTS_CFG_NAME_RE})"
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

  # --- Push decision (shared resolver) ---
  # Decided before any git command, so an unresolved or origin-isolated
  # project contacts no remote. There is no fallback to pushing: a resolver
  # that cannot answer reads as unresolved.
  # The project's git remote (origin when git_remote_name is absent or
  # blank; empty when it is invalid).
  local remote
  remote="$(pp_git_remote_name --quiet "$project_name")" || remote=""
  pp_push_decision "$project_name"
  case "$PP_PUSH_DECISION" in
    push) ;;
    no-push)
      # Projects with push_to_remote=false are origin-isolated by design; the
      # watchdog MUST NOT push them out-of-band.  Log so the operator can grep
      # for the local-only signal.
      _log "[push_to_remote=false] skipping ${remote:-the configured remote} push for ${project_name}: push_to_remote=false — project is local-only by design"
      return 0
      ;;
    *)
      _log "$PP_PUSH_STATUS_LINE"
      return 0
      ;;
  esac

  # The dev tree is the project.cfg dev_tree_path the resolver read.
  local dev_tree="$PP_PUSH_DEV_TREE"

  # --- Resolve the release-tag prefix and MAIN_BRANCH (release_refs.sh) ---
  # This project's release tags are <branch_prefix>vX.Y.Z and its main
  # branch <branch_prefix>main (ai_main with branch_prefix=ai_, main with
  # none). A prefix that cannot be read leaves the tag check off for this
  # tick rather than guessing: an empty-prefix guess would push another
  # lane's bare tags. MAIN_BRANCH then falls back to "main", as before.
  local tag_prefix tag_prefix_ok=true MAIN_BRANCH
  if ! tag_prefix="$(release_refs_tag_prefix "$project_name")"; then
    tag_prefix_ok=false
    tag_prefix=""
  fi
  MAIN_BRANCH="$(release_refs_main_branch "$tag_prefix")"

  # --- Verify git repo ---
  if ! git -C "$dev_tree" rev-parse --git-dir &>/dev/null 2>&1; then
    _log "dev_tree_path '$dev_tree' is not a git repository — skipping"
    return 0
  fi

  # --- A remote name git can be handed safely ---
  # An invalid git_remote_name skips this project this tick; the resolver
  # prints its dual-channel block (git_remote_name.invalid) on stderr.
  if [[ -z "$remote" ]]; then
    pp_git_remote_name "$project_name" >/dev/null || true
    _log "git_remote_name for ${project_name} is not a valid git remote name — skipping (fix [project] git_remote_name in its project.cfg)"
    return 0
  fi

  # --- Fetch from the remote to get its current state ---
  # Best-effort: network or credential failure just means we can't compare accurately.
  local fetch_ok=true
  if ! git -C "$dev_tree" fetch "$remote" --tags --quiet 2>/dev/null; then
    _log "WARNING: fetch from $remote failed — push detection may use stale remote state"
    fetch_ok=false
  fi

  # --- Check: is local MAIN_BRANCH ahead of <remote>/MAIN_BRANCH? ---
  local push_main=false
  # Verify both refs exist before comparing
  if git -C "$dev_tree" rev-parse --verify "$MAIN_BRANCH" &>/dev/null 2>&1 && \
     git -C "$dev_tree" rev-parse --verify "${remote}/$MAIN_BRANCH" &>/dev/null 2>&1; then
    local commits_ahead
    commits_ahead="$(git -C "$dev_tree" rev-list --count "${remote}/${MAIN_BRANCH}..${MAIN_BRANCH}" 2>/dev/null || echo 0)"
    if [[ "$commits_ahead" -gt 0 ]]; then
      _log "$MAIN_BRANCH is $commits_ahead commit(s) ahead of ${remote}/$MAIN_BRANCH — push needed"
      push_main=true
    else
      _log "$MAIN_BRANCH is up to date with ${remote}/$MAIN_BRANCH"
    fi
  elif git -C "$dev_tree" rev-parse --verify "$MAIN_BRANCH" &>/dev/null 2>&1; then
    # Local MAIN_BRANCH exists but <remote>/MAIN_BRANCH does not — definitely needs push
    _log "${remote}/$MAIN_BRANCH does not exist — local $MAIN_BRANCH needs initial push"
    push_main=true
  else
    _log "local $MAIN_BRANCH branch does not exist — nothing to push for main"
  fi

  # --- Check: this project's release tags absent from the remote ---
  # Only <tag_prefix>vX.Y.Z counts; compare each to the tags on the remote.
  local missing_tags=()
  local tag release_tags=""
  if [[ "$tag_prefix_ok" == "true" ]]; then
    release_tags="$(release_refs_local_release_tags "$dev_tree" "$tag_prefix")"
  else
    _log "WARNING: branch_prefix for ${project_name} could not be read — release tags not checked this tick (fix [project] branch_prefix in its project.cfg)"
  fi
  if [[ -n "$release_tags" ]]; then
    while IFS= read -r tag; do
      [[ -z "$tag" ]] && continue
      # A tag is "on the remote" when the remote advertises refs/tags/<tag>
      if ! git -C "$dev_tree" ls-remote --tags "$remote" "refs/tags/${tag}" 2>/dev/null | grep -q .; then
        missing_tags+=("$tag")
      fi
    done <<< "$release_tags"
  fi

  if [[ ${#missing_tags[@]} -gt 0 ]]; then
    _log "Tags missing from ${remote}: ${missing_tags[*]}"
  else
    _log "All local release tags are present on ${remote}"
  fi

  # The explicit refs a tag push names — one per missing release tag.
  local tag_refs=()
  for tag in "${missing_tags[@]}"; do
    tag_refs+=("refs/tags/${tag}")
  done

  # --- RC branches a Push Lag Event recorded as still on the remote ---
  # The reader decides which items name this project's own RC branch for
  # the event's version and this remote ("delete") and which do not
  # ("skip", never deleted). A branch the remote no longer holds needs
  # nothing; one the remote cannot be asked about waits for the next tick.
  local rc_deletes=()
  if [[ "$tag_prefix_ok" == "true" ]]; then
    local release_state lag_out lag_rc=0 action lag_branch lag_reason heads_out
    release_state="$(pp_release_state "$project_name" 2>/dev/null)" || release_state=""
    if [[ -n "$release_state" && -f "$release_state" ]]; then
      lag_out="$(pp_run_ops pgai_agent_kanban.cm.push_lag_events rc-branch-deletes "$release_state" \
        --remote "$remote" --branch-prefix "$tag_prefix" 2>&1)" || lag_rc=$?
      if [[ $lag_rc -ne 0 ]]; then
        _log "WARNING: could not read Push Lag Events from ${release_state} (exit ${lag_rc}) — RC-branch deletes not checked this tick: ${lag_out}"
      else
        while IFS=$'\t' read -r action lag_branch lag_reason; do
          case "$action" in
            delete)
              if ! heads_out="$(git -C "$dev_tree" ls-remote --heads "$remote" "refs/heads/${lag_branch}" 2>&1)"; then
                _log "WARNING: could not list ${lag_branch} on ${remote} — its delete waits for the next tick: ${heads_out}"
              elif [[ -n "$heads_out" ]]; then
                _log "${lag_branch} is still on ${remote} (a Push Lag Event recorded its deletion as refused) — delete needed"
                rc_deletes+=("$lag_branch")
              else
                _log "${lag_branch} named by a Push Lag Event is already gone from ${remote}"
              fi
              ;;
            skip)
              _log "Push Lag Event item '${lag_branch}' is not deleted: ${lag_reason}"
              ;;
          esac
        done <<< "$lag_out"
      fi
    fi
  fi

  # --- Nothing to do? ---
  if [[ "$push_main" == "false" && ${#missing_tags[@]} -eq 0 && ${#rc_deletes[@]} -eq 0 ]]; then
    _log "Nothing to push — idempotent exit"
    return 0
  fi

  # --- Dry-run mode ---
  if [[ "$DRY_RUN" == "true" ]]; then
    if [[ "$push_main" == "true" ]]; then
      _log "[dry-run] would: git -C '$dev_tree' push ${remote} $MAIN_BRANCH"
    fi
    if [[ ${#missing_tags[@]} -gt 0 ]]; then
      _log "[dry-run] would: git -C '$dev_tree' push ${remote} ${tag_refs[*]}"
    fi
    if [[ ${#rc_deletes[@]} -gt 0 ]]; then
      _log "[dry-run] would: git -C '$dev_tree' push ${remote} --delete ${rc_deletes[*]}"
    fi
    return 0
  fi

  # --- Push MAIN_BRANCH ---
  local push_main_rc=0
  if [[ "$push_main" == "true" ]]; then
    _log "Pushing $MAIN_BRANCH to ${remote}..."
    if git -C "$dev_tree" push "$remote" "$MAIN_BRANCH" 2>&1 | \
       while IFS= read -r line; do _log "  git: $line"; done; then
      _log "$MAIN_BRANCH pushed successfully"
    else
      push_main_rc=$?
      _log "WARNING: push ${remote} $MAIN_BRANCH failed (exit code $push_main_rc) — will retry on next tick"
    fi
  fi

  # --- Push missing release tags, each by its explicit ref ---
  local push_tags_rc=0
  if [[ ${#missing_tags[@]} -gt 0 ]]; then
    _log "Pushing tags to ${remote}: ${tag_refs[*]}"
    if git -C "$dev_tree" push "$remote" "${tag_refs[@]}" 2>&1 | \
       while IFS= read -r line; do _log "  git: $line"; done; then
      _log "Tags pushed successfully"
    else
      push_tags_rc=$?
      _log "WARNING: push ${remote} ${tag_refs[*]} failed (exit code $push_tags_rc) — will retry on next tick"
    fi
  fi

  # --- Delete the RC branches a Push Lag Event named, once main is there ---
  # The RC branch's work reaches the remote through main, so the delete
  # runs only when main is up to date there or was pushed above.
  local delete_rc=0
  if [[ ${#rc_deletes[@]} -gt 0 ]]; then
    if [[ $push_main_rc -ne 0 ]]; then
      _log "Not deleting ${rc_deletes[*]} from ${remote} while $MAIN_BRANCH is not on it — will retry on next tick"
    else
      _log "Deleting from ${remote}: ${rc_deletes[*]}"
      if git -C "$dev_tree" push "$remote" --delete "${rc_deletes[@]}" 2>&1 | \
         while IFS= read -r line; do _log "  git: $line"; done; then
        _log "RC branch(es) deleted from ${remote}: ${rc_deletes[*]}"
      else
        delete_rc=$?
        _log "WARNING: push ${remote} --delete ${rc_deletes[*]} failed (exit code $delete_rc) — will retry on next tick"
      fi
    fi
  fi

  # Log overall outcome for this project
  if [[ $push_main_rc -eq 0 && $push_tags_rc -eq 0 && $delete_rc -eq 0 ]]; then
    _log "Push complete for $project_name"
  else
    _log "One or more pushes failed for $project_name (main_rc=$push_main_rc tags_rc=$push_tags_rc delete_rc=$delete_rc)"
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
