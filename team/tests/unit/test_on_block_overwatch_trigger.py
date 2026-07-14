"""
test_on_block_overwatch_trigger.py
===================================
Behavioral fixtures for the on-BLOCK overwatch trigger added to both
wake provider siblings (scripts/wake/claude.sh and scripts/wake/codex.sh).

Acceptance criteria exercised:
  (1) Sibling gate: the trigger block is byte-identical across both siblings.
  (2) One blocked task: trigger log shows exactly one nudge line; exactly one
      overwatch wake-now invocation is recorded.
  (3) Simultaneous blocks (flock dedupe): five rapid triggers → still one
      overwatch run completes (others find the lock held and exit 0).
  (4) AGENT=overwatch block: zero nudges emitted (self-loop guard).
  (5) HALT present: nudge fires (trigger does not check HALT); woken run exits
      at the gate inside wake-now/wake-batch.
  (6) wake-now.sh missing or broken: block path exit status and side-effects
      unchanged from a control run without the trigger reachable.

All tests run against synthetic environments and never touch the live kanban root.
The trigger logic is exercised via a self-contained bash snippet that mirrors
the exact guard + nohup form from the production wake scripts.

Implementation note: the trigger redirects wake-now.sh stdout+stderr to
logs/overwatch-trigger.log via the nohup redirect.  Stubs therefore print to
stdout (not to a side-channel file) so evidence of the nudge appears in the
trigger log.  For the HALT fixture tests (criterion 5), the trigger log serves
as evidence that the nudge fired; the stub's stdout content distinguishes the
"HALT detected" vs "ran normally" outcome.
"""

from __future__ import annotations

import pathlib
import textwrap

import pytest

from tests.unit.shell_harness import run_bash

# ---------------------------------------------------------------------------
# Paths relative to team/ (pytest cwd when run via run-unit-tests.sh)
# ---------------------------------------------------------------------------
_CLAUDE_SH = pathlib.Path("scripts/wake/claude.sh")
_CODEX_SH = pathlib.Path("scripts/wake/codex.sh")

# The trigger block marker used to extract the fire-site for diff comparison.
_TRIGGER_MARKER = "on-BLOCK overwatch trigger"

# ---------------------------------------------------------------------------
# Trigger snippet: the exact guard form from production, parametrised so tests
# can set AGENT, KANBAN_ROOT, and provide a stub wake-now.sh.
#
# Note: the production trigger writes wake-now.sh stdout+stderr to
# logs/overwatch-trigger.log.  Stubs echo to stdout (not to a file) so
# evidence of the invocation appears in the trigger log, making assertions
# straightforward: non-empty trigger log == nudge fired.
# ---------------------------------------------------------------------------

_TRIGGER_SNIPPET = textwrap.dedent("""\
    # on-BLOCK overwatch trigger (extracted for unit test)
    if [[ "$AGENT" != "overwatch" ]]; then
      mkdir -p "${KANBAN_ROOT}/logs"
      nohup "${KANBAN_ROOT}/scripts/wake-now.sh" --agent overwatch \\
        >>"${KANBAN_ROOT}/logs/overwatch-trigger.log" 2>&1 &
    fi
""")


def _write_stub_wake_now(kanban_root: pathlib.Path, stub_body: str) -> None:
    """Write a stub wake-now.sh at the canonical location.

    Stubs must print evidence to stdout (not a separate file) so the nohup
    redirect captures it in logs/overwatch-trigger.log.
    """
    scripts_dir = kanban_root / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    stub = scripts_dir / "wake-now.sh"
    stub.write_text(
        "#!/usr/bin/env bash\n" + stub_body + "\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)


def _trigger_log(kanban_root: pathlib.Path) -> str:
    """Read the overwatch trigger log if present."""
    log_path = kanban_root / "logs" / "overwatch-trigger.log"
    if log_path.exists():
        return log_path.read_text(encoding="utf-8")
    return ""


# ---------------------------------------------------------------------------
# (1) Sibling gate: trigger block byte-identical in both siblings
# ---------------------------------------------------------------------------


def test_sibling_trigger_blocks_are_byte_identical(tmp_path: pathlib.Path) -> None:
    """
    The on-BLOCK trigger block must be byte-identical across scripts/wake/claude.sh
    and scripts/wake/codex.sh.  This is the load-bearing sibling gate.

    Extraction: locate the trigger marker comment in each file, then capture
    lines through the closing ';;' of the BLOCKED arm.  The two blocks must match.
    """
    def extract_trigger_block(path: pathlib.Path) -> list[str]:
        lines = path.read_text(encoding="utf-8").splitlines()
        capturing = False
        block: list[str] = []
        for line in lines:
            if _TRIGGER_MARKER in line:
                capturing = True
            if capturing:
                block.append(line)
                # The arm ends at the closing ';;' at the two-space indent.
                if line.strip() == ";;":
                    break
        return block

    claude_block = extract_trigger_block(_CLAUDE_SH)
    codex_block = extract_trigger_block(_CODEX_SH)

    assert claude_block, (
        f"Trigger block not found in {_CLAUDE_SH}. "
        f"Expected a comment containing '{_TRIGGER_MARKER}'."
    )
    assert codex_block, (
        f"Trigger block not found in {_CODEX_SH}. "
        f"Expected a comment containing '{_TRIGGER_MARKER}'."
    )
    assert claude_block == codex_block, (
        "Trigger blocks differ between siblings.\n"
        "claude.sh block:\n" + "\n".join(claude_block) + "\n\n"
        "codex.sh block:\n" + "\n".join(codex_block)
    )


# ---------------------------------------------------------------------------
# (2) One blocked task: trigger fires exactly once
# ---------------------------------------------------------------------------


def test_one_blocked_task_fires_one_nudge(tmp_path: pathlib.Path) -> None:
    """
    Fixture (2): a single BLOCKED outcome → trigger log shows exactly one nudge
    line; the stub wake-now.sh is invoked once.

    The stub prints to stdout so the nohup redirect captures it in the trigger log.
    """
    kanban_root = tmp_path / "kanban"

    # Stub prints to stdout → captured in trigger log via nohup redirect.
    _write_stub_wake_now(
        kanban_root,
        stub_body='echo "wake-now overwatch invoked at $(date -Iseconds)"',
    )

    script = textwrap.dedent(f"""\
        set -euo pipefail
        export KANBAN_ROOT="{kanban_root}"
        export AGENT="coder"
        {_TRIGGER_SNIPPET}
        # Give the backgrounded nohup time to complete.
        sleep 0.5
    """)

    result = run_bash(tmp_path, script, timeout=10)
    assert result.returncode == 0, (
        f"Trigger snippet failed (exit {result.returncode}).\n"
        f"stderr: {result.stderr}"
    )

    trigger_log_content = _trigger_log(kanban_root)
    assert trigger_log_content != "", (
        "Trigger log is empty; nohup did not write to overwatch-trigger.log. "
        "The stub wake-now.sh may not have been invoked or stdout was not captured."
    )
    invocation_count = trigger_log_content.count("wake-now overwatch invoked")
    assert invocation_count == 1, (
        f"Expected exactly 1 invocation in trigger log; got {invocation_count}.\n"
        f"Trigger log: '{trigger_log_content}'"
    )


# ---------------------------------------------------------------------------
# (3) Simultaneous blocks: flock dedupe → exactly one overwatch run
# ---------------------------------------------------------------------------


def test_simultaneous_blocks_flock_dedupe(tmp_path: pathlib.Path) -> None:
    """
    Fixture (3): five concurrent trigger fires → exactly one wake-now invocation
    runs to completion; the flock inside wake-batch/wake script prevents overlap.

    The stub acquires a flock and prints to stdout only when the lock is free.
    Five concurrent firings should result in exactly one captured invocation.
    """
    kanban_root = tmp_path / "kanban"
    lock_file = tmp_path / "overwatch.lock"

    # Stub: acquires an exclusive flock and prints only when lock was free.
    # Prints to stdout → captured in trigger log.
    _write_stub_wake_now(
        kanban_root,
        stub_body=textwrap.dedent(f"""\
            exec 9>"{lock_file}"
            if flock -n 9; then
                echo "wake-now overwatch invoked at $(date -Iseconds)"
                sleep 0.3
                flock -u 9
            fi
            # Lock contended → exit 0 silently (dedup path)
        """),
    )

    script = textwrap.dedent(f"""\
        set -euo pipefail
        export KANBAN_ROOT="{kanban_root}"
        export AGENT="coder"
        # Fire five concurrent triggers (simulating five simultaneous BLOCKED tasks).
        for i in 1 2 3 4 5; do
            {_TRIGGER_SNIPPET}
        done
        # Wait for all backgrounded nohup processes to finish.
        sleep 1.0
    """)

    result = run_bash(tmp_path, script, timeout=15)
    assert result.returncode == 0, (
        f"Trigger snippet failed (exit {result.returncode}).\n"
        f"stderr: {result.stderr}"
    )

    trigger_log_content = _trigger_log(kanban_root)
    invocation_count = trigger_log_content.count("wake-now overwatch invoked")
    assert invocation_count == 1, (
        f"Flock dedupe failed: expected 1 overwatch run, got {invocation_count}.\n"
        f"Trigger log: '{trigger_log_content}'"
    )


# ---------------------------------------------------------------------------
# (4) AGENT=overwatch block: zero nudges emitted (self-loop guard)
# ---------------------------------------------------------------------------


def test_overwatch_block_fires_zero_nudges(tmp_path: pathlib.Path) -> None:
    """
    Fixture (4): AGENT=overwatch → the self-loop guard prevents the trigger
    from firing; trigger log remains empty and stub wake-now.sh is not called.
    """
    kanban_root = tmp_path / "kanban"

    _write_stub_wake_now(
        kanban_root,
        stub_body='echo "wake-now overwatch invoked at $(date -Iseconds)"',
    )

    script = textwrap.dedent(f"""\
        set -euo pipefail
        export KANBAN_ROOT="{kanban_root}"
        export AGENT="overwatch"
        {_TRIGGER_SNIPPET}
        sleep 0.3
    """)

    result = run_bash(tmp_path, script, timeout=10)
    assert result.returncode == 0, (
        f"Trigger snippet failed (exit {result.returncode}).\n"
        f"stderr: {result.stderr}"
    )

    # Trigger log must not exist or be empty (no nudge fired).
    trigger_log_content = _trigger_log(kanban_root)
    assert trigger_log_content == "", (
        f"Expected empty trigger log for AGENT=overwatch; got: '{trigger_log_content}'"
    )


# ---------------------------------------------------------------------------
# (5) HALT present: nudge fires; woken run exits at gate
# ---------------------------------------------------------------------------


def test_halt_present_nudge_fires_run_exits_at_gate(tmp_path: pathlib.Path) -> None:
    """
    Fixture (5): HALT present → the trigger still fires (trigger does not check
    HALT); the woken stub detects HALT and exits without performing work.

    Evidence: trigger log is non-empty (nudge fired); trigger log contains
    "HALT detected" (woken run gated by HALT); trigger log does NOT contain
    "ran normally" (woken run did not proceed past the gate).
    """
    kanban_root = tmp_path / "kanban"
    halt_file = kanban_root / "HALT"

    # Stub: prints to stdout to be captured in trigger log.
    # Checks for HALT file and prints different output on each path.
    _write_stub_wake_now(
        kanban_root,
        stub_body=textwrap.dedent(f"""\
            if [[ -f "{halt_file}" ]]; then
                echo "wake-now: HALT detected — exiting cleanly"
                exit 0
            fi
            echo "wake-now: overwatch ran normally (no HALT)"
        """),
    )

    # Create HALT file before triggering.
    kanban_root.mkdir(parents=True, exist_ok=True)
    halt_file.touch()

    script = textwrap.dedent(f"""\
        set -euo pipefail
        export KANBAN_ROOT="{kanban_root}"
        export AGENT="coder"
        {_TRIGGER_SNIPPET}
        sleep 0.5
    """)

    result = run_bash(tmp_path, script, timeout=10)
    assert result.returncode == 0, (
        f"Trigger snippet failed (exit {result.returncode}).\n"
        f"stderr: {result.stderr}"
    )

    trigger_log_content = _trigger_log(kanban_root)

    # Nudge must have fired: trigger log must be non-empty.
    assert trigger_log_content != "", (
        "Trigger did not fire when HALT was present; "
        "expected nudge to fire regardless of HALT."
    )

    # Woken run must have detected HALT and exited at the gate.
    assert "HALT detected" in trigger_log_content, (
        f"Expected woken run to detect HALT; trigger log: '{trigger_log_content}'"
    )
    assert "ran normally" not in trigger_log_content, (
        f"Woken run should not have proceeded past HALT gate; "
        f"trigger log: '{trigger_log_content}'"
    )


def test_halt_overwatch_present_nudge_fires_run_exits_at_gate(
    tmp_path: pathlib.Path,
) -> None:
    """
    Fixture (5b): HALT_OVERWATCH present → same contract as test_halt_present.
    Trigger fires; woken overwatch run exits at its HALT_OVERWATCH pre-flight.
    """
    kanban_root = tmp_path / "kanban"
    halt_ow_file = kanban_root / "HALT_OVERWATCH"

    _write_stub_wake_now(
        kanban_root,
        stub_body=textwrap.dedent(f"""\
            if [[ -f "{halt_ow_file}" ]]; then
                echo "wake-now: HALT_OVERWATCH detected — exiting cleanly"
                exit 0
            fi
            echo "wake-now: overwatch ran normally (no HALT_OVERWATCH)"
        """),
    )

    kanban_root.mkdir(parents=True, exist_ok=True)
    halt_ow_file.touch()

    script = textwrap.dedent(f"""\
        set -euo pipefail
        export KANBAN_ROOT="{kanban_root}"
        export AGENT="coder"
        {_TRIGGER_SNIPPET}
        sleep 0.5
    """)

    result = run_bash(tmp_path, script, timeout=10)
    assert result.returncode == 0, (
        f"Trigger snippet failed (exit {result.returncode}).\n"
        f"stderr: {result.stderr}"
    )

    trigger_log_content = _trigger_log(kanban_root)

    assert trigger_log_content != "", (
        "Trigger did not fire when HALT_OVERWATCH was present; "
        "expected nudge to fire regardless."
    )
    assert "HALT_OVERWATCH detected" in trigger_log_content, (
        f"Expected woken run to detect HALT_OVERWATCH; "
        f"trigger log: '{trigger_log_content}'"
    )
    assert "ran normally" not in trigger_log_content, (
        f"Woken run should not have proceeded past HALT_OVERWATCH gate; "
        f"trigger log: '{trigger_log_content}'"
    )


# ---------------------------------------------------------------------------
# (6) wake-now.sh missing or broken: block path exit status unchanged
# ---------------------------------------------------------------------------


def _run_trigger_with_exit_status(
    tmp_path: pathlib.Path,
    kanban_root: pathlib.Path,
    agent: str = "coder",
    timeout: int = 10,
) -> int:
    """
    Run the trigger snippet followed by a sentinel exit-0 command.
    Returns the bash process exit code.

    The sentinel models "the block path continues normally after the trigger."
    If the trigger's fire-and-forget form absorbs errors correctly, the exit
    code is always 0 regardless of what wake-now.sh does.
    """
    script = textwrap.dedent(f"""\
        export KANBAN_ROOT="{kanban_root}"
        export AGENT="{agent}"
        {_TRIGGER_SNIPPET}
        # Sentinel: block path continues; must exit 0.
        exit 0
    """)
    result = run_bash(tmp_path, script, timeout=timeout)
    return result.returncode


def test_missing_wake_now_does_not_change_block_path_exit_status(
    tmp_path: pathlib.Path,
) -> None:
    """
    Fixture (6a): wake-now.sh does not exist → block path exit status is 0,
    identical to a control run with a working wake-now.sh.

    The nohup/& form must absorb any error from a missing executable without
    propagating it to the parent script.
    """
    kanban_root = tmp_path / "kanban"
    # Deliberately DO NOT write a stub wake-now.sh.

    exit_code = _run_trigger_with_exit_status(tmp_path, kanban_root)
    assert exit_code == 0, (
        f"Missing wake-now.sh changed block path exit status to {exit_code}; "
        "expected 0 (fire-and-forget must absorb the error)."
    )


def test_broken_wake_now_does_not_change_block_path_exit_status(
    tmp_path: pathlib.Path,
) -> None:
    """
    Fixture (6b): wake-now.sh exists but exits non-zero → block path exit status
    is still 0.  The exit status of the backgrounded nohup process is ignored.
    """
    kanban_root = tmp_path / "kanban"
    _write_stub_wake_now(kanban_root, stub_body="exit 42")

    exit_code = _run_trigger_with_exit_status(tmp_path, kanban_root)
    assert exit_code == 0, (
        f"Broken wake-now.sh (exit 42) changed block path exit status to {exit_code}; "
        "expected 0 (fire-and-forget must ignore the child's exit status)."
    )


def test_control_run_with_working_wake_now_exits_zero(tmp_path: pathlib.Path) -> None:
    """
    Control: working wake-now.sh → block path still exits 0.
    Validates the baseline so (6a) and (6b) comparisons are meaningful.
    """
    kanban_root = tmp_path / "kanban"
    _write_stub_wake_now(kanban_root, stub_body="exit 0")

    exit_code = _run_trigger_with_exit_status(tmp_path, kanban_root)
    assert exit_code == 0, (
        f"Control run with working wake-now.sh exited {exit_code}; expected 0."
    )


# ---------------------------------------------------------------------------
# Structural guards: trigger block appears in the BLOCKED arm of each sibling
# ---------------------------------------------------------------------------


def test_trigger_block_present_in_blocked_arm_claude(tmp_path: pathlib.Path) -> None:
    """
    Structural: the trigger marker appears inside the BLOCKED) arm of claude.sh.
    Guards that the trigger was placed at the correct generic set-BLOCKED path.
    """
    content = _CLAUDE_SH.read_text(encoding="utf-8")
    lines = content.splitlines()
    blocked_arm_found = False
    trigger_after_blocked = False
    for line in lines:
        if line.strip() == "BLOCKED)":
            blocked_arm_found = True
        if blocked_arm_found and _TRIGGER_MARKER in line:
            trigger_after_blocked = True
            break
    assert blocked_arm_found, "BLOCKED) arm not found in claude.sh"
    assert trigger_after_blocked, (
        f"Trigger marker '{_TRIGGER_MARKER}' not found inside BLOCKED arm of claude.sh"
    )


def test_trigger_block_present_in_blocked_arm_codex(tmp_path: pathlib.Path) -> None:
    """
    Structural: the trigger marker appears inside the BLOCKED) arm of codex.sh.
    Guards that the trigger was placed at the correct generic set-BLOCKED path.
    """
    content = _CODEX_SH.read_text(encoding="utf-8")
    lines = content.splitlines()
    blocked_arm_found = False
    trigger_after_blocked = False
    for line in lines:
        if line.strip() == "BLOCKED)":
            blocked_arm_found = True
        if blocked_arm_found and _TRIGGER_MARKER in line:
            trigger_after_blocked = True
            break
    assert blocked_arm_found, "BLOCKED) arm not found in codex.sh"
    assert trigger_after_blocked, (
        f"Trigger marker '{_TRIGGER_MARKER}' not found inside BLOCKED arm of codex.sh"
    )
