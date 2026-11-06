#!/usr/bin/env bash
# team/scripts/lib/cm/measured_pace_refresh.sh
#
# CM open-rc stage: regenerate the measured-pace blocks in a project's
# README.md and HOW_TO.md on a freshly cut RC branch and commit them there,
# so every RC starts with blocks that name the newest shipped rollup.
# Sourced by team/scripts/cm/open-rc.sh, which calls it right after it
# creates the RC branch and before it pushes that branch.
#
# Why at RC open: the per-RC rollup a block is rendered from
# (projects/<name>/metrics/rc/<version>.json) is written by release.sh
# Step 15a, after the tag is cut and main is final. Opening the next RC is
# the first point where that rollup exists and a commit can land without
# moving main past its tag; the RC branch carries the commit forward and the
# release squash takes it to main with the rest of the RC.
#
# Public entry point
# -------------------
#   cm_refresh_measured_pace_docs <repo_root> <project_name> <kanban_root> [generator]
#       Runs the measured-pace generator against <repo_root>/README.md and
#       <repo_root>/HOW_TO.md, reading the rollups of <project_name> under
#       <kanban_root>, and commits the two files on the branch checked out in
#       <repo_root> when the render changed them. [generator] defaults to
#       generate_measured_pace_docs.py in this tree's own scripts directory
#       (the live-install copy when sourced from the live install).
#
#       Quality class: always returns 0 and never stops the caller. Each of
#       these narrates one line and changes no file:
#         - README.md or HOW_TO.md is absent or carries no measured-pace
#           marker (every project but the ones that render the block);
#         - README.md or HOW_TO.md is untracked or has uncommitted changes
#           (an operator's edit is never overwritten or swept into a commit);
#         - the generator is missing, exits non-zero, or the commit fails
#           (both files are restored to HEAD, and the line names the command
#           that re-renders by hand). A missing generator, a generator that
#           exits non-zero, or a commit that fails also leaves one operator
#           inbox entry naming the two files, the cause (a missing
#           generator's path included) and that command
#           (operator_inbox_notice, --once: a re-run meeting the same
#           failure adds no second entry).
#       A render that changes nothing narrates "already current" and commits
#       nothing.
#
# Dependencies: git and python3 on PATH; operator_inbox_notice.sh, sourced
# from beside this lib directory. Safe under the caller's
# `set -euo pipefail`; every failing command is caught here.

# Guard against double-sourcing.
if declare -F cm_refresh_measured_pace_docs >/dev/null 2>&1; then
  return 0
fi

# The generated block's opening marker; a doc without it is not a target.
_CM_MEASURED_PACE_BEGIN_MARKER='<!-- MEASURED-PACE-GENERATED:BEGIN -->'

# This file's directory, as an absolute path resolved once at load: no
# function depends on the caller's current directory or on whether this
# file was named by a relative path.
_CM_MEASURED_PACE_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || _CM_MEASURED_PACE_LIB_DIR=""

# shellcheck source=../operator_inbox_notice.sh
source "$(dirname "${BASH_SOURCE[0]}")/../operator_inbox_notice.sh"

# Restore README.md and HOW_TO.md in <repo_root> to HEAD. Best-effort.
_cm_measured_pace_restore() {
  git -C "$1" checkout -q -- README.md HOW_TO.md 2>/dev/null || true
}

# One operator inbox entry for a refresh that failed after the RC branch
# was cut. Usage:
#   _cm_measured_pace_inbox_notice <repo_root> <project_name> <kanban_root> <cause> <rerun>
# <cause> says what failed; <rerun> is the shell-quoted re-render command.
# The entry's source is rc:<version> when the checked-out branch is an RC
# branch, else this step's caller. Narrates the entry ID on stdout, or why
# none was written on stderr; always returns 0.
_cm_measured_pace_inbox_notice() {
  local repo_root="$1" project_name="$2" kanban_root="$3" cause="$4" rerun="$5"
  local tag="[measured-pace]" branch source entry
  branch="$(git -C "$repo_root" branch --show-current 2>/dev/null)" || branch=""
  source="scripts/cm/open-rc.sh"
  [[ "$branch" == *rc/v* ]] && source="rc:${branch##*rc/}"
  local commit_cmd
  commit_cmd="git -C $(printf '%q' "$repo_root") commit -m $(printf '%q' "Regenerate the measured-pace blocks from the shipped rollups") -- README.md HOW_TO.md"
  if entry="$(operator_inbox_notice "$kanban_root" "$project_name" "$source" \
      "The measured-pace blocks in ${repo_root}/README.md and ${repo_root}/HOW_TO.md were not regenerated on ${branch:-the RC branch}: ${cause}. Re-render them by hand and commit them?" \
      "Both files were left at HEAD and the RC opened without the refresh, so its blocks still name the previous rollup." \
      "Run \`${rerun}\` from any directory, then \`${commit_cmd}\`. List this entry with \`$(printf '%q' "${kanban_root}/scripts/inbox.sh") --project $(printf '%q' "$project_name") list\` and close it with the same script's close command.")"; then
    echo "${tag} Operator inbox: ${entry} records the refresh that failed."
  else
    echo "${tag} WARNING: ${entry}" >&2
  fi
  return 0
}

cm_refresh_measured_pace_docs() {
  local repo_root="$1"
  local project_name="$2"
  local kanban_root="$3"
  local generator="${4:-}"
  local tag="[measured-pace]"

  if [[ -z "$generator" ]]; then
    generator="$(cd "${_CM_MEASURED_PACE_LIB_DIR}/../.." && pwd)/generate_measured_pace_docs.py"
  fi

  local readme="${repo_root}/README.md"
  local how_to="${repo_root}/HOW_TO.md"
  local doc
  for doc in "$readme" "$how_to"; do
    if [[ ! -f "$doc" ]] || ! grep -qF "$_CM_MEASURED_PACE_BEGIN_MARKER" "$doc" 2>/dev/null; then
      echo "${tag} skipped: ${doc} carries no measured-pace block; nothing to regenerate."
      return 0
    fi
  done

  local dirty="" status_rc=0
  dirty="$(git -C "$repo_root" status --porcelain -- README.md HOW_TO.md 2>/dev/null)" || status_rc=$?
  if [[ $status_rc -ne 0 ]]; then
    echo "${tag} skipped: ${repo_root} is not a readable git work tree; README.md and HOW_TO.md left as they are."
    return 0
  fi
  if [[ -n "$dirty" ]]; then
    echo "${tag} skipped: README.md or HOW_TO.md in ${repo_root} is untracked or has uncommitted changes; left untouched."
    return 0
  fi

  local rerun="python3 ${generator} --project ${project_name} --kanban-root ${kanban_root} --readme ${readme} --how-to ${how_to}"
  local rerun_quoted
  rerun_quoted="python3 $(printf '%q' "$generator") --project $(printf '%q' "$project_name") --kanban-root $(printf '%q' "$kanban_root") --readme $(printf '%q' "$readme") --how-to $(printf '%q' "$how_to")"
  if [[ ! -f "$generator" ]]; then
    echo "${tag} WARNING: generator not found at ${generator}; blocks not regenerated. The RC continues." >&2
    _cm_measured_pace_inbox_notice "$repo_root" "$project_name" "$kanban_root" \
      "the generator ${generator} is missing (reinstall the kanban so that file is present)" "$rerun_quoted"
    return 0
  fi

  local gen_rc=0
  (
    set +e
    python3 "$generator" \
      --project "$project_name" \
      --kanban-root "$kanban_root" \
      --readme "$readme" \
      --how-to "$how_to" \
      2>&1 | sed "s/^/${tag} /"
    exit "${PIPESTATUS[0]}"
  ) || gen_rc=$?
  if [[ $gen_rc -ne 0 ]]; then
    _cm_measured_pace_restore "$repo_root"
    _cm_measured_pace_inbox_notice "$repo_root" "$project_name" "$kanban_root" \
      "the generator exited ${gen_rc}" "$rerun_quoted"
    echo "${tag} WARNING: generator exited ${gen_rc}; README.md and HOW_TO.md left at HEAD. The RC continues. Re-render by hand: ${rerun}" >&2
    return 0
  fi

  if git -C "$repo_root" diff --quiet -- README.md HOW_TO.md 2>/dev/null; then
    echo "${tag} README.md and HOW_TO.md blocks already current; nothing to commit."
    return 0
  fi

  local commit_rc=0 commit_out=""
  commit_out="$(git -C "$repo_root" commit -q \
    -m "Regenerate the measured-pace blocks from the shipped rollups" \
    -- README.md HOW_TO.md 2>&1)" || commit_rc=$?
  if [[ -n "$commit_out" ]]; then
    printf '%s\n' "$commit_out" | sed "s/^/${tag} /"
  fi
  if [[ $commit_rc -ne 0 ]]; then
    _cm_measured_pace_restore "$repo_root"
    _cm_measured_pace_inbox_notice "$repo_root" "$project_name" "$kanban_root" \
      "committing the regenerated blocks failed (git exited ${commit_rc})" "$rerun_quoted"
    echo "${tag} WARNING: committing the regenerated blocks failed; README.md and HOW_TO.md left at HEAD. The RC continues. Re-render by hand: ${rerun}" >&2
    return 0
  fi

  local branch sha
  branch="$(git -C "$repo_root" branch --show-current 2>/dev/null)" || branch="HEAD"
  sha="$(git -C "$repo_root" rev-parse --short HEAD 2>/dev/null)" || sha="?"
  echo "${tag} regenerated README.md and HOW_TO.md blocks; committed ${sha} on ${branch}."
  return 0
}
