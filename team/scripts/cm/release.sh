#!/usr/bin/env bash
# team/scripts/cm/release.sh
# Human-invoked: closes an active release candidate branch.
#
# Usage:
#   cm/release.sh [--project <name>] [--decide-only] [--help]
#   cm/release.sh [--project <name>] --force --reason <text>     (operator only)
#
# --project <name>   explicit project name (overrides PGAI_PROJECT_NAME env var)
# --decide-only      print the ship-policy decision and exit (0 ship, 3 HALT)
#                     without writing anything (release_decide_only.sh)
# --help, -h         print full usage and exit 0 (see _cm_release_help below
#                     for the full argument/exit-code/configuration reference)
#
# Operator-only options (release_operator_override.sh):
# --force                   override a ship-policy stop; refused under an
#                            agent wake (PGAI_AGENT_WAKE_ACTIVE=1)
# --reason <text>           required with --force; recorded verbatim in
#                            release-state.md
# --overwatch-lane-b-ship   OVERWATCH's ship path: admits --force under an
#                            agent wake only when --reason exactly matches a
#                            live INTENT.md entry
#
# Thin driver: argument parsing plus an ordered composition of the CM
# release lib stages under team/scripts/lib/cm/ (release_preflight.sh,
# release_known_issue.sh, release_operator_override.sh,
# release_ship_posture.sh, release_ship_policy.sh, release_squash.sh, release_notes.sh,
# release_campaign_spend.sh, release_finalize.sh — sourced and called below
# in that order; release_decide_only.sh, the --decide-only stage, is sourced
# last and ends a decide-only run at the ship-policy decision;
# release_origin_summary.sh, the closing-summary formatter
# release_finalize.sh calls, is sourced just ahead of it; release_notes.sh
# sources its own VERSION-file stage, release_version_file.sh, and
# release_finalize.sh its own Fixed-In stage, release_fixed_in.sh, and
# archive stage, release_archive.sh). Each
# stage documents its own steps, HALT triggers, and globals in its own
# file header. The pre-tag/post-tag hook steps stay defined below
# (_cm_run_pre_tag_hook_step / _cm_run_post_tag_hook_step) and are
# threaded into release_finalize.sh by name — see that file's "Why two
# callbacks" for why.
#
# Reads the Active RC from the project-scoped release-state.md at
# $KANBAN_ROOT/projects/<name>/release-state.md. Works regardless of which
# branch is checked out; release_preflight checks out the RC branch first.
#
# Verify before destroy: every gate that can veto the release runs BEFORE
# any destructive step (RC branch deletion, release-state.md rewrite),
# which run only after release_finalize.sh's "verification complete"
# narration — see cm_release_cleanup_on_exit (release_preflight.sh) for the
# rollback this buys on an earlier failure. HALT triggers create the
# project-scoped $KANBAN_ROOT/projects/<name>/HALT (never the global HALT)
# and exit 1 — see docs/blocking-site-census.md for the full inventory.
#
# Post-release archive: the last finalize step (Step 16c) moves this
# project's finished items beyond [archive] archive_count into
# $KANBAN_ROOT/archive/<project>/ once the RC branch is gone and
# release-state.md records the release. It is quality class: it never blocks
# or delays the release or changes its exit status; a skipped or failed run
# is narrated and retried after the next release. Run from CM's own wake, it
# works under the repo lock that wake chain already holds; a repo lock held
# by any other process skips it with nothing moved. A cancelled RC
# (cm/cancel-rc.sh) never runs it.
#
# Operator handoff: after this script exits, the operator runs
# cm/finalize-release.sh (or manually: git push origin main && git push
# origin <VERSION>) to push main and the tag to origin.

# --- Argument parsing ---
# Done before strict mode so error messages are clean.
PROJECT_ARG=""

# --- Operator force-override state ---
# FORCE_ARG / FORCE_REASON are populated by the --force / --reason case
# branches below and listed under "Operator-only options" in
# _cm_release_usage/_cm_release_help. Neither variable is read until
# after cm_release_apply_ship_policy resolves the ship-policy decision (see
# team/scripts/lib/cm/release_ship_policy.sh, _cm_apply_matrix_trigger) —
# passing --force with no matching matrix stop is simply a no-op.
FORCE_ARG="false"
FORCE_REASON=""

# --- OVERWATCH Lane B ship-path discriminator ---
# OVERWATCH_LANE_B_ARG is populated by the --overwatch-lane-b-ship case
# branch below. It is listed under "Operator-only options" in
# _cm_release_usage/_cm_release_help, though its one caller is
# pgai_agent_kanban.overwatch.resolution_stage's Lane B ship path, never an
# operator by hand. Passing this flag alone proves nothing — it only
# selects which refusal branch the wake-context check below evaluates;
# admission still requires this script's own independent re-verification
# that --reason matches a live INTENT.md entry (see
# "_cm_release_lane_b_intent_verified" below).
OVERWATCH_LANE_B_ARG="false"

# --- Decide-only state ---
# DECIDE_ONLY_ARG is populated by the --decide-only case branch below; the
# run then stops after the ship-policy decision (release_decide_only.sh).
DECIDE_ONLY_ARG="false"

_cm_release_usage() {
  echo "Usage: $(basename "$0") [--project <name>] [--decide-only] [--help]" >&2
  echo "" >&2
  echo "  --project <name>  project name (required when PGAI_PROJECT_NAME not set)" >&2
  echo "  --decide-only     print the ship-policy decision and exit; writes nothing" >&2
  echo "  --help, -h        print full usage and exit 0" >&2
  echo "" >&2
  echo "Operator-only options:" >&2
  echo "  --force                  override a ship-policy stop (refused under an agent wake)" >&2
  echo "  --reason <text>          required with --force; recorded verbatim in release-state.md" >&2
  echo "  --overwatch-lane-b-ship  admit --force under an agent wake only when --reason" >&2
  echo "                           exactly matches a live INTENT.md entry" >&2
}

_cm_release_help() {
  cat <<HELPTEXT
Usage: $(basename "$0") [--project <name>] [--decide-only] [--help]

Close an active release candidate: squash-merge to main, tag, and ship.

Reads the Active RC from \$KANBAN_ROOT/projects/<name>/release-state.md.
No positional arguments — the version is derived from the live release-state.

Arguments:
  --project <name>  project name; overrides PGAI_PROJECT_NAME env var
  --decide-only     run the preflight and the ship-policy matrix with the
                    project's ship posture applied, print the decision a
                    full run would reach, and stop. Writes nothing: no
                    fetch, push or checkout, no commit, branch or tag, no
                    HALT file, no release-state.md change, no bug filed and
                    no release-notes stamp. Runs under an agent wake
                    (PGAI_AGENT_WAKE_ACTIVE=1). Cannot be combined with
                    --force or --reason. Narration goes to stderr; stdout
                    carries one line:
                      ship-policy: decision=<SHIP-…|HALT> posture=<matrix|always> trigger=<n|none> notes_status=<…>
                    trigger is the stop's trigger number on HALT ("none" for
                    the unrecognized-TESTER-state and no-row-matched stops)
                    and, on a ship, each trigger the always posture shipped
                    through, comma-separated in the order they fired.
                    notes_status is "none" on HALT. On HALT the line is
                    followed by the would-be HALT's error:, message: and
                    remedy: lines; error: is the HALT error key.
  --help, -h        print this message and exit 0

Operator-only options:
  --force           override the ship-policy stop this release would
                    otherwise HALT on, and ship. Refused when an agent wake
                    runs the command (PGAI_AGENT_WAKE_ACTIVE=1), except
                    with --overwatch-lane-b-ship below. A no-op when no
                    stop fires. Requires --reason.
  --reason <text>   required with --force, never blank or defaulted; the
                    text is recorded verbatim in release-state.md with the
                    overridden stop.
  --overwatch-lane-b-ship
                    OVERWATCH's ship path, not for use by hand: admits
                    --force under an agent wake only when --reason exactly
                    matches a live entry in the project's INTENT.md;
                    refused otherwise.
  Usage: $(basename "$0") --project <name> --force --reason "<text>"

Project context resolution (highest to lowest precedence):
  1. --project <name> flag
  2. PGAI_PROJECT_NAME environment variable
  3. FAIL — prints error naming both knobs, exits non-zero

Exit codes:
  0  Release shipped (or --help requested); with --decide-only, the
     decision is a SHIP-* decision
  1  HALT condition, pre-condition error, or git failure
     (a \$KANBAN_ROOT/projects/<name>/HALT file is created on HALT
     conditions — project-scoped; the global \$KANBAN_ROOT/HALT is never
     written by this script); with --decide-only, a pre-condition error
     that kept the decision from being reached (nothing on stdout)
  3  --decide-only: the decision is HALT (nothing is written)

Example:
  scripts/cm/release.sh --project my-project
  scripts/cm/release.sh --project my-project --decide-only

Configuration:
  PGAI_PROJECT_NAME              fallback project name (when --project flag not used)
  REPO_ROOT                      override path to the repository root
  CM_SQUASH_STRICT_POLLUTION_GUARD
                                 set to 'true' to HALT on squash pollution detection
  [release] ship_posture         matrix|always, in project.cfg or kanban.cfg
                                 (default matrix). Under always, ship-policy
                                 triggers 2 and 3 ship with release-notes Status
                                 NON-FUNCTIONAL instead of halting, and trigger 8
                                 files a bug naming the pattern and ships; each
                                 appends an '## Auto-Ship Event' to
                                 release-state.md and puts the line 'Auto-shipped
                                 under ship_posture=always (would have halted:
                                 trigger N — <error key>)' under the release
                                 notes' '## Known Issues'. Every other HALT is
                                 unchanged.
                                 A push_to_remote = true lane also needs
                                 [release] ship_posture_confirm_remote = true.
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
    --force)
      FORCE_ARG="true"
      shift
      ;;
    --reason)
      if [[ -z "${2:-}" ]]; then
        echo "ERROR: --reason requires a value" >&2
        echo "" >&2
        _cm_release_usage
        exit 1
      fi
      FORCE_REASON="$2"
      shift 2
      ;;
    --overwatch-lane-b-ship)
      OVERWATCH_LANE_B_ARG="true"
      shift
      ;;
    --decide-only)
      DECIDE_ONLY_ARG="true"
      shift
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

# env_bootstrap.sh resolves and exports PGAI_AGENT_KANBAN_ROOT_PATH only —
# it touches no project/repo state (no git, no release-state.md, no HALT
# file), so sourcing it this early does not weaken the "before ANY
# project/repo state is touched" guarantee the force-override refusals
# below still rely on; it must simply run before this script's own first
# reference to PGAI_AGENT_KANBAN_ROOT_PATH, which is the Lane B admission
# check further down (_cm_release_lane_b_intent_verified's first argument).
# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

# --- Operator force-override refusals ---
# Both refusals below must run before ANY project/repo state is touched
# (before project/config resolution, before the RC branch is even
# discovered) so a refused --force invocation truly ships nothing and
# leaves release-state.md and every release-notes file untouched — these
# are argument-validation refusals, not ship-policy HALTs, and must never
# call cm_halt (which writes a HALT file and appends a HALT Event to
# release-state.md; that write is exactly what "leaves the release state
# ... unchanged" rules out here). error_contract.sh has no dependencies of
# its own, so it is safe to source this early, ahead of the rest of this
# script's lib sourcing below.
# shellcheck source=../lib/error_contract.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/error_contract.sh"

# pp_run_ops.sh is also safe to source this early: it is self-locating
# (BASH_SOURCE[0]-only, no env/project dependency) and sourcing it twice is
# harmless (this script sources it again, unconditionally, further below
# for its ordinary post-bootstrap uses). Needed here so the Lane B
# verification helper immediately below can compose a correct PYTHONPATH
# before project/config resolution has run.
# shellcheck source=../lib/pp_run_ops.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/pp_run_ops.sh"

# Helper: independently verify that *reason* is the verbatim declaration
# text of a LIVE entry in <kanban_root>/projects/<project>/INTENT.md, read
# through the canonical parser
# (pgai_agent_kanban.overwatch.intent.parse_entries) — the SAME parser
# Lane B's own classifier (pgai_agent_kanban.overwatch.resolution_matrix.
# classify_lane) uses, so this check and that classifier can never disagree
# about what counts as a match. This is the sole authority behind the Lane
# B narrow admission below: the flag OVERWATCH passes proves nothing by
# itself, only this independent re-read of the live ledger does.
# Usage: _cm_release_lane_b_intent_verified <kanban_root> <project> <reason>
# Returns 1 (no admission) for a missing kanban_root/project, an absent
# INTENT.md, or a reason that is not an exact match for some entry's
# declaration text — fail-closed throughout, exactly like the parser it
# calls (an unreadable/malformed INTENT.md parses to zero entries there,
# which never matches anything here either).
_cm_release_lane_b_intent_verified() {
  local kanban_root="$1"
  local project="$2"
  local reason="$3"
  if [[ -z "$kanban_root" || -z "$project" ]]; then
    return 1
  fi
  local intent_file="${kanban_root}/projects/${project}/INTENT.md"
  [[ -f "$intent_file" ]] || return 1
  pp_run_ops pgai_agent_kanban.overwatch.intent declares "$intent_file" "$reason"
}

# --decide-only predicts the unforced decision and writes nothing, so it
# takes none of the override flags. Refused before any project/repo state is
# read, through pgai_format_error_contract (stderr only) rather than the
# emitter, whose refusal-ledger record would be a write.
if [[ "$DECIDE_ONLY_ARG" == "true" && ( "$FORCE_ARG" == "true" || -n "$FORCE_REASON" \
      || "$OVERWATCH_LANE_B_ARG" == "true" ) ]]; then
  # The remedy names the project the operator asked about (--project, else
  # PGAI_PROJECT_NAME). With neither, it names the upper-case PROJECT_NAME
  # placeholder and says to replace it: project names are lower-case, so the
  # placeholder can never be mistaken for, or run against, a real project.
  _decide_only_project="${PROJECT_ARG:-${PGAI_PROJECT_NAME:-}}"
  if [[ -n "$_decide_only_project" ]]; then
    _decide_only_remedy="Run the decider alone: $(basename "$0") --project $(printf '%q' "$_decide_only_project") --decide-only"
  else
    _decide_only_remedy="Run the decider alone, replacing PROJECT_NAME with the project to decide for: $(basename "$0") --project PROJECT_NAME --decide-only"
  fi
  echo "" >&2
  pgai_format_error_contract \
    "cm-release.decide-only.override-flag-refused" \
    "--decide-only was combined with --force, --reason or --overwatch-lane-b-ship — the decider reports the decision the unforced release reaches and writes nothing, so it takes no override flag." \
    "$_decide_only_remedy" >&2
  echo "" >&2
  # BLOCK-WORTHY: an argument-validation refusal -- an override flag would
  # make the decider report something other than the unforced decision.
  # CENSUS-ANCHOR: release-decide-only-override-refused-01
  exit 1
fi

if [[ "$FORCE_ARG" == "true" ]]; then
  # The reason is the record --force leaves; a default or blank reason would
  # make that record a lie, so it is refused rather than defaulted.
  if [[ -z "$(printf '%s' "$FORCE_REASON" | tr -d '[:space:]')" ]]; then
    echo "" >&2
    pgai_emit_error_contract \
      "cm-release.operator-force.reason-required" \
      "--force was given without --reason, or --reason was empty or whitespace-only — an operator override must carry a written reason, never a default one." \
      "Re-run with both flags, substituting your own one-line justification for the placeholder text (it is recorded verbatim in release-state.md): $(basename "$0") --force --reason \"write your reason here\" (plus --project if required)."
    # BLOCK-WORTHY: an approval-by-design invariant -- an operator override
    # with no attributed reason would make its own permanent record a lie.
    # CENSUS-ANCHOR: operator-force-reason-required-01
    exit 1
  fi

  # An agent wake sets PGAI_AGENT_WAKE_ACTIVE=1 (team/scripts/lib/
  # wake_driver_invoke.sh, where the per-task environment is exported) —
  # a positive signal, never inferred from a TTY, a parent process name, or
  # any other environment variable that happens to be set for other
  # reasons. --force is operator-only: it must never be reachable from an
  # automated agent invocation, EXCEPT the one narrow, separately-named,
  # separately-anchored carve-out below: OVERWATCH's own Lane B
  # ship path, admitted only when independently proven against the live
  # INTENT.md ledger — never on the caller's say-so.
  if [[ "${PGAI_AGENT_WAKE_ACTIVE:-0}" == "1" ]]; then
    if [[ "$OVERWATCH_LANE_B_ARG" == "true" ]]; then
      # --- Lane B narrow admission ---
      # pgai_agent_kanban.overwatch.resolution_stage's Lane B ship path
      # passes --overwatch-lane-b-ship alongside --force --reason <text>
      # from inside OVERWATCH's own wake. The flag only selects this
      # branch; admission itself requires _cm_release_lane_b_intent_verified
      # (above) to independently confirm --reason is the verbatim text of a
      # live entry in the resolved project's INTENT.md. No entry, no
      # admission, regardless of who passes the flag or why — the
      # declaration is the entire authority (requirements doc, "Lane B —
      # declared operator intent"), and this script never manufactures one
      # on the caller's behalf.
      if _cm_release_lane_b_intent_verified \
           "${PGAI_AGENT_KANBAN_ROOT_PATH:-}" \
           "${PROJECT_ARG:-${PGAI_PROJECT_NAME:-}}" \
           "$FORCE_REASON"; then
        echo "[cm-release] OVERWATCH LANE B: --force admitted under wake context — --reason verbatim-matches a live INTENT.md entry for this project." >&2
      else
        echo "" >&2
        pgai_emit_error_contract \
          "cm-release.overwatch-lane-b.intent-not-matched" \
          "--overwatch-lane-b-ship was passed together with --force --reason under PGAI_AGENT_WAKE_ACTIVE=1, but --reason does not verbatim-match any live entry in the resolved project's INTENT.md — Lane B's declared-intent authority requires an exact match, never a partial or thematic one." \
          "Add (or correct) a dated entry in the project's INTENT.md whose declaration text is byte-for-byte identical to --reason — not merely containing it as a substring alongside other words — then re-invoke; or, if this was never meant to be a Lane B ship, drop --overwatch-lane-b-ship and --force entirely and let the block escalate to Lane C for a human."
        # BLOCK-WORTHY: an approval-by-design invariant -- OVERWATCH's own
        # Lane B claim must be independently proven against the live
        # INTENT.md ledger before the operator-only override is admitted;
        # an unverified claim is refused exactly like a bare unauthorized
        # --force below, just under a separate, more specific key.
        # CENSUS-ANCHOR: overwatch-lane-b-intent-unmatched-01
        exit 1
      fi
    else
      echo "" >&2
      pgai_emit_error_contract \
        "cm-release.operator-force.agent-wake-refused" \
        "--force was passed while PGAI_AGENT_WAKE_ACTIVE=1 — an agent wake is running this invocation, and the operator override is operator-only. (The one narrow exception, OVERWATCH's own Lane B ship path, requires --overwatch-lane-b-ship together with a --reason that verbatim-matches a live INTENT.md entry — this invocation carried neither.)" \
        "From an interactive operator shell, outside any wake/cron invocation, run $(basename "$0") --force --reason \"write your reason here\", substituting your own one-line justification for the placeholder text (it is recorded verbatim in release-state.md)."
      # BLOCK-WORTHY: an approval-by-design invariant -- the operator
      # override must never be reachable from an automated agent context,
      # except the one narrow, separately-anchored Lane B carve-out above
      # (CENSUS-ANCHOR overwatch-lane-b-intent-unmatched-01).
      # CENSUS-ANCHOR: operator-force-wake-context-01
      exit 1
    fi
  fi
fi

# env_bootstrap.sh already sourced above (before the operator force-override
# refusals), so PGAI_AGENT_KANBAN_ROOT_PATH is already resolved and exported
# by this point — nothing further to do here.

# --- Source optional config files (BEFORE strict mode) — bashrc/env may ---
# --- have unset vars or non-zero returns that would trip strict mode.  ---
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
TEAM_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
[[ -f "$KANBAN_ROOT/bashrc" ]] && source "$KANBAN_ROOT/bashrc"
[[ -f "$KANBAN_ROOT/env" ]] && source "$KANBAN_ROOT/env"
# $HOME/.config/pgai-kanban.cfg is operator-local bash config; sourced as-is.
[[ -f "$HOME/.config/pgai-kanban.cfg" ]] && source "$HOME/.config/pgai-kanban.cfg"

# shellcheck source=lib/project_paths.sh
# NOTE: project_paths.sh loads ini_parser.sh (read_ini), used just below.
source "$(dirname "${BASH_SOURCE[0]}")/../lib/project_paths.sh"

# --- Read INI config (kanban.cfg); falls back to legacy config.cfg if absent. ---
_CM_KANBAN_CFG="${TEAM_ROOT}/kanban.cfg"
if [[ -f "$_CM_KANBAN_CFG" ]]; then
    export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(read_ini "$_CM_KANBAN_CFG" paths dev_tree_path "")}"
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "$_CM_KANBAN_CFG" chain pm_mode automatic)}"
fi

# shellcheck source=lib/projects.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/projects.sh"

# cm_resolve_and_enforce_hook — the single hook-resolution call site shared
# by release.sh and the ship-rc verb (cm/ship.py) (one-implementation rule).
# shellcheck source=lib/cm_release_hooks.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/cm_release_hooks.sh"

# shellcheck source=lib/pp_run_ops.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/pp_run_ops.sh"

# HALT-AFTER scope-root resolution + `python3 -m halt_after` invocation --
# shared with the wake loop (team/scripts/lib/wake_project.sh) so CM's
# ship-time spend of a version-pinned sentinel (release_finalize.sh, Step
# 12c) uses the exact same evaluator, not a second implementation.
# shellcheck source=lib/halt_after_invoke.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/halt_after_invoke.sh"

# Dual-channel error-contract emitter — every fail-loud site in this script
# and its sourced lib stages speaks through this one primitive.
# shellcheck source=lib/error_contract.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/error_contract.sh"

# CM release preflight, ship-policy, squash, notes, and finalize lib stages
# (see each file's own header for its public API and step-by-step doc).
# shellcheck source=lib/cm/release_preflight.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/cm/release_preflight.sh"
# shellcheck source=lib/cm/release_known_issue.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/cm/release_known_issue.sh"
# shellcheck source=lib/cm/release_operator_override.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/cm/release_operator_override.sh"
# shellcheck source=lib/cm/release_ship_posture.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/cm/release_ship_posture.sh"
# shellcheck source=lib/cm/release_ship_policy.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/cm/release_ship_policy.sh"
# shellcheck source=lib/cm/release_squash.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/cm/release_squash.sh"
# shellcheck source=lib/cm/release_notes.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/cm/release_notes.sh"
# shellcheck source=lib/cm/release_campaign_spend.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/cm/release_campaign_spend.sh"
# shellcheck source=lib/cm/release_halt_after_spend.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/cm/release_halt_after_spend.sh"
# shellcheck source=lib/cm/release_origin_summary.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/cm/release_origin_summary.sh"
# shellcheck source=lib/cm/release_finalize.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/cm/release_finalize.sh"
# shellcheck source=lib/cm/release_decide_only.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/cm/release_decide_only.sh"

# --- Enable strict mode for our own code ---
set -euo pipefail

# Helper: run a git command and report the step name on failure.
# Usage: git_step "Step N description" git -C ... <args>
git_step() {
  local step_desc="$1"
  shift
  if ! "$@"; then
    echo "" >&2
    pgai_emit_error_contract \
      "cm-release.git-step.failed" \
      "git operation failed at step: ${step_desc} -- release-state.md has NOT been modified." \
      "Inspect the git state (git status / git log) in the repo, then re-run $(basename "$0") after fixing the underlying git problem, or revert any partial git changes manually first."
    # BLOCK-WORTHY: a git operation failing mid-release leaves the repo in a
    # state release-state.md does not yet describe -- the release must stop
    # rather than let a later step act on a checkout/merge/tag that never
    # actually completed.
    # CENSUS-ANCHOR: release-git-step-failure-01
    exit 1
  fi
}

# --- Preflight: project/config resolution, RC discovery/validation, checkout ---
# Runs the original Steps 1 through 4a (see release_preflight.sh for the
# full step-by-step doc). Sets PROJECT_NAME, MAIN_BRANCH, REPO_ROOT,
# _CM_PROJECT_ROOT, PROJECT_HOOKS_DIR, PROJECT_HALT_FILE, _CM_PUSH_TO_REMOTE,
# RELEASE_STATE, RC_BRANCH, ACTIVE_RC, STATE_ACTIVE_RC for the rest of this
# script, and registers the script's one EXIT trap.
# A --decide-only run enters decide-only mode first (release_decide_only.sh):
# narration moves to stderr and the stages below write nothing.
if [[ "$DECIDE_ONLY_ARG" == "true" ]]; then
  cm_release_decide_only_begin
fi
cm_release_preflight

# --- Step 4b: Pre-squash hook (if configured); HALT on failure. ---
# Kept inline in release.sh (not moved to release_ship_policy.sh, unlike
# Steps 4c-4f) so test_ship_rc_hooks.py's release.sh-file-content assertion
# (exactly 3 non-comment cm_resolve_and_enforce_hook call sites — one per
# phase) continues to hold. cm_release_apply_ship_policy (below) invokes
# this function by name between Step 4d and Step 4e, preserving the
# original execution order (4c, 4d, 4b, 4e, 4f) — HALT precedence is
# load-bearing there; see that function's own header doc.
#
# NOTE: run in a subshell — _run_release_hook calls exit 1 on failure.
_cm_run_pre_squash_hook_step() {
  echo "[Step 4b] Running pre-squash hook (if present)..."
  CM_RESOLVED_HOOK_PATH=""
  cm_resolve_and_enforce_hook "$PROJECT_NAME" "pre-squash" "$PROJECT_HOOKS_DIR" "$REPO_ROOT" "$RC_BRANCH" || {
    cm_halt \
      "cm-release.trigger-4.pre-squash-hook-not-executable" \
      "Trigger 4: the cm-release-pre-squash in-repo hook exists but is not executable, so the finalization mechanic cannot run and the squash cannot proceed safely." \
      "chmod +x the in-repo hook path printed above by cm_release_hooks.sh, commit the mode change, then re-run scripts/cm/release.sh."
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
      "Inspect the [hook cm-release-pre-squash]-prefixed lines printed above for the hook's own diagnostic output, fix the hook, then re-run scripts/cm/release.sh."
  fi
}

# --- Ship policy (Steps 4c-4f: NON-FUNCTIONAL-streak guard, TESTER lookup, ---
# --- ship-policy decision, pre-squash hook via callback, notes status    ---
# --- stamp, squash pollution guard). See release_ship_policy.sh for the  ---
# --- full step-by-step doc. Sets _SHIP_DECISION, _RELEASE_NOTES_STATUS,  ---
# --- _TESTER_* globals, _HAS_LARGE_FIX_EFFORT, _TESTER_TASK_FOUND.       ---
cm_release_apply_ship_policy "_cm_run_pre_squash_hook_step"

# --- Decide-only: a HALT already exited 3 inside the stage above; a ship ---
# --- decision is printed here and the run exits 0 before any write.    ---
if [[ "$DECIDE_ONLY_ARG" == "true" ]]; then
  cm_release_decide_only_report
fi

# --- Squash to main (Steps 5-7a): checkout, pull, squash merge, commit,  ---
# --- fidelity gate. See release_squash.sh. Sets MAIN_SHA and RC_SHA.     ---
cm_release_squash_to_main

# --- Release notes, CHANGELOG, README pointer, VERSION stamp (Steps 8,   ---
# --- 8b, 11b-11d). See release_notes.sh. Sets _cl_buf (EXIT-trap cleanup) ---
# --- and _CLEAN_RELEASE_VERSION (read by Step 13's pre-tag VERSION gate). ---
cm_release_generate_notes

# --- Pre-tag hook step: threaded into release_finalize.sh by name. ---
_cm_run_pre_tag_hook_step() {
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
  # Explicit return 0: called as a bare statement by cm_release_finalize, so
  # under `set -e` its exit status must not leak the `[[ ]] &&` cleanup
  # line's incidental false-test status (exempt at its own statement level,
  # not once it becomes this function's return value to a bare caller).
  return 0
}

# --- Post-tag hook step: threaded into release_finalize.sh by name. ---
_cm_run_post_tag_hook_step() {
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
  return 0  # see _cm_run_pre_tag_hook_step's comment above
}

# --- Finalize (Steps 12b-16c + summary): tag, RC teardown, release-state.md, ---
# --- promotion, auto-push, metrics, cleanup, Fixed-In stamp, then the       ---
# --- archive (16c, quality class: never blocks the release). See           ---
# --- release_finalize.sh (callbacks above keep hook calls in this file).    ---
cm_release_finalize "_cm_run_pre_tag_hook_step" "_cm_run_post_tag_hook_step"
