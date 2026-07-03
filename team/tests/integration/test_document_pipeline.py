"""
test_document_pipeline.py
=========================
Integration tests for the document pipeline end-to-end:
  create-project (document) → cm-open-doc → [WRITER output stub] → cm-finalize → artifact on disk.

These tests exercise the real CM scripts against temporary kanban trees.  No
seam is mocked — each test stands up a real temp tree, runs the real scripts, and
asserts on the resulting on-disk state.  The WRITER step is represented by a
polished.md stub written directly into the WRITER task artifacts directory; this
correctly models the pipeline integration point (cm-finalize reads from
PGAI_WRITER_POLISH_TASK_ARTIFACTS, not from the WRITER agent internals).

Coverage:
  - cm-open-doc.sh creates input/working/output dirs under the framework temp root
  - cm-open-doc.sh writes Active RC into the project's release-state.md
  - Full pipeline (open-doc → polish stub → finalize) publishes the artifact to
    projects/<project>/artifacts/<version>-<name>.md
  - cm-finalize.sh clears Active RC to 'none' in release-state.md after publishing
  - cm-finalize.sh records Last Released in release-state.md after publishing
  - Finalize exits non-zero when PGAI_WRITER_POLISH_TASK_ARTIFACTS is not set
  - Finalize exits non-zero when polished.md is absent from the artifacts directory

Design notes:
  - All tests use tmp_path for scratch.  No bare /tmp paths appear in this file.
  - Each test is self-contained; no shared state between tests.
  - Test names describe the behavior under test; no bug IDs, version numbers, or
    gate tokens appear in function names (SOP.md Anti-pattern 6).
  - The minimal kanban root built here includes the pgai_agent_kanban/cm/ Python
    helpers (write_rc_state.py etc.) because open-doc.sh and finalize.sh invoke
    them via $KANBAN_ROOT/pgai_agent_kanban/cm/.  Failures in those helpers are
    non-blocking in both scripts (they log a warning and continue), so tests remain
    valid even in environments where the Python helpers behave unexpectedly.
  - PGAI_AGENT_KANBAN_TEMP_DIR is set to a subdir of tmp_path for subprocess runs
    so the scripts' working dirs land under tmp_path, never bare /tmp.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
from typing import Optional

import pytest

# ---------------------------------------------------------------------------
# Path helpers — resolved from this file's own location.
#
# File structure:
#   team/tests/integration/test_document_pipeline.py
#                     └── team/tests/integration/  (this file)
#                         └── team/tests/
#                             └── team/
#                                 └── <dev-tree-root>
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_CREATE_PROJECT_SCRIPT = _SCRIPTS_DIR / "create-project.sh"
_CM_SCRIPTS_DIR = _SCRIPTS_DIR / "cm"
_OPEN_DOC_SCRIPT = _CM_SCRIPTS_DIR / "open-doc.sh"
_FINALIZE_SCRIPT = _CM_SCRIPTS_DIR / "finalize.sh"
_KANBAN_PY_DIR = _TEAM_DIR / "pgai_agent_kanban"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_doc_kanban_root(
    parent: pathlib.Path,
    project_name: str,
) -> pathlib.Path:
    """Build a kanban root pre-configured for document workflow testing.

    Creates the full project layout that create-project.sh would produce for a
    document project, plus the pgai_agent_kanban/cm/ Python helpers needed by
    open-doc.sh and finalize.sh.  This avoids running create-project.sh as a
    dependency (each test is self-contained).

    Args:
        parent:       Parent directory for the kanban root.
        project_name: Name of the project (lowercase letters, digits, hyphens).

    Returns:
        pathlib.Path — the kanban root directory.
    """
    root = parent / "kanban_root"
    root.mkdir(parents=True, exist_ok=True)

    # kanban.cfg — required by the runner helper and some lib functions.
    (root / "kanban.cfg").write_text(
        "[paths]\n\n[chain]\npm_mode = automatic\n\n[wake]\nmax_tasks_per_wake = 1\n",
        encoding="utf-8",
    )

    # projects.cfg — register the project so finalize can reference it.
    (root / "projects.cfg").write_text(
        f"[project:{project_name}]\n"
        "priority = 1\n"
        "description = Document pipeline integration test project\n"
        "enabled = true\n",
        encoding="utf-8",
    )

    # Project directory layout.
    proj = root / "projects" / project_name
    (proj / "tasks" / "queues").mkdir(parents=True, exist_ok=True)
    (proj / "requirements" / "templates").mkdir(parents=True, exist_ok=True)
    (proj / "artifacts").mkdir(parents=True, exist_ok=True)
    (proj / "release-state").mkdir(parents=True, exist_ok=True)
    (proj / "bugs" / "templates").mkdir(parents=True, exist_ok=True)
    (proj / "priority" / "templates").mkdir(parents=True, exist_ok=True)
    (proj / "logs").mkdir(parents=True, exist_ok=True)

    # project.cfg for the document workflow.
    (proj / "project.cfg").write_text(
        f"[project]\n"
        f"project_name = {project_name}\n"
        "workflow_type = document\n"
        "git_remote_name = origin\n"
        "dev_tree_path =\n"
        "git_repo_url =\n"
        "branch_prefix = ai_\n"
        "push_to_remote = false\n"
        "\n"
        "[versioning]\n"
        "max_patch = 21\n"
        "max_minor = 13\n"
        "max_major = 0\n",
        encoding="utf-8",
    )

    # release-state.md — idle (no active document).
    (proj / "release-state.md").write_text(
        "# Release State\n\n"
        "## Active RC\n"
        "none\n\n"
        "## RC Opened At\n"
        "none\n\n"
        "## RC Opened By Task\n"
        "none\n\n"
        "## Last Released\n"
        "none\n",
        encoding="utf-8",
    )

    # Runtime directories.
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "locks").mkdir(parents=True, exist_ok=True)

    # Copy the pgai_agent_kanban/cm/ Python helpers into the temp kanban root.
    # open-doc.sh calls: python3 "$KANBAN_ROOT/pgai_agent_kanban/cm/write_rc_state.py" open ...
    # finalize.sh calls: python3 "$KANBAN_ROOT/pgai_agent_kanban/cm/write_rc_state.py" ship ...
    # Failure in these helpers is non-blocking (both scripts log a warning and continue),
    # but having them present ensures complete pipeline behaviour for assertions.
    cm_py_dir = root / "pgai_agent_kanban" / "cm"
    cm_py_dir.mkdir(parents=True, exist_ok=True)

    src_cm_dir = _KANBAN_PY_DIR / "cm"
    if src_cm_dir.is_dir():
        for py_file in src_cm_dir.glob("*.py"):
            (cm_py_dir / py_file.name).write_text(
                py_file.read_text(encoding="utf-8"), encoding="utf-8"
            )

    # Also copy metrics_aggregator.py and metrics_csv_writer.py into scripts/lib/
    # if present in the dev tree.  finalize.sh invokes these as non-blocking steps;
    # their absence triggers an advisory WARNING, not a failure.
    src_metrics_dir = _SCRIPTS_DIR / "lib"
    if src_metrics_dir.is_dir():
        scripts_lib_dir = root / "scripts" / "lib"
        scripts_lib_dir.mkdir(parents=True, exist_ok=True)
        for metrics_script in ("metrics_aggregator.py", "metrics_csv_writer.py"):
            src = src_metrics_dir / metrics_script
            if src.is_file():
                (scripts_lib_dir / metrics_script).write_text(
                    src.read_text(encoding="utf-8"), encoding="utf-8"
                )

    return root


def _make_requirements_file(
    kanban_root: pathlib.Path,
    project_name: str,
    version: str,
    artifact_name: str = "test-document",
) -> pathlib.Path:
    """Create a requirements file in the project's requirements/ directory.

    finalize.sh scans requirements/ to find the file whose ## Target Version
    matches the version argument; it reads ## Artifact Name from that file
    to determine the published artifact filename.

    Args:
        kanban_root:   The temp kanban root.
        project_name:  Project to write the requirements file for.
        version:       Target version (e.g. "v0.0.1").
        artifact_name: Value for ## Artifact Name (used in artifact filename).

    Returns:
        pathlib.Path — the created requirements file.
    """
    req_dir = kanban_root / "projects" / project_name / "requirements"
    req_dir.mkdir(parents=True, exist_ok=True)
    slug = artifact_name.replace(" ", "-").lower()
    req_file = req_dir / f"{version}-{slug}.md"
    req_file.write_text(
        f"# {version}: {slug}\n\n"
        f"## Status\nrunning\n\n"
        f"## Target Version\n{version}\n\n"
        f"## Workflow Type\ndocument\n\n"
        f"## Artifact Name\n{artifact_name}\n\n"
        "## Output Formats\n- markdown\n\n"
        "## PM Task\nnone\n\n"
        "## Summary\nIntegration test requirements document.\n",
        encoding="utf-8",
    )
    return req_file


def _run_cm_script(
    script_path: pathlib.Path,
    args: list[str],
    kanban_root: pathlib.Path,
    project_name: str,
    *,
    extra_env: Optional[dict] = None,
    pgai_temp_dir: Optional[pathlib.Path] = None,
    timeout: int = 90,
) -> subprocess.CompletedProcess:
    """Run a CM script against a temporary kanban root.

    Sets PGAI_AGENT_KANBAN_ROOT_PATH, KANBAN_ROOT, TEAM_ROOT, and PGAI_PROJECT_NAME
    so the script operates against the caller's temp tree.

    PGAI_AGENT_KANBAN_TEMP_DIR is set to pgai_temp_dir (a path under tmp_path)
    so scripts that create working dirs use the framework temp root rather than
    bare /tmp.

    Args:
        script_path:   Absolute path to the CM script.
        args:          Additional arguments.
        kanban_root:   Temp kanban root.
        project_name:  Project to operate on.
        extra_env:     Additional env overrides (caller wins).
        pgai_temp_dir: Path to use as PGAI_AGENT_KANBAN_TEMP_DIR.  When None,
                       uses a 'pgai_temp' subdir of kanban_root.
        timeout:       Subprocess timeout in seconds.

    Returns:
        subprocess.CompletedProcess with returncode, stdout, stderr.
    """
    base_env = dict(os.environ)
    base_env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    base_env["KANBAN_ROOT"] = str(kanban_root)
    base_env["TEAM_ROOT"] = str(kanban_root)
    base_env["PGAI_PROJECT_NAME"] = project_name
    base_env.pop("PGAI_DEV_TREE_PATH", None)

    # Direct the framework temp root under tmp_path to avoid bare /tmp writes.
    _temp_root = pgai_temp_dir or (kanban_root.parent / "pgai_temp")
    _temp_root.mkdir(parents=True, exist_ok=True)
    base_env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(_temp_root)

    if extra_env:
        base_env.update(extra_env)

    return subprocess.run(
        ["bash", str(script_path)] + args,
        capture_output=True,
        text=True,
        env=base_env,
        cwd=str(kanban_root),
        timeout=timeout,
    )


def _read_release_state(kanban_root: pathlib.Path, project_name: str) -> str:
    """Read the project's release-state.md content."""
    return (kanban_root / "projects" / project_name / "release-state.md").read_text(
        encoding="utf-8"
    )


def _active_rc_value(release_state_text: str) -> str:
    """Extract the ## Active RC value from release-state.md content."""
    m = re.search(
        r"##\s*Active RC\s*\n(.*?)(?=\n##|\Z)",
        release_state_text,
        re.IGNORECASE | re.DOTALL,
    )
    return m.group(1).strip() if m else ""


def _last_released_value(release_state_text: str) -> str:
    """Extract the ## Last Released value from release-state.md content."""
    m = re.search(
        r"##\s*Last Released\s*\n(.*?)(?=\n##|\Z)",
        release_state_text,
        re.IGNORECASE | re.DOTALL,
    )
    return m.group(1).strip() if m else ""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOpenDocScratchDirectories:
    """cm-open-doc.sh creates the expected working directory structure."""

    def test_open_doc_creates_input_working_output_directories(
        self, tmp_path: pathlib.Path
    ) -> None:
        """cm-open-doc.sh creates input/, working/, and output/ dirs under the temp root.

        These directories are the pipeline's scratch space: WRITER reads from
        input/ and writes to working/; finalize packages from working/ to output/.
        All three must exist after cm-open-doc.sh completes.
        """
        version = "v0.0.1"
        project_name = "open-doc-dirs-test"
        pgai_temp = tmp_path / "pgai_temp"

        root = _build_doc_kanban_root(tmp_path, project_name)
        result = _run_cm_script(
            _OPEN_DOC_SCRIPT, [project_name, version],
            root, project_name,
            pgai_temp_dir=pgai_temp,
        )

        assert result.returncode == 0, (
            f"cm-open-doc.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Derive the expected working dir path (mirrors open-doc.sh logic).
        # Layout: PGAI_AGENT_KANBAN_TEMP_DIR/projects/<project>/doc/<version>/
        doc_scratch = pgai_temp / "projects" / project_name / "doc" / version
        assert (doc_scratch / "input").is_dir(), (
            f"Expected input/ at {doc_scratch / 'input'}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert (doc_scratch / "working").is_dir(), (
            f"Expected working/ at {doc_scratch / 'working'}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert (doc_scratch / "output").is_dir(), (
            f"Expected output/ at {doc_scratch / 'output'}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_open_doc_sets_active_rc_in_release_state(
        self, tmp_path: pathlib.Path
    ) -> None:
        """cm-open-doc.sh records the target version as Active RC in release-state.md.

        The discovery pipeline reads Active RC to defer bundling while a document
        version is in progress; open-doc.sh must write the version string here.
        """
        version = "v0.0.2"
        project_name = "open-doc-active-rc-test"
        pgai_temp = tmp_path / "pgai_temp"

        root = _build_doc_kanban_root(tmp_path, project_name)
        result = _run_cm_script(
            _OPEN_DOC_SCRIPT, [project_name, version],
            root, project_name,
            pgai_temp_dir=pgai_temp,
        )

        assert result.returncode == 0, (
            f"cm-open-doc.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        state_text = _read_release_state(root, project_name)
        active_rc = _active_rc_value(state_text)
        assert active_rc == version, (
            f"Expected Active RC = '{version}' in release-state.md after cm-open-doc.sh, "
            f"got '{active_rc}'.\nrelease-state.md:\n{state_text}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


class TestFinalizePipelineEnd:
    """cm-finalize.sh publishes the deliverable and updates project state."""

    def test_full_document_pipeline_publishes_artifact_to_artifacts_dir(
        self, tmp_path: pathlib.Path
    ) -> None:
        """open-doc → polish stub → finalize publishes the document to artifacts/.

        The canonical end-of-pipeline proof: a versioned artifact file must exist
        under projects/<project>/artifacts/ after finalize completes.  The artifact
        filename format is <version>-<artifact_name>.md.
        """
        version = "v0.0.1"
        project_name = "doc-pipeline-publish-test"
        artifact_name = "my-test-document"
        pgai_temp = tmp_path / "pgai_temp"

        root = _build_doc_kanban_root(tmp_path, project_name)
        _make_requirements_file(root, project_name, version, artifact_name)

        # Step 1: Open the document — creates working dirs, writes Active RC.
        open_result = _run_cm_script(
            _OPEN_DOC_SCRIPT, [project_name, version],
            root, project_name,
            pgai_temp_dir=pgai_temp,
        )
        assert open_result.returncode == 0, (
            f"cm-open-doc.sh failed.\nstdout: {open_result.stdout}\nstderr: {open_result.stderr}"
        )

        # Step 2: Simulate WRITER polish output.
        # finalize.sh reads polished.md from PGAI_WRITER_POLISH_TASK_ARTIFACTS.
        writer_artifacts = tmp_path / "writer_polish_artifacts"
        writer_artifacts.mkdir(parents=True, exist_ok=True)
        polished_md = writer_artifacts / "polished.md"
        polished_md.write_text(
            "# My Test Document\n\n"
            "This is the polished integration-test document.\n",
            encoding="utf-8",
        )

        # Step 3: Finalize — packages the polished content and publishes to artifacts/.
        finalize_result = _run_cm_script(
            _FINALIZE_SCRIPT, [project_name, version],
            root, project_name,
            pgai_temp_dir=pgai_temp,
            extra_env={
                "PGAI_WRITER_POLISH_TASK_ARTIFACTS": str(writer_artifacts),
                "PGAI_ARTIFACT_NAME": artifact_name,
            },
        )
        assert finalize_result.returncode == 0, (
            f"cm-finalize.sh failed.\nstdout: {finalize_result.stdout}\n"
            f"stderr: {finalize_result.stderr}"
        )

        # Assert: the published artifact exists in projects/<project>/artifacts/.
        expected_artifact = (
            root / "projects" / project_name / "artifacts" / f"{version}-{artifact_name}.md"
        )
        assert expected_artifact.is_file(), (
            f"Expected published artifact at {expected_artifact}.\n"
            f"artifacts/ contents: {list((root / 'projects' / project_name / 'artifacts').iterdir())}\n"
            f"finalize stdout: {finalize_result.stdout}\nfinalize stderr: {finalize_result.stderr}"
        )

    def test_full_document_pipeline_artifact_contains_polished_content(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The artifact published by finalize contains the WRITER's polished content.

        finalize must copy the polished content faithfully — not an empty file,
        not a template, not a placeholder.  The artifact content must match what
        the WRITER produced.
        """
        version = "v0.0.1"
        project_name = "doc-pipeline-content-test"
        artifact_name = "content-check-doc"
        pgai_temp = tmp_path / "pgai_temp"

        root = _build_doc_kanban_root(tmp_path, project_name)
        _make_requirements_file(root, project_name, version, artifact_name)

        open_result = _run_cm_script(
            _OPEN_DOC_SCRIPT, [project_name, version],
            root, project_name,
            pgai_temp_dir=pgai_temp,
        )
        assert open_result.returncode == 0, (
            f"cm-open-doc.sh failed.\nstdout: {open_result.stdout}\nstderr: {open_result.stderr}"
        )

        writer_artifacts = tmp_path / "writer_polish_artifacts"
        writer_artifacts.mkdir(parents=True, exist_ok=True)
        sentinel_text = "SENTINEL_CONTENT_FOR_INTEGRATION_TEST"
        (writer_artifacts / "polished.md").write_text(
            f"# Content Check\n\n{sentinel_text}\n",
            encoding="utf-8",
        )

        finalize_result = _run_cm_script(
            _FINALIZE_SCRIPT, [project_name, version],
            root, project_name,
            pgai_temp_dir=pgai_temp,
            extra_env={
                "PGAI_WRITER_POLISH_TASK_ARTIFACTS": str(writer_artifacts),
                "PGAI_ARTIFACT_NAME": artifact_name,
            },
        )
        assert finalize_result.returncode == 0, (
            f"cm-finalize.sh failed.\nstdout: {finalize_result.stdout}\n"
            f"stderr: {finalize_result.stderr}"
        )

        artifact = root / "projects" / project_name / "artifacts" / f"{version}-{artifact_name}.md"
        assert artifact.is_file(), (
            f"Expected artifact at {artifact}.\nstdout: {finalize_result.stdout}"
        )

        artifact_text = artifact.read_text(encoding="utf-8")
        assert sentinel_text in artifact_text, (
            f"Expected artifact to contain polished content sentinel '{sentinel_text}'.\n"
            f"Actual artifact content:\n{artifact_text}"
        )

    def test_finalize_clears_active_rc_after_publishing(
        self, tmp_path: pathlib.Path
    ) -> None:
        """cm-finalize.sh clears Active RC to 'none' in release-state.md after publishing.

        Active RC being cleared is the signal that allows the discovery pipeline to
        bundle new requirements for the next document version.  It must be 'none'
        after finalize, mirroring what cm-release.sh does for the release workflow.
        """
        version = "v0.0.1"
        project_name = "doc-active-rc-clear-test"
        artifact_name = "rc-clear-check-doc"
        pgai_temp = tmp_path / "pgai_temp"

        root = _build_doc_kanban_root(tmp_path, project_name)
        _make_requirements_file(root, project_name, version, artifact_name)

        _run_cm_script(
            _OPEN_DOC_SCRIPT, [project_name, version],
            root, project_name,
            pgai_temp_dir=pgai_temp,
        )

        # Confirm Active RC was set by open-doc.
        state_after_open = _read_release_state(root, project_name)
        assert _active_rc_value(state_after_open) == version, (
            f"Expected Active RC = '{version}' after cm-open-doc.sh.\n"
            f"release-state.md:\n{state_after_open}"
        )

        writer_artifacts = tmp_path / "writer_polish_artifacts"
        writer_artifacts.mkdir(parents=True, exist_ok=True)
        (writer_artifacts / "polished.md").write_text(
            "# RC Clear Test\n\nContent.\n", encoding="utf-8"
        )

        finalize_result = _run_cm_script(
            _FINALIZE_SCRIPT, [project_name, version],
            root, project_name,
            pgai_temp_dir=pgai_temp,
            extra_env={
                "PGAI_WRITER_POLISH_TASK_ARTIFACTS": str(writer_artifacts),
                "PGAI_ARTIFACT_NAME": artifact_name,
            },
        )
        assert finalize_result.returncode == 0, (
            f"cm-finalize.sh failed.\nstdout: {finalize_result.stdout}\n"
            f"stderr: {finalize_result.stderr}"
        )

        state_after_finalize = _read_release_state(root, project_name)
        active_rc = _active_rc_value(state_after_finalize)
        assert active_rc == "none", (
            f"Expected Active RC = 'none' after cm-finalize.sh, got '{active_rc}'.\n"
            f"release-state.md after finalize:\n{state_after_finalize}\n"
            f"finalize stdout: {finalize_result.stdout}\nfinalize stderr: {finalize_result.stderr}"
        )

    def test_finalize_records_last_released_version_in_release_state(
        self, tmp_path: pathlib.Path
    ) -> None:
        """cm-finalize.sh records the published version in ## Last Released.

        Document projects have no git repo, so release-state.md is the canonical
        record of what version was last published.  ## Last Released must be set to
        the version string after finalize completes.
        """
        version = "v0.0.1"
        project_name = "doc-last-released-test"
        artifact_name = "last-released-doc"
        pgai_temp = tmp_path / "pgai_temp"

        root = _build_doc_kanban_root(tmp_path, project_name)
        _make_requirements_file(root, project_name, version, artifact_name)

        _run_cm_script(
            _OPEN_DOC_SCRIPT, [project_name, version],
            root, project_name,
            pgai_temp_dir=pgai_temp,
        )

        writer_artifacts = tmp_path / "writer_polish_artifacts"
        writer_artifacts.mkdir(parents=True, exist_ok=True)
        (writer_artifacts / "polished.md").write_text(
            "# Last Released Test\n\nContent.\n", encoding="utf-8"
        )

        finalize_result = _run_cm_script(
            _FINALIZE_SCRIPT, [project_name, version],
            root, project_name,
            pgai_temp_dir=pgai_temp,
            extra_env={
                "PGAI_WRITER_POLISH_TASK_ARTIFACTS": str(writer_artifacts),
                "PGAI_ARTIFACT_NAME": artifact_name,
            },
        )
        assert finalize_result.returncode == 0, (
            f"cm-finalize.sh failed.\nstdout: {finalize_result.stdout}\n"
            f"stderr: {finalize_result.stderr}"
        )

        state_text = _read_release_state(root, project_name)
        last_released = _last_released_value(state_text)
        assert last_released == version, (
            f"Expected Last Released = '{version}' in release-state.md after finalize, "
            f"got '{last_released}'.\nrelease-state.md:\n{state_text}\n"
            f"finalize stdout: {finalize_result.stdout}\nfinalize stderr: {finalize_result.stderr}"
        )


class TestFinalizeGuardBehavior:
    """cm-finalize.sh guard conditions: missing env var, missing polished.md."""

    def test_finalize_exits_nonzero_when_writer_artifacts_env_not_set(
        self, tmp_path: pathlib.Path
    ) -> None:
        """cm-finalize.sh fails when PGAI_WRITER_POLISH_TASK_ARTIFACTS is not set.

        finalize must not fall back silently to any working-dir content when the
        WRITER artifacts env var is missing — it must fail loudly so the operator
        knows the pipeline is misconfigured.
        """
        version = "v0.0.1"
        project_name = "finalize-no-env-test"
        pgai_temp = tmp_path / "pgai_temp"

        root = _build_doc_kanban_root(tmp_path, project_name)
        _make_requirements_file(root, project_name, version)

        _run_cm_script(
            _OPEN_DOC_SCRIPT, [project_name, version],
            root, project_name,
            pgai_temp_dir=pgai_temp,
        )

        # Run finalize WITHOUT PGAI_WRITER_POLISH_TASK_ARTIFACTS.
        result = _run_cm_script(
            _FINALIZE_SCRIPT, [project_name, version],
            root, project_name,
            pgai_temp_dir=pgai_temp,
            # Deliberately omit PGAI_WRITER_POLISH_TASK_ARTIFACTS.
        )

        assert result.returncode != 0, (
            "Expected cm-finalize.sh to exit non-zero when PGAI_WRITER_POLISH_TASK_ARTIFACTS "
            "is not set.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert "PGAI_WRITER_POLISH_TASK_ARTIFACTS" in combined, (
            "Expected error output to reference PGAI_WRITER_POLISH_TASK_ARTIFACTS.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_finalize_exits_nonzero_when_polished_md_is_absent(
        self, tmp_path: pathlib.Path
    ) -> None:
        """cm-finalize.sh fails when polished.md is missing from the writer artifacts dir.

        PGAI_WRITER_POLISH_TASK_ARTIFACTS may point at an existing directory, but
        if polished.md does not exist within it, finalize must fail — not silently
        skip or publish an empty artifact.
        """
        version = "v0.0.1"
        project_name = "finalize-no-polished-test"
        pgai_temp = tmp_path / "pgai_temp"

        root = _build_doc_kanban_root(tmp_path, project_name)
        _make_requirements_file(root, project_name, version)

        _run_cm_script(
            _OPEN_DOC_SCRIPT, [project_name, version],
            root, project_name,
            pgai_temp_dir=pgai_temp,
        )

        # Create the artifacts dir but intentionally omit polished.md.
        writer_artifacts = tmp_path / "writer_polish_artifacts_empty"
        writer_artifacts.mkdir(parents=True, exist_ok=True)

        result = _run_cm_script(
            _FINALIZE_SCRIPT, [project_name, version],
            root, project_name,
            pgai_temp_dir=pgai_temp,
            extra_env={
                "PGAI_WRITER_POLISH_TASK_ARTIFACTS": str(writer_artifacts),
            },
        )

        assert result.returncode != 0, (
            "Expected cm-finalize.sh to exit non-zero when polished.md is absent.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert "polished.md" in combined, (
            "Expected error output to reference 'polished.md'.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
