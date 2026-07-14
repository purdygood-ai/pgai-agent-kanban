"""
test_pm_agent_ceiling_semantics_bash.py
========================================
Behavioral unit tests for the version ceiling gate in team/scripts/pm-agent.sh.

These tests invoke pm-agent.sh directly via bash to assert three behavioral
requirements introduced by an earlier defect:

1. Label-version project passes through pm-agent without crash.
   A project whose workflow plugin declares version_semantics=label (or none)
   must have its ceiling block skipped entirely.  The script must create the
   task folder, append a pm_backlog entry, and emit the expected "Folder :"
   line.  Pre-fix: unbound-variable crash at the arithmetic step.

2. Semver regression: ceiling-exceeded on a semver project still produces the
   precise component ceiling error message unchanged from pre-fix behavior.
   This asserts the fix did not change the happy-ceiling-check path.

3. Hardening: a garbage version string on a semver project produces a named
   parse/ceiling error and exits non-zero, never an unbound-variable crash.
   This covers the case where pp_version_within_ceiling returns non-zero but
   the version string itself cannot be split into numeric components.

Test naming follows SOP.md Anti-pattern 6: names describe behavior, not the
bug ID or task ID that prompted them.
"""

from __future__ import annotations

import pathlib
import shutil
import textwrap

import pytest

from tests.unit.shell_harness import run_bash

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_SCRIPTS_LIB = _SCRIPTS_DIR / "lib"
_REAL_WORKFLOWS_DIR = _TEAM_DIR / "workflows"

# Workflow plugins needed by pm-agent.sh via wf_load_plugin.
_WORKFLOW_PLUGINS = ["release", "document", "testing-only"]

_PM_AGENT = str(_SCRIPTS_DIR / "pm-agent.sh")


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _build_kanban_root_for_pm_agent(
    parent: pathlib.Path,
    project_name: str,
    workflow_type: str,
    *,
    max_major: str = "0",
    max_minor: str = "9",
    max_patch: str = "99",
    versioning_section: bool = True,
) -> pathlib.Path:
    """Build a minimal kanban root suitable for invoking pm-agent.sh directly.

    Creates the directory structure pm-agent.sh expects when it resolves
    KANBAN_ROOT / _TARGET_PROJECT and writes out the PM ticket.

    Args:
        parent:           Parent temp directory (use pytest's tmp_path).
        project_name:     Name of the single project to create.
        workflow_type:    Value for [project] workflow_type in project.cfg.
        max_major:        Ceiling max_major value (for release/semver projects).
        max_minor:        Ceiling max_minor value.
        max_patch:        Ceiling max_patch value.
        versioning_section:
                          When True (default), write a [versioning] section to
                          project.cfg.  Set False to omit it (no ceiling defined).

    Returns:
        pathlib.Path — the kanban root.
    """
    root = parent / "kanban_pm_agent"
    root.mkdir(parents=True, exist_ok=True)

    # kanban.cfg — minimal INI config
    (root / "kanban.cfg").write_text(
        "[paths]\n\n[chain]\npm_mode = automatic\n\n[wake]\nmax_tasks_per_wake = 1\n",
        encoding="utf-8",
    )

    # projects.cfg — register the project
    (root / "projects.cfg").write_text(
        f"[project:{project_name}]\n"
        f"priority=1\ndescription=pm-agent test fixture\nenabled=true\n",
        encoding="utf-8",
    )

    # project directory
    proj = root / "projects" / project_name
    proj.mkdir(parents=True, exist_ok=True)

    # project.cfg
    versioning_block = (
        f"\n[versioning]\nmax_patch = {max_patch}\nmax_minor = {max_minor}\nmax_major = {max_major}\n"
        if versioning_section
        else ""
    )
    (proj / "project.cfg").write_text(
        f"[project]\n"
        f"project_name = {project_name}\n"
        f"dev_tree_path = /dev/null\n"
        f"git_repo_url = git@example.com:{project_name}.git\n"
        f"git_remote_name = origin\n"
        f"workflow_type = {workflow_type}\n"
        f"is_self_build = false\n"
        + versioning_block,
        encoding="utf-8",
    )

    # release-state.md
    (proj / "release-state.md").write_text(
        "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n",
        encoding="utf-8",
    )

    # tasks/queues/ + per-agent backlogs
    queues_dir = proj / "tasks" / "queues"
    queues_dir.mkdir(parents=True, exist_ok=True)
    (queues_dir / "plans").mkdir(parents=True, exist_ok=True)
    for agent in ("coder", "pm", "writer", "tester", "cm", "bug", "priority"):
        (queues_dir / f"{agent}_backlog.md").write_text(
            f"# {agent.upper()} Backlog\n\n<!-- Format: - [ ] TASK-ID -->\n",
            encoding="utf-8",
        )

    # runtime directories pm-agent.sh may write into
    (proj / "logs").mkdir(parents=True, exist_ok=True)
    (root / "logs" / "agents").mkdir(parents=True, exist_ok=True)
    (root / "logs" / "debug" / "archive").mkdir(parents=True, exist_ok=True)
    (root / "locks").mkdir(parents=True, exist_ok=True)
    (root / "tasks" / "queues" / "plans").mkdir(parents=True, exist_ok=True)

    # workflows/ — copy plugins so wf_load_plugin can resolve the type
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


def _write_requirements_file(
    parent: pathlib.Path,
    filename: str,
    target_version: str,
) -> pathlib.Path:
    """Write a minimal requirements file with the given Target Version header."""
    req = parent / filename
    req.write_text(
        textwrap.dedent(f"""\
            # Test Requirements

            ## Target Version
            {target_version}

            ## Summary
            Fixture requirements file for pm-agent ceiling semantics tests.
        """),
        encoding="utf-8",
    )
    return req


def _run_pm_agent(
    tmp_path: pathlib.Path,
    kanban_root: pathlib.Path,
    requirements_file: pathlib.Path,
    project_name: str,
    *,
    extra_env: dict | None = None,
) -> object:
    """Invoke pm-agent.sh with the given fixture tree.

    Sets PGAI_AGENT_KANBAN_ROOT_PATH to kanban_root and PGAI_PROJECT_NAME to
    project_name so pm-agent.sh can resolve the project without a --project flag.

    Args:
        tmp_path:          Pytest tmp_path (for run_bash).
        kanban_root:       Kanban root as built by _build_kanban_root_for_pm_agent.
        requirements_file: Path to the requirements document.
        project_name:      Project name (resolves to PGAI_PROJECT_NAME).
        extra_env:         Additional env overrides.

    Returns:
        BashResult from run_bash.
    """
    env: dict[str, str] = {
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
        "KANBAN_ROOT": str(kanban_root),
        "PGAI_PROJECT_NAME": project_name,
        "PGAI_DEV_TREE_PATH": str(_TEAM_DIR.parent),  # dev-tree root (parent of team/)
    }
    if extra_env:
        env.update(extra_env)

    script = f"bash {_PM_AGENT} {requirements_file}"
    return run_bash(tmp_path, script, extra_env=env)


# ---------------------------------------------------------------------------
# Tests: label-version project passes through without crash
# ---------------------------------------------------------------------------


class TestLabelVersionProjectPassesThrough:
    """pm-agent passes a label-version requirements doc without crashing.

    an earlier defect: pre-fix, the ceiling arithmetic evaluated 'v20260712-fieldtest'
    and treated the hyphen as subtraction, triggering an unbound-variable crash.
    Post-fix: when version_semantics != semver the ceiling block is skipped.
    """

    def test_label_version_pm_agent_exits_zero(self, tmp_path: pathlib.Path) -> None:
        """pm-agent exits 0 on a label-version requirements doc.

        A testing-only project (version_semantics=label) must process a
        requirements file whose Target Version is v20260712-fieldtest without
        crashing.  Pre-fix exit code was non-zero due to unbound-variable error.
        """
        root = _build_kanban_root_for_pm_agent(
            tmp_path, "label-proj", "testing-only"
        )
        req = _write_requirements_file(
            tmp_path, "req-label.md", "v20260712-fieldtest"
        )
        result = _run_pm_agent(tmp_path, root, req, "label-proj")

        assert result.returncode == 0, (
            "pm-agent.sh must exit 0 for a label-version project.\n"
            "Pre-fix: unbound-variable crash at ceiling arithmetic.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_label_version_pm_agent_creates_task_folder(
        self, tmp_path: pathlib.Path
    ) -> None:
        """pm-agent creates a task folder for a label-version requirements doc.

        After running pm-agent on a testing-only project with v20260712-fieldtest,
        a PM task folder must exist under the project's tasks/ directory.
        """
        root = _build_kanban_root_for_pm_agent(
            tmp_path, "label-proj", "testing-only"
        )
        req = _write_requirements_file(
            tmp_path, "req-label-folder.md", "v20260712-fieldtest"
        )
        result = _run_pm_agent(tmp_path, root, req, "label-proj")

        assert result.returncode == 0, (
            f"pm-agent.sh exited non-zero.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

        tasks_dir = root / "projects" / "label-proj" / "tasks"
        task_folders = [
            d for d in tasks_dir.iterdir()
            if d.is_dir() and d.name.startswith("PM-")
        ]
        assert task_folders, (
            f"Expected at least one PM task folder under {tasks_dir}.\n"
            f"tasks/ contents: {list(tasks_dir.iterdir())}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_label_version_pm_agent_appends_pm_backlog_entry(
        self, tmp_path: pathlib.Path
    ) -> None:
        """pm-agent appends a pm_backlog entry for a label-version requirements doc.

        The pm_backlog.md file must receive a pending task entry so the PM
        agent's wake logic can pick it up.
        """
        root = _build_kanban_root_for_pm_agent(
            tmp_path, "label-proj", "testing-only"
        )
        req = _write_requirements_file(
            tmp_path, "req-label-backlog.md", "v20260712-fieldtest"
        )
        result = _run_pm_agent(tmp_path, root, req, "label-proj")

        assert result.returncode == 0, (
            f"pm-agent.sh exited non-zero.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

        pm_backlog = root / "projects" / "label-proj" / "tasks" / "queues" / "pm_backlog.md"
        assert pm_backlog.exists(), f"pm_backlog.md not found at {pm_backlog}"
        backlog_content = pm_backlog.read_text(encoding="utf-8")
        assert "PM-" in backlog_content, (
            f"Expected a PM task entry in pm_backlog.md.\n"
            f"Actual content:\n{backlog_content}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_label_version_pm_agent_emits_folder_line(
        self, tmp_path: pathlib.Path
    ) -> None:
        """pm-agent emits a 'Folder :' line parseable by discovery.

        The "Folder  : <path>" line in pm-agent's stdout is the handoff signal
        that discovery_step_requirements uses to locate the created PM task.
        It must appear in stdout when a label-version requirements file is processed.
        """
        root = _build_kanban_root_for_pm_agent(
            tmp_path, "label-proj", "testing-only"
        )
        req = _write_requirements_file(
            tmp_path, "req-label-folder-line.md", "v20260712-fieldtest"
        )
        result = _run_pm_agent(tmp_path, root, req, "label-proj")

        assert result.returncode == 0, (
            f"pm-agent.sh exited non-zero.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

        assert "Folder" in result.stdout, (
            "Expected a 'Folder :' line in pm-agent stdout for discovery to parse.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# Tests: semver regression — ceiling exceeded still produces precise error
# ---------------------------------------------------------------------------


class TestSemverCeilingExceededProducesPreciseError:
    """Semver regression: ceiling-exceeded still yields the component error message.

    The fix must not change the behavior for semver projects when Target Version
    exceeds the project ceiling.  The precise component message (e.g. "minor
    version N exceeds max_minor=M") must be present in stderr and exit must be
    non-zero.
    """

    def test_semver_ceiling_exceeded_exits_nonzero(
        self, tmp_path: pathlib.Path
    ) -> None:
        """pm-agent exits non-zero when Target Version exceeds the semver ceiling."""
        root = _build_kanban_root_for_pm_agent(
            tmp_path,
            "semver-proj",
            "release",
            max_major="0",
            max_minor="9",
            max_patch="99",
        )
        # v0.10.0 exceeds max_minor=9
        req = _write_requirements_file(
            tmp_path, "req-semver-ceil.md", "v0.10.0"
        )
        result = _run_pm_agent(tmp_path, root, req, "semver-proj")

        assert result.returncode != 0, (
            "pm-agent.sh must exit non-zero when Target Version exceeds ceiling.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_semver_ceiling_exceeded_error_names_component(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Ceiling-exceeded error message names the violated component.

        The error must identify which ceiling was exceeded (major, minor, or patch)
        to match the pre-fix precise-error behavior.
        """
        root = _build_kanban_root_for_pm_agent(
            tmp_path,
            "semver-proj",
            "release",
            max_major="0",
            max_minor="9",
            max_patch="99",
        )
        # v0.10.0 exceeds max_minor=9
        req = _write_requirements_file(
            tmp_path, "req-semver-ceil-msg.md", "v0.10.0"
        )
        result = _run_pm_agent(tmp_path, root, req, "semver-proj")

        assert result.returncode != 0, (
            f"pm-agent.sh must exit non-zero.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        # Must name the component violation
        assert "minor" in result.stderr or "minor" in result.stdout, (
            "Expected the ceiling error to name the 'minor' component violation.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_semver_within_ceiling_exits_zero(
        self, tmp_path: pathlib.Path
    ) -> None:
        """pm-agent exits 0 for a semver project whose Target Version is within ceiling."""
        root = _build_kanban_root_for_pm_agent(
            tmp_path,
            "semver-proj-ok",
            "release",
            max_major="0",
            max_minor="9",
            max_patch="99",
        )
        # v0.5.3 is within max_minor=9, max_patch=99
        req = _write_requirements_file(
            tmp_path, "req-semver-ok.md", "v0.5.3"
        )
        result = _run_pm_agent(tmp_path, root, req, "semver-proj-ok")

        assert result.returncode == 0, (
            "pm-agent.sh must exit 0 for a version within ceiling.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# Tests: hardening — garbage version on semver project yields named error
# ---------------------------------------------------------------------------


class TestGarbageVersionOnSemverProjectYieldsNamedError:
    """Hardening: garbage version string on a semver project must not crash.

    an earlier defect hardening rider: any unparseable version on a semver project must
    produce a named parse/ceiling error and exit non-zero.  Pre-fix: the
    arithmetic path could still crash with an unbound-variable error if the
    version reached the error-detail path with non-numeric components.
    """

    def test_garbage_version_on_semver_project_exits_nonzero(
        self, tmp_path: pathlib.Path
    ) -> None:
        """pm-agent exits non-zero for a garbage version on a semver project.

        Version v1garbage-label is extracted by the requirements awk parser
        (starts with v followed by a digit), passes to the ceiling block as a
        semver project version, but fails pp_version_within_ceiling's format
        check (not X.Y.Z).  This routes into the error-detail path; the
        arithmetic guard must produce a named error rather than a crash.
        """
        root = _build_kanban_root_for_pm_agent(
            tmp_path, "semver-hardened", "release"
        )
        # v1garbage-label: starts with v + digit (extracted by awk), but is not
        # valid semver (X.Y.Z), so pp_version_within_ceiling returns non-zero.
        req = _write_requirements_file(
            tmp_path, "req-garbage.md", "v1garbage-label"
        )
        result = _run_pm_agent(tmp_path, root, req, "semver-hardened")

        assert result.returncode != 0, (
            "pm-agent.sh must exit non-zero for a garbage version on a semver project.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_garbage_version_on_semver_project_produces_named_error(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Garbage version string on a semver project produces a named parse error.

        The error output must contain a human-readable message (not just 'unbound
        variable' or a bash arithmetic crash) that names the problem.  Pre-fix:
        a crash with 'unbound variable' would appear; post-fix: a named parse error.
        """
        root = _build_kanban_root_for_pm_agent(
            tmp_path, "semver-hardened", "release"
        )
        req = _write_requirements_file(
            tmp_path, "req-garbage-msg.md", "v1garbage-label"
        )
        result = _run_pm_agent(tmp_path, root, req, "semver-hardened")

        assert result.returncode != 0, (
            f"pm-agent.sh must exit non-zero.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

        combined = result.stdout + result.stderr
        # Must NOT produce 'unbound variable' (the crash symptom)
        assert "unbound variable" not in combined, (
            "pm-agent.sh must not crash with 'unbound variable' for a garbage version.\n"
            "Post-fix behavior: a named parse/ceiling error message.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        # Must produce a named error (ERROR: prefix from pm-agent, or ceiling message)
        assert "ERROR" in combined or "ceiling" in combined or "parsed" in combined or "semver" in combined, (
            "Expected a named error in stdout/stderr for garbage version on semver project.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_garbage_version_does_not_create_task_folder(
        self, tmp_path: pathlib.Path
    ) -> None:
        """No task folder is created when a garbage version fails the ceiling check."""
        root = _build_kanban_root_for_pm_agent(
            tmp_path, "semver-hardened-no-folder", "release"
        )
        req = _write_requirements_file(
            tmp_path, "req-garbage-no-folder.md", "v1garbage-label"
        )
        _run_pm_agent(tmp_path, root, req, "semver-hardened-no-folder")

        tasks_dir = root / "projects" / "semver-hardened-no-folder" / "tasks"
        task_folders = [
            d for d in tasks_dir.iterdir()
            if d.is_dir() and d.name.startswith("PM-")
        ]
        assert not task_folders, (
            f"pm-agent.sh must not create a task folder when version parsing fails.\n"
            f"Found folders: {task_folders}"
        )


# ---------------------------------------------------------------------------
# Tests: structural — pm-agent ceiling block uses wf_version_semantics
# ---------------------------------------------------------------------------


class TestCeilingBlockUsesVersionSemanticsAccessor:
    """Structural: pm-agent ceiling block routes through wf_version_semantics.

    an earlier defect acceptance criterion 4: grep of team/scripts confirms the ceiling
    block uses the wf_version_semantics accessor and no independent semver
    assumption (direct type-string comparison) remains.

    This is the B39/B42 grep-gate pattern applied to pm-agent.sh's ceiling region.
    """

    _PM_AGENT_SH = _SCRIPTS_DIR / "pm-agent.sh"

    def test_pm_agent_sh_exists(self) -> None:
        """pm-agent.sh exists at the expected path."""
        assert self._PM_AGENT_SH.exists(), (
            f"Expected pm-agent.sh at {self._PM_AGENT_SH}."
        )

    def test_ceiling_block_references_wf_version_semantics(self) -> None:
        """pm-agent.sh's ceiling block references the wf_version_semantics accessor.

        The fix routes version_semantics through the workflow plugin dispatcher.
        The accessor call must appear in the script so the ceiling block branches
        on a capability flag rather than a hardcoded type comparison.
        """
        source = self._PM_AGENT_SH.read_text(encoding="utf-8")
        assert "wf_version_semantics" in source, (
            "Expected 'wf_version_semantics' to appear in pm-agent.sh.\n"
            "The ceiling block must query the plugin capability, not compare type strings."
        )

    def test_ceiling_block_sources_workflow_dispatcher(self) -> None:
        """pm-agent.sh sources workflow.sh (the wf_load_plugin dispatcher).

        The dispatcher must be loaded before the ceiling block runs so that
        wf_load_plugin and wf_version_semantics are available.
        """
        source = self._PM_AGENT_SH.read_text(encoding="utf-8")
        assert "workflow.sh" in source, (
            "Expected pm-agent.sh to source workflow.sh for the wf_load_plugin dispatcher.\n"
            "The include guard pattern (declare -F wf_load_plugin) is the expected form."
        )

    def test_ceiling_block_contains_no_direct_semver_type_comparison(self) -> None:
        """pm-agent.sh ceiling block contains no direct 'semver' string comparison.

        The workflow_type string 'semver' must not appear as a quoted literal in
        an executable (non-comment) comparison inside the ceiling block.  The
        version_semantics value ('semver') is compared against the accessor result,
        which is expected.  What is forbidden is a direct workflow-type string
        comparison like [[ "$_wf_type" == "semver" ]] or [[ "$_pm_wf_type" == "semver" ]].

        Note: [[ "$_pm_version_semantics" != "semver" ]] IS the correct form and is
        allowed — it compares the capability value, not the workflow type name.
        This test is intentionally narrow: it checks only that the workflow-type
        variable (_pm_wf_type, wf_type, etc.) is not compared to a literal "semver".
        """
        source = self._PM_AGENT_SH.read_text(encoding="utf-8")
        import re
        # Detect: _pm_wf_type (or _wf_type) compared directly to "semver"
        # This would indicate a type-switch regression (comparing the workflow type
        # name string instead of the capability value).
        # Pattern: workflow type variable == or != "semver" or 'semver'
        pattern = re.compile(r'_(?:pm_)?wf_type\s*[!=]=\s*["\']semver["\']')
        matches = pattern.findall(source)
        assert not matches, (
            "pm-agent.sh ceiling block contains a direct workflow-type string comparison "
            f"to 'semver': {matches}\n"
            "The ceiling block must branch on the version_semantics capability value "
            "(_pm_version_semantics), not the workflow_type string (_pm_wf_type)."
        )
