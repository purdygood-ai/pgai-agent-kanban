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
    or gate tokens appear in function names (roles/TESTER.md Anti-pattern 6).
  - create-project.sh is invoked via subprocess against a temp kanban root built
    by _build_minimal_kanban_root(), which creates only what the script requires
    (an existing KANBAN_ROOT dir).  The script creates projects.cfg itself when
    absent (via projects_cfg_ensure).
"""

from __future__ import annotations

import os
import pathlib
import re
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


def _extract_release_section(cfg_text: str) -> str:
    """Return the [release] section body from a generated project.cfg.

    Slices from the real [release] INI header (not a mention of "[release]"
    inside a preceding comment line) to the next section header or EOF.
    """
    release_header = re.search(r"^\[release\]\s*$", cfg_text, re.MULTILINE)
    assert release_header is not None, (
        f"Expected a [release] section header in generated project.cfg.\ncfg content:\n{cfg_text}"
    )
    release_start = release_header.end()
    next_section = re.search(r"^\[\w+\]\s*$", cfg_text[release_start:], re.MULTILINE)
    release_end = (
        release_start + next_section.start() if next_section else len(cfg_text)
    )
    return cfg_text[release_start:release_end]


def _extract_deep_verify_comment(release_section: str) -> str:
    """Return the deep_verify key's descriptive comment as one joined string.

    Collects every commented line starting at '# deep_verify:' up to (but not
    including) the commented-out '# deep_verify = ...' example line, strips
    the leading '#' from each, and joins with spaces -- so a sentence the
    generator wraps across two physical comment lines reads as one string a
    regex can search without caring where the line break fell.
    """
    lines = release_section.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip().startswith("# deep_verify:"):
            start = i
            break
    assert start is not None, (
        f"Expected a '# deep_verify:' comment line in the [release] section.\n{release_section}"
    )
    collected = []
    for line in lines[start:]:
        stripped = line.strip()
        if re.match(r"^#\s*deep_verify\s*=", stripped):
            break
        collected.append(stripped.lstrip("#").strip())
    return " ".join(collected)


def _parse_accepted_deep_verify_values(comment_text: str) -> list[str]:
    """Derive the raw deep_verify spellings the comment claims are accepted.

    Parses the 'Accepted: A|B (C|D also accepted as aliases: C=A, D=B)'
    sentence out of the comment text itself, rather than a value list copied
    into the test -- a test carrying its own copy of the accepted set is a
    third place for the vocabulary to drift, which is what this test exists
    to prevent rather than reproduce. Returns the base word pair first (e.g.
    ["weekly", "off"]), then the aliased pair the comment separately names
    (e.g. ["true", "false"]).
    """
    base_match = re.search(r"Accepted:\s*(\w+)\|(\w+)", comment_text)
    assert base_match is not None, (
        "Could not find an 'Accepted: X|Y' pair in the deep_verify comment; "
        "if the comment's shape changed, update this parser to match.\n"
        f"comment text: {comment_text!r}"
    )
    values = [base_match.group(1), base_match.group(2)]

    alias_match = re.search(
        r"(\w+)\|(\w+)\s+also accepted as aliases:\s*\w+=\w+,\s*\w+=\w+",
        comment_text,
    )
    assert alias_match is not None, (
        "Could not find the '<word>|<word> also accepted as aliases: "
        "X=Y, Z=W' clause in the deep_verify comment; if the comment's "
        "shape changed, update this parser to match.\n"
        f"comment text: {comment_text!r}"
    )
    values.extend([alias_match.group(1), alias_match.group(2)])

    return values


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


_SOP_CORE_MD = _TEAM_DIR / "SOP-core.md"

# Markers inside the queue-file legend heredoc are written as bash-escaped
# backtick-code spans, e.g. \`[B]\` -- capture the bracketed marker only.
_MARKER_RE = re.compile(r"`\[([^\]]*)\]`")


def _markers_in_text(text: str) -> set[str]:
    return set(_MARKER_RE.findall(text))


def _sop_core_marker_table_section(sop_text: str) -> str:
    """Return just the 'Per-Agent Queues and Marker Correctness' section's
    marker table, so marker-shaped strings elsewhere in SOP-core.md (queue
    line examples, prose) never leak into the comparison."""
    start = sop_text.index("| Marker | State | Written by |")
    end = sop_text.index("\n\n", start)
    return sop_text[start:end]


class TestCreateProjectQueueMarkerLegend:
    """create-project.sh's generated queue-file legend names all five
    markers and stays bound to team/SOP-core.md's own marker
    table -- the canonical five-row reference the legend must match.
    """

    def test_coder_backlog_legend_names_the_blocked_marker(
        self, tmp_path: pathlib.Path
    ) -> None:
        """grep -q '\\[B\\]' <root>/projects/<name>/tasks/queues/coder_backlog.md
        succeeds -- the wake layer's documented BLOCKED escalation marker is
        present in the legend a freshly created project ships with."""
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "marker-legend-test", workflow_type="release")
        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        coder_backlog = (
            _project_dir(root, "marker-legend-test") / "tasks" / "queues" / "coder_backlog.md"
        )
        assert coder_backlog.is_file(), f"Expected coder_backlog.md at {coder_backlog}"

        grep_result = subprocess.run(
            ["grep", "-q", r"\[B\]", str(coder_backlog)],
            capture_output=True,
            text=True,
        )
        assert grep_result.returncode == 0, (
            f"Expected '[B]' to appear in the generated legend at {coder_backlog}.\n"
            f"Content:\n{coder_backlog.read_text(encoding='utf-8')}"
        )

    def test_generated_legend_names_the_same_markers_as_sop_core_table(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The five markers the generated queue-file legend documents are
        exactly the five markers team/SOP-core.md's own marker table names
        -- the legend must never drift from the canonical reference."""
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "marker-legend-binding", workflow_type="release")
        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        coder_backlog = (
            _project_dir(root, "marker-legend-binding") / "tasks" / "queues" / "coder_backlog.md"
        )
        legend_markers = _markers_in_text(coder_backlog.read_text(encoding="utf-8"))

        sop_text = _SOP_CORE_MD.read_text(encoding="utf-8")
        table_markers = _markers_in_text(_sop_core_marker_table_section(sop_text))

        assert legend_markers == table_markers, (
            f"Generated legend markers {sorted(legend_markers)} do not match "
            f"team/SOP-core.md's marker table {sorted(table_markers)}."
        )
        # Pin down the expected five explicitly too, so a table edit that
        # accidentally drops a row (making both sides agree on a WRONG,
        # smaller set) still fails this test.
        assert legend_markers == {" ", "W", "A", "B", "x"}, (
            f"Expected exactly the five documented markers, got {sorted(legend_markers)}"
        )


class TestCreateProjectDevTreeAndGitRepoFlags:
    """create-project.sh --dev-tree/--git-repo (and their aliases) write project.cfg fields.

    Regression coverage: the flags were documented in the script header and --help
    but silently ignored — the generated project.cfg always carried empty
    dev_tree_path/git_repo_url fields regardless of what was passed on the
    command line.
    """

    @pytest.mark.parametrize(
        "dev_tree_flag, git_repo_flag, dir_name, project_name",
        [
            pytest.param(
                "--dev-tree", "--git-repo",
                "some-dev-tree", "flags-verbatim-test",
                id="canonical_spellings",
            ),
            pytest.param(
                "--dev-tree-path", "--git-repo-url",
                "aliased-dev-tree", "flags-alias-test",
                id="documented_aliases",
            ),
        ],
    )
    def test_dev_tree_and_git_repo_flag_spellings_write_values_verbatim(
        self,
        tmp_path: pathlib.Path,
        dev_tree_flag: str,
        git_repo_flag: str,
        dir_name: str,
        project_name: str,
    ) -> None:
        """Each documented --dev-tree/--git-repo flag spelling writes project.cfg verbatim.

        All four spellings (--dev-tree/--dev-tree-path and
        --git-repo/--git-repo-url) are required to work identically: both the
        canonical flags and their documented aliases must write the
        operator's exact string into the same project.cfg fields, with no
        transformation or normalization.
        """
        root = _build_minimal_kanban_root(tmp_path)
        dev_tree = tmp_path / dir_name
        dev_tree.mkdir()
        git_repo_url = f"git@example.com:org/{project_name}.git"

        result = _run_create_project(
            root,
            project_name,
            workflow_type="release",
            extra_args=[
                dev_tree_flag, str(dev_tree),
                git_repo_flag, git_repo_url,
            ],
        )
        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        cfg_text = _read_project_cfg(root, project_name)
        assert f"dev_tree_path = {dev_tree}" in cfg_text, (
            f"Expected dev_tree_path = {dev_tree} in project.cfg.\ncfg content:\n{cfg_text}"
        )
        assert f"git_repo_url = {git_repo_url}" in cfg_text, (
            f"Expected git_repo_url to carry the supplied URL verbatim.\ncfg content:\n{cfg_text}"
        )

    def test_omitted_flags_produce_empty_fields_with_edit_manually_guidance(
        self, tmp_path: pathlib.Path
    ) -> None:
        """With neither flag, dev_tree_path/git_repo_url stay empty (back-compat).

        This is the byte-identical-to-today contract: omitting the flags must
        reproduce the flagless output exactly -- empty fields plus the
        edit-manually guidance comment.
        """
        root = _build_minimal_kanban_root(tmp_path)

        result = _run_create_project(root, "flags-omitted-test", workflow_type="release")
        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        cfg_text = _read_project_cfg(root, "flags-omitted-test")
        assert "dev_tree_path =\n" in cfg_text, (
            f"Expected dev_tree_path to be written empty when --dev-tree is omitted.\n"
            f"cfg content:\n{cfg_text}"
        )
        assert "git_repo_url =\n" in cfg_text, (
            f"Expected git_repo_url to be written empty when --git-repo is omitted.\n"
            f"cfg content:\n{cfg_text}"
        )
        assert "IMPORTANT: dev_tree_path and git_repo_url are intentionally empty." in cfg_text, (
            f"Expected the edit-manually guidance comment to be retained for the "
            f"omitted-flag case.\ncfg content:\n{cfg_text}"
        )

    def test_nonexistent_dev_tree_path_warns_but_still_writes_and_exits_zero(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A --dev-tree path that does not exist yet still succeeds, with a WARN.

        Projects are often registered before their dev tree is cloned, so a
        missing path is not an error -- the script names the path in a WARN
        and writes the value into project.cfg anyway, exiting 0.
        """
        root = _build_minimal_kanban_root(tmp_path)
        missing_path = tmp_path / "not-cloned-yet" / "dev-tree"

        result = _run_create_project(
            root,
            "flags-missing-path-test",
            workflow_type="release",
            extra_args=["--dev-tree", str(missing_path)],
        )
        assert result.returncode == 0, (
            f"Expected exit 0 for a nonexistent --dev-tree path (WARN, not fail).\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "WARN" in result.stderr.upper(), (
            f"Expected a WARN naming the nonexistent path on stderr.\nstderr: {result.stderr}"
        )
        assert str(missing_path) in result.stderr, (
            f"Expected the WARN to name the nonexistent path.\nstderr: {result.stderr}"
        )

        cfg_text = _read_project_cfg(root, "flags-missing-path-test")
        assert f"dev_tree_path = {missing_path}" in cfg_text, (
            f"Expected the nonexistent path to be written verbatim into project.cfg anyway.\n"
            f"cfg content:\n{cfg_text}"
        )

    def test_empty_dev_tree_flag_value_is_rejected(
        self, tmp_path: pathlib.Path
    ) -> None:
        """--dev-tree= (explicit empty value) is rejected -- non-empty check only.

        The acceptance criteria call for "no validation beyond non-empty";
        an explicitly empty value must still be refused.
        """
        root = _build_minimal_kanban_root(tmp_path)

        result = _run_create_project(
            root,
            "flags-empty-value-test",
            workflow_type="release",
            extra_args=["--dev-tree="],
        )
        assert result.returncode != 0, (
            f"Expected create-project.sh to reject an explicitly empty --dev-tree value.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "non-empty" in result.stderr.lower(), (
            f"Expected the error to name the non-empty requirement.\nstderr: {result.stderr}"
        )

    def test_help_names_all_four_dev_tree_and_git_repo_flag_spellings(
        self, tmp_path: pathlib.Path
    ) -> None:
        """--help documents --dev-tree, --dev-tree-path, --git-repo, --git-repo-url.

        The flags must be documented as real (not silently
        inert); --help is the operator-facing contract that must name all
        four spellings.
        """
        root = _build_minimal_kanban_root(tmp_path)
        base_env = dict(os.environ)
        base_env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)

        result = subprocess.run(
            ["bash", str(_CREATE_PROJECT_SCRIPT), "--help"],
            capture_output=True,
            text=True,
            env=base_env,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"--help must exit 0.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        for flag in ("--dev-tree", "--dev-tree-path", "--git-repo", "--git-repo-url"):
            assert flag in result.stdout, (
                f"Expected --help to name {flag}.\nstdout: {result.stdout}"
            )


class TestCreateProjectDeepVerifyReleaseSection:
    """create-project.sh scaffolds project.cfg's [release] DEEP-VERIFY keys.

    Regression coverage: create-project.sh previously emitted no [release]
    section at all, so an operator reading the file at the one moment they
    are looking at it never learned that deep_verify and
    deep_verify_offhours_window exist. Both keys must land commented out --
    resolution behavior for every project (new or existing) is unchanged;
    this closes only the discoverability gap.
    """

    def test_generated_project_cfg_has_release_section_with_commented_deep_verify_keys(
        self, tmp_path: pathlib.Path
    ) -> None:
        """project.cfg written by create-project.sh names both DEEP-VERIFY keys, commented, under [release]."""
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "deep-verify-cfg-test", workflow_type="release")

        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        cfg_text = _read_project_cfg(root, "deep-verify-cfg-test")

        # Find the ACTUAL [release] section header line, not a mention of
        # "[release]" inside a preceding comment line (e.g. a header comment
        # like "# [release] -- DEEP-VERIFY ..."). A real INI header sits
        # alone on its own line.
        release_header = re.search(r"^\[release\]\s*$", cfg_text, re.MULTILINE)
        assert release_header is not None, (
            f"Expected a [release] section header in generated project.cfg.\ncfg content:\n{cfg_text}"
        )

        # Both keys must land inside [release] specifically, not merely
        # appear somewhere in the file -- slice from the [release] header to
        # the next section header (or EOF) and search only there.
        release_start = release_header.end()
        next_section = re.search(r"^\[\w+\]\s*$", cfg_text[release_start:], re.MULTILINE)
        release_end = (
            release_start + next_section.start() if next_section else len(cfg_text)
        )
        release_section = cfg_text[release_start:release_end]

        assert "# deep_verify" in release_section, (
            f"Expected a commented 'deep_verify' key inside [release].\n[release] content:\n{release_section}"
        )
        assert "# deep_verify_offhours_window" in release_section, (
            f"Expected a commented 'deep_verify_offhours_window' key inside [release].\n"
            f"[release] content:\n{release_section}"
        )
        # Neither key may be written uncommented -- an uncommented key would
        # change resolution for every newly created project, which is out of
        # scope for a discoverability fix.
        assert "\ndeep_verify = " not in release_section, (
            f"deep_verify must be commented out in the generated [release] section.\n{release_section}"
        )
        assert "\ndeep_verify_offhours_window = " not in release_section, (
            f"deep_verify_offhours_window must be commented out in the generated [release] section.\n"
            f"{release_section}"
        )

    def test_generated_deep_verify_comment_names_accepted_values_and_default(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The generated comments name weekly|off / 03:00-03:59 defaults verbatim.

        These must agree with what pp_deep_verify_mode and
        pp_deep_verify_offhours_window (team/scripts/lib/project_paths.sh)
        actually return for a project whose cfg omits the key.
        """
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "deep-verify-comment-test", workflow_type="release")
        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        cfg_text = _read_project_cfg(root, "deep-verify-comment-test")
        assert "weekly|off" in cfg_text, (
            f"Expected accepted values 'weekly|off' named in the generated comment.\ncfg content:\n{cfg_text}"
        )
        assert "Default: weekly" in cfg_text, (
            f"Expected 'Default: weekly' named in the generated comment.\ncfg content:\n{cfg_text}"
        )
        assert "HH:MM-HH:MM" in cfg_text, (
            f"Expected the HH:MM-HH:MM (UTC) form named in the generated comment.\ncfg content:\n{cfg_text}"
        )
        assert "Default: 03:00-03:59" in cfg_text, (
            f"Expected 'Default: 03:00-03:59' named in the generated comment.\ncfg content:\n{cfg_text}"
        )

    def test_deep_verify_resolvers_return_shipped_defaults_for_generated_project(
        self, tmp_path: pathlib.Path
    ) -> None:
        """pp_deep_verify_mode/pp_deep_verify_offhours_window resolve to the
        shipped defaults for a project created by the modified script --
        proving the newly commented-out keys changed no resolution behavior.
        """
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "deep-verify-resolve-test", workflow_type="release")
        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        env = dict(os.environ)
        env["KANBAN_ROOT"] = str(root)
        env["PGAI_PROJECT_NAME"] = "deep-verify-resolve-test"

        mode_result = subprocess.run(
            ["bash", "-c", "source scripts/lib/project_paths.sh && pp_deep_verify_mode"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(_TEAM_DIR),
            timeout=30,
        )
        assert mode_result.returncode == 0, (
            f"pp_deep_verify_mode failed.\nstdout: {mode_result.stdout}\nstderr: {mode_result.stderr}"
        )
        assert mode_result.stdout.strip() == "weekly", (
            f"Expected pp_deep_verify_mode to resolve to 'weekly'.\nstdout: {mode_result.stdout}"
        )

        window_result = subprocess.run(
            ["bash", "-c", "source scripts/lib/project_paths.sh && pp_deep_verify_offhours_window"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(_TEAM_DIR),
            timeout=30,
        )
        assert window_result.returncode == 0, (
            f"pp_deep_verify_offhours_window failed.\n"
            f"stdout: {window_result.stdout}\nstderr: {window_result.stderr}"
        )
        assert window_result.stdout.strip() == "03:00-03:59", (
            f"Expected pp_deep_verify_offhours_window to resolve to '03:00-03:59'.\n"
            f"stdout: {window_result.stdout}"
        )

    def test_every_value_the_generated_deep_verify_comment_advertises_resolves_through_the_reader(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Every raw spelling the generated deep_verify comment claims is
        accepted actually resolves through pp_deep_verify_mode.

        The accepted-value list under test is parsed out of the comment
        create-project.sh writes into project.cfg -- not a list hardcoded
        here -- so a future edit that widens or narrows either the comment
        or the reader without the other fails this test instead of shipping
        a scaffolded project.cfg that promises a value its own reader
        refuses.
        """
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "deep-verify-alias-test", workflow_type="release")
        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        cfg_text = _read_project_cfg(root, "deep-verify-alias-test")
        release_section = _extract_release_section(cfg_text)
        comment_text = _extract_deep_verify_comment(release_section)
        accepted_values = _parse_accepted_deep_verify_values(comment_text)

        assert len(accepted_values) == len(set(accepted_values)) and len(accepted_values) >= 2, (
            f"Expected at least two distinct accepted values parsed from the "
            f"deep_verify comment.\nparsed: {accepted_values}\ncomment text: {comment_text!r}"
        )

        project_cfg_path = _project_dir(root, "deep-verify-alias-test") / "project.cfg"
        env = dict(os.environ)
        env["KANBAN_ROOT"] = str(root)
        env["PGAI_PROJECT_NAME"] = "deep-verify-alias-test"

        for raw_value in accepted_values:
            # Uncomment and set the commented-out example line so the raw
            # value the comment advertises is the value the reader actually
            # sees -- exercising the same [release] deep_verify = <value>
            # shape an operator would type after reading the comment.
            cfg_with_value, substitutions = re.subn(
                r"^# deep_verify = \S+\s*$",
                f"deep_verify = {raw_value}",
                cfg_text,
                count=1,
                flags=re.MULTILINE,
            )
            assert substitutions == 1, (
                f"Failed to uncomment/set deep_verify in project.cfg for value {raw_value!r}.\n"
                f"cfg content:\n{cfg_text}"
            )
            project_cfg_path.write_text(cfg_with_value, encoding="utf-8")

            mode_result = subprocess.run(
                ["bash", "-c", "source scripts/lib/project_paths.sh && pp_deep_verify_mode"],
                capture_output=True,
                text=True,
                env=env,
                cwd=str(_TEAM_DIR),
                timeout=30,
            )
            assert mode_result.returncode == 0, (
                f"pp_deep_verify_mode refused the value {raw_value!r}, which the "
                f"generated project.cfg comment advertises as accepted.\n"
                f"stdout: {mode_result.stdout}\nstderr: {mode_result.stderr}"
            )
            assert mode_result.stdout.strip() in ("weekly", "off"), (
                f"Expected pp_deep_verify_mode to print 'weekly' or 'off' for "
                f"{raw_value!r}, got {mode_result.stdout.strip()!r}."
            )


def _live_intent_entry_lines(intent_text: str) -> list[str]:
    """Return every unfenced '- YYYY-MM-DD: ...' bullet line in INTENT.md text.

    Strips fenced code blocks (paired ``` lines) before scanning, since the
    template's worked example is deliberately fenced so it is never counted
    as a live entry -- the documented marker keeps it out of any reader of
    live entries. A line only counts as a live
    entry when it sits outside any fence and matches the shipped grammar.
    """
    lines = []
    in_fence = False
    for line in intent_text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if re.match(r"^-\s+\d{4}-\d{2}-\d{2}:", line.strip()):
            lines.append(line)
    return lines


class TestCreateProjectOverwatchResolveScaffolding:
    """create-project.sh seeds the commented overwatch_resolve key and a
    zero-live-entry INTENT.md template -- OVERWATCH's autonomous block
    resolution ships dark on every new project until an operator
    deliberately turns both on: uncommenting overwatch_resolve in
    project.cfg AND adding a real entry to INTENT.md.
    """

    def test_generated_project_cfg_has_commented_overwatch_resolve_key(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The generated [release] section names overwatch_resolve, commented out."""
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "overwatch-cfg-test", workflow_type="release")
        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        cfg_text = _read_project_cfg(root, "overwatch-cfg-test")
        release_section = _extract_release_section(cfg_text)

        assert "# overwatch_resolve" in release_section, (
            f"Expected a commented 'overwatch_resolve' key inside [release].\n"
            f"[release] content:\n{release_section}"
        )
        assert "\noverwatch_resolve = " not in release_section, (
            f"overwatch_resolve must be commented out in a freshly generated "
            f"[release] section -- a fresh project must never ship live.\n{release_section}"
        )
        assert "Accepted: true|false" in release_section, (
            f"Expected 'Accepted: true|false' named in the generated comment.\n{release_section}"
        )
        assert "Default: false" in release_section, (
            f"Expected 'Default: false' named in the generated comment.\n{release_section}"
        )

    def test_overwatch_resolve_resolves_false_for_a_freshly_created_project(
        self, tmp_path: pathlib.Path
    ) -> None:
        """pp_overwatch_resolve resolves to 'false' for a project create-project.sh
        just generated -- proving the commented-out key changes no resolution
        behavior, exactly like the deep_verify keys beside it.
        """
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "overwatch-resolve-test", workflow_type="release")
        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        env = dict(os.environ)
        env["KANBAN_ROOT"] = str(root)
        env["PGAI_PROJECT_NAME"] = "overwatch-resolve-test"

        resolve_result = subprocess.run(
            ["bash", "-c", "source scripts/lib/project_paths.sh && pp_overwatch_resolve"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(_TEAM_DIR),
            timeout=30,
        )
        assert resolve_result.returncode == 0, (
            f"pp_overwatch_resolve failed.\nstdout: {resolve_result.stdout}\nstderr: {resolve_result.stderr}"
        )
        assert resolve_result.stdout.strip() == "false", (
            f"Expected pp_overwatch_resolve to resolve to 'false' for a freshly "
            f"created project.\nstdout: {resolve_result.stdout}"
        )

    def test_generated_project_has_intent_md_at_project_root(
        self, tmp_path: pathlib.Path
    ) -> None:
        """create-project.sh seeds INTENT.md at the project root (not a subdir)."""
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "intent-file-test", workflow_type="release")
        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        intent_path = _project_dir(root, "intent-file-test") / "INTENT.md"
        assert intent_path.is_file(), (
            f"Expected INTENT.md to be seeded at {intent_path}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_generated_intent_md_has_zero_live_entries(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The shipped INTENT.md carries its worked example fenced -- outside
        the fence, there is no bullet matching the live-entry grammar.
        """
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "intent-zero-entries-test", workflow_type="release")
        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        intent_text = (
            _project_dir(root, "intent-zero-entries-test") / "INTENT.md"
        ).read_text(encoding="utf-8")

        live_entries = _live_intent_entry_lines(intent_text)
        assert live_entries == [], (
            f"Expected zero live entries in the shipped INTENT.md template, "
            f"found: {live_entries}\nfull text:\n{intent_text}"
        )

    def test_generated_intent_md_marks_its_worked_example_unmistakably(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The one worked example in INTENT.md is fenced and labeled as an example,
        so a human reader (or a parser of live entries) cannot mistake it for a
        live, operator-authored entry.
        """
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "intent-example-test", workflow_type="release")
        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        intent_text = (
            _project_dir(root, "intent-example-test") / "INTENT.md"
        ).read_text(encoding="utf-8")

        assert "```" in intent_text, (
            f"Expected the worked example to be fenced with a code block.\n{intent_text}"
        )
        assert re.search(r"EXAMPLE", intent_text), (
            f"Expected the worked example to be labeled unmistakably as an example.\n{intent_text}"
        )

    def test_document_workflow_project_also_gets_overwatch_scaffolding(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A document-workflow project gets the same commented key and INTENT.md
        as a release-workflow project -- OVERWATCH's CM-block resolution is not
        release-workflow-specific scaffolding.
        """
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "intent-doc-test", workflow_type="document")
        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        cfg_text = _read_project_cfg(root, "intent-doc-test")
        release_section = _extract_release_section(cfg_text)
        assert "# overwatch_resolve" in release_section, (
            f"Expected a commented 'overwatch_resolve' key for a document-workflow "
            f"project too.\n[release] content:\n{release_section}"
        )

        intent_path = _project_dir(root, "intent-doc-test") / "INTENT.md"
        assert intent_path.is_file(), (
            f"Expected INTENT.md to be seeded for a document-workflow project too "
            f"at {intent_path}."
        )

    def test_generated_project_has_overwatch_backlog_queue_file(
        self, tmp_path: pathlib.Path
    ) -> None:
        """create-project.sh seeds tasks/queues/overwatch_backlog.md.

        OVERWATCH is horizontal, not part of any workflow's agent roster, so
        this file is not driven by queue-files.list -- it must exist
        regardless, exactly like INTENT.md and the commented
        overwatch_resolve key above.
        """
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "overwatch-backlog-test", workflow_type="release")
        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        backlog_path = (
            _project_dir(root, "overwatch-backlog-test") / "tasks" / "queues" / "overwatch_backlog.md"
        )
        assert backlog_path.is_file(), (
            f"Expected tasks/queues/overwatch_backlog.md to be seeded at {backlog_path}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_generated_overwatch_backlog_has_no_live_entries(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The freshly seeded overwatch_backlog.md carries a header/legend
        only -- no bracket-marker line a wake firing could mistake for a
        queued task."""
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "overwatch-backlog-empty-test", workflow_type="release")
        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        backlog_text = (
            _project_dir(root, "overwatch-backlog-empty-test")
            / "tasks" / "queues" / "overwatch_backlog.md"
        ).read_text(encoding="utf-8")

        assert not re.search(r"^\s*-\s*\[[ WABx]\]\s+\S", backlog_text, re.MULTILINE), (
            f"Expected zero bracket-marker entries in the freshly seeded "
            f"overwatch_backlog.md, found some:\n{backlog_text}"
        )

    def test_document_workflow_project_also_gets_overwatch_backlog_queue_file(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A document-workflow project gets overwatch_backlog.md too -- same
        reasoning as test_document_workflow_project_also_gets_overwatch_scaffolding
        above: OVERWATCH's scaffolding is not release-workflow-specific."""
        root = _build_minimal_kanban_root(tmp_path)
        result = _run_create_project(root, "overwatch-backlog-doc-test", workflow_type="document")
        assert result.returncode == 0, (
            f"create-project.sh failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        backlog_path = (
            _project_dir(root, "overwatch-backlog-doc-test") / "tasks" / "queues" / "overwatch_backlog.md"
        )
        assert backlog_path.is_file(), (
            f"Expected tasks/queues/overwatch_backlog.md to be seeded for a "
            f"document-workflow project too at {backlog_path}."
        )
