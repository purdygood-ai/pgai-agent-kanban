"""
test_cross_cutting.py
=====================
Integration tests for cross-cutting system guarantees.

These tests exercise real multi-project trees, real lock mechanisms, the
real Active-RC gate, and real halt behavior — no seams are mocked.  Each
test stands up a temporary kanban tree (via two_project_root), runs real
flows, and asserts the guarantee holds in the resulting on-disk state.

Coverage:
  - Multi-project isolation: work in project_a does not bleed into project_b
  - Per-repo lock: the advisory flock prevents concurrent agent runs on the
    same project; a second acquisition attempt while the lock is held is
    detected and back-offed
  - Active-RC gate: PM decomposition (discovery_step_requirements) is
    blocked while an RC is active in the same project, enforcing the single-
    RC-in-flight invariant
  - Global halt vs per-project halt: global HALT blocks all projects;
    per-project HALT blocks only the named project, leaving others runnable

Design notes:
  - All tests use tmp_path (via two_project_root) for scratch. No bare /tmp
    paths appear in this file.
  - Each test is self-contained and produces no state visible to other tests.
  - Test names describe the behavioral guarantee, not the bug ID or gate label
    that motivated them (SOP.md Anti-pattern 6).
  - Discovery is invoked via _run_discovery(), a thin subprocess wrapper that
    sources the discovery library against the caller's temp tree and calls
    discovery_run_pipeline for the named project.
  - Lock behavior is tested via a Bash helper that exercises the real flock
    mechanism inside a subprocess.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import textwrap
from typing import Optional

import pytest

# ---------------------------------------------------------------------------
# Path to the dev tree (three levels up from team/tests/integration/).
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_SCRIPTS_LIB = _TEAM_DIR / "scripts" / "lib"


# ---------------------------------------------------------------------------
# Internal helpers (scoped to this file; same shape as test_discovery_pipeline.py)
# ---------------------------------------------------------------------------

def _run_discovery(
    kanban_root: pathlib.Path,
    project_name: str,
    *,
    extra_env: Optional[dict] = None,
    threshold: int = 3,
) -> subprocess.CompletedProcess:
    """Run discovery_run_pipeline against a temporary kanban tree.

    Sources project_paths.sh and discovery.sh from the dev tree's scripts/lib/,
    sets KANBAN_ROOT and TEAM_ROOT to *kanban_root* (the temp tree), then calls
    discovery_run_pipeline for the named project.

    DISCOVERY_LAST_STATUS is echoed on stdout after the run so callers can
    assert on the pipeline's outcome without parsing log lines.

    Args:
        kanban_root:  Path to the temporary kanban root (from two_project_root).
        project_name: Name of the project to run the pipeline for.
        extra_env:    Additional environment overrides (caller wins on collision).
        threshold:    PGAI_DISCOVERY_REJECT_THRESHOLD (default 3).

    Returns:
        subprocess.CompletedProcess with returncode, stdout, and stderr.
    """
    script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        source "{_SCRIPTS_LIB}/ini_parser.sh"
        source "{_SCRIPTS_LIB}/temp.sh"
        source "{_SCRIPTS_LIB}/semver.sh"
        source "{_SCRIPTS_LIB}/project_paths.sh"
        source "{_SCRIPTS_LIB}/discovery.sh"
        discovery_run_pipeline "{project_name}"
        echo "DISCOVERY_STATUS=${{DISCOVERY_LAST_STATUS}}"
    """)

    base_env = dict(os.environ)
    base_env["KANBAN_ROOT"] = str(kanban_root)
    base_env["TEAM_ROOT"] = str(kanban_root)
    base_env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    base_env["PGAI_PROJECT_NAME"] = project_name
    base_env["PGAI_DISCOVERY_REJECT_THRESHOLD"] = str(threshold)
    # Suppress git remote checks in compute_next_patch.
    base_env.pop("PGAI_DEV_TREE_PATH", None)
    if extra_env:
        base_env.update(extra_env)

    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=base_env,
        cwd=str(kanban_root),
        timeout=60,
    )


def _add_bug_file(
    kanban_root: pathlib.Path,
    project_name: str,
    bug_id: str = "BUG-0001",
    slug: str = "test-fix",
    status: str = "open",
) -> pathlib.Path:
    """Create a well-formed bug file in the project's bugs/ directory."""
    bugs_dir = kanban_root / "projects" / project_name / "bugs"
    bugs_dir.mkdir(parents=True, exist_ok=True)
    bug_file = bugs_dir / f"{bug_id}-{slug}.md"
    bug_file.write_text(
        f"# {bug_id}: {slug}\n\n"
        f"## Status\n{status}\n\n"
        "## Summary\nTest bug file.\n",
        encoding="utf-8",
    )
    return bug_file


def _set_active_rc(
    kanban_root: pathlib.Path,
    project_name: str,
    rc_version: str,
) -> None:
    """Set the Active RC field in the project's release-state.md."""
    release_state_path = kanban_root / "projects" / project_name / "release-state.md"
    release_state_path.write_text(
        "# Release State\n\n"
        f"## Active RC\n{rc_version}\n\n"
        "## RC Opened At\n2026-06-01T00:00:00+00:00\n\n"
        "## RC Opened By Task\nCM-20260601-001-open-rc\n",
        encoding="utf-8",
    )


def _requirements_dir(kanban_root: pathlib.Path, project_name: str) -> pathlib.Path:
    """Return the requirements/ path for a project."""
    return kanban_root / "projects" / project_name / "requirements"


def _add_requirements_file(
    kanban_root: pathlib.Path,
    project_name: str,
    version: str = "v0.0.2",
    slug: str = "test-feature",
    status: str = "open",
    workflow_type: str = "release",
) -> pathlib.Path:
    """Create a requirements file in the project's requirements/ directory."""
    req_dir = kanban_root / "projects" / project_name / "requirements"
    req_dir.mkdir(parents=True, exist_ok=True)
    req_file = req_dir / f"{version}-{slug}.md"
    req_file.write_text(
        f"# {version}: {slug}\n\n"
        f"## Status\n{status}\n\n"
        f"## Target Version\n{version}\n\n"
        f"## Workflow Type\n{workflow_type}\n\n"
        f"## PM Task\nnone\n\n"
        "## Summary\nTest requirements file.\n",
        encoding="utf-8",
    )
    return req_file


# ---------------------------------------------------------------------------
# Tests: Multi-Project Isolation
# ---------------------------------------------------------------------------


class TestMultiProjectIsolation:
    """Work in one project does not affect state in another project."""

    def test_bug_bundle_for_project_a_leaves_project_b_requirements_empty(
        self, two_project_root: pathlib.Path
    ) -> None:
        """A bug bundled in project_a does not create any file in project_b.

        The discovery pipeline runs per-project.  Bundling a bug in project_a
        must write a requirements file only inside project_a/requirements/;
        project_b/requirements/ must remain untouched.
        """
        root = two_project_root
        _add_bug_file(root, "project_a", bug_id="BUG-0070", slug="isolation-a-to-b")

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, (
            f"Pipeline exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # project_a must have a bundle
        req_a = _requirements_dir(root, "project_a")
        bundles_a = list(req_a.glob("*-bugfix-bundle-*.md")) if req_a.exists() else []
        assert bundles_a, (
            f"Expected project_a to produce a bundle.\n"
            f"req_a contents: {list(req_a.iterdir()) if req_a.exists() else '(missing)'}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # project_b must have no files created
        req_b = _requirements_dir(root, "project_b")
        files_in_b = list(req_b.iterdir()) if req_b.exists() else []
        assert not files_in_b, (
            f"Expected project_b/requirements/ to be empty after project_a pipeline run.\n"
            f"Found in project_b/requirements/: {files_in_b}"
        )

    def test_release_state_mutations_are_scoped_per_project(
        self, two_project_root: pathlib.Path
    ) -> None:
        """Setting an Active RC in project_a does not affect project_b's release state.

        Each project has its own release-state.md.  Writing an Active RC to
        project_a must not alter project_b's release-state.md.
        """
        root = two_project_root

        # Record project_b's initial release state content.
        release_b_before = (
            root / "projects" / "project_b" / "release-state.md"
        ).read_text(encoding="utf-8")

        # Mutate project_a's release state.
        _set_active_rc(root, "project_a", "v0.1.0")

        # project_b's release state must be unchanged.
        release_b_after = (
            root / "projects" / "project_b" / "release-state.md"
        ).read_text(encoding="utf-8")

        assert release_b_after == release_b_before, (
            "Setting Active RC in project_a must not alter project_b's release-state.md.\n"
            f"Before:\n{release_b_before}\nAfter:\n{release_b_after}"
        )

    def test_each_project_maintains_independent_version_namespace(
        self, two_project_root: pathlib.Path
    ) -> None:
        """Both projects bundle bugs independently, each into its own requirements/.

        Running discovery for project_a and project_b in sequence produces
        a bundle under each project's own requirements/ directory.  The bundles
        are independent — each project has its own versioning namespace starting
        from v0.0.1 (since last_released defaults to v0.0.0 for both).
        """
        root = two_project_root
        _add_bug_file(root, "project_a", bug_id="BUG-0071", slug="namespace-a")
        _add_bug_file(root, "project_b", bug_id="BUG-0072", slug="namespace-b")

        result_a = _run_discovery(root, "project_a")
        result_b = _run_discovery(root, "project_b")

        assert result_a.returncode == 0, f"project_a pipeline failed: {result_a.stderr}"
        assert result_b.returncode == 0, f"project_b pipeline failed: {result_b.stderr}"

        req_a = list(_requirements_dir(root, "project_a").glob("*-bugfix-bundle-*.md"))
        req_b = list(_requirements_dir(root, "project_b").glob("*-bugfix-bundle-*.md"))

        assert req_a, "Expected project_a to produce a bundle"
        assert req_b, "Expected project_b to produce a bundle"

        # The bundle paths must be under different project directories.
        assert "project_a" in str(req_a[0]), (
            f"Expected project_a bundle under project_a dir, got: {req_a[0]}"
        )
        assert "project_b" in str(req_b[0]), (
            f"Expected project_b bundle under project_b dir, got: {req_b[0]}"
        )

    def test_halt_in_project_a_does_not_affect_project_b_bundling(
        self, two_project_root: pathlib.Path
    ) -> None:
        """Halting project_a leaves project_b's pipeline operational.

        A per-project HALT file under project_a/HALT halts only project_a.
        project_b can still bundle bugs on the same discovery iteration.
        """
        root = two_project_root

        # Halt project_a.
        (root / "projects" / "project_a" / "HALT").write_text(
            "halted for isolation test\n", encoding="utf-8"
        )
        _add_bug_file(root, "project_b", bug_id="BUG-0073", slug="b-unaffected-by-a-halt")

        result_b = _run_discovery(root, "project_b")

        assert result_b.returncode == 0
        req_b = _requirements_dir(root, "project_b")
        bundles_b = list(req_b.glob("*-bugfix-bundle-*.md")) if req_b.exists() else []
        assert bundles_b, (
            f"Expected project_b to bundle bugs despite project_a being halted.\n"
            f"req_b contents: {list(req_b.iterdir()) if req_b.exists() else '(missing)'}\n"
            f"stdout: {result_b.stdout}\nstderr: {result_b.stderr}"
        )


# ---------------------------------------------------------------------------
# Tests: Per-Repo Lock
# ---------------------------------------------------------------------------


class TestPerRepoLockContention:
    """Per-repo advisory flock prevents concurrent agent runs on the same project.

    The wake script acquires $KANBAN_ROOT/locks/repo-wake-<project_id>.lock
    with flock before processing a project.  If the lock is held (another
    agent is running), the second agent skips the project rather than
    creating race conditions.  The OVERWATCH protocol also checks the per-repo
    lock before applying state corrections.

    These tests exercise the real flock mechanism: one process holds the lock
    while another attempts acquisition, and we assert the behavioral outcome
    (contention detected, work skipped or operation aborted).
    """

    def test_lock_file_is_created_in_locks_directory(
        self, two_project_root: pathlib.Path
    ) -> None:
        """The kanban root's locks/ directory exists and is created by the fixture.

        The per-repo lock mechanism requires a locks/ directory under the
        kanban root.  The two_project fixture creates it.  This is the
        pre-condition for all lock-based tests.
        """
        root = two_project_root
        locks_dir = root / "locks"
        assert locks_dir.is_dir(), (
            f"Expected locks/ directory at {locks_dir} to be created by two_project fixture."
        )

    def test_second_flock_acquisition_is_rejected_while_first_holds(
        self, two_project_root: pathlib.Path
    ) -> None:
        """A second exclusive flock attempt on a held lock file returns non-zero.

        The real behavior: flock -n (non-blocking) on a file descriptor that
        another process already holds returns exit code 1.  This test holds the
        lock in a background subprocess, then verifies a second non-blocking
        acquisition attempt fails.  The behavioral guarantee is that two agents
        cannot run concurrently for the same project.
        """
        root = two_project_root
        lock_file = root / "locks" / "repo-wake-project_a.lock"
        lock_file.touch()

        # Script: hold the lock in the background, wait for a signal, then release.
        # The holder uses a named pipe to coordinate with the tester.
        holder_script = textwrap.dedent(f"""\
            #!/usr/bin/env bash
            # Hold the lock exclusively, signal readiness, then wait.
            LOCK_FILE="{lock_file}"
            READY_FILE="{root}/locks/holder-ready"
            DONE_FILE="{root}/locks/holder-done"

            exec 9>"$LOCK_FILE"
            flock -x 9

            # Signal that the lock is held.
            touch "$READY_FILE"

            # Wait until the tester tells us to release.
            while [[ ! -f "$DONE_FILE" ]]; do sleep 0.05; done

            # Release by closing the fd.
            exec 9>&-
        """)

        holder_result = subprocess.Popen(
            ["bash", "-c", holder_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            # Wait until the holder has acquired the lock.
            ready_file = root / "locks" / "holder-ready"
            import time
            deadline = time.time() + 5.0
            while not ready_file.exists() and time.time() < deadline:
                time.sleep(0.05)

            assert ready_file.exists(), (
                "Holder subprocess did not signal readiness within 5 seconds."
            )

            # Now attempt a non-blocking exclusive flock on the same file.
            # flock -n exits 1 when it cannot acquire the lock.
            contender_script = textwrap.dedent(f"""\
                #!/usr/bin/env bash
                LOCK_FILE="{lock_file}"
                exec 9>"$LOCK_FILE"
                if flock -n 9; then
                    echo "ACQUIRED"
                    exec 9>&-
                else
                    echo "CONTENDED"
                    exec 9>&-
                fi
            """)

            contender = subprocess.run(
                ["bash", "-c", contender_script],
                capture_output=True,
                text=True,
                timeout=5,
            )

            assert "CONTENDED" in contender.stdout, (
                f"Expected 'CONTENDED' when second process attempts non-blocking flock "
                f"on a held lock.\nstdout: {contender.stdout}\nstderr: {contender.stderr}"
            )
            assert "ACQUIRED" not in contender.stdout, (
                "Second flock acquisition must not succeed while first process holds the lock."
            )

        finally:
            # Signal holder to release.
            (root / "locks" / "holder-done").touch()
            holder_result.wait(timeout=5)

    def test_lock_is_released_and_reacquirable_after_holder_exits(
        self, two_project_root: pathlib.Path
    ) -> None:
        """After the lock-holding process exits, the lock can be re-acquired.

        Advisory flocks are released when all file descriptors to the lock
        file are closed.  After the holder exits, a subsequent flock attempt
        must succeed.  This verifies the cleanup semantics that prevent
        permanent lock seizure after an agent crash.
        """
        root = two_project_root
        lock_file = root / "locks" / "repo-wake-project_a.lock"
        lock_file.touch()

        # First: hold and immediately release.
        hold_then_release = textwrap.dedent(f"""\
            #!/usr/bin/env bash
            exec 9>"{lock_file}"
            flock -x 9
            # Release immediately by closing the fd.
            exec 9>&-
        """)
        subprocess.run(
            ["bash", "-c", hold_then_release],
            capture_output=True,
            timeout=5,
        )

        # Second: acquire after holder released — must succeed.
        acquire_after_release = textwrap.dedent(f"""\
            #!/usr/bin/env bash
            exec 9>"{lock_file}"
            if flock -n 9; then
                echo "ACQUIRED"
                exec 9>&-
            else
                echo "CONTENDED"
                exec 9>&-
            fi
        """)
        result = subprocess.run(
            ["bash", "-c", acquire_after_release],
            capture_output=True,
            text=True,
            timeout=5,
        )

        assert "ACQUIRED" in result.stdout, (
            f"Expected lock to be re-acquirable after previous holder exited.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_overwatch_protocol_declines_when_repo_lock_is_held(
        self, two_project_root: pathlib.Path
    ) -> None:
        """overwatch_halt_first_fix returns sentinel 4 when the per-repo lock is held.

        The OVERWATCH protocol checks the per-repo flock before applying state
        corrections.  When the lock is held (another agent is running), the
        protocol declines to act and returns sentinel 4, preventing OVERWATCH
        from modifying state while an agent is mid-flight.

        This test holds the `repo-wake-pgai-kanban.lock` file and verifies
        that the overwatch_protocol script emits the sentinel-4 message.
        """
        root = two_project_root

        # Create the specific lock file the overwatch_protocol checks.
        lock_file = root / "locks" / "repo-wake-pgai-kanban.lock"
        lock_file.touch()

        # Hold the lock in a background process.
        holder_script = textwrap.dedent(f"""\
            #!/usr/bin/env bash
            READY_FILE="{root}/locks/ow-holder-ready"
            DONE_FILE="{root}/locks/ow-holder-done"
            exec 9>"{lock_file}"
            flock -x 9
            touch "$READY_FILE"
            while [[ ! -f "$DONE_FILE" ]]; do sleep 0.05; done
            exec 9>&-
        """)

        holder = subprocess.Popen(
            ["bash", "-c", holder_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            import time
            ready_file = root / "locks" / "ow-holder-ready"
            deadline = time.time() + 5.0
            while not ready_file.exists() and time.time() < deadline:
                time.sleep(0.05)

            assert ready_file.exists(), (
                "OVERWATCH holder subprocess did not signal readiness within 5 seconds."
            )

            # Run the overwatch_protocol's overwatch_halt_first_fix function.
            # When the per-repo lock is held, it must emit the sentinel-4 message
            # and return exit code 4.
            protocol_script = textwrap.dedent(f"""\
                #!/usr/bin/env bash
                set +e
                source "{_SCRIPTS_LIB}/ini_parser.sh"
                source "{_SCRIPTS_LIB}/temp.sh"
                source "{_SCRIPTS_LIB}/project_paths.sh"
                source "{_SCRIPTS_LIB}/overwatch_lib.sh"
                source "{_SCRIPTS_LIB}/overwatch_protocol.sh"
                KANBAN_ROOT="{root}"
                OVERWATCH_PROJECT="pgai-agent-kanban"
                export KANBAN_ROOT OVERWATCH_PROJECT

                # Provide a no-op function for the halt_first_fix wrapper to call.
                _noop_fn() {{ echo "noop ran"; }}

                overwatch_halt_first_fix _noop_fn
                echo "EXIT_CODE=$?"
            """)

            protocol_result = subprocess.run(
                ["bash", "-c", protocol_script],
                capture_output=True,
                text=True,
                timeout=10,
                env={
                    **os.environ,
                    "KANBAN_ROOT": str(root),
                    "TEAM_ROOT": str(root),
                    "OVERWATCH_PROJECT": "pgai-agent-kanban",
                },
            )

            # Sentinel 4 means "per-repo flock contended; declining to act."
            combined = protocol_result.stdout + protocol_result.stderr
            assert "EXIT_CODE=4" in protocol_result.stdout or "flock contended" in combined, (
                f"Expected overwatch_protocol to return sentinel 4 (per-repo flock contended).\n"
                f"stdout: {protocol_result.stdout}\nstderr: {protocol_result.stderr}"
            )

        finally:
            (root / "locks" / "ow-holder-done").touch()
            holder.wait(timeout=5)


# ---------------------------------------------------------------------------
# Tests: Active-RC Gate (PM Decomposition Blocked)
# ---------------------------------------------------------------------------


class TestActiveRCGateBlocksPMDecomposition:
    """A new PM decomposition is blocked while an RC is active in the same project.

    discovery_step_requirements includes a belt-and-suspenders Active-RC guard:
    even when called directly (bypassing pipeline orchestration), it refuses to
    queue PM when Active RC is set.  The single-RC-in-flight invariant prevents
    RC N+1's PM from waking while RC N is still open.
    """

    def test_pm_is_not_queued_when_active_rc_is_set(
        self, two_project_root: pathlib.Path
    ) -> None:
        """discovery_step_requirements does not queue PM when an RC is in flight.

        When Active RC is set in release-state.md, the requirements pipeline
        step refuses to queue a new PM task.  The requirements file remains in
        'open' state (not updated to 'running'), and the PM backlog does not
        receive a new entry.
        """
        root = two_project_root

        # Place a requirements bundle ready for PM pickup.
        req_file = _add_requirements_file(
            root, "project_a", version="v0.0.2", slug="blocked-by-active-rc"
        )

        # Set an active RC — this triggers the gate.
        _set_active_rc(root, "project_a", "v0.1.0")

        # Run the full pipeline.  With an Active RC set, Step 3 must not fire.
        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, (
            f"Pipeline exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # The requirements file must still be in 'open' state (PM not queued).
        req_text = req_file.read_text(encoding="utf-8")
        assert "running" not in req_text.lower(), (
            "Expected requirements file to remain in 'open' state when Active RC is set "
            "(PM must not be queued while an RC is in flight).\n"
            f"Requirements file content:\n{req_text}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # PM backlog must not have a new pending entry.
        # The fixture initialises pm_backlog.md with a template comment containing
        # '- [ ] TASK-ID'; a real pending entry looks like '- [ ] PM-<id>'.  We
        # check for a PM-prefixed task entry rather than the bare marker so we
        # do not false-positive on the template comment line.
        pm_backlog = (
            root / "projects" / "project_a" / "tasks" / "queues" / "pm_backlog.md"
        )
        if pm_backlog.exists():
            backlog_text = pm_backlog.read_text(encoding="utf-8")
            # A real queued-and-pending PM task looks like '- [ ] PM-<date>-...'
            import re as _re
            pending_tasks = _re.findall(r"^- \[ \] PM-", backlog_text, _re.MULTILINE)
            assert not pending_tasks, (
                f"Expected PM backlog to have no pending PM task entry while Active RC is set.\n"
                f"Pending entries found: {pending_tasks}\n"
                f"PM backlog contents:\n{backlog_text}\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )

    def test_pm_queue_is_unblocked_after_active_rc_clears(
        self, two_project_root: pathlib.Path
    ) -> None:
        """After the Active RC clears to 'none', PM can be queued for the next RC.

        This verifies the end-to-end gate cycle: RC N ships → Active RC resets
        to 'none' → next discovery iteration queues PM for RC N+1.  A stub
        pm-agent.sh is installed so the subprocess can invoke it without the
        live install.
        """
        root = two_project_root

        # Install a stub pm-agent.sh under the temp tree's scripts/ directory.
        scripts_dir = root / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)

        task_id = "PM-20260628-001-decompose-v0-0-2-next-rc"
        pm_tasks_dir = root / "projects" / "project_a" / "tasks"
        pm_tasks_dir.mkdir(parents=True, exist_ok=True)

        stub_pm = scripts_dir / "pm-agent.sh"
        stub_pm.write_text(
            textwrap.dedent(f"""\
                #!/usr/bin/env bash
                # Stub pm-agent.sh: create a minimal task folder and emit Folder line.
                TASK_ID="{task_id}"
                TASK_DIR="{pm_tasks_dir}/${{TASK_ID}}"
                mkdir -p "$TASK_DIR"
                printf '# %s\\n## State\\nBACKLOG\\n' "$TASK_ID" > "$TASK_DIR/status.md"
                PM_BACKLOG="${{KANBAN_ROOT}}/projects/${{PGAI_PROJECT_NAME:-project_a}}/tasks/queues/pm_backlog.md"
                echo "- [ ] $TASK_ID" >> "$PM_BACKLOG"
                echo "  Folder   : $TASK_DIR"
            """),
            encoding="utf-8",
        )
        stub_pm.chmod(0o755)

        req_file = _add_requirements_file(
            root, "project_a", version="v0.0.2", slug="unblocked-after-rc"
        )

        # Phase 1: RC in flight — PM must NOT be queued.
        _set_active_rc(root, "project_a", "v0.1.0")
        _run_discovery(root, "project_a")
        req_text_while_active = req_file.read_text(encoding="utf-8")
        assert "running" not in req_text_while_active.lower(), (
            "PM must not be queued while Active RC is set."
        )

        # Phase 2: RC ships — Active RC clears to 'none'.
        (root / "projects" / "project_a" / "release-state.md").write_text(
            "# Release State\n\n## Active RC\nnone\n\n"
            "## RC Opened At\nnone\n\n## RC Opened By Task\nnone\n",
            encoding="utf-8",
        )

        result_after = _run_discovery(root, "project_a")

        assert result_after.returncode == 0, (
            f"Pipeline exited non-zero after Active RC cleared.\n"
            f"stdout: {result_after.stdout}\nstderr: {result_after.stderr}"
        )

        # The requirements file must now be in 'running' state (PM queued).
        req_text_after = req_file.read_text(encoding="utf-8")
        assert "running" in req_text_after.lower(), (
            "Expected requirements file ## Status to be 'running' after Active RC clears "
            "(PM should be queued for the next RC).\n"
            f"Requirements file content:\n{req_text_after}\n"
            f"stdout: {result_after.stdout}\nstderr: {result_after.stderr}"
        )

    def test_active_rc_in_project_a_does_not_block_project_b_bundling(
        self, two_project_root: pathlib.Path
    ) -> None:
        """An Active RC in project_a does not prevent project_b from bundling bugs.

        The Active-RC gate is per-project.  If project_a has an RC in flight,
        project_b can still bundle bugs in the same cron iteration.
        """
        root = two_project_root

        # Set project_a to have an Active RC.
        _set_active_rc(root, "project_a", "v0.1.0")

        # Add a bug to project_b.
        _add_bug_file(root, "project_b", bug_id="BUG-0080", slug="b-unblocked-by-a-rc")

        result_b = _run_discovery(root, "project_b")

        assert result_b.returncode == 0, (
            f"project_b pipeline exited non-zero.\n"
            f"stdout: {result_b.stdout}\nstderr: {result_b.stderr}"
        )

        req_b = _requirements_dir(root, "project_b")
        bundles_b = list(req_b.glob("*-bugfix-bundle-*.md")) if req_b.exists() else []
        assert bundles_b, (
            f"Expected project_b to bundle bugs even when project_a has an Active RC.\n"
            f"req_b contents: {list(req_b.iterdir()) if req_b.exists() else '(missing)'}\n"
            f"stdout: {result_b.stdout}\nstderr: {result_b.stderr}"
        )


# ---------------------------------------------------------------------------
# Tests: Halt Gates (Global vs Per-Project)
# ---------------------------------------------------------------------------


class TestHaltGates:
    """Global HALT blocks all projects; per-project HALT blocks only the named project.

    The discovery pipeline has two halt guards:
    - Per-project HALT: projects/<name>/HALT — halts only that project
    - Global HALT: $KANBAN_ROOT/HALT (TEAM_ROOT/HALT) — halts all projects

    These are independent mechanisms with different scopes.
    """

    def test_global_halt_blocks_all_projects(
        self, two_project_root: pathlib.Path
    ) -> None:
        """A HALT file at the kanban root blocks discovery for every project.

        Both project_a and project_b have bugs ready to bundle.  With a global
        HALT file present, neither project produces a bundle.  Both pipelines
        exit with DISCOVERY_STATUS=blocked.
        """
        root = two_project_root
        _add_bug_file(root, "project_a", bug_id="BUG-0090", slug="global-halt-a")
        _add_bug_file(root, "project_b", bug_id="BUG-0091", slug="global-halt-b")

        (root / "HALT").write_text("global halt for cross-cutting test\n", encoding="utf-8")

        for project_name in ["project_a", "project_b"]:
            result = _run_discovery(root, project_name)

            assert result.returncode == 0, (
                f"Pipeline exited non-zero for {project_name} under global HALT.\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )
            assert "DISCOVERY_STATUS=blocked" in result.stdout, (
                f"Expected DISCOVERY_STATUS=blocked for {project_name} under global HALT.\n"
                f"stdout: {result.stdout}"
            )

            req_dir = _requirements_dir(root, project_name)
            bundles = list(req_dir.glob("*-bugfix-bundle-*.md")) if req_dir.exists() else []
            assert not bundles, (
                f"Expected no bundle for {project_name} under global HALT, but found: {bundles}"
            )

    def test_per_project_halt_blocks_only_the_named_project(
        self, two_project_root: pathlib.Path
    ) -> None:
        """A HALT file under projects/<name>/HALT blocks only that project.

        project_a is halted; project_b has a bug and must bundle normally.
        project_a's pipeline exits with DISCOVERY_STATUS=blocked; project_b's
        exits with DISCOVERY_STATUS=produced_work.
        """
        root = two_project_root

        # Halt project_a only.
        (root / "projects" / "project_a" / "HALT").write_text(
            "per-project halt for cross-cutting test\n", encoding="utf-8"
        )
        _add_bug_file(root, "project_a", bug_id="BUG-0092", slug="per-halt-a-blocked")
        _add_bug_file(root, "project_b", bug_id="BUG-0093", slug="per-halt-b-unaffected")

        # project_a must be blocked.
        result_a = _run_discovery(root, "project_a")
        assert result_a.returncode == 0
        assert "DISCOVERY_STATUS=blocked" in result_a.stdout, (
            f"Expected DISCOVERY_STATUS=blocked for halted project_a.\n"
            f"stdout: {result_a.stdout}"
        )
        req_a = _requirements_dir(root, "project_a")
        assert not list(req_a.glob("*-bugfix-bundle-*.md")), (
            "Expected no bundle for halted project_a."
        )

        # project_b must proceed normally.
        result_b = _run_discovery(root, "project_b")
        assert result_b.returncode == 0, (
            f"project_b pipeline exited non-zero.\n"
            f"stdout: {result_b.stdout}\nstderr: {result_b.stderr}"
        )
        req_b = _requirements_dir(root, "project_b")
        bundles_b = list(req_b.glob("*-bugfix-bundle-*.md")) if req_b.exists() else []
        assert bundles_b, (
            f"Expected project_b to bundle bugs when only project_a is halted.\n"
            f"stdout: {result_b.stdout}\nstderr: {result_b.stderr}"
        )

    def test_removing_global_halt_file_restores_all_project_pipelines(
        self, two_project_root: pathlib.Path
    ) -> None:
        """After the global HALT file is removed, discovery resumes for all projects.

        This verifies the halt-and-resume cycle: global HALT placed → discovery
        blocked → HALT removed → discovery produces work again.
        """
        root = two_project_root
        _add_bug_file(root, "project_a", bug_id="BUG-0094", slug="resume-after-global-halt")

        halt_file = root / "HALT"
        halt_file.write_text("temporary halt\n", encoding="utf-8")

        # Verify halt blocks.
        result_blocked = _run_discovery(root, "project_a")
        assert "DISCOVERY_STATUS=blocked" in result_blocked.stdout, (
            f"Expected blocked status while HALT is present.\nstdout: {result_blocked.stdout}"
        )

        # Remove the global HALT file.
        halt_file.unlink()

        # Discovery must now produce work.
        result_resumed = _run_discovery(root, "project_a")
        assert result_resumed.returncode == 0
        req_a = _requirements_dir(root, "project_a")
        bundles = list(req_a.glob("*-bugfix-bundle-*.md")) if req_a.exists() else []
        assert bundles, (
            f"Expected project_a to bundle bugs after global HALT is removed.\n"
            f"stdout: {result_resumed.stdout}\nstderr: {result_resumed.stderr}"
        )

    def test_removing_per_project_halt_restores_that_project_pipeline(
        self, two_project_root: pathlib.Path
    ) -> None:
        """After a per-project HALT file is removed, that project's pipeline resumes.

        Halt-and-resume: place per-project HALT → blocked → remove HALT →
        pipeline produces work again.  The other project is unaffected throughout.
        """
        root = two_project_root
        _add_bug_file(root, "project_a", bug_id="BUG-0095", slug="resume-after-per-halt")

        halt_file = root / "projects" / "project_a" / "HALT"
        halt_file.write_text("per-project halt\n", encoding="utf-8")

        # Verify halt blocks.
        result_blocked = _run_discovery(root, "project_a")
        assert "DISCOVERY_STATUS=blocked" in result_blocked.stdout, (
            f"Expected blocked status while per-project HALT is present.\n"
            f"stdout: {result_blocked.stdout}"
        )

        # Remove the per-project HALT.
        halt_file.unlink()

        # Discovery for project_a must now produce work.
        result_resumed = _run_discovery(root, "project_a")
        assert result_resumed.returncode == 0
        req_a = _requirements_dir(root, "project_a")
        bundles = list(req_a.glob("*-bugfix-bundle-*.md")) if req_a.exists() else []
        assert bundles, (
            f"Expected project_a to bundle bugs after per-project HALT is removed.\n"
            f"stdout: {result_resumed.stdout}\nstderr: {result_resumed.stderr}"
        )

    def test_global_halt_does_not_affect_project_on_disk_state(
        self, two_project_root: pathlib.Path
    ) -> None:
        """When the global HALT file blocks discovery, no on-disk state is mutated.

        The pipeline exits early under HALT — no bundle is written, no backlog
        markers are updated, no bug file status fields are modified.  On-disk
        state must be identical before and after the blocked pipeline run.
        """
        root = two_project_root
        bug_file = _add_bug_file(
            root, "project_a", bug_id="BUG-0096", slug="halt-no-mutation"
        )

        # Capture the bug file's initial content.
        initial_content = bug_file.read_text(encoding="utf-8")
        initial_req_files = (
            list(_requirements_dir(root, "project_a").iterdir())
            if _requirements_dir(root, "project_a").exists()
            else []
        )

        (root / "HALT").write_text("no-mutation halt\n", encoding="utf-8")

        _run_discovery(root, "project_a")

        # Bug file content must be unchanged.
        assert bug_file.read_text(encoding="utf-8") == initial_content, (
            "Bug file content must not be mutated when the pipeline is halted by global HALT."
        )

        # Requirements directory must remain in the same state as before.
        post_req_files = (
            list(_requirements_dir(root, "project_a").iterdir())
            if _requirements_dir(root, "project_a").exists()
            else []
        )
        assert post_req_files == initial_req_files, (
            f"Requirements directory state changed under global HALT.\n"
            f"Before: {initial_req_files}\nAfter: {post_req_files}"
        )
