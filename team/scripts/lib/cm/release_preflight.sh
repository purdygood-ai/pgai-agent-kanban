#!/usr/bin/env bash
# team/scripts/lib/cm/release_preflight.sh
#
# CM release preflight stage: project/config resolution, RC branch discovery
# and validation, the release-state.md field readers, the HALT-writing
# primitives, and the squash auto-resolve helper. Sourced by
# team/scripts/cm/release.sh; extracted from that script's former inline
# Steps 1-4a (plus the argument/config resolution that precedes them) so
# release.sh's own body stays a thin driver over sourced lib stages. This is
# a behavior-preserving move: no stdout/stderr line, exit code, HALT-file
# write, or release-state.md write changes as a result of this file
# existing — it is one stage split out of a larger driver so each stays a
# focused, independently testable unit.
#
# Public entry point
# -------------------
#   cm_release_preflight
#       Resolves project/repo context, discovers and validates the active RC
#       branch, reads release-state.md, and checks out the RC branch — the
#       original Steps 1 through 4a. Takes no arguments; reads PROJECT_ARG
#       (set by release.sh's own --project/--help arg-parsing loop) and the
#       ambient environment. Must be called directly (never via command
#       substitution) — several of its internal error paths call `exit`
#       directly, and it also registers the script's one EXIT trap
#       (cm_release_cleanup_on_exit).
#
#       On return, these globals are set for the rest of release.sh to read:
#         PROJECT_NAME, _RELEASE_PREFIX, MAIN_BRANCH, REPO_ROOT,
#         _CM_PROJECT_ROOT, PROJECT_HOOKS_DIR, PROJECT_HALT_FILE,
#         _CM_PUSH_TO_REMOTE, RELEASE_STATE, RC_BRANCH, ACTIVE_RC,
#         STATE_ACTIVE_RC.
#
# Other functions in this file (cm_halt, write_halt_file,
# append_halt_event_to_release_state, append_autoresolve_note_to_release_state,
# _cm_record_operator_override_event, read_md_field,
# _cm_autoresolve_ud_conflicts, cm_release_cleanup_on_exit -- plus
# read_field, which this file sources from lib/cm/common.sh) are shared
# primitives, not private helpers of cm_release_preflight alone — cm_halt in
# particular is called throughout the rest of release.sh (Steps 5-16) and
# from team/scripts/lib/cm/release_ship_policy.sh (which also calls
# _cm_record_operator_override_event from its _cm_apply_matrix_trigger
# helper; release_finalize.sh's Step 12 post-write block appends the
# recorded event to release-state.md, same as the auto-resolve log), and
# _cm_autoresolve_ud_conflicts is called from release.sh's Step 6 squash.
# They are defined at source time (available as soon as this file is sourced,
# independent of when cm_release_preflight itself is invoked).
#
# Dependencies (must already be sourced by the caller before this file)
# -----------------------------------------------------------------------
#   team/scripts/lib/project_paths.sh   — pp_require_project_context,
#                                         pp_branch_prefix, pp_prefix_branch,
#                                         pp_load_config, pp_project_root,
#                                         pp_release_state
#   team/scripts/lib/project_paths.sh   — pp_push_policy (the shared push
#                                         resolver's answer: _CM_PUSH_TO_REMOTE
#                                         is "true" only for a registered
#                                         project whose own project.cfg names
#                                         dev_tree_path; anything unresolved
#                                         logs skipped-unresolved-project and
#                                         runs local-only)
#   team/scripts/lib/project_paths.sh   — pp_git_remote_name (the remote
#                                         Steps 1 and 3 fetch from, ls-remote
#                                         and re-push to, and that every
#                                         remote-naming line here names)
#   team/scripts/lib/error_contract.sh  — pgai_format_error_contract,
#                                         pgai_emit_error_contract
#   team/scripts/lib/cm/release_decide_only.sh — read only when
#                                         _CM_DECIDE_ONLY is "true": Steps 1
#                                         and 3 then never contact the remote,
#                                         Step 4a does not check out, and
#                                         cm_halt reports instead of writing
#
# The caller must also set PROJECT_ARG (from --project, or "") and KANBAN_ROOT
# before calling cm_release_preflight, and must have `set -euo pipefail`
# enabled — this file relies on strict-mode error propagation exactly as the
# inline code it replaces did.

# Guard against double-sourcing.
if declare -F cm_release_preflight >/dev/null 2>&1; then
    return 0
fi

# -----------------------------------------------------------------------
# cm_release_cleanup_on_exit
#
# The script's single EXIT trap (registered by cm_release_preflight below).
# Rollback guard (verify-before-destroy): if the script exits non-zero after
# main has already received local commits (MAIN_SHA is set at Step 6, before
# the squash) but BEFORE all verification completed (_CM_VERIFICATION_COMPLETE
# is set once the pre-tag hook chain, the pre-tag VERSION gate, and the tag
# itself have all succeeded), a gate fired partway through committing release
# artifacts onto main. Reset local main back to the commit it was on when
# this release attempt began rather than leave those partial commits
# stranded. The RC branch and release-state.md are never touched before
# verification completes, so no explicit restoration is needed for either.
# -----------------------------------------------------------------------
cm_release_cleanup_on_exit() {
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
  # Scratch release-notes copy a decide-only run reads (release_decide_only.sh).
  [[ -n "${_CM_DECIDE_ONLY_SCRATCH:-}" ]] && rm -rf "$_CM_DECIDE_ONLY_SCRATCH" 2>/dev/null

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

# Helper: read a named field from the project-scoped release-state.md.
# read_field <field_heading>: the value under "## <field_heading>" in
# RELEASE_STATE, or "none" (lib/cm/common.sh).
# shellcheck source=common.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

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
CM Task: scripts/cm/release.sh (automated)
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
# The closing footer says whether the release shipped: once the finalize
# stage has cut RELEASE_TAG and set _CM_VERIFICATION_COMPLETE=1 (past the
# rollback guard in cm_release_cleanup_on_exit, so nothing undoes the tag),
# a HALT names the tag and warns against cancelling instead of saying
# nothing shipped.
cm_halt() {
  local error_key="$1"
  local message="$2"
  local remedy="$3"
  # A decide-only run reports the HALT and exits 3 before anything below
  # writes (release_decide_only.sh).
  if [[ "${_CM_DECIDE_ONLY:-false}" == "true" ]]; then
    cm_release_decide_only_halt "$error_key" "$message" "$remedy"
  fi
  echo "" >&2
  echo "[cm-release] HALT" >&2
  pgai_emit_error_contract "$error_key" "$message" "$remedy"
  write_halt_file "$error_key" "$message" "$remedy"
  append_halt_event_to_release_state "$error_key" "$message" "$remedy"
  echo "" >&2
  if [[ "${_CM_VERIFICATION_COMPLETE:-0}" == "1" && -n "${RELEASE_TAG:-}" ]]; then
    echo "HALT: scripts/cm/release.sh stopped after tagging. The release IS tagged (${RELEASE_TAG}); do not cancel it — finish it with the remedy above." >&2
    echo "The tag and every step completed before this HALT are kept for operator inspection." >&2
  else
    echo "HALT: scripts/cm/release.sh stopped. The release has NOT been shipped." >&2
    echo "The dev tree, branches, and RC state are unchanged for operator inspection." >&2
  fi
  echo "Resolve the issue above, then run: team/scripts/unhalt.sh --project $PROJECT_NAME (removes $PROJECT_HALT_FILE and resumes this project's chain)." >&2
  echo "(This HALT is scoped to project '$PROJECT_NAME' only; sibling projects' chains are unaffected.)" >&2
  # BLOCK-WORTHY: the shared project-scoped HALT dispatcher every named
  # cm/release.sh HALT trigger routes through -- error_key varies per
  # caller, so this one physical exit is named by anchor token.
  # CENSUS-ANCHOR: release-preflight-halt-01
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
CM Task: scripts/cm/release.sh (automated)
Action: Auto-resolved modify/delete (UD/DU) conflicts by taking main's side.
Resolved Paths:
${paths_resolved}
EOF
  echo "[cm-release] Auto-resolve note appended to release-state.md"
}

# Helper: record an Operator Override event, to be appended to the
# project-scoped release-state.md AFTER release_finalize.sh's Step 12
# rewrites that file wholesale ('cat > $RELEASE_STATE').
# Usage: _cm_record_operator_override_event <version> <stop_error_key> <reason>
# Called only from team/scripts/lib/cm/release_ship_policy.sh's
# _cm_apply_matrix_trigger, when cm/release.sh --force passes one of the
# three named ship-policy matrix stops it is scoped to. This is one of the
# override's two permanent records — the release-notes ## Status stamp
# (SHIPPED-BY-OPERATOR-OVERRIDE) is the other.
#
# Deferred write, same pattern _cm_autoresolve_ud_conflicts already uses for
# _CM_AUTORESOLVE_LOG: the ship-policy stage (where --force is resolved)
# runs well before Step 12's release-state.md rewrite, so writing directly
# here would be silently erased by that rewrite. Sets the
# _CM_OPERATOR_OVERRIDE_LOG global instead; release_finalize.sh's Step 12
# post-write block appends it, mirroring the auto-resolve log's own
# post-write append immediately beside it — one append-after-rewrite
# convention, not two.
#
# Untrusted-input handling: <reason> is operator-supplied text that may
# contain any bytes, including newlines or a line that reads like a markdown
# heading (e.g. "## Active RC"). It is captured with `printf '%s\n'` — never
# interpolated into a heredoc — so it can never be evaluated as shell and
# can never prematurely terminate a heredoc body. It is always the last
# field of the last event appended to the file, so even a reason line that
# looks exactly like a real heading can never be mistaken for one:
# read_state_field()/read_md_field() both return the value following the
# FIRST "## <heading>" occurrence in the file, and every real
# release-state.md schema heading is rewritten by Step 12 before this event
# is ever appended. A multi-line reason simply renders as a multi-line
# "Reason:" block.
_cm_record_operator_override_event() {
  local version="$1"
  local stop_error_key="$2"
  local reason="$3"
  local timestamp
  timestamp="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  _CM_OPERATOR_OVERRIDE_LOG="$(
    printf '\n## Operator Override\n'
    printf 'Timestamp: %s\n' "$timestamp"
    printf 'Version: %s\n' "$version"
    printf 'Stop Overridden: %s\n' "$stop_error_key"
    printf 'Reason: %s\n' "$reason"
  )"
  echo "[cm-release] Operator Override event recorded (appended to release-state.md after Step 12's rewrite)."
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

# -----------------------------------------------------------------------
# cm_release_preflight — entry point (see header doc above).
# -----------------------------------------------------------------------
cm_release_preflight() {
  # --- Resolve project context ---
  # Resolution order: --project flag > PGAI_PROJECT_NAME env var > error.
  # pp_require_project_context prints a diagnostic and returns 1 when neither is set.
  PROJECT_NAME="$(pp_require_project_context "${PROJECT_ARG:-}")" || {
    echo "" >&2
    echo "ERROR: project context is required." >&2
    echo "  Set PGAI_PROJECT_NAME=<name> in the environment, or pass --project <name>." >&2
    # BLOCK-WORTHY: release preflight cannot resolve which project's RC to
    # validate without an unambiguous project context.
    # CENSUS-ANCHOR: release-preflight-project-required-01
    exit 1
  }

  # --- Push decision (shared resolver), before any git command ---
  # Only a project registered in projects.cfg whose own project.cfg names
  # dev_tree_path may push. An unresolved one logs one skipped-unresolved-project
  # line here and runs local-only; REPO_ROOT in the environment never counts.
  pp_push_policy "$PROJECT_NAME" "[cm-release]"
  _CM_PUSH_TO_REMOTE="$PP_PUSH_TO_REMOTE"

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
      # BLOCK-WORTHY: an unloadable project.cfg leaves REPO_ROOT unknown --
      # release preflight cannot validate a dev tree it cannot locate.
      # CENSUS-ANCHOR: release-preflight-config-unloadable-01
      exit 1
    }
    REPO_ROOT="${PP_dev_tree_path:-}"
    if [[ -z "$REPO_ROOT" ]]; then
      echo "ERROR: dev_tree_path is not set in project.cfg for project '$PROJECT_NAME'" >&2
      echo "  Add 'dev_tree_path=<path>' to $(pp_project_root "$PROJECT_NAME")/project.cfg" >&2
      # BLOCK-WORTHY: same reasoning as release-preflight-config-unloadable-01
      # -- a blank dev_tree_path is as unusable as a missing project.cfg.
      # CENSUS-ANCHOR: release-preflight-dev-tree-path-unset-01
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
  # for framework-wide emergencies (see OVERVIEW.md, SOP-core.md "HALT Flags"); a
  # single project's release failure must never freeze sibling projects' chains.
  # wake_common.sh's dispatch loop checks $KANBAN_ROOT/HALT once, globally,
  # before iterating projects (fast exit for the whole chain) and separately
  # checks pp_project_halted() per project inside the loop — writing here at
  # project scope is what keeps a release failure from tripping that global
  # fast-exit and starving every other project's chain.
  PROJECT_HALT_FILE="${_CM_PROJECT_ROOT}/HALT"

  # --- Resolve the project's git remote, once ---
  # Steps 1 and 3 fetch from, ls-remote and re-push to this remote, and every
  # line below that names a remote names it: project.cfg [project]
  # git_remote_name, origin when absent or blank (pp_git_remote_name). Only a
  # run that will contact the remote refuses an invalid value here, before any
  # git step; a decide-only or push_to_remote=false run contacts none, so it
  # resolves quietly and narrates a placeholder instead (the squash stage still
  # refuses the value on a real local-only run, as it did before).
  local _pf_remote
  if [[ "$_CM_PUSH_TO_REMOTE" == "true" && "${_CM_DECIDE_ONLY:-false}" != "true" ]]; then
    if ! _pf_remote="$(pp_git_remote_name "$PROJECT_NAME")"; then
      # BLOCK-WORTHY (docs/blocking-site-census.md): a git_remote_name that
      # cannot name a remote would hand git an option, path or URL as the
      # remote Step 1 fetches from and Step 3 pushes to; pp_git_remote_name
      # has printed the dual-channel block naming the key and the file.
      # CENSUS-ANCHOR: release-preflight-git-remote-name-01
      exit 1
    fi
  else
    _pf_remote="$(pp_git_remote_name --quiet "$PROJECT_NAME")" || _pf_remote="<invalid git_remote_name>"
  fi

  # --- Narrate the push policy decided above ---
  # A resolved project with no push_to_remote key pushes (the default);
  # push_to_remote = false completes the full local release (squash/merge/tag)
  # without any git push to the remote -- for developer-workstation or demo
  # installs where a human controls when work reaches the company remote.
  if [[ "$PP_PUSH_DECISION" == "push" ]]; then
    echo "[cm-release] Push policy: push_to_remote=true — branches and tag will be pushed to ${_pf_remote}."
  elif [[ "$PP_PUSH_DECISION" == "no-push" ]]; then
    echo "[cm-release] Push policy: push_to_remote=false — branches and tag stay local. Operator must push manually."
  fi

  # --- Clean exit handling ---
  # This is the ONLY EXIT trap for the whole script (do not add another `trap ...
  # EXIT` further down in release.sh — it would silently replace this one and
  # disable both the job cleanup and the rollback guard for the remainder of the
  # run; clean up any later scratch file by extending cm_release_cleanup_on_exit
  # instead, as done there for _cl_buf).
  trap cm_release_cleanup_on_exit EXIT

  # --- Paths ---
  # Canonical release state file: project-scoped live install path.
  # This is the ONLY file cm/release.sh reads from and writes to.
  # The dev tree's team/release-state.md is NOT consulted.
  RELEASE_STATE="$(pp_release_state "$PROJECT_NAME")"

  # Accumulator for auto-resolved UD/DU paths (set by _cm_autoresolve_ud_conflicts).
  # Appended to release-state.md after Step 15 (which overwrites the file with 'cat >').
  _CM_AUTORESOLVE_LOG=""

  # Accumulator for an Operator Override event (set by
  # _cm_record_operator_override_event when --force passes a matrix stop).
  # Appended to release-state.md after Step 12's rewrite, same as above.
  _CM_OPERATOR_OVERRIDE_LOG=""

  # --- Step 1: git fetch <remote> --tags ---
  # Fetch first so we can find and read the RC branch without depending on the working tree.
  # Gated by push_to_remote flag: skipped entirely when push_to_remote=false (no remote).
  # A decide-only run never fetches: it reads local refs and changes none.
  if [[ "${_CM_DECIDE_ONLY:-false}" == "true" ]]; then
    echo "[Step 1] decide-only — skipping git fetch ${_pf_remote} (the decision reads local refs only)."
  elif [[ "$_CM_PUSH_TO_REMOTE" == "true" ]]; then
    echo "[Step 1] Fetching ${_pf_remote} and tags..."
    git_step "git fetch ${_pf_remote} --tags" git -C "$REPO_ROOT" fetch "$_pf_remote" --tags
  else
    echo "[Step 1] push_to_remote=false — skipping git fetch ${_pf_remote} (local-only install, no remote to fetch from)."
  fi

  # --- Defensive stash check ---
  # Warn if stashes exist. Stashed changes could indicate uncommitted work that
  # was intended for the RC. This is advisory; the human operator decides.
  local STASH_OUTPUT
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
  local _RC_GLOB="${_RELEASE_PREFIX}rc/*"
  mapfile -t RC_BRANCHES < <(git -C "$REPO_ROOT" branch --list "$_RC_GLOB" --format='%(refname:short)' 2>/dev/null || true)

  if [[ ${#RC_BRANCHES[@]} -eq 0 ]]; then
    echo "ERROR: no local ${_RC_GLOB} branch found." >&2
    echo "There is no active release candidate to close." >&2
    # BLOCK-WORTHY: cm/release.sh cannot ship a release with no RC branch
    # to close.
    # CENSUS-ANCHOR: release-preflight-no-rc-branch-01
    exit 1
  fi

  if [[ ${#RC_BRANCHES[@]} -gt 1 ]]; then
    echo "ERROR: multiple ${_RC_GLOB} branches found locally: ${RC_BRANCHES[*]}" >&2
    echo "Only one RC branch may be active at a time. Resolve manually." >&2
    # BLOCK-WORTHY: shipping cannot disambiguate which of several RC
    # branches is the one to release -- the single-flight invariant.
    # CENSUS-ANCHOR: release-preflight-multiple-rc-branches-01
    exit 1
  fi

  RC_BRANCH="${RC_BRANCHES[0]}"
  # Strip the leading "${_RELEASE_PREFIX}rc/" to get just the version token
  ACTIVE_RC="${RC_BRANCH#${_RELEASE_PREFIX}rc/}"

  echo "  Found RC branch: $RC_BRANCH"

  # --- Step 3: Validate RC branch exists locally and on the remote ---
  # Gated by push_to_remote flag:
  #   push_to_remote=true  — validate the RC branch exists on the remote (original behavior).
  #     Idempotency path A: remote RC already absent AND local tag exists → Step 13 already
  #       ran in a prior partial run; continue without re-pushing.
  #     Idempotency path B: remote RC already absent AND local tag absent AND local RC
  #       branch present → interrupted before Step 13; re-establish the RC branch on
  #       the remote from the known-good local ref and continue (avoids manual recovery push).
  #   push_to_remote=false — RC was never pushed to the remote (cm/open-rc.sh honored the
  #     flag and kept the RC local). Validate locally via refs/heads/ instead.
  #   decide-only — validated locally like push_to_remote=false: the decision
  #     never contacts the remote, and path B's re-push is a write it must not make.
  if [[ "$_CM_PUSH_TO_REMOTE" == "true" && "${_CM_DECIDE_ONLY:-false}" != "true" ]]; then
    echo "[Step 3] Validating $RC_BRANCH exists locally and on ${_pf_remote}..."
    if ! git -C "$REPO_ROOT" rev-parse --verify "refs/heads/$RC_BRANCH" >/dev/null 2>&1; then
      echo "ERROR: branch '$RC_BRANCH' does not exist locally" >&2
      # BLOCK-WORTHY: there is nothing to ship without the RC branch
      # locally (push_to_remote=true path).
      # CENSUS-ANCHOR: release-preflight-branch-missing-local-pushed-01
      exit 1
    fi
    if ! git -C "$REPO_ROOT" ls-remote --exit-code --heads "$_pf_remote" "$RC_BRANCH" >/dev/null 2>&1; then
      # The remote's RC branch is absent. Determine which intermediate state we are in.
      local _step3_release_tag _step3_tag_sha _step3_push_out _step3_push_rc
      _step3_release_tag="$(pp_prefix_tag "$PROJECT_NAME" "$ACTIVE_RC" 2>/dev/null)" || _step3_release_tag="$ACTIVE_RC"
      _step3_tag_sha="$(git -C "$REPO_ROOT" rev-parse "${_step3_release_tag}^{}" 2>/dev/null)" || _step3_tag_sha=""
      if [[ -n "$_step3_tag_sha" ]]; then
        # Path A: tag already exists locally → Step 13 ran in a prior partial run. Skip re-push.
        echo "[Step 3] ${_pf_remote} RC already absent AND local tag ${_step3_release_tag} exists — Step 13 already ran in prior partial run. Continuing."
      else
        # Path B: tag absent + local RC branch present → release was interrupted before Step 13.
        # Re-establish the RC branch on the remote from the known-good local ref so downstream
        # steps (squash, merge, tag) can proceed without manual intervention.
        echo "[Step 3] ${_pf_remote} RC absent and tag absent — local RC branch present; re-establishing ${RC_BRANCH} on ${_pf_remote}..."
        _step3_push_out=$(git -C "$REPO_ROOT" push "$_pf_remote" "${RC_BRANCH}" 2>&1); _step3_push_rc=$?
        if [[ $_step3_push_rc -ne 0 ]]; then
          echo "ERROR: failed to re-push ${RC_BRANCH} to ${_pf_remote} (rc=${_step3_push_rc})" >&2
          echo "  git push output: ${_step3_push_out}" >&2
          echo "  Recover manually: git push ${_pf_remote} ${RC_BRANCH}" >&2
          # BLOCK-WORTHY: a failed re-push of the RC branch leaves the remote
          # and local disagreeing about the release's own source branch.
          # CENSUS-ANCHOR: release-preflight-rc-repush-failure-01
          exit 1
        fi
        echo "[Step 3] ${RC_BRANCH} re-pushed to ${_pf_remote} successfully. Resuming release."
      fi
    else
      echo "  Confirmed: $RC_BRANCH exists locally and on ${_pf_remote}."
    fi
  else
    # push_to_remote=false: RC was kept local by cm/open-rc.sh; validate locally only.
    if [[ "${_CM_DECIDE_ONLY:-false}" == "true" ]]; then
      echo "[Step 3] decide-only — validating $RC_BRANCH exists locally (no ${_pf_remote} check)..."
    else
      echo "[Step 3] push_to_remote=false — validating $RC_BRANCH exists locally (no ${_pf_remote} check)..."
    fi
    if ! git -C "$REPO_ROOT" rev-parse --verify "refs/heads/$RC_BRANCH" >/dev/null 2>&1; then
      echo "ERROR: branch '$RC_BRANCH' does not exist locally" >&2
      # BLOCK-WORTHY: there is nothing to ship without the RC branch
      # locally (push_to_remote=false path).
      # CENSUS-ANCHOR: release-preflight-branch-missing-local-unpushed-01
      exit 1
    fi
    echo "  Confirmed: $RC_BRANCH exists locally$([[ "${_CM_DECIDE_ONLY:-false}" == "true" ]] || echo " (local-only install, ${_pf_remote} check skipped)")."
  fi

  # --- Step 4: Read Active RC from project-scoped release-state.md ---
  # Read from the canonical live install file, not from git show.
  # The active RC must already be set there by cm/open-rc.sh.
  echo "[Step 4] Reading Active RC from project-scoped release-state.md..."

  if [[ ! -f "$RELEASE_STATE" ]]; then
    echo "ERROR: project-scoped release-state.md not found: $RELEASE_STATE" >&2
    # BLOCK-WORTHY: cm/release.sh cannot read Active RC without this file.
    # CENSUS-ANCHOR: release-preflight-release-state-missing-01
    exit 1
  fi

  STATE_ACTIVE_RC="$(read_field "Active RC")"

  if [[ "$STATE_ACTIVE_RC" == "none" ]]; then
    echo "ERROR: project-scoped release-state.md shows Active RC = none" >&2
    echo "  Path: $RELEASE_STATE" >&2
    echo "Nothing to release. The RC branch may be in an inconsistent state." >&2
    # BLOCK-WORTHY: release-state.md disagreeing with the RC branch found
    # on disk is a state-corrupting inconsistency, not a guess to paper over.
    # CENSUS-ANCHOR: release-preflight-active-rc-none-01
    exit 1
  fi

  if [[ "$STATE_ACTIVE_RC" != "$ACTIVE_RC" ]]; then
    echo "ERROR: branch name mismatch: branch is '$RC_BRANCH' but release-state.md says Active RC = '$STATE_ACTIVE_RC'" >&2
    echo "  Path: $RELEASE_STATE" >&2
    echo "Resolve manually." >&2
    # BLOCK-WORTHY: shipping under a branch name that disagrees with the
    # recorded Active RC risks releasing the wrong version.
    # CENSUS-ANCHOR: release-preflight-active-rc-mismatch-01
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
  # This checkout decides which branch a release hook's own git operations
  # land on. cm_resolve_and_enforce_hook's rc_branch-aware git-show
  # resolution (team/scripts/lib/cm_release_hooks.sh) makes hook CONTENT
  # discovery independent of the current checkout, but a hook that COMMITS
  # (e.g. a version bump) still executes via `cd "$REPO_ROOT"` in
  # _run_release_hook — its commit lands on whatever branch the working copy
  # is on. Resolution settles "which script runs"; this checkout settles
  # "what branch the script's own git operations land on", so a hook-wired
  # release works even when the dev tree was parked on another branch at
  # invocation time. Checking out the RC branch here — before any RC-scoped
  # step reads or writes the working tree — covers every RC-scoped step at
  # once instead of asking each to route through git plumbing.
  #
  # A decide-only run does not check out: the only RC-scoped read it makes
  # is Step 4c's release-notes/ scan, which cm_release_decide_only_locate_notes
  # (release_decide_only.sh) serves from the RC branch without moving HEAD.
  if [[ "${_CM_DECIDE_ONLY:-false}" == "true" ]]; then
    cm_release_decide_only_locate_notes
    return 0
  fi
  echo "[Step 4a] Checking out $RC_BRANCH so all RC-scoped steps (hooks included) operate on the RC's own working tree..."
  git_step "git checkout $RC_BRANCH" git -C "$REPO_ROOT" checkout "$RC_BRANCH"
}
