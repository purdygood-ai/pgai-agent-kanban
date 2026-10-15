#!/usr/bin/env bash
# team/scripts/lib/wake_safeguard_refusal.sh
#
# Wake safeguard-refusal classifier stage: decides whether the log output of
# one provider invocation carries the shape of a provider-level safeguard
# refusing the request, as opposed to a timeout, network, auth or rate-limit
# failure, or anything unrecognised. One of the single-concern lib stages of
# the shared wake pipeline, sourced by wake_driver_invoke.sh itself
# (self-located, so the classifier is defined whether the invoke stage is
# loaded through wake_driver.sh or sourced on its own by a test), and kept
# separate so the invoke stage stays under the engine-decomposition line
# ceiling (team/scripts/lint_engine_size_dupe.py).
#
# Public entry point
# -------------------
#   _wd_is_safeguard_refusal <text>
#       Exit 0 for safeguard-shaped text, 1 otherwise. Its only caller is
#       _wd_run_provider_with_telemetry_safeguard_retry
#       (wake_driver_invoke.sh), which retries exactly once without the
#       documentation-feedback block when this returns 0.
#
# Dependencies: none beyond printf and grep. Pure function: reads only its
# argument, writes nothing, logs nothing.

# ---------------------------------------------------------------------------
# _wd_is_safeguard_refusal <text> (private)
#
# True (exit 0) when <text> — the newly appended log output from one
# provider invocation — carries the shape of a provider-level safeguard
# refusing the request outright, as distinct from the model declining in
# its own generated text, or an unrelated provider failure.
#
# Exclusion first, signal second: a timeout/network/auth/rate-limit
# signature disqualifies the text immediately regardless of anything else
# it contains — those are real provider failures with their own handling
# (the MAX_TASK_SECONDS watchdog; wake_driver_finalize.sh's existing
# exit-code-to-state mapping), and retrying one without the telemetry
# block would mask the real failure and burn a second invocation for
# nothing. Only once that exclusion clears does a positive safeguard
# signature — safety-system / content-policy / prompt-injection-guard /
# moderation-classifier wording — qualify as a telemetry-block refusal
# worth retrying without the block. Text matching neither list returns
# false: an unrecognised failure is never assumed to be the block's
# fault, which is what keeps the caller's retry to exactly once.
# ---------------------------------------------------------------------------
_wd_is_safeguard_refusal() {
  local _sr_text="${1:-}"

  if [[ -z "$_sr_text" ]]; then
    return 1
  fi

  # Exclusion list: timeout / network / auth / rate-limit signatures.
  if grep -qiE \
    'time(d)?[[:space:]]*out|ETIMEDOUT|connection[[:space:]]+(refused|reset)|network[[:space:]]+error|ECONNREFUSED|getaddrinfo|name[[:space:]]+resolution|auth(entication)?[[:space:]]+(failed|error)|unauthorized|invalid[[:space:]]+api[[:space:]]+key|\b401\b|rate[[:space:]-]*limit|\b429\b|quota[[:space:]]+exceeded|overloaded' <<< "$_sr_text"; then
    return 1
  fi

  # Positive signal: a provider-level content/safety safeguard refusal.
  if grep -qiE \
    '(safety[[:space:]-]?system|content[[:space:]-]?polic|prompt[[:space:]-]?injection[[:space:]-]?(defense|guard)|blocked[[:space:]-]?by[[:space:]-]?(safety|policy|classifier)|flagged[[:space:]-]?(as|for)[[:space:]-]?(unsafe|polic)|moderation[[:space:]-]?(block|refus))' <<< "$_sr_text"; then
    return 0
  fi

  return 1
}
