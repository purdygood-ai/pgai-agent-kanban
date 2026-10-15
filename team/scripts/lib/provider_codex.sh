#!/usr/bin/env bash
# team/scripts/lib/provider_codex.sh
# Codex implementation of the five-function wake provider interface.
#
# Source this file in wake/codex.sh (after the active-provider fast-exit
# check, before sourcing wake_common.sh / wake_driver.sh). It defines the
# provider identity constants and exactly the five interface functions
# below — no pipeline logic. The shared pipeline lives in
# lib/wake_driver.sh and its sibling stage files (wake_driver_prep.sh,
# wake_driver_worktree.sh, wake_driver_invoke.sh, wake_driver_finalize.sh);
# those files call the five functions below and hold no Codex-specific code
# themselves. See lib/provider_claude.sh's header for the full interface
# contract this file implements the Codex side of.
#
# Folded out of the former wake_codex_provider.sh's single
# provider_invoke_agent function, which combined CLI invocation with the
# token-capture/merge boilerplate that now lives once in
# wake_driver_invoke.sh's _wd_run_provider_and_capture_tokens — that
# centralization is the point of this decomposition: the boilerplate was
# duplicated near-verbatim between this file's predecessor and
# provider_claude.sh's predecessor before it.
#
# Codex-only helpers kept here (used by provider_model_preflight and
# provider_parse_output, not part of the five-function interface):
#   codex_read_model_from_config   — read the configured model from
#                                     ~/.codex/config.toml when the caller
#                                     did not pin one via -m
#   codex_parse_jsonl_result       — parse the Codex JSONL event stream
#
# -------------------------------------------------------------------------
# Codex CLI interface notes
# -------------------------------------------------------------------------
# - Invocation: `codex exec --json --dangerously-bypass-approvals-and-sandbox`
#   NOT `codex -p`. The -p flag maps to --profile in Codex.
# - JSON output: JSONL event stream (one JSON object per line), NOT a single
#   JSON object. Relevant event types:
#     thread.started   — session start
#     turn.started     — turn start
#     item.completed   — agent message or tool result
#     turn.completed   — turn end; contains usage block
# - Token usage is in the turn.completed event's "usage" field:
#     { "input_tokens": N, "cached_input_tokens": N, "output_tokens": N }
#   NOTE: "input_tokens" includes cached tokens combined (not separate).
#   NOTE: "cached_input_tokens" maps to tokens.json "cache_read_input_tokens".
#   NOTE: "cache_creation_input_tokens" does NOT exist in Codex output — always 0.
# - Model identifier: NOT present in the JSONL stream. Must be read from
#   ~/.codex/config.toml (model = "...") or passed via -m/--model at invocation.
#   provider_parse_output resolves this into a "canonical_model_id" field on
#   its usage record when the caller did not pin a model, mirroring the
#   optional canonical_model_id field documented in provider_claude.sh.
# - Agent loading: no --agent CLI flag. Role identity must be embedded in the
#   prompt itself. ~/.codex/AGENTS.md is the global instruction file.
# - stdout/stderr separation: clean in --json mode.
# - Authentication: OAuth via `codex login` (ChatGPT Plus) on this host.
#   OPENAI_API_KEY env var is NOT sufficient alone; credentials are in
#   ~/.codex/auth.json managed by `codex login`.

# Provider identity constants
PROVIDER_NAME="codex"
PROVIDER_CLI="codex"

# The reasoning-effort levels codex's model_reasoning_effort accepts. config_loader.sh
# reads this one-line declaration from this file at config load (without
# sourcing it) and refuses an llm_effort / PGAI_<ROLE>_EFFORT value the
# active provider does not list, so keep it a single literal assignment of
# space-separated values.
PROVIDER_EFFORT_VALUES="minimal low medium high xhigh"

# Source token capture helper (shared with the Claude provider; Codex uses
# the flat-token schema path since it does not expose per-model cost
# breakdown). SCRIPT_DIR must be set by the calling wake script before
# sourcing this file.
# shellcheck source=scripts/lib/token_capture.sh
source "${SCRIPT_DIR}/../lib/token_capture.sh"

# Source temp helpers so pgai_temp_dir / pgai_mktemp are available.
# shellcheck source=scripts/lib/temp.sh
source "${SCRIPT_DIR}/../lib/temp.sh"

# ---------------------------------------------------------------------------
# provider_env_preflight
# Check that the provider CLI is available in PATH.
# Called by wake/codex.sh before wake_common.sh / wake_driver.sh are sourced.
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
# Validate that the configured Codex model is usable before any task is
# transitioned to WORKING. Called by run_project_chain at most once per
# project chain, and only when the queue holds a dispatchable task.
#
# Strategy: run a minimal codex exec with the selected model and a trivial
# prompt ("ok"). Codex emits a JSONL event stream; if the first meaningful
# event is {"type":"error",...} the model is not usable. On success, the
# stream will include at least a turn.completed event.
#
# That call is a real model turn, so:
#   - A PASS is remembered per model for MODEL_PREFLIGHT_TTL_SECONDS
#     (kanban.cfg [wake] model_preflight_ttl_seconds, default 3600) in the
#     memo codex_preflight_pass_memo names; inside the window the preflight
#     passes without calling codex. A FAIL is never remembered. 0 = no memo.
#   - The call is bounded by MODEL_PREFLIGHT_TIMEOUT_SECONDS ([wake]
#     model_preflight_timeout_seconds, default 60; SIGTERM, then SIGKILL 5s
#     later). It runs while the caller holds the project's repo lock, so a
#     hung CLI must not hold that lock; expiry is a FAIL. 0 = no bound.
#   - An error event that reports a usage or rate limit (codex_is_limit_error)
#     is a FAIL reported as a limit -- the model is fine, the account is
#     not -- never as a rejected model with advice to change the config.
#
# Arguments:
#   $1  selected_model — model string resolved by 3-tier selection (may be "")
#                        An empty string means "use codex config default"; we
#                        then read it from ~/.codex/config.toml for logging.
#   $2  log_file       — path to the wake batch log file
#
# Returns:
#   0 — model is usable; task processing may proceed
#   1 — model is NOT usable; task processing MUST be skipped for this wake
#       A clear, actionable operator message has been written to log_file.
#
# Side effects: log_file writes, at most one short codex invocation, and the
# PASS memo.
# ---------------------------------------------------------------------------
provider_model_preflight() {
    local selected_model="${1:-}"
    local log_file="${2:-/dev/stderr}"

    # Determine the model we will actually validate.
    local _pf_model
    if [[ -n "$selected_model" ]]; then
        _pf_model="$selected_model"
    else
        _pf_model="$(codex_read_model_from_config 2>>"$log_file" || echo "unknown")"
    fi

    local _pf_ttl="${MODEL_PREFLIGHT_TTL_SECONDS:-3600}"
    local _pf_timeout="${MODEL_PREFLIGHT_TIMEOUT_SECONDS:-60}"
    [[ "$_pf_ttl" =~ ^[0-9]+$ ]] || _pf_ttl=3600
    [[ "$_pf_timeout" =~ ^[0-9]+$ ]] || _pf_timeout=60
    local _pf_memo _pf_memo_age
    _pf_memo="$(codex_preflight_pass_memo "$_pf_model")"
    if (( _pf_ttl > 0 )) && [[ -f "$_pf_memo" ]]; then
        _pf_memo_age=$(( $(date +%s) - $(stat -c %Y "$_pf_memo" 2>/dev/null || echo 0) ))
        if (( _pf_memo_age >= 0 && _pf_memo_age < _pf_ttl )); then
            echo "[$(date -Iseconds)] wake(${AGENT:-wake}): model-preflight: PASS — model '${_pf_model}' accepted ${_pf_memo_age}s ago (remembered for ${_pf_ttl}s; no codex call)" | tee -a "$log_file"
            return 0
        fi
    fi

    echo "[$(date -Iseconds)] wake(${AGENT:-wake}): model-preflight: validating model '${_pf_model}' with ${PROVIDER_CLI}" | tee -a "$log_file"

    # Build a minimal codex invocation.
    # --json: emit JSONL event stream (error events are detectable)
    # --dangerously-bypass-approvals-and-sandbox: required for non-interactive run
    local _pf_cmd
    _pf_cmd=(codex exec)
    if [[ -n "$selected_model" ]]; then
        _pf_cmd+=(-m "$selected_model")
    fi
    _pf_cmd+=(--json --dangerously-bypass-approvals-and-sandbox "ok")
    if (( _pf_timeout > 0 )); then
        _pf_cmd=(timeout -k 5 "$_pf_timeout" "${_pf_cmd[@]}")
    fi

    # Write the JSONL output to a temp file so it can be passed to Python
    # without stdin-redirect conflicts (heredoc script vs. input data).
    local _pf_jsonl_file _pf_exit
    _pf_jsonl_file="$(mktemp "$(pgai_temp_dir)/pf_jsonl.XXXXXX" 2>/dev/null || echo "")"
    _pf_exit=0

    if [[ -n "$_pf_jsonl_file" ]]; then
        # Capture stdout (JSONL stream) to temp file; route stderr to log.
        set +e
        "${_pf_cmd[@]}" </dev/null >"$_pf_jsonl_file" 2>>"$log_file"
        _pf_exit=$?
        set -e
    else
        # Cannot create temp file — fall back to in-memory capture.
        local _pf_output
        set +e
        _pf_output="$("${_pf_cmd[@]}" </dev/null 2>>"$log_file")"
        _pf_exit=$?
        set -e
        # Write to a temp file via /dev/stdin pipe so the python path is uniform.
        _pf_jsonl_file="$(echo "$_pf_output" | python3 -c "
import sys, tempfile, pathlib
data = sys.stdin.buffer.read()
with tempfile.NamedTemporaryFile(delete=False, prefix='pf_jsonl_') as f:
    f.write(data)
    print(f.name)
" 2>/dev/null || echo "")"
    fi

    # Scan the JSONL stream for an error event. The error event has the form:
    #   {"type":"error","message":"..."}
    # We look for the first non-empty line and check its type field.
    # Pass the JSONL file path and metadata as arguments — no stdin conflict.
    local _pf_error_msg=""
    _pf_error_msg="$(python3 - "$_pf_model" "$_pf_exit" "${_pf_jsonl_file:-}" <<'PY'
import json, sys, pathlib

model = sys.argv[1]
cli_exit = int(sys.argv[2])
jsonl_path = sys.argv[3] if len(sys.argv) > 3 else ""

try:
    if jsonl_path:
        text = pathlib.Path(jsonl_path).read_text(encoding="utf-8")
    else:
        text = ""
    lines = text.splitlines()
except OSError:
    lines = []

for raw in lines:
    raw = raw.strip()
    if not raw:
        continue
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        continue
    if obj.get("type") == "error":
        msg = obj.get("message", "unknown error")
        print(f"model '{model}' rejected by codex: {msg}")
        sys.exit(0)
    # Any non-error event means the model was accepted.
    break

if cli_exit != 0 and not lines:
    # CLI exited non-zero with no output — codex itself may be broken.
    print(f"codex exited {cli_exit} with no output; check CLI installation")
    sys.exit(0)

# No error event found: model is usable.
sys.exit(1)
PY
    )" || true

    rm -f "${_pf_jsonl_file:-}" 2>/dev/null || true

    # timeout(1) exits 124 when it stopped the call, 137 when the SIGKILL
    # follow-up was needed.
    if (( _pf_timeout > 0 )) && [[ "$_pf_exit" == "124" || "$_pf_exit" == "137" ]]; then
        echo "[$(date -Iseconds)] wake(${AGENT:-wake}): model-preflight: FAIL — ${PROVIDER_CLI} did not answer within ${_pf_timeout}s (model '${_pf_model}'); stopped it, no task is dispatched this wake" | tee -a "$log_file"
        echo "[$(date -Iseconds)] wake(${AGENT:-wake}): model-preflight: check that '${PROVIDER_CLI} exec' runs non-interactively (network, codex login), or raise [wake] model_preflight_timeout_seconds" | tee -a "$log_file"
        return 1
    fi

    if [[ -n "$_pf_error_msg" ]] && codex_is_limit_error "$_pf_error_msg"; then
        echo "[$(date -Iseconds)] wake(${AGENT:-wake}): model-preflight: FAIL — ${PROVIDER_CLI} reported a usage or rate limit, not a model problem: ${_pf_error_msg#*rejected by codex: }" | tee -a "$log_file"
        echo "[$(date -Iseconds)] wake(${AGENT:-wake}): model-preflight: no task is dispatched this wake; the next firing retries once the limit resets (the model config needs no change)" | tee -a "$log_file"
        return 1
    fi

    if [[ -n "$_pf_error_msg" ]]; then
        echo "[$(date -Iseconds)] wake(${AGENT:-wake}): model-preflight: FAIL — ${_pf_error_msg}" | tee -a "$log_file"
        echo "[$(date -Iseconds)] wake(${AGENT:-wake}): model-preflight: fix the model in ~/.codex/config.toml or set PGAI_${AGENT^^}_MODEL to a supported model, then retry" | tee -a "$log_file"
        return 1
    fi

    echo "[$(date -Iseconds)] wake(${AGENT:-wake}): model-preflight: PASS — model '${_pf_model}' accepted" | tee -a "$log_file"
    if (( _pf_ttl > 0 )); then
        mkdir -p "$(dirname "$_pf_memo")" 2>/dev/null && : > "$_pf_memo" 2>/dev/null || true
    fi
    return 0
}

# ---------------------------------------------------------------------------
# codex_preflight_pass_memo <model>
#
# Path of the memo a model-preflight PASS leaves for <model>, under the
# framework temp root (temp.sh's pgai_temp_dir, never bare /tmp):
#   <temp root>/provider_codex/preflight_pass.<model>
# Its mtime is when the PASS happened; provider_model_preflight trusts it
# for MODEL_PREFLIGHT_TTL_SECONDS. Characters outside [A-Za-z0-9._-] in the
# model name are written as '_'.
# ---------------------------------------------------------------------------
codex_preflight_pass_memo() {
    local _key="${1:-unknown}"
    _key="${_key//[^A-Za-z0-9._-]/_}"
    printf '%s/provider_codex/preflight_pass.%s' "$(pgai_temp_dir)" "$_key"
}

# ---------------------------------------------------------------------------
# codex_is_limit_error <text>
#
# True when <text> -- a codex error event's message -- reports a usage,
# rate or quota limit (HTTP 429, "rate limit", "usage limit", "too many
# requests", "quota") rather than a problem with the model itself.
# ---------------------------------------------------------------------------
codex_is_limit_error() {
    grep -qiE 'rate[ _-]?limit|usage[ _-]?limit|too many requests|quota|(^|[^0-9])429([^0-9]|$)' <<< "${1:-}"
}

# ---------------------------------------------------------------------------
# codex_read_model_from_config
# Read the configured model from ~/.codex/config.toml.
# Echoes the model string (e.g. "gpt-5.3-codex") or "unknown" on failure.
# Never exits non-zero — always returns a usable string.
# ---------------------------------------------------------------------------
codex_read_model_from_config() {
    local config_file="${HOME}/.codex/config.toml"
    if [[ ! -f "$config_file" ]]; then
        echo "unknown"
        return 0
    fi
    # Parse model = "..." or model = '...' from TOML.
    # Uses python3 for correctness; avoids brittle regex on a real config format.
    local model_value
    model_value="$(python3 - "$config_file" <<'PY' 2>/dev/null
import sys, re, pathlib
try:
    text = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')
    m = re.search(r'^\s*model\s*=\s*["\']?([^"\'#\n\r]+)["\']?', text, re.M)
    print(m.group(1).strip() if m else 'unknown')
except Exception:
    print('unknown')
PY
    )" || model_value="unknown"
    echo "${model_value:-unknown}"
}

# ---------------------------------------------------------------------------
# codex_parse_jsonl_result <jsonl_file> <result_text_file>
#
# Parse the Codex JSONL event stream captured in <jsonl_file>.
# Writes the agent's text output to <result_text_file>.
# Prints a compact JSON usage record on stdout in the form:
#   {
#     "input_tokens": N,
#     "output_tokens": N,
#     "cache_read_input_tokens": N,     -- from Codex "cached_input_tokens"
#     "cache_creation_input_tokens": 0  -- always 0; not available in Codex
#   }
#
# Returns:
#   0  — success; caller may read <result_text_file> and consume stdout
#   1  — parse failure; warning emitted on stderr; empty record on stdout
#
# The tokens.json schema for Codex uses the flat-token schema because Codex
# does not provide per-model cost breakdown (no modelUsage equivalent).
# cache_creation_input_tokens is always 0 because Codex does not expose it.
# cache_read_input_tokens maps from Codex's "cached_input_tokens".
#
# Note: Codex's "input_tokens" field includes cached tokens in the total.
# For tokens.json consistency with the Claude schema, we store the raw
# Codex "input_tokens" value as input_tokens (it includes cache reads).
# ---------------------------------------------------------------------------
codex_parse_jsonl_result() {
    local jsonl_file="$1"
    local result_text_file="$2"

    if [[ -z "$jsonl_file" || -z "$result_text_file" ]]; then
        echo "provider_codex.sh: codex_parse_jsonl_result requires two arguments" >&2
        echo '{}'
        return 1
    fi

    if [[ ! -f "$jsonl_file" || ! -s "$jsonl_file" ]]; then
        echo "provider_codex.sh: WARNING: jsonl_file '${jsonl_file}' missing or empty — no usage captured" >&2
        printf '' > "$result_text_file" 2>/dev/null || true
        echo '{"captured":false,"reason":"agent exited without usage output (raw jsonl missing or empty)"}'
        return 1
    fi

    local parse_exit=0
    local usage_json
    usage_json="$(python3 - "$jsonl_file" "$result_text_file" <<'PY'
import json, sys, pathlib

jsonl_path   = pathlib.Path(sys.argv[1])
result_path  = pathlib.Path(sys.argv[2])

try:
    lines = jsonl_path.read_text(encoding='utf-8').splitlines()
except OSError as e:
    print(f"provider_codex: cannot read jsonl_file: {e}", file=sys.stderr)
    result_path.write_text('', encoding='utf-8')
    print(json.dumps({"captured": False, "reason": f"cannot read jsonl_file: {e}"}))
    sys.exit(1)

agent_text_parts = []
usage_record = {
    'input_tokens':                0,
    'output_tokens':               0,
    'cache_read_input_tokens':     0,
    'cache_creation_input_tokens': 0,   # always 0; Codex does not expose this field
}
usage_found = False

for raw_line in lines:
    raw_line = raw_line.strip()
    if not raw_line:
        continue
    try:
        obj = json.loads(raw_line)
    except json.JSONDecodeError:
        continue

    event_type = obj.get('type', '')

    # item.completed with type=agent_message => collect agent text
    if event_type == 'item.completed':
        item = obj.get('item', {})
        if isinstance(item, dict) and item.get('type') == 'agent_message':
            text = item.get('text', '')
            if isinstance(text, str) and text:
                agent_text_parts.append(text)

    # turn.completed => extract usage block
    elif event_type == 'turn.completed':
        u = obj.get('usage', {})
        if isinstance(u, dict):
            usage_found = True
            usage_record['input_tokens']            = int(u.get('input_tokens',         0))
            usage_record['output_tokens']           = int(u.get('output_tokens',        0))
            # Codex uses "cached_input_tokens"; map to cache_read_input_tokens
            # for tokens.json schema compatibility.
            usage_record['cache_read_input_tokens'] = int(u.get('cached_input_tokens',  0))
            # cache_creation_input_tokens is never present in Codex output.
            usage_record['cache_creation_input_tokens'] = 0

# Write agent text result
agent_text = '\n'.join(agent_text_parts)
try:
    result_path.write_text(agent_text, encoding='utf-8')
except OSError as e:
    print(f"provider_codex: cannot write result_text_file: {e}", file=sys.stderr)

if not usage_found:
    print("provider_codex: WARNING: no turn.completed event found in JSONL — usage will be 0", file=sys.stderr)

print(json.dumps(usage_record, separators=(',', ':')))
PY
    )" || parse_exit=$?

    if [[ $parse_exit -ne 0 ]]; then
        echo "provider_codex.sh: WARNING: codex_parse_jsonl_result failed (exit ${parse_exit})" >&2
        echo '{"captured":false,"reason":"codex_parse_jsonl_result failed (parse error)"}'
        return 1
    fi

    echo "$usage_json"
    return 0
}

# ---------------------------------------------------------------------------
# provider_dispatch <prompt> <selected_model> <model_source> <selected_effort>
#                    <effort_source> <task_id> <log_file> <subagent>
#                    <raw_output_file>
#
# Build the Codex CLI invocation and run it. See provider_claude.sh's
# interface contract for the two output-routing modes selected by
# <raw_output_file>.
#
# Invocation: codex exec --json --dangerously-bypass-approvals-and-sandbox <prompt>
# Optional:   -m <model> when selected_model is non-empty.
# Optional:   -c model_reasoning_effort=<value> when selected_effort is non-empty.
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

  # Build codex invocation
  # Usage: codex exec [OPTIONS] <PROMPT>
  local codex_cmd
  codex_cmd=(codex exec)

  if [[ -n "${selected_model:-}" ]]; then
    echo "[$(date -Iseconds)] wake(${AGENT}): model: ${selected_model} (from ${model_source})" | tee -a "$log_file"
    codex_cmd+=(-m "${selected_model}")
  else
    echo "[$(date -Iseconds)] wake(${AGENT}): model: ${model_source}" | tee -a "$log_file"
  fi

  # model_reasoning_effort config override: appended only when a non-empty
  # reasoning-effort value was resolved (config_loader.sh's
  # resolve_role_effort — [providers] llm_effort cfg or PGAI_<ROLE>_EFFORT
  # env). Absent effort appends no override at all, preserving today's
  # assembled command byte-identically. Codex has no dedicated --effort
  # flag, so the value is set via the CLI's `-c key=value` config-override
  # mechanism. Positioned immediately after -m, consistent with the model
  # flag's ordering before --json.
  if [[ -n "${selected_effort:-}" ]]; then
    echo "[$(date -Iseconds)] wake(${AGENT}): effort: ${selected_effort} (from ${effort_source})" | tee -a "$log_file"
    codex_cmd+=(-c "model_reasoning_effort=${selected_effort}")
  fi

  # --json: emit JSONL event stream to stdout
  # --dangerously-bypass-approvals-and-sandbox: run non-interactively
  codex_cmd+=(--json --dangerously-bypass-approvals-and-sandbox)

  # Prompt is the last positional argument
  codex_cmd+=("$prompt")

  set +e
  if [[ -n "$raw_output_file" ]]; then
    # Debug: log codex_cmd contents before invocation
    {
      echo "[token_capture] codex_cmd array (${#codex_cmd[@]} elements):"
      local _tc_i
      for _tc_i in "${!codex_cmd[@]}"; do
        echo "  [${_tc_i}]: ${codex_cmd[$_tc_i]}"
      done
    } >>"$log_file" 2>/dev/null || true

    # Stream separation: route stdout (JSONL) to the raw-output file, stderr
    # to the log. Backgrounded (rather than run in the foreground) so this
    # invocation's own pid ($!) can be recorded as task_id's dispatch-
    # liveness record (task_liveness_record, wake_task_state.sh) before
    # waiting on it: the reconciliation sweeps later ask "is this task's
    # agent still running" by probing THIS pid specifically (kill -0),
    # never by `pgrep -f "$task_id"` substring-searching every process on
    # the host. Recorded identically to provider_claude.sh's own
    # provider_dispatch -- same shared helper, same holder/pid/op/task
    # fields -- so both provider libs answer the same liveness question
    # the same way.
    "${codex_cmd[@]}" </dev/null >"$raw_output_file" 2>>"$log_file" &
  else
    # No raw-output file (caller could not create temp files) — merge
    # stdout+stderr and append to the log directly. A live `cmd | tee`
    # pipeline would make $! name tee's pid rather than codex's, and the
    # dispatch-liveness record below must name the actual CLI process, so
    # this degraded path forgoes the previous live-tee-to-stdout narration
    # (the invocation is still fully visible in the log file, just not
    # streamed live) in exchange for a genuine, checkable dispatch pid.
    "${codex_cmd[@]}" </dev/null >>"$log_file" 2>&1 &
  fi
  local _pd_dispatch_pid=$!
  # Best-effort: task_liveness_record may be undefined when this function
  # is exercised by a narrower test harness that sources provider_codex.sh
  # without wake_task_state.sh -- see that function's own header for the
  # conservative "undetermined" default a missing record degrades to.
  if declare -F task_liveness_record >/dev/null 2>&1; then
    task_liveness_record "$task_id" "$PROVIDER_NAME" "$_pd_dispatch_pid" "dispatch" || true
  fi
  wait "$_pd_dispatch_pid"
  PROVIDER_AGENT_EXIT_CODE=$?
  set -e
}

# ---------------------------------------------------------------------------
# provider_parse_output <raw_output_file> <result_text_file> [selected_model]
#
# Parse the Codex JSONL event stream captured by provider_dispatch. Thin
# wrapper over codex_parse_jsonl_result, with one addition: Codex never
# includes the model identifier in its JSONL stream, so when the caller did
# not pin a model (<selected_model> empty), this function resolves it from
# ~/.codex/config.toml and threads it through as "canonical_model_id" on the
# usage record — the same optional field provider_claude.sh documents,
# picked up by wake_driver_invoke.sh's _wd_run_provider_and_capture_tokens
# for tokens.json pricing lookups instead of falling back to "unknown".
# ---------------------------------------------------------------------------
provider_parse_output() {
  local raw_output_file="$1"
  local result_text_file="$2"
  local selected_model="${3:-}"

  local usage_json parse_exit=0
  usage_json="$(codex_parse_jsonl_result "$raw_output_file" "$result_text_file")"
  parse_exit=$?

  if [[ $parse_exit -eq 0 && -z "$selected_model" ]]; then
    local _codex_model
    _codex_model="$(codex_read_model_from_config 2>/dev/null || echo "unknown")"
    if [[ -n "$_codex_model" && "$_codex_model" != "unknown" ]]; then
      usage_json="$(python3 - "$usage_json" "$_codex_model" <<'PY'
import json, sys
record = json.loads(sys.argv[1])
record["canonical_model_id"] = sys.argv[2]
print(json.dumps(record, separators=(",", ":")))
PY
      )"
    fi
  fi

  echo "$usage_json"
  return $parse_exit
}

# ---------------------------------------------------------------------------
# provider_dispatch_refused <task_id>
#
# Codex side of the fifth interface function (see provider_claude.sh's
# header): no Codex output signature is classified as "the CLI refused the
# dispatch before any work began" today, so this always answers 1 and the
# driver's ordinary post-exit classification applies to every Codex run.
# ---------------------------------------------------------------------------
provider_dispatch_refused() {
  return 1
}
