"""
test_transient_retry_record.py
===============================
Behavioral fixtures for BUG-0107: unify transient retry state into one
durable machine-readable record.

Prior to this fix, transient-retry state had two uncoordinated authorities:
  - wake's prose "retry N/M" embedded in ## Blocked Reason, erased by
    promote_blocked_to_backlog on every BACKLOG promotion.
  - OVERWATCH's separate ## Transient Requeue Count field with its own
    (different) ceiling.
An off-by-one in the prose path (`retry_count <= cap` allowed Needs Human=no
at exactly the cap, while the recheck path only promoted while
`retry_count < cap`) could leave a task BLOCKED + Needs Human=no forever —
a limbo state with no path back to BACKLOG and no human escalation.

This module exercises the fix directly against the real production
functions in wake_common.sh (handle_working_after_exit,
promote_blocked_to_backlog) and the shared durable-record substrate in
overwatch_protocol.sh (transient_retry_field / transient_retry_write /
transient_retry_limit), extracted via the same awk brace-matching pattern
test_census_closure.py established — this avoids the heavy top-level
bootstrap wake_common.sh performs when fully sourced, while still exercising
the genuine production function bodies (not a Python reimplementation).

Fixtures exercised (mirrors the task's Acceptance Criteria):
  (A) Four consecutive transient failures across separate "wake invocations"
      (four independent calls to handle_working_after_exit against the same
      status.md) drive retry_count to the ceiling monotonically — never
      resetting — and the task ends BLOCKED + Needs Human=yes.
  (B) Promotion to BACKLOG (promote_blocked_to_backlog) preserves the
      durable ## Transient Retry record even though it resets
      ## Blocked Reason to "none".
  (C) Exactly-at-cap is BLOCKED + Needs Human=yes — never a limbo state
      (BLOCKED + Needs Human=no with no further path back to BACKLOG).

Naming: function names describe behavior under test, never bug IDs or task
IDs (SOP.md "Test Authoring Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import textwrap

import pytest

from tests.fixtures.log_stub import make_log_stub_fragment

# ---------------------------------------------------------------------------
# Paths (relative to team/, which is the CWD when run via run-unit-tests.sh)
# ---------------------------------------------------------------------------

_WAKE_COMMON_LIB = pathlib.Path("scripts/lib/wake_common.sh")
_OVERWATCH_PROTOCOL_LIB = pathlib.Path("scripts/lib/overwatch_protocol.sh")


# ---------------------------------------------------------------------------
# Function extraction (mirrors test_census_closure.py's pattern): pull named
# top-level function bodies out of wake_common.sh via awk brace-matching, so
# tests exercise the real production functions without paying for
# wake_common.sh's full top-level bootstrap (which requires AGENT, TEAM_ROOT,
# SCRIPT_DIR, provider_invoke_agent, and a real kanban.cfg on disk).
# ---------------------------------------------------------------------------


def _extract_functions_snippet(func_names: list[str]) -> str:
    """Return a bash snippet that extracts and loads the named top-level
    functions from wake_common.sh via awk brace-counting.

    Args:
        func_names: function names to extract, e.g. ["set_state", "set_block"].

    Returns:
        A bash fragment (`eval "$(awk ...)"`) that defines the named
        functions in the calling shell, using their real bodies verbatim.
    """
    alternation = "|".join(re.escape(name) for name in func_names)
    return textwrap.dedent(f"""\
        eval "$(awk '
            /^({alternation})\\(\\)/ {{ found=1; depth=0 }}
            found {{
                print
                n = split($0, a, "")
                for (i = 1; i <= n; i++) {{
                    if (a[i] == "{{") depth++
                    if (a[i] == "}}") {{
                        depth--
                        if (depth == 0) {{ found=0 }}
                    }}
                }}
            }}
        ' '{_WAKE_COMMON_LIB}')"
    """)


def _build_status_file(tmp_path: pathlib.Path, task_id: str) -> pathlib.Path:
    """Write a minimal real-shaped status.md (mirrors the CODER template)."""
    task_dir = tmp_path / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    status_file = task_dir / "status.md"
    status_file.write_text(
        textwrap.dedent(f"""\
            # Status

            ## Task
            {task_id}

            ## State
            WORKING

            ## Summary
            In progress.

            ## Artifacts
            none

            ## Blockers
            none

            ## Blocked By Agent
            none

            ## Blocked Reason
            none

            ## Transient Retry
            none

            ## Needs Human
            no

            ## Next Recommended Step
            TBD

            ## Instruction Conflicts
            none
        """),
        encoding="utf-8",
    )
    return status_file


def _write_transient_log(tmp_path: pathlib.Path, name: str, signature: str) -> pathlib.Path:
    """Write a synthetic agent log file whose tail contains a transient signature."""
    log_file = tmp_path / name
    log_file.write_text(
        f"Agent started.\nProcessing...\n{signature}\nAgent exited.\n",
        encoding="utf-8",
    )
    return log_file


def _script_header(log_file: pathlib.Path, extra_functions: list[str] | None = None) -> str:
    """Bash header shared by every test in this module.

    Sources the real overwatch_protocol.sh (transient_retry_field /
    transient_retry_write / transient_retry_limit — BUG-0107's shared
    substrate), the faithful log() stub, and the real
    normalize_status_file/set_state/set_block/handle_working_after_exit
    (plus any extra_functions requested) extracted from wake_common.sh.
    """
    funcs = ["normalize_status_file", "set_state", "set_block", "handle_working_after_exit"]
    if extra_functions:
        funcs += [f for f in extra_functions if f not in funcs]
    return textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        source {_OVERWATCH_PROTOCOL_LIB}

        {make_log_stub_fragment(log_file)}

        {_extract_functions_snippet(funcs)}
    """)


def _run(script: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=timeout)


def _read(status_file: pathlib.Path) -> str:
    return status_file.read_text(encoding="utf-8")


def _field(status_text: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}\s*\n(.*?)(\n+##|\Z)", status_text, flags=re.S | re.M)
    assert m is not None, f"## {heading} section not found in:\n{status_text}"
    return m.group(1).strip()


def _record_field(status_text: str, key: str) -> str | None:
    body = _field(status_text, "Transient Retry")
    if body.strip().lower() == "none":
        return None
    m = re.search(rf"^{re.escape(key)}:\s*(.*)$", body, flags=re.M)
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------------------
# (A) Four consecutive transient failures monotonically drive the count to
#     the ceiling, then Needs Human=yes.
# ---------------------------------------------------------------------------


def test_four_consecutive_transient_failures_reach_ceiling_and_escalate(
    tmp_path: pathlib.Path,
) -> None:
    """Four separate handle_working_after_exit invocations (simulating four
    separate wake firings against the same task) drive retry_count to the
    configured ceiling monotonically — never resetting — and the fourth
    invocation (exactly at the cap) escalates to Needs Human=yes.

    TRANSIENT_RETRY_CAP=4 is used explicitly so the fixture exercises exactly
    the boundary described in the acceptance criterion: the count must never
    reset across invocations, and Needs Human flips to yes exactly when the
    ceiling is reached (not one invocation later, not one invocation early).
    """
    task_id = "CODER-20260101-010-fixture-a"
    status_file = _build_status_file(tmp_path, task_id)
    log_file = tmp_path / "wake.log"
    header = _script_header(log_file)

    observed_counts: list[int] = []
    observed_needs_human: list[str] = []

    for i in range(1, 5):
        agent_log = _write_transient_log(tmp_path, f"agent-{i}.log", "API Error: 503 Service Unavailable")
        script = header + textwrap.dedent(f"""\
            export TRANSIENT_RETRY_CAP=4
            handle_working_after_exit "{task_id}" "{status_file}" "{agent_log}" 1
        """)
        result = _run(script)
        assert result.returncode == 0, (
            f"invocation {i} failed\nstdout={result.stdout}\nstderr={result.stderr}"
        )
        assert result.stdout.strip().splitlines()[-1] == "BLOCKED", (
            f"invocation {i}: expected final_state BLOCKED on stdout, got: {result.stdout!r}"
        )

        status_text = _read(status_file)
        assert _field(status_text, "State") == "BLOCKED", (
            f"invocation {i}: State should be BLOCKED\n{status_text}"
        )
        count = _record_field(status_text, "retry_count")
        assert count is not None, f"invocation {i}: retry_count missing\n{status_text}"
        observed_counts.append(int(count))
        observed_needs_human.append(_field(status_text, "Needs Human").lower())

    # Monotonic, never resetting: 1, 2, 3, 4.
    assert observed_counts == [1, 2, 3, 4], (
        f"retry_count must increase monotonically across invocations, got {observed_counts}"
    )
    # Needs Human=no while strictly below the ceiling; yes exactly at the ceiling.
    assert observed_needs_human == ["no", "no", "no", "yes"], (
        f"Needs Human must stay 'no' below the ceiling and flip to 'yes' exactly "
        f"at the ceiling (no limbo), got {observed_needs_human}"
    )


# ---------------------------------------------------------------------------
# (B) Promotion to BACKLOG preserves the durable retry record.
# ---------------------------------------------------------------------------


def test_backlog_promotion_preserves_transient_retry_record(tmp_path: pathlib.Path) -> None:
    """promote_blocked_to_backlog resets ## Blocked Reason to "none" (existing
    behavior, unaffected) but must NOT erase the durable ## Transient Retry
    record — the record is what makes retry counting survive BACKLOG
    promotion (BUG-0107's core fix; before this, the count lived only in
    ## Blocked Reason prose and was wiped on every promotion).
    """
    task_id = "CODER-20260101-011-fixture-b"
    status_file = _build_status_file(tmp_path, task_id)
    log_file = tmp_path / "wake.log"
    header = _script_header(log_file, extra_functions=["promote_blocked_to_backlog"])

    agent_log = _write_transient_log(tmp_path, "agent.log", "529 Overloaded")

    # First: a transient failure is classified, writing the durable record.
    classify_script = header + textwrap.dedent(f"""\
        export TRANSIENT_RETRY_CAP=3
        handle_working_after_exit "{task_id}" "{status_file}" "{agent_log}" 1
    """)
    result = _run(classify_script)
    assert result.returncode == 0, f"classification failed\nstderr={result.stderr}"

    pre_promotion_text = _read(status_file)
    pre_count = _record_field(pre_promotion_text, "retry_count")
    assert pre_count == "1", f"expected retry_count=1 before promotion\n{pre_promotion_text}"
    assert _field(pre_promotion_text, "Blocked Reason") != "none", (
        "Blocked Reason should carry a human-readable narrative before promotion"
    )

    # Build a minimal BACKLOG queue file with this task marked [B], as
    # promote_blocked_to_backlog expects.
    backlog_file = tmp_path / "coder_backlog.md"
    backlog_file.write_text(f"# Coder Backlog\n\n- [B] {task_id}\n", encoding="utf-8")

    promote_script = header + textwrap.dedent(f"""\
        BACKLOG="{backlog_file}"
        promote_blocked_to_backlog "{task_id}" "{status_file}" "transient API error auto-retry (attempt 1/3)"
    """)
    result = _run(promote_script)
    assert result.returncode == 0, f"promotion failed\nstdout={result.stdout}\nstderr={result.stderr}"

    post_promotion_text = _read(status_file)
    assert _field(post_promotion_text, "State") == "BACKLOG", (
        f"State should be BACKLOG after promotion\n{post_promotion_text}"
    )
    # Blocked Reason IS reset — that is expected, existing behavior.
    assert _field(post_promotion_text, "Blocked Reason") == "none", (
        f"Blocked Reason should be reset to none on promotion\n{post_promotion_text}"
    )
    # But the durable retry record must survive, byte-for-byte on the count.
    post_count = _record_field(post_promotion_text, "retry_count")
    assert post_count == "1", (
        f"Durable ## Transient Retry record must survive BACKLOG promotion "
        f"(BUG-0107); expected retry_count=1, got {post_count!r}\n{post_promotion_text}"
    )
    assert _record_field(post_promotion_text, "reason_code") == "transient_api_error", (
        f"reason_code should survive promotion unchanged\n{post_promotion_text}"
    )

    # Confirm the queue marker flip happened too (sanity: real function ran).
    backlog_text = backlog_file.read_text(encoding="utf-8")
    assert "[ ]" in backlog_text and "[B]" not in backlog_text, (
        f"Queue marker should flip from [B] to [ ]\n{backlog_text}"
    )


# ---------------------------------------------------------------------------
# (C) Exactly-at-cap is BLOCKED + Needs Human=yes — never a limbo state.
# ---------------------------------------------------------------------------


def test_exactly_at_cap_is_blocked_and_needs_human_no_limbo(tmp_path: pathlib.Path) -> None:
    """A task whose retry_count is already exactly at the ceiling BEFORE this
    invocation (simulating a task that was somehow re-classified once more at
    the boundary) is escalated to Needs Human=yes on this invocation, not left
    at Needs Human=no. This is the literal off-by-one BUG-0107 fixed: the old
    code used `retry_count <= cap` (allowing Needs Human=no AT the cap) while
    the recheck path used `retry_count < cap` (refusing to promote AT the
    cap) — the two disagreed exactly at the boundary, producing a limbo state
    (BLOCKED, Needs Human=no, never promoted again). The fixed code uses the
    same `< cap` comparison in both places, so they can never disagree.
    """
    task_id = "CODER-20260101-012-fixture-c"
    status_file = _build_status_file(tmp_path, task_id)
    # Seed the record at count=2, one shy of a cap of 3 (as if two prior
    # invocations already ran) so this third invocation lands exactly at cap.
    status_file.write_text(
        status_file.read_text(encoding="utf-8").replace(
            "## Transient Retry\nnone",
            "## Transient Retry\n"
            "reason_code: transient_api_error\n"
            "retry_count: 2\n"
            "retry_limit: 3\n"
            "provider: claude\n"
            "last_error: API Error: 503 Service Unavailable",
        ),
        encoding="utf-8",
    )

    log_file = tmp_path / "wake.log"
    header = _script_header(log_file)
    agent_log = _write_transient_log(tmp_path, "agent.log", "API Error: 503 Service Unavailable")

    script = header + textwrap.dedent(f"""\
        export TRANSIENT_RETRY_CAP=3
        handle_working_after_exit "{task_id}" "{status_file}" "{agent_log}" 1
    """)
    result = _run(script)
    assert result.returncode == 0, f"invocation failed\nstderr={result.stderr}"

    status_text = _read(status_file)
    assert _field(status_text, "State") == "BLOCKED", f"State should be BLOCKED\n{status_text}"
    assert _record_field(status_text, "retry_count") == "3", (
        f"retry_count should reach exactly the cap (3)\n{status_text}"
    )
    assert _field(status_text, "Needs Human").lower() == "yes", (
        f"Exactly-at-cap must be Needs Human=yes — no limbo state\n{status_text}"
    )


# ---------------------------------------------------------------------------
# (D) Transient signature detection survives a pipe-buffer-exceeding tail
#     window deterministically (BUG-0038's rapid-fire increment race).
# ---------------------------------------------------------------------------


def _write_transient_log_exceeding_pipe_buffer(tmp_path: pathlib.Path, name: str, signature: str) -> pathlib.Path:
    """Write an agent log whose last-50-lines tail window is large enough to
    exceed a Linux pipe's default buffer capacity (65536 bytes), with the
    transient signature on the FIRST of those 50 lines.

    This deterministically (not load-dependently) reproduces the exact race
    class behind BUG-0038: `handle_working_after_exit` used to detect the
    signature via `echo "$_tail_lines" | grep -qiE "$_DETECT_RE"`. Piping
    into `grep -q` forks a separate writer (echo) whose output grep may not
    fully drain — `grep -q` exits the instant it finds a match, which here
    happens on line 1, long before echo has written the remaining ~49
    padded lines. Under `set -o pipefail` (active in every caller), if echo
    is still blocked writing into a full pipe buffer when grep's exit closes
    the read end, echo receives SIGPIPE and the pipeline's reported exit
    status becomes echo's failure code instead of grep's real (successful)
    match — so detection silently reports "no signature found" even though
    the tail plainly contains one. A payload sized past the pipe buffer
    forces this outcome on every run, independent of system load or
    scheduling luck (whereas a short payload only races intermittently under
    contention — see the ~0.8%-under-load figure recorded in the fix commit).
    """
    lines = [signature]
    lines += [f"filler line {i} " + ("padding " * 400) for i in range(49)]
    log_file = tmp_path / name
    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log_file


def test_transient_signature_detected_when_tail_window_exceeds_pipe_buffer(
    tmp_path: pathlib.Path,
) -> None:
    """A transient signature on the first line of a large (>64KB) tail-50
    window is detected and increments retry_count, exactly as a short-log
    signature would. Before the fix, this scenario deterministically failed:
    the early-match-then-large-remaining-write shape guarantees the pipe
    write races grep's early exit under `set -o pipefail`, so the pre-fix
    `echo | grep -q` detection silently reported no match on every run
    (not just intermittently) and the task fell through to the generic
    "agent exited without updating state" branch instead of the transient
    retry path — dropping the retry_count increment entirely.
    """
    task_id = "CODER-20260101-013-fixture-d"
    status_file = _build_status_file(tmp_path, task_id)
    log_file = tmp_path / "wake.log"
    header = _script_header(log_file)
    agent_log = _write_transient_log_exceeding_pipe_buffer(
        tmp_path, "agent-big.log", "API Error: 503 Service Unavailable"
    )

    script = header + textwrap.dedent(f"""\
        export TRANSIENT_RETRY_CAP=4
        handle_working_after_exit "{task_id}" "{status_file}" "{agent_log}" 1
    """)
    result = _run(script)
    assert result.returncode == 0, f"invocation failed\nstderr={result.stderr}"
    assert result.stdout.strip().splitlines()[-1] == "BLOCKED", (
        f"expected final_state BLOCKED on stdout, got: {result.stdout!r}"
    )

    status_text = _read(status_file)
    assert _field(status_text, "State") == "BLOCKED", f"State should be BLOCKED\n{status_text}"
    assert _record_field(status_text, "retry_count") == "1", (
        f"transient signature on a large tail window must still be detected and "
        f"increment retry_count to 1 — a dropped detection leaves the durable "
        f"record at 'none' instead\n{status_text}"
    )
    assert _record_field(status_text, "reason_code") == "transient_api_error", (
        f"reason_code should be recorded once the signature is detected\n{status_text}"
    )
    assert _field(status_text, "Needs Human").lower() == "no", (
        f"first transient failure (retry 1/4) must stay Needs Human=no\n{status_text}"
    )
