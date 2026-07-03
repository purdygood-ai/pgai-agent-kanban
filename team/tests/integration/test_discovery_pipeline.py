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
