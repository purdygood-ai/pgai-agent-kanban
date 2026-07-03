"""
test_project_md.py
==================
Behavioral unit tests for team/pm-agent/lib/project_md.py.

Tests exercise read_project_md(), write_project_md(), and
validate_project_md() using real PROJECT.md files written to tmp_path.
No live filesystem side effects remain after each test (pytest cleans tmp_path).

validate_project_md() requires a WorkflowDefinition stub; we build minimal
stubs with InputsSpec rather than loading real YAML, keeping these tests
strictly unit-scoped.
"""

from __future__ import annotations

import pathlib

import pytest

try:
    from pm_agent.lib.project_md import (
        read_project_md,
        write_project_md,
        validate_project_md,
        ProjectMetadata,
        ProjectMdError,
    )
    from pm_agent.lib.workflow_loader import InputsSpec, WorkflowDefinition, OutputsSpec
except ImportError:
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent / "pm-agent"))
    from lib.project_md import (  # type: ignore[no-redef]
        read_project_md,
        write_project_md,
        validate_project_md,
        ProjectMetadata,
        ProjectMdError,
    )
    from lib.workflow_loader import InputsSpec, WorkflowDefinition, OutputsSpec  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_project_md(directory: pathlib.Path, content: str) -> None:
    """Write content to PROJECT.md in the given directory."""
    (directory / "PROJECT.md").write_text(content, encoding="utf-8")


def _minimal_project_md(
    name: str = "Test Project",
    workflow_type: str = "release",
    description: str = "A test project.",
    output_name: str = "report",
    output_formats: str = "- markdown\n- pdf",
    priority: str = "1",
    next_version: str = "1",
) -> str:
    return (
        f"# Project: {name}\n\n"
        f"## Workflow Type\n{workflow_type}\n\n"
        f"## Description\n{description}\n\n"
        f"## Output Name\n{output_name}\n\n"
        f"## Output Formats\n{output_formats}\n\n"
        f"## Priority\n{priority}\n\n"
        f"## Next Version\n{next_version}\n"
    )


def _make_workflow(
    name: str = "release",
    required: list | None = None,
) -> WorkflowDefinition:
    """Build a minimal WorkflowDefinition stub for validate_project_md tests."""
    # PipelineStep was already imported at the top of the file via the
    # try/except import block; use it directly here.
    try:
        from pm_agent.lib.workflow_loader import PipelineStep as PS
    except ImportError:
        from lib.workflow_loader import PipelineStep as PS  # type: ignore[no-redef]

    return WorkflowDefinition(
        name=name,
        description="Test workflow",
        inputs=InputsSpec(
            required=required or [],
            optional=[],
            context=[],
        ),
        agents={"author": "WRITER"},
        pipeline=[PS(role="WRITER", name="Draft")],
        outputs=OutputsSpec(format="markdown", location="output/"),
        versioning="auto-increment",
    )


# ---------------------------------------------------------------------------
# read_project_md() — happy path
# ---------------------------------------------------------------------------


def test_read_project_md_extracts_project_name(tmp_path: pathlib.Path) -> None:
    """read_project_md extracts the project name from the '# Project: NAME' title."""
    _write_project_md(tmp_path, _minimal_project_md(name="My Cool Project"))
    meta = read_project_md(tmp_path)
    assert meta.name == "My Cool Project"


def test_read_project_md_extracts_workflow_type(tmp_path: pathlib.Path) -> None:
    """read_project_md parses the ## Workflow Type section."""
    _write_project_md(tmp_path, _minimal_project_md(workflow_type="document"))
    meta = read_project_md(tmp_path)
    assert meta.workflow_type == "document"


def test_read_project_md_extracts_description(tmp_path: pathlib.Path) -> None:
    """read_project_md extracts the ## Description body."""
    _write_project_md(tmp_path, _minimal_project_md(description="Detailed description here."))
    meta = read_project_md(tmp_path)
    assert meta.description == "Detailed description here."


def test_read_project_md_extracts_output_name(tmp_path: pathlib.Path) -> None:
    """read_project_md extracts the ## Output Name value."""
    _write_project_md(tmp_path, _minimal_project_md(output_name="final-report"))
    meta = read_project_md(tmp_path)
    assert meta.output_name == "final-report"


def test_read_project_md_parses_output_formats_list(tmp_path: pathlib.Path) -> None:
    """read_project_md parses a bulleted Output Formats list into a Python list."""
    _write_project_md(tmp_path, _minimal_project_md(output_formats="- markdown\n- pdf\n- html"))
    meta = read_project_md(tmp_path)
    assert meta.output_formats == ["markdown", "pdf", "html"]


def test_read_project_md_parses_priority_as_integer(tmp_path: pathlib.Path) -> None:
    """read_project_md parses the ## Priority section as an integer."""
    _write_project_md(tmp_path, _minimal_project_md(priority="3"))
    meta = read_project_md(tmp_path)
    assert meta.priority == 3


def test_read_project_md_parses_next_version_as_integer(tmp_path: pathlib.Path) -> None:
    """read_project_md parses ## Next Version as an integer."""
    _write_project_md(tmp_path, _minimal_project_md(next_version="5"))
    meta = read_project_md(tmp_path)
    assert meta.next_version == 5


def test_read_project_md_accepts_star_output_format_item(tmp_path: pathlib.Path) -> None:
    """read_project_md accepts '* item' as well as '- item' in Output Formats."""
    content = _minimal_project_md(output_formats="* docx\n* odt")
    _write_project_md(tmp_path, content)
    meta = read_project_md(tmp_path)
    assert "docx" in meta.output_formats
    assert "odt" in meta.output_formats


def test_read_project_md_fallback_name_from_directory_when_title_absent(
    tmp_path: pathlib.Path,
) -> None:
    """read_project_md falls back to the directory name when the title line is missing."""
    content = "## Workflow Type\nrelease\n\n## Description\nno title\n"
    _write_project_md(tmp_path, content)
    meta = read_project_md(tmp_path)
    assert meta.name == tmp_path.name


def test_read_project_md_non_integer_priority_is_none(tmp_path: pathlib.Path) -> None:
    """read_project_md silently ignores a non-integer priority, leaving it as None."""
    _write_project_md(tmp_path, _minimal_project_md(priority="high"))
    meta = read_project_md(tmp_path)
    assert meta.priority is None


def test_read_project_md_non_integer_next_version_defaults_to_one(
    tmp_path: pathlib.Path,
) -> None:
    """read_project_md defaults next_version to 1 when the value is non-integer."""
    _write_project_md(tmp_path, _minimal_project_md(next_version="n/a"))
    meta = read_project_md(tmp_path)
    assert meta.next_version == 1


def test_read_project_md_plain_text_output_format_is_wrapped_in_list(
    tmp_path: pathlib.Path,
) -> None:
    """read_project_md wraps a plain-text (non-list) Output Formats value in a list."""
    content = (
        "# Project: Test\n\n"
        "## Output Formats\nmarkdown\n"
    )
    _write_project_md(tmp_path, content)
    meta = read_project_md(tmp_path)
    assert meta.output_formats == ["markdown"]


# ---------------------------------------------------------------------------
# read_project_md() — error cases
# ---------------------------------------------------------------------------


def test_read_project_md_raises_when_file_missing(tmp_path: pathlib.Path) -> None:
    """read_project_md raises ProjectMdError when PROJECT.md does not exist."""
    with pytest.raises(ProjectMdError, match="not found"):
        read_project_md(tmp_path)


# ---------------------------------------------------------------------------
# write_project_md() — round-trip
# ---------------------------------------------------------------------------


def test_write_project_md_creates_file_in_directory(tmp_path: pathlib.Path) -> None:
    """write_project_md creates PROJECT.md in the given directory."""
    meta = ProjectMetadata(
        name="Round Trip",
        workflow_type="release",
        description="Test round-trip.",
        output_name="output",
        output_formats=["markdown"],
        priority=2,
        next_version=3,
    )
    write_project_md(tmp_path, meta)
    assert (tmp_path / "PROJECT.md").exists()


def test_write_then_read_preserves_metadata(tmp_path: pathlib.Path) -> None:
    """Writing then reading PROJECT.md preserves all metadata fields accurately."""
    original = ProjectMetadata(
        name="Preservation Test",
        workflow_type="document",
        description="Checks round-trip fidelity.",
        output_name="final-doc",
        output_formats=["markdown", "pdf"],
        priority=5,
        next_version=7,
    )
    write_project_md(tmp_path, original)
    restored = read_project_md(tmp_path)

    assert restored.name == original.name
    assert restored.workflow_type == original.workflow_type
    assert restored.description == original.description
    assert restored.output_name == original.output_name
    assert restored.output_formats == original.output_formats
    assert restored.priority == original.priority
    assert restored.next_version == original.next_version


def test_write_project_md_creates_parent_directory(tmp_path: pathlib.Path) -> None:
    """write_project_md creates the project directory if it does not exist."""
    new_dir = tmp_path / "nested" / "project-dir"
    meta = ProjectMetadata(name="New Dir", workflow_type="release")
    write_project_md(new_dir, meta)
    assert (new_dir / "PROJECT.md").exists()


def test_write_project_md_overwrites_existing_file(tmp_path: pathlib.Path) -> None:
    """write_project_md overwrites an existing PROJECT.md without error."""
    meta1 = ProjectMetadata(name="First", workflow_type="release")
    meta2 = ProjectMetadata(name="Second", workflow_type="document")
    write_project_md(tmp_path, meta1)
    write_project_md(tmp_path, meta2)
    restored = read_project_md(tmp_path)
    assert restored.name == "Second"


# ---------------------------------------------------------------------------
# validate_project_md()
# ---------------------------------------------------------------------------


def test_validate_project_md_returns_empty_list_for_valid_metadata(
    tmp_path: pathlib.Path,
) -> None:
    """validate_project_md returns [] when all required fields are present and valid."""
    meta = ProjectMetadata(
        name="Valid",
        workflow_type="release",
        description="A valid description.",
        output_name="output",
        output_formats=["markdown"],
        next_version=1,
    )
    workflow = _make_workflow(name="release")
    errors = validate_project_md(meta, workflow)
    assert errors == []


def test_validate_project_md_reports_missing_workflow_type() -> None:
    """validate_project_md flags an empty workflow_type as an error."""
    meta = ProjectMetadata(
        name="Bad",
        workflow_type="",
        description="Desc.",
        output_name="out",
        output_formats=["markdown"],
    )
    workflow = _make_workflow(name="release")
    errors = validate_project_md(meta, workflow)
    assert any("Workflow Type" in e for e in errors)


def test_validate_project_md_reports_workflow_type_mismatch() -> None:
    """validate_project_md flags a workflow_type that does not match the workflow name."""
    meta = ProjectMetadata(
        name="Mismatch",
        workflow_type="document",
        description="Desc.",
        output_name="out",
        output_formats=["markdown"],
    )
    workflow = _make_workflow(name="release")
    errors = validate_project_md(meta, workflow)
    assert any("does not match" in e for e in errors)


def test_validate_project_md_reports_missing_description() -> None:
    """validate_project_md flags an empty description as an error."""
    meta = ProjectMetadata(
        name="No Desc",
        workflow_type="release",
        description="",
        output_name="out",
        output_formats=["markdown"],
    )
    workflow = _make_workflow(name="release")
    errors = validate_project_md(meta, workflow)
    assert any("Description" in e for e in errors)


def test_validate_project_md_reports_missing_output_name() -> None:
    """validate_project_md flags an empty output_name as an error."""
    meta = ProjectMetadata(
        name="No Output Name",
        workflow_type="release",
        description="Has description.",
        output_name="",
        output_formats=["markdown"],
    )
    workflow = _make_workflow(name="release")
    errors = validate_project_md(meta, workflow)
    assert any("Output Name" in e for e in errors)


def test_validate_project_md_reports_empty_output_formats() -> None:
    """validate_project_md flags an empty output_formats list as an error."""
    meta = ProjectMetadata(
        name="No Formats",
        workflow_type="release",
        description="Has desc.",
        output_name="out",
        output_formats=[],
    )
    workflow = _make_workflow(name="release")
    errors = validate_project_md(meta, workflow)
    assert any("Output Formats" in e for e in errors)


def test_validate_project_md_reports_invalid_next_version() -> None:
    """validate_project_md flags next_version < 1 as an error."""
    meta = ProjectMetadata(
        name="Bad Version",
        workflow_type="release",
        description="Desc.",
        output_name="out",
        output_formats=["markdown"],
        next_version=0,
    )
    workflow = _make_workflow(name="release")
    errors = validate_project_md(meta, workflow)
    assert any("Next Version" in e for e in errors)


def test_validate_project_md_accumulates_multiple_errors() -> None:
    """validate_project_md returns all errors, not just the first one."""
    meta = ProjectMetadata(
        name="All Bad",
        workflow_type="",
        description="",
        output_name="",
        output_formats=[],
        next_version=0,
    )
    workflow = _make_workflow(name="release")
    errors = validate_project_md(meta, workflow)
    assert len(errors) >= 4
