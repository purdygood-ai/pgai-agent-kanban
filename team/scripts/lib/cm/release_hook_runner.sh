#!/usr/bin/env bash
# team/scripts/lib/cm/release_hook_runner.sh
#
# CM release hook runner: runs one project lifecycle hook (pre-squash,
# pre-tag, post-tag) for team/scripts/cm/release.sh, and tells a failure of
# the runner itself apart from a failure of the hook it runs. Sourced by
# release_finalize.sh, so every caller of that stage has it.
#
# Functions
# ---------
#   cm_release_hook_runner_check <hook-name>
#       Checks, before any hook starts, that the runner can start one: it
#       runs `pp_run_ops pgai_agent_kanban.lib.bounded_run --help` from
#       $REPO_ROOT, exactly as the hook is launched, which needs pp_run_ops'
#       PYTHONPATH and an importable bounded runner. Prints nothing. Returns
#       0 when the runner is ready; returns 1 and sets
#       _CM_HOOK_RUNNER_FAULT_MESSAGE and _CM_HOOK_RUNNER_FAULT_REMEDY
#       (naming the runner, never the hook) otherwise. release.sh's pre-squash step calls it before its trigger 4
#       HALT can blame the hook.
#   _run_release_hook <hook-name> <hook-path> [--no-block|--inbox]
#       Runs the hook; see its own header below.
#
# Dependencies (must be sourced by the caller before either function runs)
# -------------------------------------------------------------------------
#   team/scripts/lib/pp_run_ops.sh — pp_run_ops.
#   team/scripts/lib/error_contract.sh — pgai_emit_error_contract.
#   team/scripts/lib/cm/release_stop_inbox.sh (through release_preflight.sh)
#     — cm_release_stop_inbox_entry, for --inbox only.
#   The release globals REPO_ROOT, ACTIVE_RC, PROJECT_NAME, _CM_PROJECT_ROOT,
#   RC_BRANCH and KANBAN_ROOT, read when a hook runs.
#   CM_HOOK_TIMEOUT_SECONDS, the hook's limit; unset or blank takes the
#   default from config_loader.sh's config_release_hook_timeout_seconds_default,
#   sourced from this tree when the caller has not.

# Guard against double-sourcing.
if declare -F _run_release_hook >/dev/null 2>&1; then
    return 0
fi

# This tree's scripts root, resolved once, now, while BASH_SOURCE[0] still
# names this file relative to the directory it was opened from. The runner
# fault remedy names this tree's pp_run_ops.sh through it.
_CM_HOOK_RUNNER_SCRIPTS_ROOT="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"

cm_release_hook_runner_check() {
  local hook_name="$1" _out="" _reason
  _CM_HOOK_RUNNER_FAULT_MESSAGE="" _CM_HOOK_RUNNER_FAULT_REMEDY=""
  # From the hook's own cwd, as at launch: `python3 -m` puts the cwd on sys.path.
  if _out="$(cd "${REPO_ROOT:-.}" && pp_run_ops pgai_agent_kanban.lib.bounded_run --help 2>&1 >/dev/null)"; then
    return 0
  fi
  # The last non-blank diagnostic line names the cause (a traceback ends in it).
  _reason="$(printf '%s\n' "$_out" | sed '/^[[:space:]]*$/d' | tail -n 1)"
  _CM_HOOK_RUNNER_FAULT_MESSAGE="The release hook runner could not start the ${hook_name} hook (${_reason:-no diagnostic output}); the hook did not run and is not at fault."
  _CM_HOOK_RUNNER_FAULT_REMEDY="Repair the kanban install until this check prints 'hook runner ready': source $(printf '%q' "${_CM_HOOK_RUNNER_SCRIPTS_ROOT}/lib/pp_run_ops.sh") && pp_run_ops pgai_agent_kanban.lib.bounded_run --help >/dev/null && echo 'hook runner ready' — then re-run scripts/cm/release.sh."
  return 1
}

# Helper: run a lifecycle hook for this release.
# Usage: _run_release_hook <hook-name> <hook-path> [--no-block|--inbox]
#
# hook-name  : identifier used in log prefixes (e.g. "cm-release-pre-squash")
# hook-path  : absolute path to the hook script
# --no-block : if set, hook failure is a logged warning; release continues
# --inbox    : a blocking failure also writes its triple to the operator inbox
#              (pre-tag; pre-squash's trigger 4 HALT writes its own entry)
#
# Before the hook starts, cm_release_hook_runner_check confirms the runner can
# start it. When it cannot, the hook is not run and the failure names the
# runner (error key cm-release.hook-runner-failure.<hook-name>), never the hook.
#
# Hook runs with cwd=$REPO_ROOT, six PGAI_* env vars and the ambient env, as the leader of its own
# process group (pgai_agent_kanban.lib.bounded_run): past $CM_HOOK_TIMEOUT_SECONDS ([release]
# hook_timeout_seconds) the whole group gets SIGTERM, then SIGKILL after 5s, and rc=124. Output is
# prefixed "[hook <name>]". Return: 0 = success or benign skip; non-zero = blocking failure (unless --no-block).
_run_release_hook() {
  local hook_name="$1"
  local hook_path="$2"
  local no_block="${3:-}"

  if [[ ! -f "$hook_path" ]]; then
    echo "[cm-release] hook ${hook_name}: not present, skipping"
    return 0
  fi
  if [[ ! -x "$hook_path" ]]; then
    echo "[cm-release] WARNING: hook ${hook_name} exists but is not executable, skipping" >&2
    return 0
  fi

  if ! cm_release_hook_runner_check "$hook_name"; then
    if [[ "$no_block" == "--no-block" ]]; then
      echo "[cm-release] WARNING: ${_CM_HOOK_RUNNER_FAULT_MESSAGE} Continuing per --no-block." >&2
      return 0
    fi
    echo "[cm-release] ERROR: ${hook_name}: FAIL — the release hook runner could not start the hook" >&2
    echo "[cm-release] ERROR: Cause: the hook runner (pp_run_ops and pgai_agent_kanban.lib.bounded_run), not the project hook, which did not run." >&2
    local _runner_err_key="cm-release.hook-runner-failure.${hook_name}"
    pgai_emit_error_contract "$_runner_err_key" "$_CM_HOOK_RUNNER_FAULT_MESSAGE" "$_CM_HOOK_RUNNER_FAULT_REMEDY"
    [[ "$no_block" == "--inbox" ]] && cm_release_stop_inbox_entry "$_runner_err_key" "$_CM_HOOK_RUNNER_FAULT_MESSAGE" "$_CM_HOOK_RUNNER_FAULT_REMEDY" no
    # BLOCK-WORTHY: a blocking hook that cannot be run cannot pass; the
    # release stops before the phase it guards, naming the runner.
    # CENSUS-ANCHOR: release-hook-runner-start-failure-01
    exit 1
  fi

  # The limit: the caller's $CM_HOOK_TIMEOUT_SECONDS, else the configured default.
  local _hook_timeout="${CM_HOOK_TIMEOUT_SECONDS:-}"
  if [[ -z "$_hook_timeout" ]]; then
    # shellcheck source=../config_loader.sh
    declare -F config_release_hook_timeout_seconds_default >/dev/null \
      || source "${_CM_HOOK_RUNNER_SCRIPTS_ROOT}/lib/config_loader.sh"
    _hook_timeout="$(config_release_hook_timeout_seconds_default)" || true
  fi

  echo "[cm-release] running hook ${hook_name}..."
  local _hook_rc=0 _hook_env=(env -u PYTHONPATH ${PYTHONPATH+"PYTHONPATH=$PYTHONPATH"})  # the caller's PYTHONPATH, not pp_run_ops'
  (
    cd "$REPO_ROOT"
    PGAI_TARGET_VERSION="$ACTIVE_RC" \
    PGAI_PROJECT_NAME="$PROJECT_NAME" \
    PGAI_PROJECT_ROOT="$_CM_PROJECT_ROOT" \
    PGAI_DEV_TREE_PATH="$REPO_ROOT" \
    PGAI_RC_BRANCH="$RC_BRANCH" \
    PGAI_KANBAN_ROOT="$KANBAN_ROOT" \
    pp_run_ops pgai_agent_kanban.lib.bounded_run --timeout-seconds "${_hook_timeout}" -- "${_hook_env[@]}" bash -c '"$0"' "$hook_path" 2>&1 | sed "s/^/[hook ${hook_name}] /"
    exit "${PIPESTATUS[0]}"
  ) || _hook_rc=$?
  (( _hook_rc == 124 )) && echo "[cm-release] WARNING: hook ${hook_name} did not finish within ${_hook_timeout}s ([release] hook_timeout_seconds); its process group was stopped" >&2

  if [[ $_hook_rc -ne 0 ]]; then
    if [[ "$no_block" == "--no-block" ]]; then
      echo "[cm-release] WARNING: hook ${hook_name} failed (rc=$_hook_rc), continuing per --no-block" >&2
      return 0
    fi
    # Non-deterministic (exempt): the hook is project-supplied, so its failure
    # cause lives inside the hook's own logic, not in anything release.sh can
    # inspect. Per docs/coding-standards.md "The non-deterministic exemption",
    # this failure is exempt from a numbered fix-command block but is NEVER
    # exempt from naming the next diagnostic step — the "[hook <name>]"
    # lines already printed above are that step.
    echo "[cm-release] ERROR: ${hook_name}: FAIL — hook exited non-zero (rc=$_hook_rc)" >&2
    echo "[cm-release] ERROR:" >&2
    echo "[cm-release] ERROR: Cause(s):" >&2
    echo "[cm-release] ERROR:   - ${hook_path} — project-supplied hook returned rc=$_hook_rc" >&2
    echo "[cm-release] ERROR:" >&2
    echo "[cm-release] ERROR: Next diagnostic step: read the \"[hook ${hook_name}]\"-prefixed lines printed above for this hook's own stdout/stderr — that output names the specific failure inside the hook. This gate cannot prescribe a fix command because the hook's internal logic is project-supplied; its failure cause is not knowable from release.sh." >&2
    echo "[cm-release] ERROR:" >&2
    echo "[cm-release] ERROR: Rule: a blocking hook (${hook_name}) must exit 0 for the release to proceed past this phase." >&2
    # Dual-channel error contract (team/scripts/lib/error_contract.sh), in
    # addition to the Cause(s)/Next-diagnostic-step/Rule block above: the
    # error key below is the hook's own phase name, the documented id an
    # operator or tool would grep for.
    local _hook_err_key="cm-release.hook-failure.${hook_name}"
    local _hook_err_msg="The ${hook_name} release hook exited non-zero (rc=${_hook_rc}); a blocking release hook must exit 0 for the release to proceed past this phase."
    local _hook_err_remedy="Read the \"[hook ${hook_name}]\"-prefixed lines printed above for the hook's own stdout/stderr, fix the underlying cause inside ${hook_path}, then re-run scripts/cm/release.sh."
    pgai_emit_error_contract "$_hook_err_key" "$_hook_err_msg" "$_hook_err_remedy"
    [[ "$no_block" == "--inbox" ]] && cm_release_stop_inbox_entry "$_hook_err_key" "$_hook_err_msg" "$_hook_err_remedy" no
    # BLOCK-WORTHY: a blocking release hook is an integrity invariant the
    # project declared; the release stops before the phase it guards.
    # CENSUS-ANCHOR: release-hook-runner-hook-failure-01
    exit 1
  fi
  echo "[cm-release] hook ${hook_name}: completed successfully"
  return 0
}
