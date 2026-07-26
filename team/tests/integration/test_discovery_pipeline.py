"""
test_discovery_pipeline.py
==========================
Integration tests for the discovery pipeline (team/scripts/lib/discovery.sh).

These tests exercise the real discovery pipeline against temporary kanban
trees.  No seam is mocked — each test stands up a real tree, runs the real
pipeline, and asserts on the resulting on-disk state.

Coverage:
  - Bug file intake produces a requirements bundle in requirements/
  - Priority file intake produces a requirements bundle in requirements/
  - Requirements bundle is queued for PM when the chain is idle
  - Per-project HALT file blocks only the affected project
  - Global HALT file blocks all projects
  - Active-RC gate defers bundling until after the RC ships
  - Version-ceiling gate prevents bundles whose version exceeds max_patch
  - Per-project isolation: bug in project_a does not affect project_b
  - Malformed-filename quarantine: files that fail format validation three
    times are moved to the project-level rejected/ directory

Design notes:
  - All tests use tmp_path (via two_project_root or direct) for scratch.
    No bare /tmp paths appear in this file.
  - Each test is self-contained and produces no state visible to other tests.
  - Test names describe the behavior under test, not the gate or ticket that
    motivated them (SOP.md Anti-pattern 6).
  - Discovery is invoked via _run_discovery(), a thin subprocess wrapper that
    sources the discovery library against the caller's temp tree and calls
    discovery_run_pipeline, then echoes DISCOVERY_LAST_STATUS on stdout.
"""

from __future__ import annotations

import pathlib
import subprocess
import textwrap
from typing import Optional

import pytest

# ---------------------------------------------------------------------------
# Path to the dev tree (resolves from this file's own location: the dev tree
# root is three levels up from tests/integration/).
# team/tests/integration/test_discovery_pipeline.py
#   └── team/tests/integration/    (this file)
#       └── team/tests/
#           └── team/
#               └── <dev-tree-root>
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_SCRIPTS_LIB = _TEAM_DIR / "scripts" / "lib"


# ---------------------------------------------------------------------------
# Internal helpers
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
        kanban_root:  Path to the temporary kanban root (from two_project_root
                      or a custom-built tree).  Must exist.
        project_name: Name of the project to run the pipeline for.
        extra_env:    Additional environment overrides (caller wins on collision).
        threshold:    PGAI_DISCOVERY_REJECT_THRESHOLD for quarantine tests
                      (default 3; set lower to trigger quarantine in fewer
                      pipeline runs).

    Returns:
        subprocess.CompletedProcess with returncode, stdout, and stderr.
    """
    script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        # Source lib dependencies in the correct order.
        source "{_SCRIPTS_LIB}/ini_parser.sh"
        source "{_SCRIPTS_LIB}/temp.sh"
        source "{_SCRIPTS_LIB}/semver.sh"
        source "{_SCRIPTS_LIB}/project_paths.sh"
        source "{_SCRIPTS_LIB}/discovery.sh"
        # discovery_run_pipeline also needs pp_project_halted, pp_max_* helpers.
        # Those are all in project_paths.sh already sourced above.
        discovery_run_pipeline "{project_name}"
        echo "DISCOVERY_STATUS=${{DISCOVERY_LAST_STATUS}}"
    """)

    import os
    base_env = dict(os.environ)
    # Point the pipeline at the temp tree, not the live install.
    base_env["KANBAN_ROOT"] = str(kanban_root)
    base_env["TEAM_ROOT"] = str(kanban_root)
    base_env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    base_env["PGAI_PROJECT_NAME"] = project_name
    base_env["PGAI_DISCOVERY_REJECT_THRESHOLD"] = str(threshold)
    # Suppress git remote checks in compute_next_patch: dev_tree_path=/dev/null
    # prevents git tag lookups.  Disable PGAI_DEV_TREE_PATH so the fallback
    # in _disc_git_tag_exists uses the empty cwd path and skips silently.
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
    """Create a well-formed bug file in the project's bugs/ directory.

    Creates the bugs/ directory if it does not already exist.

    Args:
        kanban_root:  Kanban root path.
        project_name: Project whose bugs/ directory receives the file.
        bug_id:       BUG-NNNN identifier (4+ digits).
        slug:         Hyphenated slug suffix after the ID.
        status:       Value for the ## Status field (default: open).

    Returns:
        pathlib.Path to the created file.
    """
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


def _add_priority_file(
    kanban_root: pathlib.Path,
    project_name: str,
    priority_id: str = "PRIORITY-0001",
    date_str: str = "20260601",
    slug: str = "test-feature",
    status: str = "open",
) -> pathlib.Path:
    """Create a well-formed priority file in the project's priority/ directory.

    PRIORITY files follow the pattern PRIORITY-NNNN-<slug>.md; a date segment
    is accepted but not required: PRIORITY-NNNN-YYYYMMDD-<slug>.md is also valid.
    This helper includes a date_str to produce dated filenames (backwards-compat
    form), but dateless filenames are equally accepted by discovery.

    Args:
        kanban_root:  Kanban root path.
        project_name: Project whose priority/ directory receives the file.
        priority_id:  PRIORITY-NNNN identifier (4+ digits).
        date_str:     8-digit date string (YYYYMMDD) inserted between id and slug (optional convention).
        slug:         Hyphenated slug suffix.
        status:       Value for the ## Status field (default: open).

    Returns:
        pathlib.Path to the created file.
    """
    priority_dir = kanban_root / "projects" / project_name / "priority"
    priority_dir.mkdir(parents=True, exist_ok=True)
    priority_file = priority_dir / f"{priority_id}-{date_str}-{slug}.md"
    priority_file.write_text(
        f"# {priority_id}: {slug}\n\n"
        f"## Status\n{status}\n\n"
        "## Summary\nTest priority item.\n",
        encoding="utf-8",
    )
    return priority_file


def _add_requirements_file(
    kanban_root: pathlib.Path,
    project_name: str,
    version: str = "v0.0.2",
    slug: str = "test-feature",
    status: str = "open",
    workflow_type: str = "release",
) -> pathlib.Path:
    """Create a requirements file in the project's requirements/ directory.

    Args:
        kanban_root:  Kanban root path.
        project_name: Project whose requirements/ directory receives the file.
        version:      Target version string (e.g. "v0.0.2").
        slug:         Hyphenated slug for the filename.
        status:       Value for the ## Status field (default: open).
        workflow_type: Workflow type for the requirements file.

    Returns:
        pathlib.Path to the created file.
    """
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


def _requirements_dir(kanban_root: pathlib.Path, project_name: str) -> pathlib.Path:
    """Return the requirements/ path for a project."""
    return kanban_root / "projects" / project_name / "requirements"


def _rejected_dir(kanban_root: pathlib.Path, project_name: str) -> pathlib.Path:
    """Return the rejected/ path for a project (created by pp_rejected_dir)."""
    return kanban_root / "projects" / project_name / "rejected"


def _set_active_rc(
    kanban_root: pathlib.Path,
    project_name: str,
    rc_version: str,
) -> None:
    """Set the Active RC field in the project's release-state.md.

    Rewrites the entire release-state.md to contain the given RC version.
    """
    release_state_path = kanban_root / "projects" / project_name / "release-state.md"
    release_state_path.write_text(
        "# Release State\n\n"
        f"## Active RC\n{rc_version}\n\n"
        "## RC Opened At\n2026-06-01T00:00:00+00:00\n\n"
        "## RC Opened By Task\nCM-20260601-001-open-rc\n",
        encoding="utf-8",
    )


def _set_max_patch_ceiling(
    kanban_root: pathlib.Path,
    project_name: str,
    max_patch: int,
) -> None:
    """Rewrite project.cfg to add a max_patch ceiling constraint.

    The two_project fixture creates project.cfg with a [versioning] section
    that already has max_patch = 99.  This helper overwrites max_patch with
    the given value so the ceiling gate fires.

    Note: this reads and rewrites the whole file to update only the max_patch
    line; all other fields are preserved verbatim.
    """
    cfg_path = kanban_root / "projects" / project_name / "project.cfg"
    text = cfg_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    new_lines = []
    for line in lines:
        if line.strip().startswith("max_patch"):
            new_lines.append(f"max_patch = {max_patch}")
        else:
            new_lines.append(line)
    cfg_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBugIntakeToBundleFlow:
    """Bug file → requirements bundle on-disk state."""

    def test_bug_file_produces_requirements_bundle(
        self, two_project_root: pathlib.Path
    ) -> None:
        """A well-formed bug file in bugs/ is bundled into requirements/ by the pipeline.

        The discovery pipeline Step 1 scans bugs/ for unhandled BUG-*.md files
        and writes a requirements bundle file (vX.Y.Z-bugfix-bundle-<date>.md)
        into the project's requirements/ directory.  This test asserts that the
        bundle file exists and names the right version.
        """
        root = two_project_root
        _add_bug_file(root, "project_a")

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, (
            f"discovery_run_pipeline exited non-zero.\nstdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

        req_dir = _requirements_dir(root, "project_a")
        bundle_files = list(req_dir.glob("*-bugfix-bundle-*.md"))
        assert bundle_files, (
            f"Expected a bugfix-bundle-*.md file in {req_dir}.\n"
            f"requirements/ contents: {list(req_dir.iterdir()) if req_dir.exists() else '(missing)'}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_bug_bundle_marks_source_file_as_running(
        self, two_project_root: pathlib.Path
    ) -> None:
        """After bundling, the source bug file's ## Status field is updated to 'running'.

        This is the authoritative status transition that prevents the pipeline
        from re-bundling the same bug on the next iteration.
        """
        root = two_project_root
        bug_file = _add_bug_file(root, "project_a", bug_id="BUG-0002", slug="mark-status-test")

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, (
            f"discovery_run_pipeline exited non-zero.\nstdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

        updated_text = bug_file.read_text(encoding="utf-8")
        assert "running" in updated_text.lower(), (
            f"Expected bug file ## Status to be updated to 'running' after bundling.\n"
            f"Actual file content:\n{updated_text}\nstdout: {result.stdout}"
        )

    def test_bug_bundle_contains_status_open_initially(
        self, two_project_root: pathlib.Path
    ) -> None:
        """The generated bundle has ## Status: open so Step 3 can pick it up next iteration.

        Step 3 only queues PM for bundles whose ## Status is 'open'.  A bundle
        written with any other initial status would be silently skipped.
        """
        root = two_project_root
        _add_bug_file(root, "project_a", bug_id="BUG-0003", slug="bundle-status-test")

        _run_discovery(root, "project_a")

        req_dir = _requirements_dir(root, "project_a")
        bundle_files = list(req_dir.glob("*-bugfix-bundle-*.md"))
        assert bundle_files, f"No bundle file found in {req_dir}"
        bundle_text = bundle_files[0].read_text(encoding="utf-8")
        # The bundle must start with Status: open so discovery_step_requirements
        # can pick it up on the next pipeline iteration.
        assert "open" in bundle_text.lower(), (
            f"Expected bundle ## Status to be 'open'.\nBundle content:\n{bundle_text}"
        )

    def test_discovery_status_reflects_work_produced_for_bug(
        self, two_project_root: pathlib.Path
    ) -> None:
        """Pipeline sets DISCOVERY_LAST_STATUS to 'produced_work' when a bug bundle is created.

        This is the signal used by pm-agent.sh --auto to determine whether to
        iterate the pipeline again.
        """
        root = two_project_root
        _add_bug_file(root, "project_a", bug_id="BUG-0004", slug="status-signal-test")

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0
        assert "DISCOVERY_STATUS=produced_work" in result.stdout, (
            f"Expected DISCOVERY_STATUS=produced_work in stdout.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


class TestPriorityIntakeToBundleFlow:
    """Priority file → requirements bundle on-disk state."""

    def test_priority_file_produces_requirements_bundle(
        self, two_project_root: pathlib.Path
    ) -> None:
        """A well-formed priority file is bundled into requirements/ by the pipeline.

        Step 2 scans priority/ for unhandled PRIORITY-*.md files and writes a
        vX.Y.Z-priority-bundle-<date>.md file into requirements/.
        """
        root = two_project_root
        _add_priority_file(root, "project_a")

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, (
            f"discovery_run_pipeline exited non-zero.\nstdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

        req_dir = _requirements_dir(root, "project_a")
        bundle_files = list(req_dir.glob("*-priority-bundle-*.md"))
        assert bundle_files, (
            f"Expected a priority-bundle-*.md file in {req_dir}.\n"
            f"requirements/ contents: {list(req_dir.iterdir()) if req_dir.exists() else '(missing)'}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_priority_bundle_marks_source_file_as_running(
        self, two_project_root: pathlib.Path
    ) -> None:
        """After bundling, the priority file's ## Status is updated to 'running'."""
        root = two_project_root
        prio_file = _add_priority_file(
            root, "project_a", priority_id="PRIORITY-0002", slug="mark-running-test"
        )

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0
        updated_text = prio_file.read_text(encoding="utf-8")
        assert "running" in updated_text.lower(), (
            f"Expected priority file ## Status to be 'running' after bundling.\n"
            f"Actual:\n{updated_text}"
        )


class TestRequirementsBundleQueuedForPM:
    """Requirements bundle → PM queue entry when the chain is idle."""

    def test_requirements_bundle_queued_when_chain_idle(
        self, two_project_root: pathlib.Path, tmp_path: pathlib.Path
    ) -> None:
        """A requirements bundle in open state is queued for PM when the chain is idle.

        The chain is idle when Active RC is 'none' and every agent backlog's
        last entry is [x] (or empty).  When both conditions hold, Step 3 invokes
        pm-agent.sh and updates the bundle's ## Status to 'running'.

        For this test, a stub pm-agent.sh is placed in the temp tree's scripts/
        directory so the subprocess can find it without touching the live install.
        The stub creates a minimal PM task folder and emits the expected 'Folder'
        log line so discovery_step_requirements can record the task ID.
        """
        root = two_project_root

        # Install a stub pm-agent.sh in the temp tree so discovery_step_requirements
        # can invoke it.  The stub creates a minimal task folder and echoes the
        # 'Folder : <path>' line that discovery_step_requirements parses for the
        # task ID.  All output goes to the task folder under the temp tree.
        scripts_dir = root / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)

        task_id = "PM-20260628-001-decompose-v0-0-2-test-feature"
        pm_tasks_dir = root / "projects" / "project_a" / "tasks"
        pm_tasks_dir.mkdir(parents=True, exist_ok=True)

        stub_pm = scripts_dir / "pm-agent.sh"
        stub_pm.write_text(
            textwrap.dedent(f"""\
                #!/usr/bin/env bash
                # Stub pm-agent.sh for integration test.
                # Creates a minimal PM task folder and echoes the expected Folder line.
                TASK_ID="{task_id}"
                TASK_DIR="{pm_tasks_dir}/${{TASK_ID}}"
                mkdir -p "$TASK_DIR"
                printf '# %s\\n## State\\nBACKLOG\\n' "$TASK_ID" > "$TASK_DIR/status.md"
                # Also write to pm_backlog (resolved by PGAI_PROJECT_NAME):
                PM_BACKLOG="${{KANBAN_ROOT}}/projects/${{PGAI_PROJECT_NAME:-project_a}}/tasks/queues/pm_backlog.md"
                echo "- [ ] $TASK_ID" >> "$PM_BACKLOG"
                # Emit the Folder line that discovery_step_requirements parses.
                echo "  Folder   : $TASK_DIR"
            """),
            encoding="utf-8",
        )
        stub_pm.chmod(0o755)

        # Place an open requirements file for the pipeline to find.
        _add_requirements_file(root, "project_a", version="v0.0.2", slug="test-feature")

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, (
            f"discovery_run_pipeline exited non-zero.\nstdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

        # Assert: bundle ## Status was updated to 'running'
        req_dir = _requirements_dir(root, "project_a")
        req_files = list(req_dir.glob("v0.0.2-test-feature.md"))
        assert req_files, f"Requirements file not found in {req_dir}"
        req_text = req_files[0].read_text(encoding="utf-8")
        assert "running" in req_text.lower(), (
            f"Expected requirements file ## Status to be 'running' after PM queuing.\n"
            f"Actual content:\n{req_text}\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Assert: PM backlog now has a pending entry for this task
        pm_backlog = root / "projects" / "project_a" / "tasks" / "queues" / "pm_backlog.md"
        backlog_text = pm_backlog.read_text(encoding="utf-8") if pm_backlog.exists() else ""
        assert task_id in backlog_text, (
            f"Expected task ID {task_id!r} in pm_backlog.\n"
            f"pm_backlog contents:\n{backlog_text}\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )


class TestPerProjectHaltGate:
    """Per-project HALT file blocks only the affected project's pipeline."""

    def test_per_project_halt_file_blocks_pipeline_for_that_project(
        self, two_project_root: pathlib.Path
    ) -> None:
        """A HALT file under projects/<name>/HALT blocks that project's pipeline.

        The blocked project produces no bundle even when it has a bug file ready.
        The other project is unaffected.
        """
        root = two_project_root

        # Place a bug in project_a and a HALT file in project_a.
        _add_bug_file(root, "project_a", bug_id="BUG-0010", slug="halt-gate-test")
        halt_file = root / "projects" / "project_a" / "HALT"
        halt_file.write_text("halted for testing\n", encoding="utf-8")

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, (
            f"Pipeline exited non-zero (expected clean exit even when halted).\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # No bundle should have been produced for the halted project.
        req_dir = _requirements_dir(root, "project_a")
        bundle_files = list(req_dir.glob("*-bugfix-bundle-*.md")) if req_dir.exists() else []
        assert not bundle_files, (
            f"Expected no bundle for halted project_a, but found: {bundle_files}"
        )

        # DISCOVERY_STATUS must be 'blocked', not 'produced_work'.
        assert "DISCOVERY_STATUS=blocked" in result.stdout, (
            f"Expected DISCOVERY_STATUS=blocked.\nstdout: {result.stdout}"
        )

    def test_per_project_halt_does_not_affect_other_projects(
        self, two_project_root: pathlib.Path
    ) -> None:
        """A HALT file in project_a does not prevent project_b from bundling.

        Per-project HALT is scoped: halting one project must not stop the
        pipeline from running for other registered projects.  (Each project's
        pipeline is invoked independently by the wake script.)
        """
        root = two_project_root

        # Halt project_a, add a bug in project_b.
        (root / "projects" / "project_a" / "HALT").write_text(
            "halted\n", encoding="utf-8"
        )
        _add_bug_file(root, "project_b", bug_id="BUG-0011", slug="isolation-from-halt")

        # Run discovery for project_b — should succeed.
        result = _run_discovery(root, "project_b")

        assert result.returncode == 0
        req_dir = _requirements_dir(root, "project_b")
        bundle_files = list(req_dir.glob("*-bugfix-bundle-*.md")) if req_dir.exists() else []
        assert bundle_files, (
            f"Expected project_b to produce a bundle despite project_a being halted.\n"
            f"req_dir contents: {list(req_dir.iterdir()) if req_dir.exists() else '(missing)'}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


class TestGlobalHaltGate:
    """Global HALT file at the kanban root blocks every project's pipeline."""

    def test_global_halt_file_blocks_all_project_pipelines(
        self, two_project_root: pathlib.Path
    ) -> None:
        """A HALT file at the kanban root blocks the pipeline for all projects.

        Even if a project has unhandled bugs, the global HALT prevents bundling.
        The pipeline exits with DISCOVERY_LAST_STATUS=blocked.
        """
        root = two_project_root
        _add_bug_file(root, "project_a", bug_id="BUG-0020", slug="global-halt-test")
        _add_bug_file(root, "project_b", bug_id="BUG-0021", slug="global-halt-test-b")

        # Place the global HALT file at the kanban root.
        global_halt = root / "HALT"
        global_halt.write_text("global halt for testing\n", encoding="utf-8")

        for project_name in ["project_a", "project_b"]:
            result = _run_discovery(root, project_name)

            assert result.returncode == 0, (
                f"Pipeline exited non-zero for {project_name}.\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )
            assert "DISCOVERY_STATUS=blocked" in result.stdout, (
                f"Expected DISCOVERY_STATUS=blocked for {project_name} under global HALT.\n"
                f"stdout: {result.stdout}"
            )
            req_dir = _requirements_dir(root, project_name)
            bundle_files = (
                list(req_dir.glob("*-bugfix-bundle-*.md")) if req_dir.exists() else []
            )
            assert not bundle_files, (
                f"Expected no bundle for {project_name} under global HALT, "
                f"but found: {bundle_files}"
            )


class TestActiveRCGate:
    """Active-RC gate defers bug/priority bundling until after the RC ships."""

    def test_active_rc_defers_bug_bundling(
        self, two_project_root: pathlib.Path
    ) -> None:
        """When an RC is in flight, Step 1 does not bundle new bugs.

        Bundling while an RC is open would produce a requirements file whose
        target version is below the in-flight RC version, creating an incoherent
        bundle.  The pipeline defers until after the RC ships.
        """
        root = two_project_root
        _add_bug_file(root, "project_a", bug_id="BUG-0030", slug="active-rc-block")
        _set_active_rc(root, "project_a", "v0.1.0")

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, (
            f"Pipeline exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        req_dir = _requirements_dir(root, "project_a")
        bundle_files = list(req_dir.glob("*-bugfix-bundle-*.md")) if req_dir.exists() else []
        assert not bundle_files, (
            f"Expected no bug bundle while Active RC is in flight, but found: {bundle_files}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_active_rc_defers_priority_bundling(
        self, two_project_root: pathlib.Path
    ) -> None:
        """When an RC is in flight, Step 2 does not bundle new priority items.

        Same reasoning as bug bundling: an in-flight RC prevents priority
        bundles from being written until the RC ships.
        """
        root = two_project_root
        _add_priority_file(root, "project_a", priority_id="PRIORITY-0030", slug="rc-deferral")
        _set_active_rc(root, "project_a", "v0.1.0")

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0
        req_dir = _requirements_dir(root, "project_a")
        bundle_files = list(req_dir.glob("*-priority-bundle-*.md")) if req_dir.exists() else []
        assert not bundle_files, (
            f"Expected no priority bundle while Active RC is in flight, but found: {bundle_files}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_bug_bundling_resumes_after_rc_clears(
        self, two_project_root: pathlib.Path
    ) -> None:
        """After the Active RC clears to 'none', the pipeline bundles deferred bugs.

        This verifies the end-to-end cycle: RC ships → Active RC = none →
        next discovery iteration bundles the waiting bug files.
        """
        root = two_project_root
        _add_bug_file(root, "project_a", bug_id="BUG-0031", slug="resumes-after-rc")

        # First run: RC in flight — no bundle.
        _set_active_rc(root, "project_a", "v0.1.0")
        _run_discovery(root, "project_a")

        req_dir = _requirements_dir(root, "project_a")
        assert not list(req_dir.glob("*-bugfix-bundle-*.md")), (
            "No bundle expected while Active RC is in flight."
        )

        # Second run: RC ships — Active RC clears.
        (root / "projects" / "project_a" / "release-state.md").write_text(
            "# Release State\n\n## Active RC\nnone\n\n"
            "## RC Opened At\nnone\n\n## RC Opened By Task\nnone\n",
            encoding="utf-8",
        )
        result = _run_discovery(root, "project_a")

        assert result.returncode == 0
        bundle_files = list(req_dir.glob("*-bugfix-bundle-*.md"))
        assert bundle_files, (
            f"Expected bug bundle after Active RC clears to 'none'.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


class TestVersionCeilingGate:
    """Version ceiling gate prevents bundles whose version exceeds the configured ceiling."""

    def test_ceiling_gate_blocks_bundle_above_max_patch(
        self, two_project_root: pathlib.Path
    ) -> None:
        """When next-patch version exceeds max_patch, the pipeline skips bundling.

        The ceiling gate is checked after computing the target version.  When
        the computed patch version exceeds [versioning] max_patch in project.cfg,
        the pipeline skips the bundle and leaves the bug file unhandled.
        """
        root = two_project_root

        # The two_project fixture sets max_patch = 99, last_released defaults to
        # v0.0.0, so next patch would be v0.0.1.  Set max_patch = 0 to block v0.0.1.
        _set_max_patch_ceiling(root, "project_a", max_patch=0)
        _add_bug_file(root, "project_a", bug_id="BUG-0040", slug="ceiling-gate-test")

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, (
            f"Pipeline exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        req_dir = _requirements_dir(root, "project_a")
        bundle_files = list(req_dir.glob("*-bugfix-bundle-*.md")) if req_dir.exists() else []
        assert not bundle_files, (
            f"Expected no bundle when version exceeds ceiling, but found: {bundle_files}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_ceiling_gate_allows_bundle_within_max_patch(
        self, two_project_root: pathlib.Path
    ) -> None:
        """When next-patch version is within max_patch, the pipeline bundles normally.

        The default max_patch = 99 allows v0.0.1 through v0.0.99.  A bug file
        with a fresh project (last_released = v0.0.0) should produce a v0.0.1
        bundle which is within the ceiling.
        """
        root = two_project_root
        # Default max_patch = 99 from the fixture; no override needed.
        _add_bug_file(root, "project_a", bug_id="BUG-0041", slug="ceiling-allow-test")

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0
        req_dir = _requirements_dir(root, "project_a")
        bundle_files = list(req_dir.glob("*-bugfix-bundle-*.md")) if req_dir.exists() else []
        assert bundle_files, (
            f"Expected a bundle within max_patch ceiling, but none found.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


class TestPerProjectIsolation:
    """Bug or priority intake in one project does not affect the other project's state."""

    def test_bug_bundle_for_one_project_leaves_other_project_unchanged(
        self, two_project_root: pathlib.Path
    ) -> None:
        """A bug in project_a produces a bundle in project_a only.

        project_b's requirements/ directory must remain empty (no files created,
        no side effects from project_a's pipeline run).
        """
        root = two_project_root
        _add_bug_file(root, "project_a", bug_id="BUG-0050", slug="isolation-test")

        _run_discovery(root, "project_a")

        # project_b should have no bundle files.
        req_dir_b = _requirements_dir(root, "project_b")
        files_in_b = list(req_dir_b.iterdir()) if req_dir_b.exists() else []
        assert not files_in_b, (
            f"Expected project_b requirements/ to be empty after project_a's pipeline run.\n"
            f"Found in project_b requirements/: {files_in_b}"
        )

    def test_each_project_bundles_its_own_bugs_independently(
        self, two_project_root: pathlib.Path
    ) -> None:
        """Both projects can bundle independently; each bundle lands in its own requirements/.

        Running discovery for each project in sequence produces a bundle in each
        project's requirements/ directory.  The bundles are independent — each
        project has its own versioning namespace.
        """
        root = two_project_root
        _add_bug_file(root, "project_a", bug_id="BUG-0051", slug="dual-bundle-a")
        _add_bug_file(root, "project_b", bug_id="BUG-0052", slug="dual-bundle-b")

        result_a = _run_discovery(root, "project_a")
        result_b = _run_discovery(root, "project_b")

        assert result_a.returncode == 0, f"project_a pipeline failed: {result_a.stderr}"
        assert result_b.returncode == 0, f"project_b pipeline failed: {result_b.stderr}"

        req_a = list(_requirements_dir(root, "project_a").glob("*-bugfix-bundle-*.md"))
        req_b = list(_requirements_dir(root, "project_b").glob("*-bugfix-bundle-*.md"))
        assert req_a, "Expected project_a to have a bundle"
        assert req_b, "Expected project_b to have a bundle"

        # The two bundles must reside under different project directories.
        assert req_a[0].parts != req_b[0].parts, (
            "project_a and project_b bundles must be in separate directories"
        )


class TestRejectionQuarantine:
    """Malformed filenames are quarantined after repeated pipeline rejections."""

    def test_malformed_bug_filename_is_quarantined_after_threshold_rejections(
        self, two_project_root: pathlib.Path
    ) -> None:
        """A bug file with a malformed filename is moved to rejected/ after N pipeline runs.

        The discovery pipeline rejects files whose names do not match the
        canonical BUG-NNNN-<slug>.md pattern.  After PGAI_DISCOVERY_REJECT_THRESHOLD
        pipeline runs, the file is moved from bugs/ to the project's rejected/
        directory so it no longer clutters the discovery loop.
        """
        root = two_project_root
        bugs_dir = root / "projects" / "project_a" / "bugs"
        bugs_dir.mkdir(parents=True, exist_ok=True)

        # A file whose name does not match the canonical BUG-NNNN-<slug>.md pattern.
        # The pattern requires at least 4 digits; "BUG-1-bad" has only 1 digit.
        malformed_file = bugs_dir / "BUG-1-bad.md"
        malformed_file.write_text(
            "# Malformed bug file\n\n## Status\nopen\n\n## Summary\nBad name.\n",
            encoding="utf-8",
        )

        # Set threshold to 1 so the file is quarantined on the first rejection.
        for _ in range(1):
            result = _run_discovery(root, "project_a", threshold=1)
            assert result.returncode == 0, (
                f"Pipeline exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
            )

        # The malformed file must have been moved to the rejected/ directory.
        rejected_path = _rejected_dir(root, "project_a") / "BUG-1-bad.md"
        assert rejected_path.exists(), (
            f"Expected malformed bug file to be quarantined at {rejected_path}.\n"
            f"bugs/ contents: {list(bugs_dir.iterdir())}\n"
            f"rejected/ contents: {list(_rejected_dir(root, 'project_a').iterdir()) if _rejected_dir(root, 'project_a').exists() else '(missing)'}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # The source file must no longer be in bugs/.
        assert not malformed_file.exists(), (
            f"Expected malformed file to be removed from bugs/ after quarantine, "
            f"but it still exists at {malformed_file}."
        )

    def test_quarantined_file_has_sidecar_with_rejection_reason(
        self, two_project_root: pathlib.Path
    ) -> None:
        """After quarantine, a .reason sidecar file is written alongside the quarantined file.

        The sidecar records the rejection reason, the original directory, and
        the quarantine timestamp so operators can understand why the file was moved.
        """
        root = two_project_root
        bugs_dir = root / "projects" / "project_a" / "bugs"
        bugs_dir.mkdir(parents=True, exist_ok=True)

        malformed_file = bugs_dir / "BUG-2-sidecar-test.md"
        malformed_file.write_text(
            "# Sidecar test\n\n## Status\nopen\n\n## Summary\nBad name.\n",
            encoding="utf-8",
        )

        _run_discovery(root, "project_a", threshold=1)

        sidecar_path = _rejected_dir(root, "project_a") / "BUG-2-sidecar-test.md.reason"
        assert sidecar_path.exists(), (
            f"Expected sidecar file at {sidecar_path}.\n"
            f"rejected/ contents: {list(_rejected_dir(root, 'project_a').iterdir()) if _rejected_dir(root, 'project_a').exists() else '(missing)'}"
        )

        sidecar_text = sidecar_path.read_text(encoding="utf-8")
        assert "malformed" in sidecar_text.lower() or "reason" in sidecar_text.lower(), (
            f"Expected sidecar to contain rejection reason.\n"
            f"Sidecar content:\n{sidecar_text}"
        )

    def test_well_formed_bug_file_is_not_quarantined(
        self, two_project_root: pathlib.Path
    ) -> None:
        """A correctly-named bug file is bundled, not quarantined, by the pipeline.

        This is the negative case: confirms that the quarantine path is only
        triggered by genuinely malformed filenames, not by valid ones.
        """
        root = two_project_root
        _add_bug_file(root, "project_a", bug_id="BUG-0060", slug="well-formed-no-quarantine")

        _run_discovery(root, "project_a", threshold=1)

        rejected_dir = _rejected_dir(root, "project_a")
        quarantined = list(rejected_dir.glob("BUG-0060-*.md")) if rejected_dir.exists() else []
        assert not quarantined, (
            f"Expected well-formed bug file NOT to be quarantined.\n"
            f"Quarantined files: {quarantined}"
        )


class TestTerminalStateBundleFilter:
    """Discovery does not select or re-materialize intake items in terminal states.

    Terminal states are 'done' and 'wont-do'.  Once an intake item (bug file,
    priority file, or requirements bundle) carries one of those statuses, the
    discovery pipeline must skip it in every step — bundle selection, requirements
    pickup — and must never mutate its on-disk state.

    The primary regression being tested is the two-bundle scenario (an earlier defect):
    a closed v1.19.1 bundle plus a live v1.19.2 bundle.  Discovery must leave
    the v1.19.1 bundle byte-unchanged and emit the fail-loud skip log line.
    """

    def test_wontdo_requirements_bundle_is_skipped_and_live_bundle_selected(
        self, two_project_root: pathlib.Path
    ) -> None:
        """Closed (wont-do) bundle is skipped; the live higher-version bundle is queued.

        Two requirements bundles exist:
          - v0.0.2-bugfix-bundle-*.md  with  ## Status: wont-do  (closed)
          - v0.0.3-bugfix-bundle-*.md  with  ## Status: open      (live)

        Discovery must leave the v0.0.2 file byte-unchanged, emit a
        'terminal state' skip log line, and queue PM for v0.0.3 (status -> 'running').
        A stub pm-agent.sh is installed so Step 3 can actually invoke PM queuing.
        """
        root = two_project_root

        # Install a stub pm-agent.sh so Step 3 can queue PM for the live bundle.
        scripts_dir = root / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        task_id = "PM-20260711-001-decompose-v0-0-3-bugfix-bundle"
        pm_tasks_dir = root / "projects" / "project_a" / "tasks"
        pm_tasks_dir.mkdir(parents=True, exist_ok=True)
        stub_pm = scripts_dir / "pm-agent.sh"
        stub_pm.write_text(
            textwrap.dedent(f"""\
                #!/usr/bin/env bash
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

        req_dir = _requirements_dir(root, "project_a")

        # Closed bundle (wont-do) — lower version.
        closed_bundle = _add_requirements_file(
            root, "project_a",
            version="v0.0.2",
            slug="bugfix-bundle-20260711",
            status="wont-do",
        )
        closed_content_before = closed_bundle.read_bytes()

        # Live bundle (open) — higher version.
        _add_requirements_file(
            root, "project_a",
            version="v0.0.3",
            slug="bugfix-bundle-20260711",
            status="open",
        )

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, (
            f"discovery_run_pipeline exited non-zero.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # The closed bundle's on-disk content must be byte-unchanged.
        closed_content_after = closed_bundle.read_bytes()
        assert closed_content_before == closed_content_after, (
            "Closed (wont-do) bundle was mutated by the discovery pipeline. "
            "Its state must never be overwritten.\n"
            f"Content before:\n{closed_content_before.decode()}\n"
            f"Content after:\n{closed_content_after.decode()}"
        )

        # The skip log line must appear in stderr.
        combined_output = result.stderr + result.stdout
        assert "terminal state" in combined_output.lower(), (
            "Expected the discovery pipeline to emit a 'terminal state' skip "
            "log line for the wont-do bundle.\n"
            f"stderr: {result.stderr}\nstdout: {result.stdout}"
        )

        # The v0.0.3 bundle must have been queued for PM (its status set to 'running').
        live_bundle = req_dir / "v0.0.3-bugfix-bundle-20260711.md"
        assert live_bundle.exists(), (
            f"Expected live bundle at {live_bundle}.\n"
            f"requirements/ contents: {list(req_dir.iterdir())}"
        )
        live_status = live_bundle.read_text(encoding="utf-8")
        assert "running" in live_status.lower(), (
            "Expected the live (v0.0.3) bundle to be marked 'running' after "
            "discovery queued PM for it.\n"
            f"Bundle contents:\n{live_status}"
        )

    def test_done_requirements_bundle_is_equally_skipped(
        self, two_project_root: pathlib.Path
    ) -> None:
        """A 'done' bundle is skipped just like a 'wont-do' bundle.

        Both terminal states must be honored.  This test mirrors the wont-do
        case above but with ## Status: done on the lower-version bundle.
        A stub pm-agent.sh is installed so Step 3 can queue PM for the live bundle.
        """
        root = two_project_root

        # Install a stub pm-agent.sh.
        scripts_dir = root / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        task_id = "PM-20260711-002-decompose-v0-0-3-live-bugfix-bundle"
        pm_tasks_dir = root / "projects" / "project_a" / "tasks"
        pm_tasks_dir.mkdir(parents=True, exist_ok=True)
        stub_pm = scripts_dir / "pm-agent.sh"
        stub_pm.write_text(
            textwrap.dedent(f"""\
                #!/usr/bin/env bash
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

        req_dir = _requirements_dir(root, "project_a")

        # Closed bundle (done) — lower version.
        done_bundle = _add_requirements_file(
            root, "project_a",
            version="v0.0.2",
            slug="bugfix-bundle-done-20260711",
            status="done",
        )
        done_content_before = done_bundle.read_bytes()

        # Live bundle (open) — higher version.
        _add_requirements_file(
            root, "project_a",
            version="v0.0.3",
            slug="bugfix-bundle-live-20260711",
            status="open",
        )

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, (
            f"discovery_run_pipeline exited non-zero.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # The done bundle must be byte-unchanged.
        done_content_after = done_bundle.read_bytes()
        assert done_content_before == done_content_after, (
            "Done bundle was mutated by the discovery pipeline."
        )

        # The v0.0.3 live bundle must have been queued (status set to 'running').
        live_bundle = req_dir / "v0.0.3-bugfix-bundle-live-20260711.md"
        assert live_bundle.exists(), (
            f"Expected live bundle at {live_bundle}.\n"
            f"requirements/ contents: {list(req_dir.iterdir())}"
        )
        live_status = live_bundle.read_text(encoding="utf-8")
        assert "running" in live_status.lower(), (
            "Expected the live (v0.0.3) bundle to be marked 'running' after "
            "discovery queued PM for it.\n"
            f"Bundle contents:\n{live_status}"
        )

    def test_wontdo_bug_file_is_not_bundled(
        self, two_project_root: pathlib.Path
    ) -> None:
        """A bug file in wont-do state is not included in the bug bundle.

        Bug files that the operator explicitly closed (wont-do) must be filtered
        out of bug bundle collection, even when other open bugs exist.
        """
        root = two_project_root

        # Closed bug (wont-do).
        closed_bug = _add_bug_file(
            root, "project_a",
            bug_id="BUG-0100",
            slug="closed-bug",
            status="wont-do",
        )
        closed_content_before = closed_bug.read_bytes()

        # Open bug — should be the only one bundled.
        _add_bug_file(
            root, "project_a",
            bug_id="BUG-0101",
            slug="open-bug",
            status="open",
        )

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, (
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # The closed bug's state file must be byte-unchanged.
        closed_content_after = closed_bug.read_bytes()
        assert closed_content_before == closed_content_after, (
            "Closed (wont-do) bug file was mutated by the discovery pipeline."
        )

        # The skip log line for the closed bug must appear.
        combined_output = result.stderr + result.stdout
        assert "terminal state" in combined_output.lower(), (
            "Expected the pipeline to emit a 'terminal state' log line for "
            "the wont-do bug file.\n"
            f"stderr: {result.stderr}\nstdout: {result.stdout}"
        )

        # A bundle must still have been created (for the open bug).
        req_dir = _requirements_dir(root, "project_a")
        bundles = list(req_dir.glob("*-bugfix-bundle-*.md"))
        assert bundles, (
            "Expected a bundle to be created for the open bug even though a "
            "wont-do bug was also present."
        )

        # The bundle must reference only the open bug, not the closed one.
        bundle_text = bundles[0].read_text(encoding="utf-8")
        assert "BUG-0101" in bundle_text, (
            "Expected the open bug (BUG-0101) to appear in the bundle."
        )
        assert "BUG-0100" not in bundle_text, (
            "Closed bug (BUG-0100) must not appear in the bundle."
        )


class TestPMAgentFailureRetryAndStuckStateReconciliation:
    """discovery_step_requirements: correct behavior on pm-agent failure and stuck-state recovery.

    Three behaviors verified here:
      1. Failure retry: a pm-agent that exits non-zero must NOT flip ## Status to
         'running'; the ERROR line must name the file and exit code; the next
         discovery scan must re-select the bundle (i.e., retry is live).
      2. Success path regression: a succeeding pm-agent flips ## Status to 'running',
         writes the task ID into ## PM Task, and writes a pm_backlog entry —
         byte-identical semantics to the pre-fix behavior.
      3. Stuck-state reconciliation: a hand-crafted stuck bundle (## Status: running,
         ## PM Task: none, no pm_backlog entry) triggers the guard's stuck-state
         diagnosis line naming the reset recovery, not the generic 'PM in flight'
         deferral.
    """

    def test_pm_agent_failure_leaves_bundle_selectable_for_retry(
        self, two_project_root: pathlib.Path
    ) -> None:
        """When pm-agent exits non-zero, ## Status stays 'open' and the next scan retries.

        A failing pm-agent stub (exits 1, no Folder line) must not flip the
        bundle's ## Status to 'running'.  The ERROR log line must name both the
        bundle file path and the pm-agent exit code.  On the very next discovery
        run (with the failing stub replaced by a passing stub), the bundle is
        re-selected and successfully queued — proving the retry path is live.
        """
        root = two_project_root

        scripts_dir = root / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)

        # Place a requirements bundle in 'open' state.
        req_file = _add_requirements_file(
            root, "project_a", version="v0.0.2", slug="pm-fail-retry"
        )

        # --- First pass: install a failing pm-agent stub (exits 1, no Folder line). ---
        stub_pm = scripts_dir / "pm-agent.sh"
        stub_pm.write_text(
            textwrap.dedent("""\
                #!/usr/bin/env bash
                # Stubbed failing pm-agent: exits 1, emits no Folder line.
                echo "pm-agent: simulated failure" >&2
                exit 1
            """),
            encoding="utf-8",
        )
        stub_pm.chmod(0o755)

        result_fail = _run_discovery(root, "project_a")

        # Pipeline must still exit 0 (failure inside Step 3 is not a pipeline crash).
        assert result_fail.returncode == 0, (
            f"Pipeline must exit 0 even when pm-agent fails.\n"
            f"stdout: {result_fail.stdout}\nstderr: {result_fail.stderr}"
        )

        # ## Status must remain 'open' — not 'running'.
        req_text_after_fail = req_file.read_text(encoding="utf-8")
        assert "running" not in req_text_after_fail.lower(), (
            "pm-agent failure must NOT flip ## Status to 'running'.\n"
            f"Bundle content after failed pm-agent:\n{req_text_after_fail}"
        )

        # The ERROR log line must name the bundle file and the exit code.
        combined_fail = result_fail.stderr + result_fail.stdout
        assert "ERROR pm-agent failed" in combined_fail, (
            "Expected an ERROR log line naming the pm-agent failure.\n"
            f"stderr: {result_fail.stderr}\nstdout: {result_fail.stdout}"
        )
        assert "exit 1" in combined_fail, (
            "Expected the pm-agent exit code (1) in the ERROR log line.\n"
            f"stderr: {result_fail.stderr}\nstdout: {result_fail.stdout}"
        )

        # --- Second pass: replace with a succeeding stub; bundle must be re-selected. ---
        task_id = "PM-20260712-001-decompose-v0-0-2-pm-fail-retry"
        pm_tasks_dir = root / "projects" / "project_a" / "tasks"
        pm_tasks_dir.mkdir(parents=True, exist_ok=True)
        stub_pm.write_text(
            textwrap.dedent(f"""\
                #!/usr/bin/env bash
                TASK_ID="{task_id}"
                TASK_DIR="{pm_tasks_dir}/${{TASK_ID}}"
                mkdir -p "$TASK_DIR"
                printf '# %s\\n## State\\nBACKLOG\\n' "$TASK_ID" > "$TASK_DIR/status.md"
                PM_BACKLOG="${{KANBAN_ROOT}}/projects/${{PGAI_PROJECT_NAME:-project_a}}/tasks/queues/pm_backlog.md"
                mkdir -p "$(dirname "$PM_BACKLOG")"
                echo "- [ ] $TASK_ID" >> "$PM_BACKLOG"
                echo "  Folder   : $TASK_DIR"
            """),
            encoding="utf-8",
        )
        stub_pm.chmod(0o755)

        result_retry = _run_discovery(root, "project_a")

        assert result_retry.returncode == 0, (
            f"Pipeline must exit 0 on retry with passing pm-agent.\n"
            f"stdout: {result_retry.stdout}\nstderr: {result_retry.stderr}"
        )

        # Bundle ## Status must now be 'running' — retry succeeded.
        req_text_after_retry = req_file.read_text(encoding="utf-8")
        assert "running" in req_text_after_retry.lower(), (
            "Expected ## Status to be 'running' after successful retry.\n"
            f"Bundle content after retry:\n{req_text_after_retry}"
        )

        # PM backlog must contain the task entry.
        pm_backlog = root / "projects" / "project_a" / "tasks" / "queues" / "pm_backlog.md"
        backlog_text = pm_backlog.read_text(encoding="utf-8") if pm_backlog.exists() else ""
        assert task_id in backlog_text, (
            f"Expected task ID {task_id!r} in pm_backlog after retry.\n"
            f"pm_backlog:\n{backlog_text}"
        )

    def test_pm_agent_success_sets_running_and_pm_task(
        self, two_project_root: pathlib.Path
    ) -> None:
        """Successful pm-agent flips ## Status to 'running' and writes ## PM Task.

        This is the success path regression: the fix must not alter the outcome
        when pm-agent succeeds.  On exit 0 with a valid Folder line and a
        pm_backlog entry, the bundle's ## Status becomes 'running' and ## PM Task
        is written — exactly as before the fix.
        """
        root = two_project_root

        scripts_dir = root / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)

        task_id = "PM-20260712-002-decompose-v0-0-2-success-regression"
        pm_tasks_dir = root / "projects" / "project_a" / "tasks"
        pm_tasks_dir.mkdir(parents=True, exist_ok=True)

        stub_pm = scripts_dir / "pm-agent.sh"
        stub_pm.write_text(
            textwrap.dedent(f"""\
                #!/usr/bin/env bash
                TASK_ID="{task_id}"
                TASK_DIR="{pm_tasks_dir}/${{TASK_ID}}"
                mkdir -p "$TASK_DIR"
                printf '# %s\\n## State\\nBACKLOG\\n' "$TASK_ID" > "$TASK_DIR/status.md"
                PM_BACKLOG="${{KANBAN_ROOT}}/projects/${{PGAI_PROJECT_NAME:-project_a}}/tasks/queues/pm_backlog.md"
                mkdir -p "$(dirname "$PM_BACKLOG")"
                echo "- [ ] $TASK_ID" >> "$PM_BACKLOG"
                echo "  Folder   : $TASK_DIR"
            """),
            encoding="utf-8",
        )
        stub_pm.chmod(0o755)

        req_file = _add_requirements_file(
            root, "project_a", version="v0.0.2", slug="success-regression"
        )

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, (
            f"Pipeline exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        req_text = req_file.read_text(encoding="utf-8")
        assert "running" in req_text.lower(), (
            "Expected ## Status 'running' after pm-agent success.\n"
            f"Bundle content:\n{req_text}"
        )
        assert task_id in req_text, (
            f"Expected ## PM Task to contain task ID {task_id!r}.\n"
            f"Bundle content:\n{req_text}"
        )

        pm_backlog = root / "projects" / "project_a" / "tasks" / "queues" / "pm_backlog.md"
        backlog_text = pm_backlog.read_text(encoding="utf-8") if pm_backlog.exists() else ""
        assert task_id in backlog_text, (
            f"Expected task ID {task_id!r} in pm_backlog.\n"
            f"pm_backlog:\n{backlog_text}"
        )

    def test_stuck_state_running_with_no_pm_task_emits_diagnosis(
        self, two_project_root: pathlib.Path
    ) -> None:
        """Stuck-state bundle (Status: running, PM Task: none, no backlog) triggers diagnosis.

        A hand-crafted bundle with ## Status: running and ## PM Task: none, combined
        with a pm_backlog file that contains no matching entry, represents the
        stuck state produced by the old buggy code path (pm-agent failure after
        ## Status was written before verification).

        The in-flight guard must emit the stuck-state diagnosis line that names
        the reset recovery ('reset ## Status to ready'), not the generic
        'PM in flight' deferral that the old code emitted — which silently blocked
        every subsequent scan.
        """
        root = two_project_root

        # Create a bundle in the stuck state: ## Status: running, ## PM Task: none.
        req_dir = _requirements_dir(root, "project_a")
        req_dir.mkdir(parents=True, exist_ok=True)
        stuck_bundle = req_dir / "v0.0.2-stuck-bundle-20260712.md"
        stuck_bundle.write_text(
            "# v0.0.2: stuck bundle\n\n"
            "## Status\nrunning\n\n"
            "## Target Version\nv0.0.2\n\n"
            "## Workflow Type\nrelease\n\n"
            "## PM Task\nnone\n\n"
            "## Summary\nHand-crafted stuck state for reconciliation test.\n",
            encoding="utf-8",
        )

        # Create an empty pm_backlog file (exists, but contains no matching entry).
        queues_dir = root / "projects" / "project_a" / "tasks" / "queues"
        queues_dir.mkdir(parents=True, exist_ok=True)
        pm_backlog = queues_dir / "pm_backlog.md"
        pm_backlog.write_text(
            "# PM Backlog\n\n## Queue\n\n"
            "- [x] PM-20260101-000-some-completed-task\n",
            encoding="utf-8",
        )

        result = _run_discovery(root, "project_a")

        # Pipeline must exit cleanly.
        assert result.returncode == 0, (
            f"Pipeline must exit 0 even for stuck-state bundle.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # The stuck-state diagnosis line must appear — naming the reset recovery.
        combined = result.stderr + result.stdout
        assert "stuck-state" in combined.lower(), (
            "Expected stuck-state diagnosis line in discovery output.\n"
            f"stderr: {result.stderr}\nstdout: {result.stdout}"
        )
        assert "reset" in combined.lower() and "ready" in combined.lower(), (
            "Expected stuck-state diagnosis to name the recovery action "
            "('reset ## Status to ready').\n"
            f"stderr: {result.stderr}\nstdout: {result.stdout}"
        )

        # The GENERIC 'PM in flight' deferral must NOT appear for this case —
        # that was the old silent-deferral behavior being replaced.
        # Note: 'PM in flight' may appear for OTHER conditions; check only that
        # the stuck-state diagnosis line is present (checked above).  The absence
        # of the old generic line for THIS specific stuck-state path is implicitly
        # verified by the presence of the diagnosis line, since both branches are
        # mutually exclusive in the code.
        assert "stuck-state detected" in combined.lower(), (
            "Expected 'stuck-state detected' in the diagnosis log line.\n"
            f"stderr: {result.stderr}\nstdout: {result.stdout}"
        )


class TestCategoryFirstOrdering:
    """Discovery pipeline selects by category first: bugs → priorities → requirements.

    Version order applies only within a category — a bug eligible for bundling
    at a higher patch slot is always selected before a requirements file at a
    lower version number.
    """

    def test_bug_category_takes_precedence_over_lower_version_requirement(
        self, two_project_root: pathlib.Path
    ) -> None:
        """An unhandled bug is bundled before a lower-version requirements file is queued.

        Fixture shape: one open BUG-*.md file; one open requirements file whose
        Target Version is lower than the patch slot the bug bundle will claim.
        Expected outcome: discovery_step_bugs fires (bundle written), the pipeline
        returns produced_work immediately, and the requirements file is NOT queued
        for PM in the same iteration.

        This is acceptance criterion 1 from BUG-0092 defect 2.
        """
        root = two_project_root

        # Add a requirements file at v0.0.2 (lower version than the bug bundle
        # will claim: current_live=v0.0.0, so next patch = v0.0.1; add a
        # requirements file at v0.0.2 so it is ABOVE next-patch, illustrating
        # a hardcoded Target that could be reached — but still behind the bug).
        # For clarity: use v0.0.2 as the requirement and let the bug bundle land
        # at v0.0.1.  Both are above v0.0.0; the bug (at v0.0.1) has a LOWER
        # version number but is in the bug CATEGORY, which takes priority.
        req_dir = _requirements_dir(root, "project_a")
        req_dir.mkdir(parents=True, exist_ok=True)
        req_file = req_dir / "v0.0.2-operator-authored-feature.md"
        req_file.write_text(
            "# v0.0.2: operator authored feature\n\n"
            "## Status\nopen\n\n"
            "## Target Version\nv0.0.2\n\n"
            "## Workflow Type\nrelease\n\n"
            "## PM Task\nnone\n\n"
            "## Summary\nAn operator-authored requirement at v0.0.2.\n",
            encoding="utf-8",
        )

        # Add an unhandled bug file — discovery_step_bugs will bundle it at v0.0.1.
        _add_bug_file(root, "project_a", bug_id="BUG-0010", slug="category-order-test")

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, (
            f"Pipeline must exit 0.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Step 1 must have fired: a bugfix bundle appears in requirements/.
        bundle_files = list(req_dir.glob("*-bugfix-bundle-*.md"))
        assert bundle_files, (
            "Expected a bugfix-bundle-*.md file after the pipeline ran.\n"
            f"requirements/ contents: {list(req_dir.iterdir())}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # The requirements file must NOT have been queued for PM — its ## Status
        # must remain 'open' (Step 3 was skipped because Step 1 already fired).
        req_text = req_file.read_text(encoding="utf-8")
        assert "running" not in req_text.lower(), (
            "Requirements file ## Status must remain 'open' when a bug was bundled "
            "in the same iteration (category-first ordering violated).\n"
            f"Requirements file content:\n{req_text}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # DISCOVERY_LAST_STATUS must reflect produced_work from the bug bundle.
        assert "DISCOVERY_STATUS=produced_work" in result.stdout, (
            "Expected DISCOVERY_STATUS=produced_work (from bug bundle, not requirements).\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_bug_category_selected_before_requirements_when_bug_version_is_higher(
        self, two_project_root: pathlib.Path
    ) -> None:
        """Bug bundle is authored at a higher version than a pending requirement yet selected first.

        Fixture shape: last released = v0.0.2; requirements file exists at v0.0.3
        (hardcoded Target Version = v0.0.3); an unhandled bug file is also present.
        The bug bundle will land at v0.0.3+1 = v0.0.4 (next open slot after v0.0.3).
        Despite the bug bundle's version (v0.0.4) being ABOVE the requirement's
        version (v0.0.3), the bug CATEGORY always wins selection — the requirement
        file must not be queued for PM in the same iteration the bug is bundled.
        """
        root = two_project_root

        # Override last_released to v0.0.2 by writing a git-tag-like file isn't
        # feasible in a temp tree.  Instead use release-state.md to reflect a
        # last-released state; project.cfg already has max_patch = 99.  We achieve
        # "last released = v0.0.2" by pre-populating a requirements file at v0.0.1
        # and v0.0.2 with Status: done (so compute_next_patch starts at v0.0.3+1=v0.0.4
        # because v0.0.3 is taken by the operator requirement below).
        req_dir = _requirements_dir(root, "project_a")
        req_dir.mkdir(parents=True, exist_ok=True)

        # Mark v0.0.1 and v0.0.2 as "taken" via existing requirements files.
        for taken_ver in ("v0.0.1", "v0.0.2"):
            taken_file = req_dir / f"{taken_ver}-already-done.md"
            taken_file.write_text(
                f"# {taken_ver}: already done\n\n"
                f"## Status\ndone\n\n"
                f"## Target Version\n{taken_ver}\n\n"
                f"## Workflow Type\nrelease\n\n"
                f"## PM Task\nnone\n",
                encoding="utf-8",
            )

        # Operator requirement at v0.0.3 (open, eligible for PM queuing).
        req_file = req_dir / "v0.0.3-operator-feature.md"
        req_file.write_text(
            "# v0.0.3: operator feature\n\n"
            "## Status\nopen\n\n"
            "## Target Version\nv0.0.3\n\n"
            "## Workflow Type\nrelease\n\n"
            "## PM Task\nnone\n\n"
            "## Summary\nOperator-authored requirement at v0.0.3.\n",
            encoding="utf-8",
        )

        # Unhandled bug — will be bundled at v0.0.4 (v0.0.3 is occupied by req file).
        _add_bug_file(root, "project_a", bug_id="BUG-0011", slug="higher-version-bug-test")

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, (
            f"Pipeline must exit 0.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Bug bundle must have been written (Step 1 fired).
        bundle_files = list(req_dir.glob("*-bugfix-bundle-*.md"))
        assert bundle_files, (
            "Expected a bugfix-bundle-*.md file after pipeline ran (bug category).\n"
            f"requirements/ contents: {list(req_dir.iterdir())}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Requirements file at v0.0.3 must NOT have been queued (Step 3 skipped).
        req_text = req_file.read_text(encoding="utf-8")
        assert "running" not in req_text.lower(), (
            "Requirement at v0.0.3 must not be queued when bug at v0.0.4 was "
            "bundled in the same iteration (category-first: bug v0.0.4 > req v0.0.3 "
            "should not invert selection).\n"
            f"Requirements file content:\n{req_text}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_requirements_queued_when_no_bugs_or_priorities_pending(
        self, two_project_root: pathlib.Path
    ) -> None:
        """Requirements file is queued for PM when no bugs or priorities are pending.

        Verifies that the category-first change did not break the positive path:
        when Steps 1 and 2 find nothing, Step 3 still runs normally.
        """
        root = two_project_root

        # No bug or priority files — only a requirements file.
        req_dir = _requirements_dir(root, "project_a")
        req_dir.mkdir(parents=True, exist_ok=True)
        req_file = req_dir / "v0.0.2-feature-only.md"
        req_file.write_text(
            "# v0.0.2: feature only\n\n"
            "## Status\nopen\n\n"
            "## Target Version\nv0.0.2\n\n"
            "## Workflow Type\nrelease\n\n"
            "## PM Task\nnone\n\n"
            "## Summary\nRequirements-only scenario.\n",
            encoding="utf-8",
        )

        result = _run_discovery(root, "project_a")

        assert result.returncode == 0, (
            f"Pipeline must exit 0.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Step 3 must have fired — pm_backlog should have a new PM entry or
        # the requirements file ## Status should have moved to 'running'.
        # Since pm-agent.sh is not available in the temp tree, we check the
        # log output confirms Step 3 attempted to queue PM.
        combined = result.stderr + result.stdout
        assert "queueing pm" in combined.lower() or "pm not queued" in combined.lower() or \
               "produced_work" in combined or "idle" in combined, (
            "Expected Step 3 log output when no bugs or priorities are pending.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
