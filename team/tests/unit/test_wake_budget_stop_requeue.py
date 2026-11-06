"""
test_wake_budget_stop_requeue.py
================================
A task the watchdog stops because the wake's remaining batch or project
time ran out is requeued, not blocked; only a kill at the full
max_task_seconds is a per-task timeout.

The mechanism under test:

  team/scripts/lib/wake_task_budget.sh -- wd_task_limit_was_budget_cap
    tells a watchdog limit the remaining budget armed (below
    max_task_seconds, or with no per-task bound) from one max_task_seconds
    armed.

  team/scripts/lib/wake_driver_finalize.sh -- wd_finalize_task, after a
    watchdog kill, sends a budget-armed kill through
    requeue_after_budget_stop (State BACKLOG, Needs Human no, no new Block
    Record, the queue marker back to "[ ]") and keeps the per_task_timeout
    block (State BLOCKED, Needs Human yes, Block Record per_task_timeout)
    for a kill at the full max_task_seconds.

  team/scripts/lib/wake_task_budget.sh -- requeue_after_timeout_with_commits:
    a kill at the full max_task_seconds after the agent committed work on
    its feature branch records the timeout and one requeue on the Block
    Record, sets its resume to branch and returns the task to BACKLOG while
    the Block Record's requeues is below [overwatch] requeue_budget; with no
    commits, or with the budget spent, the task is blocked exactly as
    before. With the budget unreadable the task is blocked too, and one
    operator inbox entry names the read error, however often it recurs.

  Requeue budget -- a budget stop leaves a Block Record the task already
    carries in place, so its requeues / requeue_reasons (the budget block
    triage spends, reset only by DONE or WONT-DO) carry into the task's
    next block.

  team/pgai_agent_kanban/overwatch/block_storm.py -- two budget stops in
    the storm window leave nothing for the dispatch gate to count, with or
    without a Block Record kept from an earlier block, while two real
    per-task timeouts in the same window still form a storm.

Each finalize case drives the real wd_finalize_task in a sandbox root under
pytest's tmp_path, with the same prelude and collaborator stubs
tests/unit/test_block_sites_write_block_record.py uses for its
per-task-timeout site. No real provider, lock or worktree is involved.
"""

from __future__ import annotations

import datetime
import pathlib
import subprocess
import textwrap

import pytest

from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.overwatch import inbox
from pgai_agent_kanban.overwatch.block_record import load_block_record, update_block_record, write_block_record
from pgai_agent_kanban.overwatch.block_storm import decide, scan_human_gated_blocks
from tests.unit.test_block_sites_write_block_record import (
    _FEATURE_BRANCH,
    _LIB,
    _RC_BRANCH,
    _TASK_ID,
    _make_repo,
    _make_sandbox,
    _run,
    _section,
    _status_text,
)

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SECOND_TASK = "WRITER-20260101-002-second-task"


def _predicate(armed: str, max_task: str) -> int:
    script = f"source '{_LIB}/wake_task_budget.sh'; wd_task_limit_was_budget_cap '{armed}' '{max_task}'"
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30).returncode


@pytest.mark.parametrize(
    ("armed", "max_task", "budget_cap"),
    [
        ("270", "7200", True),  # 270 s of batch left under a 2 h per-task limit
        ("7200", "7200", False),  # the per-task limit itself
        ("270", "0", True),  # no per-task bound: any armed limit is the budget's
        ("0", "7200", False),  # nothing armed
        ("", "7200", False),  # unreadable armed limit
    ],
)
def test_budget_cap_predicate(armed: str, max_task: str, budget_cap: bool) -> None:
    assert (_predicate(armed, max_task) == 0) is budget_cap


def test_270_seconds_of_batch_left_arms_a_budget_capped_limit() -> None:
    script = textwrap.dedent(f"""\
        source '{_LIB}/wake_task_budget.sh'
        armed="$(wd_effective_task_timeout 7200 0 0 14400 14130)"
        echo "ARMED=$armed"
        wd_task_limit_was_budget_cap "$armed" 7200 && echo "BUDGET_CAP=yes" || echo "BUDGET_CAP=no"
        """)
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30).stdout
    assert "ARMED=270" in out and "BUDGET_CAP=yes" in out, out


def _finalize_kill(
    sb, task_id: str, armed: int, max_task: int | None, *, with_branch: bool = False
) -> subprocess.CompletedProcess:
    """Drive wd_finalize_task for a watchdog kill at <armed> seconds; a
    <max_task> of None leaves MAX_TASK_SECONDS unset. *with_branch* points
    the task at the sandbox repo's feature and RC branches."""
    task_dir = sb.task_dir.parent / task_id
    max_task_line = f"MAX_TASK_SECONDS={max_task}" if max_task is not None else "unset MAX_TASK_SECONDS"
    repo, branch, source = (sb.repo, _FEATURE_BRANCH, _RC_BRANCH) if with_branch else ("", "", "")
    return _run(
        sb,
        textwrap.dedent(f"""\
        source '{_LIB}/wake_driver_finalize.sh'
        narrate_model_mismatch() {{ :; }}
        metrics_rollup_refresh_for_task() {{ :; }}
        provider_dispatch_refused() {{ return 1; }}
        pgai_lock_reverify_or_narrate() {{ return 0; }}
        wake_bracket_post_session() {{ :; }}
        teardown_task_worktree() {{ :; }}
        task_id='{task_id}'; task_dir='{task_dir}'; task_status='{task_dir}/status.md'
        AGENT=writer; SUBAGENT=writer; role=WRITER; _CURRENT_PROJECT=demo
        _task_killed=true; _provider_pgid=""; _provider_timeout_seconds={armed}; {max_task_line}
        agent_exit=124; task_artifact_dir="$task_dir/artifacts"; _debug_gate=false
        _pollution_snapshot_ok=false; _litter_snapshot_file=""; _project_lock_file=""
        _task_worktree_path=""; _task_feature_branch='{branch}'; _task_source_branch='{source}'
        _canonical_dev_tree_path='{repo}'; _park_resting_branch=""
        wd_finalize_task
        echo "FINAL=$LAST_TASK_FINAL_STATE"
        """),
    )


def _add_task(sb, task_id: str) -> pathlib.Path:
    task_dir = sb.task_dir.parent / task_id
    if not task_dir.exists():
        (task_dir / "logs").mkdir(parents=True)
        (task_dir / "status.md").write_text(_status_text("WORKING"), encoding="utf-8")
        (task_dir / "README.md").write_text(f"# Task\n\n## Task ID\n{task_id}\n\n## Role\nWRITER\n", encoding="utf-8")
    return task_dir / "status.md"


def test_kill_at_the_batch_limit_requeues_without_a_block(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path, role="WRITER")

    result = _finalize_kill(sb, _TASK_ID, armed=270, max_task=7200)

    text = sb.status.read_text(encoding="utf-8")
    assert "FINAL=BACKLOG" in result.stdout, result.stderr
    assert _section(text, "State") == "BACKLOG", text
    assert _section(text, "Needs Human") == "no", text
    assert load_block_record(sb.status) is None, f"a budget stop must write no Block Record:\n{text}"
    assert "per_task_timeout" not in text, text
    assert "batch or project time budget ran out" in _section(text, "Summary"), text
    assert (sb.root / "markers.log").read_text(encoding="utf-8").strip() == f"{_TASK_ID} [ ]"
    assert "not a per-task timeout" in result.stderr, result.stderr


def test_kill_at_the_full_max_task_seconds_still_blocks_as_a_timeout(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path, role="WRITER")

    result = _finalize_kill(sb, _TASK_ID, armed=7200, max_task=7200)

    text = sb.status.read_text(encoding="utf-8")
    assert "FINAL=BLOCKED" in result.stdout, result.stderr
    assert _section(text, "State") == "BLOCKED", text
    assert _section(text, "Needs Human") == "yes", text
    record = load_block_record(sb.status)
    assert record is not None and record.reason_code == "per_task_timeout", text


def test_kill_at_the_registry_default_with_max_task_seconds_unset_blocks_as_a_timeout(tmp_path: pathlib.Path) -> None:
    """An unset MAX_TASK_SECONDS reads as the registry default, not 0 (no
    per-task bound), so a kill at that default is a per-task timeout rather
    than a budget stop, and the block reason names the default."""
    default = subprocess.run(
        ["bash", "-c", f"source '{_LIB}/config_loader.sh'; config_wake_max_task_seconds_default"],
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    assert default.isdigit(), default
    sb = _make_sandbox(tmp_path, role="WRITER")

    result = _finalize_kill(sb, _TASK_ID, armed=int(default), max_task=None)

    text = sb.status.read_text(encoding="utf-8")
    assert "FINAL=BLOCKED" in result.stdout, result.stderr
    record = load_block_record(sb.status)
    assert record is not None and record.reason_code == "per_task_timeout", text
    assert f"configured max_task_seconds={default}s" in text, text


def _storm_verdict(sb, task_ids: tuple[str, ...]) -> str:
    """The dispatch gate's verdict with every task's queue line read as [B]."""
    queues = sb.root / "projects" / "demo" / "tasks" / "queues"
    queues.mkdir(parents=True, exist_ok=True)
    (queues / "writer_backlog.md").write_text(
        "# writer backlog\n\n" + "".join(f"- [B] {t}\n" for t in task_ids), encoding="utf-8"
    )
    blocks = scan_human_gated_blocks(queues, sb.task_dir.parent)
    return decide(blocks, window_s=3600, now=datetime.datetime.now(datetime.UTC)).verdict


def test_two_batch_limit_kills_in_the_storm_window_do_not_hold_dispatch(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path, role="WRITER")
    _add_task(sb, _SECOND_TASK)

    _finalize_kill(sb, _TASK_ID, armed=270, max_task=7200)
    _finalize_kill(sb, _SECOND_TASK, armed=95, max_task=7200)

    assert _storm_verdict(sb, (_TASK_ID, _SECOND_TASK)) == "none"


def test_two_real_timeouts_in_the_storm_window_still_hold_dispatch(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path, role="WRITER")
    _add_task(sb, _SECOND_TASK)

    _finalize_kill(sb, _TASK_ID, armed=7200, max_task=7200)
    _finalize_kill(sb, _SECOND_TASK, armed=7200, max_task=7200)

    assert _storm_verdict(sb, (_TASK_ID, _SECOND_TASK)) == "systemic"


# ---------------------------------------------------------------------------
# The requeue budget survives a budget stop
# ---------------------------------------------------------------------------


def _carry_spent_requeues(status: pathlib.Path) -> None:
    """Give *status* the Block Record of a task triage has requeued twice."""
    write_block_record(status, "per_task_timeout", "WRITER", ["timeout_s=7200"])
    update_block_record(status, record_requeue="per_task_timeout")
    update_block_record(status, record_requeue="gate_dirty_worktree")


def _redispatch(status: pathlib.Path) -> None:
    """The wake's next dispatch of a BACKLOG task: State back to WORKING."""
    text = status.read_text(encoding="utf-8")
    status.write_text(text.replace("## State\nBACKLOG", "## State\nWORKING"), encoding="utf-8")


def test_budget_stop_keeps_an_existing_block_record_and_its_requeue_count(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path, role="WRITER")
    _carry_spent_requeues(sb.status)

    result = _finalize_kill(sb, _TASK_ID, armed=270, max_task=7200)

    text = sb.status.read_text(encoding="utf-8")
    assert "FINAL=BACKLOG" in result.stdout, result.stderr
    assert _section(text, "State") == "BACKLOG", text
    assert _section(text, "Needs Human") == "no", text
    record = load_block_record(sb.status)
    assert record is not None, f"a budget stop must keep the Block Record the task carried:\n{text}"
    assert record.requeues == 2, text
    assert record.requeue_reasons == "per_task_timeout, gate_dirty_worktree", text


def test_requeue_count_survives_a_budget_stop_followed_by_a_real_timeout(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path, role="WRITER")
    _carry_spent_requeues(sb.status)

    _finalize_kill(sb, _TASK_ID, armed=270, max_task=7200)  # the batch clock ran out
    _redispatch(sb.status)
    result = _finalize_kill(sb, _TASK_ID, armed=7200, max_task=7200)  # the task's own limit

    text = sb.status.read_text(encoding="utf-8")
    assert "FINAL=BLOCKED" in result.stdout, result.stderr
    record = load_block_record(sb.status)
    assert record is not None and record.reason_code == "per_task_timeout", text
    assert record.requeues >= 2, f"the budget stop reset the requeue budget:\n{text}"
    assert record.requeue_reasons.startswith("per_task_timeout, gate_dirty_worktree"), text


def test_two_budget_stops_on_tasks_with_kept_records_do_not_hold_dispatch(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path, role="WRITER")
    second = _add_task(sb, _SECOND_TASK)
    _carry_spent_requeues(sb.status)
    _carry_spent_requeues(second)

    _finalize_kill(sb, _TASK_ID, armed=270, max_task=7200)
    _finalize_kill(sb, _SECOND_TASK, armed=95, max_task=7200)

    assert load_block_record(sb.status) is not None and load_block_record(second) is not None
    assert _storm_verdict(sb, (_TASK_ID, _SECOND_TASK)) == "none"


# ---------------------------------------------------------------------------
# A per-task timeout after committed work requeues onto its branch
# ---------------------------------------------------------------------------


def _timeout_blockers(sb) -> str:
    """The ## Blockers text a per-task timeout at 7200 s writes."""
    return (
        "exceeded max_task_seconds (7200s effective per-task limit; configured max_task_seconds=7200s). "
        f"Agent was still running after the hard timeout. See {sb.batch_log} for execution log."
    )


def test_timeout_after_committed_work_requeues_with_resume_branch(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path, role="WRITER")
    _make_repo(sb, feature_commits=2)

    result = _finalize_kill(sb, _TASK_ID, armed=7200, max_task=7200, with_branch=True)

    text = sb.status.read_text(encoding="utf-8")
    assert "FINAL=BACKLOG" in result.stdout, result.stderr
    assert _section(text, "State") == "BACKLOG", text
    assert _section(text, "Needs Human") == "no", text
    assert (sb.root / "markers.log").read_text(encoding="utf-8").strip() == f"{_TASK_ID} [ ]"
    record = load_block_record(sb.status)
    assert record is not None and record.reason_code == "per_task_timeout", text
    assert record.resume == "branch", text
    assert (record.requeues, record.requeue_reasons) == (1, "per_task_timeout"), text
    assert "ahead=2" in record.evidence and "timeout_s=7200" in record.evidence, record.evidence
    summary = _section(text, "Summary")
    assert "per-task timeout" in summary and "Requeue 1 of 3" in summary, summary
    assert "returning it to BACKLOG to resume on that branch" in result.stderr, result.stderr


@pytest.mark.parametrize("feature_commits", [0, None], ids=["branch-without-commits", "no-branch"])
def test_timeout_without_commits_blocks_with_the_same_message(
    tmp_path: pathlib.Path, feature_commits: int | None
) -> None:
    sb = _make_sandbox(tmp_path, role="WRITER")
    if feature_commits is not None:
        _make_repo(sb, feature_commits=feature_commits)

    result = _finalize_kill(sb, _TASK_ID, armed=7200, max_task=7200, with_branch=feature_commits is not None)

    text = sb.status.read_text(encoding="utf-8")
    assert "FINAL=BLOCKED" in result.stdout, result.stderr
    assert _section(text, "Needs Human") == "yes", text
    assert _section(text, "Blockers") == _timeout_blockers(sb), text
    record = load_block_record(sb.status)
    assert record is not None and record.reason_code == "per_task_timeout", text
    assert (record.resume, record.requeues) == ("fresh", 0), text


def test_timeouts_after_committed_work_block_once_the_requeue_budget_is_spent(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path, role="WRITER")
    _make_repo(sb, feature_commits=1)

    for attempt in (1, 2, 3):  # the default [overwatch] requeue_budget is 3
        result = _finalize_kill(sb, _TASK_ID, armed=7200, max_task=7200, with_branch=True)
        assert "FINAL=BACKLOG" in result.stdout, f"timeout {attempt}: {result.stderr}"
        _redispatch(sb.status)
    result = _finalize_kill(sb, _TASK_ID, armed=7200, max_task=7200, with_branch=True)

    text = sb.status.read_text(encoding="utf-8")
    assert "FINAL=BLOCKED" in result.stdout, result.stderr
    assert _section(text, "Needs Human") == "yes", text
    assert _section(text, "Blockers") == _timeout_blockers(sb), text
    record = load_block_record(sb.status)
    assert record is not None and record.reason_code == "per_task_timeout", text
    assert record.requeues == 3, text
    assert "requeue budget is spent (3 of 3)" in result.stderr, result.stderr


def test_timeout_after_committed_work_blocks_when_the_carried_record_spent_the_budget(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path, role="WRITER")
    _make_repo(sb, feature_commits=2)
    (sb.root / "kanban.cfg").write_text("[overwatch]\nrequeue_budget = 2\n", encoding="utf-8")
    _carry_spent_requeues(sb.status)  # triage already requeued it twice

    result = _finalize_kill(sb, _TASK_ID, armed=7200, max_task=7200, with_branch=True)

    text = sb.status.read_text(encoding="utf-8")
    assert "FINAL=BLOCKED" in result.stdout, result.stderr
    assert _section(text, "Needs Human") == "yes", text
    record = load_block_record(sb.status)
    assert record is not None and record.requeues == 2, text


def test_timeout_after_committed_work_blocks_when_the_requeue_budget_is_unreadable(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path, role="WRITER")
    _make_repo(sb, feature_commits=2)
    (sb.root / "kanban.cfg").write_text("[overwatch]\nrequeue_budget = many\n", encoding="utf-8")

    result = _finalize_kill(sb, _TASK_ID, armed=7200, max_task=7200, with_branch=True)

    text = sb.status.read_text(encoding="utf-8")
    assert "FINAL=BLOCKED" in result.stdout, result.stderr
    assert _section(text, "Blockers") == _timeout_blockers(sb), text
    assert "requeue_budget could not be read" in result.stderr, result.stderr


def test_unreadable_requeue_budget_blocks_with_one_inbox_entry_naming_the_read_error(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path, role="WRITER")
    _make_repo(sb, feature_commits=2)
    (sb.root / "kanban.cfg").write_text("[overwatch]\nrequeue_budget = many\n", encoding="utf-8")

    first = _finalize_kill(sb, _TASK_ID, armed=7200, max_task=7200, with_branch=True)
    # Returned to the queue and dispatched again, it times out on the same error.
    text = sb.status.read_text(encoding="utf-8")
    sb.status.write_text(text.replace("## State\nBLOCKED", "## State\nWORKING"), encoding="utf-8")
    second = _finalize_kill(sb, _TASK_ID, armed=7200, max_task=7200, with_branch=True)

    assert "FINAL=BLOCKED" in first.stdout and "FINAL=BLOCKED" in second.stdout, first.stderr + second.stderr
    entries = inbox.list_open(OpsContext(kanban_root=sb.root), "demo")
    assert len(entries) == 1, entries
    assert entries[0].source == _TASK_ID
    assert "requeue_budget" in entries[0].question and "'many'" in entries[0].question, entries[0].question
    assert f"operator inbox entry {entries[0].entry_id} names the requeue_budget read error" in first.stderr
    assert sb.status.read_text(encoding="utf-8").count("## State\nBLOCKED") == 1, "the second timeout did not block"


def test_two_timeouts_requeued_onto_their_branches_do_not_hold_dispatch(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path, role="WRITER")
    _make_repo(sb, feature_commits=1)
    _add_task(sb, _SECOND_TASK)

    _finalize_kill(sb, _TASK_ID, armed=7200, max_task=7200, with_branch=True)
    _finalize_kill(sb, _SECOND_TASK, armed=7200, max_task=7200, with_branch=True)

    assert _storm_verdict(sb, (_TASK_ID, _SECOND_TASK)) == "none"
