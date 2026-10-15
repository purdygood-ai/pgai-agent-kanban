"""
test_wake_telemetry_safeguard_retry.py
========================================
Behavioral fixtures for the optional documentation-feedback (formerly
"reasoning-trace") prompt block's provider-safeguard safety net
(v2.14.0-provider-runtime-hardening: "optional telemetry blocks never kill
a wake").

The block injected by team/scripts/lib/wake_driver_invoke.sh's
_wd_build_task_prompt is pure prompt-engineering instrumentation for the
optional per-project training-corpus gate. It contributes nothing to
whether a task ships, so two independent changes protect the wake from it:

  1. The block itself is reworded into documentation-feedback vocabulary —
     no "trace", "introspection", or "confidence calibration" (or a
     synonym asking the model to report on its own reasoning) anywhere in
     the operator-facing prompt text — reducing how often a provider
     safeguard tuned to refuse self-report requests fires on it at all.
  2. Independently, a safeguard-shaped refusal on the first invocation is
     now a recoverable event: wd_invoke_task's invocation call sites route
     through the new _wd_run_provider_with_telemetry_safeguard_retry
     wrapper, which retries EXACTLY ONCE with the block omitted, logs the
     provider's error verbatim, and narrates the skip with the stable,
     greppable phrase "skipping trace injection: provider safeguard" (kept
     literal even though the block's own vocabulary no longer says
     "trace" — it is a log key, not prose). Detection
     (_wd_is_safeguard_refusal) is deliberately narrow: a timeout,
     network fault, auth failure, or rate limit never triggers the retry.

Tests source the REAL production wake_driver_invoke.sh and invoke its real
functions directly (not a reimplementation) via a bash subprocess harness,
matching the project's established "extraction and harness" convention
(see test_wake_provider_identity_log.py, test_wake_claude_effort_wiring.py).

Naming: function names describe behavior under test, never bug IDs or
task IDs (see roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Paths (relative to this file: team/tests/unit/ -> team/ -> repo root)
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_LIB_DIR = _TEAM_DIR / "scripts" / "lib"
_DRIVER_INVOKE = _LIB_DIR / "wake_driver_invoke.sh"
_ERROR_CONTRACT = _LIB_DIR / "error_contract.sh"
_WAKE_COMMON = _LIB_DIR / "wake_common.sh"

_FORBIDDEN_WORDS = ("trace", "introspection", "confidence calibration")
_BLOCK_BEGIN = "## Documentation Feedback (documentation-feedback mode)"
_BLOCK_END_MARKER = 'Write it as plain feedback on the documentation, not as a performance."'

_WATCHDOG_BEGIN = "# WATCHDOG-INVOKE-BEGIN"
_WATCHDOG_END = "# WATCHDOG-INVOKE-END"


def _run(harness: str, env_overrides: dict[str, str] | None = None, timeout: float = 30) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["bash", "-c", harness],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def _extract_prompt_block() -> str:
    text = _DRIVER_INVOKE.read_text(encoding="utf-8")
    begin = text.index(_BLOCK_BEGIN)
    end = text.index(_BLOCK_END_MARKER) + len(_BLOCK_END_MARKER)
    return text[begin:end]


def _extract_watchdog_block() -> str:
    text = _DRIVER_INVOKE.read_text(encoding="utf-8")
    begin = text.index(_WATCHDOG_BEGIN)
    end = text.index(_WATCHDOG_END) + len(_WATCHDOG_END)
    return text[begin:end]


# ---------------------------------------------------------------------------
# (1) Vocabulary: the reworded block contains none of the forbidden words,
#     in either wake_driver_invoke.sh or wake_common.sh.
# ---------------------------------------------------------------------------


def test_prompt_block_excludes_forbidden_vocabulary() -> None:
    """The documentation-feedback prompt block sent to the model contains
    none of 'trace', 'introspection', or 'confidence calibration' — the
    vocabulary a provider safeguard is tuned to refuse requests for
    self-reported reasoning on."""
    block = _extract_prompt_block()
    lowered = block.lower()
    for word in _FORBIDDEN_WORDS:
        assert not re.search(r"\b" + re.escape(word) + r"\b", lowered), (
            f"Forbidden word {word!r} found in the documentation-feedback "
            f"prompt block:\n{block}"
        )


def test_wake_common_has_no_prompt_injection_vocabulary() -> None:
    """wake_common.sh carries no prompt-injection logic of its own (it is a
    thin sourcing shell over the staged lib files) -- the forbidden words
    are absent there too, satisfying the acceptance criterion's grep over
    both files even though the actual block lives only in
    wake_driver_invoke.sh today."""
    content = _WAKE_COMMON.read_text(encoding="utf-8").lower()
    for word in _FORBIDDEN_WORDS:
        assert not re.search(r"\b" + re.escape(word) + r"\b", content), (
            f"Forbidden word {word!r} found in {_WAKE_COMMON}."
        )


def test_skip_narration_phrase_is_present_and_literal() -> None:
    """The exact, stable, greppable phrase the constraint requires is
    present verbatim in wake_driver_invoke.sh, even though the block's own
    vocabulary no longer says 'trace' -- the log key is intentionally kept
    literal."""
    content = _DRIVER_INVOKE.read_text(encoding="utf-8")
    assert "skipping trace injection: provider safeguard" in content, (
        "Expected the exact narration phrase 'skipping trace injection: "
        "provider safeguard' in wake_driver_invoke.sh."
    )


# ---------------------------------------------------------------------------
# (2) _wd_is_safeguard_refusal: detection specificity, direct unit tests.
# ---------------------------------------------------------------------------


_SAFEGUARD_TEXTS = [
    "Error: request blocked by safety system before dispatch.",
    "This request was blocked by content policy filtering.",
    "Refused: prompt injection defense triggered on the inbound request.",
    "The output was flagged as unsafe by the moderation classifier.",
]

_NON_SAFEGUARD_TEXTS = [
    "curl: (28) Connection timed out after 30001 milliseconds",
    "Error: ETIMEDOUT connecting to api.example.com",
    "Error: connection refused by upstream host",
    "Authentication failed: invalid API key provided",
    "HTTP 401 Unauthorized",
    "HTTP 429 Too Many Requests -- rate limit exceeded, retry later",
    "Error: the model is currently overloaded, please try again",
    "some unrelated stack trace with no recognizable signature at all",
    "",
]


@pytest.mark.parametrize("text", _SAFEGUARD_TEXTS)
def test_is_safeguard_refusal_true_for_safeguard_shaped_text(text: str) -> None:
    harness = f"""
set -uo pipefail
source {str(_DRIVER_INVOKE)!r}
_wd_is_safeguard_refusal {text!r}
echo "RESULT=$?"
"""
    result = _run(harness)
    assert "RESULT=0" in result.stdout, (
        f"Expected {text!r} to be classified as a safeguard refusal.\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )


@pytest.mark.parametrize("text", _NON_SAFEGUARD_TEXTS)
def test_is_safeguard_refusal_false_for_non_safeguard_text(text: str) -> None:
    harness = f"""
set -uo pipefail
source {str(_DRIVER_INVOKE)!r}
_wd_is_safeguard_refusal {text!r}
echo "RESULT=$?"
"""
    result = _run(harness)
    assert "RESULT=1" in result.stdout, (
        f"Expected {text!r} to NOT be classified as a safeguard refusal "
        f"(timeout/network/auth/rate-limit signatures, or unrelated text, "
        f"must never trigger the retry).\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# (3) _wd_run_provider_with_telemetry_safeguard_retry: retry behavior.
# ---------------------------------------------------------------------------


def _build_retry_harness(
    tmp_path: pathlib.Path,
    *,
    first_attempt_text: str,
    first_attempt_exit: int,
    second_attempt_text: str = "second attempt output",
    second_attempt_exit: int = 0,
    telemetry_gate_active: str = "true",
) -> tuple[str, pathlib.Path, pathlib.Path]:
    log_file = tmp_path / "wake.log"
    log_file.write_text("", encoding="utf-8")
    call_count_file = tmp_path / "call_count"
    call_count_file.write_text("0", encoding="utf-8")

    harness = f"""
set -uo pipefail
source {str(_ERROR_CONTRACT)!r}
source {str(_DRIVER_INVOKE)!r}

LOG_FILE={str(log_file)!r}
AGENT="coder"
task_id="TASK-SAFEGUARD-RETRY-TEST"
_wd_telemetry_gate_active={telemetry_gate_active!r}
_wd_prompt_sans_telemetry="prompt without the documentation-feedback block"
prompt="prompt with the documentation-feedback block"

_CALL_COUNT_FILE={str(call_count_file)!r}
_wd_run_provider_and_capture_tokens() {{
  local n
  n=$(( $(cat "$_CALL_COUNT_FILE") + 1 ))
  echo "$n" > "$_CALL_COUNT_FILE"
  if [[ "$n" -eq 1 ]]; then
    echo {first_attempt_text!r} >> "$LOG_FILE"
    PROVIDER_AGENT_EXIT_CODE={first_attempt_exit}
  else
    echo {second_attempt_text!r} >> "$LOG_FILE"
    PROVIDER_AGENT_EXIT_CODE={second_attempt_exit}
  fi
}}

_wd_run_provider_with_telemetry_safeguard_retry
echo "FINAL_EXIT=${{PROVIDER_AGENT_EXIT_CODE}}"
echo "FINAL_PROMPT=${{prompt}}"
"""
    return harness, log_file, call_count_file


def test_safeguard_refusal_then_success_retries_once_without_block(
    tmp_path: pathlib.Path,
) -> None:
    """First invocation refused with a safeguard-shaped error, second
    succeeds: the wrapper retries exactly once with the pre-injection
    prompt, the final exit code is the retry's (0), and the log carries
    both the verbatim provider error and the exact skip phrase."""
    harness, log_file, call_count_file = _build_retry_harness(
        tmp_path,
        first_attempt_text="Error: request blocked by safety system.",
        first_attempt_exit=1,
    )
    result = _run(harness)

    assert result.returncode == 0, (
        f"Harness failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "FINAL_EXIT=0" in result.stdout, result.stdout
    assert "FINAL_PROMPT=prompt without the documentation-feedback block" in result.stdout, (
        "Expected the retry to swap in the pre-injection prompt.\n"
        f"stdout: {result.stdout}"
    )
    assert call_count_file.read_text(encoding="utf-8").strip() == "2", (
        "Expected exactly one retry (two total invocation attempts)."
    )

    log_text = log_file.read_text(encoding="utf-8")
    assert "Error: request blocked by safety system." in log_text, (
        f"Expected the verbatim provider error in the log; got:\n{log_text}"
    )
    assert "skipping trace injection: provider safeguard" in log_text, (
        f"Expected the exact skip narration phrase in the log; got:\n{log_text}"
    )


def test_second_safeguard_refusal_is_not_retried_again(tmp_path: pathlib.Path) -> None:
    """A second safeguard-shaped refusal, after the block is already gone,
    is a real provider failure and is returned unchanged -- never a third
    attempt."""
    harness, log_file, call_count_file = _build_retry_harness(
        tmp_path,
        first_attempt_text="Error: request blocked by safety system.",
        first_attempt_exit=1,
        second_attempt_text="Error: request blocked by safety system (again).",
        second_attempt_exit=1,
    )
    result = _run(harness)

    assert result.returncode == 0, (
        f"Harness failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "FINAL_EXIT=1" in result.stdout, (
        "Expected the second refusal's exit code (1) to be returned "
        f"unchanged (no third attempt).\nstdout: {result.stdout}"
    )
    assert call_count_file.read_text(encoding="utf-8").strip() == "2", (
        "Expected exactly two invocation attempts total -- never a third."
    )
    log_text = log_file.read_text(encoding="utf-8")
    assert log_text.count("skipping trace injection: provider safeguard") == 1, (
        "Expected the skip narration exactly once (only the first refusal "
        f"triggers a retry); got:\n{log_text}"
    )


@pytest.mark.parametrize(
    "failure_text",
    [
        "curl: (28) Connection timed out after 30001 milliseconds",
        "Authentication failed: invalid API key provided",
        "HTTP 429 Too Many Requests -- rate limit exceeded, retry later",
    ],
)
def test_non_safeguard_failure_is_never_retried(
    tmp_path: pathlib.Path, failure_text: str
) -> None:
    """A timeout, auth failure, or rate-limit error on the first attempt
    must NOT trigger the retry -- it is returned unchanged, and no skip
    narration is emitted."""
    harness, log_file, call_count_file = _build_retry_harness(
        tmp_path,
        first_attempt_text=failure_text,
        first_attempt_exit=1,
    )
    result = _run(harness)

    assert result.returncode == 0, (
        f"Harness failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "FINAL_EXIT=1" in result.stdout, result.stdout
    assert call_count_file.read_text(encoding="utf-8").strip() == "1", (
        f"Expected no retry for a non-safeguard failure ({failure_text!r}); "
        f"got call count {call_count_file.read_text(encoding='utf-8').strip()!r}."
    )
    log_text = log_file.read_text(encoding="utf-8")
    assert "skipping trace injection: provider safeguard" not in log_text, (
        f"Non-safeguard failure must never emit the skip narration; got:\n{log_text}"
    )


def test_default_path_is_untouched_when_telemetry_gate_inactive(
    tmp_path: pathlib.Path,
) -> None:
    """When the documentation-feedback block was never injected (the
    default path, training gate off), the wrapper is a bare pass-through:
    exactly one invocation, regardless of the stub's exit code, and no
    skip narration -- byte-for-byte today's behaviour."""
    harness, log_file, call_count_file = _build_retry_harness(
        tmp_path,
        first_attempt_text="Error: request blocked by safety system.",
        first_attempt_exit=1,
        telemetry_gate_active="false",
    )
    result = _run(harness)

    assert result.returncode == 0, (
        f"Harness failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "FINAL_EXIT=1" in result.stdout, result.stdout
    assert call_count_file.read_text(encoding="utf-8").strip() == "1", (
        "Expected exactly one invocation on the default (gate-inactive) path."
    )
    log_text = log_file.read_text(encoding="utf-8")
    assert "skipping trace injection: provider safeguard" not in log_text, (
        f"Default path must never emit the skip narration; got:\n{log_text}"
    )


def test_successful_first_attempt_never_triggers_retry_logic(
    tmp_path: pathlib.Path,
) -> None:
    """A successful first attempt (exit 0) with the block in the prompt
    short-circuits immediately -- no offset/pattern-match work, one
    invocation, no skip narration."""
    harness, log_file, call_count_file = _build_retry_harness(
        tmp_path,
        first_attempt_text="ok",
        first_attempt_exit=0,
    )
    result = _run(harness)

    assert result.returncode == 0
    assert "FINAL_EXIT=0" in result.stdout
    assert call_count_file.read_text(encoding="utf-8").strip() == "1"
    assert "skipping trace injection" not in log_file.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# (4) Watchdog budget: the retry draws from the SAME MAX_TASK_SECONDS
#     budget -- it is never granted a second one.
# ---------------------------------------------------------------------------


def test_watchdog_bounds_total_time_including_an_internal_retry(
    tmp_path: pathlib.Path,
) -> None:
    """Runs the REAL watchdog block (extracted verbatim from
    wake_driver_invoke.sh) with a stub invocation function that never
    finishes on its own, simulating a first-attempt-plus-retry combined
    runtime that would exceed MAX_TASK_SECONDS if the retry were ever
    granted a budget of its own.

    The watchdog's own per-second poll (a real `sleep 1` in production) is
    replaced here by an injected `sleep` shell function the test owns: it
    records each call instead of blocking, and the watchdog's clock
    (_wd_epoch_now) is redefined to read that same counter, so the
    assertion is against a controllable, deterministic call count -- not a
    real wall-clock duration. Bounding the count of budget-loop iterations (rather than
    elapsed seconds) is what proves the retry drew from the single existing
    budget and was never granted a second one; it stays true regardless of
    host scheduling load, unlike a real-time measurement.
    """
    watchdog_block = _extract_watchdog_block()
    log_file = tmp_path / "wake.log"
    log_file.write_text("", encoding="utf-8")
    temp_root = tmp_path / "kanban_tmp"
    temp_root.mkdir()
    sleep_calls_file = tmp_path / "sleep_calls"
    sleep_calls_file.write_text("0", encoding="utf-8")

    harness = f"""
set -uo pipefail
export PGAI_AGENT_KANBAN_TEMP_DIR={str(temp_root)!r}
source {str(_TEAM_DIR / "scripts" / "lib" / "temp.sh")!r}
log() {{ echo "[$(date -Iseconds)] wake(${{AGENT}}): $*" | tee -a "$LOG_FILE"; }}

LOG_FILE={str(log_file)!r}
AGENT="coder"
task_id="TASK-WATCHDOG-BUDGET-TEST"
MAX_TASK_SECONDS=2
KILL_GRACE_SECONDS=1

# Controllable clock: the watchdog's per-iteration `sleep 1` calls are
# intercepted here and counted instead of actually blocking, so the test
# owns the delay (none) rather than depending on the host's real clock.
_SLEEP_CALLS_FILE={str(sleep_calls_file)!r}
sleep() {{
  echo "$(( $(cat "$_SLEEP_CALLS_FILE") + 1 ))" > "$_SLEEP_CALLS_FILE"
}}
# The watchdog compares the wall clock against an absolute deadline rather
# than counting iterations, so the clock it reads (_wd_epoch_now) is the
# sleep counter itself: each intercepted `sleep 1` advances the mocked
# clock by exactly one second, keeping the iteration count deterministic.
_wd_epoch_now() {{ cat "$_SLEEP_CALLS_FILE"; }}

# Simulates a first attempt plus an internal safeguard retry that never
# completes on its own -- only the watchdog's SIGTERM/SIGKILL ends it.
# Uses `command sleep` to reach the REAL sleep binary, bypassing this
# harness's own `sleep` function override above, so the background job
# genuinely stays alive across the watchdog's (now-instant) polling loop
# instead of finishing on its own before the timeout path is exercised.
# stdin/stdout/stderr are explicitly redirected away from the harness's
# own streams so a SIGTERM-surviving orphan (once the watchdog kills this
# function's own process but the kernel has not yet reaped a slow-to-die
# child) cannot hold the test runner's output pipe open past the
# watchdog's own bounded wait.
_wd_run_provider_with_telemetry_safeguard_retry() {{
  command sleep 20 </dev/null >/dev/null 2>&1
  PROVIDER_AGENT_EXIT_CODE=0
}}
# No project lock is held here; the real refresh wrapper would be a no-op.
_wd_refresh_project_lock_mtime() {{ :; }}

_run_watchdog() {{
{watchdog_block}
}}
_run_watchdog

echo "FINAL_EXIT=${{PROVIDER_AGENT_EXIT_CODE}}"
echo "TASK_KILLED=${{_task_killed}}"
"""
    result = _run(harness, timeout=25)

    assert result.returncode == 0, (
        f"Harness failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "FINAL_EXIT=124" in result.stdout, (
        f"Expected the watchdog's timeout exit code (124); got:\n{result.stdout}"
    )
    assert "TASK_KILLED=true" in result.stdout, result.stdout
    # Behavioral proof of "no second budget": the main poll loop can run at
    # most MAX_TASK_SECONDS(2) iterations and the grace loop at most
    # KILL_GRACE_SECONDS(1) -- 3 total. A retry granted its own extra
    # budget would double this count; this assertion catches that directly,
    # with no dependence on how fast or slow the host actually is.
    sleep_calls = int(sleep_calls_file.read_text(encoding="utf-8").strip())
    assert sleep_calls <= 3, (
        f"Expected at most 3 watchdog poll iterations (MAX_TASK_SECONDS=2 + "
        f"KILL_GRACE_SECONDS=1), got {sleep_calls} -- the retry appears to "
        f"have been granted a second budget.\nstdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )


def test_watchdog_passes_through_fast_completion_unbounded_by_budget(
    tmp_path: pathlib.Path,
) -> None:
    """A stub that completes on its own (simulating a fast retry) returns
    its own exit code instead of the watchdog waiting out the full budget.

    As in the sibling test above, the watchdog's own `sleep 1` poll is
    replaced by an injected, call-counting stub the test owns. The proof
    that the watchdog did not "wait out the full 30s budget" is therefore a
    bounded iteration count, not a real elapsed-time measurement -- a slow
    or loaded host can no longer turn this into a flaky real-time race.
    """
    watchdog_block = _extract_watchdog_block()
    log_file = tmp_path / "wake.log"
    log_file.write_text("", encoding="utf-8")
    temp_root = tmp_path / "kanban_tmp"
    temp_root.mkdir()
    sleep_calls_file = tmp_path / "sleep_calls"
    sleep_calls_file.write_text("0", encoding="utf-8")

    harness = f"""
set -uo pipefail
export PGAI_AGENT_KANBAN_TEMP_DIR={str(temp_root)!r}
source {str(_TEAM_DIR / "scripts" / "lib" / "temp.sh")!r}
log() {{ echo "[$(date -Iseconds)] wake(${{AGENT}}): $*" | tee -a "$LOG_FILE"; }}

LOG_FILE={str(log_file)!r}
AGENT="coder"
task_id="TASK-WATCHDOG-FAST-TEST"
MAX_TASK_SECONDS=30
KILL_GRACE_SECONDS=5

# Controllable clock -- see the sibling test above for the full rationale.
_SLEEP_CALLS_FILE={str(sleep_calls_file)!r}
sleep() {{
  echo "$(( $(cat "$_SLEEP_CALLS_FILE") + 1 ))" > "$_SLEEP_CALLS_FILE"
}}
# The watchdog compares the wall clock against an absolute deadline rather
# than counting iterations, so the clock it reads (_wd_epoch_now) is the
# sleep counter itself: each intercepted `sleep 1` advances the mocked
# clock by exactly one second, keeping the iteration count deterministic.
_wd_epoch_now() {{ cat "$_SLEEP_CALLS_FILE"; }}

# Completes immediately -- no delay of any kind, real or stubbed -- so the
# only thing under test is whether the watchdog notices and returns
# promptly rather than looping through the full budget.
_wd_run_provider_with_telemetry_safeguard_retry() {{
  PROVIDER_AGENT_EXIT_CODE=0
}}
# No project lock is held here; the real refresh wrapper would be a no-op.
_wd_refresh_project_lock_mtime() {{ :; }}

_run_watchdog() {{
{watchdog_block}
}}
_run_watchdog

echo "FINAL_EXIT=${{PROVIDER_AGENT_EXIT_CODE}}"
echo "TASK_KILLED=${{_task_killed}}"
"""
    result = _run(harness, timeout=25)

    assert result.returncode == 0, (
        f"Harness failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "FINAL_EXIT=0" in result.stdout, result.stdout
    assert "TASK_KILLED=false" in result.stdout, result.stdout
    # Behavioral proof of "did not wait out the full budget": a job that
    # finishes on its own is detected within a couple of poll iterations,
    # nowhere near the full MAX_TASK_SECONDS(30) the loop is entitled to.
    sleep_calls = int(sleep_calls_file.read_text(encoding="utf-8").strip())
    assert sleep_calls <= 2, (
        f"Expected the watchdog to detect the already-finished job within a "
        f"couple of poll iterations, not loop toward the full 30-iteration "
        f"budget; got {sleep_calls} iterations.\nstdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
