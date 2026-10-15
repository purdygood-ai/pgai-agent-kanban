"""
test_deep_verify_rc_isolation.py
===================================
Integration test for the DEEP-VERIFY tier's RC-isolation guarantee: a
deep-verify run firing while an RC happens to be open (a weekly scheduled
firing at 4am mid-flight, or an on-demand run) must not change that RC's
state, must not write release-state.md, must not modify a task or a queue
file, and must not halt anything. It files the bug and it is done -- the
loop heals it in the next RC, not this one (team/SOP-core.md, "Gates and
Refusals" -- "Unusable build blocks; an unavailable build environment does not").

This test asserts the absence behaviorally against a fixture kanban with an
RC open and a WORKING task in the queue -- never by grepping
team/scripts/deep-verify.sh for the strings it does not contain. The stub
lane fails (so the run actually reaches deep-verify's bug-filing path),
and the assertion is: everything outside the project's bugs/
directory and its deep-verify ledger is byte-identical before and after.

Uses the two_project_root fixture (team/tests/fixtures/two_project.py,
re-exported by tests/integration/conftest.py) for its full project tree
(release-state.md, tasks/queues/, project.cfg) rather than hand-rolling a
second synthetic tree with the same shape.

No test invokes a real container build -- the cold lane is stubbed via the
PGAI_DEEP_VERIFY_COLD_LANE_CMD seam, the same idiom test_deep_verify_run.py
uses. tmp_path is redirected under $PGAI_AGENT_KANBAN_TEMP_DIR by
team/tests/conftest.py's pytest_configure, so every fixture path this file
creates already satisfies the framework's temp-file hygiene rule.

Test names describe behaviour under test, never bug/task IDs (team/roles/TESTER.md,
Test Authoring Guidelines, anti-pattern 6).
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

from tests.fixtures.two_project import PROJECT_A
from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import scrub_agent_scoped_env


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Every test here builds, inspects, or drives a real git repository; a
    module-wide autouse guard keeps that requirement in one place instead
    of repeating a per-test check at each git-dependent call site.
    """
    require_binary("git", label="deep-verify-rc-isolation")

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_DEEP_VERIFY_SCRIPT = _TEAM_DIR / "scripts" / "deep-verify.sh"

_STUB_DOCKERFILE_FAILURE = """\
#!/usr/bin/env bash
echo "E       Failed: docker build of docker/claude-code/Dockerfile failed (exit 1) at step: RUN npm ci; base-image reachability was confirmed" 1>&2
echo "Cold-build lane FAILED (exit code: 1)" 1>&2
exit 1
"""

_TASK_ID = "CODER-20260601-042-mid-flight-task"


def _write_stub(tmp_path: pathlib.Path) -> pathlib.Path:
    stub = tmp_path / "stub_fail.sh"
    stub.write_text(_STUB_DOCKERFILE_FAILURE, encoding="utf-8")
    stub.chmod(0o755)
    return stub


def _set_active_rc(project_dir: pathlib.Path, rc_version: str) -> None:
    (project_dir / "release-state.md").write_text(
        "# Release State\n\n"
        f"## Active RC\n{rc_version}\n\n"
        "## RC Opened At\n2026-06-01T00:00:00+00:00\n\n"
        "## RC Opened By Task\nCM-20260601-001-open-rc\n\n"
        "## Last Released\nv0.0.0\n",
        encoding="utf-8",
    )


def _add_working_task(project_dir: pathlib.Path) -> pathlib.Path:
    """Create a WORKING task under the project's tasks/ dir, plus a queue
    entry naming it, mirroring the shape a real mid-RC task carries."""
    task_dir = project_dir / "tasks" / _TASK_ID
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "README.md").write_text(
        f"# Task: {_TASK_ID}\n\n## Working Directory\nnone\n", encoding="utf-8"
    )
    (task_dir / "status.md").write_text(
        "# Status\n\n"
        f"## Task\n{_TASK_ID}\n\n"
        "## State\nWORKING\n\n"
        "## Summary\nmid-flight fixture task\n\n"
        "## Blockers\nnone\n\n"
        "## Needs Human\nno\n",
        encoding="utf-8",
    )
    queue_file = project_dir / "tasks" / "queues" / "coder_backlog.md"
    with open(queue_file, "a", encoding="utf-8") as handle:
        handle.write(f"- [ ] {_TASK_ID} — mid-flight fixture task\n")
    return task_dir


def _snapshot(project_dir: pathlib.Path) -> dict:
    """Capture the byte content of every file this test's isolation
    guarantee covers: release-state.md, every task status.md, and every
    queue file. Keyed by path relative to project_dir so a diff names the
    exact file that changed."""
    snapshot = {}
    snapshot["release-state.md"] = (project_dir / "release-state.md").read_bytes()
    for status_file in sorted((project_dir / "tasks").rglob("status.md")):
        snapshot[str(status_file.relative_to(project_dir))] = status_file.read_bytes()
    queues_dir = project_dir / "tasks" / "queues"
    for queue_file in sorted(queues_dir.glob("*.md")):
        snapshot[str(queue_file.relative_to(project_dir))] = queue_file.read_bytes()
    return snapshot


def _run_deep_verify_red(kanban_root: pathlib.Path, project: str, stub: pathlib.Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env.pop("PGAI_PROJECT_NAME", None)
    env["PGAI_DEEP_VERIFY_COLD_LANE_CMD"] = str(stub)
    # The stubbed lane stands in for the container tier, which runs only
    # with operator authorization and never inside an agent wake.
    scrub_agent_scoped_env(env)
    env["PGAI_CONTAINER_TIER"] = "1"
    return subprocess.run(
        ["bash", str(_DEEP_VERIFY_SCRIPT), "--project", project, "--image", "all"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_red_run_against_open_rc_leaves_release_state_tasks_and_queues_byte_identical(
    two_project_root: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    kanban_root = two_project_root
    project_dir = kanban_root / "projects" / PROJECT_A

    _set_active_rc(project_dir, "v3.1.4")
    _add_working_task(project_dir)
    stub = _write_stub(tmp_path)

    before = _snapshot(project_dir)
    global_halt_before = (kanban_root / "HALT").exists()
    project_halt_before = (project_dir / "HALT").exists()

    result = _run_deep_verify_red(kanban_root, PROJECT_A, stub)

    assert result.returncode == 1, result.stderr
    # The run actually reached the failure path this test is guarding --
    # otherwise a no-op run would trivially "preserve" everything and the
    # isolation guarantee would be untested.
    bug_files = list((project_dir / "bugs").glob("BUG-*.md"))
    assert len(bug_files) == 1, "the red run must reach the bug-filing path for this test to mean anything"

    after = _snapshot(project_dir)
    assert after == before, "deep-verify.sh must not modify release-state.md, any task status.md, or any queue file"

    assert not (kanban_root / "HALT").exists()
    assert not (project_dir / "HALT").exists()
    assert (kanban_root / "HALT").exists() == global_halt_before
    assert (project_dir / "HALT").exists() == project_halt_before

    # Active RC is still exactly what it was -- read back explicitly, not
    # just via the byte-identical release-state.md check above, so a future
    # reader sees the load-bearing assertion spelled out.
    release_state_text = (project_dir / "release-state.md").read_text(encoding="utf-8")
    assert "## Active RC\nv3.1.4" in release_state_text

    # The WORKING task is still WORKING -- the run did not touch it.
    status_text = (project_dir / "tasks" / _TASK_ID / "status.md").read_text(encoding="utf-8")
    assert "## State\nWORKING" in status_text


def _current_ref_and_commit(repo_dir: pathlib.Path) -> tuple[str, str]:
    """Return (ref, commit) for repo_dir's HEAD, defined for both an
    attached and a detached HEAD.

    `git rev-parse --abbrev-ref HEAD` -- unlike `git symbolic-ref --short
    HEAD` -- never exits non-zero for a detached HEAD: it prints the literal
    string "HEAD" instead of a branch name (team/roles/TESTER.md, Step 3:
    every TESTER verification worktree is checked out detached). Pairing it
    with the commit sha (`git rev-parse HEAD`, likewise defined in both
    states) closes the gap "HEAD" alone would leave: two detached checkouts
    both report ref "HEAD", so a bare ref comparison would miss HEAD moving
    to a different commit while staying detached.
    """
    ref = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return ref, commit


def test_red_run_against_open_rc_creates_no_branch_in_the_dev_tree(
    two_project_root: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """deep-verify.sh's own repo root is always the real dev tree it ships
    from (self-located from its own script path), independent of the fixture
    --project's kanban root -- so the branch/commit this test watches is the
    real tree's own HEAD, not anything inside the fixture. The tree may be
    branch-attached (a normal checkout) or detached (every TESTER
    verification worktree) -- the assertion below holds either way."""
    kanban_root = two_project_root
    project_dir = kanban_root / "projects" / PROJECT_A
    _set_active_rc(project_dir, "v3.1.4")
    stub = _write_stub(tmp_path)

    branches_before = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=str(_TEAM_DIR.parent),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    ref_before, commit_before = _current_ref_and_commit(_TEAM_DIR.parent)

    result = _run_deep_verify_red(kanban_root, PROJECT_A, stub)
    assert result.returncode == 1, result.stderr

    branches_after = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=str(_TEAM_DIR.parent),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    ref_after, commit_after = _current_ref_and_commit(_TEAM_DIR.parent)

    assert branches_after == branches_before
    assert ref_after == ref_before
    assert commit_after == commit_before
