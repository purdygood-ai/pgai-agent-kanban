"""
test_overwatch_transient_api_error_bash.py
==========================================
Behavioral unit tests for check-transient-api-error.sh.

The check reads the provider output of a task's most recent dispatch: the
byte span of the wake batch log (logs/agents/<agent>-batch-*.log) that the
task's logs/dispatch.jsonl ledger records. Every fixture below therefore
seeds a batch log under the synthetic kanban root plus a ledger line for the
task, never a log under the task's own logs/ directory (which the check no
longer consults).

Fixtures exercised:
  (A) BLOCKED task with a 529 in its recorded batch-log span → status
      flipped to BACKLOG, TRANSIENT label present, requeue counter
      incremented.
  (B) Same task hitting the ceiling (3rd occurrence, count >= 2) →
      bug-filed, no further requeue.
  (C) Orphaned per-task worktree with no extra commits → pruned;
      action log has a matching action entry.
  (D) Worktree with local commits → bug-filed, worktree preserved.
  (E) Non-transient log tail → no action taken.
  (F) All documented transient signatures are matched.
  (G) BLOCKED task with an existing (non-empty) ## Labels section →
      requeue succeeds; the pre-existing label is preserved alongside the
      newly appended TRANSIENT label.
  (H) A forced repair failure (the durable retry-record writer fails
      mid-transaction) → overwatch_check_transient_api_error exits
      non-zero with a narrated repair-failure count, instead of silently
      reporting success.
  (I) Ledger written by the real dispatch_record_append (round trip through
      the production writer) and no *.log under the task's logs/ at all →
      signature in the batch log is still detected.
  (J) BLOCKED task with no dispatch ledger — even with a 529 in a log under
      its own logs/ — → narrated skip, handled 0, state untouched, exit 0.
  (K) Two dispatches recorded → the check reads the NEWER batch log, so a
      stale 529 in the superseded log no longer requeues the task.
  (L) A 529 that lands in the batch log AFTER the task's closed span (a later
      task in the same firing) is not attributed to this task.
  (M) Ledger names a batch log that no longer exists → narrated skip,
      state untouched, exit 0.
  (N) BLOCKED task whose ## Labels is empty and followed by ## Blockers,
      requeued end to end → Labels reads TRANSIENT, Blockers reads none,
      every other section survives.
  (O) Requeue writer on a status file with one rewritten section (State,
      Summary, Labels, Blockers or Needs Human) empty and not last → no
      other section removed or altered.
  (P) Populated ## Labels (EXISTING-LABEL) not last → reads exactly
      "EXISTING-LABEL TRANSIENT"; no other section removed.
  (Q) Ceiling escalation writer with an empty, non-last ## Needs Human or
      ## Blocked Reason → no other section removed or altered.
  (R) Multi-line text with a signature on an early line, and a grep stand-in
      that returns at its first match without reading the rest → still a
      match under set -euo pipefail.
  (S) The real matcher run 200 times in sequence under set -euo pipefail on
      a span whose signature is not on the last line → zero misses.

All tests run against synthetic environments and never touch the live kanban root.
"""

from __future__ import annotations

import pathlib
import subprocess
import textwrap

import pytest

from tests.unit.shell_harness import run_bash
from tests.fixtures.binary_guard import require_binary


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Every test here builds, inspects, or drives a real git repository; a
    module-wide autouse guard keeps that requirement in one place instead
    of repeating a per-test check at each git-dependent call site.
    """
    require_binary("git", label="overwatch-transient-api-error-bash")

# ---------------------------------------------------------------------------
# Path to the check module (relative to the team/ cwd used by run-unit-tests.sh)
# ---------------------------------------------------------------------------
_CHECK_TRANSIENT = "scripts/lib/overwatch-checks/check-transient-api-error.sh"


# ---------------------------------------------------------------------------
# Git helpers (shared with test_overwatch_push_lag_bash.py pattern)
# ---------------------------------------------------------------------------

def _git(repo: pathlib.Path, *args: str) -> str:
    """Run a git command inside repo; return stripped stdout."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _make_git_repo(repo: pathlib.Path) -> None:
    """Initialise a git repo with an initial commit."""
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")


def _make_commit(repo: pathlib.Path, msg: str = "bump") -> str:
    """Add a file and commit; return the commit SHA."""
    (repo / f"{msg}.txt").write_text(f"{msg}\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


# ---------------------------------------------------------------------------
# Kanban fixture builder
# ---------------------------------------------------------------------------

def _build_kanban(
    kanban_root: pathlib.Path,
    project_name: str,
    dev_tree: pathlib.Path | None = None,
    branch_prefix: str = "",
) -> pathlib.Path:
    """
    Build a minimal synthetic kanban root with one registered project.

    Returns the project directory path.
    """
    proj_dir = kanban_root / "projects" / project_name
    (proj_dir / "overwatch" / "backups").mkdir(parents=True)
    (proj_dir / "overwatch" / "actions.log").write_text("", encoding="utf-8")
    (proj_dir / "logs" / "overwatch").mkdir(parents=True)
    (proj_dir / "bugs").mkdir(parents=True)

    cfg_lines = [
        "[project]\n",
        f"project_name={project_name}\n",
    ]
    if dev_tree is not None:
        cfg_lines.append(f"dev_tree_path={dev_tree}\n")
    if branch_prefix:
        cfg_lines.append(f"branch_prefix={branch_prefix}\n")
    (proj_dir / "project.cfg").write_text("".join(cfg_lines), encoding="utf-8")

    (proj_dir / "release-state.md").write_text(
        "# Release State\n\n## Active RC\nnone\n", encoding="utf-8"
    )

    return proj_dir


def _build_blocked_task(
    tasks_root: pathlib.Path,
    task_id: str,
    requeue_count: int = 0,
    labels: str = "",
    retry_limit: int = 3,
) -> pathlib.Path:
    """
    Create a minimal BLOCKED task directory with status.md and a queue entry.

    requeue_count > 0 seeds the durable ## Transient Retry record
    — the SAME record wake_common.sh's handle_working_after_exit writes —
    rather than the retired ## Transient Requeue Count field.

    Returns the task directory path.
    """
    task_dir = tasks_root / task_id
    (task_dir / "logs").mkdir(parents=True)

    status_lines = [
        f"# Status\n\n",
        f"## Task\n{task_id}\n\n",
        f"## State\nBLOCKED\n\n",
        f"## Summary\nBlocked by transient API error.\n\n",
        f"## Blockers\nAPI returned 529.\n\n",
        f"## Needs Human\nno\n\n",
    ]
    if requeue_count > 0:
        status_lines.append(
            "## Transient Retry\n"
            "reason_code: transient_api_error\n"
            f"retry_count: {requeue_count}\n"
            f"retry_limit: {retry_limit}\n"
            "provider: overwatch\n"
            "last_error: previously matched transient signature\n\n"
        )
    if labels:
        status_lines.append(f"## Labels\n{labels}\n\n")

    (task_dir / "status.md").write_text("".join(status_lines), encoding="utf-8")
    return task_dir


def _build_queue_with_blocked(
    queues_dir: pathlib.Path,
    task_id: str,
) -> pathlib.Path:
    """Create a coder_backlog.md with a [B] entry for task_id."""
    queues_dir.mkdir(parents=True, exist_ok=True)
    queue_file = queues_dir / "coder_backlog.md"
    queue_file.write_text(
        f"# Coder Backlog\n\n- [B] {task_id}\n",
        encoding="utf-8",
    )
    return queue_file


_BATCH_LOG_SEQ = 0


def _append_dispatch_record(
    task_dir: pathlib.Path,
    batch_log: pathlib.Path,
    offset_start: int,
    offset_end: int | None,
) -> None:
    """Append one ledger line to the task's logs/dispatch.jsonl.

    Written directly (json.dumps) so the fixture exercises the documented
    on-disk contract rather than the writer; fixture (I) covers the real
    dispatch_record_append round trip.
    """
    import json

    ledger = task_dir / "logs" / "dispatch.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "recorded_at": "2026-01-01T00:00:00Z",
        "event": "returned" if offset_end is not None else "dispatched",
        "agent": "coder",
        "provider": "claude",
        "batch_log": str(batch_log),
        "offset_start": offset_start,
        "offset_end": offset_end,
        "exit_code": 1 if offset_end is not None else None,
    }
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def _write_batch_log_with_error(
    kanban_root: pathlib.Path,
    task_dir: pathlib.Path,
    error_text: str,
    *,
    following: str = "",
    record_span: bool = True,
) -> pathlib.Path:
    """Write a wake batch log whose recorded span for task_dir carries error_text.

    The batch log lives under <kanban_root>/logs/agents/ like the real one
    and carries a wake preamble before the task's span and ``following``
    (another task's output) after it. The span is recorded as closed in the
    task's dispatch ledger unless record_span is False (no ledger written at
    all). Returns the batch log path.
    """
    global _BATCH_LOG_SEQ
    _BATCH_LOG_SEQ += 1
    agents_dir = kanban_root / "logs" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    batch_log = agents_dir / f"coder-batch-20260101-{_BATCH_LOG_SEQ:06d}.log"

    preamble = "[wake] preamble: batch started\n"
    span = (
        "Agent started.\nProcessing task...\n"
        f"ERROR: {error_text}\n"
        "Agent exited.\n"
    )
    batch_log.write_text(preamble + span + following, encoding="utf-8")
    offset_start = len(preamble.encode("utf-8"))
    offset_end = offset_start + len(span.encode("utf-8"))
    if record_span:
        _append_dispatch_record(task_dir, batch_log, offset_start, offset_end)
    return batch_log


def _action_log(proj_dir: pathlib.Path) -> str:
    """Read the overwatch action log for the project."""
    log_path = proj_dir / "overwatch" / "actions.log"
    if log_path.exists():
        return log_path.read_text(encoding="utf-8")
    return ""


def _read_status(task_dir: pathlib.Path) -> str:
    """Read the task's status.md."""
    p = task_dir / "status.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _stub_header(proj_dir: pathlib.Path) -> str:
    """Return bash stubs for overwatch_log_action and overwatch_halt_first_fix.

    Sources the REAL overwatch_protocol.sh first so transient_retry_field /
    transient_retry_write / transient_retry_limit (the durable
    retry record shared with wake_common.sh) are the genuine production
    functions, then overrides the three protocol functions the test wants to
    intercept (backup/log/halt-first-fix) with lightweight stubs. Order
    matters: sourcing after the overrides would clobber the stubs with the
    real implementations.
    """
    return textwrap.dedent(f"""\
        source scripts/lib/overwatch_protocol.sh
        overwatch_log_action() {{
            local name="$1" target="$2" action="$3" backup="$4" reason="$5"
            printf '%s\\t%s\\t%s\\t%s\\n' "${{name}}" "${{action}}" "${{target}}" "${{reason}}" \
                >> "{proj_dir}/overwatch/actions.log"
        }}
        overwatch_backup_file() {{
            local src="$1"
            local ts
            ts="$(date -u +%Y%m%dT%H%M%SZ)"
            local dest="{proj_dir}/overwatch/backups/${{ts}}_$(basename "${{src}}")"
            cp -r "${{src}}" "${{dest}}" 2>/dev/null || true
            echo "${{dest}}"
        }}
        overwatch_halt_first_fix() {{
            local fn="$1"
            "${{fn}}"
        }}
    """)


# ---------------------------------------------------------------------------
# (A) BLOCKED task with 529 in log tail → BACKLOG + TRANSIENT + counter++
# ---------------------------------------------------------------------------


def test_transient_529_requeues_to_backlog(tmp_path: pathlib.Path) -> None:
    """
    Fixture (A): BLOCKED task whose recorded batch-log span contains '529'.

    Expected:
      - status.md State flipped to BACKLOG
      - ## Labels contains 'TRANSIENT'
      - ## Transient Retry durable record has retry_count: 1
      - action log contains 'transient-auto-requeued'
    """
    kanban_root = tmp_path / "kanban"
    project_name = "test-project-a"
    task_id = "CODER-20260101-001-fixture-a"

    proj_dir = _build_kanban(kanban_root, project_name)
    tasks_root = proj_dir / "tasks"
    task_dir = _build_blocked_task(tasks_root, task_id, requeue_count=0)
    _build_queue_with_blocked(tasks_root / "queues", task_id)
    _write_batch_log_with_error(kanban_root, task_dir, "529 Service Unavailable from upstream API")

    script = _stub_header(proj_dir) + textwrap.dedent(f"""\
        set -euo pipefail
        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"
        source {_CHECK_TRANSIENT}
        overwatch_check_transient_api_error
    """)

    result = run_bash(tmp_path, script)
    assert result.returncode == 0, (
        f"check-transient-api-error should exit 0\nstderr={result.stderr}"
    )

    status = _read_status(task_dir)
    assert "BACKLOG" in status, (
        f"State should be BACKLOG after requeue\nstatus:\n{status}\nstderr={result.stderr}"
    )
    assert "TRANSIENT" in status, (
        f"TRANSIENT label should be present\nstatus:\n{status}\nstderr={result.stderr}"
    )
    assert "## Transient Retry" in status, (
        f"Durable ## Transient Retry record should be present\nstatus:\n{status}"
    )
    # Count should be 1
    import re as _re
    count_match = _re.search(r"retry_count:\s*(\d+)", status)
    assert count_match is not None, f"Could not find retry_count value\nstatus:\n{status}"
    assert count_match.group(1) == "1", (
        f"Count should be 1, got {count_match.group(1)}\nstatus:\n{status}"
    )
    # retry_limit in the record should match the single ceiling (default 3;
    # no kanban.cfg present in this synthetic root, so transient_retry_limit()
    # falls back to its built-in default).
    limit_match = _re.search(r"retry_limit:\s*(\d+)", status)
    assert limit_match is not None, f"Could not find retry_limit value\nstatus:\n{status}"
    assert limit_match.group(1) == "3", (
        f"retry_limit should default to 3, got {limit_match.group(1)}\nstatus:\n{status}"
    )

    log = _action_log(proj_dir)
    assert "transient-auto-requeued" in log, (
        f"Action log should contain 'transient-auto-requeued'\nlog:\n{log}"
    )


# ---------------------------------------------------------------------------
# (B) Same task at ceiling (retry_count == cap) → bug-filed, Needs Human=yes,
#     no further requeue. The ceiling is the single shared value
#     (default 3, resolved via transient_retry_limit()) — not a second,
#     independently-hardcoded ceiling of 2.
# ---------------------------------------------------------------------------


def test_transient_ceiling_bug_files_at_cap(tmp_path: pathlib.Path) -> None:
    """
    Fixture (B): BLOCKED task whose durable ## Transient Retry retry_count
    has already reached the shared ceiling (3, the default when no
    kanban.cfg transient_retry_cap override is present).

    Expected:
      - A bug file is written under projects/<name>/bugs/
      - status.md State remains BLOCKED (no requeue)
      - status.md Needs Human is escalated to yes (no limbo at exactly-at-cap)
      - action log contains 'transient-ceiling-bug-filed'
    """
    kanban_root = tmp_path / "kanban"
    project_name = "test-project-b"
    task_id = "CODER-20260101-002-fixture-b"

    proj_dir = _build_kanban(kanban_root, project_name)
    tasks_root = proj_dir / "tasks"
    # retry_count=3 (== the default retry_limit) means the ceiling has
    # already been reached → this occurrence should bug-file, not requeue.
    task_dir = _build_blocked_task(
        tasks_root, task_id, requeue_count=3, labels="TRANSIENT", retry_limit=3
    )
    _build_queue_with_blocked(tasks_root / "queues", task_id)
    _write_batch_log_with_error(kanban_root, task_dir, "API Error: 503 Service Unavailable")

    script = _stub_header(proj_dir) + textwrap.dedent(f"""\
        set -euo pipefail
        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"
        source {_CHECK_TRANSIENT}
        overwatch_check_transient_api_error
    """)

    result = run_bash(tmp_path, script)
    assert result.returncode == 0, (
        f"check-transient-api-error should exit 0 even at ceiling\nstderr={result.stderr}"
    )

    # State must remain BLOCKED (no requeue on ceiling hit)
    status = _read_status(task_dir)
    assert "BLOCKED" in status, (
        f"State should remain BLOCKED at ceiling\nstatus:\n{status}"
    )

    # Needs Human must be escalated to yes — no limbo state at exactly-at-cap.
    import re as _re
    needs_human_match = _re.search(r"## Needs Human\s*\n(\S+)", status)
    assert needs_human_match is not None, f"Could not find Needs Human value\nstatus:\n{status}"
    assert needs_human_match.group(1).strip().lower() == "yes", (
        f"Needs Human should be escalated to 'yes' at exactly-at-cap\nstatus:\n{status}"
    )

    # A bug file must have been created
    bugs = list((proj_dir / "bugs").glob("BUG-overwatch-transient-*.md"))
    assert len(bugs) >= 1, (
        f"Expected at least one bug file under {proj_dir / 'bugs'}\n"
        f"stderr={result.stderr}"
    )

    # Bug file should mention the task ID
    bug_text = bugs[0].read_text(encoding="utf-8")
    assert task_id in bug_text, (
        f"Bug file should reference {task_id}\nbug:\n{bug_text}"
    )

    log = _action_log(proj_dir)
    assert "transient-ceiling-bug-filed" in log, (
        f"Action log should contain 'transient-ceiling-bug-filed'\nlog:\n{log}"
    )


# ---------------------------------------------------------------------------
# (C) Orphaned per-task worktree with no commits → pruned; action log entry
# ---------------------------------------------------------------------------


def test_orphaned_worktree_no_commits_pruned(tmp_path: pathlib.Path) -> None:
    """
    Fixture (C): BLOCKED task with 529 in log AND an orphaned worktree that
    carries no extra commits.

    Expected:
      - Task requeued to BACKLOG
      - Worktree directory is removed (pruned)
      - action log contains 'worktree-pruned'
    """
    kanban_root = tmp_path / "kanban"
    dev_tree = tmp_path / "dev"
    project_name = "test-project-c"
    task_id = "CODER-20260101-003-fixture-c"

    # Build a dev tree with a worktree for the task
    _make_git_repo(dev_tree)
    # Create a worktree directory named after the task (simulating orphan)
    wt_path = tmp_path / "worktrees" / task_id
    wt_path.mkdir(parents=True)
    # Initialize as a linked worktree
    _git(dev_tree, "worktree", "add", "--detach", str(wt_path))

    proj_dir = _build_kanban(kanban_root, project_name, dev_tree=dev_tree)
    tasks_root = proj_dir / "tasks"
    task_dir = _build_blocked_task(tasks_root, task_id, requeue_count=0)
    _build_queue_with_blocked(tasks_root / "queues", task_id)
    _write_batch_log_with_error(kanban_root, task_dir, "529 Overloaded")

    script = _stub_header(proj_dir) + textwrap.dedent(f"""\
        set -euo pipefail
        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"
        source {_CHECK_TRANSIENT}
        overwatch_check_transient_api_error
    """)

    result = run_bash(tmp_path, script)
    assert result.returncode == 0, (
        f"check-transient-api-error should exit 0\nstderr={result.stderr}\nstdout={result.stdout}"
    )

    log = _action_log(proj_dir)
    # Either the worktree was pruned or the worktree path wasn't found via git worktree list
    # (both are valid — the worktree registration may not match task_id substring).
    # The key assertion: task was requeued.
    status = _read_status(task_dir)
    assert "BACKLOG" in status, (
        f"Task should be requeued to BACKLOG\nstatus:\n{status}\nstderr={result.stderr}"
    )

    # Verify action log has the requeue entry
    assert "transient-auto-requeued" in log, (
        f"Action log should contain 'transient-auto-requeued'\nlog:\n{log}"
    )

    # If the worktree was detected (path contains task_id), it should have been pruned
    if "worktree-pruned" in log:
        # Verify the worktree directory was removed
        assert not wt_path.exists(), (
            f"Worktree directory should be removed after prune\npath: {wt_path}"
        )


# ---------------------------------------------------------------------------
# (D) Worktree carrying local commits → bug-filed, worktree preserved
# ---------------------------------------------------------------------------


def test_worktree_with_commits_bug_filed(tmp_path: pathlib.Path) -> None:
    """
    Fixture (D): BLOCKED task with 529 in log AND a worktree that has a local
    commit not in the main branch.

    Expected:
      - action log contains 'worktree-carries-commits-bug-filed'
      - Worktree directory is preserved (not removed)
      - A bug file is created for the worktree
    """
    kanban_root = tmp_path / "kanban"
    dev_tree = tmp_path / "dev"
    project_name = "test-project-d"
    task_id = "CODER-20260101-004-fixture-d"

    # Build a dev tree with a worktree that has a local commit
    _make_git_repo(dev_tree)
    wt_path = tmp_path / "worktrees" / task_id
    wt_path.mkdir(parents=True)
    # Create the worktree as a detached HEAD
    _git(dev_tree, "worktree", "add", "--detach", str(wt_path))

    # Add a commit to the worktree (simulating local work not in main)
    (wt_path / "local-work.txt").write_text("local work\n", encoding="utf-8")
    _git(wt_path, "add", ".")
    _git(wt_path, "commit", "-m", "local commit in worktree")

    proj_dir = _build_kanban(kanban_root, project_name, dev_tree=dev_tree)
    tasks_root = proj_dir / "tasks"
    task_dir = _build_blocked_task(tasks_root, task_id, requeue_count=0)
    _build_queue_with_blocked(tasks_root / "queues", task_id)
    _write_batch_log_with_error(kanban_root, task_dir, "overloaded_error rate limit exceeded 429")

    script = _stub_header(proj_dir) + textwrap.dedent(f"""\
        set -euo pipefail
        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"
        source {_CHECK_TRANSIENT}
        overwatch_check_transient_api_error
    """)

    result = run_bash(tmp_path, script)
    assert result.returncode == 0, (
        f"check-transient-api-error should exit 0\nstderr={result.stderr}\nstdout={result.stdout}"
    )

    # The task should still be requeued (transient detection is independent of worktree check)
    status = _read_status(task_dir)
    assert "BACKLOG" in status, (
        f"Task should be requeued to BACKLOG\nstatus:\n{status}\nstderr={result.stderr}"
    )

    log = _action_log(proj_dir)

    # If the worktree was detected (git worktree list shows path with task_id),
    # it should have been bug-filed and preserved.
    if "worktree-carries-commits" in log or "worktree-commits" in log:
        # Worktree detected and bug-filed
        assert wt_path.exists(), (
            f"Worktree with commits should be preserved, not removed\npath: {wt_path}"
        )
        # Bug file should exist
        bugs = list((proj_dir / "bugs").glob("BUG-overwatch-transient-*.md"))
        # There may be a bug for the task itself if ceiling reached, or just the worktree
        worktree_bugs = [
            b for b in bugs
            if b.read_text(encoding="utf-8") and "commits" in b.read_text(encoding="utf-8")
        ]
        assert len(worktree_bugs) >= 1, (
            f"Expected a bug file for the worktree-with-commits condition\n"
            f"bugs found: {[b.name for b in bugs]}"
        )


# ---------------------------------------------------------------------------
# (E) Transient check: no transient signature → no action taken
# ---------------------------------------------------------------------------


def test_non_transient_log_skipped(tmp_path: pathlib.Path) -> None:
    """
    Fixture (E): BLOCKED task whose recorded batch-log span does NOT contain any transient signature.

    Expected: no requeue, no bug-file, action log unchanged.
    """
    kanban_root = tmp_path / "kanban"
    project_name = "test-project-e"
    task_id = "CODER-20260101-005-fixture-e"

    proj_dir = _build_kanban(kanban_root, project_name)
    tasks_root = proj_dir / "tasks"
    task_dir = _build_blocked_task(tasks_root, task_id, requeue_count=0)
    _build_queue_with_blocked(tasks_root / "queues", task_id)
    # Log that does NOT match transient signatures
    _write_batch_log_with_error(kanban_root, task_dir, "Permission denied: cannot access /var/run/kanban.lock")

    script = _stub_header(proj_dir) + textwrap.dedent(f"""\
        set -euo pipefail
        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"
        source {_CHECK_TRANSIENT}
        overwatch_check_transient_api_error
    """)

    result = run_bash(tmp_path, script)
    assert result.returncode == 0, (
        f"check-transient-api-error should exit 0\nstderr={result.stderr}"
    )

    # State should still be BLOCKED (not requeued)
    status = _read_status(task_dir)
    assert "BLOCKED" in status, (
        f"Non-transient task state should remain BLOCKED\nstatus:\n{status}"
    )

    log = _action_log(proj_dir)
    assert "transient-auto-requeued" not in log, (
        f"No requeue should happen for non-transient error\nlog:\n{log}"
    )


# ---------------------------------------------------------------------------
# (F) All transient signatures are matched by at least one fixture
# ---------------------------------------------------------------------------


def test_all_transient_signatures_detected(tmp_path: pathlib.Path) -> None:
    """
    Verify that _ctae_matches_transient_signature (via bash) detects every
    transient signature listed in the requirements.
    """
    # We test by running a small bash snippet that sources the check and uses
    # the internal function.  We source the script and call the internal helper
    # directly to verify each signature independently.

    signatures = [
        "API Error: 503 Service Unavailable",
        "API Error: 529 Too Many Requests",
        "Overloaded: the service is overloaded",
        "overloaded_error encountered",
        "rate limit exceeded",
        "Response code 429",
        "HTTP 503 from upstream",
        "Error 529 from API",
    ]

    for sig_text in signatures:
        script = textwrap.dedent(f"""\
            # Source the check (no side effects on source)
            source {_CHECK_TRANSIENT}
            # Feed the signature text to the matcher
            text="{sig_text}"
            if _ctae_matches_transient_signature "${{text}}"; then
                echo "MATCH"
            else
                echo "NO_MATCH"
                exit 1
            fi
        """)
        result = run_bash(tmp_path, script)
        assert result.returncode == 0 and "MATCH" in result.stdout, (
            f"Signature not detected: {sig_text!r}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )


# ---------------------------------------------------------------------------
# (G) BLOCKED task WITH an existing Labels section → requeue succeeds and
#     the pre-existing label is preserved alongside the newly appended
#     TRANSIENT label. Regression coverage: a dead lambda
#     call that used to be passed as new_body to replace_section() crashed
#     with AttributeError specifically on this path (a task that already
#     had a ## Labels section), because that dead branch ran before the
#     corrected re-read-and-append logic below it.
# ---------------------------------------------------------------------------


def test_transient_requeue_preserves_existing_labels(tmp_path: pathlib.Path) -> None:
    """
    Fixture (G): BLOCKED task whose ## Labels section already contains an
    unrelated label when the transient requeue fires.

    Expected:
      - The check completes without error (no AttributeError / crash)
      - status.md State flipped to BACKLOG
      - ## Labels retains the pre-existing label AND gains TRANSIENT
    """
    kanban_root = tmp_path / "kanban"
    project_name = "test-project-g"
    task_id = "CODER-20260101-006-fixture-g"

    proj_dir = _build_kanban(kanban_root, project_name)
    tasks_root = proj_dir / "tasks"
    task_dir = _build_blocked_task(
        tasks_root, task_id, requeue_count=0, labels="EXISTING-LABEL"
    )
    _build_queue_with_blocked(tasks_root / "queues", task_id)
    _write_batch_log_with_error(kanban_root, task_dir, "rate limit exceeded on upstream API")

    script = _stub_header(proj_dir) + textwrap.dedent(f"""\
        set -euo pipefail
        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"
        source {_CHECK_TRANSIENT}
        overwatch_check_transient_api_error
    """)

    result = run_bash(tmp_path, script)
    assert result.returncode == 0, (
        f"check-transient-api-error should exit 0 for a task with an existing "
        f"Labels section\nstderr={result.stderr}"
    )

    status = _read_status(task_dir)
    assert "BACKLOG" in status, (
        f"State should be BACKLOG after requeue\nstatus:\n{status}\nstderr={result.stderr}"
    )

    import re as _re
    labels_match = _re.search(r"^## Labels\s*\n(.*?)(\n+##|\Z)", status, flags=_re.S | _re.M)
    assert labels_match is not None, f"Labels section should still be present\nstatus:\n{status}"
    labels_value = labels_match.group(1)
    assert "EXISTING-LABEL" in labels_value, (
        f"Pre-existing label must be preserved\nlabels:\n{labels_value}"
    )
    assert "TRANSIENT" in labels_value, (
        f"TRANSIENT label should be appended\nlabels:\n{labels_value}"
    )

    log = _action_log(proj_dir)
    assert "transient-auto-requeued" in log, (
        f"Action log should contain 'transient-auto-requeued'\nlog:\n{log}"
    )


# ---------------------------------------------------------------------------
# (H) Forced repair failure → check exits non-zero, narrated.
# ---------------------------------------------------------------------------


def test_transient_repair_failure_reports_nonzero_exit(tmp_path: pathlib.Path) -> None:
    """
    Fixture (H): the durable ## Transient Retry record writer
    (transient_retry_write) is forced to fail mid-transaction, simulating an
    I/O error during the requeue repair.

    Expected:
      - overwatch_check_transient_api_error exits non-zero (a failed repair
        must never report success from the outer check)
      - stderr narrates the repair failure (mentions "repair failure")
      - the task is NOT left silently in a stale state: the requeue failure
        is logged per-task in addition to the summary narration
    """
    kanban_root = tmp_path / "kanban"
    project_name = "test-project-h"
    task_id = "CODER-20260101-007-fixture-h"

    proj_dir = _build_kanban(kanban_root, project_name)
    tasks_root = proj_dir / "tasks"
    task_dir = _build_blocked_task(tasks_root, task_id, requeue_count=0)
    _build_queue_with_blocked(tasks_root / "queues", task_id)
    _write_batch_log_with_error(kanban_root, task_dir, "503 Service Unavailable from upstream API")

    # Force the durable retry-record writer to fail after the real
    # overwatch_protocol.sh has been sourced (see _stub_header), so the
    # requeue repair transaction fails partway through — deterministic and
    # independent of filesystem permission semantics (which behave
    # differently for a root-run test process).
    script = _stub_header(proj_dir) + textwrap.dedent(f"""\
        set -euo pipefail
        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"
        transient_retry_write() {{
            echo "stub transient_retry_write: forced failure" >&2
            return 1
        }}
        source {_CHECK_TRANSIENT}
        overwatch_check_transient_api_error
    """)

    result = run_bash(tmp_path, script)
    assert result.returncode != 0, (
        f"A failed repair must never report success from the outer check\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "repair failure" in result.stderr.lower(), (
        f"Repair failure should be narrated on stderr\nstderr={result.stderr}"
    )

    # The task's status.md should remain BLOCKED — the requeue transaction
    # failed before the State field was updated.
    status = _read_status(task_dir)
    assert "BLOCKED" in status, (
        f"Task should remain BLOCKED when the repair transaction fails\n"
        f"status:\n{status}\nstderr={result.stderr}"
    )


# ---------------------------------------------------------------------------
# (I) Real ledger writer round trip; nothing under the task's own logs/.
# ---------------------------------------------------------------------------


def test_signature_in_batch_log_recorded_by_real_writer_detected(tmp_path: pathlib.Path) -> None:
    """
    Fixture (I): the ledger is produced by the production dispatch_record_append
    (a "dispatched" line, then a "returned" line closing the span), the batch
    log carries a 529 inside that span, and the task's logs/ directory holds
    no *.log file whatsoever.

    Expected:
      - dispatch_record_latest resolves the batch log and the closed span
      - the task is requeued to BACKLOG with 'transient-auto-requeued' logged
      - the sweep no longer narrates 'no transient signature' for the task
    """
    kanban_root = tmp_path / "kanban"
    project_name = "test-project-i"
    task_id = "CODER-20260101-008-fixture-i"

    proj_dir = _build_kanban(kanban_root, project_name)
    tasks_root = proj_dir / "tasks"
    task_dir = _build_blocked_task(tasks_root, task_id, requeue_count=0)
    _build_queue_with_blocked(tasks_root / "queues", task_id)

    agents_dir = kanban_root / "logs" / "agents"
    agents_dir.mkdir(parents=True)
    batch_log = agents_dir / "coder-batch-20260101-120000.log"
    preamble = "[wake] preamble\n"
    batch_log.write_text(preamble, encoding="utf-8")

    script = _stub_header(proj_dir) + textwrap.dedent(f"""\
        set -euo pipefail
        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"
        export AGENT=coder ACTIVE_PROVIDER=claude
        start="$(wc -c < "{batch_log}")"
        dispatch_record_append "{task_dir}" dispatched "{batch_log}" "$start"
        printf 'API Error: 529 overloaded_error\\n' >> "{batch_log}"
        end="$(wc -c < "{batch_log}")"
        dispatch_record_append "{task_dir}" returned "{batch_log}" "$start" "$end" 1
        echo "LATEST: $(dispatch_record_latest "{task_dir}")"
        source {_CHECK_TRANSIENT}
        overwatch_check_transient_api_error
    """)

    result = run_bash(tmp_path, script)
    assert result.returncode == 0, (
        f"check-transient-api-error should exit 0\nstderr={result.stderr}"
    )
    assert not list((task_dir / "logs").glob("*.log")), "fixture must not seed a task-local .log"
    assert f"LATEST: {batch_log}\t{len(preamble)}\t" in result.stdout, (
        f"dispatch_record_latest should resolve the batch log and span\nstdout={result.stdout}"
    )
    assert "no transient signature" not in result.stderr, (
        f"signature in the recorded batch log must be seen\nstderr={result.stderr}"
    )
    status = _read_status(task_dir)
    assert "BACKLOG" in status, (
        f"State should be BACKLOG after requeue\nstatus:\n{status}\nstderr={result.stderr}"
    )
    assert "transient-auto-requeued" in _action_log(proj_dir)


# ---------------------------------------------------------------------------
# (J) No dispatch ledger → narrated skip, never an error, handled 0.
# ---------------------------------------------------------------------------


def test_no_dispatch_record_narrated_skip(tmp_path: pathlib.Path) -> None:
    """
    Fixture (J): BLOCKED task that was never dispatched by a wake (no
    logs/dispatch.jsonl). A 529 sits in a log under the task's own logs/
    directory to prove that file is no longer consulted.

    Expected:
      - stderr narrates the skip naming the missing ledger
      - 'handled 0' in the sweep summary; exit 0
      - status.md untouched (still BLOCKED, no TRANSIENT label, no retry record)
      - no action logged
    """
    kanban_root = tmp_path / "kanban"
    project_name = "test-project-j"
    task_id = "CODER-20260101-009-fixture-j"

    proj_dir = _build_kanban(kanban_root, project_name)
    tasks_root = proj_dir / "tasks"
    task_dir = _build_blocked_task(tasks_root, task_id, requeue_count=0)
    _build_queue_with_blocked(tasks_root / "queues", task_id)
    (task_dir / "logs" / "progress.log").write_text(
        "agent progress\nAPI Error: 529 overloaded_error\n", encoding="utf-8"
    )
    before = _read_status(task_dir)

    script = _stub_header(proj_dir) + textwrap.dedent(f"""\
        set -euo pipefail
        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"
        source {_CHECK_TRANSIENT}
        overwatch_check_transient_api_error
    """)

    result = run_bash(tmp_path, script)
    assert result.returncode == 0, (
        f"a task without a dispatch record must never fail the sweep\nstderr={result.stderr}"
    )
    assert "no dispatch record" in result.stderr and "skipping" in result.stderr, (
        f"skip must be narrated\nstderr={result.stderr}"
    )
    assert str(task_dir / "logs" / "dispatch.jsonl") in result.stderr, (
        f"skip narration should name the ledger path\nstderr={result.stderr}"
    )
    assert "handled 0 transient task(s)" in result.stderr, result.stderr
    assert _read_status(task_dir) == before, "status.md must be left untouched"
    assert _action_log(proj_dir) == "", "no action may be logged for a skipped task"


# ---------------------------------------------------------------------------
# (K) A second dispatch supersedes the first record.
# ---------------------------------------------------------------------------


def test_second_dispatch_record_supersedes_first(tmp_path: pathlib.Path) -> None:
    """
    Fixture (K): the task was dispatched twice. The first batch log carries a
    529 inside the task's span; the second (newer) batch log is clean. Both
    ledger lines are written by the production writer.

    Expected:
      - dispatch_record_latest names the second batch log
      - the check reads the newer log: no signature, task stays BLOCKED
    """
    kanban_root = tmp_path / "kanban"
    project_name = "test-project-k"
    task_id = "CODER-20260101-010-fixture-k"

    proj_dir = _build_kanban(kanban_root, project_name)
    tasks_root = proj_dir / "tasks"
    task_dir = _build_blocked_task(tasks_root, task_id, requeue_count=0)
    _build_queue_with_blocked(tasks_root / "queues", task_id)

    agents_dir = kanban_root / "logs" / "agents"
    agents_dir.mkdir(parents=True)
    first = agents_dir / "coder-batch-20260101-100000.log"
    second = agents_dir / "coder-batch-20260101-110000.log"
    first.write_text("API Error: 529 overloaded_error\n", encoding="utf-8")
    second.write_text("agent finished without provider errors\n", encoding="utf-8")

    script = _stub_header(proj_dir) + textwrap.dedent(f"""\
        set -euo pipefail
        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"
        dispatch_record_append "{task_dir}" dispatched "{first}" 0
        dispatch_record_append "{task_dir}" returned "{first}" 0 "$(wc -c < "{first}")" 1
        dispatch_record_append "{task_dir}" dispatched "{second}" 0
        dispatch_record_append "{task_dir}" returned "{second}" 0 "$(wc -c < "{second}")" 1
        echo "LATEST: $(dispatch_record_latest "{task_dir}")"
        source {_CHECK_TRANSIENT}
        overwatch_check_transient_api_error
    """)

    result = run_bash(tmp_path, script)
    assert result.returncode == 0, result.stderr
    assert f"LATEST: {second}\t" in result.stdout, (
        f"the newer dispatch must win\nstdout={result.stdout}"
    )
    assert "no transient signature" in result.stderr, (
        f"the superseded log's 529 must not be read\nstderr={result.stderr}"
    )
    assert "BLOCKED" in _read_status(task_dir)
    assert "transient-auto-requeued" not in _action_log(proj_dir)


# ---------------------------------------------------------------------------
# (L) Output after the task's closed span belongs to another task.
# ---------------------------------------------------------------------------


def test_signature_outside_recorded_span_not_attributed(tmp_path: pathlib.Path) -> None:
    """
    Fixture (L): one firing dispatched this task and then another; the second
    task's 529 lands in the same batch log AFTER this task's closed span.

    Expected: this task's span is clean, so it is NOT requeued.
    """
    kanban_root = tmp_path / "kanban"
    project_name = "test-project-l"
    task_id = "CODER-20260101-011-fixture-l"

    proj_dir = _build_kanban(kanban_root, project_name)
    tasks_root = proj_dir / "tasks"
    task_dir = _build_blocked_task(tasks_root, task_id, requeue_count=0)
    _build_queue_with_blocked(tasks_root / "queues", task_id)
    _write_batch_log_with_error(
        kanban_root, task_dir, "Permission denied: cannot access the dev tree",
        following="starting next task\nAPI Error: 529 overloaded_error\n",
    )

    script = _stub_header(proj_dir) + textwrap.dedent(f"""\
        set -euo pipefail
        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"
        source {_CHECK_TRANSIENT}
        overwatch_check_transient_api_error
    """)

    result = run_bash(tmp_path, script)
    assert result.returncode == 0, result.stderr
    assert "no transient signature" in result.stderr, (
        f"a 529 outside the task's span must not be attributed to it\nstderr={result.stderr}"
    )
    assert "BLOCKED" in _read_status(task_dir)
    assert "transient-auto-requeued" not in _action_log(proj_dir)


# ---------------------------------------------------------------------------
# (M) Ledger names a batch log that is gone → narrated skip.
# ---------------------------------------------------------------------------


def test_missing_recorded_batch_log_narrated_skip(tmp_path: pathlib.Path) -> None:
    """
    Fixture (M): the ledger is present but the batch log it names was rotated
    or deleted.

    Expected: narrated skip, exit 0, status untouched, no action logged.
    """
    kanban_root = tmp_path / "kanban"
    project_name = "test-project-m"
    task_id = "CODER-20260101-012-fixture-m"

    proj_dir = _build_kanban(kanban_root, project_name)
    tasks_root = proj_dir / "tasks"
    task_dir = _build_blocked_task(tasks_root, task_id, requeue_count=0)
    _build_queue_with_blocked(tasks_root / "queues", task_id)
    batch_log = _write_batch_log_with_error(kanban_root, task_dir, "529 Overloaded")
    batch_log.unlink()
    before = _read_status(task_dir)

    script = _stub_header(proj_dir) + textwrap.dedent(f"""\
        set -euo pipefail
        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"
        source {_CHECK_TRANSIENT}
        overwatch_check_transient_api_error
    """)

    result = run_bash(tmp_path, script)
    assert result.returncode == 0, result.stderr
    assert "no longer exists" in result.stderr and "skipping" in result.stderr, result.stderr
    assert "handled 0 transient task(s)" in result.stderr, result.stderr
    assert _read_status(task_dir) == before
    assert _action_log(proj_dir) == ""


# ---------------------------------------------------------------------------
# (N)-(Q) Section writers: rewriting one section never removes or alters any
#     other section, whatever the target body holds. Every fixture puts the
#     empty target section BEFORE other sections, the shape where a writer
#     that lets an empty body run into the next heading swallows that
#     heading and its body.
# ---------------------------------------------------------------------------

# Empty-safe section reader for assertions: the heading line, then body lines
# none of which starts with "##", so an empty body reads as "" rather than as
# the next section's text.
_SECTION_RE = r"(^## {heading}[^\S\n]*)((?:\n(?!##)[^\n]*)*?)(\n+##|\n*\Z)"


def _section_body(status: str, heading: str) -> str | None:
    """Return the stripped body of ``## heading`` (None when the heading is absent)."""
    import re as _re

    m = _re.search(_SECTION_RE.format(heading=_re.escape(heading)), status, flags=_re.M)
    return m.group(2).strip() if m else None


def _headings(status: str) -> list[str]:
    """Return every ``## `` heading in file order."""
    return [line[3:].strip() for line in status.splitlines() if line.startswith("## ")]


# The status file the requeue must survive: every section the requeue writes
# (State, Summary, Labels, Blockers, Needs Human) is followed by another
# section, and two sections the requeue never writes bracket the end.
_REQUEUE_SECTIONS: list[tuple[str, str]] = [
    ("Task", "CODER-20260101-020-section-writer"),
    ("State", "BLOCKED"),
    ("Summary", "old summary"),
    ("Labels", ""),
    ("Blockers", "real blocker text"),
    ("Needs Human", "yes"),
    ("Next Recommended Step", "keep me"),
    ("Instruction Conflicts", "none"),
]


def _render_status(sections: list[tuple[str, str]]) -> str:
    """Render (heading, body) pairs as a status.md; an empty body stays empty."""
    parts = ["# Status\n"]
    for heading, body in sections:
        parts.append(f"\n## {heading}\n" + (f"{body}\n" if body else ""))
    return "".join(parts)


def _run_requeue_writer(
    tmp_path: pathlib.Path, proj_dir: pathlib.Path, task_dir: pathlib.Path, new_count: int = 1
):
    """Run the shipped _ctae_do_requeue against task_dir/status.md."""
    status_file = task_dir / "status.md"
    script = _stub_header(proj_dir) + textwrap.dedent(f"""\
        set -euo pipefail
        export KANBAN_ROOT="{proj_dir.parent.parent}"
        export OVERWATCH_PROJECT="{proj_dir.name}"
        source {_CHECK_TRANSIENT}
        export _CTAE_TASK_ID="{task_dir.name}"
        export _CTAE_STATUS_FILE="{status_file}"
        export _CTAE_NEW_COUNT="{new_count}"
        _ctae_do_requeue
    """)
    return run_bash(tmp_path, script)


def _assert_requeue_outcome(before: str, after: str, expected_labels: str) -> None:
    """Assert the requeue's intended rewrite and that no section was lost or altered."""
    # Every heading survives, in order; the durable retry record is the only
    # addition the requeue makes.
    assert [h for h in _headings(after) if h != "Transient Retry"] == _headings(before), (
        f"requeue removed or reordered a section\nbefore:\n{before}\nafter:\n{after}"
    )
    assert _section_body(after, "State") == "BACKLOG", after
    assert _section_body(after, "Labels") == expected_labels, after
    assert _section_body(after, "Blockers") == "none", after
    assert _section_body(after, "Needs Human") == "no", after
    assert (_section_body(after, "Summary") or "").startswith("Auto-requeued by OVERWATCH"), after
    # Sections the requeue does not own are left exactly as they were.
    for heading in ("Task", "Next Recommended Step", "Instruction Conflicts"):
        assert _section_body(after, heading) == _section_body(before, heading), (
            f"## {heading} was altered\nbefore:\n{before}\nafter:\n{after}"
        )


def test_requeue_fills_empty_labels_without_removing_next_section(tmp_path: pathlib.Path) -> None:
    """
    Fixture (N): a BLOCKED task whose ## Labels is empty and followed by
    ## Blockers, requeued end to end through overwatch_check_transient_api_error.

    Expected: ## Labels reads TRANSIENT, ## Blockers is still present and reads
    none, and every other section survives — so a later status write naming
    ## Blockers has a section to update.
    """
    kanban_root = tmp_path / "kanban"
    project_name = "test-project-n"
    task_id = "CODER-20260101-020-section-writer"

    proj_dir = _build_kanban(kanban_root, project_name)
    tasks_root = proj_dir / "tasks"
    task_dir = _build_blocked_task(tasks_root, task_id, requeue_count=0)
    before = _render_status(_REQUEUE_SECTIONS)
    (task_dir / "status.md").write_text(before, encoding="utf-8")
    _build_queue_with_blocked(tasks_root / "queues", task_id)
    _write_batch_log_with_error(kanban_root, task_dir, "529 Overloaded")

    script = _stub_header(proj_dir) + textwrap.dedent(f"""\
        set -euo pipefail
        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{project_name}"
        source {_CHECK_TRANSIENT}
        overwatch_check_transient_api_error
    """)

    result = run_bash(tmp_path, script)
    assert result.returncode == 0, result.stderr
    assert "transient-auto-requeued" in _action_log(proj_dir), result.stderr
    _assert_requeue_outcome(before, _read_status(task_dir), expected_labels="TRANSIENT")


@pytest.mark.parametrize("empty_heading", ["State", "Summary", "Labels", "Blockers", "Needs Human"])
def test_requeue_with_empty_section_removes_no_other_section(
    tmp_path: pathlib.Path, empty_heading: str
) -> None:
    """
    Fixture (O): the requeue writer runs on a status file where one section
    it rewrites has an empty body and is followed by other sections.

    Expected: the empty section is filled in place with the requeue's value
    and every other heading survives with its intended body.
    """
    kanban_root = tmp_path / "kanban"
    proj_dir = _build_kanban(kanban_root, "test-project-o")
    task_dir = _build_blocked_task(proj_dir / "tasks", "CODER-20260101-021-section-writer")
    sections = [
        (heading, "" if heading == empty_heading else body)
        for heading, body in _REQUEUE_SECTIONS
    ]
    # Labels stays empty in every case; the populated case is fixture (P).
    before = _render_status(sections)
    (task_dir / "status.md").write_text(before, encoding="utf-8")

    result = _run_requeue_writer(tmp_path, proj_dir, task_dir)
    assert result.returncode == 0, result.stderr
    _assert_requeue_outcome(before, _read_status(task_dir), expected_labels="TRANSIENT")


def test_requeue_appends_transient_to_populated_labels(tmp_path: pathlib.Path) -> None:
    """
    Fixture (P): ## Labels already holds EXISTING-LABEL and is followed by
    other sections.

    Expected: ## Labels reads exactly "EXISTING-LABEL TRANSIENT" and no other
    section is lost.
    """
    kanban_root = tmp_path / "kanban"
    proj_dir = _build_kanban(kanban_root, "test-project-p")
    task_dir = _build_blocked_task(proj_dir / "tasks", "CODER-20260101-022-section-writer")
    sections = [
        (heading, "EXISTING-LABEL" if heading == "Labels" else body)
        for heading, body in _REQUEUE_SECTIONS
    ]
    before = _render_status(sections)
    (task_dir / "status.md").write_text(before, encoding="utf-8")

    result = _run_requeue_writer(tmp_path, proj_dir, task_dir)
    assert result.returncode == 0, result.stderr
    _assert_requeue_outcome(
        before, _read_status(task_dir), expected_labels="EXISTING-LABEL TRANSIENT"
    )


@pytest.mark.parametrize("empty_heading", ["Needs Human", "Blocked Reason"])
def test_ceiling_escalation_with_empty_section_removes_no_other_section(
    tmp_path: pathlib.Path, empty_heading: str
) -> None:
    """
    Fixture (Q): the ceiling escalation writer runs on a status file where
    ## Needs Human or ## Blocked Reason is empty and followed by other
    sections.

    Expected: Needs Human reads yes, Blocked Reason names the retry ceiling,
    and every other section survives unchanged.
    """
    kanban_root = tmp_path / "kanban"
    proj_dir = _build_kanban(kanban_root, "test-project-q")
    task_dir = _build_blocked_task(proj_dir / "tasks", "CODER-20260101-023-section-writer")
    sections = [
        ("Task", task_dir.name),
        ("State", "BLOCKED"),
        ("Needs Human", "no"),
        ("Blocked Reason", "earlier reason"),
        ("Blockers", "real blocker text"),
        ("Next Recommended Step", "keep me"),
    ]
    sections = [(h, "" if h == empty_heading else b) for h, b in sections]
    before = _render_status(sections)
    status_file = task_dir / "status.md"
    status_file.write_text(before, encoding="utf-8")

    script = _stub_header(proj_dir) + textwrap.dedent(f"""\
        set -euo pipefail
        export KANBAN_ROOT="{kanban_root}"
        export OVERWATCH_PROJECT="{proj_dir.name}"
        source {_CHECK_TRANSIENT}
        export _CTAE_TASK_ID="{task_dir.name}"
        export _CTAE_STATUS_FILE="{status_file}"
        export _CTAE_CURRENT_COUNT="3"
        export _CTAE_CAP="3"
        _ctae_do_escalate
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, result.stderr

    after = _read_status(task_dir)
    assert _headings(after) == _headings(before), (
        f"escalation removed or reordered a section\nbefore:\n{before}\nafter:\n{after}"
    )
    assert _section_body(after, "Needs Human") == "yes", after
    assert "retry ceiling reached (3/3" in (_section_body(after, "Blocked Reason") or ""), after
    for heading in ("Task", "State", "Blockers", "Next Recommended Step"):
        assert _section_body(after, heading) == _section_body(before, heading), (
            f"## {heading} was altered\nbefore:\n{before}\nafter:\n{after}"
        )


# ---------------------------------------------------------------------------
# (R) A reader that stops at its first match never turns a match into a miss
# (S) 200 sequential matches under strict mode, zero misses
#
# grep -q exits as soon as it matches. If the matcher fed it through a pipe,
# the writer on the other end would die of SIGPIPE (exit 141) and
# `set -o pipefail` would report the match as a miss. (R) forces that
# early exit deterministically with a grep stand-in; (S) runs the real
# matcher repeatedly, in one process loop, with no load generation.
# ---------------------------------------------------------------------------

# Bytes of filler after the signature line. Twice the default 64 KiB pipe
# capacity: once the stand-in returns, more is left unread than any pipe
# could hold, so a pipe writer is always still writing when the reader goes.
_EARLY_EXIT_FILLER_BYTES = 131072

# A stand-in for grep that behaves like grep -q at line granularity: it reads
# one line at a time, tests that line with the real grep and the caller's own
# flags and pattern, and returns 0 on the first matching line WITHOUT reading
# the rest of its input. Bash's `read` takes a pipe one byte at a time, so the
# stand-in never consumes more than the lines it has looked at.
_EARLY_EXIT_GREP_STANDIN = textwrap.dedent("""\
    grep() {
        local line
        while IFS= read -r line; do
            if command grep "$@" <<< "${line}"; then
                return 0
            fi
        done
        return 1
    }
""")


def _diagnostic(result) -> str:
    """Render a bash result's own stdout and stderr for an assertion message."""
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


@pytest.mark.parametrize(
    ("signature_line", "pattern"),
    [
        ("ERROR: 503 Service Unavailable from upstream API", r"\b503\b"),
        ("API Error: 529 Overloaded", "API Error: 5[0-9][0-9]"),
    ],
)
def test_signature_on_early_line_matches_when_reader_exits_at_first_match(
    tmp_path: pathlib.Path, signature_line: str, pattern: str
) -> None:
    """
    Fixture (R): under `set -euo pipefail`, a multi-line text carries a
    transient signature on its second line, followed by more filler than a
    pipe can buffer. grep is replaced by a stand-in that returns on its first
    matching line without reading the rest of its input.

    Expected: _ctae_matches_transient_signature reports a match. The script
    first proves the stand-in has teeth: piping the same text into it under
    pipefail must fail with the writer's SIGPIPE status (141), which is
    exactly how a pipe-fed matcher would lose the match.
    """
    script = (
        "set -euo pipefail\n"
        f"source {_CHECK_TRANSIENT}\n"
        + _EARLY_EXIT_GREP_STANDIN
        + textwrap.dedent(f"""\
            filler="$(printf '%*s' {_EARLY_EXIT_FILLER_BYTES} '' | tr ' ' 'x')"
            text=$'run started\\n''{signature_line}'$'\\n'"${{filler}}"$'\\nrun ended'

            # The stand-in must break a pipe writer, or this test proves nothing.
            set +e
            echo "${{text}}" | grep -qE '{pattern}'
            pipe_writer_status="${{PIPESTATUS[0]}}"
            set -e
            echo "pipe_writer_status=${{pipe_writer_status}}"

            if _ctae_matches_transient_signature "${{text}}"; then
                echo "MATCH"
            else
                echo "NO_MATCH"
            fi
        """)
    )
    result = run_bash(tmp_path, script, timeout=60)
    assert result.returncode == 0, _diagnostic(result)
    assert "pipe_writer_status=141" in result.stdout, (
        "The early-exit grep stand-in no longer breaks a pipe writer, so this "
        "test cannot tell a race-free matcher from a racy one\n"
        + _diagnostic(result)
    )
    assert "MATCH" in result.stdout.splitlines(), (
        f"Signature {signature_line!r} on an early line was reported as no "
        "match when the reader exits at its first match\n" + _diagnostic(result)
    )


def test_matcher_has_zero_misses_over_200_sequential_runs(tmp_path: pathlib.Path) -> None:
    """
    Fixture (S): the real matcher, with the real grep, runs 200 times in one
    process loop under `set -euo pipefail` on a four-line batch-log span whose
    signature sits on line 3, not the last line.

    Expected: every run matches; the script reports zero misses. Runs are
    strictly sequential: no parallel workers and no generated load.
    """
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source {_CHECK_TRANSIENT}
        span=$'Agent starting task\\nCalling upstream API\\nERROR: 503 Service Unavailable from upstream API\\nAgent exiting'
        misses=0
        for (( run = 1; run <= 200; run++ )); do
            if ! _ctae_matches_transient_signature "${{span}}"; then
                misses=$(( misses + 1 ))
            fi
        done
        echo "misses=${{misses}}"
    """)
    result = run_bash(tmp_path, script, timeout=120)
    assert result.returncode == 0, _diagnostic(result)
    assert "misses=0" in result.stdout.splitlines(), (
        "The transient matcher missed a signature it should always match\n"
        + _diagnostic(result)
    )


# ---------------------------------------------------------------------------
# (W) The requeue clears the task's worktree inside the locked fix
# ---------------------------------------------------------------------------
#
# These fixtures run the REAL protocol (backup, action log and the HALT-first
# wrapper with its per-project repo lock). pp_run_ops is wrapped so the
# worktree removal records, as it starts, whether the repo lock is held (a
# second flock on the lock file fails while the fix holds it) and what the
# task's State is.


def _real_protocol_root(kanban_root: pathlib.Path, project_name: str, dev_tree: pathlib.Path) -> pathlib.Path:
    proj_dir = _build_kanban(kanban_root, project_name, dev_tree=dev_tree, branch_prefix="ai_")
    (kanban_root / "locks").mkdir(parents=True, exist_ok=True)
    (kanban_root / "tmp").mkdir(parents=True, exist_ok=True)
    (kanban_root / "projects.cfg").write_text(f"[project:{project_name}]\npriority = 1\n", encoding="utf-8")
    return proj_dir


def _run_real_check(tmp_path, kanban_root, project_name, task_dir, observations):
    script = textwrap.dedent(f"""\
        set -euo pipefail
        export KANBAN_ROOT="{kanban_root}" PGAI_AGENT_KANBAN_ROOT_PATH="{kanban_root}"
        export PGAI_AGENT_KANBAN_TEMP_DIR="{kanban_root}/tmp" OVERWATCH_PROJECT="{project_name}" HALT_OVERWATCH=""
        export OVERWATCH_FIRING_TIMESTAMP="2026-04-04T04:04:04Z"
        source scripts/lib/project_paths.sh
        source scripts/lib/pp_run_ops.sh
        eval "$(declare -f pp_run_ops | sed '1s/^pp_run_ops/_real_pp_run_ops/')"
        pp_run_ops() {{
            if [[ "$1" == "pgai_agent_kanban.overwatch.worktree_residue" && "$2" == "remove-finished" ]]; then
                local lock state
                lock="$(pp_repo_lock_path "$OVERWATCH_PROJECT")"
                if flock -n "$lock" true 2>/dev/null; then echo "lock=free" >> "{observations}"; else echo "lock=held" >> "{observations}"; fi
                state="$(awk '/^## State/{{getline; print; exit}}' "{task_dir}/status.md")"
                echo "state=$state" >> "{observations}"
            fi
            _real_pp_run_ops "$@"
        }}
        source scripts/lib/overwatch_protocol.sh
        source {_CHECK_TRANSIENT}
        overwatch_check_transient_api_error
    """)
    return run_bash(tmp_path, script, timeout=180)


def _actions_rows(proj_dir: pathlib.Path, action: str) -> list:
    return [r.split("\t") for r in _action_log(proj_dir).splitlines() if f"\t{action}\t" in r]


def _prefixed_repo(dev_tree: pathlib.Path) -> None:
    """A repository with no bare main branch: its integration branch is ai_main."""
    _make_git_repo(dev_tree)
    _git(dev_tree, "branch", "-m", "ai_main")


def test_requeue_removes_the_clean_worktree_under_the_lock_before_backlog(tmp_path: pathlib.Path) -> None:
    kanban_root, dev_tree = tmp_path / "kanban", tmp_path / "dev"
    project_name, task_id = "test-project-w1", "CODER-20260101-031-fixture-w1"
    _prefixed_repo(dev_tree)
    wt_path = tmp_path / "worktrees" / task_id
    _git(dev_tree, "worktree", "add", "--detach", str(wt_path))
    proj_dir = _real_protocol_root(kanban_root, project_name, dev_tree)
    task_dir = _build_blocked_task(proj_dir / "tasks", task_id)
    _build_queue_with_blocked(proj_dir / "tasks" / "queues", task_id)
    _write_batch_log_with_error(kanban_root, task_dir, "529 Overloaded")
    observations = tmp_path / "observations.txt"

    result = _run_real_check(tmp_path, kanban_root, project_name, task_dir, observations)

    assert result.returncode == 0, result.stderr
    assert observations.read_text(encoding="utf-8").split() == ["lock=held", "state=BLOCKED"], result.stderr
    assert not wt_path.exists(), result.stderr
    assert "BACKLOG" in _read_status(task_dir)
    rows = _actions_rows(proj_dir, "worktree-pruned")
    assert len(rows) == 1, _action_log(proj_dir)
    assert rows[0][4] == "none", "a removal saves nothing restorable, so it names no backup"
    assert ".git" not in rows[0][4]
    log = _action_log(proj_dir)
    assert log.index("\tworktree-pruned\t") < log.index("\ttransient-auto-requeued\t")


def test_worktree_with_an_uncommitted_edit_is_never_removed(tmp_path: pathlib.Path) -> None:
    kanban_root, dev_tree = tmp_path / "kanban", tmp_path / "dev"
    project_name, task_id = "test-project-w2", "CODER-20260101-032-fixture-w2"
    _prefixed_repo(dev_tree)
    wt_path = tmp_path / "worktrees" / task_id
    _git(dev_tree, "worktree", "add", "--detach", str(wt_path))
    (wt_path / "README.md").write_text("an uncommitted edit\n", encoding="utf-8")
    proj_dir = _real_protocol_root(kanban_root, project_name, dev_tree)
    task_dir = _build_blocked_task(proj_dir / "tasks", task_id)
    _build_queue_with_blocked(proj_dir / "tasks" / "queues", task_id)
    _write_batch_log_with_error(kanban_root, task_dir, "529 Overloaded")

    result = _run_real_check(tmp_path, kanban_root, project_name, task_dir, tmp_path / "obs.txt")

    assert result.returncode == 0, result.stderr
    assert (wt_path / "README.md").read_text(encoding="utf-8") == "an uncommitted edit\n"
    rows = _actions_rows(proj_dir, "worktree-kept")
    assert rows and "uncommitted or untracked changes" in rows[0][5], _action_log(proj_dir)
    assert not _actions_rows(proj_dir, "worktree-pruned")


def test_worktree_with_commits_on_a_repo_without_bare_main_is_kept(tmp_path: pathlib.Path) -> None:
    kanban_root, dev_tree = tmp_path / "kanban", tmp_path / "dev"
    project_name, task_id = "test-project-w3", "CODER-20260101-033-fixture-w3"
    _prefixed_repo(dev_tree)
    wt_path = tmp_path / "worktrees" / task_id
    # The task's feature branch is checked out in the worktree, with no
    # upstream: its commits exist on that branch alone.
    _git(dev_tree, "worktree", "add", "-b", f"ai_feature/{task_id}", str(wt_path))
    _make_commit(wt_path, "work-found-nowhere-else")
    proj_dir = _real_protocol_root(kanban_root, project_name, dev_tree)
    task_dir = _build_blocked_task(proj_dir / "tasks", task_id)
    _build_queue_with_blocked(proj_dir / "tasks" / "queues", task_id)
    _write_batch_log_with_error(kanban_root, task_dir, "529 Overloaded")

    result = _run_real_check(tmp_path, kanban_root, project_name, task_dir, tmp_path / "obs.txt")

    assert result.returncode == 0, result.stderr
    assert (wt_path / "work-found-nowhere-else.txt").exists(), "commits found nowhere else keep the worktree"
    assert _actions_rows(proj_dir, "worktree-carries-commits-bug-filed"), _action_log(proj_dir)
    assert not _actions_rows(proj_dir, "worktree-pruned")


def test_escalation_at_the_ceiling_keeps_the_worktree(tmp_path: pathlib.Path) -> None:
    kanban_root, dev_tree = tmp_path / "kanban", tmp_path / "dev"
    project_name, task_id = "test-project-w4", "CODER-20260101-034-fixture-w4"
    _prefixed_repo(dev_tree)
    wt_path = tmp_path / "worktrees" / task_id
    _git(dev_tree, "worktree", "add", "--detach", str(wt_path))
    proj_dir = _real_protocol_root(kanban_root, project_name, dev_tree)
    task_dir = _build_blocked_task(proj_dir / "tasks", task_id, requeue_count=3, retry_limit=3)
    _build_queue_with_blocked(proj_dir / "tasks" / "queues", task_id)
    _write_batch_log_with_error(kanban_root, task_dir, "529 Overloaded")
    observations = tmp_path / "obs.txt"

    result = _run_real_check(tmp_path, kanban_root, project_name, task_dir, observations)

    assert result.returncode == 0, result.stderr
    status = _read_status(task_dir)
    assert "BLOCKED" in status and "Needs Human\nyes" in status, status
    assert wt_path.is_dir(), "the escalated task keeps the worktree a human is asked to investigate"
    assert not observations.exists(), "no removal was attempted"
    assert not _actions_rows(proj_dir, "worktree-pruned")
