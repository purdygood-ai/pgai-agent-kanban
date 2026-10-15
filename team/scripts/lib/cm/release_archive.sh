#!/usr/bin/env bash
# team/scripts/lib/cm/release_archive.sh
#
# CM release archive stage: the helper behind Step 16c, which moves the
# released project's finished items beyond [archive] archive_count out of
# the live project into $KANBAN_ROOT/archive/<project>/. Called from
# cm_release_finalize (release_finalize.sh), which sources this file itself;
# split into its own file so release_finalize.sh stays under the shared
# per-file size ceiling (docs/coding-standards.md; enforced by
# team/scripts/lint_engine_size_dupe.py).
#
# Quality class: archiving never blocks or delays a release. The step runs
# last, after the tag is cut, the RC branch is deleted and release-state.md
# is rewritten, and whatever the archive does -- moves items, finds nothing
# to move, is skipped because another process holds the project's repo
# lock, or stops at a failing item -- the release finishes with the exit
# status it would have had without this step. A run that did not finish is
# narrated, and the archive runs again after the next release (or on
# demand through scripts/archive.sh).
#
# The repo lock: CM ships from inside its own wake chain, and that chain
# holds $KANBAN_ROOT/locks/repo-wake-<project>.lock for its whole run
# (wake_driver.sh, "Per-project repo flock"), so the lock is always busy
# when this step runs there. The step therefore asks the archive to accept
# the chain's own hold (--accept-chain-lock): when the lock's recorded
# holder pid is the archive process or one of its ancestors, the archive
# works under that hold and leaves the chain's record as it is. A lock
# held by any other process -- another project chain's agent, an operator
# run of scripts/archive.sh, the dashboard -- still skips the archive and
# moves nothing (archive.repo_lock_busy, outcome skipped-lock-busy). A
# release run from a bare shell holds no lock and the archive takes it
# normally.
#
# Functions
# ---------
#   _cm_release_archive_finished_items
#       Step 16c's body. Checks that the release has closed the RC (the
#       local RC branch is gone, and release-state.md reads Active RC
#       "none" and Last Released "$ACTIVE_RC"); when it has not, archives
#       nothing. Otherwise runs the archive for $PROJECT_NAME only, through
#       the archive module's run_archive verb reached through pp_run_ops
#       (the function scripts/archive.sh and POST /operations/archive also
#       call), with the kanban root passed explicitly so the run can only
#       act on $KANBAN_ROOT, and with --accept-chain-lock so a repo lock
#       held by the wake chain this release runs inside does not skip it
#       (see "The repo lock" above). Prefixes every line the archive prints with
#       "[Step 16c] [archive] " and sets _CM_ARCHIVE_OUTCOME to one of
#       archived | skipped-rc-open | skipped-lock-busy | failed-<rc>.
#       Always returns 0.
#
# Globals read
# ------------
#   PROJECT_NAME, REPO_ROOT, RC_BRANCH, ACTIVE_RC, RELEASE_STATE, KANBAN_ROOT
#     (set by cm_release_preflight), and PGAI_TASK_ID when the release runs
#     inside an agent wake (recorded in the repo-lock provenance when the
#     archive takes the lock itself; "none" when unset).
#
# Dependencies
# ------------
#   team/scripts/lib/cm/common.sh -- release_state_section (sourced below).
#   team/scripts/lib/pp_run_ops.sh -- pp_run_ops (sourced by common.sh).

# Guard against double-sourcing.
if declare -F _cm_release_archive_finished_items >/dev/null 2>&1; then
    return 0
fi

# shellcheck source=common.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

# The operator script that runs the same archive on demand, named in the
# narration when a run did not finish. Resolved from this file's own
# location: scripts/lib/cm/ -> scripts/ in the install, team/scripts/lib/cm/
# -> team/scripts/ in a dev tree.
_CM_RELEASE_ARCHIVE_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/archive.sh"

# Helper: Step 16c body -- archive the released project's finished items.
# Usage: _cm_release_archive_finished_items
# Never fails the release; see the file header.
_cm_release_archive_finished_items() {
  local _branch_open=0 _fields="" _active="" _last="" _rc=0
  local _on_demand="${_CM_RELEASE_ARCHIVE_SCRIPT} --project ${PROJECT_NAME}"

  # The RC must be closed before anything is archived: the RC's own tasks
  # only count as finished once the RC branch is gone and release-state.md
  # records the release. Unknown or unreadable state archives nothing.
  if git -C "$REPO_ROOT" rev-parse --verify --quiet "refs/heads/${RC_BRANCH}" >/dev/null 2>&1; then
    _branch_open=1
  fi
  _fields="$(release_state_section "$RELEASE_STATE" "Active RC" "Last Released")" || true
  _active="$(printf '%s\n' "$_fields" | sed -n '1p' | tr -d '[:space:]')"
  _last="$(printf '%s\n' "$_fields" | sed -n '2p' | tr -d '[:space:]')"
  if [[ $_branch_open -eq 1 || "$_active" != "none" || "$_last" != "$ACTIVE_RC" ]]; then
    echo "[Step 16c] WARNING: the RC is not closed (local branch ${RC_BRANCH} $( ((_branch_open)) && echo present || echo absent); release-state.md Active RC '${_active:-unreadable}', Last Released '${_last:-unreadable}', expected 'none' and '${ACTIVE_RC}') -- nothing archived; release continues. Run on demand once the RC is closed: ${_on_demand}" >&2
    _CM_ARCHIVE_OUTCOME="skipped-rc-open"
    return 0
  fi

  (
    set +e
    pp_run_ops pgai_agent_kanban.archive run_archive \
      --kanban-root "$KANBAN_ROOT" \
      --project "$PROJECT_NAME" \
      --task "${PGAI_TASK_ID:-none}" \
      --accept-chain-lock \
      2>&1 | sed 's/^/[Step 16c] [archive] /'
    exit "${PIPESTATUS[0]}"
  ) || _rc=$?

  case "$_rc" in
    0)
      echo "[Step 16c] Archive finished for ${PROJECT_NAME}."
      _CM_ARCHIVE_OUTCOME="archived"
      ;;
    3)
      # A process outside the wake chain this release runs inside holds
      # the project's repo lock (the chain's own hold is accepted, so this
      # is never the release's own chain). The archive moved nothing.
      echo "[Step 16c] WARNING: archive skipped for ${PROJECT_NAME}: the project's repo lock is held by a process outside this release's own wake chain (see the [archive] lines above); nothing moved; release continues. It runs again after the next release, or on demand: ${_on_demand}" >&2
      _CM_ARCHIVE_OUTCOME="skipped-lock-busy"
      ;;
    1)
      echo "[Step 16c] WARNING: archive refused for ${PROJECT_NAME} (exit 1; the reason and its remedy are in the [archive] lines above); nothing moved; release continues. It runs again after the next release, or on demand: ${_on_demand}" >&2
      _CM_ARCHIVE_OUTCOME="failed-1"
      ;;
    *)
      echo "[Step 16c] WARNING: archive exited ${_rc} for ${PROJECT_NAME} (see the [archive] lines above); items moved before the failure are in the ledger; release continues. It runs again after the next release, or on demand: ${_on_demand}" >&2
      _CM_ARCHIVE_OUTCOME="failed-${_rc}"
      ;;
  esac
  return 0
}
