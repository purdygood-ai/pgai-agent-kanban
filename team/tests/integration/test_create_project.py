"""
test_create_project.py
======================
Integration tests for team/scripts/create-project.sh.

These tests exercise the real create-project.sh script end-to-end against
temporary kanban trees.  No seam is mocked — each test stands up a real temp
kanban root, runs the real script, and asserts on the resulting on-disk state
(directory layout, project.cfg content, queue file wiring, projects.cfg
registration).

Coverage:
  - Release workflow: expected directory layout exists after creation
  - Release workflow: project.cfg records workflow_type=release
  - Release workflow: release-specific queue files (coder_backlog.md) are seeded
  - Document workflow: expected directory layout exists after creation
  - Document workflow: project.cfg records workflow_type=document
  - Document workflow: document-specific queue files (no coder_backlog.md) seeded
  - Release workflow: release-state.md is seeded with Active RC=none
  - Document workflow: project is registered in projects.cfg after creation
  - Release workflow: project is registered in projects.cfg after creation
  - Script refuses to create a project whose directory already exists

Design notes:
  - All tests use tmp_path for scratch.  No bare /tmp paths appear in this file.
  - Each test is self-contained and produces no state visible to other tests.
  - Test names describe the behavior under test; no bug IDs, version numbers,
    or gate tokens appear in function names (SOP.md Anti-pattern 6).
  - create-project.sh is invoked via subprocess against a temp kanban root built
    by _build_minimal_kanban_root(), which creates only what the script requires
    (an existing KANBAN_ROOT dir).  The script creates projects.cfg itself when
    absent (via projects_cfg_ensure).
"""

from __future__ import annotations

import os
import pathlib
import subprocess
from typing import Optional

import pytest

# ---------------------------------------------------------------------------
# Path to the dev tree (resolves from this file's own location).
#
# File structure:
#   team/tests/integration/test_create_project.py
#                     └── team/tests/integration/  (this file)
#                         └── team/tests/
#                             └── team/
#                                 └── <dev-tree-root>
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_CREATE_PROJECT_SCRIPT = _SCRIPTS_DIR / "create-project.sh"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_minimal_kanban_root(parent: pathlib.Path) -> pathlib.Path:
    """Build a minimal kanban root for create-project.sh to operate against.

    create-project.sh requires:
      - PGAI_AGENT_KANBAN_ROOT_PATH to point at an existing directory
      - kanban.cfg (so log-path helpers do not fail; optional in practice)
      - projects.cfg (created automatically via projects_cfg_ensure if absent)

    Args:
        parent: Parent directory for the kanban root.

    Returns:
        pathlib.Path — the kanban root directory.
    """
    root = parent / "kanban_root"
    root.mkdir(parents=True, exist_ok=True)

    # Minimal kanban.cfg so scripts that read chain/paths do not fail.
    (root / "kanban.cfg").write_text(
        "[paths]\n\n[chain]\npm_mode = automatic\n\n[wake]\nmax_tasks_per_wake = 1\n",
        encoding="utf-8",
    )

    # logs/ directory expected by some helpers.
    (root / "logs").mkdir(parents=True, exist_ok=True)

    return root


def _run_create_project(
    kanban_root: pathlib.Path,
    project_name: str,
    *,
    workflow_type: str = "release",
    extra_args: Optional[list[str]] = None,
    extra_env: Optional[dict] = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    """Run create-project.sh against a temporary kanban root.

    Sets PGAI_AGENT_KANBAN_ROOT_PATH so the script creates the project under
    the caller's temp tree, never the live install.

    Args:
        kanban_root:   Path to the temporary kanban root.
        project_name:  Name of the project to create.
        workflow_type: Workflow type to pass (default: 'release').
        extra_args:    Additional CLI arguments to pass to the script.
        extra_env:     Additional environment overrides (caller wins).
        timeout:       Subprocess timeout in seconds.

    Returns:
        subprocess.CompletedProcess with returncode, stdout, stderr.
    """
    base_env = dict(os.environ)
    base_env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    # Prevent the live install root from leaking through kanban.cfg dev_tree_path.
    base_env.pop("PGAI_DEV_TREE_PATH", None)
    if extra_env:
        base_env.update(extra_env)

    cmd = [
        "bash", str(_CREATE_PROJECT_SCRIPT),
        "--project", project_name,
        "--workflow-type", workflow_type,
    ]
    if extra_args:
        cmd.extend(extra_args)

    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=base_env,
        cwd=str(kanban_root),
        timeout=timeout,
    )


def _project_dir(kanban_root: pathlib.Path, project_name: str) -> pathlib.Path:
    """Return the expected project directory path."""
    return kanban_root / "projects" / project_name


def _read_project_cfg(kanban_root: pathlib.Path, project_name: str) -> str:
    """Read and return the content of the project's project.cfg."""
    return _project_dir(kanban_root, project_name).joinpath("project.cfg").read_text(
        encoding="utf-8"
    )


def _read_release_state(kanban_root: pathlib.Path, project_name: str) -> str:
    """Read and return the content of the project's release-state.md."""
    return _project_dir(kanban_root, project_name).joinpath("release-state.md").read_text(
        encoding="utf-8"
    )


def _read_projects_cfg(kanban_root: pathlib.Path) -> str:
    """Read and return the content of projects.cfg."""
    cfg_path = kanban_root / "projects.cfg"
    return cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestReleaseWorkflowProjectLayout:
    """create-project with workflow_type=release produces the expected on-disk layout."""

    def test_release_project_directory_is_created(
        self, tmp_path: pathlib.Path
    ) -> None:
        """create-project.sh creates the project directory under projects/<name>/.

        The script must produce a directory at $KANBAN_ROOT/projects/<name>/
        after a successful run with --workflow-type release.
        """
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "my-release-proj", workflow_type="release")

        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        assert _project_dir(root, "my-release-proj").is_dir(), (
            f"Expected project directory to exist at {_project_dir(root, 'my-release-proj')}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_release_project_has_tasks_queues_subdirectory(
        self, tmp_path: pathlib.Path
    ) -> None:
        """create-project.sh creates tasks/queues/ for a release project.

        Queue files for all agents land here; the directory must exist for the
        chain to start writing backlog entries.
        """
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "release-queues-test", workflow_type="release")

        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        queues_dir = _project_dir(root, "release-queues-test") / "tasks" / "queues"
        assert queues_dir.is_dir(), (
            f"Expected tasks/queues/ to exist at {queues_dir}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_release_project_has_coder_backlog_queue(
        self, tmp_path: pathlib.Path
    ) -> None:
        """create-project.sh seeds coder_backlog.md for a release workflow project.

        The release workflow includes a CODER agent; its queue file must be
        present so the wake script can dispatch tasks to it.
        """
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "release-coder-queue", workflow_type="release")

        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        coder_backlog = (
            _project_dir(root, "release-coder-queue") / "tasks" / "queues" / "coder_backlog.md"
        )
        assert coder_backlog.is_file(), (
            f"Expected coder_backlog.md to be seeded for a release project at {coder_backlog}.\n"
            f"Queue dir contents: {list(coder_backlog.parent.iterdir()) if coder_backlog.parent.exists() else '(missing)'}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_release_project_cfg_records_release_workflow_type(
        self, tmp_path: pathlib.Path
    ) -> None:
        """project.cfg written by create-project.sh records workflow_type = release.

        The discovery pipeline and wake script read workflow_type from project.cfg
        to dispatch agents correctly; a wrong value breaks the entire chain.
        """
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "release-cfg-test", workflow_type="release")

        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        cfg_text = _read_project_cfg(root, "release-cfg-test")
        assert "workflow_type = release" in cfg_text, (
            f"Expected 'workflow_type = release' in project.cfg.\n"
            f"project.cfg content:\n{cfg_text}"
        )

    def test_release_project_cfg_records_versioning_ceilings(
        self, tmp_path: pathlib.Path
    ) -> None:
        """project.cfg written by create-project.sh includes [versioning] section with defaults.

        The discovery pipeline reads max_patch, max_minor, and max_major from
        project.cfg to enforce version ceilings; these fields must be present.
        """
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "release-versioning-test", workflow_type="release")

        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        cfg_text = _read_project_cfg(root, "release-versioning-test")
        assert "[versioning]" in cfg_text, (
            f"Expected [versioning] section in project.cfg.\ncfg content:\n{cfg_text}"
        )
        assert "max_patch" in cfg_text, (
            f"Expected 'max_patch' in project.cfg [versioning].\ncfg content:\n{cfg_text}"
        )
        assert "max_minor" in cfg_text, (
            f"Expected 'max_minor' in project.cfg [versioning].\ncfg content:\n{cfg_text}"
        )

    def test_release_project_has_seeded_release_state(
        self, tmp_path: pathlib.Path
    ) -> None:
        """create-project.sh seeds release-state.md with Active RC=none.

        The discovery pipeline reads Active RC from release-state.md to determine
        whether bundling is safe; the initial value must be 'none'.
        """
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "release-state-test", workflow_type="release")

        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        state_text = _read_release_state(root, "release-state-test")
        assert "## Active RC" in state_text, (
            f"Expected '## Active RC' in release-state.md.\nContent:\n{state_text}"
        )
        assert "none" in state_text, (
            f"Expected Active RC to be 'none' in release-state.md.\nContent:\n{state_text}"
        )

    def test_release_project_has_requirements_directory_with_templates(
        self, tmp_path: pathlib.Path
    ) -> None:
        """create-project.sh seeds requirements/templates/ for a release project.

        A REQUIREMENTS-TEMPLATE.md under requirements/templates/ is how operators
        bootstrap new requirements files — the file must exist after project creation.
        """
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "release-req-tmpl-test", workflow_type="release")

        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        req_template = (
            _project_dir(root, "release-req-tmpl-test")
            / "requirements" / "templates" / "REQUIREMENTS-TEMPLATE.md"
        )
        assert req_template.is_file(), (
            f"Expected REQUIREMENTS-TEMPLATE.md at {req_template}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_release_project_is_registered_in_projects_cfg(
        self, tmp_path: pathlib.Path
    ) -> None:
        """create-project.sh registers the new release project in projects.cfg.

        The wake script reads projects.cfg to enumerate which projects to run the
        pipeline for; a project not in projects.cfg is invisible to the chain.
        """
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "release-reg-test", workflow_type="release")

        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        cfg_text = _read_projects_cfg(root)
        assert "release-reg-test" in cfg_text, (
            f"Expected 'release-reg-test' to be registered in projects.cfg.\n"
            f"projects.cfg content:\n{cfg_text}"
        )


class TestDocumentWorkflowProjectLayout:
    """create-project with workflow_type=document produces the expected on-disk layout."""

    def test_document_project_directory_is_created(
        self, tmp_path: pathlib.Path
    ) -> None:
        """create-project.sh creates the project directory under projects/<name>/.

        The script must produce a directory at $KANBAN_ROOT/projects/<name>/
        after a successful run with --workflow-type document.
        """
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "my-doc-proj", workflow_type="document")

        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        assert _project_dir(root, "my-doc-proj").is_dir(), (
            f"Expected project directory to exist at {_project_dir(root, 'my-doc-proj')}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_document_project_cfg_records_document_workflow_type(
        self, tmp_path: pathlib.Path
    ) -> None:
        """project.cfg written by create-project.sh records workflow_type = document.

        The wake script and discovery pipeline dispatch differently for document
        vs release workflows; the wrong value breaks the pipeline routing.
        """
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "doc-cfg-test", workflow_type="document")

        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        cfg_text = _read_project_cfg(root, "doc-cfg-test")
        assert "workflow_type = document" in cfg_text, (
            f"Expected 'workflow_type = document' in project.cfg.\n"
            f"project.cfg content:\n{cfg_text}"
        )

    def test_document_project_has_writer_backlog_queue(
        self, tmp_path: pathlib.Path
    ) -> None:
        """create-project.sh seeds writer_backlog.md for a document workflow project.

        The document workflow's primary agent is WRITER; its queue file must be
        present so the wake script can dispatch tasks to it.
        """
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "doc-writer-queue", workflow_type="document")

        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        writer_backlog = (
            _project_dir(root, "doc-writer-queue") / "tasks" / "queues" / "writer_backlog.md"
        )
        assert writer_backlog.is_file(), (
            f"Expected writer_backlog.md to be seeded for a document project at {writer_backlog}.\n"
            f"Queue dir contents: {list(writer_backlog.parent.iterdir()) if writer_backlog.parent.exists() else '(missing)'}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_document_project_does_not_have_coder_backlog_queue(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Document workflow projects do not seed coder_backlog.md.

        The CODER agent is not part of the document pipeline; seeding its queue
        would mislead the wake script into dispatching CODER tasks that will never
        be created for this project.
        """
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "doc-no-coder-queue", workflow_type="document")

        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        coder_backlog = (
            _project_dir(root, "doc-no-coder-queue") / "tasks" / "queues" / "coder_backlog.md"
        )
        assert not coder_backlog.exists(), (
            f"Expected coder_backlog.md NOT to be seeded for a document project.\n"
            f"Found unexpectedly at {coder_backlog}."
        )

    def test_document_project_has_artifacts_directory(
        self, tmp_path: pathlib.Path
    ) -> None:
        """create-project.sh creates artifacts/ for a document project.

        The document pipeline's finalize step publishes the deliverable to
        projects/<name>/artifacts/; the directory must exist at creation time.
        """
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "doc-artifacts-test", workflow_type="document")

        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        artifacts_dir = _project_dir(root, "doc-artifacts-test") / "artifacts"
        assert artifacts_dir.is_dir(), (
            f"Expected artifacts/ to be created for a document project at {artifacts_dir}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_document_project_has_seeded_release_state(
        self, tmp_path: pathlib.Path
    ) -> None:
        """create-project.sh seeds release-state.md with Active RC=none for document projects.

        Document projects use release-state.md to track the active document version
        (set by cm-open-doc.sh and cleared by cm-finalize.sh); the initial state must be 'none'.
        """
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "doc-state-test", workflow_type="document")

        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        state_text = _read_release_state(root, "doc-state-test")
        assert "## Active RC" in state_text, (
            f"Expected '## Active RC' in release-state.md.\nContent:\n{state_text}"
        )
        assert "none" in state_text, (
            f"Expected Active RC to be 'none' in release-state.md.\nContent:\n{state_text}"
        )

    def test_document_project_is_registered_in_projects_cfg(
        self, tmp_path: pathlib.Path
    ) -> None:
        """create-project.sh registers the new document project in projects.cfg.

        The wake script reads projects.cfg to enumerate which projects to run the
        pipeline for; a project not in projects.cfg is invisible to the chain.
        """
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "doc-reg-test", workflow_type="document")

        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        cfg_text = _read_projects_cfg(root)
        assert "doc-reg-test" in cfg_text, (
            f"Expected 'doc-reg-test' to be registered in projects.cfg.\n"
            f"projects.cfg content:\n{cfg_text}"
        )

    def test_document_project_has_requirements_directory_with_templates(
        self, tmp_path: pathlib.Path
    ) -> None:
        """create-project.sh seeds requirements/templates/ for a document project.

        A requirements file in the project's requirements/ directory is the entry
        point for the document pipeline; the template must exist to guide operators.
        """
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "doc-req-tmpl-test", workflow_type="document")

        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        req_template = (
            _project_dir(root, "doc-req-tmpl-test")
            / "requirements" / "templates" / "REQUIREMENTS-TEMPLATE.md"
        )
        assert req_template.is_file(), (
            f"Expected REQUIREMENTS-TEMPLATE.md at {req_template}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


class TestCreateProjectGuardBehavior:
    """create-project.sh guard conditions: missing project, duplicate, invalid workflow."""

    def test_create_project_refuses_duplicate_project_directory(
        self, tmp_path: pathlib.Path
    ) -> None:
        """create-project.sh exits non-zero when the project directory already exists.

        Re-running create-project.sh for an existing project name must be rejected
        with exit code 2 rather than silently overwriting the project data.
        """
        root = _build_minimal_kanban_root(tmp_path)

        # First creation must succeed.
        result_first = _run_create_project(root, "dup-guard-test", workflow_type="release")
        assert result_first.returncode == 0, (
            f"First create-project.sh failed unexpectedly.\n"
            f"stdout: {result_first.stdout}\nstderr: {result_first.stderr}"
        )

        # Second creation of the same name must be refused.
        result_second = _run_create_project(root, "dup-guard-test", workflow_type="release")
        assert result_second.returncode == 2, (
            "Expected create-project.sh to exit with code 2 for a duplicate project name.\n"
            f"returncode: {result_second.returncode}\n"
            f"stdout: {result_second.stdout}\nstderr: {result_second.stderr}"
        )

    def test_create_project_refuses_invalid_workflow_type(
        self, tmp_path: pathlib.Path
    ) -> None:
        """create-project.sh exits non-zero when an unknown workflow type is given.

        The script only accepts 'release' and 'document'; any other value must be
        rejected early with a clear error.
        """
        root = _build_minimal_kanban_root(tmp_path)

        result = _run_create_project(root, "bad-workflow-test", workflow_type="prose")
        assert result.returncode != 0, (
            "Expected create-project.sh to fail with invalid workflow type 'prose'.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert "prose" in combined.lower() or "workflow" in combined.lower(), (
            "Expected error output to reference the invalid workflow type or 'workflow'.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
