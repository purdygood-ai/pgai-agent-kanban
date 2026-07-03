#!/usr/bin/env bash
# team/scripts/lib/wake_claude_provider.sh
# Claude-specific provider functions for wake-claude.sh.
#
# Source this file in wake-claude.sh (after argument parsing, before sourcing
# wake_common.sh).  It defines:
#   - PROVIDER_NAME="claude"   — the provider identifier
#   - PROVIDER_CLI="claude"    — the CLI binary name
#   - provider_invoke_agent    — the CLI invocation function
#
# provider_invoke_agent is called by process_one_task (in scripts/wake/claude.sh) with:
#   $1  prompt          — the full task prompt string
#   $2  selected_model  — model alias/ID (empty string = use subagent default)
#   $3  model_source    — human-readable model source description (for logging)
#   $4  task_id         — the task ID (for logging and token merge)
#   $5  task_artifact_dir — artifacts directory path (for tokens.json)
#   $6  log_file        — path to the per-agent batch log file
#   $7  subagent        — the subagent role name (coder, writer, etc.)
#
# On return, PROVIDER_AGENT_EXIT_CODE is set to the CLI exit code.
#
# This file also sources token_capture.sh (Claude-specific token tracking).
#
# Provider-specific layer over wake_common.sh. Parallel file:
# lib/wake_codex_provider.sh.

# Provider identity constants
PROVIDER_NAME="claude"
PROVIDER_CLI="claude"

# Source token capture helper (Claude-specific)
# SCRIPT_DIR must be set by the calling wake script before sourcing this file.
# shellcheck source=scripts/lib/token_capture.sh
source "${SCRIPT_DIR}/../lib/token_capture.sh"

# Source temp helpers so pgai_temp_dir / pgai_mktemp are available.
# shellcheck source=scripts/lib/temp.sh
source "${SCRIPT_DIR}/../lib/temp.sh"

# ---------------------------------------------------------------------------
# provider_preflight
# Check that the provider CLI is available in PATH.
# Called by wake-claude.sh before wake_common_preflight.
# ---------------------------------------------------------------------------
provider_preflight() {
    command -v "$PROVIDER_CLI" >/dev/null 2>&1 || {
        # log() may not be available yet if this is called before wake_common.sh
        # is sourced; fall back to stderr.
        echo "ERROR: ${PROVIDER_CLI} CLI not found in PATH" >&2
        exit 1
    }
}

# ---------------------------------------------------------------------------
# provider_model_preflight <selected_model> <log_file>
#
# Validate the configured model before task processing begins.
#
# Claude provider behavior: the Claude CLI does not expose a cheap model
# listing or model-validation endpoint that can be used without a full API
# call. A full `claude -p "ok"` invocation is too expensive to run as a
# preflight (it would burn context and credits for no task output). Therefore
# this provider implements a no-op pass: the preflight always succeeds.
#
# If an invalid Claude model causes a task to fail mid-turn, the resulting
# BLOCKED task is operator-visible in the normal way. The cost here is lower
# than for Codex because the Claude CLI typically reports model errors
# synchronously with a clear error message before any significant work.
#
# Arguments:
#   $1  selected_model — resolved model string (may be empty; not used here)
#   $2  log_file       — path to the wake batch log file
#
# Returns:
#   0  — always; task processing proceeds normally
# ---------------------------------------------------------------------------
provider_model_preflight() {
    local selected_model="${1:-}"
    local log_file="${2:-/dev/stderr}"
    local _pf_model="${selected_model:-<subagent default>}"

    echo "[$(date -Iseconds)] wake(${AGENT:-wake}): model-preflight: claude provider — no-op pass (model validation not available cheaply; model='${_pf_model}')" | tee -a "$log_file"
    return 0
}

# ---------------------------------------------------------------------------
# provider_invoke_agent <prompt> <selected_model> <model_source>
#                       <task_id> <task_artifact_dir> <log_file> <subagent>
#
# Invokes the Claude CLI with the given prompt, captures token usage, and
# writes structured usage to tokens.json in the task artifacts directory.
#
# Sets PROVIDER_AGENT_EXIT_CODE to the CLI exit code on return.
# ---------------------------------------------------------------------------
provider_invoke_agent() {
  local prompt="$1"
  local selected_model="$2"
  local model_source="$3"
  local task_id="$4"
  local task_artifact_dir="$5"
  local log_file="$6"
  local subagent="$7"

  PROVIDER_AGENT_EXIT_CODE=0

  # Build claude invocation: --model flag MUST appear before -p and the prompt
  local claude_cmd
  claude_cmd=(claude)
  if [[ -n "${selected_model:-}" ]]; then
    echo "[$(date -Iseconds)] wake(${AGENT}): model: ${selected_model} (from ${model_source})" | tee -a "$log_file"
    claude_cmd+=(--model "${selected_model}")
  else
    echo "[$(date -Iseconds)] wake(${AGENT}): model: ${model_source}" | tee -a "$log_file"
  fi
  claude_cmd+=(--dangerously-skip-permissions -p "$prompt")

  # Add --output-format json for structured token usage capture.
  # NOTE: changes to this block cannot benefit the RC that introduces them.
  # The RC runs the pre-upgrade live install, so the new code is only effective
  # once upgrade.sh installs it into the live tree (i.e., in the RC after the
  # one that ships this change). This is expected and unavoidable.
  claude_cmd+=(--output-format json)

  # Temp file for the raw JSON output from claude.
  local _tc_raw_json _tc_result_text _tc_capture_dir
  _tc_capture_dir="$(pgai_temp_dir)/token_capture"
  mkdir -p "$_tc_capture_dir" 2>/dev/null || true
  _tc_raw_json="$(mktemp "${_tc_capture_dir}/raw_json.XXXXXX" 2>/dev/null || echo "")"
  _tc_result_text="$(mktemp "${_tc_capture_dir}/result_text.XXXXXX" 2>/dev/null || echo "")"

  local _tc_invocation_start
  _tc_invocation_start="$(date +%s)"

  set +e
  if [[ -n "$_tc_raw_json" && -n "$_tc_result_text" ]]; then
    # Debug: log claude_cmd contents before invocation
    {
      echo "[token_capture] claude_cmd array (${#claude_cmd[@]} elements):"
      local _tc_i
      for _tc_i in "${!claude_cmd[@]}"; do
        echo "  [$_tc_i]: ${claude_cmd[$_tc_i]}"
      done
    } >>"$log_file" 2>/dev/null || true

    # Stream separation: route stdout (JSON) to temp file, stderr to log.
    "${claude_cmd[@]}" </dev/null >"$_tc_raw_json" 2>>"$log_file"
    PROVIDER_AGENT_EXIT_CODE=$?

    # Extract agent text result from JSON and tee to log.
    local _tc_usage_record
    _tc_usage_record="$(token_capture_parse_result "$_tc_raw_json" "$_tc_result_text" "${selected_model:-}" 2>>"$log_file")"
    local _tc_parse_exit=$?
    if [[ -f "$_tc_result_text" ]]; then
      tee -a "$log_file" < "$_tc_result_text" >/dev/null
    fi

    # Compute elapsed seconds
    local _tc_elapsed
    _tc_elapsed=$(( $(date +%s) - _tc_invocation_start ))

    # Determine canonical model ID for tokens.json pricing lookups.
    local _tc_model _tc_canonical_model_id
    if command -v jq >/dev/null 2>&1; then
      _tc_canonical_model_id="$(printf '%s' "$_tc_usage_record" | jq -r '.canonical_model_id // ""' 2>/dev/null || echo "")"
    else
      _tc_canonical_model_id="$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(d.get('canonical_model_id',''))" "$_tc_usage_record" 2>/dev/null || echo "")"
    fi
    if [[ -n "$_tc_canonical_model_id" ]]; then
      _tc_model="$_tc_canonical_model_id"
    else
      local _tc_raw_model
      _tc_raw_model="${selected_model:-unknown}"
      _tc_model="$(token_canonicalize_model "$_tc_raw_model" 2>>"$log_file" || echo "$_tc_raw_model")"
      if [[ "$_tc_model" != "$_tc_raw_model" ]]; then
        echo "[$(date -Iseconds)] wake(${AGENT}): token capture: mapped model shortname '${_tc_raw_model}' -> '${_tc_model}'" | tee -a "$log_file"
      fi
    fi

    local _tc_provider="${PROVIDER_NAME}"

    # Determine rc_version for tokens.json
    local _tc_rc_version
    _tc_rc_version="$(get_release_state_field "Active RC" 2>/dev/null || echo "none")"
    if [[ -z "$_tc_rc_version" || "$_tc_rc_version" == "none" ]]; then
      _tc_rc_version="none"
    fi

    # Merge usage into task artifacts/tokens.json.
    # On parse failure, _tc_usage_record contains a captured:false sentinel
    # from token_capture_parse_result; pass it directly so merge writes an
    # explicit failure record rather than zeroed counts.
    local _tc_merge_exit=0
    if [[ $_tc_parse_exit -eq 0 ]]; then
      token_capture_merge_into_task_artifacts \
        "$_tc_usage_record" "$task_artifact_dir" \
        "$_tc_model" "$_tc_provider" "$subagent" "$_tc_elapsed" "$_tc_rc_version" \
        2>>"$log_file" || _tc_merge_exit=$?
    else
      echo "token capture: WARNING: parse_result failed (exit ${_tc_parse_exit}) — no usage captured for ${task_id}" \
        | tee -a "$log_file" >&2
      token_capture_merge_into_task_artifacts \
        "$_tc_usage_record" "$task_artifact_dir" \
        "$_tc_model" "$_tc_provider" "$subagent" "$_tc_elapsed" "$_tc_rc_version" \
        2>>"$log_file" || _tc_merge_exit=$?
    fi

    # Verify tokens.json landed and determine capture status.
    # A captured:false record means the agent exited without emitting usage data;
    # report INCOMPLETE so the operator can see the gap in cost accounting.
    if [[ -f "${task_artifact_dir}/tokens.json" ]]; then
      local _tc_was_captured
      if command -v jq >/dev/null 2>&1; then
        _tc_was_captured="$(jq -r '.captured // "true"' "${task_artifact_dir}/tokens.json" 2>/dev/null || echo "true")"
      else
        _tc_was_captured="$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(str(d.get('captured','true')).lower())" "${task_artifact_dir}/tokens.json" 2>/dev/null || echo "true")"
      fi
      if [[ "$_tc_was_captured" == "false" ]]; then
        echo "token capture status: INCOMPLETE (no usage emitted) — tokens.json written for ${task_id}" \
          | tee -a "$log_file" >&2
      else
        echo "token capture status: success — tokens.json written for ${task_id}" \
          | tee -a "$log_file" >&2
      fi
    else
      echo "token capture status: WARNING — tokens.json NOT found at ${task_artifact_dir}/tokens.json after merge (merge_exit=${_tc_merge_exit}, parse_exit=${_tc_parse_exit}) for ${task_id}" \
        | tee -a "$log_file" >&2
    fi

    rm -f "$_tc_raw_json" "$_tc_result_text" 2>/dev/null || true
  else
    # Temp file creation failed — fall back to plain invocation (no token capture)
    echo "token capture: WARNING: cannot create temp files under ${_tc_capture_dir} — token capture disabled for ${task_id}" \
      | tee -a "$log_file" >&2
    "${claude_cmd[@]}" </dev/null 2>&1 | tee -a "$log_file"
    PROVIDER_AGENT_EXIT_CODE=${PIPESTATUS[0]}
  fi
  set -e
}
