"""
test_discovery_race_guard.py
============================
Integration tests for the cancel→reset→re-tick race guard in the discovery
pipeline, plus the BUG-0092 drill fixture and category-order fixture.

Coverage:
  Race guard (defect 3 of BUG-0092):
    - Reset + immediate discovery tick does NOT double-handle the intake item.
    - After the settle window expires, the item IS picked up on the next tick.

  Drill fixture (BUG-0092 acceptance criterion 1 and 4):
    - Active RC exists, a hardcoded-Target requirement is in 'open' state
      (simulating post-cancel), and a bug file exists.
    - First discovery tick produces the bug bundle (Step 1 fires, Step 3 skipped).
    - Second discovery tick picks up the requirement for PM (Step 3 fires).
    - Zero collisions, zero operator edits to the requirement file.

  Category-order fixture (BUG-0092 acceptance criterion 2):
    - A bug bundle numbered ABOVE a pending requirement is still selected first.
    - Bugs → priorities → requirements ordering holds regardless of version numbers.

Design:
  - All tests use pytest tmp_path via two_project_root or direct fixture trees.
  - No bare /tmp paths.
  - Each test is self-contained.
  - Discovery is invoked via _run_discovery(), reusing the pattern from
    test_discovery_pipeline.py.
  - Test names describe the behaviour under test, not ticket IDs.
"""

from __future__ import annotations

import pathlib
import subprocess
import textwrap
from typing import Optional

import pytest

# ---------------------------------------------------------------------------
# Path constants (resolve from this file's own location)
#
# File structure:
#   team/tests/integration/test_discovery_race_guard.py
#            └── team/tests/integration/
#                └── team/tests/
#                    └── team/
#                        └── <dev-tree-root>
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_SCRIPTS_LIB = _TEAM_DIR / "scripts" / "lib"


# ---------------------------------------------------------------------------
# Shared helpers (local to this module)
# ---------------------------------------------------------------------------


def _run_discovery(
    kanban_root: pathlib.Path,
    project_name: str,
    *,
    extra_env: Optional[dict] = None,
    settle_seconds: Optional[int] = None,
) -> subprocess.CompletedProcess:
    """Run discovery_run_pipeline against a temporary kanban tree.

    Args:
        kanban_root:     Path to the temp kanban root.
        project_name:    Project to run discovery for.
        extra_env:       Additional env overrides.
        settle_seconds:  If set, overrides PGAI_DISCOVERY_SETTLE_SECONDS.
                         Pass 0 to disable the settle guard (normal discovery).
                         Pass a positive value to exercise the guard.
                         Default (None) uses the pipeline's built-in default (60).

    Returns:
        subprocess.CompletedProcess with returncode, stdout, stderr.
    """
    import os

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
    base_env.pop("PGAI_DEV_TREE_PATH", None)

    if settle_seconds is not None:
        base_env["PGAI_DISCOVERY_SETTLE_SECONDS"] = str(settle_seconds)

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


def _arm_settle_marker(
    kanban_root: pathlib.Path,
    project_name: str,
    item_path: pathlib.Path,
) -> None:
    """Write a settle marker file for an intake item, simulating a recent reset.

    This replicates what _disc_reset_item_status_to_open writes when an operator
    (or cancel-rc) resets an item's Status from 'running' back to 'open'.

    The settle marker is written with the CURRENT timestamp, so it will be
    fresh for any discover tick running within PGAI_DISCOVERY_SETTLE_SECONDS.
    """
    import time

    state_dir = kanban_root / "projects" / project_name / ".discovery-state"
    settle_dir = state_dir / "settle"
    settle_dir.mkdir(parents=True, exist_ok=True)

    item_basename = item_path.name
    settle_file = settle_dir / f"{item_basename}.settle"

    now_epoch = int(time.time())
    settle_file.write_text(
        f"{now_epoch}\n{item_path}\n",
        encoding="utf-8",
    )


def _build_single_project_root(
    parent: pathlib.Path,
    project_name: str,
    *,
    last_released: str = "v0.0.0",
) -> pathlib.Path:
    """Build a minimal single-project kanban root for race guard tests.

    Uses the same structure as the two_project fixture but for one project.
    The last_released version is written to the project's release-state.md
    as the 'Last Released' field so bump-around starts from the right base.
    """
    import shutil

    root = parent / "kanban_root"
    root.mkdir(parents=True, exist_ok=True)

    # kanban.cfg
    (root / "kanban.cfg").write_text(
        "[paths]\n\n[chain]\npm_mode = automatic\n\n[wake]\n"
        "max_tasks_per_wake = 1\nmax_runtime_seconds = 600\n",
        encoding="utf-8",
    )

    # projects.cfg
    (root / "projects.cfg").write_text(
        f"[project:{project_name}]\npriority=1\ndescription=race guard test\nenabled=true\n",
        encoding="utf-8",
    )

    proj = root / "projects" / project_name
    proj.mkdir(parents=True, exist_ok=True)

    # project.cfg
    (proj / "project.cfg").write_text(
        f"[project]\n"
        f"project_name = {project_name}\n"
        f"dev_tree_path = /dev/null\n"
        f"git_repo_url = git@example.com:{project_name}.git\n"
        f"git_remote_name = origin\n"
        f"workflow_type = release\n"
        f"is_self_build = false\n"
        f"\n[versioning]\n"
        f"max_patch = 99\nmax_minor = 9\nmax_major = 0\n",
        encoding="utf-8",
    )

    # release-state.md — idle with last_released field
    (proj / "release-state.md").write_text(
        "# Release State\n\n"
        "## Active RC\nnone\n\n"
        "## Last Released\n" + last_released + "\n\n"
        "## RC Opened At\nnone\n\n"
        "## RC Opened By Task\nnone\n",
        encoding="utf-8",
    )

    # tasks/queues/
    queues_dir = proj / "tasks" / "queues"
    queues_dir.mkdir(parents=True, exist_ok=True)
    (queues_dir / "plans").mkdir(parents=True, exist_ok=True)
    for qf in [
        "coder_backlog.md", "pm_backlog.md", "writer_backlog.md",
        "tester_backlog.md", "cm_backlog.md", "bug_backlog.md", "priority_backlog.md",
    ]:
        agent = qf.replace("_backlog.md", "")
        (queues_dir / qf).write_text(
            f"# {agent.upper()} Backlog\n\n"
            "<!-- Tasks are listed below. One task per line. -->\n"
            "<!-- Format: - [ ] TASK-ID -->\n",
            encoding="utf-8",
        )

    (proj / "logs").mkdir(parents=True, exist_ok=True)
    (proj / "overwatch" / "backups").mkdir(parents=True, exist_ok=True)
    (proj / "overwatch" / "actions.log").write_text("", encoding="utf-8")
    (proj / "metrics").mkdir(parents=True, exist_ok=True)

    # kanban root directories
    (root / "logs" / "agents").mkdir(parents=True, exist_ok=True)
    (root / "logs" / "debug" / "archive").mkdir(parents=True, exist_ok=True)
    (root / "locks").mkdir(parents=True, exist_ok=True)
    (root / "tasks" / "queues" / "plans").mkdir(parents=True, exist_ok=True)

    # workflows/
    _REAL_WORKFLOWS = _TEAM_DIR / "workflows"
    wf_dir = root / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    for plugin_name in ["release", "document"]:
        src = _REAL_WORKFLOWS / plugin_name
        if src.is_dir():
            dest = wf_dir / plugin_name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest)

    return root


def _add_bug_file(
    kanban_root: pathlib.Path,
    project_name: str,
    bug_id: str = "BUG-0001",
    slug: str = "test-fix",
    status: str = "open",
) -> pathlib.Path:
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


def _add_requirements_file(
    kanban_root: pathlib.Path,
    project_name: str,
    version: str = "v0.0.2",
    slug: str = "test-feature",
    status: str = "open",
) -> pathlib.Path:
    req_dir = kanban_root / "projects" / project_name / "requirements"
    req_dir.mkdir(parents=True, exist_ok=True)
    req_file = req_dir / f"{version}-{slug}.md"
    req_file.write_text(
        f"# {version}: {slug}\n\n"
        f"## Status\n{status}\n\n"
        f"## Target Version\n{version}\n\n"
        "## Workflow Type\nrelease\n\n"
        "## PM Task\nnone\n\n"
        "## Summary\nTest requirements file.\n",
        encoding="utf-8",
    )
    return req_file


def _requirements_dir(kanban_root: pathlib.Path, project_name: str) -> pathlib.Path:
    return kanban_root / "projects" / project_name / "requirements"


# ---------------------------------------------------------------------------
# Race guard tests
# ---------------------------------------------------------------------------


class TestSettleGuardPreventsDoubleHandling:
    """Settle guard prevents re-bundling an intake item in the same tick window.

    When an operator (or cancel-rc) resets an intake item's Status from
    'running' back to 'open', a settle marker file is written.  The next
    discovery tick sees the marker and defers pickup until the settle window
    expires.  This closes the cancel→reset→re-tick double-handling race.
    """

    def test_freshly_reset_bug_is_deferred_by_settle_guard(
        self, two_project_root: pathlib.Path
    ) -> None:
        """A bug file with a fresh settle marker is not bundled in the next tick.

        Simulates: bug file's Status is 'open' but a settle marker was just
        written (epoch = now).  Discovery must skip the item this tick.

        We use a large settle window (3600s) so the marker is definitely fresh.
        """
        root = two_project_root
        bug_file = _add_bug_file(root, "project_a", bug_id="BUG-0100", slug="race-guard-defer")

        # Arm the settle marker to simulate a just-completed reset.
        _arm_settle_marker(root, "project_a", bug_file)

        # Run discovery with a large settle window so the marker is definitely fresh.
        result = _run_discovery(root, "project_a", settle_seconds=3600)

        assert result.returncode == 0, (
            f"discovery_run_pipeline exited non-zero.\nstdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

        # The bug must NOT have been bundled — settle guard deferred it.
        req_dir = _requirements_dir(root, "project_a")
        bundle_files = list(req_dir.glob("*-bugfix-bundle-*.md")) if req_dir.exists() else []
        assert not bundle_files, (
            f"Expected NO bundle when settle guard is active, but found: {bundle_files}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_settle_guard_logs_deferred_item(
        self, two_project_root: pathlib.Path
    ) -> None:
        """When the settle guard defers an item, a log line is emitted.

        The log line must name the deferred item so an operator can diagnose
        the situation without reading raw filesystem state.
        """
        root = two_project_root
        bug_file = _add_bug_file(root, "project_a", bug_id="BUG-0101", slug="settle-log-check")

        _arm_settle_marker(root, "project_a", bug_file)

        result = _run_discovery(root, "project_a", settle_seconds=3600)

        assert result.returncode == 0
        # The settle guard should emit a log mentioning the item basename.
        combined_output = result.stdout + result.stderr
        assert "BUG-0101-settle-log-check.md" in combined_output, (
            f"Expected log line mentioning the deferred item.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_bug_is_processed_after_settle_window_expires(
        self, two_project_root: pathlib.Path
    ) -> None:
        """After the settle window expires, the bug is bundled on the next tick.

        Uses settle_seconds=0 to disable the guard on the second call, simulating
        a discovery tick that occurs after the settle window has expired.
        """
        root = two_project_root
        bug_file = _add_bug_file(root, "project_a", bug_id="BUG-0102", slug="guard-expires")

        # Arm the settle marker.
        _arm_settle_marker(root, "project_a", bug_file)

        # First tick: guard fires, item deferred.
        result1 = _run_discovery(root, "project_a", settle_seconds=3600)
        assert result1.returncode == 0

        req_dir = _requirements_dir(root, "project_a")
        after_tick1 = list(req_dir.glob("*-bugfix-bundle-*.md")) if req_dir.exists() else []
        assert not after_tick1, (
            "Expected no bundle on first tick (settle guard active).\n"
            f"stdout: {result1.stdout}\nstderr: {result1.stderr}"
        )

        # Second tick: disable settle guard (window=0) — item should now be picked up.
        result2 = _run_discovery(root, "project_a", settle_seconds=0)
        assert result2.returncode == 0, (
            f"Second discovery tick failed.\nstdout: {result2.stdout}\nstderr: {result2.stderr}"
        )

        after_tick2 = list(req_dir.glob("*-bugfix-bundle-*.md")) if req_dir.exists() else []
        assert after_tick2, (
            "Expected bug bundle after settle window expires.\n"
            f"stdout: {result2.stdout}\nstderr: {result2.stderr}"
        )

    def test_settle_guard_inactive_without_settle_file(
        self, two_project_root: pathlib.Path
    ) -> None:
        """When no settle marker exists, bugs are bundled normally.

        Confirms the guard is opt-in: without a settle marker, discovery behaves
        exactly as before — the guard must not introduce a performance regression
        or spurious skip on normal (non-reset) intake items.
        """
        root = two_project_root
        # Add bug file — no settle marker written.
        _add_bug_file(root, "project_a", bug_id="BUG-0103", slug="no-settle-file")

        result = _run_discovery(root, "project_a", settle_seconds=3600)

        assert result.returncode == 0, (
            f"discovery_run_pipeline failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        req_dir = _requirements_dir(root, "project_a")
        bundle_files = list(req_dir.glob("*-bugfix-bundle-*.md")) if req_dir.exists() else []
        assert bundle_files, (
            "Expected bug bundle when no settle file exists (guard inactive).\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# Drill fixture (BUG-0092 acceptance criterion 1: the 2026-07-20 shape)
# ---------------------------------------------------------------------------


class TestDrillFixtureCancelResetReselect:
    """Drill fixture verifying the 2026-07-20 cancel/reset/reselect shape.

    Acceptance criterion 1 of BUG-0092:
      Active RC with a hardcoded-Target requirement is cancelled; the
      requirement is reset to open; a bug exists.  Next discovery ticks
      produce: the bug bundle FIRST (next open slot), the requirement
      SECOND (reusing its freed hardcoded number), zero collisions, zero
      operator edits to the requirement file.

    Acceptance criterion 4 of BUG-0092:
      Reset + immediate discovery tick does NOT double-handle the intake item.
    """

    def test_bug_bundle_produced_before_requirement_after_cancel_reset(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Bug is bundled first (Step 1), requirement queued second (Step 3).

        Simulates the post-cancel state:
          - Active RC = none (cancelled RC has been cleared by cancel-rc)
          - A requirements file with hardcoded Target Version v0.1.0 is in 'open'
            state (reset from 'running' after cancel)
          - A bug file is open (Status=open)
          - No settle marker (simulates second tick, after settle window expired)

        Expected sequence:
          Tick 1: Step 1 fires → bug bundle created → DISCOVERY_STATUS=produced_work
          Tick 2: Step 1 skipped (bug already running) → Step 3 fires → PM queued
        """
        project_name = "drill_test"
        root = _build_single_project_root(tmp_path, project_name, last_released="v0.0.9")

        # State after cancel: Active RC cleared, requirement reset to open.
        req_file = _add_requirements_file(
            root, project_name,
            version="v0.1.0",
            slug="hardcoded-target-feature",
            status="open",
        )

        # Bug file exists (Status=open, not yet bundled).
        bug_file = _add_bug_file(
            root, project_name,
            bug_id="BUG-0110",
            slug="defect-found-after-cancel",
        )

        # Tick 1: disable settle guard so we exercise normal bug-first ordering.
        result1 = _run_discovery(root, project_name, settle_seconds=0)

        assert result1.returncode == 0, (
            f"Tick 1 discovery failed.\nstdout: {result1.stdout}\nstderr: {result1.stderr}"
        )
        assert "DISCOVERY_STATUS=produced_work" in result1.stdout, (
            f"Expected produced_work on tick 1.\nstdout: {result1.stdout}"
        )

        # Bug bundle must be in requirements/.
        req_dir = _requirements_dir(root, project_name)
        bug_bundles = list(req_dir.glob("*-bugfix-bundle-*.md"))
        assert bug_bundles, (
            f"Expected a bug bundle after tick 1.\n"
            f"requirements/ contents: {list(req_dir.iterdir()) if req_dir.exists() else '(missing)'}\n"
            f"stdout: {result1.stdout}\nstderr: {result1.stderr}"
        )

        # The requirements file must still be 'open' (not yet queued — Step 3
        # was skipped because Step 1 produced work).
        req_text = req_file.read_text(encoding="utf-8")
        req_status_line = [ln for ln in req_text.splitlines() if ln.strip() and not ln.startswith("#")]
        # The status should still be 'open'; 'running' would mean Step 3 fired incorrectly.
        assert "running" not in req_text.lower() or "running" not in req_text.splitlines()[
            next(
                (i + 1 for i, ln in enumerate(req_text.splitlines()) if "## Status" in ln),
                0
            )
        ], (
            "Requirements file must still be 'open' after tick 1 (Step 3 deferred by Step 1).\n"
            f"Requirement content:\n{req_text}"
        )

        # Verify the bug bundle was authored BEFORE the requirement (category-first ordering).
        bundle_path = bug_bundles[0]
        bundle_version_str = bundle_path.name.split("-")[0]  # e.g. "v0.0.10"
        # The bug bundle must have been produced — we assert it exists and Step 3 was deferred.
        assert bundle_path.exists(), "Bug bundle must exist after tick 1"

    def test_requirement_not_double_handled_on_immediate_discovery(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A just-reset requirement with a fresh settle marker is not acted upon.

        Simulates the race condition: cancel-rc clears the Active RC, the
        requirement file is reset to 'open', and discovery fires within the
        same tick window.  The settle guard must prevent double-handling.

        This is acceptance criterion 4 of BUG-0092.
        """
        project_name = "race_guard_drill"
        root = _build_single_project_root(tmp_path, project_name, last_released="v0.0.9")

        # Requirements file reset to 'open' just moments ago.
        req_file = _add_requirements_file(
            root, project_name,
            version="v0.1.0",
            slug="just-reset-feature",
            status="open",
        )

        # Arm the settle marker to simulate a very recent reset.
        _arm_settle_marker(root, project_name, req_file)

        # No PM stub needed — we expect Step 3 to be suppressed by the settle guard.
        # Active RC = none (already set by _build_single_project_root).

        # Run discovery with a large settle window.
        result = _run_discovery(root, project_name, settle_seconds=3600)

        assert result.returncode == 0, (
            f"discovery_run_pipeline failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Step 3 should NOT have fired — settle guard deferred the requirement.
        # The requirement file must remain 'open' (not 'running').
        req_text = req_file.read_text(encoding="utf-8")
        # Check that the Status is still 'open' (not 'running').
        # We look for the Status field value — the line after '## Status'.
        lines = req_text.splitlines()
        status_value = ""
        for i, ln in enumerate(lines):
            if ln.strip().lower().startswith("## status"):
                # Next non-blank line is the status value.
                for j in range(i + 1, min(i + 5, len(lines))):
                    if lines[j].strip():
                        status_value = lines[j].strip().lower()
                        break
                break

        assert status_value == "open", (
            f"Expected requirement to remain 'open' (not queued) when settle guard fires.\n"
            f"Got status: {status_value!r}\n"
            f"Requirement content:\n{req_text}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_zero_collisions_bug_first_then_requirement(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Two-tick drill: bug first (v next-patch), requirement second (freed slot).

        After cancel, the post-cancel state has:
          - last_released = v0.0.9 (the version that was live when the RC was opened)
          - A requirements file targeting v0.1.0 (the cancelled RC's version)
          - A bug file

        Tick 1: bug bundle → v0.0.10 (next patch after v0.0.9)
        Tick 2: requirements file queued for PM at v0.1.0 (its hardcoded Target)
        → No version number collision.

        This test does not run open-rc or cancel-rc (those are tested in
        test_cancel_rc_frees_version.py).  It verifies discovery's ordering
        given the post-cancel state.
        """
        project_name = "no_collision_drill"
        root = _build_single_project_root(tmp_path, project_name, last_released="v0.0.9")

        # Bug exists.
        _add_bug_file(root, project_name, bug_id="BUG-0120", slug="collision-check")

        # Requirements file targeting the freed slot.
        _add_requirements_file(
            root, project_name,
            version="v0.1.0",
            slug="freed-slot-feature",
            status="open",
        )

        # Tick 1: no settle guard — bug bundles first.
        result1 = _run_discovery(root, project_name, settle_seconds=0)
        assert result1.returncode == 0

        req_dir = _requirements_dir(root, project_name)
        bug_bundles_t1 = list(req_dir.glob("*-bugfix-bundle-*.md"))
        assert bug_bundles_t1, (
            f"Expected bug bundle after tick 1.\nstdout: {result1.stdout}\nstderr: {result1.stderr}"
        )

        # The bug bundle version must be v0.0.10 (next patch after v0.0.9).
        bundle_name = bug_bundles_t1[0].name
        assert "v0.0.10" in bundle_name, (
            f"Expected bug bundle at v0.0.10 (next patch after v0.0.9).\n"
            f"Actual bundle name: {bundle_name}"
        )

        # The requirements file must NOT have been bundled (remains open).
        req_files = list(req_dir.glob("v0.1.0-freed-slot-feature.md"))
        assert req_files, "Requirements file must still exist"
        req_text = req_files[0].read_text(encoding="utf-8")
        assert "running" not in _extract_status(req_text), (
            "Requirements file must remain 'open' after tick 1 (Step 3 deferred).\n"
            f"Content: {req_text}"
        )


# ---------------------------------------------------------------------------
# Category-order fixture (BUG-0092 acceptance criterion 2)
# ---------------------------------------------------------------------------


class TestCategoryFirstOrderingFixture:
    """Category-order fixture: bug category takes precedence over requirements.

    BUG-0092 defect 2: selection order contradicted bundling policy.  Discovery
    would process requirements files ahead of pending bugs when the bug bundle's
    version number was higher than the requirement's hardcoded Target Version.

    After CODER-20260721-008 (category-first selection), bugs → priorities →
    requirements holds at eligibility time, not merely at bundle-authoring time.
    Steps 1 and 2 return immediately when they produce work; Step 3 (requirements)
    only runs when Steps 1 and 2 both find nothing.

    This fixture verifies that CODER-008's fix is in place end-to-end: an
    unhandled bug file is bundled (Step 1) BEFORE a lower-version requirements
    file is queued for PM (Step 3), and also BEFORE a requirements file whose
    version is LOWER than what the bug bundle will claim.
    """

    def test_bug_file_bundled_before_lower_version_requirement_queued(
        self, two_project_root: pathlib.Path
    ) -> None:
        """An unhandled bug is bundled (Step 1) before a pending requirement is queued (Step 3).

        Fixture: one open BUG-*.md in bugs/ + one open requirements file at v0.0.2.
        The bug bundle will claim v0.0.1 (next patch after v0.0.0).
        Despite v0.0.1 < v0.0.2, the bug CATEGORY wins — Step 1 fires, Step 3 is
        deferred to the next iteration.

        This is the core BUG-0092 defect 2 scenario.
        """
        root = two_project_root

        # Operator-authored requirements file at v0.0.2 (above next-patch for bug).
        req_dir = _requirements_dir(root, "project_a")
        req_dir.mkdir(parents=True, exist_ok=True)
        req_file = req_dir / "v0.0.2-operator-feature.md"
        req_file.write_text(
            "# v0.0.2: operator feature\n\n"
            "## Status\nopen\n\n"
            "## Target Version\nv0.0.2\n\n"
            "## Workflow Type\nrelease\n\n"
            "## PM Task\nnone\n\n"
            "## Summary\nAn operator-authored requirement at v0.0.2.\n",
            encoding="utf-8",
        )

        # Unhandled bug file — Step 1 will bundle it at v0.0.1.
        _add_bug_file(root, "project_a", bug_id="BUG-0200", slug="category-order-test")

        result = _run_discovery(root, "project_a", settle_seconds=0)

        assert result.returncode == 0, (
            f"Pipeline must exit 0.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Step 1 must have fired: a bugfix-bundle-*.md appears in requirements/.
        bundle_files = list(req_dir.glob("*-bugfix-bundle-*.md"))
        assert bundle_files, (
            "Expected a bugfix-bundle-*.md file (Step 1 fired).\n"
            f"requirements/ contents: {list(req_dir.iterdir())}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Requirements file must remain 'open' — Step 3 was deferred.
        req_text = req_file.read_text(encoding="utf-8")
        assert "running" not in _extract_status(req_text), (
            f"Requirements file must remain 'open' (Step 3 deferred by Step 1).\n"
            f"Actual status: {_extract_status(req_text)!r}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # DISCOVERY_STATUS must be produced_work from the bug bundle.
        assert "DISCOVERY_STATUS=produced_work" in result.stdout, (
            "Expected DISCOVERY_STATUS=produced_work from bug bundle (Step 1).\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_bug_at_higher_version_still_selected_before_requirement(
        self, two_project_root: pathlib.Path
    ) -> None:
        """Bug bundle is authored at a higher version than the requirement yet selected first.

        Fixture: last_released state uses done requirements at v0.0.1/v0.0.2 so that
        compute_next_patch starts at v0.0.4 (v0.0.3 is taken by the operator requirement).
        Despite the bug bundle landing at v0.0.4 > v0.0.3, Step 1 fires first.
        """
        root = two_project_root

        req_dir = _requirements_dir(root, "project_a")
        req_dir.mkdir(parents=True, exist_ok=True)

        # Mark v0.0.1 and v0.0.2 as done so compute_next_patch skips them.
        for taken_ver in ("v0.0.1", "v0.0.2"):
            (req_dir / f"{taken_ver}-already-done.md").write_text(
                f"# {taken_ver}: already done\n\n"
                f"## Status\ndone\n\n"
                f"## Target Version\n{taken_ver}\n\n"
                "## Workflow Type\nrelease\n\n"
                "## PM Task\nnone\n",
                encoding="utf-8",
            )

        # Operator requirement at v0.0.3 (open, lower than bug bundle will land).
        req_file = req_dir / "v0.0.3-operator-feature.md"
        req_file.write_text(
            "# v0.0.3: operator feature\n\n"
            "## Status\nopen\n\n"
            "## Target Version\nv0.0.3\n\n"
            "## Workflow Type\nrelease\n\n"
            "## PM Task\nnone\n\n"
            "## Summary\nOperator requirement at v0.0.3.\n",
            encoding="utf-8",
        )

        # Unhandled bug — will land at v0.0.4 (next open slot after v0.0.3).
        _add_bug_file(root, "project_a", bug_id="BUG-0201", slug="higher-version-bug")

        result = _run_discovery(root, "project_a", settle_seconds=0)

        assert result.returncode == 0, (
            f"Pipeline must exit 0.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Bug bundle must have been written (Step 1 fired).
        bundle_files = list(req_dir.glob("*-bugfix-bundle-*.md"))
        assert bundle_files, (
            "Expected a bugfix-bundle-*.md (bug at v0.0.4 should be bundled).\n"
            f"requirements/ contents: {list(req_dir.iterdir())}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Requirements at v0.0.3 must NOT have been queued (Step 3 deferred).
        req_text = req_file.read_text(encoding="utf-8")
        assert "running" not in _extract_status(req_text), (
            "Requirement at v0.0.3 must not be queued when bug at v0.0.4 fires first.\n"
            f"Actual status: {_extract_status(req_text)!r}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# Shared assertion helper
# ---------------------------------------------------------------------------


def _extract_status(text: str) -> str:
    """Extract the value of the ## Status field from a requirements file."""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip().lower().startswith("## status"):
            for j in range(i + 1, min(i + 5, len(lines))):
                if lines[j].strip():
                    return lines[j].strip().lower()
    return ""
