#!/usr/bin/env bash
# team/scripts/lib/cm/release_halt_after_spend.sh
#
# Ship-time HALT-AFTER spend step: the moment a project's Active RC is
# rewritten to "none" (team/scripts/lib/cm/release_finalize.sh, Step 12) is
# an RC close, and every HALT-AFTER token converts (promotes HALT-AFTER ->
# HALT) only at RC close -- the RC-close gate
# (team/pgai_agent_kanban/halt_after/rc_gate.py). The wake loop would also
# observe this on its next firing and convert it there -- except for the
# version-pinned rc:vX.Y.Z token, which needs this synchronous spend
# specifically: a follow-on RC can open (Active RC set to a new version)
# before the wake loop's next firing, and once it does, the wake loop's own
# RC-close window has already closed even though the shipped version
# already satisfied the target ("Back-to-back RCs do not defer the drain"
# -- team/SOP.md, HALT-AFTER family). Checking both scopes here with the
# identical evaluator the wake loop uses means every token (not just
# rc:vX.Y.Z) converts immediately when THIS RC's close is what satisfies
# it, and nothing is duplicated -- promote() is idempotent-safe to call
# exactly once per satisfied drain, which is what rc_gate.py's latch
# guarantees.
#
# Public entry point
# -------------------
#   cm_release_spend_halt_after_sentinel <project_root> <kanban_root>
#
#     Called from release_finalize.sh's Step 12c, immediately after Step 12
#     has rewritten release-state.md (Active RC=none, Last Released=the
#     just-shipped version). Resolves the HALT-AFTER scope root exactly as
#     the wake loop does -- project scope
#     (<project_root>/HALT-AFTER) takes precedence over root scope
#     (<kanban_root>/HALT-AFTER) -- and, only when a sentinel is present at
#     the winning scope, invokes the same `python3 -m halt_after` evaluator
#     the wake loop calls (team/scripts/lib/wake_project.sh). When that
#     invocation promotes (exit 0), narrates the spend on stdout using
#     token.describe_token so the release output names the target the same
#     way the status bar and arm confirmation do. Never fails the release:
#     every path through this function returns 0, because by this point in
#     cm_release_finalize the tag is already cut and release-state.md
#     already rewritten -- irreversible steps a HALT-AFTER bookkeeping
#     concern must never roll back.
#
# Dependencies
# ------------
#   team/scripts/lib/halt_after_invoke.sh — pgai_halt_after_resolve_scope_root,
#   pgai_halt_after_run (sourced by team/scripts/cm/release.sh before this
#   file; both are plain bash functions, safe to call directly here).
#   team/scripts/lib/pp_run_ops.sh — pp_run_ops, which runs the token
#   describer for the spend narration; release.sh loads it first, and this
#   file sources it itself when no caller has.
#   KANBAN_ROOT must be set (release.sh sets it from
#   PGAI_AGENT_KANBAN_ROOT_PATH). python3 must be on PATH.

# Guard against double-sourcing.
if declare -F cm_release_spend_halt_after_sentinel >/dev/null 2>&1; then
    return 0
fi

# pp_run_ops — skipped when the caller already loaded it; sourced by
# absolute path so the helper can locate its own tree whatever the working
# directory.
if ! declare -F pp_run_ops >/dev/null 2>&1; then
    # shellcheck source=../pp_run_ops.sh
    source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/pp_run_ops.sh"
fi

cm_release_spend_halt_after_sentinel() {
  local project_root="$1"
  local kanban_root="$2"

  echo "[Step 12c] Checking for a HALT-AFTER sentinel to spend at RC close..."

  local scope_root
  scope_root="$(pgai_halt_after_resolve_scope_root "$project_root" "$kanban_root" 2>/dev/null)" || {
    echo "[cm-release] no HALT-AFTER sentinel present at either scope; nothing to spend."
    return 0
  }

  local token_raw
  token_raw="$(cat "${scope_root}/HALT-AFTER" 2>/dev/null || true)"

  # release.sh runs under set -euo pipefail; capturing the code in an `||`
  # list lets a non-zero (not-yet-drained) answer reach the branch below
  # without toggling errexit for the rest of the release.
  local ha_exit=0
  pgai_halt_after_run "$scope_root" "$project_root" || ha_exit=$?

  if [[ -n "${PGAI_HALT_AFTER_OUTPUT:-}" ]]; then
    while IFS= read -r _line; do
      echo "[cm-release] halt_after: ${_line}"
    done <<< "$PGAI_HALT_AFTER_OUTPUT"
  fi

  if [[ "$ha_exit" -ne 0 ]]; then
    # 1/2/3/5 — not yet drained, invalid token, absent (shouldn't happen
    # since resolve_scope_root just confirmed presence), or drained-but-
    # still-deferred (an outer/root-scope sentinel while this project's own
    # RC just closed, but Active RC at that OTHER scope's release-state.md
    # is unrelated and could still be open — see pgai_halt_after_run's own
    # exit-code contract). Nothing more to do; the wake loop's next firing
    # re-evaluates normally.
    return 0
  fi

  # The kanban root goes first on PYTHONPATH, so pp_run_ops imports the
  # describer from the same install the evaluator above ran from.
  local desc
  desc="$(PYTHONPATH="${kanban_root}${PYTHONPATH:+:${PYTHONPATH}}" pp_run_ops pgai_agent_kanban.halt_after.describe \
    describe-raw "$token_raw" 2>/dev/null || printf '%s' "$token_raw" | tr -d '[:space:]')"
  echo "[cm-release] HALT-AFTER sentinel spent at RC close: ${desc} (scope: ${scope_root}) -- HALT-AFTER removed, HALT created, HALT Event appended to release-state.md."
  return 0
}
