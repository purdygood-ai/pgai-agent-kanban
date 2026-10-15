"""
test_wake_budget_stop_keeps_feature_branch.py
=============================================
A task the wake's batch or project clock stops keeps the commits on its
feature branch when it is dispatched again.

The mechanism under test:

  team/scripts/lib/wake_task_budget.sh -- requeue_after_budget_stop returns
    the stopped task to BACKLOG and records resume mode branch for its next
    dispatch (wd_record_branch_resume): the Block Record's ``resume`` field
    when the task carries a record, otherwise the wake-owned
    ``## Resume Mode`` section -- never a new Block Record.

  team/scripts/lib/wake_driver_worktree.sh -- wd_setup_task_worktree, the
    wake prep stage that calls create_task_worktree, reads the Block
    Record's resume mode and, for a task with no record, the
    ``## Resume Mode`` section; 'branch' attaches the existing feature
    branch at its tip, anything else re-creates it from the source branch.

Each case runs, in one bash process, the real requeue_after_budget_stop
(with the real transition_task_state and queue-marker writer), the real
teardown_task_worktree the finalize stage runs after it, and the real
wd_setup_task_worktree with the real create_task_worktree, against a
throwaway git repository and a sandbox kanban root under pytest's tmp_path.
The budget stop is driven directly with an armed limit below
max_task_seconds; no watchdog, timeout or provider runs. A ``git`` wrapper
records every git call, so "no branch -D and no -b" is asserted on what
actually ran.

Naming: function names describe behaviour under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import textwrap

import pytest

from pgai_agent_kanban.overwatch.block_record import load_block_record, update_block_record, write_block_record
from tests.unit.test_worktree_resume_branch import (
    _FEATURE_BRANCH,
    _LIB,
    _RC_BRANCH,
    _TASK_ID,
    _TEAM_DIR,
    Sandbox,
    _assert_branch_recreated_from_rc,
    _assert_no_branch_delete_or_create,
    _env,
    _git,
    _git_calls,
    _make_sandbox,
)

# The stopped dispatch was armed with 270 s of batch left under a 2 h
# per-task limit: a budget stop, not a per-task timeout.
_BUDGET_ARMED, _MAX_TASK = 270, 7200

_WORKING_STATUS = textwrap.dedent(f"""\
    # Status

    ## Task
    {_TASK_ID}

    ## State
    WORKING

    ## Summary
    In progress.

    ## Blockers
    none

    ## Needs Human
    no
    """)


def _section(text: str, heading: str) -> str | None:
    """Body of ``## <heading>`` stripped, or None when the heading is absent."""
    m = re.search(rf"^##\s+{re.escape(heading)}[ \t]*\n((?:(?!##).*\n?)*)", text, re.M)
    return m.group(1).strip() if m else None


def _queue(sb: Sandbox) -> pathlib.Path:
    return sb.root / "projects" / "demo" / "tasks" / "queues" / "coder_backlog.md"


def _dispatched(sb: Sandbox) -> None:
    """The task as the stopped dispatch left it: WORKING under [A], its
    worktree still checked out on the feature branch."""
    sb.status.write_text(_WORKING_STATUS, encoding="utf-8")
    _queue(sb).parent.mkdir(parents=True, exist_ok=True)
    _queue(sb).write_text(f"# coder backlog\n\n- [A] {_TASK_ID} — example task\n", encoding="utf-8")
    _git(sb.repo, "worktree", "add", "-q", str(sb.worktree), _FEATURE_BRANCH)


def _budget_stop_then_prep(sb: Sandbox, *, budget_stop: bool = True, armed: int = _BUDGET_ARMED,
                           between: str = "") -> subprocess.CompletedProcess:
    """Run the budget stop (and the finalize teardown after it), then *between*
    (bash), then the next dispatch's worktree stage. TEAM_ROOT under tmp_path
    makes the stage skip dev-tree parking."""
    stop = textwrap.dedent(f"""\
        if requeue_after_budget_stop "$task_id" "$task_status" "$LOG_FILE" {armed} {_MAX_TASK}; then
          echo "STOP=requeued"
        else
          echo "STOP=per_task_timeout"
        fi
        teardown_task_worktree "$task_id" '{sb.repo}' >/dev/null 2>&1 || true
        """) if budget_stop else ""
    script = textwrap.dedent(f"""\
        set -uo pipefail
        cd '{_TEAM_DIR}'
        export LOG_FILE='{sb.root}/wake.log'
        log() {{ echo "[test] wake: $*" | tee -a "$LOG_FILE" >&2; }}
        BACKLOG='{_queue(sb)}'
        TASKS_ROOT='{sb.task_dir.parent}'
        source '{_LIB}/temp.sh'
        source '{_LIB}/worktree.sh'
        source '{_LIB}/pp_run_ops.sh'
        source '{_LIB}/error_contract.sh'
        source '{_LIB}/block_record.sh'
        source '{_LIB}/wake_task_state.sh'
        source '{_LIB}/wake_task_transition.sh'
        source '{_LIB}/wake_task_budget.sh'
        source '{_LIB}/wake_driver_task_setup.sh'
        source '{_LIB}/wake_driver_worktree.sh'
        task_id='{_TASK_ID}'; task_readme='{sb.task_dir}/README.md'; task_status='{sb.status}'
        TEAM_ROOT='{sb.root}'; KANBAN_ROOT='{sb.root}'; LAST_TASK_FINAL_STATE=""
        SUBAGENT=coder; role=CODER; _CURRENT_PROJECT=demo
        PP_dev_tree_path='{sb.repo}'
        _task_feature_branch=""; _task_worktree_path=""; _task_workflow_type=""
        _task_source_branch=""; _canonical_dev_tree_path='{sb.repo}'; _park_resting_branch=""; role_reminder=""
        pp_prefix_branch() {{ echo "ai_$2"; }}
        _update_readme_feature_branch() {{ :; }}
        wf_load_plugin() {{ return 0; }}
        wf_git_mode() {{ echo branch; }}
        """) + stop + between + textwrap.dedent("""\
        set -e
        wd_setup_task_worktree
        printf 'FINAL=[%s]\\nPATH_OUT=[%s]\\n' "$LAST_TASK_FINAL_STATE" "$_task_worktree_path"
        """)
    # The trace records only the calls made by the code under test.
    sb.trace.write_text("", encoding="utf-8")
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=180, env=_env(sb))


def _explain(result: subprocess.CompletedProcess) -> str:
    return f"rc={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"


# ---------------------------------------------------------------------------
# A budget stop keeps the branch
# ---------------------------------------------------------------------------


def test_budget_stop_then_redispatch_attaches_the_feature_branch_with_every_commit(tmp_path: pathlib.Path) -> None:
    commits = 3
    sb = _make_sandbox(tmp_path, feature_commits=commits)
    _dispatched(sb)
    tip_before = _git(sb.repo, "rev-parse", _FEATURE_BRANCH)

    result = _budget_stop_then_prep(sb)

    assert result.returncode == 0 and "STOP=requeued" in result.stdout, _explain(result)
    assert "FINAL=[]" in result.stdout and f"PATH_OUT=[{sb.worktree}]" in result.stdout, _explain(result)
    assert _git(sb.worktree, "rev-parse", "HEAD") == tip_before, "worktree HEAD is not the branch's old tip"
    assert _git(sb.worktree, "rev-parse", "--abbrev-ref", "HEAD") == _FEATURE_BRANCH
    assert _git(sb.repo, "rev-parse", _FEATURE_BRANCH) == tip_before, "the feature branch was moved"
    assert _git(sb.repo, "rev-list", "--count", f"{_RC_BRANCH}..{_FEATURE_BRANCH}") == str(commits), "commits were lost"
    _assert_no_branch_delete_or_create(sb)
    # The stop stays "not a task failure": BACKLOG under [ ], no Block Record.
    text = sb.status.read_text(encoding="utf-8")
    assert _section(text, "State") == "BACKLOG" and _section(text, "Needs Human") == "no", text
    assert load_block_record(sb.status) is None, f"a budget stop must not create a Block Record:\n{text}"
    assert _section(text, "Resume Mode") == "branch", text
    assert f"- [ ] {_TASK_ID}" in _queue(sb).read_text(encoding="utf-8")
    assert "## Resume Mode: branch" in result.stderr, result.stderr


def test_budget_stop_sets_branch_on_a_block_record_that_said_fresh(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path, feature_commits=2)
    _dispatched(sb)
    # An earlier block triage requeued with resume: fresh, and spent one requeue.
    write_block_record(sb.status, "zero_progress", "CODER", ["worktree=gone"])
    update_block_record(sb.status, record_requeue="zero_progress")
    tip_before = _git(sb.repo, "rev-parse", _FEATURE_BRANCH)

    result = _budget_stop_then_prep(sb)

    assert result.returncode == 0 and "FINAL=[]" in result.stdout, _explain(result)
    assert _git(sb.worktree, "rev-parse", "HEAD") == tip_before
    assert _git(sb.repo, "rev-list", "--count", f"{_RC_BRANCH}..{_FEATURE_BRANCH}") == "2"
    _assert_no_branch_delete_or_create(sb)
    record = load_block_record(sb.status)
    assert record is not None and record.resume == "branch", sb.status.read_text(encoding="utf-8")
    assert (record.reason_code, record.requeues, record.requeue_reasons) == ("zero_progress", 1, "zero_progress")
    assert _section(sb.status.read_text(encoding="utf-8"), "Resume Mode") is None, "the record carries the mode"


def test_a_block_written_after_the_budget_stop_decides_the_next_resume_mode(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path, feature_commits=2)
    _dispatched(sb)
    # After the stop, a later dispatch blocks and its record says resume: fresh:
    # the record, not the earlier ## Resume Mode section, decides.
    between = "block_record_write \"$task_status\" zero_progress CODER worktree=gone >/dev/null\n"

    result = _budget_stop_then_prep(sb, between=between)

    assert result.returncode == 0 and "FINAL=[]" in result.stdout, _explain(result)
    assert ["-C", str(sb.repo), "branch", "-D", _FEATURE_BRANCH] in _git_calls(sb)
    _assert_branch_recreated_from_rc(sb)


# ---------------------------------------------------------------------------
# Without a budget stop the fresh mode is unchanged
# ---------------------------------------------------------------------------


def test_a_task_that_never_ran_gets_its_branch_created_from_the_source_branch(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path, feature_commits=0)

    result = _budget_stop_then_prep(sb, budget_stop=False)

    assert result.returncode == 0 and "FINAL=[]" in result.stdout, _explain(result)
    assert "resume mode" not in result.stderr.lower(), result.stderr
    _assert_branch_recreated_from_rc(sb)


@pytest.mark.parametrize("record", ["resume-fresh", "no-record"])
def test_an_existing_branch_without_a_budget_stop_is_recreated_fresh(tmp_path: pathlib.Path, record: str) -> None:
    sb = _make_sandbox(tmp_path, feature_commits=2)
    if record == "resume-fresh":
        write_block_record(sb.status, "per_task_timeout", "CODER", ["worktree=gone"])

    result = _budget_stop_then_prep(sb, budget_stop=False)

    assert result.returncode == 0 and "FINAL=[]" in result.stdout, _explain(result)
    assert ["-C", str(sb.repo), "branch", "-D", _FEATURE_BRANCH] in _git_calls(sb)
    _assert_branch_recreated_from_rc(sb)


def test_a_kill_at_the_full_max_task_seconds_records_no_resume_mode(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path, feature_commits=1)
    _dispatched(sb)

    result = _budget_stop_then_prep(sb, armed=_MAX_TASK)

    assert "STOP=per_task_timeout" in result.stdout, _explain(result)
    text = sb.status.read_text(encoding="utf-8")
    assert _section(text, "State") == "WORKING", "the per-task timeout is the caller's to block"
    assert _section(text, "Resume Mode") is None, text
    assert load_block_record(sb.status) is None, text
