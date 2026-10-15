"""
test_wake_survives_unreadable_task_state.py
============================================
A task whose status.md has no readable ``## State`` never exits the wake.

The wake runs under ``set -euo pipefail``. Three paths used to end the whole
wake process (and every project after it in the list) on such a task:

* the prep stage (``wd_prepare_task``, team/scripts/lib/wake_driver_prep.sh)
  re-enables errexit and then reads ``## State``; a status.md with no heading
  made that read fail and the wake exit;
* the finalize stage (``wd_finalize_task``,
  team/scripts/lib/wake_driver_finalize.sh) fell back to BLOCKED when the read
  failed but wrote only the ``[B]`` marker, leaving ``## State`` absent -- the
  very shape that kills the next wake's prep stage;
* the WAITING and BLOCKED re-check scans (team/scripts/lib/wake_task_state.sh)
  assigned the queue scan's output bare, so a failing scan exited the wake
  before dispatch.

Every other block site in the prep stage (an invalid Role or Assigned Agent,
an invalid reasoning-effort configuration) and the finalize stage's
per-task-timeout block write ``## State`` the same way on such a file: the
heading is inserted reading BLOCKED, the Block Record names the site's own
reason code, and the ``[B]`` marker never stands alone.

Each case drives the real stage code in a sandbox root under pytest's
tmp_path (rooted under $PGAI_AGENT_KANBAN_TEMP_DIR by the gated runner): a
real queue file, real task folders, the real status writers and Block Record
module. Only collaborators that would reach outside the sandbox (worktree
creation, the provider, the repo lock, telemetry) are stubbed. No live task
folder or kanban install is read or written.

Naming: function names describe behaviour under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import textwrap

import pytest

from pgai_agent_kanban.overwatch.block_record import load_block_record
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

# This file lives at team/tests/unit/; team/ is three parents up.
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_LIB = _TEAM_DIR / "scripts" / "lib"

_BAD_TASK = "CODER-20260101-001-unreadable-state"
_GOOD_TASK = "CODER-20260101-002-eligible-task"

# A status.md that lost its ## State heading; every other section is intact.
_NO_STATE_STATUS = textwrap.dedent("""\
    # Status

    ## Task
    {task_id}

    ## Role
    CODER

    ## Model
    example-model

    ## Summary
    In progress.

    ## Artifacts
    none

    ## Blockers
    none

    ## Needs Human
    no
    """)

_BACKLOG_STATUS = textwrap.dedent("""\
    # Status

    ## Task
    {task_id}

    ## State
    BACKLOG

    ## Summary
    TBD

    ## Blockers
    none

    ## Needs Human
    no
    """)


def _section(text: str, heading: str) -> str | None:
    """Body of ``## <heading>`` stripped, or None when the heading is absent."""
    m = re.search(rf"^##\s+{re.escape(heading)}[ \t]*\n((?:(?!##).*\n?)*)", text, re.M)
    return m.group(1).strip() if m else None


def _make_task(root: pathlib.Path, task_id: str, status_text: str) -> pathlib.Path:
    task_dir = root / "projects" / "demo" / "tasks" / task_id
    (task_dir / "logs").mkdir(parents=True)
    (task_dir / "artifacts").mkdir()
    (task_dir / "README.md").write_text(
        f"# Task\n\n## Task ID\n{task_id}\n\n## Role\nCODER\n\n## Prerequisites\nnone\n",
        encoding="utf-8",
    )
    status = task_dir / "status.md"
    status.write_text(status_text.format(task_id=task_id), encoding="utf-8")
    return status


def _sandbox(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "kanban"
    for sub in ("tmp", "home", "logs", "projects/demo/tasks/queues"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def _queue_path(root: pathlib.Path) -> pathlib.Path:
    return root / "projects" / "demo" / "tasks" / "queues" / "coder_backlog.md"


def _run(root: pathlib.Path, body: str) -> subprocess.CompletedProcess:
    """Run *body* after a prelude sourcing the real wake_task_state.sh."""
    script = textwrap.dedent(f"""\
        set -euo pipefail
        cd '{_TEAM_DIR}'
        export LOG_FILE='{root / "logs" / "wake.log"}'
        log() {{ echo "[test] wake: $*" | tee -a "$LOG_FILE" >&2; }}
        BACKLOG='{_queue_path(root)}'
        TASKS_ROOT='{root / "projects" / "demo" / "tasks"}'
        KANBAN_ROOT='{root}'
        TEAM_ROOT='{root}'
        PGAI_PROJECT_ROOT='{root / "projects" / "demo"}'
        get_release_state_field() {{ echo none; }}
        transient_retry_field() {{ printf '%s\\n' "${{3:-}}"; }}
        transient_retry_limit() {{ echo 3; }}
        source '{_LIB}/pp_run_ops.sh'
        source '{_LIB}/error_contract.sh'
        source '{_LIB}/block_record.sh'
        source '{_LIB}/wake_task_state.sh'
        """) + body
    env = build_scrubbed_env(
        home_dir=root / "home",
        extra_env={"PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"), "PGAI_AGENT_KANBAN_ROOT_PATH": str(root)},
    )
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=120, env=env)


def _explain(proc: subprocess.CompletedProcess) -> str:
    return f"rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"


def _extract(name: str, source: pathlib.Path) -> str:
    """Bash that defines the top-level function *name* from *source* (brace-matched)."""
    return textwrap.dedent(f"""\
        eval "$(awk '
            /^{name}\\(\\)/ {{ found=1; depth=0 }}
            found {{
                print
                n = split($0, a, "")
                for (i = 1; i <= n; i++) {{
                    if (a[i] == "{{") depth++
                    if (a[i] == "}}") {{ depth--; if (depth == 0) {{ found=0 }} }}
                }}
            }}
        ' '{source}')"
        """)


# ---------------------------------------------------------------------------
# Prep: a [B] task with no ## State is blocked and the next task dispatched.
# ---------------------------------------------------------------------------


def _chain_body() -> str:
    """Two iterations of run_project_chain's own process_one_task call shape.

    The real process_one_task, get_first_pending_task, mark_backlog and
    wd_prepare_task run; the worktree stage is stubbed to record which task
    reached it -- the task the chain prepared for dispatch -- and stop there.
    """
    return textwrap.dedent(f"""\
        source '{_LIB}/wake_readme_meta.sh'
        source '{_LIB}/wake_driver_prep.sh'
        """) + _extract("process_one_task", _LIB / "wake_driver.sh") + textwrap.dedent(f"""\
        AGENT=coder; ACTIVE_PROVIDER=claude; _CURRENT_PROJECT=demo
        _resolve_debug_gate() {{ echo false; }}
        setup_project_directories() {{ :; }}
        resolve_role_effort() {{ echo "|none"; }}
        transition_task_state() {{ echo "unexpected transition_task_state $*" >&2; return 1; }}
        wd_setup_task_worktree() {{ echo "$task_id" >> '{{prepared}}'; LAST_TASK_FINAL_STATE=PREPARED; }}
        _chain_blocked_a_task=false
        for iteration in 1 2; do
          set +e
          process_one_task
          _pot_exit=$?
          set -e
          echo "iteration ${{iteration}}: exit ${{_pot_exit}} final ${{LAST_TASK_FINAL_STATE}}"
        done
        echo "chain blocked a task: ${{_chain_blocked_a_task}}"
        echo WAKE_CONTINUED
        """)


def test_prep_blocks_task_without_state_heading_and_prepares_next_task(tmp_path: pathlib.Path) -> None:
    root = _sandbox(tmp_path)
    bad = _make_task(root, _BAD_TASK, _NO_STATE_STATUS)
    _make_task(root, _GOOD_TASK, _BACKLOG_STATUS)
    _queue_path(root).write_text(f"- [B] {_BAD_TASK} — lost its state\n- [ ] {_GOOD_TASK} — eligible\n",
                                 encoding="utf-8")
    prepared = root / "prepared.log"

    proc = _run(root, _chain_body().replace("{prepared}", str(prepared)))

    assert proc.returncode == 0 and "WAKE_CONTINUED" in proc.stdout, f"the wake exited\n{_explain(proc)}"
    text = bad.read_text(encoding="utf-8")
    assert _section(text, "State") == "BLOCKED", f"## State not written BLOCKED:\n{text}"
    record = load_block_record(bad)
    assert record is not None, f"no ## Block Record:\n{text}"
    assert (record.reason_code, record.owner_role) == ("invalid_state", "CODER")
    assert _section(text, "Needs Human") == "yes"
    assert "no ## State heading" in (_section(text, "Blockers") or ""), text
    # The inserted heading sits with the identity sections, not at the end.
    assert text.index("## State\n") < text.index("## Summary"), text
    queue = _queue_path(root).read_text(encoding="utf-8")
    assert f"- [B] {_BAD_TASK}" in queue, f"marker must agree with ## State BLOCKED:\n{queue}"
    assert prepared.read_text(encoding="utf-8").split() == [_GOOD_TASK], _explain(proc)
    assert "chain blocked a task: true" in proc.stdout, _explain(proc)


def test_prep_blocks_task_with_empty_state_body(tmp_path: pathlib.Path) -> None:
    root = _sandbox(tmp_path)
    bad = _make_task(root, _BAD_TASK, _BACKLOG_STATUS.replace("## State\nBACKLOG\n", "## State\n\n"))
    _queue_path(root).write_text(f"- [ ] {_BAD_TASK}\n", encoding="utf-8")
    prepared = root / "prepared.log"

    proc = _run(root, _chain_body().replace("{prepared}", str(prepared)))

    assert proc.returncode == 0 and "WAKE_CONTINUED" in proc.stdout, _explain(proc)
    text = bad.read_text(encoding="utf-8")
    assert _section(text, "State") == "BLOCKED", text
    assert len(re.findall(r"^##\s+State\s*$", text, re.M)) == 1, f"the empty heading is filled, not duplicated:\n{text}"
    assert load_block_record(bad).reason_code == "invalid_state"
    assert f"- [B] {_BAD_TASK}" in _queue_path(root).read_text(encoding="utf-8")
    assert not prepared.exists(), "an unreadable task is never prepared for dispatch"


# Each prep block site, on a [B] task whose status.md lost its ## State
# heading: (README edit, resolve_role_effort stub, the site's reason code).
# The effort stub fails for the bad task alone, so the eligible task behind
# it still resolves an effort and reaches the worktree stage.
_PREP_BLOCK_SITES = {
    "bad_role": (("## Role\nCODER", "## Role\nBOGUS"), None, "invalid_assignment"),
    "bad_effort": (
        None,
        f'resolve_role_effort() {{ [[ "$task_id" == "{_BAD_TASK}" ]] && return 1; echo "|none"; }}',
        "invalid_effort_config",
    ),
}


@pytest.mark.parametrize("variant", sorted(_PREP_BLOCK_SITES))
def test_prep_block_site_writes_missing_state_heading_and_prepares_next_task(
    tmp_path: pathlib.Path, variant: str
) -> None:
    readme_edit, effort_stub, reason_code = _PREP_BLOCK_SITES[variant]
    root = _sandbox(tmp_path)
    bad = _make_task(root, _BAD_TASK, _NO_STATE_STATUS)
    _make_task(root, _GOOD_TASK, _BACKLOG_STATUS)
    _queue_path(root).write_text(f"- [B] {_BAD_TASK} — lost its state\n- [ ] {_GOOD_TASK} — eligible\n",
                                 encoding="utf-8")
    if readme_edit is not None:
        readme = bad.parent / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8").replace(*readme_edit), encoding="utf-8")
    prepared = root / "prepared.log"
    body = _chain_body().replace("{prepared}", str(prepared))
    if effort_stub is not None:
        body = body.replace('resolve_role_effort() { echo "|none"; }', effort_stub)

    proc = _run(root, body)

    assert proc.returncode == 0 and "WAKE_CONTINUED" in proc.stdout, f"the wake exited\n{_explain(proc)}"
    text = bad.read_text(encoding="utf-8")
    assert _section(text, "State") == "BLOCKED", f"## State not written BLOCKED:\n{text}"
    assert len(re.findall(r"^##\s+State\s*$", text, re.M)) == 1, text
    record = load_block_record(bad)
    assert record is not None and record.reason_code == reason_code, f"Block Record:\n{text}"
    assert _section(text, "Needs Human") == "yes", text
    queue = _queue_path(root).read_text(encoding="utf-8")
    assert f"- [B] {_BAD_TASK}" in queue, f"marker must agree with ## State BLOCKED:\n{queue}"
    assert prepared.read_text(encoding="utf-8").split() == [_GOOD_TASK], _explain(proc)
    assert "chain blocked a task: true" in proc.stdout, _explain(proc)


# ---------------------------------------------------------------------------
# Finalize: a failed ## State read writes State and marker together.
# ---------------------------------------------------------------------------


def _finalize_body(markers: pathlib.Path) -> str:
    return textwrap.dedent(f"""\
        source '{_LIB}/wake_driver_finalize.sh'
        mark_backlog() {{ echo "$1 [$2]" >> '{markers}'; }}
        narrate_model_mismatch() {{ :; }}
        metrics_rollup_refresh_for_task() {{ :; }}
        provider_dispatch_refused() {{ return 1; }}
        pgai_lock_reverify_or_narrate() {{ return 0; }}
        wake_bracket_post_session() {{ :; }}
        teardown_task_worktree() {{ :; }}
        task_finalize_record_write() {{ :; }}
        zero_progress_reset() {{ :; }}
        wake_usage_limit_requeue_reset() {{ :; }}
        _resolve_training_gate() {{ echo false; }}
        handle_working_after_exit() {{ echo "unexpected handle_working_after_exit" >&2; echo WORKING; }}
        AGENT=coder; SUBAGENT=coder; role=CODER; _CURRENT_PROJECT=demo
        task_id='{_BAD_TASK}'; task_dir="$TASKS_ROOT/$task_id"; task_status="$task_dir/status.md"
        task_readme="$task_dir/README.md"; task_artifact_dir="$task_dir/artifacts"
        _task_killed=false; _provider_pgid=""; agent_exit=0; _debug_gate=false
        _pollution_snapshot_ok=false; _litter_snapshot_file=""; _project_lock_file=""
        _task_worktree_path=""; _task_feature_branch=""; _task_source_branch=""
        _canonical_dev_tree_path=""; _park_resting_branch=""; _chain_blocked_a_task=false
        LAST_TASK_FINAL_STATE=""
        wd_finalize_task
        echo "final ${{LAST_TASK_FINAL_STATE}} chain blocked a task: ${{_chain_blocked_a_task}}"
        echo WAKE_CONTINUED
        """)


def test_finalize_writes_state_with_marker_when_state_heading_missing(tmp_path: pathlib.Path) -> None:
    root = _sandbox(tmp_path)
    status = _make_task(root, _BAD_TASK, _NO_STATE_STATUS)
    markers = root / "markers.log"

    proc = _run(root, _finalize_body(markers))

    assert proc.returncode == 0 and "WAKE_CONTINUED" in proc.stdout, _explain(proc)
    assert "final BLOCKED chain blocked a task: true" in proc.stdout, _explain(proc)
    text = status.read_text(encoding="utf-8")
    assert _section(text, "State") == "BLOCKED", f"the [B] marker was written without ## State:\n{text}"
    assert markers.read_text(encoding="utf-8").split("\n")[0] == f"{_BAD_TASK} [B]"
    record = load_block_record(status)
    assert record is not None and record.reason_code == "invalid_state", text
    assert _section(text, "Needs Human") == "yes"


def test_finalize_blocks_unrecognised_state_instead_of_exiting(tmp_path: pathlib.Path) -> None:
    root = _sandbox(tmp_path)
    status = _make_task(root, _BAD_TASK, _BACKLOG_STATUS.replace("BACKLOG", "SIDEWAYS"))
    markers = root / "markers.log"

    proc = _run(root, _finalize_body(markers))

    assert proc.returncode == 0 and "WAKE_CONTINUED" in proc.stdout, _explain(proc)
    text = status.read_text(encoding="utf-8")
    assert _section(text, "State") == "BLOCKED", text
    assert "SIDEWAYS" in (_section(text, "Blockers") or ""), "the fault names the value it read"
    assert markers.read_text(encoding="utf-8").split("\n")[0] == f"{_BAD_TASK} [B]"


def test_finalize_per_task_timeout_writes_missing_state_heading(tmp_path: pathlib.Path) -> None:
    root = _sandbox(tmp_path)
    status = _make_task(root, _BAD_TASK, _NO_STATE_STATUS)
    markers = root / "markers.log"
    body = _finalize_body(markers).replace(
        "_task_killed=false;",
        "_task_killed=true; _provider_timeout_seconds=60; MAX_TASK_SECONDS=60;",
    )

    proc = _run(root, body)

    assert proc.returncode == 0 and "WAKE_CONTINUED" in proc.stdout, _explain(proc)
    assert "final BLOCKED" in proc.stdout, _explain(proc)
    assert "Could not update ## State block" not in proc.stderr, _explain(proc)
    text = status.read_text(encoding="utf-8")
    assert _section(text, "State") == "BLOCKED", text
    record = load_block_record(status)
    assert record is not None and record.reason_code == "per_task_timeout", (
        f"the timeout block, not the unreadable-state fallback, must write the record:\n{text}"
    )
    assert "exceeded max_task_seconds" in (_section(text, "Blockers") or ""), text
    assert markers.read_text(encoding="utf-8").split("\n")[0] == f"{_BAD_TASK} [B]"


# ---------------------------------------------------------------------------
# Re-check scans: a failing queue scan is logged, never fatal.
# ---------------------------------------------------------------------------


def test_recheck_scans_survive_failing_queue_scan(tmp_path: pathlib.Path) -> None:
    root = _sandbox(tmp_path)
    _make_task(root, _GOOD_TASK, _BACKLOG_STATUS)
    _queue_path(root).write_text(f"- [W] {_GOOD_TASK}\n", encoding="utf-8")
    # The queue scan alone fails; every other module call reaches the real one.
    body = textwrap.dedent("""\
        eval "_real_$(declare -f pp_run_ops)"
        pp_run_ops() {
          if [[ "$1" == pgai_agent_kanban.ops && "$2" == scan_queue ]]; then
            echo "scan_queue: cannot read a status.md" >&2
            return 1
          fi
          _real_pp_run_ops "$@"
        }
        recheck_waiting_tasks
        recheck_blocked_tasks
        get_first_pending_task || echo "pending scan returned $?"
        echo WAKE_CONTINUED
        """)

    proc = _run(root, body)

    assert proc.returncode == 0 and "WAKE_CONTINUED" in proc.stdout, f"a failing scan exited the wake\n{_explain(proc)}"
    assert "WAITING scan" in proc.stderr and "failed" in proc.stderr, _explain(proc)
    assert "BLOCKED scan" in proc.stderr, _explain(proc)
    assert "pending-task scan" in proc.stderr, _explain(proc)
    assert "pending scan returned 1" in proc.stdout, _explain(proc)
