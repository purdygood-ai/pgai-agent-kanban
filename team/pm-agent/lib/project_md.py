"""lib/project_md.py — Read, write, and validate PROJECT.md files.

PROJECT.md lives in the project artifacts directory and tracks metadata about
a creative/document project:

    # Project: <name>

    ## Workflow Type
    <workflow-type-name>

    ## Description
    <one-paragraph description>

    ## Output Name
    <filename-base>

    ## Output Formats
    - markdown
    - pdf

    ## Priority
    <integer>

    ## Next Version
    <integer>

All section parsing follows the liberal regex principle: whitespace variations,
extra blank lines, and mixed case are tolerated.

Public API
----------
    read_project_md(project_path) -> ProjectMetadata
    write_project_md(project_path, metadata) -> None
    validate_project_md(metadata, workflow) -> list[str]

Raises ProjectMdError with a descriptive message when a file is missing or
structurally unparseable.  Validation errors are returned as a list (not raised).
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    from .workflow_loader import WorkflowDefinition
except ImportError:
    from workflow_loader import WorkflowDefinition  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Constants — liberal field-header patterns
# ---------------------------------------------------------------------------

# Each pattern matches a section heading and captures the body text that
# follows (up to the next ## heading or end of file).
# We use re.IGNORECASE so "workflow type" == "Workflow Type".

_SECTION_RE = re.compile(
    r'##[^\S\n]*([^\n]+?)[^\S\n]*\n(.*?)(?=\n##|\Z)',
    re.DOTALL | re.IGNORECASE,
)

# Heading alias patterns — liberal matching so minor wording differences work.
_HEADING_WORKFLOW_TYPE = re.compile(r'workflow\s*type', re.IGNORECASE)
_HEADING_DESCRIPTION   = re.compile(r'description', re.IGNORECASE)
_HEADING_OUTPUT_NAME   = re.compile(r'output\s*name', re.IGNORECASE)
_HEADING_OUTPUT_FORMATS = re.compile(r'output\s*formats?', re.IGNORECASE)
_HEADING_PRIORITY      = re.compile(r'priority', re.IGNORECASE)
_HEADING_NEXT_VERSION  = re.compile(r'next\s*version', re.IGNORECASE)

# Project title: "# Project: <name>" (first line, liberal spacing)
_TITLE_RE = re.compile(r'^#\s*Project\s*:\s*(.+?)\s*$', re.IGNORECASE | re.MULTILINE)

# List-item stripper for Output Formats: "- item" or "* item"
_LIST_ITEM_RE = re.compile(r'^\s*[-*]\s*(.+?)\s*$')


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ProjectMetadata:
    """Metadata stored in PROJECT.md."""
    name: str                           # from title line "# Project: <name>"
    workflow_type: str = ""             # ## Workflow Type
    description: str = ""              # ## Description
    output_name: str = ""              # ## Output Name
    output_formats: list = field(default_factory=list)  # ## Output Formats (list[str])
    priority: Optional[int] = None     # ## Priority (integer)
    next_version: int = 1              # ## Next Version


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ProjectMdError(Exception):
    """Raised when PROJECT.md is missing or structurally unparseable."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_sections(text: str) -> dict:
    """Parse text into a dict mapping lowercased heading -> body text.

    The title line (# Project: ...) is not included in sections.
    """
    sections = {}
    for m in _SECTION_RE.finditer(text):
        heading = m.group(1).strip()
        body = m.group(2).strip()
        sections[heading.lower()] = (heading, body)
    return sections


def _find_section(sections: dict, heading_re: re.Pattern) -> Optional[str]:
    """Return the body text for the first heading matching heading_re, or None."""
    for key, (_heading, body) in sections.items():
        if heading_re.search(key):
            return body
    return None


def _parse_list_body(body: str) -> list:
    """Parse a markdown list body into a list of strings."""
    items = []
    for line in body.splitlines():
        m = _LIST_ITEM_RE.match(line)
        if m:
            items.append(m.group(1))
    return items


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_project_md(project_path) -> ProjectMetadata:
    """Parse PROJECT.md from a project directory and return ProjectMetadata.

    Parameters
    ----------
    project_path:
        Path to the project directory containing PROJECT.md.
        Accepts str or Path.

    Returns
    -------
    ProjectMetadata
        Parsed metadata. Fields absent from the file are left at defaults.

    Raises
    ------
    ProjectMdError
        If PROJECT.md does not exist or cannot be read.
    """
    project_path = Path(project_path)
    md_path = project_path / "PROJECT.md"

    try:
        text = md_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ProjectMdError(
            f"PROJECT.md not found in '{project_path}'"
        )
    except OSError as exc:
        raise ProjectMdError(
            f"Cannot read PROJECT.md in '{project_path}': {exc}"
        ) from exc

    # Extract project name from title
    title_match = _TITLE_RE.search(text)
    if title_match:
        name = title_match.group(1).strip()
    else:
        # Fallback: use directory name
        name = project_path.name

    sections = _extract_sections(text)

    # Workflow Type
    workflow_type = ""
    raw = _find_section(sections, _HEADING_WORKFLOW_TYPE)
    if raw is not None:
        workflow_type = raw.strip()

    # Description
    description = ""
    raw = _find_section(sections, _HEADING_DESCRIPTION)
    if raw is not None:
        description = raw.strip()

    # Output Name
    output_name = ""
    raw = _find_section(sections, _HEADING_OUTPUT_NAME)
    if raw is not None:
        output_name = raw.strip()

    # Output Formats — list items
    output_formats = []
    raw = _find_section(sections, _HEADING_OUTPUT_FORMATS)
    if raw is not None:
        output_formats = _parse_list_body(raw)
        if not output_formats:
            # Maybe it was given as plain text on one line
            stripped = raw.strip()
            if stripped:
                output_formats = [stripped]

    # Priority
    priority = None
    raw = _find_section(sections, _HEADING_PRIORITY)
    if raw is not None:
        try:
            priority = int(raw.strip())
        except ValueError:
            priority = None  # non-integer priority is silently ignored

    # Next Version
    next_version = 1
    raw = _find_section(sections, _HEADING_NEXT_VERSION)
    if raw is not None:
        try:
            next_version = int(raw.strip())
        except ValueError:
            next_version = 1

    return ProjectMetadata(
        name=name,
        workflow_type=workflow_type,
        description=description,
        output_name=output_name,
        output_formats=output_formats,
        priority=priority,
        next_version=next_version,
    )


def write_project_md(project_path, metadata: ProjectMetadata) -> None:
    """Write ProjectMetadata to PROJECT.md in the given project directory.

    If the file already exists it is overwritten. The parent directory is
    created if it does not exist.

    Parameters
    ----------
    project_path:
        Path to the project directory where PROJECT.md should be written.
        Accepts str or Path.
    metadata:
        The ProjectMetadata to serialise.

    Returns
    -------
    None
    """
    project_path = Path(project_path)
    project_path.mkdir(parents=True, exist_ok=True)

    md_path = project_path / "PROJECT.md"

    lines = [f"# Project: {metadata.name}", ""]

    lines += ["## Workflow Type", metadata.workflow_type, ""]

    lines += ["## Description", metadata.description, ""]

    lines += ["## Output Name", metadata.output_name, ""]

    lines += ["## Output Formats"]
    for fmt in metadata.output_formats:
        lines.append(f"- {fmt}")
    lines.append("")

    priority_str = str(metadata.priority) if metadata.priority is not None else ""
    lines += ["## Priority", priority_str, ""]

    lines += ["## Next Version", str(metadata.next_version), ""]

    md_path.write_text("\n".join(lines), encoding="utf-8")


def validate_project_md(
    metadata: ProjectMetadata,
    workflow: WorkflowDefinition,
) -> list:
    """Check ProjectMetadata against a WorkflowDefinition's required fields.

    Validation checks (all workflow types):
      - workflow_type is non-empty and matches the workflow name
      - description is non-empty
      - output_name is non-empty
      - output_formats is non-empty and all entries are strings
      - next_version is a positive integer

    Additional checks based on workflow.inputs.required:
      Any listed required input field name that maps to a PROJECT.md field
      is verified to be present. (This is an extensibility hook; currently
      the workflow YAML's inputs.required lists file names, not field names,
      so most workflows do not add extra field constraints here.)

    Parameters
    ----------
    metadata:
        The parsed ProjectMetadata to validate.
    workflow:
        The WorkflowDefinition the project runs under.

    Returns
    -------
    list[str]
        List of human-readable error strings. Empty list means the metadata
        is valid for the given workflow.
    """
    errors = []

    # workflow_type must be non-empty and must match the workflow name
    if not metadata.workflow_type:
        errors.append("Missing required field: Workflow Type")
    elif metadata.workflow_type != workflow.name:
        errors.append(
            f"Workflow Type '{metadata.workflow_type}' does not match "
            f"workflow name '{workflow.name}'"
        )

    # description is required
    if not metadata.description:
        errors.append("Missing required field: Description")

    # output_name is required
    if not metadata.output_name:
        errors.append("Missing required field: Output Name")

    # output_formats must be a non-empty list of strings
    if not metadata.output_formats:
        errors.append("Missing required field: Output Formats (must list at least one format)")
    else:
        for i, fmt in enumerate(metadata.output_formats):
            if not isinstance(fmt, str) or not fmt.strip():
                errors.append(
                    f"Output Formats entry {i} must be a non-empty string"
                )

    # next_version must be a positive integer
    if not isinstance(metadata.next_version, int) or metadata.next_version < 1:
        errors.append(
            f"Next Version must be a positive integer, got {metadata.next_version!r}"
        )

    # Check workflow-specific required inputs that map to PROJECT.md fields.
    # The convention: if a required input item matches a known PROJECT.md field
    # key pattern, verify that field is populated.
    _FIELD_MAP = {
        re.compile(r'output.?name', re.IGNORECASE): ("output_name", metadata.output_name),
        re.compile(r'description', re.IGNORECASE): ("description", metadata.description),
        re.compile(r'workflow.?type', re.IGNORECASE): ("workflow_type", metadata.workflow_type),
    }
    for required_input in workflow.inputs.required:
        for pattern, (field_name, field_value) in _FIELD_MAP.items():
            if pattern.search(required_input):
                if not field_value:
                    errors.append(
                        f"Workflow '{workflow.name}' requires '{required_input}' "
                        f"but PROJECT.md field '{field_name}' is empty"
                    )

    return errors
