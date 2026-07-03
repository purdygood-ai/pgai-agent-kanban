#!/usr/bin/env bash
# team/scripts/lib/token_capture.sh
# Token usage capture and aggregation helper for pgai-agent-kanban.
#
# Source this file to get the token_capture_* functions in your shell:
#   source "$(dirname "${BASH_SOURCE[0]}")/token_capture.sh"
#
# -------------------------------------------------------------------------
# HOW TOKEN DATA IS EXTRACTED FROM claude -p
# -------------------------------------------------------------------------
# Chosen approach: --output-format json (selected over alternatives below)
#
# Claude CLI (version 2.1+) supports --output-format <format> with -p.
# When format is "json", the CLI emits a single JSON object on stdout:
#   {
#     "type": "result",
#     "result": "<agent text output>",
#     "usage": {
#       "input_tokens": N,
#       "output_tokens": N,
#       "cache_creation_input_tokens": N,
#       "cache_read_input_tokens": N,
#       ...
#     },
#     "total_cost_usd": N,
#     "modelUsage": {
#       "claude-opus-4-8": {
#         "inputTokens": N,
#         "outputTokens": N,
#         "cacheReadInputTokens": N,
#         "cacheCreationInputTokens": N,
#         "costUSD": N
#       },
#       ...
#     },
#     ...
#   }
#
# This is a single-pass, structured signal — no regex parsing, no stderr
# scraping. The "result" field contains the exact text the agent produced,
# so switching to JSON mode does not lose any agent output (we extract
# and tee the result field separately).
#
# Alternatives considered and rejected:
#   --output-format stream-json  — multiple lines, more complex to parse;
#       the last line's "usage" block would work but stream-json requires
#       buffering the entire output before usage is available anyway.
#   --verbose / --show-usage    — not present in claude 2.1; no stable flag.
#   stderr parsing              — brittle; format undocumented and changes.
#   text output + regex         — would miss cache_creation_input_tokens and
#       other fields unless claude prints them explicitly (it does not in text mode).
#
# The approach: the caller (wake/claude.sh) passes --output-format json
# to the claude_cmd array. token_capture_parse_result extracts the text
# result (for logging) and the usage block (for tokens.json). If parsing
# fails for any reason, token_capture_merge_into_task_artifacts is called
# with an empty record and emits a single warning line — the wake chain
# continues unaffected.
#
# -------------------------------------------------------------------------
# SCHEMA: tokens.json (new, written by this version)
# -------------------------------------------------------------------------
# {
#   "provider":       "claude",
#   "agent":          "cm",
#   "rc_version":     "v0.24.3",
#   "invocations":    2,
#   "elapsed_seconds": 90,
#   "timestamp":      "2026-05-17T03:24:46Z",
#   "total_cost_usd": 0.027,
#   "model_usage": {
#     "claude-opus-4-8": {
#       "input_tokens": 12,
#       "output_tokens": 14,
#       "cache_creation_input_tokens": 0,
#       "cache_read_input_tokens": 52366,
#       "cost_usd": 0.026593
#     },
#     "claude-haiku-4-5-20251001": {
#       "input_tokens": 686,
#       "output_tokens": 26,
#       "cache_creation_input_tokens": 0,
#       "cache_read_input_tokens": 0,
#       "cost_usd": 0.000816
#     }
#   }
# }
#
# Fields removed from old schema:
#   model, input_tokens, output_tokens,
#   cache_creation_input_tokens, cache_read_input_tokens
#
# Legacy fallback: when the CLI response lacks total_cost_usd or modelUsage
# (older CLI builds), token_capture_parse_result sets total_cost_usd=null and
# model_usage=null in the usage record. The merge function detects null and
# writes the old schema (with model, input_tokens, etc.) so legacy environments
# do not crash and historical files remain readable.
#
# -------------------------------------------------------------------------
# FUNCTIONS
# -------------------------------------------------------------------------
#   token_canonicalize_model <model_name>
#       Maps a model shortname (opus, sonnet, haiku) to the canonical model ID
#       (claude-opus-4-8, claude-sonnet-4-6, claude-haiku-4-5-20251001).
#       If the input is already a canonical ID (contains a dash), it is echoed
#       unchanged. If the input is an unrecognised shortname, emits a loud
#       warning on stderr and returns 1 (non-zero exit). Callers that need a
#       hard failure should check the return code; callers in best-effort paths
#       should treat a non-zero return as "use the raw value and warn."
#
#   token_capture_parse_result <json_file> <result_text_file>
#       Reads the captured JSON from <json_file>, writes the agent's text
#       output to <result_text_file>, and prints a usage JSON record on
#       stdout. Returns 0 on success, 1 on parse failure (warning emitted).
#
#   token_capture_merge_into_task_artifacts <usage_record> <task_artifact_dir> \
#           <model> <provider> <agent> <elapsed_seconds> [<rc_version>]
#       Merges a usage record (JSON string) into <task_artifact_dir>/tokens.json.
#       If tokens.json does not exist, creates it. If it exists, sums numeric
#       token fields, increments invocations, accumulates elapsed_seconds, and
#       refreshes the timestamp. On any error, emits a single warning and returns
#       0 (never aborts the caller).
#
#       <rc_version> (optional, 7th arg): the RC version string to write as
#       rc_version in tokens.json (e.g. "v0.24.12"). Pass "none" when no RC is
#       in scope. If omitted, the field is left unchanged from an existing file
#       or set to "none" in a new file.
#
#       When the usage record contains total_cost_usd and model_usage (new CLI),
#       the new schema is written. When those fields are absent (old CLI), the
#       legacy schema (with top-level model/input_tokens/etc.) is written so
#       legacy environments continue to work unchanged.
#
# -------------------------------------------------------------------------
# DEPENDENCIES
# -------------------------------------------------------------------------
# Prefers jq for JSON merging (gated on `command -v jq`).
# Falls back to a small inline python3 snippet if jq is absent.
# python3 is assumed available (it is used throughout wake/claude.sh).
# -------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# token_canonicalize_model <model_name>
#
# Maps a model shortname to its canonical model ID.
#
# Recognised shortnames and their canonical targets:
#   opus   -> claude-opus-4-8
#   sonnet -> claude-sonnet-4-6
#   haiku  -> claude-haiku-4-5-20251001
#
# Canonical IDs (those containing a dash) are echoed unchanged.
#
# Any unrecognised shortname (a value that contains no dash and is not in the
# map above) causes a loud error message on stderr and a non-zero return code.
# Callers in best-effort paths should emit a warning and continue; hard-failure
# callers should propagate the non-zero exit.
#
# Returns:
#   0  — success; canonical ID on stdout
#   1  — unrecognised shortname; error on stderr; raw input on stdout
# ---------------------------------------------------------------------------
token_canonicalize_model() {
    local model="$1"

    if [[ -z "$model" ]]; then
        echo "token_capture.sh: token_canonicalize_model: model argument is empty" >&2
        echo ""
        return 1
    fi

    # If the model string already contains a dash it is assumed to be a
    # canonical ID (e.g. "claude-opus-4-7") — pass through unchanged.
    if [[ "$model" == *-* ]]; then
        echo "$model"
        return 0
    fi

    # Shortname map
    case "$model" in
        opus)
            echo "claude-opus-4-8"
            return 0
            ;;
        sonnet)
            echo "claude-sonnet-4-6"
            return 0
            ;;
        haiku)
            echo "claude-haiku-4-5-20251001"
            return 0
            ;;
        *)
            echo "token_capture.sh: ERROR: unrecognised model shortname '${model}' — cannot canonicalize. Known shortnames: opus, sonnet, haiku. Add to token_canonicalize_model() if this is a new model." >&2
            echo "$model"
            return 1
            ;;
    esac
}

# ---------------------------------------------------------------------------
# token_capture_parse_result <json_file> <result_text_file> [<selected_model>]
#
# Parse the claude --output-format json output captured in <json_file>.
# Writes the agent's text ("result" field) to <result_text_file>.
# Prints a compact JSON usage record on stdout in the form:
#   {
#     "input_tokens": N,
#     "output_tokens": N,
#     "cache_creation_input_tokens": N,
#     "cache_read_input_tokens": N,
#     "canonical_model_id": "claude-opus-4-8",
#     "total_cost_usd": 0.013704500000000001,   -- null when absent (old CLI)
#     "model_usage": {                           -- null when absent (old CLI)
#       "claude-opus-4-8": {
#         "input_tokens": N,
#         "output_tokens": N,
#         "cache_creation_input_tokens": N,
#         "cache_read_input_tokens": N,
#         "cost_usd": 0.013296500000000001
#       },
#       ...
#     }
#   }
#
# The canonical_model_id field identifies the primary model for this task.
# Selection priority (when modelUsage is present and non-empty):
#   1. selected_model (the model the wake script requested via --model), if it
#      appears as a key in modelUsage — the requested model is by definition
#      the primary; ancillary models used internally by the CLI are the others.
#   2. The modelUsage key with the greatest total token volume (input+output),
#      i.e. the model that did the most work. Robust fallback when the requested
#      model is not echoed verbatim in modelUsage.
#   3. Empty string — caller falls back to selected_model or a short alias.
# If modelUsage is absent or unparseable, canonical_model_id is set to ""
# (empty string — caller should fall back to a short alias).
#
# total_cost_usd is the CLI's authoritative per-invocation cost. Captured
# verbatim (no rounding). Set to JSON null when the CLI response omits it.
#
# model_usage is the per-model breakdown with snake_case keys (transformed from
# the CLI's camelCase: inputTokens→input_tokens, outputTokens→output_tokens,
# cacheCreationInputTokens→cache_creation_input_tokens,
# cacheReadInputTokens→cache_read_input_tokens, costUSD→cost_usd). Missing
# optional token fields default to 0. Set to JSON null when modelUsage absent.
#
# Returns:
#   0  — success; caller may read <result_text_file> and consume stdout
#   1  — parse failure; warning emitted on stderr; empty record on stdout
# ---------------------------------------------------------------------------
token_capture_parse_result() {
    local json_file="$1"
    local result_text_file="$2"
    local selected_model="${3:-}"

    if [[ -z "$json_file" || -z "$result_text_file" ]]; then
        echo "token_capture.sh: token_capture_parse_result requires two arguments" >&2
        echo '{}'
        return 1
    fi

    if [[ ! -f "$json_file" || ! -s "$json_file" ]]; then
        echo "token_capture.sh: WARNING: json_file '${json_file}' missing or empty — no usage captured" >&2
        echo '{}' > "$result_text_file" 2>/dev/null || true
        echo '{"captured":false,"reason":"agent exited without usage output (raw json missing or empty)"}'
        return 1
    fi

    # Use python3 to parse — available everywhere claude is used.
    local parse_exit=0
    local usage_json
    usage_json="$(python3 - "$json_file" "$result_text_file" "${selected_model}" <<'PY'
import json, sys, pathlib

json_path = pathlib.Path(sys.argv[1])
result_path = pathlib.Path(sys.argv[2])
requested_model = sys.argv[3] if len(sys.argv) > 3 else ""

try:
    raw = json_path.read_text(encoding='utf-8').strip()
except OSError as e:
    print(f"token_capture: cannot read json_file: {e}", file=sys.stderr)
    result_path.write_text('', encoding='utf-8')
    print(json.dumps({"captured": False, "reason": f"cannot read json_file: {e}"}))
    sys.exit(1)

# Claude --output-format json may emit a single JSON object or
# (in edge cases) a JSON array. Handle both.
try:
    data = json.loads(raw)
except json.JSONDecodeError:
    # Try to grab the last non-empty line (stream-json fallback)
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            break
        except json.JSONDecodeError:
            pass
    else:
        print("token_capture: cannot parse JSON from claude output", file=sys.stderr)
        result_path.write_text(raw, encoding='utf-8')  # preserve raw as result
        print(json.dumps({"captured": False, "reason": "cannot parse JSON from claude output"}))
        sys.exit(1)

# Extract agent text result (may be list of content blocks or plain string)
result = data.get('result', '')
if isinstance(result, list):
    # content block array: extract text blocks
    result = '\n'.join(
        b.get('text', '') for b in result
        if isinstance(b, dict) and b.get('type') == 'text'
    )
elif not isinstance(result, str):
    result = str(result)

# Write result text (always, even if empty)
try:
    result_path.write_text(result, encoding='utf-8')
except OSError as e:
    print(f"token_capture: cannot write result_text_file: {e}", file=sys.stderr)

# ---------------------------------------------------------------------------
# Extract top-level usage fields (legacy / aggregate totals from CLI)
# ---------------------------------------------------------------------------
usage = data.get('usage', {})
if not isinstance(usage, dict):
    usage = {}

record = {
    'input_tokens':                  int(usage.get('input_tokens',                  0)),
    'output_tokens':                 int(usage.get('output_tokens',                 0)),
    'cache_creation_input_tokens':   int(usage.get('cache_creation_input_tokens',   0)),
    'cache_read_input_tokens':       int(usage.get('cache_read_input_tokens',       0)),
    'canonical_model_id':            '',
    'total_cost_usd':                None,
    'model_usage':                   None,
}

# ---------------------------------------------------------------------------
# Extract total_cost_usd — authoritative per-invocation cost from the CLI.
# Captured verbatim as a float; set to None (JSON null) when absent.
# ---------------------------------------------------------------------------
raw_cost = data.get('total_cost_usd')
if isinstance(raw_cost, (int, float)):
    record['total_cost_usd'] = float(raw_cost)

# ---------------------------------------------------------------------------
# Extract modelUsage and transform to snake_case model_usage.
# CLI shape:
#   "modelUsage": {
#     "claude-opus-4-8": {
#       "inputTokens": N,
#       "outputTokens": N,
#       "cacheReadInputTokens": N,       -- optional
#       "cacheCreationInputTokens": N,   -- optional
#       "costUSD": N
#     },
#     ...
#   }
# We do NOT assume key order — iterate explicitly over all models.
# ---------------------------------------------------------------------------
raw_model_usage = data.get('modelUsage', None)
if isinstance(raw_model_usage, dict) and raw_model_usage:
    model_usage = {}
    for model_name, mu in raw_model_usage.items():
        if not isinstance(mu, dict):
            continue
        model_usage[model_name] = {
            'input_tokens':                 int(mu.get('inputTokens',               0)),
            'output_tokens':                int(mu.get('outputTokens',              0)),
            'cache_creation_input_tokens':  int(mu.get('cacheCreationInputTokens',  0)),
            'cache_read_input_tokens':      int(mu.get('cacheReadInputTokens',       0)),
            'cost_usd':                     float(mu.get('costUSD', 0)),
        }
    record['model_usage'] = model_usage

    # canonical_model_id: identify the primary model for this task.
    # Priority:
    #   1. requested_model (the --model value the wake script used), when it
    #      appears as a key in modelUsage — that is by definition the primary.
    #   2. The key with the greatest total token volume (input+output tokens),
    #      i.e. the model that performed the most work.
    #   3. Empty string — caller falls back to selected_model or a short alias.
    # (Used for legacy pricing-table fallback in wake/claude.sh.)
    canonical = ""
    if requested_model and requested_model in model_usage:
        canonical = requested_model
    elif model_usage:
        canonical = max(
            model_usage,
            key=lambda m: (
                model_usage[m].get('input_tokens', 0)
                + model_usage[m].get('output_tokens', 0)
            ),
        )
    if canonical:
        record['canonical_model_id'] = canonical
else:
    # modelUsage absent (old CLI) — fall back to canonical_model_id extraction
    # from whatever modelUsage remnant may exist (e.g. empty dict or absent).
    # If truly absent, canonical_model_id stays "" and caller uses selected_model.
    pass

print(json.dumps(record, separators=(',', ':')))
PY
    )" || parse_exit=$?

    if [[ $parse_exit -ne 0 ]]; then
        echo "token_capture.sh: WARNING: parse_result failed (exit ${parse_exit})" >&2
        echo '{}'
        return 1
    fi

    echo "$usage_json"
    return 0
}

# ---------------------------------------------------------------------------
# token_capture_merge_into_task_artifacts
#         <usage_record_json> <task_artifact_dir>
#         <model> <provider> <agent> <elapsed_seconds> [<rc_version>]
#
# Merges a per-invocation usage record into <task_artifact_dir>/tokens.json.
#
# NEW SCHEMA (when usage_record contains total_cost_usd and model_usage):
#   total_cost_usd is summed across invocations.
#   model_usage entries are merged: per-model numeric fields are summed;
#   new models are added as new keys.
#   Top-level model, input_tokens, output_tokens, cache_* fields are NOT written.
#
# LEGACY SCHEMA (when total_cost_usd or model_usage is absent / null):
#   Numeric fields (input_tokens, output_tokens, cache_creation_input_tokens,
#   cache_read_input_tokens, elapsed_seconds) are summed across invocations.
#   The invocations counter is incremented by 1.
#   The model, provider, and agent fields reflect the most recent invocation.
#   Numeric fields are summed across invocations when merging records.
#
# The timestamp field is always refreshed to the current UTC ISO-8601 time.
# provider, agent, and rc_version are refreshed from the most recent invocation.
#
# If <usage_record_json> is empty or '{}' (no usage captured), the function
# still increments invocations and accumulates elapsed_seconds — only token
# fields remain 0.
#
# Arguments:
#   $1  usage_record_json   — compact JSON string from token_capture_parse_result
#                             e.g. '{"input_tokens":100,"output_tokens":50,...}'
#   $2  task_artifact_dir   — absolute path to the task's artifacts/ directory
#   $3  model               — model name string (e.g. "claude-sonnet-4-6");
#                             used in legacy schema and as fallback when model_usage absent
#   $4  provider            — provider string (e.g. "claude")
#   $5  agent               — agent role string (e.g. "coder")
#   $6  elapsed_seconds     — integer seconds this invocation took
#   $7  rc_version          — (optional) RC version string (e.g. "v0.24.12") or "none".
#                             Written to tokens.json as the rc_version field.
#                             When omitted, existing file's rc_version is preserved;
#                             a new file gets rc_version="none".
#
# Returns 0 always. Errors emit a single warning line and do not propagate.
# ---------------------------------------------------------------------------
token_capture_merge_into_task_artifacts() {
    local usage_record_json="$1"
    local task_artifact_dir="$2"
    local model="${3:-unknown}"
    local provider="${4:-claude}"
    local agent="${5:-unknown}"
    local elapsed_seconds="${6:-0}"
    local rc_version="${7:-}"

    if [[ -z "$task_artifact_dir" ]]; then
        echo "token_capture.sh: WARNING: token_capture_merge_into_task_artifacts: task_artifact_dir is empty — skipping" >&2
        return 0
    fi

    # Ensure artifacts directory exists
    if ! mkdir -p "$task_artifact_dir" 2>/dev/null; then
        echo "token_capture.sh: WARNING: cannot create artifacts dir '${task_artifact_dir}' — skipping token capture" >&2
        return 0
    fi

    local tokens_json="${task_artifact_dir}/tokens.json"

    # Attempt merge with jq if available; fall back to python3
    if command -v jq >/dev/null 2>&1; then
        _token_capture_merge_jq \
            "$usage_record_json" "$tokens_json" \
            "$model" "$provider" "$agent" "$elapsed_seconds" "$rc_version" \
            || echo "token_capture.sh: WARNING: jq merge failed — skipping token capture" >&2
    else
        _token_capture_merge_python \
            "$usage_record_json" "$tokens_json" \
            "$model" "$provider" "$agent" "$elapsed_seconds" "$rc_version" \
            || echo "token_capture.sh: WARNING: python3 merge failed — skipping token capture" >&2
    fi

    return 0
}

# ---------------------------------------------------------------------------
# _token_capture_merge_jq (internal)
# Called by token_capture_merge_into_task_artifacts when jq is available.
# ---------------------------------------------------------------------------
_token_capture_merge_jq() {
    local usage_json="$1"
    local tokens_json="$2"
    local model="$3"
    local provider="$4"
    local agent="$5"
    local elapsed_seconds="$6"
    local rc_version="${7:-}"

    local now
    now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    # Detect captured:false (parse failure sentinel from token_capture_parse_result).
    # When captured is false, write an explicit failure record and return.
    local is_captured_false
    is_captured_false="$(printf '%s' "$usage_json" | jq -r 'if .captured == false then "yes" else "no" end' 2>/dev/null || echo "no")"
    if [[ "$is_captured_false" == "yes" ]]; then
        local failure_reason
        failure_reason="$(printf '%s' "$usage_json" | jq -r '.reason // "no usage emitted"' 2>/dev/null || echo "no usage emitted")"
        local effective_rc_version_f="${rc_version:-none}"
        jq -n \
            --arg provider "$provider" \
            --arg agent "$agent" \
            --arg now "$now" \
            --arg rc_version "$effective_rc_version_f" \
            --arg reason "$failure_reason" \
            --argjson elapsed "$elapsed_seconds" \
            '{
              captured:        false,
              reason:          $reason,
              provider:        $provider,
              agent:           $agent,
              rc_version:      $rc_version,
              invocations:     1,
              elapsed_seconds: $elapsed,
              timestamp:       $now,
              total_cost_usd:  0,
              model_usage:     {}
            }' > "$tokens_json" 2>/dev/null || return 1
        return 0
    fi

    # Detect whether this is a new-schema invocation (total_cost_usd + model_usage present).
    # jq: .total_cost_usd is a number AND .model_usage is a non-null object.
    local has_new_schema
    has_new_schema="$(printf '%s' "$usage_json" | jq -r '
        if ((.total_cost_usd | type) == "number") and
           ((.model_usage | type) == "object") and
           (.model_usage != null)
        then "yes" else "no" end
    ' 2>/dev/null || echo "no")"

    if [[ "$has_new_schema" == "yes" ]]; then
        # ---- NEW SCHEMA ----
        # Merge total_cost_usd (sum) and model_usage (per-model sum).
        # Drop top-level model/input_tokens/output_tokens/cache_* from output.
        local new_cost new_model_usage
        new_cost="$(printf '%s' "$usage_json" | jq '.total_cost_usd' 2>/dev/null || echo 0)"
        new_model_usage="$(printf '%s' "$usage_json" | jq '.model_usage' 2>/dev/null || echo '{}')"

        # Resolve rc_version: use passed arg when non-empty; otherwise preserve
        # existing file's value (merge case) or default to "none" (new file case).
        local effective_rc_version
        effective_rc_version="${rc_version:-none}"

        if [[ -f "$tokens_json" && -s "$tokens_json" ]]; then
            local merged
            # Use a jq def to merge model_usage per-model entries.
            # jq 1.6 does not support "reduce...as $r |" binding outside of reduce;
            # using def avoids that restriction.
            # rc_version: use $new_rc_version when non-empty; otherwise fall back
            # to existing file's rc_version field.
            merged="$(jq -n \
                --arg provider "$provider" \
                --arg agent "$agent" \
                --arg now "$now" \
                --arg new_rc_version "$effective_rc_version" \
                --argjson new_cost "$new_cost" \
                --argjson new_model_usage "$new_model_usage" \
                --argjson new_elapsed "$elapsed_seconds" \
                --slurpfile existing "$tokens_json" \
                '
                def merge_model_usage(existing_mu; new_mu):
                  reduce (new_mu | to_entries[]) as $entry (
                    existing_mu;
                    . + {
                      ($entry.key): {
                        input_tokens:                ((.[($entry.key)].input_tokens                // 0) + ($entry.value.input_tokens                // 0)),
                        output_tokens:               ((.[($entry.key)].output_tokens               // 0) + ($entry.value.output_tokens               // 0)),
                        cache_creation_input_tokens: ((.[($entry.key)].cache_creation_input_tokens // 0) + ($entry.value.cache_creation_input_tokens // 0)),
                        cache_read_input_tokens:     ((.[($entry.key)].cache_read_input_tokens     // 0) + ($entry.value.cache_read_input_tokens     // 0)),
                        cost_usd:                    ((.[($entry.key)].cost_usd                    // 0) + ($entry.value.cost_usd                    // 0))
                      }
                    }
                  );
                ($existing[0]) as $e |
                {
                  provider:        $provider,
                  agent:           $agent,
                  rc_version:      (if ($new_rc_version != "") then $new_rc_version else ($e.rc_version // "none") end),
                  invocations:     (($e.invocations // 0) + 1),
                  elapsed_seconds: (($e.elapsed_seconds // 0) + $new_elapsed),
                  timestamp:       $now,
                  total_cost_usd:  (($e.total_cost_usd // 0) + $new_cost),
                  model_usage:     merge_model_usage(($e.model_usage // {}); $new_model_usage)
                }
                ' 2>/dev/null)" || return 1
            printf '%s\n' "$merged" > "$tokens_json" || return 1
        else
            # Create new file with new schema
            local merged
            merged="$(jq -n \
                --arg provider "$provider" \
                --arg agent "$agent" \
                --arg now "$now" \
                --arg new_rc_version "$effective_rc_version" \
                --argjson new_cost "$new_cost" \
                --argjson new_model_usage "$new_model_usage" \
                --argjson new_elapsed "$elapsed_seconds" \
                '{
                  provider:        $provider,
                  agent:           $agent,
                  rc_version:      $new_rc_version,
                  invocations:     1,
                  elapsed_seconds: $new_elapsed,
                  timestamp:       $now,
                  total_cost_usd:  $new_cost,
                  model_usage:     $new_model_usage
                }' 2>/dev/null)" || return 1
            printf '%s\n' "$merged" > "$tokens_json" || return 1
        fi
    else
        # ---- LEGACY SCHEMA ----
        # total_cost_usd or model_usage absent — use old capture behavior.
        local new_input new_output new_cache_creation new_cache_read
        new_input="$(printf '%s' "$usage_json" | jq -r '.input_tokens // 0' 2>/dev/null || echo 0)"
        new_output="$(printf '%s' "$usage_json" | jq -r '.output_tokens // 0' 2>/dev/null || echo 0)"
        new_cache_creation="$(printf '%s' "$usage_json" | jq -r '.cache_creation_input_tokens // 0' 2>/dev/null || echo 0)"
        new_cache_read="$(printf '%s' "$usage_json" | jq -r '.cache_read_input_tokens // 0' 2>/dev/null || echo 0)"

        if [[ -f "$tokens_json" && -s "$tokens_json" ]]; then
            local merged
            merged="$(jq -n \
                --arg model "$model" \
                --arg provider "$provider" \
                --arg agent "$agent" \
                --arg now "$now" \
                --arg new_rc_version "${rc_version:-none}" \
                --argjson new_input "$new_input" \
                --argjson new_output "$new_output" \
                --argjson new_cache_creation "$new_cache_creation" \
                --argjson new_cache_read "$new_cache_read" \
                --argjson new_elapsed "$elapsed_seconds" \
                --slurpfile existing "$tokens_json" \
                '
                ($existing[0]) as $e |
                {
                  model:                        $model,
                  provider:                     $provider,
                  agent:                        $agent,
                  rc_version:                   (if ($new_rc_version != "") then $new_rc_version else ($e.rc_version // "none") end),
                  input_tokens:                 (($e.input_tokens                  // 0) + $new_input),
                  output_tokens:                (($e.output_tokens                 // 0) + $new_output),
                  cache_creation_input_tokens:  (($e.cache_creation_input_tokens   // 0) + $new_cache_creation),
                  cache_read_input_tokens:      (($e.cache_read_input_tokens       // 0) + $new_cache_read),
                  invocations:                  (($e.invocations                   // 0) + 1),
                  elapsed_seconds:              (($e.elapsed_seconds               // 0) + $new_elapsed),
                  timestamp:                    $now
                }
                ' 2>/dev/null)" || return 1
            printf '%s\n' "$merged" > "$tokens_json" || return 1
        else
            # Create new file with legacy schema
            jq -n \
                --arg model "$model" \
                --arg provider "$provider" \
                --arg agent "$agent" \
                --arg now "$now" \
                --arg new_rc_version "${rc_version:-none}" \
                --argjson new_input "$new_input" \
                --argjson new_output "$new_output" \
                --argjson new_cache_creation "$new_cache_creation" \
                --argjson new_cache_read "$new_cache_read" \
                --argjson new_elapsed "$elapsed_seconds" \
                '{
                  model:                        $model,
                  provider:                     $provider,
                  agent:                        $agent,
                  rc_version:                   $new_rc_version,
                  input_tokens:                 $new_input,
                  output_tokens:                $new_output,
                  cache_creation_input_tokens:  $new_cache_creation,
                  cache_read_input_tokens:      $new_cache_read,
                  invocations:                  1,
                  elapsed_seconds:              $new_elapsed,
                  timestamp:                    $now
                }' > "$tokens_json" 2>/dev/null || return 1
        fi
    fi

    return 0
}

# ---------------------------------------------------------------------------
# _token_capture_merge_python (internal)
# Called by token_capture_merge_into_task_artifacts when jq is NOT available.
# ---------------------------------------------------------------------------
_token_capture_merge_python() {
    local usage_json="$1"
    local tokens_json="$2"
    local model="$3"
    local provider="$4"
    local agent="$5"
    local elapsed_seconds="$6"
    local rc_version="${7:-}"

    python3 - \
        "$usage_json" "$tokens_json" \
        "$model" "$provider" "$agent" "$elapsed_seconds" "${rc_version:-none}" <<'PY'
import json, sys, pathlib, datetime

usage_json_str   = sys.argv[1]
tokens_json_path = pathlib.Path(sys.argv[2])
model            = sys.argv[3]
provider         = sys.argv[4]
agent            = sys.argv[5]
elapsed_seconds  = int(sys.argv[6]) if sys.argv[6].isdigit() else 0
rc_version       = sys.argv[7] if len(sys.argv) > 7 else 'none'
# Treat empty string as "none" (caller may pass empty when no RC is active)
if not rc_version:
    rc_version = 'none'

# Parse the per-invocation usage record
try:
    new = json.loads(usage_json_str) if usage_json_str.strip() not in ('', '{}') else {}
except json.JSONDecodeError:
    new = {}

now = datetime.datetime.now(tz=datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

# Detect captured:false sentinel (parse failure from token_capture_parse_result).
# When captured is False, write an explicit failure record and exit.
if new.get('captured') is False:
    failure_reason = new.get('reason', 'no usage emitted')
    merged_failure = {
        'captured':        False,
        'reason':          failure_reason,
        'provider':        provider,
        'agent':           agent,
        'rc_version':      rc_version if rc_version else 'none',
        'invocations':     1,
        'elapsed_seconds': elapsed_seconds,
        'timestamp':       now,
        'total_cost_usd':  0,
        'model_usage':     {},
    }
    try:
        tokens_json_path.write_text(json.dumps(merged_failure, indent=2) + '\n', encoding='utf-8')
    except OSError as e:
        print(f"token_capture: cannot write tokens.json: {e}", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)

# Load existing record (if any)
existing = {}
if tokens_json_path.is_file() and tokens_json_path.stat().st_size > 0:
    try:
        existing = json.loads(tokens_json_path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        existing = {}

# Detect new schema: total_cost_usd is a float AND model_usage is a non-null dict.
new_total_cost = new.get('total_cost_usd')
new_model_usage = new.get('model_usage')
has_new_schema = (
    isinstance(new_total_cost, float) and
    isinstance(new_model_usage, dict) and
    new_model_usage is not None
)

if has_new_schema:
    # ---- NEW SCHEMA ----
    # Merge total_cost_usd (sum) and model_usage (per-model numeric sums).
    # Drop top-level model/input_tokens/output_tokens/cache_* from output.

    existing_cost = existing.get('total_cost_usd', 0)
    if not isinstance(existing_cost, (int, float)):
        existing_cost = 0
    merged_cost = existing_cost + new_total_cost

    # Merge model_usage: preserve existing models, sum fields for shared models.
    existing_mu = existing.get('model_usage', {})
    if not isinstance(existing_mu, dict):
        existing_mu = {}
    merged_mu = dict(existing_mu)  # copy so we don't mutate existing
    for m, mu in new_model_usage.items():
        if m in merged_mu:
            prev = merged_mu[m]
            merged_mu[m] = {
                'input_tokens':                 prev.get('input_tokens',                0) + mu.get('input_tokens',                0),
                'output_tokens':                prev.get('output_tokens',               0) + mu.get('output_tokens',               0),
                'cache_creation_input_tokens':  prev.get('cache_creation_input_tokens', 0) + mu.get('cache_creation_input_tokens', 0),
                'cache_read_input_tokens':      prev.get('cache_read_input_tokens',     0) + mu.get('cache_read_input_tokens',     0),
                'cost_usd':                     prev.get('cost_usd',                    0) + mu.get('cost_usd',                    0),
            }
        else:
            merged_mu[m] = {
                'input_tokens':                 mu.get('input_tokens',                0),
                'output_tokens':                mu.get('output_tokens',               0),
                'cache_creation_input_tokens':  mu.get('cache_creation_input_tokens', 0),
                'cache_read_input_tokens':      mu.get('cache_read_input_tokens',     0),
                'cost_usd':                     mu.get('cost_usd',                    0),
            }

    # rc_version: use passed arg when non-empty/non-"none"; otherwise
    # fall back to existing file's rc_version, defaulting to "none".
    effective_rc_version = rc_version if rc_version and rc_version != 'none' else existing.get('rc_version', 'none') or 'none'

    merged = {
        'provider':        provider,
        'agent':           agent,
        'rc_version':      effective_rc_version,
        'invocations':     existing.get('invocations', 0) + 1,
        'elapsed_seconds': existing.get('elapsed_seconds', 0) + elapsed_seconds,
        'timestamp':       now,
        'total_cost_usd':  merged_cost,
        'model_usage':     merged_mu,
    }
else:
    # ---- LEGACY SCHEMA ----
    # total_cost_usd or model_usage absent — use old capture behavior.
    new_input = int(new.get('input_tokens',                0))
    new_output = int(new.get('output_tokens',               0))
    new_cc     = int(new.get('cache_creation_input_tokens', 0))
    new_cr     = int(new.get('cache_read_input_tokens',     0))

    # rc_version: use passed arg when non-empty; otherwise fall back to
    # existing file's rc_version, defaulting to "none".
    effective_rc_version = rc_version if rc_version and rc_version != 'none' else existing.get('rc_version', 'none') or 'none'

    merged = {
        'model':                        model,
        'provider':                     provider,
        'agent':                        agent,
        'rc_version':                   effective_rc_version,
        'input_tokens':                 existing.get('input_tokens',                0) + new_input,
        'output_tokens':                existing.get('output_tokens',               0) + new_output,
        'cache_creation_input_tokens':  existing.get('cache_creation_input_tokens', 0) + new_cc,
        'cache_read_input_tokens':      existing.get('cache_read_input_tokens',     0) + new_cr,
        'invocations':                  existing.get('invocations',                 0) + 1,
        'elapsed_seconds':              existing.get('elapsed_seconds',             0) + elapsed_seconds,
        'timestamp':                    now,
    }

try:
    tokens_json_path.write_text(json.dumps(merged, indent=2) + '\n', encoding='utf-8')
except OSError as e:
    print(f"token_capture: cannot write tokens.json: {e}", file=sys.stderr)
    sys.exit(1)
PY
    return $?
}
