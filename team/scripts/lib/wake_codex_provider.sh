#!/usr/bin/env bash
# team/scripts/lib/wake_codex_provider.sh
# Codex-specific provider functions for wake-codex.sh.
#
# Source this file in wake-codex.sh (after argument parsing, before sourcing
# wake_common.sh).  It defines:
#   - PROVIDER_NAME="codex"   — the provider identifier
#   - PROVIDER_CLI="codex"    — the CLI binary name
#   - provider_invoke_agent   — the CLI invocation function
#
# provider_invoke_agent is called by process_one_task (in scripts/wake/codex.sh) with:
#   $1  prompt          — the full task prompt string
#   $2  selected_model  — model alias/ID (empty string = use config default)
#   $3  model_source    — human-readable model source description (for logging)
#   $4  task_id         — the task ID (for logging and token merge)
#   $5  task_artifact_dir — artifacts directory path (for tokens.json)
#   $6  log_file        — path to the per-agent batch log file
#   $7  subagent        — the subagent role name (coder, writer, etc.)
#
# On return, PROVIDER_AGENT_EXIT_CODE is set to the CLI exit code.
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
# - Agent loading: no --agent CLI flag. Role identity must be embedded in the
#   prompt itself. ~/.codex/AGENTS.md is the global instruction file.
# - stdout/stderr separation: clean in --json mode.
# - Authentication: OAuth via `codex login` (ChatGPT Plus) on this host.
#   OPENAI_API_KEY env var is NOT sufficient alone; credentials are in
#   ~/.codex/auth.json managed by `codex login`.

# Provider identity constants
PROVIDER_NAME="codex"
PROVIDER_CLI="codex"

# Source token capture helper (shared with Claude provider; Codex uses the
# flat-token schema path since it does not expose per-model cost breakdown).
# SCRIPT_DIR must be set by the calling wake script before sourcing this file.
# shellcheck source=scripts/lib/token_capture.sh
source "${SCRIPT_DIR}/../lib/token_capture.sh"

# Source temp helpers so pgai_temp_dir / pgai_mktemp are available.
# shellcheck source=scripts/lib/temp.sh
source "${SCRIPT_DIR}/../lib/temp.sh"

# ---------------------------------------------------------------------------
# provider_preflight
# Check that the provider CLI is available in PATH.
# Called by wake-codex.sh before wake_common_preflight.
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
# Validate that the configured Codex model is usable before any task is
# transitioned to WORKING. Called once per run_project_chain invocation, not
# once per task, so the overhead is bounded to a single lightweight invocation
# per wake.
#
# Strategy: run a minimal codex exec with the selected model and a trivial
# prompt ("ok"). Codex emits a JSONL event stream; if the first meaningful
# event is {"type":"error",...} the model is not usable. On success, the
# stream will include at least a turn.completed event.
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
# Side effects: none beyond log_file writes and a single short codex invocation.
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

    if [[ -n "$_pf_error_msg" ]]; then
        echo "[$(date -Iseconds)] wake(${AGENT:-wake}): model-preflight: FAIL — ${_pf_error_msg}" | tee -a "$log_file"
        echo "[$(date -Iseconds)] wake(${AGENT:-wake}): model-preflight: fix the model in ~/.codex/config.toml or set PGAI_${AGENT^^}_MODEL to a supported model, then retry" | tee -a "$log_file"
        return 1
    fi

    echo "[$(date -Iseconds)] wake(${AGENT:-wake}): model-preflight: PASS — model '${_pf_model}' accepted" | tee -a "$log_file"
    return 0
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
        echo "wake_codex_provider.sh: codex_parse_jsonl_result requires two arguments" >&2
        echo '{}'
        return 1
    fi

    if [[ ! -f "$jsonl_file" || ! -s "$jsonl_file" ]]; then
        echo "wake_codex_provider.sh: WARNING: jsonl_file '${jsonl_file}' missing or empty — no usage captured" >&2
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
    print(f"wake_codex_provider: cannot read jsonl_file: {e}", file=sys.stderr)
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
    print(f"wake_codex_provider: cannot write result_text_file: {e}", file=sys.stderr)

if not usage_found:
    print("wake_codex_provider: WARNING: no turn.completed event found in JSONL — usage will be 0", file=sys.stderr)

print(json.dumps(usage_record, separators=(',', ':')))
PY
    )" || parse_exit=$?

    if [[ $parse_exit -ne 0 ]]; then
        echo "wake_codex_provider.sh: WARNING: codex_parse_jsonl_result failed (exit ${parse_exit})" >&2
        echo '{"captured":false,"reason":"codex_parse_jsonl_result failed (parse error)"}'
        return 1
    fi

    echo "$usage_json"
    return 0
}

# ---------------------------------------------------------------------------
# provider_invoke_agent <prompt> <selected_model> <model_source>
#                       <task_id> <task_artifact_dir> <log_file> <subagent>
#
# Invokes the Codex CLI with the given prompt, captures token usage, and
# writes structured usage to tokens.json in the task artifacts directory.
#
# Sets PROVIDER_AGENT_EXIT_CODE to the CLI exit code on return.
#
# Invocation: codex exec --json --dangerously-bypass-approvals-and-sandbox <prompt>
# Optional:   -m <model> when selected_model is non-empty.
#
# Token capture uses the flat schema (no per-model cost breakdown)
# because Codex does not provide a modelUsage equivalent. Fields written:
#   model, provider, agent, rc_version, input_tokens, output_tokens,
#   cache_read_input_tokens, cache_creation_input_tokens (always 0),
#   invocations, elapsed_seconds, timestamp.
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

    # --json: emit JSONL event stream to stdout
    # --dangerously-bypass-approvals-and-sandbox: run non-interactively
    codex_cmd+=(--json --dangerously-bypass-approvals-and-sandbox)

    # Prompt is the last positional argument
    codex_cmd+=("$prompt")

    # Determine the Codex model for tokens.json.
    # Codex does not include the model in its JSON stream, so we read it
    # from config.toml when not specified on the command line.
    local _codex_model
    if [[ -n "${selected_model:-}" ]]; then
        _codex_model="$selected_model"
    else
        _codex_model="$(codex_read_model_from_config 2>>"$log_file" || echo "unknown")"
        if [[ -z "$_codex_model" || "$_codex_model" == "unknown" ]]; then
            echo "[$(date -Iseconds)] wake(${AGENT}): token capture: WARNING: could not read model from ~/.codex/config.toml; using 'unknown'" | tee -a "$log_file" >&2
        else
            echo "[$(date -Iseconds)] wake(${AGENT}): token capture: model from config: ${_codex_model}" | tee -a "$log_file"
        fi
    fi

    # Temp files for raw JSONL output and extracted agent text
    local _tc_raw_jsonl _tc_result_text _tc_capture_dir
    _tc_capture_dir="$(pgai_temp_dir)/token_capture"
    mkdir -p "$_tc_capture_dir" 2>/dev/null || true
    _tc_raw_jsonl="$(mktemp "${_tc_capture_dir}/codex_raw_jsonl.XXXXXX" 2>/dev/null || echo "")"
    _tc_result_text="$(mktemp "${_tc_capture_dir}/codex_result_text.XXXXXX" 2>/dev/null || echo "")"

    local _tc_invocation_start
    _tc_invocation_start="$(date +%s)"

    set +e
    if [[ -n "$_tc_raw_jsonl" && -n "$_tc_result_text" ]]; then
        # Debug: log codex_cmd contents before invocation
        {
            echo "[token_capture] codex_cmd array (${#codex_cmd[@]} elements):"
            local _tc_i
            for _tc_i in "${!codex_cmd[@]}"; do
                echo "  [${_tc_i}]: ${codex_cmd[$_tc_i]}"
            done
        } >>"$log_file" 2>/dev/null || true

        # Stream separation: route stdout (JSONL) to temp file, stderr to log.
        # This prevents stdout-stderr interleaving in the log.
        "${codex_cmd[@]}" </dev/null >"$_tc_raw_jsonl" 2>>"$log_file"
        PROVIDER_AGENT_EXIT_CODE=$?

        # Parse JSONL: extract agent text and usage record
        local _tc_usage_record
        _tc_usage_record="$(codex_parse_jsonl_result "$_tc_raw_jsonl" "$_tc_result_text" 2>>"$log_file")"
        local _tc_parse_exit=$?

        # Tee agent text to the batch log
        if [[ -f "$_tc_result_text" ]]; then
            tee -a "$log_file" < "$_tc_result_text" >/dev/null
        fi

        # Compute elapsed seconds
        local _tc_elapsed
        _tc_elapsed=$(( $(date +%s) - _tc_invocation_start ))

        local _tc_provider="${PROVIDER_NAME}"

        # Determine rc_version for tokens.json
        local _tc_rc_version
        _tc_rc_version="$(get_release_state_field "Active RC" 2>/dev/null || echo "none")"
        if [[ -z "$_tc_rc_version" || "$_tc_rc_version" == "none" ]]; then
            _tc_rc_version="none"
        fi

        # Merge usage into task artifacts/tokens.json using token_capture.sh
        # helper. Codex usage does not include total_cost_usd or modelUsage, so
        # the flat-schema path in token_capture_merge_into_task_artifacts fires.
        # On parse failure, _tc_usage_record contains a captured:false sentinel;
        # pass it directly so merge writes an explicit failure record.
        local _tc_merge_exit=0
        if [[ $_tc_parse_exit -eq 0 ]]; then
            token_capture_merge_into_task_artifacts \
                "$_tc_usage_record" "$task_artifact_dir" \
                "$_codex_model" "$_tc_provider" "$subagent" "$_tc_elapsed" "$_tc_rc_version" \
                2>>"$log_file" || _tc_merge_exit=$?
        else
            echo "token capture: WARNING: codex_parse_jsonl_result failed (exit ${_tc_parse_exit}) — no usage captured for ${task_id}" \
                | tee -a "$log_file" >&2
            token_capture_merge_into_task_artifacts \
                "$_tc_usage_record" "$task_artifact_dir" \
                "$_codex_model" "$_tc_provider" "$subagent" "$_tc_elapsed" "$_tc_rc_version" \
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

        rm -f "$_tc_raw_jsonl" "$_tc_result_text" 2>/dev/null || true
    else
        # Temp file creation failed — fall back to plain invocation (no token capture)
        echo "token capture: WARNING: cannot create temp files under ${_tc_capture_dir} — token capture disabled for ${task_id}" \
            | tee -a "$log_file" >&2
        "${codex_cmd[@]}" </dev/null 2>&1 | tee -a "$log_file"
        PROVIDER_AGENT_EXIT_CODE=${PIPESTATUS[0]}
    fi
    set -e
}
