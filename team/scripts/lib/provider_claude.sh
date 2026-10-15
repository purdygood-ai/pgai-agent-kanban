#!/usr/bin/env bash
# team/scripts/lib/provider_claude.sh
# Claude implementation of the five-function wake provider interface.
#
# Source this file in wake/claude.sh (after argument parsing, before sourcing
# wake_driver.sh / wake_common.sh). It defines the provider identity
# constants and exactly the five interface functions below — no pipeline
# logic. The shared pipeline lives in lib/wake_driver.sh and its sibling
# stage files (wake_driver_prep.sh, wake_driver_worktree.sh,
# wake_driver_invoke.sh, wake_driver_finalize.sh); those files call the four
# functions below and hold no Claude-specific code themselves.
#
# Provider Interface Contract
# ----------------------------
# Every wake provider lib (provider_claude.sh, and any future sibling such
# as provider_codex.sh) MUST define exactly these five functions and
# nothing pipeline-shaped:
#
#   provider_env_preflight()
#       Verify the provider CLI is on PATH. Exits the process with status 1
#       and a stderr message when missing. Called once at wake-script
#       startup, before wake_driver.sh / wake_common.sh is sourced (so it
#       must not depend on log() or any wake_common.sh state).
#
#   provider_model_preflight <selected_model> <log_file>
#       Validate the resolved model is usable before any task in this
#       wake's queue moves to WORKING. Returns 0 (proceed) or 1 (skip task
#       dispatch for this wake — the queue is left untouched). Writes its
#       verdict to <log_file> via `tee` so the operator sees it in both the
#       batch log and (via caller redirection) stdout/stderr.
#
#   provider_dispatch <prompt> <selected_model> <model_source>
#                      <selected_effort> <effort_source> <task_id>
#                      <log_file> <subagent> <raw_output_file>
#       Build and run the provider CLI invocation for one task turn.
#       When <raw_output_file> is non-empty, stdout is captured there and
#       stderr is appended to <log_file> (the token-capture path). When
#       <raw_output_file> is empty (the caller could not create a temp
#       file), stdout+stderr are merged and appended to <log_file> instead
#       (the no-token-capture fallback path) — both paths are provider-owned
#       because the CLI invocation shape differs only in output routing,
#       not in command construction. Sets PROVIDER_AGENT_EXIT_CODE (global)
#       to the CLI's exit status either way. Both paths background the CLI
#       invocation and record its pid via task_liveness_record
#       (wake_task_state.sh, best-effort — see that function's own header)
#       before waiting on it, so the wake reconciliation sweeps can later
#       answer "is <task_id>'s own agent still running" from that specific
#       pid instead of a `pgrep -f "$task_id"` command-line substring
#       search across every process on the host, which false-positives on
#       any unrelated process that merely mentions the task id.
#
#   provider_parse_output <raw_output_file> <result_text_file> [selected_model]
#       Parse the raw CLI output <raw_output_file> that provider_dispatch
#       captured. Writes the agent's plain-text response to
#       <result_text_file> and prints a compact usage JSON record
#       (input_tokens, output_tokens, cache_read_input_tokens,
#       cache_creation_input_tokens, and optionally canonical_model_id) on
#       stdout for the caller to merge into tokens.json. Returns 0 on
#       success; on parse failure returns 1 and still prints an explicit
#       {"captured":false,"reason":"..."} sentinel on stdout so the caller
#       always has valid JSON to merge.
#
#   provider_dispatch_refused <task_id>
#       Returns 0 when the most recent provider_dispatch for <task_id> was
#       refused by the provider CLI before any work began — an
#       infrastructure condition (the CLI cannot run the configured model
#       at all), never task progress — and 1 otherwise. Answered from a
#       per-task memo file provider_dispatch writes, not a shell variable,
#       because the dispatch runs inside the per-task watchdog's background
#       subshell while the finalize stage that asks lives in the parent
#       shell. The driver returns such a task to BACKLOG without touching
#       its zero-progress record and dispatches no further task this wake.
#
# Everything else — temp-file bookkeeping around dispatch/parse, elapsed-time
# measurement, rc_version resolution, calling
# token_capture_merge_into_task_artifacts, and the per-task hard-timeout
# watchdog — is provider-neutral and lives in wake_driver_invoke.sh's
# _wd_run_provider_and_capture_tokens. None of that belongs here: it was
# duplicated near-verbatim between this file's predecessor
# (wake_claude_provider.sh) and wake_codex_provider.sh before this
# decomposition, and centralizing it is the point of the shared driver.

# Provider identity constants
PROVIDER_NAME="claude"
PROVIDER_CLI="claude"

# The reasoning-effort levels `claude --effort` accepts. config_loader.sh
# reads this one-line declaration from this file at config load (without
# sourcing it) and refuses an llm_effort / PGAI_<ROLE>_EFFORT value the
# active provider does not list, so keep it a single literal assignment of
# space-separated values.
PROVIDER_EFFORT_VALUES="low medium high xhigh max"

# Source token capture helper (Claude-specific parse logic lives in
# token_capture_parse_result, which provider_parse_output wraps below).
# SCRIPT_DIR must be set by the calling wake script before sourcing this file.
# shellcheck source=scripts/lib/token_capture.sh
source "${SCRIPT_DIR}/../lib/token_capture.sh"

# Source temp helpers so pgai_temp_dir / pgai_mktemp are available.
# shellcheck source=scripts/lib/temp.sh
source "${SCRIPT_DIR}/../lib/temp.sh"

# Source the dual-channel emitter and the semver helpers this file's own
# refusal sites use (pgai_emit_error_contract; semver_lt — version order is
# never compared inline).
# shellcheck source=scripts/lib/error_contract.sh
source "${SCRIPT_DIR}/../lib/error_contract.sh"
# shellcheck source=scripts/lib/semver.sh
source "${SCRIPT_DIR}/../lib/semver.sh"

# ---------------------------------------------------------------------------
# claude_cli_version
# Prints the installed Claude CLI's version (the first X.Y.Z that
# `claude --version` reports), or nothing when the CLI cannot report one.
# Always returns 0: an unreadable version reads as "unknown" to the callers
# below rather than failing the wake on its own.
# ---------------------------------------------------------------------------
claude_cli_version() {
    local _v
    _v="$("$PROVIDER_CLI" --version 2>/dev/null </dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1 || true)"
    printf '%s' "$_v"
    return 0
}

# ---------------------------------------------------------------------------
# claude_cli_too_old_for_model <text>
#
# The CLI-too-old-for-model signature, named once. When <text> — one
# dispatch's provider output — carries it, prints "<installed> <required>"
# (each "unknown" when the text did not name it) and returns 0; returns 1
# and prints nothing otherwise. Either line of the pair the CLI prints
# qualifies on its own:
#
#   [claude-code:unrecognized_model] {"model":"...","query_source":"sdk"}
#   API Error: 400 Claude Code 2.1.276 does not support this model;
#   version 2.1.280 or newer is required.
#
# A transient provider error (5xx, overloaded, rate limit), a safeguard
# refusal, or any other 400 never matches — those keep their own handling.
# ---------------------------------------------------------------------------
claude_cli_too_old_for_model() {
    local _text="${1:-}"
    [[ -n "$_text" ]] || return 1
    if ! grep -qiE 'unrecognized_model|does[[:space:]]+not[[:space:]]+support[[:space:]]+this[[:space:]]+model' <<< "$_text"; then
        return 1
    fi
    local _installed _required
    _installed="$(printf '%s' "$_text" | grep -oiE 'Claude Code[[:space:]]+[0-9]+\.[0-9]+\.[0-9]+' | head -n1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || true)"
    _required="$(printf '%s' "$_text" | grep -oiE 'version[[:space:]]+[0-9]+\.[0-9]+\.[0-9]+[[:space:]]+or[[:space:]]+newer' | head -n1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || true)"
    printf '%s %s' "${_installed:-unknown}" "${_required:-unknown}"
    return 0
}

# ---------------------------------------------------------------------------
# claude_required_cli_memo / claude_dispatch_refusal_memo <task_id>
#
# Paths of the two memo files a CLI-too-old refusal leaves under the
# framework temp root (temp.sh's pgai_temp_dir, never bare /tmp):
#   required_cli_version         — "<model><TAB><required-version>", read by
#                                  provider_model_preflight on the next wake
#                                  so the refusal moves ahead of dispatch
#   dispatch_refused.<task_id>   — the per-task flag provider_dispatch_refused
#                                  answers from; cleared at each dispatch
# ---------------------------------------------------------------------------
claude_required_cli_memo() {
    printf '%s/provider_claude/required_cli_version' "$(pgai_temp_dir)"
}

claude_dispatch_refusal_memo() {
    printf '%s/provider_claude/dispatch_refused.%s' "$(pgai_temp_dir)" "${1:-none}"
}

# ---------------------------------------------------------------------------
# claude_refuse_if_cli_too_old <task_id> <selected_model> <log_file>
#                               <log_offset> <raw_output_file>
#
# BLOCK-WORTHY (verification unable to run, SOP "Gates and Refusals"): the
# installed CLI cannot run the configured model at all, so no agent ran and
# dispatching again changes nothing until the CLI is updated. The stop is
# scoped to this wake's dispatching — no task state is written here; the
# driver returns the task to BACKLOG without spending its zero-progress
# budget (never BLOCKED, never Needs Human), and provider_model_preflight
# refuses ahead of dispatch on later wakes until `claude --version`
# satisfies the required version.
#
# Reads the output this one dispatch produced — the stderr appended to
# <log_file> past <log_offset> plus the stdout captured in
# <raw_output_file> (either stream may carry the signature) — and, when it
# carries the CLI-too-old-for-model signature, narrates the dual-channel
# refusal into <log_file> and stderr, leaves both memo files, and returns
# 0. Returns 1 silently for any other output.
# ---------------------------------------------------------------------------
claude_refuse_if_cli_too_old() {
    local task_id="${1:-}" selected_model="${2:-}" log_file="${3:-}"
    local log_offset="${4:-0}" raw_output_file="${5:-}"
    local _output=""
    if [[ -f "$log_file" ]]; then
        _output="$(tail -c +"$(( log_offset + 1 ))" "$log_file" 2>/dev/null || true)"
    fi
    if [[ -n "$raw_output_file" && -f "$raw_output_file" ]]; then
        _output="${_output}"$'\n'"$(cat "$raw_output_file" 2>/dev/null || true)"
    fi
    local _versions
    _versions="$(claude_cli_too_old_for_model "$_output")" || return 1
    local _installed="${_versions%% *}" _required="${_versions##* }"
    local _model="${selected_model:-<provider CLI default>}"

    local _memo_dir
    _memo_dir="$(dirname "$(claude_required_cli_memo)")"
    mkdir -p "$_memo_dir" 2>/dev/null || true
    printf '%s\t%s\n' "$selected_model" "$_required" > "$(claude_required_cli_memo)" 2>/dev/null || true
    : > "$(claude_dispatch_refusal_memo "$task_id")" 2>/dev/null || true

    echo "[$(date -Iseconds)] wake(${AGENT:-wake}): task ${task_id}: ${PROVIDER_CLI} CLI ${_installed} refused the dispatch before any work began — model '${_model}' needs CLI ${_required} or newer (infrastructure condition, not task progress)" | tee -a "$log_file" >&2
    local _refusal
    # CENSUS-ANCHOR: provider-claude-01
    _refusal="$(pgai_emit_error_contract \
        "provider-claude-cli-too-old-for-model" \
        "task ${task_id}: the installed Claude Code CLI ${_installed} does not support the configured model '${_model}'; version ${_required} or newer is required. The provider refused before any work began, so this dispatch is an infrastructure condition, not task progress: the task returns to BACKLOG with its zero-progress count unchanged, and no further task is dispatched this wake." \
        "claude update && claude --version" 2>&1)"
    printf '%s\n' "$_refusal" | tee -a "$log_file" >&2
    return 0
}

# ---------------------------------------------------------------------------
# provider_env_preflight
# Check that the provider CLI is available in PATH.
# Called by wake/claude.sh before wake_driver.sh / wake_common.sh are sourced.
# ---------------------------------------------------------------------------
provider_env_preflight() {
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
# Claude provider behavior: the Claude CLI exposes no cheap model listing or
# validation endpoint, and a full `claude -p "ok"` is too expensive to run
# as a preflight, so model support is verified on the first dispatch
# (claude_refuse_if_cli_too_old). What IS cheap is `claude --version`, and
# a previous dispatch's CLI-too-old refusal leaves the required version in
# the memo claude_required_cli_memo names. When that memo names the model
# this wake would dispatch with and the installed CLI is still below the
# required version, this preflight refuses dual-channel and returns 1 so
# the wake dispatches nothing (the queue is left untouched) instead of
# burning a 3-second failed dispatch per task; once the installed CLI
# satisfies it, the memo is cleared and the preflight passes.
#
# Arguments:
#   $1  selected_model — resolved model string (may be empty: CLI default)
#   $2  log_file       — path to the wake batch log file
#
# Returns:
#   0  — proceed; the installed CLI version is narrated to the log
#   1  — the installed CLI is known too old for this model; skip dispatch
#        for this wake. The dual-channel refusal has been written to
#        log_file.
# ---------------------------------------------------------------------------
provider_model_preflight() {
    local selected_model="${1:-}"
    local log_file="${2:-/dev/stderr}"
    local _pf_model="${selected_model:-<provider CLI default>}"
    local _pf_installed
    _pf_installed="$(claude_cli_version)"

    local _pf_memo _pf_memo_model="" _pf_memo_required=""
    _pf_memo="$(claude_required_cli_memo)"
    if [[ -f "$_pf_memo" ]]; then
        IFS=$'\t' read -r _pf_memo_model _pf_memo_required < "$_pf_memo" || true
    fi
    if [[ -f "$_pf_memo" && "$_pf_memo_model" == "$selected_model" && -n "$_pf_memo_required" && -n "$_pf_installed" ]]; then
        if semver_lt "$_pf_installed" "$_pf_memo_required"; then
            echo "[$(date -Iseconds)] wake(${AGENT:-wake}): model-preflight: FAIL — installed ${PROVIDER_CLI} CLI ${_pf_installed} is below the ${_pf_memo_required} a previous dispatch reported as required for model '${_pf_model}'; not dispatching this wake" | tee -a "$log_file"
            # BLOCK-WORTHY (verification unable to run): the same condition
            # claude_refuse_if_cli_too_old classifies, caught before dispatch.
            # CENSUS-ANCHOR: provider-claude-02
            pgai_emit_error_contract \
                "provider-claude-cli-too-old-for-model" \
                "the installed Claude Code CLI ${_pf_installed} does not support the configured model '${_pf_model}'; version ${_pf_memo_required} or newer is required (reported by the provider on an earlier dispatch). No task is dispatched this wake; the queue is untouched." \
                "claude update && claude --version" 2>&1 | tee -a "$log_file" >&2
            return 1
        fi
        rm -f "$_pf_memo" 2>/dev/null || true
        echo "[$(date -Iseconds)] wake(${AGENT:-wake}): model-preflight: installed ${PROVIDER_CLI} CLI ${_pf_installed} now satisfies the ${_pf_memo_required} required for model '${_pf_model}'; memo cleared" | tee -a "$log_file"
    fi

    echo "[$(date -Iseconds)] wake(${AGENT:-wake}): model-preflight: claude provider — installed CLI ${_pf_installed:-unknown}; support for model '${_pf_model}' is verified on first dispatch (no cheap validation endpoint)" | tee -a "$log_file"
    return 0
}

# ---------------------------------------------------------------------------
# provider_dispatch <prompt> <selected_model> <model_source> <selected_effort>
#                    <effort_source> <task_id> <log_file> <subagent>
#                    <raw_output_file>
#
# Build the Claude CLI invocation and run it. See the interface contract
# above for the two output-routing modes selected by <raw_output_file>.
#
# Sets PROVIDER_AGENT_EXIT_CODE to the CLI exit code on return.
# ---------------------------------------------------------------------------
provider_dispatch() {
  local prompt="$1"
  local selected_model="$2"
  local model_source="$3"
  local selected_effort="$4"
  local effort_source="$5"
  local task_id="$6"
  local log_file="$7"
  local subagent="$8"
  local raw_output_file="$9"

  PROVIDER_AGENT_EXIT_CODE=0
  # Each dispatch starts with no refusal on record for this task — see
  # provider_dispatch_refused for the memo this clears.
  rm -f "$(claude_dispatch_refusal_memo "$task_id")" 2>/dev/null || true

  # Build claude invocation: --model flag MUST appear before -p and the prompt
  local claude_cmd
  claude_cmd=(claude)
  if [[ -n "${selected_model:-}" ]]; then
    echo "[$(date -Iseconds)] wake(${AGENT}): model: ${selected_model} (from ${model_source})" | tee -a "$log_file"
    claude_cmd+=(--model "${selected_model}")
  else
    echo "[$(date -Iseconds)] wake(${AGENT}): model: ${model_source}" | tee -a "$log_file"
  fi

  # --effort flag: appended only when a non-empty reasoning-effort value was
  # resolved (config_loader.sh's resolve_role_effort — [providers] llm_effort
  # cfg or PGAI_<ROLE>_EFFORT env). Absent effort appends no flag at all,
  # preserving today's assembled command byte-identically. Positioned
  # immediately after --model, consistent with the --model flag's ordering
  # before -p and the prompt.
  if [[ -n "${selected_effort:-}" ]]; then
    echo "[$(date -Iseconds)] wake(${AGENT}): effort: ${selected_effort} (from ${effort_source})" | tee -a "$log_file"
    claude_cmd+=(--effort "${selected_effort}")
  fi
  # Read llm_thinking_enabled from kanban.cfg via the standard config loader.
  # Absent key or value "true" → do nothing (default-true, back-compat).
  # Value "false" → append --thinking disabled to tell the Claude CLI to
  #   suppress extended-thinking blocks (required on proxies that reject them).
  # Any other value → fail loud, naming the key, so the operator can correct it.
  local _llm_thinking_enabled
  _llm_thinking_enabled="$(config_get providers llm_thinking_enabled "true")"
  if [[ "${_llm_thinking_enabled}" == "false" ]]; then
    claude_cmd+=(--thinking disabled)
  elif [[ "${_llm_thinking_enabled}" != "true" ]]; then
    echo "ERROR: wake_claude_provider: kanban.cfg [providers] llm_thinking_enabled has invalid value '${_llm_thinking_enabled}'; expected 'true' or 'false'" >&2
    return 1
  fi

  claude_cmd+=(--dangerously-skip-permissions -p "$prompt")

  # Add --output-format json for structured token usage capture.
  # NOTE: changes to this block cannot benefit the RC that introduces them.
  # The RC runs the pre-upgrade live install, so the new code is only effective
  # once upgrade.sh installs it into the live tree (i.e., in the RC after the
  # one that ships this change). This is expected and unavoidable.
  claude_cmd+=(--output-format json)

  # Byte offset of log_file just before launch: everything appended past it
  # is this dispatch's own stderr, which claude_refuse_if_cli_too_old reads.
  local _pd_log_offset=0
  set +e
  if [[ -n "$raw_output_file" ]]; then
    # Debug: log claude_cmd contents before invocation
    {
      echo "[token_capture] claude_cmd array (${#claude_cmd[@]} elements):"
      local _tc_i
      for _tc_i in "${!claude_cmd[@]}"; do
        echo "  [$_tc_i]: ${claude_cmd[$_tc_i]}"
      done
    } >>"$log_file" 2>/dev/null || true

    # Stream separation: route stdout (JSON) to the raw-output file, stderr
    # to the log. Backgrounded (rather than run in the foreground) so this
    # invocation's own pid ($!) can be recorded as task_id's dispatch-
    # liveness record (task_liveness_record, wake_task_state.sh) before
    # waiting on it: the reconciliation sweeps later ask "is this task's
    # agent still running" by probing THIS pid specifically (kill -0),
    # never by `pgrep -f "$task_id"` substring-searching every process on
    # the host.
    _pd_log_offset="$(wc -c < "$log_file" 2>/dev/null || echo 0)"
    "${claude_cmd[@]}" </dev/null >"$raw_output_file" 2>>"$log_file" &
  else
    # No raw-output file (caller could not create temp files) — merge
    # stdout+stderr and append to the log directly. A live `cmd | tee`
    # pipeline would make $! name tee's pid rather than claude's, and the
    # dispatch-liveness record below must name the actual CLI process, so
    # this degraded path forgoes the previous live-tee-to-stdout narration
    # (the invocation is still fully visible in the log file, just not
    # streamed live) in exchange for a genuine, checkable dispatch pid.
    _pd_log_offset="$(wc -c < "$log_file" 2>/dev/null || echo 0)"
    "${claude_cmd[@]}" </dev/null >>"$log_file" 2>&1 &
  fi
  local _pd_dispatch_pid=$!
  # Best-effort: task_liveness_record may be undefined when this function
  # is exercised by a narrower test harness that sources provider_claude.sh
  # without wake_task_state.sh -- see that function's own header for the
  # conservative "undetermined" default a missing record degrades to.
  if declare -F task_liveness_record >/dev/null 2>&1; then
    task_liveness_record "$task_id" "$PROVIDER_NAME" "$_pd_dispatch_pid" "dispatch" || true
  fi
  wait "$_pd_dispatch_pid"
  PROVIDER_AGENT_EXIT_CODE=$?
  # A non-zero exit whose output says the CLI is too old for the model is an
  # infrastructure refusal, not a failed run: narrate it and leave the memos
  # provider_dispatch_refused / provider_model_preflight read.
  if [[ "$PROVIDER_AGENT_EXIT_CODE" -ne 0 ]]; then
    claude_refuse_if_cli_too_old "$task_id" "$selected_model" "$log_file" "$_pd_log_offset" "$raw_output_file" || true
  fi
  set -e
}

# ---------------------------------------------------------------------------
# provider_parse_output <raw_output_file> <result_text_file> [selected_model]
#
# Parse the Claude CLI's --output-format json response captured by
# provider_dispatch. Thin wrapper over token_capture_parse_result (which
# already implements the Claude JSON schema and canonical_model_id
# selection); kept as its own function so the call site is provider-neutral.
# ---------------------------------------------------------------------------
provider_parse_output() {
  local raw_output_file="$1"
  local result_text_file="$2"
  local selected_model="${3:-}"

  token_capture_parse_result "$raw_output_file" "$result_text_file" "$selected_model"
}

# ---------------------------------------------------------------------------
# provider_dispatch_refused <task_id>
#
# True (exit 0) when the most recent provider_dispatch for <task_id> was
# refused by the CLI before any work began — see the interface contract
# above and claude_refuse_if_cli_too_old, which leaves the per-task memo
# this reads (provider_dispatch clears it at the start of every dispatch,
# so a stale memo from an earlier run never answers for a later one).
# ---------------------------------------------------------------------------
provider_dispatch_refused() {
  local task_id="${1:-}"
  [[ -n "$task_id" && -f "$(claude_dispatch_refusal_memo "$task_id")" ]]
}
