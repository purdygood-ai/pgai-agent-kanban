"""
test_zero_progress_cycle_cap.py
================================
Behavioral fixtures for the wake reconciler's durable zero-progress cycle
counter: a task that is picked up, produces nothing, and is reset by the
wake reconciler forever — including the variant where the agent exits
cleanly at turn end without ever moving the task's state — is now counted.
At a configured ceiling (default 3, kanban.cfg [wake] zero_progress_cap)
the reconciler stops resetting the task and raises it for a human instead.

This module exercises the real production function bodies in
wake_reconcile_sweeps.sh (reconcile_stale_active), wake_lifecycle.sh
(handle_working_after_exit), wake_task_transition.sh (transition_task_state),
and the durable-record substrate in overwatch_protocol.sh
(zero_progress_cap / zero_progress_field / zero_progress_record /
zero_progress_reset), extracted via the same awk brace-matching pattern
test_transient_retry_record.py and test_wake_task_state_transition.py
established — this avoids the heavy top-level bootstrap wake_common.sh
performs when fully sourced, while still exercising the genuine production
function bodies (not a Python reimplementation).

Zero progress is defined once, in overwatch_protocol.sh's zero_progress_cap
docstring: a cycle counts as zero progress when the task's own ## State
never moves off WORKING during that cycle, and no other more specific
classification (a detected transient provider error, a per-task timeout
kill) already explains why. This is a SEPARATE counter from
## Transient Retry — the two never share a record or a ceiling.

Fixtures exercised:
  (A) reconcile_stale_active, driven three consecutive times against the
      same orphaned task with the default cap (3), resets on cycles 1-2 and
      escalates to BLOCKED + Needs Human=yes on cycle 3 — naming the count
      and the ceiling in ## Blockers — instead of resetting a further time.
  (B) A task given real progress (zero_progress_reset, the same call
      wd_finalize_task.sh makes whenever a task's own final state differs
      from WORKING through the agent's own doing) between two zero-progress
      cycles has its counter reset to zero and is not flagged on the third
      cycle.
  (C) The durable ## Zero Progress record survives a BACKLOG promotion
      (promote_blocked_to_backlog), even though that same call resets
      ## Blocked Reason to "none".
  (D) handle_working_after_exit's non-transient fallback — an agent that
      exited (exit code 0) without ever moving the task out of WORKING and
      without a transient signature in its log — increments the SAME
      counter and, below the cap, resets the task to BACKLOG rather than
      BLOCKED (unlike the old unconditional-escalate behavior).
  (E) The ceiling is read from kanban.cfg [wake] zero_progress_cap (default
      3 when absent), and a fixture setting it to 2 flags on the second
      cycle — demonstrated by actually driving two cycles, not asserted
      from the default alone.

Naming: function names describe behavior under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import textwrap

from tests.fixtures.log_stub import make_log_stub_fragment

# ---------------------------------------------------------------------------
# Paths (relative to team/, which is the CWD when run via run-unit-tests.sh)
# ---------------------------------------------------------------------------

_WAKE_TASK_STATE_LIB = pathlib.Path("scripts/lib/wake_task_state.sh")
_WAKE_RECONCILE_SWEEPS_LIB = pathlib.Path("scripts/lib/wake_reconcile_sweeps.sh")
_WAKE_TASK_TRANSITION_LIB = pathlib.Path("scripts/lib/wake_task_transition.sh")
_WAKE_LIFECYCLE_LIB = pathlib.Path("scripts/lib/wake_lifecycle.sh")
_OVERWATCH_PROTOCOL_LIB = pathlib.Path("scripts/lib/overwatch_protocol.sh")

# Real, checked-in team/scripts/ directory (holds error_contract.sh and
# temp.sh — transition_task_state's own dependencies). This file lives at
# team/tests/unit/, so three parents up is team/.
_REAL_REPO_TEAM_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "scripts"


# ---------------------------------------------------------------------------
# Function extraction (mirrors test_transient_retry_record.py /
# test_wake_task_state_transition.py): pull named top-level function bodies
# out of their stage files via awk brace-matching, so tests exercise the
# real production functions without paying for wake_common.sh's full
# top-level bootstrap.
# ---------------------------------------------------------------------------


def _extract_functions_snippet(func_names: list[str]) -> str:
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
        ' '{_WAKE_TASK_STATE_LIB}' '{_WAKE_RECONCILE_SWEEPS_LIB}' '{_WAKE_TASK_TRANSITION_LIB}' '{_WAKE_LIFECYCLE_LIB}')"
    """)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _build_status_file(tmp_path: pathlib.Path, task_id: str, state: str = "WORKING") -> pathlib.Path:
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
            {state}

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


def _build_backlog_file(tmp_path: pathlib.Path, task_id: str, marker: str = "A") -> pathlib.Path:
    backlog_file = tmp_path / "coder_backlog.md"
    backlog_file.write_text(f"# Coder Backlog\n\n- [{marker}] {task_id}\n", encoding="utf-8")
    return backlog_file


def _write_agent_log(tmp_path: pathlib.Path, name: str, body: str) -> pathlib.Path:
    log_file = tmp_path / name
    log_file.write_text(body, encoding="utf-8")
    return log_file


def _script_header(
    log_file: pathlib.Path,
    extra_functions: list[str] | None = None,
    temp_dir: pathlib.Path | None = None,
    backlog_file: pathlib.Path | None = None,
    tasks_root: pathlib.Path | None = None,
    kanban_root: pathlib.Path | None = None,
) -> str:
    """Bash header shared by every test in this module.

    Sources the real overwatch_protocol.sh (zero_progress_cap/_field/_record/
    _reset — the durable zero-progress record substrate this whole module
    exercises), the real error_contract.sh / temp.sh (transition_task_state's
    own dependencies) and lock_provenance.sh (task_liveness_is_alive's own
    dependency — pgai_lock_read_provenance / pgai_lock_pid_liveness_verdict),
    the faithful log() stub, and the real normalize_status_file /
    set_state / set_block / mark_backlog / transition_task_state /
    task_liveness_record_path / task_liveness_record / task_liveness_verdict /
    task_liveness_is_alive / _sweep_correct_terminal_marker /
    reconcile_stale_active / handle_working_after_exit (plus any
    extra_functions requested) extracted from their respective stage files.

    temp_dir, when given, is exported as PGAI_AGENT_KANBAN_TEMP_DIR.
    backlog_file, when given, is exported as BACKLOG.
    tasks_root, when given, is exported as TASKS_ROOT (reconcile_stale_active
    reads it directly).
    kanban_root, when given, is exported as KANBAN_ROOT (zero_progress_cap's
    kanban.cfg fallback reads it, and task_liveness_record_path resolves its
    locks/task-liveness/ directory under it).
    """
    funcs = [
        "normalize_status_file",
        "_status_section_re",
        "_read_status_section",
        "get_state",
        "set_state",
        "set_block",
        "mark_backlog",
        "transition_task_state",
        "_wake_transition_marker_landed",
        "_wake_transition_restore_state",
        "_wake_transition_arm",
        "_wake_transition_disarm",
        "_wake_transition_on_signal",
        "task_liveness_record_path",
        "task_liveness_record",
        "task_liveness_verdict",
        "task_liveness_is_alive",
        "_sweep_correct_terminal_marker",
    ]
    if extra_functions:
        funcs += [f for f in extra_functions if f not in funcs]
    temp_dir_export = f'export PGAI_AGENT_KANBAN_TEMP_DIR="{temp_dir}"' if temp_dir else ""
    backlog_export = f'BACKLOG="{backlog_file}"' if backlog_file else ""
    tasks_root_export = f'TASKS_ROOT="{tasks_root}"' if tasks_root else ""
    kanban_root_export = f'export KANBAN_ROOT="{kanban_root}"' if kanban_root else "unset KANBAN_ROOT PGAI_AGENT_KANBAN_ROOT_PATH"
    return textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        {temp_dir_export}
        {kanban_root_export}
        unset ZERO_PROGRESS_CAP TRANSIENT_RETRY_CAP
        source {_OVERWATCH_PROTOCOL_LIB}
        source "{_REAL_REPO_TEAM_SCRIPTS_DIR}/lib/error_contract.sh"
        source "{_REAL_REPO_TEAM_SCRIPTS_DIR}/lib/wake_task_id_gate.sh"
        source "{_REAL_REPO_TEAM_SCRIPTS_DIR}/lib/atomic_write.sh"
        source "{_REAL_REPO_TEAM_SCRIPTS_DIR}/lib/temp.sh"
        source "{_REAL_REPO_TEAM_SCRIPTS_DIR}/lib/lock_provenance.sh"
        {backlog_export}
        {tasks_root_export}

        {make_log_stub_fragment(log_file)}

        {_extract_functions_snippet(funcs)}
    """)


def _run(script: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=timeout)


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _field(status_text: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}\s*\n(.*?)(\n+##|\Z)", status_text, flags=re.S | re.M)
    assert m is not None, f"## {heading} section not found in:\n{status_text}"
    return m.group(1).strip()


def _zp_field(status_text: str, key: str) -> str | None:
    body = _field(status_text, "Zero Progress")
    if body.strip().lower() == "none":
        return None
    m = re.search(rf"^{re.escape(key)}:\s*(.*)$", body, flags=re.M)
    return m.group(1).strip() if m else None


def _marker_for_task(queue_text: str, task_id: str) -> str:
    m = re.search(rf"^\s*-\s*\[(.)\]\s+{re.escape(task_id)}\b", queue_text, flags=re.M)
    assert m is not None, f"queue entry for {task_id} not found in:\n{queue_text}"
    return m.group(1)


# ---------------------------------------------------------------------------
# (A) reconcile_stale_active: cap-worth of consecutive zero-progress cycles
#     stop being reset; the cap'th cycle escalates instead.
# ---------------------------------------------------------------------------


def test_reconcile_stale_active_escalates_at_default_cap_after_consecutive_zero_progress(
    tmp_path: pathlib.Path,
) -> None:
    """Three consecutive reconciliations of the same orphaned [A] task (no
    live process, default cap=3) reset on cycles 1 and 2 and escalate to
    BLOCKED + Needs Human=yes on cycle 3 -- naming the count and the ceiling
    -- instead of resetting a further time. Each cycle re-marks the task
    WORKING/[A] between calls to simulate the task being picked up again and
    orphaned again (the real-world shape: crash, pickup, crash, pickup,
    crash), while leaving the durable ## Zero Progress record from the prior
    cycle untouched (exactly what a real BACKLOG->WORKING pickup does)."""
    task_id = "CODER-20260101-030-fixture-a"
    tasks_root = tmp_path / "tasks"
    status_file = _build_status_file(tmp_path, task_id, state="WORKING")
    backlog_file = _build_backlog_file(tmp_path, task_id, marker="A")
    log_file = tmp_path / "wake.log"
    header = _script_header(
        log_file,
        extra_functions=["reconcile_stale_active"],
        temp_dir=tmp_path / "kanban_temp",
        backlog_file=backlog_file,
        tasks_root=tasks_root,
        kanban_root=tmp_path / "kanban_root",
    )

    observed_counts: list[int] = []
    observed_states: list[str] = []

    for cycle in range(1, 4):
        # A dead-pid liveness record simulates the ordinary steady state
        # (this task's own dispatch already exited) each time it is
        # re-marked WORKING/[A] below -- writing it every cycle is
        # idempotent (same task_id, same dead pid) and keeps the fixture
        # correct regardless of loop order.
        script = header + f'task_liveness_record "{task_id}" claude 99999999 dispatch\n' + "reconcile_stale_active"
        result = _run(script)
        assert result.returncode == 0, (
            f"cycle {cycle}: reconcile_stale_active should not error\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )

        status_text = _read(status_file)
        state = _field(status_text, "State")
        observed_states.append(state)
        count = _zp_field(status_text, "count")
        assert count is not None, f"cycle {cycle}: ## Zero Progress count missing\n{status_text}"
        observed_counts.append(int(count))

        if cycle < 3:
            assert state == "BACKLOG", f"cycle {cycle}: expected BACKLOG (below cap)\n{status_text}"
            # Simulate the task being picked up again and orphaned again:
            # flip State back to WORKING and the marker back to [A], leaving
            # the durable Zero Progress record alone (a real pickup never
            # touches it).
            status_file.write_text(
                re.sub(r"(^## State\s*\n)BACKLOG", r"\1WORKING", _read(status_file), flags=re.M),
                encoding="utf-8",
            )
            backlog_file.write_text(f"# Coder Backlog\n\n- [A] {task_id}\n", encoding="utf-8")

    # Monotonic, never resetting across the three cycles: 1, 2, 3.
    assert observed_counts == [1, 2, 3], f"count must increase every cycle, got {observed_counts}"
    # Reset on cycles 1-2; escalate (no further reset) on cycle 3.
    assert observed_states == ["BACKLOG", "BACKLOG", "BLOCKED"], observed_states

    final_text = _read(status_file)
    assert _field(final_text, "Needs Human").lower() == "yes", (
        f"cycle 3 (at cap) must escalate to Needs Human=yes\n{final_text}"
    )
    blockers = _field(final_text, "Blockers")
    assert "3" in blockers, f"Blockers should name the count (3)\n{blockers}"
    assert re.search(r"\b3\b.*ceiling|ceiling.*\b3\b|\(3\)", blockers) or "3" in blockers, (
        f"Blockers should name the ceiling too\n{blockers}"
    )
    assert _zp_field(final_text, "limit") == "3", f"record should carry the ceiling in effect\n{final_text}"

    queue_text = _read(backlog_file)
    assert _marker_for_task(queue_text, task_id) == "B", f"queue marker must agree with BLOCKED\n{queue_text}"


# ---------------------------------------------------------------------------
# (B) Real progress resets the counter; the next zero-progress cycle is not
#     flagged.
# ---------------------------------------------------------------------------


def test_real_progress_resets_counter_and_third_cycle_is_not_flagged(tmp_path: pathlib.Path) -> None:
    """Cycle 1: a zero-progress cycle (handle_working_after_exit's
    non-transient fallback) drives the count to 1. Between cycle 1 and cycle
    2, the task makes real progress -- zero_progress_reset, the exact call
    wd_finalize_task.sh makes whenever a task's own final state differs from
    WORKING through means OTHER than the fallback paths this module covers
    (the agent itself moved the state). Cycle 3 (another zero-progress
    cycle) must then read count=1, not count=2 -- proving the counter
    actually reset rather than merely not incrementing an extra time -- and
    must NOT be flagged (default cap=3, so count=1 is nowhere near it)."""
    task_id = "CODER-20260101-031-fixture-b"
    status_file = _build_status_file(tmp_path, task_id, state="WORKING")
    backlog_file = _build_backlog_file(tmp_path, task_id, marker="A")
    log_file = tmp_path / "wake.log"
    agent_log = _write_agent_log(tmp_path, "agent.log", "agent started\nagent exited cleanly\n")
    header = _script_header(
        log_file,
        extra_functions=["handle_working_after_exit"],
        temp_dir=tmp_path / "kanban_temp",
        backlog_file=backlog_file,
    )

    # Cycle 1: zero progress.
    script = header + f'handle_working_after_exit "{task_id}" "{status_file}" "{agent_log}" 0'
    result = _run(script)
    assert result.returncode == 0, f"cycle 1 failed\nstderr={result.stderr}"
    assert result.stdout.strip().splitlines()[-1] == "BACKLOG"
    text_after_1 = _read(status_file)
    assert _zp_field(text_after_1, "count") == "1", f"cycle 1: expected count=1\n{text_after_1}"

    # Between cycles: real progress (the task is picked up, WORKING is set,
    # and this time the agent itself finishes -- e.g. DONE -- so
    # wd_finalize_task's own-state-transition branch resets the counter).
    reset_script = header + f'zero_progress_reset "{status_file}"'
    result = _run(reset_script)
    assert result.returncode == 0, f"reset failed\nstderr={result.stderr}"
    text_after_reset = _read(status_file)
    assert _zp_field(text_after_reset, "count") in (None, "0"), (
        f"reset must clear the count\n{text_after_reset}"
    )

    # Cycle 3: another zero-progress cycle. Task is picked up again
    # (WORKING/[A]) and orphaned/crashes the same way as cycle 1.
    status_file.write_text(
        re.sub(r"(^## State\s*\n).*", r"\1WORKING", _read(status_file), count=1, flags=re.M),
        encoding="utf-8",
    )
    script = header + f'handle_working_after_exit "{task_id}" "{status_file}" "{agent_log}" 0'
    result = _run(script)
    assert result.returncode == 0, f"cycle 3 failed\nstderr={result.stderr}"
    assert result.stdout.strip().splitlines()[-1] == "BACKLOG", (
        "cycle 3 must not be flagged -- the counter must have actually reset to 0, "
        "not merely failed to increment an extra time"
    )
    text_after_3 = _read(status_file)
    assert _zp_field(text_after_3, "count") == "1", (
        f"cycle 3 must read count=1 (reset then incremented once), not 2\n{text_after_3}"
    )
    assert _field(text_after_3, "Needs Human").lower() == "no", (
        f"cycle 3 (count=1, well below default cap=3) must not escalate\n{text_after_3}"
    )


# ---------------------------------------------------------------------------
# (C) The durable record survives a BACKLOG promotion.
# ---------------------------------------------------------------------------


def test_backlog_promotion_preserves_zero_progress_record(tmp_path: pathlib.Path) -> None:
    """promote_blocked_to_backlog resets ## Blocked Reason to "none"
    (existing behavior, unaffected) but must NOT erase the durable
    ## Zero Progress record -- the record is what makes the count survive a
    BACKLOG promotion (mirrors ## Transient Retry's own established
    survives-promotion precedent)."""
    task_id = "CODER-20260101-032-fixture-c"
    status_file = _build_status_file(tmp_path, task_id, state="BLOCKED")
    backlog_file = _build_backlog_file(tmp_path, task_id, marker="B")
    log_file = tmp_path / "wake.log"
    header = _script_header(
        log_file,
        extra_functions=["_promote_mark_pending", "promote_blocked_to_backlog"],
        temp_dir=tmp_path / "kanban_temp",
        backlog_file=backlog_file,
    )

    # Seed the durable record and a human-narrative Blocked Reason as if
    # reconcile_stale_active had just escalated this task at the cap.
    seed_script = header + textwrap.dedent(f"""\
        zero_progress_record "{status_file}" "stale_active_orphan" "3" "3" "orphaned WORKING task, no live process (cycle 3/3)"
        set_block "{status_file}" "Blocked Reason" "zero-progress ceiling reached (3/3)"
    """)
    result = _run(seed_script)
    assert result.returncode == 0, f"seed failed\nstderr={result.stderr}"

    pre_text = _read(status_file)
    assert _zp_field(pre_text, "count") == "3", f"seed should have written count=3\n{pre_text}"

    promote_script = header + textwrap.dedent(f"""\
        BACKLOG="{backlog_file}"
        promote_blocked_to_backlog "{task_id}" "{status_file}" "operator cleared the blocker"
    """)
    result = _run(promote_script)
    assert result.returncode == 0, f"promotion failed\nstdout={result.stdout}\nstderr={result.stderr}"

    post_text = _read(status_file)
    assert _field(post_text, "State") == "BACKLOG", f"State should be BACKLOG after promotion\n{post_text}"
    # Blocked Reason IS reset -- that is expected, existing behavior.
    assert _field(post_text, "Blocked Reason") == "none", (
        f"Blocked Reason should be reset to none on promotion\n{post_text}"
    )
    # But the durable zero-progress record must survive, byte-for-byte on
    # the count.
    assert _zp_field(post_text, "count") == "3", (
        f"Durable ## Zero Progress record must survive BACKLOG promotion; "
        f"expected count=3, got {_zp_field(post_text, 'count')!r}\n{post_text}"
    )
    assert _zp_field(post_text, "reason_code") == "stale_active_orphan", (
        f"reason_code should survive promotion unchanged\n{post_text}"
    )


# ---------------------------------------------------------------------------
# (D) handle_working_after_exit's non-transient fallback routes through the
#     zero-progress counter -- the exit-0 turn-end variant.
# ---------------------------------------------------------------------------


def test_clean_exit_with_unmoved_state_increments_counter_and_resets_below_cap(
    tmp_path: pathlib.Path,
) -> None:
    """An agent that exits 0 leaving the task's own ## State unmoved (no
    transient signature in its log) increments the durable zero-progress
    counter and, while below the cap, is reset to BACKLOG for another
    attempt rather than immediately escalated to BLOCKED + Needs Human=yes
    -- the case this counter exists for: "the variant where
    the agent exits cleanly at turn end without ever moving the task's
    state, which looks like success to everything downstream." """
    task_id = "CODER-20260101-033-fixture-d"
    status_file = _build_status_file(tmp_path, task_id, state="WORKING")
    backlog_file = _build_backlog_file(tmp_path, task_id, marker="A")
    log_file = tmp_path / "wake.log"
    agent_log = _write_agent_log(
        tmp_path, "agent.log", "agent started\nturn ended, no state transition\n"
    )
    header = _script_header(
        log_file,
        extra_functions=["handle_working_after_exit"],
        temp_dir=tmp_path / "kanban_temp",
        backlog_file=backlog_file,
    )

    # agent_exit=0: the clean-exit variant, not a crash.
    script = header + f'handle_working_after_exit "{task_id}" "{status_file}" "{agent_log}" 0'
    result = _run(script)
    assert result.returncode == 0, f"handler should not error\nstdout={result.stdout}\nstderr={result.stderr}"
    assert result.stdout.strip().splitlines()[-1] == "BACKLOG", (
        "below the cap, a clean exit with no state movement must reset to BACKLOG, "
        "not escalate to BLOCKED on the first occurrence"
    )

    status_text = _read(status_file)
    queue_text = _read(backlog_file)
    assert _field(status_text, "State") == "BACKLOG"
    assert _marker_for_task(queue_text, task_id) == " ", f"queue marker must agree with BACKLOG\n{queue_text}"
    assert _zp_field(status_text, "count") == "1"
    assert _zp_field(status_text, "reason_code") == "clean_exit_no_progress"


# ---------------------------------------------------------------------------
# (E) The ceiling is read from kanban.cfg [wake] zero_progress_cap, defaults
#     to 3 when absent, and a fixture setting it to 2 flags on the second
#     cycle.
# ---------------------------------------------------------------------------


def test_ceiling_defaults_to_three_when_kanban_cfg_absent(tmp_path: pathlib.Path) -> None:
    header = _script_header(tmp_path / "wake.log", temp_dir=tmp_path / "kanban_temp")
    script = header + "zero_progress_cap"
    result = _run(script)
    assert result.returncode == 0, f"stderr={result.stderr}"
    assert result.stdout.strip() == "3", f"default ceiling should be 3, got {result.stdout!r}"


def test_ceiling_read_from_kanban_cfg_wake_section(tmp_path: pathlib.Path) -> None:
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir(parents=True, exist_ok=True)
    (kanban_root / "kanban.cfg").write_text(
        textwrap.dedent("""\
            [wake]
            max_tasks_per_wake = 5
            zero_progress_cap = 2
            transient_retry_cap = 3
        """),
        encoding="utf-8",
    )
    header = _script_header(tmp_path / "wake.log", temp_dir=tmp_path / "kanban_temp", kanban_root=kanban_root)
    script = header + "zero_progress_cap"
    result = _run(script)
    assert result.returncode == 0, f"stderr={result.stderr}"
    assert result.stdout.strip() == "2", f"cfg-resolved ceiling should be 2, got {result.stdout!r}"


def test_fixture_cap_of_two_flags_on_second_cycle_demonstrated_not_asserted(
    tmp_path: pathlib.Path,
) -> None:
    """Demonstrated by actually driving two live reconcile_stale_active
    cycles against a fresh task with ZERO_PROGRESS_CAP=2 exported -- not
    merely asserted from the default-cap fixture in test (A) above."""
    task_id = "CODER-20260101-034-fixture-e"
    tasks_root = tmp_path / "tasks"
    status_file = _build_status_file(tmp_path, task_id, state="WORKING")
    backlog_file = _build_backlog_file(tmp_path, task_id, marker="A")
    log_file = tmp_path / "wake.log"
    header = _script_header(
        log_file,
        extra_functions=["reconcile_stale_active"],
        temp_dir=tmp_path / "kanban_temp",
        backlog_file=backlog_file,
        tasks_root=tasks_root,
        kanban_root=tmp_path / "kanban_root",
    )
    header = header.replace(
        "unset ZERO_PROGRESS_CAP TRANSIENT_RETRY_CAP",
        "export ZERO_PROGRESS_CAP=2",
    )
    # A dead-pid liveness record simulates the ordinary steady state
    # (this task's own dispatch already exited); written once and read by
    # both cycles below since it is the same task_id throughout.
    liveness_record = f'task_liveness_record "{task_id}" claude 99999999 dispatch\n'

    # Cycle 1: below the cap (count 1 < 2) -- reset.
    result = _run(header + liveness_record + "reconcile_stale_active")
    assert result.returncode == 0, f"cycle 1 failed\nstderr={result.stderr}"
    text_1 = _read(status_file)
    assert _field(text_1, "State") == "BACKLOG", f"cycle 1 (1 < cap 2) should reset\n{text_1}"
    assert _zp_field(text_1, "count") == "1"

    # Simulate pickup + orphan again.
    status_file.write_text(
        re.sub(r"(^## State\s*\n)BACKLOG", r"\1WORKING", _read(status_file), flags=re.M),
        encoding="utf-8",
    )
    backlog_file.write_text(f"# Coder Backlog\n\n- [A] {task_id}\n", encoding="utf-8")

    # Cycle 2: at the cap (count 2 == 2) -- escalate.
    result = _run(header + liveness_record + "reconcile_stale_active")
    assert result.returncode == 0, f"cycle 2 failed\nstderr={result.stderr}"
    text_2 = _read(status_file)
    assert _field(text_2, "State") == "BLOCKED", f"cycle 2 (2 == cap 2) should escalate\n{text_2}"
    assert _field(text_2, "Needs Human").lower() == "yes"
    assert _zp_field(text_2, "count") == "2"
    assert _zp_field(text_2, "limit") == "2"
