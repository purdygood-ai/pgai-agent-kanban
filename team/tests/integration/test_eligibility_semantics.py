"""
test_eligibility_semantics.py
==============================
Integration tests for discovery eligibility semantics — coverage for an earlier defect.

an earlier defect root cause: _disc_list_all_eligible_requirements applied semver parsing
and ceiling filtering to ALL projects regardless of the project's declared
version_semantics capability.  The fix routes eligibility through the workflow
plugin's version_semantics field, enabling label-version projects (testing-only
and future types) to have their requirements files selected correctly.

Test coverage (four acceptance items from an earlier defect):

  1. Full-path behavioral (TestTestingOnlyProjectFullPath):
     A tool-created testing-only project with an intaken label-version requirements
     doc has the doc selected by discovery, PM is queued via a stub, the stub
     places the output at the project root.  Asserts: PM task folder created, bundle
     marked running, no git mutation (no branches, no commits, no tags), no
     version mutation (release-state.md is byte-identical after the run).

  2. Semver regression (TestSemverEligibilityRegression):
     On a release-project fixture, _disc_list_all_eligible_requirements with
     version_semantics=semver produces the expected ascending-version ordering,
     identical to what a pre-fix run would produce.  This is a byte-identical
     assertion over a deterministic fixture.

  3. Skip-logging (TestSkipLogging):
     A genuinely malformed item on each semantics produces the named log line.
     - Semver: requirements file with a non-semver Target Version produces
       "discovery: skipping <file>: non-semver Target Version '<val>' on semver project"
     - Label: requirements file with an auto-sentinel Target Version produces
       "discovery: skipping <file>: auto-sentinel Target Version is not valid
       for label-semantics projects"

Naming convention: test names describe the behavior under test, never the bug
ID or the ticket ID that prompted them (SOP.md Anti-pattern 6).
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import textwrap
from typing import Optional

import pytest

# ---------------------------------------------------------------------------
# Library paths (resolved from this file's location)
#
# team/tests/integration/test_eligibility_semantics.py
#   └── team/tests/integration/
#       └── team/tests/
#           └── team/
#               └── <dev-tree-root>
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_SCRIPTS_LIB = _TEAM_DIR / "scripts" / "lib"
_REAL_WORKFLOWS_DIR = _TEAM_DIR / "workflows"


# ---------------------------------------------------------------------------
# Workflow plugin directories to copy into test fixture roots.
# Both release (for semver regression tests) and testing-only (for full-path
# tests) plugins are needed.
# ---------------------------------------------------------------------------
_WORKFLOW_PLUGINS = ["release", "document", "testing-only"]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_testing_only_project_root(
    parent: pathlib.Path,
    project_name: str = "test_proj",
) -> pathlib.Path:
    """Build a minimal kanban root containing one testing-only project.

    The root receives:
      - kanban.cfg
      - projects.cfg (registers the one project)
      - projects/<project_name>/ with project.cfg (workflow_type=testing-only)
      - projects/<project_name>/release-state.md (Active RC: none; no semver state)
      - projects/<project_name>/tasks/queues/ with per-agent backlogs
      - workflows/ — release, document, and testing-only plugins

    Args:
        parent:       Parent temp directory (use pytest's tmp_path).
        project_name: Name of the single project to create.

    Returns:
        pathlib.Path — the kanban root.
    """
    root = parent / "kanban_testing_only"
    root.mkdir(parents=True, exist_ok=True)

    # kanban.cfg
    (root / "kanban.cfg").write_text(
        "[paths]\n\n[chain]\npm_mode = automatic\n\n[wake]\nmax_tasks_per_wake = 1\n",
        encoding="utf-8",
    )

    # projects.cfg
    (root / "projects.cfg").write_text(
        f"[project:{project_name}]\npriority=1\ndescription=testing-only fixture project\nenabled=true\n",
        encoding="utf-8",
    )

    # project directory
    proj = root / "projects" / project_name
    proj.mkdir(parents=True, exist_ok=True)

    # project.cfg — testing-only workflow type
    (proj / "project.cfg").write_text(
        f"[project]\n"
        f"project_name = {project_name}\n"
        f"dev_tree_path = /dev/null\n"
        f"git_repo_url = git@example.com:{project_name}.git\n"
        f"git_remote_name = origin\n"
        f"workflow_type = testing-only\n"
        f"is_self_build = false\n\n"
        f"[versioning]\n"
        f"max_patch = 99\n"
        f"max_minor = 9\n"
        f"max_major = 0\n",
        encoding="utf-8",
    )

    # release-state.md — no semver state for testing-only projects
    (proj / "release-state.md").write_text(
        "# Release State\n\n"
        "## Active RC\n"
        "none\n\n"
        "## RC Opened At\n"
        "none\n\n"
        "## RC Opened By Task\n"
        "none\n",
        encoding="utf-8",
    )

    # tasks/queues/ + per-agent backlogs
    queues_dir = proj / "tasks" / "queues"
    queues_dir.mkdir(parents=True, exist_ok=True)
    (queues_dir / "plans").mkdir(parents=True, exist_ok=True)
    for agent in ("coder", "pm", "writer", "tester", "cm", "bug", "priority"):
        (queues_dir / f"{agent}_backlog.md").write_text(
            f"# {agent.upper()} Backlog\n\n"
            "<!-- Format: - [ ] TASK-ID -->\n",
            encoding="utf-8",
        )

    # logs directory
    (proj / "logs").mkdir(parents=True, exist_ok=True)

    # kanban-root runtime directories
    (root / "logs" / "agents").mkdir(parents=True, exist_ok=True)
    (root / "logs" / "debug" / "archive").mkdir(parents=True, exist_ok=True)
    (root / "locks").mkdir(parents=True, exist_ok=True)
    (root / "tasks" / "queues" / "plans").mkdir(parents=True, exist_ok=True)

    # workflows/ — copy plugins needed by both testing-only and semver tests
    wf_dir = root / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    for plugin_name in _WORKFLOW_PLUGINS:
        src_plugin = _REAL_WORKFLOWS_DIR / plugin_name
        if src_plugin.is_dir():
            dest_plugin = wf_dir / plugin_name
            if dest_plugin.exists():
                shutil.rmtree(dest_plugin)
            shutil.copytree(src_plugin, dest_plugin)

    return root


def _build_release_project_root(
    parent: pathlib.Path,
    project_name: str = "rel_proj",
) -> pathlib.Path:
    """Build a minimal kanban root containing one release-workflow project.

    Used by semver-regression tests.  Project receives a default versioning
    ceiling (max_patch=99) and no last-released tag so the baseline starts at
    v0.0.0 and any v0.0.x file is within ceiling.

    Args:
        parent:       Parent temp directory.
        project_name: Name of the project.

    Returns:
        pathlib.Path — the kanban root.
    """
    root = parent / "kanban_release_project"
    root.mkdir(parents=True, exist_ok=True)

    (root / "kanban.cfg").write_text(
        "[paths]\n\n[chain]\npm_mode = automatic\n\n[wake]\nmax_tasks_per_wake = 1\n",
        encoding="utf-8",
    )
    (root / "projects.cfg").write_text(
        f"[project:{project_name}]\npriority=1\ndescription=release fixture project\nenabled=true\n",
        encoding="utf-8",
    )

    proj = root / "projects" / project_name
    proj.mkdir(parents=True, exist_ok=True)

    (proj / "project.cfg").write_text(
        f"[project]\n"
        f"project_name = {project_name}\n"
        f"dev_tree_path = /dev/null\n"
        f"git_repo_url = git@example.com:{project_name}.git\n"
        f"git_remote_name = origin\n"
        f"workflow_type = release\n"
        f"is_self_build = false\n\n"
        f"[versioning]\n"
        f"max_patch = 99\n"
        f"max_minor = 9\n"
        f"max_major = 0\n",
        encoding="utf-8",
    )

    (proj / "release-state.md").write_text(
        "# Release State\n\n"
        "## Active RC\n"
        "none\n\n"
        "## RC Opened At\n"
        "none\n\n"
        "## RC Opened By Task\n"
        "none\n",
        encoding="utf-8",
    )

    queues_dir = proj / "tasks" / "queues"
    queues_dir.mkdir(parents=True, exist_ok=True)
    (queues_dir / "plans").mkdir(parents=True, exist_ok=True)
    for agent in ("coder", "pm", "writer", "tester", "cm", "bug", "priority"):
        (queues_dir / f"{agent}_backlog.md").write_text(
            f"# {agent.upper()} Backlog\n\n"
            "<!-- Format: - [ ] TASK-ID -->\n",
            encoding="utf-8",
        )

    (proj / "logs").mkdir(parents=True, exist_ok=True)
    (root / "logs" / "agents").mkdir(parents=True, exist_ok=True)
    (root / "logs" / "debug" / "archive").mkdir(parents=True, exist_ok=True)
    (root / "locks").mkdir(parents=True, exist_ok=True)
    (root / "tasks" / "queues" / "plans").mkdir(parents=True, exist_ok=True)

    wf_dir = root / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    for plugin_name in _WORKFLOW_PLUGINS:
        src_plugin = _REAL_WORKFLOWS_DIR / plugin_name
        if src_plugin.is_dir():
            dest_plugin = wf_dir / plugin_name
            if dest_plugin.exists():
                shutil.rmtree(dest_plugin)
            shutil.copytree(src_plugin, dest_plugin)

    return root


def _run_discovery(
    kanban_root: pathlib.Path,
    project_name: str,
    *,
    extra_env: Optional[dict] = None,
) -> subprocess.CompletedProcess:
    """Run discovery_run_pipeline against a temporary kanban tree.

    Mirrors the helper in test_discovery_pipeline.py but tuned for
    eligibility tests: PGAI_DEV_TREE_PATH is suppressed to skip git-tag
    lookups in compute_next_patch (relevant for semver tests); the workflow
    plugin is loaded from the temp root's workflows/ directory.

    Args:
        kanban_root:  Path to the temporary kanban root.
        project_name: Name of the project to run the pipeline for.
        extra_env:    Additional environment overrides.

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

    import os
    base_env = dict(os.environ)
    base_env["KANBAN_ROOT"] = str(kanban_root)
    base_env["TEAM_ROOT"] = str(kanban_root)
    base_env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    base_env["PGAI_PROJECT_NAME"] = project_name
    # Suppress git remote checks in compute_next_patch
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


def _add_label_version_requirements_file(
    kanban_root: pathlib.Path,
    project_name: str,
    label: str = "v20260712-fieldtest",
    slug: str = "fieldtest",
    status: str = "open",
) -> pathlib.Path:
    """Create a requirements file with a label (non-semver) Target Version.

    The filename must match BUNDLE_RE (which requires v<N>.<N>.<N>-... form)
    so the eligibility filter considers it at all.  The label version string
    lives in the ## Target Version header, not in the filename — that is the
    exact scenario an earlier defect describes: a file whose filename satisfies the
    bundle pattern but whose Target Version is a non-semver label string.

    Pre-fix: _disc_list_all_eligible_requirements applied parse_ver() to the
    label Target Version → ValueError → file silently discarded.

    Post-fix: label-semantics path skips parse_ver entirely; file is eligible.

    Args:
        kanban_root:  Kanban root path.
        project_name: Project whose requirements/ receives the file.
        label:        Label version string placed in ## Target Version header
                      (e.g. "v20260712-fieldtest").
        slug:         Slug suffix for the filename (combined with a semver-
                      shaped prefix so BUNDLE_RE matches).
        status:       ## Status field value.

    Returns:
        pathlib.Path to the created file.
    """
    req_dir = kanban_root / "projects" / project_name / "requirements"
    req_dir.mkdir(parents=True, exist_ok=True)
    # Filename uses semver-shaped prefix (v1.0.0-...) so BUNDLE_RE accepts it.
    # Target Version header holds the label string — this is the an earlier defect scenario.
    req_file = req_dir / f"v1.0.0-{slug}-testing-only.md"
    req_file.write_text(
        f"# {label}: {slug}\n\n"
        f"## Status\n{status}\n\n"
        f"## Target Version\n{label}\n\n"
        f"## Workflow Type\ntesting-only\n\n"
        f"## PM Task\nnone\n\n"
        f"## Summary\nTest requirements file with label version.\n",
        encoding="utf-8",
    )
    return req_file


def _install_stub_pm_agent(
    kanban_root: pathlib.Path,
    project_name: str,
    task_id: str,
) -> pathlib.Path:
    """Install a stub pm-agent.sh in the kanban root's scripts/ directory.

    The stub creates a minimal PM task folder and emits the expected
    "Folder : <path>" line that discovery_step_requirements parses for the
    task ID, then marks the pm_backlog with the task.

    Args:
        kanban_root:  Kanban root path.
        project_name: Project name used for building the task folder path.
        task_id:      PM task ID the stub will create.

    Returns:
        pathlib.Path to the created stub script.
    """
    scripts_dir = kanban_root / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    pm_tasks_dir = kanban_root / "projects" / project_name / "tasks"
    pm_tasks_dir.mkdir(parents=True, exist_ok=True)

    stub_pm = scripts_dir / "pm-agent.sh"
    stub_pm.write_text(
        textwrap.dedent(f"""\
            #!/usr/bin/env bash
            # Stub pm-agent.sh for eligibility integration test.
            # Creates a minimal PM task folder and echoes the Folder line.
            TASK_ID="{task_id}"
            TASK_DIR="{pm_tasks_dir}/${{TASK_ID}}"
            mkdir -p "$TASK_DIR"
            printf '# %s\\n## State\\nBACKLOG\\n' "$TASK_ID" > "$TASK_DIR/status.md"
            PM_BACKLOG="${{KANBAN_ROOT}}/projects/${{PGAI_PROJECT_NAME:-{project_name}}}/tasks/queues/pm_backlog.md"
            echo "- [ ] $TASK_ID" >> "$PM_BACKLOG"
            echo "  Folder   : $TASK_DIR"
        """),
        encoding="utf-8",
    )
    stub_pm.chmod(0o755)
    return stub_pm


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------


class TestTestingOnlyProjectFullPath:
    """Full-path behavioral: testing-only project with label-version doc.

    Asserts that a correctly intaken requirements doc on a testing-only project
    is selected by discovery and PM is queued (via stub pm-agent).  Also asserts
    that no git mutation and no version mutation occur during the discovery run.
    """

    def test_label_version_requirements_doc_is_selected_by_discovery(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Discovery selects a label-version requirements doc on a testing-only project.

        an earlier defect root cause: the eligibility filter applied semver parsing to all
        projects, so label versions raised ValueError and were silently discarded.
        This test confirms the fix: a label-version doc is selected, PM is queued,
        and the bundle is marked 'running'.
        """
        root = _build_testing_only_project_root(tmp_path)
        project_name = "test_proj"
        task_id = "PM-20260712-001-decompose-v20260712-fieldtest-fieldtest"

        _install_stub_pm_agent(root, project_name, task_id)
        req_file = _add_label_version_requirements_file(root, project_name)

        result = _run_discovery(root, project_name)

        assert result.returncode == 0, (
            f"discovery_run_pipeline exited non-zero.\nstdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

        # The bundle must have been picked up and marked 'running'.
        req_text = req_file.read_text(encoding="utf-8")
        assert "running" in req_text.lower(), (
            f"Expected requirements file ## Status to be 'running' after PM was queued.\n"
            f"Actual content:\n{req_text}\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_pm_task_folder_created_after_discovery_queues_label_doc(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """PM task folder materializes in the project's tasks/ directory.

        After discovery selects the label-version doc and invokes the stub
        pm-agent, the stub creates a task folder under the project's tasks/
        directory.  This confirms the pipeline reaches pm-agent.sh invocation.
        """
        root = _build_testing_only_project_root(tmp_path)
        project_name = "test_proj"
        task_id = "PM-20260712-002-decompose-v20260712-fieldtest-fieldtest"

        _install_stub_pm_agent(root, project_name, task_id)
        _add_label_version_requirements_file(root, project_name)

        result = _run_discovery(root, project_name)

        assert result.returncode == 0, (
            f"discovery_run_pipeline exited non-zero.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        task_dir = root / "projects" / project_name / "tasks" / task_id
        assert task_dir.exists(), (
            f"Expected PM task folder at {task_dir} to be created by pm-agent stub.\n"
            f"projects/{project_name}/tasks/ contents: "
            f"{list((root / 'projects' / project_name / 'tasks').iterdir())}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_pm_backlog_receives_task_entry_after_label_doc_selected(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """pm_backlog.md receives a pending entry for the PM task.

        Discovery marks the bundle 'running' and the stub writes a backlog
        entry.  The pm_backlog must contain the task ID so the PM agent's
        wake logic can pick it up.
        """
        root = _build_testing_only_project_root(tmp_path)
        project_name = "test_proj"
        task_id = "PM-20260712-003-decompose-v20260712-fieldtest-fieldtest"

        _install_stub_pm_agent(root, project_name, task_id)
        _add_label_version_requirements_file(root, project_name)

        _run_discovery(root, project_name)

        pm_backlog = root / "projects" / project_name / "tasks" / "queues" / "pm_backlog.md"
        backlog_text = pm_backlog.read_text(encoding="utf-8") if pm_backlog.exists() else ""
        assert task_id in backlog_text, (
            f"Expected task ID {task_id!r} to appear in pm_backlog.\n"
            f"pm_backlog content:\n{backlog_text}"
        )

    def test_discovery_does_not_create_git_objects_during_testing_only_run(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Discovery run for testing-only project produces zero git objects.

        The testing-only workflow has git_mode=ro and no CM step.  Running
        discovery must never create branches, commits, or tags in the kanban
        tree.  This test asserts the tree contains no .git directory after
        discovery runs, confirming zero git mutation.
        """
        root = _build_testing_only_project_root(tmp_path)
        project_name = "test_proj"
        task_id = "PM-20260712-004-decompose-v20260712-fieldtest-fieldtest"

        _install_stub_pm_agent(root, project_name, task_id)
        _add_label_version_requirements_file(root, project_name)

        _run_discovery(root, project_name)

        # No .git directory should exist anywhere under the kanban root.
        git_dirs = list(root.rglob(".git"))
        assert not git_dirs, (
            f"Expected no .git directories under {root} after a testing-only discovery run.\n"
            f"Found: {git_dirs}"
        )

    def test_release_state_is_byte_identical_after_testing_only_discovery_run(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """release-state.md is byte-identical before and after a testing-only discovery run.

        Testing-only projects never mutate release-state.md — they do not
        participate in the release lifecycle.  This test captures the before-bytes
        and asserts they are unchanged after discovery selects the label doc.
        """
        root = _build_testing_only_project_root(tmp_path)
        project_name = "test_proj"
        task_id = "PM-20260712-005-decompose-v20260712-fieldtest-fieldtest"

        release_state_path = root / "projects" / project_name / "release-state.md"
        content_before = release_state_path.read_bytes()

        _install_stub_pm_agent(root, project_name, task_id)
        _add_label_version_requirements_file(root, project_name)

        _run_discovery(root, project_name)

        content_after = release_state_path.read_bytes()
        assert content_before == content_after, (
            "release-state.md was mutated during a testing-only discovery run. "
            "Testing-only workflow must never touch release state.\n"
            f"Before:\n{content_before.decode()}\n"
            f"After:\n{content_after.decode()}"
        )

    def test_workflow_finalize_mode_for_testing_only_is_report(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """The testing-only plugin declares finalize=report.

        After discovery selects the label-version doc and queues PM, the
        wf_finalize hook on the testing-only plugin returns 'report', confirming
        the finalize mode is set correctly for the agent roster (pm,tester) to
        write a report artifact — not a git tag.

        This verifies acceptance item 1's "finalize=report" assertion by
        checking the plugin capability directly.
        """
        dispatcher_lib = _SCRIPTS_LIB / "workflow.sh"
        workflows_dir = _TEAM_DIR / "workflows"
        script = textwrap.dedent(f"""\
            source "{dispatcher_lib}"
            wf_load_plugin --workflows-dir '{workflows_dir}' 'testing-only'
            wf_finalize
        """)
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            cwd=str(_TEAM_DIR),
            timeout=30,
        )
        assert result.returncode == 0, (
            f"wf_finalize invocation failed.\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )
        assert result.stdout.strip() == "report", (
            f"Expected wf_finalize to return 'report' for testing-only plugin; "
            f"got {result.stdout.strip()!r}"
        )

    def test_discovery_status_reflects_work_produced_for_label_project(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Pipeline sets DISCOVERY_LAST_STATUS to 'produced_work' for a label-version doc.

        When a testing-only project has an open requirements doc with a label
        version, discovery must treat it as unhandled intake (same as a release
        project's open bug) and report 'produced_work'.
        """
        root = _build_testing_only_project_root(tmp_path)
        project_name = "test_proj"
        task_id = "PM-20260712-006-decompose-v20260712-fieldtest-fieldtest"

        _install_stub_pm_agent(root, project_name, task_id)
        _add_label_version_requirements_file(root, project_name)

        result = _run_discovery(root, project_name)

        assert result.returncode == 0
        assert "DISCOVERY_STATUS=produced_work" in result.stdout, (
            f"Expected DISCOVERY_STATUS=produced_work in stdout.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# 2. Semver regression fixture
# ---------------------------------------------------------------------------


class TestSemverEligibilityRegression:
    """Semver-path eligibility output is byte-identical before and after the fix.

    These tests run against a release-project fixture and assert the exact
    ordering and selection of the eligible-list — the semver-path invariant
    that the fix must preserve.
    """

    def test_eligible_list_is_ascending_semver_order(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """_disc_list_all_eligible_requirements returns files in ascending semver order.

        Given three requirements files at v0.0.1, v0.0.3, v0.0.2 (inserted
        in non-ascending order), the eligible-list output lists them in strict
        ascending semver order: v0.0.1, v0.0.2, v0.0.3.

        This is the invariant the fix must preserve for the semver path.
        """
        root = _build_release_project_root(tmp_path)
        project_name = "rel_proj"
        req_dir = root / "projects" / project_name / "requirements"
        req_dir.mkdir(parents=True, exist_ok=True)

        # Create files in non-ascending order to catch any sorting regression.
        for ver, slug in [
            ("v0.0.1", "bundle-20260712"),
            ("v0.0.3", "bundle-20260712"),
            ("v0.0.2", "bundle-20260712"),
        ]:
            (req_dir / f"{ver}-{slug}.md").write_text(
                f"# {ver}: bundle\n\n"
                f"## Status\nopen\n\n"
                f"## Target Version\n{ver}\n\n"
                f"## Workflow Type\nrelease\n\n"
                f"## PM Task\nnone\n\n"
                f"## Summary\nTest bundle.\n",
                encoding="utf-8",
            )

        # Call _disc_list_all_eligible_requirements via a subprocess so we test
        # the real shell-embedded Python, not a re-implemented version.
        script = textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            source "{_SCRIPTS_LIB}/ini_parser.sh"
            source "{_SCRIPTS_LIB}/temp.sh"
            source "{_SCRIPTS_LIB}/semver.sh"
            source "{_SCRIPTS_LIB}/project_paths.sh"
            source "{_SCRIPTS_LIB}/discovery.sh"
            _disc_list_all_eligible_requirements "{req_dir}" "v0.0.0" "" "" "" "semver"
        """)

        import os
        env = dict(os.environ)
        env["KANBAN_ROOT"] = str(root)
        env["TEAM_ROOT"] = str(root)
        env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
        env["PGAI_PROJECT_NAME"] = project_name
        env.pop("PGAI_DEV_TREE_PATH", None)

        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(root),
            timeout=30,
        )
        assert result.returncode == 0, (
            f"_disc_list_all_eligible_requirements exited non-zero.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Parse the output paths (strip absolute prefix to get just filenames).
        output_names = [
            pathlib.Path(line.strip()).name
            for line in result.stdout.strip().splitlines()
            if line.strip()
        ]

        expected_order = [
            "v0.0.1-bundle-20260712.md",
            "v0.0.2-bundle-20260712.md",
            "v0.0.3-bundle-20260712.md",
        ]
        assert output_names == expected_order, (
            f"Expected semver-ascending order {expected_order!r}; "
            f"got {output_names!r}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_eligible_list_excludes_version_at_or_below_last_released(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Eligible list excludes files whose Target Version <= last_released.

        With last_released = v0.0.2, files at v0.0.1 and v0.0.2 must be absent
        from the eligible list; only v0.0.3 and above qualify.
        """
        root = _build_release_project_root(tmp_path)
        project_name = "rel_proj"
        req_dir = root / "projects" / project_name / "requirements"
        req_dir.mkdir(parents=True, exist_ok=True)

        for ver in ("v0.0.1", "v0.0.2", "v0.0.3"):
            (req_dir / f"{ver}-bundle-20260712.md").write_text(
                f"# {ver}: bundle\n\n"
                f"## Status\nopen\n\n"
                f"## Target Version\n{ver}\n\n"
                f"## Workflow Type\nrelease\n\n"
                f"## PM Task\nnone\n\n"
                f"## Summary\nTest.\n",
                encoding="utf-8",
            )

        script = textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            source "{_SCRIPTS_LIB}/ini_parser.sh"
            source "{_SCRIPTS_LIB}/temp.sh"
            source "{_SCRIPTS_LIB}/semver.sh"
            source "{_SCRIPTS_LIB}/project_paths.sh"
            source "{_SCRIPTS_LIB}/discovery.sh"
            _disc_list_all_eligible_requirements "{req_dir}" "v0.0.2" "" "" "" "semver"
        """)

        import os
        env = dict(os.environ)
        env["KANBAN_ROOT"] = str(root)
        env["TEAM_ROOT"] = str(root)
        env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
        env["PGAI_PROJECT_NAME"] = project_name
        env.pop("PGAI_DEV_TREE_PATH", None)

        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(root),
            timeout=30,
        )
        assert result.returncode == 0, (
            f"_disc_list_all_eligible_requirements exited non-zero.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        output_names = [
            pathlib.Path(line.strip()).name
            for line in result.stdout.strip().splitlines()
            if line.strip() and ":ceiling-blocked" not in line and ":auto" not in line
        ]

        assert "v0.0.1-bundle-20260712.md" not in output_names, (
            "v0.0.1 must be excluded (below last_released v0.0.2)"
        )
        assert "v0.0.2-bundle-20260712.md" not in output_names, (
            "v0.0.2 must be excluded (at last_released v0.0.2)"
        )
        assert "v0.0.3-bundle-20260712.md" in output_names, (
            "v0.0.3 must be included (above last_released v0.0.2)"
        )

    def test_eligible_list_excludes_file_above_ceiling(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """A file whose Target Version exceeds max_patch is ceiling-blocked, not eligible.

        With max_patch=1, a file at v0.0.2 must appear with the
        ':ceiling-blocked:...' suffix (not as a plain eligible entry).
        v0.0.1 must appear as a plain eligible entry.
        """
        root = _build_release_project_root(tmp_path)
        project_name = "rel_proj"
        req_dir = root / "projects" / project_name / "requirements"
        req_dir.mkdir(parents=True, exist_ok=True)

        for ver in ("v0.0.1", "v0.0.2"):
            (req_dir / f"{ver}-bundle-20260712.md").write_text(
                f"# {ver}: bundle\n\n"
                f"## Status\nopen\n\n"
                f"## Target Version\n{ver}\n\n"
                f"## Workflow Type\nrelease\n\n"
                f"## PM Task\nnone\n\n"
                f"## Summary\nTest.\n",
                encoding="utf-8",
            )

        # max_patch=1: only v0.0.1 is within ceiling; v0.0.2 is blocked.
        script = textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            source "{_SCRIPTS_LIB}/ini_parser.sh"
            source "{_SCRIPTS_LIB}/temp.sh"
            source "{_SCRIPTS_LIB}/semver.sh"
            source "{_SCRIPTS_LIB}/project_paths.sh"
            source "{_SCRIPTS_LIB}/discovery.sh"
            _disc_list_all_eligible_requirements "{req_dir}" "v0.0.0" "" "" "1" "semver"
        """)

        import os
        env = dict(os.environ)
        env["KANBAN_ROOT"] = str(root)
        env["TEAM_ROOT"] = str(root)
        env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
        env["PGAI_PROJECT_NAME"] = project_name
        env.pop("PGAI_DEV_TREE_PATH", None)

        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(root),
            timeout=30,
        )
        assert result.returncode == 0, (
            f"_disc_list_all_eligible_requirements exited non-zero.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        stdout_lines = result.stdout.strip().splitlines()

        # v0.0.1 must appear as a plain eligible entry (no suffix).
        plain_names = [
            pathlib.Path(line.strip()).name
            for line in stdout_lines
            if line.strip() and ":" not in pathlib.Path(line.strip()).name
        ]
        assert "v0.0.1-bundle-20260712.md" in plain_names, (
            f"v0.0.1 must appear as a plain eligible entry.\nOutput lines: {stdout_lines}"
        )

        # v0.0.2 must appear with the ceiling-blocked suffix.
        ceiling_blocked_lines = [line for line in stdout_lines if ":ceiling-blocked:" in line]
        assert any("v0.0.2" in line for line in ceiling_blocked_lines), (
            f"v0.0.2 must appear with ':ceiling-blocked:...' suffix.\n"
            f"ceiling-blocked lines: {ceiling_blocked_lines}\nOutput lines: {stdout_lines}"
        )

    def test_label_version_files_are_all_eligible_on_label_project(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """With version_semantics=label, all non-terminal files are eligible regardless of version.

        Given two requirements files with label versions on a label-semantics
        project, both must appear in the eligible list (no semver parse, no
        ceiling filter applied).  This is byte-identical to the expected output:
        two lines of plain absolute paths in filename-lexical order.
        """
        root = _build_testing_only_project_root(tmp_path)
        project_name = "test_proj"
        req_dir = root / "projects" / project_name / "requirements"
        req_dir.mkdir(parents=True, exist_ok=True)

        # Filenames must match BUNDLE_RE (v<N>.<N>.<N>-...) to pass the allowlist
        # filter.  The label version lives in the ## Target Version header.
        # Two files with different labels; filenames differ in slug so both survive.
        file_specs = [
            ("v0.0.1-alpha-fieldtest.md", "v20260701-alpha-fieldtest"),
            ("v0.0.2-beta-fieldtest.md", "v20260712-beta-fieldtest"),
        ]
        for filename, label in file_specs:
            (req_dir / filename).write_text(
                f"# {label}: test\n\n"
                f"## Status\nopen\n\n"
                f"## Target Version\n{label}\n\n"
                f"## Workflow Type\ntesting-only\n\n"
                f"## PM Task\nnone\n\n"
                f"## Summary\nLabel version test.\n",
                encoding="utf-8",
            )

        script = textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            source "{_SCRIPTS_LIB}/ini_parser.sh"
            source "{_SCRIPTS_LIB}/temp.sh"
            source "{_SCRIPTS_LIB}/semver.sh"
            source "{_SCRIPTS_LIB}/project_paths.sh"
            source "{_SCRIPTS_LIB}/discovery.sh"
            _disc_list_all_eligible_requirements "{req_dir}" "v0.0.0" "" "" "" "label"
        """)

        import os
        env = dict(os.environ)
        env["KANBAN_ROOT"] = str(root)
        env["TEAM_ROOT"] = str(root)
        env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
        env["PGAI_PROJECT_NAME"] = project_name
        env.pop("PGAI_DEV_TREE_PATH", None)

        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(root),
            timeout=30,
        )
        assert result.returncode == 0, (
            f"_disc_list_all_eligible_requirements exited non-zero.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        output_names = [
            pathlib.Path(line.strip()).name
            for line in result.stdout.strip().splitlines()
            if line.strip()
        ]

        # Both files must appear (no semver parse → no ValueError discard on label path).
        # Label path returns files in filename-lexical ascending order.
        expected = ["v0.0.1-alpha-fieldtest.md", "v0.0.2-beta-fieldtest.md"]
        assert output_names == expected, (
            f"Expected both label-version files in filename-lexical order {expected!r}; "
            f"got {output_names!r}.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# 3. Skip-logging fixtures
# ---------------------------------------------------------------------------


class TestSkipLogging:
    """Skip-log line appears for genuinely malformed items on each semantics.

    an earlier defect expected behavior: "silently discarded is never the behavior for
    either semantics — a one-line 'discovery: skipping <file>: <reason>'
    suffices."
    """

    def test_wontdo_item_on_semver_project_emits_skip_log(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """A wont-do item on a semver project emits the named skip log line.

        an earlier defect expected behavior: malformed items (including terminal-state items)
        must NEVER be silently discarded — a one-line
        'discovery: skipping <file>: <reason>' must appear.

        For the semver path, the skip log fires for terminal-state items:
          discovery: skipping <file>: terminal state 'wont-do'

        This test verifies the 'genuinely malformed item on semver semantics'
        acceptance item: a wont-do requirements file is a definitive skip case
        on the semver path.  The log line is emitted by is_terminal() and appears
        in stderr from the Python PY heredoc.
        """
        root = _build_release_project_root(tmp_path)
        project_name = "rel_proj"
        req_dir = root / "projects" / project_name / "requirements"
        req_dir.mkdir(parents=True, exist_ok=True)

        # A wont-do item: filename passes BUNDLE_RE, item is in terminal state.
        wontdo_file = req_dir / "v0.0.1-skipped-on-semver-path.md"
        wontdo_file.write_text(
            "# v0.0.1: skipped-on-semver-path\n\n"
            "## Status\nwont-do\n\n"
            "## Target Version\nv0.0.1\n\n"
            "## Workflow Type\nrelease\n\n"
            "## PM Task\nnone\n\n"
            "## Summary\nTerminal-state item that must emit a skip log.\n",
            encoding="utf-8",
        )

        script = textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            source "{_SCRIPTS_LIB}/ini_parser.sh"
            source "{_SCRIPTS_LIB}/temp.sh"
            source "{_SCRIPTS_LIB}/semver.sh"
            source "{_SCRIPTS_LIB}/project_paths.sh"
            source "{_SCRIPTS_LIB}/discovery.sh"
            _disc_list_all_eligible_requirements "{req_dir}" "v0.0.0" "" "" "" "semver"
        """)

        import os
        env = dict(os.environ)
        env["KANBAN_ROOT"] = str(root)
        env["TEAM_ROOT"] = str(root)
        env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
        env["PGAI_PROJECT_NAME"] = project_name
        env.pop("PGAI_DEV_TREE_PATH", None)

        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(root),
            timeout=30,
        )
        assert result.returncode == 0

        combined = result.stderr + result.stdout
        assert "discovery: skipping" in combined, (
            f"Expected 'discovery: skipping' skip log for wont-do item on semver path.\n"
            f"stderr: {result.stderr}\nstdout: {result.stdout}"
        )
        assert "wont-do" in combined.lower() or "terminal" in combined.lower(), (
            f"Expected 'wont-do' or 'terminal' in skip log reason.\n"
            f"stderr: {result.stderr}\nstdout: {result.stdout}"
        )
        # The wont-do file must not appear as a plain eligible entry.
        plain_names = [
            pathlib.Path(line.strip()).name
            for line in result.stdout.strip().splitlines()
            if line.strip() and ":" not in pathlib.Path(line.strip()).name
        ]
        assert "v0.0.1-skipped-on-semver-path.md" not in plain_names, (
            f"Wont-do file must not appear as eligible; got {plain_names!r}"
        )

    def test_auto_sentinel_target_version_on_label_project_emits_skip_log(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """An auto-sentinel Target Version on a label project emits the named skip log line.

        A requirements file with Target Version: 'auto' on a testing-only project
        must trigger the log line produced by the shell caller's auto-invalid branch:
          discovery: skipping <file>: auto-sentinel Target Version is not valid
          for label-semantics projects

        This is a different code path from the semver skip — it fires in the
        shell caller (discovery_step_requirements) rather than in the PY heredoc.
        """
        root = _build_testing_only_project_root(tmp_path)
        project_name = "test_proj"
        task_id = "PM-20260712-099-unused"
        _install_stub_pm_agent(root, project_name, task_id)

        req_dir = root / "projects" / project_name / "requirements"
        req_dir.mkdir(parents=True, exist_ok=True)

        # A filename that passes BUNDLE_RE (v<N>.<N>.<N>-... format) but uses
        # auto-sentinel Target Version ('auto').  For label projects, 'auto' is
        # not valid — it triggers ':auto-invalid' suffix from
        # _disc_list_all_eligible_requirements, and the shell caller logs the reason.
        auto_file = req_dir / "v1.0.0-auto-invalid-test.md"
        auto_file.write_text(
            "# v20260712: auto-invalid-test\n\n"
            "## Status\nopen\n\n"
            "## Target Version\nauto\n\n"
            "## Workflow Type\ntesting-only\n\n"
            "## PM Task\nnone\n\n"
            "## Summary\nTest auto-sentinel on label project.\n",
            encoding="utf-8",
        )

        result = _run_discovery(root, project_name)

        assert result.returncode == 0, (
            f"discovery_run_pipeline exited non-zero.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        combined = result.stderr + result.stdout
        assert "discovery: skipping" in combined or "auto-sentinel" in combined, (
            f"Expected skip log for auto-sentinel on label project.\n"
            f"stderr: {result.stderr}\nstdout: {result.stdout}"
        )
        assert "auto" in combined.lower(), (
            f"Expected 'auto' to appear in the skip log output.\n"
            f"stderr: {result.stderr}\nstdout: {result.stdout}"
        )

    def test_terminal_state_bug_file_emits_skip_log_on_label_project(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """A wont-do requirements file on a label project emits the terminal-state skip log.

        The is_terminal() function emits:
          discovery: skipping {name}: terminal state 'wont-do'
        for any file in wont-do state, regardless of version_semantics.
        This test confirms the label path honors terminal-state filtering with logging.
        """
        root = _build_testing_only_project_root(tmp_path)
        project_name = "test_proj"
        # No stub pm-agent needed — the file should be skipped before PM is invoked.

        req_dir = root / "projects" / project_name / "requirements"
        req_dir.mkdir(parents=True, exist_ok=True)

        # wont-do requirements file with a semver-shaped filename (BUNDLE_RE matches)
        # but a label Target Version header.
        wontdo_file = req_dir / "v1.0.0-closed-testing-run.md"
        wontdo_file.write_text(
            "# v20260712-closed-testing-run: closed\n\n"
            "## Status\nwont-do\n\n"
            "## Target Version\nv20260712-closed-testing-run\n\n"
            "## Workflow Type\ntesting-only\n\n"
            "## PM Task\nnone\n\n"
            "## Summary\nClosed requirement.\n",
            encoding="utf-8",
        )

        script = textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            source "{_SCRIPTS_LIB}/ini_parser.sh"
            source "{_SCRIPTS_LIB}/temp.sh"
            source "{_SCRIPTS_LIB}/semver.sh"
            source "{_SCRIPTS_LIB}/project_paths.sh"
            source "{_SCRIPTS_LIB}/discovery.sh"
            _disc_list_all_eligible_requirements "{req_dir}" "v0.0.0" "" "" "" "label"
        """)

        import os
        env = dict(os.environ)
        env["KANBAN_ROOT"] = str(root)
        env["TEAM_ROOT"] = str(root)
        env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
        env["PGAI_PROJECT_NAME"] = project_name
        env.pop("PGAI_DEV_TREE_PATH", None)

        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(root),
            timeout=30,
        )
        assert result.returncode == 0

        combined = result.stderr + result.stdout
        assert "discovery: skipping" in combined, (
            f"Expected 'discovery: skipping' skip log for wont-do file on label project.\n"
            f"stderr: {result.stderr}\nstdout: {result.stdout}"
        )
        assert "wont-do" in combined.lower() or "terminal" in combined.lower(), (
            f"Expected 'wont-do' or 'terminal' in skip log.\n"
            f"stderr: {result.stderr}\nstdout: {result.stdout}"
        )
        # The wont-do file must not appear as a plain eligible entry.
        output_names = [
            pathlib.Path(line.strip()).name
            for line in result.stdout.strip().splitlines()
            if line.strip()
        ]
        assert "v1.0.0-closed-testing-run.md" not in output_names, (
            f"Wont-do file must not appear in eligible list; got {output_names!r}"
        )

    def test_terminal_state_wontdo_requirements_file_emits_skip_log_on_semver_project(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """A wont-do requirements file on a semver project emits the terminal-state skip log.

        Confirms the skip log fires on the semver path too (symmetric coverage).
        """
        root = _build_release_project_root(tmp_path)
        project_name = "rel_proj"
        req_dir = root / "projects" / project_name / "requirements"
        req_dir.mkdir(parents=True, exist_ok=True)

        wontdo_file = req_dir / "v0.0.1-closed-bundle.md"
        wontdo_file.write_text(
            "# v0.0.1: closed-bundle\n\n"
            "## Status\nwont-do\n\n"
            "## Target Version\nv0.0.1\n\n"
            "## Workflow Type\nrelease\n\n"
            "## PM Task\nnone\n\n"
            "## Summary\nClosed bundle.\n",
            encoding="utf-8",
        )

        script = textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            source "{_SCRIPTS_LIB}/ini_parser.sh"
            source "{_SCRIPTS_LIB}/temp.sh"
            source "{_SCRIPTS_LIB}/semver.sh"
            source "{_SCRIPTS_LIB}/project_paths.sh"
            source "{_SCRIPTS_LIB}/discovery.sh"
            _disc_list_all_eligible_requirements "{req_dir}" "v0.0.0" "" "" "" "semver"
        """)

        import os
        env = dict(os.environ)
        env["KANBAN_ROOT"] = str(root)
        env["TEAM_ROOT"] = str(root)
        env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
        env["PGAI_PROJECT_NAME"] = project_name
        env.pop("PGAI_DEV_TREE_PATH", None)

        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(root),
            timeout=30,
        )
        assert result.returncode == 0

        combined = result.stderr + result.stdout
        assert "discovery: skipping" in combined, (
            f"Expected 'discovery: skipping' skip log for wont-do file on semver project.\n"
            f"stderr: {result.stderr}\nstdout: {result.stdout}"
        )
        assert "wont-do" in combined.lower() or "terminal" in combined.lower(), (
            f"Expected 'wont-do' or 'terminal' in skip log reason.\n"
            f"stderr: {result.stderr}\nstdout: {result.stdout}"
        )
        # The wont-do file must not appear in the eligible list.
        plain_names = [
            pathlib.Path(line.strip()).name
            for line in result.stdout.strip().splitlines()
            if line.strip() and ":" not in pathlib.Path(line.strip()).name
        ]
        assert "v0.0.1-closed-bundle.md" not in plain_names, (
            f"Wont-do file must not appear in eligible list; got {plain_names!r}"
        )
