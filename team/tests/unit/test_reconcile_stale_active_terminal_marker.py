"""
test_reconcile_stale_active_terminal_marker.py
===============================================
Behavioral coverage for reconcile_stale_active's decision order
(wake_reconcile_sweeps.sh): the task's own status.md ## State is read
FIRST, and a task that already reached DONE or WONT-DO has its [A] queue
marker corrected to [x] whatever its liveness record says; only a task
whose own state is still non-terminal has the record consulted, through
task_liveness_verdict (wake_task_state.sh), which tells an ABSENT record
apart from one that is present but unreadable.

This module exercises the real production function bodies extracted via
the same awk brace-matching pattern test_zero_progress_cycle_cap.py and
test_wake_task_state_transition.py established, against a throwaway
kanban root under pytest's tmp_path — no real project state or the live
kanban install is read or mutated, and no queue file is hand-edited
after the sweep runs.

Fixtures exercised:
  (A) A DONE task under [A] with NO liveness record is corrected to [x] by
      one call to reconcile_stale_active; ## State stays DONE; the log
      names the task and the correction. This is the shape the sibling
      sweep leaves behind when it removes a terminal task's record as
      housekeeping — the record's absence must not read as "alive".
  (B) A DONE task under [A] whose record names a DEAD pid is corrected to
      [x], not reset to [ ]/BACKLOG (the state branch wins over the
      orphan-reset branch).
  (C) A WONT-DO task under [A] is corrected to [x] the same way.
  (D) A WORKING task under [A] whose record names a LIVE pid is kept at
      [A] with ## State untouched — unchanged behavior.
  (E) A WORKING task under [A] with NO liveness record is treated as
      orphaned: reset to BACKLOG/[ ] through the zero-progress path
      (count 1 below the default cap) with a log line naming the absent
      record — the decision the sweep's header records; never silently
      kept.
  (F) A WORKING task under [A] whose record is PRESENT but carries no
      usable pid is kept at [A]: the fail-open for what cannot be proven
      dead when the task's own state says it is still working.
  (G) task_liveness_verdict itself: absent / held / stale / unparseable
      for the four record shapes, and task_liveness_is_alive's boolean
      mapping (only "stale" reads as dead).
  (H) reconcile_terminal_worktrees, given this agent's BACKLOG, corrects a
      DONE task's [A] marker to [x] in the same pass that tears down its
      worktree, and leaves a task absent from this agent's queue alone.

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

# Real, checked-in team/scripts/ directory (holds error_contract.sh,
# temp.sh, lock_provenance.sh, overwatch_protocol.sh, worktree.sh). This
# file lives at team/tests/unit/, so three parents up is team/.
_REAL_REPO_TEAM_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "scripts"

# A pid this high is virtually guaranteed not to exist -- the same
# dead-pid-simulation convention the sibling sweep tests use.
_DEAD_PID = "99999999"

_STATE_TO_MARKER = {
    "BACKLOG": " ",
    "WAITING": "W",
    "WORKING": "A",
    "BLOCKED": "B",
    "DONE": "x",
    "WONT-DO": "x",
}


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
        ' '{_WAKE_TASK_STATE_LIB}' '{_WAKE_RECONCILE_SWEEPS_LIB}' '{_WAKE_TASK_TRANSITION_LIB}')"
    """)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _build_status_file(tasks_root: pathlib.Path, task_id: str, state: str) -> pathlib.Path:
    task_dir = tasks_root / task_id
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
            fixture.

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


def _build_backlog_file(root: pathlib.Path, entries: list[tuple[str, str]]) -> pathlib.Path:
    """entries: (marker, task_id) pairs, written in the canonical `- [M] id` shape
    with one extra entry carrying a trailing description so the parser's
    tolerance for it is exercised on every run."""
    backlog_file = root / "coder_backlog.md"
    lines = ["# Coder Backlog", ""]
    for marker, task_id in entries:
        lines.append(f"- [{marker}] {task_id}")
    lines.append("- [ ] OTHER-20260101-000-unrelated — an unrelated fixture task")
    backlog_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return backlog_file


def _script_header(
    root: pathlib.Path,
    log_file: pathlib.Path,
    backlog_file: pathlib.Path,
    tasks_root: pathlib.Path,
    extra_functions: list[str] | None = None,
    dev_tree: pathlib.Path | None = None,
) -> str:
    """Bash header shared by every test in this module.

    Sources the real overwatch_protocol.sh (zero_progress_* — the reset
    path an orphaned WORKING task takes), error_contract.sh / temp.sh
    (transition_task_state's dependencies), lock_provenance.sh
    (task_liveness_verdict's classifier), worktree.sh (only fixture (H)
    uses it), the faithful log() stub, and the real function bodies
    extracted from their stage files.

    KANBAN_ROOT is `root` so task_liveness_record_path resolves to a real,
    writable locks/task-liveness/ under the fixture. PP_project_name and
    PGAI_WORKTREE_BASE are unset so pgai_worktree_base falls back to
    $(pgai_temp_dir)/worktrees under the fixture's own temp dir.
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
        "task_finalize_record_path",
        "task_finalize_record_present",
        "_sweep_gate_unfinalized_done",
        "_sweep_mark_gate_refused",
        "reconcile_stale_active",
        "reconcile_terminal_worktrees",
    ]
    if extra_functions:
        funcs += [f for f in extra_functions if f not in funcs]
    dev_tree_export = f'PP_dev_tree_path="{dev_tree}"' if dev_tree else "unset PP_dev_tree_path PGAI_DEV_TREE_PATH"
    return textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        export PGAI_AGENT_KANBAN_TEMP_DIR="{root / 'kanban_temp'}"
        unset PGAI_AGENT_KANBAN_ROOT_PATH PP_project_name PGAI_WORKTREE_BASE
        unset ZERO_PROGRESS_CAP TRANSIENT_RETRY_CAP
        export KANBAN_ROOT="{root}"
        source "{_REAL_REPO_TEAM_SCRIPTS_DIR}/lib/overwatch_protocol.sh"
        source "{_REAL_REPO_TEAM_SCRIPTS_DIR}/lib/error_contract.sh"
        source "{_REAL_REPO_TEAM_SCRIPTS_DIR}/lib/wake_task_id_gate.sh"
        source "{_REAL_REPO_TEAM_SCRIPTS_DIR}/lib/atomic_write.sh"
        source "{_REAL_REPO_TEAM_SCRIPTS_DIR}/lib/temp.sh"
        source "{_REAL_REPO_TEAM_SCRIPTS_DIR}/lib/lock_provenance.sh"
        source "{_REAL_REPO_TEAM_SCRIPTS_DIR}/lib/worktree.sh"
        BACKLOG="{backlog_file}"
        TASKS_ROOT="{tasks_root}"
        {dev_tree_export}

        {make_log_stub_fragment(log_file)}

        {_extract_functions_snippet(funcs)}
    """)


def _run(script: str, root: pathlib.Path, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run from a file, not `bash -c`, so no task id appears in this
    process's own argv (the same precaution test_reconcile_terminal_worktrees.py
    takes)."""
    script_path = root / "run_script.sh"
    script_path.write_text(script, encoding="utf-8")
    return subprocess.run(["bash", str(script_path)], capture_output=True, text=True, timeout=timeout)


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _field(status_text: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}\s*\n(.*?)(\n+##|\Z)", status_text, flags=re.S | re.M)
    assert m is not None, f"## {heading} section not found in:\n{status_text}"
    return m.group(1).strip()


def _zp_count(status_text: str) -> str | None:
    m = re.search(r"^## Zero Progress\s*\n(.*?)(\n+##|\Z)", status_text, flags=re.S | re.M)
    if not m or m.group(1).strip().lower() == "none":
        return None
    c = re.search(r"^count:\s*(.*)$", m.group(1), flags=re.M)
    return c.group(1).strip() if c else None


def _marker_for_task(queue_text: str, task_id: str) -> str:
    m = re.search(rf"^\s*-\s*\[(.)\]\s+{re.escape(task_id)}\b", queue_text, flags=re.M)
    assert m is not None, f"queue entry for {task_id} not found in:\n{queue_text}"
    return m.group(1)


def _assert_state_and_marker_agree(status_text: str, queue_text: str, task_id: str) -> None:
    state = _field(status_text, "State")
    marker = _marker_for_task(queue_text, task_id)
    expected = _STATE_TO_MARKER[state]
    assert marker == expected, (
        f"status.md ## State={state!r} maps to marker {expected!r}, but the queue "
        f"marker for {task_id} reads {marker!r} — the two surfaces disagree"
    )


def _record_path(root: pathlib.Path, task_id: str) -> pathlib.Path:
    return root / "locks" / "task-liveness" / f"{task_id}.record"


# ---------------------------------------------------------------------------
# (A) DONE + [A] + NO record -> [x]; state unchanged; log names the task.
# ---------------------------------------------------------------------------


def test_done_task_with_absent_record_is_corrected_to_x(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-041-terminal-marker-absent-record"
    tasks_root = tmp_path / "tasks"
    status_file = _build_status_file(tasks_root, task_id, "DONE")
    backlog_file = _build_backlog_file(tmp_path, [("A", task_id)])
    log_file = tmp_path / "wake.log"
    before_state_text = _read(status_file)

    assert not _record_path(tmp_path, task_id).exists(), "fixture precondition: no liveness record"

    header = _script_header(tmp_path, log_file, backlog_file, tasks_root)
    result = _run(header + "reconcile_stale_active\n", tmp_path)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"

    status_text = _read(status_file)
    queue_text = _read(backlog_file)
    assert _field(status_text, "State") == "DONE", f"the task's own state must not move\n{status_text}"
    assert _marker_for_task(queue_text, task_id) == "x", f"marker must be corrected to [x]\n{queue_text}"
    _assert_state_and_marker_agree(status_text, queue_text, task_id)
    # The status file is re-normalized, never left with the extra blank line
    # set_state's block rewrite introduces.
    assert "\n\n\n" not in status_text, f"status.md must be normalized after the correction\n{status_text!r}"
    assert _field(before_state_text, "Summary") == _field(status_text, "Summary"), "no other section may change"

    log_text = _read(log_file)
    assert re.search(rf"stale-active: {re.escape(task_id)} is DONE .*corrected marker \[A\] -> \[x\]", log_text), (
        f"the log must name the task and the correction\n{log_text}"
    )
    assert "has active process, keeping [A]" not in log_text, (
        f"an absent record must never be reported as a live process for a terminal task\n{log_text}"
    )
    # Never escalated through the orphan path: no zero-progress record.
    assert _zp_count(status_text) is None, f"a terminal task must not enter the zero-progress path\n{status_text}"


# ---------------------------------------------------------------------------
# (B) DONE + [A] + DEAD pid -> [x], not [ ]/BACKLOG.
# ---------------------------------------------------------------------------


def test_done_task_with_dead_pid_record_is_corrected_not_reset(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-042-terminal-marker-dead-pid"
    tasks_root = tmp_path / "tasks"
    status_file = _build_status_file(tasks_root, task_id, "DONE")
    backlog_file = _build_backlog_file(tmp_path, [("A", task_id)])
    log_file = tmp_path / "wake.log"

    header = _script_header(tmp_path, log_file, backlog_file, tasks_root)
    script = header + textwrap.dedent(f"""\
        task_liveness_record "{task_id}" claude {_DEAD_PID} dispatch
        reconcile_stale_active
    """)
    result = _run(script, tmp_path)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"

    status_text = _read(status_file)
    queue_text = _read(backlog_file)
    assert _field(status_text, "State") == "DONE", f"a DONE task must never be reset to BACKLOG\n{status_text}"
    assert _marker_for_task(queue_text, task_id) == "x", f"marker must read [x], not [ ]\n{queue_text}"
    assert _zp_count(status_text) is None, f"a DONE task must not be counted as a zero-progress cycle\n{status_text}"
    log_text = _read(log_file)
    assert "resetting to [ ]/BACKLOG" not in log_text, f"the orphan-reset branch must not run for a DONE task\n{log_text}"


# ---------------------------------------------------------------------------
# (C) WONT-DO is terminal too.
# ---------------------------------------------------------------------------


def test_wont_do_task_under_active_marker_is_corrected_to_x(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-043-terminal-marker-wont-do"
    tasks_root = tmp_path / "tasks"
    status_file = _build_status_file(tasks_root, task_id, "WONT-DO")
    backlog_file = _build_backlog_file(tmp_path, [("A", task_id)])
    log_file = tmp_path / "wake.log"

    header = _script_header(tmp_path, log_file, backlog_file, tasks_root)
    result = _run(header + "reconcile_stale_active\n", tmp_path)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"

    status_text = _read(status_file)
    queue_text = _read(backlog_file)
    assert _field(status_text, "State") == "WONT-DO"
    assert _marker_for_task(queue_text, task_id) == "x"
    _assert_state_and_marker_agree(status_text, queue_text, task_id)
    assert re.search(rf"stale-active: {re.escape(task_id)} is WONT-DO .*\[A\] -> \[x\]", _read(log_file))


# ---------------------------------------------------------------------------
# (D) WORKING + [A] + LIVE pid -> kept at [A] (unchanged behavior).
# ---------------------------------------------------------------------------


def test_working_task_with_live_pid_is_kept_active(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-044-terminal-marker-live-pid"
    tasks_root = tmp_path / "tasks"
    status_file = _build_status_file(tasks_root, task_id, "WORKING")
    backlog_file = _build_backlog_file(tmp_path, [("A", task_id)])
    log_file = tmp_path / "wake.log"
    before_status = _read(status_file)
    before_queue = _read(backlog_file)

    header = _script_header(tmp_path, log_file, backlog_file, tasks_root)
    # A real backgrounded process's own pid is recorded, exactly as
    # provider_dispatch does; killed at the end of the same script.
    script = header + textwrap.dedent(f"""\
        sleep 30 &
        live_pid=$!
        task_liveness_record "{task_id}" claude "$live_pid" dispatch
        reconcile_stale_active
        kill "$live_pid" 2>/dev/null || true
        wait "$live_pid" 2>/dev/null || true
    """)
    result = _run(script, tmp_path)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"

    assert _read(status_file) == before_status, "a live WORKING task's status.md must be untouched"
    assert _read(backlog_file) == before_queue, "a live WORKING task's [A] marker must be kept"
    assert f"stale-active: {task_id} has active process, keeping [A]" in _read(log_file)


# ---------------------------------------------------------------------------
# (E) WORKING + [A] + NO record -> treated as orphaned: reset once through
#     the zero-progress path, with a log line naming the absent record.
# ---------------------------------------------------------------------------


def test_working_task_with_absent_record_is_reset_through_zero_progress_path(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-045-terminal-marker-working-absent"
    tasks_root = tmp_path / "tasks"
    status_file = _build_status_file(tasks_root, task_id, "WORKING")
    backlog_file = _build_backlog_file(tmp_path, [("A", task_id)])
    log_file = tmp_path / "wake.log"
    assert not _record_path(tmp_path, task_id).exists()

    header = _script_header(tmp_path, log_file, backlog_file, tasks_root)
    result = _run(header + "reconcile_stale_active\n", tmp_path)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"

    status_text = _read(status_file)
    queue_text = _read(backlog_file)
    assert _field(status_text, "State") == "BACKLOG", (
        f"an orphaned WORKING task with no record must be reset, never kept forever\n{status_text}"
    )
    _assert_state_and_marker_agree(status_text, queue_text, task_id)
    assert _zp_count(status_text) == "1", f"the reset must be counted so the cap bounds repeats\n{status_text}"
    assert _field(status_text, "Needs Human").lower() == "no", "one reset below the cap must not escalate"

    log_text = _read(log_file)
    assert re.search(rf"stale-active: {re.escape(task_id)} has no liveness record at \S+{re.escape(task_id)}\.record", log_text), (
        f"the decision must be logged naming the absent record path\n{log_text}"
    )
    assert "has active process, keeping [A]" not in log_text, (
        f"an absent record must not be reported as a live process\n{log_text}"
    )


# ---------------------------------------------------------------------------
# (F) WORKING + [A] + record PRESENT but unreadable -> kept at [A].
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "record_body",
    [
        pytest.param("", id="empty-file"),
        pytest.param("holder=claude\npid=\nop=dispatch\ntask=x\n", id="blank-pid"),
        pytest.param("garbage without any field\n", id="no-fields"),
    ],
)
def test_working_task_with_unreadable_record_is_kept_active(tmp_path: pathlib.Path, record_body: str) -> None:
    task_id = "CODER-20260101-046-terminal-marker-working-unparseable"
    tasks_root = tmp_path / "tasks"
    status_file = _build_status_file(tasks_root, task_id, "WORKING")
    backlog_file = _build_backlog_file(tmp_path, [("A", task_id)])
    log_file = tmp_path / "wake.log"
    record = _record_path(tmp_path, task_id)
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(record_body, encoding="utf-8")
    before_status = _read(status_file)
    before_queue = _read(backlog_file)

    header = _script_header(tmp_path, log_file, backlog_file, tasks_root)
    result = _run(header + "reconcile_stale_active\n", tmp_path)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"

    assert _read(status_file) == before_status, "what cannot be proven dead must not be torn down"
    assert _read(backlog_file) == before_queue, "the [A] marker must be kept for an unreadable record"
    log_text = _read(log_file)
    assert f"stale-active: {task_id} liveness cannot be determined" in log_text, log_text
    assert "keeping [A]" in log_text


# ---------------------------------------------------------------------------
# (G) task_liveness_verdict: four shapes, four words; is_alive maps only
#     "stale" to dead.
# ---------------------------------------------------------------------------


def test_liveness_verdict_distinguishes_absent_from_unparseable(tmp_path: pathlib.Path) -> None:
    tasks_root = tmp_path / "tasks"
    backlog_file = _build_backlog_file(tmp_path, [])
    log_file = tmp_path / "wake.log"
    unparseable_record = _record_path(tmp_path, "verdict-unparseable")
    unparseable_record.parent.mkdir(parents=True, exist_ok=True)
    unparseable_record.write_text("holder=claude\npid=\nop=dispatch\ntask=verdict-unparseable\n", encoding="utf-8")

    header = _script_header(tmp_path, log_file, backlog_file, tasks_root)
    script = header + textwrap.dedent(f"""\
        sleep 30 &
        live_pid=$!
        task_liveness_record verdict-held claude "$live_pid" dispatch
        task_liveness_record verdict-stale claude {_DEAD_PID} dispatch
        printf 'absent=%s\\n'      "$(task_liveness_verdict verdict-absent)"
        printf 'held=%s\\n'        "$(task_liveness_verdict verdict-held)"
        printf 'stale=%s\\n'       "$(task_liveness_verdict verdict-stale)"
        printf 'unparseable=%s\\n' "$(task_liveness_verdict verdict-unparseable)"
        printf 'noroot=%s\\n'      "$(KANBAN_ROOT= task_liveness_verdict verdict-held)"
        for t in verdict-absent verdict-held verdict-stale verdict-unparseable; do
            if task_liveness_is_alive "$t"; then printf 'alive:%s=yes\\n' "$t"; else printf 'alive:%s=no\\n' "$t"; fi
        done
        kill "$live_pid" 2>/dev/null || true
        wait "$live_pid" 2>/dev/null || true
    """)
    result = _run(script, tmp_path)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    out = result.stdout
    assert "absent=absent" in out, out
    assert "held=held" in out, out
    assert "stale=stale" in out, out
    assert "unparseable=unparseable" in out, out
    # An unresolvable record path is "cannot determine", never "no record".
    assert "noroot=unparseable" in out, out
    assert "alive:verdict-absent=yes" in out, out
    assert "alive:verdict-held=yes" in out, out
    assert "alive:verdict-stale=no" in out, out
    assert "alive:verdict-unparseable=yes" in out, out


# ---------------------------------------------------------------------------
# (H) reconcile_terminal_worktrees corrects [A] -> [x] in the same pass it
#     tears the worktree down, for an entry in this agent's own queue.
# ---------------------------------------------------------------------------


def _git(cwd: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result


def _init_dev_tree(dev_tree: pathlib.Path, rc_branch: str) -> None:
    dev_tree.mkdir(parents=True, exist_ok=True)
    _git(dev_tree, "init", "-q", "-b", "ai_main")
    _git(dev_tree, "config", "user.email", "test@example.invalid")
    _git(dev_tree, "config", "user.name", "test")
    (dev_tree / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(dev_tree, "add", "README.md")
    _git(dev_tree, "commit", "-q", "-m", "initial")
    _git(dev_tree, "branch", rc_branch)


def _registered_worktree_paths(dev_tree: pathlib.Path) -> list[str]:
    result = _git(dev_tree, "worktree", "list", "--porcelain")
    return [line[len("worktree "):] for line in result.stdout.splitlines() if line.startswith("worktree ")]


def test_terminal_worktree_sweep_corrects_marker_in_same_pass(tmp_path: pathlib.Path) -> None:
    rc_branch = "rc/v1.0.0"
    own_task = "CODER-20260101-047-terminal-sweep-own-queue"
    other_task = "writer-terminal-sweep-other-queue"
    tasks_root = tmp_path / "tasks"
    dev_tree = tmp_path / "dev_tree"
    _init_dev_tree(dev_tree, rc_branch)
    own_status = _build_status_file(tasks_root, own_task, "DONE")
    _build_status_file(tasks_root, other_task, "DONE")
    # Only own_task is in THIS agent's queue; other_task belongs to another
    # agent's queue file and must be left for that agent's own sweep.
    backlog_file = _build_backlog_file(tmp_path, [("A", own_task)])
    log_file = tmp_path / "wake.log"
    before_queue = _read(backlog_file)

    header = _script_header(tmp_path, log_file, backlog_file, tasks_root, dev_tree=dev_tree)
    script = header + textwrap.dedent(f"""\
        create_task_worktree "{own_task}" "{rc_branch}" "feature/{own_task}" "{dev_tree}" >/dev/null
        create_task_worktree "{other_task}" "{rc_branch}" "feature/{other_task}" "{dev_tree}" >/dev/null
        task_liveness_record "{own_task}" claude {_DEAD_PID} dispatch
        task_liveness_record "{other_task}" claude {_DEAD_PID} dispatch
        reconcile_terminal_worktrees
    """)
    result = _run(script, tmp_path)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"

    remaining = _registered_worktree_paths(dev_tree)
    assert not any(own_task in p for p in remaining), f"own task's worktree must be torn down; found {remaining}"
    assert not any(other_task in p for p in remaining), f"other task's worktree must be torn down too; found {remaining}"

    status_text = _read(own_status)
    queue_text = _read(backlog_file)
    assert _field(status_text, "State") == "DONE"
    assert _marker_for_task(queue_text, own_task) == "x", f"same-pass marker correction must land\n{queue_text}"
    _assert_state_and_marker_agree(status_text, queue_text, own_task)
    assert other_task not in queue_text and queue_text != before_queue, "only the own-queue entry changes"
    log_text = _read(log_file)
    assert re.search(rf"reconcile-terminal-worktrees: {re.escape(own_task)} is DONE .*\[A\] -> \[x\]", log_text), log_text
    assert f"reconcile-terminal-worktrees: {other_task} is DONE in status.md; corrected" not in log_text, (
        f"a task absent from this agent's queue must not be reported as corrected\n{log_text}"
    )
    # Housekeeping still happens after a successful teardown.
    assert not _record_path(tmp_path, own_task).exists()
