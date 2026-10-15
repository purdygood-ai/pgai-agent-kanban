"""
test_reconcile_terminal_worktrees.py
=====================================
Behavioral coverage for reconcile_terminal_worktrees (wake_reconcile_sweeps.sh),
the companion sweep to reconcile_stale_active: it reclaims a task worktree
left behind when wd_finalize_task's own teardown step never ran for that
task (the "merge" mutating-phase-boundary reverification in
process_one_task returns before finalize whenever the chain lost the
project repo lock while the agent was dispatched, so the losing chain's
finalize — and with it, worktree teardown — never executes even though the
task's own status.md already reached a terminal state).

Unlike reconcile_stale_active (keyed off a stale "[A]" queue marker), this
sweep is keyed off status.md's own ## State, so it heals the leak
regardless of what the queue marker shows.

This module drives the real production function body (extracted via the
same awk brace-matching pattern test_zero_progress_cycle_cap.py and
test_wake_task_state_transition.py established) against a real git
repository with a real linked worktree built by the real create_task_worktree
helper — not a mock of git — so a passing test means git worktree list
actually changes, not that a stub was called correctly.

reconcile_terminal_worktrees answers "is this task's own agent still
running?" through the shared task_liveness_is_alive predicate (also
extracted below), which reads a dispatch pid recorded under
locks/task-liveness/ rather than searching every process's command line
for the task id (`pgrep -f "$task_id"`) — the latter left a finished
task's worktree unreclaimed for as long as some UNRELATED process's argv
happened to contain its id. Fixtures (A), (C), and (D) below write a
dead-pid record before sweeping (simulating the
ordinary steady state: this task's own dispatch has already exited),
isolating each fixture's own concern from the liveness question that (E)
and (F) exist to cover.

Fixtures exercised:
  (A) A DONE task with a registered worktree is torn down: git worktree
      list no longer shows an entry for it afterward, and its now-
      permanently-dead liveness record is cleaned up alongside it.
  (B) A WORKING task's worktree is never removed, even when it is
      registered and idle (a dead-pid record isolates this to the STATE
      gate specifically, not an accidental liveness skip).
  (C) A BLOCKED task's worktree is retained (conservative default — an
      operator may still be inspecting it; wd_finalize_task itself retains
      a BLOCKED worktree for TESTER and for a completion-gate refusal for
      the same reason).
  (D) Calling the sweep twice in a row (idempotent path) does not error the
      second time once the worktree is already gone.
  (E) A DONE task whose own recorded dispatch pid is still alive is
      skipped — task_liveness_is_alive's positive case, driven by a real
      pid the fixture itself records (mirroring what provider_dispatch
      does at real dispatch time), not a command-line substring match.
  (F) Regression: an unrelated process whose own argv merely contains the
      task id as a substring (the `bash -c "<text>"` shape) does NOT block
      reclaim of a terminal task's worktree — the exact false positive the
      retired `pgrep -f "$task_id"` guard produced.

Naming: function names describe behavior under test, never a bug ID or task
ID (roles/TESTER.md "Test Authoring Guidelines", Anti-pattern 6).
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
_WORKTREE_LIB = pathlib.Path("scripts/lib/worktree.sh")
_TEMP_LIB = pathlib.Path("scripts/lib/temp.sh")
_LOCK_PROVENANCE_LIB = pathlib.Path("scripts/lib/lock_provenance.sh")

# A pid this high is virtually guaranteed not to exist -- the same
# dead-pid-simulation convention test_lock_provenance.py's own staleness
# fixture uses, since a real dispatch's pid is always dead by the time a
# terminal task's worktree sweep examines it (the ordinary steady state:
# the provider CLI process that owned this task has long since exited).
_DEAD_PID = "99999999"


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
        ' '{_WAKE_TASK_STATE_LIB}' '{_WAKE_RECONCILE_SWEEPS_LIB}')"
    """)


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

            ## Needs Human
            no
        """),
        encoding="utf-8",
    )
    return status_file


def _init_dev_tree(dev_tree: pathlib.Path, rc_branch: str) -> None:
    dev_tree.mkdir(parents=True, exist_ok=True)
    _git(dev_tree, "init", "-q", "-b", "ai_main")
    _git(dev_tree, "config", "user.email", "test@example.invalid")
    _git(dev_tree, "config", "user.name", "test")
    (dev_tree / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(dev_tree, "add", "README.md")
    _git(dev_tree, "commit", "-q", "-m", "initial")
    _git(dev_tree, "branch", rc_branch)


def _git(cwd: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result


def _registered_worktree_paths(dev_tree: pathlib.Path) -> list[str]:
    result = _git(dev_tree, "worktree", "list", "--porcelain")
    return [
        line[len("worktree "):]
        for line in result.stdout.splitlines()
        if line.startswith("worktree ")
    ]


def _script_header(
    log_file: pathlib.Path,
    temp_dir: pathlib.Path,
    tasks_root: pathlib.Path,
    dev_tree: pathlib.Path,
) -> str:
    """Bash header shared by every test in this module.

    Sources the real worktree.sh, temp.sh, and lock_provenance.sh in full
    (all three declare only functions plus an include guard at top level --
    no heavy bootstrap, see worktree.sh's own "No top-level side effects
    when sourced" invariant; lock_provenance.sh self-sources its own
    error_contract.sh / ini_parser.sh dependencies relative to its own
    BASH_SOURCE, so no extra sourcing is needed here), the faithful log()
    stub, and the real get_state / task_liveness_record_path /
    task_liveness_record / task_liveness_verdict / task_liveness_is_alive
    (wake_task_state.sh) / _sweep_correct_terminal_marker / the finalize
    record and completion-gate helpers the sweep calls for a DONE task /
    reconcile_terminal_worktrees (wake_reconcile_sweeps.sh), extracted from
    their respective stage files. BACKLOG is deliberately left unset here,
    so the sweep's same-pass [A] -> [x] marker correction has nothing to
    examine and the fixtures below stay focused on the worktree question;
    test_reconcile_stale_active_terminal_marker.py covers the marker side.

    PP_project_name is deliberately left unset so pgai_worktree_base falls
    back to its Tier 3b default ($(pgai_temp_dir)/worktrees) -- predictable
    under the fixture's own temp_dir without needing a synthetic project.cfg
    (unaffected by KANBAN_ROOT below: pgai_worktree_base's own resolution
    order never reads it -- only PGAI_WORKTREE_BASE, kanban.cfg, and
    PP_project_name).

    KANBAN_ROOT is set to this fixture's own temp_dir (rather than left
    unset) so task_liveness_record_path resolves to a real, writable
    locks/task-liveness/ directory under it -- the same root every other
    pgai_lock_* provenance record already uses in production.
    """
    return textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        export PGAI_AGENT_KANBAN_TEMP_DIR="{temp_dir}"
        unset PP_project_name PGAI_WORKTREE_BASE PGAI_AGENT_KANBAN_ROOT_PATH
        KANBAN_ROOT="{temp_dir}"
        source {_TEMP_LIB}
        source {_WORKTREE_LIB}
        source {_LOCK_PROVENANCE_LIB}
        TASKS_ROOT="{tasks_root}"
        PP_dev_tree_path="{dev_tree}"

        {make_log_stub_fragment(log_file)}

        {_extract_functions_snippet(["_status_section_re", "_read_status_section", "get_state", "task_liveness_record_path", "task_liveness_record", "task_liveness_verdict", "task_liveness_is_alive", "_sweep_correct_terminal_marker", "task_finalize_record_path", "task_finalize_record_present", "_sweep_gate_unfinalized_done", "_sweep_mark_gate_refused", "reconcile_terminal_worktrees"])}
    """)


def _run(script: str, tmp_path: pathlib.Path, timeout: int = 30) -> subprocess.CompletedProcess:
    """Write the script to a file under tmp_path and run it as `bash <path>`.

    Deliberately NOT `bash -c "<script>"`: the whole script text (including
    every task_id it interpolates) would then live in THIS process's own
    argv, and the diagnostic pgrep -f "$task_id" test (F) runs to confirm
    its own unrelated process is genuinely visible would match the test
    harness's own subprocess too, corrupting the fixture. Running from a
    file keeps this process's argv to `bash <path>`, which contains no
    task_id.
    """
    script_path = tmp_path / "run_script.sh"
    script_path.write_text(script, encoding="utf-8")
    return subprocess.run(["bash", str(script_path)], capture_output=True, text=True, timeout=timeout)


# ---------------------------------------------------------------------------
# (A) DONE task, registered worktree -> torn down.
# ---------------------------------------------------------------------------


def test_done_task_worktree_is_torn_down(tmp_path: pathlib.Path) -> None:
    dev_tree = tmp_path / "dev_tree"
    tasks_root = tmp_path / "tasks"
    log_file = tmp_path / "log.txt"
    rc_branch = "rc/v1.0.0"
    task_id = "coder-worktree-reap-done"

    _init_dev_tree(dev_tree, rc_branch)
    _build_status_file(tasks_root, task_id, "DONE")

    header = _script_header(log_file, tmp_path, tasks_root, dev_tree)
    script = header + textwrap.dedent(f"""\
        create_task_worktree "{task_id}" "{rc_branch}" "feature/{task_id}" "{dev_tree}" >/dev/null
        # Simulates the ordinary steady state: this task's own dispatch
        # already recorded its pid and that process has long since exited.
        task_liveness_record "{task_id}" claude {_DEAD_PID} dispatch
        reconcile_terminal_worktrees
    """)
    result = _run(script, tmp_path)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"

    remaining = _registered_worktree_paths(dev_tree)
    assert not any(task_id in p for p in remaining), (
        f"expected no worktree registered for {task_id} after sweep; found: {remaining}"
    )
    wt_dir = tmp_path / "worktrees" / task_id
    assert not wt_dir.exists(), f"worktree directory {wt_dir} should have been removed"

    # Housekeeping: the sweep should have removed the now-permanently-dead
    # liveness record along with the worktree (task ids are never reused).
    liveness_record = tmp_path / "locks" / "task-liveness" / f"{task_id}.record"
    assert not liveness_record.exists(), (
        f"expected the liveness record for {task_id} to be cleaned up after teardown; "
        f"found: {liveness_record}"
    )


# ---------------------------------------------------------------------------
# (B) WORKING task, registered worktree -> left alone.
# ---------------------------------------------------------------------------


def test_working_task_worktree_is_preserved(tmp_path: pathlib.Path) -> None:
    dev_tree = tmp_path / "dev_tree"
    tasks_root = tmp_path / "tasks"
    log_file = tmp_path / "log.txt"
    rc_branch = "rc/v1.0.0"
    task_id = "coder-worktree-keep-working"

    _init_dev_tree(dev_tree, rc_branch)
    _build_status_file(tasks_root, task_id, "WORKING")

    header = _script_header(log_file, tmp_path, tasks_root, dev_tree)
    script = header + textwrap.dedent(f"""\
        create_task_worktree "{task_id}" "{rc_branch}" "feature/{task_id}" "{dev_tree}" >/dev/null
        # A dead-pid record isolates this test to the STATE gate specifically
        # (never DONE/WONT-DO -> no case-statement branch matches): liveness
        # itself answers "dead" here, so nothing but the state check protects
        # this worktree, matching the header's "even when idle" framing.
        task_liveness_record "{task_id}" claude {_DEAD_PID} dispatch
        reconcile_terminal_worktrees
    """)
    result = _run(script, tmp_path)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"

    remaining = _registered_worktree_paths(dev_tree)
    assert any(task_id in p for p in remaining), (
        f"expected the WORKING task's worktree to survive the sweep; found: {remaining}"
    )
    wt_dir = tmp_path / "worktrees" / task_id
    assert wt_dir.exists(), "a WORKING task's worktree directory must never be removed by the sweep"


# ---------------------------------------------------------------------------
# (C) BLOCKED task, registered worktree -> retained (conservative default).
# ---------------------------------------------------------------------------


def test_blocked_task_worktree_is_retained(tmp_path: pathlib.Path) -> None:
    dev_tree = tmp_path / "dev_tree"
    tasks_root = tmp_path / "tasks"
    log_file = tmp_path / "log.txt"
    rc_branch = "rc/v1.0.0"
    task_id = "coder-worktree-keep-blocked"

    _init_dev_tree(dev_tree, rc_branch)
    _build_status_file(tasks_root, task_id, "BLOCKED")

    header = _script_header(log_file, tmp_path, tasks_root, dev_tree)
    script = header + textwrap.dedent(f"""\
        create_task_worktree "{task_id}" "{rc_branch}" "feature/{task_id}" "{dev_tree}" >/dev/null
        task_liveness_record "{task_id}" claude {_DEAD_PID} dispatch
        reconcile_terminal_worktrees
    """)
    result = _run(script, tmp_path)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"

    remaining = _registered_worktree_paths(dev_tree)
    assert any(task_id in p for p in remaining), (
        f"expected the BLOCKED task's worktree to be retained for operator inspection; found: {remaining}"
    )


# ---------------------------------------------------------------------------
# (D) Idempotent: a second sweep after the worktree is already gone must not
#     error the finalize/sweep stage.
# ---------------------------------------------------------------------------


def test_sweep_is_idempotent_when_worktree_already_removed(tmp_path: pathlib.Path) -> None:
    dev_tree = tmp_path / "dev_tree"
    tasks_root = tmp_path / "tasks"
    log_file = tmp_path / "log.txt"
    rc_branch = "rc/v1.0.0"
    task_id = "coder-worktree-double-sweep"

    _init_dev_tree(dev_tree, rc_branch)
    _build_status_file(tasks_root, task_id, "DONE")

    header = _script_header(log_file, tmp_path, tasks_root, dev_tree)
    script = header + textwrap.dedent(f"""\
        create_task_worktree "{task_id}" "{rc_branch}" "feature/{task_id}" "{dev_tree}" >/dev/null
        task_liveness_record "{task_id}" claude {_DEAD_PID} dispatch
        reconcile_terminal_worktrees
        reconcile_terminal_worktrees
        reconcile_terminal_worktrees
    """)
    result = _run(script, tmp_path)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"

    remaining = _registered_worktree_paths(dev_tree)
    assert not any(task_id in p for p in remaining)


# ---------------------------------------------------------------------------
# (E) A DONE task whose own recorded dispatch pid is still alive is skipped
#     -- the shared task_liveness_is_alive predicate's positive case: a
#     REAL, specifically-recorded pid, not a command-line substring search.
# ---------------------------------------------------------------------------


def test_done_task_with_live_dispatch_pid_is_skipped(tmp_path: pathlib.Path) -> None:
    dev_tree = tmp_path / "dev_tree"
    tasks_root = tmp_path / "tasks"
    log_file = tmp_path / "log.txt"
    rc_branch = "rc/v1.0.0"
    task_id = "coder-worktree-live-dispatch-pid-guard"

    _init_dev_tree(dev_tree, rc_branch)
    _build_status_file(tasks_root, task_id, "DONE")

    header = _script_header(log_file, tmp_path, tasks_root, dev_tree)
    # A real backgrounded process's own pid is recorded as this task's
    # dispatch-liveness record -- exactly what provider_claude.sh /
    # provider_codex.sh's provider_dispatch does at real dispatch time --
    # so the sweep's kill -0 probe genuinely finds it alive. Killed at the
    # end of the same script so no orphan survives the test.
    script = header + textwrap.dedent(f"""\
        create_task_worktree "{task_id}" "{rc_branch}" "feature/{task_id}" "{dev_tree}" >/dev/null
        sleep 30 &
        live_pid=$!
        task_liveness_record "{task_id}" claude "$live_pid" dispatch
        reconcile_terminal_worktrees
        kill "$live_pid" 2>/dev/null || true
        wait "$live_pid" 2>/dev/null || true
    """)
    result = _run(script, tmp_path)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"

    remaining = _registered_worktree_paths(dev_tree)
    assert any(task_id in p for p in remaining), (
        f"expected the worktree of a task with a live recorded dispatch pid to be skipped this cycle; found: {remaining}"
    )


# ---------------------------------------------------------------------------
# (F) Regression: an UNRELATED process whose own argv merely mentions the
#     task id as a substring must NOT block reclaim of a terminal task's
#     worktree -- the exact false positive the old `pgrep -f "$task_id"`
#     guard produced (observed live: a DONE task's [A] marker survived 61
#     consecutive cycles because some other process's command line
#     happened to contain its id).
# ---------------------------------------------------------------------------


def test_unrelated_process_mentioning_task_id_does_not_block_reclaim(tmp_path: pathlib.Path) -> None:
    dev_tree = tmp_path / "dev_tree"
    tasks_root = tmp_path / "tasks"
    log_file = tmp_path / "log.txt"
    rc_branch = "rc/v1.0.0"
    task_id = "coder-worktree-unrelated-argv-mention"

    _init_dev_tree(dev_tree, rc_branch)
    _build_status_file(tasks_root, task_id, "DONE")

    header = _script_header(log_file, tmp_path, tasks_root, dev_tree)
    # This task's own dispatch has already exited (a dead-pid record -- the
    # ordinary steady state for any DONE task) while an entirely
    # UNRELATED process is running whose own argv contains the task id as a
    # plain substring -- a `bash -c "<text>"` process (a sibling agent's
    # dispatch prompt quoting an old task id, an operator's diagnostic
    # grep) satisfies `pgrep -f "$task_id"` for as long as it runs. The shared
    # task_liveness_is_alive predicate reads only the recorded pid and must
    # not be fooled by it. The pgrep loop below is a diagnostic wait, not
    # part of the assertion: it proves the unrelated process really is
    # visible to a command-line substring search (i.e. the OLD guard really
    # would have matched it here) before the sweep runs.
    script = header + textwrap.dedent(f"""\
        create_task_worktree "{task_id}" "{rc_branch}" "feature/{task_id}" "{dev_tree}" >/dev/null
        task_liveness_record "{task_id}" claude {_DEAD_PID} dispatch
        bash -c "sleep 5; echo mentions {task_id} in its own argv" &
        unrelated_pid=$!
        for _ in 1 2 3 4 5; do
            pgrep -f "{task_id}" >/dev/null 2>&1 && break
            sleep 0.2
        done
        reconcile_terminal_worktrees
        kill "$unrelated_pid" 2>/dev/null || true
        wait "$unrelated_pid" 2>/dev/null || true
    """)
    result = _run(script, tmp_path)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"

    remaining = _registered_worktree_paths(dev_tree)
    assert not any(task_id in p for p in remaining), (
        f"expected the worktree to be reclaimed despite an unrelated process's argv "
        f"mentioning the task id; found: {remaining}"
    )
