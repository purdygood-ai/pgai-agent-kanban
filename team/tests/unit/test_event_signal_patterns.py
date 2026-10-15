"""
test_event_signal_patterns.py
==============================
Behavioral unit tests for team/scripts/dashboard/lib/event_signal_patterns.sh,
the single shared source of truth for classifying a wake-log line as SIGNAL
(worth an operator's glance) or BOILERPLATE (routine idle-cycle noise).

Coverage:
  - Sourcing the library under `set -euo pipefail` produces no output and no
    error (safe to source from any dashboard renderer).
  - dashboard_event_signal_is_signal classifies each of the documented
    SIGNAL classes true: agent dispatch, task-finished-with-state,
    non-zero completed count, HALT detection, non-zero promotion counts,
    worktree reaps, dual-channel error/message/remedy lines, and
    sweep-census skip lines with their stated reason.
  - dashboard_event_signal_is_signal classifies each of the documented
    BOILERPLATE classes false: WAITING/BLOCKED re-check lines, idle
    "no tasks"/"no BACKLOG" lines, sleep/stagger lines, timing lines, and
    zero-completed done-lines.
  - dashboard_event_signal_filter, fed a fixture mixing both classes,
    emits only the signal-class lines, in their original order.
  - dashboard_event_signal_collapse_key returns a collapse identity for a
    collapsing pattern (sweep-census skip) that is byte-identical across
    agent and timestamp, and differs when the named bundle or the stated
    reason differs; returns no identity for a non-collapsing pattern
    (agent dispatch) or a line matching no pattern at all.
"""

from __future__ import annotations

import pathlib

import pytest

from tests.unit.shell_harness import run_bash

_LIB = "scripts/dashboard/lib/event_signal_patterns.sh"

# ---------------------------------------------------------------------------
# Real wake-log line shapes, one per documented class. These mirror actual
# lines emitted by team/scripts/lib/wake_driver.sh, wake_driver_finalize.sh,
# and wake_task_state.sh (task IDs and timestamps here are fixture-only
# examples, not citations of any real run).
# ---------------------------------------------------------------------------

SIGNAL_LINES = [
    # Agent dispatch.
    "[2026-07-27T00:07:15+00:00] wake(coder): task CODER-20260727-001-example: transitioned to WORKING [A]",
    # Task-finished line carrying a task state.
    "[2026-07-27T00:21:28+00:00] wake(coder): finished task CODER-20260727-001-example with state DONE",
    # Done-line with a non-zero completed count.
    '[2026-07-27T00:03:32+00:00] wake(pm): project demo: done: completed=1 project_elapsed=331s batch_elapsed=331s reason="no more BACKLOG tasks in pm queue"',
    # HALT detection.
    "[2026-07-26T23:50:02+00:00] wake-claude(cm): HALT flag detected — exiting cleanly",
    "[2026-07-26T23:50:03+00:00] wake(pm): project demo: per-project HALT present, resolution stage evaluated, skipping task processing",
    # Non-zero promotion counts.
    "[2026-07-27T01:15:26+00:00] wake(cm): re-check complete: promoted 1 task(s) from WAITING to BACKLOG",
    "[2026-07-27T01:15:26+00:00] wake(cm): WAITING task CM-20260727-007-example: prerequisites satisfied, promoting to BACKLOG",
    # Reap of an abandoned worktree.
    "[2026-07-27T00:48:14+00:00] wake(coder): stale-active: CODER-20260727-003-example tearing down prior worktree at /tmp/example",
    "[2026-08-30T07:27:03+00:00] wake(pm): stale-active reconciliation: reset 1 orphaned [A] task(s)",
    # Dual-channel error/message/remedy block.
    "error:   lock.provenance.contended",
    "message: pgai_lock_acquire could not take the lock: it is already held",
    "remedy:  wait and retry, or inspect and clear the stale lock",
    # Sweep-census skip line with its stated reason.
    "[2026-07-26T23:58:05+00:00] wake(pm): sweep-census: skipped — /some/requirements.md: finalize=tag — not report",
]

BOILERPLATE_LINES = [
    # Re-checking WAITING/BLOCKED lines that found nothing.
    "[2026-07-26T23:46:02+00:00] wake(pm): re-checking WAITING tasks for satisfied prerequisites...",
    "[2026-07-26T23:46:02+00:00] wake(pm): no WAITING tasks to re-check",
    "[2026-07-26T23:46:02+00:00] wake(pm): re-checking BLOCKED tasks for cleared blockers...",
    "[2026-07-26T23:46:02+00:00] wake(pm): no BLOCKED tasks to re-check",
    # Idle 'no tasks'/'no BACKLOG' lines.
    "[2026-07-26T23:46:04+00:00] wake(pm): discovery_run_pipeline: no work pending, idle exit",
    "[2026-07-26T23:46:26+00:00] wake(cm): sweep-census: project demo: no running intake items",
    # Sleeping/stagger lines.
    "[2026-07-26T23:18:02+00:00] wake(cm): sleeping 24s before work (--sleep stagger)",
    # Timing lines.
    "[2026-07-26T23:18:02+00:00] wake(pm): timing: work_begin=1785107882 work_end=1785107882 work_delta=0s",
    # Zero-completed done-lines.
    '[2026-07-26T23:46:04+00:00] wake(pm): project demo: done: completed=0 project_elapsed=3s batch_elapsed=3s reason="no more BACKLOG tasks in pm queue"',
    "[2026-07-27T00:04:26+00:00] wake(cm): re-check complete: promoted 0 task(s) from WAITING to BACKLOG",
]


def _classify(tmp_path: pathlib.Path, line: str) -> bool:
    """Run dashboard_event_signal_is_signal on one line; return its verdict."""
    script = (
        f"source {_LIB} && "
        'dashboard_event_signal_is_signal "$1" && echo SIGNAL || echo BOILERPLATE'
    )
    result = run_bash(tmp_path, f'bash -c \'{script}\' _ {_shquote(line)}')
    assert result.returncode == 0, result.stderr
    return "SIGNAL" in result.stdout


def _collapse_key(tmp_path: pathlib.Path, line: str) -> tuple[str, int]:
    """Run dashboard_event_signal_collapse_key on one line.

    Returns (stdout with any trailing newline stripped, returncode). The
    accessor prints nothing and exits 1 when the line declares no collapse
    identity, so callers check the returncode rather than assume the
    empty string always means "no identity" (an identity is never empty
    in practice, but the returncode is the documented contract).
    """
    script = f"source {_LIB} && dashboard_event_signal_collapse_key \"$1\""
    result = run_bash(tmp_path, f'bash -c \'{script}\' _ {_shquote(line)}')
    return result.stdout.rstrip("\n"), result.returncode


def _shquote(value: str) -> str:
    """Single-quote a value for safe embedding in a bash -c argument list."""
    return "'" + value.replace("'", "'\\''") + "'"


def test_library_sources_cleanly_under_strict_mode(tmp_path: pathlib.Path) -> None:
    """Sourcing the library under set -euo pipefail must produce no output."""
    result = run_bash(tmp_path, f"set -euo pipefail; source {_LIB}")
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.parametrize("line", SIGNAL_LINES)
def test_signal_lines_classified_as_signal(tmp_path: pathlib.Path, line: str) -> None:
    assert _classify(tmp_path, line) is True, f"expected SIGNAL: {line!r}"


@pytest.mark.parametrize("line", BOILERPLATE_LINES)
def test_boilerplate_lines_classified_as_boilerplate(
    tmp_path: pathlib.Path, line: str
) -> None:
    assert _classify(tmp_path, line) is False, f"expected BOILERPLATE: {line!r}"


def test_filter_keeps_only_signal_lines_in_order(tmp_path: pathlib.Path) -> None:
    """Feed a fixture mixing both classes; only signal lines survive, in order."""
    fixture_lines = []
    # Interleave so ordering is genuinely exercised, not just "signals first".
    for signal_line, boilerplate_line in zip(SIGNAL_LINES, BOILERPLATE_LINES):
        fixture_lines.append(boilerplate_line)
        fixture_lines.append(signal_line)
    # Any leftover lines (the lists are different lengths) go at the end.
    fixture_lines.extend(SIGNAL_LINES[len(BOILERPLATE_LINES):])
    fixture_lines.extend(BOILERPLATE_LINES[len(SIGNAL_LINES):])

    fixture_path = tmp_path / "wake-fixture.log"
    fixture_path.write_text("\n".join(fixture_lines) + "\n", encoding="utf-8")

    result = run_bash(
        tmp_path,
        f"set -euo pipefail; source {_LIB} && "
        f'dashboard_event_signal_filter < "{fixture_path}"',
    )
    assert result.returncode == 0, result.stderr

    survived = result.stdout.splitlines()
    expected_order = [line for line in fixture_lines if line in SIGNAL_LINES]
    assert survived == expected_order
    for boilerplate_line in BOILERPLATE_LINES:
        assert boilerplate_line not in survived


# ---------------------------------------------------------------------------
# dashboard_event_signal_collapse_key
# ---------------------------------------------------------------------------

_SWEEP_CENSUS_LINE_AGENT_A = (
    "[2026-07-26T23:58:05+00:00] wake(pm): sweep-census: skipped — "
    "/tmp/kanban/requirements/v2.24.1-bugfix-bundle-20260918.md: "
    "finalize=tag — not report"
)
_SWEEP_CENSUS_LINE_AGENT_B_LATER = (
    "[2026-09-19T03:12:00+00:00] wake(coder): sweep-census: skipped — "
    "/tmp/kanban/requirements/v2.24.1-bugfix-bundle-20260918.md: "
    "finalize=tag — not report"
)
_SWEEP_CENSUS_LINE_DIFFERENT_BUNDLE = (
    "[2026-09-19T03:12:00+00:00] wake(coder): sweep-census: skipped — "
    "/tmp/kanban/requirements/DIFFERENT-BUNDLE-20260919.md: "
    "finalize=tag — not report"
)
_SWEEP_CENSUS_LINE_DIFFERENT_REASON = (
    "[2026-09-19T03:12:00+00:00] wake(coder): sweep-census: skipped — "
    "/tmp/kanban/requirements/v2.24.1-bugfix-bundle-20260918.md: "
    "finalize=untag — not report"
)


def test_collapsing_pattern_returns_a_collapse_identity(
    tmp_path: pathlib.Path,
) -> None:
    """A sweep-census skip line — the one pattern that declares collapse
    metadata — returns a non-empty identity and exit 0."""
    key, returncode = _collapse_key(tmp_path, _SWEEP_CENSUS_LINE_AGENT_A)
    assert returncode == 0
    assert key != ""


def test_collapse_identity_is_invariant_across_agent_and_timestamp(
    tmp_path: pathlib.Path,
) -> None:
    """The same underlying sweep-census skip, logged by a different agent
    at a different timestamp, collapses to a byte-identical identity."""
    key_a, rc_a = _collapse_key(tmp_path, _SWEEP_CENSUS_LINE_AGENT_A)
    key_b, rc_b = _collapse_key(tmp_path, _SWEEP_CENSUS_LINE_AGENT_B_LATER)
    assert rc_a == 0 and rc_b == 0
    assert key_a == key_b


def test_collapse_identity_differs_on_a_different_bundle(
    tmp_path: pathlib.Path,
) -> None:
    """A sweep-census skip naming a different bundle path is a different
    event and must not collapse into the first bundle's identity."""
    key_a, _ = _collapse_key(tmp_path, _SWEEP_CENSUS_LINE_AGENT_A)
    key_other, rc_other = _collapse_key(
        tmp_path, _SWEEP_CENSUS_LINE_DIFFERENT_BUNDLE
    )
    assert rc_other == 0
    assert key_other != key_a


def test_collapse_identity_differs_on_a_different_reason(
    tmp_path: pathlib.Path,
) -> None:
    """A sweep-census skip stating a different reason is a different event
    and must not collapse into the first reason's identity."""
    key_a, _ = _collapse_key(tmp_path, _SWEEP_CENSUS_LINE_AGENT_A)
    key_other, rc_other = _collapse_key(
        tmp_path, _SWEEP_CENSUS_LINE_DIFFERENT_REASON
    )
    assert rc_other == 0
    assert key_other != key_a


def test_non_collapsing_pattern_returns_no_collapse_identity(
    tmp_path: pathlib.Path,
) -> None:
    """A line matching a pattern that declares no collapse metadata (agent
    dispatch) must never collapse: no output, non-zero exit."""
    dispatch_line = SIGNAL_LINES[0]
    assert "transitioned to WORKING [A]" in dispatch_line
    key, returncode = _collapse_key(tmp_path, dispatch_line)
    assert returncode == 1
    assert key == ""


def test_line_matching_no_pattern_returns_no_collapse_identity(
    tmp_path: pathlib.Path,
) -> None:
    """A boilerplate line matching no pattern at all must never collapse:
    no output, non-zero exit."""
    key, returncode = _collapse_key(tmp_path, BOILERPLATE_LINES[0])
    assert returncode == 1
    assert key == ""
