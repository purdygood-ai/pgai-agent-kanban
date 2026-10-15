"""
test_wake_task_state_transition.py
===================================
Behavioral fixtures for transition_task_state, the single write path that
moves a task's status.md ## State block and its queue marker together, as
one operation, instead of leaving callers to pair set_state and mark_backlog
by hand.

This module exercises the real production function bodies in
wake_reconcile_sweeps.sh (reconcile_stale_active), wake_task_transition.sh
(transition_task_state), and wake_lifecycle.sh
(handle_working_after_exit), and the real call-site block in
wake_driver_prep.sh (the marker self-correction switch), extracted via
the same awk brace-matching / anchor-to-close technique
test_coder_completion_merge_gate_bash.py and test_transient_retry_record.py
established. All fixtures are tmp_path-based; no real project state or the
live kanban install is read or mutated.

Fixtures exercised:
  (A) One call moves a throwaway task from BACKLOG to each of the six
      canonical states, leaving status.md's ## State and the queue marker
      in agreement for every one.
  (B) A missing queue entry refuses the whole call: status.md is rolled
      back to its pre-call bytes, the marker is untouched, and the caller
      gets a message naming the failed surface.
  (C) A missing status.md file refuses the call before either surface is
      touched.
  (D) An invalid target state refuses the call before either surface is
      touched.
  (E) The state/marker agreement check every fixture above relies on is not
      vacuous: a caller that writes ## State directly without writing the
      queue marker (the exact hand-paired pattern this function replaces)
      is caught by it. Built here as a genuinely half-applied write (the
      real set_state called alone), not by editing transition_task_state.
  (F) reconcile_stale_active resets an orphaned WORKING/[A] task to
      BACKLOG/[ ] through transition_task_state, and both surfaces agree
      afterward.
  (G) handle_working_after_exit's BLOCKED transition (both the generic
      crash path and the transient-retry path) goes through
      transition_task_state, and both surfaces agree afterward.
  (H) wake_driver_prep.sh's marker self-correction switch routes its
      BLOCKED/WAITING/DONE/WONT-DO branches through transition_task_state
      (surfaces agree), while its documented WORKING exception (state stays
      WORKING, marker becomes "B") still bypasses transition_task_state by
      design.

Naming: function names describe behavior under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines", Anti-pattern 6).
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

_WAKE_TASK_STATE_LIB = pathlib.Path("scripts/lib/wake_task_state.sh")
_WAKE_RECONCILE_SWEEPS_LIB = pathlib.Path("scripts/lib/wake_reconcile_sweeps.sh")
_WAKE_TASK_TRANSITION_LIB = pathlib.Path("scripts/lib/wake_task_transition.sh")
_WAKE_LIFECYCLE_LIB = pathlib.Path("scripts/lib/wake_lifecycle.sh")
_WAKE_DRIVER_PREP_LIB = pathlib.Path("scripts/lib/wake_driver_prep.sh")

# Real, checked-in team/scripts/ directory (holds error_contract.sh and
# temp.sh, both dependencies of transition_task_state). This file lives at
# team/tests/unit/, so three parents up is team/.
_REAL_REPO_TEAM_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "scripts"

# The one state->marker table transition_task_state owns, mirrored here so
# tests assert against the same contract the production code documents in
# its own header comment.
_STATE_TO_MARKER = {
    "BACKLOG": " ",
    "WAITING": "W",
    "WORKING": "A",
    "BLOCKED": "B",
    "DONE": "x",
    "WONT-DO": "x",
}


# ---------------------------------------------------------------------------
# Function extraction: pull named top-level function bodies out of their
# stage files via awk brace-counting, so tests exercise the real production
# function bodies without paying for wake_common.sh's full top-level
# bootstrap (AGENT, TEAM_ROOT, SCRIPT_DIR, a real kanban.cfg, provider
# wiring).
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


_PREP_SWITCH_ANCHOR = "# --- Marker self-correction for a task rediscovered off BACKLOG ---"


def _extract_prep_switch_block() -> list[str]:
    """Extract wake_driver_prep.sh's marker self-correction switch: from its
    anchor comment through the matching `fi` that closes the
    `if [[ "$current_state" != "BACKLOG" ]]; then` block immediately below
    it. Depth-counts the `if`/`case` openers against `fi`/`esac` closers
    (both keywords appear once each here) so the extraction is exact rather
    than a fixed line count.
    """
    lines = _WAKE_DRIVER_PREP_LIB.read_text(encoding="utf-8").splitlines()
    capturing = False
    depth = 0
    block: list[str] = []
    for line in lines:
        if _PREP_SWITCH_ANCHOR in line:
            capturing = True
            block.append(line)
            continue
        if not capturing:
            continue
        block.append(line)
        stripped = line.strip()
        if stripped.startswith("if ") or stripped.startswith("case "):
            depth += 1
        elif stripped in ("fi", "esac"):
            depth -= 1
            if depth == 0:
                break
    assert block, f"marker self-correction anchor not found in {_WAKE_DRIVER_PREP_LIB}"
    return block


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _build_status_file(tmp_path: pathlib.Path, task_id: str, state: str = "BACKLOG") -> pathlib.Path:
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
            TBD

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


def _build_backlog_file(tmp_path: pathlib.Path, task_id: str, marker: str = " ") -> pathlib.Path:
    backlog_file = tmp_path / "coder_backlog.md"
    backlog_file.write_text(f"# Coder Backlog\n\n- [{marker}] {task_id} — fixture task\n", encoding="utf-8")
    return backlog_file


def _script_header(
    log_file: pathlib.Path,
    temp_dir: pathlib.Path,
    extra_functions: list[str] | None = None,
    backlog_file: pathlib.Path | None = None,
    tasks_root: pathlib.Path | None = None,
) -> str:
    """Bash header shared by every test in this module.

    Sources the real error_contract.sh / temp.sh (transition_task_state's
    own dependencies) and lock_provenance.sh (task_liveness_is_alive's own
    dependency — pgai_lock_read_provenance / pgai_lock_pid_liveness_verdict),
    the faithful log() stub, and the real normalize_status_file /
    get_state / set_state / set_block / mark_backlog / transition_task_state
    / task_liveness_record_path / task_liveness_record / task_liveness_verdict
    / task_liveness_is_alive (wake_task_state.sh) /
    _sweep_correct_terminal_marker (wake_reconcile_sweeps.sh) /
    handle_working_after_exit (wake_lifecycle.sh)
    (plus any extra_functions requested) extracted from their respective
    stage files.

    temp_dir is exported as PGAI_AGENT_KANBAN_TEMP_DIR so
    transition_task_state's scratch-file snapshots land under an isolated
    per-test directory. KANBAN_ROOT is set to the same temp_dir (rather
    than left unset) so task_liveness_record_path resolves to a real,
    writable locks/task-liveness/ directory under it. backlog_file, when
    given, is exported as BACKLOG. tasks_root, when given, is exported as
    TASKS_ROOT (reconcile_stale_active reads it directly).
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
    backlog_export = f'BACKLOG="{backlog_file}"' if backlog_file else ""
    tasks_root_export = f'TASKS_ROOT="{tasks_root}"' if tasks_root else ""
    return textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        export PGAI_AGENT_KANBAN_TEMP_DIR="{temp_dir}"
        unset ZERO_PROGRESS_CAP TRANSIENT_RETRY_CAP PGAI_AGENT_KANBAN_ROOT_PATH
        KANBAN_ROOT="{temp_dir}"
        source "{_REAL_REPO_TEAM_SCRIPTS_DIR}/lib/overwatch_protocol.sh"
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


def _marker_for_task(queue_text: str, task_id: str) -> str:
    m = re.search(rf"^\s*-\s*\[(.)\]\s+{re.escape(task_id)}\b", queue_text, flags=re.M)
    assert m is not None, f"queue entry for {task_id} not found in:\n{queue_text}"
    return m.group(1)


def _assert_state_and_marker_agree(status_text: str, queue_text: str, task_id: str) -> None:
    """The one invariant this whole module exercises: the queue marker for
    task_id must equal the marker _STATE_TO_MARKER derives for whatever
    ## State currently reads."""
    state = _field(status_text, "State")
    marker = _marker_for_task(queue_text, task_id)
    expected = _STATE_TO_MARKER[state]
    assert marker == expected, (
        f"status.md ## State={state!r} maps to marker {expected!r}, but the queue "
        f"marker for {task_id} reads {marker!r} — the two surfaces disagree"
    )


# ---------------------------------------------------------------------------
# (A) One call moves BACKLOG -> each of the six canonical states, both
#     surfaces in agreement afterward.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("target_state", ["BACKLOG", "WAITING", "WORKING", "BLOCKED", "DONE", "WONT-DO"])
def test_transition_moves_task_and_marker_together(tmp_path: pathlib.Path, target_state: str) -> None:
    task_id = f"CODER-20260101-020-fixture-{target_state.lower().replace('-', '')}"
    status_file = _build_status_file(tmp_path, task_id, state="BACKLOG")
    backlog_file = _build_backlog_file(tmp_path, task_id, marker=" ")
    log_file = tmp_path / "wake.log"
    header = _script_header(log_file, tmp_path / "kanban_temp", backlog_file=backlog_file)

    script = header + textwrap.dedent(f"""\
        transition_task_state "{task_id}" "{status_file}" "{target_state}"
        printf 'EXIT=[%s]\\n' "$?"
    """)
    result = _run(script)
    assert result.returncode == 0, f"transition should succeed\nstdout={result.stdout}\nstderr={result.stderr}"
    assert "EXIT=[0]" in result.stdout, f"transition should report success\n{result.stdout}"

    status_text = _read(status_file)
    queue_text = _read(backlog_file)
    assert _field(status_text, "State") == target_state, f"State should read {target_state}\n{status_text}"
    _assert_state_and_marker_agree(status_text, queue_text, task_id)


# ---------------------------------------------------------------------------
# (B) A missing queue entry refuses the whole call: status.md rolled back,
#     marker untouched, message names the failed surface.
# ---------------------------------------------------------------------------


def test_missing_queue_entry_refuses_and_leaves_both_surfaces_untouched(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-021-fixture-b"
    status_file = _build_status_file(tmp_path, task_id, state="WORKING")
    # Queue file exists but carries no entry for this task — the "missing or
    # unparseable queue entry" refusal outcome.
    backlog_file = tmp_path / "coder_backlog.md"
    backlog_file.write_text("# Coder Backlog\n\n- [ ] OTHER-999 — unrelated task\n", encoding="utf-8")
    log_file = tmp_path / "wake.log"
    header = _script_header(log_file, tmp_path / "kanban_temp", backlog_file=backlog_file)

    before_status = _read(status_file)
    before_queue = _read(backlog_file)

    script = header + textwrap.dedent(f"""\
        transition_task_state "{task_id}" "{status_file}" "BLOCKED"
    """)
    result = _run(script)
    assert result.returncode != 0, "transition must refuse when the queue entry is missing"
    assert "error:   wake.transition.marker-write-failed" in result.stderr, (
        f"refusal must carry the house-shape error key naming the marker surface\nstderr={result.stderr}"
    )
    assert "message: " in result.stderr and "remedy:  " in result.stderr, (
        f"refusal must carry the house-shape message/remedy fields\nstderr={result.stderr}"
    )

    after_status = _read(status_file)
    after_queue = _read(backlog_file)
    assert after_status == before_status, (
        f"status.md must be rolled back byte-for-byte on marker-write failure\n"
        f"before={before_status!r}\nafter={after_status!r}"
    )
    assert after_queue == before_queue, "queue file must be untouched on marker-write failure"


# ---------------------------------------------------------------------------
# (C) A missing status.md file refuses before either surface is touched.
# ---------------------------------------------------------------------------


def test_missing_status_file_refuses_before_touching_the_queue(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-022-fixture-c"
    status_file = tmp_path / "tasks" / task_id / "status.md"  # never created
    backlog_file = _build_backlog_file(tmp_path, task_id, marker=" ")
    log_file = tmp_path / "wake.log"
    header = _script_header(log_file, tmp_path / "kanban_temp", backlog_file=backlog_file)

    before_queue = _read(backlog_file)

    script = header + textwrap.dedent(f"""\
        transition_task_state "{task_id}" "{status_file}" "BLOCKED"
    """)
    result = _run(script)
    assert result.returncode != 0, "transition must refuse when status.md does not exist"
    assert "error:   wake.transition.status-missing" in result.stderr, (
        f"refusal must carry the house-shape error key naming the status surface\nstderr={result.stderr}"
    )

    assert _read(backlog_file) == before_queue, "queue file must be untouched when status.md is missing"
    assert not status_file.exists(), "a missing status.md must not be created by a refused transition"


# ---------------------------------------------------------------------------
# (D) An invalid target state refuses before either surface is touched.
# ---------------------------------------------------------------------------


def test_invalid_target_state_refuses_before_touching_either_surface(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-023-fixture-d"
    status_file = _build_status_file(tmp_path, task_id, state="BACKLOG")
    backlog_file = _build_backlog_file(tmp_path, task_id, marker=" ")
    log_file = tmp_path / "wake.log"
    header = _script_header(log_file, tmp_path / "kanban_temp", backlog_file=backlog_file)

    before_status = _read(status_file)
    before_queue = _read(backlog_file)

    script = header + textwrap.dedent(f"""\
        transition_task_state "{task_id}" "{status_file}" "NOT_A_CANONICAL_STATE"
    """)
    result = _run(script)
    assert result.returncode != 0, "transition must refuse an unrecognized state name"
    assert "error:   wake.transition.invalid-state" in result.stderr, (
        f"refusal must carry the house-shape error key\nstderr={result.stderr}"
    )

    assert _read(status_file) == before_status, "status.md must be untouched on an invalid-state refusal"
    assert _read(backlog_file) == before_queue, "queue file must be untouched on an invalid-state refusal"


# ---------------------------------------------------------------------------
# (E) The agreement check above is not vacuous: a caller that writes ##
#     State without writing the queue marker — the exact hand-paired
#     pattern transition_task_state replaces — is caught by it.
# ---------------------------------------------------------------------------


def test_agreement_check_catches_a_caller_that_writes_only_state(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-024-fixture-e"
    status_file = _build_status_file(tmp_path, task_id, state="WORKING")
    backlog_file = _build_backlog_file(tmp_path, task_id, marker="A")
    log_file = tmp_path / "wake.log"
    header = _script_header(log_file, tmp_path / "kanban_temp", backlog_file=backlog_file)

    # Deliberately half-applied: call the real set_state directly, the exact
    # hand-paired sequence transition_task_state exists to replace, and never
    # call mark_backlog. This is a red case built from real production
    # primitives inside the test, not an edit to transition_task_state.
    script = header + textwrap.dedent(f"""\
        set_state "{status_file}" "BLOCKED"
    """)
    result = _run(script)
    assert result.returncode == 0, f"set_state alone should succeed\nstderr={result.stderr}"

    status_text = _read(status_file)
    queue_text = _read(backlog_file)
    assert _field(status_text, "State") == "BLOCKED", "the state write itself must have landed"
    assert _marker_for_task(queue_text, task_id) == "A", "the marker must be stale — mark_backlog was never called"

    with pytest.raises(AssertionError):
        _assert_state_and_marker_agree(status_text, queue_text, task_id)


# ---------------------------------------------------------------------------
# (F) reconcile_stale_active resets an orphaned WORKING/[A] task to
#     BACKLOG/[ ] through transition_task_state.
# ---------------------------------------------------------------------------


def test_reconcile_stale_active_resets_orphan_through_transition_path(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-025-fixture-f"
    tasks_root = tmp_path / "tasks"
    status_file = _build_status_file(tmp_path, task_id, state="WORKING")
    backlog_file = _build_backlog_file(tmp_path, task_id, marker="A")
    log_file = tmp_path / "wake.log"
    header = _script_header(
        log_file,
        tmp_path / "kanban_temp",
        extra_functions=["reconcile_stale_active"],
        backlog_file=backlog_file,
        tasks_root=tasks_root,
    )

    # No PP_dev_tree_path / PGAI_DEV_TREE_PATH set, and a dead-pid liveness
    # record simulates the ordinary steady state (this task's own
    # dispatch already exited) — reconcile_stale_active takes its
    # "orphaned, no dev tree configured" path, which needs no worktree/git
    # dependencies beyond what this header already provides.
    script = header + f'task_liveness_record "{task_id}" claude 99999999 dispatch\n' + "reconcile_stale_active"
    result = _run(script)
    assert result.returncode == 0, f"reconcile_stale_active should not error\nstdout={result.stdout}\nstderr={result.stderr}"

    status_text = _read(status_file)
    queue_text = _read(backlog_file)
    assert _field(status_text, "State") == "BACKLOG", f"orphaned task must reset to BACKLOG\n{status_text}"
    _assert_state_and_marker_agree(status_text, queue_text, task_id)


# ---------------------------------------------------------------------------
# (G) handle_working_after_exit's transitions go through transition_task_state,
#     for the zero-progress, transient-retry, and at-cap-escalation branches.
#     (The zero-progress ceiling's full behavior — reset-below-cap,
#     escalate-at-cap, counter reset on real progress — is exercised in
#     test_zero_progress_cycle_cap.py; this module's concern is narrower:
#     confirming each branch's transition still routes through
#     transition_task_state with both surfaces in agreement afterward.)
# ---------------------------------------------------------------------------


def test_generic_crash_resets_task_through_transition_path(tmp_path: pathlib.Path) -> None:
    """A non-transient WORKING-after-exit (no recognizable log signature) is
    the zero-progress path. The FIRST such cycle (durable count 1, below the
    default cap of 3) routes its BACKLOG transition through
    transition_task_state, with both surfaces in agreement afterward — it no
    longer escalates straight to BLOCKED on the first occurrence; that now
    only happens once the durable zero-progress count reaches its cap."""
    task_id = "CODER-20260101-026-fixture-g1"
    status_file = _build_status_file(tmp_path, task_id, state="WORKING")
    backlog_file = _build_backlog_file(tmp_path, task_id, marker="A")
    log_file = tmp_path / "wake.log"
    agent_log = tmp_path / "agent.log"
    agent_log.write_text("agent started\nagent exited with no recognizable signature\n", encoding="utf-8")
    header = _script_header(
        log_file,
        tmp_path / "kanban_temp",
        extra_functions=["handle_working_after_exit"],
        backlog_file=backlog_file,
    )

    script = header + textwrap.dedent(f"""\
        handle_working_after_exit "{task_id}" "{status_file}" "{agent_log}" 1
    """)
    result = _run(script)
    assert result.returncode == 0, f"handler should not error\nstdout={result.stdout}\nstderr={result.stderr}"
    assert result.stdout.strip().splitlines()[-1] == "BACKLOG"

    status_text = _read(status_file)
    queue_text = _read(backlog_file)
    assert _field(status_text, "State") == "BACKLOG"
    _assert_state_and_marker_agree(status_text, queue_text, task_id)


def test_transient_signature_blocks_task_through_transition_path(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-027-fixture-g2"
    status_file = _build_status_file(tmp_path, task_id, state="WORKING")
    backlog_file = _build_backlog_file(tmp_path, task_id, marker="A")
    log_file = tmp_path / "wake.log"
    agent_log = tmp_path / "agent.log"
    agent_log.write_text("agent started\nAPI Error: 503 Service Unavailable\nagent exited\n", encoding="utf-8")
    header = _script_header(
        log_file,
        tmp_path / "kanban_temp",
        extra_functions=["handle_working_after_exit"],
        backlog_file=backlog_file,
    )

    script = header + textwrap.dedent(f"""\
        export TRANSIENT_RETRY_CAP=4
        handle_working_after_exit "{task_id}" "{status_file}" "{agent_log}" 1
    """)
    result = _run(script)
    assert result.returncode == 0, f"handler should not error\nstdout={result.stdout}\nstderr={result.stderr}"
    assert result.stdout.strip().splitlines()[-1] == "BLOCKED"

    status_text = _read(status_file)
    queue_text = _read(backlog_file)
    assert _field(status_text, "State") == "BLOCKED"
    assert _field(status_text, "Needs Human").lower() == "no"
    _assert_state_and_marker_agree(status_text, queue_text, task_id)


# ---------------------------------------------------------------------------
# (H) wake_driver_prep.sh's marker self-correction switch routes its
#     BLOCKED/WAITING/DONE/WONT-DO branches through transition_task_state;
#     its documented WORKING exception still bypasses it.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("current_state", ["BLOCKED", "WAITING", "DONE", "WONT-DO"])
def test_prep_switch_normalizes_marker_through_transition_path(tmp_path: pathlib.Path, current_state: str) -> None:
    task_id = f"CODER-20260101-028-fixture-h-{current_state.lower().replace('-', '')}"
    # Seed a marker that DISAGREES with current_state (but is still one of
    # the five legal bracket values mark_backlog's own regex accepts), so a
    # passing test proves the switch actually corrected it, not merely that
    # it was already right. None of BLOCKED/WAITING/DONE/WONT-DO map to "A",
    # so "A" is always the wrong-but-legal starting marker here.
    stale_marker = "A"
    status_file = _build_status_file(tmp_path, task_id, state=current_state)
    backlog_file = _build_backlog_file(tmp_path, task_id, marker=stale_marker)
    log_file = tmp_path / "wake.log"
    header = _script_header(log_file, tmp_path / "kanban_temp", backlog_file=backlog_file)

    block = "\n".join(_extract_prep_switch_block())
    script = header + textwrap.dedent(f"""\
        task_id="{task_id}"
        task_status="{status_file}"
        current_state="{current_state}"
        LAST_TASK_FINAL_STATE=""
        _run_prep_switch() {{
        {block}
        }}
        _run_prep_switch
        printf 'LAST_TASK_FINAL_STATE=[%s]\\n' "$LAST_TASK_FINAL_STATE"
    """)
    result = _run(script)
    assert result.returncode == 0, f"prep switch should not error\nstdout={result.stdout}\nstderr={result.stderr}"
    assert f"LAST_TASK_FINAL_STATE=[{current_state}]" in result.stdout, result.stdout

    status_text = _read(status_file)
    queue_text = _read(backlog_file)
    assert _field(status_text, "State") == current_state, "the switch must not change the task's own state"
    _assert_state_and_marker_agree(status_text, queue_text, task_id)


def test_prep_switch_working_exception_bypasses_transition_path(tmp_path: pathlib.Path) -> None:
    """The documented exception (team/SOP-core.md, "The two WORKING
    situations"): a task rediscovered here still in WORKING is marked "B"
    while ## State is deliberately left at WORKING — a disagreement by
    design, not a bug, so this scenario is exempt from the shared agreement
    check the other branches satisfy."""
    task_id = "CODER-20260101-029-fixture-h-working"
    status_file = _build_status_file(tmp_path, task_id, state="WORKING")
    backlog_file = _build_backlog_file(tmp_path, task_id, marker=" ")
    log_file = tmp_path / "wake.log"
    header = _script_header(log_file, tmp_path / "kanban_temp", backlog_file=backlog_file)

    block = "\n".join(_extract_prep_switch_block())
    script = header + textwrap.dedent(f"""\
        task_id="{task_id}"
        task_status="{status_file}"
        current_state="WORKING"
        LAST_TASK_FINAL_STATE=""
        _run_prep_switch() {{
        {block}
        }}
        _run_prep_switch
    """)
    result = _run(script)
    assert result.returncode == 0, f"prep switch should not error\nstdout={result.stdout}\nstderr={result.stderr}"

    status_text = _read(status_file)
    queue_text = _read(backlog_file)
    assert _field(status_text, "State") == "WORKING", "state must be left alone for the WORKING exception"
    assert _marker_for_task(queue_text, task_id) == "B", "marker must still normalize to B for a stuck WORKING task"
