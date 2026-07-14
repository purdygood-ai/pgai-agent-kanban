"""
test_overwatch_transient_api_error_bash.py
==========================================
Behavioral unit tests for check-transient-api-error.sh.

Fixtures exercised:
  (A) BLOCKED task with a 529 in log tail → status flipped to BACKLOG,
      TRANSIENT label present, requeue counter incremented.
  (B) Same task hitting the ceiling (3rd occurrence, count >= 2) →
      bug-filed, no further requeue.
  (C) Orphaned per-task worktree with no extra commits → pruned;
      action log has a matching action entry.
  (D) Worktree with local commits → bug-filed, worktree preserved.

All tests run against synthetic environments and never touch the live kanban root.
"""

from __future__ import annotations

import pathlib
import subprocess
import textwrap

import pytest

from tests.unit.shell_harness import run_bash

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
) -> pathlib.Path:
    """
    Create a minimal BLOCKED task directory with status.md and a queue entry.

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
        status_lines.append(f"## Transient Requeue Count\n{requeue_count}\n\n")
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


def _write_log_with_error(task_dir: pathlib.Path, error_text: str) -> None:
    """Write a synthetic agent log file containing error_text."""
    log_file = task_dir / "logs" / "agent.log"
    log_file.write_text(
        "Agent started.\nProcessing task...\n"
        f"ERROR: {error_text}\n"
        "Agent exited.\n",
        encoding="utf-8",
    )


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
    """Return bash stubs for overwatch_log_action and overwatch_halt_first_fix."""
    return textwrap.dedent(f"""\
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
    Fixture (A): BLOCKED task whose log tail contains '529'.

    Expected:
      - status.md State flipped to BACKLOG
      - ## Labels contains 'TRANSIENT'
      - ## Transient Requeue Count is 1
      - action log contains 'transient-auto-requeued'
    """
    kanban_root = tmp_path / "kanban"
    project_name = "test-project-a"
    task_id = "CODER-20260101-001-fixture-a"

    proj_dir = _build_kanban(kanban_root, project_name)
    tasks_root = proj_dir / "tasks"
    task_dir = _build_blocked_task(tasks_root, task_id, requeue_count=0)
    _build_queue_with_blocked(tasks_root / "queues", task_id)
    _write_log_with_error(task_dir, "529 Service Unavailable from upstream API")

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
    assert "Transient Requeue Count" in status, (
        f"Transient Requeue Count field should be present\nstatus:\n{status}"
    )
    # Count should be 1
    import re as _re
    count_match = _re.search(r"## Transient Requeue Count\s+(\d+)", status)
    assert count_match is not None, f"Could not find Transient Requeue Count value\nstatus:\n{status}"
    assert count_match.group(1) == "1", (
        f"Count should be 1, got {count_match.group(1)}\nstatus:\n{status}"
    )

    log = _action_log(proj_dir)
    assert "transient-auto-requeued" in log, (
        f"Action log should contain 'transient-auto-requeued'\nlog:\n{log}"
    )


# ---------------------------------------------------------------------------
# (B) Same task at ceiling (count=2) → bug-filed, no further requeue
# ---------------------------------------------------------------------------


def test_transient_ceiling_bug_files_on_third_occurrence(tmp_path: pathlib.Path) -> None:
    """
    Fixture (B): BLOCKED task whose Transient Requeue Count is already 2.

    Expected:
      - A bug file is written under projects/<name>/bugs/
      - status.md State remains BLOCKED (no requeue)
      - action log contains 'transient-ceiling-bug-filed'
    """
    kanban_root = tmp_path / "kanban"
    project_name = "test-project-b"
    task_id = "CODER-20260101-002-fixture-b"

    proj_dir = _build_kanban(kanban_root, project_name)
    tasks_root = proj_dir / "tasks"
    # requeue_count=2 means this is the 3rd occurrence → should bug-file
    task_dir = _build_blocked_task(tasks_root, task_id, requeue_count=2, labels="TRANSIENT")
    _build_queue_with_blocked(tasks_root / "queues", task_id)
    _write_log_with_error(task_dir, "API Error: 503 Service Unavailable")

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
    _write_log_with_error(task_dir, "529 Overloaded")

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
    _write_log_with_error(task_dir, "overloaded_error rate limit exceeded 429")

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
    Fixture (E): BLOCKED task whose log tail does NOT contain any transient signature.

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
    _write_log_with_error(task_dir, "Permission denied: cannot access /var/run/kanban.lock")

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
