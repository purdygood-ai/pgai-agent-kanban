#!/usr/bin/env bash
# team/scripts/lib/cm/release_origin_summary.sh
#
# CM release closing-summary formatter: the lines of the "Local release
# preparation complete" summary that describe the tag, the RC branch and the
# best-effort auto-push. A pure formatter: it reads variables the finalize
# stage has already set and prints; it runs no git command and changes no
# state. Sourced by team/scripts/cm/release.sh beside the other
# lib/cm/release_*.sh stages; called by cm_release_finalize
# (release_finalize.sh) when it prints the closing summary.
#
# Public entry point
# -------------------
#   _cm_release_print_origin_summary_lines tag-and-branch|auto-push REMOTE
#       (see the function's own comment below)
#
# Dependencies
# ------------
#   Only its REMOTE argument (the remote cm_release_finalize resolved
#   through pp_git_remote_name) and the variables it reads: RELEASE_TAG,
#   RC_BRANCH, MAIN_BRANCH, _CM_PUSH_TO_REMOTE (release_preflight.sh) and
#   the four _CM_*_OUTCOME values cm_release_finalize initialises and its
#   Steps 10, 11 and 14 record. It needs to exist by call time only.

# Guard against double-sourcing.
if declare -F _cm_release_print_origin_summary_lines >/dev/null 2>&1; then
    return 0
fi

# Helper: print the closing-summary lines that describe remote and branch
# actions, built from the outcomes Steps 10, 11 and 14 recorded rather than
# from what those steps would do on a fully remote-backed run.
# Usage: _cm_release_print_origin_summary_lines tag-and-branch|auto-push REMOTE
#
# tag-and-branch : the tag line and the RC-branch deletion line
# auto-push      : the Step 14 main/tags push line
# REMOTE         : the remote those steps pushed to and deleted from, named
#                  in every line that mentions one (the finalizer passes the
#                  name it resolved through pp_git_remote_name)
#
# Reads RELEASE_TAG, RC_BRANCH, MAIN_BRANCH, _CM_PUSH_TO_REMOTE and the four
# _CM_*_OUTCOME values cm_release_finalize initialises. In local-only mode
# (push_to_remote=false) no line claims a push to, or deletion from, the remote.
_cm_release_print_origin_summary_lines() {
  local _which="$1"
  local _remote="${2:?_cm_release_print_origin_summary_lines: pass the remote name (pp_git_remote_name) as the second argument}"
  local _local_only=0
  [[ "${_CM_PUSH_TO_REMOTE:-}" != "true" ]] && _local_only=1

  case "$_which" in
    tag-and-branch)
      # The tag reaches the remote only through Step 14's tags push.
      if [[ $_local_only -eq 1 ]]; then
        echo "  - Tag $RELEASE_TAG created locally only (push_to_remote=false; not pushed)"
      else
        case "${_CM_PUSH_TAGS_OUTCOME:-not-run}" in
          PUSHED)     echo "  - Tag $RELEASE_TAG created locally and pushed to ${_remote}" ;;
          UP-TO-DATE) echo "  - Tag $RELEASE_TAG created locally; ${_remote} tags were already up to date" ;;
          FAILED)     echo "  - Tag $RELEASE_TAG created locally; push to ${_remote} FAILED (see [cm-release auto-push] lines above)" ;;
          *)          echo "  - Tag $RELEASE_TAG created locally; tags push did not run" ;;
        esac
      fi

      local _local_part
      case "${_CM_RC_LOCAL_DELETE_OUTCOME:-not-run}" in
        deleted)        _local_part="deleted locally" ;;
        already-absent) _local_part="already absent locally" ;;
        *)              _local_part="local deletion did not run" ;;
      esac
      case "${_CM_RC_ORIGIN_DELETE_OUTCOME:-not-run}" in
        deleted)
          echo "  - $RC_BRANCH deleted from ${_remote}; ${_local_part}" ;;
        already-absent)
          echo "  - $RC_BRANCH already absent on ${_remote}; ${_local_part}" ;;
        skipped)
          echo "  - $RC_BRANCH ${_local_part}; ${_remote} not touched (local only, push_to_remote=false)" ;;
        *)
          echo "  - $RC_BRANCH ${_remote} deletion did not run; ${_local_part}" ;;
      esac
      ;;
    auto-push)
      if [[ $_local_only -eq 1 ]]; then
        echo "  - auto-push of $MAIN_BRANCH and tags skipped (Step 14; push_to_remote=false — release is local only)"
      else
        echo "  - auto-push to ${_remote} (Step 14, best-effort): $MAIN_BRANCH ${_CM_PUSH_MAIN_OUTCOME:-not-run}, tags ${_CM_PUSH_TAGS_OUTCOME:-not-run} — see [cm-release auto-push] lines above"
      fi
      ;;
  esac
}
